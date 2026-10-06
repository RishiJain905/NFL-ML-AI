"""LLM call + checks + the regenerate-once flow (documentation/06 -> Automated checks).

generate -> checks -> if a fail-level check failed: generate once more with the list of
offending tokens (and the first draft) -> checks again -> if it still fails, publish anyway
with a visible warning banner (a flagged digest is better than none; documentation/06).

If the provider itself fails (network, quota, unusable JSON), the `fallback` writer (the
placeholder) produces that attempt instead and the run records why (D56): the digest still
ships, and the footer and W&B say which writer wrote it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from nflengine.digest.checks import CheckReport, Lexicon, run_checks
from nflengine.digest.facts import FactIndex
from nflengine.digest.llm.base import LLMClient, LLMError
from nflengine.digest.payload import Payload
from nflengine.digest.prompt import PromptBundle
from nflengine.ops import events


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


def _llm_event(phase: str, client: LLMClient, t0: float | None = None, calls: int = 0) -> None:
    """CR02: what the live view can know about an LLM call (`ops/events.py`; a no-op without
    an events writer). The call itself is unchanged: the provider, time, counts and cost are
    read from the client's `calls` record once it returns."""
    routing = getattr(client, "routing", None) or {}
    route = routing.get("order") or routing.get("only") or []
    new = list(getattr(client, "calls", None) or [])[calls:]
    last = new[-1] if new else {}
    usage = (
        {
            "prompt": last.get("prompt_tokens"),
            "completion": last.get("completion_tokens"),
            "reasoning": last.get("reasoning_tokens"),
        }
        if last
        else None
    )
    events.llm(
        phase,
        writer=getattr(client, "name", None),
        model=getattr(client, "model", None),
        route=[str(r) for r in route] if isinstance(route, list) else [],
        provider=last.get("provider") if last else None,
        seconds=None if t0 is None else time.monotonic() - t0,
        usage=usage,
        cost=last.get("cost") if last else None,
    )


def _call(llm: LLMClient, system: str, data: dict[str, Any], spec: dict[str, Any]):
    before = len(getattr(llm, "calls", None) or [])
    _llm_event("rewrite" if spec.get("previous") is not None else "start", llm, calls=before)
    t0 = time.monotonic()
    try:
        out = llm.generate(system, data, spec)
    except Exception:
        _llm_event("failed", llm, t0, before)
        raise
    _llm_event("done", llm, t0, before)
    return out


def _generate(
    llm: LLMClient,
    fallback: LLMClient | None,
    system: str,
    data: dict[str, Any],
    spec: dict[str, Any],
    notes: list[str],
) -> tuple[dict[str, str], str]:
    try:
        return _call(llm, system, data, spec), llm.name
    except Exception as e:  # any provider failure: fall back rather than publish nothing
        if fallback is None:
            raise
        # LLMError text is sanitized by construction; anything else is reported by type only
        detail = f"{type(e).__name__}: {e}" if isinstance(e, LLMError) else type(e).__name__
        notes.append(f"{llm.name} failed ({detail[:200]}); used {fallback.name}")
        return _call(fallback, system, data, spec), fallback.name


def length_feedback(
    out: dict[str, str], budgets: dict[str, int], total_budgets: dict[str, int] | None = None
) -> list[str]:
    """Every section's word count and limit, sent with any regeneration: fixing one issue
    must not push another section over its limit (the first live digest went 149 -> 151
    words on a 150-word limit while fixing a binding issue). With `total_budgets` (every
    active section, code-written ones included) the whole digest's cap is restated too:
    the per-section limits add up to more than it (a P05 backtest regenerated to 740 words
    on a 735-word cap with every section within its own limit)."""
    from nflengine.digest.checks import LENGTH_TOLERANCE, TOTAL_TOLERANCE, word_count

    lines = [
        f"[length] {sec}: {word_count(out.get(sec, ''))} words now; aim for {b}, "
        f"never more than {int(b * (1 + LENGTH_TOLERANCE))}"
        for sec, b in budgets.items()
    ]
    if total_budgets:
        total = sum(total_budgets.values())
        now = sum(word_count(out.get(sec, "")) for sec in total_budgets)
        lines.append(
            f"[length] whole digest: {now} words now; aim for {total}, never more than "
            f"{int(total * (1 + TOTAL_TOLERANCE))} in all (stay near each section's aim, "
            "not its maximum)"
        )
    return lines


def synthesize(
    payload: Payload,
    facts: FactIndex,
    llm: LLMClient,
    prompt: PromptBundle,
    budgets: dict[str, int],
    lexicon: Lexicon | None = None,
    fallback: LLMClient | None = None,
    fixed: dict[str, str] | None = None,
    enabled_phases: tuple[str, ...] = (),
) -> Synthesis:
    """`fixed`: sections written by code instead of the LLM (e.g. a report card with
    nothing to grade); they are left out of the LLM's output spec but checked like the rest.
    `enabled_phases`: phase-tagged sections to switch on (("P05",): the graph sections)."""
    fixed = fixed or {}
    spec = prompt.output_spec(budgets, enabled_phases)
    expected = [s["id"] for s in spec["sections"]]
    spec = {
        **spec,
        "sections": [s for s in spec["sections"] if s["id"] not in fixed],
        "total_word_budget": sum(
            budgets.get(s["id"], 0) for s in spec["sections"] if s["id"] not in fixed
        ),
    }
    active_budgets = {k: v for k, v in budgets.items() if k in expected}
    # length is the LLM's job: code-written sections (a 23-word "nothing to grade" card) are
    # left out of the length checks, or every such week warned `length_short`
    llm_budgets = {k: v for k, v in active_budgets.items() if k not in fixed}
    data = payload.model_dump(mode="json")
    notes: list[str] = []

    def with_fixed(raw: dict[str, str]) -> dict[str, str]:
        return _clean({**raw, **fixed}, expected)

    raw, writer = _generate(llm, fallback, prompt.system, data, spec, notes)
    first_out = with_fixed(raw)
    first = run_checks(first_out, facts, budgets=llm_budgets, lexicon=lexicon)
    events.progress(None, 85, 100, "checks " + ("passed" if first.passed else "failed: rewriting"))
    if first.passed:
        return Synthesis(first_out, first, first, False, [first_out], [writer], notes)
    feedback = first.feedback() + length_feedback(first_out, llm_budgets, llm_budgets)
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
    final = run_checks(second_out, facts, budgets=llm_budgets, lexicon=lexicon)
    return Synthesis(
        second_out, first, final, True, [first_out, second_out], [writer, writer2], notes
    )
