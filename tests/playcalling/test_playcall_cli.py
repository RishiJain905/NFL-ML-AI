"""`nfl playcalling build` (PC00): the CLI wiring, with `run_build` stubbed.

The command imports `run_build` when it runs, so the stub goes on `nflengine.playcalling.build`:
nothing here reads or writes D: or logs to W&B.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import nflengine.playcalling.build as build_mod
from nflengine import cli
from nflengine.cli import app

runner = CliRunner()


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def season_row(season: int = 2025, **over) -> dict:
    row = {
        "season": season,
        "folder": f"D:/data/playcalling/{season}",
        "as_of_weeks": [1, 19],
        "rows": {
            "plays_enriched": 69_404,
            "team_game_tendencies": 1,
            "team_tendencies": 171_050,
            "league_tendencies": 1,
        },
        "coverage": {"ftn_join_rate": 0.9986, "part_join_rate": 0.0, "games_without_ftn": []},
    }
    row.update(over)
    return row


class Stub:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self.result: dict = {"seasons": [season_row()], "url": None, "notes": []}
        self.raises: Exception | None = None

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.result


@pytest.fixture
def stub(monkeypatch) -> Stub:
    s = Stub()
    monkeypatch.setattr(build_mod, "run_build", s)
    monkeypatch.setattr(cli.console, "_width", 250)  # no wrapping inside tables and long lines
    return s


def run(*args: str):
    return runner.invoke(app, ["playcalling", "build", *args])


# --- wiring -------------------------------------------------------------------------------------
def test_the_group_and_its_command_are_listed() -> None:
    top = runner.invoke(app, ["--help"])
    assert top.exit_code == 0 and "playcalling" in top.output
    group = runner.invoke(app, ["playcalling", "--help"])
    assert group.exit_code == 0 and "build" in group.output
    help_ = runner.invoke(app, ["playcalling", "build", "--help"])
    assert help_.exit_code == 0
    for opt in (
        "--season",
        "--through-week",
        "--history",
        "--no-wandb",
        "--smoke",
        "--launched-by",
    ):
        assert opt in help_.output, opt


def test_season_is_required(stub) -> None:
    r = run()
    assert r.exit_code == 2 and "--season" in flat(r.output)
    assert stub.calls == []


def test_defaults_are_passed_through(stub) -> None:
    r = run("--season", "2025")
    assert r.exit_code == 0, r.output
    ((args, kwargs),) = stub.calls
    assert args == (2025, None, [])
    assert set(kwargs) == {"use_wandb", "launched_by", "log", "smoke"}
    assert kwargs["use_wandb"] is True and kwargs["smoke"] is False
    assert kwargs["launched_by"] is None
    assert kwargs["log"] == cli.console.print  # progress lines go to the console


def test_every_option_is_passed_through(stub) -> None:
    r = run(
        "--season", "2026", "--through-week", "9", "--history", "2018,2020-2021", "--no-wandb",
        "--smoke", "--launched-by", "agent",
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    ((args, kwargs),) = stub.calls
    assert args == (2026, 9, [2018, 2020, 2021])
    assert kwargs["use_wandb"] is False and kwargs["smoke"] is True
    assert kwargs["launched_by"] == "agent"


def test_history_range(stub) -> None:
    run("--season", "2025", "--history", "2016-2018")
    assert stub.calls[0][0] == (2025, None, [2016, 2017, 2018])


def test_history_all_reads_the_settings(stub, monkeypatch) -> None:
    import nflengine.settings as settings

    monkeypatch.setattr(
        settings,
        "get_config",
        lambda: SimpleNamespace(playcalling={"history_seasons": "2020-2022"}),
    )
    r = run("--season", "2025", "--history", "all")
    assert r.exit_code == 0, r.output
    assert stub.calls[0][0] == (2025, None, [2020, 2021, 2022])


def test_history_all_falls_back_to_the_participation_years(stub, monkeypatch) -> None:
    import nflengine.settings as settings

    monkeypatch.setattr(settings, "get_config", lambda: SimpleNamespace(playcalling={}))
    run("--season", "2025", "--history", "all")
    assert stub.calls[0][0][2] == list(range(2016, 2026))


def test_history_all_with_the_real_settings_file(stub) -> None:
    """config/settings.yaml: playcalling.history_seasons is the participation window."""
    r = run("--season", "2025", "--history", "all")
    assert r.exit_code == 0, r.output
    assert stub.calls[0][0][2] == list(range(2016, 2026))


def test_a_plain_history_value_is_not_read_from_the_settings(stub, monkeypatch) -> None:
    import nflengine.settings as settings

    def boom():
        raise AssertionError("settings read without --history all")

    monkeypatch.setattr(settings, "get_config", boom)
    assert run("--season", "2025", "--history", "2024").exit_code == 0
    assert stub.calls[0][0][2] == [2024]


# --- errors -------------------------------------------------------------------------------------
def test_a_value_error_exits_1_and_shows_the_message(stub) -> None:
    stub.raises = ValueError("play-calling tables start in 2016; got [2015]")
    r = run("--season", "2015")
    assert r.exit_code == 1
    out = flat(r.output)
    assert "play-calling tables start in 2016; got [2015]" in out  # brackets are not markup
    assert "play-calling build" not in out  # no table after an error
    assert len(stub.calls) == 1


def test_missing_curated_plays_exit_1(stub) -> None:
    stub.raises = FileNotFoundError("no curated plays for seasons [2024, 2030] under X/plays")
    r = run("--season", "2030")
    assert r.exit_code == 1
    assert "no curated plays for seasons [2024, 2030]" in flat(r.output)


def test_other_errors_are_not_swallowed(stub) -> None:
    stub.raises = RuntimeError("disk on fire")
    r = run("--season", "2025")
    assert r.exit_code == 1 and isinstance(r.exception, RuntimeError)
    assert "disk on fire" not in flat(r.output)  # not the friendly red message


# --- output -------------------------------------------------------------------------------------
def test_the_summary_table(stub) -> None:
    stub.result = {
        "seasons": [
            season_row(2024, as_of_weeks=[1, 23]),
            season_row(2025, as_of_weeks=[1, 19]),
        ],
        "url": None,
        "notes": [],
    }
    r = run("--season", "2025", "--history", "2024")
    assert r.exit_code == 0, r.output
    out = flat(r.output)
    assert "play-calling build" in out
    for header in ("season", "as-of weeks", "plays", "tendency rows", "FTN", "participation"):
        assert header in out, header
    assert "2024" in out and "1-23" in out and "1-19" in out
    assert "69,404" in out and "171,050" in out  # thousands separators
    assert "99.9%" in out and "0.0%" in out  # FTN 0.9986, participation 0
    assert "W&B:" not in out and "games without FTN" not in out


def test_a_season_with_no_as_of_week_shows_a_dash(stub) -> None:
    stub.result = {"seasons": [season_row(2026, as_of_weeks=[])], "url": None, "notes": []}
    r = run("--season", "2026")
    assert r.exit_code == 0 and re.search(r"2026 \W+ - \W+ 69,404", flat(r.output))


def test_games_without_ftn_are_listed_up_to_twelve(stub) -> None:
    ids = [f"2026_05_AA{i:02d}_BB{i:02d}" for i in range(15)]
    cov = {"ftn_join_rate": 0.9, "part_join_rate": 0.0, "games_without_ftn": ids[:10]}
    cov2 = {"ftn_join_rate": 0.9, "part_join_rate": 0.0, "games_without_ftn": ids[10:]}
    stub.result = {
        "seasons": [season_row(2025, coverage=cov), season_row(2026, coverage=cov2)],
        "url": None,
        "notes": [],
    }
    out = flat(run("--season", "2026", "--history", "2025").output)
    assert "games without FTN yet:" in out
    assert ids[0] in out and ids[11] in out  # the first twelve across the seasons ...
    assert ids[12] not in out and ids[14] not in out  # ... and no more


def test_the_wandb_url_is_printed(stub) -> None:
    stub.result = {
        "seasons": [season_row()],
        "url": "https://wandb.ai/x/nfl-analytics-engine/runs/abc123",
        "notes": [],
    }
    out = flat(run("--season", "2025").output)
    assert "W&B: https://wandb.ai/x/nfl-analytics-engine/runs/abc123" in out


def test_the_command_never_reaches_the_real_build(monkeypatch) -> None:
    """Guard for the file: with the stub removed the real run_build would write to D:."""
    seen = []
    monkeypatch.setattr(cli.console, "_width", 250)
    monkeypatch.setattr(
        build_mod,
        "run_build",
        lambda *a, **k: seen.append(a) or {"seasons": [], "url": None, "notes": []},
    )
    r = run("--season", "2025", "--no-wandb")
    assert r.exit_code == 0 and seen == [(2025, None, [])]
