@echo off
REM Starts the Local Tax Quarterly Report app on this computer.
cd /d "%~dp0"
if not exist .venv (
    echo First run: setting up Python environment...
    py -3 -m venv .venv || python -m venv .venv || goto :nopython
    .venv\Scripts\python -m pip install --upgrade pip >nul
    .venv\Scripts\python -m pip install -r requirements.txt || goto :fail
)
.venv\Scripts\python -m streamlit run app.py
goto :eof

:nopython
echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/
pause
goto :eof

:fail
echo Could not install requirements.
pause
