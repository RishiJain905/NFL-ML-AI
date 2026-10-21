"""Question skeletons for AE04's training data (`config/ask_train_phrasings.yaml`).

The local model is trained on **behaviour**, not facts (Rishi, 2026-10-20): every fact it needs
reaches it through the prompt (the candidate templates, the resolved names, the schema). So the
training questions are skeletons with slots, and the exporter (`trainset.py`) fills each slot
with a random real entity: the only way to be right is to copy values from the question and
the Names block, never to remember them.

The YAML (`version: 1`):

```yaml
templates:
  head_to_head:                       # a template id from config/ask_templates.yaml
    - q: "What's the {team} record against {other} since {first_season}?"
    - q: "How have {team} done against {other} in the playoffs?"
      params: {season_type: POST}     # values the wording decides (not slots)
refuse:
  - {q: "Delete every {team} game from {season}", reason: write}
generate:                             # questions no template answers (the router says generate)
  - {q: "Which teams went for it most on 4th down in {season}?", source: sql}
```

Slots: `{name}` or `{name:hint}`. In a template's skeleton `name` is one of its parameters (its
type decides how the slot is filled; `asof` templates' season / week can't be slots), or a
**pair slot**: `{season}` in a template with `first_season` / `last_season` (and no `season`
parameter) sets both to one season ("the Bears' record in 2021"); `{year}` does the same for
`first_year` / `last_year`; in
`refuse` and `generate` skeletons `name` is a type: `team`, `player`, `coach`, `season`, `week`,
`int`. Hints: a player's position group (`QB`, `RB`, `WR`, `TE`, `K`, `DEF`, `OL`, `DL`, `LB`,
`DB`, or `WR/TE`-style alternatives); a season's `this` ("this season" -> the current season) or
`last` ("last season"); a team's form (`code`, `nickname`, `city`, `full`; default: any).
A template parameter may be a slot or in `params`, not both; required parameters must be one of
the two. `params` values must be valid for the parameter (a listed choice, an int in range).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from nflengine.settings import CONFIG_DIR

PHRASINGS_PATH = CONFIG_DIR / "ask_train_phrasings.yaml"
SLOT = re.compile(r"\{([a-z_]+)(?::([A-Za-z/]+))?\}")
SLOT_TYPES = ("team", "player", "coach", "person", "season", "week", "int")
# a pair slot's name -> the two parameters it sets to one value (AE04)
PAIR_SLOTS = {"season": ("first_season", "last_season"), "year": ("first_year", "last_year")}
POSITION_HINTS = ("QB", "RB", "WR", "TE", "K", "P", "DEF", "OL", "DL", "LB", "DB", "FB")
SEASON_HINTS = ("this", "last")
TEAM_HINTS = ("code", "nickname", "city", "full")
REFUSE_REASONS = ("write", "no_data", "out_of_range", "not_nfl")
GEN_SOURCES = ("sql", "graph", "history")
MAX_Q_CHARS = 300


@dataclass(frozen=True)
class Slot:
    name: str  # the parameter (template skeleton) or the type (refuse / generate)
    type: str
    hint: str | None = None


@dataclass(frozen=True)
class Skeleton:
    id: str  # "<template>#<n>", "refuse#<n>", "generate#<n>": stable across runs
    kind: str  # template | refuse | generate
    q: str
    slots: tuple[Slot, ...]
    template: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None  # refuse
    source: str | None = None  # generate


def _check_hint(where: str, s: Slot) -> None:
    if s.hint is None:
        return
    if s.type in ("player", "person"):
        ok = all(h in POSITION_HINTS for h in s.hint.split("/"))
    elif s.type in ("season", "season_pair"):
        ok = s.hint in SEASON_HINTS
    elif s.type == "team":
        ok = s.hint in TEAM_HINTS
    else:
        ok = False
    if not ok:
        raise ValueError(f"{where}: bad hint {s.hint!r} for a {s.type} slot")


def _slots(where: str, q: str, types: dict[str, str]) -> tuple[Slot, ...]:
    out: list[Slot] = []
    for m in SLOT.finditer(q):
        name, hint = m.group(1), m.group(2)
        if name not in types:
            raise ValueError(f"{where}: unknown slot {{{name}}}")
        s = Slot(name=name, type=types[name], hint=hint)
        _check_hint(where, s)
        out.append(s)
    leftover = SLOT.sub("", q)
    if "{" in leftover or "}" in leftover:
        raise ValueError(f"{where}: a brace that isn't a slot: {q!r}")
    names = [s.name for s in out]
    if len(set(names)) != len(names):
        raise ValueError(f"{where}: a slot appears twice")
    return tuple(out)


def _question(where: str, raw: Any) -> str:
    q = " ".join(str(raw or "").split())
    if not q:
        raise ValueError(f"{where}: empty question")
    if len(q) > MAX_Q_CHARS:
        raise ValueError(f"{where}: longer than {MAX_Q_CHARS} characters")
    return q


def phrasing_texts(path: Path | None = None) -> dict[str, list[str]]:
    """{template id: its skeleton questions}, read without validation (the retriever's extra
    texts at question time; a missing or unreadable file gives {})."""
    path = path or PHRASINGS_PATH
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    out: dict[str, list[str]] = {}
    for tid, entries in (data.get("templates") or {}).items():
        qs = [str(e["q"]) for e in entries or [] if isinstance(e, dict) and e.get("q")]
        out[str(tid)] = qs
    return out


def load_phrasings(
    path: Path | None = None, templates: dict | None = None, current_season: int | None = None
) -> list[Skeleton]:
    """Every skeleton, validated against the template library (bad entries raise ValueError
    naming the entry)."""
    from nflengine.ask.templates import TemplateError, load_templates, normalise

    path = path or PHRASINGS_PATH
    if templates is None:
        templates = load_templates(current_season=current_season)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("version") != 1:
        raise ValueError(f"{path.name}: version must be 1")
    out: list[Skeleton] = []
    seen_q: set[str] = set()

    def unique(where: str, q: str) -> None:
        key = q.lower()
        if key in seen_q:
            raise ValueError(f"{where}: duplicate question {q!r}")
        seen_q.add(key)

    for tid, entries in (data.get("templates") or {}).items():
        t = templates.get(tid)
        if t is None:
            raise ValueError(f"{path.name}: unknown template {tid!r}")
        settable = {
            p.name: p.type for p in t.params if not (t.asof and p.name in ("season", "week"))
        }
        slot_types = {n: ty for n, ty in settable.items() if ty in SLOT_TYPES or ty == "int"}
        for pair, (a, b) in PAIR_SLOTS.items():  # `{season}` sets first_season and last_season
            if pair not in settable and a in settable and b in settable:
                slot_types[pair] = "season_pair" if pair == "season" else "year_pair"
        for i, e in enumerate(entries or []):
            where = f"{path.name}: {tid}[{i}]"
            q = _question(where, (e or {}).get("q"))
            unique(where, q)
            slots = _slots(where, q, slot_types)
            covered = {s.name for s in slots}
            for s in slots:
                if s.type in ("season_pair", "year_pair"):
                    covered |= set(PAIR_SLOTS[s.name])
            params = dict((e or {}).get("params") or {})
            for name, value in params.items():
                p = t.param(name)
                if p is None or name not in settable:
                    raise ValueError(f"{where}: {name!r} isn't a settable parameter of {tid}")
                if name in covered:
                    raise ValueError(f"{where}: {name!r} is both a slot and a fixed value")
                if p.type in ("team", "player", "coach", "person"):
                    raise ValueError(f"{where}: {name!r} names an entity: make it a slot")
                try:
                    normalise(p, value, None)
                except TemplateError as err:
                    raise ValueError(f"{where}: {name}={value!r}: {err.message}") from None
            filled = covered | set(params)
            missing = [p.name for p in t.params if p.required and p.name in settable]
            missing = [n for n in missing if n not in filled]
            if missing:
                raise ValueError(f"{where}: required {missing} is neither a slot nor fixed")
            out.append(
                Skeleton(
                    id=f"{tid}#{i}",
                    kind="template",
                    q=q,
                    slots=slots,
                    template=tid,
                    params=params,
                )
            )

    type_slots = {ty: ty for ty in SLOT_TYPES}
    for i, e in enumerate(data.get("refuse") or []):
        where = f"{path.name}: refuse[{i}]"
        q = _question(where, (e or {}).get("q"))
        unique(where, q)
        reason = str((e or {}).get("reason") or "")
        if reason not in REFUSE_REASONS:
            raise ValueError(f"{where}: reason must be one of {REFUSE_REASONS}")
        out.append(
            Skeleton(
                id=f"refuse#{i}",
                kind="refuse",
                q=q,
                slots=_slots(where, q, type_slots),
                reason=reason,
            )
        )
    for i, e in enumerate(data.get("generate") or []):
        where = f"{path.name}: generate[{i}]"
        q = _question(where, (e or {}).get("q"))
        unique(where, q)
        source = str((e or {}).get("source") or "")
        if source not in GEN_SOURCES:
            raise ValueError(f"{where}: source must be one of {GEN_SOURCES}")
        out.append(
            Skeleton(
                id=f"generate#{i}",
                kind="generate",
                q=q,
                slots=_slots(where, q, type_slots),
                source=source,
            )
        )
    return out
