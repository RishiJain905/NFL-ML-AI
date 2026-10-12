"""The control room's **Update play calling** button (D132): the pre-flight gate, `POST /api/run`
with `playcalling_update`, re-attaching to the run (its own events folder, a terminal run's lock),
and the rule that a play-calling lock is not "the week's run".

The runner never starts a process (a fake launcher records the argv); the plan is the real
`plan_update` on a tmp tree (`tests/playcalling/playcall_update_fixtures.py`, shared with the
planner's own tests) at the same pinned Tuesday as the other run-control tests.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from app_helpers import ALL_OK, BASE, TOKEN, TUESDAY, final_through_week4, make_client, write_week

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "playcalling"))
import playcall_update_fixtures as U  # noqa: E402

from nflengine.app.preflight import playcalling_gate  # noqa: E402
from nflengine.app.readers.meta import is_playcalling_update, lock_target  # noqa: E402
from nflengine.app.runner import Runner, RunRefused, app_dir, build_launch  # noqa: E402
from nflengine.ops import lock as L  # noqa: E402
from nflengine.ops.calendar import plan_week  # noqa: E402
from nflengine.paths import DataPaths  # noqa: E402

POST = {"origin": BASE, "x-cr-token": TOKEN}
WED = U.MNF + dt.timedelta(hours=42)  # Wed 2026-10-07 18:00 UTC: Monday night's FTN is due
COMMAND = "nfl playcalling update --auto --expect-week 5"
STEPS = ["ftn", "build", "review", "grade", "forecast"]


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
        self.procs.append(FakeProc(pid=4242 + len(self.calls)))
        return self.procs[-1]


def client_for(tmp_path, *, now=TUESDAY, rehearsal=False, **kw):
    launcher = FakeLauncher()
    runner = Runner(rehearsal=rehearsal, launcher=launcher, python="PY")
    c = make_client(
        tmp_path, now=now, sched=final_through_week4(), runner=runner, rehearsal=rehearsal, **kw
    )
    return c, launcher, runner, DataPaths(tmp_path)


def only_forecast_due(paths: DataPaths) -> U.Tree:
    """Week 5's Tuesday: tables built, last week reviewed and graded, no forecast yet."""
    t = U.up_to_date_tree(paths)
    for name in ("forecast.parquet", "forecast.json"):
        (t.pc / "week05" / name).unlink()
    return t


def publish(paths: DataPaths) -> None:
    write_week(paths, 2026, 5, ALL_OK, report=True)  # the Tuesday run is out


def gate(c) -> dict[str, Any]:
    return c.get("/api/preflight").json()["playcalling"]


def step_map(g: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["step"]: s for s in g["steps"]}


# ---- the pre-flight gate -------------------------------------------------------------------------


def test_the_gate_waits_for_the_tuesday_run(tmp_path) -> None:
    only_forecast_due(DataPaths(tmp_path))
    c, *_ = client_for(tmp_path)
    g = gate(c)
    assert g["allowed"] is False
    assert g["reason"] == "Needs this week's Tuesday run first (Run week 5)."
    assert g["command"] == COMMAND
    assert [s["step"] for s in g["steps"]] == STEPS  # the plan is shown even while blocked
    assert [s["step"] for s in g["steps"] if s["due"]] == ["forecast"]


def test_the_gate_opens_when_a_step_is_due(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, *_ = client_for(tmp_path)
    g = gate(c)
    assert g == {
        "allowed": True,
        "reason": None,
        "command": COMMAND,
        "steps": [
            {"step": "ftn", "label": "Refresh FTN charting", "due": False,
             "reason": "every earlier game has its FTN charting", "week": None},
            {"step": "build", "label": "Build the play-calling tables", "due": False,
             "reason": "up to date (as of week 5)", "week": None},
            {"step": "review", "label": "Week 4's 4th-down decision review", "due": False,
             "reason": "week 4 is already reviewed", "week": 4},
            {"step": "grade", "label": "Grade week 4's forecast", "due": False,
             "reason": "week 4 is graded", "week": 4},
            {"step": "forecast", "label": "Forecast week 5", "due": True,
             "reason": "no forecast for week 5 yet", "week": 5},
        ],
    }  # fmt: skip


def test_wednesday_afternoon_wants_the_ftn_refresh_and_what_follows(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    t = U.up_to_date_tree(paths)
    t.build(waiting=(U.G_MNF,))
    publish(paths)
    c, *_ = client_for(tmp_path, now=WED)
    g = gate(c)
    assert g["allowed"] is True
    s = step_map(g)
    assert [k for k, v in s.items() if v["due"]] == ["ftn", "build", "grade", "forecast"]
    assert s["ftn"]["reason"] == "ATL at NO should have FTN by now (it lands ~2 days after a game)"
    assert s["build"]["reason"] == "after the FTN refresh"
    # Tuesday morning the same tree is not ripe: nothing to do, the button stays shut
    c2, *_ = client_for(tmp_path)
    g2 = gate(c2)
    assert g2["allowed"] is False and g2["reason"] == "Everything is up to date."
    assert "Wednesday afternoon" in step_map(g2)["ftn"]["reason"]


def test_a_finished_week_has_nothing_to_update(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    U.up_to_date_tree(paths)
    publish(paths)
    c, *_ = client_for(tmp_path)
    g = gate(c)
    assert (g["allowed"], g["reason"]) == (False, "Everything is up to date.")
    assert g["command"] == COMMAND and not any(s["due"] for s in g["steps"])


def test_a_run_in_progress_shuts_the_button(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, *_ = client_for(tmp_path)
    with L.run_lock(L.lock_path(paths.runs), "nfl weekly injury-update --auto"):
        g = gate(c)
    assert (g["allowed"], g["reason"]) == (False, "A run is in progress.")
    assert [s["step"] for s in g["steps"] if s["due"]] == ["forecast"]  # still listed
    assert gate(c)["allowed"] is True  # and open again once the lock is free


def test_an_unreadable_plan_is_a_closed_gate_not_an_error(tmp_path, monkeypatch) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    import nflengine.playcalling.update as P

    def boom(*a, **k):
        raise ValueError("a damaged file")

    monkeypatch.setattr(P, "plan_update", boom)
    c, *_ = client_for(tmp_path)
    pf = c.get("/api/preflight")
    assert pf.status_code == 200
    g = pf.json()["playcalling"]
    assert g["allowed"] is False and g["reason"] == "Couldn't read the play-calling files."
    assert (
        g["steps"] == [] and pf.json()["run"]["command"] == "nfl weekly run --auto --expect-week 5"
    )


def test_an_empty_tree_has_a_plan_too(tmp_path) -> None:
    publish(DataPaths(tmp_path))  # no tables, no schedule, no model
    c, *_ = client_for(tmp_path)
    g = gate(c)
    s = step_map(g)
    assert (
        g["allowed"] is True and s["build"]["due"] and s["build"]["reason"] == "no 2026 tables yet"
    )
    assert not s["forecast"]["due"]  # no model, no forecast


def test_the_gate_in_the_offseason(tmp_path) -> None:
    c, *_ = client_for(tmp_path, now=dt.datetime(2026, 6, 1, tzinfo=dt.UTC))
    pf = c.get("/api/preflight").json()
    assert pf["week"] is None
    assert pf["playcalling"] == {
        "allowed": False, "reason": "No week in the calendar.", "command": None, "steps": [],
    }  # fmt: skip


def test_the_gate_in_rehearsal_mode(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    write_week(paths, 2026, 4, ALL_OK, report=True)
    c, launcher, *_ = client_for(tmp_path, rehearsal=True)
    pf = c.get("/api/preflight").json()
    assert pf["mode"] == "rehearsal"
    assert pf["playcalling"] == {
        "allowed": False, "reason": "Not in rehearsal mode.", "command": None, "steps": [],
    }  # fmt: skip
    r = c.post("/api/run", json={"kind": "playcalling_update", "expect_week": 4}, headers=POST)
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_allowed"
    assert r.json()["error"]["message"] == "Not in rehearsal mode."
    assert launcher.calls == []


def test_the_gate_calls_the_plan_with_the_servers_week_and_clock(tmp_path, monkeypatch) -> None:
    import nflengine.playcalling.update as P

    seen: list[tuple] = []
    real = P.plan_update
    monkeypatch.setattr(P, "plan_update", lambda *a: seen.append(a[1:]) or real(*a))
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    c, *_ = client_for(tmp_path, now=WED)
    c.get("/api/preflight")
    assert seen == [(2026, 5, WED)]


def test_the_gate_text_is_scrubbed(tmp_path) -> None:
    """A team name from a file is text: the pre-flight cleans every label and reason."""
    paths = DataPaths(tmp_path)
    rows = [
        (w, g, "api_key=abc123 x" if g == U.G_MNF else a,
         f"{paths.root}\\secret" if g == U.G_MNF else h, k, r)
        for w, g, a, h, k, r in U.GAMES
    ]  # fmt: skip
    t = U.up_to_date_tree(paths).games(rows)
    t.build(waiting=(U.G_MNF,))
    publish(paths)
    c, *_ = client_for(tmp_path, now=WED)
    r = c.get("/api/preflight")
    assert "abc123" not in r.text and str(paths.root) not in r.text
    assert step_map(r.json()["playcalling"])["ftn"]["due"] is True


def test_the_gate_writes_nothing(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, *_ = client_for(tmp_path)

    def inventory() -> dict[str, tuple[int, int]]:
        return {
            str(p.relative_to(tmp_path)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in tmp_path.rglob("*")
            if p.is_file()
        }

    before = inventory()
    for url in ("/api/preflight", "/api/run/current", "/api/weeks", "/api/meta"):
        assert c.get(url).status_code == 200
    assert inventory() == before


def test_preflight_stays_in_the_security_scan() -> None:
    text = (Path(__file__).parent / "test_app_security.py").read_text(encoding="utf-8")
    assert '"/api/preflight"' in text and "SCANNED_GETS" in text


def test_the_gate_function_without_a_week() -> None:
    g = playcalling_gate(
        DataPaths(Path("nowhere")), None, None, TUESDAY, published=True, busy=False
    )
    assert g == {
        "allowed": False,
        "reason": "No week in the calendar.",
        "command": None,
        "steps": [],
    }


# ---- POST /api/run -------------------------------------------------------------------------------


def start(c, **body):
    return c.post("/api/run", json={"kind": "playcalling_update", "expect_week": 5, **body},
                  headers=POST)  # fmt: skip


def test_the_run_is_started_with_a_command_built_in_code(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, launcher, _, _ = client_for(tmp_path)
    r = start(c)
    assert r.status_code == 202, r.text
    body = r.json()
    assert set(body) == {"run_id", "kind", "command", "season", "week"}
    assert (body["kind"], body["season"], body["week"]) == ("playcalling_update", 2026, 5)
    assert body["command"] == COMMAND
    assert launcher.calls == [
        ["PY", "-m", "nflengine.cli", "playcalling", "update", "--auto", "--expect-week", "5",
         "--launched-by", "rishi", "--app-run", body["run_id"]],
    ]  # fmt: skip
    rec = json.loads((app_dir(paths) / "runs" / f"{body['run_id']}.json").read_text("utf-8"))
    assert rec["kind"] == rec["request_kind"] == "playcalling_update"
    assert rec["command"] == COMMAND and (rec["season"], rec["week"]) == (2026, 5)
    # its events go to the play-calling folder, never the week's Pipeline log
    assert rec["events_dir"] == str(paths.playcalling / "2026" / "events")
    assert str(tmp_path) not in r.text  # the folder stays on the server
    assert (app_dir(paths) / "logs" / f"{body['run_id']}.log").is_file()


@pytest.mark.parametrize("extra", [{"command": "nfl weekly run --force"}, {"season": 2025},
                                   {"argv": ["x"]}, {"steps": ["ftn"]}])  # fmt: skip
def test_nothing_else_the_browser_sends_reaches_the_command(tmp_path, extra) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, launcher, *_ = client_for(tmp_path)
    r = start(c, **extra)
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_request"
    assert "--force" not in r.text and launcher.calls == []


@pytest.mark.parametrize("kind", ["playcalling", "playcalling_updates", "Playcalling_update",
                                  "playcalling_update; ls", "play-calling_update"])  # fmt: skip
def test_only_the_exact_kind_is_accepted(tmp_path, kind) -> None:
    c, launcher, *_ = client_for(tmp_path)
    r = c.post("/api/run", json={"kind": kind, "expect_week": 5}, headers=POST)
    assert r.status_code == 422 and launcher.calls == []


def test_a_week_that_is_not_the_servers_is_refused(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, launcher, *_ = client_for(tmp_path)
    for week in (4, 6):
        r = start(c, expect_week=week)
        assert r.status_code == 403 and "reload the page" in r.json()["error"]["message"]
    assert launcher.calls == []


def test_the_post_needs_the_token_and_the_origin(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, launcher, *_ = client_for(tmp_path)
    body = {"kind": "playcalling_update", "expect_week": 5}
    assert c.post("/api/run", json=body).status_code == 403
    assert c.post("/api/run", json=body, headers={"origin": BASE}).status_code == 403
    evil = {"origin": "http://evil.example", "x-cr-token": TOKEN}
    assert c.post("/api/run", json=body, headers=evil).status_code == 403
    assert launcher.calls == []


def test_a_closed_gate_is_a_403_with_its_reason(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    c, launcher, *_ = client_for(tmp_path)
    r = start(c)  # the Tuesday run isn't published yet
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_allowed"
    assert r.json()["error"]["message"] == "Needs this week's Tuesday run first (Run week 5)."
    publish(paths)
    (paths.playcalling / "2026" / "week05").mkdir(parents=True, exist_ok=True)
    U.up_to_date_tree(paths)  # everything done
    r = start(c)
    assert r.status_code == 403 and r.json()["error"]["message"] == "Everything is up to date."
    assert launcher.calls == []


def test_a_held_lock_is_a_409(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, launcher, *_ = client_for(tmp_path)
    with L.run_lock(L.lock_path(paths.runs), "nfl weekly run --season 2026 --week 5"):
        r = start(c)
    assert r.status_code == 409 and r.json()["error"]["code"] == "locked"
    assert launcher.calls == []
    # another play-calling update holds it: the same
    with L.run_lock(L.lock_path(paths.runs), "nfl playcalling update --season 2026 --week 5"):
        assert start(c).status_code == 409


def test_a_second_click_while_the_first_is_running_is_a_409(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, launcher, *_ = client_for(tmp_path)
    assert start(c).status_code == 202
    r = start(c)
    assert r.status_code == 409 and r.json()["error"]["code"] == "running"
    assert len(launcher.calls) == 1
    # and the weekly run can't start over it either
    w = c.post("/api/run", json={"kind": "weekly", "expect_week": 5}, headers=POST)
    assert w.status_code == 409


def test_the_build_launch_function_directly(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    ok = {"season": 2026, "week": 5, "run": {}, "resume": {}, "injury_update": {},
          "playcalling": {"allowed": True, "reason": None, "command": COMMAND}}  # fmt: skip
    launch = build_launch("playcalling_update", preflight=ok, paths=paths, python="PY",
                          run_id="20261006T140012Z-a1b2")  # fmt: skip
    assert launch.kind == launch.request_kind == "playcalling_update"
    assert launch.command == COMMAND and (launch.season, launch.week) == (2026, 5)
    assert launch.argv[-2:] == ["--app-run", "20261006T140012Z-a1b2"]
    assert launch.events_dir == paths.playcalling / "2026" / "events"
    with pytest.raises(RunRefused, match="No play-calling update in rehearsal mode") as e:
        build_launch("playcalling_update", preflight=ok, paths=paths, python="PY", run_id="x",
                     rehearsal=True)  # fmt: skip
    assert e.value.code == "not_allowed"
    # a pre-flight without the gate (an older one), a closed gate, an unknown kind, no week
    with pytest.raises(RunRefused, match="Not allowed right now"):
        build_launch("playcalling_update", preflight={**ok, "playcalling": None}, paths=paths,
                     python="PY", run_id="x")  # fmt: skip
    closed = {**ok, "playcalling": {"allowed": False, "reason": "Everything is up to date."}}
    with pytest.raises(RunRefused, match="Everything is up to date"):
        build_launch("playcalling_update", preflight=closed, paths=paths, python="PY", run_id="x")
    with pytest.raises(RunRefused, match="playcalling_update"):
        build_launch("graph_rebuild", preflight=ok, paths=paths, python="PY", run_id="x")
    with pytest.raises(RunRefused, match="no week"):
        build_launch("playcalling_update", preflight={**ok, "week": None}, paths=paths,
                     python="PY", run_id="x")  # fmt: skip


# ---- finding the run again -----------------------------------------------------------------------


def write_events(path: Path, run_id: str, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"seq": i + 1, "t": f"2026-10-06T10:00:{i:02d}-04:00", "run_id": run_id, **e})
        for i, e in enumerate(events)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_story(end: dict[str, Any] | None) -> list[dict[str, Any]]:
    evs: list[dict[str, Any]] = [
        {"type": "run_start", "kind": "playcalling_update",
         "command": "nfl playcalling update --auto", "season": 2026, "week": 5,
         "steps": ["forecast"], "from_step": None, "earlier": {}, "launched_by": "rishi",
         "via": "control-room", "plan": ["forecast: no forecast for week 5 yet"]},
        {"type": "step_start", "step": "forecast"},
        {"type": "step_end", "step": "forecast", "status": "ok", "seconds": 3.0,
         "detail": "4 games, 24 forecasts"},
    ]  # fmt: skip
    if end is not None:
        evs.append({"type": "run_end", **end})
    return evs


OK_END = {"status": "ok", "exit_code": 0, "message": "done: forecast", "published": False,
          "failed_step": None, "material": None}  # fmt: skip


def test_current_follows_the_app_run_through_its_own_events_folder(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, launcher, *_ = client_for(tmp_path)
    assert c.get("/api/run/current").json() == {"state": "idle", "run": None}
    rid = start(c).json()["run_id"]
    cur = c.get("/api/run/current").json()
    assert cur["state"] == "running"
    run = cur["run"]
    assert (run["run_id"], run["kind"], run["launched_from"]) == (rid, "playcalling_update", "app")
    assert (run["command"], run["season"], run["week"]) == (COMMAND, 2026, 5)
    assert run["has_events"] is False and "_events" not in run
    # a file in the week's Pipeline folder is not this run's events
    write_events(paths.run_dir(2026, 5) / "events" / f"{rid}.jsonl", rid, update_story(OK_END))
    assert c.get("/api/run/current").json()["run"]["has_events"] is False
    # the run writes to the play-calling folder and ends
    write_events(paths.playcalling / "2026" / "events" / f"{rid}.jsonl", rid, update_story(OK_END))
    launcher.procs[0].code = 0
    cur = c.get("/api/run/current").json()
    assert cur["state"] == "finished"
    run = cur["run"]
    assert (run["status"], run["exit_code"], run["message"]) == ("ok", 0, "done: forecast")
    assert run["has_events"] is True and run["published"] is False and run["output_tail"] == []


def test_a_failed_update_reads_as_failed(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, launcher, *_ = client_for(tmp_path)
    rid = start(c).json()["run_id"]
    end = {**OK_END, "status": "failed", "exit_code": 1, "message": "done: ftn; failed: build"}
    write_events(paths.playcalling / "2026" / "events" / f"{rid}.jsonl", rid, update_story(end))
    launcher.procs[0].code = 1
    run = c.get("/api/run/current").json()["run"]
    assert (run["status"], run["exit_code"], run["message"]) == (
        "failed", 1, "done: ftn; failed: build",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("code", "status"),
    [(0, "idle"), (6, "week_mismatch"), (4, "locked"), (5, "no_data_root"), (1, "failed")],
)
def test_an_update_that_wrote_no_events_reports_its_exit_code(tmp_path, code, status) -> None:
    """`nothing is due` (exit 0) takes no lock and writes nothing: the page reads it as idle."""
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, launcher, *_ = client_for(tmp_path)
    rid = start(c).json()["run_id"]
    log = app_dir(paths) / "logs" / f"{rid}.log"
    log.write_text(f"nothing is due\nlog at {tmp_path}\\x\n", encoding="utf-8")
    launcher.procs[0].code = code
    run = c.get("/api/run/current").json()["run"]
    assert (run["status"], run["exit_code"], run["has_events"]) == (status, code, False)
    assert any("nothing is due" in x for x in run["output_tail"])
    assert all(str(tmp_path) not in x for x in run["output_tail"])


def test_after_an_app_restart_a_dead_update_reads_as_interrupted(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, *_ = client_for(tmp_path)
    rid = start(c).json()["run_id"]
    write_events(paths.playcalling / "2026" / "events" / f"{rid}.jsonl", rid, update_story(None))
    fresh, *_ = client_for(tmp_path)  # a new app: no child handle, no lock held
    run = fresh.get("/api/run/current").json()["run"]
    assert (run["run_id"], run["kind"], run["status"]) == (rid, "playcalling_update", "interrupted")


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


def test_the_stream_replays_the_update_from_its_own_folder(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    only_forecast_due(paths)
    publish(paths)
    c, launcher, *_ = client_for(tmp_path)
    rid = start(c).json()["run_id"]
    write_events(paths.playcalling / "2026" / "events" / f"{rid}.jsonl", rid, update_story(OK_END))
    launcher.procs[0].code = 0
    text = c.get("/api/run/stream").text
    msgs = parse_sse(text)
    runs = [m for m in msgs if m.get("event") == "run"]
    assert [m["data"]["type"] for m in runs] == ["run_start", "step_start", "step_end", "run_end"]
    assert runs[0]["data"]["kind"] == "playcalling_update"
    end = msgs[-1]
    assert end["event"] == "end" and end["data"]["status"] == "ok"
    assert end["data"]["kind"] == "playcalling_update" and "_events" not in end["data"]
    assert str(tmp_path) not in text


# a terminal `nfl playcalling update` holds the same lock


def test_a_terminal_update_holding_the_lock_is_found(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    c, *_ = client_for(tmp_path)
    rid = "20261006T140000Z"
    write_events(paths.playcalling / "2026" / "events" / f"{rid}.jsonl", rid, update_story(None))
    with L.run_lock(L.lock_path(paths.runs), "nfl playcalling update --season 2026 --week 5",
                    run_id=rid):  # fmt: skip
        cur = c.get("/api/run/current").json()
    assert cur["state"] == "running"
    run = cur["run"]
    assert (run["launched_from"], run["kind"], run["season"], run["week"]) == (
        "terminal", "playcalling_update", 2026, 5,
    )  # fmt: skip
    assert run["run_id"] == rid and run["has_events"] is True
    assert run["command"] == "nfl playcalling update --season 2026 --week 5"


def test_a_terminal_update_looks_in_the_playcalling_folder_only(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    c, *_ = client_for(tmp_path)
    rid = "20261006T140000Z"
    # the week's Pipeline folder has a file of that name: not this run's
    write_events(paths.run_dir(2026, 5) / "events" / f"{rid}.jsonl", rid, update_story(None))
    with L.run_lock(L.lock_path(paths.runs), "nfl playcalling update --season 2026 --week 5",
                    run_id=rid):  # fmt: skip
        run = c.get("/api/run/current").json()["run"]
    assert run["kind"] == "playcalling_update" and run["has_events"] is False


def test_an_auto_update_takes_the_calendars_week(tmp_path) -> None:
    c, *_ = client_for(tmp_path)
    with L.run_lock(L.lock_path(paths_of(tmp_path).runs), "nfl playcalling update --auto"):
        run = c.get("/api/run/current").json()["run"]
    assert (run["kind"], run["season"], run["week"]) == ("playcalling_update", 2026, 5)


def paths_of(tmp_path) -> DataPaths:
    return DataPaths(tmp_path)


def test_an_older_lock_note_without_a_run_id_finds_the_newest_events(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    c, *_ = client_for(tmp_path)
    write_events(paths.playcalling / "2026" / "events" / "20261006T140000Z.jsonl",
                 "20261006T140000Z", update_story(None))  # fmt: skip
    with L.run_lock(L.lock_path(paths.runs), "nfl playcalling update --season 2026 --week 5"):
        # (no run id in the note: written before ids existed)
        note = L.read_holder(L.lock_path(paths.runs)) or {}
        assert "run_id" not in note
        run = c.get("/api/run/current").json()["run"]
    assert run["kind"] == "playcalling_update" and run["has_events"] is True


# ---- a play-calling lock is not "the week's run" -------------------------------------------------


def entry(c, week: int = 5) -> dict[str, Any]:
    return next(w for w in c.get("/api/weeks").json()["weeks"] if w["week"] == week)


@pytest.mark.parametrize(
    "command",
    ["nfl playcalling update --season 2026 --week 5", "nfl playcalling update --auto",
     "nfl playcalling update --auto --launched-by agent"],
)  # fmt: skip
def test_the_sidebar_the_header_and_the_pipeline_do_not_show_a_running_week(
    tmp_path, command
) -> None:
    paths = DataPaths(tmp_path)
    write_week(paths, 2026, 5, {"ingest": "ok", "ready": "ok", "curate": "ok"})  # a partial run
    c, *_ = client_for(tmp_path)
    free = (entry(c)["status"], c.get("/api/weeks/2026/5/pipeline").json()["state"])
    with L.run_lock(L.lock_path(paths.runs), command):
        assert entry(c)["status"] != "running"
        assert c.get("/api/weeks/2026/5").json()["entry"]["status"] != "running"
        assert c.get("/api/weeks/2026/5/pipeline").json()["state"] != "running"
        assert (entry(c)["status"], c.get("/api/weeks/2026/5/pipeline").json()["state"]) == free
        meta = c.get("/api/meta").json()
        assert meta["lock"]["held"] is True and meta["lock"]["command"] == command


def test_a_weekly_run_does_show_the_week_as_running(tmp_path) -> None:
    """The control for the test above: the same tree under the weekly run's lock."""
    paths = DataPaths(tmp_path)
    write_week(paths, 2026, 5, {"ingest": "ok", "ready": "ok", "curate": "ok"})
    c, *_ = client_for(tmp_path)
    with L.run_lock(L.lock_path(paths.runs), "nfl weekly run --auto --expect-week 5"):
        assert entry(c)["status"] == "running"
        assert c.get("/api/weeks/2026/5/pipeline").json()["state"] == "running"


def test_a_playcalling_lock_still_blocks_the_weekly_run_and_says_why(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    c, *_ = client_for(tmp_path)
    cmd = "nfl playcalling update --season 2026 --week 5"
    with L.run_lock(L.lock_path(paths.runs), cmd):
        pf = c.get("/api/preflight").json()
        lock = next(x for x in pf["checks"] if x["id"] == "lock")
        r = c.post("/api/run", json={"kind": "weekly", "expect_week": 5}, headers=POST)
    assert pf["run"]["allowed"] is False and lock["status"] == "fail"
    assert cmd in lock["reason"]  # the page names what holds the lock
    assert r.status_code == 409 and r.json()["error"]["code"] == "locked"


def test_lock_target_and_is_playcalling_update() -> None:
    plan = plan_week(final_through_week4(), TUESDAY)
    assert (plan.season, plan.week) == (2026, 5)

    def lock(command: str | None, held: bool = True) -> dict[str, Any]:
        return {"held": held, "command": command, "started": "2026-10-06T10:00:00-04:00"}

    for cmd in ("nfl playcalling update --season 2026 --week 5", "nfl playcalling update --auto",
                "nfl playcalling update --auto --expect-week 5"):  # fmt: skip
        assert is_playcalling_update(lock(cmd)) is True
        assert lock_target(lock(cmd), plan) is None  # it names a week, but runs none of its steps
    assert is_playcalling_update(lock("nfl playcalling update --auto", held=False)) is False
    assert is_playcalling_update({"held": True, "command": None}) is False
    assert is_playcalling_update(lock("nfl playcalling build --season 2026")) is False
    # the other commands are unchanged
    assert lock_target(lock("nfl weekly run --season 2026 --week 4"), plan) == (2026, 4)
    assert lock_target(lock("nfl weekly run --auto"), plan) == (2026, 5)
    assert lock_target(lock("nfl weekly injury-update --auto"), plan) == (2026, 5)
    assert lock_target(lock("nfl weekly run --auto", held=False), plan) is None
