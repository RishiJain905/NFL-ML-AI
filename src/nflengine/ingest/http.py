"""Polite HTTP client for unofficial / free sources (ESPN, NGS site, Open-Meteo, odds).

- per-host minimum interval between requests (rate limiting)
- retries with exponential backoff on network errors / 429 / 5xx
- optional on-disk response cache (keyed by URL + params) with a TTL
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) nfl-analytics-engine (personal project)"

# Seconds between requests per host. PFR is strict; others are generous.
HOST_MIN_INTERVAL = {
    "www.pro-football-reference.com": 3.5,
    "nextgenstats.nfl.com": 1.0,
    "site.api.espn.com": 0.5,
    "site.web.api.espn.com": 0.5,
    "sports.core.api.espn.com": 0.5,
    "api.open-meteo.com": 0.3,
    "api.the-odds-api.com": 1.0,
}


class PoliteClient:
    def __init__(
        self,
        cache_dir: Path | None = None,
        cache_ttl_s: float = 3600,
        timeout: float = 30,
        max_retries: int = 3,
    ):
        self.cache_dir = cache_dir
        self.cache_ttl_s = cache_ttl_s
        self.max_retries = max_retries
        self._last_call: dict[str, float] = {}
        self.last_headers: dict[str, str] = {}
        self._client = httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": DEFAULT_UA}
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self, host: str) -> None:
        wait = HOST_MIN_INTERVAL.get(host, 0.5) - (time.monotonic() - self._last_call.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self._last_call[host] = time.monotonic()

    def _cache_path(self, url: str, params: dict[str, Any] | None) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha256((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
        return self.cache_dir / key[:2] / f"{key}.json"

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
        retry_429: bool = True,
    ) -> Any:
        """GET JSON. Response headers of the last network call are kept in `last_headers`.

        `retry_429=False` for quota-limited APIs, where a 429 means the quota is used up.
        """
        cpath = self._cache_path(url, params) if use_cache else None
        if cpath and cpath.exists() and time.time() - cpath.stat().st_mtime < self.cache_ttl_s:
            return json.loads(cpath.read_text(encoding="utf-8"))

        host = urlsplit(url).netloc
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle(host)
            try:
                resp = self._client.get(url, params=params, headers=headers)
                self.last_headers = dict(resp.headers)
                if (resp.status_code == 429 and retry_429) or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                data = resp.json()
                if cpath:
                    cpath.parent.mkdir(parents=True, exist_ok=True)
                    cpath.write_text(json.dumps(data), encoding="utf-8")
                return data
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                code = exc.response.status_code
                if code < 500 and (code != 429 or not retry_429):
                    raise  # 4xx (or a quota 429): retrying will not help
            except (httpx.TransportError, ValueError) as exc:
                last_exc = exc
            time.sleep(min(2**attempt, 10))
        assert last_exc is not None
        raise last_exc
