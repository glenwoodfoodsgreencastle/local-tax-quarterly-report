"""Local Tax Quarterly Report - browser UI.

Run with:  streamlit run app.py   (or use run_app.bat / run_app.sh)

Streamlit serves this page from your own computer (http://localhost:8501).
Uploaded files are read into memory by the local Python process only; they
are never sent to any outside server and nothing is written to disk.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from taxreport.converter import (
    TEMPLATE_COLUMNS,
    FilingOptions,
    InputError,
    build_filing,
    format_money,
    is_valid_psd,
    load_employee_info,
    parse_local_tax_summary,
    to_csv,
    totals,
)

SETTINGS_FILE = Path(__file__).with_name("local_settings.json")


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    except OSError:
        pass


def last_completed_quarter(today: dt.date) -> tuple[int, int]:
    q = (today.month - 1) // 3  # quarter before the current one (0 = previous year's Q4)
    return (today.year, q) if q else (today.year - 1, 4)


st.set_page_config(page_title="Local Tax Quarterly Report", page_icon="🧾", layout="wide")
st.title("Local Tax Quarterly Report")
st.caption(
    "🔒 Runs only on this computer. Files you choose are processed in memory by the "
    "local app and are not uploaded to the internet."
)

settings = load_settings()
default_year, default_quarter = last_completed_quarter(dt.date.today())

with st.sidebar:
    st.header("Filing details")
    tax_year = st.number_input("Tax Year", min_value=2000, max_value=2100, value=default_year, step=1)
    tax_quarter = st.selectbox("Tax Quarter", [1, 2, 3, 4], index=default_quarter - 1)
    tax_month = st.selectbox(
        "Tax Month (optional)", [""] + [str(m) for m in range(1, 13)], index=0,
        format_func=lambda m: m or "(blank)",
        help="Leave blank for a quarterly filing.",
    )
    work_psd = st.text_input(
        "Work PSD", value=settings.get("work_psd", ""), max_chars=6,
        help="6-digit PSD code for the work location. Saved on this computer for next time.",
    ).strip()
    if work_psd and not is_valid_psd(work_psd):
        st.error("Work PSD must be 6 digits.")
    ssn_digits_only = st.checkbox(
        "Write SSNs without dashes", value=settings.get("ssn_digits_only", False)
    )
    new_settings = {"work_psd": work_psd, "ssn_digits_only": ssn_digits_only}
    if new_settings != settings and is_valid_psd(work_psd):
        save_settings(new_settings)

col1, col2 = st.columns(2)
emp_file = col1.file_uploader("1. Employee info CSV", type=["csv"])
summary_file = col2.file_uploader("2. Local Tax Summary CSV", type=["csv"])

if not (emp_file and summary_file):
    st.info("Choose both CSV files to build the filing.")
    st.stop()

try:
    employees, emp_issues = load_employee_info(emp_file.getvalue())
    sections = parse_local_tax_summary(summary_file.getvalue())
except InputError as exc:
    st.error(str(exc))
    st.stop()

# ---- Sections found in the tax summary ------------------------------------
st.subheader("Tax summary sections")
section_labels = [f"{s.name} ({s.kind}, {len(s.rows)} lines)" for s in sections]
chosen = st.multiselect(
    "Include these sections", section_labels, default=section_labels,
    help="Every EIT section is added into 'EIT Withheld' and every LST section into 'LST Withheld'.",
)
included = [s for s, label in zip(sections, section_labels) if label in chosen]
if not included:
    st.warning("Select at least one section.")
    st.stop()

if not work_psd:
    st.warning("Enter the Work PSD in the sidebar.")

result = build_filing(
    employees,
    included,
    FilingOptions(
        tax_year=str(int(tax_year)),
        tax_quarter=str(tax_quarter),
        tax_month=tax_month,
        work_psd=work_psd,
        ssn_digits_only=ssn_digits_only,
    ),
)
issues = [i for i in emp_issues + result.issues if not (i.message.startswith("Work PSD") and not work_psd)]

# ---- Problems to review ----------------------------------------------------
errors = [i for i in issues if i.severity == "error"]
warnings = [i for i in issues if i.severity == "warning"]
infos = [i for i in issues if i.severity == "info"]
st.subheader("Review")
if not issues:
    st.success("No problems found.")
else:
    if errors:
        st.error(f"{len(errors)} problem(s) to fix before filing. You can correct them in the table below.")
    if warnings:
        st.warning(f"{len(warnings)} item(s) to double-check.")
    issue_df = pd.DataFrame(
        [
            {
                "Severity": i.severity.title(),
                "Row": i.row if i.row else "",
                "Employee": i.name,
                "SSN": i.masked_ssn,
                "Message": i.message,
            }
            for i in errors + warnings + infos
        ]
    )
    st.dataframe(issue_df, hide_index=True, width="stretch")

# ---- Editable output -------------------------------------------------------
st.subheader("Filing data")
st.caption("Row numbers match the Review list. Click a cell to edit it before downloading.")
df = pd.DataFrame(result.rows, columns=TEMPLATE_COLUMNS, dtype=str)
df.index = range(1, len(df) + 1)
edited = st.data_editor(
    df,
    width="stretch",
    num_rows="dynamic",
    column_config={c: st.column_config.TextColumn(c) for c in TEMPLATE_COLUMNS},
    key=f"editor-{emp_file.file_id}-{summary_file.file_id}",
)
rows = [
    {c: ("" if pd.isna(v) else str(v)) for c, v in row.items()}
    for row in edited.to_dict(orient="records")
]

# ---- Totals and reconciliation --------------------------------------------
try:
    t = totals(rows)
except InputError as exc:
    st.error(f"A money column contains something that isn't a number: {exc}")
    st.stop()

m = st.columns(4)
m[0].metric("Employees", len(rows))
m[1].metric("Local Wages", f"${t['Local Wages']:,.2f}")
m[2].metric("EIT Withheld", f"${t['EIT Withheld']:,.2f}")
m[3].metric("LST Withheld", f"${t['LST Withheld']:,.2f}")

recon = []
for kind, col in (("EIT", "EIT Withheld"), ("LST", "LST Withheld")):
    secs = [s for s in included if s.kind == kind]
    if not secs:
        continue
    reported = sum((s.reported_tax if s.reported_tax is not None else s.tax for s in secs), 0)
    recon.append(
        {
            "Tax": kind,
            "Tax summary total": format_money(reported),
            "Filing total": format_money(t[col]),
            "Match": "✅" if reported == t[col] else "❌",
        }
    )
st.dataframe(pd.DataFrame(recon), hide_index=True)
if any(r["Match"] == "❌" for r in recon):
    st.error("Filing totals do not match the tax summary totals.")

# ---- Download ----------------------------------------------------------------
missing_psd = sum(1 for r in rows if not is_valid_psd(r["Resident PSD"]) or not is_valid_psd(r["Work PSD"]))
if missing_psd:
    st.warning(f"{missing_psd} row(s) still have a missing or invalid Resident/Work PSD.")

file_name = f"LocalTax_Filing_{int(tax_year)}_Q{tax_quarter}" + (f"_M{tax_month}" if tax_month else "") + ".csv"
st.download_button(
    "⬇️ Download filing CSV",
    data=to_csv(rows).encode("utf-8"),
    file_name=file_name,
    mime="text/csv",
    type="primary",
)
