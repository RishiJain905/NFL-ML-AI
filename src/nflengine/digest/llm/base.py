"""The provider-agnostic LLM interface (documentation/06 -> LLM provider interface).

Nothing outside `digest/llm/` imports a provider: the pipeline asks the registry in
`digest/llm/__init__.py` for whatever `llm.provider` names in `config/settings.yaml`.
Switching providers changes only config (and, for a real provider, the env file).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    name: str
    model: str | None

    def generate(
        self, system_prompt: str, payload: dict[str, Any], output_spec: dict[str, Any]
    ) -> dict[str, str]:
        """Return {section_id: markdown} for the prose sections in `output_spec`.

        On a regeneration, `output_spec["feedback"]` lists the offending tokens from the
        failed checks.
        """
        ...


class ProviderNotConnected(RuntimeError):
    """The configured provider has no client (`anthropic` / `openai_compatible`, D87)."""


class LLMError(RuntimeError):
    """The provider couldn't produce usable output (the pipeline falls back).

    Messages are built only from fixed text, HTTP status codes and short error codes,
    never from provider-supplied text (which could echo a credential), so they are safe
    to log, save to `raw_llm_output.json` / `checks.json` and send to W&B.
    """
