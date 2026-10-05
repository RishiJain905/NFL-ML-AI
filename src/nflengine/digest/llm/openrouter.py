"""OpenRouter provider for the digest prose (D56; `llm.provider: openrouter`).

One chat-completions call per generation to `https://openrouter.ai/api/v1`:
- `model` and `reasoning.effort` from `config/settings.yaml` (`llm.*`);
- provider routing from `llm.openrouter` (for example `only` = the allowed endpoints and
  `sort: price`, so the cheapest allowed endpoint serves the request and the next one is
  the fallback);
- the answer must be a JSON object `{section_id: markdown}`. JSON mode is requested but not
  required (not every endpoint supports it), so the reply is parsed leniently (code fences
  and text around the object are ignored).

A regeneration sends the first draft back with the failed checks' offending tokens.
Timeouts, 429 and 5xx are retried with backoff. Usage, cost, the serving provider and
latency of every call are kept in `calls` (logged to `raw_llm_output.json` and W&B).

Security: the key comes only from `EnvSettings.openrouter_api_key` (`SecretStr`), goes only
into the Authorization header, and never appears in logs, errors or W&B. Error messages
carry the HTTP status and OpenRouter's error message, never the request.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from nflengine.digest.llm.base import LLMError
from nflengine.settings import get_config, get_env

BASE_URL = "https://openrouter.ai/api/v1"
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_sections(text: str, expected: list[str]) -> dict[str, str]:
    """Lenient JSON parsing: strip code fences, take the outermost {...} object."""
    if not text:
        raise LLMError("empty reply")
    body = _FENCE.sub("", text.strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("no JSON object in the reply")
    try:
        data = json.loads(body[start : end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"reply is not valid JSON ({e.msg})") from None
    if not isinstance(data, dict):
        raise LLMError("reply JSON is not an object")
    bad = [k for k in expected if not isinstance(data.get(k), str) or not data[k].strip()]
    if bad:  # a partial reply must not pass as "checks passed" with empty sections
        raise LLMError(f"reply is missing or has blank sections: {', '.join(bad)}")
    return {k: data[k] for k in expected}


def user_message(payload: dict[str, Any], output_spec: dict[str, Any]) -> str:
    spec = {k: v for k, v in output_spec.items() if k not in ("feedback", "previous")}
    return (
        "OUTPUT SPEC (return exactly these section ids as one JSON object):\n"
        + json.dumps(spec, indent=1)
        + "\n\nPAYLOAD (use display strings exactly as written):\n<payload>\n"
        + json.dumps(payload, separators=(",", ":"))
        + "\n</payload>\n\nReturn only the JSON object."
    )


def fix_message(feedback: list[str]) -> str:
    return (
        "Your draft failed these automated checks. Rewrite the sections so every number is "
        "copied exactly from a display string, each number's owner is named in the same "
        "sentence, no banned word appears, and every section stays within the word limits "
        "listed below (fixing one issue must not make a section longer than its limit). "
        # P09 fact-check: a regeneration rewrote sections that hadn't failed and cut the
        # tail off a copied fact text ("..., compared with an average team") to save words
        "Change only what these issues require and keep every other sentence as it was. "
        "An issue that says 'no entity named' means the sentence names nobody: add the "
        "player or team name to it rather than deleting it. An issue that names someone "
        "means that number isn't one of theirs (often a count you added yourself, such as "
        "'in 1 game'): remove it or use their own display string. If a section must get "
        "shorter, drop a whole sentence or item, never words inside a copied display string "
        "or fact text. "
        "Return the full corrected JSON object.\n- " + "\n- ".join(feedback)
    )


class OpenRouterLLM:
    name = "openrouter"

    def __init__(self, model: str | None = None, client: httpx.Client | None = None):
        cfg = get_config().llm
        self.model = model or cfg.model
        if not self.model:
            raise ValueError("llm.model must be set for the openrouter provider")
        self.effort = cfg.reasoning_effort
        self.max_tokens = cfg.max_tokens
        self.temperature = cfg.temperature
        self.retries = max(0, int(cfg.retries))
        self.routing = dict(cfg.openrouter or {})
        self.timeout = float(cfg.timeout_seconds)
        self._client = client
        self.calls: list[dict[str, Any]] = []

    # ---- request ------------------------------------------------------------------------------

    def request_body(
        self, system_prompt: str, payload: dict[str, Any], output_spec: dict[str, Any]
    ) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message(payload, output_spec)},
        ]
        if output_spec.get("previous") is not None:
            messages.append({"role": "assistant", "content": json.dumps(output_spec["previous"])})
            messages.append({"role": "user", "content": fix_message(output_spec["feedback"])})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
        }
        if self.effort:
            body["reasoning"] = {"effort": self.effort, "exclude": True}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.routing:
            body["provider"] = self.routing
        return body

    def _headers(self) -> dict[str, str]:
        env = get_env()
        if not env.is_set("OPENROUTER_API_KEY"):
            raise LLMError("OPENROUTER_API_KEY is not set (check with `nfl doctor`)")
        return {
            "Authorization": f"Bearer {env.openrouter_api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "X-Title": "NFL Analytics Engine digest",
        }

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=self.timeout)
        try:
            last = "no attempt"
            for attempt in range(self.retries + 1):
                if attempt:
                    time.sleep(min(30, 5 * 2 ** (attempt - 1)))
                try:
                    r = client.post(
                        f"{BASE_URL}/chat/completions", json=body, headers=self._headers()
                    )
                except httpx.TimeoutException:
                    last = "timeout"
                    continue
                except httpx.HTTPError as e:
                    last = type(e).__name__
                    continue
                if r.status_code in RETRY_STATUS:
                    last = f"HTTP {r.status_code}"
                    continue
                data = _json(r)
                if r.status_code != 200:
                    raise LLMError(f"HTTP {r.status_code}: {_error_text(data)}")
                if "error" in data and not data.get("choices"):
                    last = f"provider error: {_error_text(data)}"
                    continue
                return data
            raise LLMError(f"gave up after {self.retries + 1} attempts ({last})")
        finally:
            if self._client is None:
                client.close()

    # ---- LLMClient ----------------------------------------------------------------------------

    def generate(
        self, system_prompt: str, payload: dict[str, Any], output_spec: dict[str, Any]
    ) -> dict[str, str]:
        expected = [s["id"] for s in output_spec.get("sections", [])]
        body = self.request_body(system_prompt, payload, output_spec)
        for attempt in range(2):  # one more request when the reply is unusable
            try:
                return self._call(body, expected, output_spec)
            except LLMError:
                if attempt == 1 or not self.calls or self.calls[-1].get("parsed", True):
                    raise
        raise LLMError("unreachable")

    def _call(
        self, body: dict[str, Any], expected: list[str], output_spec: dict[str, Any]
    ) -> dict[str, str]:
        t0 = time.monotonic()
        data = self._post(body)
        latency = time.monotonic() - t0
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        self.calls.append(
            {
                "model": data.get("model"),
                "provider": data.get("provider"),
                "latency_s": round(latency, 2),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get(
                    "reasoning_tokens"
                ),
                "cost": usage.get("cost"),
                "finish_reason": choice.get("finish_reason"),
                "regeneration": output_spec.get("previous") is not None,
                "parsed": False,
            }
        )
        sections = parse_sections(text, expected)
        self.calls[-1]["parsed"] = True
        return sections


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


_CODE = re.compile(r"[A-Za-z0-9_.-]{1,40}")


def _error_text(data: dict[str, Any]) -> str:
    """Only a short machine error code, never the provider's message text: a message could
    echo request content, including the credential (Sol review)."""
    err = data.get("error")
    code = err.get("code") if isinstance(err, dict) else None
    code = str(code) if code is not None else ""
    return f"error code {code}" if _CODE.fullmatch(code) else "provider error"
