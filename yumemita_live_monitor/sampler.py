"""Batch concurrent-viewer sampling."""

from __future__ import annotations

import logging
from typing import Dict, List, Sequence, Tuple

from .config import AppConfig
from .discovery import classify_live_status, parse_video_item
from .models import Channel, SampleResult, StreamRecord, ViewerSample
from .utils import format_utc, utc_now
from .youtube_client import YouTubeAPIError, YouTubeClient

logger = logging.getLogger("yumemita_live_monitor.sampler")


def extract_concurrent_viewers(item: dict) -> SampleResult:
    video_id = item.get("id") or ""
    live = item.get("liveStreamingDetails") or {}
    status = classify_live_status(live)

    if status == "ended":
        return SampleResult(video_id=video_id, success=False, reason="ended")
    if status == "upcoming":
        return SampleResult(video_id=video_id, success=False, reason="not_started")
    if status == "unknown" and not live:
        return SampleResult(video_id=video_id, success=False, reason="not_livestream")

    if "concurrentViewers" not in live:
        return SampleResult(
            video_id=video_id, success=False, reason="missing_concurrent_viewers"
        )

    raw = live.get("concurrentViewers")
    try:
        viewers = int(raw)
    except (TypeError, ValueError):
        return SampleResult(
            video_id=video_id,
            success=False,
            reason=f"parse_error: concurrentViewers={raw!r}",
        )

    return SampleResult(
        video_id=video_id, success=True, concurrent_viewers=viewers, reason="ok"
    )


class ViewerSampler:
    def __init__(
        self,
        client: YouTubeClient,
        cfg: AppConfig,
        channel_map: Dict[str, Channel],
    ):
        self.client = client
        self.cfg = cfg
        self.channel_map = channel_map

    def sample_live(
        self,
        live_records: Sequence[StreamRecord],
    ) -> Tuple[List[ViewerSample], List[SampleResult], List[StreamRecord]]:
        if not live_records:
            return [], [], []

        by_id: Dict[str, StreamRecord] = {r.video_id: r for r in live_records}
        ids = list(by_id.keys())
        now_iso = format_utc(utc_now())

        try:
            items = self.client.videos_list(
                ids,
                part="snippet,liveStreamingDetails",
                batch_size=self.cfg.videos_batch_size,
            )
        except YouTubeAPIError as e:
            logger.error("Batch sample videos.list failed: %s", e)
            results = [
                SampleResult(video_id=vid, success=False, reason=f"api_error: {e}")
                for vid in ids
            ]
            return [], results, []

        results: List[SampleResult] = []
        samples: List[ViewerSample] = []
        meta_updates: List[StreamRecord] = []
        seen: set[str] = set()

        for item in items:
            vid = item.get("id") or ""
            seen.add(vid)
            existing = by_id.get(vid)
            sn = item.get("snippet") or {}
            ch_id = sn.get("channelId") or (existing.channel_id if existing else "")
            ch = self.channel_map.get(ch_id)

            updated = parse_video_item(
                item,
                channel=ch,
                channel_map=self.channel_map,
                now_iso=now_iso,
                existing=existing,
            )
            if updated is not None:
                meta_updates.append(updated)

            result = extract_concurrent_viewers(item)
            results.append(result)
            if result.success and result.concurrent_viewers is not None and existing:
                samples.append(
                    ViewerSample(
                        sampled_at=now_iso,
                        video_id=vid,
                        channel_id=existing.channel_id,
                        member_key=existing.member_key,
                        member_name=existing.member_name,
                        concurrent_viewers=result.concurrent_viewers,
                    )
                )

        for vid in ids:
            if vid not in seen:
                results.append(
                    SampleResult(
                        video_id=vid, success=False, reason="not_returned_by_api"
                    )
                )

        return samples, results, meta_updates
