"""Read a purchase register exported from Tally, Excel or anything else.

Nobody's export has the same headers. `Invoice No`, `Bill No`, `Voucher No`,
`Supplier Invoice`, `Inv #` all mean the same thing, and Tally adds preamble
rows above the real header often enough that it has to be handled. So the
reader scores every header cell against a list of aliases and takes the best
match rather than demanding exact names.

If a required column cannot be found, the error lists the headers it actually
saw. A message like "couldn't find invoice number; found: Date, Party, Amount"
is the difference between a five-second fix and giving up.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from pathlib import Path

from .model import Doc
from .money import parse_amount
from .normalize import norm_gstin

ALIASES = {
    "gstin": ["suppliergstin", "gstin", "gstno", "gstnumber", "partygstin",
              "gstinuin", "gstinofsupplier", "suppliergst", "gst"],
    "invoice_no": ["invoiceno", "invoicenumber", "billno", "billnumber",
                   "voucherno", "vouchernumber", "supplierinvoiceno",
                   "supplierinvoicenumber", "invno", "invoiceno.", "inv",
                   "documentno", "refno"],
    "invoice_date": ["invoicedate", "billdate", "voucherdate", "date", "dt",
                     "invoicedt", "docdate"],
    "taxable": ["taxablevalue", "taxable", "taxableamount", "netamount",
                "basicamount", "assessablevalue", "taxavalue", "taxableval"],
    "igst": ["igst", "igstamount", "integratedtax"],
    "cgst": ["cgst", "cgstamount", "centraltax"],
    "sgst": ["sgst", "sgstamount", "utgst", "statetax", "sgstutgst"],
    "cess": ["cess", "cessamount"],
    "invoice_value": ["invoicevalue", "totalvalue", "total", "grossamount",
                      "invoiceamount", "totalinvoicevalue", "grosstotal"],
    "vendor": ["partyname", "suppliername", "party", "supplier", "ledgername",
               "vendorfirm", "nameoftheparty", "vendor"],
    "itc": ["itcavailability", "itcavailable", "itc"],
}

REQUIRED = ("gstin", "invoice_no")


class RegisterError(Exception):
    pass


def _canon(header: str) -> str:
    return "".join(ch for ch in str(header).lower() if ch.isalnum())


def _score(header: str, aliases: list[str]) -> int:
    h = _canon(header)
    if not h:
        return 0
    best = 0
    for alias in aliases:
        if h == alias:
            best = max(best, 100)
        elif h.startswith(alias) or alias.startswith(h):
            best = max(best, 80 - abs(len(h) - len(alias)))
        elif alias in h:
            best = max(best, 60 - (len(h) - len(alias)))
    return best


def _map_columns(headers: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    used: set[int] = set()
    scores = []
    for field_name, aliases in ALIASES.items():
        for idx, header in enumerate(headers):
            s = _score(header, aliases)
            if s > 0:
                scores.append((s, field_name, idx))
    # Highest-confidence assignments win, and a column is only used once, so
    # "Invoice Date" cannot be claimed by both `date` and `invoice_no`.
    for s, field_name, idx in sorted(scores, reverse=True):
        if field_name in mapping or idx in used:
            continue
        mapping[field_name] = idx
        used.add(idx)
    return mapping


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y",
                "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Excel serial dates arrive as bare numbers when a sheet is exported.
    try:
        serial = int(float(text))
    except ValueError:
        return None
    if 20000 < serial < 80000:
        from datetime import timedelta

        return date(1899, 12, 30) + timedelta(days=serial)
    return None


def _find_header_row(rows: list[list[str]], limit: int = 15) -> int | None:
    """Locate the real header when a report has title rows above it."""
    best_idx, best_hits = None, 0
    for i, row in enumerate(rows[:limit]):
        hits = 0
        for cell in row:
            for aliases in ALIASES.values():
                if _score(cell, aliases) >= 60:
                    hits += 1
                    break
        if hits > best_hits:
            best_idx, best_hits = i, hits
    return best_idx if best_hits >= 2 else None


def load(path: str | Path, *, delimiter: str | None = None) -> tuple[list[Doc], dict]:
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix in {".xlsx", ".xlsm"}:
        from .xlsx import read_sheet

        rows = read_sheet(p)
    else:
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        if delimiter is None:
            sample = text[:4096]
            delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))

    rows = [[c for c in row] for row in rows if any(str(c).strip() for c in row)]
    if not rows:
        raise RegisterError(f"{path}: the file is empty")

    header_idx = _find_header_row(rows)
    if header_idx is None:
        preview = ", ".join(str(c) for c in rows[0][:8])
        raise RegisterError(
            f"{path}: could not find a header row. First row was: {preview}. "
            "The register needs at least a supplier GSTIN column and an invoice "
            "number column."
        )

    headers = [str(c).strip() for c in rows[header_idx]]
    cols = _map_columns(headers)

    missing = [name for name in REQUIRED if name not in cols]
    if missing:
        seen = ", ".join(h for h in headers if h)[:200]
        raise RegisterError(
            f"{path}: could not find column(s) {missing}. "
            f"Headers found: {seen}. "
            "Rename the column, or pass --map to point at the right one."
        )

    docs: list[Doc] = []
    skipped = 0
    for offset, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        def cell(field_name, default=""):
            idx = cols.get(field_name)
            if idx is None or idx >= len(row):
                return default
            return row[idx]

        gstin = norm_gstin(cell("gstin"))
        invoice_no = str(cell("invoice_no")).strip()
        if not gstin and not invoice_no:
            skipped += 1
            continue
        if not invoice_no:
            skipped += 1
            continue

        docs.append(Doc(
            source="books",
            gstin=gstin,
            vendor=str(cell("vendor")).strip(),
            invoice_no=invoice_no,
            invoice_date=_parse_date(cell("invoice_date")),
            taxable=parse_amount(cell("taxable")),
            igst=parse_amount(cell("igst")),
            cgst=parse_amount(cell("cgst")),
            sgst=parse_amount(cell("sgst")),
            cess=parse_amount(cell("cess")),
            itc_available=str(cell("itc", "Y")).strip().upper() not in {"N", "NO"},
            row=offset,
        ))

    meta = {
        "documents": len(docs),
        "skipped": skipped,
        "columns": cols,
        "headers": headers,
        "suppliers": len({d.gstin for d in docs}),
    }
    return docs, meta
