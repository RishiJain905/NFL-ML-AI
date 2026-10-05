"""Player-game data for the player model (plan P06; documentation/04 -> C, 11).

Everything the player features read, loaded once (`load_inputs`), plus the **player-game
history table** (`player_history`): one row per (player, regular-season game) he took part
in, from the box score (`player_games`) merged with the snap counts (a blocking TE has snaps
and no box row; the reverse happens too), PFR pressures and play-by-play extras (dropbacks,
EPA per dropback, red-zone looks).

Conventions:
- **Time index** `t = (season - 2000) * 22 + week`, continuous across seasons (as in
  `features/qb.py`). A feature for a row at `t` may only use history rows with `t_hist < t`
  (`asof_join`); a source that publishes a week late (PFR, FTN; D44) only rows with
  `t_hist < t - 1` (`asof_join(..., lag=1)`).
- **Position group** (`pgroup`): QB, RB, FB, WR, TE, OL, DL, LB, CB, S, SPEC. The box
  score's `position_group` splits DB into CB / S by position; rows with only a snap row
  map the snap position. nflverse labels many edge rushers LB (doc 03, P04 quirks), so
  the pass-rusher pool is DL + LB (`TARGET_GROUPS`).
- **Played:** offense groups need offensive snaps > 0, defense groups defensive snaps > 0;
  without a snap row (rare since 2013) a box-score stat stands in. Only played games are
  targets (documentation/11: scored only when the player plays).
- **Main QB:** the QB with the most dropbacks for his team in the game (ties: more
  attempts, then id). Only main-QB games are QB targets.
- **Pressures** come from PFR (2018+); a defender who played with no PFR row had none,
  but a team-game PFR hasn't published yet stays null (not scorable yet).
- **P08 labels:** `any_td` (a rushing or receiving TD), `def_int_any` (an interception),
  `pd_any` (a pass defended), all 0.0 / 1.0, and `pfr_completions_allowed` (PFR coverage:
  zero-filled like the other PFR columns, null for a team-game PFR hasn't published).
- **One row per (player, week):** rows without a player id are dropped, and a traded
  player listed by both teams in one week keeps the row where he played the most snaps.
- `games.kickoff_utc` is converted to UTC (DuckDB returns it in the machine's time zone).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass, field, replace

import duckdb
import polars as pl

from nflengine.paths import DataPaths, ensure_data_root

FIRST_SEASON = 2012  # one season before the first snap counts (2013) for last-season stats
TRAIN_START = 2013  # first season with snap counts: first training rows
PFR_START = 2018
RED_ZONE = 20  # yardline_100 <= 20

OFFENSE_GROUPS = ("QB", "RB", "FB", "WR", "TE", "OL")
DEFENSE_GROUPS = ("DL", "LB", "CB", "S")

# snap / roster position -> pgroup (for rows without a box-score position group)
POSITION_MAP = {
    "QB": "QB",
    "RB": "RB",
    "HB": "RB",
    "FB": "FB",
    "WR": "WR",
    "TE": "TE",
    "T": "OL",
    "OT": "OL",
    "G": "OL",
    "OG": "OL",
    "C": "OL",
    "OL": "OL",
    "LS": "SPEC",
    "K": "SPEC",
    "P": "SPEC",
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
    "S": "S",
    "SS": "S",
    "FS": "S",
    "SAF": "S",
    "DB": "S",
}

GAME_COLS = [
    "game_id",
    "season",
    "week",
    "game_type",
    "kickoff_utc",
    "home_team",
    "away_team",
    "completed",
    "neutral_site",
    "div_game",
    "roof",
]
PG_COLS = [
    "player_id",
    "player_display_name",
    "position",
    "position_group",
    "season",
    "week",
    "game_id",
    "team",
    "opponent_team",
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "sacks_suffered",
    "passing_air_yards",
    "passing_epa",
    "passing_cpoe",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "rushing_epa",
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "receiving_air_yards",
    "receiving_yards_after_catch",
    "receiving_epa",
    "target_share",
    "air_yards_share",
    "wopr",
    "def_tackles_solo",
    "def_tackle_assists",
    "def_tackles_for_loss",
    "def_sacks",
    "def_qb_hits",
    "def_interceptions",
    "def_pass_defended",
]
PLAY_COLS = [
    "game_id",
    "play_id",
    "season",
    "week",
    "season_type",
    "posteam",
    "defteam",
    "play_type",
    "pass",
    "rush",
    "qb_dropback",
    "qb_scramble",
    "sack",
    "two_point_attempt",
    "passer_id",
    "rusher_id",
    "receiver_id",
    "passer_player_id",
    "receiver_player_id",
    "rusher_player_id",
    "qb_epa",
    "epa",
    "success",
    "air_yards",
    "yards_gained",
    "yardline_100",
    "xpass",
    "pass_oe",
    "game_seconds_remaining",
    "wp",
    "down",
    "ydstogo",
    "no_huddle",
    "shotgun",
]


def tkey(season: str | pl.Expr = "season", week: str | pl.Expr = "week") -> pl.Expr:
    """Continuous week index `(season - 2000) * 22 + week` (Int32)."""
    s = pl.col(season) if isinstance(season, str) else season
    w = pl.col(week) if isinstance(week, str) else week
    return ((s.cast(pl.Int32) - 2000) * 22 + w.cast(pl.Int32)).cast(pl.Int32)


def asof_join(
    rows: pl.DataFrame,
    hist: pl.DataFrame,
    by: str | Sequence[str],
    *,
    lag: int = 0,
    suffix: str = "_h",
) -> pl.DataFrame:
    """For every row, the latest `hist` row (same `by`) strictly before it in time.

    Both frames need a `t` column (`tkey`). With `lag=k` only history rows with
    `t_hist < t - k` count (a source published k weeks late). `hist`'s own `t` comes back
    as `t_hist`; its other columns keep their names (colliding names get `suffix`).
    Row order and count of `rows` are kept.
    """
    by = [by] if isinstance(by, str) else list(by)
    h = hist.with_columns((pl.col("t") + lag + 1).alias("_t_avail"), pl.col("t").alias("t_hist"))
    h = h.drop("t").sort("_t_avail")
    r = rows.with_row_index("_ri").sort("t")
    out = r.join_asof(
        h,
        left_on="t",
        right_on="_t_avail",
        by=by,
        strategy="backward",
        suffix=suffix,
        check_sortedness=False,
    )
    return out.sort("_ri").drop("_ri", "_t_avail")


@dataclass
class PlayerInputs:
    """Curated frames the player features read (REG season only, `FIRST_SEASON` on)."""

    games: pl.DataFrame
    player_games: pl.DataFrame
    snaps: pl.DataFrame
    plays: pl.DataFrame
    pfr_def: pl.DataFrame
    pfr_pass: pl.DataFrame
    pfr_rush: pl.DataFrame
    pfr_rec: pl.DataFrame
    ngs_passing: pl.DataFrame
    ngs_receiving: pl.DataFrame
    ngs_rushing: pl.DataFrame
    ftn: pl.DataFrame
    injuries: pl.DataFrame
    rosters: pl.DataFrame
    team_ratings: pl.DataFrame
    game_features: pl.DataFrame
    game_context: pl.DataFrame = field(default_factory=pl.DataFrame)  # P03 predictions
    first_season: int = FIRST_SEASON
    last_season: int = 0


def _read(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> pl.DataFrame:
    return con.execute(sql, params or []).pl()


def _exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
        ).fetchone()[0]
    )


def load_inputs(
    paths: DataPaths | None = None,
    first_season: int = FIRST_SEASON,
    last_season: int | None = None,
    log: Callable[[str], None] = print,
    playoffs: bool = False,
) -> PlayerInputs:
    """Read every curated table the player features need (read-only DuckDB). Regular season
    only; `playoffs=True` (P10, scoring only: `score_weeks`, the watch-list look-back) also
    reads the playoff games, so saved playoff projections can be graded. Features and
    training never set it."""
    paths = paths or ensure_data_root()
    cur = paths.curated
    stype = "season_type IN ('REG', 'POST')" if playoffs else "season_type = 'REG'"
    gtype = "game_type <> 'SBBYE'" if playoffs else "game_type = 'REG'"
    with closing(duckdb.connect(str(cur / "nfl.duckdb"), read_only=True)) as con:
        last = last_season or int(_read(con, "SELECT max(season) s FROM games")["s"][0])
        rng = [first_season, last]
        games = _read(
            con,
            f"SELECT {', '.join(GAME_COLS)} FROM games WHERE {gtype} AND season BETWEEN ? AND ?",
            rng,
        )
        reg = f"{stype} AND season BETWEEN ? AND ?"
        greg = f"{gtype} AND season BETWEEN ? AND ?"
        player_games = _read(con, f"SELECT {', '.join(PG_COLS)} FROM player_games WHERE {reg}", rng)
        snaps = _read(
            con,
            "SELECT gsis_id, game_id, season, week, team, position, player, offense_snaps, "
            f"offense_pct, defense_snaps, defense_pct, st_snaps FROM snaps WHERE {greg} "
            "AND gsis_id IS NOT NULL",
            rng,
        )
        pfr_def = _read(con, f"SELECT * FROM pfr_def WHERE {greg}", rng)
        pfr_pass = _read(con, f"SELECT * FROM pfr_pass WHERE {greg}", rng)
        pfr_rush = _read(con, f"SELECT * FROM pfr_rush WHERE {greg}", rng)
        pfr_rec = _read(con, f"SELECT * FROM pfr_rec WHERE {greg}", rng)
        ngs = {
            k: _read(
                con,
                f"SELECT * FROM ngs_{k} WHERE {stype} AND NOT is_season_total "
                "AND week >= 1 AND season BETWEEN ? AND ?",
                rng,
            )
            for k in ("passing", "receiving", "rushing")
        }
        ftn = (
            _read(con, "SELECT * FROM ftn_plays WHERE season BETWEEN ? AND ?", rng)
            if _exists(con, "ftn_plays")
            else pl.DataFrame()
        )
        injuries = _read(
            con,
            "SELECT season, week, team, gsis_id, position, full_name, report_status, "
            f"practice_status, report_primary_injury FROM injuries WHERE {greg}",
            rng,
        )
        rosters = _read(
            con,
            "SELECT season, week, team, gsis_id, position, depth_chart_position, status, "
            f"full_name, years_exp, rookie_year FROM rosters_weekly WHERE {greg}",
            rng,
        )
    plays = (
        pl.scan_parquet((cur / "plays" / "*.parquet").as_posix())
        .filter(
            (pl.col("season") >= first_season)
            & (pl.col("season") <= last)
            & pl.col("season_type").is_in(["REG", "POST"] if playoffs else ["REG"])
        )
        .select(PLAY_COLS)
        .collect()
    )
    feat = paths.features
    team_ratings = (
        pl.read_parquet(feat / "team_ratings.parquet")
        if (feat / "team_ratings.parquet").exists()
        else pl.DataFrame()
    )
    game_features = (
        pl.read_parquet(feat / "game_features.parquet")
        if (feat / "game_features.parquet").exists()
        else pl.DataFrame()
    )
    log(
        f"player inputs {first_season}-{last}: {player_games.height:,} box-score rows, "
        f"{snaps.height:,} snap rows, {plays.height:,} plays"
    )
    return PlayerInputs(
        games=games.with_columns(
            pl.col("season", "week").cast(pl.Int32),
            pl.col("kickoff_utc").dt.convert_time_zone("UTC"),  # DuckDB hands back local time
        ),
        player_games=player_games,
        snaps=snaps,
        plays=plays,
        pfr_def=pfr_def,
        pfr_pass=pfr_pass,
        pfr_rush=pfr_rush,
        pfr_rec=pfr_rec,
        ngs_passing=ngs["passing"],
        ngs_receiving=ngs["receiving"],
        ngs_rushing=ngs["rushing"],
        ftn=ftn,
        injuries=injuries,
        rosters=rosters,
        team_ratings=team_ratings,
        game_features=game_features,
        first_season=first_season,
        last_season=last,
    )


def with_playoff_week(
    inp: PlayerInputs, season: int, week: int, paths: DataPaths | None = None
) -> PlayerInputs:
    """A playoff week to project (P10): the inputs above are regular season only, so add
    that week's playoff games (as upcoming: `completed` false), injury reports and weekly
    rosters. History, labels and training stay regular season: a playoff projection uses
    the player's regular-season form (doc 04 -> C, "As built in P10"). No-op for a regular
    week or a week without playoff games."""
    paths = paths or ensure_data_root()
    where = "game_type <> 'REG' AND season = ? AND week = ?"
    with closing(duckdb.connect(str(paths.curated / "nfl.duckdb"), read_only=True)) as con:
        games = _read(
            con, f"SELECT {', '.join(GAME_COLS)} FROM games WHERE {where}", [season, week]
        )
        if games.is_empty():
            return inp
        injuries = _read(
            con,
            f"SELECT {', '.join(inp.injuries.columns)} FROM injuries WHERE {where}",
            [season, week],
        )
        rosters = _read(
            con,
            f"SELECT {', '.join(inp.rosters.columns)} FROM rosters_weekly WHERE {where}",
            [season, week],
        )
    games = games.with_columns(
        pl.col("season", "week").cast(pl.Int32),
        pl.col("kickoff_utc").dt.convert_time_zone("UTC"),
        pl.lit(False).alias("completed"),  # predicted as if not played yet (rehearsals too)
    ).select(inp.games.columns)
    ids = games["game_id"].to_list()
    return replace(
        inp,
        games=pl.concat(
            [inp.games.filter(~pl.col("game_id").is_in(ids)), games.cast(inp.games.schema)]
        ),
        injuries=pl.concat([inp.injuries, injuries.cast(inp.injuries.schema)]),
        rosters=pl.concat([inp.rosters, rosters.cast(inp.rosters.schema)]),
    )


# ---- team-game frame ----------------------------------------------------------------------


def team_games(games: pl.DataFrame) -> pl.DataFrame:
    """One row per (team, REG game): opponent, home flag, kickoff, completed, `t`."""
    sides = [
        games.select(
            "game_id",
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            "kickoff_utc",
            pl.col(f"{side}_team").alias("team"),
            pl.col(f"{other}_team").alias("opponent"),
            pl.lit(side == "home").alias("home"),
            pl.col("completed").fill_null(False),
        )
        for side, other in (("home", "away"), ("away", "home"))
    ]
    return pl.concat(sides).with_columns(tkey().alias("t")).sort("t", "team")


# ---- play-by-play extras ------------------------------------------------------------------


def _scrimmage(plays: pl.DataFrame) -> pl.DataFrame:
    """Real offensive snaps: pass / run plays, no two-point tries."""
    return plays.filter(
        pl.col("play_type").is_in(["pass", "run"]) & (pl.col("two_point_attempt").fill_null(0) == 0)
    )


def play_extras(plays: pl.DataFrame) -> pl.DataFrame:
    """Per (player, game): dropbacks + QB EPA (as passer), red-zone targets / carries.

    Dropbacks: `qb_dropback == 1` with a `passer_id` (scrambles and sacks included; the
    `features/qb.py` definition). Team totals come along for the shares.
    """
    sc = _scrimmage(plays)
    db = (
        sc.filter((pl.col("qb_dropback") == 1) & pl.col("passer_id").is_not_null())
        .group_by("game_id", pl.col("passer_id").alias("player_id"))
        .agg(
            pl.len().cast(pl.Int32).alias("dropbacks"),
            pl.col("qb_epa").sum().alias("qb_epa_sum"),
        )
    )
    rushes = (
        sc.filter(pl.col("rusher_player_id").is_not_null() & (pl.col("rush") == 1))
        .group_by("game_id", pl.col("rusher_player_id").alias("player_id"))
        .agg(pl.len().cast(pl.Int32).alias("rush_plays"))
    )
    rz = sc.filter(pl.col("yardline_100") <= RED_ZONE)
    rz_t = (
        rz.filter(pl.col("receiver_player_id").is_not_null() & (pl.col("pass") == 1))
        .group_by("game_id", pl.col("receiver_player_id").alias("player_id"))
        .agg(pl.len().cast(pl.Int32).alias("rz_targets"))
    )
    rz_c = (
        rz.filter(pl.col("rusher_player_id").is_not_null() & (pl.col("rush") == 1))
        .group_by("game_id", pl.col("rusher_player_id").alias("player_id"))
        .agg(pl.len().cast(pl.Int32).alias("rz_carries"))
    )
    out = db
    for part in (rushes, rz_t, rz_c):
        out = out.join(part, on=["game_id", "player_id"], how="full", coalesce=True)
    return out.with_columns(
        pl.col("dropbacks", "rush_plays", "rz_targets", "rz_carries").fill_null(0)
    )


def team_play_totals(plays: pl.DataFrame) -> pl.DataFrame:
    """Per (team, game) on offense: plays, dropbacks, carries, targets, red-zone looks,
    pass rate over expected, seconds per play (pace) and EPA per play."""
    sc = _scrimmage(plays)
    return (
        sc.group_by("game_id", pl.col("posteam").alias("team"))
        .agg(
            pl.len().cast(pl.Int32).alias("team_plays"),
            (pl.col("qb_dropback") == 1).sum().cast(pl.Int32).alias("team_dropbacks"),
            ((pl.col("rush") == 1) & pl.col("rusher_player_id").is_not_null())
            .sum()
            .cast(pl.Int32)
            .alias("team_rushes"),
            ((pl.col("pass") == 1) & pl.col("receiver_player_id").is_not_null())
            .sum()
            .cast(pl.Int32)
            .alias("team_targets"),
            (
                (pl.col("yardline_100") <= RED_ZONE)
                & (pl.col("pass") == 1)
                & pl.col("receiver_player_id").is_not_null()
            )
            .sum()
            .cast(pl.Int32)
            .alias("team_rz_targets"),
            (
                (pl.col("yardline_100") <= RED_ZONE)
                & (pl.col("rush") == 1)
                & pl.col("rusher_player_id").is_not_null()
            )
            .sum()
            .cast(pl.Int32)
            .alias("team_rz_carries"),
            pl.col("pass_oe").mean().alias("team_proe"),
            pl.col("epa").mean().alias("team_epa_play"),
            pl.col("pass").mean().alias("team_pass_rate"),
        )
        .with_columns(pl.col("team_proe") / 100.0)  # nflverse pass_oe is in percentage points
    )


# ---- the history table --------------------------------------------------------------------


def _pgroup(position: pl.Expr, group: pl.Expr) -> pl.Expr:
    """Box-score position group, with DB split into CB / S; else the mapped position."""
    mapped = position.replace_strict(POSITION_MAP, default=None, return_dtype=pl.String)
    return (
        pl.when(group == "DB")
        .then(pl.when(position == "CB").then(pl.lit("CB")).otherwise(pl.lit("S")))
        .when(group.is_in(["QB", "RB", "WR", "TE", "OL", "DL", "LB", "SPEC"]))
        .then(pl.when((group == "RB") & (position == "FB")).then(pl.lit("FB")).otherwise(group))
        .otherwise(mapped)
    )


def player_history(inp: PlayerInputs) -> pl.DataFrame:
    """One row per (player, REG game) he took part in, with every stat the targets and
    features use. Sorted by (player_id, t)."""
    games = inp.games.select(
        "game_id", "season", "week", "kickoff_utc", "home_team", "away_team"
    ).with_columns(pl.col("season", "week").cast(pl.Int32))
    pg = inp.player_games.filter(pl.col("game_id").is_in(games["game_id"].to_list()))
    sn = (
        inp.snaps.group_by("gsis_id", "game_id", maintain_order=True)
        .agg(
            pl.col("team").first().alias("team_snap"),
            pl.col("position").first().alias("position_snap"),
            pl.col("player").first().alias("player_snap"),
            pl.col("offense_snaps").max(),
            pl.col("offense_pct").max(),
            pl.col("defense_snaps").max(),
            pl.col("defense_pct").max(),
            pl.col("st_snaps").max(),
        )
        .rename({"gsis_id": "player_id"})
    )
    h = pg.drop("season", "week").join(sn, on=["player_id", "game_id"], how="full", coalesce=True)
    h = h.join(games, on="game_id", how="inner")
    h = h.with_columns(
        pl.coalesce("team", "team_snap").alias("team"),
        pl.coalesce("player_display_name", "player_snap").alias("player"),
        pl.coalesce("position", "position_snap").alias("position"),
    )
    h = h.with_columns(
        _pgroup(pl.col("position"), pl.col("position_group")).alias("pgroup"),
        pl.when(pl.col("team") == pl.col("home_team"))
        .then(pl.col("away_team"))
        .otherwise(pl.col("home_team"))
        .alias("opponent"),
        (pl.col("team") == pl.col("home_team")).alias("home"),
    ).filter(pl.col("team").is_not_null())

    # play-by-play extras + team totals
    h = h.join(play_extras(inp.plays), on=["player_id", "game_id"], how="left")
    h = h.join(team_play_totals(inp.plays), on=["game_id", "team"], how="left")

    # PFR pressures etc. (null when PFR has nothing for that team-game yet)
    pfr = inp.pfr_def.filter(pl.col("gsis_id").is_not_null())
    pfr_cols = {
        "def_pressures": "pressures",
        "def_times_hurried": "pfr_hurries",
        "def_times_hitqb": "pfr_qb_hits",
        "def_times_blitzed": "pfr_blitzes",
        "def_missed_tackles": "pfr_missed_tackles",
        "def_tackles_combined": "pfr_tackles",
        "def_targets": "pfr_targets_allowed",
        "def_completions_allowed": "pfr_completions_allowed",
        "def_yards_allowed": "pfr_yards_allowed",
    }
    have = {c: n for c, n in pfr_cols.items() if c in pfr.columns}  # an older table may lack one
    pfr_rows = (
        pfr.group_by(pl.col("gsis_id").alias("player_id"), "game_id")
        .agg([pl.col(c).sum().alias(n) for c, n in have.items()])
        .with_columns(
            pl.lit(True).alias("_pfr_row"),
            *[
                pl.lit(None, dtype=pl.Float64).alias(n)
                for c, n in pfr_cols.items()
                if c not in have
            ],
        )
    )
    pfr_games = pfr.select("game_id", "team").unique().with_columns(pl.lit(True).alias("_pfr_game"))
    h = h.join(pfr_rows, on=["player_id", "game_id"], how="left").join(
        pfr_games, on=["game_id", "team"], how="left"
    )
    zero_if_game = [
        pl.when(pl.col("_pfr_game")).then(pl.col(n).fill_null(0)).otherwise(pl.col(n)).alias(n)
        for n in have.values()  # a column the table lacks stays null, not a made-up zero
    ]
    h = h.with_columns(zero_if_game)

    count_cols = [
        "completions",
        "attempts",
        "passing_yards",
        "passing_tds",
        "passing_interceptions",
        "sacks_suffered",
        "passing_air_yards",
        "carries",
        "rushing_yards",
        "rushing_tds",
        "receptions",
        "targets",
        "receiving_yards",
        "receiving_tds",
        "receiving_air_yards",
        "receiving_yards_after_catch",
        "def_tackles_solo",
        "def_tackle_assists",
        "def_tackles_for_loss",
        "def_sacks",
        "def_qb_hits",
        "def_interceptions",
        "def_pass_defended",
        "dropbacks",
        "rush_plays",
        "rz_targets",
        "rz_carries",
    ]
    h = h.with_columns(
        [pl.col(c).fill_null(0).cast(pl.Float64) for c in count_cols]
        + [pl.col(c).fill_null(0.0) for c in ("target_share", "air_yards_share", "wopr")]
    )
    box_off = (pl.col("attempts") + pl.col("carries") + pl.col("targets")) > 0
    box_def = (
        pl.col("def_tackles_solo") + pl.col("def_tackle_assists") + pl.col("def_pass_defended")
    ) > 0
    h = h.with_columns(
        pl.when(pl.col("offense_snaps").is_not_null())
        .then(pl.col("offense_snaps") > 0)
        .otherwise(box_off)
        .alias("played_off"),
        pl.when(pl.col("defense_snaps").is_not_null())
        .then(pl.col("defense_snaps") > 0)
        .otherwise(box_def)
        .alias("played_def"),
        (pl.col("rushing_yards") + pl.col("receiving_yards")).alias("scrimmage_yards"),
        (pl.col("def_tackles_solo") + pl.col("def_tackle_assists")).alias("tackles"),
        # P08 yes / no labels (probability targets): scored a rushing or receiving TD, made an
        # interception, defended a pass
        ((pl.col("rushing_tds") + pl.col("receiving_tds")) >= 1).cast(pl.Float64).alias("any_td"),
        (pl.col("def_interceptions") >= 1).cast(pl.Float64).alias("def_int_any"),
        (pl.col("def_pass_defended") >= 1).cast(pl.Float64).alias("pd_any"),
        pl.when(pl.col("dropbacks") > 0)
        .then(pl.col("qb_epa_sum") / pl.col("dropbacks"))
        .otherwise(None)
        .alias("epa_per_db"),
        pl.when(pl.col("team_rushes") > 0)
        .then(pl.col("rush_plays") / pl.col("team_rushes"))
        .otherwise(0.0)
        .alias("carry_share"),
        pl.when(pl.col("team_rz_targets") > 0)
        .then(pl.col("rz_targets") / pl.col("team_rz_targets"))
        .otherwise(None)
        .alias("rz_target_share"),
        pl.when(pl.col("team_rz_carries") > 0)
        .then(pl.col("rz_carries") / pl.col("team_rz_carries"))
        .otherwise(None)
        .alias("rz_carry_share"),
        pl.when(pl.col("team_dropbacks") > 0)
        .then(pl.col("dropbacks") / pl.col("team_dropbacks"))
        .otherwise(0.0)
        .alias("dropback_share"),
        tkey().alias("t"),
    )
    # main QB of each team-game: most dropbacks (ties: attempts, then id)
    qbs = (
        h.filter((pl.col("pgroup") == "QB") & (pl.col("dropbacks") > 0))
        .sort(
            ["game_id", "team", "dropbacks", "attempts", "player_id"],
            descending=[False, False, True, True, False],
        )
        .group_by("game_id", "team", maintain_order=True)
        .first()
        .select("game_id", "team", pl.col("player_id").alias("_main_qb"))
    )
    h = h.join(qbs, on=["game_id", "team"], how="left").with_columns(
        (pl.col("player_id") == pl.col("_main_qb")).fill_null(False).alias("main_qb")
    )
    drop = [c for c in h.columns if c.startswith("_") or c.endswith("_snap")] + [
        "player_display_name",
        "opponent_team",
        "home_team",
        "away_team",
    ]
    h = h.drop([c for c in drop if c in h.columns]).filter(pl.col("player_id").is_not_null())
    # a mid-season trade can leave a player on both teams' sheets in one week (2019 w16:
    # 0 snaps for the old team, 9 for the new one): keep the row where he played most
    played_most = (
        pl.col("offense_snaps").fill_null(0) + pl.col("defense_snaps").fill_null(0)
    ).alias("_snaps_all")
    h = (
        h.with_columns(played_most)
        .sort(["player_id", "t", "_snaps_all"], descending=[False, False, True])
        .unique(["player_id", "t"], keep="first", maintain_order=True)
        .drop("_snaps_all")
    )
    return h.sort("player_id", "t")
