"""`nfl history-graph ...` (AE02): the CLI group's wiring. Every command stubs the outermost
function it calls (`run_build`, `run_append`, `history_status`, `start`, `stop`), so no test can
reach Docker, Neo4j, W&B or the data root."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from nflengine import cli
from nflengine.history_graph.append import HistoryAppendError
from nflengine.history_graph.build import HistoryBuildError
from nflengine.history_graph.docker import ServiceResult

runner = CliRunner()
GROUP = ["history-graph"]


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch):
    monkeypatch.setattr(cli.console, "_width", 250)


@pytest.fixture(autouse=True)
def _tmp_data_root(tmp_path, monkeypatch):
    # `start` / `stop` take the history lock under the data root (Sol): never the real one
    from nflengine.paths import DataPaths

    root = DataPaths(tmp_path / "data")
    monkeypatch.setattr("nflengine.paths.ensure_data_root", lambda *a, **k: root)
    return root


def invoke(*args):
    return runner.invoke(cli.app, [*GROUP, *args])


def test_the_group_is_registered_with_its_commands():
    top = runner.invoke(cli.app, ["--help"])
    assert "history-graph" in top.output
    r = invoke("--help")
    assert r.exit_code == 0
    for name in ("build", "append", "status", "start", "stop"):
        assert name in r.output
    assert "weekly graph is never touched" in " ".join(r.output.split())


def test_bare_group_prints_help():
    r = invoke()
    assert "Usage" in r.output


# ---- build -----------------------------------------------------------------------------------


BUILD_DOC = {
    "counts": {"Play": 1234, "ON_FIELD": 99},
    "counts_after_append": {"Play": 1300, "ON_FIELD": 99},
    "seconds": {"export": 10.0, "total": 355.0},
    "store_mb": 1100.4,
    "dropped": {"OFFENSE": 2},
    "wandb_url": "https://wandb.example/r",
}


def test_build_passes_its_options_through_and_prints_the_counts(monkeypatch):
    seen = {}

    def fake(through_season, **kw):
        seen["through"], seen["kw"] = through_season, kw
        return BUILD_DOC

    monkeypatch.setattr("nflengine.history_graph.build.run_build", fake)
    r = invoke(
        "build",
        "--through-season",
        "2025",
        "--first-season",
        "2019",
        "--no-append",
        "--no-wandb",
        "--smoke",
        "--launched-by",
        "agent",
    )
    assert r.exit_code == 0, r.output
    assert seen["through"] == 2025
    kw = seen["kw"]
    assert (kw["first_season"], kw["append_current"], kw["use_wandb"], kw["smoke"]) == (
        2019,
        False,
        False,
        True,
    )
    assert kw["launched_by"] == "agent"
    assert "1,300" in r.output and "store 1,100 MB" in r.output  # the after-append counts win
    assert "dropped" in r.output and "https://wandb.example/r" in r.output


def test_build_defaults(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "nflengine.history_graph.build.run_build",
        lambda through_season, **kw: seen.update(t=through_season, kw=kw) or BUILD_DOC,
    )
    r = invoke("build")
    assert r.exit_code == 0
    assert (
        seen["t"] is None
        and seen["kw"]["append_current"] is True
        and seen["kw"]["use_wandb"] is True
    )


def test_a_failed_build_exits_2_and_says_where(monkeypatch):
    def fake(*a, **k):
        raise HistoryBuildError("import", "neo4j-admin import exited with code 70 (see x.log)")

    monkeypatch.setattr("nflengine.history_graph.build.run_build", fake)
    r = invoke("build")
    assert r.exit_code == 2
    assert "failed at import" in r.output and "code 70" in r.output


# ---- append ----------------------------------------------------------------------------------


APPEND_DOC = {
    "season": 2026,
    "weeks": [1, 2, 3],
    "games": 40,
    "plays": 12849,
    "changes": {"Play": {"create": 5}, "TACKLED": {"update": 2}},
    "seconds": {"total": 42.0},
    "wandb_url": None,
}


def test_append_runs_when_the_graph_is_up(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "nflengine.history_graph.client.history_up", lambda cfg=None, timeout=5.0: True
    )
    monkeypatch.setattr(
        "nflengine.history_graph.docker.start", lambda *a, **k: pytest.fail("no start")
    )
    monkeypatch.setattr(
        "nflengine.history_graph.append.run_append",
        lambda season, through_week, **kw: (
            seen.update(s=season, w=through_week, kw=kw) or APPEND_DOC
        ),
    )
    r = invoke("append", "--season", "2026", "--through-week", "3", "--no-wandb")
    assert r.exit_code == 0, r.output
    assert (seen["s"], seen["w"], seen["kw"]["use_wandb"]) == (2026, 3, False)
    assert "2026 weeks 1-3: 40 games, 12,849 plays; 7 writes" in r.output


def test_append_leaves_the_start_to_run_append_under_the_lock(monkeypatch):
    # Sol: the CLI used to start the container before `run_append` took the lock, so it could
    # open a store a build was replacing; now `run_append(start=True)` starts it inside the lock
    seen = {}
    monkeypatch.setattr(
        "nflengine.history_graph.docker.start", lambda *a, **k: pytest.fail("no start here")
    )
    monkeypatch.setattr(
        "nflengine.history_graph.append.run_append",
        lambda *a, **k: seen.update(k) or APPEND_DOC,
    )
    r = invoke("append")
    assert r.exit_code == 0 and seen["start"] is True


def test_append_stops_when_the_container_will_not_start(monkeypatch):
    def refuse(*a, **k):
        raise HistoryAppendError("history graph not reachable: the Docker engine isn't running")

    monkeypatch.setattr("nflengine.history_graph.append.run_append", refuse)
    r = invoke("append")
    assert r.exit_code == 2 and "the Docker engine isn't running" in r.output


@pytest.mark.parametrize("command", ["start", "stop"])
def test_start_and_stop_refuse_while_a_build_or_append_holds_the_lock(
    monkeypatch, _tmp_data_root, command
):
    from nflengine.history_graph.state import lock_path
    from nflengine.ops.lock import run_lock

    monkeypatch.setattr(
        f"nflengine.history_graph.docker.{command}", lambda *a, **k: pytest.fail("must wait")
    )
    lock = lock_path(_tmp_data_root)
    with run_lock(lock, "nfl history-graph build --through-season 2025"):
        r = invoke(command)
    assert r.exit_code == 2 and "a history-graph build or append is running" in r.output


def test_start_holds_the_lock_while_it_starts(monkeypatch, _tmp_data_root):
    from nflengine.history_graph.state import lock_path
    from nflengine.ops.lock import is_locked

    seen = {}

    def fake_start(cfg, log):
        seen["locked"] = is_locked(lock_path(_tmp_data_root))
        return ServiceResult(True, "started", "history graph started")

    monkeypatch.setattr("nflengine.history_graph.docker.start", fake_start)
    assert invoke("start").exit_code == 0 and seen == {"locked": True}


def test_a_refused_append_exits_2_with_its_words(monkeypatch):
    monkeypatch.setattr(
        "nflengine.history_graph.client.history_up", lambda cfg=None, timeout=5.0: True
    )

    def refuse(*a, **k):
        raise HistoryAppendError("the weekly run holds the weekly lock: append after it")

    monkeypatch.setattr("nflengine.history_graph.append.run_append", refuse)
    r = invoke("append")
    assert r.exit_code == 2 and "weekly lock" in r.output


# ---- status, start, stop ---------------------------------------------------------------------


def status_doc(**over):
    base = {
        "container": "running",
        "up": True,
        "bolt_uri": "bolt://localhost:7688",
        "browser_url": "http://localhost:7475",
        "asof": {"label": "history graph 2018-2025 + 2026 week 5"},
        "build": {
            "build_id": "20261018T120000Z",
            "first_season": 2018,
            "through_season": 2025,
            "seconds": {"total": 355.0},
        },
        "last_failed_build": None,
        "append": {
            "season": 2026,
            "through_week": 5,
            "plays": 12849,
            "appended_at": "2026-10-18T13:00:00+00:00",
        },
        "counts": {"Play": 374319, "Player": 7000},
        "store_mb": 1100.0,
        "busy": False,
    }
    return {**base, **over}


def test_status_prints_the_container_the_build_and_the_label(monkeypatch):
    monkeypatch.setattr("nflengine.history_graph.status.history_status", lambda: status_doc())
    r = invoke("status")
    assert r.exit_code == 0, r.output
    assert "container running" in r.output and "bolt://localhost:7688" in r.output
    assert "build 20261018T120000Z: 2018-2025" in r.output and "store 1,100 MB" in r.output
    assert "append: 2026 through week 5" in r.output
    assert "Ask's label: history graph 2018-2025 + 2026 week 5" in r.output
    assert "374,319" in r.output


def test_status_without_a_build_points_at_the_build_command(monkeypatch):
    monkeypatch.setattr(
        "nflengine.history_graph.status.history_status",
        lambda: status_doc(
            build=None, append=None, asof=None, counts=None, up=False, container="missing"
        ),
    )
    r = invoke("status")
    assert r.exit_code == 0 and "no build yet" in r.output and "history-graph build" in r.output


def test_status_shows_a_busy_graph_and_a_failed_build(monkeypatch):
    monkeypatch.setattr(
        "nflengine.history_graph.status.history_status",
        lambda: status_doc(
            busy=True, last_failed_build={"failed_stage": "import", "error": "code 70 [x]"}
        ),
    )
    r = invoke("status")
    assert "a build or append is running" in r.output
    assert "last build failed at import" in r.output and "code 70" in r.output


def test_start_and_stop_report_and_exit(monkeypatch):
    monkeypatch.setattr(
        "nflengine.history_graph.docker.start",
        lambda cfg, log: ServiceResult(True, "started", "history graph started"),
    )
    r = invoke("start")
    assert r.exit_code == 0 and "history graph started" in r.output and "7475" in r.output
    monkeypatch.setattr(
        "nflengine.history_graph.docker.start",
        lambda cfg, log: ServiceResult(False, "failed", "Bolt didn't answer"),
    )
    r = invoke("start")
    assert r.exit_code == 2 and "Bolt didn't answer" in r.output

    monkeypatch.setattr(
        "nflengine.history_graph.docker.stop",
        lambda cfg: ServiceResult(True, "stopped", "history container stopped"),
    )
    r = invoke("stop")
    assert r.exit_code == 0 and "history container stopped" in r.output
    monkeypatch.setattr(
        "nflengine.history_graph.docker.stop",
        lambda cfg: ServiceResult(False, "failed", "`docker compose stop` exited with code 1"),
    )
    r = invoke("stop")
    assert r.exit_code == 2 and "exited with code 1" in r.output


def test_no_command_of_the_group_can_name_the_weekly_graph():
    # the group's help texts say what they touch; none of them mentions starting or stopping
    # the weekly graph except to say it is never touched
    for name in ("build", "append", "status", "start", "stop"):
        text = " ".join(invoke(name, "--help").output.split())
        assert "docker compose up -d`" not in text.replace("--profile history up -d", "")


def test_start_refuses_after_an_unfinished_build(monkeypatch, _tmp_data_root):
    # Sol's verification: a build killed mid-install may leave a copy container writing the
    # store; Neo4j must not be started on it
    from nflengine.history_graph.state import mark_unfinished

    mark_unfinished(_tmp_data_root, "build", "B9")
    monkeypatch.setattr(
        "nflengine.history_graph.docker.start", lambda *a, **k: pytest.fail("must not start")
    )
    r = invoke("start")
    assert r.exit_code == 2 and "the last build didn't finish" in r.output


def test_start_after_an_unfinished_append_is_allowed(monkeypatch, _tmp_data_root):
    from nflengine.history_graph.state import mark_unfinished

    mark_unfinished(_tmp_data_root, "append", "B9", 2026)
    monkeypatch.setattr(
        "nflengine.history_graph.docker.start",
        lambda cfg, log: ServiceResult(True, "started", "history graph started"),
    )
    assert invoke("start").exit_code == 0


def test_status_tells_an_unfinished_run_from_a_running_one(monkeypatch):
    note = {"what": "append", "season": 2026, "build_id": "B9"}
    monkeypatch.setattr(
        "nflengine.history_graph.status.history_status",
        lambda: status_doc(busy=True, running=None, unfinished=note),
    )
    r = invoke("status")
    assert "unfinished" in r.output and "--season 2026" in r.output
    assert "running now" not in r.output
    monkeypatch.setattr(
        "nflengine.history_graph.status.history_status",
        lambda: status_doc(
            busy=True,
            running={"command": "nfl history-graph build --through-season 2025", "started": "x"},
            unfinished=None,
        ),
    )
    r = invoke("status")
    assert "running now" in r.output and "history-graph build" in r.output
