"""Read a GSTR-2B JSON file downloaded from the GST portal.

Why this file is a tolerant walker rather than a fixed schema read:

The portal's JSON has changed shape more than once, GSTR-2A uses different field
names from GSTR-2B, and the wrapper differs between the "generate JSON" download
and the API. Field codes already diverge in documented ways:

    invoice number      inum          (both)
    invoice date        dt in 2B      idt in 2A and GSTR-1
    item list           items in 2B   itms in 2A
    tax amounts         igst/cgst/sgst/cess in 2B
                        iamt/camt/samt/csamt in 2A
    item wrapper        flat or nested under itm_det

A parser that assumes one of those works on exactly one file. This one reads all
of them, and when it finds nothing it says what it looked for instead of
returning an empty report that looks like a clean month. That distinction --
"no invoices" versus "I could not read your file" -- is the difference between a
tool a CA trusts and one they abandon.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .model import Doc
from .money import parse_amount
from .normalize import norm_gstin

BILLING_KEYS = ("b2b",)
# Credit and debit notes live in their own sections in GSTR-2B. Inside `b2b`,
# `typ` is the *invoice* type, where "R" means Regular -- not "refund voucher".
# Treating them as the same thing flips the sign on every ordinary invoice and
# silently produces a report where nothing matches anything.
NOTE_KEYS = ("cdnr", "cdn", "cdnra", "b2bcdn")
# Where the invoice list may be nested, from the root downwards.
CONTAINER_KEYS = ("docdata", "data", "itc_avl", "itcavl", "b2b_data")


class ParseError(Exception):
    pass


def _pick(mapping: dict, *names, default=None):
    for n in names:
        if n in mapping and mapping[n] not in (None, ""):
            return mapping[n]
    return default


def _parse_date(value) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%y", "%d%m%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _add_amounts(target: dict, item: dict) -> None:
    """Add one item's tax amounts to a running total, accepting either naming."""
    detail = item.get("itm_det") if isinstance(item.get("itm_det"), dict) else item
    target["taxable"] += parse_amount(_pick(detail, "txval", "taxable_value", "taxable", default=0))
    target["igst"] += parse_amount(_pick(detail, "igst", "iamt", default=0))
    target["cgst"] += parse_amount(_pick(detail, "cgst", "camt", default=0))
    target["sgst"] += parse_amount(_pick(detail, "sgst", "samt", default=0))
    target["cess"] += parse_amount(_pick(detail, "cess", "csamt", default=0))


def _iter_invoices(node) -> list[dict]:
    """Find every invoice list in the document, whatever the wrapping."""
    found: list[dict] = []

    def walk(obj, parent_gstin=None, depth=0):
        if depth > 8:
            return
        if isinstance(obj, list):
            for entry in obj:
                walk(entry, parent_gstin, depth + 1)
            return
        if not isinstance(obj, dict):
            return

        gstin = norm_gstin(_pick(obj, "ctin", "gstin", "supplier_gstin", default=parent_gstin))

        for key in BILLING_KEYS + NOTE_KEYS:
            if isinstance(obj.get(key), list):
                for group in obj[key]:
                    walk(group, gstin, depth + 1)

        for key in ("inv", "nt", "doclist", "documents", "invoice"):
            if isinstance(obj.get(key), list):
                for inv in obj[key]:
                    if isinstance(inv, dict):
                        found.append({
                            "inv": inv,
                            "gstin": norm_gstin(_pick(inv, "ctin", default=gstin)),
                            "vendor": _pick(obj, "trdnm", "cfs", "trade_name", default=""),
                            "note_section": key in ("nt",),
                        })

        for key in CONTAINER_KEYS:
            if isinstance(obj.get(key), (dict, list)):
                walk(obj[key], gstin, depth + 1)

    walk(node)
    return found


def load(path: str | Path) -> tuple[list[Doc], dict]:
    """Parse a GSTR-2B (or GSTR-2A) JSON file into documents.

    Returns the documents and a small meta dict describing what was found, so
    the caller can tell a genuinely empty month from an unreadable file.
    """
    raw_text = Path(path).read_text(encoding="utf-8-sig")
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"{path}: not valid JSON ({exc.msg} at line {exc.lineno}). "
            "The portal's JSON download is what this expects, not the Excel file."
        ) from exc

    hits = _iter_invoices(payload)
    if not hits:
        raise ParseError(
            f"{path}: no B2B invoices found. Looked for a `b2b` array containing "
            "`inv` entries (current GSTR-2B shape) and for `itc_avl` wrappers. "
            "If this is a GSTR-2A file it will still work; if it is the Excel "
            "download, export the JSON instead."
        )

    docs: list[Doc] = []
    for hit in hits:
        inv = hit["inv"]
        totals = {"taxable": 0, "igst": 0, "cgst": 0, "sgst": 0, "cess": 0}

        items = _pick(inv, "items", "itms", default=None)
        if isinstance(items, list) and items:
            for item in items:
                if isinstance(item, dict):
                    _add_amounts(totals, item)
        else:
            # Some downloads carry the amounts flat on the invoice instead.
            _add_amounts(totals, inv)

        note_type = str(_pick(inv, "ntty", "typ", "inv_typ", "doc_type", default="")).upper()
        # A credit note is a credit note if it sits in a notes section, or if it
        # says so explicitly. "R" alone is *regular invoice* outside a notes
        # section and *refund voucher* inside one.
        in_note_section = hit.get("note_section", False)
        sign = -1 if (note_type == "C" or (in_note_section and note_type in {"C", "R"})) else 1

        itc_flag = str(_pick(inv, "itcavl", "itc_available", default="Y")).upper()
        docs.append(Doc(
            source="2b",
            gstin=hit["gstin"],
            vendor=str(hit["vendor"] or "").strip(),
            invoice_no=str(_pick(inv, "inum", "nt_num", "docnum", default="")).strip(),
            invoice_date=_parse_date(_pick(inv, "dt", "idt", "nt_dt", "docdate")),
            taxable=totals["taxable"],
            igst=totals["igst"],
            cgst=totals["cgst"],
            sgst=totals["sgst"],
            cess=totals["cess"],
            sign=sign,
            itc_available=(itc_flag not in {"N", "NO"}),
            raw={"typ": note_type},
        ))

    meta = {
        "period": str(_pick(payload, "rtnprd", "ret_period", "fp", default="")),
        "gstin": norm_gstin(_pick(payload, "gstin", default="")),
        "documents": len(docs),
        "credit_notes": sum(1 for d in docs if d.sign < 0),
        "blocked_itc": sum(1 for d in docs if not d.itc_available),
        "suppliers": len({d.gstin for d in docs}),
    }
    return docs, meta
