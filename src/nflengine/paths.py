"""Every data path, built from NFL_DATA_ROOT (documentation/02 -> Storage).

Code never hard-codes data paths: it asks `ensure_data_root()` for a `DataPaths`.
The data root lives on the D: drive, which may be an external disk, so every
command checks the drive is connected before touching anything.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from nflengine.settings import get_env


class DataRootError(RuntimeError):
    """The data root is not configured or its drive is not connected."""


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def curated(self) -> Path:
        return self.root / "curated"

    @property
    def features(self) -> Path:
        return self.root / "features"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def bdb(self) -> Path:
        return self.root / "bdb"

    @property
    def research(self) -> Path:
        """Research-only data (e.g. participation); never read by live features."""
        return self.root / "research"

    @property
    def neo4j_data(self) -> Path:
        return self.root / "neo4j" / "data"

    @property
    def neo4j_logs(self) -> Path:
        return self.root / "neo4j" / "logs"

    @property
    def neo4j_plugins(self) -> Path:
        return self.root / "neo4j" / "plugins"

    @property
    def wandb(self) -> Path:
        return self.root / "wandb"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def cache_nflreadpy(self) -> Path:
        return self.cache / "nflreadpy"

    @property
    def cache_http(self) -> Path:
        return self.cache / "http"

    @property
    def cache_uv(self) -> Path:
        return self.cache / "uv"

    def run_dir(self, season: int, week: int) -> Path:
        return self.runs / str(season) / f"week{week:02d}"

    def report_path(self, season: int, week: int, kind: str = "digest") -> Path:
        return self.reports / str(season) / f"week{week:02d}-{kind}.md"

    def all_dirs(self) -> list[Path]:
        return [
            self.raw,
            self.curated,
            self.features,
            self.models,
            self.runs,
            self.reports,
            self.bdb,
            self.research,
            self.neo4j_data,
            self.neo4j_logs,
            self.neo4j_plugins,
            self.wandb,
            self.cache_nflreadpy,
            self.cache_http,
            self.cache_uv,
        ]


_redirect: DataPaths | None = None


@contextmanager
def redirect_data_root(paths: DataPaths) -> Iterator[DataPaths]:
    """Inside the block, `ensure_data_root()` (no explicit root) returns `paths` (P10
    rehearsals: `ops/rehearsal.RehearsalPaths` reads the real raw and curated data and
    writes features, models, runs and reports into a scratch folder)."""
    global _redirect
    previous, _redirect = _redirect, paths
    try:
        for d in paths.all_dirs():
            d.mkdir(parents=True, exist_ok=True)
        yield paths
    finally:
        _redirect = previous


def ensure_data_root(
    root: Path | str | None = None, init: bool = False, create_dirs: bool = True
) -> DataPaths:
    """Return DataPaths for the data root, failing clearly if the drive or root is missing.

    The root folder itself is only created when `init=True` (`nfl doctor --init-data-root`),
    so a typo in NFL_DATA_ROOT fails loudly instead of silently building a new tree.
    Standard subfolders are created whenever the root exists, unless `create_dirs=False`
    (the control room only reads, CR01). Inside `redirect_data_root` a call without `root`
    returns the redirected paths.
    """
    if root is None and _redirect is not None:
        return _redirect
    if root is None:
        root = get_env().nfl_data_root
    if root is None:
        raise DataRootError(
            "NFL_DATA_ROOT is not set. Add NFL_DATA_ROOT=D:/nfl-ml-data to the env file."
        )
    root = Path(root)
    anchor = Path(root.anchor) if root.anchor else root.parent
    if not anchor.exists():
        raise DataRootError(
            f"Data drive {anchor} is not connected (NFL_DATA_ROOT={root}). "
            "Connect the drive and retry. Nothing was written."
        )
    if not root.exists():
        if not init:
            raise DataRootError(
                f"Data root {root} does not exist. Check NFL_DATA_ROOT for typos, or run "
                "`nfl doctor --init-data-root` to create it."
            )
        root.mkdir(parents=True)
    paths = DataPaths(root)
    if create_dirs:
        for d in paths.all_dirs():
            d.mkdir(parents=True, exist_ok=True)
    return paths


def configure_tool_env(paths: DataPaths) -> None:
    """Point third-party tool caches/dirs at the data root.

    Env vars cover subprocesses and late imports; nflreadpy builds its config at
    import time, so it is also updated explicitly (safe whether or not it was
    already imported).
    """
    os.environ.setdefault("WANDB_DIR", str(paths.wandb))
    os.environ.setdefault("NFLREADPY_CACHE", "filesystem")
    os.environ.setdefault("NFLREADPY_CACHE_DIR", str(paths.cache_nflreadpy))
    import nflreadpy.config

    nflreadpy.config.update_config(cache_mode="filesystem", cache_dir=paths.cache_nflreadpy)
