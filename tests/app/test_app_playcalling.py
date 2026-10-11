"""PC01: Explore -> Play calling and the Play calls week tab
(documentation/play-calling/PC01-play-calling-pages.md), backend.

`GET /api/playcalling/teams?season=`, `/api/playcalling/teams/{team}?season=&side=`,
`/api/playcalling/teams/{team}/history?side=` and `/api/weeks/{season}/{week}/play-calls`, on a
synthetic `playcalling/<S>/` tree (`playcall_fixtures.py`: the league is a permutation, so every
rate, percentile and matchup shift can be worked out by hand; KC's page and MIN's defense are
pinned with exact numbers). Nothing reads the real data root except the `integration` tests at
the end, which compare the answers with the real tables and the TypeScript contract.

Safety (README §5): the endpoints write nothing under the data root, send no NaN / inf, no
secret-shaped text and no path, and take only a validated season, week, side and one of the 32
team codes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, NamedTuple

import playcall_fixtures as F
import polars as pl
import pytest
from app_helpers import BASE, FakeStatus, make_client, make_paths
from fastapi.testclient import TestClient
from playcall_fixtures import (
    SD32,
    TEAMS,
    TeamGames,
    Tend,
    World,
    build_doc,
    make_world,
    rank,
    read_in,
    schedule,
    team_with_rank,
    write_custom_games,
    write_games,
    write_season,
)
from week_fixtures import write_curated, write_full_week

from nflengine import settings as S
from nflengine.curate.teams import CANONICAL_TEAMS
from nflengine.ops.summary import scrub
from nflengine.paths import DataRootError

WINDOWS = ("season", "last4", "last_season")
FAIL_422 = "The season must be a year, the side offense or defense, and the team a 2-3 letter code."
NO_TEAM = "No such team: use one of the 32 team codes."
WEEK5 = [
    "2026_05_ARI_ATL",
    "2026_05_BAL_BUF",
    "2026_05_CAR_CHI",
    "2026_05_CIN_CLE",
    "2026_05_DAL_DEN",
    "2026_05_DET_GB",
    "2026_05_HOU_IND",
    "2026_05_JAX_LV",
    "2026_05_LA_LAC",
    "2026_05_MIA_MIN",
    "2026_05_NE_NO",
    "2026_05_NYG_NYJ",
    "2026_05_PHI_PIT",
    "2026_05_SEA_SF",
    "2026_05_TB_TEN",
]


# ---- helpers -----------------------------------------------------------------------------------


def get(client: TestClient, url: str, status: int = 200) -> dict[str, Any]:
    r = client.get(url)
    assert r.status_code == status, (url, r.status_code, r.text[:400])
    return r.json()


def r6(x: float | None) -> float | None:
    return round(x, 6) if x is not None and math.isfinite(x) else None


def r1(x: float | None) -> float | None:
    return round(x, 1) if x is not None and math.isfinite(x) else None


def exp_cell(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """What a stored row looks like in an answer: 6 decimals, a percentile to 1, small = n < 20."""
    if row is None:
        return None
    n = int(row["n"])
    return {
        "n": n,
        "games": row["games"],
        "value": r6(row["value"]),
        "league": r6(row["league_value"]),
        "diff": r6(row["diff"]),
        "pct": r1(row["pct"]),
        "small": n < 20,
    }


def cell_at(
    tend: Tend, team: str, side: str, as_of: int, window: str, metric: str, sit: str | None = None
):
    key = tend.key(team, side, as_of, window, metric, sit or read_in(metric))
    return exp_cell(tend.rows.get(key))


class Base(NamedTuple):
    root: Path
    paths: Any
    world: World
    client: TestClient


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> Base:
    """One tree for the read-only tests (module scope: the tables are written once)."""
    root = tmp_path_factory.mktemp("pc01")
    paths = make_paths(root)
    world = make_world(paths)
    return Base(root, paths, world, make_client(root))


def solo(tmp_path: Path, seasons: list[tuple[Tend, dict | None]] = (), **kw) -> TestClient:
    """An app over a data root holding just these seasons (no schedule unless a test writes one)."""
    paths = make_paths(tmp_path)
    for tend, doc in seasons:
        write_season(paths, tend, doc)
    return make_client(tmp_path, **kw)


def ids(items: list[dict[str, Any]], key: str = "metric") -> list[str]:
    return [i[key] for i in items]


# ---- the fixture itself (a guard on the guard) -------------------------------------------------


def test_the_fixture_league_has_the_documented_mean_and_spread(base):
    t = base.world.tend[2026]
    for side, metric in (("offense", "success_rate"), ("defense", "success_rate")):
        rows = [t.get(team, side, 5, "season", metric, "all") for team in TEAMS]
        values = [r["value"] for r in rows]
        assert sorted(round(v, 9) for v in values) == [round(0.10 + 0.01 * p, 9) for p in range(32)]
        assert statistics.mean(values) == pytest.approx(rows[0]["league_value"])
        assert statistics.stdev(values) == pytest.approx(0.01 * SD32)
        assert sorted(round(r["pct"] * 31 / 100) for r in rows) == list(range(32))
    assert pytest.approx(9.3808315196) == SD32
    # the shift anchors the tests lean on: (p - 15.5) / SD32 to two decimals
    anchors = [(31, 1.65), (30, 1.55), (29, 1.44), (25, 1.01), (24, 0.91), (7, -0.91), (6, -1.01)]
    assert [round((p - 15.5) / SD32, 2) for p, _ in anchors] == [s for _, s in anchors]
    assert [round((p - 15.5) / SD32, 2) for p in (2, 1, 0)] == [-1.44, -1.55, -1.65]


def test_the_fixture_team_game_schema_is_the_builds():
    """The team-game columns are not a constant in build.py: run its table builder on one row."""
    from nflengine.playcalling import build as B

    tg = pl.DataFrame(
        {
            "season": [2026],
            "week": [1],
            "game_id": ["2026_01_KC_LV"],
            "team": ["KC"],
            "opponent": ["LV"],
            "side": ["offense"],
            "metric": ["proe"],
            "situation": ["all"],
            "num": [1.0],
            "den": [2],
        },
        schema_overrides={"season": pl.Int32, "week": pl.Int32, "den": pl.Int64},
    )
    assert dict(B.team_game_table(tg).schema) == F.TG_SCHEMA


def test_the_fixture_tree_is_what_the_build_writes(base):
    d = base.paths.playcalling / "2026"
    assert sorted(p.name for p in d.iterdir()) == [
        "build.json",
        "league_tendencies.parquet",
        "team_game_tendencies.parquet",
        "team_tendencies.parquet",
    ]
    assert not (base.paths.playcalling / "2027" / "build.json").exists()
    doc = json.loads((d / "build.json").read_text("utf-8"))
    assert doc["as_of_weeks"] == [1, 5] and doc["history_metrics"] is False


# ---- the teams grid ----------------------------------------------------------------------------


def test_grid_header_and_columns(base):
    b = get(base.client, "/api/playcalling/teams?season=2026")
    assert (b["status"], b["season"], b["as_of_week"], b["through_week"]) == ("ok", 2026, 5, 4)
    assert (b["window"], b["min_n"], b["message"]) == ("season", 20, None)
    assert b["seasons"] == [2026, 2025, 2024, 2023, 2022, 2019]  # 2027 has no build.json
    assert b["built_at"] == "2026-10-11T00:15:21+00:00"
    assert b["ftn_waiting"] == ["ATL at NO, week 4"]
    cols = b["columns"]
    assert len({c["id"] for c in cols}) == len(cols)
    assert all(c["id"] == f"{c['side']}.{c['metric']}" for c in cols)
    assert [c["id"] for c in cols if c["signature"]] == ["offense.proe", "defense.blitz_rate"]
    proe = next(c for c in cols if c["id"] == "offense.proe")
    assert (proe["unit"], proe["situation"], proe["digits"], proe["caller"]) == (
        "over_expected",
        "neutral",
        1,
        "offense",
    )
    assert next(c for c in cols if c["id"] == "defense.blitz_rate")["caller"] == "defense"
    assert next(c for c in cols if c["id"] == "offense.play_action_rate")["per"] == "dropback"


def test_grid_cells_are_the_read_in_numbers_of_the_newest_as_of_week(base):
    b = get(base.client, "/api/playcalling/teams?season=2026")
    assert ids(b["teams"], "team") == TEAMS == sorted(CANONICAL_TEAMS)
    tend = base.world.tend[2026]
    for t in b["teams"]:
        assert t["games"] == 4
        assert set(t["cells"]) == {c["id"] for c in b["columns"]}
        for c in b["columns"]:
            want = cell_at(tend, t["team"], c["side"], 5, "season", c["metric"], c["situation"])
            assert t["cells"][c["id"]] == want, (t["team"], c["id"])
    kc = next(t for t in b["teams"] if t["team"] == "KC")
    # KC's neutral PROE, worked out by hand: 3.7 points over play-by-play's expectation, the
    # league at -2.0, so +5.7 above it, 90th percentile on 177 plays
    assert kc["cells"]["offense.proe"] == {
        "n": 177,
        "games": 4,
        "value": 0.037,
        "league": -0.02,
        "diff": 0.057,
        "pct": 90.0,
        "small": False,
    }
    # the same metric in `all` plays (the decoy 0.9) never stands in for the neutral one
    assert kc["cells"]["offense.proe"]["value"] != 0.9


def test_grid_ignores_history_only_rows(base):
    """A research row sharing KC's key, written after the real one, must not win."""
    tend = base.world.tend[2026]
    assert any(
        r["history_only"] and r["team"] == "KC" and r["metric"] == "proe" for r in tend.extra
    )
    b = get(base.client, "/api/playcalling/teams?season=2026")
    kc = next(t for t in b["teams"] if t["team"] == "KC")
    assert kc["cells"]["offense.proe"]["n"] == 177 and kc["cells"]["offense.proe"]["value"] == 0.037
    minn = next(t for t in b["teams"] if t["team"] == "MIN")
    assert minn["cells"]["defense.blitz_rate"]["value"] == 0.725
    assert minn["cells"]["defense.blitz_rate"]["n"] == 300


def test_grid_non_finite_numbers_are_null(base):
    b = get(base.client, "/api/playcalling/teams?season=2026")
    la = next(t for t in b["teams"] if t["team"] == "LA")
    assert la["cells"]["offense.proe"] == {
        "n": 200,
        "games": 4,
        "value": None,
        "league": None,
        "diff": None,
        "pct": None,
        "small": False,
    }


def test_grid_small_flag_at_the_edge(tmp_path):
    t = Tend(2026).fill(2, F.week_pairs(["motion_rate", "proe"]), teams=["KC", "LA", "DEN"])
    t.pin("KC", "offense", 2, "season", "motion_rate", "all", n=19)
    t.pin("LA", "offense", 2, "season", "motion_rate", "all", n=20)
    t.pin("DEN", "offense", 2, "season", "motion_rate", "all", n=0)  # no plays at all
    b = get(solo(tmp_path, [(t, build_doc(2026, 2))]), "/api/playcalling/teams?season=2026")
    cell = {tm["team"]: tm["cells"]["offense.motion_rate"] for tm in b["teams"]}
    assert (cell["KC"]["n"], cell["KC"]["small"]) == (19, True)
    assert (cell["LA"]["n"], cell["LA"]["small"]) == (20, False)
    assert (cell["DEN"]["n"], cell["DEN"]["small"]) == (0, True)
    assert cell["ARI"] is None  # no row at all: no cell


def test_grid_before_week_1_is_last_seasons_window(tmp_path):
    t = Tend(2027).fill(1, F.week_pairs(["proe", "blitz_rate"]))
    b = get(solo(tmp_path, [(t, build_doc(2027, 1))]), "/api/playcalling/teams?season=2027")
    assert (b["as_of_week"], b["through_week"], b["window"]) == (1, None, "last_season")
    kc = next(tm for tm in b["teams"] if tm["team"] == "KC")
    assert kc["cells"]["offense.proe"] == cell_at(t, "KC", "offense", 1, "last_season", "proe")
    assert kc["cells"]["offense.proe"]["games"] == 17


def test_grid_for_a_season_before_ftn_uses_pfr_blitzes(base):
    b = get(base.client, "/api/playcalling/teams?season=2019")
    assert b["status"] == "ok" and b["as_of_week"] == 22
    got = ids(b["columns"], "id")
    assert "defense.blitz_rate_pfr" in got and "defense.blitz_rate" not in got
    assert [c["id"] for c in b["columns"] if c["signature"]] == [
        "offense.proe",
        "defense.blitz_rate_pfr",
    ]
    ftn = {
        "offense.play_action_rate",
        "offense.motion_rate",
        "offense.qb_under_center_share",
        "defense.rushers_avg",
        "defense.heavy_box_rate",
    }
    assert not ftn & set(got), "no FTN column without FTN data"
    kc = next(t for t in b["teams"] if t["team"] == "KC")
    assert kc["cells"]["defense.blitz_rate_pfr"] == cell_at(
        base.world.tend[2019], "KC", "defense", 22, "season", "blitz_rate_pfr"
    )


def test_a_built_season_without_its_tables_is_an_empty_grid_not_an_error(tmp_path):
    t = Tend(2026).fill(2, F.week_pairs(["proe"]), teams=["KC"])
    c = solo(tmp_path)
    write_season(make_paths(tmp_path), t, build_doc(2026, 5), tables=False)
    b = get(c, "/api/playcalling/teams?season=2026")
    assert b["status"] == "ok" and len(b["teams"]) == 32
    # no blitz data at all: the defense's signature is the PROE offenses had against it
    assert ids(b["columns"], "id") == ["offense.proe", "defense.proe"]
    assert all(v is None for tm in b["teams"] for v in tm["cells"].values())
    team = get(c, "/api/playcalling/teams/KC?season=2026")
    assert (team["status"], team["identity"], team["summary"], team["weekly"]) == (
        "ok",
        [],
        [],
        None,
    )
    assert team["games"] == 0 and team["next_game"] is None
    assert get(c, "/api/weeks/2026/3/play-calls")["games"] == []


# ---- seasons: the default, not built, validation -----------------------------------------------


def test_default_season_is_the_newest_with_a_build_json(base):
    a = get(base.client, "/api/playcalling/teams")
    assert a["season"] == 2026 and a["seasons"][0] == 2026  # 2027's folder has no build.json
    team = get(base.client, "/api/playcalling/teams/KC")
    assert team["season"] == 2026 and team["as_of_week"] == 5


def test_default_season_follows_what_is_built(tmp_path):
    t = Tend(2025).fill(23, F.week_pairs(["proe"]), windows=("season",))
    c = solo(tmp_path, [(t, build_doc(2025, 23))])
    assert get(c, "/api/playcalling/teams")["season"] == 2025
    assert get(c, "/api/playcalling/teams/KC")["season"] == 2025


@pytest.mark.parametrize("make_folder", [False, True])
def test_nothing_built_names_the_command(tmp_path, make_folder):
    paths = make_paths(tmp_path)
    if make_folder:
        paths.playcalling.mkdir()
        (paths.playcalling / "2026").mkdir()  # a folder without build.json
        (paths.playcalling / "notes").mkdir()
    c = make_client(tmp_path)
    teams = get(c, "/api/playcalling/teams")
    assert (teams["status"], teams["season"], teams["seasons"]) == ("not_built", 2026, [])
    assert "nfl playcalling build --season 2026" in teams["message"]
    assert (teams["teams"], teams["as_of_week"], teams["through_week"]) == ([], None, None)
    assert [c["signature"] for c in teams["columns"]].count(
        True
    ) == 2  # the empty grid keeps its shape
    team = get(c, "/api/playcalling/teams/KC")
    assert team["status"] == "not_built" and "nfl playcalling build" in team["message"]
    assert (team["identity"], team["situations"], team["field"], team["runs"]) == (
        [],
        None,
        None,
        [],
    )
    assert (team["weekly"], team["next_game"], team["notes"], team["games"]) == (None, None, [], 0)
    hist = get(c, "/api/playcalling/teams/KC/history")
    assert hist["status"] == "not_built" and "--history all" in hist["message"]
    assert (
        "nfl playcalling build --season 2023" in hist["message"]
    )  # nothing built: the first FTN-charted season
    week = get(c, "/api/weeks/2026/5/play-calls")
    assert (
        week["status"] == "not_built" and "nfl playcalling build --season 2026" in week["message"]
    )
    assert (week["games"], week["shifts"], week["as_of_week"]) == ([], [], None)
    assert len(week["metrics"]) >= 5, "the empty tab still knows its rows"


def test_a_valid_season_that_is_not_built_is_an_empty_state_with_the_command(base):
    b = get(base.client, "/api/playcalling/teams?season=2015")
    assert b["status"] == "not_built" and b["season"] == 2015
    assert "uv run nfl playcalling build --season 2015" in b["message"]
    assert b["seasons"] == [2026, 2025, 2024, 2023, 2022, 2019]
    assert get(base.client, "/api/playcalling/teams/KC?season=2027")["status"] == "not_built"


@pytest.mark.parametrize("season", [1999, 2100])
def test_the_ends_of_the_season_range_are_valid(base, season):
    for url in (
        f"/api/playcalling/teams?season={season}",
        f"/api/playcalling/teams/KC?season={season}",
    ):
        b = get(base.client, url)
        assert b["status"] == "not_built" and b["season"] == season


@pytest.mark.parametrize(
    "q", ["season=1998", "season=2101", "season=abc", "season=", "season=20.5"]
)
def test_season_must_be_a_year(base, q):
    for url in (f"/api/playcalling/teams?{q}", f"/api/playcalling/teams/KC?{q}"):
        r = base.client.get(url)
        assert r.status_code == 422, url
        err = r.json()["error"]
        assert err["code"] == "bad_request" and err["message"] == FAIL_422
        value = q.split("=")[1]
        assert not value or value not in r.text, "the input is not echoed"


# ---- a team's page: identity -------------------------------------------------------------------


def team_page(base: Base, team: str = "KC", side: str = "offense", season: int = 2026):
    return get(base.client, f"/api/playcalling/teams/{team}?season={season}&side={side}")


def test_team_page_header(base):
    b = team_page(base)
    assert (b["status"], b["team"], b["side"], b["season"]) == ("ok", "KC", "offense", 2026)
    assert (b["as_of_week"], b["through_week"], b["games"], b["min_n"]) == (5, 4, 4, 20)
    assert team_page(base, side="defense")["side"] == "defense"
    assert get(base.client, "/api/playcalling/teams/KC")["side"] == "offense"  # the default


def test_offense_identity_groups_and_rows(base):
    b = team_page(base)
    assert ids(b["identity"], "id") == ["pass_run", "formation", "pass_game", "results"]
    got = {g["id"]: ids(g["rows"]) for g in b["identity"]}
    assert got == {
        "pass_run": ["proe", "dropback_rate", "no_huddle_rate"],
        # shotgun_rate (play-by-play's flag) steps aside once FTN splits the snap three ways
        "formation": [
            "qb_under_center_share",
            "qb_shotgun_share",
            "qb_pistol_share",
            "motion_rate",
        ],
        "pass_game": ["play_action_rate", "screen_rate", "rpo_rate", "deep_shot_rate", "adot"],
        "results": ["explosive_rate", "epa_per_play", "success_rate"],
    }
    assert all(g["title"] and "note" in g for g in b["identity"])


def test_identity_windows_hold_the_stored_numbers(base):
    b = team_page(base)
    rows = {r["metric"]: r for g in b["identity"] for r in g["rows"]}
    tend = base.world.tend[2026]
    for m, row in rows.items():
        assert list(row["windows"]) == list(WINDOWS), m
        for w in WINDOWS:
            assert row["windows"][w] == cell_at(tend, "KC", "offense", 5, w, m, row["situation"])
    proe = rows["proe"]
    assert proe["situation"] == "neutral" and proe["unit"] == "over_expected"
    assert proe["windows"]["season"] == {
        "n": 177,
        "games": 4,
        "value": 0.037,
        "league": -0.02,
        "diff": 0.057,
        "pct": 90.0,
        "small": False,
    }
    # 6 decimals and a percentile to one: 0.0365574 -> 0.036557, diff 0.0560574 -> 0.056057
    assert proe["windows"]["last_season"] == {
        "n": 638,
        "games": 17,
        "value": 0.036557,
        "league": -0.0195,
        "diff": 0.056057,
        "pct": 90.3,
        "small": False,
    }
    assert rows["rpo_rate"]["windows"]["season"]["value"] == 0.144


def test_identity_small_flag_at_the_edge_and_a_missing_window_is_null(base):
    rows = {r["metric"]: r for g in team_page(base)["identity"] for r in g["rows"]}
    pa = rows["play_action_rate"]["windows"]
    assert (pa["season"]["n"], pa["season"]["small"]) == (19, True)
    assert (pa["last4"]["n"], pa["last4"]["small"]) == (20, False)
    assert rows["proe"]["windows"]["last4"]["small"] is True  # n = 19
    screen = rows["screen_rate"]["windows"]
    assert (
        screen["last4"] is None
        and screen["season"] is not None
        and screen["last_season"] is not None
    )


def test_history_only_rows_never_reach_the_identity(base):
    rows = {r["metric"]: r for g in team_page(base)["identity"] for r in g["rows"]}
    assert rows["proe"]["windows"]["season"]["n"] == 177  # not the research row's 999
    d = {r["metric"]: r for g in team_page(base, "MIN", "defense")["identity"] for r in g["rows"]}
    assert d["blitz_rate"]["windows"]["season"]["value"] == 0.725
    assert not any(m.startswith(("personnel_", "route_", "cov_", "formation_")) for m in rows | d)


def test_defense_identity_groups_and_wording(base):
    b = team_page(base, "MIN", "defense")
    assert ids(b["identity"], "id") == ["pressure", "box", "faced", "results"]
    got = {g["id"]: ids(g["rows"]) for g in b["identity"]}
    assert got["pressure"] == ["blitz_rate", "blitz_rate_pfr", "rushers_avg"]
    assert got["box"] == ["box_avg", "light_box_rate", "heavy_box_rate"]
    assert got["faced"] == [
        "proe",
        "dropback_rate",
        "motion_rate",
        "play_action_rate",
        "screen_rate",
        "deep_shot_rate",
        "adot",
    ]
    notes = {g["id"]: g["note"] for g in b["identity"]}
    assert "depend on who it played" in notes["faced"] and notes["pressure"] is None
    assert any("what offenses did against it" in n for n in b["notes"])
    callers = {r["metric"]: r["caller"] for g in b["identity"] for r in g["rows"]}
    assert callers["blitz_rate"] == "defense" and callers["proe"] == "offense"


def test_pre_ftn_season_falls_back_to_play_by_play_shotgun(base):
    b = team_page(base, season=2019)
    got = {g["id"]: ids(g["rows"]) for g in b["identity"]}
    assert got["formation"] == ["shotgun_rate"]
    assert got["pass_game"] == ["deep_shot_rate", "adot"], "no FTN, no play-action / screen / RPO"
    assert b["weekly"] is None and b["games"] == 21  # 21 games before the 22nd as-of week


# ---- the five-second summary -------------------------------------------------------------------


def test_offense_summary_is_proe_then_the_two_furthest_from_the_middle(base):
    # RPO (100th pct) and no-huddle (3.2) are furthest from 50; under center (6.5) is third; the
    # play-action rate is lower still but on n = 19, so it is left out
    assert team_page(base)["summary"] == [
        "Pass rate over expected: +3.7 points in neutral situations (league \u22122.0), "
        "90th percentile.",
        "RPO: 14.4% of snaps (league 6.2%), the league's highest.",
        "No-huddle: 2.5% of snaps (league 9.9%), 3rd percentile.",
    ]


def test_defense_summary_says_what_offenses_did_against_it(base):
    assert team_page(base, "MIN", "defense")["summary"] == [
        "Blitz: 72.5% of dropbacks (league 31.1%), the league's highest.",
        "Offenses against it, pass rate over expected: \u22128.9 points in neutral situations "
        "(league \u22122.2), the league's lowest.",
        "Heavy box (8+): 5.8% of snaps (league 5.7%), 88th percentile.",
    ]


def test_summary_leaves_out_every_small_rate(tmp_path):
    t = Tend(2026).fill(4, F.page_pairs(["proe", "no_huddle_rate", "rpo_rate"]), n=19, teams=["KC"])
    b = get(solo(tmp_path, [(t, build_doc(2026, 4))]), "/api/playcalling/teams/KC?season=2026")
    assert b["summary"] == []
    assert b["identity"], "the page still shows the rows (greyed)"
    assert all(
        c["small"] for g in b["identity"] for r in g["rows"] for c in r["windows"].values() if c
    )


# ---- a team's page: situations, field, runs ----------------------------------------------------


def test_situations_table(base):
    s = team_page(base)["situations"]
    assert ids(s["metrics"]) == [
        "dropback_rate",
        "proe",
        "shotgun_rate",
        "deep_shot_rate",
        "play_action_rate",
        "blitz_rate",
    ]
    assert all(m["situation"] == "all" for m in s["metrics"]), "every column is read per situation"
    assert (s["baseline"]["situation"], s["baseline"]["label"]) == ("all", "All plays")
    fams = {f["family"]: f for f in s["families"]}
    assert [f["family"] for f in s["families"]] == ["down_distance", "field_zone", "score"]
    assert [f["title"] for f in s["families"]] == ["Down & distance", "Field zone", "Score & clock"]
    assert ids(fams["down_distance"]["rows"], "situation") == [
        "1st_down",
        "2nd_short",
        "2nd_medium",
        "2nd_long",
        "3rd_short",
        "3rd_medium",
        "3rd_long",
        "4th_down",
    ]
    assert ids(fams["field_zone"]["rows"], "situation") == [
        "own_deep",
        "own_half",
        "opp_half",
        "red_zone",
    ]
    assert ids(fams["score"]["rows"], "situation") == [
        "trail_9plus",
        "trail_1to8",
        "tied",
        "lead_1to8",
        "lead_9plus",
        "two_minute",
    ]
    labels = {r["situation"]: r["label"] for f in s["families"] for r in f["rows"]}
    assert labels["2nd_short"] == "2nd & 1-3" and labels["3rd_long"] == "3rd & 7+"
    assert labels["opp_half"] == "Opp. 49-21" and labels["two_minute"] == "Last 2 min of a half"


def test_situation_cells_are_the_stored_numbers(base):
    s = team_page(base)["situations"]
    tend = base.world.tend[2026]
    rows = {r["situation"]: r for f in s["families"] for r in f["rows"]} | {"all": s["baseline"]}
    for sit, row in rows.items():
        assert set(row["cells"]) == {m["metric"] for m in s["metrics"]}
        for m, windows in row["cells"].items():
            assert list(windows) == list(WINDOWS)
            for w in WINDOWS:
                assert windows[w] == cell_at(tend, "KC", "offense", 5, w, m, sit), (sit, m, w)
    assert rows["3rd_long"]["cells"]["dropback_rate"]["season"] == {
        "n": 42,
        "games": 4,
        "value": 0.923,
        "league": 0.9,
        "diff": 0.023,
        "pct": 55.0,
        "small": False,
    }
    assert rows["1st_down"]["cells"]["play_action_rate"]["season"]["value"] == 0.39
    # `all` is the baseline, not the neutral decoy
    assert s["baseline"]["cells"]["proe"]["season"] == cell_at(
        base.world.tend[2026], "KC", "offense", 5, "season", "proe", "all"
    )
    assert s["baseline"]["cells"]["proe"]["season"]["value"] == 0.9


def test_field_zones_directions_and_depth(base):
    b = team_page(base)
    f = b["field"]
    assert [(z["depth"], z["direction"], z["metric"]) for z in f["zones"]] == [
        ("short", "left", "pass_short_left"),
        ("short", "middle", "pass_short_middle"),
        ("short", "right", "pass_short_right"),
        ("deep", "left", "pass_deep_left"),
        ("deep", "middle", "pass_deep_middle"),
        ("deep", "right", "pass_deep_right"),
    ]
    assert [(z["depth"], z["direction"]) for z in f["directions"]] == [
        (None, "left"),
        (None, "middle"),
        (None, "right"),
    ]
    assert ids(f["depth"]) == ["deep_shot_rate", "adot"]
    tend = base.world.tend[2026]
    for z in [*f["zones"], *f["directions"]]:
        assert list(z["windows"]) == list(WINDOWS)
        assert z["windows"]["season"] == cell_at(tend, "KC", "offense", 5, "season", z["metric"])
    assert f["depth"][1]["unit"] == "mean" and f["depth"][1]["per"] == "attempt"


def test_run_lanes_left_to_right(base):
    runs = team_page(base)["runs"]
    assert [(r["lane"], r["label"]) for r in runs] == [
        ("left_end", "Left end"),
        ("left_tackle", "Left tackle"),
        ("left_guard", "Left guard"),
        ("middle", "Middle"),
        ("right_guard", "Right guard"),
        ("right_tackle", "Right tackle"),
        ("right_end", "Right end"),
    ]
    assert all(r["metric"] == f"run_{r['lane']}" for r in runs)
    right_end = next(r for r in runs if r["lane"] == "right_end")
    assert right_end["windows"]["season"] == {
        "n": 60,
        "games": 4,
        "value": 0.0894,
        "league": 0.1043,
        "diff": -0.0149,
        "pct": 41.9,
        "small": False,
    }


# ---- a team's page: week by week, the next game, notes -----------------------------------------


def test_weekly_games_and_series(base):
    w = team_page(base)["weekly"]
    assert w["games"] == [
        {"week": 1, "game_id": "2026_01_KC_LV", "opponent": "LV", "home": False},
        {"week": 2, "game_id": "2026_02_BAL_KC", "opponent": "BAL", "home": True},
        {"week": 4, "game_id": "2026_04_KC_DEN", "opponent": "DEN", "home": False},
    ]  # week 3 was a bye
    assert ids(w["series"]) == [
        "proe",
        "play_action_rate",
        "motion_rate",
        "qb_under_center_share",
        "deep_shot_rate",
        "epa_per_play",
    ]
    s = {x["metric"]: x for x in w["series"]}
    assert s["proe"]["points"] == [
        {"week": 1, "value": 0.05, "n": 40},
        {"week": 2, "value": 0.01, "n": 38},
        {"week": 4, "value": None, "n": 35},  # NaN in the table
    ]
    # the neutral PROE rows are used, not the `all` decoy (0.999) or the research copy (0.9)
    assert [p["value"] for p in s["motion_rate"]["points"]] == [0.5, 0.45, 0.52]
    # a game with no row for the metric: a gap in the line, not a zero
    assert s["qb_under_center_share"]["points"][2] == {"week": 4, "value": None, "n": 0}
    assert s["epa_per_play"]["points"][1]["value"] == -0.05
    assert s["deep_shot_rate"]["points"][2]["value"] == 0.0
    # the reference line is the page's own league rate
    assert s["proe"]["league"] == -0.02 and s["qb_under_center_share"]["league"] == 0.337
    assert s["proe"]["situation"] == "neutral" and s["proe"]["unit"] == "over_expected"


def test_weekly_defense_side_uses_its_own_rows(base):
    w = team_page(base, side="defense")["weekly"]
    assert ids(w["series"]) == ["blitz_rate", "proe"]
    s = {x["metric"]: x for x in w["series"]}
    assert [p["value"] for p in s["blitz_rate"]["points"]] == [0.31, 0.45, 0.28]
    assert [p["n"] for p in s["proe"]["points"]] == [37, 37, 37]
    assert [g["week"] for g in w["games"]] == [1, 2, 4]


def test_a_team_with_no_game_rows_has_no_weekly_block(base):
    assert team_page(base, "LA")["weekly"] is None
    assert team_page(base, "LA")["status"] == "ok"


def test_next_game_after_a_bye_is_the_following_week(base):
    g = team_page(base)["next_game"]  # KC is on a bye in week 5
    assert g == {
        "season": 2026,
        "week": 6,
        "game_id": "2026_06_KC_WAS",
        "opponent": "WAS",
        "home": False,
        "kickoff": "2026-10-18T17:00:00Z",
    }


def test_next_game_this_week_home_and_away(base):
    la = team_page(base, "LA")["next_game"]
    assert la == {
        "season": 2026,
        "week": 5,
        "game_id": "2026_05_LA_LAC",
        "opponent": "LAC",
        "home": False,
        "kickoff": "2026-10-11T20:05:00Z",
    }
    lac = team_page(base, "LAC")["next_game"]
    assert (lac["week"], lac["opponent"], lac["home"]) == (5, "LA", True)
    tn = team_page(base, "TEN")["next_game"]  # TB at TEN has no kickoff time yet
    assert (tn["game_id"], tn["kickoff"]) == ("2026_05_TB_TEN", None)


def test_next_game_looks_forward_only_and_may_be_absent(base):
    # KC's week-4 game is behind the page's as-of week; and no 2025 schedule is on file
    assert team_page(base)["next_game"]["week"] == 6
    assert team_page(base, season=2025)["next_game"] is None


def test_notes_name_the_games_ftn_is_waiting_for(base):
    notes = team_page(base)["notes"]
    assert len(notes) == 1 and "ATL at NO, week 4" in notes[0] and "FTN" in notes[0]
    d = team_page(base, "MIN", "defense")["notes"]
    assert len(d) == 2 and "what offenses did against it" in d[0] and "ATL at NO" in d[1]


def test_week_1_note_says_every_rate_is_last_seasons(tmp_path):
    t = Tend(2027).fill(1, F.page_pairs(["proe", "blitz_rate"]), teams=["KC"])
    c = solo(tmp_path, [(t, build_doc(2027, 1))])
    b = get(c, "/api/playcalling/teams/KC?season=2027")
    assert (b["as_of_week"], b["through_week"], b["games"]) == (1, None, 0)
    assert any("last season's" in n for n in b["notes"]) and len(b["notes"]) == 1
    row = next(r for g in b["identity"] for r in g["rows"] if r["metric"] == "proe")
    assert row["windows"]["season"] is None and row["windows"]["last_season"]["n"] == 200
    assert b["summary"], "week 1's sentences read the last-season window"


# ---- team and side validation ------------------------------------------------------------------


@pytest.mark.parametrize("team", sorted(CANONICAL_TEAMS))
def test_every_canonical_team_has_a_page_on_both_sides(base, team):
    for side in ("offense", "defense"):
        b = get(base.client, f"/api/playcalling/teams/{team}?season=2026&side={side}")
        assert (b["team"], b["side"], b["status"]) == (team, side, "ok")
        assert b["identity"], team


@pytest.mark.parametrize(
    "team", ["kc", "Kc", "K", "KCCC", "K1", "ZZ9", "%20KC", "KC%20", "..", "K-C"]
)
def test_a_malformed_team_is_a_422_that_does_not_echo_it(base, team):
    for url in (
        f"/api/playcalling/teams/{team}",
        f"/api/playcalling/teams/{team}/history",
    ):
        r = base.client.get(url)
        # ".." is resolved by the client into a path with no route; everything else is a bad code
        assert r.status_code == (404 if team == ".." else 422), (url, r.status_code)
        err = r.json()["error"]
        if r.status_code == 422:
            assert err["code"] == "bad_request" and err["message"] == FAIL_422
        for needle in {"ZZ9", "KCCC", "K-C"} & {team}:
            assert needle not in r.text


@pytest.mark.parametrize("team", ["XYZ", "OAK", "SD", "STL", "AAA", "LVR"])
def test_a_well_formed_code_that_is_not_a_team_is_a_404_that_does_not_echo_it(base, team):
    assert team not in CANONICAL_TEAMS
    for url in (
        f"/api/playcalling/teams/{team}",
        f"/api/playcalling/teams/{team}?season=2026&side=defense",
        f"/api/playcalling/teams/{team}/history",
    ):
        r = base.client.get(url)
        assert r.status_code == 404, url
        assert r.json()["error"] == {"code": "not_found", "message": NO_TEAM}
        assert team not in r.text


@pytest.mark.parametrize("side", ["both", "OFFENSE", "off", "", "offense,defense", "defence"])
def test_side_must_be_offense_or_defense(base, side):
    for url in (
        f"/api/playcalling/teams/KC?side={side}",
        f"/api/playcalling/teams/KC/history?side={side}",
    ):
        r = base.client.get(url)
        assert r.status_code == 422, url
        assert r.json()["error"]["message"] == FAIL_422
        if side and side not in FAIL_422:
            assert side not in r.text


def test_unknown_routes_under_playcalling_stay_json_404s(base):
    for url in (
        "/api/playcalling",
        "/api/playcalling/",
        "/api/playcalling/league",
        "/api/playcalling/teams/KC/plays",
        "/api/playcalling/teams/KC/history/2025",
        "/api/weeks/2026/5/play-call",
    ):
        r = base.client.get(url)
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", url


def test_extra_query_parameters_cannot_name_a_file(base):
    plain = base.client.get("/api/playcalling/teams?season=2026").text
    for q in ("path=C:/Windows/win.ini", "file=../../x.parquet", "root=D:/", "cmd=dir"):
        assert base.client.get(f"/api/playcalling/teams?season=2026&{q}").text == plain


# ---- History (participation, research) ---------------------------------------------------------


def hist(base: Base, team: str = "KC", side: str = "offense") -> dict[str, Any]:
    return get(base.client, f"/api/playcalling/teams/{team}/history?side={side}")


def test_history_header(base):
    b = hist(base)
    assert (b["status"], b["message"], b["team"], b["side"]) == ("ok", None, "KC", "offense")
    assert b["seasons"] == [2023, 2024, 2025]  # oldest first; 2022 is before the FTN era
    assert b["research"] is True and b["min_n"] == 20
    assert "Research data" in b["source_note"] and "Never used by a forecast" in b["source_note"]


def test_history_groups_offense(base):
    groups = {g["id"]: g for g in hist(base)["groups"]}
    assert [g for g in groups] == ["personnel", "formation", "routes", "coverage", "pressure"]
    assert groups["personnel"]["kind"] == "stack" and groups["routes"]["kind"] == "bars"
    assert ids(groups["personnel"]["rows"]) == [
        "personnel_11",
        "personnel_12",
        "personnel_13",
        "personnel_21",
        "personnel_22",
        "personnel_10",
        "personnel_other",
    ]
    assert ids(groups["formation"]["rows"]) == [
        "formation_shotgun",
        "formation_under_center",
        "formation_pistol",
    ]  # the FTN-era names only
    assert ids(groups["pressure"]["rows"]) == ["pressure_rate", "time_to_throw_avg"]
    assert not any(
        r["metric"].startswith(("formation_single", "route_flat"))
        for g in groups.values()
        for r in g["rows"]
    )


def test_history_cells_are_each_seasons_whole_season_numbers(base):
    groups = {g["id"]: g for g in hist(base)["groups"]}
    for g in groups.values():
        for row in g["rows"]:
            assert list(row["seasons"]) == ["2023", "2024", "2025"], row["metric"]
            for s in (2023, 2024, 2025):
                want = cell_at(
                    base.world.tend[s], "KC", "offense", 23, "season", row["metric"], "all"
                )
                assert row["seasons"][str(s)] == want, (row["metric"], s)
    # the decoys: a play-by-play-flagged row on a participation metric, and another window
    p11 = next(r for r in groups["personnel"]["rows"] if r["metric"] == "personnel_11")
    assert p11["seasons"]["2025"]["value"] != 0.999 and p11["seasons"]["2025"]["n"] == 200
    p12 = next(r for r in groups["personnel"]["rows"] if r["metric"] == "personnel_12")
    assert p12["seasons"]["2025"]["value"] != 0.888


def test_history_routes_are_sorted_by_the_newest_season(base):
    routes = {g["id"]: g for g in hist(base)["groups"]}["routes"]["rows"]
    newest = [r["seasons"]["2025"]["value"] for r in routes]
    assert newest == sorted(newest, reverse=True) and len(routes) == 13


def test_history_small_samples_and_nan(base):
    groups = {g["id"]: g for g in hist(base)["groups"]}
    screen = next(r for r in groups["routes"]["rows"] if r["metric"] == "route_screen")
    assert screen["seasons"]["2024"]["n"] == 5 and screen["seasons"]["2024"]["small"] is True
    assert screen["seasons"]["2025"]["small"] is False
    ttt = next(r for r in groups["pressure"]["rows"] if r["metric"] == "time_to_throw_avg")
    assert ttt["seasons"]["2023"]["value"] is None and ttt["seasons"]["2023"]["n"] == 200


def test_history_defense_groups(base):
    b = hist(base, "MIN", "defense")
    groups = {g["id"]: ids(g["rows"]) for g in b["groups"]}
    assert list(groups) == ["coverage", "man_zone", "packages", "pressure", "personnel"]
    assert groups["man_zone"] == ["man_rate"]
    assert groups["packages"] == ["def_base_share", "def_nickel_share", "def_dime_share"]
    assert b["side"] == "defense"
    row = next(r for g in b["groups"] for r in g["rows"] if r["metric"] == "man_rate")
    assert row["seasons"]["2025"] == cell_at(
        base.world.tend[2025], "MIN", "defense", 23, "season", "man_rate", "all"
    )


def test_history_shows_only_the_newest_three_participation_seasons(tmp_path):
    paths = make_paths(tmp_path)
    make_world(paths, history_2026=True)
    c = make_client(tmp_path)
    assert get(c, "/api/playcalling/teams/KC/history")["seasons"] == [2024, 2025, 2026]


def test_history_needs_a_build_with_history_metrics(tmp_path):
    t = Tend(2026).fill(5, F.week_pairs(["proe"]), teams=["KC"])
    b = get(solo(tmp_path, [(t, build_doc(2026, 5))]), "/api/playcalling/teams/KC/history")
    assert b["status"] == "not_built" and b["seasons"] == [] and b["groups"] == []
    assert "uv run nfl playcalling build --season 2026 --history all" in b["message"]
    assert b["research"] is True  # still labelled


def test_history_for_a_team_without_rows_is_an_empty_ok(tmp_path):
    t = Tend(2025).fill(23, F.history_pairs(), windows=("season",), teams=["LV"])
    c = solo(
        tmp_path,
        [
            (t, build_doc(2025, 23, history=True)),
            (Tend(2024), build_doc(2024, 23, history=True)),
            (Tend(2023), build_doc(2023, 23, history=True)),
        ],
    )
    b = get(c, "/api/playcalling/teams/KC/history")
    assert (b["status"], b["groups"], b["seasons"]) == ("ok", [], [2023, 2024, 2025])


# ---- the Play calls week tab -------------------------------------------------------------------


MATCHUP_KEYS = {"metric", "offense", "defense", "league", "shift", "same_way"}


def metrics_of(body: dict[str, Any]) -> list[str]:
    return ids(body["metrics"])


def sample_sd(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    var = statistics.variance(values)
    return math.sqrt(var) if var > 0 else None


def ref_games(week: int) -> list[tuple[str, str, dt.datetime | None]]:
    rows = [(a, h, k) for w, a, h, k in schedule() if w == week and a in TEAMS and h in TEAMS]
    far = dt.datetime.max.replace(tzinfo=dt.UTC)
    return sorted(
        rows, key=lambda r: (r[2] is None, r[2] or far, F.game_id(2026, week, r[0], r[1]))
    )


def ref_play_calls(tend: Tend, week: int, metrics: list[str]) -> dict[str, Any]:
    """The tab worked out again from the stored rows: sorted games, two matchups each, a shift per
    cell (the defense's diff in SDs of the 32 defenses), and the 8 biggest (|shift| >= 1, neither
    cell small, one per game and offense)."""
    window = "season" if week > 1 else "last_season"
    cells = {k: exp_cell(r) for k, r in tend.cells(week, window, metrics).items()}
    sd = {}
    for m in metrics:
        vals = [
            c["value"]
            for (t, s, mm), c in cells.items()
            if s == "defense" and mm == m and c["value"] is not None
        ]
        sd[m] = sample_sd(vals)
    games, cands = [], []
    for away, home, kick in ref_games(week):
        gid = F.game_id(2026, week, away, home)
        mus = []
        for off, dfn in ((away, home), (home, away)):
            rows = []
            for m in metrics:
                o, d = cells.get((off, "offense", m)), cells.get((dfn, "defense", m))
                shift = (
                    round(d["diff"] / sd[m], 2) if d and d["diff"] is not None and sd[m] else None
                )
                same = None
                if o and d and o["diff"] is not None and d["diff"] is not None:
                    same = (o["diff"] > 0) == (d["diff"] > 0)
                rows.append(
                    {
                        "metric": m,
                        "offense": o,
                        "defense": d,
                        "league": (d or o or {}).get("league"),
                        "shift": shift,
                        "same_way": same,
                    }
                )
                if (
                    shift is not None
                    and abs(shift) >= 1
                    and not d["small"]
                    and o
                    and not o["small"]
                ):
                    cands.append((abs(shift), gid, off, dfn, m, shift))
            mus.append({"offense": off, "defense": dfn, "rows": rows})
        games.append({"game_id": gid, "away": away, "home": home, "kick": kick, "matchups": mus})
    shifts, seen = [], set()
    for _, gid, off, dfn, m, shift in sorted(cands, key=lambda c: -c[0]):
        if (gid, off) not in seen:
            seen.add((gid, off))
            shifts.append((gid, off, dfn, m, shift))
    by_matchup = defaultdict(int)
    for _, gid, off, *_rest in cands:
        by_matchup[(gid, off)] += 1
    return {"games": games, "shifts": shifts[:8], "candidates": cands, "multi": by_matchup}


def week_tab(base: Base, week: int, season: int = 2026) -> dict[str, Any]:
    return get(base.client, f"/api/weeks/{season}/{week}/play-calls")


def kick_text(k: dt.datetime | None) -> str | None:
    return None if k is None else k.isoformat().replace("+00:00", "Z")


def test_week5_header_and_games_in_kickoff_order(base):
    b = week_tab(base, 5)
    assert (b["status"], b["week"], b["window"], b["as_of_week"], b["through_week"]) == (
        "ok",
        5,
        "season",
        5,
        4,
    )
    assert "Descriptive" in b["note"] and "isn't a forecast" in b["note"]
    # Thursday, then Sunday by kickoff and game id; no kickoff time last; KC and WAS are on a bye;
    # the row with a code that is not a team is left out
    assert ids(b["games"], "game_id") == WEEK5
    first, last = b["games"][0], b["games"][-1]
    assert (first["away"], first["home"], first["kickoff"]) == (
        "ARI",
        "ATL",
        "2026-10-09T00:15:00Z",
    )
    assert (last["away"], last["home"], last["kickoff"]) == ("TB", "TEN", None)
    teams = {t for g in b["games"] for t in (g["away"], g["home"])}
    assert "KC" not in teams and "WAS" not in teams and "OAK" not in teams


def test_week5_every_matchup_row_is_the_stored_numbers(base):
    b = week_tab(base, 5)
    ms = metrics_of(b)
    assert len(set(ms)) == len(ms) >= 8 and {"proe", "play_action_rate", "blitz_rate"} <= set(ms)
    ref = ref_play_calls(base.world.tend[2026], 5, ms)
    assert len(b["games"]) == len(ref["games"]) == 15
    for g, rg in zip(b["games"], ref["games"], strict=True):
        assert (g["game_id"], g["away"], g["home"]) == (rg["game_id"], rg["away"], rg["home"])
        assert g["kickoff"] == kick_text(rg["kick"])
        assert len(g["matchups"]) == 2
        for mu, rmu in zip(g["matchups"], rg["matchups"], strict=True):
            assert (mu["offense"], mu["defense"]) == (rmu["offense"], rmu["defense"])
            assert ids(mu["rows"]) == ms
            for row, rrow in zip(mu["rows"], rmu["rows"], strict=True):
                assert set(row) == MATCHUP_KEYS
                assert row == rrow, (g["game_id"], mu["offense"], row["metric"])
    # [away offense vs home defense, home offense vs away defense]
    g0 = b["games"][0]
    assert [(m["offense"], m["defense"]) for m in g0["matchups"]] == [
        ("ARI", "ATL"),
        ("ATL", "ARI"),
    ]


def test_the_matchup_reads_the_defense_side_for_what_the_offense_will_face(base):
    b = week_tab(base, 5)
    mia = next(g for g in b["games"] if g["game_id"] == "2026_05_MIA_MIN")
    row = next(r for r in mia["matchups"][0]["rows"] if r["metric"] == "blitz_rate")
    # MIA's offense against MIN's defense: MIN's own blitz (pinned: 72.5%, n = 300), against how
    # often MIA has seen one (its offense row)
    assert row["defense"]["value"] == 0.725 and row["defense"]["n"] == 300
    assert row["offense"] == cell_at(
        base.world.tend[2026], "MIA", "offense", 5, "season", "blitz_rate"
    )
    assert row["league"] == 0.311  # the defense cell's league
    other = next(r for r in mia["matchups"][1]["rows"] if r["metric"] == "blitz_rate")
    assert other["defense"] == cell_at(
        base.world.tend[2026], "MIA", "defense", 5, "season", "blitz_rate"
    )


def test_the_shift_is_the_defenses_diff_in_standard_deviations(base):
    """screen_rate is unpinned: 32 ranks, so the shift is (p - 15.5) / SD32 to two decimals."""
    b = week_tab(base, 5)
    seen = 0
    for g in b["games"]:
        for mu in g["matchups"]:
            row = next(r for r in mu["rows"] if r["metric"] == "screen_rate")
            p = rank(mu["defense"], 5, "defense", "season", "screen_rate", "all")
            assert row["shift"] == round((p - 15.5) / SD32, 2), (g["game_id"], mu["defense"])
            seen += 1
    assert seen == 30


def test_week5_shifts_are_the_eight_biggest_by_the_rules(base):
    b = week_tab(base, 5)
    ref = ref_play_calls(base.world.tend[2026], 5, metrics_of(b))
    got = [(s["game_id"], s["offense"], s["defense"], s["metric"], s["shift"]) for s in b["shifts"]]
    assert got == ref["shifts"] and len(got) == 8
    mags = [abs(s["shift"]) for s in b["shifts"]]
    assert mags == sorted(mags, reverse=True) and min(mags) >= 1
    assert len({(s["game_id"], s["offense"]) for s in b["shifts"]}) == 8, "one per game and offense"
    assert any(n > 1 for n in ref["multi"].values()), (
        "the fixture has matchups with several candidates"
    )
    for s in b["shifts"]:  # the shift is the largest of that matchup's candidates
        best = max(c[0] for c in ref["candidates"] if (c[1], c[2]) == (s["game_id"], s["offense"]))
        assert abs(s["shift"]) == best
    assert set(b["shifts"][0]) == {"game_id", "offense", "defense", "metric", "shift", "text"}


def test_the_biggest_shift_is_minnesotas_blitz_and_its_sentence(base):
    top = week_tab(base, 5)["shifts"][0]
    assert (top["game_id"], top["offense"], top["defense"], top["metric"]) == (
        "2026_05_MIA_MIN",
        "MIA",
        "MIN",
        "blitz_rate",
    )
    assert top["shift"] > 3
    mia_faced = base.world.tend[2026].get("MIA", "offense", 5, "season", "blitz_rate", "all")[
        "value"
    ]
    assert top["text"] == (
        "MIN's blitz: 72.5% of dropbacks (the league's highest; league 31.1%). "
        f"MIA has faced {mia_faced * 100:.1f}%."
    )


def test_shift_sentences_name_the_teams_and_the_numbers(base):
    b = week_tab(base, 5)
    short = {m["metric"]: m["short"] for m in b["metrics"]}
    caller = {m["metric"]: m["caller"] for m in b["metrics"]}
    for s in b["shifts"]:
        t = s["text"]
        assert s["defense"] in t and s["offense"] in t and t.endswith(".")
        assert short[s["metric"]][1:].lower() in t.lower()
        assert "league" in t and ("highest" in t or "lowest" in t)
        if caller[s["metric"]] == "defense":
            assert t.startswith(f"{s['defense']}'s ") and "has faced" in t
        else:
            assert t.startswith(f"Against {s['defense']}: ")


def test_week3_reads_its_own_as_of_week(base):
    b = week_tab(base, 3)
    assert (b["as_of_week"], b["through_week"], b["window"]) == (3, 2, "season")
    ms = metrics_of(b)
    ref = ref_play_calls(base.world.tend[2026], 3, ms)
    assert ids(b["games"], "game_id") == ["2026_03_MIN_CHI", "2026_03_LA_SEA"]
    for g, rg in zip(b["games"], ref["games"], strict=True):
        for mu, rmu in zip(g["matchups"], rg["matchups"], strict=True):
            assert mu["rows"] == rmu["rows"]
    # the same metric differs between as-of weeks (week 5's cells are not week 3's)
    w5 = base.world.tend[2026].get("LA", "offense", 5, "season", "motion_rate", "all")["value"]
    w3 = b["games"][1]["matchups"][0]["rows"][ms.index("motion_rate")]["offense"]["value"]
    assert (
        r6(w5)
        != w3
        == r6(
            base.world.tend[2026].get("LA", "offense", 3, "season", "motion_rate", "all")["value"]
        )
    )
    got = [(s["game_id"], s["offense"], s["defense"], s["metric"], s["shift"]) for s in b["shifts"]]
    assert got == ref["shifts"]


def test_week3_non_finite_numbers_are_null_and_leave_the_spread_to_31_teams(base):
    b = week_tab(base, 3)
    chi = next(g for g in b["games"] if g["game_id"] == "2026_03_MIN_CHI")
    # MIN's defense PROE is NaN at week 3: no value, no diff, no shift; its offense row is fine
    row = next(
        r for r in chi["matchups"][1]["rows"] if r["metric"] == "proe"
    )  # CHI offense vs MIN defense
    assert row["defense"]["value"] is None and row["defense"]["diff"] is None
    assert row["shift"] is None and row["same_way"] is None
    sea = next(g for g in b["games"] if g["game_id"] == "2026_03_LA_SEA")
    pa = next(r for r in sea["matchups"][0]["rows"] if r["metric"] == "play_action_rate")
    assert pa["offense"]["league"] is None and pa["offense"]["value"] is not None  # an inf league
    assert pa["league"] == pa["defense"]["league"], "the row's league comes from the defense cell"


def test_week1_reads_last_seasons_window(base):
    b = week_tab(base, 1)
    assert (b["window"], b["as_of_week"], b["through_week"]) == ("last_season", 1, None)
    assert ids(b["games"], "game_id") == ["2026_01_KC_LV", "2026_01_LA_LAC", "2026_01_BAL_BUF"]
    ref = ref_play_calls(base.world.tend[2026], 1, metrics_of(b))
    for g, rg in zip(b["games"], ref["games"], strict=True):
        for mu, rmu in zip(g["matchups"], rg["matchups"], strict=True):
            assert mu["rows"] == rmu["rows"]
    kc = b["games"][0]["matchups"][0]["rows"][0]
    assert kc["offense"]["games"] == 17, "last season's games"
    assert [
        (s["game_id"], s["offense"], s["defense"], s["metric"], s["shift"]) for s in b["shifts"]
    ] == ref["shifts"]


def test_a_week_past_the_newest_as_of_week_is_not_yet(base):
    for week in (6, 7, 22):
        b = week_tab(base, week)
        assert b["status"] == "not_yet" and b["week"] == week
        assert (
            "nfl playcalling build --season 2026" in b["message"]
            and "through week 5" in b["message"]
        )
        assert (b["as_of_week"], b["through_week"], b["window"]) == (None, None, "season")
        assert (b["games"], b["shifts"]) == ([], [])
        assert b["season"] == 2026 and b["seasons"][0] == 2026
        assert len(b["metrics"]) >= 5
    assert week_tab(base, 5)["status"] == "ok"  # the newest as-of week itself is fine


def test_a_week_with_as_of_rows_but_no_schedule_is_ok_and_says_so(tmp_path):
    t = Tend(2026).fill(2, F.week_pairs(["blitz_rate", "proe"]))
    c = solo(tmp_path, [(t, build_doc(2026, 5))])
    b = get(c, "/api/weeks/2026/2/play-calls")
    assert (b["status"], b["games"], b["shifts"]) == ("ok", [], [])
    assert b["message"] == "No week-2 games in the schedule."
    paths = make_paths(tmp_path)
    write_games(paths)  # a schedule with nothing in week 2 but KC and MIN...
    assert len(get(c, "/api/weeks/2026/2/play-calls")["games"]) == 2


def test_play_calls_for_a_season_that_is_not_built(base):
    b = get(base.client, "/api/weeks/2015/5/play-calls")
    assert b["status"] == "not_built" and "--season 2015" in b["message"] and b["games"] == []


@pytest.mark.parametrize(
    "url",
    [
        "/api/weeks/2026/0/play-calls",
        "/api/weeks/2026/23/play-calls",
        "/api/weeks/1998/5/play-calls",
        "/api/weeks/2101/5/play-calls",
        "/api/weeks/abc/5/play-calls",
        "/api/weeks/2026/five/play-calls",
        "/api/weeks/2026/-1/play-calls",
        "/api/weeks/2026/5.5/play-calls",
    ],
)
def test_play_calls_season_and_week_are_validated(base, url):
    r = base.client.get(url)
    assert r.status_code == 422
    assert r.json()["error"] == {
        "code": "bad_request",
        "message": "Season and week must be whole numbers in range.",
    }
    for needle in ("abc", "five"):
        assert needle not in r.text


def slate_world(tmp_path, small_defense: bool):
    """One metric (blitz_rate), week 2, seven games; the ranks pick who is who. Returns the
    teams by role and the week tab's answer. With `small_defense`, the defense at rank 30 has
    n = 19 (a small sample); the spread of the league then leaves it out."""
    paths = make_paths(tmp_path)
    t = Tend(2026).fill(2, F.week_pairs(["blitz_rate"]))

    def rk(team: str) -> int:
        return rank(team, 2, "defense", "season", "blitz_rate", "all")

    taken: set[str] = set()

    def d(p: int) -> str:
        team = team_with_rank(p, 2, "defense", "season", "blitz_rate", "all", avoid=taken)
        taken.add(team)
        return team

    def safe() -> str:  # a defense that never shifts by 1 (rank 8..23)
        team = next(x for x in TEAMS if 8 <= rk(x) <= 23 and x not in taken)
        taken.add(team)
        return team

    roles = dict(
        zip(
            ("d31", "d25", "d24", "d30", "d0", "d1", "d29", "d2"),
            map(d, (31, 25, 24, 30, 0, 1, 29, 2)),
            strict=True,
        )
    )
    offs = [safe() for _ in range(6)]
    if small_defense:
        t.pin(roles["d30"], "defense", 2, "season", "blitz_rate", "all", n=19)
    t.pin(offs[4], "offense", 2, "season", "blitz_rate", "all", n=19)
    sun = dt.datetime(2026, 9, 20, 17, 0, tzinfo=dt.UTC)
    slate = [
        (2, offs[0], roles["d31"], sun),
        (2, offs[1], roles["d25"], sun + dt.timedelta(minutes=1)),
        (2, offs[2], roles["d24"], sun + dt.timedelta(minutes=2)),
        (2, offs[3], roles["d30"], sun + dt.timedelta(minutes=3)),
        (2, offs[4], roles["d0"], sun + dt.timedelta(minutes=4)),
        (2, offs[5], roles["d1"], sun + dt.timedelta(minutes=5)),
        (2, roles["d2"], roles["d29"], sun + dt.timedelta(minutes=6)),
    ]
    write_season(paths, t, build_doc(2026, 5))
    write_custom_games(paths, slate)
    body = get(make_client(tmp_path), "/api/weeks/2026/2/play-calls")
    return roles, offs, body


def blitz_row(body: dict, home: str, matchup: int = 0) -> dict:
    game = next(g for g in body["games"] if g["home"] == home)
    return next(r for r in game["matchups"][matchup]["rows"] if r["metric"] == "blitz_rate")


def test_shift_rules_on_a_hand_built_slate(tmp_path):
    """The league's 32 ranks, none small: a shift is (p - 15.5) / 9.3808 to two decimals.

    G1 D at rank 31 -> +1.65 (listed)         G5 offense small (n = 19) -> not listed
    G2 D at rank 25 -> +1.01 (listed, edge)   G6 D at rank 1  -> -1.55 (listed)
    G3 D at rank 24 -> +0.91 (under 1)        G7 home at 29 / away at 2 -> both offenses listed
    G4 D at rank 30 -> +1.55 (listed)
    """
    roles, offs, b = slate_world(tmp_path, small_defense=False)
    assert metrics_of(b) == ["blitz_rate"], "a season with only this metric has only this row"
    got = [(s["offense"], s["defense"], s["metric"], s["shift"]) for s in b["shifts"]]
    assert got == [
        (offs[0], roles["d31"], "blitz_rate", 1.65),
        (offs[3], roles["d30"], "blitz_rate", 1.55),
        (offs[5], roles["d1"], "blitz_rate", -1.55),
        (roles["d2"], roles["d29"], "blitz_rate", 1.44),  # G7: the away offense (rank 2) ...
        (roles["d29"], roles["d2"], "blitz_rate", -1.44),  # ... and the home offense
        (offs[1], roles["d25"], "blitz_rate", 1.01),
    ]  # fmt: skip
    # G3 is under 1: still a row of the matchup, not in the list; G5's offense is small
    assert blitz_row(b, roles["d24"])["shift"] == 0.91
    row5 = blitz_row(b, roles["d0"])
    assert row5["shift"] == -1.65 and row5["offense"]["small"] and not row5["defense"]["small"]
    assert not any(s["defense"] in (roles["d24"], roles["d0"]) for s in b["shifts"])


def test_a_defense_on_a_small_sample_is_left_out_of_the_spread(tmp_path):
    """Rank 30 on n = 19: it is not listed (small), its row keeps its shift, and the SD that
    scales every shift is the SD of the other 31 defenses (0.0915, not 0.0938): 1.69, not 1.65."""
    roles, offs, b = slate_world(tmp_path, small_defense=True)
    sd31 = statistics.stdev([0.01 * p for p in range(32) if p != 30])
    assert sd31 == pytest.approx(0.0914871, abs=1e-7)
    got = [(s["offense"], s["defense"], s["shift"]) for s in b["shifts"]]
    assert got == [
        (offs[0], roles["d31"], 1.69),
        (offs[5], roles["d1"], -1.58),
        (roles["d2"], roles["d29"], 1.48),
        (roles["d29"], roles["d2"], -1.48),
        (offs[1], roles["d25"], 1.04),
    ]  # fmt: skip
    for p, want in ((31, 1.69), (25, 1.04), (24, 0.93), (30, 1.58), (0, -1.69), (1, -1.58)):
        assert round(0.01 * (p - 15.5) / sd31, 2) == want
    row4 = blitz_row(b, roles["d30"])
    assert row4["shift"] == 1.58 and row4["defense"]["small"] is True and row4["defense"]["n"] == 19
    assert not any(s["defense"] == roles["d30"] for s in b["shifts"]), "small: not listed"
    assert blitz_row(b, roles["d24"])["shift"] == 0.93


def test_the_rank_phrase_counts_only_defenses_with_a_real_sample(tmp_path):
    """The highest blitz in the league is on n = 19: the next one down is then "the league's
    highest" among the defenses worth ranking, not "2nd highest"."""
    texts = {}
    for small in (False, True):
        paths = make_paths(tmp_path / str(small))
        t = Tend(2026).fill(2, F.week_pairs(["blitz_rate"]))
        top = team_with_rank(31, 2, "defense", "season", "blitz_rate", "all")
        second = team_with_rank(30, 2, "defense", "season", "blitz_rate", "all")
        offense = next(
            x for x in TEAMS if 8 <= rank(x, 2, "defense", "season", "blitz_rate", "all") <= 23
        )
        if small:
            t.pin(top, "defense", 2, "season", "blitz_rate", "all", n=19)
        write_season(paths, t, build_doc(2026, 5))
        sun = dt.datetime(2026, 9, 20, 17, 0, tzinfo=dt.UTC)
        write_custom_games(paths, [(2, offense, second, sun)])
        b = get(make_client(tmp_path / str(small)), "/api/weeks/2026/2/play-calls")
        (shift,) = [s for s in b["shifts"] if s["defense"] == second]
        texts[small] = shift["text"]
    assert "40.0% of dropbacks (2nd highest; league 25.5%)" in texts[False]
    assert "40.0% of dropbacks (the league's highest; league 25.5%)" in texts[True]


def test_a_matchup_without_a_spread_has_no_shifts(tmp_path):
    """Two teams only: the league's spread is not defined, so there is nothing to compare."""
    paths = make_paths(tmp_path)
    t = Tend(2026).fill(2, F.week_pairs(["blitz_rate"]), teams=["KC", "LV"])
    write_season(paths, t, build_doc(2026, 5))
    write_custom_games(paths, [(2, "KC", "LV", dt.datetime(2026, 9, 20, 17, 0, tzinfo=dt.UTC))])
    b = get(make_client(tmp_path), "/api/weeks/2026/2/play-calls")
    rows = b["games"][0]["matchups"][0]["rows"]
    blitz = next(r for r in rows if r["metric"] == "blitz_rate")
    assert blitz["defense"] is not None and blitz["offense"] is not None
    assert all(r["shift"] is None for r in rows) and b["shifts"] == []


def test_a_league_without_a_spread_has_no_shifts(tmp_path):
    """Every defense at the same value: the spread is 0, a shift would divide by it."""
    paths = make_paths(tmp_path)
    t = Tend(2026).fill(2, F.week_pairs(["blitz_rate"]))
    for team in TEAMS:
        t.pin(
            team, "defense", 2, "season", "blitz_rate", "all", value=0.3, league_value=0.3, diff=0.0
        )
    write_season(paths, t, build_doc(2026, 5))
    write_games(paths)
    b = get(make_client(tmp_path), "/api/weeks/2026/2/play-calls")
    rows = [
        r
        for g in b["games"]
        for mu in g["matchups"]
        for r in mu["rows"]
        if r["metric"] == "blitz_rate"
    ]
    assert rows and all(r["shift"] is None and r["defense"]["value"] == 0.3 for r in rows)
    assert not [s for s in b["shifts"] if s["metric"] == "blitz_rate"]


def test_ftn_waiting_is_capped_and_the_page_note_names_six(tmp_path):
    paths = make_paths(tmp_path)
    t = Tend(2026).fill(5, F.page_pairs(["proe"]), teams=["KC"])
    c = make_client(tmp_path)
    few = ("2026_04_ATL_NO", "2026_03_KC_LV", "2026_03_BAL_BUF")
    write_season(paths, t, build_doc(2026, 5, waiting=few))
    assert get(c, "/api/playcalling/teams?season=2026")["ftn_waiting"] == [
        "ATL at NO, week 4",
        "KC at LV, week 3",
        "BAL at BUF, week 3",
    ]
    many = tuple(f"2026_04_{TEAMS[i % 16]}_{TEAMS[16 + i % 16]}" for i in range(60))
    write_season(paths, t, build_doc(2026, 5, waiting=many))
    assert len(get(c, "/api/playcalling/teams?season=2026")["ftn_waiting"]) == 40
    note = get(c, "/api/playcalling/teams/KC?season=2026")["notes"][0]
    assert note.count(" at ") == 6, note


def test_a_games_table_without_season_and_week_reads_as_no_schedule(tmp_path):
    paths = make_paths(tmp_path)
    t = Tend(2026).fill(5, F.page_pairs(["proe"]), teams=["KC"])
    write_season(paths, t, build_doc(2026, 5))
    pl.DataFrame({"game_id": ["2026_05_KC_LV"]}).write_parquet(paths.curated / "games.parquet")
    c = make_client(tmp_path)
    assert c.get("/api/playcalling/teams/KC?season=2026").status_code == 200
    assert c.get("/api/weeks/2026/5/play-calls").status_code == 200


# ---- what a page leaves out when the season lacks it, and what the clock decides -----------------


def test_next_game_skips_a_game_that_has_already_kicked_off(tmp_path):
    """Thursday's game, played since the tables were built, is not the "next" game."""
    make_world(make_paths(tmp_path))
    thursday = dt.datetime(2026, 10, 9, 0, 15, tzinfo=dt.UTC)  # ARI at ATL
    before = make_client(tmp_path, now=thursday - dt.timedelta(seconds=1))
    after = make_client(tmp_path, now=thursday + dt.timedelta(seconds=1))
    atl = get(before, "/api/playcalling/teams/ATL?season=2026")["next_game"]
    assert (atl["game_id"], atl["home"], atl["kickoff"]) == (
        "2026_05_ARI_ATL", True, "2026-10-09T00:15:00Z",
    )  # fmt: skip
    assert get(after, "/api/playcalling/teams/ATL?season=2026")["next_game"] is None
    assert get(after, "/api/playcalling/teams/ARI?season=2026")["next_game"] is None
    # Sunday's games, and a game with no kickoff time yet, are still ahead
    assert get(after, "/api/playcalling/teams/LA?season=2026")["next_game"]["week"] == 5
    assert get(after, "/api/playcalling/teams/TEN?season=2026")["next_game"]["kickoff"] is None
    # a team on a bye looks past the played week to week 6 either way
    assert get(after, "/api/playcalling/teams/KC?season=2026")["next_game"]["week"] == 6


def test_weekly_shows_only_the_games_before_the_as_of_week(tmp_path):
    """A Thursday game curated since the build waits for the next as-of week: the line stops
    where the header's "weeks 1-4 played" does."""
    t = Tend(2026).fill(5, F.page_pairs(["proe"]), teams=["LA"])
    tg = TeamGames(2026)
    for week, gid, opp in ((3, "2026_03_LA_SEA", "SEA"), (4, "2026_04_DEN_LA", "DEN"),
                           (5, "2026_05_LA_LAC", "LAC")):  # fmt: skip
        tg.add(week, gid, "LA", opp, "offense", "proe", "neutral", 40, 0.01 * week)
    paths = make_paths(tmp_path)
    write_season(paths, t, build_doc(2026, 5), tg)
    b = get(make_client(tmp_path), "/api/playcalling/teams/LA?season=2026")
    assert [g["week"] for g in b["weekly"]["games"]] == [3, 4]
    assert [p["week"] for p in b["weekly"]["series"][0]["points"]] == [3, 4]


def test_situation_columns_are_only_the_metrics_the_season_has(base):
    s = team_page(base, season=2019)["situations"]
    assert ids(s["metrics"]) == ["dropback_rate", "proe", "shotgun_rate", "deep_shot_rate"]
    assert set(s["baseline"]["cells"]) == set(ids(s["metrics"]))
    assert all(set(r["cells"]) == set(ids(s["metrics"])) for f in s["families"] for r in f["rows"])
    full = team_page(base)["situations"]
    assert len(full["metrics"]) == 6 and "blitz_rate" in ids(full["metrics"])


def test_matchup_rows_are_only_the_metrics_the_season_has(tmp_path):
    pbp = [m for m in F.MATCHUP_METRICS if F.meta(m)[1] == "pbp"]
    assert 2 <= len(pbp) < len(F.MATCHUP_METRICS)
    paths = make_paths(tmp_path)
    write_season(paths, Tend(2019).fill(3, F.week_pairs(pbp)), build_doc(2019, 22))
    write_custom_games(
        paths, [(3, "KC", "LV", dt.datetime(2019, 9, 22, 17, 0, tzinfo=dt.UTC))], season=2019
    )
    b = get(make_client(tmp_path), "/api/weeks/2019/3/play-calls")
    assert metrics_of(b) == pbp and b["status"] == "ok"
    assert all(ids(mu["rows"]) == pbp for g in b["games"] for mu in g["matchups"])


def test_a_week_without_its_as_of_rows_has_the_schedule_and_no_rows(base):
    b = week_tab(base, 4)  # the fixture has as-of rows for weeks 1, 3 and 5 only
    assert b["status"] == "ok" and b["as_of_week"] == 4
    assert ids(b["games"], "game_id") == ["2026_04_KC_DEN", "2026_04_ATL_NO"]
    assert b["metrics"] == [] and b["shifts"] == []
    assert all(mu["rows"] == [] for g in b["games"] for mu in g["matchups"])


def test_the_week_tab_waits_only_for_ftn_games_it_counts(base):
    assert week_tab(base, 5)["ftn_waiting"] == ["ATL at NO, week 4"]
    for week in (1, 3, 4):  # week 4's game is not before week 4
        assert week_tab(base, week)["ftn_waiting"] == [], week
    assert team_page(base)["ftn_waiting"] == ["ATL at NO, week 4"]


def test_without_any_blitz_data_the_defense_signature_is_the_proe_it_faced(tmp_path):
    """2016-2017: no FTN and no PFR blitz counts."""
    pbp = [m for m in F.PBP_ONLY_METRICS]
    t = Tend(2017).fill(22, F.page_pairs(pbp))
    b = get(solo(tmp_path, [(t, build_doc(2017, 22))]), "/api/playcalling/teams?season=2017")
    got = ids(b["columns"], "id")
    assert not {"defense.blitz_rate", "defense.blitz_rate_pfr"} & set(got)
    assert [c["id"] for c in b["columns"] if c["signature"]] == ["offense.proe", "defense.proe"]
    kc = next(tm for tm in b["teams"] if tm["team"] == "KC")
    assert kc["cells"]["defense.proe"] == cell_at(t, "KC", "defense", 22, "season", "proe")


def test_history_needs_a_real_true_in_build_json(tmp_path):
    """The build writes a boolean; any other value is not a participation season."""
    paths = make_paths(tmp_path)
    make_world(paths)
    doc_path = paths.playcalling / "2025" / "build.json"
    doc = json.loads(doc_path.read_text("utf-8"))
    for odd in ("true", 1, "yes"):
        doc_path.write_text(json.dumps({**doc, "history_metrics": odd}), encoding="utf-8")
        got = get(make_client(tmp_path), "/api/playcalling/teams/KC/history")["seasons"]
        assert got == [2023, 2024], odd


# ---- safety: nothing written, no NaN, no leak, the data drive ----------------------------------


def all_urls() -> list[str]:
    urls = [
        "/api/playcalling/teams",
        "/api/playcalling/teams?season=2026",
        "/api/playcalling/teams?season=2025",
        "/api/playcalling/teams?season=2019",
        "/api/playcalling/teams?season=2015",
        "/api/playcalling/teams?season=1998",
        "/api/playcalling/teams/KC/history",
        "/api/playcalling/teams/MIN/history?side=defense",
        "/api/playcalling/teams/XYZ",
        "/api/playcalling/teams/kc",
        "/api/playcalling/teams/KC?side=x",
    ]
    for team in sorted(CANONICAL_TEAMS):
        urls += [f"/api/playcalling/teams/{team}", f"/api/playcalling/teams/{team}?side=defense"]
    urls += [f"/api/weeks/2026/{w}/play-calls" for w in range(1, 8)]
    urls += [
        "/api/weeks/2026/22/play-calls",
        "/api/weeks/2015/5/play-calls",
        "/api/weeks/2026/0/play-calls",
    ]
    return urls


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        if p.is_file()
        else "dir"
        for p in root.rglob("*")
    }


def test_no_endpoint_writes_anything_under_the_data_root(base):
    before = tree(base.root)
    for url in all_urls():
        base.client.get(url)
    assert tree(base.root) == before


def test_every_read_is_short_filtered_and_never_memory_mapped(base, monkeypatch):
    """D100: no memory map on a file the Tuesday build replaces; and only the two tendency tables
    (and the curated schedule) are read: never `plays_enriched`."""
    import polars as pl_mod
    import pyarrow.parquet as pq_mod

    table_reads: list[tuple[str, dict]] = []
    frame_reads: list[tuple[str, dict]] = []
    real_table, real_frame = pq_mod.read_table, pl_mod.read_parquet

    def table_spy(path, *args, **kw):
        table_reads.append((Path(str(path)).name, kw))
        return real_table(path, *args, **kw)

    def frame_spy(source, *args, **kw):
        frame_reads.append((Path(str(source)).name, kw))
        return real_frame(source, *args, **kw)

    monkeypatch.setattr(pq_mod, "read_table", table_spy)
    monkeypatch.setattr(pl_mod, "read_parquet", frame_spy)
    for url in (
        "/api/playcalling/teams",
        "/api/playcalling/teams/KC",
        "/api/playcalling/teams/MIN?side=defense",
        "/api/playcalling/teams/KC/history",
        "/api/weeks/2026/5/play-calls",
    ):
        get(base.client, url)
    assert table_reads and frame_reads
    assert {n for n, _ in table_reads} == {
        "team_tendencies.parquet",
        "team_game_tendencies.parquet",
    }
    assert all(kw.get("memory_map") is False for _, kw in table_reads + frame_reads), table_reads
    assert all(kw.get("filters") for _, kw in table_reads), "a whole table is never read"
    assert {n for n, _ in frame_reads} <= {"games.parquet"}


def test_no_answer_carries_a_nan_or_an_infinity(base):
    def boom(constant: str):
        raise AssertionError(f"{constant} in a JSON answer")

    for url in all_urls():
        text = base.client.get(url).text
        json.loads(text, parse_constant=boom)
        assert not re.search(r"\b(NaN|-?Infinity)\b", text), url


def test_a_build_can_replace_the_tables_while_the_app_is_open(tmp_path):
    """D100: the Tuesday build replaces files with a rename; on Windows that fails if the app
    still has a handle or a memory map on the file."""
    paths = make_paths(tmp_path)
    world = make_world(paths)
    c = make_client(tmp_path)
    for url in (
        "/api/playcalling/teams",
        "/api/playcalling/teams/KC",
        "/api/weeks/2026/5/play-calls",
        "/api/playcalling/teams/KC/history",
    ):
        get(c, url)
    d = paths.playcalling / "2026"
    for name, frame in (
        ("team_tendencies.parquet", world.tend[2026].frame()),
        ("team_game_tendencies.parquet", world.tg[2026].frame()),
    ):
        tmp = d / (name + ".tmp")
        frame.write_parquet(tmp)
        tmp.replace(d / name)
    for name in ("build.json",):
        tmp = d / (name + ".tmp")
        tmp.write_text((d / name).read_text("utf-8"), encoding="utf-8")
        tmp.replace(d / name)
    games = paths.curated / "games.parquet"
    tmp = games.with_name("games.parquet.tmp")
    tmp.write_bytes(games.read_bytes())
    tmp.replace(games)
    assert get(c, "/api/playcalling/teams/KC")["status"] == "ok"


def test_no_secret_or_path_shaped_text_reaches_the_browser(tmp_path, monkeypatch):
    sentinels = {
        "WANDB_API_KEY": "wandb_v1_SENTINELwandbSENTINELwandb",
        "NEO4J_PASSWORD": "SENTINEL-neo4j-pw-123",
        "OPENROUTER_API_KEY": "sk-or-v1-SENTINELSENTINELSENTINEL",
    }
    for k, v in sentinels.items():
        monkeypatch.setenv(k, v)
    S.get_env.cache_clear()
    try:
        paths = make_paths(tmp_path)
        leak = "api_key=SENTINEL-leaky-123 tail"  # a masked value needs a word after it
        root_id = f"{tmp_path.as_posix()}/leak"
        world = make_world(
            paths,
            waiting=("2026_04_ATL_NO", leak, root_id),
            built_at=leak,
            extra_games=[
                {
                    "game_id": leak,
                    "season": 2026,
                    "week": 5,
                    "away_team": "HOU",
                    "home_team": "IND",
                    "kickoff_utc": dt.datetime(2026, 10, 11, 17, 0, tzinfo=dt.UTC),
                }
            ],
        )
        # a game id straight from the table, carrying the root: shown, so cleaned and made relative
        world.tg[2026].add(
            2, f"{tmp_path.as_posix()}/gid-leak", "KC", "BAL", "offense",
            "motion_rate", "all", 60, 0.4,
        )  # fmt: skip
        world.tg[2026].frame().write_parquet(
            paths.playcalling / "2026" / "team_game_tendencies.parquet"
        )
        c = make_client(tmp_path)
        kc = c.get("/api/playcalling/teams/KC?season=2026")
        assert "gid-leak" in kc.text, "the game id is shown, relative to the data root"
        assert {g["opponent"] for g in kc.json()["weekly"]["games"]} == {"LV", "BAL", "DEN"}
        seen_mask = False
        for url in all_urls():
            body = c.get(url).text
            for v in sentinels.values():
                assert v not in body, url
            for s in (
                "SENTINEL-leaky-123",
                "token=abc",
                str(tmp_path),
                str(tmp_path).replace("\\", "\\\\"),
                tmp_path.as_posix(),
            ):
                assert s not in body, (url, s)
            assert scrub(body, limit=10**9) == body, f"{url} has text that looks like a secret"
            seen_mask |= "api_key=***" in body
        assert seen_mask, "the planted text was masked, not dropped"
    finally:
        S.get_env.cache_clear()


def test_a_missing_data_drive_is_the_standard_503(tmp_path):
    def gone():
        raise DataRootError("Data drive D:\\ is not connected (NFL_DATA_ROOT=D:/nfl-ml-data).")

    c = make_client(tmp_path, data_root=gone)
    for url in (
        "/api/playcalling/teams",
        "/api/playcalling/teams/KC",
        "/api/playcalling/teams/KC/history",
        "/api/weeks/2026/5/play-calls",
    ):
        r = c.get(url)
        assert (r.status_code, r.json()["error"]["code"]) == (503, "data_root_missing"), url
        assert "nfl-ml-data" not in r.text, (
            "the drive's own message (and path) never reaches the browser"
        )


def test_the_existing_endpoints_answer_the_same_with_the_play_calling_tables_present(tmp_path):
    """PC01 adds routes and a reader; none of the CR endpoints may change (exit criterion)."""
    plain, rich = tmp_path / "plain", tmp_path / "rich"
    for root in (plain, rich):
        paths = make_paths(root)
        write_curated(paths)
        write_full_week(paths, 2026, 5, report_card_for=4)
    make_world(make_paths(rich), games=False, teams_table=False)  # the schedule stays CR's
    urls = ["/api/meta", "/api/weeks", "/api/team-info", "/api/teams", "/api/preflight"]
    for w in (4, 5):
        urls.append(f"/api/weeks/2026/{w}")
        urls += [
            f"/api/weeks/2026/{w}/{t}"
            for t in ("pipeline", "digest", "games", "players", "results", "graph")
        ]
    a, b = make_client(plain), make_client(rich)
    for url in urls:
        ra, rb = a.get(url), b.get(url)
        assert ra.status_code == rb.status_code == 200, url
        assert ra.json() == rb.json(), url  # parsed: one existing reader orders a dict at random
    # a typo is still a JSON 404 from the catch-all, and the real tabs still answer
    assert b.get("/api/weeks/2026/5/nonsense").status_code == 404


# ---- Sol review (PC01): file text, no kickoff column, old team codes, a raw shift ----------

PLANT = "api_key=abc123 x"  # credential-shaped; a word after the value keeps scrub idempotent


def write_raw_games(paths, rows: list[dict[str, Any]], kickoff: bool = True) -> None:
    """A curated games table of your own: game ids and team columns exactly as given."""
    schema = dict(F.GAME_SCHEMA)
    if not kickoff:
        del schema["kickoff_utc"]
        rows = [{k: v for k, v in r.items() if k != "kickoff_utc"} for r in rows]
    pl.DataFrame(rows, schema=schema).write_parquet(paths.curated / "games.parquet")


def raw_game(gid, season, week, away, home, kick=None) -> dict[str, Any]:
    row = {"game_id": gid, "season": season, "week": week, "away_team": away, "home_team": home}
    return {**row, "kickoff_utc": kick}


def is_label(text: str) -> re.Match | None:
    return re.fullmatch(r"([A-Z]{2,3}) at ([A-Z]{2,3}), week \d+", text)


def test_sol_s1_ftn_waiting_labels_are_two_known_teams_or_absent(tmp_path):
    """A label is `<away> at <home>, week N` from known team codes (an old code mapped); an id
    that is not a game between two known teams is left out, never cleaned and shown."""
    paths = make_paths(tmp_path)
    t = Tend(2026).fill(5, F.page_pairs(["proe"]), teams=["KC"])
    waiting = (
        "2026_04_ATL_NO",
        "2019_03_KC_OAK",  # an old franchise code: today's
        "2026_04_token=abc123 x_NO",  # four parts, a credential where the away team goes
        "2026_04_XYZ_NO",  # well-formed, not a team
        PLANT,  # not a game id at all
        "2026_04_ATL",  # too few parts
    )
    write_season(paths, t, build_doc(2026, 5, waiting=waiting))
    c = make_client(tmp_path)
    for url in (
        "/api/playcalling/teams?season=2026",
        "/api/playcalling/teams/KC?season=2026",
        "/api/weeks/2026/5/play-calls",
    ):
        r = c.get(url)
        assert r.status_code == 200, url
        assert "abc123" not in r.text and "XYZ" not in r.text, url
        assert r.json()["ftn_waiting"] == ["ATL at NO, week 4", "KC at LV, week 3"], url


def test_sol_s1_weekly_games_with_an_unknown_opponent_are_left_out(tmp_path):
    """Dropped games leave no gap in any line; two kept games whose ids clean to the same text
    still get their own values (the points are looked up by the raw id, not the shown one)."""
    paths = make_paths(tmp_path)
    t = Tend(2026).fill(5, F.page_pairs(["proe"]), teams=["KC"])
    tg = TeamGames(2026)
    for week, gid, opp, value in (
        (1, "2026_01_KC_LV", "LV", 0.01),
        (2, "2026_02_BAL_KC", PLANT, 0.5),  # a credential as the opponent: dropped
        (2, "token=abc123 x", "BAL", 0.02),  # a credential as the id: kept, id masked
        (3, "2026_03_KC_DEN", "XYZ", 0.5),  # well-formed, not a team: dropped
        (3, "token=zzz999 x", "DEN", 0.03),  # masks to the same text as the one above
        (4, "2026_04_KC_SF", "OAK", 0.04),  # an old franchise code: today's LV
    ):
        tg.add(week, gid, "KC", opp, "offense", "proe", "neutral", 40, value)
    write_season(paths, t, build_doc(2026, 5), tg)
    r = make_client(tmp_path).get("/api/playcalling/teams/KC?season=2026")
    assert r.status_code == 200
    for needle in ("abc123", "zzz999", "XYZ", "api_key"):
        assert needle not in r.text, needle
    weekly = r.json()["weekly"]
    assert weekly["games"] == [
        {"week": 1, "game_id": "2026_01_KC_LV", "opponent": "LV", "home": False},
        {"week": 2, "game_id": "token=*** x", "opponent": "BAL", "home": False},
        {"week": 3, "game_id": "token=*** x", "opponent": "DEN", "home": False},
        {"week": 4, "game_id": "2026_04_KC_SF", "opponent": "LV", "home": False},
    ]
    for s in weekly["series"]:
        assert s["points"] == [
            {"week": 1, "value": 0.01, "n": 40},
            {"week": 2, "value": 0.02, "n": 40},
            {"week": 3, "value": 0.03, "n": 40},
            {"week": 4, "value": 0.04, "n": 40},
        ]


def test_sol_s1_next_game_skips_games_between_unknown_teams(tmp_path):
    paths = make_paths(tmp_path)
    t = Tend(2026).fill(5, F.page_pairs(["proe"]), teams=["KC", "LA", "SF", "SEA"])
    write_season(paths, t, build_doc(2026, 5))
    kick = dt.datetime(2026, 10, 18, 17, 0, tzinfo=dt.UTC)
    write_raw_games(
        paths,
        [
            raw_game("2026_05_KC_LV", 2026, 5, "KC", PLANT),  # a credential as the home team
            raw_game("2026_06_KC_WAS", 2026, 6, "KC", "WAS", kick),
            raw_game("token=abc123 x", 2026, 5, "LA", "LAC", kick),  # a credential as the game id
            raw_game("2026_05_SF_ZZZ", 2026, 5, "SF", "ZZZ", kick),  # well-formed, not a team
            raw_game("2019_05_OAK_SEA", 2026, 5, "OAK", "SEA", kick),  # an old franchise code
        ],
    )
    c = make_client(tmp_path)

    def page(team: str):
        r = c.get(f"/api/playcalling/teams/{team}?season=2026")
        assert r.status_code == 200, team
        assert "abc123" not in r.text and "ZZZ" not in r.text, team
        return r.json()["next_game"]

    kc = page("KC")  # the week-5 game against a "team" that isn't one is skipped: the next valid
    assert (kc["week"], kc["opponent"], kc["home"]) == (6, "WAS", False)
    assert page("SF") is None, "its only game is against an unknown team"
    sea = page("SEA")
    assert (sea["week"], sea["opponent"], sea["home"]) == (5, "LV", True), "OAK is today's LV"
    la = page("LA")
    assert (la["opponent"], la["game_id"]) == ("LAC", "token=*** x"), (
        "a clean game keeps its masked id"
    )
    week = c.get("/api/weeks/2026/5/play-calls")
    assert week.status_code == 200 and "abc123" not in week.text
    # the week tab lists only games between two canonical codes, as the schedule has them
    assert [g["game_id"] for g in week.json()["games"]] == ["token=*** x"]


def test_sol_s2_a_games_table_without_kickoff_times_answers_with_null_kickoffs(tmp_path):
    paths = make_paths(tmp_path)
    make_world(paths, games=False)
    write_raw_games(
        paths,
        [
            raw_game("2026_05_HOU_IND", 2026, 5, "HOU", "IND"),
            raw_game("2026_05_ARI_ATL", 2026, 5, "ARI", "ATL"),
            raw_game("2026_06_KC_WAS", 2026, 6, "KC", "WAS"),
        ],
        kickoff=False,
    )
    c = make_client(tmp_path)
    r = c.get("/api/weeks/2026/5/play-calls")
    assert r.status_code == 200, r.text[:300]
    games = r.json()["games"]
    assert [g["game_id"] for g in games] == ["2026_05_ARI_ATL", "2026_05_HOU_IND"]  # by game id
    assert all(g["kickoff"] is None for g in games)
    assert all(len(g["matchups"]) == 2 for g in games)
    page = c.get("/api/playcalling/teams/ATL?season=2026")
    assert page.status_code == 200
    ng = page.json()["next_game"]
    assert (ng["game_id"], ng["home"], ng["kickoff"]) == ("2026_05_ARI_ATL", True, None)
    assert c.get("/api/playcalling/teams/KC?season=2026").json()["next_game"]["week"] == 6


@pytest.mark.parametrize(
    ("old", "new", "season"),
    [("OAK", "LV", 2019), ("SD", "LAC", 2016), ("STL", "LA", 2015)],
)
def test_sol_s3_a_relocated_franchises_home_games_are_home(tmp_path, old, new, season):
    """The game id keeps the franchise's old code; the team columns and tables use today's."""
    gid = f"{season}_03_KC_{old}"
    paths = make_paths(tmp_path)
    t = Tend(season).fill(22, F.page_pairs(["proe"]), teams=["KC", new])
    tg = TeamGames(season)
    tg.add(3, gid, new, "KC", "offense", "proe", "neutral", 40, 0.02)
    tg.add(3, gid, "KC", new, "offense", "proe", "neutral", 41, 0.03)
    write_season(paths, t, build_doc(season, 22), tg)
    write_raw_games(paths, [raw_game(gid, season, 3, "KC", new)])
    c = make_client(tmp_path)
    home = c.get(f"/api/playcalling/teams/{new}?season={season}").json()["weekly"]["games"]
    away = c.get(f"/api/playcalling/teams/KC?season={season}").json()["weekly"]["games"]
    assert home == [{"week": 3, "game_id": gid, "opponent": "KC", "home": True}]
    assert away == [{"week": 3, "game_id": gid, "opponent": new, "home": False}]


def test_sol_s7_the_shift_filter_and_ranking_use_the_unrounded_ratio(tmp_path):
    """Raw ratios 0.996 / 1.004 both round to 1.0 and 1.296 / 1.304 both to 1.3: the first is
    under 1 and stays out, and of the second pair the larger raw ratio ranks first even though
    its game is later."""
    paths = make_paths(tmp_path)
    t = Tend(2026).fill(2, F.week_pairs(["blitz_rate"]))
    spread = statistics.stdev(
        r6(t.get(x, "defense", 2, "season", "blitz_rate", "all")["value"]) for x in TEAMS
    )

    def safe(avoid: set[str]) -> str:  # a defense that never shifts by 1 (rank 8..23)
        team = next(
            x
            for x in TEAMS
            if 8 <= rank(x, 2, "defense", "season", "blitz_rate", "all") <= 23 and x not in avoid
        )
        avoid.add(team)
        return team

    used: set[str] = set()
    roles = {name: (safe(used), safe(used)) for name in ("a", "b", "c", "d")}  # (offense, defense)
    for name, raw in (("a", 1.296), ("b", 1.304), ("c", 0.996), ("d", 1.004)):
        t.pin(roles[name][1], "defense", 2, "season", "blitz_rate", "all", diff=raw * spread)
    sun = dt.datetime(2026, 9, 20, 17, 0, tzinfo=dt.UTC)
    games = [  # a, b, c, d in kickoff order
        (2, *roles[n], sun + dt.timedelta(minutes=k)) for k, n in enumerate(("a", "b", "c", "d"))
    ]
    write_season(paths, t, build_doc(2026, 5))
    write_custom_games(paths, games)
    b = get(make_client(tmp_path), "/api/weeks/2026/2/play-calls")
    got = [(s["offense"], s["defense"], s["shift"]) for s in b["shifts"]]
    assert got == [
        (*roles["b"], 1.3),  # raw 1.304, a later game, ranks above ...
        (*roles["a"], 1.3),  # ... raw 1.296
        (*roles["d"], 1.0),  # raw 1.004 is at least 1: listed, shown as 1.0
    ]
    assert not any(s["defense"] == roles["c"][1] for s in b["shifts"]), "raw 0.996 is under 1"
    row_c = blitz_row(b, roles["c"][1])
    assert row_c["defense"]["small"] is False and row_c["shift"] in (0.996, 1.0)


# ---- the contract with web/src/api/types.ts ----------------------------------------------------

TYPES_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "api" / "types.ts"
TOKEN = re.compile(r"\s*('[^']*'|[A-Za-z_]\w*|\[\]|\d+|[{}<>|;,:()?=])")
PRIMITIVES = {"string", "number", "boolean", "null", "true", "false"}


def pc01_source() -> str:
    """The PC01 block of types.ts without its comments (to the next phase's block, if any)."""
    text = TYPES_TS.read_text(encoding="utf-8")
    start = text.index("// ---- PC01: Play calling")
    nxt = re.search(r"^// ---- PC0[2-9]", text[start + 10 :], re.M)
    block = text[start : start + 10 + nxt.start()] if nxt else text[start:]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    return re.sub(r"//[^\n]*", "", block)


class TypeParser:
    """The small TypeScript the types file uses: unions, `T[]`, `Record<K, V>`, inline objects,
    string literals, named types, interfaces with `extends`."""

    def __init__(self, src: str):
        self.toks = TOKEN.findall(src)
        self.i = 0

    def peek(self) -> str | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self, want: str | None = None) -> str:
        tok = self.peek()
        assert tok is not None and (want is None or tok == want), f"expected {want!r}, got {tok!r}"
        self.i += 1
        return tok

    def union(self):
        if self.peek() == "|":
            self.take()
        parts = [self.postfix()]
        while self.peek() == "|":
            self.take()
            parts.append(self.postfix())
        return parts[0] if len(parts) == 1 else ("union", parts)

    def postfix(self):
        node = self.primary()
        while self.peek() == "[]":
            self.take()
            node = ("arr", node)
        return node

    def primary(self):
        tok = self.take()
        if tok.startswith("'"):
            return ("lit", tok[1:-1])
        if tok == "{":
            return self.fields()
        if tok == "(":
            node = self.union()
            self.take(")")
            return node
        if tok == "Record":
            self.take("<")
            key = self.union()
            self.take(",")
            value = self.union()
            self.take(">")
            return ("rec", key, value)
        return ("name", tok)

    def fields(self):
        out: dict[str, tuple[Any, bool]] = {}
        while self.peek() != "}":
            name = self.take()
            optional = False
            if self.peek() == "?":
                self.take()
                optional = True
            self.take(":")
            out[name] = (self.union(), optional)
            if self.peek() in (";", ","):
                self.take()
        self.take("}")
        return ("obj", out)


class Contract:
    def __init__(self, src: str):
        self.types: dict[str, Any] = {}
        self.ifaces: dict[str, tuple[list[str], dict[str, tuple[Any, bool]]]] = {}
        p = TypeParser(src)
        while p.peek() is not None:
            p.take("export")
            kind, name = p.take(), p.take()
            if kind == "type":
                p.take("=")
                self.types[name] = p.union()
                p.take(";")
            else:
                assert kind == "interface", kind
                parents = []
                if p.peek() == "extends":
                    p.take()
                    parents.append(p.take())
                p.take("{")
                self.ifaces[name] = (parents, p.fields()[1])

    def fields_of(self, name: str) -> dict[str, tuple[Any, bool]]:
        parents, own = self.ifaces[name]
        merged: dict[str, tuple[Any, bool]] = {}
        for parent in parents:
            merged |= self.fields_of(parent)
        return merged | own

    def literals(self, node) -> set[str] | None:
        """The string values of a union of literals (or an alias for one), else None."""
        if node[0] == "lit":
            return {node[1]}
        if node[0] == "name" and node[1] in self.types:
            return self.literals(self.types[node[1]])
        if node[0] == "union":
            subs = [self.literals(m) for m in node[1]]
            return None if any(s is None for s in subs) else set().union(*subs)
        return None

    def check(self, value, node, where: str, problems: list[str], seen: dict) -> None:
        kind = node[0]
        if kind == "union":
            members = node[1]
            if value is None:
                if ("name", "null") not in members:
                    problems.append(f"{where}: null where the type does not allow it")
                return
            attempts: list[list[str]] = []
            for m in members:
                if m == ("name", "null"):
                    continue
                sub: list[str] = []
                tmp: dict = defaultdict(set)
                self.check(value, m, where, sub, tmp)
                if not sub:
                    for k, v in tmp.items():
                        seen[k] |= v
                    return
                attempts.append(sub)
            problems.extend(
                attempts[0]
                if len(attempts) == 1
                else [f"{where}: {value!r} fits none of {members}"]
            )
        elif kind == "lit":
            if value != node[1]:
                problems.append(f"{where}: {value!r} is not '{node[1]}'")
        elif kind == "arr":
            if not isinstance(value, list):
                problems.append(f"{where}: expected a list, got {type(value).__name__}")
                return
            for i, item in enumerate(value):
                self.check(item, node[1], f"{where}[{i}]", problems, seen)
        elif kind == "rec":
            if not isinstance(value, dict):
                problems.append(f"{where}: expected an object, got {type(value).__name__}")
                return
            keys = self.literals(node[1])
            if keys is not None and set(value) != keys:
                problems.append(f"{where}: keys {sorted(value)} != {sorted(keys)}")
            for k, v in value.items():
                self.check(v, node[2], f"{where}.{k}", problems, seen)
        elif kind == "obj":
            self.check_fields(value, node[1], "(inline)", where, problems, seen)
        else:  # a name
            name = node[1]
            if name in PRIMITIVES:
                ok = {
                    "string": isinstance(value, str),
                    "number": isinstance(value, int | float) and not isinstance(value, bool),
                    "boolean": isinstance(value, bool),
                    "null": value is None,
                    "true": value is True,
                    "false": value is False,
                }[name]
                if not ok:
                    problems.append(f"{where}: {value!r} is not a {name}")
            elif name in self.types:
                self.check(value, self.types[name], where, problems, seen)
            elif name in self.ifaces:
                self.check_fields(value, self.fields_of(name), name, where, problems, seen)
            else:
                problems.append(f"{where}: unknown type {name}")

    def check_fields(
        self, value, fields, owner: str, where: str, problems: list[str], seen: dict
    ) -> None:
        if not isinstance(value, dict):
            problems.append(f"{where}: expected an object for {owner}, got {type(value).__name__}")
            return
        for k in sorted(set(value) - set(fields)):
            problems.append(f"{where}: field {k!r} is not in {owner}")
        for k, (ty, optional) in fields.items():
            if k not in value:
                if not optional:
                    problems.append(f"{where}: {owner}.{k} is missing")
                continue
            seen[(owner, k)].add("null" if value[k] is None else "value")
            self.check(value[k], ty, f"{where}.{k}", problems, seen)


@pytest.fixture(scope="module")
def contract() -> Contract:
    return Contract(pc01_source())


def check_answer(contract: Contract, body: Any, iface: str, where: str, seen: dict) -> list[str]:
    problems: list[str] = []
    contract.check(body, ("name", iface), where, problems, seen)
    return problems


def test_the_ts_parser_sees_the_play_calling_types(contract):
    assert {
        "PlaycallTeamsResponse",
        "PlaycallTeamResponse",
        "PlaycallHistoryResponse",
        "PlayCallsResponse",
        "PlaycallCell",
        "PlaycallMeta",
        "PlaycallMetric",
    } <= set(contract.ifaces)
    assert contract.literals(contract.types["PlaycallStatus"]) == {"ok", "not_built", "not_yet"}
    assert contract.literals(contract.types["PlaycallWindow"]) == set(WINDOWS)
    fields = contract.fields_of("PlaycallTeamsResponse")
    assert {"status", "message", "season", "seasons", "min_n", "window", "columns", "teams"} <= set(
        fields
    )
    assert "points" in contract.fields_of(
        "PlaycallWeeklySeries"
    ) and "league" in contract.fields_of("PlaycallWeeklySeries")
    assert contract.fields_of("PlaycallCell")["pct"][0] == (
        "union",
        [("name", "number"), ("name", "null")],
    )
    assert contract.fields_of("PlaycallHistoryResponse")["research"][0] == ("name", "true")


def contract_answers(base: Base, tmp_path: Path) -> list[tuple[str, str, Any]]:
    c = base.client
    out: list[tuple[str, str, Any]] = []

    def add(iface: str, url: str, client: TestClient = c):
        out.append((iface, url, get(client, url)))

    for season in (2026, 2025, 2019, 2015):
        add("PlaycallTeamsResponse", f"/api/playcalling/teams?season={season}")
    for team, side, season in (
        ("KC", "offense", 2026),
        ("MIN", "defense", 2026),
        ("LA", "offense", 2026),
        ("KC", "offense", 2019),
        ("KC", "defense", 2025),
        ("KC", "offense", 2015),
    ):
        add("PlaycallTeamResponse", f"/api/playcalling/teams/{team}?season={season}&side={side}")
    for team, side in (("KC", "offense"), ("MIN", "defense"), ("CHI", "offense")):
        add("PlaycallHistoryResponse", f"/api/playcalling/teams/{team}/history?side={side}")
    for week in (1, 3, 5, 6):
        add("PlayCallsResponse", f"/api/weeks/2026/{week}/play-calls")
    add("PlayCallsResponse", "/api/weeks/2015/5/play-calls")
    # the empty roots: nothing built; a build without History; a first week
    empty = make_client(tmp_path / "empty")
    add("PlaycallTeamsResponse", "/api/playcalling/teams", empty)
    add("PlaycallTeamResponse", "/api/playcalling/teams/KC", empty)
    add("PlaycallHistoryResponse", "/api/playcalling/teams/KC/history", empty)
    add("PlayCallsResponse", "/api/weeks/2026/5/play-calls", empty)
    t = Tend(2027).fill(1, F.page_pairs(["proe", "blitz_rate", "play_action_rate"]), teams=["KC"])
    first = solo(tmp_path / "first", [(t, build_doc(2027, 1))])
    add("PlaycallTeamsResponse", "/api/playcalling/teams?season=2027", first)
    add("PlaycallTeamResponse", "/api/playcalling/teams/KC?season=2027", first)
    return out


def test_every_answer_matches_the_typescript_types(base, contract, tmp_path):
    seen: dict = defaultdict(set)
    problems: list[str] = []
    for iface, url, body in contract_answers(base, tmp_path):
        problems += check_answer(contract, body, iface, url, seen)
    assert not problems, "\n".join(problems[:30])
    # the fixtures exercise the types: every field of every type an answer used was non-null at
    # least once (a field the answers never fill is a field this check has not looked at)
    owners = {o for o, _ in seen}
    for owner in owners:
        for field in (
            contract.fields_of(owner) if owner in contract.ifaces else seen_fields(owner, seen)
        ):
            assert "value" in seen.get((owner, field), set()), f"{owner}.{field} was never non-null"
    unused = set(contract.ifaces) - owners - {"PlaycallMeta", "PlaycallMetric"}
    assert not unused, f"types.ts declares {sorted(unused)} but no answer uses them"
    # and the nullable ones were null somewhere too
    for _owner, field in (
        ("PlaycallTeamResponse", "next_game"),
        ("PlaycallTeamResponse", "weekly"),
        ("PlaycallTeamResponse", "situations"),
        ("PlaycallMeta", "as_of_week"),
        ("PlaycallMeta", "message"),
        ("PlaycallCell", "value"),
    ):
        assert any(
            "null" in v for (o, f), v in seen.items() if f == field and o.startswith("Play")
        ), field


def seen_fields(owner: str, seen: dict) -> list[str]:
    return [f for o, f in seen if o == owner]


def test_the_contract_check_catches_a_drifting_answer(base, contract):
    body = week_tab(base, 5)
    seen: dict = defaultdict(set)
    assert check_answer(contract, body, "PlayCallsResponse", "ok", seen) == []
    bad = json.loads(json.dumps(body))
    bad["surprise"] = 1
    del bad["window"]
    bad["status"] = "gone"
    bad["games"][0]["kickoff"] = 5
    bad["games"][0]["matchups"][0]["rows"][0]["shift"] = "big"
    bad["games"][0]["matchups"][0]["rows"][0]["offense"]["n"] = None
    bad["shifts"][0]["text"] = None
    del bad["metrics"][0]["help"]
    text = "\n".join(check_answer(contract, bad, "PlayCallsResponse", "bad", seen))
    for needle in (
        "field 'surprise' is not in PlayCallsResponse",
        "PlayCallsResponse.window is missing",
        "'gone'",
        ".kickoff: 5",
        "'big' is not a number",
        ".n: None is not a number",
        ".text: None is not a string",
        "PlaycallMetric.help is missing",
    ):
        assert needle in text, needle
    team = team_page(base)
    team["identity"][0]["rows"][0]["windows"].pop("last4")
    team["field"]["zones"][0]["depth"] = "medium"
    text = "\n".join(check_answer(contract, team, "PlaycallTeamResponse", "team", seen))
    assert "keys ['last_season', 'season'] != ['last4', 'last_season', 'season']" in text
    assert "'medium'" in text


# ---- real data (-m integration) ----------------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    from nflengine.app.server import AppSettings, create_app
    from nflengine.paths import ensure_data_root

    paths = ensure_data_root()
    client = TestClient(
        create_app(AppSettings(services=FakeStatus(), data_root=lambda: paths)), base_url=BASE
    )
    return paths, client


def real_tendencies(paths, season: int) -> pl.DataFrame:
    return pl.read_parquet(paths.playcalling / str(season) / "team_tendencies.parquet")


def real_as_of(paths, season: int) -> int:
    doc = json.loads((paths.playcalling / str(season) / "build.json").read_text("utf-8"))
    return doc["as_of_weeks"][1]


def want_cell(df: pl.DataFrame, team, side, as_of, window, metric, sit, hist=False) -> dict | None:
    rows = df.filter(
        (pl.col("team") == team)
        & (pl.col("side") == side)
        & (pl.col("as_of_week") == as_of)
        & (pl.col("window") == window)
        & (pl.col("metric") == metric)
        & (pl.col("situation") == sit)
        & (pl.col("history_only") == hist)
    )
    assert rows.height <= 1
    return exp_cell(rows.row(0, named=True)) if rows.height else None


def same_cell(got: dict | None, want: dict | None, where: str) -> None:
    if want is None:
        assert got is None, where
        return
    assert got is not None, where
    assert (got["n"], got["games"], got["small"]) == (want["n"], want["games"], want["small"]), (
        where
    )
    for k in ("value", "league", "diff"):
        assert got[k] == pytest.approx(want[k], abs=1e-9), (where, k)
    assert got["pct"] == pytest.approx(want["pct"], abs=1e-9), where


@pytest.mark.integration
@pytest.mark.parametrize("season", [2025, 2026])
@pytest.mark.parametrize("team", ["KC", "MIN", "LA", "CHI"])
@pytest.mark.parametrize("side", ["offense", "defense"])
def test_real_team_pages_are_the_parquet_rows(real, season, team, side):
    paths, client = real
    df = real_tendencies(paths, season)
    as_of = real_as_of(paths, season)
    b = get(client, f"/api/playcalling/teams/{team}?season={season}&side={side}")
    assert (b["status"], b["as_of_week"]) == ("ok", as_of)
    n_rows = 0
    for g in b["identity"]:
        for row in g["rows"]:
            for w in WINDOWS:
                same_cell(
                    row["windows"][w],
                    want_cell(df, team, side, as_of, w, row["metric"], row["situation"]),
                    f"{season} {team} {side} {row['metric']} {w}",
                )
                n_rows += row["windows"][w] is not None
    assert n_rows > 40
    for fam in b["situations"]["families"]:
        for srow in fam["rows"]:
            for m, windows in srow["cells"].items():
                for w in WINDOWS:
                    same_cell(
                        windows[w],
                        want_cell(df, team, side, as_of, w, m, srow["situation"]),
                        f"{season} {team} {side} {m} {srow['situation']} {w}",
                    )
    for lane in b["runs"]:
        for w in WINDOWS:
            same_cell(
                lane["windows"][w],
                want_cell(df, team, side, as_of, w, lane["metric"], "all"),
                lane["metric"],
            )
    for z in b["field"]["zones"]:
        for w in WINDOWS:
            same_cell(
                z["windows"][w],
                want_cell(df, team, side, as_of, w, z["metric"], "all"),
                z["metric"],
            )
    assert b["games"] == int(
        df.filter(
            (pl.col("team") == team)
            & (pl.col("side") == side)
            & (pl.col("as_of_week") == as_of)
            & (pl.col("window") == "season")
            & ~pl.col("history_only")
        )["games"].max()
        or 0
    )


@pytest.mark.integration
def test_real_kc_2025_identity_equals_the_parquet_rows(real):
    """The headline numbers of the spot check (KC 2025, regular season plus playoffs)."""
    paths, client = real
    b = get(client, "/api/playcalling/teams/KC?season=2025")
    rows = {r["metric"]: r for g in b["identity"] for r in g["rows"]}
    df = real_tendencies(paths, 2025)
    full = df.filter(
        (pl.col("team") == "KC")
        & (pl.col("side") == "offense")
        & (pl.col("as_of_week") == 23)
        & (pl.col("window") == "season")
        & (pl.col("situation") == "all")
        & ~pl.col("history_only")
    )
    for r in full.iter_rows(named=True):
        if r["metric"] in rows and rows[r["metric"]]["situation"] == "all":
            assert rows[r["metric"]]["windows"]["season"]["value"] == pytest.approx(
                r["value"], abs=1e-6
            )
    assert rows["rpo_rate"]["windows"]["season"]["pct"] == 100.0


@pytest.mark.integration
def test_real_grid_has_32_teams_and_the_stored_cells(real):
    paths, client = real
    for season in (2025, 2026):
        df = real_tendencies(paths, season)
        as_of = real_as_of(paths, season)
        b = get(client, f"/api/playcalling/teams?season={season}")
        assert ids(b["teams"], "team") == TEAMS and b["as_of_week"] == as_of
        window = "season" if as_of > 1 else "last_season"
        for tm in b["teams"]:
            for c in b["columns"]:
                same_cell(
                    tm["cells"][c["id"]],
                    want_cell(
                        df, tm["team"], c["side"], as_of, window, c["metric"], c["situation"]
                    ),
                    f"{season} {tm['team']} {c['id']}",
                )


@pytest.mark.integration
def test_real_week_tab_lists_every_curated_game_with_the_stored_cells(real):
    paths, client = real
    season, week = 2026, 5
    assert real_as_of(paths, season) >= week
    games = pl.read_parquet(
        paths.curated / "games.parquet",
        columns=["game_id", "season", "week", "away_team", "home_team"],
    )
    want = games.filter((pl.col("season") == season) & (pl.col("week") == week))
    b = get(client, f"/api/weeks/{season}/{week}/play-calls")
    assert b["status"] == "ok" and b["window"] == "season" and b["as_of_week"] == week
    assert sorted(ids(b["games"], "game_id")) == sorted(want["game_id"].to_list())
    assert len(b["games"]) == want.height
    df = real_tendencies(paths, season)
    pairs = {(r["game_id"]): (r["away_team"], r["home_team"]) for r in want.iter_rows(named=True)}
    for g in b["games"]:
        assert (g["away"], g["home"]) == pairs[g["game_id"]]
        for mu in g["matchups"]:
            for row in mu["rows"]:
                sit = next(m["situation"] for m in b["metrics"] if m["metric"] == row["metric"])
                same_cell(
                    row["offense"],
                    want_cell(df, mu["offense"], "offense", week, "season", row["metric"], sit),
                    "offense",
                )
                same_cell(
                    row["defense"],
                    want_cell(df, mu["defense"], "defense", week, "season", row["metric"], sit),
                    "defense",
                )
    # the shifts, worked out again from the stored defense rows
    defs = df.filter(
        (pl.col("side") == "defense")
        & (pl.col("as_of_week") == week)
        & (pl.col("window") == "season")
        & ~pl.col("history_only")
    )
    for s in b["shifts"]:
        sit = next(m["situation"] for m in b["metrics"] if m["metric"] == s["metric"])
        col = defs.filter((pl.col("metric") == s["metric"]) & (pl.col("situation") == sit))
        sd = col["value"].std()
        row = col.filter(pl.col("team") == s["defense"]).row(0, named=True)
        assert s["shift"] == pytest.approx(round(r6(row["diff"]) / sd, 2), abs=0.011)
        assert abs(s["shift"]) >= 1 and row["n"] >= 20
    assert 0 < len(b["shifts"]) <= 8
    mags = [abs(s["shift"]) for s in b["shifts"]]
    assert mags == sorted(mags, reverse=True)


@pytest.mark.integration
def test_real_history_is_the_participation_rows(real):
    paths, client = real
    b = get(client, "/api/playcalling/teams/KC/history?side=offense")
    assert b["status"] == "ok" and b["seasons"] == [2023, 2024, 2025]
    frames = {s: real_tendencies(paths, s) for s in b["seasons"]}
    n = 0
    for g in b["groups"]:
        for row in g["rows"]:
            for s in b["seasons"]:
                same_cell(
                    row["seasons"][str(s)],
                    want_cell(
                        frames[s],
                        "KC",
                        "offense",
                        real_as_of(paths, s),
                        "season",
                        row["metric"],
                        "all",
                        hist=True,
                    ),
                    f"{row['metric']} {s}",
                )
                n += row["seasons"][str(s)] is not None
    assert n > 100


@pytest.mark.integration
def test_real_answers_match_the_typescript_types(real, contract):
    _, client = real
    seen: dict = defaultdict(set)
    problems: list[str] = []
    urls = [
        ("PlaycallTeamsResponse", "/api/playcalling/teams"),
        ("PlaycallTeamsResponse", "/api/playcalling/teams?season=2025"),
        ("PlaycallTeamResponse", "/api/playcalling/teams/KC"),
        ("PlaycallTeamResponse", "/api/playcalling/teams/MIN?side=defense"),
        ("PlaycallTeamResponse", "/api/playcalling/teams/LA?season=2025&side=offense"),
        ("PlaycallHistoryResponse", "/api/playcalling/teams/KC/history"),
        ("PlaycallHistoryResponse", "/api/playcalling/teams/MIN/history?side=defense"),
        ("PlayCallsResponse", "/api/weeks/2026/5/play-calls"),
        ("PlayCallsResponse", "/api/weeks/2026/1/play-calls"),
        ("PlayCallsResponse", "/api/weeks/2026/22/play-calls"),
    ]
    for iface, url in urls:
        problems += check_answer(contract, get(client, url), iface, url, seen)
    assert not problems, "\n".join(problems[:30])


@pytest.mark.integration
def test_real_reads_write_nothing(real):
    paths, client = real

    def snapshot():
        return {
            p.relative_to(paths.playcalling).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in paths.playcalling.rglob("*")
            if p.is_file()
        }

    before = snapshot()
    for url in (
        "/api/playcalling/teams",
        "/api/playcalling/teams/KC",
        "/api/playcalling/teams/KC?season=2025&side=defense",
        "/api/playcalling/teams/KC/history",
        "/api/weeks/2026/5/play-calls",
        "/api/weeks/2026/1/play-calls",
    ):
        get(client, url)
    assert snapshot() == before


@pytest.mark.integration
def test_real_every_team_answers_on_both_sides(real):
    _, client = real
    for team in sorted(CANONICAL_TEAMS):
        for side in ("offense", "defense"):
            b = get(client, f"/api/playcalling/teams/{team}?side={side}")
            assert b["status"] == "ok" and b["identity"] and b["summary"], (team, side)


@pytest.mark.integration
def test_the_fixture_schemas_are_the_real_files(real):
    paths, _ = real
    from nflengine.playcalling import build as B

    d = paths.playcalling / "2026"
    assert dict(pl.scan_parquet(d / "team_tendencies.parquet").collect_schema()) == B.TEND_SCHEMA
    assert dict(pl.scan_parquet(d / "team_game_tendencies.parquet").collect_schema()) == F.TG_SCHEMA
    assert (
        dict(pl.scan_parquet(d / "league_tendencies.parquet").collect_schema()) == B.LEAGUE_SCHEMA
    )
    real_build = json.loads((d / "build.json").read_text("utf-8"))
    ours = build_doc(2026, 5)
    assert set(ours) <= set(real_build), set(ours) - set(real_build)
