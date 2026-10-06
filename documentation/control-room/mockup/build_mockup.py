"""Rebuild the control-room mockup (a single HTML file) from this folder's sources.

The mockup is the visual spec for the control room (documentation/control-room/README.md).
Its sources (template.html, styles.css, app.js) live here; the data it shows is read from
the data root and embedded at build time, so no data is committed.

    uv run python documentation/control-room/mockup/build_mockup.py [--no-wandb]

Writes {NFL_DATA_ROOT}/cache/control-room-mockup/control-room.html. Read-only on the data
root otherwise. --no-wandb skips the W&B artifact listing (the Artifacts section then shows
"no versions"). The mockup was built from 2026 week 4 (the week-5 slate is the "this week").
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nflengine.paths import DataPaths, ensure_data_root

HERE = Path(__file__).parent
SEASON, WEEK, NEXT = 2026, 4, 5
WANDB_PROJECT = "models-ontario-tech-university/nfl-analytics-engine"
STEP_ORDER = ["ingest", "ready", "curate", "ratings", "game", "graph", "player", "digest"]


def num(x: Any, n: int = 3) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, n)


def clean(o: Any) -> Any:
    """NaN / inf → None, recursively (browsers reject NaN in JSON)."""
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


def drivers(d: Any) -> list[dict[str, Any]]:
    try:
        return [{"phrase": x["phrase"], "c": num(x["contribution"], 2)} for x in list(d)[:3]]
    except (TypeError, KeyError):
        return []


def player_row(x: pd.Series) -> dict[str, Any]:
    return {
        "player": x.player,
        "team": x.team,
        "opp": x.opponent,
        "home": bool(x.home),
        "pos": x.position,
        "group": x.group,
        "target": x.target_label,
        "unit": x.unit,
        "p10": num(x.p10, 1),
        "p50": num(x.p50, 1),
        "mean": num(x["mean"], 1),
        "p90": num(x.p90, 1),
        "baseline": num(x.baseline, 1),
        "outperf": num(x.outperformance, 1),
        "inj": None if pd.isna(x.injury_status) else x.injury_status,
        "conf": x.confidence,
    }


def week_files(paths: DataPaths, out: dict[str, Any]) -> None:
    w = paths.runs / str(SEASON) / f"week{WEEK:02d}"
    steps = json.loads((w / "weekly_run.json").read_text())["steps"]
    out["w4_steps"] = []
    for k in STEP_ORDER:
        s = steps[k]
        secs = dt.datetime.fromisoformat(s["finished"]) - dt.datetime.fromisoformat(s["started"])
        out["w4_steps"].append(
            {
                "name": k,
                "status": s["status"],
                "started": s["started"],
                "seconds": secs.total_seconds(),
                "detail": s["detail"],
            }
        )

    g = pd.read_parquet(w / "predictions_games.parquet")
    cur = pd.read_parquet(paths.curated / "games.parquet")
    games = []
    for gid, grp in g.groupby("game_id"):
        p = grp[grp.is_primary].iloc[0]
        mo = grp[grp.variant == "model_only"].iloc[0]
        c = cur[cur.game_id == gid].iloc[0]
        games.append(
            {
                "id": gid,
                "kick": p.kickoff_utc.isoformat(),
                "away": p.away_team,
                "home": p.home_team,
                "neutral": bool(p.neutral_site),
                "p_home": num(p.home_win_prob),
                "p_home_model_only": num(mo.home_win_prob),
                "p_elo": num(p.elo_prob),
                "p_market": num(p.market_prob),
                "pts_home": num(p.pred_home_points, 1),
                "pts_away": num(p.pred_away_points, 1),
                "margin": num(p.expected_margin, 1),
                "spread": num(p.spread_line, 1),
                "total": num(p.total_line, 1),
                "conf": p.confidence_label,
                "qb_home": p.home_qb_name,
                "qb_away": p.away_qb_name,
                "qb_src_home": p.home_qb_source,
                "qb_src_away": p.away_qb_source,
                "score_home": num(c.home_score, 0),
                "score_away": num(c.away_score, 0),
            }
        )
    out["w4_games"] = sorted(games, key=lambda x: x["kick"])
    out["w4_model"] = {
        "game": g.model_version.iloc[0],
        "trained_through": g.trained_through.iloc[0],
        "created_at": g.created_at.iloc[0].isoformat(),
    }

    wl = pd.read_parquet(w / "watchlist.parquet")
    out["w4_watch"] = [
        {**player_row(x), "kind": x.kind, "drivers": drivers(x.drivers), "rank": int(x["rank"])}
        for _, x in wl.iterrows()
    ]
    pp = pd.read_parquet(w / "predictions_players.parquet")
    main = pp[pp.is_main]
    out["w4_players"] = [
        player_row(x)
        for _, d in main.groupby("group")
        for _, x in d.sort_values("p50", ascending=False).head(18).iterrows()
    ]
    out["w4_player_counts"] = {
        "total": int(len(pp)),
        "model": pp.model_version.iloc[0],
        "by_target": {
            f"{k[0]}|{k[1]}": int(v)
            for k, v in pp.groupby(["group", "target_label"]).size().items()
        },
    }

    gr = json.loads((w / "graph_results.json").read_text())
    counts = gr["counts"]

    def rows(v: Any) -> int:
        if isinstance(v, list):
            return len(v)
        if isinstance(v, dict) and isinstance(v.get("results"), list):
            return len(v["results"])
        return 0

    def insight(i: dict[str, Any]) -> dict[str, Any]:
        return {
            "section": i["section"],
            "matchup": i["matchup"],
            "type": i["insight_type"],
            "query": i["graph_query"],
            "strength": i["strength"],
            "confidence": i["confidence"],
            "headline": i.get("headline"),
            "brief": i["brief"],
        }

    out["w4_graph"] = {
        "status": gr["status"],
        "built_at": gr["built_at"],
        "nodes": sum(v for k, v in counts.items() if k.startswith("node:")),
        "rels": sum(v for k, v in counts.items() if k.startswith("rel:")),
        "node_counts": {k[5:]: v for k, v in counts.items() if k.startswith("node:")},
        "timings": {
            k: num(v, 1) for k, v in gr["timings"].items() if not k.startswith(("node", "rel"))
        },
        "queries": {k: rows(v) for k, v in gr["queries"].items()},
        "selected": [insight(i) for lst in gr["selected"].values() for i in lst],
        "candidates": [
            insight(i) for i in sorted(gr["candidates"], key=lambda i: -i["strength"])[:14]
        ],
        "n_candidates": len(gr["candidates"]),
        "skipped": {k: len(v) if isinstance(v, list) else v for k, v in gr["skipped"].items()},
        "mismatches": len(gr["count_mismatches"]),
    }

    ch = json.loads((w / "checks.json").read_text())
    out["w4_checks"] = {
        k: ch.get(k) for k in ("passed", "regenerated", "banner", "writers", "final")
    }
    llm = json.loads((w / "raw_llm_output.json").read_text())  # metadata only, not the prose
    keys = (
        "provider",
        "latency_s",
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "cost",
        "regeneration",
        "finish_reason",
    )
    out["w4_llm"] = {
        "provider": llm["provider"],
        "model": llm["model"],
        "calls": [{k: c.get(k) for k in keys} for c in llm["calls"]],
    }
    out["w4_digest"] = paths.report_path(SEASON, WEEK).read_text(encoding="utf-8")

    nxt = cur[(cur.season == SEASON) & (cur.week == NEXT)].sort_values("kickoff_utc")
    out["w5_slate"] = [
        {
            "id": x.game_id,
            "kick": x.kickoff_utc.isoformat(),
            "away": x.away_team,
            "home": x.home_team,
            "spread": num(x.spread_line, 1),
            "total": num(x.total_line, 1),
            "stadium": x.stadium,
        }
        for x in nxt.itertuples()
    ]

    manifests = sorted((paths.raw / "_runs").glob("ingest-*.json"))
    picked = next((m for m in manifests if "20261004" in m.name), manifests[-1])
    man = json.loads(picked.read_text())
    out["w4_ingest"] = {
        "snapshot_date": man.get("snapshot_date"),
        "results": [
            {k: r.get(k) for k in ("source", "dataset", "status", "rows")} for r in man["results"]
        ],
    }
    q = json.loads((paths.curated / "_quality" / "latest.json").read_text())
    out["quality_checks"] = [
        {k: r.get(k) for k in ("name", "level", "passed", "detail")} for r in q["results"]
    ]
    out["quality"] = {"run_at": q["run_at"], "n": len(q["results"])}


def season_files(paths: DataPaths, out: dict[str, Any]) -> None:
    teams = pd.read_parquet(paths.curated / "teams.parquet")
    out["teams"] = {
        t.team: {
            "name": t.team_name,
            "nick": t.team_nick,
            "conf": t.team_conf,
            "div": t.team_division,
            "color": t.team_color,
        }
        for t in teams.itertuples()
    }
    elo = pd.read_parquet(paths.features / "team_elo.parquet")
    e = elo[elo.season == SEASON]
    out["elo"] = {t: [num(v, 1) for v in d.sort_values("week").elo] for t, d in e.groupby("team")}
    out["elo_weeks"] = [int(x) for x in sorted(e.week.unique())]
    rat = pd.read_parquet(paths.features / "team_ratings.parquet")
    rt = rat[(rat.season == SEASON) & (rat.week == WEEK)].set_index("team")
    out["ratings_w4"] = {
        t: {
            "net": num(x.net_epa),
            "off": num(x.off_epa),
            "def": num(x.def_epa),
            "pass": num(x.net_pass_epa),
            "rush": num(x.net_rush_epa),
        }
        for t, x in rt.iterrows()
    }
    sb = pd.read_parquet(paths.runs / str(SEASON) / "accuracy_scoreboard.parquet")
    out["sb2026"] = clean(json.loads(sb.to_json(orient="records")))

    bt = pd.read_parquet(paths.runs / "backtests" / "game" / "market" / "predictions_games.parquet")
    b = bt[bt.season == SEASON - 1].copy()
    b["mk"] = b.market_prob.fillna(b.market_ml_prob)
    weeks, acc = [], {"m": 0.0, "e": 0.0, "k": 0.0, "n": 0, "hits": 0}
    for week, d in b.groupby("week"):
        d = d.dropna(subset=["home_win"])
        hits = (d.home_win_prob > 0.5) == (d.home_win == 1)
        acc["m"] += ((d.home_win_prob - d.home_win) ** 2).sum()
        acc["e"] += ((d.elo_prob - d.home_win) ** 2).sum()
        acc["k"] += ((d.mk - d.home_win) ** 2).sum()
        acc["n"] += len(d)
        acc["hits"] += int(hits.sum())
        weeks.append(
            {
                "week": int(week),
                "n": int(len(d)),
                "acc": num(hits.mean()),
                "cum_model": num(acc["m"] / acc["n"], 4),
                "cum_elo": num(acc["e"] / acc["n"], 4),
                "cum_market": num(acc["k"] / acc["n"], 4),
                "cum_acc": num(acc["hits"] / acc["n"]),
            }
        )
    out["s2025_weeks"] = weeks
    d = b.dropna(subset=["home_win"])
    idx = np.clip(np.digitize(d.home_win_prob, np.linspace(0, 1, 11)) - 1, 0, 9)
    out["s2025_cal"] = [
        {
            "bin": i,
            "pred": num(d.home_win_prob[idx == i].mean()),
            "obs": num(d.home_win[idx == i].mean()),
            "n": int((idx == i).sum()),
        }
        for i in range(10)
        if (idx == i).sum() > 0
    ]
    ps = pd.read_parquet(paths.runs / "backtests" / "player" / "scoreboard.parquet")
    ps = ps[(ps.season == SEASON - 1) & ps.improvement_pct.notna()]
    out["s2025_player"] = [
        {
            "week": int(wk),
            "group": pg,
            "imp": num(np.average(d2.improvement_pct, weights=d2.n_scored), 2),
        }
        for (wk, pg), d2 in ps.groupby(["week", "position_group"])
    ]


def wandb_artifacts() -> dict[str, Any]:
    """Read-only artifact listing; the key comes from the settings module, never printed."""
    import wandb

    from nflengine.tracking import _wandb_env

    _wandb_env()
    api = wandb.Api(timeout=60)
    out: dict[str, Any] = {}
    for name, typ in [
        ("game-model", "model"),
        ("player-model", "model"),
        ("team-model", "model"),
        ("graph-results", "graph"),
        ("digest", "digest"),
        ("injury-update", "digest"),
    ]:
        try:
            rows = []
            for a in api.artifacts(typ, f"{WANDB_PROJECT}/{name}", per_page=50):
                run = a.logged_by()
                rows.append(
                    {
                        "version": a.version,
                        "aliases": list(a.aliases),
                        "created": str(a.created_at)[:19],
                        "run": run.id if run else None,
                        "size": a.size,
                    }
                )
            out[name] = rows
        except Exception as e:  # a collection that doesn't exist yet
            out[name] = {"error": type(e).__name__}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-wandb", action="store_true")
    args = ap.parse_args()
    paths = ensure_data_root()
    data: dict[str, Any] = {}
    week_files(paths, data)
    season_files(paths, data)
    data["wb_artifacts"] = {} if args.no_wandb else wandb_artifacts()
    blob = json.dumps(clean(data), allow_nan=False, ensure_ascii=False, default=str)
    html = (HERE / "template.html").read_text(encoding="utf-8")
    html = (
        html.replace("{{CSS}}", (HERE / "styles.css").read_text(encoding="utf-8"))
        .replace("{{JS}}", (HERE / "app.js").read_text(encoding="utf-8"))
        .replace("{{DATA}}", blob.replace("</", "<\\/"))
    )
    dest = paths.cache / "control-room-mockup" / "control-room.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
    print(f"wrote {dest} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
