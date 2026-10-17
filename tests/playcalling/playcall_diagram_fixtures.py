"""PC03 fixtures: `plays_enriched` rows as dicts, for the play diagrams and the play browser.

`make_row(**overrides)` is one 2026 play as the build writes it (FTN charted, no participation
yet: that data arrives about February 2027): a 12-yard completion to the middle of the field,
KC against DEN, first and ten on KC's own 25, shotgun, one back, six in the box, four rushers.
Every column of `plays_enriched` is present (a key a reader forgets to ask for can't hide).

`row_2019(...)` is a pre-FTN play (participation only: personnel, coverage, the target's route,
the box and rushers by participation; no hash, alignment, backfield or motion) and
`row_2025(...)` has both FTN and participation. The other builders make the named cases (a run,
a sack, a scramble, a deep shot, a screen, touchdowns, a fumble, a placeholder FTN row, an
aborted snap); `check_diagram` is the contract every reconstruction must meet.
"""

from __future__ import annotations

import json
from typing import Any

# every column of plays_enriched, in the build's order (build.enrich_plays), with the 2026 default
COLUMNS = (
    "game_id", "play_id", "season", "week", "season_type", "game_date", "order_sequence",
    "offense", "defense", "home", "quarter", "half_seconds", "down", "distance",
    "yards_to_goal", "goal_to_go", "score_diff", "wp", "call", "dropback", "designed_run",
    "pass_attempt", "sack", "scramble", "aborted", "xpass", "pass_oe", "pass_length",
    "pass_location", "air_yards", "complete", "interception", "run_location", "run_gap",
    "shotgun", "no_huddle", "yards_gained", "epa", "success", "first_down", "touchdown",
    "passer_id", "rusher_id", "receiver_id", "desc", "field_zone", "score_state",
    "distance_bucket", "down_distance", "time_bucket", "two_minute", "neutral", "pass_zone",
    "run_direction", "deep_shot", "explosive", "ftn_charted", "play_action", "screen", "rpo",
    "motion", "trick_play", "qb_alignment", "backfield", "box", "blitzers", "rushers", "blitz",
    "hash", "out_of_pocket", "part_charted", "formation", "personnel", "extra_ol",
    "def_package", "coverage", "man_zone", "target_route", "pressure", "time_to_throw",
    "part_box", "part_rushers",
)  # fmt: skip

GAME = "2026_04_KC_DEN"
PASS_DESC = (
    "(10:15) (Shotgun) 15-P.Mahomes pass short middle to 87-T.Kelce to DEN 13 for 12 yards "
    "(58-J.Doe)."
)
RUN_DESC = "(8:02) 10-I.Pacheco left guard to KC 31 for 6 yards (95-C.Jones)."

BASE: dict[str, Any] = {
    "game_id": GAME,
    "play_id": 1234.0,
    "season": 2026,
    "week": 4,
    "season_type": "REG",
    "game_date": "2026-10-04",
    "order_sequence": 1234.0,
    "offense": "KC",
    "defense": "DEN",
    "home": False,
    "quarter": 2,
    "half_seconds": 615,  # 10:15 left in the 2nd quarter (and in the half)
    "down": 1,
    "distance": 10,
    "yards_to_goal": 75,  # KC's own 25
    "goal_to_go": False,
    "score_diff": 0,
    "wp": 0.5,
    "call": "pass",
    "dropback": True,
    "designed_run": False,
    "pass_attempt": True,
    "sack": False,
    "scramble": False,
    "aborted": False,
    "xpass": 0.6,
    "pass_oe": 5.0,
    "pass_length": "short",
    "pass_location": "middle",
    "air_yards": 7.0,
    "complete": True,
    "interception": False,
    "run_location": None,
    "run_gap": None,
    "shotgun": True,
    "no_huddle": False,
    "yards_gained": 12.0,
    "epa": 0.512,
    "success": True,
    "first_down": True,
    "touchdown": False,
    "passer_id": "00-QB-KC",
    "rusher_id": None,
    "receiver_id": "00-WR-KC",
    "desc": PASS_DESC,
    "field_zone": "own",
    "score_state": "tied",
    "distance_bucket": "long",
    "down_distance": "1st & 10",
    "time_bucket": "q2_early",
    "two_minute": False,
    "neutral": True,
    "pass_zone": "short_middle",
    "run_direction": None,
    "deep_shot": False,
    "explosive": False,
    "ftn_charted": True,
    "play_action": False,
    "screen": False,
    "rpo": False,
    "motion": False,
    "trick_play": False,
    "qb_alignment": "shotgun",
    "backfield": 1,
    "box": 6,
    "blitzers": 0,
    "rushers": 4,
    "blitz": False,
    "hash": "middle",
    "out_of_pocket": False,
    "part_charted": False,
    "formation": None,
    "personnel": None,
    "extra_ol": None,
    "def_package": None,
    "coverage": None,
    "man_zone": None,
    "target_route": None,
    "pressure": None,
    "time_to_throw": None,
    "part_box": None,
    "part_rushers": None,
}
assert tuple(BASE) == COLUMNS, "the fixture row must carry exactly the columns the build writes"

# FTN's columns and participation's: the two sources a row can lack
FTN_NULL = {
    "ftn_charted": False,
    "play_action": None,
    "screen": None,
    "rpo": None,
    "motion": None,
    "trick_play": None,
    "qb_alignment": None,
    "backfield": None,
    "box": None,
    "blitzers": None,
    "rushers": None,
    "blitz": None,
    "hash": None,
    "out_of_pocket": None,
}
PART = {
    "part_charted": True,
    "formation": "SHOTGUN",
    "personnel": "11",
    "extra_ol": False,
    "def_package": "4-2-5",
    "coverage": "cover_3",
    "man_zone": "zone",
    "target_route": "hitch_curl",
    "pressure": False,
    "time_to_throw": 2.61,
    "part_box": 6,
    "part_rushers": 4,
}


def make_row(**overrides: Any) -> dict[str, Any]:
    """A 2026 FTN play (see the module doc); `overrides` replace columns by name."""
    unknown = set(overrides) - set(BASE)
    assert not unknown, f"not a plays_enriched column: {sorted(unknown)}"
    return {**BASE, **overrides}


def row_2019(**overrides: Any) -> dict[str, Any]:
    """A pre-FTN play: participation only (personnel, coverage, route, box and rushers)."""
    return make_row(
        **{
            "game_id": "2019_06_KC_DEN",
            "season": 2019,
            "week": 6,
            "desc": "(10:15) 15-P.Mahomes pass short middle to 87-T.Kelce for 12 yards.",
            **FTN_NULL,
            **PART,
            **overrides,
        }
    )


def row_2025(**overrides: Any) -> dict[str, Any]:
    """A 2025 play: FTN charted and participation (both sources)."""
    return make_row(
        **{
            "game_id": "2025_06_KC_DEN",
            "season": 2025,
            "week": 6,
            **PART,
            **overrides,
        }
    )


def run_row(**overrides: Any) -> dict[str, Any]:
    """A designed run: left guard for 6 yards, under center, two backs, FTN charted."""
    return make_row(
        **{
            "call": "run",
            "dropback": False,
            "designed_run": True,
            "pass_attempt": False,
            "pass_length": None,
            "pass_location": None,
            "air_yards": None,
            "complete": None,
            "interception": None,
            "run_location": "left",
            "run_gap": "guard",
            "shotgun": False,
            "yards_gained": 6.0,
            "first_down": False,
            "rusher_id": "00-RB-KC",
            "passer_id": None,
            "receiver_id": None,
            "desc": RUN_DESC,
            "pass_zone": None,
            "run_direction": "left_guard",
            "qb_alignment": "under_center",
            "backfield": 1,
            "box": 7,
            "rushers": 0,
            "blitzers": 0,
            "blitz": False,
            **overrides,
        }
    )


def sack_row(**overrides: Any) -> dict[str, Any]:
    """A sack for 7 yards: no attempt, no air yards, a dropback."""
    return make_row(
        **{
            "pass_attempt": False,
            "sack": True,
            "pass_length": None,
            "pass_location": None,
            "air_yards": None,
            "complete": None,
            "interception": None,
            "yards_gained": -7.0,
            "first_down": False,
            "success": False,
            "desc": "(9:40) (Shotgun) 15-P.Mahomes sacked at KC 18 for -7 yards (95-C.Jones).",
            "rushers": 5,
            "blitzers": 1,
            "blitz": True,
            **overrides,
        }
    )


def scramble_row(**overrides: Any) -> dict[str, Any]:
    """A scramble to the left for 9 yards."""
    return make_row(
        **{
            "pass_attempt": False,
            "scramble": True,
            "pass_length": None,
            "pass_location": None,
            "air_yards": None,
            "complete": None,
            "interception": None,
            "run_location": "left",
            "run_gap": None,
            "yards_gained": 9.0,
            "first_down": False,
            "desc": "(9:11) (Shotgun) 15-P.Mahomes scrambles left end to KC 34 for 9 yards.",
            **overrides,
        }
    )


def deep_shot_row(**overrides: Any) -> dict[str, Any]:
    """A play-action deep shot to the right, 28 air yards, complete for 31 (under center,
    two backs, a motion man)."""
    return make_row(
        **{
            "yards_to_goal": 60,
            "distance": 10,
            "pass_length": "deep",
            "pass_location": "right",
            "air_yards": 28.0,
            "yards_gained": 31.0,
            "first_down": True,
            "explosive": True,
            "deep_shot": True,
            "play_action": True,
            "motion": True,
            "shotgun": False,
            "qb_alignment": "under_center",
            "backfield": 2,
            "box": 7,
            "rushers": 4,
            "hash": "left",
            "desc": (
                "(4:30) 15-P.Mahomes pass deep right to 11-M.Brown to DEN 29 for 31 yards "
                "(33-T.Smith)."
            ),
            **overrides,
        }
    )


def screen_row(**overrides: Any) -> dict[str, Any]:
    """A screen to the left, 4 yards behind the line, complete for 11."""
    return make_row(
        **{
            "pass_length": "short",
            "pass_location": "left",
            "air_yards": -4.0,
            "yards_gained": 11.0,
            "first_down": True,
            "screen": True,
            "desc": "(7:55) (Shotgun) 15-P.Mahomes pass short left to 10-I.Pacheco for 11 yards.",
            "rushers": 4,
            **overrides,
        }
    )


def td_pass(**overrides: Any) -> dict[str, Any]:
    """A 20-yard touchdown pass from the DEN 20 (yards_to_goal 20)."""
    return make_row(
        **{
            "yards_to_goal": 20,
            "distance": 10,
            "pass_location": "right",
            "air_yards": 17.0,
            "pass_length": "short",
            "yards_gained": 20.0,
            "touchdown": True,
            "first_down": True,
            "desc": "(2:00) (Shotgun) 15-P.Mahomes pass short right to 87-T.Kelce for 20 yards, "
            "TOUCHDOWN.",
            **overrides,
        }
    )


def td_run(**overrides: Any) -> dict[str, Any]:
    """A 3-yard touchdown run up the middle from the DEN 3."""
    return run_row(
        **{
            "yards_to_goal": 3,
            "distance": 3,
            "goal_to_go": True,
            "run_location": "middle",
            "run_gap": None,
            "yards_gained": 3.0,
            "touchdown": True,
            "first_down": True,
            "desc": "(1:10) 10-I.Pacheco up the middle for 3 yards, TOUCHDOWN.",
            **overrides,
        }
    )


def fumble_row(recovered_by: str, **overrides: Any) -> dict[str, Any]:
    """A completion for 4 yards that is fumbled and recovered by `recovered_by` (a team code)."""
    return make_row(
        **{
            "air_yards": 3.0,
            "yards_gained": 4.0,
            "first_down": False,
            "desc": (
                "(6:20) (Shotgun) 15-P.Mahomes pass short middle to 87-T.Kelce to KC 29 for 4 "
                "yards (58-J.Doe). FUMBLES (58-J.Doe), "
                f"RECOVERED by {recovered_by}-58-J.Doe at KC 27."
            ),
            **overrides,
        }
    )


def aborted_row(**overrides: Any) -> dict[str, Any]:
    """An aborted snap: no pass, no run, no yards."""
    return make_row(
        **{
            "dropback": False,
            "pass_attempt": False,
            "aborted": True,
            "pass_length": None,
            "pass_location": None,
            "air_yards": None,
            "complete": None,
            "interception": None,
            "yards_gained": 0.0,
            "first_down": False,
            "success": False,
            "desc": "(5:00) (Shotgun) 15-P.Mahomes Aborted. 66-N.Martin FUMBLES at KC 20.",
            **overrides,
        }
    )


def placeholder_ftn_row(**overrides: Any) -> dict[str, Any]:
    """A play FTN did not chart (its placeholder row was dropped by the build): no FTN labels."""
    return make_row(**{**FTN_NULL, **overrides})


# ---- accessors and the invariants every reconstruction must satisfy ------------------------------
WIDTH = 160 / 3
PHASE_NAMES = {
    "presnap", "motion", "snap", "fake", "handoff", "carry", "throw", "catch", "scramble", "end",
}  # fmt: skip
MARK_KINDS = {
    "motion", "fake", "handoff", "lane", "run", "throw", "end", "sack", "incomplete",
    "interception",
}  # fmt: skip
ALWAYS_UNKNOWN = (
    "Routes of the receivers who weren't targeted",
    "Coverage assignments (who covered whom)",
    "Blocking assignments",
    "Where the defenders went after the snap (beyond the rushers)",
)


def player(d: dict[str, Any], pid: str) -> dict[str, Any]:
    return next(p for p in d["players"] if p["id"] == pid)


def players(d: dict[str, Any], role: str | None = None, side: str | None = None) -> list[dict]:
    return [
        p
        for p in d["players"]
        if (role is None or p["role"] == role) and (side is None or p["side"] == side)
    ]


def phase(d: dict[str, Any], name: str) -> dict[str, Any]:
    return next(p for p in d["phases"] if p["name"] == name)


def phase_names(d: dict[str, Any]) -> list[str]:
    return [p["name"] for p in d["phases"]]


def marks(d: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [m for m in d["marks"] if m["kind"] == kind]


def start_of(p: dict[str, Any]) -> list[float]:
    return p["track"][0]


def end_of(p: dict[str, Any]) -> list[float]:
    return p["track"][-1]


def box_known(d: dict[str, Any]) -> bool:
    return any(
        k.endswith("defenders in the box (FTN)") or "in the box (par" in k for k in d["known"]
    )


def check_diagram(d: dict[str, Any], row: dict[str, Any] | None = None) -> None:
    """Every rule a reconstruction obeys whatever the play (the phase's "unit-tested" contract)."""
    assert d["label"] == "Reconstructed from charting data, not player tracking"
    assert d["kind"] == "reconstruction" and d["version"] == 1
    json.loads(json.dumps(d, allow_nan=False))  # JSON-ready, no NaN / inf
    offense, defense = players(d, side="offense"), players(d, side="defense")
    assert (len(offense), len(defense)) == (11, 11), d["play"]
    ids = [p["id"] for p in d["players"]]
    assert len(set(ids)) == 22
    end_t = 0.0
    for p in d["players"]:
        keys = p["track"]
        assert keys and keys[0][0] == 0.0, p["id"]
        times = [k[0] for k in keys]
        assert all(a < b for a, b in zip(times, times[1:], strict=False)), (p["id"], times)
        for k in keys:
            assert len(k) == 3
            assert -10.0 <= k[1] <= 110.0, (p["id"], k)
            assert 0.5 <= k[2] <= WIDTH - 0.5, (p["id"], k)
        end_t = max(end_t, times[-1])
    assert players(d, "qb", "offense") and len(players(d, "qb")) == 1
    ball = d["ball"]
    assert ball and ball[0][0] == 0.0
    btimes = [k[0] for k in ball]
    assert all(a < b for a, b in zip(btimes, btimes[1:], strict=False)), btimes
    for k in ball:
        assert len(k) == 4 and -10.0 <= k[1] <= 110.0 and 0.5 <= k[2] <= WIDTH - 0.5 and k[3] >= 0
    end_t = max(end_t, btimes[-1])
    names = phase_names(d)
    assert names[0] == "presnap" and d["phases"][0]["t"] == 0.0
    assert set(names) <= PHASE_NAMES and names.count("end") == 1 and names[-1] == "end"
    ptimes = [p["t"] for p in d["phases"]]
    assert ptimes == sorted(ptimes)
    assert all(isinstance(p["text"], str) and p["text"] for p in d["phases"])
    end_t = max(end_t, ptimes[-1])
    for m in d["marks"]:
        assert m["kind"] in MARK_KINDS and m["points"] and isinstance(m["approx"], bool)
        assert 0.0 <= m["t"] <= d["duration"]
        for x, y in m["points"]:
            assert -10.0 <= x <= 110.0 and 0.5 <= y <= WIDTH - 0.5, m
    assert d["duration"] > end_t, (d["duration"], end_t)
    # the camera holds everything that moves
    lo, hi = d["field"]["view"]
    xs = [k[1] for p in d["players"] for k in p["track"]] + [k[1] for k in ball]
    assert lo <= min(xs) and hi >= max(xs) and -10.0 <= lo < hi <= 110.0, (lo, hi, min(xs), max(xs))
    if row is not None and row.get("yards_to_goal") is not None:
        assert d["field"]["los"] == 100 - row["yards_to_goal"]
    # the honesty rules: nothing generic is ever "known"; the unknowns are always named
    known, approx, unknown = d["known"], d["approximate"], d["unknown"]
    for lst in (known, approx, unknown):
        assert len(lst) == len(set(lst)) and all(isinstance(s, str) and s for s in lst)
    assert not (set(known) & set(approx)) and not (set(known) & set(unknown))
    assert not (set(approx) & set(unknown))
    for s in ALWAYS_UNKNOWN:
        assert s in unknown, s
    for p in d["players"]:
        if p["role"] in ("receiver", "back", "dl", "lb", "db"):
            assert p["approx"], f"{p['id']} is at a generic spot and must say so"
    if box_known(d):  # the count is charted, the split and the spots aren't
        assert any("box defenders stand" in s for s in approx)
    # only what the data measures is drawn as measured: the center over a CHARTED ball, and the
    # QB when the hash and his alignment are both known; the linemen beside the center are
    # always at standard splits
    hash_charted = d["field"]["hash"] is not None
    for p in players(d, "ol"):
        assert p["approx"] == (p["label"] != "C" or not hash_charted), p["id"]
    align_known = any(k.startswith("QB ") for k in known)
    assert players(d, "qb")[0]["approx"] == (not (hash_charted and align_known))
    assert (
        "The linemen beside the center, at standard 1.3-yard splits"
        + ("" if hash_charted else "; the center and the QB with the ball, in the middle")
    ) in approx
    if players(d, "receiver"):
        assert any(s.startswith("Where the") and "receiver" in s for s in approx)
    if players(d, "db"):
        assert any("defenders outside the box stand" in s for s in approx)
    for s in known:
        assert not s.startswith("Where the"), s
    assert d["sources"][0] == "nflverse play-by-play"
