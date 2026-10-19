"""Connections ("six degrees") for the control room (AE03): `GET /api/ask/connections`.

The only part of the Graph lab that talks to Neo4j: the history graph (Bolt 7688), read-only,
through `history_graph.connections` (Cypher `SHORTEST` for the fewest links, GDS Dijkstra /
Yen's for the strongest). Everything else (the name search, the lab views, the benchmark) is a
file read (`readers/graphlab.py`).

Rules (the control room's §5): the two people are ids the search gave (`player:<GSIS id>` /
`coach:<id>`), validated by pattern and checked against the people index before any query;
at most `MAX_INFLIGHT` searches at a time (409 `busy` otherwise); every refusal is fixed text;
nothing a driver says reaches the browser; every string in the answer is cleaned.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from typing import Any

from nflengine.paths import DataPaths

MAX_INFLIGHT = 2
ERRORS = {
    "bad_id": "Pick both people from the search (a player or a coach).",
    "same_person": "Pick two different people.",
    "bad_mode": "The mode is fewest or strongest.",
    "not_found": "That person isn't in the history graph.",
    "no_links": "The person links haven't been made yet: run `uv run nfl history-graph derive`.",
    "history_down": (
        "The history graph isn't running: start it with `uv run nfl history-graph start` "
        "(about 2 minutes)."
    ),
    "history_busy": (
        "The history graph is being rebuilt or updated (or the last run didn't finish: "
        "`uv run nfl history-graph status`). Try again later."
    ),
    "busy": "Another connection search is running: try again in a moment.",
    "too_slow": "That search took too long and was stopped.",
    "failed": "The search failed. Check `uv run nfl history-graph status`.",
}


class ConnectionsRefused(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = ERRORS[code]


def _default_driver():
    from nflengine.history_graph.client import get_history_driver

    return get_history_driver(connection_timeout=5.0)


class ConnectionsService:
    """One driver for the app's life, made on first use; `check` is the Ask page's cached
    "is the history graph answering?" (`ask_service.HistoryCheck`)."""

    def __init__(
        self,
        check: Any = None,
        driver_factory: Callable[[], Any] | None = None,
        connect: Callable[..., dict[str, Any]] | None = None,
        max_inflight: int = MAX_INFLIGHT,
        warm: bool = False,
    ) -> None:
        if check is None:
            from nflengine.app.ask_service import HistoryCheck

            check = HistoryCheck()
        self._check = check
        self._factory = driver_factory or _default_driver
        self._connect = connect
        self._driver = None
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max_inflight)
        self._warm_on = warm  # `nfl app` only: tests never reach a real server from a thread
        self._warmed: str | None = None
        self._warming = False

    def _get_driver(self):
        with self._lock:
            if self._driver is None:
                self._driver = self._factory()
            return self._driver

    def _reset(self) -> None:
        with self._lock:
            d, self._driver = self._driver, None
        if d is not None:
            with contextlib.suppress(Exception):  # closing a broken driver
                d.close()

    def close(self) -> None:
        self._reset()

    def history_state(self) -> str:
        return self._check.state()

    def warm_if_up(self, paths: DataPaths) -> None:
        """Once per derive, warm the history graph in the background when it's up: one read of
        every person link (the HDD's cold page cache made the first search take 10 s) and the
        "strongest" projection (32 s cold, < 0.5 s warm; the UI worker, AE03). The page asks
        for the status when it opens, so the graph is warm before the first search."""
        if not self._warm_on or self._check.state() != "up":
            return
        from nflengine.app.readers.graphlab import links_asof
        from nflengine.history_graph.state import history_busy

        links = links_asof(paths)
        if links is None or history_busy(paths):
            return
        did = links["derive_id"]
        with self._lock:
            if self._warmed == did or self._warming:
                return
            self._warming = True
        threading.Thread(target=self._warm, args=(did,), name="cr-connections-warm",
                         daemon=True).start()  # fmt: skip

    def _warm(self, derive_id: str) -> None:
        from nflengine.history_graph import connections as C

        try:
            driver = self._get_driver()
            with driver.session() as s:
                s.run(
                    f"MATCH (a:Player|Coach)-[r:{C.LINKS}]->() RETURN sum(r.snaps) AS n"
                ).consume()
            C.ensure_projection(driver, derive_id)
            self._warmed = derive_id
        except Exception:  # noqa: BLE001 - a failed warm-up only means a slow first search
            self._reset()
        finally:
            with self._lock:
                self._warming = False

    def connect(self, paths: DataPaths, a: str, b: str, k: int = 1, mode: str = "fewest"):
        from nflengine.app.readers.graphlab import clean_tree, known_person, links_asof
        from nflengine.history_graph import connections as C
        from nflengine.history_graph.state import history_busy

        try:
            ra, rb = C.parse_person(a), C.parse_person(b)
        except C.ConnectionError_:
            raise ConnectionsRefused(422, "bad_id") from None
        if mode not in ("fewest", "strongest"):
            raise ConnectionsRefused(422, "bad_mode")
        if ra == rb:
            raise ConnectionsRefused(422, "same_person")
        links = links_asof(paths)
        if links is None:
            raise ConnectionsRefused(503, "no_links")
        if not (known_person(paths, ra) and known_person(paths, rb)):
            raise ConnectionsRefused(404, "not_found")
        if history_busy(paths):
            raise ConnectionsRefused(503, "history_busy")
        if self._check.state() == "down":
            raise ConnectionsRefused(503, "history_down")
        if not self._slots.acquire(blocking=False):
            raise ConnectionsRefused(409, "busy")
        try:
            run = self._connect or C.connect
            res = run(self._get_driver(), ra, rb, mode=mode, k=k, derive_id=links["derive_id"])
        except C.ConnectionError_ as e:
            if e.code == "no_derive":  # the links went away between the check and the search
                raise ConnectionsRefused(503, "no_links") from None
            code = e.code if e.code in ERRORS else "failed"
            raise ConnectionsRefused(404 if code == "not_found" else 422, code) from None
        except Exception as e:  # the driver's text never leaves
            name = type(e).__name__
            if name in ("ServiceUnavailable", "SessionExpired") or isinstance(e, OSError):
                self._reset()
                raise ConnectionsRefused(503, "history_down") from None
            if "timed out" in str(e).lower() or "Timeout" in name:
                raise ConnectionsRefused(504, "too_slow") from None
            raise ConnectionsRefused(500, "failed") from None
        finally:
            self._slots.release()
        from nflengine.app.readers.graphlab import person_facts

        facts = person_facts(paths)
        chained = [x for chain in res.get("paths") or [] for x in chain["people"]]
        for person in [res.get("a"), res.get("b"), *chained]:
            if person:
                person.update(facts.get((person.get("kind"), person.get("id")), {}))
        return clean_tree({**res, "asof": links}, paths)
