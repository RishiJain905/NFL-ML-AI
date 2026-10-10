"""LD00 schema: the game state contract, eras, rules by season, gain classes, and the check that
training and live scoring use the same formulas (`state_features` vs `decide.wp_row`)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from live_stubs import random_state

from nflengine.live import schema
from nflengine.live.decide import wp_row
from nflengine.live.schema import GameState


def ok_state(**kw) -> GameState:
    base = dict(season=2025, score_diff=0, game_seconds=2400, half_seconds=600, down=4,
                ydstogo=3, yardline_100=40)  # fmt: skip
    return GameState(**{**base, **kw})


# --- validation -------------------------------------------------------------------------------
def test_valid_state_builds() -> None:
    s = ok_state()
    assert s.era == 4 and s.first_half is True


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"down": 0}, "down 0"),
        ({"down": 5}, "down 5"),
        ({"yardline_100": 0}, "yard line 0"),
        ({"yardline_100": 100}, "yard line 100"),
        ({"ydstogo": 0}, "distance 0"),
        ({"ydstogo": 100}, "distance 100"),
        ({"ydstogo": 41}, "distance beyond the goal line (40 to go)"),
        ({"game_seconds": -1, "half_seconds": 0}, "negative clock"),
        ({"game_seconds": 3601, "half_seconds": 1801}, "game clock beyond 60:00"),
        ({"half_seconds": 1000}, "half clock inconsistent with the game clock"),
        ({"game_seconds": 1000, "half_seconds": 1000, "ot": True, "season": 2025}, "OT > 10:00"),
        ({"off_timeouts": 4}, "4 timeouts"),
        ({"def_timeouts": -1}, "-1 timeouts"),
        ({"home": 2}, "home 2"),
        ({"home": -2}, "home -2"),
        ({"receive_2h_ko": 2}, "receive_2h_ko 2"),
        ({"season": 2009}, "season before 2010"),
        ({"spread": 31.0}, "spread beyond 30"),
        ({"spread": float("nan")}, "NaN spread"),
        ({"total": 19.0}, "total under 20"),
        ({"total": 81.0}, "total over 80"),
        ({"score_diff": 81}, "score difference beyond 80"),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_bad_states_raise(kw: dict, why: str) -> None:
    with pytest.raises(ValueError, match="bad game state"):  # not a TypeError, not a success
        ok_state(**kw)


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"game_seconds": None}, "game_seconds must be a number"),
        ({"half_seconds": None}, "half_seconds must be a number"),
        ({"spread": None}, "spread must be a number"),
        ({"total": None}, "total must be a number"),
        ({"game_seconds": float("nan"), "half_seconds": float("nan")}, "must be a number"),
        ({"spread": float("nan")}, "spread must be a number"),
        ({"total": "44"}, "total must be a number"),
        ({"game_seconds": "1500"}, "game_seconds must be a number"),
        ({"spread": True}, "spread must be a number"),
        ({"yardline_100": 45.5}, "yardline_100 must be a whole number"),
        ({"score_diff": 2.5}, "score_diff must be a whole number"),
        ({"down": 3.5}, "down must be a whole number"),
        ({"down": "4"}, "down must be a whole number"),
        ({"down": None}, "down must be a whole number"),
        ({"down": True}, "down must be a whole number"),
        ({"ydstogo": "3"}, "ydstogo must be a whole number"),
        ({"season": 2025.5}, "season must be a whole number"),
        ({"off_timeouts": 1.5}, "off_timeouts must be a whole number"),
        ({"def_timeouts": None}, "def_timeouts must be a whole number"),
        ({"home": 0.5}, "home must be a whole number"),
        ({"receive_2h_ko": float("nan")}, "receive_2h_ko must be a whole number"),
        ({"wind": "calm"}, "wind must be a number or null"),
        ({"temp": float("nan")}, "temp must be a number or null"),
        ({"wind": True}, "wind must be a number or null"),
    ],
)
def test_wrong_types_are_a_value_error_with_a_plain_message(kw: dict, message: str) -> None:
    """Types are checked before ranges: no TypeError from a comparison, a clear ValueError."""
    with pytest.raises(ValueError, match="bad game state") as e:
        ok_state(**kw)
    assert message in str(e.value), str(e.value)


def test_whole_number_floats_and_missing_weather_are_accepted() -> None:
    s = ok_state(yardline_100=45.0, ydstogo=3.0, down=4.0, score_diff=-7.0, off_timeouts=2.0)
    assert s.yardline_100 == 45 and s.score_diff == -7
    assert ok_state(wind=None, temp=None).wind is None
    assert ok_state(wind=12, temp=35).temp == 35, "an int is a number"
    assert ok_state(game_seconds=1500.5, half_seconds=1500.5).game_seconds == 1500.5, (
        "clocks and the line may be fractional"
    )
    assert ok_state(spread=-3.5, total=47.5).spread == -3.5


def test_type_problems_are_reported_together() -> None:
    with pytest.raises(ValueError) as e:
        ok_state(game_seconds=None, spread="x", down=4.5, wind="calm")
    msg = str(e.value)
    for part in (
        "game_seconds must be a number",
        "spread must be a number",
        "down must be a whole number",
        "wind must be a number or null",
    ):
        assert part in msg, f"{part!r} missing from {msg!r}"


def test_from_dict_with_json_nulls_gives_the_same_clear_error() -> None:
    d = ok_state().as_dict()
    with pytest.raises(ValueError, match="game_seconds must be a number"):
        GameState.from_dict({**d, "game_seconds": None})
    assert GameState.from_dict({**d, "wind": None}).wind is None


def test_ydstogo_equal_to_yards_to_goal_is_goal_to_go() -> None:
    assert ok_state(ydstogo=40).ydstogo == 40, "a distance equal to the yards to goal is allowed"
    assert ok_state(yardline_100=3, ydstogo=3).ydstogo == 3, "goal to go: distance == yards to goal"


def test_overtime_clock_limit_follows_the_season() -> None:
    # 15-minute overtime to 2016, 10 minutes from 2017
    assert ok_state(season=2016, ot=True, game_seconds=900, half_seconds=900).ot
    with pytest.raises(ValueError, match="game_seconds"):
        ok_state(season=2017, ot=True, game_seconds=900, half_seconds=900)


def test_problems_are_all_listed_in_one_message() -> None:
    with pytest.raises(ValueError) as e:
        ok_state(down=9, home=5)
    assert "down 9" in str(e.value) and "home must be" in str(e.value), (
        "every problem should be reported at once, not just the first"
    )


def test_from_dict_roundtrip_and_unknown_fields() -> None:
    s = ok_state(wind=12.0, temp=33.0, roof="dome", playoffs=True)
    assert GameState.from_dict(s.as_dict()) == s, "as_dict / from_dict must round-trip"
    with pytest.raises(ValueError, match="unknown game-state fields: bogus, extra"):
        GameState.from_dict({**s.as_dict(), "extra": 1, "bogus": 2})
    with pytest.raises(TypeError):  # missing required fields: the CLI catches this as bad input
        GameState.from_dict({"season": 2025})


def test_state_is_frozen_and_replace_revalidates() -> None:
    s = ok_state()
    with pytest.raises(AttributeError):
        s.down = 3  # type: ignore[misc]
    assert s.replace(down=3).down == 3
    with pytest.raises(ValueError, match="bad game state"):
        s.replace(down=7)


# --- first half -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("game", "half", "ot", "expected"),
    [
        (3600, 1800, False, True),  # the opening snap
        (1801, 1, False, True),
        (1800, 0, False, True),  # end of Q2 (00:00 of the first half)
        (1800, 1800, False, False),  # the second half's first snap
        (1799, 1799, False, False),
        (0, 0, False, False),
        (300, 300, True, False),  # overtime is never "first half"
    ],
)
def test_first_half_edges(game: float, half: float, ot: bool, expected: bool) -> None:
    s = ok_state(game_seconds=game, half_seconds=half, ot=ot)
    assert s.first_half is expected, f"game {game} / half {half} / ot {ot}"


# --- eras and rules by season -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("season", "era"),
    [(2005, 0), (2010, 0), (2014, 0), (2015, 1), (2017, 1), (2018, 2), (2020, 2), (2021, 3),
     (2023, 3), (2024, 4), (2025, 4), (2040, 4)],
)  # fmt: skip
def test_era_of(season: int, era: int) -> None:
    assert schema.era_of(season) == era


@pytest.mark.parametrize(
    ("season", "era"),
    [(2010, "tb20"), (2015, "tb20"), (2016, "tb25"), (2023, "tb25"), (2024, "tb30"),
     (2025, "tb35"), (2030, "tb35")],
)  # fmt: skip
def test_kickoff_era(season: int, era: str) -> None:
    assert schema.kickoff_era(season) == era


@pytest.mark.parametrize(
    ("season", "era"), [(2010, "short"), (2014, "short"), (2015, "long"), (2025, "long")]
)
def test_pat_era(season: int, era: str) -> None:
    assert schema.pat_era(season) == era


def test_ot_minutes_boundary() -> None:
    assert schema.ot_minutes(2016) == 15
    assert schema.ot_minutes(2017) == 10


@pytest.mark.parametrize(
    ("season", "playoffs", "both"),
    [(2021, True, False), (2022, True, True), (2022, False, False), (2024, False, False),
     (2024, True, True), (2025, False, True), (2025, True, True)],
)  # fmt: skip
def test_ot_both_possess(season: int, playoffs: bool, both: bool) -> None:
    assert schema.ot_both_possess(season, playoffs) is both


def test_indoor_roofs() -> None:
    assert [schema.indoor(r) for r in ("dome", "closed", "DOME", "Closed")] == [1, 1, 1, 1]
    assert [schema.indoor(r) for r in ("outdoors", "open", "", None)] == [0, 0, 0, 0]


# --- gain classes -----------------------------------------------------------------------------
def test_gain_class_values_and_clipping() -> None:
    assert schema.N_GAIN_CLASSES == 77 and schema.TD_CLASS == 76
    assert schema.gain_class(0, False) == 10
    assert schema.gain_class(-10, False) == 0
    assert schema.gain_class(-11, False) == 0, "losses clip at -10"
    assert schema.gain_class(-50, False) == 0
    assert schema.gain_class(65, False) == 75
    assert schema.gain_class(66, False) == 75, "gains clip at +65"
    assert schema.gain_class(99, False) == 75
    assert schema.gain_class(2.4, False) == 12 and schema.gain_class(2.6, False) == 13
    assert schema.gain_class(3, True) == schema.TD_CLASS, "a touchdown is its own class"
    assert schema.gain_class(-4, True) == schema.TD_CLASS, "yards don't matter for a touchdown"
    assert schema.GAIN_VALUES[0] == -10 and schema.GAIN_VALUES[-1] == 65
    assert len(schema.GAIN_VALUES) == schema.TD_CLASS


# --- training and live use the same formulas --------------------------------------------------
def test_elapsed_share() -> None:
    assert schema.elapsed_share(3600) == 0.0
    assert schema.elapsed_share(0) == 1.0
    assert schema.elapsed_share(1800) == 0.5
    assert schema.elapsed_share(123, ot=True) == 1.0, "overtime counts as all of regulation"


def test_state_features_matches_wp_row_for_random_states() -> None:
    """`schema.state_features` (live + training frames) and `decide.wp_row` (the engine's batch)
    must give the same wp-model inputs, in the order of `WP_FEATURES`."""
    rng = np.random.default_rng(11)
    for _ in range(400):
        s = random_state(rng)
        feats = schema.state_features(s)
        want = [feats[f] for f in schema.WP_FEATURES]
        got = wp_row(
            diff=s.score_diff, gs=s.game_seconds, hs=s.half_seconds, yl=s.yardline_100,
            down=s.down, togo=s.ydstogo, off_to=s.off_timeouts, def_to=s.def_timeouts,
            home=s.home, r2h=s.receive_2h_ko, spread=s.spread, ot=s.ot, era=s.era,
            indoor=schema.indoor(s.roof),
        )  # fmt: skip
        assert len(got) == len(schema.WP_FEATURES)
        for name, a, b in zip(schema.WP_FEATURES, want, got, strict=True):
            assert math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-9), f"{name}: {a} vs {b} for {s}"


def test_state_features_values() -> None:
    s = ok_state(score_diff=7, game_seconds=1800, half_seconds=1800, spread=-3.0, total=50.0)
    f = schema.state_features(s)
    decay = math.exp(-4.0 * 0.5)
    assert f["diff_time_ratio"] == pytest.approx(7 / decay)
    assert f["spread_time"] == pytest.approx(-3.0 * decay)
    assert f["team_total"] == pytest.approx(50.0 / 2 + -3.0 / 2), "team total = total/2 + spread/2"
    assert f["era"] == 4.0 and f["indoor"] == 0.0 and f["ot"] == 0.0
    ot = schema.state_features(ok_state(ot=True, game_seconds=300, half_seconds=300, score_diff=3))
    assert ot["diff_time_ratio"] == pytest.approx(3 / math.exp(-4.0)), "OT: the full decay"
    assert set(schema.WP_FEATURES) <= set(f) and set(schema.GAIN_FEATURES) <= set(f)
    assert set(schema.PASS_FEATURES) - {"wp"} <= set(f)


def test_monotone_features_are_wp_features() -> None:
    assert set(schema.WP_MONOTONE) <= set(schema.WP_FEATURES)
    assert all(v == 1 for v in schema.WP_MONOTONE.values())


def test_numpy_numbers_are_accepted() -> None:
    """A state built straight from numpy / polars values (LD01) is fine; fractions still aren't."""
    s = GameState(np.int64(2025), np.int64(0), np.float64(2400), 600, np.int32(4), 1, np.int64(40))
    assert s.down == 4 and s.yardline_100 == 40
    with pytest.raises(ValueError, match="whole number"):
        GameState(2025, 0, 2400, 600, 4, 1, np.float64(40.5))
