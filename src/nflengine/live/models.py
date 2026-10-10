"""The six live-decision models (LD00): fit, score, save and load.

- `wp`: LightGBM binary, monotone in score difference and spread; the offense wins.
- `gain`: LightGBM multiclass, 77 classes (-10 ... +65 yards, touchdown); yards gained on 3rd
  and 4th downs.
- `fg`: logistic, piecewise-linear in distance + era, roof, weather; the kick is good.
- `punt`: kernel-smoothed empirical distribution by the punt's yard line; the next possession.
- `kickoff`: empirical distribution by kickoff era (onside kicks left out); the next possession.
- `pass`: LightGBM binary on the situation + our own win probability; a dropback.

Plus the try after a touchdown (`pat`: extra-point and two-point rates by era) and the
bootstrap refits of `gain` and `fg` that the confidence label is scored with.

Nothing here imports from `nflengine.models` (D107: no coupling to production code).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from nflengine.live import schema

DEFAULTS: dict[str, Any] = {
    # shapes chosen on the tuning window (fit 2010-2012, early-stopped on 2013, seasons the
    # leave-one-season-out backtest never reports); rounds = the early-stopped count x ~2.2
    # for ~5x more training rows (model card -> Tuning)
    "wp": {"num_leaves": 7, "learning_rate": 0.05, "n_estimators": 150, "min_data_in_leaf": 200},
    "gain": {
        "num_leaves": 4,
        "learning_rate": 0.15,
        "n_estimators": 50,
        "min_data_in_leaf": 300,
        "lambda_l2": 10.0,
    },
    "pass": {"num_leaves": 31, "learning_rate": 0.05, "n_estimators": 600, "min_data_in_leaf": 200},
    "fg": {"C": 10.0},
    "punt": {"bandwidth": 1.0, "min_effective_n": 400.0},
    "n_boot": 20,
    "threads": 8,
    "seed": 7,
}

OUTCOMES = ("recv", "ret_td", "keep")


def settings(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Model settings: code defaults < settings.yaml `live.models` < `overrides`."""
    from nflengine.settings import get_config

    out = json.loads(json.dumps(DEFAULTS))
    for src in ((get_config().live or {}).get("models") or {}, overrides or {}):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k].update(v)
            else:
                out[k] = v
    return out


# --- feature matrices -----------------------------------------------------------------------
def matrix(df: pl.DataFrame, features: list[str]) -> np.ndarray:
    return df.select([pl.col(f).cast(pl.Float64) for f in features]).to_numpy()


def state_matrix(states: list[dict[str, float]], features: list[str]) -> np.ndarray:
    return np.array([[s[f] for f in features] for s in states], dtype=np.float64)


def _lgb_params(cfg: dict, objective: str, threads: int, seed: int, **extra) -> dict:
    return {
        "objective": objective,
        "num_leaves": cfg["num_leaves"],
        "learning_rate": cfg["learning_rate"],
        "min_data_in_leaf": cfg["min_data_in_leaf"],
        "feature_fraction": cfg.get("feature_fraction", 1.0),
        "lambda_l2": cfg.get("lambda_l2", 1.0),
        "num_threads": threads,
        "seed": seed,
        "deterministic": True,
        "force_row_wise": True,
        "verbosity": -1,
        **extra,
    }


def _train(
    params: dict,
    X: np.ndarray,
    y: np.ndarray,
    rounds: int,
    names: list[str],
    weight: np.ndarray | None = None,
    valid: tuple[np.ndarray, np.ndarray] | None = None,
    callbacks: list | None = None,
) -> lgb.Booster:
    ds = lgb.Dataset(X, y, weight=weight, feature_name=names, free_raw_data=True)
    valid_sets, valid_names = [ds], ["train"]
    if valid is not None:
        valid_sets.append(lgb.Dataset(valid[0], valid[1], reference=ds))
        valid_names.append("valid")
    return lgb.train(
        params,
        ds,
        num_boost_round=rounds,
        valid_sets=valid_sets,
        valid_names=valid_names,
        callbacks=callbacks or [],
    )


# --- wp ---------------------------------------------------------------------------------------
# The base: a logistic regression on smooth terms of the WP features. Boosting from it (LightGBM
# `init_score`) beat every tree model on its own on the tuning window (fit 2010-2012, scored on
# 2013: Brier 0.1525 vs 0.1550 for the best pure-tree setting, calibration error 0.012 vs 0.016):
# every snap of a game shares one label, so trees from scratch learn game noise (model card).
_I = {f: i for i, f in enumerate(schema.WP_FEATURES)}
WP_BASE_TERMS = ["spread", "spread_time", "score_diff", "diff_time_ratio", "yardline_elapsed"]
WP_BASE_MONOTONE = {"spread": 1, "spread_time": 1, "score_diff": 1, "diff_time_ratio": 1}


def wp_base_cols(X: np.ndarray) -> np.ndarray:
    """The base's terms from a WP feature matrix (`schema.WP_FEATURES` order)."""
    return np.column_stack(
        [
            X[:, _I["spread"]],
            X[:, _I["spread_time"]],
            X[:, _I["score_diff"]],
            np.clip(X[:, _I["diff_time_ratio"]], -500.0, 500.0),
            X[:, _I["yardline_100"]] * (1.0 - X[:, _I["game_seconds"]] / 3600.0),
        ]
    )


@dataclass
class WPModel:
    """Win probability = logistic(base(X) + trees(X)): LightGBM boosted from a logistic base,
    monotone in score difference and spread (the base's coefficients on them are >= 0 and the
    trees are constrained)."""

    booster: lgb.Booster
    coef: list[float]
    intercept: float

    def base(self, X: np.ndarray) -> np.ndarray:
        return wp_base_cols(X) @ np.asarray(self.coef) + self.intercept

    def predict(self, X: np.ndarray, num_threads: int = 0) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        z = self.booster.predict(X, raw_score=True, num_threads=num_threads) + self.base(X)
        return 1.0 / (1.0 + np.exp(-z))

    def as_dict(self) -> dict:
        return {"terms": WP_BASE_TERMS, "coef": list(self.coef), "intercept": self.intercept}


def fit_wp(df: pl.DataFrame, cfg: dict, threads: int = 8, seed: int = 7, **kw) -> WPModel:
    X, y = matrix(df, schema.WP_FEATURES), df["win"].to_numpy()
    coef, intercept = _fit_wp_base(wp_base_cols(X), y)
    base = WPModel(None, coef, intercept)  # type: ignore[arg-type]
    params = _lgb_params(
        cfg["wp"],
        "binary",
        threads,
        seed,
        monotone_constraints=[schema.WP_MONOTONE.get(f, 0) for f in schema.WP_FEATURES],
        monotone_constraints_method="advanced",
    )
    ds = lgb.Dataset(X, y, init_score=base.base(X), feature_name=schema.WP_FEATURES)
    booster = lgb.train(
        params,
        ds,
        num_boost_round=cfg["wp"]["n_estimators"],
        valid_sets=[ds],
        valid_names=["train"],
        callbacks=kw.get("callbacks") or [],
    )
    return WPModel(booster, coef, intercept)


def _fit_wp_base(B: np.ndarray, y: np.ndarray) -> tuple[list[float], float]:
    """The logistic base with a coefficient >= 0 on every monotone term: a term that comes out
    negative (collinear `spread` / `spread_time` on a small sample) is dropped and the rest
    refit (Sol / tests-builder, LD00). 2010-2025 data never needs it."""
    from sklearn.linear_model import LogisticRegression

    keep = list(range(B.shape[1]))
    while True:
        lr = LogisticRegression(C=1.0, max_iter=5000).fit(B[:, keep], y)
        fitted = dict(zip(keep, (float(c) for c in lr.coef_[0]), strict=True))
        bad = [i for i in keep if WP_BASE_MONOTONE.get(WP_BASE_TERMS[i]) and fitted[i] < 0]
        if not bad:
            break
        keep = [i for i in keep if i not in bad]
        if not keep:
            raise ValueError("wp base: no term left with a non-negative coefficient")
    coef = [fitted.get(i, 0.0) for i in range(B.shape[1])]
    return coef, float(lr.intercept_[0])


# --- gain -------------------------------------------------------------------------------------
def gain_features(with_ratings: bool) -> list[str]:
    return schema.GAIN_FEATURES + (schema.GAIN_RATING_FEATURES if with_ratings else [])


def fit_gain(
    df: pl.DataFrame,
    cfg: dict,
    with_ratings: bool = False,
    threads: int = 8,
    seed: int = 7,
    weight: np.ndarray | None = None,
    **kw,
) -> lgb.Booster:
    feats = gain_features(with_ratings)
    params = _lgb_params(cfg["gain"], "multiclass", threads, seed, num_class=schema.N_GAIN_CLASSES)
    X, y = matrix(df, feats), df["gain_class"].to_numpy()
    return _train(params, X, y, cfg["gain"]["n_estimators"], feats, weight=weight, **kw)


def legal_gain(probs: np.ndarray, yardline_100: np.ndarray) -> np.ndarray:
    """Move impossible mass: a non-touchdown gain can't reach the goal line, so classes with
    gain >= yards to goal go to the touchdown class (rows sum to 1 after)."""
    p = probs.copy()
    over = _GAINS[None, :] >= np.asarray(yardline_100, dtype=float)[:, None]
    body = p[:, : schema.TD_CLASS]
    p[:, schema.TD_CLASS] += np.where(over, body, 0.0).sum(axis=1)
    body[over] = 0.0
    return p


_GAINS = np.asarray(schema.GAIN_VALUES, dtype=float)


def conversion_prob(probs: np.ndarray, ydstogo: np.ndarray) -> np.ndarray:
    """P(gain >= distance) from gain-class probabilities (the touchdown class converts)."""
    reach = _GAINS[None, :] >= np.asarray(ydstogo, dtype=float)[:, None]
    return probs[:, schema.TD_CLASS] + np.where(reach, probs[:, : schema.TD_CLASS], 0.0).sum(axis=1)


# --- fg ---------------------------------------------------------------------------------------
@dataclass
class FGModel:
    """P(make) = logistic(design(distance, era, indoor, wind, temp) . coef + intercept)."""

    coef: list[float]
    intercept: float
    wind_fill: float  # outdoor wind when unknown (the training median)

    def design(self, distance, era, indoor, wind, temp) -> np.ndarray:
        d = np.asarray(distance, dtype=float)
        n = d.shape[0]
        era = np.asarray(era, dtype=int)
        ind = np.asarray(indoor, dtype=float)
        w = np.array([np.nan if v is None else v for v in np.atleast_1d(wind)], dtype=float)
        t = np.array([np.nan if v is None else v for v in np.atleast_1d(temp)], dtype=float)
        if w.shape[0] == 1 and n > 1:
            w = np.repeat(w, n)
        if t.shape[0] == 1 and n > 1:
            t = np.repeat(t, n)
        w = np.where(np.isnan(w), self.wind_fill, w) * (1 - ind)
        cold = (np.nan_to_num(t, nan=60.0) < 40).astype(float) * (1 - ind)
        cols = [d / 10.0] + [np.maximum(0.0, d - k) / 10.0 for k in schema.FG_HINGES]
        cols += [(era == e).astype(float) for e in range(1, len(schema.ERA_STARTS))]
        cols += [ind, w / 10.0, cold]
        return np.column_stack(cols)

    def predict(self, distance, era, indoor, wind=None, temp=None) -> np.ndarray:
        X = self.design(distance, era, indoor, wind, temp)
        z = X @ np.asarray(self.coef) + self.intercept
        p = 1.0 / (1.0 + np.exp(-z))
        return np.where(np.asarray(distance, dtype=float) > schema.FG_MAX_DISTANCE, 0.0, p)

    def as_dict(self) -> dict:
        return {"coef": list(self.coef), "intercept": self.intercept, "wind_fill": self.wind_fill}


def fit_fg(
    df: pl.DataFrame, cfg: dict, weight: np.ndarray | None = None, wind_fill: float | None = None
) -> FGModel:
    """The fg logistic. A bootstrap refit passes the main model's `wind_fill`, so every refit
    shares one design matrix (the engine scores all of them in one product)."""
    from sklearn.linear_model import LogisticRegression

    if wind_fill is None:
        wind_fill = float(df["wind_out"].drop_nulls().median() or 8.0)
    m = FGModel([], 0.0, wind_fill)
    X = m.design(
        df["distance"].to_numpy(),
        df["era"].to_numpy(),
        df["indoor"].to_numpy(),
        df["wind_out"].to_list(),
        df["temp_out"].to_list(),
    )
    lr = LogisticRegression(C=cfg["fg"]["C"], max_iter=5000)
    lr.fit(X, df["make"].to_numpy(), sample_weight=weight)
    return FGModel([float(c) for c in lr.coef_[0]], float(lr.intercept_[0]), wind_fill)


def fg_distance_only(df: pl.DataFrame) -> Callable[[np.ndarray], np.ndarray]:
    """The fg baseline: a logistic on distance alone."""
    from sklearn.linear_model import LogisticRegression

    lr = LogisticRegression(max_iter=5000).fit(df["distance"].to_numpy().reshape(-1, 1), df["make"])
    return lambda d: lr.predict_proba(np.asarray(d, dtype=float).reshape(-1, 1))[:, 1]


# --- punt and kickoff: next-possession distributions -----------------------------------------
@dataclass
class NextPossession:
    """Where the next possession starts. `recv[s-1]` = P(the receiving team snaps first at s
    yards to goal), `ret_td` = P(the return is a touchdown), `keep[s-1]` = P(the kicking team
    keeps the ball, snapping at s yards to goal)."""

    recv: np.ndarray
    ret_td: float
    keep: np.ndarray

    def support(self, min_p: float = 5e-4) -> list[tuple[str, int, float]]:
        """(outcome, spot, prob) with tiny probabilities folded away (renormalised)."""
        out = [("recv", s + 1, float(p)) for s, p in enumerate(self.recv) if p >= min_p]
        out += [("keep", s + 1, float(p)) for s, p in enumerate(self.keep) if p >= min_p]
        if self.ret_td >= min_p:
            out.append(("ret_td", 0, float(self.ret_td)))
        total = sum(p for _, _, p in out)
        return [(o, s, p / total) for o, s, p in out]

    def mean_recv_spot(self) -> float:
        w = self.recv / max(self.recv.sum(), 1e-12)
        return float((w * np.arange(1, 100)).sum())

    def as_dict(self) -> dict:
        return {"recv": self.recv.tolist(), "ret_td": self.ret_td, "keep": self.keep.tolist()}

    @classmethod
    def from_dict(cls, d: dict) -> NextPossession:
        return cls(np.asarray(d["recv"]), float(d["ret_td"]), np.asarray(d["keep"]))


def _empirical(df: pl.DataFrame, weights: np.ndarray | None = None) -> NextPossession:
    w = np.ones(df.height) if weights is None else weights
    out = df["outcome"].to_numpy()
    spot = df["spot"].fill_null(0).to_numpy().astype(int)
    recv, keep = np.zeros(99), np.zeros(99)
    ok = (spot >= 1) & (spot <= 99)
    np.add.at(recv, spot[(out == "recv") & ok] - 1, w[(out == "recv") & ok])
    np.add.at(keep, spot[(out == "keep") & ok] - 1, w[(out == "keep") & ok])
    td = float(w[out == "ret_td"].sum())
    total = recv.sum() + keep.sum() + td
    return NextPossession(recv / total, td / total, keep / total)


@dataclass
class PuntModel:
    """A `NextPossession` for every punt yard line 1-99 (the punting team's yards to goal),
    smoothed with a Gaussian kernel over nearby yard lines (wider where punts are rare)."""

    by_yardline: list[NextPossession]
    bandwidth: list[float]

    def at(self, yardline_100: int) -> NextPossession:
        return self.by_yardline[int(min(max(yardline_100, 1), 99)) - 1]

    def as_dict(self) -> dict:
        return {"by_yardline": [n.as_dict() for n in self.by_yardline], "bandwidth": self.bandwidth}

    @classmethod
    def from_dict(cls, d: dict) -> PuntModel:
        return cls([NextPossession.from_dict(x) for x in d["by_yardline"]], d["bandwidth"])


def fit_punt(df: pl.DataFrame, cfg: dict) -> PuntModel:
    x = df["yardline_100"].to_numpy().astype(float)
    out, bws = [], []
    for y in range(1, 100):
        bw = float(cfg["punt"]["bandwidth"])
        while True:
            w = np.exp(-0.5 * ((x - y) / bw) ** 2)
            if w.sum() >= cfg["punt"]["min_effective_n"] or bw >= 15:
                break
            bw *= 1.25
        out.append(_empirical(df, w))
        bws.append(round(bw, 2))
    return PuntModel(out, bws)


@dataclass
class KickoffModel:
    """A `NextPossession` per kickoff era (`schema.kickoff_era`); an era with no training
    rows falls back to the nearest earlier one (the 2024 kickoff for a 2025 hold-out)."""

    by_era: dict[str, NextPossession]
    counts: dict[str, int] = field(default_factory=dict)

    ORDER = ("tb20", "tb25", "tb30", "tb35")

    def at(self, season: int) -> NextPossession:
        era = schema.kickoff_era(season)
        i = self.ORDER.index(era)
        for e in [*self.ORDER[i::-1], *self.ORDER[i + 1 :]]:
            if e in self.by_era:
                return self.by_era[e]
        raise KeyError("kickoff model has no eras")

    def as_dict(self) -> dict:
        return {"by_era": {k: v.as_dict() for k, v in self.by_era.items()}, "counts": self.counts}

    @classmethod
    def from_dict(cls, d: dict) -> KickoffModel:
        return cls({k: NextPossession.from_dict(v) for k, v in d["by_era"].items()}, d["counts"])


def fit_kickoff(df: pl.DataFrame) -> KickoffModel:
    df = df.filter(~pl.col("onside"))
    by, counts = {}, {}
    for (era,), part in df.group_by(["ko_era"], maintain_order=True):
        by[era] = _empirical(part)
        counts[era] = part.height
    return KickoffModel(by, counts)


# --- the try after a touchdown -----------------------------------------------------------------
def fit_pat(plays: pl.DataFrame) -> dict[str, dict[str, float]]:
    """Extra-point and two-point success by era: the extra point from the last three
    seasons of its era in the training data (it keeps improving), the two-point rate from all
    of them (small samples)."""
    out = {}
    for era in ("short", "long"):
        short = pl.col("season") <= max(
            s for s in range(2010, 2030) if schema.pat_era(s) == "short"
        )
        part = plays.filter(short if era == "short" else ~short)
        if part.is_empty():
            continue
        last = sorted(part["season"].unique().to_list())[-3:]
        xp = part.filter(pl.col("season").is_in(last) & (pl.col("extra_point_attempt") == 1))
        two = part.filter(pl.col("two_point_attempt") == 1)
        out[era] = {
            "xp": float((xp["extra_point_result"] == "good").mean() or 0.94),
            "two": float((two["two_point_conv_result"] == "success").mean() or 0.48),
            "xp_seasons": last,
        }
    return out


# --- pass -------------------------------------------------------------------------------------
def fit_pass(df: pl.DataFrame, cfg: dict, threads: int = 8, seed: int = 7, **kw) -> lgb.Booster:
    params = _lgb_params(cfg["pass"], "binary", threads, seed)
    X, y = matrix(df, schema.PASS_FEATURES), df["is_pass"].to_numpy()
    return _train(params, X, y, cfg["pass"]["n_estimators"], schema.PASS_FEATURES, **kw)


# --- the bundle -------------------------------------------------------------------------------
@dataclass
class LiveModels:
    wp: WPModel
    gain: lgb.Booster
    fg: FGModel
    punt: PuntModel
    kickoff: KickoffModel
    pass_: lgb.Booster
    pat: dict[str, dict[str, float]]
    gain_boot: list[lgb.Booster] = field(default_factory=list)
    fg_boot: list[FGModel] = field(default_factory=list)
    gain_ratings: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    # scoring (single-threaded: decisions score a few hundred rows at most)
    def wp_prob(self, X: np.ndarray) -> np.ndarray:
        return self.wp.predict(X, num_threads=1)

    def gain_probs(self, X: np.ndarray, boot: int | None = None) -> np.ndarray:
        model = self.gain if boot is None else self.gain_boot[boot]
        p = model.predict(X, num_threads=1)
        return legal_gain(p, X[:, 2])  # GAIN_FEATURES[2] = yardline_100

    def pass_prob(self, X: np.ndarray) -> np.ndarray:
        return self.pass_.predict(X, num_threads=1)

    def pat_rates(self, season: int) -> dict[str, float]:
        return self.pat.get(schema.pat_era(season)) or {"xp": 0.94, "two": 0.48}

    @property
    def gain_features(self) -> list[str]:
        return gain_features(self.gain_ratings)


@dataclass
class FitReport:
    seconds: dict[str, float] = field(default_factory=dict)
    rows: dict[str, int] = field(default_factory=dict)


def fit_all(
    frames: dict[str, pl.DataFrame],
    seasons: list[int],
    cfg: dict | None = None,
    n_boot: int | None = None,
    gain_ratings: bool = False,
    log: Callable[[str], None] = print,
    callbacks: dict[str, list] | None = None,
) -> tuple[LiveModels, FitReport]:
    """Fit every model on `seasons` only (each frame is filtered here, so a caller can't leak
    a held-out season in by mistake). `frames` holds `wp`, `gain`, `fg`, `punt`, `kickoff`,
    `pass` and `plays` (the try rates)."""
    cfg = cfg or settings()
    n_boot = cfg["n_boot"] if n_boot is None else n_boot
    th, seed = int(cfg["threads"]), int(cfg["seed"])
    keep = pl.col("season").is_in(seasons)
    f = {k: v.filter(keep) for k, v in frames.items()}
    rep = FitReport(rows={k: v.height for k, v in f.items()})
    cb = callbacks or {}

    def timed(name, fn):
        t = time.perf_counter()
        out = fn()
        rep.seconds[name] = round(time.perf_counter() - t, 2)
        rows = rep.rows.get(name)
        log(f"  {name}: " + (f"{rows:,} rows, " if rows else "") + f"{rep.seconds[name]} s")
        return out

    wp = timed("wp", lambda: fit_wp(f["wp"], cfg, th, seed, callbacks=cb.get("wp")))
    gain = timed(
        "gain",
        lambda: fit_gain(f["gain"], cfg, gain_ratings, th, seed, callbacks=cb.get("gain")),
    )
    fg = timed("fg", lambda: fit_fg(f["fg"], cfg))
    punt = timed("punt", lambda: fit_punt(f["punt"], cfg))
    kickoff = timed("kickoff", lambda: fit_kickoff(f["kickoff"]))
    pat = timed("pat", lambda: fit_pat(f["plays"]))
    pdf = f["pass"].with_columns(pl.Series("wp", wp.predict(matrix(f["pass"], schema.WP_FEATURES))))
    pas = timed("pass", lambda: fit_pass(pdf, cfg, th, seed, callbacks=cb.get("pass")))
    rng = np.random.default_rng(seed)
    gain_boot, fg_boot = [], []
    t = time.perf_counter()
    for b in range(n_boot):
        wg = np.bincount(
            rng.integers(0, f["gain"].height, f["gain"].height), minlength=f["gain"].height
        )
        gain_boot.append(
            fit_gain(f["gain"], cfg, gain_ratings, th, seed + b + 1, weight=wg.astype(float))
        )
        wf = np.bincount(rng.integers(0, f["fg"].height, f["fg"].height), minlength=f["fg"].height)
        fg_boot.append(fit_fg(f["fg"], cfg, weight=wf.astype(float), wind_fill=fg.wind_fill))
    if n_boot:
        rep.seconds["boot"] = round(time.perf_counter() - t, 2)
        log(f"  {n_boot} bootstrap refits of gain + fg: {rep.seconds['boot']} s")
    models = LiveModels(
        wp=wp, gain=gain, fg=fg, punt=punt, kickoff=kickoff, pass_=pas, pat=pat,
        gain_boot=gain_boot, fg_boot=fg_boot, gain_ratings=gain_ratings,
        meta={"seasons": sorted(seasons), "settings": cfg, "rows": rep.rows},
    )  # fmt: skip
    return models, rep


# --- save / load ------------------------------------------------------------------------------
MODEL_FILES = (
    "wp.txt",
    "wp_base.json",
    "gain.txt",
    "pass.txt",
    "fg.json",
    "punt.json",
    "kickoff.json",
    "pat.json",
)


def save(models: LiveModels, folder: Path) -> list[Path]:
    """Write every model under `folder` (LightGBM as text, the rest as JSON) + `meta.json`."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "gain_boot").mkdir(exist_ok=True)
    models.wp.booster.save_model(str(folder / "wp.txt"))
    models.gain.save_model(str(folder / "gain.txt"))
    models.pass_.save_model(str(folder / "pass.txt"))
    for b, g in enumerate(models.gain_boot):
        g.save_model(str(folder / "gain_boot" / f"{b:02d}.txt"))

    def dump(name, obj):
        (folder / name).write_text(json.dumps(obj, indent=1), encoding="utf-8")

    dump("fg.json", {"main": models.fg.as_dict(), "boot": [m.as_dict() for m in models.fg_boot]})
    dump("punt.json", models.punt.as_dict())
    dump("kickoff.json", models.kickoff.as_dict())
    dump("pat.json", models.pat)
    dump("wp_base.json", models.wp.as_dict())
    dump(
        "meta.json",
        {**models.meta, "gain_ratings": models.gain_ratings, "n_boot": len(models.gain_boot)},
    )
    return sorted(p for p in folder.rglob("*") if p.is_file())


def load(folder: Path) -> LiveModels:
    def read(name):
        return json.loads((folder / name).read_text(encoding="utf-8"))

    missing = [n for n in (*MODEL_FILES, "meta.json") if not (folder / n).exists()]
    if missing:
        raise FileNotFoundError(f"live-decision models in {folder} are missing {missing}")
    meta = read("meta.json")
    fg = read("fg.json")
    boots = sorted((folder / "gain_boot").glob("*.txt"))
    return LiveModels(
        wp=WPModel(
            lgb.Booster(model_file=str(folder / "wp.txt")),
            read("wp_base.json")["coef"],
            read("wp_base.json")["intercept"],
        ),
        gain=lgb.Booster(model_file=str(folder / "gain.txt")),
        fg=FGModel(**fg["main"]),
        punt=PuntModel.from_dict(read("punt.json")),
        kickoff=KickoffModel.from_dict(read("kickoff.json")),
        pass_=lgb.Booster(model_file=str(folder / "pass.txt")),
        pat=read("pat.json"),
        gain_boot=[lgb.Booster(model_file=str(p)) for p in boots],
        fg_boot=[FGModel(**m) for m in fg["boot"]],
        gain_ratings=bool(meta.get("gain_ratings")),
        meta=meta,
    )
