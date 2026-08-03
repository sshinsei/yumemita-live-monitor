"""Per-member discovery appointment-first decision tests."""

from __future__ import annotations

from datetime import datetime, timezone

from yumemita_live_monitor.config import AppConfig
from yumemita_live_monitor.discovery_policy import (
    decide_all_members,
    decide_member_discovery,
    desired_x_refresh_interval_seconds,
    sampling_interval_seconds,
)
from yumemita_live_monitor.models import Channel, ScheduleHint, StreamRecord
from yumemita_live_monitor.schedule import default_off_peak, default_time_bands


def _cfg(**kw) -> AppConfig:
    base = dict(
        youtube_api_key="test-key",
        schedule_enabled=True,
        schedule_timezone="Asia/Tokyo",
        time_bands=default_time_bands(),
        off_peak=default_off_peak(),
        sampling_interval_seconds=45,
        discovery_near_pre_start_window_seconds=300,
        discovery_near_post_start_grace_seconds=1800,
        discovery_near_probe_interval_seconds=30,
        discovery_known_schedule_interval_seconds=10800,
        discovery_no_schedule_off_band_interval_seconds=7200,
        discovery_active_band_youtube_interval_seconds=300,
        discovery_active_band_x_refresh_interval_seconds=1800,
        x_schedule_refresh_interval_seconds=3600,
    )
    base.update(kw)
    return AppConfig(**base)


def _hint(member: str, planned: str, status: str = "active") -> ScheduleHint:
    return ScheduleHint(
        source_post_id="1",
        source_post_created_at="2026-02-20T00:00:00Z",
        schedule_date="2026-02-21",
        member_key=member,
        member_name=member,
        planned_start_at=planned,
        status=status,
    )


def _upcoming(member: str, start: str, video_id: str = "vid1") -> StreamRecord:
    return StreamRecord(
        video_id=video_id,
        channel_id="UC1",
        member_key=member,
        member_name=member,
        status="upcoming",
        scheduled_start_at=start,
    )


# ---------------------------------------------------------------------------
# Case 1: YT far away → ordinary 3h, next = now+3h
# ---------------------------------------------------------------------------
def test_case1_youtube_far_ordinary_3h():
    # JST 10:00 = UTC 01:00; start JST 20:00 = UTC 11:00
    now = datetime(2026, 2, 21, 1, 0, 0, tzinfo=timezone.utc)
    start = "2026-02-21T11:00:00Z"
    d = decide_member_discovery(
        "arale",
        streams=[_upcoming("arale", start)],
        hints=[],
        cfg=_cfg(),
        now=now,
    )
    assert d.mode == "ordinary"
    assert d.interval_seconds == 10800
    assert d.reason == "youtube_scheduled_outside_near_window"
    assert d.anchor_source == "youtube"
    assert d.next_run_at == datetime(2026, 2, 21, 4, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Case 2: 3h would skip near window → clamp to start-5min
# ---------------------------------------------------------------------------
def test_case2_clamp_next_to_near_window_start():
    # now 18:00 JST = 09:00 UTC; start 20:00 JST = 11:00 UTC → near at 10:55 UTC
    now = datetime(2026, 2, 21, 9, 0, 0, tzinfo=timezone.utc)
    start = "2026-02-21T11:00:00Z"
    d = decide_member_discovery(
        "arale",
        streams=[_upcoming("arale", start)],
        hints=[],
        cfg=_cfg(),
        now=now,
    )
    assert d.mode == "ordinary"
    assert d.interval_seconds == 10800
    assert d.reason == "youtube_scheduled_outside_near_window"
    # must be 19:55 JST = 10:55 UTC, not 21:00 / 12:00 UTC
    assert d.next_run_at == datetime(2026, 2, 21, 10, 55, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Case 3: cross midnight, only 5 min away → near_probe
# ---------------------------------------------------------------------------
def test_case3_cross_midnight_near_probe():
    # now 23:58 JST = 14:58 UTC; start next day 00:03 JST = 15:03 UTC
    now = datetime(2026, 2, 21, 14, 58, 0, tzinfo=timezone.utc)
    start = "2026-02-21T15:03:00Z"
    d = decide_member_discovery(
        "arale",
        streams=[_upcoming("arale", start)],
        hints=[],
        cfg=_cfg(),
        now=now,
    )
    assert d.mode == "near_probe"
    assert d.interval_seconds == 30
    assert d.reason == "youtube_near_window"


# ---------------------------------------------------------------------------
# Case 4: no YT, off band → 2h
# ---------------------------------------------------------------------------
def test_case4_no_schedule_off_band():
    # JST 15:00 = UTC 06:00 (between midday end 12:30 and evening 20:00)
    now = datetime(2026, 2, 21, 6, 0, 0, tzinfo=timezone.utc)
    d = decide_member_discovery(
        "arale",
        streams=[],
        hints=[],
        cfg=_cfg(),
        now=now,
    )
    assert d.mode == "ordinary"
    assert d.interval_seconds == 7200
    assert d.reason == "no_schedule_off_band"
    assert d.anchor_source == "none"


# ---------------------------------------------------------------------------
# Case 5: no YT, active band, X plan → known start clamp
# ---------------------------------------------------------------------------
def test_case5_active_band_x_known_start():
    # JST 20:00 = UTC 11:00; X planned 21:00 JST = 12:00 UTC → near 11:55
    now = datetime(2026, 2, 21, 11, 0, 0, tzinfo=timezone.utc)
    d = decide_member_discovery(
        "arale",
        streams=[],
        hints=[_hint("arale", "2026-02-21T12:00:00Z")],
        cfg=_cfg(),
        now=now,
    )
    assert d.mode == "ordinary"
    assert d.reason == "x_scheduled_outside_near_window"
    assert d.anchor_source == "x"
    assert d.next_run_at == datetime(2026, 2, 21, 11, 55, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Case 6: active band, no YT, no X → 5min probe + 30min x refresh hint
# ---------------------------------------------------------------------------
def test_case6_active_band_unscheduled_probe():
    now = datetime(2026, 2, 21, 11, 0, 0, tzinfo=timezone.utc)  # JST 20:00
    d = decide_member_discovery(
        "arale",
        streams=[],
        hints=[],
        cfg=_cfg(),
        now=now,
    )
    assert d.mode == "active_unscheduled_probe"
    assert d.interval_seconds == 300
    assert d.reason == "active_band_unscheduled_probe"
    assert d.x_refresh_interval_seconds == 1800


# ---------------------------------------------------------------------------
# Case 7: YT appointment wins over active band 5min probe
# ---------------------------------------------------------------------------
def test_case7_youtube_overrides_active_band():
    now = datetime(2026, 2, 21, 11, 0, 0, tzinfo=timezone.utc)  # evening_peak
    # far YT start
    d = decide_member_discovery(
        "arale",
        streams=[_upcoming("arale", "2026-02-22T11:00:00Z")],
        hints=[],
        cfg=_cfg(),
        now=now,
    )
    assert d.anchor_source == "youtube"
    assert d.mode == "ordinary"
    assert d.reason == "youtube_scheduled_outside_near_window"
    assert d.interval_seconds == 10800
    assert d.mode != "active_unscheduled_probe"


# ---------------------------------------------------------------------------
# Case 8: multiple upcoming → nearest
# ---------------------------------------------------------------------------
def test_case8_nearest_of_multiple_upcoming():
    now = datetime(2026, 2, 21, 1, 0, 0, tzinfo=timezone.utc)
    streams = [
        _upcoming("arale", "2026-02-22T10:00:00Z", "far"),
        _upcoming("arale", "2026-02-21T11:00:00Z", "near"),
    ]
    d = decide_member_discovery(
        "arale",
        streams=streams,
        hints=[],
        cfg=_cfg(),
        now=now,
    )
    assert d.anchor_at == "2026-02-21T11:00:00Z"


def test_x_in_pre_window_near_probe():
    # planned 13:00 UTC; now 12:58 within 300s; need active band for X path
    # Use schedule_enabled=False legacy path which still honors X anchors
    now = datetime(2026, 2, 21, 12, 58, 0, tzinfo=timezone.utc)
    d = decide_member_discovery(
        "arale",
        streams=[],
        hints=[_hint("arale", "2026-02-21T13:00:00Z")],
        cfg=_cfg(schedule_enabled=False),
        now=now,
    )
    assert d.mode == "near_probe"
    assert d.interval_seconds == 30
    assert d.anchor_source == "x"
    assert d.reason == "x_near_window"


def test_x_in_post_grace_near_probe():
    now = datetime(2026, 2, 21, 13, 10, 0, tzinfo=timezone.utc)
    d = decide_member_discovery(
        "arale",
        streams=[],
        hints=[_hint("arale", "2026-02-21T13:00:00Z")],
        cfg=_cfg(schedule_enabled=False),
        now=now,
    )
    assert d.mode == "near_probe"
    assert d.reason == "x_near_window"


def test_past_grace_falls_through_not_near_probe():
    now = datetime(2026, 2, 21, 14, 0, 0, tzinfo=timezone.utc)  # 60 min after
    d = decide_member_discovery(
        "arale",
        streams=[],
        hints=[_hint("arale", "2026-02-21T13:00:00Z")],
        cfg=_cfg(schedule_enabled=False),
        now=now,
    )
    assert d.mode == "ordinary"
    assert d.reason == "no_schedule_off_band"
    assert d.anchor_source == "none"


def test_youtube_overrides_x_even_when_x_near():
    now = datetime(2026, 2, 21, 12, 58, 0, tzinfo=timezone.utc)
    # X near, YT far → YT branch ordinary
    d = decide_member_discovery(
        "arale",
        streams=[_upcoming("arale", "2026-02-21T20:00:00Z")],
        hints=[_hint("arale", "2026-02-21T13:00:00Z")],
        cfg=_cfg(schedule_enabled=False),
        now=now,
    )
    assert d.mode == "ordinary"
    assert d.anchor_source == "youtube"
    assert d.reason == "youtube_scheduled_outside_near_window"


def test_youtube_in_near_window():
    now = datetime(2026, 2, 21, 12, 58, 0, tzinfo=timezone.utc)
    d = decide_member_discovery(
        "arale",
        streams=[_upcoming("arale", "2026-02-21T13:00:00Z")],
        hints=[],
        cfg=_cfg(),
        now=now,
    )
    assert d.mode == "near_probe"
    assert d.anchor_source == "youtube"
    assert d.reason == "youtube_near_window"


def test_one_member_near_does_not_affect_other():
    # JST 21:58 = UTC 12:58 — evening_peak
    now = datetime(2026, 2, 21, 12, 58, 0, tzinfo=timezone.utc)
    channels = [
        Channel("arale", "仲町あられ", "UC1", True),
        Channel("ritsu", "峰月律", "UC2", True),
    ]
    decisions = decide_all_members(
        channels,
        streams=[],
        hints=[_hint("arale", "2026-02-21T13:00:00Z")],
        cfg=_cfg(),
        now=now,
    )
    assert decisions["arale"].mode == "near_probe"
    assert decisions["ritsu"].mode == "active_unscheduled_probe"
    assert decisions["ritsu"].interval_seconds == 300


def test_superseded_hint_ignored():
    now = datetime(2026, 2, 21, 11, 0, 0, tzinfo=timezone.utc)  # evening
    d = decide_member_discovery(
        "arale",
        streams=[],
        hints=[_hint("arale", "2026-02-21T13:00:00Z", status="superseded")],
        cfg=_cfg(),
        now=now,
    )
    assert d.mode == "active_unscheduled_probe"
    assert d.anchor_source == "none"


def test_x_hint_ignored_outside_active_band_when_schedule_enabled():
    # Off band + active X should NOT use X path under new rules
    now = datetime(2026, 2, 21, 6, 0, 0, tzinfo=timezone.utc)  # JST 15:00
    d = decide_member_discovery(
        "arale",
        streams=[],
        hints=[_hint("arale", "2026-02-21T12:00:00Z")],
        cfg=_cfg(schedule_enabled=True),
        now=now,
    )
    assert d.reason == "no_schedule_off_band"
    assert d.anchor_source == "none"


def test_desired_x_refresh_interval_active_band():
    now = datetime(2026, 2, 21, 11, 0, 0, tzinfo=timezone.utc)
    cfg = _cfg()
    decisions = {
        "arale": decide_member_discovery(
            "arale", streams=[], hints=[], cfg=cfg, now=now
        )
    }
    assert desired_x_refresh_interval_seconds(decisions, cfg) == 1800


def test_desired_x_refresh_interval_default():
    now = datetime(2026, 2, 21, 6, 0, 0, tzinfo=timezone.utc)
    cfg = _cfg()
    decisions = {
        "arale": decide_member_discovery(
            "arale", streams=[], hints=[], cfg=cfg, now=now
        )
    }
    assert desired_x_refresh_interval_seconds(decisions, cfg) == 3600


def test_sampling_independent_of_near_probe():
    cfg = _cfg(schedule_enabled=False, sampling_interval_seconds=45)
    now = datetime(2026, 2, 21, 12, 58, 0, tzinfo=timezone.utc)
    assert sampling_interval_seconds(cfg, now) == 45


def test_legacy_schedule_disabled_off_band_uses_no_schedule_interval():
    now = datetime(2026, 2, 21, 6, 0, 0, tzinfo=timezone.utc)
    d = decide_member_discovery(
        "arale",
        streams=[],
        hints=[],
        cfg=_cfg(
            schedule_enabled=False,
            discovery_no_schedule_off_band_interval_seconds=7200,
        ),
        now=now,
    )
    assert d.interval_seconds == 7200
    assert d.reason == "no_schedule_off_band"
