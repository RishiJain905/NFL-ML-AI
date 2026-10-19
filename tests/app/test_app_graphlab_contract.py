"""The AE03 endpoints against `web/src/api/types.ts` (the Connections and Graph lab block): every
answer the routes give is checked field by field against the TypeScript interface the page reads
(a missing field, an extra one, a wrong type, a null where the type says no). The reader is the
small TypeScript parser of `test_app_playcalling.py` (PC01's contract test), which this file
extends with `unknown`. The answers are real: the readers over files written by the fixtures, and
`connections._assemble` for the chain, so a change to the evidence's fields shows up here."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
import test_app_playcalling as T
from app_helpers import make_client, make_paths
from graphlab_fixtures import (
    BENCH_DOC,
    KELCE,
    MAHOMES,
    NOBODY,
    Factory,
    FakeCheck,
    write_benchmark,
    write_everything,
    write_history,
)

from nflengine.app.connections_service import ConnectionsService
from nflengine.app.readers import graphlab as G
from nflengine.history_graph import connections as C
from nflengine.history_graph import schema as S

TYPES_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "api" / "types.ts"


def ae03_source() -> str:
    """The AE03 block of types.ts (up to PC01's header) without comments."""
    text = TYPES_TS.read_text(encoding="utf-8")
    m = re.search(r"^// -{10,} AE03: Connections and the Graph lab", text, re.M)
    assert m, "no AE03 block in web/src/api/types.ts (the UI worker adds it)"
    end = re.search(r"^// ---- PC0[1-9]", text[m.end() :], re.M)
    block = text[m.start() : m.end() + end.start()] if end else text[m.start() :]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    return re.sub(r"//[^\n]*", "", block)


class Contract(T.Contract):
    """... and `unknown` (any JSON value)."""

    def check(self, value, node, where, problems, seen):
        if node == ("name", "unknown"):
            return None
        return super().check(value, node, where, problems, seen)


@pytest.fixture(scope="module")
def contract() -> Contract:
    return Contract(ae03_source())


def check_answer(contract: Contract, body: Any, iface: str, where: str, seen: dict) -> list[str]:
    problems: list[str] = []
    contract.check(body, ("name", iface), where, problems, seen)
    return problems


# ---- the parser sees the block ---------------------------------------------------------------


def test_the_ts_parser_sees_the_ae03_types(contract):
    assert {
        "PersonLinksAsOf", "LabAsOf", "GraphLabStatus", "PersonMatch", "PeopleResponse",
        "ConnectionPerson", "ConnectionStint", "ConnectionLink", "ConnectionPath",
        "ConnectionsResponse", "NeighbourRow", "NeighboursResponse", "MapPoint",
        "NeighboursMapResponse", "Family", "FamilyMember", "FamiliesResponse", "ChemistryDuo",
        "PageRankRow", "ChemistryResponse", "BenchmarkRow", "BenchmarkSize", "BenchmarkResponse",
    } <= set(contract.ifaces)  # fmt: skip
    assert contract.literals(contract.types["ConnectionMode"]) == {"fewest", "strongest"}
    assert contract.literals(contract.types["GraphLabPersonKind"]) == {"player", "coach"}
    assert contract.literals(contract.types["BenchmarkFamily"]) == {"pair", "source", "all"}


def test_the_link_types_in_the_ts_are_the_graphs_person_links(contract):
    lits = contract.literals(contract.types["PersonLinkType"])
    assert lits == set(S.PERSON_LINKS)


def test_the_ts_has_the_fields_the_python_sends(contract):
    assert {"derive_id", "first_season", "through_season", "season", "week", "derived_at",
            "counts", "label"} == set(contract.fields_of("PersonLinksAsOf"))  # fmt: skip
    assert {"query", "people", "links", "ready"} == set(contract.fields_of("PeopleResponse"))
    assert {"a", "b", "mode", "k", "paths", "note", "found", "ms", "asof"} == set(
        contract.fields_of("ConnectionsResponse")
    )
    assert {"id", "kind", "name", "position", "team", "seasons", "links"} == set(
        contract.fields_of("PersonMatch")
    )


# ---- the answers -----------------------------------------------------------------------------


def real_chain(kind_a="player", kind_b="player") -> dict[str, Any]:
    """A two-path answer with every kind of link, built by `connections._assemble`."""

    def person(el, kind, pid, name, pos):
        return {"element": el, "kind": kind, "id": pid, "name": name, "position": pos}

    p0 = person("e0", "player", MAHOMES, "Patrick Mahomes", "QB")
    p1 = person("e1", "player", KELCE, "Travis Kelce", "TE")
    c0 = person("e2", "coach", "andy-reid", "Andy Reid", "coach")
    c1 = person("e3", "coach", "bill-belichick", "Bill Belichick", "coach")
    tm = {"type": "TEAMMATE", "start": "e0", "props": {
        "snaps": 8372, "seasons": [2018, 2025], "teams": ["KC"], "stint_seasons": [2018, 2025],
        "stint_teams": ["KC", "KC"], "stint_snaps": [1164, 7208]}}  # fmt: skip
    coached = {"type": "COACHED", "start": "e2", "props": {
        "snaps": 4000, "seasons": [2018], "teams": ["KC"], "roles": ["HC"],
        "stint_seasons": [2018], "stint_teams": ["KC"], "stint_roles": ["HC/OC"],
        "stint_snaps": [4000]}}  # fmt: skip
    worked = {"type": "WORKED_WITH", "start": "e2", "props": {
        "snaps": 2000, "games": 34, "seasons": [2018], "teams": ["KC"], "stint_seasons": [2018],
        "stint_teams": ["KC"], "stint_roles": ["HC|OC"]}}  # fmt: skip
    return {
        "a": {"kind": kind_a, "id": MAHOMES, "name": "Patrick Mahomes", "position": "QB",
              "links": 40},
        "b": {"kind": kind_b, "id": KELCE, "name": "Travis Kelce", "position": "TE", "links": 90},
        "mode": "strongest", "k": 2, "note": None, "found": True, "ms": 37,
        "paths": [C._assemble([p0, p1], [tm]),
                  C._assemble([p0, c0, c1], [{**coached, "start": "e2"}, {**worked,
                                                                          "start": "e2"}])],
    }  # fmt: skip


class Fake:
    def __init__(self, result):
        self.result = result

    def __call__(self, driver, a, b, **kw):
        return self.result


def client_for(tmp_path, result=None):
    svc = ConnectionsService(
        check=FakeCheck("up"), driver_factory=Factory(), connect=Fake(result or real_chain())
    )
    return make_client(tmp_path, connections=svc), make_paths(tmp_path)


@pytest.fixture
def seen():
    return defaultdict(set)


A, B = f"player:{MAHOMES}", f"player:{KELCE}"


def test_every_answer_fits_its_interface(tmp_path, contract):
    G._cache.clear()
    client, paths = client_for(tmp_path)
    write_everything(paths)
    seen: dict = defaultdict(set)
    answers = {
        "GraphLabStatus": "/api/ask/graph-lab",
        "PeopleResponse": "/api/ask/people?q=Patrick%20Mahomes",
        "ConnectionsResponse": f"/api/ask/connections?a={A}&b={B}&k=2&mode=strongest",
        "NeighboursResponse": f"/api/ask/lab/neighbours?player={A}",
        "NeighboursMapResponse": "/api/ask/lab/neighbours/map",
        "FamiliesResponse": "/api/ask/lab/families?family=1",
        "ChemistryResponse": "/api/ask/lab/chemistry",
        "BenchmarkResponse": "/api/ask/lab/benchmark",
    }
    for iface, path in answers.items():
        r = client.get(path)
        assert r.status_code == 200, (path, r.text)
        problems = check_answer(contract, r.json(), iface, path, seen)
        assert problems == [], problems
    # the owners the answers reached: every interface the block declares is used
    owners = {o for o, _ in seen}
    declared = set(contract.ifaces)
    assert declared - owners == set(), f"types.ts declares {sorted(declared - owners)}: unused"
    G._cache.clear()


def test_the_other_shapes_of_each_answer_fit_too(tmp_path, contract):
    G._cache.clear()
    client, paths = client_for(tmp_path)
    seen: dict = defaultdict(set)

    def ok(path, iface):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text)
        assert check_answer(contract, r.json(), iface, path, seen) == [], path

    # nothing built: the status and the not-ready search
    ok("/api/ask/graph-lab", "GraphLabStatus")
    write_history(paths, people=False)
    ok("/api/ask/people?q=mahomes", "PeopleResponse")
    write_everything(paths)
    ok("/api/ask/graph-lab", "GraphLabStatus")
    # a search with a coach and a nobody, an empty result, a player not in the map
    ok("/api/ask/people?q=Andy%20Reid", "PeopleResponse")
    ok("/api/ask/people?q=zzzzzz", "PeopleResponse")
    ok("/api/ask/lab/neighbours?player=player:00-0099999", "NeighboursResponse")
    # families: the list, by person, a person in none
    ok("/api/ask/lab/families", "FamiliesResponse")
    ok("/api/ask/lab/families?person=coach:andy-reid", "FamiliesResponse")
    ok(f"/api/ask/lab/families?person=player:{NOBODY}", "FamiliesResponse")
    ok(f"/api/ask/lab/chemistry?qb={MAHOMES}&min_targets=300", "ChemistryResponse")
    # a benchmark with nothing but the required fields
    write_benchmark(paths, {k: v for k, v in BENCH_DOC.items() if k in (
        "derive_id", "seed", "sizes", "machine")})  # fmt: skip
    G._cache.clear()
    ok("/api/ask/lab/benchmark", "BenchmarkResponse")
    # no chain found
    res = real_chain()
    res.update(found=False, paths=[], note="no chain of person links joins them")
    client2, paths2 = client_for(tmp_path / "second", res)
    write_everything(paths2)
    r = client2.get(f"/api/ask/connections?a={A}&b={B}")
    assert check_answer(contract, r.json(), "ConnectionsResponse", "no chain", seen) == []
    G._cache.clear()


def test_a_connection_answer_carries_every_optional_link_field(tmp_path, contract):
    G._cache.clear()
    client, paths = client_for(tmp_path)
    write_everything(paths)
    body = client.get(f"/api/ask/connections?a={A}&b={B}&k=2&mode=strongest").json()
    links = [lk for p in body["paths"] for lk in p["links"]]
    assert {lk["type"] for lk in links} == {"TEAMMATE", "COACHED", "WORKED_WITH"}
    by = {lk["type"]: lk for lk in links}
    assert "roles" in by["COACHED"] and "games" in by["WORKED_WITH"] and "best" in by["TEAMMATE"]
    seen: dict = defaultdict(set)
    assert check_answer(contract, body, "ConnectionsResponse", "x", seen) == []
    for field in ("best", "roles", "games"):
        assert ("ConnectionLink", field) in seen, field
    assert ("ConnectionPerson", "links") in seen and ("ConnectionPerson", "team") in seen
    G._cache.clear()


# ---- the check itself catches a drifting answer ----------------------------------------------


def drift_target(tmp_path):
    G._cache.clear()
    client, paths = client_for(tmp_path)
    write_everything(paths)
    return client


def test_the_contract_check_catches_a_drifting_answer(tmp_path, contract):
    client = drift_target(tmp_path)
    body = client.get(f"/api/ask/connections?a={A}&b={B}").json()
    seen: dict = defaultdict(set)
    assert check_answer(contract, body, "ConnectionsResponse", "ok", seen) == []
    bad = json.loads(json.dumps(body))
    bad["surprise"] = 1  # a field the type doesn't have
    del bad["asof"]  # a field it needs
    bad["found"] = "yes"  # a wrong type
    bad["paths"][0]["links"][0]["type"] = None  # a null the type forbids
    bad["a"]["kind"] = "team"  # a value outside the literal union
    problems = check_answer(contract, bad, "ConnectionsResponse", "bad", seen)
    text = "\n".join(problems)
    assert "surprise" in text and "asof" in text and "'yes'" in text
    assert "kind" in text and "type" in text
    G._cache.clear()


def test_the_check_catches_a_renamed_field_in_the_status_and_the_lab(tmp_path, contract):
    client = drift_target(tmp_path)
    seen: dict = defaultdict(set)
    for path, iface, key in (
        ("/api/ask/graph-lab", "GraphLabStatus", "history_state"),
        ("/api/ask/lab/chemistry", "ChemistryResponse", "min_targets"),
        ("/api/ask/lab/families?family=1", "FamiliesResponse", "person_found"),
        (f"/api/ask/lab/neighbours?player={A}", "NeighboursResponse", "found"),
        ("/api/ask/lab/benchmark", "BenchmarkResponse", "sizes"),
    ):
        body = client.get(path).json()
        assert check_answer(contract, body, iface, path, seen) == []
        body[key + "_x"] = body.pop(key)
        problems = check_answer(contract, body, iface, path, seen)
        assert any(key in p for p in problems), (iface, problems)
    G._cache.clear()
