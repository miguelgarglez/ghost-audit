"""Tolerant timestamp canonicalisation. The pipeline compares timestamps as canonical UTC strings
(``YYYY-MM-DDTHH:MM:SSZ``); canonical input is returned unchanged, other ISO-8601 variants are converted,
anything unparseable yields ``None`` so the caller can degrade the case instead of crashing."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

CANON = "%Y-%m-%dT%H:%M:%SZ"
_CANON_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def canon(ts: Optional[str]) -> Optional[str]:
    if ts is None:
        return None
    s = str(ts).strip()
    if _CANON_RE.match(s):
        return s
    if not s:
        return None
    t = s.replace(" ", "T", 1) if re.match(r"^\d{4}-\d{2}-\d{2} \d", s) else s
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        for f in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
            try:
                d = datetime.strptime(s, f)
                break
            except ValueError:
                d = None
        if d is None:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)  # naive timestamps are taken as UTC (documented assumption)
    return d.astimezone(timezone.utc).strftime(CANON)
