#!/usr/bin/env python3
"""Extract text from the PDF / PNG evidence once and cache it under code/ghost/ocr_cache/.

Needs ``pdftotext`` (poppler) and ``tesseract`` + Pillow. At audit time ``ghost.media`` only reads the cache
(and re-runs this extraction automatically if the cache is missing and the tools are installed), so the audit
itself has no hard dependency on OCR tooling.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ghost.media import CACHE_DIR, extract_text  # noqa: E402
from ghost.evidence import Manifest  # noqa: E402


def main() -> int:
    m = Manifest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for row in m.rows:
        p = m.path(row["file_id"])
        if p.suffix.lower() in (".pdf", ".png"):
            txt = extract_text(p, use_cache=False)
            (CACHE_DIR / (p.name + ".txt")).write_text(txt, encoding="utf-8")
            print(f"{row['file_id']} {p.name}: {len(txt)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
