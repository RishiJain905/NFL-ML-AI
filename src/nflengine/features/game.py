"""Game feature table for the game model (documentation/04 -> B. Game model; plan P03).

One row per game, built **as of the game's week** (D44): every team-level input is the
feature-table row keyed by (season, week, team), which only contains games from weeks
before `week`. Schedule facts (home/away, neutral site, rest days, divisional game,
venue) are known before the season and are not leakage. Market columns are the closing
line for past games and the current line for upcoming ones.

Column groups (see `FEATURES` in `models/game_model.py` for what each model uses):
- ids: season, week, game_id, game_type, kickoff_utc, home_team, away_team, neutral_site
- targets (null until played): home_score, away_score, margin (= result, home - away),
  total, home_win (1 / 0 / 0.5 for a tie)
- per side (home_* / away_*): ratings (net/off/def EPA, pass/rush splits, net success
  rate, prior_weight), elo, rolling points for/against, QB status, travel
- matchup features: net_diff, pass_matchup / rush_matchup (expected home offense EPA in
  that split minus expected away offense EPA: `(home_off + away_def) - (away_off + home_def)`,
  because `def_*` is EPA **allowed**, so a weak defense adds to the opponent's offense),
  net_sr_diff, elo_diff (per 100
  Elo points), hfa (0 at neutral sites), rest_diff (clipped to +-7 days), bye_diff,
  short_diff, div_game, travel_diff (1000 km), tz_diff (hours crossed), qb_adj_diff,
  prior_weight (mean of both sides), net_x_prior
- totals features: off_sum, def_sum, qb_adj_sum, league_ppg, pts_base_total, roof_dome
- baselines: elo_prob and elo_margin (as-of Elo, home field 0 at neutral sites),
  pts_base_home / pts_base_away (rolling points), market_ml_prob (vig-free moneyline)
- market: spread_line (+ = home favored), total_line, market_source,
  mkt_home_points / mkt_away_points (implied team totals)

`build_game_features` is pure (frames in, frame out). `load_game_inputs` reads what it
needs from D:, and `nfl features game` writes `features/game_features.parquet`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

from nflengine.features.asof import asof_keys
from nflengine.paths import DataPaths
from nflengine.settings import get_config

RATING_COLS = (
    "net_epa",
    "off_epa",
    "def_epa",
    "off_pass_epa",
    "def_pass_epa",
    "off_rush_epa",
    "def_rush_epa",
    "net_sr",
    "prior_weight",
)
QB_COLS = ("qb_id", "qb_name", "qb_source", "qb_value", "qb_baseline", "qb_adj", "qb_changed")
TRAVEL_COLS = ("home_travel_km", "away_travel_km", "home_tz_shift", "away_tz_shift")
TARGET_COLS = ("home_score", "away_score", "margin", "total", "home_win")
# Known before kickoff (schedule and market); never treated as an outcome.
PREGAME_COLS = (
    "home_rest",
    "away_rest",
    "div_game",
    "spread_line",
    "total_line",
    "home_moneyline",
    "away_moneyline",
    "away_spread_odds",
    "home_spread_odds",
    "under_odds",
    "over_odds",
    "neutral_site",
    "gsis",
    "pff",
    "ftn",
)
FORM_WINDOW = 8  # team games in the rolling points baseline
LEAGUE_WEEKS = 16  # weeks with games in the league scoring level
REST_CLIP = 7


def _t(season: pl.Expr | str = "season", week: pl.Expr | str = "week") -> pl.Expr:
    """Continuous week index across seasons (weeks never exceed 22)."""
    s = pl.col(season) if isinstance(season, str) else season
    w = pl.col(week) if isinstance(week, str) else week
    return ((s.cast(pl.Int64) - 2000) * 22 + w.cast(pl.Int64)).alias("t")


def _played(games: pl.DataFrame) -> pl.DataFrame:
    return games.filter(
        pl.col("completed").fill_null(False)
        & pl.col("home_score").is_not_null()
        & pl.col("away_score").is_not_null()
    )


def team_points_form(games: pl.DataFrame, window: int = FORM_WINDOW) -> pl.DataFrame:
    """Rolling points for / against per team over its last `window` played games (any
    season), as rows (team, t) to be joined as-of strictly before a game's week."""
    played = _played(games)
    long = pl.concat(
        [
            played.select(
                "season",
                "week",
                pl.col(f"{a}_team").alias("team"),
                pl.col(f"{a}_score").cast(pl.Float64).alias("pf"),
                pl.col(f"{b}_score").cast(pl.Float64).alias("pa"),
            )
            for a, b in (("home", "away"), ("away", "home"))
        ]
    )
    return (
        long.with_columns(_t())
        .sort("team", "t")
        .with_columns(
            pl.col("pf").rolling_mean(window, min_samples=1).over("team").alias("pf_avg"),
            pl.col("pa").rolling_mean(window, min_samples=1).over("team").alias("pa_avg"),
        )
        .select("team", "t", "pf_avg", "pa_avg")
        .sort("t")
    )


def league_scoring(games: pl.DataFrame, weeks: int = LEAGUE_WEEKS) -> pl.DataFrame:
    """League points per team-game over the last `weeks` weeks with played games, as rows
    (t, league_ppg) to be joined as-of strictly before a game's week."""
    per_week = (
        _played(games)
        .group_by("season", "week")
        .agg(
            (pl.col("home_score") + pl.col("away_score")).sum().cast(pl.Float64).alias("pts"),
            (pl.len() * 2).cast(pl.Float64).alias("n"),
        )
        .with_columns(_t())
        .sort("t")
    )
    return per_week.select(
        "t",
        (
            pl.col("pts").rolling_sum(weeks, min_samples=1)
            / pl.col("n").rolling_sum(weeks, min_samples=1)
        ).alias("league_ppg"),
    )


def current_lines(lines: pl.DataFrame | None) -> pl.DataFrame:
    """One line per game from the `lines` table: nflverse, then the Odds API median across
    books, then ESPN (D40; the `curated-data` recipe)."""
    schema = {
        "game_id": pl.String,
        "ln_spread": pl.Float64,
        "ln_total": pl.Float64,
        "ln_source": pl.String,
    }
    if lines is None or lines.is_empty():
        return pl.DataFrame(schema=schema)
    agg = lines.group_by("game_id").agg(
        *[
            pl.col(c).filter(pl.col("source") == src).median().alias(f"{c}_{src}")
            for c in ("home_spread", "total")
            for src in ("nflverse_schedules", "odds_api", "espn")
        ]
    )
    order = ("nflverse_schedules", "odds_api", "espn")
    source = pl.lit(None, dtype=pl.String)
    for src in reversed(order):
        source = (
            pl.when(pl.col(f"home_spread_{src}").is_not_null()).then(pl.lit(src)).otherwise(source)
        )
    return agg.select(
        "game_id",
        pl.coalesce([pl.col(f"home_spread_{s}") for s in order]).alias("ln_spread"),
        pl.coalesce([pl.col(f"total_{s}") for s in order]).alias("ln_total"),
        source.alias("ln_source"),
    ).cast(schema)


def _side(df: pl.DataFrame, cols: tuple[str, ...] | list[str], side: str) -> pl.DataFrame:
    keep = [c for c in cols if c in df.columns]
    return df.select(
        "season",
        "week",
        pl.col("team").alias(f"{side}_team"),
        *[pl.col(c).alias(f"{side}_{c}") for c in keep],
    )


def _ml_prob(ml: str) -> pl.Expr:
    """Implied probability of an American moneyline (with vig)."""
    m = pl.col(ml).cast(pl.Float64)
    return pl.when(m > 0).then(100 / (m + 100)).otherwise(-m / (-m + 100))


def build_game_features(
    games: pl.DataFrame,
    ratings: pl.DataFrame,
    elo: pl.DataFrame,
    *,
    qb: pl.DataFrame | None = None,
    travel: pl.DataFrame | None = None,
    lines: pl.DataFrame | None = None,
    elo_hfa: float = 48.0,
    min_season: int = 2011,
) -> pl.DataFrame:
    """The game feature table (see the module docstring). Games without as-of team rows
    (weeks beyond the latest as-of key) are left out."""
    g = games.filter(pl.col("season") >= min_season)
    base = g.select(
        "season",
        "week",
        "game_id",
        "game_type",
        "kickoff_utc",
        "home_team",
        "away_team",
        pl.col("neutral_site").fill_null(False),
        "completed",
        "home_rest",
        "away_rest",
        "div_game",
        "roof",
        "spread_line",
        "total_line",
        "home_moneyline",
        "away_moneyline",
        # Not the schedule's starting-QB fields: for played games they name the actual
        # starter, which a Tuesday run doesn't know (QB status comes from `features.qb`).
        pl.when(pl.col("completed").fill_null(False))
        .then(pl.col("home_score"))
        .alias("home_score"),
        pl.when(pl.col("completed").fill_null(False))
        .then(pl.col("away_score"))
        .alias("away_score"),
    ).with_columns(_t())

    df = base
    for side in ("home", "away"):
        df = df.join(
            _side(ratings, RATING_COLS, side), on=["season", "week", f"{side}_team"], how="left"
        )
        df = df.join(_side(elo, ["elo"], side), on=["season", "week", f"{side}_team"], how="left")
        if qb is not None:
            df = df.join(
                _side(qb, QB_COLS, side), on=["season", "week", f"{side}_team"], how="left"
            )
    df = df.filter(pl.col("home_net_epa").is_not_null() & pl.col("away_net_epa").is_not_null())

    form = team_points_form(games)
    df = df.sort("t")
    for side in ("home", "away"):
        df = df.join_asof(
            form.rename(
                {"team": f"{side}_team", "pf_avg": f"{side}_pf_avg", "pa_avg": f"{side}_pa_avg"}
            ),
            on="t",
            by=f"{side}_team",
            strategy="backward",
            allow_exact_matches=False,
            check_sortedness=False,  # both sides sorted by t above
        )
    df = df.join_asof(
        league_scoring(games),
        on="t",
        strategy="backward",
        allow_exact_matches=False,
        check_sortedness=False,
    )
    if travel is not None:
        df = df.join(travel.select("game_id", *TRAVEL_COLS), on="game_id", how="left")
    ln = current_lines(lines)
    df = df.join(ln, on="game_id", how="left")

    hfa = 1 - pl.col("neutral_site").cast(pl.Int8)
    elo_pts = pl.col("home_elo") - pl.col("away_elo") + elo_hfa * hfa
    played = pl.col("completed").fill_null(False) & pl.col("home_score").is_not_null()
    margin = (pl.col("home_score") - pl.col("away_score")).cast(pl.Float64)
    ml_h, ml_a = _ml_prob("home_moneyline"), _ml_prob("away_moneyline")
    pw = (pl.col("home_prior_weight") + pl.col("away_prior_weight")) / 2
    league = pl.col("league_ppg").fill_null(22.0)
    out = df.with_columns(
        # ---- targets
        pl.when(played).then(margin).alias("margin"),
        pl.when(played)
        .then((pl.col("home_score") + pl.col("away_score")).cast(pl.Float64))
        .alias("total"),
        pl.when(played)
        .then(pl.when(margin > 0).then(1.0).when(margin < 0).then(0.0).otherwise(0.5))
        .alias("home_win"),
        # ---- matchup
        (pl.col("home_net_epa") - pl.col("away_net_epa")).alias("net_diff"),
        (
            (pl.col("home_off_pass_epa") + pl.col("away_def_pass_epa"))
            - (pl.col("away_off_pass_epa") + pl.col("home_def_pass_epa"))
        ).alias("pass_matchup"),
        (
            (pl.col("home_off_rush_epa") + pl.col("away_def_rush_epa"))
            - (pl.col("away_off_rush_epa") + pl.col("home_def_rush_epa"))
        ).alias("rush_matchup"),
        (pl.col("home_net_sr") - pl.col("away_net_sr")).alias("net_sr_diff"),
        ((pl.col("home_elo") - pl.col("away_elo")) / 100).alias("elo_diff"),
        hfa.cast(pl.Float64).alias("hfa"),
        (pl.col("home_rest") - pl.col("away_rest"))
        .clip(-REST_CLIP, REST_CLIP)
        .cast(pl.Float64)
        .fill_null(0.0)
        .alias("rest_diff"),
        (
            (pl.col("home_rest") >= 13).cast(pl.Float64)
            - (pl.col("away_rest") >= 13).cast(pl.Float64)
        )
        .fill_null(0.0)
        .alias("bye_diff"),
        ((pl.col("home_rest") <= 5).cast(pl.Float64) - (pl.col("away_rest") <= 5).cast(pl.Float64))
        .fill_null(0.0)
        .alias("short_diff"),
        pl.col("div_game").cast(pl.Float64).fill_null(0.0),
        pw.alias("prior_weight"),
        ((pl.col("home_net_epa") - pl.col("away_net_epa")) * pw).alias("net_x_prior"),
        # ---- totals
        (pl.col("home_off_epa") + pl.col("away_off_epa")).alias("off_sum"),
        (pl.col("home_def_epa") + pl.col("away_def_epa")).alias("def_sum"),
        league.alias("league_ppg"),
        (
            (pl.col("home_pf_avg").fill_null(league) + pl.col("away_pa_avg").fill_null(league)) / 2
        ).alias("pts_base_home"),
        (
            (pl.col("away_pf_avg").fill_null(league) + pl.col("home_pa_avg").fill_null(league)) / 2
        ).alias("pts_base_away"),
        pl.when(pl.col("roof") == "dome")
        .then(1.0)
        .when(pl.col("roof").is_in(["closed", "open"]))
        .then(0.5)
        .otherwise(0.0)
        .alias("roof_dome"),
        # ---- baselines
        (1 / (1 + 10 ** (-elo_pts / 400))).alias("elo_prob"),
        (elo_pts / 25).alias("elo_margin"),
        (ml_h / (ml_h + ml_a)).alias("market_ml_prob"),
        # ---- market (nflverse closing / current first, then the lines table)
        pl.coalesce(["spread_line", "ln_spread"]).alias("spread_line"),
        pl.coalesce(["total_line", "ln_total"]).alias("total_line"),
        pl.when(pl.col("spread_line").is_not_null())
        .then(pl.lit("nflverse_schedules"))
        .otherwise(pl.col("ln_source"))
        .alias("market_source"),
    ).with_columns(
        (pl.col("pts_base_home") + pl.col("pts_base_away")).alias("pts_base_total"),
        ((pl.col("total_line") + pl.col("spread_line")) / 2).alias("mkt_home_points"),
        ((pl.col("total_line") - pl.col("spread_line")) / 2).alias("mkt_away_points"),
    )
    if travel is not None:
        out = out.with_columns(
            ((pl.col("away_travel_km") - pl.col("home_travel_km")) / 1000)
            .fill_null(0.0)
            .alias("travel_diff"),
            (pl.col("away_tz_shift").abs() - pl.col("home_tz_shift").abs())
            .fill_null(0.0)
            .alias("tz_diff"),
        )
    if qb is not None:
        out = out.with_columns(
            (pl.col("home_qb_adj").fill_null(0.0) - pl.col("away_qb_adj").fill_null(0.0)).alias(
                "qb_adj_diff"
            ),
            (pl.col("home_qb_adj").fill_null(0.0) + pl.col("away_qb_adj").fill_null(0.0)).alias(
                "qb_adj_sum"
            ),
        )
    drop = [c for c in ("t", "ln_spread", "ln_total", "ln_source") if c in out.columns]
    return out.drop(drop).sort("season", "week", "kickoff_utc", "game_id")


# ---- I/O ------------------------------------------------------------------------------------


@dataclass
class GameInputs:
    games: pl.DataFrame
    ratings: pl.DataFrame
    elo: pl.DataFrame
    qb: pl.DataFrame | None
    travel: pl.DataFrame | None
    lines: pl.DataFrame | None


def load_game_inputs(
    paths: DataPaths,
    min_season: int = 2011,
    *,
    with_qb: bool = True,
    with_travel: bool = True,
    qb_overrides: Callable[[pl.DataFrame], pl.DataFrame | None] | None = None,
    log: Callable[[str], None] = print,
) -> GameInputs:
    """Read games, the P02 feature tables, lines; compute QB status and travel.

    `qb_overrides(games)` may return expected-starter overrides (the live run's schedule /
    depth-chart / injury resolver, or the research oracle) for `features.qb`.
    """
    cur, feat = paths.curated, paths.features
    games = pl.read_parquet(cur / "games.parquet")
    missing = [n for n in ("team_ratings", "team_elo") if not (feat / f"{n}.parquet").exists()]
    if missing:
        raise FileNotFoundError(f"feature tables {missing} missing: run `nfl ratings build`")
    ratings = pl.read_parquet(feat / "team_ratings.parquet")
    elo = pl.read_parquet(feat / "team_elo.parquet")
    lines_path = cur / "lines.parquet"
    lines = pl.read_parquet(lines_path) if lines_path.exists() else None
    qb = travel = None
    if with_travel:
        from nflengine.features.venues import game_travel, load_venues

        travel = game_travel(games.filter(pl.col("season") >= min_season - 1), load_venues())
    if with_qb:
        from nflengine.features.qb import QBParams, load_qb_inputs, team_qb_features

        qi = load_qb_inputs(paths, min_season - 3)
        seasons = range(min_season, int(games["season"].max()) + 1)
        keys = asof_keys(games, seasons)
        overrides = qb_overrides(games) if qb_overrides else None
        qb = team_qb_features(
            qi["games"],
            qi["dropbacks"],
            keys,
            params=QBParams.from_config(get_config().qb),
            depth_charts=qi.get("depth_charts"),
            overrides=overrides,
            players=qi.get("players"),
        )
        log(f"  qb features: {qb.height:,} team-weeks")
    return GameInputs(games, ratings, elo, qb, travel, lines)
