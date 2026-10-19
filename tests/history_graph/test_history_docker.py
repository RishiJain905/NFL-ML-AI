"""Docker safety for the history graph (AE02): every command goes through a fake runner that
records its arguments. The rules (CLAUDE.md, the phase file): every `docker compose` call has
`--profile history` and ends at the history service; nothing runs `down`, `compose config`,
`docker inspect`, a bare `up`, or touches the weekly container, its folders or its ports; the
only volume removed is the staging volume; failures report fixed text, never command output."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from history_fakes import (
    FakeDocker,
    assert_commands_safe,
    is_compose,
    is_import,
    is_install,
)

from nflengine.history_graph import docker as D
from nflengine.history_graph.config import HistoryConfig
from nflengine.history_graph.docker import Done
from nflengine.paths import DataPaths

CFG = HistoryConfig()
SENTINEL = "SENTINEL-COMMAND-OUTPUT-3f9a"
FILES = {
    "Play": {"file": "nodes_Play.csv", "kind": "nodes", "rows": 3},
    "Player": {"file": "nodes_Player.csv", "kind": "nodes", "rows": 2},
    "ON_FIELD": {"file": "rels_ON_FIELD.csv", "kind": "relationships", "rows": 9},
    "PLAYED_FOR": {"file": "rels_PLAYED_FOR.csv", "kind": "relationships", "rows": 4},
}


@pytest.fixture
def paths(tmp_path) -> DataPaths:
    return DataPaths(tmp_path / "root")


def sleeps():
    got: list[float] = []
    return got, got.append


# ---- start -----------------------------------------------------------------------------------


class TestStart:
    def test_already_up_runs_no_command(self):
        fd = FakeDocker()
        res = D.start(CFG, lambda *_: None, runner=fd, up=lambda: True)
        assert (res.ok, res.action) == (True, "already_up")
        assert fd.calls == []

    def test_starts_only_the_history_service_under_its_profile_and_waits(self):
        fd = FakeDocker()
        slept, sleep = sleeps()
        polls = iter([False, False, False, True])  # up before; after the compose call: 3rd poll
        logs: list[str] = []

        def up():
            return fd.compose_up_seen and next(polls, True) if fd.compose_up_seen else False

        res = D.start(CFG, logs.append, runner=fd, up=up, sleep=sleep, timeout_s=60)
        assert (res.ok, res.action) == (True, "started")
        compose = [c for c in fd.calls if is_compose(c)]
        assert compose == [
            ["docker", "compose", "--profile", "history", "up", "-d", "neo4j-history"]
        ]
        assert fd.calls[0][:2] == ["docker", "version"]  # the engine check comes first
        assert slept and set(slept) == {3}
        assert_commands_safe(fd.calls, DataPaths(Path("x")), CFG)

    def test_never_a_bare_up_a_down_or_a_config(self):
        fd = FakeDocker()
        D.start(CFG, lambda *_: None, runner=fd, up=lambda: fd.compose_up_seen)
        for c in fd.calls:
            assert "down" not in c and "config" not in c and "inspect" not in c
            if is_compose(c):
                assert "--profile" in c and c[-1] == "neo4j-history" and "neo4j" not in c

    def test_the_docker_engine_being_down_is_reported_in_fixed_words(self):
        fd = FakeDocker().on(lambda a: a[:2] == ["docker", "version"], Done(1, SENTINEL))
        res = D.start(CFG, lambda *_: None, runner=fd, up=lambda: False)
        assert not res.ok and res.action == "failed"
        assert "Docker engine isn't running" in res.detail and SENTINEL not in res.detail
        assert len(fd.calls) == 1  # nothing else was tried

    def test_a_failing_compose_up_reports_its_exit_code_not_its_output(self):
        fd = FakeDocker().on(lambda a: "up" in a, Done(3, SENTINEL))
        logs: list[str] = []
        res = D.start(CFG, logs.append, runner=fd, up=lambda: False)
        assert (
            not res.ok and res.detail == "`docker compose up` for neo4j-history exited with code 3"
        )
        assert SENTINEL not in res.detail and not any(SENTINEL in x for x in logs)

    def test_bolt_not_answering_in_time_fails_with_the_limit(self):
        fd = FakeDocker()
        res = D.start(
            CFG, lambda *_: None, runner=fd, up=lambda: False, sleep=lambda s: None, timeout_s=0
        )
        assert not res.ok and "Bolt didn't answer within 0 s" in res.detail

    def test_the_default_wait_comes_from_the_config(self):
        fd = FakeDocker()
        cfg = HistoryConfig(start_timeout_s=0)
        res = D.start(cfg, lambda *_: None, runner=fd, up=lambda: False, sleep=lambda s: None)
        assert "within 0 s" in res.detail

    def test_a_compose_file_without_the_profile_stops_everything(self, monkeypatch):
        fd = FakeDocker()

        def refuse(cfg):
            raise D.HistoryDockerError("`neo4j-history` isn't under the `history` profile")

        monkeypatch.setattr(D, "compose_service", refuse)
        with pytest.raises(D.HistoryDockerError, match="profile"):
            D.start(CFG, lambda *_: None, runner=fd, up=lambda: False)
        assert not [c for c in fd.calls if is_compose(c)]  # no compose command was built

    def test_the_compose_up_has_a_generous_timeout(self):
        fd = FakeDocker()
        D.start(CFG, lambda *_: None, runner=fd, up=lambda: fd.compose_up_seen)
        i = fd.first_index(is_compose)
        assert fd.timeouts[i] >= 300


# ---- stop ------------------------------------------------------------------------------------


class TestStop:
    def test_a_running_container_is_stopped_with_a_grace_period_by_service_name(self):
        fd = FakeDocker()
        res = D.stop(CFG, runner=fd)
        assert (res.ok, res.action) == (True, "stopped")
        assert [c for c in fd.calls if is_compose(c)] == [
            [
                "docker",
                "compose",
                "--profile",
                "history",
                "stop",
                "--timeout",
                "120",
                "neo4j-history",
            ]
        ]
        assert D.STOP_TIMEOUT_S == 120
        assert fd.timeouts[fd.first_index(is_compose)] > D.STOP_TIMEOUT_S  # the call outlasts it
        assert_commands_safe(fd.calls, DataPaths(Path("x")), CFG)

    def test_the_state_probe_is_a_filtered_docker_ps(self):
        fd = FakeDocker()
        D.stop(CFG, runner=fd)
        assert fd.calls[0] == [
            "docker",
            "ps",
            "-a",
            "--filter",
            "name=^/nfl-neo4j-history$",
            "--format",
            "{{.State}}",
        ]

    @pytest.mark.parametrize("state", ["missing", "exited", "created"])
    def test_a_container_that_is_not_running_needs_no_stop(self, state):
        fd = FakeDocker()
        fd.container = state
        res = D.stop(CFG, runner=fd)
        assert (res.ok, res.action) == (True, "not_running")
        assert not [c for c in fd.calls if is_compose(c)]

    def test_a_failing_stop_reports_the_exit_code_only(self):
        fd = FakeDocker().on(is_compose, Done(1, SENTINEL))
        res = D.stop(CFG, runner=fd)
        assert not res.ok and res.detail == "`docker compose stop` exited with code 1"
        assert SENTINEL not in res.detail

    def test_unreachable_docker_still_tries_the_stop(self):
        fd = FakeDocker().on(lambda a: a[:2] == ["docker", "ps"], Done(1, SENTINEL))
        res = D.stop(CFG, runner=fd)
        assert [c for c in fd.calls if is_compose(c)]  # state "unknown": try anyway
        assert res.ok

    def test_stop_checks_the_compose_file_first(self, monkeypatch):
        fd = FakeDocker()

        def refuse(cfg):
            raise D.HistoryDockerError("`neo4j-history` isn't under the `history` profile")

        monkeypatch.setattr(D, "compose_service", refuse)
        with pytest.raises(D.HistoryDockerError):
            D.stop(CFG, runner=fd)
        assert fd.calls == []  # not even a docker ps

    def test_a_misconfigured_service_can_no_longer_reach_the_weekly_container(self):
        # the config refuses `service: neo4j`, so no HistoryConfig can ask stop() for it
        with pytest.raises(Exception, match="history graph's own name"):
            HistoryConfig(service="neo4j")


class TestContainerState:
    def test_parses_the_state(self):
        fd = FakeDocker()
        fd.container = "running"
        assert D.container_state(CFG, fd) == "running"
        fd.container = "exited"
        assert D.container_state(CFG, fd) == "exited"

    def test_no_output_is_missing_and_a_failure_is_unknown(self):
        fd = FakeDocker()
        fd.container = "missing"
        assert D.container_state(CFG, fd) == "missing"
        bad = FakeDocker().on(lambda a: a[:2] == ["docker", "ps"], Done(1, "error"))
        assert D.container_state(CFG, bad) == "unknown"

    def test_the_last_line_wins(self):
        fd = FakeDocker().on(lambda a: a[:2] == ["docker", "ps"], Done(0, "warning\nrunning\n"))
        assert D.container_state(CFG, fd) == "running"

    def test_engine_probe(self):
        assert D.docker_engine_up(FakeDocker())
        assert not D.docker_engine_up(FakeDocker().on(lambda a: True, Done(1)))


# ---- the path guard --------------------------------------------------------------------------


class TestHistoryDir:
    def test_the_four_folders_are_inside_the_history_folder(self, paths):
        for sub in ("data", "logs", "import", "plugins"):
            p = D.history_dir(paths, sub)
            assert p == paths.root / "neo4j-history" / sub

    def test_nested_paths_are_fine(self, paths):
        assert D.history_dir(paths, "data/databases") == paths.root / "neo4j-history/data/databases"

    @pytest.mark.parametrize(
        "sub", ["../neo4j", "../neo4j/data", "../neo4j/logs/x", "data/../../neo4j"]
    )
    def test_a_path_into_the_weekly_folder_is_refused(self, paths, sub):
        with pytest.raises(D.HistoryDockerError, match="weekly graph's folder"):
            D.history_dir(paths, sub)

    @pytest.mark.parametrize("sub", ["", ".", "..", "../elsewhere", "../../x", "data/../.."])
    def test_a_path_outside_neo4j_history_is_refused(self, paths, sub):
        with pytest.raises(D.HistoryDockerError, match="outside the history graph's folder"):
            D.history_dir(paths, sub)

    def test_a_data_root_whose_history_folder_is_the_weekly_one_is_refused(self, tmp_path):
        class Wrong(DataPaths):
            @property
            def neo4j_history(self):  # a misconfigured property
                return self.root / "neo4j"

        with pytest.raises(D.HistoryDockerError, match="weekly graph's folder"):
            D.history_dir(Wrong(tmp_path), "data")

    def test_a_history_folder_nested_in_the_weekly_one_is_refused(self, tmp_path):
        class Nested(DataPaths):
            @property
            def neo4j_history(self):
                return self.root / "neo4j" / "history"

        with pytest.raises(D.HistoryDockerError, match="weekly graph's folder"):
            D.history_dir(Nested(tmp_path), "import")


# ---- the import ------------------------------------------------------------------------------


class TestImportArgs:
    def test_every_csv_is_listed_once(self, paths):
        args = D.import_args(CFG, "neo4j:5.26-community", paths.neo4j_history / "import", FILES)
        assert [a for a in args if a.startswith("--nodes=")] == [
            "--nodes=Play=/import/nodes_Play.csv",
            "--nodes=Player=/import/nodes_Player.csv",
        ]
        assert [a for a in args if a.startswith("--relationships=")] == [
            "--relationships=ON_FIELD=/import/rels_ON_FIELD.csv",
            "--relationships=PLAYED_FOR=/import/rels_PLAYED_FOR.csv",
        ]

    def test_the_command(self, paths):
        imp = paths.neo4j_history / "import"
        a = D.import_args(CFG, "img:1", imp, FILES)
        assert a[:8] == [
            "docker",
            "run",
            "--rm",
            "--name",
            "nfl-neo4j-history-import",
            "--entrypoint",
            "neo4j-admin",
            "-v",
        ]
        assert f"{imp.as_posix()}:/import:ro" in a  # CSVs read-only
        assert "nfl-neo4j-history-staging:/data" in a  # the store goes into the staging volume
        i = a.index("img:1")
        assert a[i + 1 : i + 5] == ["database", "import", "full", "neo4j"]
        assert "--overwrite-destination" in a and "--report-file=/data/import.report" in a

    def test_the_import_container_gets_no_environment(self, paths):
        a = D.import_args(CFG, "img:1", paths.neo4j_history / "import", FILES)
        assert not {"-e", "--env", "--env-file", "--network", "-p", "--publish"} & set(a)
        assert not any("NEO4J" in x or "PASSWORD" in x for x in a)

    def test_a_windows_style_path_is_passed_with_forward_slashes(self):
        win = DataPaths(Path("D:/nfl-ml-data"))
        a = D.import_args(CFG, "img:1", win.neo4j_history / "import", FILES)
        assert "D:/nfl-ml-data/neo4j-history/import:/import:ro" in a
        assert all("\\" not in x for x in a)

    def test_the_staging_volume_follows_the_config(self, paths):
        cfg = HistoryConfig(staging_volume="nfl-neo4j-history-other")
        a = D.import_args(cfg, "img:1", paths.neo4j_history / "import", FILES)
        assert "nfl-neo4j-history-other:/data" in a


class TestImportFull:
    def test_success_order_log_and_safety(self, paths):
        fd = FakeDocker().on(is_import, Done(0, "IMPORT DONE in 12s\n"))
        res = D.import_full(paths, CFG, FILES, runner=fd, stamp="20261018T120000Z")
        assert res["log"] == "import-20261018T120000Z.log" and res["seconds"] >= 0
        assert (paths.neo4j_history / "logs" / res["log"]).read_text(encoding="utf-8") == (
            "IMPORT DONE in 12s\n"
        )
        # a leftover container and volume are cleared, a fresh volume made, then the import
        assert fd.calls[0] == ["docker", "rm", "-f", "nfl-neo4j-history-import"]
        assert fd.calls[1] == ["docker", "volume", "rm", "-f", "nfl-neo4j-history-staging"]
        assert fd.calls[2] == ["docker", "volume", "create", "nfl-neo4j-history-staging"]
        assert is_import(fd.calls[3]) and len(fd.calls) == 4
        assert_commands_safe(fd.calls, paths, CFG)

    def test_the_import_gets_a_long_timeout_and_the_default_is_an_hour(self, paths):
        fd = FakeDocker()
        D.import_full(paths, CFG, FILES, runner=fd, stamp="s")
        assert fd.timeouts[fd.first_index(is_import)] == 3600

    def test_a_failing_import_raises_a_fixed_message_and_keeps_the_output_in_the_log(self, paths):
        fd = FakeDocker().on(is_import, Done(70, f"Error: {SENTINEL}\nstart:null is missing"))
        with pytest.raises(D.HistoryDockerError) as e:
            D.import_full(paths, CFG, FILES, runner=fd, stamp="S1")
        assert str(e.value) == (
            "neo4j-admin import exited with code 70 (see import-S1.log in neo4j-history/logs)"
        )
        assert SENTINEL not in str(e.value)
        log = (paths.neo4j_history / "logs" / "import-S1.log").read_text(encoding="utf-8")
        assert SENTINEL in log and "start:null" in log

    def test_no_import_is_attempted_when_the_staging_volume_cant_be_made(self, paths):
        fd = FakeDocker().on(lambda a: a[:3] == ["docker", "volume", "create"], Done(1, SENTINEL))
        with pytest.raises(D.HistoryDockerError, match="couldn't create the staging volume"):
            D.import_full(paths, CFG, FILES, runner=fd, stamp="s")
        assert not [c for c in fd.calls if is_import(c)]

    def test_a_timeout_removes_the_throw_away_container(self, paths):
        fd = FakeDocker().on(is_import, Done(-1))
        with pytest.raises(D.HistoryDockerError, match="code -1"):
            D.import_full(paths, CFG, FILES, runner=fd, stamp="s")
        i = fd.first_index(is_import)
        assert fd.calls[i + 1] == ["docker", "rm", "-f", "nfl-neo4j-history-import"]
        assert_commands_safe(fd.calls, paths, CFG)

    def test_only_the_staging_volume_is_ever_removed(self, paths):
        fd = FakeDocker()
        D.import_full(paths, CFG, FILES, runner=fd, stamp="s")
        D.drop_staging(CFG, runner=fd)
        removed = [c for c in fd.calls if c[:3] == ["docker", "volume", "rm"]]
        assert removed and all(
            c == ["docker", "volume", "rm", "-f", CFG.staging_volume] for c in removed
        )

    def test_the_logs_folder_is_created_inside_the_history_folder(self, paths):
        D.import_full(paths, CFG, FILES, runner=FakeDocker(), stamp="s")
        assert (paths.neo4j_history / "logs").is_dir()
        assert not (paths.root / "neo4j").exists()  # the weekly folder is never created

    def test_a_compose_file_without_the_profile_stops_the_import(self, paths, monkeypatch):
        fd = FakeDocker()

        def refuse(cfg):
            raise D.HistoryDockerError("no profile")

        monkeypatch.setattr(D, "compose_service", refuse)
        with pytest.raises(D.HistoryDockerError):
            D.import_full(paths, CFG, FILES, runner=fd, stamp="s")
        assert fd.calls == []


# ---- the install -----------------------------------------------------------------------------


class TestInstallStore:
    def test_the_command_mounts_staging_read_only_and_the_history_data_folder(self, paths):
        fd = FakeDocker()
        secs = D.install_store(paths, CFG, runner=fd)
        assert secs >= 0
        assert fd.calls[0] == ["docker", "rm", "-f", "nfl-neo4j-history-copy"]
        run = fd.calls[fd.first_index(is_install)]
        assert "nfl-neo4j-history-staging:/staging:ro" in run
        assert f"{(paths.neo4j_history / 'data').as_posix()}:/data" in run
        assert run[run.index("--entrypoint") + 1] == "sh"
        assert run[-1] == D.INSTALL_SCRIPT and run[-2] == "-c"
        assert (paths.neo4j_history / "data").is_dir()
        assert_commands_safe(fd.calls, paths, CFG)

    def test_the_script_replaces_only_the_one_database_and_its_transactions(self):
        script = D.INSTALL_SCRIPT
        assert script.startswith("set -e;")
        assert script.count("rm -rf") == 1
        assert "rm -rf /data/databases/neo4j /data/transactions/neo4j;" in script
        for never in ("system", "dbms", "/logs", "/plugins", "/import", "rm -rf /data ", "*"):
            assert never not in script, never
        assert "cp -a /staging/databases/neo4j /data/databases/neo4j" in script
        assert "cp -a /staging/transactions/neo4j /data/transactions/neo4j" in script

    def test_a_failing_copy_raises_a_fixed_message(self, paths):
        fd = FakeDocker().on(is_install, Done(5, SENTINEL))
        with pytest.raises(D.HistoryDockerError) as e:
            D.install_store(paths, CFG, runner=fd)
        assert str(e.value) == "copying the new store to D: failed (exit code 5)"

    def test_a_timeout_removes_the_copy_container(self, paths):
        fd = FakeDocker().on(is_install, Done(-1))
        with pytest.raises(D.HistoryDockerError):
            D.install_store(paths, CFG, runner=fd)
        i = fd.first_index(is_install)
        assert fd.calls[i + 1] == ["docker", "rm", "-f", "nfl-neo4j-history-copy"]

    def test_the_default_timeout_is_half_an_hour(self, paths):
        fd = FakeDocker()
        D.install_store(paths, CFG, runner=fd)
        assert fd.timeouts[fd.first_index(is_install)] == 1800


class TestDropStaging:
    def test_removes_the_staging_volume_and_nothing_else(self):
        fd = FakeDocker()
        D.drop_staging(CFG, runner=fd)
        assert fd.calls == [["docker", "volume", "rm", "-f", "nfl-neo4j-history-staging"]]


# ---- the default runner ----------------------------------------------------------------------


class TestRun:
    def test_runs_a_list_without_a_shell_from_the_repo_root_and_captures_output(self, monkeypatch):
        seen = {}

        def fake_run(args, **kw):
            seen["args"], seen["kw"] = args, kw
            return subprocess.CompletedProcess(args, 7, stdout="out", stderr="err")

        monkeypatch.setattr(D.subprocess, "run", fake_run)
        done = D.run(("docker", "ps"), 5)
        assert done == Done(7, "outerr")
        assert seen["args"] == ["docker", "ps"] and isinstance(seen["args"], list)
        kw = seen["kw"]
        assert kw["cwd"] == D.REPO_ROOT and kw["capture_output"] is True and kw["timeout"] == 5
        assert not kw.get("shell") and kw["check"] is False
        assert kw["encoding"] == "utf-8" and kw["errors"] == "replace"

    @pytest.mark.parametrize(
        "exc", [FileNotFoundError("docker"), subprocess.TimeoutExpired(["docker"], 5)]
    )
    def test_a_missing_program_or_a_timeout_is_minus_one_never_an_exception(self, monkeypatch, exc):
        def boom(*a, **k):
            raise exc

        monkeypatch.setattr(D.subprocess, "run", boom)
        assert D.run(["docker", "ps"], 1) == Done(-1)

    def test_none_output_is_an_empty_string(self, monkeypatch):
        monkeypatch.setattr(
            D.subprocess,
            "run",
            lambda a, **k: subprocess.CompletedProcess(a, 0, stdout=None, stderr=None),
        )
        assert D.run(["x"], 1) == Done(0, "")

    def test_the_repo_root_is_where_docker_compose_yml_lives(self):
        assert (D.REPO_ROOT / "docker-compose.yml").is_file() and D.COMPOSE_FILE.is_file()


# ---- a whole sequence ------------------------------------------------------------------------


def test_a_whole_start_import_stop_install_start_sequence_is_safe(paths):
    fd = FakeDocker().on(is_import, Done(0, "ok"))
    D.import_full(paths, CFG, FILES, runner=fd, stamp="s")
    D.stop(CFG, runner=fd)
    D.install_store(paths, CFG, runner=fd)
    D.drop_staging(CFG, runner=fd)
    D.start(CFG, lambda *_: None, runner=fd, up=lambda: fd.compose_up_seen)
    assert len(fd.calls) >= 10
    assert_commands_safe(fd.calls, paths, CFG)
    # nothing in the whole sequence names the weekly container, service, folders or ports
    text = "\n".join(" ".join(c) for c in fd.calls)
    for forbidden in ("nfl-neo4j ", "nfl-neo4j\n", " down", "7474:", "7687:", "inspect"):
        assert forbidden not in text + "\n", forbidden
