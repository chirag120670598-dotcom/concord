"""The matching engine.

Four passes, cheapest first, each narrower than the last. A pair is never taken
by two passes, so the confidence attached to a match means something.

    1. exact       same GSTIN, same invoice number after stripping punctuation
    2. memory      a pair this firm confirmed in an earlier month
    3. numbered    same GSTIN, same trailing digits, amounts agree
    4. fuzzy       same GSTIN, invoice numbers similar, amounts agree closely
    5. dated       same GSTIN, same amount, invoice dates within a few days

Pass 3 is the one that needs care. `145` alone is a weak key -- thousands of
invoices are numbered 145. It is only accepted when the amounts also agree
within tolerance, which drops the false-positive rate to near zero without
losing the `GST/2025/145` vs `INV-145` case that breaks every VLOOKUP.

Nothing is silently matched. Every pair carries the pass that found it and the
report prints it, because a CA signing off on a reconciliation needs to know
which rows were judgement calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Doc
from .money import format_inr
from .normalize import digit_key, loose_key, norm_invoice, similarity

EXACT = "exact"
MEMORY = "memory"
NUMBERED = "numbered"
FUZZY = "fuzzy"
DATED = "dated"

ONLY_2B = "only_2b"
ONLY_BOOKS = "only_books"
VALUE_MISMATCH = "value_mismatch"
BLOCKED = "blocked"


@dataclass
class Match:
    kind: str
    two_b: Doc | None
    book: Doc | None
    note: str = ""
    confidence: float = 1.0

    @property
    def gstin(self) -> str:
        return (self.two_b or self.book).gstin  # type: ignore[union-attr]

    @property
    def vendor(self) -> str:
        for d in (self.two_b, self.book):
            if d and d.vendor:
                return d.vendor
        return self.gstin

    @property
    def tax_gap(self) -> int:
        """Tax claimed in the books that GSTR-2B does not support."""
        if self.book is None:
            return 0
        if self.two_b is None:
            return self.book.total_tax if self.book.claimable else 0
        return self.book.total_tax - self.two_b.total_tax

    @property
    def risk(self) -> int:
        """Rupees of credit this row puts in doubt.

        Three different shapes, one question -- how much would the department
        disallow. Claimed in the books with nothing in 2B: the whole amount.
        Credit blocked by the portal: the whole amount, not the difference.
        A value difference: only the excess. Credit sitting unclaimed in 2B is
        not a risk at all, it is money left on the table.
        """
        if self.book is None:
            return 0
        if self.two_b is None:
            return max(0, self.book.total_tax)
        if not self.two_b.itc_available:
            return abs(self.book.total_tax)
        return max(0, self.book.total_tax - self.two_b.total_tax)

    @property
    def at_risk(self) -> int:
        """Alias kept for callers that predate `risk`."""
        return self.risk


@dataclass
class Result:
    matches: list[Match] = field(default_factory=list)
    only_2b: list[Match] = field(default_factory=list)
    only_books: list[Match] = field(default_factory=list)
    mismatched: list[Match] = field(default_factory=list)
    blocked: list[Doc] = field(default_factory=list)
    # Matched documents that the portal says carry no available credit. The
    # books claimed them anyway. This is not a mismatch, it is an exposure --
    # every rupee is at risk, not just a difference -- so it is counted
    # separately rather than folded into the mismatch bucket.
    blocked_claims: list[Match] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def matched_count(self) -> int:
        return len(self.matches)

    @property
    def itc_at_risk(self) -> int:
        return sum(m.risk for m in self.only_books + self.mismatched + self.blocked_claims)

    @property
    def unclaimed_itc(self) -> int:
        """Credit in GSTR-2B that the books never claimed."""
        return sum(abs(m.two_b.total_tax) for m in self.only_2b if m.two_b)

    @property
    def match_rate(self) -> float:
        """Clean matches as a fraction of every document that needed a look.

        The denominator deliberately includes mismatches, blocked credits and
        both sides of the exceptions. Leaving those out is the easy way to
        report 92% on a file where a fifth of the credit is in doubt, and a
        number that flatters itself is worse than no number at all.
        """
        total = (self.matched_count + len(self.mismatched)
                 + len(self.blocked_claims) + len(self.only_2b) + len(self.only_books))
        return self.matched_count / total if total else 0.0


def _close(a: int, b: int, rel: float, absolute: int) -> bool:
    gap = abs(a - b)
    if gap <= absolute:
        return True
    base = max(abs(a), abs(b), 1)
    return gap / base <= rel


def reconcile(
    two_b: list[Doc],
    books: list[Doc],
    *,
    value_tolerance: float = 0.005,
    absolute_tolerance: int = 100,     # paise: Re 1
    date_window: int = 7,
    fuzzy_threshold: float = 0.82,
    memory: dict | None = None,
) -> Result:
    """Reconcile GSTR-2B against the purchase register."""
    result = Result()
    confirmed = (memory or {}).get("confirmed", {})

    # Credit notes and invoices are matched inside their own group, so a credit
    # note can never be "matched" against an invoice. The group is the sign of
    # the money itself rather than a parser's flag: GSTR-2B marks a credit note
    # with typ "C" and positive amounts, while a register writes the same note
    # as negative amounts. Both mean -2,700 and both must land in one group.
    def group_of(doc: Doc) -> int:
        return 1 if doc.total_tax >= 0 else -1

    by_sign_2b: dict[int, list[Doc]] = {}
    by_sign_books: dict[int, list[Doc]] = {}
    for d in two_b:
        by_sign_2b.setdefault(group_of(d), []).append(d)
    for d in books:
        by_sign_books.setdefault(group_of(d), []).append(d)

    for sign in sorted(set(by_sign_2b) | set(by_sign_books)):
        pending_2b = list(by_sign_2b.get(sign, []))
        pending_books = list(by_sign_books.get(sign, []))
        taken_2b: set[int] = set()
        taken_books: set[int] = set()

        def remaining(side: list[Doc], taken: set[int]) -> list[Doc]:
            return [d for i, d in enumerate(side) if i not in taken]

        def pair(i: int, j: int, kind: str, note: str, conf: float) -> None:
            taken_2b.add(i)
            taken_books.add(j)
            a, b = pending_2b[i], pending_books[j]
            gap = b.total_tax - a.total_tax
            if kind in (EXACT, MEMORY) and not _close(a.total_tax, b.total_tax,
                                                       value_tolerance, absolute_tolerance):
                result.mismatched.append(Match(
                    VALUE_MISMATCH, a, b,
                    note=(f"same document, tax differs by {format_inr(gap)} "
                          f"(2B {format_inr(a.total_tax)}, books {format_inr(b.total_tax)})"),
                    confidence=conf,
                ))
            else:
                result.matches.append(Match(kind, a, b, note=note, confidence=conf))

        # ---- pass 1: exact ----------------------------------------------
        index_2b: dict[str, list[int]] = {}
        for i, d in enumerate(pending_2b):
            index_2b.setdefault(norm_invoice(d.invoice_no), []).append(i)

        for j, b in enumerate(pending_books):
            if j in taken_books:
                continue
            key = norm_invoice(b.invoice_no)
            for i in index_2b.get(key, []):
                if i in taken_2b:
                    continue
                if pending_2b[i].gstin != b.gstin:
                    continue
                pair(i, j, EXACT, "same invoice number", 1.0)
                break

        # ---- pass 2: memory from earlier months -------------------------
        for j, b in enumerate(pending_books):
            if j in taken_books:
                continue
            remembered = confirmed.get(f"{b.gstin}|{norm_invoice(b.invoice_no)}")
            if not remembered:
                continue
            for i, a in enumerate(pending_2b):
                if i in taken_2b:
                    continue
                if a.gstin == b.gstin and norm_invoice(a.invoice_no) == remembered:
                    pair(i, j, MEMORY,
                         "matched a pair this firm confirmed in an earlier month",
                         0.98)
                    break

        # ---- pass 3: same trailing digits and same money ----------------
        digits_2b: dict[tuple[str, str], list[int]] = {}
        for i, d in enumerate(pending_2b):
            digits_2b.setdefault((d.gstin, digit_key(d.invoice_no)), []).append(i)

        for j, b in enumerate(pending_books):
            if j in taken_books:
                continue
            for i in digits_2b.get((b.gstin, digit_key(b.invoice_no)), []):
                if i in taken_2b:
                    continue
                a = pending_2b[i]
                if not _close(a.total_tax, b.total_tax, value_tolerance, absolute_tolerance):
                    continue
                pair(i, j, NUMBERED,
                     f"invoice numbers differ ({a.invoice_no} vs {b.invoice_no}) "
                     f"but the amount agrees", 0.9)
                break

        # ---- pass 4: similar numbers, close money -----------------------
        for j, b in enumerate(pending_books):
            if j in taken_books:
                continue
            best_i, best_score = None, 0.0
            for i, a in enumerate(pending_2b):
                if i in taken_2b or a.gstin != b.gstin:
                    continue
                if not _close(a.total_tax, b.total_tax, value_tolerance, absolute_tolerance):
                    continue
                score = similarity(norm_invoice(a.invoice_no), norm_invoice(b.invoice_no))
                if score > best_score:
                    best_i, best_score = i, score
            if best_i is not None and best_score >= fuzzy_threshold:
                pair(best_i, j, FUZZY,
                     f"invoice numbers {best_score:.0%} similar, amount agrees",
                     round(best_score, 2))

        # ---- pass 5: same money, dates close ----------------------------
        for j, b in enumerate(pending_books):
            if j in taken_books:
                continue
            for i, a in enumerate(pending_2b):
                if i in taken_2b or a.gstin != b.gstin:
                    continue
                if not _close(a.total_tax, b.total_tax, 0.0, absolute_tolerance):
                    continue
                if a.invoice_date and b.invoice_date:
                    gap = abs((a.invoice_date - b.invoice_date).days)
                    if gap > date_window:
                        continue
                pair(i, j, DATED,
                     f"same amount, invoice dates {a.invoice_date} / {b.invoice_date}",
                     0.75)
                break

        # ---- leftovers ---------------------------------------------------
        for i, a in enumerate(pending_2b):
            if i in taken_2b:
                continue
            if not a.itc_available:
                result.blocked.append(a)
            result.only_2b.append(Match(ONLY_2B, a, None,
                                        note="in GSTR-2B, not recorded in the books"))
        for j, b in enumerate(pending_books):
            if j in taken_books:
                continue
            result.only_books.append(Match(ONLY_BOOKS, None, b,
                                           note="claimed in the books, absent from GSTR-2B"))

    # A matched pair whose credit the portal has blocked is pulled out of the
    # match list and reported as an exposure.
    still_matched = []
    for m in result.matches:
        if m.two_b is not None and not m.two_b.itc_available and m.book is not None:
            m.note = "credit blocked in GSTR-2B but claimed in the books"
            result.blocked_claims.append(m)
        else:
            still_matched.append(m)
    result.matches = still_matched

    result.blocked_claims.sort(key=lambda m: abs(m.book.total_tax), reverse=True)  # type: ignore[union-attr]
    result.only_books.sort(key=lambda m: abs(m.book.total_tax), reverse=True)  # type: ignore[union-attr]
    result.only_2b.sort(key=lambda m: abs(m.two_b.total_tax), reverse=True)    # type: ignore[union-attr]
    result.mismatched.sort(key=lambda m: abs(m.tax_gap), reverse=True)

    result.stats = {
        "2b_documents": len(two_b),
        "book_documents": len(books),
        "matched": len(result.matches),
        "only_2b": len(result.only_2b),
        "only_books": len(result.only_books),
        "value_mismatch": len(result.mismatched),
        "match_rate": result.match_rate,
        "by_pass": {
            kind: sum(1 for m in result.matches if m.kind == kind)
            for kind in (EXACT, MEMORY, NUMBERED, FUZZY, DATED)
        },
    }
    return result


def learn(result: Result, memory: dict | None = None) -> dict:
    """Carry this month's judgement calls into next month.

    Only pairs found by a pass that had to think -- numbered, fuzzy, dated --
    are remembered. Exact matches need no memory, and remembering a value
    mismatch would bake an error in.
    """
    memory = memory or {}
    confirmed = dict(memory.get("confirmed", {}))
    for m in result.matches:
        if m.kind in (EXACT, MEMORY) or not m.two_b or not m.book:
            continue
        confirmed[f"{m.book.gstin}|{norm_invoice(m.book.invoice_no)}"] = \
            norm_invoice(m.two_b.invoice_no)
    memory["confirmed"] = confirmed
    memory["version"] = 1
    return memory


def vendor_exposure(result: Result) -> list[dict]:
    """Rank suppliers by the credit sitting at risk with them.

    This is the list a CA actually works from: the vendor holding 71,400 of
    credit is the first phone call, not the one holding 400.
    """
    buckets: dict[str, dict] = {}
    for m in result.only_books + result.mismatched + result.blocked_claims:
        key = m.gstin
        bucket = buckets.setdefault(key, {
            "gstin": key,
            "vendor": m.vendor,
            "at_risk": 0,
            "documents": 0,
            "reasons": set(),
            "documents_list": [],
        })
        bucket["at_risk"] += m.risk
        bucket["documents"] += 1
        source = m.book or m.two_b
        bucket["documents_list"].append({
            "invoice_no": (m.book.invoice_no if m.book else m.two_b.invoice_no),
            "invoice_date": source.invoice_date.isoformat() if source and source.invoice_date else "",
            "tax": m.risk,
            "reason": ("not filed in GSTR-1" if m.kind == ONLY_BOOKS
                       else "value differs" if m.kind == VALUE_MISMATCH
                       else "credit blocked by portal"),
        })
        bucket["reasons"].add(
            "not filed in GSTR-1" if m.kind == ONLY_BOOKS
            else "value differs" if m.kind == VALUE_MISMATCH
            else "credit blocked by portal")
    rows = list(buckets.values())
    for r in rows:
        r["reasons"] = sorted(r["reasons"])
        r["documents_list"].sort(key=lambda d: d["tax"], reverse=True)
    rows.sort(key=lambda r: r["at_risk"], reverse=True)
    return rows
