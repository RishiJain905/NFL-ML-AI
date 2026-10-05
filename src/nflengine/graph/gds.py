"""Graph Data Science jobs of the weekly build (documentation/05 -> Q10; plan P08).

Build order (doc 05): load -> model outputs -> **GDS jobs** -> query library. The jobs read
the freshly loaded, already as-of graph, run GDS algorithms on small in-memory projections
and write the results back as relationships the library queries read:

- **Passing network** (`pass_network`): the current season's `THREW_TO` edges (as of the
  build: only visible targets) as an undirected weighted graph (weight = targets), one
  relationship type per team, projected with a Cypher aggregation (`gds.graph.project`);
  per team, weighted degree and PageRank over that team's type (`stream` mode). The team's
  top receiver by PageRank is its "hub". Then this week's network: a second projection
  without the pass catchers who won't play this week (the library's Q0 rows), PageRank
  again for those teams: who leads the network without them.
  Written as `(Player)-[:PASS_CENTRALITY {season, week, team_id, role, pagerank, degree,
  share, rank, hub_id, out_this_week, share_this_week, rank_this_week}]->(Team)`. `share` =
  his part of the team's receiver PageRank; `role` = passer (QBs) or receiver.
- **Player similarity** (`player_similarity`): per position group (WR, TE, RB), the
  `UsageProfile` nodes (one per player-season, z-scored usage vectors; `graph/tables_extra`)
  projected with labels `Current` (this season) and `Past`, then filtered k-nearest
  neighbors (Euclidean on the vectors) from each current profile to past ones. The top
  `SIMILAR_TOP` other players' seasons are written as `(Player)-[:SIMILAR_TO {season,
  other_season, other_team, group, score, rank, basis, week}]->(Player)`. A comparison of
  usage, never a prediction.

Fail-soft: every job runs on its own; an error (GDS missing, out of memory, a bad
projection) is recorded with `safe_error` text, the job's projections are dropped, and the
build goes on: the library queries that read a job's output simply return no rows.
Projections are named `nfl-<job>-<part>` and dropped after use; leftovers of a crashed run
are dropped first.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nflengine.graph.client import safe_error

if TYPE_CHECKING:
    from neo4j import Driver

    from nflengine.graph.tables import GraphKey

PREFIX = "nfl-"
PAGERANK = {"dampingFactor": 0.85, "maxIterations": 40, "tolerance": 1e-7}
KNN = {
    "topK": 10,  # before dropping the player's own seasons
    "sampleRate": 1.0,
    "deltaThreshold": 0.0,
    "maxIterations": 100,
    "randomSeed": 42,  # with concurrency 1: the same neighbors every run
    "concurrency": 1,
}
SIMILAR_TOP = 3
SIMILAR_GROUPS = ("WR", "TE", "RB")
# relationships the GDS jobs write (after the count check; not in graph/tables.py's tables)
GDS_RELS = ("PASS_CENTRALITY", "SIMILAR_TO")


@dataclass
class GdsResult:
    status: str  # ok | partial | unavailable | skipped
    version: str | None = None
    seconds: float = 0.0
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "version": self.version,
            "seconds": round(self.seconds, 2),
            "jobs": self.jobs,
            "error": self.error,
        }

    @property
    def summary(self) -> str:
        if self.status in ("unavailable", "skipped"):
            return f"GDS {self.status} ({self.error})"
        parts = [f"{n} {j['status']} in {j.get('seconds', 0):.1f} s" for n, j in self.jobs.items()]
        return f"GDS {self.version}: " + "; ".join(parts)


# ---- projections ---------------------------------------------------------------------------------


def drop_projections(driver: Driver, prefix: str = PREFIX) -> list[str]:
    """Drop every in-memory projection whose name starts with `prefix` (best effort)."""
    with driver.session() as s:
        names = s.run(
            "CALL gds.graph.list() YIELD graphName WHERE graphName STARTS WITH $p "
            "RETURN collect(graphName) AS names",
            p=prefix,
        ).single()["names"]
        for n in names:
            s.run("CALL gds.graph.drop($n, false) YIELD graphName RETURN graphName", n=n).consume()
    return list(names)


# one projection holds every team's network: each THREW_TO edge becomes a relationship of
# type <team_id> (a player traded mid-season is one node with edges of two types); an
# algorithm run with `relationshipTypes: [team]` sees only that team's edges. `$exclude`
# ("KC:<player_id>") leaves a team's edges to those receivers out. (One type per team, not
# per team and receiver: ~1,000 relationship types ran the 2 GB heap out of memory.)
PASS_PROJECT = """
MATCH (q:Player)-[t:THREW_TO {season: $season}]->(r:Player)
WHERE t.team_id IS NOT NULL AND NOT (t.team_id + ':' + r.player_id) IN $exclude
WITH gds.graph.project($name, q, r,
       {relationshipType: t.team_id,
        relationshipProperties: {targets: toFloat(t.targets)}},
       {undirectedRelationshipTypes: ['*']}) AS g
RETURN g.nodeCount AS nodes, g.relationshipCount AS rels
"""
DEGREE_STREAM = """
CALL gds.degree.stream($name, {relationshipTypes: [$team],
                               relationshipWeightProperty: 'targets'})
YIELD nodeId, score
WITH nodeId, score WHERE score > 0
RETURN nodeId, score
"""
PAGERANK_STREAM = """
CALL gds.pageRank.stream($name, $config)
YIELD nodeId, score
WITH nodeId, score WHERE nodeId IN $ids
WITH nodeId, score, gds.util.asNode(nodeId) AS p
RETURN nodeId, p.player_id AS player_id, p.name AS name, p.position_group AS grp, score
"""
WRITE_CENTRALITY = """
UNWIND $rows AS r
MATCH (p:Player {player_id: r.player_id})
MATCH (t:Team {team_id: r.team_id})
MERGE (p)-[c:PASS_CENTRALITY {season: r.season, team_id: r.team_id}]->(t)
SET c += r.props
"""
DROP = "CALL gds.graph.drop($n, false) YIELD graphName RETURN graphName"
OUT_GROUPS = ("WR", "TE", "RB")


def _project(session, name: str, season: int, exclude: list[str]) -> bool:
    rec = session.run(PASS_PROJECT, name=name, season=season, exclude=exclude).single()
    return rec is not None and bool(rec["nodes"])


def _team_pagerank(session, name: str, team: str, ids: list[int]) -> dict[str, Any]:
    """PageRank over one team's edges, for the given nodes: {player_id: (score, group)}."""
    cfg = {**PAGERANK, "relationshipTypes": [team], "relationshipWeightProperty": "targets"}
    pr: dict[str, tuple[float, str | None]] = {}
    names: dict[str, str] = {}
    node_of: dict[str, int] = {}
    for r in session.run(PAGERANK_STREAM, name=name, config=cfg, ids=ids):
        pr[r["player_id"]] = (r["score"], r["grp"])
        names[r["player_id"]] = r["name"]
        node_of[r["player_id"]] = r["nodeId"]
    return {"pr": pr, "names": names, "node_of": node_of}


def _shares(pr: dict[str, tuple[float, str | None]]) -> dict[str, tuple[float, int]]:
    """Receiver shares of PageRank (QBs left out) and their ranks (1 = the hub)."""
    rec = {pid: s for pid, (s, grp) in pr.items() if grp != "QB"}
    total = sum(rec.values()) or 1.0
    order = sorted(rec, key=lambda p: (-rec[p], p))
    return {pid: (rec[pid] / total, i + 1) for i, pid in enumerate(order)}


def out_receivers(driver: Driver, key: GraphKey) -> dict[str, set[str]]:
    """Pass catchers who won't play this week, per team: the rows of the library's Q0
    (`q0_starters_out`: listed Out / Doubtful, on a reserve list, or ruled out of the last
    game before this week's report), WR / TE / RB only."""
    from nflengine.graph.queries import run_query

    res = run_query(driver, "q0_starters_out", key.season, key.week)
    if res.error:
        raise RuntimeError(f"q0_starters_out failed ({res.error})")
    out: dict[str, set[str]] = {}
    for r in res.rows:
        if r.get("position_group") in OUT_GROUPS:
            out.setdefault(r["team"], set()).add(r["player_id"])
    return out


def pass_network(driver: Driver, key: GraphKey) -> dict[str, Any]:
    """PageRank + weighted degree on every team's passing network this season; then, for the
    teams with pass catchers who won't play this week (Q0), PageRank again on the network
    without them: who leads it this week. Writes PASS_CENTRALITY. Two projections in all.
    Returns counts and each team's hub (for the build record)."""
    season, week = key.season, key.week
    full_name, week_name = f"{PREFIX}pass", f"{PREFIX}pass-this-week"
    rows: list[dict[str, Any]] = []
    hubs: dict[str, dict[str, Any]] = {}
    with driver.session() as s:
        teams = [
            r["team"]
            for r in s.run(
                "MATCH ()-[t:THREW_TO {season: $season}]->() WHERE t.team_id IS NOT NULL "
                "RETURN DISTINCT t.team_id AS team ORDER BY team",
                season=season,
            )
        ]
        if not teams or not _project(s, full_name, season, []):
            return {"teams": 0, "written": 0, "hubs": {}, "out": 0}
        nets: dict[str, dict[str, Any]] = {}
        try:
            for team in teams:
                deg = {
                    r["nodeId"]: r["score"] for r in s.run(DEGREE_STREAM, name=full_name, team=team)
                }
                net = _team_pagerank(s, full_name, team, list(deg))
                net["deg"] = {pid: deg.get(n, 0.0) for pid, n in net["node_of"].items()}
                net["shares"] = _shares(net["pr"])
                if net["shares"]:
                    nets[team] = net
        finally:
            s.run(DROP, n=full_name).consume()
        # who won't play this week, among each network's receivers
        outs = {
            t: {p for p in ps if p in nets[t]["shares"]}
            for t, ps in out_receivers(driver, key).items()
            if t in nets
        }
        outs = {t: ps for t, ps in outs.items() if ps}
        this_week: dict[str, dict[str, tuple[float, int]]] = {}
        exclude = [f"{t}:{p}" for t, ps in outs.items() for p in ps]
        if exclude and _project(s, week_name, season, exclude):
            try:
                for team, ps in outs.items():
                    ids = [n for pid, n in nets[team]["node_of"].items() if pid not in ps]
                    this_week[team] = _shares(_team_pagerank(s, week_name, team, ids)["pr"])
            finally:
                s.run(DROP, n=week_name).consume()
        for team, net in nets.items():
            shares = net["shares"]
            hub = min(shares, key=lambda p, sh=shares: sh[p][1])
            hubs[team] = {
                "hub_id": hub,
                "hub": net["names"].get(hub),
                "share": round(shares[hub][0], 4),
            }
            wk = this_week.get(team, {})
            for pid, (score, grp) in net["pr"].items():
                props: dict[str, Any] = {
                    "week": week,
                    "role": "passer" if grp == "QB" else "receiver",
                    "pagerank": round(score, 5),
                    "degree": round(net["deg"].get(pid, 0.0), 1),
                    "hub_id": hub,
                    "out_this_week": pid in outs.get(team, set()),
                }
                if pid in shares:
                    props["share"] = round(shares[pid][0], 4)
                    props["rank"] = shares[pid][1]
                if pid in wk:
                    props["share_this_week"] = round(wk[pid][0], 4)
                    props["rank_this_week"] = wk[pid][1]
                rows.append({"player_id": pid, "team_id": team, "season": season, "props": props})
        s.execute_write(lambda tx: tx.run(WRITE_CENTRALITY, rows=rows).consume())
    return {
        "teams": len(nets),
        "written": len(rows),
        "hubs": hubs,
        "out": sum(len(ps) for ps in outs.values()),
    }


USAGE_PROJECT = """
MATCH (u:UsageProfile {group: $group})
WITH gds.graph.project($name, u, null, {
       sourceNodeLabels: CASE WHEN u.current THEN ['Current'] ELSE ['Past'] END,
       targetNodeLabels: NULL,
       sourceNodeProperties: {vector: u.vector},
       targetNodeProperties: NULL}) AS g
RETURN g.nodeCount AS nodes
"""
KNN_STREAM = """
CALL gds.knn.filtered.stream($name, $config)
YIELD node1, node2, similarity
WITH gds.util.asNode(node1) AS a, gds.util.asNode(node2) AS b, similarity
WHERE a.player_id <> b.player_id
RETURN a.player_id AS player_id, b.player_id AS other_id, b.season AS other_season,
       b.team_id AS other_team, b.basis AS basis, similarity
ORDER BY player_id, similarity DESC, other_id, other_season
"""
WRITE_SIMILAR = """
UNWIND $rows AS r
MATCH (a:Player {player_id: r.player_id})
MATCH (b:Player {player_id: r.other_id})
MERGE (a)-[x:SIMILAR_TO {season: r.season, other_season: r.other_season}]->(b)
SET x += r.props
"""


def player_similarity(driver: Driver, key: GraphKey) -> dict[str, Any]:
    """Filtered KNN from this season's usage profiles to past seasons, per position group;
    writes SIMILAR_TO (the `SIMILAR_TOP` closest other players' seasons)."""
    rows: list[dict[str, Any]] = []
    per_group: dict[str, int] = {}
    with driver.session() as s:
        for group in SIMILAR_GROUPS:
            n_cur = s.run(
                "MATCH (u:UsageProfile {group: $g}) RETURN count(CASE WHEN u.current THEN 1 END) "
                "AS cur, count(CASE WHEN NOT u.current THEN 1 END) AS past",
                g=group,
            ).single()
            if not n_cur or not n_cur["cur"] or not n_cur["past"]:
                per_group[group] = 0
                continue
            name = f"{PREFIX}usage-{group}"
            s.run(USAGE_PROJECT, name=name, group=group).consume()
            try:
                cfg = {
                    **KNN,
                    "nodeProperties": {"vector": "EUCLIDEAN"},
                    "sourceNodeFilter": "Current",
                    "targetNodeFilter": "Past",
                }
                found = list(s.run(KNN_STREAM, name=name, config=cfg))
            finally:
                s.run(DROP, n=name).consume()
            ranks: dict[str, int] = {}
            n = 0
            for r in found:
                k = ranks.get(r["player_id"], 0)
                if k >= SIMILAR_TOP:
                    continue
                ranks[r["player_id"]] = k + 1
                n += 1
                rows.append(
                    {
                        "player_id": r["player_id"],
                        "other_id": r["other_id"],
                        "season": key.season,
                        "other_season": r["other_season"],
                        "props": {
                            "week": key.week,
                            "group": group,
                            "other_team": r["other_team"],
                            "score": round(float(r["similarity"]), 4),
                            "rank": k + 1,
                            "basis": r["basis"],
                        },
                    }
                )
            per_group[group] = n
        s.execute_write(lambda tx: tx.run(WRITE_SIMILAR, rows=rows).consume())
    return {"written": len(rows), "per_group": per_group}


JOBS: dict[str, Callable[[Driver, GraphKey], dict[str, Any]]] = {
    "pass_network": pass_network,
    "player_similarity": player_similarity,
}


def run_gds_jobs(driver: Driver, key: GraphKey, *, log: Callable[[str], None] = print) -> GdsResult:
    """Run every GDS job; never raises (see the module docstring)."""
    t_all = time.perf_counter()
    try:
        with driver.session() as s:
            version = s.run("RETURN gds.version() AS v").single()["v"]
        drop_projections(driver)
    except Exception as e:  # GDS not installed / not allowed / Neo4j gone
        err = safe_error(e)
        log(f"[yellow]graph: GDS unavailable ({err}); GDS insights skipped[/]")
        return GdsResult("unavailable", seconds=time.perf_counter() - t_all, error=err)
    res = GdsResult("ok", version=version)
    for name, job in JOBS.items():
        t0 = time.perf_counter()
        try:
            info = job(driver, key)
            res.jobs[name] = {"status": "ok", "seconds": round(time.perf_counter() - t0, 2), **info}
        except Exception as e:  # one failed job never sinks the build or the other job
            err = safe_error(e)
            res.jobs[name] = {
                "status": "failed",
                "seconds": round(time.perf_counter() - t0, 2),
                "error": err,
            }
            log(f"[yellow]graph: GDS job {name} failed ({err}); its insights are skipped[/]")
        finally:
            # best effort: the next build drops leftovers first
            with contextlib.suppress(Exception):
                drop_projections(driver)
    failed = [n for n, j in res.jobs.items() if j["status"] != "ok"]
    if failed:
        res.status = "partial" if len(failed) < len(res.jobs) else "unavailable"
        res.error = f"failed: {', '.join(failed)}"
    res.seconds = time.perf_counter() - t_all
    return res
