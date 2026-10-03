"""Shared fixtures: a tiny synthetic league for the P02 rating / trend tests."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

LEAGUE_TEAMS = ["ARI", "ATL", "BAL", "BUF"]
TRUE_OFF = {"ARI": 0.10, "ATL": -0.05, "BAL": 0.0, "BUF": -0.05}
TRUE_DEF = {"ARI": 0.0, "ATL": 0.05, "BAL": -0.10, "BUF": 0.05}  # EPA allowed: lower = better
TRUE_HFA = 0.04
PAIRINGS = [((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2))]


def make_league(
    seasons=(2020, 2021, 2022),
    weeks: int = 6,
    plays_per_side: int = 60,
    in_progress: int | None = 2022,
    seed: int = 7,
) -> dict[str, pl.DataFrame]:
    """Games, plays and week-1 depth charts for 4 teams.

    Each week has two games (round robin, home side alternating). The `in_progress`
    season has weeks 1-4 played, one week-5 game played, and the rest scheduled.
    ATL starts a new QB in week 1 of the second season.
    """
    rng = np.random.default_rng(seed)
    games, plays, depth = [], [], []
    for si, season in enumerate(seasons):
        start = dt.datetime(season, 9, 10, 17, tzinfo=dt.UTC)
        for week in range(1, weeks + 1):
            pairs = PAIRINGS[(week - 1) % 3]
            for gi, (a, b) in enumerate(pairs):
                home, away = (a, b) if week % 2 else (b, a)
                h, v = LEAGUE_TEAMS[home], LEAGUE_TEAMS[away]
                kickoff = start + dt.timedelta(days=7 * (week - 1), hours=3 * gi)
                played = not (season == in_progress and (week > 5 or (week == 5 and gi == 1)))
                game_id = f"{season}_{week:02d}_{v}_{h}"
                qb = {t: f"QB_{t}_{2 if (t == 'ATL' and si >= 1) else 1}" for t in (h, v)}
                hs, vs = (
                    (int(rng.integers(10, 35)), int(rng.integers(10, 35)))
                    if played
                    else (
                        None,
                        None,
                    )
                )
                games.append(
                    {
                        "game_id": game_id,
                        "season": season,
                        "week": week,
                        "game_type": "REG",
                        "home_team": h,
                        "away_team": v,
                        "home_score": hs,
                        "away_score": vs,
                        "result": None if hs is None else hs - vs,
                        "neutral_site": False,
                        "completed": played,
                        "kickoff_utc": kickoff,
                        "home_qb_id": qb[h],
                        "away_qb_id": qb[v],
                        "home_qb_name": qb[h],
                        "away_qb_name": qb[v],
                    }
                )
                if not played:
                    continue
                for off, dfn in ((h, v), (v, h)):
                    for i in range(plays_per_side):
                        drop = i % 5 < 3
                        epa = (
                            TRUE_OFF[off]
                            + TRUE_DEF[dfn]
                            + (TRUE_HFA if off == h else 0.0)
                            + rng.normal(0, 0.5)
                        )
                        plays.append(
                            {
                                "game_id": game_id,
                                "play_id": float(i + 1),
                                "season": season,
                                "week": week,
                                "season_type": "REG",
                                "posteam": off,
                                "defteam": dfn,
                                "home_team": h,
                                "location": "Home",
                                "play_type": "pass" if drop else "run",
                                "two_point_attempt": 0.0,
                                "qb_dropback": 1.0 if drop else 0.0,
                                "epa": epa,
                                "success": 1.0 if epa > 0 else 0.0,
                                "wp": float(rng.uniform(0.01, 0.99)),
                            }
                        )
                # Rows the rating filters must drop.
                for ptype, extra in (
                    ("qb_kneel", {}),
                    ("no_play", {}),
                    ("pass", {"two_point_attempt": 1.0}),
                ):
                    row = {
                        "game_id": game_id,
                        "play_id": 999.0,
                        "season": season,
                        "week": week,
                        "season_type": "REG",
                        "posteam": h,
                        "defteam": v,
                        "home_team": h,
                        "location": "Home",
                        "play_type": ptype,
                        "two_point_attempt": 0.0,
                        "qb_dropback": 0.0,
                        "epa": 5.0,
                        "success": 1.0,
                        "wp": 0.5,
                    }
                    row.update(extra)
                    plays.append(row)
        for t in LEAGUE_TEAMS:
            qb = f"QB_{t}_{2 if (t == 'ATL' and si >= 1) else 1}"
            depth.append(
                {
                    "season": season,
                    "week": 1,
                    "team": t,
                    "position": "QB",
                    "depth_rank": 1,
                    "gsis_id": qb,
                }
            )
    games_df = pl.DataFrame(games).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )
    plays_df = pl.DataFrame(plays).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )
    depth_df = pl.DataFrame(depth).with_columns(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("depth_rank").cast(pl.Int32),
    )
    return {"games": games_df, "plays": plays_df, "depth_charts": depth_df}


@pytest.fixture(scope="session")
def league() -> dict[str, pl.DataFrame]:
    return make_league()
