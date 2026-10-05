"""Team stat model v1 (plan P08; documentation/11 -> Game and team targets).

Five targets on one row per (team, regular-season game): `pass_yds` and `rush_yds` (amounts:
quantile LightGBM, P50 = the projection, P10-P90 the 80% range), `sacks_made`, `sacks_taken`
and `takeaways` (counts: a Poisson LightGBM mean + a negative binomial). The fit is the player
model's (`fit_player_model`: quantile boosters + a conformal shift fitted on the model's own
earlier walk-forward misses; Poisson boosters + a dispersion and a tail level from the same
history), so a team-stat range is calibrated exactly like a player one. What this module adds:

- `TEAM_TARGETS`, `live_team_targets` (the ones the weekly run projects: settings
  `team_model.live_targets`, empty until the ship decision) and the per-target LightGBM settings
  `TEAM_PARAMS` (shallower trees and
  fewer rounds than the player defaults: a team-game table has ~500 rows a season, 3-5k
  training rows; picked by a small grid on 2017-2018 only, `team_runs.run_tune`);
- `TeamWeekModel`, the `walk_forward` model (the player one needs a `player_id`), which also
  returns P(count >= 1) for the model and for the baseline (`p_ge1`, `baseline_p_ge1`, the
  player model's recalibrated negative-binomial probability);
- the count metrics: `poisson_deviance` and `count_prob_table`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import polars as pl

from nflengine.models.backtest import SampleWeights
from nflengine.models.player_model import (
    FittedPlayerModel,
    PlayerModelConfig,
    baseline_p50,
    baseline_p_ge1,
    fit_player_model,
)
from nflengine.models.player_schema import Target

TEAM_TARGETS: tuple[Target, ...] = (
    Target(
        "pass_yds",
        "TEAM",
        "pass_yds",
        "amount",
        "team passing yards",
        "yards",
        main=True,
        phase="p08",
    ),  # fmt: skip
    Target("rush_yds", "TEAM", "rush_yds", "amount", "team rushing yards", "yards", phase="p08"),
    Target(
        "sacks_made",
        "TEAM",
        "sacks_made",
        "count",
        "sacks made",
        "count",
        event_probs=True,
        phase="p08",
    ),  # fmt: skip
    Target(
        "sacks_taken",
        "TEAM",
        "sacks_taken",
        "count",
        "sacks taken",
        "count",
        event_probs=True,
        phase="p08",
    ),  # fmt: skip
    Target(
        "takeaways",
        "TEAM",
        "takeaways",
        "count",
        "takeaways",
        "count",
        event_probs=True,
        phase="p08",
    ),  # fmt: skip
)
TEAM_TARGET_BY_NAME = {t.name: t for t in TEAM_TARGETS}

# LightGBM settings per target (the rest = `PlayerModelConfig` defaults: learning_rate 0.05,
# feature_fraction 0.8, bagging 0.8, lambda_l2 1.0). Each is the winner of `team_runs.TUNE_GRID`
# (16 configurations) scored on the 2017-2018 walk-forward only, never the reported 2019-2025
# weeks: an amount by MAE, a count by Poisson deviance (W&B group track1-team, job `tune`, one
# run per target). The grid was flat (the best five within ~0.3-0.5% of each other), see the
# model card. Settings `team_model.per_target.<key>` override these.
TEAM_PARAMS: dict[str, dict[str, Any]] = {
    "pass_yds": {"num_leaves": 4, "min_data_in_leaf": 100, "n_estimators": 200},
    "rush_yds": {"num_leaves": 4, "min_data_in_leaf": 200, "n_estimators": 100},
    "sacks_made": {"num_leaves": 8, "min_data_in_leaf": 200, "n_estimators": 60},
    "sacks_taken": {"num_leaves": 8, "min_data_in_leaf": 200, "n_estimators": 60},
    "takeaways": {"num_leaves": 8, "min_data_in_leaf": 200, "n_estimators": 60},
}


def live_team_targets(keys: Sequence[str] | None = None) -> tuple[Target, ...]:
    """The targets the weekly run refits and projects: settings `team_model.live_targets` (a
    list of keys such as `pass_yds-team`). Empty until the P08 ship decision names them: a team
    target joins the weekly run only after it beat its baseline in the walk-forward backtest."""
    if keys is None:
        from nflengine.settings import get_config

        keys = (getattr(get_config(), "team_model", None) or {}).get("live_targets") or ()
    unknown = [k for k in keys if k not in {t.key for t in TEAM_TARGETS}]
    if unknown:
        raise ValueError(f"unknown live team target keys {unknown}; see TEAM_TARGETS")
    return tuple(t for t in TEAM_TARGETS if t.key in set(keys))


def get_team_target(name: str) -> list[Target]:
    """Targets matching a name (`pass_yds`), a key (`pass_yds-team`) or `all`."""
    if name == "all":
        return list(TEAM_TARGETS)
    found = [t for t in TEAM_TARGETS if name in (t.name, t.key)]
    if not found:
        raise ValueError(f"unknown team target {name!r}; see TEAM_TARGETS")
    return found


def team_config(
    target: Target,
    settings: Mapping[str, Any] | None = None,
    weights: SampleWeights | None = None,
    **overrides: Any,
) -> PlayerModelConfig:
    """The target's LightGBM config: `PlayerModelConfig` defaults, `TEAM_PARAMS`, then the
    settings block `team_model` (and its `per_target.<key>`), then `overrides`."""
    s = dict(settings or {})
    s.pop("live_targets", None)  # which targets run weekly: `live_team_targets`
    per = dict((s.pop("per_target", None) or {}).get(target.key, {}))
    values = {**TEAM_PARAMS.get(target.name, {}), **s, **per}
    if weights is not None:
        overrides["weights"] = weights
    return PlayerModelConfig.from_config(values, **overrides)


# ---- distributions and count metrics -------------------------------------------------------


def poisson_deviance(y: np.ndarray, mu: np.ndarray) -> float:
    """Mean Poisson deviance `2 * (y log(y / mu) - (y - mu))` over rows with a finite mu."""
    y = np.asarray(y, dtype=float)
    mu = np.clip(np.asarray(mu, dtype=float), 1e-9, None)
    ok = np.isfinite(y) & np.isfinite(mu)
    y, mu = y[ok], mu[ok]
    if y.size == 0:
        return float("nan")
    term = np.where(y > 0, y * np.log(np.where(y > 0, y, 1.0) / mu), 0.0)
    return float(np.mean(2 * (term - (y - mu))))


def count_prob_table(prob: np.ndarray, outcome: np.ndarray, bins: int = 10) -> pl.DataFrame:
    """Reliability bins of a probability: (bin, mean predicted, observed share, n)."""
    p, o = np.asarray(prob, dtype=float), np.asarray(outcome, dtype=float)
    ok = np.isfinite(p) & np.isfinite(o)
    p, o = p[ok], o[ok]
    idx = np.clip((p * bins).astype(int), 0, bins - 1)
    rows = [
        {
            "bin": b,
            "mean_pred": float(p[idx == b].mean()),
            "observed": float(o[idx == b].mean()),
            "n": int((idx == b).sum()),
        }
        for b in range(bins)
        if (idx == b).any()
    ]
    schema = {"bin": pl.Int64, "mean_pred": pl.Float64, "observed": pl.Float64, "n": pl.Int64}
    return pl.DataFrame(rows, schema=schema)


# ---- harness model ------------------------------------------------------------------------------

ID_COLS = ["season", "week", "game_id", "team"]


class TeamWeekModel:
    """`walk_forward` model for one target: fit on `train`, predict `test`. Keeps the last
    fit in `.last`.

    A count's P(>= 1) (`p_ge1`) is the player model's: the negative binomial's `1 - cdf(0)`
    recalibrated (Platt) on the model's own earlier walk-forward values (`_p_raw` in the
    history; the raw value until there are enough), and the baseline's `baseline_p_ge1` goes
    through the same dispersion and calibrator, so model and baseline differ only in the
    mean."""

    def __init__(self, target: Target, features: Sequence[str], config: PlayerModelConfig):
        self.target = target
        self.features = list(features)
        self.config = config
        self.last: FittedPlayerModel | None = None

    def __call__(
        self, train: pl.DataFrame, weights: np.ndarray, test: pl.DataFrame, history: pl.DataFrame
    ) -> pl.DataFrame:
        fit = fit_player_model(
            train, weights, self.target, self.features, self.config, history=history
        )
        self.last = fit
        out = test.select(*ID_COLS, "baseline", "y").hstack(fit.predict(test))
        out = out.with_columns(baseline_p50(out["baseline"], self.target, fit.dispersion))
        if self.target.kind == "count":
            out = out.with_columns(
                baseline_p_ge1(out["baseline"], self.target, fit.dispersion, fit.calibrator)
            ).drop("p_ge2")
        else:
            out = out.drop("p_ge1", "p_ge2")
        return out.with_columns(
            pl.lit(fit.scale).alias("scale"),
            pl.lit(fit.n_train).cast(pl.Int32).alias("n_train"),
            pl.lit(fit.shift if self.target.kind == "amount" else fit.dispersion).alias(
                "range_param"
            ),
            pl.lit(fit.tail if self.target.kind == "count" else None, dtype=pl.Float64).alias(
                "range_tail"
            ),
        )
