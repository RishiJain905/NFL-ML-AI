"""Playoff weeks and rehearsals (P10), the model side: the round's name, player rows for a
playoff week, a past week replayed with a pinned clock, and the walk-forward history a refit
borrows from the canonical backtest. The digest side is `tests/digest/test_playoff_digest.py`."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import duckdb
import polars as pl
from test_player_core import _augment
from test_player_efficiency import make_world

from nflengine.features import player as PF
from nflengine.features.player_data import with_playoff_week
from nflengine.models import player_runs as R
from nflengine.models.player_schema import get_target
from nflengine.ops.calendar import round_name
from nflengine.paths import DataPaths


def test_round_name() -> None:
    assert round_name(["REG", "REG"]) is None
    assert round_name([]) is None
    assert round_name(["WC", "WC"]) == "Wild Card round"
    assert round_name(["DIV"]) == "Divisional round"
    assert round_name(["CON"]) == "Conference championships"
    assert round_name(["SB"]) == "Super Bowl"


# ---- player rows -------------------------------------------------------------------------------


def _world():
    inp, hist, _ = make_world()
    return _augment(inp), hist


def test_upcoming_rows_with_a_pinned_clock_replay_a_past_week() -> None:
    """2022 week 7 is unplayed in the synthetic league. Marked completed (a past week), the
    live rule finds no slate; with `now` before its kickoffs it finds the same players."""
    inp, hist = _world()
    season, week = 2022, 7
    qbs = pl.DataFrame({"team": ["ARI"], "qb_id": ["ARI_QB"]})
    live = PF.upcoming_rows(inp, hist, season, week, qbs).sort("player_id", "game_id")
    assert live.height
    wk = (pl.col("season") == season) & (pl.col("week") == week)
    past = replace(
        inp,
        games=inp.games.with_columns(
            pl.when(wk).then(pl.lit(True)).otherwise(pl.col("completed")).alias("completed")
        ),
    )
    assert PF.upcoming_rows(past, hist, season, week, qbs).is_empty()
    first = past.games.filter(wk)["kickoff_utc"].min()
    tuesday = first - dt.timedelta(days=3)
    replay = PF.upcoming_rows(past, hist, season, week, qbs, now=tuesday)
    assert replay.sort("player_id", "game_id").equals(live)
    after = first + dt.timedelta(minutes=1)  # games that kicked off by then are left out
    assert PF.upcoming_rows(past, hist, season, week, qbs, now=after).height < live.height


def _curated_db(tmp_path, inp, rows: dict[str, pl.DataFrame]) -> DataPaths:
    paths = DataPaths(tmp_path)
    paths.curated.mkdir(parents=True)
    con = duckdb.connect(str(paths.curated / "nfl.duckdb"))
    for name, df in rows.items():
        con.register("df", df.to_arrow())
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM df")
        con.unregister("df")
    con.close()
    return paths


def test_with_playoff_week_adds_the_rounds_games_reports_and_rosters(tmp_path) -> None:
    inp, _ = _world()
    kick = dt.datetime(2023, 1, 14, 21, 30, tzinfo=dt.UTC)
    games = pl.DataFrame(
        {
            "game_id": ["2022_19_ARI_BUF", "2022_08_X_Y"],
            "season": [2022, 2022],
            "week": [19, 8],
            "game_type": ["WC", "REG"],
            "kickoff_utc": [kick, kick],
            "home_team": ["BUF", "X"],
            "away_team": ["ARI", "Y"],
            "completed": [True, True],  # a past playoff game: projected as upcoming anyway
            "neutral_site": [False, False],
            "div_game": [False, False],
            "roof": ["outdoors", "outdoors"],
        }
    )
    inj = inp.injuries.head(1).with_columns(
        pl.lit(2022).cast(pl.Int32).alias("season"),
        pl.lit(19).cast(pl.Int32).alias("week"),
        pl.lit("WC").alias("game_type"),
    )
    ros = inp.rosters.head(2).with_columns(
        pl.lit(2022).cast(pl.Int32).alias("season"),
        pl.lit(19).cast(pl.Int32).alias("week"),
        pl.lit("WC").alias("game_type"),
    )
    paths = _curated_db(tmp_path, inp, {"games": games, "injuries": inj, "rosters_weekly": ros})
    out = with_playoff_week(inp, 2022, 19, paths)
    wc = out.games.filter(pl.col("week") == 19)
    assert wc["game_id"].to_list() == ["2022_19_ARI_BUF"] and wc["completed"].to_list() == [False]
    assert out.games.height == inp.games.height + 1 and out.games.schema == inp.games.schema
    assert out.injuries.height == inp.injuries.height + 1
    assert out.rosters.height == inp.rosters.height + 2
    # a regular-season week (no playoff games) changes nothing
    assert with_playoff_week(inp, 2022, 8, paths) is inp


# ---- the refit's borrowed history ----------------------------------------------------------------


def test_playoff_role_needs_the_last_3_games_not_just_the_rested_final_week() -> None:
    """Jeremiah Trotter Jr. (2025): ~0% of snaps in weeks 16-17, 100% in week 18 (starters
    rested), then 0% in the Wild Card. Last 2 = 0.50 (a "regular"), last 3 = 0.36. Drake
    Dabney: 2 games all season (weeks 17-18): too few for a playoff role."""
    p = pl.DataFrame(
        {
            "week": [19, 18, 19, 19],
            "use_snap_l2": [0.50, 0.50, 0.80, 0.52],
            "use_snap_l3": [0.36, 0.36, 0.70, 0.52],
            "own_n_season": [6, 6, 16, 2],
        }
    )
    got = p.select(R._role_ok(p).alias("ok"), R._playoff_row(p).alias("po"))
    assert got["ok"].to_list() == [False, True, True, False]
    assert got["po"].to_list() == [True, False, True, True]
    # 2020 (17 regular-season weeks): its Wild Card is week 18
    old = pl.DataFrame({"season": [2020, 2020, 2021], "week": [18, 17, 18]})
    assert old.select(R._playoff_row(old))["week"].to_list() == [True, False, False]
    no_l3 = p.drop("use_snap_l3")  # older frames without the column: the plain rule
    assert no_l3.select(R._role_ok(no_l3))["use_snap_l2"].to_list() == [True, True, True, True]


def test_seed_history_stops_before_the_target_season(tmp_path) -> None:
    target = get_target("rec_yds")[0]
    paths = DataPaths(tmp_path)
    folder = paths.runs / "backtests" / "player" / target.key
    folder.mkdir(parents=True)
    pl.DataFrame(
        {
            "season": [2022, 2023, 2024, 2025],
            "week": [5, 5, 5, 5],
            "player_id": ["a"] * 4,
            "game_id": ["g"] * 4,
            "p10": [1.0] * 4,
            "p50": [2.0] * 4,
            "p90": [3.0] * 4,
            "mean": [2.0] * 4,
            "actual": [2.0] * 4,
        }
    ).write_parquet(folder / R.PRED_FILE)
    # live 2026: the last BURN_IN backtested seasons; a rehearsal of 2025: none of 2025
    assert R._seed_history(paths, target, 2026)["season"].to_list() == [2024, 2025]
    assert R._seed_history(paths, target, 2025)["season"].to_list() == [2023, 2024]
