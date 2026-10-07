"""W&B runs for the player model: `nfl features player`, `nfl backtest player`,
`nfl tune player`, `nfl train player`, `nfl scoreboard` (plan P06; documentation/08, 11).

All runs go to group `track1-player`, tagged with the position group:
- **backtest** (job_type `backtest`), one run per target x group: walk-forward by week,
  2019-2025 reported after `BURN_IN` seasons (their predictions calibrate the 80% range
  and the count dispersion). Per-week and cumulative MAE (model, rolling baseline,
  season mean), improvement %, and range coverage are logged live (`bt/*`, step
  `bt/step`); each reported season also logs a LightGBM training curve (`lgb/*`: the
  season's first fit replayed with the previous season held out). The end adds summaries,
  a by-season table, the weekly scoreboard rows, feature importance, a SHAP summary and
  the predictions. Predictions go to `runs/backtests/player/<key>/` and their scoreboard
  rows to `runs/backtests/player/scoreboard.parquet`.
- **tune** (job_type `tune`): a W&B grid sweep per target over the tree settings, scored
  on a 2017-2018 walk-forward (never the reported weeks).
- **train** (job_type `train`): the weekly production fit. Every target is refit for each
  week of the current season up to N, continuing the saved backtest's walk-forward
  history (so the range and dispersion come from earlier weeks' misses), predicts week N,
  writes `runs/<season>/week<NN>/predictions_players.parquet`, saves the boosters under
  `models/player-model/<season>-w<NN>/` and logs the W&B artifact `player-model`.
- **scoreboard** (job_type `eval`): scores a played week's saved pre-kickoff projections
  and appends the `accuracy_scoreboard` rows (documentation/11).

P08 adds the new targets (`player_schema.TARGETS`, phase `p08`) to the same machinery:
- **probability targets** (a touchdown, an interception, a pass defended) log Brier of the
  calibrated probability against the player's rolling rate (`bt/brier_*`, `bt/cum_brier_*`),
  a by-season Brier chart and a decile reliability diagram (`reliability_diagram`); they have
  no MAE, range or coverage;
- **event counts** (pass TDs, interceptions, sacks) log both: MAE as before, plus the same
  Brier / reliability for P(at least one);
- every backtest summary ends with the **ship rule** (`ship_rule`, keys `ship_*`), the
  pre-registered bar a P08 target must clear (documentation/plans/P08): it only reports,
  the lead decides what ships;
- `TARGET_DEFAULTS` holds per-target tree settings tuned on 2017-2018 (`settings.yaml`
  `player_model.per_target` wins over them);
- `build_player_data(targets=...)` builds only the rows and feature families those targets
  need, so the weekly run of the P06 targets builds exactly the P06 table.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml

from nflengine import clock
from nflengine.features import player as PF
from nflengine.features.asof import AsOf, last_asof_week
from nflengine.features.player_data import (
    PlayerInputs,
    load_inputs,
    player_history,
    with_playoff_week,
)
from nflengine.models import metrics as M
from nflengine.models.backtest import SampleWeights, walk_forward, week_keys
from nflengine.models.player_model import (
    GROUP_PREFIXES,
    PlayerModelConfig,
    PlayerWeekModel,
    add_position_flags,
    feature_columns,
    feature_hash,
    fit_player_model,
    top_drivers,
)
from nflengine.models.player_schema import (
    GROUP_SLUG,
    PRED_SCHEMA,
    SCOREBOARD_SCHEMA,
    Target,
    conform,
    event_expr,
    live_targets,
    score_predictions,
)
from nflengine.ops import events
from nflengine.paths import DataPaths, ensure_data_root
from nflengine.settings import get_config
from nflengine.tracking import dataset_version, git_commit, init_run

GROUP = "track1-player"
FAMILY = "player-model"
MODEL_VERSION = "v1"
REPORT_SEASONS = tuple(range(2019, 2026))
BURN_IN = 2
TUNE_SEASONS = (2017, 2018)
MIN_TRAIN_ROWS = 2000
PRED_FILE = "predictions_players.parquet"
UPDATE_PRED_FILE = "predictions_players_update.parquet"  # Saturday injury update (P07)
SCOREBOARD_FILE = "accuracy_scoreboard.parquet"
FEATURE_FILE = "player_features.parquet"
DESCRIPTIONS = Path(__file__).resolve().parents[1] / "features" / "descriptions.yaml"
ROLE_SNAP = 0.5  # documentation/04: a real role = snap share >= 50% over the last 2 games
LAST_REG_WEEK = 18  # since 2021 (17 before: `last_reg_week`); history is regular season only
PLAYOFF_MIN_GAMES = 3  # a playoff pick's role needs this many games this season (D91)
ROLE_CHANGE_TGT = 0.15  # target share left open (`open_tgt`) that counts as a role change
ROLE_CHANGE_CAR = 0.25
# P08 per-target settings that live in code until the ship decision moves them to
# `settings.yaml` (`player_model.per_target`, which wins over these); filled from the 2017-2018
# tuning grid (documentation/model_cards/player-p08.md)
TARGET_DEFAULTS: dict[str, dict[str, Any]] = {
    "pass_tds-qb": {"num_leaves": 15, "min_data_in_leaf": 200, "n_estimators": 150},  # ct2dr946
    "ints-qb": {"num_leaves": 7, "min_data_in_leaf": 200, "n_estimators": 300},  # pus3fn5t
    "rush_yds-qb": {"num_leaves": 15, "min_data_in_leaf": 50, "n_estimators": 300},  # ud7xp3xt
    "td-rb": {"num_leaves": 4, "min_data_in_leaf": 300, "n_estimators": 100},  # zv4j4md1
    "td-wrte": {"num_leaves": 4, "min_data_in_leaf": 100, "n_estimators": 100},  # coyymj3b
    "sacks-edge": {"num_leaves": 31, "min_data_in_leaf": 50, "n_estimators": 150},  # 1wdr6q3m
    "qb_hits-edge": {"num_leaves": 15, "min_data_in_leaf": 50, "n_estimators": 300},  # bu4pv1s8
    "cov_tgt-cbs": {"num_leaves": 7, "min_data_in_leaf": 50, "n_estimators": 300},  # zgwo3kuu
    "cov_cmp-cbs": {"num_leaves": 15, "min_data_in_leaf": 200, "n_estimators": 150},  # smgtm7m6
    "cov_yds-cbs": {"num_leaves": 7, "min_data_in_leaf": 200, "n_estimators": 150},  # y4yt569d
    "int-cbs": {"num_leaves": 4, "min_data_in_leaf": 300, "n_estimators": 100},  # eweebi9l
    "pd-cbs": {"num_leaves": 4, "min_data_in_leaf": 300, "n_estimators": 100},  # 9figg6pe
}


def model_config(target: Target | None = None, **overrides: Any) -> PlayerModelConfig:
    """`settings.yaml` player_model (+ its per-target overrides) and the sample weights."""
    cfg = get_config()
    pm = dict(cfg.player_model or {})
    per = (pm.pop("per_target", None) or {}).get(target.key, {}) if target else {}
    pm.pop("per_target", None)
    pm.pop("live_targets", None)  # which targets run weekly, not a model setting
    if target and target.key in TARGET_DEFAULTS:
        per = {**TARGET_DEFAULTS[target.key], **per}
    weights = SampleWeights.from_config(cfg.training.get("sample_weights"))
    if overrides.get("weights") is not None:
        weights = overrides.pop("weights")
    overrides.pop("weights", None)
    return PlayerModelConfig.from_config({**pm, **per}, weights=weights, **overrides)


def load_phrases() -> dict[str, str]:
    if not DESCRIPTIONS.exists():
        return {}
    return {
        str(k): str(v) for k, v in (yaml.safe_load(DESCRIPTIONS.read_text("utf-8")) or {}).items()
    }


# ---- data ------------------------------------------------------------------------------------


@dataclass
class PlayerData:
    inp: PlayerInputs
    hist: pl.DataFrame
    feats: pl.DataFrame
    live_key: AsOf | None = None


def game_context_frames(
    paths: DataPaths,
    inp: PlayerInputs,
    live_key: AsOf | None = None,
    run_date: dt.date | None = None,
    log: Callable[[str], None] = print,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(P03 context per game x team, expected QB per game x team).

    Past seasons: the canonical model-only walk-forward backtest. The current season (the
    last one in `inp`): always a model-only walk-forward refit here (cheap: ridge), with
    the live expected-starter resolver for `live_key`'s week.
    """
    from nflengine.models.game_runs import load_frame, run_walk_forward
    from nflengine.models.game_runs import model_config as game_config

    bt_path = paths.runs / "backtests" / "game" / "model_only" / "predictions_games.parquet"
    parts = []
    current = inp.last_season
    if bt_path.exists():  # finished seasons; the current one is always refit here
        parts.append(pl.read_parquet(bt_path).filter(pl.col("season") < current))
    eq = PF.expected_qbs_from_games(inp.game_features)
    frame = load_frame(paths, live_key=live_key, run_date=run_date, log=lambda *_: None)
    upto = live_key.week if live_key and live_key.season == current else None
    f = frame.filter(
        (pl.col("season") < current)
        | ((pl.col("season") == current) & (pl.col("week") <= (upto or 99)))
    )
    keys = [live_key] if live_key else []
    cur = run_walk_forward(f, game_config("model_only"), [current], extra_keys=keys)
    cur = cur.filter(pl.col("season") == current)
    parts.append(cur)
    log(f"game context: {current} walk-forward, {cur.height} games")
    if live_key is not None:  # the live resolver's expected starters for that week
        live = PF.expected_qbs_from_games(
            frame.filter((pl.col("season") == live_key.season) & (pl.col("week") == live_key.week))
        )
        eq = pl.concat([eq.join(live, on=["game_id", "team"], how="anti"), live])
    ctx = PF.game_context(pl.concat(parts, how="diagonal_relaxed")) if parts else None
    return (ctx if ctx is not None else pl.DataFrame()), eq


def build_player_data(
    paths: DataPaths | None = None,
    live_key: AsOf | None = None,
    run_date: dt.date | None = None,
    log: Callable[[str], None] = print,
    targets: Sequence[Target] | None = None,
) -> PlayerData:
    """Inputs, history and every shared feature for history rows (+ the live week's
    expected players when `live_key` is set).

    `targets`: build rows and features only for what these targets need (the weekly run
    passes its live targets, so a P06-only run builds no CB rows and no `cvg_*` family and
    produces exactly the P06 table); `None` (`nfl features player`) builds everything."""
    paths = paths or ensure_data_root()
    inp = load_inputs(paths, log=log)
    hist = player_history(inp)
    if live_key is not None:  # P10: a playoff week's games, injuries and rosters (no-op REG)
        inp = with_playoff_week(inp, live_key.season, live_key.week, paths)
    pgroups = PF.target_pgroups(targets) if targets is not None else None
    coverage = targets is None or any(t.group == "CB/S" for t in targets)
    rows = PF.history_rows(hist, pgroups)
    ctx, eq = game_context_frames(paths, inp, live_key, run_date, log)
    if live_key is not None:
        qbs = eq.join(
            inp.games.filter(
                (pl.col("season") == live_key.season) & (pl.col("week") == live_key.week)
            ).select("game_id"),
            on="game_id",
        ).select("team", "qb_id")
        # a rehearsal (P10) replays a past week as of its Tuesday: the pinned clock picks
        # the games still to come, and their box-score rows (who actually played) give way
        # to the expected players, as on a live Tuesday; rows of the week's games already
        # played by then stay, as they would have live
        now = clock.utc_now() if clock.is_pinned() else None
        up = PF.upcoming_rows(
            inp, hist, live_key.season, live_key.week, qbs, pgroups=pgroups, now=now
        )
        if now is not None:
            rows = rows.filter(
                ~(
                    (pl.col("season") == live_key.season)
                    & (pl.col("week") == live_key.week)
                    & (pl.col("kickoff_utc") > now)
                )
            )
        rows = pl.concat([rows.join(up, on=["player_id", "game_id"], how="anti"), up])
        log(f"live rows for {live_key}: {up.height} players")
    log("building player features ...")
    feats = PF.build_features(
        inp, hist, rows, context=ctx, expected_qbs=eq, log=log, coverage=coverage
    )
    return PlayerData(inp, hist, feats, live_key)


def write_feature_table(feats: pl.DataFrame, paths: DataPaths | None = None) -> Path:
    from nflengine.curate.build import write_duckdb_views

    paths = paths or ensure_data_root()
    out = paths.features / FEATURE_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    feats.write_parquet(out, compression="zstd")
    with contextlib.suppress(Exception):  # views are a convenience
        write_duckdb_views(paths)
    return out


def target_data(data: PlayerData, target: Target) -> pl.DataFrame:
    return add_position_flags(PF.target_frame(data.feats, data.hist, target))


def target_features(frame: pl.DataFrame, target: Target) -> list[str]:
    """The model features of one target: the shared families, plus its group's own family
    (CB/S: `cvg_*`). A group without one gets exactly what `feature_columns(frame)` gives."""
    if target.group in GROUP_PREFIXES:
        return feature_columns(frame, target)
    return feature_columns(frame)


# ---- assembling prediction rows ---------------------------------------------------------------


def tuesdays(games: pl.DataFrame) -> pl.DataFrame:
    """(season, week) -> 14:00 UTC on the Tuesday before the week's first kickoff (the
    simulated run time of a backtest week, as in `digest.run.tuesday_before`)."""
    first = games.group_by("season", "week").agg(pl.col("kickoff_utc").min().alias("_k"))
    day = (pl.col("_k") - pl.duration(hours=5)).dt.date()
    tue = day - pl.duration(days=(day.dt.weekday() - 2) % 7)
    return first.select(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        (tue.cast(pl.Datetime("us")) + pl.duration(hours=14))
        .dt.replace_time_zone("UTC")
        .alias("created_at"),
    )


def last_reg_week(season: pl.Expr) -> pl.Expr:
    """The season's last regular-season week: 18 since 2021, 17 before (Sol review, P10)."""
    return pl.when(season >= 2021).then(LAST_REG_WEEK).otherwise(LAST_REG_WEEK - 1)


def _playoff_row(p: pl.DataFrame) -> pl.Expr:
    """A playoff row (after the season's last regular-season week): only live playoff weeks
    and rehearsals of them have one (history is regular season), so backtests and
    regular-season weeks never match."""
    if "week" not in p.columns:
        return pl.lit(False)
    if "season" not in p.columns:
        return pl.col("week") > LAST_REG_WEEK
    return pl.col("week") > last_reg_week(pl.col("season"))


def _role_ok(p: pl.DataFrame) -> pl.Expr:
    """A real role: snap share >= `ROLE_SNAP` over his last 2 games. In a playoff week (P10,
    D91) also over his last 3, with 3+ games this season: the history is regular season
    only, so a playoff row's last games include the final regular-season week, when many
    teams rest starters, and a backup who started only that game (or a late call-up with 1-2
    games) looked like a regular. 2025 Wild Card rehearsal: 5 of the 20 picks had ~0% of
    snaps before week 18 and ~0% in the playoff game; the 3-game rule removed them, and 4
    of the 5 non-players left had 2 or fewer games. Regular-season rows are unchanged."""
    ok = pl.col("use_snap_l2").fill_null(0) >= ROLE_SNAP
    if "use_snap_l3" not in p.columns:
        return ok
    steady = (pl.col("use_snap_l3").fill_null(0) >= ROLE_SNAP) & (
        pl.col("own_n_season").fill_null(0) >= PLAYOFF_MIN_GAMES
    )
    return ok & (~_playoff_row(p) | steady)


def assemble(
    preds: pl.DataFrame,
    frame: pl.DataFrame,
    target: Target,
    *,
    model_version: str,
    fhash: str,
    trained_through: str,
    created_at: pl.DataFrame | dt.datetime,
    phrases: dict[str, str] | None = None,
    features: Sequence[str] = (),
) -> pl.DataFrame:
    """Model output rows (`PlayerWeekModel`) -> `PRED_SCHEMA` rows."""
    info = frame.select(
        "player_id",
        "game_id",
        "player",
        "team",
        "opponent",
        "home",
        "position",
        "pgroup",
        "kickoff_utc",
        "own_n_career",
        "own_n_season",
        "use_snap_l2",
        *(["use_snap_l3"] if "use_snap_l3" in frame.columns else []),
        "baseline_source",
        "baseline_season_mean",
        "baseline_role",
        "avail_status",
        "open_tgt",
        "open_car",
    )
    p = preds.join(info, on=["player_id", "game_id"], how="left")
    if isinstance(created_at, dt.datetime):
        p = p.with_columns(pl.lit(created_at).cast(pl.Datetime("us", "UTC")).alias("created_at"))
    else:
        p = p.join(created_at, on=["season", "week"], how="left")
    # usage left open by teammates who missed the last game OR are ruled out, each counted
    # once (adding the two features double-counted a teammate in both; Sol review)
    off_tgt = pl.col("open_tgt").fill_null(0)
    off_car = pl.col("open_car").fill_null(0)
    if target.group == "RB":
        vac, change = off_car, off_car >= ROLE_CHANGE_CAR
    elif target.group == "WR/TE":
        vac, change = off_tgt, off_tgt >= ROLE_CHANGE_TGT
    else:
        vac, change = pl.lit(0.0), pl.lit(False)
    proj = pl.col("p50") if target.kind == "amount" else pl.col("mean")
    # a probability target has no range: it never reads as "wide" (its confidence rests on
    # his history alone)
    width = pl.lit(0.0) if target.kind == "prob" else pl.col("p90") - pl.col("p10")
    p = p.with_columns(
        pl.lit(target.group).alias("group"),
        pl.lit(target.name).alias("target"),
        pl.lit(target.label).alias("target_label"),
        pl.lit(target.kind).alias("kind"),
        pl.lit(target.unit).alias("unit"),
        pl.lit(target.main).alias("is_main"),
        (proj - pl.col("baseline")).alias("outperformance"),
        ((proj - pl.col("baseline")) / pl.col("scale").clip(1e-6)).alias("outperf_z"),
        pl.col("own_n_career").fill_null(0).cast(pl.Int32).alias("games_history"),
        pl.col("own_n_season").fill_null(0).cast(pl.Int32).alias("games_season"),
        pl.col("use_snap_l2").alias("snap_share_l2"),
        _role_ok(p).alias("role_ok"),
        # a playoff week: the teammate "missing" from the last game was rested in week 18
        (change & ~_playoff_row(p)).alias("role_change"),
        vac.alias("vacated_share"),
        pl.col("avail_status")
        .replace_strict(
            {1.0: "Questionable", 2.0: "Doubtful", 3.0: "Out"}, default=None, return_dtype=pl.String
        )
        .alias("injury_status"),
        pl.lit(model_version).alias("model_version"),
        pl.lit(fhash).alias("feature_hash"),
        pl.lit(trained_through).alias("trained_through"),
        pl.col("y").alias("actual"),
        pl.col("y").is_not_null().alias("played"),
    )
    # range width relative to the projection's own size (a 0-4 pressure range for a 2.5
    # mean is normal; comparing raw widths flagged every real pass rusher as low confidence)
    if target.kind == "prob":  # a null range from the model's NaN, a flat zero here
        p = p.with_columns(pl.col(c).fill_nan(None) for c in ("p10", "p50", "p90", "baseline_p50"))
    p = p.with_columns(
        pl.col(c).fill_nan(None) for c in ("p_ge1", "p_ge2", "baseline_p_ge1") if c in p.columns
    )
    rel = width / pl.max_horizontal(proj.abs(), pl.col("scale"))
    med = p.group_by("season", "week").agg(rel.median().alias("_rmed"))
    p = p.join(med, on=["season", "week"], how="left").with_columns(
        pl.when(
            (pl.col("games_history") < 3)
            | (pl.col("baseline_source") == "role")
            | (pl.col("games_season") == 0)
            | (rel > 1.5 * pl.col("_rmed"))
        )
        .then(pl.lit("low"))
        .when((pl.col("games_season") >= 4) & (rel <= pl.col("_rmed")))
        .then(pl.lit("high"))
        .otherwise(pl.lit("medium"))
        .alias("confidence")
    )
    if "_contrib" in p.columns and features:
        vals = frame.select("player_id", "game_id", *features)
        pv = p.select("player_id", "game_id").join(vals, on=["player_id", "game_id"], how="left")
        contrib = np.vstack(p["_contrib"].to_list()) if p.height else np.zeros((0, len(features)))
        drivers = top_drivers(
            contrib, features, pv.select(features).to_numpy().astype(float), phrases or {}
        )
        p = p.with_columns(pl.Series("drivers", drivers, dtype=PRED_SCHEMA["drivers"]))
    return conform(p)


# ---- scoring ---------------------------------------------------------------------------------


EVENT_KEYS = ["season", "week", "target", "group"]


def _event_rows(preds: pl.DataFrame) -> pl.DataFrame:
    """Scored rows that carry an event probability and its baseline (probability targets;
    counts with `event_probs`), with `_ev` = did the "P(>= 1)" event happen (1.0 / 0.0)."""
    if "p_ge1" not in preds.columns or "baseline_p_ge1" not in preds.columns:
        return preds.clear().with_columns(pl.lit(None, pl.Float64).alias("_ev"))
    return preds.filter(
        pl.col("played").fill_null(False)
        & pl.col("actual").is_not_null()
        & pl.col("p_ge1").is_not_null()
        & pl.col("baseline_p_ge1").is_not_null()
    ).with_columns(event_expr().alias("_ev"))


def _event_metrics(preds: pl.DataFrame) -> pl.DataFrame:
    """Brier and ECE of `p_ge1` and of `baseline_p_ge1` per (season, week, target, group)."""
    schema = {
        **{"season": pl.Int32, "week": pl.Int32, "target": pl.String, "group": pl.String},
        **dict.fromkeys(("brier_model", "brier_baseline", "calibration_ece"), pl.Float64),
    }
    rows = []
    for keys, part in _event_rows(preds).group_by(EVENT_KEYS, maintain_order=True):
        rows.append(
            {
                **dict(zip(EVENT_KEYS, keys, strict=True)),
                "brier_model": M.brier(part["p_ge1"], part["_ev"]),
                "brier_baseline": M.brier(part["baseline_p_ge1"], part["_ev"]),
                "calibration_ece": M.ece(part["p_ge1"], part["_ev"]),
            }
        )
    return pl.DataFrame(rows, schema=schema)


def scoreboard_rows(preds: pl.DataFrame, mode: str) -> pl.DataFrame:
    """One `SCOREBOARD_SCHEMA` row per (season, week, target, group) from scored rows.
    Probability targets and event counts also get `brier_model` / `brier_baseline` /
    `calibration_ece` (of `p_ge1` against the "at least one" event); a probability target's
    MAE and coverage columns stay null."""
    if preds.is_empty():
        return pl.DataFrame(schema=SCOREBOARD_SCHEMA)
    played = pl.col("played").fill_null(False) & pl.col("actual").is_not_null()
    err_m = (pl.col("actual") - pl.col("p50")).abs()
    err_b = (pl.col("actual") - pl.col("baseline_p50")).abs()
    inside = (pl.col("actual") >= pl.col("p10")) & (pl.col("actual") <= pl.col("p90"))
    g = preds.group_by("season", "week", "target", "group", "target_label").agg(
        played.sum().cast(pl.Int32).alias("n_scored"),
        (pl.col("played").is_not_null() & ~pl.col("played").fill_null(False))
        .sum()
        .cast(pl.Int32)
        .alias("n_not_played"),
        err_m.filter(played & pl.col("baseline").is_not_null()).mean().alias("mae_model"),
        err_b.filter(played & pl.col("baseline").is_not_null()).mean().alias("mae_baseline"),
        inside.filter(played).mean().alias("coverage_80"),
    )
    ev = _event_metrics(preds)
    if ev.height:
        g = g.join(ev, on=EVENT_KEYS, how="left")
    return (
        g.with_columns(
            (100 * (pl.col("mae_baseline") - pl.col("mae_model")) / pl.col("mae_baseline")).alias(
                "improvement_pct"
            ),
            pl.col("group").alias("position_group"),
            pl.lit(mode).alias("mode"),
        )
        .filter(pl.col("n_scored") > 0)
        .select(
            [
                pl.col(c).cast(t)
                if c in g.columns or c in ("improvement_pct", "position_group", "mode")
                else pl.lit(None, dtype=t).alias(c)
                for c, t in SCOREBOARD_SCHEMA.items()
            ]
        )
        .sort("season", "week", "target", "position_group")
    )


def fitted_params(fit) -> dict[str, Any]:
    """The fitted numbers outside the booster files (saved in the weekly `meta.json`, so a
    `player-model` / `team-model` artifact reproduces its projections): conformal `shift`,
    NB `dispersion` and `tail`, and the calibration layer (`kind`, Platt `slope` /
    `intercept`, rows `n`; an isotonic layer is a step function and is only named)."""
    cal = getattr(fit, "calibrator", None)
    return {
        "shift": float(fit.shift),
        "dispersion": float(fit.dispersion),
        "tail": float(fit.tail),
        "calibrator": None
        if cal is None or cal.kind == "none"
        else {
            "kind": cal.kind,
            "slope": float(cal.slope) if cal.kind == "platt" else None,
            "intercept": float(cal.intercept) if cal.kind == "platt" else None,
            "n": int(cal.n),
        },
    }


def upsert_scoreboard(path: Path, rows: pl.DataFrame) -> pl.DataFrame:
    """Replace the rows with the same (season, week, target, group, mode); keep the rest.

    Parallel backtest chains and the weekly player and team fits all upsert into shared
    scoreboard files, so the read-modify-write runs under an OS lock (`ops.lock.file_lock`:
    released by the OS if the holder dies, never taken from a live holder) and the file is
    replaced atomically (an interrupted write never leaves a truncated file; Sol review)."""
    from nflengine.ops.lock import file_lock, write_parquet_atomic

    key = ["season", "week", "target", "position_group", "mode"]
    with file_lock(path):
        old = pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=SCOREBOARD_SCHEMA)
        if rows.height:
            old = old.join(rows.select(key), on=key, how="anti")
        out = pl.concat([old.cast(SCOREBOARD_SCHEMA), rows.cast(SCOREBOARD_SCHEMA)]).sort(  # type: ignore[arg-type]
            "season", "week", "target", "position_group"
        )
        write_parquet_atomic(out, path)
    return out


def _quantile_bins(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> pl.DataFrame:
    """Reliability bins with (about) equal counts: rare events (an interception: 7%) sit in
    the first equal-width bin or two, which hides the shape; deciles of the predicted
    probability show it. Columns: bin, n, mean_prob, mean_outcome."""
    ok = np.isfinite(p) & np.isfinite(y)
    p, y = p[ok], y[ok]
    schema = {"bin": pl.Int32, "n": pl.Int64, "mean_prob": pl.Float64, "mean_outcome": pl.Float64}
    if not p.size:
        return pl.DataFrame(schema=schema)
    order = np.argsort(p, kind="stable")
    rows = []
    for b, idx in enumerate(np.array_split(order, min(n_bins, p.size))):
        if idx.size:
            rows.append((b, int(idx.size), float(p[idx].mean()), float(y[idx].mean())))
    return pl.DataFrame(rows, schema=schema, orient="row")


def ece_quantile(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    """Expected calibration error over `_quantile_bins`."""
    t = _quantile_bins(p, y, n_bins)
    if t.is_empty():
        return float("nan")
    w = t["n"].to_numpy() / t["n"].sum()
    return float(np.sum(w * np.abs(t["mean_prob"].to_numpy() - t["mean_outcome"].to_numpy())))


def event_summary(r: pl.DataFrame) -> dict[str, float]:
    """Pooled metrics of the "at least one" event (probability targets; counts with
    `event_probs`) over scored rows: the base rate, Brier and ECE of the model's `p_ge1` and of
    the baseline's, Brier of the constant base rate (climatology) and the model's log loss.
    Empty when the rows carry no event probability."""
    e = _event_rows(r)
    if e.is_empty():
        return {}
    p, pb = e["p_ge1"].to_numpy(), e["baseline_p_ge1"].to_numpy()
    ev = e["_ev"].to_numpy()
    rate = float(ev.mean())
    bm, bb = M.brier(p, ev), M.brier(pb, ev)
    zero = pb < 0.005  # a rolling rate of exactly zero: the baseline says "never"
    out = {
        "event_n": float(e.height),
        "event_rate": rate,
        "event_mean_p": float(p.mean()),
        "event_mean_p_baseline": float(pb.mean()),
        "brier_model": bm,
        "brier_baseline": bb,
        "brier_improvement_pct": 100 * (bb - bm) / bb,
        "brier_climatology": rate * (1 - rate),
        "log_loss_model": M.log_loss(p, ev),
        "ece_model": M.ece(p, ev),
        "ece_baseline": M.ece(pb, ev),
        "ece_q10_model": ece_quantile(p, ev),
        "ece_q10_baseline": ece_quantile(pb, ev),
        "baseline_zero_share": float(zero.mean()),
    }
    if zero.any():
        out["baseline_zero_event_rate"] = float(ev[zero].mean())
    return out


def summarize(preds: pl.DataFrame) -> dict[str, float]:
    """Pooled metrics over scored rows (model vs every baseline on the same rows).

    Amounts and counts: MAE against the baseline (a median for counts, D65), range coverage.
    Probability targets: no MAE or coverage (they have no P50 or range). Both kinds add the
    event metrics (`event_summary`) when the rows carry an event probability."""
    r = preds.filter(
        pl.col("played").fill_null(False)
        & pl.col("actual").is_not_null()
        & pl.col("baseline").is_not_null()
    )
    if r.is_empty():
        return {"n_scored": 0.0}
    is_prob = "kind" in r.columns and bool((r["kind"] == "prob").fill_null(False).all())
    out = {"n_scored": float(r.height)}
    if not is_prob:
        y = r["actual"]
        mae = lambda c: float((y - r[c]).abs().mean())  # noqa: E731
        out.update(
            {
                "mae_model": mae("p50"),
                "mae_baseline": mae("baseline_p50"),
                "mae_baseline_mean": mae("baseline"),  # the raw rolling mean (D65: not the bar)
                "coverage_80": float(((y >= r["p10"]) & (y <= r["p90"])).mean()),
            }
        )
        sm = r.filter(pl.col("baseline_season_mean").is_not_null())
        if sm.height:
            out["mae_season_mean_same_rows"] = float(
                (sm["actual"] - sm["baseline_season_mean"]).abs().mean()
            )
            out["mae_model_season_mean_rows"] = float((sm["actual"] - sm["p50"]).abs().mean())
        out["improvement_pct"] = (
            100 * (out["mae_baseline"] - out["mae_model"]) / out["mae_baseline"]
        )
        if "mae_season_mean_same_rows" in out:
            out["improvement_vs_season_mean_pct"] = (
                100
                * (out["mae_season_mean_same_rows"] - out["mae_model_season_mean_rows"])
                / out["mae_season_mean_same_rows"]
            )
    actual_out = (r["actual"] - r["baseline"]).to_numpy()
    pred_out = r["outperformance"].to_numpy()
    ok = np.isfinite(actual_out) & np.isfinite(pred_out)
    if ok.sum() > 10:
        from scipy.stats import spearmanr

        out["spearman_outperformance"] = float(spearmanr(pred_out[ok], actual_out[ok]).statistic)
    out["share_role_baseline"] = (
        float((r["baseline_source"] == "role").mean()) if "baseline_source" in r.columns else 0.0
    )
    out.update(event_summary(r))
    return out


def by_season(preds: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for (s,), part in preds.group_by("season", maintain_order=True):
        rows.append({"season": int(s), **summarize(part)})
    return pl.DataFrame(rows).sort("season") if rows else pl.DataFrame()


# ---- backtest --------------------------------------------------------------------------------


class LiveLogger:
    """`walk_forward` on_week callback: per-week + cumulative curves for reported weeks.

    Amounts and counts log MAE (model vs the baseline's median), improvement and range
    coverage; probability targets log Brier (model vs the baseline's rate) and its cumulative
    curves; counts with `event_probs` log both."""

    def __init__(
        self,
        run,
        report: Sequence[int],
        to_rows: Callable[[pl.DataFrame], pl.DataFrame],
        target: Target | None = None,
    ):
        self.run = run
        self.report = set(report)
        self.to_rows = to_rows
        self.target = target
        self.step = 0
        self.done: list[pl.DataFrame] = []
        if run is not None:
            run.define_metric("bt/step")
            run.define_metric("bt/*", step_metric="bt/step")

    def _event(self, df: pl.DataFrame) -> tuple[float, float] | None:
        """(Brier of p_ge1, Brier of the baseline's) over rows that have both, or None."""
        t = self.target
        if t is None or not (t.kind == "prob" or t.event_probs):
            return None
        e = df.filter(pl.col("p_ge1").is_not_null() & pl.col("baseline_p_ge1").is_not_null())
        if e.is_empty():
            return None
        ev = (e["y"] >= (0.5 if t.kind == "prob" else t.event_min)).cast(pl.Float64)
        return M.brier(e["p_ge1"], ev), M.brier(e["baseline_p_ge1"], ev)

    def __call__(self, key: AsOf, out: pl.DataFrame) -> None:
        if key.season not in self.report:
            return
        r = out.filter(pl.col("y").is_not_null() & pl.col("baseline").is_not_null())
        if r.is_empty():
            return
        self.done.append(r)
        cum = pl.concat(self.done)
        self.step += 1
        log: dict[str, float] = {
            "bt/step": self.step,
            "bt/season": key.season,
            "bt/week": key.week,
            "bt/n": r.height,
        }
        if self.target is None or self.target.kind != "prob":

            def m(df: pl.DataFrame, col: str) -> float:
                return float((df["y"] - df[col]).abs().mean())

            def cov(df: pl.DataFrame) -> float:
                return float(((df["y"] >= df["p10"]) & (df["y"] <= df["p90"])).mean())

            wm, wb = m(r, "p50"), m(r, "baseline_p50")
            cm, cb = m(cum, "p50"), m(cum, "baseline_p50")
            log.update(
                {
                    "bt/mae_model": wm,
                    "bt/mae_baseline": wb,
                    "bt/improvement_pct": 100 * (wb - wm) / wb if wb else 0.0,
                    "bt/coverage_80": cov(r),
                    "bt/cum_mae_model": cm,
                    "bt/cum_mae_baseline": cb,
                    "bt/cum_improvement_pct": 100 * (cb - cm) / cb if cb else 0.0,
                    "bt/cum_coverage_80": cov(cum),
                }
            )
        week_ev, cum_ev = self._event(r), self._event(cum)
        if week_ev and cum_ev:
            log.update(
                {
                    "bt/brier_model": week_ev[0],
                    "bt/brier_baseline": week_ev[1],
                    "bt/cum_brier_model": cum_ev[0],
                    "bt/cum_brier_baseline": cum_ev[1],
                    "bt/cum_brier_improvement_pct": (
                        100 * (cum_ev[1] - cum_ev[0]) / cum_ev[1] if cum_ev[1] else 0.0
                    ),
                }
            )
        rp = r["range_param"][0] if "range_param" in r.columns else None
        if rp is not None and np.isfinite(rp):
            log["bt/range_param"] = float(rp)
        if self.run is not None:
            self.run.log(log)


def _curve(
    frame: pl.DataFrame, target: Target, feats: list[str], config: PlayerModelConfig, season: int
) -> dict[str, list[float]] | None:
    """The season's opening fit replayed with the previous season held out: per-round
    training and validation loss (P50 / Poisson objective)."""
    lab = frame.filter(pl.col("y").is_not_null())
    train = lab.filter(pl.col("season") < season - 1)
    valid = lab.filter(pl.col("season") == season - 1)
    if train.height < MIN_TRAIN_ROWS or valid.is_empty():
        return None
    w = config.weights.for_rows(train["season"], season - 1)
    evals: dict[str, dict] = {}
    fit_player_model(train, w, target, feats, config, valid=valid, evals=evals)
    key = "q50" if target.kind == "amount" else "mean"
    hist = evals.get(key, {})
    metric = next(iter(hist.get("train", {})), None)
    if metric is None:
        return None
    return {"train": hist["train"][metric], "valid": hist["valid"][metric], "metric": metric}


@dataclass
class BacktestResult:
    preds: pl.DataFrame
    summary: dict[str, Any]
    url: str | None
    saved_to: Path | None


MARKET_PREFIX = "team_mkt_"  # closing-line features (leakage rule 5: report with and without)


def backtest_label(target: Target, no_market: bool = False, **overrides: Any) -> str:
    """Run name and D: folder: the bare target key only for the canonical configuration, so
    a research variant (`_nomarket`, overrides) never overwrites the files the digest reads."""
    parts = [target.key] + (["nomarket"] if no_market else [])
    for k, v in sorted(overrides.items()):
        if v is not None:
            parts.append(f"{k}-{v}")
    return "_".join(parts)


def run_backtest(
    target: Target,
    data: PlayerData | None = None,
    report_seasons: Sequence[int] = REPORT_SEASONS,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    save: bool = True,
    use_wandb: bool = True,
    tags: Sequence[str] = (),
    num_threads: int | None = None,
    no_market: bool = False,
    **overrides: Any,
) -> BacktestResult:
    """Walk-forward backtest of one target (model vs baselines on the same rows).

    `no_market` drops the market features (`team_mkt_*`): historical lines are closing
    lines, more informed than what a Tuesday run sees, so doc 04's leakage rule 5 asks for
    results with and without them."""
    import wandb

    paths = ensure_data_root()
    data = data or load_saved_data(paths, log)
    label = backtest_label(target, no_market=no_market, **overrides)
    config = model_config(target, num_threads=num_threads, **overrides)
    frame = target_data(data, target)
    feats = target_features(frame, target)
    if no_market:
        feats = [f for f in feats if not f.startswith(MARKET_PREFIX)]
    fhash = feature_hash(feats)
    first = min(report_seasons) - BURN_IN
    keys = week_keys(frame.filter(pl.col("y").is_not_null()), range(first, max(report_seasons) + 1))
    cfg = {
        "model": f"player model {MODEL_VERSION} (documentation/04 C)",
        "target": target.name,
        "group": target.group,
        "kind": target.kind,
        **config.as_dict(),
        "feature_hash": fhash,
        "market_features": not no_market,
        "n_features": len(feats),
        "features": feats,
        "report_seasons": [min(report_seasons), max(report_seasons)],
        "burn_in_seasons": BURN_IN,
        "dataset_version": dataset_version(paths),
        "git_commit": git_commit(),
    }
    run = None
    if use_wandb:
        run = init_run(
            GROUP,
            "backtest",
            config=cfg,
            tags=[
                target.phase,
                "player",
                f"group:{GROUP_SLUG[target.group]}",
                f"target:{target.name}",
                *tags,
                *(["no-market"] if no_market and "no-market" not in tags else []),
            ],
            launched_by=launched_by,
            name=f"backtest-{label}",
        )
    try:
        model = PlayerWeekModel(target, feats, config, explain=True)
        logger = LiveLogger(run, report_seasons, lambda df: df, target=target)
        created = tuesdays(data.inp.games)
        if run is not None:
            for s in report_seasons:
                c = _curve(frame, target, feats, config, s)
                if c is None:
                    continue
                n = len(c["train"])
                run.log(
                    {
                        f"lgb/curve_{s}": wandb.plot.line_series(
                            xs=list(range(1, n + 1)),
                            ys=[c["train"], c["valid"]],
                            keys=["train", f"valid ({s - 1} held out)"],
                            title=f"{target.label} ({target.group}): {c['metric']} per boosting "
                            f"round, fit before {s - 1}",
                            xname="boosting round",
                        )
                    }
                )
                break  # one curve up front; the rest after the walk-forward
        log(f"{target.key}: {len(keys)} weekly refits, {len(feats)} features ...")
        out = walk_forward(
            frame,
            keys,
            model,
            label="y",
            weights=config.weights,
            min_train_rows=MIN_TRAIN_ROWS,
            on_week=logger,
        )
        version = f"{FAMILY}-{MODEL_VERSION}:backtest"
        preds = assemble(
            out,
            frame,
            target,
            model_version=version,
            fhash=fhash,
            trained_through="walk-forward",
            created_at=created,
            phrases=load_phrases(),
            features=feats,
        )
        extra = {"amount": ["_p10_raw", "_p90_raw"]}.get(target.kind, [])
        if target.kind == "prob" or target.event_probs:
            extra = ["_p_raw"]
        raw = out.select("season", "week", "player_id", "game_id", *extra, "range_param", "scale")
        reported = preds.filter(pl.col("season").is_in(list(report_seasons)))
        summary = summarize(reported)
        summary.update(uncalibrated_event_metrics(target, out, report_seasons))
        per = by_season(reported)
        if per.height:
            summary.update(season_wins(target, per))
        summary.update(ship_rule(target, summary))
        board = scoreboard_rows(reported, "backtest")
        if run is not None:
            run.summary.update(summary)
            _final_logs(
                run, reported, per, board, model, feats, out, target, frame, config, report_seasons
            )
            url = run.url
        else:
            url = None
        log(f"{target.key}: {json.dumps({k: round(v, 4) for k, v in summary.items()})}")
    finally:
        if run is not None:
            run.finish()
    saved = None
    if save:
        root = paths.runs / "backtests" / "player" / label
        root.mkdir(parents=True, exist_ok=True)
        preds.join(raw, on=["season", "week", "player_id", "game_id"], how="left").write_parquet(
            root / PRED_FILE, compression="zstd"
        )
        (root / "summary.json").write_text(
            json.dumps({**summary, "config": cfg, "url": url}, indent=2, default=str)
        )
        if label == target.key:  # canonical runs feed the backtest scoreboard
            upsert_scoreboard(paths.runs / "backtests" / "player" / "scoreboard.parquet", board)
        saved = root
    return BacktestResult(preds, summary, url, saved)


SHIP_MIN_SEASONS = 5  # of the 7 reported: the P08 ship rule (documentation/plans/P08)
SHIP_COVERAGE = (0.75, 0.88)  # the 80% range must hold between these
SHIP_ECE_FLOOR = 0.02  # ECE may be up to max(this, the baseline's)
SHIP_MAE_SLACK_PCT = 0.5  # event counts: MAE may be worse than the baseline's by this much


def uncalibrated_event_metrics(
    target: Target, out: pl.DataFrame, report_seasons: Sequence[int]
) -> dict[str, float]:
    """Brier and ECE of the probability *before* the calibration layer (a classifier's raw
    output; a count's negative-binomial P(>= 1)), on the reported rows that have an outcome:
    what the layer is worth (`brier_raw` vs `brier_model`)."""
    if "_p_raw" not in out.columns or not (target.kind == "prob" or target.event_probs):
        return {}
    e = out.filter(
        pl.col("season").is_in(list(report_seasons))
        & pl.col("y").is_not_null()
        & pl.col("baseline").is_not_null()
        & pl.col("_p_raw").is_not_null()
        & ~pl.col("_p_raw").is_nan()
    )
    if e.is_empty():
        return {}
    ev = (e["y"] >= (0.5 if target.kind == "prob" else target.event_min)).cast(pl.Float64)
    return {
        "brier_raw": M.brier(e["_p_raw"], ev),
        "ece_raw": M.ece(e["_p_raw"], ev),
        "event_mean_p_raw": float(e["_p_raw"].mean()),
    }


def season_wins(target: Target, per: pl.DataFrame) -> dict[str, float]:
    """In how many reported seasons the model beat its baseline: on MAE (amounts, counts) or,
    for probability targets, on Brier; event counts also get the Brier count."""
    out = {"seasons": float(per.height)}
    if target.kind != "prob":
        out["seasons_beating_baseline"] = float((per["mae_model"] < per["mae_baseline"]).sum())
    has_brier = "brier_model" in per.columns and "brier_baseline" in per.columns
    if has_brier and (target.kind == "prob" or target.event_probs):
        wins = float((per["brier_model"] < per["brier_baseline"]).sum())
        out["seasons_beating_brier"] = wins
        if target.kind == "prob":
            out["seasons_beating_baseline"] = wins
    return out


def ship_rule(target: Target, summary: dict[str, float]) -> dict[str, float]:
    """The P08 pre-registered ship rule over a backtest's pooled summary (1.0 = met):
    - amounts / counts: pooled MAE below the baseline's (counts: its median, D65), better in
      >= 5 seasons, 80% range coverage within 0.75-0.88;
    - event counts: Brier of `p_ge1` below the baseline's pooled and in >= 5 seasons,
      ECE <= max(0.02, the baseline's), MAE not worse than the baseline's by more than 0.5%;
    - probability targets: Brier below the baseline's pooled and in >= 5 seasons,
      ECE <= max(0.02, the baseline's).
    Returns each criterion as `ship_<name>` and the overall `ship_pass`; the lead makes the
    final call."""
    g = summary.get
    checks: dict[str, bool] = {}
    if target.kind != "prob":
        if target.event_probs:
            checks["mae_not_worse"] = g("improvement_pct", -1e9) >= -SHIP_MAE_SLACK_PCT
        else:
            checks["mae_better"] = g("mae_model", float("inf")) < g("mae_baseline", 0.0)
            checks["seasons"] = g("seasons_beating_baseline", 0.0) >= SHIP_MIN_SEASONS
            lo, hi = SHIP_COVERAGE
            checks["coverage"] = lo <= g("coverage_80", -1.0) <= hi
    if target.kind == "prob" or target.event_probs:
        checks["brier_better"] = g("brier_model", float("inf")) < g("brier_baseline", 0.0)
        checks["brier_seasons"] = g("seasons_beating_brier", 0.0) >= SHIP_MIN_SEASONS
        checks["ece"] = g("ece_model", float("inf")) <= max(SHIP_ECE_FLOOR, g("ece_baseline", 0.0))
    out = {f"ship_{k}": float(v) for k, v in checks.items()}
    out["ship_pass"] = float(all(checks.values()))
    return out


def backtest_table_row(target: Target, s: dict[str, float]) -> list[str]:
    """The six cells `nfl backtest player` prints per target: key, rows scored, model metric,
    baseline metric, improvement and the range coverage. Amounts and counts show MAE;
    probability targets show Brier of the calibrated probability and the ECE instead of the
    range (they have none)."""
    n = f"{int(s.get('n_scored', 0)):,}"
    nan = float("nan")
    if target.kind == "prob":
        return [
            target.key,
            n,
            f"Brier {s.get('brier_model', nan):.4f}",
            f"Brier {s.get('brier_baseline', nan):.4f}",
            f"{s.get('brier_improvement_pct', nan):+.1f}%",
            f"ECE {s.get('ece_model', nan):.3f}",
        ]
    return [
        target.key,
        n,
        f"{s.get('mae_model', nan):.3f}",
        f"{s.get('mae_baseline', nan):.3f}",
        f"{s.get('improvement_pct', nan):+.1f}%",
        f"{100 * s.get('coverage_80', nan):.0f}%",
    ]


def reliability_tables(reported: pl.DataFrame) -> pl.DataFrame:
    """Decile reliability bins of the model's `p_ge1` and of the baseline's, one table with a
    `predictor` column (`model` / `rolling baseline`); empty without event probabilities."""
    e = _event_rows(reported)
    if e.is_empty():
        return pl.DataFrame()
    ev = e["_ev"].to_numpy()
    parts = [
        _quantile_bins(e[col].to_numpy(), ev).with_columns(pl.lit(name).alias("predictor"))
        for col, name in (("p_ge1", "model"), ("baseline_p_ge1", "rolling baseline"))
    ]
    return pl.concat(parts)


def _final_logs(run, reported, per, board, model, feats, out, target, frame, config, report):
    import wandb

    tables: dict[str, Any] = {
        "by_season": wandb.Table(dataframe=per.to_pandas()),
        "accuracy_scoreboard": wandb.Table(dataframe=board.to_pandas()),
    }
    if per.height and target.kind != "prob":
        tables["mae_by_season"] = wandb.plot.line_series(
            xs=per["season"].to_list(),
            ys=[per["mae_model"].to_list(), per["mae_baseline"].to_list()],
            keys=["model", "rolling baseline"],
            title=f"{target.label} ({target.group}): MAE by season (lower is better)",
            xname="season",
        )
    if per.height and "brier_model" in per.columns:
        tables["brier_by_season"] = wandb.plot.line_series(
            xs=per["season"].to_list(),
            ys=[per["brier_model"].to_list(), per["brier_baseline"].to_list()],
            keys=["model", "rolling baseline"],
            title=f"{target.label} ({target.group}): Brier of P(at least one) by season "
            "(lower is better)",
            xname="season",
        )
    rel = reliability_tables(reported)
    if rel.height:
        m = rel.filter(pl.col("predictor") == "model")
        tables["reliability_table"] = wandb.Table(dataframe=rel.to_pandas())
        tables["reliability_diagram"] = wandb.plot.line_series(
            xs=[float(x) for x in m["mean_prob"]],
            ys=[list(m["mean_outcome"]), list(m["mean_prob"])],
            keys=["model: how often it happened", "perfect calibration"],
            title=f"Reliability, {target.label} ({target.group}): actual rate vs predicted "
            "probability (deciles)",
            xname="predicted probability",
        )
    fit = model.last
    if fit is not None:
        main = fit.boosters["q50" if target.kind == "amount" else "mean"]
        gain = main.feature_importance("gain")
        imp = (
            pl.DataFrame({"feature": feats, "gain": gain.astype(float)})
            .sort("gain", descending=True)
            .head(25)
        )
        tables["feature_importance"] = wandb.plot.bar(
            wandb.Table(dataframe=imp.to_pandas()),
            "feature",
            "gain",
            title=f"{target.label}: feature importance (gain, last fit)",
        )
    if "_contrib" in out.columns:
        last_season = out.filter(pl.col("season") == max(report))
        if last_season.height:
            c = np.abs(np.vstack(last_season["_contrib"].to_list())).mean(axis=0)
            shap = (
                pl.DataFrame({"feature": feats, "mean_abs_shap": c.astype(float)})
                .sort("mean_abs_shap", descending=True)
                .head(20)
            )
            tables["shap_summary"] = wandb.plot.bar(
                wandb.Table(dataframe=shap.to_pandas()),
                "feature",
                "mean_abs_shap",
                title=f"{target.label}: mean |SHAP| in {max(report)} (target units)",
            )
    for s in report[1:]:
        c = _curve(frame, target, feats, config, s)
        if c is None:
            continue
        n = len(c["train"])
        tables[f"lgb/curve_{s}"] = wandb.plot.line_series(
            xs=list(range(1, n + 1)),
            ys=[c["train"], c["valid"]],
            keys=["train", f"valid ({s - 1} held out)"],
            title=f"{target.label} ({target.group}): {c['metric']} per boosting round, fit "
            f"before {s - 1}",
            xname="boosting round",
        )
    shown = ["p_ge1"] if target.kind == "prob" else ["p10", "p50", "p90"]
    if target.kind == "count" and target.event_probs:
        shown.append("p_ge1")
    sample = reported.filter(pl.col("season") == max(report)).select(
        "season",
        "week",
        "player",
        "team",
        "opponent",
        *shown,
        "baseline",
        "actual",
        "confidence",
    )
    tables["predictions"] = wandb.Table(
        dataframe=sample.with_columns(pl.selectors.float().round(2)).to_pandas()
    )
    run.log(tables)


# ---- saved feature table -------------------------------------------------------------------


def load_saved_data(
    paths: DataPaths | None = None, log: Callable[[str], None] = print
) -> PlayerData:
    """The feature table from `nfl features player` (history rows), plus inputs/history."""
    paths = paths or ensure_data_root()
    path = paths.features / FEATURE_FILE
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run `nfl features player` first")
    inp = load_inputs(paths, log=log)
    hist = player_history(inp)
    return PlayerData(inp, hist, pl.read_parquet(path))


# ---- tuning ----------------------------------------------------------------------------------


TUNE_GRID = {
    "num_leaves": [7, 15, 31],
    "min_data_in_leaf": [50, 200],
    "n_estimators": [150, 300],
}
# yes / no targets (a touchdown 15-25%, an interception 8%): smaller trees, bigger leaves and
# fewer rounds, because a rare event leaves little to split on and the boosting overfits it
TUNE_GRID_PROB = {
    "num_leaves": [4, 8, 16],
    "min_data_in_leaf": [100, 300],
    "n_estimators": [100, 250],
}


def tune_objective(
    frame: pl.DataFrame, target: Target, feats: list[str], config: PlayerModelConfig
) -> dict[str, float]:
    """Walk-forward over `TUNE_SEASONS` (every other week, to keep it cheap): MAE of the
    P50 / NB-median model, never the reported 2019-2025 weeks. Weeks with fewer than
    `MIN_TRAIN_ROWS` training rows are skipped (pressures: only late 2018 counts)."""
    lab = frame.filter(pl.col("y").is_not_null())
    keys = [k for k in week_keys(lab, TUNE_SEASONS) if k.week % 2 == 1]
    errs, base = [], []
    is_prob = target.kind == "prob"
    for k in keys:
        train = lab.filter(
            (pl.col("season") < k.season)
            | ((pl.col("season") == k.season) & (pl.col("week") < k.week))
        )
        test = lab.filter((pl.col("season") == k.season) & (pl.col("week") == k.week))
        if target.label_lag:  # a week-late label isn't known at the key yet (Sol review)
            kt = (k.season - 2000) * 22 + k.week
            tt = (pl.col("season").cast(pl.Int32) - 2000) * 22 + pl.col("week").cast(pl.Int32)
            train = train.filter(tt < kt - target.label_lag)
        if train.height < MIN_TRAIN_ROWS:  # pressures (PFR, 2018+) only has 2018 to learn from
            continue
        w = config.weights.for_rows(train["season"], k.season)
        fit = fit_player_model(train, w, target, feats, config, main_only=True)
        main = fit.boosters["q50" if target.kind == "amount" else "mean"]
        pred = main.predict(test.select([pl.col(c).cast(pl.Float64) for c in feats]).to_numpy())
        if is_prob:  # squared error of the raw probability (no history to calibrate on)
            y = test["y"].to_numpy()
            errs.append((y - pred) ** 2)
            base.append((y - test["baseline"].to_numpy().astype(float)) ** 2)
            continue
        if target.kind == "count":  # score the NB median, as the backtest does
            from nflengine.models.player_model import count_quantiles, nb_dispersion

            r = nb_dispersion(
                train["y"].to_numpy(),
                main.predict(train.select([pl.col(c).cast(pl.Float64) for c in feats]).to_numpy()),
            )
            pred = count_quantiles(pred, r, (0.5,))[:, 0]
        errs.append(np.abs(test["y"].to_numpy() - pred))
        bm = test["baseline"]
        if target.kind == "count":
            from nflengine.models.player_model import baseline_p50

            bm = baseline_p50(bm, target, r)
        base.append(np.abs(test["y"].to_numpy() - bm.to_numpy().astype(float)))
    e, b = np.concatenate(errs), np.concatenate(base)
    ok = np.isfinite(b)
    if is_prob:
        return {
            "tune/brier_model": float(e[ok].mean()),
            "tune/brier_baseline": float(b[ok].mean()),
            "tune/improvement_pct": float(100 * (b[ok].mean() - e[ok].mean()) / b[ok].mean()),
            "tune/n": float(e.size),
        }
    return {
        "tune/mae_model": float(e.mean()),
        "tune/mae_baseline": float(b[ok].mean()),
        "tune/improvement_pct": float(100 * (b[ok].mean() - e[ok].mean()) / b[ok].mean()),
        "tune/n": float(e.size),
    }


def run_tune(
    target: Target,
    data: PlayerData | None = None,
    grid: dict[str, list] | None = None,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    num_threads: int | None = None,
) -> pl.DataFrame:
    """W&B grid sweep for one target (`TUNE_GRID`; `TUNE_GRID_PROB` for yes / no targets),
    scored on `TUNE_SEASONS` (MAE; probability targets: Brier)."""
    from nflengine.tracking import run_sweep

    data = data or load_saved_data(log=log)
    frame = target_data(data, target)
    feats = target_features(frame, target)
    grid = grid or (TUNE_GRID_PROB if target.kind == "prob" else TUNE_GRID)
    metric = "tune/brier_model" if target.kind == "prob" else "tune/mae_model"
    results: list[dict[str, Any]] = []

    def one() -> None:
        run = init_run(
            GROUP,
            "tune",
            config={
                "target": target.name,
                "group": target.group,
                "tune_seasons": list(TUNE_SEASONS),
                "feature_hash": feature_hash(feats),
            },
            tags=[
                target.phase,
                "player",
                "sweep",
                f"group:{GROUP_SLUG[target.group]}",
                f"target:{target.name}",
            ],
            launched_by=launched_by,
        )
        try:
            params = {k: run.config[k] for k in grid}
            cfg = model_config(target, num_threads=num_threads, **params)
            res = tune_objective(frame, target, feats, cfg)
            run.log(res)
            run.summary.update(res)
            results.append({**params, **res})
            log(f"{target.key} {params}: {res[metric]:.4f} ({res['tune/improvement_pct']:+.2f}%)")
        finally:
            run.finish()

    sweep = {
        "name": f"tune-{target.key}",
        "method": "grid",
        "metric": {"name": metric, "goal": "minimize"},
        "parameters": {k: {"values": v} for k, v in grid.items()},
    }
    sweep_id = run_sweep(sweep, one)
    out = pl.DataFrame(results).sort(metric)
    log(f"sweep {sweep_id}: best {out.row(0, named=True) if out.height else None}")
    return out.with_columns(pl.lit(sweep_id).alias("sweep_id"), pl.lit(target.key).alias("key"))


# ---- weekly train ---------------------------------------------------------------------------


def _seed_history(paths: DataPaths, target: Target, season: int) -> pl.DataFrame:
    """The canonical backtest's last seasons, as walk-forward history for a weekly refit.
    Only seasons before `season`: the refit's own walk-forward covers the season itself
    (the same rows live; a P10 rehearsal of a backtested season would otherwise hand the
    harness "history" from after its weeks, which it rightly refuses)."""
    path = paths.runs / "backtests" / "player" / target.key / PRED_FILE
    if not path.exists():
        return pl.DataFrame()
    p = pl.read_parquet(path).filter(
        (pl.col("season") >= season - BURN_IN) & (pl.col("season") < season)
    )
    cols = ["season", "week", "player_id", "game_id", "p10", "p50", "p90", "mean", "actual"]
    if target.kind == "amount":
        cols += ["_p10_raw", "_p90_raw"]
    if target.kind == "prob" or target.event_probs:
        cols += ["_p_raw"]  # the calibration layer is fitted on raw probabilities
    return p.select([c for c in cols if c in p.columns]).rename({"actual": "y"})


def run_train(
    season: int,
    week: int | None = None,
    launched_by: str | None = None,
    promote: bool = False,
    log: Callable[[str], None] = print,
    run_date: dt.date | None = None,
    targets: Sequence[Target] | None = None,
    use_wandb: bool = True,
    output: str = "main",
) -> dict[str, Any]:
    """Weekly production fit: predict every live target (`player_schema.live_targets`:
    the P06 targets plus any shipped P08 one) for week N of `season`.

    `output="update"` (the Saturday injury update, P07) writes
    `predictions_players_update.parquet` next to the main file and nothing else: no model
    files or meta, no `player_status.json`, no walk-forward or scoreboard rows, no W&B run,
    never promoted. Games that already kicked off keep the main file's pre-kickoff rows."""
    import wandb

    if output not in ("main", "update"):
        raise ValueError(f"output must be 'main' or 'update', not {output!r}")
    if output == "update" and promote:
        raise ValueError("an injury update never promotes a model")
    update = output == "update"
    targets = tuple(targets) if targets is not None else live_targets()
    paths = ensure_data_root()
    games = pl.read_parquet(paths.curated / "games.parquet")
    latest = last_asof_week(games, season)
    if latest is None:
        raise ValueError(f"no games for season {season}")
    week = week or latest
    if week > latest:
        raise ValueError(f"week {week} of {season} isn't predictable yet (latest {latest})")
    key = AsOf(season, week)
    now = clock.utc_now()
    data = build_player_data(paths, live_key=key, run_date=run_date, log=log, targets=targets)
    phrases = load_phrases()
    tag = f"{season}-w{week:02d}"
    version = f"{FAMILY}-{MODEL_VERSION}:{tag}"
    created = tuesdays(data.inp.games)
    model_dir = paths.models / FAMILY / tag
    if not update:
        model_dir.mkdir(parents=True, exist_ok=True)
    all_preds, walk, meta_t = [], [], {}
    charts = use_wandb and not update  # the W&B run's training charts (visibility only)
    entries = []
    for i, target in enumerate(targets):
        events.progress(None, i, len(targets), f"refitting {i + 1}/{len(targets)} · {target.key}")
        frame = target_data(data, target)
        frame = frame.filter(
            (pl.col("season") < season) | ((pl.col("season") == season) & (pl.col("week") <= week))
        )
        feats = target_features(frame, target)
        config = model_config(target)
        model = PlayerWeekModel(target, feats, config, explain=True)
        if charts:
            model.record_at = (season, week)  # this week's fit records its training loss
        keys = [AsOf(season, w) for w in range(1, week + 1)]
        out = walk_forward(
            frame,
            keys,
            model,
            label="y",
            weights=config.weights,
            min_train_rows=MIN_TRAIN_ROWS,
            history=_seed_history(paths, target, season),
        )
        fit = model.last
        assert fit is not None
        if charts:
            from nflengine.models.training_charts import TrainingEntry

            entries.append(TrainingEntry(target, fit, model.last_evals, feats, config))
        tt = (
            f"{fit.trained_through[0]}-w{fit.trained_through[1]:02d}"
            if fit.trained_through
            else "none"
        )
        fhash = feature_hash(feats)
        this = out.filter(pl.col("week") == week)
        earlier = out.filter(pl.col("week") < week)
        all_preds.append(
            assemble(
                this,
                frame,
                target,
                model_version=version,
                fhash=fhash,
                trained_through=tt,
                created_at=now,
                phrases=phrases,
                features=feats,
            )
        )
        if earlier.height and not update:
            walk.append(
                assemble(
                    earlier,
                    frame,
                    target,
                    model_version=f"{FAMILY}-{MODEL_VERSION}:walk-forward",
                    fhash=fhash,
                    trained_through="walk-forward",
                    created_at=created,
                    phrases=phrases,
                    features=feats,
                )
            )
        if not update:
            for name, b in fit.boosters.items():
                b.save_model(str(model_dir / f"{target.key}-{name}.txt"))
        meta_t[target.key] = {
            "features": len(feats),
            "feature_hash": fhash,
            "n_train": fit.n_train,
            "trained_through": tt,
            "range_param": model._range_param(fit),
            "scale": fit.scale,
            # everything besides the booster files that the projections depend on (P08):
            # the conformal shift (amounts), the NB dispersion and range tail (counts), the
            # calibration layer (prob targets and event counts' P(>= 1))
            "fitted": fitted_params(fit),
            "config": config.as_dict(),
        }
        log(
            f"{target.key}: {this.height} projections "
            f"(trained on {fit.n_train:,} rows through {tt})"
        )
    preds = pl.concat(all_preds, how="vertical_relaxed")
    # the refits take minutes: the projections exist from now, so the kickoff cutoff and
    # `created_at` use the save time, not the start time (Sol review: a game kicking off
    # mid-run must not get a projection stamped before its kickoff)
    now = clock.utc_now()
    preds = preds.with_columns(pl.lit(now).cast(pl.Datetime("us", "UTC")).alias("created_at"))
    # a game that already kicked off gets no new projection (it would be made after
    # kickoff); its saved pre-kickoff rows are kept below. Results come later (`score_weeks`).
    started = preds.filter(pl.col("kickoff_utc") <= now)["game_id"].n_unique()
    if started:
        log(f"no new projections for {started} game(s) already kicked off")
    preds = preds.filter(pl.col("kickoff_utc") > now).with_columns(
        pl.lit(None, dtype=pl.Float64).alias("actual"),
        pl.lit(None, dtype=pl.Boolean).alias("played"),
    )
    run_dir = paths.run_dir(season, week)
    run_dir.mkdir(parents=True, exist_ok=True)
    pred_path = run_dir / PRED_FILE
    preds, kept = keep_started_players(preds, pred_path, now)
    if kept:
        log(f"kept the saved projections of {kept} game(s) that already kicked off")
    from nflengine.ops.lock import write_parquet_atomic

    if update:
        out_path = run_dir / UPDATE_PRED_FILE
        write_parquet_atomic(preds, out_path, compression="zstd")
        log(f"update projections -> {out_path} ({preds.height} rows)")
        return {
            "predictions": out_path,
            "models": None,
            "url": None,
            "table": preds,
            "aliases": [],
            "kept": kept,
        }
    # atomic: an interrupted re-run never loses the saved rows of games already kicked off
    write_parquet_atomic(preds, pred_path, compression="zstd")
    from nflengine.models.player_schema import write_status

    write_status(run_dir, "ok", f"{preds.height} projections")
    if walk:
        wf = pl.concat(walk, how="vertical_relaxed")
        write_parquet_atomic(
            wf, paths.runs / str(season) / "player_walkforward.parquet", compression="zstd"
        )
        upsert_scoreboard(
            paths.runs / str(season) / SCOREBOARD_FILE, scoreboard_rows(wf, "backtest")
        )
    meta = {
        "model_version": version,
        "created_at": now.isoformat(),
        "targets": meta_t,
        "git_commit": git_commit(),
        "dataset_version": dataset_version(paths),
    }
    (model_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    url, aliases = None, [tag, *(["production"] if promote else [])]
    if use_wandb:
        run = init_run(
            GROUP,
            "train",
            config={**meta, "season": season, "week": week},
            tags=["p06", "player", f"season:{season}", f"week:{week:02d}", "prod-candidate"],
            launched_by=launched_by,
            name=f"train-{tag}",
        )
        try:
            log_slate(run, preds)
            from nflengine.models.training_charts import log_training_charts

            log_training_charts(run, entries, log)
            card = (
                Path(__file__).resolve().parents[3]
                / "documentation"
                / "model_cards"
                / "player-model-v1.md"
            )
            art = wandb.Artifact(
                FAMILY,
                type="model",
                description=card.read_text(encoding="utf-8") if card.exists() else version,
                metadata=meta,
            )
            art.add_dir(str(model_dir))
            run.log_artifact(art, aliases=aliases)
            url = run.url
        finally:
            run.finish()
    log(f"player projections -> {pred_path} ({preds.height} rows)")
    return {
        "predictions": pred_path,
        "models": model_dir,
        "url": url,
        "table": preds,
        "aliases": aliases,
    }


def keep_started_players(
    preds: pl.DataFrame, path: Path, now: dt.datetime
) -> tuple[pl.DataFrame, int]:
    """Re-running a week never replaces projections for games that already kicked off:
    their saved rows (made before kickoff) are kept and the new ones dropped."""
    if not path.exists():
        return preds, 0
    old = pl.read_parquet(path)
    started = old.filter(
        (pl.col("kickoff_utc") <= now) & (pl.col("created_at") < pl.col("kickoff_utc"))
    )
    if started.is_empty():
        return preds, 0
    games = started["game_id"].unique().to_list()
    new = preds.filter(~pl.col("game_id").is_in(games))
    return pl.concat([conform(started), new], how="vertical_relaxed"), len(games)


def log_slate(run, preds: pl.DataFrame) -> None:
    """The week's projections as tables + charts (a one-shot run draws nothing else)."""
    import wandb

    main = preds.filter(pl.col("is_main")).sort("outperf_z", descending=True)
    show = main.select(
        "player",
        "team",
        "opponent",
        "group",
        "target_label",
        "p10",
        "p50",
        "p90",
        "baseline",
        "outperformance",
        "confidence",
    ).with_columns(pl.selectors.float().round(1))
    counts = preds.group_by("group", "target").len().sort("group", "target")
    run.log(
        {
            "projections_main": wandb.Table(dataframe=show.to_pandas()),
            "projections_per_target": wandb.plot.bar(
                wandb.Table(
                    dataframe=counts.with_columns(
                        (pl.col("target") + " " + pl.col("group")).alias("model")
                    ).to_pandas()
                ),
                "model",
                "len",
                title="Projections per model this week",
            ),
            "top_outperformance": wandb.plot.bar(
                wandb.Table(
                    dataframe=main.head(15)
                    .select("player", pl.col("outperf_z").round(2))
                    .to_pandas()
                ),
                "player",
                "outperf_z",
                title="Biggest projected jumps over baseline (z)",
            ),
        }
    )
    run.summary.update(
        {
            "projections": preds.height,
            "players": preds["player_id"].n_unique(),
            "low_confidence_share": float((preds["confidence"] == "low").mean()),
        }
    )


# ---- live scoreboard ------------------------------------------------------------------------


def score_weeks(
    season: int,
    weeks: Sequence[int],
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    use_wandb: bool = True,
) -> pl.DataFrame:
    """Score the saved pre-kickoff projections of every played week in `weeks`, upsert the
    live `accuracy_scoreboard` rows and log the season's table + curves to W&B (once).

    Every earlier week is re-scored each time, not only the last one: PFR pressures arrive
    a week late, so a week's pressure rows are only complete one run later (Sol review).
    Re-scoring is idempotent (rows are replaced by key)."""
    paths = ensure_data_root()
    board_path = paths.runs / str(season) / SCOREBOARD_FILE
    files = {w: paths.run_dir(season, w) / PRED_FILE for w in weeks}
    files = {w: p for w, p in files.items() if p.exists()}
    if not files:
        log(f"no saved player projections for {season} weeks {list(weeks)}: nothing to score")
        if not board_path.exists():
            return pl.DataFrame(schema=SCOREBOARD_SCHEMA)
        board = pl.read_parquet(board_path)
        if use_wandb and board.height:  # keep the season panel current (backtest rows)
            log_scoreboard(season, max(weeks), board, launched_by)
        return board
    # playoff box scores too (P10): saved playoff projections are graded like any week
    inp = load_inputs(
        paths, first_season=season, last_season=season, log=lambda *_: None, playoffs=True
    )
    hist = player_history(inp)
    all_rows = []
    for w, path in sorted(files.items()):
        preds = pl.read_parquet(path).filter(pl.col("created_at") < pl.col("kickoff_utc"))
        scored = score_predictions(preds, hist)
        scored.write_parquet(path.with_name("predictions_players_scored.parquet"))
        rows = scoreboard_rows(scored, "live")
        all_rows.append(rows)
        log(f"scored {season} week {w}: {rows.height} scoreboard rows")
    board = upsert_scoreboard(board_path, pl.concat(all_rows))
    if use_wandb:
        log_scoreboard(season, max(files), board, launched_by)
    return board


def score_week(
    season: int,
    week: int,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    use_wandb: bool = True,
) -> pl.DataFrame:
    """Score one played week's saved pre-kickoff projections (`score_weeks`)."""
    return score_weeks(season, [week], launched_by, log, use_wandb)


def log_scoreboard(season: int, week: int, board: pl.DataFrame, launched_by: str | None) -> str:
    """W&B `accuracy_scoreboard` table + `scoreboard/*` improvement curves by week."""
    import wandb

    run = init_run(
        GROUP,
        "eval",
        config={"season": season, "week": week},
        tags=["p06", "player", "scoreboard", f"season:{season}", f"week:{week:02d}"],
        launched_by=launched_by,
        name=f"scoreboard-{season}-w{week:02d}",
    )
    try:
        run.define_metric("scoreboard/week")
        run.define_metric("scoreboard/*", step_metric="scoreboard/week")
        live = board.filter(pl.col("mode") == "live")
        src = live if live.height else board
        for (w,), part in src.sort("week").group_by("week", maintain_order=True):
            point: dict[str, float] = {"scoreboard/week": int(w)}
            for r in part.iter_rows(named=True):
                k = f"{r['target']}_{GROUP_SLUG[r['position_group']]}"
                if r["improvement_pct"] is not None:
                    point[f"scoreboard/improvement_{k}"] = float(r["improvement_pct"])
                if r["coverage_80"] is not None:
                    point[f"scoreboard/coverage_{k}"] = float(r["coverage_80"])
                bm, bb = r.get("brier_model"), r.get("brier_baseline")
                if bm is not None and bb:  # P08: Brier skill vs the rolling baseline, %
                    point[f"scoreboard/brier_skill_{k}"] = 100 * (float(bb) - float(bm)) / float(bb)
            run.log(point)
        run.log({"accuracy_scoreboard": wandb.Table(dataframe=board.to_pandas())})
        return run.url
    finally:
        run.finish()


__all__ = [
    "PRED_SCHEMA",
    "run_backtest",
    "run_train",
    "run_tune",
    "score_week",
]
