"""`nfl playcalling update` (D132): the command's wiring on a tmp tree.

The data root, the calendar and the clock are pinned (`env`), the five steps are stubbed where
`run_update` imports them, and `--app-run` marks are recorded instead of set. Nothing reaches
D:, the network or W&B. The plan itself is the real `plan_update` on the tree.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import playcall_update_fixtures as U
import polars as pl
import pytest
from playcall_update_fixtures import G_MNF, MNF, Tree, up_to_date_tree
from typer.testing import CliRunner

import nflengine.live.review as review_mod
import nflengine.ops.calendar as cal
import nflengine.paths as paths_mod
import nflengine.playcalling.build as build_mod
import nflengine.playcalling.forecast_runs as runs_mod
import nflengine.playcalling.update as P
import nflengine.tracking as tracking
from nflengine import cli
from nflengine.cli import app
from nflengine.ops.lock import is_locked, lock_path, read_holder, run_lock
from nflengine.paths import DataPaths, DataRootError

runner = CliRunner()
H = dt.timedelta(hours=1)
RUN_ID = "20261006T140012Z-a1b2"


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


@dataclass
class Env:
    paths: DataPaths
    now: dt.datetime = U.NOW
    calendar: Any = field(default_factory=lambda: SimpleNamespace(season=2026, week=5))
    ensure_calls: list[bool] = field(default_factory=list)
    calendar_calls: int = 0
    plan_calls: int = 0
    marks: list[str] = field(default_factory=list)
    calls: list[tuple[str, tuple, dict]] = field(default_factory=list)
    fail: dict[str, Exception] = field(default_factory=dict)
    during: dict[str, Any] = field(default_factory=dict)

    @property
    def order(self) -> list[str]:
        return [c[0] for c in self.calls]

    @property
    def events_dir(self):
        return self.paths.playcalling / "2026" / "events"

    def events(self) -> list[dict[str, Any]]:
        files = sorted(self.events_dir.glob("*.jsonl")) if self.events_dir.exists() else []
        assert len(files) <= 1, files
        if not files:
            return []
        return [json.loads(x) for x in files[0].read_text(encoding="utf-8").splitlines() if x]

    def all_due(self) -> Env:
        """Every step due: Monday night's FTN ripe, week 4 unreviewed and ungraded, no forecast."""
        t = Tree(self.paths).games().model().forecast(4, made_at=U.FORECAST_MADE)
        t.build(waiting=(G_MNF,))
        self.now = MNF + 42 * H
        return self

    def only_forecast_due(self) -> Env:
        t = up_to_date_tree(self.paths)
        for name in ("forecast.parquet", "forecast.json"):
            (t.pc / "week05" / name).unlink()
        return self


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    e = Env(DataPaths(tmp_path))

    def ensure(create_dirs: bool = True):
        e.ensure_calls.append(create_dirs)
        return e.paths

    def load_schedules(paths):
        e.calendar_calls += 1

    monkeypatch.setattr(paths_mod, "ensure_data_root", ensure)
    monkeypatch.setattr(cal, "load_schedules", load_schedules)
    monkeypatch.setattr(cal, "plan_week", lambda sched, *a, **k: e.calendar)
    real_plan = P.plan_update

    def plan(paths, season, week, now):
        e.plan_calls += 1
        return real_plan(paths, season, week, e.now)

    monkeypatch.setattr(P, "plan_update", plan)
    monkeypatch.setattr(tracking, "set_launch_via", lambda via: e.marks.append(via))
    monkeypatch.setattr(cli.console, "_width", 250)

    def stub(name, ret):
        def fn(*a, **k):
            e.calls.append((name, a, k))
            if name in e.during:
                e.during[name](e)
            if name in e.fail:
                raise e.fail[name]
            return ret

        return fn

    monkeypatch.setattr(
        build_mod, "run_build", stub("build", {"seasons": [{"as_of_weeks": [1, 5]}]})
    )
    monkeypatch.setattr(review_mod, "run_review", stub("review", [{"week": 4}]))
    monkeypatch.setattr(
        runs_mod, "run_grade",
        stub("grade", {"counts": {"graded": 24}, "frame": pl.DataFrame({"x": range(24)})}),
    )  # fmt: skip
    monkeypatch.setattr(
        runs_mod, "run_forecast",
        stub("forecast", {"meta": {"games": 4, "rows": 24, "games_after_kickoff": []}}),
    )  # fmt: skip
    monkeypatch.setattr(P, "_ftn_refresh", stub("ftn", "FTN pulled, 9 curated tables rebuilt"))
    return e


def update(*args: str):
    return runner.invoke(app, ["playcalling", "update", *args])


# ---- wiring --------------------------------------------------------------------------------------


def test_the_command_is_listed_with_its_options() -> None:
    assert "update" in runner.invoke(app, ["playcalling", "--help"]).output
    out = runner.invoke(app, ["playcalling", "update", "--help"])
    assert out.exit_code == 0
    for opt in ("--season", "--week", "--auto", "--expect-week", "--dry-run", "--no-wandb",
                "--launched-by"):  # fmt: skip
        assert opt in out.output, opt
    assert "--app-run" not in out.output  # hidden: only the control room sends it


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--expect-week", "5"], "--expect-week only works with --auto"),
        ([], "give --season and --week, or --auto"),
        (["--season", "2026"], "give --season and --week, or --auto"),
        (["--week", "5"], "give --season and --week, or --auto"),
        (["--auto", "--app-run", "not-a-run-id"], "--app-run takes a run id"),
        (["--auto", "--app-run", "20261006T140012Z-a1b2; rm"], "--app-run takes a run id"),
    ],
)
def test_bad_usage_is_exit_2_and_runs_nothing(env, args, message) -> None:
    r = update(*args)
    assert r.exit_code == 2 and message in flat(r.output)
    assert env.calls == [] and env.plan_calls == 0 and env.marks == []


@pytest.mark.parametrize("n", ["0", "23", "-1"])
def test_expect_week_is_a_week_number(env, n) -> None:
    assert update("--auto", "--expect-week", n).exit_code == 2
    assert env.calls == [] and env.ensure_calls == []


# ---- exit codes before anything runs -------------------------------------------------------------


def test_exit_6_when_the_calendar_is_in_another_week(env) -> None:
    env.all_due()
    env.calendar = SimpleNamespace(season=2026, week=6)
    r = update("--auto", "--expect-week", "5")
    assert r.exit_code == 6
    assert flat(r.output).strip() == (
        "the calendar's week is week 6, not week 5 (--expect-week): nothing was run"
    )
    assert env.ensure_calls == [False]  # looked at, not created
    assert env.plan_calls == 0 and env.calls == []
    assert not lock_path(env.paths.runs).exists() and not env.events_dir.exists()


def test_exit_6_in_the_offseason_when_a_week_was_expected(env) -> None:
    env.calendar = SimpleNamespace(season=2026, week=None)
    r = update("--auto", "--expect-week", "5")
    assert r.exit_code == 6 and "no week (offseason)" in flat(r.output)
    assert env.plan_calls == 0


def test_the_offseason_is_nothing_to_update(env) -> None:
    env.calendar = SimpleNamespace(season=2026, week=None)
    r = update("--auto")
    assert r.exit_code == 0 and "offseason: nothing to update" in flat(r.output)
    assert env.plan_calls == 0 and env.calls == []


def test_exit_5_without_the_data_drive(env, monkeypatch) -> None:
    def gone(create_dirs: bool = True):
        raise DataRootError("the data drive isn't connected")

    monkeypatch.setattr(paths_mod, "ensure_data_root", gone)
    r = update("--auto")
    assert r.exit_code == 5 and "data drive isn't connected" in flat(r.output)
    assert env.calls == []


def test_exit_4_when_another_run_holds_the_lock(env) -> None:
    env.all_due()
    with run_lock(lock_path(env.paths.runs), "nfl weekly run --auto --expect-week 5"):
        r = update("--auto", "--expect-week", "5")
    assert r.exit_code == 4
    out = flat(r.output)
    assert "another weekly run holds" in out and "nfl weekly run --auto --expect-week 5" in out
    assert env.calls == []  # not even the first step
    assert not env.events_dir.exists()  # and no events file
    # the lock is the other run's: its note is untouched
    assert env.plan_calls == 1  # (the plan is read-only and comes first)


def test_an_explicit_week_does_not_ask_the_calendar(env) -> None:
    env.only_forecast_due()
    r = update("--season", "2026", "--week", "5")
    assert r.exit_code == 0, r.output
    assert env.calendar_calls == 0 and env.ensure_calls[0] is True  # (the events writer asks too)
    assert env.order == ["forecast"]


def test_an_expected_week_makes_the_data_root_after_the_check(env) -> None:
    env.only_forecast_due()
    assert update("--auto", "--expect-week", "5").exit_code == 0
    assert env.ensure_calls[:2] == [False, True]  # read-only first, then the real one
    env.ensure_calls.clear()
    assert update("--auto").exit_code == 0
    assert env.ensure_calls[0] is True


# ---- the plan, a dry run, nothing due ------------------------------------------------------------


def test_a_dry_run_lists_the_plan_and_does_nothing(env) -> None:
    env.all_due()
    r = update("--auto", "--dry-run")
    assert r.exit_code == 0, r.output
    lines = [flat(x) for x in r.output.strip().splitlines()]
    assert [x.split(":")[0] for x in lines] == [
        "due ftn", "due build", "due review", "due grade", "due forecast",
    ]  # fmt: skip
    assert lines[0] == (
        "due ftn: Refresh FTN charting: ATL at NO should have FTN by now (it lands ~2 days after a "
        "game)"
    )
    assert lines[4] == "due forecast: Forecast week 5: no forecast for week 5 yet"
    assert env.calls == [] and not lock_path(env.paths.runs).exists()
    assert not env.events_dir.exists()


def test_a_dry_run_marks_what_is_skipped(env) -> None:
    env.only_forecast_due()
    out = flat(update("--season", "2026", "--week", "5", "--dry-run").output)
    assert "skip ftn: Refresh FTN charting: every earlier game has its FTN charting" in out
    assert "skip build: Build the play-calling tables: up to date (as of week 5)" in out
    assert "due forecast: Forecast week 5: no forecast for week 5 yet" in out
    assert "nothing is due" not in out  # a dry run only lists
    assert env.calls == []


def test_nothing_due_says_so_and_takes_no_lock(env) -> None:
    up_to_date_tree(env.paths)
    r = update("--auto", "--expect-week", "5")
    assert r.exit_code == 0
    out = flat(r.output)
    assert "skip ftn:" in out and "nothing is due: everything is up to date" in out
    assert env.calls == []
    assert not lock_path(env.paths.runs).exists() and not env.events_dir.exists()
    # so even while another run holds the lock this exits 0
    with run_lock(lock_path(env.paths.runs), "nfl weekly run --auto"):
        assert update("--auto", "--expect-week", "5").exit_code == 0


def test_labels_and_reasons_are_not_read_as_markup(env) -> None:
    env.only_forecast_due()
    (env.paths.models / "playcall" / "production.json").unlink()
    out = flat(update("--season", "2026", "--week", "5", "--dry-run").output)
    assert "no promoted playcall-model (`nfl playcalling backtest`)" in out


# ---- a run ---------------------------------------------------------------------------------------


def test_a_run_does_the_due_steps_in_order_and_exits_0(env) -> None:
    env.all_due()
    r = update("--auto", "--expect-week", "5", "--launched-by", "agent")
    assert r.exit_code == 0, r.output
    assert env.order == ["ftn", "build", "review", "grade", "forecast"]
    out = flat(r.output)
    assert "done: ftn, build, review, grade, forecast" in out
    assert re.search(r"done: ftn, build, review, grade, forecast \(\d+ s\)", out)
    for name in ("ftn", "build", "review", "grade", "forecast"):
        assert f"{name}: ok (" in out
    # options reach the steps
    _, a, k = next(c for c in env.calls if c[0] == "build")
    assert k["use_wandb"] is True and k["launched_by"] == "agent" and k["paths"] == env.paths
    _, a, k = next(c for c in env.calls if c[0] == "forecast")
    assert a == (2026, 5) and k["use_wandb"] is True


def test_no_wandb_reaches_every_step(env) -> None:
    env.all_due()
    assert update("--auto", "--no-wandb").exit_code == 0
    for name, _, k in env.calls:
        if name != "ftn":  # the FTN refresh logs nothing to W&B
            assert k["use_wandb"] is False, name


def test_only_the_due_steps_run(env) -> None:
    env.only_forecast_due()
    r = update("--auto")
    assert r.exit_code == 0 and env.order == ["forecast"]
    assert "done: forecast" in flat(r.output)


def test_the_lock_is_held_during_the_steps_and_freed_after(env) -> None:
    env.all_due()
    seen: dict[str, Any] = {}

    def during(e: Env) -> None:
        path = lock_path(e.paths.runs)
        seen["held"] = is_locked(path)
        seen["note"] = read_holder(path)

    env.during["build"] = during
    assert update("--auto", "--expect-week", "5", "--app-run", RUN_ID).exit_code == 0
    assert seen["held"] is True
    note = seen["note"]
    # the control room finds the run by this command and run id
    assert note["command"] == "nfl playcalling update --season 2026 --week 5"
    assert note["run_id"] == RUN_ID
    assert not is_locked(lock_path(env.paths.runs))
    assert not read_holder(lock_path(env.paths.runs))  # the note is emptied


def test_the_lock_note_has_the_same_command_without_a_run_id(env) -> None:
    env.all_due()
    seen: dict[str, Any] = {}
    env.during["review"] = lambda e: seen.update(note=read_holder(lock_path(e.paths.runs)))
    assert update("--season", "2026", "--week", "5").exit_code == 0
    assert seen["note"]["command"] == "nfl playcalling update --season 2026 --week 5"
    assert re.fullmatch(r"\d{8}T\d{6}Z", seen["note"]["run_id"])  # a fresh id of its own


# ---- the events file -----------------------------------------------------------------------------


def test_events_of_a_successful_run(env) -> None:
    env.all_due()
    assert update("--auto", "--expect-week", "5", "--app-run", RUN_ID,
                  "--launched-by", "rishi").exit_code == 0  # fmt: skip
    assert env.marks == ["control-room"]  # the process is marked as launched by the app
    assert [f.name for f in env.events_dir.glob("*")] == [f"{RUN_ID}.jsonl"]
    evs = env.events()
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    assert all(e["run_id"] == RUN_ID for e in evs)
    start = evs[0]
    assert start["type"] == "run_start"
    assert (start["kind"], start["season"], start["week"]) == ("playcalling_update", 2026, 5)
    assert start["command"] == "nfl playcalling update --auto"
    assert start["steps"] == ["ftn", "build", "review", "grade", "forecast"]
    assert (start["launched_by"], start["via"]) == ("rishi", "control-room")
    assert [p.split(":")[0] for p in start["plan"]] == list(P.STEPS)
    assert start["plan"][0].startswith("ftn: ATL at NO should have FTN by now")
    steps = [(e["type"], e["step"]) for e in evs if e["type"] in ("step_start", "step_end")]
    assert steps == [(t, n) for n in P.STEPS for t in ("step_start", "step_end")]
    ends = {e["step"]: e for e in evs if e["type"] == "step_end"}
    assert all(e["status"] == "ok" for e in ends.values())
    assert ends["build"]["detail"] == "tables as of weeks 1-5"
    assert ends["review"]["detail"] == "week 4 reviewed"
    assert ends["forecast"]["detail"] == "4 games, 24 forecasts"
    # the console lines are log events, too
    logs = [e["text"] for e in evs if e["type"] == "log"]
    assert any(x.startswith("build: ok (") for x in logs)
    end = evs[-1]
    assert end["type"] == "run_end"
    assert (end["status"], end["exit_code"]) == ("ok", 0)
    assert end["message"] == "done: ftn, build, review, grade, forecast"
    assert end["published"] is False


def test_the_plan_in_run_start_lists_the_skipped_steps_too(env) -> None:
    env.only_forecast_due()
    update("--season", "2026", "--week", "5")
    start = env.events()[0]
    assert start["steps"] == ["forecast"] and len(start["plan"]) == 5
    assert start["plan"][1] == "build: up to date (as of week 5)"
    assert start["command"] == "nfl playcalling update --season 2026 --week 5"  # not --auto
    assert start["via"] is None  # not from the control room
    assert re.fullmatch(r"\d{8}T\d{6}Z", start["run_id"])


def test_a_failed_step_is_exit_1_and_the_events_say_which(env) -> None:
    env.all_due()
    env.fail["build"] = ValueError("no curated plays for seasons [2026]")
    r = update("--auto", "--expect-week", "5", "--app-run", RUN_ID)
    assert r.exit_code == 1
    out = flat(r.output)
    assert "done: ftn, review; failed: build" in out
    evs = env.events()
    ends = {e["step"]: (e["status"], e["detail"]) for e in evs if e["type"] == "step_end"}
    assert ends["build"] == ("failed", "ValueError: no curated plays for seasons [2026]")
    assert ends["grade"][0] == "skipped" and ends["forecast"][0] == "skipped"
    assert ends["review"][0] == "ok"
    end = evs[-1]
    assert (end["type"], end["status"], end["exit_code"]) == ("run_end", "failed", 1)
    assert end["message"] == "done: ftn, review; failed: build"
    assert env.order == ["ftn", "build", "review"]


def test_an_unexpected_error_is_exit_1_with_a_scrubbed_message(env, monkeypatch) -> None:
    env.all_due()

    def boom(*a, **k):
        raise RuntimeError("the events disk is full: api_key=abc123 x")

    monkeypatch.setattr(P, "run_update", boom)
    r = update("--auto")
    assert r.exit_code == 1
    out = flat(r.output)
    assert "play-calling update failed: RuntimeError: the events disk is full" in out
    assert "abc123" not in out
    # the lock was let go
    assert not is_locked(lock_path(env.paths.runs))


def test_every_event_is_free_of_the_data_roots_path(env) -> None:
    env.all_due()
    env.fail["review"] = RuntimeError(f"cannot write {env.paths.root}\\live\\2026\\week04")
    update("--auto", "--expect-week", "5", "--app-run", RUN_ID)
    text = (env.events_dir / f"{RUN_ID}.jsonl").read_text(encoding="utf-8")
    assert str(env.paths.root) not in text
