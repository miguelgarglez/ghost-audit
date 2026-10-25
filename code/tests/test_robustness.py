"""Robustness: the pipeline must never crash on unseen or malformed input, must always write a valid
output.csv, must keep semantics-preserving perturbations byte-identical, and must flag what it cannot
audit (INCOMPLETE + needs_manual_review, never invented ids). Scenarios live in code/tools/stress.py;
run them all with `python3 code/tools/stress.py` (29 scenarios incl. a 10x-volume run, ~40 s)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT / "code" / "tools"))

HAVE_DATASET = (ROOT / "dataset" / "deletion_requests.csv").exists()


@unittest.skipUnless(HAVE_DATASET, "needs dataset/ next to README.md")
class AdversarialInputs(unittest.TestCase):
    """Every scenario except the slow 10x-volume one (covered by stress.py)."""

    def test_all_scenarios_survive(self):
        import stress

        names = [n for n in stress.S if n != "ten_x_cases"]
        self.assertGreaterEqual(len(names), 28)
        self.assertEqual(stress.main(["--only", *names, "--json"]), 0)


class TolerantPrimitives(unittest.TestCase):
    def test_timestamps(self):
        from ghost.timeutil import canon

        self.assertEqual(canon("2026-07-19T13:00:00Z"), "2026-07-19T13:00:00Z")  # canonical is unchanged
        self.assertEqual(canon("2026-07-19T15:00:00+02:00"), "2026-07-19T13:00:00Z")
        self.assertEqual(canon("2026-07-19 13:00:00"), "2026-07-19T13:00:00Z")
        self.assertIsNone(canon("next tuesday"))
        self.assertIsNone(canon(""))

    def test_plan_never_claims_complete_without_proof(self):
        from ghost import model as M
        from ghost.audit import Auditor, CaseWork

        a = Auditor.__new__(Auditor)
        cw = CaseWork(M.Case("case_x", "a@b.co", "", "COMPLETE", "", "", "delete my account"), True, degraded=True)
        self.assertEqual(a.plan(cw), ("INCOMPLETE", []))

    def test_identity_hints_do_not_merge(self):
        from ghost.audit import _identity_hint

        class W:
            class dir:
                @staticmethod
                def resolve(v):
                    return {"user_01"} if v == "ana@x.com" else set()
        self.assertIn("candidate user_01", _identity_hint(W, "ana+gdpr@x.com"))
        self.assertIn("homoglyph", _identity_hint(W, "\\u0430na@x.com".encode().decode("unicode_escape")))


if __name__ == "__main__":
    unittest.main()
