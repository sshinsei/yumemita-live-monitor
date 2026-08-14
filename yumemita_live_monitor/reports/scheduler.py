"""Weekly report scheduling, backfill, and manual rebuild."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Sequence
from zoneinfo import ZoneInfo

from ..config import AppConfig
from ..models import Channel
from ..storage import SampleStore, StreamsStore
from ..utils import parse_iso, utc_now
from .weekly import generate_weekly_report
from .windows import (
    CROSS_WEEK_SAMPLE_LOOKAHEAD,
    TimeWindow,
    iso_week_window,
    list_complete_iso_weeks_until,
    parse_iso_week_label,
    previous_complete_iso_week,
)

logger = logging.getLogger("yumemita_live_monitor.reports.scheduler")


class ReportScheduler:
    def __init__(
        self,
        cfg: AppConfig,
        channels: Sequence[Channel],
        streams_store: StreamsStore,
        sample_store: SampleStore,
    ):
        self.cfg = cfg
        self.channels = list(channels)
        self.streams_store = streams_store
        self.sample_store = sample_store
        self.tz = ZoneInfo(cfg.report_timezone)
        self._last_weekly_label: Optional[str] = None
        self._last_backfill_check: Optional[datetime] = None

    def _now_local(self) -> datetime:
        return utc_now().astimezone(self.tz)

    def _parse_hhmm(self, s: str) -> tuple[int, int]:
        h, m = s.split(":")
        return int(h), int(m)

    def weekly_dir(self, label: str) -> Path:
        return self.cfg.weekly_reports_path / label

    def weekly_exists(self, label: str) -> bool:
        return (self.weekly_dir(label) / "summary.json").exists()

    def _earliest_data_utc(self) -> Optional[datetime]:
        times: List[datetime] = []
        for rec in self.streams_store.all():
            for field in (rec.discovered_at, rec.actual_start_at, rec.scheduled_start_at):
                dt = parse_iso(field)
                if dt:
                    times.append(dt)
        for month in self.sample_store.list_available_months():
            try:
                y, m = month.split("-")
                times.append(
                    datetime(int(y), int(m), 1, tzinfo=self.tz).astimezone(
                        __import__("datetime").timezone.utc
                    )
                )
            except Exception:
                pass
        if not times:
            return None
        return min(times)

    def _load_samples_for_window(self, window: TimeWindow):
        # Include a short lookahead so Sunday-night streams that run past
        # Monday 00:00 still have their Monday samples in this week's report.
        return self.sample_store.read_months(
            window.months_spanned(extra_after=CROSS_WEEK_SAMPLE_LOOKAHEAD)
        )

    def generate_week(self, window: TimeWindow) -> Path:
        samples = self._load_samples_for_window(window)
        streams = self.streams_store.all()
        out = self.weekly_dir(window.label)
        try:
            return generate_weekly_report(
                window=window,
                channels=self.channels,
                streams=streams,
                samples=samples,
                output_dir=out,
                sampling_interval_seconds=self.cfg.sampling_interval_seconds,
            )
        except Exception:
            logger.exception("Weekly report generation failed for %s", window.label)
            raise

    def backfill_missing(self) -> None:
        earliest = self._earliest_data_utc()
        now_local = self._now_local()
        if earliest is None:
            logger.info("No historical data found; skip weekly report backfill")
            return
        try:
            weeks = list_complete_iso_weeks_until(
                earliest, now_local, self.cfg.report_timezone
            )
            for w in weeks:
                if not self.weekly_exists(w.label):
                    logger.info("Backfilling weekly report %s", w.label)
                    try:
                        self.generate_week(w)
                    except Exception:
                        logger.exception("Backfill weekly %s failed", w.label)
        except Exception:
            logger.exception("Weekly backfill scan failed")

    def tick(self) -> None:
        """Called from main loop; scheduled weekly + periodic backfill."""
        now_local = self._now_local()
        try:
            wh, wm = self._parse_hhmm(self.cfg.weekly_report_time)
            if now_local.isoweekday() == self.cfg.weekly_report_day:
                if now_local.hour > wh or (
                    now_local.hour == wh and now_local.minute >= wm
                ):
                    prev = previous_complete_iso_week(
                        now_local, self.cfg.report_timezone
                    )
                    if self._last_weekly_label != prev.label and not self.weekly_exists(
                        prev.label
                    ):
                        logger.info("Scheduled weekly report for %s", prev.label)
                        self.generate_week(prev)
                        self._last_weekly_label = prev.label
                    elif self.weekly_exists(prev.label):
                        self._last_weekly_label = prev.label
        except Exception:
            logger.exception("Scheduled weekly check failed")

        now = utc_now()
        if self._last_backfill_check is None or (
            now - self._last_backfill_check
        ) > timedelta(hours=6):
            self._last_backfill_check = now
            try:
                self.backfill_missing()
            except Exception:
                logger.exception("Periodic weekly backfill failed")

    def rebuild_week(self, label: str) -> Path:
        y, w = parse_iso_week_label(label)
        window = iso_week_window(y, w, self.cfg.report_timezone)
        return self.generate_week(window)
