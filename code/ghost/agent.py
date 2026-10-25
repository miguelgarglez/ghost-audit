"""Agent-facing read-only service over the audit (shared by ``ghost_cli.py`` and ``ghost_mcp.py``).

Everything returned here is derived from the Auditor / audit trail and the dataset files; nothing is invented and
nothing is written (``output.csv`` is never touched). The World is loaded once, lazily, and cached.
"""
from __future__ import annotations

import contextlib
import hashlib
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from . import audit as A
from . import policy as P
from .dataset import DATASET, read_csv
from .evidence import DATASET
from .confidence import score as confidence_v2

ROOT = Path(__file__).resolve().parents[2]
from .media import CACHE_DIR as OCR_DIR  # noqa: E402
TEXT_EXT = {".log", ".json", ".sql", ".txt", ".csv", ".md", ".jsonl", ".yaml", ".yml", ".xml", ".html"}
VERSION = "1.0.1"


class GhostError(Exception):
    def __init__(self, error: str, hint: str = "", code: int = 2) -> None:
        super().__init__(error)
        self.error, self.hint, self.code = error, hint, code

    def as_dict(self) -> dict:
        return {"error": self.error, "hint": self.hint}


# ---------------------------------------------------------------------------------------------- masking
_EMAIL = re.compile(r"([A-Za-z0-9])[A-Za-z0-9._%+\-]*@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_PHONE = re.compile(r"\+\d[\d ()\-]{6,}(\d{2})")


def mask_text(s: str) -> str:
    return _PHONE.sub(lambda m: "+** *** ***" + m.group(1), _EMAIL.sub(lambda m: m.group(1) + "***@" + m.group(2), s))


def mask(obj):
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, list):
        return [mask(x) for x in obj]
    if isinstance(obj, dict):
        return {k: ("".join(w[0] + "." for w in v.split()) if k == "name" and isinstance(v, str) else
                    ["***" + str(x)[-2:] for x in v] if k == "known_phones" else mask(v)) for k, v in obj.items()}
    return obj


def link_confidence(linked_by: str, notes: List[str], rid: str) -> float:
    """1/k for a pick among k same-day candidates (the same quantity MIN_PICK_CONFIDENCE thresholds), else 1."""
    for n in notes:
        m = re.match(rf"account {re.escape(rid)} chosen among (\d+)", n)
        if m:
            return round(1.0 / int(m.group(1)), 3)
    return 0.5 if "ambiguous" in linked_by else 1.0


class Ghost:
    def __init__(self) -> None:
        self._w = self._a = None
        self._rules = None

    # ------------------------------------------------------------------ lazy load
    def _load(self):
        if self._a is None:
            from .world import World
            from . import diag
            diag.reset()
            with contextlib.redirect_stdout(sys.stderr):
                w = World()
                a = A.Auditor(w)
                a.prepare()
            self._w, self._a = w, a
            self._ingest = diag.snapshot()
            self._rules = {r["rule_id"]: r for r in read_csv("deletion_rules.csv")}
            self._samples = {c.case_id for c in w.sample_cases}
            self._trail = {cid: A.explain(a, cw) for cid, cw in a.work.items()}
            self._act = {r["activity_id"]: r for r in w.activity}
        return self._w, self._a

    def _cw(self, case_id: str):
        _, a = self._load()
        cw = a.work.get(case_id)
        if cw is None:
            raise GhostError(f"unknown case_id {case_id!r}", "list ids with `cases` / ghost_list_cases", 3)
        return cw

    def _signals(self, t: dict) -> dict:
        notes = t.get("notes", [])
        reasons, conf = [], 1.0
        for r in t.get("resources", []):
            c = link_confidence(r["linked_by"], notes, r["resource"])
            conf = min(conf, c)
            if c < 1.0:
                reasons.append(f"ambiguous link: {r['resource']} ({r['linked_by']}, confidence {c})")
            if r["proof"] == "inferred":
                reasons.append(f"no evidence captured before audit_at for {r['resource']} (state inferred)")
        for n in notes:
            if "withheld" in n or "not resolvable" in n:
                reasons.append(n)
                conf = 0.0 if "not resolvable" in n else min(conf, 0.25)
            elif n.startswith(("degraded input", "audit failed")):
                reasons.append(n)
                conf = 0.0
        if t["claimed_status"] == "COMPLETE" and t["verified_status"] == "INCOMPLETE":
            reasons.append("false COMPLETE: workflow claims done but active risk remains: " + ", ".join(t.get("risks", [])))
        ambiguous = conf < 1.0
        manual = any(x.startswith(("ambiguous", "account withheld", "subject not", "degraded input", "audit failed")) for x in reasons)
        v2, v2why = confidence_v2(t)
        return {"confidence": round(conf, 3), "confidence_v2": v2, "confidence_reasons": v2why, "ambiguous": ambiguous, "needs_manual_review": manual, "review_reasons": reasons}

    def _row(self, t: dict) -> dict:
        s = self._signals(t)
        return {"case_id": t["case_id"], "sample": t["case_id"] in self._samples, "request_email": t["request_email"],
                "claimed_status": t["claimed_status"], "verified_status": t["verified_status"],
                "user_id": (t.get("subject") or {}).get("user_id"), "risks": t.get("risks", []), "steps": t.get("steps", 0),
                "confidence": s["confidence"], "confidence_v2": s["confidence_v2"], "needs_manual_review": s["needs_manual_review"]}

    def _scope(self, include_samples: bool):
        self._load()
        return [t for cid, t in sorted(self._trail.items(), key=lambda kv: A._ckey(kv[0]))
                if include_samples or cid not in self._samples]

    # ------------------------------------------------------------------ tools
    def describe(self) -> dict:
        return {
            "name": "ghost-audit", "version": VERSION,
            "purpose": "Read-only, deterministic audit of GDPR-style deletion requests: verifies whether personal data still exists at audit_at and plans FINDING -> REMEDIATION -> VERIFICATION steps.",
            "runtime": {"llm": False, "network": False, "deterministic": True, "writes": "nothing (output.csv is produced only by code/main.py)"},
            "input": {"dataset_dir": "dataset/", "files": sorted(p.name for p in DATASET.glob("*.csv"))},
            "tools": TOOLS_DOC,
            "glossary": {
                "claimed_status": ["COMPLETE", "PARTIALLY_COMPLETE", "NOT_STARTED"],
                "verified_status": {"COMPLETE": "no policy violation remains at audit_at", "INCOMPLETE": "at least one resource still violates its rule",
                                    "NOT_APPLICABLE": "request text does not authorise deletion"},
                "phases": [P.PHASE_FINDING, P.PHASE_REMEDIATION, P.PHASE_VERIFICATION],
                "operations": {"FINDING": [o for o, _ in P.FINDING_OPS], "REMEDIATION": [o for o, _ in P.REMEDIATION_OPS], "VERIFICATION": [o for o, _ in P.VERIFICATION_OPS]},
                "operation_target_state": P.OP_STATE,
                "resource_fields": {"state_at_audit": "reconstructed state at audit_at", "policy": "rule id from deletion_rules.csv", "required": "action the rule demands",
                                    "violation": "true when state_at_audit breaks the rule", "proof": "evidence file id or 'inferred'", "linked_by": "how the resource was tied to the subject"},
                "confidence": "min over linked resources of 1/k for a pick among k equally plausible same-day candidates (1.0 = unambiguous)",
                "confidence_v2": "review-PRIORITY signal in [0,1]: product of hand-set per-resource link reliabilities (tie 1/k, identifier 0.995, catalog/LINK 0.99, indirect 0.97; x0.93 if the state is inferred); unresolvable/degraded -> 0, withheld -> <=0.25. Not a calibrated probability (docs/CONFIDENCE.md)",
                "confidence_reasons": "why confidence_v2 is below 1, one line per discount",
            },
            "limits": [
                "Accuracy ceiling ~98% step-F1 on the public samples; see code/evaluation/limits_and_ceiling.md.",
                "Some retained audit-record ids appear in no file: the verification step is omitted rather than invented.",
                f"Picks among >= {round(1 / A.MIN_PICK_CONFIDENCE)} equally plausible candidates are withheld (MIN_PICK_CONFIDENCE={A.MIN_PICK_CONFIDENCE}).",
                "Ambiguous same-day account picks are flagged with confidence < 1; treat them as needing human review.",
                "confidence_v2 ranks cases for review; with 3 wrong rows in 32 samples it cannot support claims like '99% accurate'.",
                "Screenshots/PDFs are read from a pre-built OCR text cache (code/ghost/ocr_cache).",
            ],
        }

    def summary(self, include_samples: bool = False) -> dict:
        ts = self._scope(include_samples)
        rows = [self._row(t) for t in ts]
        trans = Counter(f"{t['claimed_status']}->{t['verified_status']}" for t in ts)
        risks = Counter(k for t in ts for k in t.get("risks", []))
        systems = Counter(r["system"] for t in ts for r in t.get("resources", []) if r["violation"])
        return {"cases": len(ts), "include_samples": include_samples,
                "verified_status": dict(sorted(Counter(t["verified_status"] for t in ts).items())),
                "claimed_status": dict(sorted(Counter(t["claimed_status"] for t in ts).items())),
                "claimed_to_verified": dict(sorted(trans.items())),
                "false_complete": trans.get("COMPLETE->INCOMPLETE", 0),
                "top_risks": [{"category": k, "cases": v} for k, v in sorted(risks.items(), key=lambda kv: (-kv[1], kv[0]))[:10]],
                "violations_by_system": dict(sorted(systems.items(), key=lambda kv: (-kv[1], kv[0]))),
                "needs_manual_review": sum(r["needs_manual_review"] for r in rows),
                "ambiguous": sum(r["confidence"] < 1.0 for r in rows),
                "total_steps": sum(t.get("steps", 0) for t in ts)}

    def cases(self, status=None, claimed=None, risk=None, needs_review=False, q=None, limit=50, include_samples=False) -> dict:
        out = []
        for t in self._scope(include_samples):
            r = self._row(t)
            if status and r["verified_status"] != status.upper():
                continue
            if claimed and r["claimed_status"] != claimed.upper():
                continue
            if risk and risk.upper() not in r["risks"]:
                continue
            if needs_review and not r["needs_manual_review"]:
                continue
            if q:
                ql = q.lower()
                t_ = self._trail[r["case_id"]]
                hay = [r["case_id"], r["request_email"], r["user_id"] or ""] + (t_.get("subject") or {}).get("known_emails", [])
                if not any(ql in h.lower() for h in hay):
                    continue
            out.append(r)
        total = len(out)
        return {"total": total, "returned": min(total, limit), "cases": out[:limit]}

    def case(self, case_id: str, do_mask: bool = False) -> dict:
        cw = self._cw(case_id)
        w, a = self._load()
        t = self._trail[case_id]
        c = cw.case
        status, steps = a.plan(cw)
        d = {"case_id": c.case_id, "sample": c.case_id in self._samples,
             "request": {"email": c.email, "requested_at": c.requested_at, "reported_status": c.reported_status,
                         "reported_at": c.reported_at, "audit_at": c.audit_at, "text": c.request_text,
                         "authorises_deletion": cw.applicable},
             "claimed_status": c.reported_status, "verified_status": status}
        s = w.dir.subjects.get(cw.user or "")
        if s:
            audit_rows = [r for r in read_csv("user_audit_log.csv") if r["user_id"] == s.user_id]
            d["subject"] = {"user_id": s.user_id, "name": s.name, "current_email": s.current_email,
                            "known_emails": sorted(s.emails), "known_phones": sorted(s.phones), "email_hashes": len(s.hashes),
                            "identifier_history": [{"audit_id": r["audit_id"], "event_time": r["event_time"], "field": r["field_name"],
                                                    "old": r["old_value"], "new": r["new_value"]} for r in audit_rows]}
        else:
            d["subject"] = None
        res = []
        for r in t.get("resources", []):
            rule = self._rules.get(r["policy"] or "", {})
            acts = [x["activity_id"] for x in w.act_by_rid.get(r["resource"], []) if x["event_time"] <= c.audit_at]
            res.append(dict(r, rule_condition=rule.get("condition"), confidence=link_confidence(r["linked_by"], cw.notes, r["resource"]),
                            activity_ids=acts))
        d["resources"] = res
        d["risks"] = t.get("risks", [])
        d["primary_evidence"] = cw.primary
        d["plan"] = [s_.as_json(i + 1) for i, s_ in enumerate(steps)]
        d["notes"] = list(cw.notes)
        if "reason" in t:
            d["notes"].append(t["reason"])
        d["review"] = self._signals(t)
        d["claimed_vs_verified"] = self._why(t)
        return mask(d) if do_mask else d

    @staticmethod
    def _why(t: dict) -> str:
        cl, vs = t["claimed_status"], t["verified_status"]
        viol = [f"{r['resource']} ({r['category']}) is {r['state_at_audit']} but {r['policy']} requires {r['required']}"
                for r in t.get("resources", []) if r["violation"]]
        if vs == "NOT_APPLICABLE":
            return t.get("reason", "request does not authorise deletion")
        if vs == "INCOMPLETE":
            return (f"claimed {cl}, verified INCOMPLETE: " if cl == "COMPLETE" else f"claimed {cl}; still INCOMPLETE: ") + "; ".join(viol)
        return f"claimed {cl}, verified COMPLETE: every linked resource is in its required state at audit_at" + \
            ("" if cl == "COMPLETE" else " (the workflow status lags behind the data)")

    def explain(self, case_id: str, do_mask: bool = False) -> str:
        d = self.case(case_id, do_mask)
        rq, sj = d["request"], d["subject"]
        L = [f"# {d['case_id']}: claimed {d['claimed_status']} -> verified {d['verified_status']}",
             f"- Request from {rq['email']} at {rq['requested_at']}; workflow reported {rq['reported_status']} at {rq['reported_at']}; audit at {rq['audit_at']}."]
        if not rq["authorises_deletion"]:
            L.append("- The request text does not authorise deletion, so no steps are planned.")
        elif not sj:
            L.append("- The subject could not be resolved to exactly one user; nothing can be verified.")
        else:
            hist = [h["audit_id"] for h in sj["identifier_history"]]
            L.append(f"- Subject {sj['user_id']} resolved via {len(sj['known_emails'])} e-mail(s), {len(sj['known_phones'])} phone(s)"
                     + (f"; identifier history [{', '.join(hist)}]." if hist else "."))
            for r in d["resources"][:8]:
                cite = [r["proof"]] + r["activity_ids"][-2:]
                verdict = "VIOLATION" if r["violation"] else "ok"
                L.append(f"- {r['resource']} ({r['category']}, {r['system']}): {r['state_at_audit']} at audit; {r['policy']} requires {r['required']} -> {verdict}. "
                         f"Linked by {r['linked_by']}. [{', '.join(cite)}]")
            if len(d["resources"]) > 8:
                L.append(f"- ... {len(d['resources']) - 8} more resources (see `case {d['case_id']}`).")
            ph = Counter(s["phase"] for s in d["plan"])
            L.append(f"- Plan: {len(d['plan'])} steps ({', '.join(f'{k} {v}' for k, v in ph.items()) or 'none'}).")
        L.append(f"- Why: {d['claimed_vs_verified']}")
        rv = d["review"]
        L.append(f"- Confidence {rv['confidence']} (link tie-break), confidence_v2 {rv['confidence_v2']} (review-priority signal, not a probability); " + ("manual review: " + "; ".join(rv["review_reasons"]) if rv["needs_manual_review"] else "no manual review needed") + ".")
        return "\n".join(L)

    def evidence(self, evidence_id: str, lines: int = 40, grep: Optional[str] = None) -> dict:
        w, _ = self._load()
        m = w.manifest.by_id.get(evidence_id)
        if m is None:
            raise GhostError(f"unknown evidence_id {evidence_id!r}", "evidence ids look like evidence_01; see a case's resources[].proof", 3)
        p = w.manifest.path(evidence_id)
        d = {"evidence_id": evidence_id, "path": m["file_path"], "origin": m["origin"], "captured_at": m["captured_at"],
             "exists": p.exists(), "bytes": p.stat().st_size if p.exists() else None,
             "observations": len(self._a.files_obs.get(evidence_id, [])),
             "resources": sorted({o.rid for o in self._a.files_obs.get(evidence_id, [])})[:50]}
        src = None
        if p.suffix.lower() in TEXT_EXT and p.exists():
            src, d["text_source"] = p, "file"
        elif (OCR_DIR / (p.name + ".txt")).exists():
            src, d["text_source"] = OCR_DIR / (p.name + ".txt"), "ocr_cache"
        else:
            d["text_source"] = None
        if src:
            text = src.read_text(encoding="utf-8", errors="replace").splitlines()
            if grep:
                hits = [(i + 1, x) for i, x in enumerate(text) if grep.lower() in x.lower()]
                d["matches"] = len(hits)
                d["snippet"] = [f"{i}: {x[:400]}" for i, x in hits[:lines]]
            else:
                d["snippet"] = [x[:400] for x in text[:lines]]
            d["total_lines"] = len(text)
        return d

    def queue(self, limit: int = 25, include_samples: bool = False, sort: str = "priority") -> dict:
        if sort not in ("priority", "confidence_v2"):
            raise GhostError(f"unknown sort {sort!r}", "use priority or confidence_v2")
        items = []
        for t in self._scope(include_samples):
            s = self._signals(t)
            if not (s["needs_manual_review"] or t["claimed_status"] == "COMPLETE" and t["verified_status"] == "INCOMPLETE"):
                continue
            score = 0
            for r in s["review_reasons"]:
                score += 50 if r.startswith(("subject not", "account withheld")) else 30 if r.startswith("ambiguous") else \
                    20 if r.startswith("false COMPLETE") else 5
            score += 5 * len(t.get("risks", []))
            items.append({"case_id": t["case_id"], "priority_score": score, "claimed_status": t["claimed_status"],
                          "verified_status": t["verified_status"], "confidence": s["confidence"],
                          "confidence_v2": s["confidence_v2"], "risks": t.get("risks", []),
                          "reasons": s["review_reasons"]})
        if sort == "confidence_v2":   # least certain first; priority score breaks ties
            items.sort(key=lambda x: (x["confidence_v2"], -x["priority_score"], A._ckey(x["case_id"])))
        else:
            items.sort(key=lambda x: (-x["priority_score"], x["confidence_v2"], A._ckey(x["case_id"])))
        return {"total": len(items), "returned": min(limit, len(items)),
                "criteria": "needs_manual_review (ambiguous/withheld/unresolved link) or false COMPLETE; inferred states only add score",
                "scoring": "withheld/unresolved 50, ambiguous link 30, false COMPLETE 20, inferred state 5, +5 per active risk category",
                "sort": sort + ("" if sort == "confidence_v2" else " (confidence_v2 ascending breaks ties)"),
                "queue": items[:limit]}

    def whatif(self, min_pick_confidence: Optional[float] = None, min_confidence_v2: Optional[float] = None) -> dict:
        """Re-run the audit in memory with another MIN_PICK_CONFIDENCE and/or list the cases a confidence_v2 review
        threshold would route to a human. Nothing is written to disk; output.csv never changes."""
        if min_pick_confidence is None and min_confidence_v2 is None:
            raise GhostError("give min_pick_confidence and/or min_confidence_v2", "e.g. whatif --min-confidence-v2 0.9")
        out = {}
        if min_confidence_v2 is not None:
            if not 0.0 <= min_confidence_v2 <= 1.0:
                raise GhostError("min_confidence_v2 must be in [0, 1]", "e.g. 0.9")
            rows = [self._row(t) for t in self._scope(False)]
            below = sorted((r for r in rows if r["confidence_v2"] < min_confidence_v2),
                           key=lambda r: (r["confidence_v2"], A._ckey(r["case_id"])))
            out["confidence_v2_review"] = {
                "threshold": min_confidence_v2, "cases": len(rows), "would_review": len(below),
                "auto_accept_share": round(1 - len(below) / max(1, len(rows)), 3),
                "already_flagged": sum(r["needs_manual_review"] for r in below),
                "lowest": [{"case_id": r["case_id"], "confidence_v2": r["confidence_v2"], "verified_status": r["verified_status"]} for r in below[:100]],
                "note": "routing simulation only: statuses and steps are unchanged; confidence_v2 is a priority signal, not a probability"}
        if min_pick_confidence is None:
            return out
        out.update(self._whatif_pick(min_pick_confidence))
        return out

    def _whatif_pick(self, min_pick_confidence: float) -> dict:
        if not 0.0 <= min_pick_confidence <= 1.0:
            raise GhostError("min_pick_confidence must be in [0, 1]", "e.g. 0.5 withholds picks among 3+ candidates")
        _, base = self._load()
        from .world import World
        old = A.MIN_PICK_CONFIDENCE
        try:
            A.MIN_PICK_CONFIDENCE = min_pick_confidence
            with contextlib.redirect_stdout(sys.stderr):
                alt = A.Auditor(World())
                alt.prepare()
        finally:
            A.MIN_PICK_CONFIDENCE = old
        changed, withheld = [], 0
        for cid in sorted(base.work, key=A._ckey):
            if cid in self._samples:
                continue
            b, n = base.plan(base.work[cid]), alt.plan(alt.work[cid])
            wh = any("withheld" in x for x in alt.work[cid].notes)
            withheld += wh
            if (b[0], [s.as_json(0) for s in b[1]]) != (n[0], [s.as_json(0) for s in n[1]]):
                changed.append({"case_id": cid, "status": f"{b[0]}->{n[0]}", "steps": f"{len(b[1])}->{len(n[1])}", "withheld": wh})
        return {"baseline_min_pick_confidence": old, "simulated_min_pick_confidence": min_pick_confidence,
                "cases_changed": len(changed), "cases_with_withheld_pick": withheld, "changes": changed[:100],
                "note": "in-memory simulation; output.csv is not modified"}

    def ingest_report(self) -> dict:
        """What the loader skipped, repaired or degraded (empty on the shipped dataset); see docs/ROBUSTNESS.md."""
        self._load()
        r = dict(self._ingest)
        r["dataset"] = str(DATASET)
        r["note"] = ("clean: every file, row and case was ingested" if r["clean"] else
                     "the audit did not crash; listed items were skipped or degraded to INCOMPLETE + needs_manual_review")
        return r

    def verify(self, path: Optional[str] = None) -> dict:
        sys.path.insert(0, str(ROOT / "code" / "evaluation"))
        try:
            from validate_output import validate
        except ImportError:
            raise GhostError("verify needs code/evaluation/validate_output.py (source checkout or code.zip only)",
                             "run from the repository, or validate the CSV with the competition tooling", 2)
        p = Path(path) if path else ROOT / "output.csv"
        if not p.exists():
            raise GhostError(f"{p} not found", "run `python3 code/main.py` first", 3)
        errs = validate(p)
        rows = read_csv(p.name, p.parent)
        return {"path": str(p), "ok": not errs, "problems": len(errs), "errors": errs[:50], "rows": len(rows),
                "deletion_status": dict(sorted(Counter(r["deletion_status"] for r in rows).items())),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


TOOLS_DOC = [
    {"cli": "describe", "mcp": "ghost_describe", "does": "capabilities, glossary, limits"},
    {"cli": "summary", "mcp": "ghost_summary", "does": "status distribution, claimed->verified, top risks, review counts"},
    {"cli": "cases", "mcp": "ghost_list_cases", "does": "filter cases by status/claimed/risk/needs-review/text"},
    {"cli": "case CASE_ID", "mcp": "ghost_get_case", "does": "full context of one case (request, subject, resources, plan, review signals)"},
    {"cli": "explain CASE_ID", "mcp": "ghost_explain_case", "does": "deterministic markdown narrative with evidence/audit/activity citations"},
    {"cli": "evidence EVIDENCE_ID", "mcp": "ghost_get_evidence", "does": "evidence file metadata and text snippet (file or OCR cache)"},
    {"cli": "ingest-report", "mcp": "ghost_ingest_report", "does": "ingestion diagnostics: unparsed files, ignored rows, degraded cases"},
    {"cli": "queue [--sort priority|confidence_v2]", "mcp": "ghost_review_queue", "does": "prioritised human review queue with reasons and confidence_v2"},
    {"cli": "whatif [--min-pick-confidence X] [--min-confidence-v2 Y]", "mcp": "ghost_whatif", "does": "in-memory re-run with another tie-break threshold and/or cases a confidence_v2 review threshold would route to a human"},
    {"cli": "verify", "mcp": "ghost_verify_output", "does": "structural validation of output.csv"},
]
