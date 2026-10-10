"""ESPN's live JSON -> the decision engine's `GameState` (LD01).

Two sources share one builder (`build_state`):
- **live:** one game's scoreboard entry (`EspnClient.game`), whose `situation` describes the
  **next snap** (`parse_event`);
- **replay:** a finished (or running) game's summary, where each play's `start` is the state
  it was snapped from (`live/replay.py`).

The parser's rules (README §2 quirks; checked against 2026 data on 2026-10-10, LD01):
- `yardLine` counts from **ESPN's home team's** goal line: yards to the offense's goal =
  `100 - yardLine` when the offense is ESPN's home team, else `yardLine`. ESPN's own
  `yardsToEndzone` is never read (wrong on home punts, 0 on timeouts).
- ESPN's home / away can differ from nflverse's at neutral sites: the yard line uses ESPN's;
  `home` (+1 / 0 / -1) and the spread's sign use nflverse's (`GameContext`).
- `down = -1` (or no down) means no snap is pending: TV timeouts, a try or a kickoff next.
  With a summary at hand the next snap comes from the last real play's `end` state.
- Team codes go through `normalize_team` (WSH -> WAS, LAR -> LA).
- The clock: game seconds = (4 - quarter) x 900 + clock in regulation; in overtime both
  clocks are the OT clock (nflverse's convention, LD00).
- The spread and total are **pre-game** (curated `lines`, never ESPN's live line): a live
  line would leak the game state into the "pre-game strength" input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import polars as pl

from nflengine.curate.teams import normalize_team
from nflengine.live import schema
from nflengine.live.schema import GameState
from nflengine.paths import DataPaths, ensure_data_root

DEFAULT_TOTAL = 44.0
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS = re.compile(r"\s+")
# ESPN marks a quarter's or the game's end, timeouts and the two-minute warning as "plays"
ADMIN_TYPES = frozenset(
    {
        "Timeout",
        "Official Timeout",
        "Two-minute warning",
        "End Period",
        "End of Half",
        "End of Game",
        "End of Regulation",
        "Coin Toss",
    }
)
LINE_SOURCES = ("nflverse_schedules", "odds_api", "espn")
SNAPSHOT_TZ = "America/New_York"  # the ingest's `snapshot_date` is the local (Eastern) date


def plain(text: Any, limit: int = 300) -> str | None:
    """ESPN text as plain, single-line text (no control characters, capped). It's data: shown
    as text, never as HTML (README §5 rule 3); the app also passes it through `clean()`."""
    if text is None:
        return None
    s = _WS.sub(" ", _CTRL.sub(" ", str(text))).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def clock_seconds(display: Any) -> float | None:
    """ESPN's clock ("12:36", "0:21", ":42", or seconds as a number) -> seconds."""
    if display is None:
        return None
    if isinstance(display, int | float):
        return float(display)
    m = re.fullmatch(r"\s*(\d*):(\d{1,2})(?:\.\d+)?\s*", str(display))
    if not m:
        return None
    return float(int(m.group(1) or 0) * 60 + int(m.group(2)))


def game_clock(period: int, clock: float) -> tuple[float, float, bool]:
    """(game seconds, half seconds, overtime) from the quarter and its clock (nflverse)."""
    if period >= 5:
        return clock, clock, True
    half = clock + (900.0 if period in (1, 3) else 0.0)
    return (4 - period) * 900.0 + clock, half, False


def yards_to_goal(yard_line: int, offense_is_espn_home: bool) -> int:
    """ESPN's `yardLine` (from ESPN's home team's goal line) -> yards to the offense's goal."""
    return 100 - int(yard_line) if offense_is_espn_home else int(yard_line)


def parse_time(s: Any) -> datetime | None:
    """ESPN's ISO time ("2026-10-09T00:15:54Z", "2026-10-09T03:27Z") -> aware UTC."""
    if not s:
        return None
    t = str(s).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# --- what curated data says about a game (read only) ------------------------------------------
@dataclass(frozen=True)
class GameContext:
    """Pre-game facts for one game, from curated data (never written; D107).

    `home` / `away` are nflverse's (canonical codes); `home_spread` follows nflverse (+ = home
    favoured) and is the newest pre-kickoff line (`line_source` says which). `wind` / `temp`
    are nflverse's game-day readings for a played game, else the pre-game forecast."""

    season: int
    home: str
    away: str
    game_id: str | None = None
    week: int | None = None
    home_spread: float | None = None
    total: float | None = None
    line_source: str | None = None
    roof: str | None = None
    neutral_site: bool = False
    playoffs: bool = False
    kickoff_utc: datetime | None = None
    wind: float | None = None
    temp: float | None = None


class Contexts:
    """Every game of a season with its pre-game facts, and ESPN's event ids mapped to our
    `game_id` (curated `espn_scoreboard` first, then date and teams)."""

    def __init__(self, games: pl.DataFrame, event_map: dict[str, str]):
        self.games = games
        self.event_map = event_map
        self._by_id = {r["game_id"]: r for r in games.iter_rows(named=True)}

    def game_id_for(self, event_id: str, event: dict[str, Any] | None = None) -> str | None:
        gid = self.event_map.get(str(event_id))
        if gid is not None or event is None:
            return gid
        teams = event_teams(event)
        if not teams:
            return None
        home = next((t["team"] for t in teams.values() if t["home_away"] == "home"), None)
        away = next((t["team"] for t in teams.values() if t["home_away"] == "away"), None)
        when = parse_time(event.get("date"))
        best = None
        for r in self._by_id.values():
            if {r["home_team"], r["away_team"]} != {home, away}:
                continue
            k = r.get("kickoff_utc")
            gap = abs((k - when).total_seconds()) if (k is not None and when) else 0.0
            if gap <= 36 * 3600 and (best is None or gap < best[0]):
                best = (gap, r["game_id"])
        return best[1] if best else None

    def context(self, event_id: str, event: dict[str, Any] | None = None) -> GameContext | None:
        gid = self.game_id_for(event_id, event)
        return self.by_game(gid) if gid else None

    def by_game(self, gid: str) -> GameContext | None:
        """One game's pre-game facts by our `game_id` (LD02: a slate without ESPN ids)."""
        r = self._by_id.get(gid)
        if r is None:
            return None
        return GameContext(
            season=int(r["season"]),
            week=int(r["week"]),
            game_id=gid,
            home=r["home_team"],
            away=r["away_team"],
            home_spread=r.get("pre_spread"),
            total=r.get("pre_total"),
            line_source=r.get("line_source"),
            roof=r.get("roof"),
            neutral_site=bool(r.get("neutral_site")),
            playoffs=r.get("game_type") not in (None, "REG"),
            kickoff_utc=r.get("kickoff_utc"),
            wind=r.get("ctx_wind"),
            temp=r.get("ctx_temp"),
        )


def pre_game_lines(lines: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """One pre-game spread and total per game: nflverse's schedule line (pre-game or closing),
    else the median across the Odds API's books, else ESPN's (DraftKings), counting only rows
    snapshotted before kickoff day for the last two (D40's order; README → LD01).

    `snapshot_date` is a date only, in the ingest machine's local time (US Eastern), so the
    comparison uses the kickoff's **Eastern** date and drops the whole game day: a Thursday
    20:15 ET game is 00:15 UTC on Friday, and a row saved Thursday night (maybe in game) must
    not pass (Sol review, LD01)."""
    k = games.select(
        "game_id", pl.col("kickoff_utc").dt.convert_time_zone(SNAPSHOT_TZ).dt.date().alias("kd")
    )
    df = lines.join(k, on="game_id", how="inner").with_columns(
        pl.col("snapshot_date").str.to_date(strict=False).alias("sd")
    )
    ok = (pl.col("source") == "nflverse_schedules") | (pl.col("sd") < pl.col("kd")).fill_null(False)
    df = df.filter(ok & pl.col("home_spread").is_not_null())
    agg = df.group_by("game_id", "source").agg(
        pl.col("home_spread").median().alias("spread"), pl.col("total").median().alias("total")
    )
    rank = {s: i for i, s in enumerate(LINE_SOURCES)}
    agg = agg.with_columns(pl.col("source").replace_strict(rank, default=99).alias("rk"))
    best = agg.sort("game_id", "rk").group_by("game_id", maintain_order=True).first()
    return best.select(
        "game_id",
        pl.col("spread").alias("pre_spread"),
        pl.col("total").alias("pre_total"),
        pl.col("source").alias("line_source"),
    )


def load_contexts(season: int, paths: DataPaths | None = None) -> Contexts:
    """Read `games`, `lines`, `espn_scoreboard` and `weather_forecasts` for one season (read
    only, from the curated Parquet files)."""
    paths = paths or ensure_data_root(create_dirs=False)
    cur = paths.curated
    every = pl.read_parquet(cur / "games.parquet")
    games = every.filter(pl.col("season") == season)
    games = games.with_columns(pl.col("kickoff_utc").dt.convert_time_zone("UTC"))
    keep = [
        "game_id", "season", "week", "game_type", "home_team", "away_team", "kickoff_utc",
        "roof", "neutral_site", "spread_line", "total_line", "temp", "wind", "stadium",
    ]  # fmt: skip
    games = games.select([c for c in keep if c in games.columns])
    if "stadium" in games.columns:
        # nflverse fills `roof` at retractable-roof stadiums only after the game: until then use
        # the stadium's usual roof (AT&T Stadium: closed in 109 of 112 games)
        usual = (
            every.filter(pl.col("roof").is_not_null() & pl.col("stadium").is_not_null())
            .group_by("stadium", "roof")
            .len()
            .sort("len", descending=True)
            .group_by("stadium", maintain_order=True)
            .first()
            .select("stadium", pl.col("roof").alias("usual_roof"))
        )
        games = games.join(usual, on="stadium", how="left").with_columns(
            pl.coalesce("roof", "usual_roof").alias("roof")
        )
    lines_path = cur / "lines.parquet"
    if lines_path.exists():
        pre = pre_game_lines(pl.read_parquet(lines_path), games)
        games = games.join(pre, on="game_id", how="left")
    else:
        games = games.with_columns(
            pl.lit(None, pl.Float64).alias("pre_spread"),
            pl.lit(None, pl.Float64).alias("pre_total"),
            pl.lit(None, pl.String).alias("line_source"),
        )
    # nflverse's own schedule line when `lines` has nothing for the game
    games = games.with_columns(
        pl.coalesce("pre_spread", "spread_line").alias("pre_spread"),
        pl.coalesce("pre_total", "total_line").alias("pre_total"),
        pl.when(pl.col("line_source").is_null() & pl.col("spread_line").is_not_null())
        .then(pl.lit("games"))
        .otherwise(pl.col("line_source"))
        .alias("line_source"),
    )
    wx_path = cur / "weather_forecasts.parquet"
    if wx_path.exists():
        wx = pl.read_parquet(wx_path, columns=["game_id", "temp_f", "wind_mph", "pulled_at"])
        wx = wx.sort("pulled_at").group_by("game_id").last().drop("pulled_at")
        games = games.join(wx, on="game_id", how="left")
    else:
        games = games.with_columns(
            pl.lit(None, pl.Float64).alias("temp_f"), pl.lit(None, pl.Float64).alias("wind_mph")
        )
    games = games.with_columns(
        pl.coalesce(pl.col("wind").cast(pl.Float64), "wind_mph").alias("ctx_wind"),
        pl.coalesce(pl.col("temp").cast(pl.Float64), "temp_f").alias("ctx_temp"),
    )
    event_map: dict[str, str] = {}
    sb_path = cur / "espn_scoreboard.parquet"
    if sb_path.exists():
        sb = pl.read_parquet(sb_path, columns=["espn_event_id", "game_id", "season"])
        sb = sb.filter((pl.col("season") == season) & pl.col("game_id").is_not_null())
        event_map = dict(zip(sb["espn_event_id"].cast(pl.String), sb["game_id"], strict=True))
    return Contexts(games, event_map)


# --- ESPN JSON helpers ------------------------------------------------------------------------
def competition(event: dict[str, Any]) -> dict[str, Any]:
    """The event's single competition (a scoreboard entry or a summary's header)."""
    comps = event.get("competitions") or [{}]
    return comps[0] or {}


def event_teams(event: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """ESPN team id -> {team (canonical), espn (ESPN's code), home_away, score}."""
    out = {}
    for c in competition(event).get("competitors") or []:
        team = c.get("team") or {}
        tid = str(c.get("id") or team.get("id") or "")
        abbr = team.get("abbreviation")
        if not tid or not abbr:
            continue
        try:
            score = int(float(c.get("score"))) if c.get("score") not in (None, "") else 0
        except (TypeError, ValueError, OverflowError):  # "1e309" is inf (Sol review, LD02)
            score = 0
        out[tid] = {
            "team": normalize_team(abbr),
            "espn": abbr,
            "home_away": c.get("homeAway"),
            "score": score,
        }
    return out


def summary_plays(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Every play of a summary in order (finished drives, then the current one)."""
    drives = summary.get("drives") or {}
    plays: dict[str, dict[str, Any]] = {}
    for d in [*(drives.get("previous") or []), drives.get("current") or {}]:
        for p in d.get("plays") or []:
            if p.get("id") is not None:
                plays[str(p["id"])] = p

    def seq(p: dict[str, Any]) -> tuple[int, str]:
        try:
            return int(p.get("sequenceNumber")), str(p["id"])
        except (TypeError, ValueError):
            return 1 << 60, str(p["id"])

    return sorted(plays.values(), key=seq)


def opening_receiver(summary: dict[str, Any]) -> str | None:
    """Who received the opening kickoff (canonical): the other team from the first
    first-quarter kickoff's kicking team (ESPN's kickoff `start.team` is the kicker)."""
    teams = event_teams(summary.get("header") or {})
    for p in summary_plays(summary):
        if (p.get("period") or {}).get("number") != 1:
            continue
        if "kickoff" not in ((p.get("type") or {}).get("text") or "").lower():
            continue
        kicker = str(((p.get("start") or {}).get("team") or {}).get("id") or "")
        others = [t["team"] for tid, t in teams.items() if tid != kicker]
        if kicker in teams and len(others) == 1:
            return others[0]
    return None


def play_times(summary: dict[str, Any]) -> dict[str, datetime]:
    """ESPN play id -> its `wallclock` (the snap time, UTC)."""
    out = {}
    for p in summary_plays(summary):
        t = parse_time(p.get("wallclock"))
        if t is not None:
            out[str(p["id"])] = t
    return out


def last_real_play(summary: dict[str, Any]) -> dict[str, Any] | None:
    """The newest play that isn't a timeout, a quarter's end or the two-minute warning."""
    for p in reversed(summary_plays(summary)):
        if ((p.get("type") or {}).get("text") or "") not in ADMIN_TYPES:
            return p
    return None


# --- one builder for both sources -------------------------------------------------------------
@dataclass(frozen=True)
class Snap:
    """A snap's raw facts from ESPN, before the context is applied."""

    offense: str  # canonical
    offense_is_espn_home: bool
    period: int
    clock: float
    down: int
    distance: int
    yard_line: int  # ESPN's, from ESPN's home team's goal line
    off_score: int
    def_score: int
    off_timeouts: int | None
    def_timeouts: int | None


def build_state(
    ctx: GameContext,
    snap: Snap,
    *,
    opening_receiver: str | None = None,
    indoor_hint: bool | None = None,
) -> tuple[GameState | None, str | None, list[str]]:
    """(state, reason it couldn't be built, warnings). `reason` is set only when `state` is
    None; warnings say what was assumed (no line, no timeouts, unknown opening kickoff ...)."""
    warn: list[str] = []
    if snap.down not in (1, 2, 3, 4):
        return None, f"no down to check (ESPN shows down {snap.down})", warn
    ytg = yards_to_goal(snap.yard_line, snap.offense_is_espn_home)
    if not 1 <= ytg <= 99:
        return None, f"ESPN's yard line {snap.yard_line} isn't a spot on the field", warn
    distance = snap.distance
    if distance < 1:
        warn.append(f"ESPN's distance was {distance}: treated as 1 (inches)")
        distance = 1
    if distance > ytg:
        warn.append(f"distance {distance} beyond the goal line: goal to go ({ytg})")
        distance = ytg
    gs, hs, ot = game_clock(snap.period, snap.clock)
    if snap.offense not in (ctx.home, ctx.away):
        return None, f"ESPN's offense {snap.offense} isn't in {ctx.away} at {ctx.home}", warn
    is_home = snap.offense == ctx.home
    home = 0 if ctx.neutral_site else (1 if is_home else -1)
    if ctx.home_spread is None:
        warn.append("no pre-game line: spread 0 assumed")
        spread = 0.0
    else:
        spread = float(ctx.home_spread) if is_home else -float(ctx.home_spread)
    total = float(ctx.total) if ctx.total is not None else DEFAULT_TOTAL
    if ctx.total is None:
        warn.append(f"no pre-game total: {DEFAULT_TOTAL:g} assumed")
    off_to, def_to = snap.off_timeouts, snap.def_timeouts
    # each team gets 3 a half, 2 in a regular-season overtime, 3 in playoff overtime
    most = 2 if (ot and not ctx.playoffs) else 3
    if off_to is None or def_to is None:
        warn.append(f"timeouts unknown: {most} each assumed")
        off_to = most if off_to is None else off_to
        def_to = most if def_to is None else def_to
    first_half = (not ot) and snap.period <= 2
    r2h = 0
    if first_half:
        if opening_receiver is None:
            warn.append("opening kickoff unknown: the offense assumed to get the 2nd-half kick")
            r2h = 1
        else:
            r2h = int(snap.offense != opening_receiver)
    roof = ctx.roof
    if roof is None:
        roof = "closed" if indoor_hint else "outdoors"
    outdoor = not schema.indoor(roof)
    try:
        state = GameState(
            season=ctx.season,
            score_diff=snap.off_score - snap.def_score,
            game_seconds=gs,
            half_seconds=hs,
            down=snap.down,
            ydstogo=distance,
            yardline_100=ytg,
            off_timeouts=max(0, min(most, off_to)),
            def_timeouts=max(0, min(most, def_to)),
            home=home,
            receive_2h_ko=r2h,
            spread=spread,
            total=total,
            roof=roof,
            ot=ot,
            playoffs=ctx.playoffs,
            wind=ctx.wind if outdoor else None,
            temp=ctx.temp if outdoor else None,
        )
    except ValueError as e:
        return None, f"ESPN's state didn't pass the checks: {e}", warn
    return state, None, warn


# --- the live parser --------------------------------------------------------------------------
@dataclass
class LiveState:
    """One game right now, from ESPN's scoreboard entry. `state` is the `GameState` of the next
    snap when there is one (any down); `reason` says why not otherwise."""

    event_id: str
    game_id: str | None
    status: str  # pre / in / post
    detail: str | None  # ESPN's status line ("2:10 - 3rd Quarter")
    period: int
    clock: float
    display_clock: str | None
    home: str  # nflverse's home team (canonical)
    away: str
    home_score: int
    away_score: int
    offense: str | None = None
    defense: str | None = None
    down: int | None = None
    distance: int | None = None
    yardline_100: int | None = None
    home_timeouts: int | None = None
    away_timeouts: int | None = None
    situation_text: str | None = None  # ESPN's "4th & 10 at TEN 14"
    last_play_id: str | None = None
    last_play_type: str | None = None
    last_play_text: str | None = None
    last_play_at: datetime | None = None  # the last play's wallclock (from a summary)
    espn_home_wp: float | None = None  # ESPN's own win probability after the last play
    fetched_at: datetime | None = None
    state: GameState | None = None
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    source: str = "situation"  # or "summary" when the next snap came from the summary

    @property
    def is_decision(self) -> bool:
        """A 3rd or 4th down the engine can score."""
        return self.state is not None and self.state.down in (3, 4)

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "state"}
        d["state"] = self.state.as_dict() if self.state is not None else None
        for k in ("last_play_at", "fetched_at"):
            d[k] = d[k].isoformat() if d[k] is not None else None
        return d


def parse_event(
    event: dict[str, Any],
    ctx: GameContext,
    *,
    fetched_at: datetime | None = None,
    opening: str | None = None,
    summary: dict[str, Any] | None = None,
) -> LiveState:
    """One game's scoreboard entry -> `LiveState`.

    `opening` = the team that received the opening kickoff (from the summary; needed in the
    first half). `summary`, when given, supplies the opening kickoff, the last play's time and
    the next snap when ESPN's situation has no down (`down = -1` on TV timeouts)."""
    comp = competition(event)
    status = comp.get("status") or event.get("status") or {}
    stype = status.get("type") or {}
    teams = event_teams(event)
    by_code = {t["team"]: t for t in teams.values()}
    period = int(status.get("period") or 0)
    clock = clock_seconds(status.get("clock"))
    if clock is None:
        clock = clock_seconds(status.get("displayClock")) or 0.0
    hs = by_code.get(ctx.home, {}).get("score", 0)
    as_ = by_code.get(ctx.away, {}).get("score", 0)
    out = LiveState(
        event_id=str(event.get("id")),
        game_id=ctx.game_id,
        status=str(stype.get("state") or "pre"),
        detail=plain(stype.get("detail") or stype.get("shortDetail"), 80),
        period=period,
        clock=float(clock),
        display_clock=plain(status.get("displayClock"), 10),
        home=ctx.home,
        away=ctx.away,
        home_score=int(hs),
        away_score=int(as_),
        fetched_at=fetched_at,
    )
    espn_home = next((t["team"] for t in teams.values() if t["home_away"] == "home"), None)
    if espn_home is not None and espn_home != ctx.home:
        out.warnings.append(
            f"ESPN lists {espn_home} at home, nflverse {ctx.home}: yard lines use ESPN's"
        )
    if summary is not None and opening is None:
        opening = opening_receiver(summary)
    sit = comp.get("situation") or {}
    last = sit.get("lastPlay") or {}
    if last:
        out.last_play_id = str(last.get("id")) if last.get("id") is not None else None
        out.last_play_type = plain((last.get("type") or {}).get("text"), 60)
        out.last_play_text = plain(last.get("text"))
        prob = (last.get("probability") or {}).get("homeWinPercentage")
        if isinstance(prob, int | float):
            # ESPN's home is nflverse's away at a neutral site that disagrees: flip it
            out.espn_home_wp = float(prob) if espn_home in (None, ctx.home) else 1 - float(prob)
    if summary is not None and out.last_play_id:
        out.last_play_at = play_times(summary).get(out.last_play_id)
    if out.status == "pre":
        out.reason = "the game hasn't started"
        return out
    if out.status == "post":
        out.reason = "the game is over"
        return out
    out.home_timeouts = _int(sit.get("homeTimeouts"))
    out.away_timeouts = _int(sit.get("awayTimeouts"))
    if espn_home is not None and espn_home != ctx.home:  # timeouts follow ESPN's sides
        out.home_timeouts, out.away_timeouts = out.away_timeouts, out.home_timeouts
    out.situation_text = plain(sit.get("downDistanceText"), 60)
    if out.last_play_type in ("End of Half", "End of Game", "End of Regulation"):
        out.reason = {
            "End of Half": "halftime: the second half starts with a kickoff",
            "End of Game": "the game is over",
            "End of Regulation": "end of regulation: overtime starts with a kickoff",
        }[out.last_play_type]
        return out
    if len(teams) != 2:  # an answer without both teams has no defense to pick (Sol review, LD02)
        out.reason = "ESPN's answer is missing a team"
        return out
    down = _int(sit.get("down"))
    poss = str(sit.get("possession") or "")
    yard_line = _int(sit.get("yardLine"))
    distance = _int(sit.get("distance"))
    if down not in (1, 2, 3, 4) or poss not in teams or yard_line is None:
        nxt, why = (
            _next_snap_from_summary(summary, teams, out.last_play_id)
            if summary is not None
            else (None, None)
        )
        if nxt is None:
            out.reason = "between plays: no snap pending (a try, a kickoff or a TV timeout)"
            if why:
                out.reason += f"; {why}"
            return out
        down, distance, yard_line, poss = nxt
        out.source = "summary"
    offense = teams[poss]["team"]
    defense = next(t["team"] for tid, t in teams.items() if tid != poss)
    out.offense, out.defense = offense, defense
    out.down, out.distance = down, distance
    is_espn_home = teams[poss]["home_away"] == "home"
    off_score = by_code.get(offense, {}).get("score", 0)
    def_score = by_code.get(defense, {}).get("score", 0)
    off_to = out.home_timeouts if offense == ctx.home else out.away_timeouts
    def_to = out.away_timeouts if offense == ctx.home else out.home_timeouts
    snap = Snap(
        offense=offense,
        offense_is_espn_home=is_espn_home,
        period=period,
        clock=float(clock),
        down=int(down),
        distance=int(distance or 0),
        yard_line=int(yard_line),
        off_score=int(off_score),
        def_score=int(def_score),
        off_timeouts=off_to,
        def_timeouts=def_to,
    )
    out.yardline_100 = yards_to_goal(snap.yard_line, is_espn_home)
    indoor = (comp.get("venue") or {}).get("indoor")
    state, reason, warn = build_state(
        ctx,
        snap,
        opening_receiver=opening,
        indoor_hint=bool(indoor) if indoor is not None else None,
    )
    out.state, out.reason = state, reason
    out.warnings.extend(warn)
    return out


def _int(x: Any) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _next_snap_from_summary(
    summary: dict[str, Any], teams: dict[str, dict[str, Any]], last_play_id: str | None
) -> tuple[tuple[int, int, int, str] | None, str | None]:
    """((down, distance, yardLine, offense's ESPN id), None) for the next snap: the `end` state
    of the last real play **up to the scoreboard's `lastPlay`** (the two answers come from
    separate requests, so the summary can be older or newer than the scoreboard: Sol review,
    LD01). (None, why) when the summary doesn't reach the scoreboard's last play yet, or a
    try or a kickoff is next (why = None)."""
    plays = summary_plays(summary)
    ids = [str(p.get("id")) for p in plays]
    if not last_play_id or last_play_id not in ids:
        return None, "the summary hasn't caught up with the scoreboard yet"
    upto = plays[: ids.index(last_play_id) + 1]
    p = next(
        (q for q in reversed(upto) if ((q.get("type") or {}).get("text") or "") not in ADMIN_TYPES),
        None,
    )
    if p is None:
        return None, None
    end = p.get("end") or {}
    down, dist, yl = _int(end.get("down")), _int(end.get("distance")), _int(end.get("yardLine"))
    tid = str((end.get("team") or {}).get("id") or "")
    if down not in (1, 2, 3, 4) or yl is None or tid not in teams:
        return None, None
    return (down, dist or 0, yl, tid), None
