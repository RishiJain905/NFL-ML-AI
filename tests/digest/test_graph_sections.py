"""The P05 graph sections: fact index owners, checks and the placeholder writer.

The items are real ones from the 2026 week-4 graph run (`graph_results.json`), copied as
literal dicts and validated with the payload models.
"""

from __future__ import annotations

import pytest
from conftest import make_payload

from nflengine.digest.checks import Lexicon, run_checks, word_count
from nflengine.digest.facts import build_fact_index
from nflengine.digest.llm.placeholder import NO_GRAPH_ITEM, PlaceholderLLM
from nflengine.digest.payload import GraphInsight

BUDGETS = {
    "report_card": 60,
    "game_outlook": 80,
    "team_trends": 120,
    "under_the_hood": 110,
    "players_to_watch": 160,
    "matchup_risk": 80,
    "non_obvious": 90,
}
GRAPH = ("matchup_risk", "non_obvious")

INJURY_RIPPLE = {
    "insight_id": "injury_ripple:00-0039817",
    "insight_type": "injury_ripple",
    "section": "matchup_risk",
    "strength": 0.85,
    "confidence": "medium",
    "graph_query": "q2_injury_ripple",
    "game_id": "2026_04_GB_TB",
    "matchup": "Packers at Buccaneers",
    "teams": ["GB", "TB"],
    "people": [
        {"player": "Jacob Monk", "player_id": "00-0039817", "team": "GB", "role": "out"},
        {
            "player": "Zach Bako-Bewele",
            "player_id": "00-0037817",
            "team": "GB",
            "role": "stepped in",
        },
        {
            "player": "Donovan Jennings",
            "player_id": "00-0039272",
            "team": "GB",
            "role": "next on chart",
        },
    ],
    "headline": "Jacob Monk (Packers G) is listed out for this week (quadricep).",
    "facts": [
        {
            "text": "In 32 games without Jacob Monk since the start of the 2024 season, the "
            "Packers offense averaged +0.11 EPA per play, against -0.18 EPA per play in 4 "
            "games he started: better without him",
            "owners": ["GB", "00-0039817"],
        },
        {
            "text": "Zach Bako-Bewele had 92% of snaps in 29 games without Jacob Monk, against "
            "53% of snaps in 2 games with him",
            "owners": ["00-0037817", "00-0039817", "GB"],
        },
        {
            "text": "Donovan Jennings is next behind Jacob Monk on the Packers' latest depth chart",
            "owners": ["00-0039272", "00-0039817", "GB"],
        },
    ],
    "sample": {"value": 32, "display": "32 games without him"},
    "note": "",
}

QB_CHANGE_LOW = {
    "insight_id": "qb_change:TB:00-0041251",
    "insight_type": "qb_change",
    "section": "matchup_risk",
    "strength": 0.8,
    "confidence": "low",
    "graph_query": "q3_qb_change",
    "game_id": "2026_04_GB_TB",
    "matchup": "Packers at Buccaneers",
    "teams": ["TB", "GB"],
    "people": [
        {
            "player": "Jalon Daniels",
            "player_id": "00-0041251",
            "team": "TB",
            "role": "expected starter",
        },
        {
            "player": "Baker Mayfield",
            "player_id": "00-0034855",
            "team": "TB",
            "role": "main starter",
        },
        {"player": "Emeka Egbuka", "player_id": "00-0040129", "team": "TB", "role": "receiver"},
        {"player": "Cade Otton", "player_id": "00-0038129", "team": "TB", "role": "receiver"},
    ],
    "headline": "Jalon Daniels is expected to start at QB for the Buccaneers this week.",
    "facts": [
        {
            "text": "Baker Mayfield started 3 of the Buccaneers' 3 games this season",
            "owners": ["00-0034855", "TB"],
        },
        {
            "text": "Jalon Daniels hasn't started a game for the Buccaneers this season; he has "
            "no NFL start since 2018",
            "owners": ["00-0041251", "TB"],
        },
        {
            "text": "Emeka Egbuka has 20 targets from Baker Mayfield this season and none from "
            "Jalon Daniels",
            "owners": ["00-0040129", "00-0041251", "00-0034855", "TB"],
        },
        {
            "text": "Cade Otton has 17 targets from Baker Mayfield this season and none from "
            "Jalon Daniels",
            "owners": ["00-0038129", "00-0041251", "00-0034855", "TB"],
        },
    ],
    "sample": {"value": 2, "display": "2 targets from Jalon Daniels to these receivers"},
    "note": "",
}

REVENGE = {
    "insight_id": "revenge:00-0035316:ATL",
    "insight_type": "revenge",
    "section": "non_obvious",
    "strength": 0.9,
    "confidence": "medium",
    "graph_query": "q1_revenge",
    "game_id": "2026_04_ATL_NO",
    "matchup": "Falcons at Saints",
    "teams": ["NO", "ATL"],
    "people": [
        {"player": "Kaden Elliss", "player_id": "00-0035316", "team": "NO", "role": "former player"}
    ],
    "headline": "Kaden Elliss (Saints LB) faces the Falcons, his team in 2024 and 2025.",
    "facts": [
        {
            "text": "Kaden Elliss played 34 games for the Falcons in 2024 and 2025",
            "owners": ["00-0035316", "ATL"],
        },
        {
            "text": "This season Kaden Elliss has played 100% of the Saints' snaps in the 3 games "
            "he played",
            "owners": ["00-0035316", "NO"],
        },
    ],
    "sample": {"value": 34, "display": "34 games with them"},
    "note": "",
}

TREND_MISMATCH = {
    "insight_id": "trend_mismatch:2026_04_DET_CAR",
    "insight_type": "trend_mismatch",
    "section": "non_obvious",
    "strength": 0.629,
    "confidence": "low",
    "graph_query": "q8_trend_mismatch",
    "game_id": "2026_04_DET_CAR",
    "matchup": "Lions at Panthers",
    "teams": ["CAR", "DET"],
    "people": [],
    "headline": (
        "The Panthers are trending up and the Lions are trending down going into this game."
    ),
    "facts": [
        {
            "text": "The Panthers' net rating is up 0.04 EPA per play over the last 3 weeks",
            "owners": ["CAR"],
        },
        {
            "text": "The Lions' net rating is down 0.05 EPA per play over the last 3 weeks",
            "owners": ["DET"],
        },
    ],
    "sample": {"value": 3, "display": "3 weeks"},
    "note": "Descriptive: trends don't predict the next game on their own.",
}

COMMON_OPPONENTS = {
    "insight_id": "common_opponents:2026_04_GB_TB",
    "insight_type": "common_opponents",
    "section": "non_obvious",
    "strength": 0.671,
    "confidence": "low",
    "graph_query": "q4_common_opponents",
    "game_id": "2026_04_GB_TB",
    "matchup": "Packers at Buccaneers",
    "teams": ["TB", "GB", "MIN"],
    "people": [],
    "headline": "The Buccaneers and the Packers have both already played the Vikings this season.",
    "facts": [
        {
            "text": "The Buccaneers lost to the Vikings by 7 in week 3; the Packers lost to the "
            "Vikings by 17 in week 1",
            "owners": ["TB", "GB", "MIN"],
        },
        {
            "text": "Against those common opponents, the Buccaneers have been 10.0 points per "
            "game better than the Packers on average",
            "owners": ["TB", "GB"],
        },
        {"text": "The game model still favors the Packers", "owners": ["TB", "GB"]},
    ],
    "sample": {"value": 1, "display": "1 common opponent"},
    "note": "A small sample: a few games against shared opponents.",
}

PEOPLE = {p["player"] for item in (INJURY_RIPPLE, QB_CHANGE_LOW, REVENGE) for p in item["people"]}
LEXICON = Lexicon(
    {*PEOPLE, "Ja'Marr Chase", "Jaylen Warren", "Christian Mahogany", "Davante Adams"}
)


def graph_payload(*items: dict):
    return make_payload(graph_insights=[GraphInsight.model_validate(i) for i in items])


def write(payload, budgets: dict[str, int] = BUDGETS) -> dict[str, str]:
    spec = {"sections": [{"id": k, "word_budget": v} for k, v in budgets.items()]}
    return PlaceholderLLM().generate("", payload.model_dump(mode="json"), spec)


def check(payload, text: str, section: str):
    return run_checks({section: text}, build_fact_index(payload), lexicon=LEXICON)


# ---- the placeholder writer -------------------------------------------------------------------


@pytest.mark.parametrize(
    "items",
    [
        (INJURY_RIPPLE, REVENGE, TREND_MISMATCH),  # the week-4 picks
        (QB_CHANGE_LOW, COMMON_OPPONENTS),  # low confidence in both sections
        (INJURY_RIPPLE, COMMON_OPPONENTS, REVENGE),
    ],
)
def test_placeholder_graph_sections_pass_every_check(items) -> None:
    payload = graph_payload(*items)
    out = write(payload)
    r = run_checks(out, build_fact_index(payload), budgets=BUDGETS, lexicon=LEXICON)
    assert r.passed, r.feedback()
    assert "hedging" not in r.warnings, r.result("hedging").issues
    for sec in GRAPH:
        n = word_count(out[sec])
        assert 0.75 * BUDGETS[sec] <= n <= 1.25 * BUDGETS[sec], (sec, n, out[sec])


def test_placeholder_copies_facts_whole_in_order() -> None:
    out = write(graph_payload(INJURY_RIPPLE, REVENGE, TREND_MISMATCH))
    risk, other = out["matchup_risk"], out["non_obvious"]
    # each item opens with its game (code-made matchup), then the headline
    assert risk.startswith(f"{INJURY_RIPPLE['matchup']}: {INJURY_RIPPLE['headline']}")
    fact = INJURY_RIPPLE["facts"][0]["text"]
    assert fact[0].upper() + fact[1:] + "." in risk
    # both non-obvious items, in order, each closed by its note when it has one
    assert other.index("Kaden Elliss") < other.index("The Panthers are trending up")
    assert other.endswith(TREND_MISMATCH["note"])


def test_low_confidence_item_gets_a_hedge_sentence() -> None:
    out = write(graph_payload(QB_CHANGE_LOW))["matchup_risk"]
    assert "A small sample: treat this as context, not a forecast." in out


def test_second_item_only_when_its_core_fits() -> None:
    payload = graph_payload(REVENGE, TREND_MISMATCH)
    tight = write(payload, {"non_obvious": 30})["non_obvious"]
    assert "Kaden Elliss" in tight and "Panthers" not in tight
    roomy = write(payload, {"non_obvious": 90})["non_obvious"]
    assert "Kaden Elliss" in roomy and "Panthers" in roomy


def test_no_items_for_a_section() -> None:
    out = write(graph_payload(REVENGE))
    assert out["matchup_risk"] == NO_GRAPH_ITEM
    assert write(make_payload())["non_obvious"] == NO_GRAPH_ITEM


# ---- entity binding on graph facts ------------------------------------------------------------


@pytest.mark.parametrize(
    "text, ok",
    [
        # copied whole: every owner is named
        (
            "The Buccaneers lost to the Vikings by 7 in week 3; the Packers lost to the "
            "Vikings by 17 in week 1.",
            True,
        ),
        ("The Packers lost by 17 in week 1.", True),
        # the 17 moved to the Bucs: the fact names the Bucs only in the other clause
        ("The Buccaneers lost by 17 in week 1.", False),
        ("The Buccaneers have been 10.0 points per game better than the Packers.", True),
        ("Against those opponents, someone was 10.0 points per game better.", False),
    ],
)
def test_common_opponent_numbers_bind_to_their_clause(text: str, ok: bool) -> None:
    r = check(graph_payload(COMMON_OPPONENTS), text, "non_obvious")
    assert r.result("number_provenance").passed
    assert r.result("entity_binding").passed is ok, r.result("entity_binding").issues


@pytest.mark.parametrize(
    "text, ok",
    [
        ("The Panthers' net rating is up 0.04 EPA per play over the last 3 weeks.", True),
        ("The Lions' net rating is up 0.04 EPA per play over the last 3 weeks.", False),
    ],
)
def test_trend_number_moved_to_the_other_team_fails(text: str, ok: bool) -> None:
    r = check(graph_payload(TREND_MISMATCH), text, "non_obvious")
    assert r.result("number_provenance").passed
    assert r.result("entity_binding").passed is ok, r.result("entity_binding").issues


@pytest.mark.parametrize(
    "text, ok",
    [
        ("Jacob Monk is out; in 32 games without him the offense averaged +0.11 EPA.", True),
        ("The Packers averaged +0.11 EPA per play in 32 games without their guard.", True),
        ("The Buccaneers averaged +0.11 EPA per play in 32 games.", False),
        # naming the backup names his team, but 92% is his and Monk's and the Packers'
        ("Zach Bako-Bewele had 92% of snaps without Monk.", True),
    ],
)
def test_injury_ripple_owners(text: str, ok: bool) -> None:
    r = check(graph_payload(INJURY_RIPPLE), text, "matchup_risk")
    assert r.result("entity_binding").passed is ok, r.result("entity_binding").issues


def test_qb_clause_without_a_name_keeps_every_owner() -> None:
    """'he has no NFL start since 2018' names no owner: the QB and his team both own it."""
    p = graph_payload(QB_CHANGE_LOW)
    assert check(p, "Jalon Daniels has no NFL start since 2018, a small sample.", "x").passed
    assert check(p, "The Buccaneers have no starter with an NFL start since 2018.", "x").passed


def test_revenge_headline_seasons_belong_to_the_player_and_teams() -> None:
    p = graph_payload(REVENGE)
    assert check(p, "Kaden Elliss faces his team from 2024 and 2025.", "non_obvious").passed
    assert check(p, "The Falcons had him in 2024 and 2025.", "non_obvious").passed
    assert not check(p, "Somebody played there in 2024 and 2025.", "non_obvious").passed


# ---- unknown entities -------------------------------------------------------------------------


def test_common_opponent_not_playing_this_week_is_a_known_team() -> None:
    text = "The Buccaneers lost to the Vikings by 7 in week 3."
    assert (
        check(graph_payload(COMMON_OPPONENTS), text, "non_obvious")
        .result("unknown_entities")
        .passed
    )
    # without the graph item the Vikings (and Bucs) are unknown
    assert not check(make_payload(), text, "non_obvious").result("unknown_entities").passed


def test_graph_people_are_known_players() -> None:
    r = check(graph_payload(QB_CHANGE_LOW), "Cade Otton and Emeka Egbuka catch passes.", "x")
    assert r.result("unknown_entities").passed
    assert "name_heuristic" not in r.warnings


# ---- hedging ----------------------------------------------------------------------------------


def test_low_confidence_section_without_any_hedge_warns() -> None:
    p = graph_payload(TREND_MISMATCH)
    bare = (
        "The Panthers are trending up and the Lions are trending down going into this game. "
        "The Panthers' net rating is up 0.04 EPA per play over the last 3 weeks."
    )
    r = check(p, bare, "non_obvious")
    assert r.passed and "hedging" in r.warnings
    assert r.result("hedging").issues[0].section == "non_obvious"
    hedged = bare + " Descriptive: trends don't predict the next game on their own."
    assert "hedging" not in check(p, hedged, "non_obvious").warnings
    # the same prose in a section without a low-confidence item doesn't warn
    assert "hedging" not in check(p, bare, "matchup_risk").warnings


def test_medium_confidence_section_needs_no_hedge() -> None:
    text = "Kaden Elliss played 34 games for the Falcons in 2024 and 2025."
    assert "hedging" not in check(graph_payload(REVENGE), text, "non_obvious").warnings


def test_low_confidence_subject_needs_a_hedge() -> None:
    p = graph_payload(QB_CHANGE_LOW)
    bare = "Jalon Daniels is expected to start at QB for the Buccaneers this week."
    r = check(p, bare, "matchup_risk")
    assert "hedging" in r.warnings
    tokens = {i.token for i in r.result("hedging").issues}
    assert "Jalon Daniels" in tokens and "matchup_risk" in tokens
    hedged = bare + " A small sample: treat this as context, not a forecast."
    assert "hedging" not in check(p, hedged, "matchup_risk").warnings
    assert "hedging" not in check(p, bare[:-1] + ", not predictive.", "matchup_risk").warnings
