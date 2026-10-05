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
from typing import Any

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
    # anything else makes `get_llm` raise, so the digest step would fail: say so plainly
    from nflengine.digest.llm import NOT_BUILT

    why = "isn't built (D87)" if provider in NOT_BUILT else "is unknown"
    return Check("llm", FAIL, f"provider '{provider}' {why}; use openrouter or placeholder")


def check_openrouter(model: str | None) -> Check:
    """Key set + accepted (GET /key, status only: the reply is never shown) + model listed +
    at least one `llm.openrouter.only` endpoint serves it (P09: after a model switch, the old
    model's tags match nothing and every request would fail over to the placeholder)."""
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
        if listed.status_code == 404:  # every digest call would fail over to the placeholder
            return Check("llm", FAIL, f"key OK; model {model} not found on OpenRouter (HTTP 404)")
        if listed.status_code != 200:
            return Check(
                "llm",
                WARN,
                f"key OK; couldn't list {model}'s endpoints (HTTP {listed.status_code})",
            )
        endpoints = _endpoint_list(listed)
        if endpoints is None:
            return Check("llm", WARN, f"key OK; {model}'s endpoint list was unreadable")
        detail = f"openrouter key accepted; {model} has {len(endpoints)} endpoints"
        only = [str(t) for t in (get_config().llm.openrouter or {}).get("only") or []]
        if not only:
            return Check("llm", OK, detail)
        tags = {str(e.get("tag") or "") for e in endpoints if isinstance(e, dict)}
        live = [t for t in only if _only_serves(t, tags)]
        if not live:
            return Check(
                "llm",
                FAIL,
                f"key OK; none of the {len(only)} llm.openrouter.only endpoints serves {model}",
            )
        return Check("llm", OK, f"{detail}, {len(live)} of {len(only)} allowed ones listed")
    except Exception as exc:
        return Check("llm", FAIL, f"openrouter unreachable: {type(exc).__name__}")


# OpenRouter service tiers: a base slug ("openai") never matches their endpoints
# ("openai/fast"); they need the tier-suffixed slug, and fast / priority are one tier
_TIERS = {"fast", "priority", "flex", "ultrafast"}
_TIER_ALIAS = {"fast": "priority", "priority": "fast"}


def _only_serves(entry: str, tags: set[str]) -> bool:
    """Whether a `llm.openrouter.only` entry matches a listed endpoint: an exact tag
    ("novita/fp8"), or a provider slug ("novita") matching its non-tier endpoints
    (OpenRouter's base-slug rule; Sol review: a slug alone never selects "openai/fast")."""
    if entry in tags:
        return True
    base, _, suffix = entry.partition("/")
    if suffix:
        return suffix in _TIER_ALIAS and f"{base}/{_TIER_ALIAS[suffix]}" in tags
    return any(t.partition("/")[0] == base and t.partition("/")[2] not in _TIERS for t in tags)


def _endpoint_list(resp: Any) -> list | None:
    """The `data.endpoints` list of a model listing, or None when the body isn't one."""
    try:
        data = resp.json()
    except ValueError:
        return None
    inner = data.get("data") if isinstance(data, dict) else None
    endpoints = inner.get("endpoints") if isinstance(inner, dict) else None
    if not isinstance(endpoints, list):
        return None
    # every entry must be an object with a string tag, or no conclusion follows (Sol review)
    if not all(isinstance(e, dict) and isinstance(e.get("tag"), str) for e in endpoints):
        return None
    return endpoints


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
