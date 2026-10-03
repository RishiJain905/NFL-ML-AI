"""Digest checks catch planted errors and pass clean text (P04 exit criterion)."""

from __future__ import annotations

import pytest
from conftest import make_payload

from nflengine.digest.checks import Lexicon, run_checks, sentences, word_count
from nflengine.digest.facts import build_fact_index
from nflengine.digest.llm.placeholder import PlaceholderLLM
from nflengine.digest.prompt import load_prompt

BUDGETS = {
    "report_card": 60,
    "game_outlook": 80,
    "team_trends": 120,
    "under_the_hood": 110,
    "players_to_watch": 160,
}


@pytest.fixture(scope="module")
def facts():
    return build_fact_index(make_payload())


def check(facts, text: str, section: str = "game_outlook", lexicon: Lexicon | None = None):
    return run_checks({section: text}, facts, lexicon=lexicon)


# ---- planted errors ---------------------------------------------------------------------------


def test_made_up_number_is_caught(facts) -> None:
    r = check(facts, "The Chiefs are 64% favorites in Denver.")
    res = r.result("number_provenance")
    assert not res.passed and res.issues[0].token == "64%"
    assert "number_provenance" in r.failed


def test_number_attached_to_wrong_team_is_caught(facts) -> None:
    # 59% exists in the payload, but it is the Chiefs' number, not the Broncos'
    r = check(facts, "The Broncos are 59% to win at home.")
    assert r.result("number_provenance").passed
    res = r.result("entity_binding")
    assert not res.passed and res.issues[0].token == "59%"


def test_number_attached_to_wrong_player_is_caught(facts) -> None:
    text = "Jaylen Warren had 4.1 yards of average separation last week."
    r = check(facts, text, "under_the_hood")
    assert r.result("number_provenance").passed
    assert not r.result("entity_binding").passed


def test_right_player_binds(facts) -> None:
    r = check(facts, "Ja'Marr Chase had 4.1 yards of average separation last week.", "uth")
    assert r.result("entity_binding").passed
    # a unique last name works as an alias
    assert check(facts, "Chase had 4.1 yards of separation.", "uth").passed


@pytest.mark.parametrize(
    "text, word",
    [
        ("The Bills win about two-thirds of these.", "two-thirds"),
        ("The Chiefs scored a dozen points.", "dozen"),
        ("The Bills doubled their output.", "doubled"),
        ("The Bills lead by half.", "half"),
    ],
)
def test_spelled_out_numbers_are_caught(facts, text: str, word: str) -> None:
    res = check(facts, text).result("spelled_out_numbers")
    assert not res.passed and res.issues[0].token.lower() == word


def test_football_phrases_with_number_words_pass(facts) -> None:
    text = "The Bills came alive in the second half and beat double coverage."
    assert check(facts, text).result("spelled_out_numbers").passed


@pytest.mark.parametrize(
    "text, token",
    [
        ("The Bills should cover against the Patriots.", "cover"),
        ("The spread looks wrong for the Chiefs.", "spread"),
        ("The Bills are a lock this week.", "lock"),
        ("Bet the Chiefs in Denver.", "Bet"),
        ("The over/under feels high for the Bills.", "over/under"),
        ("Great fantasy start for the Chiefs.", "fantasy"),
        ("Fade the Patriots on the road.", "Fade"),
        ("The moneyline favors the Bills.", "moneyline"),
        ("The odds favor the Chiefs.", "odds"),
    ],
)
def test_betting_and_fantasy_words_are_caught(facts, text: str, token: str) -> None:
    res = check(facts, text).result("banned_language")
    assert not res.passed and res.issues[0].token == token


@pytest.mark.parametrize(
    "text",
    [
        "The Bills offensive line held up all game.",
        "The Chiefs ran it at the line of scrimmage.",
        "The Patriots defensive line lined up wide.",
        "The Broncos stayed on the sideline.",
    ],
)
def test_allowed_line_phrases_pass(facts, text: str) -> None:
    assert check(facts, text).result("banned_language").passed


def test_unknown_team_is_caught(facts) -> None:
    res = check(facts, "The Raiders keep rising.").result("unknown_entities")
    assert not res.passed and res.issues[0].token in ("Raiders", "LV", "Las Vegas")


def test_unknown_rostered_player_is_caught(facts) -> None:
    lex = Lexicon({"Davante Adams", "Ja'Marr Chase", "Jaylen Warren"})
    r = check(facts, "Davante Adams looked sharp against the Bills.", lexicon=lex)
    res = r.result("unknown_entities")
    assert not res.passed and res.issues[0].token == "Davante Adams"
    assert check(facts, "Ja'Marr Chase looked sharp.", lexicon=lex).passed


def test_made_up_name_is_a_warning(facts) -> None:
    r = check(facts, "Johnny Madeup looked sharp against the Bills.")
    assert r.passed  # warn only
    assert "name_heuristic" in r.warnings


def test_sentence_with_number_and_no_owner_fails(facts) -> None:
    r = check(facts, "Someone was 73% sure.")
    assert not r.result("entity_binding").passed


# ---- normalization (P04 pitfall: formatting false positives) ----------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The Chiefs are 59 % to win.",
        "The Chiefs are 59 percent to win.",
        "The Bills are 73% against the Patriots.",
        "In games the model called 60-70%, the favorite won 6 of 8.",
        "In games the model called 60–70%, the favorite won 6 of 8.",
        "The model went 11 of 15 in week 7 with a Brier of 0.201 vs Elo's 0.226.",
        "The Lions net rating moved −0.05 EPA per play over the last 3 weeks.",
        "Biggest miss: the Bills were 78% and the Jets won 24-17.",
    ],
)
def test_normalized_numbers_pass(facts, text: str) -> None:
    r = check(facts, text, "report_card")
    assert r.result("number_provenance").passed, r.result("number_provenance").issues
    assert r.result("entity_binding").passed, r.result("entity_binding").issues


def test_week_and_season_numbers_need_no_owner(facts) -> None:
    assert check(facts, "Week 8 of the 2025 season is here.").passed


def test_sentence_split_keeps_initials_and_decimals() -> None:
    s = sentences("C.J. Stroud had 4.1 yards. Michael Penix Jr. threw for 2.91 seconds!")
    assert s == ["C.J. Stroud had 4.1 yards.", "Michael Penix Jr. threw for 2.91 seconds!"]


# ---- length and hedging -----------------------------------------------------------------------


def test_length_over_budget_fails_under_budget_warns(facts) -> None:
    long = " ".join(["The Bills look good."] * 40)
    r = run_checks({"game_outlook": long}, facts, budgets={"game_outlook": 80})
    assert "length" in r.failed
    short = run_checks({"game_outlook": "The Bills look good."}, facts, budgets=BUDGETS)
    assert short.passed and "length_short" in short.warnings


def test_low_confidence_item_without_hedge_warns(facts) -> None:
    text = "Jaylen Warren has a carry share of 58% lately."
    r = check(facts, text, "players_to_watch")
    assert r.passed and "hedging" in r.warnings
    hedged = check(facts, text[:-1] + ", a low-confidence heuristic pick.", "players_to_watch")
    assert "hedging" not in hedged.warnings


# ---- the placeholder writer passes every check ------------------------------------------------


def test_placeholder_output_passes_all_checks() -> None:
    payload = make_payload()
    fx = build_fact_index(payload)
    prompt = load_prompt()
    spec = prompt.output_spec(BUDGETS)
    out = PlaceholderLLM().generate(prompt.system, payload.model_dump(mode="json"), spec)
    assert set(out) == set(BUDGETS)
    lex = Lexicon({"Ja'Marr Chase", "Jaylen Warren", "Christian Mahogany", "Davante Adams"})
    r = run_checks(out, fx, budgets=BUDGETS, lexicon=lex)
    assert r.passed, r.feedback()
    assert "hedging" not in r.warnings
    assert all(word_count(t) > 0 for t in out.values())


def test_last_name_alias_does_not_match_inside_another_full_name(facts) -> None:
    """'Chase' is Ja'Marr Chase's short alias, but 'Chase Brown' is someone else (P04)."""
    lex = Lexicon({"Chase Brown", "Ja'Marr Chase", "Jaylen Warren"})
    r = check(facts, "Chase Brown had 4.1 yards of average separation.", "uth", lexicon=lex)
    assert not r.result("entity_binding").passed  # 4.1 is Ja'Marr Chase's number
    assert check(facts, "Chase had 4.1 yards of average separation.", "uth", lexicon=lex).passed


def test_section_disclaimer_counts_as_hedge(facts) -> None:
    text = (
        "These are heuristic picks with low confidence. "
        "Jaylen Warren has a carry share of 58% lately."
    )
    r = check(facts, text, "players_to_watch")
    assert r.passed and "hedging" not in r.warnings


@pytest.mark.parametrize(
    "text, ok",
    [
        ("Week 7 went 11 of 15.", True),  # the report card's subject is the model
        ("In the 60-70% bucket, favorites won 6 of 8.", True),
        ("In the 60–70% bucket, favorites won 6 of 8.", True),
        ("In the 60-70% bucket, favorites won 11 of 15.", False),  # the model's numbers
        ("Favorites won 6 of 8 in those games.", False),  # bucket counts need the bucket
    ],
)
def test_report_card_owners(facts, text: str, ok: bool) -> None:
    r = check(facts, text, "report_card")
    assert r.result("entity_binding").passed is ok, r.result("entity_binding").issues


def test_implicit_owner_only_in_report_card(facts) -> None:
    assert not check(facts, "Week 7 went 11 of 15.", "team_trends").passed


@pytest.mark.parametrize("text", ["Chiefs-Broncos: 59% to 41%.", "Chiefs–Broncos is 59% vs 41%."])
def test_hyphenated_matchup_names_both_teams(facts, text: str) -> None:
    assert check(facts, text).result("entity_binding").passed


# ---- meaning checks (the fact-check's real GLM errors, P04) -----------------------------------


@pytest.mark.parametrize(
    "text, ok",
    [
        ("The Patriots at the Bills is the big one.", True),
        ("Bills at Patriots is the big one.", False),  # home/road swapped (W08: Colts at Titans)
        ("Chiefs at Bills is the big one.", False),  # no such game
        ("Chiefs vs Bills would be fun.", True),  # "vs" claims no venue
    ],
)
def test_matchup_direction(facts, text: str, ok: bool) -> None:
    assert check(facts, text).result("meaning").passed is ok


@pytest.mark.parametrize(
    "text, ok",
    [
        ("The closest game is Chiefs at Broncos.", True),
        ("The closest game is Patriots at Bills.", False),  # W09: wrong "tightest call"
        ("The most lopsided call is the Bills at 73%.", True),
        ("The Chiefs at 59% are right behind.", False),  # W14: wrong "right behind"
        ("Chiefs at Broncos is a toss-up.", True),  # in the closest list
    ],
)
def test_game_superlatives_come_from_highlights(facts, text: str, ok: bool) -> None:
    assert check(facts, text, "game_outlook").result("meaning").passed is ok


@pytest.mark.parametrize(
    "text, ok",
    [
        ("The model is notably higher on the Bills than consensus.", True),
        ("The model is much higher on the Bills than consensus.", False),  # tier is notable
        ("The model is notably higher on the Chiefs than consensus.", False),  # no gap there
        ("The model and consensus mostly agree this week.", True),
    ],
)
def test_consensus_wording_matches_tier(facts, text: str, ok: bool) -> None:
    assert check(facts, text, "game_outlook").result("meaning").passed is ok


def test_suffix_split_only_before_a_sentence_opener() -> None:
    s = sentences("Without Deebo Samuel Sr. The Vikings fell. Michael Penix Jr. threw 2 TDs.")
    assert s == ["Without Deebo Samuel Sr.", "The Vikings fell.", "Michael Penix Jr. threw 2 TDs."]


# ---- Sol review regressions (P04) ------------------------------------------------------------


@pytest.mark.parametrize(
    "text, check_name, ok",
    [
        ("Last week's biggest miss was Jets at Bills.", "meaning", True),  # #5 past game
        ("The Chiefs and consensus agree this week.", "meaning", True),  # #6 agreement
        ("The model's Brier is .999 this week.", "number_provenance", False),  # #7
        ("The Broncos are projected to score 23 points.", "entity_binding", False),  # #8
        ("The Chiefs are a must-bet.", "banned_language", False),  # #9
        ("The Chiefs are a betting-lock.", "banned_language", False),
        ("The Bills played Cover 2 and ran a fade route.", "banned_language", True),
        ("The Chiefs are 59 percent to win.", None, True),  # #11: every check passes
    ],
)
def test_sol_review_regressions(facts, text: str, check_name: str | None, ok: bool) -> None:
    sec = "report_card" if "miss" in text or "Brier" in text else "game_outlook"
    r = check(facts, text, sec)
    if check_name is None:
        assert r.passed and not r.failed, r.feedback()
    else:
        assert r.result(check_name).passed is ok, r.result(check_name).issues


def test_swapped_past_matchup_is_caught() -> None:
    from nflengine.digest.payload import BiggestMiss

    p = make_payload()
    bm = p.report_card.biggest_miss.model_copy(update={"home": "BUF", "away": "NYJ"})
    p = p.model_copy(update={"report_card": p.report_card.model_copy(update={"biggest_miss": bm})})
    assert isinstance(p.report_card.biggest_miss, BiggestMiss)
    fx = build_fact_index(p)
    bad = run_checks({"report_card": "The biggest miss was Bills at Jets."}, fx)
    assert not bad.result("meaning").passed


def test_blank_section_fails_completeness(facts) -> None:
    r = run_checks({"report_card": "Week 7 went 11 of 15.", "team_trends": "  "}, facts)
    assert "complete" in r.failed


def test_possessive_known_name_is_not_flagged(facts) -> None:
    r = check(facts, "Ja'Marr Chase's separation was 4.1 yards of average separation.", "uth")
    assert "name_heuristic" not in r.warnings
