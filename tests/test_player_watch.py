"""Watch-list selection from player projections (plan P06; models/player_watch.py)."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from nflengine.models.player_schema import MAIN_TARGETS, PRED_SCHEMA, conform
from nflengine.models.player_watch import (
    FOLLOWED_BOOST,
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
    assert out["rank"].to_list() == [1, 2]
    assert set(PRED_SCHEMA) <= set(out.columns) and {"rank", "score"} <= set(out.columns)


def test_at_most_two_per_team():
    df = frame([row(f"k{i}", "KC", 2.0 - i / 10, group=g) for i, g in enumerate(
        ["WR/TE", "RB", "QB", "LB/S"])] + [row("b", "BUF", 0.1)])  # fmt: skip
    assert ids(select_watchlist(df)) == ["k0", "k1", "b"]


def test_group_cap_keeps_variety_but_relaxes_when_the_list_would_be_short():
    qbs = [row(f"q{i}", f"T{i}", 3.0 - i / 10, group="QB") for i in range(6)]
    wr = [row("w", "WR1", 0.3)]
    out = select_watchlist(frame([*qbs, *wr]), n_items=4)
    assert ids(out) == ["q0", "q1", "q2", "w"]  # 3 QBs, then the receiver
    out = select_watchlist(frame([*qbs, *wr]), n_items=6)
    assert ids(out) == ["q0", "q1", "q2", "w", "q3", "q4"]  # caps relaxed to fill
    assert ids(select_watchlist(frame(qbs), n_items=4, max_per_group=None)) == [
        "q0", "q1", "q2", "q3",
    ]  # fmt: skip


def test_at_most_three_defenders_unless_the_list_would_be_short():
    d = [row(f"d{i}", f"D{i}", 3.0 - i / 10, group=("EDGE/DL", "LB/S")[i % 2]) for i in range(5)]
    o = [row("q", "Q1", 0.4, group="QB"), row("w", "W1", 0.3)]
    assert ids(select_watchlist(frame([*d, *o]), n_items=5)) == ["d0", "d1", "d2", "q", "w"]
    assert ids(select_watchlist(frame([*d, *o]), n_items=6)) == ["d0", "d1", "d2", "q", "w", "d3"]
    assert len(select_watchlist(frame(d), n_items=5, max_defense=None)) == 5


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
            row("x", "BUF", 0.8),
        ]
    )
    out = select_watchlist(df)
    assert ids(out) == ["lb", "x"]
    assert out.filter(pl.col("player_id") == "lb")["target"].item() == "tackles"


def test_ties_break_on_player_id_and_empty_input_keeps_columns():
    df = frame([row("b", "KC", 1.0), row("a", "BUF", 1.0)])
    assert ids(select_watchlist(df)) == ["a", "b"]
    empty = select_watchlist(frame([row("a", "KC", -1.0)]))
    assert empty.is_empty() and {"rank", "score", "p50"} <= set(empty.columns)


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
    assert out["score"].to_list() == [-1.5, -0.8]


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
    assert set(res.picks["player_id"]) == {"a", "b"}
    assert res.picks.filter(pl.col("week") == 1)["inside"].to_list() == [True, True]


def test_backtest_of_nothing_is_empty():
    res = watchlist_backtest([])
    assert res.overall == {} and res.picks.is_empty()
