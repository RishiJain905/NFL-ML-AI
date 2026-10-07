"""Helpers for the control-room tests: a fixture data root, a pinned clock, a fake Neo4j check,
and a TestClient that talks to 127.0.0.1:8765 (the Host the safety middleware expects).
No real server, no real data root, no network."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import polars as pl
from fastapi.testclient import TestClient

from nflengine.app.health import SystemChecks
from nflengine.app.server import AppSettings, create_app
from nflengine.app.status import ServiceStatus
from nflengine.app.wandb_api import WandbReader
from nflengine.paths import DataPaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "schedules_2024_2026.csv"
TUESDAY = dt.datetime(2026, 10, 6, 14, 0, tzinfo=dt.UTC)  # Tue 2026-10-06 10:00 ET: week 5
TOKEN = "test-launch-token"
BASE = "http://127.0.0.1:8765"


def raw_sched() -> pl.DataFrame:
    return pl.read_csv(FIXTURE, schema_overrides={"gametime": pl.String, "gameday": pl.String})


def final_through_week4() -> pl.DataFrame:
    """2026 weeks 1-4 all final (scores 0): week 5 is ready to run."""
    s = raw_sched()
    done = (pl.col("season") == 2026) & (pl.col("week") <= 4) & pl.col("result").is_null()
    return s.with_columns(pl.when(done).then(0).otherwise(pl.col("result")).alias("result"))


class FakeStatus(ServiceStatus):
    def __init__(self, state: str = "down") -> None:
        super().__init__(ping=lambda: (state, "test"), every_s=3600)
        self.check_now()

    def start(self) -> None:  # no background thread in tests
        pass


def offline_wandb(cache_dir: Path | None = None) -> WandbReader:
    """A W&B reader whose key "isn't set": every W&B part is unavailable, nothing is cached."""
    return WandbReader(cache_dir=lambda: cache_dir, client=lambda _timeout: None)


def fake_checks(results: dict[str, tuple[str, str]] | None = None) -> SystemChecks:
    """The Health page's slow checks, answered at once (no Neo4j, Docker, W&B or OpenRouter)."""
    results = results or {
        "Neo4j": ("ok", "neo4j 5.26.31 (community), gds 2.13.13, apoc 5.26.31"),
        "Docker": ("ok", "engine running (container nfl-neo4j)"),
        "Weights & Biases": ("ok", "authenticated as tester; project ent/proj"),
        "OpenRouter": ("ok", "openrouter key accepted"),
    }
    checks = SystemChecks(checks={k: (lambda v=v: v) for k, v in results.items()})
    checks.snapshot(wait=5)
    return checks


def make_paths(root: Path) -> DataPaths:
    paths = DataPaths(root)
    for d in paths.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    return paths


def make_client(
    root: Path,
    *,
    sched: pl.DataFrame | None = None,
    now: dt.datetime = TUESDAY,
    dev: bool = False,
    data_root: Any = None,
    web_dist: Path | None = None,
    **extra: Any,
) -> TestClient:
    """`extra` goes to `AppSettings` (CR02: `runner`, `keys_set`, `rehearsal`, ...). Keys are
    reported set by default: no test reads the real env file."""
    paths = make_paths(root)
    table = raw_sched() if sched is None else sched
    extra.setdefault("keys_set", lambda names: dict.fromkeys(names, True))
    extra.setdefault("stream_poll_s", 0.01)
    # CR03: never the real W&B or the doctor's network checks in a test
    extra.setdefault("wandb", offline_wandb())
    extra.setdefault("system_checks", fake_checks())
    settings = AppSettings(
        port=8765,
        token=TOKEN,
        dev=dev,
        web_dist=web_dist,
        data_root=data_root or (lambda: paths),
        schedules=lambda _p: table,
        now=lambda: now,
        current_season=lambda: 2026,
        services=FakeStatus(),
        **extra,
    )
    return TestClient(create_app(settings), base_url=BASE)


def write_week(
    paths: DataPaths,
    season: int,
    week: int,
    steps: dict[str, str],
    *,
    report: bool = False,
    checks: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    finished: str = "2026-10-04T05:02:13",
    details: dict[str, str] | None = None,
) -> Path:
    run_dir = paths.run_dir(season, week)
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "season": season,
        "week": week,
        "steps": {
            k: {
                "status": v,
                "started": "2026-10-04T03:18:17",
                "finished": finished,
                "detail": (details or {}).get(k, ""),
            }
            for k, v in steps.items()
        },
    }
    (run_dir / "weekly_run.json").write_text(json.dumps(state))
    if report:
        rp = paths.report_path(season, week)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(f"# NFL digest: {season} week {week}\n")
    if checks is not None:
        (run_dir / "checks.json").write_text(json.dumps(checks))
    if summary is not None:
        (run_dir / "run_summary.json").write_text(json.dumps(summary))
    return run_dir


ALL_OK = {
    s: "ok" for s in ("ingest", "ready", "curate", "ratings", "game", "graph", "player", "digest")
}
