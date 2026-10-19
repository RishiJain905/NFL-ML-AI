"""AE02: the control room's Ask the Engine with the history graph (`source: history`).

`GET /api/ask/examples` gains `sources.history` (on, status from the cached Bolt check, what the
build covers) and a "The history graph" group; `POST /api/ask` accepts `source: history`; an
answer from it carries `source.kind = "history"` and the build's label. The rig is AE01's
(`ask_app_fixtures.make_rig`: the real guard, a fake weekly driver, a scripted LLM) with a fake
history driver, a tmp `neo4j-history/build.json` and an injected `HistoryCheck`.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from ask_app_fixtures import MODEL, PLANTED, assert_json_hygiene, make_rig, template, wins
from ask_fakes import FakeDriver

from nflengine.app.ask_service import FEATURED, LANGUAGES, HistoryCheck, _source_info
from nflengine.ask.config import AskConfig, AskLLMConfig
from nflengine.ask.route import FIXED, history_asof
from nflengine.ask.templates import load_templates
from nflengine.history_graph.state import lock_path as history_lock_path
from nflengine.ops.lock import run_lock

TYPES_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "api" / "types.ts"
ON = {"graph": True, "sql": True, "history": True}
LABEL = "history graph 2018-2025 + 2026 week 5"
HISTORY_TEMPLATE = {
    "id": "history_third_down_catches",
    "source": "history",
    "title": "3rd-down catches from a QB",
    "description": "Receivers who caught 3rd-down passes from a QB.",
    "examples": ["Who has caught the most 3rd-down passes from Patrick Mahomes?"],
    "params": {"player": {"type": "player"}},
    "query": "MATCH (qb:Player {player_id: $player})-[:PASSED]->(p:Play)<-[:CAUGHT]-(r:Player) "
    "WHERE p.down = 3 RETURN r.name AS receiver, count(p) AS catches ORDER BY catches DESC",
}
QUESTION = "Who has caught the most 3rd-down passes from Patrick Mahomes?"


def write_history_build(paths: Any, append: bool = True) -> None:
    d = paths.neo4j_history
    d.mkdir(parents=True, exist_ok=True)
    build = {"status": "ok", "build_id": "b1", "first_season": 2018, "through_season": 2025,
             "built_at": "2026-10-18T10:00:00Z"}  # fmt: skip
    (d / "build.json").write_text(json.dumps(build), encoding="utf-8")
    if append:
        app = {"status": "ok", "build_id": "b1", "season": 2026, "through_week": 5,
               "appended_at": "2026-10-18T11:00:00Z"}  # fmt: skip
        (d / "append.json").write_text(json.dumps(app), encoding="utf-8")


class Pings:
    def __init__(self, answer: bool = True) -> None:
        self.answer = answer
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.answer


def history_rig(
    tmp_path,
    monkeypatch,
    replies: Any = (),
    *,
    up: bool = True,
    built: bool = True,
    on: bool = True,
    hdriver: FakeDriver | None = None,
):
    enabled = dict(ON) if on else {"graph": True, "sql": True, "history": False}
    cfg = AskConfig(llm=AskLLMConfig(model=MODEL, reasoning_effort="low"), sources=enabled)
    rig = make_rig(tmp_path, monkeypatch, replies, config=cfg, enabled=dict(enabled))
    if built:
        write_history_build(rig.paths)
    hist = hdriver or FakeDriver(keys=["receiver", "catches"], records=[["Travis Kelce", 120]])
    if on:
        rig.sources.history_driver = hist
        rig.sources.history_lock = history_lock_path(rig.paths)
    rig.engine.history = history_asof(rig.paths, cfg)
    path = tmp_path / "history_templates.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "templates": [HISTORY_TEMPLATE]}), "utf-8")
    rig.engine.templates.update(load_templates(path, current_season=2026))
    pings = Pings(up)
    rig.service._history = HistoryCheck(ping=pings, background=False)
    return rig, hist, pings


def history_block() -> set[str]:
    """The field names of `AskStatusInfo.sources.history` in types.ts."""
    text = TYPES_TS.read_text(encoding="utf-8")
    start = text.index("export interface AskStatusInfo")
    m = re.search(r"\n\s+history: \{\n(.*?)\n\s+\};", text[start:], re.S)
    assert m, "no history block in AskStatusInfo"
    return set(re.findall(r"^\s+(\w+)\??:", m.group(1), re.M))


# ---- the status and the examples ------------------------------------------------------------


class TestStatus:
    def test_off_by_default(self, tmp_path, monkeypatch):
        rig = make_rig(tmp_path, monkeypatch)
        b = rig.examples().json()
        assert b["sources"]["history"] == {
            "on": False,
            "status": "off",
            "first_season": None,
            "through_season": None,
            "season": None,
            "week": None,
            "label": None,
        }
        assert "history" not in [g["source"] for g in b["groups"]]
        assert all(f["source"] != "history" for f in b["featured"])

    def test_on_and_built_and_answering(self, tmp_path, monkeypatch):
        rig, _, pings = history_rig(tmp_path, monkeypatch)
        b = rig.examples().json()
        assert b["sources"]["history"] == {
            "on": True,
            "status": "up",
            "first_season": 2018,
            "through_season": 2025,
            "season": 2026,
            "week": 5,
            "label": LABEL,
        }
        assert pings.calls == 1
        groups = {g["source"]: g for g in b["groups"]}
        assert groups["history"]["label"] == "The history graph"
        real = [t for t in load_templates(current_season=2026).values() if t.source == "history"]
        assert [e["template"] for e in groups["history"]["examples"]] == [t.id for t in real]
        assert all(
            e["source"] == "history" and not e["this_week"] for e in groups["history"]["examples"]
        )
        assert [f["template"] for f in b["featured"]] == list(FEATURED)
        assert set(b["sources"]["history"]) == history_block()

    def test_a_build_without_an_append_has_no_current_week(self, tmp_path, monkeypatch):
        rig, _, _ = history_rig(tmp_path, monkeypatch)
        (rig.paths.neo4j_history / "append.json").unlink()
        h = rig.examples().json()["sources"]["history"]
        assert h["label"] == "history graph 2018-2025" and h["season"] is None and h["week"] is None

    def test_on_but_never_built(self, tmp_path, monkeypatch):
        rig, _, pings = history_rig(tmp_path, monkeypatch, built=False)
        b = rig.examples().json()
        assert b["sources"]["history"]["status"] == "none"
        assert b["sources"]["history"]["label"] is None
        assert "history" not in [g["source"] for g in b["groups"]]
        assert all(f["source"] != "history" for f in b["featured"])
        assert pings.calls == 0  # no Bolt check for a graph that doesn't exist

    def test_down(self, tmp_path, monkeypatch):
        rig, _, _ = history_rig(tmp_path, monkeypatch, up=False)
        b = rig.examples().json()
        assert b["sources"]["history"]["status"] == "down"
        assert "history" in [g["source"] for g in b["groups"]]  # pickable: it says how to start

    def test_being_rebuilt(self, tmp_path, monkeypatch):
        rig, _, pings = history_rig(tmp_path, monkeypatch)
        with run_lock(history_lock_path(rig.paths), "test history build"):
            assert rig.examples().json()["sources"]["history"]["status"] == "busy"
        assert pings.calls == 0
        assert rig.examples().json()["sources"]["history"]["status"] == "up"

    def test_the_weekly_lock_doesnt_hide_the_history_graph(self, tmp_path, monkeypatch):
        from nflengine.ops.lock import lock_path

        rig, _, _ = history_rig(tmp_path, monkeypatch)
        with run_lock(lock_path(rig.paths.runs), "test weekly run"):
            b = rig.examples().json()
        assert b["busy"] is True and b["sources"]["history"]["label"] == LABEL


class TestHistoryCheck:
    def test_it_is_cached_for_30_seconds(self):
        t = [100.0]
        pings = Pings(True)
        check = HistoryCheck(ping=pings, background=False, clock=lambda: t[0])
        assert check.state() == "up" and pings.calls == 1
        t[0] += 29.0
        assert check.state() == "up" and pings.calls == 1
        pings.answer = False
        t[0] += 1.0
        assert check.state() == "down" and pings.calls == 2

    def test_a_request_never_waits_on_a_slow_check(self):
        gate = threading.Event()
        started = threading.Event()

        def slow() -> bool:
            started.set()
            gate.wait(10)
            return True

        check = HistoryCheck(ping=slow, background=True)
        t0 = time.monotonic()
        assert check.state() == "checking"
        assert started.wait(5)
        assert check.state() == "checking"  # still running: one check at a time, no wait
        assert time.monotonic() - t0 < 1.0
        gate.set()
        for _ in range(100):
            if check.state() == "up":
                break
            time.sleep(0.02)
        assert check.state() == "up"

    def test_a_ping_that_raises_is_down(self):
        def boom() -> bool:
            raise RuntimeError("NEO4J_PASSWORD is not set")

        assert HistoryCheck(ping=boom, background=False).state() == "down"


# ---- questions ----------------------------------------------------------------------------------


class TestAsk:
    def test_a_history_question_answers_with_the_build_s_label(self, tmp_path, monkeypatch):
        replies = [template("history_third_down_catches", player="Patrick Mahomes"),
                   {"answer": "Travis Kelce has caught 120 of them."}]  # fmt: skip
        rig, hist, _ = history_rig(tmp_path, monkeypatch, replies)
        r = rig.ask(QUESTION, "history")
        assert r.status_code == 200
        b = r.json()
        assert b["status"] == "answered" and b["rows"] == [["Travis Kelce", 120]]
        assert b["source"] == {
            "kind": "history",
            "season": 2026,
            "week": 5,
            "through_season": 2025,
            "label": LABEL,
        }
        assert b["language"] == "cypher" and LANGUAGES["history"] == "cypher"
        assert b["template"]["id"] == "history_third_down_catches"
        assert rig.queries[0][0] == "history"
        assert hist.tx_runs and rig.driver.tx_runs == []  # the weekly graph wasn't read
        assert "Only this source may be used: history." in rig.llm.requests[0]["system"]
        assert_json_hygiene(b)

    def test_auto_offers_the_history_graph_when_it_is_up(self, tmp_path, monkeypatch):
        rig, _, _ = history_rig(tmp_path, monkeypatch, wins())
        rig.ask("How many wins do the Cowboys have?")
        system = rig.llm.requests[0]["system"]
        assert f"- history: the history graph (Neo4j), {LABEL}" in system
        assert "history_third_down_catches [history]" in system

    def test_auto_leaves_it_out_while_it_is_down(self, tmp_path, monkeypatch):
        rig, _, _ = history_rig(tmp_path, monkeypatch, wins(), up=False)
        b = rig.ask("How many wins do the Cowboys have?").json()
        assert b["status"] == "answered"
        assert "history" not in rig.llm.requests[0]["system"].replace("history_", "x")

    @pytest.mark.parametrize(
        ("kw", "rule", "fixed", "by"),
        [
            ({"up": False}, "source.history_down", "history_down", "source"),
            ({"built": False}, "source.history_none", "history_none", "code"),
            ({"on": False}, "source.off", "source_off", "code"),
        ],
    )
    def test_asking_it_alone_when_it_cannot_answer(
        self, tmp_path, monkeypatch, kw, rule, fixed, by
    ):
        rig, hist, _ = history_rig(tmp_path, monkeypatch, **kw)
        b = rig.ask(QUESTION, "history").json()
        assert b["status"] == "refused"
        assert b["refusal"] == {"by": by, "rule": rule, "message": FIXED[fixed]}
        assert rig.llm.requests == [] and rig.queries == [] and hist.sessions == []
        assert b["cost"] == 0.0

    def test_a_build_or_append_running_stops_it(self, tmp_path, monkeypatch):
        rig, hist, _ = history_rig(tmp_path, monkeypatch)
        with run_lock(history_lock_path(rig.paths), "test history append"):
            b = rig.ask(QUESTION, "history").json()
        assert b["refusal"] == {
            "by": "source",
            "rule": "source.history_busy",
            "message": FIXED["history_busy"],
        }
        assert rig.llm.requests == [] and hist.sessions == []

    def test_the_server_going_away_mid_question_is_history_down(self, tmp_path, monkeypatch):
        from ask_fakes import ServiceUnavailable

        hdriver = FakeDriver(explain_error=ServiceUnavailable("gone"))
        rig, _, _ = history_rig(
            tmp_path,
            monkeypatch,
            [template("history_third_down_catches", player="Patrick Mahomes")],
            hdriver=hdriver,
        )
        b = rig.ask(QUESTION, "history").json()
        assert b["refusal"]["rule"] == "source.history_down"
        assert b["answer"] == FIXED["history_down"]
        assert [r["purpose"] for r in rig.llm.requests] == ["route"]

    def test_text_from_the_history_graph_is_cleaned(self, tmp_path, monkeypatch):
        planted = f"Pass short right to T.Kelce {PLANTED['kv']} {PLANTED['openrouter']}"
        hdriver = FakeDriver(keys=["description", "catches"], records=[[planted, 1]])
        replies = [template("history_third_down_catches", player="Patrick Mahomes"),
                   {"answer": "One catch."}]  # fmt: skip
        rig, _, _ = history_rig(tmp_path, monkeypatch, replies, hdriver=hdriver)
        r = rig.ask(QUESTION, "history")
        assert "hunter2-planted" not in r.text and PLANTED["openrouter"] not in r.text
        assert_json_hygiene(r.json())

    @pytest.mark.parametrize("source", ["History", "HISTORY", "history ", "histories", "hist"])
    def test_the_request_pattern(self, tmp_path, monkeypatch, source):
        rig, _, _ = history_rig(tmp_path, monkeypatch)
        r = rig.post({"question": QUESTION, "source": source})
        assert r.status_code == 422 and r.json()["error"]["code"] == "bad_request"
        assert "history" in r.json()["error"]["message"]


def test_source_info_for_each_kind():
    class E:
        history = {
            "season": None,
            "week": None,
            "through_season": 2025,
            "label": "history graph 2018-2025",
        }
        asof = (2026, 6)
        curated = (None, 5)
        current = 2026

    class R:
        source = "history"

    assert _source_info(R, E) == {
        "kind": "history",
        "season": None,
        "week": None,
        "through_season": 2025,
        "label": "history graph 2018-2025",
    }
    R.source = "graph"
    assert _source_info(R, E)["kind"] == "graph"
    E.history = None
    R.source = "history"
    assert _source_info(R, E)["label"] == "history graph"
