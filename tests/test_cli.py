from typer.testing import CliRunner

from nflengine.cli import PLACEHOLDERS, app

runner = CliRunner()


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ["doctor", "wandb-smoke", "ingest", "curate", "data-status", *PLACEHOLDERS]:
        assert name in result.output


def test_placeholders_exit_nonzero_with_phase() -> None:
    result = runner.invoke(app, ["features"])
    assert result.exit_code == 1
    assert "P02" in result.output


def test_doctor_exit_code_reflects_failures(monkeypatch) -> None:
    from nflengine import doctor

    def fake(status):
        return lambda **_: [doctor.Check("x", status, "detail")]

    monkeypatch.setattr(doctor, "run_checks", fake(doctor.OK))
    assert runner.invoke(app, ["doctor"]).exit_code == 0
    monkeypatch.setattr(doctor, "run_checks", fake(doctor.FAIL))
    assert runner.invoke(app, ["doctor"]).exit_code == 1
