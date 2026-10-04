"""The graph query library v1 (documentation/05 -> Query library; plan P05).

Each query is a parameterized `.cypher` file next to this module. Every row it returns
carries the entities, numbers already computed (the LLM does no math), `insight_type`, a
`strength` score in [0, 1] and `sample_size`. Queries run in **read** transactions only.

| query | insight_type | digest section |
|---|---|---|
| q1_revenge | revenge | non_obvious |
| q2_injury_ripple | injury_ripple | matchup_risk |
| q3_qb_change | qb_change | matchup_risk |
| q4_common_opponents | common_opponents | non_obvious |
| q8_trend_mismatch | trend_mismatch | matchup_risk / non_obvious |

Q5-Q7, Q9 and the GDS jobs (Q10) come in P08.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j import Driver

QUERY_DIR = Path(__file__).resolve().parent
OFFENSE_GROUPS = ["RB", "WR", "TE", "OL"]
DEFENSE_GROUPS = ["DL", "LB", "DB"]

# query name -> extra parameters (besides $season / $week)
LIBRARY: dict[str, dict[str, Any]] = {
    "q1_revenge": {"min_old_games": 4, "min_snap_pct": 0.5},
    "q2_injury_ripple": {"groups": OFFENSE_GROUPS + DEFENSE_GROUPS, "history_seasons": 2},
    "q3_qb_change": {"min_receiver_targets": 5},
    "q4_common_opponents": {},
    "q8_trend_mismatch": {},
}


def query_text(name: str) -> str:
    if name not in LIBRARY:
        raise KeyError(f"unknown graph query {name!r}; library: {', '.join(LIBRARY)}")
    return (QUERY_DIR / f"{name}.cypher").read_text(encoding="utf-8")


@dataclass
class QueryResult:
    name: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rows": len(self.rows),
            "seconds": round(self.seconds, 3),
            "error": self.error,
            "results": self.rows,
        }


def _plain(v: Any) -> Any:
    """Neo4j values -> JSON-friendly Python values."""
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if hasattr(v, "iso_format"):  # neo4j.time types
        return v.iso_format()
    return v


def run_query(driver: Driver, name: str, season: int, week: int, **overrides: Any) -> QueryResult:
    """Run one library query in a read transaction; never raises (errors are recorded)."""
    params = {"season": season, "week": week, **LIBRARY[name], **overrides}
    text = query_text(name)
    t0 = time.perf_counter()
    try:
        with driver.session() as session:
            records = session.execute_read(lambda tx: [r.data() for r in tx.run(text, **params)])
        rows = [{k: _plain(v) for k, v in r.items()} for r in records]
        return QueryResult(name, rows, time.perf_counter() - t0)
    except Exception as e:  # one broken query must not sink the build
        from nflengine.graph.client import safe_error

        return QueryResult(name, [], time.perf_counter() - t0, safe_error(e))


def run_library(driver: Driver, season: int, week: int) -> dict[str, QueryResult]:
    return {name: run_query(driver, name, season, week) for name in LIBRARY}
