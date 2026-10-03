from typer.testing import CliRunner

from nflengine.cli import PLACEHOLDERS, app

runner = CliRunner()


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in [
        "doctor",
        "wandb-smoke",
        "ingest",
        "curate",
        "data-status",
        "ratings",
        "features",
        "backtest",
        "train",
        *PLACEHOLDERS,
    ]:
        assert name in result.output


def test_placeholders_exit_nonzero_with_phase() -> None:
    result = runner.invoke(app, ["graph"])
    assert result.exit_code == 1
    assert "P05" in result.output


def test_game_model_subcommands_listed() -> None:
    for group, names in (
        ("features", ["game"]),
        ("backtest", ["game", "game-weights"]),
        ("train", ["game"]),
    ):
        result = runner.invoke(app, [group, "--help"])
        assert result.exit_code == 0
        for name in names:
            assert name in result.output


def test_backtest_rejects_unknown_variant() -> None:
    result = runner.invoke(app, ["backtest", "game", "--variant", "vegas"])
    assert result.exit_code == 2  # a usage error, before any data or W&B is touched
    assert "variant must be model-only or market" in result.output


def test_ratings_subcommands_listed() -> None:
    result = runner.invoke(app, ["ratings", "--help"])
    assert result.exit_code == 0
    for name in ["build", "tune", "eval", "validate-trend"]:
        assert name in result.output


def test_doctor_exit_code_reflects_failures(monkeypatch) -> None:
    from nflengine import doctor

    def fake(status):
        return lambda **_: [doctor.Check("x", status, "detail")]

    monkeypatch.setattr(doctor, "run_checks", fake(doctor.OK))
    assert runner.invoke(app, ["doctor"]).exit_code == 0
    monkeypatch.setattr(doctor, "run_checks", fake(doctor.FAIL))
    assert runner.invoke(app, ["doctor"]).exit_code == 1
