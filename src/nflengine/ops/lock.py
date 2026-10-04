"""One weekly run at a time (P07): an operating-system file lock under the data root's
`runs/` folder.

`nfl weekly run` and `nfl weekly injury-update` both write the same run folders, Neo4j and
W&B aliases, so they share one lock: `runs/.weekly.lock`. The lock itself is an OS
byte-range lock on that file (`msvcrt.locking` on Windows, `fcntl.flock` elsewhere), so the
operating system releases it the moment the holding process ends, crash included. Nothing
has to guess whether a lock is stale: no process-id checks, no age limits, no take-over race
between two runs, and a run that sleeps with the PC keeps its lock (Sol review, P07).

The file's text is only a note for people (pid, computer, command, start time). A second run
that finds the lock held reports that note; a run that acquires the lock and finds a note
left behind by a run that died reports it as `took_over`.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import socket
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

LOCK_NAME = ".weekly.lock"
LOCK_OFFSET = 1 << 20  # the locked byte sits past the note, so the note stays readable


class LockHeld(RuntimeError):
    """Another weekly run holds the lock."""

    def __init__(self, path: Path, holder: dict):
        self.path, self.holder = path, holder
        super().__init__(
            f"another weekly run holds {path} (pid {holder.get('pid')} on "
            f"{holder.get('host')}, `{holder.get('command')}`, since {holder.get('started')})"
        )


@dataclass(frozen=True)
class LockInfo:
    path: Path
    holder: dict
    took_over: dict | None = None  # the note a run that died left behind, if any


def lock_path(runs_root: Path) -> Path:
    return runs_root / LOCK_NAME


def _try_lock(fd: int) -> bool:
    """Take the OS lock without waiting; False if another handle holds it."""
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        with contextlib.suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)


def _read_note(fd: int) -> dict | None:
    os.lseek(fd, 0, os.SEEK_SET)
    raw = os.read(fd, 4096)
    if not raw.strip():
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def read_holder(path: Path) -> dict | None:
    """The note in the lock file (who holds it, or who held it last), or None."""
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        return json.loads(text) if text.strip() else None
    except json.JSONDecodeError:
        return None


def is_locked(path: Path) -> bool:
    """Is a weekly run holding the lock right now? (Checks without keeping it.)"""
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    try:
        if not _try_lock(fd):
            return True
        _unlock(fd)
        return False
    finally:
        os.close(fd)


@contextlib.contextmanager
def run_lock(path: Path, command: str, stale_hours: float | None = None) -> Iterator[LockInfo]:
    """Hold the weekly-run lock for the duration of the block, or raise `LockHeld`.
    (`stale_hours` is accepted for older callers and ignored: the OS releases a dead run's
    lock by itself.)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
    try:
        if not _try_lock(fd):
            raise LockHeld(path, _read_note(fd) or {})
        left_behind = _read_note(fd)  # a note but no lock: that run ended without cleaning up
        me = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "command": command,
            "started": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
        }
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(me).encode("utf-8"))
        try:
            yield LockInfo(path, me, left_behind)
        finally:
            os.ftruncate(fd, 0)  # the note goes with the lock
            _unlock(fd)
    finally:
        os.close(fd)
