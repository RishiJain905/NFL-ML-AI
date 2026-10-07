"""Training charts for the weekly refits (visibility only).

Every Tuesday the weekly run retrains the player models (37 LightGBM boosters across the 23
live stats), the team stat totals (8 boosters across 4 stats) and the game model (two ridge
regressions per variant) from scratch on every game through the week before. These functions
add what that training looked like to the week's W&B runs:

- `train_loss/<stat>/<booster>` (x = `lgb/round`): each booster's loss on its training rows,
  round by round (pinball loss for the 10th / 50th / 90th percentile models, Poisson deviance
  for the counts, log loss for the yes / no stats), recorded with LightGBM's own
  `record_evaluation` callback while the model trains (`fit_player_model(evals=...)`);
- `feature_importance/<stat>`: the main booster's top features by gain (share of the total);
- tables `training_summary` (one row per booster: objective, rounds, leaves, learning rate,
  training rows, features, trained through, first and final loss) and `feature_importance`
  (every booster's features);
- the game model: `coefficients/<variant>_<head>` (each feature's weight per standard
  deviation, in points) and the `coefficients` table (also in the feature's own units).

Nothing here changes training: recording the loss reads a number LightGBM computes anyway, and
importance / coefficients are read from the finished models (a test proves the boosters are
byte-identical with and without recording). Logging is fail-soft: a chart that can't be logged
is a line in the step's log, never a failed step.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl

TOP_FEATURES = 15
OBJECTIVE = {"amount": "quantile", "count": "poisson", "prob": "binary"}


@dataclass
class TrainingEntry:
    """One stat's weekly fit: the fitted model, its recorded curves and its features."""

    target: Any  # player_schema.Target
    fit: Any  # player_model.FittedPlayerModel
    evals: dict[str, dict] | None
    features: Sequence[str]
    config: Any  # player_model.PlayerModelConfig

    @property
    def label(self) -> str:
        return self.target.key.replace("-", "_")


def train_curve(evals: dict[str, dict] | None, booster: str) -> tuple[str, list[float]] | None:
    """(metric name, per-round loss on the training rows) for one booster, or None."""
    hist = ((evals or {}).get(booster) or {}).get("train") or {}
    metric = next(iter(hist), None)
    if metric is None:
        return None
    return metric, [float(v) for v in hist[metric]]


def importance(entry: TrainingEntry) -> pl.DataFrame:
    """Every booster's features by gain, with the share of that booster's total gain."""
    rows = []
    for name, b in entry.fit.boosters.items():
        gain = np.asarray(b.feature_importance("gain"), dtype=float)
        total = float(gain.sum()) or 1.0
        for f, g in zip(entry.features, gain, strict=True):
            rows.append(
                {
                    "stat": entry.label,
                    "booster": name,
                    "feature": f,
                    "gain": float(g),
                    "gain_share_pct": 100.0 * float(g) / total,
                }
            )
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def summary_rows(entry: TrainingEntry) -> list[dict[str, Any]]:
    tt = entry.fit.trained_through
    rows = []
    for name in entry.fit.boosters:
        curve = train_curve(entry.evals, name)
        objective = OBJECTIVE.get(entry.target.kind, entry.target.kind)
        if objective == "quantile":
            objective = f"quantile {int(name[1:]) / 100:.2f}"
        rows.append(
            {
                "stat": entry.label,
                "stat_label": getattr(entry.target, "label", entry.label),
                "group": entry.target.group,
                "booster": name,
                "objective": objective,
                "metric": curve[0] if curve else None,
                "rounds": int(entry.fit.boosters[name].current_iteration()),
                "num_leaves": int(entry.config.num_leaves),
                "learning_rate": float(entry.config.learning_rate),
                "train_rows": int(entry.fit.n_train),
                "features": len(entry.features),
                "trained_through": f"{tt[0]}-w{tt[1]:02d}" if tt else None,
                "loss_first_round": curve[1][0] if curve else None,
                "loss_final_round": curve[1][-1] if curve else None,
            }
        )
    return rows


def log_training_charts(
    run: Any, entries: Sequence[TrainingEntry], log: Callable[[str], None] = print
) -> int:
    """Log the weekly refit's training charts to `run`. Returns how many boosters were
    charted; never raises (a failure is one line in `log`)."""
    try:
        import wandb

        run.define_metric("lgb/round")
        run.define_metric("train_loss/*", step_metric="lgb/round")
        series = {}
        for e in entries:
            for name in e.fit.boosters:
                curve = train_curve(e.evals, name)
                if curve is not None:
                    series[f"train_loss/{e.label}/{name}"] = curve[1]
        rounds = max((len(v) for v in series.values()), default=0)
        for i in range(rounds):
            point: dict[str, float] = {"lgb/round": i + 1}
            point.update({k: v[i] for k, v in series.items() if i < len(v)})
            run.log(point)
        imp_frames = [importance(e) for e in entries]
        imp = (
            pl.concat([f for f in imp_frames if f.height], how="vertical_relaxed")
            if any(f.height for f in imp_frames)
            else pl.DataFrame()
        )
        charts: dict[str, Any] = {}
        for e in entries:
            main = "q50" if "q50" in e.fit.boosters else "mean"
            top = (
                imp.filter((pl.col("stat") == e.label) & (pl.col("booster") == main))
                .sort("gain", descending=True)
                .head(TOP_FEATURES)
                if imp.height
                else pl.DataFrame()
            )
            if top.height:
                charts[f"feature_importance/{e.label}"] = wandb.plot.bar(
                    wandb.Table(
                        dataframe=top.select(
                            "feature", pl.col("gain_share_pct").round(2).alias("gain %")
                        ).to_pandas()
                    ),
                    "feature",
                    "gain %",
                    title=f"{e.label}: top features by gain ({main} model)",
                )
        summary = pl.DataFrame([r for e in entries for r in summary_rows(e)])
        if summary.height:
            charts["training_summary"] = wandb.Table(dataframe=summary.to_pandas())
        if imp.height:
            charts["feature_importance"] = wandb.Table(
                dataframe=imp.with_columns(pl.col("gain_share_pct").round(3)).to_pandas()
            )
        if charts:
            run.log(charts)
        run.summary["train/boosters"] = len(series)
        log(f"training charts: {len(series)} boosters' loss curves, {len(entries)} stats")
        return len(series)
    except Exception as exc:  # noqa: BLE001 - charts never fail a step
        log(f"training charts not logged ({type(exc).__name__})")
        return 0


def coefficient_rows(fits: dict[str, Any]) -> pl.DataFrame:
    """The game model's ridge weights per variant and head: per standard deviation of the
    feature (comparable across features) and in the feature's own units."""
    rows = []
    for variant, fit in fits.items():
        fit = getattr(fit, "base", fit)  # game model v1: its v0 ridge base (as the CSV files)
        if not hasattr(fit, "margin_model"):
            continue  # no linear heads
        for head, pipe, feats in (
            ("margin", fit.margin_model, fit.config.mfeats),
            ("total", fit.total_model, fit.config.tfeats),
        ):
            scaler, reg = pipe[0], pipe[-1]
            per_sd = np.asarray(reg.coef_, dtype=float)
            per_unit = per_sd / np.asarray(scaler.scale_, dtype=float)
            for f, sd, unit in zip(feats, per_sd, per_unit, strict=True):
                rows.append(
                    {
                        "variant": variant,
                        "head": head,
                        "feature": f,
                        "points_per_sd": float(sd),
                        "points_per_unit": float(unit),
                    }
                )
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def log_coefficients(run: Any, fits: dict[str, Any], log: Callable[[str], None] = print) -> int:
    """Log the game model's coefficients (bars per variant and head, and a table). Returns
    how many were logged; never raises."""
    try:
        import wandb

        coef = coefficient_rows(fits)
        if coef.is_empty():
            return 0
        charts: dict[str, Any] = {"coefficients": wandb.Table(dataframe=coef.to_pandas())}
        for (variant, head), part in coef.group_by("variant", "head", maintain_order=True):
            charts[f"coefficients/{variant}_{head}"] = wandb.plot.bar(
                wandb.Table(
                    dataframe=part.sort(pl.col("points_per_sd").abs(), descending=True)
                    .select("feature", pl.col("points_per_sd").round(3).alias("points per SD"))
                    .to_pandas()
                ),
                "feature",
                "points per SD",
                title=f"{variant} {head}: points per standard deviation of each feature",
            )
        run.log(charts)
        log(f"training charts: {coef.height} game-model coefficients")
        return coef.height
    except Exception as exc:  # noqa: BLE001 - charts never fail a step
        log(f"game coefficients not logged ({type(exc).__name__})")
        return 0
