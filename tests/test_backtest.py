"""Walk-forward harness: no leakage, correct week ordering, sample weights (plan P03)."""

import numpy as np
import polars as pl
import pytest

from nflengine.features.asof import AsOf
from nflengine.features.leakage import LeakageError
from nflengine.models import backtest as B


def tiny_frame() -> pl.DataFrame:
    rows = []
    for season in (2020, 2021):
        for week in (1, 2, 3):
            for g in range(2):
                played = not (season == 2021 and week == 3 and g == 1)
                rows.append(
                    {
                        "season": season,
                        "week": week,
                        "game_id": f"{season}_{week}_{g}",
                        "y": float(season * 10 + week) if played else None,
                    }
                )
    return pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, train, weights, test, history):
        self.calls.append((train, weights, test, history))
        return test.select("season", "week", "game_id", "y").with_columns(
            pl.lit(float(train["y"].mean())).alias("pred")
        )


def test_walk_forward_trains_only_on_earlier_weeks_in_order() -> None:
    f = tiny_frame()
    rec = Recorder()
    seen = []
    keys = [AsOf(2021, 3), AsOf(2020, 2), AsOf(2021, 1), AsOf(2020, 3), AsOf(2021, 2)]
    out = B.walk_forward(f, keys, rec, label="y", on_week=lambda k, p: seen.append(k))
    assert seen == sorted(keys)  # time order regardless of input order
    for (train, weights, test, history), key in zip(rec.calls, seen, strict=True):
        assert (train["season"] * 100 + train["week"]).max() < key.season * 100 + key.week
        assert train["y"].null_count() == 0  # unplayed games are never trained on
        assert set(test["week"].to_list()) == {key.week}
        assert set(test["season"].to_list()) == {key.season}
        if history.height:
            assert (history["season"] * 100 + history["week"]).max() < key.season * 100 + key.week
        assert len(weights) == train.height
    # the 2021 week-3 test includes the unplayed game (predicted, never trained on)
    last = out.filter((pl.col("season") == 2021) & (pl.col("week") == 3))
    assert last.height == 2 and last["y"].null_count() == 1
    assert out.height == 10


def test_sample_weights_by_season() -> None:
    w = B.SampleWeights(3.0, 1.5, 1.0)
    got = w.for_rows(np.array([2019, 2020, 2021, 2021]), 2021)
    assert got.tolist() == [1.0, 1.5, 3.0, 3.0]
    assert B.SampleWeights.for_current(1.0).as_dict() == {
        "current_season": 1.0,
        "last_season": 1.0,
        "older": 1.0,
    }
    assert B.SampleWeights.for_current(5.0).last_season == 1.5
    with pytest.raises(ValueError):
        B.SampleWeights(0.0, 1.0, 1.0)
    with pytest.raises(ValueError):
        B.SampleWeights.from_config({"currrent_season": 2})
    assert B.SampleWeights.from_config({"current_season": 2}, older=0.5).older == 0.5


def test_harness_passes_current_season_weights() -> None:
    rec = Recorder()
    B.walk_forward(
        tiny_frame(), [AsOf(2021, 3)], rec, label="y", weights=B.SampleWeights(4.0, 2.0, 1.0)
    )
    train, weights, _, _ = rec.calls[0]
    expect = np.where(train["season"].to_numpy() == 2021, 4.0, 2.0)
    assert weights.tolist() == expect.tolist()


def test_min_train_rows_and_empty_weeks_skip() -> None:
    rec = Recorder()
    out = B.walk_forward(tiny_frame(), [AsOf(2020, 1), AsOf(2020, 9)], rec, label="y")
    assert out.is_empty() and not rec.calls  # week 1 of the first season has no training rows
    out = B.walk_forward(tiny_frame(), [AsOf(2020, 3)], rec, label="y", min_train_rows=5)
    assert out.is_empty()


def test_boundary_checks_raise() -> None:
    f = tiny_frame()
    key = AsOf(2020, 2)
    with pytest.raises(LeakageError):
        B._check_boundary(key, f, f.filter(pl.col("week") == 2), pl.DataFrame())
    with pytest.raises(LeakageError):
        B._check_boundary(key, f.head(0), f.filter(pl.col("week") == 3), pl.DataFrame())
    with pytest.raises(LeakageError):
        B._check_boundary(key, f.head(0), f.filter(pl.col("week") == 2).head(0), f)


def test_model_must_return_one_row_per_test_row() -> None:
    def bad(train, weights, test, history):
        return test.head(1)

    with pytest.raises(ValueError):
        B.walk_forward(tiny_frame(), [AsOf(2021, 1)], bad, label="y")


def test_week_keys() -> None:
    keys = B.week_keys(tiny_frame(), [2021])
    assert keys == [AsOf(2021, 1), AsOf(2021, 2), AsOf(2021, 3)]
