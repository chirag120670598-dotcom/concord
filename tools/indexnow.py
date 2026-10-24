"""Tell Bing and Yandex the site exists. Google is not part of IndexNow.

    python3 tools/indexnow.py

Run it only after GitHub Pages is live: the key file must already be reachable
at the host root, or the endpoint rejects the submission.
"""

from __future__ import annotations

import json
import urllib.request

HOST = "chirag120670598-dotcom.github.io"
KEY = "a7f3c9d21b6e4f80a1c3d5e7b9f0a2c4"
URLS = [f"https://{HOST}/concord/"]


def main() -> int:
    payload = {
        "host": HOST,
        "key": KEY,
        "keyLocation": f"https://{HOST}/concord/{KEY}.txt",
        "urlList": URLS,
    }
    req = urllib.request.Request(
        "https://api.indexnow.org/indexnow",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            print(f"submitted {len(URLS)} url(s) -> HTTP {resp.status} (2xx = accepted)")
            return 0
    except urllib.error.HTTPError as exc:
        print(f"rejected -> HTTP {exc.code}: {exc.read()[:200]!r}")
        return 1
    except Exception as exc:  # noqa: BLE001 - network is out of our control
        print(f"could not reach indexnow: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
