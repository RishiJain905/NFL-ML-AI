"""CR01 parity: the week endpoints against the real files on the data root (`-m integration`).

Week 4 of 2026 is the first live week (no run records). Its Results become gradable once
week 5's run exists (Tuesday 2026-10-06); until then the graded path is checked on P07's
simulated 2025 weeks (`runs/digest-backtests/2025`), whose next-week report cards exist.
Every check also confirms the files it read are unchanged afterwards."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pytest
from app_helpers import BASE, FakeStatus
from fastapi.testclient import TestClient

from nflengine.app.readers.results import get_results
from nflengine.app.server import AppSettings, create_app
from nflengine.ops.summary import scrub
from nflengine.paths import DataPaths, ensure_data_root

pytestmark = pytest.mark.integration
SEASON, WEEK = 2026, 4


@pytest.fixture(scope="module")
def paths() -> DataPaths:
    return ensure_data_root()


@pytest.fixture(scope="module")
def client(paths) -> TestClient:
    return TestClient(
        create_app(AppSettings(services=FakeStatus(), data_root=lambda: paths)), base_url=BASE
    )


def _hashes(files) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}


def test_games_match_the_prediction_file(client, paths):
    preds = pl.read_parquet(paths.run_dir(SEASON, WEEK) / "predictions_games.parquet")
    primary = {r["game_id"]: r for r in preds.filter(pl.col("is_primary")).iter_rows(named=True)}
    model_only = {
        r["game_id"]: r
        for r in preds.filter(pl.col("variant") == "model_only").iter_rows(named=True)
    }
    g = client.get(f"/api/weeks/{SEASON}/{WEEK}/games").json()
    assert g["status"] == "predicted" and len(g["games"]) == 16 == len(primary)
    for game in g["games"]:
        p = primary[game["game_id"]]
        assert game["p_home"] == pytest.approx(p["home_win_prob"], abs=1e-12)
        assert game["p_home_model_only"] == pytest.approx(
            model_only[game["game_id"]]["home_win_prob"], abs=1e-12
        )
        assert game["pts_home"] == pytest.approx(p["pred_home_points"], abs=1e-12)
        assert game["pts_away"] == pytest.approx(p["pred_away_points"], abs=1e-12)
        assert (game["home"], game["away"]) == (p["home_team"], p["away_team"])
    # the QB changes come from the payload (README §9), and they're all on the cards
    payload = json.loads((paths.run_dir(SEASON, WEEK) / "payload.json").read_text("utf-8"))
    flagged = sum(bool(x["qb_change_home"]) + bool(x["qb_change_away"]) for x in g["games"])
    assert flagged == len(payload["qb_changes"])


def test_players_have_every_projection(client, paths):
    p = client.get(f"/api/weeks/{SEASON}/{WEEK}/players").json()
    assert p["counts"]["total"] == 1860
    a = client.get(f"/api/weeks/{SEASON}/{WEEK}/players?stats=all").json()
    assert len(a["rows"]) == 1860
    payload = json.loads((paths.run_dir(SEASON, WEEK) / "payload.json").read_text("utf-8"))
    assert [w["player"] for w in p["watch"]] == [x["player"] for x in payload["players_to_watch"]]
    for w, x in zip(p["watch"], payload["players_to_watch"], strict=True):
        assert w["projection"] == pytest.approx(x["projection"]["value"], abs=1e-3)
        assert w["display"]["range"] == x["interval"]["display"]


def test_graph_counts_sum_to_the_build(client, paths):
    g = client.get(f"/api/weeks/{SEASON}/{WEEK}/graph").json()
    assert g["totals"]["nodes"] == 15522
    raw = json.loads((paths.run_dir(SEASON, WEEK) / "graph_results.json").read_text("utf-8"))
    rels = sum(v for k, v in raw["counts"].items() if k.startswith("rel:"))
    assert g["totals"]["rels"] == rels
    assert len(g["used"]) == sum(len(v) for v in raw["selected"].values())
    assert len(g["used"]) + len(g["not_used"]) == len(raw["candidates"])


def test_digest_is_the_published_file_byte_for_byte(client, paths):
    report = paths.report_path(SEASON, WEEK)
    d = client.get(f"/api/weeks/{SEASON}/{WEEK}/digest").json()
    assert d["status"] == "published"
    assert d["markdown"].encode("utf-8") == report.read_bytes()
    assert re.fullmatch(r"[0-9a-f]{12}", d["writer"]["prompt"] or "")


def test_pipeline_and_header_of_the_first_live_week(client):
    p = client.get(f"/api/weeks/{SEASON}/{WEEK}/pipeline").json()
    assert (p["state"], p["source"], p["no_run_records"], p["sittings"]) == (
        "finished",
        "weekly_run",
        True,
        3,
    )
    assert [s["status"] for s in p["steps"]] == ["ok"] * 8 + ["none"]
    h = client.get(f"/api/weeks/{SEASON}/{WEEK}").json()
    assert h["first_live_week"] is True and h["on_time"] is False  # Thursday's game had started
    assert h["tab_counts"] == {"games": 16, "players": 1860, "graph": 3}


def test_week4_results_match_week5s_report_card(client, paths):
    payload = paths.run_dir(SEASON, WEEK + 1) / "payload.json"
    if not payload.exists():
        r = client.get(f"/api/weeks/{SEASON}/{WEEK}/results").json()
        assert r["status"] == "not_graded" and r["graded_by_week"] == WEEK + 1
        pytest.skip("week 5 hasn't run yet: week 4 isn't graded")
    card = json.loads(payload.read_text("utf-8"))["report_card"]
    r = client.get(f"/api/weeks/{SEASON}/{WEEK}/results").json()
    assert r["status"] == "graded" and r["consistent"] is True
    assert r["tiles"]["picks_correct"] == card["picks_correct"]["value"]
    assert r["tiles"]["brier_model"] == card["brier"]["value"]


@dataclass(frozen=True)
class _Backtests(DataPaths):
    @property
    def runs(self) -> Path:
        return self.root / "runs" / "digest-backtests"


@pytest.mark.parametrize("week", [9, 10, 13])
def test_graded_backtest_weeks_agree_with_their_report_cards(paths, week):
    bt = _Backtests(paths.root)
    if not (bt.run_dir(2025, week + 1) / "payload.json").exists():
        pytest.skip("no simulated week to grade it")
    r = get_results(bt, 2025, week)
    card = json.loads((bt.run_dir(2025, week + 1) / "payload.json").read_text("utf-8"))[
        "report_card"
    ]
    assert r["status"] == "graded" and r["consistent"] is True
    assert r["tiles"]["picks_correct"] == card["picks_correct"]["value"]
    assert sum(1 for g in r["games"] if g["graded"]) == r["tiles"]["picks_total"]
    assert len(r["watch"]) == len(card["watch_lookback"])


def test_reading_the_real_week_changes_nothing(client, paths):
    run_dir = paths.run_dir(SEASON, WEEK)
    files = [*run_dir.iterdir(), paths.report_path(SEASON, WEEK), paths.curated / "games.parquet"]
    before = _hashes(files)
    for tab in ("", "/pipeline", "/digest", "/games", "/players", "/results", "/graph"):
        r = client.get(f"/api/weeks/{SEASON}/{WEEK}{tab}")
        assert r.status_code == 200
        assert scrub(r.text, limit=10**9) == r.text
        assert str(paths.root) not in r.text and paths.root.as_posix() not in r.text
    assert _hashes(files) == before
