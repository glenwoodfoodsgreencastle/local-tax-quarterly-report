import csv
import io
from decimal import Decimal
from pathlib import Path

import pytest

from taxreport.converter import (
    OUT_OF_STATE_PSD,
    TEMPLATE_COLUMNS,
    FilingOptions,
    InputError,
    build_filing,
    load_employee_info,
    parse_local_tax_summary,
    split_address,
    split_name,
    to_csv,
    totals,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
OPTS = FilingOptions(tax_year="2026", tax_quarter="3", work_psd="280301")

EMPLOYEES = """\
Employee,SS No.,Street1,Street2,City,State,Zip,Township Code
JOHN A. SMITH,900-00-0001,1 MAIN ST,,GREENCASTLE,PA,17225,280301
MARY B. JONES,900-00-0002,2 OAK ST,PO BOX 5,HAGERSTOWN,MD,21740,
ROBERT C. MILLER JR,900-00-0003,3 ELM ST,,CHAMBERSBURG,PA,17202,ST THOMAS
"""

SUMMARY = """\
Local Tax Summary,,,,,,,,,,,,
FRANKLIN COUNTY AREA TAX BUREAU,,,,,,,,,,,,
TCD28,,,,,,,,,,,,
Employee,SSN,Street,Street 2,City,State,ZIP,Print on W2 As,Misc 1,Misc 2,Misc 3,Wagebase,Tax
JOHN A. SMITH,900-00-0001,1 MAIN ST,,GREENCASTLE,PA,17225,TCD28,1.0%,,,"1,000.00",10.00
ROBERT C. MILLER JR,900-00-0003,3 ELM ST,,CHAMBERSBURG,PA,17202,TCD28,1.7%,,,"2,000.00",34.00
TCD28 Subtotal,,,,,,,,,,,"3,000.00",44.00
Total FRANKLIN COUNTY AREA TAX BUREAU,,,,,,,,,,,"3,000.00",44.00
,,,,,,,,,,,,
LST,,,,,,,,,,,,
LST,,,,,,,,,,,,
Employee,SSN,Street,Street 2,City,State,ZIP,Print on W2 As,Misc 1,Misc 2,Misc 3,Wagebase,Tax
JOHN A. SMITH,900-00-0001,1 MAIN ST,,GREENCASTLE,PA,17225,LST,2.00,52.00,,"1,000.00",14.00
MARY B. JONES,900-00-0002,2 OAK ST,PO BOX 5,HAGERSTOWN,MD,21740,LST,2.00,52.00,,500.50,4.00
ROBERT C. MILLER JR,900-00-0003,3 ELM ST,,CHAMBERSBURG,PA,17202,LST,2.00,52.00,,"2,000.00",14.00
LST Subtotal,,,,,,,,,,,"3,500.50",32.00
Total LST,,,,,,,,,,,"3,500.50",32.00
"""


def build(employees=EMPLOYEES, summary=SUMMARY, opts=OPTS):
    emps, _ = load_employee_info(employees)
    return build_filing(emps, parse_local_tax_summary(summary), opts)


def test_parse_sections():
    sections = parse_local_tax_summary(SUMMARY)
    assert [(s.name, s.kind, len(s.rows)) for s in sections] == [
        ("FRANKLIN COUNTY AREA TAX BUREAU - TCD28", "EIT", 2),
        ("LST", "LST", 3),
    ]
    assert sections[0].reported_tax == Decimal("44.00")
    assert sections[1].reported_wagebase == Decimal("3500.50")


def test_rows_are_combined_per_employee():
    result = build()
    by_ssn = {r["Soc Sec No"]: r for r in result.rows}
    assert len(result.rows) == 3

    john = by_ssn["900-00-0001"]
    assert (john["First Name"], john["Middle Name"], john["Last Name"]) == ("JOHN", "A", "SMITH")
    assert (john["Local Wages"], john["EIT Withheld"], john["LST Withheld"]) == ("1000.00", "10.00", "14.00")
    assert (john["Resident PSD"], john["Work PSD"]) == ("280301", "280301")
    assert (john["Tax Year"], john["Tax Quarter"], john["Tax Month"]) == ("2026", "3", "")

    mary = by_ssn["900-00-0002"]  # LST only, out of state, PO box
    assert (mary["Local Wages"], mary["EIT Withheld"], mary["LST Withheld"]) == ("500.50", "0.00", "4.00")
    assert mary["Resident PSD"] == OUT_OF_STATE_PSD
    assert mary["Physical Street Address"] == "2 OAK ST"
    assert mary["Mailing Address"] == "PO BOX 5"

    bob = by_ssn["900-00-0003"]
    assert (bob["Last Name"], bob["Suffix (Jr Sr)"]) == ("MILLER", "JR")
    assert bob["Resident PSD"] == ""  # "ST THOMAS" is not a PSD code

    assert totals(result.rows) == {
        "Local Wages": Decimal("3500.50"),
        "EIT Withheld": Decimal("44.00"),
        "LST Withheld": Decimal("32.00"),
    }


def test_invalid_psd_is_reported():
    result = build()
    errors = [i for i in result.issues if i.severity == "error"]
    assert len(errors) == 1
    assert "ST THOMAS" in errors[0].message
    assert errors[0].row == 2
    assert errors[0].masked_ssn == "***-**-0003"


def test_employee_missing_from_info_file():
    result = build(employees="Employee,SS No.,Street1,Street2,City,State,Zip,Township Code\n")
    assert len(result.rows) == 3
    # Address falls back to the tax summary.
    assert result.rows[0]["Physical City"] == "GREENCASTLE"
    assert any("Not found in the employee info file" in i.message for i in result.issues)


def test_bad_work_psd():
    result = build(opts=FilingOptions("2026", "3", "12345"))
    assert any(i.message.startswith("Work PSD") for i in result.issues)


def test_ssn_digits_only():
    result = build(opts=FilingOptions("2026", "3", "280301", ssn_digits_only=True))
    assert result.rows[0]["Soc Sec No"] == "900000001"


def test_csv_matches_template_header():
    out = to_csv(build().rows)
    template = (SAMPLES / "FilingTemplate.csv").read_text().splitlines()[0]
    assert out.splitlines()[0] == template
    parsed = list(csv.DictReader(io.StringIO(out)))
    assert list(parsed[0].keys()) == TEMPLATE_COLUMNS
    assert parsed[0]["Physical Zip"] == "17225"


@pytest.mark.parametrize(
    "name, expected",
    [
        ("FIRST M. LAST", ("FIRST", "M", "LAST", "")),
        ("JOHN SMITH", ("JOHN", "", "SMITH", "")),
        ("JOHN Q SMITH III", ("JOHN", "Q", "SMITH", "III")),
        ("SMITH, JOHN Q", ("JOHN", "Q", "SMITH", "")),
        ("SMITH JR, JOHN Q", ("JOHN", "Q", "SMITH", "JR")),
        ("JOHN SMITH, SR.", ("JOHN", "", "SMITH", "SR")),
    ],
)
def test_split_name(name, expected):
    assert split_name(name) == expected


def test_split_address():
    assert split_address("1 MAIN ST", "APT 3") == ("1 MAIN ST APT 3", "1 MAIN ST APT 3")
    assert split_address("1 MAIN ST", "P.O. BOX 9") == ("1 MAIN ST", "P.O. BOX 9")
    assert split_address("PO BOX 9", "1 MAIN ST") == ("1 MAIN ST", "PO BOX 9")


def test_missing_header_raises():
    with pytest.raises(InputError):
        load_employee_info("a,b,c\n1,2,3\n")
    with pytest.raises(InputError):
        parse_local_tax_summary("nothing,here\n")
