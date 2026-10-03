import math

import numpy as np
import polars as pl
import pytest

from nflengine.models import metrics as M


def test_brier_and_log_loss_known_values() -> None:
    p, y = [0.8, 0.3, 0.5], [1.0, 0.0, 0.5]
    assert M.brier(p, y) == pytest.approx((0.04 + 0.09 + 0.0) / 3)
    expected = -(math.log(0.8) + math.log(0.7) + math.log(0.5)) / 3
    assert M.log_loss(p, y) == pytest.approx(expected)


def test_accuracy_skips_ties_and_picks_home_above_half() -> None:
    assert M.accuracy([0.6, 0.4, 0.9, 0.5], [1.0, 1.0, 0.5, 0.0]) == pytest.approx(2 / 3)


def test_missing_values_are_ignored() -> None:
    p = pl.Series([0.7, None, 0.2], dtype=pl.Float64)
    y = pl.Series([1.0, 1.0, None], dtype=pl.Float64)
    assert M.brier(p, y) == pytest.approx(0.09)
    assert math.isnan(M.brier([], []))


def test_log_loss_clips_certain_wrong_calls() -> None:
    assert math.isfinite(M.log_loss([1.0], [0.0]))


def test_ece_near_zero_when_calibrated_and_large_when_not() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 200_000)
    y = (rng.uniform(0, 1, p.size) < p).astype(float)
    assert M.ece(p, y) < 0.01
    assert M.ece(np.clip(p * 1.6 - 0.3, 0, 1), y) > 0.05


def test_reliability_bins() -> None:
    t = M.reliability([0.05, 0.07, 0.95, 1.0], [0.0, 1.0, 1.0, 1.0], n_bins=10)
    assert t["n"].to_list() == [2, 2]
    assert t["bin"].to_list() == [0, 9]
    assert t["mean_outcome"].to_list() == [0.5, 1.0]


def test_score_frame_and_by_group() -> None:
    df = pl.DataFrame(
        {
            "season": [1, 1, 2, 2],
            "p": [0.9, 0.2, 0.6, 0.6],
            "q": [0.5, 0.5, 0.5, 0.5],
            "home_win": [1.0, 0.0, 0.0, 1.0],
            "m": [3.0, -1.0, 2.0, 0.0],
            "margin": [7.0, -3.0, -1.0, 4.0],
        }
    )
    s = M.score_frame(df, {"model": "p", "coin": "q"}, amounts={"margin": ("m", "margin")})
    assert s["games"] == 4
    assert s["brier_coin"] == pytest.approx(0.25)
    assert s["mae_margin"] == pytest.approx((4 + 2 + 3 + 4) / 4)
    g = M.by_group(df, "season", {"model": "p"})
    assert g["season"].to_list() == [1, 2]
    assert g["brier_model"][1] == pytest.approx((0.36 + 0.16) / 2)


def test_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        M.brier([0.5, 0.5], [1.0])
