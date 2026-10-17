"""PC03 fixtures: a synthetic `playcalling/<S>/plays_enriched.parquet` tree for the play browser.

Nothing here runs the real build. The rows come from the diagram test fixtures
(`tests/playcalling/playcall_diagram_fixtures.py`: every `plays_enriched` column, a 2026 FTN
play by default) and are written with the build's own column types (`SCHEMA`; an integration
test compares it with the file on the data root), next to a `build.json` in the PC00 layout.

**The league** (2026) is small and hand-countable:

- KC week 1 at LV (5 plays; the opponent's rows carry the OLD code `OAK`, which the reader maps
  to `LV`), week 3 vs DEN (4 plays), week 4 at DEN (`W4`, 17 plays chosen so that every filter
  has a known answer: each `Spec` declares the down, the play types and the results it must
  match), week 6 at ZZZ (a code that is not a team: no opponent; `home` null) and a week 7 row
  with a malformed game id that must never show. KC is on a bye in week 5 and the build's
  newest as-of week is 5, so KC's default week is its newest, 6.
- LV week 5 (and a week 19 playoff game at DEN), DEN weeks 3-5, CHI with only a malformed id
  (no plays), NYJ with none at all.
- 2025: KC week 1 vs DEN with participation (a FTN + participation row).
- 2024 has only a `build.json` (the plays file is missing); 2023 has plays and no `build.json`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "playcalling"))
import playcall_diagram_fixtures as X  # noqa: E402
from playcall_fixtures import build_doc  # noqa: E402

from nflengine.paths import DataPaths  # noqa: E402

SCHEMA: dict[str, Any] = {
    "game_id": pl.String, "play_id": pl.Float64, "season": pl.Int32, "week": pl.Int32,
    "season_type": pl.String, "game_date": pl.String, "order_sequence": pl.Float64,
    "offense": pl.String, "defense": pl.String, "home": pl.Boolean, "quarter": pl.Int32,
    "half_seconds": pl.Int32, "down": pl.Int32, "distance": pl.Int32, "yards_to_goal": pl.Int32,
    "goal_to_go": pl.Boolean, "score_diff": pl.Int32, "wp": pl.Float64, "call": pl.String,
    "dropback": pl.Boolean, "designed_run": pl.Boolean, "pass_attempt": pl.Boolean,
    "sack": pl.Boolean, "scramble": pl.Boolean, "aborted": pl.Boolean, "xpass": pl.Float64,
    "pass_oe": pl.Float64, "pass_length": pl.String, "pass_location": pl.String,
    "air_yards": pl.Float64, "complete": pl.Boolean, "interception": pl.Boolean,
    "run_location": pl.String, "run_gap": pl.String, "shotgun": pl.Boolean,
    "no_huddle": pl.Boolean, "yards_gained": pl.Float64, "epa": pl.Float64,
    "success": pl.Boolean, "first_down": pl.Boolean, "touchdown": pl.Boolean,
    "passer_id": pl.String, "rusher_id": pl.String, "receiver_id": pl.String, "desc": pl.String,
    "field_zone": pl.String, "score_state": pl.String, "distance_bucket": pl.String,
    "down_distance": pl.String, "time_bucket": pl.String, "two_minute": pl.Boolean,
    "neutral": pl.Boolean, "pass_zone": pl.String, "run_direction": pl.String,
    "deep_shot": pl.Boolean, "explosive": pl.Boolean, "ftn_charted": pl.Boolean,
    "play_action": pl.Boolean, "screen": pl.Boolean, "rpo": pl.Boolean, "motion": pl.Boolean,
    "trick_play": pl.Boolean, "qb_alignment": pl.String, "backfield": pl.Int32,
    "box": pl.Int32, "blitzers": pl.Int32, "rushers": pl.Int32, "blitz": pl.Boolean,
    "hash": pl.String, "out_of_pocket": pl.Boolean, "part_charted": pl.Boolean,
    "formation": pl.String, "personnel": pl.String, "extra_ol": pl.Boolean,
    "def_package": pl.String, "coverage": pl.String, "man_zone": pl.String,
    "target_route": pl.String, "pressure": pl.Boolean, "time_to_throw": pl.Float64,
    "part_box": pl.Int32, "part_rushers": pl.Int32,
}  # fmt: skip
assert list(SCHEMA) == list(X.COLUMNS)

PASS_KINDS = {"pass", "sack", "scramble", "dropback"}  # what the `pass` filter matches
TYPES = ("run", "pass", "sack", "scramble", "play_action", "screen", "rpo", "deep_shot", "motion")
RESULTS = ("first_down", "touchdown", "turnover", "explosive", "negative", "incomplete", "success")


def frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=SCHEMA)


def game_id(season: int, week: int, away: str, home: str) -> str:
    return f"{season}_{week:02d}_{away}_{home}"


@dataclass(frozen=True)
class Game:
    gid: str
    week: int
    stype: str = "REG"
    season: int = 2026


# the rows of one team in one game
def rows_in(game: Game, offense: str, defense: str, home: bool | None, plays: list[dict]) -> list:
    return [
        {
            **r,
            "game_id": game.gid,
            "season": game.season,
            "week": game.week,
            "season_type": game.stype,
            "offense": offense,
            "defense": defense,
            "home": home,
        }
        for r in plays
    ]


@dataclass(frozen=True)
class Spec:
    """One KC week-4 play with the answers a filter must give for it, declared by hand."""

    pid: int
    row: dict[str, Any]
    down: int
    types: frozenset[str]  # the play-type filters it matches
    results: frozenset[str]  # the result filters it matches
    kind: str  # the play's type in the list

    @property
    def sort_key(self) -> tuple[float, int]:
        return (self.row["order_sequence"], self.pid)


def spec(pid, row, down, types, results, kind, order=None) -> Spec:
    row = {**row, "play_id": float(pid), "order_sequence": float(order or pid * 10), "down": down}
    return Spec(pid, row, down, frozenset(types), frozenset(results), kind)


# KC at DEN, week 4, 17 plays (the first number is the play id)
W4: list[Spec] = [
    spec(
        10, X.run_row(yards_gained=6.0, first_down=False, success=False), 1, {"run"}, set(), "run"
    ),  # fmt: skip
    spec(
        20,
        X.make_row(complete=False, yards_gained=0.0, first_down=False, success=False, distance=4),
        2,
        {"pass"},
        {"incomplete"},
        "pass",
    ),  # fmt: skip
    spec(
        30,
        X.make_row(play_action=True, yards_gained=14.0, first_down=True, success=True, distance=11),
        3,
        {"pass", "play_action"},
        {"first_down", "success"},
        "pass",
        order=300,
    ),  # fmt: skip
    spec(40, X.sack_row(success=False), 1, {"pass", "sack"}, {"negative"}, "sack", order=300),
    spec(50, X.scramble_row(success=True), 2, {"pass", "scramble"}, {"success"}, "scramble"),
    spec(
        60, X.screen_row(success=True), 3, {"pass", "screen"}, {"first_down", "success"}, "pass"
    ),  # fmt: skip
    spec(
        70,
        X.deep_shot_row(success=True),
        1,
        {"pass", "play_action", "deep_shot", "motion"},
        {"first_down", "explosive", "success"},
        "pass",
    ),  # fmt: skip
    spec(
        80,
        X.make_row(
            complete=False, interception=True, yards_gained=0.0, first_down=False, success=False
        ),
        2,
        {"pass"},
        {"turnover"},
        "pass",
    ),  # fmt: skip
    spec(
        90,
        X.run_row(yards_gained=-3.0, first_down=False, success=False),
        1,
        {"run"},
        {"negative"},
        "run",
    ),  # fmt: skip
    spec(100, X.td_run(success=True), 4, {"run"}, {"touchdown", "first_down", "success"}, "run"),
    spec(110, X.fumble_row("DEN", success=False), 1, {"pass"}, {"turnover"}, "pass"),
    spec(
        120,
        X.make_row(rpo=True, yards_gained=5.0, first_down=False, success=True),
        2,
        {"pass", "rpo"},
        {"success"},
        "pass",
    ),  # fmt: skip
    spec(130, X.aborted_row(), 3, set(), set(), "aborted"),
    spec(
        140, X.td_pass(success=True), 3, {"pass"}, {"touchdown", "first_down", "success"}, "pass"
    ),  # fmt: skip
    spec(
        150,
        X.run_row(yards_gained=22.0, explosive=True, first_down=True, success=True),
        1,
        {"run"},
        {"explosive", "first_down", "success"},
        "run",
    ),  # fmt: skip
    spec(
        160,
        X.make_row(motion=True, yards_gained=8.0, first_down=False, success=True),
        2,
        {"pass", "motion"},
        {"success"},
        "pass",
    ),  # fmt: skip
    # a pick-six: nflverse flags it `touchdown`, but it is the defense's score (a turnover)
    spec(
        170,
        X.make_row(
            complete=False,
            interception=True,
            touchdown=True,
            yards_gained=0.0,
            first_down=False,
            success=False,
            desc="(8:00) (Shotgun) 15-P.Mahomes pass short left INTERCEPTED by "
            "1-X.Y at DEN 30. 1-X.Y for 30 yards, TOUCHDOWN.",
        ),
        3,
        {"pass"},
        {"turnover"},
        "pass",
        order=5,
    ),  # fmt: skip
]
assert len(W4) == 17


def expected(specs: list[Spec], down=None, kind=None, result=None) -> list[Spec]:
    """The specs a filter must keep, in game order (order_sequence, then play id)."""
    keep = [
        s
        for s in specs
        if (down is None or s.down == down)
        and (kind is None or kind in s.types)
        and (result is None or result in s.results)
    ]
    return sorted(keep, key=lambda s: s.sort_key)


def simple(pid: int, down: int = 1, **over: Any) -> dict[str, Any]:
    return X.make_row(play_id=float(pid), order_sequence=float(pid), down=down, **over)


def nulls() -> dict[str, Any]:
    """A play with almost every nullable column null (KC week 6, play 63)."""
    return simple(
        63, None, quarter=None, half_seconds=None, distance=None, yards_to_goal=None,
        epa=None, yards_gained=None, air_yards=None, desc=None, first_down=None,
    )  # fmt: skip


G1 = Game("2026_01_KC_LV", 1)
G3 = Game("2026_03_DEN_KC", 3)
G4 = Game("2026_04_KC_DEN", 4)
G5 = Game("2026_05_LV_DEN", 5)
G6 = Game("2026_06_KC_ZZZ", 6)
G7_BAD = Game("not-a-game", 7)
G19 = Game("2026_19_LV_DEN", 19, "POST")
G25 = Game("2025_01_KC_DEN", 1, season=2025)


def world_rows_2026(extra_kc_w4: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    kc_w4 = [s.row for s in W4] + (extra_kc_w4 or [])
    rows: list[dict[str, Any]] = []
    # week 1: KC at LV; LV's rows say OAK (an old code in the file)
    rows += rows_in(G1, "KC", "OAK", False, [simple(i) for i in range(1, 6)])
    rows += rows_in(G1, "OAK", "KC", True, [simple(i, 2) for i in range(11, 14)])
    # week 3: DEN at KC
    rows += rows_in(G3, "KC", "DEN", True, [simple(i, 3) for i in range(21, 25)])
    rows += rows_in(G3, "DEN", "KC", False, [simple(i) for i in range(31, 33)])
    # week 4: KC at DEN
    rows += rows_in(G4, "KC", "DEN", False, kc_w4)
    rows += rows_in(G4, "DEN", "KC", True, [simple(i, 1 + i % 3) for i in range(1001, 1004)])
    # week 5 (KC on a bye): LV at DEN
    rows += rows_in(G5, "LV", "DEN", False, [simple(i) for i in range(41, 45)])
    rows += rows_in(G5, "DEN", "LV", True, [simple(i) for i in range(51, 54)])
    # week 6: KC away at a code that is not a team, the home flag unknown
    rows += rows_in(G6, "KC", "ZZZ", None, [simple(61), simple(62, 2, ftn_charted=False), nulls()])
    # a malformed game id never shows (KC week 7; CHI's only rows)
    rows += rows_in(G7_BAD, "KC", "DEN", False, [simple(71)])
    rows += rows_in(G7_BAD, "CHI", "DEN", False, [simple(72)])
    # a playoff game
    rows += rows_in(G19, "LV", "DEN", False, [simple(81), simple(82, 3)])
    rows += rows_in(G19, "DEN", "LV", True, [simple(91)])
    return rows


def world_rows_2025() -> list[dict[str, Any]]:
    kc = [
        X.row_2025(play_id=float(i), order_sequence=float(i), down=1 + i % 3, season=2025)
        for i in (1, 2, 3)
    ]
    den = [X.row_2025(play_id=float(i), order_sequence=float(i), season=2025) for i in (11, 12)]
    return rows_in(G25, "KC", "DEN", False, kc) + rows_in(G25, "DEN", "KC", True, den)


def write_plays(paths: DataPaths, season: int, rows: list[dict[str, Any]], last: int = 5) -> Path:
    """`plays_enriched.parquet` (in a scrambled order, so the reader must sort) and then the
    `build.json` (the build writes it last)."""
    d = paths.playcalling / str(season)
    d.mkdir(parents=True, exist_ok=True)
    frame(list(reversed(rows))).write_parquet(d / "plays_enriched.parquet")
    (d / "build.json").write_text(json.dumps(build_doc(season, last)), encoding="utf-8")
    return d


@dataclass
class World:
    paths: DataPaths
    seasons: list[int] = field(default_factory=list)


def make_world(paths: DataPaths, extra_kc_w4: list[dict[str, Any]] | None = None) -> World:
    write_plays(paths, 2026, world_rows_2026(extra_kc_w4), last=5)
    write_plays(paths, 2025, world_rows_2025(), last=19)
    # 2024: a build.json and no plays file; 2023: a plays file and no build.json
    d = paths.playcalling / "2024"
    d.mkdir(parents=True, exist_ok=True)
    (d / "build.json").write_text(json.dumps(build_doc(2024, 19)), encoding="utf-8")
    d = paths.playcalling / "2023"
    d.mkdir(parents=True, exist_ok=True)
    frame(
        rows_in(Game("2023_01_KC_DEN", 1, season=2023), "KC", "DEN", False, [simple(1)])
    ).write_parquet(d / "plays_enriched.parquet")
    return World(paths, [2026, 2025, 2024])


def write_library(paths: DataPaths, concepts: dict[str, Any], **top: Any) -> Path:
    """`playcalling/bdb_library/index.json` with these `concepts` (the library's last file)."""
    d = paths.playcalling / "bdb_library"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.json").write_text(json.dumps({"concepts": concepts, **top}), encoding="utf-8")
    return d
