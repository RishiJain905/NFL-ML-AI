"""Schedule helpers: which weeks are complete, which is next (regular season + playoffs).

P07 extends this into the full calendar module (deadlines, special slates).
nflverse `week` runs 1-18 for the regular season and 19-22 for the playoffs.
"""

from __future__ import annotations

import polars as pl


def season_games(schedules: pl.DataFrame, season: int) -> pl.DataFrame:
    return schedules.filter(pl.col("season") == season)


def week_completion(schedules: pl.DataFrame, season: int) -> pl.DataFrame:
    """One row per week: games, completed games, and whether the week is complete."""
    return (
        season_games(schedules, season)
        .group_by("week")
        .agg(
            pl.len().alias("games"),
            pl.col("result").is_not_null().sum().alias("completed"),
            pl.col("gameday").min().alias("first_gameday"),
            pl.col("gameday").max().alias("last_gameday"),
        )
        .with_columns((pl.col("games") == pl.col("completed")).alias("is_complete"))
        .sort("week")
    )


def completed_weeks(schedules: pl.DataFrame, season: int) -> list[int]:
    wc = week_completion(schedules, season)
    return wc.filter(pl.col("is_complete"))["week"].to_list()


def last_completed_week(schedules: pl.DataFrame, season: int) -> int | None:
    weeks = completed_weeks(schedules, season)
    return max(weeks) if weeks else None


def next_week(schedules: pl.DataFrame, season: int) -> int | None:
    """First week with at least one game not yet played (the week being predicted)."""
    wc = week_completion(schedules, season).filter(~pl.col("is_complete"))
    return int(wc["week"].min()) if wc.height else None


def games_in_week(schedules: pl.DataFrame, season: int, week: int) -> pl.DataFrame:
    return season_games(schedules, season).filter(pl.col("week") == week)
