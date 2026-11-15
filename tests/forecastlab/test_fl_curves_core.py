"""FL01: the pure curve functions, with numbers worked out by hand."""

from __future__ import annotations

import math

import numpy as np
import polars as pl
import pytest
from fl_curves_fixtures import synth_rows
from scipy import stats

from nflengine.forecastlab import curves as C
from nflengine.forecastlab.schema import CURVE_BY_KEY

# a small curve on the whole numbers 0 .. 4
CDF = np.array([[0.1, 0.3, 0.6, 0.9, 1.0]])


def test_crps_by_hand():
    # y = 2: indicators [0, 0, 1, 1, 1]; (0.1)^2 + (0.3)^2 + (0.4)^2 + (0.1)^2 + 0
    assert C.crps(CDF, np.array([2.0]), 0)[0] == pytest.approx(0.27)
    # y = 0: every indicator is 1: .81 + .49 + .16 + .01 + 0
    assert C.crps(CDF, np.array([0.0]), 0)[0] == pytest.approx(1.47)
    # a certain curve is exact when right and costs one per whole number it is off by
    certain = np.array([[0.0, 0.0, 1.0, 1.0, 1.0]])
    assert C.crps(certain, np.array([2.0]), 0)[0] == 0.0
    assert C.crps(certain, np.array([4.0]), 0)[0] == pytest.approx(2.0)


def test_crps_grid_offset():
    # the same curve on the grid -2 .. 2: y = 0 is index 2
    assert C.crps(CDF, np.array([0.0]), -2)[0] == pytest.approx(0.27)


def test_brier_marks_by_hand():
    # marks 1.5 and 3.5, y = 2: P(>1.5) = 1 - F(1) = 0.7 and it happened -> 0.09;
    # P(>3.5) = 1 - F(3) = 0.1 and it didn't -> 0.01; mean 0.05
    assert C.brier_marks(CDF, np.array([2.0]), [1.5, 3.5], 0)[0] == pytest.approx(0.05)
    assert C.p_over(CDF, [1.5, 3.5], 0)[0] == pytest.approx([0.7, 0.1])


def test_band_by_hand():
    # within 1 of 2 = {1, 2, 3}: F(3) - F(0) = 0.9 - 0.1
    assert C.band_prob(CDF, np.array([2.0]), 1.0, 0)[0] == pytest.approx(0.8)
    # within 1 of 0 = {0, 1}: nothing below the grid
    assert C.band_prob(CDF, np.array([0.0]), 1.0, 0)[0] == pytest.approx(0.3)
    # the band is centred on the rounded projection
    assert C.band_prob(CDF, np.array([2.4]), 1.0, 0)[0] == pytest.approx(0.8)
    assert C.band_prob(CDF, np.array([2.6]), 1.0, 0)[0] == pytest.approx(
        0.7
    )  # centre 3: {2, 3, 4} = F(4) - F(1)
    assert C.band_hit(np.array([3.0, 4.0]), np.array([2.0, 2.0]), 1.0).tolist() == [1.0, 0.0]


def test_bell_by_hand():
    # a normal with sigma = w / 2.563 = 10 centred on 50: P(Y <= 49) = Phi(-0.05)
    w = np.array([10 * C.BELL_Z])
    f = C.bell_cdf("amount", np.array([50.0]), w, np.array([50.0]), -15, 120)
    assert f[0, 49 + 15] == pytest.approx(stats.norm.cdf(-0.05), abs=1e-12)
    assert f[0, 49 + 15] == pytest.approx(0.480061, abs=1e-6)
    assert f[0, -1] == 1.0
    # its 10th and 90th percentiles are the published ones (P10 = 50 - 12.816)
    p10, p90 = 50 - 1.2815515655 * 10, 50 + 1.2815515655 * 10
    f2 = C.bell_cdf("amount", np.array([50.0]), np.array([p90 - p10]), np.array([50.0]), -15, 120)
    assert f2[0, int(p90 + 0.5) - (-15)] >= 0.9 - 0.02
    # a count: a Poisson at the mean (mean 2: e^-2 and 3 e^-2)
    p = C.bell_cdf("count", np.array([2.0]), np.array([4.0]), np.array([2.0]), 0, 20)
    assert p[0, 0] == pytest.approx(math.exp(-2))
    assert p[0, 1] == pytest.approx(3 * math.exp(-2))


def test_nb_by_hand():
    # mean 2, alpha 0.5 -> r = 2, p = r / (r + mu) = 0.5: P(0) = 0.25, P(1) = 2 * .25 * .5 = .25,
    # P(2) = 3 * .25 * .25 = 0.1875
    f = C.nb_cdf(np.array([2.0]), np.array([0.5]), 0, 40)
    assert f[0, 0] == pytest.approx(0.25)
    assert f[0, 1] == pytest.approx(0.5)
    assert f[0, 2] == pytest.approx(0.6875)
    assert f[0, -1] == 1.0
    # and it matches scipy's own
    assert f[0, 5] == pytest.approx(stats.nbinom.cdf(5, 2.0, 0.5), abs=1e-12)


def test_trim_and_quantiles():
    row = np.array([0.2, 0.9, 1.0, 1.0, 1.0])
    assert C.trim_cdf(row).tolist() == [0.2, 0.9, 1.0]
    q = C.quantiles(CDF, [0.05, 0.1, 0.5, 0.95], 0)
    assert q[0].tolist() == [0.0, 0.0, 2.0, 4.0]  # the smallest whole number with F >= q


def test_pit_expected_histogram_by_hand():
    # F = [0.2, 0.5, 1.0]; y = 1 lands uniformly on (0.2, 0.5]: bins 2, 3 and 4 get a third each
    cdf = np.array([[0.2, 0.5, 1.0]])
    h = C.pit_rows(cdf, np.array([1.0]), 0)[0]
    assert h.sum() == pytest.approx(1.0)
    assert h[2:5].tolist() == pytest.approx([1 / 3] * 3)
    assert h[:2].sum() == 0 and h[5:].sum() == 0
    # an impossible outcome (no mass) is a point
    cdf0 = np.array([[0.5, 0.5, 1.0]])
    h0 = C.pit_rows(cdf0, np.array([1.0]), 0)[0]
    assert h0.sum() == pytest.approx(1.0) and h0.max() == 1.0


def test_pit_of_a_true_curve_is_flat():
    rng = np.random.default_rng(3)
    mu = rng.uniform(1.0, 6.0, size=4000)
    cdf = C.nb_cdf(mu, np.full(mu.shape, 0.3), 0, 60)
    y = rng.negative_binomial(1 / 0.3, (1 / 0.3) / (1 / 0.3 + mu)).astype(float)
    good = C.pit_ece(C.pit_rows(cdf, y, 0))
    assert good < 0.01
    # a curve that is too narrow is not
    narrow = C.nb_cdf(mu, np.full(mu.shape, 0.001), 0, 60)
    assert C.pit_ece(C.pit_rows(narrow, y, 0)) > max(0.025, 3 * good)


def test_own_history_baseline():
    fb = C.bell_cdf(
        "count", np.array([3.0, 3.0]), np.array([4.0, 4.0]), np.array([3.0, 3.0]), 0, 10
    )
    prior = np.full((2, C.OWN_MAX), np.nan)
    prior[0, -6:] = [1, 2, 3, 4, 5, 6]  # six games: used
    prior[1, -5:] = [1, 2, 3, 4, 5]  # five: not enough
    cdf, n = C.own_cdf(prior, fb, 0, 10)
    assert n.tolist() == [6, 0]
    assert cdf[0, 3] == pytest.approx(0.5)  # three of six results were <= 3
    assert cdf[0, 0] == 0.0 and cdf[0, 6] == 1.0
    assert np.array_equal(cdf[1], fb[1])  # the bell curve instead


def test_own_prior_takes_the_last_sixteen_oldest_first():
    hist = pl.DataFrame(
        {
            "player_id": ["a"] * 20 + ["b"] * 2,
            "actual": [float(i) for i in range(20)] + [7.0, 8.0],
        }
    )
    m = C.own_prior(hist, ["a", "b", "c"])
    assert m[0].tolist() == [float(i) for i in range(4, 20)]
    assert np.isnan(m[1, : C.OWN_MAX - 2]).all() and m[1, -2:].tolist() == [7.0, 8.0]
    assert np.isnan(m[2]).all()


# ---- the analog curve ---------------------------------------------------------------------------
def _hist_from(point, w, y, season=2020) -> C.Hist:
    n = len(point)
    return C.Hist(
        np.full(n, season), np.asarray(point, float), np.asarray(w, float), np.asarray(y, float)
    )


def test_analog_curve_from_five_known_analogs(monkeypatch):
    monkeypatch.setattr(C, "MIN_HIST", 5)
    # P50 100, width 40; results 60 80 100 120 160 -> z = -1, -.5, 0, .5, 1.5
    h = _hist_from([100] * 5, [40] * 5, [60, 80, 100, 120, 160])
    s = C.Settings("analog", 5, "point", 0, w_floor=5.0)
    # a new projection of P50 50 and width 20: outcomes 50 + z * 20 = 30, 40, 50, 60, 80
    out = C.make_curves(
        "amount", [s], h, np.array([50.0]), np.array([20.0]), lo=-15, hi=120, q_season=2021
    )[s]
    cdf = out.cdf[0]
    at = lambda k: cdf[k + 15]  # noqa: E731
    assert at(29) == 0.0 and at(30) == pytest.approx(0.2) and at(39) == pytest.approx(0.2)
    assert at(40) == pytest.approx(0.4) and at(50) == pytest.approx(0.6)
    assert at(60) == pytest.approx(0.8) and at(79) == pytest.approx(0.8)
    assert at(80) == 1.0 and cdf[-1] == 1.0
    assert out.n_analogs.tolist() == [5]
    # more than 49.5: the outcomes 50, 60 and 80, three of the five analogs
    assert C.p_over(out.cdf, [49.5], -15)[0, 0] == pytest.approx(0.6)


def test_analog_curve_clips_at_the_floor(monkeypatch):
    monkeypatch.setattr(C, "MIN_HIST", 3)
    h = _hist_from([10] * 3, [10] * 3, [-30, 0, 10])  # z = -4, -1, 0
    s = C.Settings("analog", 3, "point", 0, w_floor=1.0)
    out = C.make_curves(
        "amount", [s], h, np.array([10.0]), np.array([10.0]), lo=-15, hi=50, q_season=2021
    )[s]
    # 10 + (-4 * 10) = -30 -> clipped to the floor -15
    assert out.cdf[0, 0] == pytest.approx(1 / 3)


def test_width_floor_keeps_degenerate_ranges_finite(monkeypatch):
    monkeypatch.setattr(C, "MIN_HIST", 3)
    # a tiny published width: z would be 0.5 / 0.01 = 50 without the floor
    h = _hist_from([0.0] * 3, [0.01] * 3, [0.0, 0.5, 3.0])
    s = C.Settings("analog", 3, "point", 0, w_floor=5.0)
    out = C.make_curves(
        "amount", [s], h, np.array([0.0]), np.array([0.01]), lo=-15, hi=50, q_season=2021
    )[s]
    # results 0, 0.5 and 3 become z = 0, 0.1, 0.6 (width floored at 5): outcomes 0, 1 and 3
    assert out.cdf[0, 0 + 15] == pytest.approx(1 / 3)
    assert out.cdf[0, 1 + 15] == pytest.approx(2 / 3)
    assert out.cdf[0, 3 + 15] == 1.0


def test_nearest_analogs_by_projection(monkeypatch):
    monkeypatch.setattr(C, "MIN_HIST", 3)
    # five history rows at P50 10, 48, 52, 90, 51; each with a distinct standardized miss
    point = [10, 48, 52, 90, 51]
    w = [20] * 5
    y = [p + z * 20 for p, z in zip(point, [0.0, 0.1, 0.2, 0.3, 0.4], strict=True)]
    h = _hist_from(point, w, y)
    s = C.Settings("analog", 3, "point", 0, w_floor=5.0)
    out = C.make_curves(
        "amount", [s], h, np.array([50.0]), np.array([20.0]), lo=0, hi=100, q_season=2021
    )[s]
    # the 3 nearest to 50 are P50 51, 52, 48: z = 0.4, 0.2, 0.1 -> outcomes 58, 54, 52
    got = np.nonzero(np.diff(np.concatenate([[0], out.cdf[0]])) > 0)[0].tolist()
    assert got == [52, 54, 58]


def test_a_smaller_k_is_unaffected_by_a_larger_one_in_the_same_call(monkeypatch):
    """Regression: with fewer history rows than the largest k, the nearest k of a smaller
    setting must still be the nearest, not the oldest."""
    monkeypatch.setattr(C, "MIN_HIST", 10)
    df = synth_rows("rec_yds-wrte", [2019], weeks=range(1, 9), players=30, seed=5)
    h = C.hist_arrays("amount", C.prepare(df))
    q_p50 = np.array([5.0, 20.0, 60.0])
    q_w = np.array([20.0, 40.0, 100.0])
    small = C.Settings("analog", 40, "point", 0)
    big = C.Settings("analog", 4000, "point", 0)
    both = C.make_curves("amount", [small, big], h, q_p50, q_w, lo=-15, hi=350, q_season=2020)
    alone = C.make_curves("amount", [small], h, q_p50, q_w, lo=-15, hi=350, q_season=2020)
    assert np.array_equal(both[small].cdf, alone[small].cdf)
    assert both[big].n_analogs.tolist() == [df.height] * 3  # every row is an analog
    assert both[small].n_analogs.tolist() == [40] * 3


def test_too_little_history_falls_back_to_the_bell_curve():
    df = synth_rows("rec_yds-wrte", [2019], weeks=[1], players=10, seed=2)  # 10 rows only
    hist = C.prepare(df)
    q = synth_rows("rec_yds-wrte", [2020], weeks=[1], players=10, seed=3)
    s = C.Settings("analog", 1000, "point", 0)
    curves, bell, _, _ = C.step_curves("amount", [s], hist, q, lo=-15, hi=350)
    assert curves[s].n_analogs.tolist() == [0] * 10
    assert np.array_equal(curves[s].cdf, bell)


def test_nb_spread_is_fit_on_the_analogs(monkeypatch):
    monkeypatch.setattr(C, "MIN_HIST", 4)
    # analogs at mean 2 with results 0 4 0 4: sum[(y - mu)^2 - mu] / sum mu^2 = 8 / 16 = alpha 0.5
    h = _hist_from([2.0] * 4, [3.0] * 4, [0, 4, 0, 4])
    s = C.Settings("nb", 4, "point", 0)
    out = C.make_curves(
        "count", [s], h, np.array([2.0]), np.array([3.0]), lo=0, hi=40, q_season=2021
    )[s]
    expect = C.nb_cdf(np.array([2.0]), np.array([0.5]), 0, 40)
    assert out.cdf[0] == pytest.approx(expect[0])
    # equidispersed analogs (variance = mean) are a Poisson: alpha at its floor
    h2 = _hist_from([2.0] * 4, [3.0] * 4, [0, 2, 2, 4])
    out2 = C.make_curves(
        "count", [s], h2, np.array([2.0]), np.array([3.0]), lo=0, hi=40, q_season=2021
    )[s]
    assert out2.cdf[0, :3] == pytest.approx(
        C.nb_cdf(np.array([2.0]), np.array([C.ALPHA_MIN]), 0, 40)[0, :3]
    )


def test_count_analog_curve_is_the_raw_outcomes(monkeypatch):
    monkeypatch.setattr(C, "MIN_HIST", 4)
    h = _hist_from([3.0] * 4, [4.0] * 4, [0, 1, 1, 5])
    s = C.Settings("analog", 4, "point", 0)
    out = C.make_curves(
        "count", [s], h, np.array([3.0]), np.array([4.0]), lo=0, hi=10, q_season=2021
    )[s]
    assert out.cdf[0, :6].tolist() == pytest.approx([0.25, 0.75, 0.75, 0.75, 0.75, 1.0])


def test_recent_seasons_window(monkeypatch):
    monkeypatch.setattr(C, "MIN_HIST", 2)
    season = np.array([2017, 2018, 2019, 2020, 2020])
    h = C.Hist(season, np.full(5, 10.0), np.full(5, 10.0), np.array([10.0, 10.0, 20.0, 30.0, 30.0]))
    s = C.Settings("analog", 10, "point", 2, w_floor=1.0)  # the 2 seasons before + this one
    out = C.make_curves(
        "amount", [s], h, np.array([10.0]), np.array([10.0]), lo=0, hi=100, q_season=2021
    )[s]
    assert out.n_analogs.tolist() == [3]  # 2019 and 2020 (3 rows), not 2017 / 2018


# ---- walk-forward: nothing from the week on may reach a curve ------------------------------------
@pytest.mark.parametrize("key", ["rec_yds-wrte", "receptions-wrte"])
def test_scrambling_actuals_from_a_week_on_changes_no_earlier_curve(key):
    t = CURVE_BY_KEY[key]
    lo, hi = C.grid_bounds(key, t.floor)
    df = synth_rows(key, [2018, 2019, 2020], weeks=range(1, 9), players=40, seed=11)
    cut = (2019, 5)
    rng = np.random.default_rng(0)
    after = (pl.col("season") > cut[0]) | (
        (pl.col("season") == cut[0]) & (pl.col("week") >= cut[1])
    )
    scrambled = df.with_columns(
        pl.when(after)
        .then(pl.Series(rng.integers(0, 400, df.height).astype(float)))
        .otherwise(pl.col("actual"))
        .alias("actual")
    )
    sl = [
        C.Settings("analog", 200, "point_w", 0)
        if t.kind == "amount"
        else C.Settings("nb_cal", 200, "point", 0),
        C.Settings("analog", 200, "point", 4),
    ]

    def run(frame):
        return {
            (st.season, st.week): (st.curves, st.bell, st.own)
            for st in C.walk_forward(frame, t.kind, sl, lo=lo, hi=hi, score_seasons=[2019, 2020])
        }

    a, b = run(df), run(scrambled)
    assert a.keys() == b.keys()
    moved = 0
    for k in a:
        for s in sl:
            same = np.array_equal(a[k][0][s].cdf, b[k][0][s].cdf)
            if k <= cut:
                assert same, f"{k} {s.label} moved when only results from {cut} on changed"
            else:
                moved += not same
        if k <= cut:  # the baselines read only earlier weeks too
            assert np.array_equal(a[k][1], b[k][1]) and np.array_equal(a[k][2], b[k][2])
    assert moved > 0  # the test has teeth: later weeks do read the earlier results


def test_walk_forward_visits_each_week_once_with_only_earlier_rows():
    key = "receptions-rb"
    t = CURVE_BY_KEY[key]
    df = synth_rows(key, [2019, 2020], weeks=range(1, 6), players=25, seed=4)
    s = C.Settings("nb", 100, "point", 0)
    seen = []
    for st in C.walk_forward(df, t.kind, [s], lo=0, hi=30, score_seasons=[2020]):
        seen.append((st.season, st.week))
        assert st.rows.height == 25
        assert st.curves[s].n_analogs.min() >= 100 or st.curves[s].n_analogs.min() == 0
    assert seen == [(2020, w) for w in range(1, 6)]


# ---- the ship rule -------------------------------------------------------------------------------
def _pooled(**over):
    base = {
        "crps_model": 9.0, "crps_bell": 10.0, "crps_own": 11.0,
        "brier_model": 0.10, "brier_bell": 0.11, "band_pred": 0.41, "band_hit": 0.40,
    }  # fmt: skip
    return {**base, **over}


def _seasons(wins: int, n: int = 5):
    return [{"crps_model": 9.0 if i < wins else 10.5, "crps_bell": 10.0} for i in range(n)]


def test_ship_rule_each_part_and_all_together():
    ok = C.ship_verdict(_pooled(), _seasons(5))
    assert ok["ships"] and all(p["ok"] for p in ok["rule"].values())
    assert C.ship_verdict(_pooled(), _seasons(4))["ships"]  # 4 of 5 seasons is enough
    three = C.ship_verdict(_pooled(), _seasons(3))
    assert not three["ships"] and not three["rule"]["crps_vs_bell"]["ok"]
    assert three["rule"]["crps_vs_bell"]["need"] == 4
    # pooled CRPS above the bell curve's
    assert not C.ship_verdict(_pooled(crps_model=10.1), _seasons(5))["rule"]["crps_vs_bell"]["ok"]
    # worse than his own history
    v = C.ship_verdict(_pooled(crps_own=8.5), _seasons(5))
    assert not v["rule"]["crps_vs_own"]["ok"] and not v["ships"]
    # thresholds
    v = C.ship_verdict(_pooled(brier_model=0.12), _seasons(5))
    assert not v["rule"]["brier_vs_bell"]["ok"] and not v["ships"]
    # bands: 2 points is the limit (inclusive), 2.5 misses
    assert C.ship_verdict(_pooled(band_pred=0.42, band_hit=0.40), _seasons(5))["rule"]["band"]["ok"]
    v = C.ship_verdict(_pooled(band_pred=0.425, band_hit=0.40), _seasons(5))
    assert not v["rule"]["band"]["ok"] and not v["ships"]
    assert v["rule"]["band"]["gap_pp"] == pytest.approx(2.5)
    # a smaller window needs the same share of seasons (4 of 5 = 80%)
    assert not C.ship_verdict(_pooled(), _seasons(1, 2))["ships"]
    assert C.ship_verdict(_pooled(), _seasons(2, 2))["ships"]


def test_settings_validate_and_round_trip():
    s = C.Settings("nb_cal", 500, "point", 4)
    assert C.Settings.from_dict(s.as_dict()) == s
    assert "nb_cal-k500-point-last4" in s.label
    with pytest.raises(ValueError):
        C.Settings("magic")
    with pytest.raises(ValueError):
        C.Settings("analog", 0)
    with pytest.raises(ValueError):
        C.make_curves(
            "amount", [C.Settings("nb", 10)], C.Hist(*[np.zeros(200)] * 4), np.zeros(1), np.ones(1),
            lo=0, hi=10, q_season=2020,
        )  # fmt: skip


# ---- analog_zero: zero is its own outcome (addendum, 2026-11-14) ---------------------------------
def _zero_curve(point, w, y, q_point, q_w, lo=-15, hi=60, method="analog_zero"):
    s = C.Settings(method, len(y), "point", 0, w_floor=5.0)
    h = _hist_from(point, w, y)
    out = C.make_curves(
        "amount", [s], h, np.array([q_point], float), np.array([q_w], float),
        lo=lo, hi=hi, q_season=2021,
    )[s]  # fmt: skip
    return out.cdf[0]


def test_analog_zero_atom_is_the_zero_share_by_hand(monkeypatch):
    monkeypatch.setattr(C, "MIN_HIST", 5)
    # five analogs at P50 10, width 20: results 0, 0, -4, 6 and 30 -> z = -.5, -.5, -.7, -.2, 1
    point, w, y = [10] * 5, [20] * 5, [0, 0, -4, 6, 30]
    cdf = _zero_curve(point, w, y, q_point=2.3, q_w=10.0)
    at = lambda k: cdf[k + 15]  # noqa: E731
    # the atom at 0 is exactly the zero share, 2 of 5; nothing at 1 from a zero
    assert at(0) - at(-1) == pytest.approx(0.4)
    # the negative result keeps its own value, -4, and not 2.3 + (-.7 * 10) = -5
    assert at(-5) == 0.0 and at(-4) - at(-5) == pytest.approx(0.2) and at(-1) == pytest.approx(0.2)
    # the positive 6 would map to round(2.3 + (-.2 * 10)) = 0 today: it goes to 1; the 30 to 12
    assert at(1) - at(0) == pytest.approx(0.2)
    assert at(11) == pytest.approx(0.8) and at(12) == pytest.approx(1.0) and cdf[-1] == 1.0
    assert (np.diff(cdf) >= 0).all()
    # the old method smears: its zeros land at -3 (2.3 - 5), the positive result at 0
    old = _zero_curve(point, w, y, 2.3, 10.0, method="analog")
    assert old[-3 + 15] - old[-4 + 15] == pytest.approx(0.4)


def test_a_zero_analog_never_puts_mass_on_one(monkeypatch):
    monkeypatch.setattr(C, "MIN_HIST", 4)
    # Kacmarek-style: analogs with a slightly different P50 (1.0, width 10) who gained 0 yards
    # -> z = -0.1; a row at P50 2.3 maps them to round(2.3 - 1.0) = 1 today
    point, w, y = [1.0] * 4, [10.0] * 4, [0, 0, 0, 0]
    old = _zero_curve(point, w, y, 2.3, 10.0, method="analog")
    assert old[1 + 15] - old[0 + 15] == pytest.approx(1.0)  # all the mass on exactly 1 yard
    new = _zero_curve(point, w, y, 2.3, 10.0)
    assert new[0 + 15] == pytest.approx(1.0) and new[-1 + 15] == 0.0
    assert new[1 + 15] - new[0 + 15] == 0.0


def test_no_positive_result_lands_below_one(monkeypatch):
    monkeypatch.setattr(C, "MIN_HIST", 50)
    rng = np.random.default_rng(8)
    n = 400
    point = rng.uniform(0, 12, n)
    w = rng.uniform(1, 30, n)
    y = np.round(np.maximum(point + (rng.gamma(1.3, 0.4, n) - 0.4) * w, 0))  # many zeros
    neg = rng.random(n) < 0.05
    y[neg] = -rng.integers(1, 8, neg.sum())
    h = _hist_from(point, w, y)
    # K above the history size: every row is an analog of every query
    s = C.Settings("analog_zero", 10_000, "point_w", 0, w_floor=5.0)
    q_p, q_w = rng.uniform(0, 15, 60), rng.uniform(1, 40, 60)
    cdf = C.make_curves("amount", [s], h, q_p, q_w, lo=-15, hi=300, q_season=2021)[s].cdf
    assert (np.diff(cdf, axis=1) >= -1e-12).all() and (cdf[:, -1] == 1.0).all()
    at = lambda k: cdf[:, k + 15]  # noqa: E731
    # exactly the analogs' shares: negatives keep their values, zeros stay at 0, and every
    # positive result is at 1 or more
    assert at(-1) == pytest.approx((y < 0).mean())
    assert at(0) - at(-1) == pytest.approx((y == 0).mean())
    assert 1 - at(0) == pytest.approx((y > 0).mean())
    assert (y == 0).mean() > 0.1 and (y < 0).any()  # the data has what the test is about


def test_analog_zero_equals_analog_where_nothing_is_zero_or_negative(monkeypatch):
    monkeypatch.setattr(C, "MIN_HIST", 5)
    point, w, y = [100] * 5, [40] * 5, [60, 80, 100, 120, 160]  # all positive, far from 0
    a = _zero_curve(point, w, y, 50.0, 20.0, hi=120, method="analog")
    b = _zero_curve(point, w, y, 50.0, 20.0, hi=120, method="analog_zero")
    assert np.array_equal(a, b)


def test_analog_zero_is_for_amounts_only():
    with pytest.raises(ValueError, match="amounts"):
        C.make_curves(
            "count", [C.Settings("analog_zero", 10)], C.Hist(*[np.zeros(200)] * 4), np.zeros(1),
            np.ones(1), lo=0, hi=10, q_season=2020,
        )  # fmt: skip


def test_the_one_or_more_diagnostic_by_hand():
    # F(0) = 0.1 on the grid 0 .. 4: P(Y >= 1) = 0.9; on the grid -2 .. 2 the same curve's
    # F(0) is the third value
    assert C.p_ge1(CDF, 0)[0] == pytest.approx(0.9)
    cdf = np.array([[0.0, 0.2, 0.5, 0.8, 1.0]])  # -2 .. 2: P(Y <= 0) = 0.5
    assert C.p_ge1(cdf, -2)[0] == pytest.approx(0.5)
    rs = C.score_curve(cdf, np.array([3.0]), np.array([1.0]), 1.0, [0.5], -2)
    assert rs.p_ge1[0] == pytest.approx(0.5)
