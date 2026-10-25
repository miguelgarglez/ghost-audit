#!/usr/bin/env python3
"""Self-supervised accuracy check of the identifier-free linking (activity elimination + shared event clock).

Ground truth without hidden labels: many accounts ARE tied to their person by an identifier in some evidence file.
We hide that link for a random subset (pretend the evidence never carried it), run the real linker, and measure how
often it recovers the true account id. Because only ~half of the accounts are masked per trial, tie groups have the
same size distribution as in the real run. Same for anonymised analytics ids is not possible (they never carry an
identifier), so analytics linking is validated on the public samples instead (see tests).

    python3 code/evaluation/masking_test.py [--trials 5] [--fraction 0.5]
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from ghost.audit import Auditor  # noqa: E402
from ghost.world import World  # noqa: E402


def trial(seed: int, fraction: float) -> tuple[int, int, int]:
    w = World()
    truth = {}
    for c in w.universe:
        u = w.subject_of(c)
        accs = [r for r in w.linked_rids(u) if r.startswith("account_")] if u else []
        if len(accs) == 1:
            truth[c.case_id] = accs[0]
    rng = random.Random(seed)
    masked = {cid: rid for cid, rid in sorted(truth.items()) if rng.random() < fraction}
    # keep only cases that are regular (evidence-log) requests: legacy/screenshot stories have their own timeline
    probe = Auditor(w)
    for rid in set(masked.values()):
        w.rid_users[rid] = set()
    probe.prepare()
    ok = wrong = withheld = 0
    for cid, rid in masked.items():
        cw = probe.work[cid]
        got = [r for r in cw.rids if r.startswith("account_")]
        if not got:
            withheld += 1
        elif got[0] == rid:
            ok += 1
        else:
            wrong += 1
    return ok, wrong, withheld


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--fraction", type=float, default=0.5)
    args = ap.parse_args()
    tot = [0, 0, 0]
    for t in range(args.trials):
        r = trial(t, args.fraction)
        print(f"trial {t}: recovered={r[0]} wrong={r[1]} withheld={r[2]}")
        tot = [a + b for a, b in zip(tot, r)]
    n = sum(tot)
    print(f"overall: recovered {tot[0]}/{n} = {100 * tot[0] / n:.1f}%  wrong {tot[1]}  withheld {tot[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
