"""Coverage features of the player model (plan P08; documentation/11 -> CB / S: opponent
passing volume and target depth), prefix `cvg_`.

What a defensive back faces, over the last 8 games of the teams involved (suffix `_l8`), from
team passing totals (the player box scores summed per team-game: targets, receptions,
receiving yards, air yards on targets):

- **The opposing offense** (`cvg_opp_*`, keyed on the row's `opponent`): targets per game
  (passing volume), air yards per target (target depth), completion rate and yards per target.
- **The defender's own defense** (`cvg_own_*`, keyed on the row's `team`): targets it faced
  per game, the depth of those targets, the completion rate and yards per target it allowed,
  its pressure rate (PFR, a week late) and sacks per opposing dropback, and its as-of pass
  defense rating (`team_ratings.def_pass_epa`, EPA allowed: higher = worse).

Everything else a back needs (the opponent's pass rate and plays per game, its pass EPA
rating, pressure allowed, the expected game script, his own snap share and history) already
comes from the existing families, so this one only adds what was missing. The `cvg_*`
columns exist only to feed the CB/S models: `models.player_model.feature_columns` picks them
for `CB/S` targets and leaves every other target's feature set untouched.

NGS cushion and separation describe receivers, not the defender covering them (the weekly
rows carry no coverage link and only receivers with 5+ targets), and P06 found separation
barely predicts volume, so they are left out.

As-of rules (as in `player_opponent`): a row at `t` uses games with `t_game < t`
(`asof_join`, lag 0); PFR-based pressure rate lags one more week. Nothing reads the row's own
game.

Interface: `coverage_features(inp, hist, rows)` -> `player_id`, `game_id` + `CVG_FEATURES`
(all Float64), one row per `rows` row in the same order.
"""

from __future__ import annotations

import polars as pl

from nflengine.features.player_data import PlayerInputs, asof_join
from nflengine.features.player_opponent import opponent_features

W = 8  # games in every window
MIN_GAMES = 3  # games before a per-game average exists
MIN_TARGETS = 60  # targets in the window before a rate exists (about two games of passing)

_OPP = (
    "cvg_opp_targets_l8",
    "cvg_opp_adot_l8",
    "cvg_opp_cmp_pct_l8",
    "cvg_opp_ypt_l8",
)
_OWN = (
    "cvg_own_targets_l8",
    "cvg_own_adot_l8",
    "cvg_own_cmp_pct_l8",
    "cvg_own_ypt_l8",
)
_OWN_DEFENSE = {  # `opponent_features` column of a defense -> feature
    "opp_def_pressure_rate_l8": "cvg_own_pressure_rate_l8",
    "opp_def_sack_rate_l8": "cvg_own_sack_rate_l8",
    "opp_def_pass_epa": "cvg_own_def_pass_epa",
}

CVG_FEATURES: tuple[str, ...] = (*_OPP, *_OWN, *_OWN_DEFENSE.values())

# the feature built from PFR (published about a week late: only games before t - 1)
LATE_FEATURES: tuple[str, ...] = ("cvg_own_pressure_rate_l8",)


def _team_passing(hist: pl.DataFrame) -> pl.DataFrame:
    """One row per (game, offense team): its defense (`opponent`), `t` and the passing totals
    its players put up (targets, receptions, yards, air yards on targets)."""
    return hist.group_by("game_id", "team").agg(
        pl.col("opponent").first(),
        pl.col("t").first(),
        pl.col("targets").sum().alias("tgt"),
        pl.col("receptions").sum().alias("rec"),
        pl.col("receiving_yards").sum().alias("yds"),
        pl.col("receiving_air_yards").sum().alias("air"),
    )


def _rates(tg: pl.DataFrame, by: str, names: tuple[str, ...]) -> pl.DataFrame:
    """Per team `by` (`team` = as the offense, `opponent` = as the defense), the rolling
    passing profile over its last `W` games: `_k`, `t`, `names` (targets per game, air yards
    per target, completion rate, yards per target)."""
    base = tg.select(
        pl.col(by).alias("_k"),
        "t",
        pl.lit(1.0).alias("n"),
        pl.col("tgt", "rec", "yds", "air").cast(pl.Float64),
    ).sort("_k", "t")
    r = base.with_columns(
        [
            pl.col(c).rolling_sum(W, min_samples=1).over("_k")
            for c in ("n", "tgt", "rec", "yds", "air")
        ]
    )

    def ratio(num: str, den: str, floor: float, alias: str) -> pl.Expr:
        return pl.when(pl.col(den) >= floor).then(pl.col(num) / pl.col(den)).alias(alias)

    return r.select(
        "_k",
        "t",
        ratio("tgt", "n", MIN_GAMES, names[0]),
        ratio("air", "tgt", MIN_TARGETS, names[1]),
        ratio("rec", "tgt", MIN_TARGETS, names[2]),
        ratio("yds", "tgt", MIN_TARGETS, names[3]),
    )


def coverage_features(inp: PlayerInputs, hist: pl.DataFrame, rows: pl.DataFrame) -> pl.DataFrame:
    """The `cvg_*` features for every row of `rows` (`player_id`, `game_id`, `team`,
    `opponent`, `t`, ...), in `rows`' order."""
    tg = _team_passing(hist)
    key = rows.select("player_id", "game_id", "t", "team", "opponent")
    parts = []
    for col, frame, names in (
        ("opponent", _rates(tg, "team", _OPP), _OPP),  # the offense the row's team faces
        ("team", _rates(tg, "opponent", _OWN), _OWN),  # the row's own defense
    ):
        spine = key.select("player_id", "game_id", "t", pl.col(col).alias("_k"))
        parts.append(asof_join(spine, frame, by="_k").select(names))
    # the row's own defense through the opponent family: `opponent` := `team`
    own = opponent_features(inp, hist, rows.with_columns(pl.col("team").alias("opponent")))
    parts.append(own.select([pl.col(s).alias(d) for s, d in _OWN_DEFENSE.items()]))
    out = key.select("player_id", "game_id").hstack(
        [c for part in parts for c in part.get_columns()]
    )
    return out.select("player_id", "game_id", pl.col(list(CVG_FEATURES)).cast(pl.Float64))
