"""Auto weekly-report retention: current + previous week only."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from yumemita_live_monitor.config import AppConfig
from yumemita_live_monitor.reports.scheduler import KEEP_MARKER, ReportScheduler
from yumemita_live_monitor.reports.windows import (
    auto_retained_iso_weeks,
    current_iso_week,
    iso_week_window,
)
from yumemita_live_monitor.storage import SampleStore, StreamsStore


def _now_jst() -> datetime:
    # Friday 2026-08-14 is ISO week 33; previous complete week is 32.
    return datetime(2026, 8, 14, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


def _scheduler(tmp_path: Path) -> ReportScheduler:
    cfg = AppConfig(
        youtube_api_key="test",
        weekly_reports_dir=str(tmp_path / "weekly"),
        streams_file=str(tmp_path / "streams.csv"),
        samples_dir=str(tmp_path / "samples"),
        report_timezone="Asia/Tokyo",
    )
    return ReportScheduler(cfg, [], StreamsStore(cfg.streams_path), SampleStore(cfg.samples_path))


def _fake_week(root: Path, label: str, *, keep: bool = False) -> None:
    d = root / label
    d.mkdir(parents=True, exist_ok=True)
    (d / "summary.json").write_text("{}\n", encoding="utf-8")
    if keep:
        (d / KEEP_MARKER).write_text("", encoding="utf-8")


def test_auto_retained_weeks_are_previous_and_current():
    now = _now_jst()
    weeks = auto_retained_iso_weeks(now, "Asia/Tokyo")
    assert [w.label for w in weeks] == ["2026-W32", "2026-W33"]
    assert current_iso_week(now, "Asia/Tokyo").label == "2026-W33"


def test_prune_drops_old_auto_weeks_keeps_window_and_manual(tmp_path: Path):
    sched = _scheduler(tmp_path)
    root = sched.cfg.weekly_reports_path
    for label in ("2026-W26", "2026-W30", "2026-W31", "2026-W32", "2026-W33"):
        _fake_week(root, label)
    _fake_week(root, "2026-W26", keep=True)

    removed = sched.prune_expired_auto_reports(_now_jst())
    leftover = sorted(p.name for p in root.iterdir() if p.is_dir())
    assert set(removed) == {"2026-W30", "2026-W31"}
    assert leftover == ["2026-W26", "2026-W32", "2026-W33"]
    assert sched.is_manually_kept("2026-W26")


def test_rebuild_week_writes_keep_marker(tmp_path: Path):
    sched = _scheduler(tmp_path)
    out = sched.rebuild_week("2026-W26")
    assert (out / "summary.json").exists()
    assert (out / KEEP_MARKER).exists()
    removed = sched.prune_expired_auto_reports(_now_jst())
    assert "2026-W26" not in removed
    assert sched.weekly_exists("2026-W26")


def test_maintain_only_creates_current_and_previous(tmp_path: Path):
    sched = _scheduler(tmp_path)
    _fake_week(sched.cfg.weekly_reports_path, "2026-W27")
    sched.maintain_auto_reports(_now_jst())
    leftover = sorted(p.name for p in sched.cfg.weekly_reports_path.iterdir() if p.is_dir())
    assert leftover == ["2026-W32", "2026-W33"]
    assert sched.weekly_exists("2026-W32")
    assert sched.weekly_exists("2026-W33")


def test_auto_generate_does_not_mark_keep(tmp_path: Path):
    sched = _scheduler(tmp_path)
    window = iso_week_window(2026, 33, "Asia/Tokyo")
    sched.generate_week(window)
    assert not sched.is_manually_kept("2026-W33")
