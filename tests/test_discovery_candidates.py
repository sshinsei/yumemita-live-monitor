"""X video_id candidate channel validation (ticket 06)."""

from __future__ import annotations

from unittest.mock import MagicMock

from x_schedule_monitor.config import AppConfig
from x_schedule_monitor.discovery import StreamDiscoveryService, UploadsPlaylistDiscovery
from x_schedule_monitor.models import Channel


def test_extra_video_id_channel_mismatch_rejected():
    client = MagicMock()
    client.videos_list.return_value = [
        {
            "id": "badvid12345",
            "snippet": {
                "channelId": "UCotherchannel0000000000",
                "title": "other",
            },
            "liveStreamingDetails": {
                "scheduledStartTime": "2026-02-21T13:00:00Z",
            },
        }
    ]
    ch = Channel("arale", "仲町あられ", "UCWfF0DB6m_t2CE3KcOOOX7g", True)
    channel_map = {ch.channel_id: ch}
    cfg = AppConfig(youtube_api_key="k", videos_batch_size=50)
    strategy = UploadsPlaylistDiscovery(client, max_results=1)
    svc = StreamDiscoveryService(client, strategy, cfg, channel_map)
    # strategy not called if only_member_keys empty channels... we pass channels
    strategy.discover_channel = MagicMock(return_value=[])  # type: ignore
    records, errors = svc.discover(
        [ch],
        {},
        extra_video_ids=["badvid12345"],
        only_member_keys=["arale"],
    )
    assert records == []


def test_extra_video_id_matching_channel_accepted():
    client = MagicMock()
    ch = Channel("arale", "仲町あられ", "UCWfF0DB6m_t2CE3KcOOOX7g", True)
    client.videos_list.return_value = [
        {
            "id": "goodvid12345",
            "snippet": {
                "channelId": ch.channel_id,
                "title": "live",
            },
            "liveStreamingDetails": {
                "scheduledStartTime": "2026-02-21T13:00:00Z",
            },
        }
    ]
    channel_map = {ch.channel_id: ch}
    cfg = AppConfig(youtube_api_key="k", videos_batch_size=50)
    strategy = UploadsPlaylistDiscovery(client, max_results=1)
    strategy.discover_channel = MagicMock(return_value=[])  # type: ignore
    svc = StreamDiscoveryService(client, strategy, cfg, channel_map)
    records, errors = svc.discover(
        [ch],
        {},
        extra_video_ids=["goodvid12345"],
        only_member_keys=["arale"],
    )
    assert len(records) == 1
    assert records[0].video_id == "goodvid12345"
    assert records[0].status == "upcoming"
    assert records[0].member_key == "arale"
