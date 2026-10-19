"""`nfl history-graph status` (AE02): the container, Bolt, the last build and append, the
counts in the graph, the store's size. Read-only (the counts come from Neo4j's count store)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nflengine.history_graph import docker as D
from nflengine.history_graph import state
from nflengine.history_graph.config import HistoryConfig, history_config
from nflengine.paths import DataPaths, ensure_data_root


def _running(paths: DataPaths) -> dict[str, Any] | None:
    """The lock holder's note while a build or an append runs, else None."""
    from nflengine.ops.lock import is_locked, read_holder

    path = state.lock_path(paths)
    try:
        note = read_holder(path)
        return note if note and is_locked(path) else None
    except OSError:
        return None


def history_status(
    paths: DataPaths | None = None,
    cfg: HistoryConfig | None = None,
    *,
    runner: D.Runner = D.run,
    up: Callable[[], bool] | None = None,
    driver_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    from nflengine.history_graph.build import graph_counts, store_mb
    from nflengine.history_graph.client import get_history_driver, history_up

    paths = paths or ensure_data_root(create_dirs=False)
    cfg = cfg or history_config()
    container = D.container_state(cfg, runner)
    is_up = (up or (lambda: history_up(cfg)))() if container in ("running", "unknown") else False
    counts = None
    if is_up:
        try:
            driver = (driver_factory or (lambda: get_history_driver(cfg=cfg)))()
            try:
                counts = graph_counts(driver)
            finally:
                driver.close()
        except Exception:
            counts = None
    build = state.read_build(paths)
    failed = state._read(paths.neo4j_history / "build_failed.json")
    return {
        "container": container,
        "up": is_up,
        "bolt_uri": cfg.bolt_uri,
        "browser_url": cfg.browser_url,
        "asof": state.history_asof(paths),
        "build": build,
        "last_failed_build": failed
        if failed and (not build or failed.get("build_id", "") > build.get("build_id", ""))
        else None,  # fmt: skip
        "append": state.read_append(paths, build),
        "counts": counts,
        "store_mb": store_mb(paths),
        "busy": state.history_busy(paths),  # running or unfinished: Ask refuses either way
        "running": _running(paths),
        "unfinished": state.read_unfinished(paths),
    }
