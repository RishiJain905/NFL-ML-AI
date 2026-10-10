"""Leave-one-season-out backtest of the live-decision models (LD00).

For each held-out season S the six models are fit on the other seasons of `train_seasons`
(`models.fit_all` filters every frame itself; `check_no_leak` then proves no row of S reached
the fit) and S is scored against a simple or published baseline on the same rows:

| model     | metric                          | baseline (same rows)                           |
|-----------|---------------------------------|------------------------------------------------|
| `wp`      | Brier, log loss, ECE (20 bins)  | nflfastR `vegas_wp` (fit on all seasons)       |
| `gain`    | conversion log loss, Brier, ECE | conversion rate by distance x era (fold train) |
| `fg`      | log loss                        | distance-only logistic (fold train)            |
| `pass`    | log loss, AUC                   | nflfastR `xpass` (fit on all seasons)          |
| `punt`    | receiving start mean / sd, rates | what happened                                 |
| `kickoff` | receiving start mean / sd, rates | what happened                                 |

and S's real 4th downs go through the decision engine: the coach's call against the bot's,
and the win-probability cost of the calls that differ. The ship rules (`ship_verdict`) were
fixed before any reported run (LD00 phase file, "Ship decision").

Definitions used throughout:
- **score state** from the offense's side: `tied` (0), `1-8` (one score either way), `9+`;
- **quarter**: Q1-Q4 and OT; plus the last two minutes of each half (`Q2 last 2:00`,
  `Q4 last 2:00`: 120 or fewer seconds left in the half);
- **ECE**: 20 equal-width bins (5 points), count-weighted |mean prediction - mean outcome|;
- log losses clip probabilities to [1e-6, 1 - 1e-6] (both sides alike).

Writes only `runs/backtests/live-decisions/<label>/` and one W&B run (`live-backtest`).
Nothing here imports from `nflengine.models` (D107).
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nflengine.live import data, models, schema
from nflengine.live.decide import Engine
from nflengine.paths import DataPaths, ensure_data_root

HELD_OUT = list(range(2014, 2026))  # the reported seasons (ship rules)
SMOKE_SEASONS = [2024, 2025]
ECE_BINS = 20
EPS = 1e-6
BASELINE_MIN_ROWS = 30  # a (distance, era) cell with fewer training rows uses distance only
TOSS_UP_GAP = 0.01
DECISION_THREADS = 4

# the pre-registered ship rules (scratch file 2026-10-10T04:58:47Z; rule 5, tests and the
# timing budget, is checked outside the backtest)
WP_ECE_MAX = 0.010
WP_BRIER_MARGIN = 0.002
SEASONS_NEEDED = 9
SEASONS_TOTAL = 12

QUARTERS = ["Q1", "Q2", "Q3", "Q4", "OT"]
TWO_MINUTE = ["Q2 last 2:00", "Q4 last 2:00"]
SCORE_STATES = ["tied", "1-8", "9+"]
FG_BANDS = ["<=29", "30-39", "40-49", "50-54", "55+"]


class LeakageError(RuntimeError):
    """A row of the held-out season could have reached the fold's fit."""


# --- metrics ----------------------------------------------------------------------------------
def _arr(x) -> np.ndarray:
    return np.asarray(x, dtype=float)


def brier(p, y) -> float:
    p, y = _arr(p), _arr(y)
    return float(np.mean((p - y) ** 2)) if p.size else math.nan


def log_loss(p, y) -> float:
    p, y = np.clip(_arr(p), EPS, 1 - EPS), _arr(y)
    if not p.size:
        return math.nan
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _bin_of(p: np.ndarray, bins: int) -> np.ndarray:
    return np.minimum((np.clip(p, 0.0, 1.0) * bins).astype(int), bins - 1)


def reliability(p, y, bins: int = ECE_BINS) -> pl.DataFrame:
    """Non-empty equal-width bins: `bin`, `bin_lo`, `n`, `mean_pred`, `mean_outcome`."""
    p, y = _arr(p), _arr(y)
    df = pl.DataFrame({"bin": _bin_of(p, bins), "p": p, "y": y})
    return (
        df.group_by("bin")
        .agg(
            pl.len().alias("n"),
            pl.col("p").mean().alias("mean_pred"),
            pl.col("y").mean().alias("mean_outcome"),
        )
        .with_columns((pl.col("bin") / bins).alias("bin_lo"))
        .select("bin", "bin_lo", "n", "mean_pred", "mean_outcome")
        .sort("bin")
    )


def ece(p, y, bins: int = ECE_BINS) -> float:
    """Expected calibration error: count-weighted |mean prediction - mean outcome| per bin."""
    p = _arr(p)
    if not p.size:
        return math.nan
    r = reliability(p, y, bins)
    gap = (r["mean_pred"] - r["mean_outcome"]).abs() * r["n"]
    return float(gap.sum() / p.size)


def auc(p, y) -> float:
    from sklearn.metrics import roc_auc_score

    y = _arr(y)
    if y.size == 0 or y.min() == y.max():
        return math.nan
    return float(roc_auc_score(y, _arr(p)))


# --- groupings --------------------------------------------------------------------------------
def score_state_expr(col: str = "score_diff") -> pl.Expr:
    """`tied`, `1-8` (one score, leading or trailing) or `9+`, from the offense's side."""
    a = pl.col(col).abs()
    return (
        pl.when(a == 0)
        .then(pl.lit("tied"))
        .when(a <= 8)
        .then(pl.lit("1-8"))
        .otherwise(pl.lit("9+"))
    )


def quarter_expr() -> pl.Expr:
    return (
        pl.when(pl.col("qtr") >= 5)
        .then(pl.lit("OT"))
        .otherwise(pl.lit("Q") + pl.col("qtr").cast(pl.Int64).cast(pl.String))
    )


def two_minute_expr() -> pl.Expr:
    """`Q2 last 2:00` / `Q4 last 2:00` (120 s or fewer left in the half), else null."""
    late = pl.col("half_seconds") <= 120
    return (
        pl.when(late & (pl.col("qtr") == 2))
        .then(pl.lit("Q2 last 2:00"))
        .when(late & (pl.col("qtr") == 4))
        .then(pl.lit("Q4 last 2:00"))
    )


def fg_band_expr() -> pl.Expr:
    d = pl.col("distance")
    return (
        pl.when(d <= 29)
        .then(pl.lit("<=29"))
        .when(d <= 39)
        .then(pl.lit("30-39"))
        .when(d <= 49)
        .then(pl.lit("40-49"))
        .when(d <= 54)
        .then(pl.lit("50-54"))
        .otherwise(pl.lit("55+"))
    )


def distance_bucket_expr() -> pl.Expr:
    """Distance 1..20, and 21 for 21+."""
    return pl.col("ydstogo").clip(1, 21).cast(pl.Int32).alias("dist_bucket")


# --- baselines --------------------------------------------------------------------------------
def conversion_baseline(train: pl.DataFrame, min_rows: int = BASELINE_MIN_ROWS):
    """The gain baseline: the training rows' conversion rate by distance (1..20, 21+) x era;
    a cell with fewer than `min_rows` rows falls back to the distance's rate (all eras), and
    a distance never seen to the overall rate. Returns `predict(df) -> np.ndarray`."""
    t = train.with_columns(distance_bucket_expr())
    cell = t.group_by("dist_bucket", "era").agg(
        pl.len().alias("n_cell"), pl.col("converted").cast(pl.Float64).mean().alias("rate_cell")
    )
    dist = t.group_by("dist_bucket").agg(
        pl.col("converted").cast(pl.Float64).mean().alias("rate_dist")
    )
    overall = float(t["converted"].cast(pl.Float64).mean() or 0.0)

    def predict(df: pl.DataFrame) -> np.ndarray:
        x = (
            df.select(
                pl.int_range(pl.len()).alias("_row"),
                "ydstogo",
                pl.col("era").cast(cell["era"].dtype),
            )
            .with_columns(distance_bucket_expr())
            .join(cell, on=["dist_bucket", "era"], how="left")
            .join(dist, on="dist_bucket", how="left")
            .sort("_row")
        )
        rate = (
            pl.when(pl.col("n_cell") >= min_rows)
            .then(pl.col("rate_cell"))
            .otherwise(pl.col("rate_dist"))
            .fill_null(overall)
        )
        return x.select(rate).to_series().to_numpy().astype(float)

    return predict


# --- leakage ----------------------------------------------------------------------------------
def check_no_leak(
    frames: dict[str, pl.DataFrame],
    held_out: int,
    fit_seasons: Iterable[int],
    fit_rows: dict[str, int] | None = None,
    model_seasons: Iterable[int] | None = None,
) -> None:
    """Raise `LeakageError` unless the fold's fit provably saw no row of `held_out`: the season
    is not among the fit seasons (nor the fitted bundle's own record of them), no training row
    shares a game with the held-out season, and the fit saw exactly the rows of the fit
    seasons (`fit_rows` = `FitReport.rows`)."""
    fit = sorted(set(fit_seasons))
    if held_out in fit:
        raise LeakageError(f"held-out season {held_out} is among the fit seasons")
    if model_seasons is not None and held_out in set(model_seasons):
        raise LeakageError(f"the fitted models record season {held_out} as a training season")
    for name, df in frames.items():
        train = df.filter(pl.col("season").is_in(fit))
        if train.filter(pl.col("season") == held_out).height:
            raise LeakageError(f"{name}: training rows from season {held_out}")
        if "game_id" in df.columns:
            held_games = df.filter(pl.col("season") == held_out)["game_id"].unique()
            if held_games.len() and train["game_id"].is_in(held_games.implode()).any():
                raise LeakageError(f"{name}: a training row shares a game with season {held_out}")
        if fit_rows is not None and name in fit_rows and fit_rows[name] != train.height:
            raise LeakageError(
                f"{name}: the fit saw {fit_rows[name]} rows, the fit seasons hold {train.height}"
            )


# --- loading ----------------------------------------------------------------------------------
def load_frames(
    seasons: Iterable[int], gain_ratings: bool = False, paths: DataPaths | None = None
) -> tuple[dict[str, pl.DataFrame], dict[str, int]]:
    """Every frame once for `seasons` (the fit frames + `fourth`, the coaches' 4th downs) and
    the wp frame's tie counts."""
    plays = data.with_state(data.load_plays(seasons, paths=paths))
    wp, counts = data.wp_frame(plays)
    gain = data.gain_frame(plays)
    if gain_ratings:
        gain = data.with_ratings(gain, paths=paths)
    frames = {
        "wp": wp,
        "gain": gain,
        "fg": data.fg_frame(plays),
        "punt": data.punt_frame(plays),
        "kickoff": data.kickoff_frame(plays),
        "pass": data.pass_frame(plays),
        "plays": plays,
        "fourth": data.fourth_frame(plays),
    }
    return frames, counts


# --- scoring one held-out season --------------------------------------------------------------
def score_wp(test: pl.DataFrame, fm: models.LiveModels) -> tuple[pl.DataFrame, dict]:
    """Our win probability vs `vegas_wp` on the same rows (rows without `vegas_wp` dropped
    from both, counted)."""
    p = fm.wp.predict(models.matrix(test, schema.WP_FEATURES)) if test.height else np.zeros(0)
    df = test.select(
        "season", "game_id", "play_id", "qtr", "score_diff", "half_seconds", "win", "vegas_wp"
    )
    df = df.with_columns(pl.Series("pred", p, dtype=pl.Float64))
    kept = df.filter(pl.col("vegas_wp").is_not_null())
    y, pm, pv = kept["win"].to_numpy(), kept["pred"].to_numpy(), kept["vegas_wp"].to_numpy()
    m = {
        "wp_n": kept.height,
        "wp_dropped_no_vegas": df.height - kept.height,
        "wp_brier": brier(pm, y),
        "wp_brier_vegas": brier(pv, y),
        "wp_logloss": log_loss(pm, y),
        "wp_logloss_vegas": log_loss(pv, y),
        "wp_ece": ece(pm, y),
        "wp_ece_vegas": ece(pv, y),
    }
    return kept, m


def score_gain(
    test: pl.DataFrame, train: pl.DataFrame, fm: models.LiveModels
) -> tuple[pl.DataFrame, dict]:
    """Conversion probability (P(gain >= distance)) vs `converted` on every 3rd/4th-down row,
    3rd downs and 4th-down attempts (selection bias: coaches go when they expect to convert),
    against the distance x era baseline; plus the multiclass log loss of the distribution."""
    base = conversion_baseline(train)(test) if test.height else np.zeros(0)
    if test.height:
        probs = models.legal_gain(
            fm.gain.predict(models.matrix(test, fm.gain_features)), test["yardline_100"].to_numpy()
        )
        conv = models.conversion_prob(probs, test["ydstogo"].to_numpy())
        p_true = probs[np.arange(test.height), test["gain_class"].to_numpy()]
    else:
        conv = p_true = np.zeros(0)
    df = test.select(
        "season",
        "game_id",
        "play_id",
        "down",
        "ydstogo",
        "yardline_100",
        "era",
        "converted",
        "gain_class",
    )
    df = df.with_columns(
        pl.Series("pred", conv, dtype=pl.Float64),
        pl.Series("base", base, dtype=pl.Float64),
        pl.Series("p_class", p_true, dtype=pl.Float64),
    )
    m = gain_metrics(df)
    return df, m


def gain_metrics(df: pl.DataFrame) -> dict[str, float]:
    m: dict[str, float] = {}
    for key, part in (
        ("", df),
        ("_3rd", df.filter(pl.col("down") == 3)),
        ("_4th", df.filter(pl.col("down") == 4)),
    ):
        y, p, b = part["converted"].to_numpy(), part["pred"].to_numpy(), part["base"].to_numpy()
        m[f"gain_n{key}"] = part.height
        m[f"gain_ll{key}"] = log_loss(p, y)
        m[f"gain_ll{key}_base"] = log_loss(b, y)
        m[f"gain_brier{key}"] = brier(p, y)
        m[f"gain_brier{key}_base"] = brier(b, y)
        m[f"gain_ece{key}"] = ece(p, y)
        m[f"gain_ece{key}_base"] = ece(b, y)
        m[f"gain_rate{key}"] = float(np.mean(y)) if part.height else math.nan
        m[f"gain_pred{key}"] = float(np.mean(p)) if part.height else math.nan
    pc = np.clip(df["p_class"].to_numpy(), EPS, 1.0)
    m["gain_mll"] = float(-np.mean(np.log(pc))) if df.height else math.nan
    return m


def score_fg(
    test: pl.DataFrame, train: pl.DataFrame, fm: models.LiveModels
) -> tuple[pl.DataFrame, dict]:
    """Our make probability vs a distance-only logistic fit on the fold's training kicks."""
    if test.height:
        p = fm.fg.predict(
            test["distance"].to_numpy(),
            test["era"].to_numpy(),
            test["indoor"].to_numpy(),
            test["wind_out"].to_list(),
            test["temp_out"].to_list(),
        )
        b = models.fg_distance_only(train)(test["distance"].to_numpy())
    else:
        p = b = np.zeros(0)
    df = test.select("season", "game_id", "play_id", "distance", "make").with_columns(
        pl.Series("pred", p, dtype=pl.Float64),
        pl.Series("base", b, dtype=pl.Float64),
        fg_band_expr().alias("band"),
    )
    y = df["make"].to_numpy()
    m = {
        "fg_n": df.height,
        "fg_ll": log_loss(p, y),
        "fg_ll_base": log_loss(b, y),
        "fg_brier": brier(p, y),
        "fg_brier_base": brier(b, y),
    }
    return df, m


def score_pass(test: pl.DataFrame, fm: models.LiveModels) -> tuple[pl.DataFrame, dict]:
    """Our pass probability (its `wp` input = the fold's own wp model) vs nflfastR's `xpass`
    on the rows that have it (the rest dropped, counted)."""
    kept = test.filter(pl.col("xpass").is_not_null())
    if kept.height:
        wp = fm.wp.predict(models.matrix(kept, schema.WP_FEATURES))
        x = kept.with_columns(pl.Series("wp", wp, dtype=pl.Float64))
        p = fm.pass_.predict(models.matrix(x, schema.PASS_FEATURES))
    else:
        p = np.zeros(0)
    df = kept.select(
        "season", "game_id", "play_id", "down", "ydstogo", "is_pass", "xpass"
    ).with_columns(pl.Series("pred", p, dtype=pl.Float64))
    y, px = df["is_pass"].to_numpy(), df["xpass"].to_numpy()
    m = {
        "pass_n": df.height,
        "pass_dropped_no_xpass": test.height - kept.height,
        "pass_ll": log_loss(p, y),
        "pass_ll_xpass": log_loss(px, y),
        "pass_brier": brier(p, y),
        "pass_brier_xpass": brier(px, y),
        "pass_auc": auc(p, y),
        "pass_auc_xpass": auc(px, y),
    }
    return df, m


def _recv_moments(np_: models.NextPossession) -> tuple[float, float]:
    """Mean and variance of the receiving start (yards to goal), given the receiver has it."""
    w = np_.recv / max(np_.recv.sum(), 1e-12)
    s = np.arange(1, 100)
    mean = float((w * s).sum())
    return mean, float((w * (s - mean) ** 2).sum())


def next_possession_metrics(
    df: pl.DataFrame, dists: list[models.NextPossession], prefix: str
) -> tuple[pl.DataFrame, dict]:
    """Predicted vs actual next possession for kicks `df` (one distribution per row): the
    receiving start's mean and sd (the predicted sd is the rows' mixture), return-touchdown
    and kept-ball rates."""
    moments = [_recv_moments(d) for d in dists]
    out = df.select("season", "game_id", "play_id", "yardline_100", "outcome", "spot").with_columns(
        pl.Series("pred_mean", [m[0] for m in moments], dtype=pl.Float64),
        pl.Series("pred_var", [m[1] for m in moments], dtype=pl.Float64),
        pl.Series("pred_ret_td", [d.ret_td for d in dists], dtype=pl.Float64),
        pl.Series("pred_keep", [float(d.keep.sum()) for d in dists], dtype=pl.Float64),
    )
    return out, next_possession_summary(out, prefix)


def next_possession_summary(out: pl.DataFrame, prefix: str) -> dict[str, float]:
    recv = out.filter(pl.col("outcome") == "recv")
    if recv.height:
        mu = recv["pred_mean"].to_numpy()
        pred_sd = math.sqrt(
            max(float(np.mean(recv["pred_var"].to_numpy() + mu**2) - np.mean(mu) ** 2), 0.0)
        )
        spot = recv["spot"].cast(pl.Float64).to_numpy()
        pred_mean, act_mean, act_sd = float(np.mean(mu)), float(np.mean(spot)), float(np.std(spot))
    else:
        pred_mean = act_mean = pred_sd = act_sd = math.nan
    n = out.height
    return {
        f"{prefix}_n": n,
        f"{prefix}_start_pred": pred_mean,
        f"{prefix}_start_actual": act_mean,
        f"{prefix}_start_sd_pred": pred_sd,
        f"{prefix}_start_sd_actual": act_sd,
        f"{prefix}_ret_td_pred": float(out["pred_ret_td"].mean()) if n else math.nan,
        f"{prefix}_ret_td_actual": float((out["outcome"] == "ret_td").mean()) if n else math.nan,
        f"{prefix}_keep_pred": float(out["pred_keep"].mean()) if n else math.nan,
        f"{prefix}_keep_actual": float((out["outcome"] == "keep").mean()) if n else math.nan,
    }


def score_punt(test: pl.DataFrame, fm: models.LiveModels) -> tuple[pl.DataFrame, dict]:
    return next_possession_metrics(test, [fm.punt.at(int(y)) for y in test["yardline_100"]], "punt")


def score_kickoff(
    test: pl.DataFrame, fm: models.LiveModels, season: int
) -> tuple[pl.DataFrame, dict]:
    """Onside kicks left out, as in `fit_kickoff`; one distribution for the season's era."""
    test = test.filter(~pl.col("onside"))
    d = fm.kickoff.at(season)
    out, m = next_possession_metrics(test, [d] * test.height, "kick")
    return out, m


DECISION_COLUMNS = [
    "season", "game_id", "play_id", "qtr", "score_diff", "game_seconds", "ydstogo",
    "yardline_100", "choice",
]  # fmt: skip


def score_decisions(
    test: pl.DataFrame, fm: models.LiveModels, threads: int = DECISION_THREADS
) -> tuple[pl.DataFrame, dict]:
    """The season's real 4th downs through the engine (no bootstraps): the coach's call, the
    bot's, the gap, the three WPs and the cost of the coach's call (wp[best] - wp[choice];
    null when the coach's option isn't priced, e.g. a field goal beyond 70 yards). States
    that fail the input checks are skipped and counted."""
    keep, states, skipped = [], [], 0
    for i, row in enumerate(test.iter_rows(named=True)):
        try:
            states.append(data.state_of_row(row))
        except (ValueError, TypeError):
            skipped += 1
            continue
        keep.append(i)
    engine = Engine(fm, threads=threads)
    try:
        decisions = engine.fourth_downs(states, bootstrap=False)
    finally:
        engine.close()
    base = test.select(DECISION_COLUMNS)[keep] if keep else test.select(DECISION_COLUMNS).clear()
    choice = base["choice"].to_list()
    wps = {k: [d.wp.get(k) for d in decisions] for k in ("go", "fg", "punt")}
    cost = []
    for d, c in zip(decisions, choice, strict=True):
        v = d.wp.get(c)
        cost.append(None if v is None else d.wp[d.best] - v)
    df = base.with_columns(
        pl.Series("best", [d.best for d in decisions], dtype=pl.String),
        pl.Series("gap", [d.gap for d in decisions], dtype=pl.Float64),
        pl.Series("wp_go", wps["go"], dtype=pl.Float64),
        pl.Series("wp_fg", wps["fg"], dtype=pl.Float64),
        pl.Series("wp_punt", wps["punt"], dtype=pl.Float64),
        pl.Series("wp_now", [d.wp_now for d in decisions], dtype=pl.Float64),
        pl.Series("convert", [d.convert for d in decisions], dtype=pl.Float64),
        pl.Series("cost", cost, dtype=pl.Float64),
    )
    m = decision_metrics(df)
    m["dec_skipped"] = skipped
    return df, m


def decision_metrics(df: pl.DataFrame) -> dict[str, float]:
    """Go / field-goal / punt rates for coaches and the bot (over every scored 4th down: go and
    punt are always priced), agreement, the toss-up share (gap < 1 point) and the WP cost of
    the coaches' calls (total = expected wins lost)."""
    n = df.height
    m: dict[str, float] = {"dec_n": n}
    for opt in ("go", "fg", "punt"):
        m[f"{opt}_rate_coach"] = float((df["choice"] == opt).mean()) if n else math.nan
        m[f"{opt}_rate_bot"] = float((df["best"] == opt).mean()) if n else math.nan
    m["agree"] = float((df["choice"] == df["best"]).mean()) if n else math.nan
    m["toss_up_share"] = float((df["gap"] < TOSS_UP_GAP).mean()) if n else math.nan
    c = df["cost"].drop_nulls()
    m["dec_cost_n"] = c.len()
    m["wp_cost_mean"] = float(c.mean()) if c.len() else math.nan
    m["wp_cost_total"] = float(c.sum()) if c.len() else 0.0
    return m


# --- the ship rules ----------------------------------------------------------------------------
def _seasons_won(by_season: pl.DataFrame, ours: str, theirs: str) -> int:
    """Seasons where our loss is strictly lower (a tie or a missing value is not a win)."""
    a = by_season[ours].cast(pl.Float64).fill_null(math.nan).to_numpy()
    b = by_season[theirs].cast(pl.Float64).fill_null(math.nan).to_numpy()
    return int((np.isfinite(a) & np.isfinite(b) & (a < b)).sum())


def seasons_needed(n: int) -> int:
    """9 of 12; a shorter run (smoke) scales it (and can't ship: `full_backtest` is False)."""
    return SEASONS_NEEDED if n == SEASONS_TOTAL else math.ceil(SEASONS_NEEDED * n / SEASONS_TOTAL)


def ship_verdict(by_season: pl.DataFrame, pooled: dict) -> dict[str, Any]:
    """Rules 1-4 of the pre-registered ship rules (rule 5, the decision tests and the timing
    budget, is checked separately):
    1. wp: pooled ECE <= 0.010 and pooled Brier <= vegas_wp's pooled Brier + 0.002;
    2. gain: conversion log loss below the distance x era baseline in >= 9 of 12 seasons;
    3. fg: log loss below the distance-only logistic in >= 9 of 12 seasons;
    4. pass: log loss below xpass in >= 9 of 12 seasons.
    A season win needs a strictly lower loss. `all_pass` also needs the full 2014-2025 run."""
    n = by_season.height
    need = seasons_needed(n)
    ece_, b, bv = (float(pooled.get(k, math.nan)) for k in ("wp_ece", "wp_brier", "wp_brier_vegas"))
    wp_pass = bool(ece_ <= WP_ECE_MAX and b <= bv + WP_BRIER_MARGIN)
    out: dict[str, Any] = {
        "seasons": n,
        "seasons_needed": need,
        "full_backtest": sorted(by_season["season"].to_list()) == HELD_OUT if n else False,
        "wp_ece": ece_,
        "wp_brier": b,
        "wp_brier_vegas": bv,
        "wp_brier_gap": b - bv,
        "wp_pass": wp_pass,
    }
    for name, ours, theirs in (
        ("gain", "gain_ll", "gain_ll_base"),
        ("fg", "fg_ll", "fg_ll_base"),
        ("pass", "pass_ll", "pass_ll_xpass"),
    ):
        won = _seasons_won(by_season, ours, theirs) if n else 0
        out[f"{name}_seasons_won"] = won
        out[f"{name}_pass"] = bool(n and won >= need)
    out["rules_1_4_pass"] = all(out[f"{k}_pass"] for k in ("wp", "gain", "fg", "pass"))
    out["all_pass"] = bool(out["rules_1_4_pass"] and out["full_backtest"])
    out["rule_5"] = "checked separately: pytest tests/live + the 3rd-down timing budget"
    return out


def ratings_verdict(
    main_by_season: pl.DataFrame,
    ratings_by_season: pl.DataFrame,
    main_pooled: dict,
    ratings_pooled: dict,
) -> dict[str, Any]:
    """The optional gain-ratings variant is kept only if its conversion log loss beats the
    no-ratings model's in >= 9 of 12 seasons AND pooled."""
    j = main_by_season.select("season", pl.col("gain_ll").alias("ll_main")).join(
        ratings_by_season.select("season", pl.col("gain_ll").alias("ll_ratings")),
        on="season",
        how="inner",
    )
    won = _seasons_won(j, "ll_ratings", "ll_main")
    need = seasons_needed(j.height)
    pooled_better = bool(
        ratings_pooled.get("gain_ll", math.inf) < main_pooled.get("gain_ll", -math.inf)
    )
    return {
        "seasons": j.height,
        "seasons_won": won,
        "seasons_needed": need,
        "pooled_ll_main": main_pooled.get("gain_ll"),
        "pooled_ll_ratings": ratings_pooled.get("gain_ll"),
        "pooled_better": pooled_better,
        "keep": bool(j.height and won >= need and pooled_better),
    }


# --- pooled metrics and tables ------------------------------------------------------------------
def pooled_metrics(preds: dict[str, pl.DataFrame]) -> dict[str, float]:
    wp = preds["wp"]
    y, pm, pv = wp["win"].to_numpy(), wp["pred"].to_numpy(), wp["vegas_wp"].to_numpy()
    out: dict[str, float] = {
        "wp_n": wp.height,
        "wp_brier": brier(pm, y),
        "wp_brier_vegas": brier(pv, y),
        "wp_logloss": log_loss(pm, y),
        "wp_logloss_vegas": log_loss(pv, y),
        "wp_ece": ece(pm, y),
        "wp_ece_vegas": ece(pv, y),
    }
    out.update(gain_metrics(preds["gain"]))
    fg = preds["fg"]
    out.update(
        fg_n=fg.height,
        fg_ll=log_loss(fg["pred"].to_numpy(), fg["make"].to_numpy()),
        fg_ll_base=log_loss(fg["base"].to_numpy(), fg["make"].to_numpy()),
    )
    ps = preds["pass"]
    y = ps["is_pass"].to_numpy()
    out.update(
        pass_n=ps.height,
        pass_ll=log_loss(ps["pred"].to_numpy(), y),
        pass_ll_xpass=log_loss(ps["xpass"].to_numpy(), y),
        pass_auc=auc(ps["pred"].to_numpy(), y),
        pass_auc_xpass=auc(ps["xpass"].to_numpy(), y),
    )
    out.update(next_possession_summary(preds["punt"], "punt"))
    out.update(next_possession_summary(preds["kickoff"], "kick"))
    out.update(decision_metrics(preds["decisions"]))
    return out


def wp_calibration(wp: pl.DataFrame, group: str | None = None) -> pl.DataFrame:
    """Reliability rows for the model and `vegas_wp` (20 bins), per `group` when given."""
    parts = []
    groups = (
        [(None, wp)]
        if group is None
        else wp.filter(pl.col(group).is_not_null()).group_by(group, maintain_order=True)
    )
    for key, part in groups:
        for name, col in (("model", "pred"), ("vegas_wp", "vegas_wp")):
            r = reliability(part[col].to_numpy(), part["win"].to_numpy()).with_columns(
                pl.lit(name).alias("predictor")
            )
            if group is not None:
                r = r.with_columns(pl.lit(key[0] if isinstance(key, tuple) else key).alias("group"))
            parts.append(r)
    if not parts:
        return pl.DataFrame()
    cols = (["group"] if group is not None else []) + [
        "predictor",
        "bin",
        "bin_lo",
        "n",
        "mean_pred",
        "mean_outcome",
    ]
    return pl.concat([p.select(cols) for p in parts])


def wp_by_group(wp: pl.DataFrame, group: str, order: list[str]) -> pl.DataFrame:
    """Brier, log loss and ECE of the model and `vegas_wp` per group (same rows)."""
    rows = []
    for g in order:
        part = wp.filter(pl.col(group) == g)
        if not part.height:
            continue
        y, pm, pv = part["win"].to_numpy(), part["pred"].to_numpy(), part["vegas_wp"].to_numpy()
        rows.append(
            {
                "group": g,
                "n": part.height,
                "win_rate": float(np.mean(y)),
                "mean_pred": float(np.mean(pm)),
                "mean_vegas": float(np.mean(pv)),
                "brier": brier(pm, y),
                "brier_vegas": brier(pv, y),
                "logloss": log_loss(pm, y),
                "logloss_vegas": log_loss(pv, y),
                "ece": ece(pm, y),
                "ece_vegas": ece(pv, y),
            }
        )
    return pl.DataFrame(rows)


def with_wp_groups(wp: pl.DataFrame) -> pl.DataFrame:
    return wp.with_columns(
        quarter_expr().alias("quarter"),
        two_minute_expr().alias("two_minute"),
        score_state_expr().alias("score_state"),
    )


def conversion_by_distance(gain: pl.DataFrame, max_distance: int = 15) -> pl.DataFrame:
    """3rd downs and 4th-down attempts, distance 1..`max_distance`: actual conversion rate,
    the model's mean prediction and the baseline's."""
    return (
        gain.filter(pl.col("ydstogo") <= max_distance)
        .with_columns(
            pl.when(pl.col("down") == 3)
            .then(pl.lit("3rd downs"))
            .otherwise(pl.lit("4th-down attempts"))
            .alias("rows")
        )
        .group_by("rows", "ydstogo")
        .agg(
            pl.len().alias("n"),
            pl.col("converted").cast(pl.Float64).mean().alias("actual"),
            pl.col("pred").mean().alias("model"),
            pl.col("base").mean().alias("baseline"),
        )
        .sort("rows", "ydstogo")
    )


def fg_by_distance(fg: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for band in FG_BANDS:
        part = fg.filter(pl.col("band") == band)
        if not part.height:
            continue
        y = part["make"].to_numpy()
        rows.append(
            {
                "band": band,
                "n": part.height,
                "make_rate": float(np.mean(y)),
                "model": float(part["pred"].mean()),
                "baseline": float(part["base"].mean()),
                "ll": log_loss(part["pred"].to_numpy(), y),
                "ll_base": log_loss(part["base"].to_numpy(), y),
            }
        )
    return pl.DataFrame(rows)


DECISION_TABLE_COLUMNS = [
    "season", "dec_n", "dec_skipped", "go_rate_coach", "go_rate_bot", "fg_rate_coach",
    "fg_rate_bot", "punt_rate_coach", "punt_rate_bot", "agree", "toss_up_share", "dec_cost_n",
    "wp_cost_mean", "wp_cost_total",
]  # fmt: skip


def build_tables(
    preds: dict[str, pl.DataFrame], by_season: pl.DataFrame
) -> dict[str, pl.DataFrame]:
    wp = with_wp_groups(preds["wp"])
    quarter_like = wp.with_columns(pl.col("quarter").alias("g"))
    late = wp.filter(pl.col("two_minute").is_not_null()).with_columns(
        pl.col("two_minute").alias("g")
    )
    both = pl.concat([quarter_like, late])
    return {
        "wp/calibration": wp_calibration(wp),
        "wp/calibration_by_quarter": wp_calibration(both, "g"),
        "wp/by_quarter": wp_by_group(both, "g", QUARTERS + TWO_MINUTE),
        "wp/by_score_state": wp_by_group(wp, "score_state", SCORE_STATES),
        "gain/conversion_by_distance": conversion_by_distance(preds["gain"]),
        "fg/by_distance": fg_by_distance(preds["fg"]),
        "decisions/by_season": by_season.select(
            [c for c in DECISION_TABLE_COLUMNS if c in by_season.columns]
        ),
        "decisions/costliest": preds["decisions"]
        .filter(pl.col("cost").is_not_null())
        .sort("cost", descending=True)
        .head(25),
        "backtest/by_season": by_season,
    }


# --- W&B ----------------------------------------------------------------------------------------
def _finite(d: dict) -> dict:
    return {k: v for k, v in d.items() if isinstance(v, int | float) and math.isfinite(float(v))}


CHARTED = {"wp/calibration", "gain/conversion_by_distance"}  # chart + `<name>_table`


def log_tables(run, tables: dict[str, pl.DataFrame], by_season: pl.DataFrame) -> None:
    """The end-of-run tables and charts (names used by the phase file's "what to look for")."""
    import wandb

    out: dict[str, Any] = {}
    for name, df in tables.items():
        key = name if name not in CHARTED else f"{name}_table"
        if df.height:
            out[key] = wandb.Table(dataframe=df.to_pandas())
    cal = tables["wp/calibration"]
    if cal.height:
        m, v = (
            cal.filter(pl.col("predictor") == "model"),
            cal.filter(pl.col("predictor") == "vegas_wp"),
        )
        out["wp/calibration"] = wandb.plot.line_series(
            xs=[m["mean_pred"].to_list(), v["mean_pred"].to_list(), [0.0, 1.0]],
            ys=[m["mean_outcome"].to_list(), v["mean_outcome"].to_list(), [0.0, 1.0]],
            keys=["model", "vegas_wp", "perfect calibration"],
            title="Win probability calibration (held-out seasons pooled, 20 bins)",
            xname="predicted win probability",
        )
    conv = tables["gain/conversion_by_distance"]
    if conv.height:
        xs, ys, keys = [], [], []
        for rows in ("4th-down attempts", "3rd downs"):
            part = conv.filter(pl.col("rows") == rows)
            for col in ("actual", "model", "baseline"):
                xs.append(part["ydstogo"].to_list())
                ys.append(part[col].to_list())
                keys.append(f"{rows}: {col}")
        out["gain/conversion_by_distance"] = wandb.plot.line_series(
            xs=xs, ys=ys, keys=keys, title="Conversion rate by distance", xname="yards to go"
        )
    if by_season.height:
        out["decisions/go_rate_vs_bot"] = wandb.plot.line_series(
            xs=by_season["season"].to_list(),
            ys=[by_season["go_rate_coach"].to_list(), by_season["go_rate_bot"].to_list()],
            keys=["coaches", "bot"],
            title="4th-down go rate: coaches vs the bot",
            xname="season",
        )
        out["decisions/go_rate_vs_bot_table"] = wandb.Table(
            dataframe=by_season.select(
                "season", "go_rate_coach", "go_rate_bot", "agree"
            ).to_pandas()
        )
    run.log(out)


def _wandb_config(seasons, train_seasons, cfg, gain_ratings, smoke, paths) -> dict[str, Any]:
    from nflengine import tracking

    return {
        "model": "live-decision-models",
        "validation": "leave-one-season-out",
        "held_out_seasons": list(seasons),
        "train_seasons": list(train_seasons),
        "settings": cfg,
        "gain_ratings": gain_ratings,
        "smoke": smoke,
        "features": {
            "wp": schema.WP_FEATURES,
            "gain": models.gain_features(gain_ratings),
            "pass": schema.PASS_FEATURES,
        },
        "ece_bins": ECE_BINS,
        "baseline_min_rows": BASELINE_MIN_ROWS,
        "ship_rules": {
            "wp_ece_max": WP_ECE_MAX,
            "wp_brier_margin": WP_BRIER_MARGIN,
            "seasons_needed": SEASONS_NEEDED,
            "seasons_total": SEASONS_TOTAL,
        },
        "dataset_version": tracking.dataset_version(paths),
        "git_commit": tracking.git_commit(),
    }


# --- saving -------------------------------------------------------------------------------------
def _write_parquet(df: pl.DataFrame, path: Path) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    df.write_parquet(tmp)
    os.replace(tmp, path)


def _write_json(obj: dict, path: Path) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=_json_default), encoding="utf-8")
    os.replace(tmp, path)


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    return str(o)


def _clean(v):
    """NaN / inf -> None so summary.json stays valid JSON."""
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def output_dir(paths: DataPaths, label: str) -> Path:
    return paths.runs / "backtests" / "live-decisions" / label


# --- the backtest -------------------------------------------------------------------------------
def run_backtest(
    seasons: Iterable[int] = range(2014, 2026),
    train_seasons: Iterable[int] = range(2010, 2026),
    *,
    gain_ratings: bool = False,
    use_wandb: bool = True,
    launched_by: str | None = None,
    smoke: bool = False,
    save: bool = True,
    log: Callable[[str], None] = print,
    paths: DataPaths | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Leave one season out: fit on the other `train_seasons`, score the held-out one, log it
    to W&B as soon as it's scored (`bt/*` against `bt/season`), then the pooled metrics, the
    tables, the ship verdict and the saved files. `smoke` holds out 2024 and 2025 only.
    `overrides` change model settings (tests)."""
    paths = paths or ensure_data_root()
    held = SMOKE_SEASONS if smoke else sorted(set(int(s) for s in seasons))
    train_seasons = sorted(set(int(s) for s in train_seasons))
    label = "smoke" if smoke else ("ratings" if gain_ratings else "main")
    cfg = models.settings(overrides)
    t0 = time.perf_counter()
    span = train_seasons + held

    log(f"live backtest ({label}): loading plays {min(span)}-{max(span)}")
    frames, tie_counts = load_frames(sorted(set(train_seasons) | set(held)), gain_ratings, paths)
    fourth = frames.pop("fourth")
    log(
        f"  frames ready in {time.perf_counter() - t0:.0f} s: "
        + ", ".join(f"{k} {v.height}" for k, v in frames.items())
    )

    run = None
    if use_wandb:
        from nflengine import tracking

        run = tracking.init_run(
            group="live-decisions",
            job_type="live-backtest",
            config=_wandb_config(held, train_seasons, cfg, gain_ratings, smoke, paths),
            tags=[
                "ld00",
                "loso",
                *(["smoke"] if smoke else []),
                *(["ratings"] if gain_ratings else []),
            ],
            launched_by=launched_by,
            name=f"live-backtest-{label}",
        )
        run.define_metric("bt/season")
        run.define_metric("bt/*", step_metric="bt/season")

    ok = False
    try:
        rows: list[dict[str, Any]] = []
        preds: dict[str, list[pl.DataFrame]] = {
            k: [] for k in ("wp", "gain", "fg", "pass", "punt", "kickoff", "decisions")
        }
        cum = {"gain": 0, "fg": 0, "pass": 0, "cost": 0.0}
        for s in held:
            t_fold = time.perf_counter()
            fit_seasons = [x for x in train_seasons if x != s]
            log(f"fold {s}: fitting on {len(fit_seasons)} seasons")
            fm, rep = models.fit_all(
                frames,
                fit_seasons,
                cfg=cfg,
                n_boot=0,
                gain_ratings=gain_ratings,
                log=lambda m: log("  " + m),
            )
            check_no_leak(frames, s, fit_seasons, rep.rows, fm.meta.get("seasons"))
            t_fit = time.perf_counter() - t_fold

            def test(name: str, season: int = s) -> pl.DataFrame:
                return frames[name].filter(pl.col("season") == season)

            def train(name: str, fit: list[int] = fit_seasons) -> pl.DataFrame:
                return frames[name].filter(pl.col("season").is_in(fit))

            def dec_rows(season: int) -> pl.DataFrame:
                rows = fourth.filter(pl.col("season") == season)
                return rows.clear() if gain_ratings else rows

            row: dict[str, Any] = {"season": s}
            for name, (df, m) in (
                ("wp", score_wp(test("wp"), fm)),
                ("gain", score_gain(test("gain"), train("gain"), fm)),
                ("fg", score_fg(test("fg"), train("fg"), fm)),
                ("pass", score_pass(test("pass"), fm)),
                ("punt", score_punt(test("punt"), fm)),
                ("kickoff", score_kickoff(test("kickoff"), fm, s)),
                # the ratings variant is a gain-model study: a live GameState carries no
                # ratings, so the engine can't price its decisions (no rows, NaN metrics)
                ("decisions", score_decisions(dec_rows(s), fm)),
            ):
                preds[name].append(df)
                row.update(m)
            row["fit_seconds"] = round(t_fit, 1)
            row["fold_seconds"] = round(time.perf_counter() - t_fold, 1)
            cum["gain"] += int(row["gain_ll"] < row["gain_ll_base"])
            cum["fg"] += int(row["fg_ll"] < row["fg_ll_base"])
            cum["pass"] += int(row["pass_ll"] < row["pass_ll_xpass"])
            cum["cost"] += row["wp_cost_total"]
            rows.append(row)
            log(
                f"fold {s} done in {row['fold_seconds']} s (fit {row['fit_seconds']} s): "
                f"wp brier {row['wp_brier']:.4f} vs vegas {row['wp_brier_vegas']:.4f}, "
                f"ece {row['wp_ece']:.4f}; "
                f"gain ll {row['gain_ll']:.4f} vs {row['gain_ll_base']:.4f}; "
                f"fg ll {row['fg_ll']:.4f} vs {row['fg_ll_base']:.4f}; "
                f"pass ll {row['pass_ll']:.4f} vs xpass {row['pass_ll_xpass']:.4f}; "
                f"go rate coach {row['go_rate_coach']:.3f} bot {row['go_rate_bot']:.3f}"
            )
            if run is not None:
                point = {f"bt/{k}": v for k, v in _finite(row).items() if k != "season"}
                point.update(
                    {
                        "bt/season": s,
                        "bt/gain_wins_cum": cum["gain"],
                        "bt/fg_wins_cum": cum["fg"],
                        "bt/pass_wins_cum": cum["pass"],
                        "bt/wp_cost_cum": cum["cost"],
                    }
                )
                run.log(point)

        by_season = pl.DataFrame(rows)
        all_preds = {k: pl.concat(v, how="vertical_relaxed") for k, v in preds.items()}
        pooled = pooled_metrics(all_preds)
        ship = ship_verdict(by_season, pooled)
        tables = build_tables(all_preds, by_season)
        summary: dict[str, Any] = {
            "label": label,
            "held_out_seasons": held,
            "train_seasons": train_seasons,
            "gain_ratings": gain_ratings,
            "smoke": smoke,
            "wp_tie_counts": tie_counts,
            "pooled": pooled,
            "by_season": by_season.to_dicts(),
            "ship": ship,
            "tables": {k: v.to_dicts() for k, v in tables.items() if k != "backtest/by_season"},
            "seconds": round(time.perf_counter() - t0, 1),
            "settings": cfg,
        }
        if gain_ratings and save:
            main = output_dir(paths, "main") / "summary.json"
            if main.exists():
                ms = json.loads(main.read_text(encoding="utf-8"))
                if sorted(ms.get("held_out_seasons", [])) == held:
                    summary["ratings_verdict"] = ratings_verdict(
                        pl.DataFrame(ms["by_season"]), by_season, ms["pooled"], pooled
                    )
        log(
            f"pooled: wp brier {pooled['wp_brier']:.4f} vs vegas {pooled['wp_brier_vegas']:.4f}, "
            f"ece {pooled['wp_ece']:.4f}; seasons won gain {ship['gain_seasons_won']}, fg "
            f"{ship['fg_seasons_won']}, pass {ship['pass_seasons_won']} of {ship['seasons']} "
            f"(need {ship['seasons_needed']}); rules 1-4 "
            f"{'pass' if ship['rules_1_4_pass'] else 'MISS'}"
        )
        if run is not None:
            log_tables(run, tables, by_season)
            run.summary.update({f"pooled/{k}": v for k, v in _finite(pooled).items()})
            run.summary.update({f"ship/{k}": v for k, v in ship.items()})
            if "ratings_verdict" in summary:
                run.summary.update(
                    {f"ship/ratings_{k}": v for k, v in summary["ratings_verdict"].items()}
                )
            summary["wandb"] = {"id": run.id, "url": getattr(run, "url", None)}
        if save:
            out = output_dir(paths, label)
            out.mkdir(parents=True, exist_ok=True)
            for name, df in all_preds.items():
                fname = (
                    "decisions.parquet" if name == "decisions" else f"predictions_{name}.parquet"
                )
                _write_parquet(df, out / fname)
            _write_json(_clean(summary), out / "summary.json")
            summary["folder"] = str(out)
            log(f"saved to {out}")
        ok = True
        return summary
    finally:
        if run is not None:
            run.finish(exit_code=0 if ok else 1)
