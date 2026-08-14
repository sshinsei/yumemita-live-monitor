"""Weekly report scheduling, retention, and manual rebuild."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Sequence
from zoneinfo import ZoneInfo

from ..config import AppConfig
from ..models import Channel
from ..storage import SampleStore, StreamsStore
from ..utils import utc_now
from .weekly import generate_weekly_report
from .windows import (
    CROSS_WEEK_SAMPLE_LOOKAHEAD,
    TimeWindow,
    auto_retained_iso_weeks,
    iso_week_window,
    parse_iso_week_label,
    previous_complete_iso_week,
)

# Sidecar written by `report --week`. Auto prune leaves these directories alone.
KEEP_MARKER = ".keep"

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
        self._last_maintain_check: Optional[datetime] = None

    def _now_local(self) -> datetime:
        return utc_now().astimezone(self.tz)

    def _parse_hhmm(self, s: str) -> tuple[int, int]:
        h, m = s.split(":")
        return int(h), int(m)

    def weekly_dir(self, label: str) -> Path:
        return self.cfg.weekly_reports_path / label

    def weekly_exists(self, label: str) -> bool:
        return (self.weekly_dir(label) / "summary.json").exists()

    def is_manually_kept(self, label: str) -> bool:
        return (self.weekly_dir(label) / KEEP_MARKER).exists()

    def _mark_kept(self, label: str) -> None:
        marker = self.weekly_dir(label) / KEEP_MARKER
        if not marker.exists():
            marker.write_text("", encoding="utf-8")

    def _load_samples_for_window(self, window: TimeWindow):
        # Include a short lookahead so Sunday-night streams that run past
        # Monday 00:00 still have their Monday samples in this week's report.
        return self.sample_store.read_months(
            window.months_spanned(extra_after=CROSS_WEEK_SAMPLE_LOOKAHEAD)
        )

    def generate_week(self, window: TimeWindow, *, keep: bool = False) -> Path:
        samples = self._load_samples_for_window(window)
        streams = self.streams_store.all()
        out = self.weekly_dir(window.label)
        try:
            generate_weekly_report(
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
        if keep:
            self._mark_kept(window.label)
        return out

    def prune_expired_auto_reports(
        self, now_local: Optional[datetime] = None
    ) -> List[str]:
        """Delete auto weeks outside current + previous. Manual `.keep` stays."""
        now_local = now_local or self._now_local()
        keep_labels = {
            w.label
            for w in auto_retained_iso_weeks(now_local, self.cfg.report_timezone)
        }
        root = self.cfg.weekly_reports_path
        if not root.exists():
            return []
        removed: List[str] = []
        for child in root.iterdir():
            if not child.is_dir():
                continue
            label = child.name
            try:
                parse_iso_week_label(label)
            except ValueError:
                continue
            if label in keep_labels:
                continue
            if (child / KEEP_MARKER).exists():
                continue
            if not (child / "summary.json").exists():
                continue
            logger.info("Removing expired auto weekly report %s", label)
            shutil.rmtree(child)
            removed.append(label)
        return removed

    def maintain_auto_reports(self, now_local: Optional[datetime] = None) -> None:
        """Keep current + previous week on disk; drop older auto reports."""
        now_local = now_local or self._now_local()
        retained = auto_retained_iso_weeks(now_local, self.cfg.report_timezone)
        previous, current = retained[0], retained[1]
        try:
            logger.info("Refreshing current weekly report %s", current.label)
            self.generate_week(current)
        except Exception:
            logger.exception("Auto weekly %s failed", current.label)
        if not self.weekly_exists(previous.label):
            try:
                logger.info("Generating previous weekly report %s", previous.label)
                self.generate_week(previous)
            except Exception:
                logger.exception("Auto weekly %s failed", previous.label)
        try:
            self.prune_expired_auto_reports(now_local)
        except Exception:
            logger.exception("Weekly report prune failed")

    def tick(self) -> None:
        """Called from main loop; scheduled previous week + retain current/previous."""
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
        if self._last_maintain_check is None or (
            now - self._last_maintain_check
        ) > timedelta(hours=6):
            self._last_maintain_check = now
            try:
                self.maintain_auto_reports(now_local)
            except Exception:
                logger.exception("Periodic weekly report maintenance failed")

    def rebuild_week(self, label: str) -> Path:
        """Manual rebuild. The week is kept even after it falls out of auto retention."""
        y, w = parse_iso_week_label(label)
        window = iso_week_window(y, w, self.cfg.report_timezone)
        return self.generate_week(window, keep=True)
