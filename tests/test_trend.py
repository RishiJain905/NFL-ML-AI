import numpy as np
import polars as pl
import pytest
from conftest import make_league

from nflengine.features.asof import AsOf
from nflengine.features.leakage import assert_future_invariant
from nflengine.models.ratings import (
    RatingParams,
    compute_ratings,
    net_lookup,
    prepare_inputs,
    rating_plays,
    ratings_frame,
)
from nflengine.models.ratings_eval import game_targets, team_game_epa
from nflengine.models.trend import (
    drivers_table,
    pass_share,
    team_game_margins,
    trend_table,
    trend_validation_frame,
)

P = RatingParams(half_life_weeks=4, prior_regression=0.2, ridge_alpha=100)
SEASONS = (2017, 2018, 2019, 2020, 2021)


def _pipeline(inputs, seasons=SEASONS):
    rp = rating_plays(inputs["plays"])
    inp = prepare_inputs(rp, inputs["games"], inputs["depth_charts"], P, list(seasons))
    res = compute_ratings(inp, P)
    ratings = ratings_frame(res)
    tg = team_game_epa(rp, P.garbage_weight, P.garbage_wp)
    margins = team_game_margins(game_targets(inputs["games"], tg), net_lookup(res))
    return rp, ratings, margins, trend_table(ratings, margins, inputs["games"])


@pytest.fixture(scope="module")
def built():
    lg = make_league(seasons=SEASONS, weeks=8, plays_per_side=30, in_progress=2021)
    return lg, *_pipeline(lg)


def test_trend_delta_is_three_week_change(built) -> None:
    _, _, ratings, _, trends = built
    t = trends.filter((pl.col("season") == 2019) & (pl.col("team") == "BAL"))
    r = dict(
        ratings.filter((pl.col("season") == 2019) & (pl.col("team") == "BAL"))
        .select("week", "net_epa")
        .iter_rows()
    )
    for row in t.iter_rows(named=True):
        w = row["week"]
        if w <= 3:
            assert row["trend_delta"] is None
        else:
            assert row["trend_delta"] == pytest.approx(r[w] - r[w - 3])


def test_perf_vs_expected_uses_last_three_games_before_the_key(built) -> None:
    _, _, _, margins, trends = built
    m = margins.filter((pl.col("season") == 2019) & (pl.col("team") == "ARI")).sort("week")
    resid = dict(zip(m["week"], (m["actual"] - m["expected"]).to_list(), strict=True))
    for row in trends.filter((pl.col("season") == 2019) & (pl.col("team") == "ARI")).iter_rows(
        named=True
    ):
        prior = [resid[w] for w in sorted(resid) if w < row["week"]][-3:]
        assert row["pve_games"] == len(prior)
        if prior:
            assert row["perf_vs_expected"] == pytest.approx(np.mean(prior))
        else:
            assert row["perf_vs_expected"] is None


def test_margins_are_symmetric(built) -> None:
    _, _, _, margins, _ = built
    g = margins.group_by("game_id").agg(pl.col("actual").sum(), pl.col("expected").sum(), pl.len())
    assert (g["len"] == 2).all()
    assert g["actual"].abs().max() < 1e-12 and g["expected"].abs().max() < 1e-12


def test_direction_bands_come_from_earlier_seasons_only(built) -> None:
    _, _, _, _, trends = built
    by_season = (
        trends.group_by("season")
        .agg(pl.col("band_low").first(), pl.col("band_high").first())
        .sort("season")
    )
    assert by_season.filter(pl.col("season") <= 2019)["band_low"].null_count() == 3
    later = by_season.filter(pl.col("season") >= 2020)
    assert later["band_low"].null_count() == 0
    assert (later["band_low"] < later["band_high"]).all()
    t = trends.drop_nulls("direction")
    up = t.filter(pl.col("direction") == "up")
    down = t.filter(pl.col("direction") == "down")
    assert (up["trend_delta"] > up["band_high"]).all()
    assert (down["trend_delta"] < down["band_low"]).all()
    assert set(t["direction"].unique()) <= {"up", "down", "stable"}
    # 2020's band = 20th/80th percentiles of 2017-2019 regular-season deltas.
    hist = trends.filter(pl.col("season") < 2020).drop_nulls("trend_delta")["trend_delta"]
    b = by_season.filter(pl.col("season") == 2020).row(0, named=True)
    assert b["band_low"] == pytest.approx(hist.quantile(0.2, "linear"))
    assert b["band_high"] == pytest.approx(hist.quantile(0.8, "linear"))


def test_drivers_rank_parts_by_contribution() -> None:
    base = {"season": [2020, 2020], "week": [1, 4], "team": ["KC", "KC"]}
    ratings = pl.DataFrame(
        {
            **base,
            "off_pass_epa": [0.00, 0.10],  # +0.10 better, pass share 0.6 -> 0.06
            "off_rush_epa": [0.00, -0.05],  # -0.05 worse, rush share 0.4 -> -0.02
            "def_pass_epa": [0.00, 0.02],  # allows more: worse, -0.02 * 0.6 = -0.012
            "def_rush_epa": [0.00, -0.10],  # allows less: better, 0.10 * 0.4 = 0.04
        }
    ).with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))
    shares = pl.DataFrame({"season": [2020], "week": [4], "pass_share": [0.6]}).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )
    d = drivers_table(ratings, shares, top=3)
    assert d["part"].to_list() == ["pass_offense", "rush_defense", "rush_offense"]
    assert d["contribution"].to_list() == pytest.approx([0.06, 0.04, -0.02])
    assert d["effect"].to_list() == ["better", "better", "worse"]
    assert d["rank"].to_list() == [1, 2, 3]


def test_validation_target_is_the_next_three_weeks(built) -> None:
    lg, _, _, margins, trends = built
    vf = trend_validation_frame(trends, margins, lg["games"])
    row = vf.filter((pl.col("season") == 2019) & (pl.col("week") == 4)).row(0, named=True)
    m = margins.filter(
        (pl.col("season") == 2019)
        & (pl.col("team") == row["team"])
        & pl.col("week").is_between(4, 6)
    )
    adj = (m["actual"] + m["opp_net"] - m["hfa_term"]).mean()
    assert row["next_perf"] == pytest.approx(adj)
    # 8-week seasons: week 7 still has 2 games ahead (7, 8), week 8 only 1 -> dropped.
    assert vf.filter(pl.col("season") == 2019)["week"].max() == 7
    assert vf.null_count().sum_horizontal()[0] == 0


def test_validation_window_is_weeks_not_games() -> None:
    def margin(week):
        return {
            "season": 2020,
            "week": week,
            "game_id": f"g{week}",
            "game_type": "REG",
            "team": "KC",
            "opp": "BUF",
            "actual": float(week),
            "expected": 0.0,
            "team_net": 0.0,
            "opp_net": 0.0,
            "hfa_term": 0.0,
        }

    # KC is on bye in week 6: weeks [5, 8) hold games 5 and 7 only; week 8 is outside.
    margins = pl.DataFrame([margin(w) for w in (5, 7, 8)]).with_columns(
        pl.col("season", "week").cast(pl.Int32)
    )
    trends = pl.DataFrame(
        {
            "season": [2020],
            "week": [5],
            "team": ["KC"],
            "net_epa": [0.1],
            "trend_delta": [0.02],
            "perf_vs_expected": [0.0],
        }
    ).with_columns(pl.col("season", "week").cast(pl.Int32))
    games = pl.DataFrame({"season": [2020], "week": [5], "game_type": ["REG"]})
    vf = trend_validation_frame(trends, margins, games)
    assert vf["next_perf"].to_list() == pytest.approx([6.0])  # mean of weeks 5 and 7


def test_pass_share_is_as_of() -> None:
    def plays(season, week, n, drop):
        return [{"season": season, "week": week, "dropback": i < drop} for i in range(n)]

    rp = pl.DataFrame(plays(2020, 1, 10, 5) + plays(2020, 2, 10, 10)).with_columns(
        pl.col("season", "week").cast(pl.Int32)
    )
    keys = pl.DataFrame({"season": [2020, 2020, 2020, 2021], "week": [1, 2, 3, 1]}).with_columns(
        pl.col("season", "week").cast(pl.Int32)
    )
    s = dict(
        ((r["season"], r["week"]), r["pass_share"])
        for r in pass_share(rp, keys).iter_rows(named=True)
    )
    assert s[(2020, 1)] == pytest.approx(0.6)  # nothing seen yet: the default
    assert s[(2020, 2)] == pytest.approx(0.5)  # week 1 only, never week 2 itself
    assert s[(2020, 3)] == pytest.approx(0.75)
    assert s[(2021, 1)] == pytest.approx(0.75)  # last season


def _drivers_build(inputs):
    rp, ratings, _, _ = _pipeline(inputs)
    return drivers_table(ratings, pass_share(rp, ratings))


def test_drivers_do_not_depend_on_the_future(built) -> None:
    for key in (AsOf(2019, 6), AsOf(2021, 5)):
        assert_future_invariant(
            _drivers_build, built[0], key, protect={"play_id", "completed", "depth_rank"}
        )


def _trend_build(inputs):
    return _pipeline(inputs)[3]


def test_trends_do_not_depend_on_the_future(built) -> None:
    lg = built[0]
    for key in (AsOf(2019, 5), AsOf(2021, 4)):
        assert_future_invariant(
            _trend_build, lg, key, protect={"play_id", "completed", "depth_rank"}
        )
