"""Persist ScheduleHint records with dedupe / supersede semantics."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .models import ScheduleHint
from .utils import atomic_write_text, ensure_dir, parse_iso, utc_now_iso

logger = logging.getLogger("x_schedule_monitor.schedule_store")


class ScheduleHintStore:
    """
    JSON store for schedule hints.
    Identity key: source_post_id|member_key|planned_start_at
    Same-day newer posts supersede older active hints for that schedule_date.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        ensure_dir(self.path.parent)
        self._hints: Dict[str, ScheduleHint] = {}
        self.load()

    def load(self) -> Dict[str, ScheduleHint]:
        self._hints = {}
        if not self.path.exists():
            return self._hints
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("root must be object")
            items = data.get("hints") or []
            if not isinstance(items, list):
                raise ValueError("hints must be array")
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                hint = ScheduleHint.from_dict(raw)
                if hint.hint_key():
                    self._hints[hint.hint_key()] = hint
            logger.info("Loaded %d schedule hints from %s", len(self._hints), self.path)
        except Exception as e:
            logger.error(
                "Schedule hints file damaged (%s): %s; starting empty (original kept)",
                self.path,
                e,
            )
            self._hints = {}
        return self._hints

    def save(self) -> None:
        payload = {
            "version": 1,
            "updated_at": utc_now_iso(),
            "hints": [h.to_dict() for h in sorted(
                self._hints.values(),
                key=lambda x: (x.schedule_date, x.planned_start_at, x.member_key),
            )],
        }
        atomic_write_text(
            self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )

    def all(self) -> List[ScheduleHint]:
        return list(self._hints.values())

    def active(self) -> List[ScheduleHint]:
        return [h for h in self._hints.values() if h.status == "active"]

    def active_for_member(self, member_key: str) -> List[ScheduleHint]:
        return [h for h in self.active() if h.member_key == member_key]

    def video_id_candidates(self) -> List[str]:
        ids: List[str] = []
        seen: set[str] = set()
        for h in self.active():
            if h.youtube_video_id and h.youtube_video_id not in seen:
                seen.add(h.youtube_video_id)
                ids.append(h.youtube_video_id)
        return ids

    def upsert_hints(
        self,
        hints: Iterable[ScheduleHint],
        *,
        supersede_same_date: bool = True,
    ) -> int:
        """
        Insert/update hints. Returns number of newly active keys written.
        When supersede_same_date, older active hints for the same schedule_date
        from different posts are marked superseded if the new post is newer.
        """
        new_count = 0
        hints = list(hints)
        if not hints:
            return 0

        # Group by post for supersede logic
        by_post: Dict[str, List[ScheduleHint]] = {}
        for h in hints:
            by_post.setdefault(h.source_post_id, []).append(h)

        for post_id, post_hints in by_post.items():
            # Dedupe same post+member+time: keep last
            for h in post_hints:
                key = h.hint_key()
                if key in self._hints and self._hints[key].status == "active":
                    # Same post re-fetched: update fields, no double create
                    self._hints[key] = h
                    continue
                if key not in self._hints:
                    new_count += 1
                self._hints[key] = h

            if not supersede_same_date:
                continue

            # Determine newest post's schedule_date and created_at
            sample = post_hints[0]
            schedule_date = sample.schedule_date
            new_created = parse_iso(sample.source_post_created_at)

            for key, existing in list(self._hints.items()):
                if existing.status != "active":
                    continue
                if existing.schedule_date != schedule_date:
                    continue
                if existing.source_post_id == post_id:
                    continue
                old_created = parse_iso(existing.source_post_created_at)
                if new_created and old_created and new_created >= old_created:
                    existing.status = "superseded"
                    logger.info(
                        "Superseded hint member=%s date=%s old_post=%s new_post=%s",
                        existing.member_key,
                        schedule_date,
                        existing.source_post_id,
                        post_id,
                    )
                elif new_created and old_created and new_created < old_created:
                    # Incoming post is older: mark incoming as superseded
                    for h in post_hints:
                        if h.schedule_date == schedule_date:
                            stored = self._hints.get(h.hint_key())
                            if stored and stored.source_post_id == post_id:
                                stored.status = "superseded"

        return new_count

    def expire_before(self, schedule_date: str) -> int:
        """Mark active hints with schedule_date < given date as expired."""
        n = 0
        for h in self._hints.values():
            if h.status == "active" and h.schedule_date and h.schedule_date < schedule_date:
                h.status = "expired"
                n += 1
        return n

    def has_post(self, post_id: str) -> bool:
        return any(h.source_post_id == post_id for h in self._hints.values())
