"""Connections' Cypher against a scripted driver (AE03): which queries `connect` sends, how they
are bound, what the answers become. No Neo4j: `history_gds_fakes.ScriptedDriver` records every
query and answers from a table. The live pairs (Mahomes - Kelce ...) are in
`test_history_connections_live.py`."""

from __future__ import annotations

import re
import threading
import time

import pytest
from history_fakes import no_real_docker  # noqa: F401  (the safety net)
from history_gds_fakes import ScriptedDriver

from nflengine.history_graph import connections as C
from nflengine.history_graph import schema as S

MAHOMES = C.parse_person("player:00-0033873")
KELCE = C.parse_person("player:00-0030506")
BRADY = C.parse_person("player:00-0019596")
REID = C.parse_person("coach:andy-reid")
BELICHICK = C.parse_person("coach:bill-belichick")
EXISTS = "RETURN a.name AS name"
COUNT = "RETURN count(p) AS n"
RANKED = "ORDER BY length(p), cost"
PLAIN = "RETURN [n IN nodes(p)"


def person(el, kind, pid, name, pos="QB"):
    return {"element": el, "kind": kind, "id": pid, "name": name, "position": pos}


def tm(start, snaps=612, season=2019, team="KC"):
    return {"type": "TEAMMATE", "start": start, "props": {
        "snaps": snaps, "seasons": [season], "teams": [team], "stint_seasons": [season],
        "stint_teams": [team], "stint_snaps": [snaps]}}  # fmt: skip


def chain(*ids, snaps=612):
    """A chain of players p0 - p1 - ...: `{people, links}` as the ranked query returns it."""
    people = [person(f"e{i}", "player", pid, f"Player {pid}") for i, pid in enumerate(ids)]
    links = [tm(f"e{i}", snaps) for i in range(len(ids) - 1)]
    return {"people": people, "links": links}


def info(name, links=10, pos="QB"):
    return [{"name": name, "position": pos, "links": links}]


def driver_for(chains, count=None, a="Patrick Mahomes", b="Travis Kelce", links=(10, 10)):
    """A driver that knows two people and returns `chains` for the ranked query."""
    d = ScriptedDriver()
    d.on(EXISTS, lambda p: info(a if p["a"].endswith("873") else b, links[p["a"] != "00-0033873"]))
    d.on(COUNT, [{"n": len(chains) if count is None else count}])
    d.on(RANKED, chains)
    return d


def all_text(d: ScriptedDriver) -> str:
    return "\n".join(q for q, _ in d.calls)


class TestConnectRefusals:
    def test_a_bad_mode_is_refused_before_any_query(self):
        d = ScriptedDriver()
        with pytest.raises(C.ConnectionError_) as e:
            C.connect(d, MAHOMES, KELCE, mode="cheapest")
        assert e.value.code == "bad_mode" and d.calls == []

    def test_the_same_person_twice_is_refused_before_any_query(self):
        d = ScriptedDriver()
        with pytest.raises(C.ConnectionError_) as e:
            C.connect(d, MAHOMES, C.parse_person("player:00-0033873"))
        assert e.value.code == "same_person" and d.calls == []

    def test_the_mode_is_checked_before_the_people(self):
        with pytest.raises(C.ConnectionError_) as e:
            C.connect(ScriptedDriver(), MAHOMES, MAHOMES, mode="nope")
        assert e.value.code == "bad_mode"

    def test_a_person_the_graph_lacks_is_not_found(self):
        d = ScriptedDriver().on(EXISTS, lambda p: [] if p["a"] == "00-0030506" else info("P"))
        with pytest.raises(C.ConnectionError_) as e:
            C.connect(d, MAHOMES, KELCE)
        assert e.value.code == "not_found"
        assert str(e.value) == "player:00-0030506 isn't in the history graph"
        assert d.queries(RANKED) == [] and d.queries("SHORTEST") == []  # no search was made

    def test_the_first_missing_person_is_named(self):
        d = ScriptedDriver().on(EXISTS, [])
        with pytest.raises(C.ConnectionError_) as e:
            C.connect(d, MAHOMES, KELCE)
        assert "player:00-0033873" in str(e.value)

    def test_strongest_needs_a_derive(self):
        d = driver_for([chain("00-0033873", "00-0030506")])
        with pytest.raises(C.ConnectionError_) as e:
            C.connect(d, MAHOMES, KELCE, mode="strongest")
        assert e.value.code == "no_derive"
        assert d.queries("gds.") == []  # nothing was projected


class TestConnectAnswers:
    def test_one_link_between_teammates(self):
        d = driver_for([chain("00-0033873", "00-0030506", snaps=8372)])
        res = C.connect(d, MAHOMES, KELCE)
        assert set(res) == {"a", "b", "mode", "k", "paths", "found", "ms", "note"}
        assert res["found"] is True and res["note"] is None and res["mode"] == "fewest"
        assert res["k"] == 1 and isinstance(res["ms"], int) and res["ms"] >= 0
        assert res["a"] == {"kind": "player", "id": "00-0033873", "name": "Patrick Mahomes",
                            "position": "QB", "links": 10}  # fmt: skip
        assert res["b"]["id"] == "00-0030506" and res["b"]["name"] == "Travis Kelce"
        (path,) = res["paths"]
        assert path["hops"] == 1 and path["weakest_snaps"] == 8372
        assert [p["id"] for p in path["people"]] == ["00-0033873", "00-0030506"]
        assert path["links"][0]["text"] == "teammates on 2019 KC, 8,372 snaps together"

    def test_a_person_with_no_links_gets_a_note_and_no_search(self):
        d = driver_for([], links=(10, 0))
        res = C.connect(d, MAHOMES, KELCE)
        assert res["found"] is False and res["paths"] == []
        assert res["note"].startswith("Travis Kelce has no person links yet")
        assert "participation, 2018-2025" in res["note"]
        assert d.queries("SHORTEST") == []

    def test_two_people_with_no_links_are_named_together(self):
        d = driver_for([], links=(0, 0))
        res = C.connect(d, MAHOMES, KELCE)
        assert res["note"].startswith("Patrick Mahomes and Travis Kelce have no person links")

    def test_no_chain_says_so(self):
        d = driver_for([], count=0)
        res = C.connect(d, MAHOMES, KELCE)
        assert res["found"] is False and res["paths"] == []
        assert res["note"] == "no chain of person links joins them"

    def test_k_is_clamped_and_reported(self):
        d = driver_for([chain("00-0033873", "00-0030506")])
        assert C.connect(d, MAHOMES, KELCE, k=99)["k"] == 5
        assert C.connect(d, MAHOMES, KELCE, k=0)["k"] == 1
        assert C.connect(d, MAHOMES, KELCE, k=-3)["k"] == 1
        assert C.connect(d, MAHOMES, KELCE, k="3")["k"] == 3

    def test_a_coach_and_a_player(self):
        d = ScriptedDriver()
        d.on(EXISTS, lambda p: info("Andy Reid", 50, "coach") if p["a"] == "andy-reid"
             else info("Patrick Mahomes"))  # fmt: skip
        d.on(COUNT, [{"n": 1}])
        people = [person("ec", "coach", "andy-reid", "Andy Reid", "coach"),
                  person("ep", "player", "00-0033873", "Patrick Mahomes")]  # fmt: skip
        link = {"type": "COACHED", "start": "ec", "props": {
            "snaps": 4000, "stint_seasons": [2018], "stint_teams": ["KC"],
            "stint_roles": ["HC"], "stint_snaps": [4000]}}  # fmt: skip
        d.on(RANKED, [{"people": people, "links": [link]}])
        res = C.connect(d, REID, MAHOMES)
        assert res["a"]["kind"] == "coach" and res["a"]["position"] == "coach"
        assert res["paths"][0]["links"][0]["text"].startswith("Andy Reid coached Patrick Mahomes")
        # the Cypher: a Coach and a Player, each by its own key and label
        q = d.queries(COUNT)[0]
        assert "MATCH (a:Coach {coach_id: $a}) MATCH (b:Player {player_id: $b})" in q


class TestFewestCypher:
    def run(self, k=1, **kw):
        d = driver_for([chain("00-0033873", "00-0030506")], **kw)
        C.connect(d, MAHOMES, KELCE, k=k)
        return d

    def test_every_query_is_a_read_with_a_server_side_timeout(self):
        d = self.run()
        assert d.timeouts and all(t == C.TIMEOUT_S == 8.0 for t in d.timeouts)
        assert len(d.timeouts) == len(d.calls)  # every one through `execute_read`

    def test_ids_are_bound_parameters_never_text(self):
        d = self.run()
        text = all_text(d)
        assert "00-0033873" not in text and "00-0030506" not in text
        for q, params in d.calls:
            if "SHORTEST" in q:
                assert params == {"a": "00-0033873", "b": "00-0030506"} and "$a" in q and "$b" in q

    def test_the_two_ends_are_matched_by_label_and_key(self):
        d = self.run()
        q = d.queries(COUNT)[0]
        assert q.startswith("MATCH (a:Player {player_id: $a}) MATCH (b:Player {player_id: $b})")

    def test_the_walk_uses_only_the_person_links(self):
        d = self.run(k=4)
        text = all_text(d)
        types = set()
        for m in re.finditer(r"\[[a-z]*:([A-Za-z_|]+)\]", text):
            types |= set(m.group(1).split("|"))
        assert types == set(S.PERSON_LINKS) == {"TEAMMATE", "COACHED", "WORKED_WITH"}
        labels = set(re.findall(r"\((?:[a-z]+)?:([A-Za-z]+)[ )]", text))
        assert labels <= {"Player", "Coach"}
        # never through a Team, a Season, a Game, a Drive or a Play
        assert not re.search(r"\b(Team|Season|Game|Drive|Play|PLAYED_FOR|OFFENSE|ON_FIELD)\b", text)
        assert not re.search(r"\b(HEAD_COACH|OFF_COORD|IN_SEASON|PASSED|TARGETED)\b", text)

    def test_the_first_look_counts_the_shortest_chains_up_to_the_rank_limit(self):
        d = self.run()
        (count,) = d.queries(COUNT)
        assert "SHORTEST 1 GROUPS" in count and "-[:TEAMMATE|COACHED|WORKED_WITH]-+" in count
        assert f"LIMIT {C.RANK_LIMIT + 1}" in count and C.RANK_LIMIT == 2000

    def test_few_chains_are_ranked_shortest_then_strongest(self):
        d = driver_for([chain("00-0033873", "00-0030506")] * 3)
        C.connect(d, MAHOMES, KELCE, k=3)
        (ranked,) = d.queries(RANKED)
        assert "ORDER BY length(p), cost LIMIT 3" in ranked
        assert "1.0 / CASE WHEN coalesce(r.snaps, 0) > 0 THEN r.snaps ELSE 1 END" in ranked
        assert "WITH p, reduce(c = 0.0, r IN relationships(p)" in ranked

    def test_too_many_chains_fall_back_to_neo4js_own_k_shortest(self):
        d = driver_for([], count=C.RANK_LIMIT + 1)
        d.on("MATCH p = SHORTEST 2 (a)", [chain("00-0033873", "00-0030506")])
        res = C.connect(d, MAHOMES, KELCE, k=2)
        assert res["found"] is True
        (plain,) = d.queries("SHORTEST 2 (a)")
        assert "ORDER BY" not in plain and "LIMIT" not in plain
        assert d.queries(RANKED) == []  # nothing was ranked

    def test_exactly_the_rank_limit_is_still_ranked(self):
        d = driver_for([chain("00-0033873", "00-0030506")], count=C.RANK_LIMIT)
        C.connect(d, MAHOMES, KELCE)
        assert len(d.queries(RANKED)) == 1

    def test_too_few_in_the_shortest_group_adds_the_next_length(self):
        # k = 4 but only two chains have the shortest length: the second look (2 GROUPS) ranks
        # up to 4 chains of the two shortest lengths
        calls = []

        def ranked(params):
            calls.append(1)
            return [chain("00-0033873", "00-0030506")] * (2 if len(calls) == 1 else 4)

        d = ScriptedDriver()
        d.on(EXISTS, lambda p: info("P"))
        d.on(COUNT, [{"n": 2}])
        d.on(RANKED, ranked)
        res = C.connect(d, MAHOMES, KELCE, k=4)
        assert len(res["paths"]) == 4
        counts = d.queries(COUNT)
        assert "SHORTEST 1 GROUPS" in counts[0] and "SHORTEST 2 GROUPS" in counts[1]
        assert "SHORTEST 2 GROUPS" in d.queries(RANKED)[1]

    def test_enough_in_the_shortest_group_stops_there(self):
        d = driver_for([chain("00-0033873", "00-0030506")] * 3)
        C.connect(d, MAHOMES, KELCE, k=3)
        assert len(d.queries(COUNT)) == 1 and not d.queries("SHORTEST 2 GROUPS")

    def test_two_groups_that_still_come_up_short_return_what_there_is(self):
        d = driver_for([chain("00-0033873", "00-0030506")] * 2, count=2)
        res = C.connect(d, MAHOMES, KELCE, k=5)
        assert len(res["paths"]) == 2
        assert len(d.queries(RANKED)) == 2  # one per group

    def test_a_count_of_nothing_means_no_chain(self):
        d = driver_for([], count=0)
        assert C.fewest(d, MAHOMES, KELCE) == []
        assert d.queries(RANKED) == []
        d2 = ScriptedDriver().on(COUNT, [])
        assert C.fewest(d2, MAHOMES, KELCE) == []

    def test_a_multi_hop_chain_is_assembled_in_order(self):
        d = driver_for([chain("00-0019596", "00-0001111", "00-0033873", snaps=300)])
        (path,) = C.fewest(d, BRADY, MAHOMES)
        assert path["hops"] == 2
        assert [p["id"] for p in path["people"]] == ["00-0019596", "00-0001111", "00-0033873"]
        assert [(x["from"], x["to"]) for x in path["links"]] == [(0, 1), (1, 2)]
        assert path["weakest_snaps"] == 300 and path["cost"] == round(2 / 300, 6)

    def test_a_driver_error_propagates_untouched(self):
        d = ScriptedDriver().on(COUNT, RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            C.fewest(d, MAHOMES, KELCE)


class TestProjection:
    EXISTS_Q = "CALL gds.graph.exists"
    LIST_Q = "CALL gds.graph.list()"
    DROP_Q = "CALL gds.graph.drop"
    PROJECT_Q = "gds.graph.project"

    def driver(self, exists=False, listed=()):
        d = ScriptedDriver()
        d.on(self.EXISTS_Q, [{"exists": exists}])
        d.on(self.LIST_Q, [{"graphName": g} for g in listed])
        d.on(self.DROP_Q, [{"graphName": "x"}])
        d.on(self.PROJECT_Q, [{"nodes": 7000, "rels": 240000}])
        return d

    def test_the_projection_is_named_after_the_derive(self):
        assert C.projection_name("20261019T120100Z") == "persons-20261019T120100Z"

    def test_an_existing_projection_is_reused_without_a_query(self):
        d = self.driver(exists=True, listed=["persons-OLD"])
        out = C.ensure_projection(d, "D1")
        assert out == {"name": "persons-D1", "made": False}
        assert d.queries(self.PROJECT_Q) == [] and d.queries(self.DROP_Q) == []
        assert d.params_of(self.EXISTS_Q) == [{"n": "persons-D1"}]

    def test_a_new_one_is_made_and_older_persons_projections_are_dropped_nothing_else(self):
        d = self.driver(listed=["persons-OLD1", "lab-neighbours-D0", "persons-OLD2", "mine",
                                "personsX", "Persons-old"])  # fmt: skip
        out = C.ensure_projection(d, "D1")
        assert out["made"] is True and out["name"] == "persons-D1"
        assert (out["nodes"], out["rels"]) == (7000, 240000) and out["seconds"] >= 0
        dropped = [p["n"] for p in d.params_of(self.DROP_Q)]
        assert dropped == ["persons-OLD1", "persons-OLD2"]  # exactly those: not a lab graph
        # and with `failIfMissing = false`
        assert all("gds.graph.drop($n, false)" in q for q in d.queries(self.DROP_Q))

    def test_the_current_projection_is_never_dropped(self):
        # another process made persons-D1 between the exists check and the listing: it is in the
        # list, and it isn't ours to drop (two processes share one server)
        d = self.driver(listed=["persons-D1", "persons-OLD", "persons-D10"])
        C.ensure_projection(d, "D1")
        dropped = [p["n"] for p in d.params_of(self.DROP_Q)]
        assert dropped == ["persons-OLD", "persons-D10"]  # a longer id is another derive's
        assert "persons-D1" not in dropped

    def test_a_projection_another_process_just_made_counts_as_made_by_then(self):
        # the project call fails ("already exists") but the graph is there now: made = False
        state = {"made": False}
        d = ScriptedDriver()
        d.on(self.EXISTS_Q, lambda p: [{"exists": state["made"]}])
        d.on(self.LIST_Q, [])

        def project(p):
            state["made"] = True  # the other process finished first
            return RuntimeError("A graph with name `persons-D1` already exists")

        d.on(self.PROJECT_Q, project)
        assert C.ensure_projection(d, "D1") == {"name": "persons-D1", "made": False}
        assert len(d.queries(self.PROJECT_Q)) == 1

    def test_a_projection_that_failed_and_isnt_there_raises_the_real_error(self):
        d = self.driver()
        d.rules.insert(0, (self.PROJECT_Q, RuntimeError("out of memory")))
        with pytest.raises(RuntimeError, match="out of memory"):
            C.ensure_projection(d, "D1")
        assert len(d.params_of(self.EXISTS_Q)) == 2  # before, and the look after the failure

    def test_the_projection_query_names_its_labels(self):
        # an unlabelled MATCH scanned 374k plays (36 s): the labels are the point
        d = self.driver()
        C.ensure_projection(d, "D1")
        (q,) = d.queries(self.PROJECT_Q)
        assert q.startswith("MATCH (a:Player|Coach)-[r:TEAMMATE|COACHED|WORKED_WITH]->(b) ")
        assert "MATCH (a)-[" not in q and "MATCH (a)--" not in q
        assert "1.0 / toFloat(CASE WHEN coalesce(r.snaps, 0) > 0 THEN r.snaps ELSE 1 END)" in q
        assert "undirectedRelationshipTypes: ['*']" in q
        assert d.params_of(self.PROJECT_Q) == [{"n": "persons-D1"}]

    def test_the_drops_come_before_the_projection(self):
        d = self.driver(listed=["persons-OLD"])
        C.ensure_projection(d, "D1")
        order = [("drop" if "drop" in q else "project" if "project" in q else "other")
                 for q, _ in d.calls if "drop" in q or "project" in q]  # fmt: skip
        assert order == ["drop", "project"]

    def test_two_threads_make_it_once(self):
        made = {"n": 0}
        d = ScriptedDriver()
        d.on(self.EXISTS_Q, lambda p: [{"exists": made["n"] > 0}])
        d.on(self.LIST_Q, [])

        def project(p):
            time.sleep(0.2)  # long enough for the other thread to be waiting on the lock
            made["n"] += 1
            return [{"nodes": 1, "rels": 2}]

        d.on(self.PROJECT_Q, project)
        outs = []
        ts = [threading.Thread(target=lambda: outs.append(C.ensure_projection(d, "D1")))
              for _ in range(3)]  # fmt: skip
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        assert made["n"] == 1 and len(d.queries(self.PROJECT_Q)) == 1
        assert sorted(o["made"] for o in outs) == [False, False, True]


class TestStrongest:
    PATH_Q = "CALL gds.shortestPath"

    def driver(self, paths, links=None, people=None):
        d = ScriptedDriver()
        d.on("CALL gds.graph.exists", [{"exists": True}])
        d.on(EXISTS, lambda p: info("P"))
        d.on(self.PATH_Q, paths)
        if links is None:
            links = [{"links": [tm("ea", 8372)], "first": None, "rest": None}]
        d.on("collect({type: type(r)", links)
        d.on("RETURN i, {element: elementId(n)", people or [
            {"i": 0, "p": person("ea", "player", "00-0033873", "Patrick Mahomes")},
            {"i": 1, "p": person("eb", "player", "00-0030506", "Travis Kelce", "TE")}])  # fmt: skip
        return d

    def test_one_chain_uses_dijkstra_on_the_cost_property(self):
        d = self.driver([{"els": ["ea", "eb"]}])
        res = C.connect(d, MAHOMES, KELCE, mode="strongest", derive_id="D1")
        (call,) = d.queries(self.PATH_Q)
        assert "gds.shortestPath.dijkstra.stream($g, {sourceNode: a, targetNode: b" in call
        assert "relationshipWeightProperty: 'cost'" in call and "yens" not in call
        assert d.params_of(self.PATH_Q)[0] == {"a": "00-0033873", "b": "00-0030506",
                                               "g": "persons-D1", "k": 1}  # fmt: skip
        assert res["mode"] == "strongest" and res["found"] is True
        (path,) = res["paths"]
        assert path["hops"] == 1 and [p["name"] for p in path["people"]] == [
            "Patrick Mahomes", "Travis Kelce"]  # fmt: skip
        assert path["links"][0]["text"] == "teammates on 2019 KC, 8,372 snaps together"

    def test_several_chains_use_yens_with_k(self):
        d = self.driver([{"els": ["ea", "eb"]}, {"els": ["ea", "eb"]}])
        res = C.connect(d, MAHOMES, KELCE, mode="strongest", k=4, derive_id="D1")
        (call,) = d.queries(self.PATH_Q)
        assert "gds.shortestPath.yens.stream($g, {sourceNode: a, targetNode: b, k: $k" in call
        assert "dijkstra" not in call and d.params_of(self.PATH_Q)[0]["k"] == 4
        assert len(res["paths"]) == 2

    def test_k_is_clamped_to_five(self):
        d = self.driver([])
        C.connect(d, MAHOMES, KELCE, mode="strongest", k=50, derive_id="D1")
        assert d.params_of(self.PATH_Q)[0]["k"] == 5

    def test_no_chain_is_found_false_with_a_note(self):
        d = self.driver([])
        res = C.connect(d, MAHOMES, KELCE, mode="strongest", derive_id="D1")
        assert res["found"] is False and res["paths"] == []
        assert res["note"] == "no chain of person links joins them"

    def test_the_projection_is_the_one_of_this_derive(self):
        d = self.driver([])
        C.connect(d, MAHOMES, KELCE, mode="strongest", derive_id="D7")
        assert d.params_of("CALL gds.graph.exists") == [{"n": "persons-D7"}]

    def test_each_hop_shows_its_strongest_link_and_every_query_is_labelled_and_typed(self):
        d = self.driver([{"els": ["ea", "eb"]}])
        C.connect(d, MAHOMES, KELCE, mode="strongest", derive_id="D1")
        (hop,) = d.queries("collect({type: type(r)")
        assert "ORDER BY i, coalesce(r.snaps, 0) DESC" in hop and "head(collect(r))" in hop
        assert "-[r:TEAMMATE|COACHED|WORKED_WITH]-" in hop
        text = all_text(d)
        assert not re.search(r"\b(Team|Season|Game|Drive|Play)\b", text)

    def test_a_chain_whose_links_cant_be_found_is_skipped(self):
        d = self.driver([{"els": ["ea", "eb"]}], links=[])
        res = C.connect(d, MAHOMES, KELCE, mode="strongest", derive_id="D1")
        assert res["paths"] == [] and res["found"] is False

    def test_every_query_has_the_timeout(self):
        d = self.driver([{"els": ["ea", "eb"]}])
        C.connect(d, MAHOMES, KELCE, mode="strongest", derive_id="D1")
        assert d.timeouts and all(t == 8.0 for t in d.timeouts)

    def test_ids_are_never_in_the_text(self):
        d = self.driver([{"els": ["ea", "eb"]}])
        C.connect(d, MAHOMES, KELCE, mode="strongest", derive_id="D1")
        assert "00-0033873" not in all_text(d) and "00-0030506" not in all_text(d)


class TestStrongestRetry:
    """The projection can vanish under a search (another process's newer derive, a restart): one
    retry when it is gone, none when it is still there."""

    PATH_Q = "CALL gds.shortestPath"

    def driver(self, exists_answers, path_answers):
        exists = list(exists_answers)
        paths = list(path_answers)
        d = ScriptedDriver()
        d.on("CALL gds.graph.exists", lambda p: [{"exists": exists.pop(0) if exists else True}])
        d.on("CALL gds.graph.list()", [])
        d.on("gds.graph.project", [{"nodes": 5, "rels": 9}])
        d.on(EXISTS, lambda p: info("P"))

        def path(p):
            r = paths.pop(0)
            return r

        d.on(self.PATH_Q, path)
        d.on("collect({type: type(r)", [{"links": [tm("ea", 8372)], "first": None, "rest": None}])
        d.on("RETURN i, {element: elementId(n)", [
            {"i": 0, "p": person("ea", "player", "00-0033873", "Patrick Mahomes")},
            {"i": 1, "p": person("eb", "player", "00-0030506", "Travis Kelce", "TE")}])  # fmt: skip
        return d

    def test_a_vanished_projection_is_made_again_and_the_search_runs_again(self):
        # exists: no (make it) / no (the failed search: it's gone) / no (make it again)
        d = self.driver(
            [False, False, False], [RuntimeError("graph not found"), [{"els": ["ea", "eb"]}]]
        )
        res = C.connect(d, MAHOMES, KELCE, mode="strongest", derive_id="D1")
        assert res["found"] is True and len(res["paths"]) == 1
        assert len(d.queries(self.PATH_Q)) == 2  # tried, then tried again
        assert len(d.queries("gds.graph.project")) == 2  # made twice

    def test_a_projection_that_is_still_there_is_not_retried(self):
        # exists: no (make it) / yes (after the failure it is there): the error is the real one
        d = self.driver([False, True], [RuntimeError("the search itself failed")])
        with pytest.raises(RuntimeError, match="the search itself failed"):
            C.connect(d, MAHOMES, KELCE, mode="strongest", derive_id="D1")
        assert len(d.queries(self.PATH_Q)) == 1 and len(d.queries("gds.graph.project")) == 1

    def test_a_second_failure_is_raised(self):
        d = self.driver(
            [False, False, False, False], [RuntimeError("gone 1"), RuntimeError("gone 2")]
        )
        with pytest.raises(RuntimeError, match="gone 2"):
            C.connect(d, MAHOMES, KELCE, mode="strongest", derive_id="D1")
        assert len(d.queries(self.PATH_Q)) == 2  # one retry, no more

    def test_a_search_that_works_the_first_time_asks_once(self):
        d = self.driver([True], [[{"els": ["ea", "eb"]}]])
        C.connect(d, MAHOMES, KELCE, mode="strongest", derive_id="D1")
        assert len(d.queries(self.PATH_Q)) == 1 and d.queries("gds.graph.project") == []
