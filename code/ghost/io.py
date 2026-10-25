"""Output serialisation."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List

from . import model as M


def steps_json(steps: List[M.Step]) -> str:
    return json.dumps([s.as_json(i + 1) for i, s in enumerate(steps)], ensure_ascii=False, separators=(",", ":"))


def write_output(path: Path, rows: Dict[str, tuple]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for cid in sorted(rows):
            status, steps = rows[cid]
            w.writerow([cid, status, steps_json(steps)])
