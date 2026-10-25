#!/usr/bin/env python3
"""Ghost Data Still Exists - deletion audit agent.

Usage:  python3 code/main.py [--out output.csv]

Reads ``dataset/`` (never organiser-only files), audits every request in ``deletion_requests.csv`` and writes
``output.csv`` at the repository root.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghost import diag  # noqa: E402
from ghost.audit import Auditor  # noqa: E402
from ghost.io import write_output  # noqa: E402
from ghost.world import World  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def run(out: Path) -> dict:
    diag.reset()
    world = World()
    auditor = Auditor(world)
    auditor.prepare()
    rows = {}
    for case in world.cases:
        rows[case.case_id] = auditor.plan(auditor.work[case.case_id])
    write_output(out, rows)
    for line in diag.lines():  # ingest report: empty on the shipped dataset
        print(line, file=sys.stderr)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "output.csv"))
    args = ap.parse_args()
    rows = run(Path(args.out))
    from collections import Counter

    print(f"wrote {len(rows)} rows to {args.out}")
    print(dict(Counter(status for status, _ in rows.values())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
