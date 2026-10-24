"""Sanity checks for site/index.html. No browser in this sandbox, so these
are structural checks plus one real comparison against the live CLI."""
import html as htmllib
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
fails = []


class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attrs = []
        self.text = []
        self.depth = 0
        self.stack = []
        self.unbalanced = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend(attrs)
        if tag not in ("meta", "link", "br", "img", "input", "hr"):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass
            if tag not in self.stack:
                pass
        else:
            self.unbalanced.append(tag)

    def handle_data(self, data):
        self.text.append(data)


c = Collector()
c.feed(src)
text = re.sub(r"\s+", " ", " ".join(c.text))

# 1. nothing may load from the network -- the preview iframe has none, and a
#    page that silently depends on a CDN is a page that breaks offline.
external = []
for name, value in c.attrs:
    if name in ("src", "href") and value and value.startswith(("http://", "https://")):
        if not re.match(r"https://(github\.com|wa\.me|chirag120670598-dotcom\.github\.io)", value):
            external.append(value)
if external:
    fails.append(f"external resource(s) that would not load offline: {external}")

# 2. structure
for tag in ("<title>", 'name="description"', 'lang="en"', 'name="viewport"'):
    if tag not in src:
        fails.append(f"missing {tag}")
if "og:title" not in src:
    fails.append("missing og:title")
if c.unbalanced:
    fails.append(f"unclosed tag(s): {set(c.unbalanced)}")

# 3. the copy that must be on the page
must = {
    "UPI id": "chirag120670598-1@oksbi",
    "honest match range": "85–92%",
    "free claim": "no paid version",
    "offline promise": "never leaves your laptop",
    "no-network claim": "no network code",
    "share link": "wa.me/?text=",
    "not tax advice": "not tax advice",
}
for label, needle in must.items():
    if needle.lower() not in src.lower():
        fails.append(f"missing {label}: {needle!r}")

# 4. the sample output on the page must be the sample output of the tool
block = re.search(r'<div class="term">.*?<pre>(.*?)</pre>', src, re.S)
if not block:
    fails.append("no terminal block on the page")
else:
    shown = htmllib.unescape(re.sub(r"<[^>]+>", "", block.group(1)))
    def clean(block_text):
        out = []
        for line in block_text.splitlines():
            line = " ".join(line.split())
            if not line or line.startswith("$"):
                continue
            out.append(line)
        return out

    shown_lines = clean(shown)
    # The tool prints UTF-8 rupees; a Windows runner would otherwise try to
    # decode those bytes as cp1252 and fail before comparing anything.
    real = subprocess.run([sys.executable, "-m", "concord", "demo"], cwd=ROOT,
                          capture_output=True, encoding="utf-8",
                          errors="replace").stdout
    real_lines = clean(real)
    if shown_lines[:12] != real_lines[:12]:
        fails.append(f"page shows {len(shown_lines)} lines, tool prints {len(real_lines)}")
        for a, b in zip(shown_lines[:12], real_lines[:12]):
            if a != b:
                fails.append(f"page output differs from real output:\n  page: {a!r}\n  tool: {b!r}")
                break

# 5. rupee amounts on the page must exist in the real output
for amount in ("₹19,845.72", "₹2,745.72", "₹17,100", "₹3,100"):
    if amount not in text:
        fails.append(f"amount {amount} missing from page copy")
    if amount not in real:
        fails.append(f"amount {amount} claimed on page but not printed by the tool")

print(f"checked {len(c.tags)} tags, {len(text)} chars of copy")
if fails:
    print(f"FAILED ({len(fails)})")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("OK — all site checks passed")
