"""Team stat features (P08): labels, rolling form, baselines, ratings / market / weather /
schedule families, and a future-invariance check per family.

The synthetic league is 4 teams, 3 seasons x 8 weeks, two games a week (a round robin); the last
season is in progress (weeks 1-5 played, the rest scheduled), with a forecast for week 6.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from nflengine.features import team_stats as TS

TEAMS = ["AAA", "BBB", "CCC", "DDD"]
PAIRINGS = [((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2))]
SEASONS = (2020, 2021, 2022)
WEEKS = 8
PLAYED_THROUGH = 5  # the in-progress season (2022) has weeks 1-5 played
FIRST = 2021  # first season with feature rows (2020 only warms the windows up)


def tk(season: int, week: int) -> int:
    return (season - 2000) * 22 + week


def make_world(seed: int = 3, seasons: tuple[int, ...] = SEASONS) -> TS.TeamInputs:
    rng = np.random.default_rng(seed)
    games, box, pace, rat, gf, ctx = [], [], [], [], [], []
    for season in seasons:
        start = dt.datetime(season, 9, 10, 17, tzinfo=dt.UTC)
        for week in range(1, WEEKS + 1):
            for gi, (a, b) in enumerate(PAIRINGS[(week - 1) % 3]):
                home, away = (a, b) if week % 2 else (b, a)
                h, v = TEAMS[home], TEAMS[away]
                done = not (season == seasons[-1] and week > PLAYED_THROUGH)
                gid = f"{season}_{week:02d}_{v}_{h}"
                dome = (home + week) % 2 == 0
                games.append(
                    {
                        "game_id": gid,
                        "season": season,
                        "week": week,
                        "kickoff_utc": start + dt.timedelta(days=7 * (week - 1), hours=3 * gi),
                        "home_team": h,
                        "away_team": v,
                        "completed": done,
                        "neutral_site": False,
                        "div_game": int(week % 3 == 0),
                        "roof": "dome" if dome else "outdoors",
                        "temp": None if dome or not done else float(rng.integers(30, 80)),
                        "wind": None if dome or not done else float(rng.integers(0, 20)),
                        "home_rest": 7 + int(rng.integers(0, 3)),
                        "away_rest": 7 + int(rng.integers(0, 3)),
                    }
                )
                spread = float(rng.integers(-7, 8)) + 0.5
                total = float(rng.integers(38, 52)) + 0.5
                gf.append(
                    {
                        "game_id": gid,
                        "spread_line": spread,
                        "total_line": total,
                        "mkt_home_points": (total + spread) / 2,
                        "mkt_away_points": (total - spread) / 2,
                    }
                )
                for team, _opp in ((h, v), (v, h)):
                    ctx.append(
                        {
                            "game_id": gid,
                            "team": team,
                            "ctx_pred_points": float(rng.uniform(17, 28)),
                            "ctx_pred_opp_points": float(rng.uniform(17, 28)),
                            "ctx_exp_margin": float(rng.uniform(-6, 6)),
                        }
                    )
                    if done:
                        box.append(
                            {
                                "game_id": gid,
                                "team": team,
                                "passing_yards": int(rng.integers(120, 380)),
                                "rushing_yards": int(rng.integers(40, 200)),
                                "sacks_suffered": int(rng.integers(0, 6)),
                                "passing_interceptions": int(rng.integers(0, 3)),
                                "fumbles_lost_total": int(rng.integers(0, 3)),
                            }
                        )
                        pace.append(
                            {
                                "game_id": gid,
                                "team": team,
                                "team_plays": int(rng.integers(50, 80)),
                                "team_dropbacks": int(rng.integers(25, 50)),
                                "team_rushes": int(rng.integers(15, 35)),
                                "team_proe": float(rng.normal(0, 0.05)),
                            }
                        )
        for week in range(1, WEEKS + 1):
            for t in TEAMS:
                rat.append(
                    {
                        "season": season,
                        "week": week,
                        "team": t,
                        **{
                            c: float(rng.normal(0, 0.1)) if c != "prior_weight" else 0.5
                            for c in TS.RATING_COLS
                        },
                    }
                )
    forecast = pl.DataFrame(
        [
            {
                "game_id": g["game_id"],
                "temp_f": 41.0,
                "wind_mph": 12.0,
                "pulled_at": dt.datetime(2022, 10, 1, tzinfo=dt.UTC),
            }
            for g in games
            if g["season"] == seasons[-1] and g["week"] == PLAYED_THROUGH + 1
        ]
    )
    g = pl.DataFrame(games).with_columns(pl.col("season", "week").cast(pl.Int32))
    return TS.TeamInputs(
        games=g,
        box=pl.DataFrame(box),
        pace=pl.DataFrame(pace),
        ratings=pl.DataFrame(rat).with_columns(pl.col("season", "week").cast(pl.Int32)),
        game_features=pl.DataFrame(gf),
        context=pl.DataFrame(ctx),
        forecast=forecast,
        last_season=seasons[-1],
    )


@pytest.fixture(scope="module")
def world() -> TS.TeamInputs:
    return make_world()


@pytest.fixture(scope="module")
def feats(world) -> pl.DataFrame:
    return TS.build_team_features(world, first_season=FIRST)


def history(world: TS.TeamInputs, team: str, before_t: int) -> pl.DataFrame:
    """The team's played games strictly before `before_t`, oldest first, with its own and
    its opponent's box rows."""
    g = world.games.filter(pl.col("completed")).with_columns(
        ((pl.col("season") - 2000) * 22 + pl.col("week")).alias("t")
    )
    mine = g.filter((pl.col("home_team") == team) | (pl.col("away_team") == team))
    mine = mine.filter(pl.col("t") < before_t).sort("t")
    own = world.box.filter(pl.col("team") == team)
    opp = world.box.filter(pl.col("team") != team)
    return (
        mine.join(own, on="game_id")
        .join(
            opp.select("game_id", pl.all().exclude("game_id", "team").name.suffix("_opp")),
            on="game_id",
        )
        .sort("t")
    )


# ---- shape and labels ----------------------------------------------------------------------


def test_one_row_per_team_game_from_the_first_season(world, feats) -> None:
    n_games = world.games.filter(pl.col("season") >= FIRST).height
    assert feats.height == 2 * n_games
    assert feats.select("game_id", "team").unique().height == feats.height
    assert feats["season"].min() == FIRST
    assert feats.equals(feats.sort("season", "week", "kickoff_utc", "game_id", "team"))
    assert set(feats.columns[:8]) == set(TS.ID_COLS)
    assert [c for c in feats.columns if c.startswith("base_")] == [f"base_{s}" for s in TS.STATS]


def test_labels_are_the_box_score_numbers(world, feats) -> None:
    row = feats.filter(pl.col("completed")).row(40, named=True)
    own = world.box.filter((pl.col("game_id") == row["game_id"]) & (pl.col("team") == row["team"]))
    opp = world.box.filter(
        (pl.col("game_id") == row["game_id"]) & (pl.col("team") == row["opponent"])
    )
    assert row["pass_yds"] == own["passing_yards"][0]
    assert row["rush_yds"] == own["rushing_yards"][0]
    assert row["sacks_taken"] == own["sacks_suffered"][0]
    assert row["sacks_made"] == opp["sacks_suffered"][0]
    assert row["takeaways"] == opp["passing_interceptions"][0] + opp["fumbles_lost_total"][0]


def test_unplayed_games_have_no_label_but_have_features(feats) -> None:
    live = feats.filter(~pl.col("completed"))
    assert live.height == 2 * 2 * (WEEKS - PLAYED_THROUGH)
    assert live.select(pl.col(c).is_null().all() for c in TS.LABELS).row(0) == (True,) * 5
    nxt = live.filter(pl.col("week") == PLAYED_THROUGH + 1)
    assert nxt["tm_pass_yds_for_l8"].null_count() == 0 and nxt["base_pass_yds"].null_count() == 0


# ---- rolling form ----------------------------------------------------------------------------


def test_form_is_the_mean_of_the_previous_eight_games(world, feats) -> None:
    row = feats.filter(
        (pl.col("season") == 2021) & (pl.col("week") == 5) & (pl.col("team") == "AAA")
    ).row(0, named=True)
    h = history(world, "AAA", tk(2021, 5)).tail(TS.WINDOW)  # crosses into 2020
    assert h.height == TS.WINDOW and h["season"].min() == 2020
    assert row["tm_pass_yds_for_l8"] == pytest.approx(h["passing_yards"].mean())
    assert row["tm_pass_yds_ag_l8"] == pytest.approx(h["passing_yards_opp"].mean())
    assert row["tm_sacks_made_for_l8"] == pytest.approx(h["sacks_suffered_opp"].mean())
    assert row["tm_sacks_taken_ag_l8"] == pytest.approx(h["sacks_suffered_opp"].mean())
    to = (h["passing_interceptions_opp"] + h["fumbles_lost_total_opp"]).mean()
    assert row["tm_takeaways_for_l8"] == pytest.approx(to)
    op = history(world, row["opponent"], tk(2021, 5)).tail(TS.WINDOW)
    assert row["op_rush_yds_for_l8"] == pytest.approx(op["rushing_yards"].mean())
    assert row["op_pass_yds_ag_l8"] == pytest.approx(op["passing_yards_opp"].mean())


def test_a_games_own_result_never_reaches_its_own_features(world) -> None:
    base = TS.build_team_features(world, first_season=FIRST)
    gid = base.filter((pl.col("season") == 2021) & (pl.col("week") == 4))["game_id"][0]
    box = world.box.with_columns(
        pl.when(pl.col("game_id") == gid)
        .then(pl.col("passing_yards") * 3 + 100)
        .otherwise(pl.col("passing_yards"))
        .alias("passing_yards")
    )
    out = TS.build_team_features(replace(world, box=box), first_season=FIRST)
    cols = TS.feature_columns(base)
    same = (
        base.filter(pl.col("game_id") == gid)
        .select(cols)
        .equals(out.filter(pl.col("game_id") == gid).select(cols))
    )
    assert same
    later = (pl.col("season") == 2021) & (pl.col("week") == 5)
    assert not base.filter(later).select(cols).equals(out.filter(later).select(cols))


def test_baselines(world, feats) -> None:
    blend = (pl.col("tm_pass_yds_for_l8") + pl.col("op_pass_yds_ag_l8")) / 2
    assert feats.select((pl.col("base_pass_yds") - blend).abs().max()).item() < 1e-9
    sacks = (pl.col("tm_sacks_taken_for_l8") + pl.col("op_sacks_taken_ag_l8")) / 2
    assert feats.select((pl.col("base_sacks_taken") - sacks).abs().max()).item() < 1e-9
    # takeaways: the league's per-team-game average over the last 16 weeks with games
    row = feats.filter((pl.col("season") == 2021) & (pl.col("week") == 6)).row(0, named=True)
    g = world.games.filter(pl.col("completed")).with_columns(
        ((pl.col("season") - 2000) * 22 + pl.col("week")).alias("t")
    )
    weeks = sorted(g.filter(pl.col("t") < tk(2021, 6))["t"].unique().to_list())[-TS.LEAGUE_WEEKS :]
    ids = g.filter(pl.col("t").is_in(weeks))["game_id"].to_list()
    b = world.box.filter(pl.col("game_id").is_in(ids))
    expected = (b["passing_interceptions"].sum() + b["fumbles_lost_total"].sum()) / b.height
    assert row["base_takeaways"] == pytest.approx(expected)
    assert row["lg_takeaways_l16"] == pytest.approx(expected)


def test_league_window_skips_the_games_own_week_and_later(world) -> None:
    out = TS.build_team_features(world, first_season=FIRST)
    first = out.filter((pl.col("season") == FIRST) & (pl.col("week") == 1))
    # the first week of the first feature season sees only the 2020 weeks
    assert first["lg_pass_yds_l16"].null_count() == 0


# ---- ratings, context, market, schedule, weather ------------------------------------------------


def test_matchup_adds_the_opponents_allowed_epa(world) -> None:
    """`def_*` is EPA allowed: expected offense = off + the opponent's def (never minus)."""
    r = world.ratings.with_columns(
        pl.when(pl.col("team") == "AAA")
        .then(0.10)
        .otherwise(pl.col("off_pass_epa"))
        .alias("off_pass_epa"),
        pl.when(pl.col("team") == "BBB")
        .then(0.20)
        .otherwise(pl.col("def_pass_epa"))
        .alias("def_pass_epa"),
    )
    out = TS.build_team_features(replace(world, ratings=r), first_season=FIRST)
    row = out.filter(
        (pl.col("season") == 2021) & (pl.col("week") == 1) & (pl.col("team") == "AAA")
    ).row(0, named=True)
    assert row["opponent"] == "BBB"
    assert row["rt_pass_matchup"] == pytest.approx(0.30)
    assert row["rt_def_pass_matchup"] == pytest.approx(
        row["rt_op_off_pass_epa"] + row["rt_tm_def_pass_epa"]
    )


def test_market_is_from_the_teams_side(world, feats) -> None:
    gf = world.game_features
    home = feats.filter(pl.col("home")).row(30, named=True)
    away = feats.filter(
        (pl.col("game_id") == home["game_id"]) & (pl.col("team") == home["opponent"])
    ).row(0, named=True)
    line = gf.filter(pl.col("game_id") == home["game_id"]).row(0, named=True)
    assert home["mkt_spread"] == line["spread_line"] and away["mkt_spread"] == -line["spread_line"]
    assert home["mkt_points"] == line["mkt_home_points"]
    assert away["mkt_points"] == line["mkt_away_points"]
    assert home["mkt_opp_points"] == away["mkt_points"]
    assert home["mkt_total"] == away["mkt_total"] == line["total_line"]


def test_context_and_schedule(world, feats) -> None:
    row = feats.row(17, named=True)
    c = world.context.filter(
        (pl.col("game_id") == row["game_id"]) & (pl.col("team") == row["team"])
    ).row(0, named=True)
    assert row["ctx_pred_points"] == c["ctx_pred_points"]
    assert row["ctx_pred_total"] == pytest.approx(c["ctx_pred_points"] + c["ctx_pred_opp_points"])
    g = world.games.filter(pl.col("game_id") == row["game_id"]).row(0, named=True)
    mine, theirs = (
        (g["home_rest"], g["away_rest"]) if row["home"] else (g["away_rest"], g["home_rest"])
    )
    assert (row["sch_rest"], row["sch_opp_rest"]) == (mine, theirs)
    assert row["sch_rest_diff"] == mine - theirs
    assert row["sch_home"] == float(row["home"])


def test_weather_is_observed_when_played_and_forecast_when_not(world, feats) -> None:
    played_out = feats.filter(pl.col("completed") & (pl.col("wx_dome") == 0.0))
    g = world.games.select("game_id", "temp", "wind")
    j = played_out.join(g, on="game_id")
    assert (j["wx_temp"] == j["temp"]).all() and (j["wx_wind"] == j["wind"]).all()
    dome = feats.filter(pl.col("completed") & (pl.col("wx_dome") == 1.0))
    assert dome.height and dome["wx_temp"].null_count() == dome.height
    upcoming = feats.filter((pl.col("season") == 2022) & (pl.col("week") == PLAYED_THROUGH + 1))
    assert (upcoming["wx_temp"] == 41.0).all() and (upcoming["wx_wind"] == 12.0).all()
    later = feats.filter((pl.col("season") == 2022) & (pl.col("week") == PLAYED_THROUGH + 2))
    assert later["wx_temp"].null_count() == later.height  # no forecast yet


def test_missing_optional_inputs_leave_null_columns(world) -> None:
    bare = replace(
        world,
        ratings=pl.DataFrame(),
        game_features=pl.DataFrame(),
        context=None,
        forecast=None,
    )
    out = TS.build_team_features(bare, first_season=FIRST)
    for c in ("rt_pass_matchup", "ctx_pred_points", "mkt_spread"):
        assert out[c].null_count() == out.height
    assert out["tm_pass_yds_for_l8"].null_count() == 0


# ---- leakage: every family -----------------------------------------------------------------------


def _noise(frame: pl.DataFrame, mask: pl.Series, rng: np.random.Generator) -> pl.DataFrame:
    """Replace the numeric values of the masked rows with large junk."""
    cols = [c for c, t in frame.schema.items() if t.is_numeric() and c not in ("season", "week")]
    out = frame
    for c in cols:
        junk = pl.Series(rng.normal(500, 300, frame.height)).cast(frame.schema[c], strict=False)
        out = out.with_columns(pl.when(mask).then(junk).otherwise(pl.col(c)).alias(c))
    return out


def scrambled(world: TS.TeamInputs, outcome_from: int, pregame_from: int, seed: int = 5):
    """`world` with outcome inputs (box score, pace) of games at `t >= outcome_from` and
    pre-game inputs (ratings, lines, context, schedule, weather, forecast) at `t >= pregame_from`
    replaced by noise. Pre-game info of a week is legitimate for that week's own games, so the
    cut for it is one week later."""
    rng = np.random.default_rng(seed)
    tmap = world.games.select(
        "game_id", ((pl.col("season") - 2000) * 22 + pl.col("week")).alias("t")
    )

    def by_game(frame: pl.DataFrame, lo: int) -> pl.DataFrame:
        t = frame.join(tmap, on="game_id", how="left")["t"]
        return _noise(frame, (t >= lo).fill_null(False), rng)

    games = _noise(
        world.games,
        pl.Series(
            (world.games["season"].cast(pl.Int32) - 2000) * 22 + world.games["week"] >= pregame_from
        ),
        rng,
    ).with_columns(world.games["completed"])  # whether a game was played isn't scrambled
    rat_t = (world.ratings["season"] - 2000) * 22 + world.ratings["week"]
    return replace(
        world,
        games=games,
        box=by_game(world.box, outcome_from),
        pace=by_game(world.pace, outcome_from),
        ratings=_noise(world.ratings, rat_t >= pregame_from, rng),
        game_features=by_game(world.game_features, pregame_from),
        context=by_game(world.context, pregame_from),
        forecast=by_game(world.forecast, pregame_from),
    )


def _feature_cols(frame: pl.DataFrame) -> list[str]:
    return [c for c in frame.columns if c.startswith((*TS.FEATURE_PREFIXES, "base_"))]


@pytest.mark.parametrize("cut", [tk(2021, 4), tk(2021, 8), tk(2022, 3)])
def test_every_family_is_future_invariant(world, cut) -> None:
    base = TS.build_team_features(world, first_season=FIRST).with_columns(
        ((pl.col("season") - 2000) * 22 + pl.col("week")).alias("_t")
    )
    out = TS.build_team_features(scrambled(world, cut, cut + 1), first_season=FIRST).with_columns(
        ((pl.col("season") - 2000) * 22 + pl.col("week")).alias("_t")
    )
    cols = _feature_cols(base)
    before = pl.col("_t") <= cut
    a, b = base.filter(before).select(cols), out.filter(before).select(cols)
    assert a.height > 10
    assert a.equals(b), [c for c in cols if not a[c].equals(b[c])]
    # teeth: every family changes once the scramble reaches its rows
    after = (pl.col("_t") > cut) & (pl.col("_t") <= tk(2022, PLAYED_THROUGH))
    a2, b2 = base.filter(after), out.filter(after)
    for family, prefixes in TS.FAMILIES.items():
        fcols = [c for c in cols if c.startswith(prefixes)]
        assert any(not a2[c].equals(b2[c]) for c in fcols), f"{family}: scramble had no teeth"


def test_market_schedule_context_and_ratings_ignore_every_outcome(world) -> None:
    """The pre-game families read no result: swapping all box scores and pace rows for noise
    leaves them untouched."""
    base = TS.build_team_features(world, first_season=FIRST)
    out = TS.build_team_features(scrambled(world, tk(2000, 1), 10**6), first_season=FIRST)
    pre = [c for c in base.columns if c.startswith(("rt_", "ctx_", "mkt_", "sch_", "wx_"))]
    assert base.select(pre).equals(out.select(pre))
    assert not base.select("tm_pass_yds_for_l8").equals(out.select("tm_pass_yds_for_l8"))


# ---- columns ----------------------------------------------------------------------------------


def test_feature_columns_own_stat_only(feats) -> None:
    allc = TS.feature_columns(feats, "pass_yds")
    own = TS.feature_columns(feats, "pass_yds", own_only=True)
    assert set(own) < set(allc) and own[-1] == "week" and "base_pass_yds" in own
    assert "tm_pass_yds_for_l8" in own and "op_pass_yds_ag_l8" in own
    assert not any("rush_yds" in c or "takeaways" in c or "sacks" in c for c in own)
    assert "lg_pass_yds_l16" in own and "lg_rush_yds_l16" not in own
    for pace in ("tm_plays_for_l8", "op_dropbacks_ag_l8", "tm_proe_for_l8"):
        assert pace in own  # the pace series are shared by every target
    assert not any(c in own for c in TS.LABELS)
    with pytest.raises(ValueError):
        TS.feature_columns(feats, own_only=True)


def test_target_frame_has_label_and_baseline(feats) -> None:
    f = TS.team_target_frame(feats, "sacks_made")
    assert f["y"].equals(feats["sacks_made"].cast(pl.Float64))
    assert f["baseline"].equals(feats["base_sacks_made"])
