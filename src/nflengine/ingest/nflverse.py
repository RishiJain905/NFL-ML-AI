"""nflverse ingestion via nflreadpy (documentation/03 -> nflverse).

Each dataset in `DATASETS` declares its loader, the first season it exists, and
whether it is pulled per season or as one whole-dataset file. Per-season datasets
write one `season=YYYY` part per season; completed seasons are only pulled once
unless `refresh_history=True` (decision D32).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

from nflengine.ingest.base import DatasetResult, SnapshotStore

SOURCE = "nflverse"


@dataclass(frozen=True)
class NflverseDataset:
    name: str
    loader: Callable[..., pl.DataFrame]
    first_season: int | None = None  # None -> whole-dataset pull (no seasons arg)
    last_season: int | None = None  # e.g. participation is only published after a season ends
    research_only: bool = False  # written under research/, never read by live features


def _datasets() -> list[NflverseDataset]:
    import nflreadpy as nfl

    def season_loader(fn: Callable[..., pl.DataFrame], **kw) -> Callable[[int], pl.DataFrame]:
        return lambda season: fn(season, **kw)

    return [
        NflverseDataset("pbp", season_loader(nfl.load_pbp), 2010),
        NflverseDataset(
            "player_stats", season_loader(nfl.load_player_stats, summary_level="week"), 2010
        ),
        NflverseDataset(
            "team_stats", season_loader(nfl.load_team_stats, summary_level="week"), 2010
        ),
        NflverseDataset(
            "ngs_passing", season_loader(nfl.load_nextgen_stats, stat_type="passing"), 2016
        ),
        NflverseDataset(
            "ngs_receiving", season_loader(nfl.load_nextgen_stats, stat_type="receiving"), 2016
        ),
        NflverseDataset(
            "ngs_rushing", season_loader(nfl.load_nextgen_stats, stat_type="rushing"), 2016
        ),
        NflverseDataset(
            "pfr_pass",
            season_loader(nfl.load_pfr_advstats, stat_type="pass", summary_level="week"),
            2018,
        ),
        NflverseDataset(
            "pfr_rush",
            season_loader(nfl.load_pfr_advstats, stat_type="rush", summary_level="week"),
            2018,
        ),
        NflverseDataset(
            "pfr_rec",
            season_loader(nfl.load_pfr_advstats, stat_type="rec", summary_level="week"),
            2018,
        ),
        NflverseDataset(
            "pfr_def",
            season_loader(nfl.load_pfr_advstats, stat_type="def", summary_level="week"),
            2018,
        ),
        NflverseDataset("ftn_charting", season_loader(nfl.load_ftn_charting), 2022),
        NflverseDataset("snap_counts", season_loader(nfl.load_snap_counts), 2013),  # 2012 is empty
        NflverseDataset("injuries", season_loader(nfl.load_injuries), 2010),
        NflverseDataset("rosters_weekly", season_loader(nfl.load_rosters_weekly), 2010),
        NflverseDataset("depth_charts", season_loader(nfl.load_depth_charts), 2010),
        NflverseDataset(
            "participation", season_loader(nfl.load_participation), 2016, research_only=True
        ),
        NflverseDataset("schedules", lambda: nfl.load_schedules(True)),
        NflverseDataset("players", nfl.load_players),
        NflverseDataset("teams", nfl.load_teams),
        NflverseDataset("officials", lambda: nfl.load_officials(True)),
        NflverseDataset("trades", nfl.load_trades),
        NflverseDataset("draft_picks", lambda: nfl.load_draft_picks(True)),
    ]


def dataset_names() -> list[str]:
    return [d.name for d in _datasets()]


def ingest_nflverse(
    store: SnapshotStore,
    research_store: SnapshotStore,
    current_season: int,
    seasons: list[int] | None = None,
    datasets: list[str] | None = None,
    refresh_history: bool = False,
    log: Callable[[str], None] = print,
) -> list[DatasetResult]:
    import nflreadpy.config

    # In-memory cache only: raw snapshots already persist everything on D:.
    nflreadpy.config.update_config(cache_mode="memory")

    results = []
    for ds in _datasets():
        if datasets and ds.name not in datasets:
            continue
        target = research_store if ds.research_only else store
        if ds.first_season is None:
            results.append(_ingest_whole(target, ds, log))
        else:
            results.append(
                _ingest_seasons(target, ds, current_season, seasons, refresh_history, log)
            )
    return results


def _ingest_whole(store: SnapshotStore, ds: NflverseDataset, log) -> DatasetResult:
    try:
        df = ds.loader()
        store.write_parquet(SOURCE, ds.name, "all", df)
        log(f"  {ds.name}: {df.height:,} rows")
        return DatasetResult(SOURCE, ds.name, "ok", df.height, ["all"])
    except Exception as exc:
        log(f"  {ds.name}: FAILED {type(exc).__name__}: {exc}")
        return DatasetResult(SOURCE, ds.name, "failed", detail=f"{type(exc).__name__}: {exc}")


def _ingest_seasons(
    store: SnapshotStore,
    ds: NflverseDataset,
    current_season: int,
    seasons: list[int] | None,
    refresh_history: bool,
    log,
) -> DatasetResult:
    wanted = seasons or list(range(ds.first_season, current_season + 1))
    wanted = [s for s in wanted if s >= ds.first_season]
    rows, parts, problems, notes = 0, [], [], []
    for season in wanted:
        part = f"season={season}"
        is_history = season < current_season
        if is_history and not refresh_history and store.has_part(SOURCE, ds.name, part):
            continue  # completed season already captured (D32)
        try:
            df = ds.loader(season)
        except Exception as exc:
            msg = str(exc)
            if season == current_season and "must be between" in msg:
                notes.append(f"{season}: not published yet")  # expected (e.g. participation)
            else:
                problems.append(f"{season}: {type(exc).__name__}: {msg[:120]}")
            continue
        if df.height == 0:
            (notes if season == current_season else problems).append(f"{season}: empty")
            continue
        store.write_parquet(SOURCE, ds.name, part, df)
        rows += df.height
        parts.append(part)
    if not problems:
        status = "ok"
    elif parts:
        status = "partial"
    else:
        status = "failed"
    detail = "; ".join(problems + notes)
    log(
        f"  {ds.name}: {len(parts)} season(s) written, {rows:,} rows"
        + (f" | {detail}" if detail else "")
    )
    return DatasetResult(SOURCE, ds.name, status, rows, parts, detail)
