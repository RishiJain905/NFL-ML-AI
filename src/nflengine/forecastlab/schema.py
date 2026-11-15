"""The Forecast lab's contract (FL00 / FL01; documentation/forecast-lab/README.md, D158).

Everything downstream (the update command, the control room's readers, the tests) reads only
what this module names: the file layout, the schemas, the targets. The model modules fill
them.

File layout, under `{data root}/forecastlab/`:

    <S>/week<NN>/availability.parquet   AVAIL_SCHEMA: one row per (player, game, view)
    <S>/week<NN>/inherit.parquet        INHERIT_SCHEMA: who gains when a player sits
    <S>/week<NN>/availability.json      meta: views made, when, model version, row counts
    <S>/week<NN>/curves.parquet         CURVE_SCHEMA: one row per (player, game, target)
    <S>/week<NN>/curves.json            meta
    <S>/availability_scoreboard.parquet AVAIL_SCORE_SCHEMA (live grades, one row per week x
                                        view x output x group)
    <S>/curves_scoreboard.parquet       CURVE_SCORE_SCHEMA (live grades)
    <S>/events/<run id>.jsonl           the update's progress events (FL02)
    backtests/availability/<view>/      predictions.parquet (AVAIL_SCHEMA, graded) + summary.json
    backtests/curves/<target key>/      predictions.parquet (CURVE_SCHEMA without `cdf`, graded)
                                        + summary.json

Models: `{data root}/models/forecastlab/<family>/<version>/` and
`models/forecastlab/<family>/production.json` (`{"version": ..., "shipped": [...]}`), family
`availability` or `curves`.

Views (availability): `tuesday` = what a Tuesday run knows (no injury report for the week);
`saturday` = after the week's final report. A week's file keeps both once both exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from nflengine.paths import DataPaths

VIEWS = ("tuesday", "saturday")
FAMILIES = ("availability", "curves")
WANDB_GROUP = "forecast-lab"
UTC = pl.Datetime("us", "UTC")


# --- where things live ----------------------------------------------------------------------
def root(paths: DataPaths) -> Path:
    return paths.root / "forecastlab"


def season_dir(paths: DataPaths, season: int) -> Path:
    return root(paths) / str(season)


def week_dir(paths: DataPaths, season: int, week: int) -> Path:
    return season_dir(paths, season) / f"week{week:02d}"


def backtest_dir(paths: DataPaths, family: str, label: str) -> Path:
    """`backtests/availability/<view>` or `backtests/curves/<target key>`. A research run
    (non-default settings) must pass its own label so it never overwrites a canonical one."""
    return root(paths) / "backtests" / family / label


def model_dir(paths: DataPaths, family: str) -> Path:
    return paths.models / "forecastlab" / family


def production_file(paths: DataPaths, family: str) -> Path:
    return model_dir(paths, family) / "production.json"


# --- availability and role (FL00) -----------------------------------------------------------
# The outputs a row can carry, and what each is scored on.
AVAIL_OUTPUTS = ("play", "snap", "tgt_share", "carry_share")

AVAIL_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "game_id": pl.String(),
    "kickoff_utc": UTC,
    "created_at": UTC,  # when the row was made (backtests: null)
    "view": pl.String(),  # tuesday | saturday
    "player_id": pl.String(),  # gsis id
    "player": pl.String(),
    "team": pl.String(),
    "opponent": pl.String(),
    "home": pl.Boolean(),
    "position": pl.String(),  # roster label
    "pgroup": pl.String(),  # QB RB WR TE DL LB CB S
    "side": pl.String(),  # off | def
    # what the view knew
    "report_status": pl.String(),  # this week's status (saturday view; null on tuesday)
    "practice_status": pl.String(),  # this week's final practice line: full | limited | dnp
    "prev_status": pl.String(),  # last week's report status (both views)
    "roster_status": pl.String(),  # latest roster status the view could see (ACT, RES ...)
    "snap_l1": pl.Float64(),  # his side's snap share in his last game (0-1)
    "snap_l2": pl.Float64(),  # mean over his last 2 games
    "games_season": pl.Int32(),
    # 1. will he play (at least one snap on his side)
    "p_play": pl.Float64(),
    "base_p_play": pl.Float64(),  # the baseline: the historical rate for his status and group
    # 2. snap share if he plays (0-1)
    "snap_p10": pl.Float64(),
    "snap_p50": pl.Float64(),
    "snap_p90": pl.Float64(),
    "base_snap": pl.Float64(),  # the baseline: his mean over his last 2 games
    # 3. opportunity share if he plays (0-1; null where it doesn't apply)
    "tgt_share": pl.Float64(),  # WR TE RB: share of the team's targets
    "base_tgt_share": pl.Float64(),  # the baseline: his rolling share
    "carry_share": pl.Float64(),  # RB (and QB): share of the team's carries
    "base_carry_share": pl.Float64(),
    "exp_snap": pl.Float64(),  # p_play * snap_p50: the expected share counting the risk
    "at_risk": pl.Boolean(),  # the tab's "might sit" list
    "model_version": pl.String(),
    "trained_through": pl.String(),  # "2026-w09"
    # filled once the game is played (null before)
    "played": pl.Boolean(),
    "actual_snap": pl.Float64(),
    "actual_tgt_share": pl.Float64(),
    "actual_carry_share": pl.Float64(),
}

INHERIT_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "game_id": pl.String(),
    "view": pl.String(),
    "team": pl.String(),
    "kind": pl.String(),  # targets | carries | snaps
    "out_player_id": pl.String(),  # the player who might sit
    "out_player": pl.String(),
    "out_position": pl.String(),
    "out_p_play": pl.Float64(),
    "out_share": pl.Float64(),  # his own expected share if he plays
    "player_id": pl.String(),  # the teammate who gains
    "player": pl.String(),
    "position": pl.String(),
    "share_if_in": pl.Float64(),  # the teammate's share when the first one plays
    "share_if_out": pl.Float64(),  # ... and when he sits
    "delta": pl.Float64(),  # share_if_out - share_if_in
    "rank": pl.Int32(),  # 1 = gains most, within (out player, kind)
}

AVAIL_SCORE_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "view": pl.String(),
    "output": pl.String(),  # one of AVAIL_OUTPUTS
    "group": pl.String(),  # ALL, or a position group
    "n": pl.Int32(),
    "brier_model": pl.Float64(),  # output == play
    "brier_baseline": pl.Float64(),
    "mae_model": pl.Float64(),  # the share outputs (points of share, 0-1 scale)
    "mae_baseline": pl.Float64(),
    "coverage_80": pl.Float64(),  # output == snap
    "mode": pl.String(),  # live | backtest
    "graded_at": UTC,
}


# --- outcome curves (FL01) ------------------------------------------------------------------
@dataclass(frozen=True)
class CurveTarget:
    """One player-model target that gets a curve. `key` is the player model's own key, so the
    walk-forward history is `runs/backtests/player/<key>/predictions_players.parquet` and the
    live rows are the week's `predictions_players.parquet` filtered on (`target`, `group`)."""

    key: str  # "rec_yds-wrte"
    name: str  # "rec_yds" (PRED_SCHEMA `target`)
    group: str  # "WR/TE" (PRED_SCHEMA `group`)
    kind: str  # amount | count
    label: str
    marks: tuple[float, ...]  # the standard thresholds it is graded on ("over 49.5")
    band: float  # the half-width of the "lands within" band around the projection
    floor: float  # the lowest value the stat can take on the grid


CURVE_TARGETS: tuple[CurveTarget, ...] = (
    CurveTarget(
        "pass_yds-qb",
        "pass_yds",
        "QB",
        "amount",
        "passing yards",
        (174.5, 199.5, 224.5, 249.5, 274.5, 299.5),
        15.0,
        -10.0,
    ),  # fmt: skip
    CurveTarget(
        "rush_yds-qb",
        "rush_yds",
        "QB",
        "amount",
        "rushing yards",
        (9.5, 19.5, 29.5, 39.5, 49.5),
        7.0,
        -15.0,
    ),  # fmt: skip
    CurveTarget(
        "rush_yds-rb",
        "rush_yds",
        "RB",
        "amount",
        "rushing yards",
        (24.5, 39.5, 49.5, 59.5, 74.5, 99.5),
        7.0,
        -15.0,
    ),  # fmt: skip
    CurveTarget(
        "scrim_yds-rb",
        "scrim_yds",
        "RB",
        "amount",
        "scrimmage yards",
        (24.5, 49.5, 74.5, 99.5, 124.5),
        7.0,
        -15.0,
    ),  # fmt: skip
    CurveTarget(
        "rec_yds-wrte",
        "rec_yds",
        "WR/TE",
        "amount",
        "receiving yards",
        (14.5, 24.5, 39.5, 49.5, 59.5, 74.5, 99.5),
        7.0,
        -15.0,
    ),  # fmt: skip
    CurveTarget(
        "receptions-wrte",
        "receptions",
        "WR/TE",
        "count",
        "receptions",
        (1.5, 2.5, 3.5, 4.5, 5.5, 6.5),
        1.0,
        0.0,
    ),  # fmt: skip
    CurveTarget(
        "targets-wrte",
        "targets",
        "WR/TE",
        "count",
        "targets",
        (2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5),
        1.0,
        0.0,
    ),  # fmt: skip
    CurveTarget(
        "receptions-rb",
        "receptions",
        "RB",
        "count",
        "receptions",
        (0.5, 1.5, 2.5, 3.5, 4.5),
        1.0,
        0.0,
    ),  # fmt: skip
    CurveTarget(
        "carries-rb",
        "carries",
        "RB",
        "count",
        "carries",
        (4.5, 7.5, 9.5, 12.5, 14.5, 17.5),
        2.0,
        0.0,
    ),  # fmt: skip
    CurveTarget(
        "tackles-lbs",
        "tackles",
        "LB/S",
        "count",
        "tackles",
        (2.5, 3.5, 4.5, 5.5, 6.5, 7.5),
        1.0,
        0.0,
    ),  # fmt: skip
)
CURVE_BY_KEY = {t.key: t for t in CURVE_TARGETS}

MARK = pl.Struct(
    {
        "mark": pl.Float64,  # 49.5
        "p_over": pl.Float64,  # the curve's chance of more than the mark
        "bell_p_over": pl.Float64,  # the bell-curve baseline's chance, for comparison
    }
)

CURVE_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "game_id": pl.String(),
    "kickoff_utc": UTC,
    "created_at": UTC,
    "player_id": pl.String(),
    "player": pl.String(),
    "team": pl.String(),
    "opponent": pl.String(),
    "position": pl.String(),
    "group": pl.String(),
    "target": pl.String(),  # CurveTarget.name
    "target_key": pl.String(),  # CurveTarget.key
    "target_label": pl.String(),
    "kind": pl.String(),
    # the published projection the curve is built on (copied, never changed)
    "p10": pl.Float64(),
    "p50": pl.Float64(),
    "p90": pl.Float64(),
    "mean": pl.Float64(),
    "source_version": pl.String(),  # the player model's `model_version`
    # the curve: P(Y <= grid_lo + i) for i = 0 .. len(cdf) - 1, on whole numbers; the last
    # value is 1.0. Backtest files leave `cdf` out (too large); the summary columns stay.
    "grid_lo": pl.Int32(),
    "cdf": pl.List(pl.Float64),
    "q05": pl.Float64(),
    "q10": pl.Float64(),
    "q25": pl.Float64(),
    "q50": pl.Float64(),
    "q75": pl.Float64(),
    "q90": pl.Float64(),
    "q95": pl.Float64(),
    "band": pl.Float64(),  # the band's half-width (CurveTarget.band)
    # chance of landing within +-band of the projection (round(p50); counts: round(mean))
    "p_band": pl.Float64(),
    "bell_p_band": pl.Float64(),  # the same from the bell-curve baseline
    "marks": pl.List(MARK),
    "n_analogs": pl.Int32(),  # how many past projections the curve was built from
    "model_version": pl.String(),
    "actual": pl.Float64(),  # filled once the game is played
    "played": pl.Boolean(),
}

CURVE_SCORE_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "target_key": pl.String(),
    "n": pl.Int32(),
    "crps_model": pl.Float64(),
    "crps_bell": pl.Float64(),  # baseline 1: a bell curve through P10 / P90
    "crps_own": pl.Float64(),  # baseline 2: the player's own past games
    "brier_model": pl.Float64(),  # mean over the target's marks
    "brier_bell": pl.Float64(),
    "brier_own": pl.Float64(),
    "band_pred": pl.Float64(),  # mean predicted chance of landing in the band
    "band_hit": pl.Float64(),  # how often it did
    "pit_ece": pl.Float64(),  # calibration of the whole curve (0 = perfect)
    "mode": pl.String(),  # live | backtest
    "graded_at": UTC,
}


# --- helpers --------------------------------------------------------------------------------
def conform(df: pl.DataFrame, schema: dict[str, pl.DataType]) -> pl.DataFrame:
    """The schema's columns, in order, cast; a missing column becomes null."""
    if df.height == 0:
        return pl.DataFrame(schema=schema)
    cols = []
    for c, t in schema.items():
        cols.append(pl.col(c).cast(t) if c in df.columns else pl.lit(None, dtype=t).alias(c))
    return df.select(cols)


def read_parquet(path: Path, columns: list[str] | None = None) -> pl.DataFrame:
    """Read a whole parquet file into memory **without holding it open** (no memory map): a
    weekly run's curate step replaces curated files, and a held file makes that fail on
    Windows (D100). Use this for every curated or published file."""
    import pyarrow.parquet as pq

    t = pq.read_table(path, columns=columns, memory_map=False)
    df = pl.from_arrow(t)
    assert isinstance(df, pl.DataFrame)
    return df
