"""The guard (AE00; documentation/ask-the-engine/README.md §5): only safe, read-only, bounded
queries reach Neo4j and DuckDB. Every query Ask runs, a template's or a generated one, goes
through `run_cypher` or `run_sql`; nothing else in `ask/` opens a session or a connection.

**Cypher** (the weekly graph, Neo4j 5.26 Community: no read-only user, D112):
1. `check_cypher`: a skeleton of the text (strings emptied, comments dropped) must be one
   statement with no write clause (CREATE, MERGE, SET, DELETE, REMOVE, FOREACH, DROP ...), no
   procedure call (`CALL name(...)`; `CALL (x) { ... }` subqueries are fine), no `LOAD CSV`,
   no `apoc.` / `gds.` / `dbms.` / `db.` / `genai.` name, no `USE`, `SHOW`, `EXPLAIN`,
   `PROFILE`, `CYPHER` prefix or `:` command, and no backtick-quoted names (none are needed
   in our schema, and they could hide a namespace).
2. `EXPLAIN` in a READ session: the plan must be query type `r` with no `ProcedureCall` or
   `LoadCSV` operator. (Probed on 2026-10-17: `LOAD CSV`, `apoc.load.*`, `apoc.export.*`,
   `gds.graph.project` and `apoc.util.sleep` all plan as `r`, and `dbms.listConfig` and
   `SHOW USERS` even *run* in READ mode, so step 1 is what stops them.)
3. `execute_read` with a transaction timeout (Neo4j stops it ~1 s past the limit) and at most
   `cap` rows kept. Values must be scalars or lists of scalars: whole nodes, relationships,
   paths and maps are refused (some nodes carry a `key` property, and a node can hold more
   than the question needs; README §5.4). The library's own templates may return maps the
   query builds (`collect({...})`: Q3, Q4, Q5's coaching tree), never nodes (`allow_maps`).

**SQL** (the curated tables):
1. `SqlSandbox`: an in-memory DuckDB connection with **no file access at all**
   (`enable_external_access = false` from the start, no allowed directories, extension
   autoload and autoinstall off, no temp directory, then `lock_configuration`). The curated
   Parquet files come in as **Arrow datasets** registered as views. Two probes on 2026-10-17
   decided this (D136): with `allowed_directories` = curated, DuckDB also *writes* there (a
   `COPY ... TO` landed in curated, and `ATTACH` opened `nfl.duckdb` read-write); and a
   native `read_parquet` view held the file open on Windows, so the curate step's
   `fsutil.replace_file` failed after its 2 s retry while a query ran. Arrow opens files with
   delete sharing: the replace goes through mid-query. A fresh connection per question,
   closed at once; the datasets are cached by the files' size and mtime.
2. `check_sql`: `duckdb.extract_statements` must give one `SELECT`; DuckDB's own parser
   (`json_serialize_sql`) then lists every table and function it names: tables must be
   curated views (or the query's own CTEs), table functions only `range` /
   `generate_series` / `unnest`, and no system or file functions (`duckdb_settings()` names
   local paths; `read_*`, `glob`, `query`, `current_setting`, `getvariable` ...).
3. The query runs wrapped in `SELECT * FROM (...) LIMIT cap + 1`, with `con.interrupt()` from
   a timer at the time limit.

Every refusal names the rule it broke in fixed text (`Refused.rule`, `Refused.message`).
Database errors become `QueryFailed` with the exception type and, for Neo4j, its status
code; their message text is kept only in `QueryFailed.hint`, scrubbed and capped, for the
correction round's prompt, and is never written to files, logs or W&B.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import decimal
import json
import math
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb
    from neo4j import Driver

MAX_QUERY_CHARS = 8000  # without comments
MAX_RAW_CHARS = 4 * MAX_QUERY_CHARS
# Result size (AE01, Sol): a query can return one row holding a 10-million-item list
# (`list(i) FROM range(10000000)`) or a megabyte of text; the timer stops the query, not the
# Python work after it. Bounded per cell and per result, refused with `result.size`.
MAX_CELL_ITEMS = 1000  # items in one list cell, every level counted
MAX_CELL_CHARS = (
    20_000  # characters in one text cell (a list cell: its text form; 1,000 numbers fit)
)
MAX_RESULT_COLUMNS = 100
MAX_RESULT_CHARS = 5_000_000  # all the cells' text together, about
_TOO_BIG = "ask: result too big"  # our own error() text inside the wrapped SQL

RULES: dict[str, str] = {
    # Cypher
    "cypher.empty": "The query is empty.",
    "cypher.too_long": f"The query is longer than {MAX_QUERY_CHARS} characters (comments "
    "not counted).",
    "cypher.unterminated": "The query has an unterminated string, comment or quoted name.",
    "cypher.multiple_statements": "Only one statement is allowed.",
    "cypher.backticks": "Backtick-quoted names aren't allowed: use the plain names in the schema.",
    "cypher.command": "Client commands (lines starting with ':') aren't allowed.",
    "cypher.write": "Writing to the graph isn't allowed (CREATE, MERGE, SET, DELETE, REMOVE, "
    "FOREACH, DROP and the like).",
    "cypher.procedure": "Procedure calls aren't allowed (CALL name(...)); use MATCH, and "
    "CALL (x) { ... } subqueries.",
    "cypher.banned": "LOAD CSV, USE, SHOW, EXPLAIN, PROFILE and CYPHER options aren't allowed.",
    "cypher.namespace": "apoc., gds., dbms., db. and genai. names aren't allowed, nor any "
    "namespaced function but Neo4j's own (date., duration., point. ...).",
    "cypher.escape": "Backslashes are only allowed inside strings, and never before a u "
    "(Unicode escapes).",
    "cypher.plan_not_read": "Neo4j plans this query as a write.",
    "cypher.plan_operator": "The query plan calls a procedure or reads a file.",
    "cypher.values": "Return named properties (p.name, t.team_id), not whole nodes, "
    "relationships, paths or maps.",
    # SQL
    "sql.empty": "The query is empty.",
    "sql.too_long": f"The query is longer than {MAX_QUERY_CHARS} characters.",
    "sql.multiple_statements": "Only one statement is allowed.",
    "sql.not_select": "Only SELECT queries are allowed.",
    "sql.parse": "The query couldn't be checked: write one plain SELECT over the curated tables.",
    "sql.table": "Only the curated tables (and the query's own WITH tables) can be read.",
    "sql.table_function": "Table functions aren't allowed, apart from range, generate_series "
    "and unnest.",
    "sql.function": "System and file functions aren't allowed.",
    "sql.values": "Return plain columns (numbers, text, dates, lists), not structs, maps or blobs.",
    "sql.star": "Name the columns you need: SELECT * isn't allowed on a table this wide.",
    "sql.columns": "The query names too many columns of one table: name only those it needs.",
    "source.busy": "The weekly data refresh is running (it rebuilds the tables and the graph): "
    "ask again when it's done.",
    "source.down": "The weekly graph (Neo4j) isn't answering right now: questions about the "
    "tables still work.",
    "result.size": f"The result is too big to show (a list over {MAX_CELL_ITEMS:,} items, a "
    f"text over {MAX_CELL_CHARS:,} characters, more than {MAX_RESULT_COLUMNS} columns, or too "
    "much in all): aggregate, filter or return fewer columns.",
    # both
    "source.off": "That source is switched off in settings.yaml (ask.sources).",
}


class Refused(Exception):
    """The guard won't run this query. `rule` is a key of `RULES`; `detail` is fixed text or
    a short identifier from the query (validated), safe to show and store."""

    def __init__(self, rule: str, detail: str | None = None):
        if rule not in RULES:
            raise ValueError(f"unknown guard rule {rule!r}")
        self.rule = rule
        self.message = RULES[rule]
        self.detail = detail
        super().__init__(f"{rule}: {self.message}" + (f" ({detail})" if detail else ""))


class QueryFailed(Exception):
    """The database raised while planning or running a checked query.

    `kind` is "timeout", "syntax" or "error"; `code` the exception type (and the Neo4j status
    code). Both are safe to store. `hint` is the database's message, scrubbed and capped: it
    goes only into a correction prompt, never into files, logs or W&B."""

    def __init__(self, kind: str, code: str, hint: str = ""):
        self.kind = kind
        self.code = code
        self.hint = hint
        super().__init__(f"{kind}: {code}")


@dataclass
class QueryResult:
    source: str  # "graph" | "sql"
    query: str
    params: dict[str, Any]
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool  # more rows existed than the cap
    seconds: float
    checks_seconds: float = 0.0  # the guard's own time (EXPLAIN, parsing)
    database: str | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "query": self.query,
            "params": self.params,
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "seconds": round(self.seconds, 3),
            "checks_seconds": round(self.checks_seconds, 3),
        }


# ---- shared ---------------------------------------------------------------------------------

_IDENT = re.compile(r"[A-Za-z0-9_.]{1,64}")


def _ident(text: Any) -> str | None:
    """A short identifier from the query, for a refusal's detail, or None if it isn't one."""
    s = str(text or "")
    return s if _IDENT.fullmatch(s) else None


def _hint(e: BaseException) -> str:
    """The database's message for a correction prompt only: data-root paths made relative,
    secret-shaped text scrubbed, capped at 400 characters."""
    from nflengine.safetext import scrub_bounded

    s = str(e)
    try:
        from nflengine.paths import ensure_data_root, relative_paths

        s = relative_paths(s, ensure_data_root(create_dirs=False).root)
    except Exception:  # no data root: scrub alone
        pass
    return scrub_bounded(s, 400)


def _scalar(v: Any, rule: str, maps: bool = False) -> Any:
    """A JSON-ready scalar (or list of scalars) for a result cell; refuses anything else.
    `maps`: also allow maps built by the query (`{from: ..., to: ...}`), for the library's
    hand-written templates only; a node, relationship or path is never allowed."""
    iso = getattr(v, "iso_format", None)  # neo4j.time.Date / DateTime / Time / Duration
    if callable(iso) and type(v).__module__.startswith("neo4j"):
        return iso()  # before the tuple check: a neo4j Duration is a tuple subclass
    if v is None or isinstance(v, bool | int | str):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, dt.datetime | dt.date | dt.time):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    if isinstance(v, list | tuple):
        return [_scalar(x, rule, maps) for x in v]
    if maps and type(v) is dict:  # a plain map (neo4j Nodes are not dicts)
        return {str(k): _scalar(x, rule, maps) for k, x in v.items()}
    raise Refused(rule)


def _columns(names: Iterable[Any]) -> list[str]:
    return [str(n) for n in names]


def _cell_size(v: Any) -> int:
    """About how many characters a cell takes; raises `Refused("result.size")` as soon as one
    cell is over a bound (checked before the cell is converted, so a huge list costs little)."""
    if isinstance(v, str):
        if len(v) > MAX_CELL_CHARS:
            raise Refused("result.size")
        return len(v)
    if isinstance(v, list | tuple | dict):
        items, chars, stack = 0, 0, [v]
        while stack:
            x = stack.pop()
            seq = x.values() if isinstance(x, dict) else x
            if items + len(seq) > MAX_CELL_ITEMS:
                raise Refused("result.size")
            items += len(seq)
            for y in seq:
                if isinstance(y, list | tuple | dict):
                    stack.append(y)
                else:
                    chars += len(y) if isinstance(y, str) else 8
                    if chars > MAX_CELL_CHARS:
                        raise Refused("result.size")
        return chars
    return 8


class _Budget:
    """The whole result's size so far (`MAX_RESULT_CHARS`)."""

    def __init__(self) -> None:
        self.chars = 0

    def add(self, row: Iterable[Any]) -> None:
        for v in row:
            self.chars += _cell_size(v)
        if self.chars > MAX_RESULT_CHARS:
            raise Refused("result.size")


# ---- Cypher ---------------------------------------------------------------------------------

CYPHER_WRITE = frozenset(
    {
        "CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "FOREACH", "DROP", "ALTER",
        "GRANT", "DENY", "REVOKE", "RENAME", "TERMINATE", "START", "STOP", "INSERT",
    }
)  # fmt: skip
CYPHER_BANNED = frozenset(
    {"LOAD", "USE", "SHOW", "EXPLAIN", "PROFILE", "CYPHER", "TRANSACTIONS", "PERIODIC"}
)
CYPHER_NAMESPACES = frozenset({"apoc", "gds", "dbms", "db", "genai"})
# a namespaced function call (`x.y(...)`) must be one of Neo4j's own: date.truncate,
# duration.between, point.distance, vector.similarity.cosine ... (a plugin's functions don't
# show in the plan: apoc.cypher.runFirstColumnSingle runs a nested query; tests worker, AE00)
CYPHER_FUNCTION_NAMESPACES = frozenset(
    {"date", "datetime", "localdatetime", "localtime", "time", "duration", "point", "vector"}
)
PLAN_BANNED_PREFIXES = ("ProcedureCall", "LoadCSV")

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Neo4j 5.26 replaces \uXXXX in the whole input before its lexer runs (its parser's
# UnicodeEscapeReplacementReader), so an escape anywhere, even inside a string, can rewrite the
# query the server sees (Sol, AE00: '\u0027 + apoc.version() + \u0027' closes the string)
_UNICODE_ESCAPE = re.compile(r"\\+[uU]")
_FUNCTION = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)+)\s*\(")


def _scan(text: str) -> tuple[str, str]:
    """(skeleton, clean). The skeleton has every string literal emptied (`''`), comments
    replaced by a space and each backtick-quoted name replaced by a bare backtick pair (so it
    can be spotted); `clean` is the query with comments replaced by a space and everything
    else verbatim. Raises `Refused("cypher.unterminated")` on an unclosed string, comment or
    quoted name."""
    sk: list[str] = []
    cl: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c in ("'", '"'):
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                raise Refused("cypher.unterminated")
            sk.append("''")
            cl.append(text[i : j + 1])
            i = j + 1
        elif c == "`":
            j = i + 1
            while True:
                j = text.find("`", j)
                if j < 0:
                    raise Refused("cypher.unterminated")
                if j + 1 < n and text[j + 1] == "`":  # `` is an escaped backtick
                    j += 2
                    continue
                break
            sk.append("``")
            cl.append(text[i : j + 1])
            i = j + 1
        elif c == "/" and nxt == "/":  # Neo4j ends a line comment at \n or \r
            ends = [k for k in (text.find("\n", i), text.find("\r", i)) if k >= 0]
            j = min(ends) if ends else -1
            sk.append(" ")
            cl.append(" ")
            i = n if j < 0 else j
        elif c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            if j < 0:
                raise Refused("cypher.unterminated")
            sk.append(" ")
            cl.append(" ")
            i = j + 2
        else:
            sk.append(c)
            cl.append(c)
            i += 1
    return "".join(sk), "".join(cl)


def cypher_skeleton(text: str) -> str:
    """The query's skeleton (see `_scan`): strings emptied, comments dropped."""
    return _scan(text)[0]


def strip_cypher_comments(text: str) -> str:
    """The query with its comments dropped and everything else verbatim (blank lines left by
    whole-line comments removed)."""
    clean = _scan(text)[1]
    return "\n".join(line.rstrip() for line in clean.splitlines() if line.strip())


def _neighbours(s: str, start: int, end: int) -> tuple[str, str]:
    """The nearest non-space characters before `start` and after `end`."""
    before = s[:start].rstrip()
    after = s[end:].lstrip()
    return (before[-1] if before else ""), (after[0] if after else "")


def check_cypher(text: str) -> str:
    """The query, without comments or a trailing `;`, if it passes the text rules; else
    `Refused`."""
    q = (text or "").strip()
    if not q:
        raise Refused("cypher.empty")
    if len(q) > MAX_RAW_CHARS:
        raise Refused("cypher.too_long")
    if _UNICODE_ESCAPE.search(q):  # decoded before tokenizing: '\u0027' closes a string
        raise Refused("cypher.escape")
    sk, q = _scan(q)
    sk, q = sk.strip(), q.strip()
    if len(q) > MAX_QUERY_CHARS:  # counted without comments (the library's files are commented)
        raise Refused("cypher.too_long")
    while sk.endswith(";"):  # outside strings, so the clean text ends with the same ';'
        sk, q = sk[:-1].rstrip(), q[:-1].rstrip()
    if not sk:
        raise Refused("cypher.empty")
    if ";" in sk:
        raise Refused("cypher.multiple_statements")
    if sk.startswith(":"):
        raise Refused("cypher.command")
    if "``" in sk:
        raise Refused("cypher.backticks")
    if "\\" in sk:  # Neo4j decodes \uXXXX outside strings too: `apoc` is `apoc`
        raise Refused("cypher.escape")
    for m in _FUNCTION.finditer(sk):
        namespace = m.group(1).split(".")[0].strip().lower()
        if namespace not in CYPHER_FUNCTION_NAMESPACES:
            raise Refused("cypher.namespace", _ident(namespace))
    for m in _WORD.finditer(sk):
        word = m.group(0)
        before, after = _neighbours(sk, m.start(), m.end())
        if before == "." and sk[: m.start()].rstrip()[:-1].rstrip().endswith("."):
            before = ""  # `0..apoc`: the end of a range, not a property access
        if before in (".", "$"):
            continue  # a property access (n.set) or a parameter ($create)
        low, up = word.lower(), word.upper()
        if low in CYPHER_NAMESPACES and after == ".":
            raise Refused("cypher.namespace", low)
        if before == ":" or after == ":":
            continue  # a label / type (:Set) or a map key ({set: 1}); never a clause
        if up in CYPHER_WRITE:
            raise Refused("cypher.write", up)
        if up in CYPHER_BANNED:
            raise Refused("cypher.banned", up)
        if up == "CALL" and after not in ("(", "{"):
            raise Refused("cypher.procedure")
    return q


def _plan_operators(plan: Any) -> list[str]:
    """Every operator name in an EXPLAIN plan (dict or object form), '@neo4j' dropped."""
    if plan is None:
        return []
    if isinstance(plan, dict):
        name, children = plan.get("operatorType", ""), plan.get("children") or []
    else:
        name = getattr(plan, "operator_type", "") or ""
        children = getattr(plan, "children", None) or []
    return [str(name).split("@")[0], *(op for c in children for op in _plan_operators(c))]


_UNREACHABLE = frozenset({"ServiceUnavailable", "SessionExpired"})


def _unreachable(e: BaseException) -> bool:
    """The driver couldn't reach Neo4j (down, restarting, no route): the graph is down,
    which no corrected query can fix (AE01: the question stops with `source.down`)."""
    return type(e).__name__ in _UNREACHABLE or isinstance(e, OSError)


def _neo4j_failed(e: BaseException) -> QueryFailed:
    code = getattr(e, "code", None)
    code = code if isinstance(code, str) and code.startswith("Neo.") else None
    name = type(e).__name__
    if code and "TimedOut" in code:
        return QueryFailed("timeout", f"{name}: {code}")
    if code and code.endswith(("SyntaxError", "ParameterMissing", "ArgumentError", "TypeError")):
        return QueryFailed("syntax", f"{name}: {code}", _hint(e))
    if code and code.startswith("Neo.ClientError"):
        return QueryFailed("error", f"{name}: {code}", _hint(e))
    from nflengine.graph.client import safe_error

    return QueryFailed("error", safe_error(e))


def _read_session(driver: Driver, database: str | None):
    from neo4j import READ_ACCESS, NotificationMinimumSeverity

    return driver.session(
        database=database,
        default_access_mode=READ_ACCESS,
        notifications_min_severity=NotificationMinimumSeverity.OFF,
    )


def explain_cypher(
    driver: Driver,
    query: str,
    params: dict[str, Any] | None = None,
    database: str | None = None,
    timeout_s: float = 10.0,
) -> None:
    """`EXPLAIN` the checked query in a READ session, timed out: query type `r`, no procedure
    / file operator. Raises `Refused` or `QueryFailed`."""
    from neo4j import Query

    try:
        with _read_session(driver, database) as s:
            summary = s.run(Query("EXPLAIN " + query, timeout=timeout_s), params or {}).consume()
    except Exception as e:
        if _unreachable(e):
            raise Refused("source.down") from None
        raise _neo4j_failed(e) from None
    if summary.query_type != "r":
        raise Refused("cypher.plan_not_read", _ident(summary.query_type))
    for op in _plan_operators(summary.plan):
        if op.startswith(PLAN_BANNED_PREFIXES):
            raise Refused("cypher.plan_operator", _ident(op))


def run_cypher(
    driver: Driver,
    query: str,
    params: dict[str, Any] | None = None,
    *,
    database: str | None = None,
    timeout_s: float = 10.0,
    cap: int = 500,
    allow_maps: bool = False,
) -> QueryResult:
    """Check, EXPLAIN and run a Cypher query read-only, timed out and capped. `allow_maps`
    lets query-built maps through (the library's templates only: see `_scalar`)."""
    from neo4j import unit_of_work

    params = dict(params or {})
    t0 = time.monotonic()
    text = check_cypher(query)
    explain_cypher(driver, text, params, database, timeout_s)
    t1 = time.monotonic()
    remaining = timeout_s - (t1 - t0)  # one deadline for the plan and the run (Sol, AE00)
    if remaining <= 0:
        raise QueryFailed("timeout", "deadline")

    @unit_of_work(timeout=max(remaining, 0.5))
    def work(tx):
        result = tx.run(text, params)
        keys = list(result.keys())
        if len(keys) > MAX_RESULT_COLUMNS:
            raise Refused("result.size")
        rows: list[list[Any]] = []
        budget = _Budget()
        for rec in result:
            if len(rows) > cap:
                break
            values = rec.values()
            budget.add(values)  # before converting: a huge list is refused cheaply
            rows.append([_scalar(v, "cypher.values", allow_maps) for v in values])
        return keys, rows

    try:
        with _read_session(driver, database) as s:
            keys, rows = s.execute_read(work)
    except Refused:
        raise
    except Exception as e:
        if _unreachable(e):
            raise Refused("source.down") from None
        raise _neo4j_failed(e) from None
    t2 = time.monotonic()
    return QueryResult(
        source="graph",
        query=text,
        params=params,
        columns=_columns(keys),
        rows=rows[:cap],
        truncated=len(rows) > cap,
        seconds=t2 - t0,
        checks_seconds=t1 - t0,
        database=database,
    )


# ---- SQL ------------------------------------------------------------------------------------

ALLOWED_TABLE_FUNCTIONS = frozenset({"range", "generate_series", "unnest"})
# the words a SELECT can start with (checked before DuckDB parses anything)
SQL_FIRST_WORDS = frozenset({"select", "with", "from", "values", "table", "pivot", "unpivot", "("})
_FIRST_WORD = re.compile(r"[A-Za-z_]+|\(")
BANNED_FUNCTION_PREFIXES = (
    "duckdb_", "pragma_", "read_", "parquet_", "sniff_", "iceberg_", "delta_", "sqlite_",
    "postgres_", "mysql_", "json_serialize", "json_deserialize", "json_execute", "arrow_scan",
    "load_", "install_",
)  # fmt: skip
BANNED_FUNCTIONS = frozenset(
    {
        "current_setting", "getvariable", "glob", "query", "query_table", "which_secret",
        "checkpoint", "force_checkpoint", "import_database", "excel", "from_json_file",
        "test_all_types", "enable_profiling", "set_variable",
    }
)  # fmt: skip


WIDE_TABLE = 60  # columns: `*` over a wider table is refused (plays 372, player_games 150 ...)
MAX_COLUMNS = 100  # columns one query may read from one table


class SqlSandbox:
    """Locked, file-less DuckDB connections over the curated tables.

    `curated` is the curated folder: one `<table>.parquet` per table plus `plays/*.parquet`.
    Tables in `exclude` (free text: `espn_news`) are left out. Each question reads **only the
    columns its query names**, from only the tables it names, into memory, **one file at a
    time** (`load`): a file is open only while it's read (~0.1 s), so the curate step's
    `fsutil.replace_file` (2 s of retries) is never blocked by a question (D136; a reader that
    holds a file for the whole query, a native view or an Arrow dataset, blocks it on Windows).
    Schemas are cached by every file's (size, mtime)."""

    def __init__(
        self,
        curated: Path,
        exclude: Iterable[str] = ("espn_news",),
        memory_limit: str = "2GB",
        threads: int = 4,
    ):
        self.curated = Path(curated)
        self.exclude = {t.lower() for t in exclude}
        self.memory_limit = memory_limit
        self.threads = int(threads)
        self._lock = threading.Lock()
        self._signature: tuple | None = None
        self._schemas: dict[str, Any] = {}  # table -> unified pyarrow schema
        self._file_columns: dict[Path, set[str]] = {}
        self._coach_fixes: Any = None  # config/head_coach_fixes.csv, read once

    def _files(self) -> dict[str, list[Path]]:
        found = {p.stem: [p] for p in sorted(self.curated.glob("*.parquet"))}
        plays = sorted((self.curated / "plays").glob("*.parquet"))
        if plays:
            found["plays"] = plays
        return {k: v for k, v in found.items() if k.lower() not in self.exclude}

    def _refresh(self) -> dict[str, list[Path]]:
        """The files, and the cached schemas rebuilt if any file changed."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        files = self._files()
        sig = tuple(
            (name, str(p), st.st_size, st.st_mtime_ns)
            for name, ps in sorted(files.items())
            for p in ps
            for st in [p.stat()]
        )
        with self._lock:
            if sig != self._signature:
                schemas: dict[str, Any] = {}
                file_columns: dict[Path, set[str]] = {}
                for name, ps in files.items():
                    each = [pq.read_schema(p) for p in ps]
                    for p, s in zip(ps, each, strict=True):
                        file_columns[p] = set(s.names)
                    # plays: one file per season; `goal_to_go` is int32 in some, f64 in others
                    schemas[name] = (
                        each[0]
                        if len(each) == 1
                        else pa.unify_schemas(each, promote_options="permissive")
                    )
                self._schemas, self._file_columns, self._signature = schemas, file_columns, sig
        return files

    def tables(self) -> list[str]:
        self._refresh()
        return sorted(self._schemas)

    def schema(self, table: str) -> Any:
        self._refresh()
        return self._schemas[table]

    def columns(self, table: str) -> list[tuple[str, str]]:
        return [(f.name, str(f.type)) for f in self.schema(table)]

    def load(self, table: str, columns: list[str], deadline: float | None = None) -> Any:
        """`columns` of `table` as one in-memory Arrow table, read file by file (each file is
        opened, read and closed before the next). Past `deadline` (a `time.monotonic()`
        value) it stops between files with `QueryFailed("timeout")`. `games.home_coach` /
        `away_coach` come with the graph's head-coach fixes applied (`_games_with_fixes`)."""
        if table == "games" and {"home_coach", "away_coach"} & set(columns):
            return self._games_with_fixes(columns, deadline)
        return self._load(table, columns, deadline)

    _COACH_KEYS = ("season", "week", "home_team", "away_team", "home_coach", "away_coach")

    def _games_with_fixes(self, columns: list[str], deadline: float | None) -> Any:
        """games with `config/head_coach_fixes.csv` applied, as the graph build does
        (`graph.tables_extra.apply_coach_fixes`): nflverse carries last season's coach into a
        new hire's season and lists no interims, so SQL and the graph named different coaches
        (the 2026-10-18 audit). The curated file is untouched."""
        import polars as pl

        from nflengine.graph.tables_extra import apply_coach_fixes, read_coach_fixes

        self._refresh()
        if not set(self._COACH_KEYS) <= set(self._schemas["games"].names):
            return self._load("games", columns, deadline)
        if self._coach_fixes is None:
            self._coach_fixes = read_coach_fixes(log=lambda _s: None)
        wanted = list(dict.fromkeys([*columns, *self._COACH_KEYS]))
        df = pl.from_arrow(self._load("games", wanted, deadline))
        return apply_coach_fixes(df, self._coach_fixes).select(columns).to_arrow()

    def _load(self, table: str, columns: list[str], deadline: float | None = None) -> Any:
        import pyarrow as pa
        import pyarrow.parquet as pq

        files = self._refresh()[table]
        schema = self._schemas[table]
        fields = [schema.field(c) for c in columns]
        parts = []
        for p in files:
            if deadline is not None and time.monotonic() > deadline:
                raise QueryFailed("timeout", "deadline")
            have = [c for c in columns if c in self._file_columns.get(p, set())]
            part = pq.read_table(p, columns=have, memory_map=False)
            arrays = [
                part.column(f.name).cast(f.type)
                if f.name in have
                else pa.nulls(part.num_rows, f.type)
                for f in fields
            ]
            parts.append(pa.Table.from_arrays(arrays, schema=pa.schema(fields)))
        return pa.concat_tables(parts) if len(parts) > 1 else parts[0]

    def connect(self) -> duckdb.DuckDBPyConnection:
        """A fresh in-memory connection with no file access and no tables yet (`run_sql`
        registers the ones a query needs)."""
        import duckdb

        con = duckdb.connect(
            ":memory:",
            config={
                "enable_external_access": False,
                "autoload_known_extensions": False,
                "autoinstall_known_extensions": False,
                "python_enable_replacements": False,  # no scanning Python variables by name
                "temp_directory": "",
                "memory_limit": self.memory_limit,
                "threads": self.threads,
            },
        )
        try:
            con.execute("SET lock_configuration = true")
        except Exception:
            con.close()
            raise
        return con


def _ast_walk(node: Any, visit: Callable[[dict], None]) -> None:
    if isinstance(node, dict):
        visit(node)
        for v in node.values():
            _ast_walk(v, visit)
    elif isinstance(node, list):
        for v in node:
            _ast_walk(v, visit)


def _cte_names(node: Any) -> set[str]:
    names: set[str] = set()

    def visit(d: dict) -> None:
        cte = d.get("cte_map")
        if isinstance(cte, dict):
            for item in cte.get("map") or []:
                if isinstance(item, dict) and isinstance(item.get("key"), str):
                    names.add(item["key"].lower())

    _ast_walk(node, visit)
    return names


def _direct_tables(node: Any) -> set[str]:
    """The base tables a FROM clause reads directly (not inside its subqueries)."""
    if not isinstance(node, dict):
        return set()
    kind = node.get("type")
    if kind == "BASE_TABLE":
        return {str(node.get("table_name") or "").lower()}
    if kind == "JOIN":
        return _direct_tables(node.get("left")) | _direct_tables(node.get("right"))
    return set()  # SUBQUERY, TABLE_FUNCTION, EXPRESSION_LIST, EMPTY ...


def _direct_refs(node: Any) -> list[tuple[str, str]]:
    """(table, alias) for each base table a FROM clause reads directly."""
    if not isinstance(node, dict):
        return []
    kind = node.get("type")
    if kind == "BASE_TABLE":
        return [(str(node.get("table_name") or "").lower(), str(node.get("alias") or "").lower())]
    if kind == "JOIN":
        return _direct_refs(node.get("left")) + _direct_refs(node.get("right"))
    return []


def _star_tables(tree: Any) -> set[str]:
    """Tables a `*` (or `t.*`, `COLUMNS(*)`) expands over: those read directly by a SELECT
    whose select list holds the star; `t.*` only the table named (or aliased) `t`."""
    out: set[str] = set()

    def stars(expr: Any) -> list[str]:
        """The relation names of the stars in this select list ('' for a bare `*`), not those
        inside a subquery of it (a subquery's star is its own)."""
        if isinstance(expr, list):
            return [r for x in expr for r in stars(x)]
        if not isinstance(expr, dict) or expr.get("class") == "SUBQUERY":
            return []
        if expr.get("type") == "STAR" or expr.get("class") == "STAR":
            return [str(expr.get("relation_name") or "").lower()]
        return [r for v in expr.values() for r in stars(v)]

    def visit(d: dict) -> None:
        if d.get("type") != "SELECT_NODE":
            return
        found = stars(d.get("select_list"))
        if not found:
            return
        refs = _direct_refs(d.get("from_table"))
        if "" in found:  # a bare `*`: every table this SELECT reads
            out.update(t for t, _ in refs)
        else:
            named = set(found)
            out.update(t for t, a in refs if a in named or (not a and t in named))

    _ast_walk(tree, visit)
    return out


@dataclass(frozen=True)
class SqlPlan:
    """A checked query: its text, the curated tables it reads, every column name it names
    (lower case), and the tables a `*` expands over."""

    text: str
    tables: frozenset[str]
    columns: frozenset[str]
    star_tables: frozenset[str]


def _strip_tail(text: str) -> str:
    """The statement without trailing `;` (and whatever comments follow them): DuckDB's
    tokenizer skips comments, so the last token tells where the statement really ends."""
    import duckdb

    text = text.rstrip()
    tokens = duckdb.tokenize(text)
    while tokens and text[tokens[-1][0]] == ";":
        text = text[: tokens[-1][0]].rstrip()
        tokens = tokens[:-1]
    return text


def check_sql_plan(sql: str, tables: Iterable[str], con: duckdb.DuckDBPyConnection) -> SqlPlan:
    """The single SELECT statement and what it reads, if it passes; else `Refused` /
    `QueryFailed`. `con` is a sandbox connection (DuckDB's parser runs there:
    `json_serialize_sql`)."""
    import duckdb

    q = (sql or "").strip()
    if not q:
        raise Refused("sql.empty")
    if len(q) > MAX_QUERY_CHARS:
        raise Refused("sql.too_long")
    # DuckDB's parser itself reads files for IMPORT DATABASE ('<dir>/schema.sql'), so the
    # first word is checked with the tokenizer (lexical only) before anything is parsed
    try:
        tokens = duckdb.tokenize(q)
    except Exception:
        raise Refused("sql.parse") from None
    if not tokens:
        raise Refused("sql.empty")
    first = _FIRST_WORD.match(q, tokens[0][0])
    if first is None or first.group(0).lower() not in SQL_FIRST_WORDS:
        raise Refused("sql.not_select", _ident(first.group(0) if first else None))
    # a `;` with anything after it is a second statement: refused before anything is parsed
    # (`SELECT 1; IMPORT DATABASE '<dir>'` read the folder in DuckDB's default parser; Sol)
    semis = [i for i, (off, _) in enumerate(tokens) if q[off] == ";"]
    if semis and any(q[off] != ";" for off, _ in tokens[semis[0] :]):
        raise Refused("sql.multiple_statements")
    try:
        statements = con.extract_statements(q)  # the locked, file-less connection
    except duckdb.Error as e:
        raise QueryFailed("syntax", type(e).__name__, _hint(e)) from None
    if not statements:
        raise Refused("sql.empty")
    if len(statements) > 1:
        raise Refused("sql.multiple_statements")
    st = statements[0]
    if st.type != duckdb.StatementType.SELECT:
        raise Refused("sql.not_select", _ident(st.type.name))
    text = st.query.strip()
    try:
        raw = con.execute("SELECT json_serialize_sql($q)", {"q": text}).fetchone()[0]
        tree = json.loads(raw)
    except Exception:
        raise Refused("sql.parse") from None
    if tree.get("error") or not tree.get("statements"):
        raise Refused("sql.parse")
    real = {t.lower() for t in tables}
    ctes = _cte_names(tree)
    allowed = real | ctes
    problems: list[tuple[str, str | None]] = []
    used: set[str] = set()
    names: set[str] = set()
    natural: set[str] = set()
    aliases: dict[str, str] = {}  # FROM alias -> table
    renamed: set[str] = set()  # tables with a column alias list: every column, in order
    single_refs: set[str] = set()  # one-part column refs: a table or alias there is a whole row
    whole: list[bool] = [False]  # a positional ref (#1) or PIVOT / UNPIVOT: needs every column
    shadow = sorted(ctes & real)
    if shadow:  # `WITH plays AS (...)`: which `plays` a star means gets ambiguous
        raise Refused("sql.table", _ident(shadow[0]))

    def visit(d: dict) -> None:
        kind = d.get("type")
        if kind == "BASE_TABLE":
            name = str(d.get("table_name") or "").lower()
            schema = str(d.get("schema_name") or "").lower()
            catalog = str(d.get("catalog_name") or "").lower()
            if name not in allowed or schema not in ("", "main") or catalog not in ("", "memory"):
                problems.append(("sql.table", _ident(name)))
            elif name in real:
                used.add(name)
                alias = str(d.get("alias") or "").lower()
                if alias:
                    aliases[alias] = name
                if d.get("column_name_alias"):  # `teams AS t(a, b)` renames by position
                    renamed.add(name)
        elif kind == "SHOW_REF":  # DESCRIBE / SUMMARIZE / SHOW
            problems.append(("sql.parse", None))
        elif kind in ("PIVOT", "POSITIONAL_REFERENCE"):
            whole[0] = True
        elif kind == "TABLE_FUNCTION":
            fn = (d.get("function") or {}).get("function_name", "")
            if str(fn).lower() not in ALLOWED_TABLE_FUNCTIONS:
                problems.append(("sql.table_function", _ident(fn)))
        elif kind == "FUNCTION":
            fn = str(d.get("function_name") or "").lower()
            schema = str(d.get("schema") or "").lower()
            if (
                fn in BANNED_FUNCTIONS
                or fn.startswith(BANNED_FUNCTION_PREFIXES)
                or schema not in ("", "main")
            ):
                problems.append(("sql.function", _ident(fn)))
        elif kind == "COLUMN_REF":
            parts = [str(n).lower() for n in d.get("column_names") or []]
            names.update(parts)
            if len(parts) == 1:
                single_refs.add(parts[0])
        elif kind == "JOIN":
            names.update(str(n).lower() for n in d.get("using_columns") or [])
            if str(d.get("ref_type") or "").upper() == "NATURAL":  # joins on every shared column
                natural.update(_direct_tables(d.get("left")) | _direct_tables(d.get("right")))

    _ast_walk(tree["statements"], visit)
    if problems:
        rule, detail = problems[0]
        raise Refused(rule, detail)
    star = (_star_tables(tree["statements"]) - ctes) | natural
    # `to_json(t)`, `t::VARCHAR`, `row_to_json(t)`: a table or its alias as a value is the row
    star |= {t for t in used if t in single_refs} | {
        aliases[a] for a in single_refs & aliases.keys()
    }
    if whole[0]:
        star |= used
    star |= renamed
    return SqlPlan(
        text=_strip_tail(text),
        tables=frozenset(used),
        columns=frozenset(names),
        star_tables=frozenset(star & used),
    )


def check_sql(sql: str, tables: Iterable[str], con: duckdb.DuckDBPyConnection) -> str:
    """The single SELECT statement's text if it passes (see `check_sql_plan`)."""
    return check_sql_plan(sql, tables, con).text


def _needed_columns(sandbox: SqlSandbox, table: str, plan: SqlPlan) -> list[str]:
    names = [f.name for f in sandbox.schema(table)]
    if table in plan.star_tables:
        if len(names) > WIDE_TABLE:
            raise Refused("sql.star", _ident(table))
        return names
    cols = [c for c in names if c.lower() in plan.columns]
    if len(cols) > MAX_COLUMNS:
        raise Refused("sql.columns", _ident(table))
    return cols or names[:1]  # count(*) alone still needs the rows


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# result column types the guard returns (DuckDB type ids): plain values. Anything else (struct,
# map, union, blob, bit, variant, uuid, a type added later) is refused before it's fetched
SCALAR_TYPE_IDS = frozenset(
    {
        "boolean", "tinyint", "smallint", "integer", "bigint", "hugeint", "utinyint",
        "usmallint", "uinteger", "ubigint", "uhugeint", "float", "double", "decimal", "varchar",
        "enum", "date", "time", "time with time zone", "timestamp", "timestamp with time zone",
        "timestamp_s", "timestamp_ms", "timestamp_ns", "interval",
    }
)  # fmt: skip


def _shape(t: Any) -> tuple[list[str], Any]:
    """(the nesting levels, outermost first: "list" / "array", the leaf type) of a column's
    DuckDB type, read from its structured form (`.id`, `.children`), never its display text: an
    ENUM label may hold "STRUCT(" or "[]" (Sol)."""
    levels: list[str] = []
    while t.id in ("list", "array"):
        kids = dict(t.children)
        if t.id == "array" and int(kids.get("size") or 0) > MAX_CELL_ITEMS:
            raise Refused("result.size")
        levels.append(t.id)
        t = kids["child"]
    return levels, t


def _sized_columns(described: list[Any]) -> str:
    """The SELECT list of the wrapped query: each column as it is, except that a list or a text
    cell over its bound raises our own error inside DuckDB (before anything is fetched into
    Python), and a column of any type but plain values (and lists of them) is refused at once.
    A list counts its items at every level (4,000 empty lists are 4,000 items; Sol). Columns are
    taken by position (`#n`): a result may repeat a column name."""
    if len(described) > MAX_RESULT_COLUMNS:
        raise Refused("result.size")
    big = f"error('{_TOO_BIG}')"
    out = []
    for i, d in enumerate(described, start=1):
        ref, name = f"#{i}", _quote(str(d[0]))
        levels, leaf = _shape(d[1])
        if leaf.id not in SCALAR_TYPE_IDS:
            raise Refused("sql.values")
        if not levels:
            if leaf.id == "varchar":
                check = f"length({ref}) > {MAX_CELL_CHARS}"
                out.append(f"CASE WHEN {check} THEN {big} ELSE {ref} END AS {name}")
            else:
                out.append(f"{ref} AS {name}")
            continue
        if "array" in levels:  # fixed sizes, each checked in `_shape`: their product is static
            if "list" in levels:
                raise Refused("sql.values")  # a list inside an array (or the reverse): not needed
            total = 1
            for lv in _array_sizes(d[1]):
                total *= lv
            if total > MAX_CELL_ITEMS:
                raise Refused("result.size")
            count = "0"
        else:
            parts, flat = [], ref
            for _ in levels:  # the items of every level: len(c) + len(flatten(c)) + ...
                parts.append(f"coalesce(len({flat}), 0)")
                flat = f"flatten({flat})"
            count = " + ".join(parts)
        out.append(
            f"CASE WHEN {count} > {MAX_CELL_ITEMS} THEN {big} "
            f"WHEN length(CAST({ref} AS VARCHAR)) > {MAX_CELL_CHARS} THEN {big} "
            f"ELSE {ref} END AS {name}"
        )
    return ", ".join(out) or "*"


def _array_sizes(t: Any) -> list[int]:
    sizes = []
    while t.id == "array":
        kids = dict(t.children)
        sizes.append(int(kids.get("size") or 0))
        t = kids["child"]
    return sizes


def run_sql(
    sandbox: SqlSandbox,
    sql: str,
    params: dict[str, Any] | None = None,
    *,
    timeout_s: float = 10.0,
    cap: int = 500,
) -> QueryResult:
    """Check a SELECT, read the columns it names into memory, and run it in a fresh sandbox
    connection, interrupted at `timeout_s`, at most `cap` rows kept."""
    import duckdb

    params = dict(params or {})
    t0 = time.monotonic()
    deadline = t0 + timeout_s  # one budget for the checks, the loading and the run (Sol, AE00)
    con = sandbox.connect()
    try:
        plan = check_sql_plan(sql, sandbox.tables(), con)
        t1 = time.monotonic()
        for table in sorted(plan.tables):
            cols = _needed_columns(sandbox, table, plan)
            con.register(table, sandbox.load(table, cols, deadline=deadline))
        # the newline before ")" keeps a trailing "-- comment" from swallowing it
        inner = f"(\n{plan.text}\n) AS ask_capped"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise QueryFailed("timeout", "deadline")
        timer = threading.Timer(remaining, con.interrupt)
        timer.start()
        raw: list[Any] = []
        try:
            # the result's columns and types, no rows. A query without parameters (every one the
            # model writes) is only bound, never run (`con.sql` is lazy; a `LIMIT 0` run can
            # still compute a projection: `range(10000000)::VARIANT` took the whole budget); a
            # template's query has parameters, which `con.sql` would run, so it gets the LIMIT 0
            if params:
                cur = con.execute(f"SELECT * FROM {inner} LIMIT 0", params)
                described = [(d[0], d[1]) for d in cur.description or []]
            else:
                rel = con.sql(f"SELECT * FROM {inner}")
                described = list(zip(rel.columns, rel.types, strict=True))
            columns = _columns(d[0] for d in described)
            wrapped = f"SELECT {_sized_columns(described)} FROM {inner} LIMIT {int(cap) + 1}"
            cur = con.execute(wrapped, params) if params else con.execute(wrapped)
            budget = _Budget()
            while chunk := cur.fetchmany(64):
                for r in chunk:
                    budget.add(r)
                raw += chunk
        except duckdb.InterruptException:
            raise QueryFailed("timeout", "InterruptException") from None
        except duckdb.Error as e:
            if _TOO_BIG in str(e):
                raise Refused("result.size") from None
            kind = "syntax" if isinstance(e, duckdb.ParserException) else "error"
            raise QueryFailed(kind, type(e).__name__, _hint(e)) from None
        finally:
            timer.cancel()
        rows = [[_scalar(v, "sql.values") for v in r] for r in raw]
    finally:
        con.close()
    return QueryResult(
        source="sql",
        query=plan.text,
        params=params,
        columns=columns,
        rows=rows[:cap],
        truncated=len(rows) > cap,
        seconds=time.monotonic() - t0,
        checks_seconds=t1 - t0,
    )


@dataclass
class Sources:
    """What a question may read: the weekly graph's driver, the SQL sandbox, and the limits.
    Built once per process (`Sources.open`) and shared by every question."""

    sandbox: SqlSandbox | None
    driver: Any | None
    database: str | None = None
    timeout_s: float = 10.0
    cap: int = 500
    enabled: dict[str, bool] = field(default_factory=dict)
    lock_file: Path | None = None  # the weekly run's lock (ops.lock): held = data being rebuilt
    # AE01: the control room's Neo4j check (`ServiceStatus`, every 30 s); False = down, so a
    # graph question stops at once instead of waiting on the driver's connection timeout
    graph_up: Callable[[], bool] | None = None

    def busy(self) -> bool:
        """Is a weekly run (or an injury update, or Update play calling) holding the weekly
        lock? Then the curated tables are being replaced and the graph may be mid-rebuild, so
        Ask doesn't read either (D136). Checked the way the control room checks it."""
        if self.lock_file is None:
            return False
        from nflengine.ops.lock import is_locked, read_holder

        # the note first (`app.readers.meta.lock_dict`'s rule): probing takes the lock for an
        # instant, and a weekly run starting in that instant would stop with exit 4. A run writes
        # its note right after taking the lock and empties it when it lets go; no note = free
        try:
            return bool(read_holder(self.lock_file)) and is_locked(self.lock_file)
        except OSError:
            return False

    @classmethod
    def open(cls, cfg=None, paths=None, driver=None) -> Sources:
        from nflengine.ask.config import ask_config
        from nflengine.ops.lock import lock_path
        from nflengine.paths import ensure_data_root

        cfg = cfg or ask_config()
        paths = paths or ensure_data_root(create_dirs=False)
        enabled = {k: bool(v) for k, v in cfg.sources.items()}
        sandbox = (
            SqlSandbox(paths.curated, cfg.sql.exclude_tables, cfg.sql.memory_limit, cfg.sql.threads)
            if enabled.get("sql", True)
            else None
        )
        if driver is None and enabled.get("graph", True):
            from nflengine.graph.client import get_driver

            try:
                driver = get_driver(connection_timeout=5.0)
            except Exception:  # NEO4J_PASSWORD not set: graph questions fail soft
                driver = None
        return cls(
            sandbox=sandbox,
            driver=driver,
            database=cfg.graph.database,
            timeout_s=cfg.limits.timeout_s,
            cap=cfg.limits.rows,
            enabled=enabled,
            lock_file=lock_path(paths.runs),
        )

    def run(
        self,
        source: str,
        query: str,
        params: dict[str, Any] | None = None,
        *,
        allow_maps: bool = False,
    ) -> QueryResult:
        """Run through the guard. `allow_maps` (graph only) is for library templates."""
        if self.busy():
            raise Refused("source.busy")
        if source == "sql":
            if self.sandbox is None:
                raise Refused("source.off", "sql")
            return run_sql(self.sandbox, query, params, timeout_s=self.timeout_s, cap=self.cap)
        if source == "graph":
            if self.driver is None:
                raise Refused("source.off", "graph")
            if self.graph_up is not None and not self.graph_up():
                raise Refused("source.down")
            return run_cypher(
                self.driver,
                query,
                params,
                database=self.database,
                timeout_s=self.timeout_s,
                cap=self.cap,
                allow_maps=allow_maps,
            )
        raise ValueError(f"unknown source {source!r}")

    def close(self) -> None:
        if self.driver is not None:
            with contextlib.suppress(Exception):
                self.driver.close()
