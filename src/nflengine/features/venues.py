"""Venue and travel features (documentation/04 -> B. Game model -> Features).

For every game: how far each team travels from its home venue to the game venue, and how
many time zones it crosses. Everything comes from `config/stadiums.yaml` (coordinates and an
IANA time zone per stadium) plus the schedule, so it needs no network and no tracking data.

Conventions:
- A team's **home venue** in a season is the stadium where it played most of its non-neutral
  home games that season (every scheduled game counts, played or not: the schedule is public
  before the season starts, so this is not leakage). Ties go to the stadium of the latest such
  game. So LV is at OAK00 through 2019 and at VEG00 from 2020, and a home team at its own
  stadium travels 0. At a neutral site both teams travel.
- **Travel** = great-circle km from the team's home venue to the game venue.
- **Time-zone shift** = UTC offset of the venue minus UTC offset of the team's home venue,
  in hours, evaluated at the game's kickoff instant (DST-aware: Phoenix has none). It is
  signed: an LA team in New York in October is +3.0 (it plays "later" on its body clock), a
  New York team in LA is -3.0. The raw difference is wrapped into (-12, +12] so a trip is
  never counted the long way round: an LA team in Melbourne in September is -7.0, not +17.0
  (an exact 12-hour difference maps to +12, never -12).
- **Venue** = the stadium the game was played in. It resolves by stadium *name* first, then
  `stadium_id` (nflverse can keep the home team's id for a game played abroad, see the header
  of `stadiums.yaml`), after the `game_venues` corrections in the YAML. A game whose venue is
  still unknown falls back to the home team's home venue and is flagged `venue_fallback`.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import yaml

from nflengine.settings import CONFIG_DIR

EARTH_RADIUS_KM = 6371.0088  # IUGG mean radius
STADIUMS_PATH = CONFIG_DIR / "stadiums.yaml"

TRAVEL_COLUMNS = [
    "game_id",
    "venue_stadium_id",
    "home_travel_km",
    "away_travel_km",
    "home_tz_shift",
    "away_tz_shift",
    "venue_fallback",
]


def _norm_name(name: str) -> str:
    """Same normalisation as `ingest.weather` uses to match stadium names."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _read_config(path: Path | None) -> dict:
    return yaml.safe_load((path or STADIUMS_PATH).read_text(encoding="utf-8"))


# ---- reference data ------------------------------------------------------------------------
def load_venues(path: Path | None = None) -> pl.DataFrame:
    """One row per stadium in `stadiums.yaml`: `stadium_id, name, lat, lon, tz, aliases`.

    `aliases` (list of other names the schedules use) feeds the name match in `game_travel`.
    Raises if an entry has no `tz` or an unknown one, so a bad YAML fails here and not as
    silent nulls in a feature table.
    """
    rows = []
    for sid, s in _read_config(path)["stadiums"].items():
        if not s.get("tz"):
            raise ValueError(f"stadium {sid} has no `tz` in stadiums.yaml")
        ZoneInfo(s["tz"])  # raises ZoneInfoNotFoundError for a typo
        rows.append(
            {
                "stadium_id": sid,
                "name": s["name"],
                "lat": float(s["lat"]),
                "lon": float(s["lon"]),
                "tz": s["tz"],
                "aliases": list(s.get("aliases") or []),
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "stadium_id": pl.String,
            "name": pl.String,
            "lat": pl.Float64,
            "lon": pl.Float64,
            "tz": pl.String,
            "aliases": pl.List(pl.String),
        },
    )


def load_game_venues(path: Path | None = None) -> dict[str, str]:
    """`game_id -> stadium_id` corrections from the `game_venues` section of the YAML."""
    return dict(_read_config(path).get("game_venues") or {})


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km between points given in degrees.

    Works on scalars or equal-length arrays (numpy broadcasting). NaN in, NaN out.
    """
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


# ---- venue resolution ------------------------------------------------------------------------
def _labeled_games(
    games: pl.DataFrame, venues: pl.DataFrame, overrides: dict[str, str]
) -> pl.DataFrame:
    """The columns the venue logic needs, with kickoff in UTC and two venue columns.

    `venue_id`: the stadium_id the game resolves to (null when unknown to the YAML).
    `venue_label`: `venue_id`, or the raw nflverse `stadium_id` when it is unknown, so an
    unknown home stadium still identifies itself.
    """
    known = venues["stadium_id"].to_list()
    by_name: dict[str, str] = {}
    for row in venues.iter_rows(named=True):
        for n in [row["name"], *(row.get("aliases") or [])]:
            by_name[_norm_name(n)] = row["stadium_id"]

    steps: list[pl.Expr] = []
    if overrides:
        steps.append(
            pl.col("game_id").replace_strict(overrides, default=None, return_dtype=pl.String)
        )
    if "stadium" in games.columns:
        hits = {raw: by_name.get(_norm_name(raw)) for raw in games["stadium"].drop_nulls().unique()}
        hits = {raw: sid for raw, sid in hits.items() if sid}
        if hits:
            steps.append(
                pl.col("stadium").replace_strict(hits, default=None, return_dtype=pl.String)
            )
    steps.append(pl.when(pl.col("stadium_id").is_in(known)).then(pl.col("stadium_id")))

    tz = games.schema["kickoff_utc"].time_zone
    kickoff = pl.col("kickoff_utc")
    kickoff = (
        kickoff.dt.replace_time_zone("UTC") if tz is None else kickoff.dt.convert_time_zone("UTC")
    )
    return games.select(
        "game_id",
        "season",
        "home_team",
        "away_team",
        pl.col("neutral_site").fill_null(False),
        kickoff.alias("kickoff_utc"),
        pl.coalesce(steps).alias("venue_id"),
        pl.col("stadium_id").alias("raw_stadium_id"),
    ).with_columns(pl.coalesce("venue_id", "raw_stadium_id").alias("venue_label"))


def _home_venues(lab: pl.DataFrame) -> pl.DataFrame:
    """`season, team, home_stadium_id` for every team that appears in the games that season."""
    seasons = pl.concat(
        [
            lab.select("season", pl.col("home_team").alias("team")),
            lab.select("season", pl.col("away_team").alias("team")),
        ]
    ).unique()
    # Rank each venue a team hosted at: non-neutral games first (a team with none that season
    # falls back to its neutral-site home games), then most games, then a venue the YAML knows
    # over one it doesn't, then the latest game.
    best = (
        lab.group_by(
            "season", pl.col("home_team").alias("team"), pl.col("venue_label").alias("venue")
        )
        .agg(
            (~pl.col("neutral_site")).sum().alias("n_home"),
            pl.len().alias("n_all"),
            pl.col("venue_id").is_not_null().first().alias("known"),
            pl.col("kickoff_utc").max().alias("last"),
        )
        .with_columns(
            (pl.col("n_home") > 0).alias("has_home"),
            pl.when(pl.col("n_home") > 0).then("n_home").otherwise("n_all").alias("n"),
        )
        .sort(["has_home", "n", "known", "last"], descending=True)
        .unique(["season", "team"], keep="first", maintain_order=True)
        .select("season", "team", pl.col("venue").alias("home_stadium_id"))
    )
    # A team with no home game at all that season (only possible for a partial schedule)
    # keeps its previous venue.
    return (
        seasons.join(best, on=["season", "team"], how="left")
        .sort("team", "season")
        .with_columns(pl.col("home_stadium_id").forward_fill().over("team"))
        .sort("season", "team")
    )


# Time zones of US venues (`stadiums.yaml` -> tz); a venue in any other zone is abroad.
US_ZONES = frozenset(
    {
        "America/New_York",
        "America/Detroit",
        "America/Indiana/Indianapolis",
        "America/Kentucky/Louisville",
        "America/Chicago",
        "America/Denver",
        "America/Phoenix",
        "America/Los_Angeles",
        "America/Anchorage",
        "Pacific/Honolulu",
    }
)


def abroad_flags(
    games: pl.DataFrame,
    venues: pl.DataFrame | None = None,
    overrides: dict[str, str] | None = None,
) -> pl.DataFrame:
    """`game_id, venue_stadium_id, abroad`: the venue each game resolves to (the
    `game_venues` corrections, then the stadium name, then the id) and whether it lies
    outside the US. A venue the YAML doesn't know isn't called abroad (it can't be told)."""
    venues = load_venues() if venues is None else venues
    overrides = load_game_venues() if overrides is None else overrides
    if "neutral_site" not in games.columns:
        games = games.with_columns(pl.lit(False).alias("neutral_site"))
    lab = _labeled_games(games, venues, overrides)
    tz = dict(zip(venues["stadium_id"], venues["tz"], strict=True))
    zone = pl.col("venue_id").replace_strict(tz, default=None, return_dtype=pl.String)
    return lab.select(
        "game_id",
        pl.col("venue_id").alias("venue_stadium_id"),
        (zone.is_not_null() & ~zone.is_in(sorted(US_ZONES))).alias("abroad"),
    )


def with_neutral_rule(
    games: pl.DataFrame,
    venues: pl.DataFrame | None = None,
    overrides: dict[str, str] | None = None,
) -> pl.DataFrame:
    """Set `neutral_site` = nflverse's `location == "Neutral"` **or** a venue abroad (D99).

    nflverse marks some games abroad as the home team's game (2026 PHI@JAX at Tottenham, the
    Bills' Toronto games in 2010-12); no team has home-field advantage there, so Elo, the
    ratings and the models must see them as neutral. Without a `location` column the existing
    `neutral_site` is the base. Row order is kept."""
    base = (
        pl.col("location") == "Neutral" if "location" in games.columns else pl.col("neutral_site")
    )
    g = games.with_columns(base.fill_null(False).alias("neutral_site"))
    ab = abroad_flags(g, venues, overrides).select("game_id", pl.col("abroad").alias("_abroad"))
    return (
        g.join(ab, on="game_id", how="left", maintain_order="left")
        .with_columns(
            (pl.col("neutral_site") | pl.col("_abroad").fill_null(False)).alias("neutral_site")
        )
        .drop("_abroad")
    )


def team_home_venues(
    games: pl.DataFrame,
    venues: pl.DataFrame | None = None,
    overrides: dict[str, str] | None = None,
) -> pl.DataFrame:
    """`season, team, home_stadium_id` for every team that appears in `games` that season.

    The home venue is where the team played most of its non-neutral home games (ties: a
    stadium the YAML knows, then the latest such game); played or not, since the schedule is
    known in advance. `venues` and `overrides` default to `stadiums.yaml` (pass `{}` to
    switch the corrections off).
    """
    venues = load_venues() if venues is None else venues
    overrides = load_game_venues() if overrides is None else overrides
    return _home_venues(_labeled_games(games, venues, overrides))


# ---- time zones ------------------------------------------------------------------------------
def _wrap_hours(raw: pl.Series) -> pl.Series:
    """Wrap an hour difference into (-12, +12]: 17 -> -7, -13 -> 11, 12 -> 12, -12 -> 12."""
    return 12 - ((12 - raw) % 24)


def _utc_offsets(pairs: pl.DataFrame) -> pl.DataFrame:
    """Add `offset_h` (UTC offset in hours at that instant) to distinct (tz, kickoff_utc) rows."""
    zones: dict[str, ZoneInfo] = {}
    hours = [
        ts.astimezone(zones.setdefault(tz, ZoneInfo(tz))).utcoffset().total_seconds() / 3600
        for tz, ts in pairs.iter_rows()
    ]
    return pairs.with_columns(pl.Series("offset_h", hours, dtype=pl.Float64))


# ---- the feature table -----------------------------------------------------------------------
def game_travel(
    games: pl.DataFrame,
    venues: pl.DataFrame | None = None,
    overrides: dict[str, str] | None = None,
) -> pl.DataFrame:
    """One row per game: how far each team travels and how many time zones it crosses.

    Columns: `game_id, venue_stadium_id, home_travel_km, away_travel_km, home_tz_shift,
    away_tz_shift, venue_fallback` (same row order as `games`). Distances and shifts are
    Float64 and measured from each team's home venue in that season (`team_home_venues`) to
    the game venue; the shift is signed, DST-aware at kickoff and wrapped into (-12, +12]
    (see the module docstring).

    `games` needs `game_id, season, home_team, away_team, neutral_site, stadium_id,
    kickoff_utc` and optionally `stadium` (matched by name before the id). `venues` and
    `overrides` default to `stadiums.yaml`. A distance or shift is null only when a venue
    involved is unknown to the YAML (a new stadium) or `kickoff_utc` is null.
    """
    venues = load_venues() if venues is None else venues
    overrides = load_game_venues() if overrides is None else overrides
    lab = _labeled_games(games, venues, overrides)
    homes = _home_venues(lab)

    def home_of(side: str) -> pl.DataFrame:
        return homes.rename({"team": f"{side}_team", "home_stadium_id": f"{side}_home"})

    def place(prefix: str) -> pl.DataFrame:
        return venues.select(
            pl.col("stadium_id").alias(prefix),
            pl.col("lat").alias(f"{prefix}_lat"),
            pl.col("lon").alias(f"{prefix}_lon"),
            pl.col("tz").alias(f"{prefix}_tz"),
        )

    df = (
        lab.select("game_id", "season", "home_team", "away_team", "kickoff_utc", "venue_id")
        .join(home_of("home"), on=["season", "home_team"], how="left", maintain_order="left")
        .join(home_of("away"), on=["season", "away_team"], how="left", maintain_order="left")
        .with_columns(
            pl.col("venue_id").is_null().alias("venue_fallback"),
            pl.coalesce("venue_id", "home_home").alias("venue_stadium_id"),
        )
        .join(
            place("v"), left_on="venue_stadium_id", right_on="v", how="left", maintain_order="left"
        )
        .join(place("h"), left_on="home_home", right_on="h", how="left", maintain_order="left")
        .join(place("a"), left_on="away_home", right_on="a", how="left", maintain_order="left")
    )

    def travel(prefix: str, name: str) -> pl.Series:
        km = haversine_km(
            df[f"{prefix}_lat"].to_numpy(),
            df[f"{prefix}_lon"].to_numpy(),
            df["v_lat"].to_numpy(),
            df["v_lon"].to_numpy(),
        )
        return pl.Series(name, km, nan_to_null=True)

    # UTC offset of the venue and of each team's home venue at the kickoff instant.
    pairs = (
        pl.concat(
            [df.select(pl.col(f"{p}_tz").alias("tz"), "kickoff_utc") for p in ("v", "h", "a")]
        )
        .drop_nulls()
        .unique()
    )
    offsets = _utc_offsets(pairs)

    def offset(prefix: str) -> pl.Series:
        return df.select("kickoff_utc", pl.col(f"{prefix}_tz").alias("tz")).join(
            offsets, on=["tz", "kickoff_utc"], how="left", maintain_order="left"
        )["offset_h"]

    venue_off = offset("v")
    return df.select(
        "game_id",
        "venue_stadium_id",
        travel("h", "home_travel_km"),
        travel("a", "away_travel_km"),
        _wrap_hours(venue_off - offset("h")).alias("home_tz_shift"),
        _wrap_hours(venue_off - offset("a")).alias("away_tz_shift"),
        "venue_fallback",
    )
