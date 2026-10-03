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
    cfg = get_config().llm
    provider = cfg.provider
    if provider == "placeholder":
        return Check("llm", OK, "placeholder provider (no API needed)")
    if provider == "openrouter":
        return check_openrouter(cfg.model)
    return Check("llm", WARN, f"provider '{provider}' not implemented until P09")


def check_openrouter(model: str | None) -> Check:
    """Key set + accepted (GET /key, status only: the reply is never shown) + model listed."""
    env = get_env()
    if not env.is_set("OPENROUTER_API_KEY"):
        return Check("llm", FAIL, "provider openrouter but OPENROUTER_API_KEY is NOT SET")
    try:
        import httpx

        headers = {"Authorization": f"Bearer {env.openrouter_api_key.get_secret_value()}"}
        key = httpx.get("https://openrouter.ai/api/v1/key", headers=headers, timeout=20)
        if key.status_code != 200:
            return Check("llm", FAIL, f"openrouter rejected the key (HTTP {key.status_code})")
        listed = httpx.get(f"https://openrouter.ai/api/v1/models/{model}/endpoints", timeout=20)
        if listed.status_code != 200:
            return Check(
                "llm", WARN, f"key OK; model {model} not found (HTTP {listed.status_code})"
            )
        n = len((listed.json().get("data") or {}).get("endpoints") or [])
        return Check("llm", OK, f"openrouter key accepted; {model} has {n} endpoints")
    except Exception as exc:
        return Check("llm", FAIL, f"openrouter unreachable: {type(exc).__name__}")


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
