"""Probe additional Fx/Vx endpoints and syndication for timeline discovery."""

from __future__ import annotations

import json
import sys

import requests

S = requests.Session()
S.headers.update({"User-Agent": "XScheduleDiscovery-probe/0.1"})

URLS = [
    # guess timeline-ish paths
    "https://api.fxtwitter.com/BDP_yumemita/latest",
    "https://api.fxtwitter.com/BDP_yumemita/tweets",
    "https://api.fxtwitter.com/BDP_yumemita/statuses",
    "https://api.fxtwitter.com/user/BDP_yumemita",
    "https://api.fxtwitter.com/user/BDP_yumemita/tweets",
    "https://api.vxtwitter.com/BDP_yumemita/tweets",
    "https://api.vxtwitter.com/BDP_yumemita/status",
    # X syndication (often used by third parties, may rate-limit)
    "https://cdn.syndication.twimg.com/widgets/timelines/profile?screen_name=BDP_yumemita",
    "https://syndication.twitter.com/srv/timeline-profile/screen-name/BDP_yumemita",
    # nitter-like public (may fail)
    "https://r.jina.ai/http://x.com/BDP_yumemita",
]


def show(url: str) -> None:
    print("=" * 72)
    print(url)
    try:
        r = S.get(url, timeout=30, allow_redirects=True)
    except Exception as e:
        print("ERR", type(e).__name__, e)
        return
    print("status", r.status_code, "len", len(r.content), "final", r.url)
    ct = (r.headers.get("content-type") or "").lower()
    body = r.text
    if "json" in ct or body.strip().startswith("{") or body.strip().startswith("["):
        try:
            data = r.json()
            if isinstance(data, dict):
                print("keys", list(data.keys())[:25])
            print(json.dumps(data, ensure_ascii=False)[:1200] if not isinstance(data, str) else data[:1200])
            return
        except Exception:
            pass
    # text/html preview
    print(body[:800].replace("\n", " "))


def main() -> int:
    for u in URLS:
        show(u)
    return 0


if __name__ == "__main__":
    sys.exit(main())
