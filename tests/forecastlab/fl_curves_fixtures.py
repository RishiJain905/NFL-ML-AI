"""Synthetic data for the FL01 (outcome curves) tests: walk-forward history in the player
model's file layout, a live week's projections, and a tiny data root. Nothing here reads the
real data root or any credential."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from nflengine.forecastlab import schema as S
from nflengine.paths import DataPaths

UTC_US = pl.Datetime("us", "UTC")
GAMES_PER_WEEK = 6


def kickoff(season: int, week: int, game: int) -> datetime:
    """Sunday-ish kickoffs, one per game, a day apart inside the week."""
    return datetime(season, 9, 1, 17, tzinfo=UTC) + timedelta(weeks=week - 1, days=game % 3)


def synth_rows(
    key: str,
    seasons: range | list[int],
    weeks: range | list[int] = range(1, 18),
    players: int = 40,
    seed: int = 7,
    *,
    actual: bool = True,
    model_version: str = "player-model-v1:backtest",
) -> pl.DataFrame:
    """Walk-forward-style rows of one curve target: every player has a projection each week
    (P10 / P50 / P90 for amounts, a mean and whole-number quantiles for counts) and a result
    drawn around it from a right-skewed distribution, so the real curve is not a bell."""
    t = S.CURVE_BY_KEY[key]
    rng = np.random.default_rng(seed)
    base = rng.gamma(2.0, 12.0 if t.kind == "amount" else 1.6, size=players) + 1.0
    recs = []
    for season in seasons:
        for week in weeks:
            for i in range(players):
                p50 = float(base[i] * rng.uniform(0.8, 1.2))
                if t.kind == "amount":
                    p10, p90 = max(0.0, p50 * 0.2), p50 * 2.0 + 8.0
                    mean = p50
                    y = max(t.floor, round(p50 + (rng.gamma(1.5, 0.35) - 0.5) * (p90 - p10)))
                else:
                    mean = p50
                    p10, p90 = float(np.floor(mean * 0.4)), float(np.ceil(mean * 1.9))
                    p50 = float(np.round(mean))
                    y = float(rng.negative_binomial(3, 3 / (3 + mean)))
                g = i % GAMES_PER_WEEK
                recs.append(
                    {
                        "season": season,
                        "week": week,
                        "game_id": f"{season}_{week:02d}_G{g}",
                        "kickoff_utc": kickoff(season, week, g),
                        "created_at": kickoff(season, week, g) - timedelta(days=5),
                        "player_id": f"P{i:03d}",
                        "player": f"Player {i}",
                        "team": f"T{i % 8}",
                        "opponent": f"T{(i + 3) % 8}",
                        "home": bool(i % 2),
                        "position": t.group.split("/")[0],
                        "pgroup": t.group.split("/")[0],
                        "group": t.group,
                        "target": t.name,
                        "target_label": t.label,
                        "kind": t.kind,
                        "unit": "yards" if t.kind == "amount" else "count",
                        "is_main": True,
                        "p10": p10,
                        "p50": p50,
                        "p90": p90,
                        "mean": mean,
                        "model_version": model_version,
                        "actual": float(y) if actual else None,
                        "played": True if actual else None,
                    }
                )
    df = pl.DataFrame(recs)
    return df.with_columns(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("kickoff_utc").cast(UTC_US),
        pl.col("created_at").cast(UTC_US),
        pl.col("actual").cast(pl.Float64),
        pl.col("played").cast(pl.Boolean),
    )


def make_root(
    tmp: Path,
    key: str = "rec_yds-wrte",
    seasons: range | list[int] = range(2017, 2022),
    **kw,
) -> DataPaths:
    """A data root with the target's walk-forward backtest file under
    `runs/backtests/player/<key>/`."""
    paths = DataPaths(tmp)
    f = paths.runs / "backtests" / "player" / key / "predictions_players.parquet"
    f.parent.mkdir(parents=True, exist_ok=True)
    synth_rows(key, seasons, **kw).write_parquet(f)
    return paths


def write_live_week(
    paths: DataPaths,
    key: str,
    season: int,
    week: int,
    players: int = 40,
    seed: int = 99,
    created: datetime | None = None,
) -> pl.DataFrame:
    """`runs/<S>/week<NN>/predictions_players.parquet`: the week's projections (no results)."""
    df = synth_rows(
        key,
        [season],
        [week],
        players=players,
        seed=seed,
        actual=False,
        model_version=f"player-model-v1:{season}-w{week:02d}",
    ).with_columns(
        pl.lit(created or datetime(season, 9, 1, tzinfo=UTC) + timedelta(weeks=week - 1))
        .cast(UTC_US)
        .alias("created_at"),
    )
    f = paths.run_dir(season, week) / "predictions_players.parquet"
    f.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(f)
    return df


def write_production(
    paths: DataPaths, params: dict, shipped: list[str], version: str = "2021-2025_20261114T120000Z"
) -> None:
    import json

    f = S.production_file(paths, "curves")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        json.dumps({"version": version, "shipped": shipped, "params": params}), encoding="utf-8"
    )
