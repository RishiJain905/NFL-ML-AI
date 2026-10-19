"""Explore -> Ask the Engine -> Connections and Graph lab (AE03): the file reads.

Everything here reads files the history graph's commands wrote, never Neo4j, so these answer
with the container stopped:
- `neo4j-history/people.parquet` (the derive's people index) for the name search;
- `neo4j-history/derive.json` for "the person links as of";
- `neo4j-history/lab/*.parquet` + `lab.json` (`nfl history-graph lab`) for the three lab views;
- `neo4j-history/lab/path_benchmark.{json,parquet}` (`nfl history-graph bench-paths`).

Each file is cached by its modification time (the app runs for days; a new derive or lab run
is picked up on the next request). Every string the browser gets is cleaned (`clean`), every
list is bounded.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.app.readers.common import clean
from nflengine.paths import DataPaths

LAB_JOBS = ("neighbours", "families", "chemistry")
MAX_PEOPLE = 20
MAX_FAMILY_MEMBERS = 400
MAX_DUOS = 200
_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def _stamped_parquet(path: Path) -> tuple[float, pl.DataFrame] | None:
    """The file and the modification time it was cached under (a cache built from it keys on
    that stamp, not on a second look at the file, which a new derive may have replaced in
    between: Sol, AE03 review)."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = str(path)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit
    df = pl.read_parquet(path, memory_map=False)  # never hold the file (D100)
    with _lock:
        _cache[key] = (mtime, df)
    return mtime, df


def _cached_parquet(path: Path) -> pl.DataFrame | None:
    hit = _stamped_parquet(path)
    return hit[1] if hit else None


def _json(path: Path) -> dict[str, Any] | None:
    from nflengine.history_graph.state import _read

    return _read(path)


def clean_tree(o: Any, paths: DataPaths | None) -> Any:
    if isinstance(o, str):
        return clean(o, paths, 400)
    if isinstance(o, dict):
        return {k: clean_tree(v, paths) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [clean_tree(v, paths) for v in o]
    return o


def _span(first: int, last: int | None) -> str:
    """ "2018-2025", or "2018" for one season."""
    return str(first) if last is None or last == first else f"{first}-{last}"


# ---- as of ------------------------------------------------------------------------------------


def links_asof(paths: DataPaths) -> dict[str, Any] | None:
    """The current derive: `{derive_id, through_season, season, week, derived_at, label}`."""
    from nflengine.history_graph.derive import read_derive

    d = read_derive(paths)
    if not d:
        return None
    week = d.get("through_week")
    label = f"person links {d.get('first_season') or 2018}-{d['through_season']}"
    if week:
        label += f" + {d['season']} week {week}"
    return {
        "derive_id": d["derive_id"],
        "first_season": d.get("first_season"),
        "through_season": d["through_season"],
        "season": d.get("season"),
        "week": week,
        "derived_at": d.get("derived_at"),
        "counts": d.get("counts"),
        "label": label,
    }


def lab_asof(paths: DataPaths, job: str) -> dict[str, Any] | None:
    from nflengine.history_graph.lab import read_lab

    doc = read_lab(paths).get(job)
    if not doc:
        return None
    current = links_asof(paths)
    return {
        "ran_at": doc.get("ran_at"),
        "derive_id": doc.get("derive_id"),
        "current": bool(current and current["derive_id"] == doc.get("derive_id")),
        "params": doc.get("params"),
        "wandb_url": doc.get("wandb_url"),
        "summary": {k: doc.get(k) for k in ("players", "communities", "families",
                                              "modularity", "largest", "median_size", "duos",
                                              "ranked", "explained_variance_2d") if k in doc},
    }  # fmt: skip


def get_status(paths: DataPaths) -> dict[str, Any]:
    """What Connections and the Graph lab have to work with."""
    from nflengine.history_graph.state import history_asof, history_busy, read_unfinished

    bench = _json(paths.neo4j_history / "lab" / "path_benchmark.json")
    return {
        "history": history_asof(paths),
        "busy": history_busy(paths),
        "unfinished": (read_unfinished(paths) or {}).get("what"),
        "links": links_asof(paths),
        "lab": {job: lab_asof(paths, job) for job in LAB_JOBS},
        "benchmark": (
            {
                "ran_at": bench.get("ran_at")
                or bench.get("started_at")
                or bench.get("finished_at"),
                "derive_id": bench.get("derive_id"),
            }
            if bench
            else None
        ),  # fmt: skip
    }


# ---- people -----------------------------------------------------------------------------------


def get_people(paths: DataPaths, q: str, kind: str | None = None, limit: int = 10):
    """The name search (`connections.search_people` over the derive's people index)."""
    from nflengine.history_graph.connections import people_path, search_people

    df = _cached_parquet(people_path(paths))
    if df is None:
        return {"query": q, "people": [], "links": links_asof(paths), "ready": False}
    if kind in ("player", "coach"):
        df = df.filter(pl.col("kind") == kind)
    found = search_people(df, q, max(1, min(limit, MAX_PEOPLE)))
    return {"query": q, "people": found, "links": links_asof(paths), "ready": True}


def known_person(paths: DataPaths, ref) -> bool:
    """Is this person in the people index (a search could have offered them)?"""
    from nflengine.history_graph.connections import people_path

    df = _cached_parquet(people_path(paths))
    if df is None:
        return False
    return df.filter((pl.col("kind") == ref.kind) & (pl.col("id") == ref.id)).height > 0


def person_facts(paths: DataPaths) -> dict[tuple[str, str], dict[str, Any]]:
    """`(kind, id) -> {team, seasons}` from the people index (the latest team; "2018-2026")."""
    from nflengine.history_graph.connections import people_path

    hit = _stamped_parquet(people_path(paths))
    if hit is None:
        return {}
    mtime, df = hit
    key = f"facts:{people_path(paths)}"
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    out = {
        (r["kind"], r["id"]): {
            "team": r.get("team"),
            "seasons": (
                _span(r["first_season"], r["last_season"])
                if r.get("first_season") is not None
                else None
            ),
        }
        for r in df.select("kind", "id", "team", "first_season", "last_season").to_dicts()
    }
    with _lock:
        _cache[key] = (mtime, out)
    return out


# ---- the lab views ----------------------------------------------------------------------------


def _lab_file(paths: DataPaths, name: str) -> pl.DataFrame | None:
    return _cached_parquet(paths.neo4j_history / "lab" / name)


def get_neighbours(paths: DataPaths, player_id: str) -> dict[str, Any] | None:
    """A player's neighbours (both scopes, 10 each) and his point on the map; None when the
    job hasn't run; `found: False` when he isn't in it (too few snaps, or no links)."""
    nb = _lab_file(paths, "neighbours.parquet")
    mp = _lab_file(paths, "neighbours_map.parquet")
    if nb is None or mp is None:
        return None
    me = mp.filter(pl.col("player_id") == player_id)
    rows = nb.filter(pl.col("player_id") == player_id).sort("scope", "rank")
    cols = ["rank", "neighbour_id", "neighbour_name", "neighbour_position", "neighbour_team",
            "similarity", "snaps_together"]  # fmt: skip
    out = {
        "player_id": player_id,
        "found": me.height > 0,
        "player": me.select(
            "player_id", "name", "position", "grp", "team", "snaps", "x", "y"
        ).to_dicts()[0]
        if me.height
        else None,
        "position": rows.filter(pl.col("scope") == "position").select(cols).to_dicts(),
        "overall": rows.filter(pl.col("scope") == "overall").select(cols).to_dicts(),
        "asof": lab_asof(paths, "neighbours"),
    }
    return out


def get_neighbours_map(paths: DataPaths) -> dict[str, Any] | None:
    """Every player's point on the 2-D map (PCA of the FastRP embeddings): ~4.3k points."""
    mp = _lab_file(paths, "neighbours_map.parquet")
    if mp is None:
        return None
    pts = mp.select(
        "player_id", "name", "grp", "team",
        pl.col("x").round(3), pl.col("y").round(3),
    ).to_dicts()  # fmt: skip
    return {"points": pts, "asof": lab_asof(paths, "neighbours")}


def get_families(paths: DataPaths, family: int | None = None, person: str | None = None):
    """Every family (biggest first); with `family` (or `person`: `player:<id>` / `coach:<id>`,
    whose family is picked) also its members, most snaps first."""
    fam = _lab_file(paths, "families.parquet")
    mem = _lab_file(paths, "family_members.parquet")
    if fam is None or mem is None:
        return None
    picked = family
    if person:
        kind, _, pid = person.partition(":")
        hit = mem.filter((pl.col("kind") == kind) & (pl.col("id") == pid))
        picked = int(hit["community"][0]) if hit.height else None
    cols = ["community", "name", "size", "players", "coaches", "team", "first_season",
            "last_season", "unit", "top_players", "top_coaches"]  # fmt: skip
    families = fam.select([c for c in cols if c in fam.columns]).to_dicts()
    members = None
    if picked is not None:
        m = mem.filter(pl.col("community") == picked).head(MAX_FAMILY_MEMBERS)
        cols = [c for c in ("kind", "id", "name", "position", "snaps", "snaps_total")
                if c in m.columns]  # fmt: skip
        members = m.select(cols).to_dicts()
    return {
        "families": families,
        "family": picked,
        "members": members,
        "person_found": None if not person else picked is not None,
        "asof": lab_asof(paths, "families"),
    }


def get_chemistry(paths: DataPaths, min_targets: int = 100, limit: int = 50,
                  qb: str | None = None) -> dict[str, Any] | None:  # fmt: skip
    """QB -> receiver duos (EPA per target, at least `min_targets`), and the PageRank tables."""
    duos = _lab_file(paths, "chemistry_duos.parquet")
    pr = _lab_file(paths, "chemistry_pagerank.parquet")
    if duos is None or pr is None:
        return None
    d = duos.filter(pl.col("targets") >= min_targets)
    if qb:
        d = d.filter(pl.col("qb_id") == qb)
    d = d.sort(["epa_per_target", "targets"], descending=[True, True]).head(
        max(1, min(limit, MAX_DUOS))
    )
    cols = ["qb_id", "qb", "receiver_id", "receiver", "position", "targets", "catches", "yards",
            "touchdowns", "epa", "epa_per_target", "catch_rate", "yards_per_target",
            "first_season", "last_season", "teams"]  # fmt: skip
    rank_cols = ["rank", "player_id", "name", "position", "score", "receivers", "qbs"]
    return {
        "min_targets": min_targets,
        "duos": d.select([c for c in cols if c in d.columns]).to_dicts(),
        "receivers": pr.filter(pl.col("role") == "receiver").sort("rank").head(25)
        .select(rank_cols).to_dicts(),
        "qbs": pr.filter(pl.col("role") == "qb").sort("rank").head(15).select(rank_cols)
        .to_dicts(),
        "asof": lab_asof(paths, "chemistry"),
    }  # fmt: skip


def get_benchmark(paths: DataPaths) -> dict[str, Any] | None:
    """The path benchmark's results (`nfl history-graph bench-paths`)."""
    doc = _json(paths.neo4j_history / "lab" / "path_benchmark.json")
    rows = _lab_file(paths, "path_benchmark.parquet")
    if doc is None or rows is None:
        return None
    # the page draws the rows and the run's facts; every run's raw times and the GDS procedure
    # list stay in the file (and W&B), not in every response
    drop = ("rows", "results", "table", "gds_procedures", "times_ms")
    keep = {k: v for k, v in doc.items() if k not in drop}
    return {**keep, "rows": rows.to_dicts()}
