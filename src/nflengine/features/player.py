"""Player-model features, rows and baselines (plan P06; documentation/04 -> C, 11).

**Rows.** One row per (player, game) to predict:
- *history rows* (training and walk-forward backtests): every regular-season game a
  player took part in (`player_history`), so the targets are only games he played;
- *upcoming rows* (a live week): the players expected to play (`upcoming_rows`): on the
  team, active in one of its last 3 games, not listed Out / Doubtful or on a reserve list
  in the run's injury snapshot; for QBs only the expected starter (P03's resolver).

**As of.** Every feature of a row at time `t` (`tkey`) uses only games strictly before it
(`asof_join`); PFR and FTN columns lag one more week (D44). Team ratings are as-of rows
already (the (season, week) row is built from earlier weeks).

**Families** (feature-name prefixes):
- `own_*` (per target, `own_features`): the target stat over the player's earlier games in
  the target's pool: last game, last 4, season to date, last season, career counts, an
  exponentially weighted mean. These are also the baseline's ingredients.
- `use_*` usage (`usage_features`): snap share (his side) last game / last 3 / trend,
  target, air-yards, carry and dropback shares, red-zone shares, per-game volume.
- `team_*` team context (`team_features`): pass rate over expected, pace, EPA per play,
  plays per game, the team's offensive ratings, P03's predicted team points and expected
  margin for this game, and the market-implied team total.
- `eff_*` efficiency (`player_efficiency.efficiency_features`), `opp_*` opponent
  (`player_opponent.opponent_features`).
- `rip_*` ripple effects (`ripple_features`): usage left by regular teammates who missed
  the team's last game (or are listed Out this week), whether the expected QB is new to
  him, and his share of that QB's targets in their games together (graph Q2 / Q3 in
  Polars).
- `avail_*` availability (`availability_features`): his own injury-report status and
  practice participation for the week (the Friday view, D64), games of the team's last 3
  he missed, and whether he missed the last one.
- `cvg_*` coverage context (`player_coverage.coverage_features`, P08): the passing volume
  and depth of the offense a defensive back faces and of his own defense. Only the CB/S
  models use it (`models.player_model.GROUP_PREFIXES`); a weekly run that projects no CB/S
  target doesn't build it.

**Baselines** (documentation/11, `baselines`): *player rolling* = 0.5 x last 4 + 0.5 x
season to date, pulled toward last season's per-game mean with `K_LAST` pseudo-games
(early in the year the pull is strong); no game this season -> last season's mean; no
history -> the *position average for his role* (`role_table`: the group's mean per game
by snap-share bucket in his previous game, from earlier seasons only). Also the plain
season-to-date mean.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import polars as pl

from nflengine.features.player_data import (
    PlayerInputs,
    asof_join,
    team_games,
    team_play_totals,
    tkey,
)
from nflengine.models.player_schema import GROUP_PGROUPS, TARGETS, Target, pool_expr

ROW_COLS = [
    "player_id",
    "game_id",
    "season",
    "week",
    "t",
    "team",
    "opponent",
    "home",
    "pgroup",
    "position",
    "player",
    "kickoff_utc",
]
POOL_PGROUPS = sorted({p for g in GROUP_PGROUPS.values() for p in g})
K_LAST = 3.0  # pseudo-games of last season's mean in the rolling baseline
MIN_LAST_GAMES = 2  # last-season games needed for its mean to count
EWM_HALF_LIFE = 4.0  # games
TEAM_WINDOW = 8  # team games in rolling team context
REGULAR_SHARE = 0.10  # season-to-date usage share that makes a teammate a regular
RECENT_WEEKS = 4  # a regular whose last game with the team was this recent counts as vacated
RETURN_GAMES = 3  # games for the team (this / last season) that make an absent player a regular
RETURN_SNAP = 0.4  # ... with at least this average snap share (his side) in them
REPORT_OUT = ("Out", "Doubtful")
STATUS_ORD = {"Questionable": 1.0, "Doubtful": 2.0, "Out": 3.0}
RESERVE = "RES"
GONE = ("CUT", "RET", "UFA", "RFA", "TRD", "TRC", "TRT")
SNAP_BUCKETS = (0.2, 0.4, 0.6, 0.8)


def _side_snap() -> pl.Expr:
    """His side's snap share: defense for defensive position groups, else offense."""
    return (
        pl.when(pl.col("pgroup").is_in(["DL", "LB", "CB", "S"]))
        .then(pl.col("defense_pct"))
        .otherwise(pl.col("offense_pct"))
    )


# ---- rows -----------------------------------------------------------------------------------


def target_pgroups(targets: Sequence[Target] | None = None) -> list[str]:
    """The position groups whose rows `targets` need (`None`: every target's)."""
    return sorted({p for t in (targets or TARGETS) for p in GROUP_PGROUPS[t.group]})


def history_rows(hist: pl.DataFrame, pgroups: Sequence[str] | None = None) -> pl.DataFrame:
    """Prediction rows for every game a pool-eligible player took part in (`pgroups`: only
    these position groups, e.g. a weekly run that projects P06 targets builds no CB rows;
    default every pool group)."""
    return (
        hist.filter(
            pl.col("pgroup").is_in(list(pgroups) if pgroups is not None else POOL_PGROUPS)
            & (pl.col("played_off") | pl.col("played_def"))
        )
        .select(ROW_COLS)
        .unique(["player_id", "game_id"], keep="first")
        .sort("t", "player_id")
    )


def unavailable(
    inp: PlayerInputs, season: int, week: int, statuses: Sequence[str] = REPORT_OUT
) -> set[str]:
    """Players listed Out / Doubtful in the (season, week) injury report or on a reserve
    list in that week's roster (P04 rule; `rosters_weekly.status == 'RES'`)."""
    inj = inp.injuries.filter(
        (pl.col("season") == season)
        & (pl.col("week") == week)
        & pl.col("report_status").is_in(list(statuses))
    )
    res = inp.rosters.filter(
        (pl.col("season") == season) & (pl.col("week") == week) & (pl.col("status") == RESERVE)
    )
    return set(inj["gsis_id"].drop_nulls()) | set(res["gsis_id"].drop_nulls())


def upcoming_rows(
    inp: PlayerInputs,
    hist: pl.DataFrame,
    season: int,
    week: int,
    expected_qbs: pl.DataFrame,
    *,
    drop_unavailable: bool = True,
    pgroups: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Rows for the players expected to play in week `week` of `season` (live).

    A non-QB qualifies when his latest game before the week was for the team, he played
    (his side) in one of the team's last 3 completed games (or he is on the week's active
    roster after `RETURN_GAMES`+ games for the team this or last season at an average snap
    share >= `RETURN_SNAP`: back from IR), he
    isn't gone from the week's roster (cut, traded ...) and isn't listed Out / Doubtful /
    reserve (`drop_unavailable`).
    QBs: only `expected_qbs` (`team`, `qb_id`), the P03 expected starters. `pgroups`: only
    these position groups (default every pool group; QBs only if `QB` is among them).
    """
    groups = list(pgroups) if pgroups is not None else POOL_PGROUPS
    non_qb = [p for p in groups if p != "QB"]
    tg = team_games(inp.games)
    t_now = (season - 2000) * 22 + week
    slate = tg.filter(
        (pl.col("season") == season) & (pl.col("week") == week) & ~pl.col("completed")
    )
    last3 = (
        tg.filter(pl.col("completed") & (pl.col("t") < t_now))
        .sort("t", descending=True)
        .group_by("team", maintain_order=True)
        .head(3)
        .select("team", "game_id")
    )
    active = hist.filter(pl.col("played_off") | pl.col("played_def")).join(
        last3, on=["team", "game_id"], how="inner"
    )
    latest = (
        hist.filter(pl.col("t") < t_now)
        .sort("t", descending=True)
        .group_by("player_id", maintain_order=True)
        .first()
        .select("player_id", "team", "pgroup", "position", "player")
    )
    cand = (
        latest.join(active.select("player_id", "team").unique(), on=["player_id", "team"])
        .filter(pl.col("pgroup").is_in(non_qb))
        .join(slate.select("team", "game_id", "opponent", "home", "kickoff_utc"), on="team")
    )
    roster = inp.rosters.filter((pl.col("season") == season) & (pl.col("week") == week))
    if roster.height:
        # a regular back from a long absence (IR) isn't in the team's last 3 games: take
        # players on the week's active roster with 3+ games for the team this or last season
        act = roster.filter(pl.col("status") == "ACT").select(
            pl.col("gsis_id").alias("player_id"), "team"
        )
        regular = (
            hist.filter(
                (pl.col("played_off") | pl.col("played_def"))
                & (pl.col("t") < t_now)
                & pl.col("season").is_in([season - 1, season])
            )
            .with_columns(_side_snap().alias("_snap"))
            .group_by("player_id", "team")
            .agg(pl.len().alias("_n"), pl.col("_snap").mean().alias("_snap_mean"))
            # a real role there (sonnet-xhigh, 2025 retrospective: snap share >= 0.4 keeps 54
            # of 66 returning starters and drops three quarters of the false additions)
            .filter((pl.col("_n") >= RETURN_GAMES) & (pl.col("_snap_mean") >= RETURN_SNAP))
            .select("player_id", "team")
        )
        back = (
            latest.join(act, on=["player_id", "team"])
            .join(regular, on=["player_id", "team"])
            .filter(pl.col("pgroup").is_in(non_qb))
            .join(slate.select("team", "game_id", "opponent", "home", "kickoff_utc"), on="team")
            .join(cand.select("player_id"), on="player_id", how="anti")
        )
        cand = pl.concat([cand, back.select(cand.columns)])
        gone = roster.filter(pl.col("status").is_in(list(GONE)))
        cand = cand.filter(~pl.col("player_id").is_in(gone["gsis_id"].drop_nulls().to_list()))
        # a player on another team's roster this week moved
        moved = (
            roster.sort(pl.col("status") == "ACT", descending=True)
            .select(pl.col("gsis_id").alias("player_id"), pl.col("team").alias("_rt"))
            .unique("player_id", keep="first", maintain_order=True)
        )
        cand = cand.join(moved, on="player_id", how="left").filter(
            pl.col("_rt").is_null() | (pl.col("_rt") == pl.col("team"))
        )
        cand = cand.drop("_rt")
    if drop_unavailable:
        out = unavailable(inp, season, week)
        cand = cand.filter(~pl.col("player_id").is_in(list(out)))
    if "QB" not in groups:
        expected_qbs = expected_qbs.clear()
    qbs = (
        expected_qbs.rename({"qb_id": "player_id"})
        .join(slate.select("team", "game_id", "opponent", "home", "kickoff_utc"), on="team")
        .join(
            latest.select("player_id", "position", "player"),
            on="player_id",
            how="left",
        )
        .with_columns(pl.lit("QB").alias("pgroup"), pl.col("position").fill_null("QB"))
    )
    rows = pl.concat(
        [
            cand.select(
                "player_id",
                "team",
                "pgroup",
                "position",
                "player",
                "game_id",
                "opponent",
                "home",
                "kickoff_utc",
            ),
            qbs.select(
                "player_id",
                "team",
                "pgroup",
                "position",
                "player",
                "game_id",
                "opponent",
                "home",
                "kickoff_utc",
            ),
        ],
        how="vertical_relaxed",
    ).unique(["player_id", "game_id"], keep="last")
    return rows.with_columns(
        pl.lit(season).cast(pl.Int32).alias("season"),
        pl.lit(week).cast(pl.Int32).alias("week"),
        pl.lit(t_now).cast(pl.Int32).alias("t"),
    ).select(ROW_COLS)


# ---- own stat (per target) -----------------------------------------------------------------


def _lag_for(target: Target) -> int:
    return target.label_lag


def own_features(hist: pl.DataFrame, rows: pl.DataFrame, target: Target) -> pl.DataFrame:
    """The target stat over the player's earlier games in the target's pool."""
    col = target.column
    h = (
        hist.filter(pool_expr(target.group) & pl.col(col).is_not_null())
        .select("player_id", "season", "t", pl.col(col).cast(pl.Float64).alias("_y"))
        .sort("player_id", "t")
    )
    by = "player_id"
    h = h.with_columns(
        pl.col("_y").alias("own_l1"),
        pl.col("_y").rolling_mean(4, min_samples=1).over(by).alias("own_l4"),
        pl.col("_y").ewm_mean(half_life=EWM_HALF_LIFE, ignore_nulls=True).over(by).alias("own_ewm"),
        pl.col("_y").cum_sum().over(by, "season").alias("_cs"),
        pl.int_range(1, pl.len() + 1).over(by, "season").cast(pl.Int32).alias("_n_season"),
        pl.int_range(1, pl.len() + 1).over(by).cast(pl.Int32).alias("own_n_career"),
        pl.col("_y").rolling_mean(4, min_samples=1).over(by, "season").alias("_l4_season"),
    ).with_columns((pl.col("_cs") / pl.col("_n_season")).alias("_std"))
    lag = _lag_for(target)
    j = asof_join(
        rows.select("player_id", "game_id", "season", "t"),
        h.select(
            "player_id",
            "t",
            pl.col("season").alias("_hseason"),
            "own_l1",
            "own_l4",
            "own_ewm",
            "own_n_career",
            "_n_season",
            "_std",
            "_l4_season",
        ),
        by="player_id",
        lag=lag,
    )
    same = pl.col("_hseason") == pl.col("season")
    j = j.with_columns(
        pl.when(same).then(pl.col("_std")).alias("own_std"),
        pl.when(same).then(pl.col("_l4_season")).alias("own_l4_season"),
        pl.when(same).then(pl.col("_n_season")).otherwise(0).fill_null(0).alias("own_n_season"),
        pl.col("own_n_career").fill_null(0),
    )
    last = (
        h.group_by("player_id", "season")
        .agg(pl.col("_y").mean().alias("own_ls"), pl.len().cast(pl.Int32).alias("own_n_ls"))
        .with_columns((pl.col("season") + 1).cast(pl.Int32).alias("season"))
    )
    j = j.join(last, on=["player_id", "season"], how="left").with_columns(
        pl.col("own_n_ls").fill_null(0),
        pl.when(pl.col("own_n_ls") >= MIN_LAST_GAMES).then(pl.col("own_ls")).alias("own_ls"),
    )
    keep = [
        "own_l1",
        "own_l4",
        "own_l4_season",
        "own_ewm",
        "own_std",
        "own_ls",
        "own_n_season",
        "own_n_career",
        "own_n_ls",
    ]
    return j.select(
        "player_id",
        "game_id",
        *[pl.col(c).cast(pl.Float64) for c in keep],
    )


def role_table(hist: pl.DataFrame, target: Target) -> pl.DataFrame:
    """Position average for role: the target's mean per game in the group, by season and
    the snap-share bucket of the player's previous game (`none` = no previous game).
    Row (season s) = averages over seasons before s, so it is as of any week of s."""
    col = target.column
    h = hist.sort("player_id", "t").with_columns(
        _side_snap().shift(1).over("player_id").alias("_prev_snap")
    )
    h = h.filter(pool_expr(target.group) & pl.col(col).is_not_null()).with_columns(
        snap_bucket(pl.col("_prev_snap")).alias("snap_bucket")
    )
    per = h.group_by("season", "snap_bucket").agg(
        pl.col(col).cast(pl.Float64).sum().alias("_s"), pl.len().alias("_n")
    )
    seasons = sorted(hist["season"].unique().to_list())
    out = []
    for s in seasons:
        prior = per.filter(pl.col("season") < s)
        if prior.is_empty():
            continue
        out.append(
            prior.group_by("snap_bucket")
            .agg((pl.col("_s").sum() / pl.col("_n").sum()).alias("role_avg"))
            .with_columns(pl.lit(s).cast(pl.Int32).alias("season"))
        )
    return (
        pl.concat(out)
        if out
        else pl.DataFrame(
            schema={"snap_bucket": pl.String, "role_avg": pl.Float64, "season": pl.Int32}
        )
    )


def snap_bucket(snap: pl.Expr) -> pl.Expr:
    lo, b2, b3, hi = SNAP_BUCKETS
    return (
        pl.when(snap.is_null())
        .then(pl.lit("none"))
        .when(snap < lo)
        .then(pl.lit("0-20"))
        .when(snap < b2)
        .then(pl.lit("20-40"))
        .when(snap < b3)
        .then(pl.lit("40-60"))
        .when(snap < hi)
        .then(pl.lit("60-80"))
        .otherwise(pl.lit("80-100"))
    )


def baselines(frame: pl.DataFrame, roles: pl.DataFrame) -> pl.DataFrame:
    """Add `baseline`, `baseline_source`, `baseline_season_mean`, `baseline_role` to a
    frame with the `own_*` columns, `season` and `use_snap_l1` (documentation/11)."""
    f = frame.with_columns(snap_bucket(pl.col("use_snap_l1")).alias("snap_bucket")).join(
        roles, on=["season", "snap_bucket"], how="left"
    )
    n = pl.col("own_n_season")
    mix = 0.5 * pl.col("own_l4_season") + 0.5 * pl.col("own_std")
    has_ls = pl.col("own_ls").is_not_null()
    rolling = (
        pl.when(has_ls)
        .then((n * mix + K_LAST * pl.col("own_ls")) / (n + K_LAST))
        .when(n >= 2)
        .then(mix)
        .otherwise((n * mix + 2.0 * pl.col("role_avg")) / (n + 2.0))
    )
    base = (
        pl.when(n >= 1)
        .then(rolling)
        .when(has_ls)
        .then(pl.col("own_ls"))
        .when(pl.col("own_n_career") >= 1)
        .then(pl.col("own_ewm"))
        .otherwise(pl.col("role_avg"))
    )
    source = (
        pl.when(n >= 1)
        .then(pl.lit("rolling"))
        .when(has_ls)
        .then(pl.lit("last_season"))
        .when(pl.col("own_n_career") >= 1)
        .then(pl.lit("rolling"))
        .otherwise(pl.lit("role"))
    )
    return f.with_columns(
        base.fill_null(pl.col("own_ewm")).fill_null(pl.col("role_avg")).alias("baseline"),
        source.alias("baseline_source"),
        pl.col("own_std").alias("baseline_season_mean"),
        pl.col("role_avg").alias("baseline_role"),
    ).drop("snap_bucket", "role_avg")


# ---- usage -----------------------------------------------------------------------------------


def usage_features(hist: pl.DataFrame, rows: pl.DataFrame) -> pl.DataFrame:
    """Snap share and usage shares over the player's earlier games (any side he played)."""
    h = (
        hist.filter(pl.col("played_off") | pl.col("played_def"))
        .sort("player_id", "t")
        .with_columns(_side_snap().alias("_snap"))
    )
    by = "player_id"

    def roll(col: str, n: int, alias: str) -> pl.Expr:
        return pl.col(col).rolling_mean(n, min_samples=1).over(by).alias(alias)

    h = h.with_columns(
        pl.col("_snap").alias("use_snap_l1"),
        roll("_snap", 2, "use_snap_l2"),
        roll("_snap", 3, "use_snap_l3"),
        pl.col("_snap").shift(2).rolling_mean(4, min_samples=1).over(by).alias("_snap_prev"),
        roll("target_share", 4, "use_tgt_share_l4"),
        roll("air_yards_share", 4, "use_air_share_l4"),
        roll("carry_share", 4, "use_carry_share_l4"),
        roll("dropback_share", 4, "use_db_share_l4"),
        roll("targets", 4, "use_targets_l4"),
        roll("carries", 4, "use_carries_l4"),
        roll("receptions", 4, "use_rec_l4"),
        roll("dropbacks", 4, "use_dropbacks_l4"),
        roll("attempts", 4, "use_attempts_l4"),
        roll("defense_snaps", 4, "use_def_snaps_l4"),
        roll("offense_snaps", 4, "use_off_snaps_l4"),
        pl.col("rz_targets").rolling_sum(8, min_samples=1).over(by).alias("_rzt"),
        pl.col("team_rz_targets").rolling_sum(8, min_samples=1).over(by).alias("_trzt"),
        pl.col("rz_carries").rolling_sum(8, min_samples=1).over(by).alias("_rzc"),
        pl.col("team_rz_carries").rolling_sum(8, min_samples=1).over(by).alias("_trzc"),
        pl.col("target_share").cum_sum().over(by, "season").alias("_ts_cum"),
        pl.col("carry_share").cum_sum().over(by, "season").alias("_cs_cum"),
        pl.int_range(1, pl.len() + 1).over(by, "season").alias("_ns"),
    ).with_columns(
        (pl.col("use_snap_l2") - pl.col("_snap_prev")).alias("use_snap_trend"),
        pl.when(pl.col("_trzt") > 0)
        .then(pl.col("_rzt") / pl.col("_trzt"))
        .alias("use_rz_tgt_share_l8"),
        pl.when(pl.col("_trzc") > 0)
        .then(pl.col("_rzc") / pl.col("_trzc"))
        .alias("use_rz_carry_share_l8"),
        (pl.col("_ts_cum") / pl.col("_ns")).alias("_tgt_share_std"),
        (pl.col("_cs_cum") / pl.col("_ns")).alias("_carry_share_std"),
    )
    cols = [
        "use_snap_l1",
        "use_snap_l2",
        "use_snap_l3",
        "use_snap_trend",
        "use_tgt_share_l4",
        "use_air_share_l4",
        "use_carry_share_l4",
        "use_db_share_l4",
        "use_targets_l4",
        "use_carries_l4",
        "use_rec_l4",
        "use_dropbacks_l4",
        "use_attempts_l4",
        "use_def_snaps_l4",
        "use_off_snaps_l4",
        "use_rz_tgt_share_l8",
        "use_rz_carry_share_l8",
    ]
    j = asof_join(
        rows.select("player_id", "game_id", "season", "t", "team"),
        h.select(
            "player_id",
            "t",
            pl.col("season").alias("_hs"),
            pl.col("team").alias("_ht"),
            "_tgt_share_std",
            "_carry_share_std",
            *cols,
        ),
        by="player_id",
    )
    same = pl.col("_hs") == pl.col("season")
    return j.with_columns(
        pl.when(same).then(pl.col("_tgt_share_std")).alias("use_tgt_share_std"),
        pl.when(same).then(pl.col("_carry_share_std")).alias("use_carry_share_std"),
        (pl.col("_ht") != pl.col("team")).cast(pl.Float64).alias("use_new_team"),
        (pl.col("t") - pl.col("t_hist")).cast(pl.Float64).alias("use_weeks_since_game"),
    ).select(
        "player_id",
        "game_id",
        *[pl.col(c).cast(pl.Float64) for c in cols],
        pl.col("use_tgt_share_std").cast(pl.Float64),
        pl.col("use_carry_share_std").cast(pl.Float64),
        "use_new_team",
        "use_weeks_since_game",
    )


# ---- team context ----------------------------------------------------------------------------


def team_features(
    inp: PlayerInputs, rows: pl.DataFrame, context: pl.DataFrame | None = None
) -> pl.DataFrame:
    """The player's team: rolling style and volume, as-of ratings, and the game's
    expected script (P03 predicted points and margin; market-implied team total).

    `context` (`game_context`): one row per (game_id, team) with `ctx_pred_points`,
    `ctx_pred_opp_points`, `ctx_exp_margin` (walk-forward / live P03 predictions).
    """
    tg = team_games(inp.games).filter(pl.col("completed"))
    tot = team_play_totals(inp.plays)
    th = tg.join(tot, on=["game_id", "team"], how="inner").sort("team", "t")
    w = TEAM_WINDOW
    th = th.with_columns(
        pl.col("team_proe").rolling_mean(w, min_samples=1).over("team").alias("team_proe_l8"),
        pl.col("team_plays").rolling_mean(w, min_samples=1).over("team").alias("team_plays_l8"),
        pl.col("team_pass_rate")
        .rolling_mean(w, min_samples=1)
        .over("team")
        .alias("team_pass_rate_l8"),
        pl.col("team_epa_play")
        .rolling_mean(w, min_samples=1)
        .over("team")
        .alias("team_epa_play_l8"),
        pl.col("team_dropbacks")
        .rolling_mean(w, min_samples=1)
        .over("team")
        .alias("team_dropbacks_l8"),
        pl.col("team_rushes").rolling_mean(w, min_samples=1).over("team").alias("team_rushes_l8"),
        pl.col("team_targets").rolling_mean(w, min_samples=1).over("team").alias("team_targets_l8"),
    )
    roll_cols = [
        "team_proe_l8",
        "team_plays_l8",
        "team_pass_rate_l8",
        "team_epa_play_l8",
        "team_dropbacks_l8",
        "team_rushes_l8",
        "team_targets_l8",
    ]
    base = rows.select("player_id", "game_id", "season", "week", "t", "team", "home")
    j = asof_join(base, th.select("team", "t", *roll_cols), by="team").drop("t_hist")
    # as-of ratings: the (season, week) row is built from earlier weeks (D44)
    tr = inp.team_ratings
    if tr.height:
        rcols = [
            c
            for c in ("off_epa", "off_pass_epa", "off_rush_epa", "def_epa", "prior_weight")
            if c in tr.columns
        ]
        rat = tr.select(
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            "team",
            *[pl.col(c).alias(f"team_{c}") for c in rcols],
        )
        rat = rat.with_columns(tkey().alias("t")).sort("t")
        # latest as-of row at or before the row's week (t_hist <= t): lag -1 trick
        j = asof_join(j, rat.drop("season", "week"), by="team", lag=-1).drop("t_hist")
    gf = inp.game_features
    if gf.height and {"mkt_home_points", "mkt_away_points", "spread_line"} <= set(gf.columns):
        mk = gf.select("game_id", "mkt_home_points", "mkt_away_points", "spread_line", "total_line")
        j = (
            j.join(mk, on="game_id", how="left")
            .with_columns(
                pl.when(pl.col("home"))
                .then(pl.col("mkt_home_points"))
                .otherwise(pl.col("mkt_away_points"))
                .alias("team_mkt_points"),
                pl.when(pl.col("home"))
                .then(pl.col("spread_line"))
                .otherwise(-pl.col("spread_line"))
                .alias("team_mkt_spread"),
                pl.col("total_line").alias("team_mkt_total"),
            )
            .drop("mkt_home_points", "mkt_away_points", "spread_line", "total_line")
        )
    if context is not None and context.height:
        j = j.join(
            context.select(
                "game_id",
                "team",
                pl.col("ctx_pred_points").alias("team_pred_points"),
                pl.col("ctx_pred_opp_points").alias("team_pred_opp_points"),
                pl.col("ctx_exp_margin").alias("team_exp_margin"),
            ),
            on=["game_id", "team"],
            how="left",
        )
    j = j.with_columns(pl.col("home").cast(pl.Float64).alias("team_home"))
    feats = [c for c in j.columns if c.startswith("team_")]
    return j.select("player_id", "game_id", *[pl.col(c).cast(pl.Float64) for c in feats])


def game_context(game_preds: pl.DataFrame) -> pl.DataFrame:
    """P03 predictions per (game, team): predicted points for / against, expected margin.
    `game_preds` = model-only walk-forward or live rows (`pred_home_points`,
    `pred_away_points`, `expected_margin` from the home side)."""
    g = game_preds.select(
        "game_id",
        "home_team",
        "away_team",
        "pred_home_points",
        "pred_away_points",
        "expected_margin",
    )
    home = g.select(
        "game_id",
        pl.col("home_team").alias("team"),
        pl.col("pred_home_points").alias("ctx_pred_points"),
        pl.col("pred_away_points").alias("ctx_pred_opp_points"),
        pl.col("expected_margin").alias("ctx_exp_margin"),
    )
    away = g.select(
        "game_id",
        pl.col("away_team").alias("team"),
        pl.col("pred_away_points").alias("ctx_pred_points"),
        pl.col("pred_home_points").alias("ctx_pred_opp_points"),
        (-pl.col("expected_margin")).alias("ctx_exp_margin"),
    )
    return pl.concat([home, away]).unique(["game_id", "team"], keep="last")


# ---- ripple effects -----------------------------------------------------------------------


def ripple_features(
    inp: PlayerInputs,
    hist: pl.DataFrame,
    rows: pl.DataFrame,
    expected_qbs: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Usage left open by teammates, and the expected QB's history with the player.

    - `rip_vacated_tgt` / `rip_vacated_car`: season-to-date target / carry share of the
      team's regular offensive players (share >= `REGULAR_SHARE`, 2+ games) who did not
      play its last game but played within the last `RECENT_WEEKS` weeks, minus his own (a
      returning player doesn't vacate for himself).
    - `rip_out_tgt` / `rip_out_car`: the same for regulars listed Out / Doubtful in the
      week's injury report (the Friday view, D64).
    - `rip_qb_new`: the expected QB isn't the main QB of the player's last game with the
      team.
    - `rip_qb_tgt_share`, `rip_qb_games`: his share of the team's targets in earlier
      games where that QB was the main QB, and how many such games (graph Q3).
    `expected_qbs`: (`game_id`, `team`, `qb_id`), the P03 Tuesday / live expected starter.
    """
    tg = team_games(inp.games).filter(pl.col("completed"))
    off = hist.filter(pl.col("played_off") & pl.col("pgroup").is_in(["RB", "WR", "TE", "FB"]))
    # season-to-date shares through each game, per player
    sh = off.sort("player_id", "t").with_columns(
        (
            pl.col("target_share").cum_sum().over("player_id", "season")
            / pl.int_range(1, pl.len() + 1).over("player_id", "season")
        ).alias("_ts"),
        (
            pl.col("carry_share").cum_sum().over("player_id", "season")
            / pl.int_range(1, pl.len() + 1).over("player_id", "season")
        ).alias("_cs"),
        pl.int_range(1, pl.len() + 1).over("player_id", "season").alias("_n"),
    )
    # each team's last completed game before each row
    base = rows.select("player_id", "game_id", "season", "t", "team")
    last = asof_join(
        base.select("game_id", "team", "season", "t").unique(["game_id", "team"]),
        tg.select("team", "t", pl.col("game_id").alias("last_game"), pl.col("season").alias("_ls")),
        by="team",
    )
    # regulars of the team this season as of each team-row: their latest state before t
    reg_state = asof_join(
        last.select("game_id", "team", "season", "t", "last_game").join(
            sh.select("player_id", "team", "season").unique(), on=["team", "season"]
        ),
        sh.select("player_id", "team", "t", pl.col("season").alias("_ps"), "_ts", "_cs", "_n"),
        by=["player_id", "team"],
    ).filter(
        (pl.col("_ps") == pl.col("season"))
        & (pl.col("_n") >= 2)
        # recently gone only: a regular cut, traded or lost for the season weeks ago has
        # already had his usage redistributed in the games since (sonnet-xhigh review)
        & ((pl.col("t") - pl.col("t_hist")) <= RECENT_WEEKS)
    )
    played_last = off.select(pl.col("game_id").alias("last_game"), "player_id").with_columns(
        pl.lit(True).alias("_pl")
    )
    reg_state = reg_state.join(played_last, on=["last_game", "player_id"], how="left").with_columns(
        pl.col("_pl").fill_null(False)
    )
    missing = reg_state.filter(~pl.col("_pl"))
    out_ids = (
        inp.injuries.filter(pl.col("report_status").is_in(list(REPORT_OUT)))
        .select(
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            pl.col("gsis_id").alias("player_id"),
            pl.lit(True).alias("_out"),
        )
        .unique()
    )
    week_of = rows.select("game_id", "team", "week").unique(["game_id", "team"])
    listed = reg_state.join(week_of, on=["game_id", "team"]).join(
        out_ids, on=["season", "week", "player_id"], how="inner"
    )

    def vac(df: pl.DataFrame, prefix: str) -> pl.DataFrame:
        return df.select(
            "game_id",
            "team",
            pl.col("player_id").alias("_mate"),
            pl.when(pl.col("_ts") >= REGULAR_SHARE)
            .then(pl.col("_ts"))
            .otherwise(0.0)
            .alias(f"{prefix}_tgt"),
            pl.when(pl.col("_cs") >= REGULAR_SHARE)
            .then(pl.col("_cs"))
            .otherwise(0.0)
            .alias(f"{prefix}_car"),
        )

    vm, vo = vac(missing, "rip_vacated"), vac(listed, "rip_out")
    # the union (missed the last game OR ruled out), each teammate once: the watch list's
    # role-change test. Not a model feature (prefix `open_`)
    union = pl.concat(
        [
            vm.rename({"rip_vacated_tgt": "open_tgt", "rip_vacated_car": "open_car"}),
            vo.rename({"rip_out_tgt": "open_tgt", "rip_out_car": "open_car"}),
        ]
    ).unique(["game_id", "team", "_mate"], keep="first")
    j = base
    for v, pre in ((vm, "rip_vacated"), (vo, "rip_out"), (union, "open")):
        # sorted by teammate before summing: float accumulation follows row order, which the
        # joins above don't fix (two identical builds differed at 1e-16, and adding rows of
        # other position groups moved it too)
        tot = (
            v.sort("game_id", "team", "_mate")
            .group_by("game_id", "team", maintain_order=True)
            .agg(pl.col(f"{pre}_tgt").sum(), pl.col(f"{pre}_car").sum())
        )
        own = v.rename({"_mate": "player_id"}).select(
            "game_id",
            "team",
            "player_id",
            pl.col(f"{pre}_tgt").alias("_ot"),
            pl.col(f"{pre}_car").alias("_oc"),
        )
        j = (
            j.join(tot, on=["game_id", "team"], how="left")
            .join(own, on=["game_id", "team", "player_id"], how="left")
            .with_columns(
                (pl.col(f"{pre}_tgt").fill_null(0) - pl.col("_ot").fill_null(0)).alias(
                    f"{pre}_tgt"
                ),
                (pl.col(f"{pre}_car").fill_null(0) - pl.col("_oc").fill_null(0)).alias(
                    f"{pre}_car"
                ),
            )
            .drop("_ot", "_oc")
        )
    # QB history with the player (graph Q3 in Polars)
    if expected_qbs is not None and expected_qbs.height:
        mq = hist.filter(pl.col("main_qb")).select(
            "game_id", "team", pl.col("player_id").alias("_qb")
        )
        catches = off.join(mq, on=["game_id", "team"], how="inner").sort("player_id", "_qb", "t")
        catches = catches.with_columns(
            pl.col("targets").cum_sum().over("player_id", "_qb").alias("_tg"),
            pl.col("team_targets").cum_sum().over("player_id", "_qb").alias("_ttg"),
            pl.int_range(1, pl.len() + 1).over("player_id", "_qb").alias("_g"),
        )
        eq = base.join(
            expected_qbs.select("game_id", "team", pl.col("qb_id").alias("_qb")),
            on=["game_id", "team"],
            how="left",
        )
        hq = asof_join(
            eq.filter(pl.col("_qb").is_not_null()),
            catches.select("player_id", "_qb", "t", "_tg", "_ttg", "_g"),
            by=["player_id", "_qb"],
        )
        hq = hq.select(
            "player_id",
            "game_id",
            pl.when(pl.col("_ttg") > 0)
            .then(pl.col("_tg") / pl.col("_ttg"))
            .alias("rip_qb_tgt_share"),
            pl.col("_g").fill_null(0).cast(pl.Float64).alias("rip_qb_games"),
        )
        # the main QB of the player's last game with this team
        recent = (
            off.join(mq, on=["game_id", "team"], how="inner")
            .sort("player_id", "t")
            .with_columns(pl.col("_qb").alias("_qb_last"))
        )
        rq = asof_join(
            eq.select("player_id", "game_id", "t", "team", "_qb"),
            recent.select("player_id", "team", "t", "_qb_last"),
            by=["player_id", "team"],
        ).select(
            "player_id",
            "game_id",
            pl.when(pl.col("_qb_last").is_null() | pl.col("_qb").is_null())
            .then(None)
            .otherwise((pl.col("_qb_last") != pl.col("_qb")).cast(pl.Float64))
            .alias("rip_qb_new"),
        )
        j = j.join(hq, on=["player_id", "game_id"], how="left").join(
            rq, on=["player_id", "game_id"], how="left"
        )
    feats = [c for c in j.columns if c.startswith(("rip_", "open_"))]
    return j.select("player_id", "game_id", *[pl.col(c).cast(pl.Float64) for c in feats])


# ---- availability ---------------------------------------------------------------------------


def availability_features(
    inp: PlayerInputs, hist: pl.DataFrame, rows: pl.DataFrame
) -> pl.DataFrame:
    """His own report status and practice level for the week (Friday view, D64), and the
    team's recent games he missed."""
    inj = (
        inp.injuries.select(
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            pl.col("gsis_id").alias("player_id"),
            "report_status",
            "practice_status",
        )
        .drop_nulls("player_id")
        .unique(["season", "week", "player_id"], keep="last")
    )
    j = rows.select("player_id", "game_id", "season", "week", "t", "team").join(
        inj, on=["season", "week", "player_id"], how="left"
    )
    ps = pl.col("practice_status").fill_null("")
    j = j.with_columns(
        pl.col("report_status")
        .replace_strict(STATUS_ORD, default=0.0, return_dtype=pl.Float64)
        .alias("avail_status"),
        ps.str.contains("Did Not").cast(pl.Float64).alias("avail_dnp"),
        ps.str.contains("Limited").cast(pl.Float64).alias("avail_limited"),
    )
    # the team's last 3 completed games before the row, and whether he played them
    tg = (
        team_games(inp.games)
        .filter(pl.col("completed"))
        .sort("team", "t")
        .with_columns(pl.int_range(0, pl.len()).over("team").alias("_gi"))
    )
    played = (
        hist.filter(pl.col("played_off") | pl.col("played_def"))
        .select("player_id", pl.col("game_id").alias("_g"))
        .with_columns(pl.lit(1.0).alias("_p"))
    )
    last = asof_join(
        j.select("player_id", "game_id", "team", "t"), tg.select("team", "t", "_gi"), by="team"
    ).filter(pl.col("_gi").is_not_null())
    back = pl.concat(
        [
            last.with_columns((pl.col("_gi") - k).alias("_gk"), pl.lit(k).alias("_k"))
            for k in range(3)
        ]
    ).filter(pl.col("_gk") >= 0)
    team_last = (
        back.join(
            tg.select("team", pl.col("_gi").alias("_gk"), pl.col("game_id").alias("_g")),
            on=["team", "_gk"],
        )
        .join(played, on=["player_id", "_g"], how="left")
        .with_columns(pl.col("_p").fill_null(0.0))
        .group_by("player_id", "game_id")
        .agg(
            (1.0 - pl.col("_p")).sum().alias("avail_missed_l3"),
            (1.0 - pl.col("_p").filter(pl.col("_k") == 0).first()).alias("avail_missed_last"),
        )
    )
    j = j.join(team_last, on=["player_id", "game_id"], how="left")
    feats = [c for c in j.columns if c.startswith("avail_")]
    return j.select("player_id", "game_id", *[pl.col(c).cast(pl.Float64) for c in feats])


# ---- assembly --------------------------------------------------------------------------------


def build_features(
    inp: PlayerInputs,
    hist: pl.DataFrame,
    rows: pl.DataFrame,
    *,
    context: pl.DataFrame | None = None,
    expected_qbs: pl.DataFrame | None = None,
    log: Callable[[str], None] = print,
    coverage: bool = True,
) -> pl.DataFrame:
    """Rows + every shared feature family (not the per-target `own_*`, see
    `target_frame`). `coverage` adds the `cvg_*` family (P08, used only by the CB/S models);
    a weekly run that projects no CB/S target leaves it out."""
    import time

    from nflengine.features.player_coverage import coverage_features
    from nflengine.features.player_efficiency import efficiency_features
    from nflengine.features.player_opponent import opponent_features

    out = rows
    fams: list[tuple[str, Callable[[], pl.DataFrame]]] = [
        ("usage", lambda: usage_features(hist, rows)),
        ("team", lambda: team_features(inp, rows, context)),
        ("ripple", lambda: ripple_features(inp, hist, rows, expected_qbs)),
        ("availability", lambda: availability_features(inp, hist, rows)),
        ("efficiency", lambda: efficiency_features(inp, hist, rows)),
        ("opponent", lambda: opponent_features(inp, hist, rows)),
    ]
    if coverage:
        fams.append(("coverage", lambda: coverage_features(inp, hist, rows)))
    for name, fn in fams:
        t0 = time.time()
        f = fn()
        if f.height != rows.height:
            raise ValueError(f"{name} features: {f.height} rows for {rows.height}")
        out = out.join(f, on=["player_id", "game_id"], how="left")
        log(f"  {name}: {len(f.columns) - 2} features ({time.time() - t0:.1f} s)")
    return out


def expected_qbs_from_games(game_features: pl.DataFrame) -> pl.DataFrame:
    """(`game_id`, `team`, `qb_id`) from the P03 game features (Tuesday expected starter;
    a live frame carries the live resolver's pick)."""
    sides = [
        game_features.select(
            "game_id",
            pl.col(f"{side}_team").alias("team"),
            pl.col(f"{side}_qb_id").alias("qb_id"),
        )
        for side in ("home", "away")
    ]
    return pl.concat(sides).drop_nulls("qb_id").unique(["game_id", "team"], keep="last")


def target_frame(
    feats: pl.DataFrame,
    hist: pl.DataFrame,
    target: Target,
    roles: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """The modelling frame of one target: its pool's rows (history rows in the pool, with
    the label; upcoming rows of the group's position groups, label null), the shared
    features, `own_*` and the baselines. Label column: `y`."""
    pg = list(GROUP_PGROUPS[target.group])
    labels = hist.filter(pool_expr(target.group)).select(
        "player_id", "game_id", pl.col(target.column).cast(pl.Float64).alias("y")
    )
    hist_games = set(hist["game_id"].unique().to_list())
    f = feats.filter(pl.col("pgroup").is_in(pg)).join(
        labels, on=["player_id", "game_id"], how="left"
    )
    # history rows must be in the pool (a backup QB's game isn't a QB target row);
    # upcoming rows (no box score yet) stay
    in_hist = pl.col("game_id").is_in(list(hist_games))
    pool_rows = (
        hist.filter(pool_expr(target.group))
        .select("player_id", "game_id")
        .with_columns(pl.lit(True).alias("_pool"))
    )
    f = (
        f.join(pool_rows, on=["player_id", "game_id"], how="left")
        .filter(~in_hist | pl.col("_pool").fill_null(False))
        .drop("_pool")
    )
    f = f.filter(pl.col("season") >= target.start_season)
    own = own_features(hist, f.select(ROW_COLS), target)
    f = f.join(own, on=["player_id", "game_id"], how="left")
    roles = roles if roles is not None else role_table(hist, target)
    return baselines(f, roles)
