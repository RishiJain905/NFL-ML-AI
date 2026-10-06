"""The Results tab: how a week's calls did, once the next week's run has graded them.

Where each part comes from (CR01 probe, written down in the control-room README §9):
- **The headline numbers** (picks, Brier vs Elo, points error, watch-list hits, games not
  graded, the note): week N+1's `payload.json` -> `report_card`, the grading the digest
  published. Nothing is re-graded for them.
- **Game by game** (pick, final, hit, per-game Brier for the model / Elo / market, margin
  error) and the market's Brier: recomputed from week N's saved `predictions_games.parquet`
  and the curated finals with the report card's own code (`grade_games`, `week_metrics`).
  `consistent` says whether the recomputed picks and Brier equal the report card's (a
  corrected final score after the grading run would make them differ).
- **The watch list**: the report card's `watch_lookback` (actual, inside the range, beat the
  baseline) with week N's `watchlist.parquet` for the range and baseline numbers.
- **Player model vs baseline**: the season's `accuracy_scoreboard.parquet`, week N's rows of
  the payload's mode (live), n-weighted per position group.

Before week N+1's run: `{"status": "not_graded", "graded_by_week": N+1, "graded_on": ...}`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl

from nflengine.app.readers.common import clean, curated, num, read_json, read_parquet
from nflengine.digest.players import center
from nflengine.digest.report_card import grade_games, week_metrics
from nflengine.ops.calendar import EASTERN
from nflengine.paths import DataPaths

LAST_WEEK = 22  # the Super Bowl: no later run grades it (the season review does)


def _value(x: Any) -> float | None:
    return num(x.get("value")) if isinstance(x, dict) else num(x)


def _count(x: Any) -> int | None:
    v = _value(x)
    return int(v) if v is not None else None


def graded_on(sched: pl.DataFrame | None, season: int, week: int) -> str | None:
    """The Tuesday (ET) after the week's last kickoff: when the next run grades it."""
    if sched is None:
        return None
    from nflengine.ops.calendar import with_kickoffs

    wk = with_kickoffs(sched).filter((pl.col("season") == season) & (pl.col("week") == week))
    if wk.is_empty():
        return None
    last = wk["kickoff_utc"].max()
    if not isinstance(last, dt.datetime):
        return None
    day = (last if last.tzinfo else last.replace(tzinfo=dt.UTC)).astimezone(EASTERN).date()
    ahead = (1 - day.weekday()) % 7 or 7
    return (day + dt.timedelta(days=ahead)).isoformat()


def _not_graded(season: int, week: int, sched, reason: str) -> dict[str, Any]:
    last = week >= LAST_WEEK
    return {
        "season": season,
        "week": week,
        "status": "not_graded",
        "graded_by_week": None if last else week + 1,
        "graded_on": None if last else graded_on(sched, season, week),
        "reason": (
            "The Super Bowl isn't graded by a weekly run: `nfl scoreboard` and the season "
            "review grade it after the season (Season calendar, PROGRESS)"
            if last
            else reason
        ),
    }


def _games(graded: pl.DataFrame) -> list[dict[str, Any]]:
    out = []
    for r in graded.sort("kickoff_utc", "game_id").iter_rows(named=True):
        p = num(r.get("home_win_prob"))
        final = (
            {"home": int(r["home_score"]), "away": int(r["away_score"])}
            if r.get("result") is not None and r.get("home_score") is not None
            else None
        )
        g = bool(r.get("graded"))
        y = num(r.get("home_win"))

        def brier(q: Any, _y=y, _g=g) -> float | None:
            q = num(q)
            return round((q - _y) ** 2, 4) if _g and q is not None and _y is not None else None

        pick = None if p is None or p == 0.5 else (r["home_team"] if p > 0.5 else r["away_team"])
        margin = num(r.get("expected_margin"))
        out.append(
            {
                "game_id": r["game_id"],
                "home": r["home_team"],
                "away": r["away_team"],
                "pick": pick,
                "pick_prob": round(max(p, 1 - p), 4) if p is not None else None,
                "final": final,
                "graded": g,
                "hit": r.get("pick_correct") if g else None,
                "brier_model": brier(p),
                "brier_elo": brier(r.get("elo_prob")),
                "brier_market": brier(r.get("market_prob")),
                "margin_error": round((final["home"] - final["away"]) - margin, 2)
                if final and margin is not None
                else None,
                "note": "predicted after kickoff: not graded" if final and not g else None,
            }
        )
    return out


def _groups(paths: DataPaths, season: int, week: int, mode: str) -> list[dict[str, Any]]:
    sb = read_parquet(paths.runs / str(season) / "accuracy_scoreboard.parquet")
    if sb is None or sb.is_empty():
        return []
    rows = sb.filter((pl.col("season") == season) & (pl.col("week") == week))
    if "mode" in rows.columns:
        rows = rows.filter(pl.col("mode") == mode)
    rows = rows.filter(pl.col("improvement_pct").is_not_null() & (pl.col("n_scored") > 0))
    out = []
    for g in ("QB", "RB", "WR/TE", "EDGE/DL", "LB/S", "CB/S"):
        part = rows.filter(pl.col("position_group") == g)
        if part.is_empty():
            continue
        n = int(part["n_scored"].sum())
        imp = float((part["improvement_pct"] * part["n_scored"]).sum() / n)
        cov = part.filter(pl.col("coverage_80").is_not_null())
        coverage = (
            float((cov["coverage_80"] * cov["n_scored"]).sum() / cov["n_scored"].sum())
            if cov.height
            else None
        )
        out.append(
            {
                "group": g,
                "improvement_pct": round(imp, 2),
                "n": n,
                "coverage_80": round(coverage, 4) if coverage is not None else None,
                "targets": [
                    {
                        "target": t["target"],
                        "label": clean(t.get("target_label") or t["target"], None, 60),
                        "improvement_pct": round(float(t["improvement_pct"]), 2),
                        "n": int(t["n_scored"]),
                    }
                    for t in part.sort("target").iter_rows(named=True)
                ],
            }
        )
    return out


def _watch(
    card: dict[str, Any], watch: pl.DataFrame | None, paths: DataPaths
) -> list[dict[str, Any]]:
    nums: dict[tuple[str, str], dict[str, Any]] = {}
    for r in watch.iter_rows(named=True) if watch is not None else []:
        nums[(str(r.get("player_id")), str(r.get("target_label")))] = r
    out = []
    for x in card.get("watch_lookback") or []:
        if not isinstance(x, dict):
            continue
        r = nums.get((str(x.get("player_id")), str(x.get("target"))), {})
        try:
            proj = num(center(r)) if r.get("p50") is not None else _value(x.get("projection"))
        except (KeyError, TypeError, ValueError):  # a heuristic pick has no quantiles
            proj = _value(x.get("projection"))
        out.append(
            {
                "player": clean(x.get("player"), None, 80),
                "player_id": clean(x.get("player_id"), None, 20),
                "team": x.get("team") or "",
                "position": x.get("position") or "",
                "side": x.get("side") if x.get("side") in ("offense", "defense") else None,
                "target_label": clean(x.get("target"), None, 60),
                "unit": r.get("unit") or "",
                "projection": proj,
                "p10": num(r.get("p10")),
                "p90": num(r.get("p90")),
                "baseline": num(r.get("baseline")),
                "actual": _value(x.get("actual")),
                "played": bool(x.get("played")),
                "inside": x.get("inside") if isinstance(x.get("inside"), bool) else None,
                "hit": x.get("hit") if isinstance(x.get("hit"), bool) else None,
                "status": clean(x.get("status"), None, 40) or "",
                "text": clean(x.get("text"), paths, 300) or "",
            }
        )
    return out


def get_results(
    paths: DataPaths, season: int, week: int, sched: pl.DataFrame | None = None
) -> dict[str, Any]:
    nxt = paths.run_dir(season, week + 1)
    payload = read_json(nxt / "payload.json")
    card = (payload or {}).get("report_card") if payload else None
    if not isinstance(card, dict):
        return _not_graded(
            season, week, sched, f"graded by week {week + 1}'s run, which hasn't happened yet"
        )
    if card.get("scored_week") != week or card.get("status") != "scored":
        note = clean(card.get("note"), paths, 300) or f"week {week + 1}'s run graded nothing"
        return _not_graded(season, week, sched, note)

    preds = read_parquet(paths.run_dir(season, week) / "predictions_games.parquet")
    finals = curated(paths, "games", ["game_id", "home_score", "away_score", "result"])
    graded = (
        grade_games(preds, finals)
        if preds is not None and preds.height and finals is not None
        else pl.DataFrame()
    )
    m = week_metrics(graded) if graded.height else {}
    picks_c, picks_t = _count(card.get("picks_correct")), _count(card.get("picks_total"))
    brier, brier_elo = _value(card.get("brier")), _value(card.get("brier_elo"))
    consistent = bool(
        m
        and picks_c == m.get("picks_correct")
        and picks_t == m.get("picks_total")
        and brier is not None
        and m.get("brier_model") is not None
        and abs(brier - m["brier_model"]) < 0.0006
        and (brier_elo is None or abs(brier_elo - (m.get("brier_elo") or 0)) < 0.0006)
    )
    mode = ((payload or {}).get("meta") or {}).get("mode") or "live"
    return {
        "season": season,
        "week": week,
        "status": "graded",
        "graded_by_week": week + 1,
        "tiles": {
            "picks_correct": picks_c,
            "picks_total": picks_t,
            "brier_model": brier,
            "brier_elo": brier_elo,
            "brier_market": num(m.get("brier_market"), 4) if m else None,
            "points_mae": _value(card.get("points_mae")),
            "watch_hits": _count(card.get("watchlist_hits")),
            "watch_total": _count(card.get("watchlist_total")),
            "not_graded": int(card.get("not_graded") or 0),
        },
        "note": clean(card.get("note"), paths, 300),
        "consistent": consistent,
        "games": _games(graded) if graded.height else [],
        "players": _groups(paths, season, week, "live" if mode == "live" else "backtest"),
        "watch": _watch(
            card, read_parquet(paths.run_dir(season, week) / "watchlist.parquet"), paths
        ),
    }
