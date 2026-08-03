"""Shared utilities: time, atomic writes, logging helpers."""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union
from zoneinfo import ZoneInfo

PathLike = Union[str, Path]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now_iso() -> str:
    return format_utc(utc_now())


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def parse_youtube_time(value: Optional[str]) -> str:
    dt = parse_iso(value)
    return format_utc(dt) if dt else ""


def get_tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def ensure_dir(path: PathLike) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def atomic_write_text(path: PathLike, content: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    ensure_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def setup_logging(log_dir: PathLike, log_name: str = "x_schedule_monitor.log") -> logging.Logger:
    ensure_dir(log_dir)
    log_path = Path(log_dir) / log_name
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(getattr(h, "_x_schedule_monitor", False) for h in root.handlers):
        fmt = logging.Formatter(
            "%(asctime)s - %(levelname)s - [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh._x_schedule_monitor = True  # type: ignore[attr-defined]
        root.addHandler(sh)

        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        fh._x_schedule_monitor = True  # type: ignore[attr-defined]
        root.addHandler(fh)
    return logging.getLogger("x_schedule_monitor")


def uploads_playlist_id(channel_id: str) -> str:
    if channel_id.startswith("UC") and len(channel_id) > 2:
        return "UU" + channel_id[2:]
    return channel_id
