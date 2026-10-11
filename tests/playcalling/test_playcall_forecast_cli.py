"""`nfl playcalling backtest | forecast | grade` (PC02): the CLI wiring, with the runs stubbed.

The commands import `run_backtest` / `run_forecast` / `run_grade` when they run, so the stubs go
on `nflengine.playcalling.forecast_runs`: nothing here reads or writes D: or logs to W&B.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import nflengine.playcalling.forecast as F
import nflengine.playcalling.forecast_runs as fr
from nflengine import cli
from nflengine.cli import app

runner = CliRunner()


def flat(text: str) -> str:
    """One line, single spaces, table borders removed."""
    return re.sub(r"\s+", " ", re.sub(r"[│┃┆|]", " ", text))


class Stub:
    def __init__(self, result) -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self.result = result
        self.raises: dict[int, Exception] = {}  # call number (1-based) -> the error it raises

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        err = self.raises.get(len(self.calls))
        if err is not None:
            raise err
        return self.result


def mae(model, b2, b3, b4, b1) -> dict:
    return {
        k: {"mae": v}
        for k, v in zip(("model", "b2", "b3", "b4", "b1"), (model, b2, b3, b4, b1), strict=True)
    }


def backtest_result(**over) -> dict:
    results = [
        SimpleNamespace(
            target=F.get_target("blitz_rate"), seasons=[2022, 2023, 2024, 2025],
            pooled=mae(0.0731, 0.0759, 0.0742, 0.0735, 0.0801), coverage=0.79,
            verdict={"wins_vs_b2": 3, "seasons": 4, "need": 3, "ships": True},
        ),
        SimpleNamespace(
            target=F.get_target("rushers_avg"), seasons=[2022, 2023, 2024, 2025],
            pooled=mae(0.512, 0.530, 0.520, 0.515, 0.560), coverage=0.81,
            verdict={"wins_vs_b2": 2, "seasons": 4, "need": 3, "ships": False},
        ),
    ]  # fmt: skip
    out = {"results": results, "shipped": ["blitz_rate"], "folder": None, "url": None,
           "version": None, "proe_per_play": {}}  # fmt: skip
    out.update(over)
    return out


def grade_result(graded: int = 8, **over) -> dict:
    shipped = {"graded": 0}
    if graded:
        shipped = {"graded": graded, "mae_model": 0.04, "mae_b2": 0.035, "beat_b2": 3,
                   "in_range": 0.75}  # fmt: skip
    summary = {"graded": graded, "rows": 24, "targets": {}, "shipped": shipped}
    out = {"frame": None, "summary": summary, "counts": {"graded": graded}, "path": "x",
           "url": None}  # fmt: skip
    out.update(over)
    return out


@pytest.fixture
def backtest(monkeypatch) -> Stub:
    s = Stub(backtest_result())
    monkeypatch.setattr(fr, "run_backtest", s)
    monkeypatch.setattr(cli.console, "_width", 250)
    return s


@pytest.fixture
def forecast(monkeypatch) -> Stub:
    s = Stub({"frame": None, "meta": {}, "folder": "x", "url": None})
    monkeypatch.setattr(fr, "run_forecast", s)
    monkeypatch.setattr(cli.console, "_width", 250)
    return s


@pytest.fixture
def grade(monkeypatch) -> Stub:
    s = Stub(grade_result())
    monkeypatch.setattr(fr, "run_grade", s)
    monkeypatch.setattr(cli.console, "_width", 250)
    return s


def run(cmd: str, *args: str):
    return runner.invoke(app, ["playcalling", cmd, *args])


# --- wiring -------------------------------------------------------------------------------------
def test_the_group_lists_the_three_commands_and_their_options() -> None:
    group = runner.invoke(app, ["playcalling", "--help"])
    assert group.exit_code == 0
    for cmd in ("build", "backtest", "forecast", "grade"):
        assert cmd in group.output
    wanted = {
        "backtest": (
            "--seasons",
            "--tune",
            "--targets",
            "--promote",
            "--no-wandb",
            "--smoke",
            "--launched-by",
        ),  # fmt: skip
        "forecast": (
            "--season",
            "--week",
            "--weeks",
            "--version",
            "--no-wandb",
            "--smoke",
            "--launched-by",
        ),  # fmt: skip
        "grade": ("--season", "--week", "--weeks", "--no-wandb", "--smoke", "--launched-by"),
    }
    for cmd, opts in wanted.items():
        out = runner.invoke(app, ["playcalling", cmd, "--help"])
        assert out.exit_code == 0, cmd
        for opt in opts:
            assert opt in out.output, (cmd, opt)


# --- backtest -----------------------------------------------------------------------------------
def test_backtest_defaults_are_passed_through(backtest) -> None:
    r = run("backtest")
    assert r.exit_code == 0, r.output
    ((args, kwargs),) = backtest.calls
    assert args == (None, None, None)  # the settings supply the seasons and every target
    assert kwargs == {"promote": False, "use_wandb": True, "launched_by": None,
                      "smoke": False, "log": cli.console.print}  # fmt: skip


def test_backtest_options_are_parsed(backtest) -> None:
    r = run(
        "backtest",
        "--seasons",
        "2019-2025",
        "--tune",
        "2019-2023",
        "--targets",
        "blitz_rate, proe ,",
        "--promote",
        "--no-wandb",
        "--smoke",
        "--launched-by",
        "agent",
    )
    assert r.exit_code == 0, r.output
    ((args, kwargs),) = backtest.calls
    assert args == (list(range(2019, 2026)), list(range(2019, 2024)), ["blitz_rate", "proe"])
    assert kwargs["promote"] is True and kwargs["use_wandb"] is False and kwargs["smoke"] is True
    assert kwargs["launched_by"] == "agent"
    run("backtest", "--seasons", "2020,2022")
    assert backtest.calls[1][0][0] == [2020, 2022]


def test_backtest_prints_a_table_in_points(backtest) -> None:
    out = flat(run("backtest").output)
    assert "play-calling backtest (game-level MAE)" in out
    for col in ("target", "seasons", "forecast", "season to date", "shrunk", "+ opponent",
                "league", "wins", "80% range", "verdict"):  # fmt: skip
        assert col in out, col
    # shares in percentage points: 0.0731 -> 7.31; rushers stay in rushers
    assert re.search(
        r"blitz_rate 2022-2025 7\.31 7\.59 7\.42 7\.35 8\.01 3/4 \(need 3\) 79% ships", out
    )
    assert re.search(
        r"rushers_avg 2022-2025 0\.51 0\.53 0\.52 0\.52 0\.56 2/4 \(need 3\) 81% descriptive", out
    )
    assert "MAE in points (percentage points; rushers in rushers)." in out
    assert "production:" not in out and "W&B:" not in out


def test_backtest_prints_the_promotion_and_the_run_url(backtest) -> None:
    backtest.result = backtest_result(
        version="2019-2025_20261011T000000Z", url="https://wandb.ai/x/nfl-analytics-engine/runs/abc"
    )
    out = flat(run("backtest", "--promote").output)
    assert "production: playcall-model 2019-2025_20261011T000000Z" in out
    assert "W&B: https://wandb.ai/x/nfl-analytics-engine/runs/abc" in out


@pytest.mark.parametrize(
    "err", [FileNotFoundError("no curated plays [2019]"), ValueError("tune [2019] outside"),
            KeyError("unknown play-calling target 'nope'"),
            RuntimeError("no target ships by the rule (D130): not promoted; ask Rishi")],
)  # fmt: skip
def test_backtest_errors_exit_1_with_the_message(backtest, err) -> None:
    backtest.raises = {1: err}
    r = run("backtest")
    assert r.exit_code == 1
    out = flat(r.output)
    assert str(err).strip("'\"") in out or str(err.args[0]) in out  # brackets are not markup
    assert "play-calling backtest (game-level MAE)" not in out  # no table after an error


def test_backtest_does_not_swallow_programming_errors(backtest) -> None:
    backtest.raises = {1: TypeError("a bug")}
    r = run("backtest")
    assert r.exit_code == 1 and isinstance(r.exception, TypeError)


# --- forecast -----------------------------------------------------------------------------------
def test_forecast_one_week(forecast) -> None:
    r = run("forecast", "--season", "2026", "--week", "5")
    assert r.exit_code == 0, r.output
    ((args, kwargs),) = forecast.calls
    assert args == (2026, 5)
    assert kwargs == {"use_wandb": True, "launched_by": None, "smoke": False, "version": None,
                      "log": cli.console.print}  # fmt: skip


def test_forecast_options_and_a_range_of_weeks(forecast) -> None:
    r = run("forecast", "--season", "2025", "--weeks", "3-5", "--version", "v1", "--no-wandb",
            "--smoke", "--launched-by", "agent")  # fmt: skip
    assert r.exit_code == 0, r.output
    assert [c[0] for c in forecast.calls] == [(2025, 3), (2025, 4), (2025, 5)]  # in order
    for _, kwargs in forecast.calls:
        assert kwargs["version"] == "v1" and kwargs["use_wandb"] is False
        assert kwargs["smoke"] is True and kwargs["launched_by"] == "agent"
    forecast.calls.clear()
    assert run("forecast", "--season", "2025", "--weeks", "2,7").exit_code == 0
    assert [c[0][1] for c in forecast.calls] == [2, 7]


def test_forecast_needs_exactly_one_of_week_and_weeks(forecast) -> None:
    both = run("forecast", "--season", "2025", "--week", "3", "--weeks", "1-4")
    assert both.exit_code == 2 and "give --week or --weeks, not both" in flat(both.output)
    neither = run("forecast", "--season", "2025")
    assert neither.exit_code == 2 and "give --week N or --weeks A-B" in flat(neither.output)
    assert run("forecast", "--week", "3").exit_code == 2  # --season is required
    assert forecast.calls == []


def test_forecast_prints_the_run_url_per_week(forecast) -> None:
    forecast.result = {
        "frame": None,
        "meta": {},
        "folder": "x",
        "url": "https://wandb.ai/u/p/runs/r1",
    }
    out = flat(run("forecast", "--season", "2025", "--weeks", "1-2").output)
    assert out.count("W&B: https://wandb.ai/u/p/runs/r1") == 2


@pytest.mark.parametrize(
    "err", [FileNotFoundError("no promoted playcall-model yet: run [x]"),
            ValueError("2025 week 20 isn't in the play-calling tables"), KeyError("kind")],
)  # fmt: skip
def test_forecast_errors_exit_1_and_stop_the_range(forecast, err) -> None:
    forecast.raises = {2: err}
    r = run("forecast", "--season", "2025", "--weeks", "1-4")
    assert r.exit_code == 1 and str(err.args[0]) in flat(r.output)
    assert len(forecast.calls) == 2  # week 1 ran, week 2 failed, weeks 3-4 were not tried


def test_forecast_does_not_swallow_programming_errors(forecast) -> None:
    forecast.raises = {1: RuntimeError("disk on fire")}
    r = run("forecast", "--season", "2025", "--week", "3")
    assert r.exit_code == 1 and isinstance(r.exception, RuntimeError)
    assert "disk on fire" not in flat(r.output)


# --- grade --------------------------------------------------------------------------------------
def test_grade_one_week_and_its_summary_line(grade) -> None:
    r = run("grade", "--season", "2026", "--week", "5")
    assert r.exit_code == 0, r.output
    ((args, kwargs),) = grade.calls
    assert args == (2026, 5)
    assert kwargs == {"use_wandb": True, "launched_by": None, "smoke": False,
                      "log": cli.console.print}  # fmt: skip
    out = flat(r.output)
    assert (
        "week 5, shipped targets: 8 rows, forecast MAE 4.00 vs season-to-date 3.50 points "
        "(shares and PROE); closer than season-to-date in 3; 80% range held 75%"
    ) in out
    # only rushers graded: no MAE in points to show
    grade.result = grade_result()
    grade.result["summary"]["shipped"] = {"graded": 2, "mae_model": None, "mae_b2": None,
                                          "beat_b2": 1, "in_range": 0.5}  # fmt: skip
    out2 = flat(run("grade", "--season", "2026", "--week", "6").output)
    assert (
        "week 6, shipped targets: 2 rows, closer than season-to-date in 1; 80% range held 50%"
        in out2
    )
    assert "forecast MAE" not in out2


def test_grade_prints_no_line_when_no_shipped_row_was_graded(grade) -> None:
    grade.result = grade_result(graded=0)
    r = run("grade", "--season", "2026", "--week", "5")
    assert r.exit_code == 0 and "shipped targets" not in flat(r.output)


def test_grade_a_range_of_weeks_and_the_url(grade) -> None:
    grade.result = grade_result(url="https://wandb.ai/u/p/runs/g1")
    r = run("grade", "--season", "2025", "--weeks", "1-3", "--no-wandb", "--smoke",
            "--launched-by", "agent")  # fmt: skip
    assert r.exit_code == 0, r.output
    assert [c[0] for c in grade.calls] == [(2025, 1), (2025, 2), (2025, 3)]
    assert all(c[1]["use_wandb"] is False and c[1]["smoke"] for c in grade.calls)
    out = flat(r.output)
    assert out.count("W&B: https://wandb.ai/u/p/runs/g1") == 3
    for w in (1, 2, 3):
        assert f"week {w}, shipped targets" in out


def test_grade_needs_exactly_one_of_week_and_weeks(grade) -> None:
    assert run("grade", "--season", "2025", "--week", "3", "--weeks", "1-4").exit_code == 2
    assert run("grade", "--season", "2025").exit_code == 2
    assert grade.calls == []


@pytest.mark.parametrize(
    "err", [FileNotFoundError("no forecast for 2025 week 3: run [it] first"),
            ValueError("bad week")],
)  # fmt: skip
def test_grade_errors_exit_1_and_stop_the_range(grade, err) -> None:
    grade.raises = {1: err}
    r = run("grade", "--season", "2025", "--weeks", "3-5")
    assert r.exit_code == 1 and str(err.args[0]) in flat(r.output)
    assert len(grade.calls) == 1


def test_grade_does_not_catch_other_errors(grade) -> None:
    grade.raises = {1: KeyError("target")}
    r = run("grade", "--season", "2025", "--week", "3")
    assert r.exit_code == 1 and isinstance(r.exception, KeyError)


def test_the_commands_never_reach_the_real_runs(monkeypatch) -> None:
    """Guard for the file: with a stub missing, the real run_* would write to D:."""
    seen = []
    monkeypatch.setattr(cli.console, "_width", 250)
    monkeypatch.setattr(fr, "run_forecast", lambda *a, **k: seen.append(a) or {"url": None})
    monkeypatch.setattr(fr, "run_grade", lambda *a, **k: seen.append(a) or grade_result())
    assert run("forecast", "--season", "2025", "--week", "3", "--no-wandb").exit_code == 0
    assert run("grade", "--season", "2025", "--week", "3", "--no-wandb").exit_code == 0
    assert seen == [(2025, 3), (2025, 3)]
