"""Loading of participant-facing CSV files and identity resolution (current + historical identifiers)."""
from __future__ import annotations

import csv
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set

import os

ROOT = Path(__file__).resolve().parents[2]
# GHOST_DATASET lets the evaluation harness point the agent at a synthetic mini dataset.
# Installed from PyPI/npm there is no repo-level dataset/, so fall back to ./dataset in the working directory.
DATASET = Path(os.environ.get("GHOST_DATASET") or (ROOT / "dataset" if (ROOT / "dataset").is_dir() else Path.cwd() / "dataset"))

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USER = re.compile(r"^user_\d+$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


OPTIONAL = {"user_audit_log.csv", "sample_deletion_requests.csv"}
REQUIRED_COLUMNS = {
    "deletion_requests.csv": ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"],
    "sample_deletion_requests.csv": ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"],
    "user_identities.csv": ["user_id", "name", "email", "phone", "email_hash", "updated_at"],
    "user_audit_log.csv": ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"],
    "system_activity.csv": ["activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id", "reported_result", "details"],
    "deletion_rules.csv": ["rule_id", "data_category", "condition", "required_action"],
    "evidence_files.csv": ["file_id", "file_path", "origin", "captured_at"],
}


def read_csv(name: str, base: Path | None = None) -> List[Dict[str, str]]:
    """Tolerant CSV reader: BOM, CRLF, extra/reordered columns and padded cells are accepted; a missing
    optional file or column is reported to :mod:`ghost.diag` and filled with empty values."""
    from . import diag

    path = (base or DATASET) / name
    if not path.exists() and name in OPTIONAL:
        diag.note("csv_issues", file=name, issue="missing optional file, treated as empty")
        return []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        rd = csv.DictReader(fh)
        header = [(h or "").strip() for h in (rd.fieldnames or [])]
        rd.fieldnames = header
        rows = []
        for r in rd:
            rows.append({(k or "").strip(): (v.strip() if isinstance(v, str) else "") for k, v in r.items() if k is not None})
    missing = [c for c in REQUIRED_COLUMNS.get(name, []) if c not in header]
    if missing:
        diag.note("csv_issues", file=name, issue=f"missing columns {missing}, filled with empty values")
        for r in rows:
            for c in missing:
                r[c] = ""
    return rows


def norm_phone(p: str) -> str:
    return re.sub(r"\D", "", p or "")


def ident_kind(v: str) -> str | None:
    if _EMAIL.match(v):
        return "email"
    if _USER.match(v):
        return "user"
    if _HASH.match(v):
        return "hash"
    d = norm_phone(v)
    if v.strip().startswith("+") and 7 <= len(d) <= 15:
        return "phone"
    return None


def norm_ident(v: str | None) -> str | None:
    """Canonical form of an identifier-like value, or None if it is not one."""
    if v is None:
        return None
    v = str(v).strip().strip('"')
    if not v or v.lower() in {"null", "none", "n/a"}:
        return None
    k = ident_kind(v.lower() if "@" in v or v.lower().startswith("user_") else v)
    if k == "email" or k == "user" or k == "hash":
        return v.lower()
    if k == "phone":
        return "tel:" + norm_phone(v)
    return None


@dataclass
class Subject:
    user_id: str
    name: str
    emails: Set[str] = field(default_factory=set)
    phones: Set[str] = field(default_factory=set)
    hashes: Set[str] = field(default_factory=set)
    current_email: str = ""
    audit_ids: List[str] = field(default_factory=list)

    @property
    def idents(self) -> Set[str]:
        return {self.user_id} | self.emails | self.hashes | {"tel:" + p for p in self.phones}


class Directory:
    """Identity directory built from user_identities.csv + user_audit_log.csv."""

    def __init__(self) -> None:
        self.subjects: Dict[str, Subject] = {}
        for r in read_csv("user_identities.csv"):
            if not r["user_id"]:
                continue
            s = Subject(r["user_id"], r["name"], current_email=r["email"].lower())
            s.emails.add(r["email"].lower())
            s.hashes.add(r["email_hash"].lower())
            s.hashes.add(hashlib.sha256(r["email"].lower().encode()).hexdigest())
            if r["phone"]:
                s.phones.add(norm_phone(r["phone"]))
            self.subjects[s.user_id] = s
        for a in read_csv("user_audit_log.csv"):
            s = self.subjects.get(a["user_id"])
            if s is None:
                continue
            s.audit_ids.append(a["audit_id"])
            for v in (a["old_value"], a["new_value"]):
                if not v:
                    continue
                f = a["field_name"]
                if f == "email":
                    s.emails.add(v.lower())
                    s.hashes.add(hashlib.sha256(v.lower().encode()).hexdigest())
                elif f == "email_hash":
                    s.hashes.add(v.lower())
                elif f == "phone":
                    s.phones.add(norm_phone(v))
        self.by_ident: Dict[str, Set[str]] = defaultdict(set)
        for s in self.subjects.values():
            for i in s.idents:
                self.by_ident[i].add(s.user_id)

    def resolve(self, value: str | None) -> Set[str]:
        n = norm_ident(value)
        return set(self.by_ident.get(n, ())) if n else set()
