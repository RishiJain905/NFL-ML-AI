"""The player model's targets and its prediction file contract (plan P06; documentation/11).

`TARGETS` lists every P06 target (one model per target x position group). Each weekly run
writes one row per (player, game, target) to `runs/<season>/week<NN>/predictions_players.
parquet` with `PRED_SCHEMA`; walk-forward backtests write the same rows (with `actual` /
`played` filled) to `runs/backtests/player/<target>-<group>/predictions_players.parquet`.
Everything downstream (watch list, payload, report card, graph, scoreboard) reads only
these columns.

Pools (who a target is projected and scored for; documentation/11: only games he played):
- QB: the team's main QB in the game (most dropbacks);
- RB: running backs with offensive snaps; WR/TE: wide receivers and tight ends with
  offensive snaps;
- EDGE/DL: defensive linemen **and linebackers** with defensive snaps (nflverse labels
  many edge rushers LB, so off-ball LBs are in the pool too; their history says they
  rarely pressure);
- LB/S: linebackers and safeties with defensive snaps.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

GROUPS = ("QB", "RB", "WR/TE", "EDGE/DL", "LB/S")
GROUP_SLUG = {"QB": "qb", "RB": "rb", "WR/TE": "wrte", "EDGE/DL": "edge", "LB/S": "lbs"}
GROUP_PGROUPS = {
    "QB": ("QB",),
    "RB": ("RB",),
    "WR/TE": ("WR", "TE"),
    "EDGE/DL": ("DL", "LB"),
    "LB/S": ("LB", "S"),
}
DEFENSE = ("EDGE/DL", "LB/S")


@dataclass(frozen=True)
class Target:
    name: str  # "rec_yds"
    group: str  # "WR/TE"
    column: str  # history / actual column ("receiving_yards")
    kind: str  # "amount" (quantile models) | "count" (Poisson mean + negative binomial)
    label: str  # "receiving yards" (reader-facing)
    unit: str  # digest/format.py metric unit: "yards" | "count" | "epa"
    main: bool = False  # the group's main stat (players to watch)
    start_season: int = 2013  # first season with this target's data
    # weeks its label publishes late (PFR pressures: 1). A Tuesday run doesn't have last
    # week's label yet, so neither its features nor the training rows use it
    label_lag: int = 0

    @property
    def key(self) -> str:
        """File / run label: `rec_yds-wrte`."""
        return f"{self.name}-{GROUP_SLUG[self.group]}"


TARGETS: tuple[Target, ...] = (
    Target("pass_yds", "QB", "passing_yards", "amount", "passing yards", "yards", main=True),
    Target("pass_epa", "QB", "epa_per_db", "amount", "EPA per dropback", "epa"),
    Target("rush_yds", "RB", "rushing_yards", "amount", "rushing yards", "yards", main=True),
    Target("carries", "RB", "carries", "count", "carries", "count"),
    Target("receptions", "RB", "receptions", "count", "receptions", "count"),
    Target("scrim_yds", "RB", "scrimmage_yards", "amount", "scrimmage yards", "yards"),
    Target("rec_yds", "WR/TE", "receiving_yards", "amount", "receiving yards", "yards", main=True),
    Target("targets", "WR/TE", "targets", "count", "targets", "count"),
    Target("receptions", "WR/TE", "receptions", "count", "receptions", "count"),
    Target("pressures", "EDGE/DL", "pressures", "count", "pressures", "count", True, 2018, 1),
    Target("tackles", "LB/S", "tackles", "count", "tackles", "count", main=True),
)
TARGET_BY_KEY = {t.key: t for t in TARGETS}
MAIN_TARGETS = {t.group: t for t in TARGETS if t.main}


def get_target(name: str, group: str | None = None) -> list[Target]:
    """Targets matching a name (`receptions` -> RB and WR/TE), a key (`receptions-rb`) or
    `all`; optionally only one group."""
    if name in TARGET_BY_KEY:
        found = [TARGET_BY_KEY[name]]
    elif name == "all":
        found = list(TARGETS)
    else:
        found = [t for t in TARGETS if t.name == name]
    if group is not None:
        found = [t for t in found if t.group == group]
    if not found:
        raise ValueError(f"unknown target {name!r} (group {group}); see TARGETS")
    return found


def pool_expr(group: str) -> pl.Expr:
    """True for history rows in `group`'s pool (needs `pgroup`, `played_off`, `played_def`,
    `main_qb` from `features.player_data.player_history`)."""
    pg = pl.col("pgroup").is_in(list(GROUP_PGROUPS[group]))
    if group == "QB":
        return pg & pl.col("main_qb")
    if group in DEFENSE:
        return pg & pl.col("played_def")
    return pg & pl.col("played_off")


DRIVER = pl.Struct(
    {
        "feature": pl.String,
        "phrase": pl.String,  # readable, from features/descriptions.yaml (no numbers)
        "value": pl.Float64,  # the feature's value for this player
        "contribution": pl.Float64,  # SHAP contribution to the projection (target units)
    }
)

PRED_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "game_id": pl.String(),
    "kickoff_utc": pl.Datetime("us", "UTC"),
    "created_at": pl.Datetime("us", "UTC"),  # when the projection was made (live: run time)
    "player_id": pl.String(),
    "player": pl.String(),
    "team": pl.String(),
    "opponent": pl.String(),
    "home": pl.Boolean(),
    "position": pl.String(),  # roster label ("WR", "OLB" ...)
    "pgroup": pl.String(),  # QB RB WR TE DL LB S
    "group": pl.String(),  # model group (GROUPS)
    "target": pl.String(),  # Target.name
    "target_label": pl.String(),
    "kind": pl.String(),
    "unit": pl.String(),
    "is_main": pl.Boolean(),
    "p10": pl.Float64(),
    "p50": pl.Float64(),  # the projection
    "p90": pl.Float64(),
    "mean": pl.Float64(),  # expected value (counts: the Poisson mean; amounts: = p50)
    "baseline": pl.Float64(),  # player rolling baseline (documentation/11)
    "baseline_source": pl.String(),  # rolling | last_season | role (rookies / no history)
    "baseline_season_mean": pl.Float64(),  # season-to-date mean (null before his 1st game)
    "baseline_role": pl.Float64(),  # position average for his snap-share role
    # the baseline as the same kind of projection, the one MAE is scored on (D65): amounts =
    # baseline; counts = the negative-binomial median at the baseline mean (same dispersion)
    "baseline_p50": pl.Float64(),
    # amounts: p50 - baseline; counts: mean - baseline (a small count's median is too coarse)
    "outperformance": pl.Float64(),
    "outperf_z": pl.Float64(),  # outperformance / the target's typical baseline miss
    "games_history": pl.Int32(),  # games in this pool before the projection (all seasons)
    "games_season": pl.Int32(),  # ... this season
    "snap_share_l2": pl.Float64(),  # mean snap share (his side) over his last 2 games
    "role_ok": pl.Boolean(),  # snap share >= 50% over the last 2 games
    "role_change": pl.Boolean(),  # a regular teammate in his group out (vacated usage)
    "vacated_share": pl.Float64(),  # usage share of teammates who missed the last game
    "injury_status": pl.String(),  # his report status for the week (null = not listed)
    "confidence": pl.String(),  # high | medium | low (low: thin history or a wide range)
    "drivers": pl.List(DRIVER),  # top 3 SHAP drivers, largest |contribution| first
    "model_version": pl.String(),  # "player-model-v1:2026-w04" (backtests: ":backtest")
    "feature_hash": pl.String(),
    "trained_through": pl.String(),  # last as-of week in the training rows ("2026-w03")
    "actual": pl.Float64(),  # filled once the game is played (null live)
    "played": pl.Boolean(),  # in the target's pool in that game (null until played)
}


# The accuracy scoreboard (documentation/11): one row per (season, week, target, group).
# Live rows: `runs/<season>/accuracy_scoreboard.parquet` (the week's saved pre-kickoff
# projections, scored the next week); backtest rows: `runs/backtests/player/scoreboard.parquet`.
SCOREBOARD_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "target": pl.String(),
    "position_group": pl.String(),
    "target_label": pl.String(),
    "n_scored": pl.Int32(),  # projected players who played
    "n_not_played": pl.Int32(),  # projected players who didn't (logged, not scored)
    "mae_model": pl.Float64(),
    "mae_baseline": pl.Float64(),  # player rolling baseline (`baseline_p50`, D65)
    "improvement_pct": pl.Float64(),  # 100 * (mae_baseline - mae_model) / mae_baseline
    "coverage_80": pl.Float64(),  # share of actuals inside P10-P90
    "brier_model": pl.Float64(),  # yes/no targets (P08); null in P06
    "brier_baseline": pl.Float64(),
    "calibration_ece": pl.Float64(),
    "mode": pl.String(),  # live | backtest
}


STATUS_FILE = "player_status.json"  # the weekly player step's verdict for the run folder


def write_status(run_dir, status: str, detail: str = "") -> None:
    """Record the player step's result next to the projections (`ok` / `degraded`): the
    digest falls back to the heuristic when the latest refit failed, even if an older
    projection file from an earlier run of the week is still there (Sol review)."""
    import datetime as dt
    import json
    from pathlib import Path

    folder = Path(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / STATUS_FILE).write_text(
        json.dumps(
            {
                "status": status,
                "detail": detail,
                "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            }
        ),
        encoding="utf-8",
    )


def read_status(run_dir) -> str | None:
    """`ok`, `degraded`, or None when no player step has run for that folder."""
    import json
    from pathlib import Path

    path = Path(run_dir) / STATUS_FILE
    if not path.exists():
        return None
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("status"))
    except (ValueError, OSError):
        return None


def empty_predictions() -> pl.DataFrame:
    return pl.DataFrame(schema=PRED_SCHEMA)


def conform(df: pl.DataFrame) -> pl.DataFrame:
    """Columns of `PRED_SCHEMA`, in order, cast (missing columns become null)."""
    if df.height == 0:
        return empty_predictions()
    cols = []
    for c, t in PRED_SCHEMA.items():
        if c in df.columns:
            cols.append(pl.col(c).cast(t))
        else:
            cols.append(pl.lit(None, dtype=t).alias(c))
    return df.select(cols)


def score_predictions(preds: pl.DataFrame, hist: pl.DataFrame) -> pl.DataFrame:
    """Fill `actual` / `played` from the player history table (`player_history`).

    `played` = the player is in the target's pool for that game (a main QB, offensive /
    defensive snaps); `actual` = the target column (null when he didn't play, and for
    pressures when PFR hasn't published that game yet). Games not in `hist` (not played
    yet) leave both null.
    """
    if preds.is_empty():
        return preds
    out = []
    played_games = set(hist["game_id"].unique().to_list())
    for (tname, group), part in preds.group_by("target", "group", maintain_order=True):
        tgt = get_target(str(tname), str(group))[0]
        h = hist.select(
            "player_id",
            "game_id",
            pool_expr(tgt.group).alias("_played"),
            pl.col(tgt.column).cast(pl.Float64).alias("_actual"),
        )
        j = part.drop("actual", "played", strict=False).join(
            h, on=["player_id", "game_id"], how="left"
        )
        game_done = pl.col("game_id").is_in(list(played_games))
        j = j.with_columns(
            pl.when(game_done).then(pl.col("_played").fill_null(False)).alias("played"),
        ).with_columns(
            pl.when(pl.col("played")).then(pl.col("_actual")).alias("actual"),
        )
        out.append(j.drop("_played", "_actual"))
    cols = list(preds.columns) + [c for c in ("actual", "played") if c not in preds.columns]
    return pl.concat(out, how="diagonal_relaxed").select(cols)
