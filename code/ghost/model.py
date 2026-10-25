"""Domain model for the deletion audit.

The central idea: every piece of evidence, whatever its format (JSON, SQL, log, text, PDF, screenshot),
is normalised into an :class:`Obs` -- "at time ``t`` the resource ``rid`` of system ``system`` was in
``state``, and the observation mentioned these identifiers". Everything downstream (identity linking,
state-at-audit-time reconstruction, policy application, step planning) works on ``Obs`` only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet

# ---- data categories (deletion_rules.csv) and the treatment each one requires -----------------------
USER_PROFILE = "USER_PROFILE"
SECONDARY_IDENTIFIER = "SECONDARY_IDENTIFIER"
SEARCH_INDEX_ENTRY = "SEARCH_INDEX_ENTRY"
CACHE_ENTRY = "CACHE_ENTRY"
SUPPORT_MESSAGE = "SUPPORT_MESSAGE"
SUPPORT_ATTACHMENT = "SUPPORT_ATTACHMENT"
ANALYTICS_PROFILE = "ANALYTICS_PROFILE"
PRODUCT_TELEMETRY = "PRODUCT_TELEMETRY"
USER_EXPORT = "USER_EXPORT"
MARKETING_PROFILE = "MARKETING_PROFILE"
EMAIL_DELIVERY_LOG = "EMAIL_DELIVERY_LOG"
SECURITY_AUDIT_LOG = "SECURITY_AUDIT_LOG"
LEGAL_HOLD_RECORD = "LEGAL_HOLD_RECORD"
BACKUP_ARCHIVE = "BACKUP_ARCHIVE"
ACCOUNT_TOMBSTONE = "ACCOUNT_TOMBSTONE"
DATA_FLOW = "DATA_FLOW"  # not a data category: an active process (integration_service connector)

# ---- observed states --------------------------------------------------------------------------------
PRESENT = "PRESENT"
ABSENT = "ABSENT"
IDENTIFIABLE = "IDENTIFIABLE"
ANONYMIZED = "ANONYMIZED"
REDACTED = "REDACTED"
ACTIVE = "ACTIVE"
DISABLED = "DISABLED"
BLOCKED = "BLOCKED"

# resource-id prefix -> (kind, default system)
PREFIX_KIND = {
    "account": (USER_PROFILE, "account_service"),
    "search": (SEARCH_INDEX_ENTRY, "search_service"),
    "support": (SUPPORT_MESSAGE, "support_platform"),
    "analytics": (ANALYTICS_PROFILE, "analytics_platform"),
    "export": (USER_EXPORT, "export_service"),
    "data_flow": (DATA_FLOW, "integration_service"),
    "backup": (BACKUP_ARCHIVE, "backup_service"),
    "audit_record": (SECURITY_AUDIT_LOG, "security_service"),
    "tombstone": (ACCOUNT_TOMBSTONE, "identity_service"),
    "legal_hold": (LEGAL_HOLD_RECORD, "legal_service"),
    "marketing": (MARKETING_PROFILE, "marketing_platform"),
    "cache": (CACHE_ENTRY, "cache_service"),
    "object": (SUPPORT_ATTACHMENT, "object_storage"),
}


@dataclass(frozen=True)
class Obs:
    """One observation of one resource."""

    file_id: str  # evidence file the observation came from
    cap: str  # manifest captured_at of that file (ISO-8601 Z, lexicographically comparable)
    t: str  # time the observed state was true (record/snapshot/log time)
    system: str
    rid: str
    kind: str
    state: str
    idents: FrozenSet[str] = frozenset()
    src: str = ""  # parser tag, for debugging
    copy: str = ""  # replica/table the row came from (replication-drift analysis)
    detail: Dict[str, str] = field(default_factory=dict, hash=False, compare=False)

    def key(self):
        return (self.system, self.rid)


@dataclass
class Case:
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str


@dataclass
class Step:
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str = ""

    def as_json(self, n: int) -> dict:
        return {
            "step_no": n,
            "phase": self.phase,
            "system_id": self.system_id,
            "resource_id": self.resource_id,
            "operation": self.operation,
            "state": self.state,
            "evidence_id": self.evidence_id,
        }
