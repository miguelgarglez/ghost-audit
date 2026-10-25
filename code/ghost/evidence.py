"""Parsers turning every evidence format into normalised observations (:class:`ghost.model.Obs`).

Formats handled: JSON service exports, JSON snapshot bundles, JSON/TXT/LOG record-lookup ledgers,
SQLite-compatible SQL snapshots (inventory, primary/replica topologies), service logs in seven line
dialects, PDFs (text layer) and screenshots (OCR, cached).
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import model as M
from .dataset import DATASET, norm_ident, read_csv
from .logparse import parse_log

EVIDENCE_DIR = DATASET / "evidence"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+\d[\d ()\-]{6,}\d")

SYSTEM_ALIASES = {"profile_service": "account_service", "object_store": "object_storage"}

# record_type (ledger vocabulary) -> kind
RECORD_TYPE_KIND = {k: k for k in (
    M.USER_PROFILE, M.SEARCH_INDEX_ENTRY, M.SUPPORT_MESSAGE, M.SUPPORT_ATTACHMENT, M.ANALYTICS_PROFILE,
    M.PRODUCT_TELEMETRY, M.USER_EXPORT, M.MARKETING_PROFILE, M.EMAIL_DELIVERY_LOG, M.SECURITY_AUDIT_LOG,
    M.LEGAL_HOLD_RECORD, M.BACKUP_ARCHIVE, M.ACCOUNT_TOMBSTONE, M.CACHE_ENTRY, M.SECONDARY_IDENTIFIER)}
RECORD_TYPE_KIND["DATA_FLOW"] = M.DATA_FLOW

_RESULT_STATE = {
    "FOUND": M.PRESENT, "PRESENT": M.PRESENT, "NOT_FOUND": M.ABSENT, "ABSENT": M.ABSENT,
    "IDENTIFIABLE": M.IDENTIFIABLE, "ANONYMIZED": M.ANONYMIZED, "REDACTED": M.REDACTED,
    "ACTIVE": M.ACTIVE, "DISABLED": M.DISABLED, "BLOCKED": M.BLOCKED,
}


def idents_of(*values) -> frozenset:
    out = set()
    for v in values:
        if v is None:
            continue
        if isinstance(v, (list, tuple, set)):
            out |= idents_of(*v)
            continue
        s = str(v)
        n = norm_ident(s)
        if n:
            out.add(n)
        for e in _EMAIL_RE.findall(s):
            out.add(e.lower())
        for p in _PHONE_RE.findall(s):
            n = norm_ident(p)
            if n:
                out.add(n)
    return frozenset(out)


def is_null(v) -> bool:
    return v is None or str(v).strip().lower() in {"", "null", "none"}


def kind_state_from_prefix(rid: str):
    m = re.match(r"([a-z_]+?)_\d+$", rid)
    return M.PREFIX_KIND.get(m.group(1)) if m else None


def system_of(rid: str, given: Optional[str] = None) -> str:
    if given:
        return SYSTEM_ALIASES.get(given, given)
    ks = kind_state_from_prefix(rid)
    return ks[1] if ks else "unknown"


class Manifest:
    def __init__(self) -> None:
        self.rows = read_csv("evidence_files.csv")
        self.by_id = {r["file_id"]: r for r in self.rows}

    def path(self, fid: str) -> Path:
        return DATASET / self.by_id[fid]["file_path"]


# ---------------------------------------------------------------------------------------------------
# collection-record vocabulary (snapshot bundles + SQL replicas share it)
# ---------------------------------------------------------------------------------------------------

def from_collection(fid, cap, t, system, coll, rec, copy="", src="collection") -> Optional[M.Obs]:
    rid = rec.get("resource_id")
    if not rid:
        return None
    system = SYSTEM_ALIASES.get(system, system)
    kind = state = None
    ids: frozenset = frozenset()
    detail: Dict[str, str] = {}
    if coll == "customer_profiles":
        kind, state = M.USER_PROFILE, M.PRESENT
        ids = idents_of(rec.get("customer_reference"), rec.get("email"), rec.get("phone"), rec.get("email_hash"), rec.get("user_id"))
        detail["account_status"] = str(rec.get("account_status"))
    elif coll == "customer_analytics_reports":
        kind = M.ANALYTICS_PROFILE
        ids = idents_of(rec.get("user_id"), rec.get("email"), rec.get("phone"), rec.get("email_hash"))
        state = M.IDENTIFIABLE if ids else M.ANONYMIZED
    elif coll == "backup_catalog":
        kind = M.BACKUP_ARCHIVE
        state = M.ACTIVE if rec.get("restore_allowed") else M.BLOCKED
        ids = idents_of(rec.get("subject_reference"))
    elif coll == "connector_configurations":
        kind = M.DATA_FLOW
        state = M.ACTIVE if rec.get("enabled") else M.DISABLED
        ids = idents_of(rec.get("subject_selector"))
    elif coll == "retained_order_records":
        kind, state = M.LEGAL_HOLD_RECORD, M.PRESENT
        ids = idents_of(rec.get("subject_reference"))
    elif coll == "customer_search_documents":
        kind, state = M.SEARCH_INDEX_ENTRY, M.PRESENT
        d = rec.get("document") or {}
        ids = idents_of(d.get("email"), d.get("customer_reference"), d.get("phone"), d.get("email_hash"))
    elif coll == "support_conversations":
        kind = M.SUPPORT_MESSAGE
        txt = str(rec.get("message_text") or "")
        direct = idents_of(txt)
        ids = idents_of(rec.get("subject_reference")) | direct
        state = M.IDENTIFIABLE if direct else M.REDACTED
    elif coll == "ticket_attachments":
        kind, state = M.SUPPORT_ATTACHMENT, M.PRESENT
        ids = idents_of(rec.get("uploaded_by"))
    elif coll == "customer_export_jobs":
        kind, state = M.USER_EXPORT, M.PRESENT
        ids = idents_of(rec.get("owner_email"), rec.get("owner_phone"), rec.get("customer_reference"))
        detail["download_available"] = str(rec.get("download_available"))
    elif coll == "security_audit_events":
        kind, state = M.SECURITY_AUDIT_LOG, M.PRESENT
        ids = idents_of(rec.get("subject_reference"))
    elif coll == "deleted_customer_references":
        kind = M.ACCOUNT_TOMBSTONE
        ids = idents_of(rec.get("subject_reference"))
        direct = idents_of(rec.get("name"), rec.get("email"), rec.get("phone"), rec.get("email_hash")) - ids
        direct_present = any(not is_null(rec.get(k)) for k in ("name", "email", "phone", "email_hash"))
        ids = ids | direct
        state = M.IDENTIFIABLE if direct_present else M.REDACTED
    elif coll == "operational_record_history":
        return from_record_lookup(fid, cap, rec.get("recorded_at") or t, rec.get("system"), rid,
                                  rec.get("record_type"), rec.get("result"), rec.get("lookup_value"), src="bundle_history")
    if kind is None:
        return None
    return M.Obs(fid, cap, t, system, rid, kind, state, ids, src, copy, detail)


def from_record_lookup(fid, cap, t, system, rid, record_type, result, subject, src="ledger") -> Optional[M.Obs]:
    kind = RECORD_TYPE_KIND.get(record_type)
    state = _RESULT_STATE.get(str(result).upper())
    if kind is None or state is None:
        return None
    return M.Obs(fid, cap, t, system_of(rid, system), rid, kind, state, idents_of(subject), src)


# ---------------------------------------------------------------------------------------------------
# file parsers
# ---------------------------------------------------------------------------------------------------

def parse_service_export(fid, cap, j) -> List[M.Obs]:
    out: List[M.Obs] = []
    for svc, secs in j["service_exports"].items():
        for sec, rows in secs.items():
            for r in rows:
                t = r.get("observed_at") or cap
                rid = r["resource_id"]
                if svc == "account_service":
                    p = r.get("profile") or {}
                    present = r.get("status_code") == 200 and bool(p)
                    out.append(M.Obs(fid, cap, t, svc, rid, M.USER_PROFILE, M.PRESENT if present else M.ABSENT,
                                     idents_of(*p.values()), "svc_export"))
                elif svc == "analytics_platform":
                    ids = idents_of(r.get("customer_reference"), r.get("email"), r.get("phone"), r.get("email_hash"))
                    out.append(M.Obs(fid, cap, t, svc, rid, M.ANALYTICS_PROFILE,
                                     M.IDENTIFIABLE if ids else M.ANONYMIZED, ids, "svc_export"))
                elif svc == "backup_service":
                    out.append(M.Obs(fid, cap, t, svc, rid, M.BACKUP_ARCHIVE,
                                     M.ACTIVE if r.get("restore_allowed") else M.BLOCKED,
                                     idents_of(r.get("customer_email"), r.get("customer_reference")), "svc_export"))
                elif svc == "export_service":
                    out.append(M.Obs(fid, cap, t, svc, rid, M.USER_EXPORT, M.PRESENT,
                                     idents_of(r.get("owner_email"), r.get("owner_phone"), r.get("customer_reference")), "svc_export"))
                elif svc == "integration_service":
                    out.append(M.Obs(fid, cap, t, svc, rid, M.DATA_FLOW, M.ACTIVE if r.get("enabled") else M.DISABLED,
                                     idents_of(r.get("customer_email"), r.get("customer_reference")), "svc_export"))
                elif svc == "search_service":
                    docs = (r.get("hits") or {}).get("documents") or []
                    ids = idents_of(r.get("query"))
                    for d in docs:
                        ids |= idents_of(d.get("email"), d.get("customer_reference"), d.get("phone"), d.get("email_hash"))
                    present = (r.get("hits") or {}).get("total", 0) > 0 or bool(docs)
                    out.append(M.Obs(fid, cap, t, svc, rid, M.SEARCH_INDEX_ENTRY, M.PRESENT if present else M.ABSENT, ids, "svc_export"))
                elif svc == "security_service":
                    out.append(M.Obs(fid, cap, t, svc, rid, M.SECURITY_AUDIT_LOG, M.PRESENT, idents_of(r.get("subject_reference")), "svc_export"))
                elif svc == "support_platform":
                    direct = idents_of(r.get("requester_email"), r.get("requester_phone"), r.get("message_excerpt"))
                    if not is_null(r.get("requester_name")):
                        direct |= frozenset({"name:" + str(r["requester_name"]).lower()})
                    ids = direct | idents_of(r.get("customer_reference"))
                    out.append(M.Obs(fid, cap, t, svc, rid, M.SUPPORT_MESSAGE, M.IDENTIFIABLE if direct else M.REDACTED, ids, "svc_export"))
    return out


def parse_bundle(fid, cap, j) -> List[M.Obs]:
    out = []
    for snap in j["snapshots"]:
        for rec in snap["records"]:
            o = from_collection(fid, cap, snap["captured_at"], snap["data_store"], snap["collection"], rec, src="bundle")
            if o:
                out.append(o)
    return out


def parse_ledger_json(fid, cap, j) -> List[M.Obs]:
    out = []
    for r in j["records"]:
        o = from_record_lookup(fid, cap, r["recorded_at"], r.get("system"), r["resource_id"], r["record_type"], r["result"], r.get("subject_reference"))
        if o:
            out.append(o)
    return out


_LEDGER_LINE = re.compile(r"^-\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(.*?)\s*$")


def parse_ledger_txt(fid, cap, text) -> List[M.Obs]:
    out = []
    for ln in text.splitlines():
        m = _LEDGER_LINE.match(ln)
        if m:
            o = from_record_lookup(fid, cap, m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6))
            if o:
                out.append(o)
    return out


def parse_flat_json_collection(fid, cap, j) -> List[M.Obs]:
    out = []
    coll = j.get("collection")
    for r in j["records"]:
        o = from_collection(fid, cap, j.get("captured_at") or cap, "", coll, r, src="flat_json")
        if o:
            o = M.Obs(**{**o.__dict__, "system": system_of(o.rid)})
            out.append(o)
    return out


_CAP_RE = re.compile(r"^-- Captured at:\s*(\S+)", re.M)


def parse_sql(fid, cap, text) -> List[M.Obs]:
    m = _CAP_RE.search(text)
    snap_t = m.group(1) if m else cap
    db = sqlite3.connect(":memory:")
    db.executescript(text)
    db.row_factory = sqlite3.Row
    tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
    out: List[M.Obs] = []
    for tname in tables:
        cols = [r[1] for r in db.execute(f"pragma table_info({tname})")]
        rows = [dict(r) for r in db.execute(f"select * from {tname}")]
        if "record_json" in cols:
            for r in rows:
                rec = json.loads(r["record_json"])
                o = from_collection(fid, cap, snap_t, r["origin_system"], r["collection_name"], rec, copy=tname, src="sql_generic")
                if o:
                    out.append(o)
            continue
        if tname == "snapshot_scope":
            continue
        for r in rows:
            o = _typed_row(fid, cap, snap_t, tname, r)
            if o:
                out.append(o)
    return out


def _typed_row(fid, cap, t, tname, r) -> Optional[M.Obs]:
    rid = r["resource_id"]
    if tname == "customer_profiles":
        ids = idents_of(r.get("customer_reference"), r.get("email"), r.get("phone"), r.get("email_hash"))
        return M.Obs(fid, cap, t, "account_service", rid, M.USER_PROFILE, M.PRESENT, ids, "sql_inventory", tname,
                     {"account_status": str(r.get("account_status"))})
    if tname == "customer_search_documents":
        ids = idents_of(r.get("customer_reference"), r.get("email"), r.get("phone"), r.get("email_hash"))
        return M.Obs(fid, cap, t, "search_service", rid, M.SEARCH_INDEX_ENTRY, M.PRESENT, ids, "sql_inventory", tname)
    if tname == "customer_analytics_profiles":
        ids = idents_of(r.get("customer_reference"), r.get("email"), r.get("phone"), r.get("email_hash"))
        return M.Obs(fid, cap, t, "analytics_platform", rid, M.ANALYTICS_PROFILE, M.IDENTIFIABLE if ids else M.ANONYMIZED, ids, "sql_inventory", tname)
    if tname == "support_records":
        direct = idents_of(r.get("requester_email"), r.get("requester_phone"), r.get("message_excerpt"))
        if not is_null(r.get("requester_name")):
            direct |= frozenset({"name:" + str(r["requester_name"]).lower()})
        ids = direct | idents_of(r.get("customer_reference"))
        return M.Obs(fid, cap, t, "support_platform", rid, M.SUPPORT_MESSAGE, M.IDENTIFIABLE if direct else M.REDACTED, ids, "sql_inventory", tname)
    if tname == "customer_exports":
        ids = idents_of(r.get("customer_reference"), r.get("owner_email"), r.get("owner_phone"))
        return M.Obs(fid, cap, t, "export_service", rid, M.USER_EXPORT, M.PRESENT, ids, "sql_inventory", tname)
    if tname == "connector_subscriptions":
        ids = idents_of(r.get("customer_reference"), r.get("customer_email"))
        return M.Obs(fid, cap, t, "integration_service", rid, M.DATA_FLOW, M.ACTIVE if int(r["enabled"]) else M.DISABLED, ids, "sql_inventory", tname)
    if tname == "backup_restore_entries":
        ids = idents_of(r.get("customer_reference"), r.get("customer_email"))
        return M.Obs(fid, cap, t, "backup_service", rid, M.BACKUP_ARCHIVE, M.ACTIVE if int(r["restore_allowed"]) else M.BLOCKED, ids, "sql_inventory", tname)
    if tname == "security_audit_records":
        return M.Obs(fid, cap, t, "security_service", rid, M.SECURITY_AUDIT_LOG, M.PRESENT, idents_of(r.get("subject_reference")), "sql_inventory", tname)
    if tname.startswith("customer_profiles_"):
        ids = idents_of(r.get("user_id"), r.get("email"), r.get("phone"))
        return M.Obs(fid, cap, t, "account_service", rid, M.USER_PROFILE, M.PRESENT, ids, "sql_failover", tname.replace("customer_profiles_", ""))
    if tname == "search_documents_primary":
        ids = idents_of(r.get("email"), r.get("source_resource_id"))
        return M.Obs(fid, cap, t, "search_service", rid, M.SEARCH_INDEX_ENTRY, M.PRESENT, ids, "sql_search_primary", "primary",
                     {"source_resource_id": str(r.get("source_resource_id"))})
    return None


# ---- logs ------------------------------------------------------------------------------------------------
_KEYFIELDS = ("lookup_key", "match_key", "subject_key", "scan_key", "owner_key", "subject_ref", "correlation_key",
              "email", "customer_email", "customer_reference", "requester_email", "requester_phone", "owner_email",
              "matched_value", "subject_reference", "phone", "email_hash", "requester_name")


def _truthy(v) -> bool:
    return str(v).strip().lower() in {"true", "1", "yes"}


def parse_log_file(fid, cap, text) -> List[M.Obs]:
    lines = list(parse_log(text))
    nonblank = sum(1 for ln in text.splitlines() if ln.strip())
    if nonblank > len(lines):
        from . import diag

        diag.note("ignored_rows", file_id=fid, lines=nonblank - len(lines), issue="log lines in no known dialect")
    out: List[M.Obs] = []
    groups: Dict[str, List[dict]] = defaultdict(list)
    for r in lines:
        if r.get("event") == "record_lookup":
            o = from_record_lookup(fid, cap, r["ts"], r.get("system"), r.get("resource_id"), r.get("record_type"),
                                   r.get("result"), r.get("subject_reference"), src="log_ledger")
            if o:
                out.append(o)
            continue
        tid = r.get("trace_id")
        if tid and tid != "-":
            groups[tid].append(r)
    for tid, rs in groups.items():
        ids = set()
        for r in rs:
            for k in _KEYFIELDS:
                if k in r:
                    ids |= idents_of(r[k])
            if "message_text" in r:
                ids |= idents_of(r["message_text"])
        for r in rs:
            o = _log_event(fid, cap, r, ids, rs)
            if o:
                out.append(o)
    return out


def _log_event(fid, cap, r, ids, group) -> Optional[M.Obs]:
    ev = r.get("event")
    rid = r.get("resource_id")
    t = r["ts"]
    svc = SYSTEM_ALIASES.get(r.get("service", ""), r.get("service", ""))
    if not rid:
        return None
    fids = frozenset(ids)
    mk = lambda kind, state, sysn=None: M.Obs(fid, cap, t, sysn or system_of(rid), rid, kind, state, fids, "log:" + str(ev))
    if ev == "profile_lookup_completed":
        present = str(r.get("http_status")) == "200" and str(r.get("rows", "1")) not in {"0"}
        return mk(M.USER_PROFILE, M.PRESENT if present else M.ABSENT)
    if ev == "index_query_completed":
        return mk(M.SEARCH_INDEX_ENTRY, M.PRESENT if r.get("result") == "hit" else M.ABSENT)
    if ev == "analytics_profile_read_completed":
        direct = any(not is_null(r.get(k)) for k in ("customer_reference", "email", "phone", "email_hash"))
        return mk(M.ANALYTICS_PROFILE, M.IDENTIFIABLE if direct else M.ANONYMIZED)
    if ev == "analytics_profile_read":
        direct = (not is_null(r.get("email"))) or (not is_null(r.get("phone"))) or str(r.get("direct_identifiers", "0")) not in {"0", ""}
        return mk(M.ANALYTICS_PROFILE, M.IDENTIFIABLE if direct else M.ANONYMIZED)
    if ev == "analytics_identity_scan":
        return mk(M.ANALYTICS_PROFILE, M.IDENTIFIABLE)
    if ev == "contact_lookup_completed":
        return mk(M.SECONDARY_IDENTIFIER, M.PRESENT if str(r.get("rows", "1")) != "0" else M.ABSENT)
    if ev == "restore_policy_read_completed":
        active = _truthy(r.get("restore_allowed")) and _truthy(r.get("archive_present", "true"))
        return mk(M.BACKUP_ARCHIVE, M.ACTIVE if active else M.BLOCKED)
    if ev == "restore_candidate_evaluated":
        active = _truthy(r.get("eligible")) and not _truthy(r.get("deletion_block"))
        return mk(M.BACKUP_ARCHIVE, M.ACTIVE if active else M.BLOCKED)
    if ev == "restore_authorization_completed":
        return mk(M.BACKUP_ARCHIVE, M.BLOCKED if str(r.get("decision")).lower() == "deny" else M.ACTIVE)
    if ev in ("export_metadata_read_completed", "export_download_check_completed"):
        return mk(M.USER_EXPORT, M.PRESENT if _truthy(r.get("available")) else M.ABSENT)
    if ev in ("connector_config_read_completed", "connector_config_loaded", "connector_heartbeat"):
        active = _truthy(r.get("enabled")) and str(r.get("scheduler_registered", "true")).lower() != "false"
        return mk(M.DATA_FLOW, M.ACTIVE if active else M.DISABLED)
    if ev == "audit_record_read_completed":
        return mk(M.SECURITY_AUDIT_LOG, M.PRESENT if str(r.get("rows", "1")) != "0" else M.ABSENT)
    if ev == "support_ticket_read_completed":
        direct = any(not is_null(r.get(k)) for k in ("requester_name", "requester_email", "requester_phone"))
        return mk(M.SUPPORT_MESSAGE, M.IDENTIFIABLE if direct else M.REDACTED)
    if ev == "message_pii_scan":
        return mk(M.SUPPORT_MESSAGE, M.IDENTIFIABLE)
    if ev == "message_read":
        return mk(M.SUPPORT_MESSAGE, M.REDACTED if "REDACTED" in str(r.get("message_text")) else M.IDENTIFIABLE)
    if ev == "attachment_pii_scan":
        return mk(M.SUPPORT_ATTACHMENT, M.PRESENT)
    if ev == "contact_query_completed":
        return mk(M.SECONDARY_IDENTIFIER, M.PRESENT if str(r.get("rows", "1")) != "0" else M.ABSENT)
    if ev == "retained_order_loaded":
        return mk(M.LEGAL_HOLD_RECORD, M.PRESENT)
    return None


def load_all(manifest: Optional[Manifest] = None, ocr=None) -> List[M.Obs]:
    """Parse every manifest file into observations. One bad file never stops the audit: it is skipped and
    reported in :mod:`ghost.diag` (unparsed_files), so the cases that depend on it degrade to manual review
    instead of the whole run failing."""
    from . import diag

    manifest = manifest or Manifest()
    obs: List[M.Obs] = []
    known = (".json", ".sql", ".log", ".txt", ".pdf", ".png")
    for row in manifest.rows:
        fid, cap = row["file_id"], row["captured_at"]
        try:
            p = manifest.path(fid)
        except Exception as e:  # malformed manifest row
            diag.note("unparsed_files", file_id=fid, path=row.get("file_path", ""), error=f"bad manifest row: {e}")
            continue
        ext = p.suffix.lower()
        if ext not in known:
            diag.note("unparsed_files", file_id=fid, path=row.get("file_path", ""), error=f"no parser for '{ext}' files")
            continue
        try:
            text = None
            if ext in (".json", ".sql", ".log", ".txt"):
                raw = p.read_bytes()
                text = raw.decode("utf-8", errors="replace")
                if "\ufffd" in text:
                    diag.note("repaired_files", file_id=fid, path=row["file_path"], issue="invalid UTF-8 bytes replaced")
                if not text.strip():
                    diag.note("unparsed_files", file_id=fid, path=row["file_path"], error="empty file")
                    continue
            got: List[M.Obs] = []
            if ext == ".json":
                j = json.loads(text)
                if not isinstance(j, dict):
                    raise ValueError("top-level JSON is not an object")
                if "service_exports" in j:
                    got = parse_service_export(fid, cap, j)
                elif "snapshots" in j:
                    got = parse_bundle(fid, cap, j)
                elif j.get("records") and isinstance(j["records"], list) and isinstance(j["records"][0], dict) and "record_type" in j["records"][0]:
                    got = parse_ledger_json(fid, cap, j)
                elif "collection" in j:
                    got = parse_flat_json_collection(fid, cap, j)
                else:
                    raise ValueError("unknown JSON layout")
            elif ext == ".sql":
                got = parse_sql(fid, cap, text)
            elif ext == ".log":
                got = parse_log_file(fid, cap, text)
            elif ext == ".txt":
                got = parse_ledger_txt(fid, cap, text)
            elif ext in (".pdf", ".png") and ocr is not None:
                got = ocr(fid, cap, p)
            obs += [o for o in got if o is not None]
        except Exception as e:
            diag.note("unparsed_files", file_id=fid, path=row.get("file_path", ""), error=f"{type(e).__name__}: {str(e)[:160]}")
    return obs
