"""LD03: the bot's call against the coach's (`review.score_decisions`), the week and season tables
built from it (`build_week`, `week_summary`, `highlights`, `leaderboard`, `trend`, `calibration`,
`season_tables`) and the answers the app shows (`week_payload`, `season_payload`).

The engine is `live_review_fixtures.StubEngine`: the test names each play's win chances, so every
expected edge / cost / rate below is worked by hand from the table in that module's docstring.
Calibration runs the stub model bundle (`live_stubs.make_models`) on hand-made snaps. No data
drive, no network, no W&B.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
from live_fakes import no_real_network  # noqa: F401  (autouse guard)
from live_review_fixtures import (
    G1,
    G2,
    StubEngine,
    World,
    make_rows,
    season_engine,
    season_world,
    spec,
)
from live_stubs import stub_wp_value

from nflengine.live import review

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
TRAINED = list(range(2010, 2026))
approx = pytest.approx


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("season")
    w = season_world(with_week4=True)
    paths = w.write(root)
    plays = review.load_season(2025, paths, root / "no_fixes.csv")
    return SimpleNamespace(w=w, paths=paths, plays=plays, root=root)


def score_week(world, week: int, engine: StubEngine | None = None):
    engine = engine or season_engine()
    frame = review.decisions_frame(world.plays.filter(pl.col("week") == week))
    rows, skipped = review.score_decisions(frame, engine)
    return rows, skipped, engine


def by_play(rows: pl.DataFrame) -> dict[int, dict]:
    return {r["play_id"]: r for r in rows.iter_rows(named=True)}


# play_id -> (choice, best, agree, label, gap, edge, cost, success, result, situation, coach)
WEEK1 = {
    1: ("go", "go", True, "Confident", 0.04, 0.04, 0.0, True, "Converted (+3 yds)",
        "4th & 2 at AWY 40", "Home Coach"),
    3: ("punt", "go", False, "Lean", 0.03, -0.03, 0.03, None, "AWY ball at own 20",
        "4th & 6 at HOM 45", "Home Coach"),
    5: ("fg", "go", False, "Confident", 0.06, -0.06, 0.06, True, "Good from 48",
        "4th & 1 at HOM 30", "Away Coach"),
    7: ("punt", "punt", True, "Confident", 0.15, 0.15, 0.0, None, "HOM ball at own 25",
        "4th & 10 at AWY 40", "Away Coach"),
    9: ("go", "go", True, "Toss-up", 0.01, 0.01, 0.0, False, "Stopped (+1 yds, needed 3)",
        "4th & 3 at AWY 25", "Home Coach"),
    11: ("go", "punt", False, "Lean", 0.03, -0.08, 0.08, False, "Stopped (+2 yds, needed 8)",
         "4th & 8 at HOM 45", "Away Coach"),
}  # fmt: skip


# --- score_decisions ------------------------------------------------------------------------------
def test_each_decision_is_scored_against_the_coachs_call(world):
    rows, skipped, engine = score_week(world, 1)
    assert skipped == 0 and rows.columns == review.ROW_COLUMNS
    assert rows["play_id"].to_list() == list(WEEK1), "kickoff order, then game order"
    assert engine.calls == [(6, True)], "one batch, with the bootstrap on"
    got = by_play(rows)
    for pid, (
        choice,
        best,
        agree,
        label,
        gap,
        edge,
        cost,
        success,
        result,
        sit,
        coach,
    ) in WEEK1.items():
        r = got[pid]
        assert (r["choice"], r["best"], r["agree"], r["label"]) == (choice, best, agree, label)
        assert r["gap"] == approx(gap) and r["edge"] == approx(edge) and r["cost"] == approx(cost)
        assert r["success"] is success and r["result"] == result
        assert r["situation"] == sit and r["coach"] == coach and r["down"] == 4
        assert (r["qtr"], r["clock"], r["off_score"], r["def_score"]) == (3, "10:00", 10, 10)
        assert (r["fake"], r["aborted"], r["wiped"]) == (False, False, False)


def test_the_engines_numbers_are_carried_onto_the_row(world):
    rows, _, _ = score_week(world, 1)
    p1, p2 = by_play(rows)[1], by_play(rows)[3]
    assert (p1["wp_go"], p1["wp_fg"], p1["wp_punt"]) == approx((0.62, 0.58, 0.50))
    assert (p1["convert"], p1["wp_now"], p1["fg_make"]) == approx((0.65, 0.55, 0.8))
    assert p2["wp_fg"] is None and p2["fg_make"] is None, "a kick that isn't priced stays null"
    assert p1["boot_share"] is None and p1["yards_gained"] == 3
    assert p1["season"] == 2025 and p1["week"] == 1 and p1["game_id"] == G1
    assert p1["kickoff_utc"] is not None and p1["desc"] == ""
    assert by_play(rows)[3]["desc"] == "J.Smith punts 40 yards"


def test_the_state_given_to_the_engine_is_the_offenses_view(world):
    _, _, engine = score_week(world, 1)
    s1, s3 = engine.states[0], engine.states[2]  # HOM (home) 4th & 2 at 40, AWY 4th & 1 at 30
    assert (s1.season, s1.score_diff, s1.down, s1.ydstogo, s1.yardline_100) == (2025, 0, 4, 2, 40)
    assert (s1.game_seconds, s1.half_seconds) == (1500.0, 1500.0)
    assert (s1.off_timeouts, s1.def_timeouts, s1.receive_2h_ko) == (3, 3, 0)
    assert (s1.home, s1.spread, s1.total, s1.roof) == (1, 3.0, 44.0, "outdoors")
    assert (s1.ot, s1.playoffs, s1.wind, s1.temp) == (False, False, None, None)
    assert (s3.home, s3.spread) == (-1, -3.0), "the away offense: the line flips"


def test_a_call_with_an_unpriced_option(tmp_path):
    w = World()
    g = w.game("2025_01_A_B", 1)
    w.kick(g, "HOM", 7, 55)  # 73 yards: the engine doesn't price it
    w.snap(g, "AWY", 75)
    w.go(g, "HOM", 4, 44, converted=False, gained=1)
    w.snap(g, "AWY", 55)
    w.go(g, "HOM", 1, 33, converted=True, gained=2)
    w.snap(g, "HOM", 31)
    engine = StubEngine(
        {
            55: spec(0.45, None, 0.50),  # coach kicked an unpriced field goal
            44: spec(0.45, None, 0.50),  # coach went, field goal unpriced
            33: spec(0.50),  # only "go" is priced
        }
    )
    frame = review.decisions_frame(w.load(tmp_path))
    rows, skipped = review.score_decisions(frame, engine)
    got = {r["yardline_100"]: r for r in rows.iter_rows(named=True)}
    kick = got[55]
    assert (kick["choice"], kick["best"], kick["agree"]) == ("fg", "punt", False)
    assert kick["edge"] is None and kick["cost"] is None and kick["wp_fg"] is None
    assert kick["success"] is True and kick["result"] == "Good from 73"
    goes = got[44]
    assert goes["best"] == "punt" and goes["edge"] == approx(-0.05) and goes["cost"] == approx(0.05)
    lone = got[33]
    assert lone["best"] == "go" and lone["agree"] is True
    assert lone["edge"] is None, "no other option to compare with"
    assert lone["cost"] == 0.0 and lone["gap"] == 0.0 and skipped == 0


def test_states_the_rules_refuse_are_counted_not_scored(tmp_path):
    w = World()
    g = w.game("2025_01_A_B", 1)
    w.punt(g, "HOM", 9, 40)  # A: fine
    w.snap(g, "AWY", 80)
    w.go(g, "AWY", 5, 3, converted=False, gained=0)  # B: 5 to go from the 3
    w.snap(g, "HOM", 60)
    w.punt(g, "HOM", 9, 28, half_seconds_remaining=900.0)  # E: the half clock disagrees
    w.snap(g, "AWY", 80)
    w.punt(g, "HOM", 9, 33)  # D: fine
    w.snap(g, "AWY", 80)
    engine = StubEngine()
    frame = review.decisions_frame(w.load(tmp_path))
    assert frame.height == 4
    rows, skipped = review.score_decisions(frame, engine)
    assert rows["yardline_100"].to_list() == [40, 33] and skipped == 2
    assert engine.calls == [(2, True)], "the refused states never reach the engine"


def test_one_state_the_engine_refuses_does_not_sink_the_rest(tmp_path):
    w = World()
    g = w.game("2025_01_A_B", 1)
    for yl in (40, 22, 33):
        w.punt(g, "HOM", 9, yl)
        w.snap(g, "AWY", 80)
    engine = StubEngine(fail_on=(22,))
    frame = review.decisions_frame(w.load(tmp_path))
    rows, skipped = review.score_decisions(frame, engine)
    assert rows["yardline_100"].to_list() == [40, 33] and skipped == 1
    # the batch failed, then each state was asked on its own (bootstrap still on)
    assert engine.calls == [(3, True), (1, True), (1, True), (1, True)]


def test_nothing_to_score_gives_empty_rows(tmp_path):
    engine = StubEngine()
    rows, skipped = review.score_decisions(pl.DataFrame(), engine)
    assert rows.height == 0 and rows.columns == review.ROW_COLUMNS and skipped == 0
    w = World()
    g = w.game("2025_01_A_B", 1)
    w.go(g, "AWY", 5, 3, converted=False, gained=0)  # the only 4th down is refused
    rows, skipped = review.score_decisions(review.decisions_frame(w.load(tmp_path)), engine)
    assert rows.height == 0 and skipped == 1 and engine.calls == []


def test_fakes_and_wiped_out_downs_keep_their_flags_through_scoring(tmp_path):
    w = World()
    g = w.game("2025_01_A_B", 1)
    desc = "(Punt formation) J.Smith runs left end for 12 yards (T.Jones)."
    w.go(g, "HOM", 9, 60, converted=True, gained=12, desc=desc)
    w.snap(g, "HOM", 48)
    w.add(g, "HOM", "no_play", 4, 3, 40, desc="T.Brady pass short right to C.Davis for 5 yards. "
          "PENALTY on AWY, Defensive Holding, 5 yards - No Play.")  # fmt: skip
    w.snap(g, "HOM", 35)
    rows, _ = review.score_decisions(review.decisions_frame(w.load(tmp_path)), StubEngine())
    fake, wiped = rows.row(0, named=True), rows.row(1, named=True)
    assert fake["fake"] is True and fake["result"] == "Fake punt: Converted (+12 yds)"
    assert wiped["wiped"] is True and wiped["success"] is True
    assert wiped["result"] == "Wiped out by a penalty: first down"


# --- build_week -----------------------------------------------------------------------------------
def test_a_week_review_carries_its_stamp_and_meta(world):
    rv = review.build_week(
        2025, 1, world.plays, season_engine(), model_version="v1", trained_seasons=TRAINED,
        fixes="abc", now=NOW,
    )  # fmt: skip
    assert rv.source == "computed" and rv.rows.height == 6
    meta = rv.meta
    assert meta["stamp"] == {
        "schema": review.SCHEMA_VERSION, "model_version": "v1",
        "data": review.week_hash(world.plays, [1]),
        "fixes": "abc",
    }  # fmt: skip
    assert (meta["schema"], meta["season"], meta["week"]) == (review.SCHEMA_VERSION, 2025, 1)
    assert (meta["in_sample"], meta["trained_seasons"]) == (True, [2010, 2025])
    assert (meta["games"], meta["decisions"], meta["skipped"]) == (1, 6, 0)
    assert meta["computed_at"] == "2026-10-10T12:00:00+00:00"


def test_a_season_after_the_training_window_is_out_of_sample(world):
    rv = review.build_week(
        2026, 1, world.plays, season_engine(), model_version="v1", trained_seasons=TRAINED,
        fixes=None, now=NOW,
    )  # fmt: skip
    assert rv.meta["in_sample"] is False and rv.meta["trained_seasons"] == [2010, 2025]
    rv = review.build_week(
        2025, 1, world.plays, season_engine(), model_version="v1", trained_seasons=None,
        fixes=None, now=NOW,
    )  # fmt: skip
    assert rv.meta["in_sample"] is False and rv.meta["trained_seasons"] is None


def test_a_week_with_no_plays_reviews_to_nothing(world):
    engine = season_engine()
    rv = review.build_week(
        2025, 9, world.plays, engine, model_version="v1", trained_seasons=TRAINED, fixes=None
    )
    assert rv.rows.height == 0 and rv.meta["games"] == 0 and rv.meta["decisions"] == 0
    assert engine.calls == []
    nothing = review.build_week(
        2025, 1, pl.DataFrame(), engine, model_version="v1", trained_seasons=None, fixes=None
    )
    assert nothing.rows.height == 0 and nothing.meta["games"] == 0


# --- week_summary ---------------------------------------------------------------------------------
def test_the_week_summary_counts_the_calls(world):
    rows, _, _ = score_week(world, 1)
    s = review.week_summary(rows)
    assert s == {
        "decisions": 6,
        "coach": {"go": 3, "fg": 1, "punt": 2},
        "bot": {"go": 4, "fg": 0, "punt": 2},
        "agree": 3, "toss_ups": 1, "go_spots": 3, "went_in_go_spots": 1, "wp_lost": 0.17,
        "fakes": 0, "wiped": 0,
    }  # fmt: skip


def test_an_empty_week_summary_is_all_zeros():
    s = review.week_summary(make_rows([]))
    assert s["decisions"] == 0 and s["coach"] == {"go": 0, "fg": 0, "punt": 0}
    assert s["bot"] == {"go": 0, "fg": 0, "punt": 0} and s["wp_lost"] == 0.0
    assert (s["agree"], s["toss_ups"], s["go_spots"], s["went_in_go_spots"]) == (0, 0, 0, 0)
    assert (s["fakes"], s["wiped"]) == (0, 0)


def test_the_summary_counts_fakes_and_wiped_downs_and_ignores_unpriced_costs():
    rows = make_rows(
        [
            {"fake": True},
            {"wiped": True},
            {"wiped": True, "choice": "punt", "best": "go", "cost": 0.04},
            {"choice": "fg", "best": "go", "cost": None},
        ]
    )
    s = review.week_summary(rows)
    assert (s["fakes"], s["wiped"], s["wp_lost"]) == (1, 2, 0.04)
    assert s["go_spots"] == 4 and s["went_in_go_spots"] == 2


# --- highlights -----------------------------------------------------------------------------------
def keys(*pairs):
    return [{"game_id": g, "play_id": p} for g, p in pairs]


def test_the_costliest_calls_in_order_with_ties_by_game_and_play():
    rows = make_rows(
        [
            {"game_id": "G2", "play_id": 5, "cost": 0.10},
            {"game_id": "G1", "play_id": 9, "cost": 0.10},
            {"game_id": "G3", "play_id": 1, "cost": 0.25},
            {"game_id": "G1", "play_id": 2, "cost": 0.0},  # agreed: not a cost
            {"game_id": "G1", "play_id": 3, "cost": None},  # unpriced: not a cost
            {"game_id": "G4", "play_id": 1, "cost": 0.02},
            {"game_id": "G4", "play_id": 2, "cost": 0.07},
            {"game_id": "G4", "play_id": 3, "cost": 0.05},
        ]
    )
    hl = review.highlights(rows)
    assert hl["costliest"] == {"game_id": "G3", "play_id": 1}
    assert hl["top"] == keys(("G3", 1), ("G1", 9), ("G2", 5), ("G4", 2), ("G4", 3))


def test_fewer_than_five_costly_calls_and_none_at_all():
    two = review.highlights(make_rows([{"cost": 0.1, "play_id": 1}, {"cost": 0.0, "play_id": 2}]))
    assert two["top"] == keys(("G1", 1)) and two["costliest"] == {"game_id": "G1", "play_id": 1}
    none = review.highlights(make_rows([{"cost": 0.0}, {"cost": None}]))
    assert none == {"costliest": None, "top": [], "boldest": None}
    empty = review.highlights(make_rows([]))
    assert empty == {"costliest": None, "top": [], "boldest": None}


def go_row(g, p, convert, wp_now, edge=0.0, **kw):
    """A go-for-it a bold-call test can look at (edge: its WP minus the best other option's)."""
    return {"game_id": g, "play_id": p, "choice": "go", "convert": convert, "wp_now": wp_now,
            "edge": edge, **kw}  # fmt: skip


def boldest(*specs):
    return review.highlights(make_rows(list(specs)))["boldest"]


def test_the_boldest_call_is_a_go_in_a_game_still_in_doubt_with_the_lowest_chance():
    rows = make_rows(
        [
            go_row("B1", 1, 0.30, 0.95),  # the game is decided
            go_row("B1", 2, 0.25, 0.05),  # the game is decided
            go_row("B1", 3, 0.40, 0.50),
            go_row("B2", 1, 0.35, 0.10, ydstogo=3),  # the edge of the range counts
            go_row("B2", 2, 0.35, 0.90, ydstogo=7),  # same chance: the longer distance first
            {"game_id": "B3", "play_id": 1, "choice": "punt", "best": "punt", "convert": 0.10},
        ]
    )
    assert review.highlights(rows)["boldest"] == {"game_id": "B2", "play_id": 2}
    only = rows.filter(~((pl.col("game_id") == "B2") & (pl.col("play_id") == 2)))
    assert review.highlights(only)["boldest"] == {"game_id": "B2", "play_id": 1}
    decided = rows.filter(pl.col("game_id") == "B1").filter(pl.col("play_id") < 3)
    assert review.highlights(decided)["boldest"] is None


def test_a_forced_go_is_not_bold():
    """Kicking has to be a real option: the go's WP less than 5 points above the best other."""
    edge = review.BOLD_MAX_EDGE
    assert edge == 0.05
    assert boldest(go_row("F", 1, 0.20, 0.5, edge=edge)) is None, "exactly 5 points: forced"
    assert boldest(go_row("F", 1, 0.20, 0.5, edge=0.12)) is None, "a desperate 4th & 13"
    assert boldest(go_row("F", 1, 0.20, 0.5, edge=None)) is None, "a go with no other option"
    assert boldest(go_row("F", 1, 0.20, 0.5, edge=0.049)) == {"game_id": "F", "play_id": 1}
    assert boldest(go_row("F", 1, 0.20, 0.5, edge=edge - 0.0001)) == {"game_id": "F", "play_id": 1}
    assert boldest(go_row("F", 1, 0.20, 0.5, edge=-0.30)) == {"game_id": "F", "play_id": 1}
    # the forced one has the lowest chance, and is passed over
    pick = boldest(go_row("F", 1, 0.05, 0.5, edge=0.20), go_row("F", 2, 0.45, 0.5, edge=0.01))
    assert pick == {"game_id": "F", "play_id": 2}


def test_a_go_wiped_out_by_a_penalty_is_not_the_boldest():
    pick = boldest(go_row("W", 1, 0.05, 0.5, wiped=True), go_row("W", 2, 0.40, 0.5))
    assert pick == {"game_id": "W", "play_id": 2}
    assert boldest(go_row("W", 1, 0.05, 0.5, wiped=True)) is None


def test_the_boldest_ties_fall_to_the_first_game_and_play():
    rows = make_rows(
        [
            go_row("B2", 1, 0.3, 0.5, ydstogo=4),
            go_row("B1", 9, 0.3, 0.5, ydstogo=4),
            go_row("B1", 2, 0.3, 0.5, ydstogo=4),
        ]
    )
    assert review.highlights(rows)["boldest"] == {"game_id": "B1", "play_id": 2}


def test_the_weeks_highlights_on_the_season_world(world):
    rows, _, _ = score_week(world, 1)
    hl = review.highlights(rows)
    assert hl["costliest"] == {"game_id": G1, "play_id": 11}
    assert hl["top"] == keys((G1, 11), (G1, 5), (G1, 3))
    assert hl["boldest"] == {"game_id": G1, "play_id": 11}, "a go at a 30% chance"


# --- leaderboard ----------------------------------------------------------------------------------
def coach_rows(coach, posteam, items, week=1):
    """items: (best, choice, label, cost) tuples."""
    return [
        {"coach": coach, "posteam": posteam, "week": week, "best": b, "choice": c, "label": lab,
         "cost": cost}
        for b, c, lab, cost in items
    ]  # fmt: skip


def board_rows():
    return make_rows(
        [
            *coach_rows(
                "Xavier", "T1", [("go", "go", "Confident", 0.0), ("go", "punt", "Lean", 0.05)]
            ),  # fmt: skip
            *coach_rows("Xavier", "T1", [("go", "punt", "Toss-up", 0.01)], week=2),
            *coach_rows("Xavier", "T2", [("punt", "go", "Confident", 0.08)], week=3),
            *coach_rows("Yolanda", "T3", [("go", "go", "Confident", 0.0)] * 2),
            *coach_rows(
                "Zed", "T4", [("punt", "punt", "Confident", 0.0), ("go", "go", "Toss-up", 0.0)]
            ),  # fmt: skip
            *coach_rows(
                "Walt",
                "T5",
                [("go", "go", "Lean", 0.0)] * 2
                + [("go", "punt", "Confident", 0.02), ("go", "fg", "Confident", None)],
            ),  # fmt: skip
            *coach_rows(
                "Abe", "T6", [("go", "go", "Confident", 0.0), ("go", "punt", "Confident", 0.03)]
            ),  # fmt: skip
            *coach_rows(None, "T7", [("go", "punt", "Confident", 0.5)]),
        ]
    )


def test_the_coach_leaderboard_is_ranked_by_the_go_rate_in_go_spots():
    board, league = review.leaderboard(board_rows())
    assert board["coach"].to_list() == ["Yolanda", "Walt", "Abe", "Xavier", "Zed"]
    assert board["rank"].to_list() == [1, 2, 3, 4, 5]
    got = {r["coach"]: r for r in board.iter_rows(named=True)}
    x = got["Xavier"]
    assert (x["decisions"], x["go"], x["go_spots"], x["went_in_go_spots"]) == (4, 2, 2, 1)
    assert (x["kick_spots"], x["went_in_kick_spots"], x["agree"], x["weeks"]) == (1, 1, 1, 3)
    assert x["go_rate"] == approx(0.5) and x["go_rate_spots"] == approx(0.5)
    assert x["go_rate_kick_spots"] == approx(1.0) and x["agree_rate"] == approx(0.25)
    # timid / bold count only clear spots: the 0.01 toss-up kick is neither (Sol review)
    assert x["wp_lost"] == approx(0.14) and x["wp_lost_timid"] == approx(0.05)
    assert x["wp_lost_bold"] == approx(0.08)
    assert x["teams"] == ["T2", "T1"], "newest team first"
    y = got["Yolanda"]
    assert (y["go_spots"], y["went_in_go_spots"], y["go_rate_spots"]) == (2, 2, 1.0)
    assert y["kick_spots"] == 0 and y["go_rate_kick_spots"] is None, "no spots, no rate"
    z = got["Zed"]
    assert z["go_spots"] == 0 and z["go_rate_spots"] is None, "unranked rates go last"
    assert (z["kick_spots"], z["went_in_kick_spots"], z["go_rate"]) == (1, 0, approx(0.5))
    assert z["wp_lost"] == 0.0


def test_ties_in_the_rate_fall_to_more_go_spots_then_to_the_name():
    board, _ = review.leaderboard(board_rows())
    got = {r["coach"]: r for r in board.iter_rows(named=True)}
    for name in ("Walt", "Abe", "Xavier"):
        assert got[name]["go_rate_spots"] == approx(0.5)
    assert [got[n]["go_spots"] for n in ("Walt", "Abe", "Xavier")] == [4, 2, 2]
    assert board["coach"].to_list().index("Abe") < board["coach"].to_list().index("Xavier")


def test_an_unpriced_call_costs_nothing_on_the_board():
    board, _ = review.leaderboard(board_rows())
    walt = board.filter(pl.col("coach") == "Walt").row(0, named=True)
    assert walt["go_spots"] == 4 and walt["went_in_go_spots"] == 2 and walt["agree"] == 2
    assert walt["wp_lost"] == approx(0.02) and walt["wp_lost_timid"] == approx(0.02)
    assert walt["wp_lost_bold"] == 0.0


def test_the_league_row_counts_every_decision_even_without_a_coach():
    board, league = review.leaderboard(board_rows())
    assert board.height == 5, "the coach-less decision isn't on the board"
    assert (league["decisions"], league["go"], league["go_spots"]) == (15, 8, 11)
    assert (league["went_in_go_spots"], league["kick_spots"], league["went_in_kick_spots"]) == (
        6, 2, 1,
    )  # fmt: skip
    assert league["agree"] == 8 and league["weeks"] == 3
    assert league["go_rate"] == approx(8 / 15, abs=1e-4)
    assert league["go_rate_spots"] == approx(6 / 11, abs=1e-4)
    assert league["go_rate_kick_spots"] == approx(0.5)
    assert league["agree_rate"] == approx(8 / 15, abs=1e-4)
    assert league["wp_lost"] == approx(0.69) and league["wp_lost_timid"] == approx(0.60)
    assert league["wp_lost_bold"] == approx(0.08)
    assert "coach" not in league and "rank" not in league


def test_the_leaderboard_of_nothing_and_of_no_known_coach():
    board, league = review.leaderboard(make_rows([]))
    assert board.height == 0 and league == {}
    board, league = review.leaderboard(
        make_rows(coach_rows(None, "T7", [("go", "go", "Lean", 0.0)]))
    )
    assert board.height == 0 and league["decisions"] == 1


def test_the_season_worlds_board_through_week_two(world):
    rows = pl.concat([score_week(world, 1)[0], score_week(world, 2)[0]])
    board, league = review.leaderboard(rows)
    assert board["coach"].to_list() == ["Home Coach", "Away Coach"]
    home, away = board.row(0, named=True), board.row(1, named=True)
    assert (home["decisions"], home["go"], home["go_spots"], home["went_in_go_spots"]) == (
        4,
        2,
        2,
        1,
    )
    assert (home["kick_spots"], home["went_in_kick_spots"], home["agree"]) == (1, 0, 3)
    assert home["wp_lost"] == approx(0.03) and home["wp_lost_timid"] == approx(0.03)
    assert home["wp_lost_bold"] == 0.0 and home["teams"] == ["HOM"] and home["weeks"] == 2
    assert (away["decisions"], away["go"], away["go_spots"], away["went_in_go_spots"]) == (
        5,
        2,
        3,
        1,
    )
    assert (away["kick_spots"], away["went_in_kick_spots"], away["agree"]) == (2, 1, 2)
    assert away["go_rate_spots"] == approx(1 / 3, abs=1e-4) and away["agree_rate"] == approx(0.4)
    assert away["wp_lost"] == approx(0.19) and away["wp_lost_timid"] == approx(0.11)
    assert away["wp_lost_bold"] == approx(0.08)
    assert (league["decisions"], league["go_spots"], league["went_in_go_spots"]) == (9, 5, 2)
    assert league["wp_lost"] == approx(0.22)


# --- trend ----------------------------------------------------------------------------------------
def test_the_weekly_trend():
    rows = make_rows(
        [
            {"week": 1, "choice": "go", "best": "go", "success": True, "convert": 0.6, "cost": 0.0},
            {
                "week": 1,
                "choice": "go",
                "best": "punt",
                "success": False,
                "convert": 0.3,
                "cost": 0.08,
            },  # fmt: skip
            {"week": 1, "choice": "punt", "best": "go", "success": None, "cost": 0.05},
            {
                "week": 1,
                "choice": "go",
                "best": "go",
                "success": True,
                "convert": 0.5,
                "wiped": True,
            },
            {"week": 1, "choice": "fg", "best": "fg", "success": True},
            {"week": 2, "choice": "punt", "best": "punt", "cost": 0.0},
        ][::-1]  # the weeks come out sorted whatever the order they went in
    )
    t = review.trend(rows)
    assert [r["week"] for r in t] == [1, 2]
    w1, w2 = t
    assert w1["decisions"] == 5 and w1["go_rate_coach"] == approx(0.6)
    assert w1["go_rate_bot"] == approx(0.6) and w1["agree_rate"] == approx(0.6)
    assert w1["wp_lost"] == approx(0.13)
    assert w1["attempts"] == 2, "a wiped-out go and a punt aren't attempts"
    assert w1["conv_actual"] == approx(0.5) and w1["conv_pred"] == approx(0.45)
    assert w2["decisions"] == 1 and w2["go_rate_coach"] == 0.0 and w2["agree_rate"] == 1.0
    assert w2["attempts"] == 0 and w2["conv_actual"] is None and w2["conv_pred"] is None


# --- calibration ----------------------------------------------------------------------------------
def calibration_world() -> World:
    """Two finished games and a tie, twelve snaps with a Vegas chance (and one without)."""
    w = World()
    g1 = w.game("2025_01_AWY_HOM", 1, "HOM", "AWY", home_score=24, away_score=17)
    g2 = w.game("2025_02_HOM_AWY", 2, "AWY", "HOM", home_score=20, away_score=23)
    g3 = w.game("2025_03_AWY_HOM", 3, "HOM", "AWY", home_score=20, away_score=20)
    # (game, offense, down, ytg, yl, offense's lead, Vegas chance)
    snaps = [
        (g1, "HOM", 1, 10, 50, 0, 0.60), (g1, "HOM", 2, 5, 40, 7, 0.90),
        (g1, "AWY", 1, 10, 60, -7, 0.10), (g1, "AWY", 3, 2, 30, -3, 0.40),
        (g1, "HOM", 4, 2, 35, 0, None),
        (g2, "HOM", 1, 10, 70, 3, 0.70), (g2, "AWY", 2, 8, 45, -3, 0.30),
        (g2, "HOM", 3, 7, 40, 0, 0.55), (g2, "AWY", 4, 12, 40, -3, 0.30),
    ]  # fmt: skip
    gained = {4: 3, 5: 0, 8: 8, 9: 1}  # by snap number: the 3rd and 4th downs' yards
    for i, (g, team, down, ytg, yl, lead, vegas) in enumerate(snaps, start=1):
        ptype = "pass" if down == 4 and g == g2 else "run"
        extra = {"yards_gained": gained[i]} if i in gained else {}
        w.add(g, team, ptype, down, ytg, yl, score_differential=lead, vegas_wp=vegas, **extra)
    for team, yl, dist, result, lead, vegas in (
        ("HOM", 27, 45, "made", 0, 0.60), ("AWY", 17, 35, "missed", -3, 0.30),
        ("AWY", 34, 52, "blocked", -3, 0.20),
    ):  # fmt: skip
        w.add(g2, team, "field_goal", 4, 8, yl, kick_distance=float(dist), field_goal_result=result,
              score_differential=lead, vegas_wp=vegas)  # fmt: skip
    w.add(g3, "HOM", "run", 1, 10, 50)
    w.add(g3, "AWY", "run", 1, 10, 50)
    return w


@pytest.fixture(scope="module")
def cal(tmp_path_factory):
    w = calibration_world()
    plays = w.load(tmp_path_factory.mktemp("cal"))
    return review.calibration(plays, StubEngine().m)


def fg_p(d: float) -> float:
    return 1.0 / (1.0 + math.exp(-(5.22 - 0.09 * d)))


def test_the_win_probability_calibration(cal):
    wp = cal["wp"]
    # snaps: every one with a down in a finished game with a Vegas chance (the tie game and the
    # snap with no Vegas chance are out); (lead, yards to goal, who won, the Vegas chance)
    snaps = [
        (0, 50, 1, 0.60), (7, 40, 1, 0.90), (-7, 60, 0, 0.10), (-3, 30, 0, 0.40),
        (3, 70, 1, 0.70), (-3, 45, 0, 0.30), (0, 40, 1, 0.55), (-3, 40, 0, 0.30),
        (0, 27, 1, 0.60), (-3, 17, 0, 0.30), (-3, 34, 0, 0.20),
    ]  # fmt: skip
    p = np.array([stub_wp_value(lead, 1500.0, yl) for lead, yl, _, _ in snaps])
    y = np.array([won for *_, won, _ in snaps], dtype=float)
    v = np.array([vegas for *_, vegas in snaps])
    assert (wp["snaps"], wp["games"], wp["tie_games"]) == (11, 2, 1)
    assert wp["brier"] == approx(float(np.mean((p - y) ** 2)), abs=1e-4)
    assert wp["brier_vegas"] == approx(float(np.mean((v - y) ** 2)), abs=1e-4)

    def bins(q):
        out = []
        for b in range(10):
            hit = np.minimum((q * 10).astype(int), 9) == b
            if hit.any():
                out.append((b / 10, int(hit.sum()), float(q[hit].mean()), float(y[hit].mean())))
        return out

    for key, q in (("model", p), ("vegas", v)):
        want = bins(q)
        assert [(b["bin_lo"], b["n"]) for b in wp[key]] == [(lo, n) for lo, n, _, _ in want]
        for got, (_, _, mean_pred, mean_out) in zip(wp[key], want, strict=True):
            assert got["mean_pred"] == approx(mean_pred, abs=1e-4)
            assert got["mean_outcome"] == approx(mean_out, abs=1e-4)
    ece = sum(abs(mp - mo) * n for _, n, mp, mo in bins(p)) / len(p)
    assert wp["ece"] == approx(ece, abs=1e-4)
    assert wp["ece_vegas"] == approx(
        sum(abs(mp - mo) * n for _, n, mp, mo in bins(v)) / len(v), abs=1e-4
    )
    assert sum(b["n"] for b in wp["model"]) == 11


def test_the_conversion_calibration_by_down_and_distance(cal):
    conv = cal["conversion"]
    rows = [(r["down"], r["distance"], r["label"], r["n"], r["actual"], r["pred"])
            for r in conv["rows"]]  # fmt: skip
    # the stub bundle gives every 3rd / 4th down a 50% chance to gain 5 yards and 50% to gain
    # nothing: a 50% chance to convert up to 5 yards to go and none beyond
    assert rows == [
        (3, 2, "2", 1, 1.0, 0.5), (3, 7, "7", 1, 1.0, 0.0),
        (4, 2, "2", 1, 0.0, 0.5), (4, 10, "10+", 1, 0.0, 0.0),
    ]  # fmt: skip
    d3, d4 = conv["overall"]
    assert (d3["down"], d3["n"], d3["actual"], d3["pred"]) == (3, 2, 1.0, 0.25)
    assert d3["brier"] == approx(0.625)  # ((0.5 - 1)^2 + (0 - 1)^2) / 2
    assert (d4["down"], d4["n"], d4["actual"], d4["pred"]) == (4, 2, 0.0, 0.25)
    assert d4["brier"] == approx(0.125)  # ((0.5 - 0)^2 + 0) / 2


def test_the_field_goal_calibration_by_distance_band(cal):
    fg = cal["fg"]
    assert fg["n"] == 3 and fg["made"] == approx(1 / 3, abs=1e-4)
    assert fg["pred"] == approx((fg_p(35) + fg_p(45) + fg_p(52)) / 3, abs=1e-4)
    assert [(r["band"], r["n"], r["made"]) for r in fg["rows"]] == [
        ("30-39", 1, 0.0), ("40-49", 1, 1.0), ("50+", 1, 0.0),
    ]  # fmt: skip
    for r, d in zip(fg["rows"], (35, 45, 52), strict=True):
        assert r["pred"] == approx(fg_p(d), abs=1e-4)


def test_calibration_of_no_plays_is_empty():
    assert review.calibration(pl.DataFrame(), StubEngine().m) == {
        "wp": None, "conversion": None, "fg": None,
    }  # fmt: skip


def test_without_a_vegas_chance_only_the_win_probability_is_missing(tmp_path):
    w = calibration_world()
    for r in w.rows:
        r["vegas_wp"] = None
    out = review.calibration(w.load(tmp_path), StubEngine().m)
    assert out["wp"] is None and out["conversion"] is not None and out["fg"] is not None


# --- season_tables --------------------------------------------------------------------------------
def season_rows(world, weeks=(1, 2, 4)):
    engine = season_engine()
    return pl.concat([score_week(world, w, engine)[0] for w in weeks])


def test_the_season_tables_through_a_week(world):
    rows = season_rows(world)
    t = review.season_tables(
        2025, 2, rows, world.plays, season_engine().m, model_version="v1",
        trained_seasons=TRAINED, fixes="abc", now=NOW,
    )  # fmt: skip
    assert (t["schema"], t["season"], t["through_week"], t["weeks"]) == (
        review.SCHEMA_VERSION, 2025, 2, [1, 2],
    )  # fmt: skip
    assert t["decisions"] == 9, "week 4 is after the cut"
    assert t["stamp"] == {
        "schema": review.SCHEMA_VERSION, "model_version": "v1",
        "data": review.week_hash(world.plays, [1, 2]),
        "fixes": "abc",
    }  # fmt: skip
    assert (t["in_sample"], t["trained_seasons"], t["model_version"]) == (True, [2010, 2025], "v1")
    assert [r["coach"] for r in t["leaderboard"]] == ["Home Coach", "Away Coach"]
    assert t["leaderboard"][0]["rank"] == 1 and t["league"]["decisions"] == 9
    assert [r["week"] for r in t["trend"]] == [1, 2]
    assert t["computed_at"] == "2026-10-10T12:00:00+00:00"
    cal = t["calibration"]
    assert cal["conversion"] is not None and cal["fg"]["n"] == 1
    assert cal["wp"]["games"] == 2, "only the games through week 2"


def test_the_season_tables_through_the_last_week(world):
    t = review.season_tables(
        2025, 4, season_rows(world), world.plays, season_engine().m, model_version="v1",
        trained_seasons=TRAINED, fixes=None,
    )  # fmt: skip
    assert t["weeks"] == [1, 2, 4] and t["decisions"] == 10
    assert t["league"]["go_spots"] == 6 and t["league"]["weeks"] == 3
    home = t["leaderboard"][0]
    assert home["coach"] == "Home Coach" and home["go_rate_spots"] == approx(2 / 3, abs=1e-4)


def test_season_tables_of_no_reviews(world):
    t = review.season_tables(
        2025, 3, make_rows([]), world.plays.head(0), season_engine().m, model_version=None,
        trained_seasons=None, fixes=None,
    )  # fmt: skip
    assert t["weeks"] == [] and t["decisions"] == 0 and t["leaderboard"] == []
    assert t["league"] == {} and t["trend"] == []
    assert t["calibration"] == {"wp": None, "conversion": None, "fg": None}
    assert t["in_sample"] is False


# --- the answers the app shows --------------------------------------------------------------------
def test_the_week_payload(world):
    engine = season_engine()
    rv = review.build_week(
        2025, 1, world.plays, engine, model_version="v1", trained_seasons=TRAINED, fixes=None,
        now=NOW,
    )  # fmt: skip
    games = review.week_games(world.paths, 2025, 1)
    p = review.week_payload(rv, games)
    assert p["model"] == {"version": "v1", "in_sample": True, "trained_seasons": [2010, 2025]}
    assert p["computed_at"] == "2026-10-10T12:00:00+00:00" and p["source"] == "computed"
    assert p["summary"]["decisions"] == 6 and p["summary"]["skipped"] == 0
    assert [x["play_id"] for x in p["plays"]] == list(WEEK1)
    top = p["highlights"]
    assert top["costliest"]["play_id"] == 11 and top["boldest"]["play_id"] == 11
    assert [x["play_id"] for x in top["top"]] == [11, 5, 3]
    assert top["costliest"] == p["plays"][-1], "the highlight is the play's own row"
    first = p["plays"][0]
    assert first["wp"] == {"go": 0.62, "fg": 0.58, "punt": 0.5}
    assert (first["edge"], first["cost"], first["convert"]) == (0.04, 0.0, 0.65)
    assert (first["fg_distance"], first["result"], first["coach"]) == (40.0, "Converted (+3 yds)",
                                                                       "Home Coach")  # fmt: skip
    assert set(first) >= {"game_id", "play_id", "posteam", "defteam", "qtr", "clock", "situation",
                          "choice", "best", "agree", "label", "gap", "wp_now", "fake", "wiped",
                          "success", "desc"}  # fmt: skip
    (game,) = p["games"]
    assert (game["game_id"], game["away"], game["home"]) == (G1, "AWY", "HOM")
    assert (game["away_score"], game["home_score"], game["decisions"]) == (17, 24, 6)
    assert game["wp_lost"] == {"AWY": approx(0.14), "HOM": approx(0.03)}
    assert game["kickoff"].startswith("2025-09-07T17:00")


def test_the_week_payload_without_games_or_decisions(world):
    rv = review.build_week(
        2025, 9, world.plays, season_engine(), model_version="v1", trained_seasons=TRAINED,
        fixes=None,
    )  # fmt: skip
    p = review.week_payload(rv, None)
    assert p["plays"] == [] and p["games"] == []
    assert p["highlights"] == {"boldest": None, "costliest": None, "top": []}
    assert p["summary"]["decisions"] == 0


def test_the_week_payload_names_every_game_even_without_decisions(world):
    rv = review.build_week(
        2025, 2, world.plays, season_engine(), model_version="v1", trained_seasons=TRAINED,
        fixes=None,
    )  # fmt: skip
    games = pl.concat([review.week_games(world.paths, 2025, 2),
                       review.week_games(world.paths, 2025, 4)])  # fmt: skip
    p = review.week_payload(rv, games)
    assert [g["game_id"] for g in p["games"]] == [G2, "2025_04_AWY_HOM"]
    assert [g["decisions"] for g in p["games"]] == [3, 0]
    assert p["games"][1]["wp_lost"] == {"AWY": 0.0, "HOM": 0.0}


def test_the_season_payload_keeps_the_fields_the_app_shows(world):
    t = review.season_tables(
        2025, 2, season_rows(world), world.plays, season_engine().m, model_version="v1",
        trained_seasons=TRAINED, fixes=None, now=NOW,
    )  # fmt: skip
    p = review.season_payload(t, "memory")
    assert set(p) == {"model", "source", "through_week", "weeks", "decisions", "leaderboard",
                      "league", "trend", "calibration", "computed_at"}  # fmt: skip
    assert p["source"] == "memory" and p["model"]["version"] == "v1"
    assert "stamp" not in p and "schema" not in p
    assert review.season_payload(t)["source"] == "computed"


def test_the_stored_row_schema_is_the_documented_one(world):
    rows, _, _ = score_week(world, 1)
    assert rows.schema == pl.Schema(review._ROW_SCHEMA)  # noqa: SLF001
    assert list(review._ROW_SCHEMA) == review.ROW_COLUMNS  # noqa: SLF001
