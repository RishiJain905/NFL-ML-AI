"""AE01 additions to the router (`ask/route.py`) and the guard's `Sources`, without the app:

- `AskEngine.ask(..., picks=)`, `_apply_picks`, `_resolve_pick` for players, teams and coaches;
- the `picked` substitution of template values that repeat a picked name;
- `source.down` (a graph that doesn't answer) at the template and the generated stage, and the
  mid-question `source.busy`, both of which end the question at once (no correction round);
- `AskEngine.refresh(paths)` (the week of the weekly graph, the curated dates, the names);
- `AskResult.error_code` (llm | query_failed) and `AskEngine.last_calls`.

No network, no Neo4j, no LLM, no data root: fakes and `tmp_path` parquet files.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest
import yaml
from ask_fakes import (
    FakeDriver,
    FakeSources,
    ScriptedLLM,
    ServiceUnavailable,
    coach,
    make_index,
    make_sandbox,
    result,
    standard_index,
)

from nflengine.ask.config import AskConfig
from nflengine.ask.guard import QueryFailed, Refused, Sources
from nflengine.ask.llm import AskLLMError, OutOfCredits
from nflengine.ask.route import _STOP_RULES, FIXED, PICK_KINDS, AskEngine, AskResult
from nflengine.ask.templates import load_templates

TODAY = dt.date(2026, 10, 18)
ASOF = (2026, 6)

TEMPLATES = [
    {
        "id": "team_wins",
        "source": "sql",
        "title": "Team wins",
        "description": "Wins of a team.",
        "examples": ["How many wins do the Cowboys have?"],
        "params": {"team": {"type": "team"}},
        "query": "SELECT name, wins FROM teams WHERE team = $team",
    },
    {
        "id": "season_wins",
        "source": "sql",
        "title": "Team wins in a season",
        "description": "Wins of a team in a season.",
        "examples": ["How many wins did Dallas have in 2023?"],
        "params": {"team": {"type": "team"}, "season": {"type": "season"}},
        "query": "SELECT wins FROM teams WHERE team = $team AND season = $season",
    },
    {
        "id": "receivers_of",
        "source": "graph",
        "title": "A QB's receivers",
        "description": "Receivers a QB threw to.",
        "examples": ["Who did Patrick Mahomes throw to?"],
        "params": {"player": {"type": "player"}},
        "query": "MATCH (q:Player {player_id: $player})-[:THREW_TO]->(w:Player) "
        "RETURN w.name AS receiver",
    },
    {
        "id": "coach_games",
        "source": "graph",
        "title": "A coach's games",
        "description": "Games of a coach.",
        "examples": ["Which games did Andy Reid coach?"],
        "params": {"coach": {"type": "coach"}},
        "query": "MATCH (c:Coach {name: $coach})-[:COACHED]->(g:Game) RETURN g.game_id AS game_id",
    },
    {
        "id": "compare_players",
        "source": "graph",
        "title": "Two players",
        "description": "Two players side by side.",
        "examples": ["Compare Patrick Mahomes and Josh Allen."],
        "params": {"first": {"type": "player"}, "second": {"type": "player"}},
        "query": "MATCH (a:Player {player_id: $first}), (b:Player {player_id: $second}) "
        "RETURN a.name AS first, b.name AS second",
    },
    {
        "id": "player_on_team",
        "source": "graph",
        "title": "A player and a team",
        "description": "A player's games against a team.",
        "examples": ["How did Patrick Mahomes do against Dallas?"],
        "params": {"player": {"type": "player"}, "team": {"type": "team"}},
        "query": "MATCH (p:Player {player_id: $player}), (t:Team {team_id: $team}) "
        "RETURN p.name AS player, t.team_id AS team",
    },
]


def write_templates(tmp_path):
    path = tmp_path / "templates.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "templates": TEMPLATES}, sort_keys=False), encoding="utf-8"
    )
    return load_templates(path, current_season=2026)


def build(tmp_path, replies, runs=(), *, names=None, asof=ASOF, sources=None, cfg=None):
    llm = ScriptedLLM(list(replies), cost=0.001)
    src = sources or FakeSources(list(runs), sandbox=make_sandbox(tmp_path), driver="driver")
    eng = AskEngine(
        llm,
        src,
        names or standard_index(),
        write_templates(tmp_path),
        cfg or AskConfig(),
        current_season=2026,
        asof=asof,
        curated=("2026-10-14T09:00:00Z", 5),
        today=TODAY,
    )
    return SimpleNamespace(eng=eng, llm=llm, src=src)


def template(tid: str, **params: Any) -> dict[str, Any]:
    return {"action": "template", "template": tid, "params": params}


def generate(source: str = "sql") -> dict[str, Any]:
    return {"action": "generate", "source": source}


def refuse(reason: str = "no_data") -> dict[str, Any]:
    return {"action": "refuse", "reason": reason}


def pick(text: str, value: str, kind: str = "player") -> dict[str, str]:
    return {"text": text, "kind": kind, "value": value}


ANSWER = {"answer": "Kelce is first."}
KELCE = result(["receiver"], [["Travis Kelce"]], source="graph")
ALLEN_Q = "How many yards did Allen throw for in 2023?"
QB, CENTER, BRIAN = "00-0034857", "00-0026000", "00-0041000"  # the Josh Allens and Brian Allen


# ---- _resolve_pick -----------------------------------------------------------------------------


class TestResolvePick:
    @pytest.fixture
    def eng(self, tmp_path):
        return build(tmp_path, []).eng

    def test_a_player_id_names_exactly_one_player(self, eng):
        r = eng._resolve_pick("player", "00-0033873")
        assert r.kind == "player" and r.status == "unique" and r.value == "00-0033873"
        assert "Patrick Mahomes" in r.label

    @pytest.mark.parametrize(
        "value",
        ["Patrick Mahomes", "Josh Allen", "Allen", "00-9999999", "", "  ", "' OR 1=1 --", "x" * 80],
    )
    def test_a_player_value_that_is_not_an_id_counts_for_nothing(self, eng, value):
        assert eng._resolve_pick("player", value) is None

    def test_a_team_code_names_exactly_one_team(self, eng):
        r = eng._resolve_pick("team", "KC")
        assert r.kind == "team" and r.status == "unique" and r.value == "KC"
        assert r.label == "Kansas City Chiefs"

    @pytest.mark.parametrize("value", ["Chiefs", "kc", "Kansas City", "XXX", "New York", "", "NY"])
    def test_a_team_value_that_is_not_the_canonical_code_counts_for_nothing(self, eng, value):
        assert eng._resolve_pick("team", value) is None

    def test_an_old_team_code_resolves_to_the_new_one_and_so_is_not_the_value(self, eng):
        assert eng._resolve_pick("team", "OAK") is None  # (OAK -> LV is not "exactly OAK")

    @pytest.mark.parametrize("value", ["Andy Reid", "Reid", "Campbell", "Andy Ried"])
    def test_a_coach_who_resolves_to_exactly_one_coach_counts(self, eng, value):
        r = eng._resolve_pick("coach", value)
        assert r is not None and r.kind == "coach" and r.status == "unique"

    @pytest.mark.parametrize("value", ["Nobody Atall", "", "x'; DROP TABLE teams; --", "Zzz"])
    def test_a_coach_value_that_matches_nobody_counts_for_nothing(self, eng, value):
        assert eng._resolve_pick("coach", value) is None

    def test_a_coach_last_name_two_coaches_share_is_ambiguous_so_it_counts_for_nothing(
        self, tmp_path
    ):
        names = make_index(coaches=[coach("Mike Smith"), coach("Will Smith")])
        eng = build(tmp_path, [], names=names).eng
        assert names.coach("Smith").status == "ambiguous"
        assert eng._resolve_pick("coach", "Smith") is None

    @pytest.mark.parametrize("kind", ["robot", "", "PLAYER", "Team", "node"])
    def test_an_unknown_kind_counts_for_nothing(self, eng, kind):
        assert eng._resolve_pick(kind, "00-0033873") is None

    def test_the_kinds_are_the_three(self):
        assert PICK_KINDS == ("team", "player", "coach")


# ---- _apply_picks ------------------------------------------------------------------------------


class TestApplyPicks:
    @pytest.fixture
    def eng(self, tmp_path):
        return build(tmp_path, []).eng

    def apply(self, eng, question, picks):
        return eng._apply_picks(eng.names.mentions(question), picks)

    def test_no_picks_returns_the_mentions_as_they_are(self, eng):
        ms = eng.names.mentions(ALLEN_Q)
        assert eng._apply_picks(ms, []) is ms

    def test_a_choice_settles_the_ambiguous_mention(self, eng):
        (m,) = self.apply(eng, ALLEN_Q, [pick("Allen", QB)])
        assert m.text == "Allen" and m.resolution.status == "unique" and m.resolution.value == QB
        assert "Josh Allen" in m.resolution.label

    def test_each_option_of_the_card_can_be_chosen(self, eng):
        (card,) = [m for m in eng.names.mentions(ALLEN_Q) if m.resolution.status == "ambiguous"]
        for opt in card.resolution.matches:
            (m,) = self.apply(eng, ALLEN_Q, [pick("Allen", opt.value)])
            assert m.resolution.value == opt.value and m.resolution.label == opt.label

    @pytest.mark.parametrize("text", ["allen", "  ALLEN  ", "Allen.", "allen's"])
    def test_the_name_is_matched_loosely(self, eng, text):
        (m,) = self.apply(eng, ALLEN_Q, [pick(text, CENTER)])
        assert m.resolution.value == CENTER

    @pytest.mark.parametrize("value", ["00-9999999", "Josh Allen", "", "BUF", "' OR 1=1 --"])
    def test_a_value_the_card_did_not_offer_leaves_it_ambiguous(self, eng, value):
        (m,) = self.apply(eng, ALLEN_Q, [pick("Allen", value)])
        assert m.resolution.status == "ambiguous"

    def test_a_pick_of_the_wrong_kind_is_ignored(self, eng):
        (m,) = self.apply(eng, ALLEN_Q, [pick("Allen", "BUF", "team")])
        assert m.resolution.status == "ambiguous"
        (m,) = self.apply(eng, ALLEN_Q, [pick("Allen", "Andy Reid", "coach")])
        assert m.resolution.status == "ambiguous"

    def test_the_last_pick_for_a_name_wins(self, eng):
        (m,) = self.apply(eng, ALLEN_Q, [pick("Allen", QB), pick("Allen", CENTER)])
        assert m.resolution.value == CENTER

    def test_only_the_first_five_picks_count(self, eng):
        filler = [pick(f"x{i}", "KC", "team") for i in range(5)]

        def allen(picks):
            return next(m for m in self.apply(eng, ALLEN_Q, picks) if m.text == "Allen")

        assert allen([*filler, pick("Allen", QB)]).resolution.status == "ambiguous"  # the 6th
        assert allen([pick("Allen", QB), *filler[:4]]).resolution.value == QB  # the 1st

    @pytest.mark.parametrize(
        "bad",
        [
            {},
            {"text": None, "kind": None, "value": None},
            {"text": 5, "kind": "player", "value": 6},
            {"text": "Allen"},
            {"kind": "player", "value": QB},
            {"text": "", "kind": "player", "value": QB},
            {"text": "Allen", "kind": "player", "value": ""},
        ],
    )
    def test_a_malformed_pick_is_ignored_without_an_error(self, eng, bad):
        (m,) = self.apply(eng, ALLEN_Q, [bad])
        assert m.resolution.status == "ambiguous"

    def test_the_inputs_are_not_changed(self, eng):
        ms = eng.names.mentions(ALLEN_Q)
        before = [(m.text, m.resolution) for m in ms]
        picks = [pick("Allen", QB)]
        eng._apply_picks(ms, picks)
        assert [(m.text, m.resolution) for m in ms] == before
        assert picks == [pick("Allen", QB)]

    def test_a_name_that_is_not_in_the_question_is_added_when_it_names_one_entity(self, eng):
        ms = self.apply(eng, "Who leads in sacks?", [pick("Mahomes", "00-0033873")])
        (m,) = ms
        assert m.text == "Mahomes" and m.kind == "player" and m.resolution.value == "00-0033873"
        assert (m.start, m.end) == (-1, -1)

    def test_a_name_already_in_the_question_is_not_added_twice(self, eng):
        q = "How many yards did Patrick Mahomes throw for in 2023?"
        ms = self.apply(eng, q, [pick("Patrick Mahomes", "00-0033873")])
        assert [m.text for m in ms] == ["Patrick Mahomes"]

    def test_a_pick_cannot_replace_a_name_that_was_not_ambiguous(self, eng):
        q = "How many yards did Patrick Mahomes throw for in 2023?"
        (m,) = self.apply(eng, q, [pick("Patrick Mahomes", QB)])
        assert m.resolution.value == "00-0033873"

    def test_picks_of_the_three_kinds_beside_the_question_s_own_names(self, eng):
        picks = [
            pick("Mahomes", "00-0033873"),
            pick("Chiefs", "KC", "team"),
            pick("R", "Reid", "coach"),
        ]
        ms = self.apply(eng, "Who leads in sacks?", picks)
        assert sorted((m.kind, m.resolution.value) for m in ms) == [
            ("coach", "Andy Reid"),
            ("player", "00-0033873"),
            ("team", "KC"),
        ]

    def test_two_cards_one_pick(self, eng):
        ms = self.apply(eng, "Compare Allen and New York in 2023", [pick("Allen", QB)])
        by = {m.text: m.resolution.status for m in ms}
        assert by == {"Allen": "unique", "New York": "ambiguous"}


# ---- ask(..., picks=) ----------------------------------------------------------------------------


class TestAskWithPicks:
    def test_an_ambiguous_name_is_a_card_and_a_pick_answers_it(self, tmp_path):
        b = build(tmp_path, [template("receivers_of", player="Allen"), ANSWER], [KELCE])
        card = b.eng.ask(ALLEN_Q)
        assert card.status == "clarify" and b.llm.requests == []
        res = b.eng.ask(ALLEN_Q, picks=[pick("Allen", QB)])
        assert res.status == "answered" and res.params == {"player": QB}
        assert f'"Allen" -> player {QB}' in b.llm.requests[0]["user"]

    def test_the_card_comes_back_for_a_value_it_did_not_offer(self, tmp_path):
        b = build(tmp_path, [])
        res = b.eng.ask(ALLEN_Q, picks=[pick("Allen", "00-9999999")])
        assert res.status == "clarify" and res.choices[0]["text"] == "Allen"
        assert b.llm.requests == [] and b.src.runs == []

    def test_picks_default_to_none_and_the_signature_is_unchanged(self, tmp_path):
        b = build(tmp_path, [refuse("no_data"), refuse("no_data"), refuse("no_data")])
        assert b.eng.ask("Who leads in sacks?").status == "refused"
        assert b.eng.ask("Who leads in sacks?", "auto", None).status == "refused"
        assert b.eng.ask("Who leads in sacks?", source="auto", picks=[]).status == "refused"

    def test_a_pick_for_a_name_the_question_lacks_reaches_the_router_prompt(self, tmp_path):
        b = build(tmp_path, [refuse("no_data")])
        b.eng.ask("Who did he throw to?", picks=[pick("Mahomes", "00-0033873")])
        assert '"Mahomes" -> player 00-0033873' in b.llm.requests[0]["user"]

    def test_a_pick_that_names_no_single_entity_leaves_the_prompt_alone(self, tmp_path):
        b = build(tmp_path, [refuse("no_data")])
        b.eng.ask("Who did he throw to?", picks=[pick("Mahomes", "Patrick Mahomes")])
        assert "(none found)" in b.llm.requests[0]["user"]

    def test_the_recorded_mention_carries_the_picked_players_label(self, tmp_path):
        b = build(tmp_path, [template("receivers_of", player="Allen"), ANSWER], [KELCE])
        res = b.eng.ask(ALLEN_Q, picks=[pick("Allen", CENTER)])
        assert (
            res.mentions and CENTER in res.mentions[0] and "(C, NE, 2014-2016)" in res.mentions[0]
        )


# ---- the `picked` substitution -------------------------------------------------------------------


class TestPickedSubstitution:
    def run(self, tmp_path, decision, question, picks, runs=None):
        b = build(tmp_path, [decision, ANSWER], runs or [KELCE])
        res = b.eng.ask(question, picks=picks)
        return b, res

    def test_a_value_that_repeats_the_picked_name_gets_the_picked_id(self, tmp_path):
        b, res = self.run(
            tmp_path, template("receivers_of", player="Allen"), ALLEN_Q, [pick("Allen", CENTER)]
        )
        assert res.status == "answered" and b.src.runs[0]["params"] == {"player": CENTER}

    @pytest.mark.parametrize("written", ["allen", "ALLEN", "Allen.", "  Allen "])
    def test_however_the_router_writes_the_name(self, tmp_path, written):
        b, res = self.run(
            tmp_path, template("receivers_of", player=written), ALLEN_Q, [pick("Allen", BRIAN)]
        )
        assert b.src.runs[0]["params"] == {"player": BRIAN}

    def test_without_a_pick_the_same_value_asks_which_one(self, tmp_path):
        b = build(tmp_path, [template("receivers_of", player="Allen")])
        res = b.eng.ask("Who did he throw to?")
        assert res.status == "clarify" and res.template_id == "receivers_of" and b.src.runs == []

    def test_a_value_of_another_kind_is_not_replaced(self, tmp_path):
        # the pick is a PLAYER called "Dallas"; the team parameter resolves on its own
        b, res = self.run(
            tmp_path,
            template("team_wins", team="Dallas"),
            "How many wins?",
            [pick("Dallas", "00-0033873")],
            [result(["name", "wins"], [["Cowboys", 7]])],
        )
        assert b.src.runs[0]["params"] == {"team": "DAL"}

    def test_a_pick_of_a_team_replaces_a_team_value_only(self, tmp_path):
        b, res = self.run(
            tmp_path,
            template("player_on_team", player="Mahomes", team="Dallas"),
            "How did Mahomes do against Dallas?",
            [pick("Dallas", "DAL", "team")],
        )
        assert b.src.runs[0]["params"] == {"player": "00-0033873", "team": "DAL"}

    def test_two_player_values_one_of_them_picked(self, tmp_path):
        b, res = self.run(
            tmp_path,
            template("compare_players", first="Allen", second="Patrick Mahomes"),
            "Compare Allen and Patrick Mahomes",
            [pick("Allen", CENTER)],
        )
        assert b.src.runs[0]["params"] == {"first": CENTER, "second": "00-0033873"}

    def test_non_text_values_pass_through(self, tmp_path):
        b, res = self.run(
            tmp_path,
            template("season_wins", team="Dallas", season=2023),
            "How many wins did Dallas have in 2023?",
            [pick("Dallas", "DAL", "team")],
            [result(["wins"], [[7]])],
        )
        assert b.src.runs[0]["params"] == {"team": "DAL", "season": 2023}

    def test_a_name_that_was_unique_without_a_pick_is_not_in_the_map(self, tmp_path):
        b, res = self.run(
            tmp_path,
            template("receivers_of", player="Patrick Mahomes"),
            "Who did Patrick Mahomes throw to?",
            [pick("Allen", QB)],  # a pick for a name this question lacks
        )
        assert b.src.runs[0]["params"] == {"player": "00-0033873"}

    def test_a_coach_pick(self, tmp_path):
        names = make_index(coaches=[coach("Mike Smith"), coach("Will Smith")])
        b = build(tmp_path, [template("coach_games", coach="Smith"), ANSWER], [KELCE], names=names)
        card = b.eng.ask("Which games did Smith coach?")
        assert card.status == "clarify" and card.choices[0]["kind"] == "coach"
        res = b.eng.ask(
            "Which games did Smith coach?", picks=[pick("Smith", "Will Smith", "coach")]
        )
        assert res.status == "answered" and b.src.runs[0]["params"] == {"coach": "Will Smith"}


# ---- source.down and source.busy end the question at once ----------------------------------------


def down() -> Refused:
    return Refused("source.down")


class TestSourceDown:
    def test_at_the_template_stage(self, tmp_path):
        b = build(tmp_path, [template("receivers_of", player="Patrick Mahomes")], [down()])
        res = b.eng.ask("Who did Patrick Mahomes throw to?")
        assert res.status == "refused" and res.route == "refused" and res.retries == 0
        assert res.refusal == {
            "by": "source",
            "rule": "source.down",
            "message": FIXED["graph_down"],
        }
        assert res.answer == FIXED["graph_down"] and res.rows == [] and res.error is None
        assert (res.template_id, res.source) == ("receivers_of", "graph")
        assert b.llm.purposes == ["route"]  # no answer call and no correction round
        assert len(b.src.runs) == 1

    def test_at_the_generated_stage(self, tmp_path):
        q = {"query": "MATCH (p:Player) RETURN p.name AS name"}
        b = build(tmp_path, [generate("graph"), q, q], [down()])
        res = b.eng.ask("Which players are in the graph?")
        assert res.status == "refused" and res.refusal["rule"] == "source.down"
        assert res.refusal["by"] == "source" and res.source == "graph" and res.retries == 0
        assert b.llm.purposes == ["route", "generate"]

    def test_no_correction_round_is_spent_however_many_are_allowed(self, tmp_path):
        cfg = AskConfig.model_validate({"limits": {"corrections": 5}})
        q = {"query": "MATCH (p:Player) RETURN p.name AS name"}
        b = build(tmp_path, [generate("graph"), *[q] * 6], [down()], cfg=cfg)
        res = b.eng.ask("Which players are in the graph?")
        assert res.retries == 0 and b.llm.purposes == ["route", "generate"]

    def test_a_template_that_fails_first_and_then_finds_the_graph_down(self, tmp_path):
        replies = [
            template("team_wins", team="Dallas"),
            generate("graph"),
            {"query": "MATCH (n) RETURN n.a"},
        ]
        b = build(tmp_path, replies, [QueryFailed("error", "BinderException"), down()])
        res = b.eng.ask("How many wins do the Cowboys have?")
        assert res.refusal["rule"] == "source.down" and res.retries == 1
        assert b.llm.purposes == ["route", "fix", "generate"]

    def test_the_weekly_lock_taken_mid_question_is_attributed_to_the_source(self, tmp_path):
        b = build(tmp_path, [template("team_wins", team="Dallas")], [Refused("source.busy")])
        res = b.eng.ask("How many wins do the Cowboys have?")
        assert res.refusal == {"by": "source", "rule": "source.busy", "message": FIXED["busy"]}
        assert b.llm.purposes == ["route"] and res.retries == 0
        b = build(tmp_path, [generate("sql"), {"query": "SELECT 1"}], [Refused("source.busy")])
        res = b.eng.ask("Which team has the most wins?")
        assert res.refusal["by"] == "source" and res.refusal["rule"] == "source.busy"

    def test_the_lock_held_at_the_start_is_still_by_code(self, tmp_path):
        b = build(tmp_path, [])
        b.src.busy_value = True
        res = b.eng.ask("Who leads in sacks?")
        assert res.refusal == {"by": "code", "rule": "source.busy", "message": FIXED["busy"]}
        assert b.llm.requests == []

    def test_only_these_rules_stop_a_question(self):
        assert _STOP_RULES == {
            "source.busy": "busy",
            "source.down": "graph_down",
            "source.history_down": "history_down",  # AE02
            "source.history_busy": "history_busy",
        }
        assert all(v in FIXED for v in _STOP_RULES.values())
        assert "tables still work" in FIXED["graph_down"]

    def test_other_guard_refusals_still_get_a_correction_round(self, tmp_path):
        q = {"query": "MATCH (p:Player) RETURN p.name AS name"}
        b = build(tmp_path, [generate("graph"), q, q, q], [Refused("cypher.write")] * 3)
        res = b.eng.ask("Which players are in the graph?")
        assert res.retries == 2 and res.refusal["by"] == "guard"


class TestSourceDownThroughTheRealGuard:
    """`Sources.graph_up` (the app's Neo4j check) and the driver's own errors."""

    def sources(self, tmp_path, driver, graph_up=None):
        return Sources(
            sandbox=make_sandbox(tmp_path),
            driver=driver,
            enabled={"graph": True, "sql": True},
            graph_up=graph_up,
        )

    def test_graph_up_false_refuses_before_the_driver_is_touched(self, tmp_path):
        driver = FakeDriver()
        src = self.sources(tmp_path, driver, graph_up=lambda: False)
        b = build(tmp_path, [template("receivers_of", player="Patrick Mahomes")], sources=src)
        res = b.eng.ask("Who did Patrick Mahomes throw to?")
        assert res.refusal["rule"] == "source.down" and res.answer == FIXED["graph_down"]
        assert driver.sessions == [] and driver.tx_runs == []

    def test_graph_up_false_does_not_touch_sql(self, tmp_path):
        src = self.sources(tmp_path, FakeDriver(), graph_up=lambda: False)
        b = build(
            tmp_path, [template("team_wins", team="Dallas"), {"answer": "Seven."}], sources=src
        )
        res = b.eng.ask("How many wins do the Cowboys have?")
        assert res.status == "answered" and res.source == "sql"

    def test_graph_up_is_asked_every_time(self, tmp_path):
        state = {"up": False}
        driver = FakeDriver(keys=["receiver"], records=[["Travis Kelce"]])
        src = self.sources(tmp_path, driver, graph_up=lambda: state["up"])
        q = "Who did Patrick Mahomes throw to?"
        b = build(
            tmp_path,
            [template("receivers_of", player="Patrick Mahomes")] * 2 + [ANSWER],
            sources=src,
        )
        assert b.eng.ask(q).refusal["rule"] == "source.down"
        state["up"] = True
        assert b.eng.ask(q).status == "answered"

    @pytest.mark.parametrize("stage", ["explain_error", "run_error"])
    def test_a_driver_that_cannot_connect_is_source_down_not_a_failed_query(self, tmp_path, stage):
        driver = FakeDriver(**{stage: ServiceUnavailable("secret-host:7687 refused")})
        src = self.sources(tmp_path, driver)
        q = {"query": "MATCH (p:Player) RETURN p.name AS name"}
        b = build(tmp_path, [generate("graph"), q, q, q], sources=src)
        res = b.eng.ask("Which players are in the graph?")
        assert res.refusal["rule"] == "source.down" and res.retries == 0
        assert b.llm.purposes == ["route", "generate"]
        assert "secret-host" not in json.dumps(res.to_dict())

    def test_graph_up_none_leaves_the_driver_to_decide(self, tmp_path):
        driver = FakeDriver(keys=["receiver"], records=[["Travis Kelce"]])
        src = self.sources(tmp_path, driver, graph_up=None)
        b = build(
            tmp_path, [template("receivers_of", player="Patrick Mahomes"), ANSWER], sources=src
        )
        assert b.eng.ask("Who did Patrick Mahomes throw to?").status == "answered"


# ---- refresh -------------------------------------------------------------------------------------


def write_names(cur, players, *, completed_through=5, stamp="2026-10-14T09:00:00Z"):
    """players: [(gsis_id, name)]. Each gets 10 games so he counts as prominent."""
    cur.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "gsis_id": [g for g, _ in players],
            "display_name": [n for _, n in players],
            "position": ["QB"] * len(players),
            "latest_team": ["KC"] * len(players),
        }
    ).write_parquet(cur / "players.parquet")
    pl.DataFrame(
        {
            "player_id": [g for g, _ in players for _ in range(10)],
            "season": [2026] * (10 * len(players)),
            "team": ["KC"] * (10 * len(players)),
        }
    ).write_parquet(cur / "player_games.parquet")
    weeks = list(range(1, 8))
    pl.DataFrame(
        {
            "season": [2026] * len(weeks),
            "week": weeks,
            "completed": [w <= completed_through for w in weeks],
            "home_team": ["KC"] * len(weeks),
            "away_team": ["BUF"] * len(weeks),
            "home_coach": ["Andy Reid"] * len(weeks),
            "away_coach": ["Sean McDermott"] * len(weeks),
        }
    ).write_parquet(cur / "games.parquet")
    (cur / "_quality").mkdir(exist_ok=True)
    (cur / "_quality" / "latest.json").write_text(json.dumps({"run_at": stamp}))


def write_graph_build(runs, week, **fields):
    d = runs / "2026" / f"week{week:02d}"
    d.mkdir(parents=True, exist_ok=True)
    body = {"status": "ok", "mode": "live", "season": 2026, "week": week, **fields}
    (d / "graph_results.json").write_text(json.dumps(body), encoding="utf-8")


class TestRefresh:
    @pytest.fixture
    def world(self, tmp_path):
        cur, runs = tmp_path / "data" / "curated", tmp_path / "data" / "runs"
        write_names(cur, [("00-1000001", "Alpha One")])
        write_graph_build(runs, 5)
        paths = SimpleNamespace(curated=cur, runs=runs)
        b = build(tmp_path, [], asof=None, names=standard_index())
        b.eng.curated = (None, None)
        b.eng.today = dt.date(2020, 1, 1)
        return b, paths

    def test_it_reads_the_graphs_week_the_curated_dates_the_names_and_today(self, world):
        b, paths = world
        b.eng.refresh(paths)
        assert b.eng.asof == (2026, 5)
        assert b.eng.curated == ("2026-10-14T09:00:00Z", 5)
        assert b.eng.today == dt.date.today()
        assert b.eng.names.player("Alpha One").status == "unique"
        assert b.eng.current == 2026

    def test_a_newly_built_graph_week_is_picked_up(self, world):
        b, paths = world
        b.eng.refresh(paths)
        write_graph_build(paths.runs, 6)
        b.eng.refresh(paths)
        assert b.eng.asof == (2026, 6)
        assert b.eng.context()["graph_asof"] == {"season": 2026, "week": 6}

    def test_a_failed_or_rehearsal_build_does_not_move_the_week(self, world):
        b, paths = world
        write_graph_build(paths.runs, 6, status="failed")
        write_graph_build(paths.runs, 7, mode="rehearsal")
        b.eng.refresh(paths)
        assert b.eng.asof == (2026, 5)

    def test_the_week_goes_back_to_none_when_the_builds_are_gone(self, world):
        b, paths = world
        b.eng.refresh(paths)
        (paths.runs / "2026" / "week05" / "graph_results.json").unlink()
        b.eng.refresh(paths)
        assert b.eng.asof is None

    def test_new_curated_data_moves_the_dates(self, world):
        b, paths = world
        b.eng.refresh(paths)
        write_names(paths.curated, [("00-1000001", "Alpha One")], completed_through=6, stamp="X6")
        b.eng.refresh(paths)
        assert b.eng.curated == ("X6", 6)

    def test_new_names_are_picked_up_and_unchanged_files_cost_nothing(self, world):
        b, paths = world
        b.eng.refresh(paths)
        first = b.eng.names
        b.eng.refresh(paths)
        assert b.eng.names is first  # the index is cached by the files' modification times
        assert first.player("Beta Two").status == "none"
        time.sleep(0.05)
        write_names(paths.curated, [("00-1000001", "Alpha One"), ("00-1000002", "Beta Two")])
        b.eng.refresh(paths)
        assert b.eng.names is not first
        assert b.eng.names.player("Beta Two").status == "unique"
        assert b.eng.names.player("Beta Two").value == "00-1000002"

    def test_the_next_question_uses_the_refreshed_week(self, world):
        b, paths = world
        b.eng.refresh(paths)
        write_graph_build(paths.runs, 7)
        b.eng.refresh(paths)
        b.llm.replies.append(refuse("no_data"))
        b.eng.ask("Who leads in sacks?")
        assert "as of 2026 week 7" in b.llm.requests[0]["system"]

    def test_a_refresh_that_fails_leaves_the_engine_usable(self, world):
        b, paths = world
        bad = SimpleNamespace(curated=paths.curated / "nowhere", runs=paths.runs)
        with pytest.raises(FileNotFoundError):
            b.eng.refresh(bad)  # (the app catches this and keeps the last context)
        b.llm.replies.append(refuse("no_data"))
        assert b.eng.ask("Who leads in sacks?").status == "refused"


# ---- error_code and last_calls -------------------------------------------------------------------


class TestErrorCode:
    def test_a_model_failure_is_llm(self, tmp_path):
        b = build(tmp_path, [AskLLMError("HTTP 500: provider error")])
        res = b.eng.ask("Who leads in sacks?")
        assert res.status == "error" and res.error_code == "llm"
        assert res.error == "The model didn't give a usable reply: HTTP 500: provider error."

    def test_a_model_failure_in_a_correction_is_llm(self, tmp_path):
        replies = [template("team_wins", team="Nowhere"), AskLLMError("HTTP 500: x")]
        res = build(tmp_path, replies).eng.ask("How many wins do the Cowboys have?")
        assert res.status == "error" and res.error_code == "llm"

    def test_no_valid_action_is_llm(self, tmp_path):
        junk = {"action": "run"}
        res = build(tmp_path, [junk, junk, junk]).eng.ask("Who leads in sacks?")
        assert res.status == "error" and res.error_code == "llm"
        assert res.error == "The model didn't give a usable reply: no valid action."

    def test_corrections_that_never_work_are_query_failed(self, tmp_path):
        bad = template("team_wins", team="Nowhere")
        res = build(tmp_path, [bad, bad, bad]).eng.ask("How many wins do the Cowboys have?")
        assert res.status == "error" and res.error_code == "query_failed"
        assert res.error == "The query still failed after 2 corrections (not_found)."

    def test_a_generated_query_that_keeps_failing_is_query_failed(self, tmp_path):
        q = {"query": "SELECT nope FROM teams"}
        fail = QueryFailed("error", "BinderException", "hint")
        b = build(tmp_path, [generate("sql"), q, q, q], [fail, fail, fail])
        res = b.eng.ask("Which team has the most wins?")
        assert res.status == "error" and res.error_code == "query_failed"
        assert "BinderException" in res.error and "hint" not in res.error

    @pytest.mark.parametrize(
        ("replies", "runs", "status"),
        [
            ([refuse("not_nfl")], [], "refused"),
            ([template("team_wins", team="Dallas"), ANSWER], [result(["wins"], [[7]])], "answered"),
            ([template("team_wins", team="Dallas")], [result(["wins"], [])], "empty"),
        ],
    )
    def test_everything_else_has_none(self, tmp_path, replies, runs, status):
        res = build(tmp_path, replies, runs).eng.ask("How many wins do the Cowboys have?")
        assert res.status == status and res.error_code is None and res.error is None

    def test_a_clarify_and_a_blank_question_have_none(self, tmp_path):
        b = build(tmp_path, [])
        assert b.eng.ask(ALLEN_Q).error_code is None
        assert b.eng.ask("   ").error_code is None

    def test_an_empty_result_after_the_corrections_is_empty_not_an_error(self, tmp_path):
        q = {"query": "SELECT team FROM teams WHERE wins > 99"}
        empty = result(["team"], [])
        b = build(tmp_path, [generate("sql"), q, q, q], [empty, empty, empty])
        res = b.eng.ask("Which team has more than 99 wins?")
        assert res.status == "empty" and res.error_code is None

    def test_the_default_and_the_dict(self, tmp_path):
        assert AskResult("q").error_code is None
        res = build(tmp_path, [AskLLMError("HTTP 500: x")]).eng.ask("Who leads in sacks?")
        d = json.loads(json.dumps(res.to_dict()))
        assert d["error_code"] == "llm"


class TestLastCalls:
    def test_it_starts_empty_and_holds_the_last_questions_calls(self, tmp_path):
        b = build(
            tmp_path, [template("team_wins", team="Dallas"), ANSWER], [result(["wins"], [[7]])]
        )
        assert b.eng.last_calls == []
        res = b.eng.ask("How many wins do the Cowboys have?")
        assert [c["purpose"] for c in b.eng.last_calls] == ["route", "answer"]
        assert b.eng.last_calls == res.calls
        b.llm.replies.append(refuse("not_nfl"))
        b.eng.ask("What is the capital of France?")
        assert [c["purpose"] for c in b.eng.last_calls] == ["route"]  # replaced, not appended

    def test_it_survives_out_of_credits(self, tmp_path):
        replies = [template("team_wins", team="Dallas"), OutOfCredits("HTTP 402: out of credits")]
        b = build(tmp_path, replies, [result(["wins"], [[7]])])
        with pytest.raises(OutOfCredits):
            b.eng.ask("How many wins do the Cowboys have?")
        assert [c["purpose"] for c in b.eng.last_calls] == ["route", "answer"]
        assert [c["ok"] for c in b.eng.last_calls] == [True, False]
        assert b.llm.calls == []  # drained

    def test_it_survives_an_unexpected_exception(self, tmp_path):
        b = build(tmp_path, [template("team_wins", team="Dallas")], [RuntimeError("bug")])
        with pytest.raises(RuntimeError):
            b.eng.ask("How many wins do the Cowboys have?")
        assert [c["purpose"] for c in b.eng.last_calls] == ["route"]
