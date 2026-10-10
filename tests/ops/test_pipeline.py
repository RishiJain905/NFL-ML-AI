"""`run_pipeline` (P07): --auto, already done, not ready + retry, the lock, failures and alerts,
simulations with --as-of, dry runs, the data drive check and the CLI exit codes."""

from __future__ import annotations

import datetime as dt
import json
import socket
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

import nflengine.weekly as W
from nflengine.cli import app
from nflengine.ops.calendar import EASTERN
from nflengine.ops.records import read_history
from nflengine.paths import DataPaths, DataRootError

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "schedules_2024_2026.csv"
TUESDAY_W5 = dt.datetime(2026, 10, 6, 10, 0, tzinfo=EASTERN).astimezone(dt.UTC)


def read_fixture() -> pl.DataFrame:
    return pl.read_csv(FIXTURE, schema_overrides={"gametime": pl.String, "gameday": pl.String})


@pytest.fixture(scope="module")
def raw_sched() -> pl.DataFrame:
    """As captured on 2026-10-04: 2026 week 4 has only Thursday's result."""
    return read_fixture()


@pytest.fixture(scope="module")
def sched(raw_sched) -> pl.DataFrame:
    """2026 weeks 1-4 final (scores 0): a Tuesday where the --auto pre-check passes and the
    (fake) `ready` step decides."""
    done = (pl.col("season") == 2026) & (pl.col("week") <= 4) & pl.col("result").is_null()
    return raw_sched.with_columns(pl.when(done).then(0).otherwise(pl.col("result")).alias("result"))


@pytest.fixture
def paths(tmp_path: Path, monkeypatch) -> DataPaths:
    p = DataPaths(tmp_path)
    monkeypatch.setattr(W, "ensure_data_root", lambda *a, **k: p)
    return p


def fake(calls: list[str], fail: dict[str, Exception] | None = None, steps=W.STEPS):
    fail = fail or {}

    def make(name):
        def step(opts, log):
            calls.append(name)
            if name in fail:
                raise fail[name]
            return f"{name} ok"

        return step

    return {n: make(n) for n in steps}


def run(sched, **kw):
    kw.setdefault("use_wandb", False)
    kw.setdefault("log", lambda m: None)
    return W.run_pipeline(schedules=sched, **kw)


def test_auto_works_out_the_week_and_writes_the_records(sched, paths, monkeypatch) -> None:
    # the run record stamps its finish with the real clock (`ops.records.utc_now`): pin it to
    # the same Tuesday, or `on_time` turns false once the real week-5 deadline has passed
    import nflengine.ops.records as R

    monkeypatch.setattr(R, "utc_now", lambda: TUESDAY_W5)
    calls: list[str] = []
    out = run(sched, auto=True, now=TUESDAY_W5, funcs=fake(calls), launched_by="agent")
    assert (out.status, out.exit_code, out.season, out.week) == ("ok", 0, 2026, 5)
    assert calls == list(W.STEPS)
    summary = json.loads(out.summary_path.read_text())
    assert summary["kind"] == "main" and summary["plan"]["week"] == 5
    assert summary["deadline"] == "2026-10-09T00:15:00Z"  # Thu 20:15 ET
    assert summary["published"] and summary["on_time"]
    hist = read_history(paths.runs / "2026" / "pipeline_history.parquet")
    assert hist["status"].to_list() == ["ok"] and hist["launched_by"].to_list() == ["agent"]
    from nflengine.ops import lock as L

    assert not L.is_locked(paths.runs / ".weekly.lock")  # released


def test_auto_promotes_but_manual_runs_do_not(sched, paths) -> None:
    seen = []

    def game(o, log):
        seen.append(o.promote)
        return "ok"

    funcs = {**fake([]), "game": game}
    run(sched, auto=True, now=TUESDAY_W5, funcs=funcs)
    run(sched, season=2026, week=5, now=TUESDAY_W5, funcs=funcs)
    run(sched, season=2026, week=5, now=TUESDAY_W5, funcs=funcs, promote=True)
    assert seen == [True, False, True]


def test_auto_skips_a_published_week(sched, paths) -> None:
    run(sched, auto=True, now=TUESDAY_W5, funcs=fake([]))
    report = paths.report_path(2026, 5)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("digest")
    calls: list[str] = []
    out = run(sched, auto=True, now=TUESDAY_W5, funcs=fake(calls))
    assert (out.status, out.exit_code, calls) == ("already_done", 0, [])
    # --force runs it anyway; a manual run never checks
    assert run(sched, auto=True, now=TUESDAY_W5, funcs=fake(calls), force=True).status == "ok"


def test_not_ready_exits_3_cleanly_and_the_retry_succeeds(sched, paths) -> None:
    calls: list[str] = []
    not_ready = W.StepFailed("ready", "2026 week 4: NOT READY (15/16 games final)", 3)
    out = run(sched, auto=True, now=TUESDAY_W5, funcs=fake(calls, {"ready": not_ready}))
    assert (out.status, out.exit_code) == ("not_ready", 3)
    assert calls == ["ingest", "ready"]
    assert not out.alerts or all(a.source != "readiness" for a in out.alerts)  # in window
    calls.clear()
    later = TUESDAY_W5 + dt.timedelta(hours=3)
    out = run(sched, auto=True, now=later, funcs=fake(calls))
    assert out.status == "ok" and calls == list(W.STEPS)
    hist = read_history(paths.runs / "2026" / "pipeline_history.parquet")
    assert hist["status"].to_list() == ["not_ready", "ok"]


def test_not_ready_after_wednesday_evening_alerts(sched, paths) -> None:
    thursday = dt.datetime(2026, 10, 8, 9, 0, tzinfo=EASTERN).astimezone(dt.UTC)
    not_ready = W.StepFailed("ready", "NOT READY", 3)
    out = run(sched, auto=True, now=thursday, funcs=fake([], {"ready": not_ready}))
    assert out.exit_code == 3
    assert any(a.source == "readiness" and a.level == "error" for a in out.alerts)


def test_a_live_lock_blocks_a_second_run(sched, paths) -> None:
    from nflengine.ops import lock as L

    calls: list[str] = []
    with L.run_lock(L.lock_path(paths.runs), "other"):  # another run holds the OS lock
        out = run(sched, auto=True, now=TUESDAY_W5, funcs=fake(calls))
        assert L.is_locked(L.lock_path(paths.runs))  # not ours: left alone
    assert (out.status, out.exit_code, calls) == ("locked", 4, [])
    assert "other" in out.message


def test_a_dead_runs_note_is_reported_with_an_info_alert(sched, paths) -> None:
    """A note without an OS lock: that run died (the OS released its lock)."""
    from nflengine.ops import lock as L

    lock = paths.runs / ".weekly.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = {"pid": 2**31 - 7, "host": socket.gethostname(), "command": "old", "started": ""}
    lock.write_text(json.dumps(holder))
    out = run(sched, auto=True, now=TUESDAY_W5, funcs=fake([]))
    assert out.status == "ok" and any(a.source == "lock" for a in out.alerts)
    assert not L.is_locked(lock) and L.read_holder(lock) is None


def test_a_failed_step_stops_alerts_and_resumes(sched, paths) -> None:
    calls: list[str] = []
    boom = W.StepFailed("curate", "blocking quality checks failed: ['games unique']")
    out = run(sched, auto=True, now=TUESDAY_W5, funcs=fake(calls, {"curate": boom}))
    assert (out.status, out.exit_code) == ("failed", 1)
    alert = next(a for a in out.alerts if a.level == "error")
    assert "at step curate" in alert.title and "--from-step curate" in alert.text
    summary = json.loads(out.summary_path.read_text())
    assert summary["failed_step"] == "curate" and not summary["published"]
    calls.clear()
    out = run(sched, season=2026, week=5, now=TUESDAY_W5, funcs=fake(calls), from_step="curate")
    assert out.status == "ok" and calls[0] == "curate"


def test_an_unexpected_error_is_a_failure(sched, paths) -> None:
    out = run(sched, auto=True, now=TUESDAY_W5, funcs=fake([], {"game": KeyError("x")}))
    assert (out.status, out.exit_code) == ("failed", 1)


def test_a_degraded_step_marks_the_run_degraded(sched, paths) -> None:
    funcs = fake([], {"graph": W.StepDegraded("graph", "Neo4j unreachable")})
    out = run(sched, auto=True, now=TUESDAY_W5, funcs=funcs)
    assert (out.status, out.exit_code) == ("degraded", 0)
    assert any(a.source == "step:graph" and a.level == "warn" for a in out.alerts)


def test_dry_run_writes_nothing(raw_sched, paths) -> None:
    calls: list[str] = []
    out = run(raw_sched, auto=True, now=TUESDAY_W5, funcs=fake(calls), dry_run=True)
    # live dry run: the fixture has no week-4 Sunday results, so it says "not final" -> 3
    assert (out.status, out.exit_code, calls) == ("planned", 3, [])
    assert not any(paths.runs.rglob("*.json")) and not (paths.runs / ".weekly.lock").exists()


def test_offseason_is_idle(sched, paths) -> None:
    feb = dt.datetime(2025, 2, 20, 15, tzinfo=dt.UTC)
    out = run(sched, auto=True, now=feb, as_of=None, funcs=fake([]))
    assert (out.status, out.exit_code, out.week) == ("idle", 0, None)


def test_auto_refuses_a_season_other_than_settings(sched, paths) -> None:
    with pytest.raises(ValueError, match="seasons.current"):
        run(sched, auto=True, now=dt.datetime(2024, 11, 26, 15, tzinfo=dt.UTC), funcs=fake([]))


def test_simulation_not_ready_then_ready(sched, paths) -> None:
    """--as-of in the past: readiness from kickoff + data lag; files under digest-backtests."""
    now = TUESDAY_W5
    monday = dt.datetime(2025, 11, 3, 22, tzinfo=EASTERN).astimezone(dt.UTC)
    tuesday = dt.datetime(2025, 11, 4, 10, tzinfo=EASTERN).astimezone(dt.UTC)
    calls: list[str] = []
    digest = fake(calls, steps=("digest",))
    out = run(sched, auto=True, now=now, as_of=monday, funcs=digest)
    assert (out.status, out.exit_code, out.season, out.week) == ("not_ready", 3, 2025, 10)
    assert calls == []  # the simulated ready step stopped it
    out = run(sched, auto=True, now=now, as_of=tuesday, funcs=digest)
    assert (out.status, out.exit_code) == ("ok", 0) and calls == ["digest"]
    sim = paths.runs / "digest-backtests" / "2025"
    summary = json.loads((sim / "week10" / "run_summary.json").read_text())
    assert summary["kind"] == "simulation" and summary["as_of"] == "2025-11-04T15:00:00Z"
    assert summary["on_time"]  # measured on the simulated clock, before Thursday's kickoff
    assert read_history(sim / "pipeline_history.parquet")["status"].to_list() == [
        "not_ready",
        "ok",
    ]
    assert not (paths.runs / "2025").exists()  # live folders untouched
    # the same simulated moment again: already done
    assert run(sched, auto=True, now=now, as_of=tuesday, funcs=digest).status == "already_done"


def test_as_of_in_the_future_is_rejected(sched, paths) -> None:
    with pytest.raises(ValueError, match="future"):
        run(sched, auto=True, now=TUESDAY_W5, as_of=TUESDAY_W5 + dt.timedelta(days=1))


def test_parse_as_of_defaults_to_eastern() -> None:
    assert W.parse_as_of("2025-11-04T10:00") == dt.datetime(2025, 11, 4, 15, tzinfo=dt.UTC)
    assert W.parse_as_of("2025-11-04T10:00Z") == dt.datetime(2025, 11, 4, 10, tzinfo=dt.UTC)


def test_cli_exit_codes(monkeypatch) -> None:
    runner = CliRunner()

    def no_drive(*a, **k):
        raise DataRootError("Data drive D:\\ is not connected. Nothing was written.")

    monkeypatch.setattr(W, "run_pipeline", no_drive)
    res = runner.invoke(app, ["weekly", "run", "--auto"])
    assert res.exit_code == 5 and "not connected" in res.output  # its own code (D74)
    assert runner.invoke(app, ["weekly", "run"]).exit_code == 2  # a usage error

    def not_ready(*a, **k):
        return W.PipelineOutcome("not_ready", 3, 2026, 5, "NOT READY")

    monkeypatch.setattr(W, "run_pipeline", not_ready)
    res = runner.invoke(app, ["weekly", "run", "--auto"])
    assert res.exit_code == 3 and "run it again later" in res.output
    help_text = runner.invoke(app, ["weekly", "run", "--help"]).output
    for flag in ("--auto", "--as-of", "--dry-run", "--from-step", "--force"):
        assert flag in help_text


def test_next_playoff_round_missing_refreshes_the_schedule_then_waits(
    sched, paths, monkeypatch
) -> None:
    """Under the lock: refresh only the schedule; still missing -> a recorded not-ready, and
    an error alert once the retry window has passed (Sol review)."""
    from nflengine import settings as ST

    cfg = ST.get_config().model_copy(deep=True)
    cfg.seasons.current = 2024
    monkeypatch.setattr(ST, "get_config", lambda: cfg)
    no_wc = sched.filter(~((pl.col("season") == 2024) & (pl.col("week") > 18)))
    refreshed: list[int] = []
    monkeypatch.setattr(W, "refresh_schedule", lambda season, log: refreshed.append(season))
    monday = dt.datetime(2025, 1, 6, 9, tzinfo=EASTERN).astimezone(dt.UTC)
    calls: list[str] = []
    out = run(no_wc, auto=True, now=monday, funcs=fake(calls))
    assert (out.status, out.exit_code, out.week) == ("not_ready", 3, 19)
    assert refreshed == [2024] and calls == []
    summary = json.loads(out.summary_path.read_text())
    assert "aren't in the schedule yet" in summary["error"]
    assert not any(a.source == "readiness" for a in out.alerts)  # still inside the window
    thursday = dt.datetime(2025, 1, 9, 9, tzinfo=EASTERN).astimezone(dt.UTC)
    late = run(no_wc, auto=True, now=thursday, funcs=fake(calls))
    assert any(a.title.endswith("games still not in the schedule") for a in late.alerts)
    hist = read_history(paths.runs / "2024" / "pipeline_history.parquet")
    assert hist["status"].to_list() == ["not_ready", "not_ready"]


def test_auto_precheck_refreshes_the_schedule_then_records_not_ready(
    raw_sched, paths, _no_real_schedule_refresh
) -> None:
    """Week 4's Sunday results aren't in the schedule: refresh only the schedule (no full
    ingest, no Odds API quota) and record a not-ready run from the calendar."""
    calls: list[str] = []
    out = run(raw_sched, auto=True, now=TUESDAY_W5, funcs=fake(calls))
    assert (out.status, out.exit_code) == ("not_ready", 3)
    assert _no_real_schedule_refresh == [2026] and calls == []  # no step function ran
    summary = json.loads(out.summary_path.read_text())
    assert list(summary["steps"]) == ["ready"] and "schedule results" in summary["error"]
    hist = read_history(paths.runs / "2026" / "pipeline_history.parquet")
    assert hist["status"].to_list() == ["not_ready"]


def test_the_dashboard_never_moves_back_to_an_older_week(sched, paths, monkeypatch) -> None:
    import nflengine.ops.dashboard as D

    logged: list[int] = []
    monkeypatch.setattr(D, "log_season_dashboard", lambda s, w, **k: logged.append(w) or "url")
    monkeypatch.setattr(W, "log_pipeline_run", lambda *a, **k: None, raising=False)
    import nflengine.ops.summary as SM

    monkeypatch.setattr(SM, "log_pipeline_run", lambda *a, **k: None)
    (paths.runs / "2026").mkdir(parents=True, exist_ok=True)
    (paths.runs / "2026" / "dashboard.json").write_text(json.dumps({"last_run_week": 6}))

    def digest(o, log):
        d = paths.run_dir(2026, o.week)
        d.mkdir(parents=True, exist_ok=True)
        (d / "checks.json").write_text(json.dumps({"passed": True, "final": {}}))
        return "digest ok"

    funcs = {**fake([]), "digest": digest}
    run(sched, season=2026, week=5, now=TUESDAY_W5, funcs=funcs, use_wandb=True)
    assert logged == []  # week 5 re-run after week 6: the report stays on week 6
    (paths.runs / "2026" / "dashboard.json").write_text(json.dumps({"last_run_week": 4}))
    run(sched, season=2026, week=5, now=TUESDAY_W5, funcs=funcs, use_wandb=True)
    assert logged == [5]


def test_a_truncated_state_file_is_set_aside_and_the_run_still_records(sched, paths) -> None:
    """Sol review: a crash mid-write could leave weekly_run.json truncated; the next run
    keeps it as .json.corrupt, starts fresh and still writes its records."""
    run_dir = paths.run_dir(2026, 5)
    run_dir.mkdir(parents=True)
    (run_dir / "weekly_run.json").write_text('{"season": 2026, "steps": {"ingest": {"st')
    calls: list[str] = []
    out = run(sched, season=2026, week=5, now=TUESDAY_W5, funcs=fake(calls))
    assert out.status == "ok" and calls == list(W.STEPS)
    assert (run_dir / "weekly_run.json.corrupt").exists()
    assert (
        json.loads((run_dir / "weekly_run.json").read_text())["steps"]["digest"]["status"] == "ok"
    )
    assert out.summary_path is not None and out.summary_path.exists()
    assert W.read_state(run_dir / "weekly_run.json.corrupt") is None
