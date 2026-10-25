#!/usr/bin/env python3
"""Portable (Windows/macOS/Linux) equivalent of ./package.sh: builds code.zip per the starter README rules.

    python3 code/tools/package.py [--check-only]

Includes README.md and code/, excludes __pycache__, venvs, node_modules, .git, .DS_Store; refuses nested archives and dataset/.
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDE = ["*/__pycache__/*", "*/.venv/*", "*/venv/*", "*/node_modules/*", "*/.git/*", "*/.DS_Store", "*.pyc"]
FORBIDDEN = (".zip", ".tar", ".gz", ".tgz", ".7z", ".rar")


def members() -> list[Path]:
    out = [ROOT / "README.md", ROOT / "Makefile", ROOT / "package.sh", ROOT / "LICENSE", ROOT / "CHANGELOG.md", ROOT / "pyproject.toml"]
    out += sorted((ROOT / "docs").glob("*.md"))  # root mirror of code/docs (INSTALL.md etc.)
    out += sorted((ROOT / "evaluation").glob("*_report.md"))  # root mirror required by problem statement
    for p in sorted((ROOT / "code").rglob("*")):
        rel = p.relative_to(ROOT).as_posix()
        if p.is_dir() or any(fnmatch.fnmatch(rel, pat) for pat in EXCLUDE):
            continue
        out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    files = members()
    bad = [p for p in files if p.suffix.lower() in FORBIDDEN or "dataset/" in p.relative_to(ROOT).as_posix()]
    if bad:
        print("forbidden entries:", bad)
        return 1
    if args.check_only:
        print(f"{len(files)} files would be packaged")
        return 0
    target = ROOT / "code.zip"
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(ROOT).as_posix())
    print(f"wrote {target} ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
