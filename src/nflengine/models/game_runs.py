"""W&B runs for the game model: `nfl backtest game`, `nfl backtest game-weights`,
`nfl train game`, `nfl features game` (plan P03; documentation/08).

All runs go to group `track1-game`:
- **backtest** (job_type `backtest`): walk-forward by week over the reported seasons, with
  a burn-in before them (its predictions feed sigma and calibration but are not
  reported). Per-week and cumulative metrics for the model and every baseline are logged
  live (`bt/*`, step `bt/step`); the end of the run adds pooled summaries, a by-season
  table, a reliability diagram, a by-week-of-season table and every prediction. The
  predictions are also saved on D: (`runs/backtests/game/<variant>/`) so P04's report
  card can be tested on past weeks.
- **sweep** (job_type `tune`): a W&B grid over the current-season sample weight (D28).
- **train** (job_type `train`): the weekly production path. Refit on everything before
  week N (same walk-forward code, so sigma and calibration come from earlier weeks'
  walk-forward predictions), predict week N in both variants, write
  `runs/<season>/week<NN>/predictions_games.parquet`, save the fitted models under
  `models/game-model/<season>-w<NN>/` and log them as the W&B artifact `game-model`.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nflengine.features.asof import AsOf, last_asof_week
from nflengine.features.game import build_game_features, load_game_inputs
from nflengine.models import metrics as M
from nflengine.models.backtest import SampleWeights, walk_forward, week_keys
from nflengine.models.game_model import (
    FittedGameModel,
    GameModelConfig,
    GameWeekModel,
    coefficients,
    feature_hash,
)
from nflengine.paths import DataPaths, ensure_data_root
from nflengine.settings import get_config
from nflengine.tracking import dataset_version, git_commit, init_run, run_sweep

GROUP = "track1-game"
FAMILY = "game-model"
MODEL_VERSION = "v0"
REPORT_SEASONS = tuple(range(2018, 2026))
LABELS = ["margin", "total", "home_win"]
PROBS = {
    "model": "home_win_prob",
    "elo": "elo_prob",
    "market": "market_prob",
    "home": "home_base_prob",
}
QB_MODES = ("tuesday", "actual")
CONFIDENCE_BANDS = ((0.55, "toss-up"), (0.65, "lean"), (0.80, "solid"), (1.01, "strong"))


def model_config(variant: str = "model_only", **overrides: Any) -> GameModelConfig:
    """`settings.yaml` game_model + training.sample_weights, then overrides."""
    cfg = get_config()
    gm = dict(cfg.game_model or {})
    for k in ("train_start", "burn_in_seasons"):
        gm.pop(k, None)
    weights = SampleWeights.from_config(cfg.training.get("sample_weights"))
    if overrides.get("weights") is not None:
        weights = overrides.pop("weights")
    overrides.pop("weights", None)
    return GameModelConfig.from_config(gm, weights=weights, variant=variant, **overrides)


def train_start() -> int:
    return int((get_config().game_model or {}).get("train_start", 2011))


def burn_in_seasons() -> int:
    return int((get_config().game_model or {}).get("burn_in_seasons", 5))


# ---- data ------------------------------------------------------------------------------------


def load_frame(
    paths: DataPaths | None = None,
    qb_mode: str = "tuesday",
    live_key: AsOf | None = None,
    run_date: dt.date | None = None,
    log: Callable[[str], None] = print,
) -> pl.DataFrame:
    """The game feature table, rebuilt from the curated data and P02 feature tables.

    `qb_mode='actual'` swaps the Tuesday expected starter for the game's listed starter
    (research only: what a game-day injury report would reveal, post-Tuesday
    information). `live_key` adds the live expected-starter resolver (schedule / depth
    chart / injuries) for that week's unplayed games.
    """
    if qb_mode not in QB_MODES:
        raise ValueError(f"qb_mode must be one of {QB_MODES}")
    paths = paths or ensure_data_root()
    overrides = None
    if qb_mode == "actual":
        overrides = _actual_overrides(paths)
    elif live_key is not None:
        overrides = _live_overrides(paths, live_key, run_date or dt.date.today())
    gi = load_game_inputs(paths, train_start(), qb_overrides=overrides, log=log)
    return build_game_features(
        gi.games,
        gi.ratings,
        gi.elo,
        qb=gi.qb,
        travel=gi.travel,
        lines=gi.lines,
        elo_hfa=float(get_config().elo.get("hfa", 48.0)),
        min_season=train_start(),
    )


def _actual_overrides(paths: DataPaths):
    from nflengine.features.qb import actual_starters, load_qb_inputs

    def fn(games: pl.DataFrame) -> pl.DataFrame:
        qi = load_qb_inputs(paths, train_start() - 3)
        return actual_starters(qi["games"], qi["dropbacks"])

    return fn


def _live_overrides(paths: DataPaths, key: AsOf, run_date: dt.date):
    from nflengine.features.qb import live_starters, load_qb_inputs

    def fn(games: pl.DataFrame) -> pl.DataFrame:
        qi = load_qb_inputs(paths, key.season - 2)
        return live_starters(
            qi["games"],
            qi.get("depth_charts"),
            qi.get("injuries"),
            key.season,
            key.week,
            run_date,
            dropbacks=qi["dropbacks"],  # an injured Tuesday starter is replaced too
        )

    return fn


def write_feature_table(frame: pl.DataFrame, paths: DataPaths | None = None) -> Path:
    from nflengine.curate.build import write_duckdb_views

    paths = paths or ensure_data_root()
    out = paths.features / "game_features.parquet"
    tmp = out.with_suffix(".parquet.tmp")
    frame.write_parquet(tmp, compression="zstd")
    tmp.replace(out)
    meta = {
        "built_at": dt.datetime.now().isoformat(timespec="seconds"),
        "rows": frame.height,
        "seasons": [int(frame["season"].min()), int(frame["season"].max())],
        "git_commit": git_commit(),
        "dataset_version": dataset_version(paths),
    }
    (paths.features / "_meta").mkdir(exist_ok=True)
    (paths.features / "_meta" / "game_features.json").write_text(json.dumps(meta, indent=2))
    write_duckdb_views(paths)
    return out


# ---- scoring ------------------------------------------------------------------------------------


# Every predictor compared with the model must exist on a row for it to be scored, so the
# model and all baselines are always measured on exactly the same games.
COMPARED = (
    *PROBS.values(),
    "expected_margin",
    "elo_margin",
    "spread_line",
    "pred_total",
    "total_line",
    "pts_base_total",
    "pred_home_points",
    "pred_away_points",
    "pts_base_home",
    "pts_base_away",
    "mkt_home_points",
    "mkt_away_points",
)


def scored(preds: pl.DataFrame) -> pl.DataFrame:
    """Played games where the model and every baseline have a prediction (the common
    cohort every metric is computed on)."""
    cols = ["home_win", "margin", "total", *[c for c in COMPARED if c in preds.columns]]
    return preds.filter(pl.all_horizontal(pl.col(c).is_not_null() for c in cols))


def pooled_summary(preds: pl.DataFrame) -> dict[str, float]:
    """Pooled metrics for the model and every baseline on the same scored games.

    `games` is the common cohort; `games_dropped` counts played games the model predicted
    but some baseline could not (left out of every metric). The vig-free moneyline is a
    reference only and is scored on the cohort rows that have moneylines (`games_ml`).
    """
    done = preds.filter(pl.col("home_win").is_not_null() & pl.col("home_win_prob").is_not_null())
    ev = scored(preds)
    s = M.score_frame(
        ev,
        PROBS,
        amounts={
            "margin_model": ("expected_margin", "margin"),
            "margin_elo": ("elo_margin", "margin"),
            "margin_market": ("spread_line", "margin"),
            "total_model": ("pred_total", "total"),
            "total_market": ("total_line", "total"),
            "total_rolling": ("pts_base_total", "total"),
        },
    )
    s["games_dropped"] = float(done.height - ev.height)
    ml = ev.filter(pl.col("market_ml_prob").is_not_null())
    s["market_ml_brier"] = M.brier(ml["market_ml_prob"], ml["home_win"])
    s["games_ml"] = float(ml.height)
    pts = {
        "model": ("pred_home_points", "pred_away_points"),
        "rolling": ("pts_base_home", "pts_base_away"),
        "market": ("mkt_home_points", "mkt_away_points"),
    }
    for name, (h, a) in pts.items():
        both = pl.concat([ev[h] - ev["home_score"], ev[a] - ev["away_score"]])
        s[f"mae_points_{name}"] = float(both.abs().mean())
    s["margin_rmse_model"] = M.rmse(ev["expected_margin"], ev["margin"])
    gap = (ev["pred_home_points"] - ev["pred_away_points"] - ev["expected_margin"]).abs()
    s["score_margin_max_gap"] = float(gap.max()) if ev.height else float("nan")
    for b in ("elo", "market", "home"):
        s[f"brier_gain_vs_{b}"] = s[f"brier_{b}"] - s["brier_model"]
    s["beats_elo"] = float(s["brier_model"] < s["brier_elo"])
    return s


def by_season(preds: pl.DataFrame) -> pl.DataFrame:
    ev = scored(preds)
    return M.by_group(
        ev,
        "season",
        PROBS,
        amounts={
            "margin_model": ("expected_margin", "margin"),
            "total_model": ("pred_total", "total"),
        },
    )


def by_week_bucket(preds: pl.DataFrame) -> pl.DataFrame:
    ev = scored(preds)
    ev = ev.with_columns(
        pl.when(pl.col("game_type") != "REG")
        .then(pl.lit("post"))
        .when(pl.col("week") <= 4)
        .then(pl.lit("w01-04"))
        .when(pl.col("week") <= 9)
        .then(pl.lit("w05-09"))
        .otherwise(pl.lit("w10+"))
        .alias("bucket")
    )
    return M.by_group(ev, "bucket", PROBS)


class LiveLogger:
    """`walk_forward` on_week callback: per-week + cumulative curves for reported weeks."""

    def __init__(self, run, report_seasons: Sequence[int]):
        self.run = run
        self.report = set(report_seasons)
        self.step = 0
        self.rows: list[pl.DataFrame] = []
        if run is not None:
            run.define_metric("bt/step")
            run.define_metric("bt/*", step_metric="bt/step")

    def __call__(self, key: AsOf, preds: pl.DataFrame) -> None:
        if key.season not in self.report:
            return
        done = scored(preds)
        if done.is_empty():
            return
        self.rows.append(done)
        cum = pl.concat(self.rows)
        self.step += 1
        log: dict[str, float] = {
            "bt/step": self.step,
            "bt/season": key.season,
            "bt/week": key.week,
            "bt/games": done.height,
            "bt/sigma": float(done["sigma"][0]),
        }
        for name, col in PROBS.items():
            log[f"bt/brier_{name}"] = M.brier(done[col], done["home_win"])
            log[f"bt/cum_brier_{name}"] = M.brier(cum[col], cum["home_win"])
        log["bt/cum_log_loss_model"] = M.log_loss(cum["home_win_prob"], cum["home_win"])
        log["bt/cum_accuracy_model"] = M.accuracy(cum["home_win_prob"], cum["home_win"])
        log["bt/cum_margin_mae_model"] = M.mae(cum["expected_margin"], cum["margin"])
        log["bt/cum_margin_mae_market"] = M.mae(cum["spread_line"], cum["margin"])
        log["bt/cum_total_mae_model"] = M.mae(cum["pred_total"], cum["total"])
        if self.run is not None:
            self.run.log(log)


def run_walk_forward(
    frame: pl.DataFrame,
    config: GameModelConfig,
    report_seasons: Sequence[int],
    on_week: Callable[[AsOf, pl.DataFrame], None] | None = None,
    model: GameWeekModel | None = None,
    extra_keys: Sequence[AsOf] = (),
) -> pl.DataFrame:
    """Walk-forward predictions for the burn-in + reported seasons (completed weeks)."""
    first = min(report_seasons) - burn_in_seasons()
    keys = week_keys(
        frame.filter(pl.col("completed").fill_null(False)), range(first, max(report_seasons) + 1)
    )
    keys = sorted(set(keys) | set(extra_keys))
    return walk_forward(
        frame,
        keys,
        model or GameWeekModel(config),
        label=LABELS,
        weights=config.weights,
        min_train_rows=500,
        on_week=on_week,
    )


def _run_config(config: GameModelConfig, report: Sequence[int], qb_mode: str) -> dict[str, Any]:
    return {
        "model": f"game model {MODEL_VERSION} (documentation/04 B)",
        **config.as_dict(),
        "sample_weights": config.weights.as_dict(),
        "feature_hash": feature_hash([*config.mfeats, *config.tfeats]),
        "report_seasons": [min(report), max(report)],
        "train_start": train_start(),
        "burn_in_seasons": burn_in_seasons(),
        "qb_mode": qb_mode,
        "dataset_version": dataset_version(),
        "git_commit": git_commit(),
    }


def _final_logs(run, preds: pl.DataFrame) -> None:
    import wandb

    ev = scored(preds)
    rel = {name: M.reliability(ev[col], ev["home_win"]) for name, col in PROBS.items()}
    per = by_season(preds)
    run.log(
        {
            "by_season": wandb.Table(dataframe=per.to_pandas()),
            "by_week_bucket": wandb.Table(dataframe=by_week_bucket(preds).to_pandas()),
            "reliability_table": wandb.Table(
                dataframe=pl.concat(
                    [t.with_columns(pl.lit(n).alias("predictor")) for n, t in rel.items()]
                ).to_pandas()
            ),
            "reliability_diagram": wandb.plot.line_series(
                xs=[float(x) for x in rel["model"]["mean_prob"]],
                ys=[list(rel["model"]["mean_outcome"]), list(rel["model"]["mean_prob"])],
                keys=["model: actual win rate", "perfect calibration"],
                title="Reliability (model): actual home win rate vs predicted probability",
                xname="predicted home win probability",
            ),
            "brier_by_season": wandb.plot.line_series(
                xs=per["season"].to_list(),
                ys=[per[f"brier_{n}"].to_list() for n in PROBS],
                keys=list(PROBS),
                title="Brier score by season (lower is better)",
                xname="season",
            ),
            "predictions": wandb.Table(dataframe=_pred_table(preds).to_pandas()),
        }
    )


def _pred_table(preds: pl.DataFrame) -> pl.DataFrame:
    cols = [
        "season",
        "week",
        "game_id",
        "home_team",
        "away_team",
        "home_win_prob",
        "elo_prob",
        "market_prob",
        "expected_margin",
        "spread_line",
        "margin",
        "pred_home_points",
        "pred_away_points",
        "home_score",
        "away_score",
    ]
    return preds.select([c for c in cols if c in preds.columns]).with_columns(
        pl.selectors.float().round(4)
    )


def backtest_label(variant: str, qb_mode: str = "tuesday", **overrides: Any) -> str:
    """Run name and D: folder: the bare variant only for the settings.yaml configuration
    with Tuesday QB information (the canonical predictions P04 reads); anything else gets a
    suffix so a research run never overwrites them."""
    parts = [variant]
    if qb_mode != "tuesday":
        parts.append(f"qb-{qb_mode}")
    for k, v in sorted(overrides.items()):
        if v is None:
            continue
        if isinstance(v, SampleWeights):
            v = f"{v.current_season:g}-{v.last_season:g}-{v.older:g}"
        elif isinstance(v, tuple | list):
            v = "+".join(map(str, v))
        parts.append(f"{k}-{v}")
    return "_".join(parts)


def save_backtest(preds: pl.DataFrame, summary: dict[str, Any], label: str) -> Path:
    paths = ensure_data_root()
    root = paths.runs / "backtests" / "game" / label
    root.mkdir(parents=True, exist_ok=True)
    preds.write_parquet(root / "predictions_games.parquet", compression="zstd")
    (root / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return root


@dataclass
class BacktestResult:
    preds: pl.DataFrame
    summary: dict[str, Any]
    url: str | None
    saved_to: Path | None


def run_backtest(
    variant: str = "model_only",
    report_seasons: Sequence[int] = REPORT_SEASONS,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    frame: pl.DataFrame | None = None,
    qb_mode: str = "tuesday",
    save: bool = True,
    tags: Sequence[str] = (),
    **overrides: Any,
) -> BacktestResult:
    """Walk-forward backtest of one configuration, logged live to W&B."""
    label = backtest_label(variant, qb_mode, **overrides)
    config = model_config(variant, **overrides)
    if frame is None:
        log("building the game feature table ...")
        frame = load_frame(qb_mode=qb_mode, log=log)
    cfg = _run_config(config, report_seasons, qb_mode)
    run = init_run(
        GROUP,
        "backtest",
        config=cfg,
        tags=["p03", "game", f"variant:{variant}", f"qb:{qb_mode}", *tags],
        launched_by=launched_by,
        name=f"backtest-{label}",
    )
    try:
        logger = LiveLogger(run, report_seasons)
        preds = run_walk_forward(frame, config, report_seasons, on_week=logger)
        reported = preds.filter(pl.col("season").is_in(list(report_seasons)))
        summary: dict[str, Any] = pooled_summary(reported)
        reg = pooled_summary(reported.filter(pl.col("game_type") == "REG"))
        summary.update({f"reg_{k}": v for k, v in reg.items() if k.startswith(("brier", "games"))})
        per = by_season(reported)
        summary["seasons_beating_elo"] = float((per["brier_model"] < per["brier_elo"]).sum())
        summary["seasons"] = float(per.height)
        run.summary.update(summary)
        _final_logs(run, reported)
        url = run.url
        log(f"backtest run: {url}")
    finally:
        run.finish()
    saved = save_backtest(preds, {**summary, "config": cfg, "url": url}, label) if save else None
    return BacktestResult(preds, summary, url, saved)


# ---- sample-weight sweep ----------------------------------------------------------------------


def run_weight_sweep(
    values: Sequence[float] = (1.0, 2.0, 3.0, 5.0),
    variant: str = "model_only",
    report_seasons: Sequence[int] = REPORT_SEASONS,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    frame: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """W&B grid sweep over the current-season sample weight (last season 1.5x, capped at the
    current weight; older 1x). Returns one row per value."""
    if frame is None:
        log("building the game feature table ...")
        frame = load_frame(log=log)
    results: list[dict[str, Any]] = []

    def one() -> None:
        base = model_config(variant)
        run = init_run(
            GROUP,
            "tune",
            config=_run_config(base, report_seasons, "tuesday"),
            tags=["p03", "game", "sweep", f"variant:{variant}"],
            launched_by=launched_by,
        )
        try:
            w = SampleWeights.for_current(float(run.config["current_season_weight"]))
            config = model_config(variant, weights=w)
            run.config.update({"sample_weights": w.as_dict()}, allow_val_change=True)
            preds = run_walk_forward(
                frame, config, report_seasons, on_week=LiveLogger(run, report_seasons)
            )
            reported = preds.filter(pl.col("season").is_in(list(report_seasons)))
            s = pooled_summary(reported)
            buckets = by_week_bucket(reported)
            for row in buckets.iter_rows(named=True):
                s[f"brier_model_{row['bucket']}"] = row["brier_model"]
            run.summary.update(s)
            results.append({"current_season_weight": w.current_season, **s, "run_id": run.id})
            log(f"  weight {w.current_season}: brier {s['brier_model']:.5f}")
        finally:
            run.finish()

    sweep_id = run_sweep(
        {
            "name": f"game-weights-{variant}-{dt.datetime.now():%Y%m%d-%H%M}",
            "method": "grid",
            "metric": {"name": "brier_model", "goal": "minimize"},
            "parameters": {"current_season_weight": {"values": [float(v) for v in values]}},
        },
        one,
    )
    df = pl.DataFrame(results).sort("current_season_weight") if results else pl.DataFrame()
    out = (
        ensure_data_root().runs
        / "backtests"
        / "game"
        / f"weights-{dt.datetime.now():%Y%m%d-%H%M%S}"
    )
    out.mkdir(parents=True, exist_ok=True)
    if df.height:
        df.write_csv(out / "results.csv")
    (out / "sweep.json").write_text(json.dumps({"sweep_id": sweep_id, "values": list(values)}))
    log(f"sweep {sweep_id}: {df.height} runs -> {out}")
    return df


# ---- weekly production path ---------------------------------------------------------------------


def confidence_label(p: float | None) -> str | None:
    if p is None or np.isnan(p):
        return None
    c = max(p, 1 - p)
    return next(label for cut, label in CONFIDENCE_BANDS if c < cut)


PREDICTION_COLS = [
    "season",
    "week",
    "game_id",
    "game_type",
    "kickoff_utc",
    "home_team",
    "away_team",
    "neutral_site",
    "variant",
    "is_primary",
    "market_fallback",
    "home_win_prob",
    "away_win_prob",
    "expected_margin",
    "pred_home_points",
    "pred_away_points",
    "pred_total",
    "sigma",
    "confidence",
    "confidence_label",
    "elo_prob",
    "market_prob",
    "spread_line",
    "total_line",
    "market_source",
    "home_qb_name",
    "away_qb_name",
    "home_qb_source",
    "away_qb_source",
    "model_version",
    "feature_hash",
    "trained_through",
    "created_at",
]


COMPLETE_COLS = (
    "home_win_prob",
    "expected_margin",
    "pred_total",
    "pred_home_points",
    "pred_away_points",
)


def assemble_predictions(
    week_preds: dict[str, pl.DataFrame],
    frame_week: pl.DataFrame,
    model_version: str,
    hashes: dict[str, str],
    trained_through: str,
) -> pl.DataFrame:
    """Both variants for one week -> the `predictions_games` table (handoff to P04).

    Every game gets a model_only row. A market row exists only when the market variant
    produced a complete prediction (probability, margin, total and both scores), which needs
    both a spread and a total. The row the digest shows (`is_primary`) is the market row, or
    the model_only row with `market_fallback = True` when the game has no complete line.
    """
    mo = week_preds["model_only"]
    mk = week_preds["market"].filter(
        pl.all_horizontal(pl.col(c).is_not_null() for c in COMPLETE_COLS)
    )
    has_market = set(mk["game_id"].to_list())
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    qb_cols = [
        c
        for c in ("home_qb_name", "away_qb_name", "home_qb_source", "away_qb_source")
        if c in frame_week.columns
    ]
    parts = []
    for variant, df in (("model_only", mo), ("market", mk)):
        primary = (
            ~pl.col("game_id").is_in(list(has_market)) if variant == "model_only" else pl.lit(True)
        )
        parts.append(
            df.join(frame_week.select("game_id", *qb_cols), on="game_id", how="left").with_columns(
                primary.alias("is_primary"),
                (pl.lit(variant == "model_only") & primary).alias("market_fallback"),
                (1 - pl.col("home_win_prob")).alias("away_win_prob"),
                pl.max_horizontal(pl.col("home_win_prob"), 1 - pl.col("home_win_prob")).alias(
                    "confidence"
                ),
                pl.col("home_win_prob")
                .map_elements(confidence_label, return_dtype=pl.String)
                .alias("confidence_label"),
                pl.lit(model_version).alias("model_version"),
                pl.lit(hashes[variant]).alias("feature_hash"),
                pl.lit(trained_through).alias("trained_through"),
                pl.lit(now).alias("created_at"),
            )
        )
    out = pl.concat(parts, how="diagonal_relaxed")
    for c in PREDICTION_COLS:
        if c not in out.columns:
            out = out.with_columns(pl.lit(None).alias(c))
    return out.select(PREDICTION_COLS).sort("kickoff_utc", "game_id", "variant")


def keep_started(
    new: pl.DataFrame, saved: Path, now: dt.datetime
) -> tuple[pl.DataFrame, list[str]]:
    """Re-running a week never replaces a game that has already kicked off.

    The report card grades only predictions made before kickoff (D55), so the earlier saved
    rows of started games are kept and only upcoming games get the new predictions. Returns
    the merged table and the game ids that were kept.
    """
    if not saved.exists():
        return new, []
    old = pl.read_parquet(saved)
    old = old.with_columns(pl.lit(None).alias(c) for c in new.columns if c not in old.columns)
    kept = old.filter(pl.col("kickoff_utc") <= now)["game_id"].unique().sort().to_list()
    if not kept:
        return new, []
    merged = pl.concat(
        [
            old.filter(pl.col("game_id").is_in(kept)).select(new.columns).cast(new.schema),
            new.filter(~pl.col("game_id").is_in(kept)),
        ]
    ).sort("kickoff_utc", "game_id", "variant")
    return merged, kept


def _save_models(fits: dict[str, FittedGameModel], folder: Path, meta: dict[str, Any]) -> None:
    import joblib

    folder.mkdir(parents=True, exist_ok=True)
    for variant, fit in fits.items():
        joblib.dump(fit, folder / f"{variant}.joblib")
        coefficients(fit).write_csv(folder / f"{variant}_coefficients.csv")
    (folder / "meta.json").write_text(json.dumps(meta, indent=2, default=str))


def run_train(
    season: int,
    week: int | None = None,
    launched_by: str | None = None,
    promote: bool = False,
    log: Callable[[str], None] = print,
    run_date: dt.date | None = None,
) -> dict[str, Any]:
    """Weekly production fit: predict week N of `season` in both variants."""
    import wandb

    paths = ensure_data_root()
    games = pl.read_parquet(paths.curated / "games.parquet")
    latest = last_asof_week(games, season)
    if latest is None:
        raise ValueError(f"no games for season {season}")
    week = week or latest
    if week > latest:
        raise ValueError(
            f"week {week} of {season} isn't predictable yet: weeks before it aren't complete "
            f"(latest as-of week is {latest}; check `nfl ingest --check-ready`)"
        )
    key = AsOf(season, week)
    log(f"building features as of {key} ...")
    frame = load_frame(paths, live_key=key, run_date=run_date, log=log)
    frame_week = frame.filter((pl.col("season") == season) & (pl.col("week") == week))
    if frame_week.is_empty():
        raise ValueError(f"no feature rows for {key}: run `nfl ratings build` first")
    report = [season]
    week_preds: dict[str, pl.DataFrame] = {}
    fits: dict[str, FittedGameModel] = {}
    configs: dict[str, GameModelConfig] = {}
    for variant in ("model_only", "market"):
        config = model_config(variant)
        model = GameWeekModel(config)
        preds = run_walk_forward(
            frame.filter(
                (pl.col("season") < season)
                | ((pl.col("season") == season) & (pl.col("week") <= week))
            ),
            config,
            report,
            model=model,
            extra_keys=[key],
        )
        week_preds[variant] = preds.filter((pl.col("season") == season) & (pl.col("week") == week))
        assert model.last is not None
        fits[variant] = model.last
        configs[variant] = config
    tag = f"{season}-w{week:02d}"
    version = f"{FAMILY}-{MODEL_VERSION}:{tag}"
    hashes = {v: feature_hash([*c.mfeats, *c.tfeats]) for v, c in configs.items()}
    tt = fits["model_only"].trained_through
    trained_through = f"{tt[0]}-w{tt[1]:02d}" if tt else "none"
    table = assemble_predictions(week_preds, frame_week, version, hashes, trained_through)

    run_dir = paths.run_dir(season, week)
    run_dir.mkdir(parents=True, exist_ok=True)
    pred_path = run_dir / "predictions_games.parquet"
    table, kept = keep_started(table, pred_path, dt.datetime.now(dt.UTC))
    if kept:
        log(f"kept the saved predictions of {len(kept)} game(s) that already kicked off: {kept}")
    table.write_parquet(pred_path, compression="zstd")
    model_dir = paths.models / FAMILY / tag
    meta = {
        "model_version": version,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "configs": {v: c.as_dict() for v, c in configs.items()},
        "sample_weights": configs["model_only"].weights.as_dict(),
        "feature_hashes": hashes,
        "trained_through": trained_through,
        "sigma": {v: f.sigma for v, f in fits.items()},
        "n_train": {v: f.n_train for v, f in fits.items()},
        "calibration": {
            v: (f.calibrator.method if f.calibrator else "none") for v, f in fits.items()
        },
        "git_commit": git_commit(),
        "dataset_version": dataset_version(paths),
    }
    _save_models(fits, model_dir, meta)

    card = (
        Path(__file__).resolve().parents[3] / "documentation" / "model_cards" / "game-model-v0.md"
    )
    run = init_run(
        GROUP,
        "train",
        config={**meta, "season": season, "week": week},
        tags=["p03", "game", f"season:{season}", f"week:{week:02d}", "prod-candidate"],
        launched_by=launched_by,
        name=f"train-{tag}",
    )
    try:
        primary = table.filter(pl.col("is_primary"))
        run.log({"predictions_games": wandb.Table(dataframe=_live_table(table).to_pandas())})
        log_slate_charts(run, table)
        run.summary.update(
            {
                "games": primary.height,
                "kept_started_games": len(kept),
                "market_fallback_games": int(primary["market_fallback"].sum()),
                "sigma_model_only": fits["model_only"].sigma,
                "sigma_market": fits["market"].sigma,
            }
        )
        art = wandb.Artifact(
            FAMILY,
            type="model",
            description=card.read_text(encoding="utf-8") if card.exists() else version,
            metadata=meta,
        )
        art.add_dir(str(model_dir))
        aliases = [tag, *(["production"] if promote else [])]
        run.log_artifact(art, aliases=aliases)
        url = run.url
    finally:
        run.finish()
    log(f"predictions -> {pred_path}")
    log(f"models -> {model_dir}; W&B artifact {FAMILY}:{tag} aliases {aliases}")
    return {
        "predictions": pred_path,
        "models": model_dir,
        "url": url,
        "table": table,
        "aliases": aliases,
    }


def log_slate_charts(run, table: pl.DataFrame) -> None:
    """Chart panels for a weekly fit (a one-shot run, so nothing else draws a chart):
    `slate/*` lines over the week's games in kickoff order (shown, model-only, market and
    Elo home win probability; expected margin), plus a bar chart of the shown home win %.
    Standard line panels, so they render in the W&B mobile app too."""
    import wandb

    shown = table.filter(pl.col("is_primary")).sort("kickoff_utc", "game_id")
    model_only = {
        r["game_id"]: r["home_win_prob"]
        for r in table.filter(pl.col("variant") == "model_only").iter_rows(named=True)
    }
    run.define_metric("slate/game")
    run.define_metric("slate/*", step_metric="slate/game")
    labels = []
    for i, r in enumerate(shown.iter_rows(named=True), start=1):
        labels.append(f"{i:02d} {r['away_team']}@{r['home_team']}")
        point = {
            "slate/game": i,
            "slate/home_win_prob_shown": r["home_win_prob"],
            "slate/home_win_prob_model_only": model_only.get(r["game_id"]),
            "slate/home_win_prob_market": r["market_prob"],
            "slate/home_win_prob_elo": r["elo_prob"],
            "slate/expected_margin": r["expected_margin"],
        }
        run.log({k: v for k, v in point.items() if v is not None})
    bars = wandb.Table(
        data=[
            [lab, round(100 * float(p), 1)]
            for lab, p in zip(labels, shown["home_win_prob"], strict=True)
        ],
        columns=["game", "home win %"],
    )
    run.log({"slate_home_win_pct": wandb.plot.bar(bars, "game", "home win %", title="Home win %")})


def _live_table(table: pl.DataFrame) -> pl.DataFrame:
    return table.with_columns(
        pl.col("kickoff_utc").cast(pl.String),
        pl.col("created_at").cast(pl.String),
        pl.selectors.float().round(4),
    )
