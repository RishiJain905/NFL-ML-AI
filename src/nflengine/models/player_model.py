"""Player model v1 (plan P06; documentation/04 -> C. Player model, 11).

One model per target x position group (`player_schema.TARGETS`):
- **Amounts** (yards, EPA per dropback): three LightGBM quantile models (alpha 0.1 / 0.5 /
  0.9). P50 is the projection, P10-P90 the 80% range. The raw range is then conformalized
  on the model's own earlier walk-forward misses (`history`): with
  `E = max(p10 - y, y - p90)` over the last `conformal_seasons` seasons, both ends move by
  the 80th percentile of E, so the range holds ~80% of outcomes (it can narrow too).
- **Counts** (carries, targets, receptions, pressures, tackles): a LightGBM Poisson model
  for the expected value `mean`; the outcome distribution is a negative binomial with that
  mean and a dispersion fitted on earlier walk-forward residuals (method of moments:
  `Var(y) = mu + mu^2 / r`). P50 is its median; the 80% range is its `tail` and
  `1 - tail` quantiles (whole numbers), with `tail` (0.10-0.25) picked on the same history
  so the range holds ~80% of outcomes: whole-number quantiles at exactly 0.1 / 0.9 cover
  more than 80% (the smoke run: 88% for tackles).
- **Event probabilities** (P08, counts with `event_probs`: pass TDs, interceptions, sacks):
  the negative binomial's `1 - NB.cdf(0)` at the model's mean and the fitted dispersion,
  recalibrated like a probability target (Platt on its own earlier walk-forward values) =
  `p_ge1`; `p_ge2 = 1 - NB.cdf(1)` (uncalibrated, capped at `p_ge1`). The baseline's `p_ge1` is
  the same function at the baseline mean, so model and baseline differ only in the mean.
- **Yes / no targets** (P08, kind `prob`: a touchdown, an interception, a pass defended): a
  LightGBM binary model, then a **calibration layer** (Platt scaling by default, isotonic as
  an option) fitted on the model's own earlier walk-forward raw probabilities (`history`,
  the last `calibration_seasons` seasons), never in-sample. While the history is too thin
  (`MIN_CAL_ROWS` rows / `MIN_CAL_EVENTS` events: the burn-in) the raw probability is used.
  `mean` = `p_ge1` = the calibrated P(event); `p10` / `p50` / `p90` stay null.
- **Drivers:** LightGBM's built-in SHAP values (`pred_contrib`) of the P50 / Poisson / binary
  model; the top 3 by size, in target units (Poisson contributions are on the log scale and
  are multiplied by the mean; binary ones are on the log-odds scale and are multiplied by
  p (1 - p), both first-order conversions).

Sample weights: current season / last season / older (D28, `training.sample_weights`).
Hyperparameters come from `settings.yaml` `player_model` (tuned on 2017-2018 walk-forward,
never on the reported 2019-2025 weeks).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl
from scipy import stats
from scipy.special import expit, logit

from nflengine.models.backtest import SampleWeights
from nflengine.models.player_schema import Target

FEATURE_PREFIXES = ("own_", "use_", "team_", "rip_", "avail_", "eff_", "opp_", "pos_")
# feature families only some groups use: adding them to every target would change the P06 models
GROUP_PREFIXES = {"CB/S": ("cvg_",)}
EXTRA_FEATURES = ("week",)
QUANTILES = (0.1, 0.5, 0.9)
COVERAGE = 0.8
MIN_HISTORY = 300  # walk-forward rows needed before the range / dispersion come from them
MAX_DISPERSION = 1e4  # r above this = Poisson
TAILS = (0.10, 0.125, 0.15, 0.175, 0.20, 0.225, 0.25)  # count-range tail levels tried
TOP_DRIVERS = 3
CALIBRATIONS = ("none", "platt", "isotonic")
MIN_CAL_ROWS = 1000  # walk-forward rows before a calibration layer is fitted (else raw)
MIN_CAL_EVENTS = 40  # ... and events (and non-events)
CAL_EPS = 1e-4  # calibrated probabilities stay inside [eps, 1 - eps]


@dataclass(frozen=True)
class PlayerModelConfig:
    num_leaves: int = 15
    learning_rate: float = 0.05
    n_estimators: int = 300
    min_data_in_leaf: int = 100
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    bagging_freq: int = 1
    lambda_l2: float = 1.0
    seed: int = 7
    num_threads: int = 4
    conformal_seasons: int = 2
    calibration: str = "platt"  # prob targets: none | platt | isotonic
    calibration_seasons: int = 3  # walk-forward seasons the calibration layer is fitted on
    weights: SampleWeights = field(default_factory=SampleWeights)

    def __post_init__(self) -> None:
        if self.calibration not in CALIBRATIONS:
            raise ValueError(f"calibration must be one of {CALIBRATIONS}, not {self.calibration!r}")

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any] | None = None, **overrides: Any):
        values = {k: v for k, v in dict(cfg or {}).items() if k != "per_target"}
        known = {f.name for f in fields(cls)}
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"unknown player_model settings: {sorted(unknown)}")
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)

    def lgb_params(self, objective: str, alpha: float | None = None) -> dict[str, Any]:
        p: dict[str, Any] = {
            "objective": objective,
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
        if alpha is not None:
            p["alpha"] = alpha
        return p

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["weights"] = self.weights.as_dict()
        return d


def feature_columns(frame: pl.DataFrame, target: Target | None = None) -> list[str]:
    """The model's features: every family column (prefixes) plus `week`, sorted. A `target`
    in a group with its own family (`GROUP_PREFIXES`: CB/S -> `cvg_`) adds that family; every
    other target (and `None`) gets the shared families only."""
    prefixes = FEATURE_PREFIXES + (GROUP_PREFIXES.get(target.group, ()) if target else ())
    cols = [c for c in frame.columns if c.startswith(prefixes)]
    return sorted(cols) + [c for c in EXTRA_FEATURES if c in frame.columns]


def add_position_flags(frame: pl.DataFrame) -> pl.DataFrame:
    """`pos_*` flags for pools that mix position groups (WR/TE, DL/LB, LB/S)."""
    return frame.with_columns(
        (pl.col("pgroup") == "TE").cast(pl.Float64).alias("pos_te"),
        (pl.col("pgroup") == "LB").cast(pl.Float64).alias("pos_lb"),
        (pl.col("pgroup") == "S").cast(pl.Float64).alias("pos_s"),
    )


def feature_hash(features: Sequence[str]) -> str:
    return hashlib.sha256(",".join(sorted(features)).encode()).hexdigest()[:10]


def _x(df: pl.DataFrame, cols: Sequence[str]) -> np.ndarray:
    return df.select([pl.col(c).cast(pl.Float64) for c in cols]).to_numpy()


# ---- distributions ---------------------------------------------------------------------------


def nb_dispersion(y: np.ndarray, mu: np.ndarray) -> float:
    """Method-of-moments negative-binomial dispersion r (Var = mu + mu^2 / r); a large r
    (no overdispersion) means Poisson."""
    ok = np.isfinite(y) & np.isfinite(mu)
    y, mu = y[ok], mu[ok]
    if y.size == 0:
        return MAX_DISPERSION
    excess = float(np.mean((y - mu) ** 2) - np.mean(mu))
    if excess <= 1e-9:
        return MAX_DISPERSION
    return float(min(np.mean(mu**2) / excess, MAX_DISPERSION))


def count_quantiles(mu: np.ndarray, r: float, qs: Sequence[float] = QUANTILES) -> np.ndarray:
    """Quantiles (rows x len(qs)) of NB(mean mu, dispersion r), or Poisson for large r."""
    mu = np.clip(np.asarray(mu, dtype=float), 1e-6, None)
    if r >= MAX_DISPERSION:
        return np.column_stack([stats.poisson.ppf(q, mu) for q in qs])
    p = r / (r + mu)
    return np.column_stack([stats.nbinom.ppf(q, r, p) for q in qs])


def conformal_shift(
    p10: np.ndarray, p90: np.ndarray, y: np.ndarray, coverage: float = COVERAGE
) -> float:
    """CQR: how much to widen (or narrow, if negative) a raw range so `coverage` of the
    history falls inside it."""
    ok = np.isfinite(y) & np.isfinite(p10) & np.isfinite(p90)
    if ok.sum() == 0:
        return 0.0
    e = np.maximum(p10[ok] - y[ok], y[ok] - p90[ok])
    n = e.size
    level = min(1.0, np.ceil((n + 1) * coverage) / n)
    return float(np.quantile(e, level))


def count_cdf(k: int, mu: np.ndarray, r: float) -> np.ndarray:
    """P(count <= k) under NB(mean mu, dispersion r), or Poisson for large r."""
    mu = np.clip(np.asarray(mu, dtype=float), 1e-6, None)
    if r >= MAX_DISPERSION:
        return stats.poisson.cdf(k, mu)
    return stats.nbinom.cdf(k, r, r / (r + mu))


def event_probs(mu: np.ndarray, r: float) -> tuple[np.ndarray, np.ndarray]:
    """(P(>= 1), P(>= 2)) of a count with mean `mu` and dispersion `r` (P08 event counts)."""
    return 1.0 - count_cdf(0, mu, r), 1.0 - count_cdf(1, mu, r)


@dataclass
class Calibrator:
    """A monotone map from a raw classifier probability to a calibrated one.

    `platt`: `expit(slope * logit(p) + intercept)`; `isotonic`: a fitted step function;
    `none`: the identity (burn-in, or too few events to fit on)."""

    kind: str = "none"
    slope: float = 1.0
    intercept: float = 0.0
    iso: Any = None
    n: int = 0  # rows it was fitted on

    def __call__(self, p: np.ndarray) -> np.ndarray:
        p = np.clip(np.asarray(p, dtype=float), CAL_EPS, 1 - CAL_EPS)
        if self.kind == "platt":
            return np.clip(expit(self.slope * logit(p) + self.intercept), CAL_EPS, 1 - CAL_EPS)
        if self.kind == "isotonic" and self.iso is not None:
            return np.clip(self.iso.predict(p), CAL_EPS, 1 - CAL_EPS)
        return p


def _platt_fit(p: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Logistic regression of y on logit(p) (Newton steps; a tiny ridge keeps it stable)."""
    x = np.column_stack([logit(np.clip(p, CAL_EPS, 1 - CAL_EPS)), np.ones(p.size)])
    beta = np.array([1.0, 0.0])
    for _ in range(50):
        q = expit(x @ beta)
        grad = x.T @ (y - q)
        hess = (x * (q * (1 - q) + 1e-9)[:, None]).T @ x + 1e-6 * np.eye(2)
        step = np.linalg.solve(hess, grad)
        beta = beta + step
        if np.abs(step).max() < 1e-8:
            break
    return float(beta[0]), float(beta[1])


def fit_calibrator(p_raw: np.ndarray, y: np.ndarray, kind: str = "platt") -> Calibrator:
    """Fit `kind` on earlier walk-forward raw probabilities `p_raw` and their 0/1 outcomes.
    Returns the identity when there are too few rows or events (`MIN_CAL_ROWS`,
    `MIN_CAL_EVENTS`): the burn-in uses raw probabilities, never an in-sample fit."""
    ok = np.isfinite(p_raw) & np.isfinite(y)
    p, y = p_raw[ok], y[ok]
    events = float(y.sum())
    if (
        kind == "none"
        or p.size < MIN_CAL_ROWS
        or events < MIN_CAL_EVENTS
        or p.size - events < MIN_CAL_EVENTS
    ):
        return Calibrator()
    if kind == "platt":
        a, b = _platt_fit(p, y)
        return Calibrator("platt", slope=a, intercept=b, n=int(p.size))
    from sklearn.isotonic import IsotonicRegression

    iso = IsotonicRegression(y_min=CAL_EPS, y_max=1 - CAL_EPS, out_of_bounds="clip")
    iso.fit(np.clip(p, CAL_EPS, 1 - CAL_EPS), y)
    return Calibrator("isotonic", iso=iso, n=int(p.size))


# ---- one fit ---------------------------------------------------------------------------------


@dataclass
class FittedPlayerModel:
    target: Target
    features: list[str]
    boosters: dict[str, lgb.Booster]  # amounts: q10/q50/q90; counts and prob: mean
    scale: float  # typical baseline miss (z of outperformance)
    n_train: int
    trained_through: tuple[int, int] | None
    shift: float = 0.0  # conformal shift (amounts)
    dispersion: float = MAX_DISPERSION  # NB r (counts)
    tail: float = 0.10  # count-range tail level (P10 / P90 for an exact distribution)
    calibrator: Calibrator = field(default_factory=Calibrator)  # prob targets

    def raw(self, frame: pl.DataFrame) -> dict[str, np.ndarray]:
        x = _x(frame, self.features)
        if self.target.kind == "amount":
            q = {k: b.predict(x) for k, b in self.boosters.items()}
            p50 = q["q50"]
            return {
                "p10_raw": np.minimum(q["q10"], p50),
                "p50": p50,
                "p90_raw": np.maximum(q["q90"], p50),
            }
        return {"mean": self.boosters["mean"].predict(x)}

    def predict(self, frame: pl.DataFrame) -> pl.DataFrame:
        r = self.raw(frame)
        n = frame.height
        nan = np.full(n, np.nan)
        if self.target.kind == "amount":
            p10 = np.minimum(r["p10_raw"] - self.shift, r["p50"])
            p90 = np.maximum(r["p90_raw"] + self.shift, r["p50"])
            cols = {
                "p10": p10,
                "p50": r["p50"],
                "p90": p90,
                "mean": r["p50"],
                "p_ge1": nan,
                "p_ge2": nan,
                "_p10_raw": r["p10_raw"],
                "_p90_raw": r["p90_raw"],
            }
        elif self.target.kind == "prob":
            p = self.calibrator(r["mean"])
            cols = {
                "p10": nan,
                "p50": nan,
                "p90": nan,
                "mean": p,
                "p_ge1": p,
                "p_ge2": nan,
                "_p_raw": r["mean"],
            }
        else:
            q = count_quantiles(r["mean"], self.dispersion, (self.tail, 0.5, 1 - self.tail))
            raw1, raw2 = (
                event_probs(r["mean"], self.dispersion) if self.target.event_probs else (nan, nan)
            )
            ge1 = self.calibrator(raw1) if self.target.event_probs else nan
            cols = {
                "p10": q[:, 0],
                "p50": q[:, 1],
                "p90": q[:, 2],
                "mean": r["mean"],
                "p_ge1": ge1,
                "p_ge2": np.minimum(raw2, ge1),  # never above P(>= 1) once that is recalibrated
                "_p_raw": raw1,  # the negative binomial's own P(>= 1), what the layer is fitted on
            }
        return pl.DataFrame({k: np.asarray(v, dtype=float) for k, v in cols.items()})

    def contributions(self, frame: pl.DataFrame) -> np.ndarray:
        """SHAP values (rows x features) in target units (no bias column): counts are
        multiplied by the mean (log scale -> counts), probability targets by p (1 - p)
        (log-odds -> probability), both first-order conversions."""
        main = self.boosters["q50" if self.target.kind == "amount" else "mean"]
        x = _x(frame, self.features)
        c = main.predict(x, pred_contrib=True)[:, :-1]
        if self.target.kind == "count":
            c = c * main.predict(x)[:, None]
        elif self.target.kind == "prob":
            p = self.calibrator(main.predict(x))
            c = c * (p * (1 - p))[:, None]
        return c


def _records(record_at: tuple[int, int] | None, test: pl.DataFrame) -> bool:
    """Whether this walk-forward fit is the one whose training curves are recorded."""
    if record_at is None or not test.height:
        return False
    return (int(test["season"][0]), int(test["week"][0])) == tuple(record_at)


def _scale(train: pl.DataFrame) -> float:
    recent = train.filter(pl.col("season") >= train["season"].max() - 1)
    d = (recent["y"] - recent["baseline"]).abs().drop_nulls()
    return float(d.mean()) if d.len() else 1.0


def fit_player_model(
    train: pl.DataFrame,
    weights: np.ndarray,
    target: Target,
    features: Sequence[str],
    config: PlayerModelConfig,
    history: pl.DataFrame | None = None,
    valid: pl.DataFrame | None = None,
    evals: dict[str, dict] | None = None,
    main_only: bool = False,
) -> FittedPlayerModel:
    """Fit one target's boosters on `train` (column `y`), with the range / dispersion from
    `history` (earlier walk-forward predictions with `y`) when it has enough rows.

    `evals`: also record each booster's per-round loss (W&B curves): on the training rows,
    and on `valid` when given. Recording reads the loss LightGBM computes anyway; it never
    changes a booster (no early stopping; `tests/models/test_training_charts.py` proves the
    trees are byte-identical with and without it).
    `main_only`: amounts fit only the P50 model (tuning; `predict` then can't be used).
    """
    feats = list(features)
    x = _x(train, feats)
    y = train["y"].to_numpy().astype(float)
    ds = lgb.Dataset(x, y, weight=weights, feature_name=feats, free_raw_data=False)
    vsets, names = [ds], ["train"]
    if valid is not None and valid.height:
        vsets.append(
            lgb.Dataset(_x(valid, feats), valid["y"].to_numpy().astype(float), reference=ds)
        )
        names.append("valid")

    def train_one(params: dict[str, Any], key: str) -> lgb.Booster:
        cb = []
        if evals is not None:
            evals[key] = {}
            cb.append(lgb.record_evaluation(evals[key]))
        return lgb.train(
            params,
            ds,
            num_boost_round=config.n_estimators,
            valid_sets=vsets if cb else None,
            valid_names=names if cb else None,
            callbacks=cb,
        )

    boosters: dict[str, lgb.Booster] = {}
    if target.kind == "amount":
        for q in (0.5,) if main_only else QUANTILES:
            k = f"q{int(q * 100):02d}"
            boosters[k] = train_one(config.lgb_params("quantile", q), k)
    elif target.kind == "prob":
        boosters["mean"] = train_one(config.lgb_params("binary"), "mean")
    else:
        boosters["mean"] = train_one(config.lgb_params("poisson"), "mean")
    tt = None
    if train.height:
        last = train.sort("season", "week").row(-1, named=True)
        tt = (int(last["season"]), int(last["week"]))
    fit = FittedPlayerModel(target, feats, boosters, _scale(train), train.height, tt)

    if target.kind == "prob":
        # calibration on the model's own earlier walk-forward raw probabilities; none in the
        # burn-in (never fitted on the training rows themselves)
        h = _recent_history(history, train, config.calibration_seasons, "_p_raw", MIN_CAL_ROWS)
        if h is not None:
            fit.calibrator = fit_calibrator(
                h["_p_raw"].to_numpy().astype(float),
                h["y"].to_numpy().astype(float),
                config.calibration,
            )
        return fit
    h = _recent_history(history, train, config.conformal_seasons)
    if target.kind == "amount":
        if h is not None:
            fit.shift = conformal_shift(
                h["_p10_raw"].to_numpy(), h["_p90_raw"].to_numpy(), h["y"].to_numpy()
            )
    else:
        if h is not None:
            hy, hm = h["y"].to_numpy(), h["mean"].to_numpy()
            fit.dispersion = nb_dispersion(hy, hm)
            fit.tail = count_tail(hy, hm, fit.dispersion)
        else:  # in-sample fallback for the first weeks (burn-in only)
            fit.dispersion = nb_dispersion(y, boosters["mean"].predict(x))
        if target.event_probs:
            # the count distribution fitted for the mean, median and range isn't exactly right
            # about zero (passing TDs 0.75 predicted vs 0.78 seen: a QB's TD count is more
            # regular than a Poisson; sacks 0.155 vs 0.167), so P(>= 1) is recalibrated on its
            # own earlier walk-forward values; identity in the burn-in
            hc = _recent_history(history, train, config.calibration_seasons, "_p_raw", MIN_CAL_ROWS)
            if hc is not None:
                fit.calibrator = fit_calibrator(
                    hc["_p_raw"].to_numpy().astype(float),
                    (hc["y"].to_numpy().astype(float) >= target.event_min).astype(float),
                    config.calibration,
                )
    return fit


def count_tail(y: np.ndarray, mu: np.ndarray, r: float, coverage: float = COVERAGE) -> float:
    """The tail level whose [tail, 1 - tail] NB range covers closest to `coverage` of the
    history (ties: the narrower range)."""
    ok = np.isfinite(y) & np.isfinite(mu)
    y, mu = y[ok], mu[ok]
    if y.size == 0:
        return TAILS[0]
    best, best_gap = TAILS[0], np.inf
    for a in TAILS:
        q = count_quantiles(mu, r, (a, 1 - a))
        cov = float(np.mean((y >= q[:, 0]) & (y <= q[:, 1])))
        gap = abs(cov - coverage)
        if gap <= best_gap + 1e-12:
            best, best_gap = a, gap
    return best


def _recent_history(
    history: pl.DataFrame | None,
    train: pl.DataFrame,
    seasons: int,
    need: str | None = None,
    min_rows: int = MIN_HISTORY,
) -> pl.DataFrame | None:
    """The last `seasons` seasons of earlier walk-forward predictions that have an outcome
    (and the column `need`, when given); None when there are fewer than `min_rows`."""
    if history is None or history.is_empty() or "y" not in history.columns:
        return None
    if need is not None and need not in history.columns:
        return None
    if not train.height:
        return None
    first = int(train["season"].max()) - seasons + 1
    keep = pl.col("y").is_not_null() & (pl.col("season") >= first)
    if need is not None:
        keep = keep & pl.col(need).is_not_null()
    h = history.filter(keep)
    return h if h.height >= min_rows else None


# ---- harness model ---------------------------------------------------------------------------


ID_COLS = ["season", "week", "player_id", "game_id"]


class PlayerWeekModel:
    """`walk_forward` model for one target: fit on `train`, predict `test`. Keeps the last
    fit in `.last`; `explain=True` adds SHAP drivers (`_contrib`)."""

    def __init__(
        self,
        target: Target,
        features: Sequence[str],
        config: PlayerModelConfig,
        explain: bool = False,
    ):
        self.target = target
        self.features = list(features)
        self.config = config
        self.explain = explain
        self.last: FittedPlayerModel | None = None
        self.curves: dict[int, dict[str, dict]] = {}  # season -> eval history (W&B)
        # the weekly refit's training charts: the fit for this (season, week) records each
        # booster's per-round training loss into `last_evals` (visibility only)
        self.record_at: tuple[int, int] | None = None
        self.last_evals: dict[str, dict] | None = None

    def __call__(
        self, train: pl.DataFrame, weights: np.ndarray, test: pl.DataFrame, history: pl.DataFrame
    ) -> pl.DataFrame:
        if self.target.label_lag and test.height:
            # a week-late label (PFR pressures) isn't known on the key's Tuesday yet
            kt = (int(test["season"][0]) - 2000) * 22 + int(test["week"][0])
            t = (train["season"].cast(pl.Int32) - 2000) * 22 + train["week"].cast(pl.Int32)
            keep = (t < kt - self.target.label_lag).to_numpy()
            train, weights = train.filter(pl.Series(keep)), weights[keep]
            if history.height:
                ht = (history["season"].cast(pl.Int32) - 2000) * 22 + history["week"].cast(pl.Int32)
                history = history.filter(ht < kt - self.target.label_lag)
        evals = {} if _records(self.record_at, test) else None
        fit = fit_player_model(
            train, weights, self.target, self.features, self.config, history=history, evals=evals
        )
        self.last = fit
        if evals is not None:
            self.last_evals = evals
        pred = fit.predict(test)
        out = test.select(
            *ID_COLS,
            "baseline",
            pl.col("y"),
        ).hstack(pred)
        out = out.with_columns(
            baseline_p50(out["baseline"], self.target, fit.dispersion),
            baseline_p_ge1(out["baseline"], self.target, fit.dispersion, fit.calibrator),
        )
        out = out.with_columns(
            pl.lit(fit.scale).alias("scale"),
            pl.lit(fit.n_train).cast(pl.Int32).alias("n_train"),
            pl.lit(self._range_param(fit), dtype=pl.Float64).alias("range_param"),
            pl.lit(fit.tail if self.target.kind == "count" else None, dtype=pl.Float64).alias(
                "range_tail"
            ),
        )
        if self.explain:
            out = out.with_columns(pl.Series("_contrib", list(fit.contributions(test))))
        return out

    def _range_param(self, fit: FittedPlayerModel) -> float | None:
        """What the run logs as `bt/range_param`: the conformal shift (amounts), the NB
        dispersion (counts), the Platt slope (prob; 1 = no recalibration, null for the
        identity and isotonic)."""
        if self.target.kind == "amount":
            return fit.shift
        if self.target.kind == "prob":
            return fit.calibrator.slope if fit.calibrator.kind == "platt" else None
        return fit.dispersion


def baseline_p50(baseline: pl.Series, target: Target, dispersion: float) -> pl.Series:
    """The baseline as the same kind of projection as P50 (D65): a count's P50 is a
    negative-binomial median, and a median beats any mean on MAE for skewed, zero-heavy
    counts (pressures: 16% "better" than the rolling mean, 3% better than its median), so
    counts are scored against the median at the baseline mean, with the same dispersion.
    A probability target has no P50: null."""
    if target.kind == "amount":
        return baseline.alias("baseline_p50")
    if target.kind == "prob":
        return pl.Series("baseline_p50", np.full(baseline.len(), np.nan)).fill_nan(None)
    b = baseline.to_numpy().astype(float)
    ok = np.isfinite(b)
    med = np.full(b.shape, np.nan)
    if ok.any():
        med[ok] = count_quantiles(np.clip(b[ok], 1e-6, None), dispersion, (0.5,))[:, 0]
    return pl.Series("baseline_p50", med, dtype=pl.Float64).fill_nan(None)


def baseline_p_ge1(
    baseline: pl.Series,
    target: Target,
    dispersion: float,
    calibrator: Calibrator | None = None,
) -> pl.Series:
    """The baseline's P(event), the bar Brier is scored against (P08): an event count's
    P(>= 1) from the negative binomial at the baseline mean, through the same dispersion and
    calibration layer as the model's (so the model and its baseline differ only in the mean);
    a probability target's baseline already is the player's rolling rate of the 0/1 label.
    Null for the other targets."""
    b = baseline.to_numpy().astype(float)
    out = np.full(b.shape, np.nan)
    ok = np.isfinite(b)
    if target.kind == "prob":
        out[ok] = np.clip(b[ok], 0.0, 1.0)
    elif target.kind == "count" and target.event_probs and ok.any():
        p = event_probs(np.clip(b[ok], 1e-6, None), dispersion)[0]
        out[ok] = calibrator(p) if calibrator is not None else p
    return pl.Series("baseline_p_ge1", out, dtype=pl.Float64).fill_nan(None)


def top_drivers(
    contrib: np.ndarray,
    features: Sequence[str],
    values: np.ndarray,
    phrases: Mapping[str, str],
    k: int = TOP_DRIVERS,
) -> list[list[dict[str, Any]]]:
    """Per row, the `k` features with the largest |SHAP| as driver structs."""
    out: list[list[dict[str, Any]]] = []
    for i in range(contrib.shape[0]):
        order = np.argsort(-np.abs(contrib[i]))[:k]
        row = []
        for j in order:
            if not np.isfinite(contrib[i, j]) or contrib[i, j] == 0:
                continue
            f = features[j]
            v = values[i, j]
            row.append(
                {
                    "feature": f,
                    "phrase": phrases.get(f, humanize(f)),
                    "value": float(v) if np.isfinite(v) else None,
                    "contribution": float(contrib[i, j]),
                }
            )
        out.append(row)
    return out


def humanize(feature: str) -> str:
    return feature.replace("_", " ")


def with_config(config: PlayerModelConfig, **changes: Any) -> PlayerModelConfig:
    return replace(config, **changes)
