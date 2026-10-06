"""The Graph tab: what the weekly graph build produced (`graph_results.json`).

Counts by label and type, time by stage, rows per query, the insights the digest used, the
candidates it didn't use and why (`skipped`: the game had started, it was used in a recent
digest, or the same player was already a pick; otherwise the digest had no room), and GDS
(P08). The query rows themselves (`queries[*].results`) stay on the server.
"""

from __future__ import annotations

from typing import Any

from nflengine.app.readers.common import NEO4J_BROWSER, clean, num, read_json
from nflengine.paths import DataPaths

STAGES = ("inputs", "tables", "wipe", "schema", "load", "counts", "queries")
SKIP_REASON = {
    "started": "game already started",
    "novelty": "used in a recent digest",
    "duplicate_player": "same player as another pick",
}


def _insight(x: dict[str, Any], paths: DataPaths) -> dict[str, Any]:
    return {
        "id": clean(x.get("insight_id"), None, 120) or "",
        "type": clean(x.get("insight_type"), None, 60) or "",
        "section": clean(x.get("section"), None, 40) or "",
        "query": clean(x.get("graph_query"), None, 60) or "",
        "strength": num(x.get("strength"), 4) or 0.0,
        "confidence": clean(x.get("confidence"), None, 20) or "",
        "game_id": clean(x.get("game_id"), None, 40),
        "matchup": clean(x.get("matchup"), None, 120),
        "headline": clean(x.get("headline"), paths, 400) or "",
        "brief": clean(x.get("brief"), paths, 600) or "",
        "note": clean(x.get("note"), paths, 400) or None,
    }


def _empty(season: int, week: int) -> dict[str, Any]:
    return {
        "season": season,
        "week": week,
        "status": "none",
        "built_at": None,
        "mode": None,
        "totals": {"nodes": 0, "rels": 0, "mismatches": 0},
        "nodes_by_label": [],
        "rels_by_type": [],
        "stages": [],
        "total_seconds": None,
        "queries": [],
        "used": [],
        "candidates": 0,
        "not_used": [],
        "skipped": {"started": 0, "novelty": 0, "duplicate_player": 0},
        "gds": None,
        "browser_url": NEO4J_BROWSER,
    }


def get_graph(paths: DataPaths, season: int, week: int) -> dict[str, Any]:
    g = read_json(paths.run_dir(season, week) / "graph_results.json")
    if g is None:
        return _empty(season, week)
    counts = g.get("counts") or {}
    nodes = sorted(
        (
            {"label": k.split(":", 1)[1], "count": int(v)}
            for k, v in counts.items()
            if k.startswith("node:") and isinstance(v, int | float)
        ),
        key=lambda x: (-x["count"], x["label"]),
    )
    rels = sorted(
        (
            {"type": k.split(":", 1)[1], "count": int(v)}
            for k, v in counts.items()
            if k.startswith("rel:") and isinstance(v, int | float)
        ),
        key=lambda x: (-x["count"], x["type"]),
    )
    timings = g.get("timings") or {}
    stages = [
        {"stage": s, "seconds": t}
        for s in STAGES
        if (t := num(timings.get(f"{s}_s"), 2)) is not None
    ]
    queries = [
        {
            "name": clean(name, None, 60) or "",
            "rows": int((q or {}).get("rows") or 0),
            "seconds": num((q or {}).get("seconds"), 3),
            "error": clean((q or {}).get("error"), paths, 300),
        }
        for name, q in (g.get("queries") or {}).items()
        if isinstance(q, dict)
    ]
    used = [
        _insight(x, paths)
        for section in (g.get("selected") or {}).values()
        for x in (section or [])
        if isinstance(x, dict)
    ]
    used_ids = {u["id"] for u in used}
    skipped = g.get("skipped") or {}
    reason_of = {
        str(i): SKIP_REASON.get(why, why) for why, ids in skipped.items() for i in (ids or [])
    }
    cands = [x for x in g.get("candidates") or [] if isinstance(x, dict)]
    not_used = sorted(
        (
            {**_insight(x, paths), "reason": reason_of.get(str(x.get("insight_id")), "not picked")}
            for x in cands
            if str(x.get("insight_id")) not in used_ids
        ),
        key=lambda x: (-x["strength"], x["id"]),
    )
    gds = g.get("gds")
    gds_out = None
    if isinstance(gds, dict):
        gds_out = {
            "status": clean(gds.get("status"), None, 40) or "",
            "version": clean(gds.get("version"), None, 40),
            "seconds": num(gds.get("seconds"), 2),
            "jobs": [
                {
                    "name": clean(name, None, 60) or "",
                    "status": clean((j or {}).get("status"), None, 40) or "",
                    "seconds": num((j or {}).get("seconds"), 2),
                }
                for name, j in (gds.get("jobs") or {}).items()
                if isinstance(j, dict)
            ],
        }
    return {
        "season": season,
        "week": week,
        "status": clean(g.get("status"), None, 40) or "ok",
        "built_at": clean(g.get("built_at"), None, 40),
        "mode": clean(g.get("mode"), None, 20),
        "totals": {
            "nodes": sum(n["count"] for n in nodes),
            "rels": sum(r["count"] for r in rels),
            "mismatches": len(g.get("count_mismatches") or {}),
        },
        "nodes_by_label": nodes,
        "rels_by_type": rels,
        "stages": stages,
        "total_seconds": num(timings.get("total_s"), 2),
        "queries": queries,
        "used": used,
        "candidates": len(cands),
        "not_used": not_used,
        "skipped": {k: len(skipped.get(k) or []) for k in SKIP_REASON},
        "gds": gds_out,
        "browser_url": NEO4J_BROWSER,
    }
