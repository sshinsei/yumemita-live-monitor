"""Failure isolation: X errors must not break discovery decision path (ticket 08)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from x_schedule_monitor.config import AppConfig
from x_schedule_monitor.schedule_feed import ScheduleFeedService
from x_schedule_monitor.schedule_store import ScheduleHintStore
from x_schedule_monitor.x_client import XAPIError


def test_x_api_error_returns_stats_not_raise(tmp_path: Path):
    cfg = AppConfig(
        youtube_api_key="k",
        x_schedule_enabled=True,
        x_bearer_token_env="X_BEARER_TOKEN",
        x_schedule_hints_file=str(tmp_path / "hints.json"),
    )
    # Fake token via env is not needed if we inject client
    store = ScheduleHintStore(tmp_path / "hints.json")
    client = MagicMock()
    client.recent_search.side_effect = XAPIError("quota", kind="quota", retryable=True)
    client.close = MagicMock()
    feed = ScheduleFeedService(cfg, store, client=client)
    # resolve token: patch by setting env-less — refresh checks token first
    # Inject path: ScheduleFeedService.refresh checks resolve_x_bearer_token
    # So monkeypatch token by putting env is easier — do client only after token
    import os

    os.environ["X_BEARER_TOKEN"] = "dummy"
    try:
        stats = feed.refresh()
    finally:
        os.environ.pop("X_BEARER_TOKEN", None)
    assert stats.error
    assert "quota" in stats.error or stats.fetched == 0
    # store still usable
    assert store.active() == []


def test_disabled_x_skips_api(tmp_path: Path):
    cfg = AppConfig(
        youtube_api_key="k",
        x_schedule_enabled=False,
        x_schedule_hints_file=str(tmp_path / "hints.json"),
    )
    store = ScheduleHintStore(tmp_path / "hints.json")
    client = MagicMock()
    feed = ScheduleFeedService(cfg, store, client=client)
    stats = feed.refresh()
    assert stats.error == "x_schedule_disabled"
    client.recent_search.assert_not_called()
