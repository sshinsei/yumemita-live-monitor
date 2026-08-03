"""One-off probe of FxTwitter/VxTwitter endpoints for schedule feed feasibility."""

from __future__ import annotations

import json
import sys

import requests

UA = {"User-Agent": "XScheduleDiscovery-probe/0.1"}
SESSION = requests.Session()
SESSION.headers.update(UA)

CANDIDATES = [
    "https://api.fxtwitter.com/BDP_yumemita",
    "https://api.fxtwitter.com/bdp_yumemita",
    "https://api.fxtwitter.com/BDP_yumemita/with_replies",
    "https://api.vxtwitter.com/BDP_yumemita",
    "https://api.fxtwitter.com/status/2082409548057616698",
    "https://api.fxtwitter.com/BDP_yumemita/status/2082409548057616698",
    "https://api.vxtwitter.com/status/2082409548057616698",
]


def summarize(url: str) -> None:
    print("=" * 72)
    print("GET", url)
    try:
        r = SESSION.get(url, timeout=25)
    except Exception as e:
        print("ERR", type(e).__name__, e)
        return

    print("status", r.status_code, "len", len(r.content), "ctype", r.headers.get("content-type"))
    try:
        data = r.json()
    except Exception as e:
        print("not_json", e)
        print(r.text[:500])
        return

    if isinstance(data, dict):
        print("top_keys:", list(data.keys())[:40])
        preview = json.dumps(data, ensure_ascii=False)
        print(preview[:2000])
        # dig common tweet fields
        tweet = data.get("tweet") or data.get("data") or data
        if isinstance(tweet, dict):
            for k in (
                "id",
                "url",
                "text",
                "raw_text",
                "created_at",
                "author",
                "entities",
                "quote",
            ):
                if k in tweet:
                    val = tweet[k]
                    s = json.dumps(val, ensure_ascii=False) if not isinstance(val, str) else val
                    print(f"  tweet.{k}:", s[:300])
    elif isinstance(data, list):
        print("list_len", len(data))
        if data and isinstance(data[0], dict):
            print("item0_keys", list(data[0].keys())[:40])
            print(json.dumps(data[0], ensure_ascii=False)[:1200])
    else:
        print(type(data), str(data)[:500])


def main() -> int:
    for url in CANDIDATES:
        summarize(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
