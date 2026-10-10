"""LD00 decision engine, on stub models (a smooth logistic wp, a fixed gain distribution, a
net-40 punt, a single-spot kickoff). The numbers are exact: each test works the expected value
out by hand from `live_stubs.stub_wp_value`, so a rules bug shows up as a wrong number."""

from __future__ import annotations

import json

import numpy as np
import pytest
from live_stubs import (
    engine,
    make_models,
    one_hot,
    random_state,
    simple_punt,
    stub_fg,
    stub_wp_value,
)

from nflengine.live import schema
from nflengine.live.decide import Decision, Engine, ThirdDown, compress, kneel_out, wp_row
from nflengine.live.models import FGModel, NextPossession
from nflengine.live.schema import GameState

APPROX = dict(abs=1e-9)
AGREE = {0: 0.5, 5: 0.5}
NO_FG = FGModel(coef=[0.0] * 12, intercept=-60.0, wind_fill=8.0)  # P(make) ~ 1e-26


def S(**kw) -> GameState:
    """A 4th & 3 at the opponent's 45, tied, Q3 with 25:00 left, unless overridden."""
    base = dict(season=2025, score_diff=0, game_seconds=1500, half_seconds=1500, down=4,
                ydstogo=3, yardline_100=45)  # fmt: skip
    return GameState(**{**base, **kw})


def v(diff, gs, yl) -> float:
    return stub_wp_value(diff, gs, yl)


# --- the plain mechanics ----------------------------------------------------------------------
def test_go_fg_and_punt_values_worked_out_by_hand() -> None:
    m = make_models()  # gain: 50% +0, 50% +5; punt: net 40; kickoff: the receiver at his own 25
    with engine(m) as eng:
        d = eng.fourth_down(S(), bootstrap=False)
    gs = 1494  # one play takes 6 s
    p_make = stub_fg().predict([63.0], [4], [0])[0]
    go = 0.5 * v(0, gs, 40) + 0.5 * (
        1 - v(0, gs, 55)
    )  # +5: a first down at 40; +0: their ball at 55
    punt = 1 - v(0, gs, 80)  # a touchback-range punt from the 45: their ball at their own 20
    make = 1 - v(-3, gs, 75)  # +3, then the kickoff to them at yards-to-goal 75
    miss = 1 - v(0, gs, 47)  # a miss from the 45 (63-yard try): their ball at the spot, 47 to go
    assert d.wp["go"] == pytest.approx(go, **APPROX)
    assert d.wp["punt"] == pytest.approx(punt, **APPROX)
    assert d.wp["fg"] == pytest.approx(p_make * make + (1 - p_make) * miss, **APPROX)
    assert d.best == max(d.wp, key=d.wp.get)
    ranked = sorted(d.wp.values(), reverse=True)
    assert d.gap == pytest.approx(ranked[0] - ranked[1], **APPROX)
    assert d.convert == pytest.approx(0.5) and d.fg_make == pytest.approx(p_make)
    assert d.fg_distance == 63.0 and d.punt_start == pytest.approx(20.0)
    assert d.wp_now == pytest.approx(v(0, 1500, 45), **APPROX)
    assert d.ms >= 0 and d.state == S()


def test_fourth_down_needs_a_fourth_down_and_third_down_a_third() -> None:
    with engine(make_models()) as eng:
        with pytest.raises(ValueError, match="4th-down"):
            eng.fourth_down(S(down=3))
        with pytest.raises(ValueError, match="3rd-down"):
            eng.third_down(S(down=4))


def test_fourth_downs_matches_one_at_a_time() -> None:
    states = [
        S(),
        S(ydstogo=1, yardline_100=30),
        S(score_diff=-7, game_seconds=300, half_seconds=300),
    ]
    with engine(make_models()) as eng:
        many = eng.fourth_downs(states)
        each = [eng.fourth_down(s, bootstrap=False) for s in states]
    for a, b in zip(many, each, strict=True):
        assert a.wp == pytest.approx(b.wp, **APPROX) and a.best == b.best


# --- (a) a field goal on the last play ---------------------------------------------------------
def test_field_goal_on_the_last_play_is_exactly_the_make_probability() -> None:
    s = S(score_diff=-2, game_seconds=5, half_seconds=5, ydstogo=5, yardline_100=22)
    with engine(make_models()) as eng:
        d = eng.fourth_down(s, bootstrap=False)
    p = stub_fg().predict([40.0], [4], [0])[0]
    assert d.fg_make == pytest.approx(p) and 0.5 < p < 1.0
    assert d.wp["fg"] == pytest.approx(p, abs=1e-12), "down 2 with 5 s left: kick WP = P(make)"
    assert d.wp["go"] == 0.0 and d.wp["punt"] == 0.0, "the clock ends the game: no way to win"
    assert d.best == "fg"


def test_last_play_field_goal_when_tied_and_when_down_three() -> None:
    with engine(make_models()) as eng:
        tied = eng.fourth_down(S(score_diff=0, game_seconds=5, half_seconds=5, yardline_100=22))
        down3 = eng.fourth_down(S(score_diff=-3, game_seconds=5, half_seconds=5, yardline_100=22))
    p = stub_fg().predict([40.0], [4], [0])[0]
    assert tied.wp["fg"] == pytest.approx(p + (1 - p) * 0.5), "make wins; a miss leaves a tie"
    assert down3.wp["fg"] == pytest.approx(p * 0.5), "make ties (0.5); a miss loses"
    assert tied.wp["punt"] == pytest.approx(0.5), "the game ends tied after the punt"


# --- (b) going for it when only a touchdown wins -----------------------------------------------
def test_go_on_the_last_play_counts_only_the_touchdown() -> None:
    xp = make_models().pat_rates(2025)["xp"]
    with engine(make_models()) as eng:
        down2 = eng.fourth_down(S(score_diff=-2, game_seconds=5, half_seconds=5, ydstogo=5,
                                  yardline_100=5))  # fmt: skip
        down7 = eng.fourth_down(S(score_diff=-7, game_seconds=5, half_seconds=5, ydstogo=5,
                                  yardline_100=5))  # fmt: skip
        down8 = eng.fourth_down(S(score_diff=-8, game_seconds=5, half_seconds=5, ydstogo=5,
                                  yardline_100=5))  # fmt: skip
    # the stub gains +5 half the time = a touchdown from the 5; a stop ends the game
    assert down2.wp["go"] == pytest.approx(0.5, abs=1e-12), "TD + any try wins when down 2"
    # down 7: only the extra point ties (0.5); a missed one leaves the offense a point short
    assert down7.wp["go"] == pytest.approx(0.5 * xp * 0.5, abs=1e-12)
    assert down8.wp["go"] == pytest.approx(0.0, abs=1e-12), "down 8: a lone touchdown can't win"
    assert down2.wp["fg"] > down2.wp["go"], "from the 5 the 23-yard kick is the better try"


# --- (c) halftime ------------------------------------------------------------------------------
def first_half_end(**kw) -> GameState:
    """4th & 3 at the opponent's 45 with 5 s left in the first half (game 1805 = half 5 + 1800)."""
    return S(game_seconds=1805, half_seconds=5, **kw)


def test_halftime_hands_the_second_half_kickoff_to_the_right_team() -> None:
    m = make_models()  # kickoff: the receiver at yards-to-goal 75
    with engine(m) as eng:
        mine = eng.fourth_down(first_half_end(score_diff=3, receive_2h_ko=1), bootstrap=False)
        theirs = eng.fourth_down(first_half_end(score_diff=3, receive_2h_ko=0), bootstrap=False)
    got = v(3, 1800, 75)  # the offense receives, ball at its own 25, up 3, 30:00 left
    gave = 1 - v(-3, 1800, 75)  # the defense receives
    assert mine.wp["punt"] == pytest.approx(got, **APPROX), "the offense gets the 2nd-half kick"
    assert theirs.wp["punt"] == pytest.approx(gave, **APPROX)
    assert mine.wp["punt"] > theirs.wp["punt"], "receiving the kick must be worth more"
    # nothing about the play matters when the half ends: stopped or converted, punted or kicked
    assert mine.wp["go"] == pytest.approx(got, **APPROX)
    assert theirs.wp["go"] == pytest.approx(gave, **APPROX)


def test_halftime_after_a_made_field_goal() -> None:
    s = first_half_end(score_diff=3, receive_2h_ko=0, yardline_100=20)
    with engine(make_models()) as eng:
        d = eng.fourth_down(s, bootstrap=False)
    p = stub_fg().predict([38.0], [4], [0])[0]
    make = 1 - v(-6, 1800, 75)  # +3 then the half ends: the other team receives
    miss = 1 - v(-3, 1800, 75)
    assert d.wp["fg"] == pytest.approx(p * make + (1 - p) * miss, **APPROX)
    flipped = first_half_end(score_diff=3, receive_2h_ko=1, yardline_100=20)
    with engine(make_models()) as eng:
        f = eng.fourth_down(flipped, bootstrap=False)
    assert f.wp["fg"] == pytest.approx(p * v(6, 1800, 75) + (1 - p) * v(3, 1800, 75), **APPROX)
    assert f.wp["fg"] > d.wp["fg"]


def test_second_half_start_is_not_the_first_half() -> None:
    """game 1800 / half 1800 is the second half's opening snap: no halftime logic, clock runs."""
    s = S(game_seconds=1800, half_seconds=1800)
    assert s.first_half is False
    with engine(make_models()) as eng:
        d = eng.fourth_down(s, bootstrap=False)
    assert d.wp["punt"] == pytest.approx(1 - v(0, 1794, 80), **APPROX)


# --- (d) kneel-outs ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("def_timeouts", "gs", "expected"),
    [(0, 60, 1.0), (0, 126, 1.0), (0, 127, None), (1, 86, 1.0), (1, 87, None), (2, 46, 1.0),
     (2, 47, None), (3, 6, 1.0), (3, 7, None), (3, 60, None)],
)  # fmt: skip
def test_kneel_out_clock_by_timeouts(def_timeouts: int, gs: int, expected) -> None:
    s0 = S(def_timeouts=def_timeouts, game_seconds=1500, half_seconds=1500)
    assert kneel_out(s0, "o", 3, gs) == expected, f"{def_timeouts} timeouts, {gs} s"


def test_kneel_out_rules() -> None:
    s0 = S(def_timeouts=0, off_timeouts=0)
    assert kneel_out(s0, "o", 3, 60) == 1.0, "leading with a first down, 1:00, no timeouts to stop"
    assert kneel_out(s0, "o", 0, 60) is None, "tied: no kneel-out"
    assert kneel_out(s0, "o", -3, 60) is None, "trailing: no kneel-out"
    assert kneel_out(s0, "d", -3, 60) == 0.0, "the defense leads: the offense's WP is 0"
    assert kneel_out(s0, "d", 3, 60) is None
    assert kneel_out(S(off_timeouts=3, def_timeouts=0), "d", -3, 60) is None, (
        "the leading defense is stopped by the OFFENSE's timeouts"
    )
    assert kneel_out(s0, "o", 3, 1900) is None, "never in the first half"
    assert kneel_out(s0, "o", 3, 1800) is None, "not at the half either (1800 > 126)"
    ot = S(ot=True, game_seconds=60, half_seconds=60, def_timeouts=0)
    assert kneel_out(ot, "o", 3, 60) is None, "never in overtime"


def test_kneel_out_inside_a_decision() -> None:
    """Converting while ahead with 1:34 left and the other team out of timeouts ends the game."""
    lead = dict(score_diff=3, game_seconds=100, half_seconds=100, ydstogo=1, yardline_100=50)
    stop = 1 - v(-3, 94, 50)  # stopped: their ball at 50, they trail by 3
    with engine(make_models()) as eng:
        none_left = eng.fourth_down(S(**lead, def_timeouts=0), bootstrap=False)
        some = eng.fourth_down(S(**lead, def_timeouts=3), bootstrap=False)
    assert none_left.wp["go"] == pytest.approx(0.5 * 1.0 + 0.5 * stop, **APPROX), (
        "converting = a sure win (kneel-out)"
    )
    assert some.wp["go"] == pytest.approx(0.5 * v(3, 94, 45) + 0.5 * stop, **APPROX)
    assert none_left.wp["go"] > some.wp["go"]


# --- (e) the field goal miss spot --------------------------------------------------------------
@pytest.mark.parametrize(
    ("yl", "their_yards_to_goal"),
    [(30, 62), (10, 80), (12, 80), (13, 79), (40, 52), (52, 40), (1, 80)],
)
def test_field_goal_miss_is_their_ball_at_the_spot_of_the_kick_or_their_20(
    yl: int, their_yards_to_goal: int
) -> None:
    """A miss from the 30 puts them at their own 38 (62 to go); inside the 12 it is their 20."""
    m = make_models(fg=NO_FG)
    with engine(m) as eng:
        d = eng.fourth_down(S(ydstogo=1, yardline_100=yl), bootstrap=False)
    want = 1 - v(0, 1494, their_yards_to_goal)
    assert d.wp["fg"] == pytest.approx(want, abs=1e-9), (
        f"miss from yards-to-goal {yl}: their ball at {their_yards_to_goal} to go"
    )


# --- (f) a safety ------------------------------------------------------------------------------
def test_stopped_in_the_own_end_zone_is_a_safety() -> None:
    s = S(ydstogo=10, yardline_100=95)  # 4th & 10 at the own 5
    with engine(make_models(gain={-7: 0.5, 0: 0.5})) as eng:
        d = eng.fourth_down(s, bootstrap=False)
    safety = 1 - v(2, 1494, 60)  # down 2 in the exchange; they take over around their own 40
    stop = 1 - v(0, 1494, 5)  # stopped at the line: their ball at the 5
    assert d.wp["go"] == pytest.approx(0.5 * safety + 0.5 * stop, **APPROX)


@pytest.mark.parametrize(("loss", "is_safety"), [(-5, True), (-4, False), (-10, True)])
def test_safety_boundary(loss: int, is_safety: bool) -> None:
    """A loss of 5 from the own 5 reaches the goal line (yards to goal 100): a safety."""
    s = S(ydstogo=10, yardline_100=95)
    with engine(make_models(gain={loss: 1.0})) as eng:
        d = eng.fourth_down(s, bootstrap=False)
    end = 95 - loss  # where the ball ends, yards to goal
    want = 1 - v(2, 1494, 60) if is_safety else 1 - v(0, 1494, 100 - end)
    assert d.wp["go"] == pytest.approx(want, **APPROX)


# --- (g) the field goal is only offered up to 70 yards -----------------------------------------
@pytest.mark.parametrize(("yl", "offered"), [(52, True), (53, False), (99, False), (1, True)])
def test_field_goal_offered_up_to_70_yards(yl: int, offered: bool) -> None:
    with engine(make_models()) as eng:
        d = eng.fourth_down(S(ydstogo=1, yardline_100=yl), bootstrap=False)
    assert d.fg_distance == yl + 18
    if offered:
        assert d.wp["fg"] is not None and d.fg_make is not None and d.fg_make > 0
    else:
        assert d.wp["fg"] is None and d.fg_make is None, f"{yl + 18} yards is too long to offer"
        assert d.best in ("go", "punt")


# --- (h) the 3rd-down table --------------------------------------------------------------------
def test_third_down_summary_numbers() -> None:
    with engine(make_models()) as eng:
        t5 = eng.third_down(S(down=3, ydstogo=5), bootstrap=False)
        t6 = eng.third_down(S(down=3, ydstogo=6), bootstrap=False)
    assert t5.convert == pytest.approx(0.5), "the stub gains +5 half the time"
    assert t6.convert == pytest.approx(0.0), "no stub gain reaches 6"
    assert t5.pass_prob == pytest.approx(0.55)
    assert t5.wp_now == pytest.approx(v(0, 1500, 45), **APPROX)
    assert isinstance(t5, ThirdDown) and t5.note is None and t5.ms >= 0


def test_third_down_table_has_a_row_per_gain_short_of_the_line() -> None:
    s = S(down=3, ydstogo=6, yardline_100=45)
    with engine(make_models()) as eng:
        t = eng.third_down(s, bootstrap=False)
        assert [r.gain for r in t.table] == list(range(-10, 6)), "gains -10 ... distance-1"
        for r in t.table:
            assert r.ydstogo == 6 - r.gain, "4th & (distance - gain)"
            assert r.yardline_100 == 45 - r.gain, "at the yard line the gain leaves"
            # the same call as a fresh 4th down at that spot, 6 s later
            ref = eng.fourth_down(
                s.replace(down=4, ydstogo=r.ydstogo, yardline_100=r.yardline_100,
                          game_seconds=1494, half_seconds=1494),
                bootstrap=False,
            )  # fmt: skip
            assert r.wp == pytest.approx(ref.wp, **APPROX) and r.best == ref.best
            assert r.gap == pytest.approx(ref.gap, **APPROX)


def test_third_down_table_drops_gains_that_leave_the_field() -> None:
    s = S(down=3, ydstogo=5, yardline_100=95)  # at the own 5: a 5-yard loss is a safety
    with engine(make_models()) as eng:
        t = eng.third_down(s, bootstrap=False)
    assert [r.gain for r in t.table] == list(range(-4, 5))
    assert t.table[0].yardline_100 == 99 and t.table[0].ydstogo == 9
    assert all(1 <= r.yardline_100 <= 99 for r in t.table)


@pytest.mark.parametrize(
    "kw",
    [
        dict(game_seconds=5, half_seconds=5),  # the game ends on this play
        dict(game_seconds=6, half_seconds=6),
        dict(game_seconds=1805, half_seconds=5),  # the half ends on this play
        dict(ot=True, game_seconds=4, half_seconds=4),  # overtime ends
    ],
    ids=["game-ends", "exactly-6s", "half-ends", "overtime-ends"],
)
def test_third_down_with_no_time_left_has_an_empty_table_and_a_note(kw: dict) -> None:
    with engine(make_models()) as eng:
        t = eng.third_down(S(down=3, **kw), bootstrap=False)
    assert t.table == [] and t.note and "clock runs out" in t.note
    assert 0.0 < t.convert <= 1.0 or t.convert == 0.0, "the summary numbers are still given"


def test_third_down_with_a_little_time_still_plans() -> None:
    with engine(make_models()) as eng:
        t = eng.third_down(S(down=3, ydstogo=3, game_seconds=7, half_seconds=7), bootstrap=False)
    assert t.note is None, "7 s left: a 4th down would follow with 1 s"
    assert [r.gain for r in t.table] == list(range(-10, 3))


# --- (i) labels and the bootstrap gate ---------------------------------------------------------
WIDE = dict(ydstogo=15, yardline_100=80)  # 4th & 15 at the own 20: punt, by a wide margin


@pytest.mark.parametrize(
    ("gap", "share", "label"),
    [(0.005, 0.99, "Toss-up"), (0.0099, 1.0, "Toss-up"), (0.01, 1.0, "Confident"),
     (0.02, 0.95, "Confident"), (0.02, 0.90, "Confident"), (0.02, 0.89, "Lean"),
     (0.02, 0.60, "Lean"), (0.02, 0.59, "Toss-up"), (0.02, 0.0, "Toss-up"), (0.02, None, None),
     (0.005, None, "Toss-up")],
)  # fmt: skip
def test_label_rules(gap: float, share: float | None, label: str | None) -> None:
    with engine(make_models()) as eng:
        assert eng.label(gap, share) == label


def test_a_gap_under_one_point_is_a_toss_up() -> None:
    """Stopping at the line puts them at the same spot a net-0 punt does: go == punt exactly."""
    m = make_models(gain={0: 1.0}, punt=simple_punt(net=0))
    with engine(m) as eng:
        d = eng.fourth_down(S(ydstogo=3, yardline_100=60), bootstrap=False)
    assert d.gap == pytest.approx(0.0, abs=1e-9) and d.label == "Toss-up"
    assert d.wp["go"] == pytest.approx(d.wp["punt"], **APPROX)


def test_wide_call_precondition() -> None:
    with engine(make_models()) as eng:
        d = eng.fourth_down(S(**WIDE), bootstrap=False)
    assert d.best == "punt" and d.gap > 0.05, "the gate tests below need a call wider than 5 points"
    assert d.wp["fg"] is None
    assert d.label is None, "no bootstrap run: a label only when the gap rule decides"


def test_agreeing_bootstraps_make_a_call_confident() -> None:
    main = {0: 0.5, 5: 0.5}
    with engine(make_models(boots=[main] * 6)) as eng:
        d = eng.fourth_down(S(**WIDE), bootstrap="all")
    assert d.boot_share == 1.0 and d.label == "Confident"


def test_the_gate_skips_calls_wider_than_boot_gap() -> None:
    """D113: a call wider than `boot_gap` is not bootstrapped (Confident), `"all"` scores it."""
    flipping = [{20: 1.0}] * 5  # every refit says "go" converts (and wins)
    with engine(make_models(boots=flipping)) as eng:
        gated = eng.fourth_down(S(**WIDE), bootstrap=True)
        scored = eng.fourth_down(S(**WIDE), bootstrap="all")
        off = eng.fourth_down(S(**WIDE), bootstrap=False)
    assert gated.gap > eng.cfg["boot_gap"]
    assert gated.boot_share is None and gated.label == "Confident", "wide call: gate says Confident"
    assert scored.boot_share == 0.0 and scored.label == "Toss-up", "scored: every refit disagrees"
    assert off.boot_share is None and off.label is None
    assert gated.best == scored.best == off.best == "punt", "the bootstrap never changes the call"


def test_a_widened_gate_bootstraps_the_call() -> None:
    with engine(make_models(boots=[{0: 0.5, 5: 0.5}] * 4), {"boot_gap": 1.0}) as eng:
        d = eng.fourth_down(S(**WIDE), bootstrap=True)
    assert d.boot_share == 1.0 and d.label == "Confident"


def test_lean_between_sixty_and_ninety_percent() -> None:
    agree, disagree = {0: 0.5, 5: 0.5}, {20: 1.0}
    with engine(make_models(boots=[agree] * 7 + [disagree] * 3)) as eng:
        lean = eng.fourth_down(S(**WIDE), bootstrap="all")
    with engine(make_models(boots=[agree] * 5 + [disagree] * 5)) as eng:
        toss = eng.fourth_down(S(**WIDE), bootstrap="all")
    assert lean.boot_share == pytest.approx(0.7) and lean.label == "Lean"
    assert toss.boot_share == pytest.approx(0.5) and toss.label == "Toss-up"


def test_no_bootstrap_models_means_no_confidence_label() -> None:
    with engine(make_models()) as eng:
        d = eng.fourth_down(S(**WIDE), bootstrap=True)
    assert d.boot_share is None and d.label is None


def test_toss_up_gap_comes_from_the_config() -> None:
    cfg = {"toss_up_gap": 0.5}
    with engine(make_models(boots=[{0: 0.5, 5: 0.5}] * 3), cfg) as eng:
        d = eng.fourth_down(S(**WIDE), bootstrap="all")
    assert d.gap < 0.5 and d.label == "Toss-up", "a gap under the configured toss-up gap"


def test_bootstrap_fg_refits_must_share_the_wind_fill() -> None:
    m = make_models(boots=[{0: 1.0}])
    m.fg_boot = [FGModel(coef=list(stub_fg().coef), intercept=5.0, wind_fill=9.0)]
    with pytest.raises(ValueError, match="wind_fill"):
        Engine(m)


@pytest.mark.parametrize(("gain_n", "fg_n"), [(3, 0), (0, 3), (3, 2), (2, 3)])
def test_bootstrap_refit_counts_must_match(gain_n: int, fg_n: int) -> None:
    """A gain refit without its fg partner (or the reverse) is refused up front, not with an
    AttributeError or a zip error in the middle of a call."""
    m = make_models(boots=[AGREE] * gain_n)
    m.fg_boot = [m.fg] * fg_n
    with pytest.raises(ValueError, match=rf"{gain_n} gain and {fg_n} fg"):
        Engine(m)


@pytest.mark.parametrize("n", [0, 1, 4])
def test_equal_bootstrap_counts_are_accepted(n: int) -> None:
    Engine(make_models(boots=[AGREE] * n)).close()


# --- (j) compress ------------------------------------------------------------------------------
def make_next(seed: int, n_recv: int = 60) -> NextPossession:
    rng = np.random.default_rng(seed)
    recv = np.zeros(99)
    spots = rng.choice(np.arange(5, 95), n_recv, replace=False)
    recv[spots - 1] = rng.dirichlet(np.ones(n_recv)) * 0.93
    keep = np.zeros(99)
    keep[[44, 59, 64, 69, 79]] = rng.dirichlet(np.ones(5)) * 0.05
    return NextPossession(recv, 0.02, keep)


@pytest.mark.parametrize(("seed", "k"), [(0, 12), (1, 12), (2, 5), (3, 1), (4, 30)])
def test_compress_keeps_total_probability_and_at_most_k_spots(seed: int, k: int) -> None:
    np_ = make_next(seed)
    out = compress(np_, k)
    assert sum(p for _, _, p in out) == pytest.approx(1.0, abs=1e-9), "probability is conserved"
    recv = [(s, p) for o, s, p in out if o == "recv"]
    keep = [(s, p) for o, s, p in out if o == "keep"]
    assert 1 <= len(recv) <= k, f"{len(recv)} receiving spots for k={k}"
    assert 1 <= len(keep) <= 3 and sum(1 for o, _, _ in out if o == "ret_td") == 1
    assert all(1 <= s <= 99 for s, _ in recv + keep)
    sup = np_.support()
    for kind, got in (("recv", recv), ("keep", keep)):
        before = [(s, p) for o, s, p in sup if o == kind]
        mean0 = sum(s * p for s, p in before) / sum(p for _, p in before)
        mean1 = sum(s * p for s, p in got) / sum(p for _, p in got)
        assert abs(mean0 - mean1) <= 0.5, (
            f"{kind}: compression moved the mean spot {mean0}->{mean1}"
        )
        assert sum(p for _, p in got) == pytest.approx(sum(p for _, p in before))


def test_compress_a_single_spot_and_a_short_support() -> None:
    one = NextPossession(one_hot(75), 0.0, np.zeros(99))
    assert compress(one, 12) == [("recv", 75, pytest.approx(1.0))]
    few = NextPossession(one_hot(70, 0.5) + one_hot(80, 0.5), 0.0, np.zeros(99))
    out = compress(few, 12)
    assert sum(p for _, _, p in out) == pytest.approx(1.0) and len(out) <= 12
    assert {s for _, s, _ in out} <= {70, 75, 80}


# --- (k) serialisation and a fuzz over random states ---------------------------------------------
def test_decisions_are_json_serialisable() -> None:
    m = make_models(boots=[{0: 0.5, 5: 0.5}] * 3)
    with engine(m) as eng:
        d = eng.fourth_down(S(), bootstrap="all")
        t = eng.third_down(S(down=3, ydstogo=6), bootstrap=True)
    out = json.loads(json.dumps(d.as_dict()))
    assert out["best"] == d.best and out["state"]["season"] == 2025
    assert set(out) >= {"state", "wp", "best", "gap", "label", "boot_share", "convert", "wp_now"}
    assert out["wp"]["go"] == pytest.approx(d.wp["go"])
    tt = json.loads(json.dumps(t.as_dict()))
    assert len(tt["table"]) == 16 and tt["table"][0]["gain"] == -10
    assert set(tt["table"][0]) == {"gain", "ydstogo", "yardline_100", "best", "gap", "label", "wp"}
    assert GameState.from_dict(out["state"]) == S(), "the state in the output reloads"
    assert isinstance(d, Decision) and tt["note"] is None


def _four(s: GameState) -> GameState:
    return s.replace(down=4)


def test_random_states_give_sane_finite_decisions() -> None:
    rng = np.random.default_rng(21)
    m = make_models(boots=[{0: 0.5, 5: 0.5}, {0: 0.4, 6: 0.6}])
    with engine(m) as eng:
        for _ in range(120):
            s = random_state(rng)
            d = eng.fourth_down(_four(s), bootstrap=bool(rng.random() < 0.5))
            vals = [x for x in d.wp.values() if x is not None]
            assert all(np.isfinite(vals)) and all(0.0 <= x <= 1.0 for x in vals), (d.wp, s)
            assert d.wp[d.best] == max(vals) and d.gap >= 0.0
            assert (d.wp["fg"] is None) == (s.yardline_100 + 18 > 70)
            assert 0.0 <= d.convert <= 1.0 and 0.0 < d.wp_now < 1.0
            assert d.label in (None, "Toss-up", "Lean", "Confident")
            json.dumps(d.as_dict())
        for _ in range(40):
            s = random_state(rng).replace(down=3)
            t = eng.third_down(s, bootstrap=False)
            assert 0.0 <= t.convert <= 1.0 and 0.0 <= t.pass_prob <= 1.0
            assert t.table == [] or t.note is None
            json.dumps(t.as_dict())


def test_more_points_never_hurts_any_option() -> None:
    """Football rules are monotone in the score: with a monotone wp model, +3 points for the
    offense can't lower the value of go, field goal or punt (ties and kneel-outs included)."""
    rng = np.random.default_rng(5)
    with engine(make_models()) as eng:
        for _ in range(150):
            s = _four(random_state(rng))
            if abs(s.score_diff) > 20:
                s = s.replace(score_diff=int(np.sign(s.score_diff)) * 20)
            lo = eng.fourth_down(s, bootstrap=False)
            hi = eng.fourth_down(s.replace(score_diff=s.score_diff + 3), bootstrap=False)
            for k, x in lo.wp.items():
                if x is not None:
                    assert hi.wp[k] >= x - 1e-9, f"{k}: {x:.4f} -> {hi.wp[k]:.4f} with +3 for {s}"


def test_the_engine_uses_wp_row_in_schema_order() -> None:
    """The engine builds wp rows with `wp_row`; the stub reads columns by `WP_FEATURES` index, so
    a column-order slip would show up as a wrong stub value in the tests above."""
    row = wp_row(diff=3, gs=1800, hs=1800, yl=75, down=1, togo=10, off_to=2, def_to=1, home=-1,
                 r2h=0, spread=-3.5, ot=False, era=4, indoor=0)  # fmt: skip
    ix = {f: i for i, f in enumerate(schema.WP_FEATURES)}
    assert row[ix["score_diff"]] == 3 and row[ix["game_seconds"]] == 1800
    assert row[ix["off_timeouts"]] == 2 and row[ix["def_timeouts"]] == 1
    assert row[ix["home"]] == -1 and row[ix["spread"]] == -3.5 and row[ix["era"]] == 4
    assert row[ix["ydstogo"]] == 10 and row[ix["down"]] == 1 and row[ix["yardline_100"]] == 75
