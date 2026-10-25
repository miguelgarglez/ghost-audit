"""The bundled synthetic demo (ghost-audit demo) and the packaged-install fallbacks."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]


def _run(args, env_extra=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **(env_extra or {}))
    return subprocess.run([sys.executable, str(CODE / "ghost_cli.py"), *args], capture_output=True, text=True, env=env)


class DemoTest(unittest.TestCase):
    def test_demo_outcomes(self):
        r = _run(["demo", "--format", "json"])
        self.assertEqual(r.returncode, 0, r.stderr)
        got = {c["case_id"]: c for c in json.loads(r.stdout)["cases"]}
        self.assertEqual(got["demo_01"]["verified_status"], "COMPLETE")
        self.assertEqual(got["demo_02"]["verified_status"], "INCOMPLETE")
        self.assertIn("DATA_FLOW", got["demo_02"]["risks"])
        self.assertEqual(got["demo_03"]["verified_status"], "INCOMPLETE")
        self.assertEqual(got["demo_04"]["verified_status"], "NOT_APPLICABLE")
        for cid in ("demo_05", "demo_06"):
            self.assertEqual(got[cid]["verified_status"], "INCOMPLETE")
            self.assertTrue(got[cid]["needs_manual_review"])
            self.assertEqual(got[cid]["confidence_v2"], 0.0)
        self.assertGreater(got["demo_01"]["confidence_v2"], 0.9)

    def test_demo_dir_then_explore_without_ocr_cache(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "demo"
            self.assertEqual(_run(["demo", "--dir", str(d), "--format", "json"]).returncode, 0)
            self.assertTrue((d / "deletion_requests.csv").exists())
            r = _run(["--dataset", str(d), "ingest-report", "--format", "json"], {"GHOST_OCR_CACHE": str(Path(td) / "none")})
            self.assertEqual(r.returncode, 0, r.stderr)
            rep = json.loads(r.stdout)
            self.assertFalse(rep["clean"])
            self.assertTrue(any(f["file_id"] == "evidence_d3" for f in rep["unparsed_files"]))
            r = _run(["--dataset=" + str(d), "explain", "demo_02", "--format", "json"])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("demo_02", r.stdout)

    def test_demo_is_synthetic(self):
        sys.path.insert(0, str(CODE))
        from ghost import demo
        with tempfile.TemporaryDirectory() as td:
            demo.write(Path(td))
            text = (Path(td) / "deletion_requests.csv").read_text(encoding="utf-8")
        self.assertTrue(all(e.endswith("@example.org") for e, _ in demo.CASES.values()))
        self.assertIn("demo_01", text)


if __name__ == "__main__":
    unittest.main()
