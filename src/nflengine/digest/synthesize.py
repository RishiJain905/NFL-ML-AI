"""LLM call + checks + the regenerate-once flow (documentation/06 -> Automated checks).

generate -> checks -> if a fail-level check failed: generate once more with the list of
offending tokens (and the first draft) -> checks again -> if it still fails, publish anyway
with a visible warning banner (a flagged digest is better than none; documentation/06).

If the provider itself fails (network, quota, unusable JSON), the `fallback` writer (the
placeholder) produces that attempt instead and the run records why (D56): the digest still
ships, and the footer and W&B say which writer wrote it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nflengine.digest.checks import CheckReport, Lexicon, run_checks
from nflengine.digest.facts import FactIndex
from nflengine.digest.llm.base import LLMClient, LLMError
from nflengine.digest.payload import Payload
from nflengine.digest.prompt import PromptBundle


@dataclass
class Synthesis:
    sections: dict[str, str]
    first: CheckReport
    final: CheckReport
    regenerated: bool
    attempts: list[dict[str, str]] = field(default_factory=list)
    writers: list[str] = field(default_factory=list)  # who wrote each attempt
    fallback_notes: list[str] = field(default_factory=list)

    @property
    def banner(self) -> str | None:
        if self.final.passed:
            return None
        return (
            "⚠️ **Automated checks failed after one regeneration:** "
            + ", ".join(self.final.failed)
            + ". Treat the prose below with care; details in `checks.json`."
        )

    @property
    def final_writer(self) -> str:
        return self.writers[-1] if self.writers else "unknown"

    def checks_json(self) -> dict:
        return {
            "passed": self.final.passed,
            "regenerated": self.regenerated,
            "banner": self.banner is not None,
            "writers": self.writers,
            "fallback_notes": self.fallback_notes,
            "first_attempt": self.first.to_dict(),
            "final": self.final.to_dict(),
        }


def _clean(raw: dict[str, str], expected: list[str]) -> dict[str, str]:
    """Exactly the expected section ids, as strings (missing -> "")."""
    return {k: str(raw.get(k) or "").strip() for k in expected}


def _generate(
    llm: LLMClient,
    fallback: LLMClient | None,
    system: str,
    data: dict[str, Any],
    spec: dict[str, Any],
    notes: list[str],
) -> tuple[dict[str, str], str]:
    try:
        return llm.generate(system, data, spec), llm.name
    except Exception as e:  # any provider failure: fall back rather than publish nothing
        if fallback is None:
            raise
        # LLMError text is sanitized by construction; anything else is reported by type only
        detail = f"{type(e).__name__}: {e}" if isinstance(e, LLMError) else type(e).__name__
        notes.append(f"{llm.name} failed ({detail[:200]}); used {fallback.name}")
        return fallback.generate(system, data, spec), fallback.name


def length_feedback(out: dict[str, str], budgets: dict[str, int]) -> list[str]:
    """Every section's word count and limit, sent with any regeneration: fixing one issue
    must not push another section over its limit (the first live digest went 149 -> 151
    words on a 150-word limit while fixing a binding issue)."""
    from nflengine.digest.checks import LENGTH_TOLERANCE, word_count

    return [
        f"[length] {sec}: {word_count(out.get(sec, ''))} words now; aim for {b}, "
        f"never more than {int(b * (1 + LENGTH_TOLERANCE))}"
        for sec, b in budgets.items()
    ]


def synthesize(
    payload: Payload,
    facts: FactIndex,
    llm: LLMClient,
    prompt: PromptBundle,
    budgets: dict[str, int],
    lexicon: Lexicon | None = None,
    fallback: LLMClient | None = None,
    fixed: dict[str, str] | None = None,
) -> Synthesis:
    """`fixed`: sections written by code instead of the LLM (e.g. a report card with
    nothing to grade); they are left out of the LLM's output spec but checked like the rest."""
    fixed = fixed or {}
    spec = prompt.output_spec(budgets)
    expected = [s["id"] for s in spec["sections"]]
    spec = {
        **spec,
        "sections": [s for s in spec["sections"] if s["id"] not in fixed],
        "total_word_budget": sum(
            budgets.get(s["id"], 0) for s in spec["sections"] if s["id"] not in fixed
        ),
    }
    active_budgets = {k: v for k, v in budgets.items() if k in expected}
    llm_budgets = {k: v for k, v in active_budgets.items() if k not in fixed}
    data = payload.model_dump(mode="json")
    notes: list[str] = []

    def with_fixed(raw: dict[str, str]) -> dict[str, str]:
        return _clean({**raw, **fixed}, expected)

    raw, writer = _generate(llm, fallback, prompt.system, data, spec, notes)
    first_out = with_fixed(raw)
    first = run_checks(first_out, facts, budgets=active_budgets, lexicon=lexicon)
    if first.passed:
        return Synthesis(first_out, first, first, False, [first_out], [writer], notes)
    feedback = first.feedback() + length_feedback(first_out, llm_budgets)
    previous = {k: v for k, v in first_out.items() if k not in fixed}
    retry_spec = {**spec, "feedback": feedback, "previous": previous}
    writer2 = writer
    try:
        raw2, writer2 = _generate(
            llm if writer == llm.name else fallback,
            fallback,
            prompt.system,
            data,
            retry_spec,
            notes,
        )
        second_out = with_fixed(raw2)
    except Exception as e:  # the regeneration itself failed: keep the first draft
        notes.append(f"regeneration failed ({type(e).__name__}); kept the first draft")
        second_out = first_out
    final = run_checks(second_out, facts, budgets=active_budgets, lexicon=lexicon)
    return Synthesis(
        second_out, first, final, True, [first_out, second_out], [writer, writer2], notes
    )
