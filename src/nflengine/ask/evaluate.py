"""The golden set and `nfl ask eval` (AE00; W&B job type `ask-eval`).

**The golden set** (`config/ask_golden.yaml`, `version: 1`): 60-100 questions, each with
`id`, `question`, `category` (template | sql | graph | history | trick), `expect` (answer |
refuse | clarify; default answer) and, for an answer, its `source` (sql | graph | history, AE02)
and a hand-written, checked
`reference` query (+ optional `params`). `template` names the template a good router should
pick; `choices` lists the values a clarify question must offer. The reference is run **on the
day's data** at every eval, so the set doesn't go stale; `$asof_season`, `$asof_week` (the
weekly graph's week) and `$current_season` are bound by the evaluator.

**Scoring** (`score`):
- answer: the system's rows equal the reference's, **order-insensitive**; the system may add
  columns, but each reference column must match a distinct system column and the row counts
  must be equal; numbers match at the system column's precision (0.2843 vs 0.284), or as a
  0-1 share written as a percentage; text case-insensitively; lists as sets (a one-item
  list is its item: `collect(...)` of one team is that team). An empty
  reference needs an empty result.
- refuse: the question was refused (by the code check, the router, the generator or the guard).
- clarify: the system asked which one, offering every value in `choices`.

**Metrics** (`summarise`): execution accuracy (answer questions), the template hit rate
(answer questions answered by a template) and template accuracy (the expected template picked),
refusal and clarify accuracy, false refusals, retries, latency p50 / p95 (the whole question,
answer included), cost per question, the answer check's pass rate, accuracy by category, and
whether the model meets the bake-off's bounds (`BOUNDS`).

**The bake-off rule** (`pick`, fixed before the runs: D137): the highest execution accuracy
among configurations with p95 latency <= 8 s and cost <= $0.002 a question; ties (within
0.5 points) go to the faster p95. No configuration within the bounds: no pick (ask Rishi).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from nflengine.settings import CONFIG_DIR

GOLDEN_PATH = CONFIG_DIR / "ask_golden.yaml"
# questions written after the 2026-10-18 audit and never used to tune anything: a check that
# the infrastructure fixes carry over to new questions (`nfl ask eval --set holdout`)
HOLDOUT_PATH = CONFIG_DIR / "ask_holdout.yaml"
GOLDEN_SETS = ("main", "holdout")
STORED_ROWS = 200  # rows of each result kept in the eval file (for `rescore`)
# bumped when `compare` changes: `latest_summaries` (and so `nfl ask pick`) reads only runs
# scored by this version (2: no whole-unit tolerance, shares-only percentages; AE00)
# 3: a same-named column stands only for its namesake (Sol, AE00); 4: a one-item list equals
# its item (['LAC'] vs 'LAC', the 2026-10-18 audit)
SCORER_VERSION = 4
CATEGORIES = ("template", "sql", "graph", "history", "trick")  # history: AE02
ANSWER_SOURCES = ("sql", "graph", "history")
EXPECTS = ("answer", "refuse", "clarify")
RESERVED = ("asof_season", "asof_week", "current_season")
# D137's bounds: still reported (`meets_bounds`, the table's "in bounds"), no longer part
# of the pick since Rishi's rule (D138)
BOUNDS = {"p95_latency_s": 8.0, "cost_per_question": 0.002}
TIE_POINTS = 0.02  # execution accuracies within 2 points tie (one question of 84; D138)
_ID = re.compile(r"g\d{3}")


@dataclass(frozen=True)
class GoldenQuestion:
    id: str
    question: str
    category: str
    expect: str = "answer"
    source: str | None = None
    reference: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    template: str | None = None
    choices: tuple[str, ...] = ()
    notes: str = ""


def load_golden(path: Path | None = None, templates: dict | None = None) -> list[GoldenQuestion]:
    """The golden set, validated (a bad entry raises ValueError naming it)."""
    path = path or GOLDEN_PATH
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("version") != 1:
        raise ValueError(f"{path.name}: version must be 1")
    out: list[GoldenQuestion] = []
    seen: set[str] = set()
    for i, q in enumerate(data.get("questions") or []):
        qid = str(q.get("id") or "")
        where = f"{path.name}: questions[{i}] ({qid or 'no id'})"
        if not _ID.fullmatch(qid) or qid in seen:
            raise ValueError(f"{where}: id must be g + 3 digits and unique")
        seen.add(qid)
        text = str(q.get("question") or "").strip()
        if not text:
            raise ValueError(f"{where}: no question")
        cat, expect = q.get("category"), q.get("expect", "answer")
        if cat not in CATEGORIES or expect not in EXPECTS:
            raise ValueError(f"{where}: category must be {CATEGORIES}, expect {EXPECTS}")
        source, ref = q.get("source"), q.get("reference")
        if expect == "answer":
            if source not in ANSWER_SOURCES or not str(ref or "").strip():
                raise ValueError(f"{where}: an answer question needs source and reference")
        elif ref:
            raise ValueError(f"{where}: a {expect} question has no reference")
        tid = q.get("template")
        if tid and templates is not None and tid not in templates:
            raise ValueError(f"{where}: unknown template {tid!r}")
        choices = tuple(str(c) for c in q.get("choices") or [])
        if choices and expect != "clarify":
            raise ValueError(f"{where}: choices only for clarify questions")
        out.append(
            GoldenQuestion(
                id=qid,
                question=text,
                category=cat,
                expect=expect,
                source=source,
                reference=str(ref) if ref else None,
                params=dict(q.get("params") or {}),
                template=str(tid) if tid else None,
                choices=choices,
                notes=str(q.get("notes") or ""),
            )
        )
    return out


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:12]


# ---- comparing results ----------------------------------------------------------------------


def _is_num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _decimals(v: float) -> int:
    if isinstance(v, int) or float(v).is_integer():
        return 0
    s = repr(float(v))
    if "e" in s:
        return 6
    return min(6, len(s.split(".")[1]))


def _norm_text(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, list | tuple):
        if len(v) == 1:  # a one-item list is its item
            return _norm_text(v[0])
        return tuple(sorted(str(_norm_text(x)) for x in v))
    if isinstance(v, dict):
        return json.dumps(v, sort_keys=True, default=str)
    if isinstance(v, str):
        return " ".join(v.split()).casefold()
    return v


@dataclass(frozen=True)
class _Transform:
    """How a system column stands for a reference column: text compared case-folded; numbers
    at the system column's precision (`decimals`), the reference scaled by `factor` (100 for a
    0-1 share the system wrote as a percentage)."""

    numeric: bool
    decimals: int = 0
    factor: float = 1.0

    def ref_value(self, v: Any) -> Any:
        """The reference's value as the system would show it before rounding."""
        if self.numeric and _is_num(v):
            return float(v) * self.factor
        return _norm_text(v)

    def ref_key(self, v: Any) -> Any:
        """The same, rounded to the system's precision: a sort key that lines rows up."""
        if self.numeric and _is_num(v):
            return round(float(v) * self.factor, self.decimals)
        return _norm_text(v)

    def got(self, v: Any) -> Any:
        if self.numeric and _is_num(v):
            return round(float(v), self.decimals)
        return _norm_text(v)


def _equal(got: Any, ref: Any, t: _Transform) -> bool:
    """`got` = `t.got(v)`, `ref` = `t.ref_value(v)` (unrounded). A number matches when it's the
    same, or the reference's value rounded to the system's precision (within half a unit)
    where that rounding keeps at least one significant digit: 0.2843 shown as 0.28 matches;
    14 vs 15, 2500 vs 2501, 0.28 vs 0.29 and 0.4 shown as 0 don't (the tests worker found an
    earlier tolerance of a whole unit that matched off-by-one counts, AE00)."""
    if t.numeric and isinstance(got, float) and isinstance(ref, float):
        if math.isclose(got, ref, rel_tol=1e-9, abs_tol=1e-12):
            return True
        unit = 10.0 ** (-t.decimals)
        return abs(ref - got) <= 0.5 * unit + 1e-9 and unit <= abs(ref)
    return got == ref


def _unwrap(v: Any) -> Any:
    """A one-item list is its item (`collect()` of one team, `[0.28]`), before the column is
    typed, so a one-item number keeps the numeric rules (Sol, AE00)."""
    while isinstance(v, list | tuple) and len(v) == 1:
        v = v[0]
    return v


def _col_name(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _sort_key(x: Any) -> tuple:
    return (x is None, str(type(x)), x if x is not None else 0)


def _column_transforms(ref_col: list[Any], got_col: list[Any]) -> list[_Transform]:
    """The ways a system column can stand for a reference column (sorted values agree)."""
    ref_nums = [v for v in ref_col if v is not None]
    got_nums = [v for v in got_col if v is not None]
    numeric = (
        bool(ref_nums)
        and all(_is_num(v) for v in ref_nums)
        and bool(got_nums)
        and all(_is_num(v) for v in got_nums)
    )
    if numeric:
        d = max(_decimals(v) for v in got_nums)
        shares = all(0.0 <= float(v) <= 1.0 for v in ref_nums)  # only a share can be a %
        candidates = [_Transform(True, d, f) for f in ((1.0, 100.0) if shares else (1.0,))]
    else:
        candidates = [_Transform(False)]
    out = []
    for t in candidates:
        r = [t.ref_value(v) for v in sorted(ref_col, key=lambda v: _sort_key(t.ref_key(v)))]
        g = sorted((t.got(v) for v in got_col), key=_sort_key)
        if len(r) == len(g) and all(_equal(a, b, t) for a, b in zip(g, r, strict=True)):
            out.append(t)
    return out


def compare(
    got_cols: list[str], got_rows: list[list[Any]], ref_cols: list[str], ref_rows: list[list[Any]]
) -> tuple[bool, str]:
    """(match, reason). See the module docstring for the rules."""
    if len(got_rows) != len(ref_rows):
        return False, f"rows {len(got_rows)} vs {len(ref_rows)}"
    if not ref_rows:
        return True, "both empty"
    if not ref_cols:
        return True, "no reference columns"
    if len(got_cols) < len(ref_cols):
        return False, f"columns {len(got_cols)} vs {len(ref_cols)}"
    ref_rows = [[_unwrap(v) for v in r] for r in ref_rows]
    got_rows = [[_unwrap(v) for v in r] for r in got_rows]
    ref_t = [list(c) for c in zip(*ref_rows, strict=True)]
    got_t = [list(c) for c in zip(*got_rows, strict=True)]
    # A system column named like one reference column can't stand for a different reference
    # column, so a labelled result can't pass with two of its columns swapped (wins / losses;
    # Sol, AE00). Otherwise columns are matched by their values: a same-named column may mean
    # something else (a template's `coach` is the subject, the reference's `coach` the answer).
    ref_names = [_col_name(c) for c in ref_cols]
    got_names = [_col_name(c) for c in got_cols]
    options: list[list[tuple[int, _Transform]]] = []
    for j, rc in enumerate(ref_t):
        other_refs = set(ref_names) - {ref_names[j]}
        pool = [k for k, g in enumerate(got_names) if g not in other_refs]
        opts = [(k, t) for k in pool for t in _column_transforms(rc, got_t[k])]
        if not opts:
            return False, f"no column matches {ref_cols[j]!r}"
        options.append(opts)

    def rows_match(assign: list[tuple[int, _Transform]]) -> bool:
        def key(x):
            return tuple(_sort_key(v) for v in x)

        ref_sorted = sorted(
            ref_rows, key=lambda row: key(t.ref_key(row[j]) for j, (_, t) in enumerate(assign))
        )
        ref_vals = [
            tuple(t.ref_value(row[j]) for j, (_, t) in enumerate(assign)) for row in ref_sorted
        ]
        got_keys = sorted((tuple(t.got(row[k]) for k, t in assign) for row in got_rows), key=key)
        return all(
            all(_equal(a, b, t) for a, b, (_, t) in zip(g, r, assign, strict=True))
            for g, r in zip(got_keys, ref_vals, strict=True)
        )

    tries = [0]

    def search(j: int, used: set[int], assign: list[tuple[int, _Transform]]) -> bool:
        if j == len(options):
            tries[0] += 1
            return rows_match(assign)
        for k, t in options[j]:
            if k in used:
                continue
            if search(j + 1, used | {k}, [*assign, (k, t)]):
                return True
            if tries[0] > 500:
                return False
        return False

    if search(0, set(), []):
        return True, "match"
    return False, "values differ"


# ---- scoring a question ---------------------------------------------------------------------


@dataclass
class Scored:
    id: str
    category: str
    expect: str
    question: str
    status: str
    route: str
    template: str | None
    expected_template: str | None
    source: str | None
    correct: bool
    reason: str
    retries: int
    latency_s: float
    cost: float | None
    rows: int
    ref_rows: int | None
    answer_passed: bool | None
    refused_by: str | None
    error: str | None
    query: str | None
    answer: str | None
    calls: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def reference_params(q: GoldenQuestion, asof: tuple[int, int] | None, current: int) -> dict:
    text = q.reference or ""
    params = dict(q.params)
    reserved = {
        "asof_season": asof[0] if asof else None,
        "asof_week": asof[1] if asof else None,
        "current_season": current,
    }
    for k, v in reserved.items():
        if re.search(rf"\${k}\b", text):
            params[k] = v
    return {k: v for k, v in params.items() if re.search(rf"\${re.escape(k)}\b", text)}


def score(q: GoldenQuestion, res: Any, ref: Any | None, ref_error: str | None = None) -> Scored:
    """Score one AskResult against the question (and its reference QueryResult)."""
    correct, reason = False, ""
    if q.expect == "refuse":
        correct = res.status == "refused"
        reason = "refused" if correct else f"not refused ({res.status})"
    elif q.expect == "clarify":
        offered = {o.get("value") for c in res.choices for o in c.get("options", [])}
        missing = [c for c in q.choices if c not in offered]
        correct = res.status == "clarify" and not missing
        if correct:
            reason = "clarified"
        elif res.status == "clarify":
            reason = f"missing choices {missing}"
        else:
            reason = f"no clarify ({res.status})"
    elif ref is None:
        reason = f"reference failed ({ref_error})"
    elif res.status not in ("answered", "empty"):
        reason = f"no rows ({res.status})"
    else:
        correct, reason = compare(res.columns, res.rows, ref.columns, ref.rows)
    from nflengine.safetext import scrub_bounded as scrub

    return Scored(
        id=q.id,
        category=q.category,
        expect=q.expect,
        question=q.question,
        status=res.status,
        route=res.route,
        template=res.template_id,
        expected_template=q.template,
        source=res.source,
        correct=bool(correct),
        reason=reason,
        retries=res.retries,
        latency_s=float(res.timings.get("total", 0.0)),
        # no LLM call costs nothing; a call without a cost makes the question's cost unknown
        # (summing the rest would understate it; Sol, AE00)
        cost=0.0
        if not res.calls
        else (res.cost if all(c.get("cost") is not None for c in res.calls) else None),
        rows=res.row_count,
        ref_rows=None if ref is None else ref.row_count,
        answer_passed=None if res.answer_check is None else bool(res.answer_check.get("passed")),
        refused_by=(res.refusal or {}).get("by"),
        error=res.error,
        query=scrub(res.query, 2000) if res.query else None,
        answer=scrub(res.answer, 600) if res.answer else None,
        calls=len(res.calls),
    )


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def summarise(rows: list[Scored]) -> dict[str, Any]:
    ans = [r for r in rows if r.expect == "answer"]
    ref = [r for r in rows if r.expect == "refuse"]
    cla = [r for r in rows if r.expect == "clarify"]
    with_t = [r for r in ans if r.expected_template]
    lat = [r.latency_s for r in rows]
    # unknown when any question that made a call has no cost: never read as free, so `pick`
    # sorts such a run last in a tie (Sol, AE00: a local model without prices won ties)
    unknown = [r for r in rows if r.cost is None and (r.calls or 0) > 0]
    costs = [r.cost or 0.0 for r in rows]
    answered = [r for r in rows if r.answer_passed is not None]
    out: dict[str, Any] = {
        "questions": len(rows),
        "exec_accuracy": _mean([float(r.correct) for r in ans]),
        "overall_accuracy": _mean([float(r.correct) for r in rows]),
        "template_hit_rate": _mean([float(r.route == "template") for r in ans]),
        "template_accuracy": _mean([float(r.template == r.expected_template) for r in with_t]),
        "refusal_accuracy": _mean([float(r.correct) for r in ref]),
        "clarify_accuracy": _mean([float(r.correct) for r in cla]),
        "false_refusal_rate": _mean([float(r.status == "refused") for r in ans]),
        "error_rate": _mean([float(r.status == "error") for r in rows]),
        "mean_retries": _mean([float(r.retries) for r in rows]),
        "retry_rate": _mean([float(r.retries > 0) for r in rows]),
        "latency_p50_s": _pct(lat, 0.5),
        "latency_p95_s": _pct(lat, 0.95),
        "cost_per_question": None if unknown else _mean(costs),
        "cost_total": sum(costs),  # of the known costs
        "cost_unknown_questions": len(unknown),
        "answer_check_pass_rate": _mean([float(bool(r.answer_passed)) for r in answered]),
    }
    for cat in CATEGORIES:
        sub = [r for r in rows if r.category == cat]
        out[f"accuracy_{cat}"] = _mean([float(r.correct) for r in sub])
    out["meets_bounds"] = bool(
        out["latency_p95_s"] is not None
        and out["latency_p95_s"] <= BOUNDS["p95_latency_s"]
        and out["cost_per_question"] is not None
        and out["cost_per_question"] <= BOUNDS["cost_per_question"]
    )
    return out


def pick(summaries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Rishi's rule (D138, replacing D137's): the best execution accuracy whatever it costs or
    however slow; accuracies within 2 points of the best tie, and a tie goes to the lowest mean
    cost per question (an unknown cost last), then the lowest p95. Each summary carries `model`
    and `reasoning_effort`."""
    ok = [s for s in summaries if s.get("exec_accuracy") is not None]
    if not ok:
        return None
    best = max(s["exec_accuracy"] for s in ok)
    tied = [s for s in ok if s["exec_accuracy"] >= best - TIE_POINTS - 1e-9]

    def cost(s: dict[str, Any]) -> float:
        c = s.get("cost_per_question")
        return math.inf if c is None else float(c)

    return min(tied, key=lambda s: (cost(s), s.get("latency_p95_s") or math.inf))


# ---- the run --------------------------------------------------------------------------------


def _slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")[:60]


def _history_available(engine) -> bool:
    """Can the history graph answer for this run? Switched on and built (`_history_ready`),
    not being rebuilt, and answering on Bolt, probed once; the answer is pinned on the
    engine's sources for the run, so "auto" doesn't route to a stopped container either."""
    ready = getattr(engine, "_history_ready", None)
    sources = getattr(engine, "sources", None)
    if ready is None or not ready():
        return False
    up = getattr(sources, "history_up", None)
    if up is not None:
        ok = bool(up())
    else:
        from nflengine.history_graph.client import history_up

        ok = history_up()
    busy = getattr(sources, "history_busy", None)
    if ok and callable(busy) and busy():
        ok = False
    if sources is not None:
        sources.history_up = lambda: ok
    return ok


def run_eval(
    model: str,
    *,
    reasoning_effort: str | None | object = ...,
    max_tokens: int | None = None,
    llm_timeout_s: float | None = None,
    n: int | None = None,
    ids: list[str] | None = None,
    use_wandb: bool = True,
    smoke: bool = False,
    golden_set: str = "main",
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    engine_factory: Callable[..., Any] | None = None,
    via: str = "engine",
) -> dict[str, Any]:
    """Run the golden set with one model; save `ask/evals/<stamp>_<model>.json`; log W&B.

    `via="api"` (AE01): every question goes through the control room's `POST /api/ask`
    in-process (`app.ask_service.ApiAsker`: the middleware, the request model, the service,
    the cleaning) and is scored from the JSON the page gets; the file gets an `_api` suffix and
    the W&B run a `via:api` tag, and `nfl ask pick` leaves such runs out."""
    from nflengine.ask.config import ask_config
    from nflengine.ask.llm import OutOfCredits, make_llm
    from nflengine.ask.route import AskEngine, prompt_hash
    from nflengine.ask.templates import TEMPLATES_PATH, load_templates
    from nflengine.paths import ensure_data_root

    cfg = ask_config()
    llm_update: dict[str, Any] = {}
    if max_tokens is not None:  # a high reasoning effort needs room (its budget is a share of it)
        llm_update["max_tokens"] = int(max_tokens)
    if llm_timeout_s is not None:  # and time: a max-effort generation took 14 s on a probe
        llm_update["timeout_s"] = float(llm_timeout_s)
    if llm_update:
        cfg = cfg.model_copy(update={"llm": cfg.llm.model_copy(update=llm_update)})
    paths = ensure_data_root()
    kw = {} if reasoning_effort is ... else {"reasoning_effort": reasoning_effort}
    llm = make_llm(cfg.llm, model=model, **kw)
    engine = (engine_factory or AskEngine.build)(llm, cfg, paths)
    if golden_set not in GOLDEN_SETS:
        raise ValueError(f"golden_set must be one of {GOLDEN_SETS}")
    if via not in ("engine", "api"):
        raise ValueError("via must be engine or api")
    golden_path = GOLDEN_PATH if golden_set == "main" else HOLDOUT_PATH  # looked up now (tests)
    golden = load_golden(golden_path, templates=load_templates(current_season=engine.current))
    golden_total = len(golden)
    # AE02: the history graph's questions need it (switched on, built, a driver, and answering:
    # Sol, a stopped container used to count as available); without it they're skipped and
    # named, the run counts as partial (never compared in `pick`), and "auto" leaves it out
    skipped = [] if _history_available(engine) else [q.id for q in golden if q.source == "history"]
    if skipped:
        log(f"history graph not available: skipping {len(skipped)} ({', '.join(skipped)})")
        golden = [q for q in golden if q.id not in set(skipped)]
    if ids:
        golden = [q for q in golden if q.id in set(ids)]
    if n:
        golden = golden[:n]
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    config = {
        "model": model,
        "provider": cfg.llm.provider,
        "reasoning_effort": llm.effort,
        # the reply cap (`ask.llm.max_tokens`; OpenRouter sizes a reasoning budget from it),
        # named without "token": W&B's secret check refuses such keys (`tracking.py`)
        "max_output": cfg.llm.max_tokens,
        "llm_timeout_s": cfg.llm.timeout_s,
        "routing": llm.routing,
        "prompt_hash": prompt_hash(),
        "golden_set": golden_set,
        "golden_hash": file_hash(golden_path),
        "golden_questions": len(golden),
        "golden_total": golden_total,  # the whole set: a run of fewer is partial
        "partial": len(golden) < golden_total,
        "skipped": skipped,  # AE02: history questions without the history graph
        "history_asof": engine.context().get("history_asof"),
        "templates_hash": file_hash(TEMPLATES_PATH),
        "templates": len(engine.templates),
        "limits": cfg.limits.model_dump(),
        "graph_asof": engine.context()["graph_asof"],
        "curated_week": engine.curated[1],
        "bounds": BOUNDS,
        "scorer_version": SCORER_VERSION,
        "via": via,
    }
    run = None
    if use_wandb:
        from nflengine.tracking import init_run

        run = init_run(
            group="ask-the-engine-smoke" if smoke else "ask-the-engine",
            job_type="ask-eval",
            config=config,
            tags=[
                "ask-eval",
                f"model:{_slug(model)}",
                f"effort:{llm.effort}",
                f"set:{golden_set}",
                f"via:{via}",
                *(["smoke"] if smoke else []),
            ],
            launched_by=launched_by,
            name=f"ask-eval-{_slug(model)}-{llm.effort}"
            + ("" if golden_set == "main" else f"-{golden_set}")
            + ("" if via == "engine" else f"-{via}"),
        )
        for metric in ("eval/running_accuracy", "eval/latency_s", "eval/cost", "eval/correct"):
            run.define_metric(metric, step_metric="eval/question")
    scored: list[Scored] = []
    tables: dict[str, dict[str, Any]] = {}
    stopped: str | None = None
    asker = None
    if via == "api":
        from nflengine.app.ask_service import ApiAsker

        asker = ApiAsker(engine, paths)
    try:
        for i, q in enumerate(golden, start=1):
            ref, ref_error = None, None
            if q.expect == "answer":
                try:
                    ref = engine.sources.run(
                        q.source,
                        q.reference,
                        reference_params(q, engine.asof, engine.current),
                        allow_maps=True,
                    )
                except Exception as e:  # a reference that fails is the set's bug: scored wrong
                    ref_error = type(e).__name__ + (
                        f": {getattr(e, 'rule', '')}" if hasattr(e, "rule") else ""
                    )
            try:
                res = engine.ask(q.question) if asker is None else asker.ask(q.question)
            except OutOfCredits:
                stopped = "out of OpenRouter credits"
                log(f"[red]stopped at {q.id}: out of OpenRouter credits (refill, then re-run)[/]")
                break
            s = score(q, res, ref, ref_error)
            scored.append(s)
            tables[q.id] = {
                "got_columns": res.columns,
                "got_rows": res.rows[:STORED_ROWS],
                "ref_columns": None if ref is None else ref.columns,
                "ref_rows": None if ref is None else ref.rows[:STORED_ROWS],
            }
            acc = sum(r.correct for r in scored) / len(scored)
            log(
                f"{q.id} {'OK ' if s.correct else 'BAD'} {s.status:<8} {s.route:<9} "
                f"{s.latency_s:5.1f}s ${s.cost or 0:.5f}  {s.reason}"
            )
            if run is not None:
                run.log({"eval/question": i, "eval/correct": float(s.correct),
                         "eval/running_accuracy": acc, "eval/latency_s": s.latency_s,
                         "eval/cost": s.cost or 0.0})  # fmt: skip
    finally:
        if asker is not None:
            asker.close()
        engine.close()
    summary = summarise(scored) if scored else {}
    summary.update({"model": model, "reasoning_effort": llm.effort, "stopped": stopped})
    out_dir = paths.ask / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = (
        ("" if golden_set == "main" else f"_{golden_set}")
        + ("" if via == "engine" else f"_{via}")
        + ("_smoke" if smoke else "")
    )
    out_file = out_dir / f"{stamp}_{_slug(model)}_{llm.effort}{suffix}.json"
    payload = {"config": config, "summary": summary, "questions": [s.to_dict() for s in scored],
               "tables": tables, "smoke": smoke, "made_at": stamp,
               "wandb_run_id": run.id if run is not None else None}  # fmt: skip
    tmp = out_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    from nflengine.fsutil import replace_file

    replace_file(tmp, out_file)
    url = None
    if run is not None:
        import wandb

        cols = list(Scored.__dataclass_fields__)
        table = wandb.Table(columns=cols)
        for s in scored:
            d = s.to_dict()
            table.add_data(*[d[c] for c in cols])
        run.log({"eval/questions": table})
        for k, v in summary.items():
            if isinstance(v, int | float | bool) and v is not None:
                run.summary[k] = v
        run.summary["stopped"] = stopped or ""
        url = run.url
        run.finish(exit_code=1 if stopped else 0)
    return {"summary": summary, "file": out_file, "url": url, "scored": scored}


def rescore(path: Path, write: bool = False) -> dict[str, Any]:
    """Re-score a saved eval run with today's `compare` (from its stored tables; answer
    questions whose rows weren't stored keep their old score). Returns the new summary; with
    `write`, the file gets the new scores and summary, `scorer_version` and the old summary as
    `summary_before_rescore` (only when every answer question had its tables stored)."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    tables = d.get("tables") or {}
    rows = []
    for q in d.get("questions") or []:
        q = dict(q)
        tb = tables.get(q["id"])
        stored = q["expect"] == "answer" and tb and tb.get("ref_rows") is not None
        if stored and q["status"] in ("answered", "empty"):
            ok, why = compare(tb["got_columns"], tb["got_rows"], tb["ref_columns"], tb["ref_rows"])
            q["correct"], q["reason"] = ok, why
        rows.append(Scored(**q))
    out = summarise(rows) if rows else {}
    out.update(
        {k: (d.get("summary") or {}).get(k) for k in ("model", "reasoning_effort", "stopped")}
    )
    complete = all(
        q["expect"] != "answer"
        or (tables.get(q["id"]) or {}).get("ref_rows") is not None
        or q["status"] not in ("answered", "empty")
        for q in d.get("questions") or []
    )
    if write and complete:
        from nflengine.fsutil import replace_file

        d["summary_before_rescore"] = d.get("summary")
        d["summary"] = out
        d["questions"] = [r.to_dict() for r in rows]
        d.setdefault("config", {})["scorer_version"] = SCORER_VERSION
        d["rescored_at"] = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        tmp = Path(path).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
        replace_file(tmp, path)
    return out


def rescore_all(paths=None, log=print) -> list[str]:
    """Re-score (and rewrite) every saved run scored by an older `compare` (`nfl ask pick
    --rescore`). Returns the file names it rewrote."""
    from nflengine.paths import ensure_data_root

    paths = paths or ensure_data_root(create_dirs=False)
    done = []
    for f in sorted((paths.ask / "evals").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            not isinstance(d, dict)
            or (d.get("config") or {}).get("scorer_version") == SCORER_VERSION
        ):
            continue
        before = (d.get("summary") or {}).get("exec_accuracy")
        after = rescore(f, write=True).get("exec_accuracy")
        rewritten = json.loads(f.read_text(encoding="utf-8")).get("rescored_at") is not None
        if rewritten:
            done.append(f.name)
        log(
            f"{f.name}: exec accuracy {before} -> {after}"
            + ("" if rewritten else " (not rewritten: tables missing)")
        )
    return done


def latest_summaries(
    paths=None, smoke: bool = False, current: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """The newest complete eval per (model, effort), for `nfl ask pick`: the current scorer, the
    whole golden set, and (with `current`, see `current_hashes`) the same prompts, golden set and
    templates."""
    from nflengine.paths import ensure_data_root

    paths = paths or ensure_data_root(create_dirs=False)
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for f in sorted((paths.ask / "evals").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or not isinstance(d.get("summary") or {}, dict):
            continue  # not an eval file
        s = d.get("summary") or {}
        if (d.get("config") or {}).get("golden_set", "main") != "main":
            continue  # the held-out set is reported on its own, never in the comparison
        if (d.get("config") or {}).get("via", "engine") != "engine":
            continue  # through the app's endpoint (AE01): a check of the API, not a candidate
        if bool(d.get("smoke")) != smoke or s.get("stopped") or not s.get("questions"):
            continue
        cfg = d.get("config") or {}
        if cfg.get("scorer_version") != SCORER_VERSION:
            continue  # scored by an older `compare`: re-run (or `rescore`) it
        total = cfg.get("golden_total", cfg.get("golden_questions"))
        if cfg.get("partial") or s.get("questions") != total:
            continue  # `--n` / `--ids`: never a bake-off entry (Sol, AE00)
        if current is not None and any(cfg.get(k) != v for k, v in current.items()):
            continue  # other prompts, golden set or templates: not comparable
        s = {**s, "file": f.name, "golden_questions": cfg.get("golden_questions")}
        best[(str(s.get("model")), str(s.get("reasoning_effort")))] = s
    return list(best.values())


def current_hashes() -> dict[str, str]:
    """The prompts', golden set's and templates' hashes now: runs made with other ones aren't
    compared with today's (`latest_summaries(current=...)`)."""
    from nflengine.ask.route import prompt_hash
    from nflengine.ask.templates import TEMPLATES_PATH

    return {
        "prompt_hash": prompt_hash(),
        "golden_hash": file_hash(GOLDEN_PATH),
        "templates_hash": file_hash(TEMPLATES_PATH),
    }


def now_s() -> float:
    return time.monotonic()
