"""PC03: what the rosters add to a diagram. `diagram(row, qb_ids, positions)`, where `positions`
maps a gsis id to its position group (the play browser's reader looks up `receiver_id` and
`rusher_id` in the curated players table).

- the target is drawn from the backs when he is a running back, from the tight-end spots for a
  tight end, from the wide-receiver spots for a wide receiver, else by the throw's depth;
- a run is a QB run when the rosters say QB, or the rusher threw a pass that game and isn't
  listed as a back or a receiver (a halfback pass doesn't make his runs QB runs; a tight end
  who threw, like Taysom Hill, stays a QB run);
- a wide receiver or tight end who carries (a jet sweep, an end-around), or a back when the
  backfield is empty, is a receiver: the one who went in motion, else the one nearest the lane.
"""

from __future__ import annotations

from playcall_diagram_fixtures import (
    check_diagram,
    end_of,
    make_row,
    marks,
    phase,
    phase_names,
    players,
    row_2025,
    run_row,
    start_of,
)

from nflengine.playcalling import diagrams as D

MIDDLE = 160 / 3 / 2


def near(a: float, b: float, tol: float = 0.011) -> bool:
    return abs(a - b) <= tol


def build(row, qb=(), pos=None):
    d = D.diagram(row, qb, pos)
    check_diagram(d, row)
    return d


def target(d):
    return next(p for p in d["players"] if p["target"])


def carrier(d):
    return next(p for p in d["players"] if p["carrier"])


# ---- the target ----------------------------------------------------------------------------------

THROW_RIGHT = {"pass_location": "right", "air_yards": 12.0, "yards_gained": 12.0}
KNOWN = {
    "RB": "The target was a running back (play-by-play, rosters)",
    "WR": "The target was a wide receiver (play-by-play, rosters)",
    "TE": "The target was a tight end (play-by-play, rosters)",
}


def test_a_running_back_target_is_drawn_from_the_backs_even_when_a_receiver_is_nearer():
    row = make_row(receiver_id="rb1", **THROW_RIGHT)
    plain = build(row)
    assert target(plain)["role"] == "receiver"  # nobody told: the nearest receiver
    d = build(row, pos={"rb1": "RB"})
    assert target(d)["id"] == "o_rb1" and target(d)["role"] == "back"
    assert target(d)["carrier"] is True  # he carries it on after the catch
    assert KNOWN["RB"] in d["known"]
    assert phase(d, "catch")["t"] > 0


def test_a_fullback_counts_as_a_back():
    d = build(make_row(receiver_id="fb1", **THROW_RIGHT), pos={"fb1": "FB"})
    assert target(d)["role"] == "back" and KNOWN["RB"] in d["known"]


def test_with_an_empty_backfield_a_running_back_target_is_a_receiver():
    d = build(make_row(receiver_id="rb1", backfield=0, **THROW_RIGHT), pos={"rb1": "RB"})
    assert not players(d, "back") and target(d)["role"] == "receiver"
    assert KNOWN["RB"] in d["known"] and target(d)["label"] == "RB"


def test_a_wide_receiver_target_is_never_a_tight_end_or_a_back():
    row = row_2025(receiver_id="wr1", personnel="12", backfield=1, **THROW_RIGHT)
    d = build(row, pos={"wr1": "WR"})
    t = target(d)
    assert t["role"] == "receiver" and t["label"] == "WR"
    assert KNOWN["WR"] in d["known"]
    # the wide-right spot is nearer than any tight end to a throw to the right third
    assert t["id"] == "o_r3"


def test_a_tight_end_target_is_drawn_from_the_tight_end_spots():
    row = row_2025(receiver_id="te1", personnel="12", backfield=1, **THROW_RIGHT)
    d = build(row, pos={"te1": "TE"})
    t = target(d)
    assert t["label"] == "TE" and t["role"] == "receiver"
    assert KNOWN["TE"] in d["known"]
    # the tight end on the right side of the ball (inline, nearer the throw)
    assert near(start_of(t)[2], MIDDLE + 4.55)
    # without positions the nearest of everyone is a wide receiver
    assert target(build(row))["label"] == "WR"


def test_a_tight_end_in_a_season_without_personnel_uses_every_receiver_spot():
    """2026: receivers are not told apart (no TE labels), so the pool is all of them."""
    d = build(make_row(receiver_id="te1", **THROW_RIGHT), pos={"te1": "TE"})
    assert target(d)["role"] == "receiver" and KNOWN["TE"] in d["known"]
    # who was targeted is known even where receiver types aren't: his label says so
    assert target(d)["label"] == "TE"
    assert [p["label"] for p in players(d, "receiver") if not p["target"]] == ["R"] * 3
    d = build(make_row(receiver_id="w1", **THROW_RIGHT), pos={"w1": "WR"})
    assert target(d)["label"] == "WR"


def test_a_qb_or_an_unlisted_target_falls_back_to_the_depth_rule():
    for pos in ({"x": "QB"}, {"x": "LB"}, {"other": "RB"}, {}, None):
        d = build(make_row(receiver_id="x", pass_location="middle", air_yards=1.0), pos=pos)
        # a check-down (air <= 1) may go to a back: the old rule
        assert target(d)["role"] == "back", pos
    d = build(make_row(receiver_id="x", **THROW_RIGHT), pos={"x": "QB"})
    assert "The target was a QB (play-by-play, rosters)" in d["known"]
    assert target(d)["role"] == "receiver"
    for pos in ({"x": "LB"}, {}, None):
        d = build(make_row(receiver_id="x", **THROW_RIGHT), pos=pos)
        assert not any(k.startswith("The target was") for k in d["known"])


def test_blank_positions_and_ids_are_ignored():
    d = build(make_row(receiver_id="a", **THROW_RIGHT), pos={"a": "", "": "RB", None: "TE"})
    assert not any(k.startswith("The target was") for k in d["known"])
    d = build(make_row(receiver_id=None, **THROW_RIGHT), pos={"a": "RB"})
    assert not any(k.startswith("The target was") for k in d["known"])


def test_the_target_position_does_not_change_an_incomplete_throw_or_the_ball():
    plain = build(make_row(complete=False, receiver_id="rb1", **THROW_RIGHT))
    rb = build(make_row(complete=False, receiver_id="rb1", **THROW_RIGHT), pos={"rb1": "RB"})
    assert plain["ball"] == rb["ball"] and target(rb)["role"] == "back"
    assert not any(p["carrier"] for p in rb["players"])
    assert phase_names(plain) == phase_names(rb)


# ---- who carried the ball ------------------------------------------------------------------------

RUN = {"rusher_id": "r1"}


def test_a_rusher_the_rosters_call_a_qb_is_a_qb_run_even_without_a_passing_record():
    d = build(run_row(**RUN), qb=[], pos={"r1": "QB"})
    assert carrier(d)["role"] == "qb" and "The QB carried it (play-by-play)" in d["known"]
    assert phase(d, "carry")["text"] == "The QB keeps it"
    assert not any(k.startswith("The ball carrier was") for k in d["known"])


def test_a_rusher_who_threw_that_game_is_a_qb_run_unless_listed_as_a_back_or_wide_receiver():
    base = run_row(**RUN)
    assert carrier(build(base, qb=["r1"]))["role"] == "qb"  # nobody told: the old rule
    assert carrier(build(base, qb=["r1"], pos={"r1": "QB"}))["role"] == "qb"
    # a tight end who threw (Taysom Hill) stays a QB run
    assert carrier(build(base, qb=["r1"], pos={"r1": "TE"}))["role"] == "qb"
    assert carrier(build(base, qb=["r1"], pos={"r1": "LB"}))["role"] == "qb"
    # a running back's halfback pass doesn't make his runs QB runs
    d = build(base, qb=["r1"], pos={"r1": "RB"})
    assert carrier(d)["role"] == "back" and "The QB carried it (play-by-play)" not in d["known"]
    assert KNOWN_CARRIER["RB"] in d["known"]
    # nor does a receiver's pass
    d = build(base, qb=["r1"], pos={"r1": "WR"})
    assert carrier(d)["role"] == "receiver"
    assert carrier(build(base, qb=["r1"], pos={"r1": "FB"}))["role"] == "back"


KNOWN_CARRIER = {
    "RB": "The ball carrier was a running back (play-by-play, rosters)",
    "WR": "The ball carrier was a wide receiver (play-by-play, rosters)",
    "TE": "The ball carrier was a tight end (play-by-play, rosters)",
}


def test_a_running_back_carries_from_the_backfield_and_the_line_says_so():
    d = build(run_row(**RUN), pos={"r1": "RB"})
    assert carrier(d)["id"] == "o_rb1" and KNOWN_CARRIER["RB"] in d["known"]
    assert marks(d, "handoff")


def test_a_wide_receiver_who_carries_is_the_receiver_nearest_the_lane():
    """An end-around: a receiver takes the handoff, not the back."""
    row = run_row(rusher_id="wr1", run_location="right", run_gap="end", yards_gained=8.0)
    d = build(row, pos={"wr1": "WR"})
    c = carrier(d)
    assert c["role"] == "receiver"
    assert not players(d, "back")[0]["carrier"]
    # the lane is the right end: ball y + 5.5 = 32.17; the nearest receiver is the slot at 38.33
    assert near(c["track"][0][2], 160 / 3 - D.SLOT_Y)
    assert KNOWN_CARRIER["WR"] in d["known"]
    assert marks(d, "handoff") and phase(d, "handoff")["text"] == "Handoff"
    # the carrier takes the ball at the mesh, then the lane, then the end of the play
    assert near(end_of(c)[1], 25.0 + 8.0) and near(d["ball"][-1][1], end_of(c)[1])


def test_a_tight_end_who_carries_is_a_tight_end_when_the_personnel_has_them():
    row = run_row(
        rusher_id="te1", part_charted=True, personnel="12", extra_ol=False, season=2025,
        run_location="left", run_gap="tackle",
    )  # fmt: skip
    d = build(row, pos={"te1": "TE"})
    c = carrier(d)
    assert c["role"] == "receiver" and KNOWN_CARRIER["TE"] in d["known"]
    assert [p["label"] for p in players(d, "receiver")][:2] == ["TE", "TE"]


def test_the_man_in_motion_is_the_receiver_who_carries_and_his_keys_start_after_the_motion():
    row = run_row(rusher_id="wr1", motion=True, run_location="left", run_gap="guard")
    d = build(row, pos={"wr1": "WR"})
    c = carrier(d)
    mover = marks(d, "motion")[0]
    # he is the receiver the motion mark follows
    assert [
        near(c["track"][0][1], mover["points"][0][0]),
        near(c["track"][0][2], mover["points"][0][1]),
    ] == [
        True,
        True,
    ]
    times = [k[0] for k in c["track"]]
    assert times[:3] == [0.0, 0.15, 0.7]
    # at the snap he is where the motion ended
    snap = next(k for k in c["track"] if near(k[0], 0.8))
    assert near(snap[1], mover["points"][1][0]) and near(snap[2], mover["points"][1][1])


def test_a_back_who_lined_up_wide_carries_as_a_receiver_when_the_backfield_is_empty():
    row = run_row(rusher_id="rb1", backfield=0, run_location="left", run_gap="guard")
    d = build(row, pos={"rb1": "RB"})
    assert not players(d, "back")
    c = carrier(d)
    assert c["role"] == "receiver" and KNOWN_CARRIER["RB"] in d["known"]
    assert marks(d, "handoff")  # the QB hands it off rather than keeping it
    # unknown position + empty backfield: the QB keeps it, as before
    d = build(row)
    assert carrier(d)["role"] == "qb"


def test_a_qb_run_is_never_a_receivers_carry():
    row = run_row(rusher_id="r1", run_location="middle", run_gap=None)
    d = build(row, qb=["r1"], pos={"r1": "QB"})
    assert carrier(d)["role"] == "qb" and not marks(d, "handoff")
    assert not any(k.startswith("The ball carrier was") for k in d["known"])


def test_other_positions_leave_the_old_behaviour_alone():
    base = run_row(**RUN)
    plain = D.diagram(base)
    for pos in ({"r1": "LB"}, {"r1": "OL"}, {"other": "WR"}, {}, None):
        d = build(base, pos=pos)
        assert carrier(d)["id"] == "o_rb1"
        assert not any(k.startswith("The ball carrier was") for k in d["known"])
        assert d["ball"] == plain["ball"]


def test_positions_never_touch_a_run_without_a_rusher_id():
    d = build(run_row(rusher_id=None), pos={"r1": "WR"})
    assert carrier(d)["id"] == "o_rb1"
