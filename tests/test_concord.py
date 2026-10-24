"""Tests for Concord.

The reconciliation logic is arithmetic plus judgement, and both fail silently.
A matcher that quietly pairs the wrong invoices produces a clean-looking report
and a wrong ITC claim, so most of these tests are about what must *not* match.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from concord import gstr2b, register  # noqa: E402
from concord.match import (  # noqa: E402
    DATED, EXACT, FUZZY, MEMORY, NUMBERED, ONLY_2B, ONLY_BOOKS, VALUE_MISMATCH,
    learn, reconcile, vendor_exposure,
)
from concord.memory import empty as empty_memory  # noqa: E402
from concord.model import Doc  # noqa: E402
from concord.money import format_inr, format_inr_short, parse_amount  # noqa: E402
from concord.normalize import (  # noqa: E402
    digit_key, gstin_looks_valid, loose_key, norm_gstin, norm_invoice, similarity,
)

EXAMPLES = ROOT / "examples"
GSTIN_A = "24AABCS1234F1Z5"
GSTIN_B = "24AACCK5678G1Z1"


def doc(source, gstin, inv, taxable, rate=18, day=1, sign=1, available=True,
        vendor="V", month=9):
    half = round(taxable * rate / 200)
    d = Doc(source=source, gstin=gstin, invoice_no=inv, vendor=vendor, sign=sign,
            taxable=taxable, cgst=half, sgst=half, itc_available=available,
            invoice_date=date(2026, month, day))
    return d


class TestMoney(unittest.TestCase):

    def test_parses_spreadsheet_formatting(self):
        self.assertEqual(parse_amount("1,41,600.00"), 14160000)
        self.assertEqual(parse_amount("141600"), 14160000)
        self.assertEqual(parse_amount(1416), 141600)
        self.assertEqual(parse_amount("1416.00"), 141600)

    def test_blank_and_placeholders_are_zero(self):
        for value in ("", None, "-", "--", "N/A", "  "):
            self.assertEqual(parse_amount(value), 0, value)

    def test_one_paise_survives(self):
        """Banker's rounding would return 0 here and silently lose a paisa."""
        self.assertEqual(parse_amount("0.005"), 1)
        self.assertEqual(parse_amount("0.015"), 2)

    def test_negatives(self):
        self.assertEqual(parse_amount("-1350.00"), -135000)
        self.assertEqual(parse_amount("(1350.00)"), -135000)
        self.assertEqual(parse_amount("1350.00-"), -135000)

    def test_float_json_amounts(self):
        self.assertEqual(parse_amount(1416.0), 141600)

    def test_indian_grouping(self):
        self.assertEqual(format_inr(14160000), "\u20b91,41,600")
        self.assertEqual(format_inr(142900000), "\u20b914,29,000")
        self.assertEqual(format_inr(100000), "\u20b91,000")
        self.assertEqual(format_inr(-270000), "-\u20b92,700")

    def test_short_form(self):
        self.assertEqual(format_inr_short(154000000), "\u20b915.40L")
        self.assertEqual(format_inr_short(1200000000), "\u20b91.20Cr")


class TestNormalise(unittest.TestCase):

    def test_invoice_numbers_that_mean_the_same_thing(self):
        cases = [
            ("GST/2025/145", "GST2025145"),
            ("gst-2025-145", "GST2025145"),
            ("INV-145", "INV145"),
            ("inv 145", "INV145"),
            (" INV/145 ", "INV145"),
        ]
        for raw, expected in cases:
            self.assertEqual(norm_invoice(raw), expected, raw)

    def test_digit_key_finds_the_sequence_number(self):
        self.assertEqual(digit_key("GST/2025/145"), "145")
        self.assertEqual(digit_key("INV-145"), "145")
        self.assertEqual(digit_key("INV-00145"), "145")
        self.assertEqual(digit_key("PH/2026/71"), "71")
        self.assertEqual(digit_key("no digits here"), "")

    def test_loose_key(self):
        self.assertEqual(loose_key("GST/2025/145"), "2025145")

    def test_gstin_normalisation_and_shape(self):
        self.assertEqual(norm_gstin(" 24aabcs1234f1z5 "), GSTIN_A)
        self.assertTrue(gstin_looks_valid(GSTIN_A))
        self.assertFalse(gstin_looks_valid("24AABCS1234F1Z"))   # 14 chars
        self.assertFalse(gstin_looks_valid(""))

    def test_similarity(self):
        self.assertEqual(similarity("ABC", "ABC"), 1.0)
        self.assertGreater(similarity("PH20260071", "PH202671"), 0.8)
        self.assertLess(similarity("ABC", "XYZ"), 0.5)


class TestGSTR2B(unittest.TestCase):

    def write(self, payload) -> str:
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8")
        json.dump(payload, fh)
        fh.close()
        return fh.name

    def sample(self):
        return json.loads((EXAMPLES / "sample-gstr2b.json").read_text())

    def test_reads_the_documented_shape(self):
        docs, meta = gstr2b.load(EXAMPLES / "sample-gstr2b.json")
        self.assertEqual(meta["documents"], 14)
        self.assertEqual(meta["suppliers"], 7)
        self.assertEqual(meta["period"], "092026")

    def test_amounts_sum_across_items(self):
        docs, _ = gstr2b.load(EXAMPLES / "sample-gstr2b.json")
        sharma = [d for d in docs if d.invoice_no == "GST/2026/0145"][0]
        self.assertEqual(sharma.taxable, 12000000)
        self.assertEqual(sharma.cgst, 1080000)
        self.assertEqual(sharma.sgst, 1080000)
        self.assertEqual(sharma.total_tax, 2160000)
        self.assertEqual(sharma.value, 14160000)

    def test_regular_invoice_is_not_treated_as_a_credit_note(self):
        """`typ: R` means Regular. Reading it as a refund voucher flips the sign
        on every invoice and produces a report where nothing matches."""
        docs, _ = gstr2b.load(EXAMPLES / "sample-gstr2b.json")
        regular = [d for d in docs if d.invoice_no == "GST/2026/0145"][0]
        self.assertEqual(regular.sign, 1)
        self.assertGreater(regular.total_tax, 0)

    def test_typ_c_inside_b2b_is_a_credit_note(self):
        docs, _ = gstr2b.load(EXAMPLES / "sample-gstr2b.json")
        cn = [d for d in docs if d.invoice_no == "CN/2026/007"][0]
        self.assertEqual(cn.sign, -1)
        self.assertLess(cn.total_tax, 0)

    def test_blocked_credit_flag(self):
        docs, meta = gstr2b.load(EXAMPLES / "sample-gstr2b.json")
        blocked = [d for d in docs if not d.itc_available]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(meta["blocked_itc"], 1)

    def test_accepts_gstr_2a_field_names(self):
        """2A calls the date `idt`, the item list `itms`, and the taxes
        iamt/camt/samt. A parser that only understands 2B silently drops it."""
        payload = {"b2b": [{"ctin": GSTIN_A, "inv": [{
            "inum": "A/1", "idt": "05-09-2026", "val": 11800,
            "itms": [{"num": 1, "itm_det": {"rt": 18, "txval": 10000,
                                            "iamt": 1800, "camt": 0, "samt": 0,
                                            "csamt": 0}}],
        }]}]}
        docs, _ = gstr2b.load(self.write(payload))
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].igst, 180000)
        self.assertEqual(docs[0].invoice_date, date(2026, 9, 5))

    def test_accepts_a_data_wrapper(self):
        payload = {"data": {"docdata": {"b2b": [{"ctin": GSTIN_A, "trdnm": "X",
                                                "inv": [{"inum": "Z/9", "dt":
                                                "01-09-2026", "val": 1180,
                                                "items": [{"itm_det": {
                                                    "txval": 1000, "cgst": 90,
                                                    "sgst": 90}}]}]}]}}}
        docs, _ = gstr2b.load(self.write(payload))
        self.assertEqual(docs[0].invoice_no, "Z/9")
        self.assertEqual(docs[0].total_tax, 18000)

    def test_notes_section_is_read_as_a_credit_note(self):
        payload = {"docdata": {"cdnr": [{"ctin": GSTIN_A, "nt": [{
            "ntty": "C", "nt_num": "CN/1", "nt_dt": "03-09-2026",
            "val": 1180, "itcavl": "Y",
            "items": [{"itm_det": {"txval": 1000, "cgst": 90, "sgst": 90}}],
        }]}]}}
        docs, _ = gstr2b.load(self.write(payload))
        self.assertEqual(docs[0].sign, -1)
        self.assertEqual(docs[0].total_tax, -18000)

    def test_invalid_json_names_the_file(self):
        with self.assertRaises(gstr2b.ParseError) as ctx:
            gstr2b.load(self.write({}) if False else self._bad())
        self.assertIn("not valid JSON", str(ctx.exception))

    def _bad(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        fh.write("{not json")
        fh.close()
        return fh.name

    def test_empty_but_valid_json_says_what_it_looked_for(self):
        """An empty report and an unreadable file look identical to a user.
        The error must distinguish them."""
        with self.assertRaises(gstr2b.ParseError) as ctx:
            gstr2b.load(self.write({"results": []}))
        self.assertIn("no B2B invoices found", str(ctx.exception))


class TestRegister(unittest.TestCase):

    def test_reads_the_sample(self):
        docs, meta = register.load(EXAMPLES / "sample-purchase-register.csv")
        self.assertEqual(meta["documents"], 13)
        self.assertIn("invoice_no", meta["columns"])

    def test_credit_note_row_is_negative(self):
        docs, _ = register.load(EXAMPLES / "sample-purchase-register.csv")
        cn = [d for d in docs if d.invoice_no == "CN/2026/007"][0]
        self.assertLess(cn.total_tax, 0)

    def test_headers_with_different_names(self):
        text = ("Bill No,Party GSTIN,Bill Date,Assessable Value,CGST,SGST\n"
                "B/1,24AABCS1234F1Z5,01-09-2026,10000,900,900\n")
        fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        fh.write(text)
        fh.close()
        docs, _ = register.load(fh.name)
        self.assertEqual(docs[0].invoice_no, "B/1")
        self.assertEqual(docs[0].cgst, 90000)

    def test_title_rows_above_the_header(self):
        text = ("Purchase Register 2026-27\nAs on 30-09-2026\n\n"
                "Invoice No,Supplier GSTIN,Invoice Date,Taxable Value,CGST,SGST\n"
                "X/1,24AABCS1234F1Z5,02-09-2026,5000,450,450\n")
        fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        fh.write(text)
        fh.close()
        docs, _ = register.load(fh.name)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].invoice_no, "X/1")

    def test_missing_required_column_lists_what_it_saw(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        fh.write("Date,Party,Amount\n01-09-2026,Foo,1000\n")
        fh.close()
        with self.assertRaises(register.RegisterError) as ctx:
            register.load(fh.name)
        message = str(ctx.exception)
        self.assertIn("invoice_no", message)
        self.assertIn("Date", message)

    def test_tab_separated_file(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False)
        fh.write("Invoice No\tSupplier GSTIN\tCGST\nT/1\t24AABCS1234F1Z5\t90\n")
        fh.close()
        docs, _ = register.load(fh.name)
        self.assertEqual(docs[0].invoice_no, "T/1")

    def test_excel_serial_dates(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        fh.write("Invoice No,Supplier GSTIN,Invoice Date\n"
                 "S/1,24AABCS1234F1Z5,46268\n")
        fh.close()
        docs, _ = register.load(fh.name)
        self.assertIsNotNone(docs[0].invoice_date)


class TestXlsx(unittest.TestCase):

    def make_xlsx(self, rows) -> str:
        """A real, minimal .xlsx written by hand -- no library involved."""
        sheet_rows = []
        for r, row in enumerate(rows, 1):
            cells = []
            for c, value in enumerate(row):
                col = chr(65 + c)
                cells.append(f'<c r="{col}{r}" t="inlineStr"><is><t>{value}</t></is></c>')
            sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
        sheet = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
        )
        path = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False).name
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("xl/worksheets/sheet1.xml", sheet)
        return path

    def test_reads_a_register_from_xlsx(self):
        path = self.make_xlsx([
            ["Invoice No", "Supplier GSTIN", "Taxable Value", "CGST", "SGST"],
            ["X/1", GSTIN_A, "10000", "900", "900"],
        ])
        docs, meta = register.load(path)
        self.assertEqual(meta["documents"], 1)
        self.assertEqual(docs[0].invoice_no, "X/1")
        self.assertEqual(docs[0].cgst, 90000)

    def test_old_xls_is_refused_with_an_explanation(self):
        path = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False).name
        Path(path).write_bytes(b"\xd0\xcf\x11\xe0not a zip")
        with self.assertRaises(ValueError) as ctx:
            register.load(path)
        self.assertIn("xls", str(ctx.exception).lower())


class TestMatching(unittest.TestCase):

    def test_exact_invoice_number(self):
        r = reconcile([doc("2b", GSTIN_A, "INV/1", 100000)],
                      [doc("books", GSTIN_A, "INV/1", 100000)])
        self.assertEqual(r.matches[0].kind, EXACT)
        self.assertEqual(r.itc_at_risk, 0)

    def test_different_prefix_same_amount(self):
        """The case that breaks every VLOOKUP."""
        r = reconcile([doc("2b", GSTIN_A, "GST/2026/0145", 120000)],
                      [doc("books", GSTIN_A, "INV-145", 120000)])
        self.assertEqual(r.matches[0].kind, NUMBERED)
        self.assertEqual(r.itc_at_risk, 0)

    def test_same_digits_but_different_money_do_not_match(self):
        """`145` on its own is a weak key. Without the amount agreeing it must
        not pair, or two unrelated invoices get welded together."""
        r = reconcile([doc("2b", GSTIN_A, "GST/2026/0145", 120000)],
                      [doc("books", GSTIN_A, "INV-145", 50000)])
        self.assertEqual(len(r.matches), 0)
        self.assertEqual(len(r.only_2b), 1)
        self.assertEqual(len(r.only_books), 1)

    def test_different_supplier_never_matches(self):
        r = reconcile([doc("2b", GSTIN_A, "INV/1", 100000)],
                      [doc("books", GSTIN_B, "INV/1", 100000)])
        self.assertEqual(len(r.matches), 0)

    def test_value_mismatch_is_reported_not_swallowed(self):
        r = reconcile([doc("2b", GSTIN_A, "DP/3311", 88000)],
                      [doc("books", GSTIN_A, "DP/3311", 103254)])
        self.assertEqual(len(r.mismatched), 1)
        self.assertEqual(r.mismatched[0].kind, VALUE_MISMATCH)
        self.assertGreater(r.itc_at_risk, 0)
        self.assertIn("differs", r.mismatched[0].note)

    def test_tiny_rounding_difference_is_tolerated(self):
        a = doc("2b", GSTIN_A, "X/1", 100000)
        b = doc("books", GSTIN_A, "X/1", 100000)
        b.cgst += 50          # 50 paise
        r = reconcile([a], [b])
        self.assertEqual(len(r.matches), 1)
        self.assertEqual(len(r.mismatched), 0)

    def test_only_in_books_is_the_risk(self):
        r = reconcile([], [doc("books", GSTIN_A, "GONE/1", 100000)])
        self.assertEqual(r.only_books[0].kind, ONLY_BOOKS)
        self.assertEqual(r.itc_at_risk, 18000)
        self.assertGreater(r.only_books[0].risk, 0)

    def test_only_in_2b_is_money_left_on_the_table(self):
        r = reconcile([doc("2b", GSTIN_A, "NEW/1", 100000)], [])
        self.assertEqual(r.only_2b[0].kind, ONLY_2B)
        self.assertEqual(r.itc_at_risk, 0)
        self.assertGreater(r.unclaimed_itc, 0)

    def test_blocked_credit_is_the_whole_amount_at_risk(self):
        """Not the difference. The portal says none of it is available, so the
        entire claim is exposed."""
        r = reconcile([doc("2b", GSTIN_A, "KS/1", 95000, available=False)],
                      [doc("books", GSTIN_A, "KS/1", 95000)])
        self.assertEqual(len(r.blocked_claims), 1)
        self.assertEqual(r.itc_at_risk, 17100)
        self.assertEqual(len(r.matches), 0)

    def test_credit_note_does_not_match_an_invoice(self):
        r = reconcile([doc("2b", GSTIN_A, "CN/1", 15000, sign=-1)],
                      [doc("books", GSTIN_A, "CN/1", 15000, sign=1)])
        self.assertEqual(len(r.matches), 0)

    def test_credit_note_matches_a_credit_note_across_conventions(self):
        """2B marks it typ C with positive amounts; the register writes a
        negative row. Both are -2,700 and both must reconcile."""
        two_b = doc("2b", GSTIN_A, "CN/2026/007", 15000, sign=-1)
        two_b.cgst, two_b.sgst = 135000, 135000
        book = doc("books", GSTIN_A, "CN/2026/007", 15000, sign=1)
        book.cgst, book.sgst = -135000, -135000
        r = reconcile([two_b], [book])
        self.assertEqual(len(r.matches), 1)

    def test_fuzzy_when_the_numbers_are_similar(self):
        two_b = doc("2b", GSTIN_A, "PH-2026-0071", 42500)
        book = doc("books", GSTIN_A, "PH-2026-00710", 42500)
        two_b.invoice_date = None
        book.invoice_date = None
        r = reconcile([two_b], [book])
        self.assertIn(r.matches[0].kind, {FUZZY, NUMBERED})

    def test_date_pass_when_numbers_are_unrelated(self):
        r = reconcile(
            [doc("2b", GSTIN_B, "AAA-900", 35000, day=10)],
            [doc("books", GSTIN_B, "ZZZ-111", 35000, day=12)],
            fuzzy_threshold=0.99,
        )
        self.assertEqual(r.matches[0].kind, DATED)

    def test_date_pass_respects_the_window(self):
        r = reconcile(
            [doc("2b", GSTIN_B, "AAA-900", 35000, day=1)],
            [doc("books", GSTIN_B, "ZZZ-111", 35000, day=28)],
            fuzzy_threshold=0.99, date_window=3,
        )
        self.assertEqual(len(r.matches), 0)

    def test_every_document_appears_exactly_once(self):
        two_b, _ = gstr2b.load(EXAMPLES / "sample-gstr2b.json")
        books, _ = register.load(EXAMPLES / "sample-purchase-register.csv")
        r = reconcile(two_b, books)
        matched_2b = [m.two_b for m in r.matches + r.mismatched + r.blocked_claims if m.two_b]
        matched_books = [m.book for m in r.matches + r.mismatched + r.blocked_claims if m.book]
        self.assertEqual(len(matched_2b), len(set(id(d) for d in matched_2b)))
        self.assertEqual(len(matched_books), len(set(id(d) for d in matched_books)))
        accounted_2b = len(matched_2b) + len(r.only_2b)
        accounted_books = len(matched_books) + len(r.only_books)
        self.assertEqual(accounted_2b, len(two_b))
        self.assertEqual(accounted_books, len(books))

    def test_match_rate_counts_the_problems_in_the_denominator(self):
        """Blocked credit and value mismatches are documents that did not
        reconcile. A rate that skips them is a rate that flatters itself."""
        r = reconcile(
            [doc("2b", GSTIN_A, "OK/1", 100000),
             doc("2b", GSTIN_B, "OK/2", 100000, available=False)],
            [doc("books", GSTIN_A, "OK/1", 100000),
             doc("books", GSTIN_B, "OK/2", 100000)],
        )
        self.assertEqual(r.matched_count, 1)
        self.assertEqual(round(r.match_rate, 4), 0.5)

    def test_sample_reconciles_at_a_believable_rate(self):
        two_b, _ = gstr2b.load(EXAMPLES / "sample-gstr2b.json")
        books, _ = register.load(EXAMPLES / "sample-purchase-register.csv")
        r = reconcile(two_b, books)
        self.assertGreater(r.match_rate, 0.75)
        self.assertLess(r.match_rate, 1.0, "a perfect sample would prove nothing")

    def test_vendor_exposure_is_sorted_by_money(self):
        two_b = [
            doc("2b", GSTIN_A, "OK/1", 100000),
            doc("2b", GSTIN_B, "OK/2", 100000, available=False),
        ]
        books = [
            doc("books", GSTIN_A, "OK/1", 100000),
            doc("books", GSTIN_B, "OK/2", 100000),
            doc("books", GSTIN_B, "MISSING/1", 50000),
        ]
        rows = vendor_exposure(reconcile(two_b, books))
        self.assertEqual(rows[0]["gstin"], GSTIN_B, "the vendor holding money leads")
        self.assertEqual(rows[0]["at_risk"], 27000)
        if len(rows) > 1:
            self.assertGreaterEqual(rows[0]["at_risk"], rows[-1]["at_risk"])


class TestMemory(unittest.TestCase):

    def test_a_fuzzy_pair_is_remembered_and_becomes_cheap(self):
        two_b = [doc("2b", GSTIN_A, "GST/2026/0145", 120000)]
        books = [doc("books", GSTIN_A, "INV-145", 120000)]
        first = reconcile(two_b, books)
        self.assertEqual(first.matches[0].kind, NUMBERED)

        memory = learn(first, empty_memory())
        self.assertEqual(len(memory["confirmed"]), 1)

        second = reconcile(two_b, books, memory=memory)
        self.assertEqual(second.matches[0].kind, MEMORY)

    def test_exact_matches_are_not_stored(self):
        r = reconcile([doc("2b", GSTIN_A, "X/1", 1000)],
                      [doc("books", GSTIN_A, "X/1", 1000)])
        self.assertEqual(len(learn(r, empty_memory())["confirmed"]), 0)

    def test_value_mismatches_are_never_learned(self):
        r = reconcile([doc("2b", GSTIN_A, "X/1", 100000)],
                      [doc("books", GSTIN_A, "X/1", 130000)])
        self.assertEqual(len(learn(r, empty_memory())["confirmed"]), 0)

    def test_corrupt_memory_does_not_stop_a_run(self):
        path = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        path.write("{ broken")
        path.close()
        from concord.memory import load

        self.assertEqual(load(path.name)["confirmed"], {})


class TestCli(unittest.TestCase):

    def run_cli(self, *argv):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from concord.cli import main

        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def test_demo_runs(self):
        code, out, _ = self.run_cli("demo")
        self.assertEqual(code, 0)
        self.assertIn("ITC at risk", out)
        self.assertIn("Period 09/2026", out)

    def test_reconcile_writes_a_csv(self):
        out_csv = tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name
        code, _, err = self.run_cli(
            "reconcile",
            "--2b", str(EXAMPLES / "sample-gstr2b.json"),
            "--books", str(EXAMPLES / "sample-purchase-register.csv"),
            "--csv", out_csv,
        )
        self.assertEqual(code, 0)
        rows = Path(out_csv).read_text(encoding="utf-8").splitlines()
        self.assertGreater(len(rows), 10)
        self.assertIn("status", rows[0])

    def test_json_output_parses(self):
        code, out, _ = self.run_cli(
            "reconcile",
            "--2b", str(EXAMPLES / "sample-gstr2b.json"),
            "--books", str(EXAMPLES / "sample-purchase-register.csv"),
            "--json",
        )
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIn("itc_at_risk", payload)
        self.assertGreater(payload["itc_at_risk"], 0)

    def test_fail_on_risk_exits_nonzero(self):
        code, _, _ = self.run_cli(
            "reconcile",
            "--2b", str(EXAMPLES / "sample-gstr2b.json"),
            "--books", str(EXAMPLES / "sample-purchase-register.csv"),
            "--fail-on-risk",
        )
        self.assertEqual(code, 2)

    def test_chase_writes_one_message_per_supplier(self):
        outdir = tempfile.mkdtemp()
        code, out, _ = self.run_cli(
            "chase",
            "--2b", str(EXAMPLES / "sample-gstr2b.json"),
            "--books", str(EXAMPLES / "sample-purchase-register.csv"),
            "--out", outdir,
        )
        self.assertEqual(code, 0)
        files = list(Path(outdir).glob("*.txt"))
        self.assertGreaterEqual(len(files), 1)
        body = files[0].read_text(encoding="utf-8")
        self.assertIn("GSTR-1", body)
        self.assertIn("input tax credit", body)

    def test_blocked_credit_message_does_not_claim_the_invoice_is_missing(self):
        """The invoice IS in 2B. Telling the supplier otherwise is a lie the CA
        has to defend on the phone."""
        from concord.report import chase_message

        row = {"gstin": GSTIN_B, "at_risk": 1710000, "documents": 1,
               "reasons": ["credit blocked by portal"],
               "documents_list": [{"invoice_no": "KS/26/1150",
                                   "invoice_date": "2026-08-22", "tax": 1710000}]}
        body = chase_message(row, "09/2026")
        self.assertNotIn("could not find", body)
        self.assertIn("NOT available", body)
        self.assertIn("KS/26/1150", body)
        self.assertIn("22/08/2026", body)
        self.assertIn("\u20b917,100", body)

    def test_value_mismatch_message_asks_about_the_amount(self):
        from concord.report import chase_message

        row = {"gstin": GSTIN_A, "at_risk": 274572, "documents": 1,
               "reasons": ["value differs"],
               "documents_list": [{"invoice_no": "DP/3311",
                                   "invoice_date": "2026-08-16", "tax": 274572}]}
        body = chase_message(row, "09/2026")
        self.assertIn("amounts do not match", body)
        self.assertNotIn("could not find", body)

    def test_cp1252_console_does_not_crash_on_the_rupee_sign(self):
        """cmd.exe cannot encode U+20B9. A Windows user piping the report to a
        file is the most ordinary thing in the world; it must not raise."""
        import os
        import subprocess

        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        proc = subprocess.run([sys.executable, "-m", "concord", "demo"],
                              cwd=ROOT, capture_output=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace")[-400:])
        self.assertIn("\u20b9".encode("utf-8"), proc.stdout)

    def test_missing_file_fails_cleanly(self):
        code, _, err = self.run_cli(
            "reconcile", "--2b", "/nope/none.json", "--books", "/nope/none.csv")
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
