"""confidence_v2: a per-case PRIORITY signal for human review, in [0, 1].

It is NOT a calibrated probability. Weights are fixed by hand from what each linking signal means (they were not fitted
to data), and the public samples hold only 3 wrong rows out of 32, far too few to calibrate anything. See docs/CONFIDENCE.md.

Per resource the reliability of how it was linked to the subject is
  * 1/k  when the account id was picked among k equally plausible same-day candidates (same quantity as `confidence`)
  * 0.995 direct identifier match in the evidence (e-mail, hash, phone, user id)
  * 0.99  data-catalog / LINK_RESOURCE_IDENTITY row
  * 0.97  anything indirect (closed-world elimination, event clock, identifier history)
multiplied by 0.93 when the state at audit_at is inferred (no evidence captured before audit_at).
The case value is the product over its resources. Notes override it: "not resolvable", "degraded input" and
"audit failed" -> 0; a withheld pick -> at most 0.25. Requests that do not authorise deletion (no resources) get 0.97,
the reliability of the intent classifier alone.
"""
from __future__ import annotations

import re
from typing import List, Tuple

W_DIRECT, W_CATALOG, W_INDIRECT, W_INFERRED, W_INTENT_ONLY, CAP_WITHHELD = 0.995, 0.99, 0.97, 0.93, 0.97, 0.25


def _pick(linked_by: str, notes: List[str], rid: str) -> float:
    for n in notes:
        m = re.match(rf"account {re.escape(rid)} chosen among (\d+)", n)
        if m:
            return 1.0 / int(m.group(1))
    return 0.5 if "ambiguous" in linked_by else 1.0


def score(t: dict) -> Tuple[float, List[str]]:
    """(confidence_v2, reasons) for one audit-trail record (ghost.audit.explain output)."""
    notes = t.get("notes", [])
    if "resources" not in t:
        p, why = W_INTENT_ONLY, ["no deletion authorised: intent classification only (x0.97)"]
    else:
        p, why = 1.0, []
        for r in t["resources"]:
            rid, lb = r["resource"], r["linked_by"]
            q = _pick(lb, notes, rid)
            if q < 1:
                why.append(f"{rid}: tie-break pick ({lb}), x{q:.3f}")
            elif "identifier match" in lb:
                q = W_DIRECT
            elif "catalog" in lb or "LINK" in lb.upper():
                q = W_CATALOG
            else:
                q = W_INDIRECT
                why.append(f"{rid}: indirect link ({lb}), x{W_INDIRECT}")
            if r["proof"] == "inferred":
                q *= W_INFERRED
                why.append(f"{rid}: state inferred, no evidence before audit_at, x{W_INFERRED}")
            p *= q
    for n in notes:
        if "not resolvable" in n or n.startswith(("degraded", "audit failed")):
            p = 0.0
            why.append(n + " -> 0")
        elif "withheld" in n:
            p = min(p, CAP_WITHHELD)
            why.append(n + f" -> at most {CAP_WITHHELD}")
    return round(max(0.0, min(1.0, p)), 4), why
