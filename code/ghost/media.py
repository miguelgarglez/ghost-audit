"""PDF / screenshot evidence: text extraction (cached OCR) and conversion to observations."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

from . import model as M
from .evidence import idents_of

# OCR text cache for screenshots/PDFs. Shipped only in the hackathon code.zip (it transcribes challenge images);
# published packages omit it. GHOST_OCR_CACHE points at another cache directory.
CACHE_DIR = Path(os.environ.get("GHOST_OCR_CACHE") or Path(__file__).resolve().parent / "ocr_cache")

RID_RE = re.compile(r"\b(account|search|support|analytics|export|data_flow|backup|audit_record|tombstone|marketing|object|legal_hold)_(\d+)\b")


def _ocr_png(p: Path) -> str:
    from PIL import Image  # type: ignore

    texts = []
    img = Image.open(p).convert("L")
    big = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "x.png"
        big.save(src)
        for psm in ("6", "11"):
            r = subprocess.run(["tesseract", str(src), "stdout", "--psm", psm], capture_output=True, text=True)
            texts.append(r.stdout)
    return "\n".join(texts)


def extract_text(p: Path, use_cache: bool = True) -> str:
    cached = CACHE_DIR / (p.name + ".txt")
    if use_cache and cached.exists():
        return cached.read_text(encoding="utf-8")
    if p.suffix.lower() == ".pdf":
        if not shutil.which("pdftotext"):
            return ""
        return subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True).stdout
    if p.suffix.lower() == ".png":
        if not shutil.which("tesseract"):
            return ""
        try:
            return _ocr_png(p)
        except Exception:
            return ""
    return ""


# ----------------------------------------------------------------------------------------------------------
# page understanding
# ----------------------------------------------------------------------------------------------------------
_MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}


def _clean(text: str) -> str:
    """Undo the OCR artefacts seen in practice (a stray slash/space inside an identifier)."""
    text = re.sub(r"(?<=\d)[/|](?=\d)", "", text)
    text = re.sub(r"(\b(?:account|search|support|analytics|export|backup|tombstone|marketing|object)_)\s+(?=\d)", r"\1", text)
    return text


def _page_type(text: str, name: str) -> str:
    t = text.lower()
    rules = [
        ("search", ("customer search", "search console")),
        ("connector", ("bridgeops", "topology")),
        ("analytics", ("pulse analytics",)),
        ("backup", ("vault recovery", "customer-platform archives")),
        ("export_console", ("customer download center", "data exports")),
        ("support_console", ("helpline console",)),
        ("registry", ("customer reference registry", "customer key")),
        ("object_storage", ("nimbus object storage",)),
        ("audit_console", ("sentinel", "account events")),
        ("marketing", ("audience operations",)),
        ("account_console", ("customer operations",)),
        ("statement", ("customer statement",)),
        ("attachment_pdf", ("delivery address correction",)),
        ("audit_pdf", ("compliance journal",)),
        ("legal_pdf", ("disputed orders",)),
    ]
    for kind, keys in rules:
        if any(k in t for k in keys):
            return kind
    return "unknown"


def _expires(text: str, rid: str) -> str:
    m = re.search(rf"{rid}\b.{{0,160}}?Expires\s+(\d{{1,2}})\s+(\w{{3}})\s+(\d{{4}})\s+(\d\d):(\d\d)", text, re.S)
    if not m:
        return ""
    d, mon, y, hh, mm = int(m.group(1)), _MONTHS.get(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
    return f"{y:04d}-{mon:02d}-{d:02d}T{hh:02d}:{mm:02d}:00Z" if mon else ""


def parse_media(fid: str, cap: str, path: Path) -> List[M.Obs]:
    text = _clean(extract_text(path))
    if not text.strip():
        from . import diag
        diag.note("unparsed_files", file_id=fid, path=path.name,
                  error="no text: no OCR cache entry and no pdftotext/tesseract result (set GHOST_OCR_CACHE or install OCR tools)")
        return []
    page = _page_type(text, path.name)
    has_person = bool(idents_of(text) - {i for i in idents_of(text) if i.startswith("user_")})
    out: List[M.Obs] = []
    # single-subject documents (statements, attachments) name their owner; list consoles do not
    page_idents = idents_of(text) if page in ("attachment_pdf", "statement") else frozenset()

    def add(rid, kind, state, system=None, **detail):
        from .evidence import system_of

        out.append(M.Obs(fid, cap, cap, system or system_of(rid), rid, kind, state, page_idents, "media:" + page, "", {k: v for k, v in detail.items() if v}))

    rids = sorted({m.group(0) for m in RID_RE.finditer(text)})
    for rid in rids:
        prefix = rid.rsplit("_", 1)[0]
        kind, system = M.PREFIX_KIND[prefix]
        if prefix == "account":
            add(rid, kind, M.PRESENT)
        elif prefix == "search":
            add(rid, kind, M.PRESENT)
        elif prefix == "support":
            if page == "attachment_pdf":
                add(rid, M.SUPPORT_ATTACHMENT, M.PRESENT)
            else:
                add(rid, kind, M.IDENTIFIABLE if has_person else M.REDACTED)
        elif prefix == "analytics":
            add(rid, kind, M.IDENTIFIABLE if has_person else M.ANONYMIZED)
        elif prefix == "export":
            add(rid, kind, M.PRESENT, valid_until=_expires(text, rid))
        elif prefix == "data_flow":
            add(rid, kind, M.DISABLED if re.search(r"disabled|paused|stopped", text, re.I) else M.ACTIVE)
        elif prefix == "backup":
            add(rid, kind, M.BLOCKED if re.search(r"restore (blocked|denied)|blocked", text, re.I) else M.ACTIVE)
        elif prefix == "audit_record":
            add(rid, kind, M.PRESENT)
        elif prefix == "marketing":
            add(rid, kind, M.PRESENT)
    # artefacts that are named rather than id-tagged
    for m in re.finditer(r"delivery_confirmation_(\d+)\.pdf", text):
        add(f"object_{m.group(1)}", M.SUPPORT_ATTACHMENT, M.PRESENT, "object_storage")
    for m in re.finditer(r"NST-\d{4}-\d{4}-(\d+)", text):
        add(f"export_{int(m.group(1))}", M.USER_EXPORT, M.PRESENT, "export_service")
    if page == "registry":
        for m in re.finditer(r"\buser_(\d+)\b", text):
            add(f"tombstone_{m.group(1)}", M.ACCOUNT_TOMBSTONE, M.IDENTIFIABLE if has_person else M.REDACTED, "identity_service")
            break
    return out
