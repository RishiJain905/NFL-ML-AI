"""Background service checks for the control room's status line.

A Neo4j ping takes up to ~5 s when the container is down (measured 4.5 s on 2026-10-05), too
slow to run on every page load. A daemon thread pings every 30 s and the API reports the last
answer ("checking" until the first one).
"""

from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Callable

from nflengine.ops.summary import scrub

UP, DOWN, CHECKING, PROBLEM = "up", "down", "checking", "config_problem"


def default_neo4j_ping() -> tuple[str, str]:
    """(state, reason). Never raises; the reason is fixed text, never a secret."""
    from nflengine.ops.services import Neo4jConfigProblem, neo4j_up

    try:
        return (
            (UP, "answering") if neo4j_up() else (DOWN, "not reachable; the weekly run starts it")
        )
    except Neo4jConfigProblem as e:
        return PROBLEM, scrub(str(e), 160)
    except Exception as e:  # anything unexpected: report the type only
        return PROBLEM, type(e).__name__


class ServiceStatus:
    def __init__(
        self, ping: Callable[[], tuple[str, str]] = default_neo4j_ping, every_s: float = 30.0
    ) -> None:
        self._ping = ping
        self._every = every_s
        self._lock = threading.Lock()
        self._state, self._reason, self._at = CHECKING, "first check running", None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check_now(self) -> None:
        state, reason = self._ping()
        with self._lock:
            self._state, self._reason = state, reason
            self._at = dt.datetime.now(dt.UTC).replace(microsecond=0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.check_now()
            self._stop.wait(self._every)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="cr-neo4j-ping", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict:
        with self._lock:
            return {"status": self._state, "reason": self._reason, "checked_at": self._at}
