from pathlib import Path

import pytest

from nflengine.settings import EnvSettings, load_app_config, load_followed_teams

FAKE_SECRET = "fake-secret-value-123"


def make_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> EnvSettings:
    for key in (
        "NFL_DATA_ROOT",
        "NEO4J_PASSWORD",
        "NEO4JS_PASSWORD",
        "WANDB_API_KEY",
        "WANDB_ENTITY",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return EnvSettings(_env_file=None)  # never touch the real env file in tests


def test_secrets_are_masked_in_repr_and_str(monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(monkeypatch, NEO4J_PASSWORD=FAKE_SECRET, WANDB_API_KEY=FAKE_SECRET)
    assert FAKE_SECRET not in repr(env)
    assert FAKE_SECRET not in str(env)
    assert FAKE_SECRET not in env.model_dump_json()
    assert env.neo4j_password.get_secret_value() == FAKE_SECRET


def test_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(monkeypatch, WANDB_API_KEY=FAKE_SECRET, NEO4J_PASSWORD="  ")
    assert env.is_set("WANDB_API_KEY")
    assert not env.is_set("NEO4J_PASSWORD")  # whitespace only counts as unset
    assert not env.is_set("NFL_DATA_ROOT")


def test_misspelled_neo4j_password_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEO4JS_PASSWORD", raising=False)
    env = make_env(monkeypatch, NEO4JS_PASSWORD=FAKE_SECRET)
    assert env.is_set("NEO4J_PASSWORD")
    assert env.neo4j_password_via_fallback
    assert FAKE_SECRET not in repr(env)
    # The correct name wins when both are present.
    env = make_env(monkeypatch, NEO4J_PASSWORD="right-one", NEO4JS_PASSWORD=FAKE_SECRET)
    assert env.neo4j_password.get_secret_value() == "right-one"
    assert not env.neo4j_password_via_fallback


def test_data_root_parses_forward_slash_path(monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(monkeypatch, NFL_DATA_ROOT="D:/nfl-ml-data")
    assert env.nfl_data_root == Path("D:/nfl-ml-data")


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(monkeypatch)
    assert env.neo4j_uri == "bolt://localhost:7687"
    assert env.neo4j_user == "neo4j"


def test_repo_app_config_loads() -> None:
    cfg = load_app_config()
    assert cfg.wandb.project == "nfl-analytics-engine"
    assert cfg.llm.provider == "placeholder"
    assert cfg.seasons.current == 2026


def test_followed_teams_uppercased(tmp_path: Path) -> None:
    f = tmp_path / "teams.yaml"
    f.write_text("teams: [det, kc]\n")
    assert load_followed_teams(f) == ["DET", "KC"]
    assert load_followed_teams(tmp_path / "missing.yaml") == []
