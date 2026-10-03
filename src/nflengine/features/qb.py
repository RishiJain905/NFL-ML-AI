"""QB status for the game model (documentation/04 -> B. Game model -> Features).

Team ratings (P02) come from the team's own plays this season plus a preseason prior. When
the QB who will start differs from the QBs who produced those plays, the rating is off in
a predictable direction. This module measures that gap, in EPA per dropback:

    qb_adj = value(expected starter) - baseline(value of the team's recent starters)

Positive when a better QB starts than the ratings saw (a star back from injury), negative
for a backup; about zero when the same QB started all along.

Definitions, for an as-of key (season s, week w) (D44: only weeks strictly before w):
- **Dropbacks:** plays with `qb_dropback == 1`, `play_type` pass or run (scrambles and
  sacks included), no two-point tries, a `passer_id` (nflfastR's dropback player) and a
  `qb_epa`. Aggregated per (game, team, player) by `qb_dropbacks`.
- **Time index:** `t = (season - 2000) * 22 + week`, continuous across seasons.
- **QB value:** recency-weighted EPA per dropback over the player's dropbacks before the
  key, shrunk toward replacement level:
  `(sum w * epa + k * prior_value) / (sum w * dropbacks + k)`, with
  `w = 0.5 ** ((t_key - t) / half_life_weeks)`. `qb_exp` = sum w * dropbacks.
- **Game starter:** the QB with the most dropbacks for the team in that game (ties: the
  listed starter, then the lower id), so an early in-game injury counts as a change
  (2023 NYJ week 1: Rodgers listed, Wilson took the dropbacks).
- **Tuesday expected starter** (`qb_last_id`; `qb_source` says which rule picked it):
  - `last_game`: the starter of the team's latest played game before the key (any season);
  - week 1: the week-1 depth-chart QB1 when a Tuesday run could have seen it
    (`depth_chart_w1`; D44: dated 2025+ charts only if published by the Tuesday of week 1,
    the undated weekly feed to 2024 counts as preseason information, as in
    `ratings.qb_changes`), else last season's main starter (`last_season_main`: most REG
    starts, latest breaks ties);
  - a team's first playoff game: the most frequent starter of its last 3 games, latest
    breaking ties (`recent_starts`), so a starter rested in week 18 counts as back.
  Measured 2011-2025: the playoff rule misses 12 of 192 first playoff games (46 with
  the last game); last season's main starter misses 20 of 64 week-1 games in 2025-26,
  where no dated chart qualifies (27 with the last game).
- **Team baseline:** mean of value(starter of game g), values as of the key, over the
  team's played games before the key in seasons s-1 and s, weighted by
  `0.5 ** ((t_key - t_g) / baseline_half_life_weeks)` (12 weeks, like the ratings, D46).
  No such games: baseline = the expected starter's value (adj 0).
- **Overrides** (season, week, team, qb_id, qb_source) replace the Tuesday starter for
  those team-weeks: `live_starters` (schedule / depth chart / injury report at run time)
  for live runs, `actual_starters` (the listed starter of the played game; post-Tuesday
  information) as a research oracle only.

Unknown expected starter (no earlier game, no depth chart): qb_value / qb_baseline null,
qb_adj 0. Float sums run in a fixed order (sorted, numpy), so rebuilds are bit-identical.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any

import numpy as np
import polars as pl

from nflengine.features.asof import AsOf
from nflengine.models.ratings import week1_tuesdays
from nflengine.models.trend_evidence import OUT_STATUSES
from nflengine.paths import DataPaths

SEASON_SLOTS = 22  # weeks per season in the time index (2021+ Super Bowl = week 22)
BASE_SEASON = 2000
RECENT_GAMES = 3  # games looked back over for a team's first playoff game
TUESDAY = "tuesday"  # live_starters' internal label for a Tuesday-rule pick

QB_PLAY_COLS = [
    "season",
    "week",
    "game_id",
    "play_id",
    "posteam",
    "passer_id",
    "qb_dropback",
    "play_type",
    "two_point_attempt",
    "qb_epa",
]
GAME_COLS = [
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
]
DROPBACK_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "game_id": pl.String(),
    "team": pl.String(),
    "player_id": pl.String(),
    "dropbacks": pl.Int64(),
    "epa_sum": pl.Float64(),
}
OVERRIDE_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "team": pl.String(),
    "qb_id": pl.String(),
    "qb_source": pl.String(),
}
OUTPUT_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "team": pl.String(),
    "qb_id": pl.String(),
    "qb_name": pl.String(),
    "qb_source": pl.String(),
    "qb_value": pl.Float64(),
    "qb_exp": pl.Float64(),
    "qb_baseline": pl.Float64(),
    "qb_adj": pl.Float64(),
    "qb_last_id": pl.String(),
    "qb_changed": pl.Boolean(),
}
# Model-facing columns (the rest are ids, names and provenance).
QB_FEATURE_COLS = ("qb_adj", "qb_value", "qb_baseline", "qb_exp", "qb_changed")


@dataclass(frozen=True)
class QBParams:
    half_life_weeks: float = 24.0  # recency of a QB's own dropbacks
    prior_dropbacks: float = 250.0  # shrinkage strength k, in weighted dropbacks
    # Replacement level, EPA per dropback, measured on 2011-2017 only (before every
    # reported season): new QBs' first 200 dropbacks -0.045, non-primary starters' starts
    # -0.065 (league mean +0.056).
    prior_value: float = -0.05
    baseline_half_life_weeks: float = 12.0  # recency of the team's starters (= ratings, D46)

    def __post_init__(self) -> None:
        for name in ("half_life_weeks", "baseline_half_life_weeks"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.prior_dropbacks <= 0:
            raise ValueError("prior_dropbacks must be > 0")

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any] | None = None, **overrides: Any) -> QBParams:
        """From `settings.yaml` -> qb, then any non-None keyword overrides."""
        known = {f.name for f in fields(cls)}
        values = dict(cfg or {})
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"unknown qb settings: {sorted(unknown)}")
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**{k: float(v) for k, v in values.items()})

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def time_index(season: str = "season", week: str = "week") -> pl.Expr:
    """Continuous week index across seasons (the offseason gap is ignored)."""
    return ((pl.col(season).cast(pl.Int64) - BASE_SEASON) * SEASON_SLOTS + pl.col(week)).cast(
        pl.Int64
    )


def _group_sums(df: pl.DataFrame, keys: list[str], cols: list[str]) -> pl.DataFrame:
    """Per-group sums of `cols` over a frame **already sorted by `keys`** (numpy, in row
    order, so float results don't depend on Polars' threading)."""
    if df.is_empty():
        return df.select(keys).with_columns(pl.lit(None, pl.Float64).alias(c) for c in cols)
    new = df.select(
        pl.any_horizontal([pl.col(c).ne_missing(pl.col(c).shift(1)) for c in keys]).fill_null(True)
    ).to_series()
    starts = np.flatnonzero(new.to_numpy())
    starts[0] = 0
    out = df[starts.tolist()].select(keys)
    return out.with_columns(
        pl.Series(c, np.add.reduceat(df[c].cast(pl.Float64).to_numpy(), starts)) for c in cols
    )


# ---- inputs ----------------------------------------------------------------------------
def qb_dropbacks(plays: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
    """Dropbacks and summed `qb_epa` per (season, week, game_id, team, player_id)."""
    rows = (
        plays.lazy()
        .filter(
            (pl.col("qb_dropback").fill_null(0) == 1)
            & pl.col("play_type").is_in(["pass", "run"])
            & (pl.col("two_point_attempt").fill_null(0) == 0)
            & pl.col("passer_id").is_not_null()
            & pl.col("posteam").is_not_null()
            & pl.col("qb_epa").is_not_null()
            & (pl.col("week") >= 1)
        )
        .select(
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            "game_id",
            pl.col("posteam").alias("team"),
            pl.col("passer_id").alias("player_id"),
            "play_id",
            pl.lit(1, pl.Int64).alias("dropbacks"),
            pl.col("qb_epa").cast(pl.Float64).alias("epa_sum"),
        )
        .collect()
    )
    keys = ["season", "week", "game_id", "team", "player_id"]
    out = _group_sums(rows.sort([*keys, "play_id"]), keys, ["dropbacks", "epa_sum"])
    return out.with_columns(pl.col("dropbacks").cast(pl.Int64)).cast(DROPBACK_SCHEMA)


def load_qb_inputs(paths: DataPaths, min_season: int) -> dict[str, pl.DataFrame]:
    """What `team_qb_features` / `live_starters` need, from the curated tables.

    Keys: games, dropbacks (from plays of seasons >= min_season), depth_charts (QB rows,
    with `snap_date` when present), injuries (QBs listed with a status), players
    (gsis_id, display_name). Optional tables missing on disk are left out.
    """
    cur = paths.curated
    out: dict[str, pl.DataFrame] = {}
    lf = pl.scan_parquet(cur / "games.parquet")
    present = lf.collect_schema().names()
    out["games"] = lf.select([c for c in GAME_COLS if c in present]).collect()
    plays = (
        pl.scan_parquet((cur / "plays" / "*.parquet").as_posix())
        .select(QB_PLAY_COLS)
        .filter(pl.col("season") >= min_season)
    )
    out["dropbacks"] = qb_dropbacks(plays)
    tables = {
        "depth_charts": (
            ["season", "week", "team", "gsis_id", "position", "depth_rank", "snap_date"],
            pl.col("position") == "QB",
        ),
        "injuries": (
            ["season", "week", "team", "gsis_id", "position", "report_status", "date_modified"],
            pl.col("report_status").is_not_null(),
        ),
        "players": (["gsis_id", "display_name"], pl.lit(True)),
    }
    for name, (cols, keep) in tables.items():
        path = cur / f"{name}.parquet"
        if not path.exists():
            continue
        lf = pl.scan_parquet(path)
        present = lf.collect_schema().names()
        lf = lf.select([c for c in cols if c in present]).filter(keep)
        if "season" in present:
            lf = lf.filter(pl.col("season") >= min_season)
        out[name] = lf.collect()
    return out


# ---- starters ----------------------------------------------------------------------------
def _team_sides(games: pl.DataFrame) -> pl.DataFrame:
    """One row per (game, team): season, week, t, game type (REG when missing), kickoff,
    listed QB id and name."""
    gtype = pl.col("game_type") if "game_type" in games.columns else pl.lit("REG")
    return pl.concat(
        [
            games.select(
                "game_id",
                pl.col("season").cast(pl.Int32),
                pl.col("week").cast(pl.Int32),
                time_index().alias("t"),
                gtype.fill_null("REG").alias("game_type"),
                "kickoff_utc",
                pl.col("completed").fill_null(False),
                pl.col(f"{side}_team").alias("team"),
                pl.col(f"{side}_qb_id").alias("listed_id"),
                pl.col(f"{side}_qb_name").alias("listed_name"),
            )
            for side in ("home", "away")
        ]
    )


def game_starters(games: pl.DataFrame, dropbacks: pl.DataFrame) -> pl.DataFrame:
    """The main QB of every played game per team: most dropbacks (ties: the listed starter,
    then the lower id; no dropback rows: the listed starter). Columns: game_id, season,
    week, t, game_type, team, qb_id."""
    sides = _team_sides(games).filter(pl.col("completed"))
    db = dropbacks.select("game_id", "team", pl.col("player_id").alias("qb_id"), "dropbacks")
    top = (
        db.join(sides.select("game_id", "team", "listed_id"), on=["game_id", "team"])
        .with_columns((pl.col("qb_id") == pl.col("listed_id")).fill_null(False).alias("listed"))
        .sort(
            ["game_id", "team", "dropbacks", "listed", "qb_id"],
            descending=[False, False, True, True, False],
        )
        .group_by("game_id", "team", maintain_order=True)
        .first()
        .select("game_id", "team", "qb_id")
    )
    return (
        sides.join(top, on=["game_id", "team"], how="left")
        .with_columns(pl.coalesce("qb_id", "listed_id").alias("qb_id"))
        .filter(pl.col("qb_id").is_not_null())
        .select("game_id", "season", "week", "t", "game_type", "team", "qb_id")
        .sort("t", "game_id", "team")
    )


ORACLE_RULES = ("listed", "most_dropbacks")


def actual_starters(
    games: pl.DataFrame, dropbacks: pl.DataFrame | None = None, rule: str = "listed"
) -> pl.DataFrame:
    """Overrides with who really started each played game (`qb_source='actual'`).

    Research oracle only: this is post-Tuesday information. Use it to measure how much a
    perfect injury feed would add, never in a live or backtest feature.

    - `rule='listed'` (default): the schedule's starting QB of the completed game, what a
      final (Saturday) injury update could have known. Falls back to the most-dropbacks QB
      when the schedule lists nobody and `dropbacks` is given.
    - `rule='most_dropbacks'`: the QB with the most dropbacks (`game_starters`). This also
      foresees in-game injuries (2023 NYJ week 1 -> Wilson), so its gain over the Tuesday
      rule is an upper bound, not the value of an injury feed.
    """
    if rule not in ORACLE_RULES:
        raise ValueError(f"rule must be one of {ORACLE_RULES}, got {rule!r}")
    if rule == "most_dropbacks":
        if dropbacks is None:
            raise ValueError("rule='most_dropbacks' needs dropbacks")
        picks = game_starters(games, dropbacks)
    else:
        picks = (
            _team_sides(games)
            .filter(pl.col("completed"))
            .select("game_id", "season", "week", "t", "team", pl.col("listed_id").alias("qb_id"))
        )
        if dropbacks is not None:
            most = game_starters(games, dropbacks).select(
                "game_id", "team", pl.col("qb_id").alias("most_id")
            )
            picks = picks.join(most, on=["game_id", "team"], how="left").with_columns(
                pl.coalesce("qb_id", "most_id").alias("qb_id")
            )
        picks = picks.filter(pl.col("qb_id").is_not_null()).sort("t", "game_id", "team")
    return (
        picks.select("season", "week", "team", "qb_id", pl.lit("actual").alias("qb_source"))
        .unique(["season", "week", "team"], keep="first", maintain_order=True)
        .cast(OVERRIDE_SCHEMA)
    )


def week1_depth_qb1(games: pl.DataFrame, depth_charts: pl.DataFrame | None) -> pl.DataFrame:
    """(season, team, qb_id): the week-1 depth-chart QB1 a Tuesday run could see (D44:
    dated charts only if published by the Tuesday of week 1; undated ones always)."""
    schema = {"season": pl.Int32, "team": pl.String, "qb_id": pl.String}
    if depth_charts is None or depth_charts.is_empty():
        return pl.DataFrame(schema=schema)
    qb1 = depth_charts.filter(
        (pl.col("week") == 1) & (pl.col("position") == "QB") & (pl.col("depth_rank") == 1)
    ).with_columns(pl.col("season").cast(pl.Int32))
    if "snap_date" in qb1.columns:
        qb1 = qb1.join(week1_tuesdays(games), on="season", how="left").filter(
            pl.col("snap_date").is_null() | (pl.col("snap_date") <= pl.col("tuesday"))
        )
    return (
        qb1.sort("season", "team", "gsis_id")
        .group_by("season", "team", maintain_order=True)
        .first()
        .select("season", "team", pl.col("gsis_id").alias("qb_id"))
        .cast(schema)
    )


def live_starters(
    games: pl.DataFrame,
    depth_charts: pl.DataFrame | None,
    injuries: pl.DataFrame | None,
    season: int,
    week: int,
    run_date: dt.date,
    dropbacks: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Overrides for the **unplayed** games of (season, week), from what a run on
    `run_date` knows.

    Pick per team: 1. the schedule's starting QB for that game (nflverse fills projected
    starters for upcoming games; `qb_source='schedule'`), 2. the latest week-`week`
    depth-chart QB1 published by `run_date` (`'depth_chart'`), 3. the Tuesday-rule starter
    of `team_qb_features` (needs `dropbacks`; without them, none). If that week's injury
    report lists the pick Out or Doubtful, the next QB on the team's latest chart (this
    week or earlier this season, published by `run_date`) who is not listed takes over
    (`'injury_next'`); with no such QB the pick stays. A Tuesday-rule pick that isn't
    replaced yields no row: the feature already uses it.
    """
    sides = (
        _team_sides(games)
        .filter((pl.col("season") == season) & (pl.col("week") == week) & ~pl.col("completed"))
        .select("team", pl.col("listed_id").alias("sched_id"))
        .unique("team", keep="first", maintain_order=True)
    )
    if sides.is_empty():
        return pl.DataFrame(schema=OVERRIDE_SCHEMA)

    chart = pl.DataFrame(
        schema={"week": pl.Int32, "team": pl.String, "gsis_id": pl.String, "depth_rank": pl.Int32}
    )
    if depth_charts is not None and not depth_charts.is_empty():
        dc = depth_charts.filter(
            (pl.col("season") == season) & (pl.col("week") <= week) & (pl.col("position") == "QB")
        )
        if "snap_date" in dc.columns:
            dc = dc.filter(pl.col("snap_date").is_null() | (pl.col("snap_date") <= run_date))
            # Latest snapshot per team-week (several can be on file).
            dc = dc.filter(
                pl.col("snap_date").fill_null(dt.date.min)
                == pl.col("snap_date").fill_null(dt.date.min).max().over("week", "team")
            )
        # Each team's latest chart week.
        dc = dc.filter(pl.col("week") == pl.col("week").max().over("team"))
        chart = dc.select(
            pl.col("week").cast(pl.Int32),
            "team",
            "gsis_id",
            pl.col("depth_rank").cast(pl.Int32),
        ).sort("team", "depth_rank", "gsis_id")
    qb1 = (
        chart.filter((pl.col("week") == week) & (pl.col("depth_rank") == 1))
        .group_by("team", maintain_order=True)
        .first()
        .select("team", pl.col("gsis_id").alias("chart_id"))
    )
    tue = pl.DataFrame(schema={"team": pl.String, "tue_id": pl.String})
    if dropbacks is not None:
        tue = team_qb_features(
            games, dropbacks, [AsOf(season, week)], depth_charts=depth_charts
        ).select("team", pl.col("qb_last_id").alias("tue_id"))
    picks = (
        sides.join(qb1, on="team", how="left")
        .join(tue, on="team", how="left")
        .with_columns(
            pl.coalesce("sched_id", "chart_id", "tue_id").alias("qb_id"),
            pl.when(pl.col("sched_id").is_not_null())
            .then(pl.lit("schedule"))
            .when(pl.col("chart_id").is_not_null())
            .then(pl.lit("depth_chart"))
            .when(pl.col("tue_id").is_not_null())
            .then(pl.lit(TUESDAY))
            .alias("qb_source"),
        )
        .filter(pl.col("qb_id").is_not_null())
    )

    listed: set[str] = set()
    if injuries is not None and not injuries.is_empty():
        inj = injuries.filter(
            (pl.col("season") == season)
            & (pl.col("week") == week)
            & pl.col("report_status").is_in(OUT_STATUSES)
            & pl.col("gsis_id").is_not_null()
        )
        if "date_modified" in inj.columns:
            inj = inj.filter(
                pl.col("date_modified").is_null() | (pl.col("date_modified").dt.date() <= run_date)
            )
        listed = set(inj["gsis_id"].to_list())
    if listed:
        nxt = (
            chart.filter(~pl.col("gsis_id").is_in(list(listed)))
            .group_by("team", maintain_order=True)
            .first()
            .select("team", pl.col("gsis_id").alias("next_id"))
        )
        hurt = pl.col("qb_id").is_in(list(listed)) & pl.col("next_id").is_not_null()
        picks = picks.join(nxt, on="team", how="left").with_columns(
            pl.when(hurt).then(pl.col("next_id")).otherwise(pl.col("qb_id")).alias("qb_id"),
            pl.when(hurt)
            .then(pl.lit("injury_next"))
            .otherwise(pl.col("qb_source"))
            .alias("qb_source"),
        )
    return (
        picks.filter(pl.col("qb_source") != TUESDAY)
        .select(
            pl.lit(season, pl.Int32).alias("season"),
            pl.lit(week, pl.Int32).alias("week"),
            "team",
            "qb_id",
            "qb_source",
        )
        .sort("team")
        .cast(OVERRIDE_SCHEMA)
    )


# ---- values ------------------------------------------------------------------------------
def _decayed_prefix(x: np.ndarray, half_life: float) -> np.ndarray:
    """S[:, t] = sum over t' < t of 0.5 ** ((t - t') / half_life) * x[:, t']."""
    d = 0.5 ** (1.0 / half_life)
    s = np.zeros_like(x)
    for t in range(1, x.shape[1]):
        s[:, t] = d * (s[:, t - 1] + x[:, t - 1])
    return s


def _qb_value_grid(
    dropbacks: pl.DataFrame, index: pl.DataFrame, t_max: int, params: QBParams
) -> tuple[np.ndarray, np.ndarray]:
    """(value, exp) per (player index, t) for every t in [0, t_max]: as of t, using only
    dropbacks from earlier weeks."""
    n_players = index.height
    rows = (
        dropbacks.with_columns(time_index().alias("t"))
        .filter((pl.col("t") >= 0) & (pl.col("t") < t_max) & (pl.col("week") >= 1))
        .join(index, left_on="player_id", right_on="qb_id")
        .sort("t", "game_id", "team", "player_id")
    )
    shape = (n_players, t_max + 1)
    n, e = np.zeros(shape), np.zeros(shape)
    at = (rows["i"].to_numpy(), rows["t"].to_numpy())
    np.add.at(n, at, rows["dropbacks"].cast(pl.Float64).to_numpy())
    np.add.at(e, at, rows["epa_sum"].cast(pl.Float64).to_numpy())
    sn = _decayed_prefix(n, params.half_life_weeks)
    se = _decayed_prefix(e, params.half_life_weeks)
    k = params.prior_dropbacks
    return (se + k * params.prior_value) / (sn + k), sn


def _lookup(grid: np.ndarray, i: pl.Series, t: pl.Series) -> pl.Series:
    """grid[i, t], null where i is null."""
    known = i.is_not_null().to_numpy()
    vals = np.full(len(i), np.nan)
    if known.any():
        vals[known] = grid[i.to_numpy()[known].astype(np.int64), t.to_numpy()[known]]
    return pl.Series(vals, nan_to_null=True)


def _qb_names(games: pl.DataFrame, players: pl.DataFrame | None) -> pl.DataFrame:
    """qb_id -> name: the latest name a schedule listed for a start, else `players`."""
    sides = (
        _team_sides(games)
        .filter(pl.col("completed") & pl.col("listed_id").is_not_null())
        .filter(pl.col("listed_name").is_not_null())
        .sort("kickoff_utc", "game_id", "team")
        .group_by("listed_id", maintain_order=True)
        .last()
        .select(pl.col("listed_id").alias("qb_id"), pl.col("listed_name").alias("g_name"))
    )
    if players is None or players.is_empty():
        return sides.rename({"g_name": "qb_name"})
    p = (
        players.filter(pl.col("gsis_id").is_not_null())
        .unique("gsis_id", keep="first", maintain_order=True)
        .select(pl.col("gsis_id").alias("qb_id"), pl.col("display_name").alias("p_name"))
    )
    return sides.join(p, on="qb_id", how="full", coalesce=True).select(
        "qb_id", pl.coalesce("g_name", "p_name").alias("qb_name")
    )


def _key_frame(keys: Sequence[AsOf] | Sequence[tuple[int, int]]) -> pl.DataFrame:
    pairs = sorted(
        {(int(k[0]), int(k[1])) if isinstance(k, tuple) else (k.season, k.week) for k in keys}
    )
    return pl.DataFrame(
        {"season": [s for s, _ in pairs], "week": [w for _, w in pairs]},
        schema={"season": pl.Int32, "week": pl.Int32},
    ).with_columns(time_index().alias("t_key"))


# ---- public ------------------------------------------------------------------------------
def team_qb_features(
    games: pl.DataFrame,
    dropbacks: pl.DataFrame,
    keys: Sequence[AsOf] | Sequence[tuple[int, int]],
    params: QBParams | None = None,
    depth_charts: pl.DataFrame | None = None,
    overrides: pl.DataFrame | None = None,
    players: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """QB status per (season, as-of week, team) for every team with a game in that week.

    `dropbacks` is the `qb_dropbacks` frame; `params` defaults to `QBParams()`; `players`
    (gsis_id, display_name) names QBs the schedule never listed. Without overrides every
    row for key (s, w) uses only weeks before w (plus the week-1 depth-chart rule) and the
    schedule (who plays in week w, and whether it's a playoff game). Columns: OUTPUT_SCHEMA.
    """
    params = params or QBParams()
    kf = _key_frame(keys)
    if kf.is_empty():
        return pl.DataFrame(schema=OUTPUT_SCHEMA)
    k = ["season", "week", "team"]
    kt = (
        _team_sides(games)
        .join(kf, on=["season", "week"])
        .select(*k, "t_key", pl.col("game_type").alias("key_type"))
        .unique(k, keep="first")
        .sort("t_key", "team")
    )
    starters = game_starters(games, dropbacks)

    # The team's played games before the key in seasons s-1 and s (baseline, recent starts).
    base = (
        kt.join(
            starters.select(
                "team",
                pl.col("season").alias("g_season"),
                "t",
                "game_id",
                pl.col("qb_id").alias("g_qb"),
            ),
            on="team",
        )
        .filter((pl.col("t") < pl.col("t_key")) & (pl.col("g_season") >= pl.col("season") - 1))
        .sort("t_key", "team", "t", "game_id")
    )

    # Tuesday rule. Default: the starter of the team's latest played game before the key.
    last = kt.join_asof(
        starters.select(
            "team",
            pl.col("t").alias("t_key"),
            pl.col("qb_id").alias("prev_id"),
            pl.col("game_type").alias("prev_type"),
        ),
        on="t_key",
        by="team",
        strategy="backward",
        allow_exact_matches=False,
        check_sortedness=False,  # both frames are sorted by t above
    )
    # Week 1: the depth-chart QB1 when a Tuesday run could see it, else last season's main
    # starter (most REG starts, latest breaks ties; a rested week-18 backup doesn't count).
    w1 = week1_depth_qb1(games, depth_charts).rename({"qb_id": "dc_id"})
    main_prev = (
        starters.filter(pl.col("game_type") == "REG")
        .group_by("season", "team", "qb_id")
        .agg(pl.len().alias("n"), pl.col("t").max().alias("t_last"))
        .sort(["season", "team", "n", "t_last"], descending=[False, False, True, True])
        .group_by("season", "team", maintain_order=True)
        .first()
        .select((pl.col("season") + 1).cast(pl.Int32).alias("season"), "team", "qb_id")
        .rename({"qb_id": "main_prev_id"})
    )
    # A team's first playoff game: the most frequent starter of its last 3 games (ties: the
    # latest), so a starter rested in the final regular-season week counts as back.
    recent = (
        base.group_by(k, maintain_order=True)
        .tail(RECENT_GAMES)
        .group_by(*k, "g_qb")
        .agg(pl.len().alias("n"), pl.col("t").max().alias("t_last"))
        .sort([*k, "n", "t_last"], descending=[False, False, False, True, True])
        .group_by(k, maintain_order=True)
        .first()
        .select(*k, pl.col("g_qb").alias("recent_id"))
    )
    week1 = pl.col("week") == 1
    first_playoff = (pl.col("key_type") != "REG") & (pl.col("prev_type") == "REG")
    rule = (
        pl.when(week1 & pl.col("dc_id").is_not_null())
        .then(pl.struct(id="dc_id", src=pl.lit("depth_chart_w1")))
        .when(week1 & pl.col("main_prev_id").is_not_null())
        .then(pl.struct(id="main_prev_id", src=pl.lit("last_season_main")))
        .when(first_playoff & pl.col("recent_id").is_not_null())
        .then(pl.struct(id="recent_id", src=pl.lit("recent_starts")))
        .when(pl.col("prev_id").is_not_null())
        .then(pl.struct(id="prev_id", src=pl.lit("last_game")))
    )
    rows = (
        last.join(w1, on=["season", "team"], how="left")
        .join(main_prev, on=["season", "team"], how="left")
        .join(recent, on=k, how="left")
        .with_columns(rule.alias("_tue"))
        .with_columns(
            pl.col("_tue").struct.field("id").alias("qb_last_id"),
            pl.col("_tue").struct.field("src").alias("tue_source"),
        )
        .drop("_tue", "dc_id", "main_prev_id", "recent_id")
    )
    if overrides is not None and not overrides.is_empty():
        ov = (
            overrides.select(
                pl.col("season").cast(pl.Int32),
                pl.col("week").cast(pl.Int32),
                "team",
                pl.col("qb_id").alias("ov_id"),
                pl.col("qb_source").alias("ov_source"),
            )
            .filter(pl.col("ov_id").is_not_null())
            .unique(k, keep="last", maintain_order=True)
        )
        rows = rows.join(ov, on=k, how="left")
    else:
        rows = rows.with_columns(
            pl.lit(None, pl.String).alias("ov_id"), pl.lit(None, pl.String).alias("ov_source")
        )
    rows = rows.with_columns(
        pl.coalesce("ov_id", "qb_last_id").alias("qb_id"),
        pl.when(pl.col("ov_id").is_not_null())
        .then(pl.col("ov_source"))
        .otherwise(pl.col("tue_source"))
        .alias("qb_source"),
    )

    # Player index and value grids.
    ids = pl.concat(
        [
            dropbacks.select(pl.col("player_id").alias("qb_id")),
            starters.select("qb_id"),
            rows.select("qb_id"),
        ]
    )
    index = (
        ids.filter(pl.col("qb_id").is_not_null())
        .unique()
        .sort("qb_id")
        .with_row_index("i")
        .with_columns(pl.col("i").cast(pl.Int64))
    )
    t_max = int(kf["t_key"].max())
    value, exp = _qb_value_grid(dropbacks, index, t_max, params)

    base = base.join(index.rename({"qb_id": "g_qb"}), on="g_qb", how="left")
    base = base.with_columns(
        _lookup(value, base["i"], base["t_key"]).alias("v"),
        (0.5 ** ((pl.col("t_key") - pl.col("t")) / params.baseline_half_life_weeks)).alias("w"),
    ).filter(pl.col("v").is_not_null())
    base = base.with_columns((pl.col("w") * pl.col("v")).alias("wv"))
    bsum = _group_sums(base, ["season", "week", "team"], ["wv", "w"]).select(
        "season", "week", "team", (pl.col("wv") / pl.col("w")).alias("qb_baseline")
    )

    rows = rows.join(index, on="qb_id", how="left")
    rows = rows.with_columns(
        _lookup(value, rows["i"], rows["t_key"]).alias("qb_value"),
        _lookup(exp, rows["i"], rows["t_key"]).alias("qb_exp"),
    )
    out = (
        rows.join(bsum, on=["season", "week", "team"], how="left")
        .join(_qb_names(games, players), on="qb_id", how="left")
        .with_columns(
            pl.when(pl.col("qb_value").is_not_null())
            .then(pl.coalesce("qb_baseline", "qb_value"))
            .alias("qb_baseline"),
        )
        .with_columns(
            (pl.col("qb_value") - pl.col("qb_baseline")).fill_null(0.0).alias("qb_adj"),
            pl.when(pl.col("qb_id").is_not_null() & pl.col("prev_id").is_not_null())
            .then(pl.col("qb_id") != pl.col("prev_id"))
            .alias("qb_changed"),
        )
    )
    return out.select(list(OUTPUT_SCHEMA)).cast(OUTPUT_SCHEMA).sort("season", "week", "team")
