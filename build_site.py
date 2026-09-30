"""Build the static GitHub Pages site into _site/.

    python build_site.py
    python -m http.server 8000 -d _site     # preview at http://localhost:8000

The site is web/ + taxreport/converter.py + a self-hosted copy of Pyodide
(Python compiled to WebAssembly). Pyodide is downloaded from the npm registry
once, checked against a pinned SHA-256, and cached in .cache/.
"""

import hashlib
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

PYODIDE_VERSION = "314.0.7"
PYODIDE_SHA256 = "66b7949e5a0cba2250b586d7e368bf12fb482800748d182c4618254b685ffaa0"
PYODIDE_URL = f"https://registry.npmjs.org/pyodide/-/pyodide-{PYODIDE_VERSION}.tgz"
PYODIDE_FILES = [
    "pyodide.mjs",
    "pyodide.asm.mjs",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
]

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_site"
CACHE = ROOT / ".cache"


def fetch_pyodide() -> Path:
    CACHE.mkdir(exist_ok=True)
    tgz = CACHE / f"pyodide-{PYODIDE_VERSION}.tgz"
    if not tgz.exists():
        print(f"Downloading {PYODIDE_URL}")
        with urllib.request.urlopen(PYODIDE_URL) as resp, open(tgz, "wb") as f:
            shutil.copyfileobj(resp, f)
    digest = hashlib.sha256(tgz.read_bytes()).hexdigest()
    if digest != PYODIDE_SHA256:
        tgz.unlink()
        sys.exit(f"Pyodide checksum mismatch: got {digest}, expected {PYODIDE_SHA256}")
    return tgz


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "web", OUT)
    (OUT / ".nojekyll").touch()

    pkg = OUT / "py" / "taxreport"
    pkg.mkdir(parents=True)
    for name in ("__init__.py", "converter.py"):
        shutil.copy2(ROOT / "taxreport" / name, pkg / name)

    dest = OUT / "pyodide"
    dest.mkdir()
    with tarfile.open(fetch_pyodide()) as tar:
        for name in PYODIDE_FILES:
            src = tar.extractfile(f"package/{name}")
            if src is None:
                sys.exit(f"{name} missing from Pyodide package")
            (dest / name).write_bytes(src.read())

    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"Built {OUT} ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
