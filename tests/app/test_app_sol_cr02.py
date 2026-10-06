"""CR02 Sol review (D104): one test per finding. The web fixes (10: the launch token after a
restart; 5: the stream never mixes runs) are in web/src/api/api.test.ts."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os

from app_helpers import ALL_OK, TUESDAY, final_through_week4, raw_sched
from test_app_run import POST, client_for, finished_run, parse_sse, seq_of, story, write_events
from typer.testing import CliRunner

import nflengine.app.runner as R
from nflengine.app.preflight import saturday_window
from nflengine.app.runner import Runner, public
from nflengine.app.stream import run_stream
from nflengine.cli import app
from nflengine.ops import events
from nflengine.ops import lock as L
from nflengine.ops.calendar import EASTERN, plan_for
from nflengine.paths import DataPaths


# 1. a capped event text never keeps part of the data root
def test_events_remove_the_root_before_capping(tmp_path, monkeypatch) -> None:
    root = tmp_path / "datadrive-secret-name"
    monkeypatch.setattr(events, "_data_roots", lambda: (root,))
    path = tmp_path / "e.jsonl"
    with events.writing(path, "20261006T140000Z"):
        events.step_start("ingest")
        log = events.tee(lambda *a, **k: None)
        log("x" * 590 + str(root) + "\\runs\\2026\\week05\\weekly_run.json")
        events.step_end("ingest", "ok", 1.0, "y" * 590 + str(root) + "\\raw\\_runs\\m.json")
    text = path.read_text(encoding="utf-8")
    assert "datadrive" not in text and "secret-name" not in text
    line = json.loads(text.splitlines()[1])
    assert line["text"].startswith("x" * 590 + "runs/")


# 2. the injury update checks the week it was confirmed for, before writing anything
def test_injury_update_expect_week(tmp_path, monkeypatch) -> None:
    import nflengine.ops.calendar as C
    import nflengine.paths as P

    p = DataPaths(tmp_path)
    monkeypatch.setattr(P, "ensure_data_root", lambda *a, **k: p)
    monkeypatch.setattr(C, "load_schedules", lambda paths=None: C.with_kickoffs(raw_sched()))
    monkeypatch.setattr(
        C, "plan_week", lambda sched, now=None, **k: plan_for(sched, 2026, 5, TUESDAY)
    )
    r = CliRunner().invoke(app, ["weekly", "injury-update", "--auto", "--expect-week", "6"])
    assert r.exit_code == 6 and "nothing was run" in r.output
    assert not any(tmp_path.rglob("*.jsonl")) and not any(tmp_path.rglob("*.parquet"))
    r = CliRunner().invoke(
        app, ["weekly", "injury-update", "--season", "2026", "--week", "5", "--expect-week", "5"]
    )
    assert r.exit_code == 2  # --expect-week needs --auto


def test_runner_sends_expect_week_with_the_injury_update(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    from app_helpers import write_week

    write_week(paths, 2026, 5, ALL_OK, report=True)
    saturday = dt.datetime(2026, 10, 10, 16, 0, tzinfo=dt.UTC)
    c, launcher, *_ = client_for(tmp_path, now=saturday)
    r = c.post("/api/run", json={"kind": "injury_update", "expect_week": 5}, headers=POST)
    assert r.status_code == 202
    assert r.json()["command"] == "nfl weekly injury-update --auto --expect-week 5"
    assert launcher.calls[0][6:8] == ["--expect-week", "5"]


# 3. resume picks the latest invocation's failure, not an older one
def test_resume_is_from_the_latest_failure(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    run_dir = paths.run_dir(2026, 5)
    run_dir.mkdir(parents=True)
    old = "2026-10-06T03:00:00-04:00"
    steps = {
        n: {"status": "ok", "started": old, "finished": old, "detail": ""}
        for n in ("ready", "curate", "ratings", "game", "graph")
    }
    steps["player"] = {"status": "failed", "started": old, "finished": old, "detail": "refit"}
    # a later full retry died during ingest (left running, no lock)
    steps["ingest"] = {"status": "running", "started": "2026-10-06T05:00:00-04:00"}
    (run_dir / "weekly_run.json").write_text(json.dumps({"steps": steps}))
    c, *_ = client_for(tmp_path)
    pf = c.get("/api/preflight").json()
    assert pf["resume"]["step"] == "ingest"
    assert c.get("/api/weeks/2026/5/pipeline").json()["failed_step"] == "ingest"


# 4. a recent not-ready run blocks even when the schedule says every game is final
def test_recent_not_ready_blocks_with_a_complete_schedule(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    finished = (TUESDAY - dt.timedelta(minutes=5)).astimezone().isoformat(timespec="seconds")
    run_dir = paths.run_dir(2026, 5)
    run_dir.mkdir(parents=True)
    detail = "2026 week 4: NOT READY (16/16 games final, 15/16 in play-by-play)"
    state = {
        "steps": {
            "ready": {
                "status": "failed",
                "started": finished,
                "finished": finished,
                "detail": detail,
            }
        }
    }
    (run_dir / "weekly_run.json").write_text(json.dumps(state))
    c, *_ = client_for(tmp_path, sched=final_through_week4())
    pf = c.get("/api/preflight").json()
    check = next(x for x in pf["checks"] if x["id"] == "last_week_final")
    assert check["status"] == "fail" and not pf["run"]["allowed"]


# 5. a reconnect cursor from another run replays the current run from its start
def test_a_cursor_from_another_run_replays_from_the_start(tmp_path) -> None:
    c, rid = finished_run(tmp_path)
    other = parse_sse(
        c.get("/api/run/stream", headers={"last-event-id": "20990101T000000Z:4"}).text
    )
    assert [seq_of(m) for m in other if m.get("event") == "run"] == [1, 2, 3, 4, 5, 6]
    same = parse_sse(c.get("/api/run/stream", headers={"last-event-id": f"{rid}:4"}).text)
    assert [seq_of(m) for m in same if m.get("event") == "run"] == [5, 6]
    assert all(m["id"].startswith(f"{rid}:") for m in same if m.get("event") == "run")


# 6. a terminal run that ends before the stream's first poll is still read to its run_end
def test_stream_reads_a_terminal_run_that_already_ended(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    ev = paths.run_dir(2026, 5) / "events" / "20261006T140000Z.jsonl"
    end = {
        "status": "ok",
        "exit_code": 0,
        "message": "ok",
        "published": True,
        "failed_step": None,
        "material": None,
    }
    write_events(ev, story(tmp_path, end), "20261006T140000Z")
    first = {
        "state": "running",
        "run": {
            "run_id": "20261006T140000Z",
            "started": "s",
            "launched_from": "terminal",
            "status": None,
            "_events": ev,
        },
    }
    calls = iter([first])

    def current():  # the lock is gone by the first poll: nothing current any more
        return next(calls, {"state": "idle", "run": None})

    async def collect() -> list[bytes]:
        async def no() -> bool:
            return False

        return [
            chunk
            async for chunk in run_stream(
                current=current,
                paths=paths,
                after=0,
                disconnected=no,
                poll_s=0.0,
                public=public,
            )
        ]

    msgs = parse_sse(b"".join(asyncio.run(collect())).decode("utf-8"))
    assert [m["data"]["type"] for m in msgs if m.get("event") == "run"][-1] == "run_end"
    assert msgs[-1]["event"] == "end" and msgs[-1]["data"]["status"] == "ok"


# 7. a held lock is matched by the run id in its note, never by a reused pid
def test_pid_reuse_does_not_attach_to_an_old_record(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    folder = R.app_dir(paths) / "runs"
    folder.mkdir(parents=True)
    old = {
        "run_id": "20261001T140000Z-aaaa",
        "kind": "weekly",
        "request_kind": "weekly",
        "command": "nfl weekly run --auto --expect-week 4",
        "season": 2026,
        "week": 4,
        "started": "2026-10-01T10:00:00-04:00",
        "pid": os.getpid(),
        "events_dir": str(paths.run_dir(2026, 4) / "events"),
    }
    (folder / f"{old['run_id']}.json").write_text(json.dumps(old))
    write_events(
        paths.run_dir(2026, 5) / "events" / "20261006T140000Z.jsonl",
        story(tmp_path),
        "20261006T140000Z",
    )
    with L.run_lock(
        L.lock_path(paths.runs), "nfl weekly run --season 2026 --week 5", run_id="20261006T140000Z"
    ):  # same pid as the old record
        cur = Runner(launcher=lambda *a: None).current(paths)
    assert cur["state"] == "running"
    run = cur["run"]
    assert (run["launched_from"], run["run_id"], run["week"]) == ("terminal", "20261006T140000Z", 5)
    assert run["has_events"]


# 8. after an app restart, a launch still starting counts as running and blocks a second one
def test_a_starting_launch_survives_an_app_restart(tmp_path, monkeypatch) -> None:
    paths = DataPaths(tmp_path)
    folder = R.app_dir(paths) / "runs"
    folder.mkdir(parents=True)
    now = dt.datetime.now(dt.UTC)
    rec = {
        "run_id": R.new_run_id(now),
        "kind": "weekly",
        "request_kind": "weekly",
        "command": "nfl weekly run --auto --expect-week 5",
        "season": 2026,
        "week": 5,
        "started": now.astimezone().isoformat(timespec="seconds"),
        "pid": 4242,
        "events_dir": str(paths.run_dir(2026, 5) / "events"),
    }
    (folder / f"{rec['run_id']}.json").write_text(json.dumps(rec))
    monkeypatch.setattr(R, "process_alive", lambda pid, launched, slack_s=60.0: pid == 4242)
    fresh = Runner(launcher=lambda *a: None)  # a restarted app: no child in memory
    assert fresh.current(paths)["state"] == "running"
    import pytest

    with pytest.raises(R.RunRefused) as e:
        fresh.start("weekly", 5, paths=paths, preflight={"week": 5}, now=now, lock_held=False)
    assert e.value.code == "running"
    monkeypatch.setattr(R, "process_alive", lambda pid, launched, slack_s=60.0: False)
    assert fresh.current(paths)["state"] == "finished"  # its process is gone


def test_process_alive_checks_the_creation_time() -> None:
    me = os.getpid()
    if os.name != "nt":
        assert R.process_alive(me, dt.datetime.now(dt.UTC))
        return
    created = R._win_process_created(me)
    assert created is not None
    assert R.process_alive(me, created)
    assert not R.process_alive(me, created + dt.timedelta(hours=1))  # a reused pid
    assert not R.process_alive(None, created) and not R.process_alive(-1, created)


# 9. the injury update's Saturday for slates that start on a Sunday, a Saturday or a Thursday
def test_saturday_window_for_every_kind_of_slate() -> None:
    sched = raw_sched()
    from nflengine.ops.calendar import with_kickoffs

    sched = with_kickoffs(sched)
    t = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)

    def opens(season: int, week: int) -> dt.date:
        o, c = saturday_window(plan_for(sched, season, week, t))
        assert o is not None and c is not None and o < c
        local = o.astimezone(EASTERN)
        assert local.weekday() == 5 and (local.hour, local.minute) == (0, 0)
        return local.date()

    assert opens(2026, 5) == dt.date(2026, 10, 10)  # Thursday start: the Saturday after
    assert opens(2024, 19) == dt.date(2025, 1, 11)  # Wild Card starts on Saturday
    assert opens(2024, 21) == dt.date(2025, 1, 25)  # conference round: Sunday only
    assert opens(2024, 22) == dt.date(2025, 2, 8)  # Super Bowl Sunday


# ---- the verification pass (D104) ------------------------------------------------------------


def test_a_root_cut_by_an_upstream_cap_never_reaches_the_browser(tmp_path, monkeypatch) -> None:
    from nflengine.ops.summary import scrub

    paths = DataPaths(tmp_path)
    run_dir = paths.run_dir(2026, 5)
    run_dir.mkdir(parents=True)
    # the pipeline caps a step detail at 400 characters before it's stored (weekly.scrub)
    detail = scrub("x" * 390 + str(tmp_path) + r"\runs\2026\week05\predictions.parquet", 400)
    assert detail.endswith("…") and str(tmp_path) not in detail  # only part of the root is left
    steps = {
        "ingest": {
            "status": "failed",
            "started": "2026-10-06T10:00:00-04:00",
            "finished": "2026-10-06T10:00:05-04:00",
            "detail": detail,
        }
    }
    (run_dir / "weekly_run.json").write_text(json.dumps({"steps": steps}))
    c, *_ = client_for(tmp_path)
    p = c.get("/api/weeks/2026/5/pipeline").json()
    # the rebuilt log keeps the whole stored detail (600 > 400): nothing but the x's may come
    # before its ellipsis (without the fix, the start of the root sat there)
    line = next(x["text"] for x in p["log"] if "ingest" in x["text"])
    assert line.endswith("x…"), line[-20:]
    # and in the events file, through the writer (its cap is 600: no second cut)
    monkeypatch.setattr(events, "_data_roots", lambda: (tmp_path,))
    ev = tmp_path / "e.jsonl"
    with events.writing(ev, "20261006T140000Z"):
        events.step_end("ingest", "failed", 5.0, detail)
    written = json.loads(ev.read_text(encoding="utf-8").splitlines()[0])["detail"]
    assert written == "x" * 390 + "…", written[-20:]


def test_injury_update_creates_no_folders_before_the_week_guard(tmp_path, monkeypatch) -> None:
    import nflengine.ops.calendar as C
    import nflengine.paths as P

    asked: list[bool] = []

    def root(*a, create_dirs: bool = True, **k):
        asked.append(create_dirs)
        return DataPaths(tmp_path)

    monkeypatch.setattr(P, "ensure_data_root", root)
    monkeypatch.setattr(C, "load_schedules", lambda paths=None: C.with_kickoffs(raw_sched()))
    monkeypatch.setattr(
        C, "plan_week", lambda sched, now=None, **k: plan_for(sched, 2026, 5, TUESDAY)
    )
    r = CliRunner().invoke(app, ["weekly", "injury-update", "--auto", "--expect-week", "6"])
    assert r.exit_code == 6 and asked == [False]


def test_an_old_not_ready_stop_doesnt_hide_a_later_failure(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    run_dir = paths.run_dir(2026, 5)
    run_dir.mkdir(parents=True)
    old = "2026-10-06T08:00:00-04:00"
    steps = {
        "ingest": {"status": "running", "started": "2026-10-06T11:00:00-04:00"},  # died
        "ready": {
            "status": "failed",
            "started": old,
            "finished": old,
            "detail": "2026 week 4: NOT READY (15/16 games final)",
        },
    }
    (run_dir / "weekly_run.json").write_text(json.dumps({"steps": steps}))
    c, *_ = client_for(tmp_path)
    p = c.get("/api/weeks/2026/5/pipeline").json()
    assert (p["state"], p["failed_step"]) == ("failed", "ingest")
    assert c.get("/api/preflight").json()["resume"]["step"] == "ingest"


def test_stream_follows_a_terminal_run_whose_events_file_appears_later(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    ev = paths.run_dir(2026, 5) / "events" / "20261006T140000Z.jsonl"  # not written yet
    first = {
        "state": "running",
        "run": {
            "run_id": "20261006T140000Z",
            "started": "s",
            "launched_from": "terminal",
            "status": None,
            "_events": None,
            "_events_path": ev,
        },
    }
    end = {
        "status": "ok",
        "exit_code": 0,
        "message": "ok",
        "published": True,
        "failed_step": None,
        "material": None,
    }
    polls = {"n": 0}

    def current():
        polls["n"] += 1
        if polls["n"] == 1:
            return first
        if polls["n"] == 2:  # between two polls the run wrote everything and let go of the lock
            write_events(ev, story(tmp_path, end), "20261006T140000Z")
        return {"state": "idle", "run": None}

    async def collect() -> list[bytes]:
        async def no() -> bool:
            return False

        return [
            chunk
            async for chunk in run_stream(
                current=current,
                paths=paths,
                after=0,
                disconnected=no,
                poll_s=0.0,
                public=public,
            )
        ]

    msgs = parse_sse(b"".join(asyncio.run(collect())).decode("utf-8"))
    assert [m["data"]["type"] for m in msgs if m.get("event") == "run"][-1] == "run_end"
    assert msgs[-1]["data"]["status"] == "ok"
