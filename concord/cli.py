"""Command line for Concord.

    concord reconcile --2b gstr2b.json --books purchase-register.xlsx
    concord reconcile ... --csv recon.csv --save-memory concord-memory.json
    concord chase     --2b gstr2b.json --books register.xlsx
    concord demo

Nothing here talks to a network. The client's purchase register is read from
disk, reconciled in memory, and written back to disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .gstr2b import ParseError, load as load_2b
from .match import learn, reconcile
from .memory import DEFAULT_NAME, load as load_memory, save as save_memory, summary as memory_summary
from .register import RegisterError, load as load_books
from .report import chase_message, full_report, summary as summary_report, to_csv, vendors

DEMO = Path(__file__).resolve().parent.parent / "examples"


def _need(path: str, what: str) -> None:
    """A mistyped path should read as a sentence, not a traceback. Most people
    running this are accountants on Windows, not developers reading stack
    frames."""
    p = Path(path)
    if not p.exists():
        sys.exit(f"concord: cannot open the {what} file: {path}\n"
                 f"         check the path, or drag the file onto the terminal "
                 f"to paste its location.")
    if p.is_dir():
        sys.exit(f"concord: {path} is a folder, not a file. Point at the file inside it.")


def _load(args):
    _need(args.gstr2b, "GSTR-2B")
    _need(args.books, "purchase register")
    try:
        two_b, meta_2b = load_2b(args.gstr2b)
    except ParseError as exc:
        sys.exit(f"concord: {exc}")
    except OSError as exc:
        sys.exit(f"concord: cannot read {args.gstr2b}: {exc.strerror or exc}")
    try:
        books, meta_books = load_books(args.books)
    except (RegisterError, ValueError) as exc:
        sys.exit(f"concord: {exc}")
    except OSError as exc:
        sys.exit(f"concord: cannot read {args.books}: {exc.strerror or exc}")
    if not books:
        sys.exit(f"concord: {args.books} has a header but no data rows.")
    return two_b, meta_2b, books, meta_books


def cmd_reconcile(args) -> int:
    two_b, meta_2b, books, meta_books = _load(args)
    memory = load_memory(args.memory)

    result = reconcile(
        two_b, books,
        value_tolerance=args.value_tolerance,
        absolute_tolerance=int(args.absolute_tolerance * 100),
        date_window=args.date_window,
        fuzzy_threshold=args.fuzzy_threshold,
        memory=memory,
    )

    if args.save_memory:
        updated = learn(result, memory)
        save_memory(updated, args.save_memory)

    if args.json:
        payload = {
            "stats": result.stats,
            "itc_at_risk": result.itc_at_risk / 100,
            "unclaimed_itc": result.unclaimed_itc / 100,
            "period": meta_2b.get("period"),
            "suppliers": _vendor_rows(result),
            "only_in_books": [
                {"vendor": m.vendor, "gstin": m.gstin,
                 "invoice_no": m.book.invoice_no,
                 "tax": m.book.total_tax / 100}
                for m in result.only_books
            ],
            "only_in_2b": [
                {"vendor": m.vendor, "gstin": m.gstin,
                 "invoice_no": m.two_b.invoice_no,
                 "tax": m.two_b.total_tax / 100}
                for m in result.only_2b
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(full_report(result, meta_2b, limit=args.limit))
        if args.memory:
            print(f"\n  memory: {memory_summary(memory)}")

    if args.csv:
        path = to_csv(result, args.csv)
        print(f"\nWritten to {path}", file=sys.stderr)

    if args.fail_on_risk and result.itc_at_risk > 0:
        return 2
    return 0


def _vendor_rows(result):
    from .match import vendor_exposure

    return vendor_exposure(result)


def cmd_chase(args) -> int:
    two_b, meta_2b, books, _ = _load(args)
    result = reconcile(two_b, books)
    rows = _vendor_rows(result)
    if not rows:
        print("Every supplier reconciles. Nothing to chase.")
        return 0

    period = meta_2b.get("period") or ""
    if len(period) == 6 and period.isdigit():
        period = f"{period[:2]}/{period[2:]}"

    out_dir = Path(args.out) if args.out else Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for n, row in enumerate(rows, 1):
        safe = "".join(ch for ch in row["vendor"] if ch.isalnum())[:20] or f"supplier{n}"
        path = out_dir / f"{n:02d}-{safe}.txt"
        path.write_text(chase_message(row, period), encoding="utf-8")
        written.append(path)

    print(f"Wrote {len(written)} follow-up messages to {out_dir}/")
    n = rows[0]["documents"]
    print(f"Largest exposure: {rows[0]['vendor']} — {n} document{'s' if n != 1 else ''}")
    for p in written[:5]:
        print(f"  {p}")
    if len(written) > 5:
        print(f"  ... and {len(written) - 5} more")
    return 0


def cmd_demo(args) -> int:
    two_b, meta_2b, books, _ = _load(argparse.Namespace(
        gstr2b=str(DEMO / "sample-gstr2b.json"),
        books=str(DEMO / "sample-purchase-register.csv"),
    ))
    result = reconcile(two_b, books)
    print(full_report(result, meta_2b, limit=args.limit))
    print("\nThis is the bundled sample. Run `concord reconcile --help` to point"
          "\nit at a real GSTR-2B download and a real purchase register.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="concord",
        description="Reconcile GSTR-2B against a purchase register. Runs entirely "
                    "on your machine; nothing is uploaded.",
    )
    ap.add_argument("--version", action="version", version=f"concord {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--2b", dest="gstr2b", required=True, metavar="FILE",
                       help="GSTR-2B JSON downloaded from the GST portal")
        p.add_argument("--books", required=True, metavar="FILE",
                       help="purchase register (.csv, .tsv or .xlsx)")

    p = sub.add_parser("reconcile", help="match GSTR-2B against the books")
    common(p)
    p.add_argument("--csv", metavar="FILE", help="write a full row-by-row CSV")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--memory", metavar="FILE", help="memory file from earlier months")
    p.add_argument("--save-memory", metavar="FILE",
                   help="record this month's judgement calls for next month")
    p.add_argument("--limit", type=int, default=15, help="rows per section (default 15)")
    p.add_argument("--date-window", type=int, default=7,
                   help="days either side when matching on amount and date")
    p.add_argument("--value-tolerance", type=float, default=0.005,
                   help="relative amount tolerance (default 0.005 = 0.5%%)")
    p.add_argument("--absolute-tolerance", type=float, default=1.0,
                   help="absolute amount tolerance in rupees (default 1)")
    p.add_argument("--fuzzy-threshold", type=float, default=0.82,
                   help="similarity needed for a fuzzy number match (default 0.82)")
    p.add_argument("--fail-on-risk", action="store_true",
                   help="exit 2 if any credit is at risk, for CI use")
    p.set_defaults(fn=cmd_reconcile)

    p = sub.add_parser("chase", help="write a follow-up message per supplier")
    common(p)
    p.add_argument("--out", metavar="DIR", help="directory for the messages")
    p.set_defaults(fn=cmd_chase)

    p = sub.add_parser("demo", help="run the bundled sample data")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(fn=cmd_demo)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
