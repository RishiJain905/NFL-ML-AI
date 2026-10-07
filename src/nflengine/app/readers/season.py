"""Season pages (CR03): the Scorecard and Teams & rankings.

**Scorecard, 2026 live:** the W&B Season Dashboard's own numbers. `ops.dashboard
.build_dashboard_data` runs on inputs this module reads itself (no memory maps, D100), for the
dashboard's run week (`runs/<S>/dashboard.json` → `last_run_week`), so every line and tile is
what the report shows. **2025 backtest (example):** the canonical walk-forward game backtest
(`runs/backtests/game/market/predictions_games.parquet`) and the player backtest scoreboard
(`runs/backtests/player/scoreboard.parquet`), scored with the same functions.

**Teams:** `features/team_elo.parquet` (a row for week W is the rating *entering* week W),
`features/team_ratings.parquet` (EPA ratings entering week W), the schedule snapshot and the
week's saved predictions.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from nflengine.app.readers.common import clean, num, read_json, read_parquet
from nflengine.app.readers.mlops import _llm, run_weeks, week_published
from nflengine.paths import DataPaths

GROUP_ORDER = ("QB", "RB", "WR/TE", "EDGE/DL", "LB/S", "CB/S")


def _graded_games(paths: DataPaths, season: int, before: int) -> pl.DataFrame:
    frames = []
    for w in range(1, before):
        df = read_parquet(paths.run_dir(season, w) / "predictions_games.parquet")
        if df is not None and df.height:
            frames.append(df)
    return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()


def _groups(sb: pl.DataFrame) -> list[dict[str, Any]]:
    """Season-to-date improvement per position group (the dashboard's `player/cum_*`)."""
    from nflengine.ops import drift

    if sb.is_empty():
        return []
    present = sb["position_group"].unique().to_list()
    order = [g for g in GROUP_ORDER if g in present] + sorted(
        g for g in present if g not in GROUP_ORDER and g != "TEAM"
    )
    out = []
    for g in order:
        series = drift.group_series(sb, g, None)
        if not series:
            continue
        last = series[-1]
        out.append(
            {
                "group": g,
                "improvement": num(last["improvement"], 3),
                "weeks": len(last["weeks"]),
                "live_weeks": int(last["live"]),
            }
        )
    return out


def _weeks_from_points(points: list[dict[str, Any]], games: dict[int, int]) -> list[dict]:
    out = []
    for p in points:
        if "season/brier_model" not in p and "season/cum_brier_model" not in p:
            continue
        w = int(p["season/week"])
        out.append(
            {
                "week": w,
                "games": games.get(w),
                **{
                    k: num(p.get(f"season/{k}"), 6)
                    for k in (
                        "brier_model",
                        "brier_elo",
                        "brier_market",
                        "cum_brier_model",
                        "cum_brier_elo",
                        "cum_brier_market",
                        "pick_accuracy",
                        "cum_pick_accuracy",
                    )
                },
            }
        )
    return out


def _calibration(curve: pl.DataFrame) -> list[dict[str, Any]]:
    if curve is None or curve.is_empty():
        return []
    return [
        {
            "bin": int(r["bin"]),
            "predicted": float(r["mean_prob"]),
            "observed": float(r["mean_outcome"]),
            "games": int(r["n"]),
        }
        for r in curve.iter_rows(named=True)
    ]


def _dashboard_info(paths: DataPaths, season: int) -> dict[str, Any]:
    d = read_json(paths.runs / str(season) / "dashboard.json") or {}
    return {
        "title": clean(d.get("title"), None, 80),
        "url": clean(d.get("url"), None, 300),
        "current_run_id": clean(d.get("last_run_id"), None, 20),
        "run_week": num(d.get("last_run_week")),
    }


def _empty_tiles() -> dict[str, Any]:
    return {
        "brier_model": None,
        "brier_elo": None,
        "brier_market": None,
        "pick_accuracy": None,
        "ece": None,
        "ece_chance": None,
        "games_graded": 0,
        "weeks_published": None,
        "checks_passed": None,
        "checks_total": None,
        "llm_spend": None,
        "player_improvement": None,
    }


def scorecard_live(paths: DataPaths, season: int) -> dict[str, Any]:
    from nflengine.ops import dashboard, drift
    from nflengine.ops.records import latest_runs

    info = _dashboard_info(paths, season)
    weeks_run = run_weeks(paths, season, 22)
    published = [w for w in weeks_run if week_published(paths, season, w)]
    # the dashboard's run week: graded weeks are the ones before it
    run_week = int(info["run_week"]) if info["run_week"] else (max(published) if published else 1)
    sc = read_parquet(paths.runs / str(season) / "season_scorecard.parquet")
    sb_raw = read_parquet(paths.runs / str(season) / "accuracy_scoreboard.parquet")
    history = read_parquet(paths.runs / str(season) / "pipeline_history.parquet")
    games = read_parquet(paths.curated / "games.parquet")
    preds = _graded_games(paths, season, run_week)
    data = dashboard.build_dashboard_data(
        season,
        run_week,
        paths=paths,
        # every input passed in (an empty frame when missing), so nothing is read with a
        # memory map behind the app's back (D100)
        scorecard=sc if sc is not None else pl.DataFrame(),
        scoreboard=sb_raw if sb_raw is not None else pl.DataFrame(),
        history=history if history is not None else pl.DataFrame(),
        games=games if games is not None else pl.DataFrame(),
        preds=preds,
    )
    from nflengine.digest.run import season_series

    sc_season = (
        sc.filter((pl.col("season") == season) & (pl.col("week") < run_week))
        if sc is not None and sc.height
        else pl.DataFrame()
    )
    game_counts = (
        {int(r["week"]): int(r["games"] or 0) for r in sc_season.iter_rows(named=True)}
        if sc_season.height
        else {}
    )
    weeks = _weeks_from_points(season_series(sc_season) if sc_season.height else [], game_counts)
    sb = (
        drift.prepare_scoreboard(sb_raw, season, run_week) if sb_raw is not None else pl.DataFrame()
    )
    s = data.summary

    checks_passed = checks_total = 0
    spend = 0.0
    spend_weeks = 0
    for w in published:
        c = read_json(paths.run_dir(season, w) / "checks.json")
        if c is not None:
            checks_total += 1
            checks_passed += int(bool(c.get("passed")))
        llm = _llm(paths, season, w)
        if llm and llm.get("total_cost") is not None:
            spend += float(llm["total_cost"])
            spend_weeks += 1
    pipeline = []
    if history is not None and history.height:
        for r in (
            latest_runs(history, "main").filter(pl.col("season") == season).iter_rows(named=True)
        ):
            pipeline.append(
                {
                    "week": int(r["week"]),
                    "status": clean(r.get("status"), None, 30) or "",
                    "on_time": r.get("on_time"),
                    "hours_before_deadline": num(r.get("hours_before_deadline"), 2),
                    "checks_passed": r.get("checks_passed"),
                    "regenerated": r.get("regenerated"),
                    "seconds": num(r.get("total_seconds"), 1),
                    "drift_alerts": num(r.get("drift_alerts")),
                    "launched_by": clean(r.get("launched_by"), None, 30),
                }
            )
    notes = []
    not_graded = int(sc_season["not_graded"].fill_null(0).sum()) if sc_season.height else 0
    if not_graded:
        notes.append(
            f"{not_graded} game(s) predicted after kickoff aren't graded (the report card's rule)."
        )
    missing = len(published) - len(pipeline)
    if missing > 0:
        notes.append(
            f"{missing} published week(s) ran before run records (P07) and have no pipeline row."
        )
    tiles = {
        **_empty_tiles(),
        "brier_model": num(s.get("cum_brier_model"), 6),
        "brier_elo": num(s.get("cum_brier_elo"), 6),
        "brier_market": num(s.get("cum_brier_market"), 6),
        "pick_accuracy": num(s.get("cum_pick_accuracy"), 6),
        "ece": num(s.get("season_ece"), 6),
        "ece_chance": num(s.get("ece_chance"), 6),
        "games_graded": int(s.get("games_graded") or 0),
        "weeks_published": len(published),
        "checks_passed": checks_passed if checks_total else None,
        "checks_total": checks_total or None,
        "llm_spend": round(spend, 6) if spend_weeks else None,
        "player_improvement": num(s.get("player_cum_improvement_all"), 3),
    }
    return {
        "season": season,
        "source": "live",
        "label": f"{season} live",
        "through_week": max((w["week"] for w in weeks), default=None),
        "tiles": tiles,
        "weeks": weeks,
        "calibration": _calibration(data.curve),
        "player_groups": _groups(sb),
        "pipeline": pipeline,
        "dashboard": {k: info[k] for k in ("title", "url", "current_run_id")},
        "notes": notes,
    }


def scorecard_backtest(paths: DataPaths, season: int) -> dict[str, Any]:
    """The canonical walk-forward backtest of `season` (the example before a live season has
    graded weeks), scored like the live season (`season_series`, `reliability`, `ece`)."""
    from nflengine.digest.run import season_series
    from nflengine.models import metrics as M
    from nflengine.ops import drift

    label = f"{season} backtest (example)"
    bt = read_parquet(paths.runs / "backtests" / "game" / "market" / "predictions_games.parquet")
    weeks: list[dict[str, Any]] = []
    calibration: list[dict[str, Any]] = []
    tiles = _empty_tiles()
    notes: list[str] = []
    if bt is not None and {"season", "week", "home_win", "home_win_prob"} <= set(bt.columns):
        b = bt.filter((pl.col("season") == season) & pl.col("home_win").is_not_null())
        mk = (
            pl.col("market_prob").fill_null(pl.col("market_ml_prob"))
            if "market_ml_prob" in b.columns
            else pl.col("market_prob")
        )
        b = b.with_columns(mk.alias("_market"))
        rows = []
        for w in sorted(b["week"].unique().to_list()):
            d = b.filter(pl.col("week") == w)
            y = d["home_win"].cast(pl.Float64)
            p = d["home_win_prob"].cast(pl.Float64)
            decided = d.filter((pl.col("home_win") != 0.5) & (pl.col("home_win_prob") != 0.5))
            picks = decided.filter(
                (pl.col("home_win_prob") > 0.5) == (pl.col("home_win") == 1)
            ).height
            dm = d.filter(pl.col("_market").is_not_null())
            rows.append(
                {
                    "season": season,
                    "week": int(w),
                    "games": d.height,
                    "picks_correct": picks,
                    "picks_total": decided.height,
                    "brier_model": float(((p - y) ** 2).mean()),
                    "brier_elo": float(((d["elo_prob"] - y) ** 2).mean())
                    if "elo_prob" in d.columns
                    else None,
                    "brier_market": float(((dm["_market"] - dm["home_win"]) ** 2).mean())
                    if dm.height
                    else None,
                    "points_mae": None,
                    "watchlist_hits": 0,
                    "watchlist_total": 0,
                }
            )
        sc = pl.DataFrame(rows) if rows else pl.DataFrame()
        counts = {r["week"]: r["games"] for r in rows}
        weeks = _weeks_from_points(season_series(sc) if sc.height else [], counts)
        if b.height:
            calibration = _calibration(M.reliability(b["home_win_prob"], b["home_win"]))
            last = weeks[-1] if weeks else {}
            tiles.update(
                brier_model=last.get("cum_brier_model"),
                brier_elo=last.get("cum_brier_elo"),
                brier_market=last.get("cum_brier_market"),
                pick_accuracy=last.get("cum_pick_accuracy"),
                ece=num(M.ece(b["home_win_prob"], b["home_win"]), 6),
                ece_chance=num(drift.ece_noise(np.asarray(b["home_win_prob"].to_numpy()))[0], 6),
                games_graded=b.height,
            )
        notes.append(
            "The canonical walk-forward backtest (runs/backtests/game/market): the market-informed "
            "probabilities the digest shows, regular season and playoffs."
        )
    sb_raw = read_parquet(paths.runs / "backtests" / "player" / "scoreboard.parquet")
    sb = (
        drift.prepare_scoreboard(sb_raw, season, 99)
        if sb_raw is not None and "season" in sb_raw.columns
        else pl.DataFrame()
    )
    groups = _groups(sb)
    if sb.height:
        tiles["player_improvement"] = num(
            drift.improvement_pct(sb.filter(pl.col("position_group") != "TEAM")), 3
        )
    return {
        "season": season,
        "source": "backtest",
        "label": label,
        "through_week": max((w["week"] for w in weeks), default=None),
        "tiles": tiles,
        "weeks": weeks,
        "calibration": calibration,
        "player_groups": groups,
        "pipeline": [],
        "dashboard": {"title": None, "url": None, "current_run_id": None},
        "notes": notes,
    }


def get_scorecard(paths: DataPaths, season: int, source: str) -> dict[str, Any]:
    return (
        scorecard_backtest(paths, season) if source == "backtest" else scorecard_live(paths, season)
    )


# ---- Teams & rankings --------------------------------------------------------------------


def _brief(
    sched: pl.DataFrame | None,
    preds: dict[str, float | None],
    season: int,
    week: int,
    team: str,
) -> dict[str, Any] | None:
    if sched is None or sched.is_empty():
        return None
    g = sched.filter(
        (pl.col("season") == season)
        & (pl.col("week") == week)
        & ((pl.col("home_team") == team) | (pl.col("away_team") == team))
    )
    if g.is_empty():
        return None
    r = g.row(0, named=True)
    ko = r.get("kickoff_utc")
    return {
        "week": week,
        "game_id": r["game_id"],
        "away": r["away_team"],
        "home": r["home_team"],
        "kickoff": ko.isoformat() if hasattr(ko, "isoformat") else (str(ko) if ko else None),
        "p_home": preds.get(r["game_id"]),
        "away_score": num(r.get("away_score")),
        "home_score": num(r.get("home_score")),
    }


def _shown_probs(paths: DataPaths, season: int, week: int) -> dict[str, float | None]:
    df = read_parquet(
        paths.run_dir(season, week) / "predictions_games.parquet",
        ["game_id", "is_primary", "home_win_prob"],
    )
    if df is None or "is_primary" not in df.columns:
        return {}
    return {
        r["game_id"]: num(r["home_win_prob"])
        for r in df.filter(pl.col("is_primary")).iter_rows(named=True)
    }


def get_teams(
    paths: DataPaths,
    season: int,
    week: int | None,
    sched: pl.DataFrame | None,
    info: dict[str, Any],
) -> dict[str, Any]:
    elo = read_parquet(paths.features / "team_elo.parquet", ["season", "week", "team", "elo"])
    empty = {"season": season, "week": None, "weeks": [], "teams": [], "risers": [], "fallers": []}
    if elo is None or elo.is_empty():
        return empty
    e = elo.filter(pl.col("season") == season)
    weeks = sorted(e["week"].unique().to_list())
    if not weeks:
        return empty
    wk = week if week in weeks else weeks[-1]
    prev_w = max((w for w in weeks if w < wk), default=None)
    ratings = read_parquet(paths.features / "team_ratings.parquet")
    rt: dict[str, dict[str, Any]] = {}
    if ratings is not None and "season" in ratings.columns:
        for r in ratings.filter((pl.col("season") == season) & (pl.col("week") == wk)).iter_rows(
            named=True
        ):
            rt[r["team"]] = r
    now = {r["team"]: float(r["elo"]) for r in e.filter(pl.col("week") == wk).iter_rows(named=True)}
    prev = (
        {
            r["team"]: float(r["elo"])
            for r in e.filter(pl.col("week") == prev_w).iter_rows(named=True)
        }
        if prev_w
        else {}
    )
    rank_now = {t: i + 1 for i, t in enumerate(sorted(now, key=lambda t: -now[t]))}
    rank_prev = {t: i + 1 for i, t in enumerate(sorted(prev, key=lambda t: -prev[t]))}
    hist: dict[str, list[dict[str, Any]]] = {}
    for r in e.filter(pl.col("week") <= wk).sort("week").iter_rows(named=True):
        hist.setdefault(r["team"], []).append(
            {"week": int(r["week"]), "elo": round(float(r["elo"]), 1)}
        )
    teams_info = info.get("teams") or {}
    probs_now = _shown_probs(paths, season, wk)
    probs_next = _shown_probs(paths, season, wk + 1)
    rows = []
    for t in sorted(now, key=lambda t: rank_now[t]):
        r = rt.get(t) or {}
        ti = teams_info.get(t) or {}
        rows.append(
            {
                "team": t,
                "conf": ti.get("conf"),
                "div": ti.get("div"),
                "rank": rank_now[t],
                "prev_rank": rank_prev.get(t),
                "elo": round(now[t], 1),
                "elo_change": round(now[t] - prev[t], 1) if t in prev else None,
                "elo_by_week": hist.get(t, []),
                "net_epa": num(r.get("net_epa"), 4),
                "off_epa": num(r.get("off_epa"), 4),
                "def_epa": num(r.get("def_epa"), 4),
                "pass_epa": num(r.get("net_pass_epa"), 4),
                "rush_epa": num(r.get("net_rush_epa"), 4),
                "this_week": _brief(sched, probs_now, season, wk, t),
                "next_week": _brief(sched, probs_next, season, wk + 1, t),
            }
        )
    movers = sorted(
        (
            {"team": x["team"], "change": x["elo_change"]}
            for x in rows
            if x["elo_change"] is not None
        ),
        key=lambda m: -m["change"],
    )
    return {
        "season": season,
        "week": wk,
        "weeks": weeks,
        "teams": rows,
        "risers": movers[:5],
        "fallers": list(reversed(movers[-5:])) if movers else [],
    }
