"""`nfl ratings build`: the P02 feature tables on D: (documentation/04 -> A; plan P02).

Writes under {NFL_DATA_ROOT}/features:
    team_ratings.parquet        opponent-adjusted EPA / success ratings per team per as-of week
    team_elo.parquet            Elo per team per as-of week
    team_trends.parquet         trend_delta, perf_vs_expected, direction + evidence fields
    team_trend_drivers.parquet  the rating parts that moved most
    _meta/team_ratings.json     parameters, git commit and dataset version used

Every row is "as of" (season, week): built only from games in weeks before `week`, so P03
can join a game in week w straight onto the week-w row. The DuckDB file gets views for
these tables too, and a sanity report goes to the current run folder.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import polars as pl

from nflengine.features.asof import AsOf, last_asof_week
from nflengine.models.elo import EloParams, team_elo
from nflengine.models.ratings import (
    RATING_PLAY_COLS,
    RatingParams,
    compute_ratings,
    net_lookup,
    prepare_inputs,
    qb_changes,
    rating_plays,
    ratings_frame,
)
from nflengine.models.ratings_eval import game_targets, team_game_epa
from nflengine.models.trend import (
    drivers_table,
    pass_share,
    team_game_margins,
    trend_table,
)
from nflengine.paths import DataPaths, ensure_data_root
from nflengine.settings import get_config
from nflengine.tracking import dataset_version, git_commit

FEATURE_TABLES = ("team_ratings", "team_elo", "team_trends", "team_trend_drivers")
DEPTH_COLS = ["season", "week", "team", "position", "depth_rank", "gsis_id"]


@dataclass
class RatingData:
    rp: pl.DataFrame  # rating plays (see ratings.rating_plays)
    games: pl.DataFrame
    depth_charts: pl.DataFrame | None


def load_rating_data(paths: DataPaths, min_season: int) -> RatingData:
    cur = paths.curated
    games = pl.read_parquet(cur / "games.parquet")
    plays = pl.scan_parquet((cur / "plays" / "*.parquet").as_posix()).select(RATING_PLAY_COLS)
    rp = rating_plays(plays.filter(pl.col("season") >= min_season))
    dc_path = cur / "depth_charts.parquet"
    depth = None
    if dc_path.exists():
        cols = [c for c in (*DEPTH_COLS, "snap_date") if c in pl.read_parquet_schema(dc_path)]
        depth = pl.read_parquet(dc_path, columns=cols)
    return RatingData(rp=rp, games=games, depth_charts=depth)


def rating_params(**overrides: float | None) -> RatingParams:
    return RatingParams.from_config(get_config().ratings, **overrides)


def elo_params() -> EloParams:
    return EloParams.from_config(get_config().elo)


def current_season(games: pl.DataFrame) -> int:
    """Newest season with any completed game (or the configured one, if later)."""
    played = games.filter(pl.col("completed"))["season"]
    return max(int(played.max()) if played.len() else 0, get_config().seasons.current)


@dataclass
class BuiltTables:
    team_ratings: pl.DataFrame
    team_elo: pl.DataFrame
    team_trends: pl.DataFrame
    team_trend_drivers: pl.DataFrame


def build_tables(
    data: RatingData,
    params: RatingParams,
    elo: EloParams,
    seasons: list[int],
    evidence: Callable[[list[AsOf]], pl.DataFrame] | None = None,
) -> BuiltTables:
    """Compute all four tables in memory (no I/O except through `evidence`)."""
    inp = prepare_inputs(data.rp, data.games, data.depth_charts, params, seasons)
    res = compute_ratings(inp, params)
    ratings = ratings_frame(res, qb_changes(data.games, data.depth_charts))
    keys = [AsOf(s, w) for s, w in res.keys]
    elo_tbl = team_elo(data.games, keys, elo)
    tg = team_game_epa(data.rp, params.garbage_weight, params.garbage_wp)
    margins = team_game_margins(game_targets(data.games, tg), net_lookup(res))
    trends = trend_table(ratings, margins, data.games)
    if evidence is not None:
        ev = evidence([k for k in keys if k.week > 3])
        if ev.height:
            trends = trends.join(ev, on=["season", "week", "team"], how="left")
    drivers = drivers_table(ratings, pass_share(data.rp, ratings))
    return BuiltTables(ratings, elo_tbl, trends, drivers)


def _evidence_fn(paths: DataPaths, min_season: int) -> Callable[[list[AsOf]], pl.DataFrame]:
    from nflengine.models.trend_evidence import load_evidence_tables, trend_evidence

    def run(keys: list[AsOf]) -> pl.DataFrame:
        return trend_evidence(load_evidence_tables(paths, min_season), keys)

    return run


def refresh_feature_views(paths: DataPaths) -> None:
    """Add/refresh DuckDB views for the feature tables (rebuilt by `nfl curate` as well)."""
    from nflengine.curate.build import write_duckdb_views

    write_duckdb_views(paths)


def sanity_report(t: BuiltTables, games: pl.DataFrame, season: int) -> str:
    """Top/bottom 5 by net EPA rating: last season's final week and the current week."""
    r = t.team_ratings
    lines = [f"# Team ratings sanity check ({dt.date.today().isoformat()})", ""]
    checks = []
    prev_last = last_asof_week(games, season - 1)
    if prev_last is not None:
        checks.append((season - 1, prev_last, f"{season - 1} final (as of week {prev_last})"))
    cur_last = last_asof_week(games, season)
    if cur_last is not None:
        checks.append((season, cur_last, f"{season} current (as of week {cur_last})"))
    cols = [
        "team",
        "net_epa",
        "off_epa",
        "def_epa",
        "net_pass_epa",
        "net_rush_epa",
        "net_sr",
        "prior_weight",
    ]
    for s, w, title in checks:
        wk = r.filter((pl.col("season") == s) & (pl.col("week") == w)).sort(
            "net_epa", descending=True
        )
        elo = t.team_elo.filter((pl.col("season") == s) & (pl.col("week") == w)).select(
            "team", "elo"
        )
        wk = wk.join(elo, on="team", how="left").select(*cols, "elo")
        lines += [
            f"## {title}",
            "",
            "Top 5:",
            "",
            _md(wk.head(5)),
            "",
            "Bottom 5:",
            "",
            _md(wk.tail(5)),
            "",
        ]
    nulls = {c: int(n) for c, n in zip(r.columns, r.null_count().row(0), strict=True) if n}
    lines += ["## Checks", "", f"- rows: {r.height:,}", f"- null counts: {nulls or 'none'}"]
    return "\n".join(lines) + "\n"


def _md(df: pl.DataFrame) -> str:
    head = "| " + " | ".join(df.columns) + " |"
    sep = "|" + "---|" * len(df.columns)
    body = [
        "| " + " | ".join(f"{v:+.3f}" if isinstance(v, float) else str(v) for v in row) + " |"
        for row in df.iter_rows()
    ]
    return "\n".join([head, sep, *body])


def write_tables(paths: DataPaths, t: BuiltTables, meta: dict) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for name in FEATURE_TABLES:
        path = paths.features / f"{name}.parquet"
        tmp = path.with_suffix(".parquet.tmp")
        getattr(t, name).write_parquet(tmp, compression="zstd")
        tmp.replace(path)
        out[name] = path
    meta_dir = paths.features / "_meta"
    meta_dir.mkdir(exist_ok=True)
    (meta_dir / "team_ratings.json").write_text(json.dumps(meta, indent=2, default=str))
    return out


def run_build(
    params: RatingParams | None = None,
    log: Callable[[str], None] = print,
    with_evidence: bool = True,
) -> dict[str, Path]:
    paths = ensure_data_root()
    params = params or rating_params()
    cfg = get_config()
    start = cfg.seasons.history_start
    data = load_rating_data(paths, start)
    season = current_season(data.games)
    seasons = list(range(start, season + 1))
    log(f"ratings: {data.rp.height:,} plays, seasons {start}-{season}, params {params.as_dict()}")
    tables = build_tables(
        data,
        params,
        elo_params(),
        seasons,
        evidence=_evidence_fn(paths, start) if with_evidence else None,
    )
    meta = {
        "built_at": dt.datetime.now().isoformat(timespec="seconds"),
        "params": params.as_dict(),
        "elo_params": asdict(elo_params()),
        "seasons": [start, season],
        "git_commit": git_commit(),
        "dataset_version": dataset_version(paths),
        "rows": {n: getattr(tables, n).height for n in FEATURE_TABLES},
    }
    out = write_tables(paths, tables, meta)
    for name, path in out.items():
        log(f"  {name}: {getattr(tables, name).height:,} rows -> {path}")
    refresh_feature_views(paths)
    week = last_asof_week(data.games, season) or 1
    run_dir = paths.run_dir(season, week)
    run_dir.mkdir(parents=True, exist_ok=True)
    report = run_dir / "ratings_sanity.md"
    report.write_text(sanity_report(tables, data.games, season), encoding="utf-8")
    log(f"  sanity report -> {report}")
    out["sanity_report"] = report
    return out
