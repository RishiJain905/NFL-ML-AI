"""Write graph tables to Neo4j (documentation/05 -> Scope and update strategy; plan P05).

A **full rebuild** every run, never an incremental update, so the graph can't drift from
the curated Parquet:

1. `wipe`: delete everything in batches (`CALL { ... } IN TRANSACTIONS`);
2. `apply_schema`: `schema.cypher` (constraints and indexes, idempotent);
3. `load_tables`: nodes, then relationships, with batched `UNWIND $rows` writes (nodes with
   `CREATE` on constrained keys; relationships with `MATCH` on both keys + `CREATE`, the
   fast path for a fresh build);
4. `graph_counts`: node and relationship counts, for the load test and W&B.

Every write is batched (`batch_size` rows per transaction, 5-10k); never one transaction
per row.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from nflengine.graph.tables import NODE_KEYS, NODE_ORDER, REL_ENDS, REL_ORDER, GraphTables

if TYPE_CHECKING:
    from neo4j import Driver

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.cypher"
NODE_BATCH = 5_000
REL_BATCH = 10_000


def schema_statements(path: Path | None = None) -> list[str]:
    text = (path or SCHEMA_PATH).read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("//")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def apply_schema(driver: Driver) -> int:
    stmts = schema_statements()
    with driver.session() as session:
        for s in stmts:
            session.run(s).consume()
        session.run("CALL db.awaitIndexes(300)").consume()
    return len(stmts)


def wipe(driver: Driver, batch: int = REL_BATCH) -> None:
    """Delete every node and relationship, in batches (implicit transactions)."""
    with driver.session() as session:
        session.run(
            "MATCH ()-[r]->() CALL (r) { DELETE r } IN TRANSACTIONS OF $n ROWS", n=batch
        ).consume()
        session.run(
            "MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF $n ROWS", n=batch
        ).consume()


def _clean(v: Any) -> Any:
    """Polars values -> Neo4j-safe values (NaN -> null)."""
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def rows(df: pl.DataFrame) -> list[dict[str, Any]]:
    return [{k: _clean(v) for k, v in r.items()} for r in df.iter_rows(named=True)]


def batches(items: list[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def node_query(label: str) -> str:
    return f"UNWIND $rows AS r CREATE (n:{label}) SET n = r"


def rel_query(rel: str) -> str:
    start, end = REL_ENDS[rel]
    sk, ek = NODE_KEYS[start], NODE_KEYS[end]
    return (
        f"UNWIND $rows AS r "
        f"MATCH (a:{start} {{{sk}: r.s}}) MATCH (b:{end} {{{ek}: r.e}}) "
        f"CREATE (a)-[x:{rel}]->(b) SET x = r.p"
    )


def rel_rows(df: pl.DataFrame) -> list[dict[str, Any]]:
    props = [c for c in df.columns if c not in ("s", "e")]
    out = []
    for r in df.iter_rows(named=True):
        out.append(
            {"s": r["s"], "e": r["e"], "p": {k: _clean(r[k]) for k in props if r[k] is not None}}
        )
    return out


def _write(driver: Driver, query: str, items: list[dict[str, Any]], size: int) -> None:
    with driver.session() as session:
        for chunk in batches(items, size):
            session.execute_write(lambda tx, c=chunk: tx.run(query, rows=c).consume())


def load_tables(
    driver: Driver,
    tables: GraphTables,
    *,
    node_batch: int = NODE_BATCH,
    rel_batch: int = REL_BATCH,
    on_step: Callable[[str, float, int], None] | None = None,
) -> dict[str, float]:
    """Write every node table, then every relationship table. Returns seconds per table."""
    timings: dict[str, float] = {}
    for label in NODE_ORDER:
        df = tables.nodes.get(label)
        if df is None or df.is_empty():
            continue
        t0 = time.perf_counter()
        _write(driver, node_query(label), rows(df), node_batch)
        timings[f"node:{label}"] = time.perf_counter() - t0
        if on_step:
            on_step(f"node:{label}", timings[f"node:{label}"], df.height)
    for rel in REL_ORDER:
        df = tables.rels.get(rel)
        if df is None or df.is_empty():
            continue
        t0 = time.perf_counter()
        _write(driver, rel_query(rel), rel_rows(df), rel_batch)
        timings[f"rel:{rel}"] = time.perf_counter() - t0
        if on_step:
            on_step(f"rel:{rel}", timings[f"rel:{rel}"], df.height)
    return timings


def graph_counts(driver: Driver) -> dict[str, int]:
    """Nodes per label and relationships per type, as `node:<label>` / `rel:<type>`."""
    out: dict[str, int] = {}
    with driver.session() as session:
        for label in NODE_ORDER:
            out[f"node:{label}"] = session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()[
                "c"
            ]
        for rel in REL_ORDER:
            out[f"rel:{rel}"] = session.run(
                f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c"
            ).single()["c"]
    return out


def count_mismatches(
    expected: dict[str, int], actual: dict[str, int]
) -> dict[str, tuple[int, int]]:
    """Keys whose Neo4j count differs from what the tables predict: {key: (expected, actual)}."""
    keys = set(expected) | {k for k, v in actual.items() if v}
    return {
        k: (expected.get(k, 0), actual.get(k, 0))
        for k in sorted(keys)
        if expected.get(k, 0) != actual.get(k, 0)
    }
