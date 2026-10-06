"""The Players tab: the watch list, tough spots and every projection.

- Projections: `predictions_players.parquet` (+ `predictions_teams.parquet` from week 5, P08)
  with the digest's own centre (`digest.players.center`: P50, or the mean for counts). A
  yes/no stat (`kind == "prob"`: TD, interception) has no range: its projection is the
  chance (`p_ge1`) and its baseline the baseline chance.
- The watch list: `watchlist.parquet` (rank, side, the numbers) with the digest's own words
  from `payload.json` -> `players_to_watch` (what the reader saw).
- Tough spots: `payload.json` -> `tough_spots`.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from nflengine.app.readers.common import clean, num, read_json, read_parquet
from nflengine.digest.names import nickname
from nflengine.digest.players import center
from nflengine.paths import DataPaths

GROUP_ORDER = ("QB", "RB", "WR/TE", "EDGE/DL", "LB/S", "CB/S", "TEAM")
INJURY_CLEAR = {"", "none", "active", "healthy"}


def _injury(v: Any) -> str | None:
    s = clean(v, None, 40)
    return None if s is None or s.strip().lower() in INJURY_CLEAR else s


def _projection(r: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """(projection, baseline, chance) as the digest reads them."""
    chance = num(r.get("p_ge1"))
    if r.get("kind") == "prob":
        base = num(r.get("baseline_p_ge1"))
        return (
            chance if chance is not None else num(r.get("mean")),
            base if base is not None else num(r.get("baseline")),
            chance,
        )
    if r.get("p50") is None and r.get("mean") is None:
        return None, num(r.get("baseline")), chance  # a heuristic pick (P04): no projection
    try:
        proj = num(center(r))
    except (KeyError, TypeError, ValueError):
        proj = num(r.get("mean"))
    return proj, num(r.get("baseline")), chance


def _row(r: dict[str, Any], *, team_row: bool = False) -> dict[str, Any]:
    proj, base, chance = _projection(r)
    team = r.get("team") or ""
    return {
        "player": nickname(team) if team_row else clean(r.get("player"), None, 80),
        "player_id": team if team_row else clean(r.get("player_id"), None, 20),
        "team": team,
        "opponent": r.get("opponent") or "",
        "home": bool(r.get("home")),
        "position": "TEAM" if team_row else (r.get("position") or ""),
        "group": "TEAM" if team_row else (r.get("group") or ""),
        "target": r.get("target") or "",
        "target_label": clean(r.get("target_label") or r.get("target"), None, 60),
        "kind": r.get("kind") or "",
        "unit": r.get("unit") or "",
        "is_main": bool(r.get("is_main")),
        "projection": num(proj, 3),
        "chance": num(chance, 4),
        "p10": num(r.get("p10"), 3),
        "p90": num(r.get("p90"), 3),
        "baseline": num(base, 3),
        "vs_baseline": num(r.get("outperformance"), 3),
        "confidence": clean(r.get("confidence"), None, 20),
        "injury": None if team_row else _injury(r.get("injury_status")),
    }


def _display(item: dict[str, Any] | None, paths: DataPaths) -> dict[str, str] | None:
    if not item:
        return None

    def d(k: str) -> str:
        v = item.get(k)
        return clean(v.get("display") if isinstance(v, dict) else "", paths, 120) or ""

    return {"projection": d("projection"), "range": d("interval"), "vs_baseline": d("vs_baseline")}


def _driver(item: dict[str, Any] | None, r: dict[str, Any]) -> str | None:
    """The digest's main driver (only shown when it explains enough of the gap); the model's
    top phrase when the payload is missing."""
    if item is not None:
        drivers = item.get("drivers") or []
        if not drivers:
            return None
        return clean(str(drivers[0]).split(" (")[0], None, 160)
    ds = r.get("drivers") or []
    return clean(ds[0].get("phrase"), None, 160) if ds and isinstance(ds[0], dict) else None


def _watch(
    watch: pl.DataFrame | None, payload: dict[str, Any] | None, paths: DataPaths
) -> list[dict[str, Any]]:
    if watch is None or watch.is_empty():
        return []
    items = [x for x in (payload or {}).get("players_to_watch") or [] if isinstance(x, dict)]
    by_player: dict[str, list[dict[str, Any]]] = {}
    for x in items:
        by_player.setdefault(str(x.get("player_id")), []).append(x)
    df = watch
    if "side" in df.columns:
        df = df.with_columns((pl.col("side") == "defense").fill_null(False).alias("_def"))
        df = df.sort("_def", "rank", nulls_last=True)
    elif "rank" in df.columns:
        df = df.sort("rank", nulls_last=True)
    out = []
    for r in df.iter_rows(named=True):
        cands = by_player.get(str(r.get("player_id")), [])
        item = next((x for x in cands if x.get("target") == r.get("target_label")), None) or (
            cands[0] if cands else None
        )
        side = r.get("side") if r.get("side") in ("offense", "defense") else None
        out.append(
            {
                **_row(r),
                "rank": int(r["rank"]) if r.get("rank") is not None else None,
                "side": side,
                "source": clean(r.get("source"), None, 20),
                "driver": _driver(item if items else None, r),
                "display": _display(item, paths),
            }
        )
    return out


def _tough(payload: dict[str, Any] | None, paths: DataPaths) -> list[dict[str, Any]]:
    out = []
    for x in (payload or {}).get("tough_spots") or []:
        if not isinstance(x, dict):
            continue
        out.append(
            {
                "player": clean(x.get("player"), None, 80),
                "player_id": clean(x.get("player_id"), None, 20),
                "team": x.get("team") or "",
                "opponent": x.get("opponent") or "",
                "position": x.get("position") or "",
                "target_label": clean(x.get("target"), None, 60),
                "projection": num((x.get("projection") or {}).get("value")),
                "baseline": num((x.get("baseline") or {}).get("value")),
                "display": _display(x, paths) or {"projection": "", "range": "", "vs_baseline": ""},
            }
        )
    return out


def get_players(paths: DataPaths, season: int, week: int, stats: str = "main") -> dict[str, Any]:
    run_dir = paths.run_dir(season, week)
    preds = read_parquet(run_dir / "predictions_players.parquet")
    teams = read_parquet(run_dir / "predictions_teams.parquet")
    payload = read_json(run_dir / "payload.json")
    base = {"season": season, "week": week, "stats": "all" if stats == "all" else "main"}
    if preds is None or preds.is_empty():
        return {
            **base,
            "status": "none",
            "model": None,
            "counts": {"total": 0, "main": 0, "teams": 0, "stats": 0, "by_group": {}},
            "groups": [],
            "watch": [],
            "tough_spots": [],
            "rows": [],
        }
    teams = teams if teams is not None else pl.DataFrame()
    main = preds.filter(pl.col("is_main")) if "is_main" in preds.columns else preds
    chosen = preds if stats == "all" else main
    rows = [_row(r) for r in chosen.iter_rows(named=True)]
    if teams.height:
        tchosen = teams if stats == "all" else teams.filter(pl.col("is_main"))
        rows += [_row(r, team_row=True) for r in tchosen.iter_rows(named=True)]
    by_group = {
        str(g): int(n) for g, n in preds.group_by("group").len().iter_rows() if g is not None
    }
    if teams.height:
        by_group["TEAM"] = teams.height
    present = {r["group"] for r in rows}
    first = preds.row(0, named=True)
    n_stats = preds.select("target", "group").unique().height + (
        teams.select("target").unique().height if teams.height else 0
    )
    return {
        **base,
        "status": "projected",
        "model": {
            "version": clean(first.get("model_version"), None, 80),
            "trained_through": clean(first.get("trained_through"), None, 20),
        },
        "counts": {
            "total": preds.height,
            "main": main.height,
            "teams": teams.height,
            "stats": n_stats,
            "by_group": by_group,
        },
        "groups": [g for g in GROUP_ORDER if g in present] + sorted(present - set(GROUP_ORDER)),
        "watch": _watch(read_parquet(run_dir / "watchlist.parquet"), payload, paths),
        "tough_spots": _tough(payload, paths),
        "rows": rows,
    }
