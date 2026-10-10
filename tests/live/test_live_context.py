"""LD02: the team context behind the Game day call card (live/context.py): "is this team good at
this?" as of the start of a week.

Everything runs on a hand-built curated folder in `tmp_path` (plays for 2023-2026, a schedule, a
coach-fixes file) and a fake engine that says "go" on 4th & 3 or less and inside the 8: no data
drive, no network, no W&B. Each expected number is worked out by hand from the rows in `build()`
(the comments say which rows make it); `PHI`, `DAL` and `NYG` are the three teams, `SEA` has no
plays yet.

The fixture world (the nflverse coach columns, then what `head_coach_fixes.csv` changes):
- 2025: DAL's Mike McCarthy is replaced by "Dave Interim" from week 2.
- 2026: DAL's new coach "Brian Schottenheimer" (nflverse still lists McCarthy) from week 1; PHI's
  "Joe Interim" from week 2 and "Joe Later" from week 3; McCarthy now coaches NYG.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from live_fakes import no_real_network  # noqa: F401  (autouse guard)
from live_stubs import PLAY_DEFAULTS, PLAY_SCHEMA, engine, make_models

from nflengine.live import context as C
from nflengine.live import data as D
from nflengine.paths import DataPaths

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)

# game id -> (season, week, season_type, home, away, home coach, away coach) as nflverse lists them
GAMES = {
    "2025_01_DAL_PHI": (2025, 1, "REG", "PHI", "DAL", "Nick Sirianni", "Mike McCarthy"),
    "2025_02_NYG_DAL": (2025, 2, "REG", "DAL", "NYG", "Mike McCarthy", "Brian Daboll"),
    "2025_19_NYG_PHI": (2025, 19, "POST", "PHI", "NYG", "Nick Sirianni", "Brian Daboll"),
    "2025_00_PHI_DAL": (2025, 1, "PRE", "DAL", "PHI", "Mike McCarthy", "Nick Sirianni"),
    "2024_01_NYG_PHI": (2024, 1, "REG", "PHI", "NYG", "Nick Sirianni", "Brian Daboll"),
    "2023_01_DAL_PHI": (2023, 1, "REG", "PHI", "DAL", "Nick Sirianni", "Mike McCarthy"),
    "2026_01_PHI_DAL": (2026, 1, "REG", "DAL", "PHI", "Mike McCarthy", "Nick Sirianni"),
    "2026_02_NYG_PHI": (2026, 2, "REG", "PHI", "NYG", "Nick Sirianni", "Mike McCarthy"),
    "2026_03_PHI_NYG": (2026, 3, "REG", "NYG", "PHI", "Mike McCarthy", "Nick Sirianni"),
}
# in the schedule, not played: SEA has no plays before week 3
UNPLAYED = {"2026_04_SEA_DAL": (2026, 4, "REG", "DAL", "SEA", "Mike McCarthy", "Mike Macdonald")}

FIXES_CSV = """# hand-checked corrections (a comment line)
season,team,from_week,coach,source
2026,PHI,3,Joe Later,a later change listed first
2025,DAL,2,Dave Interim,interim
2026,DAL,1,Brian Schottenheimer,new hire
2026,PHI,2,Joe Interim,interim
"""

EXTRA_SCHEMA = {
    "home_coach": pl.String, "away_coach": pl.String, "fixed_drive": pl.Float64,
    "fixed_drive_result": pl.String, "drive_inside20": pl.Float64,
    "kicker_player_name": pl.String, "punter_player_id": pl.String,
    "punter_player_name": pl.String, "return_yards": pl.Float64, "touchback": pl.Float64,
    "third_down_converted": pl.Float64, "fourth_down_converted": pl.Float64,
}  # fmt: skip
SCHEMA = {k: v for k, v in PLAY_SCHEMA.items() if k not in ("neutral_site", "i")} | EXTRA_SCHEMA

KICKERS = {
    "ELL": ("K-ELL", "J.Elliott"), "AUB": ("K-AUB", "B.Aubrey"), "GAN": ("K-GAN", "G.Gano"),
    "OLD": ("K-OLD", "O.Old"), "EAR": ("K-EAR", "A.Early"), "LEK": ("K-LEK", "Z.Leak"),
}  # fmt: skip
PUNTERS = {
    "MAN": ("P-MAN", "B.Mann"), "STO": ("P-STO", "J.Stout"), "DIX": ("P-DIX", "R.Dixon"),
    "ZZZ": ("P-ZZZ", "Z.Punt"),
}  # fmt: skip


class Rows:
    """Plays in game order; `order_sequence` / `play_id` count up per game unless given."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._n: dict[str, int] = {}

    def add(self, game, team, ptype, down=4, ytg=None, yl=None, **kw) -> None:
        season, week, stype, home, away, home_coach, away_coach = GAMES[game]
        n = self._n[game] = self._n.get(game, 0) + 1
        row = {
            "game_id": game, "season": season, "week": week, "season_type": stype,
            "posteam": team, "defteam": away if team == home else home,
            "home_team": home, "away_team": away,
            "home_coach": home_coach, "away_coach": away_coach, "play_type": ptype,
            "down": None if down is None else float(down), "ydstogo": ytg,
            "yardline_100": None if yl is None else float(yl),
            "play_id": float(n), "order_sequence": float(n),
        }  # fmt: skip
        self.rows.append({**row, **kw})

    def go(self, game, team, ptype, ytg, yl, conv, **kw) -> None:
        """A 4th-down run or pass (`conv` = nflverse's fourth_down_converted)."""
        self.add(game, team, ptype, 4, ytg, yl, fourth_down_converted=float(conv), **kw)

    def kick(self, game, team, who, dist, result, down=3, ytg=None, yl=None, **kw) -> None:
        kid, name = KICKERS[who]
        self.add(
            game, team, "field_goal", down, ytg, yl, kicker_player_id=kid,
            kicker_player_name=name, kick_distance=float(dist), field_goal_result=result, **kw,
        )  # fmt: skip

    def punt(self, game, team, who, ytg, yl, dist, ret, tb=0, blocked=0, **kw) -> None:
        pid, name = PUNTERS[who]
        self.add(
            game, team, "punt", 4, ytg, yl, punter_player_id=pid, punter_player_name=name,
            kick_distance=float(dist), return_yards=ret, touchback=float(tb),
            punt_blocked=float(blocked), **kw,
        )  # fmt: skip

    def drive(self, game, team, number, result, inside20, snaps=1, down=1) -> None:
        for _ in range(snaps):
            self.add(
                game, team, "run", down, 10, 30, fixed_drive=float(number),
                fixed_drive_result=result, drive_inside20=float(inside20),
            )  # fmt: skip


def build() -> list[dict]:
    r = Rows()
    A, B, C_, P = "2025_01_DAL_PHI", "2025_02_NYG_DAL", "2025_19_NYG_PHI", "2025_00_PHI_DAL"
    # ---- 2025 week 1: PHI (home) v DAL
    r.go(A, "PHI", "run", 1, 40, 1)  # A1  go, converted
    r.go(A, "PHI", "pass", 2, 30, 0)  # A2  go, failed
    r.punt(A, "PHI", "MAN", 8, 60, 50, 5.0)  # A3  net 50 - 5 = 45
    r.kick(A, "DAL", "AUB", 53, "made", down=4, ytg=1, yl=35)  # A4  DAL kicks in a go spot
    r.go(A, "DAL", "pass", 6, 50, 1)  # A5  go, converted, not a spot for the bot
    r.go(A, "DAL", "run", 2, 15, 1)  # A6  go, converted
    r.add(A, "PHI", "no_play", 4, 5, 40)  # A7  a penalty: not a decision
    r.add(A, "PHI", "qb_kneel", 4, 1, 60)  # A8  a kneel: not a decision
    r.add(A, "PHI", "run", 3, 1, 45, third_down_converted=1.0)  # A9  3rd & 1 made
    r.add(A, "DAL", "pass", 3, 2, 45, third_down_converted=0.0)  # A10 3rd & 2 stopped
    r.add(A, "PHI", "pass", 3, 5, 45, third_down_converted=1.0)  # A11 3rd & 5: not short
    r.add(A, "PHI", "run", 3, 2, 45, two_point_attempt=1.0, third_down_converted=1.0)  # A12 2-pt
    r.go(A, "PHI", "pass", 7, 7, 0)  # A13 4th & goal from the 7, failed (a spot: inside the 8)
    for dist, res in ((29, "made"), (30, "made"), (39, "missed"), (40, "made"), (49, "made"),
                      (50, "missed")):  # fmt: skip
        r.kick(A, "PHI", "ELL", dist, res)  # A14-A19 Elliott's 2025 tries, band edges
    r.punt(A, "PHI", "MAN", 9, 80, 62, None, tb=1)  # A21 net 62 - 0 - 20 = 42
    r.kick(A, "PHI", "GAN", 33, "made")  # A24 Gano kicking for PHI in 2025
    r.go(A, "PHI", "run", 5, 3, 0)  # A26 bad row: 5 to go from the 3 (the state rules refuse it)
    r.drive(A, "PHI", 10, "Touchdown", 1, snaps=2)  # R1  one red-zone drive, a touchdown
    r.drive(A, "PHI", 12, "Field goal", 1)  # R2
    r.drive(A, "DAL", 11, "Touchdown", 1)  # R3
    r.add(A, "DAL", "kickoff", None, None, 35, fixed_drive=10.0,
          fixed_drive_result="Touchdown", drive_inside20=1.0)  # fmt: skip  # R5 phantom, no snap
    r.drive(A, "PHI", 14, "Touchdown", 0)  # R6  a touchdown from outside the 20
    # ---- 2025 week 2: DAL (home) v NYG
    r.kick(B, "DAL", "AUB", 38, "missed", down=4, ytg=3, yl=20)  # B1  kicks in a go spot
    r.go(B, "DAL", "run", 1, 10, 1)  # B2
    r.punt(B, "NYG", "STO", 4, 45, 40, 10.0)  # B3  net 30
    r.go(B, "NYG", "pass", 2, 50, 0)  # B4
    r.kick(B, "NYG", "GAN", 51, "made")  # B5
    r.drive(B, "NYG", 10, "Turnover on downs", 1)  # R4  same drive number as R1, another game
    # ---- 2025 week 19 (playoffs): PHI (home) v NYG
    r.go(C_, "PHI", "run", 1, 5, 1)  # C1
    r.punt(C_, "NYG", "STO", 10, 70, 12, None, blocked=1)  # C2  blocked: net 0 (kick_distance 12)
    r.kick(C_, "NYG", "GAN", 43, "blocked", down=4, ytg=3, yl=25)  # C3  a block is a miss
    # ---- 2025 preseason: ignored everywhere
    r.go(P, "PHI", "run", 1, 40, 1)
    # ---- older seasons: only the kickers' career longs
    r.kick("2024_01_NYG_PHI", "NYG", "GAN", 51, "made")
    r.kick("2023_01_DAL_PHI", "DAL", "ELL", 58, "made")
    D_, E, F = "2026_01_PHI_DAL", "2026_02_NYG_PHI", "2026_03_PHI_NYG"
    # ---- 2026 week 1: DAL (home) v PHI
    r.go(D_, "DAL", "run", 1, 30, 1)  # D1
    r.punt(D_, "DAL", "DIX", 7, 55, 45, None)  # D2  net 45
    r.kick(D_, "PHI", "OLD", 56, "made", down=4, ytg=2, yl=38)  # D3  kicks in a go spot
    r.go(D_, "PHI", "run", 5, 45, 0)  # D4
    r.drive(D_, "DAL", 2, "Touchdown", 1)  # R7
    r.drive(D_, "PHI", 3, "Missed field goal", 1)  # R8
    # ---- 2026 week 2: PHI (home) v NYG
    r.go(E, "PHI", "pass", 1, 2, 1)  # E1
    r.go(E, "PHI", "run", 3, 33, 0)  # E2
    r.kick(E, "NYG", "GAN", 40, "made", down=4, ytg=2, yl=22)  # E3  kicks in a go spot
    r.punt(E, "NYG", "STO", 9, 80, 55, 3.0)  # E4  net 52
    r.add(E, "NYG", "pass", 3, 2, 40, third_down_converted=1.0)  # E5
    r.kick(E, "PHI", "EAR", 33, "made", order_sequence=20.0, play_id=20.0)  # E6
    r.add(E, "PHI", "extra_point", None, None, 15, kicker_player_id="K-ELL",
          kicker_player_name="J.Elliott", order_sequence=80.0, play_id=80.0)  # fmt: skip  # E7
    r.drive(E, "PHI", 1, "Touchdown", 1)  # R9
    r.drive(E, "PHI", 5, "Touchdown", 1)  # R10
    # ---- 2026 week 3: the week being played (nothing here may count at week 3)
    r.go(F, "PHI", "run", 1, 20, 1)  # F1
    r.kick(F, "PHI", "LEK", 62, "made")  # F2
    r.add(F, "PHI", "run", 3, 1, 30, third_down_converted=1.0)  # F3
    r.punt(F, "NYG", "ZZZ", 10, 60, 30, 0.0)  # F4
    r.drive(F, "PHI", 7, "Touchdown", 1)  # R11
    return r.rows


def frame(rows: list[dict]) -> pl.DataFrame:
    for row in rows:
        assert set(row) <= set(SCHEMA), f"unknown play columns {set(row) - set(SCHEMA)}"
    full = [{c: {**PLAY_DEFAULTS, **row}.get(c) for c in SCHEMA} for row in rows]
    return pl.DataFrame(full, schema=SCHEMA)


def write_curated(root: Path, rows: list[dict]) -> DataPaths:
    """`curated/plays/season=YYYY.parquet` (one file per season) and `curated/games.parquet`."""
    paths = DataPaths(root)
    plays = paths.curated / "plays"
    plays.mkdir(parents=True, exist_ok=True)
    for season in sorted({r["season"] for r in rows}):
        frame([r for r in rows if r["season"] == season]).write_parquet(
            plays / f"season={season}.parquet"
        )
    games = [
        {
            "game_id": gid, "season": s, "week": w, "game_type": t, "home_team": h,
            "away_team": a, "home_coach": hc, "away_coach": ac, "neutral_site": False,
        }
        for gid, (s, w, t, h, a, hc, ac) in {**GAMES, **UNPLAYED}.items()
        if t != "PRE"
    ]  # fmt: skip
    pl.DataFrame(games).write_parquet(paths.curated / "games.parquet")
    return paths


class FakeEngine:
    """Says "go" on 4th & 3 or less and inside the 8, "punt" otherwise."""

    def __init__(self) -> None:
        self.calls = 0

    def fourth_downs(self, states, bootstrap=False):
        assert bootstrap is False, "the context never asks for the bootstrap"
        self.calls += 1
        return [
            SimpleNamespace(best="go" if s.ydstogo <= 3 or s.yardline_100 <= 8 else "punt")
            for s in states
        ]


@pytest.fixture
def world(tmp_path):
    paths = write_curated(tmp_path / "data", build())
    fixes = tmp_path / "head_coach_fixes.csv"
    fixes.write_text(FIXES_CSV, encoding="utf-8")
    return SimpleNamespace(paths=paths, fixes=fixes, tmp=tmp_path)


def ctx_at(world, week=3, season=2026, eng="fake", **kw):
    eng = FakeEngine() if eng == "fake" else eng
    return C.build_context(
        season, week, paths=world.paths, engine=eng, model_version="v-test",
        fixes_path=world.fixes, now=NOW, **kw,
    )  # fmt: skip


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    """One read-only world and its week-3 context (2026 weeks 1-2 and the 2025 season)."""
    tmp = tmp_path_factory.mktemp("ctx")
    paths = write_curated(tmp / "data", build())
    fixes = tmp / "head_coach_fixes.csv"
    fixes.write_text(FIXES_CSV, encoding="utf-8")
    world = SimpleNamespace(paths=paths, fixes=fixes, tmp=tmp)
    return SimpleNamespace(world=world, ctx=ctx_at(world, 3))


def s(num: int, den: int, rate: float | None) -> dict:
    return {"num": num, "den": den, "rate": rate}


def measure(this, last, lg_this, lg_last) -> dict:
    return {
        "this": s(*this), "last": s(*last), "league_this": s(*lg_this), "league_last": s(*lg_last),
    }  # fmt: skip


NONE = (0, 0, None)


# --- the head-coach fixes ---------------------------------------------------------------------
def test_coach_fixes_reads_the_file(tmp_path) -> None:
    p = tmp_path / "fixes.csv"
    p.write_text(
        "# a comment\nSeason , Team,from_week ,coach,source\n"
        " 2026 , ari ,1, Mike LaFleur ,nflverse lists Gannon\n"
        "2025,TEN,7,,blank coach: dropped\n"
        "# 2025,NYG,11,Mike Kafka,commented out\n",
        encoding="utf-8",
    )
    df = C.coach_fixes(p)
    assert df.columns == ["season", "team", "from_week", "coach"]
    assert df.dtypes == [pl.Int32, pl.String, pl.Int32, pl.String]
    assert df.to_dicts() == [
        {"season": 2026, "team": "ARI", "from_week": 1, "coach": "Mike LaFleur"}
    ], "trimmed, team upper-cased, comments and blank coaches dropped"


@pytest.mark.parametrize(
    "text",
    [
        "",  # an empty file
        "# only comments\n",
        "garbage, not,a csv\x00\n1,2\n",
        "season,team,coach\n2026,ARI,Mike LaFleur\n",  # no from_week
        "season,team,from_week,coach\n2026,ARI,week one,Mike LaFleur\n",  # from_week not a number
    ],
)
def test_coach_fixes_malformed_is_empty_never_an_error(tmp_path, text) -> None:
    p = tmp_path / "bad.csv"
    p.write_text(text, encoding="utf-8")
    df = C.coach_fixes(p)
    assert df.is_empty() and df.columns == ["season", "team", "from_week", "coach"]


def test_coach_fixes_missing_file_and_the_repo_default(tmp_path) -> None:
    assert C.coach_fixes(tmp_path / "nope.csv").is_empty()
    from nflengine.settings import CONFIG_DIR

    assert C.default_fixes_path() == CONFIG_DIR / "head_coach_fixes.csv"
    real = C.coach_fixes()
    assert not real.is_empty(), "the repo's config/head_coach_fixes.csv has rows"
    assert real.filter((pl.col("season") == 2025) & (pl.col("team") == "TEN")).height >= 1


def test_fixes_apply_from_their_week_and_a_later_one_wins(world) -> None:
    """The fixture file lists PHI's week-3 fix before its week-2 one."""
    coach = {wk: ctx_at(world, wk)["teams"]["PHI"]["coach"] for wk in (2, 3, 4)}
    assert coach == {2: "Nick Sirianni", 3: "Joe Interim", 4: "Joe Later"}, (
        "PHI's coach in its newest game before each week"
    )
    # 2025: DAL's McCarthy coached week 1 only; "Dave Interim" the rest of the season
    teams = ctx_at(world, 3)["teams"]
    assert teams["DAL"]["coach_go"]["last"] == s(*NONE), "Schottenheimer has no 2025"
    assert teams["NYG"]["coach_go"]["last"] == s(1, 2, 0.5), (
        "McCarthy's 2025 is DAL's week 1 (A4 kicked, A6 went); B1 and B2 are the interim's"
    )


def test_without_fixes_the_nflverse_coach_stands(world, tmp_path) -> None:
    ctx = C.build_context(
        2026, 3, paths=world.paths, engine=FakeEngine(),
        fixes_path=tmp_path / "none.csv", now=NOW,
    )  # fmt: skip
    teams = ctx["teams"]
    assert (teams["PHI"]["coach"], teams["DAL"]["coach"]) == ("Nick Sirianni", "Mike McCarthy")
    assert teams["DAL"]["coach_last_team"] == "DAL", "McCarthy then coached all of DAL's 2025"


# --- the plain measures ------------------------------------------------------------------------
def test_top_level_fields(shared) -> None:
    ctx = shared.ctx
    assert {k: ctx[k] for k in ("schema", "season", "week", "this_season", "last_season")} == {
        "schema": 1, "season": 2026, "week": 3, "this_season": 2026, "last_season": 2025,
    }  # fmt: skip
    assert ctx["through_week"] == 2
    assert ctx["through"] == "2026 weeks 1-2 and the 2025 season"
    assert ctx["computed_at"] == "2026-10-10T12:00:00+00:00"
    assert ctx["model_version"] == "v-test"
    assert sorted(ctx["teams"]) == ["DAL", "NYG", "PHI", "SEA"], "the schedule's teams"
    assert (ctx["coach_go_scored"], ctx["coach_go_skipped"]) == (23, 1), "A26 can't be scored"


def test_go_rate_and_fourth_down_conversion(shared) -> None:
    """4th downs decided = run / pass / field goal / punt (A7 no_play, A8 kneel, the preseason
    game and week 3 are out). PHI last: A1 A2 A13 A26 C1 went, A3 A21 punted = 5 / 7."""
    t = shared.ctx["teams"]
    lg_this, lg_last = (4, 8, 0.5), (9, 16, 0.5625)
    assert t["PHI"]["go_rate"] == measure((3, 4, 0.75), (5, 7, 0.7143), lg_this, lg_last)
    assert t["DAL"]["go_rate"] == measure((1, 2, 0.5), (3, 5, 0.6), lg_this, lg_last)
    assert t["NYG"]["go_rate"] == measure((0, 2, 0.0), (1, 4, 0.25), lg_this, lg_last)
    assert t["SEA"]["go_rate"] == measure(NONE, NONE, lg_this, lg_last)
    # on go plays only: PHI last A1 and C1 converted of A1 A2 A13 A26 C1
    lg_this, lg_last = (2, 4, 0.5), (5, 9, 0.5556)
    assert t["PHI"]["fourth_conv"] == measure((1, 3, 0.3333), (2, 5, 0.4), lg_this, lg_last)
    assert t["DAL"]["fourth_conv"] == measure((1, 1, 1.0), (3, 3, 1.0), lg_this, lg_last)
    assert t["NYG"]["fourth_conv"] == measure(NONE, (0, 1, 0.0), lg_this, lg_last)


def test_short_yardage_counts_3rd_and_4th_and_2_or_less(shared) -> None:
    """Runs and passes only, no two-point tries (A12), not 3rd & 5 (A11). PHI last: A1 (4th & 1,
    made), A2 (4th & 2, failed), A9 (3rd & 1, made), C1 (4th & 1, made) = 3 / 4."""
    t = shared.ctx["teams"]
    lg_this, lg_last = (3, 3, 1.0), (5, 8, 0.625)
    assert t["PHI"]["short_conv"] == measure((1, 1, 1.0), (3, 4, 0.75), lg_this, lg_last)
    assert t["DAL"]["short_conv"] == measure((1, 1, 1.0), (2, 3, 0.6667), lg_this, lg_last)
    assert t["NYG"]["short_conv"] == measure((1, 1, 1.0), (0, 1, 0.0), lg_this, lg_last)


def test_red_zone_counts_drives_once_and_skips_phantom_drives(shared) -> None:
    """PHI last: drive 10 (two snaps, one drive) is a touchdown, drive 12 a field goal; drive 14
    never reached the 20. DAL last: drive 11 only: the kickoff row under DAL carrying drive 10's
    result (a real nflverse quirk) has no snap and is not a drive."""
    t = shared.ctx["teams"]
    lg_this, lg_last = (3, 4, 0.75), (2, 4, 0.5)
    assert t["PHI"]["red_zone_td"] == measure((2, 3, 0.6667), (1, 2, 0.5), lg_this, lg_last)
    assert t["DAL"]["red_zone_td"] == measure((1, 1, 1.0), (1, 1, 1.0), lg_this, lg_last)
    assert t["NYG"]["red_zone_td"] == measure(NONE, (0, 1, 0.0), lg_this, lg_last)


def test_a_team_with_no_plays_yet_is_all_zeros(shared) -> None:
    sea = shared.ctx["teams"]["SEA"]
    assert sea["team"] == "SEA"
    assert sea["coach"] == "Mike Macdonald", "no games yet: its first game in the schedule"
    assert sea["coach_last_team"] is None
    for name in ("go_rate", "fourth_conv", "short_conv", "red_zone_td"):
        assert sea[name]["this"] == s(*NONE) and sea[name]["last"] == s(*NONE), name
    assert sea["coach_go"]["this"] == s(*NONE) and sea["coach_go"]["last"] == s(*NONE)
    assert [b["this"] for b in sea["coach_go_by_distance"]] == [s(*NONE)] * 3
    assert sea["kicker"] is None and sea["punter"] is None


# --- the coach ---------------------------------------------------------------------------------
def test_coach_is_the_newest_games_coach_with_fixes_applied(shared) -> None:
    t = shared.ctx["teams"]
    assert t["PHI"]["coach"] == "Joe Interim", "week 2 is PHI's newest game: the interim"
    assert t["DAL"]["coach"] == "Brian Schottenheimer", "nflverse still lists McCarthy: renamed"
    assert t["NYG"]["coach"] == "Mike McCarthy", "he moved to NYG in 2026"


def test_coach_last_team_is_where_he_coached_most_4th_downs_last_season(shared) -> None:
    t = shared.ctx["teams"]
    assert t["NYG"]["coach_last_team"] == "DAL", "McCarthy: 3 decided 4th downs for DAL in week 1"
    assert t["PHI"]["coach_last_team"] is None, "Joe Interim was not a head coach in 2025"
    assert t["DAL"]["coach_last_team"] is None, "nor was Schottenheimer"
    assert t["SEA"]["coach_last_team"] is None


def test_coach_go_counts_the_coachs_own_4th_downs_in_the_bots_go_spots(shared) -> None:
    """Spots (the fake engine: 4th & 3 or less, or inside the 8): this season D1 D3 E1 E2 E3, of
    which D1 E1 E2 went = 3 / 5 for the league; last season 10 spots, 7 went. Joe Interim has
    E1 and E2 (both went); McCarthy's NYG E3 kicked; his 2025 DAL week 1: A4 kicked, A6 went."""
    t = shared.ctx["teams"]
    lg_this, lg_last = (3, 5, 0.6), (7, 10, 0.7)
    assert t["PHI"]["coach_go"] == measure((2, 2, 1.0), NONE, lg_this, lg_last)
    assert t["DAL"]["coach_go"] == measure((1, 1, 1.0), NONE, lg_this, lg_last)
    assert t["NYG"]["coach_go"] == measure((0, 1, 0.0), (1, 2, 0.5), lg_this, lg_last)


def test_coach_go_by_distance(shared) -> None:
    """Bands by yards to go: league last 1-2: 7 spots (6 went), 3-5: B1 and C3 kicked (0 / 2),
    6+: A13 (4th & 7 from the 7) went (1 / 1)."""
    lg = {"1-2": s(6, 7, 0.8571), "3-5": s(0, 2, 0.0), "6+": s(1, 1, 1.0)}

    def rows(team: str) -> list[tuple]:
        out = shared.ctx["teams"][team]["coach_go_by_distance"]
        assert [b["band"] for b in out] == list(C.BANDS_DIST)
        assert all(b["league_last"] == lg[b["band"]] for b in out)
        return [(b["band"], b["this"], b["last"]) for b in out]

    zero = s(*NONE)
    assert rows("PHI") == [
        ("1-2", s(1, 1, 1.0), zero), ("3-5", s(1, 1, 1.0), zero), ("6+", zero, zero),
    ]  # fmt: skip
    assert rows("NYG") == [
        ("1-2", s(0, 1, 0.0), s(1, 2, 0.5)), ("3-5", zero, zero), ("6+", zero, zero),
    ]  # fmt: skip
    assert rows("DAL") == [("1-2", s(1, 1, 1.0), zero), ("3-5", zero, zero), ("6+", zero, zero)]


def test_without_an_engine_the_coach_go_is_none(world) -> None:
    ctx = ctx_at(world, 3, eng=None)
    for team in ctx["teams"].values():
        assert team["coach_go"] is None and team["coach_go_by_distance"] == []
        assert team["coach"] is not None
    assert ctx["teams"]["PHI"]["go_rate"]["this"] == s(3, 4, 0.75)
    assert ctx["league"]["coach_go"] is None
    assert (ctx["coach_go_scored"], ctx["coach_go_skipped"]) == (0, 0)


def test_one_state_the_engine_refuses_is_skipped_not_fatal(world) -> None:
    """The engine raises on E2 (the 4th & 3 from the 33): the batch retries state by state."""

    class Picky(FakeEngine):
        def fourth_downs(self, states, bootstrap=False):
            if any(st.yardline_100 == 33 for st in states):
                raise ValueError("refused")
            return super().fourth_downs(states, bootstrap)

    ctx = ctx_at(world, 3, eng=Picky())
    assert ctx["coach_go_skipped"] == 2 and ctx["coach_go_scored"] == 22
    assert ctx["teams"]["PHI"]["coach_go"]["this"] == s(1, 1, 1.0), "only E1 is left"
    assert ctx["teams"]["PHI"]["go_rate"]["this"] == s(3, 4, 0.75), "the plain measures don't care"


# --- the kicker --------------------------------------------------------------------------------
LEAGUE_KICKS_LAST = {
    "<30": s(1, 1, 1.0), "30-39": s(2, 4, 0.5), "40-49": s(2, 3, 0.6667), "50+": s(2, 3, 0.6667),
}  # fmt: skip


def band_rows(kicker: dict) -> dict:
    assert [b["band"] for b in kicker["bands"]] == list(C.BANDS_KICK)
    return {b["band"]: (b["this"], b["last"], b["league_last"]) for b in kicker["bands"]}


def test_kicker_is_the_newest_kick_even_an_extra_point(shared) -> None:
    """PHI's week 2: A.Early's field goal (order 20) then J.Elliott's extra point (order 80)."""
    k = shared.ctx["teams"]["PHI"]["kicker"]
    assert k["name"] == "J.Elliott"
    zero = s(*NONE)
    # Elliott has only the extra point in 2026; his 2025 tries sit on the band edges: 29 made,
    # 30 made, 39 missed, 40 made, 49 made, 50 missed
    assert band_rows(k) == {
        "<30": (zero, s(1, 1, 1.0), LEAGUE_KICKS_LAST["<30"]),
        "30-39": (zero, s(1, 2, 0.5), LEAGUE_KICKS_LAST["30-39"]),
        "40-49": (zero, s(2, 2, 1.0), LEAGUE_KICKS_LAST["40-49"]),
        "50+": (zero, s(0, 1, 0.0), LEAGUE_KICKS_LAST["50+"]),
    }


def test_kicker_career_long_spans_seasons_and_teams(shared) -> None:
    t = shared.ctx["teams"]
    elliott = t["PHI"]["kicker"]
    assert (elliott["long"], elliott["long_season"]) == (58, 2023), "58 for DAL in 2023"
    assert (t["DAL"]["kicker"]["long"], t["DAL"]["kicker"]["long_season"]) == (53, 2025)
    gano = t["NYG"]["kicker"]
    assert (gano["long"], gano["long_season"]) == (51, 2025), "51 in 2024 and 2025: the latest"


def test_kicker_falls_back_to_last_seasons_newest_and_counts_blocks_as_misses(shared) -> None:
    t = shared.ctx["teams"]
    dal = t["DAL"]["kicker"]
    assert dal["name"] == "B.Aubrey", "DAL has no kick in 2026: B.Aubrey kicked last in 2025"
    zero = s(*NONE)
    rows = band_rows(dal)
    assert rows["30-39"][:2] == (zero, s(0, 1, 0.0)), "B1: 38 yards, missed"
    assert rows["50+"][:2] == (zero, s(1, 1, 1.0)), "A4: 53 yards, made"
    assert t["NYG"]["kicker"]["name"] == "G.Gano"
    gano = band_rows(t["NYG"]["kicker"])
    assert gano["40-49"][:2] == (s(1, 1, 1.0), s(0, 1, 0.0)), "E3 made from 40; C3 blocked from 43"
    assert gano["30-39"][:2] == (zero, s(1, 1, 1.0)), "A24: his kick for PHI counts (any team)"
    assert gano["50+"][:2] == (zero, s(1, 1, 1.0)), "B5: 51 yards"
    assert all(v[2] == LEAGUE_KICKS_LAST[b] for b, v in gano.items())


# --- the punter --------------------------------------------------------------------------------
def test_punter_net_average_and_the_formula_pieces(shared) -> None:
    """net = (kick distance - return yards - 20 per touchback) / punts; a blocked punt counts 0
    yards, a missing return 0. Mann 2025: A3 50 - 5 = 45 and A21 62 - 0 - 20 = 42 -> 43.5 (gross
    56.0). Stout 2025: B3 40 - 10 = 30 and C2 blocked (12 recorded, counts 0) -> 15.0 (gross 26.0
    = (40 + 12) / 2: blocked punts have 0 yards in nflverse, so gross doesn't special-case them).
    League last: (45 + 42 + 30 + 0) / 4 = 29.25; this: (45 + 52) / 2 = 48.5."""
    t = shared.ctx["teams"]
    no_punts = {"punts": 0, "net": None, "gross": None}
    lg_this = {"punts": 2, "net": 48.5, "gross": 50.0}
    lg_last = {"punts": 4, "net": 29.25, "gross": 41.0}
    assert t["PHI"]["punter"] == {
        "name": "B.Mann", "this": no_punts, "last": {"punts": 2, "net": 43.5, "gross": 56.0},
        "league_this": lg_this, "league_last": lg_last,
    }  # PHI has no punt in 2026: last season's newest punter  # fmt: skip
    assert t["NYG"]["punter"] == {
        "name": "J.Stout", "this": {"punts": 1, "net": 52.0, "gross": 55.0},
        "last": {"punts": 2, "net": 15.0, "gross": 26.0},
        "league_this": lg_this, "league_last": lg_last,
    }  # fmt: skip
    assert t["DAL"]["punter"]["name"] == "R.Dixon"
    assert t["DAL"]["punter"]["this"] == {"punts": 1, "net": 45.0, "gross": 45.0}
    assert t["DAL"]["punter"]["last"] == no_punts


def test_league_block(shared) -> None:
    lg = shared.ctx["league"]
    assert lg["go_rate"] == {"this": s(4, 8, 0.5), "last": s(9, 16, 0.5625)}
    assert lg["fourth_conv"] == {"this": s(2, 4, 0.5), "last": s(5, 9, 0.5556)}
    assert lg["short_conv"] == {"this": s(3, 3, 1.0), "last": s(5, 8, 0.625)}
    assert lg["red_zone_td"] == {"this": s(3, 4, 0.75), "last": s(2, 4, 0.5)}
    assert lg["coach_go"] == {"this": s(3, 5, 0.6), "last": s(7, 10, 0.7)}
    assert [(b["band"], b["this"], b["last"]) for b in lg["kicker_bands"]] == [
        ("<30", s(*NONE), s(1, 1, 1.0)),
        ("30-39", s(1, 1, 1.0), s(2, 4, 0.5)),
        ("40-49", s(1, 1, 1.0), s(2, 3, 0.6667)),
        ("50+", s(1, 1, 1.0), s(2, 3, 0.6667)),
    ]
    assert lg["punting"]["last"]["punts"] == 4


# --- leakage -----------------------------------------------------------------------------------
def test_nothing_from_the_week_being_played_or_later_counts(shared, tmp_path) -> None:
    """The same world without the whole of game F (2026 week 3) gives the identical context at
    week 3: F's run, field goal (a 62-yarder), punt and red-zone touchdown change nothing."""
    rows = [r for r in build() if r["game_id"] != "2026_03_PHI_NYG"]
    paths = write_curated(tmp_path / "without", rows)
    world = SimpleNamespace(paths=paths, fixes=shared.world.fixes)
    assert ctx_at(world, 3) == shared.ctx
    # ...and at week 4 those plays are history
    full = ctx_at(shared.world, 4)
    phi = full["teams"]["PHI"]
    assert full["through"] == "2026 weeks 1-3 and the 2025 season"
    assert phi["go_rate"]["this"] == s(4, 5, 0.8)
    assert phi["kicker"]["name"] == "Z.Leak" and phi["kicker"]["long"] == 62
    assert full["teams"]["NYG"]["punter"]["name"] == "Z.Punt"
    assert phi["coach"] == "Joe Later"


def test_week_one_has_only_last_season_and_the_schedules_coaches(world) -> None:
    ctx = ctx_at(world, 1)
    assert ctx["through_week"] is None
    assert ctx["through"] == "the 2025 season (no 2026 games yet)"
    t = ctx["teams"]
    assert t["PHI"]["go_rate"] == measure(NONE, (5, 7, 0.7143), NONE, (9, 16, 0.5625))
    assert t["PHI"]["coach"] == "Nick Sirianni", "first game in the schedule, before the interim"
    assert t["DAL"]["coach"] == "Brian Schottenheimer", "the schedule's coach with the fix applied"
    assert t["NYG"]["coach"] == "Mike McCarthy", "NYG's first game is week 2"
    assert t["NYG"]["coach_go"]["this"] == s(*NONE)
    assert t["NYG"]["coach_go"]["last"] == s(1, 2, 0.5)
    assert t["PHI"]["kicker"]["name"] == "G.Gano", "last season's newest PHI kick (A24)"
    assert t["PHI"]["kicker"]["long"] == 51
    assert ctx["league"]["coach_go"]["this"] == s(*NONE)


def test_one_game_in_gives_a_singular_week(world) -> None:
    assert ctx_at(world, 2)["through"] == "2026 week 1 and the 2025 season"


def test_last_season_with_no_plays_file_is_all_zero(world) -> None:
    (world.paths.curated / "plays" / "season=2025.parquet").unlink()
    ctx = ctx_at(world, 3)
    assert ctx["teams"]["NYG"]["coach_go"]["last"] == s(*NONE)
    assert ctx["teams"]["PHI"]["go_rate"]["last"] == s(*NONE)
    assert ctx["teams"]["PHI"]["go_rate"]["this"] == s(3, 4, 0.75)


# --- the real engine ---------------------------------------------------------------------------
def test_coach_go_follows_the_real_engines_calls(world) -> None:
    """With the stub models' engine the league totals equal an independent count of its own
    "go" calls over the same 4th downs."""
    paths = world.paths
    frame = D.fourth_frame(D.with_state(D.load_plays([2025, 2026], paths)))
    frame = frame.filter(
        (pl.col("season") == 2025) | ((pl.col("season") == 2026) & (pl.col("week") < 3))
    )
    with engine(make_models()) as eng:
        want = {"this": [0, 0], "last": [0, 0]}
        skipped = 0
        for row in frame.iter_rows(named=True):
            try:
                state = D.state_of_row(row)
            except ValueError:
                skipped += 1
                continue
            if eng.fourth_downs([state], bootstrap=False)[0].best == "go":
                win = want["this" if row["season"] == 2026 else "last"]
                win[1] += 1
                win[0] += int(row["choice"] == "go")
        ctx = ctx_at(world, 3, eng=eng)
    assert skipped == 1
    got = ctx["league"]["coach_go"]
    assert [got["this"]["num"], got["this"]["den"]] == want["this"]
    assert [got["last"]["num"], got["last"]["den"]] == want["last"]
    assert got["this"]["den"] + got["last"]["den"] > 0, "the stub engine says go somewhere"


# --- team_context ------------------------------------------------------------------------------
def test_team_context_is_the_endpoints_shape(shared) -> None:
    out = C.team_context(shared.ctx, "PHI")
    assert list(out) == [
        "season", "week", "this_season", "last_season", "through_week", "through", "team",
        "computed_at", "model_version",
    ]  # fmt: skip
    assert out["team"] is shared.ctx["teams"]["PHI"]
    assert (out["season"], out["week"], out["through_week"]) == (2026, 3, 2)
    assert out["through"] == shared.ctx["through"] and out["model_version"] == "v-test"
    assert C.team_context(shared.ctx, "phi")["team"]["team"] == "PHI"


def test_team_context_unknown_team_is_none(shared) -> None:
    assert C.team_context(shared.ctx, "ZZZ") is None
    assert C.team_context(shared.ctx, "") is None
    assert C.team_context({}, "PHI") is None


def test_the_context_is_plain_json_without_masked_field_names(shared) -> None:
    text = json.dumps(shared.ctx, allow_nan=False)
    assert json.loads(text) == shared.ctx, "ints, floats, None and strings round-trip exactly"
    names: set[str] = set()

    def walk(x) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                names.add(k.lower())
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(shared.ctx)
    walk(C.source_stamp(2026, shared.world.paths, shared.world.fixes, "v"))
    banned = ("key", "token", "auth", "secret", "password", "credential")
    assert not [n for n in names if any(b in n for b in banned)]


# --- source_stamp ------------------------------------------------------------------------------
def test_source_stamp_names_what_the_answer_depends_on(world) -> None:
    st = C.source_stamp(2026, world.paths, world.fixes, "v1")
    plays = world.paths.curated / "plays"
    assert set(st) == {"plays", "plays_last", "games", "fixes", "model_version"}
    assert st["plays"]["size"] == (plays / "season=2026.parquet").stat().st_size
    assert st["plays"]["mtime_ns"] == (plays / "season=2026.parquet").stat().st_mtime_ns
    assert st["plays_last"]["size"] == (plays / "season=2025.parquet").stat().st_size
    assert st["games"]["size"] == (world.paths.curated / "games.parquet").stat().st_size
    assert st["fixes"]["size"] == world.fixes.stat().st_size
    assert st["model_version"] == "v1"
    assert json.loads(json.dumps(st)) == st


def test_source_stamp_missing_files_are_none(world, tmp_path) -> None:
    st = C.source_stamp(2030, world.paths, tmp_path / "none.csv", None)
    assert st["plays"] is None and st["plays_last"] is None and st["fixes"] is None
    assert st["model_version"] is None and st["games"] is not None


def test_source_stamp_moves_when_a_file_does(world) -> None:
    before = C.source_stamp(2026, world.paths, world.fixes, "v1")
    world.fixes.write_text(FIXES_CSV + "2026,NYG,1,Another Coach,x\n", encoding="utf-8")
    after = C.source_stamp(2026, world.paths, world.fixes, "v1")
    assert after["fixes"] != before["fixes"]
    assert {k: v for k, v in after.items() if k != "fixes"} == {
        k: v for k, v in before.items() if k != "fixes"
    }
    assert C.source_stamp(2026, world.paths, world.fixes, "v2") != after


# --- the cache ---------------------------------------------------------------------------------
class Builder:
    """A fake `build_context` that counts its calls (and can dawdle)."""

    def __init__(self, delay: float = 0.0, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.delay, self.fail = delay, fail
        self._lock = threading.Lock()
        self.started = threading.Event()

    def __call__(self, season, week, **kw) -> dict:
        with self._lock:
            self.calls.append(kw)
        self.started.set()
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        return {
            "schema": C.SCHEMA_VERSION, "season": season, "week": week, "this_season": season,
            "last_season": season - 1, "through_week": None, "through": "x",
            "computed_at": "t", "model_version": kw.get("model_version"),
            "teams": {"PHI": {"team": "PHI"}}, "league": {},
        }  # fmt: skip

    @property
    def n(self) -> int:
        return len(self.calls)


def factory(version="v1", engine_obj=None):
    calls = []

    def make():
        calls.append(1)
        return engine_obj, version

    make.calls = calls
    return make


def snapshot(root: Path) -> dict[Path, int]:
    return {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}


def test_cache_memory_hit_builds_once(world) -> None:
    build, fac = Builder(), factory()
    cache = C.ContextCache(None, build)
    first = cache.get(2026, 3, paths=world.paths, engine_factory=fac)
    second = cache.get(2026, 3, paths=world.paths, engine_factory=fac)
    assert second is first and build.n == 1 and len(fac.calls) == 1
    assert first["model_version"] == "v1", "the factory's version reaches the build"
    cache.get(2026, 4, paths=world.paths, engine_factory=fac)
    assert build.n == 2, "another week is another answer"


def test_cache_passes_the_engine_and_the_fixes_path_to_the_build(world) -> None:
    sentinel = object()
    build = Builder()
    C.ContextCache(None, build).get(
        2026, 3, paths=world.paths, engine_factory=factory("v9", sentinel)
    )
    assert build.calls[0] == {"paths": world.paths, "engine": sentinel, "model_version": "v9"}
    build = Builder()
    C.ContextCache(None, build, fixes_path=world.fixes).get(
        2026, 3, paths=world.paths, engine_factory=factory()
    )
    assert build.calls[0]["fixes_path"] == world.fixes


def test_cache_disk_hit_does_not_call_build_or_the_factory(world, tmp_path) -> None:
    folder = tmp_path / "cache"
    first = C.ContextCache(folder, Builder()).get(
        2026, 3, paths=world.paths, engine_factory=factory()
    )
    path = folder / "2026-w03.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert set(doc) == {"schema", "season", "week", "stamp", "context"}
    assert (doc["schema"], doc["season"], doc["week"]) == (1, 2026, 3)
    assert doc["stamp"] == C.source_stamp(2026, world.paths, None, "v1")
    build = Builder(fail=True)

    def no_factory():
        raise AssertionError("the engine factory runs only for a build")

    again = C.ContextCache(folder, build).get(2026, 3, paths=world.paths, engine_factory=no_factory)
    assert again == first and build.n == 0


@pytest.mark.parametrize("what", ["plays", "plays_last", "games", "fixes"])
def test_cache_rebuilds_when_a_source_file_changes(world, tmp_path, what) -> None:
    build, fac = Builder(), factory()
    cache = C.ContextCache(tmp_path / "cache", build, fixes_path=world.fixes)
    cache.get(2026, 3, paths=world.paths, engine_factory=fac)
    cache.get(2026, 3, paths=world.paths, engine_factory=fac)
    assert build.n == 1
    target = {
        "plays": world.paths.curated / "plays" / "season=2026.parquet",
        "plays_last": world.paths.curated / "plays" / "season=2025.parquet",
        "games": world.paths.curated / "games.parquet",
        "fixes": world.fixes,
    }[what]
    target.write_bytes(target.read_bytes() + b"\n")  # a different size (and mtime)
    cache.get(2026, 3, paths=world.paths, engine_factory=fac)
    assert build.n == 2, f"a changed {what} file must rebuild (memory)"
    fresh = C.ContextCache(tmp_path / "cache", build, fixes_path=world.fixes)
    fresh.get(2026, 3, paths=world.paths, engine_factory=fac)
    assert build.n == 2, "the rebuilt answer was written to disk with the new stamp"


def test_cache_rebuilds_when_only_the_mtime_changes(world, tmp_path) -> None:
    build, fac = Builder(), factory()
    cache = C.ContextCache(tmp_path / "cache", build)
    cache.get(2026, 3, paths=world.paths, engine_factory=fac)
    plays = world.paths.curated / "plays" / "season=2026.parquet"
    st = plays.stat()
    os.utime(plays, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    cache.get(2026, 3, paths=world.paths, engine_factory=fac)
    assert build.n == 2


def test_cache_a_missing_data_file_is_a_stamp_change_too(world, tmp_path) -> None:
    build, fac = Builder(), factory()
    cache = C.ContextCache(tmp_path / "cache", build)
    cache.get(2026, 3, paths=world.paths, engine_factory=fac)
    (world.paths.curated / "plays" / "season=2025.parquet").unlink()
    cache.get(2026, 3, paths=world.paths, engine_factory=fac)
    assert build.n == 2


def test_cache_model_version_decides_only_when_the_caller_names_one(world, tmp_path) -> None:
    build = Builder()
    cache = C.ContextCache(None, build)
    got = cache.get(2026, 3, paths=world.paths, engine_factory=factory("v1"))
    assert got["model_version"] == "v1"
    cache.get(2026, 3, paths=world.paths, engine_factory=factory("v2"))
    assert build.n == 1, "no version asked for: the files decide"
    cache.get(2026, 3, paths=world.paths, engine_factory=factory("v1"), model_version="v1")
    assert build.n == 1, "the same bundle"
    new = cache.get(2026, 3, paths=world.paths, engine_factory=factory("v2"), model_version="v2")
    assert build.n == 2 and new["model_version"] == "v2", "a promoted bundle rebuilds"
    # and from disk
    disk = C.ContextCache(tmp_path / "cache", build)
    disk.get(2026, 3, paths=world.paths, engine_factory=factory("v2"), model_version="v2")
    fresh = C.ContextCache(tmp_path / "cache", build)
    fresh.get(2026, 3, paths=world.paths, engine_factory=factory("v3"), model_version="v3")
    assert build.n == 4, "the stored v2 answer is not v3's"


def break_file(path: Path, how: str) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if how == "corrupt":
        path.write_text('{"schema": 1, "season": 20', encoding="utf-8")
        return
    if how == "empty":
        path.write_text("", encoding="utf-8")
        return
    if how == "list":
        path.write_text("[1, 2, 3]", encoding="utf-8")
        return
    if how == "binary":
        path.write_bytes(b"\xff\xfe\x00\x01")
        return
    if how == "schema":
        doc["schema"] = 99
    elif how == "season":
        doc["season"] = 2025
    elif how == "week":
        doc["week"] = 4
    elif how == "stamp":
        doc["stamp"]["plays"] = {"mtime_ns": 1, "size": 1}
    elif how == "no_stamp":
        del doc["stamp"]
    elif how == "context_schema":
        doc["context"]["schema"] = 2
    elif how == "context_week":
        doc["context"]["week"] = 9
    elif how == "no_teams":
        doc["context"]["teams"] = []
    elif how == "no_context":
        doc["context"] = None
    path.write_text(json.dumps(doc), encoding="utf-8")


@pytest.mark.parametrize(
    "how",
    ["corrupt", "empty", "list", "binary", "schema", "season", "week", "stamp", "no_stamp",
     "context_schema", "context_week", "no_teams", "no_context"],
)  # fmt: skip
def test_cache_a_bad_file_is_a_miss_and_is_replaced(world, tmp_path, how) -> None:
    folder = tmp_path / "cache"
    C.ContextCache(folder, Builder()).get(2026, 3, paths=world.paths, engine_factory=factory())
    path = folder / "2026-w03.json"
    break_file(path, how)
    build = Builder()
    got = C.ContextCache(folder, build).get(2026, 3, paths=world.paths, engine_factory=factory())
    assert build.n == 1 and got["week"] == 3, f"{how}: rebuilt"
    rebuilt = json.loads(path.read_text(encoding="utf-8"))
    assert rebuilt["context"] == got and rebuilt["schema"] == 1, "and the file is whole again"
    assert not [p for p in folder.iterdir() if p.name != "2026-w03.json"]


def test_cache_a_files_from_another_week_is_not_this_weeks(world, tmp_path) -> None:
    folder = tmp_path / "cache"
    C.ContextCache(folder, Builder()).get(2026, 3, paths=world.paths, engine_factory=factory())
    (folder / "2026-w04.json").write_bytes((folder / "2026-w03.json").read_bytes())
    build = Builder()
    got = C.ContextCache(folder, build).get(2026, 4, paths=world.paths, engine_factory=factory())
    assert build.n == 1 and got["week"] == 4


def test_cache_writes_only_inside_its_folder_and_leaves_no_temp_files(world, tmp_path) -> None:
    folder = tmp_path / "scratch" / "live" / "context"
    before = snapshot(tmp_path)
    cache = C.ContextCache(folder, Builder())
    cache.get(2026, 3, paths=world.paths, engine_factory=factory())
    cache.get(2026, 4, paths=world.paths, engine_factory=factory())
    after = snapshot(tmp_path)
    new = set(after) - set(before)
    assert new == {folder / "2026-w03.json", folder / "2026-w04.json"}
    assert all(after[p] == before[p] for p in before), "nothing else was touched"
    assert sorted(p.name for p in folder.iterdir()) == ["2026-w03.json", "2026-w04.json"]


def test_cache_write_is_atomic_tmp_then_replace(world, tmp_path, monkeypatch) -> None:
    folder = tmp_path / "cache"
    seen: list[tuple[str, str, bool]] = []
    real = os.replace

    def spy(src, dst):
        seen.append((Path(src).name, Path(dst).name, Path(dst).exists()))
        assert json.loads(Path(src).read_text(encoding="utf-8"))["week"] == 3, "tmp is complete"
        real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    C.ContextCache(folder, Builder()).get(2026, 3, paths=world.paths, engine_factory=factory())
    assert len(seen) == 1 and seen[0][1] == "2026-w03.json" and seen[0][0] != seen[0][1]
    assert seen[0][2] is False and seen[0][0].endswith(".tmp")


def test_cache_a_failed_write_still_answers_and_cleans_up(world, tmp_path, monkeypatch) -> None:
    folder = tmp_path / "cache"

    def refuse(src, dst):
        raise PermissionError("read-only disk")

    monkeypatch.setattr(os, "replace", refuse)
    build = Builder()
    cache = C.ContextCache(folder, build)
    got = cache.get(2026, 3, paths=world.paths, engine_factory=factory())
    assert got["week"] == 3 and list(folder.iterdir()) == [], "no half-written files"
    cache.get(2026, 3, paths=world.paths, engine_factory=factory())
    assert build.n == 1, "the memory copy still serves"


def test_cache_folder_none_is_memory_only(world, tmp_path) -> None:
    before = snapshot(tmp_path)
    build = Builder()
    C.ContextCache(None, build).get(2026, 3, paths=world.paths, engine_factory=factory())
    assert snapshot(tmp_path) == before
    C.ContextCache(None, build).get(2026, 3, paths=world.paths, engine_factory=factory())
    assert build.n == 2, "a new cache has nothing"


def test_cache_two_threads_wait_for_one_build(world, tmp_path) -> None:
    build, fac = Builder(delay=0.4), factory()
    cache = C.ContextCache(tmp_path / "cache", build)
    out: list[dict] = []
    gate = threading.Barrier(4)

    def run() -> None:
        gate.wait()
        out.append(cache.get(2026, 3, paths=world.paths, engine_factory=fac))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert build.n == 1 and len(fac.calls) == 1, "one build at a time per week"
    assert len(out) == 4 and all(o is out[0] for o in out), "everyone got the first one's answer"


def test_cache_different_weeks_build_side_by_side(world, tmp_path) -> None:
    build = Builder(delay=0.5)
    cache = C.ContextCache(None, build)
    t0 = time.perf_counter()
    threads = [
        threading.Thread(
            target=lambda wk=wk: cache.get(2026, wk, paths=world.paths, engine_factory=factory())
        )
        for wk in (3, 4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert build.n == 2 and time.perf_counter() - t0 < 0.95, "the lock is per week"


def test_cache_a_failed_build_raises_and_the_next_call_tries_again(world) -> None:
    build = Builder(fail=True)
    cache = C.ContextCache(None, build)
    with pytest.raises(RuntimeError, match="boom"):
        cache.get(2026, 3, paths=world.paths, engine_factory=factory())
    build.fail = False
    assert cache.get(2026, 3, paths=world.paths, engine_factory=factory())["week"] == 3
    assert build.n == 2


def test_cache_default_build_is_build_context(world, tmp_path) -> None:
    folder = tmp_path / "cache"
    cache = C.ContextCache(folder, fixes_path=world.fixes)
    ctx = cache.get(2026, 3, paths=world.paths, engine_factory=lambda: (FakeEngine(), "v-fake"))
    assert sorted(ctx["teams"]) == ["DAL", "NYG", "PHI", "SEA"] and ctx["model_version"] == "v-fake"
    assert ctx["teams"]["PHI"]["coach"] == "Joe Interim"
    stored = json.loads((folder / "2026-w03.json").read_text(encoding="utf-8"))
    assert stored["context"] == ctx, "the disk copy is the same answer"
    again = C.ContextCache(folder, fixes_path=world.fixes).get(
        2026, 3, paths=world.paths, engine_factory=lambda: 1 / 0
    )
    assert again == ctx


# --- warm --------------------------------------------------------------------------------------
def finish(cache: C.ContextCache, season: int, week: int) -> None:
    thread = cache._threads.get((season, week))
    if thread is not None:
        thread.join(10)
        assert not thread.is_alive()


def test_warm_builds_in_a_daemon_thread(world, tmp_path) -> None:
    build, fac = Builder(), factory()
    cache = C.ContextCache(tmp_path / "cache", build)
    cache.warm(2026, 3, paths=world.paths, engine_factory=fac)
    thread = cache._threads[(2026, 3)]
    assert thread.daemon
    finish(cache, 2026, 3)
    assert build.n == 1 and (tmp_path / "cache" / "2026-w03.json").exists()
    assert cache.get(2026, 3, paths=world.paths, engine_factory=fac)["week"] == 3
    assert build.n == 1, "get after warm is a hit"


def test_warm_is_a_noop_when_cached(world, tmp_path) -> None:
    build, fac = Builder(), factory()
    cache = C.ContextCache(tmp_path / "cache", build)
    cache.get(2026, 3, paths=world.paths, engine_factory=fac)
    cache.warm(2026, 3, paths=world.paths, engine_factory=fac)
    assert (2026, 3) not in cache._threads and build.n == 1
    fresh = C.ContextCache(tmp_path / "cache", build)
    fresh.warm(2026, 3, paths=world.paths, engine_factory=fac)
    assert (2026, 3) not in fresh._threads, "a valid file on disk counts as cached"
    assert build.n == 1


def test_warm_is_a_noop_while_a_build_is_running(world) -> None:
    build, fac = Builder(delay=0.5), factory()
    cache = C.ContextCache(None, build)
    runner = threading.Thread(
        target=lambda: cache.get(2026, 3, paths=world.paths, engine_factory=fac)
    )
    runner.start()
    assert build.started.wait(5)
    cache.warm(2026, 3, paths=world.paths, engine_factory=fac)
    assert (2026, 3) not in cache._threads
    runner.join(10)
    assert build.n == 1


def test_warm_twice_starts_one_thread(world) -> None:
    build, fac = Builder(delay=0.3), factory()
    cache = C.ContextCache(None, build)
    cache.warm(2026, 3, paths=world.paths, engine_factory=fac)
    first = cache._threads[(2026, 3)]
    cache.warm(2026, 3, paths=world.paths, engine_factory=fac)
    assert cache._threads[(2026, 3)] is first
    finish(cache, 2026, 3)
    assert build.n == 1


def test_warm_never_raises(world) -> None:
    cache = C.ContextCache(None, Builder(fail=True))
    cache.warm(2026, 3, paths=world.paths, engine_factory=factory())  # the build fails
    finish(cache, 2026, 3)

    def bad_factory():
        raise RuntimeError("no models")

    cache.warm(2026, 4, paths=world.paths, engine_factory=bad_factory)  # the factory fails
    finish(cache, 2026, 4)
    cache.warm(2026, 5, paths=None, engine_factory=factory())  # even nonsense arguments
    finish(cache, 2026, 5)
    # a failed warm leaves the way clear for a retry
    good = Builder()
    retry = C.ContextCache(None, good)
    retry.warm(2026, 3, paths=world.paths, engine_factory=bad_factory)
    finish(retry, 2026, 3)
    retry.warm(2026, 3, paths=world.paths, engine_factory=factory())
    finish(retry, 2026, 3)
    assert good.n == 1


# --- the contract with the web app -------------------------------------------------------------
TYPES_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "api" / "types.ts"


def ts_shape(name: str) -> tuple[list[str], dict[str, list[str]]]:
    """The field names of `export interface <name>` in web/src/api/types.ts: the top-level ones
    and, for a field whose type is an inline object, that object's own."""
    text = TYPES_TS.read_text(encoding="utf-8")
    body = re.search(rf"export interface {name} \{{\n(.*?)\n\}}", text, re.S)
    assert body, f"{name} is not in types.ts"
    top: list[str] = []
    nested: dict[str, list[str]] = {}
    current = None
    for line in body.group(1).splitlines():
        field = re.match(r"^  (\w+)\??: (.*)$", line)
        if field:
            top.append(field.group(1))
            current = field.group(1) if field.group(2).startswith("{") else None
            if current:
                nested[current] = []
        elif current and (inner := re.match(r"^    (\w+)\??:", line)):
            nested[current].append(inner.group(1))
        elif re.match(r"^  \}", line):
            current = None
    return top, nested


@pytest.mark.skipif(not TYPES_TS.exists(), reason="web/src/api/types.ts is not in this checkout")
def test_every_block_has_exactly_the_fields_the_web_types_declare(shared) -> None:
    team = shared.ctx["teams"]["PHI"]
    top, nested = ts_shape("LiveContextTeam")
    assert set(team) == set(top)
    assert set(team["kicker"]) == set(nested["kicker"])
    assert set(team["punter"]) == set(nested["punter"])
    assert set(team["coach_go_by_distance"][0]) == set(nested["coach_go_by_distance"])
    assert set(team["go_rate"]) == set(ts_shape("LiveMeasure")[0])
    assert set(team["go_rate"]["this"]) == set(ts_shape("LiveStat")[0])
    assert set(team["kicker"]["bands"][0]) == set(ts_shape("LiveKickerBand")[0])
    assert set(team["punter"]["this"]) == set(ts_shape("LivePunting")[0])
    assert set(C.team_context(shared.ctx, "PHI")) == set(ts_shape("LiveContextResponse")[0])
    assert team["kicker"]["bands"][0]["band"] in ("<30", "30-39", "40-49", "50+")
