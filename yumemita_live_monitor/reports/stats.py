"""Statistics for weekly reports from streams.csv + samples."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..models import StreamRecord, ViewerSample
from ..utils import parse_iso
from .windows import TimeWindow


@dataclass
class StreamStats:
    video_id: str
    channel_id: str
    member_key: str
    member_name: str
    title: str
    status: str
    actual_start_at: str
    actual_end_at: str
    sample_count: int = 0
    expected_samples: int = 0
    coverage: float = 0.0
    peak_concurrent_viewers: int = 0
    peak_at: str = ""
    time_weighted_avg: Optional[float] = None
    median_viewers: Optional[float] = None
    duration_seconds: float = 0.0
    samples_in_window: List[Tuple[str, int]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("samples_in_window", None)
        return d


@dataclass
class MemberStats:
    member_key: str
    member_name: str
    color: str
    stream_count: int = 0
    active_stream_count: int = 0
    total_duration_seconds: float = 0.0
    sample_count: int = 0
    expected_samples: int = 0
    coverage: float = 0.0
    peak_concurrent_viewers: int = 0
    peak_video_id: str = ""
    peak_at: str = ""
    time_weighted_avg: Optional[float] = None
    median_viewers: Optional[float] = None
    streams: List[StreamStats] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "member_key": self.member_key,
            "member_name": self.member_name,
            "color": self.color,
            "stream_count": self.stream_count,
            "active_stream_count": self.active_stream_count,
            "total_duration_seconds": self.total_duration_seconds,
            "sample_count": self.sample_count,
            "expected_samples": self.expected_samples,
            "coverage": self.coverage,
            "peak_concurrent_viewers": self.peak_concurrent_viewers,
            "peak_video_id": self.peak_video_id,
            "peak_at": self.peak_at,
            "time_weighted_avg": self.time_weighted_avg,
            "median_viewers": self.median_viewers,
            "streams": [s.to_dict() for s in self.streams],
        }


def time_weighted_average(
    points: Sequence[Tuple[datetime, int]],
    *,
    default_interval: float,
    window_end: Optional[datetime] = None,
) -> Optional[float]:
    if not points:
        return None
    pts = sorted(points, key=lambda x: x[0])
    if len(pts) == 1:
        return float(pts[0][1])

    weighted_sum = 0.0
    total_w = 0.0
    for i, (t, v) in enumerate(pts):
        if i + 1 < len(pts):
            dt = (pts[i + 1][0] - t).total_seconds()
        else:
            if window_end is not None and window_end > t:
                dt = min(default_interval, (window_end - t).total_seconds())
            else:
                dt = default_interval
        if dt <= 0:
            continue
        weighted_sum += v * dt
        total_w += dt
    if total_w <= 0:
        return float(statistics.mean(v for _, v in pts))
    return weighted_sum / total_w


def stream_anchor_dt(
    rec: StreamRecord,
    samples: Sequence[ViewerSample] = (),
) -> Optional[datetime]:
    """Time used to assign a stream to exactly one report window.

    Prefer actual start, then the first sample, then the scheduled start.
    A Sunday-night stream that continues into Monday belongs to the week
    it started, not the week the clock rolled over.
    """
    start = parse_iso(rec.actual_start_at)
    if start is not None:
        return start
    if samples:
        first = parse_iso(samples[0].sampled_at)
        if first is not None:
            return first
    return parse_iso(rec.scheduled_start_at)


def compute_stream_duration(
    rec: StreamRecord,
    samples: Sequence[ViewerSample],
) -> float:
    """Full on-air duration; not clipped to a report window."""
    start = parse_iso(rec.actual_start_at) or (
        parse_iso(samples[0].sampled_at) if samples else None
    )
    end = parse_iso(rec.actual_end_at)
    if end is None and samples:
        end = parse_iso(samples[-1].sampled_at)
    if start is None:
        return 0.0
    if end is None:
        end = start + timedelta(hours=12)
    if end <= start:
        return 0.0
    return (end - start).total_seconds()


def expected_sample_count(duration_seconds: float, interval_seconds: float) -> int:
    if duration_seconds <= 0 or interval_seconds <= 0:
        return 0
    return max(1, int(duration_seconds / interval_seconds) + 1)


def build_member_stats(
    *,
    member_key: str,
    member_name: str,
    color: str,
    streams: Sequence[StreamRecord],
    samples: Sequence[ViewerSample],
    window: TimeWindow,
    sampling_interval_seconds: float = 45.0,
) -> MemberStats:
    samples_by_video: Dict[str, List[ViewerSample]] = defaultdict(list)
    for s in samples:
        if s.member_key != member_key:
            continue
        samples_by_video[s.video_id].append(s)

    stream_map = {r.video_id: r for r in streams if r.member_key == member_key}
    video_ids = set(stream_map.keys()) | set(samples_by_video.keys())

    stream_stats_list: List[StreamStats] = []
    all_viewer_values: List[int] = []
    all_points: List[Tuple[datetime, int]] = []
    total_duration = 0.0
    total_samples = 0
    total_expected = 0
    peak = 0
    peak_vid = ""
    peak_at = ""

    for vid in sorted(video_ids):
        rec = stream_map.get(vid) or StreamRecord(
            video_id=vid,
            channel_id="",
            member_key=member_key,
            member_name=member_name,
        )
        vsamples = sorted(
            samples_by_video.get(vid, []),
            key=lambda x: x.sampled_at,
        )
        anchor = stream_anchor_dt(rec, vsamples)
        if anchor is None or not window.contains(anchor):
            continue

        duration = compute_stream_duration(rec, vsamples)
        if duration <= 0 and not vsamples:
            continue

        points: List[Tuple[datetime, int]] = []
        peak_local = 0
        peak_local_at = ""
        for s in vsamples:
            dt = parse_iso(s.sampled_at)
            if dt is None:
                continue
            points.append((dt, s.concurrent_viewers))
            all_viewer_values.append(s.concurrent_viewers)
            all_points.append((dt, s.concurrent_viewers))
            if s.concurrent_viewers > peak_local:
                peak_local = s.concurrent_viewers
                peak_local_at = s.sampled_at

        stream_end = parse_iso(rec.actual_end_at)
        tw_avg = time_weighted_average(
            points,
            default_interval=sampling_interval_seconds,
            window_end=stream_end,
        )
        med = float(statistics.median([p[1] for p in points])) if points else None
        exp = expected_sample_count(duration, sampling_interval_seconds)
        cov = (len(points) / exp) if exp > 0 else (1.0 if points else 0.0)
        cov = min(cov, 1.0)

        ss = StreamStats(
            video_id=vid,
            channel_id=rec.channel_id,
            member_key=member_key,
            member_name=member_name,
            title=rec.title,
            status=rec.status,
            actual_start_at=rec.actual_start_at,
            actual_end_at=rec.actual_end_at,
            sample_count=len(points),
            expected_samples=exp,
            coverage=round(cov, 4),
            peak_concurrent_viewers=peak_local,
            peak_at=peak_local_at,
            time_weighted_avg=round(tw_avg, 2) if tw_avg is not None else None,
            median_viewers=round(med, 2) if med is not None else None,
            duration_seconds=duration,
            samples_in_window=[(s.sampled_at, s.concurrent_viewers) for s in vsamples],
        )
        stream_stats_list.append(ss)

        total_duration += duration
        total_samples += len(points)
        total_expected += exp
        if peak_local > peak:
            peak = peak_local
            peak_vid = vid
            peak_at = peak_local_at

    member_tw = time_weighted_average(
        all_points,
        default_interval=sampling_interval_seconds,
    )
    member_med = (
        float(statistics.median(all_viewer_values)) if all_viewer_values else None
    )
    active = sum(1 for s in stream_stats_list if s.sample_count > 0)
    coverage = min(1.0, total_samples / total_expected) if total_expected > 0 else 0.0

    stream_stats_list.sort(
        key=lambda s: s.actual_start_at
        or (s.samples_in_window[0][0] if s.samples_in_window else ""),
    )

    return MemberStats(
        member_key=member_key,
        member_name=member_name,
        color=color,
        stream_count=len(stream_stats_list),
        active_stream_count=active,
        total_duration_seconds=total_duration,
        sample_count=total_samples,
        expected_samples=total_expected,
        coverage=round(coverage, 4),
        peak_concurrent_viewers=peak,
        peak_video_id=peak_vid,
        peak_at=peak_at,
        time_weighted_avg=round(member_tw, 2) if member_tw is not None else None,
        median_viewers=round(member_med, 2) if member_med is not None else None,
        streams=stream_stats_list,
    )


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "0m"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m"
