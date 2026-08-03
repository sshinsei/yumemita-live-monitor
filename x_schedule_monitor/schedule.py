"""Time bands for peak windows and sampling intervals.

Discovery cadence is configured separately on AppConfig
(discovery_*_interval_seconds). Bands only define when a Tokyo-local
window is "active" and what sampling interval applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import List, Optional, Sequence
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SamplingProfile:
    """Resolved sampling interval for the current schedule band."""

    name: str
    sampling_seconds: int


@dataclass(frozen=True)
class TimeBand:
    name: str
    start: time
    end: time
    end_is_midnight: bool = False
    days: Optional[frozenset[int]] = None
    sampling_seconds: int = 45

    def matches(self, local_dt: datetime) -> bool:
        if self.days is not None and local_dt.isoweekday() not in self.days:
            return False
        minutes = local_dt.hour * 60 + local_dt.minute
        start_m = self.start.hour * 60 + self.start.minute
        if self.end_is_midnight:
            end_m = 24 * 60
        else:
            end_m = self.end.hour * 60 + self.end.minute

        if start_m < end_m:
            return start_m <= minutes < end_m
        if start_m == end_m:
            return False
        return minutes >= start_m or minutes < end_m

    def to_profile(self) -> SamplingProfile:
        return SamplingProfile(name=self.name, sampling_seconds=self.sampling_seconds)


def _local_now(
    now_utc: Optional[datetime] = None,
    *,
    tz_name: str = "Asia/Tokyo",
) -> datetime:
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)
    return now_utc.astimezone(ZoneInfo(tz_name))


def match_time_band(
    bands: Sequence[TimeBand],
    *,
    now_utc: Optional[datetime] = None,
    tz_name: str = "Asia/Tokyo",
) -> Optional[TimeBand]:
    """Return the matching time band for local time, or None (off-peak).

    When multiple bands match, prefer the more aggressive (shorter) sampling.
    """
    local = _local_now(now_utc, tz_name=tz_name)
    matched: List[TimeBand] = [b for b in bands if b.matches(local)]
    if not matched:
        return None
    return min(matched, key=lambda b: b.sampling_seconds)


def resolve_profile(
    bands: Sequence[TimeBand],
    off_peak: SamplingProfile,
    *,
    now_utc: Optional[datetime] = None,
    tz_name: str = "Asia/Tokyo",
) -> SamplingProfile:
    band = match_time_band(bands, now_utc=now_utc, tz_name=tz_name)
    if band is None:
        return off_peak
    return band.to_profile()


def default_time_bands() -> List[TimeBand]:
    return [
        TimeBand(
            name="evening_peak",
            start=time(20, 0),
            end=time(0, 0),
            end_is_midnight=True,
            sampling_seconds=45,
        ),
        TimeBand(
            name="morning",
            start=time(6, 0),
            end=time(8, 0),
            sampling_seconds=45,
        ),
        TimeBand(
            name="midday",
            start=time(11, 45),
            end=time(12, 30),
            sampling_seconds=45,
        ),
    ]


def default_off_peak() -> SamplingProfile:
    return SamplingProfile(name="off_peak", sampling_seconds=60)
