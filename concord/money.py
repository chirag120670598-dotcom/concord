"""Money as integer paise.

Every amount in this project is an int counting paise. GST reconciliation is
subtraction-heavy: the whole output is "this much credit is at risk". Float
drift of one paisa across 4,000 invoices is how a CA stops trusting a tool, so
floats never touch an amount. Parsing goes through Decimal, never float().
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

PAISE = 100


def parse_amount(value) -> int:
    """Parse anything a spreadsheet or JSON might contain into paise.

    Returns 0 for blanks and the usual placeholder junk, because a purchase
    register exported from Tally is full of both and stopping on the first one
    would make the tool useless on real files.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value * PAISE
    if isinstance(value, float):
        # Only reached for JSON numbers. str() first so 1416.0000000001 does
        # not become a different amount from "1416.00".
        return _dec(Decimal(str(value)))
    text = str(value).strip()
    if not text or text in {"-", "--", "N/A", "NA", "nil", "Nil", "NIL"}:
        return 0
    text = text.replace(",", "").replace("\u20b9", "").replace(" ", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    if text.endswith("-"):
        text = "-" + text[:-1]
    amount = _dec(Decimal(text)) if _is_decimal(text) else 0
    return -amount if negative else amount


def _is_decimal(text: str) -> bool:
    try:
        Decimal(text)
        return True
    except InvalidOperation:
        return False


def _dec(d: Decimal) -> int:
    """Decimal rupees -> int paise, rounding half away from zero.

    round() is not used: Python rounds half to even, so round(0.5) == 0 and a
    single paise can silently vanish. That exact bug has bitten this codebase's
    sibling project before.
    """
    scaled = d * PAISE
    sign = -1 if scaled < 0 else 1
    return sign * int((abs(scaled) + Decimal("0.5")).to_integral_value())


def rupees(paise: int) -> Decimal:
    return Decimal(paise) / PAISE


def format_inr(paise: int) -> str:
    """Indian grouping: 14,29,000 not 1,429,000."""
    sign = "-" if paise < 0 else ""
    whole = abs(paise) // PAISE
    frac = abs(paise) % PAISE
    text = str(whole)
    if len(text) > 3:
        head, tail = text[:-3], text[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        text = ",".join(parts) + "," + tail
    return f"{sign}\u20b9{text}" + (f".{frac:02d}" if frac else "")


def format_inr_short(paise: int) -> str:
    """Compact form for summary lines: 14.29L, 1.2Cr."""
    sign = "-" if paise < 0 else ""
    r = Decimal(abs(paise)) / PAISE
    if r >= Decimal("10000000"):
        return f"{sign}\u20b9{r / Decimal('10000000'):.2f}Cr"
    if r >= Decimal("100000"):
        return f"{sign}\u20b9{r / Decimal('100000'):.2f}L"
    return format_inr(paise)
