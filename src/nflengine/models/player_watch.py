"""Players to watch from the player model (plan P06; documentation/04 -> C, "Players to watch").

Everything here reads one week's projections (`player_schema.PRED_SCHEMA` rows) and nothing
else, so the live digest, backtest digests and the watch-list backtest share one rule.

**Pool** (`eligible`):
- main targets only (`is_main`: passing yards, rushing yards, receiving yards, pressures,
  tackles; one per position group);
- a real role: `role_ok` (snap share >= 50% over his last 2 games) or `role_change` (a
  regular teammate in his group is out, so usage is vacated);
- a game that hasn't kicked off at the run time (`kickoff_utc > run_time`; without a run
  time, each row's own `created_at`), so a Saturday run never "predicts" Thursday's game;
- not listed Out / Doubtful (upstream drops them when known; checked again here);
- `outperf_z` known.

**Watch list** (`select_watchlist`):
- score = `outperf_z` = (p50 - baseline) / the target's typical baseline miss, so a yardage
  jump and a pressure jump compare; x `FOLLOWED_BOOST` (1.15) for a followed team, a nudge
  among close calls that never lifts a weak signal over a strong one;
- only positive scores (a projection above his own baseline);
- one row per player: a linebacker can be in two pools (pressures with EDGE/DL, tackles with
  LB/S) and keeps his best-scoring row;
- greedy by score (ties: larger raw `outperf_z`, then `player_id`), at most `max_per_team`
  (2) per team, `max_per_group` (3) per position group and `max_defense` (3) defenders
  (EDGE/DL + LB/S), so a week can't be eight quarterbacks or six defenders. If the caps
  leave the list short, the group and defense caps are dropped for the rest (the team cap
  never is). The defense cap is a variety rule: defenders' count targets move further from
  their baselines in z than receiving yards do, and without it 5-6 of 8 picks were
  defenders and a receiver made the list about once a month (2019-2025 backtests); with
  it the hit rate held (checked on 2019-2020, confirmed on 2021-2025).

**Tough spots** (`tough_spots`): the most negative `outperf_z` among regular starters
(`role_ok`, not low confidence, `outperf_z <= -TOUGH_MIN_Z`), one per team, never a
watch-list player: candidates for *Matchup / risk to watch*.

**Backtest** (`watchlist_backtest`): the exit criterion "watch-list hit rate > 50% in
backtests". Each week's list is selected from that week's rows; a pick is a hit when he
played and `actual > baseline`. A hit rate means nothing without the **base rate**: every
eligible main-target role player of the week with `actual > baseline` (P04 measured ~43%
for yardage: medians sit below means, so most players finish under their average).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from nflengine.models.player_schema import DEFENSE

FOLLOWED_BOOST = 1.15
MAX_PER_TEAM = 2
MAX_PER_GROUP = 3
MAX_DEFENSE = 3
TOUGH_MIN_Z = 0.25  # a tough spot is at least a quarter of a typical miss below baseline
OUT_STATUSES = ("Out", "Doubtful")


# ---- pool -------------------------------------------------------------------------------------


def _run_time(run_time: dt.datetime | None) -> pl.Expr:
    if run_time is None:
        return pl.col("created_at")
    if run_time.tzinfo is None:
        run_time = run_time.replace(tzinfo=dt.UTC)
    return pl.lit(run_time).cast(pl.Datetime("us", "UTC"))


def eligible(preds: pl.DataFrame, run_time: dt.datetime | None = None) -> pl.DataFrame:
    """Main-target rows of role players in games not yet started at `run_time` (see the
    module doc)."""
    if preds.is_empty():
        return preds
    when, ko = _run_time(run_time), pl.col("kickoff_utc")
    z = pl.col("outperf_z")
    return preds.filter(
        pl.col("is_main").fill_null(False)
        & (pl.col("role_ok").fill_null(False) | pl.col("role_change").fill_null(False))
        & (ko.is_null() | when.is_null() | (ko > when))
        & ~pl.col("injury_status").fill_null("").is_in(OUT_STATUSES)
        & z.is_not_null()
        & ~z.is_nan()
    )


def _best_per_player(df: pl.DataFrame, descending: bool = True) -> pl.DataFrame:
    """One row per (player, game): his best score (or most negative, for tough spots)."""
    return df.sort(
        ["score", "outperf_z", "player_id"], descending=[descending, descending, False]
    ).unique(["player_id", "game_id"], keep="first", maintain_order=True)


def _pick(
    df: pl.DataFrame,
    n: int,
    max_team: int,
    max_group: int | None,
    max_defense: int | None = None,
) -> pl.DataFrame:
    """Greedy pick in the frame's order under the team / group / defense caps (the group
    and defense caps are dropped if they leave the list short); adds `rank`."""
    rows = df.with_row_index("_i").to_dicts()
    chosen: list[int] = []
    team: Counter[str] = Counter()
    group: Counter[str] = Counter()
    defense = 0
    soft_caps = max_group is not None or max_defense is not None
    for relax in (False, True):
        for r in rows:
            if len(chosen) >= n:
                break
            if r["_i"] in chosen or team[r["team"]] >= max_team:
                continue
            is_def = r["group"] in DEFENSE
            if not relax and (
                (max_group is not None and group[r["group"]] >= max_group)
                or (max_defense is not None and is_def and defense >= max_defense)
            ):
                continue
            chosen.append(r["_i"])
            team[r["team"]] += 1
            group[r["group"]] += 1
            defense += is_def
        if len(chosen) >= n or not soft_caps:
            break
    order = {i: k for k, i in enumerate(chosen, start=1)}
    return (
        df.with_row_index("_i")
        .filter(pl.col("_i").is_in(chosen))
        .with_columns(pl.col("_i").replace_strict(order, return_dtype=pl.Int32).alias("rank"))
        .sort("rank")
        .drop("_i")
    )


def _empty(preds: pl.DataFrame) -> pl.DataFrame:
    return preds.clear().with_columns(
        pl.lit(None, pl.Int32).alias("rank"), pl.lit(None, pl.Float64).alias("score")
    )


# ---- watch list and tough spots -----------------------------------------------------------------


def select_watchlist(
    preds: pl.DataFrame,
    n_items: int = 8,
    max_per_team: int = MAX_PER_TEAM,
    followed: Collection[str] = (),
    *,
    run_time: dt.datetime | None = None,
    max_per_group: int | None = MAX_PER_GROUP,
    max_defense: int | None = MAX_DEFENSE,
) -> pl.DataFrame:
    """The week's players to watch (module doc): `preds`' columns plus `rank` (1 = first)
    and `score`, in rank order. Empty (same columns) when nothing qualifies."""
    pool = eligible(preds, run_time)
    if pool.is_empty():
        return _empty(preds)
    boost = pl.when(pl.col("team").is_in(list(followed))).then(FOLLOWED_BOOST).otherwise(1.0)
    pool = pool.with_columns((pl.col("outperf_z") * boost).alias("score")).filter(
        pl.col("score") > 0
    )
    if pool.is_empty():
        return _empty(preds)
    return _pick(_best_per_player(pool), n_items, max_per_team, max_per_group, max_defense)


def tough_spots(
    preds: pl.DataFrame,
    n: int = 3,
    *,
    run_time: dt.datetime | None = None,
    exclude: Collection[str] = (),
    min_z: float = TOUGH_MIN_Z,
) -> pl.DataFrame:
    """Regular starters projected furthest below their own baseline (module doc): columns
    of `preds` plus `rank` and `score` (= `outperf_z`, negative), most negative first."""
    pool = eligible(preds, run_time)
    if pool.is_empty():
        return _empty(preds)
    pool = pool.filter(
        pl.col("role_ok").fill_null(False)
        & (pl.col("confidence").fill_null("low") != "low")
        & (pl.col("outperf_z") <= -min_z)
        & ~pl.col("player_id").is_in(list(exclude))
    ).with_columns(pl.col("outperf_z").alias("score"))
    if pool.is_empty():
        return _empty(preds)
    worst = _best_per_player(pool, descending=False)
    return _pick(worst, n, 1, None)


# ---- backtest -----------------------------------------------------------------------------------


@dataclass
class WatchBacktest:
    """Watch-list hit rate vs the base rate (exit criterion: hit rate > 50% in backtests).

    `picks`: every pick with `scored` (played, actual known), `hit` (actual > baseline) and
    `inside` (P10 <= actual <= P90). `by_season` / `by_group`: picks, scored, hits,
    hit_rate, the pool's `base_rate` (all eligible main-target role players) and
    `positive_rate` (the pool's players projected above baseline), `lift` = hit_rate -
    base_rate, `inside_rate`. `overall`: the same over everything.
    """

    picks: pl.DataFrame
    by_season: pl.DataFrame
    by_group: pl.DataFrame
    overall: dict[str, Any] = field(default_factory=dict)

    def summary(self, prefix: str = "watch/") -> dict[str, Any]:
        """Flat numbers for W&B run summaries."""
        return {f"{prefix}{k}": v for k, v in self.overall.items()}


def _frames(preds_by_week: pl.DataFrame | Mapping[Any, pl.DataFrame] | Iterable[pl.DataFrame]):
    if isinstance(preds_by_week, pl.DataFrame):
        return preds_by_week
    parts = list(preds_by_week.values() if isinstance(preds_by_week, Mapping) else preds_by_week)
    parts = [p for p in parts if p.height]
    return pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame()


def _outcomes(df: pl.DataFrame) -> pl.DataFrame:
    scored = pl.col("played").fill_null(False) & pl.col("actual").is_not_null()
    return df.with_columns(scored.alias("scored")).with_columns(
        pl.when(pl.col("scored")).then(pl.col("actual") > pl.col("baseline")).alias("hit"),
        pl.when(pl.col("scored"))
        .then((pl.col("actual") >= pl.col("p10")) & (pl.col("actual") <= pl.col("p90")))
        .alias("inside"),
    )


def _rates(picks: pl.DataFrame, pool: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
    def n(expr: pl.Expr) -> pl.Expr:
        return expr.fill_null(False).sum().cast(pl.Int64)

    p = picks.group_by(keys).agg(
        pl.struct("season", "week").n_unique().alias("weeks"),
        pl.len().alias("picks"),
        n(pl.col("scored")).alias("scored"),
        n(pl.col("hit")).alias("hits"),
        n(pl.col("inside")).alias("inside"),
    )
    pos = pl.col("outperf_z") > 0
    b = pool.group_by(keys).agg(
        n(pl.col("scored")).alias("pool_scored"),
        n(pl.col("hit")).alias("pool_hits"),
        n(pl.col("scored") & pos).alias("positive_scored"),
        n(pl.col("hit") & pos).alias("positive_hits"),
    )
    out = b.join(p, on=keys, how="left").with_columns(
        pl.col("weeks", "picks", "scored", "hits", "inside").fill_null(0)
    )

    def rate(a: str, d: str) -> pl.Expr:
        return pl.when(pl.col(d) > 0).then(pl.col(a) / pl.col(d))

    return (
        out.with_columns(
            rate("hits", "scored").alias("hit_rate"),
            rate("pool_hits", "pool_scored").alias("base_rate"),
            rate("positive_hits", "positive_scored").alias("positive_rate"),
            rate("inside", "scored").alias("inside_rate"),
        )
        .with_columns((pl.col("hit_rate") - pl.col("base_rate")).alias("lift"))
        .sort(keys)
    )


def watchlist_backtest(
    preds_by_week: pl.DataFrame | Mapping[Any, pl.DataFrame] | Iterable[pl.DataFrame],
    n_items: int = 8,
    max_per_team: int = MAX_PER_TEAM,
    followed: Collection[str] = (),
    *,
    max_per_group: int | None = MAX_PER_GROUP,
    max_defense: int | None = MAX_DEFENSE,
) -> WatchBacktest:
    """Select each backtest week's list from that week's rows (main targets with `actual` /
    `played` filled, `created_at` = that week's Tuesday) and score it against the base rate.
    Accepts one frame of many weeks, or several frames (a dict or list, e.g. one per
    target file)."""
    df = _frames(preds_by_week)
    picks, pools = [], []
    if df.height:
        for _, week in df.group_by("season", "week", maintain_order=True):
            pools.append(eligible(week))
            picks.append(
                select_watchlist(
                    week,
                    n_items,
                    max_per_team,
                    followed,
                    max_per_group=max_per_group,
                    max_defense=max_defense,
                )
            )
    picks_df = _outcomes(pl.concat(picks, how="diagonal_relaxed")) if picks else pl.DataFrame()
    pool_df = _outcomes(pl.concat(pools, how="diagonal_relaxed")) if pools else pl.DataFrame()
    if pool_df.is_empty():
        empty = pl.DataFrame()
        return WatchBacktest(empty, empty, empty, {})
    by_season = _rates(picks_df, pool_df, ["season"])
    by_group = _rates(picks_df, pool_df, ["group"])
    allr = _rates(
        picks_df.with_columns(pl.lit("all").alias("_all")),
        pool_df.with_columns(pl.lit("all").alias("_all")),
        ["_all"],
    ).row(0, named=True)
    overall = {k: v for k, v in allr.items() if k != "_all"}
    keep = [
        "season", "week", "rank", "player_id", "player", "team", "group", "target",
        "p10", "p50", "p90", "baseline", "outperf_z", "confidence", "actual", "played",
        "scored", "hit", "inside",
    ]  # fmt: skip
    return WatchBacktest(picks_df.select(keep), by_season, by_group, overall)
