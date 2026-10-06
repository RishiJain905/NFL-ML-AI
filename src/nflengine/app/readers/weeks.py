"""The sidebar's week list: one entry per week of the season, newest first.

Status, in order of precedence:
- `running`: the weekly-run lock is held by a run of this week;
- `published`: the same rule `--auto` uses (`weekly.already_published`: the digest step ok and
  the report file on D:);
- `failed`: a step's last status is `failed`;
- `partial`: steps ran but the digest didn't (and nothing failed);
- `ready` / `waiting`: the calendar's current week with no published run: last week final
  in the local schedule snapshot or not (the run refreshes the snapshot first, so `waiting`
  can be stale on a Tuesday morning; CR02's pre-flight handles that);
- `not_run`: a past week with no run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nflengine.app.readers.meta import lock_target, to_local_iso
from nflengine.ops.calendar import WeekPlan
from nflengine.ops.summary import scrub
from nflengine.paths import DataPaths
from nflengine.weekly import already_published, read_state

# How a not-ready `ready` step words its detail (curate/readiness.py, weekly._calendar_ready).
# Used only for weeks with no run summary (before P07): a failed `ready` step can also be a
# real failure (a corrupt play-by-play file, exit 1), so the summary's status wins.
NOT_READY_TEXT = re.compile(r"NOT READY|; missing \[|aren't in the schedule yet")

STATUS_LABEL = {
    "published": "Published",
    "running": "Running",
    "failed": "Failed",
    "partial": "Incomplete",
    "ready": "Ready to run",
    "not_run": "No run",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def checks_line(checks: dict[str, Any] | None) -> str | None:
    if not checks:
        return None
    if checks.get("banner"):
        return "Published with a warning banner"
    if checks.get("passed"):
        return "Checks passed after a rewrite" if checks.get("regenerated") else "Checks passed"
    return "Checks failed"


def week_entry(
    paths: DataPaths,
    season: int,
    week: int,
    plan: WeekPlan | None,
    lock: dict[str, Any],
) -> dict[str, Any]:
    run_dir = paths.run_dir(season, week)
    state_file = run_dir / "weekly_run.json"
    state = read_state(state_file)
    steps = (state or {}).get("steps", {}) or {}
    step_status = {k: (v or {}).get("status") for k, v in steps.items()}
    published = already_published(state_file, paths.report_path(season, week))
    failed_steps = [k for k, v in step_status.items() if v == "failed"]
    summary = _read_json(run_dir / "run_summary.json")
    checks = _read_json(run_dir / "checks.json")
    is_current = plan is not None and plan.season == season and plan.week == week
    # a "not ready" run records its `ready` step as failed (weekly.run_weekly); it isn't one
    if summary is not None:
        not_ready = summary.get("status") == "not_ready"
    else:
        ready_detail = str((steps.get("ready") or {}).get("detail") or "")
        not_ready = failed_steps == ["ready"] and bool(NOT_READY_TEXT.search(ready_detail))
    label = None

    if lock_target(lock, plan) == (season, week):
        status, detail = "running", lock.get("command")
    elif published:
        status, detail = "published", checks_line(checks)
    elif failed_steps and not not_ready:
        status, detail = "failed", f"{failed_steps[0]} failed"
    elif is_current and plan is not None:
        prev = plan.previous_week
        if plan.slate is None:  # e.g. the next playoff round isn't in the schedule yet
            status, label = "waiting", "Waiting on the schedule"
            detail = f"Week {week}'s games aren't in the schedule yet; the run checks again"
        elif plan.previous_complete:
            status, detail = (
                "ready",
                f"Week {prev} is final ({plan.previous_final}/{plan.previous_games})",
            )
        else:
            status = "waiting"
            detail = (
                f"{plan.previous_final}/{plan.previous_games} of week {prev} final in the local "
                "schedule snapshot; the run refreshes it first"
            )
    elif not_ready:
        status, detail = "partial", "The last run stopped: the week before wasn't final yet"
    elif step_status:
        status, detail = "partial", "The last run stopped before the digest"
    else:
        status, detail = "not_run", None

    if label is None:
        label = (
            f"Waiting on week {plan.previous_week}"
            if status == "waiting" and plan is not None
            else STATUS_LABEL[status]
        )
    digest_step = steps.get("digest") or {}
    return {
        "season": season,
        "week": week,
        "status": status,
        "label": label,
        "detail": scrub(detail, 200) if detail else None,
        "is_current": is_current,
        "has_run": bool(step_status),
        "has_run_summary": summary is not None,
        "published_at": to_local_iso(digest_step.get("finished")) if published else None,
        "on_time": summary.get("on_time") if summary else None,
        "steps": step_status,
    }


def list_weeks(
    paths: DataPaths, plan: WeekPlan | None, lock: dict[str, Any], season: int
) -> dict[str, Any]:
    season_dir = paths.runs / str(season)
    found = (
        sorted(
            int(d.name[4:]) for d in season_dir.glob("week*") if d.is_dir() and d.name[4:].isdigit()
        )
        if season_dir.exists()
        else []
    )
    weeks = set(found)
    current = plan.week if plan is not None and plan.season == season else None
    if current is not None:
        weeks.add(current)
    run_weeks = [w for w in found if (paths.run_dir(season, w) / "weekly_run.json").exists()]
    go_live = min(run_weeks) if run_weeks else None
    entries = [week_entry(paths, season, w, plan, lock) for w in sorted(weeks, reverse=True)]
    return {
        "season": season,
        "current_week": current,
        "phase": plan.phase if plan is not None else None,
        "go_live_week": go_live,
        "weeks": entries,
    }
