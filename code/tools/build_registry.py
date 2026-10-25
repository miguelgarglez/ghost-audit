#!/usr/bin/env python3
"""Build the PyPI sdist + wheel and the npm tarball from this source tree (single source of truth).

    python3 code/tools/build_registry.py --out DIR [--python PY]

Stages (per the repository-root pyproject.toml) ghost/ (without ocr_cache/ and __pycache__/), ghost_cli.py, ghost_mcp.py, LICENSE and packaging/README.md
into temporary directories, then runs ``PY -m build`` (PY needs the ``build`` package) and ``npm pack``.
Nothing is published. The OCR cache transcribes challenge images, so it stays out of the published packages.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
ROOT = CODE.parent
PKG = CODE / "packaging"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "ocr_cache", ".DS_Store")


def stage_sources(dst: Path) -> None:
    shutil.copytree(CODE / "ghost", dst / "ghost", ignore=IGNORE)
    for f in ("ghost_cli.py", "ghost_mcp.py"):
        shutil.copy2(CODE / f, dst / f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args()
    out = Path(a.out).resolve()
    (out / "dist").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        # PyPI-style artifacts come from the repository-root pyproject.toml (also used by `pip install git+...`).
        py = Path(td) / "py"
        (py / "code" / "packaging").mkdir(parents=True)
        stage_sources(py / "code")
        shutil.copy2(PKG / "README.md", py / "code" / "packaging" / "README.md")
        for f in (ROOT / "pyproject.toml", ROOT / "LICENSE"):
            shutil.copy2(f, py / f.name)
        subprocess.run([a.python, "-m", "build", "--outdir", str(out / "dist"), str(py)], check=True)
        npm = Path(td) / "npm"
        shutil.copytree(PKG / "npm", npm)
        stage_sources(npm / "py")
        shutil.copy2(PKG / "README.md", npm / "README.md")
        shutil.copy2(ROOT / "LICENSE", npm / "LICENSE")
        subprocess.run(["npm", "pack", "--pack-destination", str(out)], check=True, cwd=npm)
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print(p.relative_to(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
