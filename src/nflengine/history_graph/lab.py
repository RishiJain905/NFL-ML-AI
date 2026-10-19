"""`nfl history-graph lab [--job neighbours|families|chemistry]` (AE03): graph experiments on
the person links, with Neo4j Graph Data Science (GDS) on the history graph.

- **neighbours** ("players most like X by who they played with and how"): FastRP embeddings
  of the players on the TEAMMATE + CONNECTION graph (links weighted by log10(1 + snaps), a
  QB -> receiver link by its targets), then the k nearest neighbours by cosine similarity:
  GDS KNN over everyone (`scope: overall`) and an exact search within the player's position
  group (`scope: position`). A 2-D PCA projection of the embeddings is the map.
- **families** ("football families"): Leiden communities on TEAMMATE + COACHED + WORKED_WITH
  (players and coaches, weighted by log10(1 + snaps)), each named by its biggest team-era
  ("2019-2021 KC offense"), with sizes and members.
- **chemistry**: QB -> receiver duos ranked by EPA per target (a minimum of targets), and
  PageRank on the QB <-> receiver graph (weighted by targets) for the most central receivers
  and quarterbacks.

Every job projects its own in-memory GDS graph (named after the job and the derive it reads,
dropped afterwards), streams the results (never writes to the database: the person links
stay as the derive made them), and writes `{NFL_DATA_ROOT}/neo4j-history/lab/<job>*.parquet`
plus `lab/lab.json` (the derive, the seed, the parameters, the counts, the times, the W&B run);
a W&B run per job (`job_type: history-graph-lab`). Random-seeded steps (FastRP, KNN, Leiden)
take the seed and run on one thread, so a run can be reproduced. Research only (D107): nothing
here feeds a weekly model or the digest.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any

import numpy as np
import polars as pl

from nflengine.history_graph import state
from nflengine.history_graph.config import HistoryConfig, history_config
from nflengine.paths import DataPaths, ensure_data_root

JOBS = ("neighbours", "families", "chemistry")
SEED = 42
LAB_FILE = "lab.json"
NEIGHBOURS = {
    "dimension": 128,
    "iteration_weights": [0.0, 1.0, 1.0, 0.5],
    "normalization_strength": 0.0,
    "connection_weight": 3.0,  # a target counts like 3 snaps together
    "k": 10,
    "min_snaps": 100,  # players with fewer snaps (2018 on) aren't anyone's neighbour
}
# resolution 16: at 1-4 the communities are whole franchises, at 8 a franchise's offense and
# defense, at 16 its eras ("2018-2021 KC offense" vs "2021-2025 KC offense"; probe 2026-10-19)
FAMILIES = {"gamma": 16.0, "theta": 0.01, "max_levels": 10, "min_size": 5}
CHEMISTRY = {"min_targets": 100, "pagerank_damping": 0.85, "pagerank_iterations": 40}
UNIT_OF = {"QB": "offense", "RB": "offense", "WR": "offense", "TE": "offense", "OL": "offense",
           "DL": "defense", "LB": "defense", "DB": "defense", "SPEC": "special teams"}  # fmt: skip


class HistoryLabError(RuntimeError):
    """A lab job can't run (fixed text)."""


def lab_dir(paths: DataPaths):
    return paths.neo4j_history / "lab"


def lab_path(paths: DataPaths, name: str):
    return lab_dir(paths) / name


def _write_parquet(df: pl.DataFrame, path) -> None:
    from nflengine.fsutil import replace_file

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    df.write_parquet(tmp)
    replace_file(tmp, path)


def read_lab(paths: DataPaths) -> dict[str, Any]:
    """`lab.json` (`{job: {...}}`), `{}` before the first run."""
    return state._read(lab_path(paths, LAB_FILE)) or {}


def _write_lab(paths: DataPaths, job: str, entry: dict[str, Any]) -> None:
    from nflengine.history_graph.build import _write_json

    doc = read_lab(paths)
    doc[job] = entry
    _write_json(lab_path(paths, LAB_FILE), doc)


# ---- GDS helpers ------------------------------------------------------------------------------


def _drop(session, name: str) -> None:
    session.run("CALL gds.graph.drop($n, false) YIELD graphName RETURN graphName", n=name).consume()


def _project(session, name: str, match: str, weight: str, extra: str = "") -> dict[str, int]:
    """A Cypher-aggregation projection, undirected, with one weight `w`."""
    _drop(session, name)
    rec = session.run(
        f"{match} WITH gds.graph.project($n, a, b, {{relationshipProperties: {{w: {weight}}}"
        f"{extra}}}, {{undirectedRelationshipTypes: ['*']}}) AS g "
        "RETURN g.nodeCount AS nodes, g.relationshipCount AS rels",
        n=name,
    ).single()
    return {"nodes": int(rec["nodes"]), "rels": int(rec["rels"])}


def _people(session) -> pl.DataFrame:
    rows = session.run(
        "MATCH (n:Player) RETURN 'player' AS kind, n.player_id AS id, n.name AS name, "
        "n.position AS position, n.position_group AS grp "
        "UNION ALL MATCH (n:Coach) RETURN 'coach' AS kind, n.coach_id AS id, n.name AS name, "
        "'coach' AS position, 'COACH' AS grp"
    ).data()
    return pl.DataFrame(rows, schema={"kind": pl.String, "id": pl.String, "name": pl.String,
                                      "position": pl.String, "grp": pl.String})  # fmt: skip


def _people_index(paths: DataPaths) -> pl.DataFrame:
    """The derive's people index (team, seasons, snaps), or an empty frame."""
    from nflengine.history_graph.connections import people_path

    p = people_path(paths)
    if not p.exists():
        return pl.DataFrame(schema={"id": pl.String, "team": pl.String, "snaps": pl.Int64})
    return pl.read_parquet(p, memory_map=False).select(
        "id", "team", "teams", "first_season", "last_season", "snaps"
    )


# ---- neighbours -------------------------------------------------------------------------------


def job_neighbours(session, paths: DataPaths, derive_id: str, seed: int, log) -> dict[str, Any]:
    p = NEIGHBOURS
    name = f"lab-neighbours-{derive_id}"
    idx = _people_index(paths)
    keep = idx.filter(pl.col("snaps") >= p["min_snaps"])["id"].to_list()
    proj = _project_keep(session, name, keep, p["connection_weight"])
    log(f"lab neighbours: projected {proj['nodes']:,} players, {proj['rels']:,} links")
    t = time.monotonic()
    rows = session.run(
        "CALL gds.fastRP.stream($n, {embeddingDimension: $dim, iterationWeights: $iw, "
        "normalizationStrength: $ns, relationshipWeightProperty: 'w', randomSeed: $seed, "
        "concurrency: 1}) YIELD nodeId, embedding "
        "RETURN gds.util.asNode(nodeId).player_id AS id, embedding",
        n=name, dim=p["dimension"], iw=p["iteration_weights"], ns=p["normalization_strength"],
        seed=seed,
    ).data()  # fmt: skip
    t_fastrp = round(time.monotonic() - t, 1)
    ids = [r["id"] for r in rows]
    emb = np.asarray([r["embedding"] for r in rows], dtype=np.float64)
    # GDS KNN over everyone (the embedding written into the projection only)
    t = time.monotonic()
    session.run(
        "CALL gds.fastRP.mutate($n, {embeddingDimension: $dim, iterationWeights: $iw, "
        "normalizationStrength: $ns, relationshipWeightProperty: 'w', randomSeed: $seed, "
        "concurrency: 1, mutateProperty: 'emb'}) YIELD nodePropertiesWritten RETURN 1",
        n=name, dim=p["dimension"], iw=p["iteration_weights"], ns=p["normalization_strength"],
        seed=seed,
    ).consume()  # fmt: skip
    knn = session.run(
        "CALL gds.knn.stream($n, {nodeProperties: ['emb'], topK: $k, randomSeed: $seed, "
        "concurrency: 1, sampleRate: 1.0, deltaThreshold: 0.0, randomJoins: 10}) "
        "YIELD node1, node2, similarity "
        "RETURN gds.util.asNode(node1).player_id AS a, gds.util.asNode(node2).player_id AS b, "
        "similarity",
        n=name, k=p["k"], seed=seed,
    ).data()  # fmt: skip
    t_knn = round(time.monotonic() - t, 1)
    _drop(session, name)

    people = _people(session).filter(pl.col("kind") == "player").drop("kind")
    meta = people.join(idx.select("id", "team", "snaps"), on="id", how="left")
    overall = pl.DataFrame(knn, schema={"a": pl.String, "b": pl.String, "similarity": pl.Float64})
    overall = overall.sort(["a", "similarity", "b"], descending=[False, True, False]).with_columns(
        pl.col("similarity").rank("ordinal", descending=True).over("a").cast(pl.Int64)
        .alias("rank"),
        pl.lit("overall").alias("scope"),
    )  # fmt: skip
    position = _exact_knn(ids, emb, meta, p["k"])
    nb = pl.concat([overall.select("scope", "a", "rank", "b", "similarity"),
                    position.select("scope", "a", "rank", "b", "similarity")])  # fmt: skip
    shared = _shared_snaps(session, nb)
    nb = (
        nb.join(shared, on=["a", "b"], how="left")
        .with_columns(pl.col("snaps_together").fill_null(0))
        .join(meta.rename({"id": "a", "name": "name", "position": "position", "grp": "grp",
                           "team": "team", "snaps": "snaps"}), on="a", how="left")
        .join(meta.rename({"id": "b", "name": "neighbour_name", "position":
                           "neighbour_position", "grp": "neighbour_grp", "team": "neighbour_team",
                           "snaps": "neighbour_snaps"}), on="b", how="left")
        .rename({"a": "player_id", "b": "neighbour_id"})
        .with_columns(pl.col("similarity").round(4))
        .sort("scope", "player_id", "rank")
    )  # fmt: skip
    # the 2-D map: PCA of the embeddings
    xy = _pca2(emb)
    mp = pl.DataFrame({"player_id": ids, "x": xy[:, 0].round(4), "y": xy[:, 1].round(4)}).join(
        meta.rename({"id": "player_id"}), on="player_id", how="left"
    )
    return {
        "params": {**p, "seed": seed},
        "projection": proj,
        "players": len(ids),
        "rows": nb.height,
        "seconds": {"fastrp": t_fastrp, "knn": t_knn},
        "explained_variance_2d": [round(float(v), 4) for v in _pca_var(emb)],
        "_frames": {"neighbours": nb, "map": mp},
    }


def _project_keep(session, name: str, keep: list[str], connection_weight: float):
    _drop(session, name)
    rec = session.run(
        "MATCH (a:Player)-[r:TEAMMATE|CONNECTION]->(b:Player) "
        "WHERE a.player_id IN $keep AND b.player_id IN $keep "
        "WITH gds.graph.project($n, a, b, {relationshipProperties: {w: log10(1.0 + CASE "
        "type(r) WHEN 'TEAMMATE' THEN toFloat(r.snaps) ELSE $cw * r.targets END)}}, "
        "{undirectedRelationshipTypes: ['*']}) AS g "
        "RETURN g.nodeCount AS nodes, g.relationshipCount AS rels",
        n=name,
        keep=keep,
        cw=float(connection_weight),
    ).single()
    return {"nodes": int(rec["nodes"]), "rels": int(rec["rels"])}


def _exact_knn(ids: list[str], emb: np.ndarray, meta: pl.DataFrame, k: int) -> pl.DataFrame:
    """The k most similar players of the same position group (cosine, exact)."""
    grp = dict(zip(meta["id"].to_list(), meta["grp"].to_list(), strict=False))
    norm = emb / np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-12, None)
    out: list[dict[str, Any]] = []
    groups: dict[str | None, list[int]] = {}
    for i, pid in enumerate(ids):
        groups.setdefault(grp.get(pid), []).append(i)
    for g, members in groups.items():
        if g is None or len(members) < 2:
            continue
        m = np.asarray(members)
        sims = norm[m] @ norm[m].T
        np.fill_diagonal(sims, -np.inf)
        for row, i in enumerate(m):
            top = np.argsort(-sims[row], kind="stable")[:k]
            for rank, j in enumerate(top, start=1):
                if not np.isfinite(sims[row, j]):
                    continue
                out.append({"a": ids[i], "rank": rank, "b": ids[m[j]],
                            "similarity": float(sims[row, j])})  # fmt: skip
    return pl.DataFrame(out, schema={"a": pl.String, "rank": pl.Int64, "b": pl.String,
                                     "similarity": pl.Float64}).with_columns(
        pl.lit("position").alias("scope")
    )  # fmt: skip


def _shared_snaps(session, nb: pl.DataFrame) -> pl.DataFrame:
    """TEAMMATE snaps between each player and his neighbours (0 = never on the field together)."""
    pairs = nb.select("a", "b").unique()
    lo = pairs.select(
        pl.min_horizontal("a", "b").alias("s"), pl.max_horizontal("a", "b").alias("e")
    )
    rows = session.run(
        "UNWIND $pairs AS pr MATCH (x:Player {player_id: pr.s})-[t:TEAMMATE]->"
        "(y:Player {player_id: pr.e}) RETURN pr.s AS s, pr.e AS e, t.snaps AS snaps",
        pairs=lo.unique().to_dicts(),
    ).data()
    got = pl.DataFrame(rows, schema={"s": pl.String, "e": pl.String, "snaps": pl.Int64})
    both = pl.concat(
        [got.select(pl.col("s").alias("a"), pl.col("e").alias("b"), "snaps"),
         got.select(pl.col("e").alias("a"), pl.col("s").alias("b"), "snaps")]
    )  # fmt: skip
    return both.rename({"snaps": "snaps_together"}).unique(["a", "b"])


def _pca2(x: np.ndarray) -> np.ndarray:
    c = x - x.mean(axis=0, keepdims=True)
    _u, _s, vt = np.linalg.svd(c, full_matrices=False)
    return c @ vt[:2].T


def _pca_var(x: np.ndarray) -> np.ndarray:
    c = x - x.mean(axis=0, keepdims=True)
    s = np.linalg.svd(c, compute_uv=False)
    v = s**2
    return v[:2] / v.sum()


# ---- families ---------------------------------------------------------------------------------


def job_families(session, paths: DataPaths, derive_id: str, seed: int, log) -> dict[str, Any]:
    p = FAMILIES
    name = f"lab-families-{derive_id}"
    proj = _project(
        session,
        name,
        "MATCH (a:Player|Coach)-[r:TEAMMATE|COACHED|WORKED_WITH]->(b)",
        "log10(1.0 + toFloat(coalesce(r.snaps, 0)))",
    )
    log(f"lab families: projected {proj['nodes']:,} people, {proj['rels']:,} links")
    t = time.monotonic()
    rows = session.run(
        "CALL gds.leiden.stream($n, {relationshipWeightProperty: 'w', gamma: $gamma, "
        "theta: $theta, maxLevels: $levels, randomSeed: $seed, concurrency: 1}) "
        "YIELD nodeId, communityId WITH gds.util.asNode(nodeId) AS n, communityId "
        "RETURN CASE WHEN n:Player THEN 'player' ELSE 'coach' END AS kind, "
        "coalesce(n.player_id, n.coach_id) AS id, communityId AS community",
        n=name, gamma=p["gamma"], theta=p["theta"], levels=p["max_levels"], seed=seed,
    ).data()  # fmt: skip
    stats = session.run(
        "CALL gds.leiden.stats($n, {relationshipWeightProperty: 'w', gamma: $gamma, "
        "theta: $theta, maxLevels: $levels, randomSeed: $seed, concurrency: 1}) "
        "YIELD communityCount, modularity, ranLevels "
        "RETURN communityCount, modularity, ranLevels",
        n=name, gamma=p["gamma"], theta=p["theta"], levels=p["max_levels"], seed=seed,
    ).single()  # fmt: skip
    t_leiden = round(time.monotonic() - t, 1)
    _drop(session, name)
    members = pl.DataFrame(rows, schema={"kind": pl.String, "id": pl.String,
                                         "community": pl.Int64})  # fmt: skip
    people = _people(session)
    members = members.join(people, on=["kind", "id"], how="left")
    named, fam_members = _name_families(paths, members, p["min_size"])
    sizes = named["size"].to_list()
    return {
        "params": {**p, "seed": seed},
        "projection": proj,
        "communities": int(stats["communityCount"]),
        "families": named.height,
        "modularity": round(float(stats["modularity"]), 4),
        "levels": int(stats["ranLevels"]),
        "largest": max(sizes) if sizes else 0,
        "median_size": float(np.median(sizes)) if sizes else 0.0,
        "seconds": {"leiden": t_leiden},
        "_frames": {"families": named, "members": fam_members},
    }


def _name_families(paths: DataPaths, members: pl.DataFrame, min_size: int):
    """Each community's biggest team-era: the team with the most member snaps (players: the
    snap counts; coaches: a staff season counts like 1,000 snaps), its seasons where members
    had at least a quarter of that team's best season, and the unit most of its players
    played in ("2019-2021 KC offense")."""
    from nflengine.history_graph.derive import read_snaps

    build = state.read_build(paths) or {}
    app = state.read_append(paths, build) if build else None
    first = int(build.get("first_season", 2018))
    through = int(build.get("through_season", 2025))
    seasons = list(range(first, through + 1))
    season = week = None
    if app and app.get("through_week"):
        season, week = int(app["season"]), int(app["through_week"])
        seasons.append(season)
    sn = read_snaps(paths, seasons, season, week)
    ps = sn.group_by("p", "team", "season").agg(
        (pl.col("off") + pl.col("def") + pl.col("st")).sum().alias("n"),
        pl.col("off").sum(),
        pl.col("def").sum(),
    )
    players = members.filter(pl.col("kind") == "player").select(
        "community", pl.col("id").alias("p")
    )
    pts = players.join(ps, on="p", how="inner")
    by_team = pts.group_by("community", "team").agg(pl.col("n").sum().alias("n"))
    best = (
        by_team.sort(["community", "n", "team"], descending=[False, True, False])
        .group_by("community", maintain_order=True)
        .first()
    )
    era = (
        pts.join(best.select("community", "team"), on=["community", "team"], how="semi")
        .group_by("community", "team", "season")
        .agg(pl.col("n").sum(), pl.col("off").sum(), pl.col("def").sum())
    )
    era = era.with_columns(pl.col("n").max().over("community").alias("peak")).filter(
        pl.col("n") >= 0.25 * pl.col("peak")
    )
    span = era.group_by("community").agg(
        pl.col("team").first(),
        pl.col("season").min().alias("first_season"),
        pl.col("season").max().alias("last_season"),
        pl.col("off").sum(),
        pl.col("def").sum(),
    )
    sizes = members.group_by("community").agg(
        pl.len().alias("size"),
        (pl.col("kind") == "player").sum().alias("players"),
        (pl.col("kind") == "coach").sum().alias("coaches"),
    )
    # a member's snaps in the family's era (its team, its seasons) and in all (2018 on), so a
    # long career doesn't outrank the era's own starters (the UI worker, AE03)
    in_era = pts.join(
        span.select("community", pl.col("team").alias("era_team"), "first_season", "last_season"),
        on="community",
        how="inner",
    ).filter(
        (pl.col("team") == pl.col("era_team"))
        & (pl.col("season") >= pl.col("first_season"))
        & (pl.col("season") <= pl.col("last_season"))
    )
    member_snaps = (
        pts.group_by("community", "p").agg(pl.col("n").sum().alias("snaps_total"))
        .join(in_era.group_by("community", "p").agg(pl.col("n").sum().alias("snaps")),
              on=["community", "p"], how="left")
        .rename({"p": "id"})
    )  # fmt: skip
    fam_members = (
        members.join(member_snaps, on=["community", "id"], how="left")
        .with_columns(  # a coach has no snaps of his own: null, never 0 (the UI worker)
            *(
                pl.when(pl.col("kind") == "player")
                .then(pl.col(c).fill_null(0))
                .otherwise(None)
                .cast(pl.Int64)
                .alias(c)
                for c in ("snaps", "snaps_total")
            )
        )
        .sort(
            ["community", "kind", "snaps", "snaps_total"], descending=[False, True, True, True]
        )  # fmt: skip
    )
    top = (
        fam_members.filter(pl.col("kind") == "player")
        .group_by("community", maintain_order=True)
        .agg(pl.col("name").head(5).alias("top_players"))
    )
    coaches = (
        fam_members.filter(pl.col("kind") == "coach")
        .group_by("community", maintain_order=True)
        .agg(pl.col("name").head(3).alias("top_coaches"))
    )
    fam = (
        sizes.join(span, on="community", how="left")
        .join(top, on="community", how="left")
        .join(coaches, on="community", how="left")
        .filter(pl.col("size") >= min_size)
        .with_columns(
            pl.when(pl.col("off") >= 2 * pl.col("def"))
            .then(pl.lit("offense"))
            .when(pl.col("def") >= 2 * pl.col("off"))
            .then(pl.lit("defense"))
            .otherwise(pl.lit("core"))
            .alias("unit")
        )
    )
    fam = fam.with_columns(
        pl.when(pl.col("team").is_null())
        .then(pl.lit("unnamed"))
        .when(pl.col("first_season") == pl.col("last_season"))
        .then(pl.format("{} {} {}", "first_season", "team", "unit"))
        .otherwise(pl.format("{}-{} {} {}", "first_season", "last_season", "team", "unit"))
        .alias("name")
    ).sort(["size", "community"], descending=[True, False])
    # the same name twice: number them by size
    fam = (
        fam.with_columns(
            pl.col("community").rank("ordinal").over("name").alias("_n"),
            pl.len().over("name").alias("_dups"),
        )
        .with_columns(
            pl.when(pl.col("_dups") > 1)
            .then(pl.format("{} ({})", "name", "_n"))
            .otherwise(pl.col("name"))
            .alias("name")
        )
        .drop("_n", "_dups", "off", "def")
    )
    kept = fam["community"]
    return fam, fam_members.filter(pl.col("community").is_in(kept.implode()))


# ---- chemistry --------------------------------------------------------------------------------


def job_chemistry(session, paths: DataPaths, derive_id: str, seed: int, log) -> dict[str, Any]:
    p = CHEMISTRY
    rows = session.run(
        "MATCH (q:Player)-[c:CONNECTION]->(r:Player) WHERE c.targets >= $m "
        "RETURN q.player_id AS qb_id, q.name AS qb, r.player_id AS receiver_id, "
        "r.name AS receiver, r.position AS position, c.targets AS targets, "
        "c.catches AS catches, c.yards AS yards, c.touchdowns AS touchdowns, c.epa AS epa, "
        "c.seasons AS seasons, c.teams AS teams",
        m=p["min_targets"],
    ).data()
    duos = (
        pl.DataFrame(rows)
        .with_columns(
            (pl.col("epa") / pl.col("targets")).round(4).alias("epa_per_target"),
            (pl.col("catches") / pl.col("targets")).round(4).alias("catch_rate"),
            (pl.col("yards") / pl.col("targets")).round(2).alias("yards_per_target"),
            pl.col("seasons").list.min().alias("first_season"),
            pl.col("seasons").list.max().alias("last_season"),
        )
        .sort(["epa_per_target", "targets"], descending=[True, True])
    )
    duos = duos.with_columns(pl.int_range(1, duos.height + 1).alias("rank"))
    name = f"lab-chemistry-{derive_id}"
    proj = _project(
        session, name, "MATCH (a:Player)-[r:CONNECTION]->(b:Player)", "toFloat(r.targets)"
    )
    t = time.monotonic()
    pr = session.run(
        "CALL gds.pageRank.stream($n, {relationshipWeightProperty: 'w', dampingFactor: $d, "
        "maxIterations: $it, concurrency: 1}) YIELD nodeId, score "
        "WITH gds.util.asNode(nodeId) AS n, score "
        "RETURN n.player_id AS player_id, n.name AS name, n.position AS position, "
        "n.position_group AS grp, score, "
        "COUNT { (n)-[:CONNECTION]->() } AS receivers, COUNT { (n)<-[:CONNECTION]-() } AS qbs",
        n=name, d=p["pagerank_damping"], it=p["pagerank_iterations"],
    ).data()  # fmt: skip
    t_pr = round(time.monotonic() - t, 1)
    _drop(session, name)
    rank = pl.DataFrame(pr).with_columns(
        pl.when(pl.col("grp") == "QB").then(pl.lit("qb")).otherwise(pl.lit("receiver"))
        .alias("role"),
        pl.col("score").round(5),
    )  # fmt: skip
    rank = rank.sort(["role", "score"], descending=[False, True]).with_columns(
        pl.col("score").rank("ordinal", descending=True).over("role").cast(pl.Int64).alias("rank")
    )
    return {
        "params": {**p, "seed": seed},
        "projection": proj,
        "duos": duos.height,
        "ranked": rank.height,
        "seconds": {"pagerank": t_pr},
        "_frames": {"duos": duos, "pagerank": rank},
    }


# ---- the run ----------------------------------------------------------------------------------

_JOB_FUNCS = {"neighbours": job_neighbours, "families": job_families, "chemistry": job_chemistry}
# each job's tables (its `_frames`) -> the files the pages read; written by `run_lab` only after
# the person links held still for the whole job (Sol, AE03 review)
FILES = {
    "neighbours": {"neighbours": "neighbours.parquet", "map": "neighbours_map.parquet"},
    "families": {"families": "families.parquet", "members": "family_members.parquet"},
    "chemistry": {"duos": "chemistry_duos.parquet", "pagerank": "chemistry_pagerank.parquet"},
}
CHANGED_TEXT = (
    "the person links changed while the lab ran (a build, append or derive): nothing was "
    "written; run it again"
)


def run_lab(
    jobs: list[str] | None = None,
    *,
    seed: int = SEED,
    paths: DataPaths | None = None,
    cfg: HistoryConfig | None = None,
    driver=None,
    use_wandb: bool = True,
    launched_by: str | None = None,
    smoke: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run the lab jobs (all by default) on the current derive; returns `{job: entry}`. One lab
    or path benchmark at a time (`state.lab_lock_path`)."""
    from nflengine.ops.lock import LockHeld, run_lock

    paths = paths or ensure_data_root(create_dirs=False)
    cfg = cfg or history_config()
    jobs = list(jobs or JOBS)
    bad = [j for j in jobs if j not in JOBS]
    if bad:
        raise HistoryLabError(f"unknown job {bad[0]!r}: one of {', '.join(JOBS)}")
    from nflengine.history_graph.build import weekly_lock_held

    if weekly_lock_held(paths):  # the weekly run shares the disk and has a deadline (AE03:
        # heavy history-graph work slowed a weekly rebuild from ~3 to ~17 minutes)
        raise HistoryLabError(
            "the weekly run (or the injury update / Update play calling) holds the weekly lock: "
            "run the lab after it"
        )
    try:
        with run_lock(state.lab_lock_path(paths), "nfl history-graph lab"):
            return _run_lab(jobs, seed, paths, cfg, driver, use_wandb, launched_by, smoke, log)
    except LockHeld as e:
        raise HistoryLabError(
            f"another lab or path benchmark is running (pid {e.holder.get('pid')})"
        ) from None


def _run_lab(jobs, seed, paths, cfg, driver, use_wandb, launched_by, smoke, log):
    from nflengine.history_graph.derive import links_unchanged, read_derive

    if state.history_busy(paths):
        raise HistoryLabError(
            "a history-graph build, append or derive is running or didn't finish "
            "(`nfl history-graph status`)"
        )
    build = state.read_build(paths)
    dv = read_derive(paths, build) if build else None
    if not dv:
        raise HistoryLabError("no person links yet: run `nfl history-graph derive` first")
    own = driver is None
    if own:
        from nflengine.history_graph.client import get_history_driver

        driver = get_history_driver(cfg=cfg)
    out: dict[str, Any] = {}
    try:
        for job in jobs:
            t = time.monotonic()
            with driver.session() as s:
                try:
                    res = _JOB_FUNCS[job](s, paths, dv["derive_id"], seed, log)
                finally:  # a job that failed half-way leaves no projection behind
                    with contextlib.suppress(Exception):
                        _drop(s, f"lab-{job}-{dv['derive_id']}")
            if not links_unchanged(paths, dv["derive_id"]):
                raise HistoryLabError(CHANGED_TEXT)
            frames = res.pop("_frames", {})
            for key, name in FILES[job].items():
                if key in frames:
                    _write_parquet(frames[key], lab_path(paths, name))
            entry = {
                "status": "ok",
                "job": job,
                "derive_id": dv["derive_id"],
                "build_id": dv["build_id"],
                "through_season": dv["through_season"],
                "season": dv.get("season"),
                "through_week": dv.get("through_week"),
                "ran_at": _now(),
                **res,
            }
            entry["seconds"] = {**res.get("seconds", {}),
                                "total": round(time.monotonic() - t, 1)}  # fmt: skip
            if use_wandb:
                entry["wandb_url"] = _wandb_lab(job, entry, frames, launched_by, smoke, log)
            _write_lab(paths, job, entry)
            out[job] = entry
            log(f"[green]lab {job}: done ({entry['seconds']['total']:.0f} s)[/]")
    finally:
        if own:
            driver.close()
    return out


def _now() -> str:
    from nflengine.history_graph.build import utc_now

    return utc_now().isoformat()


def _wandb_lab(job: str, entry: dict[str, Any], frames: dict[str, pl.DataFrame], launched_by,
               smoke: bool, log) -> str | None:  # fmt: skip
    try:
        import wandb

        from nflengine.tracking import git_commit, init_run

        wb = init_run(
            group="history-graph-smoke" if smoke else "history-graph",
            job_type="history-graph-lab",
            config={
                "job": job,
                "derive_id": entry["derive_id"],
                **entry["params"],
                "git_commit": git_commit(),
            },  # fmt: skip
            tags=["history-graph", "lab", job, *(["smoke"] if smoke else [])],
            launched_by=launched_by,
            name=f"history-graph-lab-{job}",
        )
        flat = {k: v for k, v in entry.items() if isinstance(v, int | float) and k != "season"}
        wb.summary.update({**flat, **{f"time/{k}": v for k, v in entry["seconds"].items()}})
        for key, df in frames.items():
            small = df.head(2000) if key not in ("map",) else df
            wb.log({f"{job}/{key}": wandb.Table(dataframe=_for_table(small).to_pandas())})
        if job == "neighbours" and "map" in frames:
            mp = frames["map"].select("x", "y", "grp", "name")
            tbl = wandb.Table(dataframe=_for_table(mp).to_pandas())
            chart = wandb.plot.scatter(tbl, "x", "y", title="Players, FastRP -> PCA")
            wb.log({"neighbours/map_2d": chart})
        if job == "families" and "families" in frames:
            sizes = frames["families"].select("name", "size").head(40)
            wb.log({"families/sizes": wandb.plot.bar(
                wandb.Table(dataframe=sizes.to_pandas()), "name", "size",
                title="Football families by size")})  # fmt: skip
        url = wb.url
        wb.finish()
        return url
    except Exception as e:  # W&B never sinks a lab job
        log(f"[yellow]W&B off for lab {job} ({type(e).__name__})[/]")
        return None


def _for_table(df: pl.DataFrame) -> pl.DataFrame:
    """Lists as text (W&B tables want scalars)."""
    return df.with_columns(
        pl.col(c).cast(pl.List(pl.String)).list.join(", ")
        for c, t in df.schema.items()
        if isinstance(t, pl.List)
    )


def lab_status(paths: DataPaths) -> dict[str, Any]:
    """Which lab files exist and which derive they were made on (for the API)."""
    doc = read_lab(paths)
    return {job: {k: doc[job].get(k) for k in ("derive_id", "ran_at", "through_season",
                                               "season", "through_week")}
            for job in JOBS if job in doc}  # fmt: skip
