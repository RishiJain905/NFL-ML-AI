"""A complete synthetic week for the CR01 week readers: every file they read, with planted
text that must never reach the browser (the LLM's raw text, a credential-shaped detail, the
data root's own path). Imported as `from week_fixtures import ...` (no conftest in
subfolders)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import polars as pl
from app_helpers import ALL_OK, write_week

from nflengine.paths import DataPaths

SECRET_RAW = "RAW-ATTEMPT-TEXT-never-sent"  # raw LLM text and query rows: never sent
LEAKY_DETAIL = "upload failed: api_key=SENTINEL-leaky-123 at https://example.com/x?token=abc"
KICK = dt.datetime(2026, 10, 4, 17, 0, tzinfo=dt.UTC)
MADE = dt.datetime(2026, 10, 3, 18, 6, tzinfo=dt.UTC)
REPORT_MD = (
    "# NFL digest: {season} week {week}\n\n"
    "| Game | Pick |\n|---|---|\n| Colts at Commanders | Colts |\n\n"
    "_Writer: OpenRouter (z-ai/glm-5.3-flash), prompt `248ed5fb0b33`_\n"
)
STEP_TIMES = {
    "ingest": ("2026-10-04T03:18:17", "2026-10-04T03:18:42"),
    "ready": ("2026-10-04T03:18:42", "2026-10-04T03:18:42"),
    "curate": ("2026-10-04T03:18:42", "2026-10-04T03:18:58"),
    "ratings": ("2026-10-04T03:18:58", "2026-10-04T03:19:02"),
    "game": ("2026-10-04T03:19:02", "2026-10-04T03:19:17"),
    "graph": ("2026-10-04T03:19:17", "2026-10-04T03:21:44"),
    "player": ("2026-10-04T03:59:25", "2026-10-04T04:00:39"),
    "digest": ("2026-10-04T04:23:24", "2026-10-04T05:02:13"),
}


def write_curated(paths: DataPaths, season: int = 2026, week: int = 4) -> None:
    """Curated `games` (two finals, one NaN stadium, one unplayed) and `teams`."""
    w = f"{season}_{week:02d}"
    pl.DataFrame(
        {
            "game_id": [f"{w}_PIT_CLE", f"{w}_IND_WAS", f"{w}_KC_LV"],
            "home_score": [27, 20, None],
            "away_score": [24, 27, None],
            "result": [3.0, -7.0, None],
            "stadium": ["Huntington Bank Field", None, "Allegiant Stadium"],
        },
        strict=False,
    ).write_parquet(paths.curated / "games.parquet")
    pl.DataFrame(
        {
            "team": ["CLE", "PIT", "WAS", "IND", "KC", "LV"],
            "team_name": [
                "Cleveland Browns",
                "Pittsburgh Steelers",
                "Washington Commanders",
                "Indianapolis Colts",
                "Kansas City Chiefs",
                "Las Vegas Raiders",
            ],
            "team_nick": ["Browns", "Steelers", "Commanders", "Colts", "Chiefs", "Raiders"],
            "team_conf": ["AFC", "AFC", "NFC", "AFC", "AFC", "AFC"],
            "team_division": [
                "AFC North",
                "AFC North",
                "NFC East",
                "AFC South",
                "AFC West",
                "AFC West",
            ],
            "team_color": ["#311D00", "#FFB612", "#5A1414", "#002C5F", "#E31837", "not-a-colour"],
        }
    ).write_parquet(paths.curated / "teams.parquet")


def game_rows(season: int, week: int) -> pl.DataFrame:
    """Three games x (market row shown, model-only row). Thursday's was predicted after its
    kickoff (a kept pick); one Elo value is NaN; one game has no market."""
    w = f"{season}_{week:02d}"
    rows = []
    for i, gid in enumerate([f"{w}_PIT_CLE", f"{w}_IND_WAS", f"{w}_KC_LV"]):
        away, home = gid.split("_")[2:]
        kick = KICK - dt.timedelta(days=3) if i == 0 else KICK
        for variant, p in (("market", (0.40, 0.36, 0.55)[i]), ("model_only", (0.52, 0.30, 0.6)[i])):
            rows.append(
                {
                    "season": season,
                    "week": week,
                    "game_id": gid,
                    "game_type": "REG",
                    "kickoff_utc": kick,
                    "home_team": home,
                    "away_team": away,
                    "neutral_site": i == 1,
                    "variant": variant,
                    "is_primary": variant == "market",
                    "market_fallback": False,
                    "home_win_prob": p,
                    "away_win_prob": 1 - p,
                    "expected_margin": (-3.3, -4.7, 1.5)[i],
                    "pred_home_points": 18.0 + i,
                    "pred_away_points": 21.0,
                    "confidence_label": "lean",
                    "elo_prob": (0.46, float("nan"), 0.5)[i],
                    "market_prob": (0.42, 0.40, None)[i],
                    "spread_line": (-2.5, -3.0, 1.0)[i],
                    "total_line": 38.5,
                    "home_qb_name": "Home QB",
                    "away_qb_name": "Away QB",
                    "model_version": f"game-model-v0:{season}-w{week:02d}",
                    "trained_through": f"{season}-w{week - 1:02d}",
                    "created_at": MADE,
                }
            )
    return pl.DataFrame(rows, strict=False)


def player_rows(season: int, week: int) -> pl.DataFrame:
    """A count stat (tackles), an amount (receiving yards) and a yes/no stat (TD, no range)."""
    base = {
        "season": season,
        "week": week,
        "game_id": f"{season}_{week:02d}_KC_LV",
        "kickoff_utc": KICK,
        "opponent": "KC",
        "home": True,
        "team": "LV",
        "confidence": "medium",
        "model_version": f"player-model-v1:{season}-w{week:02d}",
        "trained_through": f"{season}-w{week - 1:02d}",
        "drivers": [
            {
                "feature": "use_snap_l1",
                "phrase": "his snap share in his last game",
                "value": 1.0,
                "contribution": 0.9,
            }
        ],
    }
    lb = {"player_id": "00-1", "player": "Jake Hansen", "position": "LB", "group": "LB/S"}
    te = {"player_id": "00-2", "player": "Brock Bowers", "position": "TE", "group": "WR/TE"}
    rows = [
        {
            **base,
            **lb,
            "target": "tackles",
            "target_label": "tackles",
            "kind": "count",
            "unit": "count",
            "is_main": True,
            "p10": 2.0,
            "p50": 5.0,
            "p90": 8.0,
            "mean": 5.29,
            "p_ge1": None,
            "baseline": 3.17,
            "baseline_p_ge1": None,
            "outperformance": 2.12,
            "injury_status": "Questionable",
        },
        {
            **base,
            **te,
            "target": "rec_yds",
            "target_label": "receiving yards",
            "kind": "amount",
            "unit": "yards",
            "is_main": True,
            "p10": 20.0,
            "p50": 61.0,
            "p90": 110.0,
            "mean": 63.0,
            "p_ge1": None,
            "baseline": 70.0,
            "baseline_p_ge1": None,
            "outperformance": -9.0,
            "injury_status": None,
        },
        {
            **base,
            **te,
            "target": "td",
            "target_label": "touchdowns",
            "kind": "prob",
            "unit": "prob",
            "is_main": False,
            "p10": None,
            "p50": None,
            "p90": None,
            "mean": 0.31,
            "p_ge1": 0.31,
            "baseline": 0.25,
            "baseline_p_ge1": 0.25,
            "outperformance": 0.06,
            "injury_status": None,
        },
    ]
    return pl.DataFrame(rows, strict=False)


def _watch_item(
    name: str, pid: str, target: str, proj: str, rng: str, vs: str, drivers: list
) -> dict:
    return {
        "player": name,
        "player_id": pid,
        "target": target,
        "projection": {"value": 0, "display": proj},
        "interval": {"value": 0, "display": rng},
        "vs_baseline": {"value": 0, "display": vs},
        "drivers": drivers,
    }


def report_card(graded_week: int) -> dict[str, Any]:
    """Week N+1's report card grading week N: 1 of 1 picks (Thursday's kept pick isn't
    graded), Brier 0.1296 (IND@WAS: 0.36 for the home team, the away team won)."""
    return {
        "status": "scored",
        "scored_week": graded_week,
        "note": None,
        "picks_correct": {"value": 1, "display": "1"},
        "picks_total": {"value": 1, "display": "1"},
        "brier": {"value": 0.1296, "display": "0.130"},
        "brier_elo": None,
        "points_mae": {"value": 4.4, "display": "4.4 points"},
        "watchlist_hits": {"value": 1, "display": "1"},
        "watchlist_total": {"value": 1, "display": "1"},
        "not_graded": 1,
        "watch_lookback": [
            {
                "player": "Jake Hansen",
                "player_id": "00-1",
                "team": "LV",
                "position": "LB",
                "target": "tackles",
                "text": "projected 5.3 tackles, range 2–8 tackles → actual 7 tackles: inside",
                "played": True,
                "hit": True,
                "inside": True,
                "side": None,
                "actual": {"value": 7.0, "display": "7 tackles"},
                "status": "",
            },
            {
                "player": "Brock Bowers",
                "player_id": "00-2",
                "team": "LV",
                "position": "TE",
                "target": "receiving yards",
                "text": "did not play",
                "played": False,
                "hit": None,
                "inside": None,
                "side": None,
                "actual": None,
                "status": "did not play",
            },
        ],
    }


def graph_results(season: int, week: int, root: str) -> dict[str, Any]:
    used = {
        "insight_id": "injury_ripple:00-9",
        "insight_type": "injury_ripple",
        "section": "matchup_risk",
        "strength": 0.784,
        "confidence": "medium",
        "graph_query": "q2_injury_ripple",
        "game_id": f"{season}_{week:02d}_KC_LV",
        "matchup": "Chiefs at Raiders",
        "headline": "A starter is out.",
        "brief": f"Brief with a path {root}\\runs\\x.json",
        "note": "",
    }
    return {
        "status": "ok",
        "season": season,
        "week": week,
        "mode": "live",
        "built_at": "2026-10-04T07:21:41+00:00",
        "counts": {"node:Team": 32, "node:Player": 100, "rel:PLAYED_FOR": 500, "rel:AT": 20},
        "count_mismatches": {},
        "timings": {
            "wipe_s": 47.5,
            "load_s": 88.2,
            "queries_s": 5.5,
            "total_s": 143.6,
            "node:Team_s": 0.2,
        },
        "queries": {
            "q2_injury_ripple": {
                "name": "q2_injury_ripple",
                "rows": 26,
                "seconds": 1.2,
                "error": None,
                "results": [{"player": SECRET_RAW}],
            },
            "q5_coach_reunion": {
                "name": "q5_coach_reunion",
                "rows": 0,
                "seconds": 0.3,
                "error": None,
                "results": [],
            },
        },
        "candidates": [
            used,
            {
                **used,
                "insight_id": "revenge:00-7:CLE",
                "insight_type": "revenge",
                "section": "non_obvious",
                "strength": 0.9,
                "graph_query": "q1_revenge",
                "brief": "Started already.",
            },
            {
                **used,
                "insight_id": "unit_mismatch:x",
                "insight_type": "unit_mismatch",
                "strength": 0.5,
                "graph_query": "q11_unit_mismatch",
                "brief": "Not picked.",
            },
        ],
        "selected": {"matchup_risk": [used], "non_obvious": []},
        "skipped": {"started": ["revenge:00-7:CLE"], "novelty": [], "duplicate_player": []},
        "gds": {
            "status": "ok",
            "version": "2.13.13",
            "seconds": 23.2,
            "jobs": {"pass_network": {"status": "ok", "seconds": 22.3, "written": 514}},
        },
    }


def write_full_week(
    paths: DataPaths,
    season: int = 2026,
    week: int = 4,
    *,
    summary: bool = True,
    report_card_for: int | None = None,
) -> Path:
    """A published week with every file, run in three sittings (week 4's shape); run records
    unless `summary=False`. With `report_card_for=N` its payload grades week N."""
    root = str(paths.root)
    details = {
        "ingest": f"30 datasets; manifest {root}\\raw\\_runs\\ingest-1.json",
        "game": f"predictions {root}\\runs\\{season}\\week{week:02d}\\predictions_games.parquet; "
        "W&B https://wandb.ai/ent/proj/runs/gm123456",
        "graph": "15,522 nodes; W&B https://wandb.ai/ent/proj/runs/gr123456",
        "player": LEAKY_DETAIL,
        "digest": f"{root}/reports/{season}/week{week:02d}-digest.md (checks passed); "
        "W&B https://wandb.ai/ent/proj/runs/dg123456",
    }
    run_dir = write_week(paths, season, week, ALL_OK, report=True, details=details)
    state = json.loads((run_dir / "weekly_run.json").read_text())
    for k, (a, b) in STEP_TIMES.items():
        state["steps"][k].update(started=a, finished=b)
    (run_dir / "weekly_run.json").write_text(json.dumps(state))
    text = REPORT_MD.format(season=season, week=week)
    paths.report_path(season, week).write_bytes(text.encode("utf-8"))
    (run_dir / "digest.md").write_bytes(text.encode("utf-8"))
    if summary:
        steps = {k: {"status": "ok", "seconds": 10.0, "this_run": k == "digest"} for k in ALL_OK}
        (run_dir / "run_summary.json").write_text(
            json.dumps(
                {
                    "season": season,
                    "week": week,
                    "status": "ok",
                    "on_time": True,
                    "finished": "2026-10-04T05:02:43",
                    "error": None,
                    "steps": steps,
                }
            )
        )
    ok = {"name": "number_provenance", "level": "fail", "passed": True, "issues": []}
    bad = {
        "name": "spelled_out_numbers",
        "level": "fail",
        "passed": False,
        "issues": [LEAKY_DETAIL],
    }
    (run_dir / "checks.json").write_text(
        json.dumps(
            {
                "passed": True,
                "regenerated": True,
                "banner": False,
                "writers": ["openrouter", "openrouter"],
                "fallback_notes": [],
                "first_attempt": {
                    "passed": False,
                    "failed": ["spelled_out_numbers"],
                    "warnings": [],
                    "word_counts": {"game_outlook": 61},
                    "checks": [ok, bad],
                },
                "final": {
                    "passed": True,
                    "failed": [],
                    "warnings": [],
                    "word_counts": {"game_outlook": 84, "team_trends": 102},
                    "checks": [ok],
                },
            }
        )
    )
    call = {
        "model": "z-ai/glm-5.3-flash",
        "provider": "DeepInfra",
        "latency_s": 1479.17,
        "prompt_tokens": 15691,
        "completion_tokens": 60899,
        "reasoning_tokens": 59896,
        "cost": 0.0164,
        "finish_reason": "stop",
        "regeneration": False,
        "parsed": True,
        "secret_field": SECRET_RAW,
    }
    (run_dir / "raw_llm_output.json").write_text(
        json.dumps(
            {
                "provider": "openrouter",
                "model": "z-ai/glm-5.3-flash",
                "writers": ["openrouter"],
                "fallback_notes": [],
                "calls": [call, {**call, "regeneration": True, "cost": 0.0092}],
                "attempts": [{"report_card": SECRET_RAW}],
                "prompt": SECRET_RAW,
            }
        )
    )
    game_rows(season, week).write_parquet(run_dir / "predictions_games.parquet")
    players = player_rows(season, week)
    players.write_parquet(run_dir / "predictions_players.parquet")
    players.filter(pl.col("is_main")).with_columns(
        pl.Series("rank", [1, 2], pl.Int32), pl.lit("model").alias("source")
    ).write_parquet(run_dir / "watchlist.parquet")
    hansen = _watch_item(
        "Jake Hansen",
        "00-1",
        "tackles",
        "5.3 tackles",
        "2–8 tackles",
        "2.1 tackles above his baseline",
        ["his snap share in his last game (puts the projection 0.9 tackles above a typical LB)"],
    )
    bowers = _watch_item(
        "Brock Bowers",
        "00-2",
        "receiving yards",
        "61 receiving yards",
        "20–110 receiving yards",
        "9 receiving yards below his baseline",
        [],
    )
    tough = {
        **bowers,
        "team": "LV",
        "opponent": "KC",
        "position": "TE",
        "projection": {"value": 61, "display": "61 receiving yards"},
        "baseline": {"value": 70, "display": "70 receiving yards"},
    }
    payload = {
        "meta": {"season": season, "week": week, "mode": "live"},
        "qb_changes": [
            {
                "game_id": f"{season}_{week:02d}_IND_WAS",
                "team": "WAS",
                "qb": "Marcus Mariota",
                "regular": "Jayden Daniels",
                "text": "Marcus Mariota starts in place of Jayden Daniels",
            }
        ],
        "players_to_watch": [hansen, bowers],
        "tough_spots": [tough],
        "report_card": report_card(report_card_for) if report_card_for is not None else None,
    }
    (run_dir / "payload.json").write_text(json.dumps(payload))
    (run_dir / "graph_results.json").write_text(json.dumps(graph_results(season, week, root)))
    return run_dir


def write_scoreboard(paths: DataPaths, season: int, week: int) -> None:
    """Accuracy scoreboard rows for one week: two live groups, one backtest row (ignored)."""
    pl.DataFrame(
        {
            "season": [season] * 3,
            "week": [week] * 3,
            "target": ["tackles", "rec_yds", "pass_yds"],
            "position_group": ["LB/S", "WR/TE", "QB"],
            "target_label": ["tackles", "receiving yards", "passing yards"],
            "n_scored": [100, 200, 30],
            "mae_model": [1.0, 20.0, 60.0],
            "mae_baseline": [1.1, 22.0, 59.0],
            "improvement_pct": [9.0, 9.1, -1.7],
            "coverage_80": [0.8, 0.82, 0.7],
            "mode": ["live", "live", "backtest"],
        }
    ).write_parquet(paths.runs / str(season) / "accuracy_scoreboard.parquet")


def full_week_paths(root: Path, **kw: Any) -> DataPaths:
    """A fixture data root with curated tables and one complete week (2026 week 4)."""
    from app_helpers import make_paths

    paths = make_paths(root)
    write_curated(paths)
    write_full_week(paths, **kw)
    return paths
