"""The `ask:` block of config/settings.yaml, typed (AE00; documentation/ask-the-engine/ §4).

Ask has its own LLM settings (D111): the digest's `llm:` block is never read or changed here.
Secrets stay in the env file under existing names: `OPENROUTER_API_KEY`, or `LLM_API_KEY` +
`LLM_BASE_URL` for a local OpenAI-compatible server.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskLLMConfig(BaseModel):
    provider: Literal["openrouter", "openai_compatible"] = "openrouter"
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

    def candidate(self, model: str) -> AskCandidate | None:
        return next((c for c in self.candidates if c.model == model), None)


def ask_config(data: dict[str, Any] | None = None) -> AskConfig:
    """The typed `ask:` block (default: settings.yaml's)."""
    if data is None:
        from nflengine.settings import get_config

        data = get_config().ask
    return AskConfig.model_validate(data or {})
