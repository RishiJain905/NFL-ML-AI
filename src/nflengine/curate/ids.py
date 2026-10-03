"""Player ID crosswalk: every source joins to `gsis_id` (documentation/03).

nflverse `load_players` carries GSIS, PFR, ESPN, PFF, ESB, NFL and Smart IDs.
PFR-keyed tables (snap counts, PFR advanced stats) and ESPN tables (injuries,
QBR, news) get a `gsis_id` column, and the join rate is reported to the
data-quality checks.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

XWALK_COLS = ["gsis_id", "pfr_id", "espn_id", "pff_id", "esb_id", "nfl_id", "smart_id"]


def player_crosswalk(players: pl.DataFrame) -> pl.DataFrame:
    keep = [c for c in XWALK_COLS if c in players.columns]
    extra = [
        c
        for c in ("display_name", "position", "position_group", "birth_date", "latest_team")
        if c in players.columns
    ]
    return (
        players.select(keep + extra)
        .with_columns([pl.col(c).cast(pl.String) for c in keep])
        .filter(pl.col("gsis_id").is_not_null())
    )


@dataclass
class JoinRate:
    table: str
    key: str
    matched: int
    total: int

    @property
    def rate(self) -> float:
        return self.matched / self.total if self.total else 1.0


def attach_gsis(
    df: pl.DataFrame, key_col: str, xwalk: pl.DataFrame, xwalk_col: str, table: str
) -> tuple[pl.DataFrame, JoinRate]:
    """Left-join gsis_id onto df via df[key_col] == xwalk[xwalk_col]."""
    if key_col not in df.columns:
        return df, JoinRate(table, key_col, 0, 0)
    mapping = (
        xwalk.select(pl.col(xwalk_col).alias("__key"), pl.col("gsis_id").alias("__gsis"))
        .drop_nulls()
        .unique(subset="__key", keep="first")
    )
    out = df.with_columns(pl.col(key_col).cast(pl.String).alias("__key")).join(
        mapping, on="__key", how="left"
    )
    if "gsis_id" in out.columns:
        out = out.with_columns(pl.coalesce("gsis_id", "__gsis").alias("gsis_id"))
    else:
        out = out.rename({"__gsis": "gsis_id"})
    out = out.drop([c for c in ("__key", "__gsis") if c in out.columns])
    keyed = out.filter(pl.col(key_col).is_not_null())
    rate = JoinRate(table, key_col, keyed["gsis_id"].is_not_null().sum(), keyed.height)
    return out, rate
