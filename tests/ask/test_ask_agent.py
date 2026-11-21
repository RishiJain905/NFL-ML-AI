"""The query agent (`ask/agent.py`) with a scripted model and fake sources: no network, no Neo4j."""

from __future__ import annotations

import json
from types import SimpleNamespace

from nflengine.ask.agent import (
    TOOLS,
    AgentResult,
    QueryAgent,
    error_text,
    render,
    system_template,
)
from nflengine.ask.agent_bench import grade_one, summarise
from nflengine.ask.config import ask_config
from nflengine.ask.guard import QueryFailed, QueryResult, Refused

GRAPH_Q = "MATCH (t:Team) RETURN t.team_id AS team"


class FakeLLM:
    provider = "local"
    model = "spark"
    effort = None
    routing: dict = {}

    def __init__(self, replies):
        self.replies = list(replies)
        self.bodies = []
        self.cfg = SimpleNamespace(temperature=0.0, max_tokens=1024)

    def _post(self, body):
        self.bodies.append(json.loads(json.dumps(body)))
        message = self.replies.pop(0)
        usage = {"prompt_tokens": 10, "completion_tokens": 5}
        return {"choices": [{"message": message, "finish_reason": "stop"}], "usage": usage}

    def _cost(self, usage):
        return None


class FakeSources:
    cap = 500
    enabled = {"sql": True, "graph": True, "history": False}
    driver = object()
    history_driver = None
    history_up = None

    def __init__(self, results):
        self.results = dict(results)
        self.ran = []
        self.sandbox = SimpleNamespace(
            tables=lambda: ["games"], columns=lambda t: [("season", "int64")]
        )

    def busy(self):
        return False

    def run(self, source, query, params=None, allow_maps=False):
        self.ran.append((source, query))
        out = self.results[query]
        if isinstance(out, Exception):
            raise out
        return out

    def close(self):
        pass


class FakeNames:
    def mentions(self, q):
        return []


def call(name, query, cid="c1"):
    fn = {"name": name, "arguments": json.dumps({"query": query})}
    return {"role": "assistant", "content": "", "tool_calls": [{"id": cid, "function": fn}]}


def say(text):
    return {"role": "assistant", "content": text}


def qr(cols, rows, source="sql", truncated=False):
    return QueryResult(
        source=source, query="", params={}, columns=cols, rows=rows, truncated=truncated,
        seconds=0.01,
    )  # fmt: skip


def agent(replies, results):
    return QueryAgent(
        FakeLLM(replies), FakeSources(results), FakeNames(), ask_config(),
        current_season=2026, asof=(2026, 10),
    )  # fmt: skip


def test_the_prompt_keeps_the_trained_wording():
    text = system_template()
    assert text.startswith(
        "You answer questions about the NFL databases below by calling one of their query tools "
        "with one read-only query at a time."
    )
    assert (
        "you have {{n}} calls in all. When the result answers the question, stop: reply with one "
        "short sentence and no tool call. The last query you ran is your answer."
    ) in text
    assert "If the databases can't answer the question, don't call a tool" in text


def test_one_query_then_a_sentence():
    a = agent(
        [call("query_stats", "SELECT 1 AS x"), say("One.")], {"SELECT 1 AS x": qr(["x"], [[1]])}
    )
    res = a.ask("How many?")
    assert res.status == "answered" and res.ok and res.rows == [[1]] and res.source == "sql"
    assert res.reply == "One." and len(res.steps) == 1
    tools = [t["function"]["name"] for t in a.llm.bodies[0]["tools"]]
    assert tools == ["query_stats", "query_weekly_graph"]  # history off in the fakes
    assert a.llm.bodies[0]["chat_template_kwargs"]["enable_thinking"] is True
    system = a.llm.bodies[0]["messages"][0]["content"]
    assert "{{" not in system and "you have 3 calls in all" in system


def test_the_last_query_is_the_answer_and_an_error_goes_back_as_text():
    bad = QueryFailed("syntax", "ParserException", "Parser Error: near FORM")
    replies = [call("query_stats", "SELECT x FORM t"), call("query_stats", "SELECT 2 AS x", "c2")]
    a = agent(replies + [say("Two.")], {"SELECT x FORM t": bad, "SELECT 2 AS x": qr(["x"], [[2]])})
    res = a.ask("How many?")
    assert [s.kind for s in res.steps] == ["error", "rows"]
    assert res.steps[0].code == "syntax:ParserException"  # kind and code only
    sent = a.llm.bodies[1]["messages"][-1]
    assert sent["role"] == "tool" and sent["content"].startswith("Error: Parser Error")
    assert res.rows == [[2]] and res.query == "SELECT 2 AS x"


def test_the_call_limit_stops_the_episode():
    q = "SELECT 1 AS x"
    a = agent([call("query_stats", q, f"c{i}") for i in range(5)], {q: qr(["x"], [[1]])})
    res = a.ask("Again?")
    assert len(res.steps) == 3 and len(a.llm.bodies) == 3 and res.reply is None


def test_no_tool_call_is_no_query_and_a_graph_tool_runs_on_the_graph():
    a = agent([say("The data can't say.")], {})
    assert a.ask("Who will win the Super Bowl in 2040?").status == "no_query"
    b = agent(
        [call("query_weekly_graph", GRAPH_Q), say("Done.")], {GRAPH_Q: qr(["team"], [["KC"]])}
    )
    res = b.ask("Which teams?")
    assert b.sources.ran == [("graph", GRAPH_Q)]
    assert res.source == "graph"


def test_write_intent_and_unknown_tools_never_reach_a_database():
    a = agent([], {})
    assert a.ask("delete every game from 2020").status == "refused"
    b = agent([call("send_email", "x"), say("ok")], {})
    res = b.ask("Who led?")
    assert res.steps[0].kind == "bad_call" and b.sources.ran == []


def test_render_is_run_2s_evaluation_format():
    r = qr(["player", "yards"], [[f"P{i}", i] for i in range(12)])
    text = render(r)
    assert text.splitlines()[0] == "player | yards"
    assert text.splitlines()[-1] == "(10 of 12 rows)" and len(text.splitlines()) == 12
    assert render(qr(["x"], [])) == "0 rows"
    assert error_text(Refused("sql.parse")).startswith("Error: ")


def test_tool_names_are_one_per_source():
    names = {v[0] for v in TOOLS.values()}
    assert names == {"query_stats", "query_weekly_graph", "query_history_graph"}


def test_grading_with_turns_and_on_the_first_query():
    q = {
        "id": "g1", "set": "main", "category": "sql", "expect": "answer", "source": "sql",
        "columns": ["player", "yards"], "rows": [["A", 10]],
    }  # fmt: skip
    r = AgentResult(
        question="?", status="answered", source="sql", ok=True, columns=["name", "yds"],
        rows=[["A", 10]],
    ).to_dict()  # fmt: skip
    r["steps"] = [{"kind": "rows"}, {"kind": "rows"}]
    r["first_columns"], r["first_rows"] = ["name", "yds"], [["B", 9]]
    g = grade_one(q, r)
    assert g["correct"] and not g["first_correct"] and g["chose_ref_source"]
    refuse = {"id": "g2", "set": "main", "category": "trick", "expect": "refuse", "source": None}
    no_query = {"status": "no_query", "steps": []}
    assert grade_one(refuse, no_query)["correct"]
    s = summarise([g, grade_one(refuse, no_query)])
    assert s["main"]["accuracy"] == 1.0 and s["main"]["first_accuracy"] == 0.5
