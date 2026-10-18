"""The template library (AE00; `config/ask_templates.yaml`).

A template is a hand-written, tested query with typed parameters. The router's LLM only picks
a template and fills its values (the safest text-to-query pattern, README §2); the values are
checked and normalised here, then bound as query parameters (`$name`), never pasted into the
text. The one exception is a `choice` parameter whose choices map to fixed query fragments
(a column name, a sort direction): the fragment comes from the YAML, never from the LLM, and is
substituted for `{name}` in the query. `filters` narrow a query that can't take the value
itself (a library query covers every game of the week): rows are kept when any listed column
equals the parameter's value.

The YAML (`version: 1`):

```yaml
templates:
  - id: hundred_yard_games_vs_team      # [a-z0-9_]+, unique
    source: sql                         # sql (curated tables) | graph (the weekly graph)
    title: 100-yard games against a team
    description: Players with the most games of N+ yards against one team.
    examples: ["Which receivers have the most 100-yard games against Dallas since 2020?"]
    params:
      team: {type: team, description: the opponent}
      first_season: {type: season, default: 2010, min: 2010}
      last_season: {type: season, default: current}
      stat: {type: choice, default: receiving,
             choices: {receiving: pg.receiving_yards, rushing: pg.rushing_yards}}
      threshold: {type: int, default: 100, min: 1, max: 700}
    query: |
      SELECT ... WHERE {stat} >= $threshold AND pg.season BETWEEN $first_season AND $last_season
  - id: former_teammates_this_week
    source: graph
    library: q6_former_teammates        # the query is graph/queries/<library>.cypher, and its
                                        # LIBRARY extras are bound as constants
    asof: true                          # $season / $week = the weekly graph's week, not the LLM's
    params:
      team: {type: team, default: null}
    filters: {team: [team, opponent]}   # keep rows whose team or opponent column is the value
```

Parameter types: `team` (a code, name, nickname or city -> the canonical code), `player` (a
name or a GSIS id -> the GSIS id), `coach` (a name -> the name as the data spells it),
`season` (min / max; `current` = `seasons.current`), `week` (1-22), `int` / `float` (min /
max), `choice` (a list of allowed strings, bound as text; or a mapping of choice -> fixed
fragment, substituted), `text` (a short search string, bound, at most 60 characters). A
parameter with a `default` is optional. `constants` are bound as they are and aren't shown to
the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml

from nflengine.settings import CONFIG_DIR

TEMPLATES_PATH = CONFIG_DIR / "ask_templates.yaml"
PARAM_TYPES = ("team", "player", "coach", "season", "week", "int", "float", "choice", "text")
_ID = re.compile(r"[a-z0-9_]{1,64}")
_PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")
_GSIS = re.compile(r"00-\d{7}")
TEXT_MAX = 60


class TemplateError(ValueError):
    """A template choice or a parameter value that can't be used. `code` is fixed
    (`unknown_template`, `missing`, `invalid`, `out_of_range`, `ambiguous`, `not_found`,
    `unknown_param`); `message` is fixed text plus the parameter's name; `choices` lists the
    candidates of an ambiguous name (display strings from the curated data)."""

    def __init__(
        self,
        code: str,
        message: str,
        param: str | None = None,
        choices: list[str] | None = None,
        values: list[str] | None = None,
    ):
        self.code = code
        self.param = param
        self.message = message
        self.choices = choices or []  # labels
        self.values = values or []  # the matching ids / codes, same order
        super().__init__(message)


class Resolver(Protocol):
    """What rendering needs from `names.py` (a fake in tests)."""

    def team(self, text: str) -> str | None: ...

    def player(self, text: str) -> Any: ...  # names.Resolution

    def coach(self, text: str) -> Any: ...  # names.Resolution


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    description: str = ""
    default: Any = None
    required: bool = True
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] = ()
    fragments: dict[str, str] | None = None  # choice -> fixed query text, for {name}

    def signature(self) -> str:
        """`name: type (...)`, for the router's prompt."""
        bits = [self.type]
        if self.choices:
            bits.append("one of " + "|".join(self.choices))
        if self.min is not None or self.max is not None:
            lo = "" if self.min is None else f"{self.min:g}"
            hi = "" if self.max is None else f"{self.max:g}"
            bits.append(f"{lo}..{hi}")
        if not self.required:
            bits.append(f"default {self.default}")
        desc = f": {self.description}" if self.description else ""
        return f"{self.name} ({', '.join(bits)}){desc}"


@dataclass(frozen=True)
class Template:
    id: str
    source: Literal["sql", "graph"]
    title: str
    description: str
    query: str
    params: tuple[Param, ...] = ()
    examples: tuple[str, ...] = ()
    constants: dict[str, Any] = field(default_factory=dict)
    asof: bool = False
    library: str | None = None
    filters: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def param(self, name: str) -> Param | None:
        return next((p for p in self.params if p.name == name), None)

    def prompt_line(self) -> str:
        sig = "; ".join(p.signature() for p in self.params if not (self.asof and _asof_param(p)))
        ex = f' e.g. "{self.examples[0]}"' if self.examples else ""
        head = f"- {self.id} [{self.source}] {self.title}: {self.description}"
        return f"{head} params: {sig or 'none'}.{ex}"


@dataclass(frozen=True)
class Rendered:
    template_id: str
    source: str
    query: str
    params: dict[str, Any]
    values: dict[str, Any]  # the normalised values (what the answer and the UI show)
    # (columns, value): a row is kept when any of the columns it has equals the value
    filters: tuple[tuple[tuple[str, ...], Any], ...] = ()

    def keep(self, columns: list[str], row: list[Any]) -> bool:
        for cols, value in self.filters:
            idx = [columns.index(c) for c in cols if c in columns]
            if idx and not any(row[i] == value for i in idx):
                return False
        return True


def _asof_param(p: Param) -> bool:
    return p.name in ("season", "week")


# ---- loading --------------------------------------------------------------------------------


def _param(name: str, spec: dict[str, Any], current_season: int, where: str) -> Param:
    if not _ID.fullmatch(name):
        raise ValueError(f"{where}: bad parameter name {name!r}")
    kind = spec.get("type")
    if kind not in PARAM_TYPES:
        raise ValueError(f"{where}.{name}: type must be one of {PARAM_TYPES}")
    choices: tuple[str, ...] = ()
    fragments: dict[str, str] | None = None
    if kind == "choice":
        raw = spec.get("choices")
        if isinstance(raw, dict) and raw:
            fragments = {str(k): str(v) for k, v in raw.items()}
            choices = tuple(fragments)
        elif isinstance(raw, list) and raw:
            choices = tuple(str(c) for c in raw)
        else:
            raise ValueError(f"{where}.{name}: a choice needs a list or mapping of choices")
    lo, hi = spec.get("min"), spec.get("max")
    if kind == "season":
        lo = 1999 if lo is None else lo
        hi = current_season if hi in (None, "current") else hi
    if kind == "week":
        lo, hi = (1 if lo is None else lo), (22 if hi is None else hi)
    default = spec.get("default")
    if default == "current" and kind == "season":
        default = current_season
    required = "default" not in spec
    if not required and default is not None and kind == "choice" and str(default) not in choices:
        raise ValueError(f"{where}.{name}: default {default!r} isn't a choice")
    return Param(
        name=name,
        type=kind,
        description=str(spec.get("description") or ""),
        default=default,
        required=required,
        min=None if lo is None else float(lo),
        max=None if hi is None else float(hi),
        choices=choices,
        fragments=fragments,
    )


def _library_query(name: str) -> tuple[str, dict[str, Any]]:
    from nflengine.graph.queries import LIBRARY, QUERY_DIR

    if name not in LIBRARY:
        raise ValueError(f"unknown library query {name!r}")
    return (QUERY_DIR / f"{name}.cypher").read_text(encoding="utf-8"), dict(LIBRARY[name])


def load_templates(
    path: Path | None = None, current_season: int | None = None
) -> dict[str, Template]:
    """{id: Template} from the YAML, validated (bad entries raise ValueError with where)."""
    if current_season is None:
        from nflengine.settings import get_config

        current_season = get_config().seasons.current
    path = path or TEMPLATES_PATH
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("version") != 1:
        raise ValueError(f"{path.name}: version must be 1")
    out: dict[str, Template] = {}
    for i, t in enumerate(data.get("templates") or []):
        where = f"{path.name}: templates[{i}]"
        tid = str(t.get("id") or "")
        if not _ID.fullmatch(tid):
            raise ValueError(f"{where}: bad id {tid!r}")
        if tid in out:
            raise ValueError(f"{where}: duplicate id {tid!r}")
        where = f"{path.name}: {tid}"
        source = t.get("source")
        if source not in ("sql", "graph"):
            raise ValueError(f"{where}: source must be sql or graph")
        constants = dict(t.get("constants") or {})
        library = t.get("library")
        if library:
            if source != "graph":
                raise ValueError(f"{where}: a library query is a graph template")
            query, lib_constants = _library_query(str(library))
            constants = {**lib_constants, **constants}
        else:
            query = str(t.get("query") or "")
        if not query.strip():
            raise ValueError(f"{where}: no query")
        if source == "graph":  # comments can name {x} or $x without being slots (the library's)
            from nflengine.ask.guard import strip_cypher_comments

            query = strip_cypher_comments(query)
        params = tuple(
            _param(str(n), dict(s or {}), current_season, where)
            for n, s in (t.get("params") or {}).items()
        )
        asof = bool(t.get("asof", False))
        if asof and source != "graph":
            raise ValueError(f"{where}: asof applies to graph templates only")
        names = {p.name for p in params}
        for ph in [] if library else _PLACEHOLDER.findall(query):  # a library file has no slots
            p = next((p for p in params if p.name == ph), None)
            if p is None or p.fragments is None:
                raise ValueError(f"{where}: {{{ph}}} needs a choice parameter with fragments")
        if library and any(p.fragments for p in params):
            raise ValueError(f"{where}: a library template can't use fragment choices")
        if asof:
            names |= {"season", "week"}
        for ref in set(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", query)):
            if ref not in names and ref not in constants:
                raise ValueError(f"{where}: ${ref} isn't a parameter or a constant")
        filters: dict[str, tuple[str, ...]] = {}
        for pname, cols in (t.get("filters") or {}).items():
            if not any(p.name == pname for p in params):
                raise ValueError(f"{where}: filter {pname!r} isn't a parameter")
            if (
                not isinstance(cols, list)
                or not cols
                or not all(_ID.fullmatch(str(c)) for c in cols)
            ):
                raise ValueError(f"{where}: filter {pname!r} needs a list of column names")
            filters[str(pname)] = tuple(str(c) for c in cols)
        examples = tuple(str(e) for e in t.get("examples") or [])
        if not examples:
            raise ValueError(f"{where}: give at least one example question")
        out[tid] = Template(
            id=tid,
            source=source,
            title=str(t.get("title") or tid),
            description=str(t.get("description") or ""),
            query=query,
            params=params,
            examples=examples,
            constants=constants,
            asof=asof,
            library=str(library) if library else None,
            filters=filters,
        )
    return out


# ---- values ---------------------------------------------------------------------------------


def _number(p: Param, raw: Any, integer: bool) -> int | float:
    if isinstance(raw, bool):
        raise TemplateError("invalid", f"{p.name} must be a number", p.name)
    try:
        v = float(str(raw).strip()) if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError):
        raise TemplateError("invalid", f"{p.name} must be a number", p.name) from None
    if v != v or v in (float("inf"), float("-inf")):
        raise TemplateError("invalid", f"{p.name} must be a number", p.name)
    if integer:
        if v != int(v):
            raise TemplateError("invalid", f"{p.name} must be a whole number", p.name)
        v = int(v)
    if (p.min is not None and v < p.min) or (p.max is not None and v > p.max):
        lo = "" if p.min is None else f"{p.min:g}"
        hi = "" if p.max is None else f"{p.max:g}"
        raise TemplateError("out_of_range", f"{p.name} must be between {lo} and {hi}", p.name)
    return v


def _resolved(p: Param, res: Any) -> Any:
    status = getattr(res, "status", None)
    if status == "unique":
        return res.value
    if status == "ambiguous":
        raise TemplateError(
            "ambiguous",
            f"{p.name} matches more than one {p.type}",
            p.name,
            [m.label for m in res.matches],
            [m.value for m in res.matches],
        )
    raise TemplateError("not_found", f"no {p.type} matches {p.name}", p.name)


def normalise(p: Param, raw: Any, resolver: Resolver | None) -> Any:
    """One parameter's value, checked and normalised (raises TemplateError)."""
    if p.type in ("int", "season", "week"):
        return _number(p, raw, integer=True)
    if p.type == "float":
        return _number(p, raw, integer=False)
    text = str(raw).strip() if raw is not None else ""
    if not text:
        raise TemplateError("missing", f"{p.name} is empty", p.name)
    if p.type == "choice":
        low = {c.lower(): c for c in p.choices}
        if text.lower() not in low:
            raise TemplateError("invalid", f"{p.name} must be one of the listed choices", p.name)
        return low[text.lower()]
    if p.type == "text":
        if len(text) > TEXT_MAX:
            raise TemplateError("invalid", f"{p.name} is longer than {TEXT_MAX} characters", p.name)
        return text
    if resolver is None:
        raise TemplateError("invalid", f"{p.name} needs the name resolver", p.name)
    if p.type == "team":
        code = resolver.team(text)
        if code is None:
            raise TemplateError("not_found", f"no team matches {p.name}", p.name)
        return code
    if p.type == "player":
        return _resolved(p, resolver.player(text))
    if p.type == "coach":
        return _resolved(p, resolver.coach(text))
    raise TemplateError("invalid", f"{p.name} has an unknown type", p.name)  # unreachable


def render(
    t: Template,
    values: dict[str, Any],
    resolver: Resolver | None = None,
    asof: tuple[int, int] | None = None,
) -> Rendered:
    """The template's query and bound parameters for these values (raises TemplateError).

    `asof` = the weekly graph's (season, week), required for an `asof` template; values the
    LLM gave for season / week are ignored there."""
    values = dict(values or {})
    unknown = sorted(k for k in values if t.param(k) is None)
    if t.asof:
        unknown = [k for k in unknown if k not in ("season", "week")]
    if unknown:
        raise TemplateError("unknown_param", f"{t.id} has no parameter {unknown[0]}", unknown[0])
    bound: dict[str, Any] = dict(t.constants)
    shown: dict[str, Any] = {}
    for p in t.params:
        if t.asof and _asof_param(p):
            continue
        raw = values.get(p.name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if p.required:
                raise TemplateError("missing", f"{p.name} is required", p.name)
            v = p.default
        else:
            v = normalise(p, raw, resolver)
        shown[p.name] = v
        if p.fragments is None:
            bound[p.name] = v
    if t.asof:
        if asof is None:
            raise TemplateError("missing", f"{t.id} needs the weekly graph's week", "week")
        bound["season"], bound["week"] = int(asof[0]), int(asof[1])
        shown["season"], shown["week"] = int(asof[0]), int(asof[1])

    def fragment(m: re.Match[str]) -> str:
        p = t.param(m.group(1))
        assert p is not None and p.fragments is not None  # checked at load
        return p.fragments[str(shown[p.name])]

    query = t.query if t.library else _PLACEHOLDER.sub(fragment, t.query)
    used = set(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", query))
    return Rendered(
        template_id=t.id,
        source=t.source,
        query=query,
        params={k: v for k, v in bound.items() if k in used},
        values=shown,
        filters=tuple(
            (cols, shown[name]) for name, cols in t.filters.items() if shown.get(name) is not None
        ),
    )


def is_gsis(text: str) -> bool:
    return bool(_GSIS.fullmatch(str(text).strip()))
