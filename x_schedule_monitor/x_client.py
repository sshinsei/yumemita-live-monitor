"""Official X API client for schedule post incremental fetch."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import requests

logger = logging.getLogger("x_schedule_monitor.x_client")


class XAPIError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        kind: str = "unknown",  # timeout | auth | quota | network | http | parse
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind
        self.retryable = retryable


@dataclass
class XPost:
    post_id: str
    text: str
    created_at: str
    expanded_urls: List[str] = field(default_factory=list)
    edit_history_tweet_ids: List[str] = field(default_factory=list)


class XClient:
    """X API v2 recent search with Bearer Token."""

    BASE_URL = "https://api.x.com/2"
    # Alternate host still used by some docs:
    ALT_BASE_URL = "https://api.twitter.com/2"

    def __init__(
        self,
        bearer_token: str,
        *,
        timeout: int = 10,
        max_retries: int = 3,
        session: Optional[requests.Session] = None,
        base_url: str = BASE_URL,
    ):
        if not bearer_token:
            raise ValueError("bearer_token is required")
        self.bearer_token = bearer_token
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            "User-Agent": "XScheduleDiscovery/0.1",
        }

    def _request(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_err: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(
                    url,
                    params=params,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                status = resp.status_code
                if status == 200:
                    try:
                        return resp.json()
                    except ValueError as e:
                        raise XAPIError(
                            f"X API invalid JSON: {e}",
                            status_code=status,
                            kind="parse",
                        ) from e

                body_snip = resp.text[:400]
                kind = "http"
                retryable = status in {429, 500, 502, 503, 504}
                if status in {401, 403}:
                    kind = "auth"
                    retryable = False
                if status == 429:
                    kind = "quota"

                msg = f"X API {path} failed: HTTP {status}: {body_snip}"
                logger.error(msg)
                if retryable and attempt < self.max_retries:
                    delay = (2**attempt) * (5 if status == 429 else 1)
                    logger.warning("Retrying X API in %ss", delay)
                    time.sleep(delay)
                    last_err = XAPIError(
                        msg, status_code=status, kind=kind, retryable=True
                    )
                    continue
                raise XAPIError(msg, status_code=status, kind=kind, retryable=retryable)

            except requests.Timeout as e:
                last_err = e
                logger.error("X API timeout: %s", e)
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                raise XAPIError(
                    f"X API timeout: {e}", kind="timeout", retryable=True
                ) from e
            except requests.RequestException as e:
                last_err = e
                logger.error("X API network error: %s", e)
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                raise XAPIError(
                    f"X API network error: {e}", kind="network", retryable=True
                ) from e

        raise XAPIError(f"X API failed after retries: {last_err}", kind="network")

    def recent_search(
        self,
        username: str,
        *,
        since_id: Optional[str] = None,
        max_results: int = 20,
    ) -> List[XPost]:
        """
        Fetch recent posts: from:{username} -is:retweet -is:reply
        """
        query = f"from:{username} -is:retweet -is:reply"
        params: Dict[str, Any] = {
            "query": query,
            "max_results": max(10, min(max_results, 100)),
            "tweet.fields": "created_at,entities,edit_history_tweet_ids",
        }
        if since_id:
            params["since_id"] = since_id

        data = self._request("tweets/search/recent", params)
        return self._parse_posts(data)

    def _parse_posts(self, data: Dict[str, Any]) -> List[XPost]:
        items = data.get("data") or []
        if not isinstance(items, list):
            return []
        posts: List[XPost] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            post_id = str(it.get("id") or "")
            if not post_id:
                continue
            text = str(it.get("text") or "")
            created = str(it.get("created_at") or "")
            expanded: List[str] = []
            entities = it.get("entities") or {}
            urls = entities.get("urls") or []
            if isinstance(urls, list):
                for u in urls:
                    if not isinstance(u, dict):
                        continue
                    exp = u.get("expanded_url") or u.get("unwound_url") or ""
                    if exp:
                        expanded.append(str(exp))
            edit_ids = it.get("edit_history_tweet_ids") or []
            if not isinstance(edit_ids, list):
                edit_ids = []
            posts.append(
                XPost(
                    post_id=post_id,
                    text=text,
                    created_at=created,
                    expanded_urls=expanded,
                    edit_history_tweet_ids=[str(x) for x in edit_ids],
                )
            )
        # API returns newest first typically; keep as-is
        return posts
