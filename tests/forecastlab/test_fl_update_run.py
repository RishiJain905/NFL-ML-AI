"""The Forecast lab update, run (FL02, D158): `run_update`'s order and its failure rules,
`summary`, and `nfl forecastlab update` on a tmp tree (the exit codes, the lock, the events file).

The two model modules the update imports lazily (`availability_runs`, `curves_runs`) are fakes
installed into `sys.modules` and onto the package: they record each call and return what the real
runs return (`{"frame": DataFrame, "meta": {...}, "url": ...}`). The data root, the calendar and
the clocks are pinned; nothing reaches D:, W&B, the network or a real model.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import fl_update_fixtures as F
import polars as pl
import pytest
from fl_update_fixtures import NOW, FLTree, H
from typer.testing import CliRunner

import nflengine.forecastlab as fl_pkg
import nflengine.forecastlab.update as U
import nflengine.ops.calendar as cal
import nflengine.paths as paths_mod
import nflengine.tracking as tracking
from nflengine import cli
from nflengine.cli import app
from nflengine.forecastlab import schema as S
from nflengine.forecastlab.update import Step, StepResult
from nflengine.ops.lock import is_locked, lock_path, read_holder, run_lock
from nflengine.paths import DataPaths, DataRootError

runner = CliRunner()
RUN_ID = "20261006T140012Z-a1b2"
MINUTE = dt.timedelta(minutes=1)


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# ---- fakes -----------------------------------------------------------------------------------


class FakeModels:
    """Stands in for `availability_runs` and `curves_runs`.

    `calls`: (name, args, kwargs) of every call, `name` one of availability.predict,
    availability.grade, curves.predict, curves.grade. `fail[name]` raises that exception when the
    name is called; `during[name](models, args, kwargs)` runs inside the call first (it may
    raise); `late[name]` is the `games_after_kickoff` list the call reports.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.fail: dict[str, Exception] = {}
        self.during: dict[str, Callable[[FakeModels, tuple, dict], None]] = {}
        self.late: dict[str, list[str]] = {}
        # a grade's `summary["waiting"]` (rows of final games that still have no result)
        self.waiting: dict[str, Any] = {}
        self.rows = {
            "availability.predict": 7,
            "availability.grade": 5,
            "curves.predict": 11,
            "curves.grade": 9,
        }
        self.urls = True
        self.events: list[tuple] | None = None  # a shared timeline, when a test wants one
        self.availability = types.ModuleType("nflengine.forecastlab.availability_runs")
        self.curves = types.ModuleType("nflengine.forecastlab.curves_runs")
        for mod, family in ((self.availability, "availability"), (self.curves, "curves")):
            mod.run_predict = self._fn(f"{family}.predict")  # type: ignore[attr-defined]
            mod.run_grade = self._fn(f"{family}.grade")  # type: ignore[attr-defined]

    def _fn(self, name: str) -> Callable[..., dict[str, Any]]:
        def fn(*a: Any, **k: Any) -> dict[str, Any]:
            self.calls.append((name, a, k))
            if self.events is not None:
                self.events.append((name, k.get("view")))
            if name in self.during:
                self.during[name](self, a, k)
            if name in self.fail:
                raise self.fail[name]
            out: dict[str, Any] = {
                "frame": pl.DataFrame({"x": range(self.rows[name])}),
                "meta": {"games_after_kickoff": self.late.get(name, [])},
            }
            if name.endswith(".grade") and name in self.waiting:
                out["summary"] = {"waiting": self.waiting[name]}
            if self.urls:
                out["url"] = f"https://wandb.example/{name}/{k.get('view', 'x')}"
            return out

        return fn

    @property
    def order(self) -> list[str]:
        return [c[0] for c in self.calls]

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeModels:
        """Both the import system and the package attribute: `from nflengine.forecastlab import
        availability_runs` reads the attribute first when the real module was imported before."""
        for name, mod in (("availability_runs", self.availability), ("curves_runs", self.curves)):
            monkeypatch.setitem(sys.modules, f"nflengine.forecastlab.{name}", mod)
            monkeypatch.setattr(fl_pkg, name, mod, raising=False)
        return self


class Clock:
    """A clock that moves a minute at every read and records the reads."""

    def __init__(self, timeline: list[tuple] | None = None) -> None:
        self.reads: list[dt.datetime] = []
        self.timeline = timeline

    def __call__(self) -> dt.datetime:
        t = NOW + len(self.reads) * MINUTE
        self.reads.append(t)
        if self.timeline is not None:
            self.timeline.append(("clock",))
        return t


def step(name: str, **kw: Any) -> Step:
    labels = {
        "availability": "Availability for week 5",
        "curves": "Outcome curves for week 5",
        "grade": "Grade week 4",
    }
    kw.setdefault("week", 4 if name == "grade" else 5)
    return Step(name, labels[name], kw.pop("due", True), "why", **kw)


def all_due() -> list[Step]:
    return [
        step("availability", views=("tuesday", "saturday")),
        step("curves"),
        step("grade", parts=("availability", "curves")),
    ]


@pytest.fixture
def paths(tmp_path) -> DataPaths:
    return DataPaths(tmp_path)


@pytest.fixture
def models(monkeypatch) -> FakeModels:
    return FakeModels().install(monkeypatch)


def run(paths, steps, **kw) -> list[StepResult]:
    kw.setdefault("use_wandb", False)
    kw.setdefault("log", lambda *a, **k: None)
    return U.run_update(2026, 5, steps=steps, paths=paths, **kw)


# ---- run_update: order, views, clock ---------------------------------------------------------


def test_the_due_steps_run_in_order_with_their_views_and_parts(paths, models) -> None:
    results = run(paths, all_due(), launched_by="agent")
    assert models.order == [
        "availability.predict",
        "availability.predict",
        "curves.predict",
        "availability.grade",
        "curves.grade",
    ]
    views = [c[2]["view"] for c in models.calls if c[0] == "availability.predict"]
    assert views == ["tuesday", "saturday"]
    assert [c[1] for c in models.calls] == [(2026, 5)] * 3 + [(2026, 4)] * 2
    assert [(r.step, r.status) for r in results] == [
        ("availability", "ok"),
        ("curves", "ok"),
        ("grade", "ok"),
    ]


def test_a_single_view_is_a_single_call(paths, models) -> None:
    run(paths, [step("availability", views=("saturday",))])
    assert models.order == ["availability.predict"]
    assert models.calls[0][2]["view"] == "saturday"


def test_steps_run_in_the_fixed_order_whatever_the_order_given(paths, models) -> None:
    results = run(paths, list(reversed(all_due())))
    assert [r.step for r in results] == ["availability", "curves", "grade"]
    assert models.order[0] == "availability.predict" and models.order[-1] == "curves.grade"


def test_only_the_due_steps_run_and_are_announced(paths, models) -> None:
    seen: list[tuple[str, str]] = []
    steps = [
        step("availability", due=False, views=("tuesday",)),
        step("curves"),
        step("grade", due=False, parts=("availability",)),
    ]
    results = run(paths, steps, on_step=lambda n, label: seen.append((n, label)))
    assert models.order == ["curves.predict"]
    assert seen == [("curves", "Outcome curves for week 5")]
    assert [r.step for r in results] == ["curves"]


def test_nothing_due_runs_nothing(paths, models) -> None:
    ended: list[StepResult] = []
    assert run(paths, [], on_step_end=ended.append) == []
    assert run(paths, [step("curves", due=False), step("grade", due=False)]) == []
    assert models.calls == [] and ended == []


def test_the_callbacks_wrap_each_step_and_the_clock_is_read_right_before_each_call(
    paths, models
) -> None:
    timeline: list[tuple] = []
    models.events = timeline
    clock = Clock(timeline)
    run(
        paths,
        all_due(),
        clock=clock,
        on_step=lambda n, label: timeline.append(("start", n)),
        on_step_end=lambda r: timeline.append(("end", r.step)),
    )
    assert timeline == [
        ("start", "availability"),
        ("clock",),
        ("availability.predict", "tuesday"),
        ("clock",),
        ("availability.predict", "saturday"),
        ("end", "availability"),
        ("start", "curves"),
        ("clock",),
        ("curves.predict", None),
        ("end", "curves"),
        ("start", "grade"),  # grading takes no clock: it grades what is final
        ("availability.grade", None),
        ("curves.grade", None),
        ("end", "grade"),
    ]
    nows = [c[2]["now"] for c in models.calls if c[0].endswith(".predict")]
    assert nows == clock.reads == [NOW, NOW + MINUTE, NOW + 2 * MINUTE]
    assert all("now" not in c[2] for c in models.calls if c[0].endswith(".grade"))


def test_without_a_clock_now_is_the_real_utc_time_at_each_call(paths, models) -> None:
    before = dt.datetime.now(dt.UTC)
    run(paths, [step("availability", views=("tuesday",)), step("curves")])
    after = dt.datetime.now(dt.UTC)
    nows = [c[2]["now"] for c in models.calls]
    assert len(nows) == 2
    for t in nows:
        assert t.tzinfo is not None and t.utcoffset() == dt.timedelta(0)
        assert before <= t <= after


def test_the_common_arguments_reach_every_call(paths, models) -> None:
    lines: list[str] = []
    run(paths, all_due(), use_wandb=False, launched_by="agent", log=lines.append)
    for name, _a, k in models.calls:
        assert k["use_wandb"] is False and k["launched_by"] == "agent", name
        assert k["paths"] == paths and k["log"] == lines.append, name
        expect = {"use_wandb", "launched_by", "log", "paths"}
        expect |= {"view", "now"} if name == "availability.predict" else set()
        expect |= {"now"} if name == "curves.predict" else set()
        assert set(k) == expect, name


def test_wandb_defaults_on_and_launched_by_to_none(paths, models) -> None:
    U.run_update(2026, 5, steps=[step("curves")], paths=paths, log=lambda *a, **k: None)
    k = models.calls[0][2]
    assert k["use_wandb"] is True and k["launched_by"] is None


def test_grade_takes_the_week_of_its_step_else_the_one_before(paths, models) -> None:
    run(paths, [step("grade", week=3, parts=("curves",))])
    run(paths, [step("grade", week=None, parts=("availability",))])
    assert [(c[0], c[1]) for c in models.calls] == [
        ("curves.grade", (2026, 3)),
        ("availability.grade", (2026, 4)),  # week 5 - 1
    ]


def test_a_grade_step_grades_only_its_parts(paths, models) -> None:
    run(paths, [step("grade", parts=("curves",))])
    assert models.order == ["curves.grade"]


# ---- run_update: the grade's jobs (every week and family the plan listed) ---------------------

JOBS = ((3, "availability"), (3, "curves"), (4, "availability"), (4, "curves"))


def graded_step(**kw: Any) -> Step:
    return step("grade", jobs=JOBS, parts=("availability", "curves"), **kw)


def test_the_grade_runs_every_job_oldest_first(paths, models) -> None:
    run(paths, [graded_step(week=4)])
    assert [(c[0], c[1]) for c in models.calls] == [
        ("availability.grade", (2026, 3)),
        ("curves.grade", (2026, 3)),
        ("availability.grade", (2026, 4)),
        ("curves.grade", (2026, 4)),
    ]
    for _name, _a, k in models.calls:  # no clock for a grade; the common arguments only
        assert set(k) == {"use_wandb", "launched_by", "log", "paths"}


def test_the_jobs_win_over_the_step_week_and_parts(paths, models) -> None:
    jobs = ((2, "curves"), (4, "availability"))
    run(paths, [step("grade", jobs=jobs, parts=("availability",), week=9)])
    assert [(c[0], c[1]) for c in models.calls] == [
        ("curves.grade", (2026, 2)),
        ("availability.grade", (2026, 4)),
    ]


def test_a_step_without_jobs_still_runs_from_its_week_and_parts(paths, models) -> None:
    (g,) = run(paths, [step("grade", week=3, parts=("curves", "availability"))])
    assert [(c[0], c[1]) for c in models.calls] == [
        ("curves.grade", (2026, 3)),
        ("availability.grade", (2026, 3)),
    ]
    assert g.detail == "graded (week 3 curves: 9 rows; week 3 availability: 5 rows)"


def test_the_grade_detail_names_each_week_and_family(paths, models) -> None:
    (g,) = run(paths, [graded_step(week=4)])
    assert g.status == "ok"
    assert g.detail == (
        "graded (week 3 availability: 5 rows; week 3 curves: 9 rows; "
        "week 4 availability: 5 rows; week 4 curves: 9 rows)"
    )
    assert len(g.urls) == 4


def test_rows_still_waiting_are_named_from_the_runs_summary(paths, models) -> None:
    models.rows["availability.grade"] = 1073
    models.rows["curves.grade"] = 1381
    models.waiting["curves.grade"] = 12
    (g,) = run(paths, [step("grade", jobs=((9, "availability"), (9, "curves")), week=9)])
    assert g.detail == (
        "graded (week 9 availability: 1073 rows; week 9 curves: 1381 rows (12 still waiting))"
    )


@pytest.mark.parametrize("waiting", [0, None, "", [], {}])
def test_no_rows_waiting_adds_nothing(paths, models, waiting) -> None:
    models.waiting["curves.grade"] = waiting
    (g,) = run(paths, [step("grade", jobs=((4, "curves"),), week=4)])
    assert g.detail == "graded (week 4 curves: 9 rows)"


def test_a_grade_without_a_summary_has_nothing_waiting(paths, models) -> None:
    models.curves.run_grade = lambda *a, **k: {
        "frame": pl.DataFrame({"x": [1, 2]}),
        "summary": None,
    }
    (g,) = run(paths, [step("grade", jobs=((4, "curves"),), week=4)])
    assert (g.status, g.detail) == ("ok", "graded (week 4 curves: 2 rows)")


def week_fails(week: int, error: Exception) -> Callable[[FakeModels, tuple, dict], None]:
    def during(m: FakeModels, a: tuple, k: dict) -> None:
        if a[1] == week:
            raise error

    return during


def test_a_failed_job_names_its_week_and_family_and_the_others_still_run(paths, models) -> None:
    models.during["availability.grade"] = week_fails(3, RuntimeError("no file"))
    (g,) = run(paths, [graded_step(week=4)])
    assert g.status == "failed"
    assert models.order == ["availability.grade", "curves.grade"] * 2  # all four were tried
    assert g.detail == (
        "done: graded (week 3 curves: 9 rows; week 4 availability: 5 rows; week 4 curves: 9 rows); "
        "failed: week 3 availability: RuntimeError: no file"
    )
    assert len(g.urls) == 3 and all("week" not in u for u in g.urls)  # urls of the jobs that worked


def test_an_older_weeks_failure_does_not_stop_the_newer_week(paths, models) -> None:
    models.fail["curves.grade"] = ValueError("bad")
    (g,) = run(paths, [graded_step(week=4)])
    assert g.status == "failed"
    assert [(c[0], c[1][1]) for c in models.calls if c[0] == "availability.grade"] == [
        ("availability.grade", 3),
        ("availability.grade", 4),
    ]
    assert g.detail == (
        "done: graded (week 3 availability: 5 rows; week 4 availability: 5 rows); "
        "failed: week 3 curves: ValueError: bad; week 4 curves: ValueError: bad"
    )


def test_a_grade_keyerror_says_what_was_missing(paths, models) -> None:
    models.fail["availability.grade"] = KeyError("played")
    (g,) = run(paths, [step("grade", jobs=((4, "availability"),), week=4)])
    assert g.detail == "failed: week 4 availability: LookupError: missing 'played'"


# ---- run_update: results ---------------------------------------------------------------------


def test_results_carry_the_detail_the_urls_and_the_time(paths, models, monkeypatch) -> None:
    clock = iter([0.0, 2.34, 10.0, 10.04, 20.0, 29.96])  # a start and an end for each step
    monkeypatch.setattr(U, "time", SimpleNamespace(perf_counter=lambda: next(clock)))
    a, c, g = run(paths, all_due())
    assert (a.status, a.seconds) == ("ok", 2.3)
    assert a.detail == "tuesday view: 7 players; saturday view: 7 players"
    assert a.urls == [
        "https://wandb.example/availability.predict/tuesday",
        "https://wandb.example/availability.predict/saturday",
    ]
    assert (c.detail, c.seconds, c.urls) == (
        "11 curves", 0.0, ["https://wandb.example/curves.predict/x"],
    )  # fmt: skip
    assert g.detail == "graded (week 4 availability: 5 rows; week 4 curves: 9 rows)"
    assert g.seconds == 10.0
    assert g.urls == [
        "https://wandb.example/availability.grade/x",
        "https://wandb.example/curves.grade/x",
    ]


def test_a_run_without_a_wandb_url_has_no_urls(paths, models) -> None:
    models.urls = False
    results = run(paths, all_due())
    assert [r.urls for r in results] == [[], [], []]


def test_games_after_kickoff_are_counted_in_the_detail(paths, models) -> None:
    models.late["availability.predict"] = ["2026_05_ARI_ATL", "2026_05_MIA_MIN"]
    models.late["curves.predict"] = ["2026_05_ARI_ATL"]
    a, c, _ = run(paths, all_due())
    assert a.detail == (
        "tuesday view: 7 players (2 games after kickoff); "
        "saturday view: 7 players (2 games after kickoff)"
    )
    assert c.detail == "11 curves (1 games after kickoff)"


def test_a_missing_meta_is_no_late_games(paths, models) -> None:
    models.availability.run_predict = lambda *a, **k: {"frame": pl.DataFrame({"x": [1]})}
    (a,) = run(paths, [step("availability", views=("tuesday",))])
    assert (a.status, a.detail, a.urls) == ("ok", "tuesday view: 1 players", [])


def test_every_step_logs_one_line(paths, models) -> None:
    lines: list[str] = []
    run(paths, all_due(), log=lines.append)
    assert len(lines) == 3
    assert re.fullmatch(r"availability: ok \(\d+ s\): tuesday view: 7 players; .*", lines[0])
    assert re.fullmatch(r"curves: ok \(\d+ s\): 11 curves", lines[1])
    assert re.fullmatch(
        r"grade: ok \(\d+ s\): graded \(week 4 availability: 5 rows; .*\)", lines[2]
    )


# ---- run_update: failures --------------------------------------------------------------------


def test_a_failing_step_does_not_stop_the_others(paths, models) -> None:
    models.fail["availability.predict"] = RuntimeError("the snap counts are missing")
    seen: list[str] = []
    a, c, g = run(paths, all_due(), on_step_end=lambda r: seen.append(f"{r.step}:{r.status}"))
    assert a.status == "failed"
    assert a.detail == (  # nothing worked: no "done:" part
        "failed: tuesday view: RuntimeError: the snap counts are missing; "
        "saturday view: RuntimeError: the snap counts are missing"
    )
    assert (c.status, g.status) == ("ok", "ok")
    assert models.order == [
        "availability.predict",  # the Tuesday view raised...
        "availability.predict",  # ...and the Saturday one is still tried
        "curves.predict",
        "availability.grade",
        "curves.grade",
    ]
    assert seen == ["availability:failed", "curves:ok", "grade:ok"]
    assert a.urls == []


@pytest.mark.parametrize("name", ["curves.predict", "availability.grade", "curves.grade"])
def test_any_one_step_can_fail_alone(paths, models, name) -> None:
    models.fail[name] = ValueError("bad")
    results = run(paths, all_due())
    failed = {"curves.predict": "curves"}.get(name, "grade")
    assert {r.step: r.status for r in results} == {
        s: "failed" if s == failed else "ok" for s in ("availability", "curves", "grade")
    }


def view_fails(view: str, error: Exception) -> Callable[[FakeModels, tuple, dict], None]:
    def during(m: FakeModels, a: tuple, k: dict) -> None:
        if k["view"] == view:
            raise error

    return during


def test_the_second_view_failing_fails_the_step_and_keeps_the_first_views_result(
    paths, models
) -> None:
    models.during["availability.predict"] = view_fails("saturday", OSError("disk full"))
    a, c, _ = run(paths, all_due())
    assert a.status == "failed"
    assert a.detail == "done: tuesday view: 7 players; failed: saturday view: OSError: disk full"
    assert a.urls == ["https://wandb.example/availability.predict/tuesday"]  # the one that worked
    assert models.order[:2] == ["availability.predict"] * 2  # both were tried
    assert c.status == "ok"


def test_the_first_view_failing_does_not_stop_the_second(paths, models) -> None:
    models.during["availability.predict"] = view_fails("tuesday", ValueError("no report"))
    a, c, g = run(paths, all_due())
    assert a.status == "failed"
    assert a.detail == "done: saturday view: 7 players; failed: tuesday view: ValueError: no report"
    assert a.urls == ["https://wandb.example/availability.predict/saturday"]
    views = [c_[2]["view"] for c_ in models.calls if c_[0] == "availability.predict"]
    assert views == ["tuesday", "saturday"]
    assert (c.status, g.status) == ("ok", "ok")


def test_a_failed_view_counts_its_late_games_only_when_it_worked(paths, models) -> None:
    models.late["availability.predict"] = ["g1"]
    models.during["availability.predict"] = view_fails("tuesday", ValueError("x"))
    (a,) = run(paths, [step("availability", views=("tuesday", "saturday"))])
    assert a.detail == (
        "done: saturday view: 7 players (1 games after kickoff); "
        "failed: tuesday view: ValueError: x"
    )


def test_a_family_that_fails_to_grade_does_not_stop_the_other(paths, models) -> None:
    models.fail["availability.grade"] = RuntimeError("no week-4 file")
    (g,) = run(paths, [step("grade", parts=("availability", "curves"))])
    assert g.status == "failed"
    assert models.order == ["availability.grade", "curves.grade"]  # both were tried
    assert g.detail == (
        "done: graded (week 4 curves: 9 rows); "
        "failed: week 4 availability: RuntimeError: no week-4 file"
    )
    assert g.urls == ["https://wandb.example/curves.grade/x"]


def test_the_second_family_failing_keeps_the_first_ones_result(paths, models) -> None:
    models.fail["curves.grade"] = IndexError("week")
    (g,) = run(paths, [step("grade", parts=("availability", "curves"))])
    assert g.status == "failed"
    assert (
        g.detail
        == "done: graded (week 4 availability: 5 rows); failed: week 4 curves: IndexError: week"
    )
    assert g.urls == ["https://wandb.example/availability.grade/x"]


def test_every_part_failing_lists_each_error_without_a_done_part(paths, models) -> None:
    models.fail["availability.grade"] = RuntimeError("a")
    models.fail["curves.grade"] = RuntimeError("b")
    (g,) = run(paths, [step("grade", parts=("availability", "curves"))])
    assert (g.status, g.urls) == ("failed", [])
    assert (
        g.detail == "failed: week 4 availability: RuntimeError: a; week 4 curves: RuntimeError: b"
    )


def test_a_partial_failure_is_a_failed_step_in_the_summary(paths, models) -> None:
    models.fail["availability.grade"] = RuntimeError("a")
    results = run(paths, all_due())
    assert [r.status for r in results] == ["ok", "ok", "failed"]
    assert U.summary(results) == ("failed", "done: availability, curves; failed: grade")


def test_a_failed_part_is_logged_with_the_step(paths, models) -> None:
    models.during["availability.predict"] = view_fails("saturday", OSError("disk full"))
    lines: list[str] = []
    run(paths, [step("availability", views=("tuesday", "saturday"))], log=lines.append)
    assert len(lines) == 1
    assert re.fullmatch(
        r"availability: failed \(\d+ s\): done: tuesday view: 7 players; "
        r"failed: saturday view: OSError: disk full",
        lines[0],
    )


def test_a_failed_parts_error_is_made_relative_scrubbed_and_capped(paths, models) -> None:
    models.during["availability.predict"] = view_fails(
        "saturday",
        RuntimeError(f"cannot write {paths.root}\\x\\a.parquet api_key=abc123 " + "z" * 400),
    )
    (a,) = run(paths, [step("availability", views=("tuesday", "saturday"))])
    assert a.status == "failed" and len(a.detail) <= 300 and a.detail.endswith("…")
    assert a.detail.startswith("done: tuesday view: 7 players; failed: saturday view: RuntimeError")
    assert "abc123" not in a.detail and str(paths.root) not in a.detail
    assert "x/a.parquet" in a.detail.replace("\\", "/")


def test_a_failure_names_its_error_class_and_hides_the_data_root(paths, models) -> None:
    models.fail["curves.predict"] = RuntimeError(
        f"cannot write {paths.root}\\forecastlab\\2026\\week05\\curves.parquet "
        "(api_key=abc123 and the rest)"
    )
    _, c, _ = run(paths, all_due())
    assert c.status == "failed" and c.detail.startswith("RuntimeError: cannot write ")
    assert "abc123" not in c.detail
    assert str(paths.root) not in c.detail
    assert "forecastlab/2026/week05/curves.parquet" in c.detail.replace("\\", "/")


def test_a_long_failure_is_capped_after_the_root_is_made_relative(paths, models) -> None:
    models.fail["curves.predict"] = RuntimeError(
        "y" * 296 + str(paths.root) + "\\tail " + "z" * 200
    )
    _, c, _ = run(paths, all_due())
    assert len(c.detail) <= 300 and c.detail.endswith("…")
    assert str(paths.root)[:12] not in c.detail  # no half a path left where the cap fell


def test_a_failure_that_has_no_message_still_reads(paths, models) -> None:
    models.fail["curves.predict"] = RuntimeError()
    _, c, _ = run(paths, all_due())
    assert c.detail == "RuntimeError: "


def test_a_missing_name_is_worded_so_the_scrubber_keeps_it(paths, models) -> None:
    """`KeyError: snap_p50` reads as a secret-looking `key: value` to the scrubber and came
    out as `KeyError: ***`; the step's detail now says what was missing."""
    models.fail["curves.predict"] = KeyError("snap_p50")
    _, c, _ = run(paths, all_due())
    assert c.detail == "LookupError: missing 'snap_p50'"
    models.fail["curves.predict"] = KeyError()
    _, c, _ = run(paths, all_due())
    assert c.detail == "LookupError: missing name"


def test_the_log_line_of_a_failed_step(paths, models) -> None:
    models.fail["curves.predict"] = ValueError("bad curve")
    lines: list[str] = []
    run(paths, [step("curves")], log=lines.append)
    assert re.fullmatch(r"curves: failed \(\d+ s\): ValueError: bad curve", lines[0])


# ---- summary ---------------------------------------------------------------------------------


def res(name: str, status: str) -> StepResult:
    return StepResult(name, status, 0.1, "d")


def test_a_stepresult_has_its_own_url_list() -> None:
    a, b = res("curves", "ok"), res("grade", "ok")
    a.urls.append("u")
    assert b.urls == [] and StepResult("x", "ok", 0.0, "d").urls == []


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([], ("ok", "nothing was due")),
        ([res("availability", "ok")], ("ok", "done: availability")),
        (
            [res("availability", "ok"), res("curves", "ok"), res("grade", "ok")],
            ("ok", "done: availability, curves, grade"),
        ),
        (
            [res("availability", "failed"), res("curves", "ok"), res("grade", "ok")],
            ("failed", "done: curves, grade; failed: availability"),
        ),
        (
            [res("availability", "failed"), res("curves", "failed")],
            ("failed", "nothing done; failed: availability, curves"),
        ),
        ([res("curves", "skipped")], ("ok", "nothing done")),
    ],
)
def test_summary(results, expected) -> None:
    assert U.summary(results) == expected


# ---- the command: wiring, exit codes -----------------------------------------------------------


@dataclass
class Env:
    paths: DataPaths
    models: FakeModels
    real_run: Callable[..., Any]
    now: dt.datetime = NOW
    calendar: Any = field(default_factory=lambda: SimpleNamespace(season=2026, week=5))
    ensure_calls: list[bool] = field(default_factory=list)
    calendar_calls: int = 0
    plan_calls: int = 0
    marks: list[str] = field(default_factory=list)

    @property
    def calls(self) -> list[tuple[str, tuple, dict]]:
        return self.models.calls

    @property
    def order(self) -> list[str]:
        return self.models.order

    @property
    def events_dir(self):
        return S.season_dir(self.paths, 2026) / "events"

    def events(self) -> list[dict[str, Any]]:
        files = sorted(self.events_dir.glob("*.jsonl")) if self.events_dir.exists() else []
        assert len(files) <= 1, files
        if not files:
            return []
        return [json.loads(x) for x in files[0].read_text(encoding="utf-8").splitlines() if x]

    def all_due(self) -> Env:
        """Both Saturday and Tuesday views, the curves and last week's grade are due."""
        t = FLTree(self.paths).games().models().projections(5)
        t.availability(4, {"tuesday": NOW - 6 * 24 * H}).curves(4, NOW - 3 * 24 * H)
        t.injury_update(5, NOW - H)
        return self

    def only_curves_due(self) -> Env:
        F.only_curves_due(self.paths)
        return self


@pytest.fixture
def env(tmp_path, monkeypatch, models) -> Env:
    real_run = U.run_update
    e = Env(DataPaths(tmp_path), models, real_run)

    def ensure(create_dirs: bool = True):
        e.ensure_calls.append(create_dirs)
        return e.paths

    def load_schedules(paths):
        e.calendar_calls += 1

    monkeypatch.setattr(paths_mod, "ensure_data_root", ensure)
    monkeypatch.setattr(cal, "load_schedules", load_schedules)
    monkeypatch.setattr(cal, "plan_week", lambda sched, *a, **k: e.calendar)
    real_plan = U.plan_update

    def plan(paths, season, week, now):
        e.plan_calls += 1
        return real_plan(paths, season, week, e.now)  # the clock is pinned

    monkeypatch.setattr(U, "plan_update", plan)
    monkeypatch.setattr(
        U, "run_update", lambda *a, **k: real_run(*a, clock=lambda: e.now, **k)
    )  # the clock of the steps is pinned, too
    monkeypatch.setattr(tracking, "set_launch_via", lambda via: e.marks.append(via))
    monkeypatch.setattr(cli.console, "_width", 250)
    return e


def update(*args: str):
    return runner.invoke(app, ["forecastlab", "update", *args])


def test_the_command_is_listed_with_its_options() -> None:
    assert "update" in runner.invoke(app, ["forecastlab", "--help"]).output
    out = runner.invoke(app, ["forecastlab", "update", "--help"])
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
    assert env.calls == [] and env.plan_calls == 0


def test_exit_4_when_another_run_holds_the_lock(env) -> None:
    env.all_due()
    other = "nfl weekly run --auto --expect-week 5"
    with run_lock(lock_path(env.paths.runs), other):
        r = update("--auto", "--expect-week", "5")
        assert read_holder(lock_path(env.paths.runs))["command"] == other  # its note is intact
    assert r.exit_code == 4
    assert other in flat(r.output)
    assert env.calls == []  # not even the first step
    assert not env.events_dir.exists()  # and no events file
    assert env.plan_calls == 1  # (the plan is read-only and comes first)


def test_an_explicit_week_does_not_ask_the_calendar(env) -> None:
    env.only_curves_due()
    r = update("--season", "2026", "--week", "5")
    assert r.exit_code == 0, r.output
    assert env.calendar_calls == 0 and env.ensure_calls[0] is True
    assert env.order == ["curves.predict"]


def test_an_expected_week_makes_the_data_root_after_the_check(env) -> None:
    env.only_curves_due()
    assert update("--auto", "--expect-week", "5").exit_code == 0
    assert env.ensure_calls[:2] == [False, True]  # read-only first, then the real one
    env.ensure_calls.clear()
    assert update("--auto").exit_code == 0  # (the fake curves wrote no file: still due)
    assert env.ensure_calls[0] is True


# ---- the plan, a dry run, nothing due --------------------------------------------------------


def test_a_dry_run_lists_the_plan_and_does_nothing(env) -> None:
    env.all_due()
    before = F.tree_state(env.paths.root)
    r = update("--auto", "--dry-run")
    assert r.exit_code == 0, r.output
    lines = [flat(x) for x in r.output.strip().splitlines()]
    assert [x.split(":")[0] for x in lines] == ["due availability", "due curves", "due grade"]
    assert lines[0] == (
        "due availability: Availability for week 5 (Tuesday + Saturday view): "
        "no Tuesday view yet; the injury update has run: the Saturday view isn't made yet"
    )
    assert lines[1] == "due curves: Outcome curves for week 5: no curves for week 5 yet"
    assert lines[2] == (
        "due grade: Grade week 4: week 4's availability and curves aren't graded yet"
    )
    assert env.calls == [] and not lock_path(env.paths.runs).exists()
    assert not env.events_dir.exists()
    assert F.tree_state(env.paths.root) == before  # not a file, not a folder, not a mtime


def test_a_dry_run_marks_what_is_skipped(env) -> None:
    env.only_curves_due()
    out = flat(update("--season", "2026", "--week", "5", "--dry-run").output)
    assert "skip availability: Availability for week 5: up to date (Tuesday view)" in out
    assert "due curves: Outcome curves for week 5: no curves for week 5 yet" in out
    assert "skip grade: Grade week 4: week 4 is graded" in out
    assert "nothing is due" not in out  # a dry run only lists
    assert env.calls == []


def test_nothing_due_says_so_and_takes_no_lock(env) -> None:
    F.up_to_date(env.paths)
    r = update("--auto", "--expect-week", "5")
    assert r.exit_code == 0
    out = flat(r.output)
    assert "skip availability:" in out and "nothing is due: everything is up to date" in out
    assert env.calls == []
    assert not lock_path(env.paths.runs).exists() and not env.events_dir.exists()
    # so even while another run holds the lock this exits 0
    with run_lock(lock_path(env.paths.runs), "nfl weekly run --auto"):
        assert update("--auto", "--expect-week", "5").exit_code == 0


def test_an_empty_data_root_has_nothing_due(env) -> None:
    r = update("--auto", "--expect-week", "5")  # no model, no games, nothing made
    assert r.exit_code == 0 and "nothing is due" in flat(r.output)
    assert "no promoted availability model yet" in flat(r.output)


def test_labels_and_reasons_are_not_read_as_markup(env) -> None:
    env.only_curves_due()
    S.production_file(env.paths, "curves").unlink()
    out = flat(update("--season", "2026", "--week", "5", "--dry-run").output)
    assert "no promoted curves model yet (`nfl forecastlab backtest`)" in out


# ---- a run -----------------------------------------------------------------------------------


def test_a_run_does_the_due_steps_in_order_and_exits_0(env) -> None:
    env.all_due()
    r = update("--auto", "--expect-week", "5", "--launched-by", "agent")
    assert r.exit_code == 0, r.output
    assert env.order == [
        "availability.predict",
        "availability.predict",
        "curves.predict",
        "availability.grade",
        "curves.grade",
    ]
    out = flat(r.output)
    assert re.search(r"done: availability, curves, grade \(\d+ s\)", out)
    for name in ("availability", "curves", "grade"):
        assert f"{name}: ok (" in out
    # options reach the steps
    for name, _a, k in env.calls:
        assert k["use_wandb"] is True and k["launched_by"] == "agent", name
        assert k["paths"] == env.paths, name
    assert [(c[0], c[1]) for c in env.calls[:3]] == [
        ("availability.predict", (2026, 5)),
        ("availability.predict", (2026, 5)),
        ("curves.predict", (2026, 5)),
    ]
    assert [c[2]["view"] for c in env.calls[:2]] == ["tuesday", "saturday"]
    assert env.calls[3][1] == (2026, 4)


def test_the_steps_get_the_clock_of_the_moment_not_the_plans(env, monkeypatch) -> None:
    """Without a pinned clock `now` is read at each step (an aware UTC time)."""
    env.only_curves_due()
    monkeypatch.setattr(U, "run_update", env.real_run)
    before = dt.datetime.now(dt.UTC)
    assert update("--auto").exit_code == 0
    t = env.calls[0][2]["now"]
    assert t.tzinfo is not None and before <= t <= dt.datetime.now(dt.UTC)


def test_no_wandb_reaches_every_step(env) -> None:
    env.all_due()
    assert update("--auto", "--no-wandb").exit_code == 0
    assert len(env.calls) == 5
    for name, _, k in env.calls:
        assert k["use_wandb"] is False, name


def test_only_the_due_steps_run(env) -> None:
    env.only_curves_due()
    r = update("--auto")
    assert r.exit_code == 0 and env.order == ["curves.predict"]
    assert "done: curves" in flat(r.output)


def test_a_failed_step_is_exit_1_and_the_others_still_run(env) -> None:
    env.all_due()
    env.models.fail["availability.predict"] = ValueError("no snap counts for 2026")
    r = update("--auto", "--expect-week", "5", "--app-run", RUN_ID)
    assert r.exit_code == 1
    assert "done: curves, grade; failed: availability" in flat(r.output)
    assert env.order == [
        "availability.predict", "availability.predict",  # (the Saturday view is still tried)
        "curves.predict", "availability.grade", "curves.grade",
    ]  # fmt: skip
    evs = env.events()
    ends = {e["step"]: (e["status"], e["detail"]) for e in evs if e["type"] == "step_end"}
    assert ends["availability"] == (
        "failed",
        "failed: tuesday view: ValueError: no snap counts for 2026; "
        "saturday view: ValueError: no snap counts for 2026",
    )
    assert ends["curves"][0] == "ok" and ends["grade"][0] == "ok"
    end = evs[-1]
    assert (end["type"], end["status"], end["exit_code"]) == ("run_end", "failed", 1)
    assert end["message"] == "done: curves, grade; failed: availability"
    assert end["failed_step"] == "availability"


def test_one_failed_part_of_a_step_is_exit_1_and_the_events_keep_what_worked(env) -> None:
    env.all_due()
    env.models.during["availability.predict"] = view_fails("saturday", OSError("disk full"))
    env.models.fail["curves.grade"] = IndexError("week")
    r = update("--auto", "--expect-week", "5", "--app-run", RUN_ID)
    assert r.exit_code == 1
    assert "done: curves; failed: availability, grade" in flat(r.output)
    evs = env.events()
    ends = {e["step"]: (e["status"], e["detail"]) for e in evs if e["type"] == "step_end"}
    assert ends["availability"] == (
        "failed", "done: tuesday view: 7 players; failed: saturday view: OSError: disk full",
    )  # fmt: skip
    assert ends["curves"] == ("ok", "11 curves")
    assert ends["grade"] == (
        "failed",
        "done: graded (week 4 availability: 5 rows); failed: week 4 curves: IndexError: week",
    )
    assert evs[-1]["failed_step"] == "availability"  # the first one that failed


def test_every_step_failing_is_still_one_exit_1_with_the_first_named(env) -> None:
    env.all_due()
    for name in ("availability.predict", "curves.predict", "availability.grade"):
        env.models.fail[name] = RuntimeError("x")
    r = update("--auto")
    assert r.exit_code == 1 and "nothing done; failed: availability, curves, grade" in flat(
        r.output
    )
    assert env.events()[-1]["failed_step"] == "availability"


def test_an_unexpected_error_is_exit_1_with_a_scrubbed_message(env, monkeypatch) -> None:
    env.all_due()

    def boom(*a, **k):
        raise RuntimeError("the events disk is full: api_key=abc123 x")

    monkeypatch.setattr(U, "run_update", boom)
    r = update("--auto")
    assert r.exit_code == 1
    out = flat(r.output)
    assert "forecast lab update failed: RuntimeError: the events disk is full" in out
    assert "abc123" not in out
    assert not is_locked(lock_path(env.paths.runs))  # the lock was let go


def test_the_lock_is_held_during_the_steps_and_freed_after(env) -> None:
    env.all_due()
    seen: dict[str, Any] = {}

    def during(m: FakeModels, a: tuple, k: dict) -> None:
        path = lock_path(env.paths.runs)
        seen["held"] = is_locked(path)
        seen["note"] = read_holder(path)

    env.models.during["curves.predict"] = during
    assert update("--auto", "--expect-week", "5", "--app-run", RUN_ID).exit_code == 0
    assert seen["held"] is True
    # the control room finds the run by this command and run id
    assert seen["note"]["command"] == "nfl forecastlab update --season 2026 --week 5"
    assert seen["note"]["run_id"] == RUN_ID
    assert not is_locked(lock_path(env.paths.runs))
    assert not read_holder(lock_path(env.paths.runs))  # the note is emptied


def test_the_lock_note_has_a_fresh_run_id_without_app_run(env) -> None:
    env.all_due()
    seen: dict[str, Any] = {}
    env.models.during["curves.predict"] = lambda m, a, k: seen.update(
        note=read_holder(lock_path(env.paths.runs))
    )
    assert update("--season", "2026", "--week", "5").exit_code == 0
    assert seen["note"]["command"] == "nfl forecastlab update --season 2026 --week 5"
    assert re.fullmatch(r"\d{8}T\d{6}Z", seen["note"]["run_id"])


# ---- the events file -------------------------------------------------------------------------


def test_events_of_a_successful_run(env) -> None:
    env.all_due()
    env.models.late["curves.predict"] = ["2026_05_ARI_ATL"]
    assert update("--auto", "--expect-week", "5", "--app-run", RUN_ID,
                  "--launched-by", "rishi").exit_code == 0  # fmt: skip
    assert env.marks == ["control-room"]  # the process is marked as launched by the app
    assert [f.name for f in env.events_dir.glob("*")] == [f"{RUN_ID}.jsonl"]
    assert env.events_dir == env.paths.root / "forecastlab" / "2026" / "events"
    evs = env.events()
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    assert all(e["run_id"] == RUN_ID for e in evs)
    start = evs[0]
    assert start["type"] == "run_start"
    assert (start["kind"], start["season"], start["week"]) == ("forecastlab_update", 2026, 5)
    assert start["command"] == "nfl forecastlab update --auto"
    assert start["steps"] == ["availability", "curves", "grade"]
    assert (start["launched_by"], start["via"]) == ("rishi", "control-room")
    assert [p.split(":")[0] for p in start["plan"]] == list(U.STEPS)
    assert start["plan"][0].startswith("availability: no Tuesday view yet; the injury update")
    steps = [(e["type"], e["step"]) for e in evs if e["type"] in ("step_start", "step_end")]
    assert steps == [(t, n) for n in U.STEPS for t in ("step_start", "step_end")]
    ends = {e["step"]: e for e in evs if e["type"] == "step_end"}
    assert all(e["status"] == "ok" for e in ends.values())
    assert ends["availability"]["detail"] == "tuesday view: 7 players; saturday view: 7 players"
    assert ends["curves"]["detail"] == "11 curves (1 games after kickoff)"
    assert ends["grade"]["detail"] == "graded (week 4 availability: 5 rows; week 4 curves: 9 rows)"
    logs = [e["text"] for e in evs if e["type"] == "log"]  # the console lines are log events
    assert any(x.startswith("curves: ok (") for x in logs)
    end = evs[-1]
    assert end["type"] == "run_end"
    assert (end["status"], end["exit_code"]) == ("ok", 0)
    assert end["message"] == "done: availability, curves, grade"
    assert end["published"] is False and end["failed_step"] is None


def test_the_plan_in_run_start_lists_the_skipped_steps_too(env) -> None:
    env.only_curves_due()
    assert update("--season", "2026", "--week", "5").exit_code == 0
    start = env.events()[0]
    assert start["steps"] == ["curves"] and len(start["plan"]) == 3
    assert start["plan"][0] == "availability: up to date (Tuesday view)"
    assert start["command"] == "nfl forecastlab update --season 2026 --week 5"  # not --auto
    assert start["via"] is None  # not from the control room
    assert re.fullmatch(r"\d{8}T\d{6}Z", start["run_id"])


def test_a_step_that_is_not_due_leaves_no_step_events(env) -> None:
    env.only_curves_due()
    update("--auto")
    evs = env.events()
    assert [(e["type"], e["step"]) for e in evs if e["type"].startswith("step_")] == [
        ("step_start", "curves"), ("step_end", "curves"),
    ]  # fmt: skip


def test_every_event_is_free_of_the_data_roots_path(env) -> None:
    env.all_due()
    env.models.fail["curves.predict"] = RuntimeError(f"cannot write {env.paths.root}\\x\\week05")
    update("--auto", "--expect-week", "5", "--app-run", RUN_ID)
    text = (env.events_dir / f"{RUN_ID}.jsonl").read_text(encoding="utf-8")
    assert str(env.paths.root) not in text
    assert "cannot write" in text  # (the failure is there, minus the root)


def test_a_second_update_writes_its_own_events_file(env) -> None:
    env.only_curves_due()
    assert update("--auto", "--app-run", RUN_ID).exit_code == 0
    other = "20261006T150000Z-c3d4"
    assert update("--auto", "--app-run", other).exit_code == 0
    assert sorted(f.name for f in env.events_dir.glob("*.jsonl")) == [
        f"{RUN_ID}.jsonl", f"{other}.jsonl",
    ]  # fmt: skip
