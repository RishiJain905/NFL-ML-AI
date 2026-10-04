"""Graph insights (P05): query rows -> digest items, and the week's picks.

Row shapes are copied from the RETURN clauses of `graph/queries/*.cypher`. No Neo4j.
The contract being tested is D53: meaning (order, direction, verdict) is decided in code and
every number in a fact text is made by `digest/format.py`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from nflengine.digest import format as F
from nflengine.digest.payload import GraphInsight, GraphPerson
from nflengine.graph import insights as gi
from nflengine.graph import queries

UTC = dt.UTC
RUN = dt.datetime(2026, 9, 29, 14, 0, tzinfo=UTC)
SEASON = 2026
GAME = "2026_04_SF_KC"


def atoms(*displays: str) -> set[str]:
    return {a for d in displays for a in F.number_atoms(d)}


def assert_numbers_come_from(ins: GraphInsight, displays: list[str], ints: list[int]) -> None:
    """Every number in every fact text is in a `digest.format` display or is a count / year
    taken straight from the row."""
    allowed = atoms(*displays) | {str(i) for i in ints}
    for fact in ins.facts:
        for a in F.number_atoms(fact.text):
            assert a in allowed, f"{a!r} in {fact.text!r} is not from format.py or the row"


def texts(ins: GraphInsight) -> list[str]:
    return [f.text for f in ins.facts]


# ---- row builders (one per query) ---------------------------------------------------------------


def ripple(**kw: Any) -> dict[str, Any]:
    row = {
        "insight_type": "injury_ripple",
        "game_id": GAME,
        "team": "SF",
        "opponent": "KC",
        "starter_id": "00-S1",
        "starter": "Dan Starter",
        "position": "DE",
        "position_group": "DL",
        "defense": True,
        "out_source": "this_week",
        "status": "Out",
        "body_part": "Hamstring",
        "starts": 3,
        "snap_pct": 0.812,
        "n_with": 12,
        "n_without": 5,
        "team_epa_with": -0.05,
        "team_epa_without": 0.06,
        "team_points_with": 18.0,
        "team_points_without": 24.0,
        "backup_id": "00-B1",
        "backup": "Ben Backup",
        "backup_use_with": 0.45,
        "backup_use_without": 0.8,
        "backup_n_with": 8,
        "backup_n_without": 3,
        "backup_yds_with": None,
        "backup_yds_without": None,
        "chart_next_id": "00-C1",
        "chart_next": "Carl Chart",
        "chart_position": "DE",
        "sample_size": 5,
        "strength": 0.71,
    }
    return row | kw


def revenge(**kw: Any) -> dict[str, Any]:
    row = {
        "insight_type": "revenge",
        "game_id": GAME,
        "team": "KC",
        "opponent": "LV",
        "player_id": "00-R1",
        "player": "Rex Revenge",
        "position": "WR",
        "seasons_with_opp": [2023, 2024, 2025],
        "games_with_opp": 38,
        "games_now": 3,
        "snap_pct": 0.874,
        "drafted_year": 2019,
        "drafted_round": 2,
        "traded_on": "2026-03-14",
        "sample_size": 38,
        "strength": 0.8,
    }
    return row | kw


def qb_change(**kw: Any) -> dict[str, Any]:
    row = {
        "insight_type": "qb_change",
        "game_id": GAME,
        "team": "KC",
        "opponent": "SF",
        "qb_id": "00-Q2",
        "qb": "Ben Backup",
        "qb_rookie_season": 2025,
        "regular_id": "00-Q1",
        "regular": "Quinn Kay",
        "r_starts": 3,
        "r_last_start": 3,
        "team_games": 3,
        "q_starts_now": 0,
        "q_starts_total": 0,
        "q_last_start": None,
        "receivers": [
            {"player_id": "00-W1", "name": "Walt Kane", "position": "WR", "r_targets": 20,
             "q_targets": 0, "q_yards": 0, "q_tds": 0, "q_seasons": []},
            {"player_id": "00-W2", "name": "Tom Block", "position": "TE", "r_targets": 14,
             "q_targets": 12, "q_yards": 100, "q_tds": 1, "q_seasons": [2025]},
        ],
        "r_targets_total": 34,
        "q_targets_total": 12,
        "receivers_with_history": 1,
        "sample_size": 12,
        "strength": 0.9,
    }  # fmt: skip
    return row | kw


def common_row(**kw: Any) -> dict[str, Any]:
    row = {
        "insight_type": "common_opponents",
        "game_id": GAME,
        "team": "KC",
        "opponent": "SF",
        "common": [
            {
                "team": "LV",
                "a_games": [{"week": 1, "margin": 7, "epa": 0.1}],
                "b_games": [{"week": 3, "margin": -7, "epa": -0.1}],
                "a_margin": 7.0,
                "b_margin": -7.0,
                "a_epa": 0.1,
                "b_epa": -0.1,
            }
        ],
        "n_common": 1,
        "team_avg_margin": 7.0,
        "opponent_avg_margin": -7.0,
        "team_avg_epa_margin": 0.1,
        "opponent_avg_epa_margin": -0.1,
        "edge": 14.0,
        "chain_favorite": "KC",
        "model_favorite": "KC",
        "model_home_win_prob": 0.6,
        "sample_size": 1,
        "strength": 0.6,
    }
    return row | kw


def trend_row(**kw: Any) -> dict[str, Any]:
    row = {
        "insight_type": "trend_mismatch",
        "game_id": GAME,
        "team": "KC",
        "opponent": "SF",
        "team_direction": "up",
        "opponent_direction": "down",
        "team_delta": 0.114,
        "opponent_delta": -0.087,
        "team_net": 0.2,
        "opponent_net": -0.05,
        "window_weeks": 3,
        "sample_size": 3,
        "strength": 0.8,
    }
    return row | kw


# ---- little helpers -----------------------------------------------------------------------------


def test_games_word() -> None:
    assert [gi._games_word(n) for n in (0, 1, 2)] == ["games", "game", "games"]


def test_seasons_text() -> None:
    assert gi._seasons_text([2025]) == "2025"
    assert gi._seasons_text([2024, 2025]) == "2024 and 2025"
    assert gi._seasons_text([2023, 2024, 2025]) == "2023, 2024 and 2025"
    assert gi._seasons_text([2025, 2023, 2025]) == "2023 and 2025"  # sorted, de-duplicated


def test_team_phrases_and_possessives() -> None:
    assert gi._the("KC") == "the Chiefs"
    assert gi._poss("KC") == "the Chiefs'"  # plural nicknames take a bare apostrophe
    assert gi._poss("SF") == "the 49ers'"
    assert gi._poss("XYZ") == "the XYZ's"  # an unknown code is its own singular nickname


def test_converters_cover_the_query_library() -> None:
    assert set(gi.CONVERTERS) == set(queries.LIBRARY)
    assert set(gi.QUERY_OF.values()) == set(queries.LIBRARY)
    types = {t for ts in gi.SECTION_TYPES.values() for t in ts}
    assert types == set(gi.QUERY_OF)


# ---- injury ripple ------------------------------------------------------------------------------


def test_ripple_defense_allowed_more_without_him_is_worse() -> None:
    ins = gi._injury_ripple(ripple(), SEASON)
    assert ins is not None
    first = ins.facts[0]
    assert first.text == (
        "In 5 games without Dan Starter since the start of the 2024 season, the 49ers defense "
        f"allowed {F.epa(0.06).display}, against {F.epa(-0.05).display} in 12 games he "
        "started: worse without him"
    )
    assert first.owners == ["SF", "00-S1"]
    assert "+0.06 EPA per play" in first.text and "-0.05 EPA per play" in first.text


def test_ripple_defense_allowed_less_without_him_is_better() -> None:
    ins = gi._injury_ripple(ripple(team_epa_with=0.06, team_epa_without=-0.05), SEASON)
    assert ins is not None
    assert ins.facts[0].text.endswith("better without him")


def test_ripple_offense_averaged_less_without_him_is_worse() -> None:
    row = ripple(position="WR", position_group="WR", defense=False)
    row |= {"team_epa_with": 0.10, "team_epa_without": 0.02}
    ins = gi._injury_ripple(row, SEASON)
    assert ins is not None
    assert "the 49ers offense averaged +0.02 EPA per play, against +0.10 EPA per play" in (
        ins.facts[0].text
    )
    assert ins.facts[0].text.endswith("worse without him")
    better = gi._injury_ripple(row | {"team_epa_with": 0.02, "team_epa_without": 0.10}, SEASON)
    assert better is not None and better.facts[0].text.endswith("better without him")


@pytest.mark.parametrize("defense", [True, False])
@pytest.mark.parametrize("with_, without", [(0.05, 0.06), (0.06, 0.05), (-0.01, 0.0), (0.1, 0.1)])
def test_ripple_under_0_02_apart_is_about_the_same(
    defense: bool, with_: float, without: float
) -> None:
    ins = gi._injury_ripple(
        ripple(defense=defense, team_epa_with=with_, team_epa_without=without), SEASON
    )
    assert ins is not None
    assert ins.facts[0].text.endswith("about the same either way")


def test_ripple_a_0_03_gap_is_a_verdict() -> None:
    ins = gi._injury_ripple(ripple(team_epa_with=0.05, team_epa_without=0.08), SEASON)
    assert ins is not None and ins.facts[0].text.endswith("worse without him")  # defense: +0.03


@pytest.mark.parametrize(
    "n_without, confidence", [(1, "low"), (3, "low"), (4, "medium"), (9, "medium")]
)
def test_ripple_confidence_is_low_under_four_games(n_without: int, confidence: str) -> None:
    ins = gi._injury_ripple(ripple(n_without=n_without), SEASON)
    assert ins is not None and ins.confidence == confidence
    assert gi.LOW_SAMPLE == 4


def test_ripple_item_fields() -> None:
    ins = gi._injury_ripple(ripple(), SEASON)
    assert ins is not None
    assert (ins.insight_id, ins.insight_type, ins.section) == (
        "injury_ripple:00-S1",
        "injury_ripple",
        "matchup_risk",
    )
    assert (ins.graph_query, ins.game_id, ins.teams, ins.strength) == (
        "q2_injury_ripple",
        GAME,
        ["SF", "KC"],
        0.71,
    )
    assert ins.sample.display == "5 games without him" and ins.sample.value == 5
    assert ins.matchup == ""  # set by `candidates` when the game is known
    one = gi._injury_ripple(ripple(n_without=1), SEASON)
    assert one is not None and one.sample.display == "1 game without him"


@pytest.mark.parametrize(
    "source, status, body, head",
    [
        (
            "this_week",
            "Out",
            "Hamstring",
            "Dan Starter (49ers DE) is listed out for this week (hamstring).",
        ),
        ("this_week", "Doubtful", None, "Dan Starter (49ers DE) is listed doubtful for this week."),
        ("this_week", None, "", "Dan Starter (49ers DE) is listed out for this week."),
        (
            "reserve",
            "Out",
            None,
            "Dan Starter (49ers DE) is on a reserve list and missed the team's last game.",
        ),
        (
            "last_week",
            "Out",
            None,
            "Dan Starter (49ers DE) was ruled out of the team's last game and didn't play; "
            "this week's injury report isn't out yet.",
        ),
    ],
)
def test_ripple_headline_says_exactly_why_he_is_out(
    source: str, status: str | None, body: str | None, head: str
) -> None:
    ins = gi._injury_ripple(ripple(out_source=source, status=status, body_part=body), SEASON)
    assert ins is not None and ins.headline == head


def test_ripple_backup_fact_for_defense_uses_snap_share() -> None:
    ins = gi._injury_ripple(ripple(), SEASON)
    assert ins is not None
    assert ins.facts[1].text == (
        f"Ben Backup had {F.share(0.8).display} of defensive snaps in 3 games without Dan "
        f"Starter, against {F.share(0.45).display} of defensive snaps in 8 games with him"
    )
    assert ins.facts[1].owners == ["00-B1", "00-S1"]


@pytest.mark.parametrize(
    "group, unit", [("WR", "targets"), ("TE", "targets"), ("RB", "carries plus targets")]
)
def test_ripple_backup_fact_for_skill_players_uses_per_game_usage(group: str, unit: str) -> None:
    row = ripple(position_group=group, defense=False, backup_use_with=3.24, backup_use_without=7.86)
    row |= {"backup_n_without": 1}
    ins = gi._injury_ripple(row, SEASON)
    assert ins is not None
    assert ins.facts[1].text == (
        f"Ben Backup had {F.per_game(7.86, unit).display} in 1 game without Dan Starter, "
        f"against {F.per_game(3.24, unit).display} in 8 games with him"
    )
    assert "7.9" in ins.facts[1].text and "3.2" in ins.facts[1].text


def test_ripple_depth_chart_fact_and_people() -> None:
    ins = gi._injury_ripple(ripple(), SEASON)
    assert ins is not None
    assert (
        ins.facts[2].text
        == "Carl Chart is next behind Dan Starter on the 49ers' latest depth chart"
    )
    assert ins.facts[2].owners == ["00-C1", "00-S1", "SF"]
    assert [(p.player, p.role) for p in ins.people] == [
        ("Dan Starter", "out"),
        ("Ben Backup", "stepped in"),
        ("Carl Chart", "next on chart"),
    ]
    assert all(p.team == "SF" for p in ins.people)


def test_ripple_skips_the_chart_fact_when_the_next_man_is_the_backup() -> None:
    ins = gi._injury_ripple(ripple(chart_next_id="00-B1", chart_next="Ben Backup"), SEASON)
    assert ins is not None
    assert len(ins.facts) == 2 and [p.role for p in ins.people] == ["out", "stepped in"]


def test_ripple_without_epa_or_backup_keeps_what_it_has() -> None:
    ins = gi._injury_ripple(ripple(team_epa_with=None, team_epa_without=None, backup=None), SEASON)
    assert ins is not None
    assert texts(ins) == ["Carl Chart is next behind Dan Starter on the 49ers' latest depth chart"]


def test_ripple_with_no_facts_is_dropped() -> None:
    row = ripple(team_epa_with=None, team_epa_without=None, backup=None, chart_next=None)
    assert gi._injury_ripple(row, SEASON) is None
    assert (
        gi._injury_ripple(ripple(team_epa_with=None, backup=None, chart_next=None), SEASON) is None
    )


def test_ripple_numbers_come_from_format_helpers() -> None:
    ins = gi._injury_ripple(ripple(), SEASON)
    assert ins is not None
    shown = [
        F.epa(0.06).display,
        F.epa(-0.05).display,
        F.share(0.8).display,
        F.share(0.45).display,
    ]
    assert_numbers_come_from(ins, shown, [5, 12, 3, 8, SEASON - 2])
    wr = gi._injury_ripple(
        ripple(position_group="WR", defense=False, backup_use_with=3.24, backup_use_without=7.86),
        SEASON,
    )
    assert wr is not None
    shown = [F.epa(0.06).display, F.epa(-0.05).display]
    shown += [F.per_game(7.86, "targets").display, F.per_game(3.24, "targets").display]
    assert_numbers_come_from(wr, shown, [5, 12, 3, 8, SEASON - 2])


def test_headlines_without_a_position_have_no_dangling_space() -> None:
    assert " )" not in gi._injury_ripple(ripple(position=None), SEASON).headline  # type: ignore[union-attr]
    assert " )" not in gi._revenge(revenge(position=None), SEASON).headline


def test_ripple_window_matches_the_query_history_and_graph_start_matches_the_key() -> None:
    """The converter says "since the start of the {season - 2} season": the query's
    `history_seasons` must stay 2, and the graph's first season is the key's default."""
    from nflengine.graph import queries
    from nflengine.graph.tables import GraphKey

    assert queries.LIBRARY["q2_injury_ripple"]["history_seasons"] == 2
    ins = gi._injury_ripple(ripple(), 2030)
    assert ins is not None and "since the start of the 2028 season" in ins.facts[0].text
    assert GraphKey(2026, 4, RUN).start_season == gi.GRAPH_SINCE


# ---- revenge ------------------------------------------------------------------------------------


def test_revenge_facts() -> None:
    ins = gi._revenge(revenge(), SEASON)
    assert texts(ins) == [
        "Rex Revenge played 38 games for the Raiders in 2023, 2024 and 2025",
        f"This season Rex Revenge has played {F.share(0.874).display} of the Chiefs' snaps in the "
        "3 games he played",
        "The Raiders drafted Rex Revenge in round 2 of the 2019 draft",
        "The Raiders traded Rex Revenge to the Chiefs on March 14, 2026",
    ]
    assert "87%" in ins.facts[1].text
    assert [f.owners for f in ins.facts] == [
        ["00-R1", "LV"],
        ["00-R1", "KC"],
        ["00-R1", "LV"],
        ["00-R1", "LV", "KC"],
    ]
    assert_numbers_come_from(
        ins, [F.share(0.874).display], [38, 3, 2, 2019, 14, 2026, 2023, 2024, 2025]
    )


def test_revenge_without_a_draft_or_trade_link_has_two_facts() -> None:
    ins = gi._revenge(revenge(drafted_year=None, drafted_round=None, traded_on=None), SEASON)
    assert len(ins.facts) == 2


def test_revenge_wording_for_one_game_one_season() -> None:
    ins = gi._revenge(revenge(games_with_opp=1, games_now=1, seasons_with_opp=[2025]), SEASON)
    assert ins.facts[0].text == "Rex Revenge played 1 game for the Raiders in 2025"
    assert ins.facts[1].text.endswith("in the 1 game he played")
    assert ins.sample.display == "1 game with them"


def test_revenge_item_fields() -> None:
    ins = gi._revenge(revenge(), SEASON)
    assert (ins.insight_id, ins.insight_type, ins.section) == (
        "revenge:00-R1:LV",
        "revenge",
        "non_obvious",
    )
    assert (ins.graph_query, ins.confidence, ins.teams) == ("q1_revenge", "medium", ["KC", "LV"])
    assert ins.headline == "Rex Revenge (Chiefs WR) faces the Raiders, his former team."
    assert ins.sample.display == "38 games with them" and ins.sample.value == 38
    assert [(p.player, p.role, p.team) for p in ins.people] == [
        ("Rex Revenge", "former player", "KC")
    ]


def test_revenge_trade_date_formats_without_a_leading_zero() -> None:
    ins = gi._revenge(revenge(traded_on="2026-03-04T00:00:00"), SEASON)
    assert ins.facts[-1].text.endswith("on March 4, 2026")


# ---- QB change ----------------------------------------------------------------------------------


def test_qb_change_for_a_rookie_who_has_never_started() -> None:
    ins = gi._qb_change(qb_change(), SEASON)
    assert texts(ins)[:2] == [
        "Quinn Kay started 3 of the Chiefs' 3 games this season",
        "Ben Backup hasn't started a game for the Chiefs this season; he has never started an "
        "NFL game",
    ]


@pytest.mark.parametrize(
    "rookie, total, now, tail",
    [
        (2025, 0, 0, "; he has never started an NFL game"),
        (2018, 0, 0, "; he has never started an NFL game"),  # the graph's first season
        (2017, 0, 0, "; he has no NFL start since 2018"),  # a veteran with no start in the graph
        (None, 0, 0, "; he has no NFL start since 2018"),
        (2025, 1, 0, "; he has 1 NFL start since 2018"),
        (2020, 7, 0, "; he has 7 NFL starts since 2018"),
        (2020, 7, 2, "; he has 7 NFL starts since 2018"),
    ],
)
def test_qb_change_expected_starters_career_wording(
    rookie: int | None, total: int, now: int, tail: str
) -> None:
    ins = gi._qb_change(
        qb_change(qb_rookie_season=rookie, q_starts_total=total, q_starts_now=now), SEASON
    )
    lead = (
        "Ben Backup started 2 of them"
        if now
        else "Ben Backup hasn't started a game for the Chiefs this season"
    )
    assert ins.facts[1].text == lead + tail


def test_qb_change_receivers_sorted_and_wording() -> None:
    row = qb_change()
    row["receivers"] = [
        {"player_id": "00-W3", "name": "Low Targets", "r_targets": 6, "q_targets": 30},
        {"player_id": "00-W1", "name": "Walt Kane", "r_targets": 20, "q_targets": 0},
        {"player_id": "00-W2", "name": "Tom Block", "r_targets": 20, "q_targets": 12},
        {"player_id": "00-W4", "name": "Fourth Man", "r_targets": 5, "q_targets": 1},
    ]
    ins = gi._qb_change(row, SEASON)
    # by targets from the regular QB, then from the new QB; only the top three
    assert texts(ins)[2:] == [
        "Tom Block has 20 targets from Quinn Kay this season; he has 12 from Ben Backup since 2018",
        "Walt Kane has 20 targets from Quinn Kay this season; he has none from Ben Backup",
        "Low Targets has 6 targets from Quinn Kay this season; "
        "he has 30 from Ben Backup since 2018",
    ]
    assert [p.role for p in ins.people] == [
        "expected starter",
        "main starter",
        "receiver",
        "receiver",
        "receiver",
    ]
    assert ins.facts[2].owners == ["00-Q1", "00-Q2"]  # each QB owns his clause; not the receiver


def test_qb_change_without_receivers() -> None:
    ins = gi._qb_change(qb_change(receivers=None, q_targets_total=None), SEASON)
    assert len(ins.facts) == 2 and ins.confidence == "low"


@pytest.mark.parametrize(
    "q_targets, confidence", [(0, "low"), (19, "low"), (20, "medium"), (80, "medium")]
)
def test_qb_change_confidence(q_targets: int, confidence: str) -> None:
    ins = gi._qb_change(qb_change(q_targets_total=q_targets), SEASON)
    assert ins.confidence == confidence
    assert ins.sample.display == (
        f"{q_targets} career targets from Ben Backup to the Chiefs' current receivers"
    )
    assert ins.sample.value == q_targets


def test_qb_change_item_fields() -> None:
    ins = gi._qb_change(qb_change(), SEASON)
    assert (ins.insight_id, ins.insight_type, ins.section) == (
        "qb_change:KC:00-Q2",
        "qb_change",
        "matchup_risk",
    )
    assert ins.headline == "Ben Backup is expected to start at QB for the Chiefs this week."
    assert (ins.graph_query, ins.teams, ins.strength) == ("q3_qb_change", ["KC", "SF"], 0.9)
    assert [(p.player_id, p.role) for p in ins.people[:2]] == [
        ("00-Q2", "expected starter"),
        ("00-Q1", "main starter"),
    ]


def test_qb_change_numbers_come_from_the_row() -> None:
    ins = gi._qb_change(qb_change(q_starts_total=7, q_starts_now=1), SEASON)
    assert_numbers_come_from(ins, [], [3, 1, 7, 20, 12, 2018, 14])


# ---- common opponents ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "margin, text",
    [
        (7, "the Chiefs beat the Raiders by 7 in week 3"),
        (-3, "the Chiefs lost to the Raiders by 3 in week 3"),
        (0, "the Chiefs tied the Raiders in week 3"),
    ],
)
def test_margin_phrase_for_one_game(margin: int, text: str) -> None:
    assert gi._margin_phrase("KC", [{"week": 3, "margin": margin}], "LV") == text


def test_margin_phrase_for_several_games_is_the_average() -> None:
    games = [{"week": 1, "margin": 10}, {"week": 2, "margin": 3}]
    disp = F.per_game(6.5, "points").display
    assert (
        gi._margin_phrase("KC", games, "LV")
        == f"the Chiefs outscored the Raiders by {disp} over 2 games"
    )
    assert disp == "6.5 points per game"
    lost = [{"week": 1, "margin": -10}, {"week": 2, "margin": -3}]
    assert gi._margin_phrase("SF", lost, "LV") == (
        f"the 49ers were outscored by the Raiders by {disp} over 2 games"
    )


def test_margin_phrase_for_several_games_that_average_zero_does_not_call_it_a_loss() -> None:
    games = [{"week": 1, "margin": 7}, {"week": 2, "margin": -7}]
    assert "outscored by" not in gi._margin_phrase("KC", games, "LV")


def test_common_opponents_facts_for_one_shared_opponent() -> None:
    ins = gi._common_opponents(common_row(), SEASON)
    assert ins is not None
    assert texts(ins) == [
        # one fact per shared opponent: both teams' games, so a cut never keeps half of it
        "The Chiefs beat the Raiders by 7 in week 1; the 49ers lost to the Raiders by 7 in week 3",
        "Against those common opponents, the Chiefs have been "
        f"{F.per_game(14.0, 'points').display} better than the 49ers on average",
    ]
    assert [f.owners for f in ins.facts] == [["KC", "SF"], ["KC", "SF"]]  # not the shared team
    assert_numbers_come_from(ins, [F.per_game(14.0, "points").display], [7, 1, 3])


def test_common_opponents_edge_direction_and_threshold() -> None:
    neg = gi._common_opponents(
        common_row(edge=-6.0, chain_favorite="SF", model_favorite="SF"), SEASON
    )
    assert neg is not None
    assert neg.facts[-1].text == (
        f"Against those common opponents, the 49ers have been "
        f"{F.per_game(6.0, 'points').display} better than the Chiefs on average"
    )
    small = gi._common_opponents(common_row(edge=0.99), SEASON)
    assert small is not None and len(small.facts) == 1  # under a point: no verdict fact
    exact = gi._common_opponents(common_row(edge=1.0), SEASON)
    assert exact is not None and len(exact.facts) == 2


@pytest.mark.parametrize(
    "model, chain, stated",
    [("SF", "KC", True), ("KC", "KC", False), (None, "KC", False), ("SF", None, False)],
)
def test_common_opponents_model_disagreement_fact(
    model: str | None, chain: str | None, stated: bool
) -> None:
    ins = gi._common_opponents(common_row(model_favorite=model, chain_favorite=chain), SEASON)
    assert ins is not None
    has = any(t == "The game model still favors the 49ers" for t in texts(ins))
    assert has is stated


def test_common_opponents_several_opponents_headline_and_cap() -> None:
    def entry(team: str, margin: int) -> dict[str, Any]:
        return {
            "team": team,
            "a_games": [{"week": 1, "margin": margin}],
            "b_games": [{"week": 2, "margin": -margin}],
        }

    teams = ["LV", "DEN", "LAC", "NE"]
    row = common_row(common=[entry(t, 7) for t in teams], n_common=4, edge=0.0)
    ins = gi._common_opponents(row, SEASON)
    assert ins is not None
    assert len(ins.facts) == 3  # at most three shared opponents are spelled out
    assert ins.headline == (
        "The Chiefs and the 49ers have both already played the Raiders, Broncos, Chargers and "
        "Patriots this season."
    )
    assert ins.teams == ["KC", "SF", *teams]
    two = gi._common_opponents(
        common_row(common=[entry("LV", 7), entry("DEN", 3)], n_common=2), SEASON
    )
    assert two is not None and "the Raiders and Broncos this season" in two.headline


def test_common_opponents_item_fields() -> None:
    ins = gi._common_opponents(common_row(), SEASON)
    assert ins is not None
    assert (ins.insight_id, ins.insight_type, ins.section) == (
        f"common_opponents:{GAME}",
        "common_opponents",
        "non_obvious",
    )
    assert ins.graph_query == "q4_common_opponents"
    assert ins.sample.display == "1 common opponent" and ins.sample.value == 1
    assert ins.note == "A small sample: a few games against shared opponents."
    assert ins.confidence == "low"
    assert ins.headline == (
        "The Chiefs and the 49ers have both already played the Raiders this season."
    )


@pytest.mark.parametrize("n, confidence", [(1, "low"), (3, "low"), (4, "medium")])
def test_common_opponents_confidence(n: int, confidence: str) -> None:
    ins = gi._common_opponents(common_row(n_common=n), SEASON)
    assert ins is not None and ins.confidence == confidence
    assert ins.sample.display == f"{n} common {'opponent' if n == 1 else 'opponents'}"


def test_common_opponents_with_no_common_opponent_is_dropped() -> None:
    assert gi._common_opponents(common_row(common=[], n_common=0), SEASON) is None
    assert gi._common_opponents(common_row(common=None), SEASON) is None


def test_common_opponents_multi_game_average_phrase_in_a_fact() -> None:
    entry = {
        "team": "LV",
        "a_games": [{"week": 1, "margin": 10}, {"week": 2, "margin": 3}],
        "b_games": [{"week": 3, "margin": -4}],
    }
    ins = gi._common_opponents(common_row(common=[entry], edge=1.0), SEASON)
    assert ins is not None
    assert texts(ins)[0] == (
        f"The Chiefs outscored the Raiders by {F.per_game(6.5, 'points').display} over 2 games; "
        "the 49ers lost to the Raiders by 4 in week 3"
    )


# ---- trend mismatch -----------------------------------------------------------------------------


def test_trend_mismatch_team_up_opponent_down() -> None:
    ins = gi._trend_mismatch(trend_row(), SEASON)
    assert ins.headline == (
        "The Chiefs are trending up and the 49ers are trending down going into this game."
    )
    assert texts(ins) == [
        f"The Chiefs' net rating is {F.epa_change(0.114, 3).display}",
        f"The 49ers' net rating is {F.epa_change(-0.087, 3).display}",
    ]
    assert texts(ins) == [
        "The Chiefs' net rating is up 0.11 EPA per play over the last 3 weeks",
        "The 49ers' net rating is down 0.09 EPA per play over the last 3 weeks",
    ]
    assert [f.owners for f in ins.facts] == [["KC"], ["SF"]]


def test_trend_mismatch_team_down_opponent_up_swaps_the_deltas() -> None:
    row = trend_row(
        team_direction="down", opponent_direction="up", team_delta=-0.2, opponent_delta=0.15
    )
    ins = gi._trend_mismatch(row, SEASON)
    assert ins.headline == (
        "The 49ers are trending up and the Chiefs are trending down going into this game."
    )
    assert texts(ins) == [
        "The 49ers' net rating is up 0.15 EPA per play over the last 3 weeks",
        "The Chiefs' net rating is down 0.20 EPA per play over the last 3 weeks",
    ]
    assert [f.owners for f in ins.facts] == [["SF"], ["KC"]]


def test_trend_mismatch_window_and_fields() -> None:
    ins = gi._trend_mismatch(trend_row(window_weeks=None), SEASON)
    assert ins.sample.display == "3 weeks" and ins.sample.value == 3  # default window
    assert "over the last 3 weeks" in ins.facts[0].text
    wide = gi._trend_mismatch(trend_row(window_weeks=4), SEASON)
    assert wide.sample.display == "4 weeks" and "over the last 4 weeks" in wide.facts[1].text
    assert (ins.insight_id, ins.section, ins.confidence) == (
        f"trend_mismatch:{GAME}",
        "non_obvious",
        "low",
    )
    assert ins.graph_query == "q8_trend_mismatch" and ins.teams == ["KC", "SF"]
    assert ins.note == "Descriptive: trends don't predict the next game on their own."
    assert_numbers_come_from(
        ins, [F.epa_change(0.114, 3).display, F.epa_change(-0.087, 3).display], [3]
    )


# ---- candidates ---------------------------------------------------------------------------------


def test_candidates_convert_every_row_and_skip_unknown_queries_and_dropped_rows() -> None:
    results = {
        "q1_revenge": [revenge()],
        "q2_injury_ripple": [
            ripple(),
            ripple(
                starter_id="00-S2",
                team_epa_with=None,
                backup=None,
                chart_next=None,
                team_epa_without=None,
            ),
        ],
        "q4_common_opponents": [common_row(common=[])],  # converter returns None
        "q99_future": [{"anything": 1}],  # no converter
        "q8_trend_mismatch": [],
    }
    out = gi.candidates(results, SEASON)
    assert [c.insight_id for c in out] == ["revenge:00-R1:LV", "injury_ripple:00-S1"]


def test_candidates_attach_the_code_made_matchup_when_the_game_is_known() -> None:
    games = {GAME: gi.GameInfo(GAME, home="KC", away="SF", kickoff=RUN)}
    out = gi.candidates(
        {"q8_trend_mismatch": [trend_row(), trend_row(game_id="other")]}, SEASON, games
    )
    assert out[0].matchup == "49ers at Chiefs"  # away at home
    assert out[1].matchup == ""  # unknown game


def test_game_info_matchup_marks_neutral_sites() -> None:
    g = gi.GameInfo("g", home="KC", away="SF", neutral=True)
    assert g.matchup == "49ers at Chiefs (neutral site)"


# ---- select -------------------------------------------------------------------------------------


def mk(
    iid: str,
    typ: str,
    strength: float,
    game: str = "g1",
    teams: tuple[str, ...] = ("KC", "SF"),
    people: tuple[tuple[str, str], ...] = (),
) -> GraphInsight:
    """A candidate; `people` are (player_id, role) pairs."""
    return GraphInsight(
        insight_id=iid,
        insight_type=typ,  # type: ignore[arg-type]
        section="matchup_risk" if typ in ("injury_ripple", "qb_change") else "non_obvious",
        strength=strength,
        graph_query=gi.QUERY_OF[typ],
        game_id=game,
        teams=list(teams),
        people=[GraphPerson(player=pid, player_id=pid, team="KC", role=r) for pid, r in people],
        headline="h",
        sample=F.text_num("x"),
    )


def games_after_run(*ids: str) -> dict[str, gi.GameInfo]:
    return {g: gi.GameInfo(g, "KC", "SF", RUN + dt.timedelta(days=3)) for g in ids}


def ids(items: list[GraphInsight]) -> list[str]:
    return [i.insight_id for i in items]


def test_games_that_already_kicked_off_are_skipped() -> None:
    games = {
        "past": gi.GameInfo("past", "KC", "SF", RUN - dt.timedelta(hours=1)),
        "now": gi.GameInfo("now", "KC", "SF", RUN),  # kicked off exactly at the run time
        "later": gi.GameInfo("later", "KC", "SF", RUN + dt.timedelta(minutes=1)),
        "tba": gi.GameInfo("tba", "KC", "SF", None),
    }
    cands = [
        mk("a", "qb_change", 0.9, "past"),
        mk("b", "qb_change", 0.8, "now"),
        mk("c", "qb_change", 0.7, "later"),
        mk("d", "revenge", 0.6, "tba"),
        mk("e", "revenge", 0.5, "unknown-game"),  # not in `games`: kept
    ]
    sel = gi.select(cands, games, RUN)
    assert sel.skipped["started"] == ["a", "b"]
    assert ids(sel.matchup_risk) == ["c"]
    assert ids(sel.non_obvious) == ["d", "e"]


def test_recently_published_insights_are_skipped() -> None:
    cands = [mk("old", "qb_change", 0.9), mk("new", "qb_change", 0.5, "g2")]
    sel = gi.select(cands, games_after_run("g1", "g2"), RUN, recent_ids=["old", "other"])
    assert sel.skipped["novelty"] == ["old"]
    assert ids(sel.matchup_risk) == ["new"]


def test_a_started_game_wins_over_novelty_in_the_skip_reason() -> None:
    games = {"g1": gi.GameInfo("g1", "KC", "SF", RUN - dt.timedelta(days=1))}
    sel = gi.select([mk("x", "qb_change", 0.9)], games, RUN, recent_ids=["x"])
    assert sel.skipped == {"started": ["x"], "novelty": [], "duplicate_player": []}


def test_followed_team_boost_adds_a_tenth_and_caps_at_one() -> None:
    cands = [
        mk("plain", "qb_change", 0.62, "g1", ("KC", "SF")),
        mk("followed", "qb_change", 0.55, "g2", ("LV", "DEN")),
        mk("capped", "revenge", 0.95, "g3", ("DEN", "NE")),
        mk("chain", "common_opponents", 0.5, "g4", ("NE", "MIA", "LV")),  # LV only a common opp
    ]
    games = games_after_run("g1", "g2", "g3", "g4")
    sel = gi.select(cands, games, RUN, followed=["LV", "DEN"])
    assert {c.insight_id: c.strength for c in sel.picked} == {
        "followed": 0.65,
        "capped": 1.0,
        "chain": 0.5,  # teams[2:] don't count as playing in the game
    }
    assert ids(sel.matchup_risk) == ["followed"]  # 0.55 + 0.1 beats the unfollowed 0.62
    assert cands[1].strength == 0.55  # the input isn't mutated


def test_followed_boost_rounds_to_three_places() -> None:
    sel = gi.select([mk("a", "qb_change", 0.451)], games_after_run("g1"), RUN, followed=["KC"])
    assert sel.matchup_risk[0].strength == 0.551


def test_matchup_risk_takes_the_strongest_injury_or_qb_item() -> None:
    cands = [
        mk("qb", "qb_change", 0.7, "g1"),
        mk("inj", "injury_ripple", 0.8, "g2"),
        mk("inj2", "injury_ripple", 0.6, "g3"),
        mk("tm", "trend_mismatch", 0.95, "g4"),
    ]
    sel = gi.select(cands, games_after_run("g1", "g2", "g3", "g4"), RUN)
    assert ids(sel.matchup_risk) == ["inj"]
    assert sel.matchup_risk[0].section == "matchup_risk"
    assert ids(sel.non_obvious) == ["tm"]  # the trend mismatch stays a non-obvious item


def test_ties_break_by_insight_id() -> None:
    cands = [mk("b", "qb_change", 0.7, "g2"), mk("a", "qb_change", 0.7, "g1")]
    sel = gi.select(cands, {}, RUN)
    assert ids(sel.matchup_risk) == ["a"]


def test_matchup_risk_falls_back_to_the_trend_mismatch_without_injury_or_qb_items() -> None:
    cands = [
        mk("tm1", "trend_mismatch", 0.8, "g1"),
        mk("tm2", "trend_mismatch", 0.6, "g2"),
        mk("rev", "revenge", 0.7, "g3"),
    ]
    sel = gi.select(cands, {}, RUN)
    assert ids(sel.matchup_risk) == ["tm1"]
    assert sel.matchup_risk[0].section == "matchup_risk"  # relabelled
    assert ids(sel.non_obvious) == ["rev", "tm2"]
    assert all(c.section == "non_obvious" for c in sel.non_obvious)


def test_nothing_to_pick_gives_an_empty_selection() -> None:
    sel = gi.select([], {}, RUN)
    assert sel.matchup_risk == [] and sel.non_obvious == [] and sel.picked == []
    assert sel.skipped == {"started": [], "novelty": [], "duplicate_player": []}


def test_a_player_is_the_subject_of_one_item_at_most() -> None:
    cands = [
        mk("inj", "injury_ripple", 0.9, "g1", people=(("P1", "out"), ("P2", "stepped in"))),
        mk("rev1", "revenge", 0.8, "g2", people=(("P1", "former player"),)),  # P1 again
        mk("rev2", "revenge", 0.7, "g3", people=(("P2", "former player"),)),  # only 'stepped in'
    ]
    sel = gi.select(cands, {}, RUN)
    assert ids(sel.matchup_risk) == ["inj"]
    assert ids(sel.non_obvious) == ["rev2"]
    assert sel.skipped["duplicate_player"] == ["rev1"]


def test_subject_roles_are_out_former_player_and_expected_starter() -> None:
    assert gi.SUBJECT_ROLES == ("out", "former player", "expected starter")
    cands = [
        mk("qb", "qb_change", 0.9, "g1", people=(("Q", "expected starter"), ("W", "receiver"))),
        mk("rev-q", "revenge", 0.8, "g2", people=(("Q", "former player"),)),
        mk(
            "rev-w", "revenge", 0.7, "g3", people=(("W", "former player"),)
        ),  # a receiver isn't used
    ]
    sel = gi.select(cands, {}, RUN)
    assert ids(sel.non_obvious) == ["rev-w"]
    assert sel.skipped["duplicate_player"] == ["rev-q"]


def test_the_same_player_cant_headline_two_non_obvious_items() -> None:
    cands = [
        mk("rev-a", "revenge", 0.9, "g1", people=(("P1", "former player"),)),
        mk("rev-b", "revenge", 0.8, "g2", people=(("P1", "former player"),)),
        mk("co", "common_opponents", 0.6, "g3"),
    ]
    sel = gi.select(cands, {}, RUN)
    assert ids(sel.non_obvious) == ["rev-a", "co"]
    assert sel.skipped["duplicate_player"] == ["rev-b"]


def test_at_most_two_non_obvious_items() -> None:
    cands = [
        mk("r1", "revenge", 0.9, "g1"),
        mk("c1", "common_opponents", 0.8, "g2"),
        mk("t1", "trend_mismatch", 0.7, "g3"),
    ]
    # a QB item takes the risk slot, so the trend mismatch stays in the non-obvious pool
    cands.append(mk("q1", "qb_change", 0.99, "g4"))
    sel = gi.select(cands, {}, RUN)
    assert ids(sel.non_obvious) == ["r1", "c1"]


def test_second_non_obvious_item_needs_a_minimum_strength() -> None:
    assert gi.SECOND_PICK_MIN == 0.45
    strong = gi.select([mk("a", "revenge", 0.9, "g1"), mk("b", "revenge", 0.45, "g2")], {}, RUN)
    assert ids(strong.non_obvious) == ["a", "b"]
    weak = gi.select([mk("a", "revenge", 0.9, "g1"), mk("b", "revenge", 0.449, "g2")], {}, RUN)
    assert ids(weak.non_obvious) == ["a"]
    # the first pick has no such bar
    only = gi.select([mk("a", "revenge", 0.1, "g1")], {}, RUN)
    assert ids(only.non_obvious) == ["a"]


def test_second_non_obvious_item_comes_from_a_different_game() -> None:
    cands = [
        mk("a", "revenge", 0.9, "g1"),
        mk("b", "common_opponents", 0.8, "g1"),  # the first item's game
        mk("c", "revenge", 0.6, "g2"),
    ]
    sel = gi.select(cands, {}, RUN)
    assert ids(sel.non_obvious) == ["a", "c"]


def test_second_non_obvious_item_avoids_the_risk_items_game() -> None:
    cands = [
        mk("q", "qb_change", 0.9, "g2"),
        mk("a", "revenge", 0.7, "g1"),
        mk("b", "common_opponents", 0.6, "g2"),  # the risk item's game
    ]
    sel = gi.select(cands, {}, RUN)
    assert ids(sel.matchup_risk) == ["q"]
    assert ids(sel.non_obvious) == ["a"]


def test_second_non_obvious_item_prefers_a_different_kind() -> None:
    cands = [
        mk("rev1", "revenge", 0.9, "g1"),
        mk("rev2", "revenge", 0.8, "g2"),
        mk("co", "common_opponents", 0.6, "g3"),
    ]
    sel = gi.select(cands, {}, RUN)
    assert ids(sel.non_obvious) == ["rev1", "co"]


def test_second_non_obvious_item_takes_the_same_kind_when_nothing_else_qualifies() -> None:
    weak_other = [
        mk("rev1", "revenge", 0.9, "g1"),
        mk("rev2", "revenge", 0.8, "g2"),
        mk("co", "common_opponents", 0.4, "g3"),  # under the bar
    ]
    assert ids(gi.select(weak_other, {}, RUN).non_obvious) == ["rev1", "rev2"]
    same_game = [
        mk("rev1", "revenge", 0.9, "g1"),
        mk("rev2", "revenge", 0.8, "g2"),
        mk("co", "common_opponents", 0.7, "g1"),  # the first item's game
    ]
    assert ids(gi.select(same_game, {}, RUN).non_obvious) == ["rev1", "rev2"]
    dup_player = [
        mk("rev1", "revenge", 0.9, "g1", people=(("P1", "former player"),)),
        mk("rev2", "revenge", 0.8, "g2"),
        mk("co", "common_opponents", 0.7, "g3", people=(("P1", "out"),)),  # P1 is taken
    ]
    assert ids(gi.select(dup_player, {}, RUN).non_obvious) == ["rev1", "rev2"]


def test_picked_lists_the_risk_item_first() -> None:
    cands = [mk("r", "revenge", 0.9, "g1"), mk("q", "qb_change", 0.5, "g2")]
    sel = gi.select(cands, {}, RUN)
    assert ids(sel.picked) == ["q", "r"]


def test_select_on_converter_output_end_to_end() -> None:
    results = {
        "q1_revenge": [revenge(game_id="g2", strength=0.7)],
        "q2_injury_ripple": [ripple(game_id="g1", strength=0.6)],
        "q3_qb_change": [qb_change(game_id="g3", strength=0.9)],
        "q4_common_opponents": [common_row(game_id="g4", strength=0.5)],
        "q8_trend_mismatch": [trend_row(game_id="g5", strength=0.4)],
    }
    games = {g: gi.GameInfo(g, "KC", "SF", RUN + dt.timedelta(days=2)) for g in
             ("g1", "g2", "g3", "g4", "g5")}  # fmt: skip
    cands = gi.candidates(results, SEASON, games)
    assert len(cands) == 5 and all(c.matchup == "49ers at Chiefs" for c in cands)
    sel = gi.select(cands, games, RUN, followed=["KC"])
    assert ids(sel.matchup_risk) == ["qb_change:KC:00-Q2"]
    assert ids(sel.non_obvious) == ["revenge:00-R1:LV", "common_opponents:g4"]
    assert [c.strength for c in sel.picked] == [1.0, 0.8, 0.6]  # everything plays KC: +0.1
