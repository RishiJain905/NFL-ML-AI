"""The router's candidate templates for a question (AE04): the few most similar, not all 66.

The router's prompt lists every template (~37,000 characters, ~10k tokens): fine for an
OpenRouter model, far too long to fine-tune a 4B model on or to read on every question
locally. With `route_candidates: k` (the `ask.local` block, D152) the router sees only the `k`
templates most similar to the question, in the library's own order (no position signal), and
the model's job is the behaviour (pick one of these, or "generate" when none answers the whole
question), which is what AE04 trains.

Similarity is lexical (rapidfuzz `token_set_ratio`, the measure `route._examples` uses) between
the question and each template's texts: its title, its examples and, when given, its training
skeletons (`config/ask_train_phrasings.yaml`, slots removed). Names, team words and numbers are
removed from both sides first: they are the question's facts, not its shape ("Mahomes's
passing yards in 2024" and "Burrow's passing yards in 2022" ask the same thing).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from nflengine.ask.templates import Template

_SLOT = re.compile(r"\{[a-z_]+(?::[A-Za-z/]+)?\}")
_DIGITS = re.compile(r"\b\d+(?:st|nd|rd|th)?\b")
_SPACE = re.compile(r"\s+")


def shape(text: str, spans: Iterable[tuple[int, int]] = ()) -> str:
    """The question without its facts: name spans cut out, numbers and slots removed,
    lower case, single spaces."""
    s = str(text or "")
    for start, end in sorted(spans, reverse=True):
        s = s[:start] + " " + s[end:]
    s = _SLOT.sub(" ", s)
    s = _DIGITS.sub(" ", s)
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return _SPACE.sub(" ", s).strip()


class TemplateRetriever:
    """Ranks templates by similarity to a question. `texts`: extra texts per template id
    (training skeletons); a template's title and examples always count."""

    def __init__(
        self,
        templates: dict[str, Template],
        texts: dict[str, list[str]] | None = None,
        names=None,
    ):
        self.templates = templates
        self.order = {tid: i for i, tid in enumerate(templates)}
        self.names = names
        self.texts: dict[str, list[str]] = {}
        for tid, t in templates.items():
            raw = [t.title, *t.examples, *((texts or {}).get(tid, []))]
            self.texts[tid] = [s for s in (self._shape(x) for x in raw) if s]

    def _shape(self, text: str) -> str:
        spans = []
        if self.names is not None:
            try:
                spans = [(m.start, m.end) for m in self.names.mentions(text)]
            except Exception:  # a name index that can't read this text: no spans
                spans = []
        return shape(text, spans)

    def scores(self, question: str, mentions=None) -> dict[str, float]:
        from rapidfuzz import fuzz

        if mentions is not None:
            q = shape(question, [(m.start, m.end) for m in mentions])
        else:
            q = self._shape(question)
        out: dict[str, float] = {}
        for tid, texts in self.texts.items():
            out[tid] = max((fuzz.token_set_ratio(q, s) for s in texts), default=0.0)
        return out

    def top(
        self,
        question: str,
        k: int,
        allowed: Iterable[str] | None = None,
        mentions=None,
    ) -> list[str]:
        """The `k` best template ids among `allowed` (all when None), in library order."""
        ok = set(self.templates if allowed is None else allowed)
        sc = self.scores(question, mentions)
        ranked = sorted(
            (tid for tid in sc if tid in ok), key=lambda tid: (-sc[tid], self.order[tid])
        )
        best = ranked[: max(0, int(k))]
        return sorted(best, key=lambda tid: self.order[tid])
