"""PC03: the play diagrams' text helpers, results, tags, concepts and the six hand-worked plays
(documentation/play-calling/PC03-play-diagrams.md: "unit-tested on a pass, a run, a screen, a
play-action deep shot, a sack and a scramble").

`src/nflengine/playcalling/diagrams.py` is pure: a `plays_enriched` row (a dict) in, the
geometry out. Every number below was worked out by hand from the constants the module documents
(the hash marks, the QB depths, the throw's landing third, the run lanes), then compared with
what the code returns; the sweeps and the finer geometry are in
`test_playcall_diagram_geometry.py`.

Coordinates (README of the module): x from the offense's own goal line (0) to the opponent's
(100), y from the offense's LEFT sideline (0) to its right (53.33).
"""

from __future__ import annotations

import math

import pytest
from playcall_diagram_fixtures import (
    ALWAYS_UNKNOWN,
    aborted_row,
    check_diagram,
    deep_shot_row,
    end_of,
    fumble_row,
    make_row,
    marks,
    phase,
    phase_names,
    placeholder_ftn_row,
    player,
    players,
    row_2019,
    row_2025,
    run_row,
    sack_row,
    scramble_row,
    screen_row,
    start_of,
    td_pass,
    td_run,
)

from nflengine.playcalling import diagrams as D

MINUS = "−"  # the texts use a real minus sign
MIDDLE = 160 / 3 / 2  # 26.666...
W = 160 / 3  # the field's width


def near(value: float, want: float, tol: float = 0.011) -> bool:
    """Keyframes are rounded to 2 decimals."""
    return abs(value - want) <= tol


def at(p: list, x: float, y: float) -> None:
    """A keyframe [t, x, y] (or a ball key [t, x, y, h]) is at (x, y)."""
    assert near(p[1], x) and near(p[2], y), (p, x, y)


def pt(p: list, x: float, y: float) -> None:
    """A mark's point [x, y] is at (x, y)."""
    assert near(p[0], x) and near(p[1], y), (p, x, y)


# ---- the label and the constants ---------------------------------------------------------------


def test_the_label_is_the_exact_sentence_and_every_answer_carries_it():
    assert D.LABEL == "Reconstructed from charting data, not player tracking"
    for row in (make_row(), run_row(), sack_row(), scramble_row(), aborted_row(), row_2019()):
        d = D.diagram(row)
        assert d["label"] == D.LABEL and d["kind"] == "reconstruction" and d["version"] == 1


def test_the_field_constants_are_the_nfl_ones():
    assert pytest.approx(53.3333, abs=1e-3) == D.FIELD_WIDTH  # 160 feet
    assert D.HASH_Y["left"] == 23.58  # 70 ft 9 in from the sideline
    assert D.HASH_Y["middle"] == pytest.approx(MIDDLE)
    assert D.HASH_Y["right"] == pytest.approx(D.FIELD_WIDTH - 23.58)
    assert {
        "left": 10.5,
        "middle": pytest.approx(MIDDLE),
        "right": pytest.approx(D.FIELD_WIDTH - 10.5),
    } == D.PASS_Y
    # the spots measured in 2023 tracking (guide §10.3): the QB at the snap and at the throw,
    # the widest receiver and the slots from the sideline, the defenders' depth at the snap
    assert D.QB_DEPTH == {"under_center": 1.5, "shotgun": 5.0, "pistol": 5.0}
    assert D.DROP_DEPTH == {"under_center": 8.3, "shotgun": 7.8, "pistol": 7.5}
    assert all(D.DROP_DEPTH[a] > D.QB_DEPTH[a] for a in D.QB_DEPTH)  # he drops back
    assert (D.WIDE_Y, D.SLOT_Y, D.SLOT2_Y) == (10.5, 16.0, 20.0)
    assert D.WIDE_Y < D.SLOT_Y < D.SLOT2_Y
    assert (D.DL_X, D.LB_X, D.CB_X, D.OVERHANG_X, D.SAFETY_X) == (0.7, 4.4, 5.0, 5.0, 11.0)
    assert D.DL_X < D.LB_X < D.CB_X <= D.OVERHANG_X < D.SAFETY_X  # line, backers, corners, deep
    assert D.LANE_OFFSET == {"middle": 0.0, "guard": 1.3, "tackle": 2.6, "end": 5.5}
    assert D.SCRAMBLE_OFFSET == {"left": -6.0, "middle": 0.0, "right": 6.0}
    assert set(D.CONCEPTS) == {
        "screen", "rpo", "play_action_deep", "inside_zone", "outside_zone", "cover_1",
        "cover_3", "2_man",
    }  # fmt: skip
    assert D.LAST_PARTICIPATION == 2025


# ---- the text helpers --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ytg", "want"),
    [
        (75, "KC 25"),  # own half
        (99, "KC 1"),
        (51, "KC 49"),
        (50, "50"),
        (49, "DEN 49"),  # the opponent's half
        (35, "DEN 35"),
        (1, "DEN 1"),
        (35.0, "DEN 35"),
        ("35", "DEN 35"),
        (None, None),
        (float("nan"), None),
        ("x", None),
    ],
)
def test_spot_text(ytg, want):
    assert D.spot_text(ytg, "KC", "DEN") == want


@pytest.mark.parametrize(
    ("quarter", "half_seconds", "want"),
    [
        (1, 1800, "15:00"),
        (1, 1515, "10:15"),
        (1, 900, "0:00"),
        (2, 900, "15:00"),
        (2, 615, "10:15"),
        (2, 0, "0:00"),
        (3, 1799, "14:59"),
        (3, 905, "0:05"),
        (4, 59, "0:59"),
        (4, 900, "15:00"),
        (5, 600, "10:00"),  # overtime
        (5, 0, "0:00"),
        (6, 600, "10:00"),
        (2, 1200, "15:00"),  # clamped to a quarter
        (1, 100, "0:00"),
        (4, -5, "0:00"),
        (None, 600, None),
        (2, None, None),
    ],
)
def test_clock_text(quarter, half_seconds, want):
    assert D.clock_text(quarter, half_seconds) == want


@pytest.mark.parametrize(
    ("quarter", "want"),
    [(1, "Q1"), (2, "Q2"), (3, "Q3"), (4, "Q4"), (5, "OT"), (6, "OT"), (2.0, "Q2"), (None, None)],
)
def test_quarter_text(quarter, want):
    assert D.quarter_text(quarter) == want


@pytest.mark.parametrize(
    ("down", "dist", "gtg", "want"),
    [
        (1, 10, False, "1st & 10"),
        (2, 3, False, "2nd & 3"),
        (3, 1, False, "3rd & 1"),
        (4, 22, False, "4th & 22"),
        (1, 8, True, "1st & Goal"),
        (3, 2, True, "3rd & Goal"),
        (3, None, False, "3rd"),
        (None, 10, False, None),
        (0, 10, False, None),
        (5, 10, False, None),
        (float("nan"), 10, False, None),
    ],
)
def test_down_text(down, dist, gtg, want):
    assert D.down_text(down, dist, gtg) == want


def test_down_text_goal_to_go_defaults_to_false():
    assert D.down_text(2, 7) == "2nd & 7"


@pytest.mark.parametrize(
    ("desc", "want"),
    [
        ("(14:56) (Shotgun) 15-P.Mahomes pass", "(Shotgun) 15-P.Mahomes pass"),
        ("(:05) (Shotgun) 15-R.Leonard pass", "(Shotgun) 15-R.Leonard pass"),  # under a minute
        ("(0:59) 10-I.Pacheco up the middle", "10-I.Pacheco up the middle"),
        ("  (9:03)   10-I.Pacheco up the middle", "10-I.Pacheco up the middle"),
        ("( 3:07 ) 10-I.Pacheco up the middle", "10-I.Pacheco up the middle"),
        ("10-I.Pacheco up the middle (14:56) later", "10-I.Pacheco up the middle (14:56) later"),
        ("(Shotgun) 15-P.Mahomes pass", "(Shotgun) 15-P.Mahomes pass"),
        ("   ", None),
        ("", None),
        (None, None),
        (float("nan"), None),
        (7, None),
    ],
)
def test_clean_desc_drops_only_the_leading_clock(desc, want):
    assert D.clean_desc(desc) == want


@pytest.mark.parametrize(
    ("n", "want"),
    [
        (22, "22 air yards"),
        (22.4, "22 air yards"),
        (2, "2 air yards"),
        (1, "1 air yard"),
        (0, "at the line"),
        (0.4, "at the line"),
        (-0.4, "at the line"),
        (-1, "1 yard behind the line"),
        (-4, "4 yards behind the line"),
        (-6.4, "6 yards behind the line"),
        (None, None),
    ],
)
def test_air_text(n, want):
    assert D.air_text(n) == want


# ---- what kind of play ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flags", "want"),
    [
        ({}, "run"),
        ({"dropback": True}, "dropback"),
        ({"dropback": True, "pass_attempt": True}, "pass"),
        ({"dropback": True, "scramble": True}, "scramble"),
        ({"dropback": True, "sack": True}, "sack"),
        ({"aborted": True}, "aborted"),
        # a fumbled snap the QB picked up and played on is that play
        ({"aborted": True, "pass_attempt": True}, "pass"),
        ({"aborted": True, "sack": True}, "sack"),
        ({"aborted": True, "scramble": True}, "scramble"),
        # priority: a sack ends the play before anything else; a QB who scrambled and then
        # threw passed (a 2016 row has both flags)
        ({"sack": True, "pass_attempt": True}, "sack"),
        ({"sack": True, "scramble": True}, "sack"),
        ({"scramble": True, "pass_attempt": True}, "pass"),
        ({"pass_attempt": False, "sack": False, "scramble": False, "aborted": False}, "run"),
    ],
)
def test_play_kind(flags, want):
    assert D.play_kind(flags) == want


# ---- fumbles and who scored ----------------------------------------------------------------------

LOST = "(6:20) ... to KC 29 for 4 yards. FUMBLES (58-J.Doe), RECOVERED by DEN-58-J.Doe at KC 27."


@pytest.mark.parametrize(
    ("desc", "offense", "want"),
    [
        (LOST, "KC", True),
        (LOST.replace("DEN", "KC"), "KC", False),  # the offense recovered its own fumble
        ("(6:20) ... FUMBLES, and recovers at KC 25.", "KC", False),
        ("(6:20) ... FUMBLES (58-J.Doe), recovered by DEN-58-J.Doe at KC 27.", "KC", True),
        (
            "(6:20) Aborted. 66-N.Martin FUMBLES at KC 21, recovered by KC-15-P.Mahomes.",
            "KC",
            False,
        ),
        # the last recovery decides
        ("FUMBLES ... RECOVERED by DEN-1-A. FUMBLES ... RECOVERED by KC-2-B.", "KC", False),
        ("FUMBLES ... RECOVERED by KC-1-A. FUMBLES ... RECOVERED by DEN-2-B.", "KC", True),
        # a recovery with no fumble in the text (a blocked kick, a muff) isn't a lost fumble
        ("Punt blocked. RECOVERED by DEN-5-C.", "KC", False),
        ("FUMBLES (58-J.Doe).", "KC", False),
        (None, "KC", False),
        ("", "KC", False),
        # team codes compare canonically: a 2019 text says OAK where the row says LV
        ("FUMBLES (9-X), RECOVERED by OAK-4-Y at LV 20.", "LV", False),
        ("FUMBLES (9-X), RECOVERED by LV-4-Y at LV 20.", "OAK", False),
        ("FUMBLES (9-X), RECOVERED by SD-4-Y at LAC 20.", "LAC", False),
        ("FUMBLES (9-X), RECOVERED by STL-4-Y at LA 20.", "LA", False),
        ("FUMBLES (9-X), RECOVERED by OAK-4-Y at KC 20.", "KC", True),
    ],
)
def test_fumble_lost_reads_the_last_recovery_against_the_offense(desc, offense, want):
    assert D.fumble_lost({"desc": desc, "offense": offense}) is want


def test_offense_touchdown_is_a_touchdown_only_when_the_offense_kept_the_ball():
    td = {"touchdown": True, "offense": "KC", "desc": "pass short right for 20 yards, TOUCHDOWN."}
    assert D.offense_touchdown(td) is True
    assert D.offense_touchdown({**td, "touchdown": False}) is False
    assert D.offense_touchdown({"offense": "KC", "desc": "x"}) is False
    # nflverse's `touchdown` is 1 for a defensive score too
    assert D.offense_touchdown({**td, "interception": True}) is False
    assert D.offense_touchdown({**td, "desc": LOST}) is False
    # a fumble the offense recovered and carried in is its touchdown
    assert D.offense_touchdown({**td, "desc": LOST.replace("DEN", "KC") + " TOUCHDOWN."}) is True


# ---- result_of -----------------------------------------------------------------------------------


def result(row):
    return D.result_of(row)


def test_result_of_a_completion_with_yards_after_the_catch():
    r = result(make_row())
    assert r == {
        "kind": "complete",
        "yards": 12.0,
        "first_down": True,
        "touchdown": False,
        "turnover": False,
        "fumble_lost": False,
        "text": "Complete, +12 (7 air yards), first down",
    }


@pytest.mark.parametrize(
    ("row", "kind", "text"),
    [
        (make_row(first_down=False), "complete", "Complete, +12 (7 air yards)"),
        (make_row(first_down=False, air_yards=None), "complete", "Complete, +12"),
        (
            make_row(first_down=False, yards_gained=0.0),
            "complete",
            "Complete, no gain (7 air yards)",
        ),
        (
            make_row(first_down=False, yards_gained=-2.0, air_yards=-5.0),
            "complete",
            f"Complete, {MINUS}2 (5 yards behind the line)",
        ),
        (
            make_row(first_down=False, yards_gained=None),
            "complete",
            "Complete, no gain recorded (7 air yards)",
        ),
        (make_row(complete=False, first_down=True), "incomplete", "Incomplete"),
        (
            make_row(complete=False, interception=True, first_down=False),
            "interception",
            "Intercepted",
        ),
        (run_row(), "run", "Run, +6"),
        (run_row(yards_gained=-3.0), "run", f"Run, {MINUS}3"),
        (run_row(yards_gained=0.0), "run", "Run, no gain"),
        (run_row(yards_gained=14.0, first_down=True), "run", "Run, +14, first down"),
        (run_row(yards_gained=3.0, first_down=True, touchdown=True), "run", "Run, +3, touchdown"),
        (sack_row(), "sack", f"Sacked, {MINUS}7"),
        (scramble_row(), "scramble", "Scramble, +9"),
        (scramble_row(yards_gained=14.0, first_down=True), "scramble", "Scramble, +14, first down"),
        (aborted_row(), "aborted", "Aborted snap"),
        (
            make_row(pass_attempt=False, first_down=False, yards_gained=0.0),
            "dropback",
            "Dropback, no gain",
        ),
    ],
)
def test_result_of_texts(row, kind, text):
    r = result(row)
    assert (r["kind"], r["text"]) == (kind, text)


def test_a_touchdown_text_replaces_first_down():
    r = result(td_pass())
    assert r["text"] == "Complete, +20 (17 air yards), touchdown"
    assert r["touchdown"] is True and r["first_down"] is True and r["turnover"] is False


def test_a_sack_and_an_incompletion_never_say_first_down():
    assert "first down" not in result(sack_row(first_down=True))["text"]
    assert "first down" not in result(make_row(complete=False, first_down=True))["text"]


def test_a_lost_fumble_is_a_turnover_and_replaces_the_touchdown_and_first_down_text():
    r = result(fumble_row("DEN", first_down=True))
    assert r["text"] == "Complete, +4 (3 air yards), fumble lost"
    assert r["fumble_lost"] is True and r["turnover"] is True and r["touchdown"] is False
    keep = result(fumble_row("KC"))
    assert keep["fumble_lost"] is False and keep["turnover"] is False
    assert keep["text"] == "Complete, +4 (3 air yards)"


def test_an_interception_is_a_turnover():
    r = result(make_row(complete=False, interception=True))
    assert r["turnover"] is True and r["fumble_lost"] is False


def test_a_pick_six_is_not_the_offenses_touchdown():
    """nflverse flags the defensive score `touchdown` too (6 plays in 2026, 32 in 2025)."""
    row = make_row(
        interception=True,
        complete=False,
        touchdown=True,
        yards_gained=0.0,
        first_down=False,
        desc="(14:16) (Shotgun) 13-C.Rush pass short right intended for 7-Bi.Robinson "
        "INTERCEPTED by 90-T.Watt at ATL 35. 90-T.Watt for 35 yards, TOUCHDOWN.",
    )
    r = result(row)
    assert r["text"] == "Intercepted" and r["touchdown"] is False and r["turnover"] is True


def test_a_fumble_returned_for_a_touchdown_is_not_the_offenses_touchdown():
    row = fumble_row(
        "DEN",
        touchdown=True,
        yards_to_goal=36,
        desc="(2:00) (Shotgun) 6-J.Browning pass short right to 86-N.Fant to MIN 32 for 4 yards. "
        "FUMBLES (2-I.Rodgers), RECOVERED by DEN-2-I.Rodgers at DEN 34. 2-I.Rodgers for 66 "
        "yards, TOUCHDOWN.",
    )
    r = result(row)
    assert r["touchdown"] is False and r["fumble_lost"] is True
    assert r["text"].endswith("fumble lost")
    d = D.diagram(row)
    check_diagram(d, row)
    # nobody on offense is drawn into the end zone
    assert all(k[1] < 100 for p in players(d, side="offense") for k in p["track"])
    assert all(b[1] < 100 for b in d["ball"])


def test_a_lost_fumble_on_a_run_returned_for_a_touchdown_is_not_drawn_scoring():
    row = run_row(
        yards_to_goal=70,
        yards_gained=22.0,
        run_gap="tackle",
        run_location="right",
        touchdown=True,
        desc="(7:05) 32-C.Carson right tackle to SEA 30 for 22 yards (25-E.Apple). FUMBLES "
        "(25-E.Apple), RECOVERED by DEN-24-V.Bell at SEA 33. 24-V.Bell for 33 yards, TOUCHDOWN.",
    )
    d = D.diagram(row)
    assert result(row)["touchdown"] is False
    carrier = next(p for p in d["players"] if p["carrier"])
    assert end_of(carrier)[1] == pytest.approx(52.0)  # LOS 30 + 22, not the end zone


# ---- tags and concepts


def test_tags_follow_a_fixed_order_and_only_true_flags():
    assert D.tags_of(make_row()) == []
    row = make_row(motion=True, deep_shot=True, play_action=True, screen=True, rpo=True)
    assert D.tags_of(row) == ["play_action", "screen", "rpo", "deep_shot", "motion"]
    row = make_row(no_huddle=True, trick_play=True)
    assert D.tags_of(row) == ["no_huddle", "trick_play"]
    assert D.tags_of(make_row(motion=False, play_action=None, screen=0)) == []
    assert D.tags_of(placeholder_ftn_row()) == []


def test_tags_read_the_fake_kick_and_the_direct_snap_from_the_text():
    fake = make_row(desc="(4:10) (Punt formation) 15-P.Mahomes pass short left to 87-T.Kelce.")
    assert D.tags_of(fake) == ["fake_kick"]
    fg = make_row(desc="(1:10) (Field Goal formation) 3-H.Butker rushes up the middle.")
    assert D.tags_of(fg) == ["fake_kick"]
    direct = make_row(desc="(9:10) Direct snap to 10-I.Pacheco. 10-I.Pacheco up the middle.")
    assert D.tags_of(direct) == ["direct_snap"]
    both = make_row(trick_play=True, desc="(Punt formation) Direct snap to 5-X, left end.")
    assert D.tags_of(both) == ["trick_play", "fake_kick", "direct_snap"]
    assert D.tags_of(make_row(desc="(8:00) punt formation was not used")) == []


def test_a_fake_punt_diagram_says_what_it_is_and_what_it_is_not():
    row = make_row(
        desc="(4:10) (Punt formation) 15-P.Mahomes pass short left to 87-T.Kelce for 12 yards."
    )
    d = D.diagram(row)
    check_diagram(d, row)
    assert "A fake from a punt or field-goal formation (play-by-play)" in d["known"]
    assert "The kicking formation is drawn as a standard offense" in d["approximate"]
    assert "fake_kick" in d["play"]["tags"]


def test_a_direct_snap_is_drawn_as_a_snap_to_the_qb_and_says_so():
    row = run_row(
        desc="(9:10) Direct snap to 10-I.Pacheco. 10-I.Pacheco up the middle for 6 yards."
    )
    d = D.diagram(row)
    check_diagram(d, row)
    assert "A direct snap to another player, drawn as a snap to the QB" in d["approximate"]
    assert "direct_snap" in d["play"]["tags"]


@pytest.mark.parametrize(
    ("row", "want"),
    [
        (make_row(), None),
        (make_row(screen=True), "screen"),
        (make_row(rpo=True), "rpo"),
        (make_row(play_action=True, deep_shot=True), "play_action_deep"),
        (make_row(play_action=True, deep_shot=False), None),
        (make_row(play_action=False, deep_shot=True), None),
        # priority: screen > rpo > play-action deep > runs > coverage
        (
            make_row(screen=True, rpo=True, play_action=True, deep_shot=True, coverage="cover_3"),
            "screen",
        ),
        (make_row(rpo=True, play_action=True, deep_shot=True, coverage="cover_1"), "rpo"),
        (make_row(play_action=True, deep_shot=True, coverage="cover_1"), "play_action_deep"),
        (run_row(screen=True), "screen"),
        (run_row(rpo=True, run_location="middle"), "rpo"),
        # runs: the middle and the guards are inside, tackles and ends outside
        (run_row(run_location="middle", run_gap=None), "inside_zone"),
        (run_row(run_location="left", run_gap="guard"), "inside_zone"),
        (run_row(run_location="right", run_gap="guard"), "inside_zone"),
        (run_row(run_location="left", run_gap="tackle"), "outside_zone"),
        (run_row(run_location="right", run_gap="end"), "outside_zone"),
        (run_row(run_location="left", run_gap=None), None),
        (run_row(run_location=None, run_gap=None), None),
        (run_row(run_location="left", run_gap="guard", coverage="cover_3"), "inside_zone"),
        (run_row(run_location="left", run_gap=None, coverage="cover_3"), None),
        # coverage concepts need a dropback
        (make_row(coverage="cover_1"), "cover_1"),
        (make_row(coverage="cover_3"), "cover_3"),
        (make_row(coverage="2_man"), "2_man"),
        (make_row(coverage="cover_2"), None),
        (make_row(coverage="cover_4"), None),
        (sack_row(coverage="cover_3"), "cover_3"),
        (scramble_row(coverage="cover_1"), "cover_1"),
        (make_row(pass_attempt=False, coverage="2_man"), "2_man"),
        (aborted_row(coverage="cover_3"), None),
    ],
)
def test_concept_of_priority(row, want):
    assert D.concept_of(row) == want


# ---- summary (the play browser's row)

SUMMARY_KEYS = {
    "game_id", "play_id", "week", "offense", "defense", "quarter", "quarter_text", "clock", "down",
    "distance", "down_text", "yards_to_goal", "spot", "type", "tags", "result", "epa", "explosive",
    "charted", "concept", "desc",
}  # fmt: skip


def test_summary_of_the_default_play():
    s = D.summary(make_row())
    assert set(s) == SUMMARY_KEYS
    assert s["game_id"] == "2026_04_KC_DEN" and s["play_id"] == 1234 and s["week"] == 4
    assert (s["offense"], s["defense"]) == ("KC", "DEN")
    assert (s["quarter"], s["quarter_text"], s["clock"]) == (2, "Q2", "10:15")
    assert (s["down"], s["distance"], s["down_text"]) == (1, 10, "1st & 10")
    assert (s["yards_to_goal"], s["spot"]) == (75, "KC 25")
    assert s["type"] == "pass" and s["tags"] == [] and s["concept"] is None
    assert s["result"]["text"] == "Complete, +12 (7 air yards), first down"
    assert s["epa"] == 0.51 and s["explosive"] is False and s["charted"] is True
    assert s["desc"].startswith("(Shotgun) 15-P.Mahomes pass short middle")


def test_summary_of_a_run_a_sack_and_an_uncharted_play():
    run = D.summary(run_row())
    assert run["type"] == "run" and run["concept"] == "inside_zone"
    assert run["desc"] == "10-I.Pacheco left guard to KC 31 for 6 yards (95-C.Jones)."
    assert D.summary(sack_row())["type"] == "sack"
    assert D.summary(placeholder_ftn_row())["charted"] is False
    thin = D.summary(
        make_row(epa=None, quarter=None, half_seconds=None, down=None, distance=None, desc=None)
    )
    assert (thin["epa"], thin["clock"], thin["quarter_text"], thin["down_text"], thin["desc"]) == (
        None,
        None,
        None,
        None,
        None,
    )


def test_summary_goal_to_go_and_overtime():
    s = D.summary(td_run(quarter=5, half_seconds=300))
    assert s["down_text"] == "1st & Goal" and s["spot"] == "DEN 3"
    assert (s["quarter_text"], s["clock"]) == ("OT", "5:00")
    assert s["result"]["text"] == "Run, +3, touchdown"


def test_summary_survives_a_row_with_almost_nothing():
    s = D.summary({})
    assert s["type"] == "run" and s["tags"] == [] and s["result"]["kind"] == "run"
    assert s["result"]["text"] == "Run, no gain recorded"
    assert s["epa"] is None and s["spot"] is None and s["play_id"] is None


# ---- the six hand-worked plays
# KC on its own 25 (yards_to_goal 75), ball on the middle hash (26.67), shotgun, one back, six in
# the box, four rushers. The line of scrimmage is x = 25; the shotgun QB stands at 25 - 5 = 20.


def test_worked_pass_complete_with_yards_after_the_catch():
    """Short right, 7 air yards, +12: the throw lands at (32, 42.83) (the right third), the
    receiver runs on to 25 + 12 = 37."""
    row = make_row(pass_location="right")
    d = D.diagram(row)
    check_diagram(d, row)
    f = d["field"]
    assert (f["los"], f["first_down"], f["goal"], f["hash"]) == (25.0, 35.0, 100.0, "middle")
    assert f["ball_y"] == pytest.approx(26.67, abs=0.005) and f["width"] == 53.33
    # before the snap
    qb = player(d, "o_qb")
    at(start_of(qb), 25.0 - D.QB_DEPTH["shotgun"], 26.67)
    for k, y in enumerate([24.07, 25.37, 26.67, 27.97, 29.27]):
        ol = players(d, "ol")[k]
        at(start_of(ol), 24.4, y)
    assert [p["label"] for p in players(d, "ol")] == ["LT", "LG", "C", "RG", "RT"]
    back = player(d, "o_rb1")
    at(start_of(back), 25.0 - 5.0, 26.67 - 2.5)  # shotgun: 5 deep, 2.5 to the boundary side
    spots = {p["id"]: start_of(p) for p in players(d, "receiver")}
    at(spots["o_r1"], 24.5, W - D.WIDE_Y)
    at(spots["o_r2"], 24.5, D.WIDE_Y)
    at(spots["o_r3"], 23.8, W - D.SLOT_Y)
    at(spots["o_r4"], 23.8, D.SLOT_Y)
    assert len(players(d, "dl")) == 4 and len(players(d, "lb")) == 2
    assert len(players(d, "db")) == 5
    assert [p["id"] for p in players(d, "db")] == ["d_cb1", "d_cb2", "d_s1", "d_s2", "d_db1"]
    # the ball: snap at the line, to the QB at t = 1.0, the drop to his set spot at t = 1.7
    drop_x = 25.0 - D.DROP_DEPTH["shotgun"]
    assert d["ball"][0] == [0.0, 25.0, pytest.approx(26.67, abs=0.005), 0.0]
    assert d["ball"][1][:2] == [0.8, 25.0]
    at(d["ball"][2], 25.0 - D.QB_DEPTH["shotgun"], 26.67)
    assert d["ball"][2][0] == 1.0
    assert near(qb["track"][2][1], drop_x) and qb["track"][2][0] == 1.7
    # the throw leaves at 1.95 and the catch is at (32, 42.83)
    throw = phase(d, "throw")
    assert throw["t"] == 1.95 and throw["text"] == "Throw: 7 air yards, right"
    catch = phase(d, "catch")
    assert catch["text"] == "Caught at the KC 32"
    assert near(catch["t"], 2.79, 0.03)  # 1.95 + 0.35 + 21.9 / 45
    ball_at_catch = next(b for b in d["ball"] if near(b[0], catch["t"], 0.006))
    at(ball_at_catch, 32.0, 42.83)
    assert near(ball_at_catch[3], 1.0)  # back to hand height
    # the arc peaks mid-flight
    flight = [b for b in d["ball"] if throw["t"] <= b[0] <= catch["t"]]
    assert max(b[3] for b in flight) == pytest.approx(flight[len(flight) // 2][3])
    # the target is the receiver nearest that third (o_r1, wide right), and carries it to 37
    target = next(p for p in d["players"] if p["target"])
    assert target["id"] == "o_r1" and target["carrier"] is True
    at(end_of(target), 37.0, 37.98)  # 30% of the way to the middle after the catch
    at(d["ball"][-1], 37.0, 37.98)
    assert end_of(target)[0] == d["ball"][-1][0] == phase(d, "end")["t"]
    # the phases
    assert phase_names(d) == ["presnap", "snap", "throw", "catch", "end"]
    assert [p["t"] for p in d["phases"]][:3] == [0.0, 0.8, 1.95]
    assert phase(d, "end")["text"] == "Complete, +12 (7 air yards), first down"
    assert near(phase(d, "end")["t"], 3.34, 0.03) and near(d["duration"], 3.94, 0.03)
    assert d["phases"][0]["text"] == (
        "Before the snap: QB shotgun, 1 back, 6 in the box, ball on the middle hash"
    )
    # the marks
    throw_mark = marks(d, "throw")[0]
    assert throw_mark["approx"] is False and throw_mark["t"] == 1.95
    pt(throw_mark["points"][0], drop_x, 26.67)
    pt(throw_mark["points"][1], 32.0, 42.83)
    run_mark = marks(d, "run")[0]
    assert run_mark["approx"] is True
    pt(run_mark["points"][1], 37.0, 37.98)
    end_mark = marks(d, "end")[0]
    assert end_mark["text"] == phase(d, "end")["text"] and end_mark["approx"] is False
    # four rushers, all on the line
    assert [p["id"] for p in d["players"] if p["rusher"]] == ["d_dl1", "d_dl2", "d_dl3", "d_dl4"]
    # the facts
    assert "The throw: 7 air yards, short right (play-by-play)" in d["known"]
    assert "The gain: +12 (play-by-play)" in d["known"]
    assert "4 pass rushers (FTN)" in d["known"]
    assert "The catch spot within its third of the field" in d["approximate"]
    assert "The run after the catch (straight to where the play ended)" in d["approximate"]
    assert d["concept"] is None and d["play"]["concept"] is None


def test_worked_run_left_guard():
    """Under center, one back, left guard for 6: the lane is the ball's y minus 1.3 at the
    line + 0.5; the back takes the ball at the mesh and ends at 25 + 6 = 31."""
    row = run_row()
    d = D.diagram(row)
    check_diagram(d, row)
    qb = player(d, "o_qb")
    at(start_of(qb), 25.0 - D.QB_DEPTH["under_center"], 26.67)  # under center
    back = player(d, "o_rb1")
    at(start_of(back), 25.0 - 7.5, 26.67)  # the single back 7.5 yards deep (2023 tracking)
    assert back["carrier"] is True and not qb["carrier"]
    lane = [k for k in back["track"] if near(k[1], 25.5)][0]
    at(lane, 25.5, 25.37)
    assert near(lane[0], 1.85)
    at(end_of(back), 31.0, 25.69)
    assert near(end_of(back)[0], 2.46)
    at(d["ball"][-1], 31.0, 25.69)
    # the handoff mesh is behind the line, then the lane, then the end
    assert phase_names(d) == ["presnap", "snap", "handoff", "end"]
    assert phase(d, "handoff")["t"] == 1.4 and phase(d, "end")["text"] == "Run, +6"
    handoff = marks(d, "handoff")[0]
    assert handoff["approx"] is True
    pt(handoff["points"][0], 22.0, 26.07)
    lane_mark = marks(d, "lane")[0]
    assert lane_mark["approx"] is False and lane_mark["text"] == "Run left guard"
    pt(lane_mark["points"][0], 23.5, 25.37)
    pt(lane_mark["points"][1], 25.5, 25.37)
    assert marks(d, "run")[0]["approx"] is True
    assert "The run's lane: left guard (play-by-play)" in d["known"]
    assert "The gain: +6 (play-by-play)" in d["known"]
    assert (
        "The ball carrier's path after the line (straight to where the play ended)"
        in d["approximate"]
    )
    assert d["concept"] == "inside_zone"
    assert d["phases"][0]["text"] == (
        "Before the snap: QB under center, 1 back, 7 in the box, ball on the middle hash"
    )
    # linemen fire out one yard
    for ol in players(d, "ol"):
        assert near(end_of(ol)[1], 25.4)
    # no one rushes on a run
    assert not any(p["rusher"] for p in d["players"])
    assert not any("pass rushers" in k for k in d["known"])
    assert "How many rushed the passer" not in d["unknown"]


def test_worked_screen_behind_the_line():
    """A screen left, 4 yards behind the line, +11: caught at (21, 17.67) (the flat on the
    left), then 25 + 11 = 36."""
    row = screen_row()
    d = D.diagram(row)
    check_diagram(d, row)
    # a screen isn't a quick throw: the QB takes the full shotgun drop
    qb = player(d, "o_qb")
    assert near(qb["track"][2][1], 25.0 - D.DROP_DEPTH["shotgun"])
    throw = phase(d, "throw")
    assert throw["text"] == "Throw: 4 yards behind the line, left"
    catch = phase(d, "catch")
    assert catch["text"] == "Caught at the KC 21"
    ball_at_catch = next(b for b in d["ball"] if near(b[0], catch["t"], 0.006))
    at(ball_at_catch, 21.0, 17.67)
    target = next(p for p in d["players"] if p["target"])
    assert target["id"] == "o_r4"  # the slot receiver nearest the left flat
    at(end_of(target), 36.0, 20.37)
    assert end_of(target)[0] == phase(d, "end")["t"]
    assert phase(d, "end")["text"] == "Complete, +11 (4 yards behind the line), first down"
    assert "The throw: 4 yards behind the line, short left (play-by-play)" in d["known"]
    assert d["concept"] == "screen" and "screen" in d["play"]["tags"]
    assert phase_names(d) == ["presnap", "snap", "throw", "catch", "end"]


def test_worked_play_action_deep_shot():
    """Under center, two backs, ball on the left hash at the KC 40: a fake to the back, motion,
    and a 28-yard throw to the right third that lands at (68, 42.83), then +3 to 71."""
    row = deep_shot_row()
    d = D.diagram(row)
    check_diagram(d, row)
    f = d["field"]
    assert (f["los"], f["first_down"], f["hash"]) == (40.0, 50.0, "left")
    assert f["ball_y"] == 23.58
    qb = player(d, "o_qb")
    qb_x = 40.0 - D.QB_DEPTH["under_center"]
    drop_x = 40.0 - D.DROP_DEPTH["under_center"]
    at(start_of(qb), qb_x, 23.58)
    rb1, rb2 = player(d, "o_rb1"), player(d, "o_rb2")
    at(start_of(rb1), 40.0 - 4.3, 23.58 + 2.1)  # an offset back, 4.3 deep, to the wide side
    at(start_of(rb2), 40.0 - 7.5, 23.58)  # the deeper back, behind the QB
    assert phase_names(d) == ["presnap", "motion", "snap", "fake", "throw", "catch", "end"]
    assert [phase(d, n)["t"] for n in ("presnap", "motion", "snap", "fake", "throw")] == [
        0.0, 0.15, 0.8, 1.0, 2.4,
    ]  # fmt: skip
    # the fake: the QB rides to the mesh behind the line and the deepest back carries it out
    fake = marks(d, "fake")[0]
    assert fake["approx"] is True and fake["text"] == "Play-action fake"
    pt(fake["points"][0], qb_x, 23.58)
    pt(fake["points"][1], 36.5, 24.38)
    mesh_qb = next(k for k in qb["track"] if near(k[0], 1.45))
    at(mesh_qb, 36.5, 24.38)
    mesh_back = next(k for k in rb2["track"] if near(k[0], 1.45))
    at(mesh_back, 36.8, 25.28)
    at(end_of(rb2), 40.5, 25.88)  # into the line after the fake
    assert len(rb1["track"]) == 1  # the other back doesn't move
    # then the drop, set at 2.15 s; the ball goes at 2.4
    drop = next(k for k in qb["track"] if near(k[0], 2.15))
    at(drop, drop_x, 23.58)
    # the motion man is the receiver nearest the ball: 6 yards towards it, between 0.15 and 0.7
    motion = marks(d, "motion")[0]
    assert motion["approx"] is True and motion["t"] == 0.15
    by = f["ball_y"]
    recs = {p["id"]: start_of(p) for p in players(d, "receiver")}
    nearest = min(recs, key=lambda i: abs(recs[i][2] - by))  # the receiver nearest the ball
    x0, y0 = recs[nearest][1], recs[nearest][2]
    to_y = y0 + (6.0 if y0 < by else -6.0)
    pt(motion["points"][0], x0, y0)
    pt(motion["points"][1], x0, to_y)
    mover = next(
        p for p in players(d, "receiver") if len(p["track"]) >= 3 and p["track"][1][0] == 0.15
    )
    assert mover["id"] == nearest
    at(mover["track"][-1], x0, to_y)
    # the throw: 28 air yards to the right third
    assert phase(d, "throw")["text"] == "Throw: 28 air yards, right"
    assert phase(d, "catch")["text"] == "Caught at the DEN 32"
    thr = marks(d, "throw")[0]
    pt(thr["points"][0], drop_x, 23.58)
    pt(thr["points"][1], 68.0, 42.83)
    target = next(p for p in d["players"] if p["target"])
    assert target["id"] == "o_r1"
    at(end_of(target), 71.0, 42.83)  # only 3 yards after the catch: no drift to the middle
    assert phase(d, "end")["text"] == "Complete, +31 (28 air yards), first down"
    assert d["concept"] == "play_action_deep"
    assert d["play"]["tags"] == ["play_action", "deep_shot", "motion"]
    assert d["phases"][0]["text"] == (
        "Before the snap: QB under center, 2 backs, 7 in the box, ball on the left hash"
    )
    assert "Motion before the snap (FTN)" in d["known"]
    assert "Who went in motion, and where" in d["approximate"]


def test_worked_sack():
    """A sack for 7: the QB ends at 25 - 7 = 18 on the ball's y; five rushers (the four down
    linemen and the linebacker nearest the ball) get there 2.0 yards short of him."""
    row = sack_row()
    d = D.diagram(row)
    check_diagram(d, row)
    qb = player(d, "o_qb")
    t_sack = phase(d, "end")["t"]
    assert near(t_sack, 2.3)
    at(end_of(qb), 18.0, 26.67)
    assert end_of(qb)[0] == t_sack
    sack = marks(d, "sack")[0]
    assert sack["approx"] is False and sack["text"] == f"Sacked, {MINUS}7"
    pt(sack["points"][0], 18.0, 26.67)
    at(d["ball"][-1], 18.0, 26.67)
    assert phase_names(d) == ["presnap", "snap", "end"]
    assert phase(d, "end")["text"] == f"Sacked, {MINUS}7"
    rushers = [p["id"] for p in d["players"] if p["rusher"]]
    assert rushers == ["d_dl1", "d_dl2", "d_dl3", "d_dl4", "d_lb1"]
    for p in d["players"]:
        if p["rusher"]:
            x, y = end_of(p)[1], end_of(p)[2]
            assert math.hypot(x - 18.0, y - 26.67) == pytest.approx(2.0, abs=0.03), p["id"]
            assert end_of(p)[0] == t_sack
    assert "5 pass rushers (FTN)" in d["known"]
    assert f"The gain: {MINUS}7 (play-by-play)" in d["known"]
    # no throw: no pass-catchers move
    assert not any(p["target"] or p["carrier"] for p in d["players"])
    assert not marks(d, "throw") and not marks(d, "run")
    assert d["concept"] is None


def test_worked_scramble_left():
    """A scramble left for 9: the QB runs to the line at the ball's y - 6 (left of the
    middle) and on to 25 + 9 = 34."""
    row = scramble_row()
    d = D.diagram(row)
    check_diagram(d, row)
    qb = player(d, "o_qb")
    assert qb["carrier"] is True
    at(end_of(qb), 34.0, 20.67)
    line = next(k for k in qb["track"] if near(k[1], 25.5))
    at(line, 25.5, 20.67)
    assert phase_names(d) == ["presnap", "snap", "scramble", "end"]
    assert phase(d, "scramble")["text"] == "The QB scrambles left"
    assert phase(d, "end")["text"] == "Scramble, +9"
    assert end_of(qb)[0] == phase(d, "end")["t"]
    run = marks(d, "run")[0]
    assert run["approx"] is True and len(run["points"]) == 3
    pt(run["points"][-1], 34.0, 20.67)
    at(d["ball"][-1], 34.0, 20.67)
    assert "A scramble to the left (play-by-play)" in d["known"]
    assert "The scramble's path" in d["approximate"]
    assert [p["id"] for p in d["players"] if p["rusher"]] == ["d_dl1", "d_dl2", "d_dl3", "d_dl4"]
    # the rush reaches where the QB set up, before he breaks
    set_up = next(k for k in qb["track"] if near(k[1], 25.0 - D.DROP_DEPTH["shotgun"]))
    for p in d["players"]:
        if p["rusher"]:
            assert math.hypot(end_of(p)[1] - set_up[1], end_of(p)[2] - set_up[2]) == pytest.approx(
                2.0, abs=0.03
            )
    assert d["play"]["type"] == "scramble"


# ---- touchdowns end in the end zone


def test_touchdown_pass_ends_past_the_goal_line():
    row = td_pass()
    d = D.diagram(row)
    check_diagram(d, row)
    f = d["field"]
    assert f["los"] == 80.0 and f["first_down"] == 90.0
    target = next(p for p in d["players"] if p["target"])
    assert end_of(target)[1] == 100.5 and d["ball"][-1][1] == 100.5
    assert phase(d, "catch")["text"] == "Caught at the DEN 3"
    assert phase(d, "end")["text"] == "Complete, +20 (17 air yards), touchdown"
    assert f["view"][1] <= 110.0


def test_touchdown_run_ends_past_the_goal_line_and_there_is_no_first_down_line():
    row = td_run()
    d = D.diagram(row)
    check_diagram(d, row)
    assert d["field"]["los"] == 97.0 and d["field"]["first_down"] is None
    carrier = next(p for p in d["players"] if p["carrier"])
    assert end_of(carrier)[1] == 100.5 and d["ball"][-1][1] == 100.5
    assert phase(d, "end")["text"] == "Run, +3, touchdown"
    assert "Run up the middle" in [m["text"] for m in d["marks"]]


def test_a_touchdown_run_that_gained_more_than_the_line_still_ends_in_the_end_zone():
    d = D.diagram(td_run(yards_gained=3.0, yards_to_goal=3, distance=3))
    carrier = next(p for p in d["players"] if p["carrier"])
    assert end_of(carrier)[1] == 100.5
    # a gain that stops short is not pushed unless the play was a touchdown
    d = D.diagram(td_run(touchdown=False, yards_gained=2.0))
    carrier = next(p for p in d["players"] if p["carrier"])
    assert end_of(carrier)[1] == 99.0


# ---- honesty, in words


def test_a_2026_play_names_what_it_does_not_have_with_the_february_2027_note():
    d = D.diagram(make_row())
    note = [u for u in d["unknown"] if u.startswith("Personnel and the coverage shell")]
    assert note == [
        "Personnel and the coverage shell (this season's participation data is published "
        "after the season, about February 2027)"
    ]
    assert d["sources"] == ["nflverse play-by-play", "FTN charting (via nflverse)"]


def test_a_2025_play_without_participation_names_the_gap_without_the_february_note():
    d = D.diagram(make_row(season=2025, game_id="2025_06_KC_DEN"))
    assert "Personnel and the coverage shell" in d["unknown"]
    assert not any("February" in u for u in d["unknown"])


def test_a_play_with_participation_does_not_call_personnel_unknown():
    for row in (row_2025(), row_2019()):
        d = D.diagram(row)
        assert not any(u.startswith("Personnel and the coverage") for u in d["unknown"])
        assert "nflverse participation (research data)" in d["sources"]
        assert any("participation, research data" in k for k in d["known"])


def test_a_pre_2022_play_says_motion_is_not_charted_and_never_claims_it():
    d = D.diagram(row_2019())
    assert "Motion before the snap (not charted before 2022)" in d["unknown"]
    assert not any("otion" in k for k in d["known"])
    assert not marks(d, "motion") and "motion" not in phase_names(d)


def test_a_2022_to_2025_play_without_ftn_says_nothing_about_motion():
    d = D.diagram(make_row(season=2024, **{"ftn_charted": False, "motion": None}))
    assert not any("otion" in s for s in d["known"] + d["unknown"] + d["approximate"])


def test_an_ftn_play_says_motion_or_no_motion_as_known():
    assert "No motion before the snap (FTN)" in D.diagram(make_row(motion=False))["known"]
    assert "Motion before the snap (FTN)" in D.diagram(make_row(motion=True))["known"]


def test_the_unknowns_are_always_listed_whatever_the_play():
    for row in (
        make_row(), run_row(), sack_row(), scramble_row(), aborted_row(), row_2019(),
        row_2025(), td_run(), placeholder_ftn_row(),
    ):  # fmt: skip
        d = D.diagram(row)
        for u in ALWAYS_UNKNOWN:
            assert u in d["unknown"], (u, row["desc"])


def test_nothing_generic_is_ever_in_the_known_list():
    for row in (make_row(), run_row(), row_2019(), row_2025(), deep_shot_row()):
        d = D.diagram(row)
        text = " ".join(d["known"]).lower()
        for word in ("generic", "line up", "outside the box", "safeties", "assumed"):
            assert word not in text, (word, d["known"])


# ---- a play that lost yards never crosses the line (Sol, PC03) -----------------------------------


def after_snap(keys):
    return [k for k in keys if k[0] >= 0.8]


@pytest.mark.parametrize("yards", [-12.0, -8.0, -4.0, -3.0, -1.0, -0.4])
def test_a_run_for_a_loss_never_puts_the_carrier_or_the_ball_past_the_line(yards):
    row = run_row(yards_gained=yards, first_down=False)
    d = D.diagram(row)
    check_diagram(d, row)
    carrier = next(p for p in d["players"] if p["carrier"])
    # every key from the snap on is behind (or on) the line of scrimmage, the end included
    assert max(k[1] for k in after_snap(carrier["track"])) <= 25.0 + 1e-9
    assert max(b[1] for b in after_snap(d["ball"])) <= 25.0 + 1e-9
    assert near(end_of(carrier)[1], 25.0 + yards)
    # the line point of the path is where the play ended; the lane mark stays at the line
    run = marks(d, "run")[0]
    assert run["points"][0][0] <= 25.0 + 1e-9 and near(run["points"][0][0], 25.0 + yards)
    lane = marks(d, "lane")[0]
    assert near(lane["points"][1][0], 25.5)


@pytest.mark.parametrize("yards", [-8.0, -3.0, -1.0])
def test_a_scramble_for_a_loss_never_puts_the_qb_or_the_ball_past_the_line(yards):
    row = scramble_row(yards_gained=yards, first_down=False)
    d = D.diagram(row)
    check_diagram(d, row)
    qb = player(d, "o_qb")
    assert max(k[1] for k in after_snap(qb["track"])) <= 25.0 + 1e-9
    assert max(b[1] for b in after_snap(d["ball"])) <= 25.0 + 1e-9
    assert near(end_of(qb)[1], 25.0 + yards)
    run = marks(d, "run")[0]
    assert run["points"][1][0] <= 25.0 + 1e-9 and near(run["points"][1][0], 25.0 + yards)


@pytest.mark.parametrize(
    ("yards", "line_x"),
    [(0.0, 25.0), (0.3, 25.3), (0.5, 25.5), (1.0, 25.5), (6.0, 25.5), (30.0, 25.5)],
)
def test_a_run_for_no_loss_reaches_the_line_point_and_a_gain_goes_through_it(yards, line_x):
    row = run_row(yards_gained=yards, first_down=False)
    d = D.diagram(row)
    carrier = next(p for p in d["players"] if p["carrier"])
    assert near(marks(d, "run")[0]["points"][0][0], line_x)
    assert any(near(k[1], line_x) for k in carrier["track"]), (yards, carrier["track"])
    sc = D.diagram(scramble_row(yards_gained=yards, first_down=False))
    assert near(marks(sc, "run")[0]["points"][1][0], line_x)


def test_a_qb_run_and_a_receivers_carry_for_a_loss_stay_behind_the_line_too():
    qb = D.diagram(run_row(yards_gained=-4.0, rusher_id="q1"), ["q1"])
    c = next(p for p in qb["players"] if p["carrier"])
    assert c["role"] == "qb" and max(k[1] for k in after_snap(c["track"])) <= 25.0 + 1e-9
    wr = D.diagram(run_row(yards_gained=-4.0, rusher_id="w1"), [], {"w1": "WR"})
    c = next(p for p in wr["players"] if p["carrier"])
    assert c["role"] == "receiver" and max(k[1] for k in after_snap(c["track"])) <= 25.0 + 1e-9


# ---- an aborted snap from a kicking formation is not a fake kick (Sol, PC03) ---------------------

KICK = "(4:10) (Punt formation) 15-P.Mahomes Aborted. 66-N.Martin FUMBLES at KC 20."
FAKE = "A fake from a punt or field-goal formation (play-by-play)"
FUMBLED_KICK = "From a punt or field-goal formation, with a fumbled snap (play-by-play)"
STANDARD = "The kicking formation is drawn as a standard offense"


@pytest.mark.parametrize("formation", ["Punt formation", "Field Goal formation", "punt FORMATION"])
def test_an_aborted_snap_from_a_kicking_formation_has_no_fake_kick_tag(formation):
    row = aborted_row(desc=f"(4:10) ({formation}) 15-P.Mahomes Aborted. 66-N.Martin FUMBLES.")
    assert "fake_kick" not in D.tags_of(row)
    d = D.diagram(row)
    check_diagram(d, row)
    assert "fake_kick" not in d["play"]["tags"]
    assert FUMBLED_KICK in d["known"] and FAKE not in d["known"]
    assert STANDARD in d["approximate"]
    assert d["play"]["type"] == "aborted"


def test_a_kick_formation_play_that_was_aborted_and_then_played_on_is_not_a_fake_either():
    for flags in (
        {"pass_attempt": True},
        {"sack": True, "pass_attempt": False},
        {"scramble": True},
    ):
        row = make_row(aborted=True, desc=KICK, **flags)
        assert "fake_kick" not in D.tags_of(row), flags
        d = D.diagram(row)
        check_diagram(d, row)
        assert FUMBLED_KICK in d["known"] and FAKE not in d["known"]
        assert "A fumbled snap, picked up by the QB (play-by-play)" in d["known"]
        assert STANDARD in d["approximate"]


def test_a_real_fake_kick_keeps_its_tag_and_its_own_lines():
    row = make_row(desc="(4:10) (Punt formation) 15-P.Mahomes pass short left to 87-T.Kelce.")
    assert "fake_kick" in D.tags_of(row)
    d = D.diagram(row)
    assert FAKE in d["known"] and FUMBLED_KICK not in d["known"] and STANDARD in d["approximate"]
    assert "fake_kick" in d["play"]["tags"]


def test_an_aborted_snap_with_no_kicking_formation_says_nothing_about_kicks():
    d = D.diagram(aborted_row())
    assert FUMBLED_KICK not in d["known"] and FAKE not in d["known"]
    assert STANDARD not in d["approximate"]


def test_the_summary_tags_of_an_aborted_kick_formation_play_leave_the_other_tags():
    row = aborted_row(desc=KICK, trick_play=True, no_huddle=True)
    assert D.tags_of(row) == ["no_huddle", "trick_play"]
    assert D.summary(row)["tags"] == ["no_huddle", "trick_play"]
