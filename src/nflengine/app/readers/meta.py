"""The calendar's plan and the run lock, as plain dicts for the API."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from nflengine.ops.calendar import WeekPlan
from nflengine.ops.lock import is_locked, lock_path, read_holder
from nflengine.ops.summary import scrub
from nflengine.paths import DataPaths


def plan_dict(plan: WeekPlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    s = plan.slate
    return {
        "season": plan.season,
        "week": plan.week,
        "phase": plan.phase,
        "previous_week": plan.previous_week,
        "previous_games": plan.previous_games,
        "previous_final": plan.previous_final,
        "previous_complete": plan.previous_complete,
        "deadline": plan.deadline,
        "hours_to_deadline": plan.hours_to_deadline,
        "deadline_passed": plan.deadline_passed,
        "retry_until": plan.retry_until,
        "started_games": plan.started_games,
        "games": s.games if s else 0,
        "game_type": s.game_type if s else None,
        "days": list(s.days) if s else [],
        "special": list(s.special) if s else [],
        "byes": list(s.teams_on_bye) if s else [],
        "neutral_sites": list(s.neutral_sites) if s else [],
        "international": list(s.international) if s else [],
        "notes": [scrub(n, 200) for n in plan.notes],
    }


_WEEK_ARGS = re.compile(r"--season\s+(\d{4}).*?--week\s+(\d{1,2})")


def lock_dict(paths: DataPaths, probe=is_locked) -> dict[str, Any]:
    """Who holds the weekly-run lock. Only the command and start time leave the server.

    Probing the lock means taking it for a moment, and a weekly run that starts in that
    moment would stop with "another run holds the lock" (exit 4). So read the note first:
    a run writes it right after taking the lock and empties it when it lets go. No note →
    free, without touching the lock. A note → probe, because a crashed run leaves its note
    behind while the OS has already released its lock."""
    path = lock_path(paths.runs)
    note = read_holder(path)
    held = bool(note) and probe(path)
    note = note if held else None
    command = scrub(note.get("command"), 200) if note else None
    return {"held": held, "command": command, "started": note.get("started") if note else None}


def lock_target(lock: dict[str, Any], plan: WeekPlan | None) -> tuple[int, int] | None:
    """(season, week) a held lock is working on: from `--season S --week N`, or the
    calendar's week for an `--auto` run."""
    if not lock.get("held") or not lock.get("command"):
        return None
    if "--as-of" in lock["command"]:  # a time-travel simulation: not the live week
        return None
    m = _WEEK_ARGS.search(lock["command"])
    if m:
        return int(m.group(1)), int(m.group(2))
    if "--auto" in lock["command"] and plan is not None and plan.week is not None:
        return plan.season, plan.week
    return None


def to_local_iso(text: str | None) -> str | None:
    """Step times before P07 are naive local time; later ones carry an offset. Return ISO
    with an offset either way (naive = this machine's local zone, where the run happened)."""
    if not text:
        return None
    try:
        t = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return (t if t.tzinfo else t.astimezone()).isoformat()
