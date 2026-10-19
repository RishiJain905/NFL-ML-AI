"""Explore -> Ask the Engine: the backend of the control room's first free-text endpoint (AE01).

Spec: documentation/ask-the-engine/README.md §3-§5 and AE01-ask-tab.md; decision D142.

`POST /api/ask` runs AE00's flow (`ask.route.AskEngine.ask`: names -> router -> template or
generated query -> the guard -> the answer and its number check) and returns what the page
shows. On top of the control room's rules (launch token + same-origin `Origin` on every POST,
`security.py`), this service adds the free-text ones:
- **one question at a time** per app (409 `busy` while one runs: the engine holds one Neo4j
  driver and one LLM client, and a question can take a minute at the model's "max" effort);
- **about 10 a minute** (429 `rate_limited`, counted over the last 60 s);
- the question is capped at 500 characters by the route; the body at 16 KB;
- **fixed text for every error**: the engine's errors are already fixed (`route.FIXED`, the
  guard's `RULES`, `llm.AskLLMError`'s codes); anything unexpected is its type name only;
- **every string that leaves goes through `clean()`** (the data root made relative, then
  `scrub()`): the answer, the column names, every text cell (names and play descriptions are
  data, shown as text), the query and its values;
- **rows are scalar cells** (numbers, text, dates, lists of them): the guard already refuses
  nodes and relationships; a map, which only the library's own templates build, is sent as
  one line of text.

Nothing here writes a file: the engine reads through the guard (a READ-mode Neo4j session and
a file-less in-memory DuckDB), and the session's questions are kept in the browser only.

The engine is built once (about 4 s: the name index), on the first question or in the
background when the page asks for its examples, and `refresh()`ed before each question: the
app runs for days and the weekly run moves the graph's week and the curated tables.

AE02 (the history graph): a third source when `ask.sources.history` is on. Its container is
started only when wanted, so its Bolt check (`HistoryCheck`) runs on a daemon thread at most
every 30 s and a request only reads the last answer (a stopped container takes the 5 s connect
timeout to say so); "down" keeps it out of "auto" and stops a history question at once.
"""

from __future__ import annotations

import collections
import contextlib
import datetime as dt
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from nflengine.app.readers.common import clean
from nflengine.paths import DataPaths

log = logging.getLogger("nflengine.app")

RATE_PER_MIN = 10
WINDOW_S = 60.0
MAX_BODY = 16_384  # bytes: a 500-character question and five picks fit with room to spare
BODY_SECONDS = 10.0  # the whole body must arrive within this (an unfinished upload is cut off)
TEXT_CELL = 1_000  # characters of one text cell (play descriptions are ~250)
QUERY_CHARS = 12_000  # the guard refuses a query over 8,000 characters without comments;
# the library's longest is ~10k raw. Bounded: cleaning costs grow with the cap (safetext)

# The chips under the question box: one tested template's first example each, a mix of the
# weekly graph and the curated tables (`GET /api/ask/examples`; the full list is grouped below).
FEATURED = (
    "stat_games_vs_team",
    "revenge_games_this_week",
    "play_action_by_down",
    "coaching_tree",
    "qb_receivers",
    "season_leaders",
    "game_predictions_this_week",
    "head_to_head",
    "history_qb_receivers_situation",  # AE02: shown only when the history graph can answer
)

ERRORS = {
    "busy": "A question is already running: wait for its answer, then ask again.",
    "rate_limited": "That's the limit of {n} a minute: wait {s} s, then ask again.",
    "too_big": "The request is too large: a question is up to 500 characters.",
    "bad_request": "Ask a question of 1-500 characters (source: auto, graph, sql or history; up "
    "to 5 picks from a which-one card).",
    "unavailable": "Ask the Engine couldn't start ({reason}): check `uv run nfl doctor`.",
    "out_of_credits": "OpenRouter is out of credits: top up the account, then ask again.",
    "body_timeout": "The request took too long to arrive: ask again.",
    "failed": "Something went wrong answering that ({reason}): see the app's console.",
}
SOURCE_NAMES = {"graph": "weekly graph", "sql": "curated data", "history": "history graph"}
LANGUAGES = {"graph": "cypher", "sql": "sql", "history": "cypher"}
HISTORY_EVERY_S = 30.0  # the history graph's Bolt check, like the weekly graph's (status.py)


def _default_history_ping() -> bool:
    from nflengine.history_graph.client import history_up

    return history_up(timeout=5.0)


class HistoryCheck:
    """Is the history graph answering on Bolt? (AE02) The last answer, refreshed at most every
    `every_s` on a daemon thread, so no request waits on a stopped container's 5 s connect
    timeout; "checking" until the first answer. `background=False` (tests) checks in line."""

    def __init__(
        self,
        ping: Callable[[], bool] | None = None,
        every_s: float = HISTORY_EVERY_S,
        background: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ping = ping or _default_history_ping
        self._every = every_s
        self._background = background
        self._clock = clock
        self._lock = threading.Lock()
        self._state = "checking"
        self._at: float | None = None
        self._running = False

    def _check(self) -> None:
        try:
            ok = bool(self._ping())
        except Exception:  # noqa: BLE001 - never raises: an error means it isn't answering
            ok = False
        with self._lock:
            self._state, self._at, self._running = ("up" if ok else "down"), self._clock(), False

    def state(self) -> str:
        """ "up" | "down" | "checking" (the last answer; a stale one starts a new check)."""
        with self._lock:
            stale = self._at is None or self._clock() - self._at >= self._every
            start = stale and not self._running
            if start:
                self._running = True
        if start:
            if self._background:
                threading.Thread(target=self._check, name="cr-ask-history", daemon=True).start()
            else:
                self._check()
        with self._lock:
            return self._state


class _WeeklyLockHeld(Exception):
    """The weekly lock was taken while the engine was about to be built."""


class AskRefused(Exception):
    """A request the service won't run: an HTTP status, a code and fixed text."""

    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


def _default_factory(paths: DataPaths) -> Any:
    from nflengine.ask.llm import make_llm
    from nflengine.ask.route import AskEngine

    return AskEngine.build(make_llm(), paths=paths)


def _model_label(name: str | None) -> str | None:
    if not name:
        return None
    return name.split("/", 1)[-1]


class AskService:
    """Per-app state for Ask the Engine: the engine, the one-at-a-time lock, the rate window.

    Tests inject `engine_factory` (an engine on a scripted LLM, fake sources), `clock` (the
    rate window) and `now` (the answer's time), and pass `background=False` so nothing is
    built on a thread."""

    def __init__(
        self,
        *,
        engine_factory: Callable[[DataPaths], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], dt.datetime] | None = None,
        rate_per_min: int = RATE_PER_MIN,
        window_s: float = WINDOW_S,
        background: bool = True,
        config: Any = None,
        history_check: HistoryCheck | None = None,
    ) -> None:
        self._factory = engine_factory  # None: `_default_factory`, looked up when used
        self._clock = clock
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._rate = rate_per_min
        self._window = window_s
        self._background = background
        self._config = config
        self._engine: Any = None
        self._build_lock = threading.Lock()
        self._busy = threading.Lock()
        self._times: collections.deque[float] = collections.deque()
        self._rate_lock = threading.Lock()
        self._warming = False
        self._last_sources: dict[str, Any] | None = None  # status() while the weekly lock is held
        self._history = history_check or HistoryCheck(background=background)

    # ---- the engine ---------------------------------------------------------------------------

    def _cfg(self) -> Any:
        if self._config is None:
            from nflengine.ask.config import ask_config

            self._config = ask_config()
        return self._config

    def engine(self, paths: DataPaths) -> Any:
        """The engine, built on first use (a failed build isn't kept: the next question
        tries again). The weekly lock is checked again under the build lock, right before the
        build reads the curated files (a warm-up scheduled before a run starts; Sol)."""
        with self._build_lock:
            if self._engine is None:
                if self._weekly_lock_held(paths):
                    raise _WeeklyLockHeld
                self._engine = (self._factory or _default_factory)(paths)
            return self._engine

    @staticmethod
    def _weekly_lock_held(paths: DataPaths) -> bool:
        """The weekly lock (a weekly run, the injury update, Update play calling): the curated
        tables are being replaced and the graph may be mid-rebuild. The note first, then a
        probe (`readers.meta.lock_dict`): a bare probe could stop a starting run (exit 4)."""
        from nflengine.app.readers.meta import lock_dict

        try:
            return bool(lock_dict(paths)["held"])
        except OSError:
            return False

    def warm(self, paths: DataPaths) -> None:
        """Build the engine on a daemon thread (the page opened), so the first question
        doesn't wait ~4 s for the name index. Never raises; a failure is retried by `ask`.
        Not while the weekly lock is held (the files it reads are being replaced; Sol)."""
        if not self._background or self._engine is not None or self._warming:
            return
        if self._weekly_lock_held(paths):
            return
        self._warming = True

        def build() -> None:
            try:
                self.engine(paths)
            except _WeeklyLockHeld:  # a run started meanwhile: the first question builds it
                pass
            except Exception as e:  # noqa: BLE001 - reported by the first question instead
                log.warning("ask: warm-up failed (%s)", type(e).__name__)
            finally:
                self._warming = False

        threading.Thread(target=build, name="cr-ask-warm", daemon=True).start()

    def close(self) -> None:
        with self._build_lock:
            if self._engine is not None:
                with contextlib.suppress(Exception):
                    self._engine.close()
                self._engine = None

    # ---- the examples and the sources' status -------------------------------------------------

    def _history_on(self) -> bool:
        return bool(self._cfg().sources.get("history", False))

    def history_status(self, paths: DataPaths) -> dict[str, Any]:
        """The history graph's part of `status()` (AE02): on / off, built or not, answering or
        not (the cached check), being rebuilt (its own lock), and what it covers."""
        from nflengine.history_graph.state import history_asof, history_busy

        on = self._history_on()
        try:
            asof = history_asof(paths) if on else None
        except (OSError, ValueError, KeyError, TypeError):  # build.json mid-write
            asof = None
        if not on:
            state = "off"
        elif asof is None:
            state = "none"
        elif history_busy(paths):
            state = "busy"
        else:
            state = self._history.state()
        a = asof or {}
        return {
            "on": on,
            "status": state,  # up | down | checking | busy | none (no build) | off
            "first_season": a.get("first_season"),
            "through_season": a.get("through_season"),
            "season": a.get("season"),  # the appended current season, if any
            "week": a.get("week"),
            "label": a.get("label"),
        }

    def status(self, paths: DataPaths, graph: dict[str, Any] | None) -> dict[str, Any]:
        """Which sources a question can use now, how fresh each is, the model, the limits."""
        from nflengine.ask.route import curated_through, graph_asof
        from nflengine.settings import get_config

        cfg = self._cfg()
        season = get_config().seasons.current
        busy = self._weekly_lock_held(paths)
        neo = (graph or {}).get("status") or "checking"
        graph_on = bool(cfg.sources.get("graph", True))
        sql_on = bool(cfg.sources.get("sql", True))
        if busy and self._last_sources is not None:
            # the files the labels come from are being replaced (Sol): show the last ones read
            sources = self._last_sources
        else:
            asof = graph_asof(paths, season) if not busy else None
            run_at, week = curated_through(paths, season) if not busy else (None, None)
            sources = self._sources(season, asof, run_at, week, graph_on, sql_on)
            if not busy:
                self._last_sources = sources
        sources = {
            **sources,
            "graph": {
                **sources["graph"],
                "status": "off"
                if not graph_on
                else ("none" if sources["graph"]["week"] is None else neo),
            },
            "history": self.history_status(paths),  # AE02: its own files, not the weekly run's
        }
        return {
            "sources": sources,
            "model": {
                "name": cfg.llm.model,
                "label": _model_label(cfg.llm.model),
                "effort": cfg.llm.reasoning_effort,
                "provider": cfg.llm.provider,
                "timeout_s": cfg.llm.timeout_s,
            },
            "limits": {
                "question_chars": cfg.limits.question_chars,
                "per_minute": self._rate,
                "rows": cfg.limits.rows,
                "query_timeout_s": cfg.limits.timeout_s,
                "corrections": cfg.limits.corrections,
            },
            "busy": busy,
        }

    @staticmethod
    def _sources(season, asof, run_at, week, graph_on: bool, sql_on: bool) -> dict[str, Any]:
        return {
            "graph": {
                "on": graph_on,
                "status": "none",  # set by status() from the app's Neo4j check
                "season": asof[0] if asof else None,
                "week": asof[1] if asof else None,
                "label": f"weekly graph as of {asof[0]} week {asof[1]}" if asof else None,
            },
            "sql": {
                "on": sql_on,
                "status": "off" if not sql_on else "up",
                "season": season if week else None,
                "week": week,
                "rebuilt": str(run_at)[:10] if run_at else None,
                "label": f"curated data through {season} week {week}" if week else None,
            },
        }

    def examples(self, paths: DataPaths, graph: dict[str, Any] | None) -> dict[str, Any]:
        """`GET /api/ask/examples`: the chips, every template's examples grouped by source,
        and `status()`. Also starts building the engine in the background."""
        from nflengine.ask.templates import load_templates
        from nflengine.settings import get_config

        templates = load_templates(current_season=get_config().seasons.current)
        body = self.status(paths, graph)
        on = {s for s, v in body["sources"].items() if v["on"]}
        if body["sources"]["history"]["status"] in ("none", "off"):
            on.discard("history")  # switched on but never built: nothing to ask it yet

        def item(t: Any) -> dict[str, Any]:
            return {
                "question": clean(t.examples[0], paths, 300),
                "template": t.id,
                "title": clean(t.title, paths, 120),
                "source": t.source,
                "this_week": bool(t.asof),
            }

        featured = [item(templates[t]) for t in FEATURED if t in templates]
        featured = [f for f in featured if f["source"] in on]
        groups = []
        for source, label in (
            ("graph", "The weekly graph"),
            ("sql", "The curated tables"),
            ("history", "The history graph"),
        ):
            if source not in on:
                continue
            ts = [t for t in templates.values() if t.source == source and t.examples]
            groups.append({"source": source, "label": label, "examples": [item(t) for t in ts]})
        self.warm(paths)
        return {**body, "featured": featured, "groups": groups}

    # ---- one question -------------------------------------------------------------------------

    def _check_rate(self) -> None:
        with self._rate_lock:
            t = self._clock()
            while self._times and t - self._times[0] >= self._window:
                self._times.popleft()
            if len(self._times) >= self._rate:
                wait = max(1, int(self._window - (t - self._times[0])) + 1)
                raise AskRefused(
                    429,
                    "rate_limited",
                    ERRORS["rate_limited"].format(
                        n=f"{self._rate} question" + ("" if self._rate == 1 else "s"), s=wait
                    ),
                    retry_after=wait,
                )
            self._times.append(t)

    def ask(
        self,
        paths: DataPaths,
        question: str,
        source: str = "auto",
        picks: list[dict[str, str]] | None = None,
        graph: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Answer one question (or raise `AskRefused`). `graph` is the app's Neo4j check
        (`ServiceStatus.snapshot()`): "down" or "config_problem" stops graph questions at once
        with a fixed sentence; SQL questions still run."""
        if not self._busy.acquire(blocking=False):
            raise AskRefused(409, "busy", ERRORS["busy"])
        try:
            self._check_rate()
            if self._weekly_lock_held(paths):  # before building or refreshing anything (Sol)
                return self._busy_body(paths, question)
            try:
                engine = self.engine(paths)
            except _WeeklyLockHeld:
                return self._busy_body(paths, question)
            except Exception as e:  # noqa: BLE001 - the reason is the type name, never a message
                log.warning("ask: the engine didn't build (%s)", type(e).__name__)
                raise AskRefused(
                    503, "ask_unavailable", ERRORS["unavailable"].format(reason=type(e).__name__)
                ) from None
            if self._weekly_lock_held(paths):  # a run started during a cold build (Sol)
                return self._busy_body(paths, question)
            return self._ask(engine, paths, question, source, picks or [], graph)
        finally:
            self._busy.release()

    def _ask(
        self,
        engine: Any,
        paths: DataPaths,
        question: str,
        source: str,
        picks: list[dict[str, str]],
        graph: dict[str, Any] | None,
    ) -> dict[str, Any]:
        from nflengine.ask.llm import OutOfCredits

        state = (graph or {}).get("status")
        engine.sources.graph_up = lambda: state not in ("down", "config_problem")
        if self._history_on():  # AE02: the cached check ("checking" counts as up)
            history = self._history.state()
            engine.sources.history_up = lambda: history != "down"
        asked = self._now()
        t0 = time.monotonic()
        if not engine.sources.busy():  # while the weekly run replaces the files, leave them be
            try:
                engine.refresh(paths)
            except Exception as e:  # noqa: BLE001 - a file mid-replace: keep the last context
                log.warning("ask: refresh failed (%s); using the last context", type(e).__name__)
        engine.last_calls = []
        try:
            res = engine.ask(question, source, picks)
        except OutOfCredits:
            calls = list(getattr(engine, "last_calls", None) or [])
            log.warning("ask: OpenRouter is out of credits")
            return self._error_body(
                engine,
                paths,
                question,
                asked,
                t0,
                "out_of_credits",
                ERRORS["out_of_credits"],
                calls,
            )
        except Exception as e:  # noqa: BLE001 - fixed text: the type name only
            calls = list(getattr(engine, "last_calls", None) or [])
            log.error("ask: the question failed (%s)", type(e).__name__)
            return self._error_body(
                engine,
                paths,
                question,
                asked,
                t0,
                "failed",
                ERRORS["failed"].format(reason=type(e).__name__),
                calls,
            )
        body = public(res, engine, paths, asked)
        log.info(
            "ask: %s via %s%s in %.1f s ($%s)",
            res.status,
            res.route,
            f" {res.template_id}" if res.template_id else "",
            res.timings.get("total", 0.0),
            "?" if res.cost is None else f"{res.cost:.5f}",
        )
        return body

    def _busy_body(self, paths: DataPaths, question: str) -> dict[str, Any]:
        """The weekly lock is held: the engine's own refusal (`route.FIXED["busy"]`), made
        without building or refreshing the engine."""
        from nflengine.ask.route import FIXED, AskResult

        res = AskResult(question=" ".join((question or "").split()), status="refused")
        res.route = "refused"
        res.refusal = {"by": "code", "rule": "source.busy", "message": FIXED["busy"]}
        res.answer = FIXED["busy"]
        res.timings["total"] = 0.0
        return public(res, self._engine, paths, self._now(), cfg=self._cfg())

    def _error_body(
        self,
        engine: Any,
        paths: DataPaths,
        question: str,
        asked: dt.datetime,
        t0: float,
        code: str,
        message: str,
        calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from nflengine.ask.route import AskResult

        res = AskResult(question=" ".join((question or "").split()), status="error")
        res.error, res.error_code, res.calls = message, code, calls
        res.timings["total"] = round(time.monotonic() - t0, 3)
        return public(res, engine, paths, asked)


# ---- the answer the page gets -------------------------------------------------------------------


def _cell(v: Any, paths: DataPaths) -> Any:
    """A table cell for the browser: text cleaned, lists cell by cell, a map as one line."""
    if isinstance(v, str):
        return clean(v, paths, TEXT_CELL)
    if isinstance(v, list | tuple):
        return [_cell(x, paths) for x in v]
    if isinstance(v, dict):
        text = "; ".join(f"{k}: {x}" for k, x in v.items())
        return clean(text, paths, TEXT_CELL)
    if v is None or isinstance(v, bool | int | float):
        return v
    return clean(str(v), paths, TEXT_CELL)


def _route_label(res: Any) -> str | None:
    if res.route == "template":
        return f"template: {res.template_id}"
    if res.route == "generated":
        n = res.retries
        return "free-form" + (f", {n} {'retry' if n == 1 else 'retries'}" if n else "")
    if res.route == "refused":
        return "refused"
    if res.route == "clarify":
        return "needs a choice"
    return None


def _source_info(res: Any, engine: Any) -> dict[str, Any] | None:
    if res.source == "history":  # AE02
        h = getattr(engine, "history", None) or {}
        return {
            "kind": "history",
            "season": h.get("season"),  # the appended current season (None without one)
            "week": h.get("week"),
            "through_season": h.get("through_season"),
            "label": h.get("label") or "history graph",
        }
    if res.source == "graph":
        asof = engine.asof
        return {
            "kind": "graph",
            "season": asof[0] if asof else None,
            "week": asof[1] if asof else None,
            "label": f"weekly graph as of {asof[0]} week {asof[1]}" if asof else "weekly graph",
        }
    if res.source == "sql":
        run_at, week = engine.curated
        return {
            "kind": "sql",
            "season": engine.current if week else None,
            "week": week,
            "rebuilt": str(run_at)[:10] if run_at else None,
            "label": f"curated data through {engine.current} week {week}"
            if week
            else "curated data",
        }
    return None


def _answer_kind(res: Any) -> str:
    """model: the model's sentences passed the number check; fallback: they didn't (or the
    model failed), so a fixed sentence points to the table; fixed: no model answer was due
    (a refusal, a choice, no rows, an error)."""
    if res.status != "answered":
        return "fixed"
    return "model" if res.answer_from_llm else "fallback"


def public(
    res: Any, engine: Any, paths: DataPaths, asked: dt.datetime, cfg: Any = None
) -> dict[str, Any]:
    """The JSON the page gets for one question (`AskResponse` in web/src/api/types.ts).
    `engine` may be None (a refusal made before the engine is built)."""
    cfg = getattr(engine, "cfg", None) or cfg
    model = getattr(getattr(engine, "llm", None), "model", None) or (
        cfg.llm.model if cfg is not None else None
    )
    calls = [
        {
            "purpose": c.get("purpose"),
            "provider": clean(c.get("provider"), paths, 60),
            "seconds": c.get("latency_s"),
            "cost": c.get("cost"),
            "ok": bool(c.get("ok")),
        }
        for c in res.calls
    ]
    costs = [c["cost"] for c in calls]
    cost_known = all(c is not None for c in costs)
    providers = list(dict.fromkeys(c["provider"] for c in calls if c["provider"]))
    t = res.timings
    error = None
    if res.status == "error":
        last = calls[-1] if calls else None
        error = {
            "code": res.error_code or "failed",
            "message": clean(res.error or "Something went wrong.", paths, 400),
            "stage": last["purpose"] if last and not last["ok"] else None,
            "provider": (last or {}).get("provider")
            or (cfg.llm.provider if cfg is not None else None),
            "model": _model_label(model),
        }
    refusal = None
    if res.refusal:
        refusal = {
            "by": res.refusal.get("by"),
            "rule": res.refusal.get("rule"),
            "message": clean(res.refusal.get("message"), paths, 400),
        }
    template = None
    if res.template_id:
        tmpl = engine.templates.get(res.template_id) if hasattr(engine, "templates") else None
        template = {
            "id": res.template_id,
            "title": clean(getattr(tmpl, "title", None), paths, 120),
        }
    return {
        "question": clean(res.question, paths, 600),
        "asked_at": asked,
        "status": res.status,
        "answer": clean(res.answer, paths, 2_000),
        "answer_kind": _answer_kind(res),
        "columns": [clean(c, paths, 120) for c in res.columns],
        "rows": [[_cell(v, paths) for v in row] for row in res.rows],
        "row_count": len(res.rows),
        "truncated": bool(res.truncated),
        "query": clean(res.query, paths, QUERY_CHARS) if res.query else None,
        "language": LANGUAGES.get(res.source or ""),
        "params": [
            {"name": clean(k, paths, 64), "value": _cell(v, paths)}
            for k, v in (res.params or {}).items()
        ],
        "source": _source_info(res, engine),
        "route": res.route,
        "route_label": _route_label(res),
        "template": template,
        "retries": res.retries,
        "refusal": refusal,
        "choices": [
            {
                "text": clean(c.get("text"), paths, 120),
                "kind": c.get("kind"),
                "options": [
                    {
                        "value": clean(o.get("value"), paths, 80),
                        "label": clean(o.get("label"), paths, 160),
                    }
                    for o in c.get("options", [])
                ],
            }
            for c in res.choices
        ],
        "error": error,
        "timing": {
            "total_s": t.get("total"),
            "route_s": t.get("route"),
            "generate_s": t.get("generate"),
            "run_s": t.get("run"),
            "answer_s": t.get("answer"),
        },
        "cost": round(sum(costs), 8) if costs and cost_known else (0.0 if not costs else None),
        "llm": {
            "model": _model_label(model),
            "calls": calls,
            "providers": providers,
        },
    }


# ---- the golden set through the API (AE01: "same accuracy through the API") --------------------


class ApiAsker:
    """Questions through the control room's endpoint, in-process: `create_app` behind an ASGI
    client, so each question passes the safety middleware (Host, Origin, launch token), the
    body cap, the request model, `AskService` and `public()`'s cleaning, exactly as the page's
    do. `nfl ask eval --via api` uses it with the eval's own engine (so the references and the
    answers read the same data); the rate limit is lifted for the run (it measures answers, not
    the limit), and nothing listens on a port."""

    BASE = "http://127.0.0.1:8765"

    def __init__(self, engine: Any, paths: DataPaths) -> None:
        import asyncio
        import secrets

        import httpx

        from nflengine.app.server import AppSettings, create_app
        from nflengine.app.status import ServiceStatus

        self.token = secrets.token_urlsafe(24)
        status = ServiceStatus()
        status.check_now()
        self.service = AskService(
            engine_factory=lambda _p: engine, background=False, rate_per_min=10**9
        )
        app = create_app(
            AppSettings(
                token=self.token, data_root=lambda: paths, services=status, ask=self.service
            )
        )
        self._loop = asyncio.new_event_loop()
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=self.BASE, timeout=None
        )

    def ask(self, question: str) -> Any:
        """An AskResult-like object from the endpoint's JSON (what `evaluate.score` reads).
        Raises `OutOfCredits` when the answer says so, so the eval stops as it does through
        the engine."""
        from types import SimpleNamespace

        from nflengine.ask.llm import OutOfCredits

        t0 = time.monotonic()
        r = self._loop.run_until_complete(
            self._client.post(
                "/api/ask",
                json={"question": question},
                headers={"origin": self.BASE, "x-cr-token": self.token},
            )
        )
        wall = round(time.monotonic() - t0, 3)
        body = r.json()
        if r.status_code != 200:
            err = (body or {}).get("error") or {}
            return SimpleNamespace(
                status="error", route="none", template_id=None, source=None, columns=[],
                rows=[], row_count=0, retries=0, timings={"total": wall}, calls=[], cost=None,
                choices=[], answer_check=None, refusal=None, query=None, answer=None,
                error=f"HTTP {r.status_code} {err.get('code') or ''}".strip(),
            )  # fmt: skip
        if (body.get("error") or {}).get("code") == "out_of_credits":
            raise OutOfCredits("HTTP 402: out of OpenRouter credits")
        answered = body.get("status") == "answered"
        return SimpleNamespace(
            status=body.get("status"),
            route=body.get("route"),
            template_id=(body.get("template") or {}).get("id"),
            source=(body.get("source") or {}).get("kind"),
            columns=body.get("columns") or [],
            rows=body.get("rows") or [],
            row_count=body.get("row_count") or 0,
            retries=body.get("retries") or 0,
            timings={"total": wall},
            calls=(body.get("llm") or {}).get("calls") or [],
            cost=body.get("cost"),
            choices=body.get("choices") or [],
            answer_check={"passed": body.get("answer_kind") == "model"} if answered else None,
            refusal=body.get("refusal"),
            query=body.get("query"),
            answer=body.get("answer"),
            error=(body.get("error") or {}).get("message"),
        )

    def close(self) -> None:
        self._loop.run_until_complete(self._client.aclose())
        self._loop.close()
