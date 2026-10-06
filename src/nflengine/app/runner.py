"""The control room's runner (CR02; documentation/control-room/README.md §5.3).

The browser sends a kind and its expected week, never a command line. This module builds
the command **in code** from three allowed kinds and nothing else:

| kind | command |
|---|---|
| `weekly` | `nfl weekly run --auto --expect-week N` (N = the server's calendar week) |
| `resume` | `nfl weekly run --season S --week N --from-step X --promote` (X = the failed step) |
| `injury_update` | `nfl weekly injury-update --auto` |

Under `nfl app --rehearsal` they become `nfl weekly rehearse --season S --week N --out
<data root>/rehearsals/control-room [--fresh | --steps <from the failed step>]` instead.
Every command also gets `--launched-by rishi` (a run started from the app is Rishi's) and the
hidden `--app-run <run id>` (the events file's name; W&B's `via:control-room` tag, D103).

The process runs **detached**: its own (hidden) console and process group, outside the app's
job when Windows allows it, output to `{data root}/cache/control-room/logs/<run id>.log`. It
outlives the app and the browser; the weekly-run lock (held by the child) stays the truth for
"is a run going?". The app's own record of what it launched is
`cache/control-room/runs/<run id>.json` (the app's only writes, README §4).

`current()` finds the run in progress after a page reload or an app restart: the lock (or a
rehearsal folder's lock) and its note, the newest events file of that week, and the record.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nflengine.app.readers.common import clean
from nflengine.ops import events as E
from nflengine.ops.lock import is_locked, lock_path, read_holder
from nflengine.paths import DataPaths

KINDS = ("weekly", "resume", "injury_update")
APP_DIR = ("cache", "control-room")
REPO_ROOT = Path(__file__).resolve().parents[3]
TAIL_LINES = 30
STARTING_S = 300  # a launch whose process lives this long without the lock still counts
OUT_SHOWN = "rehearsals/control-room"  # the rehearsal folder as the page shows it (relative)
# exit code -> status, for a run that ended without writing its events
EXIT_STATUS = {
    1: "failed",
    2: "usage",
    3: "not_ready",
    4: "locked",
    5: "no_data_root",
    6: "week_mismatch",
}


class RunRefused(Exception):
    """The request can't run: `code` is `not_allowed` (403), `locked` or `running` (409)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Launch:
    run_id: str
    kind: str  # weekly | resume | injury_update | rehearsal
    request_kind: str  # the browser's kind
    argv: list[str]  # never leaves the server (absolute paths)
    command: str  # the display form
    season: int
    week: int
    events_dir: Path  # where the child writes events/<run_id>.jsonl


@dataclass
class _Child:
    launch: Launch
    proc: Any  # subprocess.Popen, or a test fake with .poll() / .pid / .returncode
    started: dt.datetime


def new_run_id(now: dt.datetime | None = None) -> str:
    return f"{E.new_run_id(now)}-{secrets.token_hex(2)}"


def app_dir(paths: DataPaths) -> Path:
    return paths.root.joinpath(*APP_DIR)


def rehearsal_root(paths: DataPaths) -> Path:
    from nflengine.app.preflight import REHEARSAL_DIR

    return paths.root.joinpath(*REHEARSAL_DIR)


def build_launch(
    kind: str,
    *,
    preflight: dict[str, Any],
    paths: DataPaths,
    python: str,
    run_id: str,
    rehearsal: bool = False,
    fail_at: str | None = None,
) -> Launch:
    """The command for `kind`, from the server's own pre-flight (week, failed step), or
    `RunRefused`. Nothing the browser sent goes into the command."""
    if kind not in KINDS:
        raise RunRefused(
            "not_allowed", "Unknown kind: the app runs only weekly, resume and injury_update."
        )
    season, week = preflight.get("season"), preflight.get("week")
    if not isinstance(season, int) or not isinstance(week, int):
        raise RunRefused("not_allowed", "The calendar has no week to run.")
    gate = {
        "weekly": preflight["run"],
        "resume": preflight["resume"],
        "injury_update": preflight["injury_update"],
    }[kind]
    if not gate.get("allowed"):
        raise RunRefused("not_allowed", gate.get("reason") or "Not allowed right now.")
    base = [python, "-m", "nflengine.cli", "weekly"]
    tail = ["--app-run", run_id]
    week_dir = paths.run_dir(season, week)
    if rehearsal:
        root = rehearsal_root(paths)
        if kind == "weekly":
            argv = [
                *base,
                "rehearse",
                "--season",
                str(season),
                "--week",
                str(week),
                "--out",
                str(root),
                "--fresh",
                *(["--fail-at", fail_at] if fail_at else []),
                *tail,
            ]
            shown = f"nfl weekly rehearse --season {season} --week {week} --out {OUT_SHOWN} --fresh"
        elif kind == "resume":
            step = gate.get("step")
            from nflengine.app.preflight import REHEARSAL_STEPS

            if step not in REHEARSAL_STEPS:
                raise RunRefused("not_allowed", "No failed rehearsal step to resume from.")
            todo = ",".join(REHEARSAL_STEPS[REHEARSAL_STEPS.index(step) :])
            argv = [
                *base,
                "rehearse",
                "--season",
                str(season),
                "--week",
                str(week),
                "--steps",
                todo,
                "--out",
                str(root),
                *tail,
            ]
            shown = (
                f"nfl weekly rehearse --season {season} --week {week} --steps {todo} "
                f"--out {OUT_SHOWN}"
            )
        else:
            raise RunRefused("not_allowed", "No injury update in rehearsal mode.")
        return Launch(
            run_id,
            "rehearsal",
            kind,
            argv,
            shown,
            season,
            week,
            E.events_dir(root / "runs" / str(season) / f"week{week:02d}"),
        )
    who = ["--launched-by", "rishi"]
    if kind == "weekly":
        argv = [*base, "run", "--auto", "--expect-week", str(week), *who, *tail]
        shown = f"nfl weekly run --auto --expect-week {week}"
    elif kind == "resume":
        step = gate.get("step")
        from nflengine.weekly import STEPS

        if step not in STEPS:
            raise RunRefused("not_allowed", "No failed step to resume from.")
        argv = [
            *base,
            "run",
            "--season",
            str(season),
            "--week",
            str(week),
            "--from-step",
            step,
            "--promote",
            *who,
            *tail,
        ]
        shown = f"nfl weekly run --season {season} --week {week} --from-step {step} --promote"
    else:
        argv = [*base, "injury-update", "--auto", "--expect-week", str(week), *who, *tail]
        shown = f"nfl weekly injury-update --auto --expect-week {week}"
    return Launch(run_id, kind, kind, argv, shown, season, week, E.events_dir(week_dir))


def default_launcher(argv: list[str], log_file: Path) -> Any:
    """Start the command detached, output to `log_file`. On Windows: a new process group with
    a hidden console of its own (closing the app's terminal or Ctrl+C there doesn't reach it),
    outside the app's job object when the job allows it."""
    env = dict(os.environ)
    # display settings only (never secrets): UTF-8 output, no buffering, a wide console
    env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", COLUMNS="160")
    kwargs: dict[str, Any] = dict(
        cwd=str(REPO_ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )
    with open(log_file, "ab") as out:
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            try:
                return subprocess.Popen(
                    argv,
                    stdout=out,
                    creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB,
                    **kwargs,
                )
            except OSError:  # the job doesn't allow breakaway: still its own group / console
                return subprocess.Popen(argv, stdout=out, creationflags=flags, **kwargs)
        return subprocess.Popen(argv, stdout=out, start_new_session=True, **kwargs)


def _parse_time(text: Any) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()


def process_alive(pid: Any, launched: dt.datetime, slack_s: float = 60.0) -> bool:
    """Is `pid` alive **and** the process the app launched (created within `slack_s` of the
    launch)? A reused pid belongs to a process created later. Windows: the process's exit code
    and creation time (no `os.kill(pid, 0)`, which terminates on Windows); elsewhere a signal-0
    probe. Any error counts as not alive."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            created = _win_process_created(pid)
            if created is None:
                return False
            return abs((created - launched).total_seconds()) <= slack_s
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _win_process_created(pid: int) -> dt.datetime | None:
    """The creation time of a live Windows process, or None (gone, or not ours to query)."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None  # 259 = STILL_ACTIVE
        created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(ended),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime  # 100 ns since 1601
        return dt.datetime(1601, 1, 1, tzinfo=dt.UTC) + dt.timedelta(microseconds=ticks // 10)
    finally:
        k32.CloseHandle(handle)


def _iso(t: dt.datetime | None) -> str | None:
    return t.astimezone().isoformat(timespec="seconds") if t else None


def read_events(path: Path) -> list[dict[str, Any]]:
    """Every complete line of an events file (a half-written last line is left out)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    out = []
    for line in raw.split(b"\n")[:-1] if not raw.endswith(b"\n") else raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            ev = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


_WEEK = re.compile(r"--season\s+(\d{4}).*?--week\s+(\d{1,2})")


@dataclass
class Runner:
    """Launches the allowed commands and finds the current run. `launcher` is injectable:
    tests use a fake that records the argv and never starts a process."""

    rehearsal: bool = False
    fail_at: str | None = None  # `nfl app --rehearsal --fail-at STEP`: the first rehearsal only
    launcher: Callable[[list[str], Path], Any] | None = None  # None: `default_launcher`
    python: str = sys.executable
    _child: _Child | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- launching ----------------------------------------------------------------------

    def start(
        self,
        kind: str,
        expect_week: int,
        *,
        paths: DataPaths,
        preflight: dict[str, Any],
        now: dt.datetime,
        lock_held: bool,
    ) -> dict[str, Any]:
        """Start `kind`, or raise `RunRefused`. The browser's `expect_week` must equal the
        server's week (a cross-check: it never goes into the command)."""
        with self._lock:
            if self._alive() or self._starting(paths, now) is not None:
                raise RunRefused("running", "A run the app started is still going.")
            if lock_held:
                raise RunRefused("locked", "Another run holds the lock; wait for it to finish.")
            week = preflight.get("week")
            if expect_week != week:
                raise RunRefused(
                    "not_allowed",
                    f"The server's week is {week}, not {expect_week}: reload the page.",
                )
            run_id = new_run_id(now)
            launch = build_launch(
                kind,
                preflight=preflight,
                paths=paths,
                python=self.python,
                run_id=run_id,
                rehearsal=self.rehearsal,
                fail_at=self.fail_at if kind == "weekly" else None,
            )
            folder = app_dir(paths)
            (folder / "logs").mkdir(parents=True, exist_ok=True)
            (folder / "runs").mkdir(parents=True, exist_ok=True)
            log_file = folder / "logs" / f"{run_id}.log"
            proc = (self.launcher or default_launcher)(launch.argv, log_file)
            if kind == "weekly":
                self.fail_at = None  # the failure hook fires once per app session
            self._child = _Child(launch, proc, now)
            record = {
                "run_id": run_id,
                "kind": launch.kind,
                "request_kind": kind,
                "command": launch.command,
                "season": launch.season,
                "week": launch.week,
                "started": _iso(now),
                "pid": getattr(proc, "pid", None),
                "events_dir": str(launch.events_dir),
            }
            tmp = folder / "runs" / f"{run_id}.json.tmp"
            tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
            tmp.replace(folder / "runs" / f"{run_id}.json")
            return {
                "run_id": run_id,
                "kind": launch.kind,
                "command": launch.command,
                "season": launch.season,
                "week": launch.week,
            }

    def _alive(self) -> bool:
        return self._child is not None and self._child.proc.poll() is None

    def _starting(self, paths: DataPaths, now: dt.datetime | None = None) -> dict[str, Any] | None:
        """The newest app launch if its process is still alive within `STARTING_S` of the
        launch and hasn't taken its lock yet: after an app restart the in-memory child is gone,
        and without this a second launch could slip in before the first takes the lock (Sol
        review). The process is checked by pid **and** creation time (pids are reused)."""
        recs = self._records(paths)
        if not recs:
            return None
        rec = recs[0]
        launched = _parse_time(rec.get("started"))
        now = now or dt.datetime.now(dt.UTC)
        if launched is None or not 0 <= (now - launched).total_seconds() <= STARTING_S:
            return None
        return rec if process_alive(rec.get("pid"), launched) else None

    # ---- finding the current run ----------------------------------------------------------

    def _records(self, paths: DataPaths) -> list[dict[str, Any]]:
        folder = app_dir(paths) / "runs"
        out = []
        try:
            files = sorted(folder.glob("*.json"), reverse=True)[:20]
        except OSError:
            return []
        for f in files:
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(rec, dict) and E.valid_run_id(rec.get("run_id")):
                out.append(rec)
        return out

    def lock_file(self, paths: DataPaths) -> Path:
        if self.rehearsal:
            from nflengine.ops.rehearsal import lock_file

            return lock_file(rehearsal_root(paths))
        return lock_path(paths.runs)

    def current(self, paths: DataPaths, plan: Any = None) -> dict[str, Any]:
        """`RunCurrent` (web/src/api/types.ts) plus `_events` (the events file's path, server
        side only; `public()` drops it)."""
        lock = self.lock_file(paths)
        note = read_holder(lock)
        held = bool(note) and is_locked(lock)
        recs = self._records(paths)
        child = self._child
        alive = self._alive()
        if held:
            # the note names the run holding the lock (`run_lock(run_id=...)`): an app launch
            # if one of our records has that id, else a terminal run (never matched by pid,
            # which Windows reuses; Sol review)
            rec = next((r for r in recs if r["run_id"] == note.get("run_id")), None)
            if rec is not None:
                return {"state": "running", "run": self._info(rec, paths, running=True)}
            return {"state": "running", "run": self._terminal(note, paths, plan)}
        if alive and child is not None:  # starting: the child hasn't taken the lock yet
            rec = next((r for r in recs if r["run_id"] == child.launch.run_id), None)
            if rec is not None:
                return {"state": "running", "run": self._info(rec, paths, running=True)}
        starting = None if alive else self._starting(paths)
        if starting is not None:  # the same, after an app restart
            return {"state": "running", "run": self._info(starting, paths, running=True)}
        if not recs:
            return {"state": "idle", "run": None}
        return {"state": "finished", "run": self._info(recs[0], paths, running=False)}

    def _events_path(self, rec: dict[str, Any]) -> Path | None:
        """Where the run writes its events (whether or not the file exists yet)."""
        if not rec.get("events_dir"):
            return None
        return Path(rec["events_dir"]) / f"{rec['run_id']}.jsonl"

    def _events_file(self, rec: dict[str, Any]) -> Path | None:
        f = self._events_path(rec)
        return f if f is not None and f.exists() else None

    def _info(self, rec: dict[str, Any], paths: DataPaths, *, running: bool) -> dict[str, Any]:
        f = self._events_file(rec)
        evs = read_events(f) if f is not None else []
        end = next((e for e in reversed(evs) if e.get("type") == "run_end"), None)
        child = self._child
        code = None
        if child is not None and child.launch.run_id == rec["run_id"]:
            code = child.proc.poll()
        status: str | None
        if running:
            status = None
        elif end is not None:
            status, code = end.get("status"), end.get("exit_code")
        elif code is not None:
            status = (
                ("already_done" if rec.get("request_kind") == "weekly" else "idle")
                if code == 0
                else EXIT_STATUS.get(int(code), "failed")
            )
            if evs:  # it started its steps and died without a run_end
                status = "interrupted"
        else:
            status = "interrupted" if evs else "unknown"
        finished = None
        if not running:
            finished = (end or {}).get("t") or (evs[-1].get("t") if evs else None)
        info = {
            "run_id": rec["run_id"],
            "kind": rec.get("kind"),
            "launched_from": "app",
            "command": clean(rec.get("command"), paths, 200),
            "season": rec.get("season"),
            "week": rec.get("week"),
            "started": rec.get("started"),
            "finished": finished,
            "status": status,
            "exit_code": code,
            "message": clean((end or {}).get("message"), paths, 400) if end else None,
            "published": (end or {}).get("published") if end else None,
            "failed_step": (end or {}).get("failed_step") if end else None,
            "material": (end or {}).get("material") if end else None,
            "has_events": f is not None,
            "output_tail": [] if running or end is not None else self._tail(paths, rec),
            "_events": f,
            "_events_path": self._events_path(rec),
        }
        return info

    def _tail(self, paths: DataPaths, rec: dict[str, Any]) -> list[str]:
        log_file = app_dir(paths) / "logs" / f"{rec['run_id']}.log"
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return [clean(x, paths, 300) or "" for x in lines[-TAIL_LINES:] if x.strip()]

    def _terminal(self, note: dict[str, Any], paths: DataPaths, plan: Any) -> dict[str, Any]:
        """A run holding the lock that the app didn't start (a terminal)."""
        command = str(note.get("command") or "")
        m = _WEEK.search(command)
        season = week = None
        if m:
            season, week = int(m.group(1)), int(m.group(2))
        elif "--auto" in command and plan is not None and plan.week is not None:
            season, week = plan.season, plan.week
        if self.rehearsal:
            kind = "rehearsal"
            root = rehearsal_root(paths) / "runs"
        else:
            kind = (
                "injury_update"
                if "injury-update" in command
                else "resume"
                if "--from-step" in command
                else "weekly"
            )
            root = paths.runs
        f = expected = None
        if season is not None and week is not None and "--as-of" not in command:
            folder = E.events_dir(root / str(season) / f"week{week:02d}")
            rid = note.get("run_id")
            if E.valid_run_id(rid):  # the note names its events file exactly
                expected = folder / f"{rid}.jsonl"
                f = expected if expected.exists() else None
            else:  # a run started before run ids were in the note: the newest file since it
                try:
                    files = sorted(folder.glob("*.jsonl"))
                except OSError:
                    files = []
                started = note.get("started")
                if files and started:
                    t0 = dt.datetime.fromisoformat(str(started)) - dt.timedelta(seconds=5)
                    files = [
                        x
                        for x in files
                        if dt.datetime.fromtimestamp(x.stat().st_mtime, dt.UTC) >= t0
                    ]
                f = files[-1] if files else None
        rid = note.get("run_id") if E.valid_run_id(note.get("run_id")) else None
        return {
            "run_id": rid or (f.stem if f is not None else None),
            "kind": kind,
            "launched_from": "terminal",
            "command": clean(command, paths, 200),
            "season": season,
            "week": week,
            "started": note.get("started"),
            "finished": None,
            "status": None,
            "exit_code": None,
            "message": None,
            "published": None,
            "failed_step": None,
            "material": None,
            "has_events": f is not None,
            "output_tail": [],
            "_events": f,
            "_events_path": expected or f,
        }


def public(current: dict[str, Any]) -> dict[str, Any]:
    """`current()` without its server-only fields."""
    run = current.get("run")
    if run is None:
        return current
    return {**current, "run": {k: v for k, v in run.items() if not k.startswith("_")}}
