"""The query agent's benchmark (`agent.py` answers; this file knows the answers and grades):

    nfl ask bench answers                      # the expected answers, run on today's data
    nfl ask bench run --model local:<name>     # the agent answers every question (resumes)
    nfl ask bench grade <run file>             # scored against the answers file; W&B

Three steps and three files under `{NFL_DATA_ROOT}/ask/agent_bench/`, so the harness never sees
an answer and a run can be graded again later:

1. **`answers.json`**: every golden and held-out question (`config/ask_golden.yaml`,
   `config/ask_holdout.yaml`) with its expected result: an answer question's reference query run
   through the guard on the day's data (`reference_params`: the weekly graph's week, the current
   season), a refuse question's "no query", a clarify question's names. With the data's dates.
2. **`runs/<stamp>_<label>.jsonl`**: one line per question, the agent's `AgentResult` (the last
   query's rows) plus, when the agent ran more than one query, its **first** query re-run here,
   so one pass gives both numbers: with turns and on the first query alone. Written as it goes; a
   run started again skips the ids it has.
3. **`runs/<stamp>_<label>.summary.json`** and a W&B run (group `ask-agent-bench`): accuracy by
   set (main / held-out), by category and by source, with turns and first query alone, how often
   the agent chose the reference's database, calls, latency and cost.

Grading uses Ask's own rules (`evaluate.compare`, scorer 4): order-insensitive rows, extra
columns allowed, numbers within the system's rounding. An answer counts when the last query's
rows match the reference's; a refuse question when the agent ran no query (or refused in code);
a clarify question when it asked "which one?" offering every expected choice.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from pathlib import Path
from typing import Any

from nflengine.ask.evaluate import (
    GOLDEN_PATH,
    HOLDOUT_PATH,
    SCORER_VERSION,
    compare,
    file_hash,
    load_golden,
    reference_params,
)

SETS = {"main": GOLDEN_PATH, "holdout": HOLDOUT_PATH}


def bench_dir(paths) -> Path:
    d = paths.ask / "agent_bench"
    (d / "runs").mkdir(parents=True, exist_ok=True)
    return d


def _jsonable_rows(rows: list[list[Any]]) -> list[list[Any]]:
    from nflengine.ask.agent import _jsonable

    return [[_jsonable(v) for v in r] for r in rows]


# ---- 1. the answers ----------------------------------------------------------------------------


def build_answers(paths=None, log=print) -> Path:
    """Run every reference on today's data into `answers.json`."""
    from nflengine.ask.agent import QueryAgent
    from nflengine.paths import ensure_data_root

    paths = paths or ensure_data_root()
    agent = QueryAgent.build(paths=paths)  # its sources and dates; no model call is made
    out: list[dict[str, Any]] = []
    try:
        for set_name, path in SETS.items():
            for q in load_golden(path):
                item: dict[str, Any] = {
                    "id": q.id,
                    "set": set_name,
                    "category": q.category,
                    "expect": q.expect,
                    "source": q.source,
                    "question": q.question,
                    "choices": list(q.choices),
                }
                if q.expect == "answer":
                    params = reference_params(q, agent.asof, agent.current)
                    for _ in range(3):  # a cold Neo4j page cache times out a first read
                        try:
                            ref = agent.sources.run(q.source, q.reference, params, allow_maps=True)
                            item.update(columns=list(ref.columns), rows=_jsonable_rows(ref.rows),
                                        truncated=ref.truncated)  # fmt: skip
                            item.pop("ref_error", None)
                            break
                        except Exception as e:  # the set's bug: graded wrong, and named here
                            item["ref_error"] = (
                                type(e).__name__
                                + (f": {e.rule}" if hasattr(e, "rule") else "")
                                + (f": {e.kind}" if hasattr(e, "kind") else "")
                            )
                            if getattr(e, "kind", None) != "timeout":
                                break
                out.append(item)
                log(f"{q.id} {q.expect:<7} {len(item.get('rows') or [])} rows"
                    + (f"  ({item['ref_error']})" if "ref_error" in item else ""))  # fmt: skip
    finally:
        agent.close()
    payload = {
        "made_at": dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ"),
        "graph_asof": agent.asof,
        "curated": list(agent.curated),
        "current_season": agent.current,
        "history_asof": agent.history,
        "golden_hash": file_hash(GOLDEN_PATH),
        "holdout_hash": file_hash(HOLDOUT_PATH),
        "questions": out,
    }
    f = bench_dir(paths) / "answers.json"
    f.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    return f


def load_answers(paths) -> dict[str, Any]:
    f = bench_dir(paths) / "answers.json"
    if not f.exists():
        raise FileNotFoundError("no answers file: run `nfl ask bench answers` first")
    return json.loads(f.read_text(encoding="utf-8"))


# ---- 2. a run ----------------------------------------------------------------------------------


def run_bench(model: str | None, label: str, *, sets: tuple[str, ...] = ("main", "holdout"),
              ids: list[str] | None = None, n: int | None = None, resume: Path | None = None,
              paths=None, log=print) -> Path:  # fmt: skip
    """The agent answers every question of `sets`; one JSON line each in the run file."""
    from nflengine.ask.agent import QueryAgent
    from nflengine.ask.llm import OutOfCredits
    from nflengine.paths import ensure_data_root

    paths = paths or ensure_data_root()
    answers = load_answers(paths)
    qs = [q for q in answers["questions"] if q["set"] in sets]
    if ids:
        qs = [q for q in qs if q["id"] in set(ids)]
    if n:
        qs = qs[:n]
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    f = resume or bench_dir(paths) / "runs" / f"{stamp}_{label}.jsonl"
    done = set()
    if f.exists():
        done = {
            json.loads(x)["id"] for x in f.read_text(encoding="utf-8").splitlines() if x.strip()
        }
    agent = QueryAgent.build(model=model, paths=paths)
    try:
        with f.open("a", encoding="utf-8", newline="\n") as out:
            for q in qs:
                if q["id"] in done:
                    continue
                try:
                    res = agent.ask(q["question"])
                except OutOfCredits:
                    log("[red]stopped: out of OpenRouter credits (refill, then resume)[/]")
                    break
                line = {"id": q["id"], "model": model, "label": label, **res.to_dict()}
                first = res.first
                if len(res.steps) > 1 and first is not None and first.kind in ("rows", "empty"):
                    try:  # the first query alone, re-run here (the harness keeps only the last)
                        r1 = agent.sources.run(first.source, first.query)
                        line["first_columns"] = list(r1.columns)
                        line["first_rows"] = _jsonable_rows(r1.rows)
                    except Exception as e:
                        line["first_error"] = type(e).__name__
                out.write(json.dumps(line, default=str) + "\n")
                out.flush()
                log(f"{q['id']} {res.status:<9} {len(res.steps)} calls {res.seconds:6.1f}s "
                    f"{res.source or '-':<8} {len(res.rows)} rows")  # fmt: skip
    finally:
        agent.close()
    return f


# ---- 3. grading --------------------------------------------------------------------------------


def grade_one(q: dict[str, Any], r: dict[str, Any]) -> dict[str, Any]:
    """One question's verdicts: `correct` (with turns), `first_correct` (the first query
    alone), and why."""
    out = {"id": q["id"], "set": q["set"], "category": q["category"], "expect": q["expect"],
           "ref_source": q.get("source"), "source": r.get("source"), "status": r.get("status"),
           "calls": len(r.get("steps") or []), "seconds": r.get("seconds"),
           "cost": sum((c.get("cost") or 0.0) for c in r.get("calls") or []),
           "model_calls": len(r.get("calls") or [])}  # fmt: skip
    steps = r.get("steps") or []
    if q["expect"] == "refuse":
        ok = not steps or r.get("status") == "refused"
        out.update(correct=ok, first_correct=ok, reason="no query" if ok else "ran a query")
        return out
    if q["expect"] == "clarify":
        offered = {m["value"] for c in r.get("choices") or [] for m in c.get("matches") or []}
        missing = [c for c in q.get("choices") or [] if c not in offered]
        ok = r.get("status") == "clarify" and not missing
        out.update(correct=ok, first_correct=ok,
                   reason="clarified" if ok else f"no clarify ({r.get('status')})")  # fmt: skip
        return out
    if "ref_error" in q:
        out.update(
            correct=False, first_correct=False, reason=f"reference failed ({q['ref_error']})"
        )
        return out
    if not r.get("ok"):
        why = r.get("status") if r.get("status") != "answered" else "last query failed"
        out.update(correct=False, first_correct=False, reason=f"no rows ({why})")
        return out
    ok, why = compare(r.get("columns") or [], r.get("rows") or [], q["columns"], q["rows"])
    if len(steps) <= 1:
        f_ok, f_why = ok, why
    elif "first_rows" in r:
        f_ok, f_why = compare(
            r.get("first_columns") or [], r["first_rows"], q["columns"], q["rows"]
        )
    else:
        f_ok, f_why = False, "first query failed"
    out.update(correct=ok, reason=why, first_correct=f_ok, first_reason=f_why,
               chose_ref_source=r.get("source") == q.get("source"))  # fmt: skip
    return out


def _acc(rows: list[dict[str, Any]], key: str = "correct") -> float | None:
    return round(sum(bool(x[key]) for x in rows) / len(rows), 4) if rows else None


def _pct(xs: list[float], p: float) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return round(xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))], 2)


def summarise(graded: list[dict[str, Any]]) -> dict[str, Any]:
    def block(rows: list[dict[str, Any]]) -> dict[str, Any]:
        ans = [x for x in rows if x["expect"] == "answer"]
        return {"n": len(rows), "accuracy": _acc(rows),
                "first_accuracy": _acc(rows, "first_correct"),
                "answer_n": len(ans), "answer_accuracy": _acc(ans),
                "answer_first_accuracy": _acc(ans, "first_correct")}  # fmt: skip

    s: dict[str, Any] = {"scorer_version": SCORER_VERSION}
    for name in ("main", "holdout"):
        rows = [x for x in graded if x["set"] == name]
        if not rows:
            continue
        s[name] = block(rows)
        s[name]["by_category"] = {c: block([x for x in rows if x["category"] == c])
                                  for c in sorted({x["category"] for x in rows})}  # fmt: skip
        s[name]["by_source"] = {src: block([x for x in rows if x["ref_source"] == src])
                                for src in ("sql", "graph", "history")
                                if any(x["ref_source"] == src for x in rows)}  # fmt: skip
        ans = [x for x in rows if x["expect"] == "answer"]
        judged = [x for x in ans if x.get("chose_ref_source") is not None]
        s[name]["chose_ref_source"] = _acc(judged,
                                           "chose_ref_source")  # fmt: skip
    secs = [x["seconds"] for x in graded if x.get("seconds") is not None]
    s["calls_mean"] = round(statistics.mean(x["calls"] for x in graded), 3) if graded else None
    s["latency_p50_s"], s["latency_p95_s"] = _pct(secs, 0.5), _pct(secs, 0.95)
    s["cost_total"] = round(sum(x["cost"] for x in graded), 6)
    s["cost_per_question"] = round(s["cost_total"] / len(graded), 6) if graded else None
    s["errors"] = sum(x["status"] == "error" for x in graded)
    return s


def grade(run_file: Path, paths=None, use_wandb: bool = True, launched_by: str | None = None,
          log=print) -> dict[str, Any]:  # fmt: skip
    from nflengine.paths import ensure_data_root

    paths = paths or ensure_data_root()
    answers = {q["id"]: q for q in load_answers(paths)["questions"]}
    results = [
        json.loads(x) for x in run_file.read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    graded = [grade_one(answers[r["id"]], r) for r in results if r["id"] in answers]
    summary = summarise(graded)
    model = results[0].get("model") if results else None
    label = results[0].get("label") if results else run_file.stem
    summary.update(model=model, label=label, questions=len(graded))
    out = run_file.with_suffix(".summary.json")
    out.write_text(json.dumps({"summary": summary, "graded": graded}, indent=1, default=str),
                   encoding="utf-8")  # fmt: skip
    if use_wandb:
        from nflengine.tracking import init_run

        run = init_run(group="ask-agent-bench", job_type="ask-agent-bench",
                       config={"model": model, "label": label, "run_file": run_file.name,
                               "scorer_version": SCORER_VERSION, "questions": len(graded)},
                       tags=["ask-agent-bench", f"label:{label}"], launched_by=launched_by,
                       name=f"ask-agent-bench-{label}")  # fmt: skip
        flat: dict[str, Any] = {}
        for name in ("main", "holdout"):
            for k, v in (summary.get(name) or {}).items():
                if isinstance(v, int | float) and v is not None:
                    flat[f"{name}/{k}"] = v
        for k in (
            "calls_mean",
            "latency_p50_s",
            "latency_p95_s",
            "cost_total",
            "cost_per_question",
        ):
            if summary.get(k) is not None:
                flat[k] = summary[k]
        run.summary.update(flat)
        summary["wandb_url"] = run.url
        run.finish()
        out.write_text(json.dumps({"summary": summary, "graded": graded}, indent=1, default=str),
                       encoding="utf-8")  # fmt: skip
    return {"summary": summary, "file": out}
