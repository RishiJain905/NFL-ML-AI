"""Weights & Biases helpers (documentation/08).

Every training / evaluation run goes through `init_run` so project, group,
job type, the `launched-by` tag and the data-root W&B dir are always set.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from nflengine.paths import configure_tool_env, ensure_data_root
from nflengine.settings import get_config, get_env

SECRET_KEY_HINTS = ("password", "secret", "token", "api_key", "apikey", "credential")


def assert_no_secrets(config: dict[str, Any], _prefix: str = "") -> None:
    """Refuse W&B configs that contain SecretStr values or secret-looking keys."""
    from pydantic import SecretStr

    for key, value in config.items():
        name = f"{_prefix}{key}"
        if isinstance(value, SecretStr) or any(h in str(key).lower() for h in SECRET_KEY_HINTS):
            raise ValueError(f"Refusing to log config key '{name}' to W&B: looks like a secret.")
        if isinstance(value, dict):
            assert_no_secrets(value, f"{name}.")


def default_launched_by() -> str:
    """'agent' when running inside a Claude Code session, otherwise 'rishi'."""
    return "agent" if os.environ.get("CLAUDECODE") else "rishi"


def init_run(
    group: str,
    job_type: str,
    config: dict[str, Any] | None = None,
    tags: Iterable[str] = (),
    launched_by: str | None = None,
    name: str | None = None,
):
    import wandb

    config = config or {}
    assert_no_secrets(config)
    env = get_env()
    paths = ensure_data_root()
    configure_tool_env(paths)
    if env.is_set("WANDB_API_KEY"):
        # Process-local only; never written to disk or logged.
        os.environ.setdefault("WANDB_API_KEY", env.wandb_api_key.get_secret_value())

    launched_by = launched_by or default_launched_by()
    return wandb.init(
        project=get_config().wandb.project,
        entity=env.wandb_entity or None,
        group=group,
        job_type=job_type,
        config=config,
        tags=[*tags, f"launched-by:{launched_by}"],
        name=name,
        dir=str(paths.wandb),
    )
