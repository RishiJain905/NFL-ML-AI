import datetime as dt

import numpy as np
import polars as pl
import pytest
from conftest import LEAGUE_TEAMS, TRUE_DEF, TRUE_OFF, make_league

from nflengine.features.asof import AsOf
from nflengine.features.leakage import assert_future_invariant
from nflengine.models.ratings import (
    DEF0,
    I_HFA,
    I_MU,
    N_COEF,
    OFF0,
    TEAMS,
    RatingParams,
    compute_ratings,
    play_weights,
    prepare_inputs,
    qb_changes,
    rating_plays,
    ratings_frame,
    week1_tuesdays,
)

P = RatingParams(half_life_weeks=3, prior_regression=0.3, ridge_alpha=80, qb_change_regression=0.5)


def _run(lg, params=P, seasons=(2020, 2021, 2022), splits=("all", "pass", "rush")):
    rp = rating_plays(lg["plays"])
    inp = prepare_inputs(rp, lg["games"], lg["depth_charts"], params, list(seasons), splits)
    return rp, inp, compute_ratings(inp, params, splits)


def _dense_ridge(rp, params, target, alpha_vec, as_of_week, season, decay=True):
    """Independent reference: explicit design matrix + weighted ridge toward `target`."""
    df = rp.filter((pl.col("season") == season) & (pl.col("week") < as_of_week))
    w = play_weights(df, params.garbage_weight, params.garbage_wp).to_numpy()
    if decay:
        w = w * params.decay ** (as_of_week - 1 - df["week"].to_numpy())
    X = np.zeros((df.height, N_COEF))
    X[:, I_MU] = 1
    X[:, I_HFA] = df["home"].to_numpy()
    idx = {t: i for i, t in enumerate(TEAMS)}
    rows = np.arange(df.height)
    X[rows, [OFF0 + idx[t] for t in df["off"]]] = 1
    X[rows, [DEF0 + idx[t] for t in df["def"]]] = 1
    y = df["epa"].to_numpy()
    lhs = X.T @ (X * w[:, None]) + np.diag(alpha_vec)
    beta = np.linalg.solve(lhs, X.T @ (w * y) + alpha_vec * target)
    for start in (OFF0, DEF0):
        m = beta[start : start + 32].mean()
        beta[start : start + 32] -= m
        beta[I_MU] += m
    return beta


def test_params_validation_and_config() -> None:
    p = RatingParams.from_config({"half_life_weeks": 8, "ridge_alpha": 300}, prior_regression=0.1)
    assert (p.half_life_weeks, p.ridge_alpha, p.prior_regression) == (8, 300, 0.1)
    assert p.decay == pytest.approx(0.5 ** (1 / 8))
    with pytest.raises(ValueError, match="unknown"):
        RatingParams.from_config({"half_life": 4})
    with pytest.raises(ValueError):
        RatingParams(prior_regression=1.5)
    with pytest.raises(ValueError):
        RatingParams(ridge_alpha=0)


def test_rating_plays_filters_and_flags(league) -> None:
    rp = rating_plays(league["plays"])
    raw = league["plays"]
    assert rp.height == raw.filter(pl.col("play_id") != 999).height  # junk rows dropped
    assert rp["epa"].max() < 5  # the junk rows carried epa = 5
    assert rp.filter(pl.col("dropback")).height == raw.filter(pl.col("qb_dropback") == 1).height
    neutral = raw.with_columns(pl.lit("Neutral").alias("location"))
    assert not rating_plays(neutral)["home"].any()


def test_first_season_matches_independent_ridge(league) -> None:
    rp, inp, res = _run(league, splits=("all",))
    k = res.keys.index((2020, 4))
    ref = _dense_ridge(rp, P, np.zeros(N_COEF), np.full(N_COEF, P.ridge_alpha), 4, 2020)
    np.testing.assert_allclose(res.coefs[("all", "epa")][k], ref, atol=1e-9)
    # Reproducible: same inputs, same numbers.
    again = compute_ratings(inp, P, ("all",))
    np.testing.assert_array_equal(res.coefs[("all", "epa")], again.coefs[("all", "epa")])


def test_prior_chain_hfa_pin_and_qb_pull(league) -> None:
    rp, _, res = _run(league, splits=("all",))
    full = res.finals[2020][("all", "epa")]
    # The full-season fit is an unweighted ridge toward average over all of 2020.
    ref_full = _dense_ridge(
        rp, P, np.zeros(N_COEF), np.full(N_COEF, P.ridge_alpha), 99, 2020, decay=False
    )
    np.testing.assert_allclose(full, ref_full, atol=1e-9)
    wk1 = res.coefs[("all", "epa")][res.keys.index((2021, 1))]
    keep = 1 - P.prior_regression
    i = {t: TEAMS.index(t) for t in LEAGUE_TEAMS}
    # Week 1 is exactly the prior: last season pulled toward average, with ATL's offense
    # (QB change) pulled further; then re-centered so team effects sum to zero.
    off = full[OFF0:DEF0] * keep
    off[i["ATL"]] *= 1 - P.qb_change_regression
    off -= off.mean()
    np.testing.assert_allclose(wk1[OFF0:DEF0], off, atol=1e-9)
    np.testing.assert_allclose(wk1[DEF0:], full[DEF0:] * keep, atol=1e-9)
    assert abs(wk1[OFF0 + i["ATL"]]) < abs(keep * full[OFF0 + i["ATL"]])
    # Home field in 2021 is pinned to 2020's full-season estimate, all season long.
    hfa_2021 = [
        res.coefs[("all", "epa")][k][I_HFA] for k, (s, _) in enumerate(res.keys) if s == 2021
    ]
    np.testing.assert_allclose(hfa_2021, full[I_HFA], atol=1e-6)


def test_independent_ridge_with_prior_target(league) -> None:
    rp, _, res = _run(league, splits=("all",))
    full = res.finals[2020][("all", "epa")]
    qb = np.array([t == "ATL" for t in TEAMS])
    target = np.zeros(N_COEF)
    target[I_MU] = full[I_MU]
    target[I_HFA] = full[I_HFA]
    keep = 1 - P.prior_regression
    target[OFF0:DEF0] = full[OFF0:DEF0] * keep * np.where(qb, 1 - P.qb_change_regression, 1)
    target[DEF0:] = full[DEF0:] * keep
    week = 4
    target[OFF0:] *= P.decay ** (week - 1)  # the prior fades with the data's half-life
    alpha = np.full(N_COEF, P.ridge_alpha)
    alpha[I_HFA] = 1e9
    ref = _dense_ridge(rp, P, target, alpha, week, 2021)
    got = res.coefs[("all", "epa")][res.keys.index((2021, week))]
    np.testing.assert_allclose(got, ref, atol=1e-7)


def test_recovers_true_effects_with_enough_data() -> None:
    # 11 games x 500 plays: the standard error of a team-effect difference is about 0.011.
    lg = make_league(seasons=(2020,), weeks=12, plays_per_side=500, in_progress=None)
    p = RatingParams(half_life_weeks=50, prior_regression=0.3, ridge_alpha=20)
    _, _, res = _run(lg, p, seasons=(2020,), splits=("all",))
    r = ratings_frame(res).filter((pl.col("week") == 12) & pl.col("team").is_in(LEAGUE_TEAMS))
    got_off = dict(r.select("team", "off_epa").iter_rows())
    got_def = dict(r.select("team", "def_epa").iter_rows())
    # Only differences between teams are identified; compare after centering on the four.
    for truth, got in ((TRUE_OFF, got_off), (TRUE_DEF, got_def)):
        t_mean = np.mean(list(truth.values()))
        g_mean = np.mean([got[t] for t in LEAGUE_TEAMS])
        for t in LEAGUE_TEAMS:
            assert got[t] - g_mean == pytest.approx(truth[t] - t_mean, abs=0.04)
    assert r.sort("off_epa", descending=True)["team"][0] == "ARI"  # best offense
    assert r.sort("def_epa")["team"][0] == "BAL"  # best defense allows the least EPA


def test_ratings_frame_shape_and_week_one_is_prior(league) -> None:
    _, _, res = _run(league)
    r = ratings_frame(res, qb_changes(league["games"], league["depth_charts"]))
    assert r.height == len(res.keys) * 32
    assert r.group_by("season", "week").len()["len"].unique().to_list() == [32]
    assert r.filter(pl.col("season") == 2022)["week"].max() == 5
    core = [c for c in r.columns if c.startswith(("off_", "def_", "net_"))]
    assert r.select(core).null_count().sum_horizontal()[0] == 0
    first = r.filter((pl.col("season") == 2020) & (pl.col("week") == 1))
    assert first["net_epa"].abs().max() == 0  # no prior for the first season, no data yet
    assert (r.filter(pl.col("week") == 1)["plays_observed"] == 0).all()
    # Week 1 is all prior; its share then falls as plays arrive and the prior fades.
    assert (r.filter(pl.col("week") == 1)["prior_weight"] == 1).all()
    ari = r.filter((pl.col("season") == 2020) & (pl.col("team") == "ARI")).sort("week")
    assert ari["prior_weight"].diff().drop_nulls().max() < 0
    assert r.filter((pl.col("season") == 2021) & (pl.col("team") == "ATL"))["qb_change_prior"].all()
    # Team effects sum to zero each week.
    sums = r.group_by("season", "week").agg(pl.col("off_epa").sum(), pl.col("def_pass_sr").sum())
    assert sums["off_epa"].abs().max() < 1e-9 and sums["def_pass_sr"].abs().max() < 1e-9


def test_qb_changes(league) -> None:
    qb = qb_changes(league["games"], league["depth_charts"])
    changed = qb.filter(pl.col("qb_changed")).select("season", "team").rows()
    assert changed == [(2021, "ATL")]
    assert qb_changes(league["games"], None).is_empty()


def test_week1_tuesday(league) -> None:
    # 2021's first kickoff is Friday 2021-09-10 (17:00 UTC) -> Tuesday 2021-09-07.
    t = dict(week1_tuesdays(league["games"]).iter_rows())
    assert t[2021] == dt.date(2021, 9, 7)


def test_qb_change_ignores_depth_charts_published_after_tuesday(league) -> None:
    # A dated week-1 chart from game day (after the Tuesday run) can't be used.
    depth = league["depth_charts"].with_columns(
        pl.when((pl.col("season") == 2021) & (pl.col("team") == "ATL"))
        .then(pl.lit(dt.date(2021, 9, 10)))
        .otherwise(None)
        .alias("snap_date")
    )
    qb = qb_changes(league["games"], depth)
    atl = qb.filter((pl.col("season") == 2021) & (pl.col("team") == "ATL")).row(0, named=True)
    assert atl["qb_new_id"] is None and atl["qb_changed"] is False
    # Published on the Tuesday itself: fine.
    on_time = depth.with_columns(
        pl.when(pl.col("snap_date").is_not_null())
        .then(pl.lit(dt.date(2021, 9, 7)))
        .otherwise(None)
        .alias("snap_date")
    )
    assert qb_changes(league["games"], on_time).filter(pl.col("qb_changed")).height == 1


def _build(inputs):
    rp = rating_plays(inputs["plays"])
    inp = prepare_inputs(rp, inputs["games"], inputs["depth_charts"], P, [2020, 2021, 2022])
    return ratings_frame(compute_ratings(inp, P))


@pytest.mark.parametrize("key", [AsOf(2020, 3), AsOf(2021, 2), AsOf(2022, 4)])
def test_ratings_do_not_depend_on_the_future(league, key) -> None:
    # Ids, schedule flags and the week-1 depth chart (preseason information) stay as they are.
    out = assert_future_invariant(
        _build,
        league,
        key,
        protect={"play_id", "completed", "depth_rank", "neutral_site"},
    )
    assert out.height > 0
