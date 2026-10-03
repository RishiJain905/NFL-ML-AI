"""Data-readiness check: are all of week N's games final and in play-by-play?

Used by `nfl ingest --check-ready` now and by the weekly scheduler in P07.
Reads the newest *raw* snapshots so it works before curation runs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import polars as pl

from nflengine.ingest.base import SnapshotStore
from nflengine.paths import ensure_data_root
from nflengine.settings import get_config


@dataclass
class ReadinessReport:
    season: int
    week: int
    games: int
    final: int
    in_pbp: int
    missing_results: list[str] = field(default_factory=list)
    missing_pbp: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.games > 0 and not self.missing_results and not self.missing_pbp

    def summary(self) -> str:
        head = f"{self.season} week {self.week}: {'READY' if self.ready else 'NOT READY'}"
        body = f"{self.final}/{self.games} games final, {self.in_pbp}/{self.games} in play-by-play"
        extra = ""
        if self.missing_results:
            extra += f"; no result yet: {self.missing_results}"
        if self.missing_pbp:
            extra += f"; missing play-by-play: {self.missing_pbp}"
        return f"{head} ({body}{extra})"


def evaluate(
    schedules: pl.DataFrame, pbp_game_ids: set[str], season: int, week: int
) -> ReadinessReport:
    wk = schedules.filter((pl.col("season") == season) & (pl.col("week") == week))
    no_result = wk.filter(pl.col("result").is_null())["game_id"].to_list()
    ids = wk["game_id"].to_list()
    missing_pbp = [g for g in ids if g not in pbp_game_ids]
    return ReadinessReport(
        season, week, len(ids), len(ids) - len(no_result), len(ids) - len(missing_pbp),
        no_result, missing_pbp,
    )  # fmt: skip


def default_week(schedules: pl.DataFrame, season: int, today: dt.date | None = None) -> int:
    """Most recent week whose last game day is before today."""
    today = today or dt.date.today()
    wk = (
        schedules.filter(pl.col("season") == season)
        .group_by("week")
        .agg(pl.col("gameday").max().str.to_date().alias("last_day"))
        .filter(pl.col("last_day") < today)
    )
    return int(wk["week"].max()) if wk.height else 1


def check_ready(season: int | None = None, week: int | None = None) -> ReadinessReport:
    season = season or get_config().seasons.current
    store = SnapshotStore(ensure_data_root().raw)
    schedules = store.read_latest("nflverse", "schedules")
    week = week or default_week(schedules, season)
    pbp_path = store.latest_part_path("nflverse", "pbp", f"season={season}")
    pbp_ids: set[str] = set()
    if pbp_path is not None:
        pbp_ids = set(pl.read_parquet(pbp_path, columns=["game_id"])["game_id"].unique().to_list())
    return evaluate(schedules, pbp_ids, season, week)
