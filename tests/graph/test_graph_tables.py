"""Knowledge-graph tables (P05): as-of visibility and leakage, per relationship.

The contract is the table in `graph/tables.py`'s docstring: for the key (season S, week W, run
time T) nothing from week W on may show up as a result. The league is in `graph_world.py`:
week W is 2026 week 4, the data holds final scores / box scores / snaps for it (as a backtest
of a past week would), and the key is the Tuesday before it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from graph_world import (
    ALL_GAMES,
    BT_KEY,
    G1,
    G2,
    G3,
    GAMES_SCHEMA,
    GW,
    KEY,
    PG_SCHEMA,
    PLAYS_SCHEMA,
    Q2,
    SNAPS_SCHEMA,
    TE,
    TEAM_GAMES_SCHEMA,
    UTC,
    VISIBLE_GAMES,
    WR,
    F,
    Q,
    S,
    canon,
    differing_tables,
    frame,
    game_row,
    make_inputs,
    pairs,
    play,
)

from nflengine.graph import tables as T
from nflengine.graph.tables import GraphInputs, GraphKey, build_tables
from nflengine.paths import DataPaths

LVDEN4 = "2026_04_LV_DEN"


@pytest.fixture(scope="module")
def inp() -> GraphInputs:
    return make_inputs()


@pytest.fixture(scope="module")
def gt(inp: GraphInputs) -> T.GraphTables:
    return build_tables(inp, KEY)


@pytest.fixture(scope="module")
def gt_bt(inp: GraphInputs) -> T.GraphTables:
    return build_tables(inp, BT_KEY)


def by(df: pl.DataFrame, *keys: str) -> dict[Any, dict[str, Any]]:
    out: dict[Any, dict[str, Any]] = {}
    for r in df.iter_rows(named=True):
        k = tuple(r[c] for c in keys)
        k = k[0] if len(keys) == 1 else k
        assert k not in out, f"duplicate key {k!r}"
        out[k] = r
    return out


# ---- GraphKey -----------------------------------------------------------------------------------


def test_key_needs_a_timezone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        GraphKey(2026, 4, dt.datetime(2026, 9, 29, 14, 0))


def test_key_rejects_an_unknown_mode() -> None:
    with pytest.raises(ValueError, match="live or backtest"):
        GraphKey(2026, 4, KEY.run_time, mode="replay")


def test_key_run_date_is_the_utc_date_and_tag_is_padded() -> None:
    est = dt.timezone(dt.timedelta(hours=-5))
    k = GraphKey(2026, 4, dt.datetime(2026, 9, 29, 23, 30, tzinfo=est))
    assert k.run_date == dt.date(2026, 9, 30)  # 04:30 UTC the next day
    assert k.tag == "2026-w04"


def test_key_before_through_and_window_expressions() -> None:
    df = pl.DataFrame({"season": [2017, 2025, 2026, 2026, 2026, 2027], "week": [9, 18, 3, 4, 5, 1]})
    assert df.filter(KEY.before())["week"].to_list() == [9, 18, 3]
    assert df.filter(KEY.through())["week"].to_list() == [9, 18, 3, 4]
    assert df.filter(KEY.in_window())["season"].to_list() == [2025, 2026, 2026, 2026]


def test_key_expressions_take_other_column_names() -> None:
    df = pl.DataFrame({"yr": [2026, 2026], "wk": [3, 4]})
    assert df.filter(KEY.before("yr", "wk"))["wk"].to_list() == [3]
    assert df.filter(KEY.through("yr", "wk"))["wk"].to_list() == [3, 4]


# ---- Game nodes: week W exists, without a result ----------------------------------------------


def test_game_nodes_are_the_games_through_week_w(gt: T.GraphTables) -> None:
    assert set(gt.nodes["Game"]["game_id"]) == ALL_GAMES
    assert gt.nodes["Game"]["game_id"].is_unique().all()


def test_week_w_game_nodes_have_no_scores_and_are_not_completed(gt: T.GraphTables) -> None:
    games = by(gt.nodes["Game"], "game_id")
    for gid in (GW, LVDEN4):  # the data holds a final score and weather for both
        g = games[gid]
        assert g["home_score"] is None and g["away_score"] is None
        assert g["completed"] is False
        assert g["temp"] is None and g["wind"] is None  # weather is a result too
        assert g["kickoff"] is not None and g["stadium_id"] is not None  # the schedule stays


def test_played_games_keep_their_results(gt: T.GraphTables) -> None:
    g = by(gt.nodes["Game"], "game_id")[G1]
    assert (g["home_score"], g["away_score"], g["completed"]) == (24, 17, True)
    assert (g["temp"], g["wind"]) == (70, 5)


def test_expected_qbs_only_on_week_w_games(gt: T.GraphTables) -> None:
    games = by(gt.nodes["Game"], "game_id")
    assert (games[GW]["home_qb_expected"], games[GW]["away_qb_expected"]) == (Q2, "00-SFQB")
    assert games[G1]["home_qb_expected"] is None and games[LVDEN4]["home_qb_expected"] is None


def test_a_played_game_not_marked_completed_shows_no_result(inp: GraphInputs) -> None:
    games = inp.games.with_columns(
        pl.when(pl.col("game_id") == G1)
        .then(False)
        .otherwise(pl.col("completed"))
        .alias("completed")
    )
    out = build_tables(dataclasses.replace(inp, games=games), KEY)
    g = by(out.nodes["Game"], "game_id")[G1]
    assert g["home_score"] is None and g["completed"] is False
    kc = by(out.rels["PLAYED_IN"], "s", "e")[("KC", G1)]
    assert kc["points"] is None and kc["result"] is None
    assert G1 not in set(out.rels["APPEARED_IN"]["e"])  # no appearances either


# ---- PLAYED_IN: results only for completed games before the key --------------------------------


def test_played_in_week_w_rows_exist_but_have_no_results(gt: T.GraphTables) -> None:
    pi = gt.rels["PLAYED_IN"]
    assert pi.height == 2 * len(ALL_GAMES)  # so queries can find this week's opponent
    w = pi.filter(pl.col("week") == 4)
    assert w.height == 4
    for col in (
        "points",
        "points_allowed",
        "margin",
        "result",
        "epa_per_play",
        "success_rate",
        "plays",
        "epa_allowed",
        "success_allowed",
        "epa_margin",
        "penalties",
        "penalty_yards",
    ):
        assert w[col].null_count() == w.height, col
    assert set(w["home"].to_list()) == {True, False}  # who is home is schedule, not a result
    assert by(w, "s")["KC"]["opponent"] == "SF"


def test_played_in_results_before_the_key(gt: T.GraphTables) -> None:
    pi = by(gt.rels["PLAYED_IN"], "s", "e")
    kc = pi[("KC", G1)]
    assert (kc["points"], kc["points_allowed"], kc["margin"], kc["result"]) == (24, 17, 7, "W")
    assert kc["epa_per_play"] == pytest.approx(0.25)
    assert (kc["penalties"], kc["penalty_yards"]) == (5, 40)
    assert pi[("LV", G1)]["result"] == "L"
    assert pi[("KC", G3)]["result"] == "T"


# ---- APPEARED_IN: completed games strictly before the key ---------------------------------------


def test_appeared_in_has_only_games_before_week_w(gt: T.GraphTables) -> None:
    ai = gt.rels["APPEARED_IN"]
    assert set(ai["e"]) <= VISIBLE_GAMES
    assert not ai.filter(pl.col("week") >= 4).height
    # the data holds week-W box scores (99 targets, ...) and snaps for these players
    assert (ai["targets"].drop_nulls() < 50).all()
    assert (TE, GW) not in pairs(ai) and (Q, GW) not in pairs(ai)


def test_appeared_in_ignores_games_not_completed(inp: GraphInputs) -> None:
    games = inp.games.with_columns(
        pl.when(pl.col("game_id") == G2)
        .then(False)
        .otherwise(pl.col("completed"))
        .alias("completed")
    )
    ai = T.appeared_in(dataclasses.replace(inp, games=games), KEY, T.qb_game_stats(inp, KEY))
    assert G2 not in set(ai["e"])
    assert G1 in set(ai["e"])


# ---- PLAYED_FOR: roster rows strictly before the key --------------------------------------------


def test_played_for_ignores_roster_rows_at_or_after_the_key(gt: T.GraphTables) -> None:
    pf = gt.rels["PLAYED_FOR"]
    assert (pf["last_week"].drop_nulls() <= 3).all()
    kc = by(pf, "s", "e", "season")[(Q, "KC", 2026)]  # his week-4 roster row is not visible
    assert (kc["first_week"], kc["last_week"], kc["weeks"]) == (1, 3, 3)
    assert "00-NEW" not in set(pf["s"])  # his only roster row is week 4
    assert "00-NEW" not in set(gt.nodes["Player"]["player_id"])


def test_played_for_status_comes_from_the_latest_visible_week(gt: T.GraphTables) -> None:
    pf = by(gt.rels["PLAYED_FOR"], "s", "e", "season")
    assert pf[(WR, "KC", 2026)]["status_last"] == "RES"  # week 3; his week 4 is ACT, invisible
    assert pf[(Q, "KC", 2026)]["status_last"] == "ACT"


def test_played_for_leaves_out_statuses_that_mean_off_the_team(gt: T.GraphTables) -> None:
    pf = by(gt.rels["PLAYED_FOR"], "s", "e", "season")
    te = pf[(TE, "KC", 2026)]  # CUT in week 3
    assert (te["last_week"], te["weeks"], te["status_last"]) == (2, 2, "ACT")
    trd = pf[("00-TRD", "LV", 2026)]  # TRD in week 3 is not a week on the team
    assert (trd["last_week"], trd["weeks"]) == (2, 2)
    assert pf[("00-TRD", "KC", 2026)]["first_week"] == 3


@pytest.mark.parametrize("status", T.NOT_ON_TEAM)
def test_every_not_on_team_status_is_excluded(inp: GraphInputs, status: str) -> None:
    ros = frame(
        [
            {"gsis_id": WR, "team": "KC", "season": 2026, "week": 1, "status": "ACT"},
            {"gsis_id": WR, "team": "KC", "season": 2026, "week": 2, "status": status},
        ],
        {
            "gsis_id": S,
            "team": S,
            "season": pl.Int32,
            "week": pl.Int32,
            "status": S,
        },
    )
    none = pl.DataFrame(schema={"s": S, "e": S, "team_id": S, "season": pl.Int32})
    out = T.played_for(dataclasses.replace(inp, rosters=ros), KEY, none)
    assert out["weeks"].to_list() == [1] and out["last_week"].to_list() == [1]


def test_future_roster_rows_do_not_change_played_for(inp: GraphInputs, gt: T.GraphTables) -> None:
    ros = inp.rosters.with_columns(
        pl.when(pl.col("week") >= 4).then(pl.lit("CUT")).otherwise(pl.col("status")).alias("status")
    )
    out = build_tables(dataclasses.replace(inp, rosters=ros), KEY)
    assert canon(out.rels["PLAYED_FOR"]).equals(canon(gt.rels["PLAYED_FOR"]))


# ---- ON_INJURY_REPORT ---------------------------------------------------------------------------


def test_injury_reports_in_a_live_run(gt: T.GraphTables) -> None:
    assert pairs(gt.rels["ON_INJURY_REPORT"]) == {
        (WR, G2),  # an earlier week
        ("00-LVWR", G1),  # an earlier week, undated
        (Q, GW),  # week W, modified before the run
        ("00-LVWR", LVDEN4),  # week W, undated: a live run takes the snapshot it has
        ("00-DENDB", LVDEN4),  # week W, modified exactly at the run time
    }


def test_injury_reports_in_a_backtest_drop_undated_week_w_rows(gt_bt: T.GraphTables) -> None:
    rel = pairs(gt_bt.rels["ON_INJURY_REPORT"])
    assert ("00-LVWR", LVDEN4) not in rel
    assert ("00-LVWR", G1) in rel  # earlier weeks stay, dated or not
    assert (Q, GW) in rel and ("00-DENDB", LVDEN4) in rel
    assert len(rel) == 4


def test_injury_reports_modified_after_the_run_or_in_later_weeks_are_excluded(
    gt: T.GraphTables,
) -> None:
    rel = pairs(gt.rels["ON_INJURY_REPORT"])
    assert ("00-SFQB", GW) not in rel  # modified 2026-09-30, after the Tuesday run
    assert not any(e.startswith("2026_05") for _, e in rel)  # week 5


def test_a_week_w_report_modified_a_minute_late_is_excluded(inp: GraphInputs) -> None:
    late = BT_KEY.run_time + dt.timedelta(minutes=1)
    inj = inp.injuries.with_columns(
        pl.when(pl.col("gsis_id") == "00-DENDB")
        .then(pl.lit(late).cast(inp.injuries.schema["date_modified"]))
        .otherwise(pl.col("date_modified"))
        .alias("date_modified")
    )
    out = T.injury_reports(dataclasses.replace(inp, injuries=inj), BT_KEY)
    assert "00-DENDB" not in set(out["s"])


# ---- DEPTH_CHART --------------------------------------------------------------------------------


def _depth(gt: T.GraphTables) -> set[tuple[str, str, int, str, int]]:
    d = gt.rels["DEPTH_CHART"]
    return {(r["s"], r["e"], r["week"], r["position"], r["rank"]) for r in d.iter_rows(named=True)}


def test_depth_chart_rows(gt: T.GraphTables) -> None:
    assert _depth(gt) == {
        (Q, "KC", 3, "QB", 1),
        (Q2, "KC", 3, "QB", 2),
        (WR, "KC", 3, "WR", 1),  # rank 1 wins over the same player's rank 2 in the slot
        ("00-DENDB", "DEN", 3, "CB", 1),  # the later of two snapshots only
        (Q2, "KC", 4, "QB", 1),  # week W: the snapshot of the run date
        (Q, "KC", 4, "QB", 2),
    }


def test_depth_chart_week_w_needs_a_snapshot_dated_by_the_run_date(gt: T.GraphTables) -> None:
    d = gt.rels["DEPTH_CHART"].filter(pl.col("week") == 4)
    assert set(d["e"]) == {"KC"}  # SF's undated week-W chart is out
    # KC's WR is only on the 28th (superseded by the 29th's snapshot) and the 30th (too late)
    assert (WR, "KC", 4, "WR", 1) not in _depth(gt)


def test_depth_chart_takes_the_latest_snapshot_per_team_week(gt: T.GraphTables) -> None:
    kc4 = gt.rels["DEPTH_CHART"].filter((pl.col("e") == "KC") & (pl.col("week") == 4))
    assert kc4.height == 2 and set(kc4["s"]) == {Q, Q2}
    assert not gt.rels["DEPTH_CHART"].filter(pl.col("s") == "00-ROOK").height  # early DEN snapshot


def test_depth_chart_leaves_out_special_teams_and_blank_slots(gt: T.GraphTables) -> None:
    d = gt.rels["DEPTH_CHART"]
    assert not set(d["position"]) & set(T.SPECIAL_SLOTS)
    assert "" not in set(d["position"])
    assert TE not in set(d["s"])  # KR, LS, blank and null slots only
    assert (d["position"] == "WR").sum() == 1  # the Special Teams formation row is out


def test_depth_chart_ignores_later_weeks_even_when_dated(gt: T.GraphTables) -> None:
    assert not gt.rels["DEPTH_CHART"].filter(pl.col("week") >= 5).height


def test_depth_chart_week_w_snapshot_after_the_run_date_gives_nothing(inp: GraphInputs) -> None:
    only_late = inp.depth_charts.filter(
        (pl.col("week") != 4) | (pl.col("snap_date") > KEY.run_date)
    )
    out = T.depth_chart(dataclasses.replace(inp, depth_charts=only_late), KEY)
    assert not out.filter(pl.col("week") == 4).height


def test_depth_chart_run_date_is_in_utc(inp: GraphInputs) -> None:
    """A run at 20:00 on the 28th US-Central is the 29th in UTC: the 29th's chart is visible."""
    cst = dt.timezone(dt.timedelta(hours=-6))
    key = dataclasses.replace(KEY, run_time=dt.datetime(2026, 9, 28, 20, 0, tzinfo=cst))
    assert key.run_date == dt.date(2026, 9, 29)
    kc4 = T.depth_chart(inp, key).filter((pl.col("e") == "KC") & (pl.col("week") == 4))
    assert set(kc4["s"]) == {Q, Q2}


# ---- DRAFTED_BY / TRADED_TO ---------------------------------------------------------------------


def test_drafted_by_stops_at_the_keys_season(gt: T.GraphTables) -> None:
    rel = by(gt.rels["DRAFTED_BY"], "s")
    assert set(rel) == {Q, "00-TRD"}  # a 2027 pick and a null id are out
    assert (rel[Q]["e"], rel[Q]["year"], rel[Q]["round"], rel[Q]["pick"]) == ("KC", 2020, 1, 10)


def test_traded_to_rows(gt: T.GraphTables) -> None:
    rel = by(gt.rels["TRADED_TO"], "s")
    assert set(rel) == {"00-TRD", TE}
    trd = rel["00-TRD"]  # pfr_id -> gsis via players
    assert (trd["e"], trd["from_team"], trd["date"], trd["season"]) == (
        "KC",
        "LV",
        dt.date(2026, 9, 22),
        2026,
    )
    assert rel[TE]["date"] == dt.date(2026, 9, 29)  # a trade on the run date counts


def test_traded_to_leaves_out_draft_picks_future_and_unusable_trades(inp: GraphInputs) -> None:
    out = T.traded_to(inp, KEY)
    assert "00-PICK" not in set(out["s"])  # pick_season set: a traded draft pick
    assert Q not in set(out["s"])  # a 2026-09-30 trade (after the run) and a 2017 one
    assert out.height == 2  # undated, blank / null pfr_id and unknown pfr_id rows are out


def test_traded_to_the_day_before_the_run_date_does_not_see_that_days_trade(
    inp: GraphInputs,
) -> None:
    earlier = dataclasses.replace(KEY, run_time=dt.datetime(2026, 9, 28, 23, 59, tzinfo=UTC))
    assert set(T.traded_to(inp, earlier)["s"]) == {"00-TRD"}


# ---- TeamWeek, PublishedInsight -----------------------------------------------------------------


def test_team_week_frames_cover_what_the_loader_hands_over(gt: T.GraphTables) -> None:
    tw = gt.nodes["TeamWeek"]
    assert tw.height == 9
    row = by(tw, "key")["KC:2026:4"]  # the week-W row is built from weeks before W
    assert (row["team_id"], row["season"], row["week"]) == ("KC", 2026, 4)
    assert row["net"] == 0.3 and row["trend_dir"] == "up" and row["elo"] == 1540.0
    assert gt.rels["HAS_WEEK"].height == 9
    assert ("KC", "KC:2025:17") in pairs(gt.rels["HAS_WEEK"])


def test_next_chains_each_teams_weeks_in_order_across_seasons(gt: T.GraphTables) -> None:
    assert pairs(gt.rels["NEXT"]) == {
        ("KC:2025:17", "KC:2025:18"),
        ("KC:2025:18", "KC:2026:1"),  # across the season boundary
        ("KC:2026:1", "KC:2026:2"),
        ("KC:2026:2", "KC:2026:3"),
        ("KC:2026:3", "KC:2026:4"),
        ("SF:2026:1", "SF:2026:2"),  # never from one team to another; LV's one row has none
    }


def test_published_insight_nodes_only_for_weeks_strictly_before_the_key(gt: T.GraphTables) -> None:
    pi = gt.nodes["PublishedInsight"]
    assert sorted(pi["key"].to_list()) == [
        "2025-w17:revenge:00-X:LV",  # an earlier season
        "2026-w2:injury_ripple:00-KCWR",
        "2026-w3:qb_change:KC:00-KCQB2",  # a repeated log row collapses to one
    ]  # not week 4 (this week's earlier run) and not week 5
    row = by(pi, "key")["2026-w3:qb_change:KC:00-KCQB2"]
    assert (row["type"], row["season"], row["week"]) == ("qb_change", 2026, 3)
    assert row["entities"] == ["KC", "Walt Kane"]


def test_published_insights_at_another_key(inp: GraphInputs) -> None:
    key = dataclasses.replace(KEY, week=3)
    keys = T.published_nodes(inp, key)["key"].to_list()
    assert sorted(keys) == ["2025-w17:revenge:00-X:LV", "2026-w2:injury_ripple:00-KCWR"]


# ---- as-of invariance: the future can't change the graph ----------------------------------------


def _bump(df: pl.DataFrame, id_col: str, ids: list[str], cols: list[str]) -> pl.DataFrame:
    return df.with_columns(
        pl.when(pl.col(id_col).is_in(ids)).then(pl.col(c) * 3 + 7).otherwise(pl.col(c)).alias(c)
        for c in cols
    )


def _numeric(schema: dict[str, Any]) -> list[str]:
    return [c for c, t in schema.items() if t == F]


def _future_ids(inp: GraphInputs) -> list[str]:
    return inp.games.filter(~KEY.before())["game_id"].to_list()


def test_changing_every_future_number_changes_no_table(inp: GraphInputs, gt: T.GraphTables) -> None:
    fut = _future_ids(inp)
    assert fut == [GW, LVDEN4]
    ngs = {
        k: df.with_columns(
            pl.when(pl.col("week") >= 4).then(pl.col(c) * 3 + 7).otherwise(pl.col(c)).alias(c)
            for c in df.columns
            if c.startswith(("avg_", "rush_"))
        )
        for k, df in inp.ngs.items()
    }
    bumped = dataclasses.replace(
        inp,
        games=_bump(inp.games, "game_id", fut, ["home_score", "away_score", "temp", "wind"]),
        player_games=_bump(inp.player_games, "game_id", fut, _numeric(PG_SCHEMA)),
        snaps=_bump(inp.snaps, "game_id", fut, _numeric(SNAPS_SCHEMA)),
        pfr_def=_bump(inp.pfr_def, "game_id", fut, ["def_pressures"]),
        ngs=ngs,
    )
    assert not bumped.games.equals(inp.games) and not bumped.player_games.equals(inp.player_games)
    assert differing_tables(gt, build_tables(bumped, KEY)) == []


def test_removing_the_future_results_changes_no_table(inp: GraphInputs, gt: T.GraphTables) -> None:
    """What a live Tuesday run has: week W scheduled, not played, no box scores yet."""
    fut = _future_ids(inp)
    unplayed = inp.games.with_columns(
        pl.when(pl.col("game_id").is_in(fut)).then(None).otherwise(pl.col(c)).alias(c)
        for c in ("home_score", "away_score", "temp", "wind")
    ).with_columns(
        pl.when(pl.col("game_id").is_in(fut))
        .then(False)
        .otherwise(pl.col("completed"))
        .alias("completed")
    )
    live = dataclasses.replace(
        inp,
        games=unplayed,
        player_games=inp.player_games.filter(~pl.col("game_id").is_in(fut)),
        snaps=inp.snaps.filter(~pl.col("game_id").is_in(fut)),
        ngs={k: df.filter(pl.col("week") < 4) for k, df in inp.ngs.items()},
    )
    assert differing_tables(gt, build_tables(live, KEY)) == []


def test_the_graph_at_week_w_is_not_the_graph_at_week_w_plus_one(inp: GraphInputs) -> None:
    """Sanity: the invariance tests above would catch a leak because the later key does see
    week W's results."""
    later = dataclasses.replace(KEY, week=5)
    out = build_tables(inp, later)
    assert GW in set(out.rels["APPEARED_IN"]["e"])
    assert by(out.nodes["Game"], "game_id")[GW]["home_score"] == 30


# ---- leaks the builders don't stop on their own (the loader is meant to) -----------------------


def test_team_games_rows_of_future_games_do_not_reach_played_in(inp: GraphInputs) -> None:
    """A backtest's `team_games` holds week W's penalties; `load_inputs` filters that table by
    season only and `played_in` doesn't mask the join."""
    tg = pl.concat(
        [
            inp.team_games,
            frame(
                [
                    {"game_id": GW, "team": "KC", "penalties": 9.0, "penalty_yards": 90.0},
                    {"game_id": GW, "team": "SF", "penalties": 1.0, "penalty_yards": 5.0},
                ],
                TEAM_GAMES_SCHEMA,
            ),
        ]
    )
    pi = T.played_in(dataclasses.replace(inp, team_games=tg), KEY)
    w = pi.filter(pl.col("e") == GW)
    assert w["penalties"].null_count() == w.height
    assert w["penalty_yards"].null_count() == w.height


def test_plays_at_week_w_do_not_reach_played_in(inp: GraphInputs) -> None:
    extra = frame([play(GW, "KC", "SF", "pass", 0.9, passer=Q, receiver=WR, yds=10)], PLAYS_SCHEMA)
    pi = T.played_in(dataclasses.replace(inp, plays=pl.concat([inp.plays, extra])), KEY)
    w = pi.filter(pl.col("e") == GW)
    assert w["epa_per_play"].null_count() == w.height


def test_plays_at_week_w_do_not_reach_threw_to(inp: GraphInputs) -> None:
    extra = frame([play(GW, "KC", "SF", "pass", 0.9, passer=Q, receiver=WR, yds=10)], PLAYS_SCHEMA)
    out = T.threw_to(dataclasses.replace(inp, plays=pl.concat([inp.plays, extra])), KEY)
    row = out.filter((pl.col("s") == Q) & (pl.col("e") == WR) & (pl.col("season") == 2026))
    assert row["targets"].to_list() == [3]


def test_games_after_week_w_are_not_nodes_even_if_the_inputs_hold_them(inp: GraphInputs) -> None:
    later = frame([game_row(2026, 5, "DEN", "KC", 30, 3)], GAMES_SCHEMA)
    custom = dataclasses.replace(inp, games=pl.concat([inp.games, later]))
    assert "2026_05_DEN_KC" not in set(T.game_nodes(custom, KEY)["game_id"])


# ---- the loader's filters, on parquet files in a temp dir ---------------------------------------


def _write(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)


def test_team_week_inputs_stop_at_the_key_and_start_at_the_graph_window(tmp_path: Path) -> None:
    feats = DataPaths(tmp_path).features
    keys = {"season": [2017, 2025, 2026, 2026, 2026, 2026], "week": [5, 18, 3, 4, 5, 6]}
    n = len(keys["week"])
    team = {"team": ["KC"] * n}
    epa_cols = (
        "off_epa",
        "def_epa",
        "net_epa",
        "off_pass_epa",
        "off_rush_epa",
        "def_pass_epa",
        "def_rush_epa",
    )
    _write(
        pl.DataFrame(keys | team | {c: [0.1] * n for c in epa_cols}), feats / "team_ratings.parquet"
    )
    _write(pl.DataFrame(keys | team | {"elo": [1500.0] * n}), feats / "team_elo.parquet")
    trends = {
        "trend_delta": [0.0] * n,
        "direction": ["up"] * n,
        "perf_vs_expected": [0.0] * n,
        "net_epa_prev": [0.0] * n,
    }
    _write(pl.DataFrame(keys | team | trends), feats / "team_trends.parquet")
    out = T.team_week_inputs(DataPaths(tmp_path), KEY)
    assert sorted(zip(out["season"].to_list(), out["week"].to_list(), strict=True)) == [
        (2025, 18),
        (2026, 3),
        (2026, 4),  # week W is in: its row is built from weeks before W
    ]  # not 2017 (before the graph's seasons), not 2026 weeks 5-6
    assert {"elo", "trend_delta", "direction"} <= set(out.columns)


def test_team_week_inputs_without_ratings_is_empty(tmp_path: Path) -> None:
    assert T.team_week_inputs(DataPaths(tmp_path), KEY).is_empty()


def test_expected_qbs_reads_the_keys_week_in_a_backtest(tmp_path: Path) -> None:
    gf = pl.DataFrame(
        {
            "season": [2026, 2026, 2026],
            "week": [3, 4, 4],
            "game_id": ["g3", "g4a", "g4b"],
            "home_team": ["KC", "KC", "DEN"],
            "away_team": ["SF", "SF", "LV"],
            "home_qb_id": ["a", "b", "c"],
            "away_qb_id": ["d", "e", None],
        }
    )
    _write(gf, DataPaths(tmp_path).features / "game_features.parquet")
    out = T.expected_qbs(DataPaths(tmp_path), BT_KEY, lambda _m: None)
    assert out.columns == [
        "game_id",
        "home_qb_expected",
        "away_qb_expected",
        "home_qb_source",
        "away_qb_source",
    ]
    assert sorted(out["game_id"].to_list()) == ["g4a", "g4b"]
    assert by(out, "game_id")["g4a"]["home_qb_expected"] == "b"
    assert by(out, "game_id")["g4b"]["away_qb_expected"] is None


def test_expected_qbs_without_game_features_is_empty(tmp_path: Path) -> None:
    out = T.expected_qbs(DataPaths(tmp_path), KEY, lambda _m: None)
    assert out.is_empty() and out.columns[:3] == [
        "game_id",
        "home_qb_expected",
        "away_qb_expected",
    ]


def test_plays_loader_keeps_only_visible_seasons_and_weeks(tmp_path: Path) -> None:
    plays_dir = DataPaths(tmp_path).curated / "plays"
    for season in (2017, 2025, 2026, 2027):
        weeks = [3, 4, 5] if season == 2026 else [17]
        n = len(weeks)
        df = pl.DataFrame(
            {
                "season": [season] * n,
                "week": weeks,
                "game_id": [f"{season}_{w}" for w in weeks],
                "epa": [0.1] * n,
                "unused": [1] * n,
            }
        )
        _write(df, plays_dir / f"season={season}.parquet")
    out = T._plays(DataPaths(tmp_path), KEY, ["season", "week", "game_id", "epa", "absent_col"])
    assert sorted(out["game_id"].to_list()) == ["2025_17", "2026_3"]  # not 2026 wk 4+, 2017, 2027
    assert out.columns == ["season", "week", "game_id", "epa"]  # unknown columns ignored


def test_plays_loader_with_no_files_is_empty(tmp_path: Path) -> None:
    assert T._plays(DataPaths(tmp_path), KEY, ["season", "week"]).is_empty()


def test_scan_of_a_missing_table_is_none(tmp_path: Path) -> None:
    assert T._scan(DataPaths(tmp_path), "nope") is None
    _write(pl.DataFrame({"a": [1]}), DataPaths(tmp_path).curated / "yes.parquet")
    lf = T._scan(DataPaths(tmp_path), "yes")
    assert lf is not None and lf.collect().height == 1
