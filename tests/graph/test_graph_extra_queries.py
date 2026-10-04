"""The non-QB graph stories added after P05 (no Neo4j): Q5 coach reunion, Q11 unit mismatch,
Q12 special-teams edge.

- Tables: `PLAYED_IN.st_epa` (net special-teams EPA, nflverse sign conventions, centered on
  the league's mean per (season, play type) over visible plays only) and
  `TeamWeek.prior_weight`, with their as-of visibility.
- Converters: code-made wording (tiers, direction words, records), ownership, confidence,
  and every number made by `digest/format.py` (D53).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from graph_world import (
    G1,
    G2,
    G3,
    G25,
    GW,
    KEY,
    PLAYS_SCHEMA,
    TEAM_WEEKS_SCHEMA,
    by,
    frame,
    make_inputs,
    team_week_rows,
)

from nflengine.digest import format as F
from nflengine.digest.payload import GraphInsight
from nflengine.graph import insights as gi
from nflengine.graph import insights_extra as gx
from nflengine.graph import queries
from nflengine.graph import tables as T
from nflengine.graph.tables import GraphInputs
from nflengine.paths import DataPaths

SEASON = 2026
GAME = "2026_04_SF_KC"


def atoms(*displays: str) -> set[str]:
    return {a for d in displays for a in F.number_atoms(d)}


def assert_numbers_come_from(ins: GraphInsight, displays: list[str], ints: list[int]) -> None:
    allowed = atoms(*displays) | {str(i) for i in ints}
    for text in [f.text for f in ins.facts] + [ins.brief]:
        for a in F.number_atoms(text):
            assert a in allowed, f"{a!r} in {text!r} is not from format.py or the row"


def no_numbers(text: str) -> bool:
    return not F.number_atoms(text)


# ---- tables: special-teams EPA on PLAYED_IN ------------------------------------------------------


def st_play(game_id: str, posteam: str, defteam: str, play_type: str, epa: float) -> dict:
    season, week = int(game_id[:4]), int(game_id[5:7])
    return {
        "season": season,
        "week": week,
        "game_id": game_id,
        "play_id": 0.0,
        "season_type": "REG",
        "posteam": posteam,
        "defteam": defteam,
        "play_type": play_type,
        "pass": 0.0,
        "rush": 0.0,
        "epa": epa,
        "success": 0.0,
        "two_point_attempt": 0.0,
    }


def st_rows() -> list[dict]:
    """nflverse: on a kickoff posteam is the RECEIVING team; on a punt / FG / XP the kicking
    team; epa is posteam's. Season-2026 means over visible plays: kickoff +0.4, punt -0.5,
    field goal -0.5 (each type's mean is taken out first)."""
    return [
        st_play(G1, "LV", "KC", "kickoff", 0.6),  # KC kicks to LV
        st_play(G1, "KC", "LV", "kickoff", 0.2),  # LV kicks to KC
        st_play(G1, "KC", "LV", "punt", -1.0),  # KC punts, LV returns it well
        st_play(G1, "LV", "KC", "field_goal", -2.0),  # LV misses a field goal
        st_play(G2, "SF", "KC", "kickoff", 0.4),
        st_play(G2, "SF", "KC", "punt", 0.0),
        st_play(G3, "KC", "DEN", "field_goal", 1.0),
        st_play(G25, "KC", "LV", "kickoff", 3.0),  # 2025: its own season's mean
        st_play(GW, "SF", "KC", "kickoff", 5.0),  # week W: invisible
    ]


@pytest.fixture(scope="module")
def inp() -> GraphInputs:
    return make_inputs()


@pytest.fixture(scope="module")
def pi(inp: GraphInputs) -> dict:
    plays = pl.concat([inp.plays, frame(st_rows(), PLAYS_SCHEMA)])
    return by(T.played_in(dataclasses.replace(inp, plays=plays), KEY), "s", "e")


def test_st_epa_signs_follow_nflverse_posteam_conventions(pi: dict) -> None:
    kc, lv = pi[("KC", G1)], pi[("LV", G1)]
    # KC: kicks to LV (-(0.6-0.4)), receives (0.2-0.4), punts (-1.0+0.5), LV misses an FG
    # (-(-2.0+0.5)): -0.2 - 0.2 - 0.5 + 1.5
    assert kc["st_epa"] == pytest.approx(0.6)
    assert lv["st_epa"] == pytest.approx(-0.6)  # the two sides of a game sum to 0
    assert (kc["st_plays"], lv["st_plays"]) == (4, 4)
    assert pi[("KC", G2)]["st_epa"] == pytest.approx(-(0.4 - 0.4) - (0.0 + 0.5))
    assert pi[("SF", G2)]["st_epa"] == pytest.approx(0.5)


def test_st_epa_is_centered_per_season(pi: dict) -> None:
    # one 2025 kickoff: centered on its own season's mean -> 0, not on 2026's
    assert pi[("KC", G25)]["st_epa"] == pytest.approx(0.0)
    assert pi[("LV", G25)]["st_epa"] == pytest.approx(0.0)


def test_st_epa_of_week_w_is_hidden_and_does_not_move_the_means(pi: dict) -> None:
    assert pi[("KC", GW)]["st_epa"] is None and pi[("SF", GW)]["st_epa"] is None
    assert pi[("KC", GW)]["st_plays"] is None
    # had the week-W kickoff (+5.0) counted, the 2026 kickoff mean would move and so would KC's
    assert pi[("KC", G1)]["st_epa"] == pytest.approx(0.6)


def test_st_epa_null_for_games_without_special_teams_plays(pi: dict) -> None:
    assert pi[("LV", "2026_02_DEN_LV")]["st_epa"] is None


def test_st_epa_helper_without_plays_is_an_empty_typed_frame() -> None:
    out = T.special_teams_epa(pl.DataFrame(), pl.DataFrame({"game_id": [G1]}))
    assert out.is_empty() and out.schema["st_epa"] == pl.Float64


def test_week_w_special_teams_plays_do_not_change_any_visible_row(inp: GraphInputs) -> None:
    base = pl.concat([inp.plays, frame(st_rows()[:-1], PLAYS_SCHEMA)])
    more = pl.concat([inp.plays, frame(st_rows(), PLAYS_SCHEMA)])
    a = T.played_in(dataclasses.replace(inp, plays=base), KEY)
    b = T.played_in(dataclasses.replace(inp, plays=more), KEY)
    assert a.sort("s", "e").equals(b.sort("s", "e"))


# ---- tables: prior_weight on TeamWeek ------------------------------------------------------------


def test_team_week_carries_prior_weight_when_the_ratings_have_it(inp: GraphInputs) -> None:
    rows = [r | {"prior_weight": 1.0 / r["week"]} for r in team_week_rows()]
    schema = TEAM_WEEKS_SCHEMA | {"prior_weight": pl.Float64}
    nodes, _, _ = T.team_week_frames(dataclasses.replace(inp, team_weeks=frame(rows, schema)))
    assert by(nodes, "key")["KC:2026:4"]["prior_weight"] == pytest.approx(0.25)


def test_team_week_without_prior_weight_still_builds(inp: GraphInputs) -> None:
    nodes, _, _ = T.team_week_frames(inp)
    assert "prior_weight" not in nodes.columns and nodes.height


def test_team_week_inputs_read_prior_weight_up_to_the_key(tmp_path: Path) -> None:
    feats = DataPaths(tmp_path).features
    feats.mkdir(parents=True)
    keys = {"season": [2026, 2026, 2026], "week": [3, 4, 5], "team": ["KC"] * 3}
    cols = ("off_epa", "def_epa", "net_epa", "off_pass_epa", "off_rush_epa")
    cols += ("def_pass_epa", "def_rush_epa")
    pl.DataFrame(
        keys | {c: [0.1] * 3 for c in cols} | {"prior_weight": [0.6, 0.5, 0.4]}
    ).write_parquet(feats / "team_ratings.parquet")
    out = T.team_week_inputs(DataPaths(tmp_path), KEY)
    assert out.sort("week")["prior_weight"].to_list() == [0.6, 0.5]  # not week 5


# ---- the library ---------------------------------------------------------------------------------


def test_the_new_queries_are_in_the_library_with_their_converters() -> None:
    for name in ("q5_coach_reunion", "q11_unit_mismatch", "q12_special_teams"):
        assert name in queries.LIBRARY
        assert gi.CONVERTERS[name] is gx.CONVERTERS[name]
    for itype, name in gx.QUERY_OF.items():
        assert gi.QUERY_OF[itype] == name
        assert itype in gi.SECTION_TYPES["non_obvious"]


# ---- Q5 coach reunion ----------------------------------------------------------------------------


def reunion(**kw: Any) -> dict[str, Any]:
    row = {
        "insight_type": "coach_reunion",
        "game_id": GAME,
        "team": "KC",
        "opponent": "SF",
        "coach_id": "pat-coach",
        "coach": "Pat Coach",
        "seasons_with_opp": [2023, 2021, 2022],
        "last_season": 2023,
        "n_seasons": 3,
        "reg_w": 27,
        "reg_l": 23,
        "reg_t": 1,
        "post_w": 2,
        "post_l": 1,
        "vs_w": 1,
        "vs_l": 0,
        "vs_t": 0,
        "meetings_since": 1,
        "sample_size": 54,
        "strength": 0.7,
    }
    return row | kw


def test_coach_reunion_wording_and_records() -> None:
    ins = gx._coach_reunion(reunion(), SEASON)
    assert ins.insight_id == "coach_reunion:pat-coach:SF"
    assert ins.insight_type == "coach_reunion" and ins.section == "non_obvious"
    assert ins.teams == ["KC", "SF"] and ins.game_id == GAME
    assert no_numbers(ins.headline)
    assert "Pat Coach" in ins.headline and "49ers" in ins.headline
    stint, vs = (f.text for f in ins.facts)
    assert stint == (
        "Pat Coach was the 49ers' head coach for 3 seasons (2021, 2022 and 2023), going "
        "27-23-1 in the regular season and 2-1 in the playoffs"
    )
    assert vs == "As another team's head coach, Pat Coach is 1-0 against the 49ers since 2018"
    assert ins.facts[0].owners == ["SF", "KC"]
    assert ins.people[0].role == "head coach" and ins.people[0].team == "KC"
    assert ins.sample.value == 54 and ins.confidence == "medium"
    assert "(27-23-1 in the regular season)" in ins.brief


def test_coach_reunion_stint_reaching_the_graph_start_is_scoped() -> None:
    """A stint that reaches 2018 may have begun earlier: no season count, a scoped record."""
    ins = gx._coach_reunion(
        reunion(seasons_with_opp=[2018, 2019, 2020], n_seasons=3, reg_t=0, post_w=0, post_l=0),
        SEASON,
    )
    assert ins.facts[0].text == (
        "Pat Coach was the 49ers' head coach until 2020; from 2018 on, he went 27-23 in the "
        "regular season"
    )
    assert "since 2018" in ins.sample.display and "until 2020" in ins.brief
    assert "3 seasons" not in ins.brief


def test_coach_reunion_first_meetings() -> None:
    ins = gx._coach_reunion(reunion(vs_w=0, vs_l=0, meetings_since=0), SEASON)
    assert ins.facts[1].text == (
        "This is Pat Coach's first game against the 49ers as another team's head coach since 2018"
    )
    ins = gx._coach_reunion(reunion(vs_w=0, vs_l=2, meetings_since=0), SEASON)
    assert ins.facts[1].text.endswith(
        "is 0-2 against the 49ers since 2018; this is his first game against them since he left"
    )


# ---- Q11 unit mismatch ---------------------------------------------------------------------------


def mismatch(**kw: Any) -> dict[str, Any]:
    row = {
        "insight_type": "unit_mismatch",
        "game_id": GAME,
        "team": "KC",
        "opponent": "SF",
        "off_team": "KC",
        "def_team": "SF",
        "unit": "pass",
        "edge": "offense",
        "off_epa": 0.151,
        "def_epa": 0.118,
        "off_rank": 2,
        "def_rank": 30,
        "n_teams": 32,
        "expected_epa": 0.269,
        "size": 1.8,
        "off_prior_weight": 0.2,
        "def_prior_weight": 0.25,
        "off_games": 8,
        "def_games": 7,
        "sample_size": 7,
        "strength": 0.8,
    }
    return row | kw


@pytest.mark.parametrize(
    "rank, best, worst, want",
    [
        (1, "best", "worst", "the league's best"),
        (2, "best", "worst", "2nd-best"),
        (8, "strongest", "weakest", "8th-strongest"),
        (12, "best", "worst", "about league-average (12th-best)"),
        (17, "strongest", "weakest", "about league-average (16th-weakest)"),
        (30, "strongest", "weakest", "3rd-weakest"),
        (32, "best", "worst", "the league's worst"),
    ],
)
def test_rank_tier_says_which_end_it_counts_from(rank, best, worst, want) -> None:
    assert gx.rank_tier(rank, 32, best, worst) == want


def test_unit_mismatch_offense_edge() -> None:
    ins = gx._unit_mismatch(mismatch(), SEASON)
    assert ins.insight_id == f"unit_mismatch:{GAME}:KC:pass"
    assert ins.teams == ["KC", "SF"]
    assert no_numbers(ins.headline)
    assert ins.headline == (
        "The Chiefs' pass offense, one of the league's best, meets one of its weakest pass "
        "defenses in the 49ers."
    )
    o, d = ins.facts
    assert o.text == (
        "The Chiefs' pass offense ranks 2nd-best in the league by the team ratings going into "
        "this week (+0.15 EPA per dropback, relative to the league average)"
    )
    assert d.text == (
        "The 49ers' pass defense ranks 3rd-weakest in the league by the team ratings going into "
        "this week (+0.12 EPA per dropback allowed, relative to the league average)"
    )
    assert (o.owners, d.owners) == (["KC"], ["SF"])  # each unit's numbers to its own team
    assert ins.brief == (
        "The Chiefs' pass offense, 2nd-best in the league (+0.15 EPA per dropback), meets the "
        "49ers' pass defense, 3rd-weakest (+0.12 EPA per dropback allowed)."
    )
    assert ins.confidence == "medium" and ins.note == ""
    assert_numbers_come_from(
        ins,
        [
            F.epa(0.151, "EPA per dropback").display,
            F.epa(0.118, "x").display,
            F.ordinal(2).display,
            F.ordinal(3).display,
        ],
        [],
    )


def test_unit_mismatch_defense_edge_run_units_and_rank_one() -> None:
    row = mismatch(
        off_team="SF",
        def_team="KC",
        unit="rush",
        edge="defense",
        off_epa=-0.08,
        def_epa=-0.11,
        off_rank=32,
        def_rank=1,
    )
    ins = gx._unit_mismatch(row, SEASON)
    assert ins.insight_id == f"unit_mismatch:{GAME}:SF:rush"
    assert ins.headline == (
        "The Chiefs' run defense, one of the league's strongest, meets one of its weakest run "
        "offenses in the 49ers."
    )
    o, d = ins.facts
    assert o.text.startswith("The 49ers' run offense is the league's worst by the team ratings")
    assert "(-0.08 EPA per rush," in o.text
    assert d.text.startswith("The Chiefs' run defense is the league's strongest")
    assert "(-0.11 EPA per rush allowed," in d.text
    assert "the league's worst (-0.08 EPA per rush)" in ins.brief
    assert "in the league in the league" not in ins.brief


def test_unit_mismatch_low_confidence_while_the_prior_dominates() -> None:
    ins = gx._unit_mismatch(mismatch(off_prior_weight=0.52, def_prior_weight=0.3), SEASON)
    assert ins.confidence == "low"
    assert "preseason prior" in ins.note and no_numbers(ins.note)
    ins = gx._unit_mismatch(mismatch(off_prior_weight=None, def_prior_weight=None), SEASON)
    assert ins.confidence == "medium"  # an old graph without the property: no claim


# ---- Q12 special teams ---------------------------------------------------------------------------


def special(**kw: Any) -> dict[str, Any]:
    row = {
        "insight_type": "special_teams",
        "game_id": GAME,
        "team": "KC",
        "opponent": "SF",
        "team_st_epa": -0.84,
        "opponent_st_epa": 1.23,
        "team_st_total": -5.04,
        "opponent_st_total": 7.38,
        "team_games": 6,
        "opponent_games": 6,
        "gap": -2.07,
        "shrunk_gap": 1.24,
        "sample_size": 6,
        "strength": 0.5,
    }
    return row | kw


@pytest.mark.parametrize(
    "value, want",
    [
        (1.23, "the 49ers have gained 1.2 EPA per game"),
        (-0.84, "the 49ers have lost 0.8 EPA per game"),
        (0.02, "the 49ers have come out about even"),
        (-0.04, "the 49ers have come out about even"),
    ],
)
def test_st_direction_words_are_made_by_code(value: float, want: str) -> None:
    assert gx.st_phrase("SF", value) == want


def test_special_teams_names_the_better_unit_from_the_numbers() -> None:
    ins = gx._special_teams(special(), SEASON)  # the away side (SF) is the better unit
    assert ins.insight_id == f"special_teams:{GAME}"
    assert ins.teams == ["KC", "SF"]
    assert no_numbers(ins.headline)
    assert ins.headline == (
        "The 49ers have had clearly better special teams than the Chiefs this season."
    )
    kc, sf, gap = ins.facts
    assert kc.text.startswith("The Chiefs have lost 0.8 EPA per game on special-teams plays")
    assert "over their 6 games this season, compared with an average team" in kc.text
    assert sf.text.startswith("The 49ers have gained 1.2 EPA per game")
    assert (kc.owners, sf.owners) == (["KC"], ["SF"])
    assert (
        gap.text
        == "That is a gap of 2.1 EPA per game on special teams this season, in the 49ers' favor"
    )
    assert set(gap.owners) == {"KC", "SF"}
    assert ins.brief == (
        "The 49ers have gained 1.2 EPA per game on special teams this season; the Chiefs have "
        "lost 0.8 EPA per game."
    )
    assert ins.confidence == "medium" and ins.sample.value == 6
    assert_numbers_come_from(
        ins,
        [
            F.per_game(1.23, "EPA").display,
            F.per_game(0.84, "x").display,
            F.per_game(2.07, "x").display,
        ],
        [6],
    )


def test_special_teams_both_negative_and_a_small_sample() -> None:
    ins = gx._special_teams(
        special(team_st_epa=-1.7, opponent_st_epa=-7.6, gap=5.9, team_games=3, opponent_games=4),
        SEASON,
    )
    assert ins.headline.startswith("The Chiefs have had clearly better special teams")
    assert "the 49ers have lost 7.6 EPA per game" in ins.brief
    assert ins.confidence == "low" and ins.sample.value == 3
    assert "noisy" in ins.note and no_numbers(ins.note)


# ---- through the shared candidates path ----------------------------------------------------------


def test_candidates_turn_the_new_rows_into_items() -> None:
    cands = gi.candidates(
        {
            "q5_coach_reunion": [reunion()],
            "q11_unit_mismatch": [mismatch()],
            "q12_special_teams": [special()],
        },
        SEASON,
    )
    assert sorted(c.insight_type for c in cands) == [
        "coach_reunion",
        "special_teams",
        "unit_mismatch",
    ]
    for c in cands:
        assert c.graph_query == gi.QUERY_OF[c.insight_type]
        assert c.brief and c.facts and all(f.owners for f in c.facts)
