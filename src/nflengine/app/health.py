"""System → Health (CR03): services, variables by name, recent runs.

The slow checks are the doctor's own functions (`nfl doctor`): Neo4j with its plugin versions,
the Docker engine, W&B and OpenRouter. They run on a background thread (a W&B login alone takes
about 3 s), every 5 minutes at most or on "Check again", so the page answers at once with the
last results and `checking: true` while new ones come. Their details report set / not set and
connection states only, never a value (CLAUDE.md Security), and pass through `scrub()` anyway.
The data drive shows its free space, never its path (NFL_DATA_ROOT's value).
"""

from __future__ import annotations

import datetime as dt
import logging
import shutil
import threading
import time
from collections.abc import Callable
from typing import Any

from nflengine.app.readers.common import clean, num, parse_time, read_parquet
from nflengine.app.readers.meta import lock_dict
from nflengine.ops.summary import scrub
from nflengine.paths import DataPaths

log = logging.getLogger("nflengine.app")
TTL_S = 300.0
RECENT = 12
WEEKLY_KINDS = ("weekly", "resume")  # pipeline_history kind "main"
INJURY_KINDS = ("injury_update",)  # pipeline_history kind "injury-update"

Check = Callable[[], tuple[str, str]]  # -> (ok | warn | fail, detail)


def _doctor(fn_name: str) -> Check:
    def run() -> tuple[str, str]:
        from nflengine import doctor

        c = getattr(doctor, fn_name)()
        return str(c.status), str(c.detail)

    return run


def _docker() -> tuple[str, str]:
    from nflengine.ops.services import docker_engine_up

    if docker_engine_up():
        return "ok", "engine running (container nfl-neo4j)"
    return "warn", "not running: the weekly run's graph step starts it"


DEFAULT_CHECKS: dict[str, Check] = {
    "Neo4j": _doctor("check_neo4j"),
    "Docker": _docker,
    "Weights & Biases": _doctor("check_wandb"),
    "OpenRouter": _doctor("check_llm"),
}


class SystemChecks:
    """The slow checks, cached; refreshed on a daemon thread."""

    def __init__(
        self,
        checks: dict[str, Check] | None = None,
        ttl_s: float = TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._checks = checks if checks is not None else DEFAULT_CHECKS
        self._ttl = ttl_s
        self._clock = clock
        self._results: dict[str, dict[str, str]] = {}
        self._at: float | None = None
        self._checked_at: str | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _run(self) -> None:
        results: dict[str, dict[str, str]] = {}
        for name, check in self._checks.items():
            try:
                status, detail = check()
            except Exception as e:  # noqa: BLE001 - a check that breaks is a failed check
                status, detail = "fail", f"check failed ({type(e).__name__})"
            status = status if status in ("ok", "warn", "fail") else "warn"
            results[name] = {"status": status, "detail": scrub(detail, 200)}
        with self._lock:
            self._results = results
            self._at = self._clock()
            self._checked_at = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
            self._thread = None

    def snapshot(self, refresh: bool = False, wait: float = 0.0) -> dict[str, Any]:
        with self._lock:
            stale = self._at is None or self._clock() - self._at > self._ttl
            if (stale or refresh) and self._thread is None:
                self._thread = threading.Thread(target=self._run, name="cr-health", daemon=True)
                self._thread.start()
            thread = self._thread
        if thread is not None and wait > 0:
            thread.join(wait)
        with self._lock:
            return {
                "checking": self._thread is not None,
                "checked_at": self._checked_at,
                "results": dict(self._results),
            }


def _recent_runs(paths: DataPaths, season: int) -> list[dict[str, Any]]:
    from nflengine.app.runner import read_events

    hist = read_parquet(paths.runs / str(season) / "pipeline_history.parquet")
    if hist is None or hist.is_empty():
        return []
    rows = hist.sort("started", descending=True).head(RECENT)
    starts: dict[int, list[dict[str, Any]]] = {}
    out = []
    for r in rows.iter_rows(named=True):
        week = int(r["week"])
        if week not in starts:
            folder = paths.run_dir(season, week) / "events"
            found = []
            try:
                files = sorted(folder.glob("*.jsonl")) if folder.exists() else []
            except OSError:
                files = []
            for f in files:
                found += [e for e in read_events(f)[:3] if e.get("type") == "run_start"]
            starts[week] = found
        t0 = parse_time(r.get("started"))
        kinds = INJURY_KINDS if r.get("kind") == "injury-update" else WEEKLY_KINDS
        # the closest run_start of the same kind within two minutes: a failed run and the
        # resume launched right after it both start near the failed run's row (Sol review)
        match, best = None, None
        for e in starts[week]:
            t = parse_time(e.get("t"))
            if not (t0 and t) or e.get("kind") not in kinds:
                continue
            gap = abs((t - t0).total_seconds())
            if gap <= 120 and (best is None or gap < best):
                match, best = e, gap
        out.append(
            {
                "run_id": clean(r.get("run_id"), None, 40),
                "season": int(r["season"]),
                "week": week,
                "kind": clean(r.get("kind"), None, 30) or "",
                "started": clean(r.get("started"), None, 40),
                "finished": clean(r.get("finished"), None, 40),
                "seconds": num(r.get("total_seconds"), 1),
                "command": clean((match or {}).get("command"), paths, 200),
                "status": clean(r.get("status"), None, 30) or "",
                "launched_by": clean(r.get("launched_by"), None, 30),
                "via": clean((match or {}).get("via"), None, 30),
            }
        )
    return out


def get_health(
    paths: DataPaths,
    season: int,
    checks: SystemChecks,
    neo4j: dict[str, Any],
    keys_set: Callable[[tuple[str, ...]], dict[str, bool]],
    refresh: bool = False,
) -> dict[str, Any]:
    from nflengine.settings import OPTIONAL_ENV_VARS, REQUIRED_ENV_VARS

    snap = checks.snapshot(refresh)
    res = snap["results"]
    services = []
    for name in ("Neo4j", "Docker", "Weights & Biases", "OpenRouter"):
        r = res.get(name)
        if r is None:  # not checked yet: Neo4j has the sidebar's quick ping meanwhile
            if name == "Neo4j" and neo4j.get("status") in ("up", "down"):
                up = neo4j["status"] == "up"
                r = {
                    "status": "ok" if up else "warn",
                    "detail": "answering (versions come with the full check)"
                    if up
                    else "not reachable: the weekly run starts it",
                }
            else:
                r = {"status": "checking", "detail": "checking…"}
        services.append({"name": name, **r})
    try:
        free_gb = round(shutil.disk_usage(paths.root).free / 1e9)
        services.append({"name": "Data drive", "status": "ok", "detail": f"{free_gb:,} GB free"})
    except OSError:
        services.append({"name": "Data drive", "status": "fail", "detail": "not reachable"})
    lock = lock_dict(paths)
    services.append(
        {
            "name": "Run lock",
            "status": "busy" if lock["held"] else "ok",
            "detail": f"held: {clean(lock['command'], paths, 120) or 'a run'}"
            if lock["held"]
            else "free",
        }
    )
    names = (*REQUIRED_ENV_VARS, *OPTIONAL_ENV_VARS)
    is_set = keys_set(tuple(names))
    variables = [
        {"name": n, "required": n in REQUIRED_ENV_VARS, "set": bool(is_set.get(n))} for n in names
    ]
    return {
        "checked_at": snap["checked_at"],
        "checking": snap["checking"],
        "services": services,
        "variables": variables,
        "recent_runs": _recent_runs(paths, season),
    }
