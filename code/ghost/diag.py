"""Ingest diagnostics: everything the pipeline skipped, repaired or degraded instead of crashing.

The audit never stops on one odd file, row or case. Each problem is recorded here, printed to stderr as
``[ingest] ...`` lines by ``code/main.py`` and exposed by ``ghost_cli.py ingest-report`` / the MCP tool
``ghost_ingest_report``. On the shipped dataset the report is empty.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

REPORT: Dict[str, list] = defaultdict(list)


def reset() -> None:
    REPORT.clear()


def note(section: str, **item) -> None:
    REPORT[section].append(item)


def case_issue(case_id: str, reason: str) -> None:
    note("degraded_cases", case_id=case_id, reason=reason)


def snapshot() -> dict:
    keys = ("unparsed_files", "repaired_files", "csv_issues", "ignored_rows", "degraded_cases")
    out = {k: list(REPORT.get(k, [])) for k in keys}
    out["clean"] = not any(out.values())
    out["counts"] = {k: len(out[k]) for k in keys}
    return out


def lines() -> List[str]:
    s = snapshot()
    out = []
    for k in ("unparsed_files", "repaired_files", "csv_issues", "ignored_rows"):
        for it in s[k]:
            out.append(f"[ingest] {k[:-1] if k.endswith('s') else k}: " + ", ".join(f"{a}={b}" for a, b in it.items()))
    for it in s["degraded_cases"]:
        out.append(f"[ingest] {it['case_id']} needs_manual_review: {it['reason']}")
    if out:
        out.append(f"[ingest] summary: {s['counts']}")
    return out
