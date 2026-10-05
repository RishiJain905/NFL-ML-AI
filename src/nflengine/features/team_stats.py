"""Team stat features for the P08 team totals (documentation/11 -> Game and team targets).

One row per (team, regular-season game), 2012 on (the rolling windows warm up on 2010-2011
box scores; training starts in 2013), every feature **as of the game's week** (D44): only games
in weeks strictly before it. No source used here publishes late (box scores and play-by-play are
complete on the Tuesday), so there is no extra lag. Playoff games are left out on both sides,
so a team's form skips them.

Labels (`LABELS`, null until the game is played; box score = nflverse stats, the same numbers
the player model's QB / RB targets sum to):
- `pass_yds`    team passing yards, gross of sack yards (= the sum of its players' `passing_yards`,
                100% of team-games; scrambles count as rushing, penalty yards are in neither);
- `rush_yds`    team rushing yards (QB runs and scrambles included);
- `sacks_taken` sacks the team's offense suffered (`sacks_suffered`, whole sacks);
- `sacks_made`  the opponent's `sacks_suffered` (whole sacks; `def_sacks` carries half sacks);
- `takeaways`   the opponent's interceptions thrown + the opponent's fumbles lost (all phases,
                `fumbles_lost_total`); it equals the team's own `def_interceptions +
                fumble_recovery_opp` in 99.5% of team-games.

Per stat T every team-game has `T` (what the team did) and `T_ag` (what its opponent did in
that game: for `pass_yds` the passing yards allowed, for `sacks_taken` the sacks the team's
defense made, for `takeaways` the team's giveaways), so the matchup arithmetic is the same for
every target: A's expected T against B = A's rolling `T` blended with B's rolling `T_ag`.

Feature families (column prefixes; `FAMILIES`):
- `tm_` / `op_` rolling `WINDOW`-game form of the team and its opponent, `*_for_l8` /
  `*_ag_l8`, for the five targets and the pace series (plays, dropbacks, rushes, pass rate over
  expected);
- `lg_`  league per-team-game average of each target over the last `LEAGUE_WEEKS` weeks;
- `rt_`  the P02 ratings (`features/team_ratings`: EPA, `def_*` = EPA **allowed**, so a matchup
         is `off + opponent def`) and the pass / rush matchups in both directions;
- `ctx_` the P03 game model's expected points / margin (model-only walk-forward: no market);
- `mkt_` the closing line in history, the current line live (leakage rule 5: `no_market`
         variants drop the family);
- `sch_` schedule facts (home, neutral, division game, rest days);
- `wx_`  roof and weather: the observed game temperature / wind in history, the Open-Meteo
         forecast for an unplayed game (the live mismatch of P08's pitfalls: training sees the
         weather that happened, a live run sees a forecast).

`base_<T>` is the baseline each target must beat: (team's rolling T + opponent's rolling T_ag) / 2
for yards and sacks; the league's rolling per-team-game average for takeaways.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field

import duckdb
import polars as pl

from nflengine.features.player_data import asof_join, team_games, team_play_totals
from nflengine.paths import DataPaths, ensure_data_root

WARMUP_SEASON = 2010  # box scores and plays that only warm the rolling windows up
FIRST_SEASON = 2012  # first season with feature rows
TRAIN_START = 2013  # first season trained on (the walk-forward starts from it)
WINDOW = 8  # team games in the rolling form
LEAGUE_WEEKS = 16  # weeks with games in the league average (as `features.game.league_scoring`)

STATS = ("pass_yds", "rush_yds", "sacks_made", "sacks_taken", "takeaways")
PACE = ("plays", "dropbacks", "rushes", "proe")
LABELS = STATS
FAMILIES = {
    "form": ("tm_", "op_"),
    "league": ("lg_",),
    "ratings": ("rt_",),
    "context": ("ctx_",),
    "market": ("mkt_",),
    "schedule": ("sch_",),
    "weather": ("wx_",),
}
FEATURE_PREFIXES = tuple(p for ps in FAMILIES.values() for p in ps)
MARKET_PREFIX = "mkt_"
RATING_COLS = ("off_pass_epa", "off_rush_epa", "def_pass_epa", "def_rush_epa", "prior_weight")
ID_COLS = ("season", "week", "game_id", "team", "opponent", "home", "kickoff_utc", "completed")

BOX_COLS = (
    "game_id",
    "team",
    "passing_yards",
    "rushing_yards",
    "sacks_suffered",
    "passing_interceptions",
    "fumbles_lost_total",
)
GAME_COLS = (
    "game_id",
    "season",
    "week",
    "kickoff_utc",
    "home_team",
    "away_team",
    "completed",
    "neutral_site",
    "div_game",
    "roof",
    "temp",
    "wind",
    "home_rest",
    "away_rest",
)
PLAY_COLS = (
    "game_id",
    "season",
    "season_type",
    "posteam",
    "play_type",
    "pass",
    "rush",
    "qb_dropback",
    "two_point_attempt",
    "rusher_player_id",
    "receiver_player_id",
    "yardline_100",
    "pass_oe",
    "epa",
)


@dataclass
class TeamInputs:
    """Frames the team features read (regular season only, `WARMUP_SEASON` on)."""

    games: pl.DataFrame  # `GAME_COLS`
    box: pl.DataFrame  # `BOX_COLS` per (team, game)
    pace: pl.DataFrame  # `team_play_totals` per (game_id, team)
    ratings: pl.DataFrame = field(default_factory=pl.DataFrame)  # features/team_ratings
    game_features: pl.DataFrame = field(default_factory=pl.DataFrame)  # market columns
    context: pl.DataFrame | None = None  # P03 `game_context`: (game_id, team, ctx_*)
    forecast: pl.DataFrame | None = None  # weather_forecasts: game_id, temp_f, wind_mph
    last_season: int = 0


# ---- loading ---------------------------------------------------------------------------------


def load_team_inputs(
    paths: DataPaths | None = None,
    context: pl.DataFrame | None = None,
    first_season: int = WARMUP_SEASON,
    last_season: int | None = None,
    log: Callable[[str], None] = print,
) -> TeamInputs:
    """Read what the team features need (read-only DuckDB + parquet).

    `context`: the P03 context per (game_id, team) (`features.player.game_context` rows;
    `team_context` builds it from the saved backtest). Games without a row get null ctx_*."""
    paths = paths or ensure_data_root()
    cur = paths.curated
    with closing(duckdb.connect(str(cur / "nfl.duckdb"), read_only=True)) as con:
        last = last_season or int(con.execute("SELECT max(season) FROM games").fetchone()[0])
        rng = [first_season, last]
        games = con.execute(
            f"SELECT {', '.join(GAME_COLS)} FROM games WHERE game_type = 'REG' "
            "AND season BETWEEN ? AND ?",
            rng,
        ).pl()
        box = con.execute(
            f"SELECT {', '.join(BOX_COLS)} FROM team_games WHERE season_type = 'REG' "
            "AND season BETWEEN ? AND ?",
            rng,
        ).pl()
        forecast = None
        if con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'weather_forecasts'"
        ).fetchone()[0]:
            forecast = con.execute(
                "SELECT game_id, temp_f, wind_mph, pulled_at FROM weather_forecasts"
            ).pl()
    plays = (
        pl.scan_parquet((cur / "plays" / "*.parquet").as_posix())
        .filter(
            (pl.col("season") >= first_season)
            & (pl.col("season") <= last)
            & (pl.col("season_type") == "REG")
        )
        .select(PLAY_COLS)
        .collect()
    )
    feat = paths.features

    def read(name: str) -> pl.DataFrame:
        f = feat / f"{name}.parquet"
        return pl.read_parquet(f) if f.exists() else pl.DataFrame()

    games = games.with_columns(
        pl.col("season", "week").cast(pl.Int32),
        pl.col("kickoff_utc").dt.convert_time_zone("UTC"),  # DuckDB hands back local time
    )
    log(
        f"team inputs {first_season}-{last}: {games.height:,} games, {box.height:,} box rows, "
        f"{plays.height:,} plays"
    )
    return TeamInputs(
        games=games,
        box=box,
        pace=team_play_totals(plays),
        ratings=read("team_ratings"),
        game_features=read("game_features"),
        context=context,
        forecast=forecast,
        last_season=last,
    )


def team_context(paths: DataPaths | None = None) -> pl.DataFrame:
    """The P03 context for finished seasons: the canonical model-only walk-forward backtest
    (`runs/backtests/game/model_only`), as the player model reads it. The current season's
    context needs the live walk-forward (`player_runs.game_context_frames`); callers pass that
    as `context` to `load_team_inputs` instead."""
    from nflengine.features.player import game_context

    paths = paths or ensure_data_root()
    path = paths.runs / "backtests" / "game" / "model_only" / "predictions_games.parquet"
    if not path.exists():
        return pl.DataFrame()
    return game_context(pl.read_parquet(path))


# ---- per-game stats and rolling form ------------------------------------------------------------


def game_stats(inp: TeamInputs, rows: pl.DataFrame) -> pl.DataFrame:
    """One row per played (team, game): the five stats (`STATS`), each also as `<T>_ag` (the
    opponent's value), and the pace series (`PACE`, each also `_ag`). Games without a box
    score row (unplayed) are left out."""
    b = inp.box
    own = b.select(
        "game_id",
        "team",
        pl.col("passing_yards").cast(pl.Float64).alias("pass_yds"),
        pl.col("rushing_yards").cast(pl.Float64).alias("rush_yds"),
        pl.col("sacks_suffered").cast(pl.Float64).alias("sacks_taken"),
        (pl.col("passing_interceptions") + pl.col("fumbles_lost_total"))
        .cast(pl.Float64)
        .alias("giveaways"),
    )
    base = rows.select("game_id", "team", "opponent", "season", "week", "t")
    f = base.join(own, on=["game_id", "team"], how="inner")
    opp = own.select(
        "game_id",
        pl.col("team").alias("opponent"),
        pl.col("sacks_taken").alias("sacks_made"),
        pl.col("giveaways").alias("takeaways"),
    )
    f = f.join(opp, on=["game_id", "opponent"], how="inner").drop("giveaways")
    p = inp.pace.select(
        "game_id",
        "team",
        pl.col("team_plays").cast(pl.Float64).alias("plays"),
        pl.col("team_dropbacks").cast(pl.Float64).alias("dropbacks"),
        pl.col("team_rushes").cast(pl.Float64).alias("rushes"),
        pl.col("team_proe").cast(pl.Float64).alias("proe"),
    )
    f = f.join(p, on=["game_id", "team"], how="left")
    ag = f.select(
        "game_id",
        pl.col("team").alias("opponent"),
        *[pl.col(s).alias(f"{s}_ag") for s in (*STATS, *PACE)],
    )
    return f.join(ag, on=["game_id", "opponent"], how="left").sort("team", "t")


def series_names() -> list[str]:
    return [*STATS, *PACE, *[f"{s}_ag" for s in (*STATS, *PACE)]]


def rolling_form(stats: pl.DataFrame, window: int = WINDOW) -> pl.DataFrame:
    """Rolling mean of every series over the team's last `window` played games (any season),
    as rows (team, t) to be joined as-of strictly before a game's week."""
    cols = series_names()
    return (
        stats.sort("team", "t")
        .with_columns(
            [pl.col(c).rolling_mean(window, min_samples=1).over("team").alias(c) for c in cols]
        )
        .select("team", "t", *cols)
    )


def league_form(stats: pl.DataFrame, weeks: int = LEAGUE_WEEKS) -> pl.DataFrame:
    """League per-team-game average of each target over the last `weeks` weeks with played
    games, as rows (t, lg_<T>_l16) to be joined as-of strictly before a game's week."""
    per_week = (
        stats.group_by("t")
        .agg([pl.col(s).sum().alias(s) for s in STATS] + [pl.len().cast(pl.Float64).alias("_n")])
        .sort("t")
    )
    n = pl.col("_n").rolling_sum(weeks, min_samples=1)
    return per_week.select(
        "t",
        *[
            (pl.col(s).rolling_sum(weeks, min_samples=1) / n).alias(f"lg_{s}_l{weeks}")
            for s in STATS
        ],
    )


def _side_form(rows: pl.DataFrame, form: pl.DataFrame, side: str) -> pl.DataFrame:
    """`tm_*` (the row's team) or `op_*` (its opponent) rolling form, as of the row's week."""
    key = pl.col("team") if side == "tm" else pl.col("opponent")
    r = rows.select("game_id", "team", "t", key.alias("_k"))
    cols = [c for c in form.columns if c not in ("team", "t")]
    h = form.select(
        pl.col("team").alias("_k"), "t", *[pl.col(c).alias(f"{side}_{c}") for c in cols]
    )
    j = asof_join(r, h, by="_k").drop("_k", "t", "t_hist")
    rename = {
        f"{side}_{c}": f"{side}_{c[:-3]}_ag_l{WINDOW}"
        if c.endswith("_ag")
        else f"{side}_{c}_for_l{WINDOW}"
        for c in cols
    }
    return j.rename(rename)


def form_features(rows: pl.DataFrame, stats: pl.DataFrame) -> pl.DataFrame:
    """`tm_*` / `op_*` rolling form (`*_for_l8` / `*_ag_l8`) per (game_id, team)."""
    form = rolling_form(stats)
    tm = _side_form(rows, form, "tm")
    op = _side_form(rows, form, "op").drop("game_id", "team")
    return tm.hstack(op)  # both keep the row order of `rows`


def league_features(rows: pl.DataFrame, stats: pl.DataFrame) -> pl.DataFrame:
    """`lg_*` league averages as of each row's week."""
    lg = league_form(stats)
    r = rows.select("game_id", "team", "t").with_row_index("_ri").sort("t")
    out = r.join_asof(
        lg.sort("t"),
        on="t",
        strategy="backward",
        allow_exact_matches=False,
        check_sortedness=False,
    )
    return out.sort("_ri").drop("_ri", "t")


# ---- other families --------------------------------------------------------------------------


def rating_features(inp: TeamInputs, rows: pl.DataFrame) -> pl.DataFrame:
    """`rt_*`: the P02 ratings of the team and its opponent, and the matchups. The (season,
    week) ratings row is built from weeks before `week` (D44), so it joins directly.
    `def_*` is EPA allowed: expected offense = `off + opponent def`."""
    r = rows.select("game_id", "team", "opponent", "season", "week")
    cols = ["game_id", "team"]
    tr = inp.ratings
    names = [f"rt_{s}_{c}" for s in ("tm", "op") for c in RATING_COLS]
    if tr.is_empty():
        return r.select(
            "game_id", "team", *[pl.lit(None, dtype=pl.Float64).alias(n) for n in names]
        ).with_columns(
            *[pl.lit(None, dtype=pl.Float64).alias(n) for n in _RATING_MATCHUPS],
        )
    t = tr.select(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        "team",
        *[pl.col(c).cast(pl.Float64) for c in RATING_COLS],
    )
    out = r.join(
        t.rename({c: f"rt_tm_{c}" for c in RATING_COLS}),
        on=["season", "week", "team"],
        how="left",
    ).join(
        t.rename({**{c: f"rt_op_{c}" for c in RATING_COLS}, "team": "opponent"}),
        on=["season", "week", "opponent"],
        how="left",
    )
    out = out.with_columns(
        (pl.col("rt_tm_off_pass_epa") + pl.col("rt_op_def_pass_epa")).alias("rt_pass_matchup"),
        (pl.col("rt_tm_off_rush_epa") + pl.col("rt_op_def_rush_epa")).alias("rt_rush_matchup"),
        (pl.col("rt_op_off_pass_epa") + pl.col("rt_tm_def_pass_epa")).alias("rt_def_pass_matchup"),
        (pl.col("rt_op_off_rush_epa") + pl.col("rt_tm_def_rush_epa")).alias("rt_def_rush_matchup"),
    )
    return out.select(*cols, *names, *_RATING_MATCHUPS)


_RATING_MATCHUPS = (
    "rt_pass_matchup",
    "rt_rush_matchup",
    "rt_def_pass_matchup",
    "rt_def_rush_matchup",
)


def context_features(inp: TeamInputs, rows: pl.DataFrame) -> pl.DataFrame:
    """`ctx_*`: the P03 game model's expected points for / against, margin and total for the
    team (walk-forward or live predictions, null where there is none)."""
    names = ("ctx_pred_points", "ctx_pred_opp_points", "ctx_exp_margin", "ctx_pred_total")
    r = rows.select("game_id", "team")
    ctx = inp.context
    if ctx is None or ctx.is_empty():
        return r.with_columns([pl.lit(None, dtype=pl.Float64).alias(n) for n in names])
    c = ctx.select(
        "game_id",
        "team",
        pl.col("ctx_pred_points").cast(pl.Float64),
        pl.col("ctx_pred_opp_points").cast(pl.Float64),
        pl.col("ctx_exp_margin").cast(pl.Float64),
    ).with_columns(
        (pl.col("ctx_pred_points") + pl.col("ctx_pred_opp_points")).alias("ctx_pred_total")
    )
    return r.join(c, on=["game_id", "team"], how="left").select("game_id", "team", *names)


def market_features(inp: TeamInputs, rows: pl.DataFrame) -> pl.DataFrame:
    """`mkt_*` from the game feature table: the team's spread (+ = favored), the total and
    the implied team totals. Closing lines in history, the current line live."""
    names = ("mkt_spread", "mkt_total", "mkt_points", "mkt_opp_points")
    r = rows.select("game_id", "team", "home")
    gf = inp.game_features
    if gf.is_empty() or not {"spread_line", "total_line"} <= set(gf.columns):
        return r.drop("home").with_columns([pl.lit(None, dtype=pl.Float64).alias(n) for n in names])
    g = gf.select(
        "game_id",
        pl.col("spread_line").cast(pl.Float64).alias("_spread"),
        pl.col("total_line").cast(pl.Float64).alias("_total"),
        pl.col("mkt_home_points").cast(pl.Float64).alias("_hp"),
        pl.col("mkt_away_points").cast(pl.Float64).alias("_ap"),
    )
    j = r.join(g, on="game_id", how="left")
    h = pl.col("home")
    return j.select(
        "game_id",
        "team",
        pl.when(h).then(pl.col("_spread")).otherwise(-pl.col("_spread")).alias("mkt_spread"),
        pl.col("_total").alias("mkt_total"),
        pl.when(h).then(pl.col("_hp")).otherwise(pl.col("_ap")).alias("mkt_points"),
        pl.when(h).then(pl.col("_ap")).otherwise(pl.col("_hp")).alias("mkt_opp_points"),
    )


def schedule_features(inp: TeamInputs, rows: pl.DataFrame) -> pl.DataFrame:
    """`sch_*`: home, neutral site, division game and rest days (schedule facts, known before
    the season, not leakage)."""
    g = inp.games.select(
        "game_id",
        pl.col("neutral_site").fill_null(False).cast(pl.Float64).alias("sch_neutral"),
        pl.col("div_game").fill_null(0).cast(pl.Float64).alias("sch_div"),
        pl.col("home_rest").cast(pl.Float64).alias("_hr"),
        pl.col("away_rest").cast(pl.Float64).alias("_ar"),
    )
    j = rows.select("game_id", "team", "home").join(g, on="game_id", how="left")
    h = pl.col("home")
    return j.select(
        "game_id",
        "team",
        h.cast(pl.Float64).alias("sch_home"),
        "sch_neutral",
        "sch_div",
        pl.when(h).then(pl.col("_hr")).otherwise(pl.col("_ar")).alias("sch_rest"),
        pl.when(h).then(pl.col("_ar")).otherwise(pl.col("_hr")).alias("sch_opp_rest"),
    ).with_columns((pl.col("sch_rest") - pl.col("sch_opp_rest")).alias("sch_rest_diff"))


def weather_features(inp: TeamInputs, rows: pl.DataFrame) -> pl.DataFrame:
    """`wx_*`: 1 for a dome (0.5 for a retractable roof), the temperature (F) and wind (mph).

    A played game has the observed weather (null indoors), an unplayed one the latest
    Open-Meteo forecast (`weather_forecasts`) when there is one: training sees what happened,
    a live run a forecast, so the model only gets coarse use out of these."""
    g = inp.games.select(
        "game_id",
        pl.col("completed").fill_null(False).alias("_done"),
        pl.when(pl.col("roof") == "dome")
        .then(1.0)
        .when(pl.col("roof").is_in(["closed", "open"]))
        .then(0.5)
        .otherwise(0.0)
        .alias("wx_dome"),
        pl.col("temp").cast(pl.Float64).alias("_t"),
        pl.col("wind").cast(pl.Float64).alias("_w"),
    )
    j = rows.select("game_id", "team").join(g, on="game_id", how="left")
    fc = inp.forecast
    if fc is not None and not fc.is_empty():
        f = (
            fc.sort("pulled_at")
            .group_by("game_id", maintain_order=True)
            .last()
            .select(
                "game_id",
                pl.col("temp_f").cast(pl.Float64).alias("_ft"),
                pl.col("wind_mph").cast(pl.Float64).alias("_fw"),
            )
        )
        j = j.join(f, on="game_id", how="left")
    else:
        j = j.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("_ft"), pl.lit(None, dtype=pl.Float64).alias("_fw")
        )
    return j.select(
        "game_id",
        "team",
        "wx_dome",
        pl.when(pl.col("_done")).then(pl.col("_t")).otherwise(pl.col("_ft")).alias("wx_temp"),
        pl.when(pl.col("_done")).then(pl.col("_w")).otherwise(pl.col("_fw")).alias("wx_wind"),
    )


# ---- the table --------------------------------------------------------------------------------


def build_team_features(inp: TeamInputs, first_season: int = FIRST_SEASON) -> pl.DataFrame:
    """The team feature table (see the module docstring): one row per (team, REG game) from
    `first_season`, sorted by (season, week, kickoff, game_id, team). Rows of unplayed games
    (the live week) carry null labels."""
    rows = team_games(inp.games)  # every REG game, played or not
    stats = game_stats(inp, rows)
    form = form_features(rows, stats)
    lg = league_features(rows, stats)
    parts = [
        form,
        lg.select("game_id", "team", *[c for c in lg.columns if c.startswith("lg_")]),
        rating_features(inp, rows),
        context_features(inp, rows),
        market_features(inp, rows),
        schedule_features(inp, rows),
        weather_features(inp, rows),
    ]
    out = rows.select(
        "season", "week", "game_id", "team", "opponent", "home", "kickoff_utc", "completed"
    )
    for p in parts:
        out = out.join(p, on=["game_id", "team"], how="left")
    labels = stats.select("game_id", "team", *LABELS)
    out = out.join(labels, on=["game_id", "team"], how="left")
    out = out.with_columns(_baselines())
    return out.filter(pl.col("season") >= first_season).sort(
        "season", "week", "kickoff_utc", "game_id", "team"
    )


def _baselines() -> list[pl.Expr]:
    """`base_<T>`: (team's rolling T + opponent's rolling T_ag) / 2; the league average for
    takeaways. Falls back to the league average when a side has no history."""
    out = []
    for s in STATS:
        lg = pl.col(f"lg_{s}_l{LEAGUE_WEEKS}")
        if s == "takeaways":
            out.append(lg.alias(f"base_{s}"))
            continue
        blend = (pl.col(f"tm_{s}_for_l{WINDOW}") + pl.col(f"op_{s}_ag_l{WINDOW}")) / 2
        out.append(blend.fill_null(lg).alias(f"base_{s}"))
    return out


# ---- column helpers -----------------------------------------------------------------------------


def feature_columns(
    frame: pl.DataFrame, stat: str | None = None, own_only: bool = False
) -> list[str]:
    """Model features of a team feature frame, sorted: every family column, plus `week`.

    `own_only` (needs `stat`): of the `tm_*` / `op_*` form columns, only the stat's own
    `*_for_l8` / `*_ag_l8` and the pace series; the other targets' form is left out."""
    cols = [c for c in frame.columns if c.startswith(FEATURE_PREFIXES)]
    if own_only:
        if stat is None:
            raise ValueError("own_only needs a stat")
        other = {s for s in STATS if s != stat}
        cols = [
            c
            for c in cols
            if not (c.startswith(("tm_", "op_")) and any(f"_{o}_" in c for o in other))
        ]
        cols = [c for c in cols if not (c.startswith("lg_") and c != f"lg_{stat}_l{LEAGUE_WEEKS}")]
    out = sorted(cols)
    if stat is not None:
        out.append(f"base_{stat}")
    return out + (["week"] if "week" in frame.columns else [])


def team_target_frame(feats: pl.DataFrame, stat: str) -> pl.DataFrame:
    """Rows of one target: label `y` (null for unplayed games) and its baseline."""
    return feats.with_columns(
        pl.col(stat).cast(pl.Float64).alias("y"), pl.col(f"base_{stat}").alias("baseline")
    )


def team_rows_for_run(feats: pl.DataFrame, season: int, week: int) -> pl.DataFrame:
    """The live week's rows (every game of `week`) plus everything before it."""
    return feats.filter(
        (pl.col("season") < season) | ((pl.col("season") == season) & (pl.col("week") <= week))
    )


__all__ = [
    "FAMILIES",
    "LABELS",
    "STATS",
    "TeamInputs",
    "build_team_features",
    "feature_columns",
    "load_team_inputs",
    "team_context",
    "team_target_frame",
]
