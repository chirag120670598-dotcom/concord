# Concord

**Reconcile GSTR-2B against your client's purchase register — on your own laptop, offline, free.**

No login. No upload. No subscription. No "pro" tier. The client's ledger never leaves the machine, because there is nowhere for it to go: Concord is a single folder of Python with no network code in it at all.

```
GSTR-2B JSON  +  purchase register (.xlsx / .csv)  ->  what is at risk, and who to call
```

---

## The problem it removes

Every month, for every GST client, someone does this by hand:

1. Download GSTR-2B from the portal.
2. Open the purchase register.
3. Try to match ~200 invoices by number — when the supplier wrote `INV-145`, the register says `GST/2026/0145`, and a third supplier wrote `145/25-26`.
4. Give up on the stragglers, eyeball the totals, and move on.

The failure mode is quiet. An invoice the supplier never filed means the credit in the books is not supported by 2B. Nothing shows up red anywhere — it surfaces eighteen months later as a notice.

Concord does the matching, then answers three questions in plain language:

- **What is at risk** — credit claimed in the books that GSTR-2B does not support.
- **What is unclaimed** — credit sitting in 2B that the books never recorded.
- **Who to call** — one ready-to-send message per supplier, largest exposure first.

---

## Try it in one minute

```bash
git clone https://github.com/chirag120670598-dotcom/concord.git
cd concord
python3 -m concord demo
```

Real output from the bundled sample (₹ figures are Indian-grouped, paise exact):

```
Concord  ·  GSTR-2B reconciliation
Period 09/2026  ·  GSTIN 24AAAAA0000A1Z5

  GSTR-2B documents            14
  Purchase register            13

  Matched                      11   78.6%
      different number, same amount        3
  Only in GSTR-2B               1
  Only in the books             0
  Value mismatches              1

--------------------------------------------------------------------
  ITC at risk                    ₹19,845.72
  not in GSTR-2B                     ₹3,100   (credit not claimed)
--------------------------------------------------------------------

  Largest exposure: Krishna Steel Pvt Ltd  ₹17,100 across 1 document(s)

Chase these suppliers, largest exposure first
--------------------------------------------------------------------
   1. Krishna Steel Pvt Ltd                    ₹17,100   1 doc
      24AACCK5678G1Z1   credit blocked by portal
   2. Desai Packaging                        ₹2,745.72   1 doc
      24AAIPD4567L1Z2   value differs
```

Three findings in one run, and all three are the kind a manual check misses:

- **Krishna Steel — ₹17,100.** The invoice is in 2B with `itcavl: N`. The portal says this credit is not available, and the books claimed it anyway. The whole amount is exposed, not a difference.
- **Desai Packaging — ₹2,745.72.** Same invoice on both sides, different value. 2B says ₹15,840 tax, the books say ₹18,585.72.
- **Mehta Textiles — ₹3,100.** In 2B, missing from the books. That is credit being paid for and not taken.

The sample is shipped deliberately imperfect. A tool that matches 100% on its own demo has not been tested.

---

## Use it on real files

```bash
# 1. Download GSTR-2B JSON from the portal:
#    Services -> Returns -> Returns Dashboard -> FY + period -> GSTR-2B
#    -> "Generate JSON File to Download"

python3 -m concord reconcile \
    --2b    ~/Downloads/gstr2b_092026.json \
    --books ~/client/ramesh-purchase-register.xlsx
```

Add `--csv recon.csv` for a row-by-row sheet you can send to the client, or `--json` for machine output.

```
concord reconcile --2b FILE --books FILE [--csv OUT.csv] [--json]
                  [--memory MEMORY.json] [--save-memory MEMORY.json]
                  [--date-window 7] [--value-tolerance 0.005]
                  [--absolute-tolerance 1] [--fuzzy-threshold 0.82]
                  [--fail-on-risk]        # exit code 2 if any credit is at risk

concord chase   --2b FILE --books FILE [--out DIR]
concord demo
```

`chase` writes one `.txt` per supplier — a paste-ready WhatsApp message naming the exact invoice and amount, worded so the supplier's accountant can act on it without a phone call.

### What it accepts

| | |
|---|---|
| **GSTR-2B** | JSON as downloaded from the portal. Handles the `b2b`, `cdnr`, `cdn` and `cdnra` sections, the flat `items` shape of 2B *and* the nested `itms` shape of 2A, and downloads with or without a `data` wrapper. |
| **Register** | `.xlsx`, `.csv`, `.tsv`. Column names are guessed by scoring, so `Invoice No`, `Bill No`, `Inv. No.`, `Voucher No` all work. Title rows above the header are skipped. Excel serial dates are understood. |

---

## How the matching works

Five passes, cheapest first, each pass only seeing documents the previous ones could not pair:

| Pass | Idea | Example |
|---|---|---|
| 1. Exact | Same normalised number, same amount | `GST/2026/0145` = `GST/2026/0145` |
| 2. Memory | A pair **you** confirmed in an earlier month | learned once, free forever |
| 3. Numbered | Trailing digit run, zeros stripped, amount agrees | `GST/2026/0145` = `INV-145` |
| 4. Fuzzy | Number similarity ≥ 0.82, amount agrees, dates near | `PH/2026/71` ≈ `PH/2026/710` |
| 5. Dated | Unrelated numbers, same amount, dates within 7 days | a renamed voucher |

Two rules keep it from inventing matches:

- **A number match alone is not a match.** `145` is a weak key — any supplier's 145th invoice looks like it. The amount must also agree, or the pair is left apart.
- **Different GSTIN is never the same supplier.** No exceptions, no fuzzy GSTINs.

Documents that pair on number but disagree on money are reported as **value mismatches**, never silently matched. Credit notes are kept in their own sign group, so a credit note can never be "matched" against an invoice — and both the 2B convention (`typ: C`, positive amounts) and the register convention (negative row) are understood.

Verdicts are never thrown away. `--save-memory` records the pairs that needed judgement (numbered, fuzzy, dated — never exact matches, never value mismatches) so next month the same supplier costs nothing.

---

## Honest limits

Read this before trusting a number.

- **Matching realistically lands at 85–92% on clean files, and lower on messy ones.** Not 100%, and any tool promising 100% is guessing at the remainder. The sample here is 78.6% because it is built to contain every failure.
- **It saves time. It does not recover money by itself.** The at-risk figure tells you where to look; calling the supplier and getting the GSTR-1 filed is what recovers it.
- **The sample is synthetic.** It exercises the code paths but proves nothing about real Tally exports, which are considerably stranger. Real files are still needed to call this validated.
- **Only the first sheet of an `.xlsx` is read.** Multi-sheet registers, merged cells, formulas and pivot tables are not parsed — flatten the register to one sheet first.
- **Old `.xls` is not supported.** Save as `.xlsx` or `.csv`.
- **`.xlsx` reading is hand-rolled** in about 200 lines of `zipfile` + `ElementTree`, so that a CA can install this without depending on anything. It is a smaller target than a general spreadsheet library, not a better one.
- **No GSTR-1, no GSTR-9, no e-invoice, no TDS.** Reconciliation of 2B against the books is the whole scope.
- **Not a filing tool.** Nothing here talks to the GST portal. You still file the return; this tells you what to fix before you do.
- **Not tax advice.** A human decides every treatment. The tool's job is to put the right document in front of that human.
- **It does not know about amendments (`b2ba`) beyond reading them as documents**, and does not reconcile ISD credit against the books beyond listing it.
- **Tested on Python 3.13, Linux.** The code is standard library only and should run anywhere Python does, but Windows is not something this repo has a CI badge for. Corrections welcome.

---

## If it saved you a day

It is free, and it stays free. If the month-end went easier, the UPI is:

```
chirag120670598-1@oksbi
```

Entirely optional — there is no paid version being held back, no licence key, and nothing in the tool checks whether you paid. If it did not help, do not send anything.

---

## Development

```bash
python3 -m unittest discover -s tests      # 59 tests, no dependencies
python3 tools/make_samples.py              # regenerate the sample data
```

The sample JSON and the sample register are generated from one table in `tools/make_samples.py`, so they cannot drift apart from each other.

```
concord/money.py      integer paise; half-away-from-zero, never round()
concord/normalize.py  invoice numbers, GSTINs, similarity
concord/model.py      the one Doc that both sides reduce to
concord/gstr2b.py     tolerant portal-JSON reader
concord/register.py   alias-scoring CSV / TSV / XLSX reader
concord/xlsx.py       stdlib XLSX reader
concord/match.py      five passes, sign groups, exposure
concord/memory.py     judgement calls, carried month to month
concord/report.py     terminal blocks, CSV, WhatsApp messages
concord/cli.py        command line
```

Apache-2.0. No telemetry, no analytics, no network calls — `grep -r "urllib\|socket\|requests" concord/` returns nothing.
