"""Game day (LD02): the live 3rd / 4th-down bot in the control room.

Three answers, all GETs (a check changes nothing; documentation/live-decisions/README.md §4):
- **the week's games** (`games`): ESPN's scoreboard for the week Game day is live for (one
  call for every game, kept 30 s), each game with our pre-game win % (the week's
  `predictions_games.parquet`, primary row) and the pre-game market line (curated `lines`,
  the line the bot uses). A past week or a future one never asks ESPN (LD03 / the slate);
- **a check** (`call`): a fresh `scoreboard/{event}` (the client's 2 s rule holds for every
  browser tab: one shared client), the parsed next snap, then the 4th-down call (go / field
  goal / punt) or the 3rd-down check with its if-stopped table, always with the "as of";
- **the team context** (`context`): "is this team good at this?" from curated plays as of the
  start of the week (`live/context.py`, cached per week under `cache/control-room/live/`);
- **the decision review** (`review`, `season_review`; LD03): a finished week's 4th downs, the
  bot's call against the coach's, and the season's coach leaderboard and calibration, from
  curated plays and the models (`live/review.py`): `nfl live review`'s files under `live/<S>/`
  when their stamp matches, else built once and kept under `cache/control-room/live/review/`.

Rules (README §5, the control room's §5):
- ESPN is reached only through `live.espn.EspnClient` (`site.api.espn.com` only, 2 s per game,
  0.5 s between requests, back-off, stale answers instead of errors);
- nothing is fetched until a click, except the list (at most once per 30 s, and only when the
  browser asks while the tab is visible) and, after a check, one summary refresh per game every
  30 s at most (it gives the snap times behind "last play 12 s ago" and the opening kickoff);
- ESPN text is data: it goes through `clean()` (control characters out, scrubbed, capped) and
  the browser renders it as text;
- the only files written are the team-context and review caches under `cache/control-room/live/`.

"Live week" = the first week of the season whose last kickoff + 6 h is still ahead (the
calendar's week moves to N+1 at week N's last kickoff, which would end Monday night's game
day at kickoff); under `nfl app --live-replay` it's the replayed week, on the replay's clock.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nflengine.app.readers.common import clean, curated, num, read_parquet
from nflengine.live.espn import EspnClient, EspnError, Fetched, check_event_id
from nflengine.live.state import (
    Contexts,
    GameContext,
    LiveState,
    competition,
    event_teams,
    load_contexts,
    opening_receiver,
    parse_event,
)
from nflengine.paths import DataPaths

log = logging.getLogger("nflengine.app.live")

REFRESH_S = 30.0  # the list: one scoreboard call per 30 s at most
SUMMARY_TTL_S = (
    30.0  # a game's summary (snap times, the opening kickoff): refreshed at most this often
)
BEHIND_S = 55.0  # snaps come ~40 s apart (3rd -> 4th: median 42 s, p90 51 s; week 4 2026)
LIVE_UNTIL_H = 6.0  # a week stays Game day's week until 6 h after its last kickoff
CHUNK = 50  # the context build scores 4th downs in chunks, so a check never waits long
NAMES = {"go": "Go for it", "fg": "Field goal", "punt": "Punt"}
OPTIONS = ("go", "fg", "punt")
NO_MODELS = "The decision models aren't trained yet (LD00): run `uv run nfl live train --promote`."
ADMIN_WORDS = ("timeout", "official timeout", "two-minute warning", "end period")
ENGINE_RETRY_S = 60.0  # a bundle that failed to load is tried again after a minute, not per request
REFRESH_MIN_S = 10.0  # the Refresh button re-asks ESPN only when the list is older than this
UNEXPECTED = "ESPN sent something unexpected for this game. Try again shortly."
MAX_SUMMARIES = 16
_PLAY_ID = re.compile(r"[0-9]{1,24}")
_CODE = re.compile(r"[A-Z]{2,4}")
CURATED_FILES = ("games", "lines", "espn_scoreboard", "weather_forecasts")


class LiveRefused(Exception):
    """A request Game day won't answer: an HTTP status, a code and a message safe to show."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(UTC).isoformat() if t is not None else None


def _r(x: Any, digits: int = 4) -> float | None:
    return num(x, digits)


def spread_text(home: str, away: str, home_spread: float | None) -> str | None:
    """nflverse's line (+ = home favoured) in the broadcast's words: "PHI -3.5", "Pick'em"."""
    if home_spread is None:
        return None
    s = float(home_spread)
    if abs(s) < 0.25:
        return "Pick'em"
    team = home if s > 0 else away
    v = abs(s)
    return f"{team} -{v:g}"


def gain_text(gain: int) -> str:
    if gain == 0:
        return "no gain"
    return f"a {gain}-yard gain" if gain > 0 else f"a {-gain}-yard loss"


def down_text(down: int, distance: int, yardline_100: int, offense: str, defense: str) -> str:
    from nflengine.live.show import down_text as _down_text

    return _down_text(down, distance, yardline_100, offense, defense)


def _code(x: Any) -> str | None:
    """A team code from ESPN, or None: only 2-4 capital letters reach the browser (review S1)."""
    s = str(x) if x is not None else ""
    return s if _CODE.fullmatch(s) else None


def _play_id(x: Any) -> str | None:
    s = str(x) if x is not None else ""
    return s if _PLAY_ID.fullmatch(s) else None


def _valid_event(x: Any) -> bool:
    try:
        check_event_id(x)
    except ValueError:
        return False
    return True


def clean_all(obj: Any, paths: DataPaths | None) -> Any:
    """Every string of an answer through `clean()`: the last pass, so nothing from ESPN (a team
    code, an id, a date) reaches the browser unmasked (review S1)."""
    if isinstance(obj, str):
        return clean(obj, paths, 2000)
    if isinstance(obj, dict):
        return {k: clean_all(v, paths) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [clean_all(v, paths) for v in obj]
    return obj


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class _LockedEngine:
    """The shared engine for the team-context build: 4th downs scored in small chunks, each
    under the engine's lock, so a check never waits behind the whole build."""

    def __init__(self, engine: Any, lock: threading.Lock):
        self._e, self._lock = engine, lock

    def fourth_downs(self, states: list[Any], bootstrap: bool = False) -> list[Any]:
        out: list[Any] = []
        for i in range(0, len(states), CHUNK):
            with self._lock:
                out.extend(self._e.fourth_downs(states[i : i + CHUNK], bootstrap=bootstrap))
        return out

    def hold(self) -> threading.Lock:
        """The engine's lock, for work that calls the models directly (LD03's calibration):
        LightGBM's thread settings are process-wide, so it must never overlap a check."""
        return self._lock

    def __getattr__(self, name: str) -> Any:  # anything else: the engine itself, under the lock
        return getattr(self._e, name)


def _default_engine(paths: DataPaths) -> tuple[Any, str]:
    """The promoted bundle (`models/live-decisions/production.json`), loaded once."""
    from nflengine.live import models as LM
    from nflengine.live import train as LT
    from nflengine.live.decide import Engine, decide_settings

    folder = LT.production_folder(paths)
    return Engine(LM.load(folder), decide_settings()), folder.name


def _default_models_version(paths: DataPaths) -> str | None:
    from nflengine.live import train as LT

    try:
        return LT.production_folder(paths).name
    except (FileNotFoundError, KeyError, ValueError, OSError):
        return None


@dataclass
class _Board:
    """The week's ESPN scoreboard as last fetched."""

    fetched: Fetched
    mono: float
    events: dict[str, dict[str, Any]] = field(default_factory=dict)


class LiveService:
    """Game day's state for one app: the one ESPN client, the decision engine (lazy), curated
    game facts per season, the team-context cache, ESPN summaries, and what each game looked
    like at its last check. Every dependency can be replaced (tests: a fake transport, stub
    engines, a pinned clock)."""

    def __init__(
        self,
        *,
        client: EspnClient | None = None,
        client_factory: Callable[[], EspnClient] | None = None,
        now: Callable[[], datetime] | None = None,
        replay: Any = None,
        engine_loader: Callable[[DataPaths], tuple[Any, str]] = _default_engine,
        models_version: Callable[[DataPaths], str | None] = _default_models_version,
        contexts_loader: Callable[[int, DataPaths], Contexts] = load_contexts,
        context_cache: Any = None,
        review_store: Any = None,
        refresh_s: float = REFRESH_S,
        summary_ttl_s: float = SUMMARY_TTL_S,
        behind_s: float = BEHIND_S,
        monotonic: Callable[[], float] = time.monotonic,
        background: bool = True,
    ):
        self.replay = replay
        if client is None and client_factory is None and replay is not None:
            client_factory = lambda: EspnClient(transport=replay.transport(), now=replay.now)  # noqa: E731
        self._client = client
        self._client_factory = client_factory or EspnClient
        self._clock = replay.now if replay is not None else (now or (lambda: datetime.now(UTC)))
        self._engine_loader = engine_loader
        self._models_version = models_version
        self._contexts_loader = contexts_loader
        self._context_cache = context_cache
        self.refresh_s = refresh_s
        self.summary_ttl_s = summary_ttl_s
        self.behind_s = behind_s
        self._mono = monotonic
        self.background = background
        self._lock = threading.RLock()
        self._engine_lock = threading.Lock()
        self._engine: tuple[Any, str] | None = None
        self._engine_error: str | None = None
        self._engine_error_at = float("-inf")
        self._engine_thread: threading.Thread | None = None
        self._contexts: dict[int, tuple[tuple, Contexts]] = {}
        self._boards: dict[tuple[int, int], _Board] = {}
        self._summaries: dict[str, tuple[dict[str, Any], float]] = {}
        self._summary_jobs: set[str] = set()
        self._opening: dict[str, str] = {}
        self._seen: dict[str, dict[str, datetime]] = {}
        self._checks: dict[str, tuple[str | None, tuple, datetime]] = {}
        self._preds: dict[tuple[int, int], tuple[Any, dict[str, float]]] = {}
        self._review_store = review_store  # LD03 (lazy: `live.review.ReviewStore`)

    # -- shared pieces ---------------------------------------------------------------------------
    def now(self) -> datetime:
        return self._clock()

    def client(self) -> EspnClient:
        with self._lock:
            if self._client is None:
                self._client = self._client_factory()
            return self._client

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
            if self._engine is not None:
                with contextlib.suppress(Exception):
                    self._engine[0].close()

    def contexts(self, paths: DataPaths, season: int) -> Contexts:
        """Curated game facts for a season, re-read when a curated file changes."""
        stamp = tuple(_stat(paths.curated / f"{n}.parquet") for n in CURATED_FILES)
        with self._lock:
            hit = self._contexts.get(season)
            if hit is not None and hit[0] == stamp:
                return hit[1]
        ctxs = self._contexts_loader(season, paths)
        with self._lock:
            self._contexts[season] = (stamp, ctxs)
        return ctxs

    def _recent_error(self) -> str | None:
        """The last load failure while it's under a minute old (no reload on every request)."""
        with self._lock:
            if self._engine_error is None:
                return None
            if self._mono() - self._engine_error_at < ENGINE_RETRY_S:
                return self._engine_error
            return None

    def models(self, paths: DataPaths) -> dict[str, Any]:
        """Is there a promoted bundle? Cheap (reads the pointer); loading happens on first use."""
        with self._lock:
            if self._engine is not None:
                return {"available": True, "version": self._engine[1], "message": None}
        err = self._recent_error()
        if err is not None:
            return {"available": False, "version": None, "message": err}
        version = self._models_version(paths)
        if version is None:
            return {"available": False, "version": None, "message": NO_MODELS}
        return {"available": True, "version": version, "message": None}

    def warm_engine(self, paths: DataPaths) -> None:
        """Load the engine in the background (the first Game day request; README §4)."""
        if not self.background:
            return
        with self._lock:
            if self._engine is not None:
                return
            if self._engine_thread is not None and self._engine_thread.is_alive():
                return
            t = threading.Thread(target=self._try_engine, args=(paths,), daemon=True)
            self._engine_thread = t
        if self._recent_error() is not None:
            return
        t.start()

    def _try_engine(self, paths: DataPaths) -> None:
        with contextlib.suppress(LiveRefused):
            self.engine(paths)

    def engine(self, paths: DataPaths) -> tuple[Any, str]:
        with self._engine_lock:
            with self._lock:
                if self._engine is not None:
                    return self._engine
            err = self._recent_error()
            if err is not None:
                raise LiveRefused(503, "no_models", err)
            try:
                loaded = self._engine_loader(paths)
            except FileNotFoundError:
                with self._lock:
                    self._engine_error, self._engine_error_at = NO_MODELS, self._mono()
                raise LiveRefused(503, "no_models", NO_MODELS) from None
            except Exception as e:  # noqa: BLE001 - a broken bundle: a fixed message, logged by type
                log.warning("live: the decision models didn't load (%s)", type(e).__name__)
                msg = (
                    f"The decision models didn't load ({type(e).__name__}). "
                    "Check `uv run nfl live call --state ...` in a terminal."
                )
                with self._lock:
                    self._engine_error, self._engine_error_at = msg, self._mono()
                raise LiveRefused(503, "no_models", msg) from None
            with self._lock:
                self._engine = loaded
                self._engine_error = None
            return loaded

    # -- which week is live ----------------------------------------------------------------------
    def live_week(self, paths: DataPaths, season: int) -> tuple[int, int] | None:
        """The week Game day is live for in `season` (see the module docstring)."""
        if self.replay is not None:
            return (self.replay.season, self.replay.week())
        try:
            games = self.contexts(paths, season).games
        except FileNotFoundError:
            return None
        if games.is_empty():
            return None
        now = self.now()
        last: dict[int, datetime] = {}
        for r in games.select("week", "kickoff_utc").iter_rows(named=True):
            k = r["kickoff_utc"]
            if k is None:
                continue
            last[int(r["week"])] = max(last.get(int(r["week"]), k), k)
        for wk in sorted(last):
            if now <= last[wk] + timedelta(hours=LIVE_UNTIL_H):
                return (season, wk)
        return None

    # -- the week's games ------------------------------------------------------------------------
    def _board(self, paths: DataPaths, season: int, week: int, force: bool = False) -> _Board:
        """ESPN's scoreboard for the week, at most once per `refresh_s`."""
        with self._lock:
            b = self._boards.get((season, week))
            if b is not None and not force and self._mono() - b.mono < self.refresh_s:
                return b
        # EspnError when ESPN fails with no earlier answer
        f = self.client().scoreboard(season, week)
        events = {
            str(e.get("id")): e
            for e in f.data.get("events") or []
            if isinstance(e, dict) and _valid_event(e.get("id"))
        }
        b = _Board(f, self._mono(), events)
        with self._lock:
            self._boards[(season, week)] = b
        for ev, e in events.items():
            self._see(ev, e)
        return b

    def _see(self, event: str, ev_json: dict[str, Any]) -> None:
        """Remember when the app first saw each `lastPlay` of a game."""
        last = (competition(ev_json).get("situation") or {}).get("lastPlay") or {}
        pid = _play_id(last.get("id"))
        if pid is None:
            return
        with self._lock:
            seen = self._seen.setdefault(event, {})
            seen.setdefault(str(pid), self.now())

    def predictions(self, paths: DataPaths, season: int, week: int) -> dict[str, float]:
        """game_id -> our pre-game home win probability (the primary row), re-read on change."""
        path = paths.run_dir(season, week) / "predictions_games.parquet"
        stamp = _stat(path)
        with self._lock:
            hit = self._preds.get((season, week))
            if hit is not None and hit[0] == stamp:
                return hit[1]
        out: dict[str, float] = {}
        df = read_parquet(path, ["game_id", "is_primary", "home_win_prob"])
        if df is not None and {"game_id", "home_win_prob"} <= set(df.columns):
            if "is_primary" in df.columns:
                df = df.filter(df["is_primary"].fill_null(False))
            for r in df.iter_rows(named=True):
                p = num(r.get("home_win_prob"))
                if r.get("game_id") and p is not None:
                    out[str(r["game_id"])] = p
        with self._lock:
            self._preds[(season, week)] = (stamp, out)
        return out

    def games(
        self, paths: DataPaths, season: int, week: int, refresh: bool = False
    ) -> dict[str, Any]:
        try:
            return clean_all(self._games(paths, season, week, refresh), paths)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            ArithmeticError,
            StopIteration,
        ) as e:
            log.warning("live: an unexpected ESPN answer for the list (%s)", type(e).__name__)
            raise LiveRefused(502, "espn_unexpected", UNEXPECTED) from None

    def _games(self, paths: DataPaths, season: int, week: int, refresh: bool) -> dict[str, Any]:
        live = self.live_week(paths, season)
        is_current = live == (season, week)
        models = self.models(paths)
        base: dict[str, Any] = {
            "season": season,
            "week": week,
            "is_current": is_current,
            "live_week": {"season": live[0], "week": live[1]} if live else None,
            "models": models,
            "replay": self.replay.info() if self.replay is not None else None,
            "refresh_s": self.refresh_s,
            "feed": None,
            "feed_error": None,
        }
        preds = self.predictions(paths, season, week)
        try:
            ctxs = self.contexts(paths, season)
        except FileNotFoundError:
            ctxs = None
        if not is_current:
            past = live is None or (season, week) < live
            base["phase"] = "past" if past else "future"
            base["games"] = self._schedule_games(paths, ctxs, season, week, preds, finals=past)
            return self._kickoffs(base)
        self.warm_engine(paths)
        self._warm_context(paths, season, week)
        try:
            force = False
            if refresh:
                with self._lock:
                    b = self._boards.get((season, week))
                force = b is None or self._mono() - b.mono >= REFRESH_MIN_S
            board = self._board(paths, season, week, force=force)
        except EspnError as e:
            base["feed_error"] = clean(str(e), paths, 200)
            base["games"] = self._schedule_games(paths, ctxs, season, week, preds, finals=False)
            base["phase"] = self._phase_from_kickoffs(base["games"])
            return self._kickoffs(base)
        base["feed"] = self._feed(board.fetched)
        games = []
        for ev, e in board.events.items():
            ctx = ctxs.context(ev, e) if ctxs is not None else None
            games.append(self._game(paths, ev, e, ctx, preds, board.fetched.fetched_at))
        games.sort(key=lambda g: (g["kickoff"] or "", g["event"]))
        base["games"] = games
        states = {g["state"] for g in games}
        if "in" in states:
            base["phase"] = "live"
        elif games and states == {"post"}:
            base["phase"] = "final"
        elif "post" in states:
            base["phase"] = "between"
        else:
            base["phase"] = "before"
        return self._kickoffs(base)

    def _phase_from_kickoffs(self, games: list[dict[str, Any]]) -> str:
        """The phase without ESPN (it failed): from the kickoff times alone (review C5)."""
        from nflengine.app.readers.common import parse_time

        now = self.now()
        kicks = sorted(t for g in games if (t := parse_time(g.get("kickoff"))) is not None)
        if not kicks or now < kicks[0]:
            return "before"
        span = timedelta(hours=4.5)
        if now > kicks[-1] + span:
            return "final"
        if any(k <= now <= k + span for k in kicks):
            return "live"
        return "between"

    def _kickoffs(self, body: dict[str, Any]) -> dict[str, Any]:
        kicks = sorted(g["kickoff"] for g in body["games"] if g.get("kickoff"))
        body["first_kickoff"] = kicks[0] if kicks else None
        upcoming = sorted(
            g["kickoff"] for g in body["games"] if g.get("kickoff") and g["state"] == "pre"
        )
        body["next_kickoff"] = upcoming[0] if upcoming else None
        return body

    def _feed(self, f: Fetched) -> dict[str, Any]:
        return {
            "as_of": _iso(f.fetched_at),
            "age_s": round(max(0.0, f.age_s(self.now())), 1),
            "stale": f.stale,
            "error": clean(f.error, None, 200) if f.error else None,
            "cached": f.cached,
            "ms": f.ms,
        }

    def _pregame(self, ctx: GameContext | None, preds: dict[str, float]) -> dict[str, Any]:
        if ctx is None:
            return {
                "home_win_prob": None,
                "home_spread": None,
                "spread_text": None,
                "total": None,
                "line_source": None,
            }
        return {
            "home_win_prob": _r(preds.get(ctx.game_id or "")),
            "home_spread": _r(ctx.home_spread, 1),
            "spread_text": spread_text(ctx.home, ctx.away, ctx.home_spread),
            "total": _r(ctx.total, 1),
            "line_source": ctx.line_source,
        }

    def _schedule_games(
        self,
        paths: DataPaths,
        ctxs: Contexts | None,
        season: int,
        week: int,
        preds: dict[str, float],
        finals: bool,
    ) -> list[dict[str, Any]]:
        """A week's games from the curated schedule (no ESPN call): the slate, or the finals."""
        if ctxs is None:
            return []
        rows = ctxs.games.filter(ctxs.games["week"] == week).sort("kickoff_utc", "game_id")
        scores: dict[str, tuple[int | None, int | None]] = {}
        if finals:
            g = curated(paths, "games", ["game_id", "home_score", "away_score"])
            if g is not None:
                for r in g.iter_rows(named=True):
                    scores[r["game_id"]] = (r.get("home_score"), r.get("away_score"))
        events = {gid: ev for ev, gid in ctxs.event_map.items()}
        out = []
        for r in rows.iter_rows(named=True):
            gid = r["game_id"]
            ctx = ctxs.by_game(gid)
            hs, as_ = scores.get(gid, (None, None))
            played = hs is not None and as_ is not None
            out.append(
                {
                    "event": events.get(gid, ""),
                    "game_id": gid,
                    "home": r["home_team"],
                    "away": r["away_team"],
                    "state": "post" if played else "pre",
                    "detail": "Final" if played else None,
                    "kickoff": _iso(r.get("kickoff_utc")),
                    "period": 0,
                    "clock": None,
                    "home_score": int(hs) if played else 0,
                    "away_score": int(as_) if played else 0,
                    "possession": None,
                    "down": None,
                    "distance": None,
                    "yardline_100": None,
                    "situation": None,
                    "red_zone": False,
                    "home_timeouts": None,
                    "away_timeouts": None,
                    "last_play": None,
                    "espn_home_wp": None,
                    "pregame": self._pregame(ctx, preds),
                    "decision_down": False,
                }
            )
        return out

    def _last_play(
        self, paths: DataPaths, event: str, ls: LiveState | None, ev_json: dict[str, Any]
    ) -> dict[str, Any] | None:
        last = (competition(ev_json).get("situation") or {}).get("lastPlay") or {}
        if not last:
            return None
        pid = _play_id(last.get("id"))
        at = ls.last_play_at if ls is not None else None
        if at is None and pid is not None:
            summ = self._summaries.get(event)
            if summ is not None:
                from nflengine.live.state import play_times

                at = play_times(summ[0]).get(pid)
        with self._lock:
            seen = dict(self._seen.get(event) or {})
        seen_at = seen.get(pid) if pid else None
        now = self.now()
        age_s, age_from = None, None
        if at is not None:
            age_s, age_from = max(0.0, (now - at).total_seconds()), "snap"
        elif seen_at is not None and len(seen) > 1:
            # the app watched this play appear (an earlier play was seen first): a lower bound
            age_s, age_from = max(0.0, (now - seen_at).total_seconds()), "seen"
        return {
            "id": pid,
            "type": clean(
                ls.last_play_type if ls else (last.get("type") or {}).get("text"), paths, 60
            ),
            "text": clean(ls.last_play_text if ls else last.get("text"), paths, 300),
            "at": _iso(at),
            "seen_at": _iso(seen_at),
            "age_s": round(age_s, 1) if age_s is not None else None,
            "age_from": age_from,
        }

    def _game(
        self,
        paths: DataPaths,
        event: str,
        ev_json: dict[str, Any],
        ctx: GameContext | None,
        preds: dict[str, float],
        fetched_at: datetime | None,
        ls: LiveState | None = None,
    ) -> dict[str, Any]:
        if ls is None and ctx is not None:
            summ = self._summaries.get(event)
            ls = parse_event(
                ev_json,
                ctx,
                fetched_at=fetched_at,
                opening=self._opening.get(event),
                summary=summ[0] if summ is not None else None,
            )
        comp = competition(ev_json)
        teams = event_teams(ev_json)
        if ls is not None:
            home, away = ls.home, ls.away
            hs, as_ = ls.home_score, ls.away_score
            state, detail, period = ls.status, ls.detail, ls.period
            clock = ls.display_clock
        else:  # a game the curated schedule doesn't know: ESPN's own words
            by_side = {t["home_away"]: t for t in teams.values()}
            home = _code((by_side.get("home") or {}).get("team")) or "?"
            away = _code((by_side.get("away") or {}).get("team")) or "?"
            hs = (by_side.get("home") or {}).get("score", 0)
            as_ = (by_side.get("away") or {}).get("score", 0)
            st = (comp.get("status") or {}).get("type") or {}
            state = str(st.get("state") or "pre")
            detail = clean(st.get("detail") or st.get("shortDetail"), paths, 80)
            period = int((comp.get("status") or {}).get("period") or 0)
            clock = clean((comp.get("status") or {}).get("displayClock"), paths, 10)
        offense = _code(ls.offense) if ls is not None else None
        defense = _code(ls.defense) if ls is not None else None
        down = ls.down if ls is not None else None
        dist = ls.distance if ls is not None else None
        yl = ls.yardline_100 if ls is not None else None
        situation = None
        if offense and defense and down in (1, 2, 3, 4) and yl is not None:
            situation = down_text(down, dist or 1, yl, offense, defense)
        kickoff = ctx.kickoff_utc if ctx is not None else None
        return {
            "event": event,
            "game_id": ctx.game_id if ctx is not None else None,
            "home": home,
            "away": away,
            "state": state if state in ("pre", "in", "post") else "pre",
            "detail": clean(detail, paths, 80),
            "kickoff": _iso(kickoff) or clean(ev_json.get("date"), paths, 40),
            "period": int(period or 0),
            "clock": clean(clock, paths, 10),
            "home_score": int(hs or 0),
            "away_score": int(as_ or 0),
            "possession": offense,
            "down": down if state == "in" else None,
            "distance": dist if state == "in" else None,
            "yardline_100": yl if state == "in" else None,
            "situation": situation if state == "in" else None,
            "red_zone": bool(state == "in" and yl is not None and yl <= 20),
            "home_timeouts": ls.home_timeouts if ls is not None else None,
            "away_timeouts": ls.away_timeouts if ls is not None else None,
            "last_play": self._last_play(paths, event, ls, ev_json) if state == "in" else None,
            "espn_home_wp": _r(ls.espn_home_wp) if ls is not None and state == "in" else None,
            "pregame": self._pregame(ctx, preds),
            "decision_down": bool(state == "in" and down in (3, 4) and ls is not None and ls.state),
        }

    # -- a check ---------------------------------------------------------------------------------
    def _require_live(self, paths: DataPaths, season: int, week: int) -> None:
        if self.live_week(paths, season) != (season, week):
            raise LiveRefused(
                409, "not_live", "Checks are for the week being played (Game day's week)."
            )

    def _in_week(self, paths: DataPaths, season: int, week: int, event: str) -> _Board:
        try:
            ev = check_event_id(event)
        except ValueError:
            raise LiveRefused(422, "bad_request", "An ESPN event id is digits only.") from None
        try:
            # the newest board of any age: re-asked only when it doesn't know the game (review C2)
            with self._lock:
                board = self._boards.get((season, week))
            if board is None or ev not in board.events:
                board = self._board(paths, season, week, force=board is not None)
        except EspnError as e:
            raise LiveRefused(503, "espn_unavailable", clean(str(e), paths, 200) or "") from None
        if ev not in board.events:
            raise LiveRefused(404, "not_in_week", "That game isn't in this week's ESPN scoreboard.")
        return board

    def _summary_age(self, event: str) -> float | None:
        with self._lock:
            s = self._summaries.get(event)
        return None if s is None else self._mono() - s[1]

    def _fetch_summary(self, event: str) -> dict[str, Any] | None:
        try:
            f = self.client().summary(event)
        except EspnError:
            return None
        if f.stale:
            return None
        with self._lock:
            self._summaries[event] = (f.data, self._mono())
            while len(self._summaries) > MAX_SUMMARIES:  # the oldest first (dicts keep order)
                self._summaries.pop(next(iter(self._summaries)))
            rec = opening_receiver(f.data)
            if rec is not None:
                self._opening.setdefault(event, rec)
        return f.data

    def _refresh_summary_later(self, event: str) -> None:
        """After a check: one summary refresh for this game in the background (it waits its
        turn under the 2 s rule outside the client's lock, so no other request waits on it)."""
        with self._lock:
            if event in self._summary_jobs:
                return
            self._summary_jobs.add(event)
        client = self.client()

        def job() -> None:
            try:
                wait = client.next_allowed_in(event)
                if wait > 0:
                    time.sleep(wait + 0.05)
                self._fetch_summary(event)
            except Exception as e:  # noqa: BLE001 - a background refresh never fails loudly
                log.info("live: summary refresh skipped (%s)", type(e).__name__)
            finally:
                with self._lock:
                    self._summary_jobs.discard(event)

        if self.background:
            threading.Thread(target=job, daemon=True).start()
        else:
            with self._lock:
                self._summary_jobs.discard(event)

    def call(self, paths: DataPaths, season: int, week: int, event: str) -> dict[str, Any]:
        try:
            return clean_all(self._call(paths, season, week, event), paths)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            ArithmeticError,
            StopIteration,
        ) as e:
            log.warning("live: an unexpected ESPN answer for a check (%s)", type(e).__name__)
            raise LiveRefused(502, "espn_unexpected", UNEXPECTED) from None

    def _call(self, paths: DataPaths, season: int, week: int, event: str) -> dict[str, Any]:
        self._require_live(paths, season, week)
        self._in_week(paths, season, week, event)
        ev = check_event_id(event)
        client = self.client()
        try:
            f = client.game(ev)
        except EspnError as e:
            raise LiveRefused(503, "espn_unavailable", clean(str(e), paths, 200) or "") from None
        self._see(ev, f.data)
        ctx = self.contexts(paths, season).context(ev, f.data)
        if ctx is None:
            raise LiveRefused(404, "not_in_schedule", "That game isn't in the curated schedule.")
        comp = competition(f.data)
        status = ((comp.get("status") or {}).get("type") or {}).get("state")
        down = (comp.get("situation") or {}).get("down")
        period = int((comp.get("status") or {}).get("period") or 0)
        with self._lock:
            cached = self._summaries.get(ev)
        summary = cached[0] if cached is not None else None
        if status == "in":
            need = (period <= 2 and ev not in self._opening) or down not in (1, 2, 3, 4)
            age = self._summary_age(ev)
            if need and (age is None or age > 3.0):
                summary = self._fetch_summary(ev) or summary
        ls = parse_event(
            f.data, ctx, fetched_at=f.fetched_at, opening=self._opening.get(ev), summary=summary
        )
        preds = self.predictions(paths, season, week)
        game = self._game(paths, ev, f.data, ctx, preds, f.fetched_at, ls=ls)
        models = self.models(paths)
        body: dict[str, Any] = {
            "season": season,
            "week": week,
            "event": ev,
            "game_id": ctx.game_id,
            "game": game,
            "feed": self._feed(f),
            "kind": "none",
            "reason": None,
            "warnings": [clean(w, paths, 200) for w in ls.warnings],
            "offense": _code(ls.offense),
            "defense": _code(ls.defense),
            "source": ls.source,
            "fourth": None,
            "third": None,
            "espn_offense_wp": None,
            "behind": None,
            "repeat": self._repeat(ev, game),
            "models": models,
        }
        if ls.espn_home_wp is not None and ls.offense:
            p = ls.espn_home_wp if ls.offense == ls.home else 1 - ls.espn_home_wp
            body["espn_offense_wp"] = _r(p)
        if not ls.is_decision:
            body["reason"] = clean(self._why_not(ls), paths, 200)
        else:
            engine, version = self.engine(paths)
            st = ls.state
            with self._engine_lock:
                if st.down == 4:
                    d = engine.fourth_down(st, bootstrap=True)
                else:
                    t3 = engine.third_down(st, bootstrap=True)
            body["models"] = {"available": True, "version": version, "message": None}
            if st.down == 4:
                body["kind"], body["fourth"] = "fourth", self._fourth(d)
            else:
                body["kind"] = "third"
                body["third"] = self._third(t3, ls.offense or "", ls.defense or "")
            body["behind"] = self._behind(game, st.down)
        if status == "in":
            age = self._summary_age(ev)
            if age is None or age > self.summary_ttl_s:
                self._refresh_summary_later(ev)
        elif status == "post":
            with self._lock:
                self._summaries.pop(ev, None)
        return body

    @staticmethod
    def _why_not(ls: LiveState) -> str:
        if ls.reason:
            return ls.reason[0].upper() + ls.reason[1:]
        if ls.down in (1, 2) and ls.offense:
            nth = {1: "1st", 2: "2nd"}[ls.down]
            return f"{nth} & {ls.distance}: checks are for 3rd and 4th downs."
        return "No 3rd or 4th down to check right now."

    def _repeat(self, event: str, game: dict[str, Any]) -> dict[str, Any]:
        """Is this the same play as the last check? ("No new play since your last check")."""
        last = game.get("last_play") or {}
        # no clock in the key: ESPN can tick the clock between plays (review C6)
        key = (game.get("situation"), game.get("home_score"), game.get("away_score"))
        now = self.now()
        with self._lock:
            prev = self._checks.get(event)
            self._checks[event] = (last.get("id"), key, now)
        if prev is None:
            return {"same": False, "last_check_at": None, "text": None}
        same = prev[0] == last.get("id") and prev[1] == key
        ago = max(0, round((now - prev[2]).total_seconds()))
        return {
            "same": same,
            "last_check_at": _iso(prev[2]),
            "text": f"No new play since your last check ({ago} s ago)." if same else None,
        }

    def _behind(self, game: dict[str, Any], down: int) -> dict[str, Any] | None:
        """ESPN may not have posted the snap the TV shows: its last play is older than a typical
        gap between snaps. A hint, never a claim (the clock can stop for a timeout)."""
        lp = game.get("last_play") or {}
        age = lp.get("age_s")
        if age is None or age < self.behind_s:
            return None
        sit = game.get("situation") or "this down"
        kind = (lp.get("type") or "").lower()
        admin = kind in ADMIN_WORDS
        word = "first seen" if lp.get("age_from") != "snap" else ("logged" if admin else "snapped")
        what = f"its last entry (a {kind})" if admin else "its last play"
        if down == 3:
            text = (
                f"ESPN still shows {sit}, and {what} was {word} {age:.0f} s ago. If your TV is "
                "past this snap, the 4th down hasn't posted yet: the if-stopped table below has it."
            )
        else:
            text = (
                f"ESPN still shows {sit}, and {what} was {word} {age:.0f} s ago. If your TV is "
                "past this snap, ESPN hasn't posted the result yet."
            )
        return {"likely": True, "text": text}

    @staticmethod
    def _fourth(d: Any) -> dict[str, Any]:
        return {
            "best": d.best,
            "best_name": NAMES[d.best],
            "gap": _r(d.gap),
            "label": d.label,
            "boot_share": _r(d.boot_share, 3),
            "options": [
                {"choice": o, "name": NAMES[o], "wp": _r(d.wp.get(o)), "best": o == d.best}
                for o in OPTIONS
            ],
            "convert": _r(d.convert),
            "fg_make": _r(d.fg_make),
            "fg_distance": _r(d.fg_distance, 1),
            "punt_start": _r(d.punt_start, 1),
            "wp_now": _r(d.wp_now),
            "ms": _r(d.ms, 2),
        }

    @staticmethod
    def _third(t: Any, offense: str, defense: str) -> dict[str, Any]:
        s = t.state
        most = max(10, int(s.ydstogo))
        rows = []
        for r in sorted(t.table, key=lambda r: r.ydstogo):
            if r.ydstogo > most:
                continue
            rows.append(
                {
                    "gain": int(r.gain),
                    "gain_text": gain_text(int(r.gain)),
                    "ydstogo": int(r.ydstogo),
                    "yardline_100": int(r.yardline_100),
                    "situation": down_text(4, r.ydstogo, r.yardline_100, offense, defense),
                    "best": r.best,
                    "best_name": NAMES[r.best],
                    "gap": _r(r.gap),
                    "label": r.label,
                    "wp": {o: _r(r.wp.get(o)) for o in OPTIONS},
                }
            )
        return {
            "convert": _r(t.convert),
            "pass_prob": _r(t.pass_prob),
            "wp_now": _r(t.wp_now),
            "note": t.note,
            "table": rows,
            "ms": _r(t.ms, 2),
        }

    # -- the team context ------------------------------------------------------------------------
    def _cache(self, paths: DataPaths) -> Any:
        with self._lock:
            if self._context_cache is None:
                from nflengine.live.context import ContextCache

                self._context_cache = ContextCache(
                    paths.cache / "control-room" / "live" / "context"
                )
            return self._context_cache

    def _context_version(self, paths: DataPaths) -> str | None:
        """The bundle the team context is scored with: the loaded engine's, else the pointer's,
        and None while loading fails (the build then has no engine either: review C4)."""
        with self._lock:
            if self._engine is not None:
                return self._engine[1]
        if self._recent_error() is not None:
            return None
        return self._models_version(paths)

    def _engine_for_context(self, paths: DataPaths) -> tuple[Any, str | None]:
        try:
            engine, version = self.engine(paths)
        except LiveRefused:
            return None, None
        return _LockedEngine(engine, self._engine_lock), version

    def _warm_context(self, paths: DataPaths, season: int, week: int) -> None:
        """Start the week's team-context build early (the cache runs it on its own thread)."""
        try:
            self._cache(paths).warm(
                season,
                week,
                paths=paths,
                engine_factory=lambda: self._engine_for_context(paths),
                model_version=self._context_version(paths),
            )
        except Exception as e:  # noqa: BLE001 - warming is best effort
            log.warning("live: team context warm-up failed (%s)", type(e).__name__)

    def context(
        self, paths: DataPaths, season: int, week: int, event: str, offense: str
    ) -> dict[str, Any]:
        from nflengine.live.context import team_context

        self._require_live(paths, season, week)
        board = self._in_week(paths, season, week, event)
        ev = check_event_id(event)
        ctx = self.contexts(paths, season).context(ev, board.events[ev])
        if ctx is None:
            raise LiveRefused(404, "not_in_schedule", "That game isn't in the curated schedule.")
        if offense not in (ctx.home, ctx.away):
            raise LiveRefused(422, "bad_request", "The team must be one of this game's two teams.")
        # the bundle's version is part of the cache's stamp: a newly promoted bundle rebuilds it
        data = self._cache(paths).get(
            season,
            week,
            paths=paths,
            engine_factory=lambda: self._engine_for_context(paths),
            model_version=self._context_version(paths),
        )
        out = team_context(data, offense)
        if out is None:
            raise LiveRefused(404, "not_found", "No context for that team.")
        return clean_all(out, paths)

    # -- LD03: the decision review ---------------------------------------------------------------
    def _reviews(self, paths: DataPaths) -> Any:
        with self._lock:
            if self._review_store is None:
                from nflengine.live.review import ReviewStore

                self._review_store = ReviewStore(
                    cache_folder=paths.cache / "control-room" / "live" / "review",
                    background_writes=self.background,
                )
            return self._review_store

    def _review_engine(self, paths: DataPaths) -> tuple[Any, str]:
        """The shared engine for a review build: 4th downs in chunks under the engine's lock,
        so a Game day check never waits behind a whole week (LiveRefused when it can't load)."""
        engine, version = self.engine(paths)
        return _LockedEngine(engine, self._engine_lock), version

    def _review_base(self, season: int) -> dict[str, Any]:
        return {"season": season, "status": "ok", "message": None, "model": None,
                "source": None, "computed_at": None}  # fmt: skip

    def _review_version(self, paths: DataPaths) -> str | None:
        """The bundle a stored review must match; None (with the status set) when there's none."""
        return self._context_version(paths)

    def review(self, paths: DataPaths, season: int, week: int) -> dict[str, Any]:
        """A finished week's decision review (README §3, LD03): read-only, never ESPN."""
        from nflengine.live import review as RV

        base = {**self._review_base(season), "week": week, "summary": None,
                "highlights": None, "games": [], "plays": []}  # fmt: skip
        store = self._reviews(paths)
        weeks = store.weeks(paths, season)
        if week not in weeks:
            base["status"] = "no_plays"
            base["message"] = no_plays_text(season, week, weeks)
            return clean_all(base, paths)
        version = self._review_version(paths)
        if version is None:
            return clean_all(self._no_models(base, paths), paths)
        try:
            rv = store.week(paths, season, week, lambda: self._review_engine(paths), version)
        except LiveRefused as e:
            if e.code != "no_models":
                raise
            return clean_all({**base, "status": "no_models", "message": e.message}, paths)
        base.update(RV.week_payload(rv, RV.week_games(paths, season, week)))
        return clean_all(base, paths)

    def season_review(self, paths: DataPaths, season: int, through: int | None) -> dict[str, Any]:
        """The season through `through` (clamped to the newest week with plays): the coach
        leaderboard, the bot's calibration and the weekly trend."""
        from nflengine.live import review as RV

        base = {**self._review_base(season), "through_week": None, "weeks": [], "decisions": 0,
                "leaderboard": [], "league": None, "trend": [], "calibration": None}  # fmt: skip
        store = self._reviews(paths)
        weeks = store.weeks(paths, season)
        upto = [w for w in weeks if through is None or w <= through]
        if not upto:
            base["status"] = "no_plays"
            base["message"] = (
                f"No {season} plays in the curated data yet."
                if not weeks
                else f"No {season} week up to week {through} is in the curated data yet."
            )
            return clean_all(base, paths)
        version = self._review_version(paths)
        if version is None:
            return clean_all(self._no_models(base, paths), paths)
        try:
            tables, src = store.season(
                paths, season, max(upto), lambda: self._review_engine(paths), version
            )
        except LiveRefused as e:
            if e.code != "no_models":
                raise
            return clean_all({**base, "status": "no_models", "message": e.message}, paths)
        base.update(RV.season_payload(tables, src))
        return clean_all(base, paths)

    def _no_models(self, base: dict[str, Any], paths: DataPaths) -> dict[str, Any]:
        m = self.models(paths)
        return {**base, "status": "no_models", "message": m.get("message") or NO_MODELS}


def no_plays_text(season: int, week: int, weeks: list[int]) -> str:
    """Why a week has no review yet: its plays aren't curated (the Tuesday run brings them)."""
    if not weeks:
        return f"No {season} plays in the curated data yet."
    if week > max(weeks):
        return (
            f"Week {week}'s plays aren't in the curated data yet: they come in with the Tuesday "
            "run after the week (ingest + curate). The review builds then."
        )
    return f"Week {week} has no plays in the curated data."
