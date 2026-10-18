"""AE00 Ask LLM provider (`ask/llm.py`) over `httpx.MockTransport`: the request body per provider,
retries and the give-up text, 402 -> `OutOfCredits`, provider error text never repeated, the key
never in a call record / exception / `to_dict()`, lenient JSON parsing, cost per provider, and
the `LLM_BASE_URL` rules. No network: every client is a mock transport; the key is a sentinel
`SecretStr` handed in through a fake `get_env`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

import nflengine.ask.llm as llm_mod
from nflengine.ask.config import AskCandidate, AskConfig, AskLLMConfig
from nflengine.ask.llm import (
    LOCAL_HOSTS,
    OPENROUTER_URL,
    RETRY_STATUS,
    AskLLM,
    AskLLMError,
    LLMCall,
    OutOfCredits,
    make_llm,
    parse_object,
)

KEY = "sk-or-v1-" + "A1b2C3d4E5f6" * 4  # key-shaped, so scrub() would catch it too
LOCAL_KEY = "local-" + "Zz9Yy8Xx7Ww6" * 3
MODEL = "vendor/small-model"
OK_BODY = {
    "model": "vendor/small-model-0613",
    "provider": "SomeHost",
    "choices": [{"message": {"content": '{"action": "refuse"}'}, "finish_reason": "stop"}],
    "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "completion_tokens_details": {"reasoning_tokens": 5},
        "cost": 0.00042,
    },
}


class FakeEnv:
    """What `get_env()` returns: the same `is_set` contract as EnvSettings."""

    def __init__(self, openrouter=KEY, llm_key=None, base_url=None):
        self.openrouter_api_key = SecretStr(openrouter) if openrouter is not None else None
        self.llm_api_key = SecretStr(llm_key) if llm_key is not None else None
        self.llm_base_url = base_url

    def is_set(self, name: str) -> bool:
        value = getattr(self, name.lower(), None)
        if value is None:
            return False
        if isinstance(value, SecretStr):
            return bool(value.get_secret_value().strip())
        return bool(str(value).strip())


@pytest.fixture
def env(monkeypatch):
    holder = SimpleNamespace(fake=FakeEnv())
    monkeypatch.setattr("nflengine.settings.get_env", lambda: holder.fake)
    return holder


@pytest.fixture
def sleeps(monkeypatch):
    out: list[float] = []
    monkeypatch.setattr(llm_mod.time, "sleep", out.append)
    return out


class Wire:
    """A mock transport: scripted responses (or exceptions) and every request kept."""

    def __init__(self, *replies: Any):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError("an unscripted request")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


def ok(body: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json=body or OK_BODY)


def make(wire: Wire, **cfg_kw: Any) -> AskLLM:
    cfg = AskLLMConfig(model=MODEL, **cfg_kw)
    return AskLLM(cfg, client=wire.client())


def text_reply(text: str) -> httpx.Response:
    return ok({"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})


# ---- the request ----------------------------------------------------------------------------


class TestRequest:
    def test_openrouter_request(self, env):
        wire = Wire(ok())
        obj = make(wire).chat_json("route", "SYS", "USER")
        assert obj == {"action": "refuse"}
        (req,) = wire.requests
        assert req.method == "POST" and str(req.url) == f"{OPENROUTER_URL}/chat/completions"
        assert req.headers["authorization"] == f"Bearer {KEY}"
        assert req.headers["x-title"] == "NFL Analytics Engine ask"
        assert req.headers["content-type"] == "application/json"
        (body,) = wire.bodies
        assert body == {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": "SYS"},
                {"role": "user", "content": "USER"},
            ],
            "max_tokens": 2000,
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "usage": {"include": True},
            "reasoning": {"effort": "low", "exclude": True},
        }

    def test_the_key_is_only_in_the_authorization_header(self, env):
        wire = Wire(ok())
        make(wire).chat_json("route", "SYS", "USER")
        (req,) = wire.requests
        assert KEY not in req.content.decode()
        assert KEY not in str(req.url)
        assert [k for k, v in req.headers.items() if KEY in v] == ["authorization"]

    def test_extra_messages_follow_system_and_user(self, env):
        wire = Wire(ok())
        extra = [{"role": "assistant", "content": "A"}, {"role": "user", "content": "fix it"}]
        make(wire).chat_json("fix", "SYS", "USER", extra)
        roles = [m["role"] for m in wire.bodies[0]["messages"]]
        assert roles == ["system", "user", "assistant", "user"]
        assert extra == [
            {"role": "assistant", "content": "A"},
            {"role": "user", "content": "fix it"},
        ]

    def test_max_tokens_and_temperature_come_from_the_config(self, env):
        wire = Wire(ok(), ok())
        make(wire, max_tokens=123, temperature=0.7).chat_json("p", "s", "u")
        assert wire.bodies[0]["max_tokens"] == 123 and wire.bodies[0]["temperature"] == 0.7
        wire = Wire(ok())
        make(wire, temperature=None).chat_json("p", "s", "u")
        assert "temperature" not in wire.bodies[0]

    def test_reasoning_effort_and_its_exclusion(self, env):
        for effort, expect in (("low", "low"), ("none", "none"), ("high", "high")):
            wire = Wire(ok())
            make(wire, reasoning_effort=effort).chat_json("p", "s", "u")
            assert wire.bodies[0]["reasoning"] == {"effort": expect, "exclude": True}
        for effort in (None, ""):
            wire = Wire(ok())
            make(wire, reasoning_effort=effort).chat_json("p", "s", "u")
            assert "reasoning" not in wire.bodies[0]

    def test_a_per_client_effort_overrides_the_configs(self, env):
        cfg = AskLLMConfig(model=MODEL, reasoning_effort="low")
        wire = Wire(ok(), ok(), ok())
        AskLLM(cfg, reasoning_effort="high", client=wire.client()).chat_json("p", "s", "u")
        assert wire.bodies[0]["reasoning"]["effort"] == "high"
        wire = Wire(ok())
        AskLLM(cfg, reasoning_effort=None, client=wire.client()).chat_json("p", "s", "u")
        assert "reasoning" not in wire.bodies[0]
        llm = AskLLM(cfg, client=Wire().client())
        assert llm.effort == "low"

    def test_routing_comes_from_the_config_or_the_override(self, env):
        wire = Wire(ok())
        make(wire, openrouter={"sort": "latency", "only": ["x"]}).chat_json("p", "s", "u")
        assert wire.bodies[0]["provider"] == {"sort": "latency", "only": ["x"]}
        cfg = AskLLMConfig(model=MODEL, openrouter={"sort": "price"})
        wire = Wire(ok(), ok())
        AskLLM(cfg, routing={"only": ["y"]}, client=wire.client()).chat_json("p", "s", "u")
        assert wire.bodies[0]["provider"] == {"only": ["y"]}
        AskLLM(cfg, routing={}, client=wire.client()).chat_json("p", "s", "u")
        assert "provider" not in wire.bodies[1]  # an explicit empty routing wins

    def test_the_routing_dict_is_copied(self, env):
        routing = {"sort": "price"}
        llm = AskLLM(AskLLMConfig(model=MODEL), routing=routing, client=Wire().client())
        llm.routing["sort"] = "other"
        assert routing == {"sort": "price"}

    def test_the_model_is_required_and_can_be_overridden(self, env):
        with pytest.raises(AskLLMError, match="ask.llm.model is not set"):
            AskLLM(AskLLMConfig())
        wire = Wire(ok())
        AskLLM(AskLLMConfig(model="a/b"), model="c/d", client=wire.client()).chat_json(
            "p", "s", "u"
        )
        assert wire.bodies[0]["model"] == "c/d"

    def test_a_missing_key_fails_before_any_request(self, env):
        env.fake = FakeEnv(openrouter=None)
        wire = Wire()
        with pytest.raises(AskLLMError, match="OPENROUTER_API_KEY is not set"):
            make(wire).chat_json("p", "s", "u")
        assert wire.requests == []
        env.fake = FakeEnv(openrouter="   ")
        with pytest.raises(AskLLMError, match="OPENROUTER_API_KEY is not set"):
            make(wire).chat_json("p", "s", "u")

    def test_an_unknown_provider(self, env):
        cfg = AskLLMConfig(model=MODEL).model_copy(update={"provider": "carrier_pigeon"})
        wire = Wire()
        with pytest.raises(AskLLMError, match="unknown ask.llm.provider"):
            AskLLM(cfg, client=wire.client()).chat_json("p", "s", "u")
        assert wire.requests == []

    def test_it_closes_a_client_it_made_and_leaves_a_given_one_open(self, env, monkeypatch):
        made = []

        class Spy(httpx.Client):
            def __init__(self, **kw):
                super().__init__(transport=httpx.MockTransport(Wire(ok())))
                made.append(self)

        monkeypatch.setattr(llm_mod.httpx, "Client", Spy)
        AskLLM(AskLLMConfig(model=MODEL)).chat_json("p", "s", "u")
        assert len(made) == 1 and made[0].is_closed
        client = Wire(ok()).client()
        AskLLM(AskLLMConfig(model=MODEL), client=client).chat_json("p", "s", "u")
        assert not client.is_closed


class TestOpenAICompatible:
    def cfg(self, **kw):
        return AskLLMConfig(model="local-7b", provider="openai_compatible", **kw)

    def test_body_and_url(self, env):
        env.fake = FakeEnv(
            openrouter=None, llm_key=LOCAL_KEY, base_url="https://llm.example.com/v1/"
        )
        wire = Wire(ok())
        AskLLM(self.cfg(), client=wire.client()).chat_json("p", "SYS", "USER")
        (req,) = wire.requests
        assert str(req.url) == "https://llm.example.com/v1/chat/completions"
        assert req.headers["authorization"] == f"Bearer {LOCAL_KEY}"
        assert "x-title" not in req.headers
        body = wire.bodies[0]
        assert "usage" not in body and "reasoning" not in body and "provider" not in body
        assert body["model"] == "local-7b" and body["response_format"] == {"type": "json_object"}

    def test_no_authorization_header_without_a_key(self, env):
        env.fake = FakeEnv(openrouter=None, base_url="http://localhost:8080/v1")
        wire = Wire(ok())
        AskLLM(self.cfg(), client=wire.client()).chat_json("p", "s", "u")
        assert "authorization" not in wire.requests[0].headers

    def test_the_openrouter_key_is_not_sent_to_a_local_server(self, env):
        env.fake = FakeEnv(openrouter=KEY, base_url="http://localhost:8080/v1")
        wire = Wire(ok())
        AskLLM(self.cfg(), client=wire.client()).chat_json("p", "s", "u")
        assert KEY not in "".join(f"{k}{v}" for k, v in wire.requests[0].headers.items())

    @pytest.mark.parametrize(
        "base, url",
        [
            ("https://llm.example.com/v1", "https://llm.example.com/v1/chat/completions"),
            ("https://llm.example.com/v1///", "https://llm.example.com/v1/chat/completions"),
            ("  https://llm.example.com/v1  ", "https://llm.example.com/v1/chat/completions"),
            ("http://localhost:8080/v1", "http://localhost:8080/v1/chat/completions"),
            ("http://LOCALHOST:8080", "http://localhost:8080/chat/completions"),
            ("http://127.0.0.1:1234/v1", "http://127.0.0.1:1234/v1/chat/completions"),
            ("http://[::1]:8000/v1", "http://[::1]:8000/v1/chat/completions"),
            ("https://localhost/v1", "https://localhost/v1/chat/completions"),
        ],
    )
    def test_accepted_base_urls(self, env, base, url):
        env.fake = FakeEnv(openrouter=None, base_url=base)
        wire = Wire(ok())
        AskLLM(self.cfg(), client=wire.client()).chat_json("p", "s", "u")
        assert str(wire.requests[0].url) == url

    @pytest.mark.parametrize(
        "base",
        [
            "http://example.com/v1",
            "http://localhost.evil.com/v1",
            "http://127.0.0.1.evil.com/v1",
            "http://0.0.0.0:8000/v1",
            "http://192.168.1.5:8000/v1",
            "ftp://llm.example.com/v1",
            "file:///etc/passwd",
            "localhost:8080/v1",
            "llm.example.com/v1",
            "ws://localhost/v1",
        ],
    )
    def test_https_is_required_except_on_localhost(self, env, base):
        env.fake = FakeEnv(openrouter=None, base_url=base)
        wire = Wire(ok())
        with pytest.raises(AskLLMError, match="must be https"):
            AskLLM(self.cfg(), client=wire.client()).chat_json("p", "s", "u")
        assert wire.requests == []

    @pytest.mark.parametrize(
        "base",
        [
            "https://user:pass-word-123@llm.example.com/v1",
            "https://user@llm.example.com/v1",
            "https://:onlypass@llm.example.com/v1",
            "https://llm.example.com/v1?api_key=abc123secret",
            "http://user:pass@localhost:8080/v1",
        ],
    )
    def test_credentials_or_a_query_in_the_url_are_refused_and_not_repeated(self, env, base):
        env.fake = FakeEnv(openrouter=None, base_url=base)
        wire = Wire(ok())
        with pytest.raises(
            AskLLMError, match="credentials or a query|query string or a fragment"
        ) as exc:
            AskLLM(self.cfg(), client=wire.client()).chat_json("p", "s", "u")
        for secret in ("pass-word-123", "onlypass", "abc123secret", "user"):
            assert secret not in str(exc.value)
        assert wire.requests == []

    @pytest.mark.parametrize(
        "base", ["https://llm.example.com/v1?", "https://llm.example.com/v1#x"]
    )
    def test_an_empty_query_or_a_fragment_is_refused(self, env, base):
        env.fake = FakeEnv(openrouter=None, base_url=base)
        wire = Wire(ok())
        try:
            AskLLM(self.cfg(), client=wire.client()).chat_json("p", "s", "u")
        except AskLLMError:
            return
        # (a refusal is the fix; an accepted URL must still end in /chat/completions)
        assert str(wire.requests[0].url).endswith("/v1/chat/completions")

    @pytest.mark.parametrize("base", [None, "", "   "])
    def test_an_unset_base_url(self, env, base):
        env.fake = FakeEnv(openrouter=None, base_url=base)
        with pytest.raises(AskLLMError, match="LLM_BASE_URL is not set"):
            AskLLM(self.cfg(), client=Wire().client()).chat_json("p", "s", "u")

    def test_local_hosts(self):
        assert {"localhost", "127.0.0.1", "::1"} == LOCAL_HOSTS


# ---- retries, errors and what they may say ----------------------------------------------------


class TestRetries:
    @pytest.mark.parametrize("status", sorted(RETRY_STATUS))
    def test_a_transient_status_is_retried_once_by_default(self, env, sleeps, status):
        wire = Wire(httpx.Response(status, json={"error": {"code": "x", "message": "m"}}), ok())
        assert make(wire).chat_json("p", "s", "u") == {"action": "refuse"}
        assert len(wire.requests) == 2 and sleeps == [1.0]

    def test_the_retry_set(self):
        assert {408, 409, 425, 429, 500, 502, 503, 504} == RETRY_STATUS

    def test_give_up_text_and_backoff(self, env, sleeps):
        wire = Wire(*[httpx.Response(429, json={})] * 3)
        with pytest.raises(AskLLMError) as exc:
            make(wire, retries=2).chat_json("p", "s", "u")
        assert str(exc.value) == "gave up after 3 attempts (HTTP 429)"
        assert sleeps == [1.0, 2.0] and len(wire.requests) == 3

    def test_backoff_is_capped_at_four_seconds(self, env, sleeps):
        wire = Wire(*[httpx.Response(503)] * 6)
        with pytest.raises(AskLLMError):
            make(wire, retries=5).chat_json("p", "s", "u")
        assert sleeps == [1.0, 2.0, 4.0, 4.0, 4.0]

    def test_no_retries(self, env, sleeps):
        wire = Wire(httpx.Response(500))
        with pytest.raises(AskLLMError, match=r"gave up after 1 attempts \(HTTP 500\)"):
            make(wire, retries=0).chat_json("p", "s", "u")
        assert sleeps == [] and len(wire.requests) == 1

    def test_negative_retries_mean_none(self, env, sleeps):
        wire = Wire(httpx.Response(500))
        with pytest.raises(AskLLMError, match="gave up after 1 attempts"):
            make(wire, retries=-3).chat_json("p", "s", "u")

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
    def test_other_errors_are_not_retried(self, env, sleeps, status):
        body = {"error": {"code": "bad_thing", "message": "the secret message text"}}
        wire = Wire(httpx.Response(status, json=body))
        with pytest.raises(AskLLMError) as exc:
            make(wire, retries=3).chat_json("p", "s", "u")
        assert str(exc.value) == f"HTTP {status}: error code bad_thing"
        assert len(wire.requests) == 1 and sleeps == []

    def test_timeouts_and_transport_errors_are_retried(self, env, sleeps):
        req = httpx.Request("POST", "https://x")
        wire = Wire(
            httpx.ReadTimeout("slow", request=req), httpx.ConnectError("no", request=req), ok()
        )
        assert make(wire, retries=2).chat_json("p", "s", "u")
        assert len(wire.requests) == 3
        wire = Wire(httpx.ReadTimeout("slow", request=req), httpx.ReadTimeout("slow", request=req))
        with pytest.raises(AskLLMError, match=r"gave up after 2 attempts \(timeout\)"):
            make(wire).chat_json("p", "s", "u")
        wire = Wire(httpx.ConnectError("no", request=req), httpx.ConnectError("no", request=req))
        with pytest.raises(AskLLMError, match=r"gave up after 2 attempts \(ConnectError\)"):
            make(wire).chat_json("p", "s", "u")

    def test_the_last_failure_is_the_one_reported(self, env, sleeps):
        req = httpx.Request("POST", "https://x")
        wire = Wire(httpx.Response(429), httpx.ReadTimeout("s", request=req), httpx.Response(503))
        with pytest.raises(AskLLMError, match=r"\(HTTP 503\)"):
            make(wire, retries=2).chat_json("p", "s", "u")

    def test_a_402_is_out_of_credits_and_is_not_retried(self, env, sleeps):
        wire = Wire(httpx.Response(402, json={"error": {"message": "balance 0.00 for key " + KEY}}))
        with pytest.raises(OutOfCredits) as exc:
            make(wire, retries=3).chat_json("p", "s", "u")
        assert isinstance(exc.value, AskLLMError)
        assert str(exc.value) == "HTTP 402: out of OpenRouter credits"
        assert len(wire.requests) == 1 and sleeps == []

    def test_a_200_with_only_an_error_is_retried(self, env, sleeps):
        wire = Wire(ok({"error": {"code": 429, "message": "upstream said no"}}), ok())
        assert make(wire).chat_json("p", "s", "u")
        wire = Wire(ok({"error": {"code": "x"}}), ok({"error": "plain text error"}))
        with pytest.raises(AskLLMError, match=r"\(provider error: (error code x|provider error)\)"):
            make(wire).chat_json("p", "s", "u")

    def test_a_200_with_an_error_and_choices_is_accepted(self, env):
        body = {**OK_BODY, "error": {"code": 1, "message": "warning"}}
        assert make(Wire(ok(body))).chat_json("p", "s", "u") == {"action": "refuse"}


class TestSecrecy:
    MESSAGE = f"you sent key {KEY} and the prompt SYSTEM-SECRET-PROMPT"

    def failing(self, env, sleeps, reply):
        llm = make(Wire(*reply), retries=1)
        with pytest.raises(AskLLMError) as exc:
            llm.chat_json("route", "SYSTEM-SECRET-PROMPT", "USER-SECRET-QUESTION")
        return llm, exc.value

    @pytest.mark.parametrize(
        "replies",
        [
            [httpx.Response(400, json={"error": {"code": "invalid", "message": MESSAGE}})],
            [httpx.Response(401, json={"error": {"message": MESSAGE}})],
            [httpx.Response(429, json={"error": {"message": MESSAGE}})] * 2,
            [ok({"error": {"code": "weird", "message": MESSAGE}})] * 2,
            [httpx.Response(402, json={"error": {"message": MESSAGE}})],
            [httpx.Response(500, text=MESSAGE)] * 2,
            [httpx.Response(400, text=MESSAGE)],
        ],
    )
    def test_a_provider_message_never_appears(self, env, sleeps, replies):
        llm, err = self.failing(env, sleeps, replies)
        everything = " ".join(
            [str(err), repr(err), *map(str, err.args), json.dumps([c.to_dict() for c in llm.calls])]
        )
        for secret in (KEY, "SYSTEM-SECRET-PROMPT", "USER-SECRET-QUESTION", "you sent key"):
            assert secret not in everything

    def test_an_error_code_must_look_like_a_code_to_be_repeated(self, env, sleeps):
        wire = Wire(httpx.Response(400, json={"error": {"code": "has spaces and (parens)"}}))
        with pytest.raises(AskLLMError) as exc:
            make(wire).chat_json("p", "s", "u")
        assert str(exc.value) == "HTTP 400: provider error"
        wire = Wire(httpx.Response(400, json={"error": {"code": "c" * 41}}))
        with pytest.raises(AskLLMError) as exc:
            make(wire).chat_json("p", "s", "u")
        assert str(exc.value) == "HTTP 400: provider error"
        wire = Wire(httpx.Response(400, json={"error": {"code": 429}}))
        with pytest.raises(AskLLMError) as exc:
            make(wire).chat_json("p", "s", "u")
        assert str(exc.value) == "HTTP 400: error code 429"

    def test_the_key_never_reaches_a_call_record_or_an_exception_on_success_or_failure(
        self, env, sleeps
    ):
        llm = make(Wire(ok(), httpx.Response(400, json={"error": {"code": "bad"}}), ok()))
        llm.chat_json("route", "SYS", "USER")
        with pytest.raises(AskLLMError):
            llm.chat_json("generate", "SYS", "USER")
        llm.chat_json("answer", "SYS", "USER")
        dumped = json.dumps([c.to_dict() for c in llm.calls]) + repr(llm.calls) + repr(llm.cost)
        assert KEY not in dumped
        for text in ("SYS", "USER"):  # prompts are not recorded either
            assert text not in dumped
        assert KEY not in repr(vars(llm)).replace("SecretStr", "")

    def test_the_key_is_not_a_field_of_the_client(self, env):
        llm = make(Wire(ok()))
        assert KEY not in repr(vars(llm)) and KEY not in repr(llm.cfg)

    def test_a_local_key_stays_out_of_records_too(self, env):
        env.fake = FakeEnv(openrouter=None, llm_key=LOCAL_KEY, base_url="https://x.example.com/v1")
        cfg = AskLLMConfig(model="m", provider="openai_compatible")
        llm = AskLLM(cfg, client=Wire(ok()).client())
        llm.chat_json("p", "s", "u")
        assert LOCAL_KEY not in json.dumps([c.to_dict() for c in llm.calls])


# ---- lenient JSON ---------------------------------------------------------------------------


class TestParse:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ('{"a": 1}', {"a": 1}),
            ('  \n{"a": 1}\n  ', {"a": 1}),
            ('```json\n{"a": 1}\n```', {"a": 1}),
            ('```\n{"a": 1}\n```', {"a": 1}),
            ('```JSON\n{"a": 1}\n```', {"a": 1}),
            ('Sure! Here it is:\n{"a": 1}\nHope that helps.', {"a": 1}),
            (
                '{"a": {"b": [1, 2, {"c": "}"}]}, "d": "{"}',
                {"a": {"b": [1, 2, {"c": "}"}]}, "d": "{"},
            ),
            ('{"query": "MATCH (n) RETURN n"}', {"query": "MATCH (n) RETURN n"}),
            ("{}", {}),
            ('[{"a": 1}]', {"a": 1}),
            ('prefix {"a": "x\\ny"} suffix', {"a": "x\ny"}),
            ('{"a": null, "b": true}', {"a": None, "b": True}),
        ],
    )
    def test_objects_in_replies(self, text, expected):
        assert parse_object(text) == expected

    @pytest.mark.parametrize("text", ["", "   ", "\n\n", None])
    def test_empty_replies(self, text):
        with pytest.raises(AskLLMError, match="empty reply"):
            parse_object(text)

    @pytest.mark.parametrize(
        "text", ["no json here", "[1, 2, 3]", "}{", "{", "}", '"string"', "42"]
    )
    def test_no_object(self, text):
        with pytest.raises(AskLLMError, match="no JSON object"):
            parse_object(text)

    @pytest.mark.parametrize(
        "text",
        [
            '{"a": 1,}',
            "{'a': 1}",
            '{"a": 1} and then {"b": 2}',
            '{"a": }',
            '{"a" 1}',
        ],
    )
    def test_invalid_json(self, text):
        with pytest.raises(AskLLMError, match="not valid JSON") as exc:
            parse_object(text)
        assert text not in str(exc.value)  # only the parser's reason

    def test_a_reply_through_the_client(self, env):
        llm = make(Wire(text_reply('```json\n{"template": "t", "params": {"team": "KC"}}\n```')))
        assert llm.chat_json("route", "s", "u") == {"template": "t", "params": {"team": "KC"}}
        assert llm.calls[0].ok and llm.calls[0].error is None

    def test_an_unparseable_reply_is_recorded_as_a_failed_call(self, env):
        # asked once more, then given up: both attempts recorded (both cost)
        llm = make(Wire(text_reply("I cannot do that"), text_reply("Still no")))
        with pytest.raises(AskLLMError, match="no JSON object"):
            llm.chat_json("route", "s", "u")
        assert len(llm.calls) == 2
        assert all(not c.ok and c.error == "no JSON object in the reply" for c in llm.calls)

    def test_an_empty_reply_is_asked_for_once_more(self, env):
        # DeepSeek V4.1 Flash 1017 at "max" sometimes spends its whole budget thinking
        wire = Wire(text_reply(""), text_reply('{"action": "template"}'))
        llm = make(wire)
        assert llm.chat_json("route", "s", "u") == {"action": "template"}
        assert len(wire.requests) == 2 and len(llm.calls) == 2
        assert not llm.calls[0].ok and llm.calls[0].error == "empty reply" and llm.calls[1].ok

    def test_out_of_credits_is_never_asked_again(self, env):
        llm = make(Wire(httpx.Response(402, json={"error": {"message": "no credits"}})))
        with pytest.raises(OutOfCredits):
            llm.chat_json("route", "s", "u")
        assert len(llm.calls) == 1

    @pytest.mark.parametrize(
        "reply",
        [
            httpx.Response(200, text="not json at all"),
            httpx.Response(200, json=[1, 2]),
            httpx.Response(200, json={}),
            httpx.Response(200, json={"choices": []}),
            httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
            httpx.Response(200, json={"choices": [{"message": None}]}),
        ],
    )
    def test_odd_bodies_are_an_error_not_a_crash(self, env, reply):
        again = httpx.Response(reply.status_code, content=reply.content, headers=reply.headers)
        llm = make(Wire(reply, again))  # an unusable reply is asked for once more
        with pytest.raises(AskLLMError):
            llm.chat_json("p", "s", "u")
        assert llm.calls[0].ok is False


# ---- call records and cost --------------------------------------------------------------------


class TestCalls:
    def test_everything_is_recorded(self, env):
        llm = make(Wire(ok()))
        llm.chat_json("route", "s", "u")
        (call,) = llm.calls
        assert isinstance(call, LLMCall)
        assert call.purpose == "route" and call.ok and call.error is None
        assert call.model == "vendor/small-model-0613" and call.provider == "SomeHost"
        assert (call.prompt_tokens, call.completion_tokens, call.reasoning_tokens) == (100, 20, 5)
        assert call.cost == 0.00042 and call.finish_reason == "stop"
        assert call.latency_s >= 0.0
        d = call.to_dict()
        assert set(d) == {
            "purpose", "model", "provider", "latency_s", "prompt_tokens", "completion_tokens",
            "reasoning_tokens", "cost", "finish_reason", "ok", "error",
        }  # fmt: skip

    def test_missing_pieces_are_none(self, env):
        llm = make(Wire(text_reply('{"a": 1}')))
        llm.chat_json("p", "s", "u")
        call = llm.calls[0]
        assert call.model == MODEL and call.provider is None
        assert call.prompt_tokens is None and call.completion_tokens is None
        assert call.reasoning_tokens is None and call.cost is None

    def test_a_non_string_provider_is_ignored(self, env):
        body = {**OK_BODY, "provider": {"name": "x"}}
        llm = make(Wire(ok(body)))
        llm.chat_json("p", "s", "u")
        assert llm.calls[0].provider is None

    @pytest.mark.parametrize(
        "cost, expected", [(0.001, 0.001), (1, 1.0), (0, 0.0), ("0.1", None), (None, None)]
    )
    def test_openrouter_cost_is_the_usage_cost(self, env, cost, expected):
        body = {**OK_BODY, "usage": {"prompt_tokens": 1, "cost": cost}}
        llm = make(Wire(ok(body)))
        llm.chat_json("p", "s", "u")
        assert llm.calls[0].cost == expected

    def test_total_cost_and_take_calls(self, env):
        bodies = [
            {**OK_BODY, "usage": {"cost": 0.1}},
            {**OK_BODY, "usage": {"cost": 0.25}},
            {**OK_BODY, "usage": {}},
        ]
        llm = make(Wire(*[ok(b) for b in bodies]))
        assert llm.cost is None
        for _ in bodies:
            llm.chat_json("p", "s", "u")
        assert llm.cost == 0.35
        taken = llm.take_calls()
        assert len(taken) == 3 and llm.calls == [] and llm.cost is None
        assert llm.take_calls() == []

    def test_the_total_is_rounded(self, env):
        bodies = [{**OK_BODY, "usage": {"cost": 0.1}}, {**OK_BODY, "usage": {"cost": 0.2}}]
        llm = make(Wire(*[ok(b) for b in bodies]))
        llm.chat_json("p", "s", "u")
        llm.chat_json("p", "s", "u")
        assert llm.cost == 0.3  # not 0.30000000000000004

    def test_a_failed_call_is_recorded_with_its_error_and_costs_nothing(self, env, sleeps):
        llm = make(Wire(httpx.Response(500), httpx.Response(500)))
        with pytest.raises(AskLLMError):
            llm.chat_json("generate", "s", "u")
        (call,) = llm.calls
        assert call.purpose == "generate" and not call.ok
        assert call.error == "gave up after 2 attempts (HTTP 500)" and call.cost is None
        assert call.model == MODEL and call.provider is None

    def test_openai_compatible_cost_comes_from_the_prices(self, env):
        env.fake = FakeEnv(openrouter=None, base_url="http://localhost:8080/v1")
        cfg = AskLLMConfig(
            model="m", provider="openai_compatible", price_in_per_m=0.5, price_out_per_m=1.5
        )
        body = {**OK_BODY, "usage": {"prompt_tokens": 2000, "completion_tokens": 1000, "cost": 99}}
        llm = AskLLM(cfg, client=Wire(ok(body), ok({**OK_BODY, "usage": {}})).client())
        llm.chat_json("p", "s", "u")
        assert llm.calls[0].cost == pytest.approx((2000 * 0.5 + 1000 * 1.5) / 1e6)
        llm.chat_json("p", "s", "u")
        assert llm.calls[1].cost == 0.0  # no tokens reported: zero, not None

    @pytest.mark.parametrize("kw", [{}, {"price_in_per_m": 1.0}, {"price_out_per_m": 1.0}])
    def test_openai_compatible_without_both_prices_has_no_cost(self, env, kw):
        env.fake = FakeEnv(openrouter=None, base_url="http://localhost:8080/v1")
        cfg = AskLLMConfig(model="m", provider="openai_compatible", **kw)
        llm = AskLLM(cfg, client=Wire(ok()).client())
        llm.chat_json("p", "s", "u")
        assert llm.calls[0].cost is None


class TestOneItemListReplies:
    """OpenRouter's Parasail endpoint for DeepSeek V4.1 Flash 1017 wrapped single values in a list
    ({"action": ["template"]}), so every router reply was "no valid action" (2026-10-18)."""

    def test_a_top_level_one_item_list_is_its_value(self):
        out = parse_object(
            '{"action": ["template"], "template": "season_leaders", "params": {"limit": 1}}'
        )
        assert out == {"action": "template", "template": "season_leaders", "params": {"limit": 1}}
        assert parse_object('{"accept": [true]}') == {"accept": True}
        assert parse_object('{"query": ["SELECT 1"]}') == {"query": "SELECT 1"}

    def test_longer_lists_and_nested_values_are_kept(self):
        assert parse_object('{"choices": ["a", "b"]}') == {"choices": ["a", "b"]}
        assert parse_object('{"params": {"teams": ["KC"]}}') == {"params": {"teams": ["KC"]}}
        assert parse_object('{"x": [{"a": 1}]}') == {"x": [{"a": 1}]}
        assert parse_object('{"x": []}') == {"x": []}


# ---- make_llm ---------------------------------------------------------------------------------


class TestMakeLLM:
    @pytest.fixture
    def full(self, monkeypatch):
        cfg = AskConfig(
            llm=AskLLMConfig(
                model="default/model", reasoning_effort="medium", openrouter={"sort": "price"}
            ),
            candidates=[
                AskCandidate(model="cand/one", efforts=["none", "low"], openrouter={"only": ["a"]}),
                AskCandidate(model="cand/two", efforts=[None]),
                AskCandidate(model="cand/empty", efforts=[]),
            ],
        )
        monkeypatch.setattr("nflengine.ask.config.ask_config", lambda data=None: cfg)
        return cfg

    def test_the_configured_default(self, full):
        llm = make_llm()
        assert llm.model == "default/model" and llm.effort == "medium"
        assert llm.routing == {"sort": "price"}

    def test_a_candidate_brings_its_first_effort_and_its_routing(self, full):
        llm = make_llm(model="cand/one")
        assert llm.model == "cand/one" and llm.effort == "none"
        assert llm.routing == {"only": ["a"]}

    def test_a_candidate_without_routing_keeps_the_configs(self, full):
        llm = make_llm(model="cand/two")
        assert llm.effort is None and llm.routing == {"sort": "price"}
        assert make_llm(model="cand/empty").effort is None

    def test_explicit_arguments_win(self, full):
        llm = make_llm(model="cand/one", reasoning_effort="high", routing={"x": 1})
        assert llm.effort == "high" and llm.routing == {"x": 1}

    def test_the_configured_model_keeps_its_own_effort_when_it_is_a_candidate_too(
        self, full, monkeypatch
    ):
        # the winner (Luna at max) also sits in the list at low / none: ask.llm must win
        cfg = full.model_copy(
            update={
                "llm": full.llm.model_copy(update={"model": "cand/one", "reasoning_effort": "max"})
            }
        )
        monkeypatch.setattr("nflengine.ask.config.ask_config", lambda data=None: cfg)
        llm = make_llm(model="cand/one")
        assert llm.effort == "max" and llm.routing == {"sort": "price"}
        assert make_llm(model="cand/one", reasoning_effort="low").effort == "low"
        assert make_llm(model="cand/two").effort is None  # another candidate: its own

    def test_an_unknown_model_uses_the_default_effort(self, full):
        llm = make_llm(model="other/model")
        assert llm.model == "other/model" and llm.effort == "medium"

    def test_a_given_config_is_used(self, full):
        llm = make_llm(AskLLMConfig(model="mine"))
        assert llm.model == "mine"
