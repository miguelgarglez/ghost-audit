#!/usr/bin/env python3
"""Ghost Audit MCP server (stdio, JSON-RPC 2.0, newline-delimited; standard library only).

    python3 code/ghost_mcp.py

Implements initialize, ping, tools/list, tools/call, resources/list, resources/templates/list, resources/read,
prompts/list, prompts/get; notifications are accepted silently. Every tool is read-only. Logs go to stderr.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _take_global_options(argv: list) -> list:
    """--dataset DIR / --ocr-cache DIR (anywhere on the command line) become GHOST_DATASET / GHOST_OCR_CACHE.

    They must be applied before ``ghost`` is imported, because the dataset location is resolved at import time.
    """
    import os

    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        for opt, env in (("--dataset", "GHOST_DATASET"), ("--ocr-cache", "GHOST_OCR_CACHE")):
            if a == opt and i + 1 < len(argv):
                os.environ[env] = str(Path(argv[i + 1]).resolve())
                i += 1
                break
            if a.startswith(opt + "="):
                os.environ[env] = str(Path(a.split("=", 1)[1]).resolve())
                break
        else:
            out.append(a)
        i += 1
    return out


sys.argv[1:] = _take_global_options(sys.argv[1:])

from ghost.agent import VERSION, Ghost, GhostError  # noqa: E402

PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]
RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
STATUS = {"type": "string", "enum": ["COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"]}
CLAIMED = {"type": "string", "enum": ["COMPLETE", "PARTIALLY_COMPLETE", "NOT_STARTED"]}
CASE = {"type": "object", "properties": {"case_id": {"type": "string", "description": "e.g. case_33"},
                                         "mask": {"type": "boolean", "default": False, "description": "mask e-mails, phones and names"}},
        "required": ["case_id"], "additionalProperties": False}
SAMPLES = {"type": "boolean", "default": False, "description": "also include the public sample cases"}


def _tool(name, title, desc, props=None, req=None):
    return {"name": name, "title": title, "description": desc, "annotations": dict(RO, title=title),
            "inputSchema": {"type": "object", "properties": props or {}, "required": req or [], "additionalProperties": False}}


TOOLS = [
    _tool("ghost_describe", "Describe Ghost Audit", "Call first. Capabilities, glossary of statuses/phases/operations, field meanings and known limits."),
    _tool("ghost_summary", "Audit summary", "Portfolio view: verified/claimed status counts, claimed->verified transitions (false COMPLETE), top risk categories, violations by system, manual-review counts. Good for dashboards and executive briefs.",
          {"include_samples": SAMPLES}),
    _tool("ghost_list_cases", "List cases", "Filter cases; returns compact rows (status, risks, steps, confidence, needs_manual_review). Use before ghost_get_case.",
          {"status": STATUS, "claimed": CLAIMED, "risk": {"type": "string", "description": "data category, e.g. USER_EXPORT, SUPPORT_MESSAGE"},
           "needs_review": {"type": "boolean", "default": False}, "q": {"type": "string", "description": "substring of case_id, e-mail or user_id"},
           "limit": {"type": "integer", "minimum": 1, "maximum": 600, "default": 50}, "include_samples": SAMPLES}),
    _tool("ghost_get_case", "Get case", "Full context of one case: request, subject and identifier history, resources with rule/proof/link/activity ids, planned steps, review signals and why claimed differs from verified.",
          CASE["properties"], ["case_id"]),
    _tool("ghost_explain_case", "Explain case", "Deterministic 8-15 line markdown narrative of one decision with [evidence/activity] citations. Quote it, do not paraphrase facts.",
          CASE["properties"], ["case_id"]),
    _tool("ghost_get_evidence", "Get evidence", "Metadata of an evidence file (path, origin, captured_at, resources seen) and a text snippet from the file or OCR cache; binaries are never returned.",
          {"evidence_id": {"type": "string", "description": "e.g. evidence_18"}, "lines": {"type": "integer", "minimum": 1, "maximum": 400, "default": 40},
           "grep": {"type": "string", "description": "only lines containing this text (e.g. a resource id)"}}, ["evidence_id"]),
    _tool("ghost_review_queue", "Review queue", "Prioritised human review queue (withheld/ambiguous links, inferred states, false COMPLETE with active risk) with reasons, a score and confidence_v2 (a review-priority signal, not a probability).",
          {"limit": {"type": "integer", "minimum": 1, "maximum": 600, "default": 25}, "include_samples": SAMPLES,
           "sort": {"type": "string", "enum": ["priority", "confidence_v2"], "default": "priority", "description": "confidence_v2 = least certain first"}}),
    _tool("ghost_whatif", "What-if threshold", "In-memory re-run with another MIN_PICK_CONFIDENCE (default 0.30): which cases would change or become manual (~1 s), and/or which cases a confidence_v2 review threshold would route to a human. Never writes output.csv.",
          {"min_pick_confidence": {"type": "number", "minimum": 0, "maximum": 1},
           "min_confidence_v2": {"type": "number", "minimum": 0, "maximum": 1}}),
    _tool("ghost_ingest_report", "Ingest report", "Ingestion diagnostics: evidence files that could not be parsed, ignored rows/log lines, CSV issues and cases degraded to INCOMPLETE + needs_manual_review. Empty on the shipped dataset."),
    _tool("ghost_verify_output", "Verify output.csv", "Structural validation of output.csv (contract, vocabulary, ids, phase order) plus sha256 and status counts.",
          {"path": {"type": "string", "description": "defaults to the repository output.csv"}}),
]

PROMPTS = {
    "review_deletion_case": ("Review one deletion case and build a case card.", [{"name": "case_id", "required": True}],
                             "Call ghost_get_case and ghost_explain_case for {case_id}. Build a case card artefact: header with claimed vs verified status, a resource table (system, resource, state, rule, violation, proof), the step plan as a checklist, and a review box with confidence and reasons. Cite evidence ids; open ghost_get_evidence (grep=resource id) for any violation you show. Do not invent facts."),
    "executive_brief": ("One-page DPO brief from the portfolio summary.", [],
                        "Call ghost_summary and ghost_review_queue (limit 10). Write a one-page DPO brief: headline numbers (false COMPLETE count first), a risk chart spec (category -> cases), systems with most violations, top 5 cases to review with reasons, and recommended next actions. Only use numbers returned by the tools."),
    "triage_queue": ("Interactive triage of the review queue.", [{"name": "limit", "required": False}],
                     "Call ghost_review_queue with limit {limit}. Render the queue as an interactive table (case, score, confidence, risks, reasons). For the top item call ghost_explain_case and propose the decision a human must take. Ask the user which case to open next."),
}


class Server:
    def __init__(self) -> None:
        self.g = Ghost()

    def call(self, name: str, a: dict):
        g = self.g
        if name == "ghost_describe":
            return g.describe()
        if name == "ghost_summary":
            return g.summary(bool(a.get("include_samples")))
        if name == "ghost_list_cases":
            return g.cases(a.get("status"), a.get("claimed"), a.get("risk"), bool(a.get("needs_review")), a.get("q"),
                           int(a.get("limit", 50)), bool(a.get("include_samples")))
        if name == "ghost_get_case":
            return g.case(self._req(a, "case_id"), bool(a.get("mask")))
        if name == "ghost_explain_case":
            cid = self._req(a, "case_id")
            return {"case_id": cid, "markdown": g.explain(cid, bool(a.get("mask")))}
        if name == "ghost_get_evidence":
            return g.evidence(self._req(a, "evidence_id"), int(a.get("lines", 40)), a.get("grep"))
        if name == "ghost_review_queue":
            return g.queue(int(a.get("limit", 25)), bool(a.get("include_samples")), a.get("sort", "priority"))
        if name == "ghost_whatif":
            mp, mv = a.get("min_pick_confidence"), a.get("min_confidence_v2")
            return g.whatif(None if mp is None else float(mp), None if mv is None else float(mv))
        if name == "ghost_ingest_report":
            return g.ingest_report()
        if name == "ghost_verify_output":
            return g.verify(a.get("path"))
        raise KeyError(name)

    @staticmethod
    def _req(a, k):
        if k not in a:
            raise GhostError(f"missing argument {k!r}", "see inputSchema in tools/list")
        return a[k]

    def handle(self, msg: dict):
        method, params, mid = msg.get("method"), msg.get("params") or {}, msg.get("id")
        if mid is None:  # notification (notifications/initialized, cancelled, ...)
            return None
        try:
            return {"jsonrpc": "2.0", "id": mid, "result": self._dispatch(method, params)}
        except _RpcError as e:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": e.code, "message": e.message}}
        except Exception as e:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"internal error: {e}"}}

    def _dispatch(self, method, p):
        if method == "initialize":
            v = p.get("protocolVersion")
            return {"protocolVersion": v if v in PROTOCOLS else PROTOCOLS[0],
                    "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False, "subscribe": False}, "prompts": {"listChanged": False}},
                    "serverInfo": {"name": "ghost-audit", "title": "Ghost Audit (deletion verification)", "version": VERSION},
                    "instructions": "Read-only deletion-audit tools. Start with ghost_describe or ghost_summary; drill down with ghost_list_cases -> ghost_get_case -> ghost_get_evidence. Data comes only from the audit; never invent ids."}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            name, args = p.get("name"), p.get("arguments") or {}
            if name not in {t["name"] for t in TOOLS}:
                raise _RpcError(-32602, f"unknown tool {name!r}")
            try:
                out = self.call(name, args)
            except GhostError as e:
                return {"content": [{"type": "text", "text": json.dumps(e.as_dict(), ensure_ascii=False)}], "structuredContent": e.as_dict(), "isError": True}
            return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "structuredContent": out, "isError": False}
        if method == "resources/list":
            return {"resources": [{"uri": "ghost://summary", "name": "summary", "mimeType": "application/json", "description": "portfolio summary"},
                                  {"uri": "ghost://queue", "name": "queue", "mimeType": "application/json", "description": "review queue (top 25)"},
                                  {"uri": "ghost://describe", "name": "describe", "mimeType": "application/json", "description": "capabilities and glossary"}]}
        if method == "resources/templates/list":
            return {"resourceTemplates": [{"uriTemplate": "ghost://case/{case_id}", "name": "case", "mimeType": "application/json"},
                                          {"uriTemplate": "ghost://evidence/{evidence_id}", "name": "evidence", "mimeType": "application/json"}]}
        if method == "resources/read":
            uri = p.get("uri", "")
            try:
                out = {"ghost://summary": lambda: self.g.summary(), "ghost://queue": lambda: self.g.queue(),
                       "ghost://describe": lambda: self.g.describe()}.get(uri)
                if out:
                    data = out()
                elif uri.startswith("ghost://case/"):
                    data = self.g.case(uri.split("/", 3)[3])
                elif uri.startswith("ghost://evidence/"):
                    data = self.g.evidence(uri.split("/", 3)[3])
                else:
                    raise _RpcError(-32002, f"resource not found: {uri}")
            except GhostError as e:
                raise _RpcError(-32002, e.error)
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, ensure_ascii=False)}]}
        if method == "prompts/list":
            return {"prompts": [{"name": k, "description": d, "arguments": args} for k, (d, args, _) in PROMPTS.items()]}
        if method == "prompts/get":
            name = p.get("name")
            if name not in PROMPTS:
                raise _RpcError(-32602, f"unknown prompt {name!r}")
            d, args, tpl = PROMPTS[name]
            vals = {"limit": 10, **(p.get("arguments") or {})}
            missing = [x["name"] for x in args if x.get("required") and x["name"] not in vals]
            if missing:
                raise _RpcError(-32602, f"missing prompt arguments: {missing}")
            return {"description": d, "messages": [{"role": "user", "content": {"type": "text", "text": tpl.format(**vals)}}]}
        raise _RpcError(-32601, f"method not found: {method}")


class _RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def main() -> int:
    srv = Server()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"parse error: {e}"}}
        else:
            if isinstance(msg, list):
                resp = [r for r in (srv.handle(m) for m in msg if isinstance(m, dict)) if r] or None
            elif not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                resp = {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None, "error": {"code": -32600, "message": "invalid request"}}
            else:
                resp = srv.handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
