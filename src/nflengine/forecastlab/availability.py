"""Availability and role (FL00, D158): will he play, how much, and who gets the work if he sits.

Pure functions on Polars frames and numpy arrays; `availability_runs.py` does the files, the
backtests and W&B. The contract (columns, files) is `forecastlab/schema.py`.

The pieces, in the order a run uses them:

- `prepare(...)`: the curated tables cleaned into what the model reads: team games, every
  player's appearances (his side's snap share, target share, carry share), the injury report,
  the weekly rosters collapsed to one row per player and week.
- `build_rows(prep, ...)`: the as-of universe. For a team's game in (season S, week W), every
  QB RB WR TE DL LB CB S who took a snap on his side in one of the team's last 3 games (with
  snap counts) before W, whose latest appearance was for the team, and whose latest roster row
  before W doesn't put him on another team or off the team (cut, traded, retired ...). Plus the
  labels when the game's snaps exist: `played` (at least one snap on his side), his snap share
  and his target / carry share.
- `add_features(prep, rows, view)`: what the view knows. `tuesday`: games before W, the report
  of the team's last game (`prev_*`), the roster as of last week. `saturday` adds week W's final
  report (`report_status`, `practice_status`) and week W's roster row, folded into one value
  (`week_roster`: ACT / RES / OFF, practice squad read as ACT). The Tuesday view never reads
  week W's roster row, except in week 1, where it decides membership only (last season's
  roster can't know the offseason). No view reads `INA` at all, in any week: it is folded into
  `ACT` (the phase file, "As built").
- `walk_forward(frame, view, settings, blocks)`: models fit only on earlier rows. The chance
  he plays (LightGBM binary + a Platt layer fitted on its own earlier walk-forward
  predictions), snap share if he plays (quantile LightGBM, range shifted on earlier misses),
  target and carry share if he plays (LightGBM, with teammates' expected absences as
  features).
- `inherit_table(...)`: for each player who might sit, his teammates' shares with him in and
  with him out.
- `score(...)` and `verdicts(...)`: the pre-registered ship rule (FL00 phase file).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl

from nflengine.forecastlab import schema

# --- vocabulary -----------------------------------------------------------------------------
OFF_GROUPS = ("QB", "RB", "WR", "TE")
DEF_GROUPS = ("DL", "LB", "CB", "S")
PGROUPS = OFF_GROUPS + DEF_GROUPS
TGT_GROUPS = ("WR", "TE", "RB")  # target share is forecast for these
CAR_GROUPS = ("RB", "QB")  # carry share
SNAP_POS = {
    "QB": "QB",
    "RB": "RB",
    "HB": "RB",
    "WR": "WR",
    "TE": "TE",
    "DE": "DL",
    "DT": "DL",
    "NT": "DL",
    "DL": "DL",
    "LB": "LB",
    "OLB": "LB",
    "ILB": "LB",
    "MLB": "LB",
    "CB": "CB",
    "DB": "CB",
    "S": "S",
    "FS": "S",
    "SS": "S",
    "SAF": "S",
}
# rosters_weekly.status -> what the model reads. INA (the game-day inactive list) is folded into
# ACT: it is never an input, in either view (README §2.1).
ROSTER_CAT = {
    "ACT": "ACT",
    "INA": "ACT",
    "DEV": "DEV",
    "RES": "RES",
    "PUP": "RES",
    "RSN": "RES",
    "RSR": "RES",
    "NWT": "OTHER",
    "SUS": "OTHER",
    "EXE": "OTHER",
    "E01": "OTHER",
    "E14": "OTHER",
}
GONE = ("CUT", "RET", "UFA", "RFA", "TRD", "TRC", "TRT", "UDF")
REPORT_ORD = {"none": 0, "listed": 1, "Questionable": 2, "Doubtful": 3, "Out": 4}
PRACTICE_ORD = {"none": 0, "full": 1, "limited": 2, "dnp": 3}
ROSTER_ORD = {"ACT": 0, "NONE": 1, "DEV": 2, "OTHER": 3, "RES": 4}
PGROUP_CODE = {g: i for i, g in enumerate(PGROUPS)}
LAST_N = 3  # the team's last N games define who could play

DEFAULTS: dict[str, Any] = {
    "train_from": 2014,
    "tune_seasons": "2017-2018",
    "report_seasons": "2019-2025",
    "at_risk_below": 0.85,
    "walk_from": 2015,  # first season with walk-forward predictions (calibration history)
    # tuned on 2017-2018 (phase file, "Tuning"): weekly refits, L1 share models, small share trees
    "refit": "week",  # week | season: how often the walk-forward refits
    "saturday_week_roster": True,  # the Saturday view reads this week's roster row, folded
    "calib_seasons": 2,  # Platt layer and range shifts: the last N seasons of own predictions
    "min_calib_rows": 2000,
    "base_prior": 20.0,  # baseline rates: shrinkage toward the coarser cell, in rows
    "threads": 6,
    "seed": 7,
    "play": {"num_leaves": 15, "learning_rate": 0.05, "rounds": 300, "min_data_in_leaf": 200},
    "snap": {"num_leaves": 31, "learning_rate": 0.05, "rounds": 300, "min_data_in_leaf": 200},
    "share": {
        "num_leaves": 15,
        "learning_rate": 0.05,
        "rounds": 150,
        "min_data_in_leaf": 200,
        "objective": "l1",
    },
    "inherit_top": 5,
}


def settings(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """`DEFAULTS` <- settings `forecastlab.availability` <- `overrides` (nested dicts merge)."""
    from nflengine.settings import get_config

    cfg = _merge(DEFAULTS, dict(get_config().forecastlab.get("availability", {}) or {}))
    return _merge(cfg, overrides or {})


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def parse_seasons(spec: str | int | Sequence[int]) -> list[int]:
    """`"2017-2018"` / `2019` / `[2019, 2020]` -> a sorted list of seasons."""
    if isinstance(spec, int):
        return [spec]
    if isinstance(spec, str):
        a, _, b = spec.partition("-")
        return list(range(int(a), int(b or a) + 1))
    return sorted(int(s) for s in spec)


def tkey(season: pl.Expr | int, week: pl.Expr | int) -> pl.Expr | int:
    """A continuous week index: `(season - 2000) * 22 + week` (playoff weeks fit below 22)."""
    if isinstance(season, int) and isinstance(week, int):
        return (season - 2000) * 22 + week
    return ((pl.lit(season) if isinstance(season, int) else season).cast(pl.Int32) - 2000) * 22 + (
        pl.lit(week) if isinstance(week, int) else week
    ).cast(pl.Int32)


def side_of(pgroup: pl.Expr) -> pl.Expr:
    return (
        pl.when(pgroup.is_in(DEF_GROUPS))
        .then(pl.lit("def"))
        .when(pgroup.is_in(OFF_GROUPS))
        .then(pl.lit("off"))
    )


def _clean_id(col: str) -> pl.Expr:
    c = pl.col(col).cast(pl.String).str.strip_chars()
    return pl.when(c == "").then(None).otherwise(c)


# --- inputs ---------------------------------------------------------------------------------
@dataclass
class Prepared:
    """The cleaned inputs (see `prepare`)."""

    tg: (
        pl.DataFrame
    )  # team games: game_id season week game_type team opponent home kickoff_utc completed t
    app: pl.DataFrame  # appearances: one row per (player, game) with a snap
    stats: pl.DataFrame  # rolling stats after each appearance on his side
    inj: pl.DataFrame  # season week player_id team rep prac
    ros: pl.DataFrame  # player_id t_r team_r status_r position_r (one row per player-week)
    snapped_games: set[str] = field(default_factory=set)  # games whose snap counts exist


def rush_counts(plays: pl.DataFrame) -> pl.DataFrame:
    """Designed runs per (game, team, rusher): `rush == 1` plays, no two-point tries (scrambles
    are dropbacks, kneels aren't `rush`; curated-data skill, P06). -> game_id team player_id
    carries team_carries."""
    if plays.height == 0:
        return pl.DataFrame(
            schema={
                "game_id": pl.String,
                "team": pl.String,
                "player_id": pl.String,
                "carries": pl.Int64,
                "team_carries": pl.Int64,
            }
        )
    r = plays.filter(
        (pl.col("rush").cast(pl.Float64) == 1.0)
        & (pl.col("two_point_attempt").cast(pl.Float64).fill_null(0.0) != 1.0)
        & pl.col("posteam").is_not_null()
    )
    team = r.group_by("game_id", "posteam").agg(pl.len().cast(pl.Int64).alias("team_carries"))
    own = (
        r.filter(pl.col("rusher_player_id").is_not_null())
        .group_by("game_id", "posteam", "rusher_player_id")
        .agg(pl.len().cast(pl.Int64).alias("carries"))
    )
    return (
        own.join(team, on=["game_id", "posteam"])
        .rename({"posteam": "team", "rusher_player_id": "player_id"})
        .select("game_id", "team", "player_id", "carries", "team_carries")
        .sort("game_id", "team", "player_id")
    )


def team_games(games: pl.DataFrame) -> pl.DataFrame:
    """Two rows per game (one per team), REG and playoffs."""
    g = games.select(
        "game_id",
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        "game_type",
        "home_team",
        "away_team",
        pl.col("kickoff_utc").cast(schema.UTC),
        pl.col("completed").fill_null(False),
    )
    home = g.select(
        pl.exclude("home_team", "away_team"),
        pl.col("home_team").alias("team"),
        pl.col("away_team").alias("opponent"),
        pl.lit(True).alias("home"),
    )
    away = g.select(
        pl.exclude("home_team", "away_team"),
        pl.col("away_team").alias("team"),
        pl.col("home_team").alias("opponent"),
        pl.lit(False).alias("home"),
    )
    return (
        pl.concat([home, away])
        .with_columns(tkey(pl.col("season"), pl.col("week")).alias("t"))
        .sort("t", "team")
    )


def prepare(
    games: pl.DataFrame,
    snaps: pl.DataFrame,
    injuries: pl.DataFrame,
    rosters: pl.DataFrame,
    player_games: pl.DataFrame,
    rushes: pl.DataFrame,
) -> Prepared:
    """Clean the curated tables (`rushes` = `rush_counts(plays)`)."""
    tg = team_games(games)
    app = appearances(snaps, player_games, rushes)
    stats = appearance_stats(app)
    inj = (
        injuries.select(
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            _clean_id("gsis_id").alias("player_id"),
            "team",
            report_cat(pl.col("report_status")).alias("rep"),
            practice_cat(pl.col("practice_status")).alias("prac"),
            pl.col("report_status").alias("report_raw"),
        )
        .filter(pl.col("player_id").is_not_null())
        .sort(
            "season",
            "week",
            "player_id",
            "team",
            pl.col("rep").replace_strict(REPORT_ORD, default=0),
            descending=[False, False, False, False, True],
        )
        .unique(["season", "week", "player_id", "team"], keep="first", maintain_order=True)
    )
    ros = collapse_rosters(rosters)
    snapped = set(app["game_id"].unique().to_list())
    return Prepared(tg=tg, app=app, stats=stats, inj=inj, ros=ros, snapped_games=snapped)


def appearances(
    snaps: pl.DataFrame, player_games: pl.DataFrame, rushes: pl.DataFrame
) -> pl.DataFrame:
    """One row per (player, game) with a snap count row: his side's snap share (defense for DL
    LB CB S, offense for QB RB WR TE), the offense / defense shares, target and carry share."""
    # a few duplicate rows since 2024 (curated skill): keep the one with most snaps
    s = (
        snaps.with_columns(
            _clean_id("gsis_id").alias("player_id"),
            pl.col("position")
            .cast(pl.String)
            .str.split("/")
            .list.first()
            .str.strip_chars()
            .str.to_uppercase()
            .alias("pos"),
            *[
                pl.col(c).cast(pl.Float64).fill_null(0.0)
                for c in ("offense_snaps", "defense_snaps", "offense_pct", "defense_pct")
            ],
        )
        .filter(pl.col("player_id").is_not_null())
        .sort(
            "player_id",
            "game_id",
            pl.col("offense_snaps") + pl.col("defense_snaps"),
            descending=[False, False, True],
        )
        .unique(["player_id", "game_id"], keep="first", maintain_order=True)
    )
    s = s.with_columns(pl.col("pos").replace_strict(SNAP_POS, default=None).alias("pgroup"))
    s = s.with_columns(
        pl.when(pl.col("pgroup").is_in(DEF_GROUPS))
        .then(pl.col("defense_pct"))
        .when(pl.col("pgroup").is_in(OFF_GROUPS))
        .then(pl.col("offense_pct"))
        .otherwise(pl.max_horizontal("offense_pct", "defense_pct"))
        .alias("side_pct"),
    )
    pg = (
        player_games.select(
            _clean_id("player_id").alias("player_id"),
            "game_id",
            pl.col("target_share").cast(pl.Float64),
        )
        .filter(pl.col("player_id").is_not_null())
        .unique(["player_id", "game_id"], keep="first")
    )
    ru = rushes.select(
        "game_id",
        "player_id",
        (pl.col("carries") / pl.col("team_carries")).cast(pl.Float64).alias("carry_share"),
    ).unique(["player_id", "game_id"], keep="first")
    app = (
        s.select(
            "player_id",
            "game_id",
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            "team",
            pl.col("player").alias("player_name"),
            pl.col("pos").alias("snap_pos"),
            "pgroup",
            "side_pct",
            "offense_pct",
            "defense_pct",
        )
        .with_columns(tkey(pl.col("season"), pl.col("week")).alias("t"))
        .join(pg, on=["player_id", "game_id"], how="left")
        .join(ru, on=["player_id", "game_id"], how="left")
        .with_columns(
            pl.col("target_share").fill_null(0.0),
            pl.col("carry_share").fill_null(0.0),
            (pl.col("side_pct") > 0).alias("played_side"),
        )
        .sort("player_id", "t", "game_id")
    )
    return app


def report_cat(col: pl.Expr) -> pl.Expr:
    """Out / Doubtful / Questionable as named; anything else on the report (null = practice
    only, Probable before 2016, Note) is `listed`. Rows not on the report get `none` later."""
    return (
        pl.when(col.is_in(["Out", "Doubtful", "Questionable"]))
        .then(col)
        .otherwise(pl.lit("listed"))
    )


def practice_cat(col: pl.Expr) -> pl.Expr:
    c = col.fill_null("")
    return (
        pl.when(c.str.contains("Did Not") | c.str.starts_with("Out"))
        .then(pl.lit("dnp"))
        .when(c.str.contains("Limited"))
        .then(pl.lit("limited"))
        .when(c.str.contains("Full"))
        .then(pl.lit("full"))
        .otherwise(pl.lit("none"))
    )


def collapse_rosters(rosters: pl.DataFrame) -> pl.DataFrame:
    """One row per (player, week): the team and status he is listed with. A week with two rows
    (a trade, a re-signing) keeps the one that puts him on a team (ACT / INA, then reserve,
    practice squad, other) before a departure row (cut, traded ...)."""
    from nflengine.curate.teams import team_expr

    r = rosters.select(
        _clean_id("gsis_id").alias("player_id"),
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        team_expr("team").alias("team_r"),
        pl.col("status").alias("status_r"),
        pl.col("position").alias("position_r"),
    ).filter(pl.col("player_id").is_not_null() & pl.col("week").is_not_null())
    prio = (
        pl.when(pl.col("status_r").is_in(["ACT", "INA"]))
        .then(0)
        .when(pl.col("status_r").replace_strict(ROSTER_CAT, default=None) == "RES")
        .then(1)
        .when(pl.col("status_r") == "DEV")
        .then(2)
        .when(pl.col("status_r").is_in(GONE) | pl.col("status_r").is_null())
        .then(4)
        .otherwise(3)
    )
    return (
        r.with_columns(tkey(pl.col("season"), pl.col("week")).alias("t_r"), prio.alias("_prio"))
        .sort("player_id", "t_r", "_prio", "team_r")
        .unique(["player_id", "t_r"], keep="first", maintain_order=True)
        .with_columns(
            # practice-squad weeks among his last 4 roster weeks (an elevated player shows ACT
            # in the week he was elevated; this keeps "he is a practice-squad player")
            (pl.col("status_r") == "DEV")
            .cast(pl.Float64)
            .fill_null(0.0)
            .rolling_sum(4, min_samples=1)
            .over("player_id")
            .alias("dev_n4"),
        )
        .select("player_id", "t_r", "team_r", "status_r", "position_r", "dev_n4")
        .sort("t_r", "player_id")
    )


def week_roster(prep: Prepared, rows: pl.DataFrame) -> pl.DataFrame:
    """Each row's own week's roster row as one category (`ACT`, `RES`, `OFF`), for the uses the
    phase file allows (the Saturday view; membership in week 1). INA (the game-day inactive
    list) and DEV (a practice-squad player, possibly elevated late on Saturday) both read
    `ACT`; cut, traded, on another team or no row read `OFF`; null when the team has no rows
    for that week at all (not published yet)."""
    wk = prep.ros.select("player_id", pl.col("t_r").alias("t"), "team_r", "status_r")
    have = (
        prep.ros.select(pl.col("t_r").alias("t"), pl.col("team_r").alias("team"))
        .unique()
        .with_columns(pl.lit(True).alias("_have"))
    )
    j = rows.select("player_id", "game_id", "t", "team").join(wk, on=["player_id", "t"], how="left")
    j = j.join(have, on=["t", "team"], how="left")
    c = pl.col("status_r").replace_strict(ROSTER_CAT, default="OTHER")
    cat = (
        pl.when(pl.col("_have").is_null())
        .then(None)
        .when(pl.col("team_r").is_null() | (pl.col("team_r") != pl.col("team")))
        .then(pl.lit("OFF"))
        .when(pl.col("status_r").is_in(GONE))
        .then(pl.lit("OFF"))
        .when(c == "RES")
        .then(pl.lit("RES"))
        .when(c.is_in(["ACT", "DEV"]))
        .then(pl.lit("ACT"))
        .otherwise(pl.lit("OFF"))
    )
    return j.select("player_id", "game_id", cat.alias("wk_roster"))


def appearance_stats(app: pl.DataFrame) -> pl.DataFrame:
    """Rolling usage after each game he played on his side (snaps > 0): the as-of join takes
    the last one strictly before a row's week."""
    p = app.filter(pl.col("played_side") & pl.col("pgroup").is_not_null()).sort(
        "player_id", "t", "game_id"
    )
    by_p = ["player_id"]
    by_ps = ["player_id", "season"]
    n_ps = pl.int_range(1, pl.len() + 1).over(by_ps)
    return (
        p.with_columns(
            pl.col("side_pct").alias("snap_l1"),
            pl.col("side_pct").rolling_mean(2, min_samples=1).over(by_p).alias("snap_l2"),
            pl.col("side_pct").rolling_mean(4, min_samples=1).over(by_p).alias("snap_l4"),
            pl.col("side_pct").shift(1).rolling_mean(3, min_samples=1).over(by_p).alias("_prev3"),
            pl.col("target_share").alias("tgt_l1"),
            pl.col("target_share").rolling_mean(4, min_samples=1).over(by_p).alias("tgt_l4"),
            pl.col("carry_share").alias("car_l1"),
            pl.col("carry_share").rolling_mean(4, min_samples=1).over(by_p).alias("car_l4"),
            n_ps.alias("games_season"),
            (pl.col("side_pct").cum_sum().over(by_ps) / n_ps).alias("snap_season"),
            (pl.col("target_share").cum_sum().over(by_ps) / n_ps).alias("tgt_season"),
            (pl.col("carry_share").cum_sum().over(by_ps) / n_ps).alias("car_season"),
            pl.int_range(1, pl.len() + 1).over(by_p).alias("games_career"),
        )
        .with_columns(
            ((pl.col("snap_l1") < 0.6 * pl.col("_prev3")) & (pl.col("_prev3") >= 0.4))
            .fill_null(False)
            .cast(pl.Float64)
            .alias("left_early"),
        )
        .select(
            "player_id",
            pl.col("t").alias("t_app"),
            pl.col("season").alias("season_app"),
            pl.col("team").alias("team_app"),
            pl.col("pgroup").alias("pgroup_app"),
            pl.col("snap_pos").alias("pos_app"),
            pl.col("player_name").alias("name_app"),
            "snap_l1",
            "snap_l2",
            "snap_l4",
            "tgt_l1",
            "tgt_l4",
            "car_l1",
            "car_l4",
            "games_season",
            "snap_season",
            "tgt_season",
            "car_season",
            "games_career",
            "left_early",
        )
        .sort("t_app", "player_id")
    )


def _asof_before(
    left: pl.DataFrame, right: pl.DataFrame, by: str | list[str], right_t: str
) -> pl.DataFrame:
    """For each left row, the latest right row with `right_t` strictly before the left's `t`."""
    lq = left.with_columns((pl.col("t") - 1).cast(pl.Int32).alias("_tq")).sort("_tq")
    rr = right.with_columns(pl.col(right_t).cast(pl.Int32)).sort(right_t)
    return lq.join_asof(
        rr, left_on="_tq", right_on=right_t, by=by, strategy="backward", check_sortedness=False
    ).drop("_tq")


# --- the universe and the labels -----------------------------------------------------------
KEY_COLS = ["season", "week", "game_id", "kickoff_utc", "team", "opponent", "home", "t"]


def build_rows(
    prep: Prepared,
    keys: pl.DataFrame | None = None,
    *,
    game_types: Sequence[str] | None = None,
) -> pl.DataFrame:
    """The as-of universe for each team-game in `keys` (`season`, `week`; default every week)
    and its labels where the game's snap counts exist.

    A player is in for (team T, week W) when he took a snap on his side in one of T's last
    `LAST_N` games before W that have snap counts, his latest appearance before W was for T,
    and his latest roster row before W (never week W's) doesn't place him off T (cut, traded,
    retired ...) or on another team's roster."""
    tg = prep.tg
    k = tg
    if keys is not None:
        k = k.join(
            keys.select(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)).unique(),
            on=["season", "week"],
        )
    if game_types is not None:
        k = k.filter(pl.col("game_type").is_in(list(game_types)))
    k = k.select(KEY_COLS)
    # the team's earlier games with snap counts, numbered per team
    done = (
        tg.filter(pl.col("completed") & pl.col("game_id").is_in(list(prep.snapped_games)))
        .sort("team", "t")
        .with_columns(pl.int_range(0, pl.len()).over("team").cast(pl.Int32).alias("gi"))
    )
    last = _asof_before(k, done.select("team", pl.col("t").alias("t_g"), "gi"), "team", "t_g")
    last = last.filter(pl.col("gi").is_not_null())
    prev = pl.concat(
        [
            last.select(*KEY_COLS, (pl.col("gi") - j).alias("gj"), pl.lit(j + 1).alias("lag"))
            for j in range(LAST_N)
        ]
    ).filter(pl.col("gj") >= 0)
    prev = prev.join(
        done.select(
            "team",
            pl.col("gi").alias("gj"),
            pl.col("game_id").alias("prev_game"),
            pl.col("week").alias("prev_week"),
            pl.col("season").alias("prev_season"),
        ),
        on=["team", "gj"],
    )
    # the team's last game, and the lags as columns (for the "missed" features)
    lags = prev.pivot(on="lag", index=["game_id", "team"], values="prev_game")
    lags = lags.rename(
        {str(j + 1): f"g_l{j + 1}" for j in range(LAST_N) if str(j + 1) in lags.columns}
    )
    for j in range(LAST_N):
        if f"g_l{j + 1}" not in lags.columns:
            lags = lags.with_columns(pl.lit(None, dtype=pl.String).alias(f"g_l{j + 1}"))
    l1 = prev.filter(pl.col("lag") == 1).select(
        "game_id",
        "team",
        pl.col("prev_week").alias("week_l1"),
        pl.col("prev_season").alias("season_l1"),
    )
    played = prep.app.filter(pl.col("played_side")).select(
        pl.col("game_id").alias("prev_game"), "team", "player_id"
    )
    cand = (
        prev.join(played, on=["prev_game", "team"])
        .select(*KEY_COLS, "player_id")
        .unique(["game_id", "team", "player_id"])
    )
    # his latest appearance before W (any team, any side) must be for this team
    lastapp = _asof_before(
        cand,
        prep.app.select("player_id", pl.col("t").alias("t_any"), pl.col("team").alias("team_any")),
        "player_id",
        "t_any",
    )
    cand = lastapp.filter(pl.col("team_any") == pl.col("team")).drop("t_any", "team_any")
    # his latest stats on his side (group, name, usage)
    cand = _asof_before(cand, prep.stats, "player_id", "t_app")
    cand = cand.rename({"pgroup_app": "pgroup"}).filter(pl.col("pgroup").is_in(PGROUPS))
    # the roster as of last week (never week W's row)
    cand = _asof_before(cand, prep.ros, "player_id", "t_r")
    on_other = (pl.col("team_r") != pl.col("team")) & ~pl.col("status_r").is_in(GONE)
    gone_here = (pl.col("team_r") == pl.col("team")) & pl.col("status_r").is_in(GONE)
    cand = cand.filter(~(on_other | gone_here).fill_null(False))
    # week 1: last season's roster says nothing about the offseason; membership comes from the
    # week's own roster row, which a Tuesday run before week 1 has (the cutdown is 10+ days
    # earlier). Membership only: a member reads `ACT`, whatever the row's status (D158 / the
    # phase file's "As built").
    w1 = cand.filter(pl.col("week") == 1)
    if w1.height:
        w1 = w1.join(week_roster(prep, w1), on=["player_id", "game_id"], how="left")
        w1 = w1.filter(pl.col("wk_roster").is_null() | (pl.col("wk_roster") != "OFF"))
        w1 = w1.with_columns(
            pl.when(pl.col("wk_roster").is_not_null())
            .then(pl.col("team"))
            .otherwise(pl.col("team_r"))
            .alias("team_r"),
            pl.when(pl.col("wk_roster").is_not_null())
            .then(pl.lit("ACT"))
            .otherwise(pl.col("status_r"))
            .alias("status_r"),
        ).drop("wk_roster")
        cand = pl.concat([cand.filter(pl.col("week") != 1), w1.select(cand.columns)])
    cand = (
        cand.join(lags, on=["game_id", "team"], how="left")
        .join(l1, on=["game_id", "team"], how="left")
        .with_columns(
            side_of(pl.col("pgroup")).alias("side"),
            pl.when(pl.col("team_r") == pl.col("team"))
            .then(pl.col("status_r").replace_strict(ROSTER_CAT, default="OTHER"))
            .otherwise(pl.lit("NONE"))
            .alias("roster_cat"),
            pl.coalesce("position_r", "pos_app").alias("position"),
            pl.col("name_app").alias("player"),
        )
    )
    return add_labels(prep, cand).sort("t", "game_id", "team", "player_id")


def add_labels(prep: Prepared, rows: pl.DataFrame) -> pl.DataFrame:
    """`played` (a snap on his side), `actual_snap`, `actual_tgt_share`, `actual_carry_share`;
    null while the game has no snap counts."""
    # private names: the rows may already carry forecast columns (`carry_share` is the
    # forecast in AVAIL_SCHEMA; a shared name made the grade read the forecast as the actual)
    a = prep.app.select(
        "player_id",
        "game_id",
        pl.col("offense_pct").alias("_a_off"),
        pl.col("defense_pct").alias("_a_def"),
        pl.col("target_share").alias("_a_tgt"),
        pl.col("carry_share").alias("_a_car"),
    )
    j = rows.drop(
        [
            c
            for c in ("played", "actual_snap", "actual_tgt_share", "actual_carry_share")
            if c in rows.columns
        ]
    ).join(a, on=["player_id", "game_id"], how="left")
    graded = pl.col("game_id").is_in(list(prep.snapped_games))
    snap = (
        pl.when(pl.col("side") == "def").then(pl.col("_a_def")).otherwise(pl.col("_a_off"))
    ).fill_null(0.0)
    played = snap > 0
    return j.with_columns(
        pl.when(graded).then(played).alias("played"),
        pl.when(graded & played).then(snap).alias("actual_snap"),
        pl.when(graded & played & pl.col("pgroup").is_in(TGT_GROUPS))
        .then(pl.col("_a_tgt").fill_null(0.0))
        .alias("actual_tgt_share"),
        pl.when(graded & played & pl.col("pgroup").is_in(CAR_GROUPS))
        .then(pl.col("_a_car").fill_null(0.0))
        .alias("actual_carry_share"),
    ).drop("_a_off", "_a_def", "_a_tgt", "_a_car")


# --- features --------------------------------------------------------------------------------
BASE_FEATURES = [
    "pgroup_code",
    "week",
    "snap_l1",
    "snap_l2",
    "snap_l4",
    "snap_season",
    "games_season",
    "games_career",
    "weeks_since",
    "left_early",
    "played_l1",
    "missed_l3",
    "team_snap_l1",
    "prev_rep",
    "prev_prac",
    "prev_out_n",
    "roster",
    "dev_n4",
    "grp_n",
    "grp_rank",
    "grp_snap_others",
]
SAT_FEATURES = ["rep", "prac", "wk_ros"]
WK_ORD = {"ACT": 0, "OFF": 1, "RES": 2}
SHARE_FEATURES = [
    "tgt_l1",
    "tgt_l4",
    "tgt_season",
    "car_l1",
    "car_l4",
    "car_season",
    "vac_tgt",
    "vac_car",
    "vac_snap",
]


def feature_list(view: str, kind: str) -> list[str]:
    """`kind`: play | snap | share."""
    f = list(BASE_FEATURES) + (SAT_FEATURES if view == "saturday" else [])
    if kind in ("snap", "share"):
        f += SHARE_FEATURES
    return f


def add_features(
    prep: Prepared, rows: pl.DataFrame, view: str, *, week_roster_on: bool = True
) -> pl.DataFrame:
    """What the view knows about each row (see the module docstring). Adds the model features,
    the baselines' inputs and the AVAIL_SCHEMA "what the view knew" columns.
    `week_roster_on` (Saturday only): read this week's roster row, folded (`week_roster`)."""
    if view not in schema.VIEWS:
        raise ValueError(f"unknown view {view!r}")
    r = rows.with_columns(
        # season-scoped usage only counts when his last game was this season
        pl.when(pl.col("season_app") == pl.col("season"))
        .then(pl.col("games_season"))
        .otherwise(0)
        .cast(pl.Int32)
        .alias("games_season"),
        *[
            pl.when(pl.col("season_app") == pl.col("season")).then(pl.col(c)).alias(c)
            for c in ("snap_season", "tgt_season", "car_season")
        ],
        (pl.col("t") - pl.col("t_app")).cast(pl.Float64).alias("weeks_since"),
    )
    # the team's last LAST_N games: did he play them (his side)?
    a = prep.app.filter(pl.col("played_side")).select("player_id", "game_id", "side_pct")
    for j in range(LAST_N):
        r = r.join(
            a.rename({"game_id": f"g_l{j + 1}", "side_pct": f"_s{j + 1}"}),
            on=["player_id", f"g_l{j + 1}"],
            how="left",
        )
    avail = [pl.col(f"g_l{j + 1}").is_not_null().cast(pl.Int32) for j in range(LAST_N)]
    playedj = [pl.col(f"_s{j + 1}").is_not_null().cast(pl.Int32) for j in range(LAST_N)]
    r = r.with_columns(
        pl.col("_s1").is_not_null().cast(pl.Float64).alias("played_l1"),
        (pl.sum_horizontal(avail) - pl.sum_horizontal(playedj)).cast(pl.Float64).alias("missed_l3"),
        pl.col("_s1").fill_null(0.0).alias("team_snap_l1"),
    ).drop([f"_s{j + 1}" for j in range(LAST_N)])
    # last week's report: the report of the team's last game, this season only
    inj = prep.inj.select("season", "week", "player_id", "team", "rep", "prac")
    r = r.join(
        inj.rename(
            {"season": "season_l1", "week": "week_l1", "rep": "_prev_rep", "prac": "_prev_prac"}
        ),
        on=["season_l1", "week_l1", "player_id", "team"],
        how="left",
    )
    same = pl.col("season_l1") == pl.col("season")
    r = r.with_columns(
        pl.when(same)
        .then(pl.col("_prev_rep"))
        .otherwise(None)
        .fill_null("none")
        .alias("prev_status"),
        pl.when(same).then(pl.col("_prev_prac")).otherwise(None).fill_null("none").alias("_pp"),
    ).drop("_prev_rep", "_prev_prac")
    # how many of the team's last LAST_N reports (this season) had him Out / Doubtful
    outs = (
        inj.filter(pl.col("rep").is_in(["Out", "Doubtful"]))
        .join(prep.tg.select("game_id", "team", "season", "week"), on=["season", "week", "team"])
        .select(pl.col("game_id").alias("_g"), "team", "player_id", "season")
        .with_columns(pl.lit(1).alias("_o"))
    )
    on = pl.lit(0)
    for j in range(LAST_N):
        r = r.join(
            outs.rename({"_g": f"g_l{j + 1}", "_o": f"_o{j + 1}"}),
            on=[f"g_l{j + 1}", "team", "player_id", "season"],
            how="left",
        )
        on = on + pl.col(f"_o{j + 1}").fill_null(0)
    r = r.with_columns(on.cast(pl.Float64).alias("prev_out_n")).drop(
        [f"_o{j + 1}" for j in range(LAST_N)]
    )
    if view == "saturday":
        r = r.join(
            inj.rename({"rep": "_rep", "prac": "_prac"}),
            on=["season", "week", "player_id", "team"],
            how="left",
        ).with_columns(
            pl.col("_rep").fill_null("none").alias("_rs"),
            pl.col("_prac").fill_null("none").alias("_ps"),
        )
        if week_roster_on:
            r = r.join(week_roster(prep, r), on=["player_id", "game_id"], how="left")
        else:
            r = r.with_columns(pl.lit(None, dtype=pl.String).alias("wk_roster"))
    else:
        r = r.with_columns(
            pl.lit(None, dtype=pl.String).alias("_rs"),
            pl.lit(None, dtype=pl.String).alias("_ps"),
            pl.lit(None, dtype=pl.String).alias("wk_roster"),
        )
    r = r.with_columns(
        pl.col("pgroup").replace_strict(PGROUP_CODE, return_dtype=pl.Float64).alias("pgroup_code"),
        pl.col("prev_status").replace_strict(REPORT_ORD, return_dtype=pl.Float64).alias("prev_rep"),
        pl.col("_pp").replace_strict(PRACTICE_ORD, return_dtype=pl.Float64).alias("prev_prac"),
        pl.col("roster_cat")
        .replace_strict(ROSTER_ORD, default=1, return_dtype=pl.Float64)
        .alias("roster"),
        pl.col("_rs")
        .replace_strict(REPORT_ORD, default=None, return_dtype=pl.Float64)
        .alias("rep"),
        pl.col("_ps")
        .replace_strict(PRACTICE_ORD, default=None, return_dtype=pl.Float64)
        .alias("prac"),
        # the AVAIL_SCHEMA columns: what the view knew, in words
        pl.when(pl.col("_rs").is_in(["Out", "Doubtful", "Questionable"]))
        .then(pl.col("_rs"))
        .when(pl.col("_rs") == "listed")
        .then(pl.lit("Listed"))
        .alias("report_status"),
        pl.when(pl.col("_ps").is_in(["full", "limited", "dnp"]))
        .then(pl.col("_ps"))
        .alias("practice_status"),
        pl.when(pl.col("prev_status").is_in(["Out", "Doubtful", "Questionable"]))
        .then(pl.col("prev_status"))
        .when(pl.col("prev_status") == "listed")
        .then(pl.lit("Listed"))
        .alias("prev_status"),
        pl.col("wk_roster")
        .replace_strict(WK_ORD, default=None, return_dtype=pl.Float64)
        .alias("wk_ros"),
        # the latest roster status the view could see (Saturday: this week's, folded)
        pl.coalesce("wk_roster", "roster_cat").alias("roster_status"),
    ).drop("_rs", "_ps", "_pp")
    # group context: how many of his group are in the universe, his rank by recent snaps
    grp = ["game_id", "team", "pgroup"]
    r = r.sort("game_id", "team", "pgroup", "player_id").with_columns(
        pl.len().over(grp).cast(pl.Float64).alias("grp_n"),
        pl.col("snap_l2")
        .rank("ordinal", descending=True)
        .over(grp)
        .cast(pl.Float64)
        .alias("grp_rank"),
        (pl.col("snap_l2").sum().over(grp) - pl.col("snap_l2")).alias("grp_snap_others"),
        # the baselines' inputs (FL00 ship rule)
        pl.col("snap_l2").alias("base_snap"),
        pl.when(pl.col("pgroup").is_in(TGT_GROUPS)).then(pl.col("tgt_l4")).alias("base_tgt_share"),
        pl.when(pl.col("pgroup").is_in(CAR_GROUPS))
        .then(pl.col("car_l4"))
        .alias("base_carry_share"),
    )
    return r.sort("t", "game_id", "team", "player_id")


def feature_matrix(frame: pl.DataFrame, cols: Sequence[str]) -> np.ndarray:
    exprs = [
        (pl.col("week").cast(pl.Float64) if c == "week" else pl.col(c).cast(pl.Float64)).alias(c)
        for c in cols
    ]
    return frame.select(exprs).to_numpy().astype(np.float64)


def add_vacated(frame: pl.DataFrame, absent: pl.Expr) -> pl.DataFrame:
    """Teammates' expected absences, as usage left open: per team-game, the sum over the other
    players of (chance he sits) x (his baseline share). `absent`: 1 - played (training rows,
    realized) or 1 - p_play (forecast rows). Targets: WR TE RB teammates; carries: RB QB;
    snaps: the same position group."""
    f = frame.with_columns(absent.cast(pl.Float64).fill_null(0.0).alias("_w"))
    f = f.with_columns(
        (pl.col("_w") * pl.col("base_tgt_share").fill_null(0.0)).alias("_vt"),
        (pl.col("_w") * pl.col("base_carry_share").fill_null(0.0)).alias("_vc"),
        (pl.col("_w") * pl.col("base_snap").fill_null(0.0)).alias("_vs"),
    )
    tg = ["game_id", "team"]
    # sorted before summing: float sums follow row order (model-experiment skill, P08 lesson)
    f = f.sort("game_id", "team", "pgroup", "player_id")
    is_t = pl.col("pgroup").is_in(TGT_GROUPS)
    is_c = pl.col("pgroup").is_in(CAR_GROUPS)
    vt = pl.when(is_t).then(pl.col("_vt")).otherwise(0.0)
    vc = pl.when(is_c).then(pl.col("_vc")).otherwise(0.0)
    return f.with_columns(
        (vt.sum().over(tg) - vt).alias("vac_tgt"),
        (vc.sum().over(tg) - vc).alias("vac_car"),
        (pl.col("_vs").sum().over([*tg, "pgroup"]) - pl.col("_vs")).alias("vac_snap"),
    ).drop("_w", "_vt", "_vc", "_vs")


# --- the baselines ---------------------------------------------------------------------------
BASE_CELLS = {
    "tuesday": ("prev_status_c", "prev_prac_c", "roster_c"),
    "saturday": ("report_c", "practice_c", "roster_c"),
}


def _base_cols(frame: pl.DataFrame) -> pl.DataFrame:
    """The baseline's cells read exactly what the model reads: `roster_status` is last week's
    roster category on Tuesday and this week's folded value on Saturday (when on)."""
    return frame.with_columns(
        pl.col("roster_status").fill_null("NONE").alias("roster_c"),
        pl.col("prev_status").fill_null("none").alias("prev_status_c"),
        pl.col("prev_prac").cast(pl.Int32).cast(pl.String).alias("prev_prac_c"),
        pl.col("report_status").fill_null("none").alias("report_c"),
        pl.col("practice_status").fill_null("none").alias("practice_c"),
    )


def base_rates(train: pl.DataFrame, view: str, prior: float = 20.0) -> dict[str, Any]:
    """The historical play rate for what the view knows (its status, practice line and roster
    status) by position group, from labelled rows (`train`: earlier seasons only). Each cell is
    shrunk toward its group x status cell, which is shrunk toward the group's rate."""
    c1, c2, c3 = BASE_CELLS[view]
    d = _base_cols(train.filter(pl.col("played").is_not_null())).with_columns(
        pl.col("played").cast(pl.Float64).alias("_y")
    )
    g = d.group_by("pgroup").agg(pl.col("_y").sum().alias("k"), pl.len().alias("n"))
    s = d.group_by("pgroup", c1).agg(pl.col("_y").sum().alias("k1"), pl.len().alias("n1"))
    f = d.group_by("pgroup", c1, c2, c3).agg(pl.col("_y").sum().alias("k2"), pl.len().alias("n2"))
    return {
        "view": view,
        "prior": prior,
        "group": g,
        "status": s,
        "full": f,
        "all": float(d["_y"].mean()) if d.height else 0.9,
    }


def apply_base(frame: pl.DataFrame, rates: dict[str, Any]) -> pl.Series:
    c1, c2, c3 = BASE_CELLS[rates["view"]]
    m = rates["prior"]
    f = _base_cols(frame).select("pgroup", c1, c2, c3).with_row_index("_i")
    f = (
        f.join(rates["group"], on="pgroup", how="left")
        .join(rates["status"], on=["pgroup", c1], how="left")
        .join(rates["full"], on=["pgroup", c1, c2, c3], how="left")
        .sort("_i")
    )
    p0 = (pl.col("k").fill_null(0) + m * rates["all"]) / (pl.col("n").fill_null(0) + m)
    p1 = (pl.col("k1").fill_null(0) + m * pl.col("_p0")) / (pl.col("n1").fill_null(0) + m)
    p2 = (pl.col("k2").fill_null(0) + m * pl.col("_p1")) / (pl.col("n2").fill_null(0) + m)
    out = f.with_columns(p0.alias("_p0")).with_columns(p1.alias("_p1")).with_columns(p2.alias("p"))
    return out["p"].cast(pl.Float64)


# --- models ------------------------------------------------------------------------------------
def _lgb_params(part: dict[str, Any], objective: str, cfg: dict[str, Any], alpha=None) -> dict:
    p = {
        "objective": objective,
        "num_leaves": int(part["num_leaves"]),
        "learning_rate": float(part["learning_rate"]),
        "min_data_in_leaf": int(part["min_data_in_leaf"]),
        "feature_fraction": float(part.get("feature_fraction", 0.9)),
        "lambda_l2": float(part.get("lambda_l2", 1.0)),
        "num_threads": min(int(cfg.get("threads", 6)), 6),
        "seed": int(cfg.get("seed", 7)),
        "deterministic": True,
        "force_col_wise": True,
        "verbose": -1,
    }
    if alpha is not None:
        p["alpha"] = float(alpha)
    return p


def fit_booster(X: np.ndarray, y: np.ndarray, part: dict, objective: str, cfg: dict, alpha=None):
    import lightgbm as lgb

    ds = lgb.Dataset(
        X,
        label=y,
        categorical_feature=[0],
        free_raw_data=True,
        params={"verbose": -1, "max_bin": 255},
    )
    return lgb.train(
        _lgb_params(part, objective, cfg, alpha), ds, num_boost_round=int(part["rounds"])
    )


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def fit_platt(p_raw: np.ndarray, y: np.ndarray, min_rows: int = 2000) -> tuple[float, float]:
    """Platt scaling `sigmoid(a * logit(p) + b)` by Newton's method; identity (1, 0) in the
    burn-in (fewer than `min_rows` rows)."""
    ok = np.isfinite(p_raw) & np.isfinite(y)
    if ok.sum() < min_rows:
        return 1.0, 0.0
    x, t = _logit(p_raw[ok]), y[ok].astype(np.float64)
    w = np.array([1.0, 0.0])
    X = np.column_stack([x, np.ones_like(x)])
    for _ in range(50):
        z = X @ w
        q = 1 / (1 + np.exp(-z))
        g = X.T @ (q - t)
        h = (X * (q * (1 - q))[:, None]).T @ X + 1e-9 * np.eye(2)
        step = np.linalg.solve(h, g)
        w = w - step
        if np.abs(step).max() < 1e-10:
            break
    return float(w[0]), float(w[1])


def apply_platt(p_raw: np.ndarray, ab: tuple[float, float]) -> np.ndarray:
    a, b = ab
    return 1 / (1 + np.exp(-(a * _logit(p_raw) + b)))


def fit_shifts(lo: np.ndarray, hi: np.ndarray, y: np.ndarray, min_rows: int = 2000) -> dict:
    """Range shifts from earlier misses (asymmetric conformal): each end moves so that 10% of
    earlier outcomes fell below the low end and 10% above the high end."""
    ok = np.isfinite(lo) & np.isfinite(hi) & np.isfinite(y)
    if ok.sum() < min_rows:
        return {"lo": 0.0, "hi": 0.0, "n": int(ok.sum())}
    # shift s_lo: smallest s with mean(y < lo - s) <= 0.1  -> s = q90(lo - y)
    return {
        "lo": float(np.quantile(lo[ok] - y[ok], 0.9)),
        "hi": float(np.quantile(y[ok] - hi[ok], 0.9)),
        "n": int(ok.sum()),
    }


@dataclass
class ViewModels:
    """One view's fitted models at one key (the live fit; the walk-forward keeps none)."""

    view: str
    play: Any
    snap: dict[float, Any]
    tgt: Any
    car: Any
    platt: tuple[float, float]
    shifts: dict[str, float]
    rates: dict[str, Any]
    cfg: dict[str, Any]
    trained_through: str | None = None

    def p_raw(self, frame: pl.DataFrame) -> np.ndarray:
        return self.play.predict(feature_matrix(frame, feature_list(self.view, "play")))

    def p_play(self, frame: pl.DataFrame) -> np.ndarray:
        return apply_platt(self.p_raw(frame), self.platt)

    def shares(self, frame: pl.DataFrame) -> dict[str, np.ndarray]:
        """Snap P10 / P50 / P90 (shifted), target and carry share, for rows that already carry
        `vac_*` columns."""
        X = feature_matrix(frame, feature_list(self.view, "snap"))
        q = {a: self.snap[a].predict(X) for a in (0.1, 0.5)}
        q[0.9] = 1.0 - self.snap[0.9].predict(X)  # reflected (see `fit_view`)
        p10 = np.clip(q[0.1] - self.shifts.get("lo", 0.0), 0.0, 1.0)
        p90 = np.clip(q[0.9] + self.shifts.get("hi", 0.0), 0.0, 1.0)
        p50 = np.clip(q[0.5], 0.0, 1.0)
        p10, p90 = np.minimum(p10, p50), np.maximum(p90, p50)
        return {
            "snap_p10": p10,
            "snap_p50": p50,
            "snap_p90": p90,
            "snap_raw_p10": q[0.1],
            "snap_raw_p90": q[0.9],
            "tgt_share": np.clip(self.tgt.predict(X), 0.0, 1.0),
            "carry_share": np.clip(self.car.predict(X), 0.0, 1.0),
        }


def fit_view(
    train: pl.DataFrame,
    view: str,
    cfg: dict[str, Any],
    *,
    platt: tuple[float, float] = (1.0, 0.0),
    shifts: dict[str, float] | None = None,
    rates_train: pl.DataFrame | None = None,
) -> ViewModels:
    """Fit one view's models on labelled `train` rows (with features). Share and snap models
    see only players who played, with teammates' realized absences as `vac_*`."""
    lab = train.filter(pl.col("played").is_not_null())
    if lab.height == 0:
        raise ValueError("no labelled rows to fit on")
    yp = lab["played"].cast(pl.Float64).to_numpy()
    play = fit_booster(
        feature_matrix(lab, feature_list(view, "play")), yp, cfg["play"], "binary", cfg
    )
    pl_rows = add_vacated(lab, 1.0 - pl.col("played").cast(pl.Float64)).filter(pl.col("played"))
    Xs = feature_matrix(pl_rows, feature_list(view, "snap"))
    ys = pl_rows["actual_snap"].to_numpy()
    # P90 is fit as 1 - P10 of (1 - share): a quantile model started at the top of the scale
    # stays there when more than 10% of outcomes sit on it (13% of snap shares are exactly 1.0;
    # LightGBM's 0.9 model then predicted 1.0 for every row in the probe)
    snap = {
        0.1: fit_booster(Xs, ys, cfg["snap"], "quantile", cfg, alpha=0.1),
        0.5: fit_booster(Xs, ys, cfg["snap"], "quantile", cfg, alpha=0.5),
        0.9: fit_booster(Xs, 1.0 - ys, cfg["snap"], "quantile", cfg, alpha=0.1),
    }
    obj = cfg["share"].get("objective", "l2")
    obj = {"l1": "l1", "l2": "regression", "huber": "huber"}.get(obj, obj)
    tr = pl_rows.filter(pl.col("pgroup").is_in(TGT_GROUPS))
    tgt = fit_booster(
        feature_matrix(tr, feature_list(view, "share")),
        tr["actual_tgt_share"].to_numpy(),
        cfg["share"],
        obj,
        cfg,
    )
    cr = pl_rows.filter(pl.col("pgroup").is_in(CAR_GROUPS))
    car = fit_booster(
        feature_matrix(cr, feature_list(view, "share")),
        cr["actual_carry_share"].to_numpy(),
        cfg["share"],
        obj,
        cfg,
    )
    rt = rates_train if rates_train is not None else lab
    rates = base_rates(rt, view, float(cfg.get("base_prior", 20.0)))
    last = lab.select(
        pl.col("season").max().alias("s"),
        pl.col("week").filter(pl.col("season") == pl.col("season").max()).max().alias("w"),
    ).row(0)
    return ViewModels(
        view=view,
        play=play,
        snap=snap,
        tgt=tgt,
        car=car,
        platt=platt,
        shifts=shifts or {"lo": 0.0, "hi": 0.0},
        rates=rates,
        cfg=cfg,
        trained_through=f"{last[0]}-w{last[1]:02d}",
    )


def predict_view(m: ViewModels, rows: pl.DataFrame, *, at_risk_below: float) -> pl.DataFrame:
    """Every output for `rows` (features added): p_play, the baseline, the shares with
    teammates' expected absences (1 - p_play), exp_snap, at_risk."""
    if rows.height == 0:
        return rows
    p_raw = m.p_raw(rows)
    p = apply_platt(p_raw, m.platt)
    f = rows.with_columns(
        pl.Series("p_raw", p_raw),
        pl.Series("p_play", p),
        apply_base(rows, m.rates).alias("base_p_play"),
    )
    f = add_vacated(f, 1.0 - pl.col("p_play"))
    sh = m.shares(f)
    f = f.with_columns(*[pl.Series(k, v) for k, v in sh.items()])
    return finish(f, at_risk_below)


def finish(f: pl.DataFrame, at_risk_below: float) -> pl.DataFrame:
    """Null the shares that don't apply to a group; exp_snap; at_risk."""
    return f.with_columns(
        pl.when(pl.col("pgroup").is_in(TGT_GROUPS)).then(pl.col("tgt_share")).alias("tgt_share"),
        pl.when(pl.col("pgroup").is_in(CAR_GROUPS))
        .then(pl.col("carry_share"))
        .alias("carry_share"),
        (pl.col("p_play") * pl.col("snap_p50")).alias("exp_snap"),
        (
            (pl.col("p_play") < at_risk_below)
            | pl.col("report_status").is_in(["Out", "Doubtful", "Questionable"]).fill_null(False)
        ).alias("at_risk"),
    )


# --- the walk-forward --------------------------------------------------------------------------
def calibrators(
    hist: pl.DataFrame | None, season: int, before_t: int, cfg: dict[str, Any]
) -> tuple[tuple[float, float], dict[str, float]]:
    """The Platt layer and the range shifts for rows of `season`, from earlier walk-forward
    predictions only: the last `calib_seasons` seasons before `season` plus its weeks before
    `before_t`. Identity / no shift in the burn-in."""
    if hist is None or hist.height == 0:
        return (1.0, 0.0), {"lo": 0.0, "hi": 0.0, "n": 0}
    h = hist.filter(
        (pl.col("season") >= season - int(cfg["calib_seasons"]))
        & (pl.col("t") < before_t)
        & pl.col("played").is_not_null()
    )
    mr = int(cfg["min_calib_rows"])
    platt = fit_platt(h["p_raw"].to_numpy(), h["played"].cast(pl.Float64).to_numpy(), mr)
    hp = h.filter(pl.col("played"))
    shifts = fit_shifts(
        hp["snap_raw_p10"].to_numpy(),
        hp["snap_raw_p90"].to_numpy(),
        hp["actual_snap"].to_numpy(),
        mr,
    )
    return platt, shifts


def blocks_for(frame: pl.DataFrame, seasons: Sequence[int], refit: str) -> list[tuple[int, int]]:
    """The walk-forward's blocks as (first t, last t): one per season or one per week."""
    ks = (
        frame.filter(pl.col("season").is_in(list(seasons))).select("season", "t").unique().sort("t")
    )
    if refit == "week":
        return [(t, t) for t in ks["t"].to_list()]
    g = ks.group_by("season").agg(pl.col("t").min().alias("a"), pl.col("t").max().alias("b"))
    return [(int(a), int(b)) for a, b in g.sort("season").select("a", "b").iter_rows()]


def walk_forward(
    frame: pl.DataFrame,
    view: str,
    cfg: dict[str, Any],
    seasons: Sequence[int],
    *,
    on_block: Callable[[pl.DataFrame], None] | None = None,
    on_model: Callable[[ViewModels], None] | None = None,
    log: Callable[..., None] = lambda *a, **k: None,
) -> pl.DataFrame:
    """Predict every row of `seasons` from models fit only on rows before its block (a season or
    a week, settings `refit`). The Platt layer and the range shifts come from this run's own
    earlier predictions (the last `calib_seasons` seasons, never the block itself); the baseline
    rates from earlier seasons only. Returns the predicted rows (+ `p_raw`, raw quantiles)."""
    train_from = int(cfg["train_from"])
    frame = frame.filter(pl.col("season") >= train_from)
    out: list[pl.DataFrame] = []
    hist: pl.DataFrame | None = None
    for a, b in blocks_for(frame, seasons, str(cfg.get("refit", "season"))):
        test = frame.filter((pl.col("t") >= a) & (pl.col("t") <= b))
        train = frame.filter((pl.col("t") < a) & pl.col("played").is_not_null())
        if train.height == 0 or test.height == 0:
            continue
        season = int(test["season"].min())
        platt, shifts = calibrators(hist, season, a, cfg)
        m = fit_view(
            train,
            view,
            cfg,
            platt=platt,
            shifts=shifts,
            rates_train=train.filter(pl.col("season") < season),
        )
        pred = predict_view(m, test, at_risk_below=float(cfg["at_risk_below"])).with_columns(
            pl.lit(m.trained_through).alias("trained_through")
        )
        out.append(pred)
        hist = pred if hist is None else pl.concat([hist, pred], how="diagonal_relaxed")
        log(f"  {view} block t={a}-{b}: train {train.height:,} rows -> {test.height:,} rows")
        if on_model is not None:
            on_model(m)
        if on_block is not None:
            on_block(pred)
    return pl.concat(out, how="diagonal_relaxed") if out else frame.clear()


# --- who inherits ----------------------------------------------------------------------------
INHERIT_KINDS = {
    "targets": ("tgt_share", TGT_GROUPS, "base_tgt_share", "vac_tgt"),
    "carries": ("carry_share", CAR_GROUPS, "base_carry_share", "vac_car"),
    "snaps": ("snap_p50", None, "base_snap", "vac_snap"),
}


def inherit_table(m: ViewModels, pred: pl.DataFrame, top: int = 5) -> pl.DataFrame:
    """For each at-risk player X and each kind, his teammates' forecast share with X in (his
    absence weight 0) and with X out (weight 1), everything else as forecast. Keeps the
    teammates who gain, ranked (1 = gains most)."""
    risk = pred.filter(pl.col("at_risk"))
    if risk.height == 0 or pred.height == 0:
        return pl.DataFrame(schema=schema.INHERIT_SCHEMA)
    out = []
    for kind, (col, groups, base, vac) in INHERIT_KINDS.items():
        xs = risk if groups is None else risk.filter(pl.col("pgroup").is_in(groups))
        xs = xs.filter(pl.col(base).fill_null(0.0) > 0)
        if xs.height == 0:
            continue
        x = xs.select(
            "game_id",
            "team",
            pl.col("player_id").alias("out_player_id"),
            pl.col("player").alias("out_player"),
            pl.col("position").alias("out_position"),
            pl.col("pgroup").alias("_xg"),
            pl.col("p_play").alias("out_p_play"),
            pl.col(col).alias("out_share"),
            pl.col(base).fill_null(0.0).alias("_xb"),
        )
        mates = pred if groups is None else pred.filter(pl.col("pgroup").is_in(groups))
        pairs = x.join(mates, on=["game_id", "team"]).filter(
            pl.col("player_id") != pl.col("out_player_id")
        )
        if groups is None:
            pairs = pairs.filter(pl.col("pgroup") == pl.col("_xg"))
        if pairs.height == 0:
            continue
        # X's current contribution to the mate's vac is (1 - p_X) * base_X
        cur = (1.0 - pl.col("out_p_play")) * pl.col("_xb")
        both = pl.concat(
            [
                pairs.with_columns(
                    (pl.col(vac) - cur).clip(0.0).alias(vac), pl.lit("in").alias("_s")
                ),
                pairs.with_columns(
                    (pl.col(vac) - cur + pl.col("_xb")).alias(vac), pl.lit("out").alias("_s")
                ),
            ]
        )
        sh = m.shares(both)
        both = both.with_columns(pl.Series("_v", sh[col] if col in sh else sh["snap_p50"]))
        keys = ["game_id", "team", "out_player_id", "player_id"]
        wide = (
            both.filter(pl.col("_s") == "in")
            .select(*keys, pl.col("_v").alias("share_if_in"))
            .join(
                both.filter(pl.col("_s") == "out").select(
                    *keys, pl.col("_v").alias("share_if_out")
                ),
                on=keys,
            )
        )
        info = pairs.select(
            "season",
            "week",
            "game_id",
            "team",
            "out_player_id",
            "out_player",
            "out_position",
            "out_p_play",
            "out_share",
            "player_id",
            "player",
            "position",
        )
        r = (
            info.join(wide, on=keys)
            .with_columns(
                (pl.col("share_if_out") - pl.col("share_if_in")).alias("delta"),
                pl.lit(kind).alias("kind"),
            )
            .filter(pl.col("delta") > 1e-4)
            .sort("out_player_id", "delta", "player_id", descending=[False, True, False])
            .with_columns(
                pl.int_range(1, pl.len() + 1)
                .over("game_id", "out_player_id")
                .cast(pl.Int32)
                .alias("rank")
            )
            .filter(pl.col("rank") <= top)
        )
        out.append(r)
    if not out:
        return pl.DataFrame(schema=schema.INHERIT_SCHEMA)
    res = pl.concat(out, how="diagonal_relaxed")
    return res.sort("game_id", "team", "kind", "out_player_id", "rank")


# --- scores and the ship rule ----------------------------------------------------------------
def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2)) if len(p) else float("nan")


def ece_deciles(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """Calibration error over equal-count bins: sum of n_b / N x |mean p - mean y|."""
    n = len(p)
    if n == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    parts = np.array_split(order, bins)
    return float(sum(len(ix) / n * abs(p[ix].mean() - y[ix].mean()) for ix in parts if len(ix)))


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    order = np.argsort(p, kind="mergesort")
    return [
        {"bin": i, "n": len(ix), "p_mean": float(p[ix].mean()), "y_mean": float(y[ix].mean())}
        for i, ix in enumerate(np.array_split(order, bins))
        if len(ix)
    ]


def mae(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b))) if len(a) else float("nan")


SHARE_OUT = {
    "snap": ("snap_p50", "base_snap", "actual_snap", None),
    "tgt_share": ("tgt_share", "base_tgt_share", "actual_tgt_share", TGT_GROUPS),
    "carry_share": ("carry_share", "base_carry_share", "actual_carry_share", CAR_GROUPS),
}


def cohort(preds: pl.DataFrame, output: str) -> pl.DataFrame:
    """The rows an output is scored on: model and baseline both present (same rows); shares
    only for players who played."""
    if output == "play":
        return preds.filter(
            pl.col("played").is_not_null()
            & pl.col("p_play").is_not_null()
            & pl.col("base_p_play").is_not_null()
        )
    m, b, a, groups = SHARE_OUT[output]
    f = preds.filter(
        pl.col("played").fill_null(False)
        & pl.col(m).is_not_null()
        & pl.col(b).is_not_null()
        & pl.col(a).is_not_null()
    )
    if groups is not None:
        f = f.filter(pl.col("pgroup").is_in(groups))
    return f


def score_output(preds: pl.DataFrame, output: str) -> dict[str, Any]:
    c = cohort(preds, output)
    if output == "play":
        p, b = c["p_play"].to_numpy(), c["base_p_play"].to_numpy()
        y = c["played"].cast(pl.Float64).to_numpy()
        return {
            "n": c.height,
            "brier_model": brier(p, y),
            "brier_baseline": brier(b, y),
            "brier_base_rate": brier(np.full_like(y, y.mean() if len(y) else 0.0), y),
            "ece_q10": ece_deciles(p, y),
            "ece_q10_baseline": ece_deciles(b, y),
            "play_rate": float(y.mean()) if len(y) else float("nan"),
        }
    m, b, a, _ = SHARE_OUT[output]
    am = c[a].to_numpy()
    d = {
        "n": c.height,
        "mae_model": mae(c[m].to_numpy(), am),
        "mae_baseline": mae(c[b].to_numpy(), am),
    }
    if output == "snap":
        lo, hi = c["snap_p10"].to_numpy(), c["snap_p90"].to_numpy()
        d["coverage_80"] = (
            float(np.mean((am >= lo - 1e-12) & (am <= hi + 1e-12))) if len(am) else float("nan")
        )
    return d


def score(preds: pl.DataFrame) -> dict[str, Any]:
    """Pooled and by-season scores for every output."""
    pooled = {o: score_output(preds, o) for o in schema.AVAIL_OUTPUTS}
    by_season = []
    for s in sorted(preds["season"].unique().to_list()):
        sub = preds.filter(pl.col("season") == s)
        by_season.append(
            {"season": int(s), **{o: score_output(sub, o) for o in schema.AVAIL_OUTPUTS}}
        )
    return {"pooled": pooled, "by_season": by_season}


def wins_needed(n: int) -> int:
    """6 of 7 seasons (FL00's rule), scaled for other window lengths."""
    return n - 1 if n >= 7 else max(1, math.ceil(6 / 7 * n))


def verdicts(scores: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The pre-registered ship rule (FL00 phase file), per output."""
    out: dict[str, dict[str, Any]] = {}
    bs = scores["by_season"]
    for o in schema.AVAIL_OUTPUTS:
        pooled = scores["pooled"][o]
        key = "brier" if o == "play" else "mae"
        seasons = [s for s in bs if s[o]["n"] > 0]
        wins = sum(1 for s in seasons if s[o][f"{key}_model"] < s[o][f"{key}_baseline"])
        need = wins_needed(len(seasons))
        beats = bool(pooled["n"] > 0 and pooled[f"{key}_model"] < pooled[f"{key}_baseline"])
        v: dict[str, Any] = {
            "wins": wins,
            "seasons": len(seasons),
            "need": need,
            "beats_pooled": beats,
        }
        ok = beats and wins >= need
        if o == "play":
            v["ece_q10"] = pooled["ece_q10"]
            v["ece_ok"] = bool(pooled["ece_q10"] <= 0.02)
            ok = ok and v["ece_ok"]
        if o == "snap":
            v["coverage_80"] = pooled.get("coverage_80")
            v["coverage_ok"] = bool(0.72 <= (pooled.get("coverage_80") or 0.0) <= 0.88)
            ok = ok and v["coverage_ok"]
        v["ships"] = bool(ok)
        out[o] = v
    return out


def scoreboard_rows(preds: pl.DataFrame, mode: str, graded_at) -> pl.DataFrame:
    """AVAIL_SCORE_SCHEMA rows: per (season, week, view, output, group), group ALL plus each
    position group."""
    rows: list[dict[str, Any]] = []
    for (season, week, view), sub in sorted(
        preds.group_by("season", "week", "view"), key=lambda kv: kv[0]
    ):
        for o in schema.AVAIL_OUTPUTS:
            groups = ["ALL", *sorted(cohort(sub, o)["pgroup"].unique().to_list())]
            for g in groups:
                part = sub if g == "ALL" else sub.filter(pl.col("pgroup") == g)
                sc = score_output(part, o)
                if sc["n"] == 0:
                    continue
                rows.append(
                    {
                        "season": season,
                        "week": week,
                        "view": view,
                        "output": o,
                        "group": g,
                        "n": sc["n"],
                        "brier_model": sc.get("brier_model"),
                        "brier_baseline": sc.get("brier_baseline"),
                        "mae_model": sc.get("mae_model"),
                        "mae_baseline": sc.get("mae_baseline"),
                        "coverage_80": sc.get("coverage_80"),
                        "mode": mode,
                        "graded_at": graded_at,
                    }
                )
    if not rows:
        return pl.DataFrame(schema=schema.AVAIL_SCORE_SCHEMA)
    return schema.conform(pl.DataFrame(rows), schema.AVAIL_SCORE_SCHEMA)


def to_schema(
    pred: pl.DataFrame, *, view: str, version: str | None, created_at=None
) -> pl.DataFrame:
    """The AVAIL_SCHEMA frame (rows sorted by game, team, group, player)."""
    f = pred.with_columns(
        pl.lit(view).alias("view"),
        pl.lit(version, dtype=pl.String).alias("model_version"),
        pl.lit(created_at, dtype=schema.UTC).alias("created_at"),
        pl.col("snap_l1").fill_null(0.0),
    )
    return schema.conform(f, schema.AVAIL_SCHEMA).sort("game_id", "team", "pgroup", "player_id")
