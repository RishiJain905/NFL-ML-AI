"""CR01: the week archive's readers and endpoints, on synthetic run folders (no real data).

The week-4 shape (no run records, three sittings), a published week with records, a failed
week, the current week before its run, a "not ready" stop, a graded week and every way text
could leak (raw LLM text, credential-shaped details, the data root's own path)."""

from __future__ import annotations

import hashlib
import json

import polars as pl
import pytest
from app_helpers import make_client, make_paths, write_week
from week_fixtures import (
    LEAKY_DETAIL,
    REPORT_MD,
    SECRET_RAW,
    write_curated,
    write_full_week,
    write_scoreboard,
)

from nflengine.ops.summary import scrub

WEEK_TABS = ("pipeline", "digest", "games", "players", "results", "graph")


def full(tmp_path, **kw):
    paths = make_paths(tmp_path)
    write_curated(paths)
    write_full_week(paths, **kw)
    return paths


def get(client, url):
    r = client.get(url)
    assert r.status_code == 200, (url, r.text)
    return r.json()


def no_leaks(body: str, root) -> None:
    for s in (
        SECRET_RAW,
        "SENTINEL-leaky-123",
        "token=abc",
        str(root),
        str(root).replace("\\", "\\\\"),
        root.as_posix(),
    ):
        assert s not in body, s
    assert scrub(body, limit=10**9) == body


# ---- header ---------------------------------------------------------------------------------


def test_week_header_for_a_published_week_without_records(tmp_path):
    full(tmp_path, summary=False)
    h = get(make_client(tmp_path), "/api/weeks/2026/4")
    assert h["title"] == "Week 4" and h["is_current"] is False
    assert h["entry"]["status"] == "published"
    assert h["plan"]["week"] == 4 and h["plan"]["games"] > 0
    # published on Sunday, after Thursday's first kickoff: late, worked out from the slate
    assert (h["on_time"], h["on_time_source"]) == (False, "deadline")
    assert h["first_live_week"] is True
    assert h["wandb_url"] == "https://wandb.ai/ent/proj/runs/dg123456"
    assert h["tab_counts"] == {"games": 3, "players": 3, "graph": 1}
    assert h["last_published_week"] == 4


def test_week_header_trusts_the_run_summary(tmp_path):
    full(tmp_path)
    h = get(make_client(tmp_path), "/api/weeks/2026/4")
    assert (h["on_time"], h["on_time_source"]) == (True, "summary")


def test_week_header_for_the_current_week(tmp_path):
    full(tmp_path)
    h = get(make_client(tmp_path), "/api/weeks/2026/5")
    assert h["is_current"] is True and h["published_at"] is None
    assert h["tab_counts"] == {"games": None, "players": None, "graph": None}
    assert h["last_published_week"] == 4 and h["first_live_week"] is False


def test_team_info(tmp_path):
    paths = make_paths(tmp_path)
    write_curated(paths)
    teams = get(make_client(tmp_path), "/api/team-info")["teams"]
    assert teams["WAS"] == {
        "nick": "Commanders",
        "name": "Washington Commanders",
        "color": "#5A1414",
        "conf": "NFC",
        "div": "NFC East",
    }
    assert teams["LV"]["color"] == "#888888"  # not a colour: never sent into a style


# ---- pipeline -------------------------------------------------------------------------------


def test_pipeline_of_a_week_without_run_records(tmp_path):
    full(tmp_path, summary=False)
    c = make_client(tmp_path)
    r = c.get("/api/weeks/2026/4/pipeline")
    p = r.json()
    assert (p["state"], p["source"], p["no_run_records"]) == ("finished", "weekly_run", True)
    steps = {s["step"]: s for s in p["steps"]}
    assert list(steps) == [
        "ingest",
        "ready",
        "curate",
        "ratings",
        "game",
        "graph",
        "player",
        "digest",
        "records",
    ]
    assert steps["records"]["status"] == "none"
    assert steps["digest"]["seconds"] == 2329.0 and steps["graph"]["seconds"] == 147.0
    assert p["step_seconds"] == 2610.0 and p["sittings"] == 3
    assert steps["ingest"]["detail"] == "30 datasets; manifest raw/_runs/ingest-1.json"
    assert steps["game"]["wandb_url"] == "https://wandb.ai/ent/proj/runs/gm123456"
    assert "W&B gm123456" in steps["game"]["detail"] and "wandb.ai" not in steps["game"]["detail"]
    assert "***" in steps["player"]["detail"]  # the credential-shaped detail is masked
    acc = [ln["text"] for ln in p["log"] if ln["cls"] == "acc"]
    assert acc == [
        "$ nfl weekly run --season 2026 --week 4",
        "$ nfl weekly run --season 2026 --week 4 --from-step player   (resumed)",
        "$ nfl weekly run --season 2026 --week 4 --from-step digest   (resumed)",
    ]
    assert [ln["text"].split(":")[0] for ln in p["log"][1:6]] == [
        "ingest",
        "ready",
        "curate",
        "ratings",
        "game",
    ]  # same-second starts keep the pipeline's order
    assert p["log"][-1]["text"].startswith("published reports/2026/week04-digest.md")
    t = p["tiles"]
    assert (t["checks_passed"], t["checks_total"], t["first_time"]) == (1, 1, False)
    assert (t["llm_calls"], t["llm_provider"], t["llm_cost"]) == (2, "DeepInfra", 0.0256)
    assert t["digest_share"] == pytest.approx(2329 / 2610, abs=1e-4)
    no_leaks(r.text, tmp_path)


def test_pipeline_with_run_records(tmp_path):
    full(tmp_path)
    p = get(make_client(tmp_path), "/api/weeks/2026/4/pipeline")
    steps = {s["step"]: s for s in p["steps"]}
    assert (p["source"], p["no_run_records"]) == ("run_summary", False)
    assert steps["records"]["status"] == "ok" and steps["records"]["seconds"] == 30.0
    assert steps["digest"]["seconds"] == 10.0  # the summary's seconds win
    assert steps["digest"]["this_run"] is True and steps["ingest"]["this_run"] is False


def test_pipeline_of_a_failed_week(tmp_path):
    paths = make_paths(tmp_path)
    write_week(paths, 2026, 3, {"ingest": "ok", "ready": "ok", "curate": "failed"})
    p = get(make_client(tmp_path), "/api/weeks/2026/3/pipeline")
    steps = {s["step"]: s["status"] for s in p["steps"]}
    assert p["state"] == "failed" and p["failed_step"] == "curate"
    assert steps["curate"] == "failed" and steps["ratings"] == "skipped"
    assert steps["digest"] == "skipped" and steps["records"] == "none"
    assert (
        p["log"][-1]
        == {
            "ts": "03:18:17",
            "text": "failed: 2026 week 03 at the curate step",
            "cls": "err",
        }
        or p["log"][-1]["cls"] == "err"
    )


def test_a_step_left_running_without_the_lock_is_interrupted(tmp_path):
    paths = make_paths(tmp_path)
    write_week(paths, 2026, 3, {"ingest": "ok", "ready": "running"})
    p = get(make_client(tmp_path), "/api/weeks/2026/3/pipeline")
    ready = next(s for s in p["steps"] if s["step"] == "ready")
    assert ready["status"] == "failed" and ready["detail"].startswith("interrupted")
    assert p["state"] == "failed"


def test_the_current_week_shows_the_plan_with_last_weeks_times(tmp_path):
    full(tmp_path)
    p = get(make_client(tmp_path), "/api/weeks/2026/5/pipeline")
    assert (p["state"], p["source"], p["log_source"]) == ("plan", "plan", "calendar")
    assert {s["status"] for s in p["steps"]} == {"pending"}
    assert p["expected_from"] == {"season": 2026, "week": 4}
    exp = {s["step"]: s["expected_seconds"] for s in p["steps"]}
    assert exp["digest"] == 10.0 and exp["records"] == 30.0
    assert p["log"][0]["text"].startswith("the calendar's plan")
    assert any("2026 week 5" in ln["text"] for ln in p["log"])


def test_the_plan_uses_defaults_before_any_published_week(tmp_path):
    p = get(make_client(tmp_path), "/api/weeks/2026/5/pipeline")
    assert p["expected_from"] is None
    assert {s["step"]: s["expected_seconds"] for s in p["steps"]}["digest"] == 540.0


def test_a_not_ready_stop_still_shows_the_plan(tmp_path):
    paths = make_paths(tmp_path)
    write_week(
        paths,
        2026,
        5,
        {"ingest": "ok", "ready": "failed"},
        details={"ready": "2026 week 4: NOT READY (15/16 games final)"},
    )
    p = get(make_client(tmp_path), "/api/weeks/2026/5/pipeline")
    assert p["state"] == "plan"
    assert p["log"][-1]["cls"] == "warn" and "NOT READY" in p["log"][-1]["text"]


def test_a_past_week_with_no_run(tmp_path):
    p = get(make_client(tmp_path), "/api/weeks/2026/2/pipeline")
    assert (p["state"], p["log"]) == ("none", [])


# ---- digest ---------------------------------------------------------------------------------


def test_digest_of_a_published_week(tmp_path):
    full(tmp_path)
    r = make_client(tmp_path).get("/api/weeks/2026/4/digest")
    d = r.json()
    assert d["status"] == "published"
    assert d["markdown"] == REPORT_MD.format(season=2026, week=4)  # byte for byte
    assert d["checks"]["regenerated"] is True
    assert [c["name"] for c in d["checks"]["final"]["checks"]] == ["number_provenance"]
    assert d["checks"]["first_attempt"]["checks"][1]["issues"][0].startswith("upload failed")
    w = d["writer"]
    assert (w["provider"], w["model"], w["prompt"]) == (
        "openrouter",
        "z-ai/glm-5.3-flash",
        "248ed5fb0b33",
    )
    assert w["calls"][0]["usage"] == {"prompt": 15691, "completion": 60899, "reasoning": 59896}
    assert set(w["calls"][0]) == {
        "model",
        "provider",
        "latency_s",
        "usage",
        "cost",
        "finish_reason",
        "regeneration",
        "parsed",
    }
    assert w["total_cost"] == 0.0256 and w["total_seconds"] == 2958.3
    assert d["wandb_url"] == "https://wandb.ai/ent/proj/runs/dg123456"
    files = {f["label"]: f for f in d["files"]}
    assert files["digest"] == {
        "label": "digest",
        "path": "reports/2026/week04-digest.md",
        "exists": True,
    }
    assert files["Saturday"]["exists"] is False and d["addendum"] is None
    assert "secret_field" not in r.text and "attempts" not in r.text
    no_leaks(r.text, tmp_path)


def test_digest_with_a_saturday_addendum(tmp_path):
    paths = full(tmp_path)
    run_dir = paths.run_dir(2026, 4)
    (run_dir / "injury_update.json").write_text(
        json.dumps({"material": True, "finished": "2026-10-10T15:02:00+00:00"})
    )
    paths.report_path(2026, 4, kind="injury-update").write_bytes(b"## Saturday update\n")
    a = get(make_client(tmp_path), "/api/weeks/2026/4/digest")["addendum"]
    assert a == {
        "markdown": "## Saturday update\n",
        "material": True,
        "at": "2026-10-10T15:02:00+00:00",
    }


def test_an_unpublished_draft_and_no_digest(tmp_path):
    paths = make_paths(tmp_path)
    run_dir = write_week(paths, 2026, 3, {"ingest": "ok", "digest": "failed"})
    (run_dir / "digest.md").write_bytes(b"# draft\n")
    c = make_client(tmp_path)
    d = get(c, "/api/weeks/2026/3/digest")
    assert (d["status"], d["markdown"]) == ("unpublished", "# draft\n")
    d = get(c, "/api/weeks/2026/5/digest")
    assert (d["status"], d["markdown"], d["checks"], d["writer"]) == ("none", None, None, None)


# ---- games ----------------------------------------------------------------------------------


def test_games_of_a_predicted_week(tmp_path):
    full(tmp_path)
    r = make_client(tmp_path).get("/api/weeks/2026/4/games")
    g = r.json()
    assert g["status"] == "predicted" and g["source"]["kind"] == "predictions"
    games = {x["game_id"]: x for x in g["games"]}
    assert [x["game_id"] for x in g["games"]][0] == "2026_04_PIT_CLE"  # kickoff order
    was = games["2026_04_IND_WAS"]
    assert (was["p_home"], was["p_home_model_only"], was["p_market"]) == (0.36, 0.30, 0.40)
    assert was["p_elo"] is None  # NaN in the file -> null
    assert was["final"] == {"home": 20, "away": 27} and was["hit"] is True
    assert was["neutral"] is True and was["stadium"] is None
    assert was["qb_change_home"] == "Marcus Mariota starts in place of Jayden Daniels"
    assert was["qb_change_away"] is None
    assert games["2026_04_PIT_CLE"]["predicted_after_kickoff"] is True
    lv = games["2026_04_KC_LV"]
    assert (lv["final"], lv["hit"], lv["p_market"], lv["stadium"]) == (
        None,
        None,
        None,
        "Allegiant Stadium",
    )
    assert g["gaps"] == [
        {"game_id": "2026_04_IND_WAS", "gap": -0.1},
        {"game_id": "2026_04_PIT_CLE", "gap": 0.1},
    ]
    assert (g["finals"], g["lines_used"]) == (2, 3)
    assert g["model"]["version"] == "game-model-v0:2026-w04"
    assert "NaN" not in r.text


def test_games_of_the_current_week_show_the_slate(tmp_path):
    g = get(make_client(tmp_path), "/api/weeks/2026/5/games")
    assert g["status"] == "slate" and g["source"] == {"kind": "schedule", "snapshot_date": None}
    assert len(g["games"]) > 10
    first = g["games"][0]
    assert first["kickoff"].endswith("+00:00") and first["p_home"] is None
    assert first["kickoff"] == min(x["kickoff"] for x in g["games"])


# ---- players --------------------------------------------------------------------------------


def test_players_main_and_all_stats(tmp_path):
    full(tmp_path)
    c = make_client(tmp_path)
    p = get(c, "/api/weeks/2026/4/players")
    assert p["status"] == "projected" and p["stats"] == "main"
    assert p["counts"] == {
        "total": 3,
        "main": 2,
        "teams": 0,
        "stats": 3,
        "by_group": {"LB/S": 1, "WR/TE": 2},
    }
    assert p["groups"] == ["WR/TE", "LB/S"]
    rows = {r["target"]: r for r in p["rows"]}
    assert rows["tackles"]["projection"] == 5.29  # a count: the mean (digest.players.center)
    assert rows["rec_yds"]["projection"] == 61.0  # an amount: P50
    assert rows["tackles"]["injury"] == "Questionable" and rows["rec_yds"]["injury"] is None
    w = p["watch"]
    assert [x["player"] for x in w] == ["Jake Hansen", "Brock Bowers"]
    assert w[0]["display"] == {
        "projection": "5.3 tackles",
        "range": "2–8 tackles",
        "vs_baseline": "2.1 tackles above his baseline",
    }
    assert w[0]["driver"] == "his snap share in his last game"
    assert w[1]["driver"] is None  # the digest showed no driver: none stood out
    assert p["tough_spots"][0]["display"]["vs_baseline"] == "9 receiving yards below his baseline"
    a = get(c, "/api/weeks/2026/4/players?stats=all")
    td = next(r for r in a["rows"] if r["target"] == "td")
    assert (td["projection"], td["chance"], td["baseline"], td["p10"]) == (0.31, 0.31, 0.25, None)


def test_players_team_rows(tmp_path):
    paths = full(tmp_path)
    pl.DataFrame(
        {
            "team": ["LV"],
            "opponent": ["KC"],
            "home": [True],
            "target": ["pass_yds"],
            "target_label": ["team passing yards"],
            "kind": ["amount"],
            "unit": ["yards"],
            "is_main": [True],
            "p10": [140.0],
            "p50": [230.0],
            "p90": [320.0],
            "mean": [231.0],
            "baseline": [220.0],
            "outperformance": [10.0],
        }
    ).write_parquet(paths.run_dir(2026, 4) / "predictions_teams.parquet")
    p = get(make_client(tmp_path), "/api/weeks/2026/4/players")
    team = next(r for r in p["rows"] if r["group"] == "TEAM")
    assert (team["player"], team["player_id"], team["projection"]) == ("Raiders", "LV", 230.0)
    assert p["groups"][-1] == "TEAM" and p["counts"]["teams"] == 1


def test_players_before_the_run(tmp_path):
    p = get(make_client(tmp_path), "/api/weeks/2026/5/players")
    assert (p["status"], p["rows"], p["watch"]) == ("none", [], [])


# ---- results --------------------------------------------------------------------------------


def test_results_wait_for_next_weeks_run(tmp_path):
    full(tmp_path)
    r = get(make_client(tmp_path), "/api/weeks/2026/4/results")
    assert r["status"] == "not_graded" and r["graded_by_week"] == 5
    assert r["graded_on"] == "2026-10-06"  # the Tuesday after Monday night's game


def test_results_graded_by_next_weeks_report_card(tmp_path):
    paths = full(tmp_path)
    write_full_week(paths, 2026, 5, report_card_for=4)
    write_scoreboard(paths, 2026, 4)
    r = get(make_client(tmp_path), "/api/weeks/2026/4/results")
    assert r["status"] == "graded" and r["graded_by_week"] == 5
    t = r["tiles"]
    assert (t["picks_correct"], t["picks_total"], t["brier_model"]) == (1, 1, 0.1296)
    assert (t["watch_hits"], t["watch_total"], t["not_graded"]) == (1, 1, 1)
    assert t["brier_market"] == 0.16  # recomputed: only IND@WAS is graded
    assert r["consistent"] is True
    games = {g["game_id"]: g for g in r["games"]}
    was = games["2026_04_IND_WAS"]
    assert (was["pick"], was["pick_prob"], was["hit"], was["graded"]) == ("IND", 0.64, True, True)
    assert (was["brier_model"], was["brier_elo"], was["brier_market"]) == (0.1296, None, 0.16)
    assert was["margin_error"] == pytest.approx(-2.3)
    kept = games["2026_04_PIT_CLE"]
    assert kept["graded"] is False and kept["note"] == "predicted after kickoff: not graded"
    assert games["2026_04_KC_LV"]["final"] is None
    assert [(g["group"], g["improvement_pct"], g["n"]) for g in r["players"]] == [
        ("WR/TE", 9.1, 200),
        ("LB/S", 9.0, 100),
    ]  # the backtest QB row isn't live
    hansen, bowers = r["watch"]
    assert (hansen["actual"], hansen["inside"], hansen["p10"], hansen["p90"]) == (
        7.0,
        True,
        2.0,
        8.0,
    )
    assert hansen["projection"] == 5.29
    assert (bowers["played"], bowers["status"], bowers["actual"]) == (False, "did not play", None)


def test_results_disagreeing_with_the_report_card(tmp_path):
    paths = full(tmp_path)
    run5 = write_full_week(paths, 2026, 5, report_card_for=4)
    payload = json.loads((run5 / "payload.json").read_text())
    payload["report_card"]["brier"]["value"] = 0.2
    (run5 / "payload.json").write_text(json.dumps(payload))
    r = get(make_client(tmp_path), "/api/weeks/2026/4/results")
    assert r["consistent"] is False and r["tiles"]["brier_model"] == 0.2  # the published one


def test_results_when_next_week_graded_nothing(tmp_path):
    paths = full(tmp_path)
    run5 = write_full_week(paths, 2026, 5)
    payload = json.loads((run5 / "payload.json").read_text())
    payload["report_card"] = {"status": "no_saved_predictions", "scored_week": 4, "note": "nothing"}
    (run5 / "payload.json").write_text(json.dumps(payload))
    r = get(make_client(tmp_path), "/api/weeks/2026/4/results")
    assert (r["status"], r["reason"]) == ("not_graded", "nothing")


# ---- graph ----------------------------------------------------------------------------------


def test_graph_of_a_week(tmp_path):
    full(tmp_path)
    r = make_client(tmp_path).get("/api/weeks/2026/4/graph")
    g = r.json()
    assert g["totals"] == {"nodes": 132, "rels": 520, "mismatches": 0}
    assert g["nodes_by_label"] == [
        {"label": "Player", "count": 100},
        {"label": "Team", "count": 32},
    ]
    assert [s["stage"] for s in g["stages"]] == ["wipe", "load", "queries"]
    assert g["total_seconds"] == 143.6
    assert g["queries"][0] == {
        "name": "q2_injury_ripple",
        "rows": 26,
        "seconds": 1.2,
        "error": None,
    }
    assert [u["id"] for u in g["used"]] == ["injury_ripple:00-9"]
    assert g["used"][0]["brief"] == "Brief with a path runs/x.json"
    assert [(n["id"], n["reason"]) for n in g["not_used"]] == [
        ("revenge:00-7:CLE", "game already started"),
        ("unit_mismatch:x", "not picked"),
    ]
    assert g["candidates"] == 3
    assert g["skipped"] == {"started": 1, "novelty": 0, "duplicate_player": 0}
    assert g["gds"]["jobs"] == [{"name": "pass_network", "status": "ok", "seconds": 22.3}]
    assert g["browser_url"] == "http://localhost:7474/browser/"
    no_leaks(r.text, tmp_path)


def test_graph_missing(tmp_path):
    g = get(make_client(tmp_path), "/api/weeks/2026/5/graph")
    assert g["status"] == "none" and g["used"] == []


# ---- safety: nothing written, nothing leaked, validated input --------------------------------


def _hashes(root):
    """Every file's hash and every folder (a GET must not create one either)."""
    return {
        p.relative_to(root).as_posix(): (
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
        )
        for p in root.rglob("*")
    }


def all_week_urls():
    out = ["/api/team-info"]
    for w in (3, 4, 5):
        out.append(f"/api/weeks/2026/{w}")
        out += [f"/api/weeks/2026/{w}/{t}" for t in WEEK_TABS]
        out.append(f"/api/weeks/2026/{w}/players?stats=all")
    return out


def test_no_endpoint_writes_anything(tmp_path):
    """Exit criterion: the readers leave every file under the data root as it was."""
    paths = full(tmp_path)
    write_full_week(paths, 2026, 5, report_card_for=4)
    write_scoreboard(paths, 2026, 4)
    c = make_client(tmp_path)
    before = _hashes(tmp_path)
    for url in all_week_urls():
        assert c.get(url).status_code == 200, url
    assert _hashes(tmp_path) == before


def test_no_secret_raw_text_or_root_path_in_any_week_response(tmp_path, monkeypatch):
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
        paths = full(tmp_path)
        write_full_week(paths, 2026, 5, report_card_for=4)
        c = make_client(tmp_path)
        for url in all_week_urls():
            body = c.get(url).text
            for v in sentinels.values():
                assert v not in body, url
            no_leaks(body, tmp_path)
    finally:
        S.get_env.cache_clear()


def test_leaky_text_is_masked_not_dropped(tmp_path):
    full(tmp_path)
    body = make_client(tmp_path).get("/api/weeks/2026/4/pipeline").text
    assert "api_key=***" in body and LEAKY_DETAIL not in body


@pytest.mark.parametrize(
    "url",
    [
        "/api/weeks/2026/0/games",
        "/api/weeks/2026/23/games",
        "/api/weeks/1990/4",
        "/api/weeks/abc/4/pipeline",
        "/api/weeks/2026/4/players?stats=everything",
    ],
)
def test_bad_season_week_or_option(tmp_path, url):
    r = make_client(tmp_path).get(url)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bad_request"
    assert "everything" not in r.text and "abc" not in r.text  # the input isn't echoed


def test_unknown_week_tab_is_a_json_404(tmp_path):
    r = make_client(tmp_path).get("/api/weeks/2026/4/nope")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")
