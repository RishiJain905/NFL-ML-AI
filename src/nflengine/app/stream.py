"""`GET /api/run/stream`: the current run's events as server-sent events (CR02).

The stream follows the run `/api/run/current` reports (the one in progress, else the last one
the app launched) and tails its `events/<run_id>.jsonl`:

- one `run` message per event line: `id: <run id>:<seq>`, `event: run`, `data: <the event>`;
  every string in it is cleaned on the way out (the data root's path removed, `scrub()` again);
- a reconnect resumes after `Last-Event-ID` (EventSource sends it by itself) **only if it names
  the same run**: a cursor from another run (a resume started meanwhile) replays the current
  run from its start (Sol review); `?after=N` resumes the current run after N;
- a comment line every `heartbeat_s` (15 s) keeps a 40-minute GLM wait alive; no timeout;
- after the run's `run_end` (or when the run is no longer going and its file has been read
  to the end), one `end` message whose data is the final RunInfo, then the stream closes.

Only complete lines are sent: a reader polling while the run appends never sees half an
event. The file is opened, read from the last offset and closed on every poll (D100: never a
held handle on a file the run writes).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from nflengine.app.jsonsafe import dumps
from nflengine.app.readers.common import clean
from nflengine.ops import events as E
from nflengine.paths import DataPaths

TEXT_LIMIT = 800


def clean_event(value: Any, paths: DataPaths) -> Any:
    """Every string in an event, as the browser may see it (root path removed, scrubbed)."""
    if isinstance(value, str):
        return clean(value, paths, TEXT_LIMIT)
    if isinstance(value, dict):
        return {k: clean_event(v, paths) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_event(v, paths) for v in value]
    return value


class Tail:
    """Complete new lines of a growing file, read from the last offset on each call."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0

    def read(self) -> list[bytes]:
        try:
            with self.path.open("rb") as f:
                f.seek(self.offset)
                chunk = f.read()
        except OSError:
            return []
        end = chunk.rfind(b"\n")
        if end < 0:
            return []
        self.offset += end + 1
        return [line for line in chunk[: end + 1].split(b"\n") if line.strip()]


def parse_cursor(last_event_id: str | None) -> tuple[str | None, int]:
    """`"<run id>:<seq>"` (or a bare seq) → (run id, seq); anything else → (None, 0)."""
    text = (last_event_id or "").strip()
    run_id, _, seq = text.rpartition(":")
    if not seq.isdigit():
        return None, 0
    if run_id and not E.valid_run_id(run_id):
        return None, 0
    return (run_id or None), int(seq)


def sse(event: str | None, data: Any, id_: int | str | None = None) -> bytes:
    out = []
    if id_ is not None:
        out.append(f"id: {id_}")
    if event:
        out.append(f"event: {event}")
    out.append(f"data: {dumps(data)}")
    return ("\n".join(out) + "\n\n").encode("utf-8")


async def run_stream(
    *,
    current: Callable[[], dict[str, Any]],
    paths: DataPaths,
    after: int,
    disconnected: Callable[[], Any],
    after_run: str | None = None,
    poll_s: float = 0.5,
    heartbeat_s: float = 15.0,
    public: Callable[[dict[str, Any]], dict[str, Any]],
) -> AsyncIterator[bytes]:
    """The generator behind the endpoint. `current()` is the runner's view (with the
    server-only `_events` path); `public()` strips it for the browser. `after_run`: the run the
    `after` cursor belongs to (from `Last-Event-ID`); another run's cursor counts as 0."""
    import json
    import time

    yield b"retry: 3000\n\n"
    first = current()
    run = first.get("run")
    if run is None:  # nothing to follow
        yield sse("end", None)
        return
    run_id = run.get("run_id")
    started = run.get("started")
    if after_run is not None and after_run != run_id:
        after = 0  # a cursor from another run: replay this one from its start
    # the events file where the run writes it, even before it exists: a terminal run that
    # writes and ends between two polls is still read (Sol review)
    first_path = run.get("_events_path") or run.get("_events")
    tail: Tail | None = Tail(Path(first_path)) if first_path else None
    seen_end = False
    end_event: dict[str, Any] | None = None
    last_beat = time.monotonic()
    quiet_polls = 0
    while True:
        if await disconnected():
            return
        now_state = current()
        now_run = now_state.get("run") or {}
        same = now_run.get("run_id") == run_id if run_id else now_run.get("started") == started
        if tail is None:
            path = (now_run.get("_events_path") or now_run.get("_events")) if same else None
            if path is not None:
                tail = Tail(Path(path))
                run_id = run_id or now_run.get("run_id")
        sent = False
        if tail is not None:
            for raw in tail.read():
                try:
                    ev = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(ev, dict):
                    continue
                seq = ev.get("seq")
                if isinstance(seq, int) and seq <= after:
                    continue
                ident = (
                    f"{ev.get('run_id') or run_id or ''}:{seq}" if isinstance(seq, int) else None
                )
                yield sse("run", clean_event(ev, paths), ident)
                sent = True
                if ev.get("type") == "run_end":
                    seen_end, end_event = True, ev
        going = same and now_state.get("state") == "running"
        if seen_end or not same or not going:
            # the run is over (or another one started): read what's left, then end
            quiet_polls = 0 if sent else quiet_polls + 1
            if seen_end or quiet_polls >= 2 or not same:
                yield sse("end", clean_event(_final(run, now_run, same, end_event, public), paths))
                return
        if time.monotonic() - last_beat >= heartbeat_s:
            yield b": hb\n\n"
            last_beat = time.monotonic()
        await asyncio.sleep(poll_s)


def _final(
    run: dict[str, Any],
    now_run: dict[str, Any],
    same: bool,
    end_event: dict[str, Any] | None,
    public: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any] | None:
    """The `end` message's RunInfo. The runner's view when it still describes this run (an
    app-launched one); otherwise (a terminal run, once its lock is gone) the run as first seen,
    completed from its own `run_end`, or `interrupted` if it ended without one."""
    body = public({"state": "finished", "run": now_run if same else run}).get("run")
    if body is None:
        return None
    if end_event is not None and (not same or body.get("status") is None):
        body = {
            **body,
            **{k: end_event.get(k) for k in ("status", "exit_code", "published", "failed_step")},
            "message": end_event.get("message"),
            "material": end_event.get("material"),
            "finished": end_event.get("t"),
        }
    elif body.get("status") is None:
        body = {**body, "status": "interrupted"}
    return body
