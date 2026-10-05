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

P08 (advanced graph and GDS):
- **passing-network hub out (Q10, GDS PageRank):** 2023 week 7: Jefferson is the center of
  the Vikings' network; without him T.J. Hockenson leads it. **Usage comparison (Q10, GDS
  KNN):** the same week, rookie Puka Nacua's usage is closest to Stefon Diggs's 2020.
- **former teammates (Q6):** 2024 week 14, Falcons at Vikings: Kirk Cousins faces Justin
  Jefferson (600+ targets for the Vikings, 2020-2023). The same build checks a scrambling QB
  (Q7, Jalen Hurts vs the Panthers), FTN's week lag, the coaching tree from a test seed
  (Q5: O'Connell and Morris both worked under McVay), the GDS outputs, and the referee query
  (Q9) with that game's crew added as a Saturday run would have it (Alex Kemp).
- GDS errors on the real server are fail-soft.
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


@pytest.fixture(autouse=True)
def _no_real_coaching_seed(tmp_path, monkeypatch):
    """Golden weeks don't depend on `config/coaching_seed.csv` (rebuilt from Wikipedia every
    preseason, D86); the P08 test sets its own seed, and the live rebuild at the end of the
    module (after this is undone) loads the real one like a weekly run."""
    from nflengine.graph import tables_extra

    monkeypatch.setattr(tables_extra, "seed_path", lambda: tmp_path / "no-seed.csv")


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


def test_golden_network_hub_and_usage_comp_2023_w7(driver, paths) -> None:
    """GDS stories on the week-7 graph of the Jefferson golden test (rebuilt if needed)."""
    from nflengine.graph.queries import run_query

    if _LOADED["key"] != (2023, 7):
        _build(driver, paths, 2023, 7)
    hub = run_query(driver, "q10_network_hub", 2023, 7)
    assert hub.error is None and hub.seconds < 2.0
    hit = [r for r in hub.rows if r["hub"] == "Justin Jefferson"]
    assert hit, [r["hub"] for r in hub.rows]
    r = hit[0]
    assert (r["team"], r["out_source"], r["next"]) == ("MIN", "reserve", "T.J. Hockenson")
    assert r["next_share_without"] > r["next_share"] and r["next_rank"] == 2
    comp = run_query(driver, "q10_usage_comp", 2023, 7)
    assert comp.error is None and comp.seconds < 2.0
    puka = [r for r in comp.rows if r["player"] == "Puka Nacua"]
    assert puka and puka[0]["other"] == "Stefon Diggs" and puka[0]["other_season"] == 2020
    assert puka[0]["young"] is True and puka[0]["other_yds_rank"] == 1


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


SEED_ROWS = """coach,team,season,role,head_coach
Kevin O'Connell,LA,2020,OC,
Kevin O'Connell,LA,2021,OC,
Raheem Morris,LA,2021,DC,
Raheem Morris,LA,2022,DC,
Raheem Morris,LA,2023,DC,
Test Coordinator,ATL,2024,OC,
Test Coordinator,MIN,2023,QB coach,
"""


def test_golden_p08_2024_w14(driver, paths, tmp_path, monkeypatch) -> None:
    """Q6 Cousins vs Jefferson, Q7, FTN's lag, the Q5 tree from a test seed, GDS outputs, Q9."""
    from nflengine.graph import tables_extra
    from nflengine.graph.queries import run_query

    seed = tmp_path / "coaching_seed.csv"
    seed.write_text(SEED_ROWS, encoding="utf-8")
    monkeypatch.setattr(tables_extra, "seed_path", lambda: seed)
    data, res = _build(driver, paths, 2024, 14)
    assert not res.mismatches and data["gds"]["status"] == "ok", data["gds"]
    game = "2024_14_ATL_MIN"

    # Q6: Kirk Cousins (ATL) faces Justin Jefferson (MIN)
    hit = [
        r
        for r in _rows(data, "q6_former_teammates")
        if (r["qb"], r["receiver"]) == ("Kirk Cousins", "Justin Jefferson")
    ]
    assert hit, [(r["qb"], r["receiver"]) for r in _rows(data, "q6_former_teammates")]
    r = hit[0]
    assert (r["game_id"], r["team"], r["opponent"]) == (game, "ATL", "MIN")
    assert r["targets"] >= 500 and r["teams_together"] == ["MIN"]
    assert set(r["seasons"]) == {2020, 2021, 2022, 2023}
    assert any(
        c["insight_id"] == f"former_teammates:{r['qb_id']}:{r['receiver_id']}"
        for c in data["candidates"]
    )

    # Q7: a scrambling QB, with samples and league numbers
    q7 = _rows(data, "q7_style_matchup")
    assert any(x["qb"] == "Jalen Hurts" and x["style"] == "scramble" for x in q7), q7
    for x in q7 + _rows(data, "q7_play_action"):
        assert x["n_style"] >= 4 and x["n_other"] >= 8 and abs(x["gap"]) >= 0.05
        assert x["lg_n_style"] > 100 and 0 <= x["strength"] <= 1

    with driver.session() as s:
        # FTN is a week late on a Tuesday: week 13 has no play-action rate, week 12 has
        pa = s.run(
            "MATCH (:Team)-[p:PLAYED_IN]->(g:Game {season: 2024}) WHERE g.week IN [12, 13] "
            "RETURN g.week AS week, count(p.pa_rate) AS n ORDER BY week"
        ).data()
        # GDS: a team's weighted degrees (undirected: each target counts at both ends) add up
        # to twice its visible THREW_TO targets
        deg = s.run(
            "MATCH (:Player)-[c:PASS_CENTRALITY {season: 2024}]->(t:Team {team_id: 'MIN'}) "
            "RETURN sum(c.degree) AS d"
        ).single()["d"]
        thr = s.run(
            "MATCH ()-[x:THREW_TO {season: 2024}]->() WHERE x.team_id = 'MIN' "
            "RETURN sum(x.targets) AS n"
        ).single()["n"]
        hub = s.run(
            "MATCH (p:Player)-[c:PASS_CENTRALITY {season: 2024, rank: 1}]->"
            "(:Team {team_id: 'MIN'}) RETURN p.name AS name"
        ).single()["name"]
        sim = s.run(
            "MATCH (:Player)-[x:SIMILAR_TO {season: 2024}]->(:Player) "
            "RETURN count(x) AS n, min(x.other_season) AS lo, max(x.other_season) AS hi"
        ).single()
    by_week = {x["week"]: x["n"] for x in pa}
    assert by_week[12] > 0 and by_week[13] == 0, pa
    assert deg == pytest.approx(2 * thr) and hub == "Justin Jefferson"
    assert sim["n"] > 300 and sim["lo"] >= 2018 and sim["hi"] <= 2023  # past seasons only

    # Q5 coaching tree from the test seed: both head coaches worked under Sean McVay; the
    # test coordinator (ATL) worked under O'Connell (MIN)
    tree = [x for x in _rows(data, "q5_coaching_tree") if x["game_id"] == game]
    kinds = {x["kind"]: x for x in tree}
    assert set(kinds) == {"head_coach_tree", "coordinator_vs_boss"}, tree
    ht = kinds["head_coach_tree"]
    assert {ht["coach"], ht["other"]} == {"Kevin O'Connell", "Raheem Morris"}
    # two shortest paths exist: through Sean McVay (both worked under him) and through the
    # test coordinator (under O'Connell in 2023, under Morris now); shortestPath picks one
    mids = {st["to"] for st in ht["steps"]} | {st["from"] for st in ht["steps"]}
    assert len(ht["steps"]) == 2 and mids & {"Sean McVay", "Test Coordinator"}, ht["steps"]
    cb = kinds["coordinator_vs_boss"]
    assert (cb["coach"], cb["other"], cb["role"]) == ("Test Coordinator", "Kevin O'Connell", "OC")

    # Q9: a backtest never has the week's crew; add it as a run that had it would, then the
    # query finds the referee and his history (gaps forced to 0 to always fire)
    assert run_query(driver, "q9_officiating", 2024, 14).rows == []
    with driver.session() as s:
        s.run(
            "MATCH (o:Official {official_id: '689'}), (g:Game {game_id: $g}) "
            "CREATE (o)-[:OFFICIATED {role: 'Referee'}]->(g)",
            g=game,
        ).consume()
    q9 = run_query(driver, "q9_officiating", 2024, 14, min_pen_gap=0.0, min_split_gap=0.0)
    assert q9.error is None and q9.seconds < 2.0
    ref = [x for x in q9.rows if x["game_id"] == game]
    assert ref and ref[0]["referee"] == "Alex Kemp" and ref[0]["ref_games"] >= 10
    assert ref[0]["strength"] <= 0.5 and ref[0]["first_season"] >= 2022


def test_gds_errors_on_the_real_server_are_fail_soft(driver, paths, monkeypatch) -> None:
    """A GDS call the server rejects: the job is `failed` with a status code only, the other
    job still runs, no projection is left behind, and the library still answers."""
    from nflengine.graph import gds
    from nflengine.graph.queries import run_query

    if _LOADED["key"] is None:
        _build(driver, paths, 2024, 14)

    def broken(drv, key):
        with drv.session() as s:
            s.run("CALL gds.pageRank.stream('nfl-does-not-exist', {})").consume()
        return {}

    monkeypatch.setattr(gds, "JOBS", {"broken": broken, "similarity": gds.player_similarity})
    key = _key(paths, 2024, 14)
    res = gds.run_gds_jobs(driver, key, log=lambda m: None)
    assert res.status == "partial"
    assert res.jobs["broken"]["status"] == "failed"
    assert res.jobs["broken"]["error"].startswith("ClientError: Neo.ClientError")
    assert res.jobs["similarity"]["status"] == "ok"
    with driver.session() as s:
        left = s.run("CALL gds.graph.list() YIELD graphName RETURN count(*) AS n").single()["n"]
    assert left == 0
    assert run_query(driver, "q10_usage_comp", 2024, 14).error is None


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
        # P08: no week-7 crew in a Tuesday graph; usage profiles only from before week 7
        crews = s.run(
            "MATCH (:Official)-[o:OFFICIATED]->(g:Game {season: 2024}) WHERE g.week >= 7 "
            "RETURN count(o) AS n"
        ).single()["n"]
        games_2024 = s.run(
            "MATCH (u:UsageProfile {season: 2024}) RETURN max(u.games) AS n"
        ).single()["n"]
        tw = s.run("MATCH (tw:TeamWeek {season: 2024}) RETURN max(tw.week) AS w").single()["w"]
    assert late > 0 and tw == 7
    assert crews == 0 and games_2024 <= 6


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


# ---- the non-QB stories (Q5 coach reunion, Q11 unit mismatch, Q12 special teams) ----------


def test_extra_queries_on_the_live_week(driver, paths) -> None:
    """The three post-P05 queries run without error on the live graph and return sensible
    rows; special-teams EPA is visible only before week W and nets to 0 within a game."""
    from nflengine.graph.insights import CONVERTERS
    from nflengine.graph.queries import LIBRARY, run_query

    latest = _latest_live_week(paths)
    if latest is None:
        pytest.skip("no live week with saved predictions")
    if _LOADED["key"] != "live":
        _build(driver, paths, *latest, mode="live")
    season, week = latest
    for name in ("q5_coach_reunion", "q11_unit_mismatch", "q12_special_teams"):
        res = run_query(driver, name, season, week)
        assert res.error is None, (name, res.error)
        assert res.seconds < 2.0, (name, res.seconds)
        for r in res.rows:
            assert r["game_id"].startswith(f"{season}_{week:02d}_"), r["game_id"]
            assert 0.0 <= r["strength"] <= 1.0
            assert CONVERTERS[name](r, season) is not None
    tier, n = LIBRARY["q11_unit_mismatch"]["tier"], 32
    for r in run_query(driver, "q11_unit_mismatch", season, week).rows:
        good, bad = (
            (r["off_rank"], r["def_rank"])
            if r["edge"] == "offense"
            else (
                r["def_rank"],
                r["off_rank"],
            )
        )
        assert good <= tier and bad > n - tier, r
        # the offense's expected EPA is off + def (def = EPA allowed), signed by the edge
        assert (r["expected_epa"] > 0) == (r["edge"] == "offense"), r
    p12 = LIBRARY["q12_special_teams"]
    for r in run_query(driver, "q12_special_teams", season, week).rows:
        assert min(r["team_games"], r["opponent_games"]) >= p12["min_games"]
        assert r["shrunk_gap"] >= p12["min_gap"] and abs(r["gap"]) >= r["shrunk_gap"]
    with driver.session() as s:
        rec = s.run(
            """
            MATCH (g:Game {season: $season})<-[p:PLAYED_IN]-(:Team)
            WITH g, count(p.st_epa) AS n, sum(p.st_epa) AS total
            RETURN sum(CASE WHEN g.week >= $week AND n > 0 THEN 1 ELSE 0 END) AS future,
                   sum(CASE WHEN g.week < $week AND g.completed AND n = 2 THEN 1 ELSE 0 END)
                     AS both_sides,
                   max(abs(total)) AS worst_net
            """,
            season=season,
            week=week,
        ).single()
        pw = s.run(
            "MATCH (tw:TeamWeek {season: $season, week: $week}) RETURN count(tw.prior_weight) AS n",
            season=season,
            week=week,
        ).single()["n"]
    assert rec["future"] == 0  # no special-teams EPA for week W
    assert rec["both_sides"] > 0 and rec["worst_net"] < 1e-6
    assert pw == 32
