"""The published-insight log: novelty that survives the weekly rebuild (plan P05).

The graph is wiped every week, so what the digest already said can't live only in Neo4j.
Each published digest appends its graph items to a small Parquet log on D:
(`runs/published_insights.parquet`; backtests keep their own in `runs/digest-backtests/`),
and every build loads the log's earlier weeks as `PublishedInsight` nodes. An insight
published in any of the 3 weeks before the run's week (same season) is not picked again.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from nflengine.digest.payload import GraphInsight

if TYPE_CHECKING:
    from neo4j import Driver

LOG_NAME = "published_insights.parquet"
NOVELTY_WEEKS = 3
SCHEMA = {
    "season": pl.Int32,
    "week": pl.Int32,
    "insight_id": pl.String,
    "insight_type": pl.String,
    "section": pl.String,
    "game_id": pl.String,
    "entities": pl.List(pl.String),
    "published_at": pl.Datetime("us", "UTC"),
}


def log_path(run_root: Path) -> Path:
    return run_root / LOG_NAME


def read_log(path: Path) -> pl.DataFrame:
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=SCHEMA)


def entities(ins: GraphInsight) -> list[str]:
    return [*ins.teams, *[p.player for p in ins.people]]


def record(
    path: Path,
    season: int,
    week: int,
    picked: Iterable[GraphInsight],
    published_at: dt.datetime,
) -> pl.DataFrame:
    """Add this run's picks to the week's rows. Earlier picks of the same week stay: they
    were published, so a re-run (say, after one of their games kicked off and dropped out)
    must not erase them and let the story return next week (Sol review). A pick logged
    twice keeps its latest row."""
    old = read_log(path)
    rows = [
        {
            "season": season,
            "week": week,
            "insight_id": i.insight_id,
            "insight_type": i.insight_type,
            "section": i.section,
            "game_id": i.game_id,
            "entities": entities(i),
            "published_at": published_at,
        }
        for i in picked
    ]
    new = pl.DataFrame(rows, schema=SCHEMA)
    out = (
        pl.concat([old.cast(SCHEMA), new])  # type: ignore[arg-type]
        .unique(["season", "week", "insight_id"], keep="last", maintain_order=True)
        .sort("season", "week", "insight_id")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(path)
    return out


def recent_from_log(log: pl.DataFrame, season: int, week: int) -> list[str]:
    """Insight ids published in the `NOVELTY_WEEKS` weeks before `week` of `season`."""
    if log.is_empty():
        return []
    return sorted(
        set(
            log.filter(
                (pl.col("season") == season)
                & (pl.col("week") < week)
                & (pl.col("week") >= week - NOVELTY_WEEKS)
            )["insight_id"].to_list()
        )
    )


RECENT_QUERY = (
    "MATCH (pi:PublishedInsight) WHERE pi.season = $season AND pi.week < $week "
    "AND pi.week >= $week - $weeks RETURN DISTINCT pi.insight_id AS id ORDER BY id"
)


def recent_from_graph(driver: Driver, season: int, week: int) -> list[str]:
    """The same window, read from the `PublishedInsight` nodes of the current graph."""
    with driver.session() as session:
        return session.execute_read(
            lambda tx: [
                r["id"] for r in tx.run(RECENT_QUERY, season=season, week=week, weeks=NOVELTY_WEEKS)
            ]
        )


MERGE_QUERY = (
    "UNWIND $rows AS r MERGE (pi:PublishedInsight {key: r.key}) "
    "SET pi.insight_id = r.insight_id, pi.type = r.type, pi.entities = r.entities, "
    "pi.season = r.season, pi.week = r.week, pi.published_at = r.published_at"
)


def write_nodes(
    driver: Driver,
    season: int,
    week: int,
    picked: Iterable[GraphInsight],
    published_at: dt.datetime,
) -> int:
    """Add this week's published items to the live graph (the log is the source of truth;
    the next rebuild reloads them from it)."""
    rows = [
        {
            "key": f"{season}-w{week}:{i.insight_id}",
            "insight_id": i.insight_id,
            "type": i.insight_type,
            "entities": entities(i),
            "season": season,
            "week": week,
            "published_at": published_at,
        }
        for i in picked
    ]
    if not rows:
        return 0
    with driver.session() as session:
        session.execute_write(lambda tx: tx.run(MERGE_QUERY, rows=rows).consume())
    return len(rows)
