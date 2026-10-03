"""Placeholder "Players to watch" until the player model exists (plans/P04, documentation/06).

A transparent heuristic, labeled `confidence = "low"` and `source = "heuristic"`: the skill
players (WR, TE, RB) whose usage has risen the most lately, weighted by how weak the
opponent's defense is against that kind of play. It is replaced by projections in P06.

For the upcoming week N of a season (candidates are all rows from weeks strictly before N):
- Candidate: a WR / TE / RB whose team plays in week N, who played offense in his team's most
  recent game before N, with a recent offensive snap share of at least `MIN_SNAP_SHARE`.
- Usage: WR / TE use target share (`player_games.target_share`). An RB uses whichever of
  carry share (his carries / the team's carries in that game) and target share rose more.
- Windows: "recent" = the player's last `RECENT_GAMES` games played this season (weeks < N).
  "before" = his earlier games this season (when there are at least `MIN_BEFORE_GAMES`),
  otherwise last season's regular-season average (`usage_before_source`). A player with
  neither is skipped. Games he missed don't count (usage while active).
- Opponent weakness: the opponent's `team_ratings` row for (season, N) (the as-of row, built
  from weeks before N): `def_pass_epa` for WR / TE, `def_rush_epa` for RB. `def_*` is EPA
  allowed, so `opp_def_rank` 1 = the weakest defense (most EPA allowed) of the 32. When week
  N hasn't been built yet, each team's latest earlier row is used.
- Score: usage increase divided by the std of increases in its position group (RB, or WR / TE;
  not centered, so scores stay positive), times (0.5 + weakness share), where the share runs
  from 1 (rank 1) to 0 (last), times `FOLLOWED_BOOST` for a followed team. Only increases
  above zero qualify.
- Selection: `n_items` players, at most `MAX_PER_TEAM` per team, with the best RB and the
  best WR / TE kept whenever such candidates exist. Ties break on usage increase, then id.
- Baseline (what a "hit" is judged against): the player's mean of the target stat over his
  last `BASELINE_GAMES` games played this season. With fewer than 2 such games it is blended
  with last season's per-game mean, which counts as `BLEND_PSEUDO_GAMES` games.

`injury_week` adds the one piece of week-N information allowed: players listed Out / Doubtful
in that week's injury report, or on the reserve list in that week's roster, are dropped.

The pure cores (`build_watchlist`, `score_results`) take frames and return a frame; the public
functions load them from the curated DuckDB (read-only) for the current and previous season.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence
from contextlib import closing

import duckdb
import polars as pl

from nflengine.paths import DataPaths, ensure_data_root

SKILL_POSITIONS = ("WR", "TE", "RB")
RECENT_GAMES = 2
MIN_BEFORE_GAMES = 2
BASELINE_GAMES = 4
BLEND_PSEUDO_GAMES = 2
MIN_SNAP_SHARE = 0.35
MIN_SPREAD = 0.02  # floor for the std of usage increases (tiny candidate pools)
MAX_PER_TEAM = 2
FOLLOWED_BOOST = 1.15
OUT_STATUSES = ("Out", "Doubtful")
RESERVE_STATUS = "RES"  # rosters_weekly: injured reserve, PUP, NFI and similar lists

GROUP_RB = "RB"
GROUP_RECEIVER = "WR/TE"

OUTPUT_SCHEMA: dict[str, pl.DataType] = {
    "rank": pl.Int32(),
    "player_id": pl.String(),
    "player": pl.String(),
    "team": pl.String(),
    "position": pl.String(),
    "opponent": pl.String(),
    "game_id": pl.String(),
    "home": pl.Boolean(),
    "target": pl.String(),
    "target_col": pl.String(),
    "baseline": pl.Float64(),
    "baseline_games": pl.Int32(),
    "usage_metric": pl.String(),
    "usage_recent": pl.Float64(),
    "usage_before": pl.Float64(),
    "usage_before_source": pl.String(),
    "recent_games": pl.Int32(),
    "snap_share_recent": pl.Float64(),
    "opp_def_rank": pl.Int32(),
    "opp_def_unit": pl.String(),
    "score": pl.Float64(),
    "confidence": pl.String(),
    "source": pl.String(),
    "season": pl.Int32(),
    "week": pl.Int32(),
}
RESULT_SCHEMA: dict[str, pl.DataType] = {
    "actual": pl.Float64(),
    "played": pl.Boolean(),
    "hit": pl.Boolean(),
}

GAME_COLS = ["game_id", "season", "week", "game_type", "home_team", "away_team", "completed"]
PLAYER_GAME_COLS = [
    "player_id",
    "player_display_name",
    "position",
    "team",
    "season",
    "week",
    "game_id",
    "carries",
    "targets",
    "target_share",
    "receiving_yards",
    "rushing_yards",
]
SNAP_COLS = [
    "gsis_id",
    "game_id",
    "season",
    "week",
    "team",
    "position",
    "player",
    "offense_snaps",
    "offense_pct",
]
RATING_COLS = ["season", "week", "team", "def_pass_epa", "def_rush_epa"]


# ---- pure cores --------------------------------------------------------------------------
def unavailable_players(
    injuries: pl.DataFrame | None,
    rosters: pl.DataFrame | None,
    season: int,
    week: int,
) -> set[str]:
    """gsis ids listed Out / Doubtful in the (season, week) injury report or on the reserve
    list (`rosters_weekly.status == 'RES'`) of that week's roster."""
    out: set[str] = set()
    if injuries is not None and injuries.height:
        gone = injuries.filter(
            (pl.col("season") == season)
            & (pl.col("week") == week)
            & pl.col("report_status").is_in(OUT_STATUSES)
        )
        out |= set(gone["gsis_id"].drop_nulls())
    if rosters is not None and rosters.height:
        res = rosters.filter(
            (pl.col("season") == season)
            & (pl.col("week") == week)
            & (pl.col("status") == RESERVE_STATUS)
        )
        out |= set(res["gsis_id"].drop_nulls())
    return out


def _player_games(
    player_games: pl.DataFrame, snaps: pl.DataFrame, game_ids: list[str]
) -> pl.DataFrame:
    """One row per (WR / TE / RB, regular-season game he was part of).

    Box-score rows (`player_games`) are merged with the snap counts: a player can have snaps
    and no stat row (a blocking TE) or the reverse. `played` = offensive snaps > 0 (or a
    carry / target when the snap row is missing). Shares are on a 0-1 scale.
    """
    pg = player_games.filter(pl.col("game_id").is_in(game_ids))
    team_carries = pg.group_by("game_id", "team").agg(
        pl.col("carries").fill_null(0).sum().alias("team_carries")
    )
    sn = (
        snaps.filter(pl.col("gsis_id").is_not_null() & pl.col("game_id").is_in(game_ids))
        .group_by("gsis_id", "game_id", maintain_order=True)
        .agg(
            pl.col("season").first(),
            pl.col("week").first(),
            pl.col("team").first(),
            pl.col("position").first(),
            pl.col("player").first().alias("snap_name"),
            pl.col("offense_snaps").max(),
            pl.col("offense_pct").max(),
        )
        .rename({"gsis_id": "player_id"})
    )
    both = pg.join(sn, on=["player_id", "game_id"], how="full", coalesce=True, suffix="_snap")
    both = both.with_columns(
        pl.coalesce("season", "season_snap").alias("season"),
        pl.coalesce("week", "week_snap").alias("week"),
        pl.coalesce("team", "team_snap").alias("team"),
        pl.coalesce("position", "position_snap").alias("position"),
        pl.coalesce("player_display_name", "snap_name").alias("player"),
        pl.col("carries", "targets", "target_share", "receiving_yards", "rushing_yards").fill_null(
            0
        ),
    )
    both = both.join(team_carries, on=["game_id", "team"], how="left")
    return both.filter(pl.col("position").is_in(SKILL_POSITIONS)).select(
        "player_id",
        "player",
        "position",
        "team",
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        "game_id",
        pl.when(pl.col("offense_snaps").is_not_null())
        .then(pl.col("offense_snaps") > 0)
        .otherwise((pl.col("carries") + pl.col("targets")) > 0)
        .alias("played"),
        pl.col("offense_pct"),
        pl.col("target_share").cast(pl.Float64),
        pl.when(pl.col("team_carries") > 0)
        .then(pl.col("carries") / pl.col("team_carries"))
        .otherwise(0.0)
        .alias("carry_share"),
        pl.col("receiving_yards").cast(pl.Float64),
        (pl.col("rushing_yards") + pl.col("receiving_yards")).cast(pl.Float64).alias("scrimmage"),
    )


def _team_games(games: pl.DataFrame) -> pl.DataFrame:
    """One row per (team, regular-season game): opponent, home flag, completed."""
    sides = [
        games.select(
            "game_id",
            "season",
            "week",
            pl.col(f"{side}_team").alias("team"),
            pl.col(f"{other}_team").alias("opponent"),
            pl.lit(side == "home").alias("home"),
            pl.col("completed").fill_null(False),
        )
        for side, other in (("home", "away"), ("away", "home"))
    ]
    return pl.concat(sides).with_columns(pl.col("season", "week").cast(pl.Int32))


def _defense_ranks(ratings: pl.DataFrame, season: int, week: int) -> pl.DataFrame:
    """Per team: pass / run defense rank and weakness share, from its latest as-of row at or
    before `week`. Rank 1 = the most EPA allowed (weakest); share = 1 for rank 1, 0 for last."""
    latest = (
        ratings.filter((pl.col("season") == season) & (pl.col("week") <= week))
        .sort("week")
        .unique("team", keep="last")
        .sort("team")
    )
    out = latest.select("team")
    for unit, col in (("pass", "def_pass_epa"), ("rush", "def_rush_epa")):
        rank = latest[col].rank("ordinal", descending=True)
        n = latest[col].count()
        share = (n - rank) / (n - 1) if n > 1 else pl.Series([0.5] * latest.height)
        out = out.with_columns(
            rank.cast(pl.Int32).alias(f"{unit}_rank"), share.cast(pl.Float64).alias(f"{unit}_share")
        )
    return out


def _windows(pgames: pl.DataFrame, season: int, week: int) -> pl.DataFrame:
    """Per player with a game this season before `week`: latest game, recent / before usage
    and the baseline inputs. Only games he played, only weeks before `week`."""
    played = pgames.filter(pl.col("played"))
    order = ["player_id", "week", "game_id"]
    cur_all = played.filter((pl.col("season") == season) & (pl.col("week") < week)).sort(
        order, descending=[False, True, True]
    )
    latest = (
        cur_all.group_by("player_id", maintain_order=True)
        .first()
        .select(
            "player_id",
            "player",
            "position",
            "team",
            pl.col("game_id").alias("last_game_id"),
        )
    )
    # usage windows only count games with his current team: a traded player's earlier
    # share belongs to another offense (P04 fact-check: Adonai Mitchell's 8% was IND usage)
    now_team = latest.select("player_id", pl.col("team").alias("_team_now"))
    cur = (
        cur_all.join(now_team, on="player_id")
        .filter(pl.col("team") == pl.col("_team_now"))
        .sort(order, descending=[False, True, True])
        .with_columns(pl.int_range(0, pl.len()).over("player_id").alias("_i"))
    )

    def use(suffix: str) -> list[pl.Expr]:
        return [
            pl.col("target_share").mean().alias(f"tgt_{suffix}"),
            pl.col("carry_share").mean().alias(f"car_{suffix}"),
        ]

    recent = (
        cur.filter(pl.col("_i") < RECENT_GAMES)
        .group_by("player_id")
        .agg(
            pl.len().cast(pl.Int32).alias("recent_games"),
            pl.col("offense_pct").mean().alias("snap_share_recent"),
            *use("recent"),
        )
    )
    earlier = (
        cur.filter(pl.col("_i") >= RECENT_GAMES)
        .group_by("player_id")
        .agg(
            pl.len().cast(pl.Int32).alias("n_earlier"),
            *use("earlier"),
        )
    )
    window = (
        cur.filter(pl.col("_i") < BASELINE_GAMES)
        .group_by("player_id")
        .agg(
            pl.len().cast(pl.Int32).alias("baseline_games"),
            pl.col("receiving_yards").mean().alias("receiving_cur"),
            pl.col("scrimmage").mean().alias("scrimmage_cur"),
        )
    )
    last = (
        played.filter(pl.col("season") == season - 1)
        .join(now_team, on="player_id")
        .filter(pl.col("team") == pl.col("_team_now"))
        .group_by("player_id")
        .agg(
            pl.len().cast(pl.Int32).alias("n_last"),
            *use("last"),
            pl.col("receiving_yards").mean().alias("receiving_last"),
            pl.col("scrimmage").mean().alias("scrimmage_last"),
        )
    )
    out = latest
    for part in (recent, earlier, window, last):
        out = out.join(part, on="player_id", how="left")
    return out.with_columns(pl.col("n_earlier", "n_last").fill_null(0))


def _lit(value: pl.Expr | str) -> pl.Expr:
    return pl.lit(value) if isinstance(value, str) else value


def _choose(flag: str, yes: pl.Expr | str, no: pl.Expr | str) -> pl.Expr:
    """`yes` where the boolean column `flag` is true, else `no` (strings are literals)."""
    return pl.when(pl.col(flag)).then(_lit(yes)).otherwise(_lit(no))


def _candidates(
    games: pl.DataFrame,
    player_games: pl.DataFrame,
    snaps: pl.DataFrame,
    ratings: pl.DataFrame,
    season: int,
    week: int,
    *,
    followed: Collection[str] = (),
    unavailable: Collection[str] = (),
    min_increase: float = 0.0,
) -> pl.DataFrame:
    """Every qualifying candidate (usage increase above `min_increase`) with its score,
    before the top-N selection."""
    reg = games.filter(pl.col("game_type") == "REG")
    pgames = _player_games(player_games, snaps, reg["game_id"].to_list())
    tg = _team_games(reg)
    slate = tg.filter((pl.col("season") == season) & (pl.col("week") == week)).select(
        "team", "opponent", "home", "game_id"
    )
    team_last = (
        tg.filter((pl.col("season") == season) & (pl.col("week") < week) & pl.col("completed"))
        .sort("week")
        .unique("team", keep="last")
        .select("team", pl.col("game_id").alias("last_game_id"))
    )
    ranks = _defense_ranks(ratings, season, week).rename({"team": "opponent"})

    c = (
        _windows(pgames, season, week)
        .join(team_last, on=["team", "last_game_id"], how="inner")  # played his team's last game
        .join(slate, on="team", how="inner")  # and the team plays in week N
        .join(ranks, on="opponent", how="inner")
        .filter(~pl.col("player_id").is_in(list(unavailable)))
        .with_columns(
            pl.when(pl.col("n_earlier") >= MIN_BEFORE_GAMES)
            .then(pl.lit("season"))
            .when(pl.col("n_last") >= MIN_BEFORE_GAMES)
            .then(pl.lit("last_season"))
            .alias("usage_before_source"),
            (pl.col("position") == "RB").alias("_rb"),
        )
        .filter(
            (pl.col("snap_share_recent") >= MIN_SNAP_SHARE)
            & pl.col("usage_before_source").is_not_null()
        )
    )
    season_before = pl.col("usage_before_source") == "season"
    c = c.with_columns(
        pl.when(season_before).then("tgt_earlier").otherwise("tgt_last").alias("tgt_b"),
        pl.when(season_before).then("car_earlier").otherwise("car_last").alias("car_b"),
    )
    carry_gain = pl.col("car_recent") - pl.col("car_b")
    target_gain = pl.col("tgt_recent") - pl.col("tgt_b")
    c = c.with_columns((pl.col("_rb") & (carry_gain >= target_gain)).alias("_carry"))
    c = c.with_columns(
        _choose("_carry", "carry share", "target share").alias("usage_metric"),
        _choose("_carry", pl.col("car_recent"), pl.col("tgt_recent")).alias("usage_recent"),
        _choose("_carry", pl.col("car_b"), pl.col("tgt_b")).alias("usage_before"),
        _choose("_rb", GROUP_RB, GROUP_RECEIVER).alias("_group"),
        _choose("_rb", "scrimmage yards", "receiving yards").alias("target"),
        _choose("_rb", "scrimmage_yards", "receiving_yards").alias("target_col"),
        _choose("_rb", "run defense", "pass defense").alias("opp_def_unit"),
        _choose("_rb", pl.col("rush_rank"), pl.col("pass_rank")).alias("opp_def_rank"),
        _choose("_rb", pl.col("rush_share"), pl.col("pass_share")).alias("_weakness"),
        _choose("_rb", pl.col("scrimmage_cur"), pl.col("receiving_cur")).alias("_cur_mean"),
        _choose("_rb", pl.col("scrimmage_last"), pl.col("receiving_last")).alias("_last_mean"),
    )
    n = pl.col("baseline_games")
    c = c.with_columns(
        (pl.col("usage_recent") - pl.col("usage_before")).alias("usage_increase"),
        pl.when((n >= MIN_BEFORE_GAMES) | pl.col("_last_mean").is_null())
        .then(pl.col("_cur_mean"))
        .otherwise(
            (n * pl.col("_cur_mean") + BLEND_PSEUDO_GAMES * pl.col("_last_mean"))
            / (n + BLEND_PSEUDO_GAMES)
        )
        .alias("baseline"),
    )
    spread = pl.col("usage_increase").std().over("_group").fill_null(MIN_SPREAD).clip(MIN_SPREAD)
    boost = pl.when(pl.col("team").is_in(list(followed))).then(FOLLOWED_BOOST).otherwise(1.0)
    return (
        c.with_columns(
            (pl.col("usage_increase") / spread * (0.5 + pl.col("_weakness")) * boost).alias("score")
        )
        .filter((pl.col("usage_increase") > min_increase) & pl.col("score").is_not_null())
        .with_columns(
            pl.lit("low").alias("confidence"),
            pl.lit("heuristic").alias("source"),
            pl.lit(season).cast(pl.Int32).alias("season"),
            pl.lit(week).cast(pl.Int32).alias("week"),
        )
    )


def _select(cands: pl.DataFrame, n_items: int) -> pl.DataFrame:
    """Top `n_items` by score: at most MAX_PER_TEAM per team, the best RB and the best
    WR / TE kept first (when n_items allows)."""
    rows = cands.sort(
        ["score", "usage_increase", "player_id"], descending=[True, True, False]
    ).to_dicts()
    chosen: list[dict] = []
    per_team: Counter[str] = Counter()
    for group in (GROUP_RB, GROUP_RECEIVER):
        best = next((r for r in rows if r["_group"] == group), None)
        if best is not None:
            chosen.append(best)
    chosen.sort(key=lambda r: (-r["score"], r["player_id"]))
    chosen = chosen[:n_items]
    taken = {r["player_id"] for r in chosen}
    per_team.update(r["team"] for r in chosen)
    for r in rows:
        if len(chosen) >= n_items:
            break
        if r["player_id"] in taken or per_team[r["team"]] >= MAX_PER_TEAM:
            continue
        chosen.append(r)
        taken.add(r["player_id"])
        per_team[r["team"]] += 1
    if not chosen:
        return cands.clear()
    return cands.filter(pl.col("player_id").is_in([r["player_id"] for r in chosen])).sort(
        ["score", "usage_increase", "player_id"], descending=[True, True, False]
    )


def build_watchlist(
    games: pl.DataFrame,
    player_games: pl.DataFrame,
    snaps: pl.DataFrame,
    ratings: pl.DataFrame,
    season: int,
    week: int,
    *,
    n_items: int = 5,
    followed: Sequence[str] = (),
    injuries: pl.DataFrame | None = None,
    rosters: pl.DataFrame | None = None,
    injury_week: int | None = None,
) -> pl.DataFrame:
    """The watch list for `week` of `season` from in-memory frames (see the module doc).

    games: `GAME_COLS`; player_games: `PLAYER_GAME_COLS`; snaps: `SNAP_COLS`; ratings:
    `RATING_COLS` (rows of several weeks; the latest as-of row at or before `week` is used).
    injuries (`gsis_id`, `season`, `week`, `report_status`) and rosters (`gsis_id`, `season`,
    `week`, `status`) only matter when `injury_week` is set. Returns `OUTPUT_SCHEMA`
    (empty when nothing qualifies, e.g. week 1: there are no earlier games this season).
    """
    unavailable = (
        unavailable_players(injuries, rosters, season, injury_week)
        if injury_week is not None
        else set()
    )
    cands = _candidates(
        games,
        player_games,
        snaps,
        ratings,
        season,
        week,
        followed=followed,
        unavailable=unavailable,
    )
    picked = _select(cands, n_items)
    if picked.is_empty():
        return pl.DataFrame(schema=OUTPUT_SCHEMA)
    return picked.with_columns(
        pl.int_range(1, pl.len() + 1).cast(pl.Int32).alias("rank"),
        pl.col("baseline_games", "recent_games", "opp_def_rank").cast(pl.Int32),
    ).select([pl.col(c).cast(t) for c, t in OUTPUT_SCHEMA.items()])


def score_results(
    watch: pl.DataFrame, player_games: pl.DataFrame, snaps: pl.DataFrame
) -> pl.DataFrame:
    """Add `actual`, `played` and `hit` to a watch list from the box score of its games.

    `played` = offensive snaps > 0 in the game (a box-score row stands in when the snap row is
    missing; a game not yet played counts as not played). `actual` = the target stat (null when
    he didn't play); `hit` = actual > baseline (null when he didn't play).
    """
    box = player_games.select(
        "player_id",
        "game_id",
        pl.col("receiving_yards").fill_null(0).cast(pl.Float64).alias("_rec"),
        (pl.col("rushing_yards").fill_null(0) + pl.col("receiving_yards").fill_null(0))
        .cast(pl.Float64)
        .alias("_scr"),
        pl.lit(True).alias("_box"),
    ).unique(["player_id", "game_id"])
    snap = (
        snaps.filter(pl.col("gsis_id").is_not_null())
        .group_by("gsis_id", "game_id")
        .agg(pl.col("offense_snaps").max().alias("_snaps"))
        .rename({"gsis_id": "player_id"})
    )
    j = (
        watch.join(box, on=["player_id", "game_id"], how="left")
        .join(snap, on=["player_id", "game_id"], how="left")
        .with_columns(
            pl.coalesce(pl.col("_snaps") > 0, pl.col("_box")).fill_null(False).alias("played")
        )
    )
    stat = (  # a player with snaps but no box-score row had no yards
        pl.when(pl.col("target_col") == "scrimmage_yards")
        .then(pl.col("_scr"))
        .otherwise(pl.col("_rec"))
        .fill_null(0.0)
    )
    j = j.with_columns(
        pl.when(pl.col("played")).then(stat).otherwise(None).cast(pl.Float64).alias("actual")
    ).with_columns(
        pl.when(pl.col("played"))
        .then(pl.col("actual") > pl.col("baseline"))
        .otherwise(None)
        .alias("hit")
    )
    return j.select(*watch.columns, *RESULT_SCHEMA)


# ---- loading ---------------------------------------------------------------------------------
def _read(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> pl.DataFrame:
    return con.execute(sql, params or []).pl()


def placeholder_watchlist(
    season: int,
    week: int,
    paths: DataPaths | None = None,
    *,
    n_items: int = 5,
    followed: Sequence[str] = (),
    injury_week: int | None = None,
) -> pl.DataFrame:
    """The placeholder watch list for the upcoming `week` (module doc), from the curated data.

    `followed` team codes get a x1.15 score boost (never enough to beat a much stronger
    signal). With `injury_week` set, players Out / Doubtful or on the reserve list in that
    week's injury report / roster are dropped. Columns: `OUTPUT_SCHEMA`.
    """
    paths = paths or ensure_data_root()
    before = "(season = ? OR (season = ? AND week < ?))"
    prior = [season - 1, season, week]
    with closing(duckdb.connect(str(paths.curated / "nfl.duckdb"), read_only=True)) as con:
        games = _read(
            con,
            f"SELECT {', '.join(GAME_COLS)} FROM games WHERE game_type = 'REG' "
            "AND (season = ? OR (season = ? AND week <= ?))",
            prior,
        )
        player_games = _read(
            con,
            f"SELECT {', '.join(PLAYER_GAME_COLS)} FROM player_games WHERE {before}",
            prior,
        )
        snaps = _read(
            con,
            f"SELECT {', '.join(SNAP_COLS)} FROM snaps WHERE game_type = 'REG' AND {before}",
            prior,
        )
        ratings = _read(
            con,
            f"SELECT {', '.join(RATING_COLS)} FROM team_ratings WHERE season = ? AND week <= ?",
            [season, week],
        )
        injuries = rosters = None
        if injury_week is not None:
            where = "game_type = 'REG' AND season = ? AND week = ?"
            injuries = _read(
                con,
                f"SELECT gsis_id, season, week, report_status FROM injuries WHERE {where}",
                [season, injury_week],
            )
            rosters = _read(
                con,
                f"SELECT gsis_id, season, week, status FROM rosters_weekly WHERE {where}",
                [season, injury_week],
            )
    return build_watchlist(
        games,
        player_games,
        snaps,
        ratings,
        season,
        week,
        n_items=n_items,
        followed=followed,
        injuries=injuries,
        rosters=rosters,
        injury_week=injury_week,
    )


def score_watchlist(watch: pl.DataFrame, paths: DataPaths | None = None) -> pl.DataFrame:
    """Add actual results for a saved watch list once its week is played.

    Returns `watch` plus `actual` (the target stat in the game; null if he didn't play),
    `played` (bool) and `hit` (actual > baseline; null when he didn't play). A game that
    hasn't been played yet counts as not played, so score a week only after its games.
    """
    if watch.is_empty():
        return watch.with_columns(
            *[pl.lit(None, dtype=t).alias(c) for c, t in RESULT_SCHEMA.items()]
        )
    paths = paths or ensure_data_root()
    game_ids = watch["game_id"].unique().to_list()
    with closing(duckdb.connect(str(paths.curated / "nfl.duckdb"), read_only=True)) as con:
        player_games = _read(
            con,
            "SELECT player_id, game_id, receiving_yards, rushing_yards FROM player_games "
            "WHERE game_id IN (SELECT unnest(?))",
            [game_ids],
        )
        snaps = _read(
            con,
            "SELECT gsis_id, game_id, offense_snaps FROM snaps WHERE game_id IN (SELECT unnest(?))",
            [game_ids],
        )
    return score_results(watch, player_games, snaps)
