"""Team code normalization: one canonical code per franchise (documentation/03).

Canonical codes are nflverse's current ones. Relocated franchises map to today's
code (OAK->LV, SD->LAC, STL->LA), so ratings and history follow the franchise.
Aliases cover other sources: NGS (LAR, ARZ, BLT, CLV, HST), ESPN (WSH, LAR),
PFR / draft data (GNB, KAN, NOR, NWE, SFO, TAM, LVR, RAI, RAM, SDG, PHO).
"""

from __future__ import annotations

from collections.abc import Iterable

import polars as pl

CANONICAL_TEAMS: tuple[str, ...] = (
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET",
    "GB", "HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO",
    "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
)  # fmt: skip

ALIASES: dict[str, str] = {
    # relocations
    "OAK": "LV", "RAI": "LV", "LVR": "LV",
    "SD": "LAC", "SDG": "LAC",
    "STL": "LA", "LAR": "LA", "RAM": "LA",
    # other sources' spellings
    "JAC": "JAX", "WSH": "WAS", "ARZ": "ARI", "PHO": "ARI", "BLT": "BAL", "CLV": "CLE",
    "HST": "HOU", "GNB": "GB", "KAN": "KC", "NOR": "NO", "NWE": "NE", "SFO": "SF",
    "TAM": "TB",
}  # fmt: skip

TEAM_MAP: dict[str, str] = {t: t for t in CANONICAL_TEAMS} | ALIASES


def normalize_team(code: str | None) -> str | None:
    if code is None:
        return None
    return TEAM_MAP.get(code.strip().upper(), code)


def team_expr(col: str) -> pl.Expr:
    """Map a team-code column to canonical codes; unknown values pass through unchanged."""
    return pl.col(col).str.strip_chars().str.to_uppercase().replace(TEAM_MAP).alias(col)


def normalize_team_cols(df: pl.DataFrame, cols: Iterable[str]) -> pl.DataFrame:
    cols = [c for c in cols if c in df.columns and df.schema[c] == pl.String]
    return df.with_columns([team_expr(c) for c in cols]) if cols else df


def team_like_columns(df: pl.DataFrame) -> list[str]:
    """String columns holding team codes in nflverse tables (posteam, *_team, ...)."""
    explicit = {"posteam", "defteam", "side_of_field", "team", "opponent", "team_abbr", "club_code"}
    return [
        c
        for c, dtype in df.schema.items()
        if dtype == pl.String
        and (c in explicit or c.endswith("_team") or c.endswith("team_abbr"))
        and not c.endswith("_name")
    ]


def unknown_codes(df: pl.DataFrame, cols: Iterable[str]) -> dict[str, list[str]]:
    out = {}
    for c in cols:
        if c in df.columns:
            bad = (
                df.select(pl.col(c).drop_nulls().unique())
                .filter(~pl.col(c).is_in(CANONICAL_TEAMS))[c]
                .to_list()
            )
            if bad:
                out[c] = sorted(bad)
    return out


def aliases_table() -> pl.DataFrame:
    return pl.DataFrame({"alias": list(TEAM_MAP), "team": list(TEAM_MAP.values())}).sort("alias")
