"""Prompt files for the digest LLM (documentation/06 -> Prompt structure).

`system.md` is the system prompt; `sections.yaml` lists the prose sections with their
instructions. Both are versioned by a hash (`PromptBundle.hash`) that each run logs to W&B,
so a prompt change is visible next to its effect. The placeholder writer ignores the text;
a real provider (P09) sends it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROMPT_DIR = Path(__file__).resolve().parent
LIVE_PHASE = "P04"  # sections tagged with a later phase stay off until that phase


@dataclass(frozen=True)
class SectionSpec:
    id: str
    title: str
    payload: tuple[str, ...]
    instructions: str
    phase: str | None = None


@dataclass(frozen=True)
class PromptBundle:
    system: str
    sections: tuple[SectionSpec, ...]
    hash: str

    def active(self, enabled_phases: tuple[str, ...] = ()) -> list[SectionSpec]:
        return [s for s in self.sections if s.phase is None or s.phase in enabled_phases]

    def output_spec(
        self, budgets: dict[str, int], enabled_phases: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """What the LLM must return: one markdown string per active section id."""
        active = self.active(enabled_phases)
        return {
            "format": "json object {section_id: markdown}",
            "sections": [
                {
                    "id": s.id,
                    "title": s.title,
                    "payload_keys": list(s.payload),
                    "word_budget": budgets.get(s.id),
                    "instructions": s.instructions,
                }
                for s in active
            ],
            "total_word_budget": sum(budgets.get(s.id, 0) for s in active),
        }


def load_prompt(prompt_dir: Path | None = None) -> PromptBundle:
    d = prompt_dir or PROMPT_DIR
    system = (d / "system.md").read_text(encoding="utf-8")
    raw = (d / "sections.yaml").read_text(encoding="utf-8")
    specs = tuple(
        SectionSpec(
            id=s["id"],
            title=s["title"],
            payload=tuple(s.get("payload", [])),
            instructions=" ".join(str(s.get("instructions", "")).split()),
            phase=s.get("phase"),
        )
        for s in (yaml.safe_load(raw) or {}).get("sections", [])
    )
    digest = hashlib.sha256((system + "\n---\n" + raw).encode("utf-8")).hexdigest()[:12]
    return PromptBundle(system=system, sections=specs, hash=digest)
