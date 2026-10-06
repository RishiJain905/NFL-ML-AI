"""The status line's lock check must not get in the way of a weekly run that is starting."""

from __future__ import annotations

import json

from app_helpers import make_paths

from nflengine.app.readers.meta import lock_dict
from nflengine.ops.lock import lock_path, run_lock


def test_no_note_means_free_without_touching_the_lock(tmp_path):
    paths = make_paths(tmp_path)

    def must_not_probe(_path):
        raise AssertionError("probed the lock while no run had written a note")

    assert lock_dict(paths, probe=must_not_probe) == {
        "held": False,
        "command": None,
        "started": None,
    }
    lock_path(paths.runs).write_text("")  # a finished run leaves an empty note
    assert lock_dict(paths, probe=must_not_probe)["held"] is False


def test_a_crashed_runs_note_reads_as_free(tmp_path):
    paths = make_paths(tmp_path)
    note = {"pid": 1, "host": "x", "command": "nfl weekly run --auto", "started": "2026-10-06"}
    lock_path(paths.runs).write_text(json.dumps(note))  # the OS released the lock with the process
    assert lock_dict(paths) == {"held": False, "command": None, "started": None}


def test_a_live_run_reads_as_held(tmp_path):
    paths = make_paths(tmp_path)
    with run_lock(lock_path(paths.runs), "nfl weekly run --auto") as info:
        got = lock_dict(paths)
    assert got == {
        "held": True,
        "command": "nfl weekly run --auto",
        "started": info.holder["started"],
    }
