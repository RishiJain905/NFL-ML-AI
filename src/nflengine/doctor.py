"""`nfl doctor`: green/red health check of everything the pipeline depends on.

Reports whether credentials are *set* and services are *reachable*, never any
secret value, so agents can check configuration without reading the env file.
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from importlib import metadata

from nflengine.paths import DataRootError, configure_tool_env, ensure_data_root
from nflengine.settings import (
    ENV_FILE,
    OPTIONAL_ENV_VARS,
    REQUIRED_ENV_VARS,
    get_config,
    get_env,
)

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def check_env_file() -> Check:
    if ENV_FILE.exists():
        return Check("env file", OK, "present at repo root (contents not shown)")
    return Check("env file", FAIL, "missing: copy .env.example and fill it in")


def check_env_vars() -> list[Check]:
    env = get_env()
    checks = []
    for var in REQUIRED_ENV_VARS:
        ok = env.is_set(var)
        checks.append(Check(f"var {var}", OK if ok else FAIL, "set" if ok else "NOT SET"))
    optional_set = [v for v in OPTIONAL_ENV_VARS if env.is_set(v)]
    detail = ", ".join(optional_set) if optional_set else "none set (fine for P00)"
    checks.append(Check("optional vars", OK, detail))
    return checks


def check_data_root(init: bool = False) -> Check:
    try:
        paths = ensure_data_root(init=init)
        configure_tool_env(paths)
        probe = paths.root / f".doctor-{uuid.uuid4().hex[:8]}"
        probe.write_text("ok")
        probe.unlink()
        free_gb = shutil.disk_usage(paths.root).free / 1e9
        return Check("data root", OK, f"{paths.root} writable, {free_gb:,.0f} GB free")
    except (DataRootError, OSError) as exc:
        return Check("data root", FAIL, str(exc))


def check_neo4j() -> Check:
    from nflengine.graph.client import get_driver, ping

    try:
        driver = get_driver()
        try:
            info = ping(driver)
        finally:
            driver.close()
    except Exception as exc:
        # Type name + fixed hint only: never echo driver messages (a URI could embed creds).
        return Check("neo4j", FAIL, f"unreachable ({type(exc).__name__}): {_neo4j_hint(exc)}")
    missing = [p for p in ("gds", "apoc") if info.get(p) == "MISSING"]
    detail = f"neo4j {info['neo4j']}, gds {info['gds']}, apoc {info['apoc']}"
    if missing:
        detail += f"; missing plugin(s): {', '.join(missing)}"
    return Check("neo4j", FAIL if missing else OK, detail)


def _neo4j_hint(exc: Exception) -> str:
    name = type(exc).__name__
    if isinstance(exc, RuntimeError) and "NEO4J_PASSWORD" in str(exc):
        return "NEO4J_PASSWORD is not set in the env file"
    if "Auth" in name:
        return "authentication failed; check NEO4J_PASSWORD matches the container's"
    if name in ("ServiceUnavailable", "DriverError") or "Unavailable" in name:
        return "server not reachable; is the container up? (`docker compose up -d`)"
    return "see `docker compose logs neo4j`"


def check_wandb() -> Check:
    env = get_env()
    if not env.is_set("WANDB_API_KEY"):
        return Check("wandb", FAIL, "WANDB_API_KEY not set")
    try:
        import wandb

        api = wandb.Api(api_key=env.wandb_api_key.get_secret_value(), timeout=20)
        viewer = api.viewer
        who = getattr(viewer, "username", None) or getattr(viewer, "entity", None) or "?"
        entity = env.wandb_entity or getattr(viewer, "entity", None) or who
        return Check(
            "wandb",
            OK,
            f"authenticated as {who}; project {entity}/{get_config().wandb.project}",
        )
    except Exception as exc:
        return Check("wandb", FAIL, f"auth failed: {type(exc).__name__}")


def check_nflreadpy() -> Check:
    try:
        import nflreadpy  # noqa: F401

        cache = os.environ.get("NFLREADPY_CACHE_DIR", "default")
        return Check("nflreadpy", OK, f"v{metadata.version('nflreadpy')}, cache {cache}")
    except Exception as exc:
        return Check("nflreadpy", FAIL, f"import failed: {exc}")


def check_llm() -> Check:
    provider = get_config().llm.provider
    if provider == "placeholder":
        return Check("llm", OK, "placeholder provider (no API needed until P09)")
    return Check("llm", WARN, f"provider '{provider}' not implemented until P09")


def run_checks(init_data_root: bool = False) -> list[Check]:
    return [
        check_env_file(),
        *check_env_vars(),
        check_data_root(init=init_data_root),
        check_neo4j(),
        check_wandb(),
        check_nflreadpy(),
        check_llm(),
    ]
