"""Progress events (CR02): a run's step-by-step story in `events/<run-id>.jsonl`.

The control room draws a live run from this file (documentation/control-room/CR02). Every
weekly run, resume, Saturday injury update and rehearsal writes one file per invocation,
next to the week's other run files: `runs/<S>/week<NN>/events/<run-id>.jsonl` (under the
rehearsal folder in a rehearsal). One JSON object per line, each with `seq` (1, 2, ...),
`t` (local time with its offset), `run_id` and `type`:

| type | fields |
|---|---|
| `run_start` | kind, command, season, week, steps, from_step, earlier, launched_by, via, plan |
| `step_start` | step |
| `progress` | step, done, total, fraction (0-1 within the step), label |
| `step_end` | step, status (ok / degraded / failed), seconds, detail |
| `llm` | phase (start / rewrite / done / failed), writer, model, route, provider, seconds, |
|       | usage {prompt, completion, reasoning}, cost |
| `log` | step, text, cls: the console line (markup removed) |
| `run_end` | status, exit_code, message, published, failed_step, material |

Rules:
- **Nothing here can change a run.** Without a writer every call is a no-op (tests, old
  callers, `nfl weekly run` from code); with one, any error while writing is swallowed and
  the writer goes quiet. Steps never see an exception from this module.
- Every text passes through `ops.summary.scrub()` before it's written.
- Each line is written whole (one `write` of a complete line) and flushed, so a reader
  that tails the file never sees half an event.
- Progress inside a step can be split into parts (`portion`): the player step maps its
  scoreboard, the 23 refits, the team totals, the consistency layer and the graph write onto
  one 0-1 range, so the bar never goes backwards.
- The console stays as it was: events go to the file, never to extra console lines.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import json
import re
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

EVENTS_DIR = "events"
RUN_ID = re.compile(r"^\d{8}T\d{6}Z(?:-[a-z0-9]{4,8})?$")
PROGRESS_EVERY_S = 0.25  # at most ~4 progress events a second per step (the last one always)
TEXT_LIMIT = 600

_writer: contextvars.ContextVar[EventWriter | None] = contextvars.ContextVar(
    "nfl_event_writer", default=None
)
_span: contextvars.ContextVar[tuple[float, float]] = contextvars.ContextVar(
    "nfl_event_span", default=(0.0, 1.0)
)


def new_run_id(now: dt.datetime | None = None) -> str:
    """`20261006T140012Z`: the run's start in UTC (the file's name)."""
    now = now or dt.datetime.now(dt.UTC)
    return now.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def valid_run_id(text: str | None) -> bool:
    return bool(text) and bool(RUN_ID.fullmatch(text or ""))


def events_dir(run_dir: Path) -> Path:
    return run_dir / EVENTS_DIR


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


_ROOTS: contextvars.ContextVar[tuple[Any, ...]] = contextvars.ContextVar(
    "nfl_event_roots", default=()
)


def _scrub(text: Any, limit: int = TEXT_LIMIT) -> str:
    """Scrubbed and capped, with the data root(s) made relative **first**: a cap that cut
    through the root's path would leave a partial path no later clean-up can match (Sol
    review, CR02)."""
    from nflengine.ops.summary import scrub
    from nflengine.paths import relative_paths

    s = "" if text is None else str(text)
    for root in _ROOTS.get():
        s = relative_paths(s, root)
    return scrub(s, limit)


def _data_roots() -> tuple[Any, ...]:
    """The data root (and, in a rehearsal, the live root behind the redirect)."""
    try:
        from nflengine.paths import ensure_data_root

        paths = ensure_data_root(create_dirs=False)
    except Exception:
        return ()
    roots = [paths.root]
    live = getattr(paths, "live", None)
    if live is not None:
        roots.append(live)
    # the longest first: a rehearsal folder sits inside the live root
    return tuple(sorted(roots, key=lambda r: len(str(r)), reverse=True))


_MARKUP = re.compile(r"\[/?[a-z][a-z0-9 _#.,=-]*\]", re.IGNORECASE)
_STYLE_CLS = (("red", "err"), ("yellow", "warn"), ("green", "ok"), ("dim", "dim"), ("bold", "acc"))


def plain(text: Any) -> tuple[str, str]:
    """A console line without its rich markup, and the log class its first style maps to
    (green ok, yellow warn, red err, dim, bold acc)."""
    s = text if isinstance(text, str) else str(text)
    cls = ""
    m = re.match(r"\s*\[([a-z][a-z0-9 _#]*)\]", s, re.IGNORECASE)
    if m:
        style = m.group(1).lower()
        cls = next((c for word, c in _STYLE_CLS if word in style), "")
    try:
        from rich.text import Text

        out = Text.from_markup(s).plain
    except Exception:  # not valid markup: strip what looks like tags
        out = _MARKUP.sub("", s)
    return out, cls


class EventWriter:
    """Appends events to one run's file. Thread-safe; fail-soft (see the module doc)."""

    def __init__(self, path: Path, run_id: str):
        self.path = Path(path)
        self.run_id = run_id
        self.seq = 0
        self.step: str | None = None
        self.broken = False
        self._lock = threading.Lock()
        self._last_progress: dict[str, tuple[float, str]] = {}

    def emit(self, type_: str, **fields: Any) -> None:
        if self.broken:
            return
        try:
            with self._lock:
                self.seq += 1
                event = {"seq": self.seq, "t": _now(), "run_id": self.run_id, "type": type_}
                event.update(fields)
                line = json.dumps(event, default=str, ensure_ascii=False) + "\n"
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="\n") as f:
                    f.write(line)
                    f.flush()
        except Exception:  # never let an event change the run
            self.broken = True

    # ---- the typed helpers ----------------------------------------------------------------

    def progress(self, step: str, done: float, total: float, label: str | None) -> None:
        lo, hi = _span.get()
        frac = 0.0 if total <= 0 else max(0.0, min(1.0, float(done) / float(total)))
        mapped = lo + (hi - lo) * frac
        now = time.monotonic()
        # throttle a fast loop, but never drop a new kind of label ("team stat totals" after
        # "refitting 23/23") or a part's last call
        kind = re.sub(r"\d+", "#", label or "")
        last = self._last_progress.get(step)
        if last is not None and now - last[0] < PROGRESS_EVERY_S and frac < 1.0 and kind == last[1]:
            return
        self._last_progress[step] = (now, kind)
        self.emit(
            "progress",
            step=step,
            done=done,
            total=total,
            fraction=round(mapped, 4),
            label=_scrub(label or "", 200),
        )


def current() -> EventWriter | None:
    return _writer.get()


@contextlib.contextmanager
def writing(path: Path, run_id: str) -> Iterator[EventWriter]:
    """Make `path` the current run's events file for the block (nested runs: innermost)."""
    w = EventWriter(path, run_id)
    token = _writer.set(w)
    roots = _ROOTS.set(_data_roots())
    try:
        yield w
    finally:
        _ROOTS.reset(roots)
        _writer.reset(token)


def emit(type_: str, **fields: Any) -> None:
    """Write one event if a run is recording (no-op otherwise; never raises)."""
    w = _writer.get()
    if w is None:
        return
    try:
        w.emit(type_, **fields)
    except Exception:
        w.broken = True


def run_start(
    *,
    kind: str,
    command: str,
    season: int | None,
    week: int | None,
    steps: list[str] | tuple[str, ...],
    from_step: str | None = None,
    launched_by: str | None = None,
    via: str | None = None,
    plan: list[str] | None = None,
    earlier: dict[str, Any] | None = None,
) -> None:
    """`earlier`: for a resume, the steps before `from_step` as an earlier sitting left them
    ({step: {status, seconds}}), so a live view can draw them as done."""
    emit(
        "run_start",
        kind=kind,
        command=_scrub(command, 300),
        season=season,
        week=week,
        steps=list(steps),
        from_step=from_step,
        launched_by=launched_by,
        via=via,
        plan=[_scrub(line, 300) for line in plan or []],
        earlier=earlier or {},
    )


def step_start(step: str) -> None:
    w = _writer.get()
    if w is not None:
        w.step = step
    emit("step_start", step=step)


def step_end(step: str, status: str, seconds: float, detail: Any) -> None:
    emit(
        "step_end",
        step=step,
        status=status,
        seconds=round(float(seconds), 1),
        detail=_scrub(detail),
    )
    w = _writer.get()
    if w is not None and w.step == step:
        w.step = None


def progress(step: str | None, done: float, total: float, label: str | None = None) -> None:
    """Progress inside a step (`step=None`: the step running now). Mapped through the
    enclosing `portion`; throttled to a few events a second (the last one always goes)."""
    w = _writer.get()
    if w is None:
        return
    try:
        name = step or w.step
        if name:
            w.progress(name, done, total, label)
    except Exception:
        w.broken = True


@contextlib.contextmanager
def portion(lo: float, hi: float) -> Iterator[None]:
    """Map the progress reported inside the block onto [lo, hi] of the enclosing range."""
    outer_lo, outer_hi = _span.get()
    a = outer_lo + (outer_hi - outer_lo) * lo
    b = outer_lo + (outer_hi - outer_lo) * hi
    token = _span.set((a, b))
    try:
        yield
    finally:
        _span.reset(token)


def llm(
    phase: str,
    *,
    writer: str | None,
    model: str | None = None,
    route: list[str] | None = None,
    provider: str | None = None,
    seconds: float | None = None,
    usage: dict[str, Any] | None = None,
    cost: float | None = None,
) -> None:
    emit(
        "llm",
        phase=phase,
        writer=writer,
        model=_scrub(model, 120) if model else None,
        route=[_scrub(r, 80) for r in route or []],
        provider=_scrub(provider, 80) if provider else None,
        seconds=None if seconds is None else round(float(seconds), 1),
        usage=usage,
        cost=cost,
    )


def run_end(
    *,
    status: str,
    exit_code: int,
    message: str | None,
    published: bool = False,
    failed_step: str | None = None,
    material: bool | None = None,
) -> None:
    emit(
        "run_end",
        status=status,
        exit_code=int(exit_code),
        message=_scrub(message or "", 400),
        published=bool(published),
        failed_step=failed_step,
        material=material,
    )


def tee(log: Callable[[Any], None]) -> Callable[[Any], None]:
    """The run's log function, also writing each line as a `log` event while a writer is
    active (the console output is unchanged)."""

    def _log(text: Any = "", *args: Any, **kwargs: Any) -> None:
        log(text, *args, **kwargs)
        w = _writer.get()
        if w is None or w.broken:
            return
        try:
            body, cls = plain(text)
            for line in body.splitlines() or [""]:
                if line.strip():
                    w.emit("log", step=w.step, text=_scrub(line), cls=cls)
        except Exception:
            w.broken = True

    return _log
