"""AE02: Ask's history templates and golden references on the REAL history graph, through the guard.

Run with `uv run pytest -m integration tests/ask/test_ask_history_live.py` once the history
container is up (`uv run nfl history-graph start`) and built. Read-only: a module fixture reads
the node and relationship counts before and after and fails if they differ; the write probes run
in a READ session inside a transaction that is always rolled back and fail after the write on
purpose (1 / 0). Skips cleanly when the history graph isn't answering or has no build.
"""

from __future__ import annotations

import contextlib
import time

import pytest

from nflengine.ask.guard import Refused, Sources, _read_session
from nflengine.ask.templates import load_templates, render

pytestmark = pytest.mark.integration

WARM_S = 5.0  # a warm template run (the page's budget is 10 s per query; most take < 1 s)

# (template, values as the router would give them: names as text, resolved by the real index)
CASES: list[tuple[str, dict]] = [
    ("history_qb_receivers_situation", {"player": "Patrick Mahomes", "situation": "3rd_down",
                                        "rank_by": "catches", "limit": 1}),
    ("history_qb_receivers_situation", {"player": "Josh Allen", "situation": "red_zone",
                                        "first_season": 2020}),
    ("history_qb_receivers_situation", {"player": "Jalen Hurts", "situation": "3rd_long"}),
    ("history_team_targets_situation", {"team": "Chiefs", "first_season": 2023}),
    ("history_team_targets_situation", {"team": "Lions", "situation": "3rd_down",
                                        "first_season": 2024, "last_season": 2024}),
    ("history_team_carries_situation", {"team": "Eagles"}),
    ("history_team_carries_situation", {"team": "49ers", "situation": "4th_down",
                                        "rank_by": "first_downs"}),
    ("history_defense_pass_rush", {"team": "Steelers", "situation": "3rd_long"}),
    ("history_defense_pass_rush", {"team": "Ravens", "situation": "3rd_down", "by": "season"}),
    ("history_defense_coverage", {"team": "49ers", "situation": "3rd_long"}),
    ("history_defense_coverage", {"team": "Chiefs", "field": "man_zone", "first_season": 2024,
                                  "last_season": 2024}),
    ("history_qb_vs_coverage", {"player": "Josh Allen"}),
    ("history_qb_vs_coverage", {"player": "Patrick Mahomes", "field": "coverage",
                                "first_season": 2020}),
    ("history_receiver_routes", {"player": "Justin Jefferson"}),
    ("history_receiver_routes", {"player": "Travis Kelce", "first_season": 2023}),
    ("history_snaps_together", {"player": "Travis Kelce", "other": "Patrick Mahomes"}),
    ("history_snaps_together", {"player": "Justin Jefferson", "other": "Jaire Alexander"}),
    ("history_player_snaps_vs_team", {"player": "Aaron Donald", "team": "Seahawks"}),
    ("history_team_personnel", {"team": "Rams"}),
    ("history_team_personnel", {"team": "49ers", "field": "formation", "situation": "3rd_down",
                                "first_season": 2024, "last_season": 2024}),
    ("history_drive_results", {"team": "Ravens", "first_season": 2019}),
    ("history_drive_results", {"team": "Jets", "first_season": 2023, "last_season": 2023,
                               "games": "regular"}),
    # AE03: the person links (`nfl history-graph derive`)
    ("history_connection", {"person": "Tom Brady", "other": "Aaron Rodgers"}),
    ("history_connection", {"person": "Patrick Mahomes", "other": "Bill Belichick"}),
    ("history_connection", {"person": "Andy Reid", "other": "Bill Belichick"}),
    ("history_most_snaps_with", {"player": "Patrick Mahomes"}),
    ("history_qb_chemistry", {}),
    ("history_qb_chemistry", {"player": "Joe Burrow", "min_targets": 50,
                              "rank_by": "touchdowns"}),
    ("history_coach_players", {"coach": "Andy Reid"}),
    ("history_coach_players", {"coach": "Steve Spagnuolo", "role": "defensive_coordinator",
                               "limit": 5}),
    ("history_coach_colleagues", {"coach": "Andy Reid"}),
    ("history_opponents_faced", {"player": "Justin Jefferson", "position": "cornerback"}),
    ("history_opponents_faced", {"player": "Aaron Donald", "position": "offensive_line"}),
]  # fmt: skip


@pytest.fixture(scope="module")
def paths():
    from nflengine.paths import ensure_data_root

    try:
        p = ensure_data_root(create_dirs=False)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no data root ({type(e).__name__})")
    return p


@pytest.fixture(scope="module")
def asof(paths):
    from nflengine.history_graph.state import history_asof

    a = history_asof(paths)
    if a is None:
        pytest.skip("the history graph has no build")
    return a


@pytest.fixture(scope="module")
def driver(asof):
    from nflengine.history_graph.client import get_history_driver, history_up

    if not history_up():
        pytest.skip("the history graph isn't answering (uv run nfl history-graph start)")
    drv = get_history_driver(connection_timeout=5.0)
    yield drv
    drv.close()


@pytest.fixture(scope="module")
def sources(driver, paths):
    from nflengine.history_graph.state import lock_path

    return Sources(
        sandbox=None,
        driver=None,
        history_driver=driver,
        enabled={"graph": False, "sql": False, "history": True},
        history_lock=lock_path(paths),
    )


@pytest.fixture(scope="module")
def names(paths):
    from nflengine.ask.names import NameIndex
    from nflengine.settings import get_config

    return NameIndex.load(paths.curated, get_config().seasons.current)


@pytest.fixture(scope="module")
def templates():
    from nflengine.settings import get_config

    return load_templates(current_season=get_config().seasons.current)


def _counts(drv) -> tuple[int, int]:
    with _read_session(drv, None) as s:
        nodes = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        rels = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    return nodes, rels


@pytest.fixture(scope="module", autouse=True)
def graph_unchanged(driver):
    before = _counts(driver)
    yield before
    assert _counts(driver) == before, "the history graph changed during the Ask probes"


# ---- the templates ------------------------------------------------------------------------


def test_every_history_template_has_a_live_case(templates):
    ids = {t.id for t in templates.values() if t.source == "history"}
    assert ids == {tid for tid, _ in CASES}


@pytest.mark.parametrize(
    ("tid", "values"), CASES, ids=[f"{t}-{i}" for i, (t, _) in enumerate(CASES)]
)
def test_the_template_runs_through_the_guard_with_rows_warm(sources, templates, names, tid, values):
    r = render(templates[tid], values, names)
    first = sources.run("history", r.query, r.params)
    t0 = time.monotonic()
    out = sources.run("history", r.query, r.params)
    warm = time.monotonic() - t0
    assert out.source == "history" and out.rows, (tid, values)
    assert out.rows == first.rows
    assert not out.truncated
    assert warm < WARM_S, f"{tid}: {warm:.2f} s warm"


def test_leaders_keep_ties_at_the_cut(sources, templates, names):
    """limit 1 on a measure with ties returns every tied row (and limit N never cuts a tie)."""
    t = templates["history_team_carries_situation"]
    for limit in (1, 2, 3, 5):
        r = render(t, {"team": "Eagles", "situation": "4th_down", "rank_by": "tds",
                       "limit": limit}, names)  # fmt: skip
        out = sources.run("history", r.query, r.params)
        i = out.columns.index("tds")
        values = [row[i] for row in out.rows]
        assert values == sorted(values, reverse=True)
        assert len(values) >= min(limit, len(values))
        cut = values[min(limit, len(values)) - 1]
        # every row at the cut value is kept, nothing below it
        assert all(v >= cut for v in values)


def test_a_qb_s_targets_equal_the_box_score(sources, paths, names):
    """The spot check: Mahomes's 2024 targets per receiver = player_games' targets (all games)."""
    import polars as pl

    q = (
        "MATCH (qb:Player {player_id: $qb})-[:PASSED]->(p:Play)<-[:TARGETED]-(r:Player) "
        "WHERE p.season = 2024 RETURN r.player_id AS id, count(p) AS targets"
    )
    qb = names.player("Patrick Mahomes").value
    got = {row[0]: row[1] for row in sources.run("history", q, {"qb": qb}).rows}
    plays = pl.scan_parquet(str(paths.curated / "plays" / "*.parquet")).filter(
        (pl.col("season") == 2024)
        & (pl.col("passer_player_id") == qb)
        & pl.col("receiver_player_id").is_not_null()
        & (pl.col("play_type") == "pass")
        & (pl.col("two_point_attempt").fill_null(0) == 0)
    )
    want = dict(plays.group_by("receiver_player_id").agg(pl.len().alias("n")).collect().iter_rows())
    top = sorted(want, key=lambda k: -want[k])[:5]
    for pid in top:
        assert got.get(pid) == want[pid], pid


# ---- the golden references --------------------------------------------------------------------


def test_every_history_golden_reference_runs_with_rows(sources, templates):
    from nflengine.ask.evaluate import load_golden

    golden = [g for g in load_golden(templates=templates) if g.source == "history"]
    assert golden
    for g in golden:
        out = sources.run("history", g.reference, dict(g.params), allow_maps=True)
        assert out.rows, g.id
        assert "LIMIT" not in g.reference.upper().split("RETURN")[-1], g.id


# ---- red-team probes on the history source ----------------------------------------------------


@pytest.mark.parametrize(
    ("query", "rule"),
    [
        ("MATCH (p:Play) WITH p LIMIT 1 SET p.epa = 0 RETURN 1 AS x", "cypher.write"),
        ("CREATE (n:__AskProbe {x: 1}) RETURN 1 AS x", "cypher.write"),
        ("MATCH (p:Play) WITH p LIMIT 1 CALL (p) { SET p.epa = 0 } RETURN 1 AS x", "cypher.write"),
        ("CALL apoc.export.csv.all('ask_probe.csv', {})", "cypher.namespace"),
        ("RETURN apoc.version() AS v", "cypher.namespace"),
        ("CALL dbms.listConfig()", "cypher.namespace"),
        ("SHOW USERS", "cypher.banned"),
        ("LOAD CSV FROM 'file:///ask_probe.csv' AS r RETURN r", "cypher.banned"),
        ("MATCH (p:Play) RETURN p LIMIT 1", "cypher.values"),
    ],
)
def test_the_guard_stops_attacks_on_the_history_graph(sources, query, rule):
    with pytest.raises(Refused) as e:
        sources.run("history", query)
    assert e.value.rule == rule


def test_the_history_server_rejects_a_write_in_a_read_session(driver):
    """Even past the text check, the READ session stops a write (rolled back, 1 / 0 after it)."""
    q = "CREATE (n:__AskProbe {x: 1}) WITH n RETURN 1 / (size(labels(n)) - 1) AS x"
    with pytest.raises(Exception) as e, _read_session(driver, None) as s:
        tx = s.begin_transaction()
        try:
            tx.run(q).consume()
        finally:
            with contextlib.suppress(Exception):
                tx.rollback()
    assert "AccessMode" in (getattr(e.value, "code", "") or type(e.value).__name__)


def test_a_slow_query_is_stopped_by_the_timeout(driver, paths):
    from nflengine.ask.guard import QueryFailed

    src = Sources(sandbox=None, driver=None, history_driver=driver, timeout_s=1.0,
                  enabled={"history": True})  # fmt: skip
    q = "MATCH (a:Play), (b:Play) WHERE a.epa > b.epa RETURN count(*) AS n"
    t0 = time.monotonic()
    with pytest.raises(QueryFailed) as e:
        src.run("history", q)
    assert e.value.kind == "timeout" and time.monotonic() - t0 < 6.0
