"""What the history graph holds, from its own files (AE02): `build.json` (the last bulk import)
and `append.json` (the current season's weekly append onto that build), both in
`{NFL_DATA_ROOT}/neo4j-history/`, and the build lock. Pure file reads: Ask's "as of" label and
`nfl history-graph status` never open a Neo4j session to know what was loaded.

An append counts only if it was made onto the current build (`build_id`): a rebuild drops the
appended weeks (the build appends the current season again at its end).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nflengine.paths import DataPaths

BUILD_FILE = "build.json"
APPEND_FILE = "append.json"
LOCK_FILE = ".history.lock"
# Written before a build replaces the store or an append starts writing, removed only after
# the result is verified (Sol, AE02): a crash, a reboot or a failed write leaves it behind,
# and while it's there Ask doesn't read the graph and `append` refuses after a build
UNFINISHED_FILE = ".history.unfinished"
LAB_LOCK_FILE = ".lab.lock"


def build_path(paths: DataPaths) -> Path:
    return paths.neo4j_history / BUILD_FILE


def append_path(paths: DataPaths) -> Path:
    return paths.neo4j_history / APPEND_FILE


def lock_path(paths: DataPaths) -> Path:
    """The history graph's own lock (build and append take it; Ask's history questions refuse
    while it's held). Not the weekly run's lock."""
    return paths.neo4j_history / LOCK_FILE


def unfinished_path(paths: DataPaths) -> Path:
    return paths.neo4j_history / UNFINISHED_FILE


def lab_lock_path(paths: DataPaths) -> Path:
    """The lab's and the path benchmark's own lock (AE03): one GDS experiment at a time, so two
    never drop each other's projections (Sol). Not the history lock: Ask, Connections and the
    pages keep answering while one runs."""
    return paths.neo4j_history / LAB_LOCK_FILE


def unfinished_next_to(lock: Path) -> bool:
    """Is there an unfinished marker beside this lock file? (Ask's guard holds the lock's path,
    not the data paths.)"""
    return (lock.parent / UNFINISHED_FILE).exists()


def mark_unfinished(
    paths: DataPaths,
    what: str,
    build_id: str | None = None,
    season: int | None = None,
    derive_pending: bool = False,
) -> None:
    """`what`: build | append | derive (with the build it appends onto and the season it writes: an
    append of another season mustn't clear it, Sol). Written and flushed before the store or
    the graph changes. `derive_pending`: an append that started over a half-written derive (or
    over an append that did) carries it, and hands the marker back to the derive when it's
    done (AE03 review, Sol): the person links stay marked until a derive finishes."""
    import datetime as dt
    import os

    doc: dict[str, Any] = {
        "what": what,
        "build_id": build_id,
        "season": season,
        "pid": os.getpid(),
        "started": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
    }
    if derive_pending:
        doc["derive_pending"] = True
    path = unfinished_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc))
        f.flush()
        os.fsync(f.fileno())


def clear_unfinished(paths: DataPaths) -> None:
    unfinished_path(paths).unlink(missing_ok=True)


def unfinished_advice(note: dict[str, Any]) -> str:
    """What to run after an unfinished build / append (fixed wording, from the marker's note)."""
    if note.get("what") == "derive":
        return "the last derive didn't finish: run `uv run nfl history-graph derive`"
    if note.get("what") == "append" and note.get("season"):
        return (
            f"the {note['season']} append didn't finish: run `uv run nfl history-graph append "
            f"--season {note['season']}`"
        )
    return "the last build didn't finish: run `uv run nfl history-graph build`"


def read_unfinished(paths: DataPaths) -> dict[str, Any] | None:
    """The marker's note (`{what, build_id, pid, started}`), `{}` when unreadable, None when
    there's no marker."""
    path = unfinished_path(paths)
    if not path.exists():
        return None
    return _read(path) or {}


def _read(path: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def read_build(paths: DataPaths) -> dict[str, Any] | None:
    """The last finished build (status ok), or None."""
    d = _read(build_path(paths))
    return d if d and d.get("status") == "ok" else None


def read_append(paths: DataPaths, build: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The last finished append onto the current build, or None."""
    build = build if build is not None else read_build(paths)
    d = _read(append_path(paths))
    if not d or d.get("status") != "ok" or not build:
        return None
    return d if d.get("build_id") == build.get("build_id") else None


def history_asof(paths: DataPaths) -> dict[str, Any] | None:
    """What the graph covers: `{first_season, through_season, season, week, built_at,
    appended_at, label}` ("history graph through 2025 + 2026 week 5"), or None before the
    first build. `season` / `week` are the appended current season's, None without one."""
    build = read_build(paths)
    if not build:
        return None
    app = read_append(paths, build)
    through = int(build["through_season"])
    season = int(app["season"]) if app and app.get("through_week") else None
    week = int(app["through_week"]) if season else None
    label = f"history graph {build['first_season']}-{through}"
    if season:
        label += f" + {season} week {week}"
    return {
        "first_season": int(build["first_season"]),
        "through_season": through,
        "season": season,
        "week": week,
        "built_at": build.get("built_at"),
        "appended_at": app.get("appended_at") if app else None,
        "label": label,
    }


def history_busy(paths: DataPaths) -> bool:
    """Is a build or an append running (holding the history lock), or did the last one not
    finish (the unfinished marker)? Either way the graph may be half-written. The lock's note
    first, as the weekly lock is read (`ask.guard.Sources.busy`): a bare probe takes the lock
    for an instant."""
    from nflengine.ops.lock import is_locked, read_holder

    if unfinished_path(paths).exists():
        return True
    path = lock_path(paths)
    try:
        return bool(read_holder(path)) and is_locked(path)
    except OSError:
        return False
