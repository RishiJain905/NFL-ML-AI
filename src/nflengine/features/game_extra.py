"""Extra game features for the game model v1 (plan P08; documentation/04 -> B. Features).

Three families, each **as of the game's week** like the rest of `features/game.py`:

- **Injury load** (`team_injury_load`): snap-weighted absence of a team's regulars by
  position group, from the snap counts. It is the **Tuesday view**: a regular (this
  season, >= 50% of his side's snaps in at least `REGULAR_GAMES` of the team's games
  before its last one) who has no snaps on his side in the team's **last game** counts
  as absent, weighted by his average snap share. The week's injury report is not used:
  the live weekly run is on a Tuesday, before the report exists (D66 is the player
  model's "Friday view"; the game model keeps P03's Tuesday rule, leakage rule 4). A
  regular must also have been one in at least one of the team's 3 games before the last
  (a benched starter stops counting). QBs
  are left out (the QB status feature covers them). Groups: `ol`, `skill` (RB, WR, TE,
  FB), `front` (DL, LB), `secondary` (CB, S). A team's first two games of a season have
  no load yet (0).
- **Weather** (`game_weather`): coarse buckets, because training uses the **actual**
  game weather and a live run only has a **forecast** (the P08 pitfall): `wind_15`
  (outdoors and wind >= 15 mph, the 90th percentile of 2011-2025 outdoor games) and
  `cold_32` (outdoors and <= 32 F). Domes and closed roofs are 0; an outdoor game without
  a reading is null (2022 has half its outdoor readings missing). Live, unplayed games
  take the Open-Meteo forecast pulled at or before the run (`weather_forecasts`).
- **Trailing home edge** (`trailing_home_edge`): the league's mean home margin over
  non-neutral regular-season games of the previous 3 seasons. Home field has shrunk
  since 2020 (P03 follow-up); v0's single home-field weight can't follow it.

All three are pure (frames in, frame out); `features.game.load_game_inputs` reads the
tables and `build_game_features` joins the results.
"""

from __future__ import annotations

import polars as pl

GROUPS = {
    "ol": ("OL",),
    "skill": ("RB", "FB", "WR", "TE"),
    "front": ("DL", "LB"),
    "secondary": ("CB", "S"),
}
OFFENSE = ("OL", "RB", "FB", "WR", "TE", "QB")
POSITION_MAP = {
    "QB": "QB",
    "RB": "RB",
    "HB": "RB",
    "FB": "FB",
    "WR": "WR",
    "TE": "TE",
    "T": "OL",
    "OT": "OL",
    "LT": "OL",
    "RT": "OL",
    "G": "OL",
    "OG": "OL",
    "LG": "OL",
    "RG": "OL",
    "C": "OL",
    "OL": "OL",
    "DE": "DL",
    "DT": "DL",
    "NT": "DL",
    "DL": "DL",
    "EDGE": "DL",
    "LB": "LB",
    "ILB": "LB",
    "OLB": "LB",
    "MLB": "LB",
    "CB": "CB",
    "DB": "S",
    "S": "S",
    "SS": "S",
    "FS": "S",
    "SAF": "S",
}
REGULAR_SNAP = 0.5  # share of his side's snaps that makes a game a "regular" game
REGULAR_GAMES = 2  # ... at least this many of the team's earlier games this season
RECENT_GAMES = 3  # ... and at least once in the team's 3 games before its last one (a
# starter benched or gone for a month stops counting; the ratings carry that by then)
WIND_MPH = 15.0
COLD_F = 32.0
OUTDOOR = ("outdoors", "open")
HOME_EDGE_SEASONS = 3
INJURY_COLS = tuple(f"inj_{g}" for g in GROUPS)
WEATHER_COLS = ("wind_15", "cold_32")


def _t(season: pl.Expr | str = "season", week: pl.Expr | str = "week") -> pl.Expr:
    s = pl.col(season) if isinstance(season, str) else season
    w = pl.col(week) if isinstance(week, str) else week
    return ((s.cast(pl.Int64) - 2000) * 22 + w.cast(pl.Int64)).alias("t")


def position_group(position: pl.Expr) -> pl.Expr:
    """Snap-count position label -> group (`FB/D` -> its first label)."""
    first = position.str.split("/").list.first().str.strip_chars().str.to_uppercase()
    return first.replace_strict(POSITION_MAP, default=None, return_dtype=pl.String)


def team_injury_load(snaps: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """Rows (season, week, team, inj_ol, inj_skill, inj_front, inj_secondary) for every
    team game in `games`, from the snap counts of the team's earlier games that season.

    `snaps`: gsis_id, game_id, team, position, offense_pct, defense_pct (0-1).
    `games`: game_id, season, week, home_team, away_team (any game types).
    """
    sides = pl.concat(
        [
            games.select(
                "game_id",
                pl.col("season").cast(pl.Int32),
                pl.col("week").cast(pl.Int32),
                pl.col(f"{s}_team").alias("team"),
            )
            for s in ("home", "away")
        ]
    ).with_columns(_t())
    sides = sides.sort("team", "t").with_columns(
        pl.int_range(1, pl.len() + 1).over("team", "season").cast(pl.Int32).alias("k")
    )
    empty = sides.select("season", "week", "team").with_columns(
        [pl.lit(0.0).alias(c) for c in INJURY_COLS]
    )
    if snaps.is_empty():
        return empty
    s = (
        snaps.filter(pl.col("gsis_id").is_not_null())
        .with_columns(position_group(pl.col("position")).alias("grp"))
        .filter(pl.col("grp").is_not_null() & (pl.col("grp") != "QB"))
        .with_columns(
            pl.when(pl.col("grp").is_in(list(OFFENSE)))
            .then(pl.col("offense_pct"))
            .otherwise(pl.col("defense_pct"))
            .fill_null(0.0)
            .alias("share")
        )
        .group_by("gsis_id", "game_id", "team")
        .agg(pl.col("grp").first(), pl.col("share").max())
    )
    # sorted, so every mean / sum below adds in the same order (group_by's group order
    # varies between runs; floats summed in another order differ at ~1e-17)
    played = s.join(sides.select("game_id", "team", "season", "k"), on=["game_id", "team"]).sort(
        "season", "team", "gsis_id", "k"
    )
    # for game k: regulars from games 1..k-2 of the season, absent in game k-1
    rows = []
    for k_last in range(1, int(sides["k"].max() or 0)):
        before = played.filter(pl.col("k") < k_last)
        if before.is_empty():
            continue
        recent = pl.col("k") >= k_last - RECENT_GAMES
        reg = (
            before.group_by("season", "team", "gsis_id", maintain_order=True)
            .agg(
                (pl.col("share") >= REGULAR_SNAP).sum().alias("_n_reg"),
                ((pl.col("share") >= REGULAR_SNAP) & recent).sum().alias("_n_recent"),
                pl.col("share").filter(pl.col("share") > 0).mean().alias("_w"),
                pl.col("grp").last(),
            )
            .filter((pl.col("_n_reg") >= REGULAR_GAMES) & (pl.col("_n_recent") >= 1))
        )
        last = played.filter(pl.col("k") == k_last).select(
            "season", "team", "gsis_id", pl.col("share").alias("_last")
        )
        out = reg.join(last, on=["season", "team", "gsis_id"], how="left").filter(
            pl.col("_last").fill_null(0.0) <= 0.0
        )
        rows.append(out.with_columns(pl.lit(k_last + 1).cast(pl.Int32).alias("k")))
    if not rows:
        return empty
    absent = pl.concat(rows).sort("season", "team", "k", "gsis_id")
    load = absent.group_by("season", "team", "k", maintain_order=True).agg(
        [
            pl.col("_w").filter(pl.col("grp").is_in(list(members))).sum().alias(f"inj_{g}")
            for g, members in GROUPS.items()
        ]
    )
    out = sides.join(load, on=["season", "team", "k"], how="left").select(
        "season", "week", "team", *[pl.col(c).fill_null(0.0) for c in INJURY_COLS]
    )
    return out.unique(["season", "week", "team"], keep="first")


def game_weather(
    games: pl.DataFrame,
    forecasts: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Rows (game_id, wind_15, cold_32): actual weather for played games, the latest
    forecast (already filtered to what the run could see) for unplayed ones."""
    g = games.select(
        "game_id",
        "roof",
        pl.col("completed").fill_null(False),
        pl.col("wind").cast(pl.Float64).alias("_wind"),
        pl.col("temp").cast(pl.Float64).alias("_temp"),
    )
    if forecasts is not None and not forecasts.is_empty():
        fc = (
            forecasts.sort("pulled_at")
            .group_by("game_id")
            .agg(
                pl.col("wind_mph").last().cast(pl.Float64).alias("_fwind"),
                pl.col("temp_f").last().cast(pl.Float64).alias("_ftemp"),
            )
        )
        g = g.join(fc, on="game_id", how="left").with_columns(
            pl.when(pl.col("completed"))
            .then(pl.col("_wind"))
            .otherwise(pl.coalesce("_fwind", "_wind"))
            .alias("_wind"),
            pl.when(pl.col("completed"))
            .then(pl.col("_temp"))
            .otherwise(pl.coalesce("_ftemp", "_temp"))
            .alias("_temp"),
        )
    outdoor = pl.col("roof").is_in(list(OUTDOOR)) | pl.col("roof").is_null()
    return g.select(
        "game_id",
        pl.when(~outdoor)
        .then(0.0)
        .when(pl.col("_wind").is_not_null())
        .then((pl.col("_wind") >= WIND_MPH).cast(pl.Float64))
        .alias("wind_15"),
        pl.when(~outdoor)
        .then(0.0)
        .when(pl.col("_temp").is_not_null())
        .then((pl.col("_temp") <= COLD_F).cast(pl.Float64))
        .alias("cold_32"),
    )


def trailing_home_edge(games: pl.DataFrame, seasons: int = HOME_EDGE_SEASONS) -> pl.DataFrame:
    """Rows (season, home_edge): mean home margin of non-neutral regular-season games in
    the `seasons` seasons before it (known before the season starts)."""
    played = games.filter(
        (pl.col("game_type") == "REG")
        & ~pl.col("neutral_site").fill_null(False)
        & pl.col("home_score").is_not_null()
        & pl.col("away_score").is_not_null()
        & pl.col("completed").fill_null(False)
    )
    per = played.group_by("season").agg(
        (pl.col("home_score") - pl.col("away_score")).cast(pl.Float64).sum().alias("_m"),
        pl.len().alias("_n"),
    )
    all_seasons = sorted(games["season"].unique().to_list())
    out = []
    for s in all_seasons:
        prior = per.filter((pl.col("season") < s) & (pl.col("season") >= s - seasons))
        if prior.is_empty():
            continue
        out.append({"season": int(s), "home_edge": float(prior["_m"].sum() / prior["_n"].sum())})
    if not out:
        return pl.DataFrame(schema={"season": pl.Int32, "home_edge": pl.Float64})
    return pl.DataFrame(out).with_columns(pl.col("season").cast(pl.Int32))
