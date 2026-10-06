"""Helpers every week reader uses: safe reads, and text that is fit for the browser.

Readers only read (documentation/control-room/README.md §4-§5):
- JSON through `read_json` (a missing or broken file is `None`, never an exception);
- Parquet through `read_parquet` with `memory_map=False`. A memory-mapped file can't be
  replaced on Windows while it is mapped, and the weekly run replaces its files in place
  (`tmp.replace(path)`), so the app never keeps a run's file open or mapped (D100);
- curated tables the same way (one short read of `curated/<table>.parquet`, no DuckDB
  connection to `nfl.duckdb`, which the curate step rebuilds by replacing the file).

Every free text passes through `clean()`: the data root's path becomes a relative path
(`D:\\nfl-ml-data\\runs\\2026\\week04\\x.parquet` -> `runs/2026/week04/x.parquet`: the root is
NFL_DATA_ROOT's value, an environment value the browser must not see), W&B run URLs are
pulled out by `wandb_urls()` first where a reader wants them, and `ops.summary.scrub` masks
anything shaped like a credential.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.ops.summary import scrub
from nflengine.paths import DataPaths, relative_paths, root_pattern, strip_partial_root

WANDB_RUN = re.compile(r"https://wandb\.ai/[\w.-]+/[\w.-]+/runs/([A-Za-z0-9]+)")
STEP_NAMES = ("ingest", "ready", "curate", "ratings", "game", "graph", "player", "digest")
NEO4J_BROWSER = "http://localhost:7474/browser/"


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_text(path: Path) -> str | None:
    """The file's text exactly as stored: no newline translation (the published digest is
    written on Windows with CRLF line ends, and the Digest tab sends it byte for byte)."""
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def read_parquet(path: Path, columns: list[str] | None = None) -> pl.DataFrame | None:
    """A whole Parquet file (or some columns), or None when it's missing or unreadable."""
    if not path.exists():
        return None
    try:
        df = pl.read_parquet(path, memory_map=False)
    except Exception:  # noqa: BLE001 - a half-written or foreign file is "no data"
        return None
    if columns is not None:
        df = df.select([c for c in columns if c in df.columns])
    return df


def curated(paths: DataPaths, table: str, columns: list[str] | None = None) -> pl.DataFrame | None:
    return read_parquet(paths.curated / f"{table}.parquet", columns)


_root_pattern = root_pattern  # one definition, shared with the progress events (CR02)


def strip_root(obj: Any, root: Path) -> Any:
    """Every string in a response with the data root made relative: the last pass over
    whatever a week endpoint returns, so a field cleaned without `paths` (a name, a label,
    a driver phrase) can't carry NFL_DATA_ROOT's value to the browser (Sol review, CR01)."""
    pattern = _root_pattern(root)

    def walk(o: Any) -> Any:
        if isinstance(o, str):
            o = pattern.sub(lambda m: (m.group(1) or "").replace("\\", "/") or ".", o)
            return strip_partial_root(o, root)
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list | tuple):
            return [walk(v) for v in o]
        return o

    return walk(obj)


def parse_time(text: Any) -> dt.datetime | None:
    """An ISO time with an offset; a naive one is this machine's local time (pre-P07)."""
    if not text:
        return None
    try:
        t = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()


def summary_is_current(summary: dict[str, Any] | None, steps: dict[str, Any]) -> bool:
    """`run_summary.json` describes the week's last run only if no step started or finished
    after it was written. A later sitting that dies before its records leaves the earlier
    summary behind (a "not ready" one, say), and that must not hide the new steps (Sol
    review, CR01)."""
    if not summary:
        return False
    fin = parse_time(summary.get("finished"))
    if fin is None:  # every real summary has one; without it there's nothing to compare
        return True
    times = [
        t
        for st in steps.values()
        for k in ("started", "finished")
        if (t := parse_time((st or {}).get(k))) is not None
    ]
    return not times or max(times) <= fin + dt.timedelta(seconds=2)


_DATA_ROOT: Path | None = None


def use_data_root(root: Path) -> None:
    """The app's data root, registered by the server on every request (`_Context.paths`):
    `clean()` removes it from every text, even where a reader passes no `paths`, and
    before truncating, so a capped text can't keep a partial root (Sol review, CR01)."""
    global _DATA_ROOT
    _DATA_ROOT = root


def clean(text: Any, paths: DataPaths | None, limit: int = 400) -> str | None:
    """Browser-safe free text: data-root paths made relative, then scrubbed and capped."""
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return None
    s = str(text)
    root = paths.root if paths is not None else _DATA_ROOT
    if root is not None:
        s = relative_paths(s, root)
    return scrub(s, limit)


def wandb_urls(text: str | None) -> list[str]:
    """The W&B run URLs a step detail names (the pipeline writes them into details)."""
    return [m.group(0) for m in WANDB_RUN.finditer(text or "")]


_WANDB_MENTION = re.compile(r"(?:W&B\s+)?" + WANDB_RUN.pattern)


def without_wandb(text: str) -> str:
    """A detail with its W&B URL shortened to `W&B <run id>` (the mockup's log format)."""
    return _WANDB_MENTION.sub(lambda m: f"W&B {m.group(1)}", text)


def num(x: Any, digits: int | None = None) -> float | None:
    """A finite float or None (NaN, inf, None and non-numbers become None)."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, digits) if digits is not None else f


def run_dir_files(paths: DataPaths, season: int, week: int) -> dict[str, bool]:
    """Which of the week's known files exist (README §9: week 4 has no run_summary.json)."""
    run_dir = paths.run_dir(season, week)
    names = (
        "weekly_run.json",
        "run_summary.json",
        "checks.json",
        "digest.md",
        "payload.json",
        "raw_llm_output.json",
        "graph_results.json",
        "predictions_games.parquet",
        "predictions_players.parquet",
        "predictions_teams.parquet",
        "watchlist.parquet",
        "consistency.json",
        "player_status.json",
        "injury_update.json",
    )
    return {n: (run_dir / n).exists() for n in names}
