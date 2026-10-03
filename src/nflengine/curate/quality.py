"""Data-quality checks on curated tables (documentation/03 -> Curation and data-quality checks).

BLOCK-level failures stop the pipeline; WARN-level ones are reported and logged.
Each check is a small function over DataFrames so it can be unit-tested on fixtures.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import polars as pl

from nflengine.curate.teams import CANONICAL_TEAMS
from nflengine.paths import ensure_data_root
from nflengine.settings import get_config

BLOCK, WARN = "block", "warn"


@dataclass
class QualityResult:
    name: str
    level: str
    passed: bool
    detail: str


# ---- individual checks (pure) ------------------------------------------------------


def check_unique(df: pl.DataFrame, keys: list[str], name: str, level: str = BLOCK) -> QualityResult:
    dupes = df.height - df.unique(subset=keys).height
    return QualityResult(name, level, dupes == 0, f"{dupes:,} duplicate rows on {keys}")


def check_team_codes(tables: dict[str, tuple[pl.DataFrame, list[str]]]) -> QualityResult:
    bad: dict[str, list[str]] = {}
    for tname, (df, cols) in tables.items():
        for c in cols:
            if c in df.columns:
                vals = df.select(pl.col(c).drop_nulls().unique())[c].to_list()
                unknown = sorted(v for v in vals if v not in CANONICAL_TEAMS)
                if unknown:
                    bad[f"{tname}.{c}"] = unknown[:8]
    detail = "all canonical" if not bad else json.dumps(bad)[:300]
    return QualityResult("team codes canonical", BLOCK, not bad, detail)


def check_completeness(
    games: pl.DataFrame, plays_game_ids: set[str], min_season: int
) -> QualityResult:
    done = games.filter(
        pl.col("completed") & (pl.col("season") >= min_season) & (pl.col("game_type") != "SBBYE")
    )
    missing = sorted(set(done["game_id"].to_list()) - plays_game_ids)
    detail = (
        f"all {done.height:,} completed games have play-by-play"
        if not missing
        else f"{len(missing)} completed games missing from plays, e.g. {missing[:5]}"
    )
    return QualityResult("completed games in play-by-play", BLOCK, not missing, detail)


def check_plays_per_game(per_game: pl.DataFrame, lo: int = 100, hi: int = 260) -> QualityResult:
    out = per_game.filter((pl.col("n") < lo) | (pl.col("n") > hi))
    return QualityResult(
        "plays per game in range",
        WARN,
        out.height == 0,
        f"{out.height} games outside [{lo}, {hi}]"
        + (f", e.g. {out.head(3).rows()}" if out.height else ""),
    )


def check_lines_sign(games: pl.DataFrame, threshold: float = 0.95) -> QualityResult:
    g = games.filter(
        pl.col("spread_line").is_not_null()
        & pl.col("home_moneyline").is_not_null()
        & (pl.col("spread_line") != 0)
        & (pl.col("home_moneyline") != pl.col("away_moneyline"))
    )
    if g.height == 0:
        return QualityResult("lines sign convention", BLOCK, False, "no games with lines")
    agree = g.filter((pl.col("spread_line") > 0) == (pl.col("home_moneyline") < 0)).height
    rate = agree / g.height
    return QualityResult(
        "lines sign convention",
        BLOCK,
        rate >= threshold,
        f"spread>0 <-> home favourite agrees on {rate:.1%} of {g.height:,} games "
        "(+ spread = home favoured)",
    )


def check_join_rates(joins: list[dict], threshold: float = 0.98) -> list[QualityResult]:
    out = []
    for j in joins:
        if j["total"] == 0:
            continue
        # ESPN tables include practice-squad / fringe players missing from nflverse players.
        thr = threshold if not j["table"].startswith("espn") else 0.85
        out.append(
            QualityResult(
                f"gsis join: {j['table']}",
                WARN,
                j["rate"] >= thr,
                f"{j['rate']:.1%} of {j['total']:,} rows matched via {j['key']} (min {thr:.0%})",
            )
        )
    return out


def check_freshness(
    latest_weeks: dict[str, int | None], expected_week: int | None
) -> QualityResult:
    if expected_week is None:
        return QualityResult("current-season freshness", WARN, True, "no completed week yet")
    # PFR advanced stats usually trail by a few days.
    lag_ok = {"pfr_def": 1, "pfr_pass": 1, "pfr_rush": 1, "pfr_rec": 1, "ftn_plays": 1}
    stale = {
        t: w for t, w in latest_weeks.items() if w is None or w < expected_week - lag_ok.get(t, 0)
    }
    detail = (
        f"all sources reach week {expected_week}"
        if not stale
        else f"behind week {expected_week}: {stale}"
    )
    return QualityResult("current-season freshness", WARN, not stale, detail)


def check_players_per_team_week(pg: pl.DataFrame, lo: int = 15, hi: int = 80) -> QualityResult:
    counts = pg.group_by("season", "week", "team").len()
    out = counts.filter((pl.col("len") < lo) | (pl.col("len") > hi))
    return QualityResult(
        "players per team-week in range",
        WARN,
        out.height == 0,
        f"{out.height} team-weeks outside [{lo}, {hi}]",
    )


# ---- runner -----------------------------------------------------------------------------


def run_quality_checks(curated: Path | None = None) -> list[QualityResult]:
    curated = curated or ensure_data_root().curated
    cfg = get_config()
    current = cfg.seasons.current

    def load(name: str) -> pl.DataFrame:
        return pl.read_parquet(curated / f"{name}.parquet")

    plays = pl.scan_parquet(
        (curated / "plays" / "*.parquet").as_posix(), allow_missing_columns=True
    )
    games = load("games")
    results: list[QualityResult] = [
        check_unique(games, ["game_id"], "games unique"),
        check_unique(
            plays.select("game_id", "play_id").collect(), ["game_id", "play_id"], "plays unique"
        ),
    ]
    pg = load("player_games")
    results.append(check_unique(pg, ["player_id", "game_id"], "player_games unique"))

    team_tables = {
        "games": (games, ["home_team", "away_team"]),
        "plays": (plays.select("posteam", "defteam").collect(), ["posteam", "defteam"]),
        "player_games": (pg, ["team", "opponent_team"]),
        "snaps": (load("snaps"), ["team", "opponent"]),
        "injuries": (load("injuries"), ["team"]),
        "ngs_receiving": (load("ngs_receiving"), ["team"]),
        "pfr_def": (load("pfr_def"), ["team", "opponent"]),
        "depth_charts": (load("depth_charts"), ["team"]),
    }
    results.append(check_team_codes(team_tables))

    play_ids = set(plays.select("game_id").unique().collect()["game_id"].to_list())
    results.append(check_completeness(games, play_ids, cfg.seasons.history_start))
    per_game = (
        plays.filter(pl.col("play_type").is_not_null())
        .group_by("game_id")
        .agg(pl.len().alias("n"))
        .collect()
    )
    results.append(check_plays_per_game(per_game))
    results.append(check_lines_sign(games))
    results.append(check_players_per_team_week(pg.filter(pl.col("season_type") == "REG")))

    joins = json.loads((curated / "_joins.json").read_text())
    results += check_join_rates(joins)

    from nflengine import schedule as sched

    expected = sched.last_completed_week(games, current)
    latest = {}
    for name in ("player_games", "snaps", "injuries", "ngs_receiving", "pfr_def", "ftn_plays"):
        df = load(name).filter(pl.col("season") == current)
        latest[name] = int(df["week"].max()) if df.height else None
    latest["plays"] = (
        plays.filter(pl.col("season") == current).select(pl.col("week").max()).collect().item()
    )
    results.append(check_freshness(latest, expected))

    qdir = curated / "_quality"
    qdir.mkdir(exist_ok=True)
    (qdir / "latest.json").write_text(
        json.dumps(
            {"run_at": dt.datetime.now().isoformat(timespec="seconds"),
             "results": [asdict(r) for r in results]},
            indent=2,
        )
    )  # fmt: skip
    return results
