"""Player projections in the knowledge graph (plan P06; documentation/05 -> PlayerProjection).

`(:Player)-[:HAS_PROJECTION]->(:PlayerProjection)-[:FOR_GAME]->(:Game)`: one node per
(player, game, target, model version), keyed `<player_id>|<game_id>|<target key>|<model
version>` (the target key, `rec_yds-wrte`, names the position group too: a linebacker has a
pressures and a tackles projection for the same game).

- **Idempotent:** `MERGE` on the constrained `key` (`schema.cypher`: `projection_key`),
  properties replaced (`SET pp = r.props`), relationships `MERGE`d, so re-running a week
  rewrites the same nodes and edges.
- **Batched:** `UNWIND $rows`, `BATCH` rows per transaction (as in `graph/load.py`).
- **Unlinked rows still count:** a projection whose `Player` or `Game` node is missing is
  written without that edge, and `ProjectionWriteResult` says how many were linked.
- **Fail-soft:** Neo4j down, bad credentials or any driver error -> `status="unavailable"`
  with `graph.client.safe_error` text (never the driver's message); it never raises into the
  weekly pipeline.
- **After every build:** the weekly graph build wipes the database, so the `player` step
  writes the week's projections after the graph step.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import polars as pl

from nflengine.graph.client import safe_error
from nflengine.models.player_schema import GROUP_SLUG

BATCH = 5_000
PROPS = (
    "player_id", "player", "team", "opponent", "game_id", "season", "week", "target",
    "target_label", "group", "unit", "kind", "is_main", "p10", "p50", "p90", "mean",
    "p_ge1", "p_ge2", "baseline", "baseline_p50", "baseline_p_ge1", "baseline_source",
    "outperformance", "outperf_z", "confidence", "model_version", "feature_hash",
    "trained_through",
)  # fmt: skip
MERGE_QUERY = (
    "UNWIND $rows AS r "
    "MERGE (pp:PlayerProjection {key: r.key}) "
    "SET pp = r.props "
    "WITH pp, r "
    "OPTIONAL MATCH (p:Player {player_id: r.player_id}) "
    "OPTIONAL MATCH (g:Game {game_id: r.game_id}) "
    "FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | "
    "MERGE (p)-[:HAS_PROJECTION]->(pp)) "
    "FOREACH (_ IN CASE WHEN g IS NULL THEN [] ELSE [1] END | "
    "MERGE (pp)-[:FOR_GAME]->(g)) "
    "RETURN count(pp) AS nodes, count(p) AS players, count(g) AS games"
)


@dataclass
class ProjectionWriteResult:
    status: str  # ok | unavailable | skipped (nothing to write)
    nodes: int = 0
    linked_players: int = 0
    linked_games: int = 0
    seconds: float = 0.0
    error: str | None = None  # `safe_error` text when unavailable

    @property
    def summary(self) -> str:
        """One log line (the weekly `player` step prints it)."""
        if self.status == "unavailable":
            return f"graph projections unavailable ({self.error})"
        if self.status == "skipped":
            return "graph projections: nothing to write"
        return (
            f"graph projections: {self.nodes} written ({self.linked_players} linked to "
            f"players, {self.linked_games} to games) in {self.seconds:.1f} s"
        )


def projection_key(r: dict[str, Any]) -> str:
    target_key = f"{r['target']}-{GROUP_SLUG.get(r['group'], r['group'])}"
    return f"{r['player_id']}|{r['game_id']}|{target_key}|{r['model_version']}"


def _value(v: Any) -> Any:
    """Polars values -> Neo4j-safe values (NaN -> null; datetimes -> ISO strings)."""
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, dt.datetime):
        return v.isoformat()
    return v


def projection_rows(preds: pl.DataFrame) -> list[dict[str, Any]]:
    """One `UNWIND` row per projection: `key`, the two endpoint ids and `props` (the
    `PROPS` columns present, `created_at` as ISO text, `top_drivers` = the driver phrases)."""
    out: dict[str, dict[str, Any]] = {}  # by key: a duplicate row keeps its last values
    for r in preds.iter_rows(named=True):
        props = {k: v for k in PROPS if k in r and (v := _value(r[k])) is not None}
        key = projection_key(r)
        props["key"] = key
        if r.get("created_at") is not None:
            props["created_at"] = _value(r["created_at"])
        phrases = [d.get("phrase") for d in (r.get("drivers") or []) if d.get("phrase")]
        if phrases:
            props["top_drivers"] = phrases
        out[key] = {
            "key": key,
            "player_id": r["player_id"],
            "game_id": r["game_id"],
            "props": props,
        }
    return list(out.values())


def write_projections(
    preds: pl.DataFrame,
    client: Any = None,
    log: Callable[[str], None] = print,
    batch_size: int = BATCH,
) -> ProjectionWriteResult:
    """Write the week's projections (`PRED_SCHEMA` rows) as `PlayerProjection` nodes.

    `client`: a Neo4j driver (anything with `.session()`); None opens one from the env
    settings and closes it afterwards. Never raises: a failure returns `unavailable`."""
    if preds.is_empty():
        return ProjectionWriteResult("skipped")
    t0 = time.perf_counter()
    own = client is None
    driver = client
    res = ProjectionWriteResult("ok")
    try:
        rows = projection_rows(preds)
        if own:
            from nflengine.graph.client import get_driver

            driver = get_driver()
        with driver.session() as session:
            for i in range(0, len(rows), batch_size):
                chunk = rows[i : i + batch_size]
                rec = session.execute_write(
                    lambda tx, c=chunk: tx.run(MERGE_QUERY, rows=c).single()
                )
                if rec is not None:
                    res.nodes += int(rec["nodes"])
                    res.linked_players += int(rec["players"])
                    res.linked_games += int(rec["games"])
    except Exception as e:  # fail-soft: Neo4j is optional for the weekly run
        err = safe_error(e)
        log(f"[yellow]player projections not written to the graph: {err}[/]")
        return ProjectionWriteResult("unavailable", error=err)
    finally:
        if own and driver is not None:
            with contextlib.suppress(Exception):  # closing a dead driver must not raise
                driver.close()
    res.seconds = time.perf_counter() - t0
    log(
        f"graph: {res.nodes} player projections written ({res.linked_players} linked to "
        f"players, {res.linked_games} to games)"
    )
    return res
