"""LLM provider registry, keyed by `llm.provider` in config/settings.yaml.

Providers load lazily by dotted path, so a provider's dependencies are only imported when
it is configured. `openrouter` (D56) serves every model, Claude included, by config alone,
so P09 closed without the `anthropic` / `openai_compatible` adapters (D87). Adding one later
is a module in this package plus a line in `PROVIDERS`; nothing else in the code base
changes.
"""

from __future__ import annotations

import importlib

from nflengine.digest.llm.base import LLMClient, ProviderNotConnected

PROVIDERS: dict[str, str] = {
    "placeholder": "nflengine.digest.llm.placeholder:PlaceholderLLM",
    "openrouter": "nflengine.digest.llm.openrouter:OpenRouterLLM",
}
NOT_BUILT = ("anthropic", "openai_compatible")  # documentation/06; not built (D87)


def get_llm(provider: str | None = None, model: str | None = None) -> LLMClient:
    """The client for `provider` (default: settings.yaml `llm.provider`)."""
    if provider is None:
        from nflengine.settings import get_config

        cfg = get_config().llm
        provider, model = cfg.provider, model or cfg.model
    if provider not in PROVIDERS:
        if provider in NOT_BUILT:
            raise ProviderNotConnected(
                f"LLM provider {provider!r} isn't built (D87). Use llm.provider: openrouter "
                "with that model's OpenRouter name, or placeholder."
            )
        raise ValueError(f"unknown LLM provider {provider!r}; known: {sorted(PROVIDERS)}")
    module, cls = PROVIDERS[provider].split(":")
    return getattr(importlib.import_module(module), cls)(model=model)


__all__ = ["LLMClient", "PROVIDERS", "ProviderNotConnected", "get_llm"]
