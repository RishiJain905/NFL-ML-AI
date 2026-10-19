"""A scripted Neo4j driver for the Connections and lab tests (AE03): it records every query with
its parameters and answers from a table of (what the query contains -> rows), so a test can check
the Cypher that was sent (labels on the projection, bound parameters, the timeout) and feed
`connections.py` / `lab.py` canned rows. Anything unscripted fails the test loudly.

A plain module (no conftest in a test subfolder). Nothing here opens a socket or touches Docker.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class Rec:
    """A neo4j `Record`: by key or by position, `.data()`."""

    def __init__(self, data: dict[str, Any]):
        self._d = dict(data)
        self._v = list(data.values())

    def data(self) -> dict[str, Any]:
        return dict(self._d)

    def __getitem__(self, k: str | int) -> Any:
        return self._v[k] if isinstance(k, int) else self._d[k]

    def get(self, k: str, default: Any = None) -> Any:
        return self._d.get(k, default)


class Res:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def __iter__(self):
        return iter(Rec(r) for r in self._rows)

    def single(self) -> Rec:
        assert len(self._rows) == 1, f"expected one row, got {len(self._rows)}"
        return Rec(self._rows[0])

    def data(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]

    def consume(self) -> None:
        return None


class Session:
    def __init__(self, driver: ScriptedDriver, kw: dict[str, Any]):
        self._driver = driver
        self.kw = kw

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def run(self, query: str, parameters: dict[str, Any] | None = None, **params: Any) -> Res:
        return self._driver.answer(query, {**(parameters or {}), **params}, self.kw)

    def execute_read(self, fn: Callable[[Session], Any]) -> Any:
        self._driver.timeouts.append(getattr(fn, "timeout", None))  # `unit_of_work(timeout=)`
        return fn(self)


class ScriptedDriver:
    """`on(contains, rows)`: the first rule whose text (or predicate) matches answers; `rows` is
    a list of dicts, an exception to raise, or a function `(params) -> rows`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.session_kw: list[dict[str, Any]] = []
        self.timeouts: list[float | None] = []
        self.rules: list[tuple[Any, Any]] = []
        self.closed = False
        self._lock = threading.Lock()

    def on(
        self, contains: str | Callable[[str, dict[str, Any]], bool], rows: Any
    ) -> ScriptedDriver:
        self.rules.append((contains, rows))
        return self

    def session(self, **kw: Any) -> Session:
        self.session_kw.append(kw)
        return Session(self, kw)

    def close(self) -> None:
        self.closed = True

    def verify_connectivity(self) -> None:
        return None

    def answer(self, query: str, params: dict[str, Any], kw: dict[str, Any]) -> Res:
        q = " ".join(query.split())
        with self._lock:
            self.calls.append((q, dict(params)))
        for cond, rows in self.rules:
            hit = cond(q, params) if callable(cond) else cond in q
            if not hit:
                continue
            if isinstance(rows, BaseException):
                raise rows
            if callable(rows):
                rows = rows(params)
            if isinstance(rows, BaseException):
                raise rows
            return Res(rows)
        raise AssertionError(f"the scripted driver has no answer for: {q}")

    # ---- inspection ----
    def queries(self, contains: str = "") -> list[str]:
        return [q for q, _ in self.calls if contains in q]

    def params_of(self, contains: str) -> list[dict[str, Any]]:
        return [p for q, p in self.calls if contains in q]
