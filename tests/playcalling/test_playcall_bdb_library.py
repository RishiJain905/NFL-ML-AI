"""PC03: the Big Data Bowl concept library (`playcalling/bdb.py`, BDB 2026 Analytics): which
plays are which concept (`classify`), which are picked (`pick`), how one tracked pass becomes a
diagram (`real_diagram`: the offense always moves to +x, y is measured from its left sideline),
and the whole build on a SYNTHETIC raw folder (`playcall_bdb_fixtures.py`: made-up plays and
tracking in the BDB 2026 column names). Nothing here is real BDB data.

The library holds five passing concepts only (BDB 2026 tracks pass plays): runs and RPOs have no
real example, whatever the labels say.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import playcall_bdb_fixtures as BF
import polars as pl
import pytest

from nflengine.paths import DataPaths
from nflengine.playcalling import bdb
from nflengine.playcalling import diagrams as D

W = 160 / 3
LIBRARY_CONCEPTS = {"screen", "play_action_deep", "cover_1", "cover_3", "2_man"}


def near(a: float, b: float, tol: float = 0.011) -> bool:
    return abs(a - b) <= tol


@pytest.fixture
def paths(tmp_path) -> DataPaths:
    p = DataPaths(tmp_path)
    for d in p.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def world(paths) -> BF.Synth:
    s = BF.standard_world(bdb.raw_dir(paths))
    s.write()
    return s


# ---- the labels ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "want"),
    [
        ("COVER_1_MAN", "cover_1"),
        ("COVER_1_ZONE", "cover_1"),
        ("Cover-1", "cover_1"),
        ("Cover-1 Double", "cover_1"),
        ("cover 1", "cover_1"),
        ("COVER_3_ZONE", "cover_3"),
        ("COVER_3_MAN", "cover_3"),
        ("COVER_3_SEAM", "cover_3"),
        ("Cover-3 Cloud Left", "cover_3"),
        ("COVER_2_MAN", "2_man"),
        ("2-Man", "2_man"),
        ("Cover-2 Man", "2_man"),
        ("COVER_2_ZONE", None),
        ("COVER_0_MAN", None),
        ("COVER_4_ZONE", None),
        ("COVER_6_ZONE", None),
        ("PREVENT_ZONE", None),
        ("Quarters", None),
        ("Red Zone", None),
        ("Bracket", None),
        ("", None),
        (None, None),
        (float("nan"), None),
    ],
)
def test_coverage_concept(label, want):
    assert bdb.coverage_concept(label) == want


@pytest.mark.parametrize(
    ("value", "want"),
    [
        (True, True), (1, True), (2.0, True), ("Y", True), ("yes", True), ("TRUE", True),
        ("true", True), ("1", True), (" y ", True),
        (False, False), (0, False), (0.0, False), (float("nan"), False), (None, False),
        ("N", False), ("false", False), ("", False), ("0", False), ("no", False),
    ],
)  # fmt: skip
def test_truthy(value, want):
    assert bdb._truthy(value) is want


@pytest.mark.parametrize(
    ("value", "want"),
    [(8, 8.0), ("8.5", 8.5), (-2, -2.0), (None, None), ("x", None), (float("nan"), None),
     (float("inf"), None), ("", None)],
)  # fmt: skip
def test_num(value, want):
    assert bdb._num(value) == want


def test_team_codes_are_the_projects():
    assert bdb._team("LAR") == "LA" and bdb._team("OAK") == "LV" and bdb._team("WSH") == "WAS"
    assert bdb._team("SD") == "LAC" and bdb._team("STL") == "LA" and bdb._team("KC") == "KC"
    assert bdb._team(" KC ") == "KC" and bdb._team(None) is None and bdb._team("") is None


@pytest.mark.parametrize(
    ("clock", "want"),
    [("13:25", "13:25"), ("08:56", "8:56"), ("13:25:10", "13:25"), ("", None), (None, None),
     ("soon", None)],
)  # fmt: skip
def test_the_game_clock(clock, want):
    assert bdb._clock(clock) == want


# ---- classify ------------------------------------------------------------------------------------


def supp_frame(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows), infer_schema_length=None)


def row(pid: int, **over) -> dict:
    """A clean completion with no concept: no screen, no play-action, no listed coverage."""
    base = {"route_of_targeted_receiver": "GO", "team_coverage_type": None, **over}
    return BF.supp_row(2023090700, pid, 1, "KC", "DET", **base)


def concepts_of(*rows: dict) -> dict[int, set[str]]:
    got: dict[int, set[str]] = {}
    for r in bdb.classify(supp_frame(*rows)).iter_rows(named=True):
        got.setdefault(r["play_id"], set()).add(r["concept"])
    return got


def test_classify_names_every_concept_of_a_clean_pass(world, paths):
    raw = bdb.load_raw(paths)
    got = {
        (r["play_id"], r["concept"], r["good"])
        for r in bdb.classify(raw.supp).iter_rows(named=True)
    }
    assert got == {
        (101, "screen", True),
        (101, "cover_3", True),  # a screen against Cover 3 is both
        (102, "play_action_deep", True),
        (102, "cover_1", True),
        (103, "2_man", True),  # a play-action throw of 19 yards is not deep
        (105, "cover_3", False),  # EPA -0.4
        (106, "cover_3", False),
        (109, "cover_3", True),
        (110, "cover_3", True),
    }


def test_classify_leaves_out_nullified_penalized_sacked_and_scrambled_plays(world, paths):
    ids = {r["play_id"] for r in bdb.classify(bdb.load_raw(paths).supp).iter_rows(named=True)}
    assert not ids & {104, 111, 107, 112, 108, 113}


def test_classify_rules_one_by_one():
    got = concepts_of(
        row(1, route_of_targeted_receiver="SCREEN"),
        row(2, route_of_targeted_receiver="screen", pass_result="I"),
        row(3, route_of_targeted_receiver="SCREEN", pass_result="S"),
        row(4, play_action="Y", pass_length=20),
        row(5, play_action="Y", pass_length=19),
        row(6, play_action="Y", pass_length=None),
        row(7, play_action="N", pass_length=30),
        row(8, play_action="Y", pass_length=30, pass_result="IN"),
        row(9, play_action="Y", pass_length=30, pass_result="R"),
        row(10, team_coverage_type="COVER_1_MAN"),
        row(11, team_coverage_type="COVER_3_ZONE"),
        row(12, team_coverage_type="COVER_2_MAN"),
        row(13, team_coverage_type="COVER_2_ZONE"),
        row(14, team_coverage_type="COVER_0_MAN"),
        row(15, team_coverage_type="COVER_4_ZONE"),
        row(16, team_coverage_type="COVER_6_ZONE"),
        row(17, team_coverage_type="COVER_3_ZONE", play_nullified_by_penalty="Y"),
        row(18, team_coverage_type="COVER_3_ZONE", penalty_yards=10),
        row(19, team_coverage_type="COVER_3_ZONE", penalty_yards=-5),
        row(20, team_coverage_type="COVER_3_ZONE", penalty_yards=0),
        row(21, team_coverage_type="COVER_3_ZONE", pass_result=None),
        row(22, team_coverage_type="COVER_3_ZONE", pass_result="S"),
        row(23, play_action=True, pass_length=22),
        row(24, route_of_targeted_receiver=None),
    )
    assert got == {
        1: {"screen"},
        2: {"screen"},  # an incompletion is still a thrown ball
        4: {"play_action_deep"},  # 20 air yards is the threshold
        8: {"play_action_deep"},  # and so is an interception
        10: {"cover_1"},
        11: {"cover_3"},
        12: {"2_man"},
        20: {"cover_3"},  # penalty yards of 0 are fine
        23: {"play_action_deep"},  # a boolean play-action too
    }


def test_a_play_can_be_all_three_kinds_at_once():
    got = concepts_of(
        row(1, route_of_targeted_receiver="SCREEN", play_action="Y", pass_length=25,
            team_coverage_type="COVER_3_ZONE"),
    )  # fmt: skip
    assert got == {1: {"screen", "play_action_deep", "cover_3"}}


def test_good_means_the_play_gained_expected_points():
    out = bdb.classify(
        supp_frame(
            row(1, team_coverage_type="COVER_3_ZONE", expected_points_added=0.01),
            row(2, team_coverage_type="COVER_3_ZONE", expected_points_added=0.0),
            row(3, team_coverage_type="COVER_3_ZONE", expected_points_added=-2.0),
            row(4, team_coverage_type="COVER_3_ZONE", expected_points_added=None),
        )
    )
    assert {r["play_id"]: r["good"] for r in out.iter_rows(named=True)} == {
        1: True,
        2: False,
        3: False,
        4: False,
    }


def test_classify_schema_is_stable_even_when_nothing_qualifies():
    out = bdb.classify(supp_frame(row(1, play_nullified_by_penalty="Y")))
    assert out.is_empty()
    assert out.schema == {
        "game_id": pl.Int64, "play_id": pl.Int64, "week": pl.Int64, "concept": pl.String,
        "good": pl.Boolean,
    }  # fmt: skip


def test_runs_and_rpos_have_no_concept_in_the_library():
    """BDB 2026 tracks pass plays only: even a row that looks like an inside-zone run or an RPO
    (labels from other editions) gives no run or RPO concept."""
    run_like = row(
        1, pass_result=None, team_coverage_type="COVER_3_ZONE", pff_runConceptPrimary="INSIDE ZONE",
        pff_runPassOption=1,
    )  # fmt: skip
    rpo_pass = row(2, team_coverage_type="COVER_3_ZONE", pff_runPassOption=1)
    got = concepts_of(run_like, rpo_pass)
    assert got == {2: {"cover_3"}}  # the pass is a Cover-3 pass; nothing is an RPO or a run
    every = set(bdb.classify(supp_frame(*[
        row(i, route_of_targeted_receiver=r, play_action=a, pass_length=25, team_coverage_type=c)
        for i, (r, a, c) in enumerate(
            [
                ("SCREEN", "Y", "COVER_1_MAN"),
                ("GO", "Y", "COVER_3_ZONE"),
                ("HITCH", "N", "COVER_2_MAN"),
            ],
            start=1,
        )
    ]))["concept"])  # fmt: skip
    assert every <= LIBRARY_CONCEPTS and not every & {"rpo", "inside_zone", "outside_zone"}


# ---- pick ----------------------------------------------------------------------------------------


COLS = ("game_id", "play_id", "week", "concept", "good")


def cands(*rows) -> pl.DataFrame:
    return pl.DataFrame(
        [dict(zip(COLS, r, strict=True)) for r in rows],
        schema={"game_id": pl.Int64, "play_id": pl.Int64, "week": pl.Int64, "concept": pl.String,
                "good": pl.Boolean},
    )  # fmt: skip


def test_pick_is_deterministic_good_first_and_round_robin_over_games(world, paths):
    cand = bdb.classify(bdb.load_raw(paths).supp)
    a = bdb.pick(cand, [1, 2], 3)
    b = bdb.pick(cand.reverse(), [1, 2], 3)
    key = ["concept", "game_id", "play_id"]
    assert a.sort(key).equals(b.sort(key))
    c3 = a.filter(pl.col("concept") == "cover_3")
    # three games, one Cover-3 pass from each (the good one of game 1 first), not game 1's two
    assert c3["game_id"].n_unique() == 3 and set(c3["play_id"]) == {101, 105, 110}
    # the concepts come out in the library's order
    order = [c for c in D.CONCEPTS if c in set(a["concept"])]
    assert list(dict.fromkeys(a["concept"].to_list())) == order
    assert a.schema == cand.schema


def test_pick_takes_a_games_good_plays_before_its_bad_ones():
    c = cands(
        (1, 10, 1, "cover_3", False),
        (1, 11, 1, "cover_3", True),
        (2, 20, 1, "cover_3", False),
        (2, 21, 1, "cover_3", True),
        (2, 22, 1, "cover_3", True),
    )
    got = bdb.pick(c, [1], 4)
    # round-robin over the games, each game's good plays before its bad ones
    assert got["play_id"].to_list() == [11, 21, 10, 22]
    assert bdb.pick(c, [1], 2)["play_id"].to_list() == [11, 21]
    assert bdb.pick(c, [1], 5)["play_id"].to_list() == [11, 21, 10, 22, 20]


def test_pick_caps_per_concept_and_honours_the_weeks():
    c = cands(
        *[(g, g * 10 + i, 1 + g % 2, "screen", True) for g in (1, 2, 3) for i in (1, 2)],
        *[(g, g * 10 + 5, 1, "2_man", False) for g in (1, 2)],
    )
    for cap in (1, 2, 3):
        got = bdb.pick(c, [1, 2], cap)
        assert max(got.group_by("concept").len()["len"]) <= cap
    only2 = bdb.pick(c, [2], 10)
    assert set(only2["week"]) == {2} and set(only2["game_id"]) == {1, 3}
    assert bdb.pick(c, [7], 10).is_empty()
    assert bdb.pick(c.head(0), [1], 10).is_empty()
    assert bdb.pick(c, [1, 2], 10).filter(pl.col("concept") == "screen").height == 6


def test_pick_takes_a_games_next_play_only_when_the_other_games_are_used_up():
    c = cands(
        (1, 1, 1, "cover_1", True), (1, 2, 1, "cover_1", True), (1, 3, 1, "cover_1", True),
        (2, 9, 1, "cover_1", True),
    )  # fmt: skip
    got = bdb.pick(c, [1], 3)
    assert sorted(got["play_id"]) == [1, 2, 9]
    assert got["game_id"].to_list().count(1) == 2


# ---- the files -----------------------------------------------------------------------------------


def test_a_week_is_on_disk_only_with_both_its_tracking_files(paths):
    raw = bdb.raw_dir(paths)
    raw.mkdir(parents=True)
    for n in (
        "input_2023_w01.csv", "output_2023_w01.csv",  # a whole week
        "input_2023_w02.csv",  # no output yet
        "output_2023_w03.csv",  # no input
        "input_2023_w10.csv", "output_2023_w10.csv",
        "input_2022_w04.csv", "output_2022_w04.csv",  # another season
        "input_2023_wxx.csv", "input_2023_w05.csv.zip", "supplementary_data.csv",
    ):  # fmt: skip
        (raw / n).write_text("x")
    assert bdb.tracking_weeks(paths) == [1, 10]


def test_read_frames_returns_only_the_requested_plays(world, paths):
    keys = pl.DataFrame({"game_id": [BF.G1], "play_id": [101]})
    inp, out = bdb.read_frames(paths, 1, keys)
    assert set(inp["play_id"]) == {101} and set(out["play_id"]) == {101}
    assert inp.height == 12 * BF.N_IN and out.height == len(BF.OUT_PLAYERS) * BF.N_OUT
    assert {"nfl_id", "frame_id", "x", "y", "player_role", "ball_land_x"} <= set(inp.columns)
    assert set(out.columns) == {"game_id", "play_id", "nfl_id", "frame_id", "x", "y"}
    two = pl.DataFrame({"game_id": [BF.G1, BF.G2], "play_id": [101, 103]})
    inp2, _ = bdb.read_frames(paths, 1, two)
    assert set(inp2["play_id"]) == {101, 103}


def test_load_raw_names_the_missing_table_and_reads_the_labels(paths, world):
    raw = bdb.load_raw(paths)
    assert raw.weeks == [1, 2] and raw.supp.height == 13
    (bdb.raw_dir(paths) / "supplementary_data.csv").unlink()
    with pytest.raises(FileNotFoundError, match=r"supplementary_data\.csv"):
        bdb.load_raw(paths)


# ---- one real play -> the diagram shape ----------------------------------------------------------


def real(direction="right", ayl=BF.LOS_X, concept="screen", play=None, **kw):
    gid, pid = BF.G1, 101
    play = play or BF.supp_row(gid, pid, 1, "KC", "DET")
    inp, out = BF.tracking(gid, pid, direction=direction, ayl=ayl, **kw)
    return bdb.real_diagram(
        play, pl.DataFrame(inp, infer_schema_length=None),
        pl.DataFrame(out, infer_schema_length=None), concept,
    )  # fmt: skip


def by_id(d, pid):
    return next(p for p in d["players"] if p["id"] == pid)


def test_a_right_moving_play_maps_x_minus_ten_and_y_flipped():
    d = real("right")
    qb = by_id(d, "o_1001")
    # the passer stands 5 yards behind the line at x=65, y=30 -> (55, 53.33 - 30)
    assert qb["track"][0] == [0.0, 55.0, round(W - 30.0, 2)]
    assert d["field"]["los"] == 60.0  # the line at absolute x=70


def test_a_left_moving_play_maps_110_minus_x_and_y_unchanged():
    d = real("left")
    qb = by_id(d, "o_1001")
    # the offense faces -x and stands at x=75; its goal line is at the right: (110 - 75, 30)
    assert qb["track"][0] == [0.0, 35.0, 30.0]
    assert d["field"]["los"] == 40.0  # 120 - 70 - 10


@pytest.mark.parametrize("direction", ["right", "left"])
def test_the_offense_always_moves_towards_plus_x_and_stands_behind_the_line(direction):
    d = real(direction)
    los = d["field"]["los"]
    target = by_id(d, "o_1003")
    xs = [k[1] for k in target["track"]]
    assert xs == sorted(xs) and xs[-1] > xs[0] + 8  # downfield, towards where the ball lands
    for p in d["players"]:
        first = p["track"][0][1]
        assert (first < los) if p["side"] == "offense" else (first > los), p["id"]
    assert by_id(d, "o_1001")["track"][0][1] == los - 5.0  # the passer, 5 yards deep


def test_both_directions_are_mirror_images_about_the_line():
    r, left = real("right"), real("left")
    for a, b in zip(r["players"], left["players"], strict=True):
        assert a["id"] == b["id"] and len(a["track"]) == len(b["track"])
        for ka, kb in zip(a["track"], b["track"], strict=True):
            assert ka[0] == kb[0]
            assert near(ka[1] - r["field"]["los"], kb[1] - left["field"]["los"])
            assert near(ka[2] + kb[2], W)
    assert near(r["ball"][-1][1] - r["field"]["los"], left["ball"][-1][1] - left["field"]["los"])


def test_the_ball_follows_the_passer_then_flies_to_where_it_landed():
    d = real("right")
    qb = by_id(d, "o_1001")
    ball = d["ball"]
    t_rel = 1.4
    held = [b for b in ball if b[0] <= t_rel]
    assert [b[:3] for b in held] == [k for k in qb["track"] if k[0] <= t_rel]  # with the passer
    assert all(b[3] == 1.0 for b in held)
    flight = [b for b in ball if b[0] > t_rel]
    assert len(flight) == 8 and near(flight[-1][0], t_rel + BF.N_OUT / 10)
    assert flight[-1][1:3] == [72.0, round(W - BF.LAND_Y, 2)] and flight[-1][3] == 1.0
    heights = [b[3] for b in flight]
    assert max(heights) == heights[3] > 1.0  # the arc peaks mid-flight
    assert all(h >= 1.0 for h in heights) and heights[0] < heights[3] > heights[-2]
    # it travels in a straight line from the passer's hand
    hand = held[-1]
    for k, b in enumerate(flight, start=1):
        s = k / 8
        assert near(b[1], hand[1] + (72.0 - hand[1]) * s) and near(
            b[2], hand[2] + (33.33 - hand[2]) * s
        )
    assert d["duration"] == 3.2  # the last key (2.6 s) and the 0.6 s hold


@pytest.mark.parametrize("depth", [6.0, 12.0, 40.0, 70.0])
def test_the_peak_of_the_arc_grows_with_the_throw_and_is_capped(depth):
    d = real("right", land_depth=depth)
    hand = [b for b in d["ball"] if b[0] <= 1.4][-1]
    flight = [b for b in d["ball"] if b[0] > 1.4]
    dist = math.hypot(flight[-1][1] - hand[1], flight[-1][2] - hand[2])
    peak = min(6.0, 1.0 + dist / 8.0)
    assert near(max(b[3] for b in flight), 1.0 + peak, 0.02)
    assert max(b[3] for b in flight) <= 7.0 + 0.01  # capped at a six-yard arc


def test_tracking_after_the_throw_extends_the_tracked_players_only():
    d = real("right")
    for pid in (1003, 2001, 2002, 2003):
        p = by_id(d, f"{'o' if pid < 2000 else 'd'}_{pid}")
        assert len(p["track"]) == BF.N_IN + BF.N_OUT
        times = [k[0] for k in p["track"]]
        assert times[0] == 0.0 and near(times[-1], 1.4 + 1.2)
        assert all(near(b - a, 0.1) for a, b in zip(times, times[1:], strict=False))
    for pid in (1001, 1002, 1004, 1005, 2004, 2005, 2006, 2007):  # held where they were
        p = by_id(d, f"{'o' if pid < 2000 else 'd'}_{pid}")
        assert len(p["track"]) == BF.N_IN and near(p["track"][-1][0], 1.4)


def test_the_target_ends_where_the_ball_lands():
    d = real("right")
    t = by_id(d, "o_1003")
    assert t["track"][-1][1:] == [72.0, round(W - BF.LAND_Y, 2)]
    assert t["track"][-1][0] == d["ball"][-1][0]


def test_roles_labels_sides_and_flags():
    d = real("right")
    assert len(d["players"]) == 12 and sum(p["side"] == "offense" for p in d["players"]) == 5
    roles = {
        "o_1001": ("qb", "QB"), "o_1002": ("back", "RB"), "o_1003": ("receiver", "WR"),
        "o_1004": ("receiver", "WR"), "o_1005": ("receiver", "TE"),
        "d_2001": ("db", "CB"), "d_2003": ("db", "FS"), "d_2004": ("db", "SS"),
        "d_2005": ("lb", "MLB"), "d_2006": ("lb", "OLB"),
    }  # fmt: skip
    for pid, (role, label) in roles.items():
        assert (by_id(d, pid)["role"], by_id(d, pid)["label"]) == (role, label), pid
    assert [p["id"] for p in d["players"] if p["target"]] == ["o_1003"]
    assert [p["id"] for p in d["players"] if p["carrier"]] == ["o_1003"]  # he caught it
    assert not any(p["rusher"] for p in d["players"]) and not any(p["approx"] for p in d["players"])
    for p in d["players"]:
        assert set(p) == {
            "id",
            "side",
            "role",
            "label",
            "approx",
            "rusher",
            "target",
            "carrier",
            "track",
        }


def test_an_incompletion_has_no_carrier_and_an_interception_marks_the_catch():
    inc = real(play=BF.supp_row(BF.G1, 101, 1, "KC", "DET", pass_result="I", yards_gained=0))
    assert not any(p["carrier"] for p in inc["players"]) and by_id(inc, "o_1003")["target"]
    assert [m["kind"] for m in inc["marks"]] == ["throw", "incomplete"]
    assert inc["play"]["result"]["text"] == "Incomplete"
    ic = real(play=BF.supp_row(BF.G1, 101, 1, "KC", "DET", pass_result="IN", yards_gained=0))
    assert [m["kind"] for m in ic["marks"]] == ["throw", "interception"]
    assert ic["play"]["result"]["turnover"] is True
    ok = real()
    assert [m["kind"] for m in ok["marks"]] == ["throw", "end"]
    assert ok["marks"][1]["text"] == ok["play"]["result"]["text"]
    assert ok["marks"][1]["t"] == ok["ball"][-1][0] and ok["marks"][0]["t"] == 1.4


def test_an_unlisted_position_still_draws_as_a_player():
    d = real(positions={1004: "", 2001: "XYZ"})
    assert by_id(d, "o_1004")["label"] == "O" and by_id(d, "o_1004")["role"] == "receiver"
    assert (by_id(d, "d_2001")["role"], by_id(d, "d_2001")["label"]) == ("db", "XYZ")


@pytest.mark.parametrize(
    "kw",
    [{"passer": False}, {"target": False}, {"land": False}],
    ids=["no passer", "no target", "no landing spot"],
)
def test_a_play_without_a_passer_a_target_or_a_landing_spot_is_not_drawn(kw):
    assert real(**kw) is None


def test_an_empty_frame_set_is_not_drawn():
    inp, out = BF.tracking(BF.G1, 101)
    play = BF.supp_row(BF.G1, 101, 1, "KC", "DET")
    assert bdb.real_diagram(play, pl.DataFrame(inp).head(0), pl.DataFrame(out), "screen") is None


def test_the_diagram_has_the_shape_of_a_reconstruction_but_is_real():
    d = real()
    rec = D.diagram(__import__("playcall_diagram_fixtures").make_row(), [])
    # the reader adds `real_example` to a reconstruction; a library file carries it as null
    assert set(d) == set(rec) | {"real_example"} and set(d["field"]) == set(rec["field"])
    assert set(d["play"]) == set(rec["play"]) | {"game_label", "season"}
    assert d["kind"] == "real" and d["version"] == D.VERSION
    assert d["label"] == "Real play: 2023 week 1, Big Data Bowl data"
    assert d["label"] != D.LABEL and "Reconstructed" not in d["label"]
    assert (
        d["real_example"] is None and d["concept"] == "screen" and d["play"]["concept"] == "screen"
    )
    assert d["sources"] == ["NFL Big Data Bowl 2026 (Next Gen Stats tracking, 2023 season)"]
    assert [p["name"] for p in d["phases"]] == ["start", "pass_forward", "pass_arrived"]
    assert [p["t"] for p in d["phases"]] == [0.0, 1.4, 2.6]
    assert d["field"]["hash"] is None and d["field"]["width"] == 53.33
    json.dumps(d, allow_nan=False)


def test_the_play_summary_reads_like_the_reconstructions():
    d = real()
    p = d["play"]
    assert p["game_id"] == "2023_01_KC_DET" and p["game_label"] == "KC at DET, 2023 week 1"
    assert p["season"] == 2023 and p["week"] == 1 and p["play_id"] == 101
    assert (p["offense"], p["defense"]) == ("KC", "DET") and p["type"] == "pass"
    assert (p["quarter"], p["quarter_text"], p["clock"]) == (2, "Q2", "13:25")
    assert (p["down"], p["distance"], p["down_text"]) == (1, 10, "1st & 10")
    assert (p["yards_to_goal"], p["spot"]) == (40, "DET 40")
    assert p["result"]["text"] == "Complete, +8 (8 air yards)" and p["epa"] == 0.5
    assert p["explosive"] is False and p["charted"] is True
    assert p["desc"] == "1-QB pass short middle to 3-WR for 8 yards (9-Tackler)."
    assert d["field"]["first_down"] == 70.0


@pytest.mark.parametrize(
    ("ayl", "direction", "los"),
    [(70, "right", 60.0), (70, "left", 40.0), (35, "right", 25.0), (35, "left", 75.0)],
)
def test_the_line_of_scrimmage_follows_the_absolute_yard_line(ayl, direction, los):
    d = real(direction, ayl=float(ayl))
    assert d["field"]["los"] == los and d["play"]["yards_to_goal"] == round(100 - los)
    assert d["field"]["ball_y"] == d["players"][0]["track"][0][2]


def test_a_first_down_line_past_the_goal_line_is_none_and_the_down_says_goal():
    d = real("right", ayl=105.0, play=BF.supp_row(BF.G1, 101, 1, "KC", "DET", yards_to_go=5))
    assert d["field"]["los"] == 95.0 and d["field"]["first_down"] is None
    assert d["play"]["down_text"] == "1st & Goal"
    d = real("right", play=BF.supp_row(BF.G1, 101, 1, "KC", "DET", yards_to_go=None))
    assert d["field"]["first_down"] is None and d["play"]["distance"] is None


def test_the_known_lines_name_the_labels_the_data_has():
    d = real(
        play=BF.supp_row(BF.G1, 101, 1, "KC", "DET", route_of_targeted_receiver="SCREEN",
                         play_action="Y", team_coverage_type="COVER_1_MAN", pass_length=25)
    )  # fmt: skip
    known = d["known"]
    assert known[0].startswith("Where the passer, every route runner and the coverage defenders")
    assert "Coverage: Cover 1 man" in known and "The target's route: screen" in known
    assert "Formation: shotgun, receivers 2x2" in known and "Play-action" in known
    assert d["play"]["tags"] == ["play_action", "screen", "deep_shot"]
    assert d["play"]["explosive"] is False
    bare = real(
        play=BF.supp_row(BF.G1, 101, 1, "KC", "DET", route_of_targeted_receiver=None,
                         team_coverage_type=None, offense_formation=None, play_action="N")
    )  # fmt: skip
    assert len(bare["known"]) == 2 and bare["play"]["tags"] == []


@pytest.mark.parametrize(("length", "deep"), [(19, False), (19.9, False), (20, True), (45, True)])
def test_a_deep_shot_is_twenty_air_yards_or_more_in_the_tag_and_in_the_label(length, deep):
    play = BF.supp_row(BF.G1, 101, 1, "KC", "DET", pass_length=length, play_action="Y")
    d = real(play=play)
    assert ("deep_shot" in d["play"]["tags"]) is deep
    got = concepts_of(row(1, play_action="Y", pass_length=length))
    assert (1 in got) is deep


@pytest.mark.parametrize(("yards", "explosive"), [(19, False), (20, True), (35, True)])
def test_a_pass_of_twenty_yards_or_more_is_explosive(yards, explosive):
    d = real(play=BF.supp_row(BF.G1, 101, 1, "KC", "DET", yards_gained=yards))
    assert d["play"]["explosive"] is explosive


def test_what_the_edition_does_not_hold_is_said_in_unknown_and_approximate():
    d = real()
    assert any("linemen" in u for u in d["unknown"]) and any(
        "after the ball landed" in u for u in d["unknown"]
    )
    assert any("ball before the throw" in a for a in d["approximate"])
    assert any("held where they were" in a for a in d["approximate"])


@pytest.mark.parametrize(
    ("over", "kind", "text"),
    [
        ({"pass_result": "C", "yards_gained": 8}, "complete", "Complete, +8 (8 air yards)"),
        (
            {"pass_result": "C", "yards_gained": 12},
            "complete",
            "Complete, +12 (8 air yards), first down",
        ),
        ({"pass_result": "I", "yards_gained": 0}, "incomplete", "Incomplete"),
        ({"pass_result": "IN", "yards_gained": 0}, "interception", "Intercepted"),
    ],
)
def test_the_result_text(over, kind, text):
    r = real(play=BF.supp_row(BF.G1, 101, 1, "KC", "DET", **over))["play"]["result"]
    assert (r["kind"], r["text"]) == (kind, text)


def test_a_touchdown_in_the_description_is_the_offenses_unless_it_was_an_interception():
    td = BF.supp_row(
        BF.G1,
        101,
        1,
        "KC",
        "DET",
        play_description="(1:00) pass to 3-WR for 20 yards, TOUCHDOWN.",
        yards_gained=20,
    )
    assert real(play=td)["play"]["result"]["touchdown"] is True
    assert real(play=td)["play"]["explosive"] is True
    pick6 = BF.supp_row(
        BF.G1, 101, 1, "KC", "DET", pass_result="IN", yards_gained=0,
        play_description="(1:00) pass INTERCEPTED by 9-X. 9-X for 40 yards, TOUCHDOWN.",
    )  # fmt: skip
    r = real(play=pick6)["play"]["result"]
    assert r["touchdown"] is False and r["turnover"] is True


def test_team_codes_in_a_play_are_the_projects():
    play = BF.supp_row(BF.G1, 101, 1, "LAR", "WSH", possession_team="LAR", defensive_team="OAK")
    d = real(play=play)
    assert (d["play"]["offense"], d["play"]["defense"]) == ("LA", "LV")
    assert d["play"]["game_label"] == "LA at WAS, 2023 week 1"
    assert d["play"]["game_id"] == "2023_01_LA_WAS"


def test_the_camera_holds_every_player_and_the_ball():
    d = real()
    xs = [k[1] for p in d["players"] for k in p["track"]] + [b[1] for b in d["ball"]]
    lo, hi = d["field"]["view"]
    assert lo <= min(xs) - 2.9 and hi >= max(xs) + 2.9 and -10 <= lo < hi <= 110


def test_keys_are_ten_a_second_and_strictly_ascending_and_the_ball_too():
    d = real()
    series = [[k[0] for k in p["track"]] for p in d["players"]]
    series.append([b[0] for b in d["ball"]][: len([b for b in d["ball"] if b[0] <= 1.4])])
    for times in series:
        assert times[0] == 0.0
        assert all(near(b - a, 0.1) for a, b in zip(times, times[1:], strict=False))
    bt = [b[0] for b in d["ball"]]
    assert all(a < b for a, b in zip(bt, bt[1:], strict=False))


def test_the_example_label_names_week_teams_down_and_result():
    d = real()
    assert (
        bdb.example_label(d)
        == "2023 week 1 \u00b7 KC vs DET \u00b7 1st & 10 \u00b7 Complete, +8 (8 air yards)"
    )
    d["play"]["down_text"] = None
    assert bdb.example_label(d) == "2023 week 1 \u00b7 KC vs DET \u00b7 Complete, +8 (8 air yards)"


# ---- the whole build -----------------------------------------------------------------------------


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        if p.is_file()
        else "dir"
        for p in root.rglob("*")
    }


def test_build_library_writes_one_json_per_play_and_the_index_last(world, paths, monkeypatch):
    written: list[str] = []
    real_write = Path.write_text

    def spy(self, *a, **k):
        written.append(self.name)
        return real_write(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", spy)
    log: list[str] = []
    index = bdb.build_library(paths, per_concept=3, log=log.append)
    assert written[-1] == "index.json" and written.count("index.json") == 1
    assert len(written) == 1 + 7  # seven plays and the index
    lib = bdb.library_dir(paths)
    assert json.loads((lib / "index.json").read_text("utf-8")) == index
    assert set(index) == {
        "edition",
        "competition",
        "season",
        "weeks",
        "built_at",
        "note",
        "concepts",
    }
    assert (index["edition"], index["competition"], index["season"]) == (
        "2026",
        bdb.COMPETITION,
        2023,
    )
    assert index["weeks"] == [1, 2] and index["note"] == bdb.NOTE
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", index["built_at"])
    counts = {c: len(v["examples"]) for c, v in index["concepts"].items()}
    assert counts == {"screen": 1, "play_action_deep": 1, "cover_1": 1, "cover_3": 3, "2_man": 1}
    assert list(index["concepts"]) == [c for c in D.CONCEPTS if c in counts]
    assert set(index["concepts"]) <= LIBRARY_CONCEPTS  # no run, no RPO
    for concept, entry in index["concepts"].items():
        assert entry["name"] == D.CONCEPTS[concept]["name"]
        for ex in entry["examples"]:
            assert re.fullmatch(r"\d{10}_\d{1,5}\.json", ex["file"]), ex
            d = json.loads((lib / concept / ex["file"]).read_text("utf-8"))
            assert d["kind"] == "real" and d["concept"] == concept and len(d["players"]) == 12
            assert ex["label"] == bdb.example_label(d)
    assert any("library:" in s for s in log) and any("candidates 9, chosen 7" in s for s in log)
    assert not (paths.playcalling / ".bdb_library.new").exists()
    assert not (paths.playcalling / ".bdb_library.old").exists()


def test_the_example_files_hold_the_play_the_index_names(world, paths):
    index = bdb.build_library(paths, per_concept=3, log=lambda s: None)
    lib = bdb.library_dir(paths)
    for concept, entry in index["concepts"].items():
        for ex in entry["examples"]:
            gid, pid = ex["file"].removesuffix(".json").split("_")
            d = json.loads((lib / concept / ex["file"]).read_text("utf-8"))
            assert d["play"]["play_id"] == int(pid)
            week = 1 if int(gid) < BF.G3 else 2
            assert d["play"]["week"] == week and d["play"]["game_id"].startswith(
                f"2023_{week:02d}_"
            )


def test_both_directions_are_in_the_library(world, paths):
    bdb.build_library(paths, per_concept=3, log=lambda s: None)
    lib = bdb.library_dir(paths)
    left = json.loads(
        (lib / "cover_3" / f"{BF.G3}_110.json").read_text("utf-8")
    )  # left-moving, ayl 30
    assert left["field"]["los"] == 80.0  # 120 - 30 - 10
    xs = [k[1] for k in by_id(left, "o_1003")["track"]]
    assert xs == sorted(xs)
    right = json.loads((lib / "cover_3" / f"{BF.G1}_101.json").read_text("utf-8"))
    assert right["field"]["los"] == 60.0


def test_a_play_that_cannot_be_drawn_is_skipped_and_logged(world, paths):
    log: list[str] = []
    index = bdb.build_library(paths, per_concept=5, log=log.append)
    assert any("skipped (2023090700, 109) (cover_3)" in s and "no passer, target" in s for s in log)
    files = {e["file"] for e in index["concepts"]["cover_3"]["examples"]}
    assert f"{BF.G1}_109.json" not in files and len(files) == 4  # five picked, one skipped
    assert not (bdb.library_dir(paths) / "cover_3" / f"{BF.G1}_109.json").exists()


def test_the_weeks_argument_limits_the_build_and_unknown_weeks_are_ignored(world, paths):
    index = bdb.build_library(paths, weeks=[2, 9], log=lambda s: None)
    assert index["weeks"] == [2]
    ids = {e["file"] for c in index["concepts"].values() for e in c["examples"]}
    assert ids == {f"{BF.G3}_106.json", f"{BF.G3}_110.json"}


def test_a_build_with_no_tracking_week_on_disk_says_so(world, paths):
    with pytest.raises(FileNotFoundError, match="input / output tracking files"):
        bdb.build_library(paths, weeks=[7], log=lambda s: None)
    for f in bdb.raw_dir(paths).glob("output_*.csv"):
        f.unlink()  # input without output is not a week
    with pytest.raises(FileNotFoundError, match="input / output tracking files"):
        bdb.build_library(paths, log=lambda s: None)
    assert not bdb.library_dir(paths).exists()


def test_a_build_without_the_labels_says_which_file_is_missing(paths):
    with pytest.raises(
        FileNotFoundError, match=r"Big Data Bowl files missing.*supplementary_data\.csv"
    ):
        bdb.build_library(paths, log=lambda s: None)


def test_the_old_library_is_replaced_only_by_a_complete_new_one(world, paths):
    lib = bdb.library_dir(paths)
    (lib / "screen").mkdir(parents=True)
    (lib / "stale.txt").write_text("old")
    (lib / "screen" / "1111111111_1.json").write_text("{}")
    (lib / "index.json").write_text(json.dumps({"concepts": {"x": {"examples": []}}}))
    stage = paths.playcalling / ".bdb_library.new"  # left by a crashed run
    stage.mkdir()
    (stage / "junk.json").write_text("junk")
    bdb.build_library(paths, log=lambda s: None)
    assert not (lib / "stale.txt").exists() and not (lib / "screen" / "1111111111_1.json").exists()
    assert "x" not in json.loads((lib / "index.json").read_text("utf-8"))["concepts"]
    assert not stage.exists() and not (paths.playcalling / ".bdb_library.old").exists()
    # the crashed run's leftovers were cleared first, not swapped into the new library
    assert not (lib / "junk.json").exists() and not list(lib.rglob("junk*"))


def test_a_build_that_fails_midway_leaves_the_old_library_untouched(world, paths, monkeypatch):
    lib = bdb.library_dir(paths)
    lib.mkdir(parents=True)
    old = json.dumps({"concepts": {"screen": {"examples": [{"file": "2023090700_101.json"}]}}})
    (lib / "index.json").write_text(old)
    real_write = Path.write_text

    def disk_full(self, *a, **k):
        if self.name == "index.json" and self.parent.name == ".bdb_library.new":
            raise OSError("disk on fire")
        return real_write(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", disk_full)
    with pytest.raises(OSError, match="disk on fire"):
        bdb.build_library(paths, log=lambda s: None)  # the index is the last file written
    monkeypatch.setattr(Path, "write_text", real_write)
    assert (lib / "index.json").read_text() == old
    assert sorted(p.name for p in lib.iterdir()) == ["index.json"]
    bdb.build_library(paths, log=lambda s: None)  # the next run cleans up after the crash
    assert len(list(lib.rglob("*.json"))) == 9  # eight drawable plays and the index
    assert not (paths.playcalling / ".bdb_library.new").exists()


def test_a_rebuild_is_the_same_library_and_touches_nothing_else(world, paths):
    bdb.build_library(paths, log=lambda s: None)
    lib = bdb.library_dir(paths)
    raw_before = tree(bdb.raw_dir(paths))
    other = tree(paths.root)
    first = {k: v for k, v in tree(lib).items() if not k.endswith("index.json")}
    bdb.build_library(paths, log=lambda s: None)
    again = {k: v for k, v in tree(lib).items() if not k.endswith("index.json")}
    assert first == again
    assert tree(bdb.raw_dir(paths)) == raw_before  # the raw files are never changed
    after = tree(paths.root)
    changed = {k for k in set(other) | set(after) if other.get(k) != after.get(k)}
    assert all(k.startswith("playcalling/bdb_library") for k in changed), changed


def test_the_build_never_touches_the_network(world, paths, monkeypatch):
    import httpx

    def boom(*a, **k):
        raise AssertionError("the build must not make an HTTP request")

    monkeypatch.setattr(httpx, "Client", boom)
    monkeypatch.setattr(httpx, "get", boom)
    assert bdb.build_library(paths, log=lambda s: None)["concepts"]


def test_the_build_reads_only_the_chosen_plays_frames(world, paths, monkeypatch):
    seen: list[int] = []
    real_read = bdb.read_frames

    def spy(p, week, keys):
        inp, out = real_read(p, week, keys)
        seen.append(inp.select("play_id").n_unique())
        return inp, out

    monkeypatch.setattr(bdb, "read_frames", spy)
    bdb.build_library(paths, per_concept=1, log=lambda s: None)
    assert seen and max(seen) <= 5  # at most one play per concept, never the week's 12 plays


def test_the_diagram_files_are_compact_and_free_of_nan(world, paths):
    bdb.build_library(paths, log=lambda s: None)
    for f in bdb.library_dir(paths).rglob("*.json"):
        text = f.read_text("utf-8")
        assert "NaN" not in text and "Infinity" not in text
        if f.name != "index.json":
            assert "\n" not in text and '", "' not in text  # separators=(",", ":")
            d = json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(AssertionError(c)))
            assert not math.isnan(d["duration"])


# ---- pick: the games are taken week by week ------------------------------------------------------


def test_pick_interleaves_the_weeks_game_by_game():
    """Week 1's first game, week 2's first, week 3's first, then each week's second game: a
    library of ten shows the season, not the opening weeks."""
    c = cands(
        (11, 1, 1, "screen", True), (12, 2, 1, "screen", True), (13, 3, 1, "screen", True),
        (21, 4, 2, "screen", True), (22, 5, 2, "screen", True),
        (31, 6, 3, "screen", True),
    )  # fmt: skip
    # games 11 12 13 are week 1, 21 22 are week 2, 31 is week 3
    order = [11, 21, 31, 12, 22, 13]
    for cap in range(1, 7):
        got = bdb.pick(c, [1, 2, 3], cap)
        assert got["game_id"].to_list() == order[:cap], cap
    # a week not on disk is skipped over, the others keep their turn
    assert bdb.pick(c, [1, 3], 4)["game_id"].to_list() == [11, 31, 12, 13]


def test_pick_takes_one_play_per_game_per_pass_good_ones_first():
    c = cands(
        (11, 1, 1, "cover_3", False), (11, 2, 1, "cover_3", True), (11, 3, 1, "cover_3", True),
        (21, 4, 2, "cover_3", True),
        (31, 5, 3, "cover_3", False),
    )  # fmt: skip
    got = bdb.pick(c, [1, 2, 3], 5)
    # pass 1: game 11 (its good play 2), game 21 (4), game 31 (5, its only play);
    # pass 2: game 11 again (its next good play, 3), then 21 and 31 have none left
    assert got["play_id"].to_list() == [2, 4, 5, 3, 1]
    assert bdb.pick(c, [1, 2, 3], 3)["play_id"].to_list() == [2, 4, 5]


def test_pick_is_independent_of_the_row_order_and_the_other_concepts():
    rows = [
        (g, g * 10 + k, 1 + g % 3, concept, k % 2 == 0)
        for g in range(1, 9)
        for k in (1, 2)
        for concept in ("screen", "cover_1", "2_man")
    ]
    a = bdb.pick(cands(*rows), list(range(1, 4)), 4)
    b = bdb.pick(cands(*reversed(rows)), list(range(1, 4)), 4)
    key = ["concept", "game_id", "play_id"]
    assert a.sort(key).equals(b.sort(key))
    alone = bdb.pick(cands(*[r for r in rows if r[3] == "cover_1"]), [1, 2, 3], 4)
    assert (
        alone["play_id"].to_list() == a.filter(pl.col("concept") == "cover_1")["play_id"].to_list()
    )


# ---- real_diagram drops frames that are not finite numbers (Sol, PC03) ---------------------------


def frames_with(mutate, **kw):
    """The tracking of play 101 with `mutate(inp_rows, out_rows)` applied before it is read."""
    inp, out = BF.tracking(BF.G1, 101, **kw)
    mutate(inp, out)
    play = BF.supp_row(BF.G1, 101, 1, "KC", "DET")
    return bdb.real_diagram(
        play,
        pl.DataFrame(inp, infer_schema_length=None, strict=False),
        pl.DataFrame(out, infer_schema_length=None, strict=False),
        "screen",
    )


def poison(rows, nfl_id, frame, col="x", value=float("nan")):
    for r in rows:
        if r["nfl_id"] == nfl_id and r["frame_id"] == frame:
            r[col] = value


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None])
@pytest.mark.parametrize("col", ["x", "y"])
def test_a_frame_with_a_non_finite_x_or_y_is_dropped_and_the_rest_of_the_play_is_kept(bad, col):
    d = frames_with(lambda i, o: poison(i, 2002, 5, col, bad))
    d2 = frames_with(lambda i, o: None)
    p, p2 = by_id(d, "d_2002"), by_id(d2, "d_2002")
    assert len(p["track"]) == len(p2["track"]) - 1  # frame 5 is gone
    assert near(p2["track"][4][0], 0.4) and 0.4 not in [k[0] for k in p["track"]]
    assert [k for k in p["track"]] == [k for k in p2["track"] if k[0] != 0.4]
    # every other player is untouched
    for q, q2 in zip(d["players"], d2["players"], strict=True):
        if q["id"] != "d_2002":
            assert q["track"] == q2["track"]
    assert d["ball"] == d2["ball"]
    json.dumps(d, allow_nan=False)  # and nothing non-finite is left in the answer
    times = [k[0] for k in p["track"]]
    assert all(a < b for a, b in zip(times, times[1:], strict=False))


def test_a_non_numeric_coordinate_in_the_file_is_dropped_too():
    def junk(inp, out):
        for r in inp:
            r["x"] = str(r["x"])
            r["y"] = str(r["y"])
        poison(inp, 2002, 5, "x", "abc")
        poison(inp, 2003, 6, "y", "")

    d = frames_with(junk)
    d2 = frames_with(lambda i, o: None)
    assert len(by_id(d, "d_2002")["track"]) == len(by_id(d2, "d_2002")["track"]) - 1
    assert len(by_id(d, "d_2003")["track"]) == len(by_id(d2, "d_2003")["track"]) - 1
    assert by_id(d, "o_1003")["track"] == by_id(d2, "o_1003")["track"]  # numeric strings parse


def test_a_bad_frame_after_the_throw_shortens_that_players_track_only():
    d = frames_with(lambda i, o: poison(o, 1003, BF.N_OUT))  # the target's last frame
    d2 = frames_with(lambda i, o: None)
    t, t2 = by_id(d, "o_1003"), by_id(d2, "o_1003")
    assert len(t["track"]) == len(t2["track"]) - 1
    assert t["track"] == t2["track"][:-1]  # it ends at the last good frame
    assert d["ball"][-1] == d2["ball"][-1]  # the ball still lands where it landed


@pytest.mark.parametrize("nid", [BF.PASSER, BF.TARGET])
def test_a_play_that_loses_its_passer_or_its_target_is_not_drawn(nid):
    def lose(inp, out):
        for r in inp:
            if r["nfl_id"] == nid:
                r["x"] = float("nan")

    assert frames_with(lose) is None


def test_a_play_with_no_finite_frame_at_all_is_not_drawn():
    def all_bad(inp, out):
        for r in inp:
            r["y"] = float("inf")

    assert frames_with(all_bad) is None


def test_a_play_losing_a_receiver_or_defender_completely_is_still_drawn_without_him():
    def lose(inp, out):
        for r in inp:
            if r["nfl_id"] in (1004, 2005):
                r["x"] = None

    d = frames_with(lose)
    ids = {p["id"] for p in d["players"]}
    assert "o_1004" not in ids and "d_2005" not in ids and len(ids) == 10


def test_a_non_finite_landing_spot_is_not_drawn():
    def no_land(inp, out):
        for r in inp:
            r["ball_land_x"] = float("nan")

    assert frames_with(no_land) is None


def test_build_library_skips_a_poisoned_play_and_builds_the_rest(paths):
    s = BF.standard_world(bdb.raw_dir(paths))
    s.play(BF.G2, 120, 1, "BUF", "NYJ")  # a Cover-3 pass whose passer never has a finite x
    s.play(BF.G2, 121, 1, "BUF", "NYJ")
    for r in s.inp[1]:
        if r["game_id"] == BF.G2 and r["play_id"] == 120 and r["nfl_id"] == BF.PASSER:
            r["x"] = float("nan")
    s.write()
    log: list[str] = []
    index = bdb.build_library(paths, per_concept=10, log=log.append)
    files = {e["file"] for c in index["concepts"].values() for e in c["examples"]}
    assert f"{BF.G2}_120.json" not in files and f"{BF.G2}_121.json" in files
    assert any(
        f"skipped ({BF.G2}, 120) (cover_3): no passer, target or landing spot" in s for s in log
    )
    # the rest of the library was built (a play in two concepts is one file)
    assert files == {
        f"{BF.G1}_101.json", f"{BF.G1}_102.json", f"{BF.G2}_103.json", f"{BF.G2}_105.json",
        f"{BF.G2}_121.json", f"{BF.G3}_106.json", f"{BF.G3}_110.json",
    }  # fmt: skip
    assert (bdb.library_dir(paths) / "index.json").is_file()


# ---- a play whose diagram raises is skipped, never fatal (Sol, PC03) -----------------------------


def test_a_play_whose_diagram_raises_is_skipped_with_the_exceptions_type_only(
    world, paths, monkeypatch
):
    real_diag = bdb.real_diagram

    def flaky(play, inp, out, concept):
        if int(play["play_id"]) == 105:
            raise ValueError(f"secret detail in {paths.root}")
        return real_diag(play, inp, out, concept)

    monkeypatch.setattr(bdb, "real_diagram", flaky)
    log: list[str] = []
    index = bdb.build_library(paths, per_concept=3, log=log.append)
    files = {e["file"] for c in index["concepts"].values() for e in c["examples"]}
    assert f"{BF.G2}_105.json" not in files and len(files) == 4  # 101, 102, 103 and 110 are left
    skipped = [s for s in log if "skipped" in s]
    assert skipped == [f"  skipped ({BF.G2}, 105) (cover_3): ValueError"]
    assert "secret detail" not in " ".join(log) and str(paths.root) not in " ".join(log)
    assert not (bdb.library_dir(paths) / "cover_3" / f"{BF.G2}_105.json").exists()


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_an_interrupt_is_not_swallowed_and_leaves_the_old_library(world, paths, monkeypatch, exc):
    lib = bdb.library_dir(paths)
    lib.mkdir(parents=True)
    (lib / "index.json").write_text('{"concepts": {}}')

    def stop(*a, **k):
        raise exc()

    monkeypatch.setattr(bdb, "real_diagram", stop)
    with pytest.raises(exc):
        bdb.build_library(paths, log=lambda s: None)
    assert (lib / "index.json").read_text() == '{"concepts": {}}'


# ---- _install: the swap moves the old library back if the new one cannot be moved in (Sol, PC03) -


def lib_dirs(tmp_path):
    out = tmp_path / "bdb_library"
    stage = tmp_path / ".bdb_library.new"
    old = tmp_path / ".bdb_library.old"
    out.mkdir()
    (out / "a.txt").write_text("old")
    stage.mkdir()
    (stage / "b.txt").write_text("new")
    return out, stage, old


def recording(monkeypatch, fail_on=(), exc=PermissionError):
    """Wrap `bdb.replace_file`: record each (source, destination) and fail the n-th calls."""
    calls: list[tuple[str, str]] = []
    real_replace = bdb.replace_file

    def fake(src, dst):
        calls.append((Path(src).name, Path(dst).name))
        if len(calls) in fail_on:
            raise exc("locked by another process")
        return real_replace(src, dst)

    monkeypatch.setattr(bdb, "replace_file", fake)
    return calls


def test_install_moves_the_old_library_aside_and_the_new_one_in(tmp_path, monkeypatch):
    out, stage, old = lib_dirs(tmp_path)
    calls = recording(monkeypatch)
    bdb._install(stage, out)
    assert calls == [("bdb_library", ".bdb_library.old"), (".bdb_library.new", "bdb_library")]
    assert (out / "b.txt").read_text() == "new" and not (out / "a.txt").exists()
    assert not old.exists() and not stage.exists()


def test_install_with_no_library_yet_is_one_move(tmp_path, monkeypatch):
    stage = tmp_path / ".bdb_library.new"
    stage.mkdir()
    (stage / "b.txt").write_text("new")
    calls = recording(monkeypatch)
    bdb._install(stage, tmp_path / "bdb_library")
    assert calls == [(".bdb_library.new", "bdb_library")]
    assert (tmp_path / "bdb_library" / "b.txt").read_text() == "new"


def test_install_clears_an_old_folder_a_crashed_run_left(tmp_path, monkeypatch):
    out, stage, old = lib_dirs(tmp_path)
    old.mkdir()
    (old / "junk.txt").write_text("junk")
    bdb._install(stage, out)
    assert (out / "b.txt").exists() and not old.exists()


@pytest.mark.parametrize("exc", [PermissionError, OSError])
def test_install_puts_the_old_library_back_when_the_new_one_cannot_be_moved_in(
    tmp_path, monkeypatch, exc
):
    out, stage, old = lib_dirs(tmp_path)
    calls = recording(monkeypatch, fail_on=(2,), exc=exc)
    with pytest.raises(exc, match="locked"):
        bdb._install(stage, out)
    assert calls == [
        ("bdb_library", ".bdb_library.old"),
        (".bdb_library.new", "bdb_library"),  # fails
        (".bdb_library.old", "bdb_library"),  # the old one goes back
    ]
    assert (out / "a.txt").read_text() == "old" and not (out / "b.txt").exists()
    assert (stage / "b.txt").read_text() == "new"  # the stage stays for a retry
    assert not old.exists()


def test_install_leaves_everything_alone_when_the_old_library_cannot_be_moved_aside(
    tmp_path, monkeypatch
):
    out, stage, old = lib_dirs(tmp_path)
    calls = recording(monkeypatch, fail_on=(1,))
    with pytest.raises(PermissionError):
        bdb._install(stage, out)
    assert len(calls) == 1
    assert (out / "a.txt").read_text() == "old" and (stage / "b.txt").exists() and not old.exists()


def test_a_build_whose_swap_fails_keeps_the_old_library_and_the_next_build_succeeds(
    world, paths, monkeypatch
):
    lib = bdb.library_dir(paths)
    lib.mkdir(parents=True)
    old = json.dumps({"concepts": {"screen": {"examples": [{"file": "2023090700_101.json"}]}}})
    (lib / "index.json").write_text(old)
    recording(monkeypatch, fail_on=(2,))
    with pytest.raises(PermissionError):
        bdb.build_library(paths, log=lambda s: None)
    assert (lib / "index.json").read_text() == old and sorted(p.name for p in lib.iterdir()) == [
        "index.json"
    ]
    monkeypatch.undo()
    index = bdb.build_library(paths, log=lambda s: None)  # the stage left behind is cleared
    assert json.loads((lib / "index.json").read_text("utf-8")) == index
    assert not (paths.playcalling / ".bdb_library.new").exists()


# ---- the season is the label row's own: weeks 14-18 hold 2023 and 2024 games ---------------------


def test_a_real_play_carries_the_season_of_its_label_row():
    d = real(play=BF.supp_row(BF.G1, 101, 14, "KC", "DET", season=2024))
    p = d["play"]
    assert d["label"] == "Real play: 2024 week 14, Big Data Bowl data"
    assert p["season"] == 2024 and p["week"] == 14
    assert p["game_id"] == "2024_14_KC_DET" and p["game_label"] == "KC at DET, 2024 week 14"
    assert d["sources"] == ["NFL Big Data Bowl 2026 (Next Gen Stats tracking, 2024 season)"]
    assert bdb.example_label(d).startswith("2024 week 14 · KC vs DET")
    # the same week of the other season
    d23 = real(play=BF.supp_row(BF.G1, 101, 14, "KC", "DET", season=2023))
    assert d23["label"] == "Real play: 2023 week 14, Big Data Bowl data"
    assert d23["play"]["game_id"] == "2023_14_KC_DET" and d23["play"]["season"] == 2023


@pytest.mark.parametrize("season", [None, 0])
def test_a_label_row_without_a_season_falls_back_to_the_tracking_files_season(season):
    play = BF.supp_row(BF.G1, 101, 3, "KC", "DET")
    play["season"] = season
    d = real(play=play)
    assert d["play"]["season"] == 2023 and d["label"].startswith("Real play: 2023 week 3")


def test_a_library_can_hold_both_seasons(paths):
    s = BF.standard_world(bdb.raw_dir(paths))
    s.play(BF.G2, 150, 14, "BUF", "NYJ", season=2024, team_coverage_type="COVER_1_MAN")
    s.play(BF.G2, 151, 14, "BUF", "NYJ", season=2023, team_coverage_type="COVER_1_MAN")
    s.write()
    index = bdb.build_library(paths, per_concept=10, log=lambda s: None)
    lib = bdb.library_dir(paths)
    seen = {}
    for concept, entry in index["concepts"].items():
        for e in entry["examples"]:
            d = json.loads((lib / concept / e["file"]).read_text("utf-8"))
            seen[e["file"]] = d["play"]["season"]
            assert e["label"].startswith(f"{d['play']['season']} week {d['play']['week']} ")
            assert (
                d["label"]
                == f"Real play: {d['play']['season']} week {d['play']['week']}, Big Data Bowl data"
            )
    assert seen[f"{BF.G2}_150.json"] == 2024 and seen[f"{BF.G2}_151.json"] == 2023
    assert seen[f"{BF.G1}_101.json"] == 2023


# ---- only plays with tracking are candidates (Sol/lead, PC03) ------------------------------------


def test_tracked_keys_are_the_plays_in_the_weeks_input_files(world, paths):
    every = {(r["game_id"], r["play_id"]) for rows in world.inp.values() for r in rows}
    got = bdb.tracked_keys(paths, [1, 2])
    assert got.schema == {"game_id": pl.Int64, "play_id": pl.Int64}
    assert set(got.iter_rows()) == every and got.height == len(every)  # each pair once
    week1 = {(r["game_id"], r["play_id"]) for r in world.inp[1]}
    assert set(bdb.tracked_keys(paths, [1]).iter_rows()) == week1
    assert set(bdb.tracked_keys(paths, [1, 1, 1]).iter_rows()) == week1


def test_tracked_keys_skips_a_week_that_is_not_on_disk_and_is_empty_without_any(world, paths):
    week2 = {(r["game_id"], r["play_id"]) for r in world.inp[2]}
    assert set(bdb.tracked_keys(paths, [2, 7, 18]).iter_rows()) == week2
    for weeks in ([], [7], [17, 18]):
        got = bdb.tracked_keys(paths, weeks)
        assert got.is_empty() and got.schema == {"game_id": pl.Int64, "play_id": pl.Int64}


def test_a_label_play_without_tracking_is_not_a_tracked_key(paths):
    s = BF.standard_world(bdb.raw_dir(paths))
    s.play(BF.G1, 130, 1, "KC", "DET", tracked=False)
    s.write()
    assert (BF.G1, 130) not in set(bdb.tracked_keys(paths, [1, 2]).iter_rows())
    assert (BF.G1, 101) in set(bdb.tracked_keys(paths, [1, 2]).iter_rows())


def test_a_play_without_tracking_is_never_picked_and_never_costs_a_slot(paths):
    s = BF.standard_world(bdb.raw_dir(paths))
    early = 2023090100  # the first game of week 1, ahead of every other in the order
    s.play(early, 140, 1, "KC", "DET", tracked=False)  # a good Cover-3 pass, no tracking
    s.play(early, 141, 1, "KC", "DET", tracked=False, team_coverage_type="COVER_1_MAN")
    s.write()
    log: list[str] = []
    index = bdb.build_library(paths, per_concept=1, log=log.append)
    # without the filter these would take the first slot of cover_3 and cover_1 and then be skipped
    assert [e["file"] for e in index["concepts"]["cover_3"]["examples"]] == [f"{BF.G1}_101.json"]
    assert [e["file"] for e in index["concepts"]["cover_1"]["examples"]] == [f"{BF.G1}_102.json"]
    assert not any("skipped" in line for line in log)
    assert any(
        line.startswith("candidates 9,") for line in log
    )  # the 11 label candidates, 9 tracked
    for e in (e for c in index["concepts"].values() for e in c["examples"]):
        assert not e["file"].startswith(str(early))


def test_every_concept_is_filled_when_tracked_plays_exist_even_behind_untracked_ones(paths):
    s = BF.standard_world(bdb.raw_dir(paths))
    for k in range(6):  # six earlier games whose Cover-3 plays have no tracking
        s.play(2023090100 + k, 200 + k, 1, "KC", "DET", tracked=False)
    s.write()
    index = bdb.build_library(paths, per_concept=3, log=lambda s: None)
    assert len(index["concepts"]["cover_3"]["examples"]) == 3


# ---- only the passer is the QB (the lead, PC03) --------------------------------------------------


def test_only_the_passer_is_drawn_as_the_qb():
    """A QB-position player who is not the passer (Taysom Hill runs routes at TE in 163 plays)
    is a receiver; the label keeps his position."""
    d = real(positions={1005: "QB"})
    assert [p["id"] for p in d["players"] if p["role"] == "qb"] == ["o_1001"]
    hill = by_id(d, "o_1005")
    assert hill["role"] == "receiver" and hill["label"] == "QB" and hill["side"] == "offense"


def test_a_second_and_third_qb_position_player_are_still_not_qbs():
    d = real(positions={1002: "QB", 1004: "QB", 1005: "QB"})
    assert [p["id"] for p in d["players"] if p["role"] == "qb"] == ["o_1001"]
    assert {by_id(d, i)["role"] for i in ("o_1002", "o_1004", "o_1005")} == {"receiver"}


def test_a_qb_position_player_on_defense_is_a_db():
    d = real(positions={2005: "QB"})
    assert by_id(d, "d_2005")["role"] == "db"
    assert [p["id"] for p in d["players"] if p["role"] == "qb"] == ["o_1001"]


@pytest.mark.parametrize("position", ["WR", "RB", "TE", "FB", ""])
def test_the_passer_is_the_qb_whatever_position_he_is_listed_at(position):
    """A trick play: a receiver or a back throws."""
    d = real(positions={1001: position})
    assert [p["id"] for p in d["players"] if p["role"] == "qb"] == ["o_1001"]
    assert d["ball"][0][:3] == by_id(d, "o_1001")["track"][0]  # the ball starts with him


def test_every_diagram_of_the_synthetic_library_has_exactly_one_qb(world, paths):
    index = bdb.build_library(paths, log=lambda s: None)
    for concept, entry in index["concepts"].items():
        for e in entry["examples"]:
            d = json.loads((bdb.library_dir(paths) / concept / e["file"]).read_text("utf-8"))
            assert sum(p["role"] == "qb" for p in d["players"]) == 1
