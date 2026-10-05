"""`nfl season weeks|review` and `nfl weekly rehearse` (P10): the CLI wiring, with the
module functions stubbed (no data drive, no W&B)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import nflengine.ops.rehearsal as R
import nflengine.ops.season_review as SR
from nflengine.cli import app
from nflengine.paths import DataPaths


def test_season_weeks_prints_one_line_per_week(monkeypatch) -> None:
    seen: dict = {}

    def fake(season, **kw):
        seen.update(kw, season=season)
        return ["week 05: published ✓ on time (58.2 h before kickoff), checks passed first time"]

    monkeypatch.setattr(SR, "week_lines", fake)
    res = CliRunner().invoke(app, ["season", "weeks", "--season", "2026"])
    assert res.exit_code == 0, res.output
    assert "week 05: published" in res.output
    assert seen == {"season": 2026, "run_root": None, "kind": "main"}


def test_season_weeks_reads_simulations(monkeypatch, tmp_path) -> None:
    seen: dict = {}
    monkeypatch.setattr(SR, "week_lines", lambda season, **kw: seen.update(kw) or [])
    monkeypatch.setattr("nflengine.paths.ensure_data_root", lambda *a, **k: DataPaths(tmp_path))
    res = CliRunner().invoke(app, ["season", "weeks", "--season", "2025", "--simulations"])
    assert res.exit_code == 0 and "no weekly runs recorded for 2025" in res.output
    assert seen["kind"] == "simulation"
    assert seen["run_root"] == tmp_path / "runs" / "digest-backtests"


def test_season_review_writes_the_file(monkeypatch, tmp_path) -> None:
    seen: dict = {}

    def fake(season, out=None, **kw):
        seen.update(kw, season=season, out=out)
        return out or tmp_path / "season-review.md"

    monkeypatch.setattr(SR, "write_review", fake)
    out = tmp_path / "2026-season-review.md"
    res = CliRunner().invoke(app, ["season", "review", "--season", "2026", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert seen["out"] == out and seen["kind"] == "main" and seen["through_week"] is None


def test_weekly_rehearse_runs_each_week_and_exits_1_on_failure(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake(season, week, **kw):
        calls.append((season, week, kw["fresh"], kw["llm"], tuple(kw["steps"])))
        status = "failed" if week == 21 else "ok"
        return R.RehearsalResult(
            season, week, None, status, Path("x"), error="game: boom" if week == 21 else None
        )

    monkeypatch.setattr(R, "run_rehearsal", fake)
    res = CliRunner().invoke(
        app, ["weekly", "rehearse", "--season", "2025", "--weeks", "19-22", "--fresh"]
    )
    assert res.exit_code == 1, res.output
    assert [c[1] for c in calls] == [19, 20, 21, 22]
    assert [c[2] for c in calls] == [True, False, False, False]  # wiped once, then chained
    assert calls[0][3] == "placeholder" and calls[0][4] == ("ready", "game", "player", "digest")
    assert "rehearsal 2025 week 21: failed (game: boom)" in res.output

    calls.clear()
    res = CliRunner().invoke(
        app, ["weekly", "rehearse", "--season", "2026", "--week", "1", "--steps", "game,player"]
    )
    assert res.exit_code == 0 and calls[0][4] == ("game", "player")
    res = CliRunner().invoke(
        app, ["weekly", "rehearse", "--season", "2025", "--weeks", "19-20", "--at", "2026-01-06"]
    )
    assert res.exit_code == 2  # --at pins one week only
