"""Terminal output for `nfl ask` (AE00): one answer, and the bake-off table."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

MAX_ROWS = 15
MAX_COLS = 8


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1e5 else f"{v:,.0f}"
    s = json.dumps(v, default=str, ensure_ascii=False) if isinstance(v, list | dict) else str(v)
    return s if len(s) <= 60 else s[:59] + "…"


def print_result(console: Console, res: Any, show_query: bool = False) -> None:
    """The answer, the rows (first 15), how it was answered, and the query on request."""
    from nflengine.safetext import scrub_bounded as scrub

    style = {"answered": "green", "empty": "yellow", "refused": "yellow", "clarify": "cyan"}.get(
        res.status, "red"
    )
    if res.answer:
        console.print(f"[{style}]{escape(scrub(res.answer, 1200))}[/]")
    if res.error:
        console.print(f"[red]{escape(res.error)}[/]")
    for c in res.choices:
        console.print(f'"{escape(c["text"])}" could be:')
        for o in c["options"]:
            console.print(f"  - {escape(o['label'])}")
    if res.columns and res.rows:
        cols = res.columns[:MAX_COLS]
        t = Table(show_lines=False)
        for c in cols:
            t.add_column(escape(str(c)))
        for r in res.rows[:MAX_ROWS]:
            t.add_row(*(escape(_cell(v)) for v in r[: len(cols)]))
        console.print(t)
        more = []
        if len(res.rows) > MAX_ROWS:
            more.append(f"{len(res.rows) - MAX_ROWS} more rows")
        if len(res.columns) > MAX_COLS:
            more.append(f"{len(res.columns) - MAX_COLS} more columns")
        if res.truncated:
            more.append("capped at the row limit")
        if more:
            console.print(f"[dim]({', '.join(more)})[/]")
    ctx = res.context or {}
    if res.source == "graph" and ctx.get("graph_asof"):
        where = f"weekly graph as of {ctx['graph_asof']['season']} week {ctx['graph_asof']['week']}"
    elif res.source == "sql":
        wk = ctx.get("curated_week")
        where = (
            f"curated data through {ctx.get('current_season')} week {wk}" if wk else "curated data"
        )
    else:
        where = "-"
    route = res.route + (f" {res.template_id}" if res.template_id else "")
    cost = f"${res.cost:.5f}" if res.cost is not None else "$-"
    console.print(
        f"[dim]source: {escape(where)} | route: {escape(route)} | corrections: {res.retries} | "
        f"{res.timings.get('total', 0):.1f} s | {cost} | {len(res.calls)} LLM calls[/]"
    )
    if show_query and res.query:
        console.print(escape(scrub(res.query, 4000)))
        if res.params:
            console.print(f"[dim]params: {escape(json.dumps(res.params, default=str))}[/]")


def bakeoff_table(summaries: list[dict[str, Any]], picked: dict[str, Any] | None) -> Table:
    t = Table(title="Ask the Engine bake-off (newest full run per model and effort)")
    for c in ("model", "effort", "exec acc", "overall", "templates", "refusals", "retries",
              "p50 s", "p95 s", "$ / question", "in bounds", ""):  # fmt: skip
        t.add_column(c)

    def pct(x):
        return "-" if x is None else f"{x:.1%}"

    for s in sorted(summaries, key=lambda s: -(s.get("exec_accuracy") or 0)):
        chosen = picked is not None and s.get("file") == picked.get("file")
        t.add_row(
            escape(str(s.get("model"))),
            escape(str(s.get("reasoning_effort"))),
            pct(s.get("exec_accuracy")),
            pct(s.get("overall_accuracy")),
            pct(s.get("template_hit_rate")),
            pct(s.get("refusal_accuracy")),
            f"{s.get('mean_retries') or 0:.2f}",
            f"{s.get('latency_p50_s') or 0:.1f}",
            f"{s.get('latency_p95_s') or 0:.1f}",
            "-" if s.get("cost_per_question") is None else f"{s['cost_per_question']:.5f}",
            "yes" if s.get("meets_bounds") else "no",
            "[green]picked[/]" if chosen else "",
        )
    return t
