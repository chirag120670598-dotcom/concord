"""What the tool remembers between months.

ICAI's own write-up of the reconciliation problem names this as the gap: every
month's matching starts from zero, and last month's decisions have to be
rediscovered. This file is the fix. It is plain JSON, it sits next to the
client's working folder, and it never leaves the machine.

What gets stored: for each supplier and each invoice number as written in the
books, the invoice number as it appears in GSTR-2B. Nothing else. No amounts, no
GSTINs of other clients, no client names.
"""

from __future__ import annotations

import json
from pathlib import Path

VERSION = 1
DEFAULT_NAME = "concord-memory.json"


def empty() -> dict:
    return {"version": VERSION, "confirmed": {}}


def load(path: str | Path | None) -> dict:
    if not path:
        return empty()
    p = Path(path)
    if not p.exists():
        return empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt memory file must not stop a reconciliation. Starting fresh
        # costs one month of fuzzy matching; refusing to run costs the month.
        return empty()
    if not isinstance(data, dict):
        return empty()
    data.setdefault("confirmed", {})
    data["version"] = VERSION
    return data


def save(memory: dict, path: str | Path) -> Path:
    p = Path(path)
    p.write_text(json.dumps(memory, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                 encoding="utf-8")
    return p


def summary(memory: dict) -> str:
    return f"{len(memory.get('confirmed', {}))} confirmed pairs remembered"
