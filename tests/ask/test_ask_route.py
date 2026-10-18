"""AE00 router (`ask/route.py`): `AskEngine.ask` with a scripted LLM, a scripted `FakeSources` (or
the real guard over a fake driver and a tiny curated folder), and a small `NameIndex`.

Every question's LLM calls are counted: a refusal in code or a name to clarify must make none.
"""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from ask_fakes import (
    FakeDriver,
    FakeSources,
    ScriptedLLM,
    make_sandbox,
    plan,
    result,
    standard_index,
)

from nflengine.ask.config import AskConfig
from nflengine.ask.guard import RULES, QueryFailed, Refused, Sources
from nflengine.ask.llm import AskLLMError, OutOfCredits
from nflengine.ask.route import (
    FIXED,
    REFUSE_REASONS,
    AskEngine,
    AskResult,
    curated_through,
    graph_asof,
    prompt_hash,
)
from nflengine.ask.templates import load_templates

TODAY = dt.date(2026, 10, 17)
ASOF = (2026, 6)

TEMPLATES = [
    {
        "id": "team_wins",
        "source": "sql",
        "title": "Team wins",
        "description": "Wins of a team in a season.",
        "examples": ["How many games did Dallas win in 2023?"],
        "params": {"team": {"type": "team"}, "season": {"type": "season", "default": "current"}},
        "query": "SELECT wins FROM teams WHERE team = $team AND season = $season",
    },
    {
        "id": "stat_leaders",
        "source": "sql",
        "title": "Stat leaders",
        "description": "Top five by a stat.",
        "examples": ["Who leads the league in passing yards?"],
        "params": {
            "stat": {
                "type": "choice",
                "default": "yards",
                "choices": {"yards": "passing_yards", "tds": "passing_tds"},
            }
        },
        "query": "SELECT player, {stat} AS v FROM pg ORDER BY 2 DESC LIMIT 5",
    },
    {
        "id": "receivers_of",
        "source": "graph",
        "title": "A QB's receivers",
        "description": "Receivers a QB threw to.",
        "examples": ["Who did Mahomes throw to?"],
        "params": {"player": {"type": "player"}},
        "query": "MATCH (q:Player {player_id: $player})-[:THREW_TO]->(w:Player) "
        "RETURN w.name AS receiver",
    },
    {
        "id": "games_this_week",
        "source": "graph",
        "asof": True,
        "title": "This week's games",
        "description": "Games of the week.",
        "examples": ["Which games are on this week?"],
        "params": {"team": {"type": "team", "default": None}},
        "filters": {"team": ["home_team", "away_team"]},
        "query": "MATCH (g:Game {season: $season, week: $week}) "
        "RETURN g.home_team AS home_team, g.away_team AS away_team",
    },
    {
        "id": "qb_changes",
        "source": "graph",
        "library": "q3_qb_change",
        "asof": True,
        "title": "QB changes",
        "description": "Teams starting a backup quarterback.",
        "examples": ["Which teams change quarterback this week?"],
        "params": {"team": {"type": "team", "default": None}},
    },
]


def write_templates(tmp_path):
    path = tmp_path / "templates.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "templates": TEMPLATES}, sort_keys=False), encoding="utf-8"
    )
    return load_templates(path, current_season=2026)


def build(
    tmp_path,
    replies,
    runs=(),
    *,
    asof=ASOF,
    cfg=None,
    enabled=None,
    driver="driver",
    sandbox=True,
    cost=0.001,
    sources=None,
):
    llm = ScriptedLLM(list(replies), cost=cost)
    src = sources or FakeSources(
        list(runs),
        sandbox=make_sandbox(tmp_path) if sandbox else None,
        driver=driver,
        enabled=enabled,
    )
    eng = AskEngine(
        llm,
        src,
        standard_index(),
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


ANSWER = {"answer": "Dallas won 12 games in 2026."}


# ---- refusals and clarifications in code: no LLM call ---------------------------------- -----


class TestBeforeTheModel:
    def ask(self, tmp_path, question, **kw):
        b = build(tmp_path, [], **kw)
        res = b.eng.ask(question)
        assert b.llm.requests == [] and b.src.runs == []
        assert res.calls == [] and res.cost is None
        return res

    @pytest.mark.parametrize("q", ["", "   ", "\n\t", None])
    def test_an_empty_question(self, tmp_path, q):
        res = self.ask(tmp_path, q)
        assert res.status == "refused" and res.route == "refused"
        assert res.refusal == {"by": "code", "rule": "question.empty", "message": FIXED["empty"]}
        assert res.answer == FIXED["empty"] and res.question == ""

    def test_the_length_cap_counts_after_whitespace_is_normalised(self, tmp_path):
        res = self.ask(tmp_path, "a " * 300)  # 599 characters
        assert res.refusal["rule"] == "question.too_long"
        assert res.answer == "The question is too long: keep it under 500 characters."
        # lots of blanks around 500 real characters is fine, so it reaches the router
        b = build(tmp_path, [refuse("not_nfl")])
        assert b.eng.ask("   " + "a" * 500 + "   \n\n").status == "refused"
        assert len(b.llm.requests) == 1

    def test_501_characters_is_too_long_and_500_is_not(self, tmp_path):
        assert self.ask(tmp_path, "a" * 501).refusal["rule"] == "question.too_long"
        b = build(tmp_path, [refuse("not_nfl")])
        assert b.eng.ask("a" * 500).refusal["by"] == "router"

    def test_the_limit_is_configurable(self, tmp_path):
        cfg = AskConfig.model_validate({"limits": {"question_chars": 20}})
        res = self.ask(tmp_path, "How many games did Dallas win?", cfg=cfg)
        assert res.answer == "The question is too long: keep it under 20 characters."

    @pytest.mark.parametrize(
        "q",
        [
            "Delete all the games",
            "delete the Bills",
            "DROP TABLE games",
            "Drop the plays table",
            "please delete everything",
            "Please, could you update the scores",
            "Can you remove the Bills from the data",
            "could you, would you, please erase week 5",
            "Now truncate the plays",
            "I want you to insert a game",
            "go ahead and wipe the graph",
            "  \n alter the schema",
            "Update the standings",
            "modify the roster",
            "Overwrite the lines table",
            "rename the Bills",
            "Remove Josh Allen",
        ],
    )
    def test_a_request_to_change_data_is_refused_without_a_call(self, tmp_path, q):
        res = self.ask(tmp_path, q)
        assert res.status == "refused" and res.route == "refused"
        assert res.refusal == {"by": "code", "rule": "write", "message": FIXED["write"]}

    @pytest.mark.parametrize(
        "q",
        [
            "Update me on the Bills",
            "Drop rate of CeeDee Lamb in 2023",
            "drop rates by team",
            "Drop percentage for Dallas receivers",
            "Drop pct by quarterback",
            "Drop passes by Patrick Mahomes this season",
            "Which players were removed from rosters this year?",
            "How many passes were dropped by Dallas?",
            "Dropped passes by CeeDee Lamb",
            "Who has the most inserted... never mind, who leads in sacks?",
            "Altered routes? Who had the most deep targets?",
        ],
    )
    def test_questions_that_only_mention_those_words_go_to_the_model(self, tmp_path, q):
        b = build(tmp_path, [refuse("not_nfl")])
        b.eng.ask(q)
        assert len(b.llm.requests) == 1

    def test_a_source_that_is_switched_off(self, tmp_path):
        res = (
            self.ask(tmp_path, "Who leads in sacks?", enabled={"graph": True, "sql": False})
            if False
            else None
        )
        b = build(tmp_path, [], enabled={"graph": True, "sql": False})
        res = b.eng.ask("Who leads in sacks?", source="sql")
        assert res.refusal == {"by": "code", "rule": "source.off", "message": FIXED["source_off"]}
        assert b.llm.requests == []
        b = build(tmp_path, [], enabled={"graph": False, "sql": True})
        assert b.eng.ask("Who leads in sacks?", source="graph").refusal["rule"] == "source.off"

    def test_neither_source_available_in_auto(self, tmp_path):
        b = build(tmp_path, [], driver=None, sandbox=False)
        assert b.eng.ask("Who leads in sacks?").refusal["rule"] == "source.off"
        b = build(tmp_path, [], enabled={"graph": False, "sql": False})
        assert b.eng.ask("Who leads in sacks?").refusal["rule"] == "source.off"

    def test_the_weekly_refresh_is_running(self, tmp_path):
        b = build(tmp_path, [])
        b.src.busy_value = True
        res = b.eng.ask("Who leads in sacks?")
        assert res.status == "refused"
        assert res.refusal == {"by": "code", "rule": "source.busy", "message": FIXED["busy"]}
        assert b.llm.requests == [] and b.src.runs == []

    def test_busy_is_checked_after_the_cheap_refusals_but_before_the_names(self, tmp_path):
        b = build(tmp_path, [])
        b.src.busy_value = True
        assert b.eng.ask("delete everything").refusal["rule"] == "write"
        assert b.eng.ask("Allen?").refusal["rule"] == "source.busy"

    def test_an_ambiguous_name_asks_which_one(self, tmp_path):
        res = self.ask(tmp_path, "How many yards did Allen throw for in 2023?")
        assert res.status == "clarify" and res.route == "clarify"
        assert res.answer == 'More than one player matches "Allen": which one?'
        (choice,) = res.choices
        assert choice["text"] == "Allen" and choice["kind"] == "player"
        values = [o["value"] for o in choice["options"]]
        assert "00-0034857" in values and len(values) >= 2
        assert all(o["label"] for o in choice["options"])
        assert res.mentions and res.mentions[0].startswith('"Allen" -> ambiguous player')

    def test_several_ambiguous_names_are_all_listed_the_first_is_asked(self, tmp_path):
        res = self.ask(tmp_path, "Compare Allen and New York in 2023")
        assert [c["text"] for c in res.choices] == ["Allen", "New York"]
        assert res.choices[1]["kind"] == "team"
        assert [o["value"] for o in res.choices[1]["options"]] == ["NYG", "NYJ"]
        assert res.answer == 'More than one player matches "Allen": which one?'

    def test_a_unique_name_goes_on_to_the_router(self, tmp_path):
        b = build(tmp_path, [refuse("no_data")])
        b.eng.ask("How many yards did Patrick Mahomes throw for?")
        assert len(b.llm.requests) == 1
        assert "00-0033873" in b.llm.requests[0]["user"]

    def test_the_context_is_on_every_result(self, tmp_path):
        res = self.ask(tmp_path, "")
        assert res.context == {
            "graph_asof": {"season": 2026, "week": 6},
            "curated_run_at": "2026-10-14T09:00:00Z",
            "curated_week": 5,
            "current_season": 2026,
            "today": "2026-10-17",
        }
        b = build(tmp_path, [], asof=None)
        assert b.eng.ask("").context["graph_asof"] is None

    def test_timings_always_have_a_total(self, tmp_path):
        assert self.ask(tmp_path, "").timings["total"] >= 0.0


# ---- the router's own decisions ---------------------------------------------------------------


class TestRouter:
    @pytest.mark.parametrize("reason", REFUSE_REASONS)
    def test_a_refusal_with_each_reason(self, tmp_path, reason):
        b = build(tmp_path, [refuse(reason)])
        res = b.eng.ask("What is Tom Brady's salary?")
        assert res.status == "refused" and res.route == "refused"
        assert res.refusal == {"by": "router", "rule": reason, "message": FIXED[reason]}
        assert res.answer == FIXED[reason] and res.rows == [] and res.query is None
        assert len(b.llm.requests) == 1 and b.src.runs == []

    @pytest.mark.parametrize(
        "decision, rule",
        [
            ({"action": "refuse"}, "no_data"),
            ({"action": "refuse", "reason": "because"}, "no_data"),
            ({"action": "refuse", "reason": None}, "no_data"),
            ({"action": "refuse", "reason": 7}, "no_data"),
            ({"action": "REFUSE", "reason": "WRITE"}, "write"),
            ({"action": " refuse"[1:], "reason": "Out_Of_Range"}, "out_of_range"),
        ],
    )
    def test_unknown_or_odd_reasons(self, tmp_path, decision, rule):
        b = build(tmp_path, [decision])
        assert b.eng.ask("What is the airspeed of a swallow?").refusal["rule"] == rule

    def test_the_route_prompt(self, tmp_path):
        b = build(tmp_path, [refuse()])
        b.eng.ask("How many wins did Dallas have?")
        (req,) = b.llm.requests
        assert req["purpose"] == "route"
        system, user = req["system"], req["user"]
        assert "{{" not in system  # every placeholder was filled
        for line in (
            "- team_wins [sql] Team wins: Wins of a team in a season.",
            "- receivers_of [graph]",
            "- games_this_week [graph]",
        ):
            assert line in system
        assert "season (season" not in system.split("- games_this_week")[1].split("\n")[0]
        assert "as of 2026 week 6 (results through week 5)" in system
        assert "2026 games through week 5, rebuilt 2026-10-14" in system
        assert "today is 2026-10-17" in system
        assert "Only this source may be used" not in system
        assert user.startswith("Question: How many wins did Dallas have?\nNames:\n")
        assert '"Dallas" -> team DAL (Dallas Cowboys)' in user

    def test_no_names_found(self, tmp_path):
        b = build(tmp_path, [refuse()])
        b.eng.ask("Who leads in sacks?")
        assert b.llm.requests[0]["user"].endswith("Names:\n(none found)")

    def test_a_single_source_is_stated_and_filters_the_templates(self, tmp_path):
        b = build(tmp_path, [refuse()])
        b.eng.ask("Who leads in sacks?", source="sql")
        system = b.llm.requests[0]["system"]
        assert "Only this source may be used: sql." in system
        assert "- team_wins [sql]" in system and "receivers_of" not in system
        b = build(tmp_path, [refuse()])
        b.eng.ask("Who leads in sacks?", source="graph")
        system = b.llm.requests[0]["system"]
        assert "Only this source may be used: graph." in system
        assert "- receivers_of [graph]" in system and "team_wins" not in system

    def test_auto_drops_a_source_that_cannot_run(self, tmp_path):
        b = build(tmp_path, [refuse()], driver=None)
        b.eng.ask("Who leads in sacks?")
        system = b.llm.requests[0]["system"]
        assert "Only this source may be used: sql." in system and "receivers_of" not in system
        b = build(tmp_path, [refuse()], sandbox=False)
        b.eng.ask("Who leads in sacks?")
        assert "Only this source may be used: graph." in b.llm.requests[0]["system"]

    def test_without_a_graph_week_the_asof_templates_are_not_offered(self, tmp_path):
        b = build(tmp_path, [refuse()], asof=None)
        b.eng.ask("Who leads in sacks?")
        system = b.llm.requests[0]["system"]
        assert "games_this_week" not in system and "qb_changes" not in system
        assert "- team_wins [sql]" in system  # auto without a week: sql only
        assert "Only this source may be used: sql." in system
        assert "not available (no weekly graph build found)" in system
        # asked for the graph explicitly, the hand-written graph templates are still offered
        b = build(tmp_path, [refuse()], asof=None)
        b.eng.ask("Who leads in sacks?", source="graph")
        system = b.llm.requests[0]["system"]
        assert "- receivers_of [graph]" in system
        assert "games_this_week" not in system and "qb_changes" not in system

    def test_an_unknown_action_gets_one_more_try_per_correction_round(self, tmp_path):
        b = build(tmp_path, [{"action": "dance"}, {}, {"nothing": 1}])
        res = b.eng.ask("Who leads in sacks?")
        assert res.status == "error" and res.retries == 2
        assert res.error == "The model didn't give a usable reply: no valid action."
        assert b.llm.purposes == ["route", "fix", "fix"]
        fix = b.llm.requests[1]
        assert fix["extra"][0] == {"role": "assistant", "content": json.dumps({"action": "dance"})}
        assert '"action" must be "template", "generate" or "refuse"' in fix["extra"][1]["content"]
        assert len(b.llm.requests[2]["extra"]) == 4  # the history grows

    def test_an_unknown_action_then_a_good_one(self, tmp_path):
        b = build(tmp_path, [{"action": "dance"}, refuse("not_nfl")])
        res = b.eng.ask("Who leads in sacks?")
        assert res.status == "refused" and res.retries == 1

    def test_the_decision_may_have_extra_keys_and_odd_case(self, tmp_path):
        b = build(tmp_path, [{"action": "Refuse", "reason": "no_data", "explain": "nope"}])
        assert b.eng.ask("x?").status == "refused"


# ---- templates ---------------------------------------------------------------------------------


class TestTemplates:
    def test_a_template_answers(self, tmp_path):
        b = build(
            tmp_path,
            [template("team_wins", team="Dallas"), ANSWER],
            [result(["wins"], [[12]], query="IGNORED")],
        )
        res = b.eng.ask("How many games did Dallas win this season?")
        assert res.status == "answered" and res.route == "template"
        assert res.template_id == "team_wins" and res.source == "sql"
        assert res.query == "SELECT wins FROM teams WHERE team = $team AND season = $season"
        assert res.params == {"team": "DAL", "season": 2026}
        assert res.values == {"team": "DAL", "season": 2026}
        assert res.columns == ["wins"] and res.rows == [[12]] and not res.truncated
        assert res.answer == "Dallas won 12 games in 2026."
        assert res.answer_from_llm is True and res.answer_check == {"passed": True, "unmatched": []}
        assert res.retries == 0 and res.error is None and res.refusal is None
        assert [a["outcome"] for a in res.attempts] == ["rows"]
        assert res.attempts[0]["template"] == "team_wins" and res.attempts[0]["rows"] == 1
        assert b.src.runs == [
            {
                "source": "sql",
                "query": res.query,
                "params": {"team": "DAL", "season": 2026},
                "allow_maps": False,
            }
        ]
        assert b.llm.purposes == ["route", "answer"]
        assert [c["purpose"] for c in res.calls] == ["route", "answer"]
        assert res.cost == 0.002 and b.llm.calls == []  # the engine drained the call log
        assert {"names", "route", "run", "answer", "total"} <= set(res.timings)

    def test_the_answer_prompt_names_the_template_and_the_values(self, tmp_path):
        b = build(
            tmp_path, [template("team_wins", team="Dallas"), ANSWER], [result(["wins"], [[12]])]
        )
        b.eng.ask("How many games did Dallas win?")
        user = b.llm.requests[1]["user"]
        assert "Query: template team_wins: Team wins" in user
        assert 'Values used: {"team": "DAL", "season": 2026}' in user

    def test_a_fragment_choice_changes_the_query_text_from_the_yaml_only(self, tmp_path):
        b = build(
            tmp_path,
            [template("stat_leaders", stat="TDS"), {"answer": "Top is 5."}],
            [result(["player", "v"], [["A", 5]])],
        )
        res = b.eng.ask("Who leads in touchdown passes?")
        assert "passing_tds" in res.query and "{stat}" not in res.query
        assert res.values == {"stat": "tds"} and res.params == {}

    def test_a_library_template_may_return_maps(self, tmp_path):
        b = build(
            tmp_path,
            [template("qb_changes"), {"answer": "One team."}],
            [result(["team"], [["KC"]], source="graph")],
        )
        res = b.eng.ask("Which teams change quarterback this week?")
        assert res.status == "answered" and res.source == "graph"
        run = b.src.runs[0]
        assert run["allow_maps"] is True and run["source"] == "graph"
        assert run["params"]["season"] == 2026 and run["params"]["week"] == 6
        assert res.values["season"] == 2026 and res.values["week"] == 6

    def test_a_hand_written_graph_template_may_not_return_maps(self, tmp_path):
        b = build(
            tmp_path,
            [template("receivers_of", player="Patrick Mahomes"), {"answer": "One."}],
            [result(["receiver"], [["Travis Kelce"]], source="graph")],
        )
        b.eng.ask("Who did Patrick Mahomes throw to?")
        assert b.src.runs[0]["allow_maps"] is False
        assert b.src.runs[0]["params"] == {"player": "00-0033873"}

    def test_the_asof_week_ignores_what_the_model_says(self, tmp_path):
        b = build(
            tmp_path,
            [template("games_this_week", season=1999, week=3), {"answer": "Two games."}],
            [result(["home_team", "away_team"], [["KC", "BUF"], ["DAL", "NYG"]], source="graph")],
        )
        res = b.eng.ask("Which games are on this week?")
        assert res.status == "answered"
        assert b.src.runs[0]["params"] == {"season": 2026, "week": 6}

    def test_filters_keep_the_rows_of_the_asked_team(self, tmp_path):
        rows = [["KC", "BUF"], ["DAL", "NYG"], ["SF", "KC"]]
        b = build(
            tmp_path,
            [template("games_this_week", team="Chiefs"), {"answer": "Two games."}],
            [result(["home_team", "away_team"], rows, source="graph")],
        )
        res = b.eng.ask("Which games are on this week for the Chiefs?")
        assert res.rows == [["KC", "BUF"], ["SF", "KC"]]

    def test_filters_that_remove_everything_make_it_empty_without_an_answer_call(self, tmp_path):
        b = build(
            tmp_path,
            [template("games_this_week", team="Cardinals")],
            [result(["home_team", "away_team"], [["KC", "BUF"]], source="graph")],
        )
        res = b.eng.ask("Which games are on this week for the Cardinals?")
        assert res.status == "empty" and res.answer == FIXED["empty_result"] and res.rows == []
        assert res.route == "template" and b.llm.purposes == ["route"]
        assert res.attempts[0]["outcome"] == "empty"

    def test_an_empty_result_is_final_for_a_template(self, tmp_path):
        b = build(tmp_path, [template("team_wins", team="Dallas")], [result(["wins"], [])])
        res = b.eng.ask("How many games did Dallas win in 1999?")
        assert res.status == "empty" and res.retries == 0 and len(b.llm.requests) == 1

    def test_truncation_reaches_the_answer_prompt(self, tmp_path):
        b = build(
            tmp_path,
            [template("stat_leaders"), {"answer": "A leads."}],
            [result(["player", "v"], [["A", 5]], truncated=True)],
        )
        res = b.eng.ask("Who leads in passing yards?")
        assert res.truncated is True
        assert "The query had more rows than the cap" in b.llm.requests[1]["user"]

    # ---- corrections ----

    def test_bad_values_get_a_correction_round(self, tmp_path):
        b = build(
            tmp_path,
            [template("team_wins", team="Nowhere"), template("team_wins", team="Dallas"), ANSWER],
            [result(["wins"], [[12]])],
        )
        res = b.eng.ask("How many games did Dallas win?")
        assert res.status == "answered" and res.retries == 1
        assert [a["outcome"] for a in res.attempts] == ["bad_values", "rows"]
        assert res.attempts[0]["code"] == "not_found"
        assert b.llm.purposes == ["route", "fix", "answer"]
        extra = b.llm.requests[1]["extra"]
        assert extra[0] == {
            "role": "assistant",
            "content": json.dumps(template("team_wins", team="Nowhere")),
        }
        assert extra[1]["role"] == "user"
        assert (
            "Template team_wins can't use those values: no team matches team."
            in extra[1]["content"]
        )
        assert [c["purpose"] for c in res.calls] == ["route", "fix", "answer"]

    def test_the_fix_call_sees_the_same_prompt_as_the_route_call(self, tmp_path):
        b = build(
            tmp_path,
            [template("team_wins", team="Nowhere"), refuse()],
        )
        b.eng.ask("How many games did Dallas win?")
        a, c = b.llm.requests
        assert (a["system"], a["user"]) == (c["system"], c["user"])

    def test_an_unknown_template(self, tmp_path):
        b = build(tmp_path, [template("nope"), refuse("no_data")])
        res = b.eng.ask("Who leads in sacks?")
        msg = b.llm.requests[1]["extra"][1]["content"]
        assert msg.startswith('There is no usable template "nope". Pick one from the list')
        assert res.attempts[0]["outcome"] == "unknown_template" and res.retries == 1

    def test_a_template_of_a_source_that_is_not_allowed_is_unknown(self, tmp_path):
        b = build(tmp_path, [template("receivers_of", player="Patrick Mahomes"), refuse()])
        res = b.eng.ask("Who did Patrick Mahomes throw to?", source="sql")
        assert res.attempts[0]["outcome"] == "unknown_template"

    def test_an_asof_template_without_a_graph_week_is_unknown(self, tmp_path):
        b = build(
            tmp_path,
            [template("games_this_week"), refuse()],
            asof=None,
            enabled={"graph": True, "sql": True},
        )
        res = b.eng.ask("Which games are on this week?", source="graph")
        assert res.attempts[0]["outcome"] == "unknown_template"

    def test_a_very_long_template_id_is_cut_in_the_message(self, tmp_path):
        b = build(tmp_path, [template("x" * 500), refuse()])
        b.eng.ask("Who leads in sacks?")
        assert "x" * 65 not in b.llm.requests[1]["extra"][1]["content"]

    def test_params_that_are_not_a_dict_are_treated_as_empty(self, tmp_path):
        b = build(
            tmp_path,
            [{"action": "template", "template": "stat_leaders", "params": "tds"}, {"answer": "A."}],
            [result(["player", "v"], [["A", 1]])],
        )
        res = b.eng.ask("Who leads in passing yards?")
        assert res.status == "answered" and res.values == {"stat": "yards"}

    def test_the_corrections_cap_ends_in_an_error_with_the_last_code(self, tmp_path):
        bad = template("team_wins", team="Nowhere")
        b = build(tmp_path, [bad, bad, bad, bad])
        res = b.eng.ask("How many games did Dallas win?")
        assert res.status == "error" and res.retries == 2
        assert res.error == "The query still failed after 2 corrections (not_found)."
        assert b.llm.purposes == ["route", "fix", "fix"]  # no fourth call
        assert res.answer is None

    def test_the_cap_follows_the_config(self, tmp_path):
        bad = template("team_wins", team="Nowhere")
        for rounds in (0, 1, 3):
            cfg = AskConfig.model_validate({"limits": {"corrections": rounds}})
            b = build(tmp_path, [bad] * 6, cfg=cfg)
            res = b.eng.ask("How many games did Dallas win?")
            assert res.retries == rounds and len(b.llm.requests) == rounds + 1, rounds
            assert res.status == "error"

    def test_a_guard_refusal_of_a_template_asks_for_generation(self, tmp_path):
        b = build(
            tmp_path,
            [
                template("team_wins", team="Dallas"),
                generate("sql"),
                {"query": "SELECT 1 AS x"},
                ANSWER,
            ],
            [Refused("sql.table", "teams"), result(["x"], [[12]], query="SELECT 1 AS x")],
        )
        res = b.eng.ask("How many games did Dallas win?")
        msg = b.llm.requests[1]["extra"][1]["content"]
        assert msg == 'Template team_wins failed (sql.table). Reply {"action": "generate", ...}.'
        assert res.route == "generated" and res.retries == 1
        assert [a["outcome"] for a in res.attempts] == ["refused", "rows"]
        assert res.attempts[0]["code"] == "sql.table"

    def test_a_failed_template_run_asks_for_generation(self, tmp_path):
        b = build(
            tmp_path,
            [template("team_wins", team="Dallas"), refuse("no_data")],
            [QueryFailed("timeout", "InterruptException", "secret hint")],
        )
        res = b.eng.ask("How many games did Dallas win?")
        msg = b.llm.requests[1]["extra"][1]["content"]
        assert msg == 'Template team_wins failed (timeout). Reply {"action": "generate", ...}.'
        assert "secret hint" not in json.dumps(res.to_dict())
        assert (
            res.attempts[0]["outcome"] == "failed"
            and res.attempts[0]["code"] == "InterruptException"
        )

    def test_a_name_only_the_value_reveals_is_a_clarification(self, tmp_path):
        b = build(tmp_path, [template("receivers_of", player="Allen")])
        res = b.eng.ask("Who did he throw to?")
        assert res.status == "clarify" and res.route == "clarify"
        assert res.template_id == "receivers_of"
        (choice,) = res.choices
        assert choice["text"] == "Allen" and choice["kind"] == "player"
        assert len(choice["options"]) >= 2 and all(
            o["value"] and o["label"] for o in choice["options"]
        )
        assert res.answer == 'More than one player matches "Allen": which one?'
        assert b.llm.purposes == ["route"] and b.src.runs == []

    def test_busy_in_the_middle_of_a_template_stops_there(self, tmp_path):
        b = build(tmp_path, [template("team_wins", team="Dallas")], [Refused("source.busy")])
        res = b.eng.ask("How many games did Dallas win?")
        assert res.status == "refused"
        assert res.refusal == {"by": "source", "rule": "source.busy", "message": FIXED["busy"]}
        assert b.llm.purposes == ["route"] and res.retries == 0


# ---- generation --------------------------------------------------------------------------------


class TestGenerate:
    def test_a_generated_sql_query_answers(self, tmp_path):
        b = build(
            tmp_path,
            [generate("sql"), {"query": "SELECT team FROM teams"}, {"answer": "KC leads."}],
            [result(["team"], [["KC"]], query="SELECT team FROM teams")],
        )
        res = b.eng.ask("Which team has the best record?")
        assert res.status == "answered" and res.route == "generated" and res.source == "sql"
        assert res.query == "SELECT team FROM teams" and res.params == {}
        assert res.template_id is None and res.retries == 0
        assert [a["outcome"] for a in res.attempts] == ["rows"]
        assert b.llm.purposes == ["route", "generate", "answer"]
        assert b.src.runs == [
            {"source": "sql", "query": "SELECT team FROM teams", "params": {}, "allow_maps": False}
        ]
        assert "Query: a query written for this question" in b.llm.requests[2]["user"]
        assert {"generate", "total", "run", "answer"} <= set(res.timings)

    def test_the_generation_prompt_for_sql(self, tmp_path):
        b = build(tmp_path, [generate("sql"), refuse()])
        b.eng.ask("Which team has the best record, say Dallas?")
        req = b.llm.requests[1]
        assert req["purpose"] == "generate" and "{{" not in req["system"]
        assert "DuckDB SQL over the curated NFL tables" in req["system"]
        assert "- teams:" in req["system"] and "- games:" in req["system"]
        assert "Q: " in req["system"] and "SELECT wins FROM teams" in req["system"]
        assert req["user"].startswith(
            "Source: sql (DuckDB SQL)\nToday: 2026-10-17; current season 2026."
        )
        assert "Question: Which team has the best record, say Dallas?" in req["user"]
        assert '"Dallas" -> team DAL' in req["user"]

    def test_the_generation_prompt_for_the_graph(self, tmp_path):
        b = build(tmp_path, [generate("graph"), refuse()])
        b.eng.ask("Who were the Bills' teammates?")
        req = b.llm.requests[1]
        assert req["user"].startswith("Source: graph (Neo4j Cypher)")
        assert "Neo4j 5 (Cypher), the weekly knowledge graph, READ ONLY" in req["system"]
        assert "The graph is as of 2026 week 6 (results through week 5)." in req["system"]
        assert "{{" not in req["system"]

    def test_examples_are_the_nearest_hand_written_queries_only(self, tmp_path):
        b = build(tmp_path, [generate("graph"), refuse()])
        b.eng.ask("Which receivers did Mahomes throw to?")
        system = b.llm.requests[1]["system"]
        assert 'Q: "Who did Mahomes throw to?" (parameters: $player = player)' in system
        assert "THREW_TO" in system
        assert "q3_qb_change" not in system  # a library template is not an example
        assert "Teams starting a backup quarterback" not in system

    @pytest.mark.parametrize(
        "decision_source, call_source, expected",
        [
            ("graph", "auto", "graph"),
            ("GRAPH", "auto", "graph"),
            ("sql", "auto", "sql"),
            ("history", "auto", "sql"),  # not a source: the first allowed
            ("", "auto", "sql"),
            (None, "auto", "sql"),
            ("sql", "graph", "graph"),  # the caller's restriction wins
            ("graph", "sql", "sql"),
        ],
    )
    def test_the_source_to_generate_for(self, tmp_path, decision_source, call_source, expected):
        decision = {"action": "generate", "source": decision_source}
        b = build(tmp_path, [decision, refuse()])
        b.eng.ask("Who were the Bills' teammates?", source=call_source)
        assert b.llm.requests[1]["user"].startswith(f"Source: {expected}")

    @pytest.mark.parametrize(
        "reply, rule",
        [
            ({"refuse": "write"}, "write"),
            ({"refuse": "NO_DATA"}, "no_data"),
            ({"refuse": True}, "no_data"),
            ({"refuse": "weird"}, "no_data"),
            ({"action": "refuse", "reason": "out_of_range"}, "out_of_range"),
            ({"action": "refuse"}, "no_data"),
            ({"action": "refuse", "reason": "not_nfl"}, "not_nfl"),
        ],
    )
    def test_the_generator_may_refuse(self, tmp_path, reply, rule):
        b = build(tmp_path, [generate("sql"), reply])
        res = b.eng.ask("Who were the Bills' teammates?")
        assert res.status == "refused"
        assert res.refusal == {"by": "generator", "rule": rule, "message": FIXED[rule]}
        assert b.src.runs == []

    def test_a_query_failure_is_corrected_with_the_hint_in_the_prompt_only(self, tmp_path):
        failure = QueryFailed("error", "BinderException", 'Referenced column "nope" not found')
        b = build(
            tmp_path,
            [
                generate("sql"),
                {"query": "SELECT nope FROM teams"},
                {"query": "SELECT 1 AS x"},
                ANSWER,
            ],
            [failure, result(["x"], [[12]], query="SELECT 1 AS x")],
        )
        res = b.eng.ask("Which team has the best record in 2026?")
        assert res.status == "answered" and res.retries == 1
        fix = b.llm.requests[2]
        assert fix["purpose"] == "fix"
        assert fix["extra"][0] == {
            "role": "assistant",
            "content": json.dumps({"query": "SELECT nope FROM teams"}),
        }
        assert fix["extra"][1]["content"] == (
            'That query failed (error). The database said: Referenced column "nope" '
            "not found Fix it."
        )
        assert [a["outcome"] for a in res.attempts] == ["failed", "rows"]
        assert res.attempts[0]["code"] == "BinderException"
        assert "not found" not in json.dumps(res.to_dict())  # the hint stays in the prompt

    def test_a_failure_without_a_hint(self, tmp_path):
        b = build(
            tmp_path,
            [generate("sql"), {"query": "SELECT 1"}, refuse()],
            [QueryFailed("timeout", "InterruptException")],
        )
        b.eng.ask("Which team has the best record?")
        assert b.llm.requests[2]["extra"][1]["content"] == "That query failed (timeout). Fix it."

    def test_a_guard_refusal_is_corrected_with_the_rule_text(self, tmp_path):
        b = build(
            tmp_path,
            [generate("sql"), {"query": "DROP TABLE teams"}, {"query": "SELECT 1 AS x"}, ANSWER],
            [Refused("sql.not_select"), result(["x"], [[12]], query="SELECT 1 AS x")],
        )
        res = b.eng.ask("Which team has the best record?")
        msg = b.llm.requests[2]["extra"][1]["content"]
        assert msg == (
            f"The guard refused that query: {RULES['sql.not_select']} Rewrite it within the rules."
        )
        assert res.status == "answered" and res.attempts[0]["code"] == "sql.not_select"

    def test_a_guard_that_refuses_every_round_ends_refused_by_the_guard(self, tmp_path):
        queries = [{"query": f"DROP TABLE t{i}"} for i in range(3)]
        b = build(tmp_path, [generate("sql"), *queries], [Refused("sql.not_select")] * 3)
        res = b.eng.ask("Which team has the best record?")
        assert res.status == "refused" and res.route == "refused"
        assert res.refusal == {
            "by": "guard",
            "rule": "sql.not_select",
            "message": RULES["sql.not_select"],
        }
        assert res.query == "DROP TABLE t2" and res.source == "sql" and res.retries == 2
        assert res.answer == RULES["sql.not_select"]
        assert b.llm.purposes == ["route", "generate", "fix", "fix"]
        assert len(b.src.runs) == 3

    def test_busy_in_the_middle_of_generation_stops_there(self, tmp_path):
        b = build(tmp_path, [generate("sql"), {"query": "SELECT 1"}], [Refused("source.busy")])
        res = b.eng.ask("Which team has the best record?")
        assert res.refusal == {"by": "source", "rule": "source.busy", "message": FIXED["busy"]}
        assert b.llm.purposes == ["route", "generate"]

    @pytest.mark.parametrize(
        "bad", [{}, {"query": ""}, {"query": "   "}, {"query": None}, {"query": 5}]
    )
    def test_a_reply_without_a_query_is_asked_again(self, tmp_path, bad):
        b = build(
            tmp_path,
            [generate("sql"), bad, {"query": "SELECT 1 AS x"}, ANSWER],
            [result(["x"], [[12]], query="SELECT 1 AS x")],
        )
        res = b.eng.ask("Which team has the best record?")
        assert res.status == "answered" and res.retries == 1
        assert res.attempts[0] == {
            "kind": "generated",
            "source": "sql",
            "query": "",
            "params": {},
            "outcome": "no_query",
        }
        assert b.llm.requests[2]["extra"][1]["content"] == (
            'Reply with {"query": "<one read-only query>"}.'
        )

    def test_empty_then_accepted(self, tmp_path):
        b = build(
            tmp_path,
            [generate("sql"), {"query": "SELECT x FROM t"}, {"accept": True}],
            [result(["x"], [], query="SELECT x FROM t")],
        )
        res = b.eng.ask("Which team won the 1990 Super Bowl?")
        assert res.status == "empty" and res.route == "generated" and res.source == "sql"
        assert res.query == "SELECT x FROM t" and res.columns == ["x"] and res.rows == []
        assert res.answer == FIXED["empty_result"] and res.retries == 1
        assert b.llm.purposes == ["route", "generate", "fix"]  # no answer call
        assert 'reply {"accept": true}' in b.llm.requests[2]["extra"][1]["content"]

    def test_empty_then_a_better_query(self, tmp_path):
        b = build(
            tmp_path,
            [
                generate("sql"),
                {"query": "SELECT 1 WHERE false"},
                {"query": "SELECT 1 AS x"},
                ANSWER,
            ],
            [
                result(["x"], [], query="SELECT 1 WHERE false"),
                result(["x"], [[12]], query="SELECT 1 AS x"),
            ],
        )
        res = b.eng.ask("Which team has the best record?")
        assert res.status == "answered" and res.query == "SELECT 1 AS x" and res.retries == 1
        assert [a["outcome"] for a in res.attempts] == ["empty", "rows"]

    def test_empty_every_round_ends_empty(self, tmp_path):
        b = build(
            tmp_path,
            [generate("sql"), {"query": "q1"}, {"query": "q2"}, {"query": "q3"}],
            [result(["x"], [], query=f"q{i}") for i in (1, 2, 3)],
        )
        res = b.eng.ask("Which team won the 1990 Super Bowl?")
        assert res.status == "empty" and res.query == "q3" and res.retries == 2
        assert res.answer == FIXED["empty_result"] and res.route == "generated"
        assert b.llm.purposes == ["route", "generate", "fix", "fix"]

    def test_accept_needs_an_empty_result_to_accept(self, tmp_path):
        b = build(
            tmp_path,
            [generate("sql"), {"accept": True}, {"query": "SELECT 1 AS x"}, ANSWER],
            [result(["x"], [[12]], query="SELECT 1 AS x")],
        )
        res = b.eng.ask("Which team has the best record?")
        assert res.status == "answered" and res.attempts[0]["outcome"] == "no_query"

    def test_accept_after_a_failure_is_not_an_acceptance(self, tmp_path):
        b = build(
            tmp_path,
            [generate("sql"), {"query": "q1"}, {"accept": True}, {"query": "q2"}],
            [
                QueryFailed("error", "BinderException", "bad"),
                QueryFailed("error", "BinderException", "bad"),
            ],
        )
        res = b.eng.ask("Which team has the best record?")
        assert res.status == "error"

    def test_failures_every_round_end_in_an_error(self, tmp_path):
        fail = QueryFailed("syntax", "ParserException", "oops")
        b = build(
            tmp_path,
            [generate("sql"), {"query": "q1"}, {"query": "q2"}, {"query": "q3"}],
            [fail, fail, fail],
        )
        res = b.eng.ask("Which team has the best record?")
        assert res.status == "error" and res.retries == 2 and res.route == "generated"
        assert res.error == "The query still failed after 2 corrections (ParserException)."
        assert res.query == "q3" and res.source == "sql"

    def test_zero_corrections_means_the_first_failure_is_final(self, tmp_path):
        cfg = AskConfig.model_validate({"limits": {"corrections": 0}})
        b = build(
            tmp_path,
            [generate("sql"), {"query": "q1"}],
            [QueryFailed("error", "BinderException", "x")],
            cfg=cfg,
        )
        res = b.eng.ask("Which team has the best record?")
        assert res.status == "error" and res.retries == 0
        assert b.llm.purposes == ["route", "generate"]

    def test_rows_over_the_cap_are_flagged(self, tmp_path):
        b = build(
            tmp_path,
            [generate("sql"), {"query": "q"}, {"answer": "Many."}],
            [result(["x"], [[i] for i in range(3)], query="q", truncated=True)],
        )
        res = b.eng.ask("List all the games")
        assert res.truncated is True and res.row_count == 3

    def test_the_answer_rows_limit_comes_from_the_config(self, tmp_path):
        cfg = AskConfig.model_validate({"limits": {"answer_rows": 2}})
        b = build(
            tmp_path,
            [generate("sql"), {"query": "q"}, {"answer": "A."}],
            [result(["x"], [[1], [2], [3], [4]], query="q")],
            cfg=cfg,
        )
        b.eng.ask("List all the games")
        user = b.llm.requests[2]["user"]
        assert "first 2 of 4 rows" in user


# ---- model errors, cost, serialisation ------------------------------------------------- -----


class TestErrorsAndCost:
    def test_a_model_error_is_an_error_status_with_fixed_text(self, tmp_path):
        b = build(tmp_path, [AskLLMError("HTTP 500: provider error")])
        res = b.eng.ask("Who leads in sacks?")
        assert res.status == "error" and res.route == "none"
        assert res.error == "The model didn't give a usable reply: HTTP 500: provider error."
        assert [c["purpose"] for c in res.calls] == ["route"] and res.calls[0]["ok"] is False
        assert b.llm.calls == [] and res.timings["total"] >= 0

    def test_out_of_credits_in_the_router_is_raised_not_swallowed(self, tmp_path):
        b = build(tmp_path, [OutOfCredits("HTTP 402: out of OpenRouter credits")])
        with pytest.raises(OutOfCredits):
            b.eng.ask("Who leads in sacks?")
        assert b.llm.calls == []  # the log was still drained

    def test_out_of_credits_in_a_correction_is_raised(self, tmp_path):
        b = build(tmp_path, [template("team_wins", team="Nowhere"), OutOfCredits("402")])
        with pytest.raises(OutOfCredits):
            b.eng.ask("How many games did Dallas win?")

    def test_out_of_credits_in_the_answer_step_is_raised(self, tmp_path):
        b = build(
            tmp_path,
            [template("team_wins", team="Dallas"), OutOfCredits("402")],
            [result(["wins"], [[12]])],
        )
        with pytest.raises(OutOfCredits):
            b.eng.ask("How many games did Dallas win?")

    def test_an_answer_that_fails_the_check_becomes_the_fixed_sentence(self, tmp_path):
        b = build(
            tmp_path,
            [template("team_wins", team="Dallas"), {"answer": "Dallas won 17 games."}],
            [result(["wins"], [[12]])],
        )
        res = b.eng.ask("How many games did Dallas win?")
        assert res.status == "answered" and res.answer_from_llm is False
        assert res.answer == "The query returned 1 row: the numbers are in the table below."
        assert res.answer_check == {"passed": False, "unmatched": ["17"]}

    def test_an_answer_call_error_becomes_the_fixed_sentence(self, tmp_path):
        b = build(
            tmp_path,
            [template("team_wins", team="Dallas"), AskLLMError("HTTP 500: provider error")],
            [result(["wins"], [[12]])],
        )
        res = b.eng.ask("How many games did Dallas win?")
        assert res.status == "answered" and res.answer_from_llm is False
        assert res.answer_check == {"passed": False, "unmatched": ["llm_error"]}
        assert [c["ok"] for c in res.calls] == [True, False]

    def test_every_calls_cost_is_summed(self, tmp_path):
        b = build(
            tmp_path,
            [template("team_wins", team="Nowhere"), template("team_wins", team="Dallas"), ANSWER],
            [result(["wins"], [[12]])],
            cost=0.00125,
        )
        res = b.eng.ask("How many games did Dallas win?")
        assert len(res.calls) == 3 and res.cost == pytest.approx(0.00375)

    def test_the_cost_is_none_when_nothing_reported_one(self, tmp_path):
        b = build(tmp_path, [refuse()], cost=None)
        res = b.eng.ask("Who leads in sacks?")
        assert res.cost is None and len(res.calls) == 1

    def test_a_mix_of_known_and_unknown_costs(self):
        res = AskResult("q", calls=[{"cost": 0.1}, {"cost": None}, {}, {"cost": 0.2}])
        assert res.cost == pytest.approx(0.3)

    def test_to_dict_is_json_ready_and_has_the_derived_fields(self, tmp_path):
        b = build(
            tmp_path,
            [template("team_wins", team="Dallas"), ANSWER],
            [result(["wins"], [[12]])],
        )
        d = b.eng.ask("How many games did Dallas win?").to_dict()
        again = json.loads(json.dumps(d))
        assert again["cost"] == 0.002 and again["row_count"] == 1
        assert again["status"] == "answered" and again["context"]["current_season"] == 2026
        assert set(again) >= {"question", "route", "query", "rows", "calls", "timings", "attempts"}

    def test_the_question_is_stored_whitespace_normalised(self, tmp_path):
        b = build(tmp_path, [refuse()])
        assert b.eng.ask("  Who   leads\n in sacks? ").question == "Who leads in sacks?"

    def test_the_default_status_of_an_untouched_result_is_error(self):
        res = AskResult("q")
        assert res.status == "error" and res.route == "none" and res.cost is None
        assert res.row_count == 0


# ---- through the real guard ---------------------------------------------------------------------


class TestWithTheRealGuard:
    def build(self, tmp_path, replies, driver=None):
        sandbox = make_sandbox(tmp_path)
        sources = Sources(
            sandbox=sandbox, driver=driver, enabled={"sql": True, "graph": driver is not None}
        )
        return build(tmp_path, replies, sources=sources, asof=ASOF)

    def test_a_generated_sql_query_runs_and_is_answered(self, tmp_path):
        b = self.build(
            tmp_path,
            [
                generate("sql"),
                {"query": "SELECT team, wins FROM teams ORDER BY wins DESC LIMIT 1 -- best"},
                {"answer": "KC has 14 wins."},
            ],
        )
        res = b.eng.ask("Which team has the most wins?")
        assert res.status == "answered" and res.rows == [["KC", 14]]
        assert res.columns == ["team", "wins"]
        assert res.query == "SELECT team, wins FROM teams ORDER BY wins DESC LIMIT 1 -- best"
        assert res.answer_from_llm and res.truncated is False

    def test_a_generated_drop_is_refused_by_the_guard_after_the_corrections(self, tmp_path):
        b = self.build(
            tmp_path,
            [
                generate("sql"),
                {"query": "DROP TABLE teams"},
                {"query": "DELETE FROM teams"},
                {"query": "COPY teams TO 'x'"},
            ],
        )
        res = b.eng.ask("Which team has the most wins?")
        assert res.status == "refused" and res.refusal["by"] == "guard"
        assert res.refusal["rule"] == "sql.not_select"
        assert res.query == "COPY teams TO 'x'" and res.retries == 2
        corrections = [r["extra"][-1]["content"] for r in b.llm.requests[2:]]
        assert all(RULES["sql.not_select"] in c for c in corrections)

    def test_a_wide_star_is_corrected_then_answered(self, tmp_path):
        from ask_fakes import write_wide

        write_wide(make_sandbox(tmp_path).curated, "wide", 70)
        b = self.build(
            tmp_path,
            [
                generate("sql"),
                {"query": "SELECT * FROM wide"},
                {"query": "SELECT team FROM wide ORDER BY c1 DESC LIMIT 1"},
                {"answer": "KC is first."},
            ],
        )
        res = b.eng.ask("Which team is first?")
        assert res.status == "answered" and res.retries == 1
        assert RULES["sql.star"] in b.llm.requests[2]["extra"][1]["content"]

    def test_a_graph_template_goes_through_the_fake_driver(self, tmp_path):
        driver = FakeDriver(keys=["receiver"], records=[["Travis Kelce"], ["Rashee Rice"]])
        b = self.build(
            tmp_path,
            [
                template("receivers_of", player="Patrick Mahomes"),
                {"answer": "Travis Kelce is first."},
            ],
            driver=driver,
        )
        res = b.eng.ask("Who did Patrick Mahomes throw to?")
        assert res.status == "answered" and res.rows == [["Travis Kelce"], ["Rashee Rice"]]
        assert driver.tx_runs[0][1] == {"player": "00-0033873"}
        assert driver.sessions[0]["default_access_mode"].lower().startswith("r")

    def test_a_generated_cypher_write_is_refused(self, tmp_path):
        driver = FakeDriver()
        b = self.build(
            tmp_path,
            [generate("graph")] + [{"query": "MATCH (n) DETACH DELETE n"}] * 3,
            driver=driver,
        )
        res = b.eng.ask("Who were the Bills' teammates?")
        assert res.status == "refused" and res.refusal["rule"] == "cypher.write"
        assert driver.sessions == [] and driver.tx_runs == []

    def test_a_generated_cypher_with_a_write_plan_is_refused(self, tmp_path):
        driver = FakeDriver(query_type="w", explain_plan=plan("ProduceResults@neo4j"))
        b = self.build(
            tmp_path,
            [generate("graph")] + [{"query": "MATCH (n) RETURN n.name"}] * 3,
            driver=driver,
        )
        res = b.eng.ask("Who were the Bills' teammates?")
        assert res.refusal["rule"] == "cypher.plan_not_read"
        assert driver.tx_runs == []

    def test_graph_questions_with_no_driver_are_refused_by_the_guard(self, tmp_path):
        sources = Sources(
            sandbox=make_sandbox(tmp_path), driver=None, enabled={"graph": True, "sql": True}
        )
        b = build(
            tmp_path,
            [generate("graph")] + [{"query": "MATCH (n) RETURN n.name"}] * 3,
            sources=sources,
        )
        res = b.eng.ask("Who were the Bills' teammates?", source="graph")
        assert res.status == "refused" and res.refusal["rule"] == "source.off"


# ---- helpers: graph_asof, curated_through, prompt_hash, build -------------------------- -----


def write_graph_result(runs, season, week_dir, **fields):
    d = runs / str(season) / week_dir
    d.mkdir(parents=True, exist_ok=True)
    body = {"status": "ok", "mode": "live", "season": season, "week": int(week_dir[4:]), **fields}
    (d / "graph_results.json").write_text(json.dumps(body), encoding="utf-8")


class TestGraphAsof:
    def test_the_newest_live_ok_week(self, tmp_path):
        runs = tmp_path / "runs"
        paths = SimpleNamespace(runs=runs)
        assert graph_asof(paths, 2026) is None
        write_graph_result(runs, 2026, "week04")
        write_graph_result(runs, 2026, "week05", status="failed")
        write_graph_result(runs, 2026, "week06", mode="rehearsal")
        assert graph_asof(paths, 2026) == (2026, 4)
        write_graph_result(runs, 2026, "week10")
        assert graph_asof(paths, 2026) == (2026, 10)

    def test_other_seasons_and_broken_files_are_ignored(self, tmp_path):
        runs = tmp_path / "runs"
        paths = SimpleNamespace(runs=runs)
        write_graph_result(runs, 2025, "week17")
        write_graph_result(runs, 2026, "week03")
        bad = runs / "2026" / "week07"
        bad.mkdir(parents=True)
        (bad / "graph_results.json").write_text("{not json", encoding="utf-8")
        assert graph_asof(paths, 2026) == (2026, 3)
        assert graph_asof(paths, 2025) == (2025, 17)
        assert graph_asof(paths, 2024) is None


class TestCuratedThrough:
    def test_the_quality_stamp_and_the_newest_completed_week(self, tmp_path):
        import polars as pl

        cur = tmp_path / "curated"
        (cur / "_quality").mkdir(parents=True)
        (cur / "_quality" / "latest.json").write_text(
            json.dumps({"run_at": "2026-10-14T09:00:00Z"}), encoding="utf-8"
        )
        pl.DataFrame(
            {
                "season": [2025, 2026, 2026, 2026, 2026],
                "week": [18, 1, 4, 5, 6],
                "completed": [True, True, True, True, False],
            }
        ).write_parquet(cur / "games.parquet")
        assert curated_through(SimpleNamespace(curated=cur), 2026) == ("2026-10-14T09:00:00Z", 5)
        assert curated_through(SimpleNamespace(curated=cur), 2025) == ("2026-10-14T09:00:00Z", 18)
        assert curated_through(SimpleNamespace(curated=cur), 2030)[1] is None

    def test_nothing_there(self, tmp_path):
        assert curated_through(SimpleNamespace(curated=tmp_path), 2026) == (None, None)
        (tmp_path / "_quality").mkdir()
        (tmp_path / "_quality" / "latest.json").write_text("{broken", encoding="utf-8")
        assert curated_through(SimpleNamespace(curated=tmp_path), 2026) == (None, None)


def test_prompt_hash_is_a_short_stable_hex_digest():
    h = prompt_hash()
    assert len(h) == 12 and int(h, 16) >= 0 and prompt_hash() == h


def test_build_wires_the_pieces_together(tmp_path, monkeypatch):
    import nflengine.ask.route as route_mod

    cur = tmp_path / "curated"
    cur.mkdir()
    seen: dict[str, Any] = {}
    sources = FakeSources([])

    monkeypatch.setattr(route_mod.Sources, "open", classmethod(lambda cls, cfg, paths: sources))
    monkeypatch.setattr(
        route_mod.NameIndex,
        "load",
        classmethod(
            lambda cls, curated, season: seen.update(names=(curated, season)) or standard_index()
        ),
    )
    monkeypatch.setattr(
        route_mod,
        "load_templates",
        lambda current_season=None: seen.update(templates=current_season) or {},
    )
    monkeypatch.setattr(route_mod, "graph_asof", lambda paths, season: (season, 3))
    monkeypatch.setattr(route_mod, "curated_through", lambda paths, season: ("stamp", 2))
    monkeypatch.setattr(
        "nflengine.settings.get_config",
        lambda: SimpleNamespace(seasons=SimpleNamespace(current=2031)),
    )
    paths = SimpleNamespace(curated=cur, runs=tmp_path / "runs")
    eng = AskEngine.build(ScriptedLLM([]), AskConfig(), paths)
    assert seen == {"names": (cur, 2031), "templates": 2031}
    assert eng.current == 2031 and eng.asof == (2031, 3) and eng.curated == ("stamp", 2)
    assert eng.sources is sources
    eng.close()
    assert sources.closed


# ---- the schema's conventions (the 2026-10-18 audit) -------------------------------------------


class TestSchemaConventions:
    """Rules the models got wrong because the schema text didn't state them (or misled)."""

    def test_the_playoff_values_of_each_table_are_stated(self):
        from nflengine.ask.schema import GRAPH_SCHEMA, SQL_RULES

        # models wrote games.game_type = 'POST' (a value games doesn't have): 21-12, not 26-13
        assert "games.game_type IN ('WC', 'DIV', 'CON', 'SB')" in SQL_RULES
        assert "games has no\n  'POST'" in SQL_RULES or "games has no 'POST'" in SQL_RULES
        assert "playoffs included, unless the question says" in SQL_RULES
        assert "playoffs included, unless the question says" in GRAPH_SCHEMA

    def test_ngs_season_figures_come_from_the_season_rows(self):
        from nflengine.ask.schema import TABLE_NOTES

        # "filter NOT is_season_total" made every model recompute 2.591 for NGS's 2.592
        note = TABLE_NOTES["ngs_passing"]
        assert "use them for a whole season" in note and "filter NOT" not in note

    def test_abbreviated_play_by_play_names_are_named_as_such(self):
        from nflengine.ask.schema import SQL_RULES

        assert "abbreviated" in SQL_RULES and "never return them" in SQL_RULES

    def test_the_graph_shows_roster_weeks_and_how_teammates_are_matched(self):
        from nflengine.ask.schema import GRAPH_SCHEMA

        assert "roster_weeks" in GRAPH_SCHEMA
        assert "any(w IN a.roster_weeks WHERE w IN b.roster_weeks)" in GRAPH_SCHEMA

    def test_the_prompts_state_the_answer_shape(self):
        from nflengine.ask.route import PROMPT_DIR

        gen = (PROMPT_DIR / "generate.md").read_text(encoding="utf-8")
        route = (PROMPT_DIR / "route.md").read_text(encoding="utf-8")
        assert "one row for a single answer" in gen and "one league-wide row" in gen
        assert "`limit` 1 for a single answer" in route
