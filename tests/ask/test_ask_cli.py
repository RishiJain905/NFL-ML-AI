"""AE00 `nfl ask`: the group's default command, `question`, `eval` and `pick`. The engine, the
model client and `run_eval` are all stubbed, so nothing reaches OpenRouter, Neo4j, W&B, the real
golden set or the data root.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

import nflengine.cli as cli
from nflengine.ask.config import AskCandidate, AskConfig, AskLLMConfig
from nflengine.ask.llm import AskLLMError, OutOfCredits
from nflengine.ask.route import AskEngine, AskResult

runner = CliRunner()


@pytest.fixture(autouse=True)
def wide_console(monkeypatch):
    """Rich wraps at 80 columns under CliRunner; keep tables and long lines on one line."""
    monkeypatch.setattr(cli.console, "_width", 250)


def invoke(*args: str):
    return runner.invoke(cli.app, ["ask", *args])


# ---- stubs ----------------------------------------------------------------------------------


class StubEngine:
    def __init__(self, res: AskResult | Exception):
        self.res = res
        self.asked: list[tuple[str, str]] = []
        self.closed = False

    def ask(self, question: str, source: str = "auto"):
        self.asked.append((question, source))
        if isinstance(self.res, Exception):
            raise self.res
        return self.res

    def close(self):
        self.closed = True


@pytest.fixture
def stub(monkeypatch):
    """Patch the model client, the config's model and the engine's builder."""
    state = SimpleNamespace(
        engine=StubEngine(AskResult("q", status="answered", answer="Dallas won 12 games.")),
        llm_calls=[],
        built=[],
        build_error=None,
        llm_error=None,
        model="cfg/model",
    )

    def fake_make_llm(cfg=None, model=None, **kw):
        state.llm_calls.append(model)
        if state.llm_error:
            raise state.llm_error
        return SimpleNamespace(model=model)

    def fake_build(cls, llm, cfg=None, paths=None):
        state.built.append(llm)
        if state.build_error:
            raise state.build_error
        return state.engine

    monkeypatch.setattr("nflengine.ask.llm.make_llm", fake_make_llm)
    monkeypatch.setattr(AskEngine, "build", classmethod(fake_build))
    monkeypatch.setattr(
        "nflengine.ask.config.ask_config",
        lambda data=None: AskConfig(llm=AskLLMConfig(model=state.model)),
    )
    return state


def result(**over: Any) -> AskResult:
    base: dict[str, Any] = {
        "question": "Who led in 2023?",
        "status": "answered",
        "route": "template",
        "template_id": "season_leaders",
        "source": "sql",
        "query": "SELECT player, yards FROM t",
        "params": {"season": 2023},
        "columns": ["player", "yards"],
        "rows": [["Patrick Mahomes", 5250], ["Josh Allen", 4306]],
        "answer": "Patrick Mahomes led with 5,250 yards.",
        "calls": [{"cost": 0.0004}, {"cost": 0.0002}],
        "timings": {"total": 1.5},
        "context": {"graph_asof": None, "curated_week": 5, "current_season": 2026},
    }
    base.update(over)
    return AskResult(**base)


# ---- the group -------------------------------------------------------------------------------


class TestGroup:
    def test_help_lists_the_commands(self):
        r = invoke("--help")
        assert r.exit_code == 0
        for word in ("question", "eval", "pick", "Ask the Engine"):
            assert word in r.output

    def test_no_arguments_shows_the_help(self):
        r = invoke()
        assert "Usage" in r.output and "eval" in r.output and "pick" in r.output

    def test_command_help_pages(self):
        for cmd, needle in (
            ("eval", "--candidates"),
            ("pick", "--smoke"),
            ("question", "--source"),
        ):
            r = invoke(cmd, "--help")
            assert r.exit_code == 0 and needle in r.output, cmd

    def test_a_bare_question_goes_to_the_default_command(self, stub):
        r = invoke("Who leads in sacks?")
        assert r.exit_code == 0, r.output
        assert stub.engine.asked == [("Who leads in sacks?", "auto")]

    def test_a_question_that_starts_with_a_command_word_is_still_a_question(self, stub):
        for q in ("eval the Bills", "pick the best QB", "question everything"):
            stub.engine.asked.clear()
            r = invoke(q)
            assert r.exit_code == 0, r.output
            assert stub.engine.asked == [(q, "auto")]

    def test_options_before_the_question(self, stub):
        r = invoke("--source", "sql", "--show-query", "Who leads in sacks?")
        assert r.exit_code == 0, r.output
        assert stub.engine.asked == [("Who leads in sacks?", "sql")]

    def test_the_command_name_works_too(self, stub):
        r = invoke("question", "Who leads in sacks?", "--source", "graph")
        assert r.exit_code == 0 and stub.engine.asked == [("Who leads in sacks?", "graph")]

    def test_the_eval_and_pick_words_alone_are_the_subcommands(self, monkeypatch, stub):
        monkeypatch.setattr(
            "nflengine.ask.evaluate.latest_summaries",
            lambda paths=None, smoke=False, current=None: [],
        )
        r = invoke("pick")
        assert r.exit_code == 1 and "no eval runs yet" in r.output
        assert stub.engine.asked == []


# ---- question ----------------------------------------------------------------------------------


class TestQuestion:
    def test_the_answer_is_printed_and_the_engine_closed(self, stub):
        stub.engine.res = result()
        r = invoke("Who led in 2023?")
        assert r.exit_code == 0, r.output
        assert "Patrick Mahomes led with 5,250 yards." in r.output
        assert "Josh Allen" in r.output and "4306" in r.output
        assert "route: template season_leaders" in r.output and "$0.00060" in r.output
        assert "SELECT player" not in r.output  # the query only on request
        assert stub.engine.closed is True
        assert stub.llm_calls == ["cfg/model"] and stub.built == [
            SimpleNamespace(model="cfg/model")
        ]

    def test_the_model_option_overrides_the_config(self, stub):
        invoke("--model", "vendor/other", "Who leads?")
        assert stub.llm_calls == ["vendor/other"]

    def test_no_model_anywhere(self, stub):
        stub.model = None
        r = invoke("Who leads?")
        assert r.exit_code == 1 and "ask.llm.model isn't set yet" in r.output
        assert stub.built == [] and stub.llm_calls == []

    def test_show_query_prints_the_query_and_params(self, stub):
        stub.engine.res = result()
        r = invoke("--show-query", "Who led in 2023?")
        assert "SELECT player, yards FROM t" in r.output and '{"season": 2023}' in r.output

    def test_a_bad_source_is_a_usage_error_before_anything_is_built(self, stub):
        r = invoke("--source", "history", "Who leads?")
        assert r.exit_code == 2 and "--source must be graph, sql or auto" in r.output
        assert stub.built == [] and stub.llm_calls == []

    @pytest.mark.parametrize("source", ["graph", "sql", "auto"])
    def test_the_sources(self, stub, source):
        invoke("--source", source, "Who leads?")
        assert stub.engine.asked == [("Who leads?", source)]

    def test_json_output(self, stub):
        stub.engine.res = result()
        r = invoke("--json", "Who led in 2023?")
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        assert data["status"] == "answered" and data["row_count"] == 2
        assert data["cost"] == pytest.approx(0.0006) and data["rows"][0][0] == "Patrick Mahomes"

    def test_json_output_is_scrubbed(self, stub):
        stub.engine.res = result(
            answer="token=sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789 and password=hunter2"
        )
        r = invoke("--json", "Who led in 2023?")
        assert "hunter2" not in r.output and "sk-or-v1-abcdefghijklmnop" not in r.output

    def test_the_text_output_is_scrubbed_and_not_read_as_markup(self, stub):
        stub.engine.res = result(
            answer="[red]boom[/] password=hunter2",
            rows=[["[bold]x[/bold]", 1]],
        )
        r = invoke("Who led in 2023?")
        assert "[red]boom[/]" in r.output and "hunter2" not in r.output
        assert "[bold]x[/bold]" in r.output

    @pytest.mark.parametrize("status", ["refused", "clarify", "empty", "answered"])
    def test_ordinary_outcomes_exit_zero(self, stub, status):
        stub.engine.res = result(status=status)
        assert invoke("Who led in 2023?").exit_code == 0

    def test_an_error_status_exits_two_and_shows_the_fixed_text(self, stub):
        stub.engine.res = result(
            status="error", answer=None, error="The model didn't give a usable reply."
        )
        r = invoke("Who led in 2023?")
        assert r.exit_code == 2 and "The model didn't give a usable reply." in r.output

    def test_a_refusal_prints_its_message(self, stub):
        stub.engine.res = AskResult(
            "q", status="refused", route="refused", answer="Ask the Engine only reads the data."
        )
        r = invoke("Who led in 2023?")
        assert "only reads the data" in r.output

    def test_a_clarification_lists_the_options(self, stub):
        stub.engine.res = AskResult(
            "q",
            status="clarify",
            answer='More than one player matches "Allen": which one?',
            choices=[
                {
                    "text": "Allen",
                    "kind": "player",
                    "options": [
                        {"value": "00-1", "label": "Josh Allen (QB, BUF)"},
                        {"value": "00-2", "label": "Brian Allen (C, LA)"},
                    ],
                }
            ],
        )
        r = invoke("Allen")
        assert "Josh Allen (QB, BUF)" in r.output and "Brian Allen (C, LA)" in r.output

    def test_out_of_credits_exits_three(self, stub):
        stub.engine.res = OutOfCredits("HTTP 402: out of OpenRouter credits")
        r = invoke("Who led in 2023?")
        assert r.exit_code == 3 and "out of OpenRouter credits" in r.output
        assert stub.engine.closed is True

    def test_a_model_setup_error_exits_one_with_fixed_text(self, stub):
        stub.build_error = AskLLMError("OPENROUTER_API_KEY is not set (check with `nfl doctor`)")
        r = invoke("Who led in 2023?")
        assert r.exit_code == 1 and "OPENROUTER_API_KEY is not set" in r.output
        stub.build_error = None
        stub.llm_error = AskLLMError("ask.llm.model is not set in settings.yaml")
        r = invoke("Who led in 2023?")
        assert r.exit_code == 1 and "ask.llm.model is not set" in r.output

    def test_the_engine_is_closed_when_asking_blows_up(self, stub):
        stub.engine.res = RuntimeError("boom")
        r = invoke("Who led in 2023?")
        assert r.exit_code != 0 and stub.engine.closed is True


# ---- eval ----------------------------------------------------------------------------------------


@pytest.fixture
def evals(monkeypatch):
    """Stub run_eval; `state.calls` has the keyword arguments of each run."""
    state = SimpleNamespace(calls=[], summary=None, stop_after=None, url=None)

    def fake_run_eval(model, **kw):
        state.calls.append({"model": model, **kw})
        summary = (
            state.summary
            if state.summary is not None
            else {
                "questions": 4,
                "exec_accuracy": 0.75,
                "overall_accuracy": 0.8,
                "template_hit_rate": 0.5,
                "latency_p50_s": 1.5,
                "latency_p95_s": 4.25,
                "cost_per_question": 0.00123,
                "meets_bounds": True,
                "stopped": None,
            }
        )
        if state.stop_after is not None and len(state.calls) >= state.stop_after:
            summary = {**summary, "stopped": "out of OpenRouter credits"}
        return {
            "summary": summary,
            "file": Path(f"D:/nope/20261017T010101Z_{len(state.calls)}.json"),
            "url": state.url,
            "scored": [],
        }

    monkeypatch.setattr("nflengine.ask.evaluate.run_eval", fake_run_eval)
    return state


def candidates_cfg(monkeypatch, model="cfg/model"):
    cfg = AskConfig(
        llm=AskLLMConfig(model=model),
        candidates=[
            AskCandidate(model="a/one", efforts=["none", "low"]),
            AskCandidate(model="b/two", efforts=["low"]),
            AskCandidate(model="c/three", efforts=[None]),
        ],
    )
    monkeypatch.setattr("nflengine.ask.config.ask_config", lambda data=None: cfg)


class TestEval:
    def test_a_single_run(self, evals, monkeypatch):
        candidates_cfg(monkeypatch)
        r = invoke("eval", "--model", "vendor/m", "--no-wandb")
        assert r.exit_code == 0, r.output
        (call,) = evals.calls
        assert call["model"] == "vendor/m" and call["use_wandb"] is False
        assert call["reasoning_effort"] is ...  # the default is not passed down
        assert call["n"] is None and call["ids"] is None and call["smoke"] is False
        assert callable(call["log"]) and call["launched_by"] is None
        assert "ask eval: vendor/m (effort default)" in r.output
        assert "exec accuracy 75.0%" in r.output and "overall 80.0%" in r.output
        assert "templates 50.0%" in r.output and "p50 1.5 s" in r.output and "p95 4.2 s" in r.output
        assert "$0.00123 a question" in r.output and "in bounds: yes" in r.output
        assert "saved 20261017T010101Z_1.json" in r.output and "W&B" not in r.output

    def test_the_config_model_is_the_default(self, evals, monkeypatch):
        candidates_cfg(monkeypatch, model="cfg/model")
        invoke("eval")
        assert evals.calls[0]["model"] == "cfg/model" and evals.calls[0]["use_wandb"] is True

    def test_no_model_anywhere(self, evals, monkeypatch):
        candidates_cfg(monkeypatch, model=None)
        r = invoke("eval")
        assert r.exit_code == 1 and "ask.llm.model isn't set yet" in r.output and evals.calls == []

    def test_options_reach_run_eval(self, evals, monkeypatch):
        candidates_cfg(monkeypatch)
        r = invoke(
            "eval", "--model", "m/x", "--effort", "high", "--n", "3", "--ids", "g001, g014",
            "--smoke", "--launched-by", "agent",
        )  # fmt: skip
        assert r.exit_code == 0, r.output
        call = evals.calls[0]
        assert call["reasoning_effort"] == "high" and call["n"] == 3
        assert call["ids"] == ["g001", "g014"] and call["smoke"] is True
        assert call["launched_by"] == "agent"
        assert "effort high" in r.output

    def test_n_all_means_every_question(self, evals, monkeypatch):
        candidates_cfg(monkeypatch)
        invoke("eval", "--n", "all")
        assert evals.calls[0]["n"] is None

    def test_a_bad_n_is_an_error(self, evals, monkeypatch):
        candidates_cfg(monkeypatch)
        r = invoke("eval", "--n", "many")
        assert r.exit_code != 0 and evals.calls == []

    def test_the_w_and_b_link_is_shown(self, evals, monkeypatch):
        candidates_cfg(monkeypatch)
        evals.url = "https://wandb.example/run/abc"
        r = invoke("eval")
        assert "W&B: https://wandb.example/run/abc" in r.output

    def test_candidates_run_at_each_of_their_efforts_in_order(self, evals, monkeypatch):
        candidates_cfg(monkeypatch)
        r = invoke("eval", "--candidates", "--no-wandb")
        assert r.exit_code == 0, r.output
        assert [(c["model"], c["reasoning_effort"]) for c in evals.calls] == [
            ("a/one", "none"),
            ("a/one", "low"),
            ("b/two", "low"),
            ("c/three", None),
        ]
        assert "ask eval: a/one (effort none)" in r.output
        assert "ask eval: c/three (effort None)" in r.output

    def test_a_candidates_own_reply_cap_and_timeout_reach_run_eval(self, evals, monkeypatch):
        cfg = AskConfig(
            llm=AskLLMConfig(model="cfg/model"),
            candidates=[
                AskCandidate(model="a/one", efforts=["low"]),
                AskCandidate(model="a/one", efforts=["max"], max_tokens=16000, timeout_s=120),
            ],
        )
        monkeypatch.setattr("nflengine.ask.config.ask_config", lambda data=None: cfg)
        r = invoke("eval", "--candidates", "--no-wandb")
        assert r.exit_code == 0, r.output
        assert [
            (c["reasoning_effort"], c["max_tokens"], c["llm_timeout_s"]) for c in evals.calls
        ] == [("low", None, None), ("max", 16000, 120)]
        evals.calls.clear()
        invoke("eval", "--candidates", "--max-tokens", "9000", "--llm-timeout", "60")
        assert [(c["max_tokens"], c["llm_timeout_s"]) for c in evals.calls] == [
            (9000, 60.0),
            (9000, 60.0),
        ]  # the command line wins

    def test_candidates_with_an_effort_run_only_that_effort(self, evals, monkeypatch):
        candidates_cfg(monkeypatch)
        invoke("eval", "--candidates", "--effort", "low")
        assert [(c["model"], c["reasoning_effort"]) for c in evals.calls] == [
            ("a/one", "low"),
            ("b/two", "low"),
        ]
        evals.calls.clear()
        invoke("eval", "--candidates", "--effort", "xhigh")
        assert evals.calls == []

    def test_a_stopped_run_exits_three_and_runs_no_more(self, evals, monkeypatch):
        candidates_cfg(monkeypatch)
        evals.stop_after = 2
        r = invoke("eval", "--candidates")
        assert r.exit_code == 3
        assert len(evals.calls) == 2  # b/two and c/three never ran

    def test_a_run_with_no_questions_prints_no_metrics(self, evals, monkeypatch):
        candidates_cfg(monkeypatch)
        evals.summary = {"model": "m", "stopped": None}
        r = invoke("eval", "--model", "m/x")
        assert r.exit_code == 0 and "exec accuracy" not in r.output and "saved" in r.output

    def test_not_in_bounds(self, evals, monkeypatch):
        candidates_cfg(monkeypatch)
        evals.summary = {
            "questions": 2, "exec_accuracy": 0.5, "overall_accuracy": 0.5, "template_hit_rate": 0.0,
            "latency_p50_s": 9.0, "latency_p95_s": 12.0, "cost_per_question": 0.01,
            "meets_bounds": False, "stopped": None,
        }  # fmt: skip
        r = invoke("eval", "--model", "m/x")
        assert "in bounds: no" in r.output


# ---- pick ----------------------------------------------------------------------------------------


def summ(model, acc, p95=2.0, cost=0.001, bounds=True, effort="low", file=None, **extra):
    return {
        "model": model,
        "reasoning_effort": effort,
        "exec_accuracy": acc,
        "overall_accuracy": acc,
        "template_hit_rate": 0.5,
        "refusal_accuracy": 1.0,
        "mean_retries": 0.25,
        "latency_p50_s": p95 / 2,
        "latency_p95_s": p95,
        "cost_per_question": cost,
        "meets_bounds": bounds,
        "file": file or f"{model.replace('/', '-')}.json",
        **extra,
    }


@pytest.fixture
def summaries(monkeypatch):
    state = SimpleNamespace(rows=[], smoke_args=[], current_args=[], rescored=[])
    hashes = {"prompt_hash": "p" * 12, "golden_hash": "g" * 12, "templates_hash": "t" * 12}

    def fake_latest(paths=None, smoke=False, current=None):
        state.smoke_args.append(smoke)
        state.current_args.append(current)
        return state.rows

    def fake_rescore_all(paths=None, log=print):
        state.rescored.append("called")
        log("20261010T010101Z_a_low.json: exec accuracy 0.9 -> 0.7")
        return ["20261010T010101Z_a_low.json"]

    monkeypatch.setattr("nflengine.ask.evaluate.latest_summaries", fake_latest)
    monkeypatch.setattr("nflengine.ask.evaluate.current_hashes", lambda: hashes)
    monkeypatch.setattr("nflengine.ask.evaluate.rescore_all", fake_rescore_all)
    state.hashes = hashes
    return state


class TestPick:
    def test_the_table_and_the_pick(self, summaries):
        summaries.rows = [
            summ("a/dear", 0.80, cost=0.002),
            summ("b/cheap", 0.785, p95=12.0, cost=0.0004, bounds=False),
            summ("c/low", 0.70, cost=0.0001),
        ]
        r = invoke("pick")
        assert r.exit_code == 0, r.output
        assert "a/dear" in r.output and "b/cheap" in r.output and "c/low" in r.output
        assert "pick: b/cheap (effort low)" in r.output  # a tie within 2 points: the cheaper
        assert "80.0%" in r.output and "78.5%" in r.output
        assert "picked" in r.output  # the marker in the table's last column
        assert summaries.smoke_args == [False]

    def test_the_table_is_sorted_by_accuracy(self, summaries):
        summaries.rows = [summ("lo/m", 0.1), summ("hi/m", 0.9), summ("mid/m", 0.5)]
        out = invoke("pick").output
        assert out.index("hi/m") < out.index("mid/m") < out.index("lo/m")

    def test_out_of_the_old_bounds_still_picks(self, summaries):
        summaries.rows = [summ("a/m", 0.9, p95=20.0, bounds=False), summ("b/m", 0.5)]
        r = invoke("pick")
        assert r.exit_code == 0 and "pick: a/m (effort low)" in r.output

    def test_no_scored_run_exits_two(self, summaries):
        summaries.rows = [summ("a/m", None), summ("b/m", None)]
        r = invoke("pick")
        assert r.exit_code == 2
        assert "no run has an execution accuracy to compare" in r.output
        assert "Rishi decides" in r.output and "pick:" not in r.output

    def test_no_runs_exits_one(self, summaries):
        r = invoke("pick")
        assert r.exit_code == 1 and "no eval runs yet" in r.output

    def test_the_smoke_option(self, summaries):
        summaries.rows = [summ("a/m", 0.5)]
        invoke("pick", "--smoke")
        assert summaries.smoke_args == [True]

    def test_the_same_model_at_two_efforts_marks_only_the_picked_row(self, summaries):
        summaries.rows = [
            summ("m/x", 0.70, effort="none", file="none.json"),
            summ("m/x", 0.90, effort="high", file="high.json", latency_p95_s=3.0),
        ]
        r = invoke("pick")
        assert "pick: m/x (effort high)" in r.output
        assert r.output.count("picked") == 1

    def test_markup_in_a_model_name_is_printed_as_text(self, summaries):
        summaries.rows = [summ("[red]evil[/red]/m", 0.9)]
        r = invoke("pick")
        assert "[red]evil[/red]/m" in r.output


class TestPickSelection:
    def test_pick_compares_only_runs_made_with_todays_prompts_golden_set_and_templates(
        self, summaries
    ):
        summaries.rows = [summ("a/m", 0.5)]
        invoke("pick")
        assert summaries.current_args == [summaries.hashes]

    def test_rescore_rewrites_old_runs_before_the_table(self, summaries):
        summaries.rows = [summ("a/m", 0.5)]
        r = invoke("pick", "--rescore")
        assert r.exit_code == 0, r.output
        assert summaries.rescored == ["called"]
        assert "20261010T010101Z_a_low.json: exec accuracy 0.9 -> 0.7" in r.output
        assert r.output.index("exec accuracy 0.9 -> 0.7") < r.output.index("a/m")

    def test_no_rescore_by_default(self, summaries):
        summaries.rows = [summ("a/m", 0.5)]
        invoke("pick")
        assert summaries.rescored == []

    def test_rescore_with_nothing_to_pick_from_still_exits_one(self, summaries):
        r = invoke("pick", "--rescore")
        assert r.exit_code == 1 and summaries.rescored == ["called"]

    def test_rescore_log_lines_are_not_read_as_markup(self, summaries, monkeypatch):
        summaries.rows = [summ("a/m", 0.5)]

        def noisy(paths=None, log=print):
            log("[red]x[/red].json: exec accuracy None -> 0.5 (not rewritten: tables missing)")
            return []

        monkeypatch.setattr("nflengine.ask.evaluate.rescore_all", noisy)
        r = invoke("pick", "--rescore")
        assert "[red]x[/red].json" in r.output and "tables missing" in r.output


class TestQuestionJson:
    def test_numbers_stay_numbers_and_only_strings_are_scrubbed(self, stub):
        from nflengine.ask.llm import LLMCall

        call = LLMCall(
            purpose="route",
            model="vendor/m",
            provider="Host",
            latency_s=0.4,
            prompt_tokens=1234,
            completion_tokens=56,
            reasoning_tokens=7,
            cost=0.00042,
            finish_reason="stop",
            ok=True,
        )
        stub.engine.res = result(
            answer="token=sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789 and password=hunter2",
            calls=[call.to_dict()],
        )
        r = invoke("--json", "Who led in 2023?")
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)  # still valid JSON
        c = data["calls"][0]
        assert c["prompt_tokens"] == 1234 and c["completion_tokens"] == 56
        assert c["cost"] == 0.00042 and c["ok"] is True and c["latency_s"] == 0.4
        assert "hunter2" not in data["answer"] and "sk-or-v1-abcdefghijklmnop" not in data["answer"]
        assert data["rows"] == [["Patrick Mahomes", 5250], ["Josh Allen", 4306]]

    def test_nested_strings_and_keys(self, stub):
        stub.engine.res = result(
            attempts=[{"kind": "template", "query": "SELECT 'password=hunter2'", "rows": 3}],
            values={"note": "api_key: sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789"},
        )
        data = json.loads(invoke("--json", "Who led in 2023?").output)
        assert data["attempts"][0]["rows"] == 3 and "hunter2" not in data["attempts"][0]["query"]
        assert "sk-or-v1-abcdefghijklmnop" not in data["values"]["note"]

    def test_scrub_tree_keeps_everything_that_is_not_a_string(self):
        data = {"a": 1, "b": [1.5, None, True, "token=abc123456789"], "c": {"d": ("x", 2)}}
        out = cli._scrub_tree(data, lambda s, n=400: s.replace("abc123456789", "***"))
        assert out == {"a": 1, "b": [1.5, None, True, "token=***"], "c": {"d": ["x", 2]}}
