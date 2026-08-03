"""Channel configuration loading from channels.csv."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import List, Optional

from .models import DEFAULT_MEMBER_COLORS, Channel, stable_fallback_color

logger = logging.getLogger("yumemita_live_monitor.channels")

REQUIRED_FIELDS = ("member_key", "member_name", "channel_id", "enabled")


class ChannelConfigError(Exception):
    """Raised when channels.csv is invalid."""


def _parse_enabled(value: str) -> bool:
    v = (value or "").strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"enabled must be 0 or 1, got '{value}'")


def load_channels(path: str | Path, *, require_enabled: bool = True) -> List[Channel]:
    path = Path(path)
    if not path.exists():
        raise ChannelConfigError(f"Channels file not found: {path}")

    channels: List[Channel] = []
    errors: List[str] = []
    seen_keys: set[str] = set()
    seen_ids: set[str] = set()

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ChannelConfigError(f"{path}: empty or missing header")

        headers = {h.strip() for h in reader.fieldnames if h}
        missing = [c for c in REQUIRED_FIELDS if c not in headers]
        if missing:
            raise ChannelConfigError(
                f"{path}: missing required columns: {', '.join(missing)}"
            )

        for i, row in enumerate(reader, start=2):
            if not row or all(not (v or "").strip() for v in row.values()):
                continue

            line_errs: List[str] = []
            member_key = (row.get("member_key") or "").strip()
            member_name = (row.get("member_name") or "").strip()
            channel_id = (row.get("channel_id") or "").strip()
            enabled_raw = (row.get("enabled") or "").strip()
            color_raw = (row.get("color") or "").strip() if "color" in headers else ""

            if not member_key:
                line_errs.append("member_key is required")
            if not member_name:
                line_errs.append("member_name is required")
            if not channel_id:
                line_errs.append("channel_id is required")
            elif not channel_id.startswith("UC"):
                line_errs.append(f"channel_id should start with UC, got '{channel_id}'")

            enabled = False
            try:
                enabled = _parse_enabled(enabled_raw)
            except ValueError as e:
                line_errs.append(str(e))

            if member_key and member_key in seen_keys:
                line_errs.append(f"duplicate member_key '{member_key}'")
            if channel_id and channel_id in seen_ids:
                line_errs.append(f"duplicate channel_id '{channel_id}'")

            color: Optional[str] = color_raw or None
            if color and not color.startswith("#"):
                line_errs.append(f"color must be hex like #RRGGBB, got '{color}'")

            if line_errs:
                errors.append(f"line {i}: " + "; ".join(line_errs))
                continue

            seen_keys.add(member_key)
            seen_ids.add(channel_id)

            if not color and member_key not in DEFAULT_MEMBER_COLORS:
                fb = stable_fallback_color(member_key)
                logger.warning(
                    "Member '%s' has no color configured; using fallback %s",
                    member_key,
                    fb,
                )
            channels.append(
                Channel(
                    member_key=member_key,
                    member_name=member_name,
                    channel_id=channel_id,
                    enabled=enabled,
                    color=color,
                )
            )

    if errors:
        raise ChannelConfigError(
            f"Invalid channels file {path}:\n  - " + "\n  - ".join(errors)
        )

    if not channels:
        raise ChannelConfigError(f"{path}: no channel rows found")

    enabled_list = [c for c in channels if c.enabled]
    if require_enabled and not enabled_list:
        raise ChannelConfigError(f"{path}: no enabled channels (all enabled=0)")

    logger.info(
        "Loaded %d channels (%d enabled) from %s",
        len(channels),
        len(enabled_list),
        path,
    )
    return channels


def enabled_channels(channels: List[Channel]) -> List[Channel]:
    return [c for c in channels if c.enabled]
