"""Team Elo (documentation/04 -> A. Team ratings and trend; plan P02).

Standard FiveThirtyEight-style NFL Elo, computed from `params.start_season` (2002) on:

- every team starts at `mean` the first time it appears (franchises keep one code across a
  move, so a relocated team keeps its rating)
- before the first game of each season every rated team is pulled toward the mean:
  `r = mean + (1 - revert) * (r - mean)`
- `elo_diff = home - away + hfa` (hfa is 0 at neutral sites), `p_home = 1 / (1 + 10^(-diff/400))`
- the winner's margin multiplier is `ln(|margin| + 1) * 2.2 / (winner_diff * 0.001 + 2.2)`,
  where `winner_diff` is the winner's rating edge including home field (a tie uses
  `ln 2`); the update is `shift = k * mult * (home_result - p_home)`

Games are walked once in kickoff order (tie-break `game_id`). Games not yet played, and
games that never will be (the cancelled 2022 BUF-CIN), carry the current ratings and
probability but update nothing.

`team_elo` snapshots the same walk at each as-of key, right before the first game on/after
the key, so a rating as of week `w` only ever contains games from weeks before `w` and is
covered by the future-invariance test (`features/leakage.py`).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any

import polars as pl

from nflengine.features.asof import AsOf

_REQUIRED = (
    "game_id",
    "season",
    "week",
    "game_type",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "neutral_site",
    "completed",
    "kickoff_utc",
)
_PROB_EPS = 1e-6

_GAME_SCHEMA: dict[str, pl.DataType | type] = {
    "game_id": pl.String,
    "season": pl.Int32,
    "week": pl.Int32,
    "game_type": pl.String,
    "home_team": pl.String,
    "away_team": pl.String,
    "neutral_site": pl.Boolean,
    "home_elo_pre": pl.Float64,
    "away_elo_pre": pl.Float64,
    "elo_diff": pl.Float64,
    "home_win_prob": pl.Float64,
    "home_result": pl.Float64,
    "completed": pl.Boolean,
    "home_elo_post": pl.Float64,
    "away_elo_post": pl.Float64,
}


@dataclass(frozen=True)
class EloParams:
    """Elo settings. Defaults are the standard 538 NFL values."""

    k: float = 20.0
    hfa: float = 48.0  # Elo points of home-field advantage; 0 at neutral sites
    revert: float = 1 / 3  # pull toward `mean` at the start of each season
    mean: float = 1505.0
    mov: bool = True  # margin-of-victory multiplier
    start_season: int = 2002

    def __post_init__(self) -> None:
        if self.k < 0:
            raise ValueError(f"k must be >= 0, got {self.k}")
        if not 0.0 <= self.revert <= 1.0:
            raise ValueError(f"revert must be in [0, 1], got {self.revert}")

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any] | None) -> EloParams:
        """Build from a config mapping (keys `k hfa revert mean mov start_season`).

        Missing keys take the defaults; unknown keys raise ValueError so a typo can't
        silently run the defaults.
        """
        cfg = dict(cfg or {})
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(cfg) - known)
        if unknown:
            raise ValueError(
                f"unknown Elo parameter(s) {unknown}; expected a subset of {sorted(known)}"
            )
        out: dict[str, Any] = {}
        for name, value in cfg.items():
            if name == "mov":
                if not isinstance(value, bool):
                    raise ValueError(f"mov must be true/false, got {value!r}")
                out[name] = value
            elif name == "start_season":
                out[name] = int(value)
            else:
                out[name] = float(value)
        return cls(**out)


DEFAULT_PARAMS = EloParams()


def _prepare(games: pl.DataFrame, params: EloParams) -> pl.DataFrame:
    """Games from `start_season` on, in walk order, with a `played` flag."""
    missing = [c for c in _REQUIRED if c not in games.columns]
    if missing:
        raise ValueError(f"games is missing column(s) {missing}")
    return (
        games.filter(pl.col("season") >= params.start_season)
        .with_columns(
            (
                pl.col("completed").fill_null(False)
                & pl.col("home_score").is_not_null()
                & pl.col("away_score").is_not_null()
            ).alias("played"),
            pl.col("neutral_site").fill_null(False),
        )
        .sort(["kickoff_utc", "game_id"], nulls_last=True)
    )


def _walk(
    g: pl.DataFrame,
    params: EloParams,
    keys: Sequence[AsOf],
    *,
    stop_after_keys: bool,
) -> tuple[dict[str, list[Any]], list[tuple[int, int, str, float, int]]]:
    """One pass over `g` (already in walk order).

    Returns the per-game columns and, for each key, one (season, week, team, elo, games)
    row per team that plays in the key's season. A key is snapshotted right before the first
    game whose (season, week) is on/after it, after any season-start reversion for that
    game's season, so ratings as of (s, w) contain only games from earlier weeks.
    """
    teams_by_season: dict[int, set[str]] = {}
    for s, h, a in g.select("season", "home_team", "away_team").iter_rows():
        teams_by_season.setdefault(s, set()).update((h, a))

    mean, k_factor, hfa_pts, revert = params.mean, params.k, params.hfa, params.revert
    ratings: dict[str, float] = {}
    played_n: dict[str, int] = {}
    season: int | None = None
    ki = 0
    snaps: list[tuple[int, int, str, float, int]] = []

    def snapshot(key: AsOf) -> None:
        n = played_n if key.season == season else {}
        for team in sorted(teams_by_season.get(key.season, ())):
            snaps.append((key.season, key.week, team, ratings.get(team, mean), n.get(team, 0)))

    cols: dict[str, list[Any]] = {name: [] for name in _GAME_SCHEMA}

    rows = g.select(
        "game_id",
        "season",
        "week",
        "game_type",
        "home_team",
        "away_team",
        "neutral_site",
        "played",
        "home_score",
        "away_score",
    ).iter_rows()
    for gid, s, w, gtype, home, away, neutral, played, hs, as_ in rows:
        # keys of earlier seasons are read before this season's reversion
        while ki < len(keys) and keys[ki].season < s:
            snapshot(keys[ki])
            ki += 1
        if season is None or s > season:
            if season is not None:
                for team in ratings:
                    ratings[team] = mean + (1.0 - revert) * (ratings[team] - mean)
            played_n = {}
            season = s
        while ki < len(keys) and (keys[ki].season, keys[ki].week) <= (s, w):
            snapshot(keys[ki])
            ki += 1
        if stop_after_keys and keys and ki == len(keys):
            break

        r_home = ratings.setdefault(home, mean)
        r_away = ratings.setdefault(away, mean)
        diff = r_home - r_away + (0.0 if neutral else hfa_pts)
        p_home = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))

        result: float | None = None
        post_home: float | None = None
        post_away: float | None = None
        if played:
            result = 1.0 if hs > as_ else (0.0 if hs < as_ else 0.5)
            mult = 1.0
            if params.mov:
                winner_diff = diff if result == 1.0 else (-diff if result == 0.0 else 0.0)
                mult = math.log(max(abs(hs - as_), 1) + 1) * 2.2 / (winner_diff * 0.001 + 2.2)
            shift = k_factor * mult * (result - p_home)
            post_home, post_away = r_home + shift, r_away - shift
            ratings[home], ratings[away] = post_home, post_away
            played_n[home] = played_n.get(home, 0) + 1
            played_n[away] = played_n.get(away, 0) + 1

        for name, value in (
            ("game_id", gid),
            ("season", s),
            ("week", w),
            ("game_type", gtype),
            ("home_team", home),
            ("away_team", away),
            ("neutral_site", neutral),
            ("home_elo_pre", r_home),
            ("away_elo_pre", r_away),
            ("elo_diff", diff),
            ("home_win_prob", p_home),
            ("home_result", result),
            ("completed", played),
            ("home_elo_post", post_home),
            ("away_elo_post", post_away),
        ):
            cols[name].append(value)

    while ki < len(keys):  # keys at or after the last game
        snapshot(keys[ki])
        ki += 1
    return cols, snaps


def elo_games(games: pl.DataFrame, params: EloParams = DEFAULT_PARAMS) -> pl.DataFrame:
    """One row per game from `params.start_season` on, in kickoff order, with the ratings
    both teams carried INTO the game.

    Includes games not yet played (and never-played ones such as a cancellation): they get
    the ratings and win probability current at that point, null `home_result` and post
    ratings, and `completed` False. `completed` here means "played": the input flag is
    true and both scores are present.

    Columns: game_id, season, week, game_type, home_team, away_team, neutral_site,
    home_elo_pre, away_elo_pre, elo_diff (home - away + hfa unless neutral), home_win_prob,
    home_result (1 / 0.5 / 0, null if not played), completed, home_elo_post, away_elo_post.
    """
    cols, _ = _walk(_prepare(games, params), params, (), stop_after_keys=False)
    return pl.DataFrame(cols, schema=_GAME_SCHEMA)


def team_elo(
    games: pl.DataFrame, keys: Sequence[AsOf], params: EloParams = DEFAULT_PARAMS
) -> pl.DataFrame:
    """Elo per team as of each key: the rating BEFORE week `w`'s games of season `s`, i.e.
    after every game played in weeks strictly before the key, plus the season-start
    reversion once season `s` has begun.

    One row per team per key, for every team that has a game in season `s` (32 normally).
    Columns: season (Int32), week (Int32), team, elo (Float64), elo_games (Int32, games the
    team played in season `s` before the key). Keys for a season with no completed games
    yet still get a row per team (with the reversion applied). The result never depends
    on games from the key's week or later, so it is safe to use as a feature.
    """
    unique_keys = sorted(set(keys))
    _, snaps = _walk(_prepare(games, params), params, unique_keys, stop_after_keys=True)
    schema = {
        "season": pl.Int32,
        "week": pl.Int32,
        "team": pl.String,
        "elo": pl.Float64,
        "elo_games": pl.Int32,
    }
    return pl.DataFrame(snaps, schema=schema, orient="row").sort("season", "week", "team")


def elo_metrics(
    elo_games_df: pl.DataFrame,
    seasons: Sequence[int],
    game_types: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Walk-forward Elo scores on completed games (the pre-game rating is the forecast).

    One row per season in `seasons` that has completed games, then a `pooled` row over all
    of them. Columns: scope ('season' | 'pooled'), season (null for pooled), games, brier,
    log_loss, accuracy. A tie counts as outcome 0.5 for Brier and log loss and is left out
    of accuracy; accuracy picks the home team when p > 0.5. Probabilities are clipped to
    [1e-6, 1 - 1e-6] for the log loss. `game_types` (e.g. ["REG"]) restricts the games.
    """
    d = elo_games_df.filter(pl.col("completed") & pl.col("season").is_in(list(seasons)))
    if game_types is not None:
        d = d.filter(pl.col("game_type").is_in(list(game_types)))
    p = pl.col("home_win_prob")
    pc = p.clip(_PROB_EPS, 1.0 - _PROB_EPS)
    y = pl.col("home_result")
    d = d.with_columns(
        ((p - y) ** 2).alias("_brier"),
        (-(y * pc.log() + (1.0 - y) * (1.0 - pc).log())).alias("_log_loss"),
        (y != 0.5).alias("_decisive"),
        ((p > 0.5) == (y == 1.0)).alias("_correct"),
    )
    aggs = [
        pl.len().cast(pl.Int32).alias("games"),
        pl.col("_brier").mean().alias("brier"),
        pl.col("_log_loss").mean().alias("log_loss"),
        pl.col("_correct").filter(pl.col("_decisive")).mean().alias("accuracy"),
    ]
    per_season = (
        d.group_by("season")
        .agg(aggs)
        .sort("season")
        .select(pl.lit("season").alias("scope"), "season", "games", "brier", "log_loss", "accuracy")
    )
    pooled = d.select(
        pl.lit("pooled").alias("scope"),
        pl.lit(None, dtype=pl.Int32).alias("season"),
        *aggs,
    )
    return pl.concat([per_season, pooled.select(per_season.columns)])
