"""Command-line version: python -m taxreport EMPLOYEE_INFO SUMMARY --year Y --quarter Q --work-psd PSD -o OUT."""

import argparse
import sys
from pathlib import Path

from .converter import (
    FilingOptions,
    InputError,
    build_filing,
    load_employee_info,
    parse_local_tax_summary,
    to_csv,
    totals,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m taxreport", description=__doc__)
    p.add_argument("employee_info", type=Path, help="Employee info CSV")
    p.add_argument("summary", type=Path, help="Local Tax Summary CSV")
    p.add_argument("--year", required=True)
    p.add_argument("--quarter", required=True, choices=["1", "2", "3", "4"])
    p.add_argument("--month", default="", help="Tax Month (optional)")
    p.add_argument("--work-psd", required=True, help="6-digit PSD code of the work location")
    p.add_argument("--ssn-digits-only", action="store_true", help="Write SSNs as 123456789")
    p.add_argument("-o", "--output", type=Path, required=True)
    args = p.parse_args(argv)

    try:
        employees, issues = load_employee_info(args.employee_info.read_bytes())
        sections = parse_local_tax_summary(args.summary.read_bytes())
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = build_filing(
        employees,
        sections,
        FilingOptions(args.year, args.quarter, args.work_psd, args.month, args.ssn_digits_only),
    )
    args.output.write_text(to_csv(result.rows), encoding="utf-8", newline="")

    for issue in issues + result.issues:
        where = f"row {issue.row} " if issue.row else ""
        who = f"{issue.name} ({issue.masked_ssn}) " if issue.ssn else ""
        print(f"{issue.severity.upper():7} {where}{who}{issue.message}", file=sys.stderr)
    t = totals(result.rows)
    print(
        f"Wrote {len(result.rows)} employees to {args.output}: "
        f"wages {t['Local Wages']}, EIT {t['EIT Withheld']}, LST {t['LST Withheld']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
