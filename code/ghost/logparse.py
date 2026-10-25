"""Tolerant parser for the heterogeneous service-log formats found in evidence/logs."""
from __future__ import annotations

import json
import re
from typing import Dict, Iterator

_KV = re.compile(r'([A-Za-z_][\w.\-]*)=("(?:[^"\\]|\\.)*"|\S*)')
_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s+(.*)$")


def _unq(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def parse_kv(text: str) -> Dict[str, str]:
    return {k: _unq(v) for k, v in _KV.findall(text)}


def parse_line(line: str) -> Dict[str, str] | None:
    line = line.rstrip("\n")
    m = _TS.match(line)
    if not m:
        return None
    ts, rest = m.group(1), m.group(2)
    rec: Dict[str, str] = {"ts": ts}
    if rest.startswith("{"):
        try:
            d = json.loads(rest)
        except ValueError:
            return None
        rec.update({k: str(v) for k, v in d.items()})
        if "severity" in rec:
            rec["level"] = rec.pop("severity")
        return rec
    if rest.startswith("["):
        mm = re.match(r"\[(\w+)\]\s+\[([^\]]+)\]\s+\[([^\]]+)\]\s+([\w]+):\s*(.*)$", rest)
        if mm:
            rec.update(level=mm.group(1), service=mm.group(2), trace_id=mm.group(3), event=mm.group(4))
            rec.update(parse_kv(mm.group(5)))
            return rec
    if rest.startswith("<"):
        mm = re.match(r"<\d+>1\s+(\S+)\s+(\S+)\s+(\S+)\s+-\s+-\s+(.*)$", rest)
        if mm:
            rec.update(node=mm.group(1), service=mm.group(2), trace_id=mm.group(3))
            rec.update(parse_kv(mm.group(4)))
            return rec
    if rest.startswith("requestId="):
        mm = re.match(r'requestId=(\S+)\s+level=(\S+)\s+function=(\S+)\s+message="(.*)"\s*$', rest)
        if mm:
            rec.update(trace_id=mm.group(1), level=mm.group(2), service=mm.group(3))
            inner = mm.group(4).replace('\\"', '"')
            rec.update(parse_kv(inner))
            return rec
    if rest.startswith("| "):
        parts = rest[2:].split(" | ", 4)
        if len(parts) == 5:
            rec.update(level=parts[0], service=parts[1], trace_id=parts[2], event=parts[3])
            rec.update(parse_kv(parts[4]))
            return rec
    rec.update(parse_kv(rest))
    return rec


def parse_log(text: str) -> Iterator[Dict[str, str]]:
    for ln in text.splitlines():
        r = parse_line(ln)
        if r is not None:
            yield r
