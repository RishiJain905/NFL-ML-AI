"""The AE03 routes of the control room: `GET /api/ask/graph-lab`, `/people`, `/connections` and
`/lab/{neighbours, neighbours/map, families, chemistry, benchmark}` (status codes, parameter
validation, 404 until built, nothing echoed, every string cleaned, the data root never in a
response). The app is built the way `tests/app` builds it, with an injected `ConnectionsService`
(a fake driver and a fake `connect`); nothing reaches Neo4j, Docker or the real data root."""

from __future__ import annotations

import json
import threading

import polars as pl
import pytest
from app_helpers import BASE, TOKEN, make_client, make_paths
from ask_app_fixtures import PLANTED, assert_no_leaks
from graphlab_fixtures import (
    BRADY,
    KELCE,
    MAHOMES,
    D,
    Factory,
    FakeCheck,
    canned_connection,
    lab_dir,
    write_benchmark,
    write_chemistry,
    write_everything,
    write_history,
)

from nflengine.app.connections_service import ERRORS, ConnectionsService
from nflengine.app.readers import graphlab as G
from nflengine.history_graph import connections as C
from nflengine.history_graph.state import mark_unfinished

A, B = f"player:{MAHOMES}", f"player:{KELCE}"
BAD_REQUEST = {
    "error": {"code": "bad_request", "message": "A parameter is malformed or out of range."}
}
ARABIC = "٠٠٣٣٨٧٣"  # a GSIS id's digits in Arabic-Indic
FULL_WIDTH = "００３３８７３"


@pytest.fixture(autouse=True)
def _fresh_cache():
    G._cache.clear()
    yield
    G._cache.clear()


class Calls:
    def __init__(self):
        self.calls: list[tuple] = []
        self.error: Exception | None = None
        self.result = None

    def __call__(self, driver, a, b, **kw):
        self.calls.append((a.text(), b.text(), kw))
        if self.error:
            raise self.error
        return self.result or canned_connection(a.id, b.id, mode=kw["mode"], k=kw["k"])


class CountingService(ConnectionsService):
    warm_calls = 0

    def warm_if_up(self, paths):
        type(self).warm_calls += 1


@pytest.fixture
def app(tmp_path):
    """The app, its data root, the fake connect and the service."""
    CountingService.warm_calls = 0
    calls = Calls()
    svc = CountingService(check=FakeCheck("up"), driver_factory=Factory(), connect=calls)
    client = make_client(tmp_path, connections=svc)
    paths = make_paths(tmp_path)
    return type("App", (), {"client": client, "paths": paths, "calls": calls, "svc": svc,
                            "root": tmp_path})()  # fmt: skip


def error_of(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()
    assert body["error"]["code"] == code, body
    return body["error"]["message"]


class TestHeaders:
    @pytest.mark.parametrize(
        "path",
        ["/api/ask/graph-lab", "/api/ask/people?q=mahomes", f"/api/ask/connections?a={A}&b={B}",
         f"/api/ask/lab/neighbours?player={A}", "/api/ask/lab/neighbours/map",
         "/api/ask/lab/families", "/api/ask/lab/chemistry", "/api/ask/lab/benchmark"],
    )  # fmt: skip
    def test_every_route_is_json_no_store_and_framed_shut(self, app, path):
        write_everything(app.paths)
        r = app.client.get(path)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/json")
        assert r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"
        assert "access-control-allow-origin" not in r.headers

    @pytest.mark.parametrize(
        "path", ["/api/ask/graph-lab", "/api/ask/people?q=ab", f"/api/ask/connections?a={A}&b={B}",
                 "/api/ask/lab/families", "/api/ask/lab/benchmark"])  # fmt: skip
    def test_they_only_read(self, app, path):
        write_everything(app.paths)
        r = app.client.post(path, headers={"origin": BASE, "x-cr-token": TOKEN})
        assert r.status_code in (404, 405)
        h = {"origin": BASE, "x-cr-token": TOKEN}
        assert app.client.delete(path, headers=h).status_code in (404, 405)

    def test_a_foreign_host_is_refused(self, app):
        write_everything(app.paths)
        r = app.client.get("/api/ask/graph-lab", headers={"host": "evil.example"})
        assert r.status_code in (400, 403, 421)


class TestStatusRoute:
    def test_before_anything_is_built(self, app):
        r = app.client.get("/api/ask/graph-lab")
        assert r.status_code == 200
        b = r.json()
        assert b["history"] is None and b["links"] is None and b["benchmark"] is None
        assert b["lab"] == {"neighbours": None, "families": None, "chemistry": None}
        assert b["busy"] is False and b["unfinished"] is None and b["history_state"] == "up"

    def test_with_everything(self, app):
        write_everything(app.paths)
        b = app.client.get("/api/ask/graph-lab").json()
        assert set(b) == {"history", "busy", "unfinished", "links", "lab", "benchmark",
                          "history_state"}  # fmt: skip
        assert b["links"]["derive_id"] == D and b["history"]["label"].startswith("history graph")
        assert all(v["current"] for v in b["lab"].values())
        assert b["benchmark"]["derive_id"] == D and b["benchmark"]["ran_at"]

    def test_the_cached_bolt_state_is_reported(self, app):
        app.svc._check.value = "down"
        assert app.client.get("/api/ask/graph-lab").json()["history_state"] == "down"
        app.svc._check.value = "checking"
        assert app.client.get("/api/ask/graph-lab").json()["history_state"] == "checking"

    def test_a_busy_graph_is_said_so(self, app):
        write_everything(app.paths)
        mark_unfinished(app.paths, "derive", "B1")
        b = app.client.get("/api/ask/graph-lab").json()
        assert b["busy"] is True and b["unfinished"] == "derive"

    def test_the_status_starts_the_warm_up(self, app):
        app.client.get("/api/ask/graph-lab")
        assert CountingService.warm_calls == 1

    def test_the_status_answers_with_the_container_stopped(self, app):
        # nothing here touches a driver: the service never opened one
        write_everything(app.paths)
        app.svc._check.value = "down"
        assert app.client.get("/api/ask/graph-lab").status_code == 200
        assert app.svc._factory.drivers == []


class TestPeopleRoute:
    def test_a_search(self, app):
        write_everything(app.paths)
        b = app.client.get("/api/ask/people?q=Patrick%20Mahomes").json()
        assert b["ready"] is True and b["query"] is None
        assert [p["id"] for p in b["people"]] == [A]
        assert set(b["people"][0]) == {"id", "kind", "name", "position", "team", "seasons", "links"}
        assert b["links"]["derive_id"] == D

    def test_the_query_is_never_echoed(self, app):
        write_everything(app.paths)
        q = "sk-or-v1-PLANTEDPLANTEDPLANTED1234"
        r = app.client.get("/api/ask/people", params={"q": q})
        assert r.status_code == 200 and r.json()["query"] is None
        assert q not in r.text and "PLANTED" not in r.text

    def test_not_ready_without_an_index(self, app):
        write_history(app.paths, people=False)
        b = app.client.get("/api/ask/people?q=mahomes").json()
        assert b["ready"] is False and b["people"] == [] and b["query"] is None

    def test_the_kind_filter(self, app):
        write_everything(app.paths)
        b = app.client.get("/api/ask/people?q=Andy%20Reid&kind=coach").json()
        assert [p["id"] for p in b["people"]] == ["coach:andy-reid"]
        assert app.client.get("/api/ask/people?q=Andy%20Reid&kind=player").json()["people"] == []

    def test_the_limit(self, app):
        write_everything(app.paths)
        assert len(app.client.get("/api/ask/people?q=travis&limit=1").json()["people"]) <= 1

    @pytest.mark.parametrize(
        "query",
        ["", "q=", "q=" + "x" * 61, "q=ab&kind=team", "q=ab&kind=", "q=ab&limit=0",
         "q=ab&limit=21", "q=ab&limit=-1", "q=ab&limit=many"],
    )  # fmt: skip
    def test_bad_parameters_are_422_with_fixed_text(self, app, query):
        write_everything(app.paths)
        r = app.client.get("/api/ask/people?" + query)
        assert r.status_code == 422 and r.json() == BAD_REQUEST

    def test_sixty_characters_are_allowed(self, app):
        write_everything(app.paths)
        assert app.client.get("/api/ask/people?q=" + "x" * 60).status_code == 200

    def test_a_planted_secret_in_the_index_is_masked(self, app):
        write_history(app.paths)
        f = app.paths.neo4j_history / "people.parquet"
        df = pl.read_parquet(f).with_columns(
            pl.when(pl.col("id") == MAHOMES).then(pl.lit(PLANTED["openrouter"]))
            .otherwise(pl.col("team")).alias("team")
        )  # fmt: skip
        df.write_parquet(f)
        r = app.client.get("/api/ask/people?q=Patrick%20Mahomes")
        assert r.status_code == 200 and "PLANTED" not in r.text

    def test_the_search_starts_the_warm_up(self, app):
        write_everything(app.paths)
        app.client.get("/api/ask/people?q=travis")
        assert CountingService.warm_calls == 1


class TestConnectionsRoute:
    def get(self, app, a=A, b=B, **extra):
        params = {"a": a, "b": b, **extra}
        return app.client.get("/api/ask/connections", params=params)

    def test_an_answer(self, app):
        write_everything(app.paths)
        r = self.get(app)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is True and body["a"]["id"] == MAHOMES and body["b"]["id"] == KELCE
        assert body["paths"][0]["links"][0]["text"].startswith("teammates on 2019 KC")
        assert body["asof"]["derive_id"] == D
        assert body["a"]["team"] == "KC" and body["b"]["seasons"] == "2018-2025"

    def test_the_service_gets_the_ids_the_mode_and_k(self, app):
        write_everything(app.paths)
        self.get(app, k=4, mode="strongest")
        (a, b, kw) = app.calls.calls[0]
        assert (a, b) == (A, B) and (kw["mode"], kw["k"], kw["derive_id"]) == ("strongest", 4, D)

    def test_the_defaults(self, app):
        write_everything(app.paths)
        self.get(app)
        assert app.calls.calls[0][2]["mode"] == "fewest" and app.calls.calls[0][2]["k"] == 1

    @pytest.mark.parametrize(
        "params",
        [{"a": A}, {"b": B}, {}, {"a": "x", "b": B},
         {"a": A.upper(), "b": B}, {"a": "player:00-0033873 ", "b": B}, {"a": A, "b": "coach:"},
         {"a": "coach:" + "a" * 70, "b": B}, {"a": A, "b": "team:KC"},
         {"a": "coach:x'}) DETACH DELETE (n) //", "b": B}, {"a": A, "b": f"{B};DROP"},
         {"a": A, "b": B, "k": 0}, {"a": A, "b": B, "k": 6}, {"a": A, "b": B, "k": "x"},
         {"a": A, "b": B, "mode": "cheapest"}, {"a": A, "b": B, "mode": ""}],
    )  # fmt: skip
    def test_bad_parameters_are_422_before_the_service_is_asked(self, app, params):
        write_everything(app.paths)
        r = app.client.get("/api/ask/connections", params=params)
        assert r.status_code == 422 and r.json() == BAD_REQUEST
        assert app.calls.calls == [] and app.svc._factory.drivers == []

    @pytest.mark.parametrize("digits", [ARABIC, FULL_WIDTH])
    @pytest.mark.parametrize("side", ["a", "b"])
    def test_a_non_ascii_digit_id_is_422(self, app, digits, side):
        write_everything(app.paths)
        bad = f"player:00-{digits}"
        r = self.get(app, **{side: bad})
        assert r.status_code == 422 and app.calls.calls == []

    def test_the_two_ends_may_be_a_player_and_a_coach(self, app):
        write_everything(app.paths)
        assert self.get(app, b="coach:andy-reid").status_code == 200

    @pytest.mark.parametrize(
        ("setup", "status", "code"),
        [("no_links", 503, "no_links"), ("not_found", 404, "not_found"),
         ("busy_graph", 503, "history_busy"), ("down", 503, "history_down")],
    )  # fmt: skip
    def test_the_service_refusals_keep_their_status_and_fixed_text(self, app, setup, status, code):
        write_everything(app.paths)
        a, b = A, B
        if setup == "no_links":
            (app.paths.neo4j_history / "derive.json").unlink()
        elif setup == "not_found":
            b = "player:00-0099999"
        elif setup == "busy_graph":
            mark_unfinished(app.paths, "derive", "B1")
        elif setup == "down":
            app.svc._check.value = "down"
        msg = error_of(self.get(app, a=a, b=b), status, code)
        assert msg == ERRORS[code]
        assert app.calls.calls == []

    def test_a_pair_that_passes_the_route_but_isnt_a_person_is_422_bad_id(self, app):
        write_everything(app.paths)
        # `abc12345` matches the route's slug pattern but is no GSIS id
        msg = error_of(self.get(app, a="player:abc12345"), 422, "bad_id")
        assert msg == ERRORS["bad_id"]

    def test_the_same_person_twice_is_422(self, app):
        write_everything(app.paths)
        assert error_of(self.get(app, a=A, b=A), 422, "same_person") == ERRORS["same_person"]

    def test_the_driver_error_is_fixed_text_and_the_drivers_words_never_leave(self, app):
        write_everything(app.paths)
        app.calls.error = RuntimeError("bolt://10.0.0.5:7688 password=hunter2 MATCH (n) DELETE n")
        r = self.get(app)
        assert error_of(r, 500, "failed") == ERRORS["failed"]
        for leak in ("hunter2", "10.0.0.5", "MATCH", "RuntimeError", "Traceback"):
            assert leak not in r.text

    def test_a_timeout_is_504(self, app):
        write_everything(app.paths)
        app.calls.error = RuntimeError("the transaction timed out")
        assert error_of(self.get(app), 504, "too_slow") == ERRORS["too_slow"]

    def test_a_connection_that_died_is_503_down(self, app):
        write_everything(app.paths)
        app.calls.error = ConnectionResetError("reset by peer 10.0.0.5")
        r = self.get(app)
        assert error_of(r, 503, "history_down") == ERRORS["history_down"]
        assert "10.0.0.5" not in r.text

    def test_the_data_root_and_key_shaped_text_never_leave(self, app):
        write_everything(app.paths)
        res = canned_connection()
        res["note"] = f"look in {app.root}/neo4j-history " + PLANTED["openrouter"]
        res["paths"][0]["people"][0]["name"] = f"{app.root}/raw/x"
        app.calls.result = res
        r = self.get(app)
        assert r.status_code == 200
        assert_no_leaks(r.text, app.root, planted=[PLANTED["openrouter"]])

    def test_two_searches_at_once_over_the_cap_get_409(self, app):
        write_everything(app.paths)
        release, inside = threading.Event(), threading.Event()

        def slow(driver, a, b, **kw):
            inside.set()
            assert release.wait(10)
            return canned_connection(a.id, b.id)

        app.svc._connect = slow
        app.svc._slots = threading.BoundedSemaphore(1)
        out = {}

        t = threading.Thread(target=lambda: out.setdefault("first", self.get(app)), daemon=True)
        t.start()
        assert inside.wait(10)
        try:
            r = self.get(app)
            assert error_of(r, 409, "busy") == ERRORS["busy"]
        finally:
            release.set()
            t.join(10)
        assert out["first"].status_code == 200
        assert self.get(app).status_code == 200  # the slot came back


class TestNeighboursRoute:
    def test_404_until_the_job_has_run(self, app):
        write_history(app.paths)
        msg = error_of(app.client.get(f"/api/ask/lab/neighbours?player={A}"), 404, "not_built")
        assert "uv run nfl history-graph lab" in msg and "neighbours" in msg
        error_of(app.client.get("/api/ask/lab/neighbours/map"), 404, "not_built")

    def test_a_players_neighbours(self, app):
        write_everything(app.paths)
        b = app.client.get(f"/api/ask/lab/neighbours?player={A}").json()
        assert b["found"] is True and b["player_id"] == MAHOMES
        assert [r["rank"] for r in b["position"]] == [1, 2] and len(b["overall"]) == 2
        assert b["asof"]["current"] is True

    def test_a_player_not_in_the_map_is_found_false_not_404(self, app):
        write_everything(app.paths)
        r = app.client.get("/api/ask/lab/neighbours?player=player:00-0099999")
        assert r.status_code == 200 and r.json()["found"] is False and r.json()["player"] is None

    @pytest.mark.parametrize(
        "player",
        ["", "00-0033873", "player:00-003387", "player:00-00338731", "player:01-0033873",
         "coach:andy-reid", "player:00-0033873x", "Player:00-0033873", "player:00-0033873\nx",
         f"player:00-{ARABIC}", f"player:00-{FULL_WIDTH}"],
    )  # fmt: skip
    def test_bad_ids_are_422(self, app, player):
        write_everything(app.paths)
        r = app.client.get("/api/ask/lab/neighbours", params={"player": player})
        assert r.status_code == 422 and r.json() == BAD_REQUEST

    def test_the_player_is_required(self, app):
        write_everything(app.paths)
        assert app.client.get("/api/ask/lab/neighbours").status_code == 422

    def test_the_map(self, app):
        write_everything(app.paths)
        b = app.client.get("/api/ask/lab/neighbours/map").json()
        assert [p["player_id"] for p in b["points"]] == [MAHOMES, KELCE, BRADY]
        assert b["asof"]["derive_id"] == D


class TestFamiliesRoute:
    def test_404_until_the_job_has_run(self, app):
        write_history(app.paths)
        error_of(app.client.get("/api/ask/lab/families"), 404, "not_built")

    def test_the_list(self, app):
        write_everything(app.paths)
        b = app.client.get("/api/ask/lab/families").json()
        assert [f["community"] for f in b["families"]] == [1, 2]
        assert b["members"] is None and b["family"] is None and b["person_found"] is None

    def test_a_family_and_its_members(self, app):
        write_everything(app.paths)
        b = app.client.get("/api/ask/lab/families?family=1").json()
        assert b["family"] == 1 and [m["id"] for m in b["members"]] == [MAHOMES, KELCE, "andy-reid"]

    def test_a_person_picks_a_family(self, app):
        write_everything(app.paths)
        b = app.client.get(f"/api/ask/lab/families?person={B}").json()
        assert b["family"] == 1 and b["person_found"] is True
        c = app.client.get("/api/ask/lab/families?person=coach:andy-reid").json()
        assert c["family"] == 1

    def test_a_person_in_no_family(self, app):
        write_everything(app.paths)
        b = app.client.get("/api/ask/lab/families?person=player:00-0000099").json()
        assert b["family"] is None and b["person_found"] is False and b["members"] is None

    @pytest.mark.parametrize(
        "query",
        ["family=-1", "family=10000001", "family=x", "family=1.5", "person=x", "person=team:KC",
         "person=" + "a" * 73, f"person=player:00-{ARABIC}", f"person=player:00-{FULL_WIDTH}",
         "person=player:00-0033873;x", "person=Player:abc", "person=coach:Andy"],
    )  # fmt: skip
    def test_bad_parameters_are_422(self, app, query):
        write_everything(app.paths)
        r = app.client.get("/api/ask/lab/families?" + query)
        assert r.status_code == 422 and r.json() == BAD_REQUEST

    def test_the_largest_family_number_is_allowed(self, app):
        write_everything(app.paths)
        assert app.client.get("/api/ask/lab/families?family=10000000").status_code == 200
        assert app.client.get("/api/ask/lab/families?family=0").status_code == 200

    def test_planted_text_in_a_family_name_is_cleaned(self, app):
        write_everything(app.paths)
        f = lab_dir(app.paths) / "families.parquet"
        df = pl.read_parquet(f).with_columns(
            pl.lit(f"{app.root}/x {PLANTED['openrouter']}").alias("name")
        )
        df.write_parquet(f)
        r = app.client.get("/api/ask/lab/families")
        assert r.status_code == 200
        assert_no_leaks(r.text, app.root, planted=[PLANTED["openrouter"]])


class TestChemistryRoute:
    def test_404_until_the_job_has_run(self, app):
        write_history(app.paths)
        error_of(app.client.get("/api/ask/lab/chemistry"), 404, "not_built")

    def test_a_new_job_is_seen_without_a_restart(self, app):
        write_history(app.paths)
        error_of(app.client.get("/api/ask/lab/chemistry"), 404, "not_built")
        from graphlab_fixtures import lab_entry, write_lab_json

        write_lab_json(app.paths, {"chemistry": lab_entry("chemistry")})
        write_chemistry(app.paths)
        assert app.client.get("/api/ask/lab/chemistry").status_code == 200

    def test_the_default(self, app):
        write_everything(app.paths)
        b = app.client.get("/api/ask/lab/chemistry").json()
        assert b["min_targets"] == 100 and len(b["duos"]) == 4
        assert len(b["receivers"]) == 25 and len(b["qbs"]) == 15

    def test_the_filters(self, app):
        write_everything(app.paths)
        b = app.client.get(f"/api/ask/lab/chemistry?min_targets=350&limit=1&qb={MAHOMES}").json()
        assert b["min_targets"] == 350 and [d["receiver"] for d in b["duos"]] == ["Tyreek Hill"]

    @pytest.mark.parametrize(
        "query",
        ["min_targets=99", "min_targets=2001", "min_targets=x", "limit=0", "limit=201",
         "qb=00-003387", "qb=player:00-0033873", f"qb=00-{ARABIC}", f"qb=00-{FULL_WIDTH}",
         "qb=00-0033873x", "qb=%2700-0033873"],
    )  # fmt: skip
    def test_bad_parameters_are_422(self, app, query):
        write_everything(app.paths)
        r = app.client.get("/api/ask/lab/chemistry?" + query)
        assert r.status_code == 422 and r.json() == BAD_REQUEST

    def test_the_edges_are_allowed(self, app):
        write_everything(app.paths)
        for q in ("min_targets=100", "min_targets=2000", "limit=1", "limit=200"):
            assert app.client.get("/api/ask/lab/chemistry?" + q).status_code == 200


class TestBenchmarkRoute:
    def test_404_until_it_has_run(self, app):
        write_history(app.paths)
        msg = error_of(app.client.get("/api/ask/lab/benchmark"), 404, "not_built")
        assert "uv run nfl history-graph bench-paths" in msg

    def test_404_with_only_half(self, app):
        write_history(app.paths)
        write_benchmark(app.paths, rows=False)
        error_of(app.client.get("/api/ask/lab/benchmark"), 404, "not_built")

    def test_the_result(self, app):
        write_history(app.paths)
        write_benchmark(app.paths)
        b = app.client.get("/api/ask/lab/benchmark").json()
        assert b["derive_id"] == D and len(b["rows"]) == 3 and b["sizes"][0]["label"] == "1000"
        for dropped in ("table", "times_ms", "gds_procedures", "results"):
            assert dropped not in b
        assert b["rows"][2]["median_ms"] is None and b["rows"][2]["runs"] == 0

    def test_no_nan_or_infinity_in_the_json(self, app):
        write_history(app.paths)
        write_benchmark(app.paths)
        text = app.client.get("/api/ask/lab/benchmark").text
        assert "NaN" not in text and "Infinity" not in text
        json.loads(text)  # strict parse


class TestTheOtherRoutesStillWork:
    def test_the_catch_all_still_404s_unknown_paths(self, app):
        r = app.client.get("/api/ask/lab/nope")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"

    def test_the_ask_examples_route_is_untouched(self, app):
        assert app.client.get("/api/ask/examples").status_code == 200


class TestNothingIsWritten:
    def test_no_route_writes_under_the_data_root(self, app):
        write_everything(app.paths)

        def tree():
            return {p: p.stat().st_mtime_ns for p in app.root.rglob("*") if p.is_file()}

        before = tree()
        for path in ("/api/ask/graph-lab", "/api/ask/people?q=travis",
                     f"/api/ask/connections?a={A}&b={B}", f"/api/ask/lab/neighbours?player={A}",
                     "/api/ask/lab/neighbours/map", "/api/ask/lab/families?family=1",
                     "/api/ask/lab/chemistry", "/api/ask/lab/benchmark"):  # fmt: skip
            assert app.client.get(path).status_code == 200, path
        assert tree() == before


def test_the_default_service_checks_the_ask_pages_history_state(tmp_path):
    """Without an injected service the app builds one on the Ask service's own check (one Bolt
    probe for both pages); no request is made here, so nothing reaches a server."""
    from nflengine.app.ask_service import AskService, HistoryCheck

    probes = []
    ask = AskService(background=False)
    ask._history = HistoryCheck(ping=lambda: probes.append(1) or True, background=False)
    client = make_client(tmp_path, ask=ask)
    write_history(make_paths(tmp_path))
    r = client.get("/api/ask/graph-lab")
    assert r.status_code == 200 and r.json()["history_state"] == "up" and probes == [1]
    assert isinstance(client.app.state.ctx.connections, ConnectionsService)
    assert C.LINKS  # (the module the service imports)


class TestWarmUpSetting:
    """`AppSettings.warm_connections`: only `nfl app` turns the background warm-up on."""

    def test_off_by_default_and_on_when_asked(self, tmp_path):
        from nflengine.app.server import AppSettings

        assert AppSettings().warm_connections is False
        off = make_client(tmp_path / "a")
        assert off.app.state.ctx.connections._warm_on is False
        on = make_client(tmp_path / "b", warm_connections=True)
        assert on.app.state.ctx.connections._warm_on is True

    def test_an_injected_service_is_used_as_it_is(self, tmp_path):
        svc = ConnectionsService(check=FakeCheck("up"), driver_factory=Factory())
        client = make_client(tmp_path, connections=svc, warm_connections=True)
        assert client.app.state.ctx.connections is svc and svc._warm_on is False

    def test_nfl_app_turns_it_on(self, tmp_path, monkeypatch):
        import socket

        import uvicorn

        import nflengine.app.serve as serve_mod
        import nflengine.app.server as srv

        seen = {}

        class NoServer:
            def __init__(self, config):
                pass

            def run(self):
                return None

        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html>")
        monkeypatch.setattr(srv, "WEB_DIST", dist)
        monkeypatch.setattr(srv, "create_app", lambda s: seen.update(settings=s) or object())
        monkeypatch.setattr(uvicorn, "Server", NoServer)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        assert serve_mod.serve(port=port, open_browser=False, log=lambda _m: None) == 0
        assert seen["settings"].warm_connections is True
