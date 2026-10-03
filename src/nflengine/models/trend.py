"""Team trend: a change in underlying strength (documentation/04 -> A -> Trend).

- `trend_delta`: net EPA rating as of week w minus as of week w-3 (same method).
- `perf_vs_expected`: mean over the team's last 3 games (this season) of actual EPA margin
  per play minus the margin its ratings expected before each game.
- `direction`: `up` / `down` when `trend_delta` is outside the band set by the 20th / 80th
  percentiles of regular-season deltas from **earlier seasons** (at least 3), otherwise
  `stable`; null while there isn't enough history.
- Drivers: the rating parts (pass/rush offense/defense) that moved most, weighted by how
  much of the game they cover. Supporting evidence fields come from `trend_evidence`.
"""

from __future__ import annotations

import polars as pl

from nflengine.models.ratings_eval import add_prediction

TREND_WINDOW = 3
BAND_QUANTILE = 0.20
MIN_BAND_SEASONS = 3

# part -> (rating column, sign so that + = better, play type it covers)
DRIVER_PARTS: dict[str, tuple[str, int, str]] = {
    "pass_offense": ("off_pass_epa", 1, "pass"),
    "rush_offense": ("off_rush_epa", 1, "rush"),
    "pass_defense": ("def_pass_epa", -1, "pass"),
    "rush_defense": ("def_rush_epa", -1, "rush"),
}


def reg_weeks(games: pl.DataFrame) -> pl.DataFrame:
    """(season, week) pairs that are regular-season weeks."""
    return (
        games.filter(pl.col("game_type") == "REG")
        .select(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))
        .unique()
    )


def team_game_margins(targets: pl.DataFrame, net: pl.DataFrame) -> pl.DataFrame:
    """Two rows per completed game (one per team): actual EPA margin per play, the margin
    the ratings expected before the game, and the pieces needed to opponent-adjust it.

    `net` is (season, week, team, net, hfa) as of each week (`ratings.net_lookup`).
    """
    t = add_prediction(targets, net, name="expected")
    lk = net.select("season", "week", "team", "net")
    t = t.join(
        lk.rename({"team": "home_team", "net": "home_net"}),
        on=["season", "week", "home_team"],
        how="left",
    ).join(
        lk.rename({"team": "away_team", "net": "away_net"}),
        on=["season", "week", "away_team"],
        how="left",
    )
    hfa = t["expected"] - (t["home_net"] - t["away_net"])  # 0 at neutral sites
    t = t.with_columns(hfa.alias("hfa_term"))
    common = ["season", "week", "game_id", "game_type"]
    home = t.select(
        *common,
        pl.col("home_team").alias("team"),
        pl.col("away_team").alias("opp"),
        pl.col("epa_margin").alias("actual"),
        "expected",
        pl.col("home_net").alias("team_net"),
        pl.col("away_net").alias("opp_net"),
        "hfa_term",
    )
    away = t.select(
        *common,
        pl.col("away_team").alias("team"),
        pl.col("home_team").alias("opp"),
        (-pl.col("epa_margin")).alias("actual"),
        (-pl.col("expected")).alias("expected"),
        pl.col("away_net").alias("team_net"),
        pl.col("home_net").alias("opp_net"),
        (-pl.col("hfa_term")).alias("hfa_term"),
    )
    return pl.concat([home, away]).sort("season", "team", "week")


def _bands(t: pl.DataFrame, regular: pl.DataFrame, q: float, min_seasons: int) -> pl.DataFrame:
    hist = t.join(regular, on=["season", "week"]).drop_nulls("trend_delta")
    rows = []
    for season in sorted(t["season"].unique().to_list()):
        prior = hist.filter(pl.col("season") < season)
        if prior["season"].n_unique() >= min_seasons:
            lo = prior["trend_delta"].quantile(q, "linear")
            hi = prior["trend_delta"].quantile(1 - q, "linear")
        else:
            lo = hi = None
        rows.append({"season": season, "band_low": lo, "band_high": hi})
    return pl.DataFrame(
        rows, schema={"season": pl.Int32, "band_low": pl.Float64, "band_high": pl.Float64}
    )


def trend_table(
    ratings: pl.DataFrame,
    margins: pl.DataFrame,
    games: pl.DataFrame,
    window: int = TREND_WINDOW,
    q: float = BAND_QUANTILE,
    min_seasons: int = MIN_BAND_SEASONS,
) -> pl.DataFrame:
    """The `team_trends` table (without evidence fields): one row per team per as-of week."""
    base = ratings.select("season", "week", "team", "net_epa")
    prev = base.select(
        "season",
        (pl.col("week") + window).cast(pl.Int32).alias("week"),
        "team",
        pl.col("net_epa").alias("net_epa_prev"),
    )
    t = base.join(prev, on=["season", "week", "team"], how="left").with_columns(
        (pl.col("net_epa") - pl.col("net_epa_prev")).alias("trend_delta")
    )
    m = (
        margins.sort("season", "team", "week")
        .with_columns(
            (pl.col("actual") - pl.col("expected"))
            .rolling_mean(window, min_samples=1)
            .over("season", "team")
            .alias("perf_vs_expected"),
            pl.int_range(1, pl.len() + 1)
            .over("season", "team")
            .clip(upper_bound=window)
            .cast(pl.Int32)
            .alias("pve_games"),
        )
        .select(
            "season", "team", pl.col("week").alias("game_week"), "perf_vs_expected", "pve_games"
        )
        .sort("game_week")
    )
    t = (
        t.with_columns((pl.col("week") - 1).alias("_lookup"))
        .sort("_lookup")
        .join_asof(
            m,
            left_on="_lookup",
            right_on="game_week",
            by=["season", "team"],
            strategy="backward",
            check_sortedness=False,  # both sides sorted on the as-of column above
        )
        .drop("_lookup", "game_week")
        .with_columns(pl.col("pve_games").fill_null(0))
    )
    t = t.join(_bands(t, reg_weeks(games), q, min_seasons), on="season", how="left")
    direction = (
        pl.when(pl.col("trend_delta").is_null() | pl.col("band_low").is_null())
        .then(None)
        .when(pl.col("trend_delta") > pl.col("band_high"))
        .then(pl.lit("up"))
        .when(pl.col("trend_delta") < pl.col("band_low"))
        .then(pl.lit("down"))
        .otherwise(pl.lit("stable"))
    )
    return t.with_columns(direction.alias("direction")).sort("season", "week", "team")


DEFAULT_PASS_SHARE = 0.6


def pass_share(rp: pl.DataFrame, keys: pl.DataFrame) -> pl.DataFrame:
    """League share of dropbacks among rated plays **as of** each (season, week) key:
    last season's plays plus this season's plays from weeks before `week`."""
    weekly = (
        rp.group_by("season", "week")
        .agg(pl.len().alias("n"), pl.col("dropback").sum().alias("d"))
        .sort("season", "week")
        .with_columns(
            pl.col("n").cum_sum().over("season").alias("n_cum"),
            pl.col("d").cum_sum().over("season").alias("d_cum"),
        )
    )
    last = (
        weekly.group_by("season")
        .agg(pl.col("n").sum().alias("n_last"), pl.col("d").sum().alias("d_last"))
        .with_columns((pl.col("season") + 1).cast(pl.Int32))
    )
    cum = weekly.select("season", pl.col("week").alias("_wk"), "n_cum", "d_cum").sort("_wk")
    k = (
        keys.select("season", "week")
        .unique()
        .with_columns((pl.col("week") - 1).alias("_lk"))
        .sort("_lk")
        .join_asof(
            cum,
            left_on="_lk",
            right_on="_wk",
            by="season",
            strategy="backward",
            check_sortedness=False,
        )  # both sides sorted on the as-of column above
        .join(last, on="season", how="left")
    )
    n = pl.col("n_cum").fill_null(0) + pl.col("n_last").fill_null(0)
    d = pl.col("d_cum").fill_null(0) + pl.col("d_last").fill_null(0)
    share = pl.when(n > 0).then(d / n).otherwise(DEFAULT_PASS_SHARE)
    return k.select("season", "week", share.alias("pass_share"))


def drivers_table(
    ratings: pl.DataFrame, shares: pl.DataFrame, window: int = TREND_WINDOW, top: int = 3
) -> pl.DataFrame:
    """`team_trend_drivers`: the `top` rating parts that moved most over `window` weeks.

    change = rating move with + meaning better (defense sign flipped, since its rating is
    EPA allowed); contribution = change x the share of plays the part covers, from
    `pass_share` (season, week) as of the same key.
    """
    cols = [c for c, _, _ in DRIVER_PARTS.values()]
    now = ratings.select("season", "week", "team", *cols)
    prev = now.select(
        "season",
        (pl.col("week") + window).cast(pl.Int32).alias("week"),
        "team",
        *[pl.col(c).alias(f"{c}_prev") for c in cols],
    )
    j = now.join(prev, on=["season", "week", "team"]).join(
        shares, on=["season", "week"], how="left"
    )
    parts = []
    for part, (col, sign, kind) in DRIVER_PARTS.items():
        share = pl.col("pass_share") if kind == "pass" else 1 - pl.col("pass_share")
        delta = pl.col(col) - pl.col(f"{col}_prev")
        parts.append(
            j.select(
                "season",
                "week",
                "team",
                pl.lit(part).alias("part"),
                pl.lit(col).alias("rating_col"),
                delta.alias("delta"),
                (delta * sign).alias("change"),
                (delta * sign * share).alias("contribution"),
            )
        )
    long = pl.concat(parts)
    return (
        long.with_columns(
            pl.col("contribution")
            .abs()
            .rank("ordinal", descending=True)
            .over("season", "week", "team")
            .cast(pl.Int32)
            .alias("rank")
        )
        .filter(pl.col("rank") <= top)
        .with_columns(
            pl.when(pl.col("change") >= 0)
            .then(pl.lit("better"))
            .otherwise(pl.lit("worse"))
            .alias("effect")
        )
        .select(
            "season",
            "week",
            "team",
            "rank",
            "part",
            "rating_col",
            "delta",
            "change",
            "contribution",
            "effect",
        )
        .sort("season", "week", "team", "rank")
    )


def trend_validation_frame(
    trends: pl.DataFrame,
    margins: pl.DataFrame,
    games: pl.DataFrame,
    window: int = TREND_WINDOW,
    min_games: int = TREND_WINDOW - 1,
) -> pl.DataFrame:
    """Rows for testing whether trends predict the next `window` weeks beyond the rating.

    One row per regular-season as-of key (season, week w) with a `trend_delta`. Target
    `next_perf` = mean opponent-adjusted EPA margin per play over the team's regular-season
    games in weeks [w, w + window) (actual margin + opponent's pre-game net rating - the
    home-field term), i.e. how well the team actually played, independent of its own
    rating. Keys with fewer than `min_games` such games (a bye plus the season's end) are
    dropped.
    """
    reg = margins.filter(pl.col("game_type") == "REG").select(
        "season",
        "team",
        pl.col("week").alias("game_week"),
        (pl.col("actual") + pl.col("opp_net") - pl.col("hfa_term")).alias("adj"),
    )
    keys = trends.join(reg_weeks(games), on=["season", "week"]).drop_nulls("trend_delta")
    ahead = (
        keys.select("season", "week", "team")
        .join(reg, on=["season", "team"])
        .filter(
            (pl.col("game_week") >= pl.col("week"))
            & (pl.col("game_week") < pl.col("week") + window)
        )
        .group_by("season", "week", "team")
        .agg(pl.col("adj").mean().alias("next_perf"), pl.len().alias("next_games"))
        .filter(pl.col("next_games") >= min_games)
    )
    rows = keys.join(ahead, on=["season", "week", "team"]).select(
        "season",
        "week",
        "team",
        pl.col("net_epa").alias("net_now"),
        "trend_delta",
        "perf_vs_expected",
        "next_perf",
    )
    return rows.drop_nulls().sort("season", "week", "team")
