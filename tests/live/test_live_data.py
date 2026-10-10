"""LD00 data: the training frames, on hand-made plays (no disk beyond a tmp parquet, no network)."""

from __future__ import annotations

import math

import polars as pl
import pytest
from live_stubs import PLAY_SCHEMA, plays_frame

from nflengine.live import data as D
from nflengine.live import schema
from nflengine.paths import DataPaths


def snap(**kw) -> dict:
    """A rushing snap in the second half (Q3, 25:00 left) unless overridden."""
    return {"play_type": "run", "down": 1.0, "ydstogo": 10, "yardline_100": 75.0, "qtr": 3.0,
            "yards_gained": 4.0, **kw}  # fmt: skip


def one(df: pl.DataFrame, **where) -> dict:
    """The single row matching `where` (column == value)."""
    expr = pl.lit(True)
    for k, v in where.items():
        expr = expr & (pl.col(k) == v)
    rows = df.filter(expr)
    assert rows.height == 1, f"expected one row for {where}, found {rows.height}"
    return rows.row(0, named=True)


# --- with_state -------------------------------------------------------------------------------
def state_rows() -> pl.DataFrame:
    return plays_frame(
        [
            # game A: AWY received the opening kickoff; HOM is favored by 3
            {"game_id": "A", "play_type": "kickoff", "qtr": 1.0, "posteam": "AWY",
             "defteam": "HOM", "home_opening_kickoff": 0.0, "game_seconds_remaining": 3600.0,
             "half_seconds_remaining": 1800.0},
            snap(game_id="A", qtr=1.0, posteam="HOM", defteam="AWY", home_opening_kickoff=0.0,
                 game_seconds_remaining=2700.0, half_seconds_remaining=900.0,
                 score_differential=3.0),
            snap(game_id="A", qtr=1.0, posteam="AWY", defteam="HOM", home_opening_kickoff=0.0,
                 game_seconds_remaining=2600.0, half_seconds_remaining=800.0,
                 score_differential=-3.0),
            snap(game_id="A", qtr=2.0, posteam="HOM", defteam="AWY", home_opening_kickoff=0.0,
                 game_seconds_remaining=2000.0, half_seconds_remaining=200.0),
            snap(game_id="A", qtr=3.0, posteam="HOM", defteam="AWY", home_opening_kickoff=0.0,
                 game_seconds_remaining=1500.0, half_seconds_remaining=1500.0),
            snap(game_id="A", qtr=4.0, posteam="AWY", defteam="HOM", home_opening_kickoff=0.0,
                 game_seconds_remaining=600.0, half_seconds_remaining=600.0),
            # game B: a neutral site, no kickoff play; `home_opening_kickoff` = 1 (HOM received)
            snap(game_id="B", neutral_site=True, qtr=1.0, posteam="HOM", defteam="AWY",
                 home_opening_kickoff=1.0, game_seconds_remaining=3000.0,
                 half_seconds_remaining=1200.0),
            snap(game_id="B", neutral_site=True, qtr=1.0, posteam="AWY", defteam="HOM",
                 home_opening_kickoff=1.0, game_seconds_remaining=2900.0,
                 half_seconds_remaining=1100.0),
            # game C: a 2013 playoff game in a dome, overtime
            snap(game_id="C", season=2013, season_type="POST", roof="dome", qtr=5.0,
                 posteam="AWY", defteam="HOM", home_opening_kickoff=1.0, score_differential=3.0,
                 game_seconds_remaining=300.0, half_seconds_remaining=300.0, spread_line=-2.5),
        ]
    )  # fmt: skip


def test_offense_spread_sign() -> None:
    """nflverse `spread_line` is from the home side (+ = home favored): the home offense keeps
    it, the away offense gets its negative."""
    df = D.with_state(state_rows())
    assert one(df, game_id="A", i=1)["spread"] == 3.0, "home favored by 3: home offense +3"
    assert one(df, game_id="A", i=2)["spread"] == -3.0, "the away offense is -3"
    c = one(df, game_id="C")
    assert c["spread"] == 2.5, "home underdog by 2.5 (spread_line -2.5): the away offense is +2.5"


def test_home_flag_incl_neutral_site() -> None:
    df = D.with_state(state_rows())
    assert one(df, game_id="A", i=1)["home"] == 1
    assert one(df, game_id="A", i=2)["home"] == -1
    assert one(df, game_id="B", i=0)["home"] == 0 and one(df, game_id="B", i=1)["home"] == 0
    assert df["home"].dtype == pl.Int8


def test_receive_2h_ko() -> None:
    """The team that did NOT get the opening kickoff gets the second-half kickoff: flag = 1 in
    the first half for that team only."""
    df = D.with_state(state_rows())
    ko = one(df, game_id="A", i=0)
    assert ko["opening_receiver"] == "AWY"
    assert ko["receive_2h_ko"] == 0, "the opening kickoff's own receiver does not get the 2h kick"
    assert one(df, game_id="A", i=1)["receive_2h_ko"] == 1, "HOM (Q1) kicked off, so receives 2h"
    assert one(df, game_id="A", i=2)["receive_2h_ko"] == 0, "AWY (Q1) received the opening kick"
    assert one(df, game_id="A", i=3)["receive_2h_ko"] == 1, "Q2 counts as the first half"
    assert one(df, game_id="A", i=4)["receive_2h_ko"] == 0, "second half: always 0"
    assert one(df, game_id="A", i=5)["receive_2h_ko"] == 0
    # no kickoff play: fall back to `home_opening_kickoff` (1 = home received)
    assert one(df, game_id="B", i=0)["receive_2h_ko"] == 0
    assert one(df, game_id="B", i=1)["receive_2h_ko"] == 1


def test_era_matches_schema_era_of() -> None:
    seasons = [2010, 2014, 2015, 2017, 2018, 2020, 2021, 2023, 2024, 2025, 2026]
    rows = [snap(game_id=f"S{s}", season=s, home_opening_kickoff=1.0) for s in seasons]
    df = D.with_state(plays_frame(rows))
    assert df["era"].to_list() == [schema.era_of(s) for s in seasons]
    assert D.era_expr("season") is not None


def test_time_ratio_formulas() -> None:
    df = D.with_state(state_rows())
    a = one(df, game_id="A", i=2)  # 2600 s left, down 3
    decay = math.exp(-4.0 * (3600 - 2600) / 3600)
    assert a["diff_time_ratio"] == pytest.approx(-3.0 / decay)
    assert a["spread_time"] == pytest.approx(-3.0 * decay)
    assert a["team_total"] == pytest.approx(44.0 / 2 + -3.0 / 2)
    c = one(df, game_id="C")  # overtime: the full decay
    assert c["ot"] == 1
    assert c["diff_time_ratio"] == pytest.approx(3.0 / math.exp(-4.0))
    assert c["spread_time"] == pytest.approx(2.5 * math.exp(-4.0))
    kick = one(df, game_id="A", i=0)  # 3600 s left: no decay (the receiver is the away team)
    assert kick["score_diff"] == 0 and kick["spread_time"] == pytest.approx(-3.0)


def test_indoor_playoffs_and_renamed_columns() -> None:
    df = D.with_state(state_rows())
    c = one(df, game_id="C")
    assert c["indoor"] == 1 and c["playoffs"] is True
    a = one(df, game_id="A", i=1)
    assert a["indoor"] == 0 and a["playoffs"] is False
    assert (a["score_diff"], a["game_seconds"], a["half_seconds"]) == (3.0, 2700.0, 900.0)
    assert (a["off_timeouts"], a["def_timeouts"], a["total"]) == (3.0, 3.0, 44.0)


def test_with_state_agrees_with_schema_state_features() -> None:
    """The training frames and the live state compute the same inputs (`state_of_row`)."""
    df = D.with_state(state_rows()).filter(pl.col("down").is_not_null())
    assert df.height == 8, "five snaps in game A, two in B, one in C"
    for r in df.iter_rows(named=True):
        feats = schema.state_features(D.state_of_row(r))
        for name in [*schema.WP_FEATURES, "total", "team_total"]:
            assert math.isclose(r[name], feats[name], rel_tol=1e-12, abs_tol=1e-9), (
                f"{name} differs for {r['game_id']} i={r['i']}: {r[name]} vs {feats[name]}"
            )


def test_state_of_row_drops_weather_indoors() -> None:
    open_ = {"home_opening_kickoff": 1.0}
    df = D.with_state(
        plays_frame(
            [
                snap(roof="dome", wind=9.0, temp=70.0, **open_),
                snap(game_id="G2", wind=9.0, temp=70.0, **open_),
                snap(game_id="G3", **open_),
            ]
        )
    )
    rows = df.iter_rows(named=True)
    indoor, outdoor, unknown = (D.state_of_row(r) for r in rows)
    assert indoor.wind is None and indoor.temp is None, "indoor games ignore weather"
    assert (outdoor.wind, outdoor.temp) == (9.0, 70.0)
    assert unknown.wind is None and unknown.temp is None


# --- wp ---------------------------------------------------------------------------------------
def test_wp_frame_labels_and_ties() -> None:
    rows = [
        # W1: the home team won by 7
        snap(game_id="W1", posteam="HOM", defteam="AWY", result=7.0),
        snap(game_id="W1", posteam="AWY", defteam="HOM", result=7.0, play_type="pass"),
        snap(game_id="W1", posteam="HOM", defteam="AWY", result=7.0, play_type="qb_kneel"),
        {"game_id": "W1", "play_type": "kickoff", "result": 7.0},  # not a snap
        snap(game_id="W1", down=None, result=7.0),  # no down
        snap(game_id="W1", posteam=None, result=7.0),  # no possession team
        snap(game_id="W1", yardline_100=0.0, result=7.0),  # off the field
        snap(game_id="W1", spread_line=None, result=7.0),  # no line
        # W2: a tie
        snap(game_id="W2", posteam="HOM", defteam="AWY", result=0.0),
        snap(game_id="W2", posteam="AWY", defteam="HOM", result=0.0),
        # W3: the result is unknown (an unfinished game)
        snap(game_id="W3", result=None),
    ]
    frame, counts = D.wp_frame(D.with_state(plays_frame(rows)))
    assert frame.height == 3, "W1's three snaps; ties, non-snaps and incomplete rows are dropped"
    assert frame.filter(pl.col("play_type") == "run")["win"].to_list() == [1]
    assert frame.filter(pl.col("play_type") == "pass")["win"].to_list() == [0], "away lost"
    assert counts == {"rows": 5, "tie_rows": 2, "tie_games": 1}, "ties are counted, not hidden"
    assert set(frame["win"].unique().to_list()) <= {0, 1} and frame["win"].dtype == pl.Int8


def test_wp_frame_away_win() -> None:
    frame, _ = D.wp_frame(
        D.with_state(
            plays_frame(
                [
                    snap(posteam="HOM", defteam="AWY", result=-3.0),
                    snap(posteam="AWY", defteam="HOM", result=-3.0),
                ]
            )
        )
    )
    assert frame["win"].to_list() == [0, 1], "result < 0 is an away win"


# --- gain -------------------------------------------------------------------------------------
def gain_rows() -> pl.DataFrame:
    pen = {"play_type": "no_play", "first_down_penalty": 1.0, "penalty_team": "AWY",
           "yards_gained": 0.0}  # fmt: skip
    return D.with_state(
        plays_frame(
            [
                snap(game_id="g1", down=4.0, ydstogo=3, yardline_100=40.0, yards_gained=5.0),
                snap(
                    game_id="g2",
                    down=3.0,
                    ydstogo=8,
                    yardline_100=30.0,
                    yards_gained=4.0,
                    play_type="pass",
                ),
                snap(
                    game_id="g3",
                    down=3.0,
                    ydstogo=8,
                    yardline_100=30.0,
                    yards_gained=-7.0,
                    play_type="pass",
                ),  # a sack
                snap(
                    game_id="g4", down=3.0, ydstogo=8, yardline_100=50.0, penalty_yards=5.0, **pen
                ),
                snap(
                    game_id="g5", down=3.0, ydstogo=8, yardline_100=50.0, penalty_yards=15.0, **pen
                ),
                snap(
                    game_id="g6", down=4.0, ydstogo=5, yardline_100=6.0, penalty_yards=15.0, **pen
                ),
                snap(
                    game_id="g7",
                    down=3.0,
                    ydstogo=8,
                    yardline_100=50.0,
                    penalty_yards=5.0,
                    **{**pen, "penalty_team": "HOM"},
                ),  # an offensive penalty: not a conversion
                snap(
                    game_id="g8",
                    down=3.0,
                    ydstogo=8,
                    yardline_100=20.0,
                    yards_gained=20.0,
                    play_type="pass",
                    touchdown=1.0,
                    td_team="HOM",
                ),
                snap(
                    game_id="g9",
                    down=3.0,
                    ydstogo=5,
                    yardline_100=30.0,
                    yards_gained=0.0,
                    play_type="pass",
                    touchdown=1.0,
                    td_team="AWY",
                ),  # a pick-six
                snap(game_id="g10", down=4.0, ydstogo=3, yardline_100=3.0, yards_gained=3.0),
                snap(game_id="g11", down=3.0, ydstogo=5, yardline_100=40.0, yards_gained=-15.0),
                snap(game_id="g12", down=3.0, ydstogo=10, yardline_100=99.0, yards_gained=80.0),
                # left out
                snap(game_id="x1", down=2.0, ydstogo=5, yards_gained=9.0),
                snap(game_id="x2", down=3.0, ydstogo=5, two_point_attempt=1.0),
                snap(game_id="x3", down=4.0, ydstogo=5, play_type="punt", yards_gained=None),
                snap(game_id="x4", down=3.0, ydstogo=5, spread_line=None),
            ]
        )
    )


def test_gain_frame_rows_and_classes() -> None:
    f = D.gain_frame(gain_rows())
    kept = sorted(f"g{i}" for i in (1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12))
    assert sorted(f["game_id"].to_list()) == kept, (
        "downs 3-4 runs / passes / defensive-penalty first downs only; no 2-pt tries, punts, "
        "offensive penalties, 1st-2nd downs or rows without a line"
    )
    g = {r["game_id"]: r for r in f.iter_rows(named=True)}
    assert (g["g1"]["gain"], g["g1"]["gain_class"], g["g1"]["converted"]) == (5, 15, 1)
    assert (g["g2"]["gain"], g["g2"]["gain_class"], g["g2"]["converted"]) == (4, 14, 0)
    assert (g["g3"]["gain"], g["g3"]["gain_class"], g["g3"]["converted"]) == (-7, 3, 0)
    for r in f.iter_rows(named=True):
        assert r["gain_class"] == schema.gain_class(r["gain"], r["offense_td"]), (
            f"{r['game_id']}: the frame's class and schema.gain_class disagree"
        )
    assert f["gain_class"].dtype == pl.Int32 and f["converted"].dtype == pl.Int8


def test_gain_frame_defensive_penalty_first_down() -> None:
    g = {r["game_id"]: r for r in D.gain_frame(gain_rows()).iter_rows(named=True)}
    assert g["g4"]["penalty_first_down"] is True and g["g1"]["penalty_first_down"] is False
    # at least the distance (8), whatever the penalty yards were
    assert g["g4"]["gain"] == 8 and g["g4"]["converted"] == 1, "5-yard penalty on 3rd & 8: gain 8"
    assert g["g5"]["gain"] == 15 and g["g5"]["converted"] == 1
    # near the goal: still at least the distance (5) but short of the goal line (6)
    assert g["g6"]["gain"] == 5, "capped at yards-to-goal minus one"
    assert g["g6"]["gain"] >= 5 and g["g6"]["gain"] < 6 and g["g6"]["converted"] == 1


def test_gain_frame_touchdowns_and_caps() -> None:
    g = {r["game_id"]: r for r in D.gain_frame(gain_rows()).iter_rows(named=True)}
    assert g["g8"]["offense_td"] is True
    assert (g["g8"]["gain"], g["g8"]["gain_class"], g["g8"]["converted"]) == (20, 76, 1)
    # a pick-six is the defense's touchdown: the offense's play is an incompletion-like 0
    assert g["g9"]["offense_td"] is False
    assert (g["g9"]["gain_class"], g["g9"]["converted"]) == (10, 0)
    # a non-touchdown gain can't reach the goal line (3 to go, gained 3, no TD)
    assert g["g10"]["gain"] == 2 and g["g10"]["gain_class"] == 12 and g["g10"]["converted"] == 0
    assert g["g11"]["gain_class"] == 0, "a 15-yard loss clips to the -10 class"
    assert g["g12"]["gain"] == 80 and g["g12"]["gain_class"] == 75, "an 80-yard gain clips to +65"
    assert g["g12"]["converted"] == 1


def test_gain_frame_drops_goal_to_go_defensive_penalty_first_downs() -> None:
    """4th & goal at the 5 -> 1st & goal at the 2 is a first down short of the line to gain, which
    no gain class can express (a goal-to-go conversion is a touchdown): the row is left out. A
    penalty first down that is not goal-to-go stays, converted."""

    def penalty(game_id, down, togo, yl, yards, team="AWY"):
        return snap(game_id=game_id, down=down, ydstogo=togo, yardline_100=yl, play_type="no_play",
                    first_down_penalty=1.0, penalty_team=team, penalty_yards=yards,
                    yards_gained=0.0)  # fmt: skip

    rows = [
        penalty("gtg", 4.0, 5, 5.0, 3.0),
        penalty("gtg1", 3.0, 1, 1.0, 0.0),
        penalty("near", 4.0, 5, 6.0, 3.0),
        penalty("mid", 3.0, 8, 50.0, 5.0),
        penalty("off", 4.0, 5, 5.0, 3.0, team="HOM"),  # an offensive penalty is never a row
        # a goal-to-go run is untouched: only penalty first downs are dropped
        snap(game_id="run", down=4.0, ydstogo=5, yardline_100=5.0, yards_gained=2.0),
    ]
    f = D.gain_frame(D.with_state(plays_frame(rows)))
    assert sorted(f["game_id"].to_list()) == ["mid", "near", "run"], (
        "goal-to-go penalty first downs (distance >= yards to goal) are dropped, the rest kept"
    )
    r = {x["game_id"]: x for x in f.iter_rows(named=True)}
    assert (r["near"]["gain"], r["near"]["converted"]) == (5, 1), "5 to go at the 6: not goal-to-go"
    assert (r["mid"]["gain"], r["mid"]["converted"]) == (8, 1)
    assert (r["run"]["gain_class"], r["run"]["converted"]) == (12, 0)


# --- fg ---------------------------------------------------------------------------------------
def test_fg_frame() -> None:
    fg = {"play_type": "field_goal", "down": 4.0, "ydstogo": 8}
    plays = D.with_state(
        plays_frame(
            [
                snap(
                    game_id="f1",
                    **fg,
                    yardline_100=27.0,
                    kick_distance=45.0,
                    field_goal_result="made",
                    wind=12.0,
                    temp=30.0,
                ),
                snap(game_id="f2", **fg, yardline_100=30.0, field_goal_result="missed"),
                snap(
                    game_id="f3",
                    **fg,
                    yardline_100=22.0,
                    kick_distance=40.0,
                    field_goal_result="blocked",
                ),
                snap(
                    game_id="f4",
                    **fg,
                    yardline_100=10.0,
                    field_goal_result="made",
                    roof="dome",
                    wind=5.0,
                    temp=70.0,
                    kick_distance=28.0,
                ),
                snap(game_id="f5", **fg, yardline_100=10.0, field_goal_result=None),
                snap(game_id="f6", **fg, yardline_100=20.0, field_goal_result="made"),
                snap(game_id="f7", play_type="punt", down=4.0, field_goal_result=None),
            ]
        )  # fmt: skip
    )
    f = D.fg_frame(plays)
    assert sorted(f["game_id"].to_list()) == ["f1", "f2", "f3", "f4", "f6"], (
        "only field goals with a result"
    )
    r = {x["game_id"]: x for x in f.iter_rows(named=True)}
    assert (r["f1"]["distance"], r["f1"]["make"], r["f1"]["blocked"]) == (45.0, 1, 0)
    assert r["f2"]["distance"] == 48.0, "no kick_distance: yards to goal + 18 = 30 + 18"
    assert (r["f2"]["make"], r["f2"]["blocked"]) == (0, 0)
    assert (r["f3"]["make"], r["f3"]["blocked"]) == (0, 1)
    assert r["f6"]["distance"] == 38.0 and r["f6"]["wind_out"] is None, "unknown wind stays null"
    assert (r["f1"]["wind_out"], r["f1"]["temp_out"]) == (12.0, 30.0)
    assert r["f4"]["wind_out"] is None and r["f4"]["temp_out"] is None, "indoors: weather nulled"
    assert r["f4"]["distance"] == 28.0
    assert schema.FG_SNAP_YARDS == 18


# --- fourth -----------------------------------------------------------------------------------
def test_fourth_frame_choice_mapping() -> None:
    f4 = {"down": 4.0, "ydstogo": 3, "yardline_100": 40.0}
    plays = D.with_state(
        plays_frame(
            [
                snap(game_id="a", **f4),  # run
                snap(game_id="b", **f4, play_type="pass"),
                snap(game_id="c", **f4, play_type="field_goal"),
                snap(game_id="d", **f4, play_type="punt"),
                snap(game_id="e", **f4, play_type="no_play"),  # a pre-snap penalty
                snap(game_id="f", **f4, play_type="qb_kneel"),
                snap(game_id="g", down=3.0, ydstogo=3, yardline_100=40.0),  # not 4th down
                snap(game_id="h", **f4, total_line=None),  # no line
                snap(game_id="i", **f4, spread_line=None),
            ]
        )
    )
    f = D.fourth_frame(plays).sort("game_id")
    assert f["game_id"].to_list() == ["a", "b", "c", "d"]
    assert f["choice"].to_list() == ["go", "go", "fg", "punt"], "run / pass are 'go'"


def test_pass_frame() -> None:
    plays = D.with_state(
        plays_frame(
            [
                snap(game_id="a", **{"pass": 1.0}, play_type="pass"),
                snap(game_id="b"),  # a run: pass is null
                snap(game_id="c", **{"pass": 0.0}),
                snap(game_id="d", two_point_attempt=1.0, **{"pass": 1.0}, play_type="pass"),
                snap(game_id="e", play_type="punt", down=4.0),
            ]
        )
    )
    f = D.pass_frame(plays).sort("game_id")
    assert f["game_id"].to_list() == ["a", "b", "c"]
    assert f["is_pass"].to_list() == [1, 0, 0], "null `pass` counts as a non-dropback"


# --- punt and kickoff -------------------------------------------------------------------------
def punt_games() -> pl.DataFrame:
    punt = {"play_type": "punt", "down": 4.0, "ydstogo": 10, "yardline_100": 60.0,
            "posteam": "HOM", "defteam": "AWY", "yards_gained": None}  # fmt: skip
    away = {"posteam": "AWY", "defteam": "HOM"}
    return plays_frame(
        [
            # P1: the receiver snaps next, in the next quarter of the same half
            snap(game_id="P1", **punt, qtr=1.0),
            snap(game_id="P1", **away, qtr=2.0, yardline_100=65.0),
            # P2: returned for a touchdown (td_team = the receiving team)
            snap(game_id="P2", **punt, touchdown=1.0, td_team="AWY"),
            {"game_id": "P2", "play_type": "extra_point", "posteam": "AWY", "defteam": "HOM"},
            {"game_id": "P2", "play_type": "kickoff", "posteam": "AWY", "defteam": "HOM"},
            snap(game_id="P2", yardline_100=75.0),
            # P3: a muff recovered by the punting team: it snaps next
            snap(game_id="P3", **punt),
            snap(game_id="P3", yardline_100=45.0),
            # P4: the punt ends the half
            snap(game_id="P4", **punt, qtr=2.0),
            snap(game_id="P4", **away, qtr=3.0, yardline_100=70.0),
            # P5: the punting team scores (a blocked punt returned by its own side)
            snap(game_id="P5", **punt, touchdown=1.0, td_team="HOM"),
            snap(game_id="P5", **away, yardline_100=75.0),
            # P6: a safety on the punt
            snap(game_id="P6", **punt, safety=1.0),
            snap(game_id="P6", **away, yardline_100=60.0),
            # P7: nothing follows
            snap(game_id="P7", **punt),
            # P8: Q3 -> Q4 is the same half
            snap(game_id="P8", **punt, qtr=3.0),
            snap(game_id="P8", **away, qtr=4.0, yardline_100=80.0),
        ]
    )


def test_punt_frame_outcomes() -> None:
    f = D.punt_frame(D.with_state(punt_games()))
    got = {r["game_id"]: (r["outcome"], r["spot"]) for r in f.iter_rows(named=True)}
    assert got == {
        "P1": ("recv", 65.0),  # the receiving team's next snap, at its yards to goal
        "P2": ("ret_td", None),
        "P3": ("keep", 45.0),
        "P8": ("recv", 80.0),
    }, "P4 (half over), P5 (kicker's TD), P6 (safety) and P7 (no next snap) are dropped"


def kickoff_games() -> pl.DataFrame:
    xp = {"play_type": "extra_point", "extra_point_attempt": 1.0, "down": None}
    ko = {"play_type": "kickoff", "posteam": "AWY", "defteam": "HOM", "down": None,
          "yards_gained": None}  # fmt: skip
    away = {"posteam": "AWY", "defteam": "HOM"}
    return plays_frame(
        [
            # K1: after a touchdown + extra point
            snap(game_id="K1", play_type="pass", touchdown=1.0, td_team="HOM", down=1.0),
            {"game_id": "K1", **xp},
            {"game_id": "K1", **ko, "qtr": 3.0},
            snap(game_id="K1", **away, yardline_100=75.0),
            # K2: after a made field goal, 2023
            snap(
                game_id="K2",
                season=2023,
                play_type="field_goal",
                down=4.0,
                field_goal_result="made",
            ),
            {"game_id": "K2", "season": 2023, **ko},
            snap(game_id="K2", season=2023, **away, yardline_100=70.0),
            # K3: returned for a touchdown (after a two-point try)
            {"game_id": "K3", "play_type": "pass", "two_point_attempt": 1.0},
            {"game_id": "K3", **ko, "touchdown": 1.0, "td_team": "AWY"},
            snap(game_id="K3", **away, yardline_100=75.0),
            # K4: an onside kick the kicking team recovers
            {"game_id": "K4", **xp},
            {"game_id": "K4", **ko, "desc": "Onside Kick formation, recovered by HOM"},
            snap(game_id="K4", yardline_100=52.0),
            # K5: the opening kickoff (no score before it)
            {"game_id": "K5", **ko},
            snap(game_id="K5", **away, yardline_100=75.0),
            # K6: the kicking team scores on the kickoff
            {"game_id": "K6", **xp},
            {"game_id": "K6", **ko, "touchdown": 1.0, "td_team": "HOM"},
            snap(game_id="K6", **away, yardline_100=75.0),
            # K7: a kickoff after a punt (not after a score)
            snap(game_id="K7", play_type="punt", down=4.0, yards_gained=None),
            {"game_id": "K7", **ko},
            snap(game_id="K7", **away, yardline_100=75.0),
        ]
    )


def test_kickoff_frame_outcomes_and_eras() -> None:
    f = D.kickoff_frame(D.with_state(kickoff_games()))
    got = {
        r["game_id"]: (r["outcome"], r["spot"], r["ko_era"], r["onside"])
        for r in f.iter_rows(named=True)
    }
    assert got == {
        "K1": ("recv", 75.0, "tb35", False),
        "K2": ("recv", 70.0, "tb25", False),
        "K3": ("ret_td", None, "tb35", False),
        "K4": ("keep", 52.0, "tb35", True),  # kept in the frame, flagged (fit_kickoff drops it)
    }, "only kickoffs right after a score; the kicker's own touchdown is dropped"


# --- load_plays (a tiny parquet tree in tmp_path) ----------------------------------------------
def test_load_plays_reads_curated_in_game_order(tmp_path) -> None:
    rows = [
        snap(game_id="2025_01_A", order_sequence=2.0, play_id=2.0),
        snap(game_id="2025_01_A", order_sequence=1.0, play_id=1.0),
        snap(game_id="2024_01_B", season=2024),
        snap(game_id="2025_00_PRE", season_type="PRE"),
        snap(game_id="2025_02_C", season_type="POST"),
    ]
    paths = DataPaths(tmp_path)
    (paths.curated / "plays").mkdir(parents=True)
    plays = plays_frame(rows)
    plays.select(D.PLAY_COLUMNS).write_parquet(paths.curated / "plays" / "plays.parquet")
    pl.DataFrame(
        {"game_id": ["2025_01_A", "2024_01_B"], "neutral_site": [True, False]}
    ).write_parquet(paths.curated / "games.parquet")
    out = D.load_plays(paths=paths)
    assert "2025_00_PRE" not in out["game_id"].to_list(), "preseason is dropped"
    assert set(out["season_type"].unique()) == {"REG", "POST"}
    a = out.filter(pl.col("game_id") == "2025_01_A")
    assert a["order_sequence"].to_list() == [1.0, 2.0], "sorted into game order"
    assert a["i"].to_list() == [0, 1], "`i` is the play's index in its game"
    assert a["neutral_site"].to_list() == [True, True]
    c = out.filter(pl.col("game_id") == "2025_02_C")
    assert c["neutral_site"].to_list() == [False], "a game missing from `games` is not neutral"
    only = D.load_plays(seasons=[2024], paths=paths)
    assert only["game_id"].unique().to_list() == ["2024_01_B"]


def test_play_columns_are_what_the_test_schema_holds() -> None:
    assert set(D.PLAY_COLUMNS) <= set(PLAY_SCHEMA), "live_stubs.PLAY_SCHEMA lags data.PLAY_COLUMNS"
