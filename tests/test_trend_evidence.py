"""Trend supporting evidence (documentation/04 -> Trend -> Drivers) on a hand-made fixture.

Two teams, AAA (home) vs BBB, play each other every week: 2024 weeks 1-2 and 2025
weeks 1-6 are played, 2025 week 7 is scheduled. Every AAA offensive game has 4
dropbacks (`sacks` of them sacks, the rest carry `cpoe`) and 1 run, all with the same
`pass_oe`; noise rows (a two-point try, a no-play, a play without posteam) must be
ignored. BBB's offense is all zeros.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from nflengine.features.asof import AsOf
from nflengine.features.leakage import LeakageError, assert_future_invariant
from nflengine.models.trend_evidence import (
    EVIDENCE_TABLES,
    OUTPUT_SCHEMA,
    PLAY_COLS,
    _ngs_team_weeks,
    load_evidence_tables,
    trend_evidence,
)
from nflengine.paths import DataPaths

KEYS = [AsOf(2025, w) for w in range(1, 8)]
PROTECT = {"game_id", "gsis_id", "player_gsis_id"}
# AAA offense per 2025 week: (sacks, cpoe, pass_oe); 2024 weeks are all zero.
A_OFF = {1: (0, 10.0, 5.0), 2: (1, 0.0, 5.0), 3: (2, -5.0, -5.0), 4: (2, -5.0, -5.0)}
A_OFF |= {5: (1, -10.0, -5.0), 6: (4, 50.0, 50.0)}
A_QB = {1: "A1", 2: "A1", 3: "A1", 4: "A2", 5: "A2", 6: "A2", 7: "A3"}


def _gid(season: int, week: int) -> str:
    return f"{season}_{week:02d}_BBB_AAA"


def _games() -> pl.DataFrame:
    rows = []
    for season, week in [(2024, 1), (2024, 2), *[(2025, w) for w in range(1, 8)]]:
        qb = A_QB[week] if season == 2025 else "A1"
        rows.append(
            {
                "game_id": _gid(season, week),
                "season": season,
                "week": week,
                "game_type": "REG",
                "home_team": "AAA",
                "away_team": "BBB",
                "home_qb_id": qb,
                "away_qb_id": "B1",
                "home_qb_name": f"QB {qb}",
                "away_qb_name": "QB B1",
                "kickoff_utc": dt.datetime(season, 9, 1, tzinfo=dt.UTC) + dt.timedelta(weeks=week),
                "completed": not (season == 2025 and week == 7),
            }
        )
    return pl.DataFrame(rows).with_columns(pl.col("season", "week").cast(pl.Int32))


def _team_plays(season, week, off, dfn, sacks, cpoe, proe) -> list[dict]:
    gid = _gid(season, week)
    base = {"game_id": gid, "season": season, "week": week, "posteam": off, "defteam": dfn}
    rows = []
    for i in range(4):
        sack = 1.0 if i < sacks else 0.0
        rows.append(
            base
            | {"play_type": "pass", "two_point_attempt": 0.0, "qb_dropback": 1.0, "sack": sack}
            | {"cpoe": None if sack else cpoe, "pass_oe": proe}
        )
    rows.append(
        base
        | {"play_type": "run", "two_point_attempt": 0.0, "qb_dropback": 0.0, "sack": 0.0}
        | {"cpoe": None, "pass_oe": proe}
    )
    noise = [
        {"play_type": "pass", "two_point_attempt": 1.0, "qb_dropback": 1.0, "sack": 1.0},
        {"play_type": "no_play", "two_point_attempt": 0.0, "qb_dropback": 0.0, "sack": 0.0},
    ]
    rows += [base | n | {"cpoe": 99.0, "pass_oe": 99.0} for n in noise]
    rows.append(
        base
        | {"posteam": None, "defteam": None, "play_type": "kickoff", "two_point_attempt": 0.0}
        | {"qb_dropback": 0.0, "sack": 1.0, "cpoe": None, "pass_oe": None}
    )
    return rows


def _plays() -> pl.DataFrame:
    rows = []
    for season, weeks in ((2024, [1, 2]), (2025, range(1, 7))):
        for w in weeks:
            sacks, cpoe, proe = A_OFF[w] if season == 2025 else (0, 0.0, 0.0)
            rows += _team_plays(season, w, "AAA", "BBB", sacks, cpoe, proe)
            rows += _team_plays(season, w, "BBB", "AAA", 0, 0.0, 0.0)
    return (
        pl.DataFrame(rows).with_columns(pl.col("season", "week").cast(pl.Int32)).select(PLAY_COLS)
    )


def _pfr(rows: list[tuple[int, str, float]], col: str) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"game_id": _gid(2025, w), "season": 2025, "week": w, "team": t, col: v}
            for w, t, v in rows
        ]
    ).with_columns(pl.col("season", "week").cast(pl.Int32))


def _ngs() -> pl.DataFrame:
    rows = [(0, 9.9, 100, True), (1, 2.0, 10, False), (2, 3.0, 30, False)]
    rows += [(3, 2.5, 20, False), (4, 3.5, 20, False), (6, 9.0, 50, False)]
    return pl.DataFrame(
        [
            {"season": 2025, "season_type": "REG", "week": w, "team": "AAA"}
            | {"player_gsis_id": "A1", "avg_time_to_throw": t, "attempts": a}
            | {"is_season_total": total}
            for w, t, a, total in rows
        ]
    ).with_columns(pl.col("season", "week").cast(pl.Int32))


def _snaps() -> pl.DataFrame:
    # (gsis_id, team, position, week, offense_pct, defense_pct); week 2 is on a 0-100 scale.
    rows = [
        ("P1", "AAA", "WR", 1, 0.9, 0.0),
        ("P1", "AAA", "WR", 2, 90.0, 0.0),
        ("P2", "AAA", "CB", 1, 0.0, 0.7),
        ("P2", "AAA", "CB", 2, 0.0, 70.0),
        ("P3", "AAA", "RB", 1, 0.3, 0.0),
        ("P3", "AAA", "RB", 2, 30.0, 0.0),
        ("P4", "AAA", "LB", 1, 0.0, 0.8),  # misses week 2: mean 0.4
        ("P5", "AAA", "TE", 1, 0.95, 0.0),
        ("P5", "AAA", "TE", 2, 95.0, 0.0),
        ("B6", "BBB", "QB", 1, 1.0, 0.0),
        ("B6", "BBB", "QB", 2, 1.0, 0.0),
    ]
    return pl.DataFrame(
        [
            {"game_id": _gid(2025, w), "season": 2025, "week": w, "team": t, "gsis_id": g}
            | {"position": p, "offense_pct": o, "defense_pct": d}
            for g, t, p, w, o, d in rows
        ]
    ).with_columns(pl.col("season", "week").cast(pl.Int32))


def _injuries() -> pl.DataFrame:
    names = {"P1": "Pat One", "P2": "Cam Two", "P3": "Ray Three", "P4": "Lee Four"}
    names |= {"P5": "Kai Five"}
    pos = {"P1": "WR", "P2": "CB", "P3": "RB", "P4": "LB", "P5": "TE"}
    rows = [
        ("P3", 3, "Out"),
        ("P1", 4, "Out"),
        ("P4", 4, "Out"),
        ("P5", 4, "Questionable"),
        ("P1", 5, "Out"),
        ("P2", 5, "Doubtful"),
        ("P5", 6, "Out"),  # the key's own week: not visible as of week 6
    ]
    return pl.DataFrame(
        [
            {"season": 2025, "week": w, "team": "AAA", "gsis_id": g, "full_name": names[g]}
            | {"position": pos[g], "report_status": s}
            for g, w, s in rows
        ]
    ).with_columns(pl.col("season", "week").cast(pl.Int32))


@pytest.fixture
def tables() -> dict[str, pl.DataFrame]:
    return {
        "games": _games(),
        "plays": _plays(),
        "injuries": _injuries(),
        "snaps": _snaps(),
        # AAA QB pressures; week 3 has two QB rows, week 5 is missing (PFR lag).
        "pfr_pass": _pfr(
            [(1, "AAA", 1.0), (2, "AAA", 1.0), (3, "AAA", 1.0), (3, "AAA", 1.0)]
            + [(4, "AAA", 2.0), (6, "AAA", 5.0)],
            "times_pressured",
        ),
        # BBB defenders' pressures on AAA's QB (player rows).
        "pfr_def": _pfr(
            [(1, "BBB", 1.0), (1, "BBB", 1.0), (2, "BBB", 2.0), (3, "BBB", 1.0)]
            + [(4, "BBB", 1.0), (5, "BBB", 1.0), (6, "BBB", 9.0)],
            "def_pressures",
        ),
        "ngs_passing": _ngs(),
    }


def _row(df: pl.DataFrame, week: int, team: str) -> dict:
    rows = df.filter((pl.col("week") == week) & (pl.col("team") == team)).to_dicts()
    assert len(rows) == 1
    return rows[0]


def test_hand_computed_week6(tables):
    out = trend_evidence(tables, KEYS)
    assert out.schema == pl.Schema(OUTPUT_SCHEMA)
    assert sorted(set(out["week"].to_list())) == [4, 5, 6, 7]  # weeks > window only
    assert out.height == 4 * 2

    a = _row(out, 6, "AAA")
    assert (a["n_recent_games"], a["n_before_games"], a["before_source"]) == (3, 2, "season")
    # Sacks/dropbacks: recent 5/12, before 1/8.
    assert a["off_sack_rate_delta"] == pytest.approx(5 / 12 - 1 / 8)
    # CPOE pooled over non-sack dropbacks: recent (-10 - 10 - 30) / 7, before 40 / 7.
    assert a["off_cpoe_delta"] == pytest.approx(-90 / 7)
    assert a["off_proe_delta"] == pytest.approx(-10.0)
    assert a["def_sack_rate_delta"] == pytest.approx(0.0)
    # PFR: week 5 missing -> recent = weeks 3-4: 4/8; before 2/8.
    assert a["off_pressure_rate_delta"] == pytest.approx(0.25)
    assert a["def_pressure_rate_delta"] is None  # no pfr_def rows for AAA's defense
    # NGS: before (2*10 + 3*30) / 40 = 2.75, recent (2.5*20 + 3.5*20) / 40 = 3.0.
    assert a["off_time_to_throw_delta"] == pytest.approx(0.25)
    assert a["qb_change"] is True
    assert (a["qb_now_id"], a["qb_before_id"]) == ("A2", "A1")
    assert (a["qb_now_name"], a["qb_before_name"]) == ("QB A2", "QB A1")
    # P1 (0.9) and P2 (0.7) are key; P3 (0.3) and P4 (0.4, missed a game) are not; P5 is
    # only Questionable in the window (Out in week 6 itself).
    assert a["key_players_out"] == 2
    assert a["key_players_out_names"] == "Pat One (WR), Cam Two (CB)"

    b = _row(out, 6, "BBB")
    assert b["qb_change"] is False
    assert b["def_sack_rate_delta"] == pytest.approx(5 / 12 - 1 / 8)
    assert b["off_cpoe_delta"] == pytest.approx(0.0)
    # BBB pass rush: recent weeks 3-5 3/12, before weeks 1-2 4/8.
    assert b["def_pressure_rate_delta"] == pytest.approx(-0.25)
    assert b["off_pressure_rate_delta"] is None
    assert b["off_time_to_throw_delta"] is None
    assert b["key_players_out"] == 0
    assert b["key_players_out_names"] is None

    # Week 7's game isn't played: its projected starter (A3) is never used.
    assert _row(out, 7, "AAA")["qb_now_id"] == "A2"


def test_pfr_publication_lag(tables):
    """PFR for week w-1 isn't out by Tuesday of week w, so it never counts for key w."""
    base = trend_evidence(tables, KEYS)
    late = dict(tables)
    extra = _pfr([(5, "AAA", 9.0)], "times_pressured")
    late["pfr_pass"] = pl.concat([tables["pfr_pass"], extra])
    out = trend_evidence(late, KEYS)
    col = "off_pressure_rate_delta"
    assert _row(out, 6, "AAA")[col] == pytest.approx(_row(base, 6, "AAA")[col])
    assert _row(out, 7, "AAA")[col] != pytest.approx(_row(base, 7, "AAA")[col])


def test_season_fallback_uses_last_season(tables):
    out = trend_evidence(tables, KEYS)
    a4 = _row(out, 4, "AAA")
    # Week 4: recent weeks 1-3, no 2025 game before week 1 -> 2024 REG weeks 1-2.
    assert (a4["n_recent_games"], a4["n_before_games"]) == (3, 2)
    assert a4["before_source"] == "last_season"
    assert a4["off_sack_rate_delta"] == pytest.approx(3 / 12)
    assert a4["off_cpoe_delta"] == pytest.approx(30 / 9)
    assert a4["off_proe_delta"] == pytest.approx(5 / 3)
    assert a4["off_pressure_rate_delta"] is None  # no PFR for 2024
    assert a4["key_players_out"] is None  # no snap counts for the 2024 games
    assert a4["qb_change"] is False  # A1 now, A1 in the last 2024 game

    # Week 5: only week 1 before the recent window (< 2 games) -> last season too, but the
    # QB comparison uses the latest 2025 game before the window (week 1, A1 -> A2).
    a5 = _row(out, 5, "AAA")
    assert (a5["before_source"], a5["n_before_games"]) == ("last_season", 2)
    assert a5["qb_change"] is True

    # Without 2024 there is nothing to fall back on.
    no_prev = dict(tables)
    no_prev["games"] = tables["games"].filter(pl.col("season") == 2025)
    out2 = trend_evidence(no_prev, KEYS)
    a4 = _row(out2, 4, "AAA")
    assert (a4["before_source"], a4["n_before_games"], a4["n_recent_games"]) == (None, 0, 3)
    assert a4["off_sack_rate_delta"] is None
    assert a4["qb_change"] is None and a4["qb_now_id"] == "A1"
    a5 = _row(out2, 5, "AAA")
    assert (a5["before_source"], a5["n_before_games"]) == ("season", 1)
    assert a5["off_sack_rate_delta"] == pytest.approx((1 + 2 + 2) / 12 - 0.0)


def _with_2024_games(games: pl.DataFrame, extra: list[tuple[int, str, str]]) -> pl.DataFrame:
    """Add 2024 AAA home games: (week, game_type, AAA starter)."""
    template = games.filter(pl.col("game_id") == _gid(2024, 1))
    rows = [
        template.with_columns(
            pl.lit(_gid(2024, w)).alias("game_id"),
            pl.lit(w, pl.Int32).alias("week"),
            pl.lit(gt).alias("game_type"),
            pl.lit(qb).alias("home_qb_id"),
            pl.lit(f"QB {qb}").alias("home_qb_name"),
        )
        for w, gt, qb in extra
    ]
    return pl.concat([games, *rows])


def test_qb_fallback_uses_last_seasons_main_starter(tables):
    # 2024: A1 starts weeks 1-2, a backup (AB) the last REG game and a playoff game.
    # Main starter = A1 (2 REG starts vs 1; playoff starts don't count) -> no change.
    t = dict(tables)
    t["games"] = _with_2024_games(tables["games"], [(3, "REG", "AB"), (19, "WC", "AB")])
    a4 = _row(trend_evidence(t, KEYS), 4, "AAA")
    assert (a4["qb_now_id"], a4["qb_before_id"], a4["qb_before_name"]) == ("A1", "A1", "QB A1")
    assert a4["qb_change"] is False
    # The in-season comparison is unchanged: week 6 still compares to 2025 week 2.
    a6 = _row(trend_evidence(t, KEYS), 6, "AAA")
    assert (a6["qb_before_id"], a6["qb_change"]) == ("A1", True)

    # A 2-2 tie in REG starts goes to the QB who started latest (AB) -> a change.
    t["games"] = _with_2024_games(tables["games"], [(3, "REG", "AB"), (4, "REG", "AB")])
    a4 = _row(trend_evidence(t, KEYS), 4, "AAA")
    assert (a4["qb_before_id"], a4["qb_change"]) == ("AB", True)


def test_missing_optional_tables(tables):
    full = trend_evidence(tables, KEYS)
    core = {k: tables[k] for k in ("games", "plays")}
    out = trend_evidence(core, KEYS)
    assert out.schema == full.schema and out.height == full.height
    optional = [
        "off_pressure_rate_delta",
        "def_pressure_rate_delta",
        "off_time_to_throw_delta",
        "key_players_out",
        "key_players_out_names",
    ]
    for c in optional:
        assert out[c].null_count() == out.height, c
    same = [c for c in out.columns if c not in optional]
    assert out.select(same).equals(full.select(same))


def test_leak_free(tables):
    key = AsOf(2025, 6)
    checked = assert_future_invariant(
        lambda t: trend_evidence(t, KEYS), tables, key, protect=PROTECT
    )
    assert set(checked["week"].to_list()) == {4, 5, 6}


def test_leak_check_catches_a_leaky_builder(tables):
    """Sanity check on the check: a builder whose windows include the key's own week
    (keys shifted by one, relabelled) must fail."""

    def leaky(t):
        shifted = [AsOf(k.season, k.week + 1) for k in KEYS]
        return trend_evidence(t, shifted).with_columns(pl.col("week") - 1)

    with pytest.raises(LeakageError):
        assert_future_invariant(leaky, tables, AsOf(2025, 6), protect=PROTECT)


def test_no_keys_after_window(tables):
    out = trend_evidence(tables, [AsOf(2025, 2), AsOf(2025, 3)])
    assert out.is_empty() and out.schema == pl.Schema(OUTPUT_SCHEMA)


def test_ngs_super_bowl_week_is_clamped():
    """NGS numbers the Super Bowl one week after `games` (Pro Bowl gap)."""
    tg = pl.DataFrame(
        {"season": [2025], "week": [22], "game_type": ["SB"], "team": ["AAA"]},
        schema_overrides={"season": pl.Int32, "week": pl.Int32},
    )
    ngs = pl.DataFrame(
        {
            "season": [2025, 2025],
            "season_type": ["POST", "POST"],
            "week": [21, 23],
            "team": ["AAA", "AAA"],
            "avg_time_to_throw": [2.0, 3.0],
            "attempts": [10, 30],
            "is_season_total": [False, False],
        }
    )
    out = _ngs_team_weeks({"ngs_passing": ngs}, tg).sort("week")
    assert out["week"].to_list() == [21, 22]


def test_load_evidence_tables(tmp_path, tables):
    cur = tmp_path / "curated"
    (cur / "plays").mkdir(parents=True)
    plays = tables["plays"].with_columns(pl.lit("extra").alias("desc"))
    for season in (2024, 2025):
        plays.filter(pl.col("season") == season).write_parquet(
            cur / "plays" / f"season={season}.parquet"
        )
    for name in ("games", "injuries", "snaps"):
        tables[name].write_parquet(cur / f"{name}.parquet")

    got = load_evidence_tables(DataPaths(tmp_path), min_season=2025)
    assert set(got) == {"games", "plays", "injuries", "snaps"}
    assert set(got) <= set(EVIDENCE_TABLES)
    assert got["plays"].columns == PLAY_COLS
    assert set(got["plays"]["season"].to_list()) == {2024, 2025}
    got2 = load_evidence_tables(DataPaths(tmp_path), min_season=2026)
    assert set(got2["plays"]["season"].to_list()) == {2025}
    out = trend_evidence(got, KEYS)
    assert out.height == 8


def test_played_qb_comes_from_dropbacks_not_the_listed_qb() -> None:
    """P04 fact-check: 2024 IND games list Flacco while Richardson took every dropback."""
    from nflengine.models.trend_evidence import _played_qbs

    tg = pl.DataFrame(
        {"game_id": ["g1", "g2"], "team": ["IND", "IND"], "qb_id": ["flacco", "flacco"],
         "qb_name": ["Joe Flacco", "Joe Flacco"]}
    )  # fmt: skip
    plays = pl.DataFrame(
        {"game_id": ["g1"] * 3 + ["g2"] * 2, "posteam": ["IND"] * 5,
         "qb_dropback": [1, 1, 1, 1, 0], "passer_id": ["ar", "ar", "flacco", "ghost", None]}
    )  # fmt: skip
    games = pl.DataFrame(
        {"home_qb_id": ["ar", "flacco"], "home_qb_name": ["Anthony Richardson", "Joe Flacco"],
         "away_qb_id": [None, None], "away_qb_name": [None, None]}
    )  # fmt: skip
    out = {r["game_id"]: r for r in _played_qbs(tg, plays, games).iter_rows(named=True)}
    assert out["g1"]["qb_name"] == "Anthony Richardson"  # most dropbacks wins
    assert out["g2"]["qb_name"] == "Joe Flacco"  # unknown passer name: keep the listed QB
