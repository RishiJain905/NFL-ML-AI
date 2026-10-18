"""AE00 safety probes against the real Neo4j and the real curated data (README §2 as tests).

Run with `uv run pytest -m integration tests/ask/test_ask_guard_live.py` (Docker Neo4j up, data
drive connected). Every test is read-only: nothing is written to the graph, the data root or the
repo. The two probes that try a write do it inside a transaction that is always rolled back, and
the statement fails after the write on purpose (a division by zero), so even a server
that did not enforce READ mode could not persist anything. A module fixture reads the node
and relationship counts before and after the whole module and fails if they differ.

Skips cleanly when Neo4j or the data root is unavailable.
"""

from __future__ import annotations

import contextlib
import hashlib
import time

import pytest
from ask_fakes import tree_listing

from nflengine.ask.guard import (
    QueryFailed,
    Refused,
    Sources,
    SqlSandbox,
    _read_session,
    check_cypher,
    run_cypher,
    run_sql,
)

pytestmark = pytest.mark.integration

BS = chr(92)  # a backslash, built so no tooling layer can decode an escape

COUNT_NODES = "MATCH (n) RETURN count(n) AS c"
COUNT_RELS = "MATCH ()-[r]->() RETURN count(r) AS c"


@pytest.fixture(scope="module")
def driver():
    from nflengine.graph.client import get_driver

    try:
        drv = get_driver()
        drv.verify_connectivity()
    except Exception as e:  # noqa: BLE001 - no Neo4j: skip, don't fail
        pytest.skip(f"Neo4j not reachable ({type(e).__name__})")
    yield drv
    drv.close()


@pytest.fixture(scope="module")
def paths():
    from nflengine.paths import ensure_data_root

    try:
        p = ensure_data_root(create_dirs=False)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no data root ({type(e).__name__})")
    if not (p.curated / "games.parquet").exists():
        pytest.skip("no curated data")
    return p


def _counts(drv) -> tuple[int, int]:
    with _read_session(drv, None) as s:
        nodes = s.run(COUNT_NODES).single()["c"]
        rels = s.run(COUNT_RELS).single()["c"]
    return nodes, rels


@pytest.fixture(scope="module", autouse=True)
def graph_unchanged(driver):
    """The graph must be exactly as it was when the module started."""
    before = _counts(driver)
    yield before
    after = _counts(driver)
    assert after == before, f"the graph changed during the Ask probes: {before} -> {after}"


class SpyDriver:
    """Delegates to the real driver and counts the sessions opened."""

    def __init__(self, inner):
        self.inner = inner
        self.sessions = 0

    def session(self, **kw):
        self.sessions += 1
        return self.inner.session(**kw)


def _try_write_in_read_session(drv, query: str):
    """Run `query` in a READ session inside a transaction that is always rolled back. The
    query must fail after its write (1/0) so nothing could persist anyway."""
    with _read_session(drv, None) as s:
        tx = s.begin_transaction()
        try:
            tx.run(query).consume()
        finally:
            with contextlib.suppress(Exception):
                tx.rollback()


# ---- writes -----------------------------------------------------------------------------------


class TestWrites:
    @pytest.mark.parametrize(
        "query",
        [
            "CREATE (n:__AskProbe {x: 1}) WITH n RETURN 1 / (size(labels(n)) - 1) AS x",
            "MATCH (t:Team) WITH t LIMIT 1 SET t.__ask_probe = 1 "
            "WITH t RETURN 1 / (size(labels(t)) - 1) AS x",
            "MATCH (t:Team) WITH t LIMIT 1 CALL (t) { SET t.__ask_probe = 1 } "
            "RETURN 1 / (size(labels(t)) - 1) AS x",
            "MERGE (n:__AskProbe {x: 1}) WITH n RETURN 1 / (size(labels(n)) - 1) AS x",
        ],
        ids=["create", "set", "set-in-call-subquery", "merge"],
    )
    def test_the_server_rejects_a_write_in_a_read_session(self, driver, query):
        with pytest.raises(Exception) as exc:  # noqa: B017 - the code is what matters
            _try_write_in_read_session(driver, query)
        assert getattr(exc.value, "code", None) == "Neo.ClientError.Statement.AccessMode"

    @pytest.mark.parametrize(
        "query",
        [
            "CREATE (n:__AskProbe {x: 1})",
            "MATCH (n:__AskProbe) DETACH DELETE n",
            "MATCH (t:Team) SET t.__ask_probe = 1",
            "MERGE (n:__AskProbe {x: 1})",
            "MATCH (t:Team) WITH t LIMIT 1 CALL (t) { SET t.__ask_probe = 1 } "
            "RETURN t.team_id AS t",
            "MATCH (t:Team) FOREACH (x IN [1] | SET t.__ask_probe = x)",
            "MATCH (t:Team) REMOVE t.__ask_probe",
        ],
    )
    def test_the_guard_refuses_writes_before_the_server_sees_them(self, driver, query):
        spy = SpyDriver(driver)
        with pytest.raises(Refused) as exc:
            run_cypher(spy, query)
        assert exc.value.rule == "cypher.write"
        assert spy.sessions == 0

    def test_a_write_the_text_check_missed_is_stopped_by_the_plan(self, driver):
        """The text check is the first layer; if a spelling slipped past it, EXPLAIN in a
        READ session would still report a write plan. Run the plan check on the text alone
        (a MATCH that matches nothing, so nothing is written even if it ran)."""
        from nflengine.ask.guard import explain_cypher

        with pytest.raises(Refused) as exc:
            explain_cypher(driver, "MATCH (n:__NoSuchLabelAsk) SET n.x = 1")
        assert exc.value.rule == "cypher.plan_not_read"


# ---- things that plan as read but must not run ------------------------------------------------


REFUSED_BY_TEXT = [
    ("LOAD CSV FROM 'file:///etc/passwd' AS r RETURN r", "cypher.banned"),
    ("LOAD CSV WITH HEADERS FROM 'https://example.invalid/x.csv' AS r RETURN r", "cypher.banned"),
    ("CALL apoc.load.json('file:///x.json') YIELD value RETURN value", "cypher.namespace"),
    ("CALL apoc.export.csv.all('x.csv', {})", "cypher.namespace"),
    ("CALL gds.graph.project('g', '*', '*')", "cypher.namespace"),
    ("CALL apoc.util.sleep(3000)", "cypher.namespace"),
    ("CALL dbms.listConfig() YIELD name RETURN name", "cypher.namespace"),
    ("SHOW USERS", "cypher.banned"),
    ("SHOW PROCEDURES", "cypher.banned"),
    ("EXPLAIN MATCH (n) RETURN n", "cypher.banned"),
    ("CALL db.labels() YIELD label RETURN label", "cypher.namespace"),
    ("RETURN apoc.version() AS v", "cypher.namespace"),
    ("RETURN apoc.cypher.runFirstColumnSingle('RETURN 1 AS x', {}) AS r", "cypher.namespace"),
    (f"RETURN {BS}u0061poc.version() AS v", "cypher.escape"),
    (
        f"RETURN {BS}u0061poc.cypher.runFirstColumnSingle('RETURN 1 AS x', {{}}) AS r",
        "cypher.escape",
    ),
    (f"RETURN {BS}u0067ds.version() AS v", "cypher.escape"),
    (f"RETURN '{BS}u0027 + apoc.version() + {BS}u0027' AS v", "cypher.escape"),
    (f"RETURN 1 AS a // {BS}u000a CREATE (n:Hack)", "cypher.escape"),
    (
        "RETURN [1,2,3][0..apoc.cypher.runFirstColumnSingle('RETURN 1 AS x', {})] AS s",
        "cypher.namespace",
    ),
    ("RETURN [1,2,3][0 .. gds.version()] AS s", "cypher.namespace"),
]


class TestPlansAsReadButMustNotRun:
    @pytest.mark.parametrize("query, rule", REFUSED_BY_TEXT)
    def test_the_guard_refuses_and_the_server_is_never_asked(self, driver, query, rule):
        spy = SpyDriver(driver)
        with pytest.raises(Refused) as exc:
            run_cypher(spy, query)
        assert exc.value.rule == rule
        assert spy.sessions == 0

    def test_control_the_server_does_run_these_in_read_mode(self, driver):
        """Why the text check matters: plain reads of apoc / gds functions, in any of the three
        spellings that hid them, run on the server. If this ever fails, the server changed."""
        queries = [
            "RETURN apoc.version() AS v",
            f"RETURN {BS}u0061poc.version() AS v",
            "RETURN [1,2,3][0..toInteger(apoc.version() = 'x')] AS v",
            f"RETURN {BS}u0067ds.version() AS v",
        ]
        for q in queries:
            with _read_session(driver, None) as s:
                assert s.run(q).single() is not None, q

    def test_control_an_escaped_quote_closes_a_string_on_the_server(self, driver):
        """Sol's query: the scanner sees one string, Neo4j decodes the escapes before lexing
        and runs `'' + apoc.version() + ''`. Read-only; the guard must refuse it (below)."""
        q = f"RETURN '{BS}u0027 + apoc.version() + {BS}u0027' AS v"
        with _read_session(driver, None) as s:
            value = s.run(q).single()["v"]
        assert isinstance(value, str) and value[:1].isdigit()  # the apoc version, not the text

    def test_the_escaped_quote_query_is_refused_and_the_server_never_asked(self, driver):
        q = f"RETURN '{BS}u0027 + apoc.version() + {BS}u0027' AS v"
        spy = SpyDriver(driver)
        with pytest.raises(Refused) as exc:
            run_cypher(spy, q)
        assert exc.value.rule == "cypher.escape" and spy.sessions == 0

    def test_control_load_csv_and_procedures_plan_as_reads(self, driver):
        """EXPLAIN never runs the query. These are the plans the text check exists for."""
        for q in (
            "EXPLAIN LOAD CSV FROM 'file:///no-such-file.csv' AS r RETURN r",
            "EXPLAIN CALL apoc.util.sleep(1)",
        ):
            with _read_session(driver, None) as s:
                summary = s.run(q).consume()
            assert summary.query_type == "r"  # README §2: they plan as read

    def test_the_plan_check_catches_a_procedure_and_a_file_read_the_text_check_did_not(
        self, driver
    ):
        from nflengine.ask.guard import explain_cypher

        for q in (
            "CALL apoc.util.sleep(1)",
            "LOAD CSV FROM 'file:///no-such-file.csv' AS r RETURN r",
        ):
            with pytest.raises(Refused) as exc:
                explain_cypher(driver, q)
            assert exc.value.rule in ("cypher.plan_operator", "cypher.plan_not_read")

    def test_a_clean_library_style_read_passes_the_whole_stack(self, driver):
        res = run_cypher(driver, "MATCH (t:Team) RETURN t.team_id AS team ORDER BY team")
        assert res.columns == ["team"] and len(res.rows) >= 30
        assert all(isinstance(r[0], str) for r in res.rows)
        assert not res.truncated

    def test_neo4js_own_namespaced_functions_run(self, driver):
        res = run_cypher(
            driver,
            "RETURN date.truncate('month', date('2026-10-17')) AS d, "
            "duration.between(date('2026-01-01'), date('2026-02-01')) AS dur",
        )
        assert res.rows[0][0] == "2026-10-01"
        assert res.rows[0][1].startswith("P")


# ---- limits ---------------------------------------------------------------------------------


class TestLimits:
    # a cartesian count(*) is answered from the count store, so enumerate paths instead
    HEAVY = "MATCH (a)-[*1..8]-(b) RETURN count(*) AS c"

    def test_the_timeout_stops_a_heavy_match(self, driver):
        t0 = time.monotonic()
        with pytest.raises(QueryFailed) as exc:
            run_cypher(driver, self.HEAVY, timeout_s=1.0)
        elapsed = time.monotonic() - t0
        assert exc.value.kind == "timeout", exc.value
        assert elapsed < 4.0, elapsed
        assert run_cypher(driver, "RETURN 1 AS x").rows == [[1]]  # the server is fine after

    def test_the_timeout_comes_through_sources(self, driver, paths):
        src = Sources(sandbox=None, driver=driver, timeout_s=1.0, cap=5)
        with pytest.raises(QueryFailed) as exc:
            src.run("graph", self.HEAVY)
        assert exc.value.kind == "timeout"

    def test_the_row_cap(self, driver):
        res = run_cypher(driver, "UNWIND range(1, 100000) AS i RETURN i", cap=50)
        assert len(res.rows) == 50 and res.truncated
        assert res.rows[0] == [1] and res.rows[-1] == [50]

    def test_the_default_cap_on_a_real_table_of_nodes(self, driver):
        res = run_cypher(driver, "MATCH (p:Player) RETURN p.name AS name")
        assert len(res.rows) == 500 and res.truncated

    def test_a_syntax_error_is_a_failure_with_a_code(self, driver):
        with pytest.raises(QueryFailed) as exc:
            run_cypher(driver, "MATCH (n RETURN n.x")
        assert exc.value.kind == "syntax"
        assert exc.value.code == "CypherSyntaxError: Neo.ClientError.Statement.SyntaxError"
        assert "MATCH" not in exc.value.code and exc.value.hint


class TestValuesOnTheRealGraph:
    def test_whole_nodes_relationships_paths_and_maps_are_refused(self, driver):
        for q in (
            "MATCH (t:Team) RETURN t LIMIT 1",
            "MATCH (a:Team)-[r:PLAYED_IN]->(g:Game) RETURN r LIMIT 1",
            "MATCH p = (a:Team)-[:PLAYED_IN]->(g:Game) RETURN p LIMIT 1",
            "MATCH (t:Team) RETURN {id: t.team_id} AS m LIMIT 1",
            "MATCH (t:Team) RETURN collect(t) AS ts",
        ):
            with pytest.raises(Refused) as exc:
                run_cypher(driver, q)
            assert exc.value.rule == "cypher.values", q

    def test_library_templates_may_return_maps_but_never_nodes(self, driver):
        res = run_cypher(
            driver, "MATCH (t:Team) RETURN {id: t.team_id} AS m LIMIT 1", allow_maps=True
        )
        assert isinstance(res.rows[0][0], dict)
        with pytest.raises(Refused):
            run_cypher(driver, "MATCH (t:Team) RETURN {node: t} AS m LIMIT 1", allow_maps=True)

    def test_temporal_values_become_text(self, driver):
        res = run_cypher(
            driver,
            "RETURN date('2026-10-17') AS d, datetime('2026-10-17T01:02:03Z') AS dt, "
            "duration({days: 1}) AS du",
        )
        d, dtime, du = res.rows[0]
        assert d == "2026-10-17"
        assert dtime.startswith("2026-10-17T01:02:03")
        assert du == "P1D"

    def test_a_property_called_key_is_just_a_value(self, driver):
        res = run_cypher(driver, "RETURN 'x' AS key")
        assert res.rows == [["x"]] and res.columns == ["key"]


# ---- the SQL sandbox on the real curated folder ----------------------------------------------


@pytest.fixture(scope="module")
def sandbox(paths):
    return SqlSandbox(paths.curated)


class TestRealSql:
    def test_a_plain_question_works_and_reads_little(self, sandbox):
        res = run_sql(sandbox, "SELECT count(*) AS n FROM games WHERE season = 2023")
        assert res.rows[0][0] > 200
        res = run_sql(
            sandbox, "SELECT season, count(*) AS plays FROM plays GROUP BY season ORDER BY 1"
        )
        assert res.row_count >= 10

    def test_wide_tables_refuse_star(self, sandbox):
        for table in ("plays", "player_games"):
            assert len(sandbox.columns(table)) > 60, table
            with pytest.raises(Refused) as exc:
                run_sql(sandbox, f"SELECT * FROM {table} LIMIT 1")
            assert exc.value.rule == "sql.star"

    def test_the_free_text_table_is_not_reachable(self, sandbox):
        assert "espn_news" not in sandbox.tables()
        with pytest.raises(Refused) as exc:
            run_sql(sandbox, "SELECT * FROM espn_news")
        assert exc.value.rule == "sql.table"

    def test_the_plays_goal_to_go_types_unify_across_seasons(self, sandbox):
        res = run_sql(
            sandbox,
            "SELECT count(*) AS n, count(goal_to_go) AS known FROM plays WHERE season >= 2010",
        )
        assert res.rows[0][0] >= res.rows[0][1] > 0

    def test_nothing_outside_the_sandbox_can_be_read_or_written(self, sandbox, paths, tmp_path):
        research = paths.research
        raw = paths.raw
        some_research = next((p for p in research.rglob("*") if p.is_file()), None)
        some_raw = next((p for p in raw.rglob("*") if p.is_file()), None)
        duck = paths.curated / "nfl.duckdb"
        duck_hash = hashlib.sha256(duck.read_bytes()).hexdigest() if duck.exists() else None
        curated_before = tree_listing(paths.curated)
        attempts = []
        for f in (some_research, some_raw):
            if f is not None:
                p = f.as_posix()
                attempts += [
                    f"SELECT * FROM read_parquet('{p}')",
                    f"SELECT * FROM read_csv('{p}')",
                    f"SELECT * FROM read_text('{p}')",
                    f"SELECT * FROM '{p}'",
                    f"SELECT * FROM glob('{f.parent.as_posix()}/*')",
                ]
        attempts += [
            f"COPY (SELECT 1) TO '{(paths.curated / 'ask_probe.csv').as_posix()}'",
            f"COPY games TO '{(tmp_path / 'g.parquet').as_posix()}' (FORMAT PARQUET)",
            f"ATTACH '{duck.as_posix()}' AS probe",
            f"ATTACH '{(paths.curated / 'ask_probe.duckdb').as_posix()}' AS probe2",
            "INSTALL httpfs",
            "LOAD httpfs",
            "SELECT * FROM read_text('C:/Windows/win.ini')",
            "SELECT * FROM read_text('/etc/passwd')",
            "SELECT * FROM duckdb_settings()",
            "SELECT current_setting('home_directory')",
            "EXPORT DATABASE 'ask_probe_export'",
            "SET enable_external_access = true",
        ]
        for sql in attempts:
            with pytest.raises((Refused, QueryFailed)):
                run_sql(sandbox, sql)
        # and at the connection level, past the AST check
        con = sandbox.connect()
        try:
            import duckdb

            for sql in attempts:
                # settings reads and SET are stopped by the AST check, not by the connection
                if sql.startswith(
                    ("SELECT current_setting", "SET ", "SELECT * FROM duckdb_settings")
                ):
                    continue
                with pytest.raises(duckdb.Error):
                    con.execute(sql)
        finally:
            con.close()
        assert tree_listing(paths.curated) == curated_before
        assert not list(paths.curated.glob("ask_probe*"))
        if duck_hash is not None:
            assert hashlib.sha256(duck.read_bytes()).hexdigest() == duck_hash
        assert not (tmp_path / "g.parquet").exists()


# ---- the graph, last -------------------------------------------------------------------------


def test_the_graph_counts_are_what_the_module_started_with(driver, graph_unchanged):
    assert _counts(driver) == graph_unchanged


def test_the_run_cypher_path_never_changes_the_graph_even_for_a_refused_batch(driver):
    spy = SpyDriver(driver)
    before = _counts(driver)
    for query, _ in REFUSED_BY_TEXT:
        with contextlib.suppress(Refused):
            run_cypher(spy, query)
    assert _counts(driver) == before


def test_check_cypher_is_the_same_offline_and_online(driver):
    """The text check has no server dependency: the same answer with or without Neo4j."""
    for query, rule in REFUSED_BY_TEXT:
        with pytest.raises(Refused) as exc:
            check_cypher(query)
        assert exc.value.rule == rule
