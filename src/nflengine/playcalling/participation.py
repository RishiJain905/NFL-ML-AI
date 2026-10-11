"""nflverse participation (research only, 2016-2025) parsed into play labels (PC00).

Participation is history: it is read from `{NFL_DATA_ROOT}/research/nflverse/participation`,
never by a live feature, and the current season's file only arrives after the season.

Two eras with different vocabularies (the lists live in `labels`):
- 2016-2022 (NGS): personnel counts RB, TE and WR always, other positions only when unusual
  ("6 OL, 1 RB, 2 TE, 1 WR", "2 QB, 1 RB, 1 TE, 2 WR", "1 RB, 2 TE, 1 WR,1 DL"; five OL are
  implied when no OL is listed); defense "4 DL, 2 LB, 5 DB". Plays without NGS charting have a
  null (or, 2016-2021, a blank '') personnel string.
- 2023-2025 (FTN): every position on the field is counted ("1 C, 2 G, 1 QB, 1 RB, 2 T, 1 TE,
  3 WR"), a position that isn't there isn't written (no "0 RB"); defense "3 CB, 2 DE, 2 DT,
  1 FS, 1 MLB, 1 OLB, 1 SS". Blanks are '' rather than null for route and man / zone.

How the strings are read (checked against all ten seasons on 2026-10-10):
- `personnel` is f"{RB + FB}{TE}". QBs and WRs don't enter it, so a 2-QB group
  "2 QB, 1 RB, 1 TE, 2 WR" is "11"; "0 RB, 1 TE, 4 WR" is "01"; a fullback counts as a back
  ("1 FB, 1 RB, 1 TE, 2 WR" is "21"). A defender lined up on offense (NGS "1 DL" as a blocker,
  FTN "1 CB" at receiver) is ignored.
- `extra_ol`: six or more offensive linemen (NGS "N OL", FTN C + G + T (+ OL)).
- `def_package` counts defensive backs: NGS "N DB", FTN CB + S + FS + SS + DB (+ SAF). A
  receiver playing defense (2025's two-way players, Hail Marys) isn't counted.
- Special teams: personnel, extra_ol and def_package are null on a play where either group has
  a K, P or LS (punts, field goals, kick returns, fakes) or the offense has 3+ defensive
  positions (kick units listed as the offense without a kicker).
- A string with a position code outside `KNOWN_POSITIONS` (none seen so far) is unparseable:
  null rather than a guess. Tokens may lack the space after the comma ("2 WR,1 DL").
- `part_box` 0 is null: FTN writes 0 on plays it didn't chart (~27.5k rows a year in 2023-2025,
  nearly all with no formation), and no real snap has an empty box.
- `part_rushers` 0 or more than 11 is null: FTN writes 0 on runs (NGS leaves them null) and has
  two impossible values (44, 45).
- Labels go through `labels.snake` ("HITCH/CURL" -> "hitch_curl", "UNDER CENTER" ->
  "under_center"); `man_zone` is "man" / "zone" from MAN_COVERAGE / ZONE_COVERAGE.
"""

from __future__ import annotations

from collections.abc import Iterable

import polars as pl

from nflengine.ingest.base import SnapshotStore
from nflengine.paths import DataPaths
from nflengine.playcalling.labels import snake

SOURCE, DATASET = "nflverse", "participation"

# Every position code seen in either era's personnel strings.
KNOWN_POSITIONS = (
    "QB", "RB", "FB", "TE", "WR", "OL", "C", "G", "T",  # offense
    "DL", "DE", "DT", "NT", "LB", "ILB", "OLB", "MLB", "DB", "CB", "S", "FS", "SS", "SAF",
    "K", "P", "LS",  # special teams
)  # fmt: skip
BACKS = ("RB", "FB")
OFFENSIVE_LINE = ("OL", "C", "G", "T")
DEFENSIVE_BACKS = ("DB", "CB", "S", "FS", "SS", "SAF")
DEFENSIVE_POSITIONS = ("DL", "DE", "DT", "NT", "LB", "ILB", "OLB", "MLB", *DEFENSIVE_BACKS)
SPECIAL_TEAMS = ("K", "P", "LS")
EXTRA_OL_MIN = 6
OFFENSE_MAX_DEFENDERS = 2  # 3+ defensive positions on offense = a kick unit, not an offense
NICKEL_DBS, DIME_DBS = 5, 6
MAX_PLAYERS = 11
MAN_ZONE = {"man_coverage": "man", "zone_coverage": "zone"}

OUTPUT_SCHEMA = {
    "game_id": pl.String,
    "play_id": pl.Float64,
    "formation": pl.String,
    "personnel": pl.String,
    "extra_ol": pl.Boolean,
    "def_package": pl.String,
    "coverage": pl.String,
    "man_zone": pl.String,
    "target_route": pl.String,
    "pressure": pl.Boolean,
    "time_to_throw": pl.Float64,
    "part_box": pl.Int32,
    "part_rushers": pl.Int32,
}
RAW_SCHEMA = {
    "nflverse_game_id": pl.String,
    "play_id": pl.Float64,
    "offense_formation": pl.String,
    "offense_personnel": pl.String,
    "defense_personnel": pl.String,
    "defense_coverage_type": pl.String,
    "defense_man_zone_type": pl.String,
    "route": pl.String,
    "was_pressure": pl.Boolean,
    "time_to_throw": pl.Float64,
    "defenders_in_box": pl.Int32,
    "number_of_pass_rushers": pl.Int32,
}


def load_participation(paths: DataPaths, seasons: Iterable[int]) -> pl.DataFrame:
    """The newest file of each requested season that exists, concatenated, with an Int32
    `season` column; an empty frame when none exist."""
    parts = SnapshotStore(paths.research).latest_parts(SOURCE, DATASET)
    frames = [
        pl.read_parquet(parts[f"season={s}"]).with_columns(season=pl.lit(s, dtype=pl.Int32))
        for s in sorted(set(seasons))
        if f"season={s}" in parts
    ]
    if not frames:
        return pl.DataFrame(schema={"season": pl.Int32})
    return pl.concat(frames, how="diagonal_relaxed")


def _count(col: str, positions: Iterable[str]) -> pl.Expr:
    """Players at `positions` in a personnel string ("1 C, 2 G, ..."); 0 when none listed."""
    alts = "|".join(positions)
    return (
        pl.col(col)
        .str.extract_all(rf"\d+\s*(?:{alts})\b")
        .list.eval(pl.element().str.extract(r"^(\d+)").cast(pl.Int32))
        .list.sum()
        .fill_null(0)
    )


def _has_any(col: str, positions: Iterable[str]) -> pl.Expr:
    return pl.col(col).str.contains(rf"\d+\s*(?:{'|'.join(positions)})\b").fill_null(False)


def _parseable(col: str) -> pl.Expr:
    """Non-blank and made only of "N POS" tokens with known position codes."""
    known = "|".join(KNOWN_POSITIONS)
    rest = pl.col(col).str.replace_all(rf"\d+\s*(?:{known})\b", "").str.replace_all(r"[\s,]", "")
    return (pl.col(col) != "") & (rest == "")


def _snake_map(series: pl.Series, mapping: dict[str, str] | None = None) -> dict[str, str | None]:
    """{raw label: snake label} over a column's distinct values ('' -> null)."""
    out: dict[str, str | None] = {}
    for v in series.drop_nulls().unique().to_list():
        s = snake(v)
        out[v] = (mapping.get(s) if mapping is not None else s) or None
    return out


def _labels(df: pl.DataFrame, col: str, mapping: dict[str, str] | None = None) -> pl.Expr:
    return pl.col(col).replace_strict(
        _snake_map(df[col], mapping), default=None, return_dtype=pl.String
    )


def parse_participation(raw: pl.DataFrame) -> pl.DataFrame:
    """One row per (game_id, play_id) with the play labels (OUTPUT_SCHEMA); see the module
    docstring for how each era's strings are read."""
    missing = [pl.lit(None, dtype=t).alias(c) for c, t in RAW_SCHEMA.items() if c not in raw]
    df = raw.with_columns(missing).with_columns(pl.col(c).cast(t) for c, t in RAW_SCHEMA.items())
    df = df.with_columns(
        pl.col("offense_personnel", "defense_personnel").str.strip_chars().str.to_uppercase()
    )
    off, dfn = "offense_personnel", "defense_personnel"
    special = (
        _has_any(off, SPECIAL_TEAMS)
        | _has_any(dfn, SPECIAL_TEAMS)
        | (_count(off, DEFENSIVE_POSITIONS) > OFFENSE_MAX_DEFENDERS)
    )
    off_ok = _parseable(off).fill_null(False) & ~special
    def_ok = _parseable(dfn).fill_null(False) & ~special
    backs, tes = _count(off, BACKS), _count(off, ("TE",))
    dbs = _count(dfn, DEFENSIVE_BACKS)
    out = df.select(
        pl.col("nflverse_game_id").alias("game_id"),
        pl.col("play_id"),
        _labels(df, "offense_formation").alias("formation"),
        pl.when(off_ok).then(backs.cast(pl.String) + tes.cast(pl.String)).alias("personnel"),
        pl.when(off_ok).then(_count(off, OFFENSIVE_LINE) >= EXTRA_OL_MIN).alias("extra_ol"),
        pl.when(def_ok)
        .then(
            pl.when(dbs >= DIME_DBS)
            .then(pl.lit("dime"))
            .when(dbs == NICKEL_DBS)
            .then(pl.lit("nickel"))
            .otherwise(pl.lit("base"))
        )
        .alias("def_package"),
        _labels(df, "defense_coverage_type").alias("coverage"),
        _labels(df, "defense_man_zone_type", MAN_ZONE).alias("man_zone"),
        _labels(df, "route").alias("target_route"),
        pl.col("was_pressure").alias("pressure"),
        pl.col("time_to_throw"),
        pl.when(pl.col("defenders_in_box") > 0).then(pl.col("defenders_in_box")).alias("part_box"),
        pl.when(pl.col("number_of_pass_rushers").is_between(1, MAX_PLAYERS))
        .then(pl.col("number_of_pass_rushers"))
        .alias("part_rushers"),
    )
    return (
        out.filter(pl.col("game_id").is_not_null() & pl.col("play_id").is_not_null())
        .unique(["game_id", "play_id"], keep="first", maintain_order=True)
        .cast(OUTPUT_SCHEMA)
    )
