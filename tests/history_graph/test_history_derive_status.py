"""What AE03 changed in the history graph's status and state files: `history_status` has a
`derive` key, a derive marker makes the graph busy for Ask, `graph_counts`' default names include
the derived types (`status.py`, `state.py`, `build.graph_counts`)."""

from __future__ import annotations

import json

import pytest
from history_fakes import FakeDocker, FakeGraph, no_real_docker  # noqa: F401  (the safety net)

from nflengine.history_graph import schema as S
from nflengine.history_graph import state
from nflengine.history_graph.build import NEO4J_STAGES, graph_counts
from nflengine.history_graph.config import HistoryConfig
from nflengine.history_graph.status import history_status
from nflengine.paths import DataPaths

CFG = HistoryConfig()
BUILD = {"status": "ok", "build_id": "B1", "first_season": 2018, "through_season": 2025}
DERIVE = {"status": "ok", "build_id": "B1", "derive_id": "D1", "counts": {"TEAMMATE": 9}}


@pytest.fixture
def paths(tmp_path):
    return DataPaths(tmp_path)


def put(paths, name, doc):
    p = paths.neo4j_history / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")


def stopped() -> FakeDocker:
    fd = FakeDocker()
    fd.container = "exited"  # no Bolt probe, no driver
    return fd


class TestStatusDerive:
    def test_the_key_is_there_and_empty_before_any_derive(self, paths):
        put(paths, "build.json", BUILD)
        st = history_status(paths, CFG, runner=stopped())
        assert "derive" in st and st["derive"] is None

    def test_a_derive_onto_the_current_build_is_reported(self, paths):
        put(paths, "build.json", BUILD)
        put(paths, "derive.json", DERIVE)
        assert history_status(paths, CFG, runner=stopped())["derive"] == DERIVE

    def test_a_derive_of_another_build_is_not(self, paths):
        put(paths, "build.json", BUILD)
        put(paths, "derive.json", {**DERIVE, "build_id": "OLD"})
        assert history_status(paths, CFG, runner=stopped())["derive"] is None

    def test_a_failed_derive_is_not(self, paths):
        put(paths, "build.json", BUILD)
        put(paths, "derive.json", {**DERIVE, "status": "failed"})
        assert history_status(paths, CFG, runner=stopped())["derive"] is None

    def test_without_a_build_there_is_no_derive(self, paths):
        put(paths, "derive.json", DERIVE)
        assert history_status(paths, CFG, runner=stopped())["derive"] is None

    def test_an_unfinished_derive_is_busy_and_named(self, paths):
        put(paths, "build.json", BUILD)
        state.mark_unfinished(paths, "derive", "B1")
        st = history_status(paths, CFG, runner=stopped())
        assert st["busy"] is True and st["unfinished"]["what"] == "derive"
        assert st["running"] is None  # nobody holds the lock: it didn't finish

    def test_the_counts_include_the_derived_types(self, paths):
        g = FakeGraph()
        g.nodes["Play"] = {1: {"play_key": 1}}
        g.rels["TEAMMATE"] = [{"s": "a", "e": "b", "p": {}}] * 4
        st = history_status(paths, CFG, runner=FakeDocker(), up=lambda: True,
                            driver_factory=lambda: g)  # fmt: skip
        assert st["counts"]["TEAMMATE"] == 4 and st["counts"]["Play"] == 1
        assert set(S.DERIVED) <= set(st["counts"])


class TestGraphCounts:
    def test_the_default_names_are_every_label_and_every_type_derived_too(self):
        counts = graph_counts(FakeGraph())
        assert set(counts) == {*S.NODES, *S.RELATIONSHIPS, *S.DERIVED}

    def test_explicit_names_are_respected(self):
        g = FakeGraph()
        g.rels["TEAMMATE"] = [{"s": "a", "e": "b", "p": {}}]
        assert graph_counts(g, ["TEAMMATE"]) == {"TEAMMATE": 1}
        assert set(graph_counts(g, [*S.NODES, *S.RELATIONSHIPS])) & set(S.DERIVED) == set()

    def test_the_derive_is_a_neo4j_stage_of_the_build(self):
        assert "derive" in NEO4J_STAGES  # its errors are reported as a status code, never text


class TestMarker:
    def test_a_derive_marker_makes_the_graph_busy_without_a_lock(self, paths):
        assert state.history_busy(paths) is False
        state.mark_unfinished(paths, "derive", "B1")
        assert state.history_busy(paths) is True
        assert state.unfinished_next_to(state.lock_path(paths)) is True  # what Ask's guard reads
        state.clear_unfinished(paths)
        assert state.history_busy(paths) is False
        assert state.unfinished_next_to(state.lock_path(paths)) is False

    def test_the_marker_note(self, paths):
        state.mark_unfinished(paths, "derive", "B1")
        note = state.read_unfinished(paths)
        assert note["what"] == "derive" and note["build_id"] == "B1" and note["season"] is None
        assert isinstance(note["pid"], int) and note["started"].startswith("2026-")

    @pytest.mark.parametrize(
        ("note", "kind"),
        [({"what": "derive"}, "derive"), ({"what": "build"}, "build"), ({}, "build"),
         ({"what": "append", "season": 2026}, "append --season 2026")],
    )  # fmt: skip
    def test_the_advice_for_each_kind_of_marker(self, note, kind):
        advice = state.unfinished_advice(note)
        assert advice.endswith(f"run `uv run nfl history-graph {kind}`")
        word = kind.split()[0]
        assert "didn't finish" in advice and word in advice


class TestAsksGuard:
    """Ask's `Sources.history_busy` (what stops a history question) while a derive is unfinished."""

    def sources(self, paths):
        from nflengine.ask.guard import Sources

        return Sources(sandbox=None, driver=None, history_lock=state.lock_path(paths))

    def test_a_derive_marker_stops_the_history_questions(self, paths):
        src = self.sources(paths)
        assert src.history_busy() is False
        state.mark_unfinished(paths, "derive", "B1")
        assert src.history_busy() is True  # Ask doesn't read a half-written graph
        state.clear_unfinished(paths)
        assert src.history_busy() is False

    def test_a_running_derive_holds_the_lock_and_stops_them_too(self, paths):
        from nflengine.ops.lock import run_lock

        src = self.sources(paths)
        with run_lock(state.lock_path(paths), "nfl history-graph derive"):
            assert src.history_busy() is True
        assert src.history_busy() is False
