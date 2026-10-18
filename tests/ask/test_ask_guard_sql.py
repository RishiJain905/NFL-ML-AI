"""AE00 guard red-team, SQL side: `SqlSandbox`, `check_sql_plan` / `check_sql`, `run_sql`,
`Sources` (the SQL half, the weekly-lock refusal). Everything runs over a tiny curated folder
under `tmp_path`: no network, no data root, no Neo4j.

The sandbox has two layers and both are tested: the AST check (refuses a query before it runs)
and the connection itself (no file access whatever the SQL says). The new column-loading design
(D136: each question reads only the columns it names, file by file, into memory) has its own
section: the star rules, USING / NATURAL, the column caps, the mixed-type `plays` files.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import sys
import threading
import time
from types import SimpleNamespace

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from ask_fakes import FakeDriver, make_sandbox, tree_listing, write_curated, write_wide

from nflengine.ask.config import AskConfig
from nflengine.ask.guard import (
    MAX_COLUMNS,
    MAX_QUERY_CHARS,
    WIDE_TABLE,
    QueryFailed,
    Refused,
    Sources,
    SqlPlan,
    SqlSandbox,
    check_sql,
    check_sql_plan,
    run_sql,
)
from nflengine.fsutil import replace_file
from nflengine.ops.lock import run_lock

TEAMS = [["KC", 14, "Chiefs", 0.824], ["BUF", 11, "Bills", 0.647]]


@pytest.fixture
def sb(tmp_path):
    return make_sandbox(tmp_path)


def refused(sb, sql, params=None, **kw) -> Refused:
    with pytest.raises(Refused) as exc:
        run_sql(sb, sql, params, **kw)
    return exc.value


def failed(sb, sql, params=None, **kw) -> QueryFailed:
    with pytest.raises(QueryFailed) as exc:
        run_sql(sb, sql, params, **kw)
    return exc.value


def rows(sb, sql, params=None, **kw):
    return run_sql(sb, sql, params, **kw).rows


# ---- what must keep working -----------------------------------------------------------------


class TestAllowed:
    @pytest.mark.parametrize(
        "sql, expected",
        [
            ("SELECT team FROM teams ORDER BY wins DESC LIMIT 2", [["KC"], ["DET"]]),
            (
                "WITH w AS (SELECT team, wins FROM teams WHERE wins > 10) "
                "SELECT team FROM w ORDER BY team",
                [["BUF"], ["DET"], ["KC"]],
            ),
            (
                "WITH a AS (SELECT 1 AS x), b AS (SELECT x + 1 AS y FROM a) SELECT y FROM b",
                [[2]],
            ),
            (
                "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 3) "
                "SELECT n FROM r",
                [[1], [2], [3]],
            ),
            (
                "SELECT t.team, g.home_score FROM teams t JOIN games g ON t.team = g.home_team "
                "ORDER BY 1",
                [["BUF", 31], ["DAL", 17], ["KC", 27]],
            ),
            (
                "SELECT t.team, g.home_score FROM teams t LEFT JOIN games g "
                "ON t.team = g.home_team WHERE g.game_id IS NULL",
                [["DET", None]],
            ),
            (
                "SELECT team FROM teams WHERE team IN (SELECT home_team FROM games) ORDER BY 1",
                [["BUF"], ["DAL"], ["KC"]],
            ),
            (
                "SELECT team FROM teams t WHERE EXISTS "
                "(SELECT 1 FROM games g WHERE g.home_team = t.team AND g.season = 2024)",
                [["DAL"]],
            ),
            (
                "SELECT team, (SELECT max(wins) FROM teams) AS m FROM teams WHERE wins = 7",
                [["DAL", 14]],
            ),
            (
                "SELECT team, sum(wins) OVER (ORDER BY team) AS run FROM teams ORDER BY team",
                [["BUF", 11], ["DAL", 18], ["DET", 30], ["KC", 44]],
            ),
            ("SELECT team FROM teams QUALIFY row_number() OVER (ORDER BY wins) = 1", [["DAL"]]),
            ("SELECT x FROM range(3) t(x)", [[0], [1], [2]]),
            ("SELECT x FROM generate_series(1, 3) t(x)", [[1], [2], [3]]),
            ("SELECT unnest([1, 2, 3]) AS x", [[1], [2], [3]]),
            ("SELECT x FROM unnest([1, 2]) t(x)", [[1], [2]]),
            ("SELECT x FROM (VALUES (1), (2)) v(x)", [[1], [2]]),
            ("FROM teams SELECT team ORDER BY team LIMIT 1", [["BUF"]]),
            ("SELECT TEAM FROM TEAMS ORDER BY TEAM LIMIT 1", [["BUF"]]),
            ('SELECT "team" FROM "teams" ORDER BY 1 LIMIT 1', [["BUF"]]),
            ("SELECT team FROM main.teams ORDER BY 1 LIMIT 1", [["BUF"]]),
            ("SELECT teams.team FROM teams ORDER BY 1 LIMIT 1", [["BUF"]]),
            (
                "SELECT 'DROP TABLE teams; COPY x TO y -- not code' AS s",
                [["DROP TABLE teams; COPY x TO y -- not code"]],
            ),
            ("SELECT 1 AS a -- trailing comment", [[1]]),
            ("SELECT 1 AS a /* trailing comment */", [[1]]),
            ("SELECT 1 AS a;", [[1]]),
            ("SELECT 1 AS a -- c\n;", [[1]]),
            ("SELECT 1 AS a; ;", [[1]]),
            ("-- leading comment\nSELECT 1 AS a", [[1]]),
            ("SELECT a FROM (SELECT 1 AS a UNION ALL SELECT 2) ORDER BY a DESC", [[2], [1]]),
            ("SELECT list_transform([1, 2], x -> x + wins) FROM teams WHERE wins = 7", [[[8, 9]]]),
        ],
    )
    def test_query(self, sb, sql, expected):
        assert rows(sb, sql) == expected

    def test_named_parameters_are_bound_not_pasted(self, sb):
        res = run_sql(sb, "SELECT team FROM teams WHERE wins > $w ORDER BY team", {"w": 11})
        assert res.rows == [["DET"], ["KC"]]
        assert res.params == {"w": 11}
        assert "11" not in res.query  # the text still has $w
        assert rows(sb, "SELECT $a + $b AS s", {"a": 1, "b": 2}) == [[3]]
        # a parameter carrying SQL stays a value
        assert rows(sb, "SELECT team FROM teams WHERE team = $t", {"t": "KC' OR '1'='1"}) == []

    def test_the_params_dict_is_copied(self, sb):
        params = {"w": 1}
        run_sql(sb, "SELECT team FROM teams WHERE wins > $w", params)
        assert params == {"w": 1}

    def test_columns_and_result_fields(self, sb):
        res = run_sql(sb, "SELECT * FROM teams ORDER BY team LIMIT 2")
        assert res.columns == ["team", "wins", "name", "win_pct"]
        assert res.source == "sql" and res.database is None
        assert res.query == "SELECT * FROM teams ORDER BY team LIMIT 2"
        assert not res.truncated and res.row_count == 2
        assert res.checks_seconds <= res.seconds
        d = res.to_dict()
        assert json.loads(json.dumps(d))["rows"][0][0] == "BUF"

    def test_the_text_that_ran_loses_its_final_semicolon(self, sb):
        assert run_sql(sb, "SELECT 1 AS a;").query == "SELECT 1 AS a"

    def test_a_trailing_comment_is_allowed(self, sb):
        assert rows(sb, "SELECT 1 AS a -- why") == [[1]]

    @pytest.mark.parametrize(
        "tail",
        ["; -- done", ";-- done", " ; /* done */", ";\n-- done\n", ";;", "; -- a; b", ";  \n  "],
    )
    def test_a_semicolon_and_whatever_follows_it_is_dropped(self, sb, tail):
        res = run_sql(sb, "SELECT team FROM teams ORDER BY team LIMIT 1" + tail)
        assert res.rows == [["BUF"]]
        assert res.query == "SELECT team FROM teams ORDER BY team LIMIT 1"

    def test_a_semicolon_in_a_string_or_comment_is_not_the_end(self, sb):
        assert rows(sb, "SELECT 'a;b' AS s -- x; y") == [["a;b"]]
        assert rows(sb, "SELECT 1 AS a /* ; */") == [[1]]

    @pytest.mark.parametrize(
        "sql",
        ["DESCRIBE teams", "SUMMARIZE teams", "SHOW TABLES", "SHOW ALL TABLES", "DESCRIBE games"],
    )
    def test_describe_summarize_and_show_are_refused(self, sb, sql):
        assert refused(sb, sql).rule in ("sql.not_select", "sql.parse")

    def test_quoted_identifiers_keep_their_case_in_the_output(self, tmp_path):
        cur = write_curated(tmp_path)
        pq.write_table(
            pa.table({"general__schedAdjQBR": [1.5, 2.5], "Name": ["a", "b"]}),
            cur / "espn_qbr.parquet",
        )
        sandbox = SqlSandbox(cur)
        res = run_sql(sandbox, "SELECT * FROM espn_qbr")
        assert res.columns == ["general__schedAdjQBR", "Name"]


# ---- refusals -------------------------------------------------------------------------------


class TestRefused:
    @pytest.fixture
    def fs(self, tmp_path, monkeypatch):
        """A sandbox, a duckdb file inside its folder, and an empty cwd to watch."""
        cur = write_curated(tmp_path)
        con = duckdb.connect(str(cur / "nfl.duckdb"))
        con.execute("CREATE TABLE secret (x INT)")
        con.execute("INSERT INTO secret VALUES (42)")
        con.close()
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        return SimpleNamespace(
            sb=SqlSandbox(cur),
            cur=cur,
            tmp=tmp_path,
            cwd=cwd,
            out=(tmp_path / "out.csv").as_posix(),
        )

    def statements(self, fs) -> list[tuple[str, str]]:
        db = (fs.cur / "nfl.duckdb").as_posix()
        out = fs.out
        return [
            (f"COPY teams TO '{out}'", "sql.not_select"),
            (f"COPY (SELECT 1) TO '{out}' (FORMAT CSV)", "sql.not_select"),
            (f"COPY teams TO '{out}.parquet' (FORMAT PARQUET)", "sql.not_select"),
            (f"ATTACH '{db}' AS x", "sql.not_select"),
            (f"ATTACH '{db}' AS x (READ_ONLY)", "sql.not_select"),
            ("ATTACH ':memory:' AS m", "sql.not_select"),
            ("DETACH x", "sql.not_select"),
            ("INSTALL httpfs", "sql.not_select"),
            ("LOAD httpfs", "sql.not_select"),
            (f"EXPORT DATABASE '{(fs.tmp / 'exp').as_posix()}'", "sql.not_select"),
            ("SET threads = 1", "sql.not_select"),
            ("SET enable_external_access = true", "sql.not_select"),
            ("RESET threads", "sql.not_select"),
            ("PRAGMA database_list", "any"),
            ("PRAGMA enable_progress_bar", "any"),
            ("CREATE TABLE t AS SELECT 1", "sql.not_select"),
            ("CREATE VIEW v AS SELECT 1", "sql.not_select"),
            ("CREATE MACRO m() AS 1", "sql.not_select"),
            ("INSERT INTO teams VALUES ('X', 1, 'x', 0.1)", "sql.not_select"),
            ("UPDATE teams SET wins = 1", "sql.not_select"),
            ("DELETE FROM teams", "sql.not_select"),
            ("DROP TABLE teams", "sql.not_select"),
            ("ALTER TABLE teams ADD COLUMN z INT", "sql.not_select"),
            ("WITH x AS (SELECT 1) DELETE FROM teams", "sql.not_select"),
            ("WITH x AS (SELECT 1) INSERT INTO teams SELECT * FROM teams", "sql.not_select"),
            ("CHECKPOINT", "sql.not_select"),
            ("VACUUM", "sql.not_select"),
            ("ANALYZE", "sql.not_select"),
            ("CALL pragma_database_list()", "sql.not_select"),
            ("EXPLAIN SELECT * FROM teams", "sql.not_select"),
            ("EXPLAIN ANALYZE SELECT * FROM teams", "sql.not_select"),
            ("USE memory", "sql.not_select"),
            ("BEGIN TRANSACTION", "sql.not_select"),
            ("COMMIT", "sql.not_select"),
            ("PREPARE p AS SELECT 1", "sql.not_select"),
            ("EXECUTE p", "sql.not_select"),
            ("SELECT 1; SELECT 2", "sql.multiple_statements"),
            ("SELECT 1; DROP TABLE teams", "sql.multiple_statements"),
            ("SELECT 1;;SELECT 2", "sql.multiple_statements"),
            ("SELECT 1; -- c\nSELECT 2", "sql.multiple_statements"),
            (f"SELECT 1; COPY teams TO '{out}'", "sql.multiple_statements"),
            ("PIVOT teams ON name USING sum(wins)", "sql.multiple_statements"),
        ]

    def test_statements_other_than_one_select(self, fs):
        for sql, rule in self.statements(fs):
            got = refused(
                fs.sb, sql
            ).rule  # a PRAGMA is a SELECT of pragma_*(): any refusal will do
            assert rule == "any" or got == rule, sql

    @pytest.mark.parametrize(
        "sql, fn",
        [
            ("SELECT * FROM read_parquet('x.parquet')", "read_parquet"),
            ("SELECT * FROM READ_PARQUET('x.parquet')", "read_parquet"),
            ("SELECT * FROM main.read_parquet('x.parquet')", "read_parquet"),
            ("SELECT * FROM \"read_parquet\"('x.parquet')", "read_parquet"),
            ("SELECT * FROM parquet_scan('x.parquet')", "parquet_scan"),
            ("SELECT * FROM parquet_metadata('x.parquet')", "parquet_metadata"),
            ("SELECT * FROM parquet_schema('x.parquet')", "parquet_schema"),
            ("SELECT * FROM read_csv('x.csv')", "read_csv"),
            ("SELECT * FROM read_csv_auto('x.csv')", "read_csv_auto"),
            ("SELECT * FROM read_text('x.txt')", "read_text"),
            ("SELECT * FROM read_blob('x.bin')", "read_blob"),
            ("SELECT * FROM read_json('x.json')", "read_json"),
            ("SELECT * FROM read_ndjson('x.json')", "read_ndjson"),
            ("SELECT * FROM read_json_objects('x.json')", "read_json_objects"),
            ("SELECT * FROM glob('*')", "glob"),
            ("SELECT * FROM sniff_csv('x.csv')", "sniff_csv"),
            ("SELECT * FROM duckdb_settings()", "duckdb_settings"),
            ("SELECT * FROM duckdb_extensions()", "duckdb_extensions"),
            ("SELECT * FROM duckdb_tables()", "duckdb_tables"),
            ("SELECT * FROM duckdb_functions()", "duckdb_functions"),
            ("SELECT * FROM duckdb_secrets()", "duckdb_secrets"),
            ("SELECT * FROM duckdb_databases()", "duckdb_databases"),
            ("SELECT * FROM pragma_database_list()", "pragma_database_list"),
            ("SELECT * FROM query('SELECT 1')", "query"),
            ("SELECT * FROM query_table('teams')", "query_table"),
            ("SELECT * FROM which_secret('a', 'b')", "which_secret"),
            ("SELECT * FROM test_all_types()", "test_all_types"),
            ("SELECT * FROM repeat_row(1, 3)", "repeat_row"),
            ("SELECT * FROM iceberg_scan('x')", "iceberg_scan"),
            ("SELECT * FROM delta_scan('x')", "delta_scan"),
            ("SELECT * FROM sqlite_scan('x', 't')", "sqlite_scan"),
            ("SELECT * FROM arrow_scan(1, 2, 3)", "arrow_scan"),
            ("SELECT * FROM json_serialize_sql('SELECT 1')", "json_serialize_sql"),
            ("SELECT * FROM teams WHERE team IN (SELECT team FROM read_csv('x'))", "read_csv"),
            ("SELECT * FROM (SELECT * FROM read_blob('x'))", "read_blob"),
            ("WITH x AS (SELECT * FROM read_text('x')) SELECT * FROM x", "read_text"),
            ("SELECT * FROM teams t, LATERAL (SELECT * FROM read_csv('x')) z", "read_csv"),
            ("SELECT (SELECT count(*) FROM glob('*'))", "glob"),
            (
                "SELECT * FROM teams WHERE EXISTS (SELECT 1 FROM duckdb_settings())",
                "duckdb_settings",
            ),
        ],
    )
    def test_table_functions(self, sb, sql, fn):
        e = refused(sb, sql)
        assert e.rule == "sql.table_function" and e.detail == fn

    @pytest.mark.parametrize(
        "sql, fn",
        [
            ("SELECT current_setting('home_directory')", "current_setting"),
            ("SELECT CURRENT_SETTING('threads')", "current_setting"),
            ("SELECT main.current_setting('x')", "current_setting"),
            ("SELECT pg_catalog.current_setting('x')", "current_setting"),
            ("SELECT getvariable('x')", "getvariable"),
            ("SELECT read_text('x')", "read_text"),
            ("SELECT read_blob('x')", "read_blob"),
            ("SELECT json_serialize_sql('SELECT 1')", "json_serialize_sql"),
            ("SELECT json_deserialize_sql('{}')", "json_deserialize_sql"),
            ("SELECT json_execute_serialized_sql('{}')", "json_execute_serialized_sql"),
            ("SELECT * FROM teams WHERE current_setting('a') = 'b'", "current_setting"),
            ("SELECT team FROM teams ORDER BY current_setting('a')", "current_setting"),
            ("SELECT list_transform([1], x -> getvariable('a'))", "getvariable"),
            ("SELECT checkpoint()", "checkpoint"),
            ("SELECT enable_profiling()", "enable_profiling"),
            ("SELECT excel('x')", "excel"),
            ("SELECT sniff_csv('x')", "sniff_csv"),
            ("SELECT pg_catalog.lower('X')", "lower"),  # any other schema is refused
        ],
    )
    def test_scalar_functions(self, sb, sql, fn):
        e = refused(sb, sql)
        assert e.rule == "sql.function" and e.detail == fn

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM information_schema.tables",
            "SELECT * FROM information_schema.columns",
            "SELECT * FROM pg_catalog.pg_tables",
            "SELECT * FROM duckdb_tables",
            "SELECT * FROM sqlite_master",
            "SELECT * FROM system.main.teams",
            "SELECT * FROM temp.main.teams",
            "SELECT * FROM other.teams",
            "SELECT * FROM 'x.parquet'",
            'SELECT * FROM "x.csv"',
            "SELECT * FROM 'C:/Windows/win.ini'",
            "SELECT * FROM 'https://example.com/x.parquet'",
            "SELECT * FROM espn_news",
            "SELECT * FROM ESPN_NEWS",
            "SELECT * FROM main.espn_news",
            "SELECT * FROM nope",
            "SELECT * FROM teams t JOIN espn_news n ON true",
            "SELECT * FROM teams WHERE team IN (SELECT headline FROM espn_news)",
            "WITH a AS (SELECT * FROM espn_news) SELECT * FROM a",
        ],
    )
    def test_tables(self, sb, sql):
        assert refused(sb, sql).rule == "sql.table"

    @pytest.mark.parametrize("sql", ["DESCRIBE 'x.parquet'", "SUMMARIZE 'x.parquet'", "SHOW 'x'"])
    def test_describing_a_file_is_refused(self, sb, sql):
        assert refused(sb, sql).rule in ("sql.not_select", "sql.parse")

    def test_the_builtin_catalog_spelling_of_a_function_is_just_the_function(self, sb):
        assert rows(sb, "SELECT system.main.lower('X') AS v") == [["x"]]

    def test_import_database_does_not_read_anything_in_the_parse_step(self, sb, tmp_path):
        """BUG 9 (fixed): duckdb.extract_statements(IMPORT DATABASE 'dir') reads dir/schema.sql
        and returns its statements, so the guard ran the file's SELECT. The first token is now
        checked (lexically) before anything is parsed."""
        d = tmp_path / "db"
        d.mkdir()
        (d / "schema.sql").write_text("SELECT 42 AS x;\n")
        (d / "load.sql").write_text("")
        e = refused(sb, f"IMPORT DATABASE '{d.as_posix()}'")
        assert e.rule == "sql.not_select" and e.detail.lower() == "import"
        assert refused(sb, "IMPORT DATABASE 'no/such/dir'").rule == "sql.not_select"

    @pytest.mark.parametrize(
        "sql, word",
        [
            ("PRAGMA version", "pragma"),
            ("EXPORT DATABASE 'x'", "export"),
            ("COPY teams TO 'x'", "copy"),
            ("-- a comment first\nINSTALL httpfs", "install"),
            ("/* c */ ATTACH 'x' AS y", "attach"),
            ("CALL pragma_database_list()", "call"),
            ("  \n\t  set threads = 1", "set"),
            ("EXPLAIN SELECT 1", "explain"),
            ("DESCRIBE teams", "describe"),
            ("SUMMARIZE teams", "summarize"),
            ("SHOW TABLES", "show"),
        ],
    )
    def test_only_a_select_shaped_first_word_is_even_parsed(self, sb, sql, word):
        e = refused(sb, sql)
        assert e.rule == "sql.not_select"
        assert e.detail.lower() == word

    @pytest.mark.parametrize(
        "sql",
        [
            "-- c\nSELECT 1 AS a",
            "/* c */ SELECT 1 AS a",
            "(SELECT 1 AS a)",
            "  \n SELECT 1 AS a",
            "select 1 as a",
            "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
            "VALUES (1)",
            "FROM teams LIMIT 1",
            "TABLE teams LIMIT 1",
        ],
    )
    def test_select_shaped_statements_still_start_fine(self, sb, sql):
        assert run_sql(sb, sql).row_count == 1

    def test_a_tokenizer_failure_is_a_parse_refusal(self, sb, monkeypatch):
        def boom(q):
            raise duckdb.Error("no tokens")

        monkeypatch.setattr(duckdb, "tokenize", boom)
        assert refused(sb, "SELECT 1").rule == "sql.parse"

    def test_the_excluded_free_text_table_is_not_even_listed(self, sb):
        assert "espn_news" not in sb.tables()
        assert SqlSandbox(sb.curated, exclude=()).tables().count("espn_news") == 1

    @pytest.mark.parametrize("sql", ["", "   ", "\n", "-- only a comment", "/* x */"])
    def test_empty(self, sb, sql):
        assert refused(sb, sql).rule == "sql.empty"

    @pytest.mark.parametrize("sql", [";", " ; ", ";;"])
    def test_only_semicolons_are_refused(self, sb, sql):
        assert refused(sb, sql).rule in ("sql.empty", "sql.not_select")

    def test_none_is_empty(self, sb):
        assert refused(sb, None).rule == "sql.empty"

    def test_too_long(self, sb):
        ok = "SELECT '" + "a" * (MAX_QUERY_CHARS - len("SELECT '' AS a")) + "' AS a"
        assert len(ok) == MAX_QUERY_CHARS
        assert run_sql(sb, ok).row_count == 1
        assert refused(sb, ok + "a").rule == "sql.too_long"

    def test_a_syntax_error_is_a_failure_with_a_hint_not_a_refusal(self, sb):
        e = failed(sb, "SELECT * FROM teams WHERE")
        assert e.kind == "syntax" and e.code == "ParserException" and e.hint
        e = failed(sb, "SELECT 1 /* open")
        assert e.kind == "syntax"

    def test_a_misspelled_first_word_is_refused_like_any_other_statement(self, sb):
        e = refused(sb, "SELEC 1")
        assert e.rule == "sql.not_select" and e.detail == "SELEC"

    def test_nothing_is_written_anywhere(self, fs):
        before_tmp, before_cwd = tree_listing(fs.tmp), tree_listing(fs.cwd)
        db_hash = hashlib.sha256((fs.cur / "nfl.duckdb").read_bytes()).hexdigest()
        for sql, _ in self.statements(fs):
            with pytest.raises((Refused, QueryFailed)):
                run_sql(fs.sb, sql)
        assert tree_listing(fs.tmp) == before_tmp
        assert tree_listing(fs.cwd) == before_cwd == {}
        assert hashlib.sha256((fs.cur / "nfl.duckdb").read_bytes()).hexdigest() == db_hash
        assert not (fs.tmp / "out.csv").exists() and not (fs.tmp / "exp").exists()

    def test_a_refused_query_loads_nothing(self, sb, monkeypatch):
        calls = []
        monkeypatch.setattr(pq, "read_table", lambda *a, **k: calls.append(a) or None)
        for sql in ("DROP TABLE teams", "SELECT * FROM read_csv('x')", "SELECT * FROM nope"):
            refused(sb, sql)
        assert calls == []


# ---- the connection itself: no file access whatever the SQL says --------------------------


class TestSandboxConnection:
    """Defence in depth: these statements skip the AST check on purpose."""

    @pytest.fixture
    def con(self, tmp_path, monkeypatch):
        cur = write_curated(tmp_path)
        d = duckdb.connect(str(cur / "nfl.duckdb"))
        d.execute("CREATE TABLE secret (x INT)")
        d.close()
        monkeypatch.chdir(tmp_path)
        self.cur, self.tmp = cur, tmp_path
        sandbox = SqlSandbox(cur)
        con = sandbox.connect()
        yield con
        con.close()

    def test_the_configuration_is_the_documented_one(self, con):
        def setting(name):
            return con.execute(f"SELECT current_setting('{name}')").fetchone()[0]

        assert setting("enable_external_access") is False
        assert setting("autoinstall_known_extensions") is False
        assert setting("autoload_known_extensions") is False
        assert setting("python_enable_replacements") is False
        assert setting("lock_configuration") is True
        assert setting("temp_directory") == ""
        assert setting("threads") == 4

    def test_the_configuration_is_locked(self, con):
        for sql in (
            "SET enable_external_access = true",
            "SET threads = 1",
            "SET lock_configuration = false",
            "SET allowed_directories = ['/']",
            "RESET enable_external_access",
        ):
            with pytest.raises(duckdb.Error):
                con.execute(sql)

    def test_no_file_can_be_read_or_written(self, con):
        before = tree_listing(self.tmp)
        real = (self.cur / "teams.parquet").as_posix()
        attempts = [
            f"SELECT * FROM read_parquet('{real}')",
            f"SELECT * FROM parquet_scan('{real}')",
            f"SELECT * FROM '{real}'",
            f"SELECT * FROM read_text('{(self.cur / 'teams.parquet').as_posix()}')",
            f"SELECT * FROM read_csv('{os.path.abspath(__file__)}')",
            f"SELECT * FROM glob('{self.cur.as_posix()}/*')",
            "SELECT * FROM read_text('C:/Windows/win.ini')",
            f"COPY (SELECT 1) TO '{(self.tmp / 'out.csv').as_posix()}'",
            f"COPY (SELECT 1) TO '{(self.cur / 'out.parquet').as_posix()}' (FORMAT PARQUET)",
            f"EXPORT DATABASE '{(self.tmp / 'exp').as_posix()}'",
            f"ATTACH '{(self.cur / 'nfl.duckdb').as_posix()}' AS x",
            f"ATTACH '{(self.tmp / 'new.duckdb').as_posix()}' AS y",
            "INSTALL httpfs",
            "LOAD httpfs",
        ]
        for sql in attempts:
            with pytest.raises(duckdb.Error):
                con.execute(sql)
        assert tree_listing(self.tmp) == before

    def test_the_connection_starts_with_no_tables(self, con):
        with pytest.raises(duckdb.CatalogException):
            con.execute("SELECT * FROM teams")

    def test_python_variables_are_not_tables(self, con):
        arrow_tbl = pa.table({"x": [1]})  # noqa: F841 - a replacement scan would find it
        with pytest.raises(duckdb.CatalogException):
            con.execute("SELECT * FROM arrow_tbl")

    def test_connections_share_nothing(self, tmp_path):
        sandbox = make_sandbox(tmp_path)
        a, b = sandbox.connect(), sandbox.connect()
        try:
            a.execute("CREATE TABLE leak AS SELECT 1 AS x")  # in-memory writes are harmless
            with pytest.raises(duckdb.CatalogException):
                b.execute("SELECT * FROM leak")
        finally:
            a.close()
            b.close()

    def test_run_sql_closes_its_connection_even_when_the_query_fails(self, tmp_path, monkeypatch):
        sandbox = make_sandbox(tmp_path)
        made = []
        real = sandbox.connect
        monkeypatch.setattr(sandbox, "connect", lambda: made.append(real()) or made[-1])
        run_sql(sandbox, "SELECT 1")
        with pytest.raises(Refused):
            run_sql(sandbox, "DROP TABLE x")
        with pytest.raises(QueryFailed):
            run_sql(sandbox, "SELECT 'a'::INT")
        assert len(made) == 3
        for c in made:
            with pytest.raises(duckdb.Error):
                c.execute("SELECT 1")


# ---- limits ---------------------------------------------------------------------------------


class TestLimits:
    SLOW = "SELECT count(*) FROM range(300000) a, range(300000) b WHERE (a.range + b.range) % 7 = 0"

    def test_a_slow_query_is_interrupted(self, sb):
        t0 = time.monotonic()
        e = failed(sb, self.SLOW, timeout_s=0.4)
        assert time.monotonic() - t0 < 6
        assert e.kind == "timeout" and e.code == "InterruptException" and e.hint == ""
        assert rows(sb, "SELECT 1 AS a") == [[1]]  # the next question is unaffected

    def test_a_fast_query_leaves_no_timer_running(self, sb):
        run_sql(sb, "SELECT 1", timeout_s=30)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and any(
            isinstance(t, threading.Timer) and t.is_alive() for t in threading.enumerate()
        ):
            time.sleep(0.02)
        assert not any(
            isinstance(t, threading.Timer) and t.is_alive() for t in threading.enumerate()
        )

    def test_a_failed_query_leaves_no_timer_running(self, sb):
        with pytest.raises(QueryFailed):
            run_sql(sb, "SELECT 'a'::INT", timeout_s=30)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and any(
            isinstance(t, threading.Timer) and t.is_alive() for t in threading.enumerate()
        ):
            time.sleep(0.02)
        assert not any(
            isinstance(t, threading.Timer) and t.is_alive() for t in threading.enumerate()
        )

    @pytest.mark.parametrize(
        "n, cap, truncated, kept",
        [
            (10, 3, True, 3),
            (10, 10, False, 10),
            (10, 9, True, 9),
            (10, 11, False, 10),
            (1, 1, False, 1),
        ],
    )
    def test_the_row_cap(self, sb, n, cap, truncated, kept):
        res = run_sql(sb, f"SELECT x FROM range({n}) t(x)", cap=cap)
        assert len(res.rows) == kept and res.truncated is truncated
        assert res.rows == [[i] for i in range(kept)]

    def test_the_cap_stops_a_huge_result_quickly(self, sb):
        t0 = time.monotonic()
        res = run_sql(sb, "SELECT * FROM range(1000000000000)", cap=5)
        assert len(res.rows) == 5 and res.truncated
        assert time.monotonic() - t0 < 5

    def test_the_default_cap_is_500_and_the_default_timeout_ten_seconds(self, sb):
        res = run_sql(sb, "SELECT x FROM range(1000) t(x)")
        assert len(res.rows) == 500 and res.truncated
        import inspect

        assert inspect.signature(run_sql).parameters["timeout_s"].default == 10.0

    def test_a_trailing_comment_cannot_swallow_the_cap(self, sb):
        res = run_sql(sb, "SELECT x FROM range(10) t(x) -- no newline, no limit", cap=2)
        assert len(res.rows) == 2 and res.truncated

    def test_a_memory_hog_fails_instead_of_filling_the_disk(self, sb, tmp_path):
        before = tree_listing(tmp_path)
        with pytest.raises(QueryFailed) as exc:
            run_sql(sb, "SELECT repeat('x', 100000000000)", timeout_s=5)
        assert exc.value.kind == "error"
        assert tree_listing(tmp_path) == before


# ---- values ---------------------------------------------------------------------------------


class TestValues:
    @pytest.mark.parametrize(
        "sql, expected",
        [
            ("SELECT NULL", None),
            ("SELECT TRUE", True),
            ("SELECT 7", 7),
            ("SELECT 1.5", 1.5),
            ("SELECT 'text'", "text"),
            ("SELECT DATE '2026-10-17'", "2026-10-17"),
            ("SELECT TIMESTAMP '2026-10-17 01:02:03'", "2026-10-17T01:02:03"),
            ("SELECT TIME '01:02:03'", "01:02:03"),
            ("SELECT INTERVAL 1 DAY", 86400.0),
            ("SELECT 1.5::DECIMAL(3,1)", 1.5),
            (
                "SELECT 170141183460469231731687303715884105727::HUGEINT",
                170141183460469231731687303715884105727,
            ),
            ("SELECT 'nan'::DOUBLE", None),
            ("SELECT 'inf'::DOUBLE", None),
            ("SELECT '-inf'::DOUBLE", None),
            ("SELECT [1, 2, 3]", [1, 2, 3]),
            ("SELECT [[1], [2, 3]]", [[1], [2, 3]]),
            ("SELECT ['a', NULL]", ["a", None]),
            ("SELECT 'a'::ENUM('a', 'b')", "a"),
        ],
    )
    def test_converted(self, sb, sql, expected):
        assert rows(sb, sql) == [[expected]]

    def test_a_timestamp_with_zone_is_iso_text_for_the_same_instant(self, sb):
        value = rows(sb, "SELECT TIMESTAMPTZ '2026-01-01 00:00:00+00'")[0][0]
        assert dt.datetime.fromisoformat(value) == dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

    def test_real_table_values(self, sb):
        r = rows(sb, "SELECT game_date, kickoff, tags, home_score FROM games WHERE game_id = 'g1'")[
            0
        ]
        assert r == ["2023-09-07", "2023-09-07T20:20:00", ["a", "b"], 27]

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT {'a': 1}",
            "SELECT MAP {'a': 1}",
            "SELECT 'abc'::BLOB",
            "SELECT [{'a': 1}]",
            "SELECT uuid()",
            "SELECT struct_pack(t := team) FROM teams",
            "SELECT t FROM teams t",
            "SELECT (1, 2)::STRUCT(a INT, b INT)",
            "SELECT map_from_entries([('a', 1)])",
            "SELECT [MAP {'a': 1}]",
        ],
    )
    def test_structs_maps_blobs_and_uuids_are_refused(self, sb, sql):
        assert refused(sb, sql).rule == "sql.values"


# ---- failures -------------------------------------------------------------------------------


class TestFailures:
    def test_binder_errors_have_kind_code_and_a_hint(self, sb):
        e = failed(sb, "SELECT nope FROM teams")
        assert e.kind == "error" and e.code == "BinderException"
        assert "nope" in e.hint
        assert str(e) == "error: BinderException"

    def test_a_missing_parameter_is_a_failure(self, sb):
        e = failed(sb, "SELECT $a")
        assert e.kind == "error" and e.code == "InvalidInputException"
        e = failed(sb, "SELECT $a", {"a": 1, "extra": 2})
        assert e.kind == "error"

    def test_a_secret_in_the_message_is_scrubbed_from_the_hint_and_absent_elsewhere(self, sb):
        e = failed(sb, "SELECT 'password=hunter2'::INT")
        assert e.kind == "error" and e.code == "ConversionException"
        assert "hunter2" not in e.hint
        outside = " ".join([str(e), repr(e), e.kind, e.code, *map(str, e.args)])
        assert "hunter2" not in outside and "password" not in outside

    def test_the_hint_is_capped(self, sb):
        e = failed(sb, "SELECT '" + "x" * 3000 + "'::INT")
        assert 0 < len(e.hint) <= 400

    def test_the_data_root_never_shows_in_a_hint(self, sb, tmp_path):
        e = failed(sb, "SELECT nope FROM teams")
        assert str(tmp_path) not in e.hint


# ---- reading only what a query names (D136) -------------------------------------------------


@pytest.fixture
def wide(tmp_path):
    cur = write_curated(tmp_path)
    write_wide(cur, "wide", 70, "c")
    write_wide(cur, "huge", 110, "d")
    return SqlSandbox(cur)


@pytest.fixture
def spy(monkeypatch):
    """Every `pq.read_table` the sandbox makes: (file name, columns, memory_map)."""
    calls: list[tuple[str, list[str] | None, object]] = []
    real = pq.read_table

    def read_table(source, *a, **kw):
        calls.append((os.path.basename(str(source)), kw.get("columns"), kw.get("memory_map")))
        return real(source, *a, **kw)

    monkeypatch.setattr(pq, "read_table", read_table)
    return calls


class TestColumnLoading:
    def test_only_the_named_columns_of_the_named_tables_are_read(self, wide, spy):
        assert rows(wide, "SELECT c1, c2 FROM wide ORDER BY 1 LIMIT 1") == [[1, 2]]
        assert spy == [("wide.parquet", ["c1", "c2"], False)]

    def test_files_are_read_without_memory_mapping(self, wide, spy):
        rows(wide, "SELECT team FROM wide")
        assert all(mm is False for _, _, mm in spy)

    def test_a_query_that_names_no_table_reads_nothing(self, wide, spy):
        rows(wide, "SELECT 1")
        assert spy == []

    def test_count_star_reads_the_first_column_only(self, wide, spy):
        assert rows(wide, "SELECT count(*) FROM wide") == [[3]]
        assert spy == [("wide.parquet", ["c0"], False)]

    def test_count_star_with_a_filter_and_group(self, wide):
        assert rows(wide, "SELECT team, count(*) FROM wide GROUP BY team ORDER BY team") == [
            ["BUF", 1],
            ["DAL", 1],
            ["KC", 1],
        ]
        assert rows(wide, "SELECT count(*) FILTER (WHERE c1 > 1) FROM wide") == [[2]]

    def test_the_columns_come_back_in_schema_order(self, wide):
        res = run_sql(wide, "SELECT c9, c1 FROM wide")
        assert res.columns == ["c9", "c1"]  # the query's own order
        res = run_sql(wide, "SELECT * FROM (SELECT c9, c1, team FROM wide) ORDER BY c1")
        assert res.columns == ["c9", "c1", "team"]

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM wide",
            "SELECT w.* FROM wide w",
            "SELECT wide.* FROM wide",
            "SELECT * EXCLUDE (c1) FROM wide",
            "SELECT * REPLACE (c1 + 1 AS c1) FROM wide",
            "SELECT COLUMNS(*) FROM wide",
            "SELECT COLUMNS('c1.') FROM wide",
            "SELECT COLUMNS(c -> c LIKE 'c1%') FROM wide",
            "SELECT * FROM (SELECT * FROM wide)",
            "SELECT * FROM wide LIMIT 1",
            "FROM wide",
            "FROM wide SELECT *",
            "SELECT * FROM teams NATURAL JOIN wide",
            "SELECT * FROM teams JOIN wide USING (team)",
            "SELECT * FROM teams t, wide w",
            "SELECT * FROM teams t CROSS JOIN wide w",
            "SELECT a.* FROM wide a JOIN teams b ON a.team = b.team",
            "SELECT * FROM wide UNION ALL SELECT * FROM wide",
            "SELECT * FROM wide WHERE c1 IN (SELECT c1 FROM wide)",
        ],
    )
    def test_a_star_over_a_wide_table_is_refused(self, wide, sql):
        e = refused(wide, sql)
        assert e.rule == "sql.star" and e.detail == "wide"

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM (SELECT team FROM wide)",
            "WITH a AS (SELECT team FROM wide) SELECT * FROM a",
            "WITH a AS (SELECT c1 FROM wide) SELECT * FROM a, teams",
            "SELECT team FROM wide WHERE EXISTS (SELECT * FROM teams)",
            "SELECT * FROM teams WHERE team IN (SELECT team FROM wide)",
            "SELECT * FROM teams WHERE team IN (SELECT team FROM wide WHERE c1 > 0)",
            "SELECT t.team, w.c5 FROM teams t JOIN wide w USING (team)",
            "SELECT team FROM teams JOIN wide USING (team)",
            "SELECT * FROM teams a, games b",
            "SELECT count(*) FROM wide",
            "SELECT team, count(*) FROM wide GROUP BY ALL",
            "SELECT count(*) OVER () FROM wide",
        ],
    )
    def test_a_star_that_does_not_reach_the_wide_table_is_fine(self, wide, sql):
        assert run_sql(wide, sql).row_count >= 1

    def test_a_qualified_star_covers_only_its_own_table(self, wide):
        res = run_sql(wide, "SELECT t.* FROM teams t JOIN wide w ON t.team = w.team")
        assert res.columns == ["team", "wins", "name", "win_pct"]
        res = run_sql(wide, "SELECT teams.*, w.c1 FROM teams JOIN wide w USING (team) LIMIT 1")
        assert res.columns[-1] == "c1" and len(res.columns) == 5

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT w.* FROM teams t JOIN wide w ON t.team = w.team",
            "SELECT * FROM teams t JOIN wide w ON t.team = w.team",
            "SELECT t.*, w.* FROM teams t JOIN wide w ON t.team = w.team",
            "SELECT wide.* FROM teams t JOIN wide ON t.team = wide.team",
        ],
    )
    def test_a_qualified_star_on_the_wide_table_is_still_refused(self, wide, sql):
        e = refused(wide, sql)
        assert e.rule == "sql.star" and e.detail == "wide"

    def test_a_star_over_a_narrow_table_loads_all_its_columns(self, wide, spy):
        run_sql(wide, "SELECT * FROM teams")
        assert spy == [("teams.parquet", ["team", "wins", "name", "win_pct"], False)]

    def test_wide_means_more_than_the_limit(self, tmp_path):
        cur = write_curated(tmp_path)
        write_wide(cur, "edge", WIDE_TABLE - 1, "e")  # + team = WIDE_TABLE columns
        write_wide(cur, "over", WIDE_TABLE, "o")  # + team = WIDE_TABLE + 1
        sandbox = SqlSandbox(cur)
        assert run_sql(sandbox, "SELECT * FROM edge").row_count == 3
        assert refused(sandbox, "SELECT * FROM over").rule == "sql.star"

    def test_the_column_cap(self, wide):
        names = [f"d{i}" for i in range(MAX_COLUMNS)]
        res = run_sql(wide, f"SELECT {', '.join(names)} FROM huge")
        assert res.columns == names
        e = refused(wide, f"SELECT {', '.join([*names, 'd100'])} FROM huge")
        assert e.rule == "sql.columns" and e.detail == "huge"

    def test_using_columns_are_loaded(self, wide, spy):
        res = run_sql(wide, "SELECT t.name FROM teams t JOIN wide w USING (team) ORDER BY 1")
        assert res.rows == [["Bills"], ["Chiefs"], ["Cowboys"]]
        assert ("wide.parquet", ["team"], False) in spy
        assert ("teams.parquet", ["team", "name"], False) in spy

    def test_using_with_several_columns(self, tmp_path):
        cur = write_curated(tmp_path)
        pq.write_table(pa.table({"k1": [1, 2], "k2": ["a", "b"], "v": [10, 20]}), cur / "l.parquet")
        pq.write_table(pa.table({"k1": [1, 2], "k2": ["a", "x"], "w": [5, 6]}), cur / "r.parquet")
        sandbox = SqlSandbox(cur)
        assert rows(sandbox, "SELECT v, w FROM l JOIN r USING (k1, k2)") == [[10, 5]]

    def test_natural_join_loads_every_shared_column(self, tmp_path):
        cur = write_curated(tmp_path)
        pq.write_table(pa.table({"k": [1, 2], "v": [10, 20]}), cur / "l.parquet")
        pq.write_table(pa.table({"k": [1, 2], "w": [5, 6]}), cur / "r.parquet")
        sandbox = SqlSandbox(cur)
        assert rows(sandbox, "SELECT v, w FROM l NATURAL JOIN r ORDER BY v") == [[10, 5], [20, 6]]

    def test_column_names_match_in_any_case(self, tmp_path, spy):
        cur = write_curated(tmp_path)
        pq.write_table(
            pa.table({"general__schedAdjQBR": [1.5, 2.5], "Name": ["a", "b"], "other": [1, 2]}),
            cur / "espn_qbr.parquet",
        )
        sandbox = SqlSandbox(cur)
        for sql in (
            "SELECT general__schedAdjQBR FROM espn_qbr ORDER BY 1",
            "SELECT GENERAL__SCHEDADJQBR FROM ESPN_QBR ORDER BY 1",
            'SELECT "general__schedadjqbr" FROM espn_qbr ORDER BY 1',
            "SELECT q.GeNeRaL__sChEdAdJqBr FROM espn_qbr q ORDER BY 1",
        ):
            assert rows(sandbox, sql) == [[1.5], [2.5]], sql
        assert all(cols == ["general__schedAdjQBR"] for _, cols, _ in spy)
        assert rows(sandbox, "SELECT name FROM espn_qbr ORDER BY 1") == [["a"], ["b"]]

    def test_a_misspelled_column_is_a_binder_error_not_a_crash(self, wide):
        e = failed(wide, "SELECT c1000 FROM wide")
        assert e.kind == "error" and e.code == "BinderException"

    def test_a_cte_or_alias_named_like_a_table_does_not_hide_the_real_read(self, wide):
        res = run_sql(wide, "WITH w AS (SELECT c3 FROM wide) SELECT c3 FROM w ORDER BY 1")
        assert res.rows == [[3], [4], [5]]

    def test_the_same_table_twice_is_loaded_once_with_every_column_either_side_names(
        self, wide, spy
    ):
        res = run_sql(
            wide, "SELECT a.c1, b.c2 FROM wide a JOIN wide b ON a.team = b.team ORDER BY 1 LIMIT 1"
        )
        assert res.rows == [[1, 2]]
        assert [c for n, c, _ in spy if n == "wide.parquet"] == [["c1", "c2", "team"]]

    def test_two_tables_one_read_each(self, wide, spy):
        run_sql(wide, "SELECT t.name, w.c1 FROM teams t JOIN wide w ON t.team = w.team")
        assert sorted(n for n, _, _ in spy) == ["teams.parquet", "wide.parquet"]

    # ---- the SqlPlan itself ----

    def plan(self, sandbox, sql) -> SqlPlan:
        con = sandbox.connect()
        try:
            return check_sql_plan(sql, sandbox.tables(), con)
        finally:
            con.close()

    def test_the_plan_lists_tables_columns_and_star_tables(self, wide):
        p = self.plan(wide, "SELECT t.name, W.C5 FROM teams t JOIN wide w USING (team)")
        assert p.tables == {"teams", "wide"}
        assert {"name", "c5", "team"} <= p.columns
        assert p.star_tables == set()
        p = self.plan(wide, "SELECT * FROM teams t, games g")
        assert p.star_tables == {"teams", "games"}
        p = self.plan(wide, "SELECT * FROM (SELECT team FROM teams)")
        assert p.star_tables == set() and p.tables == {"teams"}

    def test_a_cte_is_not_a_table_of_the_plan(self, wide):
        p = self.plan(wide, "WITH a AS (SELECT team FROM teams) SELECT * FROM a")
        assert p.tables == {"teams"} and p.star_tables == set()

    def test_check_sql_returns_the_text(self, wide):
        con = wide.connect()
        try:
            assert check_sql("  SELECT 1 AS a ;", wide.tables(), con) == "SELECT 1 AS a"
            with pytest.raises(Refused):
                check_sql("SELECT * FROM read_csv('x')", wide.tables(), con)
        finally:
            con.close()

    # ---- constructs that mean "every column" without naming one (BUG 5 / 6 / 8, fixed) ----

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT to_json(t) AS j FROM teams t",
            "SELECT teams::VARCHAR AS j FROM teams",
            "SELECT row_to_json(t) AS j FROM teams t",
            "SELECT CAST(t AS VARCHAR) AS j FROM teams AS t",
        ],
    )
    def test_a_table_used_as_a_struct_has_every_column(self, sb, sql):
        res = run_sql(sb, sql)
        text = str(res.rows[0][0])
        assert all(c in text for c in ("team", "wins", "name", "win_pct")), text

    def test_a_table_used_as_a_struct_is_a_star_over_it(self, wide):
        e = refused(wide, "SELECT to_json(w) AS j FROM wide w")
        assert e.rule == "sql.star" and e.detail == "wide"
        e = refused(wide, "SELECT wide::VARCHAR AS j FROM wide")
        assert e.rule == "sql.star"

    def test_a_column_named_like_the_table_is_still_a_column(self, tmp_path):
        cur = write_curated(tmp_path)
        pq.write_table(pa.table({"clubs": ["a", "b"], "n": [1, 2]}), cur / "clubs.parquet")
        sandbox = SqlSandbox(cur)
        assert rows(sandbox, "SELECT clubs FROM clubs ORDER BY 1") == [["a"], ["b"]]
        assert rows(sandbox, "SELECT count(*) FROM clubs") == [[2]]

    def test_positional_references_mean_the_table_s_columns(self, sb):
        assert rows(sb, "SELECT #1, wins FROM teams ORDER BY wins LIMIT 1") == [["DAL", 7]]
        assert rows(sb, "SELECT #2 FROM teams ORDER BY 1 LIMIT 1") == [[7]]

    def test_positional_references_over_a_wide_table_are_refused(self, wide):
        assert refused(wide, "SELECT #1 FROM wide").rule == "sql.star"

    def test_group_and_order_by_numbers_are_not_positional_references(self, wide):
        assert rows(wide, "SELECT team, count(*) FROM wide GROUP BY 1 ORDER BY 1 LIMIT 1") == [
            ["BUF", 1]
        ]

    def test_pivot_groups_by_every_other_column(self, sb):
        res = run_sql(sb, "SELECT * FROM teams PIVOT (sum(wins) FOR name IN ('Chiefs'))")
        assert len(res.rows) == 4  # one per (team, win_pct) group
        assert res.columns == ["team", "win_pct", "Chiefs"]

    def test_pivot_over_a_wide_table_is_refused(self, wide):
        assert (
            refused(wide, "SELECT * FROM wide PIVOT (sum(c1) FOR team IN ('KC'))").rule
            == "sql.star"
        )

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM teams UNPIVOT (v FOR k IN (wins, win_pct))",
            "UNPIVOT teams ON wins, win_pct INTO NAME k VALUE v",
        ],
    )
    def test_unpivot(self, sb, sql):
        res = run_sql(sb, sql)
        assert len(res.rows) == 8  # 4 teams x 2 columns

    def test_a_cte_named_like_a_real_table_is_refused(self, wide):
        e = refused(wide, "WITH wide AS (SELECT 1 AS x) SELECT * FROM wide")
        assert e.rule == "sql.table" and e.detail == "wide"
        e = refused(wide, "WITH Teams AS (SELECT 1 AS x) SELECT x FROM teams")
        assert e.rule == "sql.table" and e.detail == "teams"
        e = refused(wide, "WITH a AS (SELECT 1 AS x), games AS (SELECT 2) SELECT * FROM a")
        assert e.rule == "sql.table"

    def test_a_star_over_a_cte_with_a_fresh_name_is_fine(self, wide):
        assert rows(wide, "WITH w AS (SELECT c3 FROM wide) SELECT * FROM w ORDER BY 1") == [
            [3],
            [4],
            [5],
        ]

    def test_a_star_inside_a_scalar_subquery_belongs_to_it(self, wide):
        res = run_sql(wide, "SELECT team, (SELECT * FROM (SELECT 7 AS k)) AS k FROM wide")
        assert res.rows[0][1] == 7
        res = run_sql(
            wide, "SELECT team, (SELECT * FROM (SELECT team FROM teams LIMIT 1)) FROM wide"
        )
        assert res.row_count == 3

    def test_a_star_in_a_subquery_over_a_wide_table_is_still_refused(self, wide):
        assert refused(
            wide, "SELECT team, (SELECT max(c1) FROM (SELECT * FROM wide)) FROM wide"
        ).rule == ("sql.star")


# ---- the files: schemas, caching, mixed types, file-by-file reads ---------------------------


class TestSandboxFiles:
    def test_tables_schema_and_columns(self, sb):
        assert sb.tables() == ["games", "plays", "teams"]  # espn_news is left out
        assert sb.columns("teams") == [
            ("team", "string"),
            ("wins", "int64"),
            ("name", "string"),
            ("win_pct", "double"),
        ]
        assert sb.schema("teams").names == ["team", "wins", "name", "win_pct"]

    def test_exclude_is_case_insensitive_and_configurable(self, tmp_path):
        cur = write_curated(tmp_path)
        assert "espn_news" not in SqlSandbox(cur, exclude=("ESPN_NEWS",)).tables()
        assert "games" not in SqlSandbox(cur, exclude=("Games", "espn_news")).tables()
        assert "espn_news" in SqlSandbox(cur, exclude=()).tables()

    def test_no_plays_folder_means_no_plays_table(self, tmp_path):
        cur = write_curated(tmp_path)
        for f in (cur / "plays").iterdir():
            f.unlink()
        assert "plays" not in SqlSandbox(cur).tables()

    def test_plays_files_with_different_types_are_unified(self, sb):
        assert dict(sb.columns("plays"))["goal_to_go"] == "double"
        res = run_sql(sb, "SELECT season, goal_to_go, epa FROM plays ORDER BY season, epa")
        assert res.rows == [[2020, 0.0, 0.1], [2020, 1.0, 0.2], [2021, 1.0, 0.3]]
        assert rows(sb, "SELECT sum(goal_to_go) FROM plays") == [[2.0]]
        assert rows(sb, "SELECT count(*) FROM plays WHERE goal_to_go = 1") == [[2]]

    def test_load_casts_every_file_to_the_unified_schema(self, sb):
        t = sb.load("plays", ["season", "goal_to_go"])
        assert t.schema.field("goal_to_go").type == pa.float64()
        assert t.num_rows == 3 and t.column_names == ["season", "goal_to_go"]

    def test_a_season_file_without_a_column_gets_nulls_and_keeps_its_rows(self, tmp_path):
        cur = write_curated(tmp_path)
        pq.write_table(
            pa.table({"season": [2022, 2022, 2022], "epa": [0.5, 0.6, 0.7]}),
            cur / "plays" / "season=2022.parquet",
        )
        sandbox = SqlSandbox(cur)
        assert rows(sandbox, "SELECT count(*) FROM plays") == [[6]]
        assert rows(sandbox, "SELECT count(*) FROM plays WHERE goal_to_go IS NULL") == [[3]]
        assert rows(sandbox, "SELECT sum(epa) FROM plays WHERE season = 2022")[0][
            0
        ] == pytest.approx(1.8)
        # a query that names only a column the 2022 file lacks still sees its three rows
        assert rows(
            sandbox, "SELECT count(*) FROM plays WHERE goal_to_go IS NULL OR goal_to_go >= 0"
        ) == [[6]]
        assert rows(sandbox, "SELECT goal_to_go FROM plays WHERE season = 2022 LIMIT 1") == [[None]]

    def test_the_schema_is_cached_until_a_file_changes(self, sb):
        first = sb.schema("teams")
        assert sb.schema("teams") is first
        pq.write_table(
            pa.table({"team": ["KC"], "wins": [1], "name": ["x"], "win_pct": [1.0], "extra": [1]}),
            sb.curated / "teams.parquet",
        )
        assert sb.schema("teams") is not first
        assert "extra" in sb.schema("teams").names
        assert rows(sb, "SELECT extra FROM teams") == [[1]]

    def test_a_rewritten_file_is_picked_up_by_the_next_question(self, sb):
        assert rows(sb, "SELECT count(*) FROM teams") == [[4]]
        pq.write_table(
            pa.table({"team": ["KC"], "wins": [1], "name": ["x"], "win_pct": [1.0]}),
            sb.curated / "teams.parquet",
        )
        assert rows(sb, "SELECT count(*) FROM teams") == [[1]]

    def test_files_added_and_removed_change_the_tables(self, sb):
        pq.write_table(pa.table({"a": [1]}), sb.curated / "rosters.parquet")
        pq.write_table(
            pa.table({"season": [2022], "goal_to_go": [1.0], "epa": [0.9]}),
            sb.curated / "plays" / "season=2022.parquet",
        )
        assert "rosters" in sb.tables()
        assert rows(sb, "SELECT count(*) FROM plays") == [[4]]
        (sb.curated / "rosters.parquet").unlink()
        (sb.curated / "plays" / "season=2020.parquet").unlink()
        assert "rosters" not in sb.tables()
        assert rows(sb, "SELECT count(*) FROM plays") == [[2]]

    def test_every_table_is_read_with_each_file_closed_before_the_next(self, sb, monkeypatch):
        """`load` reads one file at a time (the open handle is what blocks a replace)."""
        opened: list[str] = []
        real = pq.read_table

        def read_table(source, *a, **kw):
            assert len(opened) == 0 or True
            t = real(source, *a, **kw)
            # the file can be replaced right now: nothing of ours holds it
            tmp = source.with_name(source.name + ".tmp")
            tmp.write_bytes(source.read_bytes())
            replace_file(tmp, source, tries=1)
            opened.append(source.name)
            return t

        monkeypatch.setattr(pq, "read_table", read_table)
        sb.load("plays", ["season", "epa"])
        assert opened == ["season=2020.parquet", "season=2021.parquet"]

    def test_the_idle_sandbox_holds_no_file(self, sb):
        run_sql(sb, "SELECT * FROM teams")
        run_sql(sb, "SELECT * FROM plays")
        for p in (sb.curated / "teams.parquet", *(sb.curated / "plays").glob("*.parquet")):
            tmp = p.with_name(p.name + ".new")
            tmp.write_bytes(p.read_bytes())
            replace_file(tmp, p, tries=1)  # no PermissionError
        p = sb.curated / "teams.parquet"
        p.unlink()  # nor can it block a delete
        assert not p.exists()


# ---- D136: the curate step's replace during a running question ------------------------------

N_BIG = 1_500_000
SLOW_BIG = "SELECT count(*) FROM big WHERE list_sum(list_transform(range(500), z -> z * x)) > 0"


@pytest.fixture(scope="module")
def big_parquet(tmp_path_factory):
    import numpy as np

    path = tmp_path_factory.mktemp("bigsrc") / "big.parquet"
    pq.write_table(
        pa.table(
            {"x": np.arange(N_BIG, dtype="int64"), "y": np.random.default_rng(1).random(N_BIG)}
        ),
        path,
        row_group_size=50_000,
    )
    return path


def _big_folder(tmp_path, big_parquet):
    import shutil

    cur = tmp_path / "curated"
    cur.mkdir()
    shutil.copy(big_parquet, cur / "big.parquet")
    return cur


class TestReplaceDuringQuery:
    @pytest.mark.parametrize("delay", [0.0, 0.05, 0.4, 1.0])
    def test_replace_file_goes_through_while_a_slow_question_runs(
        self, tmp_path, big_parquet, delay
    ):
        cur = _big_folder(tmp_path, big_parquet)
        sandbox = SqlSandbox(cur)
        out: dict = {}

        def ask():
            try:
                out["res"] = run_sql(sandbox, SLOW_BIG, timeout_s=15)
            except Exception as e:  # noqa: BLE001
                out["err"] = e

        th = threading.Thread(target=ask)
        th.start()
        time.sleep(delay)
        tmp = cur / "big.tmp"
        pq.write_table(pa.table({"x": list(range(10)), "y": [0.5] * 10}), tmp)
        t0 = time.monotonic()
        replace_file(tmp, cur / "big.parquet")  # the pipeline's own call: 2 s of retries
        waited = time.monotonic() - t0
        th.join(timeout=60)
        assert "err" not in out, out.get("err")
        assert out["res"].rows[0][0] in (N_BIG - 1, 9)  # the old file's count, or the new one's
        assert waited < 2.0  # never the full retry budget

    def test_the_replaced_file_is_what_the_next_question_reads(self, tmp_path, big_parquet):
        cur = _big_folder(tmp_path, big_parquet)
        sandbox = SqlSandbox(cur)
        assert rows(sandbox, "SELECT count(*) FROM big") == [[N_BIG]]
        tmp = cur / "big.tmp"
        pq.write_table(pa.table({"x": [1, 2], "y": [0.1, 0.2]}), tmp)
        replace_file(tmp, cur / "big.parquet", tries=1)
        assert rows(sandbox, "SELECT count(*) FROM big") == [[2]]

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing")
    def test_control_a_native_view_does_block_the_replace(self, tmp_path, big_parquet):
        """If this ever stops failing, the replace tests above no longer prove anything."""
        cur = _big_folder(tmp_path, big_parquet)
        con = duckdb.connect()
        con.execute(
            f"CREATE VIEW big AS SELECT * FROM read_parquet('{(cur / 'big.parquet').as_posix()}')"
        )
        th = threading.Thread(target=lambda: _quiet(lambda: con.execute(SLOW_BIG).fetchall()))
        th.start()
        try:
            time.sleep(0.4)
            tmp = cur / "big.tmp"
            pq.write_table(pa.table({"x": [1], "y": [0.1]}), tmp)
            with pytest.raises(PermissionError):
                replace_file(tmp, cur / "big.parquet", tries=1)
        finally:
            con.interrupt()
            th.join(timeout=30)
            con.close()


def _quiet(fn):
    with contextlib.suppress(Exception):  # the control query is interrupted on purpose
        fn()


# ---- Sources: the SQL half, the weekly lock --------------------------------------------------


class TestSources:
    def test_sql_runs_through_the_guard_with_the_configured_limits(self, sb):
        src = Sources(sandbox=sb, driver=None, timeout_s=0.4, cap=2)
        res = src.run("sql", "SELECT team FROM teams ORDER BY team")
        assert [r[0] for r in res.rows] == ["BUF", "DAL"] and res.truncated
        with pytest.raises(QueryFailed) as exc:
            src.run("sql", TestLimits.SLOW)
        assert exc.value.kind == "timeout"

    def test_a_refusal_comes_through_unchanged(self, sb):
        src = Sources(sandbox=sb, driver=None)
        with pytest.raises(Refused) as exc:
            src.run("sql", "DROP TABLE teams")
        assert exc.value.rule == "sql.not_select"

    def test_busy_while_the_weekly_lock_is_held(self, sb, tmp_path):
        lock = tmp_path / "runs" / ".weekly.lock"
        driver = FakeDriver()
        src = Sources(sandbox=sb, driver=driver, lock_file=lock)
        assert not src.busy()
        assert src.run("sql", "SELECT 1 AS a").rows == [[1]]
        with run_lock(lock, "nfl weekly run"):
            assert src.busy()
            for source, query in (("sql", "SELECT 1"), ("graph", "MATCH (n) RETURN n.x")):
                with pytest.raises(Refused) as exc:
                    src.run(source, query)
                assert exc.value.rule == "source.busy"
            assert driver.sessions == []  # nothing reached Neo4j
        assert not src.busy()
        assert src.run("sql", "SELECT 2 AS a").rows == [[2]]
        assert src.run("graph", "MATCH (n) RETURN n.x AS x").rows  # and the graph again

    def test_busy_is_checked_before_the_source_is_looked_at(self, tmp_path):
        lock = tmp_path / ".weekly.lock"
        src = Sources(sandbox=None, driver=None, lock_file=lock)
        with run_lock(lock, "x"):
            with pytest.raises(Refused) as exc:
                src.run("sql", "SELECT 1")
            assert exc.value.rule == "source.busy"
        with pytest.raises(Refused) as exc:
            src.run("sql", "SELECT 1")
        assert exc.value.rule == "source.off"

    def test_no_lock_file_is_never_busy(self, sb):
        assert Sources(sandbox=sb, driver=None).busy() is False

    def test_a_missing_or_stale_lock_file_is_not_busy(self, sb, tmp_path):
        lock = tmp_path / "nope" / ".weekly.lock"
        assert Sources(sandbox=sb, driver=None, lock_file=lock).busy() is False
        lock.parent.mkdir()
        lock.write_text('{"pid": 1, "command": "a run that died"}')  # a note, no OS lock
        assert Sources(sandbox=sb, driver=None, lock_file=lock).busy() is False

    def test_an_unreadable_lock_file_is_not_busy(self, sb, tmp_path, monkeypatch):
        import nflengine.ops.lock as L

        def boom(path):
            raise PermissionError("denied")

        monkeypatch.setattr(L, "is_locked", boom)
        assert Sources(sandbox=sb, driver=None, lock_file=tmp_path / "x").busy() is False

    def test_open_builds_the_sandbox_the_limits_and_the_lock_path(self, tmp_path):
        cur = write_curated(tmp_path)
        paths = SimpleNamespace(curated=cur, runs=tmp_path / "runs")
        cfg = AskConfig.model_validate(
            {
                "limits": {"rows": 7, "timeout_s": 3.5},
                "sql": {"exclude_tables": ["games"], "memory_limit": "1GB", "threads": 2},
                "graph": {"database": "weekly"},
            }
        )
        drv = FakeDriver()
        src = Sources.open(cfg, paths, driver=drv)
        assert src.driver is drv and src.database == "weekly"
        assert src.cap == 7 and src.timeout_s == 3.5
        assert src.lock_file == tmp_path / "runs" / ".weekly.lock"
        assert src.sandbox.exclude == {"games"} and src.sandbox.memory_limit == "1GB"
        assert src.sandbox.threads == 2
        assert "games" not in src.sandbox.tables()
        assert src.enabled == {"graph": True, "sql": True, "history": False}

    def test_open_with_sources_switched_off(self, tmp_path):
        paths = SimpleNamespace(curated=write_curated(tmp_path), runs=tmp_path / "runs")
        cfg = AskConfig.model_validate({"sources": {"graph": False, "sql": False}})
        src = Sources.open(cfg, paths)
        assert src.sandbox is None and src.driver is None
        for source in ("sql", "graph"):
            with pytest.raises(Refused) as exc:
                src.run(source, "SELECT 1")
            assert exc.value.rule == "source.off"

    def test_open_survives_a_graph_that_cannot_connect(self, tmp_path, monkeypatch):
        import nflengine.graph.client as client

        def no_graph(**kw):
            raise RuntimeError("no password")

        monkeypatch.setattr(client, "get_driver", no_graph)
        paths = SimpleNamespace(curated=write_curated(tmp_path), runs=tmp_path / "runs")
        src = Sources.open(AskConfig(), paths)
        assert src.driver is None and src.sandbox is not None
        assert src.run("sql", "SELECT 1 AS a").rows == [[1]]


# ---- statements, alias lists and the one deadline (Sol review) ---------------------------------


class TestStatementsAndAliases:
    def test_a_second_statement_is_refused_before_anything_is_parsed(
        self, sb, tmp_path, monkeypatch
    ):
        """`SELECT 1; IMPORT DATABASE '<dir>'` made DuckDB's module-level parser read the folder."""
        d = tmp_path / "db"
        d.mkdir()
        (d / "schema.sql").write_text("SELECT 42 AS x;\n")
        (d / "load.sql").write_text("")

        def boom(*a, **k):
            raise AssertionError("the module-level parser was used")

        monkeypatch.setattr(duckdb, "extract_statements", boom)
        e = refused(sb, f"SELECT 1; IMPORT DATABASE '{d.as_posix()}'")
        assert e.rule == "sql.multiple_statements"
        assert refused(sb, "SELECT 1; SELECT 2").rule == "sql.multiple_statements"
        assert refused(sb, "SELECT 1; -- c\nSELECT 2").rule == "sql.multiple_statements"
        assert refused(sb, "SELECT 1;\n/* c */ DROP TABLE teams").rule == "sql.multiple_statements"
        # the statements that are fine still parse, on the sandbox's own connection
        assert rows(sb, "SELECT team FROM teams ORDER BY team LIMIT 1") == [["BUF"]]

    @pytest.mark.parametrize(
        "tail",
        [";", " ;", ";;", "; ;", "; -- c", ";/* c */", "; /* c */ ;", ";\n-- a\n;", " ; ; -- x"],
    )
    def test_trailing_semicolons_and_comments_are_fine(self, sb, tail):
        res = run_sql(sb, "SELECT 1 AS a" + tail)
        assert res.rows == [[1]] and res.query == "SELECT 1 AS a"

    def test_a_semicolon_in_a_string_or_comment_is_not_a_second_statement(self, sb):
        assert rows(sb, "SELECT 'a;b' AS s; ") == [["a;b"]]
        assert rows(sb, "SELECT 1 AS a -- ; DROP TABLE teams") == [[1]]
        assert rows(sb, "SELECT 1 AS a /* ; DROP TABLE teams */") == [[1]]

    def test_a_column_alias_list_renames_by_position_so_the_table_is_loaded_whole(self, sb):
        res = run_sql(sb, "SELECT a, b FROM teams AS t(a, b) ORDER BY a")
        assert res.columns == ["a", "b"]
        assert res.rows[:2] == [["BUF", 11], ["DAL", 7]]  # the first two columns, renamed
        assert rows(sb, "SELECT a FROM teams t(a) ORDER BY a LIMIT 1") == [["BUF"]]

    def test_an_alias_list_over_a_wide_table_is_a_star(self, wide):
        e = refused(wide, "SELECT a FROM wide AS w(a)")
        assert e.rule == "sql.star" and e.detail == "wide"

    def test_an_alias_without_a_list_is_not(self, wide):
        assert rows(wide, "SELECT w.c1 FROM wide AS w ORDER BY 1 LIMIT 1") == [[1]]

    def test_describe_in_a_subquery_is_refused_too(self, sb):
        for sql in (
            "SELECT * FROM (DESCRIBE teams)",
            "SELECT * FROM (SHOW TABLES)",
            "SELECT * FROM (SUMMARIZE teams)",
        ):
            assert refused(sb, sql).rule == "sql.parse", sql


class TestOneDeadline:
    SLOW_JOIN = (
        "SELECT count(*) FROM teams, range(300000) a, range(300000) b "
        "WHERE (a.range + b.range + wins) % 7 = 0"
    )

    @pytest.fixture
    def slow_reads(self, monkeypatch):
        """Every file read takes `delay` seconds (read_table is wrapped)."""
        calls: list[str] = []
        real = pq.read_table
        delay = [0.0]

        def read_table(source, *a, **kw):
            calls.append(os.path.basename(str(source)))
            time.sleep(delay[0])
            return real(source, *a, **kw)

        monkeypatch.setattr(pq, "read_table", read_table)
        return SimpleNamespace(calls=calls, delay=delay)

    @pytest.mark.parametrize("budget", [0, -1, 0.0])
    def test_no_budget_is_a_timeout_before_anything_runs(self, sb, budget):
        for sql in ("SELECT 1", "SELECT team FROM teams"):
            e = failed(sb, sql, timeout_s=budget)
            assert e.kind == "timeout" and e.code == "deadline" and e.hint == ""

    def test_load_with_a_deadline_in_the_past_reads_nothing(self, sb, slow_reads):
        with pytest.raises(QueryFailed) as exc:
            sb.load("teams", ["team"], deadline=time.monotonic() - 1)
        assert exc.value.kind == "timeout" and exc.value.code == "deadline"
        assert slow_reads.calls == []

    def test_load_without_a_deadline_is_unbounded(self, sb):
        assert sb.load("teams", ["team"]).num_rows == 4
        assert sb.load("teams", ["team"], deadline=None).num_rows == 4
        assert sb.load("teams", ["team"], deadline=time.monotonic() + 60).num_rows == 4

    def test_loading_stops_between_files_when_the_budget_is_spent(self, sb, slow_reads):
        slow_reads.delay[0] = 0.25  # plays has two files; the budget is gone after the first
        e = failed(sb, "SELECT season FROM plays", timeout_s=0.1)
        assert e.kind == "timeout" and e.code == "deadline"
        assert slow_reads.calls == ["season=2020.parquet"]

    def test_the_interrupt_gets_only_what_the_loading_left(self, sb, slow_reads):
        slow_reads.delay[0] = 0.7
        t0 = time.monotonic()
        e = failed(sb, self.SLOW_JOIN, timeout_s=1.0)
        elapsed = time.monotonic() - t0
        assert e.kind == "timeout"
        assert elapsed < 1.6, elapsed  # not 0.7 s of loading plus a fresh 1.0 s

    def test_a_fast_query_within_the_budget_is_untouched(self, sb, slow_reads):
        slow_reads.delay[0] = 0.05
        assert run_sql(sb, "SELECT count(*) FROM plays", timeout_s=5).rows == [[3]]
        assert slow_reads.calls == ["season=2020.parquet", "season=2021.parquet"]


class TestSchemaCacheKey:
    def test_a_rewrite_of_the_same_size_with_a_new_mtime_is_picked_up(self, tmp_path):
        cur = write_curated(tmp_path)
        path = cur / "t.parquet"
        pq.write_table(pa.table({"abc": [1, 2]}), path)
        sandbox = SqlSandbox(cur)
        assert sandbox.schema("t").names == ["abc"]
        size = path.stat().st_size
        pq.write_table(pa.table({"abd": [1, 2]}), path)
        assert path.stat().st_size == size  # the same size: only the mtime tells
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        assert sandbox.schema("t").names == ["abd"]
        assert rows(sandbox, "SELECT abd FROM t ORDER BY 1") == [[1], [2]]
