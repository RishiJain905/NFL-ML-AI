"""OpenRouter provider: request shape, lenient parsing, retries, fallback (no network)."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import make_payload
from pydantic import SecretStr

from nflengine.digest.facts import build_fact_index
from nflengine.digest.llm import get_llm
from nflengine.digest.llm import openrouter as orr
from nflengine.digest.llm.placeholder import PlaceholderLLM
from nflengine.digest.prompt import load_prompt
from nflengine.digest.synthesize import synthesize
from nflengine.settings import AppConfig, EnvSettings, LLMConfig

FAKE_KEY = "test-not-a-real-key"
SPEC = {"sections": [{"id": "report_card"}, {"id": "game_outlook"}]}
CFG = AppConfig(
    llm=LLMConfig(
        provider="openrouter",
        model="z-ai/glm-5.3-flash",
        reasoning_effort="max",
        retries=1,
        openrouter={"only": ["baseten/fp8", "relace"], "sort": "price"},
    )
)


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch):
    env = EnvSettings(_env_file=None, openrouter_api_key=SecretStr(FAKE_KEY))
    monkeypatch.setattr(orr, "get_env", lambda: env)
    monkeypatch.setattr(orr, "get_config", lambda: CFG)
    monkeypatch.setattr(orr.time, "sleep", lambda s: None)


def reply(content: str, status: int = 200) -> httpx.Response:
    body = {
        "model": "z-ai/glm-5.3-flash",
        "provider": "Relace",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.0001},
    }
    return httpx.Response(status, json=body)


def client(responses: list[httpx.Response], seen: list[httpx.Request]) -> httpx.Client:
    it = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return next(it)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_request_has_model_reasoning_routing_and_key_only_in_header() -> None:
    seen: list[httpx.Request] = []
    good = json.dumps({"report_card": "a", "game_outlook": "b"})
    llm = orr.OpenRouterLLM(client=client([reply(good)], seen))
    out = llm.generate("SYSTEM", {"x": 1}, SPEC)
    assert out == {"report_card": "a", "game_outlook": "b"}
    body = json.loads(seen[0].content)
    assert body["model"] == "z-ai/glm-5.3-flash"
    assert body["reasoning"] == {"effort": "max", "exclude": True}
    assert body["provider"] == {"only": ["baseten/fp8", "relace"], "sort": "price"}
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert seen[0].headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in seen[0].content.decode()
    call = llm.calls[0]
    assert call["provider"] == "Relace" and call["cost"] == 0.0001
    assert FAKE_KEY not in json.dumps(llm.calls)


def test_regeneration_sends_draft_and_feedback() -> None:
    seen: list[httpx.Request] = []
    good = json.dumps({"report_card": "a", "game_outlook": "b"})
    llm = orr.OpenRouterLLM(client=client([reply(good)], seen))
    spec = {**SPEC, "feedback": ["[x] '88%' (bad)"], "previous": {"report_card": "old"}}
    llm.generate("S", {}, spec)
    msgs = json.loads(seen[0].content)["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert "'88%'" in msgs[-1]["content"] and "feedback" not in msgs[1]["content"]
    # P09 fact-check: a regeneration rewrote sections that hadn't failed and cut words out
    # of a copied fact text while trimming ("... compared with an average team" lost)
    assert "Change only what these issues require" in msgs[-1]["content"]
    assert "never words inside a copied" in msgs[-1]["content"]


@pytest.mark.parametrize(
    "text",
    [
        '```json\n{"report_card": "a", "game_outlook": "b"}\n```',
        'Here you go: {"report_card": "a", "game_outlook": "b"} thanks',
    ],
)
def test_lenient_parsing(text: str) -> None:
    assert orr.parse_sections(text, ["report_card", "game_outlook"])["game_outlook"] == "b"


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2]", '{"other": "x"}', "{bad json}"])
def test_unusable_replies_raise(text: str) -> None:
    with pytest.raises(orr.LLMError):
        orr.parse_sections(text, ["report_card", "game_outlook"])


def test_retries_then_gives_up_without_leaking() -> None:
    seen: list[httpx.Request] = []
    llm = orr.OpenRouterLLM(client=client([httpx.Response(429), httpx.Response(503)], seen))
    with pytest.raises(orr.LLMError) as e:
        llm.generate("S", {}, SPEC)
    assert len(seen) == 2 and "HTTP 503" in str(e.value) and FAKE_KEY not in str(e.value)


def test_client_error_is_not_retried() -> None:
    seen: list[httpx.Request] = []
    bad = httpx.Response(401, json={"error": {"message": "No auth credentials found"}})
    llm = orr.OpenRouterLLM(client=client([bad], seen))
    with pytest.raises(orr.LLMError, match="HTTP 401"):
        llm.generate("S", {}, SPEC)
    assert len(seen) == 1


def test_missing_key_is_reported_by_name(monkeypatch) -> None:
    monkeypatch.setattr(orr, "get_env", lambda: EnvSettings(_env_file=None))
    with pytest.raises(orr.LLMError, match="OPENROUTER_API_KEY is not set"):
        orr.OpenRouterLLM(client=client([], [])).generate("S", {}, SPEC)


def test_registry_builds_openrouter() -> None:
    assert get_llm("openrouter", "z-ai/glm-5.3-flash").name == "openrouter"


class Broken:
    name, model = "openrouter", "x"

    def generate(self, *a, **k):
        raise orr.LLMError("HTTP 503: down")


def test_provider_failure_falls_back_to_placeholder() -> None:
    payload = make_payload()
    budgets = {"report_card": 60, "game_outlook": 80}
    s = synthesize(
        payload, build_fact_index(payload), Broken(), load_prompt(), budgets,
        fallback=PlaceholderLLM(),
    )  # fmt: skip
    assert s.writers == ["placeholder"] and s.final.passed
    assert "openrouter failed" in s.fallback_notes[0] and "HTTP 503" in s.fallback_notes[0]
    with pytest.raises(orr.LLMError):
        synthesize(payload, build_fact_index(payload), Broken(), load_prompt(), budgets)


def test_provider_error_text_never_reaches_messages_or_notes() -> None:
    """Sol #1: an error body that echoes the key must not end up in errors or notes."""
    seen: list[httpx.Request] = []
    echo = httpx.Response(400, json={"error": {"message": f"bad key {FAKE_KEY}", "code": 400}})
    llm = orr.OpenRouterLLM(client=client([echo], seen))
    with pytest.raises(orr.LLMError) as e:
        llm.generate("S", {}, SPEC)
    assert FAKE_KEY not in str(e.value) and "error code 400" in str(e.value)

    class Echo:
        name, model = "openrouter", "x"

        def generate(self, *a, **k):
            raise ValueError(f"oops {FAKE_KEY}")  # not an LLMError: reported by type only

    payload = make_payload()
    s = synthesize(
        payload, build_fact_index(payload), Echo(), load_prompt(), {"report_card": 60},
        fallback=PlaceholderLLM(),
    )  # fmt: skip
    assert FAKE_KEY not in json.dumps(s.checks_json()) and "ValueError" in s.fallback_notes[0]


def test_partial_reply_is_retried_once_then_rejected() -> None:
    """Sol #4: a reply missing sections must not pass with empty prose."""
    seen: list[httpx.Request] = []
    partial = json.dumps({"report_card": "a"})
    good = json.dumps({"report_card": "a", "game_outlook": "b"})
    llm = orr.OpenRouterLLM(client=client([reply(partial), reply(good)], seen))
    assert llm.generate("S", {}, SPEC)["game_outlook"] == "b" and len(seen) == 2
    llm = orr.OpenRouterLLM(client=client([reply(partial), reply(partial)], []))
    with pytest.raises(orr.LLMError, match="missing or has blank sections: game_outlook"):
        llm.generate("S", {}, SPEC)
