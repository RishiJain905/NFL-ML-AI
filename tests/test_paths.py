from pathlib import Path

import pytest

from nflengine.paths import DataPaths, DataRootError, configure_tool_env, ensure_data_root


def test_init_creates_root_and_standard_dirs(tmp_path: Path) -> None:
    paths = ensure_data_root(tmp_path / "nfl-ml-data", init=True)
    for d in paths.all_dirs():
        assert d.is_dir(), d
    assert paths.neo4j_data == tmp_path / "nfl-ml-data" / "neo4j" / "data"


def test_missing_root_without_init_fails_and_creates_nothing(tmp_path: Path) -> None:
    typo = tmp_path / "nfl-ml-dta"
    with pytest.raises(DataRootError, match="does not exist"):
        ensure_data_root(typo)
    assert not typo.exists()


def test_existing_root_gets_subdirs_without_init(tmp_path: Path) -> None:
    paths = ensure_data_root(tmp_path)
    assert paths.raw.is_dir() and paths.neo4j_plugins.is_dir()


def test_missing_drive_fails_clearly_and_writes_nothing() -> None:
    # A drive letter that should not exist on this machine.
    missing = Path("Q:/nfl-ml-data") if Path("C:/").exists() else Path("/nonexistent-drive/x")
    if Path(missing.anchor or "/nonexistent-drive").exists():
        pytest.skip("test drive unexpectedly exists")
    with pytest.raises(DataRootError, match="not connected"):
        ensure_data_root(missing)


def test_unset_root_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from nflengine import paths as paths_mod

    class _Env:
        nfl_data_root = None

    monkeypatch.setattr(paths_mod, "get_env", lambda: _Env())
    with pytest.raises(DataRootError, match="NFL_DATA_ROOT is not set"):
        ensure_data_root()


def test_run_and_report_paths() -> None:
    p = DataPaths(Path("D:/nfl-ml-data"))
    assert p.run_dir(2026, 5) == Path("D:/nfl-ml-data/runs/2026/week05")
    assert p.report_path(2026, 5).name == "week05-digest.md"


def test_configure_tool_env_points_caches_at_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("WANDB_DIR", "NFLREADPY_CACHE", "NFLREADPY_CACHE_DIR"):
        monkeypatch.delenv(var, raising=False)
    paths = ensure_data_root(tmp_path)
    configure_tool_env(paths)
    import os

    assert os.environ["WANDB_DIR"] == str(paths.wandb)
    assert os.environ["NFLREADPY_CACHE_DIR"] == str(paths.cache_nflreadpy)
    assert os.environ["NFLREADPY_CACHE"] == "filesystem"
    import nflreadpy.config

    cfg = nflreadpy.config.get_config()
    assert Path(cfg.cache_dir) == paths.cache_nflreadpy
    nflreadpy.config.reset_config()
