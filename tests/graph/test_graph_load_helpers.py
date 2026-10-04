"""Neo4j loader helpers (P05): everything in `graph/load.py` that runs without a server.

Pure helpers are checked directly; the writers (`wipe`, `apply_schema`, `load_tables`,
`graph_counts`) run against a fake driver that records every query and its parameters.
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl
import pytest
from graph_world import KEY, FakeDriver

from nflengine.graph import load
from nflengine.graph import tables as T

# ---- schema.cypher ------------------------------------------------------------------------------


def non_comment_text(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(ln for ln in lines if not ln.strip().startswith("//"))


def test_schema_statements_are_every_statement_of_the_file_without_comments() -> None:
    stmts = load.schema_statements()
    assert stmts
    text = non_comment_text(load.SCHEMA_PATH)
    assert text.count("CREATE ") == len(stmts)
    # same text, statement by statement, comments gone, separators gone
    assert " ".join("; ".join(stmts).split()) == " ".join(text.split()).rstrip(";")
    for s in stmts:
        assert s.startswith("CREATE ") and "//" not in s and not s.endswith(";")
        assert "IF NOT EXISTS" in s  # applied before every load, so each must be idempotent


def test_schema_statements_split_and_comment_handling(tmp_path: Path) -> None:
    f = tmp_path / "s.cypher"
    f.write_text(
        "// header; with a semicolon\n"
        "CREATE CONSTRAINT a IF NOT EXISTS FOR (t:Team) REQUIRE t.team_id IS UNIQUE;\n"
        "\n"
        "   // an indented comment\n"
        "CREATE INDEX b IF NOT EXISTS\n"
        "  FOR (g:Game) ON (g.season, g.week);\n"
        "CREATE INDEX c IF NOT EXISTS FOR (p:Player) ON (p.name)\n",  # no final semicolon
        encoding="utf-8",
    )
    assert load.schema_statements(f) == [
        "CREATE CONSTRAINT a IF NOT EXISTS FOR (t:Team) REQUIRE t.team_id IS UNIQUE",
        "CREATE INDEX b IF NOT EXISTS\n  FOR (g:Game) ON (g.season, g.week)",
        "CREATE INDEX c IF NOT EXISTS FOR (p:Player) ON (p.name)",
    ]
    f.write_text("// nothing but comments;\n\n", encoding="utf-8")
    assert load.schema_statements(f) == []


def test_every_node_label_has_a_unique_constraint_on_its_key() -> None:
    stmts = load.schema_statements()
    for label, key in T.NODE_KEYS.items():
        pattern = (
            rf"CREATE CONSTRAINT \w+ IF NOT EXISTS FOR \((\w+):{label}\) "
            rf"REQUIRE \1\.{key} IS UNIQUE"
        )
        assert any(re.fullmatch(pattern, s) for s in stmts), f"no constraint for {label}.{key}"


def test_schema_indexes_name_known_relationship_types() -> None:
    rel_indexes = [s for s in load.schema_statements() if "FOR ()-[" in s]
    assert rel_indexes
    for s in rel_indexes:
        assert re.search(r"\[\w+:(\w+)\]", s).group(1) in T.REL_ENDS  # type: ignore[union-attr]


def test_apply_schema_runs_each_statement_then_waits_for_the_indexes() -> None:
    driver = FakeDriver()
    n = load.apply_schema(driver)
    stmts = load.schema_statements()
    assert n == len(stmts)
    assert [q for q, _ in driver.calls] == [*stmts, "CALL db.awaitIndexes(300)"]


# ---- wipe ---------------------------------------------------------------------------------------


def test_wipe_deletes_relationships_then_nodes_in_batches() -> None:
    driver = FakeDriver()
    load.wipe(driver)
    assert len(driver.calls) == 2
    (q1, p1), (q2, p2) = driver.calls
    assert q1.startswith("MATCH ()-[r]->()") and "DELETE r" in q1 and "IN TRANSACTIONS" in q1
    assert q2.startswith("MATCH (n)") and "DETACH DELETE n" in q2 and "IN TRANSACTIONS" in q2
    assert p1 == p2 == {"n": load.REL_BATCH}
    load.wipe(driver, batch=500)
    assert driver.calls[-1][1] == {"n": 500}


# ---- query text ---------------------------------------------------------------------------------


@pytest.mark.parametrize("label", T.NODE_ORDER)
def test_node_query_creates_the_label_from_the_row_map(label: str) -> None:
    assert load.node_query(label) == f"UNWIND $rows AS r CREATE (n:{label}) SET n = r"


# relationship -> (start label, start key, end label, end key)
REL_KEYS = {
    "PLAYED_FOR": ("Player", "player_id", "Team", "team_id"),
    "APPEARED_IN": ("Player", "player_id", "Game", "game_id"),
    "PLAYED_IN": ("Team", "team_id", "Game", "game_id"),
    "AT": ("Game", "game_id", "Venue", "stadium_id"),
    "HEAD_COACH_OF": ("Coach", "coach_id", "Team", "team_id"),
    "COACHED_IN": ("Coach", "coach_id", "Game", "game_id"),
    "OFFICIATED": ("Official", "official_id", "Game", "game_id"),
    "THREW_TO": ("Player", "player_id", "Player", "player_id"),
    "ON_INJURY_REPORT": ("Player", "player_id", "Game", "game_id"),
    "DEPTH_CHART": ("Player", "player_id", "Team", "team_id"),
    "DRAFTED_BY": ("Player", "player_id", "Team", "team_id"),
    "TRADED_TO": ("Player", "player_id", "Team", "team_id"),
    "HAS_WEEK": ("Team", "team_id", "TeamWeek", "key"),
    "NEXT": ("TeamWeek", "key", "TeamWeek", "key"),
    "HAS_PREDICTION": ("Game", "game_id", "GamePrediction", "key"),
}


def test_the_expected_relationship_table_covers_every_relationship() -> None:
    assert set(REL_KEYS) == set(T.REL_ENDS)


@pytest.mark.parametrize("rel", list(REL_KEYS))
def test_rel_query_matches_both_ends_by_their_keys(rel: str) -> None:
    start, sk, end, ek = REL_KEYS[rel]
    assert load.rel_query(rel) == (
        f"UNWIND $rows AS r "
        f"MATCH (a:{start} {{{sk}: r.s}}) MATCH (b:{end} {{{ek}: r.e}}) "
        f"CREATE (a)-[x:{rel}]->(b) SET x = r.p"
    )


def test_rel_query_for_an_unknown_relationship() -> None:
    with pytest.raises(KeyError):
        load.rel_query("FOLLOWS")


# ---- rows ---------------------------------------------------------------------------------------


def test_rows_turns_nan_into_null_and_keeps_everything_else() -> None:
    df = pl.DataFrame(
        {
            "id": ["a", "b", "c"],
            "x": [1.5, float("nan"), None],
            "n": [1, None, 3],
            "ok": [True, False, None],
            "tags": [["KC"], [], None],
        }
    )
    assert load.rows(df) == [
        {"id": "a", "x": 1.5, "n": 1, "ok": True, "tags": ["KC"]},
        {"id": "b", "x": None, "n": None, "ok": False, "tags": []},
        {"id": "c", "x": None, "n": 3, "ok": None, "tags": None},
    ]


def test_rows_keeps_infinity_and_empty_frames_give_no_rows() -> None:
    assert load.rows(pl.DataFrame({"x": [float("inf")]})) == [{"x": float("inf")}]
    assert load.rows(pl.DataFrame({"x": []})) == []


def test_rel_rows_split_ends_from_properties() -> None:
    df = pl.DataFrame({"s": ["p1", "p2"], "e": ["KC", "LV"], "season": [2026, 2025]})
    assert load.rel_rows(df) == [
        {"s": "p1", "e": "KC", "p": {"season": 2026}},
        {"s": "p2", "e": "LV", "p": {"season": 2025}},
    ]


def test_rel_rows_drop_null_properties_and_clean_nan() -> None:
    df = pl.DataFrame(
        {
            "s": ["p1", "p2"],
            "e": ["KC", "KC"],
            "weeks": [3, None],
            "epa": [float("nan"), 0.5],
            "status": [None, "RES"],
        }
    )
    got = load.rel_rows(df)
    assert got[0] == {"s": "p1", "e": "KC", "p": {"weeks": 3, "epa": None}}  # NaN -> null, not NaN
    assert got[1] == {"s": "p2", "e": "KC", "p": {"epa": 0.5, "status": "RES"}}  # nulls dropped
    assert "status" not in got[0]["p"] and "weeks" not in got[1]["p"]


def test_rel_rows_with_only_ends_have_empty_properties() -> None:
    df = pl.DataFrame({"s": ["a"], "e": ["b"]})
    assert load.rel_rows(df) == [{"s": "a", "e": "b", "p": {}}]
    assert load.rel_rows(pl.DataFrame({"s": [], "e": []})) == []


def test_batches() -> None:
    items = [{"i": i} for i in range(25)]
    sizes = [len(b) for b in load.batches(items, 10)]
    assert sizes == [10, 10, 5]
    assert [b for chunk in load.batches(items, 10) for b in chunk] == items  # in order, no loss
    assert [len(b) for b in load.batches(items[:20], 10)] == [10, 10]
    assert list(load.batches([], 10)) == []
    assert [len(b) for b in load.batches(items[:3], 100)] == [3]


# ---- load_tables --------------------------------------------------------------------------------


def small_tables() -> T.GraphTables:
    gt = T.GraphTables(KEY)
    # inserted out of order on purpose: the loader follows NODE_ORDER / REL_ORDER
    gt.rels = {
        "NEXT": pl.DataFrame({"s": ["KC:2026:1", "KC:2026:2"], "e": ["KC:2026:2", "KC:2026:3"]}),
        "PLAYED_FOR": pl.DataFrame(
            {
                "s": ["p1", "p2", "p3", "p4", "p5"],
                "e": ["KC", "KC", "LV", "LV", "SF"],
                "season": [2026] * 5,
                "status_last": [None, "RES", None, None, "ACT"],
            }
        ),
        "AT": pl.DataFrame(schema={"s": pl.String, "e": pl.String}),  # nothing to write
    }
    gt.nodes = {
        "Player": pl.DataFrame({"player_id": ["p1", "p2"], "name": ["A", "B"]}),
        "Game": pl.DataFrame(schema={"game_id": pl.String}),  # empty: skipped
        "Team": pl.DataFrame({"team_id": ["KC", "LV", "SF"]}),
    }
    return gt


def test_load_tables_writes_nodes_then_relationships_in_the_planned_order() -> None:
    driver = FakeDriver()
    timings = load.load_tables(driver, small_tables(), node_batch=2, rel_batch=2)
    queries = [q for q, _ in driver.calls]
    assert queries == [
        load.node_query("Team"),
        load.node_query("Team"),  # 3 rows in batches of 2
        load.node_query("Player"),
        load.rel_query("PLAYED_FOR"),
        load.rel_query("PLAYED_FOR"),
        load.rel_query("PLAYED_FOR"),  # 5 rows in batches of 2
        load.rel_query("NEXT"),
    ]
    assert list(timings) == ["node:Team", "node:Player", "rel:PLAYED_FOR", "rel:NEXT"]
    assert all(t >= 0 for t in timings.values())


def test_load_tables_batches_carry_the_cleaned_rows() -> None:
    driver = FakeDriver()
    load.load_tables(driver, small_tables(), node_batch=2, rel_batch=2)
    team_batches = [p["rows"] for q, p in driver.calls if q == load.node_query("Team")]
    assert team_batches == [[{"team_id": "KC"}, {"team_id": "LV"}], [{"team_id": "SF"}]]
    rel_batches = [p["rows"] for q, p in driver.calls if q == load.rel_query("PLAYED_FOR")]
    assert [len(b) for b in rel_batches] == [2, 2, 1]
    assert rel_batches[0] == [
        {"s": "p1", "e": "KC", "p": {"season": 2026}},  # status_last is null: not written
        {"s": "p2", "e": "KC", "p": {"season": 2026, "status_last": "RES"}},
    ]


def test_load_tables_reports_each_step() -> None:
    steps: list[tuple[str, float, int]] = []
    load.load_tables(FakeDriver(), small_tables(), on_step=lambda *a: steps.append(a))
    assert [(name, n) for name, _, n in steps] == [
        ("node:Team", 3),
        ("node:Player", 2),
        ("rel:PLAYED_FOR", 5),
        ("rel:NEXT", 2),
    ]


def test_load_tables_one_batch_by_default_for_small_tables() -> None:
    driver = FakeDriver()
    load.load_tables(driver, small_tables())
    assert len(driver.calls) == 4
    assert (load.NODE_BATCH, load.REL_BATCH) == (5_000, 10_000)


def test_load_tables_with_nothing_to_write_never_opens_a_session() -> None:
    driver = FakeDriver(fail=RuntimeError("must not connect"))
    assert load.load_tables(driver, T.GraphTables(KEY)) == {}


# ---- graph_counts / count_mismatches -------------------------------------------------------------


def test_graph_counts_reads_every_label_and_relationship_type() -> None:
    def respond(query: str, params: dict) -> list[dict]:
        if query.startswith("MATCH (n:"):
            label = re.search(r"\(n:(\w+)\)", query).group(1)  # type: ignore[union-attr]
            return [{"c": 10 + T.NODE_ORDER.index(label)}]
        rel = re.search(r"\[r:(\w+)\]", query).group(1)  # type: ignore[union-attr]
        return [{"c": 100 + T.REL_ORDER.index(rel)}]

    counts = load.graph_counts(FakeDriver(respond))
    assert list(counts) == [f"node:{k}" for k in T.NODE_ORDER] + [f"rel:{k}" for k in T.REL_ORDER]
    assert counts["node:Team"] == 10 and counts["node:PublishedInsight"] == 18
    assert counts["rel:PLAYED_FOR"] == 100 and counts["rel:HAS_PREDICTION"] == 114


def test_count_mismatches_reports_only_differences() -> None:
    expected = {"node:Team": 32, "node:Game": 5, "rel:NEXT": 0, "rel:AT": 5}
    actual = {"node:Team": 32, "node:Game": 4, "rel:NEXT": 0, "rel:AT": 5}
    assert load.count_mismatches(expected, actual) == {"node:Game": (5, 4)}
    assert load.count_mismatches(expected, expected) == {}


def test_count_mismatches_missing_and_surplus_keys() -> None:
    expected = {"node:Team": 3, "rel:NEXT": 0}
    actual = {"rel:AT": 7, "node:Venue": 0, "rel:NEXT": 0}
    got = load.count_mismatches(expected, actual)
    # Team expected but absent from Neo4j; AT in Neo4j but not expected; a zero count is no surplus
    assert got == {"node:Team": (3, 0), "rel:AT": (0, 7)}
    assert list(got) == sorted(got)


def test_count_mismatches_zero_expected_and_missing_actual_agree() -> None:
    assert load.count_mismatches({"rel:AT": 0}, {}) == {}
    assert load.count_mismatches({}, {}) == {}
