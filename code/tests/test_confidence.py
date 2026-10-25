"""Tests for confidence_v2 (ghost/confidence.py): values, range, monotonicity, notes, and that output.csv never depends on it."""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "dataset" / "deletion_requests.csv").exists():
    raise unittest.SkipTest("needs the HackerRank challenge dataset/ (not redistributed in the public repo); run `ghost-audit demo` or tests/test_demo.py instead")
sys.path.insert(0, str(ROOT / "code"))

from ghost.confidence import score  # noqa: E402

OUTPUT_SHA = "ea64cff4bade32d64fd94d9fcb8d1b040e6a31910265c1c3ac92608b94ec850f"
HAS_DATA = (ROOT / "dataset" / "deletion_requests.csv").exists()


def res(rid="account_1", linked_by="identifier match (email)", proof="evidence_1"):
    return {"resource": rid, "linked_by": linked_by, "proof": proof}


def trail(*resources, notes=()):
    return {"resources": list(resources), "notes": list(notes)}


class ConfidenceUnit(unittest.TestCase):
    def test_weights(self):
        self.assertEqual(score(trail(res()))[0], 0.995)
        self.assertEqual(score(trail(res(linked_by="data catalog row")))[0], 0.99)
        self.assertEqual(score(trail(res(linked_by="LINK_RESOURCE_IDENTITY activity")))[0], 0.99)
        self.assertEqual(score(trail(res(linked_by="event clock")))[0], 0.97)
        self.assertEqual(score(trail(res(proof="inferred")))[0], round(0.995 * 0.93, 4))
        self.assertEqual(score(trail(res(linked_by="elimination (ambiguous: same-day peers)")))[0], 0.5)
        self.assertEqual(score(trail(res(rid="account_9", linked_by="elimination"), notes=["account account_9 chosen among 3 same-day candidates"]))[0],
                         round(1 / 3, 4))
        self.assertEqual(score({"notes": []}), (0.97, ["no deletion authorised: intent classification only (x0.97)"]))

    def test_range_and_monotonic(self):
        base = trail(res(), res("search_1", "data catalog row"))
        v0 = score(base)[0]
        self.assertTrue(0.0 <= v0 <= 1.0)
        more = trail(res(), res("search_1", "data catalog row"), res("ticket_1", "event clock"))
        weaker = trail(res(), res("search_1", "data catalog row", proof="inferred"))
        self.assertLess(score(more)[0], v0)       # every extra resource can only lower it
        self.assertLess(score(weaker)[0], v0)     # inferred state lowers it
        self.assertGreaterEqual(score(trail())[0], v0)
        big = trail(*[res(f"r{i}", "event clock", "inferred") for i in range(200)])
        self.assertTrue(0.0 <= score(big)[0] <= 1.0)

    def test_notes(self):
        self.assertEqual(score(trail(res(), notes=["subject not resolvable: no user"]))[0], 0.0)
        self.assertEqual(score(trail(res(), notes=["degraded input: audit_at unparseable"]))[0], 0.0)
        self.assertEqual(score(trail(notes=["audit failed: boom"]))[0], 0.0)
        v, why = score(trail(res(), notes=["account withheld: 4 candidates"]))
        self.assertEqual(v, 0.25)
        self.assertTrue(any("withheld" in w for w in why))
        self.assertEqual(score(trail(res(), notes=["something else"]))[0], 0.995)


@unittest.skipUnless(HAS_DATA, "needs dataset/")
class ConfidenceOnDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ghost.agent import Ghost
        cls.g = Ghost()

    def test_sample_values_match_experiment(self):
        got = {c: self.g.case(c)["review"]["confidence_v2"] for c in ("case_04", "case_05", "case_09", "case_12")}
        self.assertEqual(got, {"case_04": 0.97, "case_05": 0.995, "case_09": 0.852, "case_12": 0.9851})

    def test_every_case_in_range_and_explained(self):
        rows = self.g.cases(limit=600, include_samples=True)["cases"]
        self.assertEqual(len(rows), 550)
        for r in rows:
            self.assertTrue(0.0 <= r["confidence_v2"] <= 1.0)
            self.assertLessEqual(r["confidence_v2"], max(r["confidence"], 0.995))
        d = self.g.case("case_09")["review"]
        self.assertTrue(d["confidence_reasons"])
        self.assertIn("confidence_v2", self.g.explain("case_09"))

    def test_queue_sort_and_whatif(self):
        q = self.g.queue(limit=600, sort="confidence_v2")["queue"]
        v = [x["confidence_v2"] for x in q]
        self.assertEqual(v, sorted(v))
        w = self.g.whatif(min_confidence_v2=0.9)["confidence_v2_review"]
        self.assertEqual((w["cases"], w["would_review"]), (518, 300))
        self.assertTrue(all(x["confidence_v2"] < 0.9 for x in w["lowest"]))

    def test_output_does_not_depend_on_confidence(self):
        import ghost.confidence as C
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "o.csv"
            env_code = ("import sys,runpy;sys.path.insert(0,%r);import ghost.confidence as C;"
                        "C.score=lambda t:(0.0,[]);C.W_DIRECT=0;sys.argv=['main.py','--out',%r];"
                        "runpy.run_path(%r,run_name='__main__')") % (str(ROOT / "code"), str(out), str(ROOT / "code" / "main.py"))
            subprocess.run([sys.executable, "-c", env_code], check=True, capture_output=True)
            self.assertEqual(hashlib.sha256(out.read_bytes()).hexdigest(), OUTPUT_SHA)
        self.assertEqual(C.W_DIRECT, 0.995)


if __name__ == "__main__":
    unittest.main()
