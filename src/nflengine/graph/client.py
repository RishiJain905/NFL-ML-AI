"""Neo4j driver factory and health check (documentation/05 -> Runtime)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nflengine.settings import EnvSettings, get_env

if TYPE_CHECKING:
    from neo4j import Driver


def get_driver(env: EnvSettings | None = None, connection_timeout: float = 10.0) -> Driver:
    from neo4j import GraphDatabase

    env = env or get_env()
    if not env.is_set("NEO4J_PASSWORD"):
        raise RuntimeError("NEO4J_PASSWORD is not set in the env file.")
    return GraphDatabase.driver(
        env.neo4j_uri,
        auth=(env.neo4j_user, env.neo4j_password.get_secret_value()),
        connection_timeout=connection_timeout,
    )


class GraphCountMismatch(RuntimeError):
    """Neo4j's node / relationship counts differ from what the loaded tables predict."""

    def __init__(self, keys: list[str]):
        super().__init__(", ".join(keys))
        self.keys = keys


def safe_error(e: BaseException) -> str:
    """An error description that is safe for files, logs and W&B: the exception type plus a
    fixed reason (or a Neo4j status code), never the driver's or server's message text,
    which can echo query parameters or server details (Sol review, P05)."""
    name = type(e).__name__
    if isinstance(e, GraphCountMismatch):
        return f"{name}: Neo4j counts differ from the tables for {', '.join(e.keys)}"
    if name in ("ServiceUnavailable", "SessionExpired") or isinstance(e, OSError):
        return f"{name}: Neo4j unreachable"
    if name == "AuthError":
        return f"{name}: Neo4j rejected the credentials"
    if isinstance(e, RuntimeError) and "NEO4J_PASSWORD" in str(e):
        return "NEO4J_PASSWORD is not set"
    code = getattr(e, "code", None)  # neo4j.exceptions.Neo4jError status code
    if isinstance(code, str) and code.startswith("Neo."):
        return f"{name}: {code}"
    return name


def ping(driver: Driver) -> dict[str, str]:
    """Return Neo4j, GDS and APOC versions. Raises if the server is unreachable."""
    driver.verify_connectivity()
    info: dict[str, str] = {}
    with driver.session() as session:
        rec = session.run(
            "CALL dbms.components() YIELD name, versions, edition "
            "RETURN versions[0] AS version, edition"
        ).single()
        info["neo4j"] = f"{rec['version']} ({rec['edition']})"
        for plugin in ("gds", "apoc"):
            try:
                info[plugin] = session.run(f"RETURN {plugin}.version() AS v").single()["v"]
            except Exception:  # plugin missing or not yet loaded
                info[plugin] = "MISSING"
    return info
