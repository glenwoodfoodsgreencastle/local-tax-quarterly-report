// Local Tax Quarterly Report: browser front end.
//
// Everything happens in this tab. Files are read with the File API, handed
// to Python (Pyodide, served from this site) for conversion, and the result
// is offered back as a download made from an in-memory Blob.

import { loadPyodide } from "./pyodide/pyodide.mjs";

const DEFAULT_WORK_PSD = "280301";
const PSD_RE = /^\d{6}$/;
const MONEY_RE = /^-?\d+(\.\d{1,2})?$/;
const SSN_RE = /^(\d{3}-\d{2}-\d{4}|\d{9})$/;
const ZIP_RE = /^\d{5}(-?\d{4})?$/;
const MONEY_COLS = ["Local Wages", "EIT Withheld", "LST Withheld"];

const $ = (id) => document.getElementById(id);

// Only settings are kept in the browser, never employee data.
const prefs = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem(key);
      return v === null ? fallback : v;
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* storage blocked: settings just won't be remembered */
    }
  },
};

const state = {
  files: { emp: null, summary: null }, // { name, bytes: Uint8Array }
  columns: [],
  sections: [],
  excluded: new Set(),
  rows: [], // [{ ...template columns, _deleted: bool }]
  issues: [],
  edited: false,
};

// ---------------------------------------------------------------------------
// Python
// ---------------------------------------------------------------------------

const python = (async () => {
  const py = await loadPyodide({ indexURL: new URL("./pyodide/", import.meta.url).href });
  py.FS.mkdirTree("/app/taxreport");
  for (const path of ["taxreport/__init__.py", "taxreport/converter.py", "bridge.py"]) {
    const resp = await fetch(`py/${path}`);
    if (!resp.ok) throw new Error(`Could not load ${path}`);
    py.FS.writeFile(`/app/${path}`, await resp.text());
  }
  py.runPython("import sys; sys.path.insert(0, '/app')");
  return py.pyimport("bridge");
})();

python.then(
  () => setStatus("ready", "Ready. Choose your two CSV files."),
  (err) => setStatus("failed", `Python failed to load: ${err.message}`),
);

function setStatus(kind, text) {
  const el = $("status");
  el.className = `status ${kind}`;
  el.textContent = text;
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

function lastCompletedQuarter(today = new Date()) {
  const q = Math.floor(today.getMonth() / 3); // 0 means Q4 of last year
  return q ? [today.getFullYear(), q] : [today.getFullYear() - 1, 4];
}

function settings() {
  return {
    tax_year: $("tax-year").value.trim(),
    tax_quarter: $("tax-quarter").value,
    tax_month: $("tax-month").value,
    work_psd: $("work-psd").value.trim(),
    ssn_digits_only: $("ssn-digits").checked,
  };
}

function initSettings() {
  const [year, quarter] = lastCompletedQuarter();
  $("tax-year").value = year;
  $("tax-quarter").value = String(quarter);
  $("work-psd").value = prefs.get("workPsd", DEFAULT_WORK_PSD);
  $("ssn-digits").checked = prefs.get("ssnDigitsOnly", "0") === "1";

  for (const id of ["tax-year", "tax-quarter", "tax-month", "work-psd", "ssn-digits"]) {
    $(id).addEventListener(id === "work-psd" || id === "tax-year" ? "input" : "change", onSettingsChange);
  }
  validateWorkPsd();
}

function validateWorkPsd() {
  const ok = PSD_RE.test($("work-psd").value.trim());
  $("work-psd").classList.toggle("invalid", !ok);
  $("work-psd-error").hidden = ok;
  return ok;
}

function formatSsn(value, digitsOnly) {
  const d = String(value).replace(/\D/g, "");
  if (d.length !== 9) return value;
  return digitsOnly ? d : `${d.slice(0, 3)}-${d.slice(3, 5)}-${d.slice(5)}`;
}

function onSettingsChange() {
  const s = settings();
  if (validateWorkPsd()) prefs.set("workPsd", s.work_psd);
  prefs.set("ssnDigitsOnly", s.ssn_digits_only ? "1" : "0");
  if (!state.rows.length) return;
  // Apply to the existing table so edits are kept.
  for (const row of state.rows) {
    row["Tax Year"] = s.tax_year;
    row["Tax Quarter"] = s.tax_quarter;
    row["Tax Month"] = s.tax_month;
    row["Work PSD"] = s.work_psd;
    row["Soc Sec No"] = formatSsn(row["Soc Sec No"], s.ssn_digits_only);
  }
  renderTable();
  updateSummary();
}

// ---------------------------------------------------------------------------
// Files
// ---------------------------------------------------------------------------

function initFiles() {
  for (const [key, input, drop, label] of [
    ["emp", "file-emp", "drop-emp", "name-emp"],
    ["summary", "file-summary", "drop-summary", "name-summary"],
  ]) {
    const dropEl = $(drop);
    $(input).addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (file) readFile(key, file, dropEl, $(label));
    });
    dropEl.addEventListener("dragover", (e) => {
      e.preventDefault();
      dropEl.classList.add("dragover");
    });
    dropEl.addEventListener("dragleave", () => dropEl.classList.remove("dragover"));
    dropEl.addEventListener("drop", (e) => {
      e.preventDefault();
      dropEl.classList.remove("dragover");
      const file = e.dataTransfer.files[0];
      if (file) readFile(key, file, dropEl, $(label));
    });
  }
}

async function readFile(key, file, dropEl, labelEl) {
  state.files[key] = { name: file.name, bytes: new Uint8Array(await file.arrayBuffer()) };
  labelEl.textContent = `✓ ${file.name}`;
  dropEl.classList.add("has-file");
  state.excluded.clear();
  if (state.files.emp && state.files.summary) await build();
}

// ---------------------------------------------------------------------------
// Build
// ---------------------------------------------------------------------------

async function build() {
  const errorEl = $("load-error");
  errorEl.hidden = true;
  let bridge;
  try {
    setStatus("loading", "Working…");
    bridge = await python;
  } catch {
    return; // status already shows the load failure
  }
  let result;
  try {
    const opts = { ...settings(), excluded_sections: [...state.excluded] };
    result = JSON.parse(bridge.process(state.files.emp.bytes, state.files.summary.bytes, JSON.stringify(opts)));
  } catch (err) {
    result = { error: `Could not read the files: ${err.message}` };
  }
  if (result.error) {
    errorEl.textContent = result.error;
    errorEl.hidden = false;
    $("results").hidden = true;
    setStatus("failed", "Fix the problem above and choose the file again.");
    return;
  }
  state.columns = result.columns;
  state.sections = result.sections;
  state.rows = result.rows.map((r) => ({ ...r, _deleted: false }));
  state.issues = result.issues;
  state.edited = false;
  setStatus("ready", `Built ${state.rows.length} employee rows.`);
  $("results").hidden = false;
  renderSections();
  renderIssues();
  renderTable();
  updateSummary();
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node[k] = v;
  }
  node.append(...children);
  return node;
}

const money = (cents) =>
  (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });

const toCents = (value) => (MONEY_RE.test(String(value).trim()) ? Math.round(parseFloat(value) * 100) : 0);

function renderSections() {
  const box = $("sections");
  box.replaceChildren(
    ...state.sections.map((s, i) =>
      el(
        "label",
        {},
        el("input", { type: "checkbox", checked: s.included, onchange: (e) => onSectionToggle(i, e.target) }),
        s.name,
        el("span", { class: "meta" }, `${s.kind} · ${s.lines} lines · ${money(toCents(s.reported_tax))}`),
      ),
    ),
  );
}

async function onSectionToggle(index, checkbox) {
  if (state.edited && !confirm("Changing sections rebuilds the table and discards your edits. Continue?")) {
    checkbox.checked = !checkbox.checked;
    return;
  }
  if (checkbox.checked) state.excluded.delete(index);
  else state.excluded.add(index);
  await build();
}

function renderIssues() {
  const errors = state.issues.filter((i) => i.severity === "error").length;
  const warnings = state.issues.filter((i) => i.severity === "warning").length;
  const banners = [];
  if (!state.issues.length) banners.push(el("p", { class: "banner ok" }, "No problems found."));
  if (errors)
    banners.push(el("p", { class: "banner error" }, `${errors} problem(s) to fix before filing. Click one to jump to its row.`));
  if (warnings) banners.push(el("p", { class: "banner warn" }, `${warnings} item(s) to double-check.`));
  $("review-banners").replaceChildren(...banners);
  $("issues-wrap").hidden = !state.issues.length;
  $("issues").replaceChildren(
    ...state.issues.map((i) =>
      el(
        "tr",
        { onclick: () => i.row && focusRow(i.row, i.message) },
        el("td", {}, el("span", { class: `sev sev-${i.severity}` }, i.severity[0].toUpperCase() + i.severity.slice(1))),
        el("td", { class: "num" }, i.row ? String(i.row) : ""),
        el("td", {}, i.name),
        el("td", {}, i.ssn),
        el("td", {}, i.message),
      ),
    ),
  );
}

function cellProblem(col, value) {
  const v = String(value ?? "").trim();
  switch (col) {
    case "Resident PSD":
    case "Work PSD":
      return PSD_RE.test(v) ? "" : "Must be a 6-digit PSD code.";
    case "Soc Sec No":
      return SSN_RE.test(v) ? "" : "Must be 9 digits (123-45-6789 or 123456789).";
    case "Physical Zip":
    case "Mailing Zip":
      return ZIP_RE.test(v) ? "" : "Must be a 5 or 9 digit ZIP code.";
    case "Tax Year":
      return /^\d{4}$/.test(v) ? "" : "Must be a 4-digit year.";
    case "Tax Quarter":
      return /^[1-4]$/.test(v) ? "" : "Must be 1, 2, 3 or 4.";
    case "Tax Month":
      return v === "" || /^(?:[1-9]|1[0-2])$/.test(v) ? "" : "Must be blank or 1-12.";
    case "First Name":
    case "Last Name":
    case "Physical Street Address":
    case "Physical City":
    case "Physical State":
      return v ? "" : "Required.";
    default:
      return MONEY_COLS.includes(col) && !MONEY_RE.test(v) ? "Must be an amount like 1234.56." : "";
  }
}

function paintCell(td, col, value) {
  const problem = cellProblem(col, value);
  td.classList.toggle("bad", Boolean(problem));
  td.title = problem;
}

function renderTable() {
  const table = $("data");
  const head = el(
    "tr",
    {},
    el("th", { class: "rownum" }, "#"),
    ...state.columns.map((c) => el("th", {}, c)),
    el("th", {}, ""),
  );
  const body = el("tbody");
  state.rows.forEach((row, r) => {
    const tr = el("tr", { class: row._deleted ? "deleted" : "", dataset: { row: r + 1 } });
    tr.append(el("td", { class: "rownum" }, String(r + 1)));
    for (const col of state.columns) {
      const td = el("td", {
        class: MONEY_COLS.includes(col) ? "num" : "",
        textContent: row[col] ?? "",
        dataset: { r, col },
      });
      if (!row._deleted) {
        try {
          td.contentEditable = "plaintext-only";
        } catch {
          td.contentEditable = "true";
        }
        td.spellcheck = false;
        paintCell(td, col, row[col]);
      }
      tr.append(td);
    }
    tr.append(
      el(
        "td",
        { class: "act" },
        el("button", { type: "button", onclick: () => toggleDeleted(r) }, row._deleted ? "Restore" : "Remove"),
      ),
    );
    body.append(tr);
  });
  table.replaceChildren(el("thead", {}, head), body);
}

function toggleDeleted(r) {
  state.rows[r]._deleted = !state.rows[r]._deleted;
  state.edited = true;
  renderTable();
  updateSummary();
}

function initTableEditing() {
  const table = $("data");
  table.addEventListener("input", (e) => {
    const td = e.target.closest("td[data-col]");
    if (!td) return;
    const { r, col } = td.dataset;
    state.rows[r][col] = td.textContent.trim();
    state.edited = true;
    paintCell(td, col, state.rows[r][col]);
    updateSummary();
  });
  table.addEventListener("focusout", (e) => {
    const td = e.target.closest("td[data-col]");
    if (td && td.textContent !== td.textContent.trim()) td.textContent = td.textContent.trim();
  });
  table.addEventListener("keydown", (e) => {
    const td = e.target.closest("td[data-col]");
    if (!td || e.key !== "Enter") return;
    e.preventDefault(); // keep values on one line; Enter moves down
    const next = table.querySelector(`td[data-r="${Number(td.dataset.r) + 1}"][data-col="${CSS.escape(td.dataset.col)}"]`);
    if (next) next.focus();
    else td.blur();
  });
}

function focusRow(rowNumber, message) {
  const tr = $("data").querySelector(`tr[data-row="${rowNumber}"]`);
  if (!tr) return;
  const col = /PSD/.test(message) ? "Resident PSD" : /[Aa]ddress/.test(message) ? "Physical Street Address" : "Soc Sec No";
  const td = tr.querySelector(`td[data-col="${col}"]`) || tr.querySelector("td[data-col]");
  tr.scrollIntoView({ block: "center" });
  td.scrollIntoView({ block: "nearest", inline: "center" });
  td.focus();
  tr.classList.add("flash");
  setTimeout(() => tr.classList.remove("flash"), 1500);
}

function updateSummary() {
  const active = state.rows.filter((r) => !r._deleted);
  const sum = (col) => active.reduce((t, r) => t + toCents(r[col]), 0);
  const totals = { "Local Wages": sum("Local Wages"), "EIT Withheld": sum("EIT Withheld"), "LST Withheld": sum("LST Withheld") };
  $("m-count").textContent = String(active.length);
  $("m-wages").textContent = money(totals["Local Wages"]);
  $("m-eit").textContent = money(totals["EIT Withheld"]);
  $("m-lst").textContent = money(totals["LST Withheld"]);

  let mismatch = false;
  const reconRows = [];
  for (const [kind, col] of [["EIT", "EIT Withheld"], ["LST", "LST Withheld"]]) {
    const secs = state.sections.filter((s) => s.included && s.kind === kind);
    if (!secs.length) continue;
    const reported = secs.reduce((t, s) => t + toCents(s.reported_tax), 0);
    const ok = reported === totals[col];
    mismatch ||= !ok;
    reconRows.push(
      el(
        "tr",
        {},
        el("td", {}, kind),
        el("td", {}, money(reported)),
        el("td", {}, money(totals[col])),
        el("td", { class: ok ? "match-yes" : "match-no" }, ok ? "✓ Match" : "✗ Different"),
      ),
    );
  }
  $("recon").replaceChildren(...reconRows);

  const bad = invalidCellCount();
  const banners = [];
  if (mismatch) banners.push(el("p", { class: "banner error" }, "Filing totals do not match the tax summary totals."));
  if (bad) banners.push(el("p", { class: "banner warn" }, `${bad} cell(s) are still invalid (outlined in red in the table).`));
  $("download-banners").replaceChildren(...banners);
}

function invalidCellCount() {
  let n = 0;
  for (const row of state.rows) {
    if (row._deleted) continue;
    for (const col of state.columns) if (cellProblem(col, row[col])) n++;
  }
  return n;
}

// ---------------------------------------------------------------------------
// Download / clear
// ---------------------------------------------------------------------------

async function download() {
  const bad = invalidCellCount();
  if (bad && !confirm(`${bad} cell(s) are still invalid. Download anyway?`)) return;
  const bridge = await python;
  const rows = state.rows
    .filter((r) => !r._deleted)
    .map((r) => Object.fromEntries(state.columns.map((c) => [c, r[c] ?? ""])));
  const csv = bridge.render_csv(JSON.stringify(rows));
  const s = settings();
  const name = `LocalTax_Filing_${s.tax_year}_Q${s.tax_quarter}${s.tax_month ? `_M${s.tax_month}` : ""}.csv`;
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function clearAll() {
  state.files = { emp: null, summary: null };
  state.rows = [];
  state.sections = [];
  state.issues = [];
  state.excluded.clear();
  state.edited = false;
  for (const id of ["file-emp", "file-summary"]) $(id).value = "";
  $("name-emp").textContent = $("name-summary").textContent = "Click to choose or drop a file here";
  $("drop-emp").classList.remove("has-file");
  $("drop-summary").classList.remove("has-file");
  $("data").replaceChildren();
  $("issues").replaceChildren();
  $("load-error").hidden = true;
  $("results").hidden = true;
  setStatus("ready", "Cleared. Choose your two CSV files.");
}

window.addEventListener("beforeunload", (e) => {
  if (state.edited) e.preventDefault();
});

initSettings();
initFiles();
initTableEditing();
$("download").addEventListener("click", download);
$("clear").addEventListener("click", clearAll);
