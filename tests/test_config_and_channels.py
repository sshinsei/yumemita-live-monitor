"""Config + channels scaffold tests (ticket 01)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from x_schedule_monitor.channels import load_channels
from x_schedule_monitor.config import validate_config_dict


ROOT = Path(__file__).resolve().parents[1]


def test_channels_csv_loads():
    channels = load_channels(ROOT / "channels.csv")
    assert len(channels) == 5
    keys = {c.member_key for c in channels}
    assert keys == {"arale", "nonoka", "ritsu", "miyako", "yuno"}


def test_config_example_x_defaults():
    data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    assert data["x_schedule_enabled"] is False
    assert data["discovery_near_probe_interval_seconds"] == 30
    data["youtube_api_key"] = "real-test-key"
    cfg = validate_config_dict(data)
    assert cfg.x_schedule_enabled is False
    assert cfg.discovery_near_pre_start_window_seconds == 300


def test_near_probe_hard_min():
    data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    data["youtube_api_key"] = "real-test-key"
    data["discovery_near_probe_interval_seconds"] = 10
    with pytest.raises(ValueError, match="discovery_near_probe"):
        validate_config_dict(data)


def test_x_enabled_requires_token(monkeypatch):
    data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    data["youtube_api_key"] = "real-test-key"
    data["x_schedule_enabled"] = True
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    with pytest.raises(ValueError, match="X_BEARER_TOKEN"):
        validate_config_dict(data)


def test_time_bands_only_need_window_and_sampling():
    data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    data["youtube_api_key"] = "real-test-key"
    for band in data["time_bands"]:
        assert "idle_discovery_seconds" not in band
        assert "scheduled_discovery_seconds" not in band
        assert "sampling_seconds" in band
    assert "idle_discovery_seconds" not in data["off_peak"]
    cfg = validate_config_dict(data)
    assert all(b.sampling_seconds > 0 for b in cfg.time_bands)
    assert cfg.off_peak.sampling_seconds == 60


def test_deprecated_band_discovery_keys_ignored():
    data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    data["youtube_api_key"] = "real-test-key"
    data["idle_discovery_interval_seconds"] = 999
    data["time_bands"][0]["idle_discovery_seconds"] = 111
    data["time_bands"][0]["scheduled_discovery_seconds"] = 222
    data["off_peak"]["idle_discovery_seconds"] = 333
    cfg = validate_config_dict(data)
    # Deprecated keys must not become discovery cadence or band fields
    assert not hasattr(cfg, "idle_discovery_interval_seconds")
    assert not hasattr(cfg.time_bands[0], "idle_discovery_seconds")
    assert cfg.discovery_no_schedule_off_band_interval_seconds == 7200
