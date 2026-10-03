"""Metric helpers for probability, margin and points predictions (documentation/04 -> B,
documentation/11 -> Game and team targets, documentation/08 -> What each run logs).

Conventions (the same as `elo.elo_metrics` from P02):
- Outcomes are 1 (home win), 0 (home loss) or 0.5 (tie). A tie counts as 0.5 in the Brier
  score, log loss and calibration, and is left out of accuracy.
- Probabilities are clipped to [1e-6, 1 - 1e-6] for the log loss only.
- Accuracy picks the home team when p > 0.5 (p == 0.5 counts as an away pick).
- ECE uses equal-width bins over [0, 1]: sum over bins of (bin share) x |mean p - mean y|.

Every function takes array-likes (numpy, Polars Series or lists) and ignores rows where the
prediction or the outcome is missing (NaN / null).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import polars as pl

PROB_EPS = 1e-6
N_BINS = 10


def _arr(x) -> np.ndarray:
    if isinstance(x, pl.Series):
        return x.cast(pl.Float64).to_numpy()
    return np.asarray(x, dtype=float)


def _pair(pred, actual) -> tuple[np.ndarray, np.ndarray]:
    p, y = _arr(pred), _arr(actual)
    if p.shape != y.shape:
        raise ValueError(f"shape mismatch: {p.shape} vs {y.shape}")
    ok = ~(np.isnan(p) | np.isnan(y))
    return p[ok], y[ok]


def brier(prob, outcome) -> float:
    p, y = _pair(prob, outcome)
    return float(np.mean((p - y) ** 2)) if p.size else float("nan")


def log_loss(prob, outcome) -> float:
    p, y = _pair(prob, outcome)
    if not p.size:
        return float("nan")
    pc = np.clip(p, PROB_EPS, 1 - PROB_EPS)
    return float(np.mean(-(y * np.log(pc) + (1 - y) * np.log(1 - pc))))


def accuracy(prob, outcome) -> float:
    p, y = _pair(prob, outcome)
    decisive = y != 0.5
    if not decisive.any():
        return float("nan")
    return float(np.mean((p[decisive] > 0.5) == (y[decisive] == 1.0)))


def reliability(prob, outcome, n_bins: int = N_BINS) -> pl.DataFrame:
    """Reliability-diagram table: one row per non-empty equal-width bin.

    Columns: bin, lo, hi, n, mean_prob, mean_outcome.
    """
    p, y = _pair(prob, outcome)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        m = idx == b
        if m.any():
            rows.append((b, edges[b], edges[b + 1], int(m.sum()), p[m].mean(), y[m].mean()))
    schema = {
        "bin": pl.Int32,
        "lo": pl.Float64,
        "hi": pl.Float64,
        "n": pl.Int64,
        "mean_prob": pl.Float64,
        "mean_outcome": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema, orient="row")


def ece(prob, outcome, n_bins: int = N_BINS) -> float:
    """Expected calibration error with equal-width bins."""
    t = reliability(prob, outcome, n_bins)
    if t.is_empty():
        return float("nan")
    w = t["n"].to_numpy() / t["n"].sum()
    gap = np.abs(t["mean_prob"].to_numpy() - t["mean_outcome"].to_numpy())
    return float(np.sum(w * gap))


def mae(pred, actual) -> float:
    p, y = _pair(pred, actual)
    return float(np.mean(np.abs(p - y))) if p.size else float("nan")


def rmse(pred, actual) -> float:
    p, y = _pair(pred, actual)
    return float(np.sqrt(np.mean((p - y) ** 2))) if p.size else float("nan")


def prob_scores(prob, outcome, prefix: str = "") -> dict[str, float]:
    """Brier, log loss, accuracy and ECE in one dict (keys prefixed with `prefix`)."""
    return {
        f"{prefix}brier": brier(prob, outcome),
        f"{prefix}log_loss": log_loss(prob, outcome),
        f"{prefix}accuracy": accuracy(prob, outcome),
        f"{prefix}ece": ece(prob, outcome),
    }


def score_frame(
    df: pl.DataFrame,
    probs: Mapping[str, str],
    outcome: str = "home_win",
    amounts: Mapping[str, tuple[str, str]] | None = None,
) -> dict[str, float]:
    """Scores for several predictors on the same rows.

    `probs` maps a name to a probability column (`{"model": "home_win_prob"}` gives
    `brier_model`, `log_loss_model`, `accuracy_model`, `ece_model`). `amounts` maps a name
    to (prediction column, actual column) for MAE (`mae_<name>`). Adds `games`.
    """
    out: dict[str, float] = {"games": float(df.height)}
    y = df[outcome]
    for name, col in probs.items():
        for k, v in prob_scores(df[col], y).items():
            out[f"{k}_{name}"] = v
    for name, (pred, actual) in (amounts or {}).items():
        out[f"mae_{name}"] = mae(df[pred], df[actual])
    return out


def by_group(
    df: pl.DataFrame,
    by: str | Sequence[str],
    probs: Mapping[str, str],
    outcome: str = "home_win",
    amounts: Mapping[str, tuple[str, str]] | None = None,
) -> pl.DataFrame:
    """`score_frame` per group (e.g. per season), sorted by the group keys."""
    keys = [by] if isinstance(by, str) else list(by)
    rows = []
    for key, part in df.group_by(keys, maintain_order=True):
        rows.append(
            {**dict(zip(keys, key, strict=True)), **score_frame(part, probs, outcome, amounts)}
        )
    return pl.DataFrame(rows).sort(keys) if rows else pl.DataFrame()
