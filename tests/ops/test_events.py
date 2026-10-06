"""CR02: progress events (`ops/events.py`), `--expect-week` (exit 6), the events of a weekly run,
a rehearsal and an injury update, and the file-replace retry (`fsutil.replace_file`)."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

import nflengine.weekly as W
from nflengine import fsutil
from nflengine.cli import app
from nflengine.ops import events
from nflengine.ops import lock as L
from nflengine.ops.calendar import EASTERN
from nflengine.paths import DataPaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "schedules_2024_2026.csv"
TUESDAY_W5 = dt.datetime(2026, 10, 6, 10, 0, tzinfo=EASTERN).astimezone(dt.UTC)


@pytest.fixture(scope="module")
def sched() -> pl.DataFrame:
    raw = pl.read_csv(FIXTURE, schema_overrides={"gametime": pl.String, "gameday": pl.String})
    done = (pl.col("season") == 2026) & (pl.col("week") <= 4) & pl.col("result").is_null()
    return raw.with_columns(pl.when(done).then(0).otherwise(pl.col("result")).alias("result"))


@pytest.fixture
def paths(tmp_path: Path, monkeypatch) -> DataPaths:
    p = DataPaths(tmp_path)
    monkeypatch.setattr(W, "ensure_data_root", lambda *a, **k: p)
    return p


def fake(calls: list[str], fail: dict[str, Exception] | None = None, progress=False):
    fail = fail or {}

    def make(name):
        def step(opts, log):
            calls.append(name)
            if progress:
                for i in range(3):
                    events.progress(None, i + 1, 3, f"{name} part {i + 1}/3")
            log(f"[dim]{name}: working[/]")
            if name in fail:
                raise fail[name]
            return f"{name} ok"

        return step

    return {n: make(n) for n in W.STEPS}


def run(sched, **kw):
    kw.setdefault("use_wandb", False)
    kw.setdefault("log", lambda *a, **k: None)
    return W.run_pipeline(schedules=sched, **kw)


def read_events(paths: DataPaths, season=2026, week=5) -> list[dict]:
    files = sorted((paths.run_dir(season, week) / "events").glob("*.jsonl"))
    assert len(files) == 1, files
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


# ---- the writer ---------------------------------------------------------------------------


def test_without_a_writer_every_call_is_a_no_op(tmp_path) -> None:
    events.emit("log", text="x")
    events.progress("game", 1, 2, "half")
    events.step_start("game")
    events.step_end("game", "ok", 1.0, "done")
    events.llm("start", writer="placeholder")
    events.run_end(status="ok", exit_code=0, message="m")
    assert events.current() is None
    assert not list(tmp_path.iterdir())


def test_lines_are_whole_numbered_and_scrubbed(tmp_path) -> None:
    path = tmp_path / "events" / "20261006T140000Z.jsonl"
    with events.writing(path, "20261006T140000Z"):
        events.run_start(
            kind="weekly", command="nfl weekly run --auto", season=2026, week=5, steps=["ingest"]
        )
        events.step_start("ingest")
        events.step_end("ingest", "ok", 3.25, "pulled https://x.test/a?apiKey=abc123secret")
        log = events.tee(lambda *a, **k: None)
        log("[yellow]odds: Authorization: Bearer sk-or-v1-0123456789abcdef0123456789[/]")
    lines = path.read_text(encoding="utf-8").splitlines()
    evs = [json.loads(x) for x in lines]
    assert [e["seq"] for e in evs] == [1, 2, 3, 4]
    assert {e["run_id"] for e in evs} == {"20261006T140000Z"}
    assert all(dt.datetime.fromisoformat(e["t"]).tzinfo for e in evs)
    assert evs[2]["seconds"] == 3.2 or evs[2]["seconds"] == 3.3
    assert "abc123secret" not in lines[2] and "sk-or-v1" not in lines[3]
    assert evs[3]["type"] == "log" and evs[3]["cls"] == "warn" and "[yellow]" not in evs[3]["text"]
    assert evs[3]["step"] is None  # printed after the step ended
    assert events.current() is None


def test_progress_is_throttled_mapped_by_portions_and_never_backwards(tmp_path) -> None:
    path = tmp_path / "e.jsonl"
    with events.writing(path, "20261006T140000Z"):
        events.step_start("player")
        with events.portion(0.08, 0.88):
            for i in range(23):
                events.progress(None, i + 1, 23, f"refitting {i + 1}/23")
        events.progress(None, 88, 100, "team stat totals")
    prog = [json.loads(x) for x in path.read_text().splitlines() if '"progress"' in x]
    fr = [p["fraction"] for p in prog]
    assert fr == sorted(fr) and fr[-1] == 0.88
    assert prog[-1]["label"] == "team stat totals"  # a new kind of label is never dropped
    assert any(p["label"] == "refitting 23/23" for p in prog)  # nor a part's last call
    assert len(prog) < 10  # throttled: 23 calls in a few ms -> first, last, the next part
    assert all(p["step"] == "player" for p in prog)


def test_a_broken_events_file_never_changes_the_run(sched, paths) -> None:
    # make the events folder a file: every write fails, the run goes on unchanged
    run_dir = paths.run_dir(2026, 5)
    run_dir.mkdir(parents=True)
    (run_dir / "events").write_text("not a folder")
    calls: list[str] = []
    out = run(sched, auto=True, now=TUESDAY_W5, funcs=fake(calls, progress=True))
    assert (out.status, out.exit_code) == ("ok", 0)
    assert calls == list(W.STEPS)


def test_plain_strips_markup_and_maps_styles() -> None:
    assert events.plain("[green]ok: done[/]") == ("ok: done", "ok")
    assert events.plain("[bold red]boom[/]") == ("boom", "err")
    assert events.plain("[bold]== ingest ==[/]") == ("== ingest ==", "acc")
    # brackets that aren't markup stay
    assert events.plain("aliases [2026-w05, production]")[0] == "aliases [2026-w05, production]"
    assert events.valid_run_id("20261006T140012Z-a1b2")
    assert not events.valid_run_id("../../etc") and not events.valid_run_id(None)


# ---- the weekly run -------------------------------------------------------------------------


def test_a_weekly_run_writes_its_story(sched, paths) -> None:
    calls: list[str] = []
    out = run(
        sched,
        auto=True,
        now=TUESDAY_W5,
        funcs=fake(calls, progress=True),
        expect_week=5,
        run_id="20261006T140000Z-ab12",
        via="control-room",
        launched_by="rishi",
    )
    assert out.exit_code == 0
    evs = read_events(paths)
    assert (paths.run_dir(2026, 5) / "events" / "20261006T140000Z-ab12.jsonl").exists()
    start, end = evs[0], evs[-1]
    assert start["type"] == "run_start" and start["kind"] == "weekly"
    assert start["command"] == "nfl weekly run --auto --expect-week 5"
    assert start["steps"] == list(W.STEPS) and start["via"] == "control-room"
    assert start["plan"] and start["launched_by"] == "rishi"
    starts = [e["step"] for e in evs if e["type"] == "step_start"]
    ends = [(e["step"], e["status"]) for e in evs if e["type"] == "step_end"]
    assert starts == [*W.STEPS, "records"]
    assert ends == [(s, "ok") for s in W.STEPS] + [("records", "ok")]
    assert end["type"] == "run_end" and end["status"] == "ok" and end["exit_code"] == 0
    logs = [e for e in evs if e["type"] == "log"]
    assert any(e["text"] == "game: working" and e["step"] == "game" for e in logs)
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))


def test_a_failed_step_and_the_resume(sched, paths) -> None:
    boom = W.StepFailed("player", "refit failed for sacks-edge")
    out = run(
        sched,
        auto=True,
        now=TUESDAY_W5,
        funcs=fake([], {"player": boom}),
        run_id="20261006T140000Z",
    )
    assert out.exit_code == 1
    evs = read_events(paths)
    assert ("player", "failed") in [
        (e["step"], e["status"]) for e in evs if e["type"] == "step_end"
    ]
    assert "digest" not in [e["step"] for e in evs if e["type"] == "step_start"]
    end = evs[-1]
    assert (end["status"], end["exit_code"], end["failed_step"]) == ("failed", 1, "player")
    assert not end["published"]
    # the resume: a manual run from the failed step, with --promote (what --auto would do)
    out2 = run(
        sched,
        season=2026,
        week=5,
        now=TUESDAY_W5,
        from_step="player",
        promote=True,
        funcs=fake([]),
        run_id="20261006T150000Z",
    )
    assert out2.exit_code == 0
    files = sorted((paths.run_dir(2026, 5) / "events").glob("*.jsonl"))
    assert [f.stem for f in files] == ["20261006T140000Z", "20261006T150000Z"]
    evs2 = [json.loads(x) for x in files[1].read_text().splitlines()]
    s = evs2[0]
    assert s["kind"] == "resume" and s["from_step"] == "player"
    assert s["steps"] == ["player", "digest"]
    assert s["command"] == "nfl weekly run --season 2026 --week 5 --from-step player --promote"
    assert set(s["earlier"]) == {"ingest", "ready", "curate", "ratings", "game", "graph"}
    assert s["earlier"]["game"]["status"] == "ok"


def test_not_ready_is_recorded_as_such(sched, paths) -> None:
    nr = W.StepFailed("ready", "2026 week 4: NOT READY (15/16 games final)", 3)
    run(sched, auto=True, now=TUESDAY_W5, funcs=fake([], {"ready": nr}))
    end = read_events(paths)[-1]
    assert (end["status"], end["exit_code"], end["failed_step"]) == ("not_ready", 3, None)


# ---- --expect-week ------------------------------------------------------------------------


def test_expect_week_mismatch_stops_before_anything(sched, paths) -> None:
    calls: list[str] = []
    out = run(sched, auto=True, now=TUESDAY_W5, funcs=fake(calls), expect_week=6)
    assert (out.status, out.exit_code, out.week) == ("week_mismatch", 6, 5)
    assert "week 5, not week 6" in out.message
    assert calls == []
    assert not paths.run_dir(2026, 5).exists()  # no state, records or events
    assert not (paths.runs / "2026" / "pipeline_history.parquet").exists()
    assert not L.is_locked(paths.runs / ".weekly.lock")  # released


def test_expect_week_matching_runs_and_dry_run_checks_it(sched, paths) -> None:
    assert run(sched, auto=True, now=TUESDAY_W5, dry_run=True, expect_week=5).exit_code == 0
    dry = run(sched, auto=True, now=TUESDAY_W5, dry_run=True, expect_week=4)
    assert (dry.status, dry.exit_code) == ("week_mismatch", 6)
    assert not paths.runs.exists() or not any(paths.runs.rglob("*.json*"))
    assert run(sched, auto=True, now=TUESDAY_W5, funcs=fake([]), expect_week=5).exit_code == 0


def test_expect_week_in_the_offseason_is_a_mismatch(sched, paths) -> None:
    summer = dt.datetime(2026, 7, 1, 12, tzinfo=dt.UTC)
    out = run(sched, auto=True, now=summer, dry_run=True, expect_week=1)
    assert (out.status, out.exit_code, out.week) == ("week_mismatch", 6, None)
    assert run(sched, auto=True, now=summer, dry_run=True).status == "idle"


def test_cli_expect_week_needs_auto_and_app_run_is_checked(monkeypatch) -> None:
    seen = {}

    def fake_pipeline(*a, **k):
        seen.update(k)
        return W.PipelineOutcome("planned", 0, 2026, 5, "dry run", None)

    monkeypatch.setattr(W, "run_pipeline", fake_pipeline)
    r = CliRunner().invoke(
        app, ["weekly", "run", "--season", "2026", "--week", "5", "--expect-week", "5"]
    )
    assert r.exit_code == 2
    r = CliRunner().invoke(app, ["weekly", "run", "--auto", "--app-run", "../x"])
    assert r.exit_code == 2
    from nflengine import tracking

    try:
        r = CliRunner().invoke(
            app,
            [
                "weekly",
                "run",
                "--auto",
                "--expect-week",
                "5",
                "--app-run",
                "20261006T140000Z-ab12",
                "--launched-by",
                "rishi",
            ],
        )
        assert r.exit_code == 0, r.output
        assert seen["expect_week"] == 5 and seen["run_id"] == "20261006T140000Z-ab12"
        assert seen["via"] == "control-room" and tracking.LAUNCH_VIA == "control-room"
    finally:
        tracking.set_launch_via(None)


def test_cli_exit_6_on_a_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        W,
        "run_pipeline",
        lambda *a, **k: W.PipelineOutcome(
            "week_mismatch",
            6,
            2026,
            5,
            "the calendar's week is week 5, not week 6 (--expect-week): nothing was run",
        ),
    )
    r = CliRunner().invoke(app, ["weekly", "run", "--auto", "--expect-week", "6"])
    assert r.exit_code == 6 and "nothing was run" in r.output


def test_via_tag_reaches_wandb_runs(monkeypatch) -> None:
    import sys
    import types

    from nflengine import tracking

    captured = {}
    fake_wandb = types.SimpleNamespace(init=lambda **k: captured.update(k) or "run")
    monkeypatch.setitem(sys.modules, "wandb", fake_wandb)
    monkeypatch.setattr(tracking, "_wandb_env", lambda: ("proj", None, DataPaths(Path("."))))
    try:
        tracking.set_launch_via("control-room")
        tracking.init_run("g", "j", launched_by="rishi")
        assert captured["tags"] == ["launched-by:rishi", "via:control-room"]
        tracking.set_launch_via(None)
        tracking.init_run("g", "j", launched_by="rishi")
        assert captured["tags"] == ["launched-by:rishi"]
    finally:
        tracking.set_launch_via(None)


# ---- the injury update and rehearsals -----------------------------------------------------


def test_injury_update_writes_events(tmp_path, monkeypatch) -> None:
    import nflengine.ops.injury_update as IU
    import nflengine.paths as P

    p = DataPaths(tmp_path)
    monkeypatch.setattr(P, "ensure_data_root", lambda *a, **k: p)

    class Res:
        material = True
        notes: list[str] = []
        finished = dt.datetime.now(dt.UTC)
        report_path = "reports/2026/week05-injury-update.md"
        summary_path = "injury_update.json"
        url = None

    def fake_update(season, week, **kw):
        events.progress(None, 1, 4, "re-predicting the unstarted games")
        kw["log"]("[green]compared 15 games[/]")
        return Res()

    monkeypatch.setattr(IU, "run_injury_update", fake_update)
    r = CliRunner().invoke(
        app,
        [
            "weekly",
            "injury-update",
            "--season",
            "2026",
            "--week",
            "5",
            "--app-run",
            "20261010T140000Z-cd34",
        ],
    )
    assert r.exit_code == 0, r.output
    evs = read_events(p)
    assert evs[0]["type"] == "run_start" and evs[0]["kind"] == "injury_update"
    assert evs[0]["via"] == "control-room"
    assert [e["type"] for e in evs[1:]] == ["step_start", "progress", "log", "step_end", "run_end"]
    assert evs[-1]["material"] is True and evs[-1]["status"] == "ok"


def test_rehearsal_lock_events_and_fail_at(tmp_path, monkeypatch) -> None:
    import nflengine.ops.rehearsal as R

    live = DataPaths(tmp_path / "live")
    for d in ("raw", "curated", "features", "runs"):
        (live.root / d).mkdir(parents=True)
    monkeypatch.setattr(R, "ensure_data_root", lambda *a, **k: live)
    monkeypatch.setattr(R, "seed", lambda *a, **k: [])
    monkeypatch.setattr("nflengine.digest.run.read_games", lambda paths: None)
    monkeypatch.setattr(
        "nflengine.digest.run.tuesday_before",
        lambda games, s, w: dt.datetime(2026, 9, 29, 14, tzinfo=dt.UTC),
    )
    monkeypatch.setattr("nflengine.ops.calendar.load_schedules", lambda p: None)
    monkeypatch.setattr(
        "nflengine.ops.calendar.plan_for",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no calendar in this test")),
    )
    calls: list[str] = []
    funcs = {n: f for n, f in fake(calls).items() if n in R.STEPS}
    out = tmp_path / "live" / "rehearsals" / "control-room"
    res = R.run_rehearsal(
        2026,
        4,
        root=out,
        funcs=funcs,
        fresh=True,
        fail_at="player",
        run_id="20261006T060000Z-ef56",
        log=lambda *a, **k: None,
    )
    assert res.status == "failed" and "injected failure" in (res.error or "")
    assert calls == ["ready", "game"]  # the hook replaced the player step
    ev_file = out / "runs" / "2026" / "week04" / "events" / "20261006T060000Z-ef56.jsonl"
    evs = [json.loads(x) for x in ev_file.read_text().splitlines()]
    assert evs[0]["kind"] == "rehearsal" and evs[0]["steps"] == list(R.STEPS)
    assert evs[-1]["status"] == "failed" and evs[-1]["failed_step"] == "player"
    assert not L.is_locked(R.lock_file(out))
    # the resume: the remaining steps, with the earlier ones in `earlier`
    calls.clear()
    res2 = R.run_rehearsal(
        2026,
        4,
        root=out,
        steps=["player", "digest"],
        funcs=funcs,
        run_id="20261006T061000Z-ef57",
        log=lambda *a, **k: None,
    )
    assert res2.status == "ok" and calls == ["player", "digest"]
    ev2 = (ev_file.parent / "20261006T061000Z-ef57.jsonl").read_text().splitlines()
    start = json.loads(ev2[0])
    assert start["from_step"] == "player" and set(start["earlier"]) == {"ready", "game"}
    # a second rehearsal into the same folder while one holds it: refused before any write
    with L.run_lock(R.lock_file(out), "another rehearsal"), pytest.raises(L.LockHeld):
        R.run_rehearsal(2026, 4, root=out, funcs=funcs, fresh=True, log=lambda *a, **k: None)
    assert ev_file.exists()  # the --fresh wipe never ran


# ---- the replace retry ----------------------------------------------------------------------


def test_replace_file_retries_while_a_reader_holds_the_target(tmp_path, monkeypatch) -> None:
    src, dst = tmp_path / "a.tmp", tmp_path / "a.json"
    src.write_text("new")
    dst.write_text("old")
    real = os.replace
    tries = {"n": 0}

    def flaky(a, b):
        tries["n"] += 1
        if tries["n"] < 3:
            raise PermissionError("in use")
        return real(a, b)

    monkeypatch.setattr(fsutil.os, "replace", flaky)
    monkeypatch.setattr(fsutil, "REPLACE_WAIT_S", 0.0)
    fsutil.replace_file(src, dst)
    assert dst.read_text() == "new" and tries["n"] == 3
    src.write_text("again")
    tries["n"] = -100
    with pytest.raises(PermissionError):
        fsutil.replace_file(src, dst, tries=3)
