#!/usr/bin/env python3
"""Generate the bundled sample GSTR-2B and purchase register.

Both files are built from the single table below, so they can never drift apart
and the totals in the README are always true.

Every supplier here is fictional. The GSTINs are structurally valid (15
characters, correct shape) but are not registered numbers; the state code is 24,
which is Gujarat, because that is where the first users are.

Run:  python3 tools/make_samples.py
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "examples"

BUYER_GSTIN = "24AAAAA0000A1Z5"
PERIOD = "092026"

# (supplier, gstin, 2b_invoice, 2b_date, taxable, rate, books_invoice,
#  books_date, scenario)
# scenario is what the sample is meant to demonstrate; it is not written into
# the files, only into this comment and the README.
DATA = [
    ("Sharma Traders", "24AABCS1234F1Z5",
     "GST/2026/0145", "12-08-2026", 120000, 18,
     "GST/2026/0145", "12-08-2026", "exact"),
    ("Sharma Traders", "24AABCS1234F1Z5",
     "GST/2026/0188", "27-08-2026", 48000, 18,
     "GST/2026/0188", "27-08-2026", "exact"),
    ("Sharma Traders", "24AABCS1234F1Z5",
     "CN/2026/007", "29-08-2026", -15000, 18,
     "CN/2026/007", "29-08-2026", "credit_note"),

    ("Krishna Steel Pvt Ltd", "24AACCK5678G1Z1",
     "KS/26/1102", "05-08-2026", 260000, 18,
     "KS/26/1102", "05-08-2026", "exact"),
    ("Krishna Steel Pvt Ltd", "24AACCK5678G1Z1",
     "KS/26/1150", "22-08-2026", 95000, 18,
     "KS/26/1150", "22-08-2026", "blocked_itc"),

    ("Mehta Textiles", "24AAECM9012H1Z9",
     "MT-4410", "08-08-2026", 175000, 5,
     "MT-4410", "08-08-2026", "exact"),
    ("Mehta Textiles", "24AAECM9012H1Z9",
     "MT-4477", "19-08-2026", 62000, 5,
     "", "", "only_in_2b"),

    ("Vora Chemicals LLP", "24AAFCT2345J1Z3",
     "GST/2026/0112", "14-08-2026", 84000, 18,
     "INV-112", "14-08-2026", "numbered"),
    ("Vora Chemicals LLP", "24AAFCT2345J1Z3",
     "GST/2026/0119", "21-08-2026", 36500, 18,
     "INV-119", "21-08-2026", "numbered"),

    ("Patel Hardware", "24AAGHP3456K1Z7",
     "PH-2026-0071", "11-08-2026", 42500, 18,
     "PH/2026/71", "11-08-2026", "fuzzy"),

    ("Desai Packaging", "24AAIPD4567L1Z2",
     "DP/3311", "16-08-2026", 88000, 18,
     "DP/3311", "16-08-2026", "value_mismatch"),

    ("Nair Electronics", "24AAJPN5678M1Z8",
     "NE/2026/0210", "", 0, 18,
     "NE/2026/0210", "06-08-2026", "only_in_books"),
    ("Nair Electronics", "24AAJPN5678M1Z8",
     "NE/2026/0224", "", 0, 18,
     "NE/2026/0224", "18-08-2026", "only_in_books"),
    ("Nair Electronics", "24AAJPN5678M1Z8",
     "NE/2026/0231", "", 0, 18,
     "NE/2026/0231", "26-08-2026", "only_in_books"),
]


def tax_split(taxable: int, rate: int) -> tuple[Decimal, Decimal, Decimal]:
    """Intra-state Gujarat supply: CGST and SGST, no IGST."""
    total = (Decimal(taxable) * Decimal(rate) / Decimal(100)).quantize(Decimal("0.01"))
    half = (total / 2).quantize(Decimal("0.01"))
    return Decimal("0"), half, half


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    b2b: dict[str, dict] = {}
    rows = []

    for (supplier, gstin, inv2b, d2b, taxable, rate,
         inv_books, dbooks, scenario) in DATA:
        # A credit note in GSTR-2B carries positive amounts and is identified
        # by typ "C". A register writes the same note as a negative row. Both
        # conventions appear below, because both appear in real files.
        abs_taxable = abs(taxable)
        igst, cgst, sgst = tax_split(abs_taxable, rate)
        total_tax = igst + cgst + sgst
        value = (Decimal(abs_taxable) + total_tax).quantize(Decimal("0.01"))

        if inv2b:
            group = b2b.setdefault(gstin, {"ctin": gstin, "trdnm": supplier, "inv": []})
            group["inv"].append({
                "inum": inv2b,
                "dt": d2b,
                "val": float(value),
                "pos": gstin[:2],
                "typ": "C" if scenario == "credit_note" else "R",
                "itcavl": "N" if scenario == "blocked_itc" else "Y",
                "items": [{
                    "num": 1,
                    "itm_det": {
                        "rt": rate,
                        "txval": float(Decimal(abs_taxable).quantize(Decimal("0.01"))),
                        "igst": float(igst),
                        "cgst": float(cgst),
                        "sgst": float(sgst),
                        "cess": 0.0,
                    },
                }],
            })

        if inv_books:
            book_taxable = Decimal(taxable)
            book_cgst, book_sgst = (cgst, sgst) if taxable >= 0 else (-cgst, -sgst)
            if scenario == "value_mismatch":
                # The books claim a larger taxable value than the supplier
                # reported. This is the case that quietly inflates an ITC claim.
                book_taxable = Decimal(taxable) + Decimal("15254.24")
                _, book_cgst, book_sgst = tax_split(int(book_taxable), rate)
            book_value = (book_taxable + book_cgst + book_sgst).quantize(Decimal("0.01"))
            rows.append([
                supplier, gstin, inv_books, dbooks,
                f"{book_taxable:.2f}", "0.00",
                f"{book_cgst:.2f}", f"{book_sgst:.2f}", "0.00",
                f"{book_value:.2f}",
            ])

    doc = {
        "gstin": BUYER_GSTIN,
        "rtnprd": PERIOD,
        "version": "3.1.1",
        "gendt": "14-09-2026",
        "docdata": {"b2b": list(b2b.values())},
    }

    json_path = OUT / "sample-gstr2b.json"
    json_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    csv_path = OUT / "sample-purchase-register.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "Supplier Name", "Supplier GSTIN", "Invoice No", "Invoice Date",
            "Taxable Value", "IGST", "CGST", "SGST", "Cess", "Invoice Value",
        ])
        writer.writerows(rows)

    print(f"{json_path.name}: {sum(len(g['inv']) for g in b2b.values())} documents, "
          f"{len(b2b)} suppliers")
    print(f"{csv_path.name}: {len(rows)} rows")


if __name__ == "__main__":
    main()
