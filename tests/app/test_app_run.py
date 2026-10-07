"""CR02: pre-flight, the runner's allowlist, POST /api/run, re-attaching to a run, and the
event stream. The runner never starts a real process here: a fake launcher records the argv
(and `tests/conftest.py` makes the real launcher refuse)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from app_helpers import ALL_OK, BASE, TOKEN, TUESDAY, final_through_week4, make_client, write_week

from nflengine.app.runner import Runner, app_dir
from nflengine.ops import lock as L
from nflengine.paths import DataPaths

POST = {"origin": BASE, "x-cr-token": TOKEN}
SATURDAY = dt.datetime(2026, 10, 10, 16, 0, tzinfo=dt.UTC)  # Sat 2026-10-10 12:00 ET


class FakeProc:
    def __init__(self, pid: int = 4242, code: int | None = None):
        self.pid, self.code = pid, code

    def poll(self):
        return self.code


class FakeLauncher:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.procs: list[FakeProc] = []

    def __call__(self, argv: list[str], log_file: Path) -> FakeProc:
        self.calls.append(list(argv))
        log_file.write_text("starting\n", encoding="utf-8")
        proc = FakeProc(pid=4242 + len(self.calls))
        self.procs.append(proc)
        return proc


def client_for(tmp_path, *, now=TUESDAY, sched=None, rehearsal=False, fail_at=None, **kw):
    launcher = FakeLauncher()
    runner = Runner(rehearsal=rehearsal, fail_at=fail_at, launcher=launcher, python="PY")
    c = make_client(
        tmp_path,
        now=now,
        sched=final_through_week4() if sched is None else sched,
        runner=runner,
        rehearsal=rehearsal,
        **kw,
    )
    return c, launcher, runner, DataPaths(tmp_path)


def checks(pf: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in pf["checks"]}


# ---- pre-flight ---------------------------------------------------------------------------


def test_preflight_on_a_ready_tuesday(tmp_path) -> None:
    c, *_ = client_for(tmp_path)
    pf = c.get("/api/preflight").json()
    assert (pf["mode"], pf["season"], pf["week"]) == ("live", 2026, 5)
    ch = checks(pf)
    assert set(ch) == {
        "calendar",
        "last_week_final",
        "not_published",
        "lock",
        "data_root",
        "neo4j",
        "keys",
        "season",
    }
    assert ch["calendar"]["value"].startswith("2026 week 5 · 15 games")
    assert (
        ch["last_week_final"]["status"] == "ok" and ch["last_week_final"]["value"] == "16/16 final"
    )
    assert (
        ch["neo4j"]["status"] == "warn" and not ch["neo4j"]["blocking"]
    )  # down: the run starts it
    assert ch["data_root"]["value"] == "found"  # never the path
    assert pf["run"] == {
        "allowed": True,
        "reason": None,
        "command": "nfl weekly run --auto --expect-week 5",
        "retry_after": None,
    }
    assert pf["confirm"]["last_week"] == "week 4 · 16/16 final"
    assert pf["confirm"]["publishes"] == ["week05-digest.md", "production alias"]
    assert pf["confirm"]["deadline"].startswith("2026-10-09T00:15")
    assert pf["resume"]["allowed"] is False and pf["resume"]["step"] is None
    assert pf["injury_update"]["allowed"] is False
    assert pf["injury_update"]["reason"] == "Needs this week's Tuesday run first."
    texts = [line["text"] for line in pf["dry_run_log"]]
    assert texts[0] == "$ nfl weekly run --auto --expect-week 5 --dry-run"
    assert texts[-1].startswith("dry run: would run ingest, ready, curate")


def test_a_stale_snapshot_only_warns(tmp_path) -> None:
    from app_helpers import raw_sched

    c, *_ = client_for(tmp_path, sched=raw_sched())  # week 4 has only Thursday's result
    pf = c.get("/api/preflight").json()
    ch = checks(pf)["last_week_final"]
    assert ch["status"] == "warn" and not ch["blocking"]
    assert ch["value"].startswith("1/16 final")
    assert "exit 3" in ch["reason"]
    assert pf["run"]["allowed"] is True


def test_a_recent_not_ready_run_blocks_for_a_while(tmp_path) -> None:
    from app_helpers import raw_sched

    paths = DataPaths(tmp_path)
    finished = (TUESDAY - dt.timedelta(minutes=5)).astimezone().isoformat(timespec="seconds")
    run_dir = paths.run_dir(2026, 5)
    run_dir.mkdir(parents=True)
    state = {
        "steps": {
            "ready": {
                "status": "failed",
                "started": finished,
                "finished": finished,
                "detail": "2026 week 4: 15/16 games final; missing ['x']",
            }
        }
    }
    (run_dir / "weekly_run.json").write_text(json.dumps(state))
    c, *_ = client_for(tmp_path, sched=raw_sched())
    pf = c.get("/api/preflight").json()
    ch = checks(pf)["last_week_final"]
    assert ch["status"] == "fail" and "Try again after" in ch["reason"]
    assert pf["run"]["allowed"] is False and pf["run"]["reason"] == ch["reason"]
    assert pf["run"]["retry_after"] is not None
    later, *_ = client_for(tmp_path, sched=raw_sched(), now=TUESDAY + dt.timedelta(minutes=30))
    assert later.get("/api/preflight").json()["run"]["allowed"] is True  # the run checks again


def test_published_week_locked_run_and_missing_keys_block(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    c, *_ = client_for(tmp_path, keys_set=lambda names: {n: n != "WANDB_API_KEY" for n in names})
    pf = c.get("/api/preflight").json()
    assert checks(pf)["keys"]["status"] == "fail"
    assert checks(pf)["keys"]["value"] == "WANDB_API_KEY not set"
    assert pf["run"]["allowed"] is False
    write_week(paths, 2026, 5, ALL_OK, report=True)
    c2, *_ = client_for(tmp_path)
    pf = c2.get("/api/preflight").json()
    assert checks(pf)["not_published"]["status"] == "fail" and not pf["run"]["allowed"]
    with L.run_lock(L.lock_path(paths.runs), "nfl weekly run --auto"):
        pf = c2.get("/api/preflight").json()
        assert checks(pf)["lock"]["status"] == "fail"


# ---- the runner's allowlist ---------------------------------------------------------------


def test_run_weekly_builds_the_command_in_code(tmp_path) -> None:
    c, launcher, _, paths = client_for(tmp_path)
    r = c.post("/api/run", json={"kind": "weekly", "expect_week": 5}, headers=POST)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["command"] == "nfl weekly run --auto --expect-week 5"
    assert (body["season"], body["week"], body["kind"]) == (2026, 5, "weekly")
    argv = launcher.calls[0]
    assert argv == [
        "PY",
        "-m",
        "nflengine.cli",
        "weekly",
        "run",
        "--auto",
        "--expect-week",
        "5",
        "--launched-by",
        "rishi",
        "--app-run",
        body["run_id"],
    ]
    rec = json.loads((app_dir(paths) / "runs" / f"{body['run_id']}.json").read_text())
    assert rec["pid"] == launcher.procs[0].pid and rec["kind"] == "weekly"
    assert str(tmp_path) not in r.text


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "weekly", "expect_week": 6},  # not the calendar's week
        {"kind": "weekly", "expect_week": 4},
    ],
)
def test_a_browser_week_that_differs_is_refused(tmp_path, body) -> None:
    c, launcher, *_ = client_for(tmp_path)
    r = c.post("/api/run", json=body, headers=POST)
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_allowed"
    assert "reload the page" in r.json()["error"]["message"]
    assert launcher.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "graph_rebuild", "expect_week": 5},
        {"kind": "weekly", "expect_week": 5, "command": "nfl weekly run --force"},
        {"kind": "weekly", "expect_week": 5, "season": 2025},
        {"kind": "weekly", "expect_week": "5; rm -rf /"},
        {"kind": "weekly", "expect_week": 99},
        {"kind": "weekly"},
        {"kind": ["weekly"], "expect_week": 5},
        {"argv": ["nfl", "weekly", "run"]},
    ],
)
def test_every_other_shape_is_refused(tmp_path, body) -> None:
    c, launcher, *_ = client_for(tmp_path)
    r = c.post("/api/run", json=body, headers=POST)
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_request"
    assert "rm -rf" not in r.text and "--force" not in r.text  # nothing echoed
    assert launcher.calls == []


def test_post_needs_the_token_and_origin(tmp_path) -> None:
    c, launcher, *_ = client_for(tmp_path)
    body = {"kind": "weekly", "expect_week": 5}
    assert c.post("/api/run", json=body).status_code == 403
    assert c.post("/api/run", json=body, headers={"origin": BASE}).status_code == 403
    bad = {"origin": "http://evil.example", "x-cr-token": TOKEN}
    assert c.post("/api/run", json=body, headers=bad).status_code == 403
    assert launcher.calls == []


def test_lock_held_is_409_and_a_running_child_is_409(tmp_path) -> None:
    c, launcher, _, paths = client_for(tmp_path)
    body = {"kind": "weekly", "expect_week": 5}
    with L.run_lock(L.lock_path(paths.runs), "nfl weekly run --season 2026 --week 5"):
        r = c.post("/api/run", json=body, headers=POST)
        assert r.status_code == 409 and r.json()["error"]["code"] == "locked"
    assert c.post("/api/run", json=body, headers=POST).status_code == 202
    r = c.post("/api/run", json=body, headers=POST)  # the fake child is still "alive"
    assert r.status_code == 409 and r.json()["error"]["code"] == "running"
    assert len(launcher.calls) == 1


def test_resume_only_from_the_failed_step(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    c, launcher, *_ = client_for(tmp_path)
    r = c.post("/api/run", json={"kind": "resume", "expect_week": 5}, headers=POST)
    assert r.status_code == 403  # nothing failed
    steps = {
        **{k: "ok" for k in ("ingest", "ready", "curate", "ratings", "game", "graph")},
        "player": "failed",
    }
    write_week(paths, 2026, 5, steps, details={"player": "refit failed"})
    write_week(paths, 2026, 4, {**ALL_OK, "digest": "failed"})  # an older week: never offered
    pf = c.get("/api/preflight").json()
    assert pf["resume"]["allowed"] and pf["resume"]["step"] == "player"
    assert pf["resume"]["command"] == (
        "nfl weekly run --season 2026 --week 5 --from-step player --promote"
    )
    r = c.post("/api/run", json={"kind": "resume", "expect_week": 5}, headers=POST)
    assert r.status_code == 202
    assert launcher.calls[0][4:] == [
        "run",
        "--season",
        "2026",
        "--week",
        "5",
        "--from-step",
        "player",
        "--promote",
        "--launched-by",
        "rishi",
        "--app-run",
        r.json()["run_id"],
    ]


def test_an_interrupted_step_counts_as_failed_for_resume(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    write_week(paths, 2026, 5, {"ingest": "ok", "ready": "ok", "curate": "running"})
    c, *_ = client_for(tmp_path)
    assert c.get("/api/preflight").json()["resume"]["step"] == "curate"


@pytest.mark.parametrize(
    ("now", "published", "allowed", "reason"),
    [
        (SATURDAY, True, True, None),
        (SATURDAY, False, False, "Needs this week's Tuesday run first."),
        (SATURDAY - dt.timedelta(days=1), True, False, "Opens Saturday Oct 10 (ET)."),
        # Monday night after week 5's last kickoff: the calendar has moved on to week 6
        (
            dt.datetime(2026, 10, 13, 2, tzinfo=dt.UTC),
            True,
            False,
            "Needs this week's Tuesday run first.",
        ),
    ],
)
def test_injury_update_timing(tmp_path, now, published, allowed, reason) -> None:
    paths = DataPaths(tmp_path)
    if published:
        write_week(paths, 2026, 5, ALL_OK, report=True)
    c, launcher, *_ = client_for(tmp_path, now=now)
    gate = c.get("/api/preflight").json()["injury_update"]
    assert (gate["allowed"], gate["reason"]) == (allowed, reason)
    r = c.post("/api/run", json={"kind": "injury_update", "expect_week": 5}, headers=POST)
    if allowed:
        assert r.status_code == 202
        assert launcher.calls[0][4:9] == [
            "injury-update",
            "--auto",
            "--expect-week",
            "5",
            "--launched-by",
        ]
    else:
        assert r.status_code == 403 and launcher.calls == []


# ---- re-attaching ---------------------------------------------------------------------------


def write_events(path: Path, events: list[dict[str, Any]], run_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {"seq": i + 1, "t": f"2026-10-06T10:00:{min(i, 59):02d}-04:00", "run_id": run_id, **e}
        )
        for i, e in enumerate(events)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def story(root: Path, end: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    evs: list[dict[str, Any]] = [
        {
            "type": "run_start",
            "kind": "weekly",
            "command": "nfl weekly run --auto --expect-week 5",
            "season": 2026,
            "week": 5,
            "steps": ["ingest"],
            "from_step": None,
            "earlier": {},
            "launched_by": "rishi",
            "via": "control-room",
            "plan": [],
        },
        {"type": "step_start", "step": "ingest"},
        {
            "type": "progress",
            "step": "ingest",
            "done": 1,
            "total": 2,
            "fraction": 0.5,
            "label": "dataset 1/2 · nflverse · pbp",
        },
        {"type": "log", "step": "ingest", "text": f"wrote {root}\\raw\\nflverse\\pbp", "cls": ""},
        {
            "type": "step_end",
            "step": "ingest",
            "status": "ok",
            "seconds": 3.0,
            "detail": f"manifest {root}\\raw\\_runs\\ingest.json",
        },
    ]
    if end is not None:
        evs.append({"type": "run_end", **end})
    return evs


def test_current_follows_the_run_and_its_end(tmp_path) -> None:
    c, launcher, runner, paths = client_for(tmp_path)
    assert c.get("/api/run/current").json() == {"state": "idle", "run": None}
    rid = c.post("/api/run", json={"kind": "weekly", "expect_week": 5}, headers=POST).json()[
        "run_id"
    ]
    cur = c.get("/api/run/current").json()
    assert cur["state"] == "running" and cur["run"]["run_id"] == rid
    assert cur["run"]["launched_from"] == "app" and "_events" not in cur["run"]
    # the child writes its events and ends
    ev = paths.run_dir(2026, 5) / "events" / f"{rid}.jsonl"
    write_events(
        ev,
        story(
            tmp_path,
            {
                "status": "ok",
                "exit_code": 0,
                "message": "2026 week 05: ok",
                "published": True,
                "failed_step": None,
                "material": None,
            },
        ),
        rid,
    )
    launcher.procs[0].code = 0
    cur = c.get("/api/run/current").json()
    assert cur["state"] == "finished"
    run = cur["run"]
    assert (run["status"], run["exit_code"], run["published"], run["has_events"]) == (
        "ok",
        0,
        True,
        True,
    )
    assert run["output_tail"] == []


def test_a_run_that_wrote_no_events_reports_its_exit_code(tmp_path) -> None:
    c, launcher, _, paths = client_for(tmp_path)
    rid = c.post("/api/run", json={"kind": "weekly", "expect_week": 5}, headers=POST).json()[
        "run_id"
    ]
    log = app_dir(paths) / "logs" / f"{rid}.log"
    log.write_text(
        f"the calendar's week is week 6, not week 5 (--expect-week): nothing was "
        f"run\nlog at {tmp_path}\\x\n",
        encoding="utf-8",
    )
    launcher.procs[0].code = 6
    run = c.get("/api/run/current").json()["run"]
    assert (run["status"], run["exit_code"], run["has_events"]) == ("week_mismatch", 6, False)
    assert any("nothing was run" in x for x in run["output_tail"])
    assert all(str(tmp_path) not in x for x in run["output_tail"])


def test_after_an_app_restart_a_dead_run_reads_as_interrupted(tmp_path) -> None:
    c, launcher, _, paths = client_for(tmp_path)
    rid = c.post("/api/run", json={"kind": "weekly", "expect_week": 5}, headers=POST).json()[
        "run_id"
    ]
    write_events(paths.run_dir(2026, 5) / "events" / f"{rid}.jsonl", story(tmp_path), rid)
    fresh, *_ = client_for(tmp_path)  # a new app: no child handle, no lock held
    run = fresh.get("/api/run/current").json()["run"]
    assert run["run_id"] == rid and run["status"] == "interrupted"


def test_a_terminal_run_holding_the_lock_is_found(tmp_path) -> None:
    c, _, _, paths = client_for(tmp_path)
    write_events(
        paths.run_dir(2026, 5) / "events" / "20261006T140000Z.jsonl",
        story(tmp_path),
        "20261006T140000Z",
    )
    with L.run_lock(L.lock_path(paths.runs), "nfl weekly run --auto --expect-week 5"):
        cur = c.get("/api/run/current").json()
    assert cur["state"] == "running"
    run = cur["run"]
    assert (run["launched_from"], run["kind"], run["season"], run["week"]) == (
        "terminal",
        "weekly",
        2026,
        5,
    )
    assert run["run_id"] == "20261006T140000Z" and run["has_events"]


# ---- the stream -----------------------------------------------------------------------------


def seq_of(msg: dict[str, Any]) -> int:
    """`id: <run id>:<seq>` (Sol review: cursors are bound to their run)."""
    return int(msg["id"].rsplit(":", 1)[1])


def parse_sse(text: str) -> list[dict[str, Any]]:
    out = []
    for block in text.split("\n\n"):
        msg: dict[str, Any] = {}
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            k, _, v = line.partition(": ")
            msg[k] = v
        if "data" in msg:
            msg["data"] = json.loads(msg["data"])
            out.append(msg)
    return out


def finished_run(tmp_path) -> tuple[Any, str]:
    c, launcher, _, paths = client_for(tmp_path)
    rid = c.post("/api/run", json={"kind": "weekly", "expect_week": 5}, headers=POST).json()[
        "run_id"
    ]
    end = {
        "status": "failed",
        "exit_code": 1,
        "message": "step 'player' failed",
        "published": False,
        "failed_step": "player",
        "material": None,
    }
    write_events(paths.run_dir(2026, 5) / "events" / f"{rid}.jsonl", story(tmp_path, end), rid)
    launcher.procs[0].code = 1
    return c, rid


def test_stream_replays_the_run_then_ends(tmp_path) -> None:
    c, rid = finished_run(tmp_path)
    r = c.get("/api/run/stream")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-store"
    msgs = parse_sse(r.text)
    runs = [m for m in msgs if m.get("event") == "run"]
    assert [seq_of(m) for m in runs] == [1, 2, 3, 4, 5, 6]
    assert runs[-1]["data"]["type"] == "run_end"
    end = msgs[-1]
    assert end["event"] == "end" and end["data"]["status"] == "failed"
    assert end["data"]["failed_step"] == "player" and "_events" not in end["data"]
    assert str(tmp_path) not in r.text  # the data root's path never leaves
    assert "raw\\\\nflverse" in r.text or "raw/nflverse" in r.text or "raw\\nflverse" in r.text


def test_stream_resumes_after_last_event_id(tmp_path) -> None:
    c, _ = finished_run(tmp_path)
    msgs = parse_sse(c.get("/api/run/stream", headers={"last-event-id": "4"}).text)
    assert [seq_of(m) for m in msgs if m.get("event") == "run"] == [5, 6]
    msgs = parse_sse(c.get("/api/run/stream?after=5").text)
    assert [seq_of(m) for m in msgs if m.get("event") == "run"] == [6]
    assert c.get("/api/run/stream?after=-1").status_code == 422


def test_stream_without_events_ends_with_the_exit_code(tmp_path) -> None:
    c, launcher, *_ = client_for(tmp_path)
    c.post("/api/run", json={"kind": "weekly", "expect_week": 5}, headers=POST)
    launcher.procs[0].code = 6
    msgs = parse_sse(c.get("/api/run/stream").text)
    assert [m["event"] for m in msgs] == ["end"]
    assert msgs[0]["data"]["status"] == "week_mismatch"


def test_stream_with_nothing_to_follow(tmp_path) -> None:
    c, *_ = client_for(tmp_path)
    msgs = parse_sse(c.get("/api/run/stream").text)
    assert msgs == [{"event": "end", "data": None}]


# ---- rehearsal mode -------------------------------------------------------------------------


def test_rehearsal_mode_rehearses_the_newest_published_week(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    write_week(paths, 2026, 4, ALL_OK, report=True)
    c, launcher, *_ = client_for(tmp_path, rehearsal=True, fail_at="player")
    pf = c.get("/api/preflight").json()
    assert (pf["mode"], pf["season"], pf["week"]) == ("rehearsal", 2026, 4)
    assert pf["run"]["command"] == (
        "nfl weekly rehearse --season 2026 --week 4 --out rehearsals/control-room --fresh"
    )
    assert pf["injury_update"]["allowed"] is False
    assert (
        c.post(
            "/api/run", json={"kind": "injury_update", "expect_week": 4}, headers=POST
        ).status_code
        == 403
    )
    r = c.post("/api/run", json={"kind": "weekly", "expect_week": 4}, headers=POST)
    assert r.status_code == 202 and r.json()["kind"] == "rehearsal"
    argv = launcher.calls[0]
    root = str(tmp_path / "rehearsals" / "control-room")
    assert argv[3:] == [
        "weekly",
        "rehearse",
        "--season",
        "2026",
        "--week",
        "4",
        "--out",
        root,
        "--fresh",
        "--fail-at",
        "player",
        "--app-run",
        r.json()["run_id"],
    ]
    assert root not in r.text  # the absolute folder stays on the server


def test_rehearsal_resume_and_the_failure_hook_fires_once(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    write_week(paths, 2026, 4, ALL_OK, report=True)
    c, launcher, *_ = client_for(tmp_path, rehearsal=True, fail_at="player")
    c.post("/api/run", json={"kind": "weekly", "expect_week": 4}, headers=POST)
    launcher.procs[0].code = 1
    res_dir = tmp_path / "rehearsals" / "control-room" / "runs" / "2026" / "week04"
    res_dir.mkdir(parents=True)
    (res_dir / "rehearsal.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "error": "player: injected failure (rehearsal test hook, --fail-at)",
            }
        )
    )
    pf = c.get("/api/preflight").json()
    assert pf["resume"]["allowed"] and pf["resume"]["step"] == "player"
    r = c.post("/api/run", json={"kind": "resume", "expect_week": 4}, headers=POST)
    assert r.status_code == 202
    assert launcher.calls[1][3:10] == [
        "weekly",
        "rehearse",
        "--season",
        "2026",
        "--week",
        "4",
        "--steps",
    ]
    assert launcher.calls[1][10] == "player,digest" and "--fail-at" not in launcher.calls[1]
    launcher.procs[1].code = 0
    c.post("/api/run", json={"kind": "weekly", "expect_week": 4}, headers=POST)
    assert "--fail-at" not in launcher.calls[2]  # once per app session


def test_gets_write_nothing_but_post_writes_only_its_own_folder(tmp_path) -> None:
    c, *_ = client_for(tmp_path)

    def inventory() -> set[str]:
        return {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")}

    before = inventory()
    for url in ("/api/preflight", "/api/run/current", "/api/run/stream"):
        c.get(url)
    assert inventory() == before
    c.post("/api/run", json={"kind": "weekly", "expect_week": 5}, headers=POST)
    new = inventory() - before
    assert new and all(x.startswith(str(Path("cache") / "control-room")) for x in new)


def test_a_finished_week_shows_its_own_log_from_the_events(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    write_week(paths, 2026, 4, ALL_OK, report=True)
    c, *_ = client_for(tmp_path)
    assert c.get("/api/weeks/2026/4/pipeline").json()["log_source"] == "rebuilt"
    end = {
        "status": "ok",
        "exit_code": 0,
        "message": "2026 week 04: ok",
        "published": True,
        "failed_step": None,
        "material": None,
    }
    write_events(
        paths.run_dir(2026, 4) / "events" / "20261004T071800Z.jsonl",
        story(tmp_path, end),
        "20261004T071800Z",
    )
    p = c.get("/api/weeks/2026/4/pipeline").json()
    assert p["log_source"] == "events"
    texts = [line["text"] for line in p["log"]]
    assert texts[0] == "$ nfl weekly run --auto --expect-week 5"
    assert any(t.startswith("wrote raw") for t in texts) and str(tmp_path) not in str(p)
    assert texts[-1] == "ok: 2026 week 04: ok (exit 0)"


def test_stream_of_a_terminal_run_ends_with_its_own_run_end(tmp_path) -> None:
    import threading

    c, _, _, paths = client_for(tmp_path)
    ev = paths.run_dir(2026, 5) / "events" / "20261006T140000Z.jsonl"
    write_events(ev, story(tmp_path), "20261006T140000Z")
    end = {
        "status": "ok",
        "exit_code": 0,
        "message": "2026 week 05: ok",
        "published": True,
        "failed_step": None,
        "material": None,
    }
    held = threading.Event()
    release = threading.Event()

    def terminal_run() -> None:
        with L.run_lock(L.lock_path(paths.runs), "nfl weekly run --auto --expect-week 5"):
            held.set()
            release.wait(5)
            write_events(ev, story(tmp_path, end), "20261006T140000Z")  # the run's last line

    t = threading.Thread(target=terminal_run)
    t.start()
    held.wait(5)
    threading.Timer(0.3, release.set).start()
    msgs = parse_sse(c.get("/api/run/stream").text)
    t.join()
    final = msgs[-1]
    assert final["event"] == "end"
    assert (
        final["data"]["status"],
        final["data"]["launched_from"],
        final["data"]["published"],
    ) == ("ok", "terminal", True)
    assert [m["data"]["type"] for m in msgs if m.get("event") == "run"][-1] == "run_end"


def test_the_records_step_is_timed_by_the_events(tmp_path) -> None:
    """The run summary is stamped before the W&B logging: its gap to the last step read 0 s
    (2026 week 5); the records step's own step_end has the real time (8.7 s)."""
    paths = DataPaths(tmp_path)
    summary = {"status": "ok", "finished": "2026-10-04T05:02:13", "steps": {}}
    write_week(paths, 2026, 4, ALL_OK, report=True, summary=summary)
    c, *_ = client_for(tmp_path)
    rec = next(
        s for s in c.get("/api/weeks/2026/4/pipeline").json()["steps"] if s["step"] == "records"
    )
    assert rec["seconds"] == 0.0  # from the summary's stamp, before any events existed
    end = {
        "status": "ok",
        "exit_code": 0,
        "message": "2026 week 04: ok",
        "published": True,
        "failed_step": None,
        "material": None,
    }
    evs = [
        *story(tmp_path),
        {"type": "step_start", "step": "records"},
        {"type": "step_end", "step": "records", "status": "ok", "seconds": 8.7, "detail": "x"},
        {"type": "run_end", **end},
    ]
    write_events(
        paths.run_dir(2026, 4) / "events" / "20261004T071800Z.jsonl", evs, "20261004T071800Z"
    )
    rec = next(
        s for s in c.get("/api/weeks/2026/4/pipeline").json()["steps"] if s["step"] == "records"
    )
    assert rec["seconds"] == 8.7
