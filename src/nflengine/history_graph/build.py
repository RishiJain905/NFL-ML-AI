"""`nfl history-graph build --through-season 2025` (AE02): the history graph from scratch.

Stages (each timed, in `build.json` and the W&B run):
1. **export**: curated + research data -> frames (`export.build_frames`), read-only;
2. **csv**: the frames -> `{NFL_DATA_ROOT}/neo4j-history/import/*.csv` (bulk-import headers);
3. **import**: `neo4j-admin database import full` into the staging Docker volume;
4. **stop**: the history container (only it);
5. **install**: the staged store replaces `neo4j-history/data/{databases,transactions}/neo4j`;
6. **start**: the history container, then wait for Bolt;
7. **schema**: `schema.cypher` (constraints, indexes; the import makes none), awaited;
8. **check**: Neo4j's node and relationship counts must equal the frames' (a mismatch fails
   the build: `build.json` says so and Ask stops using the graph);
9. **append**: the current season through its newest week (`append.run_append`), so a rebuild
   never leaves the graph without this season's plays.

Takes the history lock (`state.lock_path`) and refuses while the weekly run holds its lock
(Tuesday's run shares the disk and has a deadline). Writes `build.json` (status, seasons,
counts, expected counts, dropped rows, stage times, versions, the W&B run) and logs a W&B
run (`job_type: history-graph-build`). Never touches the weekly graph.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nflengine.history_graph import docker as D
from nflengine.history_graph import schema as S
from nflengine.history_graph import state
from nflengine.history_graph.config import HistoryConfig, history_config
from nflengine.paths import DataPaths, ensure_data_root

SCHEMA_VERSION = 1
NEO4J_STAGES = ("schema", "check", "append")  # the stages that talk to Neo4j over Bolt
SCHEMA_FILE = Path(__file__).resolve().parent / "schema.cypher"


class HistoryBuildError(RuntimeError):
    """The build stopped (fixed, secret-free text; `stage` says where)."""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def schema_statements(text: str | None = None) -> list[str]:
    """`schema.cypher` split into statements, comments dropped."""
    text = text if text is not None else SCHEMA_FILE.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("//")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def apply_schema(driver, timeout_s: float = 1800) -> float:
    """Create the constraints and indexes (idempotent) and wait for them to be online."""
    t0 = time.monotonic()
    with driver.session() as s:
        for stmt in schema_statements():
            s.run(stmt).consume()
        s.run("CALL db.awaitIndexes($t)", t=int(timeout_s)).consume()
    return round(time.monotonic() - t0, 1)


def graph_counts(driver, names: list[str] | None = None) -> dict[str, int]:
    """Node counts per label and relationship counts per type (Neo4j's count store: instant)."""
    names = names or [*S.NODES, *S.RELATIONSHIPS]
    out: dict[str, int] = {}
    with driver.session() as s:
        for n in names:
            if n in S.NODES:
                q = f"MATCH (n:{n}) RETURN count(n) AS c"
            else:
                q = f"MATCH ()-[r:{n}]->() RETURN count(r) AS c"
            out[n] = int(s.run(q).single()["c"])
    return out


def versions(driver) -> dict[str, str]:
    from nflengine.graph.client import ping

    try:
        return ping(driver)
    except Exception:
        return {}


def store_mb(paths: DataPaths) -> float | None:
    root = paths.neo4j_history / "data" / "databases" / D.DATABASE
    if not root.exists():
        return None
    total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    return round(total / 1e6, 1)


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    from nflengine.fsutil import replace_file

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    replace_file(tmp, path)


def weekly_lock_held(paths: DataPaths) -> bool:
    from nflengine.ops.lock import is_locked, lock_path, read_holder

    p = lock_path(paths.runs)
    try:
        return bool(read_holder(p)) and is_locked(p)
    except OSError:
        return False


def run_build(
    through_season: int | None = None,
    *,
    first_season: int | None = None,
    append_current: bool = True,
    use_wandb: bool = True,
    launched_by: str | None = None,
    smoke: bool = False,
    log: Callable[[str], None] = print,
    paths: DataPaths | None = None,
    cfg: HistoryConfig | None = None,
    runner: D.Runner = D.run,
    driver_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Build the history graph (see the module docstring). Returns the `build.json` document;
    raises `HistoryBuildError` (after writing what it can) when a stage fails."""
    from nflengine.ops.lock import LockHeld, run_lock
    from nflengine.settings import get_config

    paths = paths or ensure_data_root(create_dirs=False)
    cfg = cfg or history_config()
    current = get_config().seasons.current
    through = through_season if through_season is not None else current - 1
    first = first_season if first_season is not None else cfg.first_season
    if through >= current:
        raise HistoryBuildError(
            "args", f"--through-season must be a finished season (before {current}): the "
            "current season is appended (`nfl history-graph append`)",
        )  # fmt: skip
    if first > through:
        raise HistoryBuildError("args", f"first season {first} is after {through}")
    if weekly_lock_held(paths):
        raise HistoryBuildError(
            "lock", "the weekly run (or the injury update / Update play calling) holds the "
            "weekly lock: build the history graph after it",
        )  # fmt: skip
    if driver_factory is None:
        from nflengine.history_graph.client import get_history_driver

        def driver_factory():
            return get_history_driver(cfg=cfg)

    try:
        with run_lock(
            state.lock_path(paths), f"nfl history-graph build --through-season {through}"
        ):
            return _build(paths, cfg, first, through, current, append_current, use_wandb,
                          launched_by, smoke, log, runner, driver_factory)  # fmt: skip
    except LockHeld as e:
        raise HistoryBuildError(
            "lock", "another history-graph build or append is running "
            f"(pid {e.holder.get('pid')}, since {e.holder.get('started')})",
        ) from None  # fmt: skip


def _build(paths, cfg, first, through, current, append_current, use_wandb, launched_by, smoke,
           log, runner, driver_factory) -> dict[str, Any]:  # fmt: skip
    from nflengine.history_graph.export import build_frames, load_inputs, write_csvs

    t_all = time.monotonic()
    started = utc_now()
    build_id = started.strftime("%Y%m%dT%H%M%SZ")
    seasons = list(range(first, through + 1))
    times: dict[str, float] = {}
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "build_id": build_id,
        "status": "running",
        "first_season": first,
        "through_season": through,
        "seasons": seasons,
        "started_at": started.isoformat(),
    }
    wb = _wandb_start(doc, use_wandb, launched_by, smoke, log)
    replaced = False
    stage = "export"
    driver = None
    try:
        t = time.monotonic()
        frames = build_frames(load_inputs(paths, seasons, log=log), log=log)
        times["export"] = round(time.monotonic() - t, 1)
        expected = frames.expected_counts()
        doc["expected"] = expected
        doc["dropped"] = frames.dropped
        _wandb_log(wb, {"time/export": times["export"]})

        stage = "csv"
        t = time.monotonic()
        files = write_csvs(frames, D.history_dir(paths, "import"))
        del frames
        times["csv"] = round(time.monotonic() - t, 1)
        csv_mb = sum((D.history_dir(paths, "import") / f["file"]).stat().st_size
                     for f in files.values()) / 1e6  # fmt: skip
        doc["csv_mb"] = round(csv_mb, 1)
        log(f"history graph: CSVs written ({csv_mb:,.0f} MB, {times['csv']:.0f} s)")
        _wandb_log(wb, {"time/csv": times["csv"], "csv_mb": csv_mb})

        stage = "import"
        log("history graph: neo4j-admin import into the staging volume ...")
        imp = D.import_full(paths, cfg, files, runner=runner, stamp=build_id)
        times["import"] = imp["seconds"]
        doc["import_log"] = imp["log"]
        _wandb_log(wb, {"time/import": times["import"]})

        stage = "stop"
        res = D.stop(cfg, runner=runner)
        if not res.ok:
            raise D.HistoryDockerError(res.detail)
        times["stop"] = res.seconds

        stage = "install"
        # from here the old store may be gone: build.json must say so, and a crash or a reboot
        # must too (Sol): the marker stays until the counts are checked
        state.mark_unfinished(paths, "build", build_id)
        replaced = True
        times["install"] = D.install_store(paths, cfg, runner=runner)
        D.drop_staging(cfg, runner=runner)
        log(f"history graph: new store on D: ({times['install']:.0f} s)")
        _wandb_log(wb, {"time/install": times["install"]})

        stage = "start"
        res = D.start(cfg, log, runner=runner)
        if not res.ok:
            raise D.HistoryDockerError(res.detail)
        times["start"] = res.seconds
        _wandb_log(wb, {"time/start": times["start"]})

        stage = "schema"
        driver = driver_factory()
        times["schema"] = apply_schema(driver)
        _wandb_log(wb, {"time/schema": times["schema"]})

        stage = "check"
        t = time.monotonic()
        counts = graph_counts(driver)
        times["check"] = round(time.monotonic() - t, 1)
        doc["counts"] = counts
        bad = {k: (counts.get(k), v) for k, v in expected.items() if counts.get(k) != v}
        if bad:
            doc["mismatch"] = {k: {"neo4j": a, "expected": b} for k, (a, b) in bad.items()}
            raise HistoryBuildError("check", "Neo4j's counts differ from the export for "
                                    + ", ".join(sorted(bad)))  # fmt: skip
        doc["versions"] = {**versions(driver), "image": D.image(cfg)}
        doc["store_mb"] = store_mb(paths)
        doc["status"] = "ok"
        doc["built_at"] = utc_now().isoformat()
        doc["seconds"] = {**times, "total": round(time.monotonic() - t_all, 1)}
        _write_json(state.build_path(paths), doc)
        state.clear_unfinished(paths)  # the new store is checked and described
        log(f"[green]history graph built: {counts.get('Play', 0):,} plays, "
            f"{sum(counts.get(r, 0) for r in S.RELATIONSHIPS):,} relationships[/]")  # fmt: skip

        if append_current and current > through:
            # the import is good and build.json says so: an append that can't run (no plays
            # yet in the offseason) or fails is reported, never turns the build into a failure
            # (test worker, AE02); `nfl history-graph append` can be run again on its own
            from nflengine.history_graph.append import HistoryAppendError, newest_week, run_append

            t = time.monotonic()
            doc["append"] = None
            if newest_week(paths, current):
                try:
                    app = run_append(current, paths=paths, cfg=cfg, driver=driver,
                                     use_wandb=False, log=log, locked=True)  # fmt: skip
                    keep = ("season", "through_week", "plays", "games")
                    doc["append"] = {k: app.get(k) for k in keep}
                except Exception as e:
                    doc["append_error"] = str(e) if isinstance(e, HistoryAppendError) else _safe(e)
                    log(f"[yellow]history graph: the {current} append failed "
                        f"({doc['append_error']}); run `nfl history-graph append`[/]")  # fmt: skip
            times["append"] = round(time.monotonic() - t, 1)
        doc["seconds"] = {**times, "total": round(time.monotonic() - t_all, 1)}
        doc["counts_after_append"] = graph_counts(driver) if "append" in times else None
        doc["wandb_url"] = getattr(wb, "url", None) if wb else None
        _write_json(state.build_path(paths), doc)
        _wandb_finish(wb, doc, log)
        return doc
    except Exception as e:
        if isinstance(e, (HistoryBuildError, D.HistoryDockerError)):
            reason = str(e)
        elif stage in NEO4J_STAGES:
            reason = _safe(e)  # the driver's text never leaves: type + a fixed reason / code
        else:  # export / csv: a file or data problem, never "Neo4j unreachable" (test worker)
            reason = type(e).__name__
        doc["status"] = "failed"
        doc["failed_stage"] = stage
        doc["error"] = reason
        doc["seconds"] = {**times, "total": round(time.monotonic() - t_all, 1)}
        if replaced:  # the old build.json no longer describes the store
            _write_json(state.build_path(paths), doc)
        else:
            _write_json(paths.neo4j_history / "build_failed.json", doc)
        _wandb_finish(wb, doc, log)
        if isinstance(e, HistoryBuildError):
            raise
        raise HistoryBuildError(stage, reason) from None
    finally:
        if driver is not None:
            driver.close()


def _safe(e: BaseException) -> str:
    from nflengine.graph.client import safe_error

    return safe_error(e)


# ---- W&B --------------------------------------------------------------------------------------


def _wandb_start(doc, use_wandb, launched_by, smoke, log):
    if not use_wandb:
        return None
    try:
        from nflengine.tracking import git_commit, init_run

        return init_run(
            group="history-graph-smoke" if smoke else "history-graph",
            job_type="history-graph-build",
            config={
                "first_season": doc["first_season"],
                "through_season": doc["through_season"],
                "build_id": doc["build_id"],
                "schema_version": SCHEMA_VERSION,
                "git_commit": git_commit(),
            },
            tags=["history-graph", *(["smoke"] if smoke else [])],
            launched_by=launched_by,
            name=f"history-graph-build-{doc['first_season']}-{doc['through_season']}",
        )
    except Exception as e:  # W&B never sinks a build
        log(f"[yellow]W&B off for this build ({type(e).__name__})[/]")
        return None


def _wandb_log(wb, data: dict[str, Any]) -> None:
    if wb is None:
        return
    with contextlib.suppress(Exception):
        wb.log(data)


def _wandb_finish(wb, doc: dict[str, Any], log) -> None:
    if wb is None:
        return
    try:
        import wandb

        counts = doc.get("counts") or {}
        expected = doc.get("expected") or {}
        rows = [[k, expected.get(k), counts.get(k), counts.get(k) == expected.get(k)]
                for k in expected]  # fmt: skip
        wb.log({"counts": wandb.Table(columns=["name", "expected", "neo4j", "match"], data=rows)})
        wb.summary.update(
            {
                "status": doc.get("status"),
                "failed_stage": doc.get("failed_stage"),
                "store_mb": doc.get("store_mb"),
                "csv_mb": doc.get("csv_mb"),
                **{f"count/{k}": v for k, v in counts.items()},
                **{f"time/{k}": v for k, v in (doc.get("seconds") or {}).items()},
                **{f"dropped/{k}": v for k, v in (doc.get("dropped") or {}).items()},
            }
        )
        wb.finish(exit_code=0 if doc.get("status") == "ok" else 1)
    except Exception as e:
        log(f"[yellow]W&B finish failed ({type(e).__name__})[/]")
