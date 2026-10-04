"""Efficiency features of the player model (plan P06; documentation/04 -> C. Player model).

How well a player turns his chances into production, from **his own history only** (the
opponent side is `player_opponent.py`). One column per statistic, prefix `eff_`, suffix the
window: `_l8` = his last 8 games of the relevant kind, `_l4` = his last 4 Next Gen Stats
weeks.

Rules (documentation/04 -> Leakage rules):
- A row at time `t` sees only games with `t_game < t` (`asof_join`, lag 0): box score, snap
  counts, play-by-play extras and NGS. PFR and FTN publish about a week late, so they use
  `lag=1` (only `t_game < t - 1`; D44).
- Ratios are rolling sum of numerator / rolling sum of denominator over the window (not a
  mean of per-game ratios) and stay null below a minimum denominator (`MIN_*`).
- The window counts the player's games *of that kind* (games with targets for receiving
  rates, with carries for rushing, with dropbacks for passing, with defensive snaps for
  defense), so a game he sat out or only blocked in doesn't dilute it. It crosses seasons.
- Every column is computed for every row; it is null where the player has no such history
  (a WR has no sack rate, a safety no yards per target). Nothing here reads the row's own
  game, and nothing from research-only data.

Interface: `efficiency_features(inp, hist, rows)` -> `player_id`, `game_id` + `EFF_FEATURES`
(all Float64), one output row per `rows` row in the same order.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from nflengine.features.player_data import PlayerInputs, asof_join, tkey

W = 8  # games in the box-score / PFR / FTN window
WN = 4  # NGS weeks in the NGS window

# minimum rolling denominators (a ratio below them is null)
MIN_TGT = 10  # targets
MIN_REC = 5  # receptions
MIN_CAR = 15  # carries
MIN_DB = 60  # dropbacks / attempts (about two games for a starter)
MIN_SNAPS = 100  # defensive snaps (about two games)
MIN_GAMES = 2  # games, for per-game averages
MIN_TKL = 10  # tackle attempts (tackles + misses)
MIN_TOUCH = 15  # touches (carries + receptions)

_RECEIVING = (
    "eff_yds_per_tgt_l8",
    "eff_catch_rate_l8",
    "eff_epa_per_tgt_l8",
    "eff_yac_per_rec_l8",
    "eff_adot_l8",
)
_RUSHING = ("eff_yds_per_carry_l8", "eff_epa_per_carry_l8")
_PASSING = ("eff_ypa_l8", "eff_epa_per_db_l8", "eff_cpoe_l8", "eff_sack_rate_l8")
_DEFENSE = ("eff_tackles_per_snap_l8", "eff_sacks_tfl_per_game_l8")
_NGS_REC = ("eff_separation_l4", "eff_cushion_l4", "eff_yac_oe_l4")
_NGS_RUSH = ("eff_ryoe_per_att_l4", "eff_rush_efficiency_l4", "eff_stacked_box_pct_l4")
_NGS_PASS = ("eff_time_to_throw_l4", "eff_aggressiveness_l4", "eff_cpoe_ngs_l4")
_PFR_DEF = (
    "eff_pressures_per_snap_l8",
    "eff_hurries_per_snap_l8",
    "eff_qb_hits_per_snap_l8",
    "eff_missed_tackle_pct_l8",
)
_PFR_SKILL = (
    "eff_drop_rate_l8",
    "eff_broken_tackle_rate_l8",
    "eff_yds_after_contact_per_att_l8",
)
_PFR_PASS = ("eff_bad_throw_pct_l8", "eff_pressured_pct_l8")
_FTN_PASS = ("eff_play_action_rate_l8", "eff_blitz_faced_rate_l8")

EFF_FEATURES: tuple[str, ...] = (
    *_RECEIVING,
    *_NGS_REC,
    *_PFR_SKILL,
    *_RUSHING,
    *_NGS_RUSH,
    *_PASSING,
    *_NGS_PASS,
    *_PFR_PASS,
    *_FTN_PASS,
    *_DEFENSE,
    *_PFR_DEF,
)

# the features built from PFR / FTN (published about a week late: only games before t - 1)
LATE_FEATURES: tuple[str, ...] = (*_PFR_SKILL, *_PFR_PASS, *_FTN_PASS, *_PFR_DEF)


@dataclass(frozen=True)
class _Stream:
    """Rolling features of one source: `player_id`, `t` (the game's time) + `names`."""

    frame: pl.DataFrame
    names: tuple[str, ...]
    lag: int = 0


def _empty(names: tuple[str, ...]) -> _Stream:
    schema = {"player_id": pl.String, "t": pl.Int32, **dict.fromkeys(names, pl.Float64)}
    return _Stream(pl.DataFrame(schema=schema), names)


def _div(num: str, den: str, min_den: float, alias: str) -> pl.Expr:
    """`num / den` when `den >= min_den`, else null (null `den` gives null)."""
    return pl.when(pl.col(den) >= min_den).then(pl.col(num) / pl.col(den)).alias(alias)


def _rolled(df: pl.DataFrame, cols: dict[str, pl.Expr], *, mean: bool = False) -> pl.DataFrame:
    """Per player, rolling sum (or mean) of each per-game expression over his last games.

    `df` holds one row per player-game; the window is `W` rows (`WN` for means), counting
    only the rows of `df`. Nulls are skipped, so a column null in some games sums the rest.
    """
    base = df.select("player_id", "t", *[e.alias(n) for n, e in cols.items()])
    base = base.sort("player_id", "t")
    if mean:
        roll = [pl.col(n).rolling_mean(WN, min_samples=1).over("player_id") for n in cols]
    else:
        roll = [pl.col(n).rolling_sum(W, min_samples=1).over("player_id") for n in cols]
    return base.with_columns(roll)


def _when(cond: pl.Expr, value: pl.Expr) -> pl.Expr:
    """`value` where `cond`, else null (a denominator that follows a nullable numerator)."""
    return pl.when(cond).then(value)


# ---- box score + play-by-play extras (lag 0) -------------------------------------------------


def _receiving(h: pl.DataFrame) -> _Stream:
    epa = pl.col("receiving_epa")
    r = _rolled(
        h.filter(pl.col("targets") > 0),
        {
            "yds": pl.col("receiving_yards"),
            "tgt": pl.col("targets"),
            "rec": pl.col("receptions"),
            "yac": pl.col("receiving_yards_after_catch"),
            "air": pl.col("receiving_air_yards"),
            "epa": epa,
            "tgt_epa": _when(epa.is_not_null(), pl.col("targets")),
        },
    )
    f = r.select(
        "player_id",
        "t",
        _div("yds", "tgt", MIN_TGT, "eff_yds_per_tgt_l8"),
        _div("rec", "tgt", MIN_TGT, "eff_catch_rate_l8"),
        _div("epa", "tgt_epa", MIN_TGT, "eff_epa_per_tgt_l8"),
        _div("yac", "rec", MIN_REC, "eff_yac_per_rec_l8"),
        _div("air", "tgt", MIN_TGT, "eff_adot_l8"),
    )
    return _Stream(f, _RECEIVING)


def _rushing(h: pl.DataFrame) -> _Stream:
    epa = pl.col("rushing_epa")
    r = _rolled(
        h.filter(pl.col("carries") > 0),
        {
            "yds": pl.col("rushing_yards"),
            "car": pl.col("carries"),
            "epa": epa,
            "car_epa": _when(epa.is_not_null(), pl.col("carries")),
        },
    )
    f = r.select(
        "player_id",
        "t",
        _div("yds", "car", MIN_CAR, "eff_yds_per_carry_l8"),
        _div("epa", "car_epa", MIN_CAR, "eff_epa_per_carry_l8"),
    )
    return _Stream(f, _RUSHING)


def _passing(h: pl.DataFrame) -> _Stream:
    epa, cpoe = pl.col("qb_epa_sum"), pl.col("passing_cpoe")
    r = _rolled(
        h.filter(pl.col("dropbacks") > 0),
        {
            "yds": pl.col("passing_yards"),
            "att": pl.col("attempts"),
            "db": pl.col("dropbacks"),
            "sacks": pl.col("sacks_suffered"),
            "epa": epa,
            "db_epa": _when(epa.is_not_null(), pl.col("dropbacks")),
            "cpoe_w": cpoe * pl.col("attempts"),  # CPOE is a per-attempt mean: weight it
            "cpoe_att": _when(cpoe.is_not_null(), pl.col("attempts")),
        },
    )
    f = r.select(
        "player_id",
        "t",
        _div("yds", "att", MIN_DB, "eff_ypa_l8"),
        _div("epa", "db_epa", MIN_DB, "eff_epa_per_db_l8"),
        _div("cpoe_w", "cpoe_att", MIN_DB, "eff_cpoe_l8"),
        _div("sacks", "db", MIN_DB, "eff_sack_rate_l8"),
    )
    return _Stream(f, _PASSING)


def _defense(h: pl.DataFrame) -> _Stream:
    r = _rolled(
        h.filter(pl.col("played_def") & (pl.col("defense_snaps") > 0)),
        {
            "snaps": pl.col("defense_snaps"),
            "tkl": pl.col("tackles"),
            "sk_tfl": pl.col("def_sacks") + pl.col("def_tackles_for_loss"),
            "n": pl.lit(1.0),
        },
    )
    f = r.select(
        "player_id",
        "t",
        _div("tkl", "snaps", MIN_SNAPS, "eff_tackles_per_snap_l8"),
        _div("sk_tfl", "n", MIN_GAMES, "eff_sacks_tfl_per_game_l8"),
    )
    return _Stream(f, _DEFENSE)


# ---- Next Gen Stats (weekly rows, lag 0) -----------------------------------------------------


def _ngs(df: pl.DataFrame, cols: dict[str, str]) -> _Stream:
    """Mean of each NGS column over the player's last `WN` weeks with an NGS row."""
    names = tuple(cols.values())
    if df.is_empty():
        return _empty(names)
    g = (
        df.filter(pl.col("player_gsis_id").is_not_null())
        .group_by(pl.col("player_gsis_id").alias("player_id"), tkey().alias("t"))
        .agg([pl.col(src).mean().alias(dst) for src, dst in cols.items()])
    )
    return _Stream(
        g.sort("player_id", "t").with_columns(
            [pl.col(n).rolling_mean(WN, min_samples=1).over("player_id") for n in names]
        ),
        names,
    )


# ---- PFR advanced stats (a week late: lag 1) -------------------------------------------------


def _pfr_defense(h: pl.DataFrame) -> _Stream:
    # `pressures` is null until PFR has published the team-game; those games don't count
    r = _rolled(
        h.filter(
            pl.col("played_def") & (pl.col("defense_snaps") > 0) & pl.col("pressures").is_not_null()
        ),
        {
            "snaps": pl.col("defense_snaps"),
            "pres": pl.col("pressures"),
            "hur": pl.col("pfr_hurries"),
            "hit": pl.col("pfr_qb_hits"),
            "miss": pl.col("pfr_missed_tackles"),
            "att": pl.col("pfr_missed_tackles") + pl.col("pfr_tackles"),
        },
    )
    f = r.select(
        "player_id",
        "t",
        _div("pres", "snaps", MIN_SNAPS, "eff_pressures_per_snap_l8"),
        _div("hur", "snaps", MIN_SNAPS, "eff_hurries_per_snap_l8"),
        _div("hit", "snaps", MIN_SNAPS, "eff_qb_hits_per_snap_l8"),
        _div("miss", "att", MIN_TKL, "eff_missed_tackle_pct_l8"),
    )
    return _Stream(f, _PFR_DEF, lag=1)


def _pfr_rows(df: pl.DataFrame, cols: dict[str, str]) -> pl.DataFrame:
    """One PFR row per (player, game) with `cols` (source -> alias); rows without a gsis id go."""
    if df.is_empty():
        return pl.DataFrame(schema={"player_id": pl.String, "game_id": pl.String})
    return (
        df.filter(pl.col("gsis_id").is_not_null())
        .unique(subset=["gsis_id", "game_id"], keep="first", maintain_order=True)
        .select(
            pl.col("gsis_id").alias("player_id"),
            "game_id",
            *[pl.col(s).alias(a) for s, a in cols.items()],
        )
    )


def _pfr_skill(inp: PlayerInputs, h: pl.DataFrame) -> _Stream:
    """Drops, broken tackles and yards after contact from `pfr_rec` / `pfr_rush`."""
    rec = _pfr_rows(inp.pfr_rec, {"receiving_drop": "drops", "receiving_broken_tackles": "bt_rec"})
    rush = _pfr_rows(
        inp.pfr_rush,
        {
            "rushing_broken_tackles": "bt_rush",
            "rushing_yards_after_contact": "ayc",
            "carries": "pfr_car",
        },
    )
    if rec.width == 2 and rush.width == 2:
        return _empty(_PFR_SKILL)
    base = h.select("player_id", "game_id", "t", "targets", "receptions")
    for part in (rec, rush):
        if part.width > 2:
            base = base.join(part, on=["player_id", "game_id"], how="left")
    for c in ("drops", "bt_rec", "bt_rush", "ayc", "pfr_car"):
        if c not in base.columns:
            base = base.with_columns(pl.lit(None, dtype=pl.Float64).alias(c))
    drops, bt_rec, bt_rush, ayc = (pl.col(c) for c in ("drops", "bt_rec", "bt_rush", "ayc"))
    base = base.filter(
        drops.is_not_null() | bt_rec.is_not_null() | bt_rush.is_not_null() | ayc.is_not_null()
    )
    r = _rolled(
        base,
        {
            "drops": drops,
            "drop_tgt": _when(drops.is_not_null(), pl.col("targets")),
            "bt": pl.coalesce(bt_rush, pl.lit(0.0)) + pl.coalesce(bt_rec, pl.lit(0.0)),
            # touches that a broken-tackle count exists for: rushes with a PFR rush row,
            # receptions with a PFR receiving row
            "touch": _when(bt_rush.is_not_null(), pl.col("pfr_car")).fill_null(0.0)
            + _when(bt_rec.is_not_null(), pl.col("receptions")).fill_null(0.0),
            "ayc": ayc,
            "ayc_car": _when(ayc.is_not_null(), pl.col("pfr_car")),
        },
    )
    f = r.select(
        "player_id",
        "t",
        _div("drops", "drop_tgt", MIN_TGT, "eff_drop_rate_l8"),
        _div("bt", "touch", MIN_TOUCH, "eff_broken_tackle_rate_l8"),
        _div("ayc", "ayc_car", MIN_CAR, "eff_yds_after_contact_per_att_l8"),
    )
    return _Stream(f, _PFR_SKILL, lag=1)


def _pfr_passing(inp: PlayerInputs, h: pl.DataFrame) -> _Stream:
    pp = _pfr_rows(inp.pfr_pass, {"passing_bad_throws": "bad", "times_pressured": "pressured"})
    if pp.width == 2:
        return _empty(_PFR_PASS)
    b = pp.join(
        h.select("player_id", "game_id", "t", "attempts", "dropbacks"),
        on=["player_id", "game_id"],
        how="inner",
    )
    bad, pres = pl.col("bad"), pl.col("pressured")
    r = _rolled(
        b,
        {
            "bad": bad,
            "att": _when(bad.is_not_null(), pl.col("attempts")),
            "pres": pres,
            "db": _when(pres.is_not_null(), pl.col("dropbacks")),
        },
    )
    f = r.select(
        "player_id",
        "t",
        _div("bad", "att", MIN_DB, "eff_bad_throw_pct_l8"),
        _div("pres", "db", MIN_DB, "eff_pressured_pct_l8"),
    )
    return _Stream(f, _PFR_PASS, lag=1)


# ---- FTN charting (a week late: lag 1) -------------------------------------------------------


def _ftn_passing(inp: PlayerInputs, h: pl.DataFrame) -> _Stream:
    """Play-action and blitz-faced rates on the QB's dropbacks (FTN, 2022+)."""
    if inp.ftn.is_empty() or inp.plays.is_empty():
        return _empty(_FTN_PASS)
    db = inp.plays.filter(
        (pl.col("qb_dropback") == 1)
        & pl.col("passer_id").is_not_null()
        & pl.col("play_type").is_in(["pass", "run"])
        & (pl.col("two_point_attempt").fill_null(0) == 0)
    ).select("game_id", pl.col("play_id").cast(pl.Int64), pl.col("passer_id").alias("player_id"))
    ftn = inp.ftn.select(
        pl.col("nflverse_game_id").alias("game_id"),
        pl.col("nflverse_play_id").cast(pl.Int64).alias("play_id"),
        "is_play_action",
        "n_blitzers",
    )
    g = (
        db.join(ftn, on=["game_id", "play_id"], how="inner")
        .group_by("player_id", "game_id")
        .agg(
            pl.len().cast(pl.Float64).alias("db"),
            pl.col("is_play_action").sum().cast(pl.Float64).alias("pa"),
            (pl.col("n_blitzers") > 0).sum().cast(pl.Float64).alias("blitz"),
        )
        .join(h.select("player_id", "game_id", "t"), on=["player_id", "game_id"], how="inner")
    )
    r = _rolled(g, {c: pl.col(c) for c in ("db", "pa", "blitz")})
    f = r.select(
        "player_id",
        "t",
        _div("pa", "db", MIN_DB, "eff_play_action_rate_l8"),
        _div("blitz", "db", MIN_DB, "eff_blitz_faced_rate_l8"),
    )
    return _Stream(f, _FTN_PASS, lag=1)


# ---- assembly ---------------------------------------------------------------------------------


def efficiency_features(inp: PlayerInputs, hist: pl.DataFrame, rows: pl.DataFrame) -> pl.DataFrame:
    """Efficiency features for every row of `rows` (`player_id`, `game_id`, `t`, ...).

    `hist` is `player_history(inp)`. Returns `rows.height` rows in `rows`' order: `player_id`,
    `game_id` + `EFF_FEATURES`, all Float64 (null where the player has no such history).
    """
    streams = [
        _receiving(hist),
        _ngs(
            inp.ngs_receiving,
            {
                "avg_separation": "eff_separation_l4",
                "avg_cushion": "eff_cushion_l4",
                "avg_yac_above_expectation": "eff_yac_oe_l4",
            },
        ),
        _pfr_skill(inp, hist),
        _rushing(hist),
        _ngs(
            inp.ngs_rushing,
            {
                "rush_yards_over_expected_per_att": "eff_ryoe_per_att_l4",
                "efficiency": "eff_rush_efficiency_l4",
                "percent_attempts_gte_eight_defenders": "eff_stacked_box_pct_l4",
            },
        ),
        _passing(hist),
        _ngs(
            inp.ngs_passing,
            {
                "avg_time_to_throw": "eff_time_to_throw_l4",
                "aggressiveness": "eff_aggressiveness_l4",
                "completion_percentage_above_expectation": "eff_cpoe_ngs_l4",
            },
        ),
        _pfr_passing(inp, hist),
        _ftn_passing(inp, hist),
        _defense(hist),
        _pfr_defense(hist),
    ]
    key = rows.select("player_id", "t")
    parts = [rows.select("player_id", "game_id")]
    for s in streams:
        if s.frame.is_empty():
            parts.append(
                pl.DataFrame(
                    {n: [None] * rows.height for n in s.names},
                    schema=dict.fromkeys(s.names, pl.Float64),
                )
            )
        else:
            parts.append(asof_join(key, s.frame, by="player_id", lag=s.lag).select(s.names))
    out = parts[0].hstack([c for part in parts[1:] for c in part.get_columns()])
    return out.select("player_id", "game_id", pl.col(list(EFF_FEATURES)).cast(pl.Float64))
