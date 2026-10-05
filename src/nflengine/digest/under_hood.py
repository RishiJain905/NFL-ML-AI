"""Last week under the hood: rule-based selection of stat items for the digest (P04).

The digest for week N looks back at week N-1 with tracking-style stats (documentation/06,
section 4) and picks 3-5 items: a player whose number moved a lot against his own norm
(`riser` / `faller`), or who sat at the top or bottom of the league last week (`standout`).

Metrics (`METRICS`), each with a minimum volume for last week's pool:
- NGS receiving (WR / TE / RB pools): `avg_separation`, `avg_cushion` (5+ targets, the
  NGS weekly floor), `avg_yac_above_expectation` (3+ catches).
- NGS rushing: `rush_yards_over_expected_per_att` (10+ carries, the NGS weekly floor).
- NGS passing: `avg_time_to_throw`, `completion_percentage_above_expectation` (20+
  attempts; CPOE is in percentage points).
- PFR advanced: `pressure_rate` faced by QBs (`pfr_pass.times_pressured_pct`, a 0-1 fraction
  in every season; the rare value above 1 is a data error and dropped; volume = dropbacks
  from play-by-play, 20+), and `pressures` by pass rushers (`pfr_def.def_pressures` of
  players whose `players.position_group` is DL or LB, listed with 3+ pressures).
- FTN charting joined to play-by-play dropbacks: `blitz_rate_faced` (share of dropbacks with
  `n_blitzers > 0`) and `play_action_rate`, per QB (20+ charted dropbacks).
Only regular-season weekly rows count (NGS week-0 season totals are dropped). Every value is
aggregated to one row per player-week, volume-weighted (pressures: plain per game).

Norm (the player's own baseline, volume-weighted): his qualifying games of this season
before week N-1 when there are at least 2 (`norm_source = 'season'`). With 1 such game and 2+
qualifying games last season, the season game is blended with last season's average counted
as `PRIOR_GAMES` games (`'season+last_season'`); with none, last season alone
(`'last_season'`). Otherwise there is no norm and the player can only be a standout. For
`pressures` every game the player has a `pfr_def` row for counts toward the norm (zeros too).

z = (last week - norm) / SD, where SD is the league-wide SD of single-week values for the
metric among qualifying player-weeks (the norm's volume floor) of the same pool (all pools
when a pool has fewer than `MIN_SD_ROWS` rows), over the current season's weeks <= N-1 and
the two seasons before it.

Score (deterministic; ties broken by player_id, then metric). Both parts mix a rank and a
capped z so that heavy-tailed metrics (YAC per catch, pressure counts) don't crowd out the
rest; `normal(r, n) = inv_cdf(1 - (r - 0.5) / n)` turns rank r of n into a z-like number:
- change strength c = mean(normal(change rank, pool members with a norm), min(|z|, `Z_CAP`))
  x n / (n + 1), n = norm games (a short baseline is trusted less); only when
  |z| >= `Z_MIN`. The change rank counts from the top for a rise, the bottom for a drop.
- standout strength s = `STANDOUT_WEIGHT` x mean(normal(r, pool size),
  min(|value - pool mean| / SD, `Z_CAP`)), r = rank from the nearer end of the pool; only when
  r <= `STANDOUT_RANK` (count metrics: top end only).
- score = metric weight x (max(c, s) + 0.25 x min(c, s)), x `FOLLOWED_BOOST` for followed
  teams. kind = riser / faller (by the sign of the change, not by good / bad) when c > 0 and
  c >= s, else standout.

Selection: best score first, at most 1 item per player and 2 per metric. Items need score
>= `MIN_SCORE`; below `min_items`, items down to `FLOOR` fill in. When every pick comes from
one source and a candidate from another source clears `FLOOR`, it is added (or replaces the
last pick when the list is full). Week 1 and weeks without data give an empty frame.

Leakage: the pure core (`build_under_hood`) first drops every row from week N on (and the
current season's week 0) with `before_expr`, so norms, SDs and pools only see weeks <= N-1
(tested by scrambling future rows). PFR and FTN for week N-1 may be missing on a Tuesday run:
that source then contributes nothing.

`rank_note` is plain text with integers only (no formatted metric values), e.g. "highest
among WRs with 5+ targets" or "2nd-biggest jump over his own average among QBs with 20+
attempts".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist

import duckdb
import polars as pl

from nflengine.curate.teams import normalize_team, team_expr
from nflengine.features.asof import AsOf, before_expr
from nflengine.paths import DataPaths, ensure_data_root


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    source: str  # NGS | PFR | FTN (rows come from `metric_rows`)
    unit: str
    higher_is_better: bool | None
    volume_label: str
    pool_min: int  # minimum volume to be in last week's pool (and in the SD rows)
    norm_min: int | None = None  # minimum volume for a norm game; None = pool_min
    weighted: bool = True  # volume-weighted averages (False: one weight per game)
    weight: float = 1.0  # score multiplier

    @property
    def base_min(self) -> int:
        return self.pool_min if self.norm_min is None else self.norm_min


# fmt: off
METRICS: tuple[Metric, ...] = (
    Metric("avg_separation", "average separation", "NGS", "yards", True, "targets", 5),
    Metric("avg_cushion", "average cushion", "NGS", "yards", None, "targets", 5),
    Metric("avg_yac_above_expectation", "YAC above expectation", "NGS", "yards_per_catch",
           True, "catches", 3),
    Metric("rush_yards_over_expected_per_att", "rush yards over expected per carry", "NGS",
           "yards_per_carry", True, "carries", 10),
    Metric("avg_time_to_throw", "average time to throw", "NGS", "seconds", None, "attempts",
           20),
    Metric("completion_percentage_above_expectation", "completion % above expectation",
           "NGS", "pct_points", True, "attempts", 20),
    Metric("pressure_rate", "pressure rate faced", "PFR", "rate", False, "dropbacks", 20),
    Metric("pressures", "pressures", "PFR", "count", True, "pressures", 3, norm_min=0,
           weighted=False),
    Metric("blitz_rate_faced", "blitz rate faced", "FTN", "rate", None, "dropbacks", 20),
    Metric("play_action_rate", "play-action rate", "FTN", "rate", None, "dropbacks", 20),
)
# fmt: on
METRIC_BY_KEY = {m.key: m for m in METRICS}
POOL_PLURAL = {"WR": "WRs", "TE": "TEs", "RB": "RBs", "QB": "QBs", "PR": "pass rushers"}
PASS_RUSH_GROUPS = ("DL", "LB")

PRIOR_GAMES = 2  # weight of last season's average when this season has a single game
MIN_NORM_GAMES = 2
MIN_SD_ROWS = 30
Z_MIN = 1.0
Z_CAP = 3.0
STANDOUT_WEIGHT = 0.85  # change vs the player's own norm is the section's main signal
STANDOUT_RANK = 3
MIN_SCORE = 1.5
FLOOR = 1.0
FOLLOWED_BOOST = 1.15
MAX_PER_METRIC = 2
HISTORY_SEASONS = 2  # prior seasons used for SDs (and last season for norms)

OUTPUT_SCHEMA: dict[str, pl.DataType] = {
    "rank": pl.Int64(),
    "player_id": pl.String(),
    "player": pl.String(),
    "team": pl.String(),
    "position": pl.String(),
    "metric": pl.String(),
    "label": pl.String(),
    "unit": pl.String(),
    "higher_is_better": pl.Boolean(),
    "last_week": pl.Float64(),
    "norm": pl.Float64(),
    "norm_games": pl.Int64(),
    "norm_source": pl.String(),
    "change": pl.Float64(),
    "z": pl.Float64(),
    "kind": pl.String(),
    "pool_rank": pl.Int64(),
    "pool_size": pl.Int64(),
    "pool_desc": pl.String(),
    "rank_note": pl.String(),
    "volume": pl.Int64(),
    "volume_label": pl.String(),
    "source": pl.String(),
    "source_week": pl.Int64(),
    "score": pl.Float64(),
}

ROW_COLS = ["metric", "season", "week", "player_id", "name", "team", "pool", "position"]
ROW_COLS += ["value", "volume"]

# Columns read per curated table by the loader.
TABLE_COLS: dict[str, list[str]] = {
    "ngs_receiving": ["targets", "receptions", "avg_separation", "avg_cushion"]
    + ["avg_yac_above_expectation"],
    "ngs_rushing": ["rush_attempts", "rush_yards_over_expected_per_att"],
    "ngs_passing": ["attempts", "avg_time_to_throw", "completion_percentage_above_expectation"],
    "pfr_pass": ["game_id", "game_type", "team", "gsis_id", "pfr_player_name"]
    + ["times_pressured_pct"],
    "pfr_def": ["game_id", "game_type", "team", "gsis_id", "pfr_player_name", "def_pressures"],
    "ftn_plays": ["nflverse_game_id", "nflverse_play_id", "is_play_action", "n_blitzers"],
}
NGS_COLS = ["season_type", "player_gsis_id", "player_display_name", "player_position", "team"]
NGS_COLS += ["is_season_total"]
PLAY_COLS = ["game_id", "play_id", "season", "week", "season_type", "posteam", "passer_id"]
PLAY_COLS += ["passer", "qb_dropback", "play_type", "two_point_attempt"]
PLAYER_COLS = ["gsis_id", "display_name", "position", "position_group"]


def empty_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=OUTPUT_SCHEMA)


# ---- metric rows (long format) --------------------------------------------------------
def _long(df: pl.DataFrame, metric: str, value: pl.Expr, volume: pl.Expr) -> pl.DataFrame:
    return df.select(
        pl.lit(metric).alias("metric"),
        pl.col("season").cast(pl.Int64),
        pl.col("week").cast(pl.Int64),
        pl.col("player_id").cast(pl.String),
        pl.col("name").cast(pl.String),
        pl.col("team").cast(pl.String),
        pl.col("pool").cast(pl.String),
        pl.col("position").cast(pl.String),
        value.cast(pl.Float64).alias("value"),
        volume.cast(pl.Float64).alias("volume"),
    )


def _regular(df: pl.DataFrame, col: str, post_season: int | None) -> pl.DataFrame:
    """Regular-season rows; in a playoff digest (P10, `post_season`) also that season's
    playoff rows, so "last week" can be the previous playoff round."""
    keep = pl.col(col) == "REG"
    if post_season is not None:
        keep = keep | (pl.col("season") == post_season)
    return df.filter(keep)


def _ngs(df: pl.DataFrame, pool: pl.Expr, post_season: int | None = None) -> pl.DataFrame:
    if "is_season_total" in df.columns:
        df = df.filter(~pl.col("is_season_total").fill_null(False))
    if "season_type" in df.columns:
        df = _regular(df, "season_type", post_season)
    return df.filter(pl.col("week") >= 1).with_columns(
        pl.col("player_gsis_id").alias("player_id"),
        pl.col("player_display_name").alias("name"),
        pool.alias("pool"),
        pl.col("player_position").alias("position"),
    )


def _dropback_plays(plays: pl.DataFrame, post_season: int | None = None) -> pl.DataFrame:
    """Regular-season dropbacks (passes, sacks, scrambles) without two-point tries (plus
    `post_season`'s playoff dropbacks in a playoff digest)."""
    df = plays
    if "season_type" in df.columns:
        df = _regular(df, "season_type", post_season)
    return df.filter(
        (pl.col("qb_dropback") == 1)
        & pl.col("play_type").is_in(["pass", "run"])
        & (pl.col("two_point_attempt").fill_null(0) == 0)
        & pl.col("passer_id").is_not_null()
    )


def metric_rows(tables: Mapping[str, pl.DataFrame], post_season: int | None = None) -> pl.DataFrame:
    """One row per metric x player x game (before week aggregation), all sources. Regular
    season only, plus `post_season`'s playoff games in a playoff digest (P10)."""
    parts: list[pl.DataFrame] = []
    if (df := tables.get("ngs_receiving")) is not None and df.height:
        rec = _ngs(df, pl.col("player_position").replace({"FB": "RB", "HB": "RB"}), post_season)
        targets, catches = pl.col("targets"), pl.col("receptions")
        parts += [
            _long(rec, "avg_separation", pl.col("avg_separation"), targets),
            _long(rec, "avg_cushion", pl.col("avg_cushion"), targets),
            _long(rec, "avg_yac_above_expectation", pl.col("avg_yac_above_expectation"), catches),
        ]
    if (df := tables.get("ngs_rushing")) is not None and df.height:
        rush = _ngs(df, pl.lit("RB"), post_season)
        col = "rush_yards_over_expected_per_att"
        parts.append(_long(rush, col, pl.col(col), pl.col("rush_attempts")))
    if (df := tables.get("ngs_passing")) is not None and df.height:
        ps = _ngs(df, pl.lit("QB"), post_season)
        for col in ("avg_time_to_throw", "completion_percentage_above_expectation"):
            parts.append(_long(ps, col, pl.col(col), pl.col("attempts")))

    plays = tables.get("plays")
    dropbacks = _dropback_plays(plays, post_season) if plays is not None and plays.height else None
    if (df := tables.get("pfr_pass")) is not None and df.height and dropbacks is not None:
        per_game = dropbacks.group_by("game_id", "passer_id").agg(pl.len().alias("dropbacks"))
        pct = pl.col("times_pressured_pct")
        pp = (
            _regular(df, "game_type", post_season)
            .filter(pct.is_between(0.0, 1.0))
            .join(per_game, left_on=["game_id", "gsis_id"], right_on=["game_id", "passer_id"])
            .with_columns(
                pl.col("gsis_id").alias("player_id"),
                pl.col("pfr_player_name").alias("name"),
                pl.lit("QB").alias("pool"),
                pl.lit(None, pl.String).alias("position"),
            )
        )
        parts.append(_long(pp, "pressure_rate", pct, pl.col("dropbacks")))
    players = tables.get("players")
    if (df := tables.get("pfr_def")) is not None and df.height and players is not None:
        rushers = players.filter(pl.col("position_group").is_in(PASS_RUSH_GROUPS))
        pd_ = (
            _regular(df, "game_type", post_season)
            .filter(pl.col("def_pressures").is_not_null())
            .join(rushers.select("gsis_id", "position"), on="gsis_id")
            .with_columns(
                pl.col("gsis_id").alias("player_id"),
                pl.col("pfr_player_name").alias("name"),
                pl.lit("PR").alias("pool"),
            )
        )
        pressures = pl.col("def_pressures")
        parts.append(_long(pd_, "pressures", pressures, pressures))
    if (ftn := tables.get("ftn_plays")) is not None and ftn.height and dropbacks is not None:
        if "passer" not in dropbacks.columns:
            dropbacks = dropbacks.with_columns(pl.lit(None, pl.String).alias("passer"))
        charted = dropbacks.with_columns(pl.col("play_id").cast(pl.Int64)).join(
            ftn.select(
                pl.col("nflverse_game_id").alias("game_id"),
                pl.col("nflverse_play_id").cast(pl.Int64).alias("play_id"),
                "is_play_action",
                "n_blitzers",
            ),
            on=["game_id", "play_id"],
        )
        fq = (
            charted.group_by("season", "week", "game_id", "passer_id", "posteam")
            .agg(
                pl.len().alias("dropbacks"),
                (pl.col("n_blitzers").fill_null(0) > 0).mean().alias("blitz_rate_faced"),
                pl.col("is_play_action").fill_null(False).mean().alias("play_action_rate"),
                pl.col("passer").first().alias("name"),
            )
            .with_columns(
                pl.col("passer_id").alias("player_id"),
                pl.col("posteam").alias("team"),
                pl.lit("QB").alias("pool"),
                pl.lit(None, pl.String).alias("position"),
            )
        )
        for col in ("blitz_rate_faced", "play_action_rate"):
            parts.append(_long(fq, col, pl.col(col), pl.col("dropbacks")))

    if not parts:
        return pl.DataFrame(schema={c: pl.String for c in ROW_COLS}).cast(
            {"season": pl.Int64, "week": pl.Int64, "value": pl.Float64, "volume": pl.Float64}
        )
    rows = pl.concat(parts).filter(
        pl.col("value").is_not_null()
        & pl.col("value").is_finite()
        & pl.col("player_id").is_not_null()
        & pl.col("volume").is_not_null()
    )
    return rows.with_columns(team_expr("team"))


def _player_weeks(rows: pl.DataFrame) -> pl.DataFrame:
    """Aggregate to one row per metric x player x week (volume-weighted value)."""
    spec = pl.DataFrame(
        {"metric": [m.key for m in METRICS], "weighted": [m.weighted for m in METRICS]}
    )
    w = pl.when(pl.col("weighted")).then(pl.col("volume")).otherwise(1.0)
    return (
        rows.join(spec, on="metric")
        .with_columns(w.alias("w"))
        .sort("metric", "player_id", "season", "week", "team", "value")
        .group_by("metric", "player_id", "season", "week", maintain_order=True)
        .agg(
            ((pl.col("value") * pl.col("w")).sum() / pl.col("w").sum()).alias("value_w"),
            pl.col("value").mean().alias("value_m"),
            pl.col("volume").sum(),
            pl.col("w").sum(),
            pl.col("name", "team", "pool", "position").drop_nulls().first(),
        )
        .with_columns(
            pl.when(pl.col("w") > 0)
            .then(pl.col("value_w"))
            .otherwise(pl.col("value_m"))
            .alias("value")
        )
        .drop("value_w", "value_m")
    )


# ---- candidates and scoring -------------------------------------------------------------
def _spec_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "metric": [m.key for m in METRICS],
            "pool_min": [float(m.pool_min) for m in METRICS],
            "base_min": [float(m.base_min) for m in METRICS],
            "metric_weight": [m.weight for m in METRICS],
            "source": [m.source for m in METRICS],
            "is_count": [m.unit == "count" for m in METRICS],
        }
    )


def _norms(base: pl.DataFrame, prefix: str) -> pl.DataFrame:
    return base.group_by("metric", "player_id").agg(
        ((pl.col("value") * pl.col("w")).sum() / pl.col("w").sum()).alias(f"{prefix}_val"),
        pl.len().alias(f"{prefix}_games"),
    )


def _pool(rows: pl.DataFrame, season: int, week: int) -> pl.DataFrame:
    """Every pool member of week - 1 with its norm, change, z and ranks."""
    lw = week - 1
    pw = _player_weeks(rows).join(_spec_frame(), on="metric")
    base = pw.filter(pl.col("volume") >= pl.col("base_min"))
    pooled = pw.filter(pl.col("volume") >= pl.col("pool_min"))
    last = pooled.filter((pl.col("season") == season) & (pl.col("week") == lw))
    if last.is_empty():
        return last

    sd_pool = base.group_by("metric", "pool").agg(
        pl.col("value").std().alias("sd_pool"), pl.len().alias("n_pool")
    )
    pool_mean = pooled.group_by("metric", "pool").agg(pl.col("value").mean().alias("pool_mean"))
    sd_all = base.group_by("metric").agg(
        pl.col("value").std().alias("sd_all"), pl.len().alias("n_all")
    )
    cur = _norms(base.filter((pl.col("season") == season) & (pl.col("week") < lw)), "cur")
    prev = _norms(base.filter(pl.col("season") == season - 1), "prev")

    g = pl.col("cur_games").fill_null(0)
    n_prev = pl.col("prev_games").fill_null(0)
    has_prev = n_prev >= MIN_NORM_GAMES
    blend = (pl.col("cur_val") + PRIOR_GAMES * pl.col("prev_val")) / (1 + PRIOR_GAMES)
    sd = (
        pl.when(pl.col("n_pool") >= MIN_SD_ROWS)
        .then(pl.col("sd_pool"))
        .when(pl.col("n_all") >= MIN_SD_ROWS)
        .then(pl.col("sd_all"))
    )
    df = (
        last.join(sd_pool, on=["metric", "pool"], how="left")
        .join(pool_mean, on=["metric", "pool"], how="left")
        .join(sd_all, on="metric", how="left")
        .join(cur, on=["metric", "player_id"], how="left")
        .join(prev, on=["metric", "player_id"], how="left")
        .with_columns(
            pl.when(g >= MIN_NORM_GAMES)
            .then(pl.col("cur_val"))
            .when((g == 1) & has_prev)
            .then(blend)
            .when((g == 0) & has_prev)
            .then(pl.col("prev_val"))
            .alias("norm"),
            pl.when(g >= MIN_NORM_GAMES)
            .then(pl.lit("season"))
            .when((g == 1) & has_prev)
            .then(pl.lit("season+last_season"))
            .when((g == 0) & has_prev)
            .then(pl.lit("last_season"))
            .alias("norm_source"),
            pl.when(g >= MIN_NORM_GAMES)
            .then(g)
            .when(has_prev)
            .then(g + n_prev)
            .cast(pl.Int64)
            .alias("norm_games"),
            sd.alias("sd"),
        )
        .with_columns((pl.col("value") - pl.col("norm")).alias("change"))
        .with_columns((pl.col("change") / pl.col("sd")).alias("z"))
    )
    over = ["metric", "pool"]
    return df.with_columns(
        pl.col("value").rank("min", descending=True).over(over).cast(pl.Int64).alias("pool_rank"),
        pl.col("value").rank("min").over(over).cast(pl.Int64).alias("low_rank"),
        pl.len().over(over).cast(pl.Int64).alias("pool_size"),
        pl.col("change").rank("min", descending=True).over(over).cast(pl.Int64).alias("up_rank"),
        pl.col("change").rank("min").over(over).cast(pl.Int64).alias("down_rank"),
        pl.col("change").count().over(over).cast(pl.Int64).alias("n_norm"),
    )


def _candidates(rows: pl.DataFrame, season: int, week: int) -> pl.DataFrame:
    """Pool members of week - 1 that are notable (a kind and a score)."""
    pool = _pool(rows, season, week)
    return pool if pool.is_empty() else _score(pool)


def _normal_scores(ranks: pl.Series, sizes: pl.Series) -> pl.Series:
    """inv_cdf(1 - (r - 0.5) / n): how extreme rank r of n is, on a z-like scale."""
    nd = NormalDist()
    return pl.Series(
        [
            nd.inv_cdf(1 - (r - 0.5) / n) if r is not None and n and n > 1 else 0.0
            for r, n in zip(ranks.to_list(), sizes.to_list(), strict=True)
        ],
        dtype=pl.Float64,
    )


def _score(df: pl.DataFrame) -> pl.DataFrame:
    top = pl.col("pool_rank") <= pl.col("low_rank")
    side = pl.when(top | pl.col("is_count")).then(pl.lit("top")).otherwise(pl.lit("bottom"))
    side_rank = pl.when(side == "top").then(pl.col("pool_rank")).otherwise(pl.col("low_rank"))
    change_rank = pl.when(pl.col("change") > 0).then(pl.col("up_rank")).otherwise("down_rank")
    df = df.with_columns(
        side.alias("side"), side_rank.alias("side_rank"), change_rank.alias("change_rank")
    )
    df = df.with_columns(
        _normal_scores(df["side_rank"], df["pool_size"]).alias("side_score"),
        _normal_scores(df["change_rank"], df["n_norm"]).alias("change_score"),
    )
    abs_z = pl.col("z").abs()
    c = (
        pl.when(pl.col("norm").is_not_null() & (abs_z >= Z_MIN))
        .then(
            0.5
            * (pl.col("change_score") + pl.min_horizontal(abs_z, pl.lit(Z_CAP)))
            * pl.col("norm_games")
            / (pl.col("norm_games") + 1)
        )
        .otherwise(0.0)
        .fill_null(0.0)
    )
    vz = ((pl.col("value") - pl.col("pool_mean")) / pl.col("sd")).abs().fill_null(0.0)
    s = (
        pl.when(pl.col("side_rank") <= STANDOUT_RANK)
        .then(STANDOUT_WEIGHT * 0.5 * (pl.col("side_score") + pl.min_horizontal(vz, pl.lit(Z_CAP))))
        .otherwise(0.0)
    )
    df = df.with_columns(c.alias("c"), s.alias("s"))
    hi = pl.max_horizontal("c", "s")
    lo = pl.min_horizontal("c", "s")
    kind = (
        pl.when((pl.col("c") > 0) & (pl.col("c") >= pl.col("s")))
        .then(pl.when(pl.col("change") > 0).then(pl.lit("riser")).otherwise(pl.lit("faller")))
        .when(pl.col("s") > 0)
        .then(pl.lit("standout"))
    )
    return df.with_columns(
        (pl.col("metric_weight") * (hi + 0.25 * lo)).alias("score"), kind.alias("kind")
    ).filter(pl.col("kind").is_not_null())


# ---- text --------------------------------------------------------------------------------
def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _prefix(n: int) -> str:
    return "" if n == 1 else f"{ordinal(n)}-"


def pool_desc(metric: Metric, pool: str) -> str:
    return f"{POOL_PLURAL.get(pool, pool + 's')} with {metric.pool_min}+ {metric.volume_label}"


def rank_note(row: Mapping, metric: Metric) -> str:
    desc = pool_desc(metric, row["pool"])
    if row["kind"] == "riser":
        return f"{_prefix(row['up_rank'])}biggest jump over his own average among {desc}"
    if row["kind"] == "faller":
        return f"{_prefix(row['down_rank'])}biggest drop below his own average among {desc}"
    count = metric.unit == "count"
    word = ("most" if count else "highest") if row["side"] == "top" else "lowest"
    if row["side"] != "top" and count:
        word = "fewest"
    return f"{_prefix(row['side_rank'])}{word} among {desc}"


# ---- selection ---------------------------------------------------------------------------
def _select(cands: pl.DataFrame, max_items: int, min_items: int) -> list[dict]:
    ordered = cands.sort(["score", "player_id", "metric"], descending=[True, False, False])
    rows = ordered.to_dicts()

    def fits(row: dict, picks: list[dict]) -> bool:
        if any(p["player_id"] == row["player_id"] for p in picks):
            return False
        return sum(p["metric"] == row["metric"] for p in picks) < MAX_PER_METRIC

    picks: list[dict] = []
    for row in rows:
        if len(picks) >= max_items or row["score"] < FLOOR:
            break
        if row["score"] < MIN_SCORE and len(picks) >= min_items:
            break
        if fits(row, picks):
            picks.append(row)

    sources = {p["source"] for p in picks}
    if len(sources) == 1:
        keep = picks if len(picks) < max_items else picks[:-1]
        alt = next(
            (
                r
                for r in rows
                if r["source"] not in sources and r["score"] >= FLOOR and fits(r, keep)
            ),
            None,
        )
        if alt is not None:
            picks = [*keep, alt]
    return sorted(picks, key=lambda r: (-r["score"], r["player_id"], r["metric"]))


# ---- public API ----------------------------------------------------------------------------
def build_under_hood(
    season: int,
    week: int,
    tables: Mapping[str, pl.DataFrame],
    *,
    max_items: int = 5,
    min_items: int = 3,
    followed: Sequence[str] = (),
    playoffs: bool = False,
    teams: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Pure core: curated frames in, selected items out (schema `OUTPUT_SCHEMA`).

    `tables` may hold `ngs_receiving`, `ngs_rushing`, `ngs_passing`, `pfr_pass`, `pfr_def`,
    `ftn_plays`, `plays` and `players` (any may be missing). Rows from week `week` on are
    dropped first, so nothing at or after the digest week can change the output.

    A playoff digest (P10, `playoffs`): the season's earlier playoff games count too (the
    Divisional round's "last week" is the Wild Card round), and `teams` keeps only players
    of the teams still playing (this week's slate).
    """
    if week < 2 or max_items < 1:
        return empty_frame()
    key = AsOf(season, week)
    visible = {
        name: df.filter(before_expr(key) & (pl.col("season") >= season - HISTORY_SEASONS))
        if {"season", "week"} <= set(df.columns)
        else df
        for name, df in tables.items()
    }
    rows = metric_rows(visible, season if playoffs else None)
    if rows.is_empty():
        return empty_frame()
    cands = _candidates(rows, season, week)
    if cands.is_empty():
        return empty_frame()

    fav = {normalize_team(t.strip().upper()) for t in followed}
    boost = pl.when(pl.col("team").is_in(list(fav))).then(FOLLOWED_BOOST).otherwise(1.0)
    cands = cands.with_columns(pl.col("score") * boost)
    players = visible.get("players")
    if players is not None and players.height:
        names = players.select(
            pl.col("gsis_id").alias("player_id"),
            pl.col("display_name").alias("display_name"),
            pl.col("position").alias("player_position"),
        ).unique("player_id", keep="first")
        cands = cands.join(names, on="player_id", how="left")
    else:
        cands = cands.with_columns(
            pl.lit(None, pl.String).alias("display_name"),
            pl.lit(None, pl.String).alias("player_position"),
        )
    cands = cands.filter(pl.col("team").is_not_null())
    if teams is not None:
        alive = [normalize_team(t.strip().upper()) for t in teams]
        cands = cands.filter(pl.col("team").is_in(alive))

    picks = _select(cands, max_items, min_items)
    if not picks:
        return empty_frame()
    out = []
    for i, row in enumerate(picks, start=1):
        m = METRIC_BY_KEY[row["metric"]]
        out.append(
            {
                "rank": i,
                "player_id": row["player_id"],
                "player": row["display_name"] or row["name"],
                "team": row["team"],
                "position": row["position"] or row["player_position"] or row["pool"],
                "metric": m.key,
                "label": m.label,
                "unit": m.unit,
                "higher_is_better": m.higher_is_better,
                "last_week": row["value"],
                "norm": row["norm"],
                "norm_games": row["norm_games"],
                "norm_source": row["norm_source"],
                "change": row["change"],
                "z": row["z"],
                "kind": row["kind"],
                "pool_rank": row["pool_rank"],
                "pool_size": row["pool_size"],
                "pool_desc": pool_desc(m, row["pool"]),
                "rank_note": rank_note(row, m),
                "volume": round(row["volume"]),
                "volume_label": m.volume_label,
                "source": m.source,
                "source_week": week - 1,
                "score": row["score"],
            }
        )
    return pl.DataFrame(out, schema=OUTPUT_SCHEMA)


def load_tables(
    season: int, week: int, paths: DataPaths, playoffs: bool = False
) -> dict[str, pl.DataFrame]:
    """Curated rows the selection needs: seasons season-2..season, weeks before `week`
    (regular-season plays, plus this season's playoff plays in a playoff digest)."""
    con = duckdb.connect(str(paths.curated / "nfl.duckdb"), read_only=True)
    try:
        have = _table_names(con)
        window = "season BETWEEN ? AND ? AND (season < ? OR (week >= 1 AND week < ?))"
        params = [season - HISTORY_SEASONS, season, season, week]
        tables: dict[str, pl.DataFrame] = {}
        for name, cols in TABLE_COLS.items():
            if name not in have:
                continue
            extra = NGS_COLS if name.startswith("ngs_") else []
            select = ", ".join(dict.fromkeys(["season", "week", *extra, *cols]))
            sql = f"SELECT {select} FROM {name} WHERE {window}"
            tables[name] = con.execute(sql, params).pl()
        if "plays" in have:
            kind = "(season_type = 'REG' OR season = ?)" if playoffs else "season_type = 'REG'"
            sql = (
                f"SELECT {', '.join(PLAY_COLS)} FROM plays WHERE {window} "
                f"AND {kind} AND qb_dropback = 1 AND play_type IN ('pass', 'run')"
            )
            tables["plays"] = con.execute(sql, params + ([season] if playoffs else [])).pl()
        if "players" in have:
            tables["players"] = con.execute(f"SELECT {', '.join(PLAYER_COLS)} FROM players").pl()
    finally:
        con.close()
    return tables


def select_under_hood(
    season: int,
    week: int,
    paths: DataPaths | None = None,
    *,
    max_items: int = 5,
    min_items: int = 3,
    followed: Sequence[str] = (),
    playoffs: bool = False,
    teams: Sequence[str] | None = None,
) -> pl.DataFrame:
    """The "Last week under the hood" items for the digest of `week` (looks at week - 1);
    `playoffs` / `teams`: see `build_under_hood`."""
    if week < 2:
        return empty_frame()
    paths = paths or ensure_data_root()
    tables = load_tables(season, week, paths, playoffs=playoffs)
    return build_under_hood(
        season,
        week,
        tables,
        max_items=max_items,
        min_items=min_items,
        followed=followed,
        playoffs=playoffs,
        teams=teams,
    )


def source_weeks(season: int, paths: DataPaths | None = None) -> dict[str, int | None]:
    """Latest week present per source ('NGS', 'PFR', 'FTN') for the season, playoff weeks
    included (P10, Sol review: regular season only, the conference and Super Bowl weeks read
    2-3 weeks "behind" and raised false stale-source alerts). NGS numbers the Super Bowl
    one week after nflverse (23 vs 22 since 2021, 22 vs 21 before): counted as nflverse's."""
    paths = paths or ensure_data_root()
    ngs = (
        "SELECT max(CASE WHEN season_type = 'POST' "
        "AND week = (CASE WHEN season >= 2021 THEN 23 ELSE 22 END) THEN week - 1 ELSE week END) "
        "FROM {t} WHERE season = ? AND season_type IN ('REG', 'POST') AND week >= 1 "
        "AND NOT coalesce(is_season_total, false)"
    )
    pfr = "SELECT max(week) FROM {t} WHERE season = ? AND game_type <> 'SBBYE'"
    ftn = (
        "SELECT max(f.week) FROM ftn_plays f JOIN games g ON g.game_id = f.nflverse_game_id "
        "WHERE g.season = ? AND g.game_type <> 'SBBYE'"
    )
    # source -> [(tables the query needs, query)]
    queries: dict[str, list[tuple[tuple[str, ...], str]]] = {
        "NGS": [((t,), ngs.format(t=t)) for t in ("ngs_receiving", "ngs_rushing", "ngs_passing")],
        "PFR": [((t,), pfr.format(t=t)) for t in ("pfr_pass", "pfr_def")],
        "FTN": [(("ftn_plays", "games"), ftn)],
    }
    con = duckdb.connect(str(paths.curated / "nfl.duckdb"), read_only=True)
    try:
        have = _table_names(con)
        out: dict[str, int | None] = {}
        for source, items in queries.items():
            weeks = []
            for needed, sql in items:
                if not set(needed) <= have:
                    continue
                value = con.execute(sql, [season]).fetchone()
                if value and value[0] is not None:
                    weeks.append(int(value[0]))
            out[source] = max(weeks) if weeks else None
    finally:
        con.close()
    return out


def _table_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
