"""Shared records for weekly operations (P07): the pipeline history file, drift signals and
alerts. Written first as the contract between the run summary (`ops/summary.py`), the drift
checks (`ops/drift.py`) and the season dashboard (`ops/dashboard.py`).

Files (live runs under `runs/<season>/`, simulations under `runs/digest-backtests/<season>/`):

- `runs/<season>/week<NN>/run_summary.json`: one weekly run in full (`RUN_SUMMARY_FILE`).
- `runs/<season>/pipeline_history.parquet`: one row per weekly run, append-only
  (`HISTORY_SCHEMA`). A week can have several rows (a "not ready" retry, then the real run,
  then a resume); weekly views take the last row per (season, week, kind) with `latest_runs`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

RUN_SUMMARY_FILE = "run_summary.json"
HISTORY_FILE = "pipeline_history.parquet"

# kind: main (the Tuesday run) | injury-update (Saturday) | simulation (`--as-of` in the past)
KINDS = ("main", "injury-update", "simulation")
# status of a whole run: ok | degraded (a fail-soft step degraded) | failed (a step failed) |
# not_ready (the previous week isn't final yet: exit 3, retry later) | already_done (the week
# was already published: exit 0, nothing ran) | locked (another run holds the lock: exit 4).
# Runs that stop before taking the lock (already_done, idle, locked, a not-yet-scheduled
# playoff round, a dry run) write no summary or history row: they did nothing.
RUN_STATUSES = ("ok", "degraded", "failed", "not_ready", "already_done", "locked")

HISTORY_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32(),
    "week": pl.Int32(),
    "kind": pl.String(),
    "run_id": pl.String(),  # started time, UTC, "YYYYMMDDTHHMMSSZ"
    "started": pl.String(),  # ISO 8601 UTC
    "finished": pl.String(),  # ISO 8601 UTC
    "status": pl.String(),
    "total_seconds": pl.Float64(),
    "deadline": pl.String(),  # first kickoff of the week's slate, ISO UTC (null if unknown)
    "on_time": pl.Boolean(),  # finished before the deadline (null when nothing was published)
    "hours_before_deadline": pl.Float64(),
    "failed_step": pl.String(),
    "degraded_steps": pl.String(),  # comma-separated
    "checks_passed": pl.Boolean(),  # the digest's final checks (null if no digest this run)
    "regenerated": pl.Boolean(),
    "banner": pl.Boolean(),
    "drift_alerts": pl.Int32(),
    "launched_by": pl.String(),
}


def history_path(run_root: Path, season: int) -> Path:
    return run_root / str(season) / HISTORY_FILE


def append_history(path: Path, row: dict[str, Any]) -> pl.DataFrame:
    """Append one run to the history file (created on first use); returns the whole file."""
    new = pl.DataFrame([{k: row.get(k) for k in HISTORY_SCHEMA}], schema=HISTORY_SCHEMA)
    if path.exists():
        old = pl.read_parquet(path)
        old = old.with_columns(
            pl.lit(None, dtype=t).alias(c) for c, t in HISTORY_SCHEMA.items() if c not in old
        ).select(list(HISTORY_SCHEMA))
        new = pl.concat([old.cast(HISTORY_SCHEMA), new])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    new.write_parquet(tmp, compression="zstd")
    tmp.replace(path)  # atomic: a crash mid-write never leaves a broken history file
    return new


def read_history(path: Path) -> pl.DataFrame:
    if not path.exists():
        return pl.DataFrame(schema=HISTORY_SCHEMA)
    return pl.read_parquet(path)


def latest_runs(history: pl.DataFrame, kind: str = "main") -> pl.DataFrame:
    """One row per (season, week) of one kind, ignoring runs that did nothing (`not_ready`,
    `already_done`, `locked`): the record of what each week's run delivered. The **last run
    that published** (it has a digest verdict) wins, since its digest replaced the report: a
    later resume that failed before the digest doesn't erase the week's on-time and checks
    point. Weeks that never published keep their last run."""
    done = history.filter(
        (pl.col("kind") == kind) & ~pl.col("status").is_in(["not_ready", "already_done", "locked"])
    )
    published = done.filter(pl.col("checks_passed").is_not_null()).sort("started")
    last_pub = published.group_by("season", "week", maintain_order=True).last()
    rest = done.join(last_pub.select("season", "week"), on=["season", "week"], how="anti")
    last_rest = rest.sort("started").group_by("season", "week", maintain_order=True).last()
    return pl.concat([last_pub, last_rest]).sort("season", "week")


@dataclass
class DriftSignal:
    """One drift check (documentation/08 -> Drift signals). Alerts only: nothing retunes."""

    name: str  # game_vs_elo | calibration | player_vs_baseline | data_freshness | checks
    status: str  # ok | alert | insufficient_data
    value: float | None  # the measured number (e.g. rolling Brier gap, season ECE)
    threshold: float | None
    detail: str  # one plain sentence with the numbers behind the status
    response: str  # what a person should do (documentation/08); never automatic
    group: str | None = None  # position group for player_vs_baseline
    weeks: list[int] = field(default_factory=list)  # weeks the signal looked at

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Alert:
    """Something a person should look at. Manual-first (D71): alerts go to the console,
    `run_summary.json` and, when enabled, a W&B alert (W&B's own email / app; no SMTP)."""

    level: str  # info | warn | error
    title: str  # short, e.g. "2026 week 5: game model behind Elo"
    text: str  # plain sentences, no secrets, no raw exception messages
    source: str  # drift:<name> | step:<name> | readiness | lock | calendar

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def iso(t: dt.datetime | None) -> str | None:
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.UTC)
    return t.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
