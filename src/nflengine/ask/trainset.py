"""AE04: the NFL training examples for the local Ask model (`nfl ask train-data`).

The local model is trained on **behaviour, not facts** (Rishi, 2026-10-20; D153). Every
example is a question built from a hand-written skeleton (`config/ask_train_phrasings.yaml`,
`phrasings.py`) with its slots filled by random real players, teams, coaches and seasons, put
to the engine's own prompt code, with the target the engine wants back:

- **route** (the router's JSON): the prompt is `AskEngine._route_prompt` with the question's
  candidate templates (`retrieve.py`, the same k the local model gets at question time). The
  catalogue is never memorised: a template is right only because it's in the list and fits.
  On a share of examples the right template is taken out of the list, and the right answer
  becomes `{"action": "generate", "source": ...}` (the router's "none of these answers the
  whole question"); refusals and no-template questions come from their own skeletons.
- **generate** (one query): the prompt is `AskEngine._generate_prompt` (the schema, filtered
  to the question, and similar tested templates as examples, most often *without* the
  question's own template, so the query is composed, not copied); the target is the
  template's query with its values written in as literals.

Every example is checked against the real data before it's kept: its template renders with its
values (`templates.render`, the names resolved by the engine's own index), the query runs
through the guard (`Sources.run`), and a generated query's literal form returns exactly the
rows of the parameterised one (`evaluate.compare`). Names that would be ambiguous (the engine
would ask "which one?" instead of routing) are re-drawn; a question too close to any golden or
held-out question (rapidfuzz ratio >= 90) is dropped: those sets are never trained on.

Splits: whole skeletons go to val (15%, by a hash of the skeleton id), so a val question's
wording is never seen in training; a few whole templates (`heldout_templates`) are val only
(`unseen_template`: can the model use a template it was never trained on, from its line in
the list alone?). Output (`{NFL_DATA_ROOT}/ask/training/nfl/<version>/`): `train.jsonl`,
`val.jsonl` (the record contract is nfl-ask-trainer's `asktrainer/data/schema.py`; the
fields are pinned by `tests/ask/test_ask_trainset.py`), `manifest.json` (counts, drops by
reason, retrieval recall, prompt sizes, the hashes of everything it was made from).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nflengine.ask.phrasings import PAIR_SLOTS, PHRASINGS_PATH, Skeleton, Slot, load_phrasings
from nflengine.ask.retrieve import TemplateRetriever
from nflengine.ask.templates import Param, Template, TemplateError, render

SCHEMA_VERSION = 1  # the record contract (nfl-ask-trainer `asktrainer/data/schema.py`)
# every record's keys, exactly: nfl-ask-trainer's `Example` has the same fields (a test on
# each side pins them)
RECORD_FIELDS = (
    "id", "origin", "task", "split", "group", "messages", "jepa", "dialect", "verify", "meta",
)  # fmt: skip
SOURCES = ("sql", "graph", "history")
# templates never trained on (val only, group `unseen_template`): two per source, chosen to
# be distinct from their neighbours (D153)
DEFAULT_HELDOUT = (
    "head_to_head",
    "team_season_results",
    "revenge_games_this_week",
    "injury_ripple_this_week",
    "history_coach_colleagues",
    "history_opponents_faced",
)
EXPLAIN = {
    "write": "Ask the Engine only reads the data; it can't change it.",
    "no_data": "That isn't in the data the engine has.",
    "out_of_range": "That's outside the seasons the data covers.",
    "not_nfl": "That isn't a question about NFL data.",
}
POSITION_GROUPS = {
    "QB": {"QB"},
    "RB": {"RB", "FB", "HB"},
    "FB": {"FB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K", "PK"},
    "P": {"P"},
    "OL": {"T", "G", "C", "OT", "OG", "OL", "LT", "RT", "LG", "RG"},
    "DL": {"DE", "DT", "NT", "DL", "EDGE"},
    "LB": {"LB", "ILB", "OLB", "MLB"},
    "DB": {"CB", "S", "SS", "FS", "DB", "SAF"},
}
POSITION_GROUPS["DEF"] = POSITION_GROUPS["DL"] | POSITION_GROUPS["LB"] | POSITION_GROUPS["DB"]
INT_VALUES = {
    "limit": [3, 5, 10, 15, 20, 25],
    "threshold": [50, 75, 100, 125, 150, 200],
    "min_games": [5, 8, 10, 12, 16],
    "min_targets": [50, 75, 100, 150, 200],
}
GENERIC_INTS = [1, 2, 3, 5, 10, 20, 25, 50, 100]
PAIRS = (("first_season", "last_season"), ("first_year", "last_year"))
_PARAM_REF = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


@dataclass
class TrainDataConfig:
    seed: int = 0
    route_fills: int = 2  # filled questions per training skeleton (route examples)
    route_fills_val: int = 1
    gen_fills: int = 1  # generate examples per skeleton (templates with a plain query)
    val_share: float = 0.15
    heldout_templates: tuple[str, ...] = DEFAULT_HELDOUT
    candidates_k: int = 10  # recall@10 98% on new wordings (D153)
    drop_gold_share: float = 0.15  # the right template taken out: the answer is "generate"
    single_source_share: float = 0.10  # "Only this source may be used: ..."
    no_history_share: float = 0.10  # the history graph not offered (it's down)
    gen_exclude_gold_share: float = 0.8  # the generator's examples leave out its template
    golden_ratio: float = 90.0
    min_player_games: int = 16
    min_season: int = 2012  # entities and seasons drawn from here on (play-by-play era)
    redraws: int = 6  # tries to fill a skeleton without an ambiguous name
    nonempty_tries: int = 4  # fills tried until a template's query returns rows
    limit_skeletons: int | None = None  # a quick smoke: the first N skeletons only
    phrasings_path: Path | None = None  # default config/ask_train_phrasings.yaml


# guard refusals that say a source isn't there (not that the query is wrong): they stop the
# export (a run that started while Neo4j was still replaying its logs dropped every graph
# template's examples, 2026-10-20)
SOURCE_STOPS = {
    "refused.source.down",
    "refused.source.busy",
    "refused.source.history_down",
    "refused.source.history_busy",
}


def _why(e: Exception) -> str:
    """A drop reason without any database text: the guard's rule, a failure's kind and code."""
    rule = getattr(e, "rule", None)
    if rule:
        return f"refused.{rule}"
    kind, code = getattr(e, "kind", None), getattr(e, "code", None)
    if kind or code:
        return f"failed.{kind}.{code}"
    return type(e).__name__


def _bucket(text: str, seed: int) -> float:
    h = hashlib.sha1(f"{seed}:{text}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def file_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:12]
    except OSError:
        return None


# ---- filling slots --------------------------------------------------------------------------


class SlotFiller:
    """Draws a random real value for a slot: (the text written into the question, the value
    the router should give back). Teams: code / nickname / city / full name; players: full
    names (sometimes lower case, as people type), by position group; coaches; seasons (or
    "this season" / "last season"); ints from realistic values."""

    def __init__(self, names, current: int, cfg: TrainDataConfig):
        from nflengine.digest.names import TEAMS

        self.names = names
        self.current = current
        self.cfg = cfg
        self.teams = sorted(TEAMS.items())
        players = [
            p
            for p in names.players
            if p.games >= cfg.min_player_games
            and (p.last or 0) >= cfg.min_season
            and p.name
            and re.fullmatch(r"[A-Za-z.'\- ]+", p.name)
        ]
        self.players = players
        self.coaches = [
            c
            for c in names.coaches
            if (c.last or 0) >= cfg.min_season and re.fullmatch(r"[A-Za-z.'\- ]+", c.name or "")
        ]
        self._unique: dict[tuple[str, str], bool] = {}

    def _is_unique(self, kind: str, name: str) -> bool:
        key = (kind, name)
        if key not in self._unique:
            res = self.names.player(name) if kind == "player" else self.names.coach(name)
            self._unique[key] = res.status == "unique"
        return self._unique[key]

    def team(self, rng: random.Random, hint: str | None, before: str, used: set) -> tuple:
        choices = [t for t in self.teams if t[0] not in used] or self.teams
        code, (loc, nick) = rng.choice(choices)
        used.add(code)
        after_the = before.rstrip().lower().endswith("the")
        forms = {
            "code": code,
            "nickname": nick,
            "city": loc,
            "full": f"{loc} {nick}",
        }
        if hint in forms:
            form = hint
        elif after_the:
            form = rng.choice(["nickname", "nickname", "full"])
        else:
            form = rng.choice(["nickname", "nickname", "city", "full", "code"])
        if form == "city" and loc.lower() in ("new york", "los angeles"):
            form = "nickname"
        text = forms[form]
        return text, text

    def player(self, rng: random.Random, hint: str | None, used: set) -> tuple:
        pool = self.players
        if hint:
            groups = set().union(*(POSITION_GROUPS.get(h, {h}) for h in hint.split("/")))
            pool = [p for p in pool if (p.position or "").upper() in groups] or self.players
        for _ in range(40):
            p = rng.choice(pool)
            if p.gsis_id in used or not self._is_unique("player", p.name):
                continue
            used.add(p.gsis_id)
            r = rng.random()
            text = p.name.lower() if r < 0.10 else p.name.replace(".", "") if r < 0.15 else p.name
            return text, text
        raise LookupError("no unique player for this slot")

    def coach(self, rng: random.Random, used: set) -> tuple:
        for _ in range(40):
            c = rng.choice(self.coaches)
            if c.name in used or not self._is_unique("coach", c.name):
                continue
            used.add(c.name)
            return c.name, c.name
        raise LookupError("no unique coach for this slot")

    def season(self, rng: random.Random, hint: str | None, p: Param | None) -> tuple:
        if hint == "this":
            return "this season", self.current
        if hint == "last":
            return "last season", self.current - 1
        lo = int(p.min) if p is not None and p.min is not None else self.cfg.min_season
        hi = int(p.max) if p is not None and p.max is not None else self.current
        lo = max(lo, self.cfg.min_season)
        v = rng.randint(lo, max(lo, hi))
        return str(v), v

    def integer(self, rng: random.Random, p: Param | None, name: str) -> tuple:
        lo = int(p.min) if p is not None and p.min is not None else 1
        hi = int(p.max) if p is not None and p.max is not None else 1000
        if name in ("first_year", "last_year"):
            v = rng.randint(max(lo, 2000), hi)
            return str(v), v
        values = [v for v in INT_VALUES.get(name, GENERIC_INTS) if lo <= v <= hi]
        v = rng.choice(values) if values else rng.randint(lo, hi)
        return str(v), v

    def fill(
        self,
        sk: Skeleton,
        t: Template | None,
        rng: random.Random,
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        """(the question, the slot values for the router's params, {slot: its text})."""
        used_teams: set[str] = set()
        used_people: set[str] = set()
        values: dict[str, Any] = {}
        texts: dict[str, str] = {}
        pos = 0
        for m in re.finditer(r"\{([a-z_]+)(?::[A-Za-z/]+)?\}", sk.q):
            slot = next(s for s in sk.slots if s.name == m.group(1))
            before = sk.q[pos : m.start()]
            text, value = self._one(slot, t, rng, before, used_teams, used_people)
            texts[slot.name], values[slot.name] = text, value
            pos = m.end()
        for a, b in PAIRS:  # a pair of seasons in order
            if not (a in values and b in values and values[a] > values[b]):
                continue
            if texts[a].isdigit() and texts[b].isdigit():
                values[a], values[b] = values[b], values[a]
                texts[a], texts[b] = texts[b], texts[a]
            elif texts[a].isdigit():  # "from 2025 through last season": move the start back
                values[a] = rng.randint(self.cfg.min_season, values[b])
                texts[a] = str(values[a])
        question = re.sub(r"\{([a-z_]+)(?::[A-Za-z/]+)?\}", lambda m: texts[m.group(1)], sk.q)
        for s in sk.slots:  # a pair slot ("in 2021") sets both ends of the template's range
            if s.type in ("season_pair", "year_pair"):
                v = values.pop(s.name)
                for name in PAIR_SLOTS[s.name]:
                    values[name] = v
        return " ".join(question.split()), values, texts

    def _one(self, slot: Slot, t, rng, before, used_teams, used_people) -> tuple:
        p = t.param(slot.name) if t is not None else None
        if slot.type == "team":
            return self.team(rng, slot.hint, before, used_teams)
        if slot.type == "player":
            return self.player(rng, slot.hint, used_people)
        if slot.type == "coach":
            return self.coach(rng, used_people)
        if slot.type == "person":
            if rng.random() < 0.7:
                return self.player(rng, slot.hint, used_people)
            return self.coach(rng, used_people)
        if slot.type == "season":
            return self.season(rng, slot.hint, p)
        if slot.type == "season_pair":  # bounded by the range's own parameter
            return self.season(rng, slot.hint, t.param(PAIR_SLOTS["season"][0]) if t else None)
        if slot.type == "year_pair":
            return self.integer(rng, t.param(PAIR_SLOTS["year"][0]) if t else None, "first_year")
        if slot.type == "week":
            v = rng.randint(1, 18)
            return str(v), v
        return self.integer(rng, p, slot.name)


# ---- literal queries (the generator's targets) ----------------------------------------------


def literal(value: Any, source: str) -> str:
    """A bound value written into a query as a literal (SQL or Cypher)."""
    sql = source == "sql"
    if value is None:
        return "NULL" if sql else "null"
    if isinstance(value, bool):
        return ("TRUE" if value else "FALSE") if sql else ("true" if value else "false")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(literal(v, source) for v in value) + "]"
    text = str(value)
    if "\\" in text:
        raise ValueError("a value with a backslash can't be written as a literal here")
    if sql:
        return "'" + text.replace("'", "''") + "'"
    if "'" in text and '"' not in text:
        return '"' + text + '"'
    if "'" in text:
        raise ValueError("a value with both quote kinds can't be a Cypher literal here")
    return "'" + text + "'"


def literalize(query: str, params: dict[str, Any], source: str) -> str:
    """The query with each `$name` replaced by its value's literal (every `$name` must be a
    bound parameter)."""

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in params:
            raise KeyError(f"${name} has no value")
        return literal(params[name], source)

    return _PARAM_REF.sub(sub, query).strip()


# ---- the exporter ---------------------------------------------------------------------------


@dataclass
class Built:
    train: list[dict[str, Any]] = field(default_factory=list)
    val: list[dict[str, Any]] = field(default_factory=list)
    drops: Counter = field(default_factory=Counter)
    recall: Counter = field(default_factory=Counter)  # (split, hit|miss)


class Exporter:
    def __init__(self, engine, cfg: TrainDataConfig | None = None, log=print):
        self.engine = engine
        self.cfg = cfg or TrainDataConfig()
        self.log = log
        self.templates: dict[str, Template] = engine.templates
        self.skeletons = load_phrasings(self.cfg.phrasings_path, templates=self.templates)
        if self.cfg.limit_skeletons:
            self.skeletons = self.skeletons[: self.cfg.limit_skeletons]
        unknown = [t for t in self.cfg.heldout_templates if t not in self.templates]
        if unknown:
            raise ValueError(f"unknown held-out templates: {unknown}")
        self.filler = SlotFiller(engine.names, engine.current, self.cfg)
        self.history_on = bool(engine._history_ready())
        self.split = {sk.id: self._split_of(sk) for sk in self.skeletons}
        # the retriever sees only training skeletons: a val question's own wording must never
        # help find its template (it isn't in the index at question time either: D153)
        train_texts: dict[str, list[str]] = {}
        for sk in self.skeletons:
            if sk.kind == "template" and self.split[sk.id] == "train":
                train_texts.setdefault(sk.template, []).append(sk.q)
        self.retriever = TemplateRetriever(self.templates, train_texts, engine.names)
        self._golden = self._golden_questions()

    # ---- bookkeeping ----

    def _split_of(self, sk: Skeleton) -> str:
        if sk.kind == "template" and sk.template in self.cfg.heldout_templates:
            return "val"
        return "val" if _bucket(sk.id, self.cfg.seed) < self.cfg.val_share else "train"

    def _group(self, sk: Skeleton, split: str) -> str:
        if split == "train":
            return "train"
        if sk.kind == "refuse":
            return "refuse"
        if sk.kind == "generate":
            return "no_template"
        return "unseen_template" if sk.template in self.cfg.heldout_templates else "seen"

    def _golden_questions(self) -> list[str]:
        """The golden and held-out questions' text, only to keep training questions away from
        them (never written anywhere)."""
        from nflengine.ask.evaluate import GOLDEN_PATH, HOLDOUT_PATH
        from nflengine.ask.names import norm

        out = []
        for path in (GOLDEN_PATH, HOLDOUT_PATH):
            if path.exists():
                import yaml

                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                out += [norm(q.get("question") or "") for q in data.get("questions") or []]
        return [q for q in out if q]

    def _near_golden(self, question: str) -> bool:
        from rapidfuzz import fuzz, process

        from nflengine.ask.names import norm

        if not self._golden:
            return False
        hit = process.extractOne(
            norm(question), self._golden, scorer=fuzz.ratio, score_cutoff=self.cfg.golden_ratio
        )
        return hit is not None

    # ---- one example ----

    def _allowed(self, rng: random.Random, gold_source: str | None) -> list[str]:
        on = ["sql", "graph"] + (["history"] if self.history_on else [])
        r = rng.random()
        if gold_source and r < self.cfg.single_source_share:
            return [gold_source]
        if (
            self.history_on
            and gold_source != "history"
            and r < (self.cfg.single_source_share + self.cfg.no_history_share)
        ):
            return ["sql", "graph"]
        return on

    def _usable(self, allowed: list[str]) -> list[str]:
        return [
            t.id
            for t in self.templates.values()
            if t.source in allowed and not (t.asof and not self.engine.asof)
        ]

    def _candidates(
        self,
        question: str,
        mentions,
        allowed: list[str],
        gold: str | None,
        drop_gold: bool,
        split: str,
        built: Built,
    ) -> list[str]:
        k = self.cfg.candidates_k
        usable = self._usable(allowed)
        if gold is None:
            return self.retriever.top(question, k, usable, mentions)
        ranked = self.retriever.top(question, len(usable), usable, mentions)  # all, in order
        sc = self.retriever.scores(question, mentions)
        by_score = sorted(usable, key=lambda tid: (-sc[tid], self.retriever.order[tid]))
        top = by_score[:k]
        built.recall[(split, "hit" if gold in top else "miss")] += 1
        if drop_gold:
            top = [tid for tid in by_score if tid != gold][:k]
        elif gold not in top:
            top = [*top[: k - 1], gold]
        keep = set(top)
        return [tid for tid in ranked if tid in keep]

    def _example(
        self, ex_id: str, task: str, split: str, group: str, system: str, user: str,
        target: dict[str, Any] | str, jepa_code: str, question: str, dialect: str | None,
        verify: dict[str, Any], meta: dict[str, Any],
    ) -> dict[str, Any]:  # fmt: skip
        target_text = target if isinstance(target, str) else json.dumps(target, ensure_ascii=False)
        return {
            "id": ex_id,
            "origin": "nfl",
            "task": task,
            "split": split,
            "group": group,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
                {"role": "assistant", "content": target_text},
            ],
            "jepa": {"text": question, "code": jepa_code},
            "dialect": dialect,
            "verify": verify,
            "meta": meta,
        }

    def _route_target(self, t: Template, sk: Skeleton, values: dict[str, Any]) -> dict:
        params: dict[str, Any] = {}
        for p in t.params:  # the template's order
            if p.name in values:
                params[p.name] = values[p.name]
            elif p.name in sk.params:
                params[p.name] = sk.params[p.name]
        return {"action": "template", "template": t.id, "params": params}

    def _run_template(self, t: Template, params: dict[str, Any]):
        r = render(t, params, self.engine.names, asof=self.engine.asof)
        out = self.engine.sources.run(t.source, r.query, r.params, allow_maps=bool(t.library))
        rows = [row for row in out.rows if r.keep(out.columns, row)]
        return r, out, rows

    def _template_examples(self, sk: Skeleton, built: Built) -> None:
        t = self.templates[sk.template]
        split = self.split[sk.id]
        group = self._group(sk, split)
        fills = self.cfg.route_fills if split == "train" else self.cfg.route_fills_val
        plain = not t.library and not t.filters
        gen_fills = self.cfg.gen_fills if plain else 0
        for i in range(max(fills, gen_fills)):
            got = self._nonempty_fill(sk, t, i, built)
            if got is None:
                continue
            rng, question, values, mentions, target, r, out, rows = got
            meta = {
                "template": t.id,
                "source": t.source,
                "skeleton": sk.id,
                "fill": i,
                "values": {k: v for k, v in r.values.items()},
                "rows": len(rows),
            }
            if i < fills:
                self._add_route(sk, t, question, mentions, target, split, group, rng, meta, built)
            if i < gen_fills and (rows or split == "train"):
                # a val query with an empty gold result can't tell a right query from a wrong
                # one (any query returning nothing would match): kept out of val
                self._add_generate(sk, t, question, mentions, r, rows, out, split, group, rng,
                                   meta, built)  # fmt: skip
            elif i < gen_fills:
                built.drops["val_generate.empty_gold"] += 1

    def _nonempty_fill(self, sk: Skeleton, t: Template, i: int, built: Built):
        """A filled, rendered and run example, redrawn (up to `nonempty_tries`) until its
        query returns rows: random entities often meet nothing (two random players were rarely
        teammates), and an empty gold result is a weak check. The last try is kept if every
        one is empty (Ask must handle empty answers too)."""
        last = None
        for attempt in range(max(1, self.cfg.nonempty_tries)):
            seed = f"{self.cfg.seed}:{sk.id}:{i}" + (f":{attempt}" if attempt else "")
            rng = random.Random(seed)
            filled = self._fill_clean(sk, t, rng, built)
            if filled is None:
                return last
            question, values, mentions = filled
            target = self._route_target(t, sk, values)
            try:
                r, out, rows = self._run_template(t, target["params"])
            except TemplateError as e:
                built.drops[f"render.{e.code}"] += 1
                continue
            except Exception as e:  # a guard refusal or a failed query: the data says no
                why = _why(e)
                if why in SOURCE_STOPS:  # a graph down or the weekly lock: never a quiet drop
                    raise RuntimeError(f"{why} mid-export: start it / wait, then re-run") from None
                built.drops[f"run.{why}"] += 1
                continue
            last = (rng, question, values, mentions, target, r, out, rows)
            if rows:
                return last
            built.drops["fill.empty_redrawn"] += 1
        return last

    def _fill_clean(self, sk: Skeleton, t: Template | None, rng: random.Random, built: Built):
        from nflengine.ask.names import ambiguous

        for _ in range(self.cfg.redraws):
            try:
                question, values, _texts = self.filler.fill(sk, t, rng)
            except LookupError:
                built.drops["fill.no_entity"] += 1
                return None
            mentions = self.engine.names.mentions(question)
            if ambiguous(mentions):
                built.drops["fill.ambiguous_redrawn"] += 1
                continue
            if len(question) > self.engine.cfg.limits.question_chars:
                built.drops["fill.too_long"] += 1
                return None
            if self._near_golden(question):
                built.drops["golden_near_duplicate"] += 1
                return None
            return question, values, mentions
        built.drops["fill.ambiguous"] += 1
        return None

    def _add_route(self, sk, t, question, mentions, target, split, group, rng, meta, built):
        drop_gold = rng.random() < self.cfg.drop_gold_share
        allowed = self._allowed(rng, t.source)
        if t.source not in allowed:
            allowed = [t.source]
        cands = self._candidates(question, mentions, allowed, t.id, drop_gold, split, built)
        gold = {"action": "generate", "source": t.source} if drop_gold else target
        system, user = self.engine._route_prompt(question, mentions, allowed, cands)
        n = len(built.train) + len(built.val)
        ex = self._example(
            f"nfl-route-{n:06d}", "route", split, group, system, user, gold,
            json.dumps(gold, ensure_ascii=False), question, None,
            {"kind": "route", "gold": gold, "allowed": allowed, "candidates": cands},
            {**meta, "gold_dropped": drop_gold, "allowed": allowed},
        )  # fmt: skip
        (built.train if split == "train" else built.val).append(ex)

    def _add_generate(self, sk, t, question, mentions, r, rows, out, split, group, rng, meta,
                      built):  # fmt: skip
        from nflengine.ask.evaluate import compare

        try:
            query = literalize(r.query, r.params, t.source)
        except (KeyError, ValueError) as e:
            built.drops[f"literal.{type(e).__name__}"] += 1
            return
        try:
            lit = self.engine.sources.run(t.source, query, {}, allow_maps=False)
        except Exception as e:  # the literal form must pass the generator's own guard
            built.drops[f"literal.run.{_why(e)}"] += 1
            return
        ok, why = compare(lit.columns, lit.rows, out.columns, rows)
        if not ok:
            built.drops["literal.differs"] += 1
            return
        exclude = (
            frozenset({t.id}) if rng.random() < self.cfg.gen_exclude_gold_share else frozenset()
        )
        system, user = self.engine._generate_prompt(question, t.source, mentions, exclude=exclude)
        target = {"query": query}
        n = len(built.train) + len(built.val)
        ex = self._example(
            f"nfl-gen-{n:06d}", "generate", split, group, system, user, target, query, question,
            "duckdb" if t.source == "sql" else "cypher",
            {"kind": "query", "source": t.source, "gold": query},
            {**meta, "own_template_in_examples": not exclude},
        )  # fmt: skip
        (built.train if split == "train" else built.val).append(ex)

    def _other_examples(self, sk: Skeleton, built: Built) -> None:
        """Refusals and no-template questions: route examples only."""
        split = self.split[sk.id]
        group = self._group(sk, split)
        fills = self.cfg.route_fills if split == "train" else self.cfg.route_fills_val
        for i in range(fills):
            rng = random.Random(f"{self.cfg.seed}:{sk.id}:{i}")
            filled = self._fill_clean(sk, None, rng, built)
            if filled is None:
                continue
            question, values, mentions = filled
            if sk.kind == "refuse":
                gold = {"action": "refuse", "reason": sk.reason, "explain": EXPLAIN[sk.reason]}
                allowed = self._allowed(rng, None)
            else:
                if sk.source == "history" and not self.history_on:
                    built.drops["history_off"] += 1
                    continue
                gold = {"action": "generate", "source": sk.source}
                allowed = self._allowed(rng, sk.source)
                if sk.source not in allowed:
                    allowed = [sk.source]
            cands = self._candidates(question, mentions, allowed, None, False, split, built)
            system, user = self.engine._route_prompt(question, mentions, allowed, cands)
            n = len(built.train) + len(built.val)
            meta = {"skeleton": sk.id, "fill": i, "values": values, "allowed": allowed}
            ex = self._example(
                f"nfl-route-{n:06d}", "route", split, group, system, user, gold,
                json.dumps(gold, ensure_ascii=False), question, None,
                {"kind": "route", "gold": gold, "allowed": allowed, "candidates": cands}, meta,
            )  # fmt: skip
            (built.train if split == "train" else built.val).append(ex)

    def build(self) -> Built:
        built = Built()
        for i, sk in enumerate(self.skeletons, start=1):
            if sk.kind == "template":
                self._template_examples(sk, built)
            else:
                self._other_examples(sk, built)
            if i % 50 == 0:
                self.log(
                    f"{i}/{len(self.skeletons)} skeletons: {len(built.train)} train, "
                    f"{len(built.val)} val, drops {sum(built.drops.values())}"
                )
        return built


def _percentiles(values: list[int]) -> dict[str, int]:
    if not values:
        return {}
    v = sorted(values)
    pick = lambda q: v[min(len(v) - 1, int(q * (len(v) - 1)))]  # noqa: E731
    return {"p50": pick(0.5), "p90": pick(0.9), "p99": pick(0.99), "max": v[-1]}


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    from nflengine.fsutil import replace_file

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    replace_file(tmp, path)


def export(
    cfg: TrainDataConfig | None = None,
    out_root: Path | None = None,
    engine=None,
    log=print,
) -> dict[str, Any]:
    """Build the NFL examples and write them under `ask/training/nfl/<version>/`. Returns the
    manifest (also written as `manifest.json`)."""
    from nflengine.ask.evaluate import GOLDEN_PATH, HOLDOUT_PATH
    from nflengine.ask.route import AskEngine, prompt_hash
    from nflengine.ask.templates import TEMPLATES_PATH
    from nflengine.paths import ensure_data_root

    cfg = cfg or TrainDataConfig()
    own_engine = engine is None
    if engine is None:
        engine = AskEngine.build(NoLLM())
    try:
        if engine.sources.busy():
            raise RuntimeError("the weekly lock is held: run train-data after the weekly run")
        _check_sources(engine)
        t0 = dt.datetime.now(dt.UTC)
        built = Exporter(engine, cfg, log).build()
    finally:
        if own_engine:
            engine.close()
    version = t0.strftime("%Y%m%dT%H%M%SZ") + f"_s{cfg.seed}"
    root = out_root or ensure_data_root().ask / "training" / "nfl"
    out = root / version
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "train.jsonl", built.train)
    write_jsonl(out / "val.jsonl", built.val)
    every = built.train + built.val
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "made_at": t0.isoformat(),
        "config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in vars(cfg).items()},
        "counts": {
            "train": len(built.train),
            "val": len(built.val),
            "by_split_task_group": dict(
                Counter(f"{r['split']}/{r['task']}/{r['group']}" for r in every)
            ),
            "templates_trained": len(
                {r["meta"].get("template") for r in built.train if r["meta"].get("template")}
            ),
        },
        "drops": dict(built.drops),
        "retrieval_recall": {
            s: (
                built.recall[(s, "hit")]
                / max(1, built.recall[(s, "hit")] + built.recall[(s, "miss")])
            )
            for s in ("train", "val")
        },
        "prompt_chars": {
            task: _percentiles(
                [
                    sum(len(m["content"]) for m in r["messages"][:-1])
                    for r in every
                    if r["task"] == task
                ]
            )
            for task in ("route", "generate")
        },
        "target_chars": {
            task: _percentiles(
                [len(r["messages"][-1]["content"]) for r in every if r["task"] == task]
            )
            for task in ("route", "generate")
        },
        "context": engine.context(),
        "hashes": {
            "templates": file_hash(TEMPLATES_PATH),
            "phrasings": file_hash(PHRASINGS_PATH),
            "golden": file_hash(GOLDEN_PATH),
            "holdout": file_hash(HOLDOUT_PATH),
            "prompt": prompt_hash(),
        },
        "history_on": bool(any(r["meta"].get("source") == "history" for r in every)),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str), "utf-8")
    (root / "LATEST").write_text(version + "\n", encoding="utf-8")
    manifest["dir"] = str(out)
    return manifest


def _check_sources(engine) -> None:
    """Both graphs must answer before an export starts (every template is exported)."""
    up = getattr(engine.sources, "graph_up", None)  # the app's cached check, else a probe
    if up is None:
        from nflengine.ops.services import neo4j_up as up
    if not up():
        raise RuntimeError("the weekly graph (Neo4j) isn't answering: start it, then re-run")
    if not engine.asof:
        raise RuntimeError("no weekly graph build found: the 'this week' templates need one")
    if engine.sources.enabled.get("history", False):
        if not engine._history_ready():
            raise RuntimeError("the history graph isn't built or has no driver")
        hup = getattr(engine.sources, "history_up", None)
        if hup is None:
            from nflengine.history_graph.client import history_up as hup
        if not hup():
            raise RuntimeError(
                "the history graph isn't answering: `uv run nfl history-graph start`"
            )


class NoLLM:
    """An engine that never calls a model (the exporter and the verifier build prompts and run
    queries only)."""

    effort = None
    calls: list = []

    def chat_json(self, *a, **k):  # pragma: no cover - a bug if reached
        raise RuntimeError("the training-data tools never call a model")

    def take_calls(self) -> list:
        return []
