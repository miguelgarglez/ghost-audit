#!/usr/bin/env python3
"""Ghost Audit - agent-friendly, read-only CLI over the deletion audit.

    python3 code/ghost_cli.py describe | summary | cases [filters] | case ID | explain ID | evidence ID
                              | queue | ingest-report | whatif [--min-pick-confidence X] [--min-confidence-v2 Y] | verify [--path output.csv]

Output is JSON by default when stdout is not a TTY (markdown on a TTY); override with --format json|md|jsonl.
Global options: --dataset DIR (default ./dataset, or GHOST_DATASET) and --ocr-cache DIR (or GHOST_OCR_CACHE).
``ghost-audit demo [--dir DIR]`` writes and audits a bundled synthetic dataset.
Exit codes: 0 ok, 1 verification failed, 2 usage error, 3 not found. Errors are JSON: {"error", "hint"}.
``python3 code/main.py`` (which writes output.csv) is unaffected; this tool never writes anything.
"""
from __future__ import annotations

import argparse
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

from ghost.agent import Ghost, GhostError  # noqa: E402


def to_md(obj, title: str = "") -> str:
    if isinstance(obj, str):
        return obj
    lines = [f"## {title}"] if title else []
    for k, v in obj.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            cols = list(v[0].keys())
            lines += [f"### {k}", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
            lines += ["| " + " | ".join(str(r.get(c, "")).replace("|", "/") if not isinstance(r.get(c), (list, dict))
                                       else json.dumps(r.get(c), ensure_ascii=False).replace("|", "/") for c in cols) + " |" for r in v]
        elif isinstance(v, (dict, list)):
            lines.append(f"- **{k}**: `{json.dumps(v, ensure_ascii=False)}`")
        else:
            lines.append(f"- **{k}**: {v}")
    return "\n".join(lines)


def emit(obj, fmt: str, title: str = "") -> None:
    if fmt == "md":
        print(to_md(obj, title))
    elif fmt == "jsonl":
        items = obj if isinstance(obj, list) else next((v for v in obj.values() if isinstance(v, list)), [obj]) if isinstance(obj, dict) else [obj]
        for it in items:
            print(json.dumps(it, ensure_ascii=False, sort_keys=False))
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--format", choices=["json", "md", "jsonl"], default=None)
    common.add_argument("--json", action="store_const", const="json", dest="format", help="shorthand for --format json")
    ap = argparse.ArgumentParser(prog="ghost-audit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("describe", parents=[common], help="capabilities, glossary, limits")
    sub.add_parser("demo", help="write and audit the bundled synthetic demo dataset (options: --dir DIR, --format)")
    p = sub.add_parser("summary", parents=[common], help="status distribution and risks")
    p.add_argument("--include-samples", action="store_true")
    p = sub.add_parser("cases", parents=[common], help="filter cases")
    p.add_argument("--status"); p.add_argument("--claimed"); p.add_argument("--risk", metavar="KIND")
    p.add_argument("--needs-review", action="store_true"); p.add_argument("--q", metavar="TEXT")
    p.add_argument("--limit", type=int, default=50); p.add_argument("--include-samples", action="store_true")
    for name in ("case", "explain"):
        p = sub.add_parser(name, parents=[common]); p.add_argument("case_id"); p.add_argument("--mask", action="store_true")
    p = sub.add_parser("evidence", parents=[common]); p.add_argument("evidence_id")
    p.add_argument("--lines", type=int, default=40); p.add_argument("--grep", metavar="TEXT")
    p = sub.add_parser("queue", parents=[common]); p.add_argument("--limit", type=int, default=25); p.add_argument("--include-samples", action="store_true")
    p.add_argument("--sort", choices=["priority", "confidence_v2"], default="priority", help="confidence_v2 = least certain first")
    p = sub.add_parser("whatif", parents=[common]); p.add_argument("--min-pick-confidence", type=float)
    p.add_argument("--min-confidence-v2", type=float, help="list cases below this confidence_v2 (routing simulation, no re-run)")
    p = sub.add_parser("verify", parents=[common]); p.add_argument("--path")
    sub.add_parser("ingest-report", parents=[common], help="files/rows/cases skipped or degraded during ingestion")
    return ap


def demo(argv) -> int:
    """Write the bundled synthetic dataset (fictional company, see ghost/demo.py) and audit it."""
    import os
    import subprocess
    import tempfile

    from ghost import demo as D

    dp = argparse.ArgumentParser(prog="ghost-audit demo", description=demo.__doc__)
    dp.add_argument("--dir", help="where to write the demo dataset (default: a temporary directory, removed afterwards)")
    dp.add_argument("--format", choices=["json", "md"], default=None)
    try:
        a = dp.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    fmt = a.format or ("md" if sys.stdout.isatty() else "json")
    with tempfile.TemporaryDirectory() as td:
        target = D.write(Path(a.dir) if a.dir else Path(td) / "ghost-audit-demo")
        env = dict(os.environ, GHOST_DATASET=str(target.resolve()), PYTHONIOENCODING="utf-8")
        env.setdefault("GHOST_OCR_CACHE", str(target / "ocr_cache"))  # demo never uses the hackathon OCR cache
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "cases", "--format", "json"],
                           env=env, capture_output=True, text=True)
        if r.returncode:
            sys.stderr.write(r.stderr)
            return r.returncode
        cases = json.loads(r.stdout)["cases"]
    keep = ("case_id", "claimed_status", "verified_status", "steps", "risks", "confidence_v2", "needs_manual_review")
    rows = [{k: c.get(k) for k in keep} for c in cases]
    if fmt == "json":
        print(json.dumps({"dataset": str(target) if a.dir else None, "cases": rows}, indent=2, ensure_ascii=False))
        return 0
    print("Ghost Audit demo - synthetic dataset (fictional company Lumen Lane Books; every case was claimed COMPLETE)\n")
    print(to_md({"cases": rows}))
    if a.dir:
        print(f"\nDataset written to {target}. Explore it with:\n  ghost-audit --dataset {target} explain demo_02\n"
              f"  ghost-audit --dataset {target} queue\n  ghost-audit --dataset {target} ingest-report")
    else:
        print("\nRe-run with `ghost-audit demo --dir ./ghost-demo` to keep the files and explore them "
              "(explain, queue, ingest-report, evidence).")
    return 0


def run(argv, g: Ghost | None = None) -> int:
    if argv and argv[0] == "demo":
        return demo(argv[1:])
    ap = parser()
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    fmt = a.format or ("md" if sys.stdout.isatty() else "json")
    g = g or Ghost()
    try:
        if a.cmd == "describe":
            out = g.describe()
        elif a.cmd == "summary":
            out = g.summary(a.include_samples)
        elif a.cmd == "cases":
            out = g.cases(a.status, a.claimed, a.risk, a.needs_review, a.q, a.limit, a.include_samples)
        elif a.cmd == "case":
            out = g.case(a.case_id, a.mask)
        elif a.cmd == "explain":
            text = g.explain(a.case_id, a.mask)
            out = text if fmt == "md" else {"case_id": a.case_id, "markdown": text}
        elif a.cmd == "evidence":
            out = g.evidence(a.evidence_id, a.lines, a.grep)
        elif a.cmd == "queue":
            out = g.queue(a.limit, a.include_samples, a.sort)
        elif a.cmd == "ingest-report":
            out = g.ingest_report()
        elif a.cmd == "whatif":
            out = g.whatif(a.min_pick_confidence, a.min_confidence_v2)
        else:
            out = g.verify(a.path)
    except GhostError as e:
        print(json.dumps(e.as_dict(), ensure_ascii=False))
        return e.code
    emit(out, fmt, a.cmd)
    return 1 if a.cmd == "verify" and not out["ok"] else 0


def main() -> int:
    """Console-script entry point (``ghost-audit``)."""
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
