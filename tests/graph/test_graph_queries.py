"""The graph query library (P05): files, parameters, columns and the runner.

The `.cypher` files are only text here (no Neo4j). What is checked: every library entry has its
file; every query is read-only and returns the columns the digest needs (`insight_type`,
`strength`, `sample_size`, ...); every column an insight converter reads is returned by its
query; every `$parameter` is supplied; and `run_query` never raises for a database error.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest
from graph_world import FakeDriver

from nflengine.graph import insights as gi
from nflengine.graph import queries as Q
from nflengine.graph import tables as T

# query name -> (insight_type it returns, converter that reads its rows)
TYPE_OF = {name: itype for itype, name in gi.QUERY_OF.items()}


def comment_free(name: str) -> str:
    lines = Q.query_text(name).splitlines()
    return "\n".join(ln for ln in lines if not ln.strip().startswith("//"))


def returned_names(name: str) -> list[str]:
    """The column names of a query's final RETURN: the alias after AS, else the bare name."""
    body = comment_free(name)
    clause = body[body.rindex("RETURN") + len("RETURN") :]
    clause = re.split(r"\bORDER BY\b", clause)[0]
    parts: list[str] = []
    depth, cur, in_str = 0, "", False
    for ch in clause:
        if ch == "'":
            in_str = not in_str
        elif not in_str:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
                continue
        cur += ch
    parts.append(cur)
    names = []
    for p in (x.strip() for x in parts):
        m = re.search(r"\bAS\s+(\w+)\s*$", p)
        names.append(m.group(1) if m else p)
    return names


# ---- the library --------------------------------------------------------------------------------


def test_every_library_entry_has_a_file_and_every_file_an_entry() -> None:
    files = {p.stem for p in Q.QUERY_DIR.glob("*.cypher")}
    assert files == set(Q.LIBRARY)
    for name in Q.LIBRARY:
        assert Q.query_text(name).strip()


def test_query_text_for_an_unknown_name_lists_the_library() -> None:
    with pytest.raises(KeyError, match="unknown graph query 'q99'"):
        Q.query_text("q99")
    with pytest.raises(KeyError, match="q1_revenge"):
        Q.query_text("q99")


def test_the_query_parser_helper_on_a_known_query() -> None:
    names = returned_names("q8_trend_mismatch")
    assert names[:3] == ["insight_type", "game_id", "team"]
    assert "window_weeks" in names and names[-1] == "strength"


@pytest.mark.parametrize("name", list(Q.LIBRARY))
def test_every_query_returns_the_columns_the_digest_needs(name: str) -> None:
    names = returned_names(name)
    assert {"insight_type", "strength", "sample_size", "game_id", "team", "opponent"} <= set(names)
    assert len(names) == len(set(names)), "a column is returned twice"
    assert f"'{TYPE_OF[name]}' AS insight_type" in comment_free(name)


@pytest.mark.parametrize("name", list(Q.LIBRARY))
def test_every_query_is_read_only(name: str) -> None:
    text = comment_free(name)
    for word in ("CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE", "DROP", "FOREACH"):
        assert not re.search(rf"\b{word}\b", text), f"{name} uses {word}"


@pytest.mark.parametrize("name", list(Q.LIBRARY))
def test_every_parameter_is_supplied_and_every_extra_is_used(name: str) -> None:
    used = set(re.findall(r"\$(\w+)", comment_free(name)))
    supplied = {"season", "week", *Q.LIBRARY[name]}
    assert used <= supplied, f"{name} uses unsupplied {used - supplied}"
    assert set(Q.LIBRARY[name]) <= used, f"{name} gets extras it never uses"
    assert {"season", "week"} <= used


def test_the_position_groups_the_queries_use_exist_in_the_graph() -> None:
    groups = set(T.POSITION_GROUPS.values())
    assert set(Q.OFFENSE_GROUPS) | set(Q.DEFENSE_GROUPS) <= groups
    assert Q.LIBRARY["q2_injury_ripple"]["groups"] == Q.OFFENSE_GROUPS + Q.DEFENSE_GROUPS
    assert not {"QB", "SPEC"} & set(Q.LIBRARY["q2_injury_ripple"]["groups"])  # QBs are Q3's
    for g in Q.DEFENSE_GROUPS:  # the converter's defense test is the same three groups
        assert g in {"DL", "LB", "DB"}


# ---- the converters read only what the queries return -------------------------------------------

ROW_KEY = re.compile(r"""\br(?:\[['"](\w+)['"]\]|\.get\(['"](\w+)['"])""")


@pytest.mark.parametrize("name", list(Q.LIBRARY))
def test_every_column_a_converter_reads_is_returned_by_its_query(name: str) -> None:
    src = inspect.getsource(gi.CONVERTERS[name])
    read = {a or b for a, b in ROW_KEY.findall(src)}
    assert read, f"found no row reads in {name}'s converter"
    missing = read - set(returned_names(name))
    assert not missing, f"{name}: converter reads {sorted(missing)} that the query doesn't return"


@pytest.mark.parametrize(
    "name, keys",
    [
        ("q3_qb_change", ["player_id", "name", "r_targets", "q_targets"]),  # `receivers` entries
        ("q4_common_opponents", ["team", "a_games", "b_games"]),  # `common` entries
        ("q4_common_opponents", ["week", "margin"]),  # entries of a_games / b_games
    ],
)
def test_nested_map_keys_the_converters_read_exist_in_the_cypher(
    name: str, keys: list[str]
) -> None:
    text = comment_free(name)
    for key in keys:
        assert re.search(rf"\b{key}:", text), f"{name} builds no map key {key!r}"


# ---- running -----------------------------------------------------------------------------------


class IsoTime:
    """What `neo4j.time` values look like to `_plain`."""

    def iso_format(self) -> str:
        return "2026-03-14T00:00:00"


def test_plain_converts_neo4j_values_recursively() -> None:
    value = {"when": IsoTime(), "rows": [IsoTime(), {"d": IsoTime()}], "n": 3, "s": "x", "z": None}
    assert Q._plain(value) == {
        "when": "2026-03-14T00:00:00",
        "rows": ["2026-03-14T00:00:00", {"d": "2026-03-14T00:00:00"}],
        "n": 3,
        "s": "x",
        "z": None,
    }


def test_run_query_sends_the_text_with_season_week_and_library_parameters() -> None:
    def respond(query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"insight_type": "revenge", "traded_on": IsoTime(), "seasons": [2024, 2025]}]

    driver = FakeDriver(respond)
    result = Q.run_query(driver, "q1_revenge", 2026, 4)
    assert result.error is None and result.name == "q1_revenge"
    assert result.rows == [
        {"insight_type": "revenge", "traded_on": "2026-03-14T00:00:00", "seasons": [2024, 2025]}
    ]
    assert driver.calls == [
        (
            Q.query_text("q1_revenge"),
            {"season": 2026, "week": 4, "min_old_games": 4, "min_snap_pct": 0.5},
        )
    ]
    assert result.seconds >= 0


def test_run_query_overrides_replace_library_parameters() -> None:
    driver = FakeDriver()
    Q.run_query(driver, "q1_revenge", 2026, 4, min_old_games=1)
    assert driver.calls[0][1]["min_old_games"] == 1 and driver.calls[0][1]["min_snap_pct"] == 0.5


def test_run_query_with_no_rows() -> None:
    result = Q.run_query(FakeDriver(), "q8_trend_mismatch", 2026, 4)
    assert result.rows == [] and result.error is None


def test_run_query_never_raises_for_a_database_error_and_records_it() -> None:
    result = Q.run_query(
        FakeDriver(fail=RuntimeError("connection refused")), "q2_injury_ripple", 2026, 4
    )
    # type only: driver / server text never reaches files or W&B (Sol review)
    assert result.rows == [] and result.error == "RuntimeError"
    assert result.seconds >= 0


def test_run_query_records_errors_raised_inside_the_transaction() -> None:
    def respond(query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        raise ValueError("Variable `x` not defined")

    result = Q.run_query(FakeDriver(respond), "q3_qb_change", 2026, 4)
    assert result.rows == [] and result.error == "ValueError"


def test_run_query_never_records_the_error_text() -> None:
    """A sentinel in the exception message (it could be server text echoing parameters)
    never reaches the recorded error (Sol review); a Neo4j status code does."""
    secret = "SENTINEL-do-not-leak"
    result = Q.run_query(FakeDriver(fail=Exception(secret)), "q4_common_opponents", 2026, 4)
    assert result.error == "Exception" and secret not in str(result.to_dict())

    class ClientError(Exception):
        code = "Neo.ClientError.Statement.SyntaxError"

    coded = Q.run_query(FakeDriver(fail=ClientError(secret)), "q4_common_opponents", 2026, 4)
    assert coded.error == "ClientError: Neo.ClientError.Statement.SyntaxError"


def test_run_query_for_an_unknown_name_is_a_programming_error() -> None:
    with pytest.raises(KeyError):
        Q.run_query(FakeDriver(), "q99", 2026, 4)


def test_run_library_runs_every_query_and_survives_a_dead_server() -> None:
    driver = FakeDriver()
    results = Q.run_library(driver, 2026, 4)
    assert list(results) == list(Q.LIBRARY)
    assert [q for q, _ in driver.calls] == [Q.query_text(n) for n in Q.LIBRARY]
    dead = Q.run_library(FakeDriver(fail=ConnectionError("down")), 2026, 4)
    assert all(
        r.error == "ConnectionError: Neo4j unreachable" and r.rows == [] for r in dead.values()
    )


def test_query_result_to_dict() -> None:
    r = Q.QueryResult("q1_revenge", [{"a": 1}, {"a": 2}], 0.123456, None)
    assert r.to_dict() == {
        "name": "q1_revenge",
        "rows": 2,
        "seconds": 0.123,
        "error": None,
        "results": [{"a": 1}, {"a": 2}],
    }
    assert Q.QueryResult("q", error="boom").to_dict()["error"] == "boom"
