"""Knowledge-graph tables (P05): what each builder produces for the visible data.

Visibility / leakage lives in test_graph_tables.py; these tests pin the builders' own logic
(joins, dedupes, aggregates, property values) on the league in `graph_world.py`.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import polars as pl
import pytest
from graph_world import (
    ALL_GAMES,
    BT_KEY,
    G1,
    G2,
    G3,
    GSF1,
    GW,
    I64,
    INJURIES_SCHEMA,
    KEY,
    PLAYS_SCHEMA,
    Q2,
    SNAPS_SCHEMA,
    TE,
    TEAM_GAMES_SCHEMA,
    WR,
    Q,
    S,
    differing_tables,
    frame,
    make_inputs,
    pairs,
)

from nflengine.graph import tables as T
from nflengine.graph.tables import GraphInputs, build_tables


@pytest.fixture(scope="module")
def inp() -> GraphInputs:
    return make_inputs()


@pytest.fixture(scope="module")
def gt(inp: GraphInputs) -> T.GraphTables:
    return build_tables(inp, KEY)


def by(df: pl.DataFrame, *keys: str) -> dict[Any, dict[str, Any]]:
    out: dict[Any, dict[str, Any]] = {}
    for r in df.iter_rows(named=True):
        k = tuple(r[c] for c in keys)
        k = k[0] if len(keys) == 1 else k
        assert k not in out, f"duplicate key {k!r}"
        out[k] = r
    return out


# ---- small helpers ------------------------------------------------------------------------------


def test_slug() -> None:
    assert T.slug("Andy Reid") == "andy-reid"
    assert T.slug("Dan O'Brien Jr.") == "dan-o-brien-jr"
    assert T.slug("  Mike  McCarthy ") == "mike-mccarthy"


def test_position_groups_and_unknown_positions() -> None:
    df = pl.DataFrame({"position": ["QB", "FB", "OT", "EDGE", "SS", "K", "XX", None]})
    got = df.select(T.position_group_expr("position"))["position"].to_list()
    assert got == ["QB", "RB", "OL", "DL", "DB", "SPEC", None, None]


def test_keep_known_drops_rows_with_unknown_endpoints_and_counts_them() -> None:
    rels = pl.DataFrame({"s": ["a", "a", "b", "z"], "e": ["x", "y", "x", "x"], "w": [1, 2, 3, 4]})
    dropped: dict[str, int] = {}
    out = T._keep_known(rels, pl.Series(["a", "b"]), pl.Series(["x"]), "REL", dropped)
    assert out["w"].to_list() == [1, 3]  # (a, y): unknown end; (z, x): unknown start
    assert dropped == {"REL": 2}


def test_keep_known_with_nothing_to_drop_records_zero() -> None:
    rels = pl.DataFrame({"s": ["a"], "e": ["x"]})
    dropped: dict[str, int] = {}
    out = T._keep_known(rels, pl.Series(["a", "a"]), pl.Series(["x"]), "REL", dropped)
    assert out.height == 1 and dropped == {"REL": 0}


def test_expected_counts_lists_every_node_and_relationship_table() -> None:
    gt = T.GraphTables(KEY)
    gt.nodes = {"Team": pl.DataFrame({"team_id": ["KC", "LV"]})}
    gt.rels = {"NEXT": pl.DataFrame({"s": ["a"], "e": ["b"]}), "AT": pl.DataFrame()}
    assert gt.expected_counts() == {"node:Team": 2, "rel:NEXT": 1, "rel:AT": 0}


def test_node_and_relationship_registries_are_consistent() -> None:
    assert set(T.NODE_ORDER) == set(T.NODE_KEYS)
    assert tuple(T.REL_ENDS) == T.REL_ORDER
    for start, end in T.REL_ENDS.values():
        assert start in T.NODE_KEYS and end in T.NODE_KEYS


# ---- PLAYED_IN ----------------------------------------------------------------------------------


def test_played_in_win_with_play_level_numbers(gt: T.GraphTables) -> None:
    kc = by(gt.rels["PLAYED_IN"], "s", "e")[("KC", G1)]
    assert (kc["home"], kc["opponent"]) == (True, "LV")
    assert (kc["points"], kc["points_allowed"], kc["margin"], kc["result"]) == (24, 17, 7, "W")
    assert kc["epa_per_play"] == pytest.approx(0.25)  # 6 plays: the two-pointer, no-play out
    assert kc["success_rate"] == pytest.approx(4 / 6)
    assert kc["plays"] == 6
    assert kc["epa_allowed"] == pytest.approx(-0.2)  # LV's two runs
    assert kc["epa_margin"] == pytest.approx(0.45)
    assert (kc["penalties"], kc["penalty_yards"]) == (5, 40)


def test_played_in_loss_and_tie(gt: T.GraphTables) -> None:
    pi = by(gt.rels["PLAYED_IN"], "s", "e")
    lv = pi[("LV", G1)]
    assert (lv["home"], lv["points"], lv["margin"], lv["result"]) == (False, 17, -7, "L")
    assert lv["epa_margin"] == pytest.approx(-0.45)
    assert (pi[("KC", G3)]["result"], pi[("DEN", G3)]["result"]) == ("T", "T")
    assert pi[("KC", G3)]["margin"] == 0


def test_played_in_games_without_plays_or_team_games_keep_their_scores(gt: T.GraphTables) -> None:
    row = by(gt.rels["PLAYED_IN"], "s", "e")[("LV", "2026_02_DEN_LV")]
    assert (row["points"], row["result"]) == (13, "L")
    assert row["epa_per_play"] is None and row["penalties"] is None


def test_played_in_without_plays_or_team_games_has_only_scores(inp: GraphInputs) -> None:
    bare = dataclasses.replace(
        inp, plays=frame([], PLAYS_SCHEMA), team_games=frame([], TEAM_GAMES_SCHEMA)
    )
    pi = T.played_in(bare, KEY)
    assert "epa_per_play" not in pi.columns and "penalties" not in pi.columns
    assert by(pi, "s", "e")[("KC", G1)]["margin"] == 7


# ---- APPEARED_IN --------------------------------------------------------------------------------


def test_appeared_in_is_a_full_join_of_box_scores_and_snaps(gt: T.GraphTables) -> None:
    ai = gt.rels["APPEARED_IN"]
    assert ai.height == 22  # 18 box rows + 5 snaps-only pairs - the unknown player's row
    rows = by(ai, "s", "e")
    te = rows[(TE, G1)]  # snaps only: a blocking TE
    assert te["targets"] is None and te["snaps"] == 40
    lvwr = rows[("00-LVWR", G1)]  # box score only
    # no snap record: snaps unknown (null), not 0, so Q2 can't read it as a missed game
    assert lvwr["targets"] == 6 and lvwr["snaps"] is None and lvwr["snap_pct"] is None
    wr = rows[(WR, G1)]  # both sources
    assert (wr["targets"], wr["rec"], wr["rec_yds"], wr["rec_tds"]) == (8, 5, 60, 1)
    assert (wr["off_snaps"], wr["snap_pct"], wr["target_share"]) == (55, 0.8, 0.25)


def test_appeared_in_dedupes_snaps_rows_keeping_the_biggest(gt: T.GraphTables) -> None:
    ai = gt.rels["APPEARED_IN"].filter((pl.col("s") == TE) & (pl.col("e") == G1))
    assert ai.height == 1
    row = ai.row(0, named=True)
    assert (row["off_snaps"], row["st_snaps"], row["snap_pct"]) == (40, 5, 0.6)


def test_appeared_in_snaps_are_offense_plus_defense_without_special_teams(
    gt: T.GraphTables,
) -> None:
    row = by(gt.rels["APPEARED_IN"], "s", "e")[("00-DENDB", G3)]
    assert (row["def_snaps"], row["st_snaps"], row["snaps"]) == (40, 8, 40)


def test_snap_pct_is_the_larger_of_offense_and_defense(inp: GraphInputs) -> None:
    snaps = frame(
        [
            {"gsis_id": WR, "game_id": G1, "team": "KC", "offense_pct": 0.2, "defense_pct": 0.7},
            {"gsis_id": TE, "game_id": G1, "team": "KC", "offense_pct": 0.5, "defense_pct": None},
            {"gsis_id": Q, "game_id": G1, "team": "KC", "offense_pct": None, "defense_pct": None},
        ],
        SNAPS_SCHEMA,
    )
    custom = dataclasses.replace(inp, snaps=snaps)
    ai = by(T.appeared_in(custom, KEY, T.qb_game_stats(custom, KEY)), "s", "e")
    assert ai[(WR, G1)]["snap_pct"] == 0.7
    assert ai[(TE, G1)]["snap_pct"] == 0.5
    assert ai[(Q, G1)]["snap_pct"] == 0.0


def test_appeared_in_stat_columns(gt: T.GraphTables) -> None:
    ai = by(gt.rels["APPEARED_IN"], "s", "e")
    assert ai[(Q, G1)]["epa"] == pytest.approx(5.5)
    assert ai[(WR, G3)]["epa"] == pytest.approx(1.5)  # passing + rushing + receiving, nulls as 0
    qb = ai[(Q, G1)]
    assert (qb["pass_att"], qb["completions"], qb["pass_yds"], qb["pass_tds"], qb["ints"]) == (
        30,
        20,
        250,
        2,
        1,
    )
    db = ai[("00-DENDB", GSF1)]
    assert (db["sacks"], db["qb_hits"], db["tackles"], db["passes_defended"]) == (1, 2, 5, 1)
    assert db["pressures"] == 3  # two PFR rows summed; the row without an id ignored
    assert ai[("00-DENDB", G3)]["pressures"] is None


def test_appeared_in_takes_the_team_from_the_box_score_else_snaps(gt: T.GraphTables) -> None:
    ai = by(gt.rels["APPEARED_IN"], "s", "e")
    assert ai[("00-TRD", G1)]["team_id"] == "LV" and ai[("00-TRD", G3)]["team_id"] == "KC"
    assert ai[("00-DENDB", G3)]["team_id"] == "DEN"  # snaps only
    assert (ai[(WR, G1)]["season"], ai[(WR, G1)]["week"]) == (2026, 1)


def test_appeared_in_ngs_join_is_by_player_season_and_week(gt: T.GraphTables) -> None:
    ai = by(gt.rels["APPEARED_IN"], "s", "e")
    assert ai[(WR, G1)]["ngs_separation"] == 3.1
    assert ai[(WR, G3)]["ngs_separation"] is None  # no week-3 row (week 4's is not joined)
    assert ai[(Q, G1)]["ngs_time_to_throw"] == 2.7 and ai[(Q, G2)]["ngs_time_to_throw"] == 2.5
    assert "ngs_ryoe" not in gt.rels["APPEARED_IN"].columns  # empty NGS table: no column


def test_appeared_in_qb_started_is_the_main_qb_of_the_game(gt: T.GraphTables) -> None:
    ai = by(gt.rels["APPEARED_IN"], "s", "e")
    assert ai[(Q, G2)]["qb_started"] is True and ai[(Q2, G2)]["qb_started"] is False
    assert ai[(Q2, G3)]["qb_started"] is True  # 3 dropbacks to Q's 1
    assert ai[(Q, G3)]["qb_started"] is False
    assert ai[(WR, G1)]["qb_started"] is False
    assert (ai[(Q, G1)]["dropbacks"], ai[(Q2, G3)]["dropbacks"]) == (5, 3)


def test_appeared_in_skips_rows_without_a_player_id_and_unknown_players(
    gt: T.GraphTables,
) -> None:
    ai = gt.rels["APPEARED_IN"]
    assert ai["s"].null_count() == 0
    assert "00-NONAME" not in set(ai["s"])  # nobody can name him: dropped at the end


def test_appeared_in_works_without_starters(inp: GraphInputs) -> None:
    empty = pl.DataFrame(
        schema={"pid": S, "game_id": S, "dropbacks": I64, "qb_started": pl.Boolean}
    )
    assert "qb_started" not in T.appeared_in(inp, KEY, empty).columns


def test_qb_game_stats_without_plays_is_empty_with_a_schema(inp: GraphInputs) -> None:
    out = T.qb_game_stats(dataclasses.replace(inp, plays=frame([], PLAYS_SCHEMA)), KEY)
    assert out.is_empty()
    assert out.columns == ["pid", "game_id", "dropbacks", "qb_started"]


def test_qb_game_stats_main_qb_is_the_one_with_most_dropbacks(inp: GraphInputs) -> None:
    out = by(T.qb_game_stats(inp, KEY), "pid", "game_id")
    assert out[(Q, G2)]["qb_started"] and not out[(Q2, G2)]["qb_started"]
    assert out[(Q2, G3)]["qb_started"] and not out[(Q, G3)]["qb_started"]
    assert out[(Q, G1)]["dropbacks"] == 5  # sack and scramble count; two-pointer, no-play don't


# ---- PLAYED_FOR ---------------------------------------------------------------------------------


def test_played_for_rows(gt: T.GraphTables) -> None:
    pf = by(gt.rels["PLAYED_FOR"], "s", "e", "season")
    assert pf[(Q, "KC", 2025)]["games"] == 1
    kc = pf[(Q, "KC", 2026)]
    assert (kc["first_week"], kc["last_week"], kc["weeks"], kc["games"]) == (1, 3, 3, 3)
    assert kc["status_last"] == "ACT"


def test_played_for_counts_each_week_once(gt: T.GraphTables) -> None:
    wr = by(gt.rels["PLAYED_FOR"], "s", "e", "season")[(WR, "KC", 2026)]
    assert (wr["first_week"], wr["last_week"], wr["weeks"]) == (1, 3, 3)  # two rows in week 2


def test_played_for_counts_games_from_appearances(gt: T.GraphTables) -> None:
    pf = by(gt.rels["PLAYED_FOR"], "s", "e", "season")
    assert pf[(WR, "KC", 2026)]["games"] == 2  # week 1 (box + snaps) and week 3 (box)
    assert pf[(TE, "KC", 2026)]["games"] == 2  # snaps only, one deduped row per game
    assert pf[("00-SFQB", "SF", 2026)]["games"] == 3
    assert pf[("00-ROOK", "KC", 2026)]["games"] == 0  # on the roster, never appeared


def test_played_for_keeps_appearances_without_roster_rows(gt: T.GraphTables) -> None:
    db = by(gt.rels["PLAYED_FOR"], "s", "e", "season")[("00-DENDB", "DEN", 2026)]
    assert db["games"] == 3
    assert db["first_week"] is None and db["weeks"] is None and db["status_last"] is None


def test_played_for_one_row_per_team_season_for_a_traded_player(gt: T.GraphTables) -> None:
    rows = gt.rels["PLAYED_FOR"].filter(pl.col("s") == "00-TRD")
    assert sorted(rows["e"].to_list()) == ["KC", "LV"]


def test_played_for_ignores_roster_rows_without_player_or_team(gt: T.GraphTables) -> None:
    sfqb = by(gt.rels["PLAYED_FOR"], "s", "e", "season")[("00-SFQB", "SF", 2026)]
    assert (sfqb["last_week"], sfqb["weeks"]) == (2, 2)  # the week-3 row has no team
    assert gt.rels["PLAYED_FOR"]["s"].null_count() == 0


# ---- THREW_TO and scramble rates ----------------------------------------------------------------


def test_threw_to_aggregates_per_passer_receiver_season(gt: T.GraphTables) -> None:
    tt = by(gt.rels["THREW_TO"], "s", "e", "season")
    a = tt[(Q, WR, 2026)]  # week 1: two completions; week 3: one
    assert (a["targets"], a["completions"], a["yards"], a["tds"], a["games_together"]) == (
        3,
        3,
        53,
        1,
        2,
    )
    assert a["epa"] == pytest.approx(2.7) and a["team_id"] == "KC"
    b = tt[(Q, TE, 2026)]
    assert (b["targets"], b["completions"], b["yards"], b["games_together"]) == (3, 1, 12, 2)
    assert b["epa"] == pytest.approx(-0.3)
    assert tt[(Q, WR, 2025)]["targets"] == 2  # another season, another row
    assert tt[(Q2, TE, 2026)]["targets"] == 1 and tt[(Q2, WR, 2026)]["targets"] == 2


def test_threw_to_leaves_out_sacks_two_point_tries_and_non_pass_plays(inp: GraphInputs) -> None:
    raw = T.threw_to(inp, KEY)
    # week 1 has a sack and a scramble (no receiver), a two-point try to the WR and a no_play
    # to the WR: none counts, so Q -> WR in 2026 is 3 targets (2 in week 1, 1 in week 3)
    assert by(raw, "s", "e", "season")[(Q, WR, 2026)]["targets"] == 3
    assert raw.height == 6  # incl. SF's pass to a receiver nobody knows


def test_threw_to_unknown_receiver_is_dropped_by_the_endpoint_check(
    gt: T.GraphTables, inp: GraphInputs
) -> None:
    assert ("00-SFQB", "00-NOBODY") in pairs(T.threw_to(inp, KEY))
    assert ("00-SFQB", "00-NOBODY") not in pairs(gt.rels["THREW_TO"])
    assert gt.dropped["THREW_TO"] == 1 and gt.rels["THREW_TO"].height == 5


def test_threw_to_without_plays_is_empty_with_a_schema(inp: GraphInputs) -> None:
    out = T.threw_to(dataclasses.replace(inp, plays=frame([], PLAYS_SCHEMA)), KEY)
    assert out.is_empty() and out.columns == ["s", "e", "season"]


def test_scramble_rates(gt: T.GraphTables, inp: GraphInputs) -> None:
    sr = by(T.scramble_rates(inp.plays), "player_id")
    # Q: 2 dropbacks in 2025, 5 in week 1 (3 passes, a sack, a scramble), 3 in week 2, 1 in week 3
    assert sr[Q]["dropbacks"] == 11 and sr[Q]["scramble_rate"] == pytest.approx(2 / 11)
    assert sr[Q2]["dropbacks"] == 4 and sr[Q2]["scramble_rate"] == pytest.approx(0.25)
    assert sr["00-SFQB"]["dropbacks"] == 1 and sr["00-SFQB"]["scramble_rate"] == 0.0
    players = by(gt.nodes["Player"], "player_id")
    assert players[Q]["dropbacks"] == 11
    assert players[WR]["scramble_rate"] is None  # not a QB


def test_scramble_rates_without_plays() -> None:
    out = T.scramble_rates(frame([], PLAYS_SCHEMA))
    assert out.is_empty() and out.columns == ["player_id", "dropbacks", "scramble_rate"]


# ---- OFFICIATED ---------------------------------------------------------------------------------


def test_officials_map_through_old_game_ids(inp: GraphInputs) -> None:
    gt_bt = build_tables(inp, BT_KEY)
    rel = gt_bt.rels["OFFICIATED"]
    assert pairs(rel) == {("101", G1), ("102", G1), ("101", G3)}  # ids as strings; no dup row
    assert by(rel, "s", "e")[("102", G1)]["role"] == "U"
    # a backtest never sees the week-W crew (103); the row without an id is never there
    assert set(gt_bt.nodes["Official"]["official_id"]) == {"101", "102"}
    assert by(gt_bt.nodes["Official"], "official_id")["101"]["name"] == "Ref One"


def test_a_live_run_sees_a_week_w_crew_the_snapshot_has(gt: T.GraphTables) -> None:
    """P08 (Q9): crews are published after their games; a live run takes a week-W crew when
    the snapshot has one, a backtest never does (above: no Tuesday run could have had it)."""
    rel = gt.rels["OFFICIATED"]
    assert pairs(rel) == {("101", G1), ("102", G1), ("101", G3), ("103", GW)}
    assert "103" in set(gt.nodes["Official"]["official_id"])


def test_officials_without_a_table_are_empty(inp: GraphInputs) -> None:
    nodes, rel = T.officiated(dataclasses.replace(inp, officials=pl.DataFrame()), KEY)
    assert nodes.is_empty() and rel.is_empty()
    assert nodes.columns == ["official_id", "name"] and rel.columns == ["s", "e", "role"]


# ---- ON_INJURY_REPORT / DEPTH_CHART / DRAFTED_BY / TRADED_TO properties -------------------------


def test_injury_report_properties_and_one_row_per_player_game(gt: T.GraphTables) -> None:
    rel = by(gt.rels["ON_INJURY_REPORT"], "s", "e")
    wr = rel[(WR, G2)]  # two reports for the same game: the later row stays
    assert (wr["status"], wr["practice_status"], wr["body_part"]) == (
        "Out",
        "Did Not Participate",
        "Knee",
    )
    assert wr["team_id"] == "KC" and (wr["season"], wr["week"]) == (2026, 2)
    assert wr["report_date"].isoformat() == "2026-09-18T12:00:00+00:00"
    q = rel[(Q, GW)]
    assert (q["status"], q["body_part"]) == ("Questionable", "Ankle")


def test_injury_reports_skip_rows_without_a_player_or_a_matching_game(gt: T.GraphTables) -> None:
    rel = gt.rels["ON_INJURY_REPORT"]
    assert rel["s"].null_count() == 0
    assert (TE, G2) not in pairs(rel)  # team XXX had no game that week


def test_empty_injury_table(inp: GraphInputs) -> None:
    out = T.injury_reports(dataclasses.replace(inp, injuries=frame([], INJURIES_SCHEMA)), KEY)
    assert out.is_empty() and out.columns == ["s", "e"]


def test_empty_depth_chart(inp: GraphInputs) -> None:
    empty = inp.depth_charts.clear()
    out = T.depth_chart(dataclasses.replace(inp, depth_charts=empty), KEY)
    assert out.is_empty() and out.columns == ["s", "e"]


# ---- TeamWeek / predictions ---------------------------------------------------------------------


def test_team_week_without_optional_columns(inp: GraphInputs) -> None:
    slim = inp.team_weeks.drop(
        "elo", "trend_delta", "direction", "perf_vs_expected", "net_epa_prev"
    )
    nodes, has, nxt = T.team_week_frames(dataclasses.replace(inp, team_weeks=slim))
    assert "trend_dir" not in nodes.columns and "elo" not in nodes.columns
    assert has.height == 9 and nxt.height == 6


def test_team_week_nodes_are_sorted_by_team_season_week(inp: GraphInputs) -> None:
    nodes, _, _ = T.team_week_frames(inp)  # the input rows are shuffled
    assert nodes["key"].to_list() == [
        "KC:2025:17",
        "KC:2025:18",
        "KC:2026:1",
        "KC:2026:2",
        "KC:2026:3",
        "KC:2026:4",
        "LV:2026:4",
        "SF:2026:1",
        "SF:2026:2",
    ]


def test_team_week_empty(inp: GraphInputs) -> None:
    nodes, has, nxt = T.team_week_frames(dataclasses.replace(inp, team_weeks=pl.DataFrame()))
    assert nodes.is_empty() and has.is_empty() and nxt.is_empty()


def test_published_insights_empty(inp: GraphInputs) -> None:
    out = T.published_nodes(dataclasses.replace(inp, published=pl.DataFrame()), KEY)
    assert out.is_empty() and out.columns == ["key"]


def test_prediction_nodes_and_relationship(gt: T.GraphTables) -> None:
    gp = by(gt.nodes["GamePrediction"], "key")
    assert set(gp) == {f"{GW}|v0|main", f"{GW}|v0|alt"}
    assert gp[f"{GW}|v0|main"]["confidence"] == "lean" and gp[f"{GW}|v0|main"]["is_primary"]
    assert pairs(gt.rels["HAS_PREDICTION"]) == {(GW, f"{GW}|v0|main"), (GW, f"{GW}|v0|alt")}


def test_predictions_empty(inp: GraphInputs) -> None:
    nodes, rel = T.prediction_frames(dataclasses.replace(inp, predictions=pl.DataFrame()))
    assert nodes.is_empty() and rel.is_empty()


# ---- nodes: teams, coaches, venues, players -----------------------------------------------------


def test_team_nodes(gt: T.GraphTables) -> None:
    teams = gt.nodes["Team"]
    assert sorted(teams["team_id"]) == ["DEN", "KC", "LV", "SF"]  # the duplicate KC row is gone
    kc = by(teams, "team_id")["KC"]
    assert (kc["franchise_id"], kc["nickname"], kc["conference"], kc["division"]) == (
        "KC",
        "Chiefs",
        "AFC",
        "AFC West",
    )


def test_coaches_head_coach_of_and_coached_in(gt: T.GraphTables) -> None:
    assert set(gt.nodes["Coach"]["coach_id"]) == {"andy-reid", "rob-roe", "kyle-shan", "sean-pay"}
    assert by(gt.nodes["Coach"], "coach_id")["andy-reid"]["name"] == "Andy Reid"
    hc = by(gt.rels["HEAD_COACH_OF"], "s", "e", "season")
    assert hc[("andy-reid", "KC", 2026)]["games"] == 4  # scheduled games through week W
    assert hc[("andy-reid", "KC", 2025)]["games"] == 1
    ci = gt.rels["COACHED_IN"]
    assert ci.height == 2 * len(ALL_GAMES)  # both coaches of every graph game, week W included
    assert ("andy-reid", GW) in pairs(ci) and ("kyle-shan", GW) in pairs(ci)


def test_a_game_without_a_coach_name_has_no_coached_in_row_for_that_side(
    inp: GraphInputs,
) -> None:
    games = inp.games.with_columns(
        pl.when(pl.col("game_id") == G1)
        .then(None)
        .otherwise(pl.col("away_coach"))
        .alias("away_coach")
    )
    _, _, ci = T.coach_frames(dataclasses.replace(inp, games=games), KEY)
    assert ("rob-roe", G1) not in pairs(ci)
    assert ci.height == 2 * len(ALL_GAMES) - 1


def test_venue_nodes_and_at(gt: T.GraphTables) -> None:
    venues = by(gt.nodes["Venue"], "stadium_id")
    assert venues["KAN00"]["name"] == "GEHA Field"  # from stadiums
    assert venues["LAS00"]["name"] == "Allegiant Stadium"  # the schedule's name
    assert venues["SFO00"]["name"] == "SFO00"  # nothing else known: the id
    assert venues["LAS00"]["roof"] == "dome"
    assert venues["KAN00"]["surface"] == "turf"  # the latest game there, not the 2025 grass
    assert gt.rels["AT"].height == len(ALL_GAMES)
    assert (GW, "KAN00") in pairs(gt.rels["AT"])


def test_player_nodes(gt: T.GraphTables) -> None:
    players = by(gt.nodes["Player"], "player_id")
    assert set(players) == {Q, Q2, WR, TE, "00-LVWR", "00-SFQB", "00-DENDB", "00-TRD", "00-ROOK"}
    q = players[Q]
    assert (q["name"], q["position"], q["position_group"]) == ("Quinn Kay", "QB", "QB")
    assert q["birth_date"] == "1999-05-17" and q["college"] == "State U"
    assert (q["rookie_season"], q["draft_year"]) == (2020, 2020)


def test_player_nodes_fall_back_to_the_roster_and_derive_the_position_group(
    gt: T.GraphTables,
) -> None:
    players = by(gt.nodes["Player"], "player_id")
    rook = players["00-ROOK"]  # not in `players`
    assert (rook["name"], rook["position"], rook["position_group"]) == ("Rory Rook", "RB", "RB")
    assert players["00-TRD"]["position_group"] == "WR"  # missing in `players`, derived


def test_players_nobody_can_name_are_not_nodes_and_their_rows_are_dropped(
    gt: T.GraphTables,
) -> None:
    ids = set(gt.nodes["Player"]["player_id"])
    assert not ids & {"00-NONAME", "00-NOBODY", "00-NEW"}
    assert gt.dropped["APPEARED_IN"] == 1  # 00-NONAME
    assert gt.dropped["PLAYED_FOR"] == 1
    assert gt.dropped["THREW_TO"] == 1  # 00-NOBODY
    assert gt.dropped["PLAYED_IN"] == 0 and gt.dropped["DEPTH_CHART"] == 0


# ---- build_tables as a whole --------------------------------------------------------------------


def test_every_relationship_endpoint_is_a_node(gt: T.GraphTables) -> None:
    for name, (start, end) in T.REL_ENDS.items():
        rel = gt.rels[name]
        if rel.is_empty():
            continue
        assert set(rel["s"]) <= set(gt.nodes[start][T.NODE_KEYS[start]]), name
        assert set(rel["e"]) <= set(gt.nodes[end][T.NODE_KEYS[end]]), name


def test_every_table_is_present_and_the_expected_counts_match(gt: T.GraphTables) -> None:
    assert set(gt.nodes) == set(T.NODE_ORDER) and set(gt.rels) == set(T.REL_ORDER)
    counts = gt.expected_counts()
    assert counts["node:Game"] == len(ALL_GAMES) and counts["rel:PLAYED_IN"] == 2 * len(ALL_GAMES)
    assert counts["node:TeamWeek"] == 9 and counts["rel:NEXT"] == 6
    assert counts == {
        **{f"node:{k}": v.height for k, v in gt.nodes.items()},
        **{f"rel:{k}": v.height for k, v in gt.rels.items()},
    }
    assert set(gt.dropped) == set(T.REL_ORDER)


def test_build_is_repeatable(inp: GraphInputs, gt: T.GraphTables) -> None:
    assert differing_tables(gt, build_tables(inp, KEY)) == []


def test_build_with_every_optional_table_empty(inp: GraphInputs) -> None:
    sparse = dataclasses.replace(
        inp,
        plays=frame([], PLAYS_SCHEMA),
        team_games=pl.DataFrame(),
        officials=pl.DataFrame(),
        pfr_def=pl.DataFrame(),
        ngs={},
        team_weeks=pl.DataFrame(),
        predictions=pl.DataFrame(),
        published=pl.DataFrame(),
        injuries=frame([], INJURIES_SCHEMA),
        depth_charts=inp.depth_charts.clear(),
    )
    out = build_tables(sparse, KEY)
    assert out.nodes["Game"].height == len(ALL_GAMES)
    assert out.rels["THREW_TO"].is_empty() and out.rels["NEXT"].is_empty()
    assert out.rels["ON_INJURY_REPORT"].is_empty() and out.rels["DEPTH_CHART"].is_empty()
    assert out.nodes["Player"].height > 0
