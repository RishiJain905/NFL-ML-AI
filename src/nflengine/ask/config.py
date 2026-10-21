"""The `ask:` block of config/settings.yaml, typed (AE00; documentation/ask-the-engine/ §4).

Ask has its own LLM settings (D111): the digest's `llm:` block is never read or changed here.
Secrets stay in the env file under existing names: `OPENROUTER_API_KEY`, or `LLM_API_KEY` +
`LLM_BASE_URL` for a local OpenAI-compatible server.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskLLMConfig(BaseModel):
    # local (AE04, D152): a model served on this computer (llama.cpp's llama-server, LM Studio);
    # its address is `base_url` (localhost only), not a secret, so it lives in settings.yaml
    provider: Literal["openrouter", "openai_compatible", "local"] = "openrouter"
    model: str | None = None
    # OpenRouter `reasoning.effort` (none | minimal | low | medium | high ...); None sends none.
    reasoning_effort: str | None = "low"
    max_tokens: int = 2000
    timeout_s: float = 30.0
    retries: int = 1  # extra attempts on timeouts, 429 and 5xx
    temperature: float | None = 0.0
    openrouter: dict[str, Any] = Field(default_factory=dict)  # provider routing (sort, only ...)
    # openai_compatible only: $ per million tokens, so a local model's cost can be logged
    price_in_per_m: float | None = None
    price_out_per_m: float | None = None
    # AE04: the router sees only the k templates most similar to the question (`retrieve.py`);
    # None: every template (the OpenRouter models' prompt, unchanged)
    route_candidates: int | None = None
    # local only: the server's address and the chat template's switches (thinking off)
    base_url: str | None = None
    chat_template_kwargs: dict[str, Any] | None = None


class AskLocalConfig(BaseModel):
    """`ask.local`: the fine-tuned local model (AE04). `nfl ask eval --model local:<name>`
    runs the golden set against it; `<name>` is the model name the server was started with."""

    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "spark-x2.6-4b-ask"
    max_tokens: int = 1024
    timeout_s: float = 120.0
    temperature: float | None = 0.0
    route_candidates: int | None = 10  # what the model was trained with (D153)
    chat_template_kwargs: dict[str, Any] = Field(default_factory=lambda: {"enable_thinking": False})

    def llm_config(self, model: str | None = None) -> AskLLMConfig:
        return AskLLMConfig(
            provider="local",
            model=model or self.model,
            reasoning_effort=None,
            max_tokens=self.max_tokens,
            timeout_s=self.timeout_s,
            temperature=self.temperature,
            price_in_per_m=0.0,  # free: a known cost of 0, so a tie can go to it (D138)
            price_out_per_m=0.0,
            route_candidates=self.route_candidates,
            base_url=self.base_url,
            chat_template_kwargs=dict(self.chat_template_kwargs),
        )


class AskLimits(BaseModel):
    rows: int = 500  # rows kept from any query (the rest is dropped and flagged)
    timeout_s: float = 10.0  # per query, both sources
    question_chars: int = 500
    corrections: int = 2  # correction rounds after an error or an empty result
    answer_rows: int = 40  # rows the answer step sees


class AskSqlConfig(BaseModel):
    # free-text tables stay out of the sandbox (README §5.3)
    exclude_tables: list[str] = Field(default_factory=lambda: ["espn_news"])
    memory_limit: str = "2GB"
    threads: int = 4


class AskGraphConfig(BaseModel):
    database: str | None = None  # None: the server's default database (the weekly graph)


class AskCandidate(BaseModel):
    """One bake-off entry: a model, the reasoning efforts to try it at, optional routing, and
    for a high effort its own reply cap and LLM timeout (the same model may have two entries)."""

    model: str
    efforts: list[str | None] = Field(default_factory=lambda: ["low"])
    openrouter: dict[str, Any] | None = None
    max_tokens: int | None = None  # None: ask.llm.max_tokens
    timeout_s: float | None = None  # None: ask.llm.timeout_s
    note: str | None = None

    @property
    def reasoning_effort(self) -> str | None:
        return self.efforts[0] if self.efforts else None


class AskConfig(BaseModel):
    llm: AskLLMConfig = Field(default_factory=AskLLMConfig)
    sources: dict[str, bool] = Field(
        default_factory=lambda: {"graph": True, "sql": True, "history": False}
    )
    limits: AskLimits = Field(default_factory=AskLimits)
    sql: AskSqlConfig = Field(default_factory=AskSqlConfig)
    graph: AskGraphConfig = Field(default_factory=AskGraphConfig)
    candidates: list[AskCandidate] = Field(default_factory=list)
    local: AskLocalConfig = Field(default_factory=AskLocalConfig)  # AE04

    def candidate(self, model: str) -> AskCandidate | None:
        return next((c for c in self.candidates if c.model == model), None)

    def llm_for(self, model: str | None) -> AskLLMConfig:
        """The LLM block a run uses: `local:<name>` -> the local model's (AE04), anything
        else -> `ask.llm` as it is."""
        if model and model.startswith(LOCAL_PREFIX):
            return self.local.llm_config(model[len(LOCAL_PREFIX) :] or None)
        return self.llm


LOCAL_PREFIX = "local:"


def ask_config(data: dict[str, Any] | None = None) -> AskConfig:
    """The typed `ask:` block (default: settings.yaml's)."""
    if data is None:
        from nflengine.settings import get_config

        data = get_config().ask
    return AskConfig.model_validate(data or {})
