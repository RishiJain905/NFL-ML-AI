"""Rehearsals (P10): the live Tuesday steps on any week, in a scratch copy.

`nfl weekly rehearse --season 2025 --week 19` runs the live code path (`ready`, `game`,
`player`, `digest`: the same functions as `nfl weekly run`) for a past or current week as if
it were the Tuesday before it:

- **the clock** (`nflengine.clock`) is pinned to 10:00 Eastern on the Tuesday before the
  week's first kickoff (`digest.run.tuesday_before`), so the week's games still count as
  upcoming and get predictions;
- **the files** go through `RehearsalPaths`: raw, curated, research and the caches are read
  from the real data root; features, models, runs and reports are written under
  `rehearsals/<season>/` on the data root, seeded once with copies of the feature tables and
  the canonical backtests the weekly refits continue from. Weeks rehearsed into the same
  folder chain like live weeks (week 20's report card grades week 19's rehearsed picks);
- **nothing shared is touched**: no W&B (`WANDB_MODE=disabled`), no graph write and no graph
  sections (the live Neo4j graph is shared), no run records, no alias moves; the
  placeholder writer unless `llm` names another. It doesn't take the weekly-run lock: it takes
  its own, one per rehearsal folder (`.<folder>.rehearsal.lock` beside it, CR02), so two
  rehearsals can't share a folder and the control room can tell one is running;
- **progress events** (CR02, `ops/events.py`) go to the rehearsed week's `events/` folder under
  the rehearsal folder, so `nfl app --rehearsal` can draw the run live. `fail_at` (`--fail-at`,
  hidden) makes one step fail on purpose: the control room's failure-and-resume proof.

Left out on purpose: `ingest`, `curate` and `ratings` refresh season-wide shared files (the
ratings step also rebuilds `curated/nfl.duckdb`'s views), and `graph` would replace the live
graph; a rehearsal reads their current output. Because it reads today's data, a rehearsal
proves the **code path**, not the accuracy: closing lines and final injury reports leak into
a past week's numbers.

Uses: the playoff check before Wild Card weekend (the 2025 playoffs, weeks 19-22), the
pre-season dry run on last season's week 1, and any risky change before a live Tuesday.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nflengine.paths import DataPaths, ensure_data_root

STEPS = ("ready", "game", "player", "digest")
MARKER = "REHEARSAL.md"  # marks a folder this module made (only those can be wiped)
RESULT_FILE = "rehearsal.json"
SEED_BACKTESTS = ("game", "player", "team")  # runs/backtests/<family>/: the refits' history


@dataclass(frozen=True)
class RehearsalPaths(DataPaths):
    """Reads the real data (`live`), writes the outputs under `root`."""

    live: Path

    @property
    def raw(self) -> Path:
        return self.live / "raw"

    @property
    def curated(self) -> Path:
        return self.live / "curated"

    @property
    def research(self) -> Path:
        return self.live / "research"

    @property
    def bdb(self) -> Path:
        return self.live / "bdb"

    @property
    def neo4j_data(self) -> Path:
        return self.live / "neo4j" / "data"

    @property
    def neo4j_logs(self) -> Path:
        return self.live / "neo4j" / "logs"

    @property
    def neo4j_plugins(self) -> Path:
        return self.live / "neo4j" / "plugins"

    @property
    def cache(self) -> Path:
        return self.live / "cache"


@dataclass
class RehearsalResult:
    season: int
    week: int
    at: dt.datetime  # the pinned Tuesday, UTC
    status: str  # ok | degraded | failed
    root: Path
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    report: Path | None = None
    error: str | None = None
    slate: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "week": self.week,
            "at": self.at.isoformat(),
            "status": self.status,
            "root": str(self.root),
            "steps": self.steps,
            "report": str(self.report) if self.report else None,
            "error": self.error,
            "slate": self.slate,
        }


def default_root(live: DataPaths, season: int) -> Path:
    return live.root / "rehearsals" / str(season)


def _copy_missing(src: Path, dst: Path) -> bool:
    if dst.exists() or not src.exists():
        return False
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return True


def seed(live: DataPaths, out: RehearsalPaths, log: Callable[[str], None] = print) -> list[str]:
    """First use of a rehearsal folder: copy what the steps read from the written side of the
    data root (the feature tables, the canonical backtests, the novelty record). Later
    rehearsals into the same folder reuse it (and see each other's weeks)."""
    copied = []
    if not (out.features / "team_ratings.parquet").exists():
        shutil.copytree(live.features, out.features, dirs_exist_ok=True)
        copied.append("features")
    for fam in SEED_BACKTESTS:
        if _copy_missing(live.runs / "backtests" / fam, out.runs / "backtests" / fam):
            copied.append(f"runs/backtests/{fam}")
    if _copy_missing(
        live.runs / "published_insights.parquet", out.runs / "published_insights.parquet"
    ):
        copied.append("runs/published_insights.parquet")
    if copied:
        log(f"rehearsal folder seeded: {', '.join(copied)}")
    return copied


LIVE_FOLDERS = (
    "raw",
    "curated",
    "features",
    "models",
    "runs",
    "reports",
    "wandb",
    "research",
    "cache",
    "neo4j",
    "bdb",
)


def check_root(root: Path, live: DataPaths) -> Path:
    """The rehearsal folder, resolved, or `ValueError` before anything is written (Sol review,
    P10): never the live data root, one of its parents or anything inside a live folder (a
    rehearsal into `--out D:/nfl-ml-data` would overwrite live files, and a later `--fresh`
    could delete the whole root), and never an existing non-empty folder that isn't a
    rehearsal folder (writing the marker into it would make it wipeable)."""
    out = Path(root).resolve()
    live_root = live.root.resolve()
    if out == live_root or live_root.is_relative_to(out):
        raise ValueError(f"{out} is the live data root or contains it: choose another folder")
    for name in LIVE_FOLDERS:
        folder = live_root / name
        if out == folder or out.is_relative_to(folder):
            raise ValueError(f"{out} is inside the live data folder {folder}: choose another")
    if out.exists() and not out.is_dir():
        raise ValueError(f"{out} is a file, not a folder")
    if out.exists() and any(out.iterdir()) and not (out / MARKER).exists():
        raise ValueError(f"{out} isn't empty and isn't a rehearsal folder (no {MARKER})")
    return out


def wipe(root: Path) -> None:
    """Delete a rehearsal folder, but only one this module made (it holds the marker)."""
    if not root.exists():
        return
    if not (root / MARKER).exists():
        raise ValueError(f"{root} isn't a rehearsal folder (no {MARKER}); not deleting it")
    shutil.rmtree(root)


@contextmanager
def _wandb_off() -> Iterator[None]:
    keys = ("WANDB_MODE", "WANDB_SILENT")
    old = {k: os.environ.get(k) for k in keys}
    os.environ["WANDB_MODE"], os.environ["WANDB_SILENT"] = "disabled", "true"
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _earlier_steps(state: dict | None, order: tuple[str, ...]) -> dict[str, Any]:
    """Steps of an earlier sitting that come before this one's first step (a resume)."""
    if not state:
        return {}
    weekly_order = ("ingest", "ready", "curate", "ratings", "game", "graph", "player", "digest")
    first = weekly_order.index(order[0]) if order and order[0] in weekly_order else 0
    out: dict[str, Any] = {}
    for name in weekly_order[:first]:
        st = (state.get("steps") or {}).get(name)
        if st:
            out[name] = {"status": st.get("status"), "seconds": _secs(st)}
    return out


def _secs(step: dict) -> float | None:
    try:
        a = dt.datetime.fromisoformat(step["started"])
        b = dt.datetime.fromisoformat(step["finished"])
    except (KeyError, TypeError, ValueError):
        return None
    return round((b - a).total_seconds(), 1)


def _marker_text() -> str:
    return (
        "# Rehearsal folder\n\n"
        "Written by `nfl weekly rehearse` (`src/nflengine/ops/rehearsal.py`): the live weekly "
        "steps run on a past or current week with the clock pinned to the Tuesday before it. "
        "Nothing here is live. Safe to delete (`--fresh` wipes it).\n"
    )


def lock_file(root: Path) -> Path:
    """The rehearsal folder's own lock, beside it (a `--fresh` wipe never deletes it)."""
    root = Path(root)
    return root.parent / f".{root.name}.rehearsal.lock"


def run_rehearsal(
    season: int,
    week: int,
    *,
    steps: Sequence[str] = STEPS,
    llm: str | None = "placeholder",
    root: Path | None = None,
    at: dt.datetime | None = None,
    fresh: bool = False,
    launched_by: str | None = "rehearsal",
    log: Callable[[str], None] = print,
    funcs: dict[str, Callable[..., str]] | None = None,
    run_id: str | None = None,
    via: str | None = None,
    fail_at: str | None = None,
) -> RehearsalResult:
    """Run `steps` (a subset of `STEPS`, kept in their order) for `season` / `week` into the
    rehearsal folder. A failing step stops the rehearsal like it stops a live run; the
    result (also `rehearsal.json` in the week's run folder) says which and why. Raises
    `ops.lock.LockHeld` if another rehearsal is using the folder."""
    from nflengine.ops.lock import run_lock

    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        raise ValueError(f"steps {unknown} can't be rehearsed; choose from {', '.join(STEPS)}")
    if fail_at is not None and fail_at not in steps:
        raise ValueError(f"--fail-at {fail_at!r} isn't one of the rehearsed steps")
    order = tuple(s for s in STEPS if s in steps)
    live = ensure_data_root()
    out_root = check_root(root if root is not None else default_root(live, season), live)
    out_root.parent.mkdir(parents=True, exist_ok=True)
    command = f"nfl weekly rehearse --season {season} --week {week} --steps {','.join(order)}"
    from nflengine.ops import events

    run_id = run_id if events.valid_run_id(run_id) else events.new_run_id()
    with run_lock(lock_file(out_root), command, run_id=run_id):
        return _rehearse(
            season,
            week,
            order=order,
            llm=llm,
            live=live,
            out_root=out_root,
            at=at,
            fresh=fresh,
            launched_by=launched_by,
            log=log,
            funcs=funcs,
            run_id=run_id,
            via=via,
            fail_at=fail_at,
            command=command,
        )


def _failing(name: str) -> Callable[..., str]:
    """The `--fail-at` hook: report some progress, then fail like a real step would."""

    def step(o: Any, step_log: Callable[[str], None]) -> str:
        from nflengine import weekly as W
        from nflengine.ops import events

        events.progress(name, 1, 2, "rehearsal test hook: failing here on purpose")
        step_log(f"[red]{name}: failing on purpose (rehearsal test hook, --fail-at)[/]")
        raise W.StepFailed(name, "injected failure (rehearsal test hook, --fail-at)")

    return step


def _rehearse(
    season: int,
    week: int,
    *,
    order: tuple[str, ...],
    llm: str | None,
    live: DataPaths,
    out_root: Path,
    at: dt.datetime | None,
    fresh: bool,
    launched_by: str | None,
    log: Callable[[str], None],
    funcs: dict[str, Callable[..., str]] | None,
    run_id: str | None,
    via: str | None,
    fail_at: str | None,
    command: str,
) -> RehearsalResult:
    from nflengine import clock
    from nflengine import weekly as W
    from nflengine.digest.run import read_games, tuesday_before
    from nflengine.ops import events
    from nflengine.ops.calendar import load_schedules, plan_for
    from nflengine.paths import redirect_data_root

    if fresh:
        wipe(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / MARKER).write_text(_marker_text(), encoding="utf-8")
    games = read_games(live)
    when = at or tuesday_before(games, season, week)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    when = when.astimezone(dt.UTC)
    slate = None
    try:
        plan = plan_for(load_schedules(live), season, week, when)
        slate = plan.slate.as_dict() if plan.slate is not None else None
    except Exception as e:  # the calendar is context only
        log(f"[yellow]calendar skipped ({type(e).__name__})[/]")
    paths = RehearsalPaths(out_root, live=live.root)
    opts = W.WeeklyOptions(
        season, week, launched_by, promote=False, llm=llm, graph=False, use_wandb=False
    )
    result = RehearsalResult(season, week, when, "ok", out_root, slate=slate)
    special = ", ".join((slate or {}).get("special") or ()) or "none"
    log(
        f"rehearsal of {season} week {week} as of {when:%a %Y-%m-%d %H:%M} UTC "
        f"({len(order)} steps: {', '.join(order)}; special: {special}) -> {out_root}"
    )
    state: dict[str, Any] | None = None
    ran: list[str] = []  # the steps this rehearsal called (the state file keeps older ones)

    def tracked(name: str, fn: Callable[..., str]) -> Callable[..., str]:
        def step(o: Any, step_log: Callable[[str], None]) -> str:
            ran.append(name)
            return fn(o, step_log)

        return step

    hook = {fail_at: _failing(fail_at)} if fail_at else {}
    step_funcs = {n: tracked(n, f) for n, f in {**W.STEP_FUNCS, **(funcs or {}), **hook}.items()}
    rid = run_id or events.new_run_id()
    with _wandb_off(), redirect_data_root(paths), clock.pinned(when):
        seed(live, paths, log)
        state_file = paths.run_dir(season, week) / "weekly_run.json"
        earlier = _earlier_steps(W.read_state(state_file), order)
        with events.writing(events.events_dir(paths.run_dir(season, week)) / f"{rid}.jsonl", rid):
            log = events.tee(log)
            events.run_start(
                kind="rehearsal",
                command=command,
                season=season,
                week=week,
                steps=list(order),
                from_step=order[0] if earlier else None,
                launched_by=launched_by,
                via=via,
                plan=[f"rehearsal as of {when:%a %Y-%m-%d %H:%M} UTC; special: {special}"],
                earlier=earlier,
            )
            try:
                state = W.run_weekly(
                    opts,
                    log=log,
                    funcs=step_funcs,
                    state_file=state_file,
                    steps=order,
                )
            except W.StepFailed as e:
                result.status, result.error = "failed", f"{e.step}: {e.detail}"
                state = W.read_state(state_file)
            report = paths.report_path(season, week)
            ran_steps = {n: s for n, s in (state or {}).get("steps", {}).items() if n in ran}
            degraded = any(s.get("status") == "degraded" for s in ran_steps.values())
            wrote = (ran_steps.get("digest") or {}).get("status") == "ok" and report.exists()
            status = "failed" if result.status == "failed" else "degraded" if degraded else "ok"
            failed = next((n for n, s in ran_steps.items() if s.get("status") == "failed"), None)
            events.run_end(
                status=status,
                exit_code=1 if status == "failed" else 0,
                message=f"rehearsal of {season} week {week:02d}: {status}"
                + (f" ({result.error})" if result.error else "")
                + ("; digest written to the rehearsal folder" if wrote else ""),
                published=False,
                failed_step=failed,
            )
    # only the steps this rehearsal ran (the state file keeps an earlier rehearsal's steps),
    # and a digest only if this run wrote one
    result.steps = {
        n: {k: s.get(k) for k in ("status", "detail")}
        for n, s in (state or {}).get("steps", {}).items()
        if n in ran
    }
    if result.status == "ok" and any(s["status"] == "degraded" for s in result.steps.values()):
        result.status = "degraded"
    digest_ok = (result.steps.get("digest") or {}).get("status") == "ok"
    result.report = report if digest_ok and report.exists() else None
    out = paths.run_dir(season, week) / RESULT_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.as_dict(), indent=2, default=str), encoding="utf-8")
    return result
