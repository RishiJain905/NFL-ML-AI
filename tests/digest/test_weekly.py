"""`nfl weekly run`: named steps, state file, resume with --from-step (P04)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nflengine.cli import app
from nflengine.weekly import STEPS, StepDegraded, StepFailed, WeeklyOptions, run_weekly


def fake_steps(calls: list[str], fail_at: str | None = None, code: int = 1):
    def make(name: str):
        def step(opts, log):
            calls.append(name)
            if name == fail_at:
                raise StepFailed(name, "boom", exit_code=code)
            return f"{name} ok"

        return step

    return {n: make(n) for n in STEPS}


def test_runs_every_step_in_order(tmp_path: Path) -> None:
    calls: list[str] = []
    state = run_weekly(
        WeeklyOptions(2026, 5), funcs=fake_steps(calls), state_file=tmp_path / "s.json"
    )
    assert calls == list(STEPS)
    assert all(state["steps"][s]["status"] == "ok" for s in STEPS)


def test_failure_stops_and_resume_skips_done_steps(tmp_path: Path) -> None:
    sf = tmp_path / "s.json"
    calls: list[str] = []
    with pytest.raises(StepFailed) as e:
        run_weekly(WeeklyOptions(2026, 5), funcs=fake_steps(calls, "ratings"), state_file=sf)
    assert e.value.step == "ratings" and calls == ["ingest", "ready", "curate", "ratings"]
    saved = json.loads(sf.read_text())
    assert saved["steps"]["ratings"]["status"] == "failed"

    calls.clear()
    run_weekly(WeeklyOptions(2026, 5), from_step="ratings", funcs=fake_steps(calls), state_file=sf)
    assert calls == ["ratings", "game", "graph", "player", "digest"]
    assert json.loads(sf.read_text())["steps"]["ratings"]["status"] == "ok"


def test_unexpected_errors_become_step_failures(tmp_path: Path) -> None:
    funcs = fake_steps([])
    funcs["curate"] = lambda o, log: 1 / 0
    with pytest.raises(StepFailed) as e:
        run_weekly(WeeklyOptions(2026, 5), funcs=funcs, state_file=tmp_path / "s.json")
    assert e.value.step == "curate" and "ZeroDivisionError" in e.value.detail


def test_unknown_step_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        run_weekly(WeeklyOptions(2026, 5), from_step="players", state_file=tmp_path / "s.json")


def test_graph_step_is_fail_soft(tmp_path: Path) -> None:
    """Neo4j down: the graph step is recorded as degraded and the digest still runs (P05)."""
    calls: list[str] = []
    funcs = fake_steps(calls)

    def graph(opts, log):
        calls.append("graph")
        raise StepDegraded("graph", "ServiceUnavailable: Neo4j unreachable")

    funcs["graph"] = graph
    sf = tmp_path / "s.json"
    state = run_weekly(WeeklyOptions(2026, 5), funcs=funcs, state_file=sf)
    assert calls == list(STEPS)
    assert state["steps"]["graph"]["status"] == "degraded"
    assert "Neo4j unreachable" in state["steps"]["graph"]["detail"]
    assert state["steps"]["digest"]["status"] == "ok"
    assert STEPS.index("game") < STEPS.index("graph") < STEPS.index("player")
    assert STEPS.index("player") < STEPS.index("digest")


def test_cli_weekly_and_digest_wiring(monkeypatch) -> None:
    runner = CliRunner()
    assert "--from-step" in runner.invoke(app, ["weekly", "run", "--help"]).output
    res = runner.invoke(app, ["digest", "--season", "2025"])
    assert res.exit_code == 2 and "exactly one of --week or --weeks" in res.output

    import nflengine.weekly as weekly

    # P07: the CLI goes through run_pipeline. Stub it: never let a CLI test reach the real
    # data root (an earlier version of this test wrote run records to D: and W&B).
    seen = {}

    def outcome(status, code):
        def fake(season, week, **kw):
            seen.update(kw, season=season, week=week)
            return weekly.PipelineOutcome(status, code, 2026, 5, "2026 week 4: NOT READY")

        return fake

    monkeypatch.setattr(weekly, "run_pipeline", outcome("not_ready", 3))
    res = runner.invoke(app, ["weekly", "run", "--season", "2026", "--week", "5"])
    assert res.exit_code == 3 and "run it again later" in res.output
    assert seen["season"] == 2026 and not seen["auto"] and seen["from_step"] is None
    monkeypatch.setattr(weekly, "run_pipeline", outcome("failed", 1))
    res = runner.invoke(app, ["weekly", "run", "--season", "2026", "--week", "5"])
    assert res.exit_code == 1 and "--from-step" in res.output


def test_cli_digest_passes_llm_override(monkeypatch) -> None:
    import nflengine.digest.run as run_mod

    seen = {}

    class Res:
        class synthesis:  # noqa: N801
            fallback_notes: list = []
            regenerated = False

            class final:  # noqa: N801
                passed, failed, warnings = True, [], []

        report_path = Path("x.md")

    def fake(season, week, **kw):
        seen.update(kw, season=season, week=week)
        return Res()

    monkeypatch.setattr(run_mod, "run_digest", fake)
    res = CliRunner().invoke(
        app, ["digest", "--season", "2025", "--week", "8", "--backtest", "--llm", "placeholder"]
    )
    assert res.exit_code == 0, res.output
    assert seen["provider"] == "placeholder" and seen["mode"] == "backtest"


def _curate_over(monkeypatch, pbp: set[str]) -> dict:
    """The weekly curate step with the build faked and the real completeness check over a
    schedule where week 8's Thursday game has finished: what stopped 2026 week 8's late run
    (2026-10-29: CAR at GB final, its play-by-play not published yet; D155)."""
    import polars as pl

    import nflengine.curate.build as build
    import nflengine.curate.quality as quality

    sched = pl.DataFrame(
        {
            "game_id": ["g_w7", "g_thu", "g_sun"],
            "season": [2026] * 3,
            "week": [7, 8, 8],
            "game_type": ["REG"] * 3,
            "completed": [True, True, False],
        }
    )
    seen: dict = {"kw": []}

    def checks(**kw):
        seen["kw"].append(kw)
        return [quality.check_completeness(sched, pbp, 2010, **kw)]

    monkeypatch.setattr(build, "build_all", lambda log=print: ["games", "plays"])
    monkeypatch.setattr(quality, "run_quality_checks", checks)
    return seen


def test_curate_doesnt_require_this_weeks_finished_games_in_pbp(monkeypatch) -> None:
    from nflengine.weekly import _curate

    seen = _curate_over(monkeypatch, {"g_w7"})
    detail = _curate(WeeklyOptions(2026, 8), print)
    assert seen["kw"] == [{"pbp_pending_week": (2026, 8)}]
    assert detail.startswith("2 tables; quality checks pass")
    assert "1 of 2026 week 8 not required yet" in detail and "g_thu" in detail


def test_curate_still_requires_last_weeks_games_in_pbp(monkeypatch) -> None:
    from nflengine.weekly import _curate

    _curate_over(monkeypatch, {"g_thu"})
    with pytest.raises(StepFailed) as e:
        _curate(WeeklyOptions(2026, 8), print)
    assert e.value.step == "curate" and "completed games in play-by-play" in e.value.detail
