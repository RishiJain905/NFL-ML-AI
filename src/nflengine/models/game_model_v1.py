"""Game model v1 (plan P08; documentation/04 -> B. Algorithms, step 2).

LightGBM on the full feature set, with **monotonic constraints** where the direction is
known (a better rating, Elo, QB, injury edge or spread can only help the home margin).
Same outputs as v0 (`game_model.FittedGameModel.predict`), so the walk-forward harness,
the report card and the digest read it unchanged:

- **margin** and **total** heads: LightGBM regressions. With `base = "ridge"` (default)
  each head **boosts from v0's ridge prediction** (`init_score`): the trees only learn
  corrections from the features v0 leaves out (matchups, rest, travel, injuries,
  weather, the trailing home edge). With `base = "none"` the trees start from zero.
- win probability `Phi(margin / sigma)`, sigma = RMSE of this model's own earlier
  walk-forward margin misses (`history`), as in v0; optional Platt / isotonic
  calibration on the same history (never in-sample).
- scores split the total by the margin, so score, margin and probability agree.
- baselines (home rate, Elo, market from the spread) come from the v0 fit on the same
  rows, so v0 and v1 are always scored on the same baseline numbers; and **v0 itself**
  rides along (`v0_prob`, `v0_margin`, `v0_total`: its ridge on the same training rows,
  its sigma from its own earlier misses), exactly what a v0 walk-forward gives, so every
  v1 backtest logs v0 next to it on the same games.

Data is small (~285 games a season), so the trees are shallow, few and heavily
regularized; doc 04 step 3 ("keep the simplest model that's statistically as good") is
the promotion rule (P08 ✋: beat v0 on pooled walk-forward Brier and calibration).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl
from scipy.stats import norm

from nflengine.models.backtest import SampleWeights
from nflengine.models.game_model import (
    VARIANTS,
    Calibrator,
    FittedGameModel,
    GameModelConfig,
    fit_calibrator,
    fit_game_model,
)

BASES = ("ridge", "none")
CALIBRATIONS = ("none", "platt", "isotonic")

MARGIN_FEATURES_V1: tuple[str, ...] = (
    "net_diff",
    "elo_diff",
    "hfa",
    "qb_adj_diff",
    "pass_matchup",
    "rush_matchup",
    "net_sr_diff",
    "rest_diff",
    "bye_diff",
    "short_diff",
    "div_game",
    "travel_diff",
    "tz_diff",
    "prior_weight",
    "net_x_prior",
    "inj_off_edge",
    "inj_def_edge",
    "wind_15",
    "cold_32",
    "home_edge_trailing",
)
TOTAL_FEATURES_V1: tuple[str, ...] = (
    "off_sum",
    "def_sum",
    "qb_adj_sum",
    "league_ppg",
    "pts_base_total",
    "roof_dome",
    "wind_15",
    "cold_32",
    "inj_off_sum",
    "inj_def_sum",
    "prior_weight",
)
# +1: the head can only rise with the feature; -1: only fall (LightGBM monotone constraints)
MONOTONE: dict[str, int] = {
    "net_diff": 1,
    "elo_diff": 1,
    "hfa": 1,
    "qb_adj_diff": 1,
    "pass_matchup": 1,
    "rush_matchup": 1,
    "net_sr_diff": 1,
    "inj_off_edge": 1,
    "inj_def_edge": 1,
    "home_edge_trailing": 1,
    "spread_line": 1,
    # totals
    "off_sum": 1,
    "def_sum": 1,  # def_* is EPA allowed: weaker defenses, more points
    "qb_adj_sum": 1,
    "league_ppg": 1,
    "pts_base_total": 1,
    "wind_15": -1,
    "inj_off_sum": -1,
    "total_line": 1,
}
MARKET_EXTRA = {"margin": "spread_line", "total": "total_line"}


@dataclass(frozen=True)
class GameModelV1Config:
    variant: str = "model_only"
    base: str = "ridge"  # boost from v0's ridge prediction | from zero
    num_leaves: int = 4
    learning_rate: float = 0.02
    n_estimators: int = 150
    min_data_in_leaf: int = 150
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    bagging_freq: int = 1
    lambda_l2: float = 10.0
    seed: int = 7
    num_threads: int = 4
    monotone: bool = True
    calibration: str = "none"
    sigma_default: float = 13.5
    sigma_min_games: int = 400
    calib_min_games: int = 500
    margin_features: tuple[str, ...] | None = None  # None -> MARGIN_FEATURES_V1 (+ spread)
    total_features: tuple[str, ...] | None = None
    weights: SampleWeights = field(default_factory=SampleWeights)
    v0: GameModelConfig | None = None  # the ridge base (None -> v0 defaults of the variant)

    def __post_init__(self) -> None:
        if self.variant not in VARIANTS:
            raise ValueError(f"variant must be one of {VARIANTS}")
        if self.base not in BASES:
            raise ValueError(f"base must be one of {BASES}")
        if self.calibration not in CALIBRATIONS:
            raise ValueError(f"calibration must be one of {CALIBRATIONS}")
        if self.n_estimators < 0 or self.learning_rate <= 0:
            raise ValueError("n_estimators must be >= 0 and learning_rate > 0")

    @property
    def mfeats(self) -> tuple[str, ...]:
        f = tuple(self.margin_features or MARGIN_FEATURES_V1)
        if self.variant == "market" and "spread_line" not in f:
            f = (*f, "spread_line")
        return f

    @property
    def tfeats(self) -> tuple[str, ...]:
        f = tuple(self.total_features or TOTAL_FEATURES_V1)
        if self.variant == "market" and "total_line" not in f:
            f = (*f, "total_line")
        return f

    @property
    def base_config(self) -> GameModelConfig:
        """The v0 ridge (base predictions + baselines), same variant and weights."""
        if self.v0 is not None:
            return replace(self.v0, variant=self.variant, weights=self.weights)
        return GameModelConfig(variant=self.variant, weights=self.weights)

    @classmethod
    def from_config(
        cls,
        cfg: Mapping[str, Any] | None = None,
        weights: SampleWeights | None = None,
        v0: GameModelConfig | None = None,
        **overrides: Any,
    ) -> GameModelV1Config:
        """From `settings.yaml` -> game_model.v1 (non-None keyword overrides win)."""
        values = dict(cfg or {})
        known = {f.name for f in fields(cls)} - {"weights", "v0"}
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"unknown game_model.v1 settings: {sorted(unknown)}")
        values.update({k: v for k, v in overrides.items() if v is not None})
        for k in ("margin_features", "total_features"):
            if values.get(k) is not None:
                values[k] = tuple(values[k])
        return cls(**values, weights=weights or SampleWeights(), v0=v0)

    def lgb_params(self, features: Sequence[str]) -> dict[str, Any]:
        p: dict[str, Any] = {
            "objective": "regression",
            "num_leaves": self.num_leaves,
            "learning_rate": self.learning_rate,
            "min_data_in_leaf": self.min_data_in_leaf,
            "feature_fraction": self.feature_fraction,
            "bagging_fraction": self.bagging_fraction,
            "bagging_freq": self.bagging_freq,
            "lambda_l2": self.lambda_l2,
            "seed": self.seed,
            "deterministic": True,
            "num_threads": self.num_threads,
            "verbosity": -1,
        }
        if self.monotone:
            p["monotone_constraints"] = [MONOTONE.get(f, 0) for f in features]
            p["monotone_constraints_method"] = "advanced"
        return p

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("v0", None)
        d["weights"] = self.weights.as_dict()
        d["margin_features"] = list(self.mfeats)
        d["total_features"] = list(self.tfeats)
        d["base_config"] = self.base_config.as_dict()
        d["base_config"]["weights"] = self.weights.as_dict()
        return d

    def with_variant(self, variant: str) -> GameModelV1Config:
        return replace(self, variant=variant)


def feature_hash(config: GameModelV1Config) -> str:
    feats = sorted({*config.mfeats, *config.tfeats, *config.base_config.mfeats})
    return hashlib.sha256(("v1:" + ",".join(feats)).encode()).hexdigest()[:10]


def _x(df: pl.DataFrame, cols: Sequence[str]) -> np.ndarray:
    return df.select([pl.col(c).cast(pl.Float64) for c in cols]).to_numpy()


@dataclass
class FittedGameModelV1:
    config: GameModelV1Config
    base: FittedGameModel  # v0 ridge: base predictions + every baseline column
    margin_booster: lgb.Booster | None
    total_booster: lgb.Booster | None
    sigma: float
    calibrator: Calibrator | None
    n_train: int
    trained_through: tuple[int, int] | None = None

    def _heads(self, test: pl.DataFrame, base_pred: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        cfg = self.config
        bm = base_pred["expected_margin"].cast(pl.Float64).fill_null(np.nan).to_numpy()
        bt = base_pred["pred_total"].cast(pl.Float64).fill_null(np.nan).to_numpy()
        if cfg.base == "none":
            bm = np.where(np.isnan(bm), np.nan, 0.0)
            bt = np.where(np.isnan(bt), np.nan, 0.0)
        margin = bm.copy()
        total = bt.copy()
        if self.margin_booster is not None and test.height:
            margin = bm + self.margin_booster.predict(_x(test, cfg.mfeats))
        if self.total_booster is not None and test.height:
            total = bt + self.total_booster.predict(_x(test, cfg.tfeats))
        return margin, total

    def predict(self, test: pl.DataFrame) -> pl.DataFrame:
        base_pred = self.base.predict(test)
        margin, total = self._heads(test, base_pred)
        p_raw = norm.cdf(margin / self.sigma)
        p = p_raw.copy()
        if self.calibrator is not None:
            ok = ~np.isnan(p_raw)
            if ok.any():
                p[ok] = self.calibrator(p_raw[ok])
        return base_pred.with_columns(
            # v0 on the same rows (its ridge, its own walk-forward sigma): the overlay
            pl.col("home_win_prob").alias("v0_prob"),
            pl.col("home_win_prob_raw").alias("v0_prob_raw"),
            pl.col("expected_margin").alias("v0_margin"),
            pl.col("pred_total").alias("v0_total"),
        ).with_columns(
            pl.Series("expected_margin", margin).fill_nan(None),
            pl.Series("pred_total", total).fill_nan(None),
            pl.Series("pred_home_points", (total + margin) / 2).fill_nan(None),
            pl.Series("pred_away_points", (total - margin) / 2).fill_nan(None),
            pl.Series("home_win_prob_raw", p_raw).fill_nan(None),
            pl.Series("home_win_prob", p).fill_nan(None),
            pl.lit(self.sigma).alias("sigma"),
            pl.lit(self.n_train).alias("n_train"),
        )

    def importance(self) -> pl.DataFrame:
        rows = []
        for head, b, feats in (
            ("margin", self.margin_booster, self.config.mfeats),
            ("total", self.total_booster, self.config.tfeats),
        ):
            if b is None:
                continue
            gain = b.feature_importance("gain")
            rows += [(head, f, float(g)) for f, g in zip(feats, gain, strict=True)]
        return pl.DataFrame(rows, schema=["head", "feature", "gain"], orient="row")


def _sigma(history: pl.DataFrame, cfg: GameModelV1Config) -> float:
    if history.is_empty() or "expected_margin" not in history.columns:
        return cfg.sigma_default
    h = history.filter(pl.col("margin").is_not_null() & pl.col("expected_margin").is_not_null())
    if h.height < cfg.sigma_min_games:
        return cfg.sigma_default
    r = (h["margin"] - h["expected_margin"]).to_numpy()
    return float(np.sqrt(np.mean(r**2)))


def fit_game_model_v1(
    train: pl.DataFrame,
    weights: np.ndarray,
    config: GameModelV1Config,
    history: pl.DataFrame | None = None,
    evals: dict[str, dict] | None = None,
    valid: pl.DataFrame | None = None,
) -> FittedGameModelV1:
    """Fit the v0 ridge base, then the boosted margin and total heads on `train`; sigma
    and calibration come from `history` (this model's earlier walk-forward predictions).
    `valid` + `evals`: also record per-round training / validation loss (W&B curves)."""
    history = history if history is not None else pl.DataFrame()
    # v0's own walk-forward history (its sigma / calibration), carried in the v0_* columns
    h0 = pl.DataFrame()
    if history.height and "v0_margin" in history.columns:
        h0 = history.select(
            "margin",
            "home_win",
            pl.col("v0_margin").alias("expected_margin"),
            pl.col("v0_prob_raw").alias("home_win_prob_raw"),
        )
    base = fit_game_model(train, weights, config.base_config, history=h0)
    base_pred = base.predict(train)
    boosters: dict[str, lgb.Booster | None] = {"margin": None, "total": None}
    for head, label, feats in (
        ("margin", "margin", config.mfeats),
        ("total", "total", config.tfeats),
    ):
        init = base_pred["expected_margin" if head == "margin" else "pred_total"]
        init = init.cast(pl.Float64).fill_null(np.nan).to_numpy()
        if config.base == "none":
            init = np.where(np.isnan(init), np.nan, 0.0)
        y = train[label].cast(pl.Float64).fill_null(np.nan).to_numpy()
        ok = ~np.isnan(y) & ~np.isnan(init)
        if ok.sum() < config.min_data_in_leaf * 2 or config.n_estimators == 0:
            continue
        x = _x(train, feats)[ok]
        # from zero: LightGBM boosts from the label average (an init score would turn that
        # off); its prediction then needs no base added (see `_heads`)
        ds = lgb.Dataset(
            x,
            y[ok],
            weight=weights[ok],
            init_score=init[ok] if config.base == "ridge" else None,
            feature_name=list(feats),
            free_raw_data=False,
        )
        cb, vsets, names = [], None, None
        if evals is not None and valid is not None and valid.height:
            vp = base.predict(valid)
            vinit = vp["expected_margin" if head == "margin" else "pred_total"]
            vinit = vinit.cast(pl.Float64).fill_null(np.nan).to_numpy()
            if config.base == "none":
                vinit = np.where(np.isnan(vinit), np.nan, 0.0)
            vy = valid[label].cast(pl.Float64).fill_null(np.nan).to_numpy()
            vok = ~np.isnan(vy) & ~np.isnan(vinit)
            vds = lgb.Dataset(
                _x(valid, feats)[vok],
                vy[vok],
                init_score=vinit[vok] if config.base == "ridge" else None,
                reference=ds,
            )
            vsets, names = [ds, vds], ["train", "valid"]
            evals[head] = {}
            cb.append(lgb.record_evaluation(evals[head]))
        boosters[head] = lgb.train(
            config.lgb_params(feats),
            ds,
            num_boost_round=config.n_estimators,
            valid_sets=vsets,
            valid_names=names,
            callbacks=cb,
        )
    calibrator = None
    if config.calibration != "none" and history.height:
        h = history.filter(
            pl.col("home_win").is_not_null() & pl.col("home_win_prob_raw").is_not_null()
        )
        if h.height >= config.calib_min_games:
            calibrator = fit_calibrator(
                config.calibration, h["home_win_prob_raw"].to_numpy(), h["home_win"].to_numpy()
            )
    return FittedGameModelV1(
        config=config,
        base=base,
        margin_booster=boosters["margin"],
        total_booster=boosters["total"],
        sigma=_sigma(history, config),
        calibrator=calibrator,
        n_train=base.n_train,
        trained_through=base.trained_through,
    )


class GameWeekModelV1:
    """`backtest.walk_forward` model for v1 (like `game_model.GameWeekModel`)."""

    def __init__(self, config: GameModelV1Config):
        self.config = config
        self.last: FittedGameModelV1 | None = None

    def __call__(
        self, train: pl.DataFrame, weights: np.ndarray, test: pl.DataFrame, history: pl.DataFrame
    ) -> pl.DataFrame:
        self.last = fit_game_model_v1(train, weights, self.config, history)
        return self.last.predict(test)
