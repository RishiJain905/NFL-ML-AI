"""Supporting evidence for team trends (documentation/04 -> A. Team ratings and trend -> Trend).

The digest explains a trend (`trend_delta`, net rating as of week w minus as of week w-3)
with "drivers": the rating parts that moved most, plus supporting evidence. This module
builds the evidence: QB change, key players out, and shifts in CPOE, pass rate over
expected, sack rate, PFR pressure rate and NGS time to throw.

Windows for an as-of key (season s, week w), per team (REG + POST games that were played):
- recent: the team's games of season s with week in [w - window, w - 1];
- before: the team's games of season s with week < w - window. When that is fewer than
  2 games, the team's REG games of season s - 1 are used instead
  (`before_source = 'last_season'`; `'season'` otherwise; null when neither exists).

Every window ends before week w, so only rows from weeks strictly before the key are
used (documentation/04 -> Leakage rules; tested with `assert_future_invariant`).

Rates are ratios of sums over the window's games (pooled), not means of per-game rates,
so a game with few dropbacks doesn't count as much as a full game:
- `off_cpoe`: mean `plays.cpoe` over the window's pass plays with a non-null value;
- `off_proe`: mean `plays.pass_oe` over pass/run plays (no `no_play` penalties);
- sack rates: sacks / dropbacks (`qb_dropback == 1`); two-point tries are left out of
  every play stat;
- `off_pressure_rate`: `pfr_pass.times_pressured` (QB rows summed to the team-game) /
  team dropbacks, over the games that have PFR rows;
- `def_pressure_rate`: `pfr_def.def_pressures` (player rows summed) / opponent
  dropbacks, over the games that have PFR rows;
- `off_time_to_throw`: NGS weekly `avg_time_to_throw` weighted by `attempts`.
PFR publishes about a week late, so PFR rows from week w - 1 never count for key w
(`PFR_LAG_WEEKS`), for live and historical keys alike: a rebuild of history sees what the
Tuesday run saw. NGS can also miss the latest week; that only shrinks the window.
Every `*_delta` is recent - before and is null when either window has no data.

QB change: a played game's QB is the passer with the most dropbacks (the curated listed-QB
columns are wrong for some games; P04). `qb_now` is the QB of the team's latest played game
before week w; the comparison QB (`qb_before_*`) is the QB of its latest game of season s
before the recent window. With no
such game, the comparison QB is last season's main starter: most completed REG starts
in s - 1, latest start breaking ties (as in `ratings.qb_changes`), so a backup who
started the last game of s - 1 (a rested week 18) doesn't count as a change.
`qb_change` is null when either QB is unknown.

Key players: distinct players listed `Out` or `Doubtful` in the injury reports of weeks
[w - window, w - 1] whose mean offense or defense snap share over the team's before
games (games they missed count as 0) is at least `KEY_SHARE`. Snap shares are read per
team-game on a 0-1 scale (a game whose shares exceed 1.5 is read as percent).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import polars as pl

from nflengine.features.asof import AsOf
from nflengine.paths import DataPaths

EVIDENCE_TABLES = ("games", "plays", "injuries", "snaps", "pfr_pass", "pfr_def", "ngs_passing")
REQUIRED_TABLES = ("games", "plays")
PFR_LAG_WEEKS = 1  # PFR advanced stats for week N appear after Tuesday of week N + 1
PFR_COLS = ["prs", "prs_db", "dprs", "dprs_db"]
PLAY_COLS = [
    "game_id",
    "season",
    "week",
    "posteam",
    "defteam",
    "play_type",
    "two_point_attempt",
    "qb_dropback",
    "sack",
    "cpoe",
    "pass_oe",
]
OPTIONAL_PLAY_COLS = ["passer_id"]  # read when present: each game's QB by dropbacks
# Columns read per table (only those present are read).
TABLE_COLS: dict[str, list[str]] = {
    "games": [
        "game_id",
        "season",
        "week",
        "game_type",
        "home_team",
        "away_team",
        "home_qb_id",
        "away_qb_id",
        "home_qb_name",
        "away_qb_name",
        "kickoff_utc",
        "completed",
    ],
    "injuries": ["season", "week", "team", "gsis_id", "full_name", "position", "report_status"],
    "snaps": [
        "game_id",
        "season",
        "week",
        "team",
        "gsis_id",
        "position",
        "offense_pct",
        "defense_pct",
    ],
    "pfr_pass": ["game_id", "season", "week", "team", "times_pressured"],
    "pfr_def": ["game_id", "season", "week", "team", "def_pressures"],
    "ngs_passing": [
        "season",
        "season_type",
        "week",
        "team",
        "player_gsis_id",
        "avg_time_to_throw",
        "attempts",
        "is_season_total",
    ],
}

KEY_SHARE = 0.60
OUT_STATUSES = ("Out", "Doubtful")
MAX_NAMES = 3
RATES = ("off_cpoe", "off_proe", "off_sack_rate", "def_sack_rate")
RATES += ("off_pressure_rate", "def_pressure_rate", "off_time_to_throw")

OUTPUT_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "team": pl.String(),
    "n_recent_games": pl.Int32(),
    "n_before_games": pl.Int32(),
    "before_source": pl.String(),
    "qb_change": pl.Boolean(),
    "qb_now_id": pl.String(),
    "qb_now_name": pl.String(),
    "qb_before_id": pl.String(),
    "qb_before_name": pl.String(),
    "key_players_out": pl.Int32(),
    "key_players_out_names": pl.String(),
    **{f"{r}_delta": pl.Float64() for r in RATES},
}


# ---- loading ---------------------------------------------------------------------------
def load_evidence_tables(paths: DataPaths, min_season: int) -> dict[str, pl.DataFrame]:
    """Read the curated tables above (plays: only PLAY_COLS, seasons >= min_season-1).

    Optional tables that are missing on disk are simply left out; games and plays are
    required.
    """
    first = min_season - 1
    cur = paths.curated
    out: dict[str, pl.DataFrame] = {}
    for name in EVIDENCE_TABLES:
        if name == "plays":
            files = sorted((cur / "plays").glob("*.parquet"))
            files = [
                f for f in files if _file_season(f.stem) is None or _file_season(f.stem) >= first
            ]
            if not files:
                raise FileNotFoundError(f"no play-by-play parquet files under {cur / 'plays'}")

            def cols(f: Path) -> list[str]:
                present = pl.scan_parquet(f).collect_schema().names()
                return PLAY_COLS + [c for c in OPTIONAL_PLAY_COLS if c in present]

            lf = pl.concat(
                [pl.scan_parquet(f).select(cols(f)) for f in files], how="diagonal_relaxed"
            )
            out[name] = lf.filter(pl.col("season") >= first).collect()
            continue
        path = cur / f"{name}.parquet"
        if not path.exists():
            if name in REQUIRED_TABLES:
                raise FileNotFoundError(f"required curated table missing: {path}")
            continue
        lf = pl.scan_parquet(path)
        present = lf.collect_schema().names()
        lf = lf.select([c for c in TABLE_COLS[name] if c in present])
        out[name] = lf.filter(pl.col("season") >= first).collect()
    return out


def _file_season(stem: str) -> int | None:
    """`season=2024` -> 2024 (None when the name doesn't say)."""
    tail = stem.rsplit("=", 1)[-1]
    return int(tail) if tail.isdigit() else None


# ---- per team-game stats -----------------------------------------------------------------
def _team_games(games: pl.DataFrame) -> pl.DataFrame:
    """One row per (game, team) for played games: opponent and starting QB."""
    g = games.filter(pl.col("completed").fill_null(False))
    base = [pl.col(c) for c in ("game_id", "season", "week", "game_type")]
    sides = []
    for side, other in (("home", "away"), ("away", "home")):
        sides.append(
            g.select(
                *base,
                pl.col(f"{side}_team").alias("team"),
                pl.col(f"{other}_team").alias("opp"),
                pl.col(f"{side}_qb_id").alias("qb_id"),
                pl.col(f"{side}_qb_name").alias("qb_name"),
            )
        )
    return pl.concat(sides).with_columns(pl.col("season", "week").cast(pl.Int32))


def _played_qbs(tg: pl.DataFrame, plays: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """Each played team-game's QB = the passer with the most dropbacks (as `features/qb.py`
    does), not the schedule's listed QB: the curated listed-QB columns are wrong for some
    games (2024 IND weeks 8, 11, 12, 13, 16 list Flacco, while Richardson took every
    dropback; found by the P04 fact-check). The listed QB is kept when a game has no
    dropbacks. Names come from the listed-QB columns of any game."""
    if "passer_id" not in plays.columns:
        return tg
    top = (
        plays.filter((pl.col("qb_dropback") == 1) & pl.col("passer_id").is_not_null())
        .group_by("game_id", pl.col("posteam").alias("team"), "passer_id")
        .agg(pl.len().alias("n"))
        .sort(["game_id", "team", "n", "passer_id"], descending=[False, False, True, False])
        .group_by("game_id", "team", maintain_order=True)
        .first()
        .select("game_id", "team", pl.col("passer_id").alias("_qb"))
    )
    names = (
        pl.concat(
            [
                games.select(pl.col(f"{s}_qb_id").alias("_qb"), pl.col(f"{s}_qb_name").alias("_n"))
                for s in ("home", "away")
            ]
        )
        .drop_nulls()
        .unique("_qb", keep="last")
    )
    return (
        tg.join(top, on=["game_id", "team"], how="left")
        .join(names, on="_qb", how="left")
        .with_columns(
            pl.when(pl.col("_qb").is_not_null() & pl.col("_n").is_not_null())
            .then(pl.col("_qb"))
            .otherwise(pl.col("qb_id"))
            .alias("qb_id"),
            pl.when(pl.col("_qb").is_not_null() & pl.col("_n").is_not_null())
            .then(pl.col("_n"))
            .otherwise(pl.col("qb_name"))
            .alias("qb_name"),
        )
        .drop("_qb", "_n")
    )


def _sum_or_null(col: str) -> pl.Expr:
    """Sum that stays null when every value is null (no data, not zero)."""
    return pl.when(pl.col(col).count() > 0).then(pl.col(col).sum()).otherwise(None)


def _game_stats(tg: pl.DataFrame, tables: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    """Per (game_id, team): numerators and denominators of every evidence rate."""
    plays = tables["plays"].join(tg.select("game_id").unique(), on="game_id", how="semi")
    plays = plays.filter(
        pl.col("posteam").is_not_null() & (pl.col("two_point_attempt").fill_null(0) == 0)
    )
    dropback = pl.col("qb_dropback") == 1
    run_pass = pl.col("play_type").is_in(["pass", "run"])
    is_pass = pl.col("play_type") == "pass"
    off = plays.group_by("game_id", pl.col("posteam").alias("team")).agg(
        pl.col("cpoe").filter(is_pass).sum().alias("cpoe_sum"),
        pl.col("cpoe").filter(is_pass).count().alias("cpoe_n"),
        pl.col("pass_oe").filter(run_pass).sum().alias("proe_sum"),
        pl.col("pass_oe").filter(run_pass).count().alias("proe_n"),
        dropback.sum().alias("db"),
        (dropback & (pl.col("sack") == 1)).sum().alias("sk"),
    )
    dfn = plays.group_by("game_id", pl.col("defteam").alias("team")).agg(
        dropback.sum().alias("d_db"),
        (dropback & (pl.col("sack") == 1)).sum().alias("d_sk"),
    )
    stats = (
        tg.select("game_id", "season", "week", "team")
        .join(off, on=["game_id", "team"], how="left")
        .join(dfn, on=["game_id", "team"], how="left")
    )

    if "pfr_pass" in tables:
        pp = tables["pfr_pass"].group_by("game_id", "team").agg(_sum_or_null("times_pressured"))
        stats = stats.join(
            pp.rename({"times_pressured": "prs"}), on=["game_id", "team"], how="left"
        )
    else:
        stats = stats.with_columns(pl.lit(None, pl.Float64).alias("prs"))
    if "pfr_def" in tables:
        pd_ = tables["pfr_def"].group_by("game_id", "team").agg(_sum_or_null("def_pressures"))
        stats = stats.join(
            pd_.rename({"def_pressures": "dprs"}), on=["game_id", "team"], how="left"
        )
    else:
        stats = stats.with_columns(pl.lit(None, pl.Float64).alias("dprs"))

    stats = stats.join(_ngs_team_weeks(tables, tg), on=["season", "week", "team"], how="left")
    return stats.with_columns(
        # Dropbacks only count where the PFR numerator exists (lagging weeks drop out).
        pl.when(pl.col("prs").is_not_null()).then(pl.col("db")).alias("prs_db"),
        pl.when(pl.col("dprs").is_not_null()).then(pl.col("d_db")).alias("dprs_db"),
    ).rename({"season": "g_season", "week": "g_week"})


def _ngs_team_weeks(tables: Mapping[str, pl.DataFrame], tg: pl.DataFrame) -> pl.DataFrame:
    """NGS passing weekly rows summed to (season, week, team): attempts-weighted TTT."""
    empty = pl.DataFrame(
        schema={
            "season": pl.Int32,
            "week": pl.Int32,
            "team": pl.String,
            "ttt_w": pl.Float64,
            "ttt_att": pl.Float64,
        }
    )
    if "ngs_passing" not in tables:
        return empty
    n = tables["ngs_passing"]
    weekly = ~pl.col("is_season_total") if "is_season_total" in n.columns else pl.col("week") > 0
    n = n.filter(weekly & (pl.col("week") > 0) & pl.col("avg_time_to_throw").is_not_null())
    n = n.with_columns(pl.col("season", "week").cast(pl.Int32))
    if "season_type" in n.columns:
        # NGS numbers the Super Bowl one week after `games` does (Pro Bowl gap); WC/DIV/CON
        # line up. Clamp POST weeks to the season's SB week.
        sb = (
            tg.filter(pl.col("game_type") == "SB")
            .group_by("season")
            .agg(pl.col("week").max().alias("sb_week"))
        )
        n = n.join(sb, on="season", how="left").with_columns(
            pl.when((pl.col("season_type") == "POST") & (pl.col("week") > pl.col("sb_week")))
            .then(pl.col("sb_week"))
            .otherwise(pl.col("week"))
            .alias("week")
        )
    att = pl.col("attempts").cast(pl.Float64)
    return n.group_by("season", "week", "team").agg(
        (pl.col("avg_time_to_throw") * att).sum().alias("ttt_w"),
        att.sum().alias("ttt_att"),
    )


# ---- windows -----------------------------------------------------------------------------
def _windows(
    kt: pl.DataFrame, tg: pl.DataFrame, window: int
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Returns (window rows, per-key window info, QB rows).

    Window rows: (season, week, team, win, game_id) with win in {recent, before}.
    """
    games = tg.select(
        "season", "team", "game_id", "game_type", "qb_id", "qb_name", pl.col("week").alias("g_week")
    )
    cur = (
        kt.join(games, on=["season", "team"], how="inner")
        .filter(pl.col("g_week") < pl.col("week"))
        .with_columns(
            pl.when(pl.col("g_week") >= pl.col("week") - window)
            .then(pl.lit("recent"))
            .otherwise(pl.lit("before"))
            .alias("win")
        )
    )
    prev_games = games.with_columns((pl.col("season") + 1).cast(pl.Int32))
    prev = kt.join(prev_games.filter(pl.col("game_type") == "REG"), on=["season", "team"])

    k = ["season", "week", "team"]
    n_cur = cur.filter(pl.col("win") == "before").group_by(k).agg(pl.len().alias("n_cur"))
    n_prev = prev.group_by(k).agg(pl.len().alias("n_prev"))
    info = (
        kt.join(n_cur, on=k, how="left")
        .join(n_prev, on=k, how="left")
        .with_columns(pl.col("n_cur", "n_prev").fill_null(0))
        .with_columns(((pl.col("n_cur") < 2) & (pl.col("n_prev") > 0)).alias("use_prev"))
        .with_columns(
            pl.when(pl.col("use_prev"))
            .then(pl.lit("last_season"))
            .when(pl.col("n_cur") > 0)
            .then(pl.lit("season"))
            .otherwise(None)
            .alias("before_source"),
            pl.when(pl.col("use_prev"))
            .then(pl.col("n_prev"))
            .otherwise(pl.col("n_cur"))
            .alias("n_before_games"),
        )
    )
    flags = info.select(*k, "use_prev")
    before_cur = cur.filter(pl.col("win") == "before").join(flags, on=k).filter(~pl.col("use_prev"))
    before_prev = (
        prev.join(flags, on=k)
        .filter(pl.col("use_prev"))
        .with_columns(pl.lit("before").alias("win"))
    )
    cols = [*k, "win", "game_id"]
    rows = pl.concat(
        [
            cur.filter(pl.col("win") == "recent").select(cols),
            before_cur.select(cols),
            before_prev.select(cols),
        ]
    )

    # QBs: latest game of season s before the key / before the recent window. With no
    # season-s game, both fall back to last season's main starter (most completed REG
    # starts, latest start breaks ties; same rule as `ratings.qb_changes`), so a backup
    # who started a meaningless week 18 doesn't read as a QB change.
    main_prev = (
        prev_games.filter((pl.col("game_type") == "REG") & pl.col("qb_id").is_not_null())
        .sort("g_week")
        .group_by("season", "team", "qb_id")
        .agg(
            pl.len().alias("n"),
            pl.col("g_week").max().alias("last"),
            pl.col("qb_name").last().alias("p_name"),
        )
        .sort(["season", "team", "n", "last"], descending=[False, False, True, True])
        .group_by("season", "team", maintain_order=True)
        .first()
        .select("season", "team", pl.col("qb_id").alias("p_id"), "p_name")
    )
    now = (
        cur.sort("g_week")
        .group_by(k)
        .agg(pl.col("qb_id").last().alias("n_id"), pl.col("qb_name").last().alias("n_name"))
    )
    then = (
        cur.filter(pl.col("win") == "before")
        .sort("g_week")
        .group_by(k)
        .agg(pl.col("qb_id").last().alias("b_id"), pl.col("qb_name").last().alias("b_name"))
    )
    qbs = (
        kt.join(now, on=k, how="left")
        .join(then, on=k, how="left")
        .join(main_prev, on=["season", "team"], how="left")
        .select(
            *k,
            pl.coalesce("n_id", "p_id").alias("qb_now_id"),
            pl.coalesce("n_name", "p_name").alias("qb_now_name"),
            pl.coalesce("b_id", "p_id").alias("qb_before_id"),
            pl.coalesce("b_name", "p_name").alias("qb_before_name"),
        )
        .with_columns((pl.col("qb_now_id") != pl.col("qb_before_id")).alias("qb_change"))
    )
    return rows, info.select(*k, "n_before_games", "before_source"), qbs


def _rates(rows: pl.DataFrame, stats: pl.DataFrame) -> pl.DataFrame:
    """Window rates per (season, week, team, win)."""
    sums = ["cpoe_sum", "cpoe_n", "proe_sum", "proe_n", "db", "sk", "d_db", "d_sk"]
    sums += [*PFR_COLS, "ttt_w", "ttt_att"]
    # PFR publishes about a week late: a Tuesday run for week w never has week w-1 yet.
    # Apply the same lag to every historical key so backtests see what live runs see.
    lagged = (pl.col("g_season") == pl.col("season")) & (
        pl.col("g_week") >= pl.col("week") - PFR_LAG_WEEKS
    )
    agg = (
        rows.join(stats, on=["game_id", "team"], how="left")
        .with_columns([pl.when(lagged).then(None).otherwise(pl.col(c)).alias(c) for c in PFR_COLS])
        .group_by("season", "week", "team", "win")
        .agg(pl.len().alias("n_games"), *[pl.col(c).sum().cast(pl.Float64) for c in sums])
    )

    def ratio(num: str, den: str) -> pl.Expr:
        return pl.when(pl.col(den) > 0).then(pl.col(num) / pl.col(den)).otherwise(None)

    return agg.select(
        "season",
        "week",
        "team",
        "win",
        "n_games",
        ratio("cpoe_sum", "cpoe_n").alias("off_cpoe"),
        ratio("proe_sum", "proe_n").alias("off_proe"),
        ratio("sk", "db").alias("off_sack_rate"),
        ratio("d_sk", "d_db").alias("def_sack_rate"),
        ratio("prs", "prs_db").alias("off_pressure_rate"),
        ratio("dprs", "dprs_db").alias("def_pressure_rate"),
        ratio("ttt_w", "ttt_att").alias("off_time_to_throw"),
    )


# ---- key players out ------------------------------------------------------------------------
def _key_players_out(
    kt: pl.DataFrame,
    rows: pl.DataFrame,
    tables: Mapping[str, pl.DataFrame],
    window: int,
) -> pl.DataFrame:
    """Per key: count and names of key players listed Out/Doubtful in the recent weeks.

    Null when there are no injury reports for those weeks or no snap counts for the
    team's before games (unknown, not zero).
    """
    k = ["season", "week", "team"]
    null = kt.with_columns(
        pl.lit(None, pl.Int32).alias("key_players_out"),
        pl.lit(None, pl.String).alias("key_players_out_names"),
    )
    if "injuries" not in tables or "snaps" not in tables:
        return null
    inj = tables["injuries"].with_columns(pl.col("season", "week").cast(pl.Int32))
    snaps = tables["snaps"].filter(pl.col("gsis_id").is_not_null())

    # Injury reports exist league-wide for the key's recent weeks?
    rep_weeks = inj.select("season", pl.col("week").alias("i_week")).unique()
    has_reports = (
        kt.select("season", "week")
        .unique()
        .join(rep_weeks, on="season")
        .filter(pl.col("i_week").is_between(pl.col("week") - window, pl.col("week") - 1))
        .select("season", "week")
        .unique()
        .with_columns(pl.lit(True).alias("has_reports"))
    )

    # Snap shares on a 0-1 scale, decided per team-game.
    pct = ["offense_pct", "defense_pct"]
    snaps = snaps.with_columns(
        pl.max_horizontal(*pct).max().over("game_id", "team").alias("_max")
    ).with_columns(
        [
            pl.when(pl.col("_max") > 1.5).then(pl.col(c) / 100).otherwise(pl.col(c)).alias(c)
            for c in pct
        ]
    )
    before = rows.filter(pl.col("win") == "before").select(*k, "game_id")
    snap_games = snaps.select("game_id", "team").unique()
    has_snaps = (
        before.join(snap_games, on=["game_id", "team"], how="semi")
        .select(k)
        .unique()
        .with_columns(pl.lit(True).alias("has_snaps"))
    )
    n_before = before.group_by(k).agg(pl.len().alias("n_b"))

    listed = inj.filter(
        pl.col("report_status").is_in(OUT_STATUSES) & pl.col("gsis_id").is_not_null()
    ).select(
        "season",
        pl.col("week").alias("i_week"),
        "team",
        "gsis_id",
        "full_name",
        "position",
    )
    out = (
        kt.join(listed, on=["season", "team"])
        .filter(pl.col("i_week").is_between(pl.col("week") - window, pl.col("week") - 1))
        .sort("i_week")
        .group_by(*k, "gsis_id")
        .agg(pl.col("full_name").last(), pl.col("position").last())
    )
    shares = (
        out.select(*k, "gsis_id")
        .join(before, on=k)
        .join(
            snaps.select("game_id", "team", "gsis_id", *pct),
            on=["game_id", "team", "gsis_id"],
            how="left",
        )
        .group_by(*k, "gsis_id")
        .agg([pl.col(c).fill_null(0).sum().alias(c) for c in pct])
        .join(n_before, on=k)
        .select(
            *k,
            "gsis_id",
            (pl.max_horizontal(*pct) / pl.col("n_b")).alias("share"),
        )
    )
    label = pl.when(pl.col("position").is_not_null() & (pl.col("position") != ""))
    label = label.then(pl.format("{} ({})", "full_name", "position")).otherwise(pl.col("full_name"))
    key = (
        out.join(shares, on=[*k, "gsis_id"])
        .filter(pl.col("share") >= KEY_SHARE)
        .sort(["share", "full_name"], descending=[True, False])
        .group_by(k, maintain_order=True)
        .agg(
            pl.len().cast(pl.Int32).alias("n_key"),
            label.head(MAX_NAMES).str.join(", ").alias("names"),
        )
    )
    return (
        kt.join(has_reports, on=["season", "week"], how="left")
        .join(has_snaps, on=k, how="left")
        .join(key, on=k, how="left")
        .select(
            *k,
            pl.when(pl.col("has_reports") & pl.col("has_snaps"))
            .then(pl.col("n_key").fill_null(0))
            .otherwise(None)
            .cast(pl.Int32)
            .alias("key_players_out"),
            pl.when(pl.col("has_reports") & pl.col("has_snaps"))
            .then(pl.col("names"))
            .otherwise(None)
            .alias("key_players_out_names"),
        )
    )


# ---- public --------------------------------------------------------------------------------
def trend_evidence(
    tables: Mapping[str, pl.DataFrame], keys: Sequence[AsOf], window: int = 3
) -> pl.DataFrame:
    """One row per (season, week, team) for every key with week > window, for every team
    with a game in that season. Leak-free: only rows from weeks strictly before the key
    are used. Columns: `OUTPUT_SCHEMA` (definitions in the module docstring).
    """
    games = tables["games"].with_columns(pl.col("season", "week").cast(pl.Int32))
    key_df = (
        pl.DataFrame(
            {"season": [k.season for k in keys], "week": [k.week for k in keys]},
            schema={"season": pl.Int32, "week": pl.Int32},
        )
        .filter(pl.col("week") > window)
        .unique()
    )
    teams = pl.concat(
        [games.select("season", pl.col(c).alias("team")) for c in ("home_team", "away_team")]
    ).unique()
    kt = key_df.join(teams, on="season").sort("season", "week", "team")
    if kt.is_empty():
        return pl.DataFrame(schema=OUTPUT_SCHEMA)

    tg = _played_qbs(_team_games(games), tables["plays"], games)
    stats = _game_stats(tg, tables)
    rows, info, qbs = _windows(kt, tg, window)
    rates = _rates(rows, stats)
    k = ["season", "week", "team"]
    recent = rates.filter(pl.col("win") == "recent").drop("win")
    before = rates.filter(pl.col("win") == "before").drop("win", "n_games")
    both = recent.join(before, on=k, how="full", coalesce=True, suffix="_b")

    out = (
        kt.join(both, on=k, how="left")
        .join(info, on=k, how="left")
        .join(qbs, on=k, how="left")
        .join(_key_players_out(kt, rows, tables, window), on=k, how="left")
        .with_columns(
            pl.col("n_games").fill_null(0).alias("n_recent_games"),
            *[(pl.col(r) - pl.col(f"{r}_b")).alias(f"{r}_delta") for r in RATES],
        )
    )
    return out.select([pl.col(c).cast(t) for c, t in OUTPUT_SCHEMA.items()]).sort(k)
