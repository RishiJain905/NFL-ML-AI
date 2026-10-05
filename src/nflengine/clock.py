"""The live steps' clock (P10).

The weekly steps ask "what time is it?" to decide which games have already kicked off (a
started game keeps its saved prediction and gets no new one, D55) and to stamp what they
write. `utc_now()` is the real time, unless a rehearsal (`ops/rehearsal.py`) pins it to the
Tuesday before a past week with `pinned(...)`, so that week's games still count as upcoming
and the live code path can run on it as if it were that Tuesday.

Run records (`ops/records.utc_now`), file timestamps and W&B keep the real clock.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager

_pinned: dt.datetime | None = None


def utc_now() -> dt.datetime:
    """Now in UTC (whole seconds): the pinned time during a rehearsal, else the real one."""
    return (_pinned or dt.datetime.now(dt.UTC)).replace(microsecond=0)


def is_pinned() -> bool:
    return _pinned is not None


@contextmanager
def pinned(at: dt.datetime) -> Iterator[dt.datetime]:
    """Pin `utc_now()` to `at` (a naive time means UTC) inside the block."""
    global _pinned
    if at.tzinfo is None:
        at = at.replace(tzinfo=dt.UTC)
    previous, _pinned = _pinned, at.astimezone(dt.UTC)
    try:
        yield _pinned
    finally:
        _pinned = previous
