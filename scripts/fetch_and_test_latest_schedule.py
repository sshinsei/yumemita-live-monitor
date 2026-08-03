"""Fetch latest BDP_yumemita schedule post via FxTwitter and parse with switch off."""

from __future__ import annotations

import json
import sys
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from x_schedule_monitor.config import validate_config_dict
from x_schedule_monitor.schedule_parser import is_schedule_post, parse_schedule_post
from x_schedule_monitor.utils import format_utc

# Latest schedule post ID from search (2026-07-31)
POST_ID = "2083138098939179188"
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    url = f"https://api.fxtwitter.com/status/{POST_ID}"
    r = requests.get(
        url,
        timeout=25,
        headers={"User-Agent": "XScheduleDiscovery-fetch/0.1"},
    )
    print("fxtwitter", r.status_code, url)
    data = r.json()
    if data.get("code") != 200:
        print("FAIL body", data)
        return 1

    tweet = data["tweet"]
    text = tweet["text"]
    created_raw = tweet.get("created_at") or ""
    print("id", tweet.get("id"))
    print("created_at", created_raw)
    print("author", (tweet.get("author") or {}).get("screen_name"))
    print("=== TEXT ===")
    print(text)
    print("=== END ===")

    fix_dir = ROOT / "fixtures" / "schedule_posts"
    fix_dir.mkdir(parents=True, exist_ok=True)
    (fix_dir / "live_latest_fetched.txt").write_text(text + "\n", encoding="utf-8")
    (fix_dir / "live_latest_fetched.meta.json").write_text(
        json.dumps(
            {
                "post_id": tweet.get("id"),
                "created_at_raw": created_raw,
                "url": tweet.get("url"),
                "fetched_via": url,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    cfg_data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    cfg_data["youtube_api_key"] = "test-key-not-placeholder-xx"
    cfg = validate_config_dict(cfg_data)
    assert cfg.x_schedule_member_only_enabled is False
    print("member_only_enabled", cfg.x_schedule_member_only_enabled)

    try:
        created_iso = format_utc(parsedate_to_datetime(created_raw))
    except Exception:
        created_iso = "2026-07-31T10:30:00Z"
    print("created_iso", created_iso)
    print("is_schedule_post", is_schedule_post(text))

    result = parse_schedule_post(
        text,
        source_post_id=str(tweet.get("id")),
        source_post_created_at=created_iso,
        fetched_at="2026-07-31T12:00:00Z",
        member_only_enabled=cfg.x_schedule_member_only_enabled,
    )
    print("header schedule_date", result.schedule_date)
    print("hints", len(result.hints), "warnings", len(result.warnings))
    for h in result.hints:
        print(
            f"  member={h.member_key:8} only={h.member_only!s:5} "
            f"date={h.schedule_date} start={h.planned_start_at}"
        )
        print(f"    url={h.youtube_url or '-'} vid={h.youtube_video_id or '-'}")
        print(f"    raw={h.raw_text!r}")
    for w in result.warnings:
        print("  WARN", w.message, "|", w.raw_line)

    ok = len(result.hints) >= 1 and all(not h.member_only for h in result.hints)
    print("PASS" if ok else "FAIL", "all member_only False and at least 1 hint")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
