"""PlayerProjection nodes in the graph (plan P06; graph/projections.py), with a fake driver."""

from __future__ import annotations

import datetime as dt

import polars as pl
from graph_world import FakeDriver

from nflengine.graph import load
from nflengine.graph.projections import (
    MERGE_QUERY,
    projection_key,
    projection_rows,
    write_projections,
)
from nflengine.models.player_schema import conform

CREATED = dt.datetime(2026, 10, 6, 14, tzinfo=dt.UTC)


def preds(n: int = 3, **kw) -> pl.DataFrame:
    rows = [
        {
            "season": 2026,
            "week": 5,
            "game_id": "2026_05_KC_DEN",
            "created_at": CREATED,
            "player_id": f"00-{i:07d}",
            "player": f"Player {i}",
            "team": "KC",
            "opponent": "DEN",
            "group": "WR/TE",
            "target": "rec_yds",
            "target_label": "receiving yards",
            "unit": "yards",
            "is_main": True,
            "p10": 20.0,
            "p50": 55.0 + i,
            "p90": 95.0,
            "baseline": 50.0,
            "outperformance": 5.0 + i,
            "outperf_z": float("nan") if i == 1 else 0.2,
            "confidence": "medium",
            "model_version": "player-model-v1:2026-w05",
            "drivers": [
                {
                    "feature": "tgt_share_l4",
                    "phrase": "target share up",
                    "value": 0.27,
                    "contribution": 6.0,
                },
            ],  # fmt: skip
            **kw,
        }
        for i in range(n)
    ]
    return conform(pl.DataFrame(rows))


def merged(driver: FakeDriver) -> list[dict]:
    return [r for q, p in driver.calls if q == MERGE_QUERY for r in p["rows"]]


def test_rows_carry_key_endpoints_and_clean_properties():
    rows = projection_rows(preds(2))
    r0, r1 = rows
    assert r0["key"] == "00-0000000|2026_05_KC_DEN|rec_yds-wrte|player-model-v1:2026-w05"
    assert (r0["player_id"], r0["game_id"]) == ("00-0000000", "2026_05_KC_DEN")
    p = r0["props"]
    assert p["key"] == r0["key"] and p["p50"] == 55.0 and p["group"] == "WR/TE"
    assert p["created_at"] == "2026-10-06T14:00:00+00:00"
    assert p["top_drivers"] == ["target share up"]
    assert "outperf_z" not in r1["props"]  # NaN -> null -> left out (Neo4j skips nulls)
    assert "actual" not in p and "injury_status" not in p  # nulls are never sent


def test_a_linebacker_gets_one_node_per_target_and_duplicates_collapse():
    two_pools = pl.concat(
        [
            preds(1, group="EDGE/DL", target="pressures", unit="count"),
            preds(1, group="LB/S", target="tackles", unit="count"),
            preds(1, group="LB/S", target="tackles", unit="count", p50=7.0),
        ]
    )
    rows = projection_rows(two_pools)
    assert [r["key"].split("|")[2] for r in rows] == ["pressures-edge", "tackles-lbs"]
    assert rows[1]["props"]["p50"] == 7.0  # the last duplicate wins


def test_write_is_an_idempotent_batched_merge_with_counts():
    def respond(query, params):
        n = len(params["rows"])
        return [{"nodes": n, "players": n, "games": n - 1}]

    driver = FakeDriver(respond)
    res = write_projections(preds(5), driver, log=lambda m: None, batch_size=2)
    assert res.status == "ok" and res.error is None
    assert (res.nodes, res.linked_players, res.linked_games) == (5, 5, 2)
    assert [len(p["rows"]) for _, p in driver.calls] == [2, 2, 1]
    assert len(merged(driver)) == 5
    assert "MERGE (pp:PlayerProjection {key: r.key})" in MERGE_QUERY
    assert "MERGE (p)-[:HAS_PROJECTION]->(pp)" in MERGE_QUERY
    assert "MERGE (pp)-[:FOR_GAME]->(g)" in MERGE_QUERY
    assert "CREATE" not in MERGE_QUERY  # re-running a week must not duplicate anything


def test_nothing_to_write_is_skipped_without_touching_neo4j():
    driver = FakeDriver()
    assert write_projections(preds(1).clear(), driver, log=lambda m: None).status == "skipped"
    assert driver.calls == []


def test_neo4j_down_is_fail_soft_and_never_leaks_the_driver_message():
    class ServiceUnavailable(Exception):
        pass

    logs: list[str] = []
    driver = FakeDriver(fail=ServiceUnavailable("SENTINEL secret server text"))
    res = write_projections(preds(2), driver, log=logs.append)
    assert res.status == "unavailable" and res.nodes == 0
    assert res.error == "ServiceUnavailable: Neo4j unreachable"
    assert not any("SENTINEL" in m for m in logs)


def test_a_query_error_mid_write_is_fail_soft_too():
    def respond(query, params):
        raise RuntimeError("SENTINEL")

    res = write_projections(preds(2), FakeDriver(respond), log=lambda m: None)
    assert res.status == "unavailable" and "SENTINEL" not in (res.error or "")


def test_projection_key_and_schema_constraint_agree():
    assert projection_key(
        {"player_id": "p", "game_id": "g", "target": "rec_yds", "group": "WR/TE",
         "model_version": "v"}
    ) == "p|g|rec_yds-wrte|v"  # fmt: skip
    stmts = load.schema_statements()
    assert any("(pp:PlayerProjection) REQUIRE pp.key IS UNIQUE" in s for s in stmts)
    assert any("(pp:PlayerProjection) ON (pp.season, pp.week)" in s for s in stmts)


def test_result_summary_is_one_log_line():
    driver = FakeDriver(lambda q, p: [{"nodes": 2, "players": 2, "games": 1}])
    res = write_projections(preds(2), driver, log=lambda m: None)
    assert res.summary.startswith("graph projections: 2 written (2 linked to players, 1 to")
    down = write_projections(preds(1), FakeDriver(fail=OSError("x")), log=lambda m: None)
    assert down.summary == "graph projections unavailable (OSError: Neo4j unreachable)"
