"""Report time windows (ISO week) in configurable timezone."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import List, Tuple
from zoneinfo import ZoneInfo

from ..utils import parse_iso

# Overnight streams may continue past Monday 00:00; their later samples
# still belong to the week they started in.
CROSS_WEEK_SAMPLE_LOOKAHEAD = timedelta(hours=48)


@dataclass(frozen=True)
class TimeWindow:
    """Half-open interval [start, end) in the report timezone, stored as UTC."""

    start_utc: datetime
    end_utc: datetime
    label: str
    kind: str  # week
    timezone: str

    def contains(self, dt: datetime) -> bool:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return self.start_utc <= dt < self.end_utc

    def months_spanned(self, extra_after: timedelta = timedelta(0)) -> List[str]:
        """Months that intersect [start, end + extra_after).

        extra_after covers samples that belong to a stream which started
        inside this window but continued past the week boundary.
        """
        months: List[str] = []
        cur = self.start_utc
        end = self.end_utc + extra_after
        seen: set[str] = set()
        while cur < end:
            key = cur.strftime("%Y-%m")
            if key not in seen:
                seen.add(key)
                months.append(key)
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1, day=1)
            else:
                try:
                    cur = cur.replace(month=cur.month + 1, day=1)
                except ValueError:
                    cur = cur + timedelta(days=28)
        if end > self.start_utc:
            end_key = (end - timedelta(seconds=1)).strftime("%Y-%m")
            if end_key not in seen:
                months.append(end_key)
        return months


def _local_midnight(d: date, tz: ZoneInfo) -> datetime:
    local = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)
    return local.astimezone(timezone.utc)


def iso_week_window(year: int, week: int, tz_name: str) -> TimeWindow:
    tz = ZoneInfo(tz_name)
    start_date = date.fromisocalendar(year, week, 1)
    end_date = start_date + timedelta(days=7)
    start_utc = _local_midnight(start_date, tz)
    end_utc = _local_midnight(end_date, tz)
    label = f"{year}-W{week:02d}"
    return TimeWindow(start_utc, end_utc, label, "week", tz_name)


def parse_iso_week_label(label: str) -> Tuple[int, int]:
    label = label.strip().upper()
    if "-W" not in label:
        raise ValueError(f"Invalid ISO week label: {label}")
    y, w = label.split("-W", 1)
    return int(y), int(w)


def current_iso_week(now_local: datetime, tz_name: str) -> TimeWindow:
    y, w, _ = now_local.date().isocalendar()
    return iso_week_window(y, w, tz_name)


def previous_complete_iso_week(now_local: datetime, tz_name: str) -> TimeWindow:
    weekday = now_local.isoweekday()
    this_monday = now_local.date() - timedelta(days=weekday - 1)
    prev_monday = this_monday - timedelta(days=7)
    y, w, _ = prev_monday.isocalendar()
    return iso_week_window(y, w, tz_name)


def auto_retained_iso_weeks(now_local: datetime, tz_name: str) -> List[TimeWindow]:
    """Weeks the monitor keeps on disk: previous complete week + current week."""
    return [
        previous_complete_iso_week(now_local, tz_name),
        current_iso_week(now_local, tz_name),
    ]


def list_complete_iso_weeks_until(
    earliest_utc: datetime,
    now_local: datetime,
    tz_name: str,
) -> List[TimeWindow]:
    prev = previous_complete_iso_week(now_local, tz_name)
    weeks: List[TimeWindow] = []
    cur_y, cur_w = parse_iso_week_label(prev.label)
    guard = 0
    while guard < 600:
        guard += 1
        w = iso_week_window(cur_y, cur_w, tz_name)
        if w.end_utc <= earliest_utc:
            break
        if w.end_utc > earliest_utc:
            weeks.append(w)
        start_date = date.fromisocalendar(cur_y, cur_w, 1) - timedelta(days=7)
        cur_y, cur_w, _ = start_date.isocalendar()
    weeks.reverse()
    return weeks
