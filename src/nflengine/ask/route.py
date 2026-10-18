"""From a question to rows and an answer (AE00; README §4, the phase file's route.py task).

`AskEngine.ask(question, source="auto")`:
1. **Checks in code, no LLM:** the length cap (500 characters), a request to change data
   ("delete ...", "drop ...", "update ...": refused), and the names (`names.py`): an ambiguous
   player, coach or team returns the choices (`clarify`), never a guess.
2. **The router call:** the LLM sees the template list, the resolved names and the data's
   dates, and picks a template and its values, or "generate" (with a source), or "refuse".
3. **Generation** (no template fits): the schema of the chosen source, filtered to the
   question's words, and the 3-5 most similar tested templates as examples; one query back.
4. **Guard and run** (`guard.Sources.run`): read-only, 10 s, 500 rows.
5. **Corrections:** on a refusal by the guard, a database error, a template whose values don't
   fit, or an empty result, up to `ask.limits.corrections` (2) more rounds with the problem
   stated (a template problem goes back to the router; a query problem to the generator, which
   may also accept an empty result as the answer).
6. **The answer** (`answer.py`): 2-3 sentences with every number checked against the rows; an
   empty result gets a fixed sentence without a call.

Everything is recorded in `AskResult` (route, template, query, rows, retries, every LLM call
with its cost, timings). Database error text goes only into a correction prompt, scrubbed
(`guard.QueryFailed.hint`); the result keeps the error's type and code.

AE01 (the control room's page, D142): `ask(..., picks=)` answers a "which one?" card (the
choice must be one the engine offered, or a value that resolves to exactly one entity); a
graph that doesn't answer (`source.down`) stops the question at once with a fixed sentence,
like the weekly lock; `refresh(paths)` re-reads today's date, the graph's week, the curated
dates and the names for a process that holds the engine for days.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from nflengine.ask import answer as answer_mod
from nflengine.ask.config import AskConfig, ask_config
from nflengine.ask.guard import QueryFailed, QueryResult, Refused, Sources
from nflengine.ask.llm import AskLLM, AskLLMError, OutOfCredits
from nflengine.ask.names import Match, Mention, NameIndex, Resolution, ambiguous, norm
from nflengine.ask.schema import graph_schema, sql_schema
from nflengine.ask.templates import Rendered, Template, TemplateError, load_templates, render

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
REFUSE_REASONS = ("write", "no_data", "out_of_range", "not_nfl")

# a request to change data, at the start of the question ("please delete ...", "can you drop")
_WRITE_INTENT = re.compile(
    r"^\s*(?:(?:please|pls|can you|could you|would you|will you|go ahead and|now|i want you to)"
    r"[\s,]+)*(?:delete|drop|truncate|insert|alter|erase|wipe|modify|overwrite|rename|remove"
    r"|update(?!\s+me\b))\b(?!\s+(?:rate|rates|percentage|pct|pass(?:es)?)\b)",
    re.IGNORECASE,
)

FIXED = {
    "too_long": "The question is too long: keep it under {n} characters.",
    "empty": "Ask a question about NFL data.",
    "write": "Ask the Engine only reads the data: it can't change, add or delete anything.",
    "no_data": "That isn't in the data the engine has.",
    "out_of_range": "That's outside the seasons the data covers.",
    "not_nfl": "Ask the Engine answers questions about NFL data only.",
    "clarify": 'More than one {kind} matches "{text}": which one?',
    "empty_result": "No rows matched.",
    "failed": "The query still failed after {n} corrections ({code}).",
    "llm": "The model didn't give a usable reply: {error}.",
    "source_off": "That source is switched off in settings.yaml.",
    "busy": "The weekly data refresh is running: ask again when it's done.",
    "graph_down": "The weekly graph (Neo4j) isn't answering right now, and this question needs "
    "it. Questions about the tables still work.",
}
# guard rules that stop a question at once, whatever round it's in: the data is being rebuilt
# (the weekly lock) or the graph doesn't answer (Neo4j down); neither is the query's fault
_STOP_RULES = {"source.busy": "busy", "source.down": "graph_down"}
PICK_KINDS = ("team", "player", "coach")


@dataclass
class AskResult:
    question: str
    status: str = "error"  # answered | empty | refused | clarify | error
    route: str = "none"  # template | generated | refused | clarify | none
    template_id: str | None = None
    source: str | None = None
    query: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    retries: int = 0  # correction rounds used
    refusal: dict[str, str] | None = None  # {"by": code|router|generator|guard, "rule", "message"}
    choices: list[dict[str, Any]] = field(default_factory=list)  # clarify
    answer: str | None = None
    answer_check: dict[str, Any] | None = None
    answer_from_llm: bool = False
    mentions: list[str] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)  # each try: what and outcome
    calls: list[dict[str, Any]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    error: str | None = None  # fixed text
    error_code: str | None = None  # llm | query_failed (AE01: the page's error card)
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def cost(self) -> float | None:
        costs = [c["cost"] for c in self.calls if c.get("cost") is not None]
        return round(sum(costs), 8) if costs else None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["cost"] = self.cost
        d["row_count"] = self.row_count
        return d


def prompt_hash() -> str:
    """A short hash of what the models are told: the three prompt files and the schema text
    with its conventions (`schema.py`), logged with every eval run so runs made with other
    instructions are never compared (the 2026-10-18 audit's fixes were mostly schema notes)."""
    from nflengine.ask import schema

    h = hashlib.sha256()
    for name in ("route.md", "generate.md", "answer.md"):
        h.update((PROMPT_DIR / name).read_bytes().replace(b"\r\n", b"\n"))
    notes = [schema.SQL_RULES, schema.GRAPH_SCHEMA, json.dumps(schema.TABLE_NOTES, sort_keys=True)]
    h.update("\n".join(notes).encode("utf-8"))
    return h.hexdigest()[:12]


def _fill(text: str, **values: str) -> str:
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def graph_asof(paths, season: int) -> tuple[int, int] | None:
    """The weekly graph's (season, week): the newest live `graph_results.json` with status ok
    (the graph is rebuilt each Tuesday; there's no build node in the graph itself)."""
    best: tuple[int, int] | None = None
    for f in sorted((paths.runs / str(season)).glob("week*/graph_results.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("status") == "ok" and d.get("mode") == "live":
            best = (int(d["season"]), int(d["week"]))
    return best


def curated_through(paths, season: int) -> tuple[str | None, int | None]:
    """(when the curated tables were last rebuilt, the newest week of `season` with a
    completed game)."""
    run_at = None
    q = paths.curated / "_quality" / "latest.json"
    if q.exists():
        try:
            run_at = json.loads(q.read_text(encoding="utf-8")).get("run_at")
        except (OSError, ValueError):
            run_at = None
    week = None
    games = paths.curated / "games.parquet"
    if games.exists():
        import polars as pl

        df = pl.read_parquet(
            games, columns=["season", "week", "completed"], memory_map=False
        ).filter((pl.col("season") == season) & pl.col("completed").fill_null(False))
        week = int(df["week"].max()) if df.height else None
    return run_at, week


class AskEngine:
    """Everything a question needs, built once (CLI, eval run, later the app)."""

    def __init__(
        self,
        llm: AskLLM,
        sources: Sources,
        names: NameIndex,
        templates: dict[str, Template],
        cfg: AskConfig | None = None,
        *,
        current_season: int,
        asof: tuple[int, int] | None,
        curated: tuple[str | None, int | None] = (None, None),
        today: dt.date | None = None,
    ):
        self.llm = llm
        self.sources = sources
        self.names = names
        self.templates = templates
        self.cfg = cfg or ask_config()
        self.current = current_season
        self.asof = asof
        self.curated = curated
        self.today = today or dt.date.today()
        # the last question's model calls, also when it raised (OutOfCredits): the app's error
        # card shows what was spent before the failure (AE01, the test worker)
        self.last_calls: list[dict[str, Any]] = []

    @classmethod
    def build(cls, llm: AskLLM, cfg: AskConfig | None = None, paths=None) -> AskEngine:
        from nflengine.paths import ensure_data_root
        from nflengine.settings import get_config

        cfg = cfg or ask_config()
        paths = paths or ensure_data_root(create_dirs=False)
        season = get_config().seasons.current
        return cls(
            llm,
            Sources.open(cfg, paths),
            NameIndex.load(paths.curated, season),
            load_templates(current_season=season),
            cfg,
            current_season=season,
            asof=graph_asof(paths, season),
            curated=curated_through(paths, season),
        )

    def close(self) -> None:
        self.sources.close()

    def refresh(self, paths) -> None:
        """Re-read what changes while a process holds the engine (the control room runs for
        days): today's date, the weekly graph's week, the curated tables' dates, and the names
        (`NameIndex.load` is cached by the files' modification times, so an unchanged week
        costs nothing). Called before each question by the app; never by the CLI or an eval."""
        self.today = dt.date.today()
        self.asof = graph_asof(paths, self.current)
        self.curated = curated_through(paths, self.current)
        self.names = NameIndex.load(paths.curated, self.current)

    # ---- context ------------------------------------------------------------------------------

    def context(self) -> dict[str, Any]:
        run_at, week = self.curated
        return {
            "graph_asof": {"season": self.asof[0], "week": self.asof[1]} if self.asof else None,
            "curated_run_at": run_at,
            "curated_week": week,
            "current_season": self.current,
            "today": self.today.isoformat(),
        }

    def _graph_text(self) -> str:
        if not self.asof:
            return "not available (no weekly graph build found)"
        return f"as of {self.asof[0]} week {self.asof[1]} (results through week {self.asof[1] - 1})"

    def _curated_text(self) -> str:
        run_at, week = self.curated
        bits = []
        if week:
            bits.append(f"{self.current} games through week {week}")
        if run_at:
            bits.append(f"rebuilt {str(run_at)[:10]}")
        return "Curated data: " + ", ".join(bits) if bits else "Curated data"

    def _allowed(self, source: str) -> list[str]:
        on = [s for s in ("sql", "graph") if self.sources.enabled.get(s, True)]
        if source in ("sql", "graph"):
            return [s for s in on if s == source]
        if self.sources.driver is None or not self.asof:
            on = [s for s in on if s != "graph"]
        if self.sources.sandbox is None:
            on = [s for s in on if s != "sql"]
        return on

    # ---- prompts ------------------------------------------------------------------------------

    def _route_prompt(
        self, question: str, mentions: list[Mention], allowed: list[str]
    ) -> tuple[str, str]:
        temps = [t for t in self.templates.values() if t.source in allowed]
        if "graph" in allowed and not self.asof:
            temps = [t for t in temps if not t.asof]
        rule = f"Only this source may be used: {allowed[0]}.\n" if len(allowed) == 1 else ""
        system = _fill(
            (PROMPT_DIR / "route.md").read_text(encoding="utf-8"),
            curated=self._curated_text(),
            graph=self._graph_text(),
            source_rule=rule,
            templates="\n".join(t.prompt_line() for t in temps) or "(none)",
            current=str(self.current),
            previous=str(self.current - 1),
            today=self.today.isoformat(),
        )
        names = "\n".join(m.prompt_line() for m in mentions) or "(none found)"
        user = f"Question: {question}\nNames:\n{names}"
        return system, user

    def _examples(self, question: str, source: str, k: int = 4) -> str:
        from rapidfuzz import fuzz

        scored = []
        for t in self.templates.values():
            if t.source != source or t.library:  # the library's files are long: not examples
                continue
            best = max((fuzz.token_set_ratio(question, e) for e in t.examples), default=0)
            scored.append((best, t))
        scored.sort(key=lambda x: -x[0])
        out = []
        for _, t in scored[:k]:
            params = ", ".join(f"${p.name} = {p.type}" for p in t.params) or "none"
            out.append(f'Q: "{t.examples[0]}" (parameters: {params})\n{t.query.strip()}')
        return "\n\n".join(out) or "(none)"

    def _generate_prompt(
        self, question: str, source: str, mentions: list[Mention]
    ) -> tuple[str, str]:
        if source == "sql":
            cols = {t: self.sources.sandbox.columns(t) for t in self.sources.sandbox.tables()}
            schema = sql_schema(question, cols)
        else:
            schema = graph_schema() + f"\nThe graph is {self._graph_text()}."
        system = _fill(
            (PROMPT_DIR / "generate.md").read_text(encoding="utf-8"),
            schema=schema,
            examples=self._examples(question, source),
        )
        names = "\n".join(m.prompt_line() for m in mentions) or "(none found)"
        user = (
            f"Source: {source} ({'DuckDB SQL' if source == 'sql' else 'Neo4j Cypher'})\n"
            f"Today: {self.today.isoformat()}; current season {self.current}.\n"
            f"Question: {question}\nNames:\n{names}"
        )
        return system, user

    # ---- the flow -----------------------------------------------------------------------------

    def ask(
        self, question: str, source: str = "auto", picks: list[dict[str, str]] | None = None
    ) -> AskResult:
        """`picks`: answers to an earlier "which one?" (`{"text", "kind", "value"}` each), from
        the control room's clarify card."""
        t0 = time.monotonic()
        q = " ".join((question or "").split())
        res = AskResult(question=q, context=self.context())
        try:
            self._ask(res, q, source, picks or [])
        except OutOfCredits:
            raise
        except AskLLMError as e:
            res.status, res.error = "error", FIXED["llm"].format(error=str(e))
            res.error_code = "llm"
        finally:
            res.calls = [c.to_dict() for c in self.llm.take_calls()]
            self.last_calls = res.calls
            res.timings["total"] = round(time.monotonic() - t0, 3)
        return res

    def _refuse(self, res: AskResult, by: str, rule: str, message: str) -> None:
        res.status, res.route = "refused", "refused"
        res.refusal = {"by": by, "rule": rule, "message": message}
        res.answer = message

    def _resolve_pick(self, kind: str, value: str) -> Resolution | None:
        """A pick that isn't one of the question's ambiguous names (a name only a template's
        value revealed): kept only when the value names exactly one entity of that kind."""
        if kind == "player":
            r = self.names.player(value)
            return r if r.status == "unique" and r.value == value else None
        if kind == "team":
            code = self.names.team(value)
            if code is None or code != value:
                return None
            return Resolution("team", "unique", (Match(code, self.names.team_label(code)),))
        if kind == "coach":
            r = self.names.coach(value)
            return r if r.status == "unique" else None
        return None

    def _apply_picks(self, mentions: list[Mention], picks: list[dict[str, str]]) -> list[Mention]:
        """The question's names with each picked "which one?" settled. A pick counts only
        when its value is one of the choices the engine offered for that name (or names one
        entity on its own); anything else is ignored, so the card simply comes back."""
        chosen: dict[str, dict[str, str]] = {}
        for p in picks[:5]:
            # whitespace collapsed like the question's: a pick can't add lines to a prompt
            text, kind, value = (
                " ".join(str(p.get(k) or "").split()) for k in ("text", "kind", "value")
            )
            if text and value and kind in PICK_KINDS:
                chosen[norm(text)] = {"text": text[:80], "kind": kind, "value": value[:80]}
        if not chosen:
            return mentions
        out, used = [], set()
        for m in mentions:
            p = chosen.get(norm(m.text))
            if p and m.resolution.status == "ambiguous" and p["kind"] == m.kind:
                match = next((x for x in m.resolution.matches if x.value == p["value"]), None)
                if match is not None:
                    out.append(replace(m, resolution=Resolution(m.kind, "unique", (match,))))
                    used.add(norm(m.text))
                    continue
            out.append(m)
        for key, p in chosen.items():
            if key in used or any(norm(m.text) == key for m in mentions):
                continue
            r = self._resolve_pick(p["kind"], p["value"])
            if r is not None:
                out.append(Mention(p["text"], -1, -1, r))
        return out

    def _ask(self, res: AskResult, q: str, source: str, picks: list[dict[str, str]]) -> None:
        limit = self.cfg.limits.question_chars
        if not q:
            return self._refuse(res, "code", "question.empty", FIXED["empty"])
        if len(q) > limit:
            return self._refuse(res, "code", "question.too_long", FIXED["too_long"].format(n=limit))
        if _WRITE_INTENT.search(q):
            return self._refuse(res, "code", "write", FIXED["write"])
        allowed = self._allowed(source)
        if not allowed:
            return self._refuse(res, "code", "source.off", FIXED["source_off"])
        if self.sources.busy():  # the weekly run is rebuilding the data (D136)
            return self._refuse(res, "code", "source.busy", FIXED["busy"])

        t = time.monotonic()
        mentions = self._apply_picks(self.names.mentions(q), picks)
        res.mentions = [m.prompt_line() for m in mentions]
        # (kind, name) -> the picked value, for template values that repeat the name
        picked = {
            (m.kind, norm(m.text)): m.resolution.value
            for m in mentions
            if m.resolution.status == "unique"
            and any(norm(p.get("text") or "") == norm(m.text) for p in picks)
        }
        res.timings["names"] = round(time.monotonic() - t, 3)
        amb = ambiguous(mentions)
        if amb:
            m = amb[0]
            res.status, res.route = "clarify", "clarify"
            res.choices = [
                {
                    "text": a.text,
                    "kind": a.kind,
                    "options": [{"value": o.value, "label": o.label} for o in a.resolution.matches],
                }
                for a in amb
            ]
            res.answer = FIXED["clarify"].format(kind=m.kind, text=m.text)
            return None

        # the router
        t = time.monotonic()
        system, user = self._route_prompt(q, mentions, allowed)
        history: list[dict[str, str]] = []
        decision = self.llm.chat_json("route", system, user)
        res.timings["route"] = round(time.monotonic() - t, 3)
        rounds = self.cfg.limits.corrections
        gen_source: str | None = None

        while True:
            action = str(decision.get("action") or "").lower()
            if action == "refuse":
                reason = str(decision.get("reason") or "no_data").lower()
                reason = reason if reason in REFUSE_REASONS else "no_data"
                return self._refuse(res, "router", reason, FIXED[reason])
            if action == "template":
                outcome = self._try_template(res, q, decision, allowed, picked)
                if outcome == "done":
                    return None
                if res.retries >= rounds:
                    return self._give_up(res, rounds)
                res.retries += 1
                history += [
                    {"role": "assistant", "content": json.dumps(decision)},
                    {"role": "user", "content": outcome},
                ]
                t = time.monotonic()
                decision = self.llm.chat_json("fix", system, user, history)
                res.timings["route"] = round(res.timings.get("route", 0) + time.monotonic() - t, 3)
                continue
            if action == "generate":
                src = str(decision.get("source") or "").lower()
                gen_source = src if src in allowed else allowed[0]
                return self._generate(res, q, gen_source, mentions)
            # an unknown action: one more router round, then give up
            if res.retries >= rounds:
                res.status, res.error = "error", FIXED["llm"].format(error="no valid action")
                res.error_code = "llm"
                return None
            res.retries += 1
            history += [
                {"role": "assistant", "content": json.dumps(decision)},
                {
                    "role": "user",
                    "content": 'Reply with one of the three JSON shapes: "action" '
                    'must be "template", "generate" or "refuse".',
                },
            ]
            decision = self.llm.chat_json("fix", system, user, history)

    def _give_up(self, res: AskResult, rounds: int) -> None:
        last = res.attempts[-1] if res.attempts else {}
        if last.get("outcome") == "empty":
            res.status = "empty"
            res.answer = FIXED["empty_result"]
            return
        res.status, res.error_code = "error", "query_failed"
        res.error = FIXED["failed"].format(n=rounds, code=last.get("code") or last.get("outcome"))

    def _run(
        self, res: AskResult, source: str, query: str, params: dict[str, Any], allow_maps: bool
    ) -> QueryResult:
        t = time.monotonic()
        try:
            return self.sources.run(source, query, params, allow_maps=allow_maps)
        finally:
            res.timings["run"] = round(res.timings.get("run", 0) + time.monotonic() - t, 3)

    def _record(
        self, res: AskResult, kind: str, source: str, query: str, params, outcome: str, **kw
    ):
        res.attempts.append(
            {
                "kind": kind,
                "source": source,
                "query": query,
                "params": params,
                "outcome": outcome,
                **kw,
            }
        )

    def _try_template(
        self,
        res: AskResult,
        q: str,
        decision: dict,
        allowed: list[str],
        picked: dict[tuple[str, str], str] | None = None,
    ) -> str:
        """'done', or the message for the next router round."""
        tid = str(decision.get("template") or "")
        t = self.templates.get(tid)
        if t is None or t.source not in allowed or (t.asof and not self.asof):
            self._record(res, "template", "", "", {}, "unknown_template", template=tid)
            return (
                f'There is no usable template "{tid[:64]}". Pick one from the list, or reply '
                '{"action": "generate", ...} or {"action": "refuse", ...}.'
            )
        params = decision.get("params") if isinstance(decision.get("params"), dict) else {}
        if picked:  # a name the user picked: the router may still write it as the question did
            params = {
                k: picked.get(((p.type if (p := t.param(k)) else ""), norm(v)), v)
                if isinstance(v, str)
                else v
                for k, v in params.items()
            }
        try:
            r: Rendered = render(t, params, self.names, asof=self.asof)
        except TemplateError as e:
            self._record(
                res, "template", t.source, "", {}, "bad_values", template=t.id, code=e.code
            )
            if e.code == "ambiguous":  # a name only the template's value revealed
                res.status, res.route = "clarify", "clarify"
                res.template_id = t.id
                kind = p.type if (p := t.param(e.param or "")) else "name"
                text = str(params.get(e.param, ""))[:80]
                res.choices = [{"text": text, "kind": kind, "options": [
                    {"value": v, "label": c} for v, c in zip(e.values, e.choices, strict=False)
                ]}]  # fmt: skip
                res.answer = FIXED["clarify"].format(kind=kind, text=text)
                return "done"
            return (
                f"Template {t.id} can't use those values: {e.message}. Fix the values, pick "
                'another template, or reply {"action": "generate", ...} / '
                '{"action": "refuse", ...}.'
            )
        try:
            out = self._run(res, t.source, r.query, r.params, allow_maps=bool(t.library))
        except Refused as e:  # a template the guard refuses is a bug in the library
            if e.rule in _STOP_RULES:  # the weekly lock was taken, or the graph is down: stop
                res.template_id, res.source = t.id, t.source
                self._refuse(res, "source", e.rule, FIXED[_STOP_RULES[e.rule]])
                return "done"
            self._record(
                res, "template", t.source, r.query, r.params, "refused", template=t.id, code=e.rule
            )
            return f"Template {t.id} failed ({e.rule}). Reply " + '{"action": "generate", ...}.'
        except QueryFailed as e:
            self._record(
                res, "template", t.source, r.query, r.params, "failed", template=t.id, code=e.code
            )
            return f"Template {t.id} failed ({e.kind}). Reply " + '{"action": "generate", ...}.'
        rows = [row for row in out.rows if r.keep(out.columns, row)]
        self._record(res, "template", t.source, r.query, r.params,
                     "rows" if rows else "empty", template=t.id, rows=len(rows))  # fmt: skip
        res.route, res.template_id, res.source = "template", t.id, t.source
        res.query, res.params, res.values = r.query, r.params, r.values
        res.columns, res.rows, res.truncated = out.columns, rows, out.truncated
        self._finish(res, q, about=f"template {t.id}: {t.title}", values=r.values)
        return "done"

    def _generate(self, res: AskResult, q: str, source: str, mentions: list[Mention]) -> None:
        t = time.monotonic()
        system, user = self._generate_prompt(q, source, mentions)
        reply = self.llm.chat_json("generate", system, user)
        res.timings["generate"] = round(time.monotonic() - t, 3)
        history: list[dict[str, str]] = []
        rounds = self.cfg.limits.corrections
        while True:
            if reply.get("refuse") or str(reply.get("action") or "") == "refuse":
                reason = str(reply.get("refuse") or reply.get("reason") or "no_data").lower()
                reason = reason if reason in REFUSE_REASONS else "no_data"
                return self._refuse(res, "generator", reason, FIXED[reason])
            if (
                reply.get("accept") is True
                and res.attempts
                and res.attempts[-1]["outcome"] == "empty"
            ):
                last = res.attempts[-1]
                res.route, res.source, res.query, res.params = (
                    "generated",
                    source,
                    last["query"],
                    {},
                )
                res.columns, res.rows = last.get("columns", []), []
                res.status, res.answer = "empty", FIXED["empty_result"]
                return None
            query = reply.get("query")
            if not isinstance(query, str) or not query.strip():
                problem = 'Reply with {"query": "<one read-only query>"}.'
                self._record(res, "generated", source, "", {}, "no_query")
            else:
                problem = None
                try:
                    out = self._run(res, source, query, {}, allow_maps=False)
                except Refused as e:
                    if e.rule in _STOP_RULES:  # the weekly lock was taken, or the graph is down
                        res.source = source
                        return self._refuse(res, "source", e.rule, FIXED[_STOP_RULES[e.rule]])
                    self._record(res, "generated", source, query, {}, "refused", code=e.rule,
                                 message=e.message)  # fmt: skip
                    problem = (
                        f"The guard refused that query: {e.message} Rewrite it within the rules."
                    )
                except QueryFailed as e:
                    self._record(res, "generated", source, query, {}, "failed", code=e.code)
                    hint = f" The database said: {e.hint}" if e.hint else ""
                    problem = f"That query failed ({e.kind}).{hint} Fix it."
                if problem is None:
                    if out.rows:
                        self._record(
                            res, "generated", source, out.query, {}, "rows", rows=len(out.rows)
                        )
                        res.route, res.source, res.query = "generated", source, out.query
                        res.columns, res.rows, res.truncated = out.columns, out.rows, out.truncated
                        return self._finish(res, q, about="a query written for this question")
                    self._record(
                        res, "generated", source, out.query, {}, "empty", columns=out.columns
                    )
                    problem = (
                        "That query returned no rows. If nothing matching is the right answer, "
                        'reply {"accept": true}. Otherwise fix it: check the names, team codes, '
                        "seasons, season types and filters."
                    )
            if res.retries >= rounds:
                last = res.attempts[-1] if res.attempts else {}
                if last.get("outcome") == "refused":
                    res.route, res.source, res.query = "generated", source, last.get("query")
                    return self._refuse(
                        res, "guard", last.get("code") or "guard", last.get("message") or ""
                    )
                if last.get("outcome") == "empty":
                    res.route, res.source, res.query = "generated", source, last.get("query")
                    res.columns = last.get("columns", [])
                    res.status, res.answer = "empty", FIXED["empty_result"]
                    return None
                res.route, res.source = "generated", source
                res.query = last.get("query")
                return self._give_up(res, rounds)
            res.retries += 1
            history += [
                {"role": "assistant", "content": json.dumps(reply)},
                {"role": "user", "content": problem},
            ]
            t = time.monotonic()
            reply = self.llm.chat_json("fix", system, user, history)
            res.timings["generate"] = round(
                res.timings.get("generate", 0) + time.monotonic() - t, 3
            )

    def _finish(self, res: AskResult, q: str, about: str, values: dict | None = None) -> None:
        if not res.rows:
            res.status, res.answer = "empty", FIXED["empty_result"]
            return
        t = time.monotonic()
        text, check, from_llm = answer_mod.write_answer(
            self.llm,
            q,
            res.columns,
            res.rows,
            res.truncated,
            about,
            values,
            max_rows=self.cfg.limits.answer_rows,
        )
        res.timings["answer"] = round(time.monotonic() - t, 3)
        res.status, res.answer = "answered", text
        res.answer_check, res.answer_from_llm = check.to_dict(), from_llm
