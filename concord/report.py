"""Output: a terminal report, a CSV for the file, and vendor chase messages.

The terminal report is ordered by money, not by document. A list of 4,000
unmatched rows sorted by invoice number is not a working document; "Sharma
Traders is holding 71,400 of your client's credit" is.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .match import EXACT, MEMORY, ONLY_2B, ONLY_BOOKS, VALUE_MISMATCH, Result
from .money import format_inr, format_inr_short

RULE = "-" * 68


def summary(result: Result, meta_2b: dict | None = None, meta_books: dict | None = None) -> str:
    s = result.stats
    lines = []
    if meta_2b:
        period = meta_2b.get("period") or "unknown period"
        if len(period) == 6 and period.isdigit():
            # The portal writes MMYYYY (092026 = September 2026).
            period = f"{period[:2]}/{period[2:]}"
        lines.append(f"Concord  ·  GSTR-2B reconciliation")
        lines.append(f"Period {period}  ·  GSTIN {meta_2b.get('gstin') or 'not in file'}")
        lines.append("")

    lines.append(f"  GSTR-2B documents        {s['2b_documents']:>6}")
    lines.append(f"  Purchase register        {s['book_documents']:>6}")
    lines.append("")
    lines.append(f"  Matched                  {s['matched']:>6}   {s['match_rate']:.1%}")
    for kind, label in ((EXACT, "exact invoice number"),
                        (MEMORY, "remembered from last month"),
                        ("numbered", "different number, same amount"),
                        ("fuzzy", "similar number"),
                        ("dated", "same amount and date")):
        count = s["by_pass"].get(kind, 0)
        if count and kind != EXACT:
            lines.append(f"      {label:<32}{count:>6}")
    lines.append(f"  Only in GSTR-2B          {s['only_2b']:>6}")
    lines.append(f"  Only in the books        {s['only_books']:>6}")
    lines.append(f"  Value mismatches         {s['value_mismatch']:>6}")
    if result.blocked:
        lines.append(f"  Credit blocked in 2B     {len(result.blocked):>6}")
    lines.append("")

    risk = result.itc_at_risk
    lines.append(RULE)
    lines.append(f"  ITC at risk              {format_inr(risk):>16}")
    lines.append(f"  not in GSTR-2B           {format_inr_short(result.unclaimed_itc):>16}   (credit not claimed)")
    lines.append(RULE)

    exposure = _vendor_rows(result)
    if exposure and exposure[0]["at_risk"] > 0:
        top = exposure[0]
        lines.append("")
        lines.append(f"  Largest exposure: {top['vendor']}  {format_inr(top['at_risk'])} "
                     f"across {top['documents']} document(s)")
    return "\n".join(lines)


def vendors(result: Result) -> str:
    rows = _vendor_rows(result)
    if not rows:
        return "\nEvery supplier reconciles. Nothing to chase."
    lines = ["", "Chase these suppliers, largest exposure first", RULE]
    for n, r in enumerate(rows, 1):
        lines.append(f"  {n:>2}. {r['vendor'][:34]:<34} {format_inr(r['at_risk']):>13}"
                     f"   {r['documents']} doc{'s' if r['documents'] != 1 else ''}")
        lines.append(f"      {r['gstin']}   {'; '.join(r['reasons'])}")
    lines.append(RULE)
    return "\n".join(lines)


def _vendor_rows(result: Result) -> list[dict]:
    from .match import vendor_exposure

    return vendor_exposure(result)


def detail(result: Result, limit: int = 15) -> str:
    """The rows a CA will actually open, capped so the report stays readable."""
    blocks = []

    if result.only_books:
        blocks.append((f"Claimed in the books, missing from GSTR-2B ", result.only_books))
    if result.mismatched:
        blocks.append(("Same document, different amount ", result.mismatched))
    if result.only_2b:
        blocks.append(("In GSTR-2B, not recorded in the books ", result.only_2b))

    out = []
    for title, rows in blocks:
        out.append("")
        out.append(title + RULE[: 68 - len(title)])
        for m in rows[:limit]:
            doc = m.two_b or m.book
            where = "2B" if m.two_b else "books"
            amount = format_inr(doc.total_tax)
            note = f"   {m.note}" if m.note else ""
            out.append(f"  {m.vendor[:28]:<28} {doc.invoice_no[:18]:<18} "
                       f"{amount:>12}  [{where}]{note}")
        if len(rows) > limit:
            out.append(f"  ... and {len(rows) - limit} more in the CSV")
    return "\n".join(out)


def chase_message(vendor_row: dict, period: str, firm: str = "") -> str:
    """A message a CA can actually paste into WhatsApp.

    Every sentence has to be true for this particular supplier. Telling someone
    "we could not find your invoice" when the invoice is sitting in GSTR-2B with
    the credit blocked makes the CA look like they did not check -- so the
    wording follows the reason, not a template.
    """
    from .money import format_inr

    money = format_inr(vendor_row["at_risk"])
    docs = vendor_row.get("documents_list") or []

    bills = []
    for d in docs:
        bits = [d.get("invoice_no", "")]
        if d.get("invoice_date"):
            bits.append(_pretty_date(d["invoice_date"]))
        if d.get("tax"):
            bits.append(format_inr(d["tax"]) + " tax")
        bills.append("  - " + "  -  ".join(b for b in bits if b))
    bill_block = "\n".join(bills)

    reasons = set(vendor_row.get("reasons") or [])
    if "credit blocked by portal" in reasons:
        body = (
            f"While reconciling GSTR-2B for {period}, your invoices do appear in "
            f"our GSTR-2B, but the portal is showing the input tax credit as NOT "
            f"available against them. Because of this we are unable to claim "
            f"{money}.\n\n"
            "This usually happens when the invoice was reported in a different "
            "period, or has since been amended or cancelled.\n\n"
            "Kindly confirm the status of these invoices, and if they are valid, "
            "re-report them in your GSTR-1 so the credit becomes available to us."
        )
    elif "value differs" in reasons:
        body = (
            f"While reconciling GSTR-2B for {period}, we found your invoices, but "
            f"the amounts do not match our books. As a result {money} of input "
            "tax credit is in disagreement.\n\n"
            "Kindly check the invoice amounts below against your records and tell "
            "us which figure is correct, or amend your GSTR-1 if a credit note or "
            "revision is involved."
        )
    else:
        body = (
            f"While reconciling GSTR-2B for {period}, we could not find the "
            "following invoices from you. Because they are not in GSTR-2B, we "
            f"cannot claim the input tax credit of {money} and it will be paid in "
            "cash this month.\n\n"
            "Kindly file your GSTR-1 for the period so these appear in GSTR-2B, "
            "or send us the correct invoice numbers if we have recorded them wrongly."
        )

    lines = [f"Hello{' ' + firm if firm else ''},", "", body, "",
             f"GSTIN: {vendor_row['gstin']}"]
    if bill_block:
        lines.append(f"Invoices ({len(docs)}):")
        lines.append(bill_block)
    else:
        lines.append(f"Invoices: {vendor_row['documents']}")
    lines += ["", "Thank you."]
    return "\n".join(lines)


def _pretty_date(value: str) -> str:
    """ISO -> DD/MM/YYYY. A supplier reading 2026/08/22 has to stop and work out
    which number is the day."""
    from datetime import date

    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    text = str(value)
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return f"{text[8:10]}/{text[5:7]}/{text[0:4]}"
    return text.replace("-", "/")


def to_csv(result: Result, path: str | Path) -> Path:
    p = Path(path)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "status", "matched_by", "supplier_name", "supplier_gstin",
            "invoice_no_2b", "invoice_no_books", "invoice_date_2b",
            "invoice_date_books", "taxable_2b", "tax_2b", "tax_books",
            "difference", "note",
        ])

        def row(status, match):
            a, b = match.two_b, match.book
            writer.writerow([
                status,
                match.kind,
                match.vendor,
                match.gstin,
                a.invoice_no if a else "",
                b.invoice_no if b else "",
                a.invoice_date.isoformat() if a and a.invoice_date else "",
                b.invoice_date.isoformat() if b and b.invoice_date else "",
                a.taxable / 100 if a else "",
                a.total_tax / 100 if a else "",
                b.total_tax / 100 if b else "",
                match.tax_gap / 100,
                match.note,
            ])

        for m in result.matches:
            row("matched", m)
        for m in result.mismatched:
            row("value_mismatch", m)
        for m in result.only_books:
            row("only_in_books", m)
        for m in result.only_2b:
            row("only_in_2b", m)
    return p


def full_report(result: Result, meta_2b: dict | None = None, limit: int = 15) -> str:
    return "\n".join([
        summary(result, meta_2b),
        vendors(result),
        detail(result, limit),
    ])
