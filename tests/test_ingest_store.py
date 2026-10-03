import json
from pathlib import Path

import polars as pl
import pytest

from nflengine.ingest import nflverse as nv
from nflengine.ingest.base import SnapshotStore


def test_write_and_read_latest_per_part(tmp_path: Path) -> None:
    old = SnapshotStore(tmp_path, "2026-09-01")
    old.write_parquet("src", "ds", "season=2025", pl.DataFrame({"x": [1]}))
    old.write_parquet("src", "ds", "season=2026", pl.DataFrame({"x": [2]}))
    new = SnapshotStore(tmp_path, "2026-10-01")
    new.write_parquet("src", "ds", "season=2026", pl.DataFrame({"x": [3, 4]}))

    assert new.snapshots("src", "ds") == ["2026-09-01", "2026-10-01"]
    parts = new.latest_parts("src", "ds")
    assert "snapshot=2026-09-01" in str(parts["season=2025"])  # history kept from first pull
    assert "snapshot=2026-10-01" in str(parts["season=2026"])  # current season refreshed
    df = new.read_latest("src", "ds")
    assert sorted(df["x"].to_list()) == [1, 3, 4]
    manifest = json.loads((new.snapshot_dir("src", "ds") / "manifest.json").read_text())
    assert manifest["files"]["season=2026.parquet"]["rows"] == 2
    assert len(manifest["files"]["season=2026.parquet"]["sha256"]) == 64


def test_json_roundtrip(tmp_path: Path) -> None:
    s = SnapshotStore(tmp_path, "2026-10-01")
    s.write_json("espn", "news_raw", "season=2026", {"articles": [1, 2]})
    assert s.read_latest_json("espn", "news_raw", "season=2026") == {"articles": [1, 2]}
    assert s.read_latest_json("espn", "news_raw", "season=2025") is None


class FakeLoader:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.unpublished = False

    def __call__(self, season: int) -> pl.DataFrame:
        self.calls.append(season)
        if season == 2026 and self.unpublished:
            raise ValueError("Season must be between 2016 and 2025")
        return pl.DataFrame({"season": [season], "week": [1]})


@pytest.fixture
def fake_nflverse(monkeypatch: pytest.MonkeyPatch) -> FakeLoader:
    loader = FakeLoader()
    monkeypatch.setattr(
        nv, "_datasets", lambda: [nv.NflverseDataset("demo", loader, first_season=2024)]
    )
    monkeypatch.setattr("nflreadpy.config.update_config", lambda **_: None)
    return loader


def test_history_pulled_once_current_refreshed(tmp_path: Path, fake_nflverse) -> None:
    store = SnapshotStore(tmp_path / "raw", "2026-10-01")
    research = SnapshotStore(tmp_path / "research", "2026-10-01")
    nv.ingest_nflverse(store, research, 2026, log=lambda _: None)
    assert fake_nflverse.calls == [2024, 2025, 2026]
    fake_nflverse.calls.clear()
    store2 = SnapshotStore(tmp_path / "raw", "2026-10-08")
    nv.ingest_nflverse(store2, research, 2026, log=lambda _: None)
    assert fake_nflverse.calls == [2026]  # completed seasons skipped (D32)
    fake_nflverse.calls.clear()
    nv.ingest_nflverse(store2, research, 2026, refresh_history=True, log=lambda _: None)
    assert fake_nflverse.calls == [2024, 2025, 2026]


def test_unpublished_current_season_is_a_note_not_a_failure(tmp_path: Path, fake_nflverse) -> None:
    fake_nflverse.unpublished = True
    store = SnapshotStore(tmp_path / "raw", "2026-10-01")
    [res] = nv.ingest_nflverse(store, store, 2026, log=lambda _: None)
    assert res.status == "ok"
    assert "not published yet" in res.detail
    assert res.parts == ["season=2024", "season=2025"]
