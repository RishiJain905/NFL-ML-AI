"""W&B runs for the team stat model: backtests, tuning, the weekly fit and the live scoreboard
(plan P08; documentation/11 -> Game and team targets, 08).

Group `track1-team`, tagged `p08`, `team` and `target:<name>`:
- **backtest** (job_type `backtest`), one run per target (and per variant: `no_market` drops
  the closing-line features, leakage rule 5): walk-forward by week, 2019-2025 reported after
  `BURN_IN` seasons (their predictions calibrate the 80% range and the count dispersion). Live
  curves `bt/*` (step `bt/step`): per-week and cumulative MAE of the model and the baseline,
  improvement %, range coverage, and for counts the Poisson deviance. The end adds summaries
  (`ship/*` = the pre-registered ship rule), a by-season table, the scoreboard rows, feature
  importance, a reliability plot of P(count >= 1) and the predictions. Predictions go to
  `runs/backtests/team/<key>/` and the scoreboard rows to `runs/backtests/team/scoreboard.parquet`.
- **tune** (job_type `tune`): a small grid scored on a 2017-2018 walk-forward, never on the
  reported weeks.
- **train** (job_type `train`): the weekly fit (`run_train`): every target is refit for each
  week of the season up to N, continuing the saved backtest's history, predicts week N and
  writes `predictions_teams.parquet` into the run folder it is given. NOT wired into the weekly
  pipeline yet (P08 hand-off): the output folders are parameters so tests and smoke runs never
  touch a live run.
- **scoreboard** (job_type `eval`): `score_weeks` scores the saved pre-kickoff projections of
  played weeks and upserts `accuracy_scoreboard` rows (`position_group = "TEAM"`).
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nflengine.features import team_stats as TS
from nflengine.features.asof import AsOf, last_asof_week
from nflengine.models import metrics as M
from nflengine.models.backtest import SampleWeights, walk_forward, week_keys
from nflengine.models.player_model import PlayerModelConfig, feature_hash, fit_player_model
from nflengine.models.player_runs import (
    BURN_IN,
    REPORT_SEASONS,
    TUNE_SEASONS,
    fitted_params,
    tuesdays,
    upsert_scoreboard,
)
from nflengine.models.player_schema import SCOREBOARD_SCHEMA, Target
from nflengine.models.team_model import (
    TeamWeekModel,
    count_prob_table,
    live_team_targets,
    poisson_deviance,
    team_config,
)
from nflengine.paths import DataPaths, ensure_data_root
from nflengine.settings import get_config
from nflengine.tracking import dataset_version, git_commit, init_run

GROUP = "track1-team"
FAMILY = "team-model"
MODEL_VERSION = "v1"
MIN_TRAIN_ROWS = 1000  # ~2 seasons of team-games
PRED_FILE = "predictions_teams.parquet"
SCOREBOARD_FILE = "accuracy_scoreboard.parquet"
# the pre-registered ship rule (P08 hand-off): pooled MAE beats the baseline over 2019-2025,
# in at least `SHIP_MIN_SEASONS` seasons, with the 80% range holding `SHIP_COVERAGE` of
# outcomes; takeaways also need a lower Poisson deviance than the league average
SHIP_MIN_SEASONS = 5
SHIP_COVERAGE = (0.75, 0.88)

TEAM_PRED_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "game_id": pl.String(),
    "kickoff_utc": pl.Datetime("us", "UTC"),
    "created_at": pl.Datetime("us", "UTC"),  # when the projection was made (live: run time)
    "team": pl.String(),
    "opponent": pl.String(),
    "home": pl.Boolean(),
    "target": pl.String(),  # Target.name
    "target_label": pl.String(),
    "kind": pl.String(),  # amount | count
    "unit": pl.String(),
    "is_main": pl.Boolean(),
    "p10": pl.Float64(),
    "p50": pl.Float64(),  # the projection (counts: the negative-binomial median)
    "p90": pl.Float64(),
    "mean": pl.Float64(),  # counts: the Poisson mean; amounts: = p50
    "p_ge1": pl.Float64(),  # counts: P(>= 1); null for amounts
    "baseline": pl.Float64(),  # rolling team / opponent average; takeaways: the league average
    "baseline_p50": pl.Float64(),  # the baseline as the same kind of projection (D65)
    "baseline_p_ge1": pl.Float64(),
    "outperformance": pl.Float64(),  # amounts: p50 - baseline; counts: mean - baseline
    "model_version": pl.String(),
    "feature_hash": pl.String(),
    "trained_through": pl.String(),
    "actual": pl.Float64(),  # filled once the game is played (null live)
    "played": pl.Boolean(),  # the game has been played (null until it has)
}
RAW_COLS = ("_p10_raw", "_p90_raw", "_p_raw", "range_param", "range_tail", "scale", "n_train")


def model_config(target: Target, **overrides: Any) -> PlayerModelConfig:
    """LightGBM config of a team target: `team_model.TEAM_PARAMS`, the optional settings block
    `team_model` (a dict with `per_target`), and the sample weights of `training`."""
    cfg = get_config()
    weights = SampleWeights.from_config(cfg.training.get("sample_weights"))
    if overrides.get("weights") is not None:
        weights = overrides.pop("weights")
    overrides.pop("weights", None)
    settings = getattr(cfg, "team_model", None) or {}
    return team_config(target, settings, weights, **overrides)


# ---- data ------------------------------------------------------------------------------------


@dataclass
class TeamData:
    feats: pl.DataFrame  # `features.team_stats.build_team_features`
    inp: TS.TeamInputs | None = None


def build_team_data(
    paths: DataPaths | None = None,
    live_key: AsOf | None = None,
    run_date: dt.date | None = None,
    log: Callable[[str], None] = print,
) -> TeamData:
    """The team feature table for every REG game from 2012 (cheap: about a second).

    Without `live_key` the P03 context is the canonical model-only backtest (finished seasons
    only: the current season's context stays null, which is fine for a backtest of 2019-2025).
    With `live_key` it is the live walk-forward the player model uses
    (`player_runs.game_context_frames`), so the live week's games have one."""
    paths = paths or ensure_data_root()
    if live_key is None:
        context = TS.team_context(paths)
    else:
        from nflengine.features.player_data import load_inputs
        from nflengine.models.player_runs import game_context_frames

        inp0 = load_inputs(paths, log=log)
        context, _ = game_context_frames(paths, inp0, live_key, run_date, log)
    inp = TS.load_team_inputs(paths, context=context, log=log)
    return TeamData(TS.build_team_features(inp), inp)


def target_frame(
    data: TeamData | pl.DataFrame, target: Target, no_market: bool = False, own_only: bool = True
) -> tuple[pl.DataFrame, list[str]]:
    """(rows of one target from 2013, its model features)."""
    feats = data.feats if isinstance(data, TeamData) else data
    frame = TS.team_target_frame(feats.filter(pl.col("season") >= TS.TRAIN_START), target.name)
    cols = TS.feature_columns(frame, target.name, own_only=own_only)
    if no_market:
        cols = [c for c in cols if not c.startswith(TS.MARKET_PREFIX)]
    return frame, cols


# ---- assembling prediction rows -----------------------------------------------------------------


def conform_team(df: pl.DataFrame) -> pl.DataFrame:
    """Columns of `TEAM_PRED_SCHEMA`, in order, cast (missing columns become null)."""
    cols = [
        pl.col(c).cast(t) if c in df.columns else pl.lit(None, dtype=t).alias(c)
        for c, t in TEAM_PRED_SCHEMA.items()
    ]
    return df.select(cols)


def assemble(
    preds: pl.DataFrame,
    frame: pl.DataFrame,
    target: Target,
    *,
    model_version: str,
    fhash: str,
    trained_through: str,
    created_at: pl.DataFrame | dt.datetime,
    keep_raw: bool = False,
) -> pl.DataFrame:
    """Model output rows (`TeamWeekModel`) -> `TEAM_PRED_SCHEMA` rows (+ the `RAW_COLS` the
    range calibration is seeded from when `keep_raw`)."""
    info = frame.select("game_id", "team", "opponent", "home", "kickoff_utc")
    p = preds.join(info, on=["game_id", "team"], how="left")
    if isinstance(created_at, dt.datetime):
        p = p.with_columns(pl.lit(created_at).cast(pl.Datetime("us", "UTC")).alias("created_at"))
    else:
        p = p.join(created_at, on=["season", "week"], how="left")
    proj = pl.col("mean") if target.kind == "count" else pl.col("p50")
    p = p.with_columns(
        pl.lit(target.name).alias("target"),
        pl.lit(target.label).alias("target_label"),
        pl.lit(target.kind).alias("kind"),
        pl.lit(target.unit).alias("unit"),
        pl.lit(target.main).alias("is_main"),
        (proj - pl.col("baseline")).alias("outperformance"),
        pl.lit(model_version).alias("model_version"),
        pl.lit(fhash).alias("feature_hash"),
        pl.lit(trained_through).alias("trained_through"),
        pl.col("y").alias("actual"),
        pl.col("y").is_not_null().alias("played"),
    )
    out = conform_team(p)
    if keep_raw:
        raw = [c for c in RAW_COLS if c in preds.columns]
        out = out.hstack(preds.select(raw))
    return out


# ---- scoring ---------------------------------------------------------------------------------


def _scored(preds: pl.DataFrame) -> pl.DataFrame:
    return preds.filter(
        pl.col("played").fill_null(False)
        & pl.col("actual").is_not_null()
        & pl.col("baseline").is_not_null()
    )


def summarize(preds: pl.DataFrame) -> dict[str, float]:
    """Pooled metrics over the scored rows (the model vs its baseline on the same rows).

    `mae_baseline` is the baseline as a projection of the same kind (`baseline_p50`, D65);
    `mae_baseline_mean` the raw average, for transparency. Counts add the Poisson deviance of
    the mean (model and baseline), and the Brier score / ECE of P(count >= 1)."""
    r = _scored(preds)
    if r.is_empty():
        return {"n_scored": 0.0}
    y = r["actual"].to_numpy()
    p50, bp50, base = (r[c].to_numpy() for c in ("p50", "baseline_p50", "baseline"))
    out: dict[str, float] = {
        "n_scored": float(r.height),
        "mae_model": float(np.mean(np.abs(y - p50))),
        "mae_baseline": float(np.mean(np.abs(y - bp50))),
        "mae_baseline_mean": float(np.mean(np.abs(y - base))),
        "coverage_80": float(np.mean((y >= r["p10"].to_numpy()) & (y <= r["p90"].to_numpy()))),
        "mean_actual": float(y.mean()),
        "mean_projection": float(np.mean(r["mean"].to_numpy())),
        "mean_baseline": float(base.mean()),
    }
    out["improvement_pct"] = 100 * (out["mae_baseline"] - out["mae_model"]) / out["mae_baseline"]
    if r["kind"][0] == "count":
        mean = r["mean"].to_numpy()
        out["deviance_model"] = poisson_deviance(y, mean)
        out["deviance_baseline"] = poisson_deviance(y, base)
        out["deviance_improvement_pct"] = (
            100 * (out["deviance_baseline"] - out["deviance_model"]) / out["deviance_baseline"]
        )
        hit = (y >= 1).astype(float)
        out["brier_p_ge1"] = M.brier(r["p_ge1"], hit)
        out["brier_baseline_p_ge1"] = M.brier(r["baseline_p_ge1"], hit)
        out["ece_p_ge1"] = M.ece(r["p_ge1"], hit)
        out["ece_baseline_p_ge1"] = M.ece(r["baseline_p_ge1"], hit)
        out["share_ge1"] = float(hit.mean())
    ok = np.isfinite(y - base) & np.isfinite(r["outperformance"].to_numpy())
    if ok.sum() > 10:
        from scipy.stats import spearmanr

        out["spearman_outperformance"] = float(
            spearmanr(r["outperformance"].to_numpy()[ok], (y - base)[ok]).statistic
        )
    return out


def by_season(preds: pl.DataFrame) -> pl.DataFrame:
    rows = [
        {"season": int(s), **summarize(part)}
        for (s,), part in preds.group_by("season", maintain_order=True)
    ]
    return pl.DataFrame(rows).sort("season") if rows else pl.DataFrame()


def ship_rule(target: Target, pooled: Mapping[str, float], per: pl.DataFrame) -> dict[str, float]:
    """The pre-registered ship rule (P08 hand-off) as numbers: `ship/<check>` is 1.0 / 0.0.

    Pooled MAE beats the baseline (a count's baseline is its negative-binomial median); better
    in `SHIP_MIN_SEASONS` or more of the reported seasons; coverage of the 80% range inside
    `SHIP_COVERAGE`; takeaways also a lower Poisson deviance than the league average."""
    better = float((per["mae_model"] < per["mae_baseline"]).sum()) if per.height else 0.0
    lo, hi = SHIP_COVERAGE
    checks = {
        "beats_baseline": float(pooled.get("mae_model", np.inf) < pooled.get("mae_baseline", 0)),
        "seasons_ok": float(better >= SHIP_MIN_SEASONS),
        "coverage_ok": float(lo <= pooled.get("coverage_80", 0.0) <= hi),
    }
    if target.name == "takeaways":
        checks["deviance_ok"] = float(
            pooled.get("deviance_model", np.inf) < pooled.get("deviance_baseline", 0)
        )
    return {
        "seasons_beating_baseline": better,
        "seasons": float(per.height),
        **{f"ship/{k}": v for k, v in checks.items()},
        "ship/pass": float(all(v == 1.0 for v in checks.values())),
    }


def scoreboard_rows(preds: pl.DataFrame, mode: str) -> pl.DataFrame:
    """One `SCOREBOARD_SCHEMA` row per (season, week, target) from scored rows
    (`position_group = "TEAM"`). Counts fill the Brier / ECE of P(count >= 1)."""
    r = _scored(preds)
    if r.is_empty():
        return pl.DataFrame(schema=SCOREBOARD_SCHEMA)
    rows = []
    for (season, week, target, label), part in r.group_by(
        "season", "week", "target", "target_label", maintain_order=True
    ):
        y = part["actual"].to_numpy()
        row: dict[str, Any] = {
            "season": season,
            "week": week,
            "target": target,
            "position_group": "TEAM",
            "target_label": label,
            "n_scored": part.height,
            "n_not_played": 0,
            "mae_model": float(np.mean(np.abs(y - part["p50"].to_numpy()))),
            "mae_baseline": float(np.mean(np.abs(y - part["baseline_p50"].to_numpy()))),
            "coverage_80": float(
                np.mean((y >= part["p10"].to_numpy()) & (y <= part["p90"].to_numpy()))
            ),
            "mode": mode,
        }
        row["improvement_pct"] = (
            100 * (row["mae_baseline"] - row["mae_model"]) / row["mae_baseline"]
        )
        if part["p_ge1"].null_count() < part.height:
            hit = (y >= 1).astype(float)
            row["brier_model"] = M.brier(part["p_ge1"], hit)
            row["brier_baseline"] = M.brier(part["baseline_p_ge1"], hit)
            row["calibration_ece"] = M.ece(part["p_ge1"], hit)
        rows.append(row)
    df = pl.DataFrame(rows)
    return df.select(
        [
            pl.col(c).cast(t) if c in df.columns else pl.lit(None, dtype=t).alias(c)
            for c, t in SCOREBOARD_SCHEMA.items()
        ]
    ).sort("season", "week", "target")


# ---- backtest --------------------------------------------------------------------------------


class TeamLogger:
    """`walk_forward` on_week callback: per-week and cumulative curves for reported weeks."""

    def __init__(self, run, report: Sequence[int], target: Target):
        self.run = run
        self.report = set(report)
        self.target = target
        self.step = 0
        self.done: list[pl.DataFrame] = []
        if run is not None:
            run.define_metric("bt/step")
            run.define_metric("bt/*", step_metric="bt/step")

    @staticmethod
    def _mae(df: pl.DataFrame, col: str) -> float:
        return float((df["y"] - df[col]).abs().mean())

    @staticmethod
    def _cov(df: pl.DataFrame) -> float:
        return float(((df["y"] >= df["p10"]) & (df["y"] <= df["p90"])).mean())

    def __call__(self, key: AsOf, out: pl.DataFrame) -> None:
        if key.season not in self.report:
            return
        r = out.filter(pl.col("y").is_not_null() & pl.col("baseline").is_not_null())
        if r.is_empty():
            return
        self.done.append(r)
        cum = pl.concat(self.done)
        self.step += 1
        wm, wb = self._mae(r, "p50"), self._mae(r, "baseline_p50")
        cm, cb = self._mae(cum, "p50"), self._mae(cum, "baseline_p50")
        log = {
            "bt/step": self.step,
            "bt/season": key.season,
            "bt/week": key.week,
            "bt/n": r.height,
            "bt/mae_model": wm,
            "bt/mae_baseline": wb,
            "bt/improvement_pct": 100 * (wb - wm) / wb if wb else 0.0,
            "bt/coverage_80": self._cov(r),
            "bt/cum_mae_model": cm,
            "bt/cum_mae_baseline": cb,
            "bt/cum_improvement_pct": 100 * (cb - cm) / cb if cb else 0.0,
            "bt/cum_coverage_80": self._cov(cum),
            "bt/range_param": float(r["range_param"][0]),
        }
        if self.target.kind == "count":
            dm = poisson_deviance(r["y"].to_numpy(), r["mean"].to_numpy())
            db = poisson_deviance(r["y"].to_numpy(), r["baseline"].to_numpy())
            cdm = poisson_deviance(cum["y"].to_numpy(), cum["mean"].to_numpy())
            cdb = poisson_deviance(cum["y"].to_numpy(), cum["baseline"].to_numpy())
            log.update(
                {
                    "bt/deviance_model": dm,
                    "bt/deviance_baseline": db,
                    "bt/cum_deviance_model": cdm,
                    "bt/cum_deviance_baseline": cdb,
                    "bt/cum_deviance_improvement_pct": 100 * (cdb - cdm) / cdb if cdb else 0.0,
                }
            )
        if self.run is not None:
            self.run.log(log)


def _curve(
    frame: pl.DataFrame,
    target: Target,
    feats: list[str],
    config: PlayerModelConfig,
    season: int,
) -> dict[str, Any] | None:
    """The season's opening fit replayed with the previous season held out: per-round
    training and validation loss of the P50 / Poisson booster."""
    lab = frame.filter(pl.col("y").is_not_null())
    train = lab.filter(pl.col("season") < season - 1)
    valid = lab.filter(pl.col("season") == season - 1)
    if train.height < MIN_TRAIN_ROWS or valid.is_empty():
        return None
    w = config.weights.for_rows(train["season"], season - 1)
    evals: dict[str, dict] = {}
    fit_player_model(train, w, target, feats, config, valid=valid, evals=evals)
    hist = evals.get("q50" if target.kind == "amount" else "mean", {})
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


def backtest_label(target: Target, no_market: bool = False, **overrides: Any) -> str:
    """Run name and D: folder: the bare target key only for the canonical configuration, so a
    research variant (`_nomarket`, overrides) never overwrites the canonical files."""
    parts = [target.key] + (["nomarket"] if no_market else [])
    for k, v in sorted(overrides.items()):
        if v is not None:
            parts.append(f"{k}-{v}")
    return "_".join(parts)


def run_backtest(
    target: Target,
    data: TeamData | None = None,
    report_seasons: Sequence[int] = REPORT_SEASONS,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    save: bool = True,
    use_wandb: bool = True,
    tags: Sequence[str] = (),
    num_threads: int | None = None,
    no_market: bool = False,
    root: Path | None = None,
    **overrides: Any,
) -> BacktestResult:
    """Walk-forward backtest of one team target (model vs baseline on the same rows).

    `no_market` drops the closing-line features (`mkt_*`; leakage rule 5). `root` is where the
    predictions and summary go (default `runs/backtests/team`); only the canonical
    configuration (no `no_market`, no overrides) feeds the scoreboard file there."""
    paths = ensure_data_root()
    data = data or build_team_data(paths, log=log)
    label = backtest_label(target, no_market=no_market, **overrides)
    config = model_config(target, num_threads=num_threads, **overrides)
    frame, feats = target_frame(data, target, no_market=no_market)
    fhash = feature_hash(feats)
    first = min(report_seasons) - BURN_IN
    keys = week_keys(frame.filter(pl.col("y").is_not_null()), range(first, max(report_seasons) + 1))
    cfg = {
        "model": f"team stat model {MODEL_VERSION} (documentation/11)",
        "target": target.name,
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
                "p08",
                "team",
                f"target:{target.name}",
                *(["no-market"] if no_market else []),
                *tags,
            ],
            launched_by=launched_by,
            name=f"backtest-{label}",
        )
    try:
        model = TeamWeekModel(target, feats, config)
        logger = TeamLogger(run, report_seasons, target)
        created = tuesdays(frame.select("season", "week", "kickoff_utc"))
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
        preds = assemble(
            out,
            frame,
            target,
            model_version=f"{FAMILY}-{MODEL_VERSION}:backtest",
            fhash=fhash,
            trained_through="walk-forward",
            created_at=created,
            keep_raw=True,
        )
        reported = preds.filter(pl.col("season").is_in(list(report_seasons)))
        summary: dict[str, Any] = summarize(reported)
        per = by_season(reported)
        summary.update(ship_rule(target, summary, per))
        board = scoreboard_rows(reported, "backtest")
        url = None
        if run is not None:
            run.summary.update(summary)
            _final_logs(
                run, reported, per, board, model, feats, target, frame, config, report_seasons
            )
            url = run.url
        log(f"{target.key}: {json.dumps({k: round(float(v), 4) for k, v in summary.items()})}")
    finally:
        if run is not None:
            run.finish()
    saved = None
    if save:
        base = root or (paths.runs / "backtests" / "team")
        folder = base / label
        folder.mkdir(parents=True, exist_ok=True)
        preds.write_parquet(folder / PRED_FILE, compression="zstd")
        (folder / "summary.json").write_text(
            json.dumps({**summary, "config": cfg, "url": url}, indent=2, default=str),
            encoding="utf-8",
        )
        if label == target.key:  # canonical runs feed the backtest scoreboard
            upsert_scoreboard(base / "scoreboard.parquet", board)
        saved = folder
    return BacktestResult(preds, summary, url, saved)


def _final_logs(run, reported, per, board, model, feats, target, frame, config, report) -> None:
    import wandb

    tables: dict[str, Any] = {
        "by_season": wandb.Table(dataframe=per.to_pandas()),
        "accuracy_scoreboard": wandb.Table(dataframe=board.to_pandas()),
    }
    if per.height:
        tables["mae_by_season"] = wandb.plot.line_series(
            xs=per["season"].to_list(),
            ys=[per["mae_model"].to_list(), per["mae_baseline"].to_list()],
            keys=["model", "baseline"],
            title=f"{target.label}: MAE by season (lower is better)",
            xname="season",
        )
        tables["coverage_by_season"] = wandb.plot.line_series(
            xs=per["season"].to_list(),
            ys=[per["coverage_80"].to_list(), [0.8] * per.height],
            keys=["80% range", "target 0.80"],
            title=f"{target.label}: share of results inside the P10-P90 range",
            xname="season",
        )
        if target.kind == "count":
            tables["deviance_by_season"] = wandb.plot.line_series(
                xs=per["season"].to_list(),
                ys=[per["deviance_model"].to_list(), per["deviance_baseline"].to_list()],
                keys=["model", "baseline"],
                title=f"{target.label}: Poisson deviance by season (lower is better)",
                xname="season",
            )
    fit = model.last
    if fit is not None:
        main = fit.boosters["q50" if target.kind == "amount" else "mean"]
        imp = (
            pl.DataFrame({"feature": feats, "gain": main.feature_importance("gain").astype(float)})
            .sort("gain", descending=True)
            .head(25)
        )
        tables["feature_importance"] = wandb.plot.bar(
            wandb.Table(dataframe=imp.to_pandas()),
            "feature",
            "gain",
            title=f"{target.label}: feature importance (gain, last fit)",
        )
    if target.kind == "count":
        rel = count_prob_table(
            reported["p_ge1"].to_numpy(), (reported["actual"] >= 1).cast(pl.Float64).to_numpy()
        )
        if rel.height:
            tables["reliability_p_ge1"] = wandb.plot.line_series(
                xs=rel["mean_pred"].to_list(),
                ys=[rel["observed"].to_list(), rel["mean_pred"].to_list()],
                keys=["observed share", "perfect"],
                title=f"{target.label}: P(at least one), predicted vs observed",
                xname="predicted probability",
            )
            tables["reliability_table"] = wandb.Table(dataframe=rel.to_pandas())
    for s in report[1:]:
        c = _curve(frame, target, feats, config, s)
        if c is None:
            continue
        tables[f"lgb/curve_{s}"] = wandb.plot.line_series(
            xs=list(range(1, len(c["train"]) + 1)),
            ys=[c["train"], c["valid"]],
            keys=["train", f"valid ({s - 1} held out)"],
            title=f"{target.label}: {c['metric']} per boosting round, fit before {s - 1}",
            xname="boosting round",
        )
    sample = reported.filter(pl.col("season") == max(report)).select(
        "season", "week", "team", "opponent", "p10", "p50", "p90", "baseline", "actual"
    )
    tables["predictions"] = wandb.Table(
        dataframe=sample.with_columns(pl.selectors.float().round(2)).to_pandas()
    )
    run.log(tables)


# ---- tuning ----------------------------------------------------------------------------------

TUNE_GRID = {
    "num_leaves": [4, 8],
    "min_data_in_leaf": [100, 200],
    "n_estimators": [60, 100, 200, 400],
}


def tune_score(target: Target) -> str:
    """The metric a configuration is picked on: an amount's MAE, a count's Poisson deviance
    (the proper score of a count's mean; the MAE of a whole-number median is quantized and
    jumps with the rounding of a tenth of a goal)."""
    return "tune/mae_model" if target.kind == "amount" else "tune/deviance_model"


def tune_objective(
    frame: pl.DataFrame, target: Target, feats: list[str], config: PlayerModelConfig
) -> dict[str, float]:
    """Walk-forward over `TUNE_SEASONS`, never the reported 2019-2025 weeks: MAE of the P50 /
    negative-binomial median against the baseline's, plus the Poisson deviance for counts."""
    from nflengine.features.asof import before_expr
    from nflengine.models.player_model import _x, count_quantiles, nb_dispersion

    lab = frame.filter(pl.col("y").is_not_null())
    errs, base, mus, bmus, ys = [], [], [], [], []
    for k in week_keys(lab, TUNE_SEASONS):
        train = lab.filter(before_expr(k))
        test = lab.filter((pl.col("season") == k.season) & (pl.col("week") == k.week))
        if train.height < MIN_TRAIN_ROWS:
            continue
        w = config.weights.for_rows(train["season"], k.season)
        fit = fit_player_model(train, w, target, feats, config, main_only=True)
        main = fit.boosters["q50" if target.kind == "amount" else "mean"]
        pred = main.predict(_x(test, feats))
        b = test["baseline"].to_numpy().astype(float)
        y = test["y"].to_numpy()
        if target.kind == "count":
            r = nb_dispersion(train["y"].to_numpy(), main.predict(_x(train, feats)))
            mus.append(pred)
            bmus.append(b)
            ys.append(y)
            pred = count_quantiles(pred, r, (0.5,))[:, 0]
            b = count_quantiles(np.clip(b, 1e-6, None), r, (0.5,))[:, 0]
        errs.append(np.abs(y - pred))
        base.append(np.abs(y - b))
    e, bb = np.concatenate(errs), np.concatenate(base)
    out = {
        "tune/mae_model": float(e.mean()),
        "tune/mae_baseline": float(bb.mean()),
        "tune/improvement_pct": float(100 * (bb.mean() - e.mean()) / bb.mean()),
        "tune/n": float(e.size),
    }
    if target.kind == "count":
        y, mu, bmu = np.concatenate(ys), np.concatenate(mus), np.concatenate(bmus)
        dm, db = poisson_deviance(y, mu), poisson_deviance(y, bmu)
        out.update(
            {
                "tune/deviance_model": dm,
                "tune/deviance_baseline": db,
                "tune/deviance_improvement_pct": 100 * (db - dm) / db,
            }
        )
    return out


def run_tune(
    target: Target,
    data: TeamData | None = None,
    grid: dict[str, list] | None = None,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    num_threads: int | None = None,
    use_wandb: bool = True,
) -> pl.DataFrame:
    """Score every configuration of `grid` (default `TUNE_GRID`) on the 2017-2018 walk-forward
    and return the results, best first (`tune_score`). One W&B `tune` run per target: a point
    per configuration (`tune/step`, the settings as `tune/<name>`, the scores), the full table
    and the best configuration in the summary (`best/*`)."""
    data = data or build_team_data(log=log)
    frame, feats = target_frame(data, target)
    grid = grid or TUNE_GRID
    names = list(grid)
    score = tune_score(target)
    run = None
    if use_wandb:
        run = init_run(
            GROUP,
            "tune",
            config={
                "target": target.name,
                "tune_seasons": list(TUNE_SEASONS),
                "selection_metric": score,
                "grid": {k: list(v) for k, v in grid.items()},
                "feature_hash": feature_hash(feats),
            },
            tags=["p08", "team", "sweep", f"target:{target.name}"],
            launched_by=launched_by,
            name=f"tune-{target.key}",
        )
        run.define_metric("tune/step")
        run.define_metric("tune/*", step_metric="tune/step")
    results: list[dict[str, Any]] = []
    try:
        for i, values in enumerate(itertools.product(*grid.values()), start=1):
            params = dict(zip(names, values, strict=True))
            res = tune_objective(
                frame, target, feats, model_config(target, num_threads=num_threads, **params)
            )
            results.append({**params, **res})
            if run is not None:
                run.log({"tune/step": i, **{f"tune/{k}": v for k, v in params.items()}, **res})
            log(f"{target.key} {params}: {score.removeprefix('tune/')} {res[score]:.4f}")
        out = pl.DataFrame(results).sort(score)
        if run is not None:
            import wandb

            best = out.row(0, named=True)
            run.summary.update({f"best/{k}": v for k, v in best.items()})
            run.log({"tune_results": wandb.Table(dataframe=out.to_pandas())})
    finally:
        if run is not None:
            run.finish()
    return out


# ---- weekly train ---------------------------------------------------------------------------


def _seed_history(root: Path, target: Target, season: int) -> pl.DataFrame:
    """The canonical backtest's seasons before `season` (up to `BURN_IN` back), as the
    walk-forward history a weekly refit calibrates its range / dispersion on."""
    path = root / target.key / PRED_FILE
    if not path.exists():
        return pl.DataFrame()
    p = pl.read_parquet(path).filter(
        (pl.col("season") >= season - BURN_IN) & (pl.col("season") < season)
    )
    cols = ["season", "week", "game_id", "team", "p10", "p50", "p90", "mean", "actual"]
    cols += ["_p10_raw", "_p90_raw"] if target.kind == "amount" else ["_p_raw"]
    return p.select([c for c in cols if c in p.columns]).rename({"actual": "y"})


def keep_started_teams(
    preds: pl.DataFrame, path: Path, now: dt.datetime
) -> tuple[pl.DataFrame, int]:
    """Re-running a week never replaces projections for games that already kicked off: their
    saved rows (made before kickoff) are kept and the new ones dropped."""
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
    return pl.concat([conform_team(started), new], how="vertical_relaxed"), len(games)


def run_train(
    season: int,
    week: int | None = None,
    launched_by: str | None = None,
    promote: bool = False,
    log: Callable[[str], None] = print,
    run_date: dt.date | None = None,
    targets: Sequence[Target] | None = None,
    use_wandb: bool = True,
    run_dir: Path | None = None,
    model_dir: Path | None = None,
    backtest_root: Path | None = None,
    data: TeamData | None = None,
) -> dict[str, Any]:
    """Weekly fit: predict every team target for week N of `season` (default the latest
    predictable week) and write `predictions_teams.parquet` into `run_dir`.

    Not wired into `weekly.py` (P08 hand-off). Output locations are parameters: `run_dir`
    (default the live `runs/<season>/week<NN>/`), `model_dir` (default
    `models/team-model/<season>-w<NN>/`), `backtest_root` (the saved walk-forward the range is
    seeded from; default `runs/backtests/team`), so tests and smoke runs can stay off the live
    folders. Like the player model: no projection is made after kickoff, and the saved
    pre-kickoff rows of started games are kept. Writes the earlier weeks' walk-forward rows to
    `team_walkforward.parquet` next to the week folder and upserts their scoreboard rows
    (`mode = "backtest"`) into `accuracy_scoreboard.parquet` there. `promote` adds the
    `production` alias to the W&B artifact: never set it from a smoke run."""
    import wandb

    paths = ensure_data_root()
    targets = tuple(targets) if targets is not None else live_team_targets()
    if not targets:
        raise ValueError(
            "no team targets to project: pass `targets` or list keys in settings "
            "`team_model.live_targets` (the P08 ship decision)"
        )
    games = pl.read_parquet(paths.curated / "games.parquet")
    latest = last_asof_week(games, season)
    if latest is None:
        raise ValueError(f"no games for season {season}")
    week = week or latest
    if week > latest:
        raise ValueError(f"week {week} of {season} isn't predictable yet (latest {latest})")
    key = AsOf(season, week)
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    data = data or build_team_data(paths, live_key=key, run_date=run_date, log=log)
    tag = f"{season}-w{week:02d}"
    version = f"{FAMILY}-{MODEL_VERSION}:{tag}"
    run_dir = run_dir or paths.run_dir(season, week)
    model_dir = model_dir or paths.models / FAMILY / tag
    root = backtest_root or paths.runs / "backtests" / "team"
    created = tuesdays(data.feats.select("season", "week", "kickoff_utc"))
    model_dir.mkdir(parents=True, exist_ok=True)
    all_preds, walk, meta_t = [], [], {}
    for target in targets:
        frame, feats = target_frame(data, target)
        frame = frame.filter(
            (pl.col("season") < season) | ((pl.col("season") == season) & (pl.col("week") <= week))
        )
        config = model_config(target)
        model = TeamWeekModel(target, feats, config)
        out = walk_forward(
            frame,
            [AsOf(season, w) for w in range(1, week + 1)],
            model,
            label="y",
            weights=config.weights,
            min_train_rows=MIN_TRAIN_ROWS,
            history=_seed_history(root, target, season),
        )
        fit = model.last
        assert fit is not None
        tt = (
            f"{fit.trained_through[0]}-w{fit.trained_through[1]:02d}"
            if fit.trained_through
            else "none"
        )
        fhash = feature_hash(feats)
        all_preds.append(
            assemble(
                out.filter(pl.col("week") == week),
                frame,
                target,
                model_version=version,
                fhash=fhash,
                trained_through=tt,
                created_at=now,
            )
        )
        earlier = out.filter(pl.col("week") < week)
        if earlier.height:
            walk.append(
                assemble(
                    earlier,
                    frame,
                    target,
                    model_version=f"{FAMILY}-{MODEL_VERSION}:walk-forward",
                    fhash=fhash,
                    trained_through="walk-forward",
                    created_at=created,
                )
            )
        for name, b in fit.boosters.items():
            b.save_model(str(model_dir / f"{target.key}-{name}.txt"))
        meta_t[target.key] = {
            "features": len(feats),
            "feature_hash": fhash,
            "n_train": fit.n_train,
            "trained_through": tt,
            "range_param": fit.shift if target.kind == "amount" else fit.dispersion,
            "fitted": fitted_params(fit),  # shift, dispersion, tail, calibration (P08)
            "config": config.as_dict(),
        }
        log(f"{target.key}: {len(all_preds[-1])} projections (trained on {fit.n_train:,} rows)")
    preds = pl.concat(all_preds, how="vertical_relaxed")
    # the refits take a moment: the projections exist from now, so the kickoff cutoff and
    # `created_at` use the save time (a game kicking off mid-run gets no new projection)
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    preds = preds.with_columns(pl.lit(now).cast(pl.Datetime("us", "UTC")).alias("created_at"))
    started = preds.filter(pl.col("kickoff_utc") <= now)["game_id"].n_unique()
    if started:
        log(f"no new projections for {started} game(s) already kicked off")
    preds = preds.filter(pl.col("kickoff_utc") > now).with_columns(
        pl.lit(None, dtype=pl.Float64).alias("actual"),
        pl.lit(None, dtype=pl.Boolean).alias("played"),
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    pred_path = run_dir / PRED_FILE
    preds, kept = keep_started_teams(preds, pred_path, now)
    if kept:
        log(f"kept the saved projections of {kept} game(s) that already kicked off")
    from nflengine.ops.lock import write_parquet_atomic

    # atomic (Sol review): an interrupted re-run never loses started games' saved rows
    write_parquet_atomic(preds, pred_path, compression="zstd")
    season_dir = run_dir.parent
    if walk:
        wf = pl.concat(walk, how="vertical_relaxed")
        write_parquet_atomic(wf, season_dir / "team_walkforward.parquet", compression="zstd")
        upsert_scoreboard(season_dir / SCOREBOARD_FILE, scoreboard_rows(wf, "backtest"))
    meta = {
        "model_version": version,
        "created_at": now.isoformat(),
        "targets": meta_t,
        "git_commit": git_commit(),
        "dataset_version": dataset_version(paths),
    }
    (model_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    url, aliases = None, [tag, *(["production"] if promote else [])]
    if use_wandb:
        run = init_run(
            GROUP,
            "train",
            config={**meta, "season": season, "week": week},
            tags=["p08", "team", f"season:{season}", f"week:{week:02d}", "prod-candidate"],
            launched_by=launched_by,
            name=f"train-{tag}",
        )
        try:
            log_slate(run, preds)
            card = (
                Path(__file__).resolve().parents[3]
                / "documentation"
                / "model_cards"
                / "team-stats-v1.md"
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
    log(f"team projections -> {pred_path} ({preds.height} rows)")
    return {
        "predictions": pred_path,
        "models": model_dir,
        "url": url,
        "table": preds,
        "aliases": aliases,
        "kept": kept,
    }


def log_slate(run, preds: pl.DataFrame) -> None:
    """The week's team projections as a table (a one-shot run draws nothing else)."""
    import wandb

    show = (
        preds.select("team", "opponent", "target_label", "p10", "p50", "p90", "baseline")
        .sort("target_label", "team")
        .with_columns(pl.selectors.float().round(1))
    )
    run.log({"projections_teams": wandb.Table(dataframe=show.to_pandas())})
    run.summary.update({"projections": preds.height, "teams": preds["team"].n_unique()})


# ---- live scoreboard ------------------------------------------------------------------------


def score_predictions(preds: pl.DataFrame, feats: pl.DataFrame) -> pl.DataFrame:
    """Fill `actual` / `played` from the team feature table's labels (null for games not
    played yet)."""
    if preds.is_empty():
        return preds
    parts = []
    for (name,), part in preds.group_by("target", maintain_order=True):
        lab = feats.select("game_id", "team", pl.col(str(name)).cast(pl.Float64).alias("_actual"))
        j = part.drop("actual", "played").join(lab, on=["game_id", "team"], how="left")
        parts.append(
            j.with_columns(
                pl.col("_actual").alias("actual"), pl.col("_actual").is_not_null().alias("played")
            ).drop("_actual")
        )
    return pl.concat(parts, how="vertical_relaxed").select(list(preds.columns))


def score_weeks(
    season: int,
    weeks: Sequence[int],
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    use_wandb: bool = True,
    season_dir: Path | None = None,
    board_path: Path | None = None,
    data: TeamData | None = None,
) -> pl.DataFrame:
    """Score the saved pre-kickoff projections of every played week in `weeks`, upsert the
    live scoreboard rows (`position_group = "TEAM"`) and log the table + curves to W&B once.

    `season_dir` (default `runs/<season>`) holds the `week<NN>/predictions_teams.parquet`
    files; `board_path` defaults to its `accuracy_scoreboard.parquet`. Re-scoring is
    idempotent (rows are replaced by key)."""
    paths = ensure_data_root()
    season_dir = season_dir or paths.runs / str(season)
    board_path = board_path or season_dir / SCOREBOARD_FILE
    files = {w: season_dir / f"week{w:02d}" / PRED_FILE for w in weeks}
    files = {w: p for w, p in files.items() if p.exists()}
    if not files:
        log(f"no saved team projections for {season} weeks {list(weeks)}: nothing to score")
        return pl.DataFrame(schema=SCOREBOARD_SCHEMA)
    data = data or build_team_data(paths, log=lambda *_: None)
    feats = data.feats.filter(pl.col("season") == season)
    all_rows = []
    for w, path in sorted(files.items()):
        preds = pl.read_parquet(path).filter(pl.col("created_at") < pl.col("kickoff_utc"))
        scored = score_predictions(preds, feats)
        scored.write_parquet(path.with_name("predictions_teams_scored.parquet"))
        rows = scoreboard_rows(scored, "live")
        all_rows.append(rows)
        log(f"scored {season} week {w}: {rows.height} scoreboard rows")
    board = upsert_scoreboard(board_path, pl.concat(all_rows))
    if use_wandb:
        log_scoreboard(season, max(files), board, launched_by)
    return board


def log_scoreboard(season: int, week: int, board: pl.DataFrame, launched_by: str | None) -> str:
    """W&B `accuracy_scoreboard` table + `scoreboard/*` improvement curves by week (team rows)."""
    import wandb

    team = board.filter(pl.col("position_group") == "TEAM")
    run = init_run(
        GROUP,
        "eval",
        config={"season": season, "week": week},
        tags=["p08", "team", "scoreboard", f"season:{season}", f"week:{week:02d}"],
        launched_by=launched_by,
        name=f"scoreboard-team-{season}-w{week:02d}",
    )
    try:
        run.define_metric("scoreboard/week")
        run.define_metric("scoreboard/*", step_metric="scoreboard/week")
        live = team.filter(pl.col("mode") == "live")
        for (w,), part in (
            (live if live.height else team).sort("week").group_by("week", maintain_order=True)
        ):
            point: dict[str, float] = {"scoreboard/week": int(w)}
            for r in part.iter_rows(named=True):
                k = f"{r['target']}_team"
                if r["improvement_pct"] is not None:
                    point[f"scoreboard/improvement_{k}"] = float(r["improvement_pct"])
                if r["coverage_80"] is not None:
                    point[f"scoreboard/coverage_{k}"] = float(r["coverage_80"])
            run.log(point)
        run.log({"accuracy_scoreboard_team": wandb.Table(dataframe=team.to_pandas())})
        return run.url
    finally:
        run.finish()


__all__ = [
    "TEAM_PRED_SCHEMA",
    "build_team_data",
    "run_backtest",
    "run_train",
    "run_tune",
    "score_weeks",
]
