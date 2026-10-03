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
