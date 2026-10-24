"""Entry point.

Windows is the common case for an Indian CA firm, and its default stream
encoding cannot represent the rupee sign. Running `concord demo > out.txt` in
cmd.exe would otherwise die with a UnicodeEncodeError after printing nine
tenths of the report. Streams are switched to UTF-8 here, once, before any
output is produced.
"""

import sys


def _utf8_streams() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # A stream that cannot be reconfigured (already wrapped, or closed)
            # is not worth crashing over.
            pass


_utf8_streams()

from .cli import main  # noqa: E402  - must follow the stream fix

if __name__ == "__main__":
    sys.exit(main())
