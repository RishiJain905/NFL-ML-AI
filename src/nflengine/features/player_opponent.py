"""Opponent features of the player model (plan P06; documentation/04 -> C. Player model).

What the row's **opponent** (its defense, and its offense for the defender rows) has done
lately, prefix `opp_`, suffix `_l8` = over its last 8 games. Everything is keyed on the row's
`opponent` team and the row's time `t`, so it is computed once per (opponent, t) and joined
back.

Families:
- **Production allowed to a position group**, raw (`opp_rec_yds_allowed_wr_l8`) and **over
  expectation** (`..._oe_l8`): for every (defense, game) the facing offense's group total
  (WR / TE / RB receiving and rushing, main QB passing) minus that offense's own mean of the
  same total over its previous 8 games, then the defense's mean over its last 8 games. A
  defense that allows more than the offenses it faced usually produce gets a positive value
  (the sign convention of `team_ratings.def_*`: higher = worse defense).
- **Pressure, blitz, sacks** (QB and pass-rusher rows): the defense's pressure and blitz rate
  against opposing dropbacks, sacks forced per dropback, and the offense's pressure and sacks
  allowed per dropback. Pressure and blitz come from PFR `pfr_pass` (2018+) and, for blitz
  also, FTN charting (2022+; the two correlate 0.69 per team-game); both a week late: lag 1.
- **Tempo** (LB / S rows): the opposing offense's pass rate, rush rate and plays per game.
- **Team ratings** (`team_ratings`, already as-of rows: the (season, week) row is built from
  earlier weeks, D44): the opponent's EPA allowed / produced by pass and run.

As-of rules: a row at `t` uses games with `t_game < t` (`asof_join`, lag 0); PFR-based rates
use lag 1; ratings rows use `t_rating <= t` and fall back to the team's latest earlier row
when the row's own (season, week) isn't there (a game weeks ahead). Nothing reads the row's own
game.

Interface: `opponent_features(inp, hist, rows)` -> `player_id`, `game_id` + `OPP_FEATURES`
(all Float64), one row per `rows` row in the same order. Every column is computed for every
row; the group columns describe the opponent, so they don't depend on the row's position.
"""

from __future__ import annotations

import polars as pl

from nflengine.features.player_data import PlayerInputs, asof_join, tkey

W = 8  # games in every window
MIN_GAMES = 3  # games before a mean (expectation or allowed) exists
MIN_DB = 100  # dropbacks in the window before a per-dropback rate exists
MIN_PLAYS = 200  # plays in the window before a pass / rush rate exists

# per-game production of one offense's position group (columns of the team-game frame)
OE_STATS = (
    "wr_yds",
    "wr_tgt",
    "te_yds",
    "te_tgt",
    "rb_rec_yds",
    "rb_tgt",
    "rb_rush_yds",
    "rb_car",
    "qb_yds",
    "qb_epa_db",
)

# feature -> team-game column; mean over the defense's last W games
ALLOWED: dict[str, str] = {
    "opp_rec_yds_allowed_wr_l8": "wr_yds",
    "opp_rec_yds_allowed_wr_oe_l8": "wr_yds_oe",
    "opp_targets_allowed_wr_oe_l8": "wr_tgt_oe",
    "opp_rec_yds_allowed_te_l8": "te_yds",
    "opp_rec_yds_allowed_te_oe_l8": "te_yds_oe",
    "opp_targets_allowed_te_oe_l8": "te_tgt_oe",
    "opp_rec_yds_allowed_rb_oe_l8": "rb_rec_yds_oe",
    "opp_targets_allowed_rb_oe_l8": "rb_tgt_oe",
    "opp_rush_yds_allowed_rb_l8": "rb_rush_yds",
    "opp_rush_yds_allowed_rb_oe_l8": "rb_rush_yds_oe",
    "opp_carries_allowed_rb_oe_l8": "rb_car_oe",
    "opp_pass_yds_allowed_qb_l8": "qb_yds",
    "opp_pass_yds_allowed_qb_oe_l8": "qb_yds_oe",
    "opp_pass_epa_allowed_qb_oe_l8": "qb_epa_db_oe",
}
_DEF_SACK = ("opp_def_sack_rate_l8",)
_DEF_LATE = (  # PFR / FTN: a week late
    "opp_def_pressure_rate_l8",
    "opp_def_blitz_rate_l8",
    "opp_def_blitz_rate_ftn_l8",
)
_OFF_PLAIN = (
    "opp_off_sack_rate_l8",
    "opp_off_pass_rate_l8",
    "opp_off_rush_rate_l8",
    "opp_off_plays_per_game_l8",
)
_OFF_PFR = ("opp_off_pressure_rate_l8",)
RATINGS: dict[str, str] = {  # team_ratings column -> feature
    "def_pass_epa": "opp_def_pass_epa",
    "def_rush_epa": "opp_def_rush_epa",
    "off_epa": "opp_off_epa",
    "off_pass_epa": "opp_off_pass_epa",
    "off_rush_epa": "opp_off_rush_epa",
}

OPP_FEATURES: tuple[str, ...] = (
    *ALLOWED,
    *_DEF_SACK,
    *_DEF_LATE,
    *_OFF_PLAIN,
    *_OFF_PFR,
    *RATINGS.values(),
)

# the features built from PFR / FTN (published about a week late: only games before t - 1)
LATE_FEATURES: tuple[str, ...] = (*_DEF_LATE, *_OFF_PFR)


def _div(num: str, den: str, min_den: float, alias: str) -> pl.Expr:
    """`num / den` when `den >= min_den`, else null (null `den` gives null)."""
    return pl.when(pl.col(den) >= min_den).then(pl.col(num) / pl.col(den)).alias(alias)


def _rolled(df: pl.DataFrame, by: str, cols: dict[str, pl.Expr]) -> pl.DataFrame:
    """Per `by` team, rolling sum of each per-game expression over its last `W` games."""
    base = df.select(by, "t", *[e.alias(n) for n, e in cols.items()]).sort(by, "t")
    return base.with_columns(
        [pl.col(n).rolling_sum(W, min_samples=1).over(by) for n in cols],
    )


# ---- the team-game frame ----------------------------------------------------------------------


def _ftn_blitz(inp: PlayerInputs) -> pl.DataFrame:
    """Per (game, offense team): dropbacks FTN charted, and how many faced a blitz."""
    if inp.ftn.is_empty() or inp.plays.is_empty():
        schema = dict.fromkeys(("ftn_db", "ftn_blitz"), pl.Float64)
        return pl.DataFrame(schema={"game_id": pl.String, "team": pl.String, **schema})
    db = inp.plays.filter(
        (pl.col("qb_dropback") == 1)
        & pl.col("passer_id").is_not_null()
        & pl.col("play_type").is_in(["pass", "run"])
        & (pl.col("two_point_attempt").fill_null(0) == 0)
    ).select("game_id", pl.col("play_id").cast(pl.Int64), pl.col("posteam").alias("team"))
    ftn = inp.ftn.select(
        pl.col("nflverse_game_id").alias("game_id"),
        pl.col("nflverse_play_id").cast(pl.Int64).alias("play_id"),
        "n_blitzers",
    )
    return (
        db.join(ftn, on=["game_id", "play_id"], how="inner")
        .group_by("game_id", "team")
        .agg(
            pl.len().cast(pl.Float64).alias("ftn_db"),
            (pl.col("n_blitzers") > 0).sum().cast(pl.Float64).alias("ftn_blitz"),
        )
    )


def _team_games(inp: PlayerInputs, hist: pl.DataFrame) -> pl.DataFrame:
    """One row per (game, offense team) of every played game, with the offense's totals.

    Columns: `game_id`, `team` (offense), `opponent` (its defense), `t`, the offense's `plays`,
    `dropbacks`, `rushes`; its position groups' production (`OE_STATS`) and the `_oe` versions
    (production minus the offense's own mean over its previous `W` games); `sacks` suffered;
    PFR `pressured` / `blitzed` counts and FTN `ftn_db` / `ftn_blitz` on its dropbacks (null
    when the source has no row).
    """
    pg = pl.col("pgroup")
    qb = pl.col("main_qb")
    prod = hist.group_by("game_id", "team").agg(
        pl.col("receiving_yards").filter(pg == "WR").sum().alias("wr_yds"),
        pl.col("targets").filter(pg == "WR").sum().alias("wr_tgt"),
        pl.col("receiving_yards").filter(pg == "TE").sum().alias("te_yds"),
        pl.col("targets").filter(pg == "TE").sum().alias("te_tgt"),
        pl.col("receiving_yards").filter(pg == "RB").sum().alias("rb_rec_yds"),
        pl.col("targets").filter(pg == "RB").sum().alias("rb_tgt"),
        pl.col("rushing_yards").filter(pg == "RB").sum().alias("rb_rush_yds"),
        pl.col("carries").filter(pg == "RB").sum().alias("rb_car"),
        pl.col("passing_yards").filter(qb).sum().alias("qb_yds"),
        pl.col("qb_epa_sum").filter(qb).sum().alias("_qb_epa"),
        pl.col("dropbacks").filter(qb).sum().alias("_qb_db"),
        pl.col("sacks_suffered").sum().alias("sacks"),
    )
    base = (
        hist.select(
            "game_id",
            "team",
            "opponent",
            "t",
            pl.col("team_plays").cast(pl.Float64).alias("plays"),
            pl.col("team_dropbacks").cast(pl.Float64).alias("dropbacks"),
            pl.col("team_rushes").cast(pl.Float64).alias("rushes"),
        )
        .unique(subset=["game_id", "team"], keep="first", maintain_order=True)
        .join(prod, on=["game_id", "team"], how="left")
        .with_columns(
            pl.when(pl.col("_qb_db") > 0)
            .then(pl.col("_qb_epa") / pl.col("_qb_db"))
            .alias("qb_epa_db")
        )
        .drop("_qb_epa", "_qb_db")
    )
    if not inp.pfr_pass.is_empty():
        pfr = inp.pfr_pass.group_by("game_id", "team").agg(
            pl.col("times_pressured").sum().alias("pressured"),
            pl.col("times_blitzed").sum().alias("blitzed"),
        )
        base = base.join(pfr, on=["game_id", "team"], how="left")
    else:
        base = base.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("pressured"),
            pl.lit(None, dtype=pl.Float64).alias("blitzed"),
        )
    base = base.join(_ftn_blitz(inp), on=["game_id", "team"], how="left")
    # the offense's expectation: its own mean over the previous W games (never the current one)
    base = base.sort("team", "t").with_columns(
        [
            pl.col(s).shift(1).rolling_mean(W, min_samples=MIN_GAMES).over("team").alias(f"{s}_exp")
            for s in OE_STATS
        ]
    )
    return base.with_columns([(pl.col(s) - pl.col(f"{s}_exp")).alias(f"{s}_oe") for s in OE_STATS])


# ---- the streams (keyed on `opponent` = the team whose past is being summarised) -------------


def _allowed(tg: pl.DataFrame) -> pl.DataFrame:
    """The defense's mean production allowed (raw and over expectation) over its last games."""
    d = tg.select(pl.col("opponent"), "t", *[pl.col(c) for c in ALLOWED.values()]).sort(
        "opponent", "t"
    )
    return d.select(
        "opponent",
        "t",
        *[
            pl.col(src).rolling_mean(W, min_samples=MIN_GAMES).over("opponent").alias(name)
            for name, src in ALLOWED.items()
        ],
    )


def _def_rates(tg: pl.DataFrame, lag: int) -> tuple[pl.DataFrame, tuple[str, ...]]:
    """Defense side: sacks forced (lag 0), pressure and blitz rate on opposing dropbacks (lag 1)."""
    pres, blz = pl.col("pressured"), pl.col("blitzed")
    if lag == 0:
        r = _rolled(
            tg.with_columns(pl.col("opponent").alias("def")),
            "def",
            {"sacks": pl.col("sacks"), "db": pl.col("dropbacks")},
        )
        f = r.select(
            pl.col("def").alias("opponent"),
            "t",
            _div("sacks", "db", MIN_DB, "opp_def_sack_rate_l8"),
        )
        return f, _DEF_SACK
    r = _rolled(
        tg.with_columns(pl.col("opponent").alias("def")),
        "def",
        {
            "pres": pres,
            "pres_db": pl.when(pres.is_not_null()).then(pl.col("dropbacks")),
            "blz": blz,
            "blz_db": pl.when(blz.is_not_null()).then(pl.col("dropbacks")),
            "ftn_blz": pl.col("ftn_blitz"),
            "ftn_db": pl.col("ftn_db"),
        },
    )
    f = r.select(
        pl.col("def").alias("opponent"),
        "t",
        _div("pres", "pres_db", MIN_DB, "opp_def_pressure_rate_l8"),
        _div("blz", "blz_db", MIN_DB, "opp_def_blitz_rate_l8"),
        _div("ftn_blz", "ftn_db", MIN_DB, "opp_def_blitz_rate_ftn_l8"),
    )
    return f, _DEF_LATE


def _off_rates(tg: pl.DataFrame, lag: int) -> tuple[pl.DataFrame, tuple[str, ...]]:
    """Offense side: sacks allowed, pass / rush rate, plays per game (lag 0); pressure allowed
    per dropback (PFR, lag 1)."""
    if lag == 0:
        r = _rolled(
            tg,
            "team",
            {
                "sacks": pl.col("sacks"),
                "db": pl.col("dropbacks"),
                "rushes": pl.col("rushes"),
                "plays": pl.col("plays"),
                "n": pl.when(pl.col("plays").is_not_null()).then(pl.lit(1.0)),
            },
        )
        f = r.select(
            pl.col("team").alias("opponent"),
            "t",
            _div("sacks", "db", MIN_DB, "opp_off_sack_rate_l8"),
            _div("db", "plays", MIN_PLAYS, "opp_off_pass_rate_l8"),
            _div("rushes", "plays", MIN_PLAYS, "opp_off_rush_rate_l8"),
            _div("plays", "n", MIN_GAMES, "opp_off_plays_per_game_l8"),
        )
        return f, _OFF_PLAIN
    pres = pl.col("pressured")
    r = _rolled(
        tg,
        "team",
        {"pres": pres, "pres_db": pl.when(pres.is_not_null()).then(pl.col("dropbacks"))},
    )
    f = r.select(
        pl.col("team").alias("opponent"),
        "t",
        _div("pres", "pres_db", MIN_DB, "opp_off_pressure_rate_l8"),
    )
    return f, _OFF_PFR


def _ratings(inp: PlayerInputs) -> pl.DataFrame:
    """Rating rows keyed so that `asof_join(lag=0)` matches `t_rating <= t` (D44): the row's own
    (season, week) rating is already built from earlier weeks."""
    tr = inp.team_ratings
    if tr.is_empty():
        return pl.DataFrame(
            schema={
                "opponent": pl.String,
                "t": pl.Int32,
                **dict.fromkeys(RATINGS.values(), pl.Float64),
            }
        )
    return tr.select(
        pl.col("team").alias("opponent"),
        (tkey() - 1).cast(pl.Int32).alias("t"),
        *[pl.col(src).cast(pl.Float64).alias(dst) for src, dst in RATINGS.items()],
    )


# ---- assembly ---------------------------------------------------------------------------------


def opponent_features(inp: PlayerInputs, hist: pl.DataFrame, rows: pl.DataFrame) -> pl.DataFrame:
    """Opponent features for every row of `rows` (`player_id`, `game_id`, `opponent`, `t`, ...).

    `hist` is `player_history(inp)`. Returns `rows.height` rows in `rows`' order: `player_id`,
    `game_id` + `OPP_FEATURES`, all Float64 (null while the opponent has too few games).
    """
    tg = _team_games(inp, hist)
    sack_f, sack_n = _def_rates(tg, 0)
    dpfr_f, dpfr_n = _def_rates(tg, 1)
    oplain_f, oplain_n = _off_rates(tg, 0)
    opfr_f, opfr_n = _off_rates(tg, 1)
    allowed = _allowed(tg)
    # (frame, feature names, lag); the defense's sack rate rides with the allowed means
    streams = [
        (allowed.join(sack_f, on=["opponent", "t"], how="left"), (*ALLOWED, *sack_n), 0),
        (dpfr_f, dpfr_n, 1),
        (oplain_f, oplain_n, 0),
        (opfr_f, opfr_n, 1),
        (_ratings(inp), tuple(RATINGS.values()), 0),
    ]
    spine = rows.select("opponent", "t").unique(maintain_order=True)
    parts = [spine]
    for frame, names, lag in streams:
        parts.append(asof_join(spine, frame, by="opponent", lag=lag).select(names))
    feats = spine.hstack([c for part in parts[1:] for c in part.get_columns()])
    out = rows.select("player_id", "game_id", "opponent", "t").join(
        feats, on=["opponent", "t"], how="left", maintain_order="left"
    )
    return out.select("player_id", "game_id", pl.col(list(OPP_FEATURES)).cast(pl.Float64))
