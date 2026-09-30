# Local Tax Quarterly Report

Builds the local tax quarterly filing CSV (`samples/FilingTemplate.csv` layout) from two payroll exports:

1. **Employee info CSV**: `Employee, SS No., Street1, Street2, City, State, Zip, Township Code`
2. **Local Tax Summary CSV**: the payroll report with an EIT section (e.g. `TCD28`) and an `LST` section

## Privacy

The app runs on your own computer. Streamlit starts a small web server that listens only on `localhost`, and your browser talks to that server. Uploaded files stay in the memory of that local Python process. They are not sent to any outside service and are not saved to disk. Usage telemetry and the Deploy button are turned off in `.streamlit/config.toml`.

`.gitignore` blocks every `*.csv` except the fake files in `samples/`, so real payroll files can't be committed by accident.

## Running it

You need Python 3.10 or newer (on Windows, get it from https://www.python.org/downloads/).

- **Windows:** double-click `run_app.bat`
- **Mac/Linux:** `./run_app.sh`

The first run creates a `.venv` folder and installs Streamlit. After that, the app opens in your browser at http://localhost:8501. To stop it, close the terminal window.

Manual start: `pip install -r requirements.txt`, then `streamlit run app.py`.

## Using it

1. In the sidebar, set **Tax Year**, **Tax Quarter**, and **Work PSD**. Tax Month is optional. The Work PSD and the SSN format are remembered in `local_settings.json`.
2. Choose the two CSV files.
3. Work through the **Review** list. Row numbers point at the **Filing data** table, where you can click any cell to fix it.
4. Check that the EIT and LST totals match the tax summary (✅), then click **Download filing CSV**.

To try it out, use the fake files in `samples/`.

## How each column is filled

| Column | Source |
| --- | --- |
| Soc Sec No | SSN, which is also the key that joins the two files (formatted `123-45-6789`, or 9 digits if you check that option) |
| First / Middle / Last / Suffix | Employee name split on spaces. Handles `FIRST M. LAST JR` and `LAST, FIRST M`. Periods are removed from the middle name. |
| Physical / Mailing address | From the employee info file, falling back to the tax summary. A PO Box goes to Mailing and the other street line to Physical. Otherwise Street1 and Street2 are combined and used for both. |
| Tax Year / Quarter / Month | From the sidebar |
| Local Wages | EIT wage base, or the LST wage base if the employee had no EIT |
| EIT Withheld | Sum of Tax for the employee in all EIT sections (0.00 if none) |
| LST Withheld | Sum of Tax for the employee in the LST section (0.00 if none) |
| Resident PSD | `Township Code` from the employee info file. Out-of-state employees with a blank code get `880000`. |
| Work PSD | From the sidebar |

Each employee in the tax summary produces one row. People who appear only in the employee info file are left out.

The app flags these problems:

- **Error:** Resident PSD that isn't 6 digits (e.g. `ST THOMAS`, `17202`), blank for a PA resident, or unknown because the employee isn't in the info file. Also an incomplete address or an invalid Work PSD.
- **Warning:** an out-of-state address with a PA PSD code, or EIT and LST wage bases that differ.

## Command line

```
python -m taxreport employee_info.csv LocalTax_Summary.csv --year 2026 --quarter 3 --work-psd 280301 -o filing.csv
```

## Tests

```
pip install pytest
python -m pytest
```
