"""Convert payroll exports into the local tax quarterly filing template.

Inputs
------
* Employee info CSV: one row per employee with address and resident PSD
  ("Township Code").
* Local Tax Summary CSV: the payroll report with one section per tax
  (an EIT section such as ``TCD28`` and an ``LST`` section).

Output
------
A CSV whose columns match ``TEMPLATE_COLUMNS`` exactly, one row per
employee (keyed by SSN) with wages, EIT and LST combined.

This module uses only the Python standard library so it can be tested and
run from the command line without the web UI.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

TEMPLATE_COLUMNS = [
    "Soc Sec No",
    "First Name",
    "Middle Name",
    "Last Name",
    "Suffix (Jr Sr)",
    "Physical Street Address",
    "Physical City",
    "Physical State",
    "Physical Zip",
    "Mailing Address",
    "Mailing City",
    "Mailing State",
    "Mailing Zip",
    "Tax Year",
    "Tax Quarter",
    "Tax Month",
    "Local Wages",
    "EIT Withheld",
    "LST Withheld",
    "Resident PSD",
    "Work PSD",
]

# Work location PSD used unless another is entered.
DEFAULT_WORK_PSD = "280301"

# PA DCED PSD code used for residents of other states.
OUT_OF_STATE_PSD = "880000"

SUFFIXES = {"JR", "SR", "II", "III", "IV", "V", "VI"}

_SSN_RE = re.compile(r"^\D*(\d{3})\D*(\d{2})\D*(\d{4})\D*$")
_PSD_RE = re.compile(r"^\d{6}$")
_PO_BOX_RE = re.compile(r"^\s*(P\.?\s*O\.?\s*BOX|POST\s+OFFICE\s+BOX|BOX)\b", re.I)
_CENT = Decimal("0.01")


class InputError(ValueError):
    """Raised when an uploaded file cannot be understood."""


@dataclass
class Issue:
    severity: str  # "error" or "warning" or "info"
    message: str
    name: str = ""
    ssn: str = ""
    row: int | None = None  # 1-based row in the output file

    @property
    def masked_ssn(self) -> str:
        return mask_ssn(self.ssn)


@dataclass
class Employee:
    name: str
    ssn: str
    street1: str
    street2: str
    city: str
    state: str
    zip: str
    psd: str


@dataclass
class SummaryRow:
    name: str
    ssn: str
    street1: str
    street2: str
    city: str
    state: str
    zip: str
    wagebase: Decimal
    tax: Decimal


@dataclass
class SummarySection:
    name: str
    kind: str  # "EIT" or "LST"
    rows: list[SummaryRow] = field(default_factory=list)
    reported_wagebase: Decimal | None = None
    reported_tax: Decimal | None = None

    @property
    def wagebase(self) -> Decimal:
        return sum((r.wagebase for r in self.rows), Decimal("0"))

    @property
    def tax(self) -> Decimal:
        return sum((r.tax for r in self.rows), Decimal("0"))


@dataclass
class FilingOptions:
    tax_year: str
    tax_quarter: str
    work_psd: str
    tax_month: str = ""
    ssn_digits_only: bool = False


@dataclass
class FilingResult:
    rows: list[dict[str, str]]
    issues: list[Issue]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def normalize_ssn(value: str | None) -> str:
    """Return the SSN as 9 digits, or "" if it is not a valid SSN."""
    m = _SSN_RE.match(value or "")
    return "".join(m.groups()) if m else ""


def format_ssn(ssn: str, digits_only: bool = False) -> str:
    if digits_only or len(ssn) != 9:
        return ssn
    return f"{ssn[:3]}-{ssn[3:5]}-{ssn[5:]}"


def mask_ssn(ssn: str) -> str:
    return f"***-**-{ssn[-4:]}" if len(ssn) == 9 else ""


def parse_money(value: str | None) -> Decimal:
    text = (value or "").strip().replace(",", "").replace("$", "")
    if not text:
        return Decimal("0")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise InputError(f"Could not read amount {value!r}") from exc
    return -amount if negative else amount


def format_money(amount: Decimal) -> str:
    return str(amount.quantize(_CENT, rounding=ROUND_HALF_UP))


def is_valid_psd(code: str) -> bool:
    return bool(_PSD_RE.match(code or ""))


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def _read_rows(data: bytes | str) -> list[list[str]]:
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
    else:
        text = data.lstrip("﻿")
    return [[_clean(c) for c in row] for row in csv.reader(io.StringIO(text))]


def _header_index(header: list[str], *candidates: str) -> int | None:
    keys = [re.sub(r"[^a-z0-9]", "", h.lower()) for h in header]
    for cand in candidates:
        key = re.sub(r"[^a-z0-9]", "", cand.lower())
        if key in keys:
            return keys.index(key)
    return None


def _cell(row: list[str], idx: int | None) -> str:
    return row[idx] if idx is not None and idx < len(row) else ""


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------


def load_employee_info(data: bytes | str) -> tuple[dict[str, Employee], list[Issue]]:
    """Read the employee info CSV, keyed by 9-digit SSN."""
    rows = _read_rows(data)
    header_at = next(
        (i for i, r in enumerate(rows) if _header_index(r, "SS No.", "SSN") is not None),
        None,
    )
    if header_at is None:
        raise InputError(
            "Employee info file: could not find a header row with an "
            "'SS No.' or 'SSN' column."
        )
    header = rows[header_at]
    col = {
        "name": _header_index(header, "Employee", "Employee Name", "Name"),
        "ssn": _header_index(header, "SS No.", "SSN"),
        "street1": _header_index(header, "Street1", "Street", "Address"),
        "street2": _header_index(header, "Street2", "Street 2"),
        "city": _header_index(header, "City"),
        "state": _header_index(header, "State"),
        "zip": _header_index(header, "Zip", "Zip Code"),
        "psd": _header_index(header, "Township Code", "PSD", "PSD Code", "Resident PSD"),
    }
    missing = [k for k in ("name", "psd") if col[k] is None]
    if missing:
        raise InputError(
            "Employee info file is missing column(s): "
            + ", ".join({"name": "Employee", "psd": "Township Code"}[k] for k in missing)
        )

    employees: dict[str, Employee] = {}
    issues: list[Issue] = []
    for row in rows[header_at + 1 :]:
        if not any(row):
            continue
        raw_ssn = _cell(row, col["ssn"])
        ssn = normalize_ssn(raw_ssn)
        name = _cell(row, col["name"])
        if not ssn:
            issues.append(
                Issue("warning", f"Employee info row skipped: invalid SSN {raw_ssn!r}.", name)
            )
            continue
        emp = Employee(
            name=name,
            ssn=ssn,
            street1=_cell(row, col["street1"]),
            street2=_cell(row, col["street2"]),
            city=_cell(row, col["city"]),
            state=_cell(row, col["state"]).upper(),
            zip=_cell(row, col["zip"]),
            psd=_cell(row, col["psd"]),
        )
        if ssn in employees:
            issues.append(
                Issue(
                    "warning",
                    "SSN appears more than once in the employee info file; "
                    "the last row was used.",
                    name,
                    ssn,
                )
            )
        employees[ssn] = emp
    return employees, issues


def parse_local_tax_summary(data: bytes | str) -> list[SummarySection]:
    """Split the Local Tax Summary report into its EIT/LST sections."""
    rows = _read_rows(data)
    sections: list[SummarySection] = []
    titles: list[str] = []
    current: SummarySection | None = None
    col: dict[str, int | None] = {}

    for row in rows:
        first = row[0] if row else ""
        is_header = (
            first.lower() == "employee" and _header_index(row, "SSN", "SS No.") is not None
        )
        if is_header:
            col = {
                "ssn": _header_index(row, "SSN", "SS No."),
                "street1": _header_index(row, "Street", "Street1"),
                "street2": _header_index(row, "Street 2", "Street2"),
                "city": _header_index(row, "City"),
                "state": _header_index(row, "State"),
                "zip": _header_index(row, "ZIP"),
                "w2": _header_index(row, "Print on W2 As"),
                "wagebase": _header_index(row, "Wagebase", "Wages"),
                "tax": _header_index(row, "Tax"),
            }
            if col["wagebase"] is None or col["tax"] is None:
                raise InputError(
                    "Local Tax Summary: a section header is missing the "
                    "'Wagebase' or 'Tax' column."
                )
            label = " - ".join(dict.fromkeys(t for t in titles if t.lower() != "local tax summary"))
            current = SummarySection(name=label or f"Section {len(sections) + 1}", kind="")
            sections.append(current)
            titles = []
            continue

        if current is None or not col:
            # Title lines before a header, e.g. "FRANKLIN COUNTY AREA TAX BUREAU".
            if first:
                titles.append(first)
            continue

        ssn = normalize_ssn(_cell(row, col["ssn"]))
        if ssn:
            if not current.kind:
                w2 = _cell(row, col["w2"]).upper()
                current.kind = "LST" if w2 == "LST" or current.name.upper() == "LST" else "EIT"
            current.rows.append(
                SummaryRow(
                    name=first,
                    ssn=ssn,
                    street1=_cell(row, col["street1"]),
                    street2=_cell(row, col["street2"]),
                    city=_cell(row, col["city"]),
                    state=_cell(row, col["state"]).upper(),
                    zip=_cell(row, col["zip"]),
                    wagebase=parse_money(_cell(row, col["wagebase"])),
                    tax=parse_money(_cell(row, col["tax"])),
                )
            )
        elif first.lower().endswith("subtotal") or (
            first.lower().startswith("total") and current.reported_tax is None
        ):
            current.reported_wagebase = parse_money(_cell(row, col["wagebase"]))
            current.reported_tax = parse_money(_cell(row, col["tax"]))
        elif first.lower().startswith("total"):
            pass
        elif first and not any(row[1:]):
            # A title line: the next section is starting.
            current, col = None, {}
            titles.append(first)

    for sec in sections:
        if not sec.kind:
            sec.kind = "LST" if "LST" in sec.name.upper() else "EIT"
    if not sections:
        raise InputError(
            "Local Tax Summary: no sections found (expected header rows "
            "starting with 'Employee,SSN,...')."
        )
    return sections


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------


def split_name(full: str) -> tuple[str, str, str, str]:
    """Split a name into (first, middle, last, suffix).

    Handles "FIRST M. LAST", "FIRST M LAST JR" and "LAST, FIRST M".
    """
    full = _clean(full)
    if "," in full:
        before, after = (p.strip() for p in full.split(",", 1))
        after_tokens = after.replace(",", " ").split()
        if after_tokens and all(t.strip(".").upper() in SUFFIXES for t in after_tokens):
            full = f"{before} {after}"  # "FIRST LAST, JR"
        else:
            last_tokens = before.split()
            suffix = ""
            if len(last_tokens) > 1 and last_tokens[-1].strip(".").upper() in SUFFIXES:
                suffix = last_tokens.pop().strip(".")
            if after_tokens and after_tokens[-1].strip(".").upper() in SUFFIXES:
                suffix = after_tokens.pop().strip(".")
            first = after_tokens[0] if after_tokens else ""
            middle = " ".join(after_tokens[1:]).replace(".", "")
            return first, middle, " ".join(last_tokens), suffix

    tokens = full.replace(",", " ").split()
    suffix = ""
    if len(tokens) > 2 and tokens[-1].strip(".").upper() in SUFFIXES:
        suffix = tokens.pop().strip(".")
    if not tokens:
        return "", "", "", suffix
    if len(tokens) == 1:
        return tokens[0], "", "", suffix
    return tokens[0], " ".join(tokens[1:-1]).replace(".", ""), tokens[-1], suffix


def split_address(street1: str, street2: str) -> tuple[str, str]:
    """Return (physical street, mailing street).

    A PO Box goes to the mailing address and the other line to the
    physical address. Otherwise both lines are combined and used for both.
    """
    if street2 and _PO_BOX_RE.match(street2) and street1:
        return street1, street2
    if street1 and _PO_BOX_RE.match(street1) and street2:
        return street2, street1
    combined = " ".join(p for p in (street1, street2) if p)
    return combined, combined


@dataclass
class _Totals:
    name: str
    first_row: SummaryRow
    eit_wages: Decimal = Decimal("0")
    lst_wages: Decimal = Decimal("0")
    eit: Decimal = Decimal("0")
    lst: Decimal = Decimal("0")
    eit_rows: int = 0
    lst_rows: int = 0


def build_filing(
    employees: dict[str, Employee],
    sections: list[SummarySection],
    options: FilingOptions,
) -> FilingResult:
    """Combine the two inputs into rows for the filing template."""
    issues: list[Issue] = []

    work_psd = options.work_psd.strip()
    if not is_valid_psd(work_psd):
        issues.append(Issue("error", f"Work PSD {work_psd!r} is not a 6-digit PSD code."))

    by_ssn: dict[str, _Totals] = {}
    for sec in sections:
        for r in sec.rows:
            t = by_ssn.setdefault(r.ssn, _Totals(r.name, r))
            if sec.kind == "LST":
                t.lst_wages += r.wagebase
                t.lst += r.tax
                t.lst_rows += 1
            else:
                t.eit_wages += r.wagebase
                t.eit += r.tax
                t.eit_rows += 1

    out: list[dict[str, str]] = []
    for row_no, (ssn, t) in enumerate(by_ssn.items(), start=1):
        emp = employees.get(ssn)
        name = emp.name if emp else t.name

        def issue(severity: str, message: str) -> None:
            issues.append(Issue(severity, message, name, ssn, row_no))

        if t.eit_rows > 1 or t.lst_rows > 1:
            issue("info", "Employee has more than one line in a tax section; amounts were added together.")

        # Address: prefer the employee info file, fall back to the tax summary.
        src = t.first_row
        if emp and (emp.street1 or emp.city or emp.zip):
            street1, street2 = emp.street1, emp.street2
            city, state, zip_code = emp.city, emp.state, emp.zip
        else:
            street1, street2 = src.street1, src.street2
            city, state, zip_code = src.city, src.state, src.zip
            if emp:
                issue("warning", "Address is blank in the employee info file; used the address from the tax summary.")
        if not (street1 and city and state and zip_code):
            issue("error", "Address is incomplete.")
        physical, mailing = split_address(street1, street2)

        # Resident PSD
        if emp is None:
            psd = ""
            if state and state != "PA":
                psd = OUT_OF_STATE_PSD
                issue("warning", f"Not found in the employee info file; out-of-state address so Resident PSD set to {OUT_OF_STATE_PSD}.")
            else:
                issue("error", "Not found in the employee info file, so Resident PSD is unknown.")
        else:
            psd = emp.psd
            if not psd:
                if state and state != "PA":
                    psd = OUT_OF_STATE_PSD
                elif state == "PA":
                    issue("error", "Resident PSD (Township Code) is blank for a PA resident.")
                else:
                    issue("error", "Resident PSD (Township Code) and state are blank.")
            elif not is_valid_psd(psd):
                issue("error", f"Resident PSD {psd!r} is not a 6-digit PSD code.")
                psd = ""
            elif state and state != "PA" and psd != OUT_OF_STATE_PSD:
                issue("warning", f"Address is in {state} but Resident PSD is {psd} (a PA code). Check which is correct.")
            elif state == "PA" and psd == OUT_OF_STATE_PSD:
                issue("warning", f"Address is in PA but Resident PSD is {OUT_OF_STATE_PSD} (out of state).")

        # Wages: the EIT wage base if the employee had EIT, otherwise the LST wage base.
        wages = t.eit_wages if t.eit_rows else t.lst_wages
        if t.eit_rows and t.lst_rows and t.eit_wages != t.lst_wages:
            issue(
                "warning",
                f"EIT wage base ({format_money(t.eit_wages)}) differs from LST wage base "
                f"({format_money(t.lst_wages)}); EIT wage base used for Local Wages.",
            )

        first, middle, last, suffix = split_name(name)
        out.append(
            {
                "Soc Sec No": format_ssn(ssn, options.ssn_digits_only),
                "First Name": first,
                "Middle Name": middle,
                "Last Name": last,
                "Suffix (Jr Sr)": suffix,
                "Physical Street Address": physical,
                "Physical City": city,
                "Physical State": state,
                "Physical Zip": zip_code,
                "Mailing Address": mailing,
                "Mailing City": city,
                "Mailing State": state,
                "Mailing Zip": zip_code,
                "Tax Year": str(options.tax_year),
                "Tax Quarter": str(options.tax_quarter),
                "Tax Month": str(options.tax_month or ""),
                "Local Wages": format_money(wages),
                "EIT Withheld": format_money(t.eit),
                "LST Withheld": format_money(t.lst),
                "Resident PSD": psd,
                "Work PSD": work_psd,
            }
        )

    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: (order.get(i.severity, 3), i.row or 0))
    return FilingResult(out, issues)


def to_csv(rows: list[dict[str, str]]) -> str:
    """Render rows in the filing template layout (all fields quoted)."""
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=TEMPLATE_COLUMNS, quoting=csv.QUOTE_ALL, lineterminator="\r\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") or "" for c in TEMPLATE_COLUMNS})
    return buf.getvalue()


def totals(rows: list[dict[str, str]]) -> dict[str, Decimal]:
    result = {}
    for col in ("Local Wages", "EIT Withheld", "LST Withheld"):
        result[col] = sum((parse_money(r.get(col)) for r in rows), Decimal("0"))
    return result
