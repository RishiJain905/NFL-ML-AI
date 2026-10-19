"""What the history graph holds, from its own files (AE02): `state.py` (build.json, append.json,
the as-of label, the busy lock) and `status.py` (`nfl history-graph status`)."""

from __future__ import annotations

import json

import pytest
from history_fakes import (
    FakeDocker,
    FakeGraph,
    no_real_docker,  # noqa: F401  (the safety net)
    write_state,
)

from nflengine.history_graph import state
from nflengine.history_graph.build import graph_counts, store_mb
from nflengine.history_graph.config import HistoryConfig
from nflengine.history_graph.docker import Done
from nflengine.history_graph.status import history_status
from nflengine.ops.lock import lock_path, run_lock
from nflengine.paths import DataPaths

BUILD = {
    "status": "ok",
    "build_id": "20261018T120000Z",
    "first_season": 2018,
    "through_season": 2025,
    "built_at": "2026-10-18T12:05:00+00:00",
    "seconds": {"total": 355.0},
}
APPEND = {
    "status": "ok",
    "build_id": "20261018T120000Z",
    "season": 2026,
    "through_week": 5,
    "appended_at": "2026-10-18T13:00:00+00:00",
    "plays": 12849,
}


@pytest.fixture
def paths(tmp_path) -> DataPaths:
    return DataPaths(tmp_path)


def put(paths, name, doc):
    return write_state(paths.neo4j_history, name, doc)


class TestFiles:
    def test_the_files_live_in_the_history_folder(self, paths):
        assert state.build_path(paths) == paths.root / "neo4j-history" / "build.json"
        assert state.append_path(paths) == paths.root / "neo4j-history" / "append.json"
        assert state.lock_path(paths) == paths.root / "neo4j-history" / ".history.lock"

    def test_the_history_lock_is_not_the_weekly_lock(self, paths):
        assert state.lock_path(paths) != lock_path(paths.runs)
        assert state.lock_path(paths).parent == paths.neo4j_history


class TestReadBuild:
    def test_nothing_yet(self, paths):
        assert state.read_build(paths) is None
        assert state.history_asof(paths) is None

    def test_a_finished_build(self, paths):
        put(paths, "build.json", BUILD)
        assert state.read_build(paths) == BUILD

    @pytest.mark.parametrize("status", ["failed", "running", "", None])
    def test_a_build_that_did_not_finish_is_not_read(self, paths, status):
        put(paths, "build.json", {**BUILD, "status": status})
        assert state.read_build(paths) is None
        assert state.history_asof(paths) is None

    def test_a_corrupt_or_odd_file_is_none_not_an_error(self, paths):
        state.build_path(paths).parent.mkdir(parents=True)
        for text in ("{not json", "", "[]", '"a string"', "null", "42"):
            state.build_path(paths).write_text(text, encoding="utf-8")
            assert state.read_build(paths) is None, text

    def test_bytes_that_are_not_utf8_are_none(self, paths):
        state.build_path(paths).parent.mkdir(parents=True)
        state.build_path(paths).write_bytes(b"\xff\xfe\x00bad")
        assert state.read_build(paths) is None


class TestReadAppend:
    def test_an_append_onto_the_current_build_counts(self, paths):
        put(paths, "build.json", BUILD)
        put(paths, "append.json", APPEND)
        assert state.read_append(paths) == APPEND

    def test_an_append_made_on_another_build_does_not_count(self, paths):
        put(paths, "build.json", BUILD)
        put(paths, "append.json", {**APPEND, "build_id": "20260101T000000Z"})
        assert state.read_append(paths) is None  # a rebuild dropped the appended weeks

    @pytest.mark.parametrize("status", ["failed", "running", None])
    def test_a_failed_append_does_not_count(self, paths, status):
        put(paths, "build.json", BUILD)
        put(paths, "append.json", {**APPEND, "status": status})
        assert state.read_append(paths) is None

    def test_no_build_means_no_append(self, paths):
        put(paths, "append.json", APPEND)
        assert state.read_append(paths) is None

    def test_a_build_passed_in_is_used_without_reading_the_file(self, paths):
        put(paths, "append.json", APPEND)
        assert state.read_append(paths, BUILD) == APPEND
        assert state.read_append(paths, {**BUILD, "build_id": "other"}) is None

    def test_corrupt_append_is_none(self, paths):
        put(paths, "build.json", BUILD)
        state.append_path(paths).write_text("{oops", encoding="utf-8")
        assert state.read_append(paths) is None


class TestAsof:
    def test_the_label_of_a_build_with_an_append(self, paths):
        put(paths, "build.json", BUILD)
        put(paths, "append.json", APPEND)
        a = state.history_asof(paths)
        assert a["label"] == "history graph 2018-2025 + 2026 week 5"
        assert (a["first_season"], a["through_season"], a["season"], a["week"]) == (
            2018,
            2025,
            2026,
            5,
        )
        assert a["built_at"] == BUILD["built_at"] and a["appended_at"] == APPEND["appended_at"]

    def test_the_label_without_an_append(self, paths):
        put(paths, "build.json", BUILD)
        a = state.history_asof(paths)
        assert a["label"] == "history graph 2018-2025"
        assert a["season"] is None and a["week"] is None and a["appended_at"] is None

    def test_an_append_on_another_build_is_left_out_of_the_label(self, paths):
        put(paths, "build.json", BUILD)
        put(paths, "append.json", {**APPEND, "build_id": "old"})
        assert state.history_asof(paths)["label"] == "history graph 2018-2025"

    @pytest.mark.parametrize("week", [0, None])
    def test_an_append_with_no_weeks_adds_nothing_to_the_label(self, paths, week):
        put(paths, "build.json", BUILD)
        put(paths, "append.json", {**APPEND, "through_week": week})
        a = state.history_asof(paths)
        assert a["label"] == "history graph 2018-2025" and a["season"] is None

    def test_numbers_stored_as_text_are_read(self, paths):
        put(paths, "build.json", {**BUILD, "first_season": "2018", "through_season": "2025"})
        put(paths, "append.json", {**APPEND, "season": "2026", "through_week": "7"})
        assert state.history_asof(paths)["label"] == "history graph 2018-2025 + 2026 week 7"


class TestBusy:
    def test_no_lock_file_is_not_busy(self, paths):
        assert state.history_busy(paths) is False

    def test_a_note_left_behind_by_a_dead_run_is_not_busy(self, paths):
        state.lock_path(paths).parent.mkdir(parents=True)
        state.lock_path(paths).write_text(
            json.dumps({"pid": 1, "command": "old"}), encoding="utf-8"
        )
        assert state.history_busy(paths) is False  # nobody holds the OS lock

    def test_a_held_lock_is_busy_and_released_after(self, paths):
        with run_lock(state.lock_path(paths), "nfl history-graph build"):
            assert state.history_busy(paths) is True
        assert state.history_busy(paths) is False

    def test_the_weekly_lock_does_not_make_the_history_graph_busy(self, paths):
        with run_lock(lock_path(paths.runs), "nfl weekly run"):
            assert state.history_busy(paths) is False

    def test_the_history_lock_does_not_make_the_weekly_run_busy(self, paths):
        from nflengine.ops.lock import is_locked

        with run_lock(state.lock_path(paths), "nfl history-graph append"):
            assert not is_locked(lock_path(paths.runs))

    def test_two_holders_cannot_coexist(self, paths):
        from nflengine.ops.lock import LockHeld

        with run_lock(state.lock_path(paths), "first"):
            with pytest.raises(LockHeld) as e, run_lock(state.lock_path(paths), "second"):
                pass
            assert e.value.holder["command"] == "first"


# ---- status ----------------------------------------------------------------------------------

CFG = HistoryConfig()


def graph_with_counts() -> FakeGraph:
    g = FakeGraph()
    g.nodes["Play"] = {i: {"play_key": i} for i in range(3)}
    g.rels["ON_FIELD"] = [{"s": 1, "e": 2, "p": {}}]
    return g


class TestStatus:
    def test_everything_up(self, paths):
        put(paths, "build.json", BUILD)
        put(paths, "append.json", APPEND)
        fd = FakeDocker()
        g = graph_with_counts()
        st = history_status(paths, CFG, runner=fd, up=lambda: True, driver_factory=lambda: g)
        assert st["container"] == "running" and st["up"] is True
        assert (
            st["bolt_uri"] == "bolt://localhost:7688"
            and st["browser_url"] == "http://localhost:7475"
        )
        assert (
            st["counts"]["Play"] == 3
            and st["counts"]["ON_FIELD"] == 1
            and st["counts"]["Game"] == 0
        )
        assert st["build"] == BUILD and st["append"] == APPEND
        assert st["asof"]["label"] == "history graph 2018-2025 + 2026 week 5"
        assert st["last_failed_build"] is None and st["busy"] is False and st["store_mb"] is None
        assert g.closed  # the driver is closed

    def test_a_stopped_container_is_not_probed(self, paths):
        fd = FakeDocker()
        fd.container = "exited"

        def boom():
            raise AssertionError("Bolt must not be probed for a stopped container")

        st = history_status(paths, CFG, runner=fd, up=boom)
        assert st["container"] == "exited" and st["up"] is False and st["counts"] is None

    def test_a_missing_container(self, paths):
        fd = FakeDocker()
        fd.container = "missing"
        st = history_status(paths, CFG, runner=fd, up=lambda: True)
        assert st["container"] == "missing" and st["up"] is False

    def test_docker_unreachable_still_asks_bolt(self, paths):
        fd = FakeDocker().on(lambda a: a[:2] == ["docker", "ps"], Done(1, "no daemon"))
        g = graph_with_counts()
        st = history_status(paths, CFG, runner=fd, up=lambda: True, driver_factory=lambda: g)
        assert st["container"] == "unknown" and st["up"] is True and st["counts"]

    def test_a_driver_that_fails_means_no_counts_not_an_error(self, paths):
        def broken():
            raise RuntimeError("server said SECRET-TEXT")

        st = history_status(paths, CFG, runner=FakeDocker(), up=lambda: True, driver_factory=broken)
        assert st["up"] is True and st["counts"] is None

    def test_the_command_set_is_read_only(self, paths):
        fd = FakeDocker()
        history_status(paths, CFG, runner=fd, up=lambda: True, driver_factory=graph_with_counts)
        assert all(c[:2] == ["docker", "ps"] for c in fd.calls)  # only a state probe

    def test_the_last_failed_build_shows_only_when_newer_than_the_good_one(self, paths):
        put(paths, "build.json", BUILD)
        put(paths, "build_failed.json", {"status": "failed", "build_id": "20260101T000000Z"})
        fd = FakeDocker()
        fd.container = "exited"
        assert history_status(paths, CFG, runner=fd)["last_failed_build"] is None  # older
        put(
            paths,
            "build_failed.json",
            {
                "status": "failed",
                "build_id": "20261019T000000Z",
                "failed_stage": "import",
                "error": "code 70",
            },
        )
        st = history_status(paths, CFG, runner=fd)
        assert st["last_failed_build"]["failed_stage"] == "import"

    def test_a_failed_build_with_no_good_one_is_shown(self, paths):
        put(
            paths, "build_failed.json", {"status": "failed", "build_id": "X", "failed_stage": "csv"}
        )
        fd = FakeDocker()
        fd.container = "missing"
        st = history_status(paths, CFG, runner=fd)
        assert st["build"] is None and st["last_failed_build"]["failed_stage"] == "csv"
        assert st["asof"] is None

    def test_busy_is_reported(self, paths):
        fd = FakeDocker()
        fd.container = "exited"
        with run_lock(state.lock_path(paths), "nfl history-graph build"):
            assert history_status(paths, CFG, runner=fd)["busy"] is True


class TestHelpers:
    def test_graph_counts_asks_for_each_name(self):
        g = graph_with_counts()
        assert graph_counts(g, ["Play", "ON_FIELD"]) == {"Play": 3, "ON_FIELD": 1}
        assert set(graph_counts(g)) >= {"Play", "Player", "ON_FIELD", "PLAYED_FOR"}

    def test_store_mb_sums_the_database_folder(self, paths):
        assert store_mb(paths) is None
        db = paths.neo4j_history / "data" / "databases" / "neo4j"
        (db / "sub").mkdir(parents=True)
        (db / "a.store").write_bytes(b"x" * 1_500_000)
        (db / "sub" / "b.store").write_bytes(b"y" * 500_000)
        assert store_mb(paths) == 2.0
