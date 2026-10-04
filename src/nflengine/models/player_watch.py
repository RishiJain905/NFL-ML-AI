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

**Watch list** (`select_watchlist`): two lists, **10 on offense** (QB, RB, WR/TE) and **10 on
defense** (EDGE/DL, LB/S), each picked on its own (Rishi, D70). One combined ranking crowds
offense out: defenders' count targets move further from their baselines in z than
receiving yards do (P06's single list of 8 needed a 3-defender cap).
- score = `outperf_z` = (p50 - baseline) / the target's typical baseline miss, so a yardage
  jump and a pressure jump compare; x `FOLLOWED_BOOST` (1.15) for a followed team, a nudge
  among close calls that never lifts a weak signal over a strong one;
- only positive scores (a projection above his own baseline);
- one row per player: a linebacker can be in two pools (pressures with EDGE/DL, tackles with
  LB/S) and keeps his best-scoring row;
- greedy by score within each side (ties: larger raw `outperf_z`, then `player_id`), at most
  `max_per_team` (2) per team per side and a soft cap per group (`GROUP_CAPS`: QB 3, RB 4,
  WR/TE 4, EDGE/DL 6, LB/S 6) for variety. If the group caps leave a side short, they are
  dropped for the rest of that side (the team cap never is). Each pick carries its `side`
  and its `rank` within the side;
- a minimum projected volume (`MIN_VOLUME`, on the reader-facing projection: the mean for
  counts, P50 for yards): a pick needs at least 1.0 projected pressures or 2.0 tackles.
  Never relaxed: a side that can't fill its quota shows fewer picks. Tough spots don't
  use it.

**Tough spots** (`tough_spots`): the most negative `outperf_z` among regular starters
(`role_ok`, not low confidence, `outperf_z <= -TOUGH_MIN_Z`), one per team, never a
watch-list player: candidates for *Matchup / risk to watch*.

**Backtest** (`watchlist_backtest`): the exit criterion "watch-list hit rate > 50% in
backtests". Each week's lists are selected from that week's rows; a pick is a hit when he
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
MAX_PER_TEAM = 2  # per side
N_OFFENSE = 10
N_DEFENSE = 10
N_TOUGH = 5
OFFENSE = ("QB", "RB", "WR/TE")
GROUP_CAPS: dict[str, int] = {"QB": 3, "RB": 4, "WR/TE": 4, "EDGE/DL": 6, "LB/S": 6}
# Minimum projected volume for a watch-list pick (not tough spots). Chosen on the
# 2019-2020 backtests only, confirmed on 2021-2025 unchanged: a rotation lineman projected
# 0.3 pressures read oddly, and pressures picks under 0.8 hit 33% (58% above). With these
# floors defense went 62.9% -> 65.3% (2019-20) and 64.2% -> 65.7% (2021-25), EDGE/DL
# 55.4% -> 59.9% / 56.0% -> 59.8%, with no week short of 10. Tackles never bind today (the
# 5th percentile of picks is 2.7) but guard the deep list. No offensive floor: picks under
# 20 receiving / rushing yards hit as often as the rest, and floors lowered WR/TE's hit rate
# (65.6% -> 62.8% at 20 yards, 2019-20).
MIN_VOLUME: dict[str, float] = {"pressures": 1.0, "tackles": 2.0}
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


def projection_expr() -> pl.Expr:
    """The reader-facing projection: the mean for counts, P50 for amounts (as the digest
    shows it, `digest.players.center`)."""
    is_count = (pl.col("kind") == "count") & pl.col("mean").is_not_null()
    return pl.when(is_count).then(pl.col("mean")).otherwise(pl.col("p50"))


def side_of(group: str = "group") -> pl.Expr:
    """The side of a model group: `defense` (EDGE/DL, LB/S) or `offense`."""
    is_def = pl.col(group).is_in(list(DEFENSE))
    return pl.when(is_def).then(pl.lit("defense")).otherwise(pl.lit("offense"))


def _best_per_player(df: pl.DataFrame, descending: bool = True) -> pl.DataFrame:
    """One row per (player, game): his best score (or most negative, for tough spots)."""
    return df.sort(
        ["score", "outperf_z", "player_id"], descending=[descending, descending, False]
    ).unique(["player_id", "game_id"], keep="first", maintain_order=True)


def _pick(
    df: pl.DataFrame,
    n: int,
    max_team: int,
    group_caps: Mapping[str, int] | None = None,
) -> pl.DataFrame:
    """Greedy pick in the frame's order under the team cap and the soft per-group caps
    (dropped for the rest if they leave the list short); adds `rank`."""
    rows = df.with_row_index("_i").to_dicts()
    chosen: list[int] = []
    team: Counter[str] = Counter()
    group: Counter[str] = Counter()
    caps = dict(group_caps or {})
    for relax in (False, True):
        for r in rows:
            if len(chosen) >= n:
                break
            if r["_i"] in chosen or team[r["team"]] >= max_team:
                continue
            cap = caps.get(r["group"])
            if not relax and cap is not None and group[r["group"]] >= cap:
                continue
            chosen.append(r["_i"])
            team[r["team"]] += 1
            group[r["group"]] += 1
        if len(chosen) >= n or not caps:
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
        pl.lit(None, pl.Int32).alias("rank"),
        pl.lit(None, pl.Float64).alias("score"),
        pl.lit(None, pl.String).alias("side"),
    )


# ---- watch list and tough spots -----------------------------------------------------------------


def select_watchlist(
    preds: pl.DataFrame,
    n_offense: int = N_OFFENSE,
    n_defense: int = N_DEFENSE,
    max_per_team: int = MAX_PER_TEAM,
    followed: Collection[str] = (),
    *,
    run_time: dt.datetime | None = None,
    group_caps: Mapping[str, int] | None = GROUP_CAPS,
    min_volume: Mapping[str, float] | None = MIN_VOLUME,
) -> pl.DataFrame:
    """The week's players to watch (module doc): `preds`' columns plus `score`, `side` and
    `rank` (1 = first within its side); offense first, each side in rank order. Empty (same
    columns) when nothing qualifies."""
    pool = eligible(preds, run_time)
    if pool.is_empty():
        return _empty(preds)
    boost = pl.when(pl.col("team").is_in(list(followed))).then(FOLLOWED_BOOST).otherwise(1.0)
    pool = pool.with_columns(
        (pl.col("outperf_z") * boost).alias("score"), side_of().alias("side")
    ).filter(pl.col("score") > 0)
    if min_volume:
        floor = pl.col("target").replace_strict(
            dict(min_volume), default=None, return_dtype=pl.Float64
        )
        pool = pool.filter(floor.is_null() | (projection_expr() >= floor))
    parts = []
    for side, n in (("offense", n_offense), ("defense", n_defense)):
        part = pool.filter(pl.col("side") == side)
        if part.height and n > 0:
            parts.append(_pick(_best_per_player(part), n, max_per_team, group_caps))
    if not parts:
        return _empty(preds)
    return pl.concat(parts, how="vertical_relaxed")


def tough_spots(
    preds: pl.DataFrame,
    n: int = N_TOUGH,
    *,
    run_time: dt.datetime | None = None,
    exclude: Collection[str] = (),
    min_z: float = TOUGH_MIN_Z,
) -> pl.DataFrame:
    """Regular starters projected furthest below their own baseline (module doc): columns
    of `preds` plus `rank`, `score` (= `outperf_z`, negative) and `side`, most negative
    first."""
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
    return _pick(worst, n, 1).with_columns(side_of().alias("side"))


# ---- backtest -----------------------------------------------------------------------------------


@dataclass
class WatchBacktest:
    """Watch-list hit rate vs the base rate (exit criterion: hit rate > 50% in backtests).

    `picks`: every pick with `side`, `scored` (played, actual known), `hit` (actual >
    baseline) and `inside` (P10 <= actual <= P90). `by_season` / `by_side` / `by_group`:
    picks, scored, hits, hit_rate, the pool's `base_rate` (all eligible main-target role
    players) and `positive_rate` (the pool's players projected above baseline), `lift` =
    hit_rate - base_rate, `inside_rate`. `overall`: the same over everything.
    """

    picks: pl.DataFrame
    by_season: pl.DataFrame
    by_group: pl.DataFrame
    overall: dict[str, Any] = field(default_factory=dict)
    by_side: pl.DataFrame = field(default_factory=pl.DataFrame)
    short_weeks: dict[str, int] = field(default_factory=dict)  # weeks a side came up short

    def summary(self, prefix: str = "watch/") -> dict[str, Any]:
        """Flat numbers for W&B run summaries (overall, plus hit and base rate per side)."""
        out = {f"{prefix}{k}": v for k, v in self.overall.items()}
        for r in self.by_side.iter_rows(named=True) if self.by_side.height else []:
            out[f"{prefix}{r['side']}/hit_rate"] = r["hit_rate"]
            out[f"{prefix}{r['side']}/base_rate"] = r["base_rate"]
        for side, n in self.short_weeks.items():
            out[f"{prefix}{side}/short_weeks"] = n
        return out


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
    n_offense: int = N_OFFENSE,
    n_defense: int = N_DEFENSE,
    max_per_team: int = MAX_PER_TEAM,
    followed: Collection[str] = (),
    *,
    group_caps: Mapping[str, int] | None = GROUP_CAPS,
    min_volume: Mapping[str, float] | None = MIN_VOLUME,
) -> WatchBacktest:
    """Select each backtest week's lists from that week's rows (main targets with `actual` /
    `played` filled, `created_at` = that week's Tuesday) and score them against the base
    rate. Accepts one frame of many weeks, or several frames (a dict or list, e.g. one per
    target file)."""
    df = _frames(preds_by_week)
    picks, pools = [], []
    short = {"offense": 0, "defense": 0}
    quota = {"offense": n_offense, "defense": n_defense}
    if df.height:
        for _, week in df.group_by("season", "week", maintain_order=True):
            pools.append(eligible(week).with_columns(side_of().alias("side")))
            chosen = select_watchlist(
                week,
                n_offense,
                n_defense,
                max_per_team,
                followed,
                group_caps=group_caps,
                min_volume=min_volume,
            )
            for side, n in quota.items():
                short[side] += int(chosen.filter(pl.col("side") == side).height < n)
            picks.append(chosen)
    picks_df = _outcomes(pl.concat(picks, how="diagonal_relaxed")) if picks else pl.DataFrame()
    pool_df = _outcomes(pl.concat(pools, how="diagonal_relaxed")) if pools else pl.DataFrame()
    if pool_df.is_empty():
        empty = pl.DataFrame()
        return WatchBacktest(empty, empty, empty, {}, empty)
    by_season = _rates(picks_df, pool_df, ["season"])
    by_group = _rates(picks_df, pool_df, ["group"])
    by_side = _rates(picks_df, pool_df, ["side"])
    allr = _rates(
        picks_df.with_columns(pl.lit("all").alias("_all")),
        pool_df.with_columns(pl.lit("all").alias("_all")),
        ["_all"],
    ).row(0, named=True)
    overall = {k: v for k, v in allr.items() if k != "_all"}
    keep = [
        "season", "week", "side", "rank", "player_id", "player", "team", "group", "target",
        "p10", "p50", "p90", "baseline", "outperf_z", "confidence", "actual", "played",
        "scored", "hit", "inside",
    ]  # fmt: skip
    return WatchBacktest(picks_df.select(keep), by_season, by_group, overall, by_side, short)
