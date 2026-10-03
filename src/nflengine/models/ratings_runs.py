"""W&B runs for team ratings: `nfl ratings tune | eval | validate-trend`.

All runs go to group `track1-ratings` (documentation/08) with live per-season curves:
- `tune` (job_type `tune`): a W&B grid sweep, one run per configuration, objective =
  `objective_mse` (see `ratings_eval`). The sweep page shows parallel coordinates and
  parameter importance. Results are also written to `runs/ratings/tune-<stamp>/` on D:.
- `eval` (job_type `eval`): the chosen configuration against both baselines, by season
  and by week of season, plus the Elo walk-forward Brier score.
- `validate-trend` (job_type `eval`): does `trend_delta` predict the next 3 games beyond
  the rating? Walk-forward out-of-sample R² by season, with a bootstrap interval.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

import numpy as np
import polars as pl

from nflengine.models.elo import elo_games, elo_metrics
from nflengine.models.ratings import (
    HFA_SEASONS,
    RatingInputs,
    RatingParams,
    compute_ratings,
    net_lookup,
    prepare_inputs,
    ratings_frame,
)
from nflengine.models.ratings_build import RatingData, elo_params, load_rating_data
from nflengine.models.ratings_eval import (
    EVAL_SEASONS,
    add_baselines,
    add_prediction,
    bootstrap_delta_r2,
    by_group,
    eval_frame,
    game_targets,
    pooled_r2,
    score,
    team_game_epa,
    walk_forward_r2,
)
from nflengine.models.trend import team_game_margins, trend_table, trend_validation_frame
from nflengine.paths import ensure_data_root
from nflengine.settings import get_config
from nflengine.tracking import dataset_version, git_commit, init_run, run_sweep

GROUP = "track1-ratings"
BASELINES = ("base_last_season", "base_to_date")
DEFAULT_GRID: dict[str, list[float]] = {
    "half_life_weeks": [4, 6, 8, 12, 16, 24, 52],
    "prior_regression": [0.0, 0.1, 0.2, 0.35, 0.5],
    "ridge_alpha": [100, 175, 250, 400, 650],
}
# Trends count as predictive only if they add clearly and consistently (documentation/04).
PREDICTIVE_MIN_DELTA_R2 = 0.005
PREDICTIVE_MIN_SEASON_SHARE = 2 / 3


@dataclass
class EvalData:
    data: RatingData
    inp: RatingInputs
    targets: pl.DataFrame  # every completed game with targets + baselines
    ev: pl.DataFrame  # the scored games (regular season of the evaluation seasons)


def prepare_eval(
    base: RatingParams, eval_seasons: Sequence[int], splits: Sequence[str] = ("all",)
) -> EvalData:
    paths = ensure_data_root()
    start = get_config().seasons.history_start
    data = load_rating_data(paths, start)
    seasons = list(range(start, max(eval_seasons) + 1))
    inp = prepare_inputs(data.rp, data.games, data.depth_charts, base, seasons, splits)
    tg = team_game_epa(data.rp, base.garbage_weight, base.garbage_wp)
    targets = add_baselines(game_targets(data.games, tg), tg)
    return EvalData(data, inp, targets, eval_frame(targets, eval_seasons))


def _run_config(eval_seasons: Sequence[int], base: RatingParams) -> dict[str, Any]:
    return {
        "model": "weighted ridge on play EPA, fading preseason prior (documentation/04 A)",
        "objective": "MSE of week-w EPA/play margin from ratings as of week w, REG games",
        "eval_seasons": [min(eval_seasons), max(eval_seasons)],
        "history_start": get_config().seasons.history_start,
        "garbage_weight": base.garbage_weight,
        "garbage_wp": base.garbage_wp,
        "hfa_seasons": HFA_SEASONS,
        "dataset_version": dataset_version(),
        "git_commit": git_commit(),
    }


def _log_by_season(run, preds: pl.DataFrame, cols: Sequence[str]) -> pl.DataFrame:
    """Per-season MSE as a live curve (step = season), plus the running pooled MSE."""
    run.define_metric("season")
    run.define_metric("season/*", step_metric="season")
    per = by_group(preds, cols, "season")
    sse = dict.fromkeys(cols, 0.0)
    n = 0
    for row in per.iter_rows(named=True):
        n += row["games"]
        log = {"season": row["season"], "season/games": row["games"]}
        for c in cols:
            sse[c] += row[f"mse_{c}"] * row["games"]
            log[f"season/mse_{c}"] = row[f"mse_{c}"]
            log[f"season/cum_mse_{c}"] = sse[c] / n
        run.log(log)
    return per


def _summary(sc: Mapping[str, float], base: Mapping[str, float]) -> dict[str, float]:
    out = {
        "objective_mse": sc["mse_pred"],
        "mae": sc["mae_pred"],
        "corr_epa_margin": sc["corr_pred"],
        "corr_points": sc["corr_points_pred"],
        "mse_w1_3": sc["mse_pred_w1_3"],
        "mse_w4_8": sc["mse_pred_w4_8"],
        "mse_w9plus": sc["mse_pred_w9plus"],
        "games": sc["games"],
    }
    for b in BASELINES:
        out[f"mse_{b}"] = base[f"mse_{b}"]
        out[f"improvement_vs_{b}"] = 1 - sc["mse_pred"] / base[f"mse_{b}"]
        out[f"mse_{b}_w1_3"] = base[f"mse_{b}_w1_3"]
    return out


def grid_size(grid: Mapping[str, Sequence[float]]) -> int:
    return int(np.prod([len(v) for v in grid.values()]))


# ---- tune -----------------------------------------------------------------------------


def run_tune(
    grid: Mapping[str, Sequence[float]] = DEFAULT_GRID,
    eval_seasons: Sequence[int] = EVAL_SEASONS,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    base: RatingParams | None = None,
) -> pl.DataFrame:
    """Grid sweep over the rating parameters; returns results sorted by objective."""
    from nflengine.models.ratings_build import rating_params

    unknown = set(grid) - set(RatingParams().as_dict())
    if unknown:
        raise ValueError(f"unknown parameters in grid: {sorted(unknown)}")
    base = base or rating_params()
    log(f"preparing data (eval seasons {min(eval_seasons)}-{max(eval_seasons)}) ...")
    ed = prepare_eval(base, eval_seasons)
    base_scores = score(ed.ev, BASELINES)
    fixed = _run_config(eval_seasons, base)
    results: list[dict[str, Any]] = []
    total = grid_size(grid)
    log(f"{total} configurations; {ed.ev.height:,} scored games")

    def one() -> None:
        run = init_run(
            GROUP, "tune", config=fixed, tags=["p02", "ratings", "sweep"], launched_by=launched_by
        )
        try:
            p = replace(base, **{k: float(run.config[k]) for k in grid})
            res = compute_ratings(ed.inp, p, splits=("all",), metrics=("epa",))
            preds = add_prediction(ed.ev, net_lookup(res))
            _log_by_season(run, preds, ["pred", *BASELINES])
            summary = _summary(score(preds, ["pred"]), base_scores)
            run.summary.update(summary)
            results.append({**p.as_dict(), **summary, "run_id": run.id})
            log(
                f"  [{len(results)}/{total}] {dict((k, run.config[k]) for k in grid)} "
                f"mse={summary['objective_mse']:.5f}"
            )
        finally:
            run.finish()

    sweep_cfg = {
        "name": f"ratings-tune-{dt.datetime.now():%Y%m%d-%H%M}",
        "method": "grid",
        "metric": {"name": "objective_mse", "goal": "minimize"},
        "parameters": {k: {"values": list(v)} for k, v in grid.items()},
    }
    sweep_id = run_sweep(sweep_cfg, one)
    df = pl.DataFrame(results).sort("objective_mse") if results else pl.DataFrame()
    out = ensure_data_root().runs / "ratings" / f"tune-{dt.datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    if df.height:
        df.write_csv(out / "results.csv")
    (out / "sweep.json").write_text(
        json.dumps({"sweep_id": sweep_id, "grid": grid, "config": fixed}, indent=2, default=str)
    )
    log(f"sweep {sweep_id}: {df.height} runs; results -> {out}")
    return df


def grid_from_options(**lists: str | None) -> dict[str, list[float]]:
    """CLI comma lists -> grid (falls back to DEFAULT_GRID per parameter)."""
    grid: dict[str, list[float]] = {}
    for key, value in lists.items():
        if value:
            grid[key] = [float(v) for v in value.split(",") if v.strip()]
        elif key in DEFAULT_GRID:
            grid[key] = list(DEFAULT_GRID[key])
    return grid


# ---- eval ----------------------------------------------------------------------------------


def run_eval(
    params: RatingParams,
    eval_seasons: Sequence[int] = EVAL_SEASONS,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, float]:
    """Chosen parameters vs baselines (by season, by week) + Elo Brier, as one W&B run."""
    import wandb

    ed = prepare_eval(params, eval_seasons)
    res = compute_ratings(ed.inp, params, splits=("all",), metrics=("epa",))
    preds = add_prediction(ed.ev, net_lookup(res))
    cols = ["pred", *BASELINES]
    ep = elo_params()
    eg = elo_games(ed.data.games, ep)
    elo_all = elo_metrics(eg, list(eval_seasons))
    elo_reg = elo_metrics(eg, list(eval_seasons), game_types=["REG"])

    cfg = {**_run_config(eval_seasons, params), "params": params.as_dict(), "elo": asdict(ep)}
    run = init_run(
        GROUP,
        "eval",
        config=cfg,
        tags=["p02", "ratings", "elo"],
        launched_by=launched_by,
        name="ratings-eval",
    )
    try:
        per = _log_by_season(run, preds, cols)
        for scope, tbl in (("all", elo_all), ("reg", elo_reg)):
            for row in tbl.filter(pl.col("scope") == "season").iter_rows(named=True):
                run.log(
                    {
                        "season": row["season"],
                        f"season/elo_brier_{scope}": row["brier"],
                        f"season/elo_log_loss_{scope}": row["log_loss"],
                        f"season/elo_accuracy_{scope}": row["accuracy"],
                    }
                )
        weekly = by_group(preds.filter(pl.col("week") <= 18), cols, "week")
        run.log(
            {
                "by_week_table": wandb.Table(dataframe=weekly.to_pandas()),
                "by_week_mse": wandb.plot.line_series(
                    xs=weekly["week"].to_list(),
                    ys=[weekly[f"mse_{c}"].to_list() for c in cols],
                    keys=list(cols),
                    title="MSE by week of season (lower is better)",
                    xname="week",
                ),
                "by_season_table": wandb.Table(dataframe=per.to_pandas()),
                "elo_by_season": wandb.Table(dataframe=elo_all.to_pandas()),
            }
        )
        summary = _summary(score(preds, ["pred"]), score(preds, BASELINES))
        pooled_all = elo_all.filter(pl.col("scope") == "pooled").row(0, named=True)
        pooled_reg = elo_reg.filter(pl.col("scope") == "pooled").row(0, named=True)
        summary.update(
            {
                "elo_brier": pooled_all["brier"],
                "elo_log_loss": pooled_all["log_loss"],
                "elo_accuracy": pooled_all["accuracy"],
                "elo_brier_reg": pooled_reg["brier"],
                "ratings_beat_last_season": float(
                    summary["objective_mse"] < summary["mse_base_last_season"]
                ),
                "ratings_beat_to_date": float(
                    summary["objective_mse"] < summary["mse_base_to_date"]
                ),
            }
        )
        run.summary.update(summary)
        summary["url"] = run.url
        log(f"eval run: {run.url}")
    finally:
        run.finish()
    return summary


# ---- trend validation ------------------------------------------------------------------------


def trend_frames(data: RatingData, params: RatingParams, seasons: Sequence[int]):
    """(ratings, margins, trends) for `seasons` using the overall EPA rating only."""
    inp = prepare_inputs(data.rp, data.games, data.depth_charts, params, seasons, ("all",))
    res = compute_ratings(inp, params, splits=("all",), metrics=("epa",))
    ratings = ratings_frame(res)
    tg = team_game_epa(data.rp, params.garbage_weight, params.garbage_wp)
    margins = team_game_margins(game_targets(data.games, tg), net_lookup(res))
    return ratings, margins, trend_table(ratings, margins, data.games)


TREND_MODELS = {
    "rating": ["net_now"],
    "rating_trend": ["net_now", "trend_delta"],
    "rating_pve": ["net_now", "perf_vs_expected"],
    "rating_both": ["net_now", "trend_delta", "perf_vs_expected"],
}


def trend_decision(delta: float, ci_low: float, season_share: float) -> str:
    """'predictive' only when the gain is clear, consistent and its interval excludes 0."""
    ok = (
        delta >= PREDICTIVE_MIN_DELTA_R2
        and ci_low > 0
        and season_share >= PREDICTIVE_MIN_SEASON_SHARE
    )
    return "predictive" if ok else "descriptive"


def run_validate_trend(
    params: RatingParams,
    eval_seasons: Sequence[int] = EVAL_SEASONS,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    import wandb

    paths = ensure_data_root()
    start = get_config().seasons.history_start
    data = load_rating_data(paths, start)
    seasons = list(range(start, max(eval_seasons) + 1))
    _, margins, trends = trend_frames(data, params, seasons)
    vf = trend_validation_frame(trends, margins, data.games)
    per, pooled = walk_forward_r2(vf, "next_perf", TREND_MODELS, eval_seasons)
    summary: dict[str, Any] = {"rows": pooled.height, "seasons": per.height}
    for name in TREND_MODELS:
        summary[f"r2_{name}"] = pooled_r2(pooled, "next_perf", name)
    for name in ("rating_trend", "rating_pve", "rating_both"):
        delta = summary[f"r2_{name}"] - summary["r2_rating"]
        lo, hi = bootstrap_delta_r2(pooled, "next_perf", "rating", name)
        share = float((per[f"r2_{name}"] > per["r2_rating"]).mean())
        summary.update(
            {
                f"delta_r2_{name}": delta,
                f"delta_r2_{name}_ci90_low": lo,
                f"delta_r2_{name}_ci90_high": hi,
                f"seasons_improved_{name}": share,
            }
        )
    # Plain correlation of the trend with what the rating missed over the next 3 games.
    resid = pooled["next_perf"] - pooled["yhat_rating"]
    joined = pooled.with_columns(resid.alias("resid")).join(
        vf.select("season", "week", "team", "trend_delta", "perf_vs_expected"),
        on=["season", "week", "team"],
    )
    for c in ("trend_delta", "perf_vs_expected"):
        summary[f"corr_{c}_vs_rating_miss"] = float(
            np.corrcoef(joined[c].to_numpy(), joined["resid"].to_numpy())[0, 1]
        )
    summary["decision_trend_delta"] = trend_decision(
        summary["delta_r2_rating_trend"],
        summary["delta_r2_rating_trend_ci90_low"],
        summary["seasons_improved_rating_trend"],
    )
    summary["decision_perf_vs_expected"] = trend_decision(
        summary["delta_r2_rating_pve"],
        summary["delta_r2_rating_pve_ci90_low"],
        summary["seasons_improved_rating_pve"],
    )
    cfg = {
        **_run_config(eval_seasons, params),
        "params": params.as_dict(),
        "target": "mean opponent-adjusted EPA/play margin, REG games in the next 3 weeks (>= 2)",
        "models": TREND_MODELS,
        "predictive_min_delta_r2": PREDICTIVE_MIN_DELTA_R2,
        "predictive_min_season_share": PREDICTIVE_MIN_SEASON_SHARE,
    }
    run = init_run(
        GROUP,
        "eval",
        config=cfg,
        tags=["p02", "trend"],
        launched_by=launched_by,
        name="validate-trend",
    )
    try:
        run.define_metric("season")
        run.define_metric("season/*", step_metric="season")
        for row in per.iter_rows(named=True):
            log_row = {"season": row["season"], "season/rows": row["rows"]}
            for name in TREND_MODELS:
                log_row[f"season/r2_{name}"] = row[f"r2_{name}"]
            log_row["season/delta_r2_trend"] = row["r2_rating_trend"] - row["r2_rating"]
            log_row["season/delta_r2_pve"] = row["r2_rating_pve"] - row["r2_rating"]
            run.log(log_row)
        run.log({"by_season": wandb.Table(dataframe=per.to_pandas())})
        run.summary.update(summary)
        summary["url"] = run.url
        log(f"validate-trend run: {run.url}")
    finally:
        run.finish()
    return summary
