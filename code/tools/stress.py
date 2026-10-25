#!/usr/bin/env python3
"""Adversarial stress harness: perturb the real dataset in a temp dir and check the pipeline never breaks.

    python3 code/tools/stress.py [--only NAME ...] [--keep] [--json]

Each scenario copies ``dataset/`` to a temporary directory, applies one perturbation, runs
``code/main.py`` against it (``GHOST_DATASET``) and checks:

* the process exits 0 (never crashes on odd input);
* ``output.csv`` passes ``validate_output.py`` for that perturbed dataset;
* an optional scenario-specific expectation (``same`` = byte-identical to the unperturbed run,
  ``review`` = the affected case is flagged ``needs_manual_review`` in the ingest diagnostics,
  ``diag`` = the oddity is reported as an ``[ingest]`` diagnostic on stderr).

Deterministic: perturbations use fixed seeds. Exit code 0 when every scenario passes.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(os.environ.get("GHOST_DATASET", ROOT / "dataset"))
MAIN = ROOT / "code" / "main.py"
VALIDATE = ROOT / "code" / "evaluation" / "validate_output.py"


# ------------------------------------------------------------------ csv helpers
def rd(d: Path, name: str):
    with open(d / name, newline="", encoding="utf-8-sig") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames or []), list(r)


def wr(d: Path, name: str, fields, rows, *, bom=False, crlf=False):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\r\n" if crlf else "\n", extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    with open(d / name, "w", encoding="utf-8", newline="") as fh:
        fh.write(("\ufeff" if bom else "") + buf.getvalue())


def edit(d, name, fn):
    f, rows = rd(d, name)
    out = fn(f, rows)
    if out is not None:
        f, rows = out
    wr(d, name, f, rows)


def req(d, fn):
    edit(d, "deletion_requests.csv", fn)


def first_case(d):
    return rd(d, "deletion_requests.csv")[1][0]


def evidence_path(d, ext):
    _, rows = rd(d, "evidence_files.csv")
    for r in rows:
        if r["file_path"].endswith(ext):
            return d / r["file_path"], r
    raise LookupError(ext)


# ------------------------------------------------------------------ scenarios
# name -> (perturb(d), expectation)  expectation: None | "same" | "review"
S = {}


def scenario(name, expect=None):
    def deco(fn):
        S[name] = (fn, expect)
        return fn
    return deco


@scenario("email_case_and_spaces", "same")
def _(d):
    req(d, lambda f, rs: [r.update(email="  " + r["email"].upper() + " ") for r in rs] and None)


@scenario("email_plus_tag_alias", "diag")
def _(d):
    req(d, lambda f, rs: [r.update(email=r["email"].replace("@", "+gdpr@", 1)) for r in rs[:5]] and None)


@scenario("email_unicode_lookalike", "diag")
def _(d):
    req(d, lambda f, rs: [r.update(email=r["email"].replace("a", "\u0430", 1)) for r in rs[:5]] and None)


@scenario("phone_other_formats", "same")
def _(d):
    def fn(f, rs):
        for r in rs:
            if r["phone"]:
                digits = "".join(c for c in r["phone"] if c.isdigit())
                r["phone"] = "+" + digits if int(digits) % 2 else "+" + "-".join([digits[:2], digits[2:6], digits[6:]])
    edit(d, "user_identities.csv", fn)


@scenario("identity_chain_3_changes", "diag")
def _(d):
    f, rows = rd(d, "user_audit_log.csv")
    _, ids = rd(d, "user_identities.csv")
    u = ids[0]
    cur = u["email"]
    for i in range(3):
        nxt = f"chain{i}.{cur}"
        rows.append({"audit_id": f"audit_stress_{i}", "user_id": u["user_id"], "event_time": f"2025-0{i+1}-01T00:00:00Z",
                     "field_name": "email", "old_value": nxt, "new_value": cur})
        cur = nxt
    wr(d, "user_audit_log.csv", f, rows)
    req(d, lambda f2, rs: rs.append(dict(rs[0], case_id="case_9001", email="  " + cur.upper()))) or None


@scenario("duplicate_requests", "diag")
def _(d):
    req(d, lambda f, rs: rs.append(dict(rs[0])) or None)


@scenario("request_without_user", "review")
def _(d):
    req(d, lambda f, rs: rs.append(dict(rs[0], case_id="case_9002", email="nobody.at.all@unknown.example")) or None)


@scenario("user_without_evidence", "diag")
def _(d):
    edit(d, "user_identities.csv", lambda f, rs: rs.append(dict(rs[0], user_id="user_9003", name="No Evidence", email="no.evidence@example.org", phone="", email_hash="0" * 64)) or None)
    req(d, lambda f, rs: rs.append(dict(rs[0], case_id="case_9003", email="no.evidence@example.org")) or None)


@scenario("unknown_data_category_and_missing_rule", "diag")
def _(d):
    f, rows = rd(d, "deletion_rules.csv")
    wr(d, "deletion_rules.csv", f, rows[1:] + [dict(rows[0], rule_id="rule_99", data_category="HOLOGRAM_ARCHIVE")])


@scenario("unknown_reported_status", "diag")
def _(d):
    req(d, lambda f, rs: [r.update(reported_status=["DONE", "", "complete", "IN_PROGRESS"][i % 4]) for i, r in enumerate(rs[:8])] and None)


@scenario("empty_evidence_files", "diag")
def _(d):
    for ext in (".json", ".sql", ".log", ".txt"):
        evidence_path(d, ext)[0].write_text("", encoding="utf-8")


@scenario("corrupt_evidence_files", "diag")
def _(d):
    rnd = random.Random(7)
    for ext in (".json", ".sql", ".log"):
        p = evidence_path(d, ext)[0]
        b = bytearray(p.read_bytes())
        for _ in range(200):
            b[rnd.randrange(len(b))] = rnd.randrange(256)
        p.write_bytes(bytes(b))


@scenario("truncated_evidence_files", "diag")
def _(d):
    for ext in (".json", ".sql", ".log", ".txt"):
        p = evidence_path(d, ext)[0]
        b = p.read_bytes()
        p.write_bytes(b[: len(b) // 2])


@scenario("missing_evidence_file", "diag")
def _(d):
    evidence_path(d, ".json")[0].unlink()


@scenario("unknown_evidence_extension", "diag")
def _(d):
    f, rows = rd(d, "evidence_files.csv")
    (d / "evidence" / "mystery.xyz").write_text("binary?", encoding="utf-8")
    rows.append(dict(rows[0], file_id="evidence_9999", file_path="evidence/mystery.xyz"))
    wr(d, "evidence_files.csv", f, rows)


@scenario("new_log_dialect_lines", "diag")
def _(d):
    p = evidence_path(d, ".log")[0]
    p.write_text(p.read_text(encoding="utf-8") + "\n<14>1 2026-09-01T00:00:00Z host app - - [weird@1 k=\"v\"] something new\n{\"msg\": \"json line\", \"ts\": 1}\n\x00\x01garbage\n", encoding="utf-8")


@scenario("dates_other_format_and_tz", "same")
def _(d):
    req(d, lambda f, rs: [r.update(audit_at=r["audit_at"].replace("Z", "+00:00"), requested_at=r["requested_at"].replace("T", " ").replace("Z", "")) for r in rs[:10]] and None)


@scenario("unparseable_dates", "review")
def _(d):
    req(d, lambda f, rs: [r.update(audit_at="next tuesday", requested_at="??") for r in rs[:3]] and None)


@scenario("evidence_after_audit_at")
def _(d):
    edit(d, "evidence_files.csv", lambda f, rs: [r.update(captured_at="2030-01-01T00:00:00Z") for r in rs[:5]] and None)


@scenario("audit_at_missing", "review")
def _(d):
    req(d, lambda f, rs: [r.update(audit_at="") for r in rs[:3]] and None)


@scenario("csv_extra_and_reordered_columns", "same")
def _(d):
    for name in ("deletion_requests.csv", "user_identities.csv", "system_activity.csv", "evidence_files.csv"):
        f, rows = rd(d, name)
        f = list(reversed(f)) + ["extra_col"]
        for r in rows:
            r["extra_col"] = "x"
        wr(d, name, f, rows)


@scenario("csv_bom_and_crlf", "same")
def _(d):
    for name in ("deletion_requests.csv", "user_identities.csv", "user_audit_log.csv", "system_activity.csv", "deletion_rules.csv", "evidence_files.csv"):
        f, rows = rd(d, name)
        wr(d, name, f, rows, bom=True, crlf=True)


@scenario("missing_column", "review")
def _(d):
    f, rows = rd(d, "deletion_requests.csv")
    wr(d, "deletion_requests.csv", [c for c in f if c != "request_text"], rows)


@scenario("zero_cases")
def _(d):
    f, _ = rd(d, "deletion_requests.csv")
    wr(d, "deletion_requests.csv", f, [])


@scenario("one_case")
def _(d):
    req(d, lambda f, rs: (f, rs[:1]))


@scenario("ten_x_cases")
def _(d):
    def fn(f, rs):
        out = list(rs)
        for k in range(1, 10):
            out += [dict(r, case_id=f"{r['case_id']}_x{k}") for r in rs]
        return f, out
    req(d, fn)


@scenario("json_extra_and_null_fields")
def _(d):
    p, _ = evidence_path(d, ".json")
    j = json.loads(p.read_text(encoding="utf-8"))

    def walk(x):
        if isinstance(x, dict):
            x["stress_extra"] = None
            for k in list(x):
                if k not in ("resource_id",) and x[k] == "":
                    x[k] = None
                walk(x[k])
        elif isinstance(x, list):
            for i in x:
                walk(i)
    walk(j)
    p.write_text(json.dumps(j), encoding="utf-8")


@scenario("ids_with_spaces", "same")
def _(d):
    req(d, lambda f, rs: [r.update(case_id=" " + r["case_id"] + " ") for r in rs] and None)


@scenario("missing_optional_csv", "diag")
def _(d):
    (d / "user_audit_log.csv").unlink()


# ------------------------------------------------------------------ runner
def run_pipeline(ds: Path, out: Path):
    env = dict(os.environ, GHOST_DATASET=str(ds), PYTHONIOENCODING="utf-8")
    t = time.time()
    p = subprocess.run([sys.executable, str(MAIN), "--out", str(out)], env=env, capture_output=True, text=True)
    dt = time.time() - t
    v = None
    if p.returncode == 0:
        v = subprocess.run([sys.executable, str(VALIDATE), str(out)], env=env, capture_output=True, text=True)
    return p, v, dt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="ghost-stress-"))
    base_out = tmp / "baseline.csv"
    p, v, _ = run_pipeline(SRC, base_out)
    if p.returncode != 0:
        print(p.stderr)
        return 2
    results = []
    for name, (fn, expect) in S.items():
        if a.only and name not in a.only:
            continue
        ds = tmp / name / "dataset"
        shutil.copytree(SRC, ds)
        fn(ds)
        out = tmp / name / "output.csv"
        p, v, dt = run_pipeline(ds, out)
        res = {"scenario": name, "exit": p.returncode, "seconds": round(dt, 2), "expect": expect}
        if p.returncode != 0:
            res["result"] = "CRASH"
            res["detail"] = (p.stderr.strip().splitlines() or ["?"])[-1][:200]
        elif v.returncode != 0:
            res["result"] = "INVALID"
            res["detail"] = " | ".join(v.stdout.strip().splitlines()[:3])[:300]
        else:
            res["result"] = "OK"
            if expect == "same" and out.read_bytes() != base_out.read_bytes():
                res["result"] = "DIFFERS"
                res["detail"] = "output differs from the unperturbed run"
            if expect == "diag" and "[ingest]" not in p.stderr:
                res["result"] = "SILENT"
                res["detail"] = "odd input but no ingest diagnostic"
            if expect == "review" and "needs_manual_review" not in p.stderr:
                res["result"] = "SILENT"
                res["detail"] = "degraded input but no manual-review diagnostic"
        res["diagnostics"] = [l for l in p.stderr.splitlines() if l.startswith("[ingest]")][:3]
        results.append(res)
        if not a.json:
            print(f"{res['result']:8} {name:40} {res['seconds']:6}s {res.get('detail', '')}")
    if a.json:
        print(json.dumps(results, indent=1))
    if not a.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    bad = [r for r in results if r["result"] != "OK"]
    if not a.json:
        print(f"{len(results) - len(bad)}/{len(results)} scenarios OK")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
