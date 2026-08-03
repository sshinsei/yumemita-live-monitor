"""Shared data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


DEFAULT_MEMBER_COLORS: Dict[str, str] = {
    "arale": "#FFEE55",
    "nonoka": "#FFBBCC",
    "ritsu": "#4477CC",
    "miyako": "#9977CC",
    "yuno": "#EE5577",
}

LIGHT_BACKGROUND_COLORS = {
    "#FFEE55",
    "#FFBBCC",
    "#ffee55",
    "#ffbbcc",
}


def text_color_for_bg(hex_color: str) -> str:
    c = (hex_color or "").strip().upper()
    if c in {x.upper() for x in LIGHT_BACKGROUND_COLORS}:
        return "#111111"
    try:
        h = c.lstrip("#")
        if len(h) != 6:
            return "#ffffff"
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return "#111111" if luminance > 0.6 else "#ffffff"
    except Exception:
        return "#ffffff"

# Display name (as written on X schedule posts) -> member_key
MEMBER_NAME_ALIASES: Dict[str, str] = {
    "仲町あられ": "arale",
    "宮永ののか": "nonoka",
    "峰月律": "ritsu",
    "藤都子": "miyako",
    "千石ユノ": "yuno",
}

SCHEDULE_HINT_STATUSES = frozenset({"active", "superseded", "expired", "invalid"})


def stable_fallback_color(member_key: str) -> str:
    palette = [
        "#5EEAD4",
        "#F472B6",
        "#A78BFA",
        "#34D399",
        "#FBBF24",
        "#60A5FA",
        "#FB7185",
        "#C084FC",
    ]
    h = sum(ord(c) for c in member_key) if member_key else 0
    return palette[h % len(palette)]


@dataclass
class Channel:
    member_key: str
    member_name: str
    channel_id: str
    enabled: bool = True
    color: Optional[str] = None

    def resolved_color(self) -> str:
        if self.color:
            return self.color
        if self.member_key in DEFAULT_MEMBER_COLORS:
            return DEFAULT_MEMBER_COLORS[self.member_key]
        return stable_fallback_color(self.member_key)


@dataclass
class StreamRecord:
    video_id: str
    channel_id: str
    member_key: str
    member_name: str
    title: str = ""
    status: str = "unknown"  # upcoming | live | ended | unknown
    scheduled_start_at: str = ""
    actual_start_at: str = ""
    actual_end_at: str = ""
    discovered_at: str = ""
    last_seen_at: str = ""
    peak_concurrent_viewers: int = 0

    def to_csv_row(self) -> Dict[str, Any]:
        return {
            "video_id": self.video_id,
            "channel_id": self.channel_id,
            "member_key": self.member_key,
            "member_name": self.member_name,
            "title": self.title,
            "status": self.status,
            "scheduled_start_at": self.scheduled_start_at,
            "actual_start_at": self.actual_start_at,
            "actual_end_at": self.actual_end_at,
            "discovered_at": self.discovered_at,
            "last_seen_at": self.last_seen_at,
            "peak_concurrent_viewers": self.peak_concurrent_viewers,
        }

    @classmethod
    def from_csv_row(cls, row: Dict[str, str]) -> "StreamRecord":
        peak = row.get("peak_concurrent_viewers") or "0"
        try:
            peak_i = int(peak)
        except (TypeError, ValueError):
            peak_i = 0
        return cls(
            video_id=row.get("video_id", "").strip(),
            channel_id=row.get("channel_id", "").strip(),
            member_key=row.get("member_key", "").strip(),
            member_name=row.get("member_name", "").strip(),
            title=row.get("title", "") or "",
            status=(row.get("status") or "unknown").strip() or "unknown",
            scheduled_start_at=row.get("scheduled_start_at", "") or "",
            actual_start_at=row.get("actual_start_at", "") or "",
            actual_end_at=row.get("actual_end_at", "") or "",
            discovered_at=row.get("discovered_at", "") or "",
            last_seen_at=row.get("last_seen_at", "") or "",
            peak_concurrent_viewers=peak_i,
        )


@dataclass
class ViewerSample:
    sampled_at: str
    video_id: str
    channel_id: str
    member_key: str
    member_name: str
    concurrent_viewers: int

    def to_csv_row(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SampleResult:
    video_id: str
    success: bool
    concurrent_viewers: Optional[int] = None
    reason: str = ""


@dataclass
class ScheduleHint:
    """Independent schedule tip from X; never a substitute for StreamRecord."""

    source_post_id: str
    source_post_created_at: str
    schedule_date: str  # YYYY-MM-DD in schedule_timezone calendar
    member_key: str
    member_name: str
    planned_start_at: str  # UTC ISO Z
    youtube_url: str = ""
    youtube_video_id: str = ""
    member_only: bool = False
    raw_text: str = ""
    fetched_at: str = ""
    status: str = "active"  # active | superseded | expired | invalid
    edit_history_tweet_ids: List[str] = field(default_factory=list)

    def hint_key(self) -> str:
        """Stable identity for upsert: post + member + planned start."""
        return f"{self.source_post_id}|{self.member_key}|{self.planned_start_at}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_post_id": self.source_post_id,
            "source_post_created_at": self.source_post_created_at,
            "schedule_date": self.schedule_date,
            "member_key": self.member_key,
            "member_name": self.member_name,
            "planned_start_at": self.planned_start_at,
            "youtube_url": self.youtube_url,
            "youtube_video_id": self.youtube_video_id,
            "member_only": self.member_only,
            "raw_text": self.raw_text,
            "fetched_at": self.fetched_at,
            "status": self.status,
            "edit_history_tweet_ids": list(self.edit_history_tweet_ids),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ScheduleHint":
        edit_ids = d.get("edit_history_tweet_ids") or []
        if not isinstance(edit_ids, list):
            edit_ids = []
        status = str(d.get("status") or "active")
        if status not in SCHEDULE_HINT_STATUSES:
            status = "invalid"
        return cls(
            source_post_id=str(d.get("source_post_id") or ""),
            source_post_created_at=str(d.get("source_post_created_at") or ""),
            schedule_date=str(d.get("schedule_date") or ""),
            member_key=str(d.get("member_key") or ""),
            member_name=str(d.get("member_name") or ""),
            planned_start_at=str(d.get("planned_start_at") or ""),
            youtube_url=str(d.get("youtube_url") or ""),
            youtube_video_id=str(d.get("youtube_video_id") or ""),
            member_only=bool(d.get("member_only")),
            raw_text=str(d.get("raw_text") or ""),
            fetched_at=str(d.get("fetched_at") or ""),
            status=status,
            edit_history_tweet_ids=[str(x) for x in edit_ids],
        )


@dataclass
class RuntimeStreamState:
    video_id: str
    status: str = "unknown"
    peak_concurrent_viewers: int = 0
    last_success_sample_at: str = ""
    last_error: str = ""
    channel_id: str = ""
    member_key: str = ""
    member_name: str = ""
    title: str = ""
    scheduled_start_at: str = ""
    actual_start_at: str = ""
    actual_end_at: str = ""
    discovered_at: str = ""
    last_seen_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RuntimeStreamState":
        return cls(
            video_id=str(d.get("video_id", "")),
            status=str(d.get("status", "unknown")),
            peak_concurrent_viewers=int(d.get("peak_concurrent_viewers") or 0),
            last_success_sample_at=str(d.get("last_success_sample_at") or ""),
            last_error=str(d.get("last_error") or ""),
            channel_id=str(d.get("channel_id") or ""),
            member_key=str(d.get("member_key") or ""),
            member_name=str(d.get("member_name") or ""),
            title=str(d.get("title") or ""),
            scheduled_start_at=str(d.get("scheduled_start_at") or ""),
            actual_start_at=str(d.get("actual_start_at") or ""),
            actual_end_at=str(d.get("actual_end_at") or ""),
            discovered_at=str(d.get("discovered_at") or ""),
            last_seen_at=str(d.get("last_seen_at") or ""),
        )

    @classmethod
    def from_stream_record(cls, rec: StreamRecord) -> "RuntimeStreamState":
        return cls(
            video_id=rec.video_id,
            status=rec.status,
            peak_concurrent_viewers=rec.peak_concurrent_viewers,
            channel_id=rec.channel_id,
            member_key=rec.member_key,
            member_name=rec.member_name,
            title=rec.title,
            scheduled_start_at=rec.scheduled_start_at,
            actual_start_at=rec.actual_start_at,
            actual_end_at=rec.actual_end_at,
            discovered_at=rec.discovered_at,
            last_seen_at=rec.last_seen_at,
        )


@dataclass
class RuntimeState:
    version: int = 1
    last_discovery_at: str = ""
    last_x_refresh_at: str = ""
    last_x_since_id: str = ""
    member_last_discovery_at: Dict[str, str] = field(default_factory=dict)
    streams: Dict[str, RuntimeStreamState] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "last_discovery_at": self.last_discovery_at,
            "last_x_refresh_at": self.last_x_refresh_at,
            "last_x_since_id": self.last_x_since_id,
            "member_last_discovery_at": dict(self.member_last_discovery_at),
            "streams": {k: v.to_dict() for k, v in self.streams.items()},
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RuntimeState":
        streams_raw = d.get("streams") or {}
        streams: Dict[str, RuntimeStreamState] = {}
        if isinstance(streams_raw, dict):
            for vid, sd in streams_raw.items():
                if isinstance(sd, dict):
                    streams[str(vid)] = RuntimeStreamState.from_dict({**sd, "video_id": vid})
        mld = d.get("member_last_discovery_at") or {}
        if not isinstance(mld, dict):
            mld = {}
        return cls(
            version=int(d.get("version") or 1),
            last_discovery_at=str(d.get("last_discovery_at") or ""),
            last_x_refresh_at=str(d.get("last_x_refresh_at") or ""),
            last_x_since_id=str(d.get("last_x_since_id") or ""),
            member_last_discovery_at={str(k): str(v) for k, v in mld.items()},
            streams=streams,
        )
