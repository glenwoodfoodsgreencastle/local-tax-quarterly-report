# Local Tax Quarterly Report

Builds the local tax quarterly filing CSV (`samples/FilingTemplate.csv` layout) from two payroll exports:

1. **Employee info CSV**: `Employee, SS No., Street1, Street2, City, State, Zip, Township Code`
2. **Local Tax Summary CSV**: the payroll report with an EIT section (e.g. `TCD28`) and an `LST` section

There are two ways to use it. Both use the same conversion code (`taxreport/converter.py`) and give the same output.

- **Web version (GitHub Pages):** https://glenwoodfoodsgreencastle.github.io/local-tax-quarterly-report/. Nothing to install.
- **Local app:** runs on your computer with Python and Streamlit.

## Privacy

**Web version.** GitHub Pages only serves static files; there is no server that could receive data. Python runs inside your browser tab using [Pyodide](https://pyodide.org) (Python compiled to WebAssembly), and Pyodide is served from this same site rather than a third-party CDN. The CSV files you choose are read into the tab's memory, converted there, and the download is created there. The page's Content Security Policy (`web/index.html`) tells the browser to refuse any connection to another site. The automated tests confirm that the page makes no requests to other sites and that an attempt to send data elsewhere is blocked. The only thing saved is your Work PSD and SSN-format choice, in the browser's local storage. To check this yourself, open the browser's developer tools (F12), go to the Network tab, and process a file. No requests appear after the page loads.

**Local app.** Streamlit starts a web server that listens only on `localhost`. Files stay in the memory of that local Python process. Usage telemetry and the Deploy button are turned off in `.streamlit/config.toml`.

**Repository.** `.gitignore` blocks every `*.csv` except `samples/FilingTemplate.csv`, so real payroll files can't be committed by accident. The repository is public, so the code is visible to anyone, but it contains no employee data. The tests use small made-up records defined in `tests/test_converter.py`.

## Using it

1. Set **Tax Year**, **Tax Quarter**, and **Work PSD** (defaults to `280301`; change it if needed). Tax Month is optional. The Work PSD and SSN format are remembered for next time.
2. Choose the two CSV files.
3. Work through the **Review** list. Clicking an item in the web version jumps to that row. Click any cell in the **Filing data** table to fix it. Invalid cells are outlined in red.
4. Check that the EIT and LST totals match the tax summary, then click **Download filing CSV**.


## Web version setup (one time)

1. In GitHub, go to the repository's **Settings → Pages**. Under **Build and deployment → Source**, choose **GitHub Actions**.
2. Go to **Actions → Test and deploy site → Run workflow**, or push a commit.

The workflow in `.github/workflows/pages.yml` runs the tests (including the browser test) on every push. It deploys only from the default branch, and only if the tests pass.

To preview the site locally:

```
python build_site.py
python -m http.server 8000 -d _site
```

Then open http://localhost:8000. `build_site.py` downloads Pyodide once from the npm registry, checks it against a pinned SHA-256, and caches it in `.cache/`. To upgrade Pyodide, change `PYODIDE_VERSION` and `PYODIDE_SHA256` in `build_site.py`.

## Local app

You need Python 3.10 or newer (on Windows, get it from https://www.python.org/downloads/).

- **Windows:** double-click `run_app.bat`
- **Mac/Linux:** `./run_app.sh`

The first run creates a `.venv` folder and installs Streamlit. After that, the app opens in your browser at http://localhost:8501. To stop it, close the terminal window. Settings are saved in `local_settings.json`.

## How each column is filled

| Column | Source |
| --- | --- |
| Soc Sec No | SSN, which is also the key that joins the two files (formatted `123-45-6789`, or 9 digits if you check that option) |
| First / Middle / Last / Suffix | Employee name split on spaces. Handles `FIRST M. LAST JR` and `LAST, FIRST M`. Periods are removed from the middle name. |
| Physical / Mailing address | From the employee info file, falling back to the tax summary. A PO Box goes to Mailing and the other street line to Physical. Otherwise Street1 and Street2 are combined and used for both. |
| Tax Year / Quarter / Month | From the settings |
| Local Wages | EIT wage base, or the LST wage base if the employee had no EIT |
| EIT Withheld | Sum of Tax for the employee in all EIT sections (0.00 if none) |
| LST Withheld | Sum of Tax for the employee in the LST section (0.00 if none) |
| Resident PSD | `Township Code` from the employee info file. Out-of-state employees with a blank code get `880000`. |
| Work PSD | From the settings (default `280301`) |

Each employee in the tax summary produces one row. The employee info file can list every employee, active or inactive (so people who quit during the quarter are still matched). Rows for people not in the tax summary, including rows with no SSN, are ignored without warnings. If an SSN appears more than once, the row with a valid Township Code is used.

The app flags these problems:

- **Error:** Resident PSD that isn't 6 digits (e.g. `ST THOMAS`, `17202`), blank for a PA resident, or unknown because the employee isn't in the info file. Also an incomplete address or an invalid Work PSD.
- **Warning:** an out-of-state address with a PA PSD code, or EIT and LST wage bases that differ.

## Command line

```
python -m taxreport employee_info.csv LocalTax_Summary.csv --year 2026 --quarter 3 -o filing.csv
```

`--work-psd` defaults to `280301`.

## Tests

```
pip install pytest playwright
python -m playwright install chromium
python build_site.py
python -m pytest
```

The browser test (`tests/test_site.py`) is skipped if the site hasn't been built or Playwright isn't installed.
