"""Glue between the web page (app.js) and taxreport.converter.

Runs inside Pyodide in the browser. Data comes in from JavaScript and goes
back as JSON; nothing here does any network or file-system I/O beyond the
in-memory Pyodide file system that holds this code.
"""

import json

from taxreport.converter import (
    TEMPLATE_COLUMNS,
    FilingOptions,
    InputError,
    build_filing,
    format_money,
    load_employee_info,
    parse_local_tax_summary,
    to_csv,
)


def _bytes(data):
    # JavaScript Uint8Array arrives as a JsProxy.
    return data.to_bytes() if hasattr(data, "to_bytes") else data


def process(employee_data, summary_data, options_json):
    opts = json.loads(options_json)
    try:
        employees, emp_issues = load_employee_info(_bytes(employee_data))
        sections = parse_local_tax_summary(_bytes(summary_data))
    except InputError as exc:
        return json.dumps({"error": str(exc)})

    excluded = set(opts.get("excluded_sections", []))
    included = [s for i, s in enumerate(sections) if i not in excluded]
    result = build_filing(
        employees,
        included,
        FilingOptions(
            tax_year=opts["tax_year"],
            tax_quarter=opts["tax_quarter"],
            tax_month=opts.get("tax_month", ""),
            work_psd=opts["work_psd"],
            ssn_digits_only=opts.get("ssn_digits_only", False),
        ),
    )
    return json.dumps(
        {
            "columns": TEMPLATE_COLUMNS,
            "sections": [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "lines": len(s.rows),
                    "tax": format_money(s.tax),
                    "reported_tax": format_money(
                        s.reported_tax if s.reported_tax is not None else s.tax
                    ),
                    "included": i not in excluded,
                }
                for i, s in enumerate(sections)
            ],
            "rows": result.rows,
            "issues": [
                {
                    "severity": i.severity,
                    "message": i.message,
                    "name": i.name,
                    "ssn": i.masked_ssn,
                    "row": i.row,
                }
                for i in emp_issues + result.issues
                # The page validates Work PSD itself as it is typed.
                if not i.message.startswith("Work PSD")
            ],
        }
    )


def render_csv(rows_json):
    return to_csv(json.loads(rows_json))
