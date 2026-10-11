"""`nfl playcalling build`: enriched plays and tendency tables, as of each week (PC00, D122).

Inputs (read only): curated `plays` (2010+), `ftn_plays` (2022+), `pfr_pass` (the blitz
cross-check, 2018+), `games`, and for history nflverse participation (research only,
2016-2025, `playcalling.participation`).

Outputs under `{NFL_DATA_ROOT}/playcalling/<S>/`, each written atomically:
- `plays_enriched.parquet`: one row per scrimmage play (runs and passes, sacks and scrambles
  included; no two-point tries) with every play-call label (labels.py);
- `team_game_tendencies.parquet`: per team, game and side, every metric x situation (n, value):
  the week-by-week view and the actuals a forecast is graded against;
- `team_tendencies.parquet`: per team, side and **as-of week** W (games strictly before week W),
  for the windows `season` (to date), `last4` (the team's last 4 games) and `last_season`
  (all of S-1): n, value, league value, difference, percentile among teams;
- `league_tendencies.parquet`: the league's pooled rate for every as-of week, window, metric
  and situation;
- `build.json`: what was built, the definitions, FTN / participation coverage, row counts.

Side: `offense` rows describe what the team does with the ball; `defense` rows describe what
offenses do against it (blitz, rushers and box are the defense's own choices). Every metric
is computed for both sides from the same play rows, so in the `season` and `last_season`
windows the league value is the same for both; `last4` pools each team's own last four games,
which differ between a team's offense rows and its opponents' defense rows, so the two sides'
`last4` league values can differ slightly (`team_tendencies.league_value` is per side).

As of a week (the leakage rule, documentation/04): a row for as-of week W only sums games with
`week < W`. FTN arrives ~2 days after a game (a Tuesday build lacks Monday night's game): FTN
metrics count only charted plays, so a missing game shrinks `n`, never biases the rate.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.paths import DataPaths, ensure_data_root
from nflengine.playcalling import labels as L

SCHEMA_VERSION = 1
FIRST_SEASON = 2016  # tables from here (participation's first season); plays reach back to 2010
FILES = (
    "plays_enriched",
    "team_game_tendencies",
    "team_tendencies",
    "league_tendencies",
)

PLAY_COLUMNS = (
    "game_id", "play_id", "season", "week", "season_type", "game_date", "order_sequence",
    "posteam", "defteam", "home_team", "qtr", "half_seconds_remaining", "down", "ydstogo",
    "yardline_100", "goal_to_go", "score_differential", "wp", "xpass", "pass_oe", "play_type",
    "pass", "qb_scramble", "sack", "aborted_play", "pass_length", "pass_location", "air_yards",
    "complete_pass", "interception", "run_location", "run_gap", "shotgun", "no_huddle",
    "yards_gained", "epa", "success", "first_down", "touchdown", "passer_player_id",
    "rusher_player_id", "receiver_player_id", "desc", "two_point_attempt",
)  # fmt: skip

# Without these a play can't be classified (a missing `pass` would make every play a run):
# a season file lacking one is refused; any other missing column becomes null with a note.
REQUIRED_COLUMNS = ("game_id", "play_id", "season", "week", "posteam", "defteam", "play_type",
                    "pass", "sack")  # fmt: skip
INT_COLS = ("season", "week")  # kept as integers; every other numeric column is read as f64
FTN_QB = {"S": "shotgun", "U": "under_center", "P": "pistol"}
FTN_HASH = {"L": "left", "M": "middle", "R": "right"}


def season_dir(paths: DataPaths, season: int) -> Path:
    return paths.playcalling / str(season)


def build_dir(paths: DataPaths, season: int, through_week: int | None = None) -> Path:
    """Where a build goes: the season's folder, or for a `--through-week` simulation its own
    subfolder (`through_week05/`), so a simulation never replaces the tables the pages read."""
    d = season_dir(paths, season)
    return d if through_week is None else d / f"through_week{through_week:02d}"


TEND_SCHEMA = {
    "season": pl.Int32, "as_of_week": pl.Int32, "team": pl.String, "side": pl.String,
    "window": pl.String, "metric": pl.String, "situation": pl.String, "family": pl.String,
    "n": pl.Int64, "games": pl.Int32, "value": pl.Float64, "league_value": pl.Float64,
    "diff": pl.Float64, "pct": pl.Float64, "unit": pl.String, "source": pl.String,
    "history_only": pl.Boolean,
}  # fmt: skip
LEAGUE_SCHEMA = {
    "season": pl.Int32, "as_of_week": pl.Int32, "window": pl.String, "metric": pl.String,
    "situation": pl.String, "family": pl.String, "unit": pl.String, "source": pl.String,
    "history_only": pl.Boolean, "n": pl.Int64, "value": pl.Float64, "teams": pl.Int32,
}  # fmt: skip


# --- inputs ------------------------------------------------------------------------------------
@dataclass
class Inputs:
    plays: pl.DataFrame  # curated plays, scrimmage only, PLAY_COLUMNS
    ftn: pl.DataFrame  # curated ftn_plays keyed by (game_id, play_id)
    part: pl.DataFrame  # parsed participation (empty outside 2016-2025)
    pfr: pl.DataFrame  # pfr_pass blitzes per (game_id, offense, defense)
    games: pl.DataFrame  # curated games: season, week, game_id, result
    notes: list[str] = field(default_factory=list)


def load_inputs(
    paths: DataPaths, seasons: Iterable[int], history: bool = True, log: Callable = print
) -> Inputs:
    """Read what a build of `seasons` needs (each season's prior season too, for the
    `last_season` window). Optional sources fail soft (an empty frame and a note)."""
    seasons = sorted(set(seasons))
    need = sorted({s for x in seasons for s in (x - 1, x)})
    cur = paths.curated
    notes: list[str] = []
    files = [cur / "plays" / f"season={s}.parquet" for s in need]
    files = [f for f in files if f.exists()]
    if not files:
        raise FileNotFoundError(f"no curated plays for seasons {need} under {cur / 'plays'}")
    frames = []
    for f in files:  # one file per season: dtypes differ between seasons (goal_to_go f64 / i32)
        scan = pl.scan_parquet(f.as_posix())
        schema = scan.collect_schema()
        cols = [c for c in PLAY_COLUMNS if c in schema]
        lost = [c for c in REQUIRED_COLUMNS if c not in cols]
        if lost:  # a missing call column would silently read every play as a run (Sol, D123)
            raise ValueError(f"{f.stem}: required plays columns missing: {', '.join(lost)}")
        frames.append(
            scan.select(
                pl.col(c).cast(pl.Float64) if schema[c].is_numeric() and c not in INT_COLS else c
                for c in cols
            )
            # a missing column becomes null (and a note) before the filter reads it
            .with_columns(pl.lit(None).alias(c) for c in PLAY_COLUMNS if c not in cols)
            .filter(L.scrimmage_filter())
            .collect()
        )
        notes += [
            f"{f.stem}: plays column {c} missing (null)" for c in PLAY_COLUMNS if c not in cols
        ]
    plays = pl.concat(frames, how="diagonal_relaxed")
    plays = plays.with_columns(pl.lit(None).alias(c) for c in PLAY_COLUMNS if c not in plays)

    ftn_path = cur / "ftn_plays.parquet"
    if ftn_path.exists():
        ftn = (
            pl.scan_parquet(ftn_path.as_posix())
            .filter(pl.col("season").is_in(need))
            .collect()
            .with_columns(
                pl.col("nflverse_game_id").alias("game_id"),
                pl.col("nflverse_play_id").cast(pl.Float64).alias("play_id"),
            )
            .unique(["game_id", "play_id"], keep="first", maintain_order=True)
        )
    else:
        ftn = pl.DataFrame()
        notes.append("ftn_plays missing: FTN metrics empty")

    part = pl.DataFrame()
    want = [s for s in need if L.PART_FIRST <= s <= L.PART_LAST]
    if history and want:
        try:
            from nflengine.playcalling.participation import (
                load_participation,
                parse_participation,
            )

            raw = load_participation(paths, want)
            part = parse_participation(raw) if raw.height else pl.DataFrame()
        except Exception as e:  # research data: fail soft
            notes.append(f"participation not loaded: {type(e).__name__}: {e}")
        if part.is_empty():
            notes.append("participation: no rows (history metrics empty)")

    pfr_path = cur / "pfr_pass.parquet"
    if pfr_path.exists():
        pfr = (
            pl.scan_parquet(pfr_path.as_posix())
            .filter(pl.col("season").is_in(need) & (pl.col("season") >= L.PFR_FIRST))
            .group_by("game_id", "team", "opponent")
            .agg(pl.col("times_blitzed").sum().alias("pfr_blitzed"))
            .collect()
            .rename({"team": "offense", "opponent": "defense"})
        )
    else:
        pfr = pl.DataFrame()
        notes.append("pfr_pass missing: the PFR blitz cross-check is empty")

    games = pl.read_parquet(cur / "games.parquet").filter(pl.col("season").is_in(need))
    games = games.select(
        "season", "week", "game_id", "game_type", "result",
        (pl.col("kickoff_utc").dt.convert_time_zone("UTC") if "kickoff_utc" in games.columns
         else pl.lit(None, dtype=pl.Datetime("us", "UTC"))).alias("kickoff_utc"),
    )  # fmt: skip
    for n in notes:
        log(f"[yellow]{n}[/]")
    return Inputs(plays=plays, ftn=ftn, part=part, pfr=pfr, games=games, notes=notes)


# --- enrich -------------------------------------------------------------------------------------
def _bool(col: str) -> pl.Expr:
    return (pl.col(col) == 1).fill_null(False)


def enrich_plays(plays: pl.DataFrame, ftn: pl.DataFrame, part: pl.DataFrame) -> pl.DataFrame:
    """Scrimmage plays (as `load_inputs` filters them) -> one row per play with every label."""
    p = plays.filter(L.scrimmage_filter()) if "two_point_attempt" in plays.columns else plays
    dropback = (pl.col("pass") == 1).fill_null(False)
    designed_run = (pl.col("play_type") == "run") & ~dropback
    attempt = (pl.col("play_type") == "pass") & ~(pl.col("sack") == 1).fill_null(False)
    base = p.select(
        pl.col("game_id"),
        pl.col("play_id").cast(pl.Float64),
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("season_type"),
        pl.col("game_date"),
        pl.col("order_sequence").cast(pl.Float64),
        pl.col("posteam").alias("offense"),
        pl.col("defteam").alias("defense"),
        (pl.col("posteam") == pl.col("home_team")).alias("home"),
        pl.col("qtr").cast(pl.Int32).alias("quarter"),
        pl.col("half_seconds_remaining").cast(pl.Int32).alias("half_seconds"),
        pl.col("down").cast(pl.Int32),
        pl.col("ydstogo").cast(pl.Int32).alias("distance"),
        pl.col("yardline_100").cast(pl.Int32).alias("yards_to_goal"),
        _bool("goal_to_go").alias("goal_to_go"),
        pl.col("score_differential").cast(pl.Int32).alias("score_diff"),
        pl.col("wp").cast(pl.Float64),
        pl.when(dropback).then(pl.lit("pass")).otherwise(pl.lit("run")).alias("call"),
        dropback.alias("dropback"),
        designed_run.alias("designed_run"),
        attempt.alias("pass_attempt"),
        _bool("sack").alias("sack"),
        _bool("qb_scramble").alias("scramble"),
        _bool("aborted_play").alias("aborted"),
        pl.col("xpass").cast(pl.Float64),
        pl.col("pass_oe").cast(pl.Float64),  # percentage points (nflverse)
        pl.when(attempt).then(pl.col("pass_length")).alias("pass_length"),
        pl.when(attempt).then(pl.col("pass_location")).alias("pass_location"),
        pl.when(attempt).then(pl.col("air_yards").cast(pl.Float64)).alias("air_yards"),
        pl.when(attempt).then(_bool("complete_pass")).alias("complete"),
        pl.when(attempt).then(_bool("interception")).alias("interception"),
        pl.when(pl.col("play_type") == "run").then(pl.col("run_location")).alias("run_location"),
        pl.when(pl.col("play_type") == "run").then(pl.col("run_gap")).alias("run_gap"),
        pl.col("shotgun").cast(pl.Boolean),
        pl.col("no_huddle").cast(pl.Boolean),
        pl.col("yards_gained").cast(pl.Float64),
        pl.col("epa").cast(pl.Float64),
        pl.col("success").cast(pl.Boolean),
        _bool("first_down").alias("first_down"),
        _bool("touchdown").alias("touchdown"),
        pl.col("passer_player_id").alias("passer_id"),
        pl.col("rusher_player_id").alias("rusher_id"),
        pl.col("receiver_player_id").alias("receiver_id"),
        pl.col("desc"),
    ).with_columns(
        L.field_zone(pl.col("yards_to_goal")).alias("field_zone"),
        L.score_state(pl.col("score_diff")).alias("score_state"),
        L.distance_bucket(pl.col("distance")).alias("distance_bucket"),
        L.down_distance(pl.col("down"), pl.col("distance")).alias("down_distance"),
        L.time_bucket(pl.col("quarter"), pl.col("half_seconds")).alias("time_bucket"),
        L.two_minute(pl.col("quarter"), pl.col("half_seconds"))
        .fill_null(False)
        .alias("two_minute"),
        L.neutral(pl.col("wp"), pl.col("down"), pl.col("half_seconds")).alias("neutral"),
        L.pass_zone(pl.col("pass_length"), pl.col("pass_location")).alias("pass_zone"),
        L.run_direction(pl.col("run_location"), pl.col("run_gap")).alias("run_direction"),
        pl.when(pl.col("pass_attempt") & pl.col("air_yards").is_not_null())
        .then(pl.col("air_yards") >= L.DEEP_SHOT_AIR_YARDS)
        .alias("deep_shot"),
        pl.when(pl.col("yards_gained").is_null())
        .then(None)
        .otherwise(
            (pl.col("dropback") & (pl.col("yards_gained") >= L.EXPLOSIVE_PASS_YARDS))
            | (pl.col("designed_run") & (pl.col("yards_gained") >= L.EXPLOSIVE_RUN_YARDS))
        )
        .alias("explosive"),
    )
    base = base.join(_ftn_labels(ftn), on=["game_id", "play_id"], how="left").with_columns(
        pl.col("ftn_charted").fill_null(False)
    )
    base = base.join(_part_labels(part), on=["game_id", "play_id"], how="left").with_columns(
        pl.col("part_charted").fill_null(False)
    )
    return base.sort("season", "week", "game_id", "order_sequence", "play_id")


FTN_COLUMNS = {
    "play_action": pl.Boolean,
    "screen": pl.Boolean,
    "rpo": pl.Boolean,
    "motion": pl.Boolean,
    "trick_play": pl.Boolean,
    "qb_alignment": pl.String,
    "backfield": pl.Int32,
    "box": pl.Int32,
    "blitzers": pl.Int32,
    "rushers": pl.Int32,
    "blitz": pl.Boolean,
    "hash": pl.String,
    "out_of_pocket": pl.Boolean,
}
PART_COLUMNS = {
    "formation": pl.String,
    "personnel": pl.String,
    "extra_ol": pl.Boolean,
    "def_package": pl.String,
    "coverage": pl.String,
    "man_zone": pl.String,
    "target_route": pl.String,
    "pressure": pl.Boolean,
    "time_to_throw": pl.Float64,
    "part_box": pl.Int32,
    "part_rushers": pl.Int32,
}


def _ftn_labels(ftn: pl.DataFrame) -> pl.DataFrame:
    keys = {"game_id": pl.String, "play_id": pl.Float64}
    if ftn.is_empty():
        return pl.DataFrame(schema={**keys, "ftn_charted": pl.Boolean, **FTN_COLUMNS})
    box = pl.col("n_defense_box").cast(pl.Int32)
    qb = pl.col("qb_location").cast(pl.String).str.strip_chars()
    hash_ = pl.col("starting_hash").cast(pl.String).str.strip_chars()
    # FTN keeps placeholder rows for plays it didn't chart: QB location '0', box 0 and every
    # flag False (2024_07_BAL_TB: 97 of 131 plays). They aren't "no play-action": drop them,
    # so the play counts as not charted (ftn_charted False, FTN labels null).
    placeholder = (qb.is_null() | qb.is_in(["", "0"])) & (box.fill_null(0) == 0)
    return ftn.filter(~placeholder).select(
        pl.col("game_id"),
        pl.col("play_id").cast(pl.Float64),
        pl.lit(True).alias("ftn_charted"),
        pl.col("is_play_action").alias("play_action"),
        pl.col("is_screen_pass").alias("screen"),
        pl.col("is_rpo").alias("rpo"),
        pl.col("is_motion").alias("motion"),
        pl.col("is_trick_play").alias("trick_play"),
        qb.replace_strict(FTN_QB, default=None).alias("qb_alignment"),  # ' S' seen once
        pl.col("n_offense_backfield").cast(pl.Int32).alias("backfield"),
        pl.when(box > 0).then(box).alias("box"),  # 0 = not charted (special teams)
        pl.col("n_blitzers").cast(pl.Int32).alias("blitzers"),
        pl.col("n_pass_rushers").cast(pl.Int32).alias("rushers"),
        (pl.col("n_blitzers") > 0).alias("blitz"),
        hash_.replace_strict(FTN_HASH, default=None).alias("hash"),
        pl.col("is_qb_out_of_pocket").alias("out_of_pocket"),
    )


def _part_labels(part: pl.DataFrame) -> pl.DataFrame:
    keys = {"game_id": pl.String, "play_id": pl.Float64}
    if part.is_empty():
        return pl.DataFrame(schema={**keys, "part_charted": pl.Boolean, **PART_COLUMNS})
    return part.select(
        pl.col("game_id"),
        pl.col("play_id").cast(pl.Float64),
        pl.lit(True).alias("part_charted"),
        *(pl.col(c).cast(t) for c, t in PART_COLUMNS.items()),
    )


# --- per team-game sums -----------------------------------------------------------------------
def metric_pairs(season: int) -> list[tuple[L.Metric, L.Situation]]:
    """Every (metric, situation) a season's tables carry."""
    return [(m, s) for m in L.METRICS if m.exists_in(season) for s in m.situations()]


def team_game_sums(
    enriched: pl.DataFrame, season: int, pfr: pl.DataFrame | None = None
) -> pl.DataFrame:
    """One row per (game, team, side, metric, situation) with `num` and `den` (n of plays),
    for one season; pairs with no counted play are dropped."""
    e = enriched.filter(pl.col("season") == season)
    out_schema = {
        "season": pl.Int32,
        "week": pl.Int32,
        "game_id": pl.String,
        "team": pl.String,
        "opponent": pl.String,
        "side": pl.String,
        "metric": pl.String,
        "situation": pl.String,
        "num": pl.Float64,
        "den": pl.Int64,
    }
    if e.is_empty():
        return pl.DataFrame(schema=out_schema)
    pairs = metric_pairs(season)
    aggs: list[pl.Expr] = []
    row = pl.col("play_id").is_not_null() | pl.col("play_id").is_null()  # True on every row
    for i, (m, s) in enumerate(pairs):
        # `row &` keeps the condition per row: `lit(True) & lit(True)` alone is one scalar, and
        # its sum in a group is 1, not the group's row count (dropback_rate x all).
        cond = (row & m.den() & s.expr()).fill_null(False)
        aggs.append(cond.sum().cast(pl.Int64).alias(f"d{i}"))
        aggs.append(pl.when(cond).then(m.num()).otherwise(0.0).fill_null(0.0).sum().alias(f"n{i}"))
    ids = pl.DataFrame(
        {
            "i": list(range(len(pairs))),
            "metric": [m.name for m, _ in pairs],
            "situation": [s.name for _, s in pairs],
        },
        schema={"i": pl.Int32, "metric": pl.String, "situation": pl.String},
    )
    frames = []
    for side, team, opp in (("offense", "offense", "defense"), ("defense", "defense", "offense")):
        wide = e.group_by("season", "week", "game_id", team, opp).agg(aggs)
        wide = wide.rename({team: "team", opp: "opponent"})
        keys = ["season", "week", "game_id", "team", "opponent"]
        long_d = wide.select(*keys, *[f"d{i}" for i in range(len(pairs))]).unpivot(
            index=keys, variable_name="v", value_name="den"
        )
        long_n = wide.select(*keys, *[f"n{i}" for i in range(len(pairs))]).unpivot(
            index=keys, variable_name="v", value_name="num"
        )
        long = (
            long_d.with_columns(pl.col("v").str.slice(1).cast(pl.Int32).alias("i"))
            .drop("v")
            .join(
                long_n.with_columns(pl.col("v").str.slice(1).cast(pl.Int32).alias("i")).drop("v"),
                on=[*keys, "i"],
            )
            .filter(pl.col("den") > 0)
            .join(ids, on="i")
            .drop("i")
            .with_columns(pl.lit(side).alias("side"))
        )
        frames.append(long)
    tg = pl.concat(frames)
    if pfr is not None and not pfr.is_empty() and L.PFR_BLITZ.exists_in(season):
        tg = pl.concat([tg, _pfr_blitz(e, pfr)], how="diagonal_relaxed")
    return tg.select([pl.col(c).cast(t) for c, t in out_schema.items()])


def _pfr_blitz(e: pl.DataFrame, pfr: pl.DataFrame) -> pl.DataFrame:
    """PFR blitzes per opponent dropback for each team-game PFR has published (the defense's
    blitzes on `defense` rows, the blitzes an offense faced on `offense` rows)."""
    db = e.group_by("season", "week", "game_id", "offense", "defense").agg(
        pl.col("dropback").sum().cast(pl.Int64).alias("den")
    )
    j = db.join(pfr, on=["game_id", "offense", "defense"], how="inner").filter(pl.col("den") > 0)
    rows = []
    for side, team, opp in (("offense", "offense", "defense"), ("defense", "defense", "offense")):
        rows.append(
            j.select(
                "season",
                "week",
                "game_id",
                pl.col(team).alias("team"),
                pl.col(opp).alias("opponent"),
                pl.lit(side).alias("side"),
                pl.lit(L.PFR_BLITZ.name).alias("metric"),
                pl.lit("all").alias("situation"),
                pl.col("pfr_blitzed").cast(pl.Float64).alias("num"),
                "den",
            )
        )
    return pl.concat(rows)


def team_game_table(tg: pl.DataFrame) -> pl.DataFrame:
    """The file `team_game_tendencies.parquet`: the sums as rates, with family / source."""
    return _with_meta(
        tg.with_columns((pl.col("num") / pl.col("den")).alias("value"))
        .rename({"den": "n"})
        .select(
            "season",
            "week",
            "game_id",
            "team",
            "opponent",
            "side",
            "metric",
            "situation",
            "n",
            "value",
        )  # fmt: skip
    ).sort("season", "week", "game_id", "side", "team", "metric", "situation")


def _with_meta(df: pl.DataFrame) -> pl.DataFrame:
    meta = pl.DataFrame(
        [
            {
                "metric": m.name,
                "unit": m.unit,
                "source": m.source,
                "history_only": m.history_only,
            }
            for m in (*L.METRICS, L.PFR_BLITZ)
        ]
    )
    fam = pl.DataFrame(
        {"situation": list(L.SITUATION_FAMILY), "family": list(L.SITUATION_FAMILY.values())}
    )
    return df.join(fam, on="situation", how="left").join(meta, on="metric", how="left")


# --- as-of windows ------------------------------------------------------------------------------
def team_index(enriched: pl.DataFrame, season: int) -> pl.DataFrame:
    """Each team's games in a season in order: (team, game_id, week, game_no 1..n)."""
    e = enriched.filter(pl.col("season") == season)
    g = (
        pl.concat(
            [
                e.select("game_id", "week", "game_date", pl.col("offense").alias("team")),
                e.select("game_id", "week", "game_date", pl.col("defense").alias("team")),
            ]
        )
        .unique()
        .sort("team", "week", "game_date", "game_id")
    )
    return g.with_columns(
        pl.int_range(1, pl.len() + 1).over("team").cast(pl.Int32).alias("game_no")
    )


def as_of_weeks(
    games: pl.DataFrame,
    enriched: pl.DataFrame,
    season: int,
    through_week: int | None = None,
) -> list[int]:
    """As-of weeks 1..W_max, where every week before W_max is complete in `games` and in the
    plays (curated plays can lag a finished game), capped at `through_week + 1`."""
    g = games.filter(pl.col("season") == season)
    if g.is_empty():
        return []
    in_plays = set(enriched.filter(pl.col("season") == season)["game_id"].unique().to_list())
    weeks = sorted(set(g["week"].to_list()))
    w_max = 1
    for w in weeks:
        gw = g.filter(pl.col("week") == w)
        done = gw["result"].is_not_null().all() and set(gw["game_id"].to_list()) <= in_plays
        if not done or (through_week is not None and w > through_week):
            break
        w_max = w + 1
    return list(range(1, w_max + 1))


def _cumulative(tg: pl.DataFrame, idx: pl.DataFrame) -> pl.DataFrame:
    """Dense cumulative sums per (team, side, metric, situation) over game_no 0..n."""
    pairs = tg.select("side", "metric", "situation").unique()
    teams = idx.group_by("team").agg(pl.col("game_no").max().alias("n_games"))
    grid = (
        teams.with_columns(pl.int_ranges(0, pl.col("n_games") + 1).alias("game_no"))
        .explode("game_no", empty_as_null=True)
        .with_columns(pl.col("game_no").cast(pl.Int32))
        .drop("n_games")
        .join(pairs, how="cross")
    )
    x = tg.join(idx.select("team", "game_id", "game_no"), on=["team", "game_id"], how="inner")
    x = x.group_by("team", "game_no", "side", "metric", "situation").agg(
        pl.col("num").sum(), pl.col("den").sum()
    )
    keys = ["team", "side", "metric", "situation"]
    return (
        grid.join(x, on=[*keys, "game_no"], how="left")
        .with_columns(pl.col("num").fill_null(0.0), pl.col("den").fill_null(0))
        .sort(*keys, "game_no")
        .with_columns(
            pl.col("num").cum_sum().over(keys).alias("cnum"),
            pl.col("den").cum_sum().over(keys).alias("cden"),
        )
        .select(*keys, "game_no", "cnum", "cden")
    )


def _subtract(frame: pl.DataFrame, held: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
    """`frame`'s num / den minus the held rows' sums for the same keys and as-of week."""
    hs = held.group_by(*keys, "as_of_week").agg(
        pl.col("num").sum().alias("_hn"), pl.col("den").sum().alias("_hd")
    )
    return (
        frame.join(hs, on=[*keys, "as_of_week"], how="left")
        .with_columns(
            (pl.col("num") - pl.col("_hn").fill_null(0.0)).alias("num"),
            (pl.col("den") - pl.col("_hd").fill_null(0)).alias("den"),
        )
        .drop("_hn", "_hd")
    )


def _with_kickoff(games: pl.DataFrame) -> pl.DataFrame:
    """`games` with a `kickoff_utc` column (all null when the frame has none)."""
    if "kickoff_utc" in games.columns:
        return games
    return games.with_columns(pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("kickoff_utc"))


def as_of_cutoffs(games: pl.DataFrame, season: int, weeks: list[int]) -> pl.DataFrame:
    """The moment each as-of week stands for (leakage rule 1, D123): the end of the Tuesday,
    US Eastern, after the last game before week W, or week W's first kickoff if that comes
    first. Null when the season has no game before week W."""
    from datetime import time as dtime
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    et = ZoneInfo(L.CUTOFF_TZ)
    g = _with_kickoff(games).filter(
        (pl.col("season") == season) & pl.col("kickoff_utc").is_not_null()
    )
    rows: list[dict[str, Any]] = []
    for w in weeks:
        before = g.filter(pl.col("week") < w)
        cut = None
        if before.height:
            day = before["kickoff_utc"].max().astimezone(et).date()
            ahead = (L.CUTOFF_WEEKDAY - day.weekday()) % 7 or 7  # the next Tuesday after it
            cut = datetime.combine(day + timedelta(days=ahead), dtime(23, 59, 59), tzinfo=et)
            cut = cut.astimezone(UTC)
            first = g.filter(pl.col("week") == w)["kickoff_utc"].min()
            if first is not None and first < cut:
                cut = first
        rows.append({"as_of_week": w, "cutoff_utc": cut})
    return pl.DataFrame(
        rows, schema={"as_of_week": pl.Int32, "cutoff_utc": pl.Datetime("us", "UTC")}
    )


def held_back(tg: pl.DataFrame, games: pl.DataFrame, cutoffs: pl.DataFrame) -> pl.DataFrame:
    """The team-game sums an as-of week couldn't have seen yet: FTN metrics of a game until
    `FTN_LAG_HOURS` after its kickoff (Monday night's game on the Tuesday after it), the PFR
    cross-check for `PFR_LAG_WEEKS` (D44). Play-by-play is never held back."""
    schema = {
        "team": pl.String, "side": pl.String, "metric": pl.String, "situation": pl.String,
        "game_id": pl.String, "as_of_week": pl.Int32, "num": pl.Float64, "den": pl.Int64,
    }  # fmt: skip
    src = pl.DataFrame(
        {
            "metric": [m.name for m in (*L.METRICS, L.PFR_BLITZ)],
            "source": [m.source for m in (*L.METRICS, L.PFR_BLITZ)],
        }
    ).filter(pl.col("source").is_in(["ftn", "pfr"]))
    late_rows = tg.join(src, on="metric")
    if late_rows.is_empty() or cutoffs.is_empty():
        return pl.DataFrame(schema=schema)
    pairs = (
        tg.select("game_id", "week")
        .unique()
        .join(_with_kickoff(games).select("game_id", "kickoff_utc"), on="game_id", how="left")
        .join(cutoffs, how="cross")
        .filter(pl.col("week") < pl.col("as_of_week"))
    )
    ko_late = pl.col("kickoff_utc") + pl.duration(hours=L.FTN_LAG_HOURS) > pl.col("cutoff_utc")
    # No kickoff time (or no cutoff): fail closed, the previous week's FTN waits a week.
    unknown = (pl.col("kickoff_utc").is_null() | pl.col("cutoff_utc").is_null()) & (
        pl.col("week") >= pl.col("as_of_week") - 1
    )
    ftn = pairs.filter(ko_late.fill_null(False) | unknown)
    pfr = pairs.filter(pl.col("week") >= pl.col("as_of_week") - L.PFR_LAG_WEEKS)
    late = pl.concat(
        [
            ftn.select("game_id", "as_of_week", pl.lit("ftn").alias("source")),
            pfr.select("game_id", "as_of_week", pl.lit("pfr").alias("source")),
        ]
    )
    return late_rows.join(late, on=["game_id", "source"]).select(
        [pl.col(c).cast(t) for c, t in schema.items()]
    )


def tendencies(
    tg: pl.DataFrame,
    idx: pl.DataFrame,
    weeks: list[int],
    tg_prev: pl.DataFrame | None = None,
    season: int | None = None,
    held: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """The windows for every as-of week, with league values and percentiles.

    `season`: games with week < W; `last4`: the team's last `RECENT_GAMES` of those;
    `last_season`: every game of the previous season (the same for every W). `held`
    (`held_back`): sums an as-of week couldn't see yet (late FTN / PFR), taken back out of the
    `season` and `last4` windows; `games` keeps counting every game (play-by-play)."""
    if not weeks:
        return pl.DataFrame(schema=TEND_SCHEMA)
    w = pl.DataFrame({"as_of_week": weeks}, schema={"as_of_week": pl.Int32})
    keys = ["team", "side", "metric", "situation"]
    frames: list[pl.DataFrame] = []
    if not tg.is_empty():
        kw = (
            idx.join(w, how="cross")
            .group_by("team", "as_of_week")
            .agg((pl.col("week") < pl.col("as_of_week")).sum().cast(pl.Int32).alias("k"))
        )
        cum = _cumulative(tg, idx)
        at_k = kw.join(cum, left_on=["team", "k"], right_on=["team", "game_no"], how="inner")
        season_f = at_k.select(
            *keys,
            "as_of_week",
            pl.lit("season").alias("window"),
            pl.col("cnum").alias("num"),
            pl.col("cden").alias("den"),
            pl.col("k").alias("games"),
        )
        back = cum.rename({"cnum": "bnum", "cden": "bden", "game_no": "b"})
        rec = at_k.with_columns(
            (pl.col("k") - L.RECENT_GAMES).clip(lower_bound=0).cast(pl.Int32).alias("b")
        ).join(back, on=[*keys, "b"], how="inner")
        last4_f = rec.select(
            *keys,
            "as_of_week",
            pl.lit("last4").alias("window"),
            (pl.col("cnum") - pl.col("bnum")).alias("num"),
            (pl.col("cden") - pl.col("bden")).alias("den"),
            (pl.col("k") - pl.col("b")).alias("games"),
        )
        if held is not None and not held.is_empty():
            h = held.join(idx.select("team", "game_id", "game_no"), on=["team", "game_id"])
            starts = rec.select("team", "as_of_week", "b").unique()
            season_f = _subtract(season_f, h, keys)
            last4_f = _subtract(  # only held games inside the window
                last4_f,
                h.join(starts, on=["team", "as_of_week"]).filter(pl.col("game_no") > pl.col("b")),
                keys,
            )
        frames += [season_f, last4_f]
    if tg_prev is not None and not tg_prev.is_empty():
        games_prev = (
            tg_prev.select("team", "game_id")
            .unique()
            .group_by("team")
            .agg(pl.len().cast(pl.Int32).alias("games"))
        )
        prev = (
            tg_prev.group_by(keys)
            .agg(pl.col("num").sum(), pl.col("den").sum())
            .join(games_prev, on="team")
            .join(w, how="cross")
            .with_columns(pl.lit("last_season").alias("window"))
        )
        frames.append(prev.select(*keys, "as_of_week", "window", "num", "den", "games"))
    if not frames:
        return pl.DataFrame(schema=TEND_SCHEMA)
    t = pl.concat([f.with_columns(pl.col("games").cast(pl.Int32)) for f in frames]).filter(
        pl.col("den") > 0
    )
    grp = ["as_of_week", "side", "window", "metric", "situation"]
    t = t.with_columns(
        (pl.col("num") / pl.col("den")).alias("value"),
        (pl.col("num").sum().over(grp) / pl.col("den").sum().over(grp)).alias("league_value"),
        pl.len().over(grp).alias("_teams"),
    ).with_columns(
        (pl.col("value") - pl.col("league_value")).alias("diff"),
        pl.when(pl.col("_teams") > 1)
        .then(
            100.0
            * (pl.col("value").rank("average").over(grp) - 1)
            / (pl.col("_teams") - 1).cast(pl.Float64)
        )
        .otherwise(50.0)
        .alias("pct"),
    )
    t = _with_meta(t.rename({"den": "n"})).with_columns(
        pl.lit(season, dtype=pl.Int32).alias("season")
    )
    return t.select(
        "season", "as_of_week", "team", "side", "window", "metric", "situation", "family",
        pl.col("n").cast(pl.Int64), "games", "value", "league_value", "diff", "pct", "unit",
        "source", "history_only",
    ).sort("as_of_week", "side", "window", "metric", "situation", "team")  # fmt: skip


def league_table(tend: pl.DataFrame) -> pl.DataFrame:
    """The league's pooled rate per as-of week, window, metric and situation, pooled over the
    offense rows (the same sums as the defense rows in `season` and `last_season`; for `last4`
    it's the offenses' last four games: use `team_tendencies.league_value` for a side's own)."""
    if tend.is_empty():
        return pl.DataFrame(schema=LEAGUE_SCHEMA)
    return (
        tend.filter(pl.col("side") == "offense")
        .with_columns((pl.col("value") * pl.col("n")).alias("_num"))
        .group_by(
            "season",
            "as_of_week",
            "window",
            "metric",
            "situation",
            "family",
            "unit",
            "source",
            "history_only",
        )  # fmt: skip
        .agg(
            pl.col("n").sum(),
            (pl.col("_num").sum() / pl.col("n").sum()).alias("value"),
            pl.len().cast(pl.Int32).alias("teams"),
        )
        .sort("as_of_week", "window", "metric", "situation")
    )


# --- coverage -----------------------------------------------------------------------------------
def coverage(enriched: pl.DataFrame, games: pl.DataFrame, season: int) -> dict[str, Any]:
    """FTN and participation coverage of a season's plays (build.json, W&B)."""
    e = enriched.filter(pl.col("season") == season)
    by_week = (
        e.group_by("week")
        .agg(
            pl.col("game_id").n_unique().alias("games"),
            pl.col("game_id").filter(pl.col("ftn_charted")).n_unique().alias("ftn_games"),
            pl.len().alias("plays"),
            pl.col("ftn_charted").sum().alias("ftn_plays"),
            pl.col("part_charted").sum().alias("part_plays"),
        )
        .sort("week")
    )
    g_done = games.filter((pl.col("season") == season) & pl.col("result").is_not_null())
    ftn_games = set(e.filter(pl.col("ftn_charted"))["game_id"].unique().to_list())
    no_ftn = (
        sorted(set(e["game_id"].unique().to_list()) - ftn_games) if season >= L.FTN_FIRST else []
    )
    n = max(e.height, 1)
    return {
        "plays": e.height,
        "games_in_plays": e["game_id"].n_unique(),
        "games_completed": g_done.height,
        "ftn_join_rate": round(e["ftn_charted"].sum() / n, 4),
        "part_join_rate": round(e["part_charted"].sum() / n, 4),
        "ftn_weeks": by_week.filter(pl.col("ftn_games") > 0)["week"].to_list(),
        "games_without_ftn": no_ftn,
        "by_week": by_week.to_dicts(),
    }


# --- the build ----------------------------------------------------------------------------------
@dataclass
class SeasonBuild:
    season: int
    weeks: list[int]
    enriched: pl.DataFrame
    tg: pl.DataFrame
    tend: pl.DataFrame
    league: pl.DataFrame
    meta: dict[str, Any]


def build_season(
    inp: Inputs,
    enriched: pl.DataFrame,
    season: int,
    through_week: int | None = None,
    tg_cache: dict[int, pl.DataFrame] | None = None,
) -> SeasonBuild:
    """Everything for one season, in memory (no writes)."""
    tg_cache = tg_cache if tg_cache is not None else {}
    e = enriched.filter(pl.col("season") == season)
    if through_week is not None:
        e = e.filter(pl.col("week") <= through_week)
    if season - 1 not in tg_cache:
        prev = enriched.filter(pl.col("season") == season - 1)
        tg_cache[season - 1] = team_game_sums(prev, season - 1, inp.pfr)
    if through_week is None:  # a cut-off build is never cached (the next season wants it all)
        if season not in tg_cache:
            tg_cache[season] = team_game_sums(e, season, inp.pfr)
        tg = tg_cache[season]
    else:
        tg = team_game_sums(e, season, inp.pfr)
    # The previous season's window carries only the metrics this season has: no 2025
    # participation (research data) on a 2026 row, no NGS-era formation on a 2023 row.
    keep = [m.name for m in (*L.METRICS, L.PFR_BLITZ) if m.exists_in(season)]
    tg_prev = tg_cache[season - 1].filter(pl.col("metric").is_in(keep))
    weeks = as_of_weeks(inp.games, e, season, through_week)
    idx = team_index(e, season)
    cutoffs = as_of_cutoffs(inp.games, season, weeks)
    held = held_back(tg, inp.games, cutoffs)
    tend = tendencies(tg, idx, weeks, tg_prev, season=season, held=held)
    league = league_table(tend)
    meta = {
        "schema_version": SCHEMA_VERSION,
        "season": season,
        "through_week": through_week,
        "as_of_weeks": [weeks[0], weeks[-1]] if weeks else [],
        "last_complete_week": (weeks[-1] - 1) if weeks else None,
        "coverage": coverage(e, inp.games, season),
        "rows": {
            "plays_enriched": e.height,
            "team_game_tendencies": tg.height,
            "team_tendencies": tend.height,
            "league_tendencies": league.height,
        },
        "history_metrics": bool(e["part_charted"].any()),
        "availability": _availability(cutoffs, held, weeks),
    }
    return SeasonBuild(season, weeks, e, tg, tend, league, meta)


def _atomic_json(path: Path, doc: dict) -> None:
    from nflengine.fsutil import replace_file

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
        replace_file(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _availability(cutoffs: pl.DataFrame, held: pl.DataFrame, weeks: list[int]) -> dict:
    """build.json: the newest as-of week's cutoff and the games it still holds back."""
    if not weeks:
        return {"cutoff_utc": None, "ftn_held_games": [], "pfr_held_games": 0}
    w = weeks[-1]
    cut = cutoffs.filter(pl.col("as_of_week") == w)["cutoff_utc"]
    hw = held.filter(pl.col("as_of_week") == w)
    src = {m.name: m.source for m in (*L.METRICS, L.PFR_BLITZ)}
    ftn = hw.filter(pl.col("metric").replace_strict(src, default="") == "ftn")
    pfr = hw.filter(pl.col("metric") == L.PFR_BLITZ.name)
    return {
        "as_of_week": w,
        "cutoff_utc": cut[0].isoformat() if cut.len() and cut[0] is not None else None,
        "ftn_held_games": sorted(ftn["game_id"].unique().to_list()),
        "pfr_held_games": pfr["game_id"].n_unique(),
    }


def write_season(paths: DataPaths, sb: SeasonBuild, extra: dict | None = None) -> Path:
    """Write the four tables, then build.json last: its presence marks a finished build, so
    the old one goes first (a write that fails half-way leaves no build.json behind)."""
    from nflengine.ops.lock import write_parquet_atomic

    out = build_dir(paths, sb.season, sb.meta.get("through_week"))
    (out / "build.json").unlink(missing_ok=True)
    frames = {
        "plays_enriched": sb.enriched,
        "team_game_tendencies": team_game_table(sb.tg),
        "team_tendencies": sb.tend,
        "league_tendencies": sb.league,
    }
    for name, df in frames.items():
        write_parquet_atomic(df, out / f"{name}.parquet", compression="zstd")
    doc = {
        **sb.meta,
        **(extra or {}),
        "files": {n: f"{n}.parquet" for n in frames},
        "definitions": L.definitions(),
        "metrics": L.catalogue(),
    }
    _atomic_json(out / "build.json", doc)
    return out


def run_build(
    season: int,
    through_week: int | None = None,
    history: Iterable[int] = (),
    *,
    use_wandb: bool = True,
    launched_by: str | None = None,
    log: Callable[..., None] = print,
    paths: DataPaths | None = None,
    smoke: bool = False,
) -> dict[str, Any]:
    """Build `season` (as of `through_week + 1` when given) plus any `history` seasons, write
    their folders, and log one `playcall-build` W&B run."""
    paths = paths or ensure_data_root()
    seasons = sorted({season, *history})
    bad = [s for s in seasons if s < FIRST_SEASON]
    if bad:
        raise ValueError(f"play-calling tables start in {FIRST_SEASON}; got {bad}")
    t0 = time.perf_counter()
    inp = load_inputs(paths, seasons, log=log)
    enriched = enrich_plays(inp.plays, inp.ftn, inp.part)
    log(f"enriched {enriched.height:,} plays ({time.perf_counter() - t0:.1f} s)")
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    cache: dict[int, pl.DataFrame] = {}
    results: list[dict[str, Any]] = []
    builds: list[SeasonBuild] = []
    for s in seasons:
        t = time.perf_counter()
        tw = through_week if s == season else None
        sb = build_season(inp, enriched, s, tw, cache)
        secs = round(time.perf_counter() - t, 1)
        folder = write_season(
            paths, sb, {"built_at": built_at, "seconds": secs, "notes": inp.notes}
        )
        weeks = f"{sb.weeks[0]}-{sb.weeks[-1]}" if sb.weeks else "none"
        log(
            f"{s}: {sb.meta['rows']['plays_enriched']:,} plays, as-of weeks {weeks}, "
            f"{sb.tend.height:,} tendency rows, FTN {sb.meta['coverage']['ftn_join_rate']:.1%}, "
            f"participation {sb.meta['coverage']['part_join_rate']:.1%} ({secs} s) -> {folder}"
        )
        # W&B needs only the league, the team-game sums and the meta: drop the big frames
        builds.append(replace(sb, enriched=sb.enriched.clear(), tend=sb.tend.clear()))
        results.append({"season": s, "folder": str(folder), **sb.meta, "seconds": secs})
        if s != season:  # keep only what the next season's last_season window needs
            cache = {k: v for k, v in cache.items() if k >= s}
    out: dict[str, Any] = {"seasons": results, "url": None, "notes": inp.notes}
    if use_wandb:
        out["url"] = log_wandb(builds, season, through_week, launched_by, paths, smoke)
    return out


# --- W&B ----------------------------------------------------------------------------------------
def league_trend(builds: list[SeasonBuild]) -> pl.DataFrame:
    """Each season's league rates for the headline metrics, at its last as-of week (the whole
    season so far)."""
    rows = []
    for sb in builds:
        if sb.league.is_empty():
            continue
        last = sb.league.filter(
            (pl.col("as_of_week") == sb.weeks[-1]) & (pl.col("window") == "season")
        )
        row: dict[str, Any] = {"season": sb.season, "through_week": sb.weeks[-1] - 1}
        for metric, sit in L.HEADLINE:
            v = last.filter((pl.col("metric") == metric) & (pl.col("situation") == sit))
            row[f"{metric}" if sit == "all" else f"{metric}_{sit}"] = (
                float(v["value"][0]) if v.height else None
            )
        rows.append(row)
    return pl.DataFrame(rows, infer_schema_length=None)


def league_by_week(sb: SeasonBuild) -> pl.DataFrame:
    """The league's headline rates game week by game week (from the team-game sums)."""
    want = pl.DataFrame(
        {"metric": [m for m, _ in L.HEADLINE], "situation": [s for _, s in L.HEADLINE]}
    )
    tg = sb.tg.filter(pl.col("side") == "offense").join(want, on=["metric", "situation"])
    if tg.is_empty():
        return pl.DataFrame()
    wk = tg.group_by("week", "metric", "situation").agg(
        (pl.col("num").sum() / pl.col("den").sum()).alias("value"), pl.col("den").sum().alias("n")
    )
    return wk.sort("week", "metric")


SHARE_TREND = (
    "play_action_rate", "motion_rate", "blitz_rate", "shotgun_rate", "screen_rate",
    "deep_shot_rate", "no_huddle_rate", "dropback_rate_neutral", "proe_neutral",
)  # fmt: skip


def log_wandb(
    builds: list[SeasonBuild],
    season: int,
    through_week: int | None,
    launched_by: str | None,
    paths: DataPaths,
    smoke: bool = False,
) -> str | None:
    import wandb

    from nflengine import tracking

    seasons = [b.season for b in builds]
    run = tracking.init_run(
        group="play-calling-smoke" if smoke else "play-calling",
        job_type="playcall-build",
        config={
            "season": season,
            "seasons": seasons,
            "through_week": through_week,
            "schema_version": SCHEMA_VERSION,
            "definitions": L.definitions(),
            "dataset_version": tracking.dataset_version(paths),
            "git_commit": tracking.git_commit(),
        },
        tags=[
            "pc00",
            f"season:{season}",
            *(["history"] if len(seasons) > 1 else []),
            *(["smoke"] if smoke else []),
        ],  # fmt: skip
        launched_by=launched_by,
        name=f"playcall-build-{season}"
        + (f"-w{through_week:02d}" if through_week is not None else "")
        + (f"+{len(seasons) - 1}" if len(seasons) > 1 else ""),
    )
    ok = False
    try:
        out: dict[str, Any] = {}
        counts = pl.DataFrame(
            [
                {
                    "season": b.season,
                    "as_of_weeks": f"{b.weeks[0]}-{b.weeks[-1]}" if b.weeks else "",
                    **b.meta["rows"],
                    "ftn_join_rate": b.meta["coverage"]["ftn_join_rate"],
                    "part_join_rate": b.meta["coverage"]["part_join_rate"],
                    "games_without_ftn": len(b.meta["coverage"]["games_without_ftn"]),
                }
                for b in builds
            ]
        )
        out["build/seasons"] = wandb.Table(dataframe=counts.to_pandas())
        weekly = [
            pl.DataFrame(b.meta["coverage"]["by_week"]).with_columns(
                pl.lit(b.season).alias("season")
            )
            for b in builds
            if b.meta["coverage"]["by_week"]
        ]
        if weekly:  # none before a season's first game is curated
            cov = pl.concat(weekly, how="diagonal_relaxed")
            out["ftn/coverage_by_week"] = wandb.Table(dataframe=cov.to_pandas())
        trend = league_trend(builds)
        if trend.height:
            out["league/by_season"] = wandb.Table(dataframe=trend.to_pandas())
            if trend.height > 1:
                keys = [k for k in SHARE_TREND if k in trend.columns]
                xs, ys, names = [], [], []
                for k in keys:
                    part = trend.filter(pl.col(k).is_not_null())
                    if part.height:
                        xs.append(part["season"].to_list())
                        ys.append(part[k].to_list())
                        names.append(k)
                out["league/trend"] = wandb.plot.line_series(
                    xs=xs, ys=ys, keys=names, title="League play-calling rates by season",
                    xname="season",
                )  # fmt: skip
        cur = next((b for b in builds if b.season == season), None)
        if cur is not None:
            wk = league_by_week(cur)
            if wk.height:
                out["league/by_week"] = wandb.Table(dataframe=wk.to_pandas())
                share = wk.filter(
                    pl.concat_str("metric", pl.lit("_"), "situation")
                    .str.replace("_all$", "")
                    .is_in(list(SHARE_TREND))
                )
                names, xs, ys = [], [], []
                for (metric, sit), part in share.group_by(
                    "metric", "situation", maintain_order=True
                ):
                    part = part.sort("week")
                    names.append(metric if sit == "all" else f"{metric}_{sit}")
                    xs.append(part["week"].to_list())
                    ys.append(part["value"].to_list())
                out["league/weekly_trend"] = wandb.plot.line_series(
                    xs=xs, ys=ys, keys=names, title=f"League play-calling rates by week, {season}",
                    xname="week",
                )  # fmt: skip
        scalars: dict[str, Any] = {}
        for b in builds:
            p = f"s{b.season}"
            scalars[f"{p}/plays"] = b.meta["rows"]["plays_enriched"]
            scalars[f"{p}/team_tendency_rows"] = b.meta["rows"]["team_tendencies"]
            scalars[f"{p}/ftn_join_rate"] = b.meta["coverage"]["ftn_join_rate"]
            scalars[f"{p}/part_join_rate"] = b.meta["coverage"]["part_join_rate"]
        if trend.height:
            last = trend.filter(pl.col("season") == season)
            for k in last.columns:
                if k not in ("season", "through_week") and last.height:
                    v = last[k][0]
                    if v is not None:
                        scalars[f"league/{k}"] = v
        out.update(scalars)
        run.log(out)
        run.summary.update(scalars)
        ok = True
        return getattr(run, "url", None)
    finally:
        run.finish(exit_code=0 if ok else 1)
