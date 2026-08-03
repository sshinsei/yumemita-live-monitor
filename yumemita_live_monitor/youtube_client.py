"""YouTube Data API v3 client with retries and timeouts."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence

import requests

logger = logging.getLogger("yumemita_live_monitor.youtube")


class YouTubeAPIError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        reason: str = "",
        retryable: bool = False,
        quota_exceeded: bool = False,
        auth_error: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.retryable = retryable
        self.quota_exceeded = quota_exceeded
        self.auth_error = auth_error


class YouTubeClient:
    BASE_URL = "https://www.googleapis.com/youtube/v3"

    def __init__(
        self,
        api_key: str,
        *,
        timeout: int = 10,
        max_retries: int = 3,
        session: Optional[requests.Session] = None,
    ):
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def _request(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        params = {**params, "key": self.api_key}
        url = f"{self.BASE_URL}/{endpoint}"
        last_err: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                status = resp.status_code

                if status == 200:
                    return resp.json()

                err_msg = resp.text[:500]
                reason = ""
                try:
                    body = resp.json()
                    err = body.get("error") or {}
                    err_msg = err.get("message") or err_msg
                    errors = err.get("errors") or []
                    if errors:
                        reason = str(errors[0].get("reason") or "")
                except Exception:
                    pass

                quota = status == 403 and reason in {
                    "quotaExceeded",
                    "dailyLimitExceeded",
                    "rateLimitExceeded",
                }
                auth = status in {401, 403} and reason in {
                    "keyInvalid",
                    "authError",
                    "forbidden",
                    "accessNotConfigured",
                }
                retryable = status in {429, 500, 502, 503, 504} or quota

                readable = (
                    f"YouTube API {endpoint} failed: HTTP {status}"
                    f"{f' ({reason})' if reason else ''}: {err_msg}"
                )
                logger.error(readable)

                if retryable and attempt < self.max_retries:
                    delay = (2**attempt) * (30 if quota else 1)
                    logger.warning(
                        "Retrying %s in %ss (attempt %s/%s)",
                        endpoint,
                        delay,
                        attempt + 1,
                        self.max_retries,
                    )
                    time.sleep(delay)
                    last_err = YouTubeAPIError(
                        readable,
                        status_code=status,
                        reason=reason,
                        retryable=True,
                        quota_exceeded=quota,
                        auth_error=auth,
                    )
                    continue

                raise YouTubeAPIError(
                    readable,
                    status_code=status,
                    reason=reason,
                    retryable=retryable,
                    quota_exceeded=quota,
                    auth_error=auth,
                )

            except requests.Timeout as e:
                last_err = e
                logger.error("YouTube API %s timeout: %s", endpoint, e)
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                raise YouTubeAPIError(
                    f"YouTube API {endpoint} timeout: {e}",
                    retryable=True,
                ) from e
            except requests.RequestException as e:
                last_err = e
                logger.error("YouTube API %s network error: %s", endpoint, e)
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                raise YouTubeAPIError(
                    f"YouTube API {endpoint} network error: {e}",
                    retryable=True,
                ) from e

        raise YouTubeAPIError(f"YouTube API {endpoint} failed after retries: {last_err}")

    def videos_list(
        self,
        video_ids: Sequence[str],
        *,
        part: str = "snippet,liveStreamingDetails",
        batch_size: int = 50,
    ) -> List[Dict[str, Any]]:
        ids = [v for v in video_ids if v]
        if not ids:
            return []

        items: List[Dict[str, Any]] = []
        for i in range(0, len(ids), batch_size):
            batch = ids[i : i + batch_size]
            data = self._request(
                "videos",
                {
                    "part": part,
                    "id": ",".join(batch),
                    "maxResults": batch_size,
                },
            )
            items.extend(data.get("items") or [])
        return items

    def playlist_items(
        self,
        playlist_id: str,
        *,
        max_results: int = 15,
    ) -> List[Dict[str, Any]]:
        data = self._request(
            "playlistItems",
            {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": min(max_results, 50),
            },
        )
        return data.get("items") or []

    def search_live_or_upcoming(
        self,
        channel_id: str,
        event_type: str,
        *,
        max_results: int = 5,
    ) -> List[Dict[str, Any]]:
        if event_type not in {"live", "upcoming"}:
            raise ValueError("event_type must be 'live' or 'upcoming'")
        data = self._request(
            "search",
            {
                "part": "snippet",
                "channelId": channel_id,
                "type": "video",
                "eventType": event_type,
                "maxResults": min(max_results, 25),
            },
        )
        return data.get("items") or []
