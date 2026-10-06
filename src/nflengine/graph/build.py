"""`nfl graph build`: the weekly knowledge-graph rebuild (documentation/05; plan P05).

wipe -> schema -> load (nodes, relationships, model outputs, the published-insight log)
-> count check -> GDS jobs (P08, `graph/gds.py`: PageRank / degree on each team's passing
network, KNN player similarity; fail-soft) -> query library -> candidates + selection ->
`graph_results.json` in the run folder. Counts and timings go to W&B (group
`track1-graph`, job `build`).

The build is **as of** the run: a live run sees what exists now; a backtest week sees what
its Tuesday saw (`graph/tables.py` has the visibility rules), so a backtest digest's graph
sections are leakage-free. `fail_soft=True` (the weekly step and the digest) turns any
failure, e.g. Neo4j down, into `status: "unavailable"` in `graph_results.json` instead of an
exception; the digest then publishes without the graph sections and with a banner.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from nflengine.graph import insights as I
from nflengine.graph import load as L
from nflengine.graph.client import GraphCountMismatch, safe_error
from nflengine.graph.gds import GdsResult, run_gds_jobs
from nflengine.graph.published import log_path, recent_from_graph
from nflengine.graph.queries import LIBRARY, QueryResult, run_library
from nflengine.graph.tables import GraphKey, build_tables, load_inputs
from nflengine.ops import events
from nflengine.paths import DataPaths, ensure_data_root

if TYPE_CHECKING:
    from neo4j import Driver

RESULTS_FILE = "graph_results.json"
SLOW_QUERY_S = 2.0  # documentation/05 -> Testing: every library query under 2 s


@dataclass
class GraphBuildResult:
    key: GraphKey
    status: str  # ok | unavailable
    results_path: Path
    counts: dict[str, int] = field(default_factory=dict)
    mismatches: dict[str, tuple[int, int]] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    queries: dict[str, QueryResult] = field(default_factory=dict)
    selection: I.Selection | None = None
    error: str | None = None
    url: str | None = None
    gds: GdsResult | None = None

    @property
    def summary(self) -> str:
        if self.status != "ok":
            return f"graph unavailable ({self.error})"
        nodes = sum(v for k, v in self.counts.items() if k.startswith("node:"))
        rels = sum(v for k, v in self.counts.items() if k.startswith("rel:"))
        picked = len(self.selection.picked) if self.selection else 0
        return (
            f"{nodes:,} nodes, {rels:,} relationships in "
            f"{self.timings.get('total_s', 0):.0f} s; "
            f"{sum(len(q.rows) for q in self.queries.values())} query rows, "
            f"{picked} insights picked"
        )


def run_roots(paths: DataPaths, mode: str) -> Path:
    return paths.runs if mode == "live" else paths.runs / "digest-backtests"


def results_path(paths: DataPaths, season: int, week: int, mode: str) -> Path:
    from nflengine.digest.report_card import week_dir

    return week_dir(run_roots(paths, mode), season, week) / RESULTS_FILE


def game_infos(games: pl.DataFrame, season: int, week: int) -> dict[str, I.GameInfo]:
    wk = games.filter((pl.col("season") == season) & (pl.col("week") == week))
    return {
        r["game_id"]: I.GameInfo(
            r["game_id"],
            r["home_team"],
            r["away_team"],
            r["kickoff_utc"],
            bool(r.get("neutral_site") or False),
        )
        for r in wk.iter_rows(named=True)
    }


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def load_results(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_graph(
    driver: Driver,
    key: GraphKey,
    paths: DataPaths,
    *,
    log: Callable[[str], None] = print,
    on_step: Callable[[str, float, int], None] | None = None,
) -> tuple[dict[str, Any], GraphBuildResult]:
    """The rebuild itself (raises on failure). Returns the `graph_results.json` content."""
    from nflengine.digest.graph_sections import LIVE_KICKOFF_MARGIN
    from nflengine.digest.report_card import PRED_FILE, week_dir
    from nflengine.settings import load_followed_teams

    t_all = time.perf_counter()
    timings: dict[str, float] = {}
    root = run_roots(paths, key.mode)
    run_dir = week_dir(root, key.season, key.week)

    events.progress(None, 0, 100, "reading the week's tables")  # CR02 (no-op without events)
    t0 = time.perf_counter()
    inp = load_inputs(
        paths,
        key,
        predictions_path=run_dir / PRED_FILE,
        published_path=log_path(root),
        log=log,
    )
    timings["inputs_s"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    tables = build_tables(inp, key)
    timings["tables_s"] = time.perf_counter() - t0
    log(
        f"graph tables as of {key.tag} ({key.mode}): "
        f"{sum(df.height for df in tables.nodes.values()):,} nodes, "
        f"{sum(df.height for df in tables.rels.values()):,} relationships"
    )

    events.progress(None, 2, 100, "wiping last week's graph")
    t0 = time.perf_counter()
    driver.verify_connectivity()
    L.wipe(driver)
    timings["wipe_s"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    L.apply_schema(driver)
    timings["schema_s"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    n_tables = sum(1 for df in [*tables.nodes.values(), *tables.rels.values()] if df.height)
    loaded = 0

    def on_table(name: str, seconds: float, rows: int) -> None:
        nonlocal loaded
        loaded += 1
        with events.portion(0.34, 0.92):
            events.progress(None, loaded, n_tables, f"loading {loaded}/{n_tables} · {name}")
        if on_step is not None:
            on_step(name, seconds, rows)

    events.progress(None, 34, 100, f"loading nodes and relationships (0/{n_tables})")
    per_table = L.load_tables(driver, tables, on_step=on_table)
    timings["load_s"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    counts = L.graph_counts(driver)
    expected = tables.expected_counts()
    mismatches = L.count_mismatches(expected, counts)
    timings["counts_s"] = time.perf_counter() - t0
    if mismatches:
        # an incomplete graph must not look healthy: fail (soft, in the weekly step) before
        # the queries run (Sol review)
        log(f"[red]graph count mismatches: {mismatches}[/]")
        raise GraphCountMismatch(sorted(mismatches))

    # GDS jobs (doc 05's build order: after the load, before the queries); fail-soft
    events.progress(None, 93, 100, "GDS: PageRank and KNN")
    t0 = time.perf_counter()
    gds = run_gds_jobs(driver, key, log=log)
    timings["gds_s"] = time.perf_counter() - t0
    log(f"graph: {gds.summary}")

    events.progress(None, 96, 100, "running the query library")
    t0 = time.perf_counter()
    queries = run_library(driver, key.season, key.week)
    timings["queries_s"] = time.perf_counter() - t0
    for name, q in queries.items():
        flag = " [red](slow)[/]" if q.seconds > SLOW_QUERY_S else ""
        err = f" [red]{q.error}[/]" if q.error else ""
        log(f"  {name}: {len(q.rows)} rows in {q.seconds:.2f} s{flag}{err}")

    games = game_infos(inp.games, key.season, key.week)
    conv_errors: dict[str, str] = {}
    cands = I.candidates(
        {n: q.rows for n, q in queries.items()}, key.season, games, errors=conv_errors
    )
    for name, err in conv_errors.items():
        log(f"  [yellow]{name}: rows skipped by the converter ({err})[/]")
    recent = recent_from_graph(driver, key.season, key.week)
    sel = I.select(
        cands,
        games,
        key.run_time,
        followed=load_followed_teams(),
        recent_ids=recent,
        kickoff_margin=LIVE_KICKOFF_MARGIN if key.mode == "live" else dt.timedelta(0),
    )
    timings["total_s"] = time.perf_counter() - t_all
    data = {
        "status": "ok",
        "season": key.season,
        "week": key.week,
        "mode": key.mode,
        "run_time": key.run_time.isoformat(),
        "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "counts": counts,
        "expected_counts": expected,
        "count_mismatches": {k: list(v) for k, v in mismatches.items()},
        "dropped_rels": tables.dropped,
        "gds": gds.to_dict(),
        "converter_errors": conv_errors,
        "timings": {
            **{k: round(v, 2) for k, v in timings.items()},
            **{f"{k}_s": round(v, 2) for k, v in per_table.items()},
        },
        "queries": {n: q.to_dict() for n, q in queries.items()},
        "recent_ids": recent,
        "candidates": [c.model_dump(mode="json") for c in cands],
        "selected": {
            "matchup_risk": [c.model_dump(mode="json") for c in sel.matchup_risk],
            "non_obvious": [c.model_dump(mode="json") for c in sel.non_obvious],
        },
        "skipped": sel.skipped,
    }
    res = GraphBuildResult(
        key,
        "ok",
        results_path(paths, key.season, key.week, key.mode),
        counts=counts,
        mismatches=mismatches,
        timings=timings,
        queries=queries,
        selection=sel,
        gds=gds,
    )
    return data, res


def default_run_time(paths: DataPaths, season: int, week: int, mode: str) -> dt.datetime:
    if mode == "live":
        return dt.datetime.now(dt.UTC).replace(microsecond=0)
    from nflengine.digest.run import read_games, tuesday_before

    return tuesday_before(read_games(paths), season, week)


def run_graph_build(
    season: int,
    week: int,
    *,
    mode: str = "live",
    run_time: dt.datetime | None = None,
    use_wandb: bool = True,
    launched_by: str | None = None,
    fail_soft: bool = False,
    log: Callable[[str], None] = print,
    driver: Driver | None = None,
) -> GraphBuildResult:
    """Rebuild the graph as of (season, week) and write `graph_results.json`."""
    from nflengine.settings import get_config

    paths = ensure_data_root()
    run_time = run_time or default_run_time(paths, season, week, mode)
    key = GraphKey(season, week, run_time, mode, int(get_config().seasons.graph_start))
    out_path = results_path(paths, season, week, mode)
    run = None
    own_driver = driver is None
    failed = False
    try:
        if use_wandb:
            run = _init_wandb(key, launched_by)
        on_step = _step_logger(run) if run is not None else None
        if driver is None:
            from nflengine.graph.client import get_driver

            driver = get_driver()
        data, res = build_graph(driver, key, paths, log=log, on_step=on_step)
        _write(out_path, data)
        if run is not None:
            res.url = _log_wandb(run, data, res)
        log(f"graph: {res.summary} -> {out_path}")
        return res
    except Exception as e:
        failed = True
        if not fail_soft:
            raise
        err = safe_error(e)
        log(f"[yellow]graph build failed ({err}); the digest goes out without graph sections[/]")
        _write(
            out_path,
            {
                "status": "unavailable",
                "season": season,
                "week": week,
                "mode": mode,
                "run_time": key.run_time.isoformat(),
                "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "error": err,
            },
        )
        if run is not None:
            run.summary.update({"status": "unavailable", "error": err})
        return GraphBuildResult(key, "unavailable", out_path, error=err)
    finally:
        if run is not None:
            # a failed build shows as a failed run in W&B, not only as `status` in its summary
            run.finish(exit_code=1 if failed else 0)
        if own_driver and driver is not None:
            driver.close()


# ---- W&B ----------------------------------------------------------------------------------------


def _init_wandb(key: GraphKey, launched_by: str | None):
    from nflengine.tracking import dataset_version, git_commit, init_run

    run = init_run(
        "track1-graph",
        "build",
        config={
            "season": key.season,
            "week": key.week,
            "mode": key.mode,
            "run_time": key.run_time.isoformat(),
            "graph_start": key.start_season,
            "node_batch": L.NODE_BATCH,
            "rel_batch": L.REL_BATCH,
            "queries": list(LIBRARY),
            "git_commit": git_commit(),
            "dataset_version": dataset_version(),
        },
        tags=["p05", "graph", f"season:{key.season}", f"week:{key.week:02d}", key.mode],
        launched_by=launched_by,
        name=f"graph-{key.tag}" + ("" if key.mode == "live" else "-backtest"),
    )
    run.define_metric("load/step")
    run.define_metric("load/*", step_metric="load/step")
    return run


def _step_logger(run) -> Callable[[str, float, int], None]:
    """One point per loaded table, as it happens: seconds, rows and rows per second."""
    state = {"step": 0, "elapsed": 0.0}

    def on_step(name: str, seconds: float, rows: int) -> None:
        state["step"] += 1
        state["elapsed"] += seconds
        run.log(
            {
                "load/step": state["step"],
                "load/seconds": seconds,
                "load/rows": rows,
                "load/rows_per_s": rows / seconds if seconds else 0.0,
                "load/elapsed_s": state["elapsed"],
            }
        )

    return on_step


def _gds_tables(res: GraphBuildResult) -> dict[str, Any]:
    """`gds_hubs`: each team's passing-network hub (top receiver by PageRank share)."""
    import wandb

    job = (res.gds.jobs if res.gds else {}).get("pass_network") or {}
    hubs = job.get("hubs") or {}
    if not hubs:
        return {}
    return {
        "gds_hubs": wandb.Table(
            columns=["team", "hub", "pagerank_share"],
            data=[[t, h.get("hub"), h.get("share")] for t, h in sorted(hubs.items())],
        )
    }


def _log_wandb(run, data: dict[str, Any], res: GraphBuildResult) -> str | None:
    import wandb

    summary: dict[str, Any] = {"status": "ok"}
    summary.update({f"count/{k.replace(':', '/')}": v for k, v in res.counts.items()})
    summary.update({f"time/{k}": round(v, 2) for k, v in res.timings.items()})
    summary["count_mismatches"] = len(res.mismatches)
    for name, q in res.queries.items():
        summary[f"query/{name}/rows"] = len(q.rows)
        summary[f"query/{name}/seconds"] = round(q.seconds, 3)
        summary[f"query/{name}/error"] = q.error or ""
    summary["query/max_seconds"] = max((q.seconds for q in res.queries.values()), default=0.0)
    sel = res.selection
    summary["insights/candidates"] = len(data.get("candidates", []))
    summary["insights/picked"] = len(sel.picked) if sel else 0
    summary["insights/skipped_novelty"] = len((sel.skipped if sel else {}).get("novelty", []))
    if res.gds is not None:  # P08: GDS jobs
        summary["gds/status"] = res.gds.status
        summary["gds/seconds"] = round(res.gds.seconds, 2)
        for name, job in res.gds.jobs.items():
            summary[f"gds/{name}/seconds"] = job.get("seconds", 0.0)
            summary[f"gds/{name}/written"] = job.get("written", 0)
    run.summary.update(summary)
    counts = wandb.Table(
        columns=["kind", "name", "count"],
        data=[[k.split(":")[0], k.split(":")[1], v] for k, v in res.counts.items()],
    )
    qt = wandb.Table(
        columns=["query", "rows", "seconds"],
        data=[[n, len(q.rows), round(q.seconds, 3)] for n, q in res.queries.items()],
    )
    picked = wandb.Table(
        columns=["section", "type", "matchup", "strength", "confidence", "headline"],
        data=[
            [c.section, c.insight_type, c.matchup, c.strength, c.confidence, c.headline]
            for c in (sel.picked if sel else [])
        ],
    )
    cands = wandb.Table(
        columns=["type", "matchup", "strength", "confidence", "headline"],
        data=[
            [c["insight_type"], c["matchup"], c["strength"], c["confidence"], c["headline"]]
            for c in data.get("candidates", [])
        ],
    )
    run.log(
        {
            "graph_counts": wandb.plot.bar(
                counts, "name", "count", title="Nodes and relationships"
            ),
            "query_seconds": wandb.plot.bar(qt, "query", "seconds", title="Query time (s)"),
            "insights_picked": picked,
            "insight_candidates": cands,
            **_gds_tables(res),
        }
    )
    # the build's record as an artifact (P06 reads Q2 / Q3 rows from it as features), like the
    # game-model and digest artifacts: `graph-results:<season>-w<NN>` (backtests `-backtest`)
    key = res.key
    art = wandb.Artifact(
        "graph-results",
        type="graph",
        description=f"Knowledge-graph build as of {key.season} week {key.week} ({key.mode})",
        metadata={
            "season": key.season,
            "week": key.week,
            "mode": key.mode,
            "run_time": key.run_time.isoformat(),
            "nodes": sum(v for k, v in res.counts.items() if k.startswith("node:")),
            "relationships": sum(v for k, v in res.counts.items() if k.startswith("rel:")),
            "picked": [c.insight_id for c in sel.picked] if sel else [],
        },
    )
    art.add_file(str(res.results_path), name="graph_results.json")
    run.log_artifact(art, aliases=[key.tag if key.mode == "live" else f"{key.tag}-backtest"])
    return run.url
