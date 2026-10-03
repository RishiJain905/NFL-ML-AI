"""`nfl weekly run`: named steps, state file, resume with --from-step (P04)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nflengine.cli import app
from nflengine.weekly import STEPS, StepFailed, WeeklyOptions, run_weekly


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
    assert calls == ["ratings", "game", "digest"]
    assert json.loads(sf.read_text())["steps"]["ratings"]["status"] == "ok"


def test_unexpected_errors_become_step_failures(tmp_path: Path) -> None:
    funcs = fake_steps([])
    funcs["curate"] = lambda o, log: 1 / 0
    with pytest.raises(StepFailed) as e:
        run_weekly(WeeklyOptions(2026, 5), funcs=funcs, state_file=tmp_path / "s.json")
    assert e.value.step == "curate" and "ZeroDivisionError" in e.value.detail


def test_unknown_step_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        run_weekly(WeeklyOptions(2026, 5), from_step="graph", state_file=tmp_path / "s.json")


def test_cli_weekly_and_digest_wiring(monkeypatch) -> None:
    runner = CliRunner()
    assert "--from-step" in runner.invoke(app, ["weekly", "run", "--help"]).output
    res = runner.invoke(app, ["digest", "--season", "2025"])
    assert res.exit_code == 2 and "exactly one of --week or --weeks" in res.output

    import nflengine.weekly as weekly

    def fail(opts, from_step=None, log=print):
        raise StepFailed("ready", "2026 week 4: NOT READY", exit_code=3)

    monkeypatch.setattr(weekly, "run_weekly", fail)
    monkeypatch.setattr(weekly, "state_path", lambda s, w: Path("state.json"))
    res = runner.invoke(app, ["weekly", "run", "--season", "2026", "--week", "5"])
    assert res.exit_code == 3 and "--from-step ready" in res.output


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
