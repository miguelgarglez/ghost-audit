"""Deletion policy (deletion_rules.csv) applied to a (kind, observed state) pair.

The rule table is loaded from the dataset so the mapping category -> required action is data-driven;
only the vocabulary translating a rule action into steps is code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import model as M
from .dataset import read_csv

PHASE_FINDING, PHASE_REMEDIATION, PHASE_VERIFICATION = "FINDING", "REMEDIATION", "VERIFICATION"

# Operation vocabulary in the order the problem statement lists it (this is the sort order inside a phase).
FINDING_OPS = [
    ("REPORT_REMAINING_RECORD", "PRESENT"),
    ("REPORT_EXPOSED_PII", "PRESENT"),
    ("REPORT_ACTIVE_DATA_FLOW", "ACTIVE"),
    ("REPORT_RESTORE_RISK", "ACTIVE"),
]
REMEDIATION_OPS = [
    ("DISABLE_DATA_FLOW", "DISABLED"),
    ("BLOCK_RESTORE", "BLOCKED"),
    ("DELETE_RECORD", "ABSENT"),
    ("REDACT_RECORD", "REDACTED"),
    ("ANONYMIZE_RECORD", "ANONYMIZED"),
]
VERIFICATION_OPS = [
    ("VERIFY_RECORD_ABSENT", "ABSENT"),
    ("VERIFY_RECORD_PRESENT", "PRESENT"),
    ("VERIFY_RECORD_REDACTED", "REDACTED"),
    ("VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
    ("VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
    ("VERIFY_RESTORE_BLOCKED", "BLOCKED"),
]
OP_RANK = {op: i for phase in (FINDING_OPS, REMEDIATION_OPS, VERIFICATION_OPS) for i, (op, _) in enumerate(phase)}
OP_STATE = {op: st for phase in (FINDING_OPS, REMEDIATION_OPS, VERIFICATION_OPS) for op, st in phase}


@dataclass(frozen=True)
class Treatment:
    """What policy requires for one record and what the observed state means."""

    required: str  # DELETE | REDACT | ANONYMIZE | RETAIN | BLOCK | DISABLE
    finding: Optional[str]  # finding operation when the observed state violates policy
    remediation: Optional[str]
    verification: str


_BY_KIND = {
    # data categories whose rule is "DELETE" (whole record)
    M.USER_PROFILE: ("DELETE", M.PRESENT),
    M.SECONDARY_IDENTIFIER: ("DELETE", M.PRESENT),
    M.SEARCH_INDEX_ENTRY: ("DELETE", M.PRESENT),
    M.CACHE_ENTRY: ("DELETE", M.PRESENT),
    M.SUPPORT_ATTACHMENT: ("DELETE", M.PRESENT),
    M.USER_EXPORT: ("DELETE", M.PRESENT),
    M.MARKETING_PROFILE: ("DELETE", M.PRESENT),
    # REDACT
    M.SUPPORT_MESSAGE: ("REDACT", M.IDENTIFIABLE),
    M.EMAIL_DELIVERY_LOG: ("REDACT", M.IDENTIFIABLE),
    M.ACCOUNT_TOMBSTONE: ("REDACT", M.IDENTIFIABLE),
    # ANONYMIZE
    M.ANALYTICS_PROFILE: ("ANONYMIZE", M.IDENTIFIABLE),
    M.PRODUCT_TELEMETRY: ("ANONYMIZE", M.IDENTIFIABLE),
    # RETAIN
    M.SECURITY_AUDIT_LOG: ("RETAIN", None),
    M.LEGAL_HOLD_RECORD: ("RETAIN", None),
    # restore / flow
    M.BACKUP_ARCHIVE: ("BLOCK", M.ACTIVE),
    M.DATA_FLOW: ("DISABLE", M.ACTIVE),
}

_TREATMENT = {
    "DELETE": Treatment("DELETE", "REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "REDACT": Treatment("REDACT", "REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "ANONYMIZE": Treatment("ANONYMIZE", "REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    "RETAIN": Treatment("RETAIN", None, None, "VERIFY_RECORD_PRESENT"),
    "BLOCK": Treatment("BLOCK", "REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
    "DISABLE": Treatment("DISABLE", "REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
}


def load_rules():
    """The rule table, keyed by data_category (kept for reporting / validation)."""
    return {r["data_category"]: r for r in read_csv("deletion_rules.csv")}


def treatment_for(kind: str) -> Optional[Treatment]:
    spec = _BY_KIND.get(kind)
    return _TREATMENT[spec[0]] if spec else None


def violates(kind: str, state: str) -> bool:
    """True when the observed ``state`` of a ``kind`` record breaks policy."""
    spec = _BY_KIND.get(kind)
    return bool(spec and spec[1] and state == spec[1])


# Order in which same-kind copies are combined: the first listed state is the "worst" (non-compliant).
def worst_state(kind: str, states) -> str:
    spec = _BY_KIND.get(kind)
    states = list(states)
    if spec and spec[1] in states:
        return spec[1]
    return states[0]
