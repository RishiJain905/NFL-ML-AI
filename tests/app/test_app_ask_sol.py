"""Sol's review of AE01 (D143): the fixes that live in the app and the guard's lock check.

- an upload left unfinished is cut off by a deadline (408), never reaching the service;
- the weekly lock is checked before any engine work: no cold build, no warm-up and no reads
  of the freshness files while the weekly run replaces them;
- `Sources.busy` reads the lock's note before probing (a bare probe could stop a run starting
  in that instant).

The result-size bounds are in tests/ask/test_ask_result_size.py, the bounded scrub's cut rules
in tests/test_safetext.py.
"""

from __future__ import annotations

import asyncio

from app_helpers import BASE, TOKEN
from ask_app_fixtures import make_rig, wins, write_graph_build

import nflengine.app.server as server_mod
from nflengine.ask.guard import Sources
from nflengine.ops.lock import lock_path, run_lock


def _scope(path: str = "/api/ask") -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"127.0.0.1:8765"),
            (b"origin", BASE.encode()),
            (b"x-cr-token", TOKEN.encode()),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8765),
    }


class TestBodyDeadline:
    def test_an_unfinished_upload_is_cut_off_and_never_reaches_the_service(
        self, tmp_path, monkeypatch
    ):
        rig = make_rig(tmp_path, monkeypatch, wins())
        monkeypatch.setattr(server_mod, "ASK_BODY_SECONDS", 0.2)
        app = rig.client.app
        sent: list[dict] = []
        first = True

        async def receive() -> dict:
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": b'{"question": "Who', "more_body": True}
            await asyncio.sleep(30)  # the rest never comes
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            sent.append(message)

        async def go() -> None:
            await asyncio.wait_for(app(_scope(), receive, send), timeout=5)

        asyncio.run(go())
        start = next(m for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        assert start["status"] == 408
        assert b"body_timeout" in body and b"Who" not in body
        assert rig.llm.requests == [] and rig.builds == []

    def test_a_whole_body_within_the_deadline_is_answered(self, tmp_path, monkeypatch):
        rig = make_rig(tmp_path, monkeypatch, wins())
        monkeypatch.setattr(server_mod, "ASK_BODY_SECONDS", 5.0)
        assert rig.ask("How many wins do the Cowboys have?").json()["status"] == "answered"


class TestWeeklyLockFirst:
    def test_no_cold_build_while_the_weekly_lock_is_held(self, tmp_path, monkeypatch):
        rig = make_rig(tmp_path, monkeypatch, wins())
        with run_lock(lock_path(rig.paths.runs), "nfl weekly run --auto"):
            b = rig.ask("How many wins do the Cowboys have?").json()
            assert b["status"] == "refused" and b["refusal"]["rule"] == "source.busy"
            assert b["refusal"]["by"] == "code" and b["cost"] == 0.0
            assert rig.builds == [] and rig.llm.requests == []
        assert rig.ask("How many wins do the Cowboys have?").json()["status"] == "answered"
        assert len(rig.builds) == 1

    def test_no_warm_up_while_the_weekly_lock_is_held(self, tmp_path, monkeypatch):
        rig = make_rig(tmp_path, monkeypatch, wins(), background=True)
        with run_lock(lock_path(rig.paths.runs), "nfl weekly run --auto"):
            assert rig.examples().json()["busy"] is True
            assert rig.builds == [] and rig.service._warming is False

    def test_the_freshness_labels_are_not_reread_while_the_lock_is_held(
        self, tmp_path, monkeypatch
    ):
        rig = make_rig(tmp_path, monkeypatch, wins())
        before = rig.examples().json()["sources"]["graph"]
        assert before["week"] == 6
        with run_lock(lock_path(rig.paths.runs), "nfl weekly run --auto"):
            write_graph_build(rig.paths, week=7)  # the run is mid-way: not read yet
            during = rig.examples().json()
            assert during["busy"] is True and during["sources"]["graph"]["week"] == 6
        assert rig.examples().json()["sources"]["graph"]["week"] == 7


class TestLockTakenDuringABuild:
    def test_a_lock_taken_right_before_the_build_refuses_without_building(
        self, tmp_path, monkeypatch
    ):
        # Sol's verification: checked again under the build lock, right before the build
        rig = make_rig(tmp_path, monkeypatch, wins())
        held = iter([False, True, True])  # free at the question's check, held at the build
        monkeypatch.setattr(rig.service, "_weekly_lock_held", lambda _p: next(held))
        b = rig.ask("How many wins do the Cowboys have?").json()
        assert b["status"] == "refused" and b["refusal"]["rule"] == "source.busy"
        assert rig.builds == [] and rig.llm.requests == []

    def test_a_lock_taken_during_a_cold_build_refuses_the_question(self, tmp_path, monkeypatch):
        rig = make_rig(tmp_path, monkeypatch, wins())
        held = iter([False, False, True])  # free before and at the build, held after it
        monkeypatch.setattr(rig.service, "_weekly_lock_held", lambda _p: next(held))
        b = rig.ask("How many wins do the Cowboys have?").json()
        assert b["status"] == "refused" and b["refusal"]["rule"] == "source.busy"
        assert len(rig.builds) == 1 and rig.llm.requests == []


class TestSourcesBusyReadsTheNoteFirst:
    def test_no_note_means_free_without_probing(self, tmp_path, monkeypatch):
        import nflengine.ops.lock as L

        path = lock_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")  # an emptied note: the last run let go

        def no_probe(_p):
            raise AssertionError("probed the lock without a note")

        monkeypatch.setattr(L, "is_locked", no_probe)
        assert Sources(sandbox=None, driver=None, lock_file=path).busy() is False

    def test_a_note_and_a_held_lock_mean_busy(self, tmp_path):
        path = lock_path(tmp_path)
        with run_lock(path, "nfl weekly run --auto"):
            assert Sources(sandbox=None, driver=None, lock_file=path).busy() is True
        assert Sources(sandbox=None, driver=None, lock_file=path).busy() is False
