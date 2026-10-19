"""`run_build` end to end with fakes (AE02): a fake Docker runner that records every command, a
fake Neo4j that "imports" the CSVs the build wrote, and the fixture league. Nothing reaches
Docker, Neo4j, W&B, the network or the real data root."""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest
from history_fakes import (
    FakeDocker,
    FakeGraph,
    FakeNeo4jError,
    assert_commands_safe,
    is_compose,
    is_import,
    is_install,
)
from history_world import install_world, load

from nflengine.history_graph import build as B
from nflengine.history_graph import schema as S
from nflengine.history_graph import state
from nflengine.history_graph.config import HistoryConfig
from nflengine.history_graph.docker import Done
from nflengine.ops.lock import lock_path, run_lock

CFG = HistoryConfig()
SENTINEL = "SENTINEL-IMPORT-OUTPUT-77c1"
OLD_BUILD = {
    "status": "ok",
    "build_id": "20250101T000000Z",
    "first_season": 2024,
    "through_season": 2024,
}


def written_from_dir(d):
    """The `written` dict `write_csvs` returns, rebuilt from the folder."""
    out = {}
    for name in S.NODES:
        out[name] = {"file": f"nodes_{name}.csv", "kind": "nodes"}
    for name in S.RELATIONSHIPS:
        out[name] = {"file": f"rels_{name}.csv", "kind": "relationships"}
    for info in out.values():
        assert (d / info["file"]).exists(), info["file"]
    return out


class Rig(SimpleNamespace):
    def run(self, **kw):
        kw.setdefault("use_wandb", False)
        kw.setdefault("log", self.logs.append)
        kw.setdefault("first_season", 2024)
        kw.setdefault("through_season", 2024)
        return B.run_build(
            paths=self.paths, cfg=CFG, runner=self.docker, driver_factory=self.factory, **kw
        )

    @property
    def import_dir(self):
        return self.paths.neo4j_history / "import"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    w = install_world(tmp_path, monkeypatch)
    fd = FakeDocker().on(is_import, Done(0, "IMPORT DONE\n"))
    # Bolt "answers" once `docker compose ... up` has been seen
    monkeypatch.setattr(
        "nflengine.history_graph.client.history_up",
        lambda cfg=None, timeout=5.0: fd.compose_up_seen,
    )
    monkeypatch.setattr(
        "nflengine.settings.get_config",
        lambda: SimpleNamespace(seasons=SimpleNamespace(current=2026), history_graph={}),
    )
    rig = Rig(world=w, paths=w.paths, docker=fd, logs=[], graphs=[])

    def factory():
        # the "server" holds what the import would have loaded: the CSVs the build just wrote
        g = FakeGraph().load_csv(rig.import_dir, written_from_dir(rig.import_dir))
        rig.graphs.append(g)
        return g

    rig.factory = factory
    return rig


class TestSuccessfulBuild:
    def test_the_document(self, rig):
        doc = rig.run()
        assert doc["status"] == "ok" and doc["through_season"] == 2024 and doc["seasons"] == [2024]
        expected = load(rig.world, seasons=(2024,)).expected_counts()
        assert doc["expected"] == expected and doc["counts"] == expected
        assert doc["dropped"] == {"OFFENSE": 1, "OFF_COORD": 1, "participation_plays_not_in_pbp": 1}
        assert doc["schema_version"] == B.SCHEMA_VERSION and doc["csv_mb"] >= 0
        assert doc["import_log"] == f"import-{doc['build_id']}.log"
        assert doc["versions"] == {"image": "neo4j:5.26-community"}  # no server to ask: image only
        assert set(doc["seconds"]) == {
            "export",
            "csv",
            "import",
            "stop",
            "install",
            "start",
            "schema",
            "check",
            "append",
            "total",
        }
        assert doc["wandb_url"] is None and doc["built_at"]

    def test_the_stages_run_in_order_and_every_command_is_safe(self, rig):
        rig.run()
        calls = rig.docker.calls
        i_import = rig.docker.first_index(is_import)
        i_stop = rig.docker.first_index(lambda c: is_compose(c) and "stop" in c)
        i_install = rig.docker.first_index(is_install)
        i_up = rig.docker.first_index(lambda c: is_compose(c) and "up" in c)
        i_drop = max(i for i, c in enumerate(calls) if c[:3] == ["docker", "volume", "rm"])
        assert i_import < i_stop < i_install < i_drop < i_up
        assert_commands_safe(calls, rig.paths, CFG)

    def test_the_history_container_is_stopped_only_after_the_import_succeeded(self, rig):
        rig.run()
        stops = rig.docker.where("stop")
        assert len(stops) == 1 and stops[0][-1] == "neo4j-history"
        assert stops[0][-3:-1] == ["--timeout", "120"]

    def test_build_json_on_disk_is_the_document(self, rig):
        doc = rig.run()
        on_disk = json.loads(state.build_path(rig.paths).read_text(encoding="utf-8"))
        assert on_disk["status"] == "ok" and on_disk["build_id"] == doc["build_id"]
        assert on_disk["counts"] == doc["counts"] and on_disk["append"] == doc["append"]
        assert state.read_build(rig.paths)["build_id"] == doc["build_id"]
        assert not (rig.paths.neo4j_history / "build_failed.json").exists()

    def test_the_csvs_and_the_import_log_are_where_they_should_be(self, rig):
        doc = rig.run()
        names = {p.name for p in rig.import_dir.glob("*.csv")}
        assert len(names) == len(S.NODES) + len(S.RELATIONSHIPS)
        log = rig.paths.neo4j_history / "logs" / doc["import_log"]
        assert log.read_text(encoding="utf-8") == "IMPORT DONE\n"

    def test_the_current_season_is_appended_after_the_import(self, rig):
        doc = rig.run()
        assert doc["append"] == {"season": 2026, "through_week": 2, "plays": 8, "games": 2}
        g = rig.graphs[0]
        assert doc["counts_after_append"] == g.counts()
        assert g.counts()["Play"] == doc["counts"]["Play"] + 8
        assert "append_error" not in doc
        asof = state.history_asof(rig.paths)
        assert asof["label"] == "history graph 2024-2024 + 2026 week 2"

    def test_the_schema_is_applied_before_the_check(self, rig):
        rig.run()
        assert rig.graphs[0].schema_statements
        assert rig.graphs[0].closed  # the build closes the driver it opened

    def test_the_lock_is_released(self, rig):
        rig.run()
        assert not state.history_busy(rig.paths)

    def test_no_append_leaves_the_current_season_out(self, rig):
        doc = rig.run(append_current=False)
        assert "append" not in doc and doc["counts_after_append"] is None
        assert "append" not in doc["seconds"]
        assert rig.graphs[0].counts() == doc["counts"]

    def test_a_rebuild_drops_the_previous_builds_append(self, rig, monkeypatch):
        stamps = iter(
            [
                dt.datetime(2026, 10, 18, 12, 0, 0, tzinfo=dt.UTC),
                dt.datetime(2026, 10, 18, 12, 0, 5, tzinfo=dt.UTC),
            ]
            + [dt.datetime(2026, 10, 18, 12, 1, 0, tzinfo=dt.UTC)] * 40
        )
        monkeypatch.setattr(B, "utc_now", lambda: next(stamps))
        first = rig.run()
        assert state.history_asof(rig.paths)["label"].endswith("+ 2026 week 2")
        second = rig.run(append_current=False)
        assert second["build_id"] != first["build_id"]
        assert state.history_asof(rig.paths)["label"] == "history graph 2024-2024"  # no append
        assert state.read_append(rig.paths) is None  # append.json still there, but another build's


class TestAppendStageNeverFailsTheBuild:
    def test_no_plays_for_the_current_season_yet(self, rig):
        (rig.paths.curated / "plays" / "season=2026.parquet").unlink()
        doc = rig.run()
        assert doc["status"] == "ok" and doc["append"] is None and "append_error" not in doc
        assert state.read_build(rig.paths)["status"] == "ok"
        assert state.history_asof(rig.paths)["label"] == "history graph 2024-2024"

    def test_an_append_that_fails_is_reported_and_the_build_stays_ok(self, rig, monkeypatch):
        orig = rig.factory

        def factory():
            g = orig()
            g.fail_after_writes = 0  # the first append write fails
            return g

        rig.factory = factory
        doc = rig.run()
        assert doc["status"] == "ok" and doc["append"] is None
        assert doc["append_error"] == "FakeNeo4jError: Neo.TransientError.General.TestFailure"
        assert state.read_build(rig.paths)["append_error"] == doc["append_error"]
        assert any("append failed" in x and "nfl history-graph append" in x for x in rig.logs)
        assert not any("must never reach a log" in x for x in rig.logs)
        assert doc["counts_after_append"] is not None

    def test_a_refused_append_keeps_its_fixed_words(self, rig, monkeypatch):
        from nflengine.history_graph import append as A

        def refuse(*a, **k):
            raise A.HistoryAppendError("the append says no, in fixed words")

        monkeypatch.setattr(A, "run_append", refuse)
        doc = rig.run()
        assert doc["status"] == "ok" and doc["append_error"] == "the append says no, in fixed words"

    def test_the_build_can_be_appended_to_afterwards(self, rig):
        from nflengine.history_graph import append as A

        (rig.paths.curated / "plays" / "season=2026.parquet").rename(
            rig.paths.root / "hold.parquet"
        )
        rig.run()
        (rig.paths.root / "hold.parquet").rename(
            rig.paths.curated / "plays" / "season=2026.parquet"
        )
        doc = A.run_append(
            2026,
            paths=rig.paths,
            cfg=CFG,
            driver=rig.graphs[0],
            use_wandb=False,
            log=lambda *_: None,
        )
        assert doc["status"] == "ok"


class TestFailures:
    def seed_old_build(self, rig):
        p = state.build_path(rig.paths)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(OLD_BUILD), encoding="utf-8")
        return p.read_text(encoding="utf-8")

    def test_a_failing_import_leaves_the_old_store_and_the_old_build_json(self, rig):
        old_text = self.seed_old_build(rig)
        rig.docker.answers.clear()
        rig.docker.on(is_import, Done(70, f"Error: {SENTINEL}\nstart:null ... missing START_ID"))
        with pytest.raises(B.HistoryBuildError) as e:
            rig.run()
        assert e.value.stage == "import" and "exited with code 70" in str(e.value)
        assert SENTINEL not in str(e.value)
        # the old build.json is untouched and still counts
        assert state.build_path(rig.paths).read_text(encoding="utf-8") == old_text
        assert state.read_build(rig.paths)["build_id"] == OLD_BUILD["build_id"]
        failed = json.loads(
            (rig.paths.neo4j_history / "build_failed.json").read_text(encoding="utf-8")
        )
        assert failed["status"] == "failed" and failed["failed_stage"] == "import"
        assert "exited with code 70" in failed["error"]
        assert SENTINEL not in json.dumps(failed)  # the output stays in the log file
        log = next((rig.paths.neo4j_history / "logs").glob("import-*.log"))
        assert SENTINEL in log.read_text(encoding="utf-8")
        # the old store was never touched: no stop, no install, no start
        assert not rig.docker.where("stop") and not rig.docker.where("up")
        assert not [c for c in rig.docker.calls if is_install(c)]
        assert_commands_safe(rig.docker.calls, rig.paths, CFG)

    def test_the_failed_document_carries_what_was_known(self, rig):
        rig.docker.answers.clear()
        rig.docker.on(is_import, Done(70, "x"))
        with pytest.raises(B.HistoryBuildError):
            rig.run()
        failed = json.loads(
            (rig.paths.neo4j_history / "build_failed.json").read_text(encoding="utf-8")
        )
        assert failed["expected"]["Play"] == 22 and failed["seasons"] == [2024]
        assert set(failed["seconds"]) >= {"export", "csv", "total"}

    def test_a_failing_stop_is_a_failure_before_the_store_is_replaced(self, rig):
        old_text = self.seed_old_build(rig)
        rig.docker.on(lambda a: is_compose(a) and "stop" in a, Done(1, SENTINEL))
        with pytest.raises(B.HistoryBuildError) as e:
            rig.run()
        assert e.value.stage == "stop" and SENTINEL not in str(e.value)
        assert state.build_path(rig.paths).read_text(encoding="utf-8") == old_text
        assert (rig.paths.neo4j_history / "build_failed.json").exists()
        assert not [c for c in rig.docker.calls if is_install(c)]

    def test_a_failing_install_marks_build_json_failed_because_the_old_store_may_be_gone(self, rig):
        self.seed_old_build(rig)
        rig.docker.on(is_install, Done(1, SENTINEL))
        with pytest.raises(B.HistoryBuildError) as e:
            rig.run()
        assert e.value.stage == "install" and "copying the new store to D: failed" in str(e.value)
        assert state.read_build(rig.paths) is None  # build.json no longer describes the store
        doc = json.loads(state.build_path(rig.paths).read_text(encoding="utf-8"))
        assert doc["status"] == "failed" and doc["failed_stage"] == "install"
        assert not (rig.paths.neo4j_history / "build_failed.json").exists()
        assert not rig.docker.where("up")  # nothing was started on a half-installed store

    def test_a_container_that_does_not_start_after_the_install(self, rig, monkeypatch):
        monkeypatch.setattr(
            "nflengine.history_graph.client.history_up", lambda cfg=None, timeout=5.0: False
        )
        monkeypatch.setattr(
            B.D,
            "start",
            lambda cfg, log, runner: SimpleNamespace(
                ok=False, detail="started, but Bolt didn't answer within 300 s", seconds=1.0
            ),
        )
        with pytest.raises(B.HistoryBuildError) as e:
            rig.run()
        assert e.value.stage == "start" and "Bolt didn't answer" in str(e.value)
        assert state.read_build(rig.paths) is None

    def test_a_count_mismatch_fails_the_build_and_names_the_labels(self, rig):
        orig = rig.factory

        def factory():
            g = orig()
            del g.nodes["Play"][next(iter(g.nodes["Play"]))]
            return g

        rig.factory = factory
        with pytest.raises(B.HistoryBuildError) as e:
            rig.run()
        assert e.value.stage == "check" and str(e.value).endswith("for Play")
        doc = json.loads(state.build_path(rig.paths).read_text(encoding="utf-8"))
        assert doc["status"] == "failed" and doc["failed_stage"] == "check"
        assert doc["mismatch"] == {"Play": {"neo4j": 21, "expected": 22}}
        assert state.read_build(rig.paths) is None  # Ask stops using a graph that doesn't match

    def test_a_server_error_in_the_schema_stage_reports_a_status_code_not_text(self, rig):
        class Boom(FakeNeo4jError):
            pass

        def factory():
            raise Boom("Neo.ClientError.Security.Unauthorized")

        rig.factory = factory
        with pytest.raises(B.HistoryBuildError) as e:
            rig.run()
        assert e.value.stage == "schema"
        assert "must never reach a log" not in str(e.value) and "Boom" in str(e.value)

    def test_a_missing_password_is_named_but_never_shown(self, rig):
        def factory():
            raise RuntimeError("NEO4J_PASSWORD is not set in the env file.")

        rig.factory = factory
        with pytest.raises(B.HistoryBuildError) as e:
            rig.run()
        assert str(e.value) == "NEO4J_PASSWORD is not set"

    def test_an_export_failure_runs_no_docker_command_and_writes_no_csv(self, rig):
        (rig.paths.curated / "plays" / "season=2024.parquet").unlink()
        with pytest.raises(B.HistoryBuildError) as e:
            rig.run()
        assert e.value.stage == "export" and str(e.value).startswith("FileNotFoundError")
        assert rig.docker.calls == []
        assert not list(rig.import_dir.glob("*.csv")) if rig.import_dir.exists() else True
        failed = json.loads(
            (rig.paths.neo4j_history / "build_failed.json").read_text(encoding="utf-8")
        )
        assert failed["failed_stage"] == "export"

    def test_an_export_error_is_not_called_a_neo4j_error(self, rig):
        (rig.paths.curated / "plays" / "season=2024.parquet").unlink()
        with pytest.raises(B.HistoryBuildError) as e:
            rig.run()
        assert "Neo4j" not in str(e.value)

    def test_a_blank_key_stops_the_build_before_anything_is_written(self, rig):
        rig.world.teams.append({"team": " ", "team_name": "Nobody"})
        rig.world.write()
        with pytest.raises(B.HistoryBuildError) as e:
            rig.run()
        assert e.value.stage == "export" and rig.docker.calls == []
        assert not rig.import_dir.exists() or not list(rig.import_dir.glob("*.csv"))

    def test_the_lock_is_released_after_a_failure(self, rig):
        rig.docker.answers.clear()
        rig.docker.on(is_import, Done(70, "x"))
        with pytest.raises(B.HistoryBuildError):
            rig.run()
        assert not state.history_busy(rig.paths)


class TestRefusals:
    def test_a_through_season_that_is_not_finished(self, rig):
        with pytest.raises(B.HistoryBuildError) as e:
            rig.run(through_season=2026)
        assert e.value.stage == "args" and "finished season" in str(e.value)
        assert rig.docker.calls == []

    def test_a_first_season_after_the_last(self, rig):
        with pytest.raises(B.HistoryBuildError) as e:
            rig.run(first_season=2025, through_season=2024)
        assert e.value.stage == "args" and "after 2024" in str(e.value)

    def test_the_weekly_lock_blocks_the_build(self, rig):
        with (
            run_lock(lock_path(rig.paths.runs), "nfl weekly run --auto"),
            pytest.raises(B.HistoryBuildError) as e,
        ):
            rig.run()
        assert e.value.stage == "lock" and "weekly lock" in str(e.value)
        assert rig.docker.calls == []

    def test_another_build_or_append_blocks_the_build(self, rig):
        with (
            run_lock(state.lock_path(rig.paths), "nfl history-graph append"),
            pytest.raises(B.HistoryBuildError) as e,
        ):
            rig.run()
        assert (
            e.value.stage == "lock"
            and "another history-graph build or append is running" in str(e.value)
        )
        assert rig.docker.calls == []

    def test_the_build_holds_the_history_lock_at_every_docker_command(self, rig):
        busy = []
        rig.docker.on(lambda a: busy.append(state.history_busy(rig.paths)) or False, Done(0))
        rig.run()
        assert busy and all(busy)


# ---- W&B -------------------------------------------------------------------------------------


class FakeRun:
    def __init__(self, **kw):
        self.kw = kw
        self.logged: list[dict] = []
        self.summary_data: dict = {}
        self.summary = SimpleNamespace(update=lambda d: self.summary_data.update(d))
        self.url = "https://wandb.example/build"
        self.exit_code = "unset"

    def log(self, d):
        self.logged.append(d)

    def finish(self, exit_code=None):
        self.exit_code = exit_code


class TestWandb:
    def patch(self, monkeypatch):
        runs: list[FakeRun] = []
        monkeypatch.setattr(
            "nflengine.tracking.init_run", lambda **kw: runs.append(FakeRun(**kw)) or runs[-1]
        )
        monkeypatch.setattr("nflengine.tracking.git_commit", lambda: "abc1234")
        return runs

    def test_a_good_build_logs_its_stages_and_counts(self, rig, monkeypatch):
        runs = self.patch(monkeypatch)
        doc = rig.run(use_wandb=True, launched_by="agent")
        (run,) = runs
        assert run.kw["job_type"] == "history-graph-build" and run.kw["group"] == "history-graph"
        assert (
            run.kw["launched_by"] == "agent" and run.kw["name"] == "history-graph-build-2024-2024"
        )
        assert set(run.kw["config"]) == {
            "first_season",
            "through_season",
            "build_id",
            "schema_version",
            "git_commit",
        }
        logged = {k for d in run.logged for k in d}
        assert {
            "time/export",
            "time/csv",
            "time/import",
            "time/install",
            "time/start",
            "time/schema",
            "csv_mb",
        } <= logged
        assert run.summary_data["status"] == "ok" and run.summary_data["count/Play"] == 22
        assert run.summary_data["dropped/OFFENSE"] == 1
        assert run.exit_code == 0 and doc["wandb_url"] == "https://wandb.example/build"

    def test_a_failed_build_finishes_the_run_as_failed(self, rig, monkeypatch):
        runs = self.patch(monkeypatch)
        rig.docker.answers.clear()
        rig.docker.on(is_import, Done(70, "x"))
        with pytest.raises(B.HistoryBuildError):
            rig.run(use_wandb=True)
        (run,) = runs
        assert run.exit_code == 1 and run.summary_data["status"] == "failed"
        assert run.summary_data["failed_stage"] == "import"

    def test_smoke_runs_go_to_the_smoke_group(self, rig, monkeypatch):
        runs = self.patch(monkeypatch)
        rig.run(use_wandb=True, smoke=True)
        assert runs[0].kw["group"] == "history-graph-smoke" and "smoke" in runs[0].kw["tags"]

    def test_a_broken_wandb_never_sinks_the_build(self, rig, monkeypatch):
        def boom(**kw):
            raise RuntimeError("wandb is down SECRET-LOG-TEXT")

        monkeypatch.setattr("nflengine.tracking.init_run", boom)
        monkeypatch.setattr("nflengine.tracking.git_commit", lambda: "x")
        doc = rig.run(use_wandb=True)
        assert doc["status"] == "ok"
        assert any("W&B off for this build (RuntimeError)" in x for x in rig.logs)
        assert not any("SECRET-LOG-TEXT" in x for x in rig.logs)

    def test_a_run_that_cannot_log_is_ignored(self, rig, monkeypatch):
        runs = self.patch(monkeypatch)

        def bad_log(self, d):
            raise OSError("disk full")

        monkeypatch.setattr(FakeRun, "log", bad_log)
        assert rig.run(use_wandb=True)["status"] == "ok" and runs


class TestHelpers:
    def test_schema_statements_split_on_semicolons_and_drop_comments(self):
        assert B.schema_statements("// c\nCREATE A;\n// d\nCREATE B;\n") == ["CREATE A", "CREATE B"]

    def test_apply_schema_runs_each_statement_then_waits_for_the_indexes(self):
        g = FakeGraph()
        B.apply_schema(g)
        assert len(g.schema_statements) == len(B.schema_statements())
        assert g.queries[-1].startswith("CALL db.awaitIndexes")

    def test_utc_now_is_a_whole_second_utc_time(self):
        t = B.utc_now()
        assert t.tzinfo is not None and t.microsecond == 0 and t.utcoffset() == dt.timedelta(0)

    def test_weekly_lock_held(self, tmp_path):
        from nflengine.paths import DataPaths

        paths = DataPaths(tmp_path)
        assert B.weekly_lock_held(paths) is False
        with run_lock(lock_path(paths.runs), "weekly"):
            assert B.weekly_lock_held(paths) is True
        assert B.weekly_lock_held(paths) is False

    def test_write_json_is_atomic_and_readable(self, tmp_path):
        p = tmp_path / "sub" / "x.json"
        B._write_json(p, {"a": 1, "when": dt.datetime(2026, 1, 1, tzinfo=dt.UTC)})
        assert json.loads(p.read_text(encoding="utf-8"))["a"] == 1
        assert not list(p.parent.glob(".*.tmp"))


# ---- Sol's review (AE02): the unfinished marker around the store's replacement ---------------


class TestUnfinishedMarker:
    def seed_old_build(self, rig):
        p = state.build_path(rig.paths)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(OLD_BUILD), encoding="utf-8")
        return p.read_text(encoding="utf-8")

    def test_a_good_build_leaves_no_marker(self, rig):
        rig.run()
        assert state.read_unfinished(rig.paths) is None
        assert not state.history_busy(rig.paths)

    def test_a_failed_install_leaves_the_marker_so_the_graph_counts_as_busy(self, rig):
        self.seed_old_build(rig)
        rig.docker.on(is_install, Done(1, SENTINEL))
        with pytest.raises(B.HistoryBuildError):
            rig.run()
        assert state.read_unfinished(rig.paths)["what"] == "build"
        assert state.history_busy(rig.paths)

    def test_a_build_killed_mid_install_leaves_the_marker_and_the_old_build_json(
        self, rig, monkeypatch
    ):
        # an abrupt end (a reboot, a kill) runs no exception handler: the old build.json still
        # says ok, and only the marker written before the install tells Ask the store changed
        old_text = self.seed_old_build(rig)

        def killed(*a, **k):
            raise KeyboardInterrupt

        monkeypatch.setattr(B.D, "install_store", killed)
        with pytest.raises(KeyboardInterrupt):
            rig.run()
        assert state.build_path(rig.paths).read_text(encoding="utf-8") == old_text
        assert state.read_unfinished(rig.paths)["what"] == "build"
        assert state.history_busy(rig.paths)

    def test_no_marker_before_the_store_is_touched(self, rig):
        rig.docker.answers.clear()
        rig.docker.on(is_import, Done(70, "no"))
        with pytest.raises(B.HistoryBuildError):
            rig.run()
        assert state.read_unfinished(rig.paths) is None  # the old store is as it was

    def test_a_failed_append_stage_leaves_an_append_marker_on_a_good_build(self, rig):
        orig = rig.factory

        def factory():
            g = orig()
            g.fail_after_writes = 0
            return g

        rig.factory = factory
        doc = rig.run()
        assert doc["status"] == "ok" and doc["append_error"]
        assert state.read_unfinished(rig.paths)["what"] == "append"
        assert state.history_busy(rig.paths)  # Ask waits for a good append
