"""The control room and the Forecast lab update (FL02, D158): the pre-flight gate, `POST /api/run`
with `forecastlab_update`, re-attaching to the run (its own events folder, a terminal run's lock),
the rule that a Forecast lab lock is not "the week's run", and **the follow-up**: after Run week N,
a resume or the injury update ends well, the app starts the update itself.

The runner never starts a process (a fake launcher records the argv); the plan is the real
`plan_update` on a tmp tree (`tests/forecastlab/fl_update_fixtures.py`, shared with the planner's
own tests) at the same pinned Tuesday as the other run-control tests. The follow-up runs on a
daemon thread: the tests give it a tiny poll interval and join it through the thread the route
got back, never a sleep.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from app_helpers import ALL_OK, BASE, TOKEN, TUESDAY, final_through_week4, make_client, write_week

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "forecastlab"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "playcalling"))
import fl_update_fixtures as F  # noqa: E402
import playcall_update_fixtures as PU  # noqa: E402

import nflengine.app.server as server_mod  # noqa: E402
import nflengine.forecastlab.update as FU  # noqa: E402
from nflengine.app.preflight import forecastlab_gate  # noqa: E402
from nflengine.app.readers.meta import is_forecastlab_update, lock_target  # noqa: E402
from nflengine.app.runner import (  # noqa: E402
    CHAIN_AFTER,
    KINDS,
    Runner,
    RunRefused,
    app_dir,
    build_launch,
)
from nflengine.forecastlab import schema as S  # noqa: E402
from nflengine.ops import lock as L  # noqa: E402
from nflengine.ops.calendar import plan_week  # noqa: E402
from nflengine.paths import DataPaths  # noqa: E402

POST = {"origin": BASE, "x-cr-token": TOKEN}
SATURDAY = dt.datetime(2026, 10, 10, 16, 0, tzinfo=dt.UTC)  # Sat 2026-10-10 12:00 ET
COMMAND = "nfl forecastlab update --auto --expect-week 5"
STEPS = ["availability", "curves", "grade"]
NAME = "rishi-pc.tail1234.ts.net"
WEEKLY_ID = "20261006T140012Z-a1b2"


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


def publish(paths: DataPaths) -> None:
    """The Tuesday run is out. The digest's time is the fixture's (before the Tuesday view was
    made): a digest newer than the view would mean the week was run again."""
    write_week(paths, 2026, 5, ALL_OK, report=True)
    F.touch(paths.report_path(2026, 5), F.PUBLISHED)


def gate(c) -> dict[str, Any]:
    return c.get("/api/preflight").json()["forecastlab"]


def step_map(g: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["step"]: s for s in g["steps"]}


def events_folder(paths: DataPaths) -> Path:
    return paths.root / "forecastlab" / "2026" / "events"


# ---- the pre-flight gate ---------------------------------------------------------------------


def test_the_gate_waits_for_the_tuesday_run(tmp_path) -> None:
    F.only_curves_due(DataPaths(tmp_path))
    c, *_ = client_for(tmp_path)
    g = gate(c)
    assert g["allowed"] is False
    assert g["reason"] == "Needs this week's Tuesday run first (Run week 5)."
    assert g["command"] == COMMAND
    assert [s["step"] for s in g["steps"]] == STEPS  # the plan is shown even while blocked
    assert [s["step"] for s in g["steps"] if s["due"]] == ["curves"]


def test_the_gate_opens_when_a_step_is_due(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    F.only_curves_due(paths)
    publish(paths)
    c, *_ = client_for(tmp_path)
    assert gate(c) == {
        "allowed": True,
        "reason": None,
        "command": COMMAND,
        "steps": [
            {"step": "availability", "label": "Availability for week 5", "due": False,
             "reason": "up to date (Tuesday view)", "week": 5},
            {"step": "curves", "label": "Outcome curves for week 5", "due": True,
             "reason": "no curves for week 5 yet", "week": 5},
            {"step": "grade", "label": "Grade week 4", "due": False,
             "reason": "week 4 is graded", "week": 4},
        ],
    }  # fmt: skip


def test_a_saturday_view_after_the_injury_update_opens_the_gate(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    F.up_to_date(paths).injury_update(5, SATURDAY - dt.timedelta(hours=1))
    publish(paths)
    c, *_ = client_for(tmp_path, now=SATURDAY)
    g = gate(c)
    assert g["allowed"] is True
    a = step_map(g)["availability"]
    assert a["due"] is True and a["label"] == "Availability for week 5 (Saturday view)"
    assert a["reason"].startswith("the injury update has run: the Saturday view isn't made yet")
    assert "some games have started" in a["reason"]  # Thursday night is over by Saturday noon


def test_a_finished_week_has_nothing_to_update(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    F.up_to_date(paths)
    publish(paths)
    c, *_ = client_for(tmp_path)
    g = gate(c)
    assert (g["allowed"], g["reason"]) == (False, "Everything is up to date.")
    assert g["command"] == COMMAND and not any(s["due"] for s in g["steps"])


def test_a_run_in_progress_shuts_the_button(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    F.only_curves_due(paths)
    publish(paths)
    c, *_ = client_for(tmp_path)
    with L.run_lock(L.lock_path(paths.runs), "nfl weekly injury-update --auto"):
        g = gate(c)
    assert (g["allowed"], g["reason"]) == (False, "A run is in progress.")
    assert [s["step"] for s in g["steps"] if s["due"]] == ["curves"]  # still listed
    assert gate(c)["allowed"] is True  # and open again once the lock is free


def test_an_unpublished_week_beats_a_busy_lock_in_the_reason(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    F.only_curves_due(paths)
    c, *_ = client_for(tmp_path)
    with L.run_lock(L.lock_path(paths.runs), "nfl weekly run --auto --expect-week 5"):
        g = gate(c)
    assert g["reason"] == "Needs this week's Tuesday run first (Run week 5)."


def test_an_unreadable_plan_is_a_closed_gate_not_an_error(tmp_path, monkeypatch) -> None:
    paths = DataPaths(tmp_path)
    F.only_curves_due(paths)
    publish(paths)

    def boom(*a, **k):
        raise ValueError("a damaged file")

    monkeypatch.setattr(FU, "plan_update", boom)
    c, *_ = client_for(tmp_path)
    pf = c.get("/api/preflight")
    assert pf.status_code == 200
    g = pf.json()["forecastlab"]
    assert g["allowed"] is False and g["reason"] == "Couldn't read the Forecast lab files."
    assert g["steps"] == [] and g["command"] == COMMAND
    assert pf.json()["run"]["command"] == "nfl weekly run --auto --expect-week 5"


def test_an_empty_tree_has_a_plan_and_says_the_models_are_not_promoted(tmp_path) -> None:
    publish(DataPaths(tmp_path))  # no model, no games, no files
    c, *_ = client_for(tmp_path)
    g = gate(c)
    assert (g["allowed"], g["reason"]) == (False, "Nothing to do until the models are promoted.")
    assert [s["step"] for s in g["steps"]] == STEPS
    assert step_map(g)["availability"]["reason"] == "no promoted availability model yet"
    assert not any(s["due"] for s in g["steps"])


def test_one_missing_model_with_nothing_due_still_says_so(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    F.up_to_date(paths)
    S.production_file(paths, "curves").unlink()  # the curves model isn't promoted
    publish(paths)
    c, *_ = client_for(tmp_path)
    g = gate(c)
    assert (g["allowed"], g["reason"]) == (False, "Nothing to do until the models are promoted.")
    assert step_map(g)["curves"]["reason"].startswith("no promoted curves model yet")


def test_a_missing_model_does_not_close_the_gate_while_another_step_is_due(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    F.only_curves_due(paths)
    S.production_file(paths, "availability").unlink()
    publish(paths)
    c, *_ = client_for(tmp_path)
    g = gate(c)
    assert (g["allowed"], g["reason"]) == (True, None)
    assert [s["step"] for s in g["steps"] if s["due"]] == ["curves"]


def test_the_gate_in_the_offseason(tmp_path) -> None:
    c, *_ = client_for(tmp_path, now=dt.datetime(2026, 6, 1, tzinfo=dt.UTC))
    pf = c.get("/api/preflight").json()
    assert pf["week"] is None
    assert pf["forecastlab"] == {
        "allowed": False, "reason": "No week in the calendar.", "command": None, "steps": [],
    }  # fmt: skip


def test_the_gate_in_rehearsal_mode(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    F.only_curves_due(paths)
    write_week(paths, 2026, 4, ALL_OK, report=True)
    c, launcher, *_ = client_for(tmp_path, rehearsal=True)
    pf = c.get("/api/preflight").json()
    assert pf["mode"] == "rehearsal"
    assert pf["forecastlab"] == {
        "allowed": False, "reason": "Not in rehearsal mode.", "command": None, "steps": [],
    }  # fmt: skip
    r = c.post("/api/run", json={"kind": "forecastlab_update", "expect_week": 4}, headers=POST)
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_allowed"
    assert r.json()["error"]["message"] == "Not in rehearsal mode."
    assert launcher.calls == []


def test_forecastlab_is_a_key_of_the_preflight_in_both_modes_shaped_like_playcalling(
    tmp_path,
) -> None:
    paths = DataPaths(tmp_path / "live")
    F.only_curves_due(paths)
    publish(paths)
    live, *_ = client_for(tmp_path / "live")
    rehearse_paths = DataPaths(tmp_path / "rehearse")
    write_week(rehearse_paths, 2026, 4, ALL_OK, report=True)
    rehearsal, *_ = client_for(tmp_path / "rehearse", rehearsal=True)
    for c, mode in ((live, "live"), (rehearsal, "rehearsal")):
        pf = c.get("/api/preflight").json()
        assert pf["mode"] == mode
        assert "forecastlab" in pf and "playcalling" in pf
        assert set(pf["forecastlab"]) == set(pf["playcalling"]) == {
            "allowed", "reason", "command", "steps",
        }  # fmt: skip
        assert isinstance(pf["forecastlab"]["steps"], list)
        assert isinstance(pf["forecastlab"]["allowed"], bool)
    steps = live.get("/api/preflight").json()["forecastlab"]["steps"]
    assert all(set(s) == {"step", "label", "due", "reason", "week"} for s in steps)


def test_the_gate_calls_the_plan_with_the_servers_week_and_clock(tmp_path, monkeypatch) -> None:
    seen: list[tuple] = []
    real = FU.plan_update
    monkeypatch.setattr(FU, "plan_update", lambda *a: seen.append(a[1:]) or real(*a))
    F.only_curves_due(DataPaths(tmp_path))
    c, *_ = client_for(tmp_path, now=SATURDAY)
    c.get("/api/preflight")
    assert seen == [(2026, 5, SATURDAY)]


def test_the_gate_text_is_scrubbed(tmp_path, monkeypatch) -> None:
    """A label or reason is text: the pre-flight cleans each one."""
    paths = DataPaths(tmp_path)
    publish(paths)

    def planted(p, season, week, now):
        return [
            FU.Step("availability", f"Availability {paths.root}\\secret", True,
                    "api_key=abc123 x " + "z" * 400, week),
            FU.Step("curves", "c" * 300, False, "fine", week),
        ]  # fmt: skip

    monkeypatch.setattr(FU, "plan_update", planted)
    c, *_ = client_for(tmp_path)
    r = c.get("/api/preflight")
    assert "abc123" not in r.text and str(paths.root) not in r.text
    s = step_map(r.json()["forecastlab"])
    assert s["availability"]["due"] is True
    assert len(s["availability"]["reason"]) <= 240 and len(s["curves"]["label"]) <= 120


def test_the_gate_writes_nothing(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    F.only_curves_due(paths)
    publish(paths)
    c, *_ = client_for(tmp_path)
    before = F.tree_state(tmp_path)
    for url in ("/api/preflight", "/api/run/current", "/api/weeks", "/api/meta"):
        assert c.get(url).status_code == 200
    assert F.tree_state(tmp_path) == before


def test_preflight_stays_in_the_security_scan() -> None:
    text = (Path(__file__).parent / "test_app_security.py").read_text(encoding="utf-8")
    assert '"/api/preflight"' in text and "SCANNED_GETS" in text


def test_the_gate_function_without_a_week() -> None:
    g = forecastlab_gate(
        DataPaths(Path("nowhere")), None, None, TUESDAY, published=True, busy=False
    )
    assert g == {
        "allowed": False,
        "reason": "No week in the calendar.",
        "command": None,
        "steps": [],
    }


def test_the_gate_function_orders_its_reasons(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    F.only_curves_due(paths)

    def reason(**kw: bool) -> str | None:
        return forecastlab_gate(paths, 2026, 5, TUESDAY, **kw)["reason"]

    assert reason(published=False, busy=True) == "Needs this week's Tuesday run first (Run week 5)."
    assert reason(published=True, busy=True) == "A run is in progress."
    assert reason(published=True, busy=False) is None


# ---- the chain flag in the pre-flight --------------------------------------------------------


def test_the_preflight_says_whether_the_app_chains_the_update(tmp_path) -> None:
    on, *_ = client_for(tmp_path / "a", chain=True)
    off, *_ = client_for(tmp_path / "b")
    assert on.get("/api/preflight").json()["chain"] is True
    assert off.get("/api/preflight").json()["chain"] is False  # the default


def test_the_chain_flag_is_off_in_rehearsal_mode_and_on_without_a_week(tmp_path) -> None:
    write_week(DataPaths(tmp_path / "r"), 2026, 4, ALL_OK, report=True)
    r, *_ = client_for(tmp_path / "r", rehearsal=True, chain=True)
    pf = r.get("/api/preflight").json()
    assert pf["mode"] == "rehearsal" and pf["chain"] is False
    off_season = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    o, *_ = client_for(tmp_path / "o", now=off_season, chain=True)
    pf = o.get("/api/preflight").json()
    assert pf["week"] is None and pf["chain"] is True


# ---- POST /api/run ---------------------------------------------------------------------------


def start(c, **body):
    return c.post(
        "/api/run", json={"kind": "forecastlab_update", "expect_week": 5, **body}, headers=POST
    )


def ready(tmp_path, **kw):
    """A published Tuesday where only week 5's curves are due, and a client for it."""
    paths = DataPaths(tmp_path)
    F.only_curves_due(paths)
    publish(paths)
    return client_for(tmp_path, **kw)


def test_the_run_is_started_with_a_command_built_in_code(tmp_path) -> None:
    c, launcher, _, paths = ready(tmp_path)
    r = start(c)
    assert r.status_code == 202, r.text
    body = r.json()
    assert set(body) == {"run_id", "kind", "command", "season", "week"}  # no `follows` here
    assert (body["kind"], body["season"], body["week"]) == ("forecastlab_update", 2026, 5)
    assert body["command"] == COMMAND
    assert launcher.calls == [
        ["PY", "-m", "nflengine.cli", "forecastlab", "update", "--auto", "--expect-week", "5",
         "--launched-by", "rishi", "--app-run", body["run_id"]],
    ]  # fmt: skip
    rec = json.loads((app_dir(paths) / "runs" / f"{body['run_id']}.json").read_text("utf-8"))
    assert rec["kind"] == rec["request_kind"] == "forecastlab_update"
    assert rec["command"] == COMMAND and (rec["season"], rec["week"]) == (2026, 5)
    # its events go to the Forecast lab's folder, never the week's Pipeline log
    assert rec["events_dir"] == str(events_folder(paths))
    assert rec["follows"] is None  # a press follows nothing
    assert str(tmp_path) not in r.text  # the folder stays on the server
    assert (app_dir(paths) / "logs" / f"{body['run_id']}.log").is_file()


@pytest.mark.parametrize(
    "extra",
    [{"command": "nfl weekly run --force"}, {"season": 2025}, {"argv": ["x"]},
     {"steps": ["curves"]}, {"follows": WEEKLY_ID}, {"follows": None}, {"views": ["saturday"]}],
)  # fmt: skip
def test_nothing_else_the_browser_sends_reaches_the_command(tmp_path, extra) -> None:
    c, launcher, *_ = ready(tmp_path)
    r = start(c, **extra)
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_request"
    assert "--force" not in r.text and launcher.calls == []


def test_a_browser_cannot_say_a_run_follows_another(tmp_path) -> None:
    """`follows` is the follow-up's own: no kind accepts it in a body."""
    c, launcher, *_ = ready(tmp_path)
    for kind in KINDS:
        body = {"kind": kind, "expect_week": 5, "follows": WEEKLY_ID}
        r = c.post("/api/run", json=body, headers=POST)
        assert r.status_code == 422, kind
    assert launcher.calls == []


@pytest.mark.parametrize(
    "kind",
    ["forecastlab", "forecastlab_updates", "Forecastlab_update", "forecastlab_update; ls",
     "forecast-lab_update", "forecast_lab_update", "forecastlab_update ", " forecastlab_update"],
)  # fmt: skip
def test_only_the_exact_kind_is_accepted(tmp_path, kind) -> None:
    c, launcher, *_ = ready(tmp_path)
    r = c.post("/api/run", json={"kind": kind, "expect_week": 5}, headers=POST)
    assert r.status_code == 422 and launcher.calls == []


def test_a_trailing_newline_does_not_pass_the_kind_pattern(tmp_path) -> None:
    c, launcher, *_ = ready(tmp_path)
    r = c.post("/api/run", json={"kind": "forecastlab_update\n", "expect_week": 5}, headers=POST)
    assert r.status_code in (403, 422) and launcher.calls == []


def test_a_week_that_is_not_the_servers_is_refused(tmp_path) -> None:
    c, launcher, *_ = ready(tmp_path)
    for week in (4, 6):
        r = start(c, expect_week=week)
        assert r.status_code == 403 and "reload the page" in r.json()["error"]["message"]
    assert launcher.calls == []


def test_the_post_needs_the_token_and_the_origin(tmp_path) -> None:
    c, launcher, *_ = ready(tmp_path)
    body = {"kind": "forecastlab_update", "expect_week": 5}
    assert c.post("/api/run", json=body).status_code == 403
    assert c.post("/api/run", json=body, headers={"origin": BASE}).status_code == 403
    evil = {"origin": "http://evil.example", "x-cr-token": TOKEN}
    assert c.post("/api/run", json=body, headers=evil).status_code == 403
    assert launcher.calls == []


def test_a_closed_gate_is_a_403_with_its_reason(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    F.only_curves_due(paths)
    c, launcher, *_ = client_for(tmp_path)
    r = start(c)  # the Tuesday run isn't published yet
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_allowed"
    assert r.json()["error"]["message"] == "Needs this week's Tuesday run first (Run week 5)."
    publish(paths)
    F.up_to_date(paths)  # everything done
    r = start(c)
    assert r.status_code == 403 and r.json()["error"]["message"] == "Everything is up to date."
    assert launcher.calls == []


def test_a_held_lock_is_a_409(tmp_path) -> None:
    c, launcher, _, paths = ready(tmp_path)
    for command in (
        "nfl weekly run --season 2026 --week 5",
        "nfl playcalling update --season 2026 --week 5",  # another update holds it: the same
        "nfl forecastlab update --season 2026 --week 5",
    ):
        with L.run_lock(L.lock_path(paths.runs), command):
            r = start(c)
        assert r.status_code == 409 and r.json()["error"]["code"] == "locked", command
    assert launcher.calls == []


def test_a_second_click_while_the_first_is_running_is_a_409(tmp_path) -> None:
    c, launcher, *_ = ready(tmp_path)
    assert start(c).status_code == 202
    r = start(c)
    assert r.status_code == 409 and r.json()["error"]["code"] == "running"
    assert len(launcher.calls) == 1
    # and the weekly run can't start over it either
    w = c.post("/api/run", json={"kind": "weekly", "expect_week": 5}, headers=POST)
    assert w.status_code == 409


def test_a_refused_press_never_starts_a_follow_up(tmp_path) -> None:
    h = chained(tmp_path)
    for body in (
        {"kind": "weekly", "expect_week": 4},  # the wrong week
        {"kind": "weekly", "expect_week": 5, "follows": WEEKLY_ID},  # a field it must not have
        {"kind": "resume", "expect_week": 5},  # nothing failed
    ):
        assert h.c.post("/api/run", json=body, headers=POST).status_code in (403, 422)
    assert h.threads == [] and h.launcher.calls == []


def test_the_kinds_and_the_chain_set() -> None:
    assert KINDS == (
        "weekly",
        "resume",
        "injury_update",
        "playcalling_update",
        "forecastlab_update",
    )
    assert CHAIN_AFTER == ("weekly", "resume", "injury_update")
    assert set(CHAIN_AFTER) <= set(KINDS)  # (the updates never chain: no loop)


def test_the_build_launch_function_directly(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    ok = {"season": 2026, "week": 5, "run": {}, "resume": {}, "injury_update": {},
          "forecastlab": {"allowed": True, "reason": None, "command": COMMAND}}  # fmt: skip
    launch = build_launch("forecastlab_update", preflight=ok, paths=paths, python="PY",
                          run_id="20261006T140012Z-a1b2")  # fmt: skip
    assert launch.kind == launch.request_kind == "forecastlab_update"
    assert launch.command == COMMAND and (launch.season, launch.week) == (2026, 5)
    assert launch.argv[-2:] == ["--app-run", "20261006T140012Z-a1b2"]
    assert launch.argv[:5] == ["PY", "-m", "nflengine.cli", "forecastlab", "update"]
    assert launch.events_dir == S.season_dir(paths, 2026) / "events"
    with pytest.raises(RunRefused, match="No Forecast lab update in rehearsal mode") as e:
        build_launch("forecastlab_update", preflight=ok, paths=paths, python="PY", run_id="x",
                     rehearsal=True)  # fmt: skip
    assert e.value.code == "not_allowed"
    # a pre-flight without the gate (an older one), a closed gate, an unknown kind, no week
    with pytest.raises(RunRefused, match="Not allowed right now"):
        build_launch("forecastlab_update", preflight={**ok, "forecastlab": None}, paths=paths,
                     python="PY", run_id="x")  # fmt: skip
    closed = {**ok, "forecastlab": {"allowed": False, "reason": "Everything is up to date."}}
    with pytest.raises(RunRefused, match="Everything is up to date"):
        build_launch("forecastlab_update", preflight=closed, paths=paths, python="PY", run_id="x")
    with pytest.raises(RunRefused, match="forecastlab_update"):
        build_launch("graph_rebuild", preflight=ok, paths=paths, python="PY", run_id="x")
    with pytest.raises(RunRefused, match="no week"):
        build_launch("forecastlab_update", preflight={**ok, "week": None}, paths=paths,
                     python="PY", run_id="x")  # fmt: skip


def test_the_runner_reports_a_launched_childs_exit_code(tmp_path) -> None:
    c, launcher, runner, _ = ready(tmp_path)
    rid = start(c).json()["run_id"]
    assert runner.exit_code(rid) is None  # still running
    launcher.procs[0].code = 3
    assert runner.exit_code(rid) == 3
    launcher.procs[0].code = 0
    assert runner.exit_code(rid) == 0
    assert runner.exit_code("20990101T000000Z-aaaa") == -1  # not the run the app holds
    assert Runner(launcher=FakeLauncher()).exit_code(rid) == -1  # a fresh app holds none


# ---- finding the run again -------------------------------------------------------------------


def write_events(path: Path, run_id: str, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"seq": i + 1, "t": f"2026-10-06T10:00:{i:02d}-04:00", "run_id": run_id, **e})
        for i, e in enumerate(events)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_story(end: dict[str, Any] | None) -> list[dict[str, Any]]:
    evs: list[dict[str, Any]] = [
        {"type": "run_start", "kind": "forecastlab_update",
         "command": "nfl forecastlab update --auto", "season": 2026, "week": 5,
         "steps": ["curves"], "from_step": None, "earlier": {}, "launched_by": "rishi",
         "via": "control-room", "plan": ["curves: no curves for week 5 yet"]},
        {"type": "step_start", "step": "curves"},
        {"type": "step_end", "step": "curves", "status": "ok", "seconds": 3.0,
         "detail": "24 curves"},
    ]  # fmt: skip
    if end is not None:
        evs.append({"type": "run_end", **end})
    return evs


OK_END = {"status": "ok", "exit_code": 0, "message": "done: curves", "published": False,
          "failed_step": None, "material": None}  # fmt: skip


def test_current_follows_the_app_run_through_its_own_events_folder(tmp_path) -> None:
    c, launcher, _, paths = ready(tmp_path)
    assert c.get("/api/run/current").json() == {"state": "idle", "run": None}
    rid = start(c).json()["run_id"]
    cur = c.get("/api/run/current").json()
    assert cur["state"] == "running"
    run = cur["run"]
    assert (run["run_id"], run["kind"], run["launched_from"]) == (rid, "forecastlab_update", "app")
    assert (run["command"], run["season"], run["week"]) == (COMMAND, 2026, 5)
    assert run["has_events"] is False and "_events" not in run
    assert run["follows"] is None  # a press
    # a file in the week's Pipeline folder or in the play-calling folder is not this run's events
    write_events(paths.run_dir(2026, 5) / "events" / f"{rid}.jsonl", rid, update_story(OK_END))
    write_events(paths.playcalling / "2026" / "events" / f"{rid}.jsonl", rid, update_story(OK_END))
    assert c.get("/api/run/current").json()["run"]["has_events"] is False
    # the run writes to the Forecast lab's folder and ends
    write_events(events_folder(paths) / f"{rid}.jsonl", rid, update_story(OK_END))
    launcher.procs[0].code = 0
    cur = c.get("/api/run/current").json()
    assert cur["state"] == "finished"
    run = cur["run"]
    assert (run["status"], run["exit_code"], run["message"]) == ("ok", 0, "done: curves")
    assert run["has_events"] is True and run["published"] is False and run["output_tail"] == []


def test_a_failed_update_reads_as_failed(tmp_path) -> None:
    c, launcher, _, paths = ready(tmp_path)
    rid = start(c).json()["run_id"]
    end = {**OK_END, "status": "failed", "exit_code": 1, "message": "done: grade; failed: curves"}
    write_events(events_folder(paths) / f"{rid}.jsonl", rid, update_story(end))
    launcher.procs[0].code = 1
    run = c.get("/api/run/current").json()["run"]
    assert (run["status"], run["exit_code"], run["message"]) == (
        "failed", 1, "done: grade; failed: curves",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("code", "status"),
    [(0, "idle"), (6, "week_mismatch"), (4, "locked"), (5, "no_data_root"), (1, "failed")],
)
def test_an_update_that_wrote_no_events_reports_its_exit_code(tmp_path, code, status) -> None:
    """`nothing is due` (exit 0) takes no lock and writes nothing: the page reads it as idle."""
    c, launcher, _, paths = ready(tmp_path)
    rid = start(c).json()["run_id"]
    log = app_dir(paths) / "logs" / f"{rid}.log"
    log.write_text(f"nothing is due\nlog at {tmp_path}\\x\n", encoding="utf-8")
    launcher.procs[0].code = code
    run = c.get("/api/run/current").json()["run"]
    assert (run["status"], run["exit_code"], run["has_events"]) == (status, code, False)
    assert any("nothing is due" in x for x in run["output_tail"])
    assert all(str(tmp_path) not in x for x in run["output_tail"])


def test_after_an_app_restart_a_dead_update_reads_as_interrupted(tmp_path) -> None:
    c, _, _, paths = ready(tmp_path)
    rid = start(c).json()["run_id"]
    write_events(events_folder(paths) / f"{rid}.jsonl", rid, update_story(None))
    fresh, *_ = client_for(tmp_path)  # a new app: no child handle, no lock held
    run = fresh.get("/api/run/current").json()["run"]
    assert (run["run_id"], run["kind"], run["status"]) == (rid, "forecastlab_update", "interrupted")


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
    c, launcher, _, paths = ready(tmp_path)
    rid = start(c).json()["run_id"]
    write_events(events_folder(paths) / f"{rid}.jsonl", rid, update_story(OK_END))
    launcher.procs[0].code = 0
    text = c.get("/api/run/stream").text
    msgs = parse_sse(text)
    runs = [m for m in msgs if m.get("event") == "run"]
    assert [m["data"]["type"] for m in runs] == ["run_start", "step_start", "step_end", "run_end"]
    assert runs[0]["data"]["kind"] == "forecastlab_update"
    end = msgs[-1]
    assert end["event"] == "end" and end["data"]["status"] == "ok"
    assert end["data"]["kind"] == "forecastlab_update" and "_events" not in end["data"]
    assert str(tmp_path) not in text


# a terminal `nfl forecastlab update` holds the same lock


def test_a_terminal_update_holding_the_lock_is_found(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    c, *_ = client_for(tmp_path)
    rid = "20261006T140000Z"
    write_events(events_folder(paths) / f"{rid}.jsonl", rid, update_story(None))
    with L.run_lock(L.lock_path(paths.runs), "nfl forecastlab update --season 2026 --week 5",
                    run_id=rid):  # fmt: skip
        cur = c.get("/api/run/current").json()
    assert cur["state"] == "running"
    run = cur["run"]
    assert (run["launched_from"], run["kind"], run["season"], run["week"]) == (
        "terminal", "forecastlab_update", 2026, 5,
    )  # fmt: skip
    assert run["run_id"] == rid and run["has_events"] is True
    assert run["command"] == "nfl forecastlab update --season 2026 --week 5"
    assert run["follows"] is None  # a terminal run follows nothing


def test_a_terminal_update_looks_in_the_forecastlab_folder_only(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    c, *_ = client_for(tmp_path)
    rid = "20261006T140000Z"
    # the week's Pipeline folder and the play-calling folder have a file of that name: not this
    write_events(paths.run_dir(2026, 5) / "events" / f"{rid}.jsonl", rid, update_story(None))
    write_events(paths.playcalling / "2026" / "events" / f"{rid}.jsonl", rid, update_story(None))
    with L.run_lock(L.lock_path(paths.runs), "nfl forecastlab update --season 2026 --week 5",
                    run_id=rid):  # fmt: skip
        run = c.get("/api/run/current").json()["run"]
    assert run["kind"] == "forecastlab_update" and run["has_events"] is False


def test_an_auto_update_takes_the_calendars_week(tmp_path) -> None:
    c, *_ = client_for(tmp_path)
    with L.run_lock(L.lock_path(DataPaths(tmp_path).runs), "nfl forecastlab update --auto"):
        run = c.get("/api/run/current").json()["run"]
    assert (run["kind"], run["season"], run["week"]) == ("forecastlab_update", 2026, 5)


def test_an_older_lock_note_without_a_run_id_finds_the_newest_events(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    c, *_ = client_for(tmp_path)
    write_events(events_folder(paths) / "20261006T140000Z.jsonl", "20261006T140000Z",
                 update_story(None))  # fmt: skip
    with L.run_lock(L.lock_path(paths.runs), "nfl forecastlab update --season 2026 --week 5"):
        note = L.read_holder(L.lock_path(paths.runs)) or {}
        assert "run_id" not in note  # (no run id in the note: written before ids existed)
        run = c.get("/api/run/current").json()["run"]
    assert run["kind"] == "forecastlab_update" and run["has_events"] is True


def test_a_forecastlab_lock_is_told_from_the_other_updates(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    c, *_ = client_for(tmp_path)
    for command, kind in (
        ("nfl forecastlab update --season 2026 --week 5", "forecastlab_update"),
        ("nfl playcalling update --season 2026 --week 5", "playcalling_update"),
        ("nfl weekly injury-update --auto", "injury_update"),
        ("nfl weekly run --season 2026 --week 5 --from-step player", "resume"),
        ("nfl weekly run --auto", "weekly"),
    ):
        with L.run_lock(L.lock_path(paths.runs), command):
            assert c.get("/api/run/current").json()["run"]["kind"] == kind, command


# ---- a Forecast lab lock is not "the week's run" ---------------------------------------------


def entry(c, week: int = 5) -> dict[str, Any]:
    return next(w for w in c.get("/api/weeks").json()["weeks"] if w["week"] == week)


@pytest.mark.parametrize(
    "command",
    ["nfl forecastlab update --season 2026 --week 5", "nfl forecastlab update --auto",
     "nfl forecastlab update --auto --launched-by agent"],
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


def test_a_forecastlab_lock_still_blocks_the_weekly_run_and_says_why(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    c, *_ = client_for(tmp_path)
    cmd = "nfl forecastlab update --season 2026 --week 5"
    with L.run_lock(L.lock_path(paths.runs), cmd):
        pf = c.get("/api/preflight").json()
        lock = next(x for x in pf["checks"] if x["id"] == "lock")
        r = c.post("/api/run", json={"kind": "weekly", "expect_week": 5}, headers=POST)
    assert pf["run"]["allowed"] is False and lock["status"] == "fail"
    assert cmd in lock["reason"]  # the page names what holds the lock
    assert r.status_code == 409 and r.json()["error"]["code"] == "locked"


def test_the_two_updates_shut_each_other_out(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    PU.up_to_date_tree(paths)
    F.only_curves_due(paths)
    publish(paths)
    c, *_ = client_for(tmp_path)
    for command in ("nfl forecastlab update --season 2026 --week 5",
                    "nfl playcalling update --season 2026 --week 5"):  # fmt: skip
        with L.run_lock(L.lock_path(paths.runs), command):
            pf = c.get("/api/preflight").json()
        assert pf["forecastlab"]["reason"] == "A run is in progress."
        assert pf["playcalling"]["reason"] == "A run is in progress."


def test_lock_target_and_is_forecastlab_update() -> None:
    plan = plan_week(final_through_week4(), TUESDAY)
    assert (plan.season, plan.week) == (2026, 5)

    def lock(command: str | None, held: bool = True) -> dict[str, Any]:
        return {"held": held, "command": command, "started": "2026-10-06T10:00:00-04:00"}

    for cmd in ("nfl forecastlab update --season 2026 --week 5", "nfl forecastlab update --auto",
                "nfl forecastlab update --auto --expect-week 5"):  # fmt: skip
        assert is_forecastlab_update(lock(cmd)) is True
        assert lock_target(lock(cmd), plan) is None  # it names a week, but runs none of its steps
    assert is_forecastlab_update(lock("nfl forecastlab update --auto", held=False)) is False
    assert is_forecastlab_update({"held": True, "command": None}) is False
    assert is_forecastlab_update({"held": True}) is False
    assert is_forecastlab_update(lock("nfl forecastlab backtest --model curves")) is False
    assert is_forecastlab_update(lock("nfl playcalling update --auto")) is False
    # the other commands are unchanged
    assert lock_target(lock("nfl weekly run --season 2026 --week 4"), plan) == (2026, 4)
    assert lock_target(lock("nfl weekly run --auto"), plan) == (2026, 5)
    assert lock_target(lock("nfl weekly injury-update --auto"), plan) == (2026, 5)
    assert lock_target(lock("nfl weekly run --auto", held=False), plan) is None


# ---- `follows`: which run an update was chained after ------------------------------------------


def preflight_of(c) -> dict[str, Any]:
    return c.get("/api/preflight").json()


def test_a_run_started_by_the_runner_records_what_it_follows(tmp_path) -> None:
    c, launcher, runner, paths = ready(tmp_path)
    pf = preflight_of(c)

    def record(rid: str) -> dict[str, Any]:
        return json.loads((app_dir(paths) / "runs" / f"{rid}.json").read_text("utf-8"))

    out = runner.start("forecastlab_update", 5, paths=paths, preflight=pf, now=TUESDAY,
                       lock_held=False, follows=WEEKLY_ID)  # fmt: skip
    assert "follows" not in out  # the start's answer is unchanged
    assert record(out["run_id"])["follows"] == WEEKLY_ID
    launcher.procs[0].code = 0
    out = runner.start("forecastlab_update", 5, paths=paths, preflight=pf, now=TUESDAY,
                       lock_held=False)  # fmt: skip
    assert record(out["run_id"])["follows"] is None
    launcher.procs[1].code = 0
    for junk in ("not a run id; rm", "", "2026", "20261006T140012Z-a1b2c3d4e5", "../x"):
        out = runner.start("forecastlab_update", 5, paths=paths, preflight=pf, now=TUESDAY,
                           lock_held=False, follows=junk)  # fmt: skip
        assert record(out["run_id"])["follows"] is None, junk
        launcher.procs[-1].code = 0


def test_a_pressed_update_has_no_follows_and_a_hand_edited_garbage_one_reads_as_none(
    tmp_path,
) -> None:
    c, launcher, _, paths = ready(tmp_path)
    rid = start(c).json()["run_id"]
    assert c.get("/api/run/current").json()["run"]["follows"] is None
    rec_file = app_dir(paths) / "runs" / f"{rid}.json"
    rec = json.loads(rec_file.read_text("utf-8"))
    for value, shown in (
        ("not a run id", None),
        ("", None),
        ("20261006T140012Z; rm -rf", None),
        (WEEKLY_ID, WEEKLY_ID),  # a valid one is kept
    ):
        rec_file.write_text(json.dumps({**rec, "follows": value}), encoding="utf-8")
        run = c.get("/api/run/current").json()["run"]
        assert run["follows"] == shown, value
    launcher.procs[0].code = 0
    rec_file.write_text(json.dumps({**rec, "follows": "junk"}), encoding="utf-8")
    assert c.get("/api/run/current").json()["run"]["follows"] is None  # finished, too


@pytest.mark.parametrize("value", [["20261006T140012Z"], 12345, True, {"id": WEEKLY_ID}, 1.5])
def test_a_hand_edited_follows_that_is_not_text_reads_as_none(tmp_path, value) -> None:
    c, launcher, _, paths = ready(tmp_path)
    rid = start(c).json()["run_id"]
    rec_file = app_dir(paths) / "runs" / f"{rid}.json"
    rec = json.loads(rec_file.read_text("utf-8"))
    rec_file.write_text(json.dumps({**rec, "follows": value}), encoding="utf-8")
    res = c.get("/api/run/current")
    assert res.status_code == 200 and res.json()["run"]["follows"] is None
    assert c.get("/api/preflight").status_code == 200  # the pre-flight reads the same record
    launcher.procs[0].code = 0
    assert c.get("/api/run/current").json()["run"]["follows"] is None  # finished, too


@pytest.mark.parametrize("run_id", [["20261006T140012Z"], 12345, True, {"a": 1}, None, ""])
def test_a_hand_edited_record_whose_run_id_is_not_text_is_skipped(tmp_path, run_id) -> None:
    """The records folder is read on every `/api/run/current` and pre-flight: a bad one is
    left out, never a 500."""
    c, _, _, paths = ready(tmp_path)
    bad = app_dir(paths) / "runs" / "20261006T150000Z-zzzz.json"  # sorts as the newest
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text(json.dumps({"run_id": run_id, "kind": "weekly", "follows": run_id}), "utf-8")
    assert c.get("/api/run/current").json() == {"state": "idle", "run": None}
    rid = start(c).json()["run_id"]
    run = c.get("/api/run/current").json()["run"]
    assert run["run_id"] == rid and run["follows"] is None
    assert c.get("/api/preflight").status_code == 200


def test_an_older_record_without_follows_reads_as_none(tmp_path) -> None:
    c, launcher, _, paths = ready(tmp_path)
    rid = start(c).json()["run_id"]
    rec_file = app_dir(paths) / "runs" / f"{rid}.json"
    rec = json.loads(rec_file.read_text("utf-8"))
    del rec["follows"]
    rec_file.write_text(json.dumps(rec), encoding="utf-8")
    launcher.procs[0].code = 0
    run = c.get("/api/run/current").json()["run"]
    assert "follows" in run and run["follows"] is None


# ---- the follow-up ---------------------------------------------------------------------------


def chained(tmp_path, *, now=TUESDAY, **kw) -> SimpleNamespace:
    """A client whose app chains the update (`chain=True`, a 10 ms poll); `threads` collects
    what `follow_up` returned for each successful POST."""
    kw.setdefault("chain", True)
    kw.setdefault("chain_poll_s", 0.01)
    c, launcher, runner, paths = client_for(tmp_path, now=now, **kw)
    ctx = c.app.state.ctx
    threads: list[threading.Thread | None] = []
    real = ctx.follow_up

    def spy(started):
        t = real(started)
        threads.append(t)
        return t

    ctx.follow_up = spy
    return SimpleNamespace(c=c, launcher=launcher, runner=runner, paths=paths, ctx=ctx,
                           threads=threads)  # fmt: skip


def weekly(c):
    return c.post("/api/run", json={"kind": "weekly", "expect_week": 5}, headers=POST)


def join(h: SimpleNamespace, timeout: float = 10.0) -> None:
    for t in h.threads:
        if t is not None:
            t.join(timeout)
            assert not t.is_alive(), "the follow-up thread is still running"


def wait_until(cond: Callable[[], bool], timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.005)
    raise AssertionError("timed out")


def chain_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "fl-chain"]


def spy_preflight(h: SimpleNamespace) -> list[str | None]:
    """Record the Forecast lab gate's reason at every pre-flight the follow-up asks for."""
    reasons: list[str | None] = []
    real = h.ctx.preflight

    def spy(paths):
        out = real(paths)
        reasons.append(out["forecastlab"]["reason"])
        return out

    h.ctx.preflight = spy
    return reasons


def forecastlab_argv(argv: list[str]) -> bool:
    return argv[3:9] == ["forecastlab", "update", "--auto", "--expect-week", "5", "--launched-by"]


def test_a_successful_weekly_run_is_followed_by_exactly_one_forecastlab_update(tmp_path) -> None:
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    r = weekly(h.c)
    assert r.status_code == 202
    weekly_id = r.json()["run_id"]
    assert len(h.threads) == 1 and h.threads[0] is not None and h.threads[0].name == "fl-chain"
    assert len(h.launcher.calls) == 1  # the run is still going: nothing else started
    publish(h.paths)  # the run publishes the week...
    h.launcher.procs[0].code = 0  # ...and ends well
    join(h)
    assert len(h.launcher.calls) == 2
    assert forecastlab_argv(h.launcher.calls[1])
    assert h.launcher.calls[1][-2] == "--app-run" and h.launcher.calls[1][-1] != weekly_id
    cur = h.c.get("/api/run/current").json()
    assert cur["state"] == "running"
    run = cur["run"]
    assert (run["kind"], run["command"], run["launched_from"]) == (
        "forecastlab_update", COMMAND, "app",
    )  # fmt: skip
    assert run["follows"] == weekly_id  # chained after the weekly run
    assert run["run_id"] == h.launcher.calls[1][-1]
    # and nothing more: the update's own end starts no third run, from any thread
    h.launcher.procs[1].code = 0
    assert len(h.threads) == 1 and chain_threads() == []
    assert len(h.launcher.calls) == 2


def test_the_chained_runs_record_names_the_weekly_run_it_followed(tmp_path) -> None:
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    weekly_id = weekly(h.c).json()["run_id"]
    publish(h.paths)
    h.launcher.procs[0].code = 0
    join(h)
    rid = h.launcher.calls[1][-1]
    rec = json.loads((app_dir(h.paths) / "runs" / f"{rid}.json").read_text("utf-8"))
    assert (rec["kind"], rec["request_kind"], rec["follows"]) == (
        "forecastlab_update", "forecastlab_update", weekly_id,
    )  # fmt: skip
    assert rec["events_dir"] == str(events_folder(h.paths))


def test_a_pressed_update_next_to_a_chained_one(tmp_path) -> None:
    """`follows` tells them apart: the chained run has it, the press after it does not."""
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    weekly_id = weekly(h.c).json()["run_id"]
    publish(h.paths)
    h.launcher.procs[0].code = 0
    join(h)
    assert h.c.get("/api/run/current").json()["run"]["follows"] == weekly_id
    h.launcher.procs[1].code = 0  # the update ends (the fake did no work: still due)
    pressed = start(h.c).json()["run_id"]
    run = h.c.get("/api/run/current").json()["run"]
    assert (run["run_id"], run["follows"]) == (pressed, None)
    assert h.threads[-1] is None and len(h.threads) == 2  # the press chained nothing


@pytest.mark.parametrize("code", [1, 2, 3, 4, 5, 6])
def test_a_run_that_does_not_end_well_starts_no_update(tmp_path, code, caplog) -> None:
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    weekly(h.c)
    publish(h.paths)
    with caplog.at_level(logging.INFO, logger="nflengine.app"):
        h.launcher.procs[0].code = code
        join(h)
    assert len(h.launcher.calls) == 1
    assert f"ended {code}" in caplog.text


def test_when_the_app_no_longer_holds_the_child_nothing_starts(tmp_path, caplog) -> None:
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    weekly(h.c)
    publish(h.paths)
    with caplog.at_level(logging.INFO, logger="nflengine.app"):
        h.runner._child = None  # what an app restart or another launch leaves
        join(h)
    assert len(h.launcher.calls) == 1 and "ended -1" in caplog.text


def test_with_chain_off_nothing_follows_a_run(tmp_path) -> None:
    h = chained(tmp_path, chain=False)  # also the default of `AppSettings`
    F.only_curves_due(h.paths)
    assert weekly(h.c).status_code == 202
    publish(h.paths)
    h.launcher.procs[0].code = 0
    assert h.threads == [None] and chain_threads() == []
    assert len(h.launcher.calls) == 1


def test_chain_is_off_unless_nfl_app_turns_it_on(tmp_path, monkeypatch) -> None:
    import socket

    import uvicorn

    import nflengine.app.serve as serve_mod

    assert server_mod.AppSettings().chain is False  # the default: tests and tools never chain
    seen: list[Any] = []

    def capture(settings):
        seen.append(settings)
        return object()  # no app is built, no service is created

    class FakeServer:  # no server is started
        def __init__(self, config):
            pass

        def run(self):
            pass

    monkeypatch.setattr(server_mod, "create_app", capture)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    for rehearsal in (False, True):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        code = serve_mod.serve(
            port=port, dev=True, open_browser=False, rehearsal=rehearsal, log=lambda _m: None
        )
        assert code == 0
    live, rehearsed = seen
    assert live.chain is True and live.rehearsal is False  # the real app chains...
    assert rehearsed.chain is True and rehearsed.rehearsal is True  # ...and the rehearsal flag
    # alone keeps a rehearsal from chaining (`follow_up`, `chain` in the pre-flight)


def test_no_follow_up_in_rehearsal_mode(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    write_week(paths, 2026, 4, ALL_OK, report=True)
    c, launcher, runner, _ = client_for(tmp_path, rehearsal=True, chain=True, chain_poll_s=0.01)
    ctx = c.app.state.ctx
    threads: list[Any] = []
    real = ctx.follow_up
    ctx.follow_up = lambda started: threads.append(real(started)) or threads[-1]
    r = c.post("/api/run", json={"kind": "weekly", "expect_week": 4}, headers=POST)
    assert r.status_code == 202 and r.json()["kind"] == "rehearsal"
    launcher.procs[0].code = 0
    assert threads == [None] and chain_threads() == []
    assert len(launcher.calls) == 1 and runner.rehearsal is True


@pytest.mark.parametrize(
    "kind", ["playcalling_update", "forecastlab_update", "rehearsal", "unknown", None]
)
def test_only_a_weekly_resume_or_injury_run_can_chain(tmp_path, kind) -> None:
    h = chained(tmp_path)
    assert h.ctx.s.chain is True
    started = {"run_id": WEEKLY_ID, "kind": kind, "command": "x", "season": 2026, "week": 5}
    assert server_mod._Context.follow_up(h.ctx, started) is None  # (the real one, not the spy)
    assert chain_threads() == []


def test_a_playcalling_update_chains_nothing(tmp_path) -> None:
    h = chained(tmp_path)
    PU.up_to_date_tree(h.paths)
    for name in ("forecast.parquet", "forecast.json"):
        (h.paths.playcalling / "2026" / "week05" / name).unlink()
    F.FLTree(h.paths).models()  # the Forecast lab has something due, too
    publish(h.paths)
    r = h.c.post("/api/run", json={"kind": "playcalling_update", "expect_week": 5}, headers=POST)
    assert r.status_code == 202, r.text
    assert gate(h.c)["reason"] == "A run is in progress."
    h.launcher.procs[0].code = 0
    assert h.threads == [None] and chain_threads() == []
    assert gate(h.c)["allowed"] is True  # it would run, if somebody pressed the button
    assert len(h.launcher.calls) == 1


def test_a_forecastlab_update_chains_nothing_not_even_when_it_fails(tmp_path) -> None:
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    publish(h.paths)
    for code in (0, 1):
        assert start(h.c).status_code == 202
        h.launcher.procs[-1].code = code
    assert h.threads == [None, None] and chain_threads() == []
    assert len(h.launcher.calls) == 2


def test_nothing_due_means_no_update_after_the_run(tmp_path, caplog) -> None:
    h = chained(tmp_path)
    F.up_to_date(h.paths)
    weekly(h.c)
    publish(h.paths)
    reasons = spy_preflight(h)
    with caplog.at_level(logging.INFO, logger="nflengine.app"):
        h.launcher.procs[0].code = 0
        join(h)
    assert len(h.launcher.calls) == 1
    assert reasons == ["Everything is up to date."]  # asked once, and left
    assert "nothing to do (Everything is up to date.)" in caplog.text


def test_a_run_that_did_not_publish_gets_no_update_and_is_not_retried(tmp_path, caplog) -> None:
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    weekly(h.c)
    reasons = spy_preflight(h)
    with caplog.at_level(logging.INFO, logger="nflengine.app"):
        h.launcher.procs[0].code = 0  # exit 0, but the week isn't published
        join(h)
    assert len(h.launcher.calls) == 1
    assert reasons == ["Needs this week's Tuesday run first (Run week 5)."]


@pytest.mark.parametrize(
    ("later", "shown"),
    [
        (F.LATER, "6"),  # the next Tuesday: the calendar is in week 6
        (dt.datetime(2026, 6, 1, tzinfo=dt.UTC), "None"),  # the offseason: no week at all
    ],
)
def test_the_follow_up_stops_when_the_calendar_moved_on_during_the_run(
    tmp_path, caplog, later, shown
) -> None:
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    r = weekly(h.c)
    assert r.json()["week"] == 5
    publish(h.paths)
    h.ctx.s.now = lambda: later  # the run takes so long that the week rolls over
    assert h.c.get("/api/preflight").json()["week"] != 5  # (the server really is elsewhere now)
    reasons = spy_preflight(h)
    with caplog.at_level(logging.INFO, logger="nflengine.app"):
        h.launcher.procs[0].code = 0
        join(h)
    assert len(h.launcher.calls) == 1  # no update, for week 5 or any other
    assert len(reasons) == 1  # one look, no retry
    assert f"the week changed ({shown})" in caplog.text


def test_the_follow_up_is_for_the_week_of_the_run_that_ended(tmp_path) -> None:
    """The same run, with the calendar still in its week, does start the update, and for it."""
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    r = weekly(h.c)
    publish(h.paths)
    h.launcher.procs[0].code = 0
    join(h)
    assert len(h.launcher.calls) == 2 and forecastlab_argv(h.launcher.calls[1])
    assert (r.json()["week"], h.c.get("/api/run/current").json()["run"]["week"]) == (5, 5)


def test_a_lock_that_is_still_held_is_retried_and_then_the_update_starts(tmp_path) -> None:
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    weekly(h.c)
    publish(h.paths)
    reasons = spy_preflight(h)
    with L.run_lock(L.lock_path(h.paths.runs), "nfl weekly run --auto --expect-week 5"):
        h.launcher.procs[0].code = 0  # the child has exited; its lock is not let go yet
        wait_until(lambda: len(reasons) >= 3)
        assert len(h.launcher.calls) == 1  # it waits: it does not start over the lock
        assert set(reasons) == {"A run is in progress."}
    join(h)
    assert len(h.launcher.calls) == 2 and forecastlab_argv(h.launcher.calls[1])
    assert reasons[-1] is None  # the gate opened, and it started once
    assert len(chain_threads()) == 0


def test_a_lock_taken_between_the_check_and_the_start_is_retried_too(tmp_path, monkeypatch) -> None:
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    weekly(h.c)
    publish(h.paths)
    real = server_mod.lock_dict
    asked: list[int] = []

    def flaky(paths):
        asked.append(1)  # the gate said yes, but somebody took the lock just now (twice)
        if len(asked) <= 2:
            return {"held": True, "command": "nfl weekly run --auto", "started": None}
        return real(paths)

    monkeypatch.setattr(server_mod, "lock_dict", flaky)
    h.launcher.procs[0].code = 0
    join(h)
    assert len(asked) == 3 and len(h.launcher.calls) == 2
    assert forecastlab_argv(h.launcher.calls[1])


def test_it_gives_up_when_the_lock_stays_busy(tmp_path, caplog) -> None:
    h = chained(tmp_path, chain_tries=3)
    F.only_curves_due(h.paths)
    weekly(h.c)
    publish(h.paths)
    reasons = spy_preflight(h)
    lock = L.run_lock(L.lock_path(h.paths.runs), "nfl weekly run --auto --expect-week 5")
    with caplog.at_level(logging.INFO, logger="nflengine.app"), lock:
        h.launcher.procs[0].code = 0
        join(h)
    assert len(h.launcher.calls) == 1 and len(reasons) == 3  # exactly `chain_tries` looks
    assert "the lock stayed busy" in caplog.text


def test_it_gives_up_waiting_for_a_run_that_never_ends(tmp_path, caplog) -> None:
    h = chained(tmp_path, chain_wait_s=0.05)
    F.only_curves_due(h.paths)
    weekly(h.c)
    with caplog.at_level(logging.INFO, logger="nflengine.app"):
        join(h)  # the fake child never exits
    assert len(h.launcher.calls) == 1 and "gave up waiting" in caplog.text


def test_a_refusal_for_another_reason_ends_the_follow_up(tmp_path, caplog) -> None:
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    weekly(h.c)
    publish(h.paths)
    tries: list[str] = []

    def refuse(kind, *a, **k):
        tries.append(kind)
        raise RunRefused("not_allowed", "nope")

    h.runner.start = refuse
    with caplog.at_level(logging.INFO, logger="nflengine.app"):
        h.launcher.procs[0].code = 0
        join(h)
    assert tries == ["forecastlab_update"]  # tried once, never again
    assert len(h.launcher.calls) == 1 and "refused: nope" in caplog.text


def test_an_error_inside_the_follow_up_never_escapes_the_thread(
    tmp_path, monkeypatch, caplog
) -> None:
    escaped: list[Any] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: escaped.append(args))
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    weekly(h.c)
    publish(h.paths)

    def boom(paths):
        raise RuntimeError("the pre-flight broke")

    h.ctx.preflight = boom
    with caplog.at_level(logging.INFO, logger="nflengine.app"):
        h.launcher.procs[0].code = 0
        join(h)
    assert escaped == [] and len(h.launcher.calls) == 1
    assert "forecast lab follow-up failed" in caplog.text


def test_a_launcher_that_fails_does_not_escape_either(tmp_path, monkeypatch, caplog) -> None:
    escaped: list[Any] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: escaped.append(args))
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    weekly(h.c)
    publish(h.paths)

    def refuse_to_launch(argv, log_file):
        raise OSError("cannot start a process")

    h.runner.launcher = refuse_to_launch
    with caplog.at_level(logging.INFO, logger="nflengine.app"):
        h.launcher.procs[0].code = 0
        join(h)
    assert escaped == [] and "forecast lab follow-up failed" in caplog.text
    assert len(h.launcher.calls) == 1


def test_a_resume_is_followed_too(tmp_path) -> None:
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    steps = {**{k: "ok" for k in ("ingest", "ready", "curate", "ratings", "game", "graph")},
             "player": "failed"}  # fmt: skip
    write_week(h.paths, 2026, 5, steps)
    r = h.c.post("/api/run", json={"kind": "resume", "expect_week": 5}, headers=POST)
    assert r.status_code == 202 and r.json()["kind"] == "resume"
    publish(h.paths)  # the resumed run finishes the week
    h.launcher.procs[0].code = 0
    join(h)
    assert len(h.launcher.calls) == 2 and forecastlab_argv(h.launcher.calls[1])
    assert h.c.get("/api/run/current").json()["run"]["follows"] == r.json()["run_id"]


def test_a_failed_resume_is_not(tmp_path) -> None:
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    write_week(h.paths, 2026, 5, {"ingest": "ok", "ready": "ok", "curate": "failed"})
    assert (
        h.c.post("/api/run", json={"kind": "resume", "expect_week": 5}, headers=POST).status_code
        == 202
    )
    h.launcher.procs[0].code = 1
    join(h)
    assert len(h.launcher.calls) == 1


def test_the_injury_update_is_followed_by_the_saturday_view(tmp_path) -> None:
    h = chained(tmp_path, now=SATURDAY)
    F.up_to_date(h.paths)  # Tuesday's view and curves are made: nothing is due yet
    publish(h.paths)
    assert not any(s["due"] for s in gate(h.c)["steps"])
    r = h.c.post("/api/run", json={"kind": "injury_update", "expect_week": 5}, headers=POST)
    assert r.status_code == 202 and r.json()["kind"] == "injury_update"
    F.FLTree(h.paths).injury_update(5, SATURDAY)  # the update writes its file...
    h.launcher.procs[0].code = 0  # ...and ends well
    join(h)
    assert len(h.launcher.calls) == 2 and forecastlab_argv(h.launcher.calls[1])
    assert h.c.get("/api/run/current").json()["run"]["follows"] == r.json()["run_id"]


def test_a_failed_injury_update_is_not_followed(tmp_path) -> None:
    h = chained(tmp_path, now=SATURDAY)
    F.up_to_date(h.paths)
    publish(h.paths)
    assert h.c.post("/api/run", json={"kind": "injury_update", "expect_week": 5},
                    headers=POST).status_code == 202  # fmt: skip
    F.FLTree(h.paths).injury_update(5, SATURDAY)
    h.launcher.procs[0].code = 1
    join(h)
    assert len(h.launcher.calls) == 1


def test_only_a_post_ever_starts_a_follow_up(tmp_path) -> None:
    h = chained(tmp_path)
    F.only_curves_due(h.paths)
    publish(h.paths)
    for url in ("/api/preflight", "/api/run/current", "/api/meta", "/api/weeks",
                "/api/weeks/2026/5", "/api/weeks/2026/5/pipeline"):  # fmt: skip
        assert h.c.get(url).status_code == 200
    assert h.threads == [] and h.launcher.calls == [] and chain_threads() == []


@pytest.mark.parametrize("kind", list(KINDS))
def test_over_the_tailscale_name_every_run_is_refused_and_nothing_chains(tmp_path, kind) -> None:
    h = chained(tmp_path, tailscale=NAME)
    F.only_curves_due(h.paths)
    publish(h.paths)
    ok = {"host": NAME, "origin": f"https://{NAME}", "x-cr-token": TOKEN}
    r = h.c.post("/api/run", json={"kind": kind, "expect_week": 5}, headers=ok)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "remote_read_only")
    assert h.threads == [] and h.launcher.calls == [] and chain_threads() == []
    # reads still answer, and say the app chains
    pf = h.c.get("/api/preflight", headers={"host": NAME})
    assert pf.status_code == 200 and pf.json()["chain"] is True
