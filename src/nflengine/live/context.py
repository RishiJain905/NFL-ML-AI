"""Team context for the Game day call card (LD02): "is this team good at this?"

For every team, as of the START of one week, from curated play-by-play only (read-only; D107):
how often it goes for it on 4th down and how often it converts, its short-yardage and
red-zone numbers, its kicker's makes by distance, its punter's net average and how often its
head coach goes in the spots where the bot says go. Each number comes with the league's beside
it. Built once per (season, week), cached in memory and as one JSON file per week (`ContextCache`).

**The windows.** *This season* = `season`'s plays with `week < week` (the leakage rule: nothing
from the week being played, nor later). *Last season* = every play of `season - 1`. Regular
season and playoffs both count. *League* = every team pooled, same windows.

**The measures** (every rate = num / den rounded to 4 places, None when den is 0; teams are the
codes in this season's schedule, so a team with no plays yet gets zeros):
- `go_rate`: 4th downs decided = `down == 4` and `play_type` run / pass / field_goal / punt (no
  no_plays, no kneels); num = run or pass (fakes are typed run / pass by nflverse).
- `fourth_conv`: on those go plays, num = `fourth_down_converted == 1`.
- `short_conv`: `down` 3 or 4, `ydstogo <= 2`, run or pass, no two-point tries; num =
  `third_down_converted` or `fourth_down_converted` == 1.
- `red_zone_td`: the offense's *drives* (distinct game, `fixed_drive`, `posteam`) with
  `drive_inside20 == 1`; num = `fixed_drive_result == "Touchdown"` (an offensive touchdown;
  "Opp touchdown" is the defense's). Only drives with a snap count: kickoff and extra-point rows
  carry the previous drive's number under the other team's `posteam` and would add phantom
  drives (2 of 1,826 red-zone "drives" in 2025).
- `coach`: the offense's head coach = `home_coach` / `away_coach` from the plays (by `posteam`
  vs `home_team`) with `config/head_coach_fixes.csv` applied (`coach_fixes`: the fix's coach
  from `from_week` of that season on, for that team; a later `from_week` wins). A team's coach
  for the context = its coach in its newest game this season (before `week`); else in its
  first game of `season` in the schedule (`games.parquet`, fixes applied); else last season's.
  `coach_last_team` = the team he coached most decided 4th downs for last season (ties: the
  alphabetically first code); None when he was not a head coach then.
- `coach_go`: the coach's own 4th downs (by coach name, after the fixes; any team) where the bot
  says go (`Engine.fourth_downs(..., bootstrap=False)` best == "go"): num = he went, den = such
  spots. `coach_go_by_distance` splits the same by `ydstogo` (1-2, 3-5, 6+). Needs an engine;
  without one `coach_go` is None and the split is []. Only 4th downs the engine can score count
  (`data.fourth_frame` rows with a full state; the rest are counted in `coach_go_skipped`).
- `kicker`: the `kicker_player_id` of the team's newest field goal or extra point this season
  (else last season's newest); `name` = `kicker_player_name`. `bands` = his field goals (any
  team) by `kick_distance` (else yards to goal + 18) <30 / 30-39 / 40-49 / 50+: num = made, den =
  attempts (a block is a miss), this season and last season, with the league's last-season band.
  `long` = his longest made field goal in every curated season up to this week (2010+, any team),
  `long_season` = the latest season he made one that long.
- `punter`: the `punter_player_id` of the team's newest punt (same rule). For him (any team) and
  for the league: `punts`, `gross` = mean `kick_distance` of punts that have one, `net` =
  (sum of `kick_distance` (a blocked punt = 0) - sum of `return_yards` (null = 0) - 20 per
  touchback) / punts. Yard averages are rounded to 2 places.

Never imports `nflengine.models`, `nflengine.features` or `nflengine.graph` (D107), and writes
nothing but the cache folder it is given.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from nflengine.live import data
from nflengine.paths import DataPaths

SCHEMA_VERSION = 1
BANDS_KICK = ("<30", "30-39", "40-49", "50+")
BANDS_DIST = ("1-2", "3-5", "6+")
FIXES_FILE = "head_coach_fixes.csv"

PLAYS_COLUMNS = [
    "game_id", "play_id", "order_sequence", "season", "week", "season_type", "posteam",
    "home_team", "home_coach", "away_coach", "play_type", "down", "ydstogo", "yardline_100",
    "two_point_attempt", "third_down_converted", "fourth_down_converted", "fixed_drive",
    "fixed_drive_result", "drive_inside20", "kicker_player_id", "kicker_player_name",
    "punter_player_id", "punter_player_name", "kick_distance", "return_yards", "touchback",
    "punt_blocked", "field_goal_result",
]  # fmt: skip
_STRING_COLUMNS = {
    "game_id", "season_type", "posteam", "home_team", "home_coach", "away_coach", "play_type",
    "fixed_drive_result", "kicker_player_id", "kicker_player_name", "punter_player_id",
    "punter_player_name", "field_goal_result",
}  # fmt: skip
_STATE_COLUMNS = [
    "season", "score_diff", "game_seconds", "half_seconds", "down", "ydstogo", "yardline_100",
    "off_timeouts", "def_timeouts", "home", "receive_2h_ko", "spread", "total", "indoor", "ot",
    "playoffs",
]  # fmt: skip
# a kick's distance is the yards to goal + 18 (the same fallback as data.fg_frame)
FG_YARDS_BEHIND_LINE = 18
PUNT_TOUCHBACK_YARDS = 20


# --- head-coach fixes -------------------------------------------------------------------------
def default_fixes_path() -> Path:
    from nflengine.settings import CONFIG_DIR

    return CONFIG_DIR / FIXES_FILE


def coach_fixes(path: Path | None = None) -> pl.DataFrame:
    """`season, team, from_week, coach` from `config/head_coach_fixes.csv` (lines starting with
    `#` are comments): hand-checked corrections to nflverse's head coaches (a copy of
    `graph/tables_extra.read_coach_fixes`, D107). A missing or malformed file is an empty frame,
    never an exception."""
    path = path or default_fixes_path()
    empty = pl.DataFrame(
        schema={"season": pl.Int32, "team": pl.String, "from_week": pl.Int32, "coach": pl.String}
    )
    try:
        if not path.exists():
            return empty
        df = pl.read_csv(path, comment_prefix="#", infer_schema_length=0)
        df = df.rename({c: c.strip().lower() for c in df.columns})
        return df.select(
            pl.col("season").str.strip_chars().cast(pl.Int32),
            pl.col("team").str.strip_chars().str.to_uppercase(),
            pl.col("from_week").str.strip_chars().cast(pl.Int32),
            pl.col("coach").str.strip_chars(),
        ).filter(pl.col("coach").is_not_null() & (pl.col("coach") != ""))
    except Exception:  # a hand-made file must never sink the card
        return empty


def _apply_fixes(df: pl.DataFrame, fixes: pl.DataFrame, team_col: str) -> pl.DataFrame:
    """`df.coach` with the fixes applied: for each (season, team) the coach from its `from_week`
    on (rows are applied in `from_week` order, so a later one wins). Needs `season`, `week`."""
    out = df
    for r in fixes.sort("season", "team", "from_week").iter_rows(named=True):
        hit = (
            (pl.col("season") == r["season"])
            & (pl.col(team_col) == r["team"])
            & (pl.col("week") >= r["from_week"])
        )
        out = out.with_columns(
            pl.when(hit).then(pl.lit(r["coach"])).otherwise(pl.col("coach")).alias("coach")
        )
    return out


# --- reading ----------------------------------------------------------------------------------
def _plays_file(paths: DataPaths, season: int) -> Path:
    return paths.curated / "plays" / f"season={season}.parquet"


def _empty_plays() -> pl.DataFrame:
    def dtype(c: str) -> pl.DataType:
        if c in _STRING_COLUMNS:
            return pl.String
        return pl.Int32 if c in ("season", "week") else pl.Float64

    return pl.DataFrame(schema={c: dtype(c) for c in PLAYS_COLUMNS})


def _read_window(paths: DataPaths, season: int, win: str, before_week: int | None) -> pl.DataFrame:
    """One window of plays (REG + POST) with a `win` column; `before_week` cuts `week < it`."""
    path = _plays_file(paths, season)
    if not path.exists():
        return _empty_plays().with_columns(pl.lit(win).alias("win"))
    lf = (
        pl.scan_parquet(path)
        .select(PLAYS_COLUMNS)
        .filter((pl.col("season") == season) & pl.col("season_type").is_in(["REG", "POST"]))
    )
    if before_week is not None:
        lf = lf.filter(pl.col("week") < before_week)
    return lf.with_columns(pl.lit(win).alias("win")).collect()


def _with_coach(plays: pl.DataFrame, fixes: pl.DataFrame) -> pl.DataFrame:
    """Add `coach`: the offense's head coach on each play, fixes applied."""
    is_home = pl.col("posteam") == pl.col("home_team")
    coach = (
        pl.when(pl.col("posteam").is_null())
        .then(None)
        .when(is_home)
        .then(pl.col("home_coach"))
        .otherwise(pl.col("away_coach"))
    )
    return _apply_fixes(plays.with_columns(coach.alias("coach")), fixes, "posteam")


def _schedule(paths: DataPaths, seasons: tuple[int, ...]) -> pl.DataFrame:
    """One row per (season, week, team) from `games.parquet` with the game's head coach (fixes
    are applied by the caller)."""
    path = paths.curated / "games.parquet"
    cols = ["season", "week", "home_team", "away_team", "home_coach", "away_coach"]
    if not path.exists():
        return pl.DataFrame(
            schema={"season": pl.Int32, "week": pl.Int32, "team": pl.String, "coach": pl.String}
        )
    g = pl.read_parquet(path, columns=cols).filter(pl.col("season").is_in(list(seasons)))
    home = g.select("season", "week", pl.col("home_team").alias("team"), "home_coach")
    away = g.select("season", "week", pl.col("away_team").alias("team"), "away_coach")
    both = pl.concat([home.rename({"home_coach": "coach"}), away.rename({"away_coach": "coach"})])
    return both.with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))


# --- tallies ----------------------------------------------------------------------------------
def _stat(num: int, den: int) -> dict:
    return {"num": int(num), "den": int(den), "rate": round(num / den, 4) if den else None}


class _Tally:
    """num / den counts by (window, *by), and pooled over every key (the league)."""

    def __init__(self, df: pl.DataFrame, by: list[str], den: pl.Expr, num: pl.Expr):
        agg = (
            df.filter(den.fill_null(False))
            .group_by(["win", *by])
            .agg(num.fill_null(False).cast(pl.Int64).sum().alias("_n"), pl.len().alias("_d"))
        )
        self._rows: dict[tuple, tuple[int, int]] = {}
        self._pool = {"this": [0, 0], "last": [0, 0]}
        for *key, n, d in agg.iter_rows():
            self._rows[tuple(key)] = (n, d)
            self._pool[key[0]][0] += n
            self._pool[key[0]][1] += d

    def stat(self, win: str, *key) -> dict:
        return _stat(*self._rows.get((win, *key), (0, 0)))

    def league(self, win: str) -> dict:
        return _stat(*self._pool[win])

    def measure(self, *key) -> dict:
        return {
            "this": self.stat("this", *key),
            "last": self.stat("last", *key),
            "league_this": self.league("this"),
            "league_last": self.league("last"),
        }


def _punting(punts: int, kick_sum: float, kick_n: int, net_sum: float) -> dict:
    return {
        "punts": int(punts),
        "net": round(net_sum / punts, 2) if punts else None,
        "gross": round(kick_sum / kick_n, 2) if kick_n else None,
    }


class _Punts:
    """Punt totals by (window, punter) and by window (the league)."""

    def __init__(self, plays: pl.DataFrame):
        punts = plays.filter(
            (pl.col("play_type") == "punt") & pl.col("punter_player_id").is_not_null()
        )
        net = (
            pl.when(pl.col("punt_blocked") == 1)
            .then(0.0)
            .otherwise(pl.col("kick_distance").fill_null(0.0))
            - pl.col("return_yards").fill_null(0.0)
            - PUNT_TOUCHBACK_YARDS * (pl.col("touchback") == 1).fill_null(False).cast(pl.Float64)
        )
        aggs = [
            pl.len().alias("punts"),
            pl.col("kick_distance").sum().alias("kick_sum"),
            pl.col("kick_distance").count().alias("kick_n"),
            net.sum().alias("net_sum"),
        ]
        self._by: dict[tuple, dict] = {}
        for win, pid, *vals in punts.group_by("win", "punter_player_id").agg(aggs).iter_rows():
            self._by[(win, pid)] = _punting(*vals)
        self._league = {
            win: _punting(*vals) for win, *vals in punts.group_by("win").agg(aggs).iter_rows()
        }

    def punter(self, win: str, pid: str) -> dict:
        return self._by.get((win, pid)) or _punting(0, 0.0, 0, 0.0)

    def league(self, win: str) -> dict:
        return self._league.get(win) or _punting(0, 0.0, 0, 0.0)


def _band(distance: pl.Expr, labels: tuple[str, ...], cuts: tuple[float, ...]) -> pl.Expr:
    """A label by the first cut the value is under (`cuts` ascending), else the last label."""
    expr = pl.when(distance.is_null()).then(None)
    for label, cut in zip(labels, cuts, strict=False):
        expr = expr.when(distance < cut).then(pl.lit(label))
    return expr.otherwise(pl.lit(labels[-1]))


def _latest_by_team(plays: pl.DataFrame, kinds: list[str], id_col: str, name_col: str) -> dict:
    """team -> (player id, name) from the team's newest play of these types (this window)."""
    rows = (
        plays.filter(
            pl.col("play_type").is_in(kinds)
            & pl.col(id_col).is_not_null()
            & pl.col("posteam").is_not_null()
        )
        .sort("week", "order_sequence", "play_id")
        .group_by("posteam", maintain_order=True)
        .agg(pl.col(id_col).last().alias("pid"), pl.col(name_col).last().alias("pname"))
    )
    return {team: (pid, pname) for team, pid, pname in rows.iter_rows()}


def _career_longs(
    paths: DataPaths, season: int, week: int, kicker_ids: set[str]
) -> dict[str, tuple[int, int]]:
    """kicker id -> (longest made field goal, the latest season he made one that long) over every
    curated season up to `season` (2010+, any team), counting only plays before `week` of
    `season`. Only the ids asked for."""
    if not kicker_ids:
        return {}
    files = [
        f
        for f in sorted((paths.curated / "plays").glob("season=*.parquet"))
        if f.stem.removeprefix("season=").isdigit()
        and int(f.stem.removeprefix("season=")) <= season
    ]
    if not files:
        return {}
    cols = ["season", "week", "season_type", "play_type", "field_goal_result",
            "kicker_player_id", "kick_distance"]  # fmt: skip
    lf = (
        pl.concat([pl.scan_parquet(f).select(cols) for f in files], how="vertical_relaxed")
        .filter(
            pl.col("season_type").is_in(["REG", "POST"])
            & (pl.col("play_type") == "field_goal")
            & (pl.col("field_goal_result") == "made")
            & pl.col("kicker_player_id").is_in(sorted(kicker_ids))
            & pl.col("kick_distance").is_not_null()
            & ((pl.col("season") < season) | (pl.col("week") < week))
        )
    )  # fmt: skip
    best = (
        lf.group_by("kicker_player_id", "season")
        .agg(pl.col("kick_distance").max().alias("long"))
        .sort("kicker_player_id", "long", "season")
        .group_by("kicker_player_id", maintain_order=True)
        .agg(pl.col("long").last(), pl.col("season").last())
        .collect()
    )
    return {pid: (int(round(lng)), int(s)) for pid, lng, s in best.iter_rows()}


# --- the bot's view of the coaches' 4th downs -------------------------------------------------
_SCORED_SCHEMA = {
    "win": pl.String, "game_id": pl.String, "posteam": pl.String, "ydstogo": pl.Int64,
    "choice": pl.String, "best": pl.String,
}  # fmt: skip


def _scored_fourth_downs(
    season: int, week: int, paths: DataPaths, engine, coach_map: pl.DataFrame
) -> tuple[pl.DataFrame, int]:
    """Every 4th down of both windows the engine can score: `win`, `posteam`, `ydstogo`, the
    coach's `choice`, the bot's `best` and the coach's name (`coach`, null when unknown).
    Returns (rows, how many 4th downs could not be scored)."""
    if not any(_plays_file(paths, s).exists() for s in (season - 1, season)):
        frame = pl.DataFrame(schema=_SCORED_SCHEMA)
        return frame.join(coach_map, on=["game_id", "posteam"], how="left"), 0
    plays = data.with_state(data.load_plays([season - 1, season], paths))
    frame = data.fourth_frame(plays).filter(
        (pl.col("season") == season - 1) | ((pl.col("season") == season) & (pl.col("week") < week))
    )
    total = frame.height
    wanted = list(dict.fromkeys([*_STATE_COLUMNS, "wind", "temp", "game_id", "posteam", "choice"]))
    rows = frame.drop_nulls(_STATE_COLUMNS).select(wanted).to_dicts()
    states, kept = [], []
    for i, row in enumerate(rows):
        try:
            states.append(data.state_of_row(row))
            kept.append(i)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            continue  # a row the state rules refuse (a clock that disagrees with itself ...)
    try:
        best = [d.best for d in engine.fourth_downs(states, bootstrap=False)]
    except Exception:  # one state the engine refuses must not sink the rest
        best, ok = [], []
        for i, s in zip(kept, states, strict=True):
            try:
                best.append(engine.fourth_downs([s], bootstrap=False)[0].best)
                ok.append(i)
            except Exception:
                continue
        kept = ok
    scored = pl.DataFrame(
        {
            "win": ["this" if rows[i]["season"] == season else "last" for i in kept],
            "game_id": [rows[i]["game_id"] for i in kept],
            "posteam": [rows[i]["posteam"] for i in kept],
            "ydstogo": [int(rows[i]["ydstogo"]) for i in kept],
            "choice": [rows[i]["choice"] for i in kept],
            "best": best,
        },
        schema=_SCORED_SCHEMA,
    )
    scored = scored.join(coach_map, on=["game_id", "posteam"], how="left")
    return scored, total - scored.height


# --- the build --------------------------------------------------------------------------------
def _through_text(season: int, weeks: list[int]) -> str:
    if not weeks:
        return f"the {season - 1} season (no {season} games yet)"
    lo, hi = min(weeks), max(weeks)
    span = f"week {lo}" if lo == hi else f"weeks {lo}-{hi}"
    return f"{season} {span} and the {season - 1} season"


def build_context(
    season: int,
    week: int,
    *,
    paths: DataPaths,
    engine=None,
    model_version: str | None = None,
    fixes_path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Every team's "is this team good at this?" as of the start of `week` (module docstring)."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    fixes = coach_fixes(fixes_path)
    this = _with_coach(_read_window(paths, season, "this", week), fixes)
    last = _with_coach(_read_window(paths, season - 1, "last", None), fixes)
    plays = pl.concat([this, last], how="vertical_relaxed")
    weeks = sorted(this["week"].drop_nulls().unique().to_list())

    sched = _schedule(paths, (season - 1, season))
    sched = _apply_fixes(sched, fixes, "team")
    teams = sorted(sched.filter(pl.col("season") == season)["team"].drop_nulls().unique())
    if not teams:  # no schedule for this season: the teams seen in the plays
        teams = sorted(this["posteam"].drop_nulls().unique())

    # ---- the plain measures
    decided = (pl.col("down") == 4) & pl.col("play_type").is_in(
        ["run", "pass", "field_goal", "punt"]
    )
    went = pl.col("play_type").is_in(["run", "pass"])
    has_team = pl.col("posteam").is_not_null()
    go_rate = _Tally(plays, ["posteam"], decided & has_team, went)
    fourth_conv = _Tally(
        plays, ["posteam"], decided & went & has_team, pl.col("fourth_down_converted") == 1
    )
    short_conv = _Tally(
        plays,
        ["posteam"],
        pl.col("down").is_in([3, 4])
        & (pl.col("ydstogo") <= 2)
        & went
        & (pl.col("two_point_attempt").fill_null(0) != 1)
        & has_team,
        (pl.col("third_down_converted") == 1) | (pl.col("fourth_down_converted") == 1),
    )
    drives = (
        plays.filter(pl.col("down").is_not_null() & has_team)
        .group_by("win", "game_id", "fixed_drive", "posteam")
        .agg(
            pl.col("drive_inside20").max().alias("in20"),
            pl.col("fixed_drive_result").drop_nulls().first().alias("result"),
        )
    )
    red_zone = _Tally(drives, ["posteam"], pl.col("in20") == 1, pl.col("result") == "Touchdown")

    # ---- coaches
    coach_map = (
        plays.filter(has_team & pl.col("coach").is_not_null())
        .select("game_id", "posteam", "coach")
        .unique(["game_id", "posteam"], keep="first", maintain_order=True)
    )
    newest = (
        this.filter(has_team & pl.col("coach").is_not_null())
        .sort("week", "order_sequence", "play_id")
        .group_by("posteam", maintain_order=True)
        .agg(pl.col("coach").last())
    )
    team_coach: dict[str, str] = dict(newest.iter_rows())
    first_game = (
        sched.filter((pl.col("season") == season) & pl.col("coach").is_not_null())
        .sort("week")
        .group_by("team", maintain_order=True)
        .agg(pl.col("coach").first())
    )
    for team, coach in first_game.iter_rows():
        team_coach.setdefault(team, coach)
    last_newest = (
        last.filter(has_team & pl.col("coach").is_not_null())
        .sort("week", "order_sequence", "play_id")
        .group_by("posteam", maintain_order=True)
        .agg(pl.col("coach").last())
    )
    for team, coach in last_newest.iter_rows():
        team_coach.setdefault(team, coach)
    last_sched = (
        sched.filter((pl.col("season") == season - 1) & pl.col("coach").is_not_null())
        .sort("week")
        .group_by("team", maintain_order=True)
        .agg(pl.col("coach").last())
    )
    for team, coach in last_sched.iter_rows():
        team_coach.setdefault(team, coach)

    most = (
        last.filter(decided & has_team & pl.col("coach").is_not_null())
        .group_by("coach", "posteam")
        .len()
        .sort(["coach", "len", "posteam"], descending=[False, True, False])
        .group_by("coach", maintain_order=True)
        .agg(pl.col("posteam").first())
    )
    coach_last_team: dict[str, str] = dict(most.iter_rows())

    # ---- the bot's view of the coaches' 4th downs
    coach_go = by_dist = None
    skipped = scored_n = 0
    if engine is not None:
        scored, skipped = _scored_fourth_downs(season, week, paths, engine, coach_map)
        scored_n = scored.height
        scored = scored.with_columns(_band(pl.col("ydstogo"), BANDS_DIST, (3, 6)).alias("band"))
        spot = (pl.col("best") == "go") & pl.col("coach").is_not_null()
        went_it = pl.col("choice") == "go"
        coach_go = _Tally(scored, ["coach"], spot, went_it)
        by_dist = (
            _Tally(scored, ["coach", "band"], spot, went_it),
            _Tally(scored, ["band"], spot, went_it),
        )

    # ---- special teams
    kicks = (
        plays.filter(
            (pl.col("play_type") == "field_goal")
            & pl.col("field_goal_result").is_not_null()
            & pl.col("kicker_player_id").is_not_null()
        )
        .with_columns(
            pl.coalesce("kick_distance", pl.col("yardline_100") + FG_YARDS_BEHIND_LINE).alias(
                "dist"
            )
        )
        .with_columns(_band(pl.col("dist"), BANDS_KICK, (30, 40, 50)).alias("band"))
        .filter(pl.col("band").is_not_null())
    )
    made = pl.col("field_goal_result") == "made"
    kick_t = _Tally(kicks, ["kicker_player_id", "band"], pl.lit(True), made)
    kick_league = _Tally(kicks, ["band"], pl.lit(True), made)
    punts = _Punts(plays)
    kickers = _latest_by_team(
        this, ["field_goal", "extra_point"], "kicker_player_id", "kicker_player_name"
    )
    punters = _latest_by_team(this, ["punt"], "punter_player_id", "punter_player_name")
    for team, found in _latest_by_team(
        last, ["field_goal", "extra_point"], "kicker_player_id", "kicker_player_name"
    ).items():
        kickers.setdefault(team, found)
    for team, found in _latest_by_team(
        last, ["punt"], "punter_player_id", "punter_player_name"
    ).items():
        punters.setdefault(team, found)
    longs = _career_longs(paths, season, week, {pid for pid, _ in kickers.values()})

    def kicker_block(found) -> dict | None:
        if found is None:
            return None
        pid, name = found
        lng, lng_season = longs.get(pid, (None, None))
        return {
            "name": name,
            "long": lng,
            "long_season": lng_season,
            "bands": [
                {
                    "band": b,
                    "this": kick_t.stat("this", pid, b),
                    "last": kick_t.stat("last", pid, b),
                    "league_last": kick_league.stat("last", b),
                }
                for b in BANDS_KICK
            ],
        }

    def punter_block(found) -> dict | None:
        if found is None:
            return None
        pid, name = found
        return {
            "name": name,
            "this": punts.punter("this", pid),
            "last": punts.punter("last", pid),
            "league_this": punts.league("this"),
            "league_last": punts.league("last"),
        }

    # ---- one block per team
    out_teams = {}
    for team in teams:
        coach = team_coach.get(team)
        block_go = None
        block_dist: list[dict] = []
        if coach_go is not None and coach is not None:
            block_go = coach_go.measure(coach)
            block_dist = [
                {
                    "band": b,
                    "this": by_dist[0].stat("this", coach, b),
                    "last": by_dist[0].stat("last", coach, b),
                    "league_last": by_dist[1].stat("last", b),
                }
                for b in BANDS_DIST
            ]
        out_teams[team] = {
            "team": team,
            "coach": coach,
            "coach_last_team": coach_last_team.get(coach) if coach else None,
            "go_rate": go_rate.measure(team),
            "fourth_conv": fourth_conv.measure(team),
            "short_conv": short_conv.measure(team),
            "red_zone_td": red_zone.measure(team),
            "coach_go": block_go,
            "coach_go_by_distance": block_dist,
            "kicker": kicker_block(kickers.get(team)),
            "punter": punter_block(punters.get(team)),
        }

    league = {
        "go_rate": {"this": go_rate.league("this"), "last": go_rate.league("last")},
        "fourth_conv": {"this": fourth_conv.league("this"), "last": fourth_conv.league("last")},
        "short_conv": {"this": short_conv.league("this"), "last": short_conv.league("last")},
        "red_zone_td": {"this": red_zone.league("this"), "last": red_zone.league("last")},
        "coach_go": (
            None
            if coach_go is None
            else {"this": coach_go.league("this"), "last": coach_go.league("last")}
        ),
        "kicker_bands": [
            {
                "band": b,
                "this": kick_league.stat("this", b),
                "last": kick_league.stat("last", b),
            }
            for b in BANDS_KICK
        ],
        "punting": {"this": punts.league("this"), "last": punts.league("last")},
    }
    return {
        "schema": SCHEMA_VERSION,
        "season": season,
        "week": week,
        "this_season": season,
        "last_season": season - 1,
        "through_week": max(weeks) if weeks else None,
        "through": _through_text(season, weeks),
        "computed_at": now.isoformat(timespec="seconds"),
        "model_version": model_version,
        "coach_go_scored": scored_n,
        "coach_go_skipped": skipped,
        "teams": out_teams,
        "league": league,
    }


def team_context(ctx: dict, team: str) -> dict | None:
    """One team's answer shaped like the endpoint's `LiveContextResponse`; None for a team the
    context doesn't have."""
    block = (ctx.get("teams") or {}).get(str(team).strip().upper())
    if block is None:
        return None
    return {
        "season": ctx["season"],
        "week": ctx["week"],
        "this_season": ctx["this_season"],
        "last_season": ctx["last_season"],
        "through_week": ctx["through_week"],
        "through": ctx["through"],
        "team": block,
        "computed_at": ctx["computed_at"],
        "model_version": ctx["model_version"],
    }


# --- the cache --------------------------------------------------------------------------------
def _file_stamp(path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"mtime_ns": st.st_mtime_ns, "size": st.st_size}


def source_stamp(
    season: int, paths: DataPaths, fixes_path: Path | None, model_version: str | None
) -> dict:
    """What a cached context depends on: the curated plays of this and last season, the games
    (schedule and coaches) and the coach-fixes file (mtime in ns + size; None when missing), and
    the model bundle behind `coach_go`. Older seasons only feed a kicker's career long, and a
    finished season doesn't change."""
    return {
        "plays": _file_stamp(_plays_file(paths, season)),
        "plays_last": _file_stamp(_plays_file(paths, season - 1)),
        "games": _file_stamp(paths.curated / "games.parquet"),
        "fixes": _file_stamp(fixes_path or default_fixes_path()),
        "model_version": model_version,
    }


def _same_stamp(want: dict, have: object, check_model: bool) -> bool:
    if not isinstance(have, dict):
        return False
    keys = [k for k in want if check_model or k != "model_version"]
    return all(k in have and have[k] == want[k] for k in keys)


class ContextCache:
    """Contexts cached by (season, week): memory, then one JSON file per week in `folder`
    (`{season}-w{week:02d}.json`: schema, season, week, the source stamp and the context), then a
    build. A stored answer is used only while its stamp equals the current one; a missing,
    corrupt, wrong-schema or stale file is a miss. One build at a time per (season, week): a
    second caller waits for the first's answer. `folder=None` keeps memory only.

    `model_version` (optional on `get` / `warm`): the bundle the caller will score with. Give it
    and a context built with another bundle is rebuilt; leave it out and only the data files
    decide (the engine factory runs only when a build is needed, so the cache cannot ask it)."""

    def __init__(
        self,
        folder: Path | None,
        build: Callable = build_context,
        *,
        fixes_path: Path | None = None,
    ):
        self._folder = Path(folder) if folder is not None else None
        self._build = build
        self._fixes_path = fixes_path
        self._mem: dict[tuple[int, int], tuple[dict, dict]] = {}
        self._guard = threading.Lock()
        self._locks: dict[tuple[int, int], threading.Lock] = {}
        self._warming: set[tuple[int, int]] = set()
        self._threads: dict[tuple[int, int], threading.Thread] = {}

    # -- files
    def _file(self, season: int, week: int) -> Path | None:
        if self._folder is None:
            return None
        return self._folder / f"{season}-w{week:02d}.json"

    def _read_disk(self, season: int, week: int, want: dict, check_model: bool):
        path = self._file(season, week)
        if path is None:
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict):
            return None
        ctx = doc.get("context")
        if (
            doc.get("schema") != SCHEMA_VERSION
            or doc.get("season") != season
            or doc.get("week") != week
            or not _same_stamp(want, doc.get("stamp"), check_model)
            or not isinstance(ctx, dict)
            or ctx.get("schema") != SCHEMA_VERSION
            or ctx.get("season") != season
            or ctx.get("week") != week
            or not isinstance(ctx.get("teams"), dict)
        ):
            return None
        return doc["stamp"], ctx

    def _write_disk(self, season: int, week: int, stamp: dict, ctx: dict) -> None:
        path = self._file(season, week)
        if path is None:
            return
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        doc = {"schema": SCHEMA_VERSION, "season": season, "week": week, "stamp": stamp,
               "context": ctx}  # fmt: skip
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:  # a cache that can't be written is just a cache miss next time
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)

    # -- lookups
    def _lock(self, key: tuple[int, int]) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _cached(self, season: int, week: int, paths: DataPaths, model_version) -> dict | None:
        key = (season, week)
        check = model_version is not None
        want = source_stamp(season, paths, self._fixes_path, model_version)
        hit = self._mem.get(key)
        if hit is not None and _same_stamp(want, hit[0], check):
            return hit[1]
        found = self._read_disk(season, week, want, check)
        if found is None:
            return None
        self._mem[key] = found
        return found[1]

    def get(
        self,
        season: int,
        week: int,
        *,
        paths: DataPaths,
        engine_factory: Callable[[], tuple[object | None, str | None]],
        model_version: str | None = None,
    ) -> dict:
        ctx = self._cached(season, week, paths, model_version)
        if ctx is not None:
            return ctx
        key = (season, week)
        with self._lock(key):
            ctx = self._cached(season, week, paths, model_version)  # built while we waited
            if ctx is not None:
                return ctx
            engine, version = engine_factory()
            stamp = source_stamp(season, paths, self._fixes_path, version)
            extra = {} if self._fixes_path is None else {"fixes_path": self._fixes_path}
            ctx = self._build(
                season, week, paths=paths, engine=engine, model_version=version, **extra
            )
            self._mem[key] = (stamp, ctx)
            self._write_disk(season, week, stamp, ctx)
            return ctx

    def warm(
        self,
        season: int,
        week: int,
        *,
        paths: DataPaths,
        engine_factory: Callable[[], tuple[object | None, str | None]],
        model_version: str | None = None,
    ) -> None:
        """`get` in a daemon thread. A no-op when the answer is cached or being built; never
        raises."""
        key = (season, week)
        try:
            with self._guard:
                if key in self._warming or self._locks.get(key, threading.Lock()).locked():
                    return
                self._warming.add(key)
            if self._cached(season, week, paths, model_version) is not None:
                with self._guard:
                    self._warming.discard(key)
                return

            def run() -> None:
                try:
                    self.get(
                        season, week, paths=paths, engine_factory=engine_factory,
                        model_version=model_version,
                    )  # fmt: skip
                except Exception:
                    pass
                finally:
                    with self._guard:
                        self._warming.discard(key)

            thread = threading.Thread(target=run, daemon=True, name=f"live-context-{season}-{week}")
            self._threads[key] = thread
            thread.start()
        except Exception:
            with self._guard:
                self._warming.discard(key)
