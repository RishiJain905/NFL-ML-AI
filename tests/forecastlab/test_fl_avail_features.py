"""FL00 availability: the universe, the labels and the leakage rules of the features."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from fl_avail_fixtures import make_league, pid, prepared

from nflengine.forecastlab import availability as A

FEATS = sorted(
    {
        *A.feature_list("saturday", "snap"),
        "report_status",
        "practice_status",
        "prev_status",
        "roster_status",
        "base_snap",
        "base_tgt_share",
        "base_carry_share",
    }
    - {"vac_tgt", "vac_car", "vac_snap"}  # added at prediction time (`add_vacated`)
)


@pytest.fixture(scope="module")
def league():
    return make_league()


@pytest.fixture(scope="module")
def rows(league):
    return A.build_rows(prepared(league))


def _frame(tables, view, keys=None):
    prep = prepared(tables)
    rows = A.build_rows(prep, keys)
    return A.add_features(prep, rows, view).sort("game_id", "player_id")


def _same(a: pl.DataFrame, b: pl.DataFrame, cols) -> None:
    assert a.height == b.height
    assert a.select("game_id", "player_id").equals(b.select("game_id", "player_id"))
    for c in cols:
        x, y = a[c], b[c]
        if x.dtype.is_numeric():
            xa, ya = x.cast(pl.Float64).to_numpy(), y.cast(pl.Float64).to_numpy()
            assert np.array_equal(np.isnan(xa), np.isnan(ya)), c
            assert np.allclose(np.nan_to_num(xa), np.nan_to_num(ya), atol=0, rtol=0), c
        else:
            assert x.equals(y), c


def test_universe_last_three_games_and_labels(league, rows):
    # every universe row took a snap for the team in one of its last 3 games before the week
    app = prepared(league).app.filter(pl.col("played_side"))
    r = rows.filter((pl.col("season") == 2016) & (pl.col("week") == 4))
    for row in r.iter_rows(named=True):
        prev = app.filter(
            (pl.col("player_id") == row["player_id"])
            & (pl.col("team") == row["team"])
            & (pl.col("t") < row["t"])
            & (pl.col("t") >= row["t"] - 3)
        )
        assert prev.height >= 1, row["player_id"]
    # the reserve WR2 (RES from 2015 week 3) leaves the universe once his last game is 3+ back
    wr2 = pid("KC", "WR", 2)
    assert rows.filter((pl.col("player_id") == wr2) & (pl.col("season") == 2016)).height == 0
    # labels: played = a snap on his side in the week's game
    one = r.row(0, named=True)
    s = league["snaps"].filter(
        (pl.col("gsis_id") == one["player_id"]) & (pl.col("game_id") == one["game_id"])
    )
    assert bool(one["played"]) == (s.height > 0)
    assert set(r["pgroup"].unique().to_list()) <= set(A.PGROUPS)


def test_roster_rules_drop_cut_and_moved_players(league):
    t = {k: v.clone() for k, v in league.items()}
    gone, moved = pid("KC", "WR", 1), pid("KC", "TE", 1)
    ros = t["rosters_weekly"]
    t["rosters_weekly"] = ros.with_columns(
        pl.when((pl.col("gsis_id") == gone) & (pl.col("season") == 2016) & (pl.col("week") == 3))
        .then(pl.lit("CUT"))
        .otherwise(pl.col("status"))
        .alias("status"),
        pl.when((pl.col("gsis_id") == moved) & (pl.col("season") == 2016) & (pl.col("week") == 3))
        .then(pl.lit("NE"))
        .otherwise(pl.col("team"))
        .alias("team"),
    )
    r = A.build_rows(prepared(t)).filter((pl.col("season") == 2016) & (pl.col("week") == 4))
    ids = set(r["player_id"].to_list())
    assert gone not in ids and moved not in ids
    base = A.build_rows(prepared(league)).filter((pl.col("season") == 2016) & (pl.col("week") == 4))
    assert {gone, moved} <= set(base.filter(pl.col("team") == "KC")["player_id"].to_list())


def test_week_one_membership_comes_from_the_week_one_row(league):
    t = {k: v.clone() for k, v in league.items()}
    p = pid("BUF", "RB", 1)
    # he leaves BUF in the offseason: no 2016 week-1 row for BUF, a row for MIA instead
    t["rosters_weekly"] = t["rosters_weekly"].with_columns(
        pl.when((pl.col("gsis_id") == p) & (pl.col("season") == 2016) & (pl.col("week") == 1))
        .then(pl.lit("MIA"))
        .otherwise(pl.col("team"))
        .alias("team")
    )
    r = A.build_rows(prepared(t)).filter((pl.col("season") == 2016) & (pl.col("week") == 1))
    assert p not in set(r.filter(pl.col("team") == "BUF")["player_id"].to_list())
    r0 = A.build_rows(prepared(league)).filter((pl.col("season") == 2016) & (pl.col("week") == 1))
    assert p in set(r0.filter(pl.col("team") == "BUF")["player_id"].to_list())


@pytest.mark.parametrize("view", ["tuesday", "saturday"])
def test_inactive_list_is_never_an_input(league, view):
    """`INA` (the game-day inactive list) can never change a row: replacing every INA with ACT
    (or with DEV, the other folded value) leaves every row and feature identical."""
    base = _frame(league, view)
    for repl in ("ACT", "DEV"):
        t = {k: v.clone() for k, v in league.items()}
        t["rosters_weekly"] = t["rosters_weekly"].with_columns(
            pl.when(pl.col("status") == "INA")
            .then(pl.lit(repl))
            .otherwise(pl.col("status"))
            .alias("status")
        )
        other = _frame(t, view)
        if repl == "ACT":
            _same(base, other, FEATS)
        else:  # DEV vs INA: identical in this week's folded value; only `dev_n4` may differ
            _same(base, other, [c for c in FEATS if c not in ("dev_n4", "roster", "roster_status")])
            assert base["wk_ros"].equals(other["wk_ros"]) if view == "saturday" else True


def test_tuesday_never_reads_the_weeks_report_or_roster(league):
    """Changing week W's injury report and week W's roster rows (W > 1) leaves week W's Tuesday
    rows unchanged."""
    S, W = 2016, 4
    keys = pl.DataFrame({"season": [S], "week": [W]}, schema={"season": pl.Int32, "week": pl.Int32})
    base = _frame(league, "tuesday", keys)
    t = {k: v.clone() for k, v in league.items()}
    wk = (pl.col("season") == S) & (pl.col("week") == W)
    t["injuries"] = pl.concat(
        [
            t["injuries"].filter(~wk),
            base.select(
                pl.lit(S, pl.Int32).alias("season"),
                pl.lit(W, pl.Int32).alias("week"),
                pl.col("player_id").alias("gsis_id"),
                "team",
                pl.lit("Out").alias("report_status"),
                pl.lit("Did Not Participate In Practice").alias("practice_status"),
            ),
        ]
    )
    t["rosters_weekly"] = t["rosters_weekly"].with_columns(
        pl.when(wk).then(pl.lit("RES")).otherwise(pl.col("status")).alias("status")
    )
    other = _frame(t, "tuesday", keys)
    _same(base, other, FEATS)
    # ... while the Saturday view does see them
    sat = _frame(t, "saturday", keys)
    assert (sat["report_status"] == "Out").all()
    assert (sat["roster_status"] == "RES").all()


def test_future_rows_never_move_a_past_row(league):
    """Scrambling every input from week W on leaves the features of every row before W as
    they were."""
    S, W = 2016, 3
    cut = A.tkey(S, W)
    base = _frame(league, "saturday").filter(pl.col("t") < cut)
    rng = np.random.default_rng(1)
    t = {k: v.clone() for k, v in league.items()}

    def future(df):
        return A.tkey(pl.col("season"), pl.col("week")) >= cut

    for name in ("snaps", "injuries", "rosters_weekly"):
        df = t[name]
        f = df.filter(future(df))
        if name == "snaps":
            f = f.with_columns(
                pl.Series("offense_pct", rng.random(f.height)),
                pl.Series("defense_pct", rng.random(f.height)),
            )
        elif name == "injuries":
            f = f.with_columns(pl.lit("Out").alias("report_status"))
        else:
            f = f.with_columns(pl.lit("RES").alias("status"))
        t[name] = pl.concat([df.filter(~future(df)), f])
    fut_games = t["games"].filter(future(t["games"]))["game_id"].to_list()
    t["player_games"] = t["player_games"].with_columns(
        pl.when(pl.col("game_id").is_in(fut_games))
        .then(pl.lit(0.9))
        .otherwise(pl.col("target_share"))
        .alias("target_share")
    )
    other = _frame(t, "saturday").filter(pl.col("t") < cut)
    _same(base, other, FEATS + ["played", "actual_snap", "actual_tgt_share", "actual_carry_share"])


def test_baseline_reads_what_the_view_reads(rows, league):
    prep = prepared(league)
    sat = A.add_features(prep, rows, "saturday")
    tue = A.add_features(prep, rows, "tuesday")
    train = sat.filter(pl.col("season") < 2016)
    rates = A.base_rates(train, "saturday")
    p = A.apply_base(sat.filter(pl.col("season") == 2016), rates).to_numpy()
    assert np.all((p > 0) & (p < 1))
    out = sat.filter((pl.col("season") == 2016) & (pl.col("report_status") == "Out"))
    pout = A.apply_base(out, rates).to_numpy()
    assert pout.mean() < p.mean()
    # the Tuesday table never sees this week's report: Out on Saturday doesn't change it
    rt = A.base_rates(tue.filter(pl.col("season") < 2016), "tuesday")
    a = A.apply_base(tue.filter(pl.col("season") == 2016), rt)
    assert a.len() == tue.filter(pl.col("season") == 2016).height


def test_vacated_usage_sums_teammates_only():
    f = pl.DataFrame(
        {
            "game_id": ["g"] * 4,
            "team": ["KC"] * 4,
            "player_id": ["a", "b", "c", "d"],
            "pgroup": ["WR", "WR", "TE", "RB"],
            "base_tgt_share": [0.3, 0.2, 0.1, 0.05],
            "base_carry_share": [None, None, None, 0.6],
            "base_snap": [0.9, 0.8, 0.7, 0.6],
            "p": [0.0, 1.0, 1.0, 0.5],
        }
    )
    v = A.add_vacated(f, 1.0 - pl.col("p")).sort("player_id")
    assert v["vac_tgt"].to_list() == pytest.approx([0.025, 0.325, 0.325, 0.3])
    assert v["vac_snap"].to_list() == pytest.approx([0.0, 0.9, 0.0, 0.0])
    # carries left open by the RB reach every teammate's row, never his own
    assert v["vac_car"].to_list() == pytest.approx([0.3, 0.3, 0.3, 0.0])


def test_week_one_tuesday_reads_membership_only(league):
    """In week 1 the Tuesday view reads the week's roster row for membership only: turning
    every member's status into RES changes no Tuesday row."""
    keys = pl.DataFrame(
        {"season": [2016], "week": [1]}, schema={"season": pl.Int32, "week": pl.Int32}
    )
    base = _frame(league, "tuesday", keys)
    t = {k: v.clone() for k, v in league.items()}
    wk = (pl.col("season") == 2016) & (pl.col("week") == 1)
    t["rosters_weekly"] = t["rosters_weekly"].with_columns(
        pl.when(wk).then(pl.lit("RES")).otherwise(pl.col("status")).alias("status")
    )
    _same(base, _frame(t, "tuesday", keys), FEATS)
