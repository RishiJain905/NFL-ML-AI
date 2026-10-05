"""`nfl doctor`'s llm row (P09): provider known, key accepted, model listed, `only` live."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from nflengine import doctor
from nflengine.settings import AppConfig, EnvSettings, LLMConfig

FAKE_KEY = "sk-or-test-not-a-real-key"
GLM_TAGS = ["relace", "baseten/fp8", "novita/fp8", "deepinfra/fp4", "together"]


def _config(provider: str = "openrouter", only: list[str] | None = None) -> AppConfig:
    routing = {"only": only, "sort": "price"} if only is not None else {}
    return AppConfig(
        llm=LLMConfig(provider=provider, model="z-ai/glm-5.3-flash", openrouter=routing)
    )


@pytest.fixture
def fake_api(monkeypatch):
    """Key set; GET /key -> `key_status`; GET .../endpoints -> GLM's tags."""
    state = {"key_status": 200, "list_status": 200, "seen": [], "tags": GLM_TAGS, "raw": None}
    env = EnvSettings(_env_file=None, openrouter_api_key=SecretStr(FAKE_KEY))
    monkeypatch.setattr(doctor, "get_env", lambda: env)

    def get(url, headers=None, timeout=None):
        state["seen"].append((url, headers))
        req = httpx.Request("GET", url)
        if url.endswith("/key"):
            return httpx.Response(state["key_status"], json={"data": {}}, request=req)
        if state["raw"] is not None:  # a malformed body
            return httpx.Response(state["list_status"], content=state["raw"], request=req)
        body = {"data": {"endpoints": [{"tag": t} for t in state["tags"]]}}
        return httpx.Response(state["list_status"], json=body, request=req)

    monkeypatch.setattr(httpx, "get", get)
    return state


def _check(monkeypatch, cfg: AppConfig) -> doctor.Check:
    monkeypatch.setattr(doctor, "get_config", lambda: cfg)
    return doctor.check_llm()


def test_allowed_endpoints_listed(monkeypatch, fake_api) -> None:
    c = _check(monkeypatch, _config(only=["baseten/fp8", "relace", "novita/fp8", "deepinfra/fp4"]))
    assert c.status == doctor.OK
    assert "5 endpoints" in c.detail and "4 of 4 allowed" in c.detail
    # the key goes only into the /key header; the public model listing is called without it
    key_call, list_call = fake_api["seen"]
    assert key_call[1]["Authorization"].endswith(FAKE_KEY)
    assert list_call[1] is None


def test_provider_slug_matches_a_tagged_endpoint(monkeypatch, fake_api) -> None:
    c = _check(monkeypatch, _config(only=["novita", "nobody/fp8"]))
    assert c.status == doctor.OK and "1 of 2 allowed" in c.detail


def test_stale_only_list_after_a_model_switch_fails(monkeypatch, fake_api) -> None:
    c = _check(monkeypatch, _config(only=["anthropic", "google-vertex"]))
    assert c.status == doctor.FAIL and "none of the 2" in c.detail
    assert FAKE_KEY not in c.detail


def test_no_only_list_is_fine(monkeypatch, fake_api) -> None:
    c = _check(monkeypatch, _config(only=None))
    assert c.status == doctor.OK and "allowed" not in c.detail


def test_rejected_key_and_unknown_model(monkeypatch, fake_api) -> None:
    fake_api["key_status"] = 401
    assert _check(monkeypatch, _config(only=["relace"])).status == doctor.FAIL
    fake_api["key_status"], fake_api["list_status"] = 200, 404  # Sol: a missing model FAILs
    c = _check(monkeypatch, _config(only=["relace"]))
    assert c.status == doctor.FAIL and "not found" in c.detail
    fake_api["list_status"] = 503  # an outage is not proof the model is gone
    assert _check(monkeypatch, _config(only=["relace"])).status == doctor.WARN


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[1, 2]",
        b'{"data": {"endpoints": "x"}}',
        b'{"data": {"endpoints": [null]}}',  # Sol follow-up: entries must be objects
        b'{"data": {"endpoints": [{"tag": 7}]}}',  # ... with a string tag
    ],
)
def test_malformed_endpoint_listing_warns(monkeypatch, fake_api, raw: bytes) -> None:
    fake_api["raw"] = raw
    c = _check(monkeypatch, _config(only=["relace"]))
    assert c.status == doctor.WARN and "unreadable" in c.detail


def test_network_error_fails_without_detail(monkeypatch, fake_api) -> None:
    def boom(url, headers=None, timeout=None):
        raise httpx.ConnectError(f"connection refused while sending {headers}")

    monkeypatch.setattr(httpx, "get", boom)
    c = _check(monkeypatch, _config(only=["relace"]))
    assert c.status == doctor.FAIL and c.detail == "openrouter unreachable: ConnectError"
    assert FAKE_KEY not in c.detail


def test_a_base_slug_never_matches_a_service_tier(monkeypatch, fake_api) -> None:
    """Sol review / OpenRouter's rule: "openai" doesn't select "openai/fast"."""
    fake_api["tags"] = ["openai/fast", "google-vertex/flex", "google-vertex/us"]
    assert _check(monkeypatch, _config(only=["openai"])).status == doctor.FAIL
    assert _check(monkeypatch, _config(only=["openai/priority"])).status == doctor.OK
    assert _check(monkeypatch, _config(only=["google-vertex"])).status == doctor.OK


@pytest.mark.parametrize(
    ("provider", "status", "words"),
    [
        ("placeholder", doctor.OK, "no API"),
        ("anthropic", doctor.FAIL, "isn't built (D87)"),
        ("openai_compatible", doctor.FAIL, "isn't built (D87)"),
        ("gpt-nonsense", doctor.FAIL, "is unknown"),
    ],
)
def test_other_providers(monkeypatch, provider: str, status: str, words: str) -> None:
    c = _check(monkeypatch, _config(provider=provider))
    assert c.status == status and words in c.detail
