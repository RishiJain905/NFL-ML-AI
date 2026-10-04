"""Knowledge graph against the real Neo4j and curated data (plan P05 -> Testing).

Run with `uv run pytest -m integration tests/graph/test_graph_integration.py` (Docker
Neo4j up, data drive connected). Each golden week rebuilds the whole graph as of that
week's Tuesday (about 1.5 minutes each); the module ends by rebuilding the live graph, so
Neo4j Browser shows the current week again. Nothing is written to the run folders.

Golden weeks (each asserts the expected entity appears):
- **revenge (Q1):** 2024 week 7, Eagles at Giants: Saquon Barkley (Giants 2018-2023) faces
  his old team.
- **injury ripple (Q2):** 2023 week 7: Justin Jefferson (MIN WR) went on IR after week 5;
  by Tuesday of week 7 he is on a reserve list and missed the last game.
- **backup-QB start (Q3):** 2023 week 13: Jake Browning starts for the Bengals after Joe
  Burrow's wrist injury (Burrow started 10 games).
"""

from __future__ import annotations

import datetime as dt
import time

import pytest

pytestmark = pytest.mark.integration
_LOADED: dict[str, object] = {"key": None}  # what the graph holds right now


@pytest.fixture(scope="module")
def driver():
    from nflengine.graph.client import get_driver

    try:
        drv = get_driver()
        drv.verify_connectivity()
    except Exception as e:  # no Neo4j: skip, don't fail
        pytest.skip(f"Neo4j not reachable ({type(e).__name__})")
    yield drv
    try:
        if _LOADED["key"] != "live":
            _rebuild_live(drv)
    finally:
        drv.close()


@pytest.fixture(scope="module")
def paths():
    from nflengine.paths import ensure_data_root

    return ensure_data_root()


def _key(paths, season: int, week: int, mode: str = "backtest"):
    from nflengine.digest.run import read_games, tuesday_before
    from nflengine.graph.tables import GraphKey

    run_time = (
        tuesday_before(read_games(paths), season, week)
        if mode == "backtest"
        else dt.datetime.now(dt.UTC)
    )
    return GraphKey(season, week, run_time, mode)


def _build(driver, paths, season: int, week: int, mode: str = "backtest"):
    from nflengine.graph.build import build_graph

    data, res = build_graph(driver, _key(paths, season, week, mode), paths, log=lambda m: None)
    _LOADED["key"] = "live" if mode == "live" else (season, week)
    return data, res


def _latest_live_week(paths) -> tuple[int, int] | None:
    from nflengine.settings import get_config

    season = int(get_config().seasons.current)
    weeks = sorted(
        int(p.parent.name.removeprefix("week"))
        for p in (paths.runs / str(season)).glob("week*/predictions_games.parquet")
    )
    return (season, weeks[-1]) if weeks else None


def _rebuild_live(driver) -> None:
    from nflengine.paths import ensure_data_root

    paths = ensure_data_root()
    latest = _latest_live_week(paths)
    if latest:
        _build(driver, paths, *latest, mode="live")


def _rows(data: dict, query: str) -> list[dict]:
    q = data["queries"][query]
    assert q["error"] is None, q["error"]
    return q["results"]


# ---- golden weeks -------------------------------------------------------------------------


def test_golden_revenge_saquon_2024_w7(driver, paths) -> None:
    data, res = _build(driver, paths, 2024, 7)
    assert not res.mismatches
    rows = _rows(data, "q1_revenge")
    hit = [r for r in rows if r["player"] == "Saquon Barkley"]
    assert hit, [r["player"] for r in rows]
    r = hit[0]
    assert (r["team"], r["opponent"], r["game_id"]) == ("PHI", "NYG", "2024_07_PHI_NYG")
    assert 2023 in r["seasons_with_opp"] and r["games_with_opp"] >= 10
    # the digest item says it with code-made text
    cands = [c for c in data["candidates"] if c["insight_id"].startswith("revenge:")]
    assert any("Saquon Barkley" in c["headline"] and "Giants" in c["headline"] for c in cands)


def test_golden_injury_ripple_jefferson_2023_w7(driver, paths) -> None:
    data, res = _build(driver, paths, 2023, 7)
    assert not res.mismatches
    rows = _rows(data, "q2_injury_ripple")
    hit = [r for r in rows if r["starter"] == "Justin Jefferson"]
    assert hit, [r["starter"] for r in rows]
    r = hit[0]
    assert r["team"] == "MIN" and r["position_group"] == "WR"
    assert r["out_source"] in ("reserve", "last_week")
    assert r["n_without"] >= 1 and r["backup"]  # someone stepped in when he missed games


def test_golden_backup_qb_browning_2023_w13(driver, paths) -> None:
    data, res = _build(driver, paths, 2023, 13)
    assert not res.mismatches
    rows = _rows(data, "q3_qb_change")
    hit = [r for r in rows if r["team"] == "CIN"]
    assert hit, [(r["team"], r["qb"]) for r in rows]
    r = hit[0]
    assert r["qb"] == "Jake Browning" and r["regular"] == "Joe Burrow"
    assert r["r_starts"] >= 9
    names = {w["name"] for w in r["receivers"]}
    assert "Ja'Marr Chase" in names


def test_backtest_graph_has_no_future_results(driver, paths) -> None:
    """Leakage, end to end: in a Tuesday-of-week-7 graph nothing from week 7 on has a result,
    an appearance, a target or a week-7 injury report."""
    _build(driver, paths, 2024, 7)
    q = """
    MATCH (g:Game {season: 2024}) WHERE g.week >= 7
    OPTIONAL MATCH (g)<-[a:APPEARED_IN]-()
    OPTIONAL MATCH (g)<-[i:ON_INJURY_REPORT]-()
    RETURN count(DISTINCT g) AS games, max(g.week) AS max_week,
           count(g.home_score) AS scored, count(a) AS appearances, count(i) AS reports
    """
    with driver.session() as s:
        rec = s.run(q).single()
    assert rec["max_week"] == 7  # only this week's games, no later ones
    assert rec["scored"] == 0 and rec["appearances"] == 0 and rec["reports"] == 0
    with driver.session() as s:
        late = s.run(
            "MATCH ()-[t:THREW_TO {season: 2024}]->() RETURN sum(t.targets) AS n"
        ).single()["n"]
        tw = s.run("MATCH (tw:TeamWeek {season: 2024}) RETURN max(tw.week) AS w").single()["w"]
    assert late > 0 and tw == 7


# ---- the live week: load test, repeatability, performance ---------------------------------


def test_live_build_counts_match_tables_and_repeat(driver, paths) -> None:
    latest = _latest_live_week(paths)
    if latest is None:
        pytest.skip("no live week with saved predictions")
    data1, res1 = _build(driver, paths, *latest, mode="live")
    assert not res1.mismatches, res1.mismatches  # Neo4j holds exactly what Parquet predicts
    assert res1.counts["node:Player"] > 5000 and res1.counts["rel:APPEARED_IN"] > 150_000
    data2, res2 = _build(driver, paths, *latest, mode="live")
    assert res2.counts == res1.counts  # a rebuild is repeatable
    assert res1.timings["total_s"] < 600  # minutes, not tens of minutes, on the HDD


def test_every_library_query_under_two_seconds(driver, paths) -> None:
    from nflengine.graph.queries import LIBRARY, run_query

    latest = _latest_live_week(paths)
    if latest is None:
        pytest.skip("no live week with saved predictions")
    if _LOADED["key"] != "live":
        _build(driver, paths, *latest, mode="live")
    for name in LIBRARY:
        run_query(driver, name, *latest)  # warm the page cache
        t0 = time.perf_counter()
        res = run_query(driver, name, *latest)
        took = time.perf_counter() - t0
        assert res.error is None, (name, res.error)
        assert took < 2.0, (name, took)


def test_fail_soft_when_neo4j_is_unreachable(paths, tmp_path, monkeypatch) -> None:
    """A driver that can't connect: run_graph_build(fail_soft=True) writes an
    `unavailable` results file instead of raising."""
    from neo4j import GraphDatabase

    import nflengine.graph.build as gb

    monkeypatch.setattr(gb, "results_path", lambda p, s, w, m: tmp_path / "graph_results.json")
    bad = GraphDatabase.driver("bolt://127.0.0.1:1", auth=("neo4j", "x"), connection_timeout=2)
    res = gb.run_graph_build(
        2024, 7, mode="backtest", use_wandb=False, fail_soft=True, log=lambda m: None, driver=bad
    )
    assert res.status == "unavailable" and "unreachable" in (res.error or "")
    saved = gb.load_results(tmp_path / "graph_results.json")
    assert saved and saved["status"] == "unavailable"
