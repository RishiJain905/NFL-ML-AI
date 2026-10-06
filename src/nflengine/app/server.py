"""The control room's FastAPI app (documentation/control-room/README.md §4).

`create_app(settings)` wires the safety middleware, the JSON rules, the readers and (when a
build exists) the React app from `web/dist/`. Everything the readers need comes in through
`AppSettings`, so tests run on fixture data with a pinned clock and no real server.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from nflengine.app.jsonsafe import SafeJSONResponse
from nflengine.app.readers.meta import lock_dict, plan_dict
from nflengine.app.readers.weeks import list_weeks
from nflengine.app.security import LocalOnlyMiddleware
from nflengine.app.status import ServiceStatus
from nflengine.ops.summary import scrub
from nflengine.paths import DataPaths, DataRootError, ensure_data_root

log = logging.getLogger("nflengine.app")
REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_DIST = REPO_ROOT / "web" / "dist"
VERSION = "0.1.0"
# Fixed text: the real message names NFL_DATA_ROOT's value, an environment value the browser
# must not see (README §5.4).
NO_DRIVE = (
    "The data drive isn't connected, or NFL_DATA_ROOT points to a folder that doesn't exist. "
    "Run `uv run nfl doctor`."
)


def _load_schedules(paths: DataPaths) -> Any:
    from nflengine.ops.calendar import load_schedules

    return load_schedules(paths)


def _utc_now() -> dt.datetime:
    from nflengine.clock import utc_now

    return utc_now()


def _current_season() -> int:
    from nflengine.settings import get_config

    return get_config().seasons.current


@dataclass
class AppSettings:
    port: int = 8765
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    dev: bool = False
    rehearsal: bool = False
    web_dist: Path | None = None
    data_root: Callable[[], DataPaths] = ensure_data_root
    schedules: Callable[[DataPaths], Any] = _load_schedules
    now: Callable[[], dt.datetime] = _utc_now
    current_season: Callable[[], int] = _current_season
    services: ServiceStatus | None = None
    schedule_ttl_s: float = 60.0


class _Context:
    """Per-app state: the settings and a short cache of the schedule snapshot."""

    def __init__(self, settings: AppSettings) -> None:
        self.s = settings
        self.services = settings.services or ServiceStatus()
        self._sched: tuple[float, Any] | None = None
        self._lock = threading.Lock()

    def paths(self) -> DataPaths:
        return self.s.data_root()

    def schedules(self, paths: DataPaths) -> Any:
        with self._lock:
            if self._sched and time.monotonic() - self._sched[0] < self.s.schedule_ttl_s:
                return self._sched[1]
        sched = self.s.schedules(paths)
        with self._lock:
            self._sched = (time.monotonic(), sched)
        return sched

    def plan(self, paths: DataPaths) -> Any:
        from nflengine.ops.calendar import plan_week

        try:
            return plan_week(self.schedules(paths), self.s.now())
        except FileNotFoundError:  # no schedule snapshot yet
            return None


def _error(status: int, code: str, message: str) -> SafeJSONResponse:
    return SafeJSONResponse({"error": {"code": code, "message": scrub(message, 300)}}, status)


def create_app(settings: AppSettings) -> FastAPI:
    ctx = _Context(settings)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        ctx.services.start()
        try:
            yield
        finally:
            ctx.services.stop()

    app = FastAPI(
        title="NFL Control Room",
        version=VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        default_response_class=SafeJSONResponse,
    )
    app.state.ctx = ctx

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_req: Request, exc: StarletteHTTPException) -> SafeJSONResponse:
        code = "not_found" if exc.status_code == 404 else f"http_{exc.status_code}"
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(DataRootError)
    async def no_drive(_req: Request, _exc: DataRootError) -> SafeJSONResponse:
        return _error(503, "data_root_missing", NO_DRIVE)

    # Anything else is handled by LocalOnlyMiddleware (scrubbed log line, 500 with headers).

    @app.get("/api/session")
    def session() -> SafeJSONResponse:
        """The per-launch token for state-changing requests. Same-origin pages only can read
        it: the Host check blocks rebinding and no CORS headers are ever sent."""
        return SafeJSONResponse({"token": settings.token})

    @app.get("/api/meta")
    def meta() -> SafeJSONResponse:
        now = settings.now()
        try:
            paths = ctx.paths()
        except DataRootError:
            return SafeJSONResponse(
                {
                    "app": "control-room",
                    "version": VERSION,
                    "now": now,
                    "mode": {"dev": settings.dev, "rehearsal": settings.rehearsal},
                    "data_root": {"found": False, "message": NO_DRIVE},
                    "lock": {"held": False, "command": None, "started": None},
                    "neo4j": ctx.services.snapshot(),
                    "calendar": None,
                }
            )
        plan = ctx.plan(paths)
        free_gb = round(shutil.disk_usage(paths.root).free / 1e9)
        return SafeJSONResponse(
            {
                "app": "control-room",
                "version": VERSION,
                "now": now,
                "mode": {"dev": settings.dev, "rehearsal": settings.rehearsal},
                "data_root": {"found": True, "free_gb": free_gb},
                "lock": lock_dict(paths),
                "neo4j": ctx.services.snapshot(),
                "calendar": plan_dict(plan),
                "current_season": settings.current_season(),
            }
        )

    @app.get("/api/weeks")
    def weeks() -> SafeJSONResponse:
        paths = ctx.paths()
        plan = ctx.plan(paths)
        season = plan.season if plan is not None and plan.week is not None else None
        season = season or settings.current_season()
        return SafeJSONResponse(list_weeks(paths, plan, lock_dict(paths), season))

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def api_not_found(rest: str) -> SafeJSONResponse:
        return _error(404, "not_found", f"No endpoint /api/{rest}")

    if settings.web_dist is not None and (settings.web_dist / "index.html").exists():
        app.mount("/", SPAStatic(directory=settings.web_dist, html=True), name="web")

    # outermost of the user middleware: every route and the static files sit behind it
    app.add_middleware(
        LocalOnlyMiddleware, port=settings.port, token=settings.token, dev=settings.dev
    )
    return app


class SPAStatic(StaticFiles):
    """The built React app; unknown non-file paths get index.html (client-side routes)."""

    async def get_response(self, path: str, scope: dict) -> Any:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404 and "." not in Path(path).name:
                return await super().get_response("index.html", scope)
            raise
