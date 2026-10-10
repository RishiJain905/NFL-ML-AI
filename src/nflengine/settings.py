"""Typed settings.

Two sources:
- Environment / env file (secrets and machine-specific values) -> `EnvSettings`.
  Secrets are `SecretStr`: never log them, never put them in W&B configs.
- `config/settings.yaml` (non-secret app config) -> `AppConfig`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
CONFIG_DIR = REPO_ROOT / "config"

REQUIRED_ENV_VARS = ("NFL_DATA_ROOT", "NEO4J_PASSWORD", "WANDB_API_KEY")
OPTIONAL_ENV_VARS = (
    "WANDB_ENTITY",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "OPENROUTER_API_KEY",
    "ODDS_API_KEY",
    "ODDS_API_KEY2",
    "KAGGLE_USERNAME",
    "KAGGLE_KEY",
)


class EnvSettings(BaseSettings):
    """Values from the process environment, falling back to the repo env file."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    nfl_data_root: Path | None = None

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr | None = None

    wandb_api_key: SecretStr | None = None
    wandb_entity: str | None = None

    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    openrouter_api_key: SecretStr | None = None  # digest LLM via OpenRouter (D56)
    odds_api_key: SecretStr | None = None
    odds_api_key2: SecretStr | None = None  # backup key, used when the first is exhausted
    kaggle_username: str | None = None
    kaggle_key: SecretStr | None = None

    def is_set(self, var_name: str) -> bool:
        """Whether a variable has a non-empty value. Never exposes the value itself."""
        value = getattr(self, var_name.lower(), None)
        if value is None:
            return False
        if isinstance(value, SecretStr):
            return bool(value.get_secret_value().strip())
        return bool(str(value).strip())


class SeasonsConfig(BaseModel):
    history_start: int = 2010
    graph_start: int = 2018
    current: int = 2026


class WandbConfig(BaseModel):
    project: str = "nfl-analytics-engine"


class LLMConfig(BaseModel):
    provider: str = "placeholder"
    model: str | None = None
    reasoning_effort: str | None = None  # OpenRouter: max | xhigh | high | medium | low | ...
    max_tokens: int = 64000
    timeout_seconds: float = 600.0
    retries: int = 2  # extra attempts on timeouts / 429 / 5xx
    temperature: float | None = None
    openrouter: dict[str, Any] = Field(default_factory=dict)  # provider routing (only, sort ...)


class AppConfig(BaseModel):
    """Non-secret configuration from config/settings.yaml."""

    seasons: SeasonsConfig = Field(default_factory=SeasonsConfig)
    wandb: WandbConfig = Field(default_factory=WandbConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    sources: dict[str, bool] = Field(default_factory=dict)
    flags: dict[str, bool] = Field(default_factory=dict)
    ratings: dict[str, Any] = Field(default_factory=dict)
    elo: dict[str, Any] = Field(default_factory=dict)
    training: dict[str, Any] = Field(default_factory=dict)
    game_model: dict[str, Any] = Field(default_factory=dict)
    player_model: dict[str, Any] = Field(default_factory=dict)
    team_model: dict[str, Any] = Field(default_factory=dict)  # team stat totals (P08)
    consistency: dict[str, Any] = Field(default_factory=dict)  # consistency layer (P08)
    qb: dict[str, Any] = Field(default_factory=dict)
    digest: dict[str, Any] = Field(default_factory=dict)
    ops: dict[str, Any] = Field(default_factory=dict)  # weekly operations (P07)
    drift: dict[str, Any] = Field(default_factory=dict)  # drift thresholds (P07, doc 08)
    injury_update: dict[str, Any] = Field(default_factory=dict)  # Saturday update (P07)
    live: dict[str, Any] = Field(default_factory=dict)  # live decisions (LD00+, D107)


def load_app_config(path: Path | None = None) -> AppConfig:
    path = path or CONFIG_DIR / "settings.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return AppConfig.model_validate(data or {})


def load_followed_teams(path: Path | None = None) -> list[str]:
    path = path or CONFIG_DIR / "followed_teams.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(t).upper() for t in data.get("teams", []) or []]


@lru_cache(maxsize=1)
def get_env() -> EnvSettings:
    return EnvSettings()


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_app_config()
