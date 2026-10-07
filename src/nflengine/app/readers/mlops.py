"""The week's MLOps tab (CR03): Health · W&B runs · Artifacts.

Local files first (control-room README §3, CR03 "Local first"): every number on these sections
comes from the files the run wrote on D: (`run_summary.json`, `weekly_run.json`, the events
file, the ingest manifest, `curated/_quality/latest.json`, the week's prediction files,
`graph_results.json`, `checks.json`, `raw_llm_output.json`, `pipeline_history.parquet`,
`runs/<S>/dashboard.json`). W&B (`app/wandb_api.py`) adds what only it holds: the run list and
links, artifact versions, aliases and lineage. A week without run records (2026 week 4, before
P07) shows what exists, with a notice.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.app.readers.common import (
    STEP_NAMES,
    clean,
    num,
    parse_time,
    read_json,
    read_parquet,
    read_text,
    summary_is_current,
    wandb_urls,
)
from nflengine.app.readers.digest import _writer
from nflengine.app.readers.meta import to_local_iso
from nflengine.app.readers.pipeline import get_pipeline, step_times
from nflengine.paths import DataPaths
from nflengine.weekly import already_published, read_state

MANIFEST = re.compile(r"ingest-[0-9T]+\.json")
GRAPH_WRITTEN = re.compile(r"graph projections: ([\d,]+) written")
GRAPH_STAGES = ("wipe", "load", "gds", "queries", "tables", "counts", "schema", "inputs")
PARTIAL_NOTICE = (
    "This week ran before run records existed (P07): no run summary, so no freshness, drift "
    "or week-over-week numbers. What the run's own files hold is shown."
)
NO_RUN_NOTICE = "No run for this week yet."


# ---- small readers ------------------------------------------------------------------------


def _rows(path: Path) -> int | None:
    """A Parquet file's row count from its footer (one short read, no memory map)."""
    if not path.exists():
        return None
    try:
        import pyarrow.parquet as pq

        with path.open("rb") as f:
            return int(pq.ParquetFile(f).metadata.num_rows)
    except Exception:  # noqa: BLE001 - a half-written file is "no data"
        return None


def _week_state(paths: DataPaths, season: int, week: int) -> tuple[dict, dict | None]:
    """The week's steps (weekly_run.json) and its run summary when it's current."""
    run_dir = paths.run_dir(season, week)
    state = read_state(run_dir / "weekly_run.json") or {}
    steps = {k: v for k, v in (state.get("steps") or {}).items() if k in STEP_NAMES}
    summary = read_json(run_dir / "run_summary.json")
    if not summary_is_current(summary, steps):
        summary = None
    return steps, summary


def run_start(paths: DataPaths, season: int, week: int) -> dict[str, Any] | None:
    """The newest weekly run's `run_start` event (command, launched by, via), when an events
    file exists (CR02 onwards). Injury updates write their own events and are skipped."""
    from nflengine.app.runner import read_events

    folder = paths.run_dir(season, week) / "events"
    try:
        files = sorted(folder.glob("*.jsonl"), reverse=True) if folder.exists() else []
    except OSError:
        return None
    for f in files:
        for e in read_events(f)[:3]:
            if e.get("type") == "run_start" and e.get("kind") in ("weekly", "resume", "rehearsal"):
                return e
    return None


def graph_totals(paths: DataPaths, season: int, week: int) -> dict[str, Any] | None:
    g = read_json(paths.run_dir(season, week) / "graph_results.json")
    if g is None:
        return None
    counts = g.get("counts") or {}
    nodes = sum(int(v) for k, v in counts.items() if k.startswith("node:") and _isnum(v))
    rels = sum(int(v) for k, v in counts.items() if k.startswith("rel:") and _isnum(v))
    timings = g.get("timings") or {}
    return {
        "status": clean(g.get("status"), None, 40),
        "built_at": clean(g.get("built_at"), None, 40),
        "nodes": nodes or None,
        "relationships": rels or None,
        "mismatches": len(g.get("count_mismatches") or {}),
        "stages": [
            {"stage": s, "seconds": t}
            for s in GRAPH_STAGES
            if (t := num(timings.get(f"{s}_s"), 2)) is not None
        ],
        "total_seconds": num(timings.get("total_s"), 2),
    }


def _isnum(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _llm(paths: DataPaths, season: int, week: int) -> dict[str, Any] | None:
    run_dir = paths.run_dir(season, week)
    raw = read_json(run_dir / "raw_llm_output.json")
    text = read_text(paths.report_path(season, week)) or read_text(run_dir / "digest.md")
    return _writer(raw, text)


def _manifest_path(paths: DataPaths, steps: dict[str, Any]) -> Path | None:
    detail = str((steps.get("ingest") or {}).get("detail") or "")
    m = MANIFEST.search(detail)
    if m:
        return paths.raw / "_runs" / m.group(0)
    # no name in the detail: the newest manifest finished inside the ingest step's window
    st = steps.get("ingest") or {}
    a, b = parse_time(st.get("started")), parse_time(st.get("finished"))
    if a is None or b is None:
        return None
    best = None
    try:
        files = sorted((paths.raw / "_runs").glob("ingest-*.json"))
    except OSError:
        return None
    for f in files:
        man = read_json(f) or {}
        t = parse_time(man.get("finished"))
        if t is not None and a <= t <= b:
            best = f
    return best


def _in_window(when: Any, step: dict[str, Any] | None) -> bool:
    t = parse_time(when)
    a, b = parse_time((step or {}).get("started")), parse_time((step or {}).get("finished"))
    return bool(t and a and b and a.timestamp() - 2 <= t.timestamp() <= b.timestamp() + 2)


# ---- week measures (this week vs last week) ------------------------------------------------

MEASURES = (
    ("Time in steps", "seconds"),
    ("GLM writing the digest", "seconds"),
    ("LLM cost", "usd"),
    ("Games predicted", "count"),
    ("Player projections", "count"),
    ("Graph nodes", "count"),
    ("Datasets ingested", "count"),
    ("Digest passed first time", "flag"),
)


def week_measures(paths: DataPaths, season: int, week: int) -> dict[str, float | None] | None:
    """One run week's numbers for "This week vs last week", or None when it has no run."""
    run_dir = paths.run_dir(season, week)
    if not (run_dir / "weekly_run.json").exists():
        return None
    steps, summary = _week_state(paths, season, week)
    secs, _ = step_times(paths, season, week)
    done = [s for s in secs.values() if s is not None]
    llm = _llm(paths, season, week)
    games = read_parquet(run_dir / "predictions_games.parquet", ["game_id"])
    checks = read_json(run_dir / "checks.json")
    datasets = num(((summary or {}).get("ingest") or {}).get("datasets"))
    if datasets is None:
        man = _manifest_path(paths, steps)
        res = (read_json(man) or {}).get("results") if man else None
        datasets = float(len(res)) if isinstance(res, list) else None
    g = graph_totals(paths, season, week)
    return {
        "Time in steps": round(sum(done), 1) if done else None,
        "GLM writing the digest": (llm or {}).get("total_seconds"),
        "LLM cost": (llm or {}).get("total_cost"),
        "Games predicted": float(games["game_id"].n_unique()) if games is not None else None,
        "Player projections": num(_rows(run_dir / "predictions_players.parquet")),
        "Graph nodes": num((g or {}).get("nodes")),
        "Datasets ingested": datasets,
        "Digest passed first time": (
            float(bool(checks.get("passed")) and not checks.get("regenerated")) if checks else None
        ),
    }


def run_weeks(paths: DataPaths, season: int, upto: int) -> list[int]:
    """The season's weeks up to `upto` that have a run folder with a weekly_run.json."""
    return [
        w for w in range(1, upto + 1) if (paths.run_dir(season, w) / "weekly_run.json").exists()
    ]


def vs_last(paths: DataPaths, season: int, week: int) -> list[dict[str, Any]]:
    weeks = run_weeks(paths, season, week)
    per = {w: week_measures(paths, season, w) or {} for w in weeks}
    prev = [w for w in weeks if w < week]
    last = prev[-1] if prev else None
    out = []
    for name, unit in MEASURES:
        out.append(
            {
                "measure": name,
                "unit": unit,
                "this_week": (per.get(week) or {}).get(name),
                "last_week": (per.get(last) or {}).get(name) if last else None,
                "trend": [{"week": w, "value": per[w].get(name)} for w in weeks],
            }
        )
    return out


# ---- Health -------------------------------------------------------------------------------


def _drift_rows(summary: dict[str, Any] | None, paths: DataPaths) -> list[dict[str, Any]]:
    rows = []
    for d in (summary or {}).get("drift") or []:
        if not isinstance(d, dict):
            continue
        rows.append(
            {
                "name": clean(d.get("name"), None, 60) or "",
                "group": clean(d.get("group"), None, 20),
                "status": clean(d.get("status"), None, 30) or "",
                "value": num(d.get("value")),
                "threshold": num(d.get("threshold")),
                "detail": clean(d.get("detail"), paths, 400) or "",
                "response": clean(d.get("response"), paths, 600) or "",
            }
        )
    return rows


def _freshness(summary: dict[str, Any] | None, paths: DataPaths) -> list[dict[str, Any]]:
    out = []
    for r in (summary or {}).get("freshness") or []:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "source": clean(r.get("source"), None, 40) or "",
                "dataset": clean(r.get("dataset"), None, 60) or "",
                "snapshot_date": clean(r.get("snapshot_date"), None, 20),
                "age_days": num(r.get("age_days")),
                "newest_week": num(r.get("newest_week")),
                "expected_week": num(r.get("expected_week")),
                "status": clean(r.get("status"), None, 30) or "",
                "stale": bool(r.get("stale")),
                "note": clean(r.get("note"), paths, 200),
            }
        )
    return out


def _ingest(paths: DataPaths, steps: dict[str, Any]) -> dict[str, Any]:
    man_path = _manifest_path(paths, steps)
    man = read_json(man_path) if man_path else None
    rows = []
    for r in (man or {}).get("results") or []:
        if not isinstance(r, dict):
            continue
        rows.append(
            {
                "source": clean(r.get("source"), None, 40) or "",
                "dataset": clean(r.get("dataset"), None, 60) or "",
                "rows": num(r.get("rows")),
                "status": clean(r.get("status"), None, 30) or "",
                "detail": clean(r.get("detail"), paths, 200) or None,
            }
        )
    return {
        "manifest": man_path.relative_to(paths.root).as_posix() if man and man_path else None,
        "snapshot_date": clean((man or {}).get("snapshot_date"), None, 20),
        "rows": rows,
    }


def _quality(
    paths: DataPaths, steps: dict[str, Any], summary: dict[str, Any] | None
) -> dict[str, Any]:
    q = read_json(paths.curated / "_quality" / "latest.json")
    sq = (summary or {}).get("quality") or {}
    failed = [clean(x, None, 80) or "" for x in sq.get("failed") or []]
    if q is None:
        return {"run_at": None, "match": "none", "checks": [], "failed": failed}
    run_at = q.get("run_at")
    this_run = (sq.get("run_at") is not None and str(sq.get("run_at")) == str(run_at)) or (
        not sq and _in_window(run_at, steps.get("curate"))
    )
    checks = [
        {
            "name": clean(c.get("name"), None, 80) or "",
            "level": clean(c.get("level"), None, 20) or "",
            "passed": bool(c.get("passed")),
            "detail": clean(c.get("detail"), paths, 200),
        }
        for c in q.get("results") or []
        if isinstance(c, dict)
    ]
    return {
        "run_at": to_local_iso(clean(run_at, None, 40)),  # naive = this machine's time
        "match": "this_run" if this_run else "later",
        "checks": checks if this_run else [],
        "failed": failed,
    }


def _promoted(start: dict[str, Any] | None, rows: dict[str, str], has_teams: bool) -> list[str]:
    """What this run gave `production`: `--promote`, or `--auto` with `ops.promote_auto` (D72),
    for the models whose step finished ok."""
    command = str((start or {}).get("command") or "")
    if not command:
        return []
    promote = "--promote" in command
    if not promote and "--auto" in command:
        from nflengine.settings import get_config

        promote = bool((get_config().ops or {}).get("promote_auto", True))
    if not promote:
        return []
    out = []
    if rows.get("game") == "ok":
        out.append("game-model")
    if rows.get("player") == "ok":
        out.append("player-model")
        if has_teams:
            out.append("team-model")
    return out


def get_health(
    paths: DataPaths, season: int, week: int, plan: Any, lock: dict[str, Any]
) -> dict[str, Any]:
    run_dir = paths.run_dir(season, week)
    steps, summary = _week_state(paths, season, week)
    records = "full" if summary is not None else "partial" if steps else "none"
    pipe = get_pipeline(paths, season, week, plan, lock)
    rows = [r for r in pipe["steps"] if r["step"] in STEP_NAMES]
    status_of = {r["step"]: r["status"] for r in rows}
    step_rows = [
        {"step": r["step"], "status": r["status"], "seconds": r["seconds"]}
        for r in pipe["steps"]
        if r["status"] not in ("none", "pending")
    ]
    timed = [r for r in step_rows if r["step"] in STEP_NAMES and r["seconds"]]
    total = sum(r["seconds"] for r in timed)
    pole = max(timed, key=lambda r: r["seconds"]) if timed else None

    sm = summary or {}
    ingest_sum = sm.get("ingest") or {}
    ingest = _ingest(paths, steps)
    quality = _quality(paths, steps, summary)
    sq = sm.get("quality") or {}
    q_total = num(sq.get("checks"))
    if q_total is None and quality["match"] == "this_run":
        q_total = float(len(quality["checks"]))
    q_failed = (
        len(sq.get("failed") or []) if sq else sum(1 for c in quality["checks"] if not c["passed"])
    )
    drift = _drift_rows(summary, paths)
    models = sm.get("models") or {}
    payload = read_json(run_dir / "payload.json") or {}
    mv = (payload.get("meta") or {}).get("model_versions") or {}
    game_label = (models.get("game") or [None])[0] or mv.get("game model")
    player_label = (models.get("player") or [None])[0] or mv.get("players to watch")
    team_label = (models.get("team") or [None])[0]
    alias = f"{season}-w{week:02d}"
    gmeta = read_json(paths.models / "game-model" / alias / "meta.json") or {}
    preds = read_parquet(run_dir / "predictions_players.parquet", ["target", "group"])
    player_stats = (
        preds.select("target", "group").unique().height
        if preds is not None and {"target", "group"} <= set(preds.columns)
        else None
    )
    llm = _llm(paths, season, week)
    start = run_start(paths, season, week)
    g = graph_totals(paths, season, week)
    m = GRAPH_WRITTEN.search(str((steps.get("player") or {}).get("detail") or ""))
    has_teams = (run_dir / "predictions_teams.parquet").exists()
    man_rows = ingest["rows"]
    datasets = num(ingest_sum.get("datasets"))
    if datasets is None and man_rows:
        datasets = float(len(man_rows))
    failures = (
        float(len(ingest_sum.get("failed") or []))
        if ingest_sum
        else float(sum(1 for r in man_rows if r["status"] != "ok"))
        if man_rows
        else None
    )
    blocking = [c for c in quality["checks"] if c["level"] == "block"]
    return {
        "season": season,
        "week": week,
        "records": records,
        "notice": PARTIAL_NOTICE
        if records == "partial"
        else NO_RUN_NOTICE
        if records == "none"
        else None,
        "tiles": {
            "run": {
                "status": clean(sm.get("status"), None, 30)
                or (
                    "ok"
                    if pipe["state"] == "finished"
                    else pipe["state"]
                    if records != "none"
                    else None
                ),
                "steps_ok": sum(1 for s in status_of.values() if s == "ok"),
                "steps_total": len(STEP_NAMES),
                "degraded": len(sm.get("degraded_steps") or [])
                or sum(1 for s in status_of.values() if s == "degraded"),
                "failed_step": clean(sm.get("failed_step"), None, 30) or pipe.get("failed_step"),
            },
            "data": {
                "datasets": datasets,
                "failures": failures,
                "snapshot_date": clean(ingest_sum.get("snapshot_date"), None, 20)
                or ingest["snapshot_date"],
            },
            "quality": {
                "passed": (q_total - q_failed) if q_total is not None else None,
                "total": q_total,
                "blocking": float(len(blocking)) if blocking else None,
                "blocking_failed": float(len(sq.get("blocking_failed") or []))
                if sq
                else float(sum(1 for c in blocking if not c["passed"]))
                if blocking
                else None,
            },
            "drift": {
                "alerts": float(sum(1 for d in drift if d["status"] == "alert"))
                if summary
                else None,
                "signals": len({d["name"] for d in drift}),
                # signals (not rows: the player signals have a row per group) still waiting
                # for the graded weeks they need
                "insufficient": sum(
                    1
                    for n in {d["name"] for d in drift}
                    if all(d["status"] == "insufficient_data" for d in drift if d["name"] == n)
                ),
            },
            "projections": {
                "players": num(_rows(run_dir / "predictions_players.parquet")),
                "teams": num(_rows(run_dir / "predictions_teams.parquet")),
                "graph_written": num(m.group(1).replace(",", "")) if m else None,
            },
        },
        "steps": step_rows,
        "long_pole": {
            "step": pole["step"],
            "seconds": pole["seconds"],
            "share": round(pole["seconds"] / total, 4),
        }
        if pole and total
        else None,
        "what_ran": {
            "game_model": clean(game_label, None, 80),
            "trained_through": clean(gmeta.get("trained_through"), None, 20),
            "player_model": clean(player_label, None, 80),
            "player_stats": player_stats,
            "team_model": clean(team_label, None, 80),
            "graph": {
                "built_at": g["built_at"],
                "nodes": g["nodes"],
                "relationships": g["relationships"],
                "status": g["status"],
            }
            if g
            else None,
            "writer": {
                "model": llm["model"] or None,
                "route": llm["provider"] or None,
                "providers": sorted({c["provider"] for c in llm["calls"] if c["provider"]}),
                "calls": len(llm["calls"]),
            }
            if llm
            else None,
            "prompt_id": (llm or {}).get("prompt"),
            "promoted": _promoted(start, status_of, has_teams),
            "command": clean((start or {}).get("command"), paths, 200),
            "launched_by": clean(
                (start or {}).get("launched_by") or sm.get("launched_by"), None, 30
            ),
            "via": clean((start or {}).get("via"), None, 30),
        },
        "freshness": _freshness(summary, paths),
        "vs_last": vs_last(paths, season, week) if records != "none" else [],
        "ingest": ingest,
        "quality": quality,
        "drift": drift,
    }


# ---- W&B runs: the chart cards (local data) and the run links -----------------------------

LOCAL_JOBS = ("game", "graph", "player", "digest")


def local_links(paths: DataPaths, season: int, week: int) -> list[dict[str, str]]:
    """The W&B runs the step details name (they work without W&B): the newest URL of each
    step (the player step names the scoreboard run first, then the fit)."""
    steps, _ = _week_state(paths, season, week)
    out = []
    for job in LOCAL_JOBS:
        urls = wandb_urls(str((steps.get(job) or {}).get("detail") or ""))
        if not urls:
            continue
        url = (
            urls[-1]
            if job != "player"
            else urls[min(1, len(urls) - 1)]
            if len(urls) > 1
            else urls[0]
        )
        out.append({"job": job, "run_id": url.rsplit("/", 1)[-1], "url": url})
    return out


def _card(run_name: str, metrics: str, source: str, note: str | None = None) -> dict[str, Any]:
    return {
        "run_name": run_name,
        "run_id": None,
        "url": None,
        "metrics": metrics,
        "source": source,
        "note": note,
    }


def _game_fit(paths: DataPaths, season: int, week: int) -> list[dict[str, Any]]:
    df = read_parquet(
        paths.run_dir(season, week) / "predictions_games.parquet",
        [
            "game_id",
            "home_team",
            "away_team",
            "variant",
            "home_win_prob",
            "market_prob",
            "kickoff_utc",
        ],
    )
    if df is None or not {"game_id", "variant", "home_win_prob"} <= set(df.columns):
        return []
    mo = df.filter(pl.col("variant") == "model_only")
    mk = df.filter(pl.col("variant") == "market")
    market = {r["game_id"]: num(r.get("market_prob")) for r in mk.iter_rows(named=True)}
    order = mo.sort("kickoff_utc", "game_id") if "kickoff_utc" in mo.columns else mo.sort("game_id")
    return [
        {
            "game_id": r["game_id"],
            "away": r.get("away_team") or "",
            "home": r.get("home_team") or "",
            "p_model_only": num(r.get("home_win_prob")),
            "p_market": market.get(r["game_id"])
            if r["game_id"] in market
            else num(r.get("market_prob")),
        }
        for r in order.iter_rows(named=True)
    ]


def _player_scoreboard(paths: DataPaths, season: int, week: int) -> dict[str, Any]:
    """Week N's run grades week N−1: the scoreboard run's `scoreboard/improvement_*` for that
    week, pooled per group the dashboard's way (`drift.improvement_pct`)."""
    from nflengine.ops import drift

    sb = read_parquet(paths.runs / str(season) / "accuracy_scoreboard.parquet")
    scored = week - 1
    if sb is None or scored < 1 or "week" not in sb.columns:
        return {"scored_week": scored if scored >= 1 else None, "mode": None, "groups": []}
    prepared = drift.prepare_scoreboard(sb, season, week)  # live rows win over walk-forward
    rows = (
        prepared.filter((pl.col("week") == scored) & (pl.col("position_group") != "TEAM"))
        if prepared.height
        else prepared
    )
    if rows.is_empty():
        return {"scored_week": scored, "mode": None, "groups": []}
    # the scoreboard run logs the week's live rows once there are any (P08's new targets
    # have walk-forward rows only until their first live week): the same rows here
    mode = "live" if (rows["mode"] == "live").any() else "backtest"
    rows = rows.filter(pl.col("mode") == mode)
    groups = []
    for g in rows["position_group"].unique().to_list():
        part = rows.filter(pl.col("position_group") == g)
        groups.append(
            {
                "group": g,
                "improvement": num(drift.improvement_pct(part), 3),
                "scored": int(part["n_scored"].sum()),
            }
        )
    groups.sort(key=lambda x: -(x["improvement"] if x["improvement"] is not None else -1e9))
    return {"scored_week": scored, "mode": mode, "groups": groups}


def _player_fit(paths: DataPaths, season: int, week: int) -> list[dict[str, Any]]:
    df = read_parquet(
        paths.run_dir(season, week) / "predictions_players.parquet",
        ["target", "group", "target_label"],
    )
    if df is None or not {"target", "group"} <= set(df.columns):
        return []
    keys = ["target", "group"] + (["target_label"] if "target_label" in df.columns else [])
    counts = df.group_by(keys).len().sort("len", "target", descending=[True, False])
    return [
        {
            "target": r["target"],
            "group": r["group"],
            "label": r.get("target_label") or r["target"],
            "count": int(r["len"]),
        }
        for r in counts.iter_rows(named=True)
    ]


def _digest_card(paths: DataPaths, season: int, week: int) -> dict[str, Any]:
    from nflengine.settings import get_config

    checks = read_json(paths.run_dir(season, week) / "checks.json") or {}
    final = checks.get("final") or {}
    budgets = (get_config().digest or {}).get("word_budgets") or {}
    words = [
        {"section": clean(k, None, 60) or "", "words": int(v), "budget": num(budgets.get(k))}
        for k, v in (final.get("word_counts") or {}).items()
        if _isnum(v)
    ]
    out = []
    for c in final.get("checks") or []:
        if isinstance(c, dict):
            out.append(
                {
                    "name": clean(c.get("name"), None, 60) or "",
                    "issues": len(c.get("issues") or []),
                    "level": "warn" if c.get("level") == "warn" else "fail",
                }
            )
    return {"words": words, "checks": out}


def get_wandb(
    paths: DataPaths,
    season: int,
    week: int,
    runs: list[dict[str, Any]] | None,
    state: dict[str, Any],
) -> dict[str, Any]:
    tag = f"{season}-w{week:02d}"
    steps, summary = _week_state(paths, season, week)
    links = local_links(paths, season, week)
    by_job: dict[str, dict[str, Any]] = {}
    for r in runs or []:
        if r.get("current") and r["job"] not in by_job:
            by_job[r["job"]] = r
    local_by_job = {x["job"]: x for x in links}

    def link(card: dict[str, Any], job: str) -> dict[str, Any]:
        r = by_job.get(job)
        if r is not None:
            card.update(run_name=r["name"], run_id=r["id"], url=r["url"])
        elif job in local_by_job:
            card.update(run_id=local_by_job[job]["run_id"], url=local_by_job[job]["url"])
        return card

    g = graph_totals(paths, season, week)
    sb = _player_scoreboard(paths, season, week)
    sb_note = None
    if sb["mode"] == "backtest":
        sb_note = (
            f"Week {sb['scored_week']} has walk-forward rows only (before the live player model)."
        )
    elif sb["scored_week"] and not sb["groups"]:
        sb_note = f"No scored rows for week {sb['scored_week']}."
    pipeline_note = None
    if summary is None:
        pipeline_note = (
            "This week ran before the pipeline run existed (P07)."
            if steps
            else "No run for this week yet."
        )
    sm = summary or {}
    cards = {
        "game_fit": link(
            {
                **_card(f"train-{tag}", "slate/*", "predictions_games.parquet"),
                "games": _game_fit(paths, season, week),
            },
            "game",
        ),
        "player_scoreboard": link(
            {
                **_card(
                    f"scoreboard-{season}-w{max(week - 1, 0):02d}",
                    "scoreboard/improvement_*",
                    "accuracy_scoreboard.parquet",
                    sb_note,
                ),
                **sb,
            },
            "scoreboard",
        ),
        "player_fit": link(
            {
                **_card(f"train-{tag}", "projections_per_target", "predictions_players.parquet"),
                "stats": _player_fit(paths, season, week),
            },
            "player",
        ),
        "graph_build": link(
            {
                **_card(f"graph-{tag}", "time/* · load/* · count/*", "graph_results.json"),
                "stages": (g or {}).get("stages") or [],
                "nodes": (g or {}).get("nodes"),
                "relationships": (g or {}).get("relationships"),
                "mismatches": (g or {}).get("mismatches"),
                "total_seconds": (g or {}).get("total_seconds"),
            },
            "graph",
        ),
        "digest": link(
            {
                **_card(
                    f"digest-{tag}",
                    "words_per_section · check_issue_counts",
                    "checks.json",
                ),
                **_digest_card(paths, season, week),
            },
            "digest",
        ),
        "pipeline": link(
            {
                **_card(
                    f"pipeline-{tag}",
                    "step_seconds · freshness · drift",
                    "run_summary.json",
                    pipeline_note,
                ),
                "steps": [
                    {
                        "step": name,
                        "seconds": num(((sm.get("steps") or {}).get(name) or {}).get("seconds")),
                        "status": clean(
                            ((sm.get("steps") or {}).get(name) or {}).get("status"), None, 20
                        )
                        or "",
                    }
                    for name in STEP_NAMES
                    if name in (sm.get("steps") or {})
                ],
                "stale_sources": float(len(sm.get("stale_sources") or [])) if summary else None,
                "drift": [
                    {"name": d["name"], "group": d["group"], "status": d["status"]}
                    for d in _drift_rows(summary, paths)
                ],
            },
            "pipeline",
        ),
    }
    dash = read_json(paths.runs / str(season) / "dashboard.json") or {}
    week_dash = by_job.get("dashboard")
    return {
        "season": season,
        "week": week,
        "wandb": state,
        "runs": runs or [],
        "local_links": links,
        "dashboard": {
            "title": clean(dash.get("title"), None, 80),
            "url": clean(dash.get("url"), None, 300),
            "current_run_id": clean(dash.get("last_run_id"), None, 20),
            "current_run_week": num(dash.get("last_run_week")),
            "week_run_id": week_dash["id"] if week_dash else None,
        },
        "cards": cards,
    }


# ---- Artifacts --------------------------------------------------------------------------

LINEAGE_ORDER = (
    ("game-model", "model"),
    ("graph-results", "graph"),
    ("player-model", "model"),
    ("team-model", "model"),
    ("digest", "published"),
)
PRODUCTION = ("game-model", "player-model", "team-model")
WEEK_ALIAS = re.compile(r"^\d{4}-w\d{2}$")


def _week_alias(aliases: list[str]) -> str | None:
    return next((a for a in aliases if WEEK_ALIAS.match(a)), None)


def get_artifacts(
    paths: DataPaths,
    season: int,
    week: int,
    data: dict[str, Any] | None,
    used: list[dict[str, Any]] | None,
    pipeline_run: str | None,
    state: dict[str, Any],
) -> dict[str, Any]:
    _, summary = _week_state(paths, season, week)
    local_models = None
    if summary and isinstance(summary.get("models"), dict):
        local_models = {
            clean(k, None, 30) or "": [clean(x, None, 80) or "" for x in (v or [])]
            for k, v in summary["models"].items()
        }
    collections = (data or {}).get("collections") or []
    by_name = {c["name"]: c for c in collections}
    production = []
    for name in PRODUCTION:
        v = next(
            (
                x
                for x in (by_name.get(name) or {}).get("versions") or []
                if "production" in x["aliases"]
            ),
            None,
        )
        production.append(
            {
                "name": name,
                "version": v["version"] if v else None,
                "week_alias": _week_alias(v["aliases"]) if v else None,
                "run_id": v["logged_by"] if v else None,
            }
        )
    alias = f"{season}-w{week:02d}"
    items: list[dict[str, Any]] = []
    source, note = "none", None
    if used:
        source = "pipeline_run"
        got = {u["name"]: u for u in used}
        for name, role in LINEAGE_ORDER:
            if name in got:
                items.append({"role": role, "name": name, "version": got[name]["version"]})
        note = f"From the week's pipeline run ({pipeline_run}): the artifacts it used in W&B."
    elif collections:
        for name, role in LINEAGE_ORDER:
            v = next(
                (
                    x
                    for x in (by_name.get(name) or {}).get("versions") or []
                    if alias in x["aliases"]
                ),
                None,
            )
            if v is not None:
                items.append({"role": role, "name": name, "version": v["version"]})
        if items:
            source = "aliases"
            note = (
                f"From the artifacts carrying the alias {alias}: no pipeline run recorded this "
                "week's lineage in W&B (it does from P07's run records on)."
            )
    return {
        "season": season,
        "week": week,
        "wandb": state,
        "production": production,
        "total_versions": sum(c["total"] for c in collections) if data else None,
        "collections_count": sum(1 for c in collections if c["total"]) if data else None,
        "lineage": {
            "source": source,
            "pipeline_run": pipeline_run if used else None,
            "items": items,
            "note": note,
        },
        "collections": collections,
        "local_models": local_models,
    }


def week_published(paths: DataPaths, season: int, week: int) -> bool:
    run_dir = paths.run_dir(season, week)
    return already_published(run_dir / "weekly_run.json", paths.report_path(season, week))
