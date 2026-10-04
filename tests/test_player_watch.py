"""Watch-list selection from player projections (plan P06; models/player_watch.py)."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from nflengine.models.player_schema import MAIN_TARGETS, PRED_SCHEMA, conform
from nflengine.models.player_watch import (
    FOLLOWED_BOOST,
    GROUP_CAPS,
    MIN_VOLUME,
    N_TOUGH,
    eligible,
    select_watchlist,
    tough_spots,
    watchlist_backtest,
)

TUESDAY = dt.datetime(2025, 10, 21, 14, tzinfo=dt.UTC)
SUNDAY = dt.datetime(2025, 10, 26, 17, tzinfo=dt.UTC)
GROUP_POS = {"QB": "QB", "RB": "RB", "WR/TE": "WR", "EDGE/DL": "DE", "LB/S": "LB"}


def row(pid: str, team: str, z: float, group: str = "WR/TE", **kw) -> dict:
    """One main-target projection row; baseline 50, p50 = 50 + 10 z."""
    t = MAIN_TARGETS[group]
    base = {
        "season": 2025,
        "week": 8,
        "game_id": f"2025_08_{team}",
        "kickoff_utc": SUNDAY,
        "created_at": TUESDAY,
        "player_id": pid,
        "player": f"Player {pid}",
        "team": team,
        "opponent": "OPP",
        "home": True,
        "position": GROUP_POS[group],
        "pgroup": GROUP_POS[group][:2],
        "group": group,
        "target": t.name,
        "target_label": t.label,
        "kind": t.kind,
        "unit": t.unit,
        "is_main": True,
        "p10": 20.0 + 10 * z,
        "p50": 50.0 + 10 * z,
        "p90": 80.0 + 10 * z,
        "baseline": 50.0,
        "baseline_source": "rolling",
        "outperformance": 10 * z,
        "outperf_z": z,
        "games_history": 20,
        "games_season": 6,
        "snap_share_l2": 0.8,
        "role_ok": True,
        "role_change": False,
        "confidence": "medium",
        "model_version": "player-model-v1:backtest",
    }
    base.update(kw)
    return base


def frame(rows: list[dict]) -> pl.DataFrame:
    return conform(pl.DataFrame(rows, schema_overrides={"kickoff_utc": PRED_SCHEMA["kickoff_utc"]}))


def ids(df: pl.DataFrame) -> list[str]:
    return df["player_id"].to_list()


# ---- pool ---------------------------------------------------------------------------------------


def test_pool_keeps_main_targets_of_role_players_in_games_not_started():
    df = frame(
        [
            row("a", "KC", 1.0),
            row("side", "KC", 2.0, is_main=False),
            row("bench", "BUF", 2.0, role_ok=False),
            row("fill-in", "BUF", 0.5, role_ok=False, role_change=True),
            row("thu", "DEN", 2.0, kickoff_utc=TUESDAY - dt.timedelta(hours=1)),
            row("out", "MIA", 2.0, injury_status="Out"),
            row("doubt", "MIA", 2.0, injury_status="Doubtful"),
            row("q", "NE", 0.2, injury_status="Questionable"),
            row("nan", "NE", float("nan")),
            row("null", "NYJ", 1.0, outperf_z=None),
        ]
    )
    assert sorted(ids(eligible(df))) == ["a", "fill-in", "q"]


def test_run_time_overrides_each_rows_created_at():
    df = frame([row("a", "KC", 1.0, kickoff_utc=TUESDAY + dt.timedelta(days=2))])
    assert ids(eligible(df)) == ["a"]
    friday = TUESDAY + dt.timedelta(days=3)
    assert eligible(df, friday).is_empty()
    assert select_watchlist(df, run_time=friday).is_empty()


# ---- watch list ---------------------------------------------------------------------------------


def test_largest_positive_outperformance_first_and_negatives_never_picked():
    df = frame([row("a", "KC", 0.4), row("b", "BUF", 1.2), row("c", "DEN", -0.5)])
    out = select_watchlist(df)
    assert ids(out) == ["b", "a"]
    assert out["rank"].to_list() == [1, 2] and out["side"].to_list() == ["offense"] * 2
    assert set(PRED_SCHEMA) <= set(out.columns) and {"rank", "score", "side"} <= set(out.columns)


def test_offense_and_defense_are_picked_separately_with_their_own_quota():
    """Defenders' bigger z can't crowd offense out: each side fills its own list."""
    d = [row(f"d{i}", f"D{i}", 3.0 - i / 10, group=("EDGE/DL", "LB/S")[i % 2]) for i in range(8)]
    o = [row(f"o{i}", f"O{i}", 0.5 - i / 100, group=("QB", "RB", "WR/TE")[i % 3]) for i in range(6)]
    out = select_watchlist(frame([*d, *o]), n_offense=4, n_defense=3)
    assert out.filter(pl.col("side") == "offense")["player_id"].to_list() == [
        "o0",
        "o1",
        "o2",
        "o3",
    ]
    assert out.filter(pl.col("side") == "defense")["player_id"].to_list() == ["d0", "d1", "d2"]
    assert out["side"].to_list() == ["offense"] * 4 + ["defense"] * 3  # offense first
    assert out["rank"].to_list() == [1, 2, 3, 4, 1, 2, 3]  # rank within the side


def test_at_most_two_per_team_per_side():
    off = [row(f"k{i}", "KC", 2.0 - i / 10, group=g) for i, g in enumerate(["WR/TE", "RB", "QB"])]
    dfn = [
        row(f"x{i}", "KC", 1.0 - i / 10, group=g) for i, g in enumerate(["LB/S", "EDGE/DL", "LB/S"])
    ]
    out = select_watchlist(frame([*off, *dfn, row("b", "BUF", 0.1)]))
    assert ids(out) == ["k0", "k1", "b", "x0", "x1"]  # 2 Chiefs on offense and 2 on defense


def test_group_caps_keep_variety_but_relax_when_a_side_would_be_short():
    qbs = [row(f"q{i}", f"T{i}", 3.0 - i / 10, group="QB") for i in range(6)]
    wr = [row("w", "WR1", 0.3)]
    out = select_watchlist(frame([*qbs, *wr]), n_offense=4)
    assert ids(out) == ["q0", "q1", "q2", "w"]  # QB cap 3, then the receiver
    out = select_watchlist(frame([*qbs, *wr]), n_offense=6)
    assert ids(out) == ["q0", "q1", "q2", "w", "q3", "q4"]  # caps relaxed to fill
    assert ids(select_watchlist(frame(qbs), n_offense=4, group_caps=None)) == [
        "q0", "q1", "q2", "q3",
    ]  # fmt: skip
    assert GROUP_CAPS == {"QB": 3, "RB": 4, "WR/TE": 4, "EDGE/DL": 6, "LB/S": 6}


def test_followed_team_boost_breaks_close_calls_only():
    df = frame([row("a", "KC", 1.0), row("b", "DET", 0.95), row("c", "DET", 0.5)])
    assert ids(select_watchlist(df, followed=["DET"]))[:2] == ["b", "a"]
    assert 0.5 * FOLLOWED_BOOST < 1.0  # a weak signal never jumps a strong one
    assert ids(select_watchlist(df, followed=["DET"]))[2] == "c"


def test_a_linebacker_in_two_pools_appears_once_with_his_best_row():
    df = frame(
        [
            row("lb", "KC", 0.6, group="EDGE/DL", position="OLB"),
            row("lb", "KC", 1.1, group="LB/S", position="OLB"),
            row("x", "BUF", 0.8, group="EDGE/DL"),
        ]
    )
    out = select_watchlist(df)
    assert ids(out) == ["lb", "x"]
    assert out.filter(pl.col("player_id") == "lb")["target"].item() == "tackles"


def test_ties_break_on_player_id_and_empty_input_keeps_columns():
    df = frame([row("b", "KC", 1.0), row("a", "BUF", 1.0)])
    assert ids(select_watchlist(df)) == ["a", "b"]
    empty = select_watchlist(frame([row("a", "KC", -1.0)]))
    assert empty.is_empty() and {"rank", "score", "p50", "side"} <= set(empty.columns)


# ---- tough spots --------------------------------------------------------------------------------


def test_tough_spots_are_regular_starters_far_below_baseline():
    df = frame(
        [
            row("w", "KC", 1.0),
            row("t1", "BUF", -1.5),
            row("t2", "BUF", -1.0),  # same team as t1: one per team
            row("t3", "DEN", -0.8),
            row("small", "MIA", -0.1),  # not notable
            row("low", "NE", -2.0, confidence="low"),
            row("fill", "NYJ", -2.0, role_ok=False, role_change=True),
            row("w2", "LV", -3.0),
        ]
    )
    out = tough_spots(df, n=3, exclude=["w2"])
    assert ids(out) == ["t1", "t3"]
    assert out["score"].to_list() == [-1.5, -0.8] and out["side"].to_list() == ["offense"] * 2
    many = frame([row(f"t{i}", f"T{i}", -1.0 - i / 10) for i in range(7)])
    assert len(tough_spots(many)) == N_TOUGH == 5


# ---- backtest -----------------------------------------------------------------------------------


def _bt_week(week: int, outcomes: dict[str, tuple[float | None, bool]]) -> list[dict]:
    """Pool: a (z 1.0), b (z 0.5), c (z -0.5), d (z -1.0), all baseline 50."""
    zs = {"a": 1.0, "b": 0.5, "c": -0.5, "d": -1.0}
    out = []
    for pid, z in zs.items():
        actual, played = outcomes[pid]
        group = "EDGE/DL" if pid == "d" else "WR/TE"
        out.append(
            row(pid, f"T{pid}", z, group=group, week=week, game_id=f"g{week}{pid}",
                actual=actual, played=played)
        )  # fmt: skip
    return out


def test_backtest_hit_rate_against_the_base_rate():
    w1 = _bt_week(1, {"a": (70, True), "b": (40, True), "c": (60, True), "d": (30, True)})
    # week 2: a didn't play; d played but PFR hasn't published (actual null): not scored
    w2 = _bt_week(2, {"a": (None, False), "b": (55, True), "c": (20, True), "d": (None, True)})
    res = watchlist_backtest({1: frame(w1), 2: frame(w2)})
    o = res.overall
    assert (o["picks"], o["scored"], o["hits"]) == (4, 3, 2)  # a, b, a(dnp), b
    assert o["hit_rate"] == pytest.approx(2 / 3)
    assert (o["pool_scored"], o["pool_hits"]) == (6, 3)  # a b c d | b c
    assert o["base_rate"] == pytest.approx(0.5)
    assert o["lift"] == pytest.approx(2 / 3 - 0.5)
    assert o["positive_rate"] == pytest.approx(2 / 3)
    assert o["weeks"] == 2
    g = {r["group"]: r for r in res.by_group.iter_rows(named=True)}
    assert g["EDGE/DL"]["picks"] == 0 and g["EDGE/DL"]["base_rate"] == 0.0
    assert g["WR/TE"]["hit_rate"] == pytest.approx(2 / 3)
    assert res.by_season["season"].to_list() == [2025]
    assert res.summary()["watch/hit_rate"] == pytest.approx(2 / 3)
    sides = {r["side"]: r for r in res.by_side.iter_rows(named=True)}
    assert sides["offense"]["hit_rate"] == pytest.approx(2 / 3)
    assert sides["defense"]["picks"] == 0 and sides["defense"]["base_rate"] == 0.0
    assert res.summary()["watch/offense/base_rate"] == pytest.approx(3 / 5)
    assert set(res.picks["player_id"]) == {"a", "b"}
    assert res.picks.filter(pl.col("week") == 1)["inside"].to_list() == [True, True]


def test_backtest_of_nothing_is_empty():
    res = watchlist_backtest([])
    assert res.overall == {} and res.picks.is_empty()


# ---- minimum projected volume -----------------------------------------------------------------


def test_a_pick_needs_a_minimum_projected_volume_and_the_list_backfills():
    """Rotation linemen projected 0.3 pressures read oddly; the next best pick takes the slot."""
    low = row("low", "A1", 2.0, group="EDGE/DL", kind="count", mean=0.3, p50=0.0)
    ok = row("ok", "A2", 1.0, group="EDGE/DL", kind="count", mean=1.2, p50=1.0)
    lb = row("lb", "A3", 0.5, group="LB/S", kind="count", mean=1.9, p50=2.0)  # mean < 2.0
    lb_ok = row("lb_ok", "A4", 0.4, group="LB/S", kind="count", mean=2.4, p50=2.0)
    out = select_watchlist(frame([low, ok, lb, lb_ok]))
    assert ids(out) == ["ok", "lb_ok"]  # the projection is the mean for counts
    assert ids(select_watchlist(frame([low, ok, lb, lb_ok]), min_volume=None)) == [
        "low", "ok", "lb", "lb_ok",
    ]  # fmt: skip
    assert MIN_VOLUME == {"pressures": 1.0, "tackles": 2.0}
    # amounts (yards) have no floor; tough spots never use one
    wr = row("wr", "W1", 1.0, p10=0.0, p50=8.0, p90=30.0)
    assert ids(select_watchlist(frame([wr]))) == ["wr"]
    weak = row("weak", "T1", -1.0, group="EDGE/DL", kind="count", mean=0.4, p50=0.0)
    assert ids(tough_spots(frame([weak]))) == ["weak"]


def test_a_side_that_cannot_fill_its_quota_shows_fewer_and_the_backtest_counts_it():
    rows = [row(f"d{i}", f"D{i}", 1.0 - i / 10, group="EDGE/DL", kind="count",
                mean=1.5 if i < 2 else 0.5) for i in range(4)]  # fmt: skip
    out = select_watchlist(frame(rows), n_defense=3)
    assert ids(out) == ["d0", "d1"]  # the floor is never relaxed to fill
    res = watchlist_backtest(frame([{**r, "actual": 2.0, "played": True} for r in rows]),
                             n_offense=0, n_defense=3)  # fmt: skip
    assert res.short_weeks == {"offense": 0, "defense": 1}
    assert res.summary()["watch/defense/short_weeks"] == 1
