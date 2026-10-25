#!/usr/bin/env python3
"""Structural validator for ``output.csv`` (the submission contract of problem_statement.md / README.md).

Checks, per row: header and row count, sorting, status vocabulary, JSON validity, consecutive step numbers,
phase order, allowed (phase, operation, state) triples, intra-phase ordering (operation table order, then
system_id, then resource_id), evidence rules (findings and COMPLETE verification cite an existing evidence id
captured at or before ``audit_at``; remediation and the verification of an INCOMPLETE case are empty), the
shape of NOT_APPLICABLE rows and the consistency between status and phases.

Usage: python3 code/evaluation/validate_output.py [output.csv]
Exit code 0 when every check passes.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from ghost import policy as P  # noqa: E402
from ghost.dataset import DATASET, read_csv  # noqa: E402

PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
ALLOWED = {
    "FINDING": dict(P.FINDING_OPS),
    "REMEDIATION": dict(P.REMEDIATION_OPS),
    "VERIFICATION": dict(P.VERIFICATION_OPS),
}
FIELDS = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def validate(path: Path, requests=None, manifest=None, activity=None, audit_ids=None) -> list[str]:
    from ghost.timeutil import canon

    requests = requests or {r["case_id"]: dict(r, audit_at=canon(r["audit_at"]) or "") for r in read_csv("deletion_requests.csv")}
    manifest = manifest or {r["file_id"]: r for r in read_csv("evidence_files.csv")}
    activity_ids = activity if activity is not None else {r["activity_id"]: r for r in read_csv("system_activity.csv")}
    audit_ids = audit_ids if audit_ids is not None else {r["audit_id"] for r in read_csv("user_audit_log.csv")}
    errs: list[str] = []
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0] != ["case_id", "deletion_status", "steps_json"]:
        return ["header must be exactly case_id,deletion_status,steps_json"]
    data = rows[1:]
    ids = [r[0] for r in data]
    if len(data) != len(requests):
        errs.append(f"expected {len(requests)} data rows, found {len(data)}")
    if ids != sorted(ids):
        errs.append("rows are not sorted by case_id")
    if set(ids) != set(requests):
        errs.append(f"case_id set differs: missing={sorted(set(requests) - set(ids))[:5]} extra={sorted(set(ids) - set(requests))[:5]}")
    if len(set(ids)) != len(ids):
        errs.append("duplicate case_id rows")
    for r in data:
        if len(r) != 3:
            errs.append(f"{r[0] if r else '?'}: row does not have 3 columns")
            continue
        cid, status, raw = r
        req = requests.get(cid)
        if status not in {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}:
            errs.append(f"{cid}: bad status {status!r}")
        try:
            steps = json.loads(raw)
        except ValueError as e:
            errs.append(f"{cid}: steps_json is not valid JSON ({e})")
            continue
        if not isinstance(steps, list):
            errs.append(f"{cid}: steps_json is not an array")
            continue
        if status == "NOT_APPLICABLE":
            if steps != []:
                errs.append(f"{cid}: NOT_APPLICABLE must have steps_json == []")
            continue
        if not steps:
            # INCOMPLETE + [] is the documented safe fallback for an un-auditable case (degraded input or
            # unresolvable subject; flagged needs_manual_review). COMPLETE always needs proof.
            if status != "INCOMPLETE":
                errs.append(f"{cid}: {status} with empty steps")
            continue
        last_phase, last_key = -1, None
        for i, s in enumerate(steps, 1):
            if sorted(s) != sorted(FIELDS):
                errs.append(f"{cid} step {i}: fields {sorted(s)} != {sorted(FIELDS)}")
                continue
            if s["step_no"] != i:
                errs.append(f"{cid} step {i}: step_no is {s['step_no']}")
            if s["phase"] not in PHASES:
                errs.append(f"{cid} step {i}: bad phase {s['phase']}")
                continue
            ph = PHASES.index(s["phase"])
            if ph < last_phase:
                errs.append(f"{cid} step {i}: phase order violated")
            if ph != last_phase:
                last_key = None
            last_phase = ph
            if ALLOWED[s["phase"]].get(s["operation"]) != s["state"]:
                errs.append(f"{cid} step {i}: operation/state {s['operation']}/{s['state']} not allowed in {s['phase']}")
            key = (P.OP_RANK.get(s["operation"], 99), s["system_id"], s["resource_id"])
            if last_key is not None and key < last_key:
                errs.append(f"{cid} step {i}: intra-phase ordering violated")
            last_key = key
            ev = s["evidence_id"]
            needs_ev = s["phase"] == "FINDING" or (s["phase"] == "VERIFICATION" and status == "COMPLETE")
            if s["phase"] == "REMEDIATION" or (s["phase"] == "VERIFICATION" and status == "INCOMPLETE"):
                if ev != "":
                    errs.append(f"{cid} step {i}: future step must have empty evidence_id")
            elif needs_ev:
                if ev in manifest:
                    if req and manifest[ev]["captured_at"] > req["audit_at"]:
                        errs.append(f"{cid} step {i}: cites {ev} captured after audit_at")
                elif ev in activity_ids:
                    if req and activity_ids[ev]["event_time"] > req["audit_at"]:
                        errs.append(f"{cid} step {i}: cites {ev} dated after audit_at")
                elif ev in audit_ids:
                    pass
                else:
                    errs.append(f"{cid} step {i}: evidence_id {ev!r} is not a file_id/activity_id/audit_id")
        phases = {s["phase"] for s in steps if isinstance(s, dict) and "phase" in s}
        if status == "COMPLETE" and phases != {"VERIFICATION"}:
            errs.append(f"{cid}: COMPLETE must contain only VERIFICATION steps, has {sorted(phases)}")
        if status == "INCOMPLETE" and not {"FINDING", "REMEDIATION"} <= phases:
            errs.append(f"{cid}: INCOMPLETE must contain findings and remediation, has {sorted(phases)}")
        if status == "INCOMPLETE":
            for s in steps:
                if s.get("phase") == "REMEDIATION":
                    f = [x for x in steps if x["phase"] == "FINDING" and x["resource_id"] == s["resource_id"]]
                    v = [x for x in steps if x["phase"] == "VERIFICATION" and x["resource_id"] == s["resource_id"]]
                    if not f or not v:
                        errs.append(f"{cid}: remediated {s['resource_id']} lacks a finding or a verification")
    return errs


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output.csv"
    errs = validate(path)
    if errs:
        print(f"{len(errs)} problem(s):")
        for e in errs[:50]:
            print("  -", e)
        return 1
    print(f"{path.name}: all structural checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
