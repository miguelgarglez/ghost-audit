"""Does the request text actually authorise deletion?

Deterministic multilingual classifier. The corpus has templated deletion requests (English, Spanish,
Portuguese, French, Japanese, Korean), "verify / finish my deletion" follow-ups that may never use the word
"delete", and free-form requests for access, correction, export, marketing opt-out or login help that
explicitly say the account must stay. Default is *deletion*; a request is NOT_APPLICABLE only when it carries
an explicit keep/negation signal, is a marketing-only opt-out, or is a pure non-deletion ask with no deletion verb.
The activity log's ``CLASSIFY_REQUEST`` events (privacy intake) are used as an independent cross-check upstream.
"""
from __future__ import annotations

import re

_KEEP = re.compile(
    r"(not asking for (a )?deletion|not (a )?request to (erase|delete)|do not (close|delete|remove|erase)|"
    r"don't (close|delete|remove|erase)|do not want (it |my account |the account )?(closed|deleted)|"
    r"leave (the|my) (account|profile)|keep the account|continue using|still (use|need|want)|"
    r"without (removing|deleting|closing)|never (delete|close))",
    re.I,
)
_MARKETING_ONLY = re.compile(r"(marketing lists only|promotional emails|unsubscribe|opt[- ]?out of (marketing|promotional))", re.I)
_DELETE = re.compile(
    r"(delet|erase|erasure|remov(e|al)|close (my|this|the) account|closed|permanent|"
    r"eliminar|elimina|eliminaci|borrar|supresi|suppression|supprim|exclus[aã]o|excluir|apagar|"
    r"削除|消去|삭제|영구|purge|wipe)",
    re.I,
)
_NON_DELETE_INTENT = re.compile(
    r"(download your|copy of|portable format|what personal information|information request|"
    r"invoices?|misspell|correct it|replace it|change the email|can't access|cannot access|reset|send me a copy)",
    re.I,
)


def authorizes_deletion(text: str) -> bool:
    t = text.strip()
    if _KEEP.search(t):
        return False
    if _MARKETING_ONLY.search(t):
        return False
    if _NON_DELETE_INTENT.search(t) and not _DELETE.search(t):
        return False
    return True
