"""How far ESPN's live feed trails the field (LD01): `nfl live latency`.

It polls one game's scoreboard entry (`EspnClient.game`, the call behind "Check this play")
every 2 s and logs the first time each play shows up as ESPN's `lastPlay`. Every 20 s it
also reads the game's summary, whose plays carry `wallclock` (the snap time), and logs when
each play first appears there. Afterwards each play gets:

- **lag** = first seen in the feed - its snap (`wallclock`); it includes the play itself
  (a few seconds) and up to one poll interval (2 s) of our own;
- **lead** = the next snap - first seen: how long the new situation was on screen before the
  next play started. A positive lead on a 4th down means a check could have run in time.

The log is `{NFL_DATA_ROOT}/live/latency/<event>.jsonl` (one JSON object per line, appended
and flushed as it goes, so a crash keeps what was measured). Pre-game it polls every 30 s
until kickoff; it stops at the time limit, a minute after the game ends, or on Ctrl+C (the
report is still written, marked `interrupted`). Penalties before the snap (false starts,
delays of game) are logged but aren't snaps: they don't count in the statistics and are never
the "next snap" (Sol review, LD01).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from nflengine.live.espn import EspnClient, EspnError, check_event_id, utc_now
from nflengine.live.replay import pre_snap_penalty
from nflengine.live.state import (
    ADMIN_TYPES,
    competition,
    parse_time,
    plain,
    summary_plays,
)

POLL_S = 2.0
PRE_GAME_POLL_S = 30.0
SUMMARY_EVERY_S = 20.0
FINAL_GRACE_S = 60.0


def latency_path(folder: Path, event_id: str) -> Path:
    return folder / f"{check_event_id(event_id)}.jsonl"


def _write(fh, row: dict[str, Any]) -> None:
    fh.write(json.dumps(row, default=str) + "\n")
    fh.flush()


class _Watch:
    """One game being watched: what has been seen, and the log file."""

    def __init__(self, client, ev: str, fh, now, log) -> None:
        self.client, self.ev, self.fh, self.now, self.log = client, ev, fh, now, log
        self.seen_game: set[str] = set()
        self.seen_summary: set[str] = set()
        self.last_error: str | None = None

    def error(self, message: str | None) -> None:
        if message and message != self.last_error:
            _write(self.fh, {"kind": "error", "at": self.now().isoformat(), "error": message})
            self.log(f"ESPN: {message}")
            self.last_error = message

    def game(self) -> str | None:
        """Ask for the game once; log a new `lastPlay`. Returns ESPN's state (pre / in / post)
        or None when there was no answer."""
        try:
            f = self.client.game(self.ev)
        except EspnError as e:
            self.error(str(e))
            return None
        if f.stale:
            self.error(f.error)
        comp = competition(f.data)
        state = ((comp.get("status") or {}).get("type") or {}).get("state")
        sit = comp.get("situation") or {}
        last = sit.get("lastPlay") or {}
        pid = str(last.get("id")) if last.get("id") is not None else None
        if pid and pid not in self.seen_game and not f.stale:
            self.seen_game.add(pid)
            row = {
                "kind": "game",
                "play_id": pid,
                "type": plain((last.get("type") or {}).get("text"), 60),
                "text": plain(last.get("text"), 200),
                "seen_at": f.fetched_at.isoformat(),
                "situation": plain(sit.get("downDistanceText"), 60),
                "down": sit.get("down"),
            }
            _write(self.fh, row)
            self.log(f"{f.fetched_at:%H:%M:%S} {row['type']}: {row['situation'] or ''}")
        return state

    def summary(self) -> None:
        """Ask for the summary; log every play not logged yet with its snap time."""
        try:
            f = self.client.summary(self.ev)
        except EspnError as e:
            _write(
                self.fh, {"kind": "error", "at": self.now().isoformat(), "error": f"summary: {e}"}
            )
            return
        if f.stale:
            return
        for p in summary_plays(f.data):
            pid = str(p.get("id"))
            if pid in self.seen_summary:
                continue
            self.seen_summary.add(pid)
            text = str(p.get("text") or "")
            _write(
                self.fh,
                {
                    "kind": "summary",
                    "play_id": pid,
                    "type": plain((p.get("type") or {}).get("text"), 60),
                    "down": (p.get("start") or {}).get("down"),
                    "pre_snap": pre_snap_penalty(text),
                    "wallclock": p.get("wallclock"),
                    "seen_at": f.fetched_at.isoformat(),
                },
            )


def run_latency(
    event_id: str,
    minutes: float,
    folder: Path,
    *,
    client: EspnClient | None = None,
    poll_s: float = POLL_S,
    summary_every_s: float = SUMMARY_EVERY_S,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utc_now,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Poll one game for `minutes`, log each play's first sighting, return `summarize(...)`
    (+ `interrupted`)."""
    ev = check_event_id(event_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = latency_path(folder, ev)
    own = client is None
    client = client or EspnClient()
    deadline = monotonic() + minutes * 60.0
    try:
        with path.open("a", encoding="utf-8") as fh:
            _write(
                fh,
                {
                    "kind": "start",
                    "event": ev,
                    "at": now().isoformat(),
                    "minutes": minutes,
                    "poll_s": poll_s,
                    "summary_every_s": summary_every_s,
                },
            )
            w = _Watch(client, ev, fh, now, log)
            interrupted = False
            try:
                next_summary = monotonic()
                final_at: float | None = None
                while monotonic() < deadline:
                    state = w.game()
                    if state == "post" and final_at is None:
                        final_at = monotonic()
                        log("final: one more minute for the last plays")
                    if final_at is not None and monotonic() - final_at > FINAL_GRACE_S:
                        break
                    if state == "in" and monotonic() >= next_summary:
                        w.summary()
                        next_summary = monotonic() + summary_every_s
                    if state == "pre":
                        sleep(min(PRE_GAME_POLL_S, max(0.0, deadline - monotonic())))
                        continue
                    sleep(max(poll_s, client.next_allowed_in(ev)))
            except KeyboardInterrupt:
                interrupted = True
                log("stopped (Ctrl+C): writing the report")
            w.summary()  # every play's snap time
            stats = {**summarize(path), "interrupted": interrupted}
            _write(fh, {"kind": "end", "at": now().isoformat(), **stats})
    finally:
        if own:
            client.close()
    return stats


def _pct(values: list[float], q: float) -> float | None:
    return round(float(np.percentile(values, q)), 1) if values else None


def summarize(path: Path) -> dict[str, Any]:
    """Per-play lag and lead from a latency log, and their median / p90 / worst case. A play's
    first sighting is the earliest across restarts (game and summary alike)."""
    game: dict[str, dict[str, Any]] = {}
    summ: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        pid = r.get("play_id")
        if r.get("kind") == "game" and pid:
            t = parse_time(r["seen_at"])
            if pid not in game or t < game[pid]["seen"]:
                game[pid] = {"seen": t, "type": r.get("type"), "down": r.get("down")}
        elif r.get("kind") == "summary" and pid:
            seen = parse_time(r.get("seen_at"))
            if pid not in summ:
                order.append(pid)
            elif summ[pid]["seen"] is not None and (seen is None or summ[pid]["seen"] <= seen):
                seen = summ[pid]["seen"]  # keep the first sighting; take the newest metadata
            summ[pid] = {
                "wall": parse_time(r.get("wallclock")),
                "seen": seen,
                "type": r.get("type"),
                "down": r.get("down"),
                "pre_snap": bool(r.get("pre_snap")),
            }
    snaps = [
        p
        for p in order
        if summ[p]["wall"] is not None
        and (summ[p]["type"] or "") not in ADMIN_TYPES
        and summ[p]["down"] in (1, 2, 3, 4)
        and not summ[p]["pre_snap"]
    ]
    rows = []
    for i, pid in enumerate(snaps):
        if pid not in game:
            continue
        g, s = game[pid], summ[pid]
        nxt = summ[snaps[i + 1]] if i + 1 < len(snaps) else None
        rows.append(
            {
                "play_id": pid,
                "type": s["type"],
                "lag_s": (g["seen"] - s["wall"]).total_seconds(),
                "summary_lag_s": (s["seen"] - s["wall"]).total_seconds() if s["seen"] else None,
                "lead_s": (nxt["wall"] - g["seen"]).total_seconds() if nxt else None,
                "next_down": nxt["down"] if nxt else None,
            }
        )
    lags = [r["lag_s"] for r in rows]
    leads = [r["lead_s"] for r in rows if r["lead_s"] is not None]
    lead4 = [r["lead_s"] for r in rows if r["lead_s"] is not None and r["next_down"] == 4]
    worst = max(rows, key=lambda r: r["lag_s"]) if rows else None
    return {
        "plays": len(rows),
        "plays_missed": len([p for p in snaps if p not in game]),
        "lag_median_s": _pct(lags, 50),
        "lag_p90_s": _pct(lags, 90),
        "lag_max_s": round(max(lags), 1) if lags else None,
        "worst_play": worst,
        "summary_lag_median_s": _pct([r["summary_lag_s"] for r in rows if r["summary_lag_s"]], 50),
        "lead_median_s": _pct(leads, 50),
        "lead_positive_share": round(sum(x > 0 for x in leads) / len(leads), 3) if leads else None,
        "fourth_down_lead_median_s": _pct(lead4, 50),
        "fourth_down_lead_positive_share": (
            round(sum(x > 0 for x in lead4) / len(lead4), 3) if lead4 else None
        ),
    }
