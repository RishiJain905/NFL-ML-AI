"""LD00 models: the pure functions, the fitted models' shapes (monotone wp, fg, punt, kickoff), the
leakage guard in `fit_all` and the save / load round trip. Everything on small synthetic frames."""

from __future__ import annotations

import json

import numpy as np
import polars as pl
import pytest
from live_stubs import (
    FIT_SEASONS,
    StubGain,
    fitted_bundle,
    make_models,
    make_wp_rows,
    simple_kickoff,
    small_cfg,
    synthetic_frames,
)

from nflengine.live import models as M
from nflengine.live import schema
from nflengine.live.models import FGModel, KickoffModel, NextPossession


# --- settings and matrices --------------------------------------------------------------------
def test_settings_merge_and_do_not_mutate_defaults() -> None:
    before = json.dumps(M.DEFAULTS, sort_keys=True)
    default_rounds = M.DEFAULTS["wp"]["n_estimators"]
    cfg = M.settings({"n_boot": 3, "wp": {"n_estimators": 7}})
    assert cfg["n_boot"] == 3 and cfg["wp"]["n_estimators"] == 7, "overrides win"
    assert "num_leaves" in cfg["wp"], "a partial override keeps the other keys of the block"
    assert json.dumps(M.DEFAULTS, sort_keys=True) == before, "DEFAULTS must not be mutated"
    cfg["wp"]["n_estimators"] = 1
    assert M.DEFAULTS["wp"]["n_estimators"] == default_rounds


def test_matrix_and_state_matrix() -> None:
    df = pl.DataFrame({"a": [1, 2], "b": [0.5, 1.5], "c": [9, 9]})
    X = M.matrix(df, ["b", "a"])
    assert X.dtype == np.float64 and X.tolist() == [[0.5, 1.0], [1.5, 2.0]]
    S = M.state_matrix([{"a": 1.0, "b": 2.0}, {"a": 3.0, "b": 4.0}], ["b", "a"])
    assert S.tolist() == [[2.0, 1.0], [4.0, 3.0]]


# --- legal_gain and conversion_prob -----------------------------------------------------------
def test_legal_gain_moves_goal_line_mass_to_the_touchdown_class() -> None:
    rng = np.random.default_rng(0)
    probs = rng.dirichlet(np.ones(schema.N_GAIN_CLASSES), size=5)
    yl = np.array([1, 3, 10, 50, 99])
    before = probs.copy()
    out = M.legal_gain(probs, yl)
    assert np.array_equal(probs, before), "the input must not be modified"
    assert np.allclose(out.sum(axis=1), 1.0), "rows still sum to 1"
    gains = np.array(schema.GAIN_VALUES)
    for r, y in enumerate(yl):
        over = gains >= y
        assert np.all(out[r, : schema.TD_CLASS][over] == 0.0), f"row {r}: gains >= {y} are illegal"
        expect_td = before[r, schema.TD_CLASS] + before[r, : schema.TD_CLASS][over].sum()
        assert out[r, schema.TD_CLASS] == pytest.approx(expect_td), f"row {r}: mass goes to the TD"
        assert np.array_equal(
            out[r, : schema.TD_CLASS][~over], before[r, : schema.TD_CLASS][~over]
        ), f"row {r}: legal gains must be untouched"
    assert out[4, schema.TD_CLASS] == pytest.approx(before[4, schema.TD_CLASS]), (
        "99 to go: no gain class (max +65) reaches the goal line"
    )


def test_conversion_prob_hand_computed() -> None:
    p = np.zeros((1, schema.N_GAIN_CLASSES))
    p[0, 0 - schema.GAIN_MIN] = 0.3  # gain 0
    p[0, 3 - schema.GAIN_MIN] = 0.2  # gain 3
    p[0, 5 - schema.GAIN_MIN] = 0.3  # gain 5
    p[0, schema.TD_CLASS] = 0.2  # a touchdown
    probs = np.repeat(p, 5, axis=0)
    got = M.conversion_prob(probs, np.array([1, 3, 4, 6, 99]))
    # 1 or 3 to go: gain >= 3 or a TD = .2 + .3 + .2; 4: gain 5 or TD; 6 / 99: only the TD
    assert got.tolist() == pytest.approx([0.7, 0.7, 0.5, 0.2, 0.2])
    assert M.conversion_prob(p, np.array([1])).shape == (1,)


# --- field goals ------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def fg_model() -> FGModel:
    frames = synthetic_frames([2016, 2019, 2022, 2025], n=1500, seed=5)
    return M.fit_fg(frames["fg"], small_cfg())


def test_fg_probability_falls_with_distance(fg_model: FGModel) -> None:
    d = np.arange(20, 71, 5, dtype=float)
    for era in (2, 4):
        p = fg_model.predict(d, [era] * len(d), [0] * len(d))
        assert np.all(np.diff(p) <= 1e-9), f"era {era}: P(make) must not rise with distance: {p}"
        assert p[0] > 0.8 > 0.35 > p[-1], f"era {era}: implausible range {p[0]:.2f}..{p[-1]:.2f}"
        assert np.all((p > 0) & (p < 1))


def test_fg_beyond_max_distance_is_zero(fg_model: FGModel) -> None:
    d = np.array([schema.FG_MAX_DISTANCE, schema.FG_MAX_DISTANCE + 1, 80.0])
    p = fg_model.predict(d, [4, 4, 4], [0, 0, 0])
    assert p[0] > 0.0, "exactly 70 yards is still offered"
    assert p[1] == 0.0 and p[2] == 0.0, "longer tries are not offered"


def test_fg_unknown_wind_uses_the_fill_and_indoors_ignore_wind(fg_model: FGModel) -> None:
    d, era = [45.0, 55.0], [4, 4]
    unknown = fg_model.predict(d, era, [0, 0], wind=None)
    filled = fg_model.predict(d, era, [0, 0], wind=[fg_model.wind_fill] * 2)
    assert np.allclose(unknown, filled), "unknown outdoor wind == the training median (wind_fill)"
    assert np.all(fg_model.predict(d, era, [0, 0], wind=[25.0, 25.0]) < unknown), (
        "a 25 mph wind must hurt an outdoor kick in a model trained on a wind effect"
    )
    assert np.all(fg_model.predict(d, era, [0, 0], wind=[0.0, 0.0]) > unknown)
    indoor = fg_model.predict(d, era, [1, 1], wind=None)
    assert np.allclose(indoor, fg_model.predict(d, era, [1, 1], wind=[30.0, 30.0])), (
        "indoors the wind is ignored"
    )
    cold = fg_model.predict(d, era, [1, 1], wind=[None, None], temp=[10.0, 10.0])
    assert np.allclose(indoor, cold), "indoors the temperature is ignored too"
    assert fg_model.wind_fill > 0


def test_fg_design_repeats_wind_and_temp_independently() -> None:
    """`wind=None` (or one value) with a per-row `temp` array used to repeat both to n*n."""
    coef = [-0.9] + [0.0] * 8 + [0.0, -0.3, -0.5]  # ..., indoor, wind / 10, cold
    fg = FGModel(coef=coef, intercept=5.0, wind_fill=8.0)
    d, era, ind = [40.0, 50.0], [4, 4], [0, 0]
    cold2, warm2 = [30.0, 30.0], [70.0, 70.0]
    ref = fg.predict(d, era, ind, wind=[None, None], temp=cold2)
    assert np.allclose(fg.predict(d, era, ind, wind=None, temp=cold2), ref), "wind=None, temp[n]"
    assert np.allclose(fg.predict(d, era, ind, wind=[None], temp=cold2), ref), "wind=[None]"
    assert np.all(ref < fg.predict(d, era, ind, wind=None, temp=warm2)), "cold weather hurts"
    wind_only = fg.predict(d, era, ind, wind=[20.0, 5.0], temp=None)
    assert np.allclose(wind_only, fg.predict(d, era, ind, wind=[20.0, 5.0], temp=[None, None]))
    # one value for either, per-row for the other
    one_wind = fg.predict(d, era, ind, wind=[20.0], temp=[30.0, 70.0])
    assert np.allclose(one_wind, fg.predict(d, era, ind, wind=[20.0, 20.0], temp=[30.0, 70.0]))
    one_temp = fg.predict(d, era, ind, wind=[0.0, 25.0], temp=[30.0])
    assert np.allclose(one_temp, fg.predict(d, era, ind, wind=[0.0, 25.0], temp=cold2))
    same_kick = fg.predict([40.0, 40.0], era, ind, wind=[0.0, 25.0], temp=[70.0])
    assert same_kick[0] > same_kick[1], "same kick, more wind: lower probability"
    both = fg.predict(d, era, ind, wind=[20.0], temp=[30.0])
    assert np.allclose(both, fg.predict(d, era, ind, wind=[20.0, 20.0], temp=cold2))
    assert fg.design(d, era, ind, None, cold2).shape == (2, 12)


def test_fg_fit_shares_wind_fill_with_a_bootstrap_refit() -> None:
    frames = synthetic_frames([2019], n=800, seed=1)
    main = M.fit_fg(frames["fg"], small_cfg())
    w = np.bincount(np.random.default_rng(0).integers(0, 800, 800), minlength=800).astype(float)
    boot = M.fit_fg(frames["fg"], small_cfg(), weight=w, wind_fill=main.wind_fill)
    assert boot.wind_fill == main.wind_fill, "refits must share one design matrix"
    assert boot.coef != main.coef, "a bootstrap weighting should move the coefficients"
    assert len(main.coef) == 12 and set(main.as_dict()) == {"coef", "intercept", "wind_fill"}


# --- punt and kickoff -------------------------------------------------------------------------
def punt_frame(n: int = 3000) -> pl.DataFrame:
    rng = np.random.default_rng(2)
    outcome = rng.choice(["recv", "keep", "ret_td"], n, p=[0.95, 0.03, 0.02])
    spot = rng.integers(55, 90, n)
    return pl.DataFrame(
        {"yardline_100": rng.integers(30, 96, n), "outcome": outcome, "spot": spot}
    ).with_columns(
        pl.when(pl.col("outcome") == "ret_td").then(None).otherwise(pl.col("spot")).alias("spot")
    )


def test_fit_punt_every_yard_line_is_a_distribution() -> None:
    cfg = small_cfg()
    cfg["punt"]["min_effective_n"] = 300.0
    pm = M.fit_punt(punt_frame(), cfg)
    assert len(pm.by_yardline) == 99 and len(pm.bandwidth) == 99
    for y in (1, 30, 60, 99):
        np_ = pm.at(y)
        total = np_.recv.sum() + np_.keep.sum() + np_.ret_td
        assert total == pytest.approx(1.0), f"punt from {y}: raw distribution sums to {total}"
        assert sum(p for _, _, p in np_.support()) == pytest.approx(1.0)
    assert pm.at(0) is pm.at(1) and pm.at(150) is pm.at(99), "yard lines clamp to 1-99"
    assert max(pm.bandwidth) >= pm.bandwidth[60 - 1], "rare yard lines get a wider kernel"
    assert M.PuntModel.from_dict(pm.as_dict()).at(40).as_dict() == pm.at(40).as_dict()
    assert pm.at(60).mean_recv_spot() == pytest.approx(72.0, abs=3.0)


def test_next_possession_support_normalises_and_folds_tiny_mass() -> None:
    recv = np.zeros(99)
    recv[74], recv[79], recv[10] = 0.6, 0.38, 1e-6
    keep = np.zeros(99)
    keep[59] = 0.01
    np_ = NextPossession(recv, 0.009, keep)
    sup = np_.support()
    assert sum(p for _, _, p in sup) == pytest.approx(1.0), "support() is renormalised"
    assert ("recv", 11) not in [(o, s) for o, s, _ in sup], "1e-6 is folded away"
    assert {o for o, _, _ in sup} == {"recv", "keep", "ret_td"}
    assert np_.mean_recv_spot() == pytest.approx((75 * 0.6 + 80 * 0.38 + 11e-6) / (0.98 + 1e-6))


def kickoff_frame_eras(eras: dict[str, int]) -> pl.DataFrame:
    """Kickoffs: era -> the receiver's start (all recv), plus onside kicks at a silly spot."""
    rows = []
    for era, spot in eras.items():
        rows += [{"onside": False, "ko_era": era, "outcome": "recv", "spot": spot}] * 30
        rows += [{"onside": True, "ko_era": era, "outcome": "keep", "spot": 50}] * 5
    return pl.DataFrame(rows)


def test_fit_kickoff_by_era_drops_onside_and_normalises() -> None:
    km = M.fit_kickoff(kickoff_frame_eras({"tb25": 75, "tb30": 70}))
    assert set(km.by_era) == {"tb25", "tb30"} and km.counts == {"tb25": 30, "tb30": 30}, (
        "onside kicks are left out of the fit and the counts"
    )
    for era, np_ in km.by_era.items():
        assert np_.keep.sum() == 0.0, f"{era}: the onside 'keep' rows must not be in the model"
        assert np_.recv.sum() + np_.keep.sum() + np_.ret_td == pytest.approx(1.0)
    assert KickoffModel.from_dict(km.as_dict()).as_dict() == km.as_dict()


def test_kickoff_era_fallback() -> None:
    km = M.fit_kickoff(kickoff_frame_eras({"tb25": 75, "tb30": 70}))
    assert km.at(2024).mean_recv_spot() == pytest.approx(70.0)
    assert km.at(2025).mean_recv_spot() == pytest.approx(70.0), "no tb35 rows: the 2024 kickoff"
    assert km.at(2030).mean_recv_spot() == pytest.approx(70.0)
    assert km.at(2016).mean_recv_spot() == pytest.approx(75.0)
    assert km.at(2012).mean_recv_spot() == pytest.approx(75.0), "no tb20 rows: the nearest era"
    only35 = M.fit_kickoff(kickoff_frame_eras({"tb35": 65}))
    assert only35.at(2016).mean_recv_spot() == pytest.approx(65.0), (
        "falls forward if nothing earlier"
    )
    with pytest.raises(KeyError):
        KickoffModel({}).at(2025)


# --- the try after a touchdown ----------------------------------------------------------------
def test_fit_pat_uses_the_last_three_seasons_of_each_era() -> None:
    rows = []
    for season, rate in [(2012, 0.95), (2013, 0.95), (2014, 0.95), (2015, 0.50), (2016, 0.90),
                         (2017, 0.95), (2018, 0.95)]:  # fmt: skip
        good = round(rate * 20)
        rows += [{"season": season, "extra_point_attempt": 1.0, "two_point_attempt": 0.0,
                  "extra_point_result": "good" if k < good else "failed",
                  "two_point_conv_result": None} for k in range(20)]  # fmt: skip
        rows += [{"season": season, "extra_point_attempt": 0.0, "two_point_attempt": 1.0,
                  "extra_point_result": None,
                  "two_point_conv_result": "success" if k < 1 else "failure"}
                 for k in range(2)]  # fmt: skip
    plays = pl.DataFrame(rows, schema_overrides={"extra_point_result": pl.String,
                                                 "two_point_conv_result": pl.String})  # fmt: skip
    pat = M.fit_pat(plays)
    assert set(pat) == {"short", "long"}
    assert pat["short"]["xp_seasons"] == [2012, 2013, 2014]
    assert pat["long"]["xp_seasons"] == [2016, 2017, 2018], "the 2015 outlier is outside the last 3"
    assert pat["long"]["xp"] == pytest.approx((18 + 19 + 19) / 60)
    assert pat["short"]["xp"] == pytest.approx(0.95)
    assert pat["long"]["two"] == pytest.approx(0.5), "two-point rate: every season of the era"
    assert M.fit_pat(plays.filter(pl.col("season") >= 2015)).keys() == {"long"}


def test_pat_rates_default_when_an_era_is_missing() -> None:
    m = make_models(pat={"long": {"xp": 0.9, "two": 0.4}})
    assert m.pat_rates(2025) == {"xp": 0.9, "two": 0.4}
    assert m.pat_rates(2012) == {"xp": 0.94, "two": 0.48}, "no 'short' era fitted: the fallback"


# --- wp: monotone in the score and the spread ------------------------------------------------
@pytest.fixture(scope="module")
def wp_model():
    rng = np.random.default_rng(4)
    return M.fit_wp(make_wp_rows(rng, 8000), small_cfg(), threads=1, seed=7)


def _grid(base: np.ndarray, col: str, values: np.ndarray, tie: str) -> np.ndarray:
    """`base` rows repeated for every value of `col`, with the time-scaled twin `tie` recomputed
    from the same decay, as the training frames do."""
    ix = {f: i for i, f in enumerate(schema.WP_FEATURES)}
    out = np.repeat(base[:, None, :], len(values), axis=1).copy()  # (rows, values, features)
    decay = np.exp(-4.0 * (3600.0 - base[:, ix["game_seconds"]]) / 3600.0)[:, None]
    out[:, :, ix[col]] = values[None, :]
    if tie == "score":
        out[:, :, ix["diff_time_ratio"]] = values[None, :] / decay
    elif tie == "spread":
        out[:, :, ix["spread_time"]] = values[None, :] * decay
    elif tie == "ratio":
        out[:, :, ix["diff_time_ratio"]] = values[None, :]
    return out


def _random_base(n: int = 60, seed: int = 8) -> np.ndarray:
    df = make_wp_rows(np.random.default_rng(seed), n).drop("win")
    return M.matrix(df, schema.WP_FEATURES)


@pytest.mark.parametrize(
    ("col", "values", "tie"),
    [
        ("score_diff", np.arange(-24, 25, 1.0), "score"),
        ("spread", np.arange(-14, 14.5, 0.5), "spread"),
        ("diff_time_ratio", np.linspace(-300, 300, 41), "ratio"),
    ],
)
def test_wp_never_decreases_in_the_monotone_features(wp_model, col, values, tie) -> None:
    base = _random_base()
    grid = _grid(base, col, values, tie)
    flat = grid.reshape(-1, grid.shape[-1])
    p = wp_model.predict(flat, num_threads=1).reshape(len(base), len(values))
    steps = np.diff(p, axis=1)
    assert steps.min() >= -1e-12, f"wp fell as {col} rose: worst step {steps.min():.3g}"
    assert (p[:, -1] - p[:, 0]).min() > 0, f"wp should rise overall with {col}"


def test_wp_learns_the_obvious(wp_model) -> None:
    rng = np.random.default_rng(1)
    X = M.matrix(make_wp_rows(rng, 2000).drop("win"), schema.WP_FEATURES)
    p = wp_model.predict(X, num_threads=1)
    assert np.all((p > 0) & (p < 1))
    lead = X[:, 0] >= 10
    trail = X[:, 0] <= -10
    assert p[lead].mean() > 0.65 > 0.35 > p[trail].mean(), "a 10-point lead should be > 65%"


def test_wp_base_coefficients_are_non_negative_on_the_monotone_terms(wp_model) -> None:
    coef = dict(zip(M.WP_BASE_TERMS, wp_model.coef, strict=True))
    assert set(M.WP_BASE_MONOTONE) <= set(coef)
    for term in M.WP_BASE_MONOTONE:
        assert coef[term] >= 0, f"base coefficient on {term} is negative: {coef[term]}"
    assert coef["score_diff"] > 0 and coef["spread"] > 0, "the synthetic labels use both"


def test_fit_wp_base_drops_a_term_that_would_break_monotonicity() -> None:
    """Nonsense labels (the favorite loses) would give the base a negative spread coefficient:
    the term is dropped and the rest refit, so the model stays monotone instead of crashing a
    sparse backtest fold."""
    rng = np.random.default_rng(6)
    df = make_wp_rows(rng, 4000)
    z = -0.3 * df["spread"].to_numpy()  # the favorite loses: nonsense labels
    flipped = df.with_columns(
        pl.Series("win", (rng.random(4000) < 1 / (1 + np.exp(-z))).astype(int))
    )
    m = M.fit_wp(flipped, small_cfg(), threads=1, seed=7)
    for term, c in zip(M.WP_BASE_TERMS, m.coef, strict=True):
        if M.WP_BASE_MONOTONE.get(term):
            assert c >= 0, f"base coefficient on {term} is {c}"
    X = M.matrix(flipped, schema.WP_FEATURES)[:200].copy()
    lo, hi = X.copy(), X.copy()
    i, j = schema.WP_FEATURES.index("spread"), schema.WP_FEATURES.index("spread_time")
    hi[:, i] += 3.0
    hi[:, j] += 3.0 * (X[:, j] / np.where(X[:, i] == 0, 1.0, X[:, i]))  # same decay
    assert (m.predict(hi) >= m.predict(lo) - 1e-12).all(), "wp fell as the spread rose"


# --- fit_all: leakage guard, row counts, save / load -------------------------------------------
def test_fit_all_filters_every_frame_to_the_given_seasons() -> None:
    """A held-out season with absurd labels, mixed into every frame, must change nothing."""
    clean = synthetic_frames(FIT_SEASONS, n=500)
    leaked = synthetic_frames(FIT_SEASONS, n=500)
    held = synthetic_frames([2021], n=500, seed=99, absurd=True)
    leaked = {k: pl.concat([leaked[k], held[k]]) for k in leaked}
    assert all(leaked[k].height > clean[k].height for k in clean), "the leak is in every frame"
    a, rep = M.fit_all(leaked, FIT_SEASONS, small_cfg(), n_boot=2, log=lambda _s: None)
    b = fitted_bundle()  # the same fit without the held-out season
    assert rep.rows == {k: v.height for k, v in clean.items()}, "row counts exclude the held-out"
    assert a.meta["rows"] == rep.rows and a.meta["seasons"] == FIT_SEASONS
    X = M.matrix(clean["wp"], schema.WP_FEATURES)[:300]
    assert np.array_equal(a.wp_prob(X), b.wp_prob(X)), "wp changed: the held-out season leaked in"
    XG = M.matrix(clean["gain"], M.gain_features(False))[:300]
    assert np.array_equal(a.gain_probs(XG), b.gain_probs(XG)), "gain leaked"
    for i in range(2):
        assert np.array_equal(a.gain_probs(XG, boot=i), b.gain_probs(XG, boot=i)), f"boot {i}"
    assert a.fg.as_dict() == b.fg.as_dict(), "fg leaked"
    assert [m.as_dict() for m in a.fg_boot] == [m.as_dict() for m in b.fg_boot]
    assert a.punt.as_dict() == b.punt.as_dict(), "punt leaked"
    assert a.kickoff.as_dict() == b.kickoff.as_dict(), "kickoff leaked"
    assert a.pat == b.pat and a.pat["long"]["xp_seasons"] == FIT_SEASONS, "try rates leaked"
    XP = np.column_stack(
        [M.matrix(clean["pass"], schema.PASS_FEATURES[:8])[:200], np.full(200, 0.5),
         M.matrix(clean["pass"], schema.PASS_FEATURES[9:])[:200]]
    )  # fmt: skip
    assert np.array_equal(a.pass_prob(XP), b.pass_prob(XP)), "pass leaked"


def test_fit_all_with_a_subset_of_seasons_drops_the_rest() -> None:
    frames = synthetic_frames(FIT_SEASONS, n=500)
    models, rep = M.fit_all(frames, [2018, 2019], small_cfg(), n_boot=1, log=lambda _s: None)
    assert rep.rows["wp"] == 1000 and rep.rows["plays"] == 160
    assert models.pat["long"]["xp_seasons"] == [2018, 2019]
    assert models.meta["seasons"] == [2018, 2019] and len(models.gain_boot) == 1
    assert set(rep.seconds) >= {"wp", "gain", "fg", "punt", "kickoff", "pat", "pass", "boot"}


def test_fit_all_without_bootstrap() -> None:
    frames = synthetic_frames([2019], n=2500)
    models, rep = M.fit_all(frames, [2019], small_cfg(), n_boot=0, log=lambda _s: None)
    assert models.gain_boot == [] and models.fg_boot == [] and "boot" not in rep.seconds


def test_save_load_roundtrip_predicts_identically(tmp_path) -> None:
    models = fitted_bundle()
    folder = tmp_path / "bundle"
    files = M.save(models, folder)
    names = {p.relative_to(folder).as_posix() for p in files}
    assert set(M.MODEL_FILES) | {"meta.json", "gain_boot/00.txt", "gain_boot/01.txt"} <= names
    back = M.load(folder)
    frames = synthetic_frames(FIT_SEASONS, n=500)
    X = M.matrix(frames["wp"], schema.WP_FEATURES)[:400]
    assert np.allclose(back.wp_prob(X), models.wp_prob(X), rtol=0, atol=1e-12)
    XG = M.matrix(frames["gain"], M.gain_features(False))[:400]
    assert np.allclose(back.gain_probs(XG), models.gain_probs(XG), rtol=0, atol=1e-12)
    assert len(back.gain_boot) == 2 and len(back.fg_boot) == 2
    for i in range(2):
        assert np.allclose(
            back.gain_probs(XG, boot=i), models.gain_probs(XG, boot=i), rtol=0, atol=1e-12
        )
    d = np.array([25.0, 40.0, 55.0, 69.0])
    for a, b in [(back.fg, models.fg), *zip(back.fg_boot, models.fg_boot, strict=True)]:
        assert np.allclose(a.predict(d, [4] * 4, [0] * 4), b.predict(d, [4] * 4, [0] * 4))
        assert a.wind_fill == b.wind_fill
    assert back.punt.as_dict() == models.punt.as_dict()
    assert back.kickoff.as_dict() == models.kickoff.as_dict()
    assert back.pat == models.pat
    assert back.gain_ratings is False and back.meta["n_boot"] == 2
    assert back.meta["seasons"] == FIT_SEASONS
    XP = M.matrix(frames["wp"], schema.WP_FEATURES)[:50]
    assert back.wp.booster.feature_name() == schema.WP_FEATURES and XP.shape[1] == 16
    assert back.wp.coef == models.wp.coef and back.wp.intercept == models.wp.intercept, (
        "the wp logistic base must round-trip through wp_base.json"
    )


def test_load_names_the_missing_files(tmp_path) -> None:
    folder = tmp_path / "bundle"
    M.save(fitted_bundle(), folder)
    (folder / "pat.json").unlink()
    (folder / "wp.txt").unlink()
    with pytest.raises(FileNotFoundError, match=r"pat\.json.*wp\.txt|wp\.txt.*pat\.json"):
        M.load(folder)
    with pytest.raises(FileNotFoundError):
        M.load(tmp_path / "nowhere")


# --- the bundle's scoring helpers -------------------------------------------------------------
def test_gain_probs_applies_legal_gain_to_the_stub() -> None:
    m = make_models(gain={0: 0.5, 5: 0.5})
    X = np.zeros((2, len(schema.GAIN_FEATURES)))
    X[:, 2] = [5, 50]  # yardline_100 is column 2 of GAIN_FEATURES
    p = m.gain_probs(X)
    assert p[0, schema.TD_CLASS] == pytest.approx(0.5), "a 5-yard gain from the 5 is a touchdown"
    assert p[0, 5 - schema.GAIN_MIN] == 0.0
    assert p[1, schema.TD_CLASS] == 0.0 and p[1, 5 - schema.GAIN_MIN] == 0.5
    assert schema.GAIN_FEATURES[2] == "yardline_100"
    assert isinstance(m.gain, StubGain) and m.gain_features == schema.GAIN_FEATURES


def test_simple_kickoff_helper_is_a_valid_model() -> None:
    km = simple_kickoff(spot=75)
    assert km.at(2025).mean_recv_spot() == 75.0
