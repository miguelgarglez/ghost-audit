#!/usr/bin/env python3
"""Additional, participant-authored evaluation cases (synthetic mini dataset, fictional people).

Each case isolates one behaviour the audit must get right and carries a hand-written expected outcome.
The mini dataset is built in a temp dir and the *real* agent (``code/main.py``) is run against it via
``GHOST_DATASET`` so the whole pipeline (parsers -> identity linking -> state at audit_at -> policy -> plan) is tested.

Cases
  X1  historical identifiers: request uses the OLD e-mail; an analytics record is keyed only by the OLD e-mail hash
  X2  time travel: a leftover search document only appears in a snapshot captured AFTER audit_at -> must be ignored
  X3  tombstone: stable user reference kept, direct identifiers still present -> REDACT; retained audit record kept
  X4  non-deletion request (access / export) -> NOT_APPLICABLE with []
  X5  recreation risks: active sync connector + restorable backup -> disable flow first, then block restore
  X6  replication drift: profile deleted on primary but still present on the read replica
  X7  compliant everything: redacted support, anonymised analytics, disabled flow, blocked backup -> COMPLETE (verification only)
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
H = lambda s: hashlib.sha256(s.lower().encode()).hexdigest()  # noqa: E731

USERS = {  # user_id: (name, current email, old email or None)
    "user_9001": ("Ana Paredes", "ana.paredes@example.org", "ana.p@example.org"),
    "user_9002": ("Bo Lindqvist", "bo.lindqvist@example.org", None),
    "user_9003": ("Chen Wu", "chen.wu@example.org", None),
    "user_9004": ("Dee Okoro", "dee.okoro@example.org", None),
    "user_9005": ("Eli Navarro", "eli.navarro@example.org", None),
    "user_9006": ("Fay Haddad", "fay.haddad@example.org", None),
    "user_9007": ("Gus Ivanov", "gus.ivanov@example.org", None),
}
AUDIT = "2026-05-10T12:00:00Z"
EARLY = "2026-05-10T09:00:00Z"
LATE = "2026-05-12T09:00:00Z"


def build(tmp: Path) -> None:
    (tmp / "evidence").mkdir()
    (tmp / "evidence" / "records").mkdir()
    (tmp / "evidence" / "databases").mkdir()
    real_rules = (ROOT / "dataset" / "deletion_rules.csv").read_text(encoding="utf-8")
    (tmp / "deletion_rules.csv").write_text(real_rules, encoding="utf-8")

    def w(name, header, rows):
        with open(tmp / name, "w", newline="", encoding="utf-8") as fh:
            cw = csv.writer(fh, lineterminator="\n")
            cw.writerow(header)
            cw.writerows(rows)

    w("user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"],
      [[u, n, e, f"+1 555 000 {i:04d}", H(e), "2026-04-01T00:00:00Z"] for i, (u, (n, e, _)) in enumerate(USERS.items())])
    w("user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"],
      [["audit_x1", "user_9001", "2026-03-01T10:00:00Z", "email", "ana.p@example.org", "ana.paredes@example.org"],
       ["audit_x2", "user_9001", "2026-03-01T10:00:00Z", "email_hash", H("ana.p@example.org"), H("ana.paredes@example.org")]])
    texts = {
        "X1": "Please delete my account; I used my old address before changing it.",
        "X2": "Please erase my account and all personal data.",
        "X3": "I want the account closed. A deleted-account reference may stay if needed.",
        "X4": "Could you send me a copy of the information you hold on me? Please do not delete anything.",
        "X5": "Delete my account and make sure nothing can bring my data back.",
        "X6": "I deleted my account but the same address is still recognised.",
        "X7": "Please audit that my account deletion is really complete.",
    }
    reqs = []
    emails = {"X1": "ana.p@example.org", "X2": USERS["user_9002"][1], "X3": USERS["user_9003"][1], "X4": USERS["user_9004"][1],
              "X5": USERS["user_9005"][1], "X6": USERS["user_9006"][1], "X7": USERS["user_9007"][1]}
    for cid, e in emails.items():
        reqs.append([cid, e, "2026-05-01T09:00:00Z", "COMPLETE", "2026-05-02T09:00:00Z", AUDIT, texts[cid]])
    w("deletion_requests.csv", ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"], reqs)
    w("sample_deletion_requests.csv", ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"], [])
    w("system_activity.csv", ["activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id", "reported_result", "details"],
      [[f"activity_x{i}", "2026-05-02T08:00:00Z", "DELETE_RECORD", "deletion_worker", "account_service", f"account_{900 + i}", "SUCCESS",
        f"Deletion worker submitted primary profile account_{900 + i} to account_service."] for i in range(1, 8) if i != 4])
    manifest = [["evidence_x1", "evidence/records/main_bundle.json", "test", EARLY],
                ["evidence_x2", "evidence/records/late_bundle.json", "test", LATE],
                ["evidence_x3", "evidence/databases/replica.sql", "test", EARLY]]
    w("evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], manifest)

    def snap(store, coll, recs, t=EARLY):
        return {"captured_at": t, "data_store": store, "collection": coll, "records": recs}

    def lookup(rid, subject):
        return {"recorded_at": EARLY, "system": "account_service", "resource_id": rid, "record_type": "USER_PROFILE",
                "result": "NOT_FOUND", "lookup_value": subject}

    main = {"exported_at": EARLY, "source": "test", "snapshots": [
        snap("backup_restore_service", "operational_record_history", [lookup(f"account_{900 + i}", e) for i, e in
                                                                      [(1, "ana.paredes@example.org"), (2, USERS["user_9002"][1]), (3, USERS["user_9003"][1]),
                                                                       (5, USERS["user_9005"][1]), (7, USERS["user_9007"][1])]]),
        snap("analytics_platform", "customer_analytics_reports", [
            {"resource_id": "analytics_901", "user_id": None, "email": None, "phone": None, "email_hash": H("ana.p@example.org"), "metrics": {}}
        ]),
        snap("security_service", "security_audit_events", [
            {"resource_id": "audit_record_903", "subject_reference": "user_9003", "event_name": "account_deletion_reviewed"}]),
        snap("identity_service", "deleted_customer_references", [
            {"resource_id": "tombstone_903", "subject_reference": "user_9003", "name": "Chen Wu", "email": "chen.wu@example.org",
             "phone": None, "email_hash": H("chen.wu@example.org"), "account_status": "DELETED"}]),
        snap("integration_service", "connector_configurations", [
            {"resource_id": "data_flow_905", "subject_selector": "user_9005", "enabled": True, "mode": "continuous_sync"},
            {"resource_id": "data_flow_907", "subject_selector": "user_9007", "enabled": False, "mode": "continuous_sync"}]),
        snap("backup_service", "backup_catalog", [
            {"resource_id": "backup_905", "subject_reference": "user_9005", "restore_allowed": True},
            {"resource_id": "backup_907", "subject_reference": "user_9007", "restore_allowed": False}]),
        snap("support_platform", "support_conversations", [
            {"resource_id": "support_907", "subject_reference": "user_9007", "message_text": "[REDACTED]"}]),
    ]}
    (tmp / "evidence/records/main_bundle.json").write_text(json.dumps(main), encoding="utf-8")
    late = {"exported_at": LATE, "source": "test", "snapshots": [
        snap("search_service", "customer_search_documents", [
            {"resource_id": "search_902", "index_name": "v3", "document": {"email": USERS["user_9002"][1], "customer_reference": "user_9002"}}], LATE)]}
    (tmp / "evidence/records/late_bundle.json").write_text(json.dumps(late), encoding="utf-8")
    sql = f"""-- Captured at: {EARLY}
BEGIN TRANSACTION;
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_data_read_replica (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO customer_data_primary VALUES ('audit_record_906','security_service','security_audit_events','{{"resource_id":"audit_record_906","subject_reference":"user_9006"}}','{EARLY}');
INSERT INTO customer_data_read_replica VALUES ('audit_record_906','security_service','security_audit_events','{{"resource_id":"audit_record_906","subject_reference":"user_9006"}}','{EARLY}');
INSERT INTO customer_data_read_replica VALUES ('account_906','account_service','customer_profiles','{{"resource_id":"account_906","customer_reference":"user_9006","full_name":"Fay Haddad","email":"fay.haddad@example.org","phone":null,"account_status":"ACTIVE"}}','{EARLY}');
COMMIT;
"""
    (tmp / "evidence/databases/replica.sql").write_text(sql, encoding="utf-8")


S = lambda ph, sysid, rid, op, st, ev="": (ph, sysid, rid, op, st, ev)  # noqa: E731
F, R, V = "FINDING", "REMEDIATION", "VERIFICATION"
EXPECTED = {
    "X1": ("INCOMPLETE", [
        S(F, "analytics_platform", "analytics_901", "REPORT_EXPOSED_PII", "PRESENT", "evidence_x1"),
        S(R, "analytics_platform", "analytics_901", "ANONYMIZE_RECORD", "ANONYMIZED"),
        S(V, "account_service", "account_901", "VERIFY_RECORD_ABSENT", "ABSENT"),
        S(V, "analytics_platform", "analytics_901", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED")]),
    "X2": ("COMPLETE", [S(V, "account_service", "account_902", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_x1")]),
    "X3": ("INCOMPLETE", [
        S(F, "identity_service", "tombstone_903", "REPORT_EXPOSED_PII", "PRESENT", "evidence_x1"),
        S(R, "identity_service", "tombstone_903", "REDACT_RECORD", "REDACTED"),
        S(V, "account_service", "account_903", "VERIFY_RECORD_ABSENT", "ABSENT"),
        S(V, "security_service", "audit_record_903", "VERIFY_RECORD_PRESENT", "PRESENT"),
        S(V, "identity_service", "tombstone_903", "VERIFY_RECORD_REDACTED", "REDACTED")]),
    "X4": ("NOT_APPLICABLE", []),
    "X5": ("INCOMPLETE", [
        S(F, "integration_service", "data_flow_905", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "evidence_x1"),
        S(F, "backup_service", "backup_905", "REPORT_RESTORE_RISK", "ACTIVE", "evidence_x1"),
        S(R, "integration_service", "data_flow_905", "DISABLE_DATA_FLOW", "DISABLED"),
        S(R, "backup_service", "backup_905", "BLOCK_RESTORE", "BLOCKED"),
        S(V, "account_service", "account_905", "VERIFY_RECORD_ABSENT", "ABSENT"),
        S(V, "integration_service", "data_flow_905", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
        S(V, "backup_service", "backup_905", "VERIFY_RESTORE_BLOCKED", "BLOCKED")]),
    "X6": ("INCOMPLETE", [
        S(F, "account_service", "account_906", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_x3"),
        S(R, "account_service", "account_906", "DELETE_RECORD", "ABSENT"),
        S(V, "account_service", "account_906", "VERIFY_RECORD_ABSENT", "ABSENT"),
        S(V, "security_service", "audit_record_906", "VERIFY_RECORD_PRESENT", "PRESENT")]),
    "X7": ("COMPLETE", [
        S(V, "account_service", "account_907", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_x1"),
        S(V, "support_platform", "support_907", "VERIFY_RECORD_REDACTED", "REDACTED", "evidence_x1"),
        S(V, "integration_service", "data_flow_907", "VERIFY_DATA_FLOW_DISABLED", "DISABLED", "evidence_x1"),
        S(V, "backup_service", "backup_907", "VERIFY_RESTORE_BLOCKED", "BLOCKED", "evidence_x1")]),
}


def run() -> list[dict]:
    results = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        build(tmp)
        out = tmp / "out.csv"
        env = dict(os.environ, GHOST_DATASET=str(tmp))
        subprocess.run([sys.executable, str(ROOT / "code" / "main.py"), "--out", str(out)], check=True, env=env,
                       capture_output=True, text=True)
        got = {r["case_id"]: r for r in csv.DictReader(open(out, encoding="utf-8"))}
    for cid, (status, steps) in EXPECTED.items():
        g = got[cid]
        gs = [(s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"], s["evidence_id"]) for s in json.loads(g["steps_json"])]
        results.append({"case": cid, "status_ok": g["deletion_status"] == status, "steps_ok": gs == steps,
                        "got_status": g["deletion_status"], "got": gs, "want": steps})
    return results


if __name__ == "__main__":
    res = run()
    for r in res:
        print(r["case"], "OK" if r["status_ok"] and r["steps_ok"] else "FAIL", r["got_status"])
        if not r["steps_ok"]:
            print("   got :", r["got"])
            print("   want:", r["want"])
    raise SystemExit(0 if all(r["status_ok"] and r["steps_ok"] for r in res) else 1)
