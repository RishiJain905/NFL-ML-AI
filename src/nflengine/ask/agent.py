"""The query agent: a model answers an NFL question by querying the databases itself.

Ask's main engine routes a question to a template or has the model write one query in a JSON
reply. The agent is the other shape, the one the fine-tuned SQL + Cypher model (nfl-ask-trainer,
then RL in NFL-RL) was trained on: the model gets the schemas and read-only query tools, calls a
tool, sees the result (a short table, an empty result or the error), may call again, up to
`max_calls` queries in all, and **the last query it ran is its answer**. It then replies with a
short sentence, or the harness stops at the call limit.

What it shares with the engine, so nothing about safety or data changes:
- every query runs through the guard (`Sources.run`): the same text checks, the file-less
  DuckDB sandbox, READ-only Neo4j sessions, timeouts, row caps and the weekly lock;
- the code-level steps before any model call: the question's length, write intent, the weekly
  lock, and names (an ambiguous name returns the choices: "which one?");
- the schema text (`schema.sql_schema` / `graph_schema` / `history_schema`), the names block and
  the dates the generator prompt shows.

What a result holds: the final query, its source, columns and rows (up to `ask.limits.rows`),
each step's kind, row count and error code, and the model calls. Never a database's error text:
`QueryFailed.hint` goes to the model only.

The prompt is the trainer's tool prompt (`asktrainer/data/tool_prompt.py`, `SYSTEM`) with run 2's
multi-turn edit (`sqlrl/run2/prompt.py`: "one query at a time" and the turn rule), and the result
text is run 2's evaluation format (`sqlrl/run2/env.py`: a table, 10 rows, cells cut at 40
characters). The model chooses the database: one tool per source, each schema under its own
heading. `tests/ask/test_ask_agent.py` pins the wording.

No grading here: `agent_bench.py` scores the agent against the golden set's reference answers.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from nflengine.ask.config import AskConfig, ask_config
from nflengine.ask.guard import QueryFailed, QueryResult, Refused, Sources
from nflengine.ask.llm import AskLLM, AskLLMError, LLMCall, OutOfCredits, make_llm
from nflengine.ask.names import NameIndex, ambiguous
from nflengine.ask.route import (
    _WRITE_INTENT,
    PROMPT_DIR,
    _fill,
    curated_through,
    graph_asof,
    history_asof,
)
from nflengine.ask.schema import graph_schema, history_schema, sql_schema

MAX_CALLS = 3
RESULT_ROWS = 10  # rows of a result the model sees (run 2's evaluation setting)
CELL_CHARS = 40
ERR_CHARS = 200
# the local model thinks first (it was trained with thinking on, a 1,024-token budget a turn set
# on the server); the reply needs room for the thought and the call
LOCAL_MAX_TOKENS = 4096
LOCAL_TEMPLATE = {"enable_thinking": True, "reasoning_effort": "high"}

# source: (tool name, what the tool is for, the schema heading)
TOOLS: dict[str, tuple[str, str, str]] = {
    "sql": (
        "query_stats",
        "Run one read-only DuckDB SQL query on the NFL stats tables and return the result rows.",
        "Stats tables (tool `query_stats`, DuckDB SQL)",
    ),
    "graph": (
        "query_weekly_graph",
        "Run one read-only Neo4j Cypher query on the weekly NFL knowledge graph and return the "
        "result rows.",
        "Weekly knowledge graph (tool `query_weekly_graph`, Neo4j Cypher)",
    ),
    "history": (
        "query_history_graph",
        "Run one read-only Neo4j Cypher query on the NFL history graph (every play since 2018) "
        "and return the result rows.",
        "History graph (tool `query_history_graph`, Neo4j Cypher)",
    ),
}
ARG = "query"

PROMPT_FILE = PROMPT_DIR / "agent.md"  # `{{name}}` placeholders, as the engine's prompts


def system_template() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def tool_def(source: str) -> dict[str, Any]:
    name, desc, _ = TOOLS[source]
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": {ARG: {"type": "string", "description": "The query text."}},
                "required": [ARG],
            },
        },
    }


SOURCE_OF_TOOL = {name: src for src, (name, _, _) in TOOLS.items()}


# ---- the result text ---------------------------------------------------------------------------


def _clean(s: str) -> str:
    return "".join(" " if ch in "\r\n\t" else ch for ch in s if ch in "\r\n\t" or ch >= " ")


def _cell(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return format(v, ".10g")
    s = _clean(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str))
    return s if len(s) <= CELL_CHARS else s[: CELL_CHARS - 3] + "..."


def render(res: QueryResult, rows: int = RESULT_ROWS) -> str:
    """The tool's reply for a result: a table with its first `rows` rows, or `0 rows`."""
    if not res.rows:
        return "0 rows"
    shown = res.rows[:rows]
    lines = [" | ".join(_clean(str(c)) for c in res.columns)]
    lines += [" | ".join(_cell(v) for v in r) for r in shown]
    if res.truncated:
        lines.append(f"({len(shown)} of more than {len(res.rows) - 1} rows)")
    elif len(res.rows) > len(shown):
        lines.append(f"({len(shown)} of {len(res.rows)} rows)")
    return "\n".join(lines)


def error_text(e: Exception) -> str:
    if isinstance(e, Refused):
        msg = e.message + (f" ({e.detail})" if e.detail else "")
    elif isinstance(e, QueryFailed):
        msg = e.hint or f"the query failed ({e.kind})"
    else:
        msg = "the query failed"
    line = next((x for x in msg.splitlines() if x.strip()), msg).strip()
    return "Error: " + (_clean(line)[:ERR_CHARS] or "the query failed")


# ---- results -----------------------------------------------------------------------------------


@dataclass
class Step:
    """One tool call: what ran and how it went (no database text)."""

    tool: str
    source: str | None
    query: str
    kind: str  # rows | empty | error | refused | bad_call
    rows: int = 0
    code: str | None = None  # the guard rule or the failure's kind / code
    seconds: float = 0.0


@dataclass
class AgentResult:
    question: str
    status: str = "answered"  # answered | no_query | clarify | refused | error
    reply: str | None = None  # the model's closing sentence (or why it didn't query)
    source: str | None = None
    query: str | None = None  # the last query run: the answer
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    ok: bool = False  # the last query ran
    steps: list[Step] = field(default_factory=list)
    choices: list[dict[str, Any]] = field(default_factory=list)  # clarify: the matches
    error: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def first(self) -> Step | None:
        return self.steps[0] if self.steps else None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rows"] = [[_jsonable(v) for v in r] for r in self.rows]
        return d


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, bool | int | float | str):
        return v
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, dt.date | dt.datetime | dt.time):
        return v.isoformat()
    return str(v)


# ---- the agent ---------------------------------------------------------------------------------


class QueryAgent:
    """Built once per process; `ask(question)` per question."""

    def __init__(
        self,
        llm: AskLLM,
        sources: Sources,
        names: NameIndex,
        cfg: AskConfig,
        *,
        current_season: int,
        asof: tuple[int, int] | None,
        curated: tuple[str | None, int | None] = (None, None),
        history: dict[str, Any] | None = None,
        today: dt.date | None = None,
        max_calls: int = MAX_CALLS,
    ):
        self.llm = llm
        self.sources = sources
        self.names = names
        self.cfg = cfg
        self.current = current_season
        self.asof = asof
        self.curated = curated
        self.history = history
        self.today = today or dt.date.today()
        self.max_calls = max_calls

    @classmethod
    def build(cls, model: str | None = None, cfg: AskConfig | None = None, paths=None,
              max_calls: int = MAX_CALLS) -> QueryAgent:  # fmt: skip
        """`model`: `local:<name>` (the server in `ask.local`) or an OpenRouter model (default:
        `ask.llm`'s)."""
        from nflengine.paths import ensure_data_root
        from nflengine.settings import get_config

        cfg = cfg or ask_config()
        paths = paths or ensure_data_root(create_dirs=False)
        season = get_config().seasons.current
        return cls(
            make_llm(model=model),
            Sources.open(cfg, paths),
            NameIndex.load(paths.curated, season),
            cfg,
            current_season=season,
            asof=graph_asof(paths, season),
            curated=curated_through(paths, season),
            history=history_asof(paths, cfg),
            max_calls=max_calls,
        )

    def close(self) -> None:
        self.sources.close()

    # ---- the prompt ---------------------------------------------------------------------------

    def allowed(self) -> list[str]:
        on = []
        if self.sources.sandbox is not None and self.sources.enabled.get("sql", True):
            on.append("sql")
        if self.sources.driver is not None and self.asof and self.sources.enabled.get("graph"):
            on.append("graph")
        if (
            self.sources.enabled.get("history", False)
            and self.sources.history_driver is not None
            and self.history
        ):
            up = self.sources.history_up
            if up is None or up():
                on.append("history")
        return on

    def _schema(self, source: str, question: str) -> str:
        if source == "sql":
            sb = self.sources.sandbox
            return sql_schema(question, {t: sb.columns(t) for t in sb.tables()})
        if source == "graph":
            w = self.asof
            return graph_schema() + (
                f"\nThe graph is as of {w[0]} week {w[1]} (results through week {w[1] - 1})."
                if w
                else ""
            )
        return history_schema(self.history)

    def _curated_text(self) -> str:
        run_at, week = self.curated
        bits = []
        if week:
            bits.append(f"{self.current} games through week {week}")
        if run_at:
            bits.append(f"rebuilt {str(run_at)[:10]}")
        return "Stats data: " + ", ".join(bits) if bits else "Stats data"

    def prompt(self, question: str, mentions: list, sources: list[str]) -> tuple[str, list]:
        """(system text, tools) for one question."""
        schemas = "\n\n".join(f"## {TOOLS[s][2]}\n{self._schema(s, question)}" for s in sources)
        names = "\n".join(m.prompt_line() for m in mentions) or "(none found)"
        system = _fill(
            system_template(),
            today=self.today.isoformat(),
            current=str(self.current),
            curated=self._curated_text(),
            schemas=schemas,
            names=names,
            n=str(self.max_calls),
        ).strip()
        return system, [tool_def(s) for s in sources]

    # ---- the model call -----------------------------------------------------------------------

    def _chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
              res: AgentResult) -> dict[str, Any]:  # fmt: skip
        llm = self.llm
        body: dict[str, Any] = {"model": llm.model, "messages": messages, "tools": tools}
        if llm.cfg.temperature is not None:
            body["temperature"] = llm.cfg.temperature
        if llm.provider == "local":
            body["max_tokens"] = max(int(llm.cfg.max_tokens), LOCAL_MAX_TOKENS)
            body["chat_template_kwargs"] = dict(LOCAL_TEMPLATE)
        else:
            body["max_tokens"] = int(llm.cfg.max_tokens)
        if llm.provider == "openrouter":
            body["usage"] = {"include": True}
            if llm.effort:
                body["reasoning"] = {"effort": llm.effort, "exclude": True}
            if llm.routing:
                body["provider"] = llm.routing
        call = LLMCall(purpose="agent", model=llm.model, provider=None, latency_s=0.0)
        t0 = time.monotonic()
        try:
            data = llm._post(body)
        except AskLLMError as e:
            call.latency_s = round(time.monotonic() - t0, 3)
            call.error = str(e)
            res.calls.append(asdict(call))
            raise
        call.latency_s = round(time.monotonic() - t0, 3)
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        call.model = data.get("model") or llm.model
        call.provider = data.get("provider") if isinstance(data.get("provider"), str) else None
        call.prompt_tokens = usage.get("prompt_tokens")
        call.completion_tokens = usage.get("completion_tokens")
        call.reasoning_tokens = (usage.get("completion_tokens_details") or {}).get(
            "reasoning_tokens"
        )
        call.cost = 0.0 if llm.provider == "local" else llm._cost(usage)
        call.finish_reason = choice.get("finish_reason")
        call.ok = True
        res.calls.append(asdict(call))
        return choice.get("message") or {}

    # ---- a question ---------------------------------------------------------------------------

    def ask(self, question: str) -> AgentResult:
        t0 = time.monotonic()
        q = " ".join((question or "").split())
        res = AgentResult(question=q)
        try:
            self._ask(res, q)
        except OutOfCredits:
            raise
        except AskLLMError as e:
            res.status, res.error = "error", f"the model didn't give a usable reply: {e}"
        res.seconds = round(time.monotonic() - t0, 3)
        return res

    def _ask(self, res: AgentResult, q: str) -> None:
        if not q or len(q) > self.cfg.limits.question_chars:
            res.status, res.error = "refused", "the question is empty or too long"
            return
        if _WRITE_INTENT.search(q):
            res.status, res.error = "refused", "the databases are read only"
            return
        if self.sources.busy():
            res.status, res.error = "refused", "the weekly run is rebuilding the data: try later"
            return
        mentions = self.names.mentions(q)
        unclear = ambiguous(mentions)
        if unclear:
            res.status = "clarify"
            res.choices = [
                {
                    "text": m.text,
                    "kind": m.resolution.kind,
                    "matches": [{"value": x.value, "label": x.label} for x in m.resolution.matches],
                }  # fmt: skip
                for m in unclear
            ]
            return
        sources = self.allowed()
        if not sources:
            res.status, res.error = "refused", "no database is available"
            return
        system, tools = self.prompt(q, mentions, sources)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": q},
        ]
        last: QueryResult | None = None
        while True:
            msg = self._chat(messages, tools, res)
            calls = msg.get("tool_calls") or []
            if not calls:
                res.reply = (msg.get("content") or "").strip() or None
                break
            if len(res.steps) >= self.max_calls:
                break  # a call past the limit doesn't run (the trained protocol)
            turn = {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}
            if msg.get("reasoning_content"):
                turn["reasoning_content"] = msg["reasoning_content"]
            messages.append(turn)
            for i, c in enumerate(calls):
                text = "Not run: one query at a time."
                if i == 0:
                    step, text, out = self._run(c)
                    res.steps.append(step)
                    last = out if step.kind in ("rows", "empty") else None
                cid = c.get("id") or f"c{len(res.steps)}"
                messages.append({"role": "tool", "tool_call_id": cid, "content": text})
            if len(res.steps) >= self.max_calls:
                break
        if not res.steps:
            res.status = "no_query"
            return
        final = res.steps[-1]
        res.source, res.query = final.source, final.query
        res.ok = last is not None
        if last is not None:
            res.columns = list(last.columns)
            res.rows = [list(r) for r in last.rows[: self.sources.cap]]
            res.truncated = last.truncated

    def _run(self, call: dict[str, Any]) -> tuple[Step, str, QueryResult | None]:
        fn = call.get("function") or {}
        name = str(fn.get("name") or "")
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        query = ""
        if isinstance(args, dict):
            v = args.get(ARG)
            if not isinstance(v, str):
                v = next((x for x in args.values() if isinstance(x, str)), "")
            query = v.strip()
        source = SOURCE_OF_TOOL.get(name)
        if source is None or not query:
            why = "Error: unknown tool" if source is None else "Error: the query is empty"
            return Step(tool=name, source=source, query=query, kind="bad_call"), why, None
        t0 = time.monotonic()
        try:
            out = self.sources.run(source, query)
        except Refused as e:
            step = Step(name, source, query, "refused", code=e.rule)
            step.seconds = round(time.monotonic() - t0, 3)
            return step, error_text(e), None
        except QueryFailed as e:
            step = Step(name, source, query, "error", code=f"{e.kind}:{e.code}")
            step.seconds = round(time.monotonic() - t0, 3)
            return step, error_text(e), None
        kind = "rows" if out.rows else "empty"
        step = Step(name, source, query, kind, rows=len(out.rows),
                    seconds=round(time.monotonic() - t0, 3))  # fmt: skip
        return step, render(out), out
