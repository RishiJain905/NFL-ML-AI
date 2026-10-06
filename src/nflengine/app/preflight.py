"""The checks behind the Run button (CR02; documentation/control-room/README.md §2, §5).

`get_preflight` answers three questions for the page, with the server's own week (the
browser never chooses it):

- **Can the Tuesday run start?** Calendar week, last week final, not published yet, run lock
  free, data drive found, Neo4j up (warn only: the run starts it), keys set (names only, the
  doctor's check), `seasons.current` matches the calendar. Run is allowed only when every
  blocking check passes.
- **Can the current week's failed run be resumed?** Only from the step that failed.
- **Can the Saturday injury update run?** Only on Saturday (ET) once the week's Tuesday run
  has published, until the week's last kickoff.

**Last week final, from a stale snapshot (D103).** The local schedule snapshot is often a day
old on a Tuesday morning ("15/16 final" before the run refreshes it), so "not final in the
snapshot" alone is only a warning: the run refreshes the schedule under the lock and stops
with exit 3 (nothing published) if the week still isn't final. It blocks only right after
such a not-ready run (fresh evidence): for `READY_RECHECK_MIN` minutes, then Run opens again.

Read-only: nothing here writes, and nothing here runs a command (the dry-run log is built
from the calendar's plan, as `nfl weekly run --auto --dry-run` prints it).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nflengine.app.readers.common import clean, read_json, summary_is_current
from nflengine.app.readers.meta import lock_dict
from nflengine.ops.calendar import EASTERN, WeekPlan
from nflengine.paths import DataPaths
from nflengine.weekly import STEPS, already_published, read_state

READY_RECHECK_MIN = 30  # after a not-ready run, Run opens again this many minutes later
REQUIRED_KEYS = ("NEO4J_PASSWORD", "WANDB_API_KEY", "OPENROUTER_API_KEY")
REHEARSAL_STEPS = ("ready", "game", "player", "digest")
REHEARSAL_DIR = ("rehearsals", "control-room")  # under the data root


def _et(t: dt.datetime | None, fmt: str) -> str:
    return t.astimezone(EASTERN).strftime(fmt).replace(" 0", " ") if t else "—"


def _clock(t: dt.datetime) -> str:
    """ "6:00 AM ET"."""
    return t.astimezone(EASTERN).strftime("%I:%M %p ET").lstrip("0")


def _day(t: dt.datetime) -> str:
    """ "Wed 6:00 PM ET"."""
    return t.astimezone(EASTERN).strftime("%a ") + _clock(t)


def _check(
    id_: str, label: str, status: str, value: str, reason: str | None = None, blocking=True
) -> dict[str, Any]:
    return {
        "id": id_,
        "label": label,
        "status": status,
        "value": clean(value, None, 200) or "",
        "reason": clean(reason, None, 300) if reason else None,
        "blocking": blocking,
    }


def schedule_snapshot_time(paths: DataPaths) -> dt.datetime | None:
    """When the newest schedule snapshot was written (its newest file's time)."""
    base = paths.raw / "nflverse" / "schedules"
    try:
        snaps = sorted(d for d in base.glob("snapshot=*") if d.is_dir())
        if not snaps:
            return None
        newest = max((f.stat().st_mtime for f in snaps[-1].iterdir() if f.is_file()), default=None)
    except OSError:
        return None
    return dt.datetime.fromtimestamp(newest, dt.UTC) if newest else None


def recent_not_ready(paths: DataPaths, season: int, week: int, now: dt.datetime):
    """(finished, detail) if this week's last run stopped as "not ready" less than
    `READY_RECHECK_MIN` minutes ago (its `ready` step is the only failed one and nothing ran
    after it; the run summary, when current, says `not_ready`), else None."""
    from nflengine.app.readers.weeks import NOT_READY_TEXT

    run_dir = paths.run_dir(season, week)
    steps = (read_state(run_dir / "weekly_run.json") or {}).get("steps") or {}
    ready = steps.get("ready") or {}
    if ready.get("status") != "failed":
        return None
    finished = _time(ready.get("finished"))
    if finished is None or now - finished > dt.timedelta(minutes=READY_RECHECK_MIN):
        return None
    later = [
        n
        for n, st in steps.items()
        if n != "ready" and (_time((st or {}).get("started")) or finished) > finished
    ]
    if later:
        return None
    summary = read_json(run_dir / "run_summary.json")
    if summary is not None and summary_is_current(summary, steps):
        if summary.get("status") != "not_ready":
            return None
    elif not NOT_READY_TEXT.search(str(ready.get("detail") or "")):
        return None
    return finished, str(ready.get("detail") or "")


def _time(text: Any) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()


def failed_step(paths: DataPaths, season: int, week: int, running: bool) -> str | None:
    """The step the week's last run failed at (None if it didn't fail, or is running): the
    first `failed` step, or one left `running` by a run that died (interrupted). A not-ready
    stop isn't a failure."""
    from nflengine.app.readers.pipeline import get_pipeline

    if running:
        return None
    p = get_pipeline(paths, season, week, None, {"held": False})
    return p["failed_step"] if p["state"] == "failed" else None


def saturday_window(plan: WeekPlan) -> tuple[dt.datetime | None, dt.datetime | None]:
    """(opens, closes): the week's Saturday 00:00 ET and its last kickoff. The week's Saturday
    is the first one on or after its first kickoff for a slate that starts Tuesday to Saturday
    (a regular week, Thanksgiving, Christmas), and the one before for a slate that starts on a
    Sunday or Monday (the conference round, the Super Bowl; Sol review)."""
    s = plan.slate
    if s is None:
        return None, None
    first = s.first_kickoff.astimezone(EASTERN)
    wd = first.weekday()  # Mon 0 ... Sat 5, Sun 6
    shift = -((wd - 5) % 7) if wd in (6, 0) else (5 - wd) % 7
    day = first.date() + dt.timedelta(days=shift)
    opens = dt.datetime.combine(day, dt.time(0, 0), EASTERN).astimezone(dt.UTC)
    return opens, s.last_kickoff


def _dry_run_log(plan: WeekPlan, season: int, week: int, lock_held: bool) -> list[dict]:
    """What `nfl weekly run --auto --expect-week N --dry-run` prints (from the same plan)."""
    ts = plan.now.astimezone(EASTERN).strftime("%H:%M:%S")
    lines = [
        {"ts": ts, "text": f"$ nfl weekly run --auto --expect-week {week} --dry-run", "cls": "acc"}
    ]
    for line in plan.describe():
        lines.append({"ts": ts, "text": clean(line, None, 300) or "", "cls": ""})
    ready = plan.previous_complete and plan.slate is not None
    lines.append(
        {
            "ts": ts,
            "text": f"lock: {'held' if lock_held else 'free'}",
            "cls": "dim",
        }
    )
    lines.append(
        {
            "ts": ts,
            "text": f"dry run: would run {', '.join(STEPS)} for {season} week {week:02d}"
            + ("" if ready else f"; week {plan.previous_week} isn't final yet (exit 3)"),
            "cls": "ok" if ready else "warn",
        }
    )
    return lines


def _gate(allowed: bool, reason: str | None, command: str | None, **extra) -> dict[str, Any]:
    return {"allowed": allowed, "reason": None if allowed else reason, "command": command, **extra}


def get_preflight(
    paths: DataPaths,
    plan: WeekPlan | None,
    *,
    now: dt.datetime,
    current_season: int,
    neo4j: dict[str, Any],
    keys_set: Callable[[tuple[str, ...]], dict[str, bool]],
    running: dict[str, Any] | None,
    rehearsal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The pre-flight answer (`PreflightResponse` in web/src/api/types.ts). `running`: the
    run in progress (`runner.current`'s run) or None; `rehearsal`: {season, week, root, lock}
    under `nfl app --rehearsal`."""
    if rehearsal is not None:
        return _rehearsal_preflight(paths, plan, now, running, rehearsal)
    lock = lock_dict(paths)
    busy = running is not None or lock["held"]
    checks: list[dict[str, Any]] = []
    season = plan.season if plan is not None else None
    week = plan.week if plan is not None else None
    retry_after: dt.datetime | None = None

    # 1. the calendar's week
    if plan is None:
        checks.append(
            _check(
                "calendar",
                "Calendar",
                "fail",
                "no schedule snapshot",
                "There's no schedule on the data drive yet: run `uv run nfl ingest`.",
            )
        )
    elif week is None:
        checks.append(
            _check(
                "calendar",
                "Calendar",
                "fail",
                f"{plan.season} {plan.phase}",
                "The calendar has no week to run (offseason).",
            )
        )
    elif plan.slate is None:
        checks.append(
            _check(
                "calendar",
                "Calendar",
                "fail",
                f"{season} week {week}",
                f"Week {week}'s games aren't in the schedule yet.",
            )
        )
    else:
        s = plan.slate
        days = f"{_et(s.first_kickoff, '%a %b %d')} → {_et(s.last_kickoff, '%a %b %d')}"
        checks.append(
            _check("calendar", "Calendar", "ok", f"{season} week {week} · {s.games} games · {days}")
        )

    # 2. last week final (D103: a stale snapshot only warns)
    if plan is not None and week is not None and plan.previous_week is not None:
        prev = plan.previous_week
        tally = f"{plan.previous_final}/{plan.previous_games} final"
        recent = recent_not_ready(paths, season, week, now) if season else None
        if recent is not None:  # fresh evidence wins over the schedule (missing pbp; Sol review)
            finished, detail = recent
            retry_after = finished + dt.timedelta(minutes=READY_RECHECK_MIN)
            window = (
                f"; the retry window runs to {_day(plan.retry_until)}" if plan.retry_until else ""
            )
            checks.append(
                _check(
                    "last_week_final",
                    f"Week {prev} is final",
                    "fail",
                    f"{tally} · checked by the run at {_clock(finished)}",
                    f"Week {prev} isn't final in the data yet ({clean(detail, paths, 160)}). "
                    f"nflverse usually has Monday night's game by Tuesday morning. Try again after "
                    f"{_clock(retry_after)}{window}.",
                )
            )
        elif plan.previous_complete:
            checks.append(_check("last_week_final", f"Week {prev} is final", "ok", tally))
        else:
            snap = schedule_snapshot_time(paths)
            when = f" in the schedule snapshot from {_day(snap)}" if snap else ""
            checks.append(
                _check(
                    "last_week_final",
                    f"Week {prev} is final",
                    "warn",
                    f"{tally}{when}",
                    f"The run refreshes the schedule first; if week {prev} still isn't final it "
                    'stops with "not ready" (exit 3) and nothing is published.',
                    blocking=False,
                )
            )
    elif plan is not None and week is not None:
        checks.append(
            _check("last_week_final", "Previous week", "ok", "week 1: nothing to wait for")
        )

    # 3. not published yet
    published = False
    if season is not None and week is not None:
        published = already_published(
            paths.run_dir(season, week) / "weekly_run.json", paths.report_path(season, week)
        )
        name = f"week{week:02d}-digest.md"
        checks.append(
            _check(
                "not_published",
                "Not published yet",
                "fail",
                f"{name} is published",
                f"Week {week} is already published. Re-running a published week stays in "
                "the terminal (`--force`).",
            )
            if published
            else _check("not_published", "Not published yet", "ok", f"no {name} on the data drive")
        )

    # 4. the run lock
    if lock["held"]:
        checks.append(
            _check(
                "lock",
                "Run lock",
                "fail",
                f"held since {lock.get('started') or '?'}",
                f"Another run holds the lock (`{lock.get('command')}`). Wait for it to finish.",
            )
        )
    else:
        checks.append(_check("lock", "Run lock", "ok", "free"))

    # 5. the data drive (never its path: it's NFL_DATA_ROOT's value)
    checks.append(_check("data_root", "Data drive", "ok", "found"))

    # 6. Neo4j (warn only: the run starts it)
    status = neo4j.get("status")
    checks.append(
        _check("neo4j", "Neo4j", "ok", "up")
        if status == "up"
        else _check(
            "neo4j",
            "Neo4j",
            "warn",
            "checking" if status == "checking" else "down",
            "The run starts it (`docker compose up -d`, about a minute); if it can't, "
            "the digest goes out without its graph sections.",
            blocking=False,
        )
    )

    # 7. keys: names only (set / not set), never values
    have = keys_set(REQUIRED_KEYS)
    missing = [k for k in REQUIRED_KEYS if not have.get(k)]
    checks.append(
        _check("keys", "Keys", "ok", " · ".join(REQUIRED_KEYS) + " set")
        if not missing
        else _check(
            "keys",
            "Keys",
            "fail",
            " · ".join(missing) + " not set",
            "Add the missing variable names to the env file (Rishi), then check "
            "with `uv run nfl doctor`.",
        )
    )

    # 8. seasons.current
    if season is not None and week is not None:
        checks.append(
            _check("season", "Season setting", "ok", f"seasons.current = {current_season}")
            if season == current_season
            else _check(
                "season",
                "Season setting",
                "fail",
                f"seasons.current = {current_season}",
                f"The calendar says {season} but settings.yaml says {current_season}: "
                "update it (pre-season checklist).",
            )
        )

    blocked = [c for c in checks if c["blocking"] and c["status"] == "fail"]
    can_run = not blocked and not busy and week is not None
    run_reason = (
        "A run is in progress."
        if busy and not lock["held"]
        else blocked[0]["reason"]
        if blocked
        else None
    )
    command = f"nfl weekly run --auto --expect-week {week}" if week is not None else None

    # resume: the current week's failed run, from its failed step
    step = failed_step(paths, season, week, busy) if season and week else None
    if step is not None and not published:
        resume = _gate(
            not busy and not lock["held"],
            "A run is in progress.",
            f"nfl weekly run --season {season} --week {week} --from-step {step} --promote",
            step=step,
        )
    else:
        resume = _gate(
            False,
            "Only the current week's failed run can be resumed, from the step that failed.",
            None,
            step=None,
        )

    # the Saturday injury update
    opens, closes = saturday_window(plan) if plan is not None and week else (None, None)
    material = None
    if season and week:
        iu = read_json(paths.run_dir(season, week) / "injury_update.json") or {}
        material = iu.get("material") if isinstance(iu.get("material"), bool) else None
    if not published:
        iu_reason = "Needs this week's Tuesday run first."
    elif opens is not None and now < opens:
        iu_reason = f"Opens {_et(opens, '%A %b %d')} (ET)."
    elif closes is not None and now >= closes:
        iu_reason = "The week's last game has kicked off."
    elif busy:
        iu_reason = "A run is in progress."
    else:
        iu_reason = None
    injury = _gate(
        iu_reason is None,
        iu_reason,
        f"nfl weekly injury-update --auto --expect-week {week}" if week else None,
        opens=opens,
        closes=closes,
        material_last=material,
    )

    return {
        "checked_at": now,
        "mode": "live",
        "season": season,
        "week": week,
        "checks": checks,
        "run": _gate(can_run, run_reason or "No week to run.", command, retry_after=retry_after),
        "confirm": {
            "last_week": (
                f"week {plan.previous_week} · {plan.previous_final}/{plan.previous_games} final"
                if plan is not None and plan.previous_week is not None
                else "none (week 1)"
            ),
            "deadline": plan.deadline if plan is not None else None,
            "hours_to_deadline": plan.hours_to_deadline if plan is not None else None,
            "retry_until": plan.retry_until if plan is not None else None,
            "steps": list(STEPS),
            "publishes": [f"week{week:02d}-digest.md", "production alias"] if week else [],
        },
        "resume": resume,
        "injury_update": injury,
        "dry_run_log": _dry_run_log(plan, season, week, lock["held"])
        if plan is not None and week is not None
        else [],
    }


def rehearsal_target(
    paths: DataPaths, plan: WeekPlan | None, season: int
) -> tuple[int, int] | None:
    """The week `nfl app --rehearsal` rehearses: the newest published live week (its data is
    complete), else the calendar's previous week."""
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
        if already_published(
            paths.run_dir(season, w) / "weekly_run.json", paths.report_path(season, w)
        ):
            return season, w
    if plan is not None and plan.previous_week is not None:
        return plan.season, plan.previous_week
    return None


def rehearsal_failed_step(root: Path, season: int, week: int) -> str | None:
    """The step the last rehearsal of this week failed at (from its `rehearsal.json`)."""
    res = read_json(root / "runs" / str(season) / f"week{week:02d}" / "rehearsal.json") or {}
    if res.get("status") != "failed":
        return None
    step = str(res.get("error") or "").split(":", 1)[0].strip()
    return step if step in REHEARSAL_STEPS else None


def _rehearsal_preflight(
    paths: DataPaths,
    plan: WeekPlan | None,
    now: dt.datetime,
    running: dict[str, Any] | None,
    r: dict[str, Any],
) -> dict[str, Any]:
    season, week, held = r.get("season"), r.get("week"), bool(r.get("lock_held"))
    busy = running is not None or held
    checks = [
        _check(
            "calendar",
            "Rehearsal week",
            "ok" if week else "fail",
            f"{season} week {week} · the newest published week" if week else "none",
            None if week else "No published week to rehearse yet.",
        ),
        _check(
            "lock",
            "Rehearsal folder",
            "fail" if held else "ok",
            "in use" if held else "free (rehearsals/control-room)",
            "Another rehearsal is using the folder." if held else None,
        ),
        _check("data_root", "Data drive", "ok", "found"),
        _check(
            "live",
            "Live files",
            "ok",
            "untouched: no W&B, no graph write, no records, placeholder writer",
        ),
    ]
    blocked = [c for c in checks if c["blocking"] and c["status"] == "fail"]
    out = "rehearsals/control-room"
    command = f"nfl weekly rehearse --season {season} --week {week} --out {out} --fresh"
    step = rehearsal_failed_step(Path(r["root"]), season, week) if week else None
    resume_steps = ",".join(REHEARSAL_STEPS[REHEARSAL_STEPS.index(step) :]) if step else ""
    return {
        "checked_at": now,
        "mode": "rehearsal",
        "season": season,
        "week": week,
        "checks": checks,
        "run": _gate(
            not blocked and not busy and bool(week),
            "A rehearsal is in progress."
            if busy
            else (blocked[0]["reason"] if blocked else "Nothing to rehearse."),
            command,
            retry_after=None,
        ),
        "confirm": {
            "last_week": f"week {week - 1}" if week and week > 1 else "none",
            "deadline": None,
            "hours_to_deadline": None,
            "retry_until": None,
            "steps": list(REHEARSAL_STEPS),
            "publishes": [f"nothing live: the rehearsal folder ({out})"],
        },
        "resume": _gate(
            step is not None and not busy,
            "A rehearsal is in progress."
            if busy and step
            else "Only a failed rehearsal can be resumed, from the step that failed.",
            f"nfl weekly rehearse --season {season} --week {week} --steps {resume_steps} "
            f"--out {out}"
            if step
            else None,
            step=step,
        ),
        "injury_update": _gate(
            False, "Not in rehearsal mode.", None, opens=None, closes=None, material_last=None
        ),
        "dry_run_log": [],
    }
