"""Per-member discovery interval decision tree (appointment-first).

Decision order:
1. Valid YouTube upcoming (nearest) → schedule around that start time
2. Else if in Tokyo time band → check X hint; else off-band ordinary (2h)
3. Active band + X planned_start → schedule around that start time
4. Active band + no X → 5min YouTube probe (+ 30min X refresh cadence)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from .config import AppConfig
from .models import Channel, ScheduleHint, StreamRecord
from .schedule import match_time_band, resolve_profile
from .utils import parse_iso, utc_now

logger = logging.getLogger("yumemita_live_monitor.discovery_policy")


@dataclass(frozen=True)
class MemberDiscoveryDecision:
    member_key: str
    interval_seconds: int
    mode: str  # near_probe | ordinary | active_unscheduled_probe
    reason: str
    anchor_source: str  # youtube | x | none
    anchor_at: str = ""
    profile_name: str = ""
    next_run_at: Optional[datetime] = None
    x_refresh_interval_seconds: Optional[int] = None


def _ensure_utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _member_streams(
    streams: Sequence[StreamRecord],
    member_key: str,
) -> List[StreamRecord]:
    return [s for s in streams if s.member_key == member_key]


def _anchor_still_valid(
    now: datetime,
    start: datetime,
    *,
    grace_seconds: int,
) -> bool:
    """Valid until start + grace (absolute delta; no calendar-day check)."""
    return now <= start + timedelta(seconds=grace_seconds)


def _best_youtube_anchor(
    streams: Sequence[StreamRecord],
    now: datetime,
    *,
    grace_seconds: int,
) -> Optional[datetime]:
    """Nearest valid YouTube upcoming scheduledStartTime; None if none."""
    best: Optional[datetime] = None
    for s in streams:
        if s.status != "upcoming":
            continue
        dt = parse_iso(s.scheduled_start_at)
        if dt is None:
            continue
        if not _anchor_still_valid(now, dt, grace_seconds=grace_seconds):
            continue
        if best is None or dt < best:
            best = dt
    return best


def _best_x_anchor(
    hints: Sequence[ScheduleHint],
    now: datetime,
    *,
    grace_seconds: int,
) -> Optional[datetime]:
    best: Optional[datetime] = None
    for h in hints:
        if h.status != "active":
            continue
        dt = parse_iso(h.planned_start_at)
        if dt is None:
            continue
        if not _anchor_still_valid(now, dt, grace_seconds=grace_seconds):
            continue
        if best is None or dt < best:
            best = dt
    return best


def decide_for_known_start(
    *,
    member_key: str,
    now: datetime,
    start: datetime,
    source: str,
    ordinary_interval_seconds: int,
    pre_seconds: int,
    grace_seconds: int,
    near_probe_seconds: int,
    band_name: str = "",
) -> Optional[MemberDiscoveryDecision]:
    """Schedule around a known start time. Returns None if past grace (expired)."""
    pre = timedelta(seconds=pre_seconds)
    grace = timedelta(seconds=grace_seconds)
    near_start = start - pre
    near_end = start + grace
    anchor_at = start.strftime("%Y-%m-%dT%H:%M:%SZ")

    if near_start <= now <= near_end:
        next_run = now + timedelta(seconds=near_probe_seconds)
        logger.info(
            "discovery near_probe: member=%s anchor=%s interval=%ss until=%s",
            member_key,
            source,
            near_probe_seconds,
            near_end.isoformat(),
        )
        return MemberDiscoveryDecision(
            member_key=member_key,
            interval_seconds=near_probe_seconds,
            mode="near_probe",
            reason=f"{source}_near_window",
            anchor_source=source,
            anchor_at=anchor_at,
            profile_name=band_name,
            next_run_at=next_run,
        )

    if now < near_start:
        ordinary = timedelta(seconds=ordinary_interval_seconds)
        next_run = min(now + ordinary, near_start)
        # Effective wait may be shorter than nominal interval when clamped to near window
        effective = max(1, int((next_run - now).total_seconds()))
        logger.info(
            "discovery ordinary: member=%s reason=%s_scheduled_outside_near_window "
            "interval=%ss next_in=%ss anchor=%s",
            member_key,
            source,
            ordinary_interval_seconds,
            effective,
            anchor_at,
        )
        return MemberDiscoveryDecision(
            member_key=member_key,
            interval_seconds=ordinary_interval_seconds,
            mode="ordinary",
            reason=f"{source}_scheduled_outside_near_window",
            anchor_source=source,
            anchor_at=anchor_at,
            profile_name=band_name,
            next_run_at=next_run,
        )

    # Past near window: caller should fall through (do not stick on 30s polling)
    logger.info(
        "discovery schedule_expired: member=%s source=%s start=%s now=%s",
        member_key,
        source,
        anchor_at,
        now.isoformat(),
    )
    return None


def decide_member_discovery(
    member_key: str,
    *,
    streams: Sequence[StreamRecord],
    hints: Sequence[ScheduleHint],
    cfg: AppConfig,
    now: Optional[datetime] = None,
) -> MemberDiscoveryDecision:
    """
    Appointment-first decision tree:
    1) Valid YT upcoming → known-start scheduling (3h / near_probe / expire)
    2) Else Tokyo time band (if schedule_enabled)
    3) Off band → 2h ordinary (no_schedule_off_band)
    4) In band + valid X planned_start → known-start scheduling
    5) In band + no X → 5min active unscheduled probe
    """
    now = _ensure_utc(now or utc_now())

    member_streams = _member_streams(streams, member_key)
    member_hints = [h for h in hints if h.member_key == member_key and h.status == "active"]

    pre = cfg.discovery_near_pre_start_window_seconds
    grace = cfg.discovery_near_post_start_grace_seconds
    near_iv = cfg.discovery_near_probe_interval_seconds
    known_iv = cfg.discovery_known_schedule_interval_seconds
    off_band_iv = cfg.discovery_no_schedule_off_band_interval_seconds
    active_yt_iv = cfg.discovery_active_band_youtube_interval_seconds
    active_x_iv = cfg.discovery_active_band_x_refresh_interval_seconds

    # --- 1. YouTube appointment first (absolute time; no "today" check) ---
    yt_anchor = _best_youtube_anchor(member_streams, now, grace_seconds=grace)
    if yt_anchor is not None:
        decided = decide_for_known_start(
            member_key=member_key,
            now=now,
            start=yt_anchor,
            source="youtube",
            ordinary_interval_seconds=known_iv,
            pre_seconds=pre,
            grace_seconds=grace,
            near_probe_seconds=near_iv,
        )
        if decided is not None:
            return decided
        # expired YT: fall through as if no valid appointment

    # --- 2. Time band only when no valid YT appointment ---
    band_name = ""
    in_active_band = False
    if cfg.schedule_enabled:
        band = match_time_band(
            cfg.time_bands,
            now_utc=now,
            tz_name=cfg.schedule_timezone,
        )
        if band is not None:
            in_active_band = True
            band_name = band.name
    else:
        # schedule_enabled=false: treat as always "off band" for YT cadence,
        # but still honor X known starts (legacy isolation tests / offline mode).
        in_active_band = False

    # --- 3. Off band: more frequent than known-schedule ordinary (2h vs 3h) ---
    if not in_active_band:
        # Legacy path: allow X known-start even outside bands when schedule disabled
        if not cfg.schedule_enabled:
            x_anchor = _best_x_anchor(member_hints, now, grace_seconds=grace)
            if x_anchor is not None:
                decided = decide_for_known_start(
                    member_key=member_key,
                    now=now,
                    start=x_anchor,
                    source="x",
                    ordinary_interval_seconds=known_iv,
                    pre_seconds=pre,
                    grace_seconds=grace,
                    near_probe_seconds=near_iv,
                    band_name="legacy",
                )
                if decided is not None:
                    return decided
            # schedule_enabled=false: still use no-schedule off-band cadence
            off_band_iv = cfg.discovery_no_schedule_off_band_interval_seconds

        next_run = now + timedelta(seconds=off_band_iv)
        logger.info(
            "discovery ordinary: member=%s reason=no_schedule_off_band interval=%ss",
            member_key,
            off_band_iv,
        )
        return MemberDiscoveryDecision(
            member_key=member_key,
            interval_seconds=off_band_iv,
            mode="ordinary",
            reason="no_schedule_off_band",
            anchor_source="none",
            profile_name=band_name or ("legacy" if not cfg.schedule_enabled else "off_band"),
            next_run_at=next_run,
        )

    # --- 4. Active band: X schedule as known plan ---
    x_anchor = _best_x_anchor(member_hints, now, grace_seconds=grace)
    if x_anchor is not None:
        decided = decide_for_known_start(
            member_key=member_key,
            now=now,
            start=x_anchor,
            source="x",
            ordinary_interval_seconds=known_iv,
            pre_seconds=pre,
            grace_seconds=grace,
            near_probe_seconds=near_iv,
            band_name=band_name,
        )
        if decided is not None:
            return decided
        # expired X: fall through to unscheduled probe

    # --- 5. Active band, no valid plan: probe for surprise streams ---
    next_run = now + timedelta(seconds=active_yt_iv)
    logger.info(
        "discovery active_unscheduled_probe: member=%s band=%s "
        "youtube_interval=%ss x_refresh_interval=%ss",
        member_key,
        band_name,
        active_yt_iv,
        active_x_iv,
    )
    return MemberDiscoveryDecision(
        member_key=member_key,
        interval_seconds=active_yt_iv,
        mode="active_unscheduled_probe",
        reason="active_band_unscheduled_probe",
        anchor_source="none",
        profile_name=band_name,
        next_run_at=next_run,
        x_refresh_interval_seconds=active_x_iv,
    )


def decide_all_members(
    channels: Sequence[Channel],
    *,
    streams: Sequence[StreamRecord],
    hints: Sequence[ScheduleHint],
    cfg: AppConfig,
    now: Optional[datetime] = None,
) -> Dict[str, MemberDiscoveryDecision]:
    now = _ensure_utc(now or utc_now())
    out: Dict[str, MemberDiscoveryDecision] = {}
    for ch in channels:
        if not ch.enabled:
            continue
        out[ch.member_key] = decide_member_discovery(
            ch.member_key,
            streams=streams,
            hints=hints,
            cfg=cfg,
            now=now,
        )
    return out


def desired_x_refresh_interval_seconds(
    decisions: Dict[str, MemberDiscoveryDecision],
    cfg: AppConfig,
) -> int:
    """Pick X refresh cadence: active-band unscheduled probe needs 30min; else global."""
    for d in decisions.values():
        if d.x_refresh_interval_seconds is not None:
            return d.x_refresh_interval_seconds
        if d.reason == "active_band_unscheduled_probe":
            return cfg.discovery_active_band_x_refresh_interval_seconds
    return cfg.x_schedule_refresh_interval_seconds


def sampling_interval_seconds(cfg: AppConfig, now: Optional[datetime] = None) -> int:
    """Sampling stays on global band path — independent of discovery tree."""
    now = now or utc_now()
    if not cfg.schedule_enabled:
        return cfg.sampling_interval_seconds
    profile = resolve_profile(
        cfg.time_bands,
        cfg.off_peak,
        now_utc=now,
        tz_name=cfg.schedule_timezone,
    )
    return profile.sampling_seconds
