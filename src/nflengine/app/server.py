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

from fastapi import FastAPI, Query, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import StreamingResponse
from starlette.staticfiles import StaticFiles

from nflengine.app import wandb_api
from nflengine.app.health import SystemChecks, get_health
from nflengine.app.jsonsafe import SafeJSONResponse
from nflengine.app.preflight import get_preflight, rehearsal_target
from nflengine.app.readers import mlops
from nflengine.app.readers.alerts import get_alerts
from nflengine.app.readers.common import strip_root, use_data_root
from nflengine.app.readers.digest import get_digest
from nflengine.app.readers.games import get_games
from nflengine.app.readers.graph import get_graph
from nflengine.app.readers.live import LiveRefused, LiveService
from nflengine.app.readers.meta import lock_dict, plan_dict
from nflengine.app.readers.models import card as model_card
from nflengine.app.readers.models import get_models
from nflengine.app.readers.pipeline import get_pipeline
from nflengine.app.readers.players import get_players
from nflengine.app.readers.results import get_results
from nflengine.app.readers.season import get_scorecard, get_teams
from nflengine.app.readers.weeks import get_week, list_weeks, team_info
from nflengine.app.runner import Runner, RunRefused, public, rehearsal_root
from nflengine.app.security import LocalOnlyMiddleware
from nflengine.app.status import ServiceStatus
from nflengine.app.stream import parse_cursor, run_stream
from nflengine.app.wandb_api import WandbReader
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


def _read_only_root() -> DataPaths:
    """The data root without creating its standard folders: a GET must never write
    (Sol review, CR01). Readers treat a missing folder as "no data"."""
    return ensure_data_root(create_dirs=False)


def _current_season() -> int:
    from nflengine.settings import get_config

    return get_config().seasons.current


def _keys_set(names: tuple[str, ...]) -> dict[str, bool]:
    """Set / not set per variable name (the doctor's check): never a value (README §5.4)."""
    from nflengine.settings import get_env

    env = get_env()
    return {n: env.is_set(n) for n in names}


class RunBody(BaseModel):
    """`POST /api/run`: a kind and the browser's expected week, nothing else."""

    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(weekly|resume|injury_update)$")
    expect_week: int = Field(ge=1, le=22)


@dataclass
class AppSettings:
    port: int = 8765
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    dev: bool = False
    rehearsal: bool = False
    web_dist: Path | None = None
    data_root: Callable[[], DataPaths] = _read_only_root
    schedules: Callable[[DataPaths], Any] = _load_schedules
    now: Callable[[], dt.datetime] = _utc_now
    current_season: Callable[[], int] = _current_season
    services: ServiceStatus | None = None
    schedule_ttl_s: float = 60.0
    # CR02: the runner (tests inject one with a fake launcher), the key check, the stream
    runner: Runner | None = None
    fail_at: str | None = None  # `nfl app --rehearsal --fail-at STEP` (the first rehearsal)
    keys_set: Callable[[tuple[str, ...]], dict[str, bool]] = _keys_set
    stream_poll_s: float = 0.5
    stream_heartbeat_s: float = 15.0
    # CR03: the cached, read-only W&B reads and the Health page's slow checks (tests inject
    # fakes: no test reaches W&B, Neo4j, Docker or OpenRouter)
    wandb: WandbReader | None = None
    system_checks: SystemChecks | None = None
    # LD02: Game day (the live bot). Tests inject a service on a fake ESPN transport; under
    # `nfl app --live-replay` the feed serves a finished week as if live.
    live: LiveService | None = None
    live_replay: Any = None


class _Context:
    """Per-app state: the settings and a short cache of the schedule snapshot."""

    def __init__(self, settings: AppSettings) -> None:
        self.s = settings
        self.services = settings.services or ServiceStatus()
        self.runner = settings.runner or Runner(
            rehearsal=settings.rehearsal, fail_at=settings.fail_at
        )
        self.wandb = settings.wandb or WandbReader(cache_dir=self._wandb_cache)
        self.checks = settings.system_checks or SystemChecks()
        self.live = settings.live or LiveService(now=settings.now, replay=settings.live_replay)
        self._sched: tuple[float, Any] | None = None
        self._lock = threading.Lock()

    def _wandb_cache(self) -> Path | None:
        """`{NFL_DATA_ROOT}/cache/control-room/wandb/`: the app's own folder (README §4)."""
        try:
            return self.s.data_root().cache / "control-room" / "wandb"
        except DataRootError:
            return None

    def paths(self) -> DataPaths:
        paths = self.s.data_root()
        use_data_root(paths.root)  # every text `clean()` touches loses the root (D102)
        return paths

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

    def schedules_or_none(self, paths: DataPaths) -> Any:
        try:
            return self.schedules(paths)
        except FileNotFoundError:
            return None

    def preflight(self, paths: DataPaths) -> dict[str, Any]:
        plan = self.plan(paths)
        cur = self.runner.current(paths, plan)
        running = cur["run"] if cur["state"] == "running" else None
        rehearsal = None
        if self.s.rehearsal:
            season = plan.season if plan is not None else self.s.current_season()
            target = rehearsal_target(paths, plan, season)
            rehearsal = {
                "season": target[0] if target else None,
                "week": target[1] if target else None,
                "root": rehearsal_root(paths),
                "lock_held": running is not None,
            }
        return get_preflight(
            paths,
            plan,
            now=self.s.now(),
            current_season=self.s.current_season(),
            neo4j=self.services.snapshot(),
            keys_set=self.s.keys_set,
            running=running,
            rehearsal=rehearsal,
        )


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
            ctx.live.close()

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

    @app.exception_handler(RequestValidationError)
    async def bad_request(req: Request, _exc: RequestValidationError) -> SafeJSONResponse:
        # fixed text: the validation details echo the request's values back
        if req.url.path == "/api/run":
            return _error(
                422,
                "bad_request",
                "A run request is {kind: weekly | resume | injury_update, expect_week: 1-22}.",
            )
        if req.url.path.startswith("/api/weeks/"):
            return _error(422, "bad_request", "Season and week must be whole numbers in range.")
        if req.url.path.startswith("/api/live/"):
            return _error(
                422,
                "bad_request",
                "Season, week and the ESPN event id must be whole numbers in range; the team a "
                "2-3 letter code.",
            )
        return _error(422, "bad_request", "A parameter is malformed or out of range.")

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

    # ---- CR01: the week archive. Season and week are validated integers; no endpoint
    # takes a file path (README §4). Every answer goes through `strip_root`: no string can
    # carry the data root's path (NFL_DATA_ROOT's value) to the browser (Sol review, CR01).
    Season = PathParam(ge=1999, le=2100)
    Week = PathParam(ge=1, le=22)

    def week_json(paths: DataPaths, body: Any) -> SafeJSONResponse:
        return SafeJSONResponse(strip_root(body, paths.root))

    @app.get("/api/team-info")
    def team_info_route() -> SafeJSONResponse:
        paths = ctx.paths()
        return week_json(paths, team_info(paths))

    @app.get("/api/weeks/{season}/{week}")
    def week_route(season: int = Season, week: int = Week) -> SafeJSONResponse:
        paths = ctx.paths()
        body = get_week(
            paths,
            season,
            week,
            ctx.plan(paths),
            lock_dict(paths),
            ctx.schedules_or_none(paths),
            settings.now(),
        )
        return week_json(paths, body)

    @app.get("/api/weeks/{season}/{week}/pipeline")
    def pipeline_route(season: int = Season, week: int = Week) -> SafeJSONResponse:
        paths = ctx.paths()
        return week_json(
            paths, get_pipeline(paths, season, week, ctx.plan(paths), lock_dict(paths))
        )

    @app.get("/api/weeks/{season}/{week}/digest")
    def digest_route(season: int = Season, week: int = Week) -> SafeJSONResponse:
        paths = ctx.paths()
        return week_json(paths, get_digest(paths, season, week))

    @app.get("/api/weeks/{season}/{week}/games")
    def games_route(season: int = Season, week: int = Week) -> SafeJSONResponse:
        paths = ctx.paths()
        body = get_games(paths, season, week, ctx.plan(paths), ctx.schedules_or_none(paths))
        return week_json(paths, body)

    @app.get("/api/weeks/{season}/{week}/players")
    def players_route(
        season: int = Season,
        week: int = Week,
        stats: str = Query("main", pattern="^(main|all)$"),
    ) -> SafeJSONResponse:
        paths = ctx.paths()
        return week_json(paths, get_players(paths, season, week, stats))

    @app.get("/api/weeks/{season}/{week}/results")
    def results_route(season: int = Season, week: int = Week) -> SafeJSONResponse:
        paths = ctx.paths()
        return week_json(paths, get_results(paths, season, week, ctx.schedules_or_none(paths)))

    @app.get("/api/weeks/{season}/{week}/graph")
    def graph_route(season: int = Season, week: int = Week) -> SafeJSONResponse:
        paths = ctx.paths()
        return week_json(paths, get_graph(paths, season, week))

    # ---- CR02: run control. The server decides the week and builds the command; the browser
    # sends a kind and its expected week (a cross-check). POSTs need the launch token and a
    # same-origin Origin (security.py). Nothing here takes a path or a command.

    @app.get("/api/preflight")
    def preflight_route() -> SafeJSONResponse:
        paths = ctx.paths()
        return week_json(paths, ctx.preflight(paths))

    @app.post("/api/run")
    def run_route(body: RunBody) -> SafeJSONResponse:
        paths = ctx.paths()
        pf = ctx.preflight(paths)
        lock_held = (
            ctx.runner.current(paths, ctx.plan(paths))["state"] == "running"
            if settings.rehearsal
            else lock_dict(paths)["held"]
        )
        try:
            started = ctx.runner.start(
                body.kind,
                body.expect_week,
                paths=paths,
                preflight=pf,
                now=settings.now(),
                lock_held=lock_held,
            )
        except RunRefused as e:
            status = 409 if e.code in ("running", "locked") else 403
            return _error(status, e.code, e.message)
        log.warning("control room started: %s (run %s)", started["command"], started["run_id"])
        return SafeJSONResponse(strip_root(started, paths.root), 202)

    @app.get("/api/run/current")
    def run_current_route() -> SafeJSONResponse:
        paths = ctx.paths()
        return week_json(paths, public(ctx.runner.current(paths, ctx.plan(paths))))

    @app.get("/api/run/stream")
    async def run_stream_route(
        request: Request, after: int | None = Query(None, ge=0)
    ) -> StreamingResponse:
        paths = ctx.paths()
        cursor_run, cursor = parse_cursor(request.headers.get("last-event-id"))
        start = after if after is not None else cursor

        def current() -> dict[str, Any]:
            return ctx.runner.current(paths, ctx.plan(paths))

        gen = run_stream(
            current=current,
            paths=paths,
            after=start,
            after_run=None if after is not None else cursor_run,
            disconnected=request.is_disconnected,
            poll_s=settings.stream_poll_s,
            heartbeat_s=settings.stream_heartbeat_s,
            public=public,
        )
        return StreamingResponse(
            gen,
            media_type="text/event-stream",
            headers={"x-accel-buffering": "no"},
        )

    # ---- CR03: MLOps, W&B and the season pages. Local files first; W&B (cached, read-only)
    # for run lists, links, versions, aliases and lineage. `refresh=1` skips the W&B cache once.

    def week_runs(paths: DataPaths, season: int, week: int, refresh: bool) -> tuple[Any, dict]:
        plan = ctx.plan(paths)
        current = plan is not None and (plan.season, plan.week) == (season, week)
        done = mlops.week_published(paths, season, week) and not current
        return ctx.wandb.get(
            f"runs-{season}-w{week:02d}",
            wandb_api.fetch_week_runs(season, week),
            ttl=wandb_api.TTL_FINISHED_S if done else wandb_api.TTL_LIVE_S,
            refresh=refresh,
        )

    def artifacts(refresh: bool) -> tuple[Any, dict]:
        return ctx.wandb.get(
            "artifacts",
            wandb_api.fetch_artifacts(ctx.wandb.peek("artifacts")),
            ttl=wandb_api.TTL_LIVE_S,
            refresh=refresh,
        )

    Refresh = Query(False)

    @app.get("/api/weeks/{season}/{week}/mlops/health")
    def mlops_health_route(season: int = Season, week: int = Week) -> SafeJSONResponse:
        paths = ctx.paths()
        body = mlops.get_health(paths, season, week, ctx.plan(paths), lock_dict(paths))
        return week_json(paths, body)

    @app.get("/api/weeks/{season}/{week}/mlops/wandb")
    def mlops_wandb_route(
        season: int = Season, week: int = Week, refresh: bool = Refresh
    ) -> SafeJSONResponse:
        paths = ctx.paths()
        runs, state = week_runs(paths, season, week, refresh)
        return week_json(paths, mlops.get_wandb(paths, season, week, runs, state))

    @app.get("/api/weeks/{season}/{week}/mlops/artifacts")
    def mlops_artifacts_route(
        season: int = Season, week: int = Week, refresh: bool = Refresh
    ) -> SafeJSONResponse:
        paths = ctx.paths()
        data, state = artifacts(refresh)
        # a refresh also re-reads the week's runs: a newer pipeline run's lineage replaces the
        # cached one's (Sol review, CR03)
        runs, runs_state = week_runs(paths, season, week, refresh)
        pipe = next(
            (r for r in runs or [] if r.get("job") == "pipeline" and r.get("current")), None
        )
        used, used_state = None, None
        if pipe is not None:
            used, used_state = ctx.wandb.get(
                f"used-{pipe['id']}",
                wandb_api.fetch_used(pipe["id"]),
                ttl=wandb_api.TTL_FROZEN_S
                if pipe.get("state") == "finished"
                else wandb_api.TTL_LIVE_S,
                refresh=refresh,
            )
        # the banner covers every W&B read behind the answer, not just the artifact list
        state = wandb_api.merge_states(
            state, ("The week's runs", runs_state), ("The lineage", used_state)
        )
        body = mlops.get_artifacts(
            paths, season, week, data, used, pipe["id"] if pipe else None, state
        )
        return week_json(paths, body)

    @app.get("/api/season/{season}/scorecard")
    def scorecard_route(
        season: int = Season, source: str = Query("live", pattern="^(live|backtest)$")
    ) -> SafeJSONResponse:
        paths = ctx.paths()
        return week_json(paths, get_scorecard(paths, season, source))

    @app.get("/api/teams")
    def teams_route(
        season: int | None = Query(None, ge=1999, le=2100),
        week: int | None = Query(None, ge=1, le=22),
    ) -> SafeJSONResponse:
        paths = ctx.paths()
        plan = ctx.plan(paths)
        season = season or (plan.season if plan is not None else settings.current_season())
        body = get_teams(paths, season, week, ctx.schedules_or_none(paths), team_info(paths))
        return week_json(paths, body)

    @app.get("/api/models")
    def models_route(refresh: bool = Refresh) -> SafeJSONResponse:
        paths = ctx.paths()
        data, state = artifacts(refresh)
        ratings, ratings_state = ctx.wandb.get(
            "ratings-eval",
            wandb_api.fetch_run_summary(
                "track1-ratings",
                "ratings-eval",
                ("objective_mse", "mse_base", "elo_brier", "games"),
            ),
            ttl=wandb_api.TTL_FROZEN_S,
            refresh=refresh,
        )
        season = settings.current_season()
        state = wandb_api.merge_states(state, ("The ratings evaluation", ratings_state))
        return week_json(paths, get_models(paths, season, data, ratings, state))

    @app.get("/api/models/card/{card_id}")
    def model_card_route(
        card_id: str = PathParam(pattern="^[a-z0-9-]{1,40}$"),
    ) -> SafeJSONResponse:
        paths = ctx.paths()
        body = model_card(card_id)
        if body is None:
            return _error(404, "not_found", "No such model card.")
        return week_json(paths, body)

    @app.get("/api/alerts")
    def alerts_route(season: int | None = Query(None, ge=1999, le=2100)) -> SafeJSONResponse:
        paths = ctx.paths()
        plan = ctx.plan(paths)
        season = season or (plan.season if plan is not None else settings.current_season())
        current = plan.week if plan is not None and plan.season == season else None
        urls = {}
        for w in range(1, 23):  # the pipeline runs' links, from the W&B cache only
            for r in ctx.wandb.peek(f"runs-{season}-w{w:02d}") or []:
                if r.get("job") == "pipeline" and r.get("current"):
                    urls[w] = r.get("url")
        body = get_alerts(paths, season, current, urls, ctx.schedules_or_none(paths))
        return week_json(paths, body)

    @app.get("/api/health")
    def health_route(refresh: bool = Refresh) -> SafeJSONResponse:
        paths = ctx.paths()
        plan = ctx.plan(paths)
        season = plan.season if plan is not None else settings.current_season()
        body = get_health(
            paths, season, ctx.checks, ctx.services.snapshot(), settings.keys_set, refresh
        )
        return week_json(paths, body)

    # ---- LD02: Game day (documentation/live-decisions/). Season, week and the ESPN event id
    # are validated; the event must be in the week's ESPN scoreboard. A check is a GET and
    # changes nothing; ESPN is reached only through the one shared client (live/espn.py).
    Event = PathParam(pattern="^[0-9]{1,12}$")

    def live_json(request: Request, paths: DataPaths, fn: Callable[[], Any]) -> SafeJSONResponse:
        # these GETs reach ESPN and run the engine: another site's page (an <img> tag) mustn't
        # set them off, so a browser's cross-site or same-site request is refused (review S2)
        if request.headers.get("sec-fetch-site", "").lower() in ("cross-site", "same-site"):
            return _error(403, "not_allowed", "Game day answers only the control room's own page.")
        try:
            body = fn()
        except LiveRefused as e:
            return _error(e.status, e.code, e.message)
        return week_json(paths, body)

    @app.get("/api/live/{season}/{week}/games")
    def live_games_route(
        request: Request, season: int = Season, week: int = Week, refresh: bool = Refresh
    ) -> SafeJSONResponse:
        paths = ctx.paths()
        return live_json(request, paths, lambda: ctx.live.games(paths, season, week, refresh))

    @app.get("/api/live/{season}/{week}/games/{event}/call")
    def live_call_route(
        request: Request, season: int = Season, week: int = Week, event: str = Event
    ) -> SafeJSONResponse:
        paths = ctx.paths()
        return live_json(request, paths, lambda: ctx.live.call(paths, season, week, event))

    @app.get("/api/live/{season}/{week}/games/{event}/context")
    def live_context_route(
        request: Request,
        season: int = Season,
        week: int = Week,
        event: str = Event,
        offense: str = Query(..., pattern="^[A-Z]{2,3}$"),
    ) -> SafeJSONResponse:
        paths = ctx.paths()
        return live_json(
            request, paths, lambda: ctx.live.context(paths, season, week, event, offense)
        )

    # ---- LD03: the decision review (curated plays + the models; never ESPN). It runs the
    # engine on a week without a stored review, so it sits behind the same cross-site refusal.
    @app.get("/api/live/{season}/{week}/review")
    def live_review_route(
        request: Request, season: int = Season, week: int = Week
    ) -> SafeJSONResponse:
        paths = ctx.paths()
        return live_json(request, paths, lambda: ctx.live.review(paths, season, week))

    @app.get("/api/live/{season}/season-review")
    def live_season_review_route(
        request: Request,
        season: int = Season,
        through: int | None = Query(None, ge=1, le=22),
    ) -> SafeJSONResponse:
        paths = ctx.paths()
        return live_json(request, paths, lambda: ctx.live.season_review(paths, season, through))

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
