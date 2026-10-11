"""Regression tests for Sol's review of PC00 (D123): what an as-of row could have seen.

(H) An as-of-W row used to count FTN and PFR rows before they were published. Now FTN counts
once 48 hours have passed since a game's kickoff, and PFR a week behind, against the cutoff:
the end of the Tuesday (US Eastern) after the last game before week W, or week W's first
kickoff if that comes first. Play-by-play is never held back.
(M) A plays file without `pass` (or another call column) used to read every play as a run; it
is now refused, naming the file and the column.

Synthetic league from test_playcall_build (4 teams, tmp_path, never D:, never W&B), plus one
read-only integration check on the real 2025 schedule.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

import polars as pl
import pytest
import test_playcall_build as T
from polars.testing import assert_frame_equal
from typer.testing import CliRunner

import nflengine.playcalling.build as B
from nflengine import cli
from nflengine.cli import app
from nflengine.paths import DataPaths
from nflengine.playcalling import labels as L

UTC = dt.UTC
ET = ZoneInfo("America/New_York")
SEASON, PREV, TEAMS = T.SEASON, T.PREV, T.TEAMS
runner = CliRunner()


def et(y: int, m: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> dt.datetime:
    """A US Eastern wall-clock time as a UTC instant."""
    return dt.datetime(y, m, d, h, mi, s, tzinfo=ET).astimezone(UTC)


def utc(y: int, m: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> dt.datetime:
    return dt.datetime(y, m, d, h, mi, s, tzinfo=UTC)


def gframe(*rows: tuple[int, int, str, dt.datetime | None], kickoff: bool = True) -> pl.DataFrame:
    """A games frame from (season, week, game_id, kickoff) rows."""
    df = pl.DataFrame(
        [
            {
                "season": s,
                "week": w,
                "game_id": g,
                "game_type": "REG",
                "result": 3,
                "kickoff_utc": k,
            }
            for s, w, g, k in rows
        ],
        schema={**T.GAMES_SCHEMA, "kickoff_utc": pl.Datetime("us", "UTC")},
    )
    return df if kickoff else df.drop("kickoff_utc")


def cutoff_of(games: pl.DataFrame, week: int, season: int = 2025) -> dt.datetime | None:
    out = B.as_of_cutoffs(games, season, [week])
    assert out.height == 1 and out["as_of_week"].to_list() == [week]
    return out["cutoff_utc"][0]


# --- the cutoff ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("day", "kickoff", "tuesday"),
    [
        ("Wed", et(2025, 9, 3, 19, 0), (2025, 9, 9)),
        ("Thu", et(2025, 9, 4, 20, 20), (2025, 9, 9)),
        ("Fri", et(2025, 9, 5, 20, 0), (2025, 9, 9)),
        ("Sat", et(2025, 9, 6, 20, 0), (2025, 9, 9)),
        ("Sun", et(2025, 9, 7, 20, 20), (2025, 9, 9)),
        ("Mon", et(2025, 9, 8, 20, 15), (2025, 9, 9)),  # 00:15 UTC on the Tuesday: Eastern date
        ("Mon late", et(2025, 9, 8, 22, 0), (2025, 9, 9)),
        ("Tue", et(2025, 9, 9, 19, 0), (2025, 9, 16)),  # a Tuesday game waits for the next one
    ],
)
def test_cutoff_is_the_end_of_the_next_tuesday_eastern(day, kickoff, tuesday) -> None:
    games = gframe((2025, 1, "early", et(2025, 9, 1, 13, 0)), (2025, 1, "last", kickoff))
    assert cutoff_of(games, 2) == et(*tuesday, 23, 59, 59)


def test_cutoff_literals_in_utc() -> None:
    mnf = gframe((2025, 1, "mnf", et(2025, 9, 8, 20, 15)))
    assert mnf["kickoff_utc"][0] == utc(2025, 9, 9, 0, 15)  # the trap: a Tuesday in UTC
    assert cutoff_of(mnf, 2) == utc(2025, 9, 10, 3, 59, 59)  # Tue 23:59:59 EDT
    assert cutoff_of(mnf, 2).tzinfo is not None


@pytest.mark.parametrize(
    ("kickoff", "cutoff"),
    [
        (et(2025, 9, 8, 20, 15), utc(2025, 9, 10, 3, 59, 59)),  # EDT: UTC-4
        (et(2025, 10, 26, 13, 0), utc(2025, 10, 29, 3, 59, 59)),  # Sun -> Tue Oct 28, EDT
        (et(2025, 11, 1, 20, 0), utc(2025, 11, 5, 4, 59, 59)),  # EDT game, EST cutoff (Nov 4)
        (et(2025, 11, 2, 13, 0), utc(2025, 11, 5, 4, 59, 59)),  # the day clocks went back
        (et(2025, 11, 3, 20, 15), utc(2025, 11, 5, 4, 59, 59)),  # EST: UTC-5
    ],
)
def test_cutoff_follows_daylight_saving_time(kickoff, cutoff) -> None:
    assert cutoff_of(gframe((2025, 1, "g", kickoff)), 2) == cutoff


def test_the_cutoff_is_capped_by_the_first_kickoff_of_week_w() -> None:
    sun = et(2025, 9, 7, 13, 0)
    # week 2 opens on Monday night, before Tuesday's 23:59:59: the cutoff is that kickoff
    early = gframe((2025, 1, "a", sun), (2025, 2, "b", et(2025, 9, 8, 22, 0)))
    assert cutoff_of(early, 2) == et(2025, 9, 8, 22, 0)
    # the earliest of several week-2 games
    several = gframe(
        (2025, 1, "a", sun),
        (2025, 2, "c", et(2025, 9, 11, 20, 15)),
        (2025, 2, "b", et(2025, 9, 8, 22, 0)),
    )
    assert cutoff_of(several, 2) == et(2025, 9, 8, 22, 0)
    # a normal Thursday opener comes after the Tuesday cutoff: no cap
    normal = gframe((2025, 1, "a", sun), (2025, 2, "b", et(2025, 9, 11, 20, 15)))
    assert cutoff_of(normal, 2) == et(2025, 9, 9, 23, 59, 59)
    # a week-1 Tuesday game pushes the cutoff to the next Tuesday, then Thursday's kickoff caps it
    tue = gframe((2025, 1, "a", et(2025, 9, 9, 19, 0)), (2025, 2, "b", et(2025, 9, 11, 20, 15)))
    assert cutoff_of(tue, 2) == et(2025, 9, 11, 20, 15)
    # the kickoff itself, not 23:59:59: a kickoff at the cutoff is not earlier than it
    tie = gframe((2025, 1, "a", sun), (2025, 2, "b", et(2025, 9, 9, 23, 59, 59)))
    assert cutoff_of(tie, 2) == et(2025, 9, 9, 23, 59, 59)


def test_the_cutoff_is_null_without_an_earlier_game() -> None:
    games = gframe((2025, 1, "a", et(2025, 9, 7, 13, 0)), (2025, 2, "b", et(2025, 9, 14, 13, 0)))
    out = B.as_of_cutoffs(games, 2025, [1, 2, 3])
    assert out["as_of_week"].to_list() == [1, 2, 3]
    assert out["cutoff_utc"][0] is None  # nothing before week 1
    assert out["cutoff_utc"][1] == et(2025, 9, 9, 23, 59, 59)
    assert out["cutoff_utc"][2] == et(2025, 9, 16, 23, 59, 59)  # week 3 has no games: no cap
    assert out.schema == pl.Schema({"as_of_week": pl.Int32, "cutoff_utc": pl.Datetime("us", "UTC")})
    assert B.as_of_cutoffs(games, 2024, [1, 2])["cutoff_utc"].to_list() == [None, None]


def test_cutoffs_only_read_the_season_and_the_known_kickoffs() -> None:
    games = gframe(
        (2024, 1, "old", et(2024, 12, 29, 13, 0)),  # another season: ignored
        (2025, 1, "a", et(2025, 9, 7, 13, 0)),
        (2025, 1, "no-time", None),  # kickoff unknown: skipped, not a null cutoff
    )
    assert cutoff_of(games, 2) == et(2025, 9, 9, 23, 59, 59)
    only_unknown = gframe((2025, 1, "no-time", None))
    assert cutoff_of(only_unknown, 2) is None


def test_games_without_kickoffs_give_null_cutoffs_and_do_not_fail() -> None:
    games = gframe((2025, 1, "a", None), (2025, 2, "b", None))
    assert B.as_of_cutoffs(games, 2025, [1, 2, 3])["cutoff_utc"].to_list() == [None] * 3
    bare = gframe((2025, 1, "a", et(2025, 9, 7, 13, 0)), kickoff=False)  # no kickoff_utc column
    assert "kickoff_utc" not in bare.columns
    assert B.as_of_cutoffs(bare, 2025, [1, 2])["cutoff_utc"].to_list() == [None, None]
    assert B._with_kickoff(bare)["kickoff_utc"].dtype == pl.Datetime("us", "UTC")
    assert B._with_kickoff(bare)["kickoff_utc"].null_count() == 1
    tg = _tg_rows(("a", 1, "play_action_rate"), ("a", 1, "blitz_rate_pfr"))
    held = B.held_back(tg, bare, B.as_of_cutoffs(bare, 2025, [1, 2]))
    # no cutoff to compare with: fail closed, the previous week's FTN waits a week like PFR
    assert set(held["metric"]) == {"play_action_rate", "blitz_rate_pfr"}


# --- held_back, by hand -------------------------------------------------------------------------
def _tg_rows(*rows: tuple[str, int, str]) -> pl.DataFrame:
    """(game_id, week, metric) -> one offense row of team A each (num 1.0, den 2)."""
    return pl.DataFrame(
        [
            {
                "season": 2025, "week": w, "game_id": g, "team": "A", "opponent": "B",
                "side": "offense", "metric": m, "situation": "all", "num": 1.0, "den": 2,
            }
            for g, w, m in rows
        ]
    )  # fmt: skip


def _held_keys(held: pl.DataFrame) -> set[tuple[int, str, str]]:
    return {(r["as_of_week"], r["game_id"], r["metric"]) for r in held.iter_rows(named=True)}


def test_the_48_hour_boundary_is_strict() -> None:
    """A game published exactly at the cutoff is in; a second later it is out."""
    games = gframe(
        (2025, 1, "early", et(2025, 9, 7, 12, 0)),
        (2025, 1, "edge", et(2025, 9, 7, 23, 59, 59)),  # + 48 h = Tue 23:59:59 = the cutoff
        (2025, 1, "late", et(2025, 9, 8, 0, 0, 0)),  # + 48 h = Wed 00:00:00 > the cutoff
    )
    tg = _tg_rows(*[(g, 1, "play_action_rate") for g in ("early", "edge", "late")])
    cuts = B.as_of_cutoffs(games, 2025, [2])
    assert cuts["cutoff_utc"][0] == et(2025, 9, 9, 23, 59, 59)
    assert L.FTN_LAG_HOURS == 48
    assert _held_keys(B.held_back(tg, games, cuts)) == {(2, "late", "play_action_rate")}


def test_pbp_is_never_held_back_and_ftn_is_held_by_kickoff() -> None:
    games = gframe(
        (2025, 1, "sun", et(2025, 9, 7, 13, 0)), (2025, 1, "mnf", et(2025, 9, 8, 20, 15))
    )
    tg = _tg_rows(
        *[(g, 1, m) for g in ("sun", "mnf") for m in ("dropback_rate", "proe", "play_action_rate")]
    )  # fmt: skip
    held = B.held_back(tg, games, B.as_of_cutoffs(games, 2025, [1, 2]))
    assert _held_keys(held) == {(2, "mnf", "play_action_rate")}  # Monday night at W2 only
    assert held.schema == pl.Schema(
        {
            "team": pl.String,
            "side": pl.String,
            "metric": pl.String,
            "situation": pl.String,
            "game_id": pl.String,
            "as_of_week": pl.Int32,
            "num": pl.Float64,
            "den": pl.Int64,
        }  # fmt: skip
    )
    assert held["num"].to_list() == [1.0] and held["den"].to_list() == [2]


def test_every_ftn_metric_is_held_and_no_pbp_metric_is() -> None:
    games = gframe((2025, 1, "mnf", et(2025, 9, 8, 20, 15)))
    ftn = [m.name for m in L.METRICS if m.source == "ftn"]
    pbp = [m.name for m in L.METRICS if m.source == "pbp"]
    hist = [m.name for m in L.METRICS if m.source == "participation"]
    tg = _tg_rows(*[("mnf", 1, m) for m in (*ftn, *pbp, *hist)])
    held = B.held_back(tg, games, B.as_of_cutoffs(games, 2025, [2]))
    assert set(held["metric"]) == set(ftn)  # the PFR metric is not in tg: nothing else is held
    assert not set(held["metric"]) & set(pbp)
    assert not set(held["metric"]) & set(hist)  # research data has no lag rule here


def test_pfr_is_held_one_week_whatever_the_kickoff() -> None:
    games = gframe(
        (2025, 1, "g1", et(2025, 9, 7, 13, 0)),
        (2025, 2, "g2", et(2025, 9, 14, 13, 0)),
        (2025, 3, "g3", et(2025, 9, 21, 13, 0)),
    )
    tg = _tg_rows(
        ("g1", 1, "blitz_rate_pfr"), ("g2", 2, "blitz_rate_pfr"), ("g3", 3, "blitz_rate_pfr")
    )
    held = B.held_back(tg, games, B.as_of_cutoffs(games, 2025, [1, 2, 3, 4]))
    assert L.PFR_LAG_WEEKS == 1
    assert _held_keys(held) == {
        (2, "g1", "blitz_rate_pfr"),  # at W2 the week-1 game is held ...
        (3, "g2", "blitz_rate_pfr"),  # ... at W3 the week-2 game, and so on
        (4, "g3", "blitz_rate_pfr"),
    }


def test_a_game_with_no_kickoff_or_no_cutoff_fails_closed_for_a_week() -> None:
    games = gframe(
        (2025, 1, "known", et(2025, 9, 7, 13, 0)),
        (2025, 1, "unknown", None),
        (2025, 2, "next", et(2025, 9, 14, 13, 0)),
    )
    tg = _tg_rows(
        *[(g, w, "play_action_rate") for g, w in (("known", 1), ("unknown", 1), ("next", 2))]
    )
    held = B.held_back(tg, games, B.as_of_cutoffs(games, 2025, [2, 3, 4]))
    assert _held_keys(held) == {(2, "unknown", "play_action_rate")}  # week >= W - 1, then released
    # with no kickoff anywhere there is no cutoff at all: each week's FTN still waits a week
    blank = gframe((2025, 1, "x", None), (2025, 2, "y", None))
    tg2 = _tg_rows(("x", 1, "play_action_rate"), ("y", 2, "play_action_rate"))
    held2 = B.held_back(tg2, blank, B.as_of_cutoffs(blank, 2025, [2, 3]))
    assert _held_keys(held2) == {(2, "x", "play_action_rate"), (3, "y", "play_action_rate")}


def test_held_back_is_empty_without_ftn_pfr_rows_or_cutoffs() -> None:
    games = gframe((2025, 1, "g", et(2025, 9, 8, 20, 15)))
    pbp_only = _tg_rows(("g", 1, "dropback_rate"))
    assert B.held_back(pbp_only, games, B.as_of_cutoffs(games, 2025, [2])).is_empty()
    ftn = _tg_rows(("g", 1, "play_action_rate"))
    assert B.held_back(ftn, games, B.as_of_cutoffs(games, 2025, [])).is_empty()
    assert B.held_back(ftn.clear(), games, B.as_of_cutoffs(games, 2025, [2])).is_empty()


# --- tendencies(held=...) by hand ---------------------------------------------------------------
def _held_frame(*rows: tuple[str, str, int]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "team": t, "side": "offense", "metric": "play_action_rate", "situation": "all",
                "game_id": g, "as_of_week": w, "num": 1.0, "den": 2,
            }
            for t, g, w in rows
        ],
        schema={
            "team": pl.String, "side": pl.String, "metric": pl.String, "situation": pl.String,
            "game_id": pl.String, "as_of_week": pl.Int32, "num": pl.Float64, "den": pl.Int64,
        },
    )  # fmt: skip


def _six_game_team() -> tuple[pl.DataFrame, pl.DataFrame]:
    games = [(f"gX{i}", i) for i in range(1, 7)]
    tg = pl.DataFrame(
        [
            {
                "season": 2025, "week": w, "game_id": g, "team": team, "opponent": "Z",
                "side": "offense", "metric": "play_action_rate", "situation": "all",
                "num": 1.0, "den": 2,
            }
            for team, gs in (("X", games), ("Y", [("gY1", 1)]))
            for g, w in gs
        ]
    )  # fmt: skip
    idx = pl.DataFrame(
        [{"team": "X", "game_id": g, "week": w, "game_date": "d", "game_no": w} for g, w in games]
        + [{"team": "Y", "game_id": "gY1", "week": 1, "game_date": "d", "game_no": 1}],
        schema={"team": pl.String, "game_id": pl.String, "week": pl.Int32, "game_date": pl.String,
                "game_no": pl.Int32},
    )  # fmt: skip
    return tg, idx


def _row(t: pl.DataFrame, team: str, window: str) -> dict | None:
    r = t.filter((pl.col("team") == team) & (pl.col("window") == window))
    return r.row(0, named=True) if r.height else None


def test_held_sums_leave_the_season_and_only_the_games_inside_last4() -> None:
    tg, idx = _six_game_team()
    base = B.tendencies(tg, idx, [7], season=2025)
    assert (_row(base, "X", "season")["n"], _row(base, "X", "last4")["n"]) == (12, 8)
    # g1 is outside X's last four (games 3-6); g6 is inside; Y's only game is held
    held = _held_frame(("X", "gX1", 7), ("X", "gX6", 7), ("Y", "gY1", 7))
    t = B.tendencies(tg, idx, [7], season=2025, held=held)
    season, last4 = _row(t, "X", "season"), _row(t, "X", "last4")
    assert (season["n"], season["value"], season["games"]) == (8, 0.5, 6)  # 12 - 2 - 2
    assert (last4["n"], last4["value"], last4["games"]) == (6, 0.5, 4)  # g1 is not in the window
    # Y has no FTN den left: the rows disappear (and the league is X alone: percentile 50)
    assert _row(t, "Y", "season") is None and _row(t, "Y", "last4") is None
    assert season["league_value"] == 0.5 and season["pct"] == 50.0
    # held rows of another as-of week do not apply
    other = B.tendencies(tg, idx, [7], season=2025, held=_held_frame(("X", "gX6", 6)))
    assert _row(other, "X", "season")["n"] == 12
    # no held frame, and an empty one, change nothing
    for h in (None, _held_frame()):
        same = B.tendencies(tg, idx, [7], season=2025, held=h)
        assert_frame_equal(same, base)


def test_held_games_still_count_as_games() -> None:
    tg, idx = _six_game_team()
    held = _held_frame(("X", "gX6", 7))
    t = B.tendencies(tg, idx, [7], season=2025, held=held)
    assert _row(t, "X", "season")["games"] == 6 and _row(t, "X", "last4")["games"] == 4


# --- the synthetic league with kickoffs ---------------------------------------------------------
SUNDAYS = {2023: dt.date(2023, 9, 10), 2024: dt.date(2024, 9, 8)}


def kickoffs() -> dict[str, dt.datetime]:
    """Sunday 1:00 pm ET for each week's first game (Sunday night, 8:20, for BAL at ARI in
    week 2), Monday night 8:15 ET for the second. All in EDT."""
    out: dict[str, dt.datetime] = {}
    for season, weeks in T.SCHEDULE.items():
        for week, pairs in weeks.items():
            sunday = SUNDAYS[season] + dt.timedelta(days=7 * (week - 1))
            for i, (away, home) in enumerate(pairs):
                gid = T.game_id_of(season, week, away, home)
                y, m, d = sunday.year, sunday.month, sunday.day
                if gid == "2024_02_BAL_ARI":
                    out[gid] = et(y, m, d, 20, 20)
                elif i == 0:
                    out[gid] = et(y, m, d, 13, 0)
                else:
                    monday = sunday + dt.timedelta(days=1)
                    out[gid] = et(monday.year, monday.month, monday.day, 20, 15)
    return out


def write_kickoffs(paths: DataPaths) -> None:
    ks = kickoffs()
    g = pl.read_parquet(paths.curated / "games.parquet")
    g = g.with_columns(
        pl.Series("kickoff_utc", [ks[x] for x in g["game_id"]], dtype=pl.Datetime("us", "UTC"))
    )
    g.write_parquet(paths.curated / "games.parquet")


def build_k(root, lg: T.League, through_week: int | None = None) -> T.World:
    paths = T.write_tree(root, lg)
    write_kickoffs(paths)
    inp = B.load_inputs(paths, [SEASON], history=False, log=T.quiet)
    enriched = B.enrich_plays(inp.plays, inp.ftn, inp.part)
    sb = B.build_season(inp, enriched, SEASON, through_week, {})
    return T.World(lg, paths, inp, enriched, sb)


@pytest.fixture(scope="module")
def kw(tmp_path_factory) -> T.World:
    return build_k(tmp_path_factory.mktemp("sol_world"), T.make_league())


# Hand-worked from kickoffs(): the cutoff is the Tuesday after the previous week's last game,
# 23:59:59 EDT (= 03:59:59 UTC the next day); Monday night is held at the next as-of week.
CUTOFFS = {
    1: None,
    2: utc(2024, 9, 11, 3, 59, 59),  # Mon Sep 9 -> Tue Sep 10
    3: utc(2024, 9, 18, 3, 59, 59),  # Mon Sep 16 -> Tue Sep 17
    4: utc(2024, 9, 25, 3, 59, 59),  # week 3 had only a Sunday game (Sep 22) -> Tue Sep 24
    5: utc(2024, 10, 2, 3, 59, 59),
    6: utc(2024, 10, 9, 3, 59, 59),
    7: utc(2024, 10, 16, 3, 59, 59),
}
HELD_FTN = {
    1: [],
    2: ["2024_01_BUF_BAL"],
    3: ["2024_02_BUF_ATL"],
    4: [],  # week 3's only game was Sunday afternoon: published by Tuesday night
    5: ["2024_04_BUF_BAL"],
    6: ["2024_05_BUF_ATL"],
    7: ["2024_06_BAL_ATL"],
}
HELD_PFR = {2: ["2024_01_ATL_ARI"], 3: ["2024_02_BAL_ARI"]}  # the only games with PFR rows
FTN_NAMES = {m.name for m in L.METRICS if m.source == "ftn"}
FTN_PROBES = [
    ("play_action_rate", "all"), ("play_action_rate", "neutral"), ("blitz_rate", "all"),
    ("rushers_avg", "all"), ("motion_rate", "all"), ("screen_rate", "all"),
    ("heavy_box_rate", "all"), ("qb_shotgun_share", "all"),
]  # fmt: skip
PBP_PROBES = [
    ("dropback_rate", "all"), ("dropback_rate", "red_zone"), ("proe", "neutral"), ("adot", "all"),
    ("explosive_rate", "all"), ("run_middle", "all"),
]  # fmt: skip


def test_the_league_calendar_is_what_the_comments_say(kw) -> None:
    ks = kickoffs()
    assert ks["2024_01_BUF_BAL"] == utc(2024, 9, 10, 0, 15)  # Monday night, a Tuesday in UTC
    assert ks["2024_02_BAL_ARI"] == utc(2024, 9, 16, 0, 20)  # Sunday night, a Monday in UTC
    g = kw.inp.games.filter(pl.col("season") == SEASON)
    assert g["kickoff_utc"].dtype == pl.Datetime("us", "UTC") and g["kickoff_utc"].null_count() == 0


def test_cutoffs_and_held_games_of_the_league(kw) -> None:
    cuts = B.as_of_cutoffs(kw.inp.games, SEASON, kw.sb.weeks)
    assert dict(zip(cuts["as_of_week"], cuts["cutoff_utc"], strict=True)) == CUTOFFS
    held = B.held_back(kw.sb.tg, kw.inp.games, cuts)
    for w in range(1, 8):
        at = held.filter(pl.col("as_of_week") == w)
        ftn = at.filter(pl.col("metric").is_in(sorted(FTN_NAMES)))
        assert sorted(ftn["game_id"].unique().to_list()) == HELD_FTN[w], w
        pfr = at.filter(pl.col("metric") == "blitz_rate_pfr")
        assert sorted(pfr["game_id"].unique().to_list()) == HELD_PFR.get(w, []), w
        assert not at.filter(~pl.col("metric").is_in([*sorted(FTN_NAMES), "blitz_rate_pfr"])).height


def test_ftn_windows_count_only_published_games(kw) -> None:
    rows = T.ref_rows(kw.lg, SEASON)
    look = T.tend_lookup(kw.sb.tend)
    checked = 0
    for w in range(2, 8):
        for team in TEAMS:
            played = [g for wk, g in T.team_games(kw.lg, SEASON, team) if wk < w]
            for window, games in (("season", played), ("last4", played[-4:])):
                seen = {g for g in games if g not in HELD_FTN[w]}
                for side in ("offense", "defense"):
                    for metric, sit in FTN_PROBES:
                        num, den = T.ref_sum(rows, team, side, metric, sit, seen)
                        r = look.get((w, team, side, window, metric, sit))
                        if den == 0:
                            assert r is None, (w, team, side, window, metric, sit)
                            continue
                        assert r is not None and r["n"] == den, (w, team, side, window, metric)
                        assert r["value"] == pytest.approx(num / den, abs=1e-9)
                        assert r["games"] == len(games)  # held games still count as games
                        checked += 1
    assert checked > 250


def test_pbp_windows_are_never_held_back(kw) -> None:
    rows = T.ref_rows(kw.lg, SEASON)
    look = T.tend_lookup(kw.sb.tend)
    for w in range(2, 8):
        for team in TEAMS:
            played = [g for wk, g in T.team_games(kw.lg, SEASON, team) if wk < w]
            for window, games in (("season", played), ("last4", played[-4:])):
                for side in ("offense", "defense"):
                    for metric, sit in PBP_PROBES:
                        num, den = T.ref_sum(rows, team, side, metric, sit, set(games))
                        r = look.get((w, team, side, window, metric, sit))
                        if den == 0:
                            assert r is None
                        else:
                            assert r["n"] == den and r["games"] == len(games)
                            assert r["value"] == pytest.approx(num / den, abs=1e-9)


def test_monday_night_ftn_is_held_one_as_of_week_then_counts(kw) -> None:
    """BUF's weeks 1 and 2 were both Monday night. Dropbacks: 5, 6 (and 4 in the week-3 game)."""
    look = T.tend_lookup(kw.sb.tend)

    def pa(w: int, window: str = "season"):
        return look.get((w, "BUF", "offense", window, "play_action_rate", "all"))

    assert pa(2) is None and pa(2, "last4") is None  # its only game is held: no FTN row at all
    assert (
        look[(2, "BUF", "offense", "season", "dropback_rate", "all")]["games"] == 1
    )  # pbp is there
    assert pa(3)["n"] == 5 and pa(3)["games"] == 2  # week 1 is out, week 2 (held) is not yet
    assert pa(4)["n"] == 5 + 6 + 4 and pa(4)["games"] == 3  # both Mondays and week 3 counted
    assert pa(4, "last4")["n"] == 15
    # BAL: week 1 Monday night (held at W2), week 2 Sunday night 8:20 pm (published by Tuesday)
    bal = {w: look.get((w, "BAL", "offense", "season", "play_action_rate", "all")) for w in (2, 3)}
    assert bal[2] is None
    assert bal[3]["n"] == 4 + 5 and bal[3]["games"] == 2  # dropbacks: week 1 has 4, week 2 has 5


def test_a_sunday_night_game_is_not_held(kw) -> None:
    """BAL at ARI, week 2, kicks off Sunday 8:20 pm ET: 48 h later is Tuesday 8:20 pm < 23:59:59."""
    look = T.tend_lookup(kw.sb.tend)
    cut = CUTOFFS[3]
    assert kickoffs()["2024_02_BAL_ARI"] + dt.timedelta(hours=48) < cut
    ari3 = look[(3, "ARI", "offense", "season", "play_action_rate", "all")]
    # ARI's week 1 (5 dropbacks, Sunday) and week 2 (6 dropbacks, Sunday night): both counted
    assert ari3["n"] == 5 + 6 and ari3["games"] == 2
    # ATL's week-2 game was Monday night: only its week 1 counts at W3
    atl3 = look[(3, "ATL", "offense", "season", "play_action_rate", "all")]
    assert atl3["n"] == 6 and atl3["games"] == 2


def test_a_team_whose_only_game_is_held_has_no_ftn_rows(kw) -> None:
    t = kw.sb.tend.filter(
        (pl.col("as_of_week") == 2) & (pl.col("window").is_in(["season", "last4"]))
    )
    for team in ("BUF", "BAL"):  # week 1 was Monday night
        mine = t.filter(pl.col("team") == team)
        assert not set(mine["metric"]) & FTN_NAMES
        assert "dropback_rate" in set(mine["metric"])
    for team in ("ARI", "ATL"):  # week 1 was Sunday afternoon
        assert {"play_action_rate", "blitz_rate"} <= set(t.filter(pl.col("team") == team)["metric"])
    # the league is pooled over the teams that have FTN: 2 offenses, not 4
    lg = kw.sb.league.filter(
        (pl.col("as_of_week") == 2) & (pl.col("window") == "season")
        & (pl.col("metric") == "play_action_rate") & (pl.col("situation") == "all")
    )  # fmt: skip
    assert lg["teams"].to_list() == [2]
    rows = T.ref_rows(kw.lg, SEASON)
    num = den = 0
    for team in ("ARI", "ATL"):
        n, d = T.ref_sum(rows, team, "offense", "play_action_rate", "all", {T.G_W1})
        num, den = num + n, den + d
    assert lg["value"].to_list() == [pytest.approx(num / den)]


def test_league_value_and_percentile_ignore_held_games(kw) -> None:
    rows = T.ref_rows(kw.lg, SEASON)
    t = kw.sb.tend
    for w in (3, 5, 7):
        num = den = 0
        for team in TEAMS:
            seen = {g for wk, g in T.team_games(kw.lg, SEASON, team) if wk < w} - set(HELD_FTN[w])
            n, d = T.ref_sum(rows, team, "offense", "blitz_rate", "all", seen)
            num, den = num + n, den + d
        part = t.filter(
            (pl.col("as_of_week") == w) & (pl.col("window") == "season")
            & (pl.col("side") == "offense") & (pl.col("metric") == "blitz_rate")
            & (pl.col("situation") == "all")
        )  # fmt: skip
        assert part["league_value"].unique().to_list() == [pytest.approx(num / den)]
        assert part.height == 4 and part["pct"].min() >= 0.0 and part["pct"].max() <= 100.0


def test_pfr_is_one_week_behind_in_the_windows(kw) -> None:
    look = T.tend_lookup(kw.sb.tend)

    def key(w, team, side, window="season"):
        return look.get((w, team, side, window, "blitz_rate_pfr", "all"))

    for window in ("season", "last4"):
        for side in ("offense", "defense"):
            for team in TEAMS:
                assert key(2, team, side, window) is None  # week 1's PFR is held at W2
    # W3: week 1's PFR is in, week 2's (ARI faced 2 blitzes on 6 dropbacks) is held
    assert (key(3, "ARI", "offense")["n"], key(3, "ARI", "offense")["value"]) == (5, 0.6)
    assert (key(3, "ATL", "offense")["n"], key(3, "ATL", "offense")["value"]) == (6, 0.5)
    assert key(3, "ATL", "defense")["value"] == 0.6 and key(3, "ARI", "defense")["value"] == 0.5
    assert key(3, "BAL", "defense") is None  # the week-2 game, held
    # W4: both counted
    ari = key(4, "ARI", "offense")
    assert ari["n"] == 5 + 6 and ari["value"] == pytest.approx((3 + 2) / 11)
    assert key(4, "BAL", "defense")["value"] == pytest.approx(2 / 6)
    assert key(4, "ARI", "offense", "last4")["n"] == 11
    # last season's PFR (2023 week 1) is a finished season: never held
    assert look[(2, "ARI", "offense", "last_season", "blitz_rate_pfr", "all")]["n"] == 5


def test_last_season_is_never_held(kw) -> None:
    t = kw.sb.tend.filter(pl.col("window") == "last_season")
    assert t.filter(pl.col("metric") == "play_action_rate").height > 0
    for w in range(1, 8):  # the same FTN rows at every as-of week, MNF games included
        row = t.filter(
            (pl.col("as_of_week") == w) & (pl.col("team") == "BUF") & (pl.col("side") == "offense")
            & (pl.col("metric") == "play_action_rate") & (pl.col("situation") == "all")
        ).row(0, named=True)  # fmt: skip
        assert row["games"] == 3 and row["n"] == 5 + 6 + 4


def test_build_json_availability(kw, tmp_path) -> None:
    av = kw.sb.meta["availability"]
    assert av == {
        "as_of_week": 7,
        "cutoff_utc": "2024-10-16T03:59:59+00:00",
        "ftn_held_games": ["2024_06_BAL_ATL"],
        "pfr_held_games": 0,
    }
    assert dt.datetime.fromisoformat(av["cutoff_utc"]) == CUTOFFS[7]
    lg = T.make_league()
    lg.pfr.append(T.pfr_row("2024_06_BUF_ARI", "ARI", "BUF", 1.0))  # a week-6 PFR row
    more = build_k(tmp_path, lg)
    assert more.sb.meta["availability"]["pfr_held_games"] == 1
    out = B.write_season(DataPaths(tmp_path / "out"), more.sb)
    doc = json.loads((out / "build.json").read_text(encoding="utf-8"))
    assert doc["availability"] == more.sb.meta["availability"]
    avail = doc["definitions"]["availability"]
    assert (avail["ftn_lag_hours"], avail["pfr_lag_weeks"]) == (48, 1)
    assert "Tuesday" in avail["cutoff"] and avail["pbp"]
    # an early as-of week of a cut-off build: Monday night of the last week it covers is held
    cut = build_k(tmp_path / "cut", T.make_league(), through_week=1)
    assert cut.sb.meta["availability"]["as_of_week"] == 2
    assert cut.sb.meta["availability"]["ftn_held_games"] == ["2024_01_BUF_BAL"]


# --- live consistency: what a Tuesday run sees equals the retrospective row ----------------------
MNF = [(1, "2024_01_BUF_BAL"), (2, "2024_02_BUF_ATL"), (4, "2024_04_BUF_BAL"),
       (5, "2024_05_BUF_ATL"), (6, "2024_06_BAL_ATL")]  # fmt: skip


@pytest.mark.parametrize(("week", "game"), MNF)
def test_a_live_build_without_the_monday_night_ftn_equals_the_retrospective_row(
    tmp_path, kw, week, game
) -> None:
    """On the Tuesday after week `week`, FTN has not published that Monday game yet. The build
    made then (no FTN rows for it) must equal what a later build says as of week + 1."""
    live = build_k(tmp_path, kw.lg.without_ftn(game))
    keep = pl.col("as_of_week") <= week + 1
    assert_frame_equal(kw.sb.tend.filter(keep), live.sb.tend.filter(keep))
    assert_frame_equal(kw.sb.league.filter(keep), live.sb.league.filter(keep))
    # the game does matter two as-of weeks later: the retrospective build counts it by then
    if week + 2 <= 7:
        later = pl.col("as_of_week") == week + 2
        assert not kw.sb.tend.filter(later).equals(live.sb.tend.filter(later))


@pytest.mark.parametrize("week", [1, 2])
def test_a_live_build_without_last_weeks_pfr_equals_the_retrospective_row(tmp_path, kw, week):
    gid = {1: "2024_01_ATL_ARI", 2: "2024_02_BAL_ARI"}[week]
    lg = kw.lg.clone()
    lg.pfr = [r for r in lg.pfr if r["game_id"] != gid]
    live = build_k(tmp_path, lg)
    keep = pl.col("as_of_week") <= week + 1
    assert_frame_equal(kw.sb.tend.filter(keep), live.sb.tend.filter(keep))
    later = pl.col("as_of_week") == week + 2
    assert not kw.sb.tend.filter(later).equals(live.sb.tend.filter(later))


def test_a_game_whose_ftn_is_late_beyond_the_lag_is_simply_missing(tmp_path, kw) -> None:
    """FTN not out 48 h after a Sunday game: nothing to hold back, n just shrinks."""
    live = build_k(tmp_path, kw.lg.without_ftn("2024_03_BUF_ARI"))
    look, full = T.tend_lookup(live.sb.tend), T.tend_lookup(kw.sb.tend)
    k = (4, "ARI", "offense", "season", "play_action_rate", "all")
    assert full[k]["n"] - look[k]["n"] == 4  # ARI's week-3 dropbacks
    assert (
        look[(4, "ARI", "offense", "season", "dropback_rate", "all")]["n"]
        == (full[(4, "ARI", "offense", "season", "dropback_rate", "all")]["n"])
    )


# --- no kickoff data: fail closed ---------------------------------------------------------------
def build_plain(root, lg: T.League) -> T.World:
    """A curated games table without `kickoff_utc` (no cutoff can be computed)."""
    paths = T.write_tree(root, lg, kickoffs=False)
    inp = B.load_inputs(paths, [SEASON], history=False, log=T.quiet)
    enriched = B.enrich_plays(inp.plays, inp.ftn, inp.part)
    return T.World(lg, paths, inp, enriched, B.build_season(inp, enriched, SEASON, None, {}))


def test_without_kickoffs_the_previous_weeks_ftn_and_pfr_wait_a_week(tmp_path) -> None:
    w = build_plain(tmp_path, T.make_league())
    games = w.inp.games
    assert games["kickoff_utc"].dtype == pl.Datetime("us", "UTC")
    assert games["kickoff_utc"].null_count() == games.height
    assert B.as_of_cutoffs(games, SEASON, w.sb.weeks)["cutoff_utc"].null_count() == 7
    t = T.tend_lookup(w.sb.tend)

    def pa(week: int):
        return t.get((week, "BUF", "offense", "season", "play_action_rate", "all"))

    # BUF played weeks 1, 2 and 3 (5, 6 and 4 dropbacks): the newest game is always held
    assert pa(2) is None
    assert pa(3)["n"] == 5 and pa(4)["n"] == 5 + 6
    assert t[(2, "BUF", "offense", "season", "dropback_rate", "all")]["games"] == 1  # pbp is in
    in_season = ("season", "last4")  # last season's PFR is a finished season: never held
    assert not any(k[4] == "blitz_rate_pfr" and k[0] == 2 and k[3] in in_season for k in t)
    assert w.sb.meta["availability"] == {
        "as_of_week": 7,
        "cutoff_utc": None,
        "ftn_held_games": ["2024_06_BAL_ATL", "2024_06_BUF_ARI"],
        "pfr_held_games": 0,
    }


def test_load_inputs_converts_kickoffs_to_utc(tmp_path) -> None:
    lg = T.make_league()
    paths = T.write_tree(tmp_path, lg)
    ks = kickoffs()
    g = pl.read_parquet(paths.curated / "games.parquet")
    eastern = pl.Series(
        "kickoff_utc", [ks[x] for x in g["game_id"]], dtype=pl.Datetime("us", "UTC")
    ).dt.convert_time_zone("America/New_York")
    g.with_columns(eastern).write_parquet(paths.curated / "games.parquet")
    inp = B.load_inputs(paths, [SEASON], history=False, log=T.quiet)
    assert inp.games["kickoff_utc"].dtype == pl.Datetime("us", "UTC")
    assert dict(zip(inp.games["game_id"], inp.games["kickoff_utc"], strict=True)) == ks
    assert list(inp.games.columns) == [
        "season", "week", "game_id", "game_type", "result", "kickoff_utc",
    ]  # fmt: skip


# --- (M) a plays file without a call column -----------------------------------------------------
def test_required_columns_are_the_ones_that_classify_a_play() -> None:
    assert set(B.REQUIRED_COLUMNS) == {
        "game_id", "play_id", "season", "week", "posteam", "defteam", "play_type", "pass", "sack",
    }  # fmt: skip
    assert set(B.REQUIRED_COLUMNS) <= set(B.PLAY_COLUMNS)


@pytest.mark.parametrize("column", B.REQUIRED_COLUMNS)
def test_a_plays_file_missing_a_required_column_is_refused(tmp_path, column) -> None:
    paths = T.write_tree(tmp_path, T.make_league(), drop_columns={SEASON: [column]})
    with pytest.raises(
        ValueError, match=rf"season=2024: required plays columns missing: {column}$"
    ):
        B.load_inputs(paths, [SEASON], history=False, log=T.quiet)


def test_several_missing_required_columns_are_all_named(tmp_path) -> None:
    paths = T.write_tree(tmp_path, T.make_league(), drop_columns={PREV: ["sack", "pass", "wp"]})
    with pytest.raises(ValueError) as e:
        B.load_inputs(paths, [SEASON], history=False, log=T.quiet)
    assert "season=2023: required plays columns missing: pass, sack" in str(
        e.value
    )  # wp is optional


def test_a_missing_optional_column_still_builds(tmp_path) -> None:
    paths = T.write_tree(tmp_path, T.make_league(), drop_columns={SEASON: ["xpass", "wp"]})
    inp = B.load_inputs(paths, [SEASON], history=False, log=T.quiet)
    assert "season=2024: plays column xpass missing (null)" in inp.notes
    assert "season=2024: plays column wp missing (null)" in inp.notes
    sb = B.build_season(inp, B.enrich_plays(inp.plays, inp.ftn, inp.part), SEASON, tg_cache={})
    metrics = set(sb.tend.filter(pl.col("season") == SEASON)["metric"])
    assert "dropback_rate" in metrics
    # PROE needs xpass: no 2024 row; the 2023 file still has it, so only last_season carries it
    proe = sb.tend.filter((pl.col("metric") == "proe") & (pl.col("window") != "last_season"))
    assert proe.is_empty()
    # every play keeps its call: dropbacks are not all zero (the old failure mode)
    assert sb.enriched["dropback"].sum() > 0 and sb.enriched["designed_run"].sum() > 0


def test_a_refused_build_writes_nothing(tmp_path) -> None:
    paths = T.write_tree(tmp_path, T.make_league(), drop_columns={SEASON: ["pass"]})
    with pytest.raises(ValueError, match="required plays columns missing: pass"):
        B.run_build(SEASON, history=[PREV], use_wandb=False, paths=paths, log=T.quiet)
    assert not (tmp_path / "playcalling").exists()


# --- the CLI end to end on a tmp tree (never D:) --------------------------------------------------
@pytest.fixture
def tree_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.console, "_width", 250)
    monkeypatch.setattr(B, "log_wandb", lambda *a, **k: pytest.fail("W&B must not be called"))

    def make(drop: dict[int, list[str]] | None = None) -> DataPaths:
        paths = T.write_tree(tmp_path, T.make_league(), drop_columns=drop)
        write_kickoffs(paths)
        monkeypatch.setattr(B, "ensure_data_root", lambda *a, **k: paths)
        return paths

    return make


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_cli_exits_1_naming_the_file_and_column(tree_cli, tmp_path) -> None:
    tree_cli(drop={SEASON: ["pass"]})
    r = runner.invoke(app, ["playcalling", "build", "--season", "2024", "--no-wandb"])
    assert r.exit_code == 1
    assert "season=2024: required plays columns missing: pass" in flat(r.output)
    assert not (tmp_path / "playcalling").exists()


def test_cli_builds_a_tmp_tree_and_records_the_availability(tree_cli, tmp_path) -> None:
    tree_cli()
    r = runner.invoke(
        app, ["playcalling", "build", "--season", "2024", "--history", "2023", "--no-wandb"]
    )
    assert r.exit_code == 0, r.output
    doc = json.loads((tmp_path / "playcalling" / "2024" / "build.json").read_text("utf-8"))
    assert doc["availability"]["ftn_held_games"] == ["2024_06_BAL_ATL"]
    assert doc["availability"]["cutoff_utc"] == "2024-10-16T03:59:59+00:00"
    t = pl.read_parquet(tmp_path / "playcalling" / "2024" / "team_tendencies.parquet")
    assert not t.filter(
        (pl.col("as_of_week") == 2) & (pl.col("team") == "BUF") & (pl.col("metric") == "blitz_rate")
        & (pl.col("window") != "last_season")
    ).height  # fmt: skip
    assert "play-calling build" in flat(r.output)


# --- the real 2025 schedule (read only, in memory) ----------------------------------------------
@pytest.fixture(scope="module")
def real():
    from nflengine.paths import ensure_data_root

    paths = ensure_data_root(create_dirs=False)
    inp = B.load_inputs(paths, [2025], history=False, log=lambda *a, **k: None)
    enriched = B.enrich_plays(inp.plays, inp.ftn, inp.part)
    return {"inp": inp, "sb": B.build_season(inp, enriched, 2025)}


@pytest.mark.integration
def test_2025_games_carry_kickoffs_and_the_early_cutoffs_are_tuesday_nights(real) -> None:
    games = real["inp"].games
    for season in (2024, 2025):
        g = games.filter(pl.col("season") == season)
        assert g.height > 270 and g["kickoff_utc"].null_count() == 0
        assert g["kickoff_utc"].dtype == pl.Datetime("us", "UTC")
    cuts = B.as_of_cutoffs(games, 2025, [1, 2, 3, 4, 5, 6])
    assert cuts["cutoff_utc"].to_list() == [
        None,
        utc(2025, 9, 10, 3, 59, 59),  # Mon Sep 8 -> Tue Sep 9, 23:59:59 EDT
        utc(2025, 9, 17, 3, 59, 59),
        utc(2025, 9, 24, 3, 59, 59),
        utc(2025, 10, 1, 3, 59, 59),
        utc(2025, 10, 8, 3, 59, 59),
    ]


@pytest.mark.integration
def test_2025_holds_back_monday_night_ftn(real) -> None:
    sb, games = real["sb"], real["inp"].games
    cuts = B.as_of_cutoffs(games, 2025, sb.weeks)
    held = B.held_back(sb.tg, games, cuts)
    ftn = held.filter(pl.col("metric").is_in(sorted(FTN_NAMES)))
    by_week = {
        w: sorted(ftn.filter(pl.col("as_of_week") == w)["game_id"].unique().to_list())
        for w in range(1, 6)
    }
    assert by_week == {
        1: [],
        2: ["2025_01_MIN_CHI"],  # the only Monday game of week 1
        3: ["2025_02_LAC_LV", "2025_02_TB_HOU"],  # Monday night doubleheader
        4: ["2025_03_DET_BAL"],
        5: ["2025_04_CIN_DEN", "2025_04_NYJ_MIA"],
    }
    # always the games of the week just played, and only ones 48 hours had not yet covered
    ko = dict(zip(games["game_id"], games["kickoff_utc"], strict=True))
    week = dict(zip(games["game_id"], games["week"], strict=True))
    cutoff = dict(zip(cuts["as_of_week"], cuts["cutoff_utc"], strict=True))
    for w in sb.weeks:
        for g in ftn.filter(pl.col("as_of_week") == w)["game_id"].unique():
            assert week[g] == w - 1, (w, g)
            assert ko[g] + dt.timedelta(hours=48) > cutoff[w], (w, g)
    # a PFR row is held for every game of the week before the as-of week
    pfr = held.filter((pl.col("metric") == "blitz_rate_pfr") & (pl.col("as_of_week") == 3))
    assert pfr["game_id"].n_unique() == 16
    assert all(week[g] == 2 for g in pfr["game_id"].unique())


@pytest.mark.integration
def test_2025_as_of_week_2_has_no_ftn_for_a_team_whose_only_game_was_monday_night(real) -> None:
    t = real["sb"].tend.filter(
        (pl.col("as_of_week") == 2) & (pl.col("window").is_in(["season", "last4"]))
    )
    for team in ("MIN", "CHI"):  # 2025_01_MIN_CHI, Monday night
        for side in ("offense", "defense"):
            mine = t.filter((pl.col("team") == team) & (pl.col("side") == side))
            assert not mine.filter(pl.col("metric").is_in(sorted(FTN_NAMES))).height, (team, side)
            db = mine.filter((pl.col("metric") == "dropback_rate") & (pl.col("situation") == "all"))
            assert db["games"].unique().to_list() == [1] and db["n"].min() > 40
    for team in ("DET", "GB"):  # a Sunday game: FTN is there
        mine = t.filter((pl.col("team") == team) & (pl.col("side") == "offense"))
        assert {"play_action_rate", "blitz_rate"} <= set(mine["metric"])
    # and by as-of week 3 MIN has FTN again (week 1 is 48 h old, week 2 was Sunday night)
    wk3 = real["sb"].tend.filter(
        (pl.col("as_of_week") == 3) & (pl.col("team") == "MIN") & (pl.col("window") == "season")
        & (pl.col("side") == "offense") & (pl.col("metric") == "play_action_rate")
    )  # fmt: skip
    assert wk3.height > 0 and wk3["games"].unique().to_list() == [2]
