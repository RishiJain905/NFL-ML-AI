"""The guard's result-size bounds (AE01, Sol's review): one huge cell, list or result is refused
with `result.size` before the Python work after the query (the timer stops the query only)."""

from __future__ import annotations

import time

import pytest
from ask_fakes import FakeDriver, make_sandbox

from nflengine.ask.guard import (
    MAX_CELL_CHARS,
    MAX_CELL_ITEMS,
    MAX_RESULT_COLUMNS,
    Refused,
    run_cypher,
    run_sql,
)


@pytest.fixture
def sb(tmp_path):
    return make_sandbox(tmp_path)


def refused(fn, *a, **kw) -> str:
    with pytest.raises(Refused) as e:
        fn(*a, **kw)
    return e.value.rule


class TestSql:
    def test_a_ten_million_item_list_is_refused_inside_duckdb(self, sb):
        t0 = time.monotonic()
        assert refused(run_sql, sb, "SELECT list(i) AS xs FROM range(10000000) t(i)") == (
            "result.size"
        )
        assert time.monotonic() - t0 < 5

    def test_a_list_at_the_bound_passes_one_over_it_is_refused(self, sb):
        ok = run_sql(sb, f"SELECT list(i) AS xs FROM range({MAX_CELL_ITEMS}) t(i)")
        assert len(ok.rows[0][0]) == MAX_CELL_ITEMS
        q = f"SELECT list(i) AS xs FROM range({MAX_CELL_ITEMS + 1}) t(i)"
        assert refused(run_sql, sb, q) == "result.size"

    def test_nested_lists_count_every_level(self, sb):
        q = f"SELECT [list(i)] AS xss FROM range({MAX_CELL_ITEMS + 1}) t(i)"
        assert refused(run_sql, sb, q) == "result.size"
        assert run_sql(sb, "SELECT [[1, 2], [3]] AS xss").rows == [[[[1, 2], [3]]]]

    def test_a_list_whose_text_is_too_long_is_refused(self, sb):
        q = f"SELECT [repeat('a', {MAX_CELL_CHARS})] AS xs"
        assert refused(run_sql, sb, q) == "result.size"

    def test_a_text_cell_at_the_bound_passes_one_over_it_is_refused(self, sb):
        assert len(run_sql(sb, f"SELECT repeat('a', {MAX_CELL_CHARS}) AS x").rows[0][0]) == (
            MAX_CELL_CHARS
        )
        q = f"SELECT repeat('a', {MAX_CELL_CHARS + 1}) AS x"
        assert refused(run_sql, sb, q) == "result.size"

    def test_the_whole_result_has_a_budget(self, sb):
        q = f"SELECT repeat('ab', {MAX_CELL_CHARS // 2}) AS x FROM range(400)"  # 8M characters
        assert refused(run_sql, sb, q) == "result.size"

    def test_too_many_columns(self, sb):
        cols = ", ".join(f"{i} AS c{i}" for i in range(MAX_RESULT_COLUMNS + 1))
        assert refused(run_sql, sb, f"SELECT {cols}") == "result.size"

    def test_structs_and_maps_are_refused_before_fetching(self, sb):
        assert refused(run_sql, sb, "SELECT {'a': 1} AS st") == "sql.values"
        assert refused(run_sql, sb, "SELECT MAP {'a': 1} AS m") == "sql.values"
        assert refused(run_sql, sb, "SELECT [{'a': 1}] AS ls") == "sql.values"

    def test_ordinary_results_are_unchanged(self, sb):
        r = run_sql(
            sb,
            "SELECT 1 AS a, 2 AS a, NULL::INTEGER[] AS n, [1.5, NULL] AS f, "
            "DATE '2024-01-01' AS d, 'x' AS s",
        )
        assert r.columns == ["a", "a_1", "n", "f", "d", "s"]
        assert r.rows == [[1, 2, None, [1.5, None], "2024-01-01", "x"]]

    def test_a_column_name_with_quotes_survives(self, sb):
        r = run_sql(sb, 'SELECT 1 AS "say ""hi"""')
        assert r.columns == ['say "hi"'] and r.rows == [[1]]


class TestSqlTypes:
    """Sol's verification (AE01): types read from DuckDB's structured form, not its text."""

    def test_a_variant_column_is_refused_before_fetching(self, sb):
        assert refused(run_sql, sb, "SELECT range(10000000)::VARIANT AS xs") == "sql.values"

    def test_many_empty_nested_lists_count_as_items(self, sb):
        q = f"SELECT list([]::INTEGER[]) AS xs FROM range({MAX_CELL_ITEMS + 1})"
        assert refused(run_sql, sb, q) == "result.size"

    def test_an_enum_label_that_looks_like_a_type_is_a_plain_value(self, sb):
        r = run_sql(
            sb, "SELECT 'STRUCT('::ENUM('STRUCT(', 'x[]') AS label, 'x[]'::ENUM('x[]') AS b"
        )
        assert r.rows == [["STRUCT(", "x[]"]]

    def test_fixed_size_arrays(self, sb):
        assert run_sql(sb, "SELECT [1, 2, 3]::INTEGER[3] AS a").rows == [[[1, 2, 3]]]
        q = f"SELECT list_value(1)::INTEGER[]::INTEGER[{MAX_CELL_ITEMS + 1}] AS a"
        with pytest.raises((Refused, Exception)):
            run_sql(sb, q)

    def test_uuid_and_blob_columns_are_refused(self, sb):
        assert refused(run_sql, sb, "SELECT gen_random_uuid() AS u") == "sql.values"
        assert refused(run_sql, sb, "SELECT 'a'::BLOB AS b") == "sql.values"


class TestCypher:
    def test_a_huge_list_is_refused_before_it_is_converted(self):
        drv = FakeDriver(keys=["xs"], records=[[list(range(MAX_CELL_ITEMS + 1))]])
        assert refused(run_cypher, drv, "MATCH (n) RETURN n.xs AS xs") == "result.size"

    def test_a_list_at_the_bound_passes(self):
        drv = FakeDriver(keys=["xs"], records=[[list(range(MAX_CELL_ITEMS))]])
        assert len(run_cypher(drv, "MATCH (n) RETURN n.xs AS xs").rows[0][0]) == MAX_CELL_ITEMS

    def test_a_long_text_cell_is_refused(self):
        drv = FakeDriver(keys=["x"], records=[["a" * (MAX_CELL_CHARS + 1)]])
        assert refused(run_cypher, drv, "MATCH (n) RETURN n.x AS x") == "result.size"

    def test_too_many_columns(self):
        keys = [f"c{i}" for i in range(MAX_RESULT_COLUMNS + 1)]
        drv = FakeDriver(keys=keys, records=[list(range(len(keys)))])
        assert refused(run_cypher, drv, "MATCH (n) RETURN n.x AS x") == "result.size"
