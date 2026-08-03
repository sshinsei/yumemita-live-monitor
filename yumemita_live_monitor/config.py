"""Configuration loading and validation."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .schedule import (
    SamplingProfile,
    TimeBand,
    default_off_peak,
    default_time_bands,
)

logger = logging.getLogger("yumemita_live_monitor.config")

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
TIME_RE_24 = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$|^24:00$")

# Deprecated discovery keys (time_bands / off_peak / top-level). Accepted then ignored.
_DEPRECATED_DISCOVERY_KEYS = frozenset(
    {
        "idle_discovery_seconds",
        "scheduled_discovery_seconds",
        "idle_discovery_interval_seconds",
        "scheduled_discovery_interval_seconds",
        "active_discovery_seconds",
        "active_discovery_interval_seconds",
    }
)

MIN_INTERVALS = {
    "sampling_interval_seconds": 15,
    "request_timeout_seconds": 3,
    "max_retries": 0,
    "sampling_seconds": 15,
    "x_schedule_refresh_interval_seconds": 300,
    "discovery_near_pre_start_window_seconds": 60,
    "discovery_near_post_start_grace_seconds": 60,
    "discovery_near_probe_interval_seconds": 30,
    "discovery_known_schedule_interval_seconds": 60,
    "discovery_no_schedule_off_band_interval_seconds": 60,
    "discovery_active_band_youtube_interval_seconds": 30,
    "discovery_active_band_x_refresh_interval_seconds": 60,
}

NEAR_PROBE_HARD_MIN = 30


@dataclass
class AppConfig:
    youtube_api_key: str
    channels_file: str = "channels.csv"
    data_dir: str = "data"
    log_dir: str = "logs"
    sampling_interval_seconds: int = 45
    request_timeout_seconds: int = 10
    max_retries: int = 3
    schedule_timezone: str = "Asia/Tokyo"
    schedule_enabled: bool = True
    time_bands: List[TimeBand] = field(default_factory=default_time_bands)
    off_peak: SamplingProfile = field(default_factory=default_off_peak)
    discovery_method: str = "uploads"
    discovery_playlist_max_results: int = 15
    videos_batch_size: int = 50
    state_file: str = "data/runtime_state.json"
    streams_file: str = "data/streams.csv"
    samples_dir: str = "data/viewer_samples"
    # Weekly reports
    report_timezone: str = "Asia/Tokyo"
    weekly_report_day: int = 1  # ISO weekday 1=Mon .. 7=Sun
    weekly_report_time: str = "09:00"
    weekly_reports_dir: str = "data/weekly_reports"
    # X schedule
    x_schedule_enabled: bool = False
    x_schedule_username: str = "BDP_yumemita"
    x_bearer_token_env: str = "X_BEARER_TOKEN"
    x_schedule_refresh_interval_seconds: int = 3600
    x_schedule_hints_file: str = "data/schedule_hints.json"
    # When true: メン限 lines set ScheduleHint.member_only and get dedicated logs.
    # When false: still strip メン限 markers so the line parses, but treat as ordinary hint.
    x_schedule_member_only_enabled: bool = False
    discovery_near_pre_start_window_seconds: int = 300
    discovery_near_post_start_grace_seconds: int = 1800
    discovery_near_probe_interval_seconds: int = 30
    # Discovery scheduler (appointment-first; time bands only when unscheduled)
    discovery_known_schedule_interval_seconds: int = 10800  # 3h when start known
    discovery_no_schedule_off_band_interval_seconds: int = 7200  # 2h off peak, no YT
    discovery_active_band_youtube_interval_seconds: int = 300  # 5min unscheduled probe
    discovery_active_band_x_refresh_interval_seconds: int = 1800  # 30min X refresh

    @property
    def streams_path(self) -> Path:
        return Path(self.streams_file)

    @property
    def state_path(self) -> Path:
        return Path(self.state_file)

    @property
    def samples_path(self) -> Path:
        return Path(self.samples_dir)

    @property
    def schedule_hints_path(self) -> Path:
        return Path(self.x_schedule_hints_file)

    @property
    def weekly_reports_path(self) -> Path:
        return Path(self.weekly_reports_dir)

    def resolve_x_bearer_token(self) -> Optional[str]:
        env = self.x_bearer_token_env or "X_BEARER_TOKEN"
        val = os.environ.get(env, "").strip()
        return val or None


def _require_int(data: Dict[str, Any], key: str, default: Optional[int] = None) -> int:
    if key not in data:
        if default is not None:
            return default
        raise ValueError(f"Missing required config key: {key}")
    val = data[key]
    if isinstance(val, bool) or not isinstance(val, int):
        if isinstance(val, bool):
            raise ValueError(f"Config key '{key}' must be an integer, got boolean")
        try:
            val = int(val)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"Config key '{key}' must be an integer, got {type(data[key]).__name__}"
            ) from e
    min_val = MIN_INTERVALS.get(key)
    if min_val is not None and val < min_val:
        raise ValueError(
            f"Config key '{key}'={val} is below minimum allowed value {min_val}"
        )
    if key.endswith("_seconds") and val <= 0:
        raise ValueError(f"Config key '{key}' must be a positive integer")
    return val


def _require_str(data: Dict[str, Any], key: str, default: Optional[str] = None) -> str:
    if key not in data:
        if default is not None:
            return default
        raise ValueError(f"Missing required config key: {key}")
    val = data[key]
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"Config key '{key}' must be a non-empty string")
    return val.strip()


def _parse_hhmm(value: str, *, allow_24: bool = False) -> tuple[time, bool]:
    s = (value or "").strip()
    pat = TIME_RE_24 if allow_24 else TIME_RE
    if not pat.match(s):
        raise ValueError(
            f"Invalid time '{value}', expected HH:MM"
            + (" or 24:00" if allow_24 else "")
        )
    if s == "24:00":
        return time(0, 0), True
    h, m = s.split(":")
    return time(int(h), int(m)), False


def _positive_seconds(obj: Dict[str, Any], key: str, default: int) -> int:
    val = obj.get(key, default)
    try:
        iv = int(val)
    except (TypeError, ValueError) as e:
        raise ValueError(f"'{key}' must be an integer") from e
    if isinstance(val, bool):
        raise ValueError(f"'{key}' must be an integer, got boolean")
    min_val = MIN_INTERVALS.get(key, 1)
    if iv < min_val:
        raise ValueError(f"'{key}'={iv} is below minimum allowed value {min_val}")
    return iv


def _warn_deprecated_discovery_keys(obj: Dict[str, Any], where: str) -> None:
    found = sorted(k for k in _DEPRECATED_DISCOVERY_KEYS if k in obj)
    if found:
        logger.warning(
            "%s: ignoring deprecated discovery interval key(s) %s "
            "(use discovery_*_interval_seconds on AppConfig instead)",
            where,
            ", ".join(found),
        )


def _parse_time_bands(raw: Any) -> List[TimeBand]:
    if raw is None:
        return default_time_bands()
    if not isinstance(raw, list):
        raise ValueError("time_bands must be a JSON array")
    bands: List[TimeBand] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"time_bands[{i}] must be an object")
        _warn_deprecated_discovery_keys(item, f"time_bands[{i}]")
        name = str(item.get("name") or f"band_{i}").strip()
        start_s = item.get("start")
        end_s = item.get("end")
        if not isinstance(start_s, str) or not isinstance(end_s, str):
            raise ValueError(f"time_bands[{i}] requires string start/end (HH:MM)")
        start_t, _ = _parse_hhmm(start_s, allow_24=False)
        end_t, end_mid = _parse_hhmm(end_s, allow_24=True)

        days_raw = item.get("days")
        days: Optional[frozenset[int]] = None
        if days_raw is not None:
            if not isinstance(days_raw, list) or not days_raw:
                raise ValueError(f"time_bands[{i}].days must be a non-empty array of 1-7")
            ds = []
            for d in days_raw:
                try:
                    di = int(d)
                except (TypeError, ValueError) as e:
                    raise ValueError(
                        f"time_bands[{i}].days entries must be integers 1-7"
                    ) from e
                if di < 1 or di > 7:
                    raise ValueError(
                        f"time_bands[{i}].days entries must be 1-7, got {di}"
                    )
                ds.append(di)
            days = frozenset(ds)

        bands.append(
            TimeBand(
                name=name,
                start=start_t,
                end=end_t,
                end_is_midnight=end_mid,
                days=days,
                sampling_seconds=_positive_seconds(item, "sampling_seconds", 45),
            )
        )
    return bands


def _parse_off_peak(raw: Any) -> SamplingProfile:
    if raw is None:
        return default_off_peak()
    if not isinstance(raw, dict):
        raise ValueError("off_peak must be a JSON object")
    _warn_deprecated_discovery_keys(raw, "off_peak")
    return SamplingProfile(
        name=str(raw.get("name") or "off_peak"),
        sampling_seconds=_positive_seconds(raw, "sampling_seconds", 60),
    )


def validate_config_dict(data: Dict[str, Any]) -> AppConfig:
    if not isinstance(data, dict):
        raise ValueError("Config root must be a JSON object")

    api_key = data.get("youtube_api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("youtube_api_key is required and must be a non-empty string")
    if api_key.strip() in {"YOUR_YOUTUBE_API_KEY_HERE", "YOUR_API_KEY"}:
        raise ValueError(
            "youtube_api_key still has the placeholder value. "
            "Copy config.example.json to config.json and set a real API key."
        )

    schedule_tz = _require_str(data, "schedule_timezone", "Asia/Tokyo")
    try:
        ZoneInfo(schedule_tz)
    except ZoneInfoNotFoundError as e:
        raise ValueError(
            f"schedule_timezone '{schedule_tz}' is not a valid IANA timezone"
        ) from e

    report_tz = _require_str(data, "report_timezone", schedule_tz)
    try:
        ZoneInfo(report_tz)
    except ZoneInfoNotFoundError as e:
        raise ValueError(
            f"report_timezone '{report_tz}' is not a valid IANA timezone"
        ) from e

    schedule_enabled = data.get("schedule_enabled", True)
    if not isinstance(schedule_enabled, bool):
        raise ValueError("schedule_enabled must be a boolean")

    weekly_day = _require_int(data, "weekly_report_day", 1)
    if weekly_day < 1 or weekly_day > 7:
        raise ValueError("weekly_report_day must be ISO weekday 1 (Mon) .. 7 (Sun)")
    weekly_time = _require_str(data, "weekly_report_time", "09:00")
    if not TIME_RE.match(weekly_time):
        raise ValueError("weekly_report_time must be HH:MM in 24-hour format")

    x_enabled = data.get("x_schedule_enabled", False)
    if not isinstance(x_enabled, bool):
        raise ValueError("x_schedule_enabled must be a boolean")

    member_only_enabled = data.get("x_schedule_member_only_enabled", False)
    if not isinstance(member_only_enabled, bool):
        raise ValueError("x_schedule_member_only_enabled must be a boolean")

    method = _require_str(data, "discovery_method", "uploads").lower()
    if method not in {"uploads", "search", "hybrid"}:
        raise ValueError("discovery_method must be one of: uploads, search, hybrid")

    batch = _require_int(data, "videos_batch_size", 50)
    if batch < 1 or batch > 50:
        raise ValueError("videos_batch_size must be between 1 and 50")

    playlist_max = _require_int(data, "discovery_playlist_max_results", 15)
    if playlist_max < 1 or playlist_max > 50:
        raise ValueError("discovery_playlist_max_results must be between 1 and 50")

    near_probe = _require_int(data, "discovery_near_probe_interval_seconds", 30)
    if near_probe < NEAR_PROBE_HARD_MIN:
        raise ValueError(
            f"discovery_near_probe_interval_seconds must be >= {NEAR_PROBE_HARD_MIN}"
        )

    _warn_deprecated_discovery_keys(data, "config root")

    data_dir = _require_str(data, "data_dir", "data")
    cfg = AppConfig(
        youtube_api_key=api_key.strip(),
        channels_file=_require_str(data, "channels_file", "channels.csv"),
        data_dir=data_dir,
        log_dir=_require_str(data, "log_dir", "logs"),
        sampling_interval_seconds=_require_int(data, "sampling_interval_seconds", 45),
        request_timeout_seconds=_require_int(data, "request_timeout_seconds", 10),
        max_retries=_require_int(data, "max_retries", 3),
        schedule_timezone=schedule_tz,
        schedule_enabled=schedule_enabled,
        time_bands=_parse_time_bands(data.get("time_bands")),
        off_peak=_parse_off_peak(data.get("off_peak")),
        discovery_method=method,
        discovery_playlist_max_results=playlist_max,
        videos_batch_size=batch,
        state_file=_require_str(data, "state_file", f"{data_dir}/runtime_state.json"),
        streams_file=_require_str(data, "streams_file", f"{data_dir}/streams.csv"),
        samples_dir=_require_str(data, "samples_dir", f"{data_dir}/viewer_samples"),
        report_timezone=report_tz,
        weekly_report_day=weekly_day,
        weekly_report_time=weekly_time,
        weekly_reports_dir=_require_str(
            data, "weekly_reports_dir", f"{data_dir}/weekly_reports"
        ),
        x_schedule_enabled=x_enabled,
        x_schedule_username=_require_str(data, "x_schedule_username", "BDP_yumemita"),
        x_bearer_token_env=_require_str(data, "x_bearer_token_env", "X_BEARER_TOKEN"),
        x_schedule_refresh_interval_seconds=_require_int(
            data, "x_schedule_refresh_interval_seconds", 3600
        ),
        x_schedule_hints_file=_require_str(
            data, "x_schedule_hints_file", f"{data_dir}/schedule_hints.json"
        ),
        x_schedule_member_only_enabled=member_only_enabled,
        discovery_near_pre_start_window_seconds=_require_int(
            data, "discovery_near_pre_start_window_seconds", 300
        ),
        discovery_near_post_start_grace_seconds=_require_int(
            data, "discovery_near_post_start_grace_seconds", 1800
        ),
        discovery_near_probe_interval_seconds=near_probe,
        # New appointment-first discovery cadence (legacy configs keep working via defaults)
        discovery_known_schedule_interval_seconds=_require_int(
            data, "discovery_known_schedule_interval_seconds", 10800
        ),
        discovery_no_schedule_off_band_interval_seconds=_require_int(
            data, "discovery_no_schedule_off_band_interval_seconds", 7200
        ),
        discovery_active_band_youtube_interval_seconds=_require_int(
            data, "discovery_active_band_youtube_interval_seconds", 300
        ),
        discovery_active_band_x_refresh_interval_seconds=_require_int(
            data, "discovery_active_band_x_refresh_interval_seconds", 1800
        ),
    )

    if cfg.x_schedule_enabled and not cfg.resolve_x_bearer_token():
        raise ValueError(
            f"x_schedule_enabled is true but environment variable "
            f"'{cfg.x_bearer_token_env}' is empty or unset"
        )

    return cfg


def load_config(path: str | Path = "config.json") -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration file {path} not found. "
            "Copy config.example.json to config.json and set your API keys."
        )
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in configuration file {path}: {e}") from e

    cfg = validate_config_dict(data)
    logger.info("Loaded config from %s", path)
    return cfg


def load_config_allow_placeholder(path: str | Path = "config.example.json") -> AppConfig:
    """Load config even with placeholder YouTube key (for offline CLI / tests)."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("youtube_api_key") in {
        "YOUR_YOUTUBE_API_KEY_HERE",
        "YOUR_API_KEY",
        None,
        "",
    }:
        data = {**data, "youtube_api_key": "test-placeholder-key-not-for-production"}
    # Don't require X token when disabled
    data["x_schedule_enabled"] = bool(data.get("x_schedule_enabled", False))
    return validate_config_dict(data)
