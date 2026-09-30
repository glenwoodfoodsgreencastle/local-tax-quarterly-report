#!/usr/bin/env sh
# Starts the Local Tax Quarterly Report app on this computer.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "First run: setting up Python environment..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip >/dev/null
    .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m streamlit run app.py
