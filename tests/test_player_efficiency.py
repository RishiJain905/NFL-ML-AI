"""Tests for `features/player_efficiency.py` (P06): leakage, lag, windows, ratios.

Also holds the synthetic league (`make_world`) and the scrambling helper (`scrambled`) that
`test_player_opponent.py` reuses: 4 teams, 2 seasons x 8 weeks, 8 players per team, every
curated source the player features read (box score, snaps, plays, PFR, NGS, FTN, team
ratings). The last two weeks of the second season are *upcoming*: schedule rows only.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import replace

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from nflengine.features.player_data import (
    GAME_COLS,
    PG_COLS,
    PLAY_COLS,
    PlayerInputs,
    player_history,
    tkey,
)
from nflengine.features.player_efficiency import (
    EFF_FEATURES,
    LATE_FEATURES,
    efficiency_features,
)

TEAMS = ["ARI", "ATL", "BAL", "BUF"]
SEASONS = (2021, 2022)
WEEKS = 8
UNPLAYED_FROM = (2022, 7)  # this week on: schedule only (the live week and one beyond)
PAIRINGS = [((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2))]
# (id suffix, roster position, box-score position group)
ROSTER = [
    ("QB", "QB", "QB"),
    ("RB", "RB", "RB"),
    ("WR1", "WR", "WR"),
    ("WR2", "WR", "WR"),
    ("TE", "TE", "TE"),
    ("DL", "DE", "DL"),
    ("LB", "OLB", "LB"),
    ("S", "FS", "DB"),
]
PGROUP = {"DB": "S"}

ALL_FRAMES = (
    "games",
    "player_games",
    "snaps",
    "plays",
    "pfr_def",
    "pfr_pass",
    "pfr_rush",
    "pfr_rec",
    "ngs_passing",
    "ngs_receiving",
    "ngs_rushing",
    "ftn",
    "team_ratings",
)
LATE_FRAMES = ("pfr_def", "pfr_pass", "pfr_rush", "pfr_rec", "ftn")
BOX_FRAMES = ("player_games", "snaps", "plays")
KEYS = {"season", "week", "play_id", "nflverse_play_id"}  # never scrambled


def tk(season: int, week: int) -> int:
    return (season - 2000) * 22 + week


# ---- the synthetic league ---------------------------------------------------------------------

_STR_PG = {
    "player_id",
    "player_display_name",
    "position",
    "position_group",
    "game_id",
    "team",
    "opponent_team",
}
_STR_PLAY = {
    "game_id",
    "season_type",
    "posteam",
    "defteam",
    "play_type",
    "passer_id",
    "rusher_id",
    "receiver_id",
    "passer_player_id",
    "receiver_player_id",
    "rusher_player_id",
}


def _schema(cols: Iterable[str], strings: set[str]) -> dict[str, pl.DataType]:
    return {
        c: pl.String if c in strings else pl.Int32 if c in ("season", "week") else pl.Float64
        for c in cols
    }


def _n(rng: np.random.Generator, lo: int, hi: int) -> float:
    return float(rng.integers(lo, hi))


def _box(kind: str, rng: np.random.Generator) -> dict:
    """Box-score stats for one player-game; fields that don't apply stay absent (null)."""

    def n(lo: int, hi: int) -> float:
        return _n(rng, lo, hi)

    if kind == "QB":
        att = n(25, 42)
        return {
            "attempts": att,
            "completions": round(att * 0.65),
            "passing_yards": n(150, 350),
            "passing_tds": n(0, 4),
            "passing_interceptions": n(0, 3),
            "sacks_suffered": n(0, 5),
            "passing_air_yards": n(150, 350),
            "passing_epa": float(rng.normal(3, 5)),
            "passing_cpoe": float(rng.normal(0, 6)),
            "carries": n(1, 5),
            "rushing_yards": n(0, 40),
            "rushing_epa": float(rng.normal(0, 1)),
        }
    if kind == "RB":
        tg = n(2, 8)
        return {
            "carries": n(8, 22),
            "rushing_yards": n(20, 110),
            "rushing_tds": n(0, 2),
            "rushing_epa": float(rng.normal(-1, 3)),
            "targets": tg,
            "receptions": min(tg, n(1, 6)),
            "receiving_yards": n(0, 50),
            "receiving_air_yards": n(0, 15),
            "receiving_yards_after_catch": n(0, 40),
            "receiving_epa": float(rng.normal(0, 2)),
        }
    if kind in ("WR", "TE"):
        tg = n(4, 13)
        return {
            "targets": tg,
            "receptions": min(tg, n(2, 9)),
            "receiving_yards": n(20, 140),
            "receiving_tds": n(0, 2),
            "receiving_air_yards": n(20, 160),
            "receiving_yards_after_catch": n(5, 60),
            "receiving_epa": float(rng.normal(2, 4)),
        }
    return {
        "def_tackles_solo": n(0, 8),
        "def_tackle_assists": n(0, 5),
        "def_tackles_for_loss": n(0, 3),
        "def_sacks": 0.5 * n(0, 3),
        "def_qb_hits": n(0, 4),
        "def_interceptions": n(0, 2),
        "def_pass_defended": n(0, 3),
    }


def make_world(
    seed: int = 11,
) -> tuple[PlayerInputs, pl.DataFrame, pl.DataFrame]:
    """(inputs, history, rows): the league above; `rows` has one row per player per game,
    played or upcoming (`player_id`, `game_id`, `season`, `week`, `t`, `team`, `opponent`,
    `home`, `pgroup`)."""
    rng = np.random.default_rng(seed)
    games, pg, snaps, plays, pdef, ppass, prush, prec, nrec, nrush, npass = ([] for _ in range(11))
    ftn, rows, ratings = [], [], []
    for season in SEASONS:
        for week in range(1, WEEKS + 1):
            played = (season, week) < UNPLAYED_FROM
            if (season, week) <= UNPLAYED_FROM:  # ratings exist up to the first unfinished week
                ratings += [
                    {
                        "season": season,
                        "week": week,
                        "team": t,
                        "off_epa": float(rng.normal(0, 0.1)),
                        "def_epa": float(rng.normal(0, 0.1)),
                        "off_pass_epa": float(rng.normal(0, 0.1)),
                        "def_pass_epa": float(rng.normal(0, 0.1)),
                        "off_rush_epa": float(rng.normal(0, 0.1)),
                        "def_rush_epa": float(rng.normal(0, 0.1)),
                    }
                    for t in TEAMS
                ]
            for gi, (a, b) in enumerate(PAIRINGS[(week - 1) % 3]):
                home, away = (TEAMS[a], TEAMS[b]) if week % 2 else (TEAMS[b], TEAMS[a])
                gid = f"{season}_{week:02d}_{away}_{home}"
                kick = dt.datetime(season, 9, 10, 17, tzinfo=dt.UTC) + dt.timedelta(
                    days=7 * (week - 1), hours=3 * gi
                )
                games.append(
                    {
                        "game_id": gid,
                        "season": season,
                        "week": week,
                        "game_type": "REG",
                        "kickoff_utc": kick,
                        "home_team": home,
                        "away_team": away,
                        "completed": played,
                        "neutral_site": False,
                        "div_game": False,
                        "roof": "outdoors",
                    }
                )
                for team, opp, base in ((home, away, 0), (away, home, 100)):
                    ids = {suf: f"{team}_{suf}" for suf, _, _ in ROSTER}
                    for suf, _pos, grp in ROSTER:
                        rows.append(
                            {
                                "player_id": ids[suf],
                                "game_id": gid,
                                "season": season,
                                "week": week,
                                "team": team,
                                "opponent": opp,
                                "home": team == home,
                                "pgroup": PGROUP.get(grp, grp),
                            }
                        )
                    if not played:
                        continue
                    skill = [ids["RB"], ids["WR1"], ids["WR2"], ids["TE"]]
                    for i in range(44):
                        drop = i < 24
                        sack = bool(drop and rng.random() < 0.08)
                        epa = float(rng.normal(0, 1))
                        plays.append(
                            {
                                "game_id": gid,
                                "play_id": float(base + i + 1),
                                "season": season,
                                "week": week,
                                "season_type": "REG",
                                "posteam": team,
                                "defteam": opp,
                                "play_type": "pass" if drop else "run",
                                "pass": 1.0 if drop else 0.0,
                                "rush": 0.0 if drop else 1.0,
                                "qb_dropback": 1.0 if drop else 0.0,
                                "qb_scramble": 0.0,
                                "sack": float(sack),
                                "two_point_attempt": 0.0,
                                "passer_id": ids["QB"] if drop else None,
                                "rusher_player_id": None if drop else ids["RB"],
                                "receiver_player_id": (
                                    str(rng.choice(skill)) if drop and not sack else None
                                ),
                                "qb_epa": epa if drop else None,
                                "epa": epa,
                                "yardline_100": float(rng.integers(3, 95)),
                                "pass_oe": float(rng.normal(0, 10)),
                            }
                        )
                        if drop:
                            ftn.append(
                                {
                                    "nflverse_game_id": gid,
                                    "nflverse_play_id": base + i + 1,
                                    "season": season,
                                    "week": week,
                                    "is_play_action": bool(rng.random() < 0.25),
                                    "n_blitzers": int(rng.choice([0, 0, 1, 2])),
                                }
                            )
                    for suf, pos, grp in ROSTER:
                        pid = ids[suf]
                        kind = {"WR1": "WR", "WR2": "WR"}.get(suf, suf)
                        offense = grp in ("QB", "RB", "WR", "TE")
                        pg.append(
                            {
                                "player_id": pid,
                                "player_display_name": pid,
                                "position": pos,
                                "position_group": grp,
                                "season": season,
                                "week": week,
                                "game_id": gid,
                                "team": team,
                                "opponent_team": opp,
                                **_box(kind if offense else "DEF", rng),
                            }
                        )
                        snaps.append(
                            {
                                "gsis_id": pid,
                                "game_id": gid,
                                "season": season,
                                "week": week,
                                "team": team,
                                "position": pos,
                                "player": pid,
                                "offense_snaps": float(rng.integers(40, 70)) if offense else None,
                                "offense_pct": 0.8 if offense else None,
                                "defense_snaps": None if offense else float(rng.integers(40, 65)),
                                "defense_pct": None if offense else 0.8,
                                "st_snaps": 0.0,
                            }
                        )
                        key = {"gsis_id": pid, "game_id": gid, "season": season, "week": week}

                        def n(lo: int, hi: int) -> float:
                            return _n(rng, lo, hi)

                        if not offense:
                            pdef.append(
                                {
                                    **key,
                                    "team": team,
                                    "def_pressures": n(0, 6),
                                    "def_times_hurried": n(0, 4),
                                    "def_times_hitqb": n(0, 3),
                                    "def_times_blitzed": n(0, 3),
                                    "def_missed_tackles": n(0, 3),
                                    "def_tackles_combined": n(1, 9),
                                    "def_targets": n(0, 4),
                                    "def_completions_allowed": n(0, 3),
                                    "def_yards_allowed": n(0, 40),
                                }
                            )
                        if suf == "QB":
                            ppass.append(
                                {
                                    **key,
                                    "team": team,
                                    "opponent": opp,
                                    "passing_bad_throws": n(2, 9),
                                    "times_pressured": n(5, 16),
                                    "times_blitzed": n(4, 12),
                                }
                            )
                            npass.append(
                                {
                                    "season": season,
                                    "week": week,
                                    "player_gsis_id": pid,
                                    "avg_time_to_throw": float(rng.normal(2.7, 0.2)),
                                    "aggressiveness": float(rng.normal(17, 3)),
                                    "completion_percentage_above_expectation": float(
                                        rng.normal(0, 4)
                                    ),
                                }
                            )
                        if suf == "RB":
                            prush.append(
                                {
                                    **key,
                                    "team": team,
                                    "carries": pg[-1]["carries"],
                                    "rushing_yards_after_contact": n(10, 60),
                                    "rushing_broken_tackles": n(0, 3),
                                }
                            )
                            nrush.append(
                                {
                                    "season": season,
                                    "week": week,
                                    "player_gsis_id": pid,
                                    "rush_yards_over_expected_per_att": float(rng.normal(0, 1)),
                                    "efficiency": float(rng.normal(4, 0.5)),
                                    "percent_attempts_gte_eight_defenders": float(
                                        rng.uniform(5, 40)
                                    ),
                                }
                            )
                        if suf in ("RB", "WR1", "WR2", "TE"):
                            prec.append(
                                {
                                    **key,
                                    "team": team,
                                    "receiving_drop": n(0, 2),
                                    "receiving_broken_tackles": n(0, 2),
                                }
                            )
                        if suf in ("WR1", "WR2", "TE"):
                            nrec.append(
                                {
                                    "season": season,
                                    "week": week,
                                    "player_gsis_id": pid,
                                    "avg_separation": float(rng.normal(3, 0.5)),
                                    "avg_cushion": float(rng.normal(6, 1)),
                                    "avg_yac_above_expectation": float(rng.normal(0, 1)),
                                }
                            )
    key_i = {"season": pl.Int32, "week": pl.Int32}
    ngs_cols = {"player_gsis_id": pl.String}

    def frame(data: list[dict], schema: dict) -> pl.DataFrame:
        return pl.DataFrame(data, schema=schema)

    pdef_s = {"gsis_id": pl.String, "game_id": pl.String, "team": pl.String, **key_i}
    inp = PlayerInputs(
        games=frame(
            games,
            {
                **dict.fromkeys(GAME_COLS, pl.String),
                **key_i,
                "kickoff_utc": pl.Datetime("us", "UTC"),
                "completed": pl.Boolean,
                "neutral_site": pl.Boolean,
                "div_game": pl.Boolean,
            },
        ),
        player_games=frame(pg, _schema(PG_COLS, _STR_PG)),
        snaps=frame(
            snaps,
            {
                **dict.fromkeys(("gsis_id", "game_id", "team", "position", "player"), pl.String),
                **key_i,
                **dict.fromkeys(
                    ("offense_snaps", "offense_pct", "defense_snaps", "defense_pct", "st_snaps"),
                    pl.Float64,
                ),
            },
        ),
        plays=frame(plays, _schema(PLAY_COLS, _STR_PLAY)),
        pfr_def=frame(
            pdef,
            {
                **pdef_s,
                **dict.fromkeys(
                    (
                        "def_pressures",
                        "def_times_hurried",
                        "def_times_hitqb",
                        "def_times_blitzed",
                        "def_missed_tackles",
                        "def_tackles_combined",
                        "def_targets",
                        "def_completions_allowed",
                        "def_yards_allowed",
                    ),
                    pl.Float64,
                ),
            },
        ),
        pfr_pass=frame(
            ppass,
            {
                **pdef_s,
                "opponent": pl.String,
                **dict.fromkeys(
                    ("passing_bad_throws", "times_pressured", "times_blitzed"), pl.Float64
                ),
            },
        ),
        pfr_rush=frame(
            prush,
            {
                **pdef_s,
                **dict.fromkeys(
                    ("carries", "rushing_yards_after_contact", "rushing_broken_tackles"),
                    pl.Float64,
                ),
            },
        ),
        pfr_rec=frame(
            prec,
            {
                **pdef_s,
                **dict.fromkeys(("receiving_drop", "receiving_broken_tackles"), pl.Float64),
            },
        ),
        ngs_passing=frame(
            npass,
            {
                **key_i,
                **ngs_cols,
                **dict.fromkeys(
                    (
                        "avg_time_to_throw",
                        "aggressiveness",
                        "completion_percentage_above_expectation",
                    ),
                    pl.Float64,
                ),
            },
        ),
        ngs_receiving=frame(
            nrec,
            {
                **key_i,
                **ngs_cols,
                **dict.fromkeys(
                    ("avg_separation", "avg_cushion", "avg_yac_above_expectation"), pl.Float64
                ),
            },
        ),
        ngs_rushing=frame(
            nrush,
            {
                **key_i,
                **ngs_cols,
                **dict.fromkeys(
                    (
                        "rush_yards_over_expected_per_att",
                        "efficiency",
                        "percent_attempts_gte_eight_defenders",
                    ),
                    pl.Float64,
                ),
            },
        ),
        ftn=frame(
            ftn,
            {
                "nflverse_game_id": pl.String,
                "nflverse_play_id": pl.Int32,
                **key_i,
                "is_play_action": pl.Boolean,
                "n_blitzers": pl.Int32,
            },
        ),
        injuries=pl.DataFrame(),
        rosters=pl.DataFrame(),
        team_ratings=frame(
            ratings,
            {
                "team": pl.String,
                **key_i,
                **dict.fromkeys(
                    (
                        "off_epa",
                        "def_epa",
                        "off_pass_epa",
                        "def_pass_epa",
                        "off_rush_epa",
                        "def_rush_epa",
                    ),
                    pl.Float64,
                ),
            },
        ),
        game_features=pl.DataFrame(),
    )
    rows_df = frame(
        rows,
        {
            "player_id": pl.String,
            "game_id": pl.String,
            **key_i,
            "team": pl.String,
            "opponent": pl.String,
            "home": pl.Boolean,
            "pgroup": pl.String,
        },
    ).with_columns(tkey().alias("t"))
    return inp, player_history(inp), rows_df


def _scramble_frame(df: pl.DataFrame, mask_expr: pl.Expr, rng: np.random.Generator) -> pl.DataFrame:
    """Replace every non-key numeric / boolean value in the masked rows with noise (nulls stay)."""
    if df.is_empty():
        return df
    mask = pl.lit(df.select(mask_expr.alias("m")).to_series())
    n = df.height
    exprs = []
    for c, dtype in df.schema.items():
        if c in KEYS:
            continue
        if dtype.is_float():
            rnd = pl.Series(rng.uniform(1, 200, n))
        elif dtype.is_integer():
            rnd = pl.Series(rng.integers(1, 200, n)).cast(dtype)
        elif dtype == pl.Boolean:
            rnd = pl.Series(rng.random(n) < 0.5)
        else:
            continue
        exprs.append(
            pl.when(mask & pl.col(c).is_not_null()).then(pl.lit(rnd)).otherwise(pl.col(c)).alias(c)
        )
    return df.with_columns(exprs)


def scrambled(
    inp: PlayerInputs,
    lo: int,
    hi: int | None = None,
    frames: Iterable[str] = ALL_FRAMES,
    seed: int = 5,
) -> PlayerInputs:
    """Copy of `inp` with the numeric values of games at `lo <= t <= hi` (no `hi`: all later
    games) replaced by noise, in `frames`. Team ratings rows are as-of rows (D44): a rating row
    at week `lo` is legitimate for a game at `lo`, so those are scrambled from `lo + 1`."""
    rng = np.random.default_rng(seed)
    out = {}
    for name in frames:
        shift = 1 if name == "team_ratings" else 0
        mask = tkey() >= lo + shift
        if hi is not None:
            mask = mask & (tkey() <= hi)
        out[name] = _scramble_frame(getattr(inp, name), mask, rng)
    return replace(inp, **out)


def assert_same(a: pl.DataFrame, b: pl.DataFrame, cols: Iterable[str] | None = None) -> None:
    cols = list(cols) if cols is not None else a.columns
    assert_frame_equal(
        a.select(cols), b.select(cols), check_exact=False, rel_tol=1e-9, abs_tol=1e-9
    )


def changed_columns(a: pl.DataFrame, b: pl.DataFrame, cols: Iterable[str]) -> list[str]:
    return [c for c in cols if not a[c].equals(b[c])]


@pytest.fixture(scope="module")
def world():
    return make_world()


# ---- shape ------------------------------------------------------------------------------------


def test_columns_dtypes_and_order(world):
    inp, hist, rows = world
    out = efficiency_features(inp, hist, rows)
    assert out.columns == ["player_id", "game_id", *EFF_FEATURES]
    assert out.height == rows.height
    assert out["player_id"].equals(rows["player_id"])
    assert out["game_id"].equals(rows["game_id"])
    assert all(out.schema[c] == pl.Float64 for c in EFF_FEATURES)
    assert len(set(EFF_FEATURES)) == len(EFF_FEATURES)
    assert all(f.startswith("eff_") for f in EFF_FEATURES)
    assert set(LATE_FEATURES) <= set(EFF_FEATURES)


def test_row_order_is_kept_for_shuffled_rows(world):
    inp, hist, rows = world
    base = efficiency_features(inp, hist, rows)
    shuffled = rows.sample(fraction=1.0, shuffle=True, seed=3)
    out = efficiency_features(inp, hist, shuffled)
    assert out["player_id"].equals(shuffled["player_id"])
    merged = out.join(base, on=["player_id", "game_id"], suffix="_b")
    for c in EFF_FEATURES:
        assert merged[c].equals(merged[f"{c}_b"]), c


def test_features_are_populated_where_they_should_be(world):
    inp, hist, rows = world
    out = efficiency_features(inp, hist, rows).join(
        rows.select("player_id", "game_id", "pgroup", "t"), on=["player_id", "game_id"]
    )
    late = out.filter(pl.col("t") == tk(2022, 6) + 1)
    by = {g: late.filter(pl.col("pgroup") == g) for g in ("QB", "WR", "RB", "DL", "S")}
    assert by["QB"]["eff_ypa_l8"].null_count() == 0
    assert by["QB"]["eff_play_action_rate_l8"].null_count() == 0
    assert by["WR"]["eff_yds_per_tgt_l8"].null_count() == 0
    assert by["WR"]["eff_separation_l4"].null_count() == 0
    assert by["RB"]["eff_ryoe_per_att_l4"].null_count() == 0
    assert by["DL"]["eff_pressures_per_snap_l8"].null_count() == 0
    # positions without that kind of history stay null
    assert by["S"]["eff_yds_per_tgt_l8"].null_count() == by["S"].height
    assert by["DL"]["eff_ypa_l8"].null_count() == by["DL"].height
    assert by["WR"]["eff_tackles_per_snap_l8"].null_count() == by["WR"].height


# ---- leakage ----------------------------------------------------------------------------------


def test_future_invariance(world):
    """Scramble every numeric input of games at t >= T (all frames, history rebuilt from them):
    the features of rows with t <= T don't move; those after T do."""
    inp, hist, rows = world
    cut = tk(2022, 4)
    base = efficiency_features(inp, hist, rows)
    inp2 = scrambled(inp, cut)
    out = efficiency_features(inp2, player_history(inp2), rows)
    before = rows["t"] <= cut
    assert before.sum() > 100
    assert_same(base.filter(before), out.filter(before), EFF_FEATURES)
    # positive control: the scramble reached the later rows, so the test has teeth
    after = rows["t"] > cut
    assert len(changed_columns(base.filter(after), out.filter(after), EFF_FEATURES)) > 20


def test_future_invariance_at_other_cuts(world):
    inp, hist, rows = world
    base = efficiency_features(inp, hist, rows)
    for cut in (tk(2021, 5), tk(2022, 1), tk(2022, 6)):
        inp2 = scrambled(inp, cut, seed=cut)
        out = efficiency_features(inp2, player_history(inp2), rows)
        before = rows["t"] <= cut
        assert_same(base.filter(before), out.filter(before), EFF_FEATURES)


def test_pfr_and_ftn_arrive_a_week_late(world):
    """PFR / FTN of week T-1 stay out of week T (lag 1) and reach week T+1; box score and NGS
    of week T-1 reach week T (lag 0)."""
    inp, hist, rows = world
    cut = tk(2022, 4)
    base = efficiency_features(inp, hist, rows)
    early = [c for c in EFF_FEATURES if c not in LATE_FEATURES]
    t = rows["t"]

    late_in = scrambled(inp, cut - 1, cut - 1, frames=LATE_FRAMES)
    late = efficiency_features(late_in, player_history(late_in), rows)
    assert_same(base.filter(t <= cut), late.filter(t <= cut), EFF_FEATURES)
    assert_same(base, late, early)  # nothing else reads PFR / FTN
    assert (
        len(changed_columns(base.filter(t == cut + 1), late.filter(t == cut + 1), LATE_FEATURES))
        > 5
    )

    box_in = scrambled(inp, cut - 1, cut - 1, frames=(*BOX_FRAMES, "ngs_receiving", "ngs_rushing"))
    box = efficiency_features(box_in, player_history(box_in), rows)
    assert_same(base.filter(t < cut), box.filter(t < cut), EFF_FEATURES)
    assert len(changed_columns(base.filter(t == cut), box.filter(t == cut), early)) > 8


def test_upcoming_games_use_history_only(world):
    """Rows of games not played yet get features; with no game in between, week 7 and week 8
    see the same box score history (PFR / FTN differ: week 6 clears the lag only at week 8)."""
    inp, hist, rows = world
    out = efficiency_features(inp, hist, rows).join(
        rows.select("player_id", "game_id", "t"), on=["player_id", "game_id"]
    )
    early = [c for c in EFF_FEATURES if c not in LATE_FEATURES]
    w7 = out.filter(pl.col("t") == tk(2022, 7)).sort("player_id")
    w8 = out.filter(pl.col("t") == tk(2022, 8)).sort("player_id")
    assert w7.height == w8.height == 32
    assert_same(w7, w8, early)
    assert changed_columns(w7, w8, LATE_FEATURES)


# ---- values (hand-built history) --------------------------------------------------------------

_NUM_ZERO = (
    "targets",
    "receptions",
    "receiving_yards",
    "receiving_air_yards",
    "receiving_yards_after_catch",
    "carries",
    "rushing_yards",
    "attempts",
    "passing_yards",
    "sacks_suffered",
    "dropbacks",
    "tackles",
    "def_sacks",
    "def_tackles_for_loss",
)
_NUM_NULL = (
    "receiving_epa",
    "rushing_epa",
    "passing_cpoe",
    "qb_epa_sum",
    "defense_snaps",
    "pressures",
    "pfr_hurries",
    "pfr_qb_hits",
    "pfr_missed_tackles",
    "pfr_tackles",
)
T0 = tk(2022, 0)


def empty_inputs() -> PlayerInputs:
    e = pl.DataFrame
    return PlayerInputs(
        games=e(),
        player_games=e(),
        snaps=e(),
        plays=e(),
        pfr_def=e(),
        pfr_pass=e(),
        pfr_rush=e(),
        pfr_rec=e(),
        ngs_passing=e(),
        ngs_receiving=e(),
        ngs_rushing=e(),
        ftn=e(),
        injuries=e(),
        rosters=e(),
        team_ratings=e(),
        game_features=e(),
    )


def mk_hist(records: list[dict]) -> pl.DataFrame:
    """History rows with every column the efficiency features read (defaults: 0 / null)."""
    base = {
        "player_id": "p",
        "pgroup": "WR",
        "played_def": False,
        **dict.fromkeys(_NUM_ZERO, 0.0),
        **dict.fromkeys(_NUM_NULL, None),
    }
    schema = {
        "player_id": pl.String,
        "game_id": pl.String,
        "pgroup": pl.String,
        "played_def": pl.Boolean,
        "t": pl.Int32,
        **dict.fromkeys((*_NUM_ZERO, *_NUM_NULL), pl.Float64),
    }
    recs = [{**base, "game_id": f"g{r['t']}", **r} for r in records]
    return pl.DataFrame(recs, schema=schema)


def mk_rows(pid: str, weeks: Iterable[int]) -> pl.DataFrame:
    ts = [T0 + w for w in weeks]
    return pl.DataFrame(
        {
            "player_id": [pid] * len(ts),
            "game_id": [f"r{t}" for t in ts],
            "t": pl.Series(ts, dtype=pl.Int32),
        }
    )


def feature(inp, hist, rows, name: str) -> dict[int, float | None]:
    out = efficiency_features(inp, hist, rows)
    return dict(zip(rows["t"].to_list(), out[name].to_list(), strict=True))


def test_ratio_is_sum_over_sum_and_strictly_before():
    h = mk_hist(
        [
            {"t": T0 + 1, "targets": 10.0, "receptions": 6.0, "receiving_yards": 100.0},
            {"t": T0 + 2, "targets": 2.0, "receptions": 2.0, "receiving_yards": 50.0},
            {"t": T0 + 3, "targets": 8.0, "receptions": 4.0, "receiving_yards": 40.0},
        ]
    )
    rows = mk_rows("p", [1, 2, 3, 4])
    ypt = feature(empty_inputs(), h, rows, "eff_yds_per_tgt_l8")
    assert ypt[T0 + 1] is None  # nothing before week 1
    assert ypt[T0 + 2] == pytest.approx(10.0)  # week 1 only: 100 / 10
    assert ypt[T0 + 3] == pytest.approx(150 / 12)  # not (10 + 25) / 2
    assert ypt[T0 + 4] == pytest.approx(190 / 20)  # the game at t = 3 counts only from t = 4
    cr = feature(empty_inputs(), h, rows, "eff_catch_rate_l8")
    assert cr[T0 + 4] == pytest.approx(12 / 20)


def test_minimum_denominator_keeps_thin_samples_null():
    h = mk_hist([{"t": T0 + 1, "targets": 4.0, "receptions": 2.0, "receiving_yards": 90.0}])
    out = feature(empty_inputs(), h, mk_rows("p", [2]), "eff_yds_per_tgt_l8")
    assert out[T0 + 2] is None  # 4 targets < 10


def test_window_is_the_last_eight_games_with_targets():
    recs = [
        {"t": T0 + g, "targets": 10.0, "receptions": 5.0, "receiving_yards": 10.0 * g}
        for g in range(1, 11)
    ]
    out = feature(empty_inputs(), mk_hist(recs), mk_rows("p", [11]), "eff_yds_per_tgt_l8")
    assert out[T0 + 11] == pytest.approx(10 * sum(range(3, 11)) / 80)  # games 3..10


def test_games_without_targets_do_not_dilute_the_window():
    recs = [{"t": T0 + 1, "targets": 10.0, "receptions": 5.0, "receiving_yards": 1000.0}]
    recs += [{"t": T0 + g, "pgroup": "TE"} for g in range(2, 10)]  # eight blocking games
    recs += [{"t": T0 + 10, "targets": 10.0, "receptions": 5.0, "receiving_yards": 50.0}]
    out = feature(empty_inputs(), mk_hist(recs), mk_rows("p", [11]), "eff_yds_per_tgt_l8")
    assert out[T0 + 11] == pytest.approx(1050 / 20)  # the 1000-yard game is still in the window


def test_epa_denominator_skips_games_without_epa():
    h = mk_hist(
        [
            {"t": T0 + 1, "targets": 10.0, "receiving_epa": 4.0},
            {"t": T0 + 2, "targets": 10.0, "receiving_epa": None},  # EPA missing that game
            {"t": T0 + 3, "targets": 10.0, "receiving_epa": 2.0},
        ]
    )
    out = feature(empty_inputs(), h, mk_rows("p", [4]), "eff_epa_per_tgt_l8")
    assert out[T0 + 4] == pytest.approx(6.0 / 20)


def test_cpoe_is_weighted_by_attempts():
    h = mk_hist(
        [
            {
                "t": T0 + 1,
                "pgroup": "QB",
                "attempts": 30.0,
                "dropbacks": 30.0,
                "passing_cpoe": 10.0,
                "passing_yards": 200.0,
            },
            {
                "t": T0 + 2,
                "pgroup": "QB",
                "attempts": 60.0,
                "dropbacks": 62.0,
                "passing_cpoe": -5.0,
                "passing_yards": 500.0,
                "sacks_suffered": 2.0,
                "qb_epa_sum": 31.0,
            },
        ]
    )
    rows = mk_rows("p", [3])
    inp = empty_inputs()
    assert feature(inp, h, rows, "eff_cpoe_l8")[T0 + 3] == pytest.approx(0.0)  # not 2.5
    assert feature(inp, h, rows, "eff_ypa_l8")[T0 + 3] == pytest.approx(700 / 90)
    assert feature(inp, h, rows, "eff_sack_rate_l8")[T0 + 3] == pytest.approx(2 / 92)
    # EPA exists in game 2 only, so only its dropbacks count: 31 / 62
    assert feature(inp, h, rows, "eff_epa_per_db_l8")[T0 + 3] == pytest.approx(0.5)


def test_pfr_defense_waits_a_week_but_box_score_does_not():
    recs = [
        {
            "t": T0 + g,
            "pgroup": "DL",
            "played_def": True,
            "defense_snaps": 100.0,
            "tackles": 5.0,
            "pressures": p,
            "pfr_hurries": 1.0,
            "pfr_qb_hits": 1.0,
            "pfr_missed_tackles": 1.0,
            "pfr_tackles": 4.0,
        }
        for g, p in ((1, 2.0), (2, 6.0), (3, 4.0))
    ]
    h = mk_hist(recs)
    rows = mk_rows("p", [2, 3, 4, 5])
    pres = feature(empty_inputs(), h, rows, "eff_pressures_per_snap_l8")
    assert pres[T0 + 2] is None  # week 1 PFR is not out yet (needs t - 1 > 1)
    assert pres[T0 + 3] == pytest.approx(2 / 100)  # week 1 only
    assert pres[T0 + 4] == pytest.approx(8 / 200)  # weeks 1-2, week 3 still pending
    assert pres[T0 + 5] == pytest.approx(12 / 300)
    tkl = feature(empty_inputs(), h, rows, "eff_tackles_per_snap_l8")
    assert tkl[T0 + 2] == pytest.approx(5 / 100)  # box score: week 1 is visible at week 2
    assert tkl[T0 + 3] == pytest.approx(10 / 200)
    mt = feature(empty_inputs(), h, rows, "eff_missed_tackle_pct_l8")
    assert mt[T0 + 3] is None  # 5 attempts < 10
    assert mt[T0 + 4] == pytest.approx(2 / 10)  # (1 + 1) / (2 * (1 + 4))


def test_ngs_is_the_mean_of_the_last_four_weeks():
    ngs = pl.DataFrame(
        {
            "season": pl.Series([2022] * 6, dtype=pl.Int32),
            "week": pl.Series(range(1, 7), dtype=pl.Int32),
            "player_gsis_id": ["p"] * 6,
            "avg_separation": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "avg_cushion": [None, 2.0, 2.0, 2.0, 2.0, 2.0],
            "avg_yac_above_expectation": [0.0] * 6,
        }
    )
    inp = empty_inputs()
    inp.ngs_receiving = ngs
    h = mk_hist([{"t": T0 + 1}])
    rows = mk_rows("p", [1, 3, 7])
    sep = feature(inp, h, rows, "eff_separation_l4")
    assert sep[T0 + 1] is None
    assert sep[T0 + 3] == pytest.approx(1.5)  # weeks 1-2
    assert sep[T0 + 7] == pytest.approx(4.5)  # weeks 3-6
    cush = feature(inp, h, mk_rows("p", [2]), "eff_cushion_l4")
    assert cush[T0 + 2] is None  # the only earlier NGS week has no cushion


def test_empty_sources_give_null_columns():
    h = mk_hist([{"t": T0 + 1, "targets": 12.0, "receiving_yards": 60.0}])
    out = efficiency_features(empty_inputs(), h, mk_rows("p", [2, 3]))
    assert out.columns == ["player_id", "game_id", *EFF_FEATURES]
    assert out["eff_yds_per_tgt_l8"].to_list() == [5.0, 5.0]
    for c in (*LATE_FEATURES, "eff_separation_l4", "eff_time_to_throw_l4"):
        assert out[c].null_count() == 2, c


# ---- brute force: an independent re-implementation on the synthetic league --------------------


def _by_player(h: pl.DataFrame) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in h.sort("player_id", "t").iter_rows(named=True):
        out.setdefault(r["player_id"], []).append(r)
    return out


def _ratio(past, num, den, min_den) -> float | None:
    """Sum of `num` over sum of `den` across the games `past`; null values are skipped."""
    ns = [v for r in past if (v := num(r)) is not None]
    ds = [v for r in past if (v := den(r)) is not None]
    if not ns or not ds or sum(ds) < min_den:
        return None
    return sum(ns) / sum(ds)


def _close(a: float | None, b: float | None, label: str) -> None:
    if a is None or b is None:
        assert a is None and b is None, f"{label}: {a} vs {b}"
    else:
        assert a == pytest.approx(b, rel=1e-9, abs=1e-9), label


def test_matches_a_naive_implementation(world):
    inp, hist, rows = world
    out = efficiency_features(inp, hist, rows)
    hp = _by_player(hist)

    def past(pid, t, lag, cond, window=8):
        return [r for r in hp.get(pid, []) if r["t"] < t - lag and cond(r)][-window:]

    def ngs_mean(df, pid, t, col):
        d = df.filter(pl.col("player_gsis_id") == pid).with_columns(tkey().alias("t"))
        vals = [v for tt, v in zip(d["t"], d[col], strict=True) if tt < t and v is not None]
        return float(np.mean(vals[-4:])) if vals else None

    # FTN: (passer, game) -> (dropbacks charted, play-action, blitzed)
    charted = {(r["nflverse_game_id"], r["nflverse_play_id"]): r for r in inp.ftn.to_dicts()}
    ftn_g: dict[tuple[str, str], list[float]] = {}
    for p in inp.plays.iter_rows(named=True):
        f = charted.get((p["game_id"], int(p["play_id"])))
        if p["qb_dropback"] == 1 and f is not None:
            acc = ftn_g.setdefault((p["passer_id"], p["game_id"]), [0.0, 0.0, 0.0])
            acc[0] += 1
            acc[1] += float(f["is_play_action"])
            acc[2] += float(f["n_blitzers"] > 0)

    checked = 0
    for rr, ff in zip(rows.iter_rows(named=True), out.iter_rows(named=True), strict=True):
        pid, t = rr["player_id"], rr["t"]
        g = past(pid, t, 0, lambda r: r["targets"] > 0)
        _close(
            ff["eff_yds_per_tgt_l8"],
            _ratio(g, lambda r: r["receiving_yards"], lambda r: r["targets"], 10),
            "yds/tgt",
        )
        _close(
            ff["eff_adot_l8"],
            _ratio(g, lambda r: r["receiving_air_yards"], lambda r: r["targets"], 10),
            "adot",
        )
        _close(
            ff["eff_epa_per_tgt_l8"],
            _ratio(
                g,
                lambda r: r["receiving_epa"],
                lambda r: None if r["receiving_epa"] is None else r["targets"],
                10,
            ),
            "epa/tgt",
        )
        g = past(pid, t, 0, lambda r: r["carries"] > 0)
        _close(
            ff["eff_yds_per_carry_l8"],
            _ratio(g, lambda r: r["rushing_yards"], lambda r: r["carries"], 15),
            "ypc",
        )
        g = past(pid, t, 0, lambda r: r["dropbacks"] > 0)
        _close(
            ff["eff_cpoe_l8"],
            _ratio(
                g,
                lambda r: None if r["passing_cpoe"] is None else r["passing_cpoe"] * r["attempts"],
                lambda r: None if r["passing_cpoe"] is None else r["attempts"],
                60,
            ),
            "cpoe",
        )
        g = past(pid, t, 0, lambda r: r["played_def"] and (r["defense_snaps"] or 0) > 0)
        _close(
            ff["eff_tackles_per_snap_l8"],
            _ratio(g, lambda r: r["tackles"], lambda r: r["defense_snaps"], 100),
            "tackles/snap",
        )
        g = past(
            pid,
            t,
            1,
            lambda r: (
                r["played_def"] and (r["defense_snaps"] or 0) > 0 and r["pressures"] is not None
            ),
        )
        _close(
            ff["eff_pressures_per_snap_l8"],
            _ratio(g, lambda r: r["pressures"], lambda r: r["defense_snaps"], 100),
            "pressures/snap",
        )
        _close(
            ff["eff_missed_tackle_pct_l8"],
            _ratio(
                g,
                lambda r: r["pfr_missed_tackles"],
                lambda r: r["pfr_missed_tackles"] + r["pfr_tackles"],
                10,
            ),
            "missed tackle %",
        )
        _close(
            ff["eff_separation_l4"],
            ngs_mean(inp.ngs_receiving, pid, t, "avg_separation"),
            "separation",
        )
        _close(
            ff["eff_time_to_throw_l4"],
            ngs_mean(inp.ngs_passing, pid, t, "avg_time_to_throw"),
            "time to throw",
        )
        # FTN: the QB's charted dropbacks, games a week late
        games_ftn = [r for r in hp.get(pid, []) if (pid, r["game_id"]) in ftn_g and r["t"] < t - 1]
        games_ftn = games_ftn[-8:]
        db = sum(ftn_g[(pid, r["game_id"])][0] for r in games_ftn)
        pa = sum(ftn_g[(pid, r["game_id"])][1] for r in games_ftn)
        _close(ff["eff_play_action_rate_l8"], pa / db if db >= 60 else None, "play action")
        checked += 1
    assert checked == rows.height
    assert out["eff_play_action_rate_l8"].drop_nulls().len() > 5
    assert out["eff_pressures_per_snap_l8"].drop_nulls().len() > 20
