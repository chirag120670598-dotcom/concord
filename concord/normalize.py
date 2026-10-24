"""Invoice number and GSTIN normalisation.

The single most common reason a reconciliation fails is not a tax disagreement.
It is that the supplier wrote `GST/2025/145` in GSTR-1 and the client's
accountant wrote `INV-145` in the books. Both are the same invoice. A VLOOKUP
cannot see that; this module can.

Three keys are produced per invoice, in increasing order of risk:

    exact     GST/2025/145     everything but alphanumerics stripped
    digits    145              the trailing digit run
    loose     2025145          every digit joined

Matching on `digits` alone is dangerous -- plenty of invoices are numbered
"145" -- so the engine only accepts a digits match when the amounts also agree.
That pairing is the whole trick.
"""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_DIGIT_RUN = re.compile(r"\d+")


def norm_invoice(raw: str | None) -> str:
    """`GST/2025/145` -> `GST2025145`. Empty string for nothing usable."""
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", str(raw)).upper()
    return _NON_ALNUM.sub("", text)


def digit_key(raw: str | None) -> str:
    """The trailing digit run with leading zeros dropped: `INV-00145` -> `145`.

    Trailing rather than first because invoice series usually put the sequence
    number last (`GST/2025/145`) or first after a prefix (`145/GST/2025` is
    rarer). Taking the last run handles the common shape without guessing.
    """
    if not raw:
        return ""
    runs = _DIGIT_RUN.findall(unicodedata.normalize("NFKC", str(raw)))
    if not runs:
        return ""
    return runs[-1].lstrip("0") or "0"


def loose_key(raw: str | None) -> str:
    """Every digit joined: `GST/2025/145` -> `2025145`."""
    if not raw:
        return ""
    return "".join(_DIGIT_RUN.findall(unicodedata.normalize("NFKC", str(raw))))


def norm_gstin(raw: str | None) -> str:
    """GSTINs arrive with stray spaces, lower case and trailing punctuation."""
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", str(raw)).upper()
    return _NON_ALNUM.sub("", text)


def gstin_looks_valid(gstin: str) -> bool:
    """Structural check only: 2 digits, 10 char PAN, entity, Z, checksum.

    This is a sanity check on shape, not a checksum verification. A GSTIN that
    fails this is almost always a typo'd or truncated cell, and flagging it is
    more useful than silently treating it as a different supplier.
    """
    return bool(re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]", gstin or ""))


def similarity(a: str, b: str) -> float:
    """Token-free similarity of two normalised invoice numbers, 0.0 to 1.0."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio()


def vendor_slug(name: str | None, gstin: str) -> str:
    """A stable, human-readable key for the memory file."""
    base = _NON_ALNUM.sub("", (name or "").upper())[:24]
    return f"{base or 'supplier'}@{gstin[-6:] if gstin else 'unknown'}"
