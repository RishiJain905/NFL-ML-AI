import datetime as dt

import polars as pl

from nflengine import schedule as sched
from nflengine.curate.build import Curator
from nflengine.curate.quality import (
    check_completeness,
    check_freshness,
    check_lines_sign,
    check_team_codes,
    check_unique,
)
from nflengine.curate.readiness import default_week, evaluate

SCHED = pl.DataFrame(
    {
        "game_id": ["g1", "g2", "g3", "g4"],
        "season": [2026] * 4,
        "week": [1, 1, 2, 2],
        "gameday": ["2026-09-13", "2026-09-14", "2026-09-20", "2026-09-21"],
        "result": [3, -7, 10, None],
    }
)


def test_schedule_helpers() -> None:
    assert sched.completed_weeks(SCHED, 2026) == [1]
    assert sched.next_week(SCHED, 2026) == 2
    assert sched.last_completed_week(SCHED, 2026) == 1


def test_readiness() -> None:
    r = evaluate(SCHED, {"g1", "g2"}, 2026, 1)
    assert r.ready and r.games == 2
    r = evaluate(SCHED, {"g3"}, 2026, 2)
    assert not r.ready and r.missing_results == ["g4"] and r.missing_pbp == ["g4"]
    assert "NOT READY" in r.summary()
    assert default_week(SCHED, 2026, dt.date(2026, 9, 22)) == 2
    assert default_week(SCHED, 2026, dt.date(2026, 9, 16)) == 1


def test_unique_and_team_codes() -> None:
    df = pl.DataFrame({"game_id": ["a", "a"], "play_id": [1, 1]})
    assert not check_unique(df, ["game_id", "play_id"], "x").passed
    ok = check_team_codes({"t": (pl.DataFrame({"team": ["KC", None]}), ["team"])})
    bad = check_team_codes({"t": (pl.DataFrame({"team": ["KC", "OAK"]}), ["team"])})
    assert ok.passed and not bad.passed and "OAK" in bad.detail


def test_completeness() -> None:
    games = pl.DataFrame(
        {
            "game_id": ["g1", "g2"],
            "season": [2026, 2026],
            "game_type": ["REG"] * 2,
            "completed": [True, False],
        }
    )
    assert check_completeness(games, {"g1"}, 2010).passed
    assert not check_completeness(games, set(), 2010).passed


def test_lines_sign_convention() -> None:
    games = pl.DataFrame(
        {
            "spread_line": [3.0, -2.5, 7.0],
            "home_moneyline": [-150, 120, -300],
            "away_moneyline": [130, -140, 250],
        }
    )
    assert check_lines_sign(games).passed
    flipped = games.with_columns(-pl.col("spread_line"))
    assert not check_lines_sign(flipped).passed


def test_freshness_allows_pfr_lag() -> None:
    assert check_freshness({"player_games": 4, "pfr_def": 3}, 4).passed
    assert not check_freshness({"player_games": 3}, 4).passed


def test_depth_new_format_maps_to_next_game_only() -> None:
    team_games = pl.DataFrame(
        {
            "season": [2026, 2026, 2026],
            "week": [1, 2, 9],
            "team": ["KC", "KC", "KC"],
            "gameday": [dt.date(2026, 9, 13), dt.date(2026, 9, 20), dt.date(2026, 11, 8)],
        }
    )
    depth = pl.DataFrame(
        {
            "dt": ["2026-09-10T12:00:00", "2026-09-18T12:00:00", "2026-09-18T12:00:00"],
            "team": ["KC", "KC", "KC"],
            "gsis_id": ["a", "a", "b"],
            "player_name": ["A", "A", "B"],
            "pos_abb": ["QB", "QB", "QB"],
            "pos_rank": [1, 1, 2],
            "pos_grp": ["Offense"] * 3,
        }
    )
    out = Curator._depth_new(depth, 2026, team_games)
    assert sorted(out["week"].unique().to_list()) == [1, 2]  # week 9 is too far ahead
    assert out.filter(pl.col("week") == 2).height == 2
