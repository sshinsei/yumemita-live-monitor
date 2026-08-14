"""Weekly report generation tests."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from yumemita_live_monitor.models import Channel, StreamRecord, ViewerSample
from yumemita_live_monitor.reports.stats import build_member_stats
from yumemita_live_monitor.reports.weekly import generate_weekly_report
from yumemita_live_monitor.reports.windows import (
    CROSS_WEEK_SAMPLE_LOOKAHEAD,
    iso_week_window,
)


def _channels():
    return [
        Channel("arale", "仲町あられ", "UCa", True, "#FFEE55"),
        Channel("nonoka", "宮永ののか", "UCb", True, "#FFBBCC"),
    ]


def _data():
    streams = [
        StreamRecord(
            "v1",
            "UCa",
            "arale",
            "仲町あられ",
            title="歌枠",
            status="ended",
            actual_start_at="2026-07-28T10:00:00Z",
            actual_end_at="2026-07-28T11:00:00Z",
            peak_concurrent_viewers=300,
        ),
        StreamRecord(
            "v2",
            "UCb",
            "nonoka",
            "宮永ののか",
            title="雑談",
            status="ended",
            actual_start_at="2026-07-28T10:30:00Z",
            actual_end_at="2026-07-28T11:30:00Z",
            peak_concurrent_viewers=150,
        ),
    ]
    samples = [
        ViewerSample("2026-07-28T10:00:00Z", "v1", "UCa", "arale", "仲町あられ", 100),
        ViewerSample("2026-07-28T10:01:00Z", "v1", "UCa", "arale", "仲町あられ", 300),
        ViewerSample("2026-07-28T10:30:00Z", "v2", "UCb", "nonoka", "宮永ののか", 150),
    ]
    return streams, samples


def _overnight_stream():
    """Sunday 23:00 JST → Monday 02:00 JST (2026-W31 / W32 boundary).

    W31 ends 2026-08-03 00:00 JST = 2026-08-02T15:00:00Z.
    """
    rec = StreamRecord(
        "overnight",
        "UCa",
        "arale",
        "仲町あられ",
        title="深夜雑談",
        status="ended",
        actual_start_at="2026-08-02T14:00:00Z",  # Sun 23:00 JST
        actual_end_at="2026-08-02T17:00:00Z",  # Mon 02:00 JST
        peak_concurrent_viewers=300,
    )
    samples = [
        ViewerSample("2026-08-02T14:00:00Z", "overnight", "UCa", "arale", "仲町あられ", 100),
        ViewerSample("2026-08-02T14:30:00Z", "overnight", "UCa", "arale", "仲町あられ", 200),
        ViewerSample("2026-08-02T15:30:00Z", "overnight", "UCa", "arale", "仲町あられ", 300),
        ViewerSample("2026-08-02T16:30:00Z", "overnight", "UCa", "arale", "仲町あられ", 250),
    ]
    return rec, samples


def test_weekly_idempotent(tmp_path: Path):
    streams, samples = _data()
    # 2026-07-28 is Tuesday of ISO week 31
    window = iso_week_window(2026, 31, "Asia/Tokyo")
    out = tmp_path / "2026-W31"
    generate_weekly_report(
        window=window,
        channels=_channels(),
        streams=streams,
        samples=samples,
        output_dir=out,
        sampling_interval_seconds=60,
    )
    assert (out / "summary.json").exists()
    assert (out / "arale.html").exists()
    assert (out / "nonoka.html").exists()
    generate_weekly_report(
        window=window,
        channels=_channels(),
        streams=streams,
        samples=samples,
        output_dir=out,
        sampling_interval_seconds=60,
    )
    html2 = (out / "arale.html").read_text(encoding="utf-8")
    assert "仲町あられ" in html2
    assert "本程序采集峰值" in html2
    assert len(list(out.glob("*.html"))) == 2


def test_weekly_empty_member_still_written(tmp_path: Path):
    window = iso_week_window(2026, 31, "Asia/Tokyo")
    out = tmp_path / "2026-W31"
    channels = _channels() + [
        Channel("ritsu", "峰月律", "UCc", True, "#4477CC"),
    ]
    streams, samples = _data()
    generate_weekly_report(
        window=window,
        channels=channels,
        streams=streams,
        samples=samples,
        output_dir=out,
    )
    ritsu = (out / "ritsu.html").read_text(encoding="utf-8")
    assert "本周无直播" in ritsu


def test_overnight_stream_stays_in_start_week():
    rec, samples = _overnight_stream()
    w31 = iso_week_window(2026, 31, "Asia/Tokyo")
    w32 = iso_week_window(2026, 32, "Asia/Tokyo")

    start_week = build_member_stats(
        member_key="arale",
        member_name="仲町あられ",
        color="#FFEE55",
        streams=[rec],
        samples=samples,
        window=w31,
        sampling_interval_seconds=1800,
    )
    next_week = build_member_stats(
        member_key="arale",
        member_name="仲町あられ",
        color="#FFEE55",
        streams=[rec],
        samples=samples,
        window=w32,
        sampling_interval_seconds=1800,
    )

    assert start_week.stream_count == 1
    assert start_week.streams[0].video_id == "overnight"
    assert start_week.streams[0].duration_seconds == 3 * 3600
    assert start_week.streams[0].sample_count == 4
    assert start_week.peak_concurrent_viewers == 300
    assert next_week.stream_count == 0
    assert next_week.sample_count == 0


def test_overnight_stream_without_start_uses_first_sample():
    rec, samples = _overnight_stream()
    rec.actual_start_at = ""
    rec.actual_end_at = ""
    w31 = iso_week_window(2026, 31, "Asia/Tokyo")
    w32 = iso_week_window(2026, 32, "Asia/Tokyo")

    start_week = build_member_stats(
        member_key="arale",
        member_name="仲町あられ",
        color="#FFEE55",
        streams=[rec],
        samples=samples,
        window=w31,
        sampling_interval_seconds=1800,
    )
    next_week = build_member_stats(
        member_key="arale",
        member_name="仲町あられ",
        color="#FFEE55",
        streams=[rec],
        samples=samples,
        window=w32,
        sampling_interval_seconds=1800,
    )

    assert start_week.stream_count == 1
    assert start_week.sample_count == 4
    assert next_week.stream_count == 0


def test_monday_start_belongs_to_new_week():
    rec = StreamRecord(
        "monday",
        "UCa",
        "arale",
        "仲町あられ",
        title="朝活",
        status="ended",
        actual_start_at="2026-08-02T15:30:00Z",  # Mon 00:30 JST
        actual_end_at="2026-08-02T16:30:00Z",
    )
    samples = [
        ViewerSample("2026-08-02T15:30:00Z", "monday", "UCa", "arale", "仲町あられ", 80),
        ViewerSample("2026-08-02T16:00:00Z", "monday", "UCa", "arale", "仲町あられ", 90),
    ]
    w31 = iso_week_window(2026, 31, "Asia/Tokyo")
    w32 = iso_week_window(2026, 32, "Asia/Tokyo")

    prev = build_member_stats(
        member_key="arale",
        member_name="仲町あられ",
        color="#FFEE55",
        streams=[rec],
        samples=samples,
        window=w31,
    )
    curr = build_member_stats(
        member_key="arale",
        member_name="仲町あられ",
        color="#FFEE55",
        streams=[rec],
        samples=samples,
        window=w32,
    )
    assert prev.stream_count == 0
    assert curr.stream_count == 1
    assert curr.streams[0].duration_seconds == 3600


def test_overnight_stream_html_not_split(tmp_path: Path):
    rec, samples = _overnight_stream()
    w31 = iso_week_window(2026, 31, "Asia/Tokyo")
    w32 = iso_week_window(2026, 32, "Asia/Tokyo")
    generate_weekly_report(
        window=w31,
        channels=_channels(),
        streams=[rec],
        samples=samples,
        output_dir=tmp_path / "2026-W31",
        sampling_interval_seconds=1800,
    )
    generate_weekly_report(
        window=w32,
        channels=_channels(),
        streams=[rec],
        samples=samples,
        output_dir=tmp_path / "2026-W32",
        sampling_interval_seconds=1800,
    )
    summary31 = json.loads((tmp_path / "2026-W31" / "summary.json").read_text(encoding="utf-8"))
    summary32 = json.loads((tmp_path / "2026-W32" / "summary.json").read_text(encoding="utf-8"))
    arale31 = next(m for m in summary31["members"] if m["member_key"] == "arale")
    arale32 = next(m for m in summary32["members"] if m["member_key"] == "arale")
    assert arale31["stream_count"] == 1
    assert arale31["peak_concurrent_viewers"] == 300
    assert arale31["streams"][0]["duration_seconds"] == 3 * 3600
    assert arale32["stream_count"] == 0
    html31 = (tmp_path / "2026-W31" / "arale.html").read_text(encoding="utf-8")
    assert "跨周不拆分" in html31


def test_months_spanned_includes_lookahead_month():
    # W35 2026: Mon Aug 24 – Mon Aug 31 00:00 JST (= Aug 30 15:00 UTC)
    window = iso_week_window(2026, 35, "Asia/Tokyo")
    assert window.months_spanned() == ["2026-08"]
    # 48h past the boundary reaches September UTC
    months = window.months_spanned(extra_after=CROSS_WEEK_SAMPLE_LOOKAHEAD)
    assert "2026-08" in months
    assert "2026-09" in months
    assert window.months_spanned(extra_after=timedelta(0)) == ["2026-08"]
