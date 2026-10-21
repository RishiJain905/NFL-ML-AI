"""The Ask LLM provider (AE00, D111): separate from the digest's writer (`digest/llm/`).

Two providers, chosen by `ask.llm.provider` in settings.yaml:
- `openrouter`: any model on OpenRouter, with the existing `OPENROUTER_API_KEY`; low or no
  reasoning (`reasoning.effort`, its text excluded), provider routing from `ask.llm.openrouter`,
  the call's cost from OpenRouter's usage accounting.
- `openai_compatible`: a local or self-hosted server speaking the OpenAI chat API at
  `LLM_BASE_URL` (HTTPS, or plain HTTP on localhost only), with `LLM_API_KEY` if it wants one.
  Its cost is computed from `price_in_per_m` / `price_out_per_m` when they're set.
- `local` (AE04, D152): the fine-tuned local model, served on this computer (llama-server or
  LM Studio). Its address is `ask.local.base_url` in settings.yaml (localhost only, no key: not
  a secret), its chat-template switches (thinking off) go in `chat_template_kwargs`, and its
  cost is a known 0. `nfl ask eval --model local:<name>` uses it.

Every reply must be one JSON object (JSON mode is requested; the reply is parsed leniently:
code fences and text around the object are ignored). Every call is recorded in `calls`
(purpose, model, serving provider, latency, tokens, cost, finish reason): never the key, never
the prompt. Errors carry fixed text, HTTP status codes and short error codes only (a provider's
message could echo the request).

Nothing here is shared with `digest/llm/` except two read-only helpers (`_json`,
`_error_text`), so switching Ask's model can't change the digest.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from nflengine.ask.config import AskLLMConfig
from nflengine.digest.llm.openrouter import _error_text, _json

OPENROUTER_URL = "https://openrouter.ai/api/v1"
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class AskLLMError(RuntimeError):
    """The model couldn't give a usable reply. The message is fixed text (+ an HTTP status or
    a short error code): safe for files, logs and W&B."""


class OutOfCredits(AskLLMError):
    """OpenRouter said 402: the account is out of credits (the bake-off pauses on it)."""


class UnusableReply(AskLLMError):
    """The reply came back but was empty or held no usable JSON object (asked once more)."""


@dataclass
class LLMCall:
    purpose: str  # route | generate | fix | answer
    model: str | None
    provider: str | None
    latency_s: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost: float | None = None
    finish_reason: str | None = None
    ok: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_object(text: str) -> dict[str, Any]:
    """The outermost JSON object in a reply (code fences and surrounding text ignored)."""
    if not text or not text.strip():
        raise AskLLMError("empty reply")
    body = _FENCE.sub("", text.strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise AskLLMError("no JSON object in the reply")
    try:
        data = json.loads(body[start : end + 1])
    except json.JSONDecodeError as e:
        raise AskLLMError(f"reply is not valid JSON ({e.msg})") from None
    if not isinstance(data, dict):
        raise AskLLMError("reply JSON is not an object")
    # some endpoints wrap a single value in a list (OpenRouter's Parasail for DeepSeek V4.1 Flash
    # 1017 sent {"action": ["template"]}, 2026-10-18): a top-level one-item list of a string,
    # number or bool is that value. Nested values (a template's params) are left as they are.
    return {
        k: v[0]
        if isinstance(v, list) and len(v) == 1 and isinstance(v[0], str | int | float | bool)
        else v
        for k, v in data.items()
    }


def _base_url(raw: str | None) -> str:
    if not raw or not raw.strip():
        raise AskLLMError("LLM_BASE_URL is not set (check with `nfl doctor`)")
    if "?" in raw or "#" in raw:  # even an empty query or fragment would eat the path
        raise AskLLMError("LLM_BASE_URL must not carry a query string or a fragment")
    u = urlparse(raw.strip())
    if u.scheme == "https" or (u.scheme == "http" and (u.hostname or "") in LOCAL_HOSTS):
        if u.username or u.password or u.query:
            raise AskLLMError("LLM_BASE_URL must not carry credentials or a query string")
        return raw.strip().rstrip("/")
    raise AskLLMError("LLM_BASE_URL must be https (plain http only on localhost)")


def _local_url(raw: str | None) -> str:
    """`ask.local.base_url`: this computer only (a model served by llama-server or LM
    Studio), http or https, no credentials, query or fragment (AE04)."""
    if not raw or not str(raw).strip():
        raise AskLLMError("ask.local.base_url is not set in settings.yaml")
    text = str(raw).strip()
    if "?" in text or "#" in text:
        raise AskLLMError("ask.local.base_url must not carry a query string or a fragment")
    u = urlparse(text)
    if u.scheme not in ("http", "https") or (u.hostname or "") not in LOCAL_HOSTS:
        raise AskLLMError("ask.local.base_url must be on this computer (localhost)")
    if u.username or u.password:
        raise AskLLMError("ask.local.base_url must not carry credentials")
    return text.rstrip("/")


class AskLLM:
    """One provider client for a question (or a whole eval run): `chat_json` per call."""

    def __init__(
        self,
        cfg: AskLLMConfig,
        *,
        model: str | None = None,
        reasoning_effort: str | None | object = ...,
        routing: dict[str, Any] | None = None,
        client: httpx.Client | None = None,
    ):
        self.cfg = cfg
        self.provider = cfg.provider
        self.model = model or cfg.model
        if not self.model:
            raise AskLLMError("ask.llm.model is not set in settings.yaml")
        self.effort = cfg.reasoning_effort if reasoning_effort is ... else reasoning_effort
        self.routing = dict(cfg.openrouter if routing is None else routing)
        self.timeout = float(cfg.timeout_s)
        self.retries = max(0, int(cfg.retries))
        self._client = client
        self.calls: list[LLMCall] = []

    # ---- request ------------------------------------------------------------------------------

    def _endpoint_and_headers(self) -> tuple[str, dict[str, str]]:
        from nflengine.settings import get_env

        env = get_env()
        headers = {"Content-Type": "application/json"}
        if self.provider == "openrouter":
            if not env.is_set("OPENROUTER_API_KEY"):
                raise AskLLMError("OPENROUTER_API_KEY is not set (check with `nfl doctor`)")
            headers["Authorization"] = f"Bearer {env.openrouter_api_key.get_secret_value()}"
            headers["X-Title"] = "NFL Analytics Engine ask"
            return f"{OPENROUTER_URL}/chat/completions", headers
        if self.provider == "openai_compatible":
            base = _base_url(env.llm_base_url)
            if env.is_set("LLM_API_KEY"):
                headers["Authorization"] = f"Bearer {env.llm_api_key.get_secret_value()}"
            return f"{base}/chat/completions", headers
        if self.provider == "local":  # AE04: settings.yaml's ask.local (localhost, no key)
            return f"{_local_url(self.cfg.base_url)}/chat/completions", headers
        raise AskLLMError("unknown ask.llm.provider")

    def request_body(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": int(self.cfg.max_tokens),
            "response_format": {"type": "json_object"},
        }
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature
        if self.provider == "openrouter":
            body["usage"] = {"include": True}
            if self.effort:
                body["reasoning"] = {"effort": self.effort, "exclude": True}
            if self.routing:
                body["provider"] = self.routing
        if self.provider == "local" and self.cfg.chat_template_kwargs:
            # llama-server / vLLM read this: thinking off for the fine-tuned model (AE04)
            body["chat_template_kwargs"] = dict(self.cfg.chat_template_kwargs)
        return body

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        url, headers = self._endpoint_and_headers()
        client = self._client or httpx.Client(timeout=self.timeout)
        try:
            last = "no attempt"
            for attempt in range(self.retries + 1):
                if attempt:
                    time.sleep(min(4.0, 1.0 * 2 ** (attempt - 1)))
                try:
                    r = client.post(url, json=body, headers=headers)
                except httpx.TimeoutException:
                    last = "timeout"
                    continue
                except httpx.HTTPError as e:
                    last = type(e).__name__
                    continue
                if r.status_code == 402:
                    raise OutOfCredits("HTTP 402: out of OpenRouter credits")
                if r.status_code in RETRY_STATUS:
                    last = f"HTTP {r.status_code}"
                    continue
                data = _json(r)
                if r.status_code != 200:
                    raise AskLLMError(f"HTTP {r.status_code}: {_error_text(data)}")
                if "error" in data and not data.get("choices"):
                    last = f"provider error: {_error_text(data)}"
                    continue
                return data
            raise AskLLMError(f"gave up after {self.retries + 1} attempts ({last})")
        finally:
            if self._client is None:
                client.close()

    # ---- calls --------------------------------------------------------------------------------

    def _cost(self, usage: dict[str, Any]) -> float | None:
        if self.provider == "openrouter":
            c = usage.get("cost")
            return float(c) if isinstance(c, int | float) else None
        pin, pout = self.cfg.price_in_per_m, self.cfg.price_out_per_m
        if pin is None or pout is None:
            return None
        return (
            (usage.get("prompt_tokens") or 0) * pin + (usage.get("completion_tokens") or 0) * pout
        ) / 1e6

    def chat_json(
        self, purpose: str, system: str, user: str, extra: list[dict] | None = None
    ) -> dict:
        """The reply's JSON object. A reply that's empty or holds no usable JSON object is asked
        for once more (at a high reasoning effort an endpoint can spend the whole budget
        thinking and send nothing: DeepSeek V4.1 Flash 1017 at "max", 2026-10-18); transport
        errors are retried in `_post`, and out of credits never is. Every attempt is recorded
        (its cost counts)."""
        try:
            return self._chat_json_once(purpose, system, user, extra)
        except UnusableReply:
            return self._chat_json_once(purpose, system, user, extra)

    def _chat_json_once(
        self, purpose: str, system: str, user: str, extra: list[dict] | None = None
    ) -> dict:
        """One call; the reply's JSON object. Records the call either way."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        messages += list(extra or [])
        call = LLMCall(purpose=purpose, model=self.model, provider=None, latency_s=0.0)
        self.calls.append(call)
        t0 = time.monotonic()
        try:
            data = self._post(self.request_body(messages))
        except AskLLMError as e:
            call.latency_s = round(time.monotonic() - t0, 3)
            call.error = str(e)
            raise
        call.latency_s = round(time.monotonic() - t0, 3)
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        call.model = data.get("model") or self.model
        call.provider = data.get("provider") if isinstance(data.get("provider"), str) else None
        call.prompt_tokens = usage.get("prompt_tokens")
        call.completion_tokens = usage.get("completion_tokens")
        call.reasoning_tokens = (usage.get("completion_tokens_details") or {}).get(
            "reasoning_tokens"
        )
        call.cost = self._cost(usage)
        call.finish_reason = choice.get("finish_reason")
        text = (choice.get("message") or {}).get("content") or ""
        try:
            obj = parse_object(text)
        except AskLLMError as e:
            call.error = str(e)
            raise UnusableReply(str(e)) from None
        call.ok = True
        return obj

    @property
    def cost(self) -> float | None:
        costs = [c.cost for c in self.calls if c.cost is not None]
        return round(sum(costs), 8) if costs else None

    def take_calls(self) -> list[LLMCall]:
        """The calls made since the last take (one question's calls in an eval run)."""
        out, self.calls = self.calls, []
        return out


def make_llm(cfg: AskLLMConfig | None = None, model: str | None = None, **kw: Any) -> AskLLM:
    """The configured client; for another model that is a bake-off candidate, the candidate's
    first effort and its routing. The configured model always runs as `ask.llm` says, even when
    it is a candidate too (the winner sits in the list at the efforts it was tried at)."""
    from nflengine.ask.config import LOCAL_PREFIX, ask_config

    full = ask_config()
    if model and model.startswith(LOCAL_PREFIX):  # AE04: the local model, its own block
        local = cfg if cfg is not None and cfg.provider == "local" else full.llm_for(model)
        kw.setdefault("reasoning_effort", None)
        return AskLLM(local, model=local.model, **kw)
    cfg = cfg or full.llm
    cand = full.candidate(model) if model and model != cfg.model else None
    if cand is not None:
        kw.setdefault("reasoning_effort", cand.reasoning_effort)
        if cand.openrouter is not None:
            kw.setdefault("routing", cand.openrouter)
    return AskLLM(cfg, model=model, **kw)
