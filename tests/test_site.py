"""End-to-end test of the GitHub Pages site in a real browser.

Needs `python build_site.py` first and Playwright with Chromium installed;
skipped otherwise. Set CHROMIUM_PATH to use a specific Chromium binary.
"""

import functools
import http.server
import os
import threading
from pathlib import Path

import pytest

from taxreport.converter import (
    FilingOptions,
    build_filing,
    load_employee_info,
    parse_local_tax_summary,
    to_csv,
)

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"
SAMPLES = ROOT / "samples"

pytestmark = pytest.mark.skipif(
    not (SITE / "pyodide" / "pyodide.mjs").exists(), reason="run build_site.py first"
)


@pytest.fixture(scope="module")
def site_url():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        path = os.environ.get("CHROMIUM_PATH")
        b = p.chromium.launch(executable_path=path) if path else p.chromium.launch()
        yield b
        b.close()


def test_site_builds_filing_in_browser(site_url, browser, tmp_path):
    page = browser.new_page(accept_downloads=True)
    errors, hosts = [], set()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("request", lambda r: hosts.add(r.url.split("/")[2]))

    page.goto(site_url)
    page.wait_for_selector("#status.ready, #status.failed", timeout=90_000)
    assert "Ready" in page.text_content("#status")
    assert page.input_value("#work-psd") == "280301"

    page.fill("#tax-year", "2026")
    page.select_option("#tax-quarter", "3")
    page.set_input_files("#file-emp", str(SAMPLES / "employee_info_sample.csv"))
    page.set_input_files("#file-summary", str(SAMPLES / "LocalTax_Summary_sample.csv"))
    page.wait_for_selector("#results:not([hidden])", timeout=30_000)

    assert page.text_content("#m-count") == "50"
    assert page.text_content("#m-eit") == "$3,312.76"
    assert page.text_content("#m-lst") == "$592.00"
    assert page.locator("#recon .match-no").count() == 0

    with page.expect_download() as info:
        page.once("dialog", lambda d: d.accept())  # "invalid cells" confirmation
        page.click("#download")
    out = tmp_path / "filing.csv"
    info.value.save_as(out)

    employees, _ = load_employee_info((SAMPLES / "employee_info_sample.csv").read_bytes())
    sections = parse_local_tax_summary((SAMPLES / "LocalTax_Summary_sample.csv").read_bytes())
    expected = to_csv(build_filing(employees, sections, FilingOptions("2026", "3", "280301")).rows)
    assert out.read_bytes().decode("utf-8") == expected

    # Editing a cell updates validation.
    bad_before = page.locator("td.bad").count()
    cell = page.locator('tr[data-row="1"] td[data-col="Resident PSD"]')
    cell.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("280404")
    assert page.locator("td.bad").count() == bad_before - 1

    # Content Security Policy blocks sending data to other sites.
    blocked = page.evaluate(
        "fetch('https://example.com/', {method: 'POST', body: 'x'}).then(() => false, () => true)"
    )
    assert blocked
    assert hosts == {site_url.split("/")[2]}
    assert not [e for e in errors if "example.com" not in e and "Content Security Policy" not in e]
