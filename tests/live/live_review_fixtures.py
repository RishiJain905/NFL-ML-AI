"""Shared helpers for the LD03 decision-review tests: a hand-made curated folder (plays + games), a
stub decision engine whose win probabilities the test chooses, and review rows built from partial
dicts. A plain module, not a conftest (repo rule); the test files do `from live_review_fixtures
import ...` (pytest puts this folder on `sys.path`; `tests/app` adds it by hand like
`test_app_live.py` does). No data drive, no network, no W&B.

**The season world** (`season_world`) is the one fixture the store, the runner and the app tests
share. Every decision there has its own yards-to-goal, so a test names the engine's answer per
play (`SEASON_SPECS`, keyed by the yard line), and every expectation in the tests is worked by
hand from this table (HOM = "Home Coach", AWY = "Away Coach"; `go` / `fg` / `punt` are the
engine's win chances, `coach` is what the team did):

  week 1, `2025_01_AWY_HOM` (HOM at home)
    p1  HOM  4th & 2 @40  go .62 fg .58 punt .50 Confident  coach go    (converted, +3)
    p2  HOM  4th & 6 @55  go .55 fg --  punt .52 Lean       coach punt  (AWY ball at own 20)
    p3  AWY  4th & 1 @30  go .66 fg .60 punt .40 Confident  coach fg    (good from 48)
    p4  AWY  4th & 10 @60 go .35 fg --  punt .50 Confident  coach punt  (HOM ball at own 25)
    p5  HOM  4th & 3 @25  go .58 fg .57 punt .30 Toss-up    coach go    (stopped, +1)
    p6  AWY  4th & 8 @45  go .40 fg .45 punt .48 Lean       coach go    (stopped, +2)
    (a kneel and a delay of game on 4th down are not decisions)
  week 2, `2025_02_HOM_AWY` (AWY at home)
    p7  AWY  4th & 2 @35  go .60 fg .50 punt .45 Confident  coach go    (converted, +4)
    p8  AWY  4th & 5 @50  go .55 fg .40 punt .50 Confident  coach punt
    p9  HOM  4th & 7 @65  go .30 fg --  punt .45 Confident  coach punt
  week 4 (`with_week4`), `2025_04_AWY_HOM` (week 3 has no plays)
    p10 HOM  4th & 1 @20  go .70 fg .60 punt .40 Confident  coach go    (converted, +2)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from live_stubs import PLAY_SCHEMA, make_models

from nflengine.live import data, review
from nflengine.live.decide import Decision
from nflengine.paths import DataPaths

EXTRA_SCHEMA: dict[str, pl.DataType] = {
    "aborted_play": pl.Float64, "fourth_down_converted": pl.Float64, "home_coach": pl.String,
    "away_coach": pl.String, "posteam_score": pl.Float64, "defteam_score": pl.Float64,
}  # fmt: skip
SCHEMA: dict[str, pl.DataType] = {
    k: v for k, v in PLAY_SCHEMA.items() if k not in ("neutral_site", "i")
} | EXTRA_SCHEMA
assert set(data.PLAY_COLUMNS) | set(review.EXTRA_COLUMNS) <= set(SCHEMA)

KICKOFF0 = datetime(2025, 9, 7, 17, 0, tzinfo=UTC)
GAME_COLUMNS = {
    "game_id": pl.String, "season": pl.Int32, "week": pl.Int32, "game_type": pl.String,
    "home_team": pl.String, "away_team": pl.String, "home_score": pl.Int32,
    "away_score": pl.Int32, "neutral_site": pl.Boolean,
    "kickoff_utc": pl.Datetime("us", "UTC"),
}  # fmt: skip


class World:
    """Curated plays and games built play by play (`add`), written with `write` (one parquet per
    season under `curated/plays/`, `curated/games.parquet`) and read back with `load`."""

    def __init__(self, season: int = 2025) -> None:
        self.season = season
        self.games: dict[str, dict[str, Any]] = {}
        self.rows: list[dict[str, Any]] = []
        self._n: dict[str, int] = {}

    def game(
        self,
        gid: str,
        week: int,
        home: str = "HOM",
        away: str = "AWY",
        *,
        season: int | None = None,
        home_coach: str = "Home Coach",
        away_coach: str = "Away Coach",
        home_score: int = 24,
        away_score: int = 17,
        kickoff: datetime | None = None,
        season_type: str = "REG",
        neutral: bool = False,
    ) -> str:
        self.games[gid] = {
            "game_id": gid, "season": season or self.season, "week": week, "game_type": season_type,
            "home_team": home, "away_team": away, "home_coach": home_coach,
            "away_coach": away_coach, "home_score": home_score, "away_score": away_score,
            "neutral_site": neutral,
            "kickoff_utc": kickoff
            or KICKOFF0 + timedelta(days=7 * (week - 1), minutes=len(self.games)),
        }  # fmt: skip
        return gid

    def add(
        self,
        gid: str,
        team: str,
        ptype: str | None,
        down: int | None = 4,
        ytg: int | None = None,
        yl: int | None = None,
        **kw: Any,
    ) -> int:
        """One play at the end of the game (qtr 3, 25:00 left, tied 10-10 unless `kw` says
        otherwise); returns its `play_id`."""
        g = self.games[gid]
        n = self._n[gid] = self._n.get(gid, 0) + 1
        row: dict[str, Any] = {
            "game_id": gid, "play_id": n, "order_sequence": n, "season": g["season"],
            "season_type": g["game_type"], "week": g["week"], "qtr": 3, "down": down,
            "ydstogo": ytg, "yardline_100": yl, "posteam": team,
            "defteam": g["away_team"] if team == g["home_team"] else g["home_team"],
            "home_team": g["home_team"], "away_team": g["away_team"], "play_type": ptype,
            "game_seconds_remaining": 1500, "half_seconds_remaining": 1500,
            "score_differential": 0, "posteam_timeouts_remaining": 3,
            "defteam_timeouts_remaining": 3, "spread_line": 3.0, "total_line": 44.0,
            "roof": "outdoors", "result": g["home_score"] - g["away_score"],
            "two_point_attempt": 0, "vegas_wp": 0.55, "home_coach": g["home_coach"],
            "away_coach": g["away_coach"], "posteam_score": 10, "defteam_score": 10,
        }  # fmt: skip
        row.update(kw)
        unknown = set(row) - set(SCHEMA)
        assert not unknown, f"unknown play columns {unknown}"
        for k, v in row.items():
            if SCHEMA[k] == pl.Float64 and isinstance(v, int) and not isinstance(v, bool):
                row[k] = float(v)
        self.rows.append(row)
        return n

    # -- decisions in the shorthand the tests use
    def go(self, gid, team, ytg, yl, *, converted, gained, ptype="run", **kw) -> int:
        """A 4th-down run or pass."""
        return self.add(
            gid, team, ptype, 4, ytg, yl, yards_gained=gained,
            fourth_down_converted=1 if converted else 0, **kw,
        )  # fmt: skip

    def punt(self, gid, team, ytg, yl, **kw) -> int:
        return self.add(
            gid, team, "punt", 4, ytg, yl, kick_distance=40.0,
            desc=kw.pop("desc", "J.Smith punts 40 yards"), **kw,
        )  # fmt: skip

    def kick(self, gid, team, ytg, yl, result="made", **kw) -> int:
        return self.add(
            gid, team, "field_goal", 4, ytg, yl, kick_distance=float(yl + 18),
            field_goal_result=result, **kw,
        )  # fmt: skip

    def snap(self, gid, team, yl, down=1, ytg=10, ptype="run", **kw) -> int:
        """A filler snap (the next snap after a decision decides where the ball goes)."""
        return self.add(gid, team, ptype, down, ytg, yl, **kw)

    # -- files
    def write(self, root: Path) -> DataPaths:
        paths = DataPaths(root)
        plays = paths.curated / "plays"
        plays.mkdir(parents=True, exist_ok=True)
        for season in sorted({r["season"] for r in self.rows}):
            rows = [r for r in self.rows if r["season"] == season]
            pl.DataFrame(
                [{c: r.get(c) for c in SCHEMA} for r in rows], schema=SCHEMA
            ).write_parquet(plays / f"season={season}.parquet")
        games = [{c: g[c] for c in GAME_COLUMNS} for g in self.games.values()]
        pl.DataFrame(games, schema=GAME_COLUMNS).write_parquet(paths.curated / "games.parquet")
        return paths

    def load(self, root: Path, fixes: Path | None = None) -> pl.DataFrame:
        """`review.load_season` on this world (a fixes file that doesn't exist: no fixes)."""
        paths = self.write(root)
        return review.load_season(self.season, paths, fixes or root / "no_fixes.csv")


# --- the stub engine ----------------------------------------------------------------------------
def spec(
    go: float | None = None,
    fg: float | None = None,
    punt: float | None = None,
    *,
    label: str | None = "Confident",
    boot_share: float | None = None,
    convert: float = 0.5,
    fg_make: float | None = 0.8,
    fg_distance: float = 40.0,
    punt_start: float | None = 35.0,
    wp_now: float = 0.5,
) -> dict[str, Any]:
    """What the stub engine says about one 4th down: the win chance after each option (None =
    not priced) and the rest of a `Decision`."""
    return {
        "wp": {"go": go, "fg": fg, "punt": punt}, "label": label, "boot_share": boot_share,
        "convert": convert, "fg_make": fg_make, "fg_distance": fg_distance,
        "punt_start": punt_start, "wp_now": wp_now,
    }  # fmt: skip


DEFAULT_SPEC = spec(0.60, 0.55, 0.50)


class StubEngine:
    """`Engine.fourth_downs(states, bootstrap)` with answers keyed by the state's yards to goal
    (every decision in a test world has its own). `.m` is a stub model bundle (`make_models()`)
    whose meta says which seasons it was fit on, so `calibration` and `in_sample` work."""

    def __init__(
        self,
        by_yl: dict[int, dict[str, Any]] | None = None,
        default: dict[str, Any] | None = None,
        *,
        seasons: range | list[int] | None = None,
        fail_on: tuple[int, ...] = (),
    ) -> None:
        self.by_yl = dict(by_yl or {})
        self.default = default or DEFAULT_SPEC
        self.fail_on = set(fail_on)
        self.calls: list[tuple[int, Any]] = []  # (how many states, the bootstrap flag)
        self.states: list[Any] = []
        self.m = make_models()
        self.m.meta = {"version": "stub", "seasons": list(seasons or range(2010, 2026))}
        self.closed = False

    def decide(self, s) -> Decision:
        sp = self.by_yl.get(s.yardline_100, self.default)
        priced = {k: v for k, v in sp["wp"].items() if v is not None}
        order = sorted(priced, key=lambda k: -priced[k])  # a tie keeps go, fg, punt order
        best = order[0]
        gap = priced[best] - priced[order[1]] if len(order) > 1 else 0.0
        return Decision(
            state=s, wp=dict(sp["wp"]), best=best, gap=gap, label=sp["label"],
            boot_share=sp["boot_share"], convert=sp["convert"], fg_make=sp["fg_make"],
            fg_distance=sp["fg_distance"], punt_start=sp["punt_start"], wp_now=sp["wp_now"],
        )  # fmt: skip

    def fourth_downs(self, states, bootstrap=False):
        self.calls.append((len(states), bootstrap))
        self.states.extend(states)
        out = []
        for s in states:
            if s.yardline_100 in self.fail_on:
                raise RuntimeError("the engine refuses this state")
            out.append(self.decide(s))
        return out

    def close(self) -> None:
        self.closed = True


def factory(engine: Any, version: str = "v1"):
    """An `engine_factory` that hands out `engine` and counts its calls."""
    calls: list[int] = []

    def make() -> tuple[Any, str]:
        calls.append(1)
        return engine, version

    make.calls = calls  # type: ignore[attr-defined]
    return make


# --- the season world ---------------------------------------------------------------------------
SEASON_SPECS: dict[int, dict[str, Any]] = {
    40: spec(0.62, 0.58, 0.50, label="Confident", convert=0.65, wp_now=0.55),  # p1
    55: spec(0.55, None, 0.52, label="Lean", convert=0.55, fg_make=None),  # p2
    30: spec(0.66, 0.60, 0.40, label="Confident", convert=0.70, wp_now=0.60),  # p3
    60: spec(0.35, None, 0.50, label="Confident", convert=0.40, fg_make=None),  # p4
    25: spec(0.58, 0.57, 0.30, label="Toss-up", convert=0.50),  # p5
    45: spec(0.40, 0.45, 0.48, label="Lean", convert=0.30),  # p6
    35: spec(0.60, 0.50, 0.45, label="Confident", convert=0.60),  # p7
    50: spec(0.55, 0.40, 0.50, label="Confident", convert=0.55),  # p8
    65: spec(0.30, None, 0.45, label="Confident", convert=0.35, fg_make=None),  # p9
    20: spec(0.70, 0.60, 0.40, label="Confident", convert=0.75),  # p10
}  # fmt: skip

G1, G2, G4 = "2025_01_AWY_HOM", "2025_02_HOM_AWY", "2025_04_AWY_HOM"


def season_world(*, with_week4: bool = False, season: int = 2025) -> World:
    w = World(season)
    w.game(G1, 1, "HOM", "AWY", home_score=24, away_score=17)
    w.go(G1, "HOM", 2, 40, converted=True, gained=3)  # p1
    w.snap(G1, "HOM", 37)
    w.punt(G1, "HOM", 6, 55)  # p2
    w.snap(G1, "AWY", 80)  # AWY ball at own 20
    w.kick(G1, "AWY", 1, 30)  # p3, good from 48
    w.snap(G1, "HOM", 75)
    w.punt(G1, "AWY", 10, 60)  # p4
    w.snap(G1, "HOM", 75)  # HOM ball at own 25
    w.go(G1, "HOM", 3, 25, converted=False, gained=1, ptype="pass")  # p5
    w.snap(G1, "AWY", 76)
    w.go(G1, "AWY", 8, 45, converted=False, gained=2)  # p6
    w.snap(G1, "HOM", 52)
    w.add(G1, "HOM", "qb_kneel", 4, 1, 70)  # a kneel: not a decision
    w.add(
        G1, "AWY", "no_play", 4, 3, 50,
        desc="(Shotgun) PENALTY on AWY, Delay of Game, 5 yards, enforced at AWY 50 - No Play.",
    )  # fmt: skip
    w.game(G2, 2, "AWY", "HOM", home_coach="Away Coach", away_coach="Home Coach",
           home_score=20, away_score=23)  # fmt: skip
    w.go(G2, "AWY", 2, 35, converted=True, gained=4)  # p7
    w.snap(G2, "AWY", 31)
    w.punt(G2, "AWY", 5, 50)  # p8
    w.snap(G2, "HOM", 80)
    w.punt(G2, "HOM", 7, 65)  # p9
    w.snap(G2, "AWY", 70)
    if with_week4:
        w.game(G4, 4, "HOM", "AWY", home_score=31, away_score=10)
        w.go(G4, "HOM", 1, 20, converted=True, gained=2)  # p10
        w.snap(G4, "HOM", 18)
    return w


def season_engine(**kw: Any) -> StubEngine:
    return StubEngine(SEASON_SPECS, **kw)


# --- review rows --------------------------------------------------------------------------------
ROW_DEFAULTS: dict[str, Any] = {
    "season": 2025, "week": 1, "game_id": "G1", "play_id": None, "i": None, "kickoff_utc": None,
    "posteam": "AAA", "defteam": "BBB", "home_team": "AAA", "away_team": "BBB",
    "coach": "Coach A", "qtr": 3, "clock": "10:00", "half_seconds": 1500.0,
    "game_seconds": 1500.0, "off_score": 10, "def_score": 10, "score_diff": 0, "down": 4,
    "ydstogo": 2, "yardline_100": 40, "situation": "4th & 2 at BBB 40", "choice": "go",
    "fake": False, "aborted": False, "wiped": False, "best": "go", "agree": None,
    "label": "Confident", "gap": 0.05, "boot_share": None, "wp_go": 0.60, "wp_fg": 0.55,
    "wp_punt": 0.50, "wp_now": 0.50, "edge": None, "cost": 0.0, "convert": 0.50,
    "fg_make": 0.80, "fg_distance": 40.0, "punt_start": 35.0, "success": None, "result": "",
    "yards_gained": None, "desc": "",
}  # fmt: skip


def make_rows(specs: list[dict[str, Any]]) -> pl.DataFrame:
    """Review rows (`review.ROW_COLUMNS`) from partial dicts: unset columns come from
    `ROW_DEFAULTS`; `play_id` counts up; `agree` is `best == choice` unless given."""
    rows = []
    for n, s in enumerate(specs, start=1):
        r = {**ROW_DEFAULTS, **s}
        if r["play_id"] is None:
            r["play_id"] = n
        if r["i"] is None:
            r["i"] = n
        if r["agree"] is None:
            r["agree"] = r["best"] == r["choice"]
        rows.append({c: r[c] for c in review.ROW_COLUMNS})
    if not rows:
        return review._empty_rows()  # noqa: SLF001
    return pl.DataFrame(rows, schema=review._ROW_SCHEMA)  # noqa: SLF001
