"""Tests for the agent-facing CLI (ghost_cli.py) and the stdio MCP server (ghost_mcp.py)."""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "dataset" / "deletion_requests.csv").exists():
    raise unittest.SkipTest("needs the HackerRank challenge dataset/ (not redistributed in the public repo); run `ghost-audit demo` or tests/test_demo.py instead")
sys.path.insert(0, str(ROOT / "code"))

if not (ROOT / "output.csv").exists():  # clean checkout from code.zip: produce it exactly as `python3 code/main.py` does
    subprocess.run([sys.executable, str(ROOT / "code" / "main.py")], check=True, capture_output=True)

import ghost_cli  # noqa: E402
from ghost.agent import Ghost  # noqa: E402

G = Ghost()
OUTPUT_SHA = "ea64cff4bade32d64fd94d9fcb8d1b040e6a31910265c1c3ac92608b94ec850f"


def cli(*args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ghost_cli.run(list(args), G)
    return rc, buf.getvalue()


def cli_json(*args):
    rc, out = cli(*args, "--format", "json")
    return rc, json.loads(out)


class CliTest(unittest.TestCase):
    def test_describe(self):
        rc, d = cli_json("describe")
        self.assertEqual(rc, 0)
        self.assertFalse(d["runtime"]["llm"])
        self.assertEqual(len(d["tools"]), 10)
        self.assertIn("VERIFY_RECORD_ABSENT", d["glossary"]["operations"]["VERIFICATION"])

    def test_summary_matches_output(self):
        _, d = cli_json("summary")
        self.assertEqual(d["cases"], 518)
        self.assertEqual(d["verified_status"], {"COMPLETE": 153, "INCOMPLETE": 355, "NOT_APPLICABLE": 10})
        self.assertEqual(d["false_complete"], 163)

    def test_cases_filters(self):
        _, d = cli_json("cases", "--claimed", "COMPLETE", "--status", "INCOMPLETE", "--limit", "5")
        self.assertEqual(d["total"], 163)
        self.assertEqual(d["returned"], 5)
        _, d = cli_json("cases", "--risk", "USER_EXPORT", "--q", "ingrid.dahl")
        self.assertIn("case_33", [c["case_id"] for c in d["cases"]])
        self.assertTrue(all("USER_EXPORT" in c["risks"] for c in d["cases"]))
        _, d = cli_json("cases", "--needs-review", "--limit", "500")
        self.assertTrue(all(c["needs_manual_review"] and c["confidence"] < 1 for c in d["cases"]))

    def test_case_23_golden(self):
        _, d = cli_json("case", "case_23")
        self.assertTrue(d["sample"])
        self.assertEqual((d["claimed_status"], d["verified_status"]), ("NOT_STARTED", "INCOMPLETE"))
        self.assertEqual(d["subject"]["user_id"], "user_23")
        self.assertEqual(d["risks"], ["SEARCH_INDEX_ENTRY", "USER_EXPORT"])
        self.assertEqual(len(d["plan"]), 8)
        self.assertEqual(d["primary_evidence"], "evidence_92")
        ex = next(r for r in d["resources"] if r["resource"] == "export_7364")
        self.assertEqual((ex["policy"], ex["violation"], ex["proof"]), ("rule_09", True, "evidence_92"))

    def test_case_33_golden_and_mask(self):
        _, d = cli_json("case", "case_33")
        self.assertEqual(d["claimed_vs_verified"][:39], "claimed COMPLETE, verified INCOMPLETE: ")
        self.assertEqual([r["resource"] for r in d["resources"]], ["export_1842"])
        _, m = cli_json("case", "case_33", "--mask")
        blob = json.dumps(m)
        self.assertNotIn("ingrid.dahl", blob)
        self.assertEqual(m["plan"], d["plan"])

    def test_plan_equals_output_csv(self):
        import csv
        rows = {r["case_id"]: r for r in csv.DictReader(open(ROOT / "output.csv", encoding="utf-8"))}
        for cid in ("case_33", "case_34", "case_104"):
            d = G.case(cid)
            self.assertEqual(d["plan"], json.loads(rows[cid]["steps_json"]))
            self.assertEqual(d["verified_status"], rows[cid]["deletion_status"])

    def test_explain(self):
        rc, out = cli("explain", "case_23", "--format", "md")
        lines = out.strip().splitlines()
        self.assertEqual(rc, 0)
        self.assertTrue(8 <= len(lines) <= 15)
        self.assertIn("[evidence_92", out)
        self.assertIn("activity_", out)

    def test_evidence(self):
        _, d = cli_json("evidence", "evidence_18", "--grep", "1842")
        self.assertEqual(d["text_source"], "ocr_cache")
        self.assertIn("export_1842", d["resources"])
        self.assertGreater(d["matches"], 0)

    def test_queue_sorted(self):
        _, d = cli_json("queue", "--limit", "500")
        scores = [q["priority_score"] for q in d["queue"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(q["reasons"] for q in d["queue"]))

    def test_whatif_does_not_write(self):
        before = hashlib.sha256((ROOT / "output.csv").read_bytes()).hexdigest()
        _, d = cli_json("whatif", "--min-pick-confidence", "0.6")
        self.assertEqual(d["cases_changed"], 35)
        _, d = cli_json("whatif", "--min-pick-confidence", "0.3")
        self.assertEqual(d["cases_changed"], 0)
        self.assertEqual(hashlib.sha256((ROOT / "output.csv").read_bytes()).hexdigest(), before)

    def test_verify_and_errors(self):
        rc, d = cli_json("verify")
        self.assertEqual((rc, d["ok"], d["sha256"]), (0, True, OUTPUT_SHA))
        rc, d = cli_json("case", "case_nope")
        self.assertEqual(rc, 3)
        self.assertIn("hint", d)
        self.assertEqual(cli("bogus")[0], 2)

    def test_ingest_report_clean_on_shipped_dataset(self):
        rc, d = cli_json("ingest-report")
        self.assertEqual(rc, 0)
        self.assertTrue(d["clean"], d["counts"])
        self.assertEqual(set(d["counts"]), {"unparsed_files", "repaired_files", "csv_issues", "ignored_rows", "degraded_cases"})


class McpTest(unittest.TestCase):
    def test_stdio_protocol(self):
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ghost_get_case", "arguments": {"case_id": "case_23"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "ghost_get_case", "arguments": {"case_id": "zzz"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "ping"},
            {"jsonrpc": "2.0", "id": 6, "method": "nope"},
            {"jsonrpc": "2.0", "id": 7, "method": "resources/read", "params": {"uri": "ghost://case/case_33"}},
            {"jsonrpc": "2.0", "id": 8, "method": "prompts/get", "params": {"name": "review_deletion_case", "arguments": {"case_id": "case_33"}}},
        ]
        p = subprocess.run([sys.executable, str(ROOT / "code" / "ghost_mcp.py")], input="\n".join(json.dumps(r) for r in reqs) + "\nnot json\n",
                           capture_output=True, text=True, timeout=120)
        resp = [json.loads(x) for x in p.stdout.splitlines()]
        self.assertEqual([r["id"] for r in resp], [1, 2, 3, 4, 5, 6, 7, 8, None])  # notification gets no reply
        self.assertTrue(all(r["jsonrpc"] == "2.0" for r in resp))
        self.assertEqual(resp[0]["result"]["protocolVersion"], "2025-06-18")
        tools = resp[1]["result"]["tools"]
        self.assertIn("ghost_get_case", {t["name"] for t in tools})
        self.assertIn("ghost_ingest_report", {t["name"] for t in tools})
        self.assertTrue(all(t["annotations"]["readOnlyHint"] and t["inputSchema"]["type"] == "object" for t in tools))
        res = resp[2]["result"]
        self.assertFalse(res["isError"])
        self.assertEqual(json.loads(res["content"][0]["text"])["verified_status"], "INCOMPLETE")
        self.assertEqual(res["structuredContent"]["subject"]["user_id"], "user_23")
        self.assertTrue(resp[3]["result"]["isError"])
        self.assertEqual(resp[4]["result"], {})
        self.assertEqual(resp[5]["error"]["code"], -32601)
        self.assertIn("export_1842", resp[6]["result"]["contents"][0]["text"])
        self.assertIn("case_33", resp[7]["result"]["messages"][0]["content"]["text"])
        self.assertEqual(resp[8]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
