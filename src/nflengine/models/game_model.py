"""Game model v0 (documentation/04 -> B. Game model; plan P03).

Three heads, all fitted on the game feature table (`features/game.py`) with current-season
sample weights (D28):
- **margin:** ridge regression of the home margin (no intercept: every feature is a
  home-minus-away difference or the home-field flag, so a neutral-site game between equal
  teams predicts 0);
- **total:** ridge regression of combined points (with intercept);
- **win (optional):** logistic regression on the same features as the margin (ties dropped).

Win probability is either `Phi(expected_margin / sigma)` (`win_method = 'margin'`; sigma =
RMSE of earlier walk-forward margin residuals, never in-sample) or the logistic head
(`'logistic'`). An optional calibration layer (Platt or isotonic) is fitted on earlier
walk-forward probabilities. Predicted scores split the total by the margin, so
`pred_home_points - pred_away_points == expected_margin` exactly.

Two variants share the code: **model_only** (football features) and **market** (the same
plus the spread and total). Rows missing a feature (for example no line yet) get null
predictions; the production path falls back to model-only for them.

Baselines are computed on exactly the same rows in the same pass: home team always
(walk-forward home win rate, 0.5 at neutral sites), Elo (as-of), market from the spread
(`Phi(spread / sigma_m)`, sigma_m = RMSE of margin around the spread in the training
rows), plus the vig-free moneyline for reference.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

import numpy as np
import polars as pl
from scipy.stats import norm
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

from nflengine.models.backtest import SampleWeights

VARIANTS = ("model_only", "market")
WIN_METHODS = ("margin", "logistic")
CALIBRATIONS = ("none", "platt", "isotonic")

MARGIN_FEATURES: dict[str, tuple[str, ...]] = {
    # Chosen on the 2013-2017 tuning window (D48). The pass / rush matchups add nothing
    # beyond net_diff there; they stay in the feature table for P08.
    "model_only": ("net_diff", "elo_diff", "hfa", "qb_adj_diff"),
}
MARGIN_FEATURES["market"] = (*MARGIN_FEATURES["model_only"], "spread_line")
TOTAL_FEATURES: dict[str, tuple[str, ...]] = {
    "model_only": ("off_sum", "def_sum", "qb_adj_sum", "league_ppg", "pts_base_total", "roof_dome"),
}
TOTAL_FEATURES["market"] = (*TOTAL_FEATURES["model_only"], "total_line")

# Carried from the feature table into every prediction row.
CARRY = (
    "season",
    "week",
    "game_id",
    "game_type",
    "kickoff_utc",
    "home_team",
    "away_team",
    "neutral_site",
    "home_score",
    "away_score",
    "margin",
    "total",
    "home_win",
    "elo_prob",
    "elo_margin",
    "market_ml_prob",
    "spread_line",
    "total_line",
    "market_source",
    "pts_base_home",
    "pts_base_away",
    "pts_base_total",
    "mkt_home_points",
    "mkt_away_points",
)


@dataclass(frozen=True)
class GameModelConfig:
    variant: str = "model_only"
    win_method: str = "margin"
    margin_alpha: float = 1.0  # ridge strength on scaled features
    total_alpha: float = 1.0
    logistic_c: float = 1.0
    calibration: str = "none"
    sigma_default: float = 13.5  # until enough walk-forward residuals exist
    sigma_min_games: int = 400
    calib_min_games: int = 500
    margin_features: tuple[str, ...] | None = None  # None -> MARGIN_FEATURES[variant]
    total_features: tuple[str, ...] | None = None
    weights: SampleWeights = field(default_factory=SampleWeights)

    def __post_init__(self) -> None:
        if self.variant not in VARIANTS:
            raise ValueError(f"variant must be one of {VARIANTS}")
        if self.win_method not in WIN_METHODS:
            raise ValueError(f"win_method must be one of {WIN_METHODS}")
        if self.calibration not in CALIBRATIONS:
            raise ValueError(f"calibration must be one of {CALIBRATIONS}")
        for name in ("margin_alpha", "total_alpha", "logistic_c", "sigma_default"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")

    @property
    def mfeats(self) -> tuple[str, ...]:
        return tuple(self.margin_features or MARGIN_FEATURES[self.variant])

    @property
    def tfeats(self) -> tuple[str, ...]:
        return tuple(self.total_features or TOTAL_FEATURES[self.variant])

    @classmethod
    def from_config(
        cls,
        cfg: Mapping[str, Any] | None = None,
        weights: SampleWeights | None = None,
        **overrides: Any,
    ) -> GameModelConfig:
        """From `settings.yaml` -> game_model (any non-None keyword overrides win)."""
        values = dict(cfg or {})
        known = {f.name for f in fields(cls)} - {"weights"}
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"unknown game_model settings: {sorted(unknown)}")
        values.update({k: v for k, v in overrides.items() if v is not None})
        for k in ("margin_features", "total_features"):
            if values.get(k) is not None:
                values[k] = tuple(values[k])
        return cls(**values, weights=weights or SampleWeights())

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["margin_features"] = list(self.mfeats)
        d["total_features"] = list(self.tfeats)
        return d

    def with_variant(self, variant: str) -> GameModelConfig:
        """Same settings for the other variant (feature lists follow the variant unless
        they were set explicitly for model-only, in which case the market adds its lines)."""
        mf, tf = self.margin_features, self.total_features
        if variant == "market" and self.variant == "model_only":
            mf = None if mf is None else (*mf, "spread_line")
            tf = None if tf is None else (*tf, "total_line")
        return replace(self, variant=variant, margin_features=mf, total_features=tf)


def feature_hash(features: Sequence[str]) -> str:
    """Short, order-independent hash of a feature list (logged with every run)."""
    return hashlib.sha256(",".join(sorted(features)).encode()).hexdigest()[:10]


def _x(df: pl.DataFrame, cols: Sequence[str]) -> np.ndarray:
    return df.select([pl.col(c).cast(pl.Float64) for c in cols]).to_numpy()


def _complete(df: pl.DataFrame, cols: Sequence[str]) -> np.ndarray:
    return (
        df.select(pl.all_horizontal(pl.col(c).is_not_null() for c in cols)).to_series().to_numpy()
    )


def _ridge(alpha: float, intercept: bool) -> Pipeline:
    # Scale only (no centering) when there is no intercept, so the home-field flag keeps
    # its meaning: centering it would erase home advantage for every non-neutral game.
    return make_pipeline(
        StandardScaler(with_mean=intercept), Ridge(alpha=alpha, fit_intercept=intercept)
    )


@dataclass
class Calibrator:
    method: str
    model: Any

    def __call__(self, p: np.ndarray) -> np.ndarray:
        if self.method == "platt":
            z = np.log(np.clip(p, 1e-6, 1 - 1e-6) / np.clip(1 - p, 1e-6, 1))
            return self.model.predict_proba(z[:, None])[:, 1]
        return self.model.predict(p)


def fit_calibrator(method: str, p: np.ndarray, y: np.ndarray) -> Calibrator | None:
    """Platt (logistic on the logit) or isotonic, on decisive games only."""
    keep = ~np.isnan(p) & ~np.isnan(y) & (y != 0.5)
    p, y = p[keep], y[keep]
    if method == "none" or len(np.unique(y)) < 2:
        return None
    if method == "platt":
        z = np.log(np.clip(p, 1e-6, 1 - 1e-6) / np.clip(1 - p, 1e-6, 1))
        return Calibrator("platt", LogisticRegression(C=1e6).fit(z[:, None], y))
    iso = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip").fit(p, y)
    return Calibrator("isotonic", iso)


@dataclass
class FittedGameModel:
    """One week's fitted heads plus everything needed to predict that week."""

    config: GameModelConfig
    margin_model: Pipeline
    total_model: Pipeline
    logit_model: Pipeline | None
    sigma: float
    calibrator: Calibrator | None
    sigma_market: float
    home_rate: float
    n_train: int
    trained_through: tuple[int, int] | None = None  # last (season, week) in training

    def predict(self, test: pl.DataFrame) -> pl.DataFrame:
        cfg = self.config
        n = test.height
        mf, tf = cfg.mfeats, cfg.tfeats
        ok_m, ok_t = _complete(test, mf), _complete(test, tf)
        margin = np.full(n, np.nan)
        total = np.full(n, np.nan)
        if ok_m.any():
            margin[ok_m] = self.margin_model.predict(_x(test.filter(pl.Series(ok_m)), mf))
        if ok_t.any():
            total[ok_t] = self.total_model.predict(_x(test.filter(pl.Series(ok_t)), tf))
        if cfg.win_method == "margin":
            p_raw = norm.cdf(margin / self.sigma)
        else:
            p_raw = np.full(n, np.nan)
            if ok_m.any():
                xs = _x(test.filter(pl.Series(ok_m)), mf)
                p_raw[ok_m] = self.logit_model.predict_proba(xs)[:, 1]
        p = p_raw.copy()
        if self.calibrator is not None:
            ok = ~np.isnan(p_raw)
            p[ok] = self.calibrator(p_raw[ok])
        spread = test["spread_line"].cast(pl.Float64).to_numpy()
        neutral = test["neutral_site"].fill_null(False).to_numpy()
        carry = [c for c in CARRY if c in test.columns]
        return test.select(carry).with_columns(
            pl.lit(cfg.variant).alias("variant"),
            pl.Series("expected_margin", margin).fill_nan(None),
            pl.Series("pred_total", total).fill_nan(None),
            pl.Series("pred_home_points", (total + margin) / 2).fill_nan(None),
            pl.Series("pred_away_points", (total - margin) / 2).fill_nan(None),
            pl.Series("home_win_prob_raw", p_raw).fill_nan(None),
            pl.Series("home_win_prob", p).fill_nan(None),
            pl.lit(self.sigma).alias("sigma"),
            pl.Series("home_base_prob", np.where(neutral, 0.5, self.home_rate)),
            pl.Series("market_prob", norm.cdf(spread / self.sigma_market)).fill_nan(None),
            pl.lit(self.n_train).alias("n_train"),
        )


def _sigma(history: pl.DataFrame, cfg: GameModelConfig) -> float:
    if history.is_empty() or "expected_margin" not in history.columns:
        return cfg.sigma_default
    h = history.filter(pl.col("margin").is_not_null() & pl.col("expected_margin").is_not_null())
    if h.height < cfg.sigma_min_games:
        return cfg.sigma_default
    r = (h["margin"] - h["expected_margin"]).to_numpy()
    return float(np.sqrt(np.mean(r**2)))


def fit_game_model(
    train: pl.DataFrame,
    weights: np.ndarray,
    config: GameModelConfig,
    history: pl.DataFrame | None = None,
) -> FittedGameModel:
    """Fit all heads on `train` (rows with targets); sigma and calibration come from
    `history` (earlier walk-forward predictions of the same configuration)."""
    history = history if history is not None else pl.DataFrame()
    mf, tf = config.mfeats, config.tfeats
    ok_m = _complete(train, [*mf, "margin"])
    ok_t = _complete(train, [*tf, "total"])
    tm, tt = train.filter(pl.Series(ok_m)), train.filter(pl.Series(ok_t))
    margin_model = _ridge(config.margin_alpha, intercept=False).fit(
        _x(tm, mf), tm["margin"].to_numpy(), ridge__sample_weight=weights[ok_m]
    )
    total_model = _ridge(config.total_alpha, intercept=True).fit(
        _x(tt, tf), tt["total"].to_numpy(), ridge__sample_weight=weights[ok_t]
    )
    logit = None
    if config.win_method == "logistic":
        dec = tm["home_win"].to_numpy() != 0.5
        logit = make_pipeline(
            StandardScaler(with_mean=False),
            LogisticRegression(C=config.logistic_c, fit_intercept=False),
        ).fit(
            _x(tm, mf)[dec],
            tm["home_win"].to_numpy()[dec],
            logisticregression__sample_weight=weights[ok_m][dec],
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
    has_spread = train.filter(pl.col("spread_line").is_not_null() & pl.col("margin").is_not_null())
    sigma_market = (
        float(np.sqrt(np.mean((has_spread["margin"] - has_spread["spread_line"]).to_numpy() ** 2)))
        if has_spread.height
        else config.sigma_default
    )
    home = train.filter(~pl.col("neutral_site").fill_null(False) & pl.col("home_win").is_not_null())
    home_rate = float(home["home_win"].mean()) if home.height else 0.5
    last = train.select("season", "week").sort("season", "week").row(-1) if train.height else None
    return FittedGameModel(
        config=config,
        margin_model=margin_model,
        total_model=total_model,
        logit_model=logit,
        sigma=_sigma(history, config),
        calibrator=calibrator,
        sigma_market=sigma_market,
        home_rate=home_rate,
        n_train=int(ok_m.sum()),
        trained_through=last,
    )


class GameWeekModel:
    """`backtest.walk_forward` model: fit on the week's training rows, predict its games.
    Keeps the latest fit in `last` (the production path saves it)."""

    def __init__(self, config: GameModelConfig):
        self.config = config
        self.last: FittedGameModel | None = None

    def __call__(
        self, train: pl.DataFrame, weights: np.ndarray, test: pl.DataFrame, history: pl.DataFrame
    ) -> pl.DataFrame:
        self.last = fit_game_model(train, weights, self.config, history)
        return self.last.predict(test)


def coefficients(model: FittedGameModel) -> pl.DataFrame:
    """Margin and total coefficients in original feature units (for the model card)."""
    rows = []
    for head, pipe, feats in (
        ("margin", model.margin_model, model.config.mfeats),
        ("total", model.total_model, model.config.tfeats),
    ):
        scaler, reg = pipe[0], pipe[-1]
        coef = reg.coef_ / scaler.scale_
        rows += [(head, f, float(c)) for f, c in zip(feats, coef, strict=True)]
        if reg.fit_intercept:
            mean = scaler.mean_ if scaler.with_mean else np.zeros_like(coef)
            rows.append((head, "intercept", float(reg.intercept_ - np.sum(coef * mean))))
    return pl.DataFrame(rows, schema=["head", "feature", "coef"], orient="row")
