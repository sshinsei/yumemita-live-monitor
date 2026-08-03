"""X client error classification tests (ticket 04)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from x_schedule_monitor.x_client import XAPIError, XClient


def test_auth_error_not_retryable():
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 401
    resp.text = "unauthorized"
    resp.json.return_value = {"title": "Unauthorized"}
    session.get.return_value = resp
    client = XClient("token", max_retries=2, session=session)
    with pytest.raises(XAPIError) as ei:
        client.recent_search("BDP_yumemita")
    assert ei.value.kind == "auth"
    assert ei.value.retryable is False
    assert session.get.call_count == 1


def test_timeout_classified():
    session = MagicMock()
    session.get.side_effect = requests.Timeout("slow")
    client = XClient("token", max_retries=0, session=session)
    with pytest.raises(XAPIError) as ei:
        client.recent_search("BDP_yumemita")
    assert ei.value.kind == "timeout"


def test_parse_posts_expanded_urls():
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": [
            {
                "id": "100",
                "text": "hello",
                "created_at": "2026-02-20T00:00:00.000Z",
                "entities": {
                    "urls": [
                        {
                            "url": "https://t.co/x",
                            "expanded_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                        }
                    ]
                },
                "edit_history_tweet_ids": ["100"],
            }
        ]
    }
    session.get.return_value = resp
    client = XClient("token", max_retries=0, session=session)
    posts = client.recent_search("BDP_yumemita")
    assert len(posts) == 1
    assert posts[0].expanded_urls[0].endswith("dQw4w9WgXcQ")
