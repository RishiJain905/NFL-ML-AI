"""`ConnectionsService` (AE03, `app/connections_service.py`): the refusals and their statuses, the
in-flight cap, what happens to a driver error (fixed text, the driver's words never leave, a dead
connection resets the driver), the answer's cleaning and the warm-up. A fake driver and a fake
`connect`; nothing reaches Neo4j or Docker."""

from __future__ import annotations

import threading
import time

import pytest
from app_helpers import make_paths
from graphlab_fixtures import (
    KELCE,
    MAHOMES,
    NOBODY,
    D,
    Factory,
    FakeCheck,
    canned_connection,
    write_history,
)

from nflengine.app.connections_service import (
    ERRORS,
    MAX_INFLIGHT,
    ConnectionsRefused,
    ConnectionsService,
)
from nflengine.app.readers import graphlab as G
from nflengine.history_graph import connections as C
from nflengine.history_graph.state import lock_path, mark_unfinished

A, B = f"player:{MAHOMES}", f"player:{KELCE}"


@pytest.fixture(autouse=True)
def _fresh_cache():
    G._cache.clear()
    yield
    G._cache.clear()


@pytest.fixture
def paths(tmp_path):
    p = make_paths(tmp_path)
    write_history(p)
    return p


class Calls:
    """A `connect=` that records its calls and answers a canned chain."""

    def __init__(self, result=None, error=None):
        self.calls: list[dict] = []
        self.result = result
        self.error = error

    def __call__(self, driver, a, b, **kw):
        self.calls.append({"driver": driver, "a": a, "b": b, **kw})
        if self.error is not None:
            raise self.error
        return self.result if self.result is not None else canned_connection(a.id, b.id)


def service(connect=None, state="up", factory=None, **kw):
    check = FakeCheck(state)
    f = factory or Factory()
    svc = ConnectionsService(check=check, driver_factory=f, connect=connect or Calls(), **kw)
    svc.check, svc.factory = check, f
    return svc


def refused(svc, paths, *args, **kw) -> ConnectionsRefused:
    with pytest.raises(ConnectionsRefused) as e:
        svc.connect(paths, *args, **kw)
    return e.value


class TestRefusals:
    @pytest.mark.parametrize(
        "bad",
        ["", "garbage", "player:00-003387", "coach:Andy-Reid", "x'}) DETACH DELETE (n) //",
         "player:00-\u0660\u0660\u0663\u0663\u0668\u0667\u0663", "team:KC", f"{A}\nMATCH (n)"],
    )  # fmt: skip
    @pytest.mark.parametrize("side", ["a", "b"])
    def test_a_bad_id_is_422_before_anything_else(self, paths, bad, side):
        calls = Calls()
        svc = service(calls)
        args = (bad, B) if side == "a" else (A, bad)
        e = refused(svc, paths, *args)
        assert (e.status, e.code, e.message) == (422, "bad_id", ERRORS["bad_id"])
        assert calls.calls == [] and svc.factory.drivers == []

    def test_a_bad_mode_is_422(self, paths):
        calls = Calls()
        e = refused(service(calls), paths, A, B, mode="cheapest")
        assert (e.status, e.code) == (422, "bad_mode") and calls.calls == []

    def test_the_same_person_twice_is_422(self, paths):
        calls = Calls()
        e = refused(service(calls), paths, A, f" {A}")
        assert (e.status, e.code) == (422, "same_person") and calls.calls == []

    def test_a_player_and_a_coach_with_the_same_text_are_not_the_same_person(self, paths):
        assert service().connect(paths, A, "coach:andy-reid")["found"] is True

    def test_the_id_check_comes_before_the_mode_and_the_mode_before_the_pair(self, paths):
        svc = service()
        assert refused(svc, paths, "bad", B, mode="nope").code == "bad_id"
        assert refused(svc, paths, A, A, mode="nope").code == "bad_mode"

    def test_no_person_links_yet_is_503(self, tmp_path):
        p = make_paths(tmp_path)
        write_history(p, derive=False)
        svc = service()
        e = refused(svc, p, A, B)
        assert (e.status, e.code, e.message) == (503, "no_links", ERRORS["no_links"])
        assert "uv run nfl history-graph derive" in e.message and svc.factory.drivers == []

    def test_a_person_not_in_the_people_index_is_404_and_never_reaches_neo4j(self, paths):
        calls = Calls()
        svc = service(calls)
        for pid in ("player:00-0099999", "coach:nobody-here"):
            e = refused(svc, paths, A, pid)
            assert (e.status, e.code) == (404, "not_found")
            e = refused(svc, paths, pid, B)
            assert (e.status, e.code) == (404, "not_found")
        assert calls.calls == [] and svc.factory.drivers == []  # no driver was even opened

    def test_a_person_with_no_links_is_in_the_index_so_the_search_runs(self, paths):
        calls = Calls()
        service(calls).connect(paths, A, f"player:{NOBODY}")
        assert len(calls.calls) == 1  # (connections.connect says "no person links yet")

    def test_a_busy_history_graph_is_503(self, paths):
        calls = Calls()
        svc = service(calls)
        mark_unfinished(paths, "derive", "B1")
        e = refused(svc, paths, A, B)
        assert (e.status, e.code, e.message) == (503, "history_busy", ERRORS["history_busy"])
        assert calls.calls == [] and svc.factory.drivers == []

    def test_a_held_history_lock_is_busy_too(self, paths):
        from nflengine.ops.lock import run_lock

        calls = Calls()
        with run_lock(lock_path(paths), "nfl history-graph append"):
            e = refused(service(calls), paths, A, B)
        assert e.code == "history_busy" and calls.calls == []

    def test_a_history_graph_that_is_down_is_503(self, paths):
        calls = Calls()
        svc = service(calls, state="down")
        e = refused(svc, paths, A, B)
        assert (e.status, e.code, e.message) == (503, "history_down", ERRORS["history_down"])
        assert "uv run nfl history-graph start" in e.message
        assert calls.calls == [] and svc.factory.drivers == []

    @pytest.mark.parametrize("state", ["up", "checking"])
    def test_only_down_refuses(self, paths, state):
        calls = Calls()
        service(calls, state=state).connect(paths, A, B)
        assert len(calls.calls) == 1

    def test_the_order_of_the_checks(self, paths, tmp_path):
        # busy beats down; not-found beats busy; no-links beats not-found
        mark_unfinished(paths, "derive", "B1")
        svc = service(state="down")
        assert refused(svc, paths, A, B).code == "history_busy"
        assert refused(svc, paths, A, "coach:nobody-here").code == "not_found"
        p2 = make_paths(tmp_path / "other")
        write_history(p2, derive=False)
        assert refused(svc, p2, A, "coach:nobody-here").code == "no_links"

    def test_every_refusal_is_fixed_text(self):
        for code, text in ERRORS.items():
            assert ConnectionsRefused(500, code).message == text
            assert "Traceback" not in text and "neo4j.exceptions" not in text


class TestAnAnswer:
    def test_the_search_gets_the_two_people_mode_k_and_the_derive(self, paths):
        calls = Calls()
        svc = service(calls)
        svc.connect(paths, A, B, k=3, mode="strongest")
        (c,) = calls.calls
        assert (c["a"].kind, c["a"].id) == ("player", MAHOMES)
        assert (c["b"].kind, c["b"].id) == ("player", KELCE)
        assert (c["mode"], c["k"], c["derive_id"]) == ("strongest", 3, D)
        assert c["driver"] is svc.factory.drivers[0]

    def test_the_defaults_are_the_fewest_chain(self, paths):
        calls = Calls()
        service(calls).connect(paths, A, B)
        assert (calls.calls[0]["mode"], calls.calls[0]["k"]) == ("fewest", 1)

    def test_the_answer_carries_the_links_as_of(self, paths):
        out = service().connect(paths, A, B)
        assert out["asof"]["derive_id"] == D and out["asof"]["label"].startswith("person links")
        assert out["found"] is True and out["ms"] == 12 and out["paths"][0]["hops"] == 1

    def test_every_person_gets_a_team_and_seasons_from_the_people_index(self, paths):
        out = service().connect(paths, A, B)
        assert (out["a"]["team"], out["a"]["seasons"]) == ("KC", "2018-2025")
        assert (out["b"]["team"], out["b"]["seasons"]) == ("KC", "2018-2025")
        for person in out["paths"][0]["people"]:
            assert person["team"] == "KC" and person["seasons"] == "2018-2025"

    def test_a_person_the_index_doesnt_know_is_left_alone(self, paths):
        calls = Calls(result=canned_connection(MAHOMES, "00-0099999"))
        out = service(calls).connect(paths, A, B)
        stranger = out["paths"][0]["people"][1]
        assert "team" not in stranger and stranger["name"] == "Travis Kelce"

    def test_no_chain_is_an_answer_not_a_refusal(self, paths):
        res = canned_connection(found=False, paths=[], note="no chain of person links joins them")
        out = service(Calls(result=res)).connect(paths, A, B)
        assert out["found"] is False and out["paths"] == [] and out["note"].startswith("no chain")

    def test_strings_in_the_answer_are_cleaned(self, paths):
        root = str(paths.root)
        res = canned_connection()
        res["note"] = f"see {root}/neo4j-history/x " + "y" * 600
        res["a"]["name"] = f"{root}/raw/name"
        out = service(Calls(result=res)).connect(paths, A, B)
        assert root not in str(out) and len(out["note"]) <= 400

    def test_a_key_shaped_text_never_leaves(self, paths):
        res = canned_connection()
        res["note"] = "the key sk-or-v1-PLANTEDPLANTEDPLANTED1234 is here"
        out = service(Calls(result=res)).connect(paths, A, B)
        assert "PLANTED" not in out["note"]

    def test_one_driver_for_the_apps_life(self, paths):
        svc = service()
        for _ in range(3):
            svc.connect(paths, A, B)
        assert len(svc.factory.drivers) == 1 and not svc.factory.drivers[0].closed

    def test_close_closes_the_driver_and_the_next_search_opens_another(self, paths):
        svc = service()
        svc.connect(paths, A, B)
        svc.close()
        assert svc.factory.drivers[0].closed
        svc.connect(paths, A, B)
        assert len(svc.factory.drivers) == 2
        svc.close()
        svc.close()  # closing twice is fine

    def test_history_state_is_the_checks(self):
        svc = service(state="checking")
        assert svc.history_state() == "checking"
        svc.check.value = "up"
        assert svc.history_state() == "up"


class Boom(Exception):
    pass


class ServiceUnavailable(Exception):
    pass


class SessionExpired(Exception):
    pass


class ClientTimeoutError(Exception):
    pass


class TestDriverErrors:
    def test_an_unexpected_error_is_500_failed_with_fixed_text(self, paths):
        calls = Calls(error=Boom("password=hunter2 neo4j://secret-host:7688 MATCH (n) RETURN n"))
        svc = service(calls)
        e = refused(svc, paths, A, B)
        assert (e.status, e.code, e.message) == (500, "failed", ERRORS["failed"])
        for part in (str(e), e.message, e.code, repr(e.args)):
            assert "hunter2" not in part and "secret-host" not in part and "MATCH" not in part
        assert not svc.factory.drivers[0].closed  # an ordinary failure keeps the connection

    @pytest.mark.parametrize("exc", [ServiceUnavailable("bolt://x gone"), SessionExpired("x"),
                                     ConnectionResetError("reset"), OSError("os says no"),
                                     TimeoutError("socket")])  # fmt: skip
    def test_a_dead_connection_is_503_down_and_resets_the_driver(self, paths, exc):
        calls = Calls(error=exc)
        svc = service(calls)
        e = refused(svc, paths, A, B)
        if isinstance(exc, TimeoutError):
            # (a TimeoutError is an OSError: the connection is treated as dead first)
            assert (e.status, e.code) == (503, "history_down")
        else:
            assert (e.status, e.code, e.message) == (503, "history_down", ERRORS["history_down"])
        assert svc.factory.drivers[0].closed  # the broken driver was closed ...
        calls.error = None
        svc.connect(paths, A, B)
        assert (
            len(svc.factory.drivers) == 2 and not svc.factory.drivers[1].closed
        )  # ... and replaced

    def test_the_text_of_a_dead_connection_never_leaves(self, paths):
        e = refused(service(Calls(error=ServiceUnavailable("bolt://10.0.0.5:7688 user=neo4j"))),
                    paths, A, B)  # fmt: skip
        assert "10.0.0.5" not in e.message and "neo4j" not in e.message.replace("history", "")

    @pytest.mark.parametrize("exc", [ClientTimeoutError("x"), Boom("the query timed out: 8s"),
                                     Boom("Transaction timed out")])  # fmt: skip
    def test_a_slow_search_is_504_and_keeps_the_driver(self, paths, exc):
        svc = service(Calls(error=exc))
        e = refused(svc, paths, A, B)
        assert (e.status, e.code, e.message) == (504, "too_slow", ERRORS["too_slow"])
        assert not svc.factory.drivers[0].closed

    @pytest.mark.parametrize(
        ("code", "status", "shown"),
        [("not_found", 404, "not_found"), ("bad_mode", 422, "bad_mode"),
         ("same_person", 422, "same_person"), ("bad_id", 422, "bad_id"),
         ("no_derive", 503, "no_links"), ("whatever", 422, "failed")],
    )  # fmt: skip
    def test_a_connections_refusal_keeps_its_code_when_the_api_knows_it(
        self, paths, code, status, shown
    ):
        svc = service(Calls(error=C.ConnectionError_(code, "internal words: neo4j.password")))
        e = refused(svc, paths, A, B)
        assert (e.status, e.code) == (status, shown)
        assert "internal words" not in e.message  # the fixed text, not the module's

    def test_the_slot_is_released_after_every_kind_of_error(self, paths):
        calls = Calls(error=Boom("x"))
        svc = service(calls, max_inflight=1)
        for exc in (Boom("x"), ServiceUnavailable("x"), ClientTimeoutError("x"),
                    C.ConnectionError_("not_found", "x")):  # fmt: skip
            calls.error = exc
            assert refused(svc, paths, A, B).code != "busy"
        calls.error = None
        assert svc.connect(paths, A, B)["found"] is True  # and the slot is still free


class TestInFlightCap:
    def blocked(self, paths, svc, release):
        """Start a search that waits on `release`; returns the thread once it is inside."""
        inside = threading.Event()
        results: list = []

        def connect(driver, a, b, **kw):
            inside.set()
            assert release.wait(10)
            return canned_connection(a.id, b.id)

        svc._connect = connect

        def run():
            try:
                results.append(svc.connect(paths, A, B))
            except ConnectionsRefused as e:  # pragma: no cover
                results.append(e)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        assert inside.wait(10)
        return t, results

    def test_the_cap_is_two_by_default(self):
        assert MAX_INFLIGHT == 2

    def test_a_search_over_the_cap_is_409_busy(self, paths):
        svc = service(max_inflight=1)
        release = threading.Event()
        t, results = self.blocked(paths, svc, release)
        try:
            e = refused(svc, paths, A, B)
            assert (e.status, e.code, e.message) == (409, "busy", ERRORS["busy"])
        finally:
            release.set()
            t.join(10)
        assert results and results[0]["found"] is True
        # the slot is free again once the first one is done
        svc._connect = Calls()
        assert svc.connect(paths, A, B)["found"] is True

    def test_the_default_cap_lets_two_run_and_refuses_the_third(self, paths):
        svc = service()
        release = threading.Event()
        started = threading.Semaphore(0)

        def connect(driver, a, b, **kw):
            started.release()
            assert release.wait(10)
            return canned_connection(a.id, b.id)

        svc._connect = connect
        ts = [threading.Thread(target=lambda: svc.connect(paths, A, B), daemon=True)
              for _ in range(2)]  # fmt: skip
        for t in ts:
            t.start()
        for _ in range(2):
            assert started.acquire(timeout=10)
        try:
            assert refused(svc, paths, A, B).code == "busy"
        finally:
            release.set()
            for t in ts:
                t.join(10)

    def test_a_refused_search_never_takes_a_slot(self, paths):
        svc = service(max_inflight=1)
        for _ in range(3):
            refused(svc, paths, "bad", B)
            refused(svc, paths, A, "player:00-0099999")
        assert svc.connect(paths, A, B)["found"] is True


class TestWarmUp:
    @pytest.fixture
    def warmed(self, monkeypatch):
        """Records `ensure_projection` calls and lets a test wait for the warm-up thread."""
        calls: list = []
        done = threading.Event()

        def ensure(driver, derive_id):
            calls.append((driver, derive_id))
            done.set()
            return {"name": f"persons-{derive_id}", "made": True}

        monkeypatch.setattr(C, "ensure_projection", ensure)
        return calls, done

    def join_warm(self):
        for t in threading.enumerate():
            if t.name == "cr-connections-warm":
                t.join(10)

    def test_off_by_default_it_never_looks_at_the_check_or_starts_a_thread(self, paths):
        svc = service()
        before = threading.active_count()
        svc.warm_if_up(paths)
        assert svc.check.calls == 0 and svc.factory.drivers == []
        assert threading.active_count() == before

    def test_it_warms_the_graph_once_per_derive(self, paths, warmed):
        calls, done = warmed
        svc = service(warm=True)
        svc.warm_if_up(paths)
        assert done.wait(10)
        self.join_warm()
        (driver, derive_id), = calls  # fmt: skip
        assert derive_id == D and driver is svc.factory.drivers[0]
        # one read of every person link, to warm the page cache
        assert driver.queries == [
            "MATCH (a:Player|Coach)-[r:TEAMMATE|COACHED|WORKED_WITH]->() RETURN sum(r.snaps) AS n"
        ]
        svc.warm_if_up(paths)
        svc.warm_if_up(paths)
        self.join_warm()
        assert len(calls) == 1  # the same derive: not again
        assert svc._warmed == D

    def test_a_new_derive_is_warmed_again(self, paths, warmed):
        from graphlab_fixtures import DERIVE, put_json

        calls, done = warmed
        svc = service(warm=True)
        svc.warm_if_up(paths)
        assert done.wait(10)
        self.join_warm()
        put_json(paths.neo4j_history / "derive.json", {**DERIVE, "derive_id": "NEWDERIVE"})
        svc.warm_if_up(paths)
        self.join_warm()
        assert [c[1] for c in calls] == [D, "NEWDERIVE"]

    @pytest.mark.parametrize("state", ["down", "checking"])
    def test_only_when_the_graph_is_up(self, paths, warmed, state):
        calls, _ = warmed
        svc = service(warm=True, state=state)
        svc.warm_if_up(paths)
        self.join_warm()
        assert calls == [] and svc.factory.drivers == []

    def test_not_without_person_links(self, tmp_path, warmed):
        calls, _ = warmed
        p = make_paths(tmp_path)
        write_history(p, derive=False)
        svc = service(warm=True)
        svc.warm_if_up(p)
        self.join_warm()
        assert calls == [] and svc.factory.drivers == []

    def test_not_while_the_graph_is_busy(self, paths, warmed):
        calls, _ = warmed
        mark_unfinished(paths, "derive", "B1")
        svc = service(warm=True)
        svc.warm_if_up(paths)
        self.join_warm()
        assert calls == [] and svc.factory.drivers == []

    def test_a_failed_warm_up_resets_the_driver_and_may_be_tried_again(self, paths, monkeypatch):
        attempts = []

        def ensure(driver, derive_id):
            attempts.append(derive_id)
            if len(attempts) == 1:
                raise ServiceUnavailable("gone")
            return {"name": "x", "made": True}

        monkeypatch.setattr(C, "ensure_projection", ensure)
        svc = service(warm=True)
        svc.warm_if_up(paths)
        self.join_warm()
        assert svc._warmed is None and svc._warming is False
        assert svc.factory.drivers[0].closed  # the broken driver was dropped
        svc.warm_if_up(paths)
        self.join_warm()
        assert len(attempts) == 2 and svc._warmed == D and len(svc.factory.drivers) == 2

    def test_a_warm_up_that_raises_never_reaches_the_caller(self, paths, monkeypatch):
        monkeypatch.setattr(C, "ensure_projection", lambda d, i: (_ for _ in ()).throw(Boom("x")))
        svc = service(warm=True)
        svc.warm_if_up(paths)  # no exception
        self.join_warm()

    def test_two_requests_start_one_warm_up(self, paths, monkeypatch):
        gate = threading.Event()
        calls: list = []

        def ensure(driver, derive_id):
            calls.append(derive_id)
            assert gate.wait(10)
            return {"name": "x", "made": True}

        monkeypatch.setattr(C, "ensure_projection", ensure)
        svc = service(warm=True)
        svc.warm_if_up(paths)
        deadline = time.time() + 10
        while not calls and time.time() < deadline:
            time.sleep(0.01)
        svc.warm_if_up(paths)  # the first is still running: nothing new starts
        svc.warm_if_up(paths)
        gate.set()
        self.join_warm()
        assert len(calls) == 1

    def test_the_thread_is_a_daemon(self, paths, monkeypatch):
        seen = {}

        def ensure(driver, derive_id):
            seen["daemon"] = threading.current_thread().daemon
            return {"name": "x"}

        monkeypatch.setattr(C, "ensure_projection", ensure)
        svc = service(warm=True)
        svc.warm_if_up(paths)
        self.join_warm()
        assert seen == {"daemon": True}


class TestDefaultDriver:
    def test_the_default_driver_is_the_history_graphs_with_a_short_connect_timeout(
        self, monkeypatch
    ):
        seen: list = []
        monkeypatch.setattr(
            "nflengine.history_graph.client.get_history_driver",
            lambda **kw: seen.append(kw) or "a driver",
        )
        svc = ConnectionsService(check=FakeCheck("up"))
        assert svc._get_driver() == "a driver" and svc._get_driver() == "a driver"
        assert seen == [{"connection_timeout": 5.0}]  # made once, never the weekly graph's

    def test_without_a_check_it_uses_the_ask_pages_history_check(self):
        from nflengine.app.ask_service import HistoryCheck

        svc = ConnectionsService()
        assert isinstance(svc._check, HistoryCheck)  # (nothing is probed until state() is asked)
