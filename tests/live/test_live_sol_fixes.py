"""Regression tests for the Sol review of LD00 (2026-10-10): playoff overtime, halftime
timeouts, whole-number floats, zero bootstrap refits, the try after a return touchdown."""

from __future__ import annotations

import pytest
from live_stubs import engine, make_models, simple_punt, stub_wp_value

from nflengine.live import schema
from nflengine.live import train as T
from nflengine.live.decide import _Batch
from nflengine.live.schema import GameState

PAT_XP = 0.94  # make_models' long-era extra-point rate


def test_playoff_overtime_clock_is_valid_and_regular_season_isnt() -> None:
    s = GameState(2025, 0, 900, 900, 4, 10, 60, ot=True, playoffs=True)
    assert s.game_seconds == 900.0
    with pytest.raises(ValueError, match="game_seconds"):
        GameState(2025, 0, 900, 900, 4, 10, 60, ot=True, playoffs=False)
    assert schema.ot_minutes(2025, playoffs=True) == 15
    assert schema.ot_minutes(2025) == 10


def test_a_tied_playoff_overtime_period_goes_on() -> None:
    """5 s left in a tied OT period: the regular season ends tied (0.5); the playoffs go on
    into a new 15-minute period with the other team's ball where the punt left it."""
    reg = GameState(2025, 0, 5, 5, 4, 10, 60, ot=True)
    post = reg.replace(playoffs=True)
    with engine(make_models(punt=simple_punt(net=40))) as eng:
        assert eng.fourth_down(reg, bootstrap=False).wp["punt"] == pytest.approx(0.5)
        # the punt from 60 yards to goal leaves them 80 yards from the goal (their 20)
        want = 1.0 - stub_wp_value(0, 900, 80, ot=True)
        assert eng.fourth_down(post, bootstrap=False).wp["punt"] == pytest.approx(want)


def test_halftime_gives_both_teams_three_timeouts() -> None:
    s = GameState(2025, 0, 1805, 5, 4, 10, 60, off_timeouts=0, def_timeouts=1)
    with engine(make_models()) as eng:
        b = _Batch(s)
        eng._punt_outcome(b, s)  # the half ends: every state is after the 2nd-half kickoff
        assert b.rows, "the halftime kickoff states were scored"
        i_off = schema.WP_FEATURES.index("off_timeouts")
        i_def = schema.WP_FEATURES.index("def_timeouts")
        i_gs = schema.WP_FEATURES.index("game_seconds")
        for row in b.rows:
            assert row[i_gs] == 1800.0
            assert (row[i_off], row[i_def]) == (3.0, 3.0), "first-half timeouts carried over"


def test_whole_number_floats_become_ints_and_the_table_works() -> None:
    s = GameState.from_dict(
        {"season": 2025.0, "score_diff": 0, "game_seconds": 1500, "half_seconds": 1500,
         "down": 3.0, "ydstogo": 6.0, "yardline_100": 45.0}
    )  # fmt: skip
    assert isinstance(s.ydstogo, int) and isinstance(s.season, int)
    assert isinstance(s.game_seconds, float)
    with engine(make_models()) as eng:
        out = eng.third_down(s, bootstrap=False)
    assert len(out.table) == 16  # gains -10 .. 5


def test_the_gate_check_is_skipped_without_bootstrap_refits() -> None:
    with engine(make_models()) as eng:
        gate = T.gate_check(eng, [GameState(2025, 0, 2400, 600, 4, 1, 40)])
    assert gate["ok"] is False and "no bootstrap" in gate["skipped"]


def test_promote_without_bootstrap_refits_is_refused_before_fitting(monkeypatch) -> None:
    def boom(*a, **k):
        raise AssertionError("fitted before checking n_boot")

    monkeypatch.setattr(T, "load_frames", boom)
    with pytest.raises(ValueError, match="needs bootstrap refits"):
        T.run_train(seasons=[2020], promote=True, n_boot=0, use_wandb=False, log=lambda _m: None)
    with pytest.raises(ValueError, match="negative"):
        T.run_train(seasons=[2020], n_boot=-1, use_wandb=False, log=lambda _m: None)


def test_a_punt_return_touchdown_gets_the_try_not_a_sure_seven() -> None:
    """Up 6 with 5 s left: a return TD ties it; their extra point wins it for them, a miss
    leaves a tie (0.5). The punt is worth (1 - xp) x 0.5, not 0."""
    s = GameState(2025, 6, 5, 5, 4, 10, 60)
    with engine(make_models(punt=simple_punt(ret_td=1.0))) as eng:
        d = eng.fourth_down(s, bootstrap=False)
    assert d.wp["punt"] == pytest.approx((1 - PAT_XP) * 0.5)
