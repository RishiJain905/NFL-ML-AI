"""Cached, read-only W&B reads for the control room (CR03; documentation/control-room/README.md
§4-§5).

W&B is the record, the app is a window: nothing here writes to W&B or moves an alias. The app
reads W&B only for what the local files don't hold (run lists and links, artifact versions,
aliases, lineage); every chart number comes from a local file (`readers/mlops.py`).

- **The key** goes from the settings straight into the client (`tracking.read_api`, the doctor's
  way): never into the process environment, a file, a log or a response.
- **Cache:** one JSON file per answer under `{NFL_DATA_ROOT}/cache/control-room/wandb/` (the
  app's own folder), plus memory. About 10 minutes for live data, a day for finished weeks,
  a week for things that never change (a finished run's lineage). `refresh=True` skips it once.
- **Failures:** timeouts on every call; after a failure W&B isn't asked again for a minute.
  The answer is then the last cached one marked `stale` with the reason, or `available: false`
  with the reason. A reason is fixed text plus the exception's type, never its message (W&B
  errors can quote the request).
- Every string W&B returns passes through `scrub()` before it's cached.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("nflengine.app")

TTL_LIVE_S = 600.0  # the current week, aliases, the artifact lists
TTL_FINISHED_S = 86_400.0  # a finished week's run list
TTL_FROZEN_S = 7 * 86_400.0  # a finished run's lineage, a finished backtest's summary
BACKOFF_S = 60.0  # after a failure, W&B isn't asked again for this long
TIMEOUT_S = 10  # per HTTP request

ARTIFACTS = (
    # name, type, note, when the first version comes (if none yet)
    ("game-model", "model", "weekly game fit", "The first one comes from the next weekly run."),
    (
        "player-model",
        "model",
        "all player stats, one artifact",
        "The first one comes from the next weekly run.",
    ),
    (
        "team-model",
        "model",
        "team stat totals (P08)",
        "The first one comes from the next weekly run.",
    ),
    (
        "graph-results",
        "graph",
        "graph_results.json per build",
        "The first one comes from the next graph build.",
    ),
    (
        "digest",
        "digest",
        "payload, checks, raw LLM output, digest.md",
        "The first one comes from the next digest.",
    ),
    (
        "injury-update",
        "digest",
        "Saturday comparison",
        "The first one comes from the first Saturday injury update.",
    ),
)
_SAFE_NAME = re.compile(r"[^a-z0-9_.-]+")

Client = tuple[Any, str, str]  # (api, entity, project)


def _default_client(timeout: int) -> Client | None:
    from nflengine.tracking import read_api

    return read_api(timeout=timeout)


def _iso_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _s(x: Any, limit: int = 200) -> str | None:
    """A W&B string fit for the cache and the browser: the data root's path made relative
    (the root the server registers per request, `readers.common.use_data_root`), then scrubbed
    and capped, before anything is cached (Sol review, CR03)."""
    if x is None:
        return None
    from nflengine.app.readers.common import clean

    return clean(str(x), None, limit)


def reason_for(exc: BaseException) -> str:
    """A fixed sentence per kind of failure; only the exception's type name is added."""
    name = type(exc).__name__
    text = f"{name} {type(exc).__module__}".lower()
    if "timeout" in text:
        return f"W&B didn't answer within {TIMEOUT_S} s ({name})."
    if "connection" in text or "connect" in name.lower():
        return f"W&B couldn't be reached: no network, or W&B is down ({name})."
    if "auth" in text or "permission" in text or "unauthorized" in text:
        return f"W&B refused the credentials; run `uv run nfl doctor` ({name})."
    return f"Reading W&B failed ({name})."


def _valid_entry(entry: Any) -> bool:
    """A cache envelope the reader can trust: `at` a finite number, `fetched_at` text or
    missing, `data` present. Anything else is treated as a miss (Sol review, CR03)."""
    if not isinstance(entry, dict) or "data" not in entry:
        return False
    at = entry.get("at")
    if (
        not isinstance(at, int | float)
        or isinstance(at, bool)
        or at != at
        or at
        in (
            float("inf"),
            float("-inf"),
        )
    ):
        return False
    fetched = entry.get("fetched_at")
    url = entry.get("project_url")
    return (fetched is None or isinstance(fetched, str)) and (url is None or isinstance(url, str))


def merge_states(primary: dict[str, Any], *parts: tuple[str, dict[str, Any] | None]) -> dict:
    """One banner state for an answer built from several W&B reads: a secondary read that
    failed or is stale (the week's run list, the lineage, the ratings evaluation) marks the
    whole answer stale with its reason, so a fallback is never shown as fresh (Sol review)."""
    out = dict(primary)
    for label, st in parts:
        if not st or (st.get("available") and not st.get("stale")):
            continue
        if out.get("available"):
            out["stale"] = True
        if not out.get("reason"):
            out["reason"] = f"{label}: {st.get('reason') or 'not available'}"
    return out


class NotConfigured(Exception):
    """WANDB_API_KEY isn't set."""


class WandbReader:
    """One per app. Thread-safe: W&B calls are serialised (FastAPI runs sync routes on a thread
    pool, and the W&B client isn't documented as thread-safe)."""

    def __init__(
        self,
        cache_dir: Callable[[], Path | None],
        client: Callable[[int], Client | None] = _default_client,
        clock: Callable[[], float] = time.time,
        timeout: int = TIMEOUT_S,
    ) -> None:
        self._cache_dir = cache_dir
        self._client_factory = client
        self._clock = clock
        self._timeout = timeout
        self._client: Client | None = None
        self._memory: dict[str, dict[str, Any]] = {}
        self._failed_at: float | None = None
        self._failed_reason: str | None = None
        # the last failure per answer, kept until that answer is fetched again successfully, so
        # a cache hit after a failed refresh still says it's stale (Sol review, CR03)
        self._failed_names: dict[str, str] = {}
        self._lock = threading.RLock()

    # ---- the client -------------------------------------------------------------------------

    def _get_client(self) -> Client:
        if self._client is None:
            got = self._client_factory(self._timeout)
            if got is None:
                raise NotConfigured
            self._client = got
        return self._client

    def project_url(self) -> str | None:
        c = self._client
        if c is None:
            return None
        return f"https://wandb.ai/{c[1]}/{c[2]}"

    # ---- the cache --------------------------------------------------------------------------

    def _file(self, name: str) -> Path | None:
        folder = self._cache_dir()
        if folder is None:
            return None
        return folder / f"{_SAFE_NAME.sub('-', name.lower())}.json"

    def _read_cached(self, name: str) -> dict[str, Any] | None:
        hit = self._memory.get(name)
        if hit is not None:
            return hit
        path = self._file(name)
        if path is None or not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not _valid_entry(entry):
            return None
        self._memory[name] = entry
        return entry

    def _write_cached(self, name: str, entry: dict[str, Any]) -> None:
        self._memory[name] = entry
        path = self._file(name)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(entry), encoding="utf-8")
            tmp.replace(path)
        except OSError as e:  # the cache is a convenience; memory still has it
            log.warning("W&B cache not written (%s)", type(e).__name__)

    def peek(self, name: str) -> Any:
        """The cached data for `name` (any age), without asking W&B."""
        with self._lock:
            cached = self._read_cached(name)
            return cached["data"] if cached is not None else None

    # ---- the one entry point ----------------------------------------------------------------

    def get(
        self,
        name: str,
        fetch: Callable[[Any, str], Any],
        *,
        ttl: float = TTL_LIVE_S,
        refresh: bool = False,
    ) -> tuple[Any, dict[str, Any]]:
        """`(data, state)`. `fetch(api, "<entity>/<project>")` runs only when the cache is
        missing, older than `ttl` or `refresh` is set, and W&B isn't in its back-off. `data` is
        None only when W&B never answered for `name`. `state` is the API's `WandbState`."""
        with self._lock:
            now = self._clock()
            cached = self._read_cached(name)
            fresh = cached is not None and now - float(cached["at"]) < ttl
            if fresh and not refresh:
                return cached["data"], self._state(cached, self._failed_names.get(name))
            if self._failed_at is not None and now - self._failed_at < BACKOFF_S:
                return self._fallback(cached, self._failed_reason)
            try:
                api, entity, project = self._get_client()
                data = fetch(api, f"{entity}/{project}")
            except NotConfigured:
                self._client = None
                return self._fallback(cached, "WANDB_API_KEY isn't set: run `uv run nfl doctor`.")
            except Exception as e:  # noqa: BLE001 - any W&B failure is "unavailable"
                reason = reason_for(e)
                # the back-off runs from when the failure happened, not from when a long fetch
                # (the artifact list: ~25 calls) started (Sol review, CR03)
                self._failed_at, self._failed_reason = self._clock(), reason
                self._failed_names[name] = reason
                if self._client is not None and "auth" in reason.lower():
                    self._client = None
                log.warning("W&B read %s failed: %s", name, type(e).__name__)
                return self._fallback(cached, reason)
            self._failed_at = self._failed_reason = None
            self._failed_names.pop(name, None)
            entry = {
                "at": now,
                "fetched_at": _iso_now(),
                "project_url": f"https://wandb.ai/{entity}/{project}",
                "data": data,
            }
            self._write_cached(name, entry)
            return data, self._state(entry, None)

    def _fallback(
        self, cached: dict[str, Any] | None, reason: str | None
    ) -> tuple[Any, dict[str, Any]]:
        if cached is not None:
            return cached["data"], self._state(cached, reason)
        return None, {
            "available": False,
            "reason": reason,
            "fetched_at": None,
            "stale": False,
            "project_url": self.project_url(),
        }

    def _state(self, entry: dict[str, Any], reason: str | None) -> dict[str, Any]:
        return {
            "available": True,
            "reason": reason,
            "fetched_at": entry.get("fetched_at"),
            "stale": reason is not None,
            "project_url": self.project_url() or entry.get("project_url"),
        }


# ---- what the app reads (each returns plain JSON, scrubbed) ------------------------------------


def job_of(group: str, job_type: str, tags: list[str]) -> str:
    """The pipeline step (or record) a W&B run belongs to."""
    if group == "track1-game" and job_type == "train":
        return "game"
    if group == "track1-graph":
        return "graph"
    if group == "track1-player" and (job_type == "eval" or "scoreboard" in tags):
        return "scoreboard"
    if group == "track1-player":
        return "player"
    if group == "track1-team":
        return "team"
    if group == "weekly-pipeline":
        return {"main": "digest", "pipeline": "pipeline", "simulation": "pipeline"}.get(
            job_type, "injury_update" if job_type == "injury-update" else "other"
        )
    if group == "season-dashboard":
        return "dashboard"
    return "other"


def _run(r: Any) -> dict[str, Any]:
    tags = [t for t in (_s(t, 60) for t in (r.tags or [])) if t]
    group, job_type = _s(r.group, 60) or "", _s(r.job_type, 60) or ""
    return {
        "id": _s(r.id, 20) or "",
        "name": _s(r.name, 80) or "",
        "group": group,
        "job_type": job_type,
        "job": job_of(group, job_type, tags),
        "state": _s(r.state, 20) or "",
        "created_at": _s(r.created_at, 40),
        "url": _s(r.url, 200) or "",
        "tags": tags,
    }


def fetch_week_runs(season: int, week: int) -> Callable[[Any, str], list[dict[str, Any]]]:
    """The runs a week's pipeline made, newest first. W&B tags them `season:S` + `week:NN`,
    except the player scoreboard, which is tagged with the week it **grades** (N−1): week N's
    list takes `scoreboard-S-w(N−1)` and gives its own `week:NN` scoreboard to week N+1."""

    def tagged(api: Any, project: str, w: int) -> list[dict[str, Any]]:
        filters = {"$and": [{"tags": f"season:{season}"}, {"tags": f"week:{w:02d}"}]}
        return [_run(r) for r in api.runs(project, filters=filters, order="-created_at")]

    def fetch(api: Any, project: str) -> list[dict[str, Any]]:
        runs = [r for r in tagged(api, project, week) if r["job"] != "scoreboard"]
        if week > 1:
            runs += [r for r in tagged(api, project, week - 1) if r["job"] == "scoreboard"]
        runs.sort(key=lambda r: r["created_at"] or "", reverse=True)
        seen: set[str] = set()
        for r in runs:  # the newest run of each job is the one that counts (W&B guide §2)
            r["current"] = r["job"] not in seen
            seen.add(r["job"])
        return runs

    return fetch


def _version(a: Any, logged_by: str | None, base: str) -> dict[str, Any]:
    size = getattr(a, "size", None)
    return {
        "version": _s(a.version, 20) or "",
        "created_at": _s(getattr(a, "created_at", None), 40),
        "aliases": [x for x in (_s(x, 60) for x in (a.aliases or [])) if x],
        "logged_by": logged_by,
        "logged_by_url": f"{base}/runs/{logged_by}" if logged_by else None,
        "size": int(size) if isinstance(size, int | float) else None,
    }


def _not_found(e: Exception) -> bool:
    text = str(e).lower()
    return "can't find" in text or "not found" in text or "does not exist" in text


def fetch_artifacts(previous: dict[str, Any] | None = None) -> Callable[[Any, str], dict[str, Any]]:
    """Every version of the six artifacts the weekly cycle logs, with the run that logged it.
    `logged_by` costs a call per version (~0.2 s); a version's logger never changes, so the
    previous answer's values are reused (`previous`: the last cached data)."""
    known: dict[tuple[str, str], str | None] = {}
    for col in (previous or {}).get("collections") or []:
        for v in col.get("versions") or []:
            known[(col.get("name"), v.get("version"))] = v.get("logged_by")

    def fetch(api: Any, project: str) -> dict[str, Any]:
        base = f"https://wandb.ai/{project}"
        out = []
        for name, typ, note, first in ARTIFACTS:
            try:
                versions = list(api.artifacts(typ, f"{project}/{name}"))
            except Exception as e:  # noqa: BLE001
                if not _not_found(e):
                    raise
                versions = []
            rows = []
            for a in versions:
                v = _s(a.version, 20) or ""
                if (name, v) in known:
                    by = known[(name, v)]
                else:
                    run = a.logged_by()
                    by = _s(getattr(run, "id", None), 20) if run is not None else None
                rows.append(_version(a, by, base))
            rows.sort(key=lambda r: int(r["version"][1:]) if r["version"][1:].isdigit() else -1)
            rows.reverse()
            out.append(
                {
                    "name": name,
                    "type": typ,
                    "note": note,
                    "url": f"{base}/artifacts/{typ}/{name}",
                    "total": len(rows),
                    "versions": rows,
                    "first_note": None if rows else first,
                }
            )
        return {"collections": out, "project_url": base}

    return fetch


def fetch_used(run_id: str) -> Callable[[Any, str], list[dict[str, Any]]]:
    """The artifacts a run used (the pipeline run's lineage)."""

    def fetch(api: Any, project: str) -> list[dict[str, Any]]:
        run = api.run(f"{project}/{run_id}")
        out = []
        for a in run.used_artifacts():
            full = _s(getattr(a, "name", None), 80) or ""  # "game-model:v5"
            name, _, version = full.partition(":")
            out.append(
                {
                    "name": name,
                    "type": _s(getattr(a, "type", None), 40) or "",
                    "version": version or _s(getattr(a, "version", None), 20),
                    "aliases": [x for x in (_s(x, 60) for x in (a.aliases or [])) if x],
                }
            )
        return out

    return fetch


def fetch_run_summary(
    group: str, name: str, prefixes: tuple[str, ...]
) -> Callable[[Any, str], dict[str, Any] | None]:
    """The newest run of `group` called `name`: its id, url and the numeric summary values whose
    names start with one of `prefixes` (the Models page's ratings evaluation)."""

    def fetch(api: Any, project: str) -> dict[str, Any] | None:
        runs = list(
            api.runs(
                project,
                filters={"$and": [{"group": group}, {"display_name": name}]},
                order="-created_at",
                per_page=5,
            )
        )
        if not runs:
            return None
        r = runs[0]
        values = {}
        for k, v in dict(r.summary).items():
            numeric = isinstance(v, int | float) and not isinstance(v, bool)
            if numeric and any(str(k).startswith(p) for p in prefixes):
                values[_s(k, 80)] = float(v)
        return {"id": _s(r.id, 20), "url": _s(r.url, 200), "summary": values}

    return fetch
