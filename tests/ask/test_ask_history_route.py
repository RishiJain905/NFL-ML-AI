"""AE02: Ask's third source, the history graph (`source: history`).

The guard (`Sources.run("history", ...)` on its own driver, the same Cypher checks, its own
down / busy rules), the router (`_allowed`, the prompt's history line, the stop rules, the
refusals before any model call), the schema text, the history templates (every choice renders
and passes the text check; each starts from an indexed anchor), `history_asof` and the golden
set's history questions. Fakes only: no Neo4j, no LLM (`ask_fakes`).
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import re
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from ask_fakes import (
    FakeDriver,
    FakeSources,
    ScriptedLLM,
    ServiceUnavailable,
    make_sandbox,
    plan,
    result,
    standard_index,
)

from nflengine.ask import guard as guard_mod
from nflengine.ask.config import AskConfig
from nflengine.ask.evaluate import CATEGORIES, load_golden
from nflengine.ask.guard import RULES, Refused, Sources, check_cypher, run_cypher
from nflengine.ask.route import FIXED, AskEngine, history_asof, prompt_hash
from nflengine.ask.schema import HISTORY_SCHEMA, history_schema
from nflengine.ask.templates import load_templates, render
from nflengine.ops.lock import run_lock
from nflengine.paths import DataPaths

TODAY = dt.date(2026, 10, 18)
ASOF = (2026, 6)
HISTORY = {
    "first_season": 2018,
    "through_season": 2025,
    "season": 2026,
    "week": 5,
    "built_at": "2026-10-18T10:00:00Z",
    "appended_at": "2026-10-18T11:00:00Z",
    "label": "history graph 2018-2025 + 2026 week 5",
}
ON = {"graph": True, "sql": True, "history": True}

TEMPLATES = [
    {
        "id": "team_wins",
        "source": "sql",
        "title": "Team wins",
        "description": "Wins of a team.",
        "examples": ["How many games did Dallas win?"],
        "params": {"team": {"type": "team"}},
        "query": "SELECT wins FROM teams WHERE team = $team",
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
        "id": "history_third_down_catches",
        "source": "history",
        "title": "3rd-down catches from a QB",
        "description": "Receivers who caught 3rd-down passes from a QB.",
        "examples": ["Who has caught the most 3rd-down passes from Patrick Mahomes?"],
        "params": {"player": {"type": "player"}},
        "query": "MATCH (qb:Player {player_id: $player})-[:PASSED]->(p:Play)<-[:CAUGHT]-"
        "(r:Player) WHERE p.down = 3 RETURN r.name AS receiver, count(p) AS catches "
        "ORDER BY catches DESC",
    },
]


class HistorySources(FakeSources):
    """FakeSources with the history graph's parts (AE02)."""

    def __init__(self, script, *, history_driver="hdriver", up=None, hbusy=False, **kw):
        kw.setdefault("enabled", dict(ON))
        super().__init__(script, **kw)
        self.history_driver = history_driver
        self.history_up = up
        self.hbusy = hbusy

    def history_busy(self) -> bool:
        return self.hbusy


def write_templates(tmp_path):
    path = tmp_path / "templates.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "templates": TEMPLATES}, sort_keys=False), encoding="utf-8"
    )
    return load_templates(path, current_season=2026)


def build(tmp_path, replies, runs=(), *, history=HISTORY, sources=None, **src_kw):
    llm = ScriptedLLM(list(replies))
    src = sources or HistorySources(list(runs), sandbox=make_sandbox(tmp_path), **src_kw)
    eng = AskEngine(
        llm,
        src,
        standard_index(),
        write_templates(tmp_path),
        AskConfig(sources=dict(src.enabled)),
        current_season=2026,
        asof=ASOF,
        curated=("2026-10-14T09:00:00Z", 5),
        today=TODAY,
        history=history,
    )
    return SimpleNamespace(eng=eng, llm=llm, src=src)


def template(tid: str, **params: Any) -> dict[str, Any]:
    return {"action": "template", "template": tid, "params": params}


ANSWER = {"answer": "Travis Kelce has caught 120 3rd-down passes from Patrick Mahomes."}
CATCHES = result(["receiver", "catches"], [["Travis Kelce", 120]], source="history")


def history_sources(driver=None, **kw) -> Sources:
    kw.setdefault("enabled", dict(ON))
    return Sources(sandbox=None, driver=FakeDriver(), history_driver=driver or FakeDriver(), **kw)


# ---- the guard ----------------------------------------------------------------------------


class TestGuard:
    def test_a_history_query_runs_read_only_on_the_history_driver(self):
        hist, weekly = FakeDriver(keys=["receiver"], records=[["Travis Kelce"]]), FakeDriver()
        src = Sources(sandbox=None, driver=weekly, history_driver=hist, enabled=dict(ON))
        out = src.run(
            "history", "MATCH (p:Play {offense: $team}) RETURN count(p) AS n", {"team": "KC"}
        )
        assert out.source == "history" and out.rows == [["Travis Kelce"]]
        assert out.database is None
        assert weekly.sessions == [] and weekly.tx_runs == []  # never the weekly graph
        # EXPLAIN first, then the read, both in READ sessions on the server's default database
        assert hist.session_runs[0][0].startswith("EXPLAIN MATCH")
        assert all(s["database"] is None for s in hist.sessions)
        assert all(str(s["default_access_mode"]).upper().startswith("R") for s in hist.sessions)
        assert len(hist.reads) == 1 and hist.tx_runs[0][1] == {"team": "KC"}

    def test_the_weekly_source_still_says_graph(self):
        src = history_sources()
        assert src.run("graph", "MATCH (n:Team) RETURN n.team_id AS t").source == "graph"
        out = run_cypher(FakeDriver(), "MATCH (n:Team) RETURN n.team_id AS t")
        assert out.source == "graph"  # the default keeps every caller as it was

    def test_run_cypher_refuses_an_unknown_source_name(self):
        with pytest.raises(ValueError):
            run_cypher(FakeDriver(), "MATCH (n) RETURN 1 AS x", source="sql")

    @pytest.mark.parametrize(
        "kw",
        [
            {"history_driver": None, "enabled": dict(ON)},
            {"history_driver": "x", "enabled": {"graph": True, "sql": True, "history": False}},
            {"history_driver": "x", "enabled": {"graph": True, "sql": True}},
        ],
    )
    def test_off_or_no_driver_is_source_off(self, kw):
        drv = FakeDriver() if kw["history_driver"] else None
        src = Sources(sandbox=None, driver=FakeDriver(), **{**kw, "history_driver": drv})
        with pytest.raises(Refused) as e:
            src.run("history", "MATCH (n) RETURN 1 AS x")
        assert e.value.rule == "source.off" and e.value.detail == "history"
        assert drv is None or drv.sessions == []

    def test_down_stops_before_the_driver_with_its_own_rule(self):
        hist = FakeDriver()
        src = history_sources(hist, history_up=lambda: False)
        with pytest.raises(Refused) as e:
            src.run("history", "MATCH (n) RETURN 1 AS x")
        assert e.value.rule == "source.history_down" and hist.sessions == []
        assert "history-graph start" in RULES["source.history_down"]
        assert "weekly graph" not in RULES["source.history_down"]

    def test_the_weekly_check_saying_down_doesnt_stop_the_history_graph(self):
        src = history_sources(graph_up=lambda: False, history_up=lambda: True)
        assert src.run("history", "MATCH (n) RETURN 1 AS x").source == "history"
        with pytest.raises(Refused) as e:
            src.run("graph", "MATCH (n) RETURN 1 AS x")
        assert e.value.rule == "source.down"

    @pytest.mark.parametrize("where", ["explain", "run"])
    @pytest.mark.parametrize("err", [ServiceUnavailable("no route"), OSError("refused")])
    def test_an_unreachable_history_server_is_history_down_not_the_weekly_wording(self, where, err):
        hist = FakeDriver(**({"explain_error": err} if where == "explain" else {"run_error": err}))
        src = history_sources(hist)
        with pytest.raises(Refused) as e:
            src.run("history", "MATCH (n) RETURN 1 AS x")
        assert e.value.rule == "source.history_down"
        # the weekly graph keeps its own rule for the same failure
        weekly = Sources(sandbox=None, driver=FakeDriver(explain_error=err), enabled=dict(ON))
        with pytest.raises(Refused) as e2:
            weekly.run("graph", "MATCH (n) RETURN 1 AS x")
        assert e2.value.rule == "source.down"

    def test_the_history_lock_stops_history_questions_only(self, tmp_path):
        lock = tmp_path / "neo4j-history" / ".history.lock"
        lock.parent.mkdir(parents=True)
        hist = FakeDriver()
        src = history_sources(hist, history_lock=lock)
        assert src.history_busy() is False
        with run_lock(lock, "test history build"):
            assert src.history_busy() is True
            with pytest.raises(Refused) as e:
                src.run("history", "MATCH (n) RETURN 1 AS x")
            assert e.value.rule == "source.history_busy" and hist.sessions == []
            assert src.run("graph", "MATCH (n) RETURN 1 AS x").source == "graph"
        assert src.history_busy() is False

    def test_the_weekly_lock_stops_the_history_graph_too(self, tmp_path):
        weekly_lock = tmp_path / "runs" / ".lock"
        weekly_lock.parent.mkdir(parents=True)
        hist = FakeDriver()
        src = history_sources(hist, lock_file=weekly_lock)
        with run_lock(weekly_lock, "test weekly run"):
            with pytest.raises(Refused) as e:
                src.run("history", "MATCH (n) RETURN 1 AS x")
            assert e.value.rule == "source.busy" and hist.sessions == []

    @pytest.mark.parametrize(
        ("query", "rule"),
        [
            ("MATCH (p:Play) SET p.epa = 0 RETURN 1 AS x", "cypher.write"),
            ("MATCH (p:Play) DETACH DELETE p", "cypher.write"),
            ("CREATE (n:Player {name: 'x'}) RETURN 1 AS x", "cypher.write"),
            ("CALL apoc.export.csv.all('x.csv', {})", "cypher.namespace"),
            ("RETURN apoc.version() AS v", "cypher.namespace"),
            ("LOAD CSV FROM 'file:///etc/passwd' AS r RETURN r", "cypher.banned"),
            (
                "MATCH (n) RETURN n.name AS a; MATCH (m) DETACH DELETE m",
                "cypher.multiple_statements",
            ),
            ("RETURN '\\u0027 + apoc.version() + \\u0027' AS x", "cypher.escape"),
            ("SHOW USERS", "cypher.banned"),
            ("CALL dbms.listConfig()", "cypher.namespace"),
            ("CALL db.labels()", "cypher.namespace"),
            ("MATCH (n) CALL { MATCH (m) SET m.x = 1 } RETURN 1 AS x", "cypher.write"),
        ],
    )
    def test_the_same_text_rules_stop_attacks_before_the_history_driver(self, query, rule):
        hist = FakeDriver()
        with pytest.raises(Refused) as e:
            history_sources(hist).run("history", query)
        assert e.value.rule == rule and hist.sessions == []

    @pytest.mark.parametrize(
        ("kw", "rule"),
        [
            ({"query_type": "rw"}, "cypher.plan_not_read"),
            ({"explain_plan": plan("ProduceResults", "ProcedureCall")}, "cypher.plan_operator"),
            ({"explain_plan": plan("ProduceResults", "LoadCSV")}, "cypher.plan_operator"),
        ],
    )
    def test_the_plan_check_runs_on_the_history_graph_too(self, kw, rule):
        hist = FakeDriver(**kw)
        with pytest.raises(Refused) as e:
            history_sources(hist).run("history", "MATCH (n) RETURN 1 AS x")
        assert e.value.rule == rule and hist.reads == []

    def test_whole_nodes_are_refused_and_maps_only_with_allow_maps(self):
        node = SimpleNamespace(labels={"Play"})
        with pytest.raises(Refused) as e:
            history_sources(FakeDriver(keys=["p"], records=[[node]])).run(
                "history", "MATCH (p:Play) RETURN p"
            )
        assert e.value.rule == "cypher.values"
        maps = FakeDriver(keys=["m"], records=[[{"a": 1}]])
        with pytest.raises(Refused):
            history_sources(maps).run("history", "MATCH (p) RETURN {a: 1} AS m")

    def test_the_row_cap_and_timeout_apply(self):
        hist = FakeDriver(keys=["n"], records=[[i] for i in range(10)])
        src = history_sources(hist, cap=3, timeout_s=4.0)
        out = src.run("history", "MATCH (p:Play) RETURN p.play_id AS n")
        assert out.rows == [[0], [1], [2]] and out.truncated
        assert hist.explain_timeouts == [4.0] and 0 < hist.reads[0]["timeout"] <= 4.0


class TestSourcesOpen:
    def paths(self, tmp_path) -> DataPaths:
        return DataPaths(tmp_path / "root")

    def cfg(self, history: bool) -> AskConfig:
        return AskConfig(sources={"graph": False, "sql": False, "history": history})

    def test_off_never_opens_a_history_driver(self, tmp_path, monkeypatch):
        import nflengine.history_graph.client as client

        monkeypatch.setattr(
            client, "get_history_driver", lambda **kw: pytest.fail("opened a history driver")
        )
        src = Sources.open(self.cfg(False), self.paths(tmp_path))
        assert src.history_driver is None and src.history_lock is None

    def test_on_opens_it_with_a_short_connect_timeout_and_its_own_lock(self, tmp_path, monkeypatch):
        import nflengine.history_graph.client as client

        seen: dict[str, Any] = {}
        drv = FakeDriver()

        def fake(**kw):
            seen.update(kw)
            return drv

        monkeypatch.setattr(client, "get_history_driver", fake)
        paths = self.paths(tmp_path)
        src = Sources.open(self.cfg(True), paths)
        assert src.history_driver is drv and seen == {"connection_timeout": 5.0}
        assert src.history_lock == paths.neo4j_history / ".history.lock"
        assert src.driver is None  # the weekly graph is off here
        src.close()
        assert drv.closed

    def test_no_password_fails_soft(self, tmp_path, monkeypatch):
        import nflengine.history_graph.client as client

        def boom(**kw):
            raise RuntimeError("NEO4J_PASSWORD is not set in the env file.")

        monkeypatch.setattr(client, "get_history_driver", boom)
        src = Sources.open(self.cfg(True), self.paths(tmp_path))
        assert src.history_driver is None
        with pytest.raises(Refused) as e:
            src.run("history", "MATCH (n) RETURN 1 AS x")
        assert e.value.rule == "source.off"

    def test_close_closes_both_drivers(self):
        weekly, hist = FakeDriver(), FakeDriver()
        Sources(sandbox=None, driver=weekly, history_driver=hist).close()
        assert weekly.closed and hist.closed


# ---- the router -----------------------------------------------------------------------------


class TestAllowed:
    def test_auto_offers_history_when_on_with_a_driver_and_a_build(self, tmp_path):
        b = build(tmp_path, [])
        assert b.eng._allowed("auto") == ["sql", "graph", "history"]
        assert b.eng._allowed("history") == ["history"]

    @pytest.mark.parametrize(
        "kw",
        [
            {"enabled": {"graph": True, "sql": True, "history": False}},
            {"enabled": {"graph": True, "sql": True}},
            {"history_driver": None},
            {"history": None},
        ],
    )
    def test_off_no_driver_or_no_build_drops_it(self, tmp_path, kw):
        b = build(tmp_path, [], **kw)
        assert b.eng._allowed("auto") == ["sql", "graph"]
        assert b.eng._allowed("history") == []

    def test_auto_drops_it_while_the_check_says_down_but_an_explicit_pick_keeps_it(self, tmp_path):
        b = build(tmp_path, [], up=lambda: False)
        assert b.eng._allowed("auto") == ["sql", "graph"]
        assert b.eng._allowed("history") == ["history"]

    def test_the_context_names_what_the_history_graph_covers(self, tmp_path):
        assert build(tmp_path, []).eng.context()["history_asof"] == HISTORY
        assert build(tmp_path, [], history=None).eng.context()["history_asof"] is None


class TestRoutePrompt:
    def system(self, b, allowed):
        return b.eng._route_prompt("q", [], allowed)[0]

    def test_the_history_line_and_its_templates_only_when_allowed(self, tmp_path):
        b = build(tmp_path, [])
        on = self.system(b, ["sql", "graph", "history"])
        assert "- history: the history graph (Neo4j), history graph 2018-2025 + 2026 week 5" in on
        assert "history_third_down_catches [history]" in on
        assert '{"action": "generate", "source": "history"}' in on
        assert "3rd and long" in on and "snaps together" in on
        off = self.system(b, ["sql", "graph"])
        assert "history" not in off.lower().replace("history_", "")
        assert "history_third_down_catches" not in off
        assert "{{" not in on and "{{" not in off

    def test_with_history_off_the_prompt_is_as_before_ae02(self, tmp_path):
        b = build(tmp_path, [], enabled={"graph": True, "sql": True, "history": False})
        text = self.system(b, b.eng._allowed("auto"))
        assert "- graph: the weekly knowledge graph" in text
        assert '"source": "graph"}\n   When no template fits.' in text
        assert "relationship questions.\n3." in text

    def test_only_history_says_so(self, tmp_path):
        b = build(tmp_path, [])
        assert "Only this source may be used: history." in self.system(b, ["history"])

    def test_the_generator_gets_the_history_schema(self, tmp_path):
        b = build(tmp_path, [])
        system, user = b.eng._generate_prompt("How often does Pittsburgh blitz?", "history", [])
        assert "the HISTORY graph" in system and "p.part_rushers >= 5" in system
        assert "covers 2018-2025 plus 2026 through week 5" in system
        assert "Source: history (Neo4j Cypher)" in user
        assert "THREW_TO" not in system  # not the weekly graph's schema
        # the examples are the history templates
        assert "history_third_down_catches" not in system  # ids aren't shown: queries are
        assert "(r:Player) WHERE p.down = 3" in system


class TestFlow:
    def test_a_history_template_runs_on_the_history_source(self, tmp_path):
        b = build(
            tmp_path,
            [template("history_third_down_catches", player="Patrick Mahomes"), ANSWER],
            [CATCHES],
        )
        res = b.eng.ask("Who has caught the most 3rd-down passes from Patrick Mahomes?")
        assert res.status == "answered" and res.source == "history"
        assert res.template_id == "history_third_down_catches"
        assert b.src.runs[0]["source"] == "history"
        assert b.src.runs[0]["params"] == {"player": "00-0033873"}
        assert b.src.runs[0]["allow_maps"] is False

    def test_a_generated_history_query(self, tmp_path):
        q = "MATCH (p:Play {defense: 'PIT'}) WHERE p.down = 3 RETURN count(p) AS plays"
        out = result(["plays"], [[812]], source="history", query=q)
        b = build(
            tmp_path,
            [{"action": "generate", "source": "history"}, {"query": q}, {"answer": "812 plays."}],
            [out],
        )
        res = b.eng.ask("How many 3rd-down plays has Pittsburgh's defense faced?")
        assert res.route == "generated" and res.source == "history" and res.rows == [[812]]
        assert b.src.runs == [{"source": "history", "query": q, "params": {}, "allow_maps": False}]
        assert b.llm.purposes == ["route", "generate", "answer"]

    def test_generate_history_when_it_isnt_allowed_falls_back(self, tmp_path):
        q = "SELECT 1 AS x"
        b = build(
            tmp_path,
            [{"action": "generate", "source": "history"}, {"query": q}, {"answer": "1."}],
            [result(["x"], [[1]])],
            enabled={"graph": True, "sql": True, "history": False},
        )
        b.eng.ask("How many plays?")
        assert b.src.runs[0]["source"] == "sql"

    @pytest.mark.parametrize(
        ("rule", "fixed"),
        [("source.history_down", "history_down"), ("source.history_busy", "history_busy")],
    )
    def test_the_two_new_rules_stop_a_template_at_once(self, tmp_path, rule, fixed):
        b = build(
            tmp_path,
            [template("history_third_down_catches", player="Patrick Mahomes")],
            [Refused(rule)],
        )
        res = b.eng.ask("Who has caught the most 3rd-down passes from Patrick Mahomes?")
        assert res.status == "refused" and res.retries == 0
        assert res.refusal == {"by": "source", "rule": rule, "message": FIXED[fixed]}
        assert b.llm.purposes == ["route"]  # no correction round, no answer call

    def test_the_two_new_rules_stop_a_generated_query_at_once(self, tmp_path):
        b = build(
            tmp_path,
            [
                {"action": "generate", "source": "history"},
                {"query": "MATCH (p:Play) RETURN 1 AS x"},
            ],
            [Refused("source.history_down")],
        )
        res = b.eng.ask("How many plays are there?")
        assert res.refusal["rule"] == "source.history_down" and res.refusal["by"] == "source"
        assert res.answer == FIXED["history_down"] and b.llm.purposes == ["route", "generate"]

    def test_the_wording(self):
        assert "uv run nfl history-graph start" in FIXED["history_down"]
        assert "tables still work" in FIXED["history_down"]
        assert "rebuilt or appended" in FIXED["history_busy"]
        assert "uv run nfl history-graph build" in FIXED["history_none"]
        assert "weekly graph (Neo4j) isn't answering" in FIXED["graph_down"]  # unchanged

    @pytest.mark.parametrize(
        ("kw", "rule", "fixed", "by"),
        [
            ({"up": lambda: False}, "source.history_down", "history_down", "source"),
            ({"hbusy": True}, "source.history_busy", "history_busy", "source"),
            ({"history": None}, "source.history_none", "history_none", "code"),
            (
                {"enabled": {"graph": True, "sql": True, "history": False}},
                "source.off",
                "source_off",
                "code",
            ),
            ({"history_driver": None}, "source.off", "source_off", "code"),
        ],
    )
    def test_asking_the_history_graph_alone_refuses_before_any_model_call(
        self, tmp_path, kw, rule, fixed, by
    ):
        b = build(tmp_path, [], **kw)
        res = b.eng.ask("How many snaps did Travis Kelce and Patrick Mahomes share?", "history")
        assert res.status == "refused"
        assert res.refusal == {"by": by, "rule": rule, "message": FIXED[fixed]}
        assert b.llm.requests == [] and b.src.runs == []

    def test_down_in_auto_still_answers_from_the_other_sources(self, tmp_path):
        b = build(tmp_path, [template("team_wins", team="Dallas"), {"answer": "7 wins."}],
                  [result(["wins"], [[7]])], up=lambda: False)  # fmt: skip
        res = b.eng.ask("How many games did Dallas win?")
        assert res.status == "answered" and res.source == "sql"
        assert "history_third_down_catches" not in b.llm.requests[0]["system"]


class TestHistoryAsof:
    def write(self, paths: DataPaths, build: dict | None, append: dict | None = None) -> None:
        paths.neo4j_history.mkdir(parents=True, exist_ok=True)
        if build is not None:
            (paths.neo4j_history / "build.json").write_text(json.dumps(build), encoding="utf-8")
        if append is not None:
            (paths.neo4j_history / "append.json").write_text(json.dumps(append), encoding="utf-8")

    def test_it_reads_the_build_files_only_when_on(self, tmp_path):
        paths = DataPaths(tmp_path / "root")
        on, off = AskConfig(sources=dict(ON)), AskConfig()
        assert history_asof(paths, on) is None  # no build yet
        build_ = {"status": "ok", "build_id": "b1", "first_season": 2018, "through_season": 2025}
        append = {"status": "ok", "build_id": "b1", "season": 2026, "through_week": 5}
        self.write(paths, build_, append)
        got = history_asof(paths, on)
        assert got["label"] == "history graph 2018-2025 + 2026 week 5"
        assert got["through_season"] == 2025 and got["week"] == 5
        assert history_asof(paths, off) is None

    def test_a_broken_build_file_is_none(self, tmp_path):
        paths = DataPaths(tmp_path / "root")
        paths.neo4j_history.mkdir(parents=True)
        (paths.neo4j_history / "build.json").write_text('{"status": "ok"}', encoding="utf-8")
        assert history_asof(paths, AskConfig(sources=dict(ON))) is None

    def test_refresh_rereads_it(self, tmp_path, monkeypatch):
        b = build(tmp_path, [], history=None)
        paths = DataPaths(tmp_path / "root")
        self.write(paths, {"status": "ok", "build_id": "b1", "first_season": 2018,
                           "through_season": 2025})  # fmt: skip
        from nflengine.ask import route as route_mod

        monkeypatch.setattr(
            route_mod.NameIndex, "load", classmethod(lambda c, *a, **k: b.eng.names)
        )
        b.eng.refresh(paths)
        assert b.eng.history["label"] == "history graph 2018-2025"


# ---- the schema text --------------------------------------------------------------------------


class TestSchema:
    def test_the_conventions_an_llm_needs(self):
        s = HISTORY_SCHEMA
        for must in [
            "p.down = 3 AND p.distance >= 7",
            "p.yards_to_goal <= 20",
            "PASSED includes sacks",
            "p.pass_attempt",
            "Scrambles are RUSHED",
            "2018-2025",
            "p.part",
            "p.ftn",
            "season_type 'REG'",
            "playoffs included",
            "quarter 5 = overtime",
            "On a kickoff the offense is the receiving team",
            "(Player)-[:ON_FIELD {side",
            "(Play)-[:IN_DRIVE]->(Drive)",
            "player_id",
        ]:
            assert must in s, must

    def test_the_project_s_own_distance_buckets(self):
        from nflengine.playcalling.labels import MEDIUM_MAX, RED_ZONE_MAX, SHORT_MAX

        assert f"long = p.down = 3 AND p.distance >= {MEDIUM_MAX + 1}" in HISTORY_SCHEMA
        assert f"short 1-{SHORT_MAX}" in HISTORY_SCHEMA
        assert f"p.yards_to_goal <= {RED_ZONE_MAX}" in HISTORY_SCHEMA

    def test_every_property_in_the_contract_is_in_the_schema_text(self):
        from nflengine.history_graph.schema import NODES, RELATIONSHIPS

        for n in NODES.values():
            assert n.label in HISTORY_SCHEMA
            for prop in [n.key, *n.props]:
                assert re.search(rf"\b{prop}\b", HISTORY_SCHEMA), (n.label, prop)
        for r in RELATIONSHIPS:
            assert r in HISTORY_SCHEMA, r

    def test_history_schema_adds_what_the_build_covers(self):
        assert history_schema(None) == HISTORY_SCHEMA
        text = history_schema(HISTORY)
        assert text.endswith(
            "covers 2018-2025 plus 2026 through week 5 (history graph 2018-2025 + 2026 week 5)."
        )
        assert history_schema({**HISTORY, "season": None, "week": None}).endswith(
            "covers 2018-2025 (history graph 2018-2025 + 2026 week 5)."
        )

    def test_the_prompt_hash_covers_it(self, monkeypatch):
        from nflengine.ask import schema

        before = prompt_hash()
        monkeypatch.setattr(schema, "HISTORY_SCHEMA", HISTORY_SCHEMA + " x")
        assert prompt_hash() != before


# ---- the real history templates ---------------------------------------------------------------


class Resolver:
    def team(self, text):
        return "KC"

    def player(self, text):
        return SimpleNamespace(status="unique", value="00-0033873", matches=())

    def coach(self, text):
        return SimpleNamespace(status="unique", value="Andy Reid", matches=())


@pytest.fixture(scope="module")
def real():
    return {t.id: t for t in load_templates(current_season=2026).values() if t.source == "history"}


class TestRealHistoryTemplates:
    def test_there_are_enough_and_they_are_documented(self, real):
        assert len(real) >= 8
        for t in real.values():
            assert t.id.startswith("history_"), t.id
            assert t.examples and t.title and t.description, t.id
            assert not t.asof and not t.library, t.id  # the weekly graph's features only

    def test_every_choice_renders_and_passes_the_text_check(self, real):
        n = 0
        for t in real.values():
            base = {p.name: "x" for p in t.params if p.required}
            frag = [p for p in t.params if p.fragments]
            for combo in itertools.product(*[p.choices for p in frag]):
                vals = {**base, **{p.name: c for p, c in zip(frag, combo, strict=True)}}
                r = render(t, vals, Resolver())
                assert r.source == "history"
                assert check_cypher(r.query)
                assert not re.search(r"\{[a-z_]+\}", r.query), (t.id, combo)  # no slot left
                assert set(re.findall(r"\$(\w+)", r.query)) <= set(r.params), t.id
                n += 1
        assert n > 100

    def test_each_starts_from_an_indexed_anchor(self, real):
        anchors = (
            "{offense: $team}",
            "{defense: $team}",
            "{player_id: $player}",
            "(d:Drive {team: $team})",
        )
        for t in real.values():
            first = t.query.strip().splitlines()[0]
            assert first.startswith("MATCH") and any(a in first for a in anchors), t.id

    def test_scrimmage_and_situation_conventions(self, real):
        sit = real["history_qb_receivers_situation"].param("situation")
        frags = sit.fragments
        assert frags["3rd_long"] == "p.down = 3 AND p.distance >= 7"
        assert frags["3rd_short"] == "p.down = 3 AND p.distance <= 3"
        assert frags["red_zone"] == "p.yards_to_goal <= 20"
        # the same situations everywhere they're offered
        for t in real.values():
            p = t.param("situation")
            if p is not None:
                assert p.fragments == frags, t.id
            g = t.param("games")
            assert g is not None and g.default == "all", t.id  # playoffs count by default

    def test_leaders_keep_ties_at_the_cut(self, real):
        for tid in (
            "history_qb_receivers_situation",
            "history_team_targets_situation",
            "history_team_carries_situation",
        ):
            q = real[tid].query
            assert "[$limit - 1] AS cut" in q and "x.m >= cut" in q, tid
            assert "LIMIT" not in q, tid

    def test_participation_templates_stop_at_2025(self, real):
        for tid in (
            "history_defense_coverage",
            "history_qb_vs_coverage",
            "history_receiver_routes",
            "history_snaps_together",
            "history_player_snaps_vs_team",
            "history_team_personnel",
        ):
            t = real[tid]
            assert t.param("last_season").max == 2025 and t.param("last_season").default == 2025
            assert t.param("first_season").min == 2018

    def test_they_run_through_the_guard_on_a_fake_history_driver(self, real):
        for t in real.values():
            vals = {p.name: "x" for p in t.params if p.required}
            r = render(t, vals, Resolver())
            hist = FakeDriver(keys=["a"], records=[[1]])
            out = history_sources(hist).run("history", r.query, r.params)
            assert out.source == "history", t.id
            assert hist.tx_runs[0][1] == r.params


# ---- the golden set -------------------------------------------------------------------------


class TestGolden:
    def test_history_questions_exist_with_references_on_the_history_source(self):
        golden = load_golden(templates=load_templates(current_season=2026))
        hist = [g for g in golden if g.source == "history"]
        assert len(hist) >= 10
        assert "history" in CATEGORIES
        assert sum(g.category == "history" for g in hist) >= 4  # generate-style
        assert sum(g.category == "template" for g in hist) >= 6
        for g in hist:
            assert g.reference and check_cypher(g.reference), g.id
            if g.template:
                assert g.template.startswith("history_"), g.id
        ids = [int(g.id[1:]) for g in hist]
        assert min(ids) > 116 and max(ids) < 200  # after AE01's g116, below the held-out g2xx


def test_the_guard_module_names_the_history_rules():
    assert {"source.history_down", "source.history_busy"} <= set(guard_mod.RULES)


# ---- Sol's review (AE02): an unfinished build / append, and the eval's probe ------------------


def test_an_unfinished_marker_beside_the_lock_makes_the_history_graph_busy(tmp_path):
    lock = tmp_path / ".history.lock"
    src = Sources(sandbox=None, driver=None, history_lock=lock)
    assert not src.history_busy()
    (tmp_path / ".history.unfinished").write_text('{"what": "append"}', encoding="utf-8")
    assert src.history_busy()


def test_the_busy_wording_names_an_unfinished_run():
    assert "didn't finish" in RULES["source.history_busy"]
    assert "didn't finish" in FIXED["history_busy"]


def test_the_eval_probes_the_history_graph_once_and_pins_the_answer():
    from nflengine.ask.evaluate import _history_available

    calls = []
    sources = SimpleNamespace(
        history_up=lambda: calls.append(1) or False, history_busy=lambda: False
    )
    engine = SimpleNamespace(_history_ready=lambda: True, sources=sources)
    assert _history_available(engine) is False  # built, but the container is stopped (Sol)
    assert calls == [1] and engine.sources.history_up() is False  # "auto" leaves it out too

    sources = SimpleNamespace(history_up=lambda: True, history_busy=lambda: True)
    assert (
        _history_available(SimpleNamespace(_history_ready=lambda: True, sources=sources)) is False
    )

    sources = SimpleNamespace(history_up=lambda: True, history_busy=lambda: False)
    engine = SimpleNamespace(_history_ready=lambda: True, sources=sources)
    assert _history_available(engine) is True and engine.sources.history_up() is True

    off = SimpleNamespace(_history_ready=lambda: False, sources=SimpleNamespace())
    assert _history_available(off) is False
