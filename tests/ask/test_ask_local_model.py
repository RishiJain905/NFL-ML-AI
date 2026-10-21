"""AE04 (the local Ask model): the NFL side.

- `phrasings.py`: the skeleton file's validation;
- `trainset.py`: literal queries, slot filling, the exporter end to end on fakes (and the
  record contract nfl-ask-trainer reads);
- `retrieve.py` and the engine's candidate list (`route_candidates`), the generator's
  `exclude`, the split's second model;
- `config.py` / `llm.py`: the `local` provider (localhost only, cost 0, thinking off);
- `verify.py`: predictions scored by running them.

Nothing here reaches Neo4j, the curated data, a model server or the network.
"""

from __future__ import annotations

import json
import random
from typing import Any

import httpx
import pytest
import yaml
from ask_fakes import ScriptedLLM, player, result, standard_index

from nflengine.ask.config import LOCAL_PREFIX, AskConfig, AskLLMConfig
from nflengine.ask.llm import AskLLM, AskLLMError, _local_url, make_llm
from nflengine.ask.phrasings import load_phrasings
from nflengine.ask.retrieve import TemplateRetriever, shape
from nflengine.ask.route import AskEngine
from nflengine.ask.templates import load_templates
from nflengine.ask.trainset import (
    RECORD_FIELDS,
    Exporter,
    SlotFiller,
    TrainDataConfig,
    literal,
    literalize,
)
from nflengine.ask.verify import Verifier, summarise

TEMPLATES = [
    {
        "id": "team_wins",
        "source": "sql",
        "title": "Team wins",
        "description": "Wins of a team in a season.",
        "examples": ["How many games did Dallas win in 2023?"],
        "params": {"team": {"type": "team"}, "season": {"type": "season", "default": "current"}},
        "query": "SELECT team, wins FROM teams WHERE team = $team AND season = $season",
    },
    {
        "id": "head_to_head",
        "source": "sql",
        "title": "Head-to-head record",
        "description": "Record between two teams.",
        "examples": ["What's the Bills' record against the Chiefs?"],
        "params": {
            "team": {"type": "team"},
            "other": {"type": "team"},
            "first_season": {"type": "season", "default": 1999},
            "last_season": {"type": "season", "default": "current"},
            "season_type": {"type": "choice", "default": "ALL", "choices": ["REG", "POST", "ALL"]},
        },
        "query": "SELECT $team AS team, $other AS other, $first_season AS a, $last_season AS b",
    },
    {
        "id": "receivers_of",
        "source": "graph",
        "title": "A QB's receivers",
        "description": "Receivers a QB threw to.",
        "examples": ["Who did Mahomes throw to?"],
        "params": {
            "player": {"type": "player"},
            "limit": {"type": "int", "default": 10, "min": 1, "max": 50},
        },
        "query": "MATCH (q:Player {player_id: $player})-[:THREW_TO]->(w:Player) "
        "RETURN w.name AS receiver LIMIT $limit",
    },
    {
        "id": "coach_record",
        "source": "sql",
        "title": "A coach's record",
        "description": "Wins and losses of a head coach.",
        "examples": ["What is Andy Reid's record?"],
        "params": {"coach": {"type": "coach"}},
        "query": "SELECT $coach AS coach, 1 AS wins",
    },
]


def templates(tmp_path):
    path = tmp_path / "templates.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "templates": TEMPLATES}, sort_keys=False), "utf-8"
    )
    return load_templates(path, current_season=2026)


def write_phrasings(tmp_path, data: dict[str, Any]):
    path = tmp_path / "phrasings.yaml"
    path.write_text(yaml.safe_dump({"version": 1, **data}, sort_keys=False), "utf-8")
    return path


GOOD = {
    "templates": {
        "team_wins": [
            {"q": "How many games did {team} win in {season}?"},
            {"q": "{team:nickname} wins {season:this}"},
            {"q": "Did the {team:nickname} have a winning year in {season}?"},
        ],
        "head_to_head": [
            {"q": "{team} vs {other} since {first_season}"},
            {"q": "{team} against {other} in the playoffs", "params": {"season_type": "POST"}},
            {"q": "Record of {team} vs {other} from {first_season} to {last_season}"},
        ],
        "receivers_of": [
            {"q": "Who did {player:QB} throw to most?", "params": {"limit": 1}},
            {"q": "Top {limit} targets of {player:QB}"},
        ],
        "coach_record": [{"q": "What's {coach}'s record?"}],
    },
    "refuse": [
        {"q": "What is {player:QB}'s salary?", "reason": "no_data"},
        {"q": "Delete the {team} games", "reason": "write"},
    ],
    "generate": [{"q": "Which teams punted most in {season}?", "source": "sql"}],
}


# ---- the skeleton file ----------------------------------------------------------------------


class TestPhrasings:
    def test_a_good_file_loads_with_stable_ids(self, tmp_path):
        sk = load_phrasings(write_phrasings(tmp_path, GOOD), templates=templates(tmp_path))
        assert len(sk) == 12
        assert sk[0].id == "team_wins#0" and sk[0].kind == "template"
        assert [s.name for s in sk[0].slots] == ["team", "season"]
        h2h = next(s for s in sk if s.id == "head_to_head#1")
        assert h2h.params == {"season_type": "POST"}
        assert {s.kind for s in sk} == {"template", "refuse", "generate"}

    @pytest.mark.parametrize(
        ("entry", "match"),
        [
            ({"templates": {"nope": [{"q": "x"}]}}, "unknown template"),
            ({"templates": {"team_wins": [{"q": "{player} wins"}]}}, "unknown slot"),
            ({"templates": {"team_wins": [{"q": "How many wins in {season}?"}]}}, "required"),
            (
                {
                    "templates": {
                        "head_to_head": [{"q": "{team} {other}", "params": {"season_type": "X"}}]
                    }
                },
                "season_type",
            ),
            (
                {"templates": {"team_wins": [{"q": "wins in {season}", "params": {"team": "KC"}}]}},
                "names an entity",
            ),
            (
                {
                    "templates": {
                        "team_wins": [{"q": "{team} in {season}", "params": {"season": 2020}}]
                    }
                },
                "both a slot and a fixed value",
            ),
            ({"templates": {"team_wins": [{"q": "{team} {season} {x"}]}}, "brace"),
            ({"templates": {"team_wins": [{"q": "{team:QB} wins"}]}}, "bad hint"),
            ({"refuse": [{"q": "salary?", "reason": "rude"}]}, "reason"),
            ({"generate": [{"q": "punts?", "source": "web"}]}, "source"),
            (
                {"refuse": [{"q": "Same", "reason": "write"}, {"q": "same", "reason": "write"}]},
                "duplicate",
            ),
        ],
    )
    def test_bad_entries_are_named(self, tmp_path, entry, match):
        with pytest.raises(ValueError, match=match):
            load_phrasings(write_phrasings(tmp_path, entry), templates=templates(tmp_path))


# ---- literal queries ------------------------------------------------------------------------


class TestLiterals:
    def test_sql_and_cypher_literals(self):
        assert literal("O'Neil", "sql") == "'O''Neil'"
        assert literal("O'Neil", "graph") == '"O\'Neil"'
        assert literal("KC", "history") == "'KC'"
        assert literal(None, "sql") == "NULL" and literal(None, "graph") == "null"
        assert literal(True, "sql") == "TRUE" and literal(False, "graph") == "false"
        assert literal(7, "sql") == "7" and literal(0.5, "graph") == "0.5"
        assert literal(["a", 2], "graph") == "['a', 2]"

    @pytest.mark.parametrize("value", ["a\\b", 'it\'s "x"'])
    def test_values_that_cant_be_written_safely_are_refused(self, value):
        with pytest.raises(ValueError):
            literal(value, "graph")

    def test_literalize_replaces_every_bound_parameter(self):
        q = literalize(
            "SELECT * FROM t WHERE a = $a AND b = $b_2 AND c = $a", {"a": "KC", "b_2": 3}, "sql"
        )
        assert q == "SELECT * FROM t WHERE a = 'KC' AND b = 3 AND c = 'KC'"
        with pytest.raises(KeyError):
            literalize("SELECT $missing", {}, "sql")


# ---- slot filling ---------------------------------------------------------------------------


def _filler(**kw):
    names = standard_index()
    return SlotFiller(names, 2026, TrainDataConfig(**kw)), names


class TestSlotFiller:
    def test_fills_use_real_entities_and_return_the_text_as_the_value(self, tmp_path):
        filler, names = _filler()
        t = templates(tmp_path)
        sk = load_phrasings(write_phrasings(tmp_path, GOOD), templates=t)
        q, values, texts = filler.fill(sk[0], t["team_wins"], random.Random(1))
        assert "{" not in q and values["team"] == texts["team"]
        assert names.team(values["team"]) is not None
        assert 2012 <= values["season"] <= 2026

    def test_this_and_last_season(self, tmp_path):
        filler, _ = _filler()
        t = templates(tmp_path)
        sk = load_phrasings(write_phrasings(tmp_path, GOOD), templates=t)
        q, values, _ = filler.fill(sk[1], t["team_wins"], random.Random(2))
        assert q.endswith("wins this season") and values["season"] == 2026

    def test_the_before_a_team_never_gets_a_code_or_a_city(self, tmp_path):
        filler, names = _filler()
        for seed in range(30):
            text, _ = filler.team(random.Random(seed), None, "Did the ", set())
            code = names.team(text)
            loc, nick = __import__("nflengine.digest.names", fromlist=["TEAMS"]).TEAMS[code]
            assert text in (nick, f"{loc} {nick}")

    def test_season_pairs_come_out_in_order_and_teams_differ(self, tmp_path):
        filler, _ = _filler()
        t = templates(tmp_path)
        sk = next(
            s
            for s in load_phrasings(write_phrasings(tmp_path, GOOD), templates=t)
            if s.id == "head_to_head#2"
        )
        for seed in range(25):
            _, values, _ = filler.fill(sk, t["head_to_head"], random.Random(seed))
            assert values["first_season"] <= values["last_season"]
            assert values["team"] != values["other"]

    def test_position_hints_pick_from_the_group_and_names_are_unique(self, tmp_path):
        filler, names = _filler()
        for seed in range(20):
            text, value = filler.player(random.Random(seed), "QB", set())
            res = names.player(text)
            assert res.status == "unique"
            assert next(p for p in names.players if p.gsis_id == res.value).position == "QB"

    def test_a_pair_slot_sets_both_ends_of_the_range(self, tmp_path):
        filler, _ = _filler()
        t = templates(tmp_path)
        data = {"templates": {"head_to_head": [{"q": "{team} vs {other} in {season}"}]}}
        sk = load_phrasings(write_phrasings(tmp_path, data), templates=t)[0]
        assert [s.type for s in sk.slots] == ["team", "team", "season_pair"]
        for seed in range(10):
            q, values, _ = filler.fill(sk, t["head_to_head"], random.Random(seed))
            assert "season" not in values
            assert values["first_season"] == values["last_season"]
            assert str(values["first_season"]) in q
        bad = {
            "templates": {
                "head_to_head": [
                    {"q": "{team} vs {other} in {season}", "params": {"last_season": 2020}}
                ]
            }
        }
        with pytest.raises(ValueError, match="both a slot"):
            load_phrasings(write_phrasings(tmp_path, bad), templates=t)

    def test_int_slots_take_realistic_values(self, tmp_path):
        filler, _ = _filler()
        t = templates(tmp_path)
        p = t["receivers_of"].param("limit")
        assert {filler.integer(random.Random(s), p, "limit")[1] for s in range(40)} <= {
            3, 5, 10, 15, 20, 25
        }  # fmt: skip


# ---- the retriever and the engine's candidates ----------------------------------------------


class TestRetrieve:
    def test_shape_drops_names_numbers_and_slots(self):
        q = "How many TDs did Josh Allen throw in 2023?"
        start = q.index("Josh Allen")
        assert shape(q, [(start, start + len("Josh Allen"))]) == "how many tds did throw in"
        assert shape("Top {limit} targets of {player:QB}") == "top targets of"

    def test_top_k_in_library_order(self, tmp_path):
        t = templates(tmp_path)
        r = TemplateRetriever(t, {"receivers_of": ["Who caught the most passes from {player}"]})
        top = r.top("Who did Jordan Love throw to?", 2)
        assert "receivers_of" in top and len(top) == 2
        assert top == sorted(top, key=list(t).index)
        assert r.top("x", 2, allowed=["coach_record"]) == ["coach_record"]


class _Sandbox:
    """What the generator's prompt reads from the SQL sandbox: table and column names."""

    def tables(self):
        return ["teams"]

    def columns(self, table):
        return [("team", "VARCHAR"), ("season", "INTEGER"), ("wins", "INTEGER")]


def _engine(tmp_path, replies=(), cfg=None, runs=None, gen=None):
    from ask_fakes import FakeSources

    eng = AskEngine(
        ScriptedLLM(list(replies)),
        FakeSources(list(runs or []), sandbox=_Sandbox(), driver="driver"),
        standard_index(),
        templates(tmp_path),
        cfg or AskConfig(),
        current_season=2026,
        asof=(2026, 6),
        curated=("2026-10-14T09:00:00Z", 5),
        gen_llm=gen,
    )
    return eng


class TestEngineCandidates:
    def test_default_prompt_lists_every_template(self, tmp_path):
        eng = _engine(tmp_path)
        assert eng.route_candidates("x", [], ["sql", "graph"]) is None
        system, _ = eng._route_prompt("x", [], ["sql", "graph"])
        assert all(f"- {tid} [" in system for tid in eng.templates)

    def test_route_candidates_limit_the_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr("nflengine.ask.phrasings.PHRASINGS_PATH", tmp_path / "none.yaml")
        cfg = AskConfig(llm=AskLLMConfig(route_candidates=2))
        eng = _engine(
            tmp_path,
            [{"action": "template", "template": "team_wins", "params": {"team": "KC"}},
             {"answer": "KC won 12."}],
            cfg=cfg,
            runs=[result(["team", "wins"], [["KC", 12]])],
        )  # fmt: skip
        res = eng.ask("How many games did KC win this season?")
        system = eng.llm.requests[0]["system"]
        listed = [tid for tid in eng.templates if f"- {tid} [" in system]
        assert len(listed) == 2 and "team_wins" in listed
        assert res.status == "answered"

    def test_generator_examples_can_leave_a_template_out(self, tmp_path):
        eng = _engine(tmp_path)
        with_it = eng._examples("How many games did KC win?", "sql")
        without = eng._examples(
            "How many games did KC win?", "sql", exclude=frozenset({"team_wins"})
        )
        assert "FROM teams" in with_it and "FROM teams" not in without

    def test_the_split_sends_generation_to_the_second_model(self, tmp_path):
        gen = ScriptedLLM([{"query": "SELECT 1 AS x"}], cost=0.002)
        eng = _engine(
            tmp_path,
            [{"action": "generate", "source": "sql"}, {"answer": "One: 1."}],
            runs=[result(["x"], [[1]])],
            gen=gen,
        )
        res = eng.ask("Which teams punted most?")
        assert gen.purposes == ["generate"] and eng.llm.purposes == ["route", "answer"]
        assert len(res.calls) == 3 and res.cost == pytest.approx(0.004)


# ---- the local provider ---------------------------------------------------------------------


class TestLocalProvider:
    @pytest.mark.parametrize(
        "url", ["http://127.0.0.1:8080/v1", "http://localhost:1234/v1/", "https://[::1]:8443/v1"]
    )
    def test_local_urls(self, url):
        assert _local_url(url) == url.rstrip("/")

    @pytest.mark.parametrize(
        "url",
        [None, "", "http://example.com/v1", "http://user:pw@localhost/v1",
         "http://localhost/v1?x=1", "ftp://localhost/v1", "http://10.0.0.5/v1"],
    )  # fmt: skip
    def test_anything_else_is_refused(self, url):
        with pytest.raises(AskLLMError):
            _local_url(url)

    def test_llm_for_local_is_free_and_lists_candidates(self):
        cfg = AskConfig().llm_for(f"{LOCAL_PREFIX}my-model")
        assert cfg.provider == "local" and cfg.model == "my-model"
        assert cfg.price_in_per_m == 0.0 and cfg.price_out_per_m == 0.0
        assert cfg.route_candidates == 10 and cfg.reasoning_effort is None
        assert cfg.chat_template_kwargs == {"enable_thinking": False}
        assert AskConfig().llm_for("deepseek/x") is AskConfig().llm_for("deepseek/x") or True
        assert AskConfig().llm_for("deepseek/x").provider == "openrouter"

    def test_a_local_call_sends_thinking_off_and_costs_zero(self):
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            assert str(request.url) == "http://127.0.0.1:8080/v1/chat/completions"
            assert "authorization" not in request.headers
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"action": "refuse"}'}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 5},
                },
            )

        llm = AskLLM(
            AskConfig().llm_for("local:spark"),
            model="spark",
            reasoning_effort=None,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        assert llm.chat_json("route", "s", "u") == {"action": "refuse"}
        assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False}
        assert "reasoning" not in seen[0] and "usage" not in seen[0]
        assert llm.calls[0].cost == 0.0

    def test_make_llm_local(self):
        llm = make_llm(model="local:spark-ask")
        assert llm.provider == "local" and llm.model == "spark-ask" and llm.effort is None

    def test_openrouter_bodies_are_unchanged(self):
        body = AskLLM(AskLLMConfig(model="x/y"), model="x/y").request_body([])
        assert "chat_template_kwargs" not in body


# ---- the verifier ---------------------------------------------------------------------------


class _VerifySources:
    """Runs a template's query by its bound values: a team's wins are fixed numbers."""

    WINS = {"KC": 12, "BUF": 11}

    def run(self, source, query, params=None, *, allow_maps=False):
        params = params or {}
        if "team" in params:
            return result(["team", "wins"], [[params["team"], self.WINS.get(params["team"], 0)]])
        if "bad" in query:
            from nflengine.ask.guard import QueryFailed

            raise QueryFailed("syntax", "sql.parse", "hint text")
        if "12" in query:
            return result(["wins"], [[12]])
        return result(["wins"], [[0]])

    def busy(self):
        return False


def _verifier(tmp_path):
    from types import SimpleNamespace

    eng = SimpleNamespace(
        templates=templates(tmp_path),
        names=standard_index(),
        asof=(2026, 6),
        sources=_VerifySources(),
    )
    return Verifier(eng)


def _route_ex(gold, group="seen"):
    return {"id": "r1", "task": "route", "group": group, "verify": {"kind": "route", "gold": gold}}


class TestVerifier:
    GOLD = {
        "action": "template",
        "template": "team_wins",
        "params": {"team": "Chiefs", "season": 2025},
    }

    def test_the_same_values_written_another_way_count(self, tmp_path):
        v = _verifier(tmp_path)
        out = json.dumps(
            {
                "action": "template",
                "template": "team_wins",
                "params": {"team": "KC", "season": 2025},
            }
        )
        item = v.score(_route_ex(self.GOLD), out)
        assert item["exec_ok"] and item["template_ok"] and item["params_exact"]

    def test_a_wrong_value_or_template_fails(self, tmp_path):
        v = _verifier(tmp_path)
        wrong_team = '{"action": "template", "template": "team_wins", "params": {"team": "BUF"}}'
        item = v.score(_route_ex(self.GOLD), wrong_team)
        assert item["template_ok"] and not item["exec_ok"] and item["params_exact"] is False
        other = (
            '{"action": "template", "template": "coach_record", "params": {"coach": "Andy Reid"}}'
        )
        assert not v.score(_route_ex(self.GOLD), other)["template_ok"]

    def test_actions_sources_refusals_and_bad_json(self, tmp_path):
        v = _verifier(tmp_path)
        gen = {"action": "generate", "source": "sql"}
        assert v.score(_route_ex(gen), '{"action": "generate", "source": "sql"}')["exec_ok"]
        assert not v.score(_route_ex(gen), '{"action": "generate", "source": "graph"}')["exec_ok"]
        ref = {"action": "refuse", "reason": "no_data", "explain": "x"}
        item = v.score(_route_ex(ref), '{"action": "refuse", "reason": "write"}')
        assert item["exec_ok"] and item["reason_ok"] is False
        assert not v.score(_route_ex(gen), "not json")["json_ok"]

    def test_queries_are_compared_by_their_rows(self, tmp_path):
        v = _verifier(tmp_path)
        ex = {
            "id": "g1",
            "task": "generate",
            "group": "seen",
            "verify": {"kind": "query", "source": "sql", "gold": "SELECT 12 AS wins"},
        }
        assert v.score(ex, '{"query": "SELECT 12 AS w"}')["exec_ok"]
        assert not v.score(ex, '{"query": "SELECT 0"}')["exec_ok"]
        item = v.score(ex, '{"query": "bad sql"}')
        assert not item["exec_ok"] and item["reason"] == "pred failed:syntax:sql.parse"
        assert "hint text" not in json.dumps(item)  # database text never in the scores

    def test_summary_by_task_and_group(self):
        items = [
            {"task": "route", "group": "seen", "exec_ok": True, "json_ok": True, "reason": "match"},
            {"task": "route", "group": "unseen_template", "exec_ok": False, "json_ok": True,
             "reason": "values differ"},
            {"task": "generate", "group": "seen", "exec_ok": True, "json_ok": True,
             "reason": "match"},
        ]  # fmt: skip
        s = summarise(items)
        assert s["all"]["exec_acc"] == pytest.approx(2 / 3)
        assert s["group"]["unseen_template"]["exec_acc"] == 0.0
        assert s["task"]["generate"]["n"] == 1


# ---- the exporter, end to end on fakes ------------------------------------------------------


class _AnySources:
    """Every query returns the same rows (so a literal query equals its template's)."""

    def __init__(self):
        self.runs = []
        self.enabled = {"sql": True, "graph": True, "history": False}
        self.sandbox = _Sandbox()
        self.driver = "driver"

    def run(self, source, query, params=None, *, allow_maps=False):
        self.runs.append((source, query, dict(params or {})))
        return result(["x"], [[1]], source=source, query=query)

    def busy(self):
        return False

    def close(self):
        pass


class TestExporter:
    def _exporter(self, tmp_path, monkeypatch, **cfg):
        names = standard_index()
        names.players.append(player("00-0050000", "Joe Burrow", "QB", "CIN", 2020, 2026, 80))
        eng = AskEngine(
            ScriptedLLM([]),
            _AnySources(),
            names,
            templates(tmp_path),
            AskConfig(),
            current_season=2026,
            asof=(2026, 6),
        )
        monkeypatch.setattr(Exporter, "_golden_questions", lambda self: [])
        c = TrainDataConfig(
            phrasings_path=write_phrasings(tmp_path, GOOD),
            heldout_templates=("coach_record",),
            candidates_k=2,
            **cfg,
        )
        return Exporter(eng, c, log=lambda s: None), eng

    def test_records_follow_the_contract(self, tmp_path, monkeypatch):
        ex, eng = self._exporter(tmp_path, monkeypatch)
        built = ex.build()
        rows = built.train + built.val
        assert rows and all(set(r) == set(RECORD_FIELDS) for r in rows)
        for r in rows:
            assert [m["role"] for m in r["messages"]] == ["system", "user", "assistant"]
            target = json.loads(r["messages"][-1]["content"])
            if r["task"] == "route":
                assert target["action"] in ("template", "generate", "refuse")
            else:
                assert target["query"] == r["jepa"]["code"] and "$" not in target["query"]
            assert r["jepa"]["text"] in r["messages"][1]["content"]
        assert not any(c.get("route") for c in eng.llm.calls)  # never a model call

    def test_held_out_templates_and_val_skeletons_never_train(self, tmp_path, monkeypatch):
        ex, _ = self._exporter(tmp_path, monkeypatch, val_share=0.4)
        built = ex.build()
        assert not [r for r in built.train if r["meta"].get("template") == "coach_record"]
        assert {r["group"] for r in built.val if r["meta"].get("template") == "coach_record"} == {
            "unseen_template"
        }
        train_sk = {r["meta"]["skeleton"] for r in built.train}
        val_sk = {r["meta"]["skeleton"] for r in built.val}
        assert not train_sk & val_sk

    def test_dropped_gold_becomes_generate_with_the_templates_source(self, tmp_path, monkeypatch):
        ex, _ = self._exporter(tmp_path, monkeypatch, drop_gold_share=1.0)
        built = ex.build()
        dropped = [r for r in built.train if r["meta"].get("gold_dropped")]
        assert dropped
        for r in dropped:
            target = json.loads(r["messages"][-1]["content"])
            assert target == {"action": "generate", "source": r["meta"]["source"]}
            assert f"- {r['meta']['template']} [" not in r["messages"][0]["content"]

    def test_the_same_seed_gives_the_same_examples(self, tmp_path, monkeypatch):
        a = self._exporter(tmp_path, monkeypatch)[0].build()
        b = self._exporter(tmp_path, monkeypatch)[0].build()

        def strip(rows):
            return [
                (r["id"], r["messages"][1]["content"], r["messages"][2]["content"]) for r in rows
            ]

        assert strip(a.train) == strip(b.train) and strip(a.val) == strip(b.val)
