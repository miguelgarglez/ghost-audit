"""Synthetic demo dataset for ``ghost-audit demo``.

Everything here is invented: the company ("Lumen Lane Books"), the people, the e-mails (reserved ``example.org``
domain), the systems' records and the evidence files. No challenge data is included. The policy table is a generic
example of deletion rules (category -> required action) in the same schema the agent reads.

Cases (all claimed COMPLETE by the operator):
  demo_01  truly complete: profile gone, support redacted, sync disabled, backup restore blocked -> COMPLETE
  demo_02  leftovers: analytics keyed by the user's OLD e-mail hash + active sync + restorable backup -> INCOMPLETE
  demo_03  replication drift: profile deleted on primary, still on the read replica -> INCOMPLETE
  demo_04  an access/export request, not a deletion -> NOT_APPLICABLE
  demo_05  e-mail matches no known subject -> INCOMPLETE + needs_manual_review
  demo_06  only evidence is a screenshot and no OCR cache is installed -> evidence degraded, flagged for review
"""
from __future__ import annotations

import csv
import hashlib
import json
import struct
import zlib
from pathlib import Path

H = lambda s: hashlib.sha256(s.lower().encode()).hexdigest()  # noqa: E731
AUDIT, EARLY = "2026-05-10T12:00:00Z", "2026-05-10T09:00:00Z"
RULES = [
    ("rule_01", "USER_PROFILE", "Always", "DELETE"),
    ("rule_02", "SECONDARY_IDENTIFIER", "Always", "DELETE"),
    ("rule_03", "SEARCH_INDEX_ENTRY", "Always", "DELETE"),
    ("rule_04", "CACHE_ENTRY", "Always", "DELETE"),
    ("rule_05", "SUPPORT_MESSAGE", "Conversation contains shared customer, agent, or order context that must be retained", "REDACT"),
    ("rule_06", "SUPPORT_ATTACHMENT", "Attachment belongs only to the deletion subject", "DELETE"),
    ("rule_07", "ANALYTICS_PROFILE", "Record is still attributable to a user", "ANONYMIZE"),
    ("rule_08", "PRODUCT_TELEMETRY", "Record is still attributable to a user", "ANONYMIZE"),
    ("rule_09", "USER_EXPORT", "Always", "DELETE"),
    ("rule_10", "MARKETING_PROFILE", "Always", "DELETE"),
    ("rule_11", "EMAIL_DELIVERY_LOG", "Log contains a user email address", "REDACT"),
    ("rule_12", "SECURITY_AUDIT_LOG", "Record is within the supplied retention period", "RETAIN"),
    ("rule_13", "LEGAL_HOLD_RECORD", "A supplied legal hold is active", "RETAIN"),
    ("rule_14", "BACKUP_ARCHIVE", "Restore access can be blocked", "RETAIN"),
    ("rule_15", "ACCOUNT_TOMBSTONE", "Account is deleted and only a stable internal user_id is needed to preserve references to retained records", "REDACT"),
]
USERS = {  # user_id: (name, current e-mail)
    "user_7101": ("Iris Calloway", "iris.calloway@example.org"),
    "user_7102": ("Jonah Pell", "jonah.pell@example.org"),
    "user_7103": ("Kira Marsh", "kira.marsh@example.org"),
    "user_7104": ("Leo Brandt", "leo.brandt@example.org"),
    "user_7106": ("Nell Ortega", "nell.ortega@example.org"),
}
CASES = {
    "demo_01": ("iris.calloway@example.org", "Please delete my Lumen Lane Books account and everything linked to it."),
    "demo_02": ("jonah.pell@example.org", "Erase my account and make sure nothing can bring my data back."),
    "demo_03": ("kira.marsh@example.org", "I deleted my account but your app still recognises my address."),
    "demo_04": ("leo.brandt@example.org", "Could you send me a copy of the data you hold on me? Please do not delete anything."),
    "demo_05": ("someone.unknown@example.org", "Please remove my account."),
    "demo_06": ("nell.ortega@example.org", "Please close my account and delete my personal data."),
}


def _png(path: Path) -> None:
    """A tiny blank 8x8 grey PNG (stands in for a console screenshot that needs OCR)."""
    raw = b"".join(b"\x00" + b"\x80" * 8 for _ in range(8))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)  # noqa: E731
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 0, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def write(target: Path) -> Path:
    """Write the synthetic dataset into ``target`` (created if needed) and return it."""
    target = Path(target)
    for sub in ("records", "databases", "images"):
        (target / "evidence" / sub).mkdir(parents=True, exist_ok=True)

    def w(name, header, rows):
        with open(target / name, "w", newline="", encoding="utf-8") as fh:
            cw = csv.writer(fh, lineterminator="\n")
            cw.writerow(header)
            cw.writerows(rows)

    w("deletion_rules.csv", ["rule_id", "data_category", "condition", "required_action"], RULES)
    w("user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"],
      [[u, n, e, f"+1 555 010 {i:04d}", H(e), "2026-04-01T00:00:00Z"] for i, (u, (n, e)) in enumerate(USERS.items())])
    w("user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"],
      [["audit_d1", "user_7102", "2026-03-01T10:00:00Z", "email", "jpell@example.org", "jonah.pell@example.org"],
       ["audit_d2", "user_7102", "2026-03-01T10:00:00Z", "email_hash", H("jpell@example.org"), H("jonah.pell@example.org")]])
    hdr = ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"]
    w("deletion_requests.csv", hdr, [[c, e, "2026-05-01T09:00:00Z", "COMPLETE", "2026-05-02T09:00:00Z", AUDIT, t]
                                     for c, (e, t) in CASES.items()])
    w("sample_deletion_requests.csv", hdr, [])
    w("system_activity.csv", ["activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id",
                              "reported_result", "details"],
      [[f"activity_d{i}", "2026-05-02T08:00:00Z", "DELETE_RECORD", "deletion_worker", "account_service", f"account_710{i}", "SUCCESS",
        f"Deletion worker submitted primary profile account_710{i} to account_service."] for i in (1, 2, 3, 6)])
    w("evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"],
      [["evidence_d1", "evidence/records/lumen_lane_bundle.json", "demo_records_export", EARLY],
       ["evidence_d2", "evidence/databases/customer_replica.sql", "demo_database_dump", EARLY],
       ["evidence_d3", "evidence/images/account_console_nell.png", "demo_console_screenshot", EARLY]])

    def snap(store, coll, recs):
        return {"captured_at": EARLY, "data_store": store, "collection": coll, "records": recs}

    def lookup(rid, subject):
        return {"recorded_at": EARLY, "system": "account_service", "resource_id": rid, "record_type": "USER_PROFILE",
                "result": "NOT_FOUND", "lookup_value": subject}

    bundle = {"exported_at": EARLY, "source": "demo", "snapshots": [
        snap("backup_restore_service", "operational_record_history",
             [lookup("account_7101", "iris.calloway@example.org"), lookup("account_7102", "jonah.pell@example.org")]),
        snap("analytics_platform", "customer_analytics_reports", [
            {"resource_id": "analytics_7102", "user_id": None, "email": None, "phone": None, "email_hash": H("jpell@example.org"), "metrics": {}}]),
        snap("integration_service", "connector_configurations", [
            {"resource_id": "data_flow_7102", "subject_selector": "user_7102", "enabled": True, "mode": "continuous_sync"},
            {"resource_id": "data_flow_7101", "subject_selector": "user_7101", "enabled": False, "mode": "continuous_sync"}]),
        snap("backup_service", "backup_catalog", [
            {"resource_id": "backup_7102", "subject_reference": "user_7102", "restore_allowed": True},
            {"resource_id": "backup_7101", "subject_reference": "user_7101", "restore_allowed": False}]),
        snap("support_platform", "support_conversations", [
            {"resource_id": "support_7101", "subject_reference": "user_7101", "message_text": "[REDACTED]"}]),
    ]}
    (target / "evidence/records/lumen_lane_bundle.json").write_text(json.dumps(bundle, indent=1), encoding="utf-8")
    cols = "(resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
    prof = '{"resource_id":"account_7103","customer_reference":"user_7103","full_name":"Kira Marsh","email":"kira.marsh@example.org","phone":null,"account_status":"ACTIVE"}'
    (target / "evidence/databases/customer_replica.sql").write_text(
        f"-- Captured at: {EARLY}\nBEGIN TRANSACTION;\nCREATE TABLE customer_data_primary {cols};\n"
        f"CREATE TABLE customer_data_read_replica {cols};\n"
        f"INSERT INTO customer_data_read_replica VALUES ('account_7103','account_service','customer_profiles','{prof}','{EARLY}');\n"
        "COMMIT;\n", encoding="utf-8")
    _png(target / "evidence/images/account_console_nell.png")
    (target / "README.txt").write_text("Synthetic Ghost Audit demo dataset (fictional company Lumen Lane Books; invented people "
                                       "and records). See ghost/demo.py for what each case demonstrates.\n", encoding="utf-8")
    return target
