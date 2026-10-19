"""Connections against the real history graph (AE03). Written by the test worker, run by the lead:

    uv run pytest -m integration tests/history_graph/test_history_connections_live.py

Needs the history container up on Bolt 7688, a finished build and a finished derive; each test is
skipped (not failed) when one is missing or while a build / append / derive runs. Read-only: no
test here runs a derive, a build, an append or a lab job, and every query is a read. The one
thing a "strongest" search makes is GDS's in-memory person projection (`persons-<derive id>`,
the GDS catalog, not the database: the first search of a derive takes a few seconds to half a
minute on a cold disk and is excluded from the timings).

What is checked:
- Mahomes - Kelce is one TEAMMATE link (8,372 snaps, 2018-2025); Brady - Mahomes is two links;
  Reid - Belichick (coaches) is two links;
- every node on every chain is a Player or a Coach (never a Team, Season, Game, Drive or Play),
  and every link is one of the person links;
- `connect` answers in under a second warm on the full graph (fewest and strongest, k = 1 and 4);
- the strongest k = 4 search gives four distinct chains;
- the derived counts in the graph equal `derive.json`'s.
"""

from __future__ import annotations

import re
import time
from typing import Any

import pytest

from nflengine.history_graph import connections as C
from nflengine.history_graph import schema as S
from nflengine.history_graph import state
from nflengine.history_graph.derive import read_derive

pytestmark = pytest.mark.integration

MAHOMES, KELCE, BRADY = "00-0033873", "00-0030506", "00-0019596"
WRITE = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP|LOAD|FOREACH)\b", re.I)
PAIRS = [
    (f"player:{MAHOMES}", f"player:{KELCE}"),
    (f"player:{BRADY}", f"player:{MAHOMES}"),
    ("coach:andy-reid", "coach:bill-belichick"),
    (f"player:{MAHOMES}", "coach:bill-belichick"),
]


def ref(text: str) -> C.PersonRef:
    return C.parse_person(text)


@pytest.fixture(scope="module")
def driver():
    from nflengine.history_graph.client import get_history_driver, history_up

    if not history_up():
        pytest.skip("the history graph isn't up on Bolt 7688 (`uv run nfl history-graph start`)")
    drv = get_history_driver()
    yield drv
    drv.close()


@pytest.fixture(scope="module")
def paths():
    from nflengine.paths import ensure_data_root

    return ensure_data_root(create_dirs=False)


@pytest.fixture(scope="module")
def derive(paths, driver) -> dict[str, Any]:
    if state.history_busy(paths):
        pytest.skip("a history-graph build, append or derive is running (or didn't finish)")
    doc = read_derive(paths)
    if not doc:
        pytest.skip("no finished derive onto the current build (`uv run nfl history-graph derive`)")
    return doc


def q(driver, query: str, **params) -> list[dict[str, Any]]:
    assert not WRITE.search(query), f"not a read-only query: {query}"
    with driver.session() as s:
        return [r.data() for r in s.run(query, **params)]


def connect(driver, derive, a, b, mode="fewest", k=1):
    return C.connect(driver, ref(a), ref(b), mode=mode, k=k, derive_id=derive["derive_id"])


@pytest.fixture(scope="module")
def warm(driver, derive):
    """One cold strongest search (makes GDS's projection) so the timings below are warm."""
    connect(driver, derive, *PAIRS[0], mode="strongest")
    return True


# ---- the known pairs -------------------------------------------------------------------------


class TestKnownPairs:
    def test_mahomes_and_kelce_are_one_teammate_link(self, driver, derive):
        res = connect(driver, derive, *PAIRS[0])
        assert res["found"] is True and res["a"]["name"] == "Patrick Mahomes"
        assert res["b"]["name"] == "Travis Kelce"
        path = res["paths"][0]
        assert path["hops"] == 1 and len(path["links"]) == 1
        link = path["links"][0]
        assert link["type"] == "TEAMMATE" and link["snaps"] == 8372
        assert (min(link["seasons"]), max(link["seasons"])) == (2018, 2025)
        assert link["teams"] == ["KC"]
        assert sum(s["snaps"] for s in link["stints"]) == 8372
        assert [p["id"] for p in path["people"]] == [MAHOMES, KELCE]
        assert link["text"].startswith("teammates on ") and "8,372 in all" in link["text"]

    def test_brady_and_mahomes_are_two_links(self, driver, derive):
        res = connect(driver, derive, *PAIRS[1])
        assert res["found"] is True
        assert {p["hops"] for p in res["paths"]} == {2}
        mid = res["paths"][0]["people"][1]
        assert mid["kind"] in ("player", "coach") and mid["id"] not in (BRADY, MAHOMES)

    def test_reid_and_belichick_are_two_links(self, driver, derive):
        res = connect(driver, derive, *PAIRS[2])
        assert res["found"] is True and res["a"]["kind"] == "coach"
        assert {p["hops"] for p in res["paths"]} == {2}

    def test_a_player_and_a_coach_he_never_played_for(self, driver, derive):
        res = connect(driver, derive, *PAIRS[3])
        assert res["found"] is True and res["paths"][0]["hops"] >= 2

    def test_the_same_person_and_a_stranger_are_refused(self, driver, derive):
        with pytest.raises(C.ConnectionError_) as e:
            connect(driver, derive, PAIRS[0][0], PAIRS[0][0])
        assert e.value.code == "same_person"
        with pytest.raises(C.ConnectionError_) as e:
            connect(driver, derive, PAIRS[0][0], "player:00-0000001")
        assert e.value.code == "not_found"

    def test_the_answer_is_the_same_twice(self, driver, derive):
        one = connect(driver, derive, *PAIRS[1])["paths"][0]
        two = connect(driver, derive, *PAIRS[1])["paths"][0]
        assert [p["id"] for p in one["people"]] == [p["id"] for p in two["people"]]


# ---- persons only ----------------------------------------------------------------------------


class TestPersonsOnly:
    @pytest.mark.parametrize("mode", ["fewest", "strongest"])
    @pytest.mark.parametrize("pair", PAIRS)
    def test_every_node_is_a_player_or_a_coach_and_every_link_a_person_link(
        self, driver, derive, pair, mode
    ):
        res = connect(driver, derive, *pair, mode=mode, k=4)
        assert res["found"] is True
        players, coaches = set(), set()
        for path in res["paths"]:
            assert len(path["people"]) == path["hops"] + 1
            for p in path["people"]:
                assert p["kind"] in ("player", "coach"), p
                (players if p["kind"] == "player" else coaches).add(p["id"])
            for link in path["links"]:
                assert link["type"] in S.PERSON_LINKS, link["type"]
        # and the database agrees: each id is a Player or a Coach node, nothing else
        got = q(
            driver,
            "MATCH (n) WHERE (n:Player AND n.player_id IN $p) OR (n:Coach AND n.coach_id IN $c) "
            "RETURN count(n) AS n",
            p=sorted(players),
            c=sorted(coaches),
        )[0]["n"]
        assert got == len(players) + len(coaches)

    def test_the_search_never_walks_through_a_team_season_game_drive_or_play(self, driver, derive):
        # a shortest chain between two players on one team is the direct link, not "via the
        # team" (two steps): the hub the README warns about is not in the walk
        res = connect(driver, derive, *PAIRS[0], k=4)
        assert res["paths"][0]["hops"] == 1
        for path in res["paths"]:
            assert all(p["kind"] in ("player", "coach") for p in path["people"])
            assert not any(
                x in str(path) for x in ("'Team'", "'Season'", "'Game'", "'Drive'", "'Play'")
            )


# ---- speed -----------------------------------------------------------------------------------


class TestSpeed:
    @pytest.mark.parametrize("mode", ["fewest", "strongest"])
    @pytest.mark.parametrize("k", [1, 4])
    @pytest.mark.parametrize("pair", PAIRS[:3])
    def test_a_warm_connect_answers_in_under_a_second(self, driver, derive, warm, pair, mode, k):
        connect(driver, derive, *pair, mode=mode, k=k)  # warm this query's own caches
        best = 9e9
        for _ in range(3):
            t = time.perf_counter()
            res = connect(driver, derive, *pair, mode=mode, k=k)
            best = min(best, time.perf_counter() - t)
        assert res["found"] is True
        assert best < 1.0, f"{pair} {mode} k={k}: {best * 1000:.0f} ms"
        assert res["ms"] < 1000


# ---- strongest k = 4 -------------------------------------------------------------------------


class TestStrongest:
    def test_four_distinct_chains(self, driver, derive, warm):
        res = connect(driver, derive, *PAIRS[1], mode="strongest", k=4)
        paths = res["paths"]
        assert len(paths) == 4
        chains = [tuple(p["id"] for p in path["people"]) for path in paths]
        assert len(set(chains)) == 4
        for path in paths:
            assert path["people"][0]["id"] == BRADY and path["people"][-1]["id"] == MAHOMES

    def test_the_strongest_chain_is_never_costlier_than_the_next(self, driver, derive, warm):
        paths = connect(driver, derive, *PAIRS[1], mode="strongest", k=4)["paths"]
        costs = [p["cost"] for p in paths]
        assert costs == sorted(costs)  # Yen's: lowest cost (1 / snaps summed) first

    def test_the_strongest_single_chain_is_the_cheapest_of_four(self, driver, derive, warm):
        one = connect(driver, derive, *PAIRS[1], mode="strongest", k=1)["paths"][0]
        four = connect(driver, derive, *PAIRS[1], mode="strongest", k=4)["paths"]
        assert one["cost"] == four[0]["cost"]

    def test_the_fewest_chain_is_never_longer_than_the_strongest(self, driver, derive, warm):
        few = connect(driver, derive, *PAIRS[1], mode="fewest")["paths"][0]
        strong = connect(driver, derive, *PAIRS[1], mode="strongest")["paths"][0]
        assert few["hops"] <= strong["hops"]

    def test_k_is_capped_at_five(self, driver, derive, warm):
        res = connect(driver, derive, *PAIRS[1], mode="fewest", k=50)
        assert res["k"] == 5 and len(res["paths"]) <= 5

    def test_the_projection_is_named_after_the_derive(self, driver, derive, warm):
        rows = q(driver, "CALL gds.graph.list() YIELD graphName RETURN graphName")
        names = {r["graphName"] for r in rows}
        assert C.projection_name(derive["derive_id"]) in names


# ---- the derived links in the graph ---------------------------------------------------------


class TestDerivedCounts:
    def test_the_counts_in_the_graph_equal_derive_json(self, driver, derive):
        from nflengine.history_graph.build import graph_counts

        got = graph_counts(driver, list(S.DERIVED))
        assert got == derive["counts"]

    def test_the_symmetric_links_are_stored_once_smaller_id_first(self, driver, derive):
        for rtype, key in (("TEAMMATE", "player_id"), ("WORKED_WITH", "coach_id")):
            bad = q(
                driver,
                f"MATCH (a)-[r:{rtype}]->(b) WHERE a.{key} >= b.{key} RETURN count(r) AS n",
            )[0]["n"]
            assert bad == 0, rtype
            dup = q(
                driver,
                f"MATCH (a)-[r:{rtype}]->(b) WITH a, b, count(r) AS n WHERE n > 1 "
                "RETURN count(*) AS n",
            )[0]["n"]
            assert dup == 0, rtype

    def test_a_link_has_stints_that_add_up(self, driver, derive):
        rows = q(
            driver,
            "MATCH (a:Player)-[r:TEAMMATE]->(b:Player) WHERE r.snaps > 3000 "
            "RETURN r.snaps AS snaps, r.stint_snaps AS stints, size(r.stint_seasons) AS ns, "
            "size(r.stint_teams) AS nt, size(r.stint_snaps) AS nn LIMIT 200",
        )
        assert rows
        for r in rows:
            assert sum(r["stints"]) == r["snaps"] and r["ns"] == r["nt"] == r["nn"]

    def test_the_known_pair_in_the_graph(self, driver, derive):
        rows = q(
            driver,
            "MATCH (a:Player {player_id: $a})-[r:TEAMMATE]-(b:Player {player_id: $b}) "
            "RETURN r.snaps AS snaps, r.first_season AS f, r.last_season AS l",
            a=MAHOMES,
            b=KELCE,
        )
        assert rows == [{"snaps": 8372, "f": 2018, "l": 2025}]
