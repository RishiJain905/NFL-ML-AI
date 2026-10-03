"""`nfl data-status`: what data we have, how fresh it is, and how well it joins."""

from __future__ import annotations

import json
import re
from pathlib import Path

import polars as pl
from rich.console import Group
from rich.table import Table

from nflengine.ingest.base import SnapshotStore
from nflengine.paths import ensure_data_root
from nflengine.settings import get_config


def _dir_size_mb(path: Path) -> float:
    return (
        sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6 if path.exists() else 0
    )


def _rows_from_manifests(store: SnapshotStore, source: str, dataset: str) -> int:
    parts = store.latest_parts(source, dataset)
    total = 0
    for p in parts.values():
        m = p.parent / "manifest.json"
        if m.exists():
            total += json.loads(m.read_text())["files"].get(p.name, {}).get("rows", 0)
    return total


def render_status() -> Group:
    paths = ensure_data_root()
    current = get_config().seasons.current
    store = SnapshotStore(paths.raw)

    t = Table(title=f"raw snapshots ({paths.raw})")
    for col in ("source/dataset", "latest snapshot", "seasons", f"{current} newest week", "rows"):
        t.add_column(col, overflow="fold")
    for source_dir in sorted(
        p for p in paths.raw.iterdir() if p.is_dir() and not p.name.startswith("_")
    ):
        for ds_dir in sorted(p for p in source_dir.iterdir() if p.is_dir()):
            source, dataset = source_dir.name, ds_dir.name
            snaps = store.snapshots(source, dataset)
            parts = store.latest_parts(source, dataset)
            if not parts:
                continue
            seasons = sorted(
                int(m.group(1)) for k in parts if (m := re.match(r"season=(\d{4})$", k))
            )
            season_txt = f"{seasons[0]}-{seasons[-1]}" if seasons else "all"
            newest_week = "-"
            cur = parts.get(f"season={current}")
            if cur is not None:
                cols = pl.read_parquet_schema(cur)
                if "week" in cols:
                    w = pl.read_parquet(cur, columns=["week"])["week"].max()
                    newest_week = str(w)
            t.add_row(
                f"{source}/{dataset}", snaps[-1], season_txt, newest_week,
                f"{_rows_from_manifests(store, source, dataset):,}",
            )  # fmt: skip

    extras = []
    joins = paths.curated / "_joins.json"
    if joins.exists():
        jt = Table(title="player-ID join rates (last curate)")
        for col in ("table", "key", "matched", "rate"):
            jt.add_column(col)
        for j in json.loads(joins.read_text()):
            jt.add_row(j["table"], j["key"], f"{j['matched']:,}/{j['total']:,}", f"{j['rate']:.1%}")
        extras.append(jt)
    q = paths.curated / "_quality" / "latest.json"
    if q.exists():
        data = json.loads(q.read_text())
        failed = [r["name"] for r in data["results"] if not r["passed"]]
        extras.append(
            f"Last quality run {data['run_at']}: "
            + ("all checks passed" if not failed else f"not passing: {failed}")
        )
    disk = Table(title="disk use on the data drive")
    disk.add_column("folder")
    disk.add_column("MB", justify="right")
    for name in ("raw", "research", "curated", "neo4j", "wandb", "cache"):
        disk.add_row(name, f"{_dir_size_mb(paths.root / name):,.0f}")
    return Group(t, *extras, disk)
