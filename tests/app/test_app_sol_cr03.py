"""CR03's Sol review (D106): each finding pinned by a test (tests/app/test_app_cr03.py has the
phase's own). No network: W&B is `cr03_fixtures.FakeApi`."""

from __future__ import annotations

import json

import polars as pl
from app_helpers import make_client, make_paths
from cr03_fixtures import FakeRun, fake_wandb_world
from test_app_cr03 import Clock, reader, world

from nflengine.app import wandb_api
from nflengine.app.readers.alerts import first_weeks
from nflengine.app.readers.common import use_data_root


def test_1_wandb_strings_lose_the_data_root_before_the_cache(tmp_path):
    paths = make_paths(tmp_path)
    use_data_root(paths.root)
    api = fake_wandb_world()
    api._runs[0].tags.append(f"note {paths.root}\\runs\\2026")
    cache = tmp_path / "cache" / "control-room" / "wandb"
    reader(api, cache).get("runs", wandb_api.fetch_week_runs(2026, 4))
    text = (cache / "runs.json").read_text(encoding="utf-8")
    assert str(paths.root) not in text and paths.root.as_posix() not in text
    assert "note runs/2026" in text


def test_3_a_malformed_cache_entry_is_a_miss(tmp_path):
    api = fake_wandb_world()
    for bad in ({"at": None, "data": []}, {"at": "x", "data": []}, {"at": True, "data": []}, []):
        (tmp_path / "runs.json").write_text(json.dumps(bad), encoding="utf-8")
        data, state = reader(api, tmp_path).get("runs", wandb_api.fetch_week_runs(2026, 4))
        assert state["available"] and not state["stale"] and data  # fetched again, no crash
    (tmp_path / "runs.json").write_text(json.dumps({"at": None, "data": []}), encoding="utf-8")
    assert reader(api, tmp_path).peek("runs") is None


def test_4_a_failed_secondary_read_marks_the_answer_stale(tmp_path):
    world(tmp_path)
    api = fake_wandb_world()
    api.fail_on = {"run"}  # the pipeline run's lineage can't be read
    c = make_client(tmp_path, wandb=reader(api, tmp_path / "wb"))
    a = c.get("/api/weeks/2026/4/mlops/artifacts").json()
    assert a["collections"] and a["wandb"]["available"]
    assert a["wandb"]["stale"] and a["wandb"]["reason"].startswith("The lineage:")
    assert a["lineage"]["source"] == "aliases"  # the fallback, now flagged
    m = c.get("/api/models").json()["wandb"]
    assert m["stale"] and "ratings" in m["reason"]


def test_5_artifacts_refresh_rereads_the_weeks_runs(tmp_path):
    world(tmp_path)
    api = fake_wandb_world()
    c = make_client(tmp_path, wandb=reader(api, tmp_path / "wb"))
    first = c.get("/api/weeks/2026/4/mlops/artifacts").json()["lineage"]
    assert first["pipeline_run"] == "pp123456"
    api._runs.append(
        FakeRun(
            "pp999999",
            "pipeline-2026-w04",
            "weekly-pipeline",
            "pipeline",
            ["season:2026", "week:04"],
            "2026-10-05T09:00:00Z",
            used=api._runs[-2].used,
        )
    )
    again = c.get("/api/weeks/2026/4/mlops/artifacts").json()["lineage"]
    assert again["pipeline_run"] == "pp123456"  # cached
    fresh = c.get("/api/weeks/2026/4/mlops/artifacts?refresh=1").json()["lineage"]
    assert fresh["pipeline_run"] == "pp999999"


def test_6_a_failed_refresh_stays_stale_until_a_good_fetch(tmp_path):
    api = fake_wandb_world()
    clock = Clock()
    r = reader(api, tmp_path, clock)
    fetch = wandb_api.fetch_week_runs(2026, 4)
    r.get("runs", fetch, ttl=600)
    api.fail = ConnectionError("down")
    _, state = r.get("runs", fetch, ttl=600, refresh=True)
    assert state["stale"]
    _, state = r.get("runs", fetch, ttl=600)  # an ordinary hit, inside the TTL
    assert state["stale"] and "couldn't be reached" in state["reason"]
    api.fail = None
    clock.t += wandb_api.BACKOFF_S + 1
    r.get("runs", fetch, ttl=600, refresh=True)
    _, state = r.get("runs", fetch, ttl=600)
    assert not state["stale"] and state["reason"] is None


def test_7_the_back_off_runs_from_the_failure(tmp_path):
    clock = Clock()
    calls = []

    def slow_failure(api, project):
        calls.append(clock.t)
        clock.t += 90  # a long fetch (the artifact list) that fails at the end
        raise TimeoutError("late")

    r = reader(fake_wandb_world(), tmp_path, clock)
    r.get("artifacts", slow_failure)
    clock.t += 10  # 100 s after the start, 10 s after the failure
    r.get("artifacts", slow_failure)
    assert len(calls) == 1  # still backing off


def test_8_first_weeks_follow_the_evaluator(tmp_path):
    paths = make_paths(tmp_path)
    (paths.runs / "2026").mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"season": [2026, 2026], "week": [4, 5], "games": [15, 14]}).write_parquet(
        paths.runs / "2026" / "season_scorecard.parquet"
    )
    pl.DataFrame(
        {
            "season": [2026] * 8,
            "week": [1, 2, 3, 4, 1, 2, 3, 4],
            "target": ["rec_yds"] * 4 + ["int"] * 4,
            "position_group": ["WR/TE"] * 4 + ["CB/S"] * 4,
            "n_scored": [10] * 8,
            "mae_model": [1.0] * 4 + [None] * 4,
            "mae_baseline": [1.1] * 4 + [None] * 4,
            "brier_model": [None] * 7 + [0.1],
            "brier_baseline": [None] * 7 + [0.11],
            "mode": ["backtest"] * 8,
        },
        schema_overrides={"brier_model": pl.Float64, "brier_baseline": pl.Float64},
    ).write_parquet(paths.runs / "2026" / "accuracy_scoreboard.parquet")
    pl.DataFrame(
        {
            "season": [2026, 2026, 2026, 2026],
            "week": [4, 5, 6, 7],
            "kind": ["main"] * 4,
            "started": ["a", "b", "c", "d"],
            "status": ["ok"] * 4,
            "checks_passed": [True] * 4,
        }
    ).write_parquet(paths.runs / "2026" / "pipeline_history.parquet")
    sched = pl.DataFrame(
        {
            "season": [2026] * 60,
            "week": [w for w in range(6, 12) for _ in range(10)],
            "game_id": [f"g{w}-{i}" for w in range(6, 12) for i in range(10)],
        }
    )
    f = first_weeks(paths, 2026, 4, 8, sched)
    assert f["game_vs_elo"][1] == 5 + 1 + 4  # 2 graded weeks of 6
    assert f["calibration"][1] == 9 + 1  # 29 + 10 a week from week 6 → 69 after week 9
    assert f["player_vs_baseline"][1] == 4 + 1 + 2  # MAE rows from week 1
    assert f["player_prob_vs_baseline"][1] == 4 + 1 + 5  # Brier rows only from week 4
    assert f["checks"][1] == 7  # the fourth verdict is week 7's own
    assert f["data_freshness"][1] == 4


def test_9_health_matches_the_closest_launch_of_the_same_kind(tmp_path):
    from cr03_fixtures import write_records

    paths = world(tmp_path)
    write_records(paths, 2026, 4, command="nfl weekly run --auto --expect-week 4")
    ev = paths.run_dir(2026, 4) / "events"
    (ev / "20261004T071900Z-cd34.jsonl").write_text(
        json.dumps(
            {
                "seq": 1,
                "t": "2026-10-04T03:19:40-04:00",
                "run_id": "20261004T071900Z-cd34",
                "type": "run_start",
                "kind": "resume",
                "command": "nfl weekly run --season 2026 --week 4 --from-step game --promote",
                "season": 2026,
                "week": 4,
                "launched_by": "rishi",
                "via": "control-room",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    runs = make_client(tmp_path).get("/api/health").json()["recent_runs"]
    assert runs[0]["command"] == "nfl weekly run --auto --expect-week 4"
