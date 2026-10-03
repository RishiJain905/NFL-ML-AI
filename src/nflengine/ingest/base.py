"""Snapshot store for raw data (documentation/03 -> Storage layout).

Layout under {NFL_DATA_ROOT}/raw:

    {source}/{dataset}/snapshot=YYYY-MM-DD/{part}.parquet   (+ manifest.json)

`part` is `season=YYYY` for per-season datasets, or `all` for whole-dataset pulls.
Raw JSON responses (ESPN etc.) are stored next to the parsed table as `{part}.json.gz`.

Snapshot policy (decision D32): completed seasons are written once and only
re-pulled with `--refresh-history`; the current season and small static datasets are
re-snapshotted on every run. Readers always take the newest snapshot per part.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

SNAPSHOT_RE = re.compile(r"^snapshot=(\d{4}-\d{2}-\d{2})$")


def today() -> str:
    return dt.date.today().isoformat()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class DatasetResult:
    """Outcome of ingesting one dataset (one line of the run manifest)."""

    source: str
    dataset: str
    status: str  # ok | skipped | failed | partial
    rows: int = 0
    parts: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SnapshotStore:
    def __init__(self, raw_root: Path, snapshot_date: str | None = None):
        self.raw_root = raw_root
        self.snapshot_date = snapshot_date or today()

    # ---- writing -------------------------------------------------------------
    def snapshot_dir(self, source: str, dataset: str) -> Path:
        return self.raw_root / source / dataset / f"snapshot={self.snapshot_date}"

    def write_parquet(self, source: str, dataset: str, part: str, df: pl.DataFrame) -> Path:
        d = self.snapshot_dir(source, dataset)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{part}.parquet"
        tmp = path.with_suffix(".parquet.tmp")
        df.write_parquet(tmp, compression="zstd")
        tmp.replace(path)
        self._update_manifest(d, path.name, {"rows": df.height, "cols": df.width})
        return path

    def write_json(self, source: str, dataset: str, part: str, payload: Any) -> Path:
        d = self.snapshot_dir(source, dataset)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{part}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(payload, f)
        self._update_manifest(d, path.name, {})
        return path

    def _update_manifest(self, d: Path, filename: str, info: dict[str, Any]) -> None:
        mpath = d / "manifest.json"
        manifest = json.loads(mpath.read_text()) if mpath.exists() else {"files": {}}
        manifest["snapshot"] = self.snapshot_date
        manifest["files"][filename] = {
            **info,
            "sha256": file_sha256(d / filename),
            "written_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        mpath.write_text(json.dumps(manifest, indent=2))

    # ---- reading -------------------------------------------------------------
    def snapshots(self, source: str, dataset: str) -> list[str]:
        base = self.raw_root / source / dataset
        if not base.exists():
            return []
        dates = [m.group(1) for p in base.iterdir() if (m := SNAPSHOT_RE.match(p.name))]
        return sorted(dates)

    def has_part(self, source: str, dataset: str, part: str) -> bool:
        return self.latest_part_path(source, dataset, part) is not None

    def latest_part_path(self, source: str, dataset: str, part: str) -> Path | None:
        base = self.raw_root / source / dataset
        for date in reversed(self.snapshots(source, dataset)):
            p = base / f"snapshot={date}" / f"{part}.parquet"
            if p.exists():
                return p
        return None

    def latest_parts(self, source: str, dataset: str) -> dict[str, Path]:
        """Newest file for every part ever written (e.g. each season)."""
        base = self.raw_root / source / dataset
        parts: dict[str, Path] = {}
        for date in self.snapshots(source, dataset):  # ascending, newer overwrite older
            for p in (base / f"snapshot={date}").glob("*.parquet"):
                parts[p.stem] = p
        return parts

    def read_latest_parts(self, source: str, dataset: str) -> dict[str, pl.DataFrame]:
        """{part: DataFrame} using the newest snapshot of every part."""
        return {
            part: pl.read_parquet(p)
            for part, p in sorted(self.latest_parts(source, dataset).items())
        }

    def read_latest(self, source: str, dataset: str) -> pl.DataFrame | None:
        parts = self.latest_parts(source, dataset)
        if not parts:
            return None
        frames = [pl.read_parquet(p) for _, p in sorted(parts.items())]
        return pl.concat(frames, how="diagonal_relaxed") if len(frames) > 1 else frames[0]

    def read_latest_json(self, source: str, dataset: str, part: str) -> Any | None:
        base = self.raw_root / source / dataset
        for date in reversed(self.snapshots(source, dataset)):
            p = base / f"snapshot={date}" / f"{part}.json.gz"
            if p.exists():
                with gzip.open(p, "rt", encoding="utf-8") as f:
                    return json.load(f)
        return None


def write_run_manifest(raw_root: Path, results: list[DatasetResult], meta: dict[str, Any]) -> Path:
    d = raw_root / "_runs"
    d.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    path = d / f"ingest-{stamp}.json"
    path.write_text(
        json.dumps({**meta, "results": [r.to_dict() for r in results]}, indent=2, default=str)
    )
    return path
