"""Orchestrate X fetch → parse → ScheduleHint store."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from .config import AppConfig
from .schedule_parser import ParseResult, parse_schedule_post
from .schedule_store import ScheduleHintStore
from .utils import utc_now_iso
from .x_client import XAPIError, XClient, XPost

logger = logging.getLogger("yumemita_live_monitor.schedule_feed")


@dataclass
class RefreshStats:
    fetched: int = 0
    new_posts: int = 0
    parsed_hints: int = 0
    schedule_posts: int = 0
    warnings: int = 0
    error: str = ""
    max_post_id: str = ""


class ScheduleFeedService:
    def __init__(
        self,
        cfg: AppConfig,
        store: ScheduleHintStore,
        client: Optional[XClient] = None,
    ):
        self.cfg = cfg
        self.store = store
        self.client = client

    def refresh(self, *, since_id: Optional[str] = None) -> RefreshStats:
        stats = RefreshStats()
        if not self.cfg.x_schedule_enabled:
            stats.error = "x_schedule_disabled"
            return stats

        token = self.cfg.resolve_x_bearer_token()
        if not token:
            stats.error = "missing_bearer_token"
            logger.warning(
                "X schedule unavailable; falling back to YouTube + time_bands discovery"
            )
            return stats

        client = self.client
        close_client = False
        if client is None:
            client = XClient(
                token,
                timeout=self.cfg.request_timeout_seconds,
                max_retries=self.cfg.max_retries,
            )
            close_client = True

        try:
            posts = client.recent_search(
                self.cfg.x_schedule_username,
                since_id=since_id or None,
            )
        except XAPIError as e:
            stats.error = f"{e.kind}: {e}"
            logger.error(
                "X schedule unavailable; falling back to YouTube + time_bands discovery (%s)",
                e,
            )
            return stats
        except Exception as e:
            stats.error = f"unexpected: {e}"
            logger.exception("X schedule refresh failed unexpectedly")
            return stats
        finally:
            if close_client and client is not None:
                client.close()

        stats.fetched = len(posts)
        fetched_at = utc_now_iso()
        max_id = since_id or ""

        # Process oldest first so supersede order is chronological
        ordered = sorted(posts, key=lambda p: p.post_id)

        for post in ordered:
            if not max_id or int(post.post_id) > int(max_id):
                max_id = post.post_id
            if self.store.has_post(post.post_id):
                continue
            stats.new_posts += 1
            result = self._ingest_post(post, fetched_at)
            if result.is_schedule_post:
                stats.schedule_posts += 1
                stats.parsed_hints += len(result.hints)
                stats.warnings += len(result.warnings)
                for w in result.warnings:
                    logger.warning(
                        "X schedule parse warning: %s | %s",
                        w.message,
                        w.raw_line[:80] if w.raw_line else "",
                    )
                for h in result.hints:
                    logger.info(
                        "X schedule hint: member=%s planned_start=%s video_id=%s member_only=%s",
                        h.member_key,
                        h.planned_start_at,
                        h.youtube_video_id or "-",
                        h.member_only,
                    )
                    if h.youtube_url and not h.youtube_video_id:
                        logger.info(
                            "X schedule hint has channel URL only: member=%s",
                            h.member_key,
                        )

        stats.max_post_id = max_id
        try:
            self.store.save()
        except Exception:
            logger.exception("Failed to save schedule hints")

        logger.info(
            "X schedule refresh: fetched=%s new=%s parsed_hints=%s",
            stats.fetched,
            stats.new_posts,
            stats.parsed_hints,
        )
        return stats

    def ingest_text(
        self,
        text: str,
        *,
        source_post_id: str,
        source_post_created_at: str,
        expanded_urls: Optional[List[str]] = None,
        edit_history_tweet_ids: Optional[List[str]] = None,
    ) -> ParseResult:
        """Offline / test path: parse and store a post body."""
        result = parse_schedule_post(
            text,
            source_post_id=source_post_id,
            source_post_created_at=source_post_created_at,
            fetched_at=utc_now_iso(),
            expanded_urls=expanded_urls,
            edit_history_tweet_ids=edit_history_tweet_ids,
            tz_name=self.cfg.schedule_timezone,
            member_only_enabled=self.cfg.x_schedule_member_only_enabled,
        )
        if result.hints:
            self.store.upsert_hints(result.hints)
            self.store.save()
        return result

    def _ingest_post(self, post: XPost, fetched_at: str) -> ParseResult:
        result = parse_schedule_post(
            post.text,
            source_post_id=post.post_id,
            source_post_created_at=post.created_at,
            fetched_at=fetched_at,
            expanded_urls=post.expanded_urls,
            edit_history_tweet_ids=post.edit_history_tweet_ids,
            tz_name=self.cfg.schedule_timezone,
            member_only_enabled=self.cfg.x_schedule_member_only_enabled,
        )
        if result.hints:
            self.store.upsert_hints(result.hints)
        return result
