"""Offline schedule parser tests (ticket 02)."""

from __future__ import annotations

from pathlib import Path

from x_schedule_monitor.schedule_parser import (
    extract_video_id,
    is_schedule_post,
    parse_schedule_post,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "schedule_posts"


def _parse(
    name: str,
    created: str = "2026-02-20T12:00:00Z",
    post_id: str = "p1",
    *,
    member_only_enabled: bool = False,
):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return parse_schedule_post(
        text,
        source_post_id=post_id,
        source_post_created_at=created,
        fetched_at="2026-02-20T12:05:00Z",
        member_only_enabled=member_only_enabled,
    )


def test_standard_multi_member():
    r = _parse("standard_multi.txt", member_only_enabled=True)
    assert r.is_schedule_post
    assert r.schedule_date == "2026-02-21"
    assert len(r.hints) == 2
    by_key = {h.member_key: h for h in r.hints}
    assert "arale" in by_key
    assert "miyako" in by_key
    assert by_key["miyako"].member_only is True
    assert by_key["arale"].member_only is False
    # JST 22:00 on 2026-02-21 = 13:00 UTC
    assert by_key["arale"].planned_start_at == "2026-02-21T13:00:00Z"
    assert by_key["miyako"].planned_start_at == "2026-02-21T13:30:00Z"
    assert by_key["arale"].youtube_video_id == ""
    assert "arale" in by_key["arale"].youtube_url or "@arale" in by_key["arale"].youtube_url


def test_standard_multi_member_only_switch_off():
    r = _parse("standard_multi.txt", member_only_enabled=False)
    by_key = {h.member_key: h for h in r.hints}
    assert by_key["miyako"].member_only is False
    assert by_key["miyako"].member_key == "miyako"


def test_video_ids():
    r = _parse("with_video_ids.txt", created="2026-07-28T12:00:00Z")
    assert r.schedule_date == "2026-07-29"
    assert len(r.hints) == 3
    vids = {h.member_key: h.youtube_video_id for h in r.hints}
    assert vids["nonoka"] == "dQw4w9WgXcQ"
    assert vids["ritsu"] == "abcdefghijk"
    assert vids["yuno"] == "lmnopqrstuv"


def test_fullwidth_numbers():
    r = _parse("fullwidth.txt", created="2026-12-30T12:00:00Z")
    assert r.is_schedule_post
    assert r.schedule_date == "2026-12-31"
    assert len(r.hints) == 1
    assert r.hints[0].member_key == "arale"
    # JST 22:00 Dec 31 = 13:00 UTC
    assert r.hints[0].planned_start_at == "2026-12-31T13:00:00Z"


def test_not_schedule():
    r = _parse("not_schedule.txt")
    assert r.is_schedule_post is False
    assert r.hints == []


def test_unknown_member_warning():
    text = """#夢限大みゅーたいぷ 3/1(日)の配信スケジュール
⭐20:00～ 誰かさん
⭐21:00～ 仲町あられ
"""
    r = parse_schedule_post(
        text,
        source_post_id="p2",
        source_post_created_at="2026-03-01T01:00:00Z",
        fetched_at="2026-03-01T01:01:00Z",
    )
    assert r.is_schedule_post
    assert len(r.hints) == 1
    assert r.hints[0].member_key == "arale"
    assert any("unknown member" in w.message for w in r.warnings)


def test_invalid_time_does_not_abort_post():
    text = """#夢限大みゅーたいぷ 3/2(月)の配信スケジュール
⭐99:00～ 仲町あられ
⭐21:00～ 峰月律
"""
    r = parse_schedule_post(
        text,
        source_post_id="p3",
        source_post_created_at="2026-03-01T12:00:00Z",
        fetched_at="2026-03-01T12:01:00Z",
    )
    assert len(r.hints) == 1
    assert r.hints[0].member_key == "ritsu"
    assert r.warnings


def test_cross_year_jan_after_dec_post():
    text = """#夢限大みゅーたいぷ 1/2(金)の配信スケジュール
⭐22:00～ 千石ユノ
"""
    # Posted Dec 31 2026 JST about Jan 2 schedule
    r = parse_schedule_post(
        text,
        source_post_id="p4",
        source_post_created_at="2026-12-31T03:00:00Z",
        fetched_at="2026-12-31T03:01:00Z",
    )
    assert r.schedule_date == "2027-01-02"


def test_weekday_mismatch_warns():
    # 2/21/2026 is Saturday (土); claim 月
    text = """#夢限大みゅーたいぷ 2/21(月)の配信スケジュール
⭐22:00～ 仲町あられ
"""
    r = parse_schedule_post(
        text,
        source_post_id="p5",
        source_post_created_at="2026-02-20T12:00:00Z",
        fetched_at="2026-02-20T12:01:00Z",
    )
    assert any("weekday mismatch" in w.message for w in r.warnings)


def test_extract_video_id():
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://youtu.be/abcdefghijk") == "abcdefghijk"
    assert extract_video_id("https://www.youtube.com/live/lmnopqrstuv") == "lmnopqrstuv"
    assert extract_video_id("https://www.youtube.com/@arale_yumemita") == ""


def test_is_schedule_post():
    assert is_schedule_post((FIXTURES / "standard_multi.txt").read_text(encoding="utf-8"))
    assert not is_schedule_post((FIXTURES / "not_schedule.txt").read_text(encoding="utf-8"))
