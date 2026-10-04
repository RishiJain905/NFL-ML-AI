"""P07 runtime pieces: the run lock, Neo4j / Docker start-up, the run summary and records."""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
from pathlib import Path

import polars as pl
import pytest

from nflengine.ops import lock as L
from nflengine.ops import services as S
from nflengine.ops import summary as SM
from nflengine.ops.records import (
    HISTORY_SCHEMA,
    Alert,
    DriftSignal,
    append_history,
    latest_runs,
    read_history,
)
from nflengine.paths import DataPaths

NOW = dt.datetime(2026, 10, 6, 14, 0, tzinfo=dt.UTC)


# ---- lock -------------------------------------------------------------------------------------


def test_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    path = tmp_path / ".weekly.lock"
    with L.run_lock(path, "first") as info:
        assert L.is_locked(path) and info.holder["command"] == "first"
        assert info.took_over is None
        with pytest.raises(L.LockHeld) as e, L.run_lock(path, "second"):
            pass
        assert "first" in str(e.value) and e.value.holder["pid"] == os.getpid()
        assert L.read_holder(path)["command"] == "first"  # the note stays readable
    assert not L.is_locked(path) and L.read_holder(path) is None


def test_lock_released_on_error(tmp_path: Path) -> None:
    path = tmp_path / ".weekly.lock"
    with pytest.raises(RuntimeError), L.run_lock(path, "x"):
        raise RuntimeError("boom")
    assert not L.is_locked(path)


def test_a_crashed_run_releases_its_lock(tmp_path: Path) -> None:
    """The OS releases the lock when the holder dies (Sol review: no stale-lock guessing, so
    no take-over race and no broken lock after the PC sleeps)."""
    import subprocess
    import sys

    path = tmp_path / ".weekly.lock"
    code = (
        "import os, sys; from pathlib import Path; from nflengine.ops import lock as L\n"
        f"cm = L.run_lock(Path(r'{path}'), 'crashing run'); cm.__enter__()\n"
        "print('held', flush=True); sys.stdin.readline(); os._exit(1)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "held"
        assert L.is_locked(path)
        with pytest.raises(L.LockHeld) as e, L.run_lock(path, "second"):
            pass
        assert e.value.holder["command"] == "crashing run"
    finally:
        child.stdin.write("go\n")
        child.stdin.flush()
        child.wait(timeout=30)
    with L.run_lock(path, "after the crash") as info:  # released by the OS
        assert info.took_over["command"] == "crashing run"  # its note was left behind


def test_a_note_without_a_lock_is_reported_as_taken_over(tmp_path: Path) -> None:
    path = tmp_path / ".weekly.lock"
    path.write_text(json.dumps({"pid": 1, "host": socket.gethostname(), "command": "old"}))
    assert not L.is_locked(path)
    with L.run_lock(path, "new") as info:
        assert info.took_over["command"] == "old"
        assert L.read_holder(path)["command"] == "new"


# ---- services ---------------------------------------------------------------------------------


class Fake:
    def __init__(self, pings: list[bool], codes: dict[str, list[int]] | None = None):
        self.pings, self.codes, self.cmds = list(pings), codes or {}, []

    def ping(self) -> bool:
        return self.pings.pop(0) if len(self.pings) > 1 else self.pings[0]

    def run(self, args: list[str], timeout: float) -> int:
        key = " ".join(args[:2])
        self.cmds.append(" ".join(args))
        seq = self.codes.get(key, [0])
        return seq.pop(0) if len(seq) > 1 else seq[0]


def test_neo4j_already_up_does_nothing() -> None:
    f = Fake([True])
    res = S.ensure_neo4j(ping=f.ping, run=f.run, sleep=lambda s: None)
    assert res.ok and res.action == "already_up" and f.cmds == []


def test_neo4j_down_starts_the_container_once() -> None:
    f = Fake([False, False, True])
    res = S.ensure_neo4j(log=lambda m: None, ping=f.ping, run=f.run, sleep=lambda s: None)
    assert res.ok and res.action == "started_container"
    assert f.cmds.count("docker compose up -d") == 1


def test_docker_engine_down_starts_docker_desktop_first() -> None:
    f = Fake([False, False, True], {"docker version": [1, 1, 0]})
    started = []
    res = S.ensure_neo4j(
        log=lambda m: None,
        ping=f.ping,
        run=f.run,
        start_desktop=lambda: started.append(1) or True,
        sleep=lambda s: None,
    )
    assert res.ok and res.action == "started_docker_and_container" and started == [1]


def test_compose_failure_and_timeouts_fail_soft() -> None:
    f = Fake([False], {"docker compose": [1]})
    res = S.ensure_neo4j(log=lambda m: None, ping=f.ping, run=f.run, sleep=lambda s: None)
    assert not res.ok and "exited with code 1" in res.detail
    f = Fake([False])
    res = S.ensure_neo4j(
        log=lambda m: None, neo4j_timeout_s=0, ping=f.ping, run=f.run, sleep=lambda s: None
    )
    assert not res.ok and "didn't answer" in res.detail
    f = Fake([False], {"docker version": [1]})
    res = S.ensure_neo4j(
        log=lambda m: None,
        ping=f.ping,
        run=f.run,
        start_desktop=lambda: False,
        sleep=lambda s: None,
    )
    assert not res.ok and "Docker Desktop" in res.detail


# ---- summary ----------------------------------------------------------------------------------


def test_scrub_removes_query_strings_and_secret_pairs() -> None:
    s = SM.scrub("GET https://api.example.com/v4/odds?apiKey=abc123&x=1 failed; token=zzz9 ok")
    assert "abc123" not in s and "zzz9" not in s
    assert "https://api.example.com/v4/odds?…" in s and "token=***" in s
    assert SM.scrub(None) == "" and len(SM.scrub("x" * 1000, limit=50)) == 50


def _manifest(paths: DataPaths, results: list[dict]) -> None:
    (paths.raw / "_runs").mkdir(parents=True, exist_ok=True)
    (paths.raw / "_runs" / "ingest-20261006T140000.json").write_text(
        json.dumps({"snapshot_date": "2026-10-06", "results": results})
    )


def test_freshness_flags_old_snapshots(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path)
    for src, ds, day in [("nflverse", "pbp", "2026-10-06"), ("espn", "news", "2026-09-20")]:
        (paths.raw / src / ds / f"snapshot={day}").mkdir(parents=True)
    _manifest(
        paths,
        [
            {"source": "nflverse", "dataset": "pbp", "status": "ok", "rows": 10},
            {"source": "espn", "dataset": "news", "status": "failed", "detail": "x?key=1"},
            {"source": "odds_api", "dataset": "odds", "status": "skipped"},
        ],
    )
    rows = SM.freshness(paths, 2026, 5, NOW)
    by = {(r["source"], r["dataset"]): r for r in rows}
    assert not by[("nflverse", "pbp")]["stale"] and by[("nflverse", "pbp")]["age_days"] == 0
    news = by[("espn", "news")]
    assert news["stale"] and news["age_days"] == 16 and "key=1" not in news["note"]
    assert not by[("odds_api", "odds")]["stale"]  # skipped by settings
    # no curated DuckDB here: content weeks are reported unavailable, never stale
    assert by[("curated", "(all)")]["status"] == "unavailable"


def test_build_summary_counts_only_this_runs_steps(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path)
    run_dir = paths.run_dir(2026, 5)
    run_dir.mkdir(parents=True)
    (run_dir / "checks.json").write_text(
        json.dumps({"passed": True, "regenerated": True, "banner": False, "final": {}})
    )
    started = dt.datetime(2026, 10, 6, 14, 0, tzinfo=dt.UTC)
    old = (started - dt.timedelta(days=1)).isoformat()
    new = (started + dt.timedelta(minutes=1)).isoformat()
    end = (started + dt.timedelta(minutes=3)).isoformat()
    state = {
        "steps": {
            "ingest": {"status": "ok", "started": old, "finished": old, "detail": "old"},
            "digest": {"status": "ok", "started": new, "finished": end, "detail": "d?apiKey=9"},
        }
    }

    class Plan:
        deadline = started + dt.timedelta(hours=54)

        def as_dict(self):
            return {"phase": "regular"}

    s = SM.build_summary(
        kind="main",
        season=2026,
        week=5,
        status="ok",
        exit_code=0,
        started=started,
        finished=started + dt.timedelta(minutes=3),
        run_dir=run_dir,
        paths=paths,
        plan=Plan(),
        state=state,
        drift=[DriftSignal("checks", "alert", 0.5, 0.2, "d", "r")],
        alerts=[Alert("warn", "t", "x", "drift:checks")],
    )
    assert not s["steps"]["ingest"]["this_run"] and s["steps"]["digest"]["this_run"]
    assert s["steps"]["digest"]["seconds"] == 120.0 and "9" not in s["steps"]["digest"]["detail"]
    assert s["published"] and s["on_time"] and s["hours_before_deadline"] == pytest.approx(53.95)
    assert s["digest"]["regenerated"] and s["ingest"] is None  # ingest didn't run this time
    row = SM.history_row(s)
    assert row["drift_alerts"] == 1 and row["checks_passed"] is True
    assert set(row) == set(HISTORY_SCHEMA)


def test_history_append_and_latest_runs(tmp_path: Path) -> None:
    path = tmp_path / "pipeline_history.parquet"
    base = {"season": 2026, "kind": "main", "drift_alerts": 0}
    append_history(path, {**base, "week": 5, "status": "not_ready", "started": "2026-10-06T14"})
    append_history(path, {**base, "week": 5, "status": "ok", "started": "2026-10-06T17"})
    append_history(path, {**base, "week": 6, "status": "failed", "started": "2026-10-13T14"})
    append_history(path, {**base, "week": 6, "status": "ok", "started": "2026-10-13T15"})
    h = read_history(path)
    assert h.height == 4 and h.schema == pl.Schema(HISTORY_SCHEMA)
    latest = latest_runs(h)
    assert latest["week"].to_list() == [5, 6] and latest["status"].to_list() == ["ok", "ok"]
    assert read_history(tmp_path / "missing.parquet").height == 0


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("Authorization: Bearer ABCDEF123456", "ABCDEF123456"),
        ("{'apiKey': 'ABC123XYZ'}", "ABC123XYZ"),
        ('{"api_key": "QWERTY987"}', "QWERTY987"),
        ("headers={'Authorization': 'Bearer zz9zz9zz'}", "zz9zz9zz"),
        ("key sk-or-v1-abcdef0123456789abcdef in use", "abcdef0123456789"),
        ("bolt://neo4j:SuperSecret@localhost:7687", "SuperSecret"),
        ("GET https://api.the-odds-api.com/v4/x?apiKey=deadbeef failed", "deadbeef"),
        ("wandb key 0123456789abcdef0123456789abcdef01234567", "0123456789abcdef"),
        ("NEO4J_PASSWORD='p w'", "p w"),
    ],
)
def test_scrub_masks_secret_shapes(raw, leaked) -> None:
    assert leaked not in SM.scrub(raw)


def test_scrub_keeps_ordinary_details() -> None:
    url = "W&B https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/kl4fzvl8"
    assert SM.scrub(url) == url
    assert SM.scrub("15,522 nodes, 588,330 relationships in 84 s") == (
        "15,522 nodes, 588,330 relationships in 84 s"
    )


def test_research_only_datasets_are_never_stale(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path)
    (paths.research / "nflverse" / "participation" / "snapshot=2026-03-01").mkdir(parents=True)
    _manifest(paths, [{"source": "nflverse", "dataset": "participation", "status": "ok"}])
    row = next(r for r in SM.freshness(paths, 2026, 5, NOW) if r["dataset"] == "participation")
    assert not row["stale"] and "research only" in row["note"]


def test_history_write_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "pipeline_history.parquet"
    append_history(path, {"season": 2026, "week": 5, "kind": "main", "status": "ok"})
    assert path.exists() and not path.with_suffix(".parquet.tmp").exists()


def test_latest_runs_keeps_the_last_published_run_of_a_week() -> None:
    h = pl.DataFrame(
        [
            {"season": 2026, "week": 5, "kind": "main", "status": "ok", "started": "a",
             "checks_passed": True, "on_time": True},
            {"season": 2026, "week": 5, "kind": "main", "status": "failed", "started": "b",
             "checks_passed": None, "on_time": None},  # a later resume that failed early
        ],
        schema=HISTORY_SCHEMA,
    )  # fmt: skip
    row = latest_runs(h).row(0, named=True)
    assert row["status"] == "ok" and row["on_time"] is True


def test_neo4j_credentials_problem_starts_nothing() -> None:
    def bad_password() -> bool:
        raise S.Neo4jConfigProblem("AuthError: Neo4j rejected the credentials")

    f = Fake([False])
    res = S.ensure_neo4j(ping=bad_password, run=f.run, sleep=lambda s: None)
    assert not res.ok and "nothing was started" in res.detail and f.cmds == []


def test_pipeline_run_scrubs_alerts_and_skips_them_for_simulations(monkeypatch) -> None:
    import types

    sent: list[dict] = []

    class Run:
        url = "https://wandb/run"
        summary: dict = {}

        def log(self, *a, **k):
            pass

        def use_artifact(self, *a, **k):
            pass

        def alert(self, **k):
            sent.append(k)

        def finish(self, **k):
            self.exit = k.get("exit_code")

    run = Run()
    import nflengine.tracking as T

    monkeypatch.setattr(T, "init_run", lambda *a, **k: run)
    base = {
        "season": 2026, "week": 5, "kind": "main", "status": "failed", "exit_code": 1,
        "total_seconds": 60.0, "published": False, "on_time": None,
        "hours_before_deadline": None, "failed_step": "game", "degraded_steps": [],
        "stale_sources": [], "steps": {}, "drift": [], "freshness": None,
        "alerts": [{"level": "error", "title": "t", "text": "token=SECRET123 failed",
                    "source": "step:game"}],
    }  # fmt: skip
    assert SM.log_pipeline_run(base, log=lambda m: None) == "https://wandb/run"
    assert len(sent) == 1 and "SECRET123" not in sent[0]["text"] and run.exit == 1
    sent.clear()
    SM.log_pipeline_run({**base, "kind": "simulation"}, log=lambda m: None)
    assert sent == [] and types is not None
