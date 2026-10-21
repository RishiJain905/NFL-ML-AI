"""AE04: score a local model's predictions on the NFL validation examples (`nfl ask verify`).

The trainer (nfl-ask-trainer, inside WSL) generates a reply for every val example after each
epoch, writes `{"id", "output"}` lines, and calls this command through Windows interop; the
scores become the run's `val/*` charts. Everything is checked by running it, as the golden set
is (`evaluate.compare`):

- a **route** example: the reply must parse (`llm.parse_object`) and have the gold action. A
  template decision is right when the template is the gold one *and* its rows equal the gold
  decision's rows (both rendered with the engine's name index and run through the guard: the
  same values written another way, "KC" for "Chiefs", still count); `params_exact` also needs
  the normalised values to be equal. `generate` needs the gold source; `refuse` the action
  (the reason is reported separately).
- a **generate** example: the reply's query runs through the guard (the generator's own:
  literal values, no maps) and its rows must equal the gold query's.

Database error text is never written (the guard's `QueryFailed` keeps its kind and code; only
those go in the scores). Gold results are cached per run: one gold query per example.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from nflengine.ask.evaluate import compare
from nflengine.ask.guard import QueryFailed, Refused
from nflengine.ask.llm import AskLLMError, parse_object
from nflengine.ask.templates import TemplateError, render


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def _err(e: Exception) -> str:
    if isinstance(e, Refused):
        return f"refused:{e.rule}"
    if isinstance(e, QueryFailed):
        return f"failed:{e.kind}:{e.code}"
    if isinstance(e, TemplateError):
        return f"template:{e.code}"
    return type(e).__name__


class Verifier:
    def __init__(self, engine):
        self.engine = engine
        self._gold: dict[str, Any] = {}

    def _template_rows(self, decision: dict[str, Any]):
        t = self.engine.templates.get(str(decision.get("template") or ""))
        if t is None:
            raise TemplateError("unknown_template", "no such template")
        params = decision.get("params") if isinstance(decision.get("params"), dict) else {}
        r = render(t, params, self.engine.names, asof=self.engine.asof)
        out = self.engine.sources.run(t.source, r.query, r.params, allow_maps=bool(t.library))
        rows = [row for row in out.rows if r.keep(out.columns, row)]
        return r, out.columns, rows

    def _gold_rows(self, ex_id: str, verify: dict[str, Any]):
        if ex_id not in self._gold:
            if verify["kind"] == "route":
                r, cols, rows = self._template_rows(verify["gold"])
                self._gold[ex_id] = (r.values, cols, rows)
            else:
                out = self.engine.sources.run(
                    verify["source"], verify["gold"], {}, allow_maps=False
                )
                self._gold[ex_id] = (None, out.columns, out.rows)
        return self._gold[ex_id]

    def score(self, ex: dict[str, Any], output: str) -> dict[str, Any]:
        v = ex["verify"]
        item: dict[str, Any] = {
            "id": ex["id"],
            "task": ex["task"],
            "group": ex.get("group"),
            "json_ok": False,
            "action_ok": None,
            "template_ok": None,
            "params_exact": None,
            "exec_ok": False,
            "reason": "",
        }
        try:
            obj = parse_object(output or "")
        except AskLLMError as e:
            item["reason"] = f"parse:{e}"
            return item
        item["json_ok"] = True
        if v["kind"] == "route":
            return self._score_route(ex, v, obj, item)
        return self._score_query(ex, v, obj, item)

    def _score_route(self, ex, v, obj, item):
        gold = v["gold"]
        action = str(obj.get("action") or "").lower()
        item["action_ok"] = action == gold["action"]
        item["gold_action"] = gold["action"]
        if not item["action_ok"]:
            item["reason"] = f"action {action or '-'} vs {gold['action']}"
            return item
        if gold["action"] == "refuse":
            item["exec_ok"] = True
            item["reason_ok"] = str(obj.get("reason") or "").lower() == gold["reason"]
            item["reason"] = "refused"
            return item
        if gold["action"] == "generate":
            ok = str(obj.get("source") or "").lower() == gold["source"]
            item["exec_ok"] = ok
            item["reason"] = (
                "source ok" if ok else f"source {obj.get('source')} vs {gold['source']}"
            )
            return item
        item["template_ok"] = str(obj.get("template") or "") == gold["template"]
        try:  # first, so every item knows whether its gold result is empty (`exec_acc_nonempty`)
            gold_values, gcols, grows = self._gold_rows(ex["id"], v)
        except Exception as e:
            item["reason"] = f"gold failed ({_err(e)})"
            item["gold_failed"] = True
            return item
        item["gold_rows"] = len(grows)
        if not item["template_ok"]:
            item["reason"] = f"template {obj.get('template')} vs {gold['template']}"
            return item
        try:
            r, cols, rows = self._template_rows(obj)
        except Exception as e:
            item["reason"] = f"pred {_err(e)}"
            return item
        item["params_exact"] = r.values == gold_values
        ok, why = compare(cols, rows, gcols, grows)
        item["exec_ok"], item["reason"] = ok, why
        return item

    def _score_query(self, ex, v, obj, item):
        try:
            _, gcols, grows = self._gold_rows(ex["id"], v)
        except Exception as e:
            item["reason"] = f"gold failed ({_err(e)})"
            item["gold_failed"] = True
            return item
        item["gold_rows"] = len(grows)
        query = obj.get("query")
        if not isinstance(query, str) or not query.strip():
            item["reason"] = "no query" if "refuse" not in obj else "refused"
            return item
        try:
            out = self.engine.sources.run(v["source"], query, {}, allow_maps=False)
        except Exception as e:
            item["reason"] = f"pred {_err(e)}"
            return item
        ok, why = compare(out.columns, out.rows, gcols, grows)
        item["exec_ok"], item["reason"] = ok, why
        return item


def _rate(items: list[dict[str, Any]], key: str) -> float | None:
    vals = [bool(i[key]) for i in items if i.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def summarise(items: list[dict[str, Any]]) -> dict[str, Any]:
    def block(sub: list[dict[str, Any]]) -> dict[str, Any]:
        # an empty gold result matches any query that returns nothing: the stricter number
        # leaves those items out (decisions without rows, refuse / generate, stay in)
        nonempty = [i for i in sub if i.get("gold_rows") != 0]
        return {
            "n": len(sub),
            "exec_acc": _rate(sub, "exec_ok"),
            "exec_acc_nonempty": _rate(nonempty, "exec_ok"),
            "json_ok": _rate(sub, "json_ok"),
            "action_acc": _rate(sub, "action_ok"),
            "template_acc": _rate(sub, "template_ok"),
            "params_exact": _rate(sub, "params_exact"),
        }

    out: dict[str, Any] = {"all": block(items)}
    by_task: dict[str, list] = defaultdict(list)
    by_group: dict[str, list] = defaultdict(list)
    for i in items:
        by_task[i["task"]].append(i)
        by_group[str(i.get("group"))].append(i)
    out["task"] = {k: block(v) for k, v in sorted(by_task.items())}
    out["group"] = {k: block(v) for k, v in sorted(by_group.items())}
    out["gold_failed"] = sum(1 for i in items if i.get("gold_failed"))
    out["reasons"] = dict(Counter(i["reason"].split(" (")[0][:40] for i in items).most_common(15))
    return out


def verify(
    data: Path, preds: Path, out: Path | None = None, engine=None, ids: set[str] | None = None
) -> dict[str, Any]:
    """Score `preds` (`{"id", "output"}` lines) against the examples in `data`; write the
    result (summary + one line per item) to `out` when given."""
    from nflengine.ask.route import AskEngine
    from nflengine.ask.trainset import NoLLM

    examples = {e["id"]: e for e in _read_jsonl(data) if e.get("verify")}
    predictions = {p["id"]: p.get("output") or "" for p in _read_jsonl(preds)}
    if ids:
        predictions = {k: v for k, v in predictions.items() if k in ids}
    own = engine is None
    engine = engine or AskEngine.build(NoLLM())
    try:
        if engine.sources.busy():
            raise RuntimeError("the weekly lock is held: verify after the weekly run")
        verifier = Verifier(engine)
        items = [
            verifier.score(examples[i], text) for i, text in predictions.items() if i in examples
        ]
    finally:
        if own:
            engine.close()
    result = {
        "summary": summarise(items),
        "missing_predictions": sorted(set(examples) - set(predictions))[:50],
        "unknown_ids": sorted(set(predictions) - set(examples))[:50],
        "items": items,
    }
    if out is not None:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return result
