"""A minimal XLSX reader with no dependencies.

Tally and Excel both hand out .xlsx, and a CA firm's purchase register is
almost always one. Every Python library that reads it is a dependency, and this
project has none by design -- `pip install` behind a corporate proxy on a CA's
laptop is where adoption dies.

An .xlsx file is a zip of XML. This reads the shared string table and the first
worksheet, which is all a register needs. It is not a general Excel library: no
formulas, no styles, no merged cells, no multiple sheets by name.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
CELL_REF = re.compile(r"([A-Z]+)(\d+)")


def _column_index(ref: str) -> int:
    """`A1` -> 0, `AB3` -> 27."""
    match = CELL_REF.match(ref or "")
    if not match:
        return 0
    letters = match.group(1)
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        data = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(data)
    out = []
    for si in root.findall("m:si", NS):
        # A string may be split across several <t> runs by rich text.
        out.append("".join(t.text or "" for t in si.iter(
            "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
    return out


def _first_sheet_path(zf: zipfile.ZipFile) -> str:
    names = [n for n in zf.namelist()
             if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
    if not names:
        raise ValueError("no worksheet found in the workbook")
    names.sort()
    return names[0]


def read_sheet(path: str | Path) -> list[list[str]]:
    """Return the first worksheet as a list of rows of strings."""
    p = Path(path)
    if not zipfile.is_zipfile(p):
        raise ValueError(
            f"{path} is not a real .xlsx file. The old .xls format from Excel "
            "97-2003 is a different container; re-save it as .xlsx or .csv."
        )
    with zipfile.ZipFile(p) as zf:
        strings = _shared_strings(zf)
        root = ET.fromstring(zf.read(_first_sheet_path(zf)))

    rows: list[list[str]] = []
    for row in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
        cells: list[str] = []
        for cell in row.findall("m:c", NS):
            value = ""
            kind = cell.get("t")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))
            else:
                v = cell.find("m:v", NS)
                if v is not None and v.text is not None:
                    if kind == "s":
                        try:
                            value = strings[int(v.text)]
                        except (ValueError, IndexError):
                            value = ""
                    else:
                        value = v.text
            idx = _column_index(cell.get("r", ""))
            while len(cells) <= idx:
                cells.append("")
            cells[idx] = value
        rows.append(cells)
    return rows
