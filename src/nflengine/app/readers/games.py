"""The Games tab: every game's call, the model vs the market, and the finals once played.

- Predictions: `predictions_games.parquet` (the `is_primary` row is the one the digest
  shows; the `model_only` row gives the model-only chance).
- QB changes: `payload.json` -> `qb_changes` (the prediction file marks every QB's source
  `schedule`/`last_game`, so it can't tell a change; README §9).
- Finals: curated `games` (a short read of its Parquet file, D100). Hit / miss uses the
  report card's own `grade_games`, so the two can never disagree.
- The current week before its run: the slate from the schedule snapshot the calendar reads
  (kickoff, stadium, market line), with its snapshot date: it can be a few days old.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl

from nflengine.app.readers.common import clean, curated, num, read_json, read_parquet
from nflengine.digest.report_card import grade_games
from nflengine.ops.calendar import WeekPlan
from nflengine.paths import DataPaths

GAME_COLS = ["game_id", "home_score", "away_score", "result", "stadium"]


def _stadiums(game_ids: list[str], games: pl.DataFrame | None) -> dict[str, str | None]:
    """Each game's stadium: the `game_venues` correction first (2025's games abroad are
    listed at the home team's stadium), else the schedule's name."""
    from nflengine.features.venues import load_game_venues, load_venues

    out: dict[str, str | None] = {}
    if games is not None and "stadium" in games.columns:
        out = dict(zip(games["game_id"].to_list(), games["stadium"].to_list(), strict=True))
    try:
        fixes = load_game_venues()
        venues = load_venues()
        names = dict(zip(venues["stadium_id"].to_list(), venues["name"].to_list(), strict=True))
    except Exception:  # noqa: BLE001 - a broken config only loses the correction
        return {g: out.get(g) for g in game_ids}
    return {g: names.get(fixes[g], out.get(g)) if g in fixes else out.get(g) for g in game_ids}


def _qb_changes(payload: dict[str, Any] | None, paths: DataPaths) -> dict[tuple[str, str], str]:
    out = {}
    for q in (payload or {}).get("qb_changes") or []:
        if isinstance(q, dict) and q.get("game_id") and q.get("team"):
            out[(q["game_id"], q["team"])] = clean(q.get("text"), paths, 200) or ""
    return out


def _iso(t: Any) -> str | None:
    if isinstance(t, dt.datetime):
        return (t if t.tzinfo else t.replace(tzinfo=dt.UTC)).isoformat()
    return None


def _byes(plan: WeekPlan | None, season: int, week: int, sched: pl.DataFrame | None) -> list[str]:
    if plan is not None and plan.season == season and plan.week == week and plan.slate:
        return list(plan.slate.teams_on_bye)
    if sched is None:
        return []
    from nflengine.ops.calendar import slate

    try:
        sl = slate(sched, season, week)
    except Exception:  # noqa: BLE001
        return []
    return list(sl.teams_on_bye) if sl else []


def _snapshot_date(paths: DataPaths) -> str | None:
    from nflengine.ingest.base import SnapshotStore

    try:
        snaps = SnapshotStore(paths.raw).snapshots("nflverse", "schedules")
    except Exception:  # noqa: BLE001
        return None
    return snaps[-1] if snaps else None


def _slate(
    paths: DataPaths, season: int, week: int, sched: pl.DataFrame | None, plan: WeekPlan | None
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if sched is not None:
        from nflengine.ops.calendar import with_kickoffs

        wk = (
            with_kickoffs(sched)
            .filter((pl.col("season") == season) & (pl.col("week") == week))
            .sort("kickoff_utc", "game_id")
        )
        stadiums = _stadiums(wk["game_id"].to_list(), wk)
        for r in wk.iter_rows(named=True):
            final = (
                {"home": int(r["home_score"]), "away": int(r["away_score"])}
                if r.get("result") is not None and r.get("home_score") is not None
                else None
            )
            rows.append(
                {
                    "game_id": r["game_id"],
                    "kickoff": _iso(r.get("kickoff_utc")),
                    "home": r["home_team"],
                    "away": r["away_team"],
                    "neutral": r.get("location") == "Neutral",
                    "stadium": clean(stadiums.get(r["game_id"]), None, 80),
                    "p_home": None,
                    "p_home_model_only": None,
                    "p_elo": None,
                    "p_market": None,
                    "pts_home": None,
                    "pts_away": None,
                    "margin": None,
                    "spread": num(r.get("spread_line")),
                    "total": num(r.get("total_line")),
                    "confidence": None,
                    "market_fallback": None,
                    "qb_home": clean(r.get("home_qb_name"), None, 60),
                    "qb_away": clean(r.get("away_qb_name"), None, 60),
                    "qb_change_home": None,
                    "qb_change_away": None,
                    "predicted_after_kickoff": False,
                    "final": final,
                    "hit": None,
                }
            )
    if plan is not None and plan.season == season and plan.week == week and plan.slate:
        neutral = set(plan.slate.neutral_sites)
        for g in rows:
            g["neutral"] = g["neutral"] or f"{g['away']}@{g['home']}" in neutral
    return {
        "season": season,
        "week": week,
        "status": "slate" if rows else "none",
        "model": None,
        "games": rows,
        "gaps": [],
        "source": {
            "kind": "schedule" if rows else "none",
            "snapshot_date": _snapshot_date(paths) if rows else None,
        },
        "byes": _byes(plan, season, week, sched),
        "finals": sum(1 for g in rows if g["final"]),
        "lines_used": 0,
    }


def get_games(
    paths: DataPaths,
    season: int,
    week: int,
    plan: WeekPlan | None = None,
    sched: pl.DataFrame | None = None,
) -> dict[str, Any]:
    run_dir = paths.run_dir(season, week)
    preds = read_parquet(run_dir / "predictions_games.parquet")
    if preds is None or preds.is_empty():
        return _slate(paths, season, week, sched, plan)

    primary = preds.filter(pl.col("is_primary")) if "is_primary" in preds.columns else preds
    model_only = (
        preds.filter(pl.col("variant") == "model_only")
        if "variant" in preds.columns
        else preds.head(0)
    )
    mo = dict(
        zip(model_only["game_id"].to_list(), model_only["home_win_prob"].to_list(), strict=True)
    )
    finals = curated(paths, "games", GAME_COLS)
    graded = (
        grade_games(primary, finals)
        if finals is not None
        else primary.with_columns(
            pl.lit(None, pl.Boolean).alias("pick_correct"),
            pl.lit(None, pl.Int64).alias("home_score"),
            pl.lit(None, pl.Int64).alias("away_score"),
            pl.lit(None, pl.Float64).alias("result"),
        )
    )
    graded = graded.sort("kickoff_utc", "game_id")
    qb = _qb_changes(read_json(run_dir / "payload.json"), paths)
    stadiums = _stadiums(graded["game_id"].to_list(), finals)

    games = []
    for r in graded.iter_rows(named=True):
        gid = r["game_id"]
        final = (
            {"home": int(r["home_score"]), "away": int(r["away_score"])}
            if r.get("result") is not None and r.get("home_score") is not None
            else None
        )
        after = (
            r.get("created_at") is not None
            and r.get("kickoff_utc") is not None
            and r["created_at"] >= r["kickoff_utc"]
        )
        games.append(
            {
                "game_id": gid,
                "kickoff": _iso(r.get("kickoff_utc")),
                "home": r["home_team"],
                "away": r["away_team"],
                "neutral": bool(r.get("neutral_site")),
                "stadium": clean(stadiums.get(gid), None, 80),
                "p_home": num(r.get("home_win_prob")),
                "p_home_model_only": num(mo.get(gid)),
                "p_elo": num(r.get("elo_prob")),
                "p_market": num(r.get("market_prob")),
                "pts_home": num(r.get("pred_home_points")),
                "pts_away": num(r.get("pred_away_points")),
                "margin": num(r.get("expected_margin")),
                "spread": num(r.get("spread_line")),
                "total": num(r.get("total_line")),
                "confidence": clean(r.get("confidence_label"), None, 20),
                "market_fallback": r.get("market_fallback"),
                "qb_home": clean(r.get("home_qb_name"), None, 60),
                "qb_away": clean(r.get("away_qb_name"), None, 60),
                "qb_change_home": qb.get((gid, r["home_team"])),
                "qb_change_away": qb.get((gid, r["away_team"])),
                "predicted_after_kickoff": bool(after),
                "final": final,
                # graded = final and predicted before kickoff (the report card's rule)
                "hit": r.get("pick_correct") if final is not None and r.get("graded") else None,
            }
        )

    gaps = sorted(
        (
            {"game_id": g["game_id"], "gap": round(g["p_home_model_only"] - g["p_market"], 4)}
            for g in games
            if g["p_home_model_only"] is not None and g["p_market"] is not None
        ),
        key=lambda x: (-abs(x["gap"]), x["game_id"]),
    )[:3]
    first = primary.row(0, named=True)
    return {
        "season": season,
        "week": week,
        "status": "predicted",
        "model": {
            "version": clean(first.get("model_version"), None, 80),
            "trained_through": clean(first.get("trained_through"), None, 20),
            "created_at": _iso(primary["created_at"].max())
            if "created_at" in primary.columns
            else None,
        },
        "games": games,
        "gaps": gaps,
        "source": {"kind": "predictions", "snapshot_date": None},
        "byes": _byes(plan, season, week, sched),
        "finals": sum(1 for g in games if g["final"]),
        "lines_used": sum(1 for g in games if g["market_fallback"] is False),
    }
