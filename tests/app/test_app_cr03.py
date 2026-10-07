"""CR03: MLOps (Health · W&B runs · Artifacts), the season pages, the cached W&B reader and the
system checks (documentation/control-room/CR03-mlops-and-season.md). No network: W&B is a
fake (`cr03_fixtures.FakeApi`) or "not configured"."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app_helpers import fake_checks, make_client, make_paths
from cr03_fixtures import (
    LEAKY,
    FakeApi,
    fake_wandb_world,
    write_backtests,
    write_ratings,
    write_records,
    write_season_scorecard,
)
from week_fixtures import write_curated, write_full_week, write_scoreboard

from nflengine.app import wandb_api
from nflengine.app.readers.alerts import season_notes
from nflengine.app.wandb_api import WandbReader, reason_for
from nflengine.ops.summary import scrub

CR03_GETS = [
    "/api/weeks/2026/4/mlops/health",
    "/api/weeks/2026/4/mlops/wandb",
    "/api/weeks/2026/4/mlops/artifacts",
    "/api/weeks/2026/5/mlops/health",
    "/api/weeks/2026/5/mlops/wandb",
    "/api/weeks/2026/5/mlops/artifacts",
    "/api/season/2026/scorecard?source=live",
    "/api/season/2025/scorecard?source=backtest",
    "/api/teams",
    "/api/models",
    "/api/models/card/game-model-v0",
    "/api/alerts",
    "/api/health",
]


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


def reader(api: FakeApi | None, cache: Path | None, clock: Clock | None = None) -> WandbReader:
    return WandbReader(
        cache_dir=lambda: cache,
        client=(lambda _t: (api, "ent", "proj")) if api is not None else (lambda _t: None),
        clock=clock or Clock(),
    )


def world(tmp_path: Path, *, records: bool = True) -> object:
    paths = make_paths(tmp_path)
    write_curated(paths)
    write_full_week(paths, 2026, 4, summary=records)
    if records:
        write_records(paths, 2026, 4)
    write_scoreboard(paths, 2026, 3)
    write_season_scorecard(paths, 2026, [3])
    write_ratings(paths)
    write_backtests(paths)
    return paths


# ---- the W&B reader --------------------------------------------------------------------------


def test_reader_caches_and_refreshes(tmp_path):
    api = fake_wandb_world()
    clock = Clock()
    r = reader(api, tmp_path / "cache", clock)
    fetch = wandb_api.fetch_week_runs(2026, 4)
    data, state = r.get("runs", fetch, ttl=600)
    assert state["available"] and not state["stale"] and data
    calls = api.calls
    r.get("runs", fetch, ttl=600)
    assert api.calls == calls  # cached
    clock.t += 601
    r.get("runs", fetch, ttl=600)
    assert api.calls > calls  # expired
    calls = api.calls
    r.get("runs", fetch, ttl=600, refresh=True)
    assert api.calls > calls  # refresh skips the cache
    assert (tmp_path / "cache" / "runs.json").exists()
    # a new reader (an app restart) answers from the file
    r2 = reader(api, tmp_path / "cache", clock)
    calls = api.calls
    data2, _ = r2.get("runs", fetch, ttl=600)
    assert data2 == data and api.calls == calls


def test_reader_unavailable_stale_and_backoff(tmp_path):
    api = fake_wandb_world()
    clock = Clock()
    r = reader(api, tmp_path, clock)
    fetch = wandb_api.fetch_week_runs(2026, 4)
    data, _ = r.get("runs", fetch, ttl=60)
    api.fail = ConnectionError(f"https://api.wandb.ai/graphql?x={LEAKY}")
    clock.t += 61
    stale, state = r.get("runs", fetch, ttl=60)
    assert stale == data
    assert state["available"] and state["stale"]
    assert "couldn't be reached" in state["reason"] and "SENTINEL" not in state["reason"]
    calls = api.calls
    r.get("runs", fetch, ttl=60)
    assert api.calls == calls  # backing off: W&B isn't asked again for a minute
    # never cached at all: unavailable
    empty, state = r.get("other", fetch, ttl=60)
    assert empty is None and not state["available"] and state["reason"]
    clock.t += wandb_api.BACKOFF_S + 1
    api.fail = None
    _, state = r.get("runs", fetch, ttl=60)
    assert state["available"] and not state["stale"]


def test_reader_without_the_key():
    data, state = reader(None, None).get("x", lambda api, p: 1)
    assert data is None and state["available"] is False
    assert "WANDB_API_KEY" in state["reason"] and state["project_url"] is None


@pytest.mark.parametrize(
    ("exc", "word"),
    [
        (TimeoutError("read timed out ?apikey=abc"), "didn't answer"),
        (ConnectionError("x"), "couldn't be reached"),
        (PermissionError("x"), "refused"),
        (ValueError("secret stuff"), "Reading W&B failed"),
    ],
)
def test_reasons_never_quote_the_exception(exc, word):
    text = reason_for(exc)
    assert word in text and str(exc) not in text and type(exc).__name__ in text


def test_week_runs_take_the_scoreboard_of_the_graded_week():
    runs = wandb_api.fetch_week_runs(2026, 4)(fake_wandb_world(), "ent/proj")
    by_id = {r["id"]: r for r in runs}
    assert "sb000003" in by_id and "sb000004" not in by_id  # scoreboard-w03 belongs to week 4
    assert by_id["gm000001"]["current"] and not by_id["gm000000"]["current"]  # a re-run
    assert by_id["dg123456"]["job"] == "digest" and by_id["pp123456"]["job"] == "pipeline"
    assert all(LEAKY not in t for r in runs for t in r["tags"])  # scrubbed before caching


def test_artifacts_reuse_known_loggers_and_handle_missing_collections():
    api = fake_wandb_world()
    data = wandb_api.fetch_artifacts()(api, "ent/proj")
    names = {c["name"]: c for c in data["collections"]}
    assert (
        names["game-model"]["total"] == 2 and names["game-model"]["versions"][0]["version"] == "v1"
    )
    assert names["injury-update"]["total"] == 0 and names["injury-update"]["first_note"]
    assert names["team-model"]["versions"] == []
    first = sum(len(a.calls) for a in api._artifacts["game-model"])
    wandb_api.fetch_artifacts(data)(api, "ent/proj")
    assert sum(len(a.calls) for a in api._artifacts["game-model"]) == first  # reused


# ---- MLOps ---------------------------------------------------------------------------------


def test_health_from_full_records(tmp_path):
    world(tmp_path)
    h = make_client(tmp_path).get("/api/weeks/2026/4/mlops/health").json()
    assert h["records"] == "full" and h["notice"] is None
    t = h["tiles"]
    assert t["run"] == {
        "status": "ok",
        "steps_ok": 8,
        "steps_total": 8,
        "degraded": 0,
        "failed_step": None,
    }
    assert t["data"] == {"datasets": 3, "failures": 1, "snapshot_date": "2026-10-04"}
    assert t["quality"]["total"] == 3 and t["quality"]["blocking"] == 2
    assert h["quality"]["match"] == "this_run" and len(h["quality"]["checks"]) == 3
    assert h["ingest"]["manifest"] == "raw/_runs/ingest-1.json" and len(h["ingest"]["rows"]) == 3
    assert h["long_pole"]["step"] == "digest"
    wr = h["what_ran"]
    assert wr["game_model"] == "game-model-v0:2026-w04" and wr["prompt_id"] == "248ed5fb0b33"
    assert wr["command"] == "nfl weekly run --auto --expect-week 4" and wr["via"] == "control-room"
    assert wr["promoted"] == ["game-model", "player-model"]  # --auto promotes; no team file
    assert wr["writer"]["calls"] == 2 and wr["writer"]["providers"] == ["DeepInfra"]
    assert len(h["freshness"]) == 2 and sum(r["stale"] for r in h["freshness"]) == 1
    assert {d["name"] for d in h["drift"]} >= {"game_vs_elo", "player_vs_baseline"}
    vs = {r["measure"]: r for r in h["vs_last"]}
    assert vs["LLM cost"]["this_week"] == pytest.approx(0.0256)
    assert vs["Digest passed first time"]["this_week"] == 0  # regenerated
    assert "SENTINEL-records" not in json.dumps(h)


def test_health_without_run_records_and_without_a_run(tmp_path):
    world(tmp_path, records=False)
    c = make_client(tmp_path)
    h = c.get("/api/weeks/2026/4/mlops/health").json()
    assert h["records"] == "partial" and "before run records" in h["notice"]
    assert h["drift"] == [] and h["freshness"] == []
    assert h["tiles"]["data"]["datasets"] is None  # no manifest file written without records
    none = c.get("/api/weeks/2026/7/mlops/health").json()
    assert none["records"] == "none" and none["steps"] == [] and none["vs_last"] == []


def test_quality_file_of_a_later_curate_is_not_shown_as_this_runs(tmp_path):
    paths = world(tmp_path)
    q = paths.curated / "_quality" / "latest.json"
    data = json.loads(q.read_text())
    data["run_at"] = "2026-10-11T03:00:00"
    q.write_text(json.dumps(data))
    h = make_client(tmp_path).get("/api/weeks/2026/4/mlops/health").json()
    assert h["quality"]["match"] == "later" and h["quality"]["checks"] == []
    assert h["tiles"]["quality"]["total"] == 3  # the run summary's own counts


def test_wandb_section_with_wandb(tmp_path):
    world(tmp_path)
    c = make_client(tmp_path, wandb=reader(fake_wandb_world(), tmp_path / "wb"))
    w = c.get("/api/weeks/2026/4/mlops/wandb").json()
    assert w["wandb"]["available"]
    jobs = {r["job"] for r in w["runs"] if r["current"]}
    assert {"game", "graph", "scoreboard", "digest", "pipeline"} <= jobs
    cards = w["cards"]
    assert cards["game_fit"]["run_id"] == "gm000001" and cards["game_fit"]["games"]
    assert cards["pipeline"]["run_id"] == "pp123456" and len(cards["pipeline"]["steps"]) == 8
    assert (
        cards["player_scoreboard"]["scored_week"] == 3
        and cards["player_scoreboard"]["mode"] == "live"
    )
    groups = {g["group"] for g in cards["player_scoreboard"]["groups"]}
    assert groups == {"LB/S", "WR/TE"}  # the backtest QB row stays out of a live week
    assert cards["digest"]["words"][0]["budget"] is not None
    assert w["dashboard"]["url"].endswith("Dash--abc")


def test_wandb_section_without_wandb_still_has_local_data(tmp_path):
    world(tmp_path)
    w = make_client(tmp_path).get("/api/weeks/2026/4/mlops/wandb").json()
    assert w["wandb"]["available"] is False and "WANDB_API_KEY" in w["wandb"]["reason"]
    assert w["runs"] == []
    assert {x["job"] for x in w["local_links"]} == {"game", "graph", "digest"}
    assert w["cards"]["game_fit"]["url"].endswith("gm123456")  # the step detail's link
    assert w["cards"]["graph_build"]["stages"]


def test_artifacts_with_and_without_wandb(tmp_path):
    world(tmp_path)
    c = make_client(tmp_path, wandb=reader(fake_wandb_world(), tmp_path / "wb"))
    a = c.get("/api/weeks/2026/4/mlops/artifacts").json()
    prod = {p["name"]: p for p in a["production"]}
    assert prod["game-model"]["version"] == "v1" and prod["team-model"]["version"] is None
    assert a["lineage"]["source"] == "pipeline_run" and a["lineage"]["pipeline_run"] == "pp123456"
    assert [i["name"] for i in a["lineage"]["items"]] == [
        "game-model",
        "graph-results",
        "player-model",
        "digest",
    ]
    assert a["total_versions"] == 5 and a["collections_count"] == 4
    off = make_client(tmp_path).get("/api/weeks/2026/4/mlops/artifacts").json()
    assert off["wandb"]["available"] is False and off["collections"] == []
    assert off["local_models"]["game"] == ["game-model-v0:2026-w04"]


def test_lineage_falls_back_to_week_aliases(tmp_path):
    world(tmp_path)
    api = fake_wandb_world()
    api._runs = [r for r in api._runs if r.job_type != "pipeline"]
    c = make_client(tmp_path, wandb=reader(api, tmp_path / "wb"))
    lin = c.get("/api/weeks/2026/4/mlops/artifacts").json()["lineage"]
    assert lin["source"] == "aliases" and "2026-w04" in lin["note"]


# ---- season pages ----------------------------------------------------------------------------


def test_scorecard_live_and_backtest(tmp_path):
    world(tmp_path)
    c = make_client(tmp_path)
    live = c.get("/api/season/2026/scorecard?source=live").json()
    assert live["source"] == "live" and live["through_week"] == 3
    assert live["tiles"]["brier_model"] == pytest.approx(0.2246)
    assert live["tiles"]["weeks_published"] == 1 and live["tiles"]["checks_total"] == 1
    assert live["pipeline"][0]["week"] == 4 and live["dashboard"]["url"]
    assert any("after kickoff" in n for n in live["notes"])
    assert live["player_groups"]  # week 3's scoreboard rows
    bt = c.get("/api/season/2025/scorecard?source=backtest").json()
    assert bt["label"] == "2025 backtest (example)" and [w["week"] for w in bt["weeks"]] == [1, 2]
    assert bt["tiles"]["games_graded"] == 8 and bt["calibration"]
    assert {g["group"] for g in bt["player_groups"]} == {"QB", "WR/TE"}


def test_scorecard_of_an_empty_season(tmp_path):
    make_paths(tmp_path)
    c = make_client(tmp_path)
    for source in ("live", "backtest"):
        s = c.get(f"/api/season/2026/scorecard?source={source}").json()
        assert s["weeks"] == [] and s["tiles"]["games_graded"] == 0 and s["calibration"] == []


def test_teams(tmp_path):
    world(tmp_path)
    t = make_client(tmp_path).get("/api/teams").json()
    assert t["week"] == 4 and t["weeks"] == [1, 2, 3, 4]
    assert [x["team"] for x in t["teams"]] == ["KC", "BUF", "IND", "WAS"]
    ind = next(x for x in t["teams"] if x["team"] == "IND")
    assert ind["elo_change"] == 30.0 and len(ind["elo_by_week"]) == 4
    assert t["risers"][0]["team"] == "IND" and t["fallers"][0]["team"] == "WAS"
    assert ind["this_week"]["week"] == 4 and ind["next_week"]["week"] == 5
    assert make_client(tmp_path).get("/api/teams?week=2").json()["week"] == 2


def test_models_and_cards(tmp_path):
    world(tmp_path)
    c = make_client(tmp_path, wandb=reader(fake_wandb_world(), tmp_path / "wb"))
    m = c.get("/api/models").json()
    by = {x["id"]: x for x in m["models"]}
    assert (
        by["game"]["production"]["version"] == "v1"
        and by["game"]["production"]["source"] == "wandb"
    )
    assert by["game"]["headline"][0]["value"] == pytest.approx(0.2199)
    assert by["ratings"]["headline"][0]["value"] == pytest.approx(0.1041)
    assert {b["metric"] for b in by["player"]["bars"]} == {"mae", "brier"}
    assert any(s.get("mono") for s in by["writer"]["settings"])
    card = c.get("/api/models/card/game-model-v0").json()
    assert card["markdown"].startswith("#") and card["title"]
    assert c.get("/api/models/card/nope").status_code == 404
    assert c.get("/api/models/card/..%2F..%2Fsecret").status_code in (404, 422)
    off = make_client(tmp_path).get("/api/models").json()
    assert off["wandb"]["available"] is False
    game = next(x for x in off["models"] if x["id"] == "game")["production"]
    assert game["source"] == "local" and game["label"] == "game-model-v0:2026-w04"


def test_alerts(tmp_path):
    paths = world(tmp_path)
    alert = {
        "level": "warn",
        "title": "calibration",
        "text": "ECE high",
        "source": "drift:calibration",
    }
    write_records(paths, 2026, 4, alerts=[alert])
    a = make_client(tmp_path).get("/api/alerts").json()
    assert a["season"] == 2026 and a["current_week"] == 5
    assert a["alerts"][0]["title"] == "calibration" and a["alerts"][0]["week"] == 4
    sig = {s["name"]: s for s in a["signals"]}
    # the fixture's season: graded week 3 (scorecard), verdict week 4 (history), scored
    # week 3 (scoreboard): the evaluator's own rules (Sol review)
    assert sig["game_vs_elo"]["first_week"] == 3 + 1 + 5
    assert sig["checks"]["first_week"] == 4 + 3  # the window counts the run's own verdict
    assert sig["player_vs_baseline"]["first_week"] == 3 + 1 + 5
    assert a["example"]["source"] == "drift:calibration" and a["example_note"]


def test_season_notes_parse_progress():
    text = (
        "## Season log (2026)\n\n- week 04: published\n  - looked at the calibration alert\n"
        "  - decided: no change (D75)\n- week 05: published\n\n## Session log\n- week 06: nope\n"
    )
    assert season_notes(2026, text) == {
        4: ["looked at the calibration alert", "decided: no change (D75)"]
    }


def test_health_page_names_only(tmp_path):
    world(tmp_path)
    checks = fake_checks({"Neo4j": ("fail", f"server down {LEAKY}"), "Docker": ("ok", "up")})
    c = make_client(
        tmp_path,
        system_checks=checks,
        keys_set=lambda names: {n: n != "KAGGLE_KEY" for n in names},
    )
    h = c.get("/api/health").json()
    svc = {s["name"]: s for s in h["services"]}
    assert svc["Neo4j"]["status"] == "fail" and LEAKY not in svc["Neo4j"]["detail"]
    assert svc["Weights & Biases"]["status"] == "checking"  # not in this fake's list
    assert svc["Run lock"]["detail"] == "free" and "GB free" in svc["Data drive"]["detail"]
    v = {x["name"]: x for x in h["variables"]}
    assert v["WANDB_API_KEY"] == {"name": "WANDB_API_KEY", "required": True, "set": True}
    assert v["KAGGLE_KEY"]["set"] is False and v["KAGGLE_KEY"]["required"] is False
    assert h["recent_runs"][0]["command"] == "nfl weekly run --auto --expect-week 4"
    assert str(tmp_path) not in json.dumps(h)


# ---- safety ----------------------------------------------------------------------------------


def test_cr03_endpoints_scrubbed_with_a_leaky_wandb(tmp_path, monkeypatch):
    from nflengine import settings as S

    sentinels = {
        "WANDB_API_KEY": "wandb_v1_SENTINELwandbSENTINELwandb",
        "NEO4J_PASSWORD": "SENTINEL-neo4j-pw-123",
        "OPENROUTER_API_KEY": "sk-or-v1-SENTINELSENTINELSENTINEL",
    }
    for k, v in sentinels.items():
        monkeypatch.setenv(k, v)
    S.get_env.cache_clear()
    try:
        world(tmp_path)
        api = fake_wandb_world()
        api._runs[0].tags.append("sk-or-v1-SENTINELSENTINELSENTINEL")
        c = make_client(tmp_path, wandb=reader(api, tmp_path / "wb"))
        for path in CR03_GETS:
            r = c.get(path)
            assert r.status_code == 200, path
            for v in sentinels.values():
                assert v not in r.text, path
            assert scrub(r.text, limit=10**9) == r.text, f"{path} has text that looks like a secret"
            assert str(tmp_path) not in r.text and "SENTINEL-records" not in r.text, path
    finally:
        S.get_env.cache_clear()


def test_cr03_reads_write_only_the_wandb_cache(tmp_path):
    from test_app_week import _hashes

    world(tmp_path)
    cache = tmp_path / "cache" / "control-room" / "wandb"
    c = make_client(tmp_path, wandb=reader(fake_wandb_world(), cache))
    before = {k: v for k, v in _hashes(tmp_path).items() if "control-room" not in k}
    for path in CR03_GETS:
        assert c.get(path).status_code == 200, path
    after = {k: v for k, v in _hashes(tmp_path).items() if "control-room" not in k}
    assert after == before
    assert sorted(p.name for p in cache.glob("*.json")) == [
        "artifacts.json",
        "ratings-eval.json",
        "runs-2026-w04.json",
        "runs-2026-w05.json",
        "used-pp123456.json",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "/api/season/2026/scorecard?source=all",
        "/api/season/1990/scorecard",
        "/api/teams?week=40",
        "/api/alerts?season=abc",
        "/api/weeks/2026/4/mlops/nope",
    ],
)
def test_bad_parameters(tmp_path, url):
    world(tmp_path)
    r = make_client(tmp_path).get(url)
    assert r.status_code in (404, 422), url
    assert "abc" not in r.text and "all" not in r.json()["error"]["message"]
