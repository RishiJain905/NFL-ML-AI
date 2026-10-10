"""Training frames for the live-decision models (LD00), built from curated play-by-play.

One loader (`load_plays`) reads the columns every model needs, regular season and playoffs,
2010+; `with_state` adds the offense-side state columns with the same formulas as
`schema.state_features` (a test checks they agree). Each model then takes its own rows:

- `wp_frame`: every snap with a down (ties dropped, counted); label `win`.
- `gain_frame`: 3rd / 4th downs: runs, passes (sacks, scrambles), defensive-penalty first
  downs; labels `gain_class`, `converted`.
- `fg_frame`: field-goal tries; label `make`.
- `punt_frame`: punts (fakes are typed run / pass) and where the next snap is.
- `kickoff_frame`: kickoffs after a score (onside kicks flagged) and where the next snap is.
- `pass_frame`: runs and passes, downs 1-4; label `is_pass`.
- `fourth_frame`: 4th downs that were run, passed, kicked or punted; the coach's `choice`.

Reads only curated data and, optionally, the published `features/team_ratings.parquet`
(never written; D107).
"""

from __future__ import annotations

from collections.abc import Iterable

import polars as pl

from nflengine.live import schema
from nflengine.paths import DataPaths, ensure_data_root

PLAY_COLUMNS = [
    "game_id", "play_id", "order_sequence", "season", "season_type", "week", "qtr", "down",
    "ydstogo", "yardline_100", "posteam", "defteam", "home_team", "away_team", "play_type",
    "yards_gained", "touchdown", "td_team", "first_down_penalty", "penalty_team", "penalty_yards",
    "game_seconds_remaining", "half_seconds_remaining", "score_differential",
    "posteam_timeouts_remaining", "defteam_timeouts_remaining", "spread_line", "total_line",
    "roof", "temp", "wind", "result", "pass", "two_point_attempt", "field_goal_result",
    "kick_distance", "kicker_player_id", "extra_point_attempt", "extra_point_result",
    "two_point_conv_result", "safety", "desc", "wp", "vegas_wp", "xpass", "home_opening_kickoff",
    "punt_blocked", "first_down", "fumble_lost", "interception",
]  # fmt: skip

SNAP_TYPES = ("run", "pass", "punt", "field_goal", "qb_kneel", "qb_spike", "no_play")


def load_plays(
    seasons: Iterable[int] | None = None, paths: DataPaths | None = None
) -> pl.DataFrame:
    """Regular-season and playoff plays (2010+ unless `seasons`), in game order, with `i` (the
    play's index in its game) and the game's `neutral_site` from `games`."""
    paths = paths or ensure_data_root(create_dirs=False)
    cur = paths.curated
    lf = pl.scan_parquet((cur / "plays" / "*.parquet").as_posix()).select(PLAY_COLUMNS)
    lf = lf.filter(pl.col("season_type").is_in(["REG", "POST"]))
    if seasons is not None:
        lf = lf.filter(pl.col("season").is_in(sorted(set(seasons))))
    plays = lf.collect().sort("game_id", "order_sequence", "play_id")
    games = pl.read_parquet(cur / "games.parquet", columns=["game_id", "neutral_site"])
    plays = plays.join(games, on="game_id", how="left").with_columns(
        pl.col("neutral_site").fill_null(False)
    )
    return plays.with_columns(pl.int_range(pl.len()).over("game_id").alias("i"))


def era_expr(season_col: str) -> pl.Expr:
    """`schema.era_of` as a Polars expression."""
    expr = pl.lit(0)
    for i, start in enumerate(schema.ERA_STARTS):
        expr = pl.when(pl.col(season_col) >= start).then(i).otherwise(expr)
    return expr.cast(pl.Int8)


def _opening_receivers(plays: pl.DataFrame) -> pl.DataFrame:
    """Who received the opening kickoff: the first first-quarter kickoff's `posteam` (on a
    kickoff nflverse's posteam is the receiver), else `home_opening_kickoff` (1 = home
    received). The other team receives the second-half kickoff (2,227 of 2,227 games
    2018-2025, LD00 probe)."""
    first_ko = (
        plays.filter((pl.col("play_type") == "kickoff") & (pl.col("qtr") == 1))
        .group_by("game_id")
        .agg(pl.col("posteam").drop_nulls().first().alias("ko_recv"))
    )
    hok = plays.group_by("game_id").agg(
        pl.col("home_opening_kickoff").drop_nulls().first(),
        pl.col("home_team").first(),
        pl.col("away_team").first(),
    )
    out = hok.join(first_ko, on="game_id", how="left").with_columns(
        pl.coalesce(
            "ko_recv",
            pl.when(pl.col("home_opening_kickoff") == 1)
            .then(pl.col("home_team"))
            .when(pl.col("home_opening_kickoff") == 0)
            .then(pl.col("away_team")),
        ).alias("opening_receiver")
    )
    return out.select("game_id", "opening_receiver")


def with_state(plays: pl.DataFrame) -> pl.DataFrame:
    """Add the offense-side state columns (names as in `schema.state_features`)."""
    plays = plays.join(_opening_receivers(plays), on="game_id", how="left")
    is_home = pl.col("posteam") == pl.col("home_team")
    ot = pl.col("qtr") >= 5
    share = pl.when(ot).then(1.0).otherwise((3600.0 - pl.col("game_seconds_remaining")) / 3600.0)
    decay = (-4.0 * share).exp()
    spread = pl.when(is_home).then(pl.col("spread_line")).otherwise(-pl.col("spread_line"))
    roof_in = pl.col("roof").is_in(list(schema.INDOOR_ROOFS)).fill_null(False)
    out = plays.with_columns(
        pl.col("score_differential").alias("score_diff"),
        pl.col("game_seconds_remaining").alias("game_seconds"),
        pl.col("half_seconds_remaining").alias("half_seconds"),
        pl.col("posteam_timeouts_remaining").alias("off_timeouts"),
        pl.col("defteam_timeouts_remaining").alias("def_timeouts"),
        pl.when(pl.col("neutral_site"))
        .then(0)
        .when(is_home)
        .then(1)
        .otherwise(-1)
        .cast(pl.Int8)
        .alias("home"),
        ((pl.col("qtr") <= 2) & (pl.col("posteam") != pl.col("opening_receiver")))
        .fill_null(False)
        .cast(pl.Int8)
        .alias("receive_2h_ko"),
        spread.alias("spread"),
        pl.col("total_line").alias("total"),
        ot.cast(pl.Int8).alias("ot"),
        era_expr("season").alias("era"),
        roof_in.cast(pl.Int8).alias("indoor"),
        (pl.col("season_type") == "POST").alias("playoffs"),
    )
    return out.with_columns(
        (pl.col("score_diff") / decay).alias("diff_time_ratio"),
        (pl.col("spread") * decay).alias("spread_time"),
        (pl.col("total") / 2.0 + pl.col("spread") / 2.0).alias("team_total"),
    )


def _win_label(df: pl.DataFrame) -> pl.DataFrame:
    is_home = pl.col("posteam") == pl.col("home_team")
    won = pl.when(is_home).then(pl.col("result") > 0).otherwise(pl.col("result") < 0)
    return df.with_columns(won.cast(pl.Int8).alias("win"))


def wp_frame(plays: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, int]]:
    """Every snap with a down and a possession team; label `win` = the offense won the game.
    Ties are dropped (and counted)."""
    st = plays.filter(
        pl.col("down").is_not_null()
        & pl.col("posteam").is_not_null()
        & pl.col("play_type").is_in(list(SNAP_TYPES))
        & pl.col("yardline_100").is_between(1, 99)
        & pl.col("result").is_not_null()
        & pl.col("spread_line").is_not_null()
    )
    ties = st.filter(pl.col("result") == 0)
    counts = {"rows": st.height, "tie_rows": ties.height, "tie_games": ties["game_id"].n_unique()}
    return _win_label(st.filter(pl.col("result") != 0)), counts


def _offense_td() -> pl.Expr:
    return ((pl.col("touchdown") == 1) & (pl.col("td_team") == pl.col("posteam"))).fill_null(False)


def gain_frame(plays: pl.DataFrame) -> pl.DataFrame:
    """3rd and 4th downs: runs and passes (sacks and scrambles included) and first downs by a
    defensive penalty. `gain` = yards gained (a penalty first down: the penalty yards, at
    least the distance, short of the goal line); `gain_class` per `schema.gain_class`;
    `converted` = gain >= distance or an offensive touchdown (agrees with nflverse's
    `first_down` on 99.5% of 2018-2025 runs and passes)."""
    rp = (pl.col("play_type").is_in(["run", "pass"])) & pl.col("yards_gained").is_not_null()
    pen = (
        (pl.col("play_type") == "no_play")
        & (pl.col("first_down_penalty") == 1)
        & (pl.col("penalty_team") == pl.col("defteam"))
    )
    # A goal-to-go defensive-penalty first down (4th & goal at the 5 -> 1st & goal at the 2) is a
    # first down short of the line to gain, which no gain class can express (a goal-to-go
    # conversion is a touchdown): left out, 276 rows of 2010-2025 (0.2%; LD00 tests-builder).
    goal_to_go_pen = pen & (pl.col("ydstogo") >= pl.col("yardline_100"))
    df = plays.filter(
        pl.col("down").is_in([3, 4])
        & (rp | (pen & ~goal_to_go_pen)).fill_null(False)
        & (pl.col("two_point_attempt").fill_null(0) == 0)
        & pl.col("ydstogo").is_between(1, 99)
        & pl.col("yardline_100").is_between(1, 99)
        & pl.col("spread_line").is_not_null()
    )
    td = _offense_td()
    pen_gain = pl.max_horizontal(pl.col("ydstogo"), pl.col("penalty_yards").fill_null(0))
    gain = (
        pl.when(pl.col("play_type") == "no_play")
        .then(pl.min_horizontal(pen_gain, pl.col("yardline_100") - 1))
        .otherwise(pl.col("yards_gained"))
    )
    # a non-touchdown gain can't reach the goal line (one 2018-25 row did: a fumble)
    gain = (
        pl.when(td)
        .then(pl.col("yardline_100"))
        .otherwise(pl.min_horizontal(gain, pl.col("yardline_100") - 1))
    )
    df = df.with_columns(gain.alias("gain"), td.alias("offense_td"))
    cls = (
        pl.when(pl.col("offense_td"))
        .then(schema.TD_CLASS)
        .otherwise(pl.col("gain").round().clip(schema.GAIN_MIN, schema.GAIN_MAX) - schema.GAIN_MIN)
        .cast(pl.Int32)
    )
    return df.with_columns(
        cls.alias("gain_class"),
        ((pl.col("gain") >= pl.col("ydstogo")) | pl.col("offense_td"))
        .cast(pl.Int8)
        .alias("converted"),
        (pl.col("play_type") == "no_play").alias("penalty_first_down"),
    )


def fg_frame(plays: pl.DataFrame) -> pl.DataFrame:
    """Field-goal tries with a result: `distance` (nflverse's `kick_distance`, else yards to
    the goal + 18), `make`, outdoor weather (`wind`, `temp`; null indoors or unknown)."""
    df = plays.filter(
        (pl.col("play_type") == "field_goal") & pl.col("field_goal_result").is_not_null()
    )
    return df.with_columns(
        pl.coalesce("kick_distance", pl.col("yardline_100") + schema.FG_SNAP_YARDS)
        .cast(pl.Float64)
        .alias("distance"),
        (pl.col("field_goal_result") == "made").cast(pl.Int8).alias("make"),
        (pl.col("field_goal_result") == "blocked").cast(pl.Int8).alias("blocked"),
        pl.when(pl.col("indoor") == 1).then(None).otherwise(pl.col("wind")).alias("wind_out"),
        pl.when(pl.col("indoor") == 1).then(None).otherwise(pl.col("temp")).alias("temp_out"),
    )


def _next_snap(df: pl.DataFrame, plays: pl.DataFrame) -> pl.DataFrame:
    """The next snap (a play with a down) after each row, in the same game: its possession
    team, yards to goal and quarter."""
    snaps = plays.filter(
        pl.col("down").is_not_null() & pl.col("play_type").is_in(list(SNAP_TYPES))
    ).select(
        "game_id",
        pl.col("i").alias("next_i"),
        pl.col("posteam").alias("next_pos"),
        pl.col("yardline_100").alias("next_yl"),
        pl.col("qtr").alias("next_qtr"),
    )
    return df.sort("game_id", "i").join_asof(
        snaps.sort("game_id", "next_i"),
        left_on="i",
        right_on="next_i",
        by="game_id",
        strategy="forward",
        allow_exact_matches=False,
        check_sortedness=False,
    )


def _same_half(q: str, nq: str) -> pl.Expr:
    a, b = pl.col(q), pl.col(nq)
    return (a == b) | ((a == 1) & (b == 2)) | ((a == 3) & (b == 4))


def _outcome(kicker_col: str, receiver_col: str, ret_td: pl.Expr) -> pl.Expr:
    """recv (the receiving team snaps next), ret_td, keep (the kicking team keeps it), or
    null (the half ended / no next snap)."""
    return (
        pl.when(ret_td)
        .then(pl.lit("ret_td"))
        .when(pl.col("next_pos").is_null() | ~_same_half("qtr", "next_qtr"))
        .then(None)
        .when(pl.col("next_pos") == pl.col(receiver_col))
        .then(pl.lit("recv"))
        .when(pl.col("next_pos") == pl.col(kicker_col))
        .then(pl.lit("keep"))
    )


def punt_frame(plays: pl.DataFrame) -> pl.DataFrame:
    """Punts and where the next possession starts: `outcome` recv / ret_td / keep, `spot` =
    the next snap's yards to goal for the team that has the ball (recv: the receiver;
    keep: the punting team). A touchdown by the kicking team, a safety, or a punt that ends
    the half are dropped."""
    df = plays.filter(pl.col("play_type") == "punt")
    df = _next_snap(df, plays)
    td_recv = ((pl.col("touchdown") == 1) & (pl.col("td_team") == pl.col("defteam"))).fill_null(
        False
    )
    td_kick = ((pl.col("touchdown") == 1) & (pl.col("td_team") == pl.col("posteam"))).fill_null(
        False
    )
    df = df.with_columns(_outcome("posteam", "defteam", td_recv).alias("outcome"))
    df = df.filter(
        ~td_kick & (pl.col("safety").fill_null(0) == 0) & pl.col("outcome").is_not_null()
    )
    return df.with_columns(
        pl.when(pl.col("outcome") == "ret_td").then(None).otherwise(pl.col("next_yl")).alias("spot")
    )


def kickoff_frame(plays: pl.DataFrame) -> pl.DataFrame:
    """Kickoffs right after a score (an extra point, a two-point try or a made field goal) with
    the next possession's start (`outcome`, `spot` as in `punt_frame`; on a kickoff `posteam`
    is the receiving team), by `kickoff_era`. Onside kicks are kept and flagged (`onside`);
    `fit_kickoff` leaves them out."""
    prev = plays.select(
        "game_id",
        (pl.col("i") + 1).alias("i"),
        pl.col("play_type").alias("prev_type"),
        pl.col("field_goal_result").alias("prev_fg"),
        pl.col("extra_point_attempt").alias("prev_xp"),
        pl.col("two_point_attempt").alias("prev_2pt"),
    )
    df = plays.filter(pl.col("play_type") == "kickoff").join(prev, on=["game_id", "i"], how="left")
    after = (
        (pl.col("prev_type") == "extra_point")
        | (pl.col("prev_xp") == 1)
        | (pl.col("prev_2pt") == 1)
        | (pl.col("prev_fg") == "made")
    ).fill_null(False)
    onside = pl.col("desc").str.to_lowercase().str.contains("onside").fill_null(False)
    df = df.with_columns(after.alias("after_score"), onside.alias("onside"))
    df = _next_snap(df.filter(pl.col("after_score")), plays)
    # posteam = receiver on a kickoff
    ret_td = ((pl.col("touchdown") == 1) & (pl.col("td_team") == pl.col("posteam"))).fill_null(
        False
    )
    kick_td = ((pl.col("touchdown") == 1) & (pl.col("td_team") == pl.col("defteam"))).fill_null(
        False
    )
    df = df.with_columns(
        _outcome("defteam", "posteam", ret_td).alias("outcome"),
        pl.col("season").map_elements(schema.kickoff_era, return_dtype=pl.String).alias("ko_era"),
    )
    df = df.filter(~kick_td & pl.col("outcome").is_not_null())
    return df.with_columns(
        pl.when(pl.col("outcome") == "ret_td").then(None).otherwise(pl.col("next_yl")).alias("spot")
    )


def pass_frame(plays: pl.DataFrame) -> pl.DataFrame:
    """Runs and passes on downs 1-4 (no two-point tries); `is_pass` = a dropback (nflverse
    `pass`: scrambles and sacks count as passes)."""
    df = plays.filter(
        pl.col("play_type").is_in(["run", "pass"])
        & pl.col("down").is_not_null()
        & (pl.col("two_point_attempt").fill_null(0) == 0)
        & pl.col("spread_line").is_not_null()
        & pl.col("yardline_100").is_between(1, 99)
    )
    return df.with_columns(pl.col("pass").fill_null(0).cast(pl.Int8).alias("is_pass"))


def fourth_frame(plays: pl.DataFrame) -> pl.DataFrame:
    """4th downs the coach decided: `choice` go (a run or pass, fakes included), fg or punt.
    Pre-snap penalties (no_play) and kneels are left out."""
    df = plays.filter(
        (pl.col("down") == 4)
        & pl.col("play_type").is_in(["run", "pass", "field_goal", "punt"])
        & pl.col("ydstogo").is_between(1, 99)
        & pl.col("yardline_100").is_between(1, 99)
        & pl.col("spread_line").is_not_null()
        & pl.col("total_line").is_not_null()
    )
    choice = (
        pl.when(pl.col("play_type") == "field_goal")
        .then(pl.lit("fg"))
        .when(pl.col("play_type") == "punt")
        .then(pl.lit("punt"))
        .otherwise(pl.lit("go"))
    )
    return df.with_columns(choice.alias("choice"))


def with_ratings(df: pl.DataFrame, paths: DataPaths | None = None) -> pl.DataFrame:
    """Join the published opponent-adjusted EPA ratings as of each row's week (read only):
    `off_epa` (the offense's) and `def_epa_opp` (the defense's EPA allowed; higher = worse)."""
    paths = paths or ensure_data_root(create_dirs=False)
    tr = pl.read_parquet(
        paths.features / "team_ratings.parquet",
        columns=["season", "week", "team", "off_epa", "def_epa"],
    )
    off = tr.select("season", "week", pl.col("team").alias("posteam"), "off_epa")
    de = tr.select(
        "season", "week", pl.col("team").alias("defteam"), pl.col("def_epa").alias("def_epa_opp")
    )
    return df.join(off, on=["season", "week", "posteam"], how="left").join(
        de, on=["season", "week", "defteam"], how="left"
    )


def state_of_row(row: dict) -> schema.GameState:
    """A `GameState` from one `with_state` row (backtests, the worked example)."""
    return schema.GameState(
        season=int(row["season"]),
        score_diff=int(row["score_diff"]),
        game_seconds=float(row["game_seconds"]),
        half_seconds=float(row["half_seconds"]),
        down=int(row["down"]),
        ydstogo=int(row["ydstogo"]),
        yardline_100=int(row["yardline_100"]),
        off_timeouts=int(row["off_timeouts"]),
        def_timeouts=int(row["def_timeouts"]),
        home=int(row["home"]),
        receive_2h_ko=int(row["receive_2h_ko"]),
        spread=float(row["spread"]),
        total=float(row["total"]),
        roof="dome" if row["indoor"] else "outdoors",
        ot=bool(row["ot"]),
        playoffs=bool(row["playoffs"]),
        wind=None if row.get("wind") is None or row["indoor"] else float(row["wind"]),
        temp=None if row.get("temp") is None or row["indoor"] else float(row["temp"]),
    )
