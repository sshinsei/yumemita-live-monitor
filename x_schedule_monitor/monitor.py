"""Main monitoring loop: per-member discovery + sampling + X schedule refresh."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .channels import enabled_channels
from .config import AppConfig
from .discovery import StreamDiscoveryService, build_discovery_strategy
from .discovery_policy import (
    decide_all_members,
    desired_x_refresh_interval_seconds,
    sampling_interval_seconds,
)
from .utils import format_utc, parse_iso, utc_now
from .models import Channel, StreamRecord
from .reports.scheduler import ReportScheduler
from .sampler import ViewerSampler
from .schedule import SamplingProfile, resolve_profile
from .schedule_feed import ScheduleFeedService
from .schedule_store import ScheduleHintStore
from .state import StateStore
from .storage import SampleStore, StreamsStore
from .youtube_client import YouTubeClient

logger = logging.getLogger("x_schedule_monitor.monitor")


class ViewerMonitor:
    def __init__(self, cfg: AppConfig, channels: List[Channel]):
        self.cfg = cfg
        self.channels = channels
        self.enabled = enabled_channels(channels)
        self.channel_map: Dict[str, Channel] = {c.channel_id: c for c in self.enabled}

        self.client = YouTubeClient(
            cfg.youtube_api_key,
            timeout=cfg.request_timeout_seconds,
            max_retries=cfg.max_retries,
        )
        strategy = build_discovery_strategy(cfg, self.client)
        self.discovery = StreamDiscoveryService(
            self.client, strategy, cfg, self.channel_map
        )
        self.sampler = ViewerSampler(self.client, cfg, self.channel_map)

        self.streams_store = StreamsStore(cfg.streams_path)
        self.sample_store = SampleStore(cfg.samples_path)
        self.state_store = StateStore(cfg.state_path)
        self.state_store.load(streams_store=self.streams_store)

        self.hint_store = ScheduleHintStore(cfg.schedule_hints_path)
        self.schedule_feed = ScheduleFeedService(cfg, self.hint_store)
        self.report_scheduler = ReportScheduler(
            cfg, self.channels, self.streams_store, self.sample_store
        )

        self._stop = False
        # Per-member next discovery due (UTC datetime); missing => due now
        self._member_next_discovery: Dict[str, Optional[datetime]] = {
            c.member_key: None for c in self.enabled
        }
        self._next_sample_at: Optional[datetime] = None
        self._next_x_refresh_at: Optional[datetime] = None  # None => immediate if enabled
        self._last_schedule_band: Optional[str] = None

    def request_stop(self, *_args) -> None:
        logger.info("Stop requested; will exit after current cycle")
        self._stop = True

    def current_profile(self, now: Optional[datetime] = None) -> SamplingProfile:
        """Resolve sampling interval for current Tokyo time band (not discovery)."""
        now = now or utc_now()
        if not self.cfg.schedule_enabled:
            return SamplingProfile(
                name="legacy",
                sampling_seconds=self.cfg.sampling_interval_seconds,
            )
        profile = resolve_profile(
            self.cfg.time_bands,
            self.cfg.off_peak,
            now_utc=now,
            tz_name=self.cfg.schedule_timezone,
        )
        if profile.name != self._last_schedule_band:
            logger.info(
                "Schedule band=%s sampling=%ss",
                profile.name,
                profile.sampling_seconds,
            )
            self._last_schedule_band = profile.name
        return profile

    def _persist(self) -> None:
        try:
            self.streams_store.save()
        except Exception:
            logger.exception("Failed to save streams.csv")
        try:
            self.state_store.save()
        except Exception:
            logger.exception("Failed to save runtime state")

    def _known_records(self) -> Dict[str, StreamRecord]:
        known = dict(self.streams_store.records)
        for vid, st in self.state_store.state.streams.items():
            if vid not in known and st.status in {"live", "upcoming"}:
                known[vid] = StreamRecord(
                    video_id=st.video_id,
                    channel_id=st.channel_id,
                    member_key=st.member_key,
                    member_name=st.member_name,
                    title=st.title,
                    status=st.status,
                    scheduled_start_at=st.scheduled_start_at,
                    actual_start_at=st.actual_start_at,
                    actual_end_at=st.actual_end_at,
                    discovered_at=st.discovered_at,
                    last_seen_at=st.last_seen_at,
                    peak_concurrent_viewers=st.peak_concurrent_viewers,
                )
        return known

    def _active_stream_list(self) -> List[StreamRecord]:
        known = self._known_records()
        return [r for r in known.values() if r.status in {"live", "upcoming"}]

    def _x_refresh_interval_now(self, now: Optional[datetime] = None) -> int:
        """Use active-band 30min cadence when any member is in unscheduled probe."""
        now = now or utc_now()
        decisions = decide_all_members(
            self.enabled,
            streams=self._active_stream_list(),
            hints=self.hint_store.active(),
            cfg=self.cfg,
            now=now,
        )
        return desired_x_refresh_interval_seconds(decisions, self.cfg)

    def run_x_refresh(self) -> None:
        if not self.cfg.x_schedule_enabled:
            self._next_x_refresh_at = utc_now() + timedelta(days=3650)
            return
        logger.info("=== X schedule refresh ===")
        since_id = self.state_store.state.last_x_since_id or None
        stats = self.schedule_feed.refresh(since_id=since_id)
        now = utc_now()
        now_iso = format_utc(now)
        self.state_store.state.last_x_refresh_at = now_iso
        if stats.max_post_id:
            self.state_store.state.last_x_since_id = stats.max_post_id
        if stats.error:
            logger.warning("X refresh error: %s", stats.error)
        iv = self._x_refresh_interval_now(now)
        self._next_x_refresh_at = now + timedelta(seconds=iv)
        logger.info("Next X schedule refresh in %ss reason=active_band_x_refresh_or_default", iv)
        try:
            self.state_store.save()
        except Exception:
            logger.exception("Failed to save state after X refresh")

    def run_discovery_for_members(self, member_keys: List[str]) -> None:
        if not member_keys:
            return
        logger.info("=== Discovery for members: %s ===", ",".join(member_keys))
        known = self._known_records()
        extra_ids = self.hint_store.video_id_candidates()
        # Prefer video_ids from due members' hints
        due_set = set(member_keys)
        member_extra = [
            h.youtube_video_id
            for h in self.hint_store.active()
            if h.member_key in due_set and h.youtube_video_id
        ]
        extra = list(dict.fromkeys(member_extra + extra_ids))

        records, errors = self.discovery.discover(
            self.enabled,
            known,
            extra_video_ids=extra,
            only_member_keys=member_keys,
        )
        now = utc_now()
        now_iso = format_utc(now)
        self.state_store.state.last_discovery_at = now_iso

        for rec in records:
            merged = self.streams_store.upsert(rec)
            self.state_store.upsert_stream(merged)
            if merged.status == "ended":
                st = self.state_store.state.streams.get(merged.video_id)
                if st:
                    st.status = "ended"

        for mk in member_keys:
            self.state_store.state.member_last_discovery_at[mk] = now_iso

        self._persist()
        if errors:
            logger.warning("Discovery finished with %d errors", len(errors))
        else:
            logger.info("Discovery finished OK (%d updates)", len(records))

        # Reschedule each due member independently
        streams = self._active_stream_list()
        hints = self.hint_store.active()
        decisions = decide_all_members(
            self.enabled,
            streams=streams,
            hints=hints,
            cfg=self.cfg,
            now=now,
        )
        for mk in member_keys:
            d = decisions.get(mk)
            if d is None:
                continue
            if d.next_run_at is not None:
                next_at = d.next_run_at
            else:
                next_at = now + timedelta(seconds=d.interval_seconds)
            self._member_next_discovery[mk] = next_at
            wait_s = max(0, int((next_at - now).total_seconds()))
            logger.info(
                "Next discovery member=%s in %ss (interval=%ss) mode=%s reason=%s "
                "anchor=%s next_at=%s",
                mk,
                wait_s,
                d.interval_seconds,
                d.mode,
                d.reason,
                d.anchor_source,
                format_utc(next_at),
            )

        # Pull X refresh earlier when members need active-band X cadence
        if self.cfg.x_schedule_enabled:
            desired_iv = desired_x_refresh_interval_seconds(decisions, self.cfg)
            last = parse_iso(self.state_store.state.last_x_refresh_at)
            if last is None:
                # No refresh yet; leave existing deadline (or due immediately)
                pass
            else:
                earliest = last + timedelta(seconds=desired_iv)
                if self._next_x_refresh_at is None or self._next_x_refresh_at > earliest:
                    self._next_x_refresh_at = earliest

    def due_members(self, now: Optional[datetime] = None) -> List[str]:
        now = now or utc_now()
        due: List[str] = []
        for ch in self.enabled:
            nxt = self._member_next_discovery.get(ch.member_key)
            if nxt is None or nxt <= now:
                due.append(ch.member_key)
        return due

    def run_sampling(self) -> None:
        live_ids = self.state_store.active_live_ids()
        for rec in self.streams_store.all():
            if rec.status == "live" and rec.video_id not in live_ids:
                live_ids.append(rec.video_id)

        if not live_ids:
            logger.debug("No live streams to sample")
            return

        live_records: List[StreamRecord] = []
        for vid in live_ids:
            rec = self.streams_store.get(vid)
            if rec is None:
                st = self.state_store.state.streams.get(vid)
                if st:
                    rec = StreamRecord(
                        video_id=st.video_id,
                        channel_id=st.channel_id,
                        member_key=st.member_key,
                        member_name=st.member_name,
                        title=st.title,
                        status="live",
                        scheduled_start_at=st.scheduled_start_at,
                        actual_start_at=st.actual_start_at,
                        actual_end_at=st.actual_end_at,
                        discovered_at=st.discovered_at,
                        last_seen_at=st.last_seen_at,
                        peak_concurrent_viewers=st.peak_concurrent_viewers,
                    )
            if rec:
                live_records.append(rec)

        logger.info("=== Sampling %d live video(s) ===", len(live_records))
        samples, results, meta_updates = self.sampler.sample_live(live_records)

        for rec in meta_updates:
            merged = self.streams_store.upsert(rec)
            self.state_store.upsert_stream(merged)

        for sample in samples:
            try:
                self.sample_store.append(sample)
                self.state_store.mark_sample_success(
                    sample.video_id, sample.sampled_at, sample.concurrent_viewers
                )
                self.streams_store.update_peak(
                    sample.video_id, sample.concurrent_viewers
                )
            except Exception:
                logger.exception("Failed to append sample for %s", sample.video_id)

        for result in results:
            if not result.success:
                self.state_store.mark_sample_error(result.video_id, result.reason)

        self._persist()

    def run_forever(self) -> None:
        logger.info(
            "Starting monitor: %d enabled channels, x_schedule_enabled=%s",
            len(self.enabled),
            self.cfg.x_schedule_enabled,
        )
        # Immediate first cycles
        try:
            if self.cfg.x_schedule_enabled:
                self.run_x_refresh()
            due = self.due_members()
            self.run_discovery_for_members(due)
            self.run_sampling()
            self._next_sample_at = utc_now() + timedelta(
                seconds=sampling_interval_seconds(self.cfg)
            )
        except Exception:
            logger.exception("Startup cycle error (continuing loop)")

        while not self._stop:
            now = utc_now()
            try:
                if self.cfg.x_schedule_enabled:
                    if (
                        self._next_x_refresh_at is None
                        or now >= self._next_x_refresh_at
                    ):
                        self.run_x_refresh()

                due = self.due_members(now)
                if due:
                    self.run_discovery_for_members(due)

                if self._next_sample_at is None or now >= self._next_sample_at:
                    self.run_sampling()
                    self._next_sample_at = utc_now() + timedelta(
                        seconds=sampling_interval_seconds(self.cfg)
                    )

                try:
                    self.report_scheduler.tick()
                except Exception:
                    logger.exception("Weekly report tick failed (non-fatal)")
            except Exception:
                logger.exception(
                    "Loop cycle error; YouTube/X isolation — sleeping briefly"
                )

            # Sleep until next interesting deadline
            now = utc_now()
            deadlines: List[datetime] = []
            if self._next_sample_at:
                deadlines.append(self._next_sample_at)
            if self.cfg.x_schedule_enabled and self._next_x_refresh_at:
                deadlines.append(self._next_x_refresh_at)
            for mk, nxt in self._member_next_discovery.items():
                if nxt is not None:
                    deadlines.append(nxt)
            if deadlines:
                sleep_s = max(1.0, min((d - now).total_seconds() for d in deadlines))
                sleep_s = min(sleep_s, 60.0)  # wake at least every 60s to check stop
            else:
                sleep_s = 5.0
            # Interruptible sleep
            end = time.time() + sleep_s
            while not self._stop and time.time() < end:
                time.sleep(min(0.5, end - time.time()))

        logger.info("Monitor stopped")
        self.sample_store.close()
        self.client.close()
