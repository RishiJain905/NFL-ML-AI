"""FL00 availability: the models, the walk-forward, calibration, inherit and the ship rule."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from fl_avail_fixtures import make_league, prepared, small_cfg

from nflengine.forecastlab import availability as A
from nflengine.forecastlab import schema


@pytest.fixture(scope="module")
def frames():
    prep = prepared(make_league())
    rows = A.build_rows(prep)
    return {v: A.add_features(prep, rows, v) for v in schema.VIEWS}


def test_platt_identity_in_burn_in_and_recovers_a_slope():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.05, 0.95, 20000)
    assert A.fit_platt(p[:10], (p[:10] > 0.5).astype(float)) == (1.0, 0.0)
    z = 0.5 * np.log(p / (1 - p)) + 0.3
    y = (rng.random(p.size) < 1 / (1 + np.exp(-z))).astype(float)
    a, b = A.fit_platt(p, y)
    assert a == pytest.approx(0.5, abs=0.05) and b == pytest.approx(0.3, abs=0.05)


def test_range_shifts_hit_ten_percent_each_side():
    rng = np.random.default_rng(1)
    y = rng.random(50000)
    lo, hi = np.full_like(y, 0.3), np.full_like(y, 0.6)  # covers 30%: too narrow
    s = A.fit_shifts(lo, hi, y, 100)
    assert np.mean(y < lo - s["lo"]) == pytest.approx(0.1, abs=0.01)
    assert np.mean(y > hi + s["hi"]) == pytest.approx(0.1, abs=0.01)


def test_high_quantile_survives_a_mass_at_one(frames):
    """P90 is fit reflected: with many shares at exactly 1.0 it must still vary by player."""
    cfg = small_cfg()
    train = frames["tuesday"].filter(pl.col("season") < 2016)
    m = A.fit_view(train, "tuesday", cfg)
    test = A.add_vacated(frames["tuesday"].filter(pl.col("season") == 2016), pl.lit(0.0))
    sh = m.shares(test)
    assert np.std(sh["snap_raw_p90"]) > 0.01
    assert np.all(sh["snap_p10"] <= sh["snap_p50"] + 1e-12)
    assert np.all(sh["snap_p50"] <= sh["snap_p90"] + 1e-12)


def test_walk_forward_rows_only_see_earlier_rows(frames):
    """A week's predictions don't move when every label from that week on is scrambled
    (weekly refits), and the fit is deterministic."""
    cfg = small_cfg(refit="week")
    f = frames["saturday"]
    cut = A.tkey(2016, 4)
    a = A.walk_forward(f, "saturday", cfg, [2015, 2016])
    rng = np.random.default_rng(5)
    fut = pl.col("t") >= cut
    g = f.with_columns(
        pl.when(fut)
        .then(pl.Series(rng.random(f.height) < 0.5))
        .otherwise(pl.col("played"))
        .alias("played"),
        pl.when(fut)
        .then(pl.Series(rng.random(f.height)))
        .otherwise(pl.col("actual_snap"))
        .alias("actual_snap"),
    )
    b = A.walk_forward(g, "saturday", cfg, [2015, 2016])
    cols = ["p_play", "base_p_play", "snap_p10", "snap_p50", "snap_p90", "tgt_share", "carry_share"]
    key = ["game_id", "player_id"]
    pa = a.filter(pl.col("t") <= cut).sort(key).select(key + cols)
    pb = b.filter(pl.col("t") <= cut).sort(key).select(key + cols)
    assert pa.equals(pb)
    again = A.walk_forward(f, "saturday", cfg, [2015, 2016]).sort(key).select(key + cols)
    assert a.sort(key).select(key + cols).equals(again)


def test_predictions_are_well_formed(frames):
    cfg = small_cfg()
    pr = A.walk_forward(frames["saturday"], "saturday", cfg, [2015, 2016])
    assert pr["p_play"].is_between(0, 1).all()
    out = A.to_schema(pr, view="saturday", version="v")
    assert list(out.columns) == list(schema.AVAIL_SCHEMA)
    tgt = out.filter(pl.col("tgt_share").is_not_null())["pgroup"].unique().to_list()
    assert set(tgt) <= set(A.TGT_GROUPS)
    car = out.filter(pl.col("carry_share").is_not_null())["pgroup"].unique().to_list()
    assert set(car) <= set(A.CAR_GROUPS)
    assert (out["exp_snap"] - out["p_play"] * out["snap_p50"]).abs().max() < 1e-12
    risk = out.filter(pl.col("report_status").is_in(["Out", "Doubtful", "Questionable"]))
    assert risk["at_risk"].all()


class _Stub:
    """A model whose shares rise by half of what is left open."""

    def shares(self, f: pl.DataFrame) -> dict[str, np.ndarray]:
        return {
            "tgt_share": (f["base_tgt_share"].fill_null(0) + 0.5 * f["vac_tgt"]).to_numpy(),
            "carry_share": (f["base_carry_share"].fill_null(0) + 0.5 * f["vac_car"]).to_numpy(),
            "snap_p50": (f["base_snap"].fill_null(0) + 0.5 * f["vac_snap"]).to_numpy(),
        }


def test_inherit_in_minus_out_is_the_open_share():
    pred = pl.DataFrame(
        {
            "season": [2016] * 4,
            "week": [3] * 4,
            "game_id": ["g"] * 4,
            "team": ["KC"] * 4,
            "player_id": ["a", "b", "c", "d"],
            "player": ["A", "B", "C", "D"],
            "position": ["WR", "WR", "TE", "RB"],
            "pgroup": ["WR", "WR", "TE", "RB"],
            "base_tgt_share": [0.3, 0.2, 0.1, 0.05],
            "base_carry_share": [None, None, None, 0.6],
            "base_snap": [0.9, 0.8, 0.7, 0.6],
            "p_play": [0.4, 1.0, 1.0, 1.0],
            "at_risk": [True, False, False, False],
        }
    )
    pred = A.add_vacated(pred, 1.0 - pl.col("p_play")).with_columns(
        pl.col("base_tgt_share").alias("tgt_share"),
        pl.col("base_snap").alias("snap_p50"),
        pl.col("base_carry_share").alias("carry_share"),
    )
    inh = A.inherit_table(_Stub(), pred)
    t = inh.filter(pl.col("kind") == "targets").sort("player_id")
    assert t["player_id"].to_list() == ["b", "c", "d"]  # every receiver gains the same 0.15
    assert t["delta"].to_list() == pytest.approx([0.15, 0.15, 0.15])
    assert sorted(t["rank"].to_list()) == [1, 2, 3]
    assert (t["share_if_out"] - t["share_if_in"] - t["delta"]).abs().max() < 1e-12
    s = inh.filter(pl.col("kind") == "snaps")
    assert s["player_id"].to_list() == ["b"] and s["delta"][0] == pytest.approx(0.45)
    assert inh.filter(pl.col("kind") == "carries").height == 0  # a WR carries nothing
    assert set(inh.columns) | {"view"} == set(schema.INHERIT_SCHEMA)  # the run adds `view`


def _sc(model, base, n=7, **extra):
    return {"n": 100, f"{model[0]}_model": model[1], f"{model[0]}_baseline": base, **extra}


def test_ship_rule():
    def scores(play_wins, ece, cov):
        by = []
        for i in range(7):
            win = i < play_wins
            by.append(
                {
                    "season": 2019 + i,
                    "play": _sc(("brier", 0.09 if win else 0.11), 0.10, ece_q10=0.01),
                    "snap": _sc(("mae", 0.11), 0.12, coverage_80=0.8),
                    "tgt_share": _sc(("mae", 0.05), 0.04),
                    "carry_share": _sc(("mae", 0.09), 0.09),
                }
            )
        pooled = {
            "play": _sc(("brier", 0.095), 0.10, ece_q10=ece),
            "snap": _sc(("mae", 0.11), 0.12, coverage_80=cov),
            "tgt_share": _sc(("mae", 0.05), 0.04),
            "carry_share": _sc(("mae", 0.09), 0.09),
        }
        return {"pooled": pooled, "by_season": by}

    v = A.verdicts(scores(6, 0.015, 0.8))
    assert v["play"]["ships"] and v["snap"]["ships"]
    assert not v["tgt_share"]["ships"] and not v["carry_share"]["ships"]  # loses / ties
    assert not A.verdicts(scores(5, 0.015, 0.8))["play"]["ships"]  # 5 of 7 seasons
    assert not A.verdicts(scores(7, 0.021, 0.8))["play"]["ships"]  # calibration error
    assert not A.verdicts(scores(7, 0.01, 0.89))["snap"]["ships"]  # range too wide
    assert A.wins_needed(7) == 6 and A.wins_needed(2) == 2
