"""PC03: the finer geometry of the play diagrams, one rule at a time, plus the sweeps that run
`check_diagram` (11 v 11, sorted keys, in bounds, the honesty rules) over every shape of play.

The worked plays (a pass, a run, a screen, a play-action deep shot, a sack, a scramble) are in
`test_playcall_diagrams.py`. Here: the line of scrimmage and the first-down line, the hash, the QB
alignments, the linemen, the backfield and the receivers by count and personnel, the box and the
secondary by shell, the rushers, motion, the play-action fake, every run lane, a QB run, every
kind of throw, aborted snaps, what participation adds (2016-2025), the target's route shapes,
and the "generic spots are always approximate" rule.
"""

from __future__ import annotations

import copy
import itertools
import math
import random

import pytest
from playcall_diagram_fixtures import (
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
)

from nflengine.playcalling import diagrams as D

MIDDLE = 160 / 3 / 2
W = 160 / 3


def near(value: float, want: float, tol: float = 0.011) -> bool:
    return abs(value - want) <= tol


def at(p, x, y):
    assert near(p[1], x) and near(p[2], y), (p, x, y)


def pt(p, x, y):
    assert near(p[0], x) and near(p[1], y), (p, x, y)


def build(row, qb=()):
    d = D.diagram(row, qb)
    check_diagram(d, row)
    return d


# ---- the line of scrimmage and the first-down line ----------------------------------------------


@pytest.mark.parametrize(
    ("ytg", "dist", "goal_to_go", "los", "first_down"),
    [
        (75, 10, False, 25.0, 35.0),
        (50, 3, False, 50.0, 53.0),
        (99, 10, False, 1.0, 11.0),
        (8, 8, True, 92.0, None),  # goal to go: no first-down line
        (5, 5, True, 95.0, None),
        (8, 10, False, 92.0, 100.0),  # the line is the goal line at most
        (2, 2, False, 98.0, 100.0),
        (60, None, False, 40.0, None),
    ],
)
def test_the_line_of_scrimmage_is_100_minus_yards_to_goal(ytg, dist, goal_to_go, los, first_down):
    row = make_row(yards_to_goal=ytg, distance=dist, goal_to_go=goal_to_go)
    d = build(row)
    assert d["field"]["los"] == los and d["field"]["first_down"] == first_down
    assert d["field"]["goal"] == 100.0 and d["field"]["width"] == 53.33
    assert d["ball"][0][:2] == [0.0, los]
    for ol in players(d, "ol"):
        assert near(start_of(ol)[1], los - 0.6)


def test_without_a_yard_line_the_ball_is_at_midfield():
    d = D.diagram(make_row(yards_to_goal=None))
    assert d["field"]["los"] == 50.0
    assert d["play"]["spot"] is None


# ---- the hash ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hash_", "ball_y"), [("left", 23.58), ("middle", MIDDLE), ("right", W - 23.58)]
)
def test_the_ball_sits_on_the_charted_hash_and_the_line_is_centred_on_it(hash_, ball_y):
    d = build(make_row(hash=hash_))
    assert d["field"]["hash"] == hash_ and near(d["field"]["ball_y"], ball_y)
    assert f"The ball on the {hash_} hash (FTN)" in d["known"]
    assert near(d["ball"][0][2], ball_y)
    centre = player(d, "o_c")
    assert near(start_of(centre)[2], ball_y) and near(start_of(player(d, "o_qb"))[2], ball_y)
    assert not any("middle of the field" in s for s in d["approximate"])
    assert d["phases"][0]["text"].endswith(f"ball on the {hash_} hash")


@pytest.mark.parametrize(
    "row",
    [
        make_row(hash=None),
        make_row(hash="up"),
        make_row(hash="LEFT"),
        placeholder_ftn_row(hash="left"),  # a hash on a play FTN didn't chart is ignored
        row_2019(hash="left"),
    ],
)
def test_an_uncharted_hash_is_drawn_in_the_middle_and_says_so(row):
    d = build(row)
    assert d["field"]["hash"] is None and near(d["field"]["ball_y"], MIDDLE)
    assert "The ball drawn in the middle of the field (the hash isn't charted)" in d["approximate"]
    assert not any("hash (FTN)" in k for k in d["known"])
    assert "hash" not in d["phases"][0]["text"]


def test_the_sixth_lineman_and_the_back_go_to_the_wide_side_of_the_field():
    """Away from the hash the ball is on: left hash -> right side, right hash -> left side."""
    left = build(row_2025(extra_ol=True, personnel="12", hash="left", qb_alignment="shotgun"))
    right = build(row_2025(extra_ol=True, personnel="12", hash="right", qb_alignment="shotgun"))
    mid = build(row_2025(extra_ol=True, personnel="12", hash="middle", qb_alignment="shotgun"))
    for d, sign in ((left, 1), (right, -1), (mid, 1)):
        by = d["field"]["ball_y"]
        sixth = player(d, "o_ol")
        assert near(start_of(sixth)[2], by + sign * 3 * 1.3) and sixth["approx"] is True
        # the lone back stands to the boundary side
        back = player(d, "o_rb1")
        assert len(players(d, "back")) == 1 and near(start_of(back)[2], by - sign * 2.5)


# ---- the QB --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("align", "text"),
    [
        ("under_center", "QB under center (FTN)"),
        ("shotgun", "QB shotgun (FTN)"),
        ("pistol", "QB pistol (FTN)"),
    ],
)
def test_the_qb_stands_at_the_charted_depth(align, text):
    d = build(make_row(qb_alignment=align, shotgun=align != "under_center"))
    qb = player(d, "o_qb")
    at(start_of(qb), 25.0 - D.QB_DEPTH[align], MIDDLE)
    assert text in d["known"]
    assert d["phases"][0]["text"].startswith(f"Before the snap: QB {align.replace('_', ' ')}")
    # the hash is charted and so is his alignment: the QB is measured
    assert qb["approx"] is False


def test_the_qb_is_measured_only_where_the_hash_and_his_alignment_are_both_known():
    cases = [
        # (row, approximate?)
        (make_row(), False),  # FTN: hash and alignment
        (make_row(hash=None), True),  # alignment known, hash not charted
        (make_row(qb_alignment=None, shotgun=True), False),  # alignment from play-by-play
        (make_row(qb_alignment=None, shotgun=None), True),  # alignment unknown
        (placeholder_ftn_row(), True),  # no FTN: no hash
        (row_2019(), True),
    ]
    for row, approx in cases:
        d = build(row)
        assert player(d, "o_qb")["approx"] is approx, row["desc"]


def test_the_ftn_alignment_beats_the_play_by_play_shotgun_flag():
    d = build(make_row(qb_alignment="under_center", shotgun=True))
    at(start_of(player(d, "o_qb")), 25.0 - D.QB_DEPTH["under_center"], MIDDLE)
    d = build(make_row(qb_alignment="pistol", shotgun=True))
    at(start_of(player(d, "o_qb")), 25.0 - D.QB_DEPTH["pistol"], MIDDLE)
    assert "QB pistol (FTN)" in d["known"] and d["phases"][0]["text"].startswith(
        "Before the snap: QB pistol"
    )


def test_without_ftn_the_shotgun_flag_decides_and_says_it_is_play_by_play():
    d = build(placeholder_ftn_row(qb_alignment="under_center", shotgun=True))  # FTN ignored
    at(start_of(player(d, "o_qb")), 25.0 - D.QB_DEPTH["shotgun"], MIDDLE)
    assert "QB in the shotgun or pistol (play-by-play)" in d["known"]
    d = build(placeholder_ftn_row(shotgun=False))
    at(start_of(player(d, "o_qb")), 25.0 - D.QB_DEPTH["under_center"], MIDDLE)
    assert "QB under center (play-by-play)" in d["known"]


def test_an_unrecorded_alignment_is_under_center_and_approximate_and_stays_out_of_the_text():
    d = build(placeholder_ftn_row(shotgun=None))
    at(start_of(player(d, "o_qb")), 25.0 - D.QB_DEPTH["under_center"], MIDDLE)
    assert "QB drawn under center (the alignment isn't recorded)" in d["approximate"]
    assert not any(k.startswith("QB") for k in d["known"])
    assert d["phases"][0]["text"] == "Before the snap"
    # FTN charted but the alignment is missing (9 plays in 2026): the pbp flag takes over
    d = build(make_row(qb_alignment=None, shotgun=True))
    at(start_of(player(d, "o_qb")), 25.0 - D.QB_DEPTH["shotgun"], MIDDLE)


# ---- the line, the backfield, the receivers ------------------------------------------------------


def test_five_linemen_by_default_spaced_across_the_ball():
    d = build(make_row())
    ol = players(d, "ol")
    assert [p["id"] for p in ol] == ["o_lt", "o_lg", "o_c", "o_rg", "o_rt"]
    ys = [start_of(p)[2] for p in ol]
    assert all(near(b - a, 1.3, 0.02) for a, b in zip(ys, ys[1:], strict=False))
    # only the center, over a charted ball, is measured; the others are at standard splits
    assert [p["approx"] for p in ol] == [True, True, False, True, True]
    assert any(
        s.startswith("5 offensive linemen") and "this season's personnel data" in s
        for s in d["approximate"]
    )


def test_the_linemen_line_says_what_is_measured_and_what_is_not():
    standard = "The linemen beside the center, at standard 1.3-yard splits"
    d = build(make_row())  # the hash is charted
    assert standard in d["approximate"] and not any("in the middle" in s for s in d["approximate"])
    d = build(make_row(hash=None))  # the ball is drawn in the middle
    assert standard + "; the center and the QB with the ball, in the middle" in d["approximate"]
    ol = players(d, "ol")
    assert all(p["approx"] for p in ol) and player(d, "o_qb")["approx"] is True
    # exactly one such line per diagram
    assert sum(s.startswith("The linemen beside the center") for s in d["approximate"]) == 1


def note_5ol(s: str) -> bool:
    return s.startswith("5 offensive linemen") and "no personnel recorded for this play" in s


def test_the_linemen_note_depends_on_the_season():
    d = build(make_row(season=2025))
    assert any(note_5ol(s) for s in d["approximate"])
    d = build(row_2025(personnel=None))
    assert any(note_5ol(s) for s in d["approximate"])
    d = build(row_2025())  # personnel known: nothing assumed
    assert not any(s.startswith("5 offensive linemen") for s in d["approximate"])


def test_extra_ol_draws_a_sixth_lineman_and_one_receiver_fewer():
    row = row_2025(extra_ol=True, personnel="12", backfield=1, hash="middle")
    d = build(row)
    assert [p["id"] for p in players(d, "ol")] == [
        "o_lt", "o_lg", "o_c", "o_rg", "o_rt", "o_ol",
    ]  # fmt: skip
    assert [p["approx"] for p in players(d, "ol")] == [True, True, False, True, True, True]
    assert len(players(d, "back")) == 1 and len(players(d, "receiver")) == 3
    assert [p["label"] for p in players(d, "receiver")] == ["TE", "TE", "WR"]
    assert (
        "12 personnel: 1 back, 2 tight ends, 6 linemen (participation, research data)" in d["known"]
    )
    # the tight ends stand inline, outside the sixth lineman
    te = [start_of(p) for p in players(d, "receiver")[:2]]
    at(te[0], 24.4, MIDDLE + 2.5 * 1.3 + 1.3 + 1.3)
    at(te[1], 24.4, MIDDLE - (2.5 * 1.3 + 1.3 + 1.3))


@pytest.mark.parametrize("backfield", [0, 1, 2, 3, 4])
def test_the_backfield_count_decides_the_backs_and_the_receivers(backfield):
    d = build(make_row(backfield=backfield))
    assert len(players(d, "back")) == backfield
    assert len(players(d, "receiver")) == 11 - 5 - 1 - backfield
    assert f"{backfield} in the backfield besides the QB (FTN)" in d["known"]
    assert all(p["approx"] for p in players(d, "back"))
    assert [p["label"] for p in players(d, "receiver")] == ["R"] * (5 - backfield)


def spots(by, fs):
    """Where the backs stand, behind the line and across from the ball, by alignment (2023
    tracking, guide §10.3): `fs` is +1 when the field side is the right (ball on the left hash or
    in the middle), -1 when it is the left."""
    return {
        "shotgun": {
            1: [(5.0, by - 2.5 * fs)],
            2: [(5.0, by - 2.5), (5.0, by + 2.5)],
            3: [(5.0, by - 2.5), (5.0, by + 2.5), (2.7, by + 4.6 * fs)],
            4: [(5.0, by - 2.5), (5.0, by + 2.5), (2.7, by + 4.6 * fs), (7.5, by)],
        },
        "under_center": {
            1: [(7.5, by)],
            2: [(4.3, by + 2.1 * fs), (7.5, by)],
            3: [(4.3, by - 2.1), (4.3, by + 2.1), (7.5, by)],
            4: [(4.3, by - 2.1), (4.3, by + 2.1), (7.5, by), (7.5, by)],
        },
        "pistol": {
            1: [(7.5, by)],
            2: [(2.7, by + 4.0 * fs), (7.5, by)],
            3: [(2.7, by - 4.0), (2.7, by + 4.0), (7.5, by)],
            4: [(2.7, by - 4.0), (2.7, by + 4.0), (7.5, by), (7.5, by)],
        },
    }


@pytest.mark.parametrize("hash_", ["left", "middle", "right"])
@pytest.mark.parametrize("align", ["shotgun", "under_center", "pistol"])
def test_back_spots_by_alignment_count_and_field_side(align, hash_):
    for n in (1, 2, 3, 4):
        d = build(
            make_row(backfield=n, qb_alignment=align, shotgun=align != "under_center", hash=hash_)
        )
        by = d["field"]["ball_y"]
        fs = -1.0 if hash_ == "right" else 1.0  # the field side is away from the ball's hash
        want = spots(by, fs)[align][n]
        got = [start_of(p) for p in players(d, "back")]
        assert len(got) == n, (align, hash_, n)
        for g, (dx, y) in zip(got, want, strict=True):
            at(g, 25.0 - dx, y)


def test_a_lone_shotgun_back_stands_to_the_boundary_side():
    d = build(make_row(backfield=1, hash="left"))  # wide side right -> back to the left
    at(start_of(player(d, "o_rb1")), 25.0 - 5.0, 23.58 - 2.5)
    d = build(make_row(backfield=1, hash="right"))  # wide side left -> back to the right
    at(start_of(player(d, "o_rb1")), 25.0 - 5.0, (W - 23.58) + 2.5)
    d = build(make_row(backfield=1, hash="middle"))
    at(start_of(player(d, "o_rb1")), 25.0 - 5.0, MIDDLE - 2.5)


@pytest.mark.parametrize("backfield", [5, 7, -1])
def test_an_impossible_backfield_count_is_treated_as_not_charted(backfield):
    d = build(make_row(backfield=backfield))
    assert len(players(d, "back")) == 1
    assert "1 back drawn (the backfield isn't charted)" in d["approximate"]
    assert not any("in the backfield" in k for k in d["known"])
    assert "back" not in d["phases"][0]["text"]


def test_receivers_are_generic_and_never_known():
    d = build(make_row())
    rec = players(d, "receiver")
    assert len(rec) == 4 and all(p["approx"] and p["label"] == "R" for p in rec)
    xs = sorted(start_of(p)[1] for p in rec)
    assert xs == [23.8, 23.8, 24.5, 24.5]
    ys = sorted(start_of(p)[2] for p in rec)
    for got, want in zip(ys, [D.WIDE_Y, D.SLOT_Y, W - D.SLOT_Y, W - D.WIDE_Y], strict=True):
        assert near(got, want)
    assert any(
        s.startswith("Where the 4 receivers line up") and "aren't told apart" in s
        for s in d["approximate"]
    )


@pytest.mark.parametrize(
    ("personnel", "backs", "tes", "wrs"),
    [
        ("11", 1, 1, 3),
        ("12", 1, 2, 2),
        ("13", 1, 3, 1),
        ("10", 1, 0, 4),
        ("21", 2, 1, 2),
        ("22", 2, 2, 1),
        ("23", 2, 3, 0),
        ("20", 2, 0, 3),
        ("01", 0, 1, 4),
        ("02", 0, 2, 3),
        ("03", 0, 3, 2),
    ],
)
def test_personnel_gives_the_backs_tight_ends_and_wide_receivers_when_ftn_is_silent(
    personnel, backs, tes, wrs
):
    d = build(row_2019(personnel=personnel))
    assert len(players(d, "back")) == backs
    labels = [p["label"] for p in players(d, "receiver")]
    assert labels == ["TE"] * tes + ["WR"] * wrs
    assert f"{backs} back{'s' if backs != 1 else ''} drawn from the personnel" in d["approximate"]
    assert not any("aren't told apart" in s for s in d["approximate"])
    assert all(p["approx"] for p in players(d, "receiver"))


def test_ftn_backs_win_over_the_personnel_but_the_tight_ends_come_from_the_personnel():
    d = build(row_2025(personnel="12", backfield=2))  # personnel says 1 back, FTN says 2
    assert len(players(d, "back")) == 2
    assert [p["label"] for p in players(d, "receiver")] == ["TE", "TE", "WR"]


@pytest.mark.parametrize("personnel", [None, "", "1", "111", "ab", "1x", "11 "])
def test_a_malformed_personnel_is_ignored(personnel):
    d = build(row_2019(personnel=personnel))
    if personnel == "11 ":  # whitespace is stripped by the reader of the field
        return
    assert [p["label"] for p in players(d, "receiver")] == ["R"] * 4
    assert not any("personnel:" in k for k in d["known"])


def test_tight_ends_stand_inline_and_a_third_one_stacks_outside():
    d = build(row_2025(personnel="13", backfield=1))
    te = [start_of(p) for p in players(d, "receiver") if p["label"] == "TE"]
    assert len(te) == 3
    at(te[0], 24.4, MIDDLE + 4.55)
    at(te[1], 24.4, MIDDLE - 4.55)
    at(te[2], 24.4, MIDDLE + 4.55 + 1.2)


# ---- the box and the secondary

BOX = {1: (1, 0), 2: (2, 0), 3: (3, 0), 4: (4, 0), 5: (4, 1), 6: (4, 2), 7: (4, 3), 8: (5, 3),
       9: (6, 3), 10: (6, 4), 11: (6, 5)}  # fmt: skip


@pytest.mark.parametrize("box", range(1, 12))
def test_the_box_count_splits_into_linemen_and_linebackers_and_the_rest_are_in_the_secondary(box):
    dl, lb = BOX[box]
    d = build(make_row(box=box))
    assert (len(players(d, "dl")), len(players(d, "lb")), len(players(d, "db"))) == (
        dl, lb, 11 - box,
    )  # fmt: skip
    assert f"{box} defenders in the box (FTN)" in d["known"]
    assert f"Where the {box} box defenders stand: {dl} on the line, {lb} off it" in " ".join(
        d["approximate"]
    )
    assert all(near(start_of(p)[1], 25.0 + D.DL_X) for p in players(d, "dl"))
    assert all(near(start_of(p)[1], 25.0 + D.LB_X) for p in players(d, "lb"))
    # centred on the ball
    for group in (players(d, "dl"), players(d, "lb")):
        if group:
            ys = [start_of(p)[2] for p in group]
            assert near(sum(ys) / len(ys), MIDDLE, 0.02)
    # every defender is approximate: the count is charted, not the spots
    assert all(p["approx"] for p in players(d, "defense" and "dl") + players(d, "lb"))
    if box < 11:
        assert any(
            f"Where the {11 - box} defenders outside the box stand" in s for s in d["approximate"]
        )
    else:
        assert not any("outside the box" in s for s in d["approximate"])
    assert f"{box} in the box" in d["phases"][0]["text"]


def test_dl_and_lb_spacing():
    d = build(make_row(box=7))  # 4 DL, 3 LB
    dls = [start_of(p)[2] for p in players(d, "dl")]
    assert all(near(b - a, 1.7, 0.02) for a, b in zip(dls, dls[1:], strict=False))
    lbs = [start_of(p)[2] for p in players(d, "lb")]
    assert all(near(b - a, 3.2, 0.02) for a, b in zip(lbs, lbs[1:], strict=False))


@pytest.mark.parametrize("box", [None, 0, 12, -3])
def test_an_uncharted_box_is_drawn_as_seven_and_says_so_without_claiming_it(box):
    d = build(make_row(box=box))
    assert (len(players(d, "dl")), len(players(d, "lb"))) == (4, 3)
    assert "7 defenders drawn in the box (the box count isn't charted)" in d["approximate"]
    assert not any("in the box" in k for k in d["known"])
    assert "in the box" not in d["phases"][0]["text"]
    assert not any("box defenders stand" in s for s in d["approximate"])


def test_participation_supplies_the_box_when_ftn_has_none_and_ftn_wins_when_both_do():
    d = build(row_2019(part_box=9))
    assert "9 defenders in the box (participation)" in d["known"]
    assert (len(players(d, "dl")), len(players(d, "lb"))) == (6, 3)
    d = build(row_2025(box=6, part_box=8))
    assert "6 defenders in the box (FTN)" in d["known"]
    assert (len(players(d, "dl")), len(players(d, "lb"))) == (4, 2)
    d = build(row_2025(box=None, part_box=8))
    assert "8 defenders in the box (participation)" in d["known"]
    # participation is ignored when the play has none
    d = build(make_row(box=None, part_box=9))
    assert (len(players(d, "dl")), len(players(d, "lb"))) == (4, 3)


def sec(d):
    return {
        "cb": [p for p in players(d, "db") if p["label"] == "CB"],
        "s": [p for p in players(d, "db") if p["label"] == "S"],
        "db": [p for p in players(d, "db") if p["label"] == "DB"],
    }


def test_corners_stand_over_the_widest_receivers():
    d = build(make_row())  # the widest receivers at D.WIDE_Y from each sideline
    cb = sec(d)["cb"]
    assert [p["id"] for p in cb] == ["d_cb1", "d_cb2"]
    at(start_of(cb[0]), 25.0 + D.CB_X, D.WIDE_Y + 0.8)
    at(start_of(cb[1]), 25.0 + D.CB_X, W - D.WIDE_Y - 0.8)


@pytest.mark.parametrize(
    ("coverage", "safeties"),
    [
        ("cover_0", 0),
        ("cover_1", 1),
        ("cover_3", 1),
        ("cover_2", 2),
        ("cover_4", 2),
        ("cover_6", 2),
        ("2_man", 2),
        ("cover_9", 2),  # no shell for it: the generic two-deep
        ("combo", 2),
        ("prevent", 2),
        ("blown", 2),
        (None, 2),
    ],
)
def test_the_coverage_shell_decides_the_deep_safeties(coverage, safeties):
    d = build(row_2025(box=6, coverage=coverage, man_zone="zone"))  # 5 outside the box
    s = sec(d)
    assert len(s["cb"]) == 2 and len(s["s"]) == safeties and len(s["db"]) == 3 - safeties
    if safeties == 1:
        at(start_of(s["s"][0]), 25.0 + D.SAFETY_X, MIDDLE)
    if safeties == 2:
        at(start_of(s["s"][0]), 25.0 + D.SAFETY_X, MIDDLE - 9.0)
        at(start_of(s["s"][1]), 25.0 + D.SAFETY_X, MIDDLE + 9.0)
    assert all(near(start_of(p)[1], 25.0 + D.OVERHANG_X) for p in s["db"])
    text = " ".join(d["approximate"])
    if coverage in D.SHELL_DEEP:
        assert f"the safeties by the {D.COVERAGE_NAME[coverage]} shell" in text
    else:
        assert "two deep safeties as a generic shell" in text
    if coverage:
        assert any(k.startswith(f"Coverage: {D.COVERAGE_NAME[coverage]}, zone") for k in d["known"])
    else:
        assert not any(k.startswith("Coverage") for k in d["known"])


def test_man_zone_is_shown_only_as_man_or_zone():
    assert (
        "Coverage: Cover 1, man (participation, research data)"
        in build(row_2025(coverage="cover_1", man_zone="man"))["known"]
    )
    assert (
        "Coverage: Cover 1 (participation, research data)"
        in build(row_2025(coverage="cover_1", man_zone="mixed"))["known"]
    )


def test_a_coverage_without_participation_is_ignored():
    d = build(make_row(coverage="cover_0"))  # 2026: no participation, whatever the column says
    assert len(sec(d)["s"]) == 2
    assert not any(k.startswith("Coverage") for k in d["known"])


@pytest.mark.parametrize(
    ("box", "coverage", "cb", "s", "db"),
    [
        (8, "cover_2", 2, 1, 0),  # three outside the box: two corners and one safety
        (8, "cover_0", 2, 0, 1),
        (8, None, 2, 1, 0),
        (9, "cover_2", 2, 0, 0),  # two outside the box: corners only
        (10, "cover_3", 1, 0, 0),  # one outside the box: one corner
        (11, "cover_3", 0, 0, 0),
        (4, "cover_0", 2, 0, 5),
        (4, None, 2, 2, 3),
        (1, "cover_3", 2, 1, 7),
    ],
)
def test_a_short_secondary_fills_corners_then_safeties_then_the_rest(box, coverage, cb, s, db):
    d = build(row_2025(box=box, coverage=coverage))
    got = sec(d)
    assert (len(got["cb"]), len(got["s"]), len(got["db"])) == (cb, s, db)
    assert len(players(d, "db")) == 11 - box


# ---- the rush


def rushers(d):
    return [p["id"] for p in d["players"] if p["rusher"]]


def test_four_rushers_are_the_four_down_linemen():
    d = build(make_row(rushers=4))
    assert rushers(d) == ["d_dl1", "d_dl2", "d_dl3", "d_dl4"]
    assert "4 pass rushers (FTN)" in d["known"]


def test_a_three_man_rush_drops_one_lineman_who_does_not_move():
    d = build(make_row(rushers=3))
    assert rushers(d) == ["d_dl1", "d_dl2", "d_dl3"]
    dropped = player(d, "d_dl4")
    assert dropped["rusher"] is False and len(dropped["track"]) == 1
    assert "3 pass rushers (FTN)" in d["known"]


def test_a_fifth_rusher_is_the_linebacker_nearest_the_ball():
    d = build(make_row(box=7, rushers=5))  # linebackers at y 23.47, 26.67, 29.87
    assert rushers(d) == ["d_dl1", "d_dl2", "d_dl3", "d_dl4", "d_lb2"]
    d = build(make_row(box=7, rushers=6))
    assert len(rushers(d)) == 6 and sum(p["role"] == "lb" for p in d["players"] if p["rusher"]) == 2


def test_more_rushers_than_the_box_comes_from_the_secondary_and_says_so():
    d = build(make_row(box=4, rushers=6))  # 4 linemen, no linebackers
    got = [p for p in d["players"] if p["rusher"]]
    assert len(got) == 6 and sum(p["role"] == "dl" for p in got) == 4
    outside = [p for p in got if p["role"] == "db"]
    assert len(outside) == 2 and all(
        p["label"] == "DB" for p in outside
    )  # the overhang DBs go first
    assert "Which defenders from outside the box rushed" in d["approximate"]
    d = build(make_row(box=6, rushers=4))
    assert "Which defenders from outside the box rushed" not in d["approximate"]


@pytest.mark.parametrize("n", [1, 2, 6, 8, 11])
def test_the_rusher_count_is_exact(n):
    d = build(make_row(box=7, rushers=n))
    assert len(rushers(d)) == n


@pytest.mark.parametrize("n", [None, 0, 12, -1])
def test_an_unusable_rusher_count_is_unknown_and_nobody_is_marked(n):
    d = build(make_row(rushers=n))
    assert rushers(d) == []
    assert "How many rushed the passer" in d["unknown"]
    assert not any("pass rushers" in k for k in d["known"])


def test_rushers_stop_a_step_short_of_the_qb_when_the_qb_sets_up():
    d = build(make_row(rushers=4, pass_location="right"))
    qb = player(d, "o_qb")
    set_up = next(k for k in qb["track"] if near(k[0], 1.7))
    t_end = None
    for p in d["players"]:
        if not p["rusher"]:
            continue
        assert near(p["track"][1][0], 0.8) and p["track"][1][1:] == p["track"][0][1:]
        k = end_of(p)
        t_end = k[0]
        assert near(k[0], 2.1)  # 0.4 s after the QB sets
        assert math.hypot(k[1] - set_up[1], k[2] - set_up[2]) == pytest.approx(2.0, abs=0.03)
    assert t_end is not None


def test_participation_supplies_the_rushers_when_ftn_has_none():
    d = build(row_2019(part_rushers=5))
    assert "5 pass rushers (participation)" in d["known"]
    assert len(rushers(d)) == 5
    d = build(row_2025(rushers=4, part_rushers=6))
    assert "4 pass rushers (FTN)" in d["known"] and len(rushers(d)) == 4


def test_a_run_has_no_rush():
    d = build(run_row(rushers=4))
    assert rushers(d) == []
    assert not any("pass rushers" in k for k in d["known"])


# ---- motion and the play-action fake


def test_no_motion_means_no_motion_mark_and_nobody_moves_before_the_snap():
    d = build(make_row(motion=False))
    assert not marks(d, "motion") and "motion" not in phase_names(d)
    assert "No motion before the snap (FTN)" in d["known"]
    assert not any("in motion" in s for s in d["approximate"])


@pytest.mark.parametrize("hash_", ["left", "middle", "right"])
def test_motion_moves_the_receiver_nearest_the_ball_six_yards_towards_it(hash_):
    d = build(make_row(motion=True, hash=hash_, rushers=4, pass_location="middle"))
    m = marks(d, "motion")[0]
    by = d["field"]["ball_y"]
    (x0, y0), (x1, y1) = m["points"]
    assert near(x0, x1) and near(abs(y1 - y0), 6.0, 0.02)
    assert abs(y1 - by) < abs(y0 - by)  # towards the ball
    assert m["t"] == 0.15 and m["approx"] is True
    assert (
        phase(d, "motion")["t"] == 0.15 and phase(d, "motion")["text"] == "Motion before the snap"
    )
    mover = next(p for p in players(d, "receiver") if p["track"][0][1:] == [x0, y0])
    assert near(mover["track"][1][0], 0.15) and near(mover["track"][2][0], 0.7)
    at(mover["track"][2], x1, y1)
    # the others stay where they were until the snap
    movers = [p for p in players(d, "receiver") if p["track"][0][1:] == [x0, y0]]
    assert len(movers) == 1


def test_a_tight_end_does_not_go_in_motion_while_a_wide_receiver_can():
    d = build(row_2025(personnel="12", motion=True))
    mover = next(
        p for p in players(d, "receiver") if len(p["track"]) >= 3 and p["track"][1][0] == 0.15
    )
    assert mover["label"] == "WR"


def test_motion_with_nobody_to_move_is_known_but_not_drawn():
    d = build(row_2025(personnel="23", extra_ol=True, backfield=4, motion=True))
    assert not players(d, "receiver")
    assert "Motion before the snap (FTN)" in d["known"] and not marks(d, "motion")


def test_a_play_action_fake_goes_to_the_deepest_back_and_delays_the_drop():
    d = build(make_row(play_action=True, backfield=2, pass_location="right", air_yards=9.0))
    assert phase(d, "fake")["t"] == 1.0
    qb = player(d, "o_qb")
    mesh = next(k for k in qb["track"] if near(k[0], 1.45))
    at(mesh, 25.0 - 3.5, MIDDLE + 0.8)
    assert near(next(k for k in qb["track"] if k[1] < 19)[0], 2.15)  # set after the fake
    back = players(d, "back")[-1]
    assert near(end_of(back)[0], 1.95) and near(end_of(back)[1], 25.5)
    assert len(players(d, "back")[0]["track"]) == 1
    f = marks(d, "fake")[0]
    assert f["approx"] is True and f["t"] == 1.0
    # the ball rides to the mesh and back out with the drop
    assert any(near(b[0], 1.45) and near(b[1], 21.5) for b in d["ball"])


def test_no_fake_without_a_back_or_without_the_flag_or_on_a_run():
    assert "fake" not in phase_names(build(make_row(play_action=True, backfield=0)))
    assert "fake" not in phase_names(build(make_row(play_action=False)))
    assert "fake" not in phase_names(build(make_row(play_action=None)))
    assert "fake" not in phase_names(build(run_row(play_action=True)))
    # a fake delays the throw by the fake's 0.45 s
    plain = build(make_row(pass_location="right"))
    fake = build(make_row(pass_location="right", play_action=True))
    assert near(phase(fake, "throw")["t"] - phase(plain, "throw")["t"], 0.45)


# ---- the run

LANES = [
    ("middle", None, 0.0, "up the middle"),
    ("middle", "guard", 0.0, "up the middle"),
    ("left", "guard", -1.3, "left guard"),
    ("right", "guard", 1.3, "right guard"),
    ("left", "tackle", -2.6, "left tackle"),
    ("right", "tackle", 2.6, "right tackle"),
    ("left", "end", -5.5, "left end"),
    ("right", "end", 5.5, "right end"),
    ("left", None, -3.5, "left"),
    ("right", None, 3.5, "right"),
    ("left", "wide", -3.5, "left"),
]


@pytest.mark.parametrize(("loc", "gap", "offset", "where"), LANES)
def test_every_run_lane(loc, gap, offset, where):
    row = run_row(run_location=loc, run_gap=gap, yards_gained=4.0)
    d = build(row)
    lane = marks(d, "lane")[0]
    pt(lane["points"][1], 25.5, MIDDLE + offset)
    pt(lane["points"][0], 23.5, MIDDLE + offset)
    assert lane["text"] == f"Run {where}" and lane["approx"] is False
    carrier = next(p for p in d["players"] if p["carrier"])
    through = next(k for k in carrier["track"] if near(k[1], 25.5))
    at(through, 25.5, MIDDLE + offset)
    at(end_of(carrier), 29.0, MIDDLE + offset)  # a short gain: it goes straight on
    assert f"The run's lane: {where} (play-by-play)" in d["known"]
    assert D.concept_of(row) == d["concept"]


def test_a_run_with_no_recorded_direction_goes_up_the_middle_and_says_it_does_not_know():
    d = build(run_row(run_location=None, run_gap=None))
    lane = marks(d, "lane")[0]
    pt(lane["points"][1], 25.5, MIDDLE)
    assert lane["text"] is None
    assert "The run's direction (not recorded for this play)" in d["unknown"]
    assert not any(k.startswith("The run's lane") for k in d["known"])


def test_the_lane_is_measured_from_the_ball_on_the_hash():
    d = build(run_row(hash="left", run_location="right", run_gap="tackle"))
    pt(marks(d, "lane")[0]["points"][1], 25.5, 23.58 + 2.6)


@pytest.mark.parametrize(
    ("yards", "end_x", "drifts"),
    [
        (6.0, 31.0, True),
        (5.0, 30.0, False),
        (-3.0, 22.0, False),
        (0.0, 25.0, False),
        (-6.0, 19.0, True),
    ],
)
def test_the_carrier_ends_at_the_line_plus_the_gain_and_drifts_to_the_middle_on_a_long_run(
    yards, end_x, drifts
):
    d = build(run_row(run_location="right", run_gap="end", yards_gained=yards, first_down=False))
    carrier = next(p for p in d["players"] if p["carrier"])
    lane_y = MIDDLE + 5.5
    want_y = lane_y + 0.25 * (MIDDLE - lane_y) if drifts else lane_y
    at(end_of(carrier), end_x, want_y)
    at(d["ball"][-1], end_x, want_y)


def test_a_run_for_no_gain_or_a_missing_gain_still_has_an_end():
    for yards in (0.0, None):
        d = build(run_row(yards_gained=yards))
        carrier = next(p for p in d["players"] if p["carrier"])
        assert near(end_of(carrier)[1], 25.0)
        assert phase(d, "end")["text"] == (
            "Run, no gain" if yards == 0.0 else "Run, no gain recorded"
        )


def test_the_deepest_back_carries_a_run_and_the_qb_hands_off():
    d = build(run_row(backfield=2, qb_alignment="shotgun", shotgun=True))
    carrier = next(p for p in d["players"] if p["carrier"])
    assert carrier["id"] == "o_rb2"
    assert phase(d, "handoff")["t"] == 1.4 and marks(d, "handoff")
    qb = player(d, "o_qb")
    assert qb["carrier"] is False and "carry" not in phase_names(d)


def test_a_qb_run_is_carried_by_the_qb_when_the_rusher_is_a_passer_of_the_game():
    row = run_row(rusher_id="00-QB-KC", run_location="middle", run_gap=None, backfield=1)
    d = build(row, qb=["00-QB-KC"])
    qb = player(d, "o_qb")
    assert qb["carrier"] is True and not any(p["carrier"] for p in players(d, "back"))
    assert phase_names(d) == ["presnap", "snap", "carry", "end"]
    assert phase(d, "carry")["text"] == "The QB keeps it" and phase(d, "carry")["t"] == 1.0
    assert not marks(d, "handoff")
    assert "The QB carried it (play-by-play)" in d["known"]
    at(end_of(qb), 31.0, MIDDLE + 0.25 * 0)  # up the middle, 6 yards
    # another passer's id, or no id at all, is a back's run
    d = build(row, qb=["00-QB-OTHER"])
    assert next(p for p in d["players"] if p["carrier"])["role"] == "back"
    d = build(row, qb=[])
    assert next(p for p in d["players"] if p["carrier"])["role"] == "back"
    d = build({**row, "rusher_id": None}, qb=[None, ""])
    assert next(p for p in d["players"] if p["carrier"])["role"] == "back"


def test_qb_ids_may_be_any_iterable():
    row = run_row(rusher_id="q1")
    assert next(p for p in D.diagram(row, iter(["q1"]))["players"] if p["carrier"])["role"] == "qb"
    assert next(p for p in D.diagram(row, ("q1",))["players"] if p["carrier"])["role"] == "qb"
    assert next(p for p in D.diagram(row, {"q1"})["players"] if p["carrier"])["role"] == "qb"


def test_with_an_empty_backfield_the_qb_keeps_it_without_claiming_he_was_the_rusher():
    d = build(run_row(backfield=0, qb_alignment="shotgun", shotgun=True))
    qb = player(d, "o_qb")
    assert qb["carrier"] is True and phase(d, "carry")["text"] == "The QB keeps it"
    assert "The QB carried it (play-by-play)" not in d["known"]
    assert d["phases"][0]["text"].startswith("Before the snap: QB shotgun, empty backfield")


# ---- the throw


@pytest.mark.parametrize(("loc", "cy"), [("left", 10.5), ("middle", MIDDLE), ("right", W - 10.5)])
def test_the_throw_lands_in_the_charted_third(loc, cy):
    d = build(make_row(pass_location=loc, air_yards=12.0, yards_gained=15.0))
    thr = marks(d, "throw")[0]
    pt(thr["points"][1], 37.0, cy)
    assert thr["approx"] is False
    assert f"Throw: 12 air yards, {loc}" == phase(d, "throw")["text"]
    # the last ball key of the throw is at the catch spot at the catch time
    catch = phase(d, "catch")
    flight_end = next(b for b in d["ball"] if near(b[0], catch["t"], 0.006))
    at(flight_end, 37.0, cy)


@pytest.mark.parametrize(("loc", "dy"), [("left", -9.0), ("middle", 0.0), ("right", 9.0)])
def test_a_throw_behind_the_line_goes_to_the_flat_on_that_side(loc, dy):
    d = build(make_row(pass_location=loc, air_yards=-3.0, yards_gained=5.0))
    pt(marks(d, "throw")[0]["points"][1], 22.0, MIDDLE + dy)
    d = build(make_row(pass_location=loc, air_yards=-3.0, yards_gained=5.0, hash="left"))
    pt(marks(d, "throw")[0]["points"][1], 22.0, 23.58 + dy)


def test_a_throw_at_the_line_goes_to_the_third_not_the_flat():
    d = build(make_row(pass_location="left", air_yards=0.0, yards_gained=3.0))
    pt(marks(d, "throw")[0]["points"][1], 25.0, 10.5)
    assert phase(d, "throw")["text"] == "Throw: at the line, left"


def test_a_check_down_can_go_to_a_back_but_a_longer_throw_goes_to_a_receiver():
    d = build(make_row(pass_location="middle", air_yards=1.0, yards_gained=3.0))
    assert next(p for p in d["players"] if p["target"])["role"] == "back"
    d = build(make_row(pass_location="middle", air_yards=2.0, yards_gained=3.0))
    assert next(p for p in d["players"] if p["target"])["role"] == "receiver"


def test_the_target_is_the_receiver_nearest_the_third_it_was_thrown_to():
    for loc, want in (("left", "o_r2"), ("right", "o_r1")):
        d = build(make_row(pass_location=loc, air_yards=12.0, yards_gained=12.0))
        assert [p["id"] for p in d["players"] if p["target"]] == [want]


def test_with_no_receivers_a_back_is_the_target():
    d = build(row_2025(personnel="23", extra_ol=True, backfield=4, air_yards=9.0))
    assert not players(d, "receiver")
    assert next(p for p in d["players"] if p["target"])["role"] == "back"


@pytest.mark.parametrize(("air", "quick"), [(2.0, True), (5.0, True), (6.0, False), (20.0, False)])
@pytest.mark.parametrize("align", ["shotgun", "under_center", "pistol"])
def test_a_quick_throw_gets_a_shorter_drop(air, quick, align):
    """Air yards <= 5 take a yard off the drop (DROP_DEPTH by alignment)."""
    d = build(
        make_row(
            qb_alignment=align,
            shotgun=align != "under_center",
            pass_location="right",
            air_yards=air,
            yards_gained=air,
        )
    )
    qb = player(d, "o_qb")
    set_x = min(k[1] for k in qb["track"])
    want = 25.0 - (D.DROP_DEPTH[align] - (1.0 if quick else 0.0))
    assert near(set_x, want), (align, air, set_x, want)


def test_the_qb_leaves_the_pocket_seven_yards_towards_the_throw():
    for loc, dy in (("left", -7.0), ("right", 7.0), ("middle", 0.0)):
        d = build(
            make_row(out_of_pocket=True, pass_location=loc, air_yards=10.0, yards_gained=10.0)
        )
        qb = player(d, "o_qb")
        at(qb["track"][-1], 25.0 - D.DROP_DEPTH["shotgun"], MIDDLE + dy)
        thr = marks(d, "throw")[0]
        pt(thr["points"][0], 25.0 - D.DROP_DEPTH["shotgun"], MIDDLE + dy)
        assert "The QB left the pocket (FTN)" in d["known"]
    d = build(make_row(out_of_pocket=False))
    assert not any("pocket" in k for k in d["known"])


def test_an_incomplete_pass_falls_short_of_the_receiver_and_nobody_carries_it():
    d = build(make_row(complete=False, yards_gained=0.0, first_down=False, pass_location="right"))
    assert phase_names(d) == ["presnap", "snap", "throw", "end"]
    assert phase(d, "end")["text"] == "Incomplete"
    inc = marks(d, "incomplete")[0]
    pt(inc["points"][0], 32.0, W - 10.5)
    assert inc["text"] == "Incomplete"
    assert d["ball"][-1][3] == 0.0 and near(d["ball"][-1][1], 33.5)  # on the ground, past the spot
    assert not any(p["carrier"] for p in d["players"])
    target = next(p for p in d["players"] if p["target"])
    assert target["carrier"] is False
    assert not any(k.startswith("The gain") for k in d["known"])
    assert not marks(d, "run") and not marks(d, "end")


def test_an_interception_ends_at_the_catch_spot_and_the_return_is_unknown():
    d = build(make_row(complete=False, interception=True, yards_gained=0.0, first_down=False))
    assert phase_names(d) == ["presnap", "snap", "throw", "end"]
    assert phase(d, "end")["text"] == "Intercepted"
    assert marks(d, "interception")[0]["text"] == "Intercepted"
    assert "The interception return" in d["unknown"]
    assert not any(p["carrier"] for p in d["players"])
    assert d["play"]["result"]["turnover"] is True


@pytest.mark.parametrize(
    "row",
    [
        make_row(pass_location=None),
        make_row(pass_location="up"),
        make_row(air_yards=None),
        make_row(air_yards=float("nan")),
        make_row(pass_attempt=False),  # a dropback with no attempt
    ],
)
def test_a_pass_with_no_direction_or_depth_is_not_given_one(row):
    d = build(row)
    assert "Where the ball went (no direction or depth recorded)" in d["unknown"]
    assert phase_names(d) == ["presnap", "snap", "end"]
    assert not marks(d, "throw") and not any(p["target"] for p in d["players"])
    assert near(phase(d, "end")["t"], 2.0)  # 0.3 s after the QB sets
    assert not any(k.startswith("The throw") for k in d["known"])
    assert rushers(d)  # the rush is still drawn


def test_a_deep_throw_near_the_end_zone_stays_inside_the_field():
    d = build(
        make_row(
            yards_to_goal=5, distance=5, air_yards=30.0, yards_gained=5.0, pass_location="left"
        )
    )
    pt(marks(d, "throw")[0]["points"][1], 109.5, 10.5)


def test_yards_after_the_catch_drift_towards_the_middle_only_when_there_are_some():
    # 7 air yards, +9: 2 yards after the catch -> no drift
    d = build(make_row(pass_location="right", air_yards=7.0, yards_gained=9.0))
    target = next(p for p in d["players"] if p["target"])
    at(end_of(target), 34.0, W - 10.5)
    # +10: 3 yards after -> still none (the rule is more than 3)
    d = build(make_row(pass_location="right", air_yards=7.0, yards_gained=10.0))
    at(end_of(next(p for p in d["players"] if p["target"])), 35.0, W - 10.5)
    # +11: 4 yards -> 30% of the way to the middle
    d = build(make_row(pass_location="right", air_yards=7.0, yards_gained=11.0))
    at(
        end_of(next(p for p in d["players"] if p["target"])),
        36.0,
        (W - 10.5) + 0.3 * (MIDDLE - (W - 10.5)),
    )


def test_a_completion_that_loses_yards_runs_back_behind_the_catch():
    d = build(make_row(pass_location="right", air_yards=3.0, yards_gained=-2.0, first_down=False))
    target = next(p for p in d["players"] if p["target"])
    at(end_of(target), 23.0, W - 10.5 + 0.3 * (MIDDLE - (W - 10.5)))
    assert phase(d, "end")["text"] == "Complete, −2 (3 air yards)"


def test_a_lost_fumble_after_a_catch_is_known_and_its_return_is_not():
    d = build(fumble_row("DEN"))
    assert "A fumble the defense recovered (play-by-play)" in d["known"]
    assert "Where the fumble happened and the return" in d["unknown"]
    assert d["play"]["result"]["fumble_lost"] is True
    d = build(fumble_row("KC"))
    assert not any("fumble" in k.lower() for k in d["known"])
    assert "Where the fumble happened and the return" not in d["unknown"]


# ---- aborted snaps and fumbled snaps


def test_an_aborted_snap_stops_at_the_snap():
    d = build(aborted_row())
    assert phase_names(d) == ["presnap", "snap", "end"]
    assert phase(d, "end")["text"] == "Aborted snap" and phase(d, "end")["t"] == 1.0
    assert "The snap was aborted (play-by-play)" in d["known"]
    assert not any(p["rusher"] or p["target"] or p["carrier"] for p in d["players"])
    assert not d["marks"]
    assert "How many rushed the passer" not in d["unknown"]
    assert d["play"]["type"] == "aborted" and d["play"]["result"]["kind"] == "aborted"


@pytest.mark.parametrize(
    ("flags", "kind", "end"),
    [
        ({"pass_attempt": True}, "pass", "Complete, +12 (7 air yards), first down"),
        ({"sack": True, "pass_attempt": False, "yards_gained": -6.0, "first_down": False,
          "pass_location": None, "air_yards": None, "complete": None}, "sack", "Sacked, −6"),
        ({"scramble": True, "pass_attempt": False, "run_location": "right", "first_down": False,
          "pass_location": None, "air_yards": None, "complete": None, "yards_gained": 5.0},
         "scramble", "Scramble, +5"),
    ],
)  # fmt: skip
def test_a_fumbled_snap_the_qb_picked_up_and_played_on_is_drawn_as_that_play(flags, kind, end):
    row = make_row(aborted=True, **flags)
    d = build(row)
    assert d["play"]["type"] == kind
    assert phase(d, "end")["text"] == end
    assert "A fumbled snap, picked up by the QB (play-by-play)" in d["known"]
    assert "The fumbled snap itself isn't drawn" in d["approximate"]
    assert "The snap was aborted (play-by-play)" not in d["known"]


def test_a_qb_who_scrambled_and_then_threw_is_drawn_as_the_pass():
    d = build(make_row(scramble=True, pass_attempt=True))
    assert d["play"]["type"] == "pass" and marks(d, "throw")


# ---- participation (2016-2025)


def test_a_2019_play_has_no_ftn_labels_and_every_one_of_them_is_drawn_generic():
    row = row_2019()
    d = build(row)
    assert d["sources"] == ["nflverse play-by-play", "nflverse participation (research data)"]
    assert d["field"]["hash"] is None
    at(start_of(player(d, "o_qb")), 20.0, MIDDLE)  # the shotgun flag from play-by-play
    assert "11 personnel: 1 back, 1 tight end (participation, research data)" in d["known"]
    assert "6 defenders in the box (participation)" in d["known"]
    assert "Coverage: Cover 3, zone (participation, research data)" in d["known"]
    assert "4 pass rushers (participation)" in d["known"]
    assert "The target's route: hitch curl (participation, research data)" in d["known"]
    assert "Time to throw: 2.61 s (participation)" in d["known"]
    assert "1 back drawn from the personnel" in d["approximate"]
    assert "Motion before the snap (not charted before 2022)" in d["unknown"]
    assert d["concept"] == "cover_3"
    assert d["phases"][0]["text"] == "Before the snap: QB shotgun, 6 in the box"


def test_time_to_throw_is_shown_for_a_pass_and_a_sack_only():
    assert any(k.startswith("Time to throw") for k in build(row_2025())["known"])
    assert any(k.startswith("Time to throw") for k in build(sack_row(**{**_part()}))["known"])
    assert not any(k.startswith("Time to throw") for k in build(scramble_row(**_part()))["known"])
    assert not any(k.startswith("Time to throw") for k in build(run_row(**_part()))["known"])
    assert not any(
        k.startswith("Time to throw") for k in build(make_row(time_to_throw=2.5))["known"]
    )
    assert not any(
        k.startswith("Time to throw") for k in build(row_2025(time_to_throw=None))["known"]
    )


def _part():
    return {"part_charted": True, "personnel": "11", "extra_ol": False, "time_to_throw": 2.4}


# The target of a throw to the middle with three receivers is the right slot, starting at
# (23.8, W - SLOT_Y); the catch is (32, MIDDLE). The point each route passes between them:
X0, Y0 = 25.0 - 1.2, W - D.SLOT_Y
CX, CY = 25.0 + 7.0, MIDDLE
OUT_LIKE = ("out", "in", "in_dig", "quick_out", "deep_out", "cross", "shallow_cross_drag", "angle",
            "texas_angle")  # fmt: skip
ROUTES = {
    # route: the point between the start and the catch (None: a straight line)
    "go": None, "fade": None, "seam": None, "unknown_route": None,
    "post": (31.0, Y0), "corner": (31.0, Y0),  # max(line + 6, catch - 7)
    "slant": (26.5, Y0),  # line + 1.5
    "hitch": (CX + 1.5, Y0), "hitch_curl": (CX + 1.5, Y0), "curl": (CX + 1.5, Y0),
    **dict.fromkeys(OUT_LIKE, (CX, Y0)),
    "flat": (X0, CY), "swing": (X0, CY), "screen": (X0, CY),
    "wheel": (25.0, CY),  # back to the line, then across
}  # fmt: skip


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_the_targets_route_shape_follows_its_name_and_ends_at_the_catch(route):
    """A throw to the middle, 7 air yards from the 25: the catch is (32, 26.67); the target is
    the right slot, starting at (23.8, W - SLOT_Y)."""
    d = build(row_2025(target_route=route, pass_location="middle", personnel="10", backfield=2))
    target = next(p for p in d["players"] if p["target"])
    assert target["id"] == "o_r3"
    keys = target["track"]
    at(keys[0], X0, Y0)
    catch = phase(d, "catch")
    mid = ROUTES[route]
    carried = [k for k in keys if k[0] <= catch["t"] + 0.006]
    at(carried[-1], CX, CY)
    assert near(carried[-1][0], catch["t"], 0.006)
    if mid is None:
        assert len(carried) == 3, (route, keys)
    else:
        assert len(carried) == 4, (route, keys)
        at(carried[2], *mid)
        assert carried[1][0] < carried[2][0] < carried[3][0]
    if route != "unknown_route":
        assert (
            f"The target's route: {route.replace('_', ' ')} (participation, research data)"
            in d["known"]
        )


def test_the_route_is_ignored_without_participation_and_when_absent():
    d = build(make_row(target_route="post", pass_location="right"))
    target = next(p for p in d["players"] if p["target"])
    assert len([k for k in target["track"] if k[0] <= phase(d, "catch")["t"] + 0.006]) == 3
    assert not any("route" in k for k in d["known"])
    d = build(row_2025(target_route=None, pass_location="right"))
    assert not any("target's route" in k for k in d["known"])


# ---- clamps


@pytest.mark.parametrize("ytg", [1, 2, 3, 98, 99])
def test_everything_stays_inside_the_back_lines(ytg):
    for row in (
        make_row(yards_to_goal=ytg, distance=ytg, goal_to_go=ytg < 10),
        run_row(yards_to_goal=ytg, distance=ytg, goal_to_go=ytg < 10),
        sack_row(yards_to_goal=ytg, distance=ytg, goal_to_go=ytg < 10, yards_gained=-12.0),
        make_row(yards_to_goal=ytg, air_yards=25.0, yards_gained=40.0),
        td_pass(yards_to_goal=ytg),
    ):
        d = D.diagram(row)
        xs = [k[1] for p in d["players"] for k in p["track"]] + [b[1] for b in d["ball"]]
        xs += [x for m in d["marks"] for x, _ in m["points"]]
        assert min(xs) >= -9.5 and max(xs) <= 109.5, (ytg, row["desc"], min(xs), max(xs))
        check_diagram(d, row)


def test_the_safeties_of_a_goal_line_stand_sit_in_the_end_zone():
    d = build(make_row(yards_to_goal=1, distance=1, goal_to_go=True, box=6))
    safeties = sec(d)["s"]
    assert safeties and all(near(start_of(p)[1], 109.5) for p in safeties)


def test_the_camera_follows_the_whole_play():
    d = build(deep_shot_row())
    lo, hi = d["field"]["view"]
    assert lo <= 40.0 - 12.0 and hi >= 71.0
    d = build(td_pass())
    assert d["field"]["view"][1] == 110.0 or d["field"]["view"][1] >= 100.5 + 4 - 1e-9


# ---- purity and determinism


def test_the_diagram_does_not_touch_its_row_and_is_deterministic():
    row = deep_shot_row()
    before = copy.deepcopy(row)
    a, b = D.diagram(row), D.diagram(row)
    assert row == before and a == b
    a["players"][0]["track"].append([99, 0, 0])
    assert D.diagram(row) == b  # no shared state between calls


def test_two_rows_that_differ_only_in_the_game_clock_have_the_same_geometry():
    a = D.diagram(make_row())
    b = D.diagram(make_row(quarter=4, half_seconds=33, wp=0.9, score_diff=14))
    for key in ("players", "ball", "marks", "phases", "duration", "field"):
        assert a[key] == b[key], key


# ---- sweeps: every shape of play meets the contract


def sweep_ftn():
    for box, back, align in itertools.product(
        range(1, 12), range(5), ("shotgun", "under_center", "pistol")
    ):
        yield make_row(
            box=box,
            backfield=back,
            qb_alignment=align,
            shotgun=align != "under_center",
            rushers=min(box, 11),
            motion=back % 2 == 0,
            play_action=back == 2,
        )


@pytest.mark.parametrize(
    "row", list(sweep_ftn()), ids=lambda r: f"b{r['box']}-bf{r['backfield']}-{r['qb_alignment']}"
)
def test_sweep_box_backfield_alignment(row):
    d = build(row)
    assert len(players(d, "back")) == row["backfield"]
    assert len(players(d, "dl")) + len(players(d, "lb")) == row["box"]


PERSONNEL = ["11", "12", "21", "13", "22", "10", "20", "01", "02", "23", "03", None]
COVERAGE = [None, "cover_0", "cover_1", "cover_2", "cover_3", "cover_4", "cover_6", "2_man",
            "cover_9", "combo", "prevent", "blown"]  # fmt: skip


def sweep_participation():
    for pers, extra, cov, ftn in itertools.product(
        PERSONNEL, (None, False, True), COVERAGE, (False, True)
    ):
        base = row_2025() if ftn else row_2019()
        yield {**base, "personnel": pers, "extra_ol": extra, "coverage": cov, "man_zone": "man",
               "target_route": "post"}  # fmt: skip


@pytest.mark.parametrize("row", list(sweep_participation()))
def test_sweep_participation(row):
    d = build(row)
    assert len(players(d, "ol")) == (6 if row["extra_ol"] else 5)


KINDS = {
    "complete": lambda: make_row(),
    "deep": lambda: make_row(air_yards=35.0, yards_gained=40.0, pass_length="deep"),
    "incomplete": lambda: make_row(complete=False, yards_gained=0.0, first_down=False),
    "interception": lambda: make_row(complete=False, interception=True, yards_gained=0.0),
    "screen": lambda: screen_row(),
    "unlocated": lambda: make_row(pass_location=None),
    "sack": lambda: sack_row(),
    "scramble": lambda: scramble_row(),
    "scramble_right": lambda: scramble_row(run_location="right", yards_gained=-3.0),
    "run_gap": lambda: run_row(),
    "run_end": lambda: run_row(run_location="right", run_gap="end", yards_gained=21.0),
    "run_loss": lambda: run_row(yards_gained=-4.0),
    "run_none": lambda: run_row(run_location=None, run_gap=None),
    "qb_run": lambda: run_row(rusher_id="qb"),
    "aborted": lambda: aborted_row(),
    "touchdown": lambda: td_pass(),
}


@pytest.mark.parametrize("hash_", ["left", "middle", "right", None])
@pytest.mark.parametrize("ytg", [2, 3, 5, 10, 25, 50, 75, 90, 95, 98, 99])
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_sweep_kind_field_position_hash(kind, ytg, hash_):
    row = KINDS[kind]()
    gain = row["yards_gained"]
    row = {
        **row,
        "yards_to_goal": ytg,
        "hash": hash_,
        "goal_to_go": ytg < 10,
        "yards_gained": min(gain, ytg) if gain else gain,
    }
    d = build(row, qb=["qb"])
    assert d["field"]["los"] == 100 - ytg


def test_sweep_random_rows_never_break_the_contract():
    """Seeded fuzz: every column drawn from its real domain, now and then null or NaN. The
    reader hands the diagram a real parquet row, so anything the data can hold must build."""
    rnd = random.Random(2026)
    pick = rnd.choice
    failures: list[str] = []
    for _ in range(1500):
        ftn = rnd.random() < 0.7
        part = rnd.random() < 0.5
        kind = pick(["pass", "run", "sack", "scramble", "aborted", "dropback"])
        row = make_row(
            season=pick([2016, 2019, 2022, 2025, 2026]),
            yards_to_goal=pick([1, 2, 10, 33, 50, 75, 99, None, float("nan")]),
            distance=pick([1, 3, 10, 25, None]),
            goal_to_go=pick([True, False]),
            yards_gained=pick([-12.0, -1.0, 0.0, 3.0, 9.0, 30.0, 75.0, None, float("nan")]),
            air_yards=pick([-8.0, 0.0, 1.0, 5.0, 12.0, 45.0, None, float("nan")]),
            pass_location=pick(["left", "middle", "right", None]),
            pass_length=pick(["short", "deep", None]),
            run_location=pick(["left", "middle", "right", None]),
            run_gap=pick(["guard", "tackle", "end", None]),
            complete=pick([True, False, None]),
            interception=pick([True, False, None]),
            touchdown=pick([True, False]),
            first_down=pick([True, False]),
            sack=kind == "sack",
            scramble=kind == "scramble",
            aborted=kind == "aborted" or rnd.random() < 0.02,
            pass_attempt=kind == "pass",
            dropback=kind in ("pass", "sack", "scramble", "dropback"),
            designed_run=kind == "run",
            shotgun=pick([True, False, None]),
            ftn_charted=ftn,
            part_charted=part,
            hash=pick(["left", "middle", "right", None]) if ftn else None,
            qb_alignment=pick(["shotgun", "under_center", "pistol", None]) if ftn else None,
            backfield=pick([0, 1, 2, 3, 4, 7, None]) if ftn else None,
            box=pick([1, 4, 6, 7, 8, 9, 11, None]) if ftn else None,
            rushers=pick([0, 3, 4, 5, 6, 8, None]) if ftn else None,
            motion=pick([True, False, None]) if ftn else None,
            play_action=pick([True, False, None]) if ftn else None,
            screen=pick([True, False, None]) if ftn else None,
            out_of_pocket=pick([True, False, None]) if ftn else None,
            personnel=pick(PERSONNEL) if part else None,
            extra_ol=pick([True, False, None]) if part else None,
            coverage=pick(COVERAGE) if part else None,
            man_zone=pick(["man", "zone", None]) if part else None,
            target_route=pick(list(ROUTES) + [None]) if part else None,
            part_box=pick([1, 5, 6, 8, 11, None]) if part else None,
            part_rushers=pick([1, 4, 5, 8, None]) if part else None,
            time_to_throw=pick([2.4, None]) if part else None,
            rusher_id=pick(["qb", "rb", None]),
            desc=pick(
                ["x", "FUMBLES (1-A), RECOVERED by DEN-2-B.", "", None, "(Punt formation) y"]
            ),
        )
        try:
            d = D.diagram(row, ["qb"])
            check_diagram(d, None)
        except Exception as e:  # noqa: BLE001 - report the offending row
            failures.append(f"{type(e).__name__}: {str(e)[:160]} for {_short(row)}")
    shown = "\n".join(failures[:6])
    assert not failures, f"{len(failures)} of 1500 rows broke the contract:\n{shown}"


def _short(row):
    keys = ("season", "yards_to_goal", "air_yards", "pass_location", "run_location", "run_gap",
            "yards_gained", "backfield", "box", "rushers", "personnel", "extra_ol", "coverage",
            "target_route", "aborted", "sack", "scramble", "pass_attempt", "dropback")  # fmt: skip
    return {k: row[k] for k in keys}
