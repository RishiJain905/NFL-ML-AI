"""PC03: the play browser and the play diagrams, backend
(documentation/play-calling/PC03-play-diagrams.md).

`GET /api/playcalling/teams/{team}/plays?season=&side=&week=&down=&type=&result=` lists a team's
plays for one week; `GET /api/playcalling/plays/{game_id}/{play_id}/diagram` returns one play's
reconstruction (`playcalling/diagrams.py`). Both read `playcalling/<S>/plays_enriched.parquet`
(PC00) on a synthetic tree (`playbrowser_fixtures.py`): KC's week 4 has 17 plays, each declared
with the down, the play types and the results it must match, so every filter has a hand-made
answer. The real-clip route (`/api/playcalling/concepts/{concept}`) is in
`test_app_playbrowser_clip.py`.

Safety (README §5): the endpoints write nothing, send no NaN, no secret-shaped text and no
path, never echo a bad code, and read parquet with short filtered reads (no memory map).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, NamedTuple

import playbrowser_fixtures as F
import polars as pl
import pytest
import test_app_playcalling as T
from app_helpers import make_client, make_paths
from fastapi.testclient import TestClient
from playcall_diagram_fixtures import check_diagram

import nflengine.app.readers.playbrowser as R
from nflengine.curate.teams import CANONICAL_TEAMS
from nflengine.ops.summary import scrub
from nflengine.paths import DataRootError
from nflengine.playcalling import diagrams as D

NO_PLAY = "No such play: check the game and the play id."
NO_TEAM = "No such team: use one of the 32 team codes."
FAIL_422 = T.FAIL_422
LABEL = "Reconstructed from charting data, not player tracking"
SUMMARY_KEYS = {
    "game_id", "play_id", "week", "offense", "defense", "quarter", "quarter_text", "clock", "down",
    "distance", "down_text", "yards_to_goal", "spot", "type", "tags", "result", "epa", "explosive",
    "charted", "concept", "desc",
}  # fmt: skip


class Tree(NamedTuple):
    root: Path
    paths: Any
    client: TestClient


@pytest.fixture(scope="module")
def tree(tmp_path_factory) -> Tree:
    """One tree for the read-only tests (module scope: the tables are written once)."""
    root = tmp_path_factory.mktemp("pc03app")
    paths = make_paths(root)
    F.make_world(paths)
    return Tree(root, paths, make_client(root))


def get(client: TestClient, url: str, status: int = 200) -> dict[str, Any]:
    r = client.get(url)
    assert r.status_code == status, (url, r.status_code, r.text[:300])
    return r.json()


def plays_url(team="KC", **q: Any) -> str:
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return f"/api/playcalling/teams/{team}/plays" + (f"?{qs}" if qs else "")


def listing(tree: Tree, team="KC", **q: Any) -> dict[str, Any]:
    return get(tree.client, plays_url(team, **q))


def ids(body: dict[str, Any]) -> list[int]:
    return [p["play_id"] for p in body["plays"]]


def diagram_url(gid: str, pid: int) -> str:
    return f"/api/playcalling/plays/{gid}/{pid}/diagram"


def diagram(tree: Tree, gid: str, pid: int) -> dict[str, Any]:
    return get(tree.client, diagram_url(gid, pid))["diagram"]


# ---- the list: weeks, the default week, the headers ----------------------------------------------


def test_the_list_header_and_the_options(tree):
    b = listing(tree, week=4)
    assert b["status"] == "ok" and b["message"] is None
    assert (b["season"], b["seasons"], b["team"], b["side"]) == (
        2026,
        [2026, 2025, 2024],
        "KC",
        "offense",
    )
    assert b["label"] == LABEL
    assert b["filters"] == {"down": None, "type": None, "result": None}
    assert b["options"]["down"] == [1, 2, 3, 4]
    assert [o["value"] for o in b["options"]["type"]] == list(F.TYPES)
    assert [o["value"] for o in b["options"]["result"]] == list(F.RESULTS)
    assert [o["label"] for o in b["options"]["type"]] == [
        "Runs", "Passes", "Sacks", "Scrambles", "Play-action", "Screens", "RPOs", "Deep shots",
        "Motion",
    ]  # fmt: skip
    assert [o["label"] for o in b["options"]["result"]] == [
        "First downs", "Touchdowns", "Turnovers", "Explosive", "Lost yards", "Incomplete",
        "Successful",
    ]  # fmt: skip
    assert b["library"] is False


def test_kcs_weeks_are_every_week_it_has_plays_in_with_the_opponent_and_home(tree):
    b = listing(tree)
    assert b["weeks"] == [
        {"week": 1, "game_id": "2026_01_KC_LV", "opponent": "LV", "home": False, "plays": 5,
         "label": "Week 1 · at LV"},
        {"week": 3, "game_id": "2026_03_DEN_KC", "opponent": "DEN", "home": True, "plays": 4,
         "label": "Week 3 · vs DEN"},
        {"week": 4, "game_id": "2026_04_KC_DEN", "opponent": "DEN", "home": False, "plays": 17,
         "label": "Week 4 · at DEN"},
        {"week": 6, "game_id": "2026_06_KC_ZZZ", "opponent": None, "home": None, "plays": 3,
         "label": "Week 6"},
    ]  # fmt: skip
    # the malformed game id of week 7 never shows


def test_the_default_week_is_the_newest_week_with_plays(tree):
    b = listing(tree)
    assert b["week"] == 6 and b["game"] == b["weeks"][-1]
    assert b["counts"] == {"total": 3, "shown": 3} and ids(b) == [61, 62, 63]
    # KC is on a bye in week 5, the build's newest as-of week: the default is its own newest
    assert 5 not in [w["week"] for w in b["weeks"]]


def test_a_chosen_week_lists_that_weeks_plays(tree):
    b = listing(tree, week=3)
    assert b["week"] == 3 and b["game"]["game_id"] == "2026_03_DEN_KC"
    assert ids(b) == [21, 22, 23, 24] and b["counts"] == {"total": 4, "shown": 4}
    assert [p["offense"] for p in b["plays"]] == ["KC"] * 4
    assert {p["defense"] for p in b["plays"]} == {"DEN"}
    # every week of the weeks list answers
    for w in b["weeks"]:
        one = listing(tree, week=w["week"])
        assert one["status"] == "ok" and one["counts"]["total"] == w["plays"]


def test_the_opponents_old_code_is_mapped_to_its_canonical_one(tree):
    b = listing(tree, week=1)  # the file says OAK
    assert b["weeks"][0]["opponent"] == "LV" and b["weeks"][0]["label"] == "Week 1 · at LV"
    assert {p["defense"] for p in b["plays"]} == {"LV"}
    assert "OAK" not in json.dumps(b)
    assert [p["spot"] for p in b["plays"]] == ["KC 25"] * 5


def test_a_code_that_is_not_a_team_never_reaches_the_browser(tree):
    b = listing(tree, week=6)
    assert b["game"]["opponent"] is None and b["game"]["home"] is None
    assert b["game"]["label"] == "Week 6"
    assert {p["defense"] for p in b["plays"]} == {None}
    # the game id (a validated identifier) names it; no team field or label does
    assert "ZZZ" not in json.dumps(b).replace("2026_06_KC_ZZZ", "")


def test_the_defense_side_lists_the_plays_run_against_the_team(tree):
    b = listing(tree, side="defense")
    assert b["side"] == "defense" and [w["week"] for w in b["weeks"]] == [1, 3, 4]
    assert b["week"] == 4 and ids(b) == [1001, 1002, 1003]
    assert {p["offense"] for p in b["plays"]} == {"DEN"} and {p["defense"] for p in b["plays"]} == {
        "KC"
    }
    assert b["weeks"][0]["opponent"] == "LV"  # the OAK rows
    # `home` is the team's: KC was away at LV and at DEN, at home to DEN in week 3
    assert [w["home"] for w in b["weeks"]] == [False, True, False]
    assert [w["label"] for w in b["weeks"]] == [
        "Week 1 · at LV",
        "Week 3 · vs DEN",
        "Week 4 · at DEN",
    ]
    assert [w["plays"] for w in b["weeks"]] == [3, 2, 3]
    one = listing(tree, side="defense", week=1)
    assert {p["offense"] for p in one["plays"]} == {"LV"}


def test_a_playoff_game_is_labelled(tree):
    lv = listing(tree, "LV")
    assert [w["week"] for w in lv["weeks"]] == [5, 19]
    assert lv["week"] == 19 and lv["game"]["label"] == "Week 19 (playoffs) · at DEN"
    den = listing(tree, "DEN", week=19)
    assert den["game"]["label"] == "Week 19 (playoffs) · vs LV"
    assert [w["label"] for w in den["weeks"]][:2] == ["Week 3 · at KC", "Week 4 · vs KC"]


def test_a_team_with_no_game_in_the_chosen_week_has_no_week(tree):
    for week in (2, 5, 22):
        b = listing(tree, week=week)
        assert b["status"] == "no_week" and b["week"] is None and b["game"] is None
        assert b["plays"] == [] and b["counts"] == {"total": 0, "shown": 0}
        assert f"week {week} of 2026" in b["message"] and "KC" in b["message"]
        assert [w["week"] for w in b["weeks"]] == [1, 3, 4, 6]  # the other weeks stay choosable
    b = listing(tree, "KC", week=5, down=3, type="pass")
    assert b["status"] == "no_week" and b["filters"] == {"down": 3, "type": "pass", "result": None}


def test_a_team_without_plays_and_a_team_with_only_malformed_game_ids_have_no_plays(tree):
    for team in ("NYJ", "CHI"):
        b = listing(tree, team)
        assert b["status"] == "no_plays" and b["message"] == f"No plays for {team} in 2026."
        assert b["weeks"] == [] and b["plays"] == [] and b["week"] is None


def test_seasons_not_built_say_how_to_build_them(tree):
    for season in (2024, 2023, 2022, 2030):
        b = listing(tree, season=season)
        assert b["status"] == "not_built" and b["season"] == season
        assert b["message"] == (
            f"No play-by-play tables for {season} yet "
            f"(`uv run nfl playcalling build --season {season}`)."
        )
        assert b["weeks"] == [] and b["plays"] == [] and b["seasons"] == [2026, 2025, 2024]
    # 2023 has a plays file but no build.json: it isn't a built season
    assert 2023 not in listing(tree, season=2023)["seasons"]


def test_the_2025_season_lists_with_participation(tree):
    b = listing(tree, season=2025)
    assert b["status"] == "ok" and b["season"] == 2025 and b["week"] == 1
    assert b["game"]["label"] == "Week 1 · at DEN" and ids(b) == [1, 2, 3]
    assert listing(tree, "DEN", season=2025, side="defense")["plays"][0]["offense"] == "KC"


def test_no_season_means_the_newest_built_one(tree, tmp_path):
    assert listing(tree)["season"] == 2026
    solo = make_client(tmp_path)  # nothing built: the app's current season
    b = get(solo, plays_url())
    assert b["status"] == "not_built" and b["season"] == 2026 and b["seasons"] == []


# ---- the list: plays, order and filters ----------------------------------------------------------


def test_the_plays_come_in_game_order_not_file_order(tree):
    b = listing(tree, week=4)
    want = F.expected(F.W4)
    assert ids(b) == [s.pid for s in want]
    assert ids(b)[0] == 170  # order_sequence 5: first
    assert ids(b).index(30) + 1 == ids(b).index(40)  # a shared order_sequence: by play id
    assert b["counts"] == {"total": 17, "shown": 17}


def test_every_play_row_carries_exactly_the_summary_fields(tree):
    for p in listing(tree, week=4)["plays"]:
        assert set(p) == SUMMARY_KEYS


def test_play_rows_are_the_hand_declared_plays(tree):
    by_id = {p["play_id"]: p for p in listing(tree, week=4)["plays"]}
    for s in F.W4:
        p = by_id[s.pid]
        assert p["type"] == s.kind and p["down"] == s.down, s.pid
        assert (p["offense"], p["defense"], p["game_id"], p["week"]) == (
            "KC",
            "DEN",
            "2026_04_KC_DEN",
            4,
        )
    assert by_id[10]["result"]["text"] == "Run, +6"
    assert by_id[10]["concept"] == "inside_zone" and by_id[10]["desc"].startswith("10-I.Pacheco")
    assert by_id[40]["result"]["text"] == "Sacked, \u22127"
    assert by_id[60]["concept"] == "screen" and by_id[60]["tags"] == ["screen"]
    assert by_id[70]["tags"] == ["play_action", "deep_shot", "motion"]
    assert by_id[70]["concept"] == "play_action_deep" and by_id[70]["explosive"] is True
    assert (
        by_id[100]["result"]["text"] == "Run, +3, touchdown"
        and by_id[100]["down_text"] == "4th & Goal"
    )
    assert by_id[130]["type"] == "aborted" and by_id[130]["result"]["text"] == "Aborted snap"
    assert by_id[10]["quarter_text"] == "Q2" and by_id[10]["clock"] == "10:15"
    assert by_id[10]["spot"] == "KC 25" and by_id[100]["spot"] == "DEN 3"
    assert by_id[10]["epa"] == 0.51 and by_id[10]["charted"] is True


def test_a_pick_six_is_a_turnover_and_not_the_offenses_touchdown(tree):
    p = next(p for p in listing(tree, week=4)["plays"] if p["play_id"] == 170)
    assert p["result"]["text"] == "Intercepted" and p["result"]["touchdown"] is False
    assert p["result"]["turnover"] is True
    assert 170 not in ids(listing(tree, week=4, result="touchdown"))
    assert 170 in ids(listing(tree, week=4, result="turnover"))


@pytest.mark.parametrize("down", [1, 2, 3, 4])
def test_the_down_filter(tree, down):
    b = listing(tree, week=4, down=down)
    assert ids(b) == [s.pid for s in F.expected(F.W4, down=down)]
    assert b["counts"] == {"total": 17, "shown": len(b["plays"])} and b["filters"]["down"] == down
    assert {p["down"] for p in b["plays"]} == {down}


@pytest.mark.parametrize("kind", F.TYPES)
def test_every_play_type_filter(tree, kind):
    b = listing(tree, week=4, type=kind)
    want = F.expected(F.W4, kind=kind)
    assert ids(b) == [s.pid for s in want] and want, kind
    assert b["counts"]["total"] == 17 and b["filters"]["type"] == kind


@pytest.mark.parametrize("result", F.RESULTS)
def test_every_result_filter(tree, result):
    b = listing(tree, week=4, result=result)
    want = F.expected(F.W4, result=result)
    assert ids(b) == [s.pid for s in want] and want, result
    assert b["counts"]["total"] == 17 and b["filters"]["result"] == result


def test_the_hand_declared_answers_are_the_numbers_we_think_they_are():
    """A guard on the table the filter tests lean on."""
    n = lambda **k: len(F.expected(F.W4, **k))  # noqa: E731
    assert [n(down=d) for d in (1, 2, 3, 4)] == [6, 5, 5, 1]
    assert [n(kind=k) for k in F.TYPES] == [4, 12, 1, 1, 2, 1, 1, 1, 2]
    assert [n(result=r) for r in F.RESULTS] == [6, 2, 3, 2, 2, 1, 9]


@pytest.mark.parametrize(
    ("flags", "kind", "want"),
    [
        # a QB who scrambled and then threw passed: his play is a pass, not a scramble
        ({"scramble": True, "pass_attempt": True}, "scramble", False),
        ({"scramble": True, "pass_attempt": True}, "pass", True),
        ({"scramble": True, "pass_attempt": True}, "run", False),
        ({"scramble": True, "dropback": True}, "scramble", True),
        ({"scramble": True, "dropback": True}, "pass", True),
        ({"sack": True, "pass_attempt": True}, "sack", True),
        ({"sack": True, "pass_attempt": True}, "pass", True),
        ({"aborted": True, "sack": True}, "sack", True),
        ({"aborted": True}, "run", False),
        ({"aborted": True}, "pass", False),
        ({"dropback": True}, "pass", True),
        ({"dropback": True}, "run", False),
        ({}, "run", True),
        ({}, "sack", False),
        # the flag types are the flag itself, `True` and nothing else
        ({"play_action": True}, "play_action", True),
        ({"play_action": 1}, "play_action", False),
        ({"motion": None}, "motion", False),
        ({"deep_shot": True, "play_action": False}, "play_action", False),
    ],
)
def test_the_type_filter_follows_the_plays_kind_and_flags(flags, kind, want):
    assert R._matches_type(flags, kind) is want


def test_the_type_filters_follow_the_readers_rules(tree):
    # `pass` includes sacks and scrambles; `run` is a designed run only (no scramble, no abort)
    assert {40, 50} <= set(ids(listing(tree, week=4, type="pass")))
    assert not {40, 50, 130} & set(ids(listing(tree, week=4, type="run")))
    assert 130 not in ids(listing(tree, week=4, type="pass"))
    # incomplete is only an incompletion: an interception is a turnover, not incomplete
    assert ids(listing(tree, week=4, result="incomplete")) == [20]
    assert 80 not in ids(listing(tree, week=4, result="incomplete"))
    # negative: yards below zero, including a sack
    assert ids(listing(tree, week=4, result="negative")) == [40, 90]
    # a lost fumble is a turnover
    assert 110 in ids(listing(tree, week=4, result="turnover"))


def test_filters_combine_with_and(tree):
    b = listing(tree, week=4, down=3, type="pass")
    assert ids(b) == [s.pid for s in F.expected(F.W4, down=3, kind="pass")]
    assert set(ids(b)) == {30, 60, 140, 170} and b["counts"] == {"total": 17, "shown": 4}
    assert b["filters"] == {"down": 3, "type": "pass", "result": None}
    b = listing(tree, week=4, down=1, type="run", result="negative")
    assert ids(b) == [90]
    b = listing(tree, week=4, down=2, type="pass", result="success")
    assert ids(b) == [s.pid for s in F.expected(F.W4, 2, "pass", "success")]


def test_a_filter_that_matches_nothing_is_an_empty_ok(tree):
    b = listing(tree, week=4, down=4, type="sack")
    assert b["status"] == "ok" and b["plays"] == [] and b["counts"] == {"total": 17, "shown": 0}
    assert b["game"]["plays"] == 17 and b["week"] == 4


def test_filters_work_on_the_defense_side_too(tree):
    b = listing(tree, side="defense", week=4, down=2)
    assert all(p["down"] == 2 for p in b["plays"]) and b["counts"]["total"] == 3
    assert ids(b) == [1003]  # the downs of DEN's plays: 3, 1, 2


def test_the_filter_values_are_echoed_as_validated(tree):
    b = listing(tree, week=4, down=2, type="rpo", result="success")
    assert b["filters"] == {"down": 2, "type": "rpo", "result": "success"}
    assert ids(b) == [120]


def test_a_missing_value_in_a_play_is_null_not_an_error(tree):
    b = listing(tree, week=6)
    p = next(p for p in b["plays"] if p["play_id"] == 63)
    assert p["quarter"] is None and p["quarter_text"] is None and p["clock"] is None
    assert p["down"] is None and p["down_text"] is None and p["distance"] is None
    assert p["epa"] is None and p["desc"] is None and p["spot"] is None
    assert p["result"]["yards"] is None and p["result"]["text"] == "Complete, no gain recorded"
    assert next(p for p in b["plays"] if p["play_id"] == 62)["charted"] is False


# ---- the diagram ---------------------------------------------------------------------------------


def test_every_week_4_play_has_a_diagram_that_meets_the_contract(tree):
    for s in F.W4:
        body = get(tree.client, diagram_url("2026_04_KC_DEN", s.pid))
        d = body["diagram"]
        assert body["status"] == "ok" and d["label"] == LABEL and d["kind"] == "reconstruction"
        check_diagram(d, s.row)
        p = d["play"]
        assert p["play_id"] == s.pid and p["type"] == s.kind and p["down"] == s.down
        assert p["game_label"] == "KC at DEN, week 4" and p["season"] == 2026
        assert (p["offense"], p["defense"]) == ("KC", "DEN") and p["game_id"] == "2026_04_KC_DEN"
        assert d["real_example"] is None  # no library on this tree


def test_the_diagrams_play_summary_is_the_lists_row_plus_the_game_label_and_season(tree):
    listed = {p["play_id"]: p for p in listing(tree, week=4)["plays"]}
    for pid in (10, 40, 70, 130, 170):
        p = diagram(tree, "2026_04_KC_DEN", pid)["play"]
        want = {**listed[pid], "game_label": "KC at DEN, week 4", "season": 2026}
        assert p == want, pid


def test_the_diagram_is_the_pure_function_of_the_stored_row(tree):
    for s in F.W4[:6]:
        got = diagram(tree, "2026_04_KC_DEN", s.pid)
        row = {**s.row, "game_id": "2026_04_KC_DEN", "offense": "KC", "defense": "DEN"}
        want = D.diagram(row, [])
        for key in (
            "players",
            "ball",
            "marks",
            "phases",
            "duration",
            "field",
            "known",
            "approximate",
        ):
            assert got[key] == json.loads(json.dumps(want[key])), (s.pid, key)


def test_a_qb_run_is_found_from_the_passers_of_that_game_and_offense(tmp_path):
    paths = make_paths(tmp_path)
    kc_pass = F.simple(1, passer_id="QB-KC")
    kc_run = {**F.X.run_row(play_id=2.0, order_sequence=2.0, rusher_id="QB-KC")}
    den_pass = F.simple(3, passer_id="QB-DEN")
    other = {
        **F.X.run_row(play_id=4.0, order_sequence=4.0, rusher_id="QB-DEN")
    }  # KC run, DEN's passer
    rows = F.rows_in(F.G4, "KC", "DEN", False, [kc_pass, kc_run, other])
    rows += F.rows_in(F.G4, "DEN", "KC", True, [den_pass])
    F.write_plays(paths, 2026, rows)
    c = make_client(tmp_path)
    qb = get(c, diagram_url("2026_04_KC_DEN", 2))["diagram"]
    assert next(p for p in qb["players"] if p["carrier"])["role"] == "qb"
    assert "The QB carried it (play-by-play)" in qb["known"]
    not_qb = get(c, diagram_url("2026_04_KC_DEN", 4))["diagram"]  # DEN's passer isn't KC's
    assert next(p for p in not_qb["players"] if p["carrier"])["role"] == "back"


def test_the_diagram_of_the_other_teams_play_in_the_same_game(tree):
    d = diagram(tree, "2026_04_KC_DEN", 1001)
    assert (d["play"]["offense"], d["play"]["defense"]) == ("DEN", "KC")
    check_diagram(d)


def test_a_game_with_an_unknown_team_code_has_no_label_and_no_opponent_text(tree):
    d = diagram(tree, "2026_06_KC_ZZZ", 61)
    assert d["play"]["game_label"] is None and d["play"]["defense"] is None
    assert "ZZZ" not in json.dumps(d).replace("2026_06_KC_ZZZ", "")
    d = diagram(tree, "2026_06_KC_ZZZ", 62)  # a play FTN didn't chart: no hash
    assert d["field"]["hash"] is None and d["play"]["charted"] is False


def test_old_team_codes_in_the_file_are_mapped_in_the_diagram(tree):
    d = diagram(tree, "2026_01_KC_LV", 1)  # defense OAK
    assert d["play"]["defense"] == "LV" and d["play"]["game_label"] == "KC at LV, week 1"
    d = diagram(tree, "2026_01_KC_LV", 11)  # offense OAK
    assert d["play"]["offense"] == "LV" and d["play"]["defense"] == "KC"
    assert "OAK" not in json.dumps(d)


def test_the_2025_diagram_uses_participation(tree):
    d = diagram(tree, "2025_01_KC_DEN", 1)
    assert "nflverse participation (research data)" in d["sources"]
    assert d["play"]["season"] == 2025 and d["play"]["game_label"] == "KC at DEN, week 1"
    check_diagram(d)


@pytest.mark.parametrize(
    ("gid", "pid"),
    [
        ("2030_01_KC_DEN", 1),  # a season that isn't built
        ("2024_01_KC_DEN", 1),  # built, plays file missing
        ("2023_01_KC_DEN", 1),  # plays file, no build.json
        ("2026_09_KC_LV", 1),  # a game that isn't there
        ("2026_04_LV_KC", 10),  # the teams the wrong way round
        ("2026_04_KC_DEN", 99999),  # a play that isn't there
        ("2026_04_KC_DEN", 61),  # a play of another game
        ("2026_01_KC_LV", 10),  # a play id from week 4
        ("2026_04_KC_DEN", 5),
    ],
)
def test_an_unknown_play_is_a_fixed_404_that_does_not_echo_the_ids(tree, gid, pid):
    r = tree.client.get(diagram_url(gid, pid))
    assert r.status_code == 404
    assert r.json()["error"] == {"code": "not_found", "message": NO_PLAY}
    assert gid not in r.text and str(pid) not in r.text


@pytest.mark.parametrize(
    "gid", ["abc", "2026_4_KC_DEN", "2026_04_kc_DEN", "2026_04_KC_DEN_X", "26_04_KC_DEN",
            "2026-04-KC-DEN", "2026_04_K_DEN", "2026_04_KCCC_DEN", "2026_04_KC_D3N"],
)  # fmt: skip
def test_a_malformed_game_id_is_a_422_with_fixed_text(tree, gid):
    r = tree.client.get(diagram_url(gid, 10))
    assert r.status_code == 422
    assert r.json()["error"] == {"code": "bad_request", "message": FAIL_422}
    assert gid not in r.text


@pytest.mark.parametrize("pid", ["0", "-1", "100000", "x", "1.5", "99999999999999999999"])
def test_a_bad_play_id_is_a_422(tree, pid):
    r = tree.client.get(f"/api/playcalling/plays/2026_04_KC_DEN/{pid}/diagram")
    assert r.status_code == 422, pid
    assert r.json()["error"]["message"] == FAIL_422 and pid not in r.text.replace(FAIL_422, "")


def test_path_tricks_in_the_game_id_are_not_files(tree):
    for gid in ("..", "%2e%2e", "2026_04_KC_DEN%2f..%2f..", "%5c%5c", "../../build.json"):
        r = tree.client.get(f"/api/playcalling/plays/{gid}/10/diagram")
        assert r.status_code in (404, 422), gid
        assert "build_at" not in r.text and "schema_version" not in r.text


@pytest.mark.parametrize(
    "q",
    [
        "type=bogus", "type=RUN", "type=run,pass", "type=", "result=bogus", "result=Success",
        "down=5", "down=0", "down=-1", "down=x", "down=1.5", "week=0", "week=23", "week=x",
        "week=1.5", "side=both", "side=OFFENSE", "side=", "season=1998", "season=2101", "season=x",
    ],
)  # fmt: skip
def test_bad_query_values_are_a_422_with_fixed_text_that_does_not_echo_them(tree, q):
    r = tree.client.get(f"/api/playcalling/teams/KC/plays?{q}")
    assert r.status_code == 422, q
    assert r.json()["error"] == {"code": "bad_request", "message": FAIL_422}
    value = q.split("=", 1)[1]
    if value and value not in FAIL_422:
        assert value not in r.text


@pytest.mark.parametrize("team", ["kc", "K", "KCCC", "K1", "ZZ9", "K-C", "%20KC"])
def test_a_malformed_team_is_a_422(tree, team):
    r = tree.client.get(plays_url(team))
    assert r.status_code == 422, team
    assert r.json()["error"]["message"] == FAIL_422


@pytest.mark.parametrize("team", ["XYZ", "OAK", "SD", "STL", "AAA", "LVR", "NFL"])
def test_a_well_formed_code_that_is_not_a_team_is_a_404_that_does_not_echo_it(tree, team):
    assert team not in CANONICAL_TEAMS
    r = tree.client.get(plays_url(team, week=4))
    assert r.status_code == 404
    assert r.json()["error"] == {"code": "not_found", "message": NO_TEAM}
    assert team not in r.text


@pytest.mark.parametrize("team", sorted(CANONICAL_TEAMS))
def test_every_canonical_team_answers_on_both_sides(tree, team):
    for side in ("offense", "defense"):
        b = listing(tree, team, side=side)
        assert b["status"] in ("ok", "no_plays"), (team, side)
        assert b["team"] == team and b["side"] == side and b["label"] == LABEL


def test_extra_query_parameters_cannot_name_a_file(tree):
    plain = tree.client.get(plays_url(week=4)).text
    for q in ("path=C:/Windows/win.ini", "file=../../x.parquet", "root=D:/", "cmd=dir"):
        assert tree.client.get(plays_url(week=4) + f"&{q}").text == plain


# ---- the library flag and the real example -------------------------------------------------------


def test_the_library_flag_and_the_real_example_follow_the_index(tmp_path):
    paths = make_paths(tmp_path)
    F.make_world(paths)
    c = make_client(tmp_path)
    assert get(c, plays_url(week=4))["library"] is False
    assert get(c, diagram_url("2026_04_KC_DEN", 10))["diagram"]["real_example"] is None
    F.write_library(
        paths,
        {
            # three usable names and one the clip endpoint would never read: the button counts
            # only what the clip can open (the test worker's finding)
            "inside_zone": {
                "name": "x",
                "examples": [
                    {"file": "2023091000_101.json"},
                    {"file": "2023091000_102.json"},
                    {"file": "2023091000_103.json"},
                    {"file": "../../x.json"},
                ],
            },
            "screen": {"examples": [{"file": "a"}]},  # only an unusable name: no button
            "play_action_deep": "oops",  # not an entry
            "outside_zone": {"examples": "oops"},
            "cover_3": {"examples": [{"file": "2023091000_104.json"}]},
        },
    )
    assert get(c, plays_url(week=4))["library"] is True
    inside = get(c, diagram_url("2026_04_KC_DEN", 10))["diagram"]
    assert inside["concept"] == "inside_zone"
    assert inside["real_example"] == {
        "concept": "inside_zone",
        "name": "Inside zone run",
        "match": "a run inside the tackles",
        "count": 3,
    }
    assert inside["play"]["concept"] == "inside_zone"
    for pid in (60, 70, 20):  # screen (no usable name), play_action_deep (not an entry), none
        assert get(c, diagram_url("2026_04_KC_DEN", pid))["diagram"]["real_example"] is None


@pytest.mark.parametrize(
    "index", ['{"concepts": []}', "[]", "not json", '{"nope": {}}', '{"concepts": null}']
)
def test_a_broken_index_means_no_library(tmp_path, index):
    paths = make_paths(tmp_path)
    F.make_world(paths)
    d = paths.playcalling / "bdb_library"
    d.mkdir()
    (d / "index.json").write_text(index, encoding="utf-8")
    c = make_client(tmp_path)
    assert get(c, plays_url(week=4))["library"] is False
    assert get(c, diagram_url("2026_04_KC_DEN", 10))["diagram"]["real_example"] is None


# ---- the rosters: positions from curated players -------------------------------------------------


def write_players(paths, mapping: dict[str, str]) -> None:
    pl.DataFrame(
        {
            "gsis_id": list(mapping),
            "position_group": list(mapping.values()),
            "extra": ["x"] * len(mapping),
        }
    ).write_parquet(paths.curated / "players.parquet")


def test_the_rosters_tell_the_diagram_who_was_targeted_and_who_carried(tmp_path):
    paths = make_paths(tmp_path)
    F.make_world(paths)
    # play 120 (KC's rpo pass) targets 00-WR-KC; play 10 (a run) is carried by 00-RB-KC
    c = make_client(tmp_path)
    plain = get(c, diagram_url("2026_04_KC_DEN", 120))["diagram"]
    assert not any(k.startswith("The target was") for k in plain["known"])
    write_players(paths, {"00-WR-KC": "RB", "00-RB-KC": "WR", "nobody": "QB"})
    d = get(c, diagram_url("2026_04_KC_DEN", 120))["diagram"]
    assert "The target was a running back (play-by-play, rosters)" in d["known"]
    assert next(p for p in d["players"] if p["target"])["role"] == "back"
    run = get(c, diagram_url("2026_04_KC_DEN", 10))["diagram"]
    assert "The ball carrier was a wide receiver (play-by-play, rosters)" in run["known"]
    assert next(p for p in run["players"] if p["carrier"])["role"] == "receiver"
    check_diagram(d)
    check_diagram(run)


def test_a_broken_players_table_is_no_positions_not_an_error(tmp_path):
    paths = make_paths(tmp_path)
    F.make_world(paths)
    (paths.curated / "players.parquet").write_bytes(b"not a parquet file")
    c = make_client(tmp_path)
    d = get(c, diagram_url("2026_04_KC_DEN", 120))["diagram"]
    assert not any(k.startswith("The target was") for k in d["known"])
    pl.DataFrame({"gsis_id": ["a"], "other": ["RB"]}).write_parquet(
        paths.curated / "players.parquet"
    )
    d = get(c, diagram_url("2026_04_KC_DEN", 120))["diagram"]  # no position_group column
    assert not any(k.startswith("The target was") for k in d["known"])


# ---- text that reaches the browser ---------------------------------------------------------------


def leaky_world(tmp_path):
    root = tmp_path.as_posix()
    desc = (
        f"(8:00) 10-I.Pacheco left guard to KC 31 for 6 yards. api_key=sk-test123 more "
        f"Authorization: Bearer abcdef0123456789 tail see {root}/secret.parquet and "
        f"{str(tmp_path)}\\raw\\x.csv end"
    )
    paths = make_paths(tmp_path)
    leak = F.X.run_row(play_id=9.0, order_sequence=9.0, desc=desc, down=1)
    long_one = F.X.make_row(play_id=8.0, order_sequence=8.0, down=2, desc="(9:00) " + "x" * 600)
    F.make_world(paths, extra_kc_w4=[leak, long_one])
    return paths


def test_no_secret_or_path_shaped_text_reaches_the_browser(tmp_path, monkeypatch):
    from nflengine import settings as S

    sentinels = {
        "WANDB_API_KEY": "wandb_v1_SENTINELwandbSENTINELwandb",
        "NEO4J_PASSWORD": "SENTINEL-neo4j-pw-123",
        "OPENROUTER_API_KEY": "sk-or-v1-SENTINELSENTINELSENTINEL",
    }
    for k, v in sentinels.items():
        monkeypatch.setenv(k, v)
    S.get_env.cache_clear()
    try:
        leaky_world(tmp_path)
        c = make_client(tmp_path)
        urls = [plays_url(week=4), plays_url(week=4, type="run"), plays_url(side="defense")]
        urls += [diagram_url("2026_04_KC_DEN", pid) for pid in (9, 8, 10)]
        seen_mask = False
        for url in urls:
            body = c.get(url).text
            for v in sentinels.values():
                assert v not in body, url
            for bad in (
                "sk-test123",
                "abcdef0123456789",
                str(tmp_path),
                str(tmp_path).replace("\\", "\\\\"),
                tmp_path.as_posix(),
            ):
                assert bad not in body, (url, bad)
            assert scrub(body, limit=10**9) == body, f"{url} has text that looks like a secret"
            seen_mask |= "api_key=***" in body
        assert seen_mask, "the planted text was masked, not dropped"
    finally:
        S.get_env.cache_clear()


def test_a_long_description_is_capped_with_an_ellipsis(tmp_path):
    leaky_world(tmp_path)
    c = make_client(tmp_path)
    lst = get(c, plays_url(week=4))
    long_list = next(p for p in lst["plays"] if p["play_id"] == 8)["desc"]
    long_diag = get(c, diagram_url("2026_04_KC_DEN", 8))["diagram"]["play"]["desc"]
    for text in (long_list, long_diag):
        assert len(text) == 300 and text.endswith("\u2026") and text.startswith("x" * 50)


def test_the_cleaned_description_has_no_clock_prefix(tree):
    for p in listing(tree, week=4)["plays"]:
        assert p["desc"] is None or not re.match(r"^\(\s*\d{0,2}:\d{2}\s*\)", p["desc"])
    assert diagram(tree, "2026_04_KC_DEN", 10)["play"]["desc"].startswith("10-I.Pacheco")


# ---- safety: nothing written, short filtered reads, no NaN ---------------------------------------


def all_urls() -> list[str]:
    urls = [
        plays_url(t, side=s) for t in ("KC", "DEN", "LV", "NYJ") for s in ("offense", "defense")
    ]
    urls += [plays_url("KC", week=w) for w in (1, 2, 3, 4, 5, 6, 19, 22)]
    urls += [
        plays_url("KC", week=4, down=d, type=k, result=r)
        for d in (2, 3)
        for k in F.TYPES
        for r in (F.RESULTS[0],)
    ]
    urls += [plays_url("KC", season=s) for s in (2025, 2024, 2023, 2030)]
    urls += [
        plays_url("XYZ"),
        plays_url("kc"),
        plays_url("KC", type="bogus"),
        plays_url("KC", down=9),
    ]
    urls += [diagram_url("2026_04_KC_DEN", s.pid) for s in F.W4]
    urls += [diagram_url("2026_06_KC_ZZZ", 63), diagram_url("2025_01_KC_DEN", 2)]
    urls += [
        diagram_url("2026_09_KC_LV", 1),
        diagram_url("bad", 1),
        diagram_url("2026_04_KC_DEN", 0),
    ]
    urls += ["/api/playcalling/concepts/inside_zone", "/api/playcalling/concepts/nope"]
    return urls


def file_tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        if p.is_file()
        else "dir"
        for p in root.rglob("*")
    }


def test_no_endpoint_writes_anything_under_the_data_root(tree):
    before = file_tree(tree.root)
    for url in all_urls():
        tree.client.get(url)
    assert file_tree(tree.root) == before


def test_every_read_is_short_filtered_and_never_memory_mapped(tmp_path, monkeypatch):
    """D100: no memory map on a file the Tuesday build replaces, and only filtered reads of the
    enriched plays (a team's weeks, a week, a game) and of the players table (positions)."""
    import polars as pl_mod
    import pyarrow.parquet as pq_mod

    paths = make_paths(tmp_path)
    F.make_world(paths)
    write_players(paths, {"00-WR-KC": "WR", "00-RB-KC": "RB"})
    c = make_client(tmp_path)
    table_reads: list[tuple[str, dict]] = []
    schema_reads: list[tuple[str, dict]] = []
    frame_reads: list[tuple[str, dict]] = []
    real_table, real_schema = pq_mod.read_table, pq_mod.read_schema
    real_frame = pl_mod.read_parquet

    def table_spy(path, *a, **kw):
        table_reads.append((Path(str(path)).name, kw))
        return real_table(path, *a, **kw)

    def schema_spy(path, *a, **kw):
        schema_reads.append((Path(str(path)).name, kw))
        return real_schema(path, *a, **kw)

    def frame_spy(source, *a, **kw):
        frame_reads.append((Path(str(source)).name, kw))
        return real_frame(source, *a, **kw)

    monkeypatch.setattr(pq_mod, "read_table", table_spy)
    monkeypatch.setattr(pq_mod, "read_schema", schema_spy)
    monkeypatch.setattr(pl_mod, "read_parquet", frame_spy)
    monkeypatch.setattr(pl_mod, "scan_parquet", lambda *a, **k: pytest.fail("a lazy scan"))
    for url in (
        plays_url(),
        plays_url(week=4, down=3),
        plays_url(side="defense"),
        diagram_url("2026_04_KC_DEN", 120),
        diagram_url("2026_04_KC_DEN", 10),
    ):
        get(c, url)
    names = {n for n, _ in table_reads}
    assert names == {"plays_enriched.parquet", "players.parquet"}, table_reads
    assert all(kw.get("memory_map") is False for _, kw in table_reads), table_reads
    assert all(kw.get("filters") for _, kw in table_reads), "a whole table is never read"
    assert all(kw.get("memory_map") in (None, False) for _, kw in schema_reads)
    assert not [n for n, _ in frame_reads if n in ("plays_enriched.parquet", "players.parquet")]
    positions = [kw for n, kw in table_reads if n == "players.parquet"]
    assert positions and all(kw["columns"] == ["gsis_id", "position_group"] for kw in positions)
    assert all(kw["filters"][0][1] == "in" for kw in positions)
    list_reads = [kw for n, kw in table_reads if n == "plays_enriched.parquet"]
    assert any(kw.get("columns") and len(kw["columns"]) < 10 for kw in list_reads)  # the weeks read


def test_no_answer_carries_a_nan_or_an_infinity(tmp_path):
    paths = make_paths(tmp_path)
    nan_play = F.simple(7, epa=float("nan"), yards_gained=float("nan"), air_yards=float("inf"))
    rows = F.rows_in(F.G4, "KC", "DEN", False, [nan_play, F.simple(8, epa=float("-inf"))])
    F.write_plays(paths, 2026, rows)
    c = make_client(tmp_path)

    def boom(constant: str):
        raise AssertionError(f"{constant} in a JSON answer")

    for url in (
        plays_url(week=4),
        diagram_url("2026_04_KC_DEN", 7),
        diagram_url("2026_04_KC_DEN", 8),
    ):
        text = c.get(url).text
        json.loads(text, parse_constant=boom)
        assert not re.search(r"\b(NaN|-?Infinity)\b", text), url
    p = get(c, plays_url(week=4))["plays"][0]
    assert p["epa"] is None and p["result"]["yards"] is None


def test_a_build_can_replace_the_tables_while_the_app_is_open(tmp_path):
    """D100: the Tuesday build replaces files with a rename; on Windows that fails if the app
    still has a handle or a memory map on the file."""
    paths = make_paths(tmp_path)
    F.make_world(paths)
    c = make_client(tmp_path)
    get(c, plays_url(week=4))
    get(c, diagram_url("2026_04_KC_DEN", 10))
    d = paths.playcalling / "2026"
    tmp = d / "plays_enriched.parquet.tmp"
    F.frame(F.world_rows_2026()).write_parquet(tmp)
    tmp.replace(d / "plays_enriched.parquet")
    assert get(c, plays_url(week=4))["status"] == "ok"
    assert get(c, diagram_url("2026_04_KC_DEN", 10))["status"] == "ok"


def test_a_half_written_plays_file_is_no_data_not_a_500(tmp_path):
    paths = make_paths(tmp_path)
    F.make_world(paths)
    (paths.playcalling / "2026" / "plays_enriched.parquet").write_bytes(b"PAR1 not really")
    c = make_client(tmp_path)
    assert get(c, plays_url())["status"] == "no_plays"
    get(c, diagram_url("2026_04_KC_DEN", 10), 404)


def test_a_plays_file_without_the_optional_columns_still_lists(tmp_path):
    paths = make_paths(tmp_path)
    rows = F.rows_in(F.G4, "KC", "DEN", False, [F.simple(1), F.simple(2, 2)])
    d = paths.playcalling / "2026"
    d.mkdir(parents=True)
    keep = ["game_id", "play_id", "season", "week", "season_type", "order_sequence", "offense",
            "defense", "home", "down", "distance", "yards_to_goal", "pass_attempt", "dropback",
            "yards_gained", "desc"]  # fmt: skip
    F.frame(rows).select(keep).write_parquet(d / "plays_enriched.parquet")
    (d / "build.json").write_text(json.dumps(F.build_doc(2026, 5)), encoding="utf-8")
    b = get(make_client(tmp_path), plays_url(week=4))
    assert b["status"] == "ok" and ids(b) == [1, 2] and b["plays"][0]["type"] == "pass"


def test_a_missing_data_drive_is_the_standard_503(tmp_path):
    def gone():
        raise DataRootError("Data drive D:\\ is not connected (NFL_DATA_ROOT=D:/nfl-ml-data).")

    c = make_client(tmp_path, data_root=gone)
    for url in (plays_url(), diagram_url("2026_04_KC_DEN", 10)):
        r = c.get(url)
        assert (r.status_code, r.json()["error"]["code"]) == (503, "data_root_missing"), url


def test_the_existing_play_calling_endpoints_still_answer_beside_the_plays(tree):
    assert get(tree.client, "/api/playcalling/teams")["status"] in ("ok", "not_built")
    assert tree.client.get("/api/playcalling/teams/KC/plays/extra").status_code == 404
    assert tree.client.get("/api/playcalling/plays/2026_04_KC_DEN/10").status_code == 404


# ---- every text of an answer is cleaned, not only the description (Sol, PC03) --------------------

POISON = {
    "coverage": "api_key=sk-cov111 more",
    "target_route": "password=hunter222 y",
    "pass_length": "token=tok333abc z",
}
SECRETS = ("sk-cov111", "hunter222", "tok333abc", "abcdef0123456789")


def poisoned_tree(tmp_path):
    """KC's week-4 play 5: a participation row (coverage and route are used) whose free-text
    columns carry secret-shaped text, and a description with the data root and a bearer token."""
    paths = make_paths(tmp_path)
    desc = (
        f"(8:00) 15-P.Mahomes pass short left to 87-T.Kelce. Authorization: Bearer "
        f"abcdef0123456789 tail {tmp_path.as_posix()}/secret.parquet"
    )
    row = F.simple(
        5, 1, part_charted=True, personnel="11", extra_ol=False, pass_location="left",
        desc=desc, **POISON,
    )  # fmt: skip
    F.write_plays(paths, 2026, F.rows_in(F.G4, "KC", "DEN", False, [row]))
    return paths, make_client(tmp_path)


def test_the_known_lines_of_a_diagram_are_cleaned_too(tmp_path):
    paths, c = poisoned_tree(tmp_path)
    r = c.get(diagram_url("2026_04_KC_DEN", 5))
    assert r.status_code == 200
    body = r.text
    for secret in (*SECRETS, str(tmp_path), tmp_path.as_posix()):
        assert secret not in body, secret
    assert scrub(body, limit=10**9) == body
    d = r.json()["diagram"]
    # the lines that quote the file's values are there, masked rather than dropped
    known = " | ".join(d["known"])
    assert "api_key=***" in known and "password=***" in known and "token=***" in known
    assert any(k.startswith("Coverage: api_key=***") for k in d["known"])
    assert any(k.startswith("The throw:") and "token=***" in k for k in d["known"])
    assert "***" in d["play"]["desc"] and "abcdef" not in d["play"]["desc"]
    check_diagram(d)


def test_every_row_of_the_play_list_is_cleaned_too(tmp_path):
    paths, c = poisoned_tree(tmp_path)
    r = c.get(plays_url(week=4))
    body = r.text
    for secret in (*SECRETS, str(tmp_path), tmp_path.as_posix()):
        assert secret not in body, secret
    assert scrub(body, limit=10**9) == body
    (play,) = r.json()["plays"]
    assert "abcdef" not in play["desc"] and "secret.parquet" in play["desc"]  # path made relative


def test_no_text_of_an_answer_is_cut_by_the_cleaning(tree):
    """`clean()` caps a text at 300 characters; none of a diagram's own lines comes near it."""
    for s in F.W4:
        d = diagram(tree, "2026_04_KC_DEN", s.pid)
        for text in strings_of(d):
            assert len(text) < 300 and not text.endswith("…"), (s.pid, text)


def test_a_very_long_free_text_in_a_known_line_is_capped_not_dropped(tmp_path):
    paths = make_paths(tmp_path)
    row = F.simple(
        5, 1, part_charted=True, personnel="11", extra_ol=False, coverage="z" * 900
    )  # fmt: skip
    F.write_plays(paths, 2026, F.rows_in(F.G4, "KC", "DEN", False, [row]))
    d = get(make_client(tmp_path), diagram_url("2026_04_KC_DEN", 5))["diagram"]
    (line,) = [k for k in d["known"] if k.startswith("Coverage: zzz")]
    assert len(line) == 300 and line.endswith("…")


def strings_of(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings_of(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings_of(v)


def test_cleaning_changes_nothing_in_a_clean_diagram(tree):
    """The app's answer equals the pure function's, key by key (only `play` gains the game label
    and the season, and `real_example` is added)."""
    for s in F.W4:
        got = diagram(tree, "2026_04_KC_DEN", s.pid)
        row = {**s.row, "game_id": "2026_04_KC_DEN", "offense": "KC", "defense": "DEN"}
        want = json.loads(json.dumps(D.diagram(row, [])))
        for key in want:
            if key == "play":
                continue
            assert got[key] == want[key], (s.pid, key)
        assert got["real_example"] is None


# ---- the contract with web/src/api/types.ts ------------------------------------------------------

TYPES_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "api" / "types.ts"
TOKEN3 = re.compile(r"\s*('[^']*'|[A-Za-z_]\w*|\[\]|\[|\]|\d+|[{}<>|;,:()?=&])")


def pc03_source() -> str:
    """The PC03 block of types.ts (to the end, or the next phase's block) without comments."""
    text = TYPES_TS.read_text(encoding="utf-8")
    start = text.index("// ---- PC03:")
    nxt = re.search(r"^// ---- PC0[4-9]", text[start + 10 :], re.M)
    block = text[start : start + 10 + nxt.start()] if nxt else text[start:]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    return re.sub(r"//[^\n]*", "", block)


class Parser(T.TypeParser):
    """PC01's small TypeScript reader plus tuples (`[number, number, number]`)."""

    def __init__(self, src: str):
        self.toks = TOKEN3.findall(src)
        self.i = 0

    def primary(self):
        if self.peek() == "[":
            self.take("[")
            items = [self.union()]
            while self.peek() == ",":
                self.take()
                items.append(self.union())
            self.take("]")
            return ("tuple", items)
        return super().primary()


class Contract(T.Contract):
    def __init__(self, src: str):
        self.types: dict[str, Any] = {}
        self.ifaces: dict[str, tuple[list[str], dict[str, tuple[Any, bool]]]] = {}
        p = Parser(src)
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

    def check(self, value, node, where, problems, seen):
        if node[0] != "tuple":
            return super().check(value, node, where, problems, seen)
        if not isinstance(value, list):
            problems.append(f"{where}: expected a list, got {type(value).__name__}")
            return
        if len(value) != len(node[1]):
            problems.append(f"{where}: {len(value)} items for a {len(node[1])}-tuple")
            return
        for i, (item, sub) in enumerate(zip(value, node[1], strict=True)):
            self.check(item, sub, f"{where}[{i}]", problems, seen)


@pytest.fixture(scope="module")
def contract() -> Contract:
    return Contract(T.pc01_source() + "\n" + pc03_source())


def test_the_ts_parser_sees_the_play_browser_types(contract):
    assert {
        "PlaycallPlaysResponse", "PlaySummary", "PlayResult", "PlayDiagram", "DiagramPlayer",
        "DiagramMark", "DiagramPhase", "DiagramField", "PlayDiagramResponse", "RealClipResponse",
        "RealExampleRef", "PlaycallPlaysWeek",
    } <= set(contract.ifaces)  # fmt: skip
    assert (
        contract.types["DiagramKey"] == ("tuple", [("name", "number")] * 3)
        and contract.types["DiagramBallKey"][0] == "tuple"
    )
    assert contract.literals(contract.types["PlayType"]) == {
        "run", "pass", "sack", "scramble", "aborted", "dropback",
    }  # fmt: skip
    assert contract.literals(contract.types["PlayConcept"]) == set(D.CONCEPTS)
    assert contract.fields_of("PlaySummary")["epa"][0] == (
        "union",
        [("name", "number"), ("name", "null")],
    )
    assert set(contract.fields_of("PlaySummary")) == SUMMARY_KEYS
    assert set(contract.fields_of("PlayResult")) == {
        "kind", "yards", "first_down", "touchdown", "turnover", "fumble_lost", "text",
    }  # fmt: skip
    assert set(contract.fields_of("DiagramPlayer")) == {
        "id", "side", "role", "label", "approx", "rusher", "target", "carrier", "track",
    }  # fmt: skip
    assert set(contract.fields_of("DiagramField")) == {
        "los", "first_down", "goal", "ball_y", "hash", "width", "view",
    }  # fmt: skip


def contract_answers(tmp_path: Path) -> list[tuple[str, str, Any]]:
    out: list[tuple[str, str, Any]] = []
    paths = make_paths(tmp_path / "full")
    F.make_world(paths)
    F.write_library(paths, {"inside_zone": {"examples": [{"file": "2022091100_1.json"}]}})
    write_players(paths, {"00-WR-KC": "TE"})
    c = make_client(tmp_path / "full")

    def add(iface: str, url: str, client: TestClient = c):
        out.append((iface, url, get(client, url)))

    for q in (
        {},
        {"week": 4},
        {"week": 4, "down": 3, "type": "pass", "result": "success"},
        {"side": "defense"},
        {"week": 2},
        {"season": 2024},
        {"season": 2025},
        {"week": 6},
    ):
        add("PlaycallPlaysResponse", plays_url("KC", **q))
    add("PlaycallPlaysResponse", plays_url("LV"))
    add("PlaycallPlaysResponse", plays_url("DEN", side="defense"))
    add("PlaycallPlaysResponse", plays_url("NYJ"))
    empty = make_client(tmp_path / "empty")
    add("PlaycallPlaysResponse", plays_url("KC"), empty)
    for pid in [s.pid for s in F.W4]:
        add("PlayDiagramResponse", diagram_url("2026_04_KC_DEN", pid))
    for gid, pid in (
        ("2026_06_KC_ZZZ", 61),
        ("2026_06_KC_ZZZ", 62),
        ("2026_06_KC_ZZZ", 63),
        ("2026_01_KC_LV", 1),
        ("2025_01_KC_DEN", 1),
        ("2025_01_KC_DEN", 2),
        ("2026_04_KC_DEN", 1001),
    ):
        add("PlayDiagramResponse", diagram_url(gid, pid))
    return out


def test_every_answer_matches_the_typescript_types(contract, tmp_path):
    seen: dict = defaultdict(set)
    problems: list[str] = []
    answers = contract_answers(tmp_path)
    for iface, url, body in answers:
        problems += T.check_answer(contract, body, iface, url, seen)
    assert not problems, "\n".join(problems[:30])
    # every field of every type an answer used was non-null at least once
    owners = {o for o, _ in seen}
    for owner in owners:
        fields = (
            contract.fields_of(owner) if owner in contract.ifaces else T.seen_fields(owner, seen)
        )
        for field in fields:
            assert "value" in seen.get((owner, field), set()), f"{owner}.{field} was never non-null"
    # and the nullable ones were null somewhere too
    for owner, field in (
        ("PlaycallPlaysResponse", "message"),
        ("PlaycallPlaysResponse", "week"),
        ("PlaycallPlaysResponse", "game"),
        ("PlaycallPlaysWeek", "opponent"),
        ("PlaycallPlaysWeek", "home"),
        ("PlaySummary", "epa"),
        ("PlaySummary", "clock"),
        ("PlaySummary", "down"),
        ("PlaySummary", "spot"),
        ("PlaySummary", "concept"),
        ("PlaySummary", "desc"),
        ("PlayResult", "yards"),
        ("DiagramField", "first_down"),
        ("DiagramField", "hash"),
        ("DiagramMark", "text"),
        ("PlayDiagram", "concept"),
        ("PlayDiagram", "real_example"),
    ):
        assert "null" in seen.get((owner, field), set()), f"{owner}.{field} was never null"
    declared = set(re.findall(r"export interface (\w+)", pc03_source()))
    used = {o for o, _ in seen} | {i for i, _, _ in answers}
    unused = (
        declared - used - {"RealClipResponse"}
    )  # the real clip's types: test_app_playbrowser_clip
    assert not unused, f"types.ts declares {sorted(unused)} but no answer uses them"


def test_the_contract_check_catches_a_drifting_answer(contract, tree):
    body = get(tree.client, diagram_url("2026_04_KC_DEN", 70))
    seen: dict = defaultdict(set)
    assert T.check_answer(contract, body, "PlayDiagramResponse", "ok", seen) == []
    bad = json.loads(json.dumps(body))
    bad["diagram"]["surprise"] = 1
    del bad["diagram"]["duration"]
    bad["diagram"]["kind"] = "tracked"
    bad["diagram"]["players"][0]["track"][0] = [0.0, 1.0]
    bad["diagram"]["players"][1]["side"] = "both"
    bad["diagram"]["ball"][0] = [0.0, 1.0, 2.0]
    bad["diagram"]["field"]["hash"] = "up"
    bad["diagram"]["field"]["view"] = [1.0]
    bad["diagram"]["marks"][0]["points"] = [[1.0, 2.0, 3.0]]
    bad["diagram"]["play"]["result"]["yards"] = "far"
    bad["diagram"]["play"]["game_label"] = 5
    text = "\n".join(T.check_answer(contract, bad, "PlayDiagramResponse", "bad", seen))
    for needle in (
        "field 'surprise' is not in PlayDiagram",
        "PlayDiagram.duration is missing",
        "'tracked'",
        "track[0]: 2 items for a 3-tuple",
        "'both'",
        "ball[0]: 3 items for a 4-tuple",
        "'up'",
        "view: 1 items for a 2-tuple",
        "points[0]: 3 items for a 2-tuple",
        "'far' is not a number",
        "game_label: 5",
    ):
        assert needle in text, needle
    lst = listing(tree, week=4)
    lst["counts"]["shown"] = "x"
    lst["plays"][0]["type"] = "kick"
    lst["status"] = "maybe"
    del lst["library"]
    text = "\n".join(T.check_answer(contract, lst, "PlaycallPlaysResponse", "list", seen))
    for needle in ("'x'", "'kick'", "'maybe'", "PlaycallPlaysResponse.library is missing"):
        assert needle in text, needle
