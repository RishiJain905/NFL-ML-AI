"""Game feature table: as-of alignment and leakage tests (documentation/04 rule 6; plan P03)."""

import polars as pl
import pytest
from conftest import LEAGUE_TEAMS, make_league

from nflengine.features.asof import AsOf, before_expr
from nflengine.features.game import (
    PREGAME_COLS,
    TARGET_COLS,
    build_game_features,
    current_lines,
    league_scoring,
    team_points_form,
)
from nflengine.features.leakage import LeakageError, assert_future_invariant

SEASONS = (2020, 2021, 2022)
WEEKS = 6


def games_fixture() -> pl.DataFrame:
    g = make_league()["games"]
    return g.with_columns(
        pl.when((pl.col("week") == 4) & (pl.col("home_team") == "ARI"))
        .then(14)
        .otherwise(7)
        .cast(pl.Int32)
        .alias("home_rest"),
        pl.lit(7, dtype=pl.Int32).alias("away_rest"),
        (pl.col("week") % 2).cast(pl.Int32).alias("div_game"),
        pl.lit("outdoors").alias("roof"),
        pl.when(pl.col("game_id") == "2022_06_ATL_BAL")
        .then(None)
        .otherwise(pl.lit(2.5))
        .alias("spread_line"),
        pl.when(pl.col("game_id") == "2022_06_ATL_BAL")
        .then(None)
        .otherwise(pl.lit(44.5))
        .alias("total_line"),
        pl.lit(-150, dtype=pl.Int32).alias("home_moneyline"),
        pl.lit(130, dtype=pl.Int32).alias("away_moneyline"),
        (pl.col("week") == 2).alias("neutral_site"),
    )


def feature_tables() -> tuple[pl.DataFrame, pl.DataFrame]:
    rows, elo = [], []
    for s in SEASONS:
        for w in range(1, WEEKS + 1):
            for i, t in enumerate(LEAGUE_TEAMS):
                rows.append(
                    {
                        "season": s,
                        "week": w,
                        "team": t,
                        "net_epa": i / 100,
                        "off_epa": w / 100,
                        "def_epa": -w / 200,
                        "off_pass_epa": i / 50,
                        "def_pass_epa": 0.0,
                        "off_rush_epa": 0.0,
                        "def_rush_epa": i / 80,
                        "net_sr": 0.0,
                        "prior_weight": 1 / w,
                    }
                )
                elo.append({"season": s, "week": w, "team": t, "elo": 1500.0 + 25 * i})
    cast = [pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)]
    return pl.DataFrame(rows).with_columns(cast), pl.DataFrame(elo).with_columns(cast)


def build(games=None, ratings=None, elo=None, lines=None):
    r, e = feature_tables()
    return build_game_features(
        games if games is not None else games_fixture(),
        ratings if ratings is not None else r,
        elo if elo is not None else e,
        lines=lines,
        min_season=2020,
    )


def test_joins_the_rating_row_of_the_games_own_week() -> None:
    f = build()
    assert f.height == games_fixture().height
    # off_epa = week/100 for every team, so off_sum = 2 * week / 100 only if aligned
    assert ((f["off_sum"] - 2 * f["week"] / 100).abs() < 1e-12).all()
    idx = {t: i for i, t in enumerate(LEAGUE_TEAMS)}
    for r in f.head(8).iter_rows(named=True):
        assert r["net_diff"] == pytest.approx((idx[r["home_team"]] - idx[r["away_team"]]) / 100)
        assert r["elo_diff"] == pytest.approx(0.25 * (idx[r["home_team"]] - idx[r["away_team"]]))


def test_neutral_site_has_no_home_field() -> None:
    f = build()
    n = f.filter(pl.col("week") == 2)
    assert (n["hfa"] == 0).all()
    r = n.row(0, named=True)
    diff = 25 * (LEAGUE_TEAMS.index(r["home_team"]) - LEAGUE_TEAMS.index(r["away_team"]))
    assert r["elo_prob"] == pytest.approx(1 / (1 + 10 ** (-diff / 400)))
    assert (f.filter(pl.col("week") != 2)["hfa"] == 1).all()


def test_rest_bye_and_targets() -> None:
    f = build()
    bye = f.filter((pl.col("week") == 4) & (pl.col("home_team") == "ARI"))
    assert bye["bye_diff"].to_list() == [1.0] * bye.height
    assert bye["rest_diff"].to_list() == [7.0] * bye.height
    played = f.filter(pl.col("completed"))
    assert (played["margin"] == played["home_score"] - played["away_score"]).all()
    assert played["home_win"].is_in([0.0, 0.5, 1.0]).all()
    unplayed = f.filter(~pl.col("completed"))
    assert unplayed.height > 0
    assert unplayed.select(TARGET_COLS).null_count().row(0) == (unplayed.height,) * 5


def test_rolling_points_use_only_earlier_games() -> None:
    g = games_fixture()
    f = build(g)
    row = f.filter((pl.col("season") == 2021) & (pl.col("week") == 3)).row(0, named=True)
    team = row["home_team"]
    earlier = g.filter(
        pl.col("completed")
        & ((pl.col("home_team") == team) | (pl.col("away_team") == team))
        & before_expr(AsOf(2021, 3))
    ).sort("kickoff_utc")
    pf = earlier.select(
        pl.when(pl.col("home_team") == team).then("home_score").otherwise("away_score")
    ).to_series()
    pa = earlier.select(
        pl.when(pl.col("home_team") == team).then("away_score").otherwise("home_score")
    ).to_series()
    assert row["home_pf_avg"] == pytest.approx(pf.tail(8).mean())
    assert row["home_pa_avg"] == pytest.approx(pa.tail(8).mean())
    first = f.filter((pl.col("season") == 2020) & (pl.col("week") == 1))
    assert first["home_pf_avg"].null_count() == first.height  # no history yet
    assert (first["pts_base_home"] == first["league_ppg"]).all()  # falls back to the league


def test_outcomes_from_the_key_week_on_never_reach_features() -> None:
    r, e = feature_tables()
    protect = {*PREGAME_COLS, "completed", "home_rest", "away_rest"}

    def run(inputs):
        out = build_game_features(inputs["games"], r, e, min_season=2020)
        return out.drop([c for c in TARGET_COLS if c in out.columns])

    for key in (AsOf(2021, 1), AsOf(2021, 4), AsOf(2022, 3)):
        assert_future_invariant(
            run, {"games": games_fixture()}, key, protect=protect, sort_by=["game_id"]
        )


def test_feature_rows_after_the_games_week_are_never_used() -> None:
    g = games_fixture()
    r, e = feature_tables()
    key = AsOf(2021, 3)
    later = ~before_expr(AsOf(2021, 4))  # rows keyed after the key's week
    num = [c for c in r.columns if c not in ("season", "week", "team")]
    r2 = r.with_columns([pl.when(later).then(pl.col(c) * -9 + 3).otherwise(pl.col(c)) for c in num])
    e2 = e.with_columns(pl.when(later).then(pl.col("elo") + 999).otherwise(pl.col("elo")))
    a = build(g, r, e).filter(before_expr(AsOf(2021, 4))).sort("game_id")
    b = build(g, r2, e2).filter(before_expr(AsOf(2021, 4))).sort("game_id")
    assert a.equals(b)
    # ...and the check has teeth: changing the key week's own rows does move features
    own = (pl.col("season") == key.season) & (pl.col("week") == key.week)
    r3 = r.with_columns(pl.when(own).then(pl.col("off_epa") + 1).otherwise(pl.col("off_epa")))
    c = build(g, r3, e).filter(before_expr(AsOf(2021, 4))).sort("game_id")
    with pytest.raises(AssertionError):
        assert a.equals(c)


def test_leakage_helper_catches_a_leaky_builder() -> None:
    def leaky(inputs):
        g = inputs["games"]
        nxt = g.select(
            pl.col("season"),
            (pl.col("week") - 1).alias("week"),
            pl.col("home_score").mean().over("season", "week").alias("next_pts"),
        ).unique(["season", "week"])
        return g.select("season", "week", "game_id").join(nxt, on=["season", "week"], how="left")

    with pytest.raises(LeakageError):
        assert_future_invariant(
            leaky,
            {"games": games_fixture()},
            AsOf(2021, 3),
            protect={"completed"},
            sort_by=["game_id"],
        )


def test_market_columns_and_line_fallback() -> None:
    lines = pl.DataFrame(
        {
            "game_id": ["2022_06_ATL_BAL"] * 3,
            "source": ["odds_api", "odds_api", "espn"],
            "provider": ["a", "b", "DraftKings"],
            "home_spread": [3.0, 4.0, 9.0],
            "total": [41.0, 43.0, 50.0],
        }
    )
    f = build(lines=lines)
    row = f.filter(pl.col("game_id") == "2022_06_ATL_BAL").row(0, named=True)
    assert row["spread_line"] == 3.5 and row["total_line"] == 42.0
    assert row["market_source"] == "odds_api"
    assert row["mkt_home_points"] == pytest.approx((42.0 + 3.5) / 2)
    other = f.filter(pl.col("game_id") != "2022_06_ATL_BAL").row(0, named=True)
    assert other["market_source"] == "nflverse_schedules"
    assert 0 < other["market_ml_prob"] < 1


def test_current_lines_priority() -> None:
    ln = pl.DataFrame(
        {
            "game_id": ["a", "a", "b"],
            "source": ["nflverse_schedules", "espn", "espn"],
            "home_spread": [1.0, 7.0, -2.0],
            "total": [40.0, 50.0, 45.0],
        }
    )
    out = current_lines(ln).sort("game_id")
    assert out["ln_spread"].to_list() == [1.0, -2.0]
    assert out["ln_source"].to_list() == ["nflverse_schedules", "espn"]
    assert current_lines(None).is_empty()


def test_form_and_league_tables_shapes() -> None:
    g = games_fixture()
    form = team_points_form(g)
    assert set(form.columns) == {"team", "t", "pf_avg", "pa_avg"}
    lg = league_scoring(g)
    assert lg["league_ppg"].min() > 0


def test_matchups_credit_the_offense_facing_a_weak_defense() -> None:
    # Fixture: off_pass = i/50, def_pass = 0; off_rush = 0, def_rush = i/80 (EPA allowed:
    # higher = worse defense). Facing a worse defense must raise your matchup edge.
    f = build()
    idx = {t: i for i, t in enumerate(LEAGUE_TEAMS)}
    for r in f.iter_rows(named=True):
        h, a = idx[r["home_team"]], idx[r["away_team"]]
        assert r["pass_matchup"] == pytest.approx((h - a) / 50)
        assert r["rush_matchup"] == pytest.approx((a - h) / 80)  # away defense allows more
