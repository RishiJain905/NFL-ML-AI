import numpy as np
import polars as pl
import pytest

from nflengine.models.ratings_eval import (
    add_baselines,
    add_prediction,
    bootstrap_delta_r2,
    by_group,
    pooled_r2,
    score,
    walk_forward_r2,
)
from nflengine.models.ratings_runs import grid_from_options, grid_size, trend_decision

I32 = pl.Int32


def _targets() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["g1", "g2", "g3"],
            "season": [2021, 2021, 2021],
            "week": [1, 2, 2],
            "game_type": ["REG"] * 3,
            "home_team": ["KC", "BUF", "KC"],
            "away_team": ["BUF", "KC", "NE"],
            "neutral_site": [False, False, True],
            "result": [3.0, -7.0, 10.0],
            "epa_margin": [0.10, -0.05, 0.20],
        }
    ).with_columns(pl.col("season").cast(I32), pl.col("week").cast(I32))


def test_prediction_uses_home_field_except_at_neutral_sites() -> None:
    net = pl.DataFrame(
        {
            "season": [2021] * 6,
            "week": [1, 1, 1, 2, 2, 2],
            "team": ["KC", "BUF", "NE"] * 2,
            "net": [0.1, 0.0, -0.1, 0.2, 0.05, -0.1],
            "hfa": [0.03] * 6,
        }
    ).with_columns(pl.col("season").cast(I32), pl.col("week").cast(I32))
    out = add_prediction(_targets(), net)
    assert out["pred"].to_list() == pytest.approx([0.13, 0.05 - 0.2 + 0.03, 0.3])


def test_baselines_use_only_earlier_games() -> None:
    # Team-game offense sums: (season, week, game, off, def, weighted epa sum, weight).
    tg = pl.DataFrame(
        {
            "season": [2020, 2020, 2021, 2021, 2021, 2021],
            "week": [1, 1, 1, 1, 2, 2],
            "game_id": ["a", "a", "g1", "g1", "g2", "g2"],
            "off": ["KC", "BUF", "KC", "BUF", "BUF", "KC"],
            "def": ["BUF", "KC", "BUF", "KC", "KC", "BUF"],
            "epa_w": [20.0, -10.0, 6.0, 0.0, 3.0, -3.0],
            "w": [100.0, 100.0, 60.0, 60.0, 60.0, 60.0],
        }
    ).with_columns(pl.col("season").cast(I32), pl.col("week").cast(I32))
    t = (
        _targets()
        .filter(pl.col("game_id") != "g3")
        .with_columns(pl.Series("neutral_site", [False, False]))
    )
    # last season's home edge: 2020 had no home games in `t`, so it falls back to 0.
    out = add_baselines(t, tg).sort("game_id")
    # 2020 raw net: KC 0.2 - (-0.1) = 0.3, BUF -0.3. Week 1 of 2021 has no 2021 games yet.
    g1 = out.row(0, named=True)
    assert g1["base_last_season"] == pytest.approx(0.6)
    assert g1["base_to_date"] == pytest.approx(0.6)
    # Week 2: 2021 week-1 game only. KC off 0.1, def allowed 0.0 -> +0.1; BUF -0.1.
    g2 = out.row(1, named=True)
    assert g2["base_to_date"] == pytest.approx(-0.1 - 0.1)
    assert g2["base_last_season"] == pytest.approx(-0.3 - 0.3)


def test_score_and_groups() -> None:
    df = _targets().with_columns(pl.Series("pred", [0.1, 0.0, 0.1]))
    s = score(df, ["pred"])
    assert s["games"] == 3
    assert s["mse_pred"] == pytest.approx((0 + 0.05**2 + 0.1**2) / 3)
    assert s["mse_pred_w1_3"] == pytest.approx(s["mse_pred"])
    g = by_group(df, ["pred"], "week")
    assert g["games"].to_list() == [1, 2]


def _wf_frame(seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    n = 600
    x, z = rng.normal(size=n), rng.normal(size=n)
    return pl.DataFrame(
        {
            "season": np.repeat(np.arange(2010, 2016), n // 6).astype(np.int32),
            "week": np.tile(np.arange(4, 104), 6).astype(np.int32),
            "team": [f"T{i % 10}" for i in range(n)],  # 10 teams -> 30 test clusters
            "x": x,
            "z": z,
            "junk": rng.normal(size=n),
            "y": 2 * x + 0.5 * z + rng.normal(scale=0.5, size=n),
        }
    )


def test_walk_forward_r2_and_bootstrap() -> None:
    models = {"base": ["x"], "junk": ["x", "junk"], "signal": ["x", "z"]}
    per, pooled = walk_forward_r2(_wf_frame(), "y", models, [2013, 2014, 2015])
    assert per["season"].to_list() == [2013, 2014, 2015]  # 3 earlier seasons needed
    assert pooled_r2(pooled, "y", "base") > 0.8
    # A useless extra feature adds ~nothing; the interval brackets the estimate.
    junk = pooled_r2(pooled, "y", "junk") - pooled_r2(pooled, "y", "base")
    lo, hi = bootstrap_delta_r2(pooled, "y", "base", "junk", n_boot=300)
    assert np.isfinite([lo, hi]).all() and lo < hi
    assert lo <= junk <= hi and abs(junk) < 0.01
    # A real extra signal: clearly positive, interval above zero.
    sig = pooled_r2(pooled, "y", "signal") - pooled_r2(pooled, "y", "base")
    lo, hi = bootstrap_delta_r2(pooled, "y", "base", "signal", n_boot=300)
    assert sig > 0.03 and 0 < lo <= sig <= hi


def test_bootstrap_ignores_global_categorical_codes() -> None:
    # Other categoricals alive in the process must not shift the cluster ids.
    other = pl.Series(["zz-a", "zz-b", "zz-c"], dtype=pl.Categorical)
    _, pooled = walk_forward_r2(_wf_frame(1), "y", {"base": ["x"], "full": ["x", "z"]}, [2015])
    lo, hi = bootstrap_delta_r2(pooled, "y", "base", "full", n_boot=200)
    assert np.isfinite([lo, hi]).all() and lo < hi
    assert other.len() == 3


def test_trend_decision_rules() -> None:
    assert trend_decision(0.01, 0.002, 0.8) == "predictive"
    assert trend_decision(0.01, -0.001, 0.8) == "descriptive"  # interval includes 0
    assert trend_decision(0.002, 0.001, 0.9) == "descriptive"  # too small to matter
    assert trend_decision(0.02, 0.01, 0.5) == "descriptive"  # not consistent by season


def test_grid_options() -> None:
    g = grid_from_options(
        half_life_weeks="4,8", prior_regression=None, ridge_alpha="100", qb_change_regression=None
    )
    assert g["half_life_weeks"] == [4.0, 8.0] and g["ridge_alpha"] == [100.0]
    assert "qb_change_regression" not in g  # not swept unless asked
    assert grid_size(g) == 2 * len(g["prior_regression"])
