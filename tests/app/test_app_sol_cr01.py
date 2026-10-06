"""Regression tests for the CR01 Sol review (Codex gpt-6.1-sol; D102): the data root's path
in any field, a GET that never creates folders, heuristic watch lists, a stale run summary,
no hit / miss for a late prediction, and the Super Bowl's Results."""

from __future__ import annotations

import json

import polars as pl
from app_helpers import BASE, TUESDAY, FakeStatus, make_client, make_paths, raw_sched, write_week
from fastapi.testclient import TestClient
from test_app_week import all_week_urls, get
from week_fixtures import full_week_paths, write_curated, write_full_week

from nflengine.app.server import AppSettings, create_app


def test_the_root_path_never_leaks_from_any_field(tmp_path):
    """Finding 1: fields cleaned without `paths` (names, driver phrases, writer metadata)."""
    paths = full_week_paths(tmp_path)
    root = str(paths.root)
    run_dir = paths.run_dir(2026, 4)
    payload = json.loads((run_dir / "payload.json").read_text())
    payload["players_to_watch"][0]["drivers"] = [f"a phrase naming {root}\\runs\\x.json"]
    payload["tough_spots"][0]["player"] = f"{root}/someone"
    (run_dir / "payload.json").write_text(json.dumps(payload))
    raw = json.loads((run_dir / "raw_llm_output.json").read_text())
    raw["model"] = f"model at {root}\\models"
    raw["calls"][0]["provider"] = root
    (run_dir / "raw_llm_output.json").write_text(json.dumps(raw))
    c = make_client(tmp_path)
    for url in all_week_urls():
        body = c.get(url).text
        for form in (root, root.replace("\\", "\\\\"), paths.root.as_posix()):
            assert form not in body, url
    d = get(c, "/api/weeks/2026/4/digest")
    assert d["writer"]["model"] == "model at models"


def test_the_app_never_creates_folders_in_the_data_root(tmp_path, monkeypatch):
    """Finding 2: the default resolver (`ensure_data_root`) used to create the standard
    folders on every request. An empty root stays empty after every GET."""
    from nflengine import settings as S

    root = tmp_path / "empty-root"
    root.mkdir()
    monkeypatch.setenv("NFL_DATA_ROOT", str(root))
    S.get_env.cache_clear()
    try:
        app = create_app(
            AppSettings(
                services=FakeStatus(),
                schedules=lambda _p: raw_sched(),
                now=lambda: TUESDAY,
                current_season=lambda: 2026,
            )
        )
        client = TestClient(app, base_url=BASE)
        for url in ["/api/meta", "/api/weeks", *all_week_urls()]:
            assert client.get(url).status_code == 200, url
        assert list(root.iterdir()) == []
    finally:
        S.get_env.cache_clear()


def _heuristic_watch(paths, season: int, week: int) -> None:
    """P04's fallback watch list (the player model degraded): no quantiles, no `kind`."""
    pl.DataFrame(
        {
            "season": [season],
            "week": [week],
            "player_id": ["00-9"],
            "player": ["Usage Riser"],
            "team": ["LV"],
            "opponent": ["KC"],
            "home": [True],
            "position": ["WR"],
            "group": ["WR/TE"],
            "target": ["receiving yards"],
            "baseline": [41.0],
            "score": [1.7],
            "rank": [1],
            "confidence": ["low"],
            "source": ["heuristic"],
        }
    ).write_parquet(paths.run_dir(season, week) / "watchlist.parquet")


def test_a_heuristic_watch_list_does_not_break_players_or_results(tmp_path):
    """Finding 3."""
    paths = full_week_paths(tmp_path)
    _heuristic_watch(paths, 2026, 4)
    run5 = write_full_week(paths, 2026, 5, report_card_for=4)
    payload = json.loads((run5 / "payload.json").read_text())
    payload["report_card"]["watch_lookback"] = [
        {
            "player": "Usage Riser",
            "player_id": "00-9",
            "team": "LV",
            "position": "WR",
            "target": "receiving yards",
            "text": "actual 55 receiving yards, above his baseline of 41",
            "played": True,
            "hit": True,
            "inside": None,
            "side": None,
            "actual": {"value": 55.0, "display": "55 receiving yards"},
            "status": "",
        }
    ]
    (run5 / "payload.json").write_text(json.dumps(payload))
    c = make_client(tmp_path)
    w = get(c, "/api/weeks/2026/4/players")["watch"]
    assert [(x["player"], x["source"], x["projection"], x["p10"]) for x in w] == [
        ("Usage Riser", "heuristic", None, None)
    ]
    assert w[0]["baseline"] == 41.0 and w[0]["target_label"] == "receiving yards"
    r = get(c, "/api/weeks/2026/4/results")["watch"]
    assert (r[0]["actual"], r[0]["inside"], r[0]["hit"], r[0]["p10"]) == (55.0, None, True, None)


def test_a_stale_summary_does_not_hide_a_later_run(tmp_path):
    """Finding 4: a "not ready" sitting wrote its summary; a later retry got to the graph
    step and died before writing its own. The week shows the interrupted step, not the
    plan, and the old summary's seconds don't replace the new step times."""
    paths = make_paths(tmp_path)
    run_dir = write_week(
        paths,
        2026,
        5,
        {"ingest": "ok", "ready": "ok", "curate": "ok", "ratings": "ok", "game": "ok"},
        summary={
            "status": "not_ready",
            "finished": "2026-10-06T06:00:05",
            "steps": {"ingest": {"status": "ok", "seconds": 999.0, "this_run": True}},
        },
    )
    state = json.loads((run_dir / "weekly_run.json").read_text())
    for name in state["steps"]:  # the retry: from 10:00 local, after the summary
        state["steps"][name].update(started="2026-10-06T10:00:00", finished="2026-10-06T10:00:30")
    state["steps"]["graph"] = {"status": "running", "started": "2026-10-06T10:00:30"}
    (run_dir / "weekly_run.json").write_text(json.dumps(state))
    c = make_client(tmp_path)
    p = get(c, "/api/weeks/2026/5/pipeline")
    steps = {s["step"]: s for s in p["steps"]}
    assert p["state"] == "failed" and p["failed_step"] == "graph"
    assert steps["graph"]["detail"].startswith("interrupted")
    assert steps["ingest"]["seconds"] == 30.0  # the step's own time, not the stale 999
    assert steps["records"]["status"] == "none" and p["no_run_records"] is False
    entry = next(w for w in get(c, "/api/weeks")["weeks"] if w["week"] == 5)
    assert entry["status"] == "failed"  # not "Waiting" from the stale not-ready summary


def test_a_prediction_made_after_kickoff_gets_no_hit_or_miss(tmp_path):
    """Finding 5: week 4's Thursday game was first predicted on Saturday."""
    full_week_paths(tmp_path)
    g = get(make_client(tmp_path), "/api/weeks/2026/4/games")
    late = next(x for x in g["games"] if x["game_id"] == "2026_04_PIT_CLE")
    assert late["predicted_after_kickoff"] is True
    assert late["final"] == {"home": 27, "away": 24} and late["hit"] is None


def test_the_super_bowl_is_graded_after_the_season(tmp_path):
    """Finding 7: no week 23 to link to."""
    r = get(make_client(tmp_path), "/api/weeks/2026/22/results")
    assert r["status"] == "not_graded"
    assert (r["graded_by_week"], r["graded_on"]) == (None, None)
    assert "season review" in r["reason"]


def test_week_fixture_helper_writes_curated_too(tmp_path):
    paths = full_week_paths(tmp_path)
    assert (paths.curated / "games.parquet").exists()
    write_curated(paths)  # idempotent
