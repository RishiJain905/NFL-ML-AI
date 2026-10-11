"""PC02 on the real 2025 data (read only: nothing is written, nothing goes to W&B).

The forecast's design is built from PC00's `team_game_tendencies`; these checks tie it to PC00's
published `team_tendencies` (the pages' numbers), then run the as-of rule on real kickoffs.
Run with `uv run pytest -m integration tests/playcalling/test_playcall_forecast_real.py`.
"""

from __future__ import annotations

import numpy as np
import playcall_forecast_fixtures as X
import polars as pl
import pytest
from playcall_forecast_fixtures import P_SCRIPT

import nflengine.playcalling.forecast as F
import nflengine.playcalling.forecast_runs as fr
from nflengine.paths import ensure_data_root

pytestmark = pytest.mark.integration

SEASON = 2025
AS_OF = (2, 9, 18)
CURVES = {SEASON: X.flat_curve(SEASON), SEASON - 1: X.flat_curve(SEASON - 1)}


@pytest.fixture(scope="module")
def real():
    paths = ensure_data_root(create_dirs=False)
    inp = fr.load_inputs(paths, [SEASON])
    tend = pl.scan_parquet(paths.playcalling / str(SEASON) / "team_tendencies.parquet")
    return {"paths": paths, "inp": inp, "tend": tend}


def design(real, key: str, **kw) -> F.Design:
    inp = real["inp"]
    t = F.get_target(key)
    return F.build_design(
        t, F.observations(inp.tg, t), inp.games, CURVES, inp.changed, inp.midseason, [SEASON], **kw
    )


def pc00_sums(real, t: F.Target, window: str, as_of: int) -> pl.DataFrame:
    """(team, k, n) from PC00's published rows: k = value * n summed over the target's metrics."""
    rows = (
        real["tend"]
        .filter(
            (pl.col("window") == window)
            & (pl.col("as_of_week") == as_of)
            & (pl.col("side") == t.side)
            & (pl.col("situation") == t.situation)
            & pl.col("metric").is_in(list(t.metrics))
        )
        .select("team", "metric", "n", "value")
        .collect()
    )
    return rows.group_by("team").agg(
        (pl.col("value").fill_null(0.0) * pl.col("n")).sum().alias("k"),
        pl.col("n").max().alias("n"),
    )


@pytest.mark.parametrize(
    "key",
    [
        "blitz_rate",
        "dropback_rate",
        "proe",
        "deep_shot_rate",
        "run_outside",
        "motion_rate",
        "rushers_avg",
    ],
)
def test_the_designs_season_sums_are_pc00s_season_window(real, key) -> None:
    """K0 / N0 at every (team, as-of week) equal PC00's `season` window n and value * n: the
    same games, the same FTN availability rule (D123), the same plays."""
    t = F.get_target(key)
    d = design(real, key)
    for w in AS_OF:
        g = d.grid.filter(pl.col("as_of_week") == w)
        assert g.height >= 28, (key, w)  # every team with a game (or an opponent's game) that week
        pc = g.join(pc00_sums(real, t, "season", w), on="team", how="left")
        gi = pc["gi"].to_numpy()
        n_pc = pc["n"].fill_null(0).to_numpy().astype(float)
        k_pc = pc["k"].fill_null(0.0).to_numpy()
        np.testing.assert_array_equal(d.a["N0"][gi], n_pc, err_msg=f"{key} N0 as of {w}")
        np.testing.assert_allclose(d.a["K0"][gi], k_pc, atol=1e-6, err_msg=f"{key} K0 as of {w}")
        zero = set(g["team"].to_numpy()[d.a["N0"][gi] == 0])
        if t.source == "ftn" and w == 2:
            # Monday night's charting arrives after the Tuesday cutoff: those teams have nothing
            monday = real["inp"].games.filter(
                (pl.col("season") == SEASON) & (pl.col("week") == 1)
                & (pl.col("kickoff_utc").dt.convert_time_zone("America/New_York").dt.weekday() == 1)
            )  # fmt: skip
            assert monday.height >= 1
            assert zero == set(monday["home_team"]) | set(monday["away_team"]), key
        else:
            assert not zero, (key, w, zero)


def grid_row(d: F.Design, team: str, as_of: int) -> int:
    return X.grid_of(d, SEASON, team, as_of)


def test_the_weighted_sums_equal_the_plain_ones_except_around_a_firing(real) -> None:
    d = design(real, "blitz_rate")
    mid = {r["team"]: r["change_week"] for r in real["inp"].midseason.filter(
        pl.col("season") == SEASON).iter_rows(named=True)}  # fmt: skip
    assert mid == {"TEN": 7, "NYG": 11}  # config/head_coach_fixes.csv: the 2025 interims
    for w in AS_OF:
        for r in d.grid.filter(pl.col("as_of_week") == w).iter_rows(named=True):
            gi, team = r["gi"], r["team"]
            weighted = team in mid and mid[team] <= w
            if weighted:
                assert d.a["N"][gi] < d.a["N0"][gi], (team, w)
            else:
                assert d.a["N"][gi] == d.a["N0"][gi] and d.a["K"][gi] == d.a["K0"][gi], (team, w)
    # NYG: the coach was fired after week 10 (change week 11): as of 10 nothing, as of 12 half
    assert d.a["N"][grid_row(d, "NYG", 10)] == d.a["N0"][grid_row(d, "NYG", 10)]
    n12, n012 = d.a["N"][grid_row(d, "NYG", 12)], d.a["N0"][grid_row(d, "NYG", 12)]
    pre = d.a["N0"][grid_row(d, "NYG", 11)]  # the plays of weeks 1-10
    assert n12 == pytest.approx(0.5 * pre + (n012 - pre))


@pytest.mark.parametrize("key", ["blitz_rate", "dropback_rate", "deep_shot_rate"])
def test_b2_in_week_one_is_pc00s_last_season_window(real, key) -> None:
    t = F.get_target(key)
    d = design(real, key)
    wk1 = d.rows.with_row_index("i").filter(pl.col("week") == 1)
    assert wk1.height == 32
    pc = pc00_sums(real, t, "last_season", 1).rename({"k": "k_pc", "n": "n_pc"})
    j = wk1.join(pc, on="team", how="left")
    b2 = F.predict(d, "b2", P_SCRIPT)[j["i"].to_numpy()]
    np.testing.assert_allclose(b2, (j["k_pc"] / j["n_pc"]).to_numpy(), atol=1e-9)
    # and the shrunk forecast of week 1 is a prior between last season and the league
    b3 = F.predict_parts(d, "b3", P_SCRIPT)
    assert np.isfinite(b3.forecast[j["i"].to_numpy()]).all()


@pytest.mark.parametrize("key", ["blitz_rate", "dropback_rate", "deep_shot_rate", "run_middle"])
def test_a_real_week_10_forecast_uses_nothing_from_week_10(real, key) -> None:
    """The live path (inputs through week 9 only) and the backtest path (the whole season in
    the tables) forecast the same numbers for week 10 on real kickoffs and charting."""
    inp = real["inp"]
    t = F.get_target(key)
    gw = inp.games.filter((pl.col("season") == SEASON) & (pl.col("week") == 10))
    rows = F.team_view(gw).select("season", "week", "game_id", "team", "opponent")
    assert rows.height >= 24
    full = design(real, key, forecast_rows=rows)
    live_tg = inp.tg.filter((pl.col("season") < SEASON) | (pl.col("week") < 10))
    live = F.build_design(
        t, F.observations(live_tg, t), inp.games, CURVES, inp.changed, inp.midseason, [SEASON],
        forecast_rows=rows,
    )  # fmt: skip
    assert full.rows.select("game_id", "team").equals(live.rows.select("game_id", "team"))
    assert np.isfinite(full.y).all() and np.isnan(live.y).all()  # only the full one has outcomes
    for kind in F.KINDS:
        np.testing.assert_allclose(
            F.predict(live, kind, P_SCRIPT), F.predict(full, kind, P_SCRIPT), atol=1e-12,
            err_msg=f"{key} {kind}",
        )  # fmt: skip


def test_real_forecasts_are_plausible(real) -> None:
    """Week 10 of 2025 with hand-picked parameters: blitz rates around the league's 29%."""
    d = design(real, "blitz_rate")
    wk = (d.rows["week"] == 10).to_numpy()
    assert wk.sum() >= 24
    p = {**P_SCRIPT, "kappa": 150.0}
    f = F.predict_parts(d, "model", p)
    b2, b3 = F.predict(d, "b2", p)[wk], F.predict(d, "b3", p)[wk]
    assert np.isfinite(f.forecast[wk]).all()
    assert 0.27 < f.forecast[wk].mean() < 0.31 and f.forecast[wk].min() > 0.15
    assert f.forecast[wk].max() < 0.50
    assert np.corrcoef(b2, b3)[0, 1] > 0.9  # shrinkage moves the rates, it doesn't reshuffle them
    assert b3.std() < b2.std()  # ... it pulls them toward the league
    assert (np.abs(f.opponent[wk]) < 0.06).all()  # the opponent's push is a few points
    lo, hi = F.predict_range(d, f, {"c": 1.0, "b": 0.0004, "q_lo": -1.28, "q_hi": 1.28})
    assert (lo[wk] < f.forecast[wk]).all() and (f.forecast[wk] < hi[wk]).all()


def test_the_real_coaching_changes_of_2025(real) -> None:
    ch = real["inp"].changed.filter(pl.col("season") == SEASON)
    assert ch.group_by("side").len().sort("side")["len"].to_list() == [32, 32]
    flag = {(r["team"], r["side"]): r["changed"] for r in ch.iter_rows(named=True)}
    # the six new head coaches of 2025 change both sides of the ball
    for team in ("CHI", "JAX", "NYJ", "NE", "LV", "NO"):
        assert flag[(team, "offense")] and flag[(team, "defense")], team
    # Kansas City kept its head coach and both coordinators
    assert not flag[("KC", "offense")] and not flag[("KC", "defense")]
    assert real["inp"].notes == []
