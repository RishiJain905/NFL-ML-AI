"""ESPN's live feed (LD01): a small, polite client for ESPN's public site API.

ESPN's site API is public but unofficial: Disney's terms bar automated access, and it can
change without notice (D109; Rishi accepted both for personal, low-volume use). The rules
(documentation/live-decisions/README.md §5):

- **one host:** only `https://site.api.espn.com` is ever called; any other host, scheme or a
  redirect is refused before a byte is sent (no credentials exist to send);
- **polite:** a 5 s timeout, at most one request per game every 2 s (the game's scoreboard and
  its summary share one clock) and 0.5 s between any two requests, a fixed User-Agent naming
  the project, gzip;
- **back-off:** after an error no request goes out for 2 s, then 4, 8 ... up to 60 s; the first
  good answer resets it;
- **fail soft:** every answer is kept in memory with its fetch time. When ESPN fails (or the
  client is backing off), the last good answer comes back marked `stale` with the reason, so
  a screen can say "ESPN didn't answer (timeout), last good state 14 s ago". Only when there
  is no earlier answer does a call raise `EspnError` (its message is safe to show).

ESPN text (play descriptions) is data: callers show it as text, never as HTML.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

ALLOWED_HOST = "site.api.espn.com"
BASE = f"https://{ALLOWED_HOST}/apis/site/v2/sports/football/nfl"
USER_AGENT = "nfl-analytics-engine/live-decisions (personal project; low-volume, click-driven)"
TIMEOUT_S = 5.0
MIN_INTERVAL_S = 2.0
HOST_INTERVAL_S = 0.5  # any two requests, whatever the game (bulk replays stay polite)
BACKOFF_START_S = 2.0
BACKOFF_MAX_S = 60.0
EVENT_ID = re.compile(r"[0-9]{1,12}")  # ASCII only: `\d` would also take other scripts' digits

# ESPN numbers the playoff weeks 1 (wild card), 2, 3 and 5 (the Super Bowl; 4 is the Pro
# Bowl) under season type 3; nflverse numbers them 19-22 from 2021 (18-21 before).
POST_WEEKS = {0: 1, 1: 2, 2: 3, 3: 5}


class EspnError(RuntimeError):
    """ESPN gave no usable answer. The message is short and safe to show on screen."""


class HostNotAllowed(EspnError):
    """A URL outside the allowlist (README §5 rule 1)."""


def check_url(url: str) -> str:
    """Refuse anything but `https://site.api.espn.com/...` (no port, no user info)."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != ALLOWED_HOST:
        raise HostNotAllowed(f"refused: only https://{ALLOWED_HOST} may be called")
    return url


def check_event_id(event_id: str | int) -> str:
    """ESPN event ids are digits (e.g. 401872980); anything else is refused."""
    s = str(event_id).strip()
    if not EVENT_ID.fullmatch(s):
        raise ValueError(f"not an ESPN event id: {event_id!r} (digits only)")
    return s


def espn_week(season: int, week: int) -> tuple[int, int]:
    """nflverse (season, week) -> ESPN (season type, week): 2 = regular season, 3 = playoffs."""
    last_reg = 18 if season >= 2021 else 17
    if week <= last_reg:
        return 2, week
    return 3, POST_WEEKS.get(week - last_reg - 1, week - last_reg)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Fetched:
    """One ESPN answer. `fetched_at` is when it arrived (UTC); `cached` = served from memory
    because a fresh request would break the 2 s rule; `stale` = an older answer served because
    ESPN failed or the client is backing off (`error` says why)."""

    data: dict[str, Any]
    url: str
    fetched_at: datetime
    ms: float
    cached: bool = False
    stale: bool = False
    error: str | None = None

    def age_s(self, now: datetime | None = None) -> float:
        return ((now or utc_now()) - self.fetched_at).total_seconds()


class EspnClient:
    """ESPN's site API for one process (the CLI, or the app's server in LD02).

    `game(event_id)` is the call for a check (3.4 KB, cached by ESPN for 1 s); `summary`
    (every play, ~50 KB gzipped) is for replays, the opening kickoff and play times;
    `scoreboard(season, week)` lists a week's games. Thread-safe: one lock serialises the
    calls, so the 2 s rule holds across threads (calls take ~100 ms).

    The clock, sleep and HTTP transport can be replaced (tests use `httpx.MockTransport`;
    no test reaches the network).
    """

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT_S,
        min_interval: float = MIN_INTERVAL_S,
        host_interval: float = HOST_INTERVAL_S,
        backoff_start: float = BACKOFF_START_S,
        backoff_max: float = BACKOFF_MAX_S,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = utc_now,
    ):
        self.timeout = timeout
        self.min_interval = min_interval
        self.host_interval = host_interval
        self.backoff_start = backoff_start
        self.backoff_max = backoff_max
        self._mono = monotonic
        self._sleep = sleep
        self._now = now
        self._http = httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
        )
        self._lock = threading.Lock()
        self._last_request: dict[str, float] = {}  # clock key -> monotonic time of the request
        self._last_any = float("-inf")  # the last request of any kind
        self._memory: dict[str, tuple[Fetched, float]] = {}  # url -> (answer, request time)
        self._fails = 0
        self._retry_at = float("-inf")
        self._last_error: str | None = None
        self.requests = 0  # network requests sent (tests and the latency log read it)

    # -- the three calls ------------------------------------------------------------------------
    def scoreboard(self, season: int | None = None, week: int | None = None) -> Fetched:
        """A week's games (status, score, clock, situation). No arguments = ESPN's current week."""
        params: dict[str, Any] = {}
        if season is not None and week is not None:
            stype, ewk = espn_week(season, week)
            params = {"dates": season, "seasontype": stype, "week": ewk}
        key = f"scoreboard:{season}:{week}"
        return self._get(key, f"{BASE}/scoreboard", params)

    def game(self, event_id: str | int) -> Fetched:
        """One game's scoreboard entry (`scoreboard/{event}`): the call behind a check."""
        ev = check_event_id(event_id)
        return self._get(f"event:{ev}", f"{BASE}/scoreboard/{ev}", {})

    def summary(self, event_id: str | int) -> Fetched:
        """One game's summary: every play with its start / end state and `wallclock`."""
        ev = check_event_id(event_id)
        return self._get(f"event:{ev}", f"{BASE}/summary", {"event": ev})

    # -- plumbing -------------------------------------------------------------------------------
    def next_allowed_in(self, event_id: str | int) -> float:
        """Seconds until this game may be asked again (0 = now)."""
        ev = check_event_id(event_id)
        with self._lock:
            last = self._last_request.get(f"event:{ev}", float("-inf"))
            return max(0.0, self.min_interval - (self._mono() - last))

    def backing_off_for(self) -> float:
        """Seconds of back-off left after errors (0 = none)."""
        with self._lock:
            return max(0.0, self._retry_at - self._mono())

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> EspnClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _stale_or_raise(self, url: str, reason: str) -> Fetched:
        mem = self._memory.get(url)
        if mem is None:
            raise EspnError(f"ESPN didn't answer ({reason})")
        return replace(mem[0], cached=False, stale=True, error=reason)

    def _get(self, clock_key: str, base_url: str, params: dict[str, Any]) -> Fetched:
        url = check_url(base_url + ("?" + urlencode(params) if params else ""))
        with self._lock:
            now = self._mono()
            mem = self._memory.get(url)
            if mem is not None and now - mem[1] < self.min_interval:
                return replace(mem[0], cached=True, stale=False, error=None)
            if now < self._retry_at:
                wait = self._retry_at - now
                return self._stale_or_raise(
                    url, f"backing off after an error, next try in {wait:.0f} s: {self._last_error}"
                )
            wait = max(
                self.min_interval - (now - self._last_request.get(clock_key, float("-inf"))),
                self.host_interval - (now - self._last_any),
            )
            if wait > 0:
                self._sleep(wait)
            sent = self._mono()
            self._last_request[clock_key] = sent
            self._last_any = sent
            self.requests += 1
            t0 = time.perf_counter()
            error: str | None = None
            data: Any = None
            try:
                resp = self._http.get(url)
                if 300 <= resp.status_code < 400:
                    error = f"redirect to another address refused (HTTP {resp.status_code})"
                elif resp.status_code != 200:
                    error = f"HTTP {resp.status_code}"
                else:
                    data = resp.json()
                    if not isinstance(data, dict):
                        error = "not a JSON object"
            except httpx.TimeoutException:
                error = f"no answer within {self.timeout:.0f} s"
            except httpx.TransportError as exc:
                error = f"connection failed ({type(exc).__name__})"
            except ValueError:
                error = "not JSON"
            ms = round((time.perf_counter() - t0) * 1000, 1)
            if error is not None:
                self._fails += 1
                # the exponent is capped: 2.0 ** 1024 overflows after ~17 hours of failures
                exp = min(self._fails - 1, 30)
                delay = min(self.backoff_start * 2**exp, self.backoff_max)
                self._retry_at = self._mono() + delay
                self._last_error = error
                return self._stale_or_raise(url, error)
            self._fails = 0
            self._retry_at = float("-inf")
            self._last_error = None
            out = Fetched(data=data, url=url, fetched_at=self._now(), ms=ms)
            self._memory[url] = (out, sent)
            return out
