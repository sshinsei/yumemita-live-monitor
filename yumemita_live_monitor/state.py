"""Runtime state persistence and recovery."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .models import RuntimeState, RuntimeStreamState, StreamRecord
from .storage import StreamsStore
from .utils import atomic_write_text, ensure_dir

logger = logging.getLogger("yumemita_live_monitor.state")

STATE_VERSION = 1


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        ensure_dir(self.path.parent)
        self.state = RuntimeState(version=STATE_VERSION)

    def load(self, *, streams_store: Optional[StreamsStore] = None) -> RuntimeState:
        if not self.path.exists():
            logger.info("No runtime state file at %s; starting fresh", self.path)
            if streams_store is not None:
                self.recover_from_streams(streams_store)
            return self.state

        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("state root is not an object")
            self.state = RuntimeState.from_dict(data)
            logger.info(
                "Loaded runtime state: %d streams, last_discovery=%s",
                len(self.state.streams),
                self.state.last_discovery_at or "-",
            )
            return self.state
        except Exception as e:
            logger.error(
                "Runtime state file damaged (%s): %s. Attempting recovery.",
                self.path,
                e,
            )
            self.state = RuntimeState(version=STATE_VERSION)
            if streams_store is not None:
                self.recover_from_streams(streams_store)
            return self.state

    def recover_from_streams(self, streams_store: StreamsStore) -> RuntimeState:
        recovered = 0
        for rec in streams_store.all():
            if rec.status in {"live", "upcoming"}:
                self.state.streams[rec.video_id] = RuntimeStreamState.from_stream_record(
                    rec
                )
                recovered += 1
        logger.info("Recovered %d active/upcoming streams from streams.csv", recovered)
        return self.state

    def save(self) -> None:
        payload = json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2)
        atomic_write_text(self.path, payload + "\n")

    def upsert_stream(self, rec: StreamRecord) -> RuntimeStreamState:
        existing = self.state.streams.get(rec.video_id)
        if existing is None:
            st = RuntimeStreamState.from_stream_record(rec)
            self.state.streams[rec.video_id] = st
            return st
        existing.status = rec.status or existing.status
        existing.title = rec.title or existing.title
        existing.channel_id = rec.channel_id or existing.channel_id
        existing.member_key = rec.member_key or existing.member_key
        existing.member_name = rec.member_name or existing.member_name
        existing.scheduled_start_at = rec.scheduled_start_at or existing.scheduled_start_at
        existing.actual_start_at = rec.actual_start_at or existing.actual_start_at
        existing.actual_end_at = rec.actual_end_at or existing.actual_end_at
        existing.discovered_at = rec.discovered_at or existing.discovered_at
        existing.last_seen_at = rec.last_seen_at or existing.last_seen_at
        if rec.peak_concurrent_viewers > existing.peak_concurrent_viewers:
            existing.peak_concurrent_viewers = rec.peak_concurrent_viewers
        return existing

    def mark_sample_success(self, video_id: str, sampled_at: str, viewers: int) -> None:
        st = self.state.streams.get(video_id)
        if st is None:
            return
        st.last_success_sample_at = sampled_at
        st.last_error = ""
        if viewers > st.peak_concurrent_viewers:
            st.peak_concurrent_viewers = viewers

    def mark_sample_error(self, video_id: str, error: str) -> None:
        st = self.state.streams.get(video_id)
        if st is None:
            return
        st.last_error = error

    def active_live_ids(self) -> list[str]:
        return [vid for vid, st in self.state.streams.items() if st.status == "live"]
