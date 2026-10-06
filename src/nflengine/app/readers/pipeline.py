"""The Pipeline tab: a week's run step by step, or the plan for the current week's run.

- A run with records (P07+): steps from `weekly_run.json` (each step's latest state) with
  `run_summary.json`'s seconds and `this_run`; the `records` step is the summary itself.
- A run before P07 (2026 week 4): `weekly_run.json` only; seconds from each step's start
  and finish; `records` is "none" (not recorded) and `no_run_records` is set.
- The current week before its run: every step pending, with expected times taken from the
  newest published week's real times (defaults for a first run), and the calendar's plan
  as the log. The dry run itself is CR02's pre-flight.

A week can have several sittings (a run, then resumes): `sittings` counts the gaps between
one step's finish and the next one's start, and the rebuilt log shows a `--from-step` line
for each resume.

From CR02 every run writes `events/<run-id>.jsonl` (`ops/events.py`): when each sitting has
one, the log is the runs' own console lines (`log_source: "events"`) instead of the rebuilt one.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from nflengine.app.readers.common import (
    STEP_NAMES,
    clean,
    num,
    parse_time,
    read_json,
    summary_is_current,
    wandb_urls,
    without_wandb,
)
from nflengine.app.readers.meta import lock_target, plan_dict
from nflengine.app.readers.weeks import NOT_READY_TEXT, checks_line
from nflengine.ops.calendar import EASTERN, WeekPlan
from nflengine.paths import DataPaths
from nflengine.weekly import already_published, read_state

# Expected seconds before any published week has real times (the mockup's estimates).
DEFAULT_SECONDS = {
    "ingest": 25.0,
    "ready": 1.0,
    "curate": 16.0,
    "ratings": 4.0,
    "game": 15.0,
    "graph": 150.0,
    "player": 95.0,
    "digest": 540.0,
    "records": 20.0,
}
ALL_STEPS = (*STEP_NAMES, "records")
SITTING_GAP_S = 120  # a gap this long between steps means a separate invocation
DETAIL_LIMIT = 220


_parse = parse_time  # naive = this machine's local time (pre-P07)


def _seconds(step: dict[str, Any]) -> float | None:
    a, b = _parse(step.get("started")), _parse(step.get("finished"))
    return round((b - a).total_seconds(), 1) if a and b else None


def step_times(
    paths: DataPaths, season: int, week: int
) -> tuple[dict[str, float | None], float | None]:
    """Real seconds per step of a week's run, and the records' seconds (None before P07)."""
    run_dir = paths.run_dir(season, week)
    state = read_state(run_dir / "weekly_run.json") or {}
    steps = state.get("steps") or {}
    summary = read_json(run_dir / "run_summary.json")
    if not summary_is_current(summary, steps):
        summary = None
    out: dict[str, float | None] = {}
    for name in STEP_NAMES:
        st = steps.get(name) or {}
        sec = num(((summary or {}).get("steps") or {}).get(name, {}).get("seconds"))
        out[name] = sec if sec is not None else _seconds(st)
    records = None
    if summary is not None:
        fin = _parse(summary.get("finished"))
        last = max(
            (t for st in steps.values() if (t := _parse((st or {}).get("finished")))),
            default=None,
        )
        if fin and last:
            records = max(0.0, round((fin - last).total_seconds(), 1))
    return out, records


def expected_times(
    paths: DataPaths, season: int, before_week: int
) -> tuple[dict[str, float], dict[str, int] | None]:
    """Each step's expected seconds: the newest published week before `before_week` with
    real times; the defaults for anything it lacks."""
    out = dict(DEFAULT_SECONDS)
    season_dir = paths.runs / str(season)
    weeks = (
        sorted(
            (int(d.name[4:]) for d in season_dir.glob("week*") if d.name[4:].isdigit()),
            reverse=True,
        )
        if season_dir.exists()
        else []
    )
    for w in weeks:
        if w >= before_week:
            continue
        run_dir = paths.run_dir(season, w)
        if not already_published(run_dir / "weekly_run.json", paths.report_path(season, w)):
            continue
        secs, records = step_times(paths, season, w)
        for name, s in secs.items():
            if s is not None:
                out[name] = s
        if records is not None:
            out["records"] = records
        return out, {"season": season, "week": w}
    return out, None


def _sittings(steps: dict[str, Any]) -> list[list[str]]:
    """Steps grouped by invocation, in time order."""
    order = {n: i for i, n in enumerate(STEP_NAMES)}
    timed = sorted(
        (t, order.get(name, 99), name)
        for name, st in steps.items()
        if (t := _parse((st or {}).get("started")))
    )
    groups: list[list[str]] = []
    last_end: dt.datetime | None = None
    for t, _, name in timed:
        if last_end is None or (t - last_end).total_seconds() > SITTING_GAP_S or not groups:
            groups.append([name])
        else:
            groups[-1].append(name)
        end = _parse((steps.get(name) or {}).get("finished")) or t
        last_end = end if last_end is None else max(last_end, end)
    return groups


def _clock(text: Any) -> str:
    t = _parse(text)
    return t.strftime("%H:%M:%S") if t else ""


def _log_detail(detail: str | None, paths: DataPaths) -> str:
    """A step detail as the log shows it: relative paths, W&B runs as `W&B <id>`, its
    `;`-separated parts joined with a middle dot."""
    if not detail:
        return ""
    text = without_wandb(clean(detail, paths, 600) or "")
    return " · ".join(p.strip() for p in text.split(";") if p.strip())


def _rebuilt_log(
    paths: DataPaths,
    season: int,
    week: int,
    steps: dict[str, Any],
    published: bool,
    checks: dict[str, Any] | None,
    failed_step: str | None,
) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    base = f"$ nfl weekly run --season {season} --week {week}"
    for i, group in enumerate(_sittings(steps)):
        first = group[0]
        cmd = base if i == 0 and first == STEP_NAMES[0] else f"{base} --from-step {first}"
        note = "" if i == 0 else "   (resumed)"
        lines.append({"ts": _clock(steps[first].get("started")), "text": cmd + note, "cls": "acc"})
        for name in group:
            st = steps[name] or {}
            status = st.get("status")
            cls = {"ok": "ok", "failed": "err", "degraded": "warn", "running": "dim"}.get(
                status, ""
            )
            mark = {"failed": "✕ ", "degraded": "! ", "running": "● "}.get(status, "")
            text = f"{mark}{name}: {_log_detail(st.get('detail'), paths)}".rstrip(": ")
            lines.append({"ts": _clock(st.get("started")), "text": text, "cls": cls})
    digest = steps.get("digest") or {}
    if published:
        rel = f"reports/{season}/week{week:02d}-digest.md"
        line = checks_line(checks)
        lines.append(
            {
                "ts": _clock(digest.get("finished")),
                "text": f"published {rel}" + (f" · {line.lower()}" if line else ""),
                "cls": "ok",
            }
        )
    elif failed_step:
        st = steps.get(failed_step) or {}
        lines.append(
            {
                "ts": _clock(st.get("finished")),
                "text": f"failed: {season} week {week:02d} at the {failed_step} step",
                "cls": "err",
            }
        )
    return lines


def events_log(paths: DataPaths, run_dir, sittings: int) -> list[dict[str, str]] | None:
    """The week's runs as their console printed them (CR02 events files, oldest first), or
    None when some sitting has no events file (it ran before CR02)."""
    from nflengine.app.runner import read_events

    folder = run_dir / "events"
    try:
        files = sorted(folder.glob("*.jsonl")) if folder.exists() else []
    except OSError:
        files = []
    runs = [evs for f in files if (evs := read_events(f))]
    runs = [evs for evs in runs if any(e.get("type") == "step_start" for e in evs)]
    if not runs or len(runs) < sittings:
        return None
    lines: list[dict[str, str]] = []
    for evs in runs:
        for e in evs:
            kind = e.get("type")
            ts = _clock(e.get("t"))
            if kind == "run_start":
                note = "   (resumed)" if e.get("from_step") else ""
                text = f"$ {clean(e.get('command'), paths, 200)}{note}"
                lines.append({"ts": ts, "text": text, "cls": "acc"})
            elif kind == "log":
                cls = e.get("cls") if e.get("cls") in ("ok", "err", "warn", "dim", "acc") else ""
                lines.append({"ts": ts, "text": clean(e.get("text"), paths, 600) or "", "cls": cls})
            elif kind == "run_end":
                ok = e.get("status") in ("ok", "degraded")
                code = e.get("exit_code")
                lines.append(
                    {
                        "ts": ts,
                        "text": f"{e.get('status')}: {clean(e.get('message'), paths, 300)} "
                        f"(exit {code})",
                        "cls": "ok" if ok else "warn" if e.get("status") == "not_ready" else "err",
                    }
                )
    return lines


def _latest_started(steps: dict[str, Any]) -> str | None:
    timed = [
        (t, i, name)
        for i, name in enumerate(STEP_NAMES)
        if (t := _parse((steps.get(name) or {}).get("started"))) is not None
    ]
    return max(timed)[2] if timed else None


def latest_failure(steps: dict[str, Any], running: bool) -> str | None:
    """The step the week's **latest** invocation failed at: of the `failed` steps and those a
    dead run left `running` (interrupted), the one that started last. An older sitting's failure
    that a later retry went past (or died before reaching) is not the one to resume from (Sol
    review, CR02)."""
    failing = []
    for i, name in enumerate(STEP_NAMES):
        st = steps.get(name) or {}
        status = st.get("status")
        if status == "failed" or (status == "running" and not running):
            failing.append(
                (_parse(st.get("started")) or dt.datetime.min.replace(tzinfo=dt.UTC), i, name)
            )
    return max(failing)[2] if failing else None


def _et(t: dt.datetime | None, fmt: str = "%a %Y-%m-%d %H:%M ET") -> str:
    return t.astimezone(EASTERN).strftime(fmt) if t else "—"


def plan_log(plan: WeekPlan, lock: dict[str, Any]) -> list[dict[str, str]]:
    """The calendar's plan for the current week, in the dry run's words (no command runs)."""
    p = plan_dict(plan) or {}
    s = plan.slate
    ts = plan.now.astimezone(EASTERN).strftime("%H:%M:%S")
    lines = [
        {
            "ts": ts,
            "text": "the calendar's plan for this week (the dry run comes with CR02)",
            "cls": "acc",
        },
        {
            "ts": ts,
            "text": f"{_et(plan.now)}: {plan.season} week {plan.week} ({plan.phase})",
            "cls": "",
        },
    ]
    if plan.previous_week is not None:
        done = plan.previous_complete
        lines.append(
            {
                "ts": ts,
                "text": f"previous week {plan.previous_week}: {plan.previous_final}/"
                f"{plan.previous_games} final"
                + (
                    ", complete"
                    if done
                    else " in the local schedule snapshot; the run refreshes it first"
                ),
                "cls": "ok" if done else "warn",
            }
        )
    if plan.deadline is not None:
        lines.append(
            {
                "ts": ts,
                "text": f"deadline (first kickoff): {_et(plan.deadline)}, "
                f"{p.get('hours_to_deadline')} h from now",
                "cls": "",
            }
        )
    if plan.retry_until is not None:
        lines.append(
            {
                "ts": ts,
                "text": f"retry window for 'not ready' ends {_et(plan.retry_until)}",
                "cls": "dim",
            }
        )
    if s is not None:
        special = ", ".join(x for x in s.special if x != "byes") or "none"
        lines.append(
            {
                "ts": ts,
                "text": f"slate: {s.games} games on {', '.join(s.days)}; special: {special}",
                "cls": "",
            }
        )
        byes = ", ".join(s.teams_on_bye) or "none"
        lock_text = "held" if lock.get("held") else "free"
        lines.append({"ts": ts, "text": f"byes: {byes} · lock: {lock_text}", "cls": "dim"})
    lines.append(
        {
            "ts": ts,
            "text": f"a run would do {', '.join(STEP_NAMES)} for {plan.season} week {plan.week:02d}"
            if plan.week is not None
            else "no week to run",
            "cls": "ok",
        }
    )
    for n in p.get("notes") or []:
        lines.append({"ts": ts, "text": n, "cls": "warn"})
    return lines


def _tiles(run_dir, checks, step_secs: dict[str, float | None]) -> dict[str, Any]:
    raw = read_json(run_dir / "raw_llm_output.json") or {}
    calls = [c for c in raw.get("calls") or [] if isinstance(c, dict)]
    final = (checks or {}).get("final") or {}
    final_checks = [c for c in final.get("checks") or [] if isinstance(c, dict)]
    costs = [num(c.get("cost")) for c in calls]
    total = sum(s for s in step_secs.values() if s is not None)
    digest = step_secs.get("digest")
    return {
        "checks_passed": sum(1 for c in final_checks if c.get("passed")) if final_checks else None,
        "checks_total": len(final_checks) if final_checks else None,
        "first_time": (bool(checks.get("passed")) and not checks.get("regenerated"))
        if checks
        else None,
        "llm_cost": round(sum(c for c in costs if c is not None), 6) if calls else None,
        "llm_calls": len(calls),
        "llm_provider": clean(calls[0].get("provider"), None, 60) if calls else None,
        "digest_share": round(digest / total, 4) if digest and total else None,
    }


def get_pipeline(
    paths: DataPaths,
    season: int,
    week: int,
    plan: WeekPlan | None,
    lock: dict[str, Any],
) -> dict[str, Any]:
    run_dir = paths.run_dir(season, week)
    state = read_state(run_dir / "weekly_run.json") or {}
    steps: dict[str, Any] = {k: v for k, v in (state.get("steps") or {}).items() if k in STEP_NAMES}
    summary = read_json(run_dir / "run_summary.json")
    stale_summary = summary is not None and not summary_is_current(summary, steps)
    if stale_summary:  # an earlier sitting's records; the last run died before writing its own
        summary = None
    checks = read_json(run_dir / "checks.json")
    is_current = plan is not None and plan.season == season and plan.week == week
    running = lock_target(lock, plan) == (season, week)
    expected, expected_from = expected_times(paths, season, week)
    published = already_published(run_dir / "weekly_run.json", paths.report_path(season, week))

    if summary is not None:
        not_ready = summary.get("status") == "not_ready"
    else:
        failed = [k for k, v in steps.items() if (v or {}).get("status") == "failed"]
        ready_detail = str((steps.get("ready") or {}).get("detail") or "")
        not_ready = failed == ["ready"] and bool(NOT_READY_TEXT.search(ready_detail))
    # an older sitting's not-ready stop doesn't describe a later retry that went past it or
    # died (Sol review, CR02): only when `ready` is the latest step to have started
    not_ready = not_ready and _latest_started(steps) == "ready"

    base = {
        "season": season,
        "week": week,
        "expected_from": expected_from,
        "error": None,
        "failed_step": None,
        "sittings": 0,
        "step_seconds": None,
        "no_run_records": False,
        "tiles": _tiles(run_dir, None, {}),
    }

    # the current week before its run (or after a run that stopped as "not ready")
    if is_current and (not steps or (not_ready and not published and not running)):
        rows = [
            {
                "step": name,
                "status": "pending",
                "seconds": None,
                "expected_seconds": expected[name],
                "started": None,
                "finished": None,
                "detail": None,
                "this_run": None,
                "wandb_url": None,
            }
            for name in ALL_STEPS
        ]
        log = plan_log(plan, lock)  # type: ignore[arg-type]
        if not_ready:
            ready = steps.get("ready") or {}
            log.append(
                {
                    "ts": _clock(ready.get("finished")),
                    "text": f"the last run stopped: {clean(ready.get('detail'), paths, 200)}",
                    "cls": "warn",
                }
            )
        return {
            **base,
            "state": "plan",
            "source": "plan",
            "steps": rows,
            "log": log,
            "log_source": "calendar",
        }

    if not steps:
        return {
            **base,
            "state": "none",
            "source": "none",
            "steps": [
                {
                    "step": name,
                    "status": "none",
                    "seconds": None,
                    "expected_seconds": expected[name],
                    "started": None,
                    "finished": None,
                    "detail": None,
                    "this_run": None,
                    "wandb_url": None,
                }
                for name in ALL_STEPS
            ],
            "log": [],
            "log_source": "none",
        }

    secs, records_s = step_times(paths, season, week)
    sum_steps = (summary or {}).get("steps") or {}
    failed_step = latest_failure(steps, running)
    if not_ready:
        failed_step = None
    rows = []
    after_failure = False
    for name in STEP_NAMES:
        st = steps.get(name)
        if st is None:
            status = "skipped" if after_failure else "pending" if running else "none"
            rows.append(
                {
                    "step": name,
                    "status": status,
                    "seconds": None,
                    "expected_seconds": expected[name],
                    "started": None,
                    "finished": None,
                    "detail": None,
                    "this_run": None,
                    "wandb_url": None,
                }
            )
            continue
        status = st.get("status") or "none"
        detail = st.get("detail")
        if status == "running" and not running:  # a run that died without finishing the step
            status, detail = "failed", "interrupted: the run stopped before this step finished"
        if status == "failed" and not_ready and name == "ready":
            status = "skipped"  # "not ready" isn't a failure (weekly.run_weekly records it so)
        if status == "failed":
            after_failure = True
        urls = wandb_urls(detail)
        rows.append(
            {
                "step": name,
                "status": status,
                "seconds": secs.get(name) if status not in ("running",) else None,
                "expected_seconds": expected[name],
                "started": st.get("started"),
                "finished": st.get("finished"),
                "detail": clean(
                    without_wandb(str(detail)) if detail else None, paths, DETAIL_LIMIT
                ),
                "this_run": (sum_steps.get(name) or {}).get("this_run"),
                "wandb_url": urls[-1] if urls else None,
            }
        )
    rows.append(
        {
            "step": "records",
            "status": "ok" if summary is not None else ("pending" if running else "none"),
            "seconds": records_s,
            "expected_seconds": expected["records"],
            "started": None,
            "finished": (summary or {}).get("finished"),
            "detail": (
                "run_summary.json · pipeline_history.parquet · drift · W&B pipeline run"
                if summary is not None
                else "the last run didn't write its records (an earlier run's are on disk)"
                if stale_summary
                else None
            ),
            "this_run": True if summary is not None else None,
            "wandb_url": None,
        }
    )

    if running:
        state_name = "running"
    elif published:
        state_name = "finished"
    elif failed_step:
        state_name = "failed"
    else:
        state_name = "partial"
    finished_secs = [r["seconds"] for r in rows[:-1] if r["seconds"] is not None]
    sittings = len(_sittings(steps))
    from_events = events_log(paths, run_dir, sittings)
    return {
        **base,
        "state": state_name,
        "source": "run_summary" if summary is not None else "weekly_run",
        "steps": rows,
        "sittings": len(_sittings(steps)),
        "step_seconds": round(sum(finished_secs), 1) if finished_secs else None,
        "failed_step": failed_step,
        "error": clean((summary or {}).get("error"), paths, 300),
        "log": from_events
        if from_events is not None
        else _rebuilt_log(paths, season, week, steps, published, checks, failed_step),
        "log_source": "events" if from_events is not None else "rebuilt",
        "tiles": _tiles(run_dir, checks, secs),
        "no_run_records": summary is None and not stale_summary,
    }
