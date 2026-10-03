"""Turn The Odds API bookmaker rows into canonical `lines` rows (D35, D40).

Odds API spreads are quoted per team with the favourite NEGATIVE (like ESPN), so the
canonical `home_spread` (+ = home favoured) is `-spreads_home_point`. Games are matched
on canonical home/away team plus kickoff within 36 hours.
"""

from __future__ import annotations

import polars as pl


def _col(df: pl.DataFrame, name: str, dtype: pl.DataType = pl.Float64) -> pl.Expr:
    return pl.col(name).cast(dtype) if name in df.columns else pl.lit(None, dtype=dtype)


def odds_api_lines(
    odds: pl.DataFrame, games: pl.DataFrame, team_names: pl.DataFrame
) -> tuple[pl.DataFrame, int, int]:
    """Returns (lines rows, matched events, total events).

    `team_names`: columns team_name (full name, e.g. "Kansas City Chiefs") and team (code).
    """
    names = team_names.select("team_name", "team").unique(subset="team_name")
    o = (
        odds.join(
            names.rename({"team_name": "home_team_name", "team": "home_team"}),
            on="home_team_name",
            how="left",
        )
        .join(
            names.rename({"team_name": "away_team_name", "team": "away_team"}),
            on="away_team_name",
            how="left",
        )
        .with_columns(
            pl.col("commence_time")
            .str.to_datetime("%Y-%m-%dT%H:%M:%SZ", time_zone="UTC", strict=False)
            .alias("commence_utc")
        )
    )
    g = games.select("game_id", "home_team", "away_team", "kickoff_utc")
    matched = o.join(g, on=["home_team", "away_team"], how="inner").filter(
        (pl.col("commence_utc") - pl.col("kickoff_utc")).abs() <= pl.duration(hours=36)
    )
    rows = matched.select(
        "game_id",
        pl.lit("odds_api").alias("source"),
        pl.col("bookmaker").alias("provider"),
        (-_col(matched, "spreads_home_point")).alias("home_spread"),
        _col(matched, "totals_over_point").alias("total"),
        _col(matched, "h2h_home_price").alias("home_moneyline"),
        _col(matched, "h2h_away_price").alias("away_moneyline"),
        pl.lit(False).alias("is_closing"),
    )
    total_events = odds["odds_event_id"].n_unique() if odds.height else 0
    matched_events = matched["odds_event_id"].n_unique() if matched.height else 0
    return rows, matched_events, total_events
