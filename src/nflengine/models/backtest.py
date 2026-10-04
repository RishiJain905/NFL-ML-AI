"""Walk-forward backtest harness (documentation/04 -> Leakage rules, Training and retraining
cadence; plan P03). Reused by the game model (P03), the player models (P06) and v2 (P08).

For every as-of key (season, week), in time order:
1. **train** = rows from weeks strictly before the key (`asof.before_expr`) that have a label;
2. **weights** = current-season sample weights (D28): rows of the key's season get
   `current_season`, the season before `last_season`, older seasons `older`;
3. **test** = the rows of the key's week;
4. `model(train, weights, test, history)` returns predictions for the test rows. `history`
   holds the predictions this run already made for earlier keys (every one of them is for
   a week before the key, so their outcomes were known by then). Models use it for
   anything fitted on walk-forward output: the margin sigma, a calibration layer.

The harness checks the boundary itself (train strictly before the key, test exactly the
key's week, history before the key) and raises `LeakageError` otherwise, so a model can't
quietly see the future. `on_week(key, predictions)` is called after each week for live
logging (W&B curves fill in while the backtest runs).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any, Protocol

import numpy as np
import polars as pl

from nflengine.features.asof import AsOf, before_expr
from nflengine.features.leakage import LeakageError


@dataclass(frozen=True)
class SampleWeights:
    """Current-season sample weights for weekly refits (D28)."""

    current_season: float = 3.0
    last_season: float = 1.5
    older: float = 1.0

    def __post_init__(self) -> None:
        for f in fields(self):
            if getattr(self, f.name) <= 0:
                raise ValueError(f"{f.name} must be > 0")

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any] | None = None, **overrides: Any) -> SampleWeights:
        values = dict(cfg or {})
        known = {f.name for f in fields(cls)}
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"unknown sample-weight settings: {sorted(unknown)}")
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**{k: float(v) for k, v in values.items()})

    @classmethod
    def for_current(cls, current_season: float) -> SampleWeights:
        """Sweep helper: vary the current-season weight; last season keeps 1.5x but never
        outweighs the current season (so 1x means every row weighs the same)."""
        return cls(current_season, min(1.5, current_season), 1.0)

    def for_rows(self, seasons: np.ndarray | pl.Series, season: int) -> np.ndarray:
        s = np.asarray(seasons)
        return np.where(
            s == season,
            self.current_season,
            np.where(s == season - 1, self.last_season, self.older),
        ).astype(float)

    def as_dict(self) -> dict[str, float]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


class WeekModel(Protocol):
    def __call__(
        self, train: pl.DataFrame, weights: np.ndarray, test: pl.DataFrame, history: pl.DataFrame
    ) -> pl.DataFrame: ...


def week_keys(frame: pl.DataFrame, seasons: Sequence[int] | range | None = None) -> list[AsOf]:
    """Distinct (season, week) keys of `frame` (optionally only `seasons`), in time order."""
    f = frame if seasons is None else frame.filter(pl.col("season").is_in(list(seasons)))
    return [
        AsOf(int(s), int(w))
        for s, w in f.select("season", "week").unique().sort("season", "week").iter_rows()
    ]


def _check_boundary(key: AsOf, train: pl.DataFrame, test: pl.DataFrame, history: pl.DataFrame):
    if train.height and train.filter(~before_expr(key)).height:
        raise LeakageError(f"training rows on/after {key}")
    if history.height and history.filter(~before_expr(key)).height:
        raise LeakageError(f"history rows on/after {key}")
    wrong = test.filter((pl.col("season") != key.season) | (pl.col("week") != key.week))
    if wrong.height:
        raise LeakageError(f"test rows outside {key}")


def walk_forward(
    frame: pl.DataFrame,
    keys: Sequence[AsOf],
    model: WeekModel,
    *,
    label: str | Sequence[str],
    weights: SampleWeights | None = None,
    min_train_rows: int = 1,
    on_week: Callable[[AsOf, pl.DataFrame], None] | None = None,
    history: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Predict every key's week with a model trained only on earlier weeks.

    `frame` has one row per prediction unit with `season`, `week` and the `label`
    column(s); rows with a null label (games not played yet) are never trained on but are
    still predicted when they fall in a key's week. Keys whose week has no rows, or whose
    training set has fewer than `min_train_rows` rows, are skipped. Returns the
    concatenated predictions (whatever columns the model returns) in key order.

    `history` seeds the walk-forward history with earlier predictions of the same model
    (P06: a weekly refit continues from the saved backtest); it must lie before every key.
    """
    labels = [label] if isinstance(label, str) else list(label)
    weights = weights or SampleWeights()
    has_label = pl.all_horizontal(pl.col(c).is_not_null() for c in labels)
    frame = frame.sort("season", "week")
    preds: list[pl.DataFrame] = []
    history = history if history is not None else pl.DataFrame()
    for key in sorted(keys):
        test = frame.filter((pl.col("season") == key.season) & (pl.col("week") == key.week))
        if test.is_empty():
            continue
        train = frame.filter(before_expr(key) & has_label)
        if train.height < min_train_rows:
            continue
        w = weights.for_rows(train["season"], key.season)
        _check_boundary(key, train, test, history)
        out = model(train, w, test, history)
        if out.height != test.height:
            raise ValueError(f"model returned {out.height} rows for {test.height} test rows")
        preds.append(out)
        history = pl.concat([history, out], how="diagonal_relaxed") if history.height else out
        if on_week is not None:
            on_week(key, out)
    return pl.concat(preds, how="diagonal_relaxed") if preds else pl.DataFrame()
