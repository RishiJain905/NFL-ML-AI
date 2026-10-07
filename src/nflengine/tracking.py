"""Weights & Biases helpers (documentation/08).

Every training / evaluation run goes through `init_run` so project, group,
job type, the `launched-by` tag and the data-root W&B dir are always set.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterable
from typing import Any

from nflengine.paths import DataPaths, configure_tool_env, ensure_data_root
from nflengine.settings import REPO_ROOT, get_config, get_env

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


# CR02: who launched this process, when not a terminal (`nfl ... --app-run <id>` sets
# "control-room"). Every W&B run of the process then carries a `via:<launcher>` tag, so runs
# started from the control room can be told apart from terminal runs (D103).
LAUNCH_VIA: str | None = None


def set_launch_via(via: str | None) -> None:
    global LAUNCH_VIA
    LAUNCH_VIA = via


def default_launched_by() -> str:
    """'agent' when running inside a Claude Code session, otherwise 'rishi'."""
    return "agent" if os.environ.get("CLAUDECODE") else "rishi"


def git_commit() -> str:
    """Short HEAD commit, with '-dirty' when the working tree has uncommitted changes."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return f"{head}-dirty" if dirty else head


def dataset_version(paths: DataPaths | None = None) -> dict[str, str | None]:
    """Which data a run used: newest play-by-play snapshot + when curation last ran."""
    paths = paths or ensure_data_root()
    pbp = paths.raw / "nflverse" / "pbp"
    snaps = sorted(p.name.split("=", 1)[1] for p in pbp.glob("snapshot=*")) if pbp.exists() else []
    quality = paths.curated / "_quality" / "latest.json"
    run_at = json.loads(quality.read_text()).get("run_at") if quality.exists() else None
    return {"pbp_snapshot": snaps[-1] if snaps else None, "curated_run_at": run_at}


def _wandb_env() -> tuple[str, str | None, DataPaths]:
    """Point W&B at the data root and make the API key available to this process."""
    env = get_env()
    paths = ensure_data_root()
    configure_tool_env(paths)
    if env.is_set("WANDB_API_KEY"):
        # Process-local only; never written to disk or logged.
        os.environ.setdefault("WANDB_API_KEY", env.wandb_api_key.get_secret_value())
    return get_config().wandb.project, env.wandb_entity or None, paths


def read_api(timeout: int = 20) -> tuple[Any, str, str] | None:
    """A read-only W&B API client for the control room (CR03), as `(api, entity, project)`, or
    None when the key isn't set. The key goes straight from the settings to the client (the
    doctor's way): nothing is written to the process environment, a file or a log."""
    env = get_env()
    if not env.is_set("WANDB_API_KEY"):
        return None
    import wandb

    api = wandb.Api(api_key=env.wandb_api_key.get_secret_value(), timeout=timeout)
    entity = env.wandb_entity or api.default_entity
    return api, entity, get_config().wandb.project


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
    project, entity, paths = _wandb_env()
    launched_by = launched_by or default_launched_by()
    return wandb.init(
        project=project,
        entity=entity,
        group=group,
        job_type=job_type,
        config=config,
        tags=[*tags, f"launched-by:{launched_by}", *([f"via:{LAUNCH_VIA}"] if LAUNCH_VIA else [])],
        name=name,
        dir=str(paths.wandb),
    )


def run_sweep(sweep_config: dict[str, Any], function: Callable[[], None]) -> str:
    """Create a W&B sweep and run every configuration in this process.

    `function` must start its run with `init_run(...)`; the sweep's parameters arrive in
    `run.config`. Returns the sweep id.
    """
    import wandb

    assert_no_secrets(sweep_config)
    project, entity, _ = _wandb_env()
    sweep_id = wandb.sweep(sweep_config, project=project, entity=entity)
    wandb.agent(sweep_id, function=function, project=project, entity=entity)
    return sweep_id
