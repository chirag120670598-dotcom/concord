"""The one shape both sides of a reconciliation are reduced to."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Doc:
    """One inward-supply document, from GSTR-2B or from the purchase register.

    A credit note is the same object with ``sign`` of -1. Keeping it in the same
    type means the matcher does not need a parallel code path, and the totals
    stay right without special cases.
    """

    source: str                     # "2b" | "books"
    gstin: str
    invoice_no: str
    invoice_date: date | None = None
    taxable: int = 0                # paise
    igst: int = 0
    cgst: int = 0
    sgst: int = 0
    cess: int = 0
    sign: int = 1                   # -1 for credit notes
    itc_available: bool = True
    vendor: str = ""
    row: int = 0                    # line in the source file, for reporting
    raw: dict = field(default_factory=dict)

    @property
    def total_tax(self) -> int:
        return self.sign * (self.igst + self.cgst + self.sgst + self.cess)

    @property
    def value(self) -> int:
        """Invoice value, tax included."""
        return self.sign * (self.taxable + self.igst + self.cgst + self.sgst + self.cess)

    @property
    def claimable(self) -> bool:
        """Whether credit on this document is available at all.

        GSTR-2B marks documents `itcavl: N` when credit is blocked -- wrong
        place of supply, supplier filed late, and so on. Claiming those in the
        books is a defect, not a mismatch, and the report says so separately.
        """
        return self.itc_available

    def describe(self) -> str:
        vendor = self.vendor or self.gstin
        return f"{vendor} / {self.invoice_no}"
