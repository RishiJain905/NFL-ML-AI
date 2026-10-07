"""CR03 parity: the MLOps sections and the season pages against W&B and the real files
(`-m integration`; reads W&B, read-only; the app's W&B cache goes to a temporary folder).

Week 5 of 2026 is the first week with full run records and a pipeline run in W&B. Every chart
the app redraws from a local file is compared here with the number W&B holds for it, so a
drift between the two shows up as a failing test instead of a silent difference (CR03
"Local first")."""

from __future__ import annotations

import hashlib

import polars as pl
import pytest
from app_helpers import BASE, FakeStatus, fake_checks
from fastapi.testclient import TestClient

from nflengine.app.server import AppSettings, create_app
from nflengine.app.wandb_api import WandbReader
from nflengine.ops.summary import scrub
from nflengine.paths import DataPaths, ensure_data_root

pytestmark = pytest.mark.integration
SEASON, WEEK = 2026, 5


@pytest.fixture(scope="module")
def paths() -> DataPaths:
    return ensure_data_root(create_dirs=False)


@pytest.fixture(scope="module")
def wb():
    from nflengine.tracking import read_api

    got = read_api(timeout=20)
    if got is None:
        pytest.skip("WANDB_API_KEY isn't set")
    api, entity, project = got
    return api, f"{entity}/{project}"


@pytest.fixture(scope="module")
def client(paths, tmp_path_factory) -> TestClient:
    cache = tmp_path_factory.mktemp("wandb-cache")
    settings = AppSettings(
        services=FakeStatus(),
        data_root=lambda: paths,
        wandb=WandbReader(cache_dir=lambda: cache),
        system_checks=fake_checks(),
    )
    return TestClient(create_app(settings), base_url=BASE)


@pytest.fixture(scope="module")
def runs(client) -> dict:
    body = client.get(f"/api/weeks/{SEASON}/{WEEK}/mlops/wandb").json()
    assert body["wandb"]["available"], body["wandb"]["reason"]
    return body


def _current(body: dict, job: str) -> dict:
    return next(r for r in body["runs"] if r["job"] == job and r["current"])


def test_the_week_runs_are_the_tagged_runs(runs, wb):
    api, project = wb
    tagged = api.runs(
        project, filters={"$and": [{"tags": f"season:{SEASON}"}, {"tags": f"week:{WEEK:02d}"}]}
    )
    graded = api.runs(
        project, filters={"$and": [{"tags": f"season:{SEASON}"}, {"tags": f"week:{WEEK - 1:02d}"}]}
    )
    want = {r.id for r in tagged if "scoreboard" not in r.tags} | {
        r.id for r in graded if "scoreboard" in r.tags
    }
    assert {r["id"] for r in runs["runs"]} == want
    assert {r["job"] for r in runs["runs"] if r["current"]} == {
        "game",
        "graph",
        "scoreboard",
        "player",
        "team",
        "digest",
        "pipeline",
        "dashboard",
    }
    for r in runs["runs"]:
        assert r["url"].endswith(f"/runs/{r['id']}")


def test_game_fit_matches_the_slate_charts(runs, wb):
    api, project = wb
    run = api.run(f"{project}/{_current(runs, 'game')['id']}")
    hist = run.history(
        samples=500,
        pandas=False,
        keys=["slate/game", "slate/home_win_prob_model_only", "slate/home_win_prob_market"],
    )
    w_model = sorted(round(h["slate/home_win_prob_model_only"], 9) for h in hist)
    w_market = sorted(round(h["slate/home_win_prob_market"], 9) for h in hist)
    games = runs["cards"]["game_fit"]["games"]
    assert sorted(round(g["p_model_only"], 9) for g in games) == w_model
    assert sorted(round(g["p_market"], 9) for g in games if g["p_market"] is not None) == w_market


def test_digest_words_and_checks_match_the_digest_run(runs, wb):
    api, project = wb
    s = dict(api.run(f"{project}/{_current(runs, 'digest')['id']}").summary)
    card = runs["cards"]["digest"]
    for w in card["words"]:
        assert s[f"words/{w['section']}"] == w["words"]
    for c in card["checks"]:
        assert s[f"check_issues/{c['name']}"] == c["issues"]


def test_graph_stages_match_the_graph_run(runs, wb):
    api, project = wb
    s = dict(api.run(f"{project}/{_current(runs, 'graph')['id']}").summary)
    card = runs["cards"]["graph_build"]
    for st in card["stages"]:
        assert s[f"time/{st['stage']}_s"] == pytest.approx(st["seconds"], abs=0.01)
    assert s["count_mismatches"] == card["mismatches"]
    nodes = sum(v for k, v in s.items() if k.startswith("count/node/"))
    assert nodes == card["nodes"]


def test_pipeline_steps_match_the_pipeline_run(runs, wb):
    api, project = wb
    s = dict(api.run(f"{project}/{_current(runs, 'pipeline')['id']}").summary)
    for st in runs["cards"]["pipeline"]["steps"]:
        assert s[f"step/{st['step']}_seconds"] == pytest.approx(st["seconds"])
        assert s[f"step/{st['step']}_status"] == st["status"]


def test_scoreboard_file_matches_the_scoreboard_run(runs, wb, paths):
    api, project = wb
    s = dict(api.run(f"{project}/{_current(runs, 'scoreboard')['id']}").summary)
    sb = pl.read_parquet(paths.runs / str(SEASON) / "accuracy_scoreboard.parquet", memory_map=False)
    live = sb.filter((pl.col("week") == WEEK - 1) & (pl.col("mode") == "live"))
    keys = {k: v for k, v in s.items() if k.startswith("scoreboard/improvement_")}
    assert keys and len(keys) == live.filter(pl.col("improvement_pct").is_not_null()).height
    from nflengine.models.player_schema import GROUP_SLUG

    for r in live.filter(pl.col("improvement_pct").is_not_null()).iter_rows(named=True):
        slug = GROUP_SLUG.get(r["position_group"], r["position_group"].lower())
        assert keys[f"scoreboard/improvement_{r['target']}_{slug}"] == pytest.approx(
            r["improvement_pct"]
        )


def test_artifacts_match_wandb(client, wb):
    api, project = wb
    a = client.get(f"/api/weeks/{SEASON}/{WEEK}/mlops/artifacts").json()
    assert a["wandb"]["available"]
    for p in a["production"]:
        art = api.artifact(f"{project}/{p['name']}:production")
        assert p["version"] == art.version
        assert p["run_id"] == art.logged_by().id
    pipe = api.run(f"{project}/{a['lineage']['pipeline_run']}")
    used = {x.name for x in pipe.used_artifacts()}
    assert {f"{i['name']}:{i['version']}" for i in a["lineage"]["items"]} == used
    week4 = client.get(f"/api/weeks/{SEASON}/4/mlops/artifacts").json()["lineage"]
    assert week4["source"] == "aliases"
    for i in week4["items"]:
        assert f"{SEASON}-w04" in api.artifact(f"{project}/{i['name']}:{i['version']}").aliases


def test_live_scorecard_is_the_season_dashboard(client, wb, paths):
    api, project = wb
    import json

    info = json.loads((paths.runs / str(SEASON) / "dashboard.json").read_text("utf-8"))
    s = dict(api.run(f"{project}/{info['last_run_id']}").summary)
    sc = client.get(f"/api/season/{SEASON}/scorecard?source=live").json()
    t = sc["tiles"]
    for tile, key in (
        ("brier_model", "cum_brier_model"),
        ("brier_elo", "cum_brier_elo"),
        ("brier_market", "cum_brier_market"),
        ("pick_accuracy", "cum_pick_accuracy"),
        ("ece", "season_ece"),
        ("ece_chance", "ece_chance"),
        ("player_improvement", "player_cum_improvement_all"),
    ):
        tol = 1e-3 if tile == "player_improvement" else 1e-5  # sent rounded to 3 places
        assert t[tile] == pytest.approx(s[key], abs=tol), tile
    assert t["games_graded"] == s["games_graded"]
    assert sc["dashboard"]["current_run_id"] == info["last_run_id"]


def test_models_and_health_on_the_real_root(client):
    m = client.get("/api/models").json()
    by = {x["id"]: x for x in m["models"]}
    assert by["game"]["production"]["source"] == "wandb"
    assert by["ratings"]["headline"][0]["value"] == pytest.approx(0.1041, abs=1e-4)
    for x in m["models"]:
        for c in x["cards"]:
            assert client.get(f"/api/models/card/{c['id']}").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_reading_week5_changes_nothing(client, paths):
    run_dir = paths.run_dir(SEASON, WEEK)
    files = [p for p in run_dir.rglob("*") if p.is_file()] + [
        paths.runs / str(SEASON) / f
        for f in ("pipeline_history.parquet", "season_scorecard.parquet", "dashboard.json")
    ]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    urls = [
        *[
            f"/api/weeks/{SEASON}/{w}/mlops/{s}"
            for w in (4, 5)
            for s in ("health", "wandb", "artifacts")
        ],
        f"/api/season/{SEASON}/scorecard?source=live",
        f"/api/season/{SEASON - 1}/scorecard?source=backtest",
        "/api/teams",
        "/api/alerts",
    ]
    for u in urls:
        r = client.get(u)
        assert r.status_code == 200, u
        assert scrub(r.text, limit=10**9) == r.text, u
        assert str(paths.root) not in r.text and paths.root.as_posix() not in r.text, u
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    assert after == before
