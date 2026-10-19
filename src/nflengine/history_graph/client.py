"""The history graph's driver (AE02): Bolt on `history_graph.bolt_uri` (7688), the same user and
`NEO4J_PASSWORD` as the weekly graph (no new secret). Never the weekly graph's URI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nflengine.history_graph.config import HistoryConfig, history_config
from nflengine.settings import EnvSettings, get_env

if TYPE_CHECKING:
    from neo4j import Driver


def get_history_driver(
    env: EnvSettings | None = None,
    cfg: HistoryConfig | None = None,
    connection_timeout: float = 10.0,
) -> Driver:
    from neo4j import GraphDatabase

    env = env or get_env()
    cfg = cfg or history_config()
    if not env.is_set("NEO4J_PASSWORD"):
        raise RuntimeError("NEO4J_PASSWORD is not set in the env file.")
    return GraphDatabase.driver(
        cfg.bolt_uri,
        auth=(env.neo4j_user, env.neo4j_password.get_secret_value()),
        connection_timeout=connection_timeout,
    )


def history_up(cfg: HistoryConfig | None = None, timeout: float = 5.0) -> bool:
    """True if the history graph answers on Bolt (short timeout), False otherwise (down,
    starting, no password). Never raises."""
    try:
        driver = get_history_driver(cfg=cfg, connection_timeout=timeout)
    except Exception:
        return False
    try:
        driver.verify_connectivity()
        return True
    except Exception:
        return False
    finally:
        driver.close()
