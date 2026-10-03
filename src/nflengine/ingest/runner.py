"""`nfl ingest`: pull every source into dated snapshots, then write a run manifest."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import polars as pl

from nflengine import schedule as sched
from nflengine.ingest.base import DatasetResult, SnapshotStore, write_run_manifest
from nflengine.ingest.http import PoliteClient
from nflengine.paths import DataPaths, configure_tool_env, ensure_data_root
from nflengine.settings import get_config

ALL_SOURCES = ("nflverse", "espn", "ngs_site", "weather", "odds_api")


def stores(
    paths: DataPaths, snapshot_date: str | None = None
) -> tuple[SnapshotStore, SnapshotStore]:
    return SnapshotStore(paths.raw, snapshot_date), SnapshotStore(paths.research, snapshot_date)


def latest_schedules(store: SnapshotStore) -> pl.DataFrame | None:
    return store.read_latest("nflverse", "schedules")


def run_ingest(
    season: int | None = None,
    sources: list[str] | None = None,
    nflverse_datasets: list[str] | None = None,
    seasons: list[int] | None = None,
    refresh_history: bool = False,
    log: Callable[[str], None] = print,
) -> tuple[list[DatasetResult], str]:
    cfg = get_config()
    paths = ensure_data_root()
    configure_tool_env(paths)
    season = season or cfg.seasons.current
    sources = list(sources or ALL_SOURCES)
    raw, research = stores(paths)
    results: list[DatasetResult] = []
    started = dt.datetime.now()

    if "nflverse" in sources:
        log("nflverse:")
        from nflengine.ingest.nflverse import ingest_nflverse

        # seasons=None -> each dataset pulls its first season .. current
        results += ingest_nflverse(
            raw, research, season, seasons, nflverse_datasets, refresh_history, log
        )

    schedules = latest_schedules(raw)
    if schedules is None:
        log("No schedules snapshot yet: skipping sources that need the calendar.")
        sources = [s for s in sources if s == "nflverse"]
    else:
        done = sched.completed_weeks(schedules, season)
        upcoming = sched.next_week(schedules, season)
        weeks_to_pull = sorted({w for w in range(1, (upcoming or max(done or [1])) + 2) if w <= 18})

    flags = cfg.sources
    with PoliteClient(cache_dir=paths.cache_http, cache_ttl_s=6 * 3600) as client:
        if "espn" in sources and flags.get("espn", True):
            log("espn:")
            from nflengine.ingest.espn import ingest_espn

            results += ingest_espn(raw, client, season, weeks_to_pull, done, log)
        if "ngs_site" in sources and flags.get("ngs_site", True):
            log("ngs_site:")
            from nflengine.ingest.ngs_site import ingest_ngs_site

            results += ingest_ngs_site(raw, client, season, log)
        if "weather" in sources and flags.get("open_meteo", True):
            log("open_meteo:")
            from nflengine.ingest.weather import ingest_weather

            upcoming_games = sched.season_games(schedules, season).filter(
                pl.col("result").is_null()
            )
            results += ingest_weather(raw, client, upcoming_games, log)
        if "odds_api" in sources:
            from nflengine.ingest.odds_api import ingest_odds_api

            if flags.get("odds_api", False):
                log("odds_api:")
                results += ingest_odds_api(raw, client, season, log)
            else:
                results.append(
                    DatasetResult("odds_api", "odds", "skipped", detail="disabled in settings")
                )

    manifest = write_run_manifest(
        paths.raw,
        results,
        {
            "season": season,
            "sources": sources,
            "refresh_history": refresh_history,
            "snapshot_date": raw.snapshot_date,
            "started": started.isoformat(timespec="seconds"),
            "finished": dt.datetime.now().isoformat(timespec="seconds"),
        },
    )
    return results, str(manifest)
