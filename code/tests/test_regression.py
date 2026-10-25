"""Regression tests. Run:  python3 -m unittest discover -s code/tests -v   (or `make test`).

They pin the behaviour that was hard to get right: identity/time reconstruction, plan ordering, the identifier-free
linking (activity elimination + shared event clock), parser dialects, intent classification, packaging rules and
determinism. Everything runs against the real dataset except the synthetic cases (custom_cases.py).
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "dataset" / "deletion_requests.csv").exists():
    raise unittest.SkipTest("needs the HackerRank challenge dataset/ (not redistributed in the public repo); run `ghost-audit demo` or tests/test_demo.py instead")
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT / "code" / "evaluation"))

from ghost.audit import Auditor  # noqa: E402
from ghost.dataset import read_csv  # noqa: E402
from ghost.intent import authorizes_deletion  # noqa: E402
from ghost.logparse import parse_line  # noqa: E402
from ghost.world import World  # noqa: E402

_STATE = {}


def audit():
    if "a" not in _STATE:
        w = World()
        a = Auditor(w)
        a.prepare()
        _STATE.update(w=w, a=a)
    return _STATE["w"], _STATE["a"]


def steps_of(a, cid):
    st, steps = a.plan(a.work[cid])
    return st, [(s.phase, s.system_id, s.resource_id, s.operation, s.state, s.evidence_id) for s in steps]


def gold():
    out = {}
    for r in read_csv("sample_output.csv"):
        out[r["case_id"]] = (r["deletion_status"], [(s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"], s["evidence_id"])
                                                    for s in json.loads(r["steps_json"])])
    return out


class SampleRegression(unittest.TestCase):
    # samples whose golden answer is fully reproduced today; a regression here is a real bug
    EXACT = [f"case_{i:02d}" for i in (1, 2, 3, 5, 6, 7, 8, 9, 10)] + [f"case_{i}" for i in
            (11, 12, 13, 14, 15, 16, 17, 19, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32)]
    # known, documented gaps: information absent from the data (see limits_and_ceiling.md)
    KNOWN_GAPS = {"case_04": "legacy account id has no event/identifier", "case_18": "audit id absent from evidence",
                  "case_20": "audit id absent from evidence"}

    def test_status_accuracy_is_perfect(self):
        _, a = audit()
        g = gold()
        for cid, (status, _) in g.items():
            self.assertEqual(steps_of(a, cid)[0], status, cid)

    def test_exact_rows(self):
        _, a = audit()
        g = gold()
        for cid in self.EXACT:
            self.assertEqual(steps_of(a, cid)[1], g[cid][1], cid)

    def test_known_gaps_never_add_wrong_steps(self):
        _, a = audit()
        g = gold()
        for cid in self.KNOWN_GAPS:
            self.assertTrue(set(steps_of(a, cid)[1]) <= set(g[cid][1]), f"{cid}: predicted a step the golden answer lacks")


class LinkingWithoutIdentifiers(unittest.TestCase):
    def test_shared_event_clock_resolves_analytics_tie(self):
        """case_03 and case_44 share a report day and both have a treated analytics copy; the copies carry no
        identifier. Their treatment events sit at a constant clock offset from the request's other events."""
        _, a = audit()
        st3 = steps_of(a, "case_03")[1]
        st44 = steps_of(a, "case_44")[1]
        self.assertIn(("VERIFICATION", "analytics_platform", "analytics_9406", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", "evidence_94"), st3)
        self.assertIn(("VERIFICATION", "analytics_platform", "analytics_5589", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", "evidence_94"), st44)

    def test_account_recovery_rate_when_links_are_hidden(self):
        import masking_test

        ok, wrong, withheld = masking_test.trial(seed=0, fraction=0.5)
        self.assertGreaterEqual(ok / (ok + wrong + withheld), 0.80)
        self.assertLessEqual(wrong / (ok + wrong + withheld), 0.08)

    def test_ambiguous_picks_are_rare_and_flagged(self):
        w, a = audit()
        from ghost.audit import explain

        amb = sum(1 for c in w.cases if any("ambiguous" in r["linked_by"] for r in explain(a, a.work[c.case_id]).get("resources", [])))
        self.assertLessEqual(amb, 30)

    def test_multi_request_subject_keeps_windows_apart(self):
        _, a = audit()
        s52 = steps_of(a, "case_52")[1]
        s33 = steps_of(a, "case_33")[1]
        self.assertTrue(all(x[2] == "export_1260" for x in s52))
        self.assertTrue(all(x[2] == "export_1842" for x in s33))


class TimeTravelAndPolicy(unittest.TestCase):
    def test_late_snapshot_is_never_cited(self):
        _, a = audit()
        ev = {x[5] for x in steps_of(a, "case_23")[1] if x[5]}
        self.assertEqual(ev, {"evidence_92"})  # evidence_02 (same people, captured two days later) is ignored

    def test_plan_order_disable_flow_and_block_restore_first(self):
        _, a = audit()
        ops = [x[3] for x in steps_of(a, "case_11")[1] if x[0] == "REMEDIATION"]
        self.assertEqual(ops, ["DISABLE_DATA_FLOW", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"])

    def test_retained_audit_record_is_verified_present_never_deleted(self):
        w, a = audit()
        for c in w.cases:
            for s in steps_of(a, c.case_id)[1]:
                if s[2].startswith("audit_record_"):
                    self.assertEqual((s[0], s[3]), ("VERIFICATION", "VERIFY_RECORD_PRESENT"))

    def test_tombstone_is_redacted_not_deleted(self):
        _, a = audit()
        st = steps_of(a, "case_32")[1]
        self.assertIn(("REMEDIATION", "identity_service", "tombstone_9178", "REDACT_RECORD", "REDACTED", ""), st)


class Intent(unittest.TestCase):
    def test_non_deletion_requests(self):
        w, a = audit()
        na = {c.case_id for c in w.cases if not authorizes_deletion(c.request_text)}
        self.assertEqual(na, {f"case_{i}" for i in range(533, 543)})
        for c in w.cases:
            self.assertEqual(a.work[c.case_id].applicable, c.case_id not in na)
            if c.case_id in na:
                self.assertEqual(a.plan(a.work[c.case_id]), ("NOT_APPLICABLE", []))

    def test_agrees_with_privacy_intake_events(self):
        w, a = audit()
        self.assertEqual(len(w.privacy_non_deletion), 10)
        self.assertEqual(sum(1 for c in w.cases if not a.work[c.case_id].applicable), 10)

    def test_multilingual_and_implicit_deletion(self):
        for t in ["Solicito la eliminación permanente de la cuenta asociada a este correo.",
                  "Je demande la suppression définitive du compte.", "このメールアドレスに関連するアカウントの完全な削除を依頼します。",
                  "이 이메일 주소에 연결된 계정의 영구 삭제를 요청합니다.", "Can you verify that my account was deleted?",
                  "Please audit the deletion as it stood when my request was reviewed."]:
            self.assertTrue(authorizes_deletion(t), t)
        for t in ["Please keep the account as it is.", "I'd like to download my data. Do not close the account.",
                  "Please remove me from marketing lists only."]:
            self.assertFalse(authorizes_deletion(t), t)


class ParserDialects(unittest.TestCase):
    CASES = {
        "logfmt": ('2026-09-15T11:56:57Z level=INFO service=account_service trace_id=tr-1 resource_id=account_9797 event=profile_lookup_completed rows=0 http_status=404', "account_service"),
        "json": ('2026-09-10T08:56:17Z {"severity":"DEBUG","service":"account_service","trace_id":"tr-1","event":"profile_lookup_started","lookup_key":"a@b.co"}', "account_service"),
        "syslog": ('2026-08-22T10:51:11Z <15>1 node-1 account_service tr-1 - - level=DEBUG event="profile_lookup_started" lookup_key="a@b.co"', "account_service"),
        "bracket": ('2026-02-22T10:58:45Z [DEBUG] [account_service] [tr-1] profile_lookup_started: lookup_key="a@b.co" attempt="1"', "account_service"),
        "cloud": ('2026-09-30T11:51:03Z requestId=tr-1 level=DEBUG function=profile_service message="resource_id=\\"account_1\\" event=\\"profile_lookup_completed\\" http_status=\\"200\\" correlation_key=\\"a@b.co\\""', "profile_service"),
        "pipe": ('2026-02-03T05:51:23Z | DEBUG | security_service | tr-1 | audit_record_query_started | subject_ref="user_517" attempt="1"', "security_service"),
    }

    def test_every_dialect_parses_to_service_and_event(self):
        for name, (line, svc) in self.CASES.items():
            r = parse_line(line)
            self.assertIsNotNone(r, name)
            self.assertEqual(r.get("service"), svc, name)
            self.assertTrue(r.get("event"), name)

    def test_all_evidence_logs_parse_without_blank_events(self):
        for p in (ROOT / "dataset" / "evidence" / "logs").glob("*.log"):
            for ln in p.read_text(encoding="utf-8").splitlines():
                r = parse_line(ln)
                self.assertIsNotNone(r, (p.name, ln[:80]))
                self.assertTrue(r.get("event"), (p.name, ln[:80]))


class Contract(unittest.TestCase):
    def test_runtime_does_not_read_labels_or_organizer_files(self):
        for p in (ROOT / "code" / "ghost").glob("*.py"):
            src = p.read_text(encoding="utf-8")
            self.assertNotIn("sample_output", src, p.name)
            self.assertNotIn("golden", src.lower(), p.name)

    def test_output_is_valid_and_deterministic(self):
        import validate_output

        digests = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "o.csv"
                subprocess.run([sys.executable, str(ROOT / "code" / "main.py"), "--out", str(out)], check=True, capture_output=True)
                self.assertEqual(validate_output.validate(out), [])
                digests.append(hashlib.sha256(out.read_bytes()).hexdigest())
        self.assertEqual(digests[0], digests[1])

    def test_synthetic_cases(self):
        import custom_cases

        for r in custom_cases.run():
            self.assertTrue(r["status_ok"] and r["steps_ok"], r["case"])

    def test_code_zip_rules(self):
        z = ROOT / "code.zip"
        if not z.exists():
            self.skipTest("code.zip not built")
        names = zipfile.ZipFile(z).namelist()
        self.assertIn("README.md", names)
        self.assertIn("code/evaluation/evaluation_report.md", names)
        self.assertIn("code/evaluation/usage_report.md", names)
        self.assertIn("evaluation/evaluation_report.md", names)
        self.assertIn("evaluation/usage_report.md", names)
        self.assertFalse([n for n in names if n.startswith("dataset/") or "__pycache__" in n or re.search(r"\.(zip|tar|gz|tgz|7z|rar)$", n)])

    @unittest.skipUnless((ROOT / "log.txt").exists(), "log.txt is a repo-only artifact (not in code.zip)")
    def test_log_txt_entries_have_tool_and_model(self):
        log = (ROOT / "log.txt").read_text(encoding="utf-8")
        entries = re.split(r"\n(?=## \[)", log)
        entries = [e for e in entries if e.startswith("## [")]
        self.assertGreaterEqual(len(entries), 2)
        for e in entries:
            self.assertEqual(len(re.findall(r"^tool=\S.*$", e, re.M)), 1, e[:60])
            self.assertEqual(len(re.findall(r"^model=\S.*$", e, re.M)), 1, e[:60])
            self.assertNotRegex(e, r"(?i)model=(unknown|ai)\b")


class SpecCompliance(unittest.TestCase):
    @unittest.skipUnless((ROOT / "problem_statement.md").exists(), "problem_statement.md is a repo-only artifact (not in code.zip)")
    def test_operation_table_matches_problem_statement(self):
        from ghost import policy as P

        text = (ROOT / "problem_statement.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\| `(FINDING|REMEDIATION|VERIFICATION)` \| `([A-Z_]+)` \| `([A-Z]+)` \|", text, re.M)
        self.assertEqual(len(rows), 15)
        ours = {(ph, op, st) for ph, table in (("FINDING", P.FINDING_OPS), ("REMEDIATION", P.REMEDIATION_OPS), ("VERIFICATION", P.VERIFICATION_OPS))
                for op, st in table}
        self.assertEqual(set(rows), ours)
        order = [(ph, op) for ph, op, _ in rows]
        self.assertEqual(order, [(ph, op) for ph, table in (("FINDING", P.FINDING_OPS), ("REMEDIATION", P.REMEDIATION_OPS), ("VERIFICATION", P.VERIFICATION_OPS))
                                 for op, _ in table])

    def test_every_deletion_rule_category_is_handled(self):
        from ghost import policy as P

        for cat in P.load_rules():
            self.assertIsNotNone(P.treatment_for(cat), cat)

    def test_report_contents_required_by_readme(self):
        ev = (ROOT / "code" / "evaluation" / "evaluation_report.md").read_text(encoding="utf-8")
        for needle in ("Case-level", "Step", "Field-level", "Sequence accuracy", "Failures found and improvements made"):
            self.assertIn(needle, ev)
        self.assertGreaterEqual(len(re.findall(r"^\| X\d \|", ev, re.M)), 3)  # >= 3 participant-authored cases
        us = (ROOT / "code" / "evaluation" / "usage_report.md").read_text(encoding="utf-8").lower()
        for needle in ("providers", "model calls", "input tokens", "total tokens", "average per request", "cost"):
            self.assertIn(needle, us)

    @unittest.skipUnless((ROOT / "package.sh").exists(), "package.sh not present")
    def test_packaging_uses_the_documented_zip_command(self):
        sh = (ROOT / "package.sh").read_text(encoding="utf-8")
        self.assertIn('zip -r code.zip README.md Makefile package.sh LICENSE CHANGELOG.md pyproject.toml docs evaluation/evaluation_report.md evaluation/usage_report.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"', sh)

    def test_no_secrets_or_personal_data_in_shipped_text(self):
        pat = re.compile(r"(sk-[A-Za-z0-9]{20,}|x-access-token|api[_-]?key\s*[=:]\s*\S{8,}|BEGIN (RSA|PRIVATE)|Bearer [A-Za-z0-9._-]{20,})", re.I)
        files = [ROOT / "README.md", ROOT / "SUBMISSION.md", ROOT / "log.txt"] + [q for q in (ROOT / "code").rglob("*.py") if q.name != "test_regression.py"] + list((ROOT / "code").rglob("*.md"))
        files = [p for p in files if p.exists()]  # SUBMISSION.md / log.txt are repo-only
        for p in files:
            self.assertIsNone(pat.search(p.read_text(encoding="utf-8")), p.name)
        if not (ROOT / "log.txt").exists():
            return
        log = (ROOT / "log.txt").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", log), "log.txt must not contain e-mail addresses")

    def test_compliance_matrix_references_existing_tests(self):
        matrix = (ROOT / "code" / "evaluation" / "compliance_matrix.md").read_text(encoding="utf-8")
        mine = {n for cls in (SampleRegression, LinkingWithoutIdentifiers, TimeTravelAndPolicy, Intent, ParserDialects, Contract, SpecCompliance)
                for n in dir(cls) if n.startswith("test_")}
        for ref in set(re.findall(r"`(test_[a-z0-9_]+)`", matrix)):
            self.assertIn(ref, mine, f"matrix references unknown test {ref}")


if __name__ == "__main__":
    unittest.main()
