"""Weekly report generation tests."""

from __future__ import annotations

from pathlib import Path

from x_schedule_monitor.models import Channel, StreamRecord, ViewerSample
from x_schedule_monitor.reports.weekly import generate_weekly_report
from x_schedule_monitor.reports.windows import iso_week_window


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
