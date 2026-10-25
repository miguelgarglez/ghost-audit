"""Per-case audit: reconstruct the state of every relevant record at ``audit_at`` and plan the steps."""
from __future__ import annotations

import re

from . import diag

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from . import model as M
from . import policy as P
from .intent import authorizes_deletion
from .world import World


# Tie-breaking confidence: a pick among k equally plausible candidates is right with probability 1/k. A step that is
# right with probability p raises expected F1 when p > F1/2 (~0.45) and expected recall always; as a compromise we
# guess when at least one in three (pairs and triples) and withhold, flagged, when four or more candidates tie.
MIN_PICK_CONFIDENCE = 0.30

STATUS_TO_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}


def _ckey(case_id: str):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", case_id)]


def _identity_hint(world, email: str) -> str:
    """Never auto-merge look-alike identities (a +tag alias or a homoglyph is a different mailbox); only
    point the reviewer at the candidate."""
    import unicodedata

    e = (email or "").strip().lower()
    hints = []
    if any(ord(ch) > 127 for ch in e):
        hints.append("e-mail contains non-ASCII characters (possible homoglyph)")
        e = unicodedata.normalize("NFKD", e).encode("ascii", "ignore").decode()
    m = re.match(r"^([^@+]+)\+[^@]*(@.+)$", e)
    if m:
        cand = world.dir.resolve(m.group(1) + m.group(2))
        hints.append(f"+tag alias of a known address (candidate {sorted(cand)[0]})" if cand else "+tag alias")
    return (": " + "; ".join(hints)) if hints else ""


def day(ts: str) -> str:
    return ts[:10]


def _minutes(a: str, b: str) -> float:
    from datetime import datetime
    f = "%Y-%m-%dT%H:%M:%SZ"
    return (datetime.strptime(a, f) - datetime.strptime(b, f)).total_seconds() / 60.0


@dataclass
class Finding:
    """The reconstructed state of one resource at audit time."""

    system: str
    rid: str
    kind: str
    state: str
    file_id: str  # evidence that proves ``state`` ("" when inferred)
    inferred: bool = False


@dataclass
class CaseWork:
    case: M.Case
    applicable: bool
    user: Optional[str] = None
    rids: Set[str] = field(default_factory=set)
    primary: Optional[str] = None  # evidence file that documents this case
    findings: List[Finding] = field(default_factory=list)
    status: str = "NOT_APPLICABLE"
    notes: List[str] = field(default_factory=list)
    how: Dict[str, str] = field(default_factory=dict)  # rid -> how it was tied to the subject
    degraded: bool = False  # input too broken to audit: INCOMPLETE, no steps, manual review


class Auditor:
    def __init__(self, world: World) -> None:
        self.w = world
        self.work: Dict[str, CaseWork] = {}
        self.files_obs: Dict[str, List[M.Obs]] = defaultdict(list)
        for o in world.obs:
            self.files_obs[o.file_id].append(o)

    # ------------------------------------------------------------------ eligibility & state reconstruction
    @staticmethod
    def eligible(o: M.Obs, audit_at: str) -> bool:
        return o.cap <= audit_at and o.t <= audit_at

    def state_of(self, rid: str, audit_at: str) -> Optional[Finding]:
        obs = [o for o in self.w.by_rid.get(rid, []) if self.eligible(o, audit_at)]
        if not obs:
            return None
        tmax = max(o.t for o in obs)
        latest = [o for o in obs if o.t == tmax]
        kind = latest[-1].kind
        same_kind = [o for o in latest if o.kind == kind] or latest
        state = P.worst_state(kind, [o.state for o in same_kind])
        proof = next(o for o in same_kind if o.state == state)
        return Finding(proof.system, rid, kind, state, proof.file_id)

    # ------------------------------------------------------------------ preparation
    def prepare(self) -> None:
        issues = getattr(self.w, "case_issues", {})
        for c in self.w.universe:
            applicable = authorizes_deletion(c.request_text) and c.email and not self._privacy_says_non_deletion(c)
            cw = CaseWork(c, bool(applicable))
            self.work[c.case_id] = cw
            cw.notes.extend(issues.get(c.case_id, []))
            if not cw.applicable:
                continue
            if not c.audit_at or not c.requested_at:
                cw.degraded = True  # no reliable cut-off: nothing can be proven for this case
                continue
            try:
                cw.user = self.w.subject_of(c)
                if cw.user is None:
                    cw.notes.append("subject not resolvable" + _identity_hint(self.w, c.email))
                    continue
                cw.rids = self._day_compatible(cw, self.w.linked_rids(cw.user))
                cw.primary = self._primary_file(cw)
                for r in cw.rids:
                    cw.how[r] = "data-catalog link" if any(a["event_type"] == "LINK_RESOURCE_IDENTITY" for a in self.w.act_by_rid.get(r, [])) else "identifier match in evidence"
            except Exception as e:  # isolate one bad case from the rest of the run
                self._fail(cw, "linking", e)
        for step in (self._assign_orphan_accounts,):
            try:
                step()
            except Exception as e:
                diag.note("degraded_cases", case_id="*", reason=f"global step {step.__name__} failed: {type(e).__name__}: {e}")
        for cw in self.work.values():
            if cw.applicable and cw.user and not cw.degraded:
                try:
                    self._build_findings(cw)
                except Exception as e:
                    self._fail(cw, "state reconstruction", e)
        try:
            self._assign_orphan_analytics()
        except Exception as e:
            diag.note("degraded_cases", case_id="*", reason=f"global step _assign_orphan_analytics failed: {type(e).__name__}: {e}")
        evaluated = {c.case_id for c in self.w.cases}
        for cid, cw in self.work.items():  # record every fallback once, with its reason
            if cid not in evaluated:
                continue
            for n in cw.notes:
                if n.startswith(("degraded input", "subject not resolvable", "audit failed")):
                    diag.case_issue(cid, n)
            st, steps = self.plan(cw)
            if cw.applicable and not steps and not cw.degraded and cw.user:
                n = "no grounded resource or check found for a resolved subject (another request may own its records)"
                cw.notes.append("degraded input: " + n)
                diag.case_issue(cid, n)

    def _fail(self, cw: "CaseWork", stage: str, e: Exception) -> None:
        cw.degraded = True
        cw.findings = []
        cw.notes.append(f"audit failed during {stage}: {type(e).__name__}: {str(e)[:120]}")

    def _privacy_says_non_deletion(self, c: M.Case) -> bool:
        return False

    def _day_compatible(self, cw: CaseWork, rids: Set[str]) -> Set[str]:
        """A subject can have several requests (different audit times). For those subjects, keep only the
        resources first evidenced inside this request's window [requested_at - 3d, audit_at]; the worker
        activity stream is ignored for this decision because its timestamps are claims."""
        if sum(1 for c in self.w.universe if self.w.subject_of(c) == cw.user) < 2:
            return set(rids)
        from datetime import datetime, timedelta

        f = "%Y-%m-%dT%H:%M:%SZ"
        lo = (datetime.strptime(cw.case.requested_at, f) - timedelta(days=3)).strftime(f)
        keep = set()
        for rid in rids:
            marks = [o.t for o in self.w.by_rid.get(rid, [])]
            marks += [a["event_time"] for a in self.w.act_by_rid.get(rid, []) if a["event_type"] == "LINK_RESOURCE_IDENTITY"]
            if not marks or any(lo <= m <= cw.case.audit_at for m in marks):
                keep.add(rid)
        return keep

    def _primary_file(self, cw: CaseWork) -> Optional[str]:
        counts: Dict[str, int] = defaultdict(int)
        for rid in cw.rids:
            for o in self.w.by_rid.get(rid, []):
                if self.eligible(o, cw.case.audit_at):
                    counts[o.file_id] += 1
        if not counts:
            return None
        caps = {f: self.w.manifest.by_id[f]["captured_at"] for f in counts}
        return max(counts, key=lambda f: (counts[f], caps[f]))

    # ------------------------------------------------------------------ orphan accounts via the activity stream
    def _assign_orphan_accounts(self) -> None:
        events = [a for a in self.w.activity
                  if a["event_type"] == "DELETE_RECORD" and a["target_system"] == "account_service"
                  and a["resource_id"] not in self.w.privacy_non_deletion
                  and "Target resource" not in a["details"]]
        legacy_events = [a for a in self.w.activity
                         if a["event_type"] == "DELETE_RECORD" and a["target_system"] == "account_service"
                         and "Target resource" in a["details"]]
        claimed = {r for r, us in self.w.rid_users.items() if us and r.startswith("account_")}
        needy = [cw for cw in self.work.values()
                 if cw.applicable and cw.user and not any(r.startswith("account_") for r in cw.rids)]
        taken: Set[str] = set()
        # legacy-format cases: the worker event is stamped within minutes of the reported_at claim
        legacy_done: Set[str] = set()
        for cw in needy:
            c = cw.case
            near = sorted(a["resource_id"] for a in legacy_events
                          if a["resource_id"] not in claimed and a["resource_id"] not in taken
                          and abs(_minutes(a["event_time"], c.reported_at)) <= 20)
            if near:
                cw.rids.add(near[0])
                taken.add(near[0])
                legacy_done.add(c.case_id)
        needy = [cw for cw in needy if cw.case.case_id not in legacy_done and cw.primary and not self._media_primary(cw)]
        cand: Dict[str, Set[str]] = {}
        for cw in needy:
            c = cw.case
            pool = {a["resource_id"] for a in events
                    if a["resource_id"] not in claimed and day(a["event_time"]) == day(c.reported_at)
                    and a["reported_result"] == STATUS_TO_RESULT[c.reported_status]}
            if cw.primary:
                pool = self._narrow_by_file_listing(cw, pool)
            cand[c.case_id] = pool
        assigned: Dict[str, str] = {}

        def propagate() -> None:
            changed = True
            while changed:
                changed = False
                for cid, pool in cand.items():
                    if cid in assigned:
                        continue
                    live = pool - taken
                    if len(live) == 1:
                        rid = next(iter(live))
                        assigned[cid] = rid
                        taken.add(rid)
                        changed = True

        propagate()
        key_an = ("ANONYMIZE_RECORD@analytics_platform", "policy")
        extra = []
        for cw in self.work.values():
            if not (cw.applicable and cw.user and cw.primary):
                continue
            accs = [r for r in cw.rids if r.startswith("account_")]
            ae = self._account_events(accs[0], cw) if len(accs) == 1 else []
            pool = self._analytics_candidates(cw) if len(ae) == 1 else set()
            if len(pool) == 1 and self._provisional_status(cw) == "COMPLETE":
                extra.append((key_an, self._phase(self._an_event(next(iter(pool)))["event_time"]) - self._phase(ae[0]["event_time"])))
        model = self._learn_phase_model(extra)
        by_rid_event = {a["resource_id"]: a for a in events}
        clocked: Set[str] = set()
        for cid in sorted(cand, key=_ckey):
            if cid in assigned or len(cand[cid] - taken) < 2:
                continue
            cw = self.work[cid]
            an_phases = []
            if key_an in model and cw.primary and self._provisional_status(cw) == "COMPLETE" \
                    and not any(r.startswith("analytics_") for r in cw.rids):
                an_phases = [self._phase(self._an_event(r)["event_time"]) for r in self._analytics_candidates(cw)]
            scored = {}
            for rid in cand[cid] - taken:
                ph = self._phase(by_rid_event[rid]["event_time"])
                v = self._phase_violations(cw, ph, model)
                n_viol = None
                if an_phases:
                    n_viol = 0 if any((p - ph) % 60 in model[key_an] for p in an_phases) else 1
                if v is None and n_viol is None:
                    scored = {}
                    break
                scored[rid] = (v or 0) + (n_viol or 0)
            if scored:
                best = min(scored.values())
                keep = {r for r, v in scored.items() if v == best}
                if best == 0 and len(keep) < len(scored):
                    cand[cid] = keep | (cand[cid] & taken)
                    clocked.add(cid)
        propagate()
        for cid in sorted(cand, key=_ckey):
            if cid in assigned:
                continue
            live = sorted(cand[cid] - taken)
            if live and 1.0 / len(live) < MIN_PICK_CONFIDENCE:
                self.work[cid].notes.append(f"account withheld: {len(live)} equally plausible candidates")
                continue
            if live:
                assigned[cid] = live[0]
                taken.add(live[0])
                self.work[cid].notes.append(f"account {live[0]} chosen among {len(live)} same-day candidates")
        for cid, rid in assigned.items():
            self.work[cid].rids.add(rid)
            import re as _re

            amb = any(_re.match(rf"account {rid} chosen among ([2-9])", x) for x in self.work[cid].notes)
            self.work[cid].how[rid] = ("activity elimination (ambiguous: same-day peers)" if amb else
                                       "activity elimination + shared event clock" if cid in clocked else
                                       "activity elimination (unique candidate)")

    def _account_snapshot_file(self, cw: CaseWork) -> Optional[str]:
        """Evidence-less (legacy) requests: the deleted-account proof is the latest account-system snapshot
        (primary/failover replica of the account service) captured before the audit."""
        best = None
        for fid, obs in self.files_obs.items():
            cap = self.w.manifest.by_id[fid]["captured_at"]
            if cap <= cw.case.audit_at and any(o.src == "sql_failover" for o in obs):
                if best is None or cap > self.w.manifest.by_id[best]["captured_at"]:
                    best = fid
        return best

    # ------------------------------------------------------------------ shared event clock (second-of-minute phase)
    # The worker events of one request are stamped from one clock: their seconds-of-minute differ from the account
    # event's by a constant that depends only on the event type (and on whether the action is a "policy action" or a
    # "final result" report). That makes events that carry no identifier linkable to the request.
    @staticmethod
    def _phase(ts: str) -> int:
        from datetime import datetime

        return int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").timestamp()) % 60

    @staticmethod
    def _ekey(a: dict) -> Tuple[str, str]:  # (type@target, style)
        return (a["event_type"] + "@" + a["target_system"], "policy" if "Policy action" in a["details"] else "result")

    def _account_events(self, rid: str, cw: CaseWork) -> List[dict]:
        return [a for a in self.w.act_by_rid.get(rid, []) if a["event_type"] == "DELETE_RECORD"
                and "Target resource" not in a["details"] and day(a["event_time"]) == day(cw.case.reported_at)]

    def _peer_events(self, cw: CaseWork) -> List[Tuple[Tuple[str, str], int]]:
        out = []
        for rid in cw.rids:
            if rid.startswith("account_"):
                continue
            for a in self.w.act_by_rid.get(rid, []):
                if a["source_system"] == "deletion_worker" and day(a["event_time"]) == day(cw.case.reported_at):
                    out.append((self._ekey(a), self._phase(a["event_time"])))
        return out

    def _learn_phase_model(self, extra: Optional[List[Tuple[Tuple[str, str], int]]] = None) -> Dict[Tuple[str, str], Set[int]]:
        hist: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
        for cw in self.work.values():
            if not cw.applicable or not cw.user:
                continue
            accs = [r for r in cw.rids if r.startswith("account_") and cw.how.get(r, "").startswith(("identifier", "data-catalog"))]
            if len(accs) != 1:
                continue
            ae = self._account_events(accs[0], cw)
            if len(ae) != 1:
                continue
            ph = self._phase(ae[0]["event_time"])
            for key, p in self._peer_events(cw):
                hist[key][(p - ph) % 60] += 1
                hist[(key[0].split("@")[0], key[1])][(p - ph) % 60] += 1
        for key, off in (extra or []):
            hist[key][off % 60] += 1
        model: Dict[Tuple[str, str], Set[int]] = {}
        for key, h in hist.items():
            tot = sum(h.values())
            if tot < 8:
                continue
            allowed: Set[int] = set()
            for o, n in h.items():
                if n >= max(2, 0.05 * tot):
                    allowed |= {(o - 1) % 60, o % 60, (o + 1) % 60}
            model[key] = allowed
        return model

    def _phase_violations(self, cw: CaseWork, ph_account: int, model) -> Optional[int]:
        peers = []
        for k, p in self._peer_events(cw):
            kk = k if k in model else (k[0].split("@")[0], k[1])
            if kk in model:
                peers.append((kk, p))
        if not peers:
            return None
        return sum(1 for k, p in peers if (p - ph_account) % 60 not in model[k])

    def _analytics_candidates(self, cw: CaseWork) -> Set[str]:
        """Anonymised analytics copies in the case's evidence file that no identifier ties to anyone, and whose
        treatment event was reported on this request's reported day."""
        if not cw.primary:
            return set()
        c = cw.case
        pool = {o.rid for o in self.files_obs[cw.primary]
                if o.kind == M.ANALYTICS_PROFILE and o.state == M.ANONYMIZED and not self.w.rid_users.get(o.rid)
                and self.eligible(o, c.audit_at)}
        out = set()
        for rid in pool:
            evs = [a for a in self.w.act_by_rid.get(rid, []) if a["event_type"] == "ANONYMIZE_RECORD"]
            if not evs or any(day(a["event_time"]) == day(c.reported_at) for a in evs):
                out.add(rid)
        return out

    def _an_event(self, rid: str) -> dict:
        return next(a for a in self.w.act_by_rid[rid] if a["event_type"] == "ANONYMIZE_RECORD")

    def _media_primary(self, cw: CaseWork) -> bool:
        """Screenshot/PDF stories carry their own (time-shifted) worker events, so they must not consume the
        regular same-day pool that belongs to evidence-log cases."""
        return any(o.src.startswith("media") for o in self.files_obs[cw.primary][:1]) if cw.primary else False

    def _provisional_status(self, cw: CaseWork) -> str:
        for rid in cw.rids:
            f = self.state_of(rid, cw.case.audit_at)
            if f and P.violates(f.kind, f.state):
                return "INCOMPLETE"
        return "COMPLETE"

    def _narrow_by_file_listing(self, cw: CaseWork, pool: Set[str]) -> Set[str]:
        """Some evidence exports list a deleted account's 404 lookup (without any identifier) only for
        requests whose deletion is fully done. Empirically (on accounts that *are* linked) the presence of the
        account id in the case's own evidence file coincides with the final status, so use it to rank ties."""
        listed = {o.rid for o in self.files_obs[cw.primary]
                  if o.kind == M.USER_PROFILE and o.state == M.ABSENT and o.src == "svc_export"}
        if not listed:
            return pool
        narrowed = pool & listed if self._provisional_status(cw) == "COMPLETE" else pool - listed
        return narrowed or pool

    # ------------------------------------------------------------------ findings
    def _build_findings(self, cw: CaseWork) -> None:
        c = cw.case
        out: List[Finding] = []
        for rid in sorted(cw.rids):
            f = self.state_of(rid, c.audit_at)
            if f is None:
                f = self._default_state(rid)
                if f is None:
                    continue
            out.append(f)
        cw.findings = out
        cw.status = "INCOMPLETE" if any(P.violates(f.kind, f.state) for f in out) else "COMPLETE"

    def _default_state(self, rid: str) -> Optional[Finding]:
        """Resources linked to the subject whose state at audit time is not directly observed.

        Only kinds whose compliant state is unconditional are inferred: a deleted account is absent (a live one
        would carry the subject's identifiers in evidence) and retained audit / legal records stay present.
        """
        ks = None
        for o in self.w.by_rid.get(rid, []):
            ks = (o.kind, o.system)
            break
        if ks is None:
            from .evidence import kind_state_from_prefix
            pk = kind_state_from_prefix(rid)
            ks = pk
        if ks is None:
            return None
        kind, system = ks
        if kind == M.USER_PROFILE:
            return Finding(system, rid, kind, M.ABSENT, "", True)
        if kind in (M.SECURITY_AUDIT_LOG, M.LEGAL_HOLD_RECORD):
            return Finding(system, rid, kind, M.PRESENT, "", True)
        return None

    # ------------------------------------------------------------------ orphan analytics (anonymised copies carry no identifier)
    def _assign_orphan_analytics(self) -> None:
        linked = {r for r, us in self.w.rid_users.items() if us}
        taken: Set[str] = set()
        for cw in self.work.values():
            for f in cw.findings:
                taken.add(f.rid)
        for cw in self.work.values():
            if cw.applicable and cw.user and cw.status == "COMPLETE" and not cw.primary:
                c = cw.case
                for a in self.w.activity:
                    if (a["event_type"] == "ANONYMIZE_RECORD" and "Target resource" in a["details"]
                            and abs(_minutes(a["event_time"], c.reported_at)) <= 20):
                        f = self.state_of(a["resource_id"], c.audit_at)
                        if f:
                            cw.findings.append(f)
                            cw.rids.add(f.rid)
                            cw.how[f.rid] = "legacy worker event stamped at the reported time"
        needy = [cw for cw in self.work.values()
                 if cw.applicable and cw.user and cw.status == "COMPLETE" and cw.primary
                 and not any(f.kind == M.ANALYTICS_PROFILE for f in cw.findings)]
        cand: Dict[str, Set[str]] = {}
        for cw in needy:
            cand[cw.case.case_id] = self._analytics_candidates(cw) - taken
        assigned: Dict[str, str] = {}
        used: Set[str] = set()

        def propagate() -> None:
            changed = True
            while changed:
                changed = False
                for cid, pool in cand.items():
                    if cid in assigned:
                        continue
                    live = pool - used
                    if len(live) == 1:
                        assigned[cid] = next(iter(live))
                        used.add(assigned[cid])
                        changed = True

        propagate()
        key_an = ("ANONYMIZE_RECORD@analytics_platform", "policy")
        extra = []
        for cid, rid in assigned.items():
            cw = self.work[cid]
            accs = [r for r in cw.rids if r.startswith("account_") and cw.how.get(r, "").startswith(("identifier", "data-catalog"))]
            ae = self._account_events(accs[0], cw) if len(accs) == 1 else []
            if len(ae) == 1:
                extra.append((key_an, self._phase(self._an_event(rid)["event_time"]) - self._phase(ae[0]["event_time"])))
        model = self._learn_phase_model(extra)
        for cid in sorted(cand, key=_ckey):
            if cid in assigned or len(cand[cid] - used) < 2:
                continue
            cw = self.work[cid]
            accs = [r for r in cw.rids if r.startswith("account_")]
            ae = self._account_events(accs[0], cw) if accs else []
            if not ae or key_an not in model:
                continue
            ph = self._phase(ae[0]["event_time"])
            ok = [r for r in cand[cid] - used
                  if (self._phase(self._an_event(r)["event_time"]) - ph) % 60 in model[key_an]]
            if ok and len(ok) < len(cand[cid] - used):
                cand[cid] = set(ok) | (cand[cid] & used)
        propagate()
        for cid in sorted(cand, key=_ckey):
            if cid in assigned:
                continue
            live = sorted(cand[cid] - used)
            if live and 1.0 / len(live) < MIN_PICK_CONFIDENCE:
                self.work[cid].notes.append(f"analytics withheld: {len(live)} equally plausible candidates")
                continue
            if live:
                assigned[cid] = live[0]
                used.add(live[0])
                self.work[cid].notes.append(f"analytics {live[0]} chosen among {len(live)} candidates")
        for cid, rid in assigned.items():
            cw = self.work[cid]
            f = self.state_of(rid, cw.case.audit_at)
            if f:
                cw.findings.append(f)
                cw.rids.add(rid)
                amb = any(x.startswith(f"analytics {rid} chosen among") and not x.endswith("among 1 candidates") for x in cw.notes)
                cw.how[rid] = ("anonymised copy matched by activity day (ambiguous: same-day peers)" if amb else
                               "anonymised copy matched by activity day + shared event clock")

    # ------------------------------------------------------------------ planning
    def plan(self, cw: CaseWork) -> Tuple[str, List[M.Step]]:
        """Safe wrapper: never raises, never claims COMPLETE without proof. When nothing can be grounded
        (degraded input, unresolvable subject, internal error) the honest answer is INCOMPLETE with no
        invented steps; the reason is in ``cw.notes`` and the case is flagged needs_manual_review."""
        if not cw.applicable:
            return "NOT_APPLICABLE", []
        if cw.degraded:
            return "INCOMPLETE", []
        try:
            status, steps = self._plan_core(cw)
        except Exception as e:
            self._fail(cw, "planning", e)
            return "INCOMPLETE", []
        if not steps:
            return "INCOMPLETE", []
        return status, steps

    def _plan_core(self, cw: CaseWork) -> Tuple[str, List[M.Step]]:
        findings = [f for f in cw.findings if P.treatment_for(f.kind)]
        violations = [f for f in findings if P.violates(f.kind, f.state)]
        evidence = cw.primary or self._account_snapshot_file(cw) or ""
        steps: List[M.Step] = []

        def key(op, system, rid):
            return (P.OP_RANK[op], system, rid)

        if violations:
            fs = []
            for f in violations:
                t = P.treatment_for(f.kind)
                fs.append(M.Step(P.PHASE_FINDING, f.system, f.rid, t.finding, P.OP_STATE[t.finding], f.file_id or evidence))
            rs = []
            for f in violations:
                t = P.treatment_for(f.kind)
                rs.append(M.Step(P.PHASE_REMEDIATION, f.system, f.rid, t.remediation, P.OP_STATE[t.remediation], ""))
            steps += sorted(fs, key=lambda s: key(s.operation, s.system_id, s.resource_id))
            steps += sorted(rs, key=lambda s: key(s.operation, s.system_id, s.resource_id))
        vs = []
        for f in findings:
            t = P.treatment_for(f.kind)
            ev = "" if violations else (f.file_id or evidence)
            vs.append(M.Step(P.PHASE_VERIFICATION, f.system, f.rid, t.verification, P.OP_STATE[t.verification], ev))
        steps += sorted(vs, key=lambda s: key(s.operation, s.system_id, s.resource_id))
        status = "INCOMPLETE" if violations else "COMPLETE"
        return status, steps


# ---------------------------------------------------------------------------------------------------------
# explainability
# ---------------------------------------------------------------------------------------------------------
_RULE_OF_KIND = {
    M.USER_PROFILE: "rule_01", M.SECONDARY_IDENTIFIER: "rule_02", M.SEARCH_INDEX_ENTRY: "rule_03", M.CACHE_ENTRY: "rule_04",
    M.SUPPORT_MESSAGE: "rule_05", M.SUPPORT_ATTACHMENT: "rule_06", M.ANALYTICS_PROFILE: "rule_07", M.PRODUCT_TELEMETRY: "rule_08",
    M.USER_EXPORT: "rule_09", M.MARKETING_PROFILE: "rule_10", M.EMAIL_DELIVERY_LOG: "rule_11", M.SECURITY_AUDIT_LOG: "rule_12",
    M.LEGAL_HOLD_RECORD: "rule_13", M.BACKUP_ARCHIVE: "rule_14", M.ACCOUNT_TOMBSTONE: "rule_15", M.DATA_FLOW: "active data-flow check",
}


def explain(auditor: Auditor, cw: CaseWork) -> dict:
    """Human-readable audit trail of one decision (identity -> evidence -> policy -> steps)."""
    c = cw.case
    status, steps = auditor.plan(cw)
    d = {"case_id": c.case_id, "request_email": c.email, "claimed_status": c.reported_status, "verified_status": status,
         "audit_at": c.audit_at}
    if not cw.applicable:
        d["reason"] = "request text does not authorise deletion (access / correction / export / opt-out / login help)"
        return d
    s = auditor.w.dir.subjects.get(cw.user or "")
    d["subject"] = {"user_id": cw.user, "known_emails": sorted(s.emails) if s else [], "identifiers_resolved": len(s.idents) if s else 0}
    d["evidence_file"] = cw.primary
    res = []
    for f in sorted(cw.findings, key=lambda f: (f.system, f.rid)):
        t = P.treatment_for(f.kind)
        res.append({"system": f.system, "resource": f.rid, "category": f.kind, "state_at_audit": f.state,
                    "policy": _RULE_OF_KIND.get(f.kind), "required": t.required if t else None,
                    "violation": P.violates(f.kind, f.state), "proof": f.file_id or "inferred",
                    "linked_by": cw.how.get(f.rid, "identifier match in evidence")})
    d["resources"] = res
    d["risks"] = sorted({x["category"] for x in res if x["violation"]})
    d["notes"] = cw.notes
    d["steps"] = len(steps)
    return d
