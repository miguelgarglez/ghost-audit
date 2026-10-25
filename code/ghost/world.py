"""The world model: directory, normalised evidence, activity stream and the resource->subject links.

Linking rules (all deterministic):

* an observation belongs to a subject when any identifier it carries (current or historical e-mail, e-mail
  hash, phone, ``user_id``) resolves to that subject in :class:`~ghost.dataset.Directory`;
* a resource id inherits the link of any of its observations (so a later, anonymised copy of the same
  record still belongs to the subject);
* ``LINK_RESOURCE_IDENTITY`` activity rows (data catalog) link a resource to an e-mail and user id explicitly.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Set

from . import model as M
from .dataset import Directory, read_csv
from . import diag
from .evidence import Manifest, load_all
from .timeutil import canon

_LINK_RE = re.compile(r"linked (\S+) .*? to (\S+@\S+) and (user_\d+)|belongs only to (\S+@\S+) and (user_\d+)")


class World:
    def __init__(self, ocr=None) -> None:
        if ocr is None:
            from .media import parse_media as ocr
        self.dir = Directory()
        self.manifest = Manifest()
        for r in self.manifest.rows:
            t = canon(r["captured_at"])
            if t is None:
                diag.note("csv_issues", file="evidence_files.csv", row=r["file_id"], issue="unparseable captured_at; file treated as captured after every audit")
                t = "9999-12-31T23:59:59Z"
            r["captured_at"] = t
        self.obs: List[M.Obs] = load_all(self.manifest, ocr=ocr)
        self.activity = []
        for a in read_csv("system_activity.csv"):
            t = canon(a["event_time"])
            if not a["activity_id"] or t is None:
                diag.note("ignored_rows", file="system_activity.csv", row=a.get("activity_id", ""), issue="missing id or unparseable event_time")
                continue
            a["event_time"] = t
            self.activity.append(a)
        self.case_issues: Dict[str, List[str]] = defaultdict(list)
        self._check_policy()
        self.cases = self._cases("deletion_requests.csv")
        self.sample_cases = self._cases("sample_deletion_requests.csv")
        # The activity stream is a closed world: events of the public sample cases and of the evaluation cases
        # share one log, so both are solved by the same algorithm (sample answers are never read here).
        self.universe = self.sample_cases + self.cases
        self.by_rid: Dict[str, List[M.Obs]] = defaultdict(list)
        for o in self.obs:
            self.by_rid[o.rid].append(o)
        self.act_by_rid: Dict[str, List[dict]] = defaultdict(list)
        for a in self.activity:
            self.act_by_rid[a["resource_id"]].append(a)
        self.rid_users: Dict[str, Set[str]] = defaultdict(set)
        for o in self.obs:
            for i in o.idents:
                self.rid_users[o.rid] |= self.dir.by_ident.get(i, set())
        for a in self.activity:
            if a["event_type"] == "LINK_RESOURCE_IDENTITY":
                m = _LINK_RE.search(a["details"])
                if m:
                    email = m.group(2) or m.group(4)
                    user = m.group(3) or m.group(5)
                    self.rid_users[a["resource_id"]] |= self.dir.resolve(email) | {user}
        self.privacy_non_deletion = {
            a["resource_id"] for a in self.activity if a["event_type"] == "CLASSIFY_REQUEST"
        }

    def _check_policy(self) -> None:
        """The treatments are compiled from deletion_rules.csv (ghost/policy.py). Report drift between the
        table and the compiled policy instead of silently guessing."""
        from . import policy as P

        try:
            table = {r["data_category"] for r in read_csv("deletion_rules.csv") if r["data_category"]}
        except Exception as e:
            diag.note("csv_issues", file="deletion_rules.csv", issue=f"unreadable ({type(e).__name__}); compiled policy used")
            return
        known = {k for k in P._BY_KIND if k != M.DATA_FLOW}
        for cat in sorted(table - known):
            diag.note("csv_issues", file="deletion_rules.csv", row=cat, issue="unknown data category: no treatment compiled; records of this category are not audited (needs a policy entry)")
        for cat in sorted(known - table):
            diag.note("csv_issues", file="deletion_rules.csv", row=cat, issue="category has no rule row; compiled treatment kept")

    def _cases(self, name: str) -> List[M.Case]:
        """Build cases defensively: ids are trimmed, duplicates keep the first row, timestamps are
        canonicalised to UTC; a case whose dates cannot be read is kept and flagged (never dropped)."""
        out, seen = [], set()
        for r in read_csv(name):
            cid = r["case_id"]
            if not cid:
                diag.note("ignored_rows", file=name, issue="row without case_id")
                continue
            if cid in seen:
                diag.note("ignored_rows", file=name, row=cid, issue="duplicate case_id, first row kept")
                continue
            seen.add(cid)
            for k in ("requested_at", "reported_at", "audit_at"):
                t = canon(r[k])
                if t is None:
                    if k != "reported_at":
                        self.case_issues[cid].append(f"degraded input: {k} missing or unparseable ({r[k]!r})")
                    t = ""
                r[k] = t
            if not r["request_text"]:
                self.case_issues[cid].append("degraded input: request_text missing (deletion intent cannot be confirmed)")
            if r["reported_status"] not in ("COMPLETE", "PARTIALLY_COMPLETE", "NOT_STARTED"):
                diag.note("csv_issues", file=name, row=cid, issue=f"unknown reported_status {r['reported_status']!r} (claim ignored, as always)")
            out.append(M.Case(**{k: r[k] for k in M.Case.__dataclass_fields__}))
        return out

    def subject_of(self, case: M.Case) -> str | None:
        us = self.dir.resolve(case.email)
        return next(iter(us)) if len(us) == 1 else None

    def linked_rids(self, user_id: str) -> Set[str]:
        return {r for r, us in self.rid_users.items() if user_id in us}
