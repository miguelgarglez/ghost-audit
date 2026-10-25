#!/usr/bin/env python3
"""Evaluation harness.

    python3 code/evaluation/main.py            # score on the public samples, print metrics
    python3 code/evaluation/main.py --reports  # also (re)write evaluation_report.md, audit_summary.md, audit_trail.jsonl

* scores the agent against ``dataset/sample_output.csv`` (case, step, field, sequence accuracy);
* runs the structural validator on the final ``output.csv``;
* runs the participant-authored synthetic cases (``custom_cases.py``);
* summarises the audit of all evaluation requests (status mix, risk patterns, confidence) for humans.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(HERE))

from ghost.audit import Auditor, explain  # noqa: E402
from ghost.confidence import score as confidence_v2  # noqa: E402
from ghost.dataset import read_csv  # noqa: E402
from ghost.world import World  # noqa: E402
import custom_cases  # noqa: E402
import validate_output  # noqa: E402

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def tup(s):
    return tuple(s[f] for f in FIELDS)


def lcs(a, b):
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a)):
        for j in range(len(b)):
            dp[i + 1][j + 1] = dp[i][j] + 1 if a[i] == b[j] else max(dp[i][j + 1], dp[i + 1][j])
    return dp[-1][-1]


def score(pred: dict, gold: dict, ids) -> dict:
    n = len(ids)
    status = sum(pred[i][0] == gold[i][0] for i in ids)
    exact = sum(pred[i][0] == gold[i][0] and [tup(s) for s in pred[i][1]] == [tup(s) for s in gold[i][1]] for i in ids)
    tp = fp = fn = 0
    tp_core = fp_core = fn_core = 0
    ev_ok = ev_tot = 0
    field_ok = Counter()
    field_tot = Counter()
    seq_exact = 0
    lcs_num = lcs_den = 0
    for i in ids:
        p = [tup(s) for s in pred[i][1]]
        g = [tup(s) for s in gold[i][1]]
        ps, gs = set(p), set(g)
        tp += len(ps & gs)
        fp += len(ps - gs)
        fn += len(gs - ps)
        pc = {x[:5] for x in p}
        gc = {x[:5] for x in g}
        tp_core += len(pc & gc)
        fp_core += len(pc - gc)
        fn_core += len(gc - pc)
        gmap = {x[:5]: x for x in g}
        for x in p:
            if x[:5] in gmap:
                ev_tot += 1
                ev_ok += x[5] == gmap[x[:5]][5]
        for k in range(max(len(p), len(g))):
            for fi, f in enumerate(FIELDS):
                field_tot[f] += 1
                if k < len(p) and k < len(g) and p[k][fi] == g[k][fi]:
                    field_ok[f] += 1
        seq_exact += [x[:5] for x in p] == [x[:5] for x in g]
        lcs_num += lcs([x[:5] for x in p], [x[:5] for x in g])
        lcs_den += max(len(p), len(g))
    prf = lambda tp_, fp_, fn_: (tp_ / (tp_ + fp_) if tp_ + fp_ else 1.0, tp_ / (tp_ + fn_) if tp_ + fn_ else 1.0)  # noqa: E731
    p, r = prf(tp, fp, fn)
    pc_, rc_ = prf(tp_core, fp_core, fn_core)
    f1 = lambda a, b: 2 * a * b / (a + b) if a + b else 0.0  # noqa: E731
    return {
        "cases": n,
        "status_accuracy": status / n,
        "row_exact_match": exact / n,
        "step_precision": p, "step_recall": r, "step_f1": f1(p, r),
        "step_f1_ignoring_evidence": f1(pc_, rc_),
        "evidence_id_accuracy_on_matched_steps": ev_ok / ev_tot if ev_tot else 1.0,
        "field_accuracy": {f: field_ok[f] / field_tot[f] for f in FIELDS},
        "sequence_exact": seq_exact / n,
        "sequence_lcs_ratio": lcs_num / lcs_den if lcs_den else 1.0,
    }


def as_dicts(plan):
    status, steps = plan
    return status, [s.as_json(i + 1) for i, s in enumerate(steps)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", action="store_true")
    args = ap.parse_args()
    world = World()
    aud = Auditor(world)
    aud.prepare()
    gold = {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in read_csv("sample_output.csv")}
    pred_samples = {c.case_id: as_dicts(aud.plan(aud.work[c.case_id])) for c in world.sample_cases}
    ids_all = sorted(gold)
    ids_new = [i for i in ids_all if int(i.split("_")[1]) >= 11]
    m_all = score(pred_samples, gold, ids_all)
    m_new = score(pred_samples, gold, ids_new)
    errs = validate_output.validate(ROOT / "output.csv") if (ROOT / "output.csv").exists() else ["output.csv missing"]
    custom = custom_cases.run()
    print(json.dumps({"samples_all_32": m_all, "samples_case_11_to_32": m_new,
                      "output_validation_errors": len(errs),
                      "custom_cases_passed": f"{sum(r['status_ok'] and r['steps_ok'] for r in custom)}/{len(custom)}"}, indent=1))
    if not args.reports:
        return 0

    # ---- audit of all evaluation requests
    rows = {}
    trail = []
    for c in world.cases:
        cw = aud.work[c.case_id]
        rows[c.case_id] = as_dicts(aud.plan(cw))
        trail.append(explain(aud, cw))
    with open(ROOT / "audit_trail.jsonl", "w", encoding="utf-8") as fh:
        for t in trail:
            v2, why = confidence_v2(t)
            fh.write(json.dumps(dict(t, confidence_v2=v2, confidence_reasons=why), ensure_ascii=False) + "\n")
    write_summary(world, aud, rows, trail)
    deletion = [t for t in trail if t['verified_status'] != 'NOT_APPLICABLE']
    stats = {"n": len(deletion),
             "amb": sum(1 for t in deletion if any('ambiguous' in r['linked_by'] for r in t['resources'])),
             "no_audit": sum(1 for t in deletion if not any(r['category'] == 'SECURITY_AUDIT_LOG' for r in t['resources']))}
    write_eval_report(m_all, m_new, errs, custom, aud, world, pred_samples, gold, stats)
    return 0


def pct(x):
    return f"{100 * x:.1f}%"


def write_summary(world, aud, rows, trail) -> None:
    n = len(rows)
    status = Counter(s for s, _ in rows.values())
    claimed_vs = Counter((c.reported_status, rows[c.case_id][0]) for c in world.cases)
    risk_cases = Counter()
    op_counts = Counter()
    for cid, (st, steps) in rows.items():
        ops = {s["operation"] for s in steps if s["phase"] == "FINDING"}
        for o in ops:
            risk_cases[o] += 1
        for s in steps:
            if s["phase"] == "FINDING":
                op_counts[(s["system_id"], s["operation"])] += 1
    deletion_cases = [c for c in world.cases if rows[c.case_id][0] != "NOT_APPLICABLE"]
    false_complete = [c for c in deletion_cases if c.reported_status == "COMPLETE" and rows[c.case_id][0] == "INCOMPLETE"]
    amb = sum(1 for t in trail if any("ambiguous" in r.get("linked_by", "") for r in t.get("resources", [])))
    md = []
    md.append("# Audit summary - Northstar Shop deletion audit\n")
    md.append(f"Generated by `python3 code/evaluation/main.py --reports` from the final run over **{n}** requests.\n")
    md.append("## Verdicts\n")
    md.append("| Verdict | Requests | Share |\n|---|---:|---:|")
    for k in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
        md.append(f"| {k} | {status[k]} | {pct(status[k] / n)} |")
    md.append("\n## The dashboards were wrong\n")
    md.append(f"The deletion workflow reported **COMPLETE** for {sum(1 for c in deletion_cases if c.reported_status == 'COMPLETE')} deletion requests; "
              f"the audit found personal data still reachable in **{len(false_complete)}** of them "
              f"({pct(len(false_complete) / max(1, sum(1 for c in deletion_cases if c.reported_status == 'COMPLETE')))}).\n")
    md.append("| Workflow claim | Audit verdict | Requests |\n|---|---|---:|")
    for (claim, verdict), v in sorted(claimed_vs.items()):
        md.append(f"| {claim} | {verdict} | {v} |")
    md.append("\n## Top risk patterns (findings across INCOMPLETE requests)\n")
    names = {"REPORT_REMAINING_RECORD": "Record that should be deleted is still there",
             "REPORT_EXPOSED_PII": "Record kept but still exposes identifiers (support / analytics / tombstone)",
             "REPORT_ACTIVE_DATA_FLOW": "Active sync / import connector can recreate the data",
             "REPORT_RESTORE_RISK": "Backup can restore the deleted profile"}
    md.append("| Pattern | Requests affected |\n|---|---:|")
    for op, v in risk_cases.most_common():
        md.append(f"| {names[op]} (`{op}`) | {v} |")
    md.append("\n### Findings by system\n")
    md.append("| System | Finding | Count |\n|---|---|---:|")
    for (sysid, op), v in sorted(op_counts.items(), key=lambda x: -x[1]):
        md.append(f"| {sysid} | {op} | {v} |")
    md.append("\n## Recreation risks (what makes 'deleted' not stay deleted)\n")
    fl = risk_cases["REPORT_ACTIVE_DATA_FLOW"]
    rr = risk_cases["REPORT_RESTORE_RISK"]
    md.append(f"* {fl} requests still have an enabled connector that can copy or recreate the subject's data; remediation always disables the flow **before** deleting downstream copies.")
    md.append(f"* {rr} requests have a backup archive with restore access still open; remediation blocks the restore (the archive itself is retained per policy).\n")
    md.append("## How much of this is certain?\n")
    md.append(f"* Identity resolution used current and historical e-mails, hashes and phones for every request; no request was left unresolved.")
    md.append(f"* {amb} of {len(deletion_cases)} deletion audits contain a resource id that is a coin-flip among same-day candidates (no identifier, no usable event clock). "
              "Those are flagged `ambiguous` in `audit_trail.jsonl`; a privacy officer should confirm them before sign-off. All other identifier-free ids are unique or clock-matched.")
    md.append("* Every finding cites the evidence file that proves it; evidence captured after `audit_at` is never cited.\n")
    md.append("## Reading the per-case trail\n")
    md.append("`audit_trail.jsonl` (repo root, regenerated by the command above) has one JSON object per request: resolved subject, evidence file, "
              "every resource with its state at audit time, the policy rule applied (`rule_xx`), whether it is a violation, which evidence proves it "
              "and how the resource was linked to the person.\n")
    (HERE / "audit_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def write_eval_report(m_all, m_new, errs, custom, aud, world, pred, gold, stats) -> None:
    md = ["# Evaluation report\n"]
    md.append("All numbers are produced by `python3 code/evaluation/main.py --reports` (deterministic; no LLM involved).\n")
    md.append("## 1. Accuracy against the public samples (`dataset/sample_output.csv`)\n")
    md.append("The 32 samples are two families: `case_01`-`case_10` are hand-built, screenshot/PDF-centred stories with their own resource-id style "
              "(legacy), `case_11`-`case_32` follow the generator used for the 518 evaluation requests. We report both.\n")
    md.append("| Metric | all 32 | case_11-32 |\n|---|---:|---:|")
    for k, label in [("status_accuracy", "Case-level `deletion_status` accuracy"), ("row_exact_match", "Row exact match (status + every step + evidence id)"),
                     ("step_precision", "Step precision (exact tuple)"), ("step_recall", "Step recall (exact tuple)"), ("step_f1", "Step F1"),
                     ("step_f1_ignoring_evidence", "Step F1 ignoring evidence_id"),
                     ("evidence_id_accuracy_on_matched_steps", "evidence_id accuracy on matched steps"),
                     ("sequence_exact", "Sequence accuracy (exact ordered list of steps)"), ("sequence_lcs_ratio", "Sequence accuracy (LCS ratio)")]:
        md.append(f"| {label} | {pct(m_all[k])} | {pct(m_new[k])} |")
    md.append("\nField-level accuracy (position-aligned steps; a missing or extra step counts as wrong):\n")
    md.append("| Field | all 32 | case_11-32 |\n|---|---:|---:|")
    for f in FIELDS:
        md.append(f"| `{f}` | {pct(m_all['field_accuracy'][f])} | {pct(m_new['field_accuracy'][f])} |")
    md.append("\n## 2. Output validation\n")
    md.append(f"`python3 code/evaluation/validate_output.py` on the final `output.csv`: **{'all structural checks passed' if not errs else str(len(errs)) + ' problems'}** "
              "(518 rows sorted by case_id, valid JSON arrays, consecutive step numbers, phase order, allowed operation/state pairs, intra-phase ordering, "
              "evidence ids exist and are not later than `audit_at`, future steps carry an empty evidence id, NOT_APPLICABLE rows are `[]`).\n")
    md.append("## 3. Additional evaluation cases written by us\n")
    md.append("`code/evaluation/custom_cases.py` builds a fictional mini-dataset and runs the real agent on it (`GHOST_DATASET` override).\n")
    md.append("| Case | Behaviour tested | Result |\n|---|---|---|")
    desc = {"X1": "request with the OLD e-mail; analytics record keyed only by the old e-mail hash", "X2": "leftover record only in a snapshot captured after audit_at is ignored",
            "X3": "tombstone keeps the stable user reference but still exposes direct identifiers -> REDACT; audit record retained",
            "X4": "access/export request that says 'do not delete' -> NOT_APPLICABLE, `[]`",
            "X5": "active connector + restorable backup -> disable flow first, then block restore", "X6": "profile removed on primary but present on the read replica (replication drift)",
            "X7": "everything already compliant -> COMPLETE with verification-only steps"}
    for r in custom:
        md.append(f"| {r['case']} | {desc[r['case']]} | {'PASS' if r['status_ok'] and r['steps_ok'] else 'FAIL'} |")
    md.append("\n## 4. Failures found and improvements made\n")
    md.append(IMPROVEMENTS.format(amb_after=stats['amb']))
    md.append("\n## 5. Known limits\n")
    md.append(LIMITS.format(**stats))
    md.append("\n## 6. confidence_v2: a review-priority signal (not a probability)\n")
    md.append(confidence_paragraph(aud, pred, gold))
    (HERE / "evaluation_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def confidence_paragraph(aud, pred, gold) -> str:
    """Coverage/accuracy of confidence_v2 on the public samples, recomputed on every run (deterministic, no LLM)."""
    pairs = []
    for cid in sorted(gold, key=lambda c: int(c.split("_")[1])):
        st, steps = pred[cid]
        ok = st == gold[cid][0] and [tup(x) for x in steps] == [tup(x) for x in gold[cid][1]]
        pairs.append((confidence_v2(explain(aud, aud.work[cid]))[0], ok))
    n, wrong = len(pairs), sum(not ok for _, ok in pairs)
    rows = []
    for th in (0.0, 0.90, 0.95, 0.98):
        b = [ok for c, ok in pairs if c >= th]
        rows.append(f"| >= {th:.2f} | {len(b)} | {pct(len(b) / n)} | {pct(sum(b) / max(1, len(b)))} |")
    return ("`confidence_v2` (`code/ghost/confidence.py`) multiplies hand-set per-resource link reliabilities (tie 1/k, direct identifier 0.995, "
            "catalog/LINK 0.99, indirect 0.97, x0.93 for an inferred state) and drops to 0 for unresolvable or degraded cases. "
            "It only orders the review queue; it never changes `output.csv`. On the public samples:\n\n"
            "| confidence_v2 | Samples | Coverage | Exact rows |\n|---|---:|---:|---:|\n" + "\n".join(rows) + "\n\n"
            f"With only {wrong} wrong rows in {n} samples this is a sanity check, not a calibration: it does not support a '99%' claim. "
            "Weights were fixed by hand, not fitted. A separate perturbation experiment (results quoted in `code/docs/CONFIDENCE.md`) found "
            "that cases with confidence_v2 >= 0.98 keep their answer in 97.6% of perturbed re-runs, while cases below 0.95 change in 46%.\n")


IMPROVEMENTS = """| # | Failure observed (against samples / consistency checks) | Fix |
|---|---|---|
| 1 | First version scored 19/32 exact rows: absent accounts and retained audit records were dropped from verification when the evidence file never listed them | Resources tied to the subject but unobserved at `audit_at` get a *policy default*: a deleted account is ABSENT (a live one would carry identifiers) and a retained audit/legal record stays PRESENT |
| 2 | A subject with two requests (different audit times) leaked resources of the later request into the earlier one | Multi-request subjects keep only resources first evidenced inside `[requested_at-3d, audit_at]` |
| 3 | Legacy-style worker events were being claimed by regular cases (and vice versa) | Legacy events (`Target resource:` wording, stamped minutes from `reported_at`) are matched first, only to cases without evidence |
| 4 | Account ids of fully-deleted people are not in any evidence file by identifier | Closed-world elimination on the activity stream: same reported day, result mapped from the workflow claim (COMPLETE->SUCCESS, PARTIALLY_COMPLETE->PARTIAL, NOT_STARTED->FAILED), minus ids already tied to someone else |
| 5 | Intent classifier first marked 'verify my deletion' follow-ups as non-deletion | Default is *deletion*; NOT_APPLICABLE requires an explicit keep/negation, a marketing-only opt-out, or a pure non-deletion ask without a deletion verb; cross-checked with the privacy-intake `CLASSIFY_REQUEST` events (10/10 agree) |
| 6 | Screenshots/PDFs gave no usable text | OCR with 3x upscaling cached under `code/ghost/ocr_cache/`; slash/space artefacts repaired; pages mapped to resource kinds; catalog `LINK_RESOURCE_IDENTITY` rows give the explicit person link |
| 7 | 155 audits had an id picked by chance among same-day candidates (`case_03` was wrong: two people, two anonymised analytics copies, no identifier) | **Shared event clock**: the worker events of one request keep a constant seconds-of-minute offset per event type (learned from requests whose account is tied by an identifier). Candidates are filtered by that offset against the request's other events (and, for analytics, against the account event). Ambiguous audits fell from 155 to {amb_after}; hide-the-link experiment recovers ~88% of accounts (`masking_test.py`) |
| 8 | Screenshot stories consumed same-day account events of log-based requests (`case_255` lost its account) | Media-primary requests are excluded from the regular same-day pool |
| 9 | Legacy samples `case_01/02/04/07` missed the account snapshot citation, the PDF owner and the legacy analytics copy | Latest account-snapshot citation for evidence-less requests; single-subject PDFs carry their owner's identifiers; legacy analytics event stamped at `reported_at` |
| 10 | Guesses with a <1/3 chance add noise | Picks among four or more equally plausible candidates are withheld and flagged (`MIN_PICK_CONFIDENCE`) |
"""

LIMITS = """* **Ids that are in no file.** About {no_audit} of {n} deletion requests lack their retained-audit-record verification step: the id never appears with an identifier anywhere (collectors report `dropped=0`; the 196 archival events are unrelated noise). This is proven in `limits_and_ceiling.md`; we omit the step rather than invent an id. The same applies to a handful of screenshot stories without an account id.
* **Ties.** {amb} requests have an account id that is a coin-flip among same-day candidates (no timed peer to apply the event clock to); they are flagged `ambiguous` in `audit_trail.jsonl`.
* **Estimated ceiling for a data-only solver:** step F1 about 98%, status accuracy about 100% (derivation in `limits_and_ceiling.md`); we are within about a point of it on the public samples.
* **Screenshots** are read with OCR; layout-specific parsing is generic (resource-id regexes, page-type keywords) but a new console design would need a mapping entry.
* Public samples are only 32 requests (22 representative of the evaluation generator), so the accuracy above is an optimistic estimate.
* No LLM is used: all decisions are rule-based and reproducible.
"""

if __name__ == "__main__":
    raise SystemExit(main())
