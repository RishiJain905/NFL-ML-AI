import pytest
from pydantic import SecretStr

from nflengine.tracking import assert_no_secrets, default_launched_by


def test_plain_config_allowed() -> None:
    assert_no_secrets({"model": "ridge", "params": {"alpha": 1.0}, "seasons": [2018, 2025]})


@pytest.mark.parametrize(
    "config",
    [
        {"neo4j_password": "x"},
        {"WANDB_API_KEY": "x"},
        {"nested": {"auth_token": "x"}},
        {"anything": SecretStr("x")},
    ],
)
def test_secret_like_config_refused(config: dict) -> None:
    with pytest.raises(ValueError, match="looks like a secret"):
        assert_no_secrets(config)


def test_launched_by_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    assert default_launched_by() == "agent"
    monkeypatch.delenv("CLAUDECODE")
    assert default_launched_by() == "rishi"
