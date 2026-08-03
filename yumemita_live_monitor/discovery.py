"""Live stream discovery with pluggable strategies and external candidates."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Sequence, Tuple

from .config import AppConfig
from .models import Channel, StreamRecord
from .utils import format_utc, uploads_playlist_id, utc_now
from .youtube_client import YouTubeAPIError, YouTubeClient

logger = logging.getLogger("yumemita_live_monitor.discovery")


def classify_live_status(live_details: dict) -> str:
    if not live_details:
        return "unknown"
    if live_details.get("actualEndTime"):
        return "ended"
    if live_details.get("actualStartTime"):
        return "live"
    if live_details.get("scheduledStartTime"):
        return "upcoming"
    if "concurrentViewers" in live_details:
        return "live"
    return "unknown"


def parse_video_item(
    item: dict,
    *,
    channel: Optional[Channel] = None,
    channel_map: Optional[Dict[str, Channel]] = None,
    now_iso: Optional[str] = None,
    existing: Optional[StreamRecord] = None,
) -> Optional[StreamRecord]:
    from .utils import parse_youtube_time

    video_id = item.get("id")
    if not video_id:
        return None

    snippet = item.get("snippet") or {}
    live = item.get("liveStreamingDetails") or {}
    channel_id = snippet.get("channelId") or (channel.channel_id if channel else "")

    ch = channel
    if ch is None and channel_map:
        ch = channel_map.get(channel_id)

    status = classify_live_status(live)
    if status == "unknown" and not live:
        return None

    now_iso = now_iso or format_utc(utc_now())
    member_key = ch.member_key if ch else (existing.member_key if existing else "")
    member_name = ch.member_name if ch else (existing.member_name if existing else "")
    if not channel_id and existing:
        channel_id = existing.channel_id

    return StreamRecord(
        video_id=video_id,
        channel_id=channel_id,
        member_key=member_key,
        member_name=member_name,
        title=snippet.get("title") or (existing.title if existing else "") or "",
        status=status,
        scheduled_start_at=parse_youtube_time(live.get("scheduledStartTime")),
        actual_start_at=parse_youtube_time(live.get("actualStartTime")),
        actual_end_at=parse_youtube_time(live.get("actualEndTime")),
        discovered_at=(
            existing.discovered_at if existing and existing.discovered_at else now_iso
        ),
        last_seen_at=now_iso,
        peak_concurrent_viewers=existing.peak_concurrent_viewers if existing else 0,
    )


class DiscoveryStrategy(ABC):
    @abstractmethod
    def discover_channel(self, channel: Channel) -> List[str]:
        ...


class UploadsPlaylistDiscovery(DiscoveryStrategy):
    def __init__(self, client: YouTubeClient, max_results: int = 15):
        self.client = client
        self.max_results = max_results

    def discover_channel(self, channel: Channel) -> List[str]:
        playlist_id = uploads_playlist_id(channel.channel_id)
        try:
            items = self.client.playlist_items(playlist_id, max_results=self.max_results)
        except YouTubeAPIError as e:
            logger.error(
                "Uploads discovery failed for %s (%s): %s",
                channel.member_key,
                channel.channel_id,
                e,
            )
            return []
        ids: List[str] = []
        for it in items:
            details = it.get("contentDetails") or {}
            vid = details.get("videoId")
            if not vid:
                sn = it.get("snippet") or {}
                res = sn.get("resourceId") or {}
                vid = res.get("videoId")
            if vid:
                ids.append(vid)
        return ids


class SearchAPIDiscovery(DiscoveryStrategy):
    def __init__(self, client: YouTubeClient, max_results: int = 5):
        self.client = client
        self.max_results = max_results

    def discover_channel(self, channel: Channel) -> List[str]:
        ids: List[str] = []
        for event_type in ("live", "upcoming"):
            try:
                items = self.client.search_live_or_upcoming(
                    channel.channel_id, event_type, max_results=self.max_results
                )
            except YouTubeAPIError as e:
                logger.error(
                    "Search discovery (%s) failed for %s: %s",
                    event_type,
                    channel.member_key,
                    e,
                )
                continue
            for it in items:
                vid = (it.get("id") or {}).get("videoId")
                if vid:
                    ids.append(vid)
        seen: set[str] = set()
        out: List[str] = []
        for v in ids:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out


class HybridDiscovery(DiscoveryStrategy):
    def __init__(self, uploads: UploadsPlaylistDiscovery, search: SearchAPIDiscovery):
        self.uploads = uploads
        self.search = search

    def discover_channel(self, channel: Channel) -> List[str]:
        ids = self.uploads.discover_channel(channel)
        seen = set(ids)
        for v in self.search.discover_channel(channel):
            if v not in seen:
                ids.append(v)
                seen.add(v)
        return ids


def build_discovery_strategy(cfg: AppConfig, client: YouTubeClient) -> DiscoveryStrategy:
    uploads = UploadsPlaylistDiscovery(
        client, max_results=cfg.discovery_playlist_max_results
    )
    if cfg.discovery_method == "uploads":
        return uploads
    search = SearchAPIDiscovery(client)
    if cfg.discovery_method == "search":
        return search
    return HybridDiscovery(uploads, search)


class StreamDiscoveryService:
    """Orchestrates channel discovery + status refresh + external video_id candidates."""

    def __init__(
        self,
        client: YouTubeClient,
        strategy: DiscoveryStrategy,
        cfg: AppConfig,
        channel_map: Dict[str, Channel],
    ):
        self.client = client
        self.strategy = strategy
        self.cfg = cfg
        self.channel_map = channel_map

    def discover(
        self,
        channels: Sequence[Channel],
        known_records: Dict[str, StreamRecord],
        *,
        extra_video_ids: Optional[Sequence[str]] = None,
        only_member_keys: Optional[Sequence[str]] = None,
    ) -> Tuple[List[StreamRecord], List[str]]:
        """
        Discover livestreams for given channels (optionally filtered by member_key).
        extra_video_ids are merged into candidates (e.g. from X ScheduleHints).
        """
        now_iso = format_utc(utc_now())
        candidate_ids: List[str] = []
        errors: List[str] = []
        only = set(only_member_keys) if only_member_keys is not None else None

        for ch in channels:
            if not ch.enabled:
                continue
            if only is not None and ch.member_key not in only:
                continue
            try:
                ids = self.strategy.discover_channel(ch)
                logger.info(
                    "Discovery candidates for %s: %d ids", ch.member_key, len(ids)
                )
                candidate_ids.extend(ids)
            except Exception as e:
                msg = f"discovery error for {ch.member_key}: {e}"
                logger.exception(msg)
                errors.append(msg)

        for vid, rec in known_records.items():
            if rec.status in {"live", "upcoming"}:
                if only is None or rec.member_key in only:
                    candidate_ids.append(vid)

        if extra_video_ids:
            candidate_ids.extend(v for v in extra_video_ids if v)

        seen: set[str] = set()
        unique_ids: List[str] = []
        for vid in candidate_ids:
            if vid not in seen:
                seen.add(vid)
                unique_ids.append(vid)

        if not unique_ids:
            return [], errors

        try:
            items = self.client.videos_list(
                unique_ids,
                part="snippet,liveStreamingDetails",
                batch_size=self.cfg.videos_batch_size,
            )
        except YouTubeAPIError as e:
            errors.append(f"videos.list failed during discovery: {e}")
            logger.error(errors[-1])
            return [], errors

        results: List[StreamRecord] = []
        found_ids: set[str] = set()
        for item in items:
            vid = item.get("id")
            existing = known_records.get(vid) if vid else None
            sn = item.get("snippet") or {}
            ch_id = sn.get("channelId") or (existing.channel_id if existing else "")
            ch = self.channel_map.get(ch_id)
            if ch is None and existing:
                ch = self.channel_map.get(existing.channel_id)

            # Reject unknown channels unless already tracked
            if ch is None and existing is None:
                logger.warning(
                    "X candidate rejected: channel mismatch video_id=%s channel_id=%s",
                    vid,
                    ch_id,
                )
                continue

            rec = parse_video_item(
                item,
                channel=ch,
                channel_map=self.channel_map,
                now_iso=now_iso,
                existing=existing,
            )
            if rec is None:
                continue
            if rec.status == "unknown" and existing is None:
                continue
            results.append(rec)
            found_ids.add(rec.video_id)

        for vid in unique_ids:
            if vid in found_ids:
                continue
            existing = known_records.get(vid)
            if existing and existing.status in {"live", "upcoming"}:
                logger.warning(
                    "Known stream %s not returned by videos.list; keeping last status=%s",
                    vid,
                    existing.status,
                )

        logger.info(
            "Discovery complete: %d candidates -> %d livestream records",
            len(unique_ids),
            len(results),
        )
        return results, errors
