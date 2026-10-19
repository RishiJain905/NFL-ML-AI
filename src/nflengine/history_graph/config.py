"""The `history_graph:` block of config/settings.yaml, typed (AE02).

Validated so that no edit of the block can point a command at the weekly graph: the service,
container and staging volume must be the history graph's own, and the Bolt / Browser ports
must not be the weekly graph's (7687 / 7474). A bad block raises pydantic's `ValidationError`
(the test worker, AE02: `service: neo4j` would have made `stop` stop the weekly container).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator

WEEKLY_NAMES = frozenset({"neo4j", "nfl-neo4j"})
WEEKLY_PORTS = {"bolt_uri": 7687, "browser_url": 7474}


class HistoryConfig(BaseModel):
    first_season: int = 2018
    bolt_uri: str = "bolt://localhost:7688"
    browser_url: str = "http://localhost:7475"
    service: str = "neo4j-history"
    container: str = "nfl-neo4j-history"
    profile: str = "history"
    staging_volume: str = "nfl-neo4j-history-staging"
    start_timeout_s: float = 300.0
    batch_size: int = 5000

    @field_validator("service", "container", "profile")
    @classmethod
    def _history_names(cls, v: str) -> str:
        if v.strip().lower() in WEEKLY_NAMES or "history" not in v.lower():
            raise ValueError("must be the history graph's own name (containing 'history')")
        return v

    @field_validator("staging_volume")
    @classmethod
    def _staging(cls, v: str) -> str:
        if not v.startswith("nfl-neo4j-history"):
            raise ValueError("the staging volume's name must start with 'nfl-neo4j-history'")
        return v

    @field_validator("bolt_uri", "browser_url")
    @classmethod
    def _ports(cls, v: str, info) -> str:
        port = urlparse(v).port
        if port is None or port == WEEKLY_PORTS[info.field_name]:
            raise ValueError("needs an explicit port that isn't the weekly graph's")
        return v

    @field_validator("batch_size")
    @classmethod
    def _batch(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be positive")
        return v


def history_config(data: dict[str, Any] | None = None) -> HistoryConfig:
    """The typed block (default: settings.yaml's)."""
    if data is None:
        from nflengine.settings import get_config

        data = get_config().history_graph
    return HistoryConfig.model_validate(data or {})
