"""The history graph's contract (AE02): schema.py's entries, the bulk-import headers, and
schema.cypher (constraints and indexes) agree with each other."""

from __future__ import annotations

import re

from nflengine.history_graph import schema as S
from nflengine.history_graph.build import SCHEMA_FILE, schema_statements


def test_every_node_has_a_key_and_unique_property_names():
    for label, n in S.NODES.items():
        assert n.label == label
        assert n.key not in n.props, label
        assert n.columns()[0] == n.key
        assert len(set(n.columns())) == len(n.columns())


def test_every_relationship_connects_known_labels():
    for rtype, r in S.RELATIONSHIPS.items():
        assert r.type == rtype
        assert r.start in S.NODES and r.end in S.NODES, rtype
        assert r.columns()[:2] == ["s", "e"]
        assert not {"s", "e"} & set(r.props), rtype


def test_property_types_are_bulk_import_types():
    ok = {S.LONG, S.DOUBLE, S.BOOL, S.STRING, S.LONGS}
    for n in S.NODES.values():
        assert set(n.props.values()) <= ok, n.label
    for r in S.RELATIONSHIPS.values():
        assert set(r.props.values()) <= ok, r.type


def test_the_pieces_the_other_modules_rely_on():
    assert set(S.ROLE_TYPES) <= set(S.RELATIONSHIPS)
    assert set(S.SEASON_TYPES) <= set(S.RELATIONSHIPS)
    assert set(S.SEED_ROLES.values()) <= set(S.RELATIONSHIPS)
    # every season-owned relationship has a `season` property (the append reads x.season)
    for t in S.SEASON_TYPES:
        assert S.RELATIONSHIPS[t].props.get("season") == S.LONG, t
    # nodes the append finds by `n.season = $season`
    for label in ("Game", "Drive", "Play"):
        assert S.NODES[label].props.get("season") == S.LONG, label


def test_key_types():
    assert S.key_type("Season") == S.LONG
    assert {S.key_type(x) for x in S.NODES if x != "Season"} == {S.STRING}
    assert S.key_of("Play") == "play_key"
    assert S.key_of("Drive") == "drive_key"
    assert S.key_of("Season") == "season"


def test_csv_headers_for_every_node_and_relationship():
    for label, n in S.NODES.items():
        h = S.csv_header(label)
        if label == "Season":
            assert h == [":ID(Season)", "season:long"]
        else:
            assert h[0] == f"{n.key}:ID({label})"
            assert h[1:] == [f"{p}:{t}" for p, t in n.props.items()]
    for rtype, r in S.RELATIONSHIPS.items():
        h = S.csv_header(rtype)
        assert h[:2] == [f":START_ID({r.start})", f":END_ID({r.end})"]
        assert h[2:] == [f"{p}:{t}" for p, t in r.props.items()]


def test_the_header_of_the_array_property():
    assert "weeks:long[]" in S.csv_header("PLAYED_FOR")


def test_play_properties_documented_by_the_phase_file_exist():
    play = S.NODES["Play"].props
    for p in (
        "game_id",
        "play_id",
        "season",
        "week",
        "quarter",
        "clock",
        "down",
        "distance",
        "yards_to_goal",
        "play_type",
        "yards",
        "epa",
        "success",
        "wp",
        "formation",
        "personnel",
        "coverage",
        "man_zone",
        "target_route",
        "pressure",
    ):
        assert p in play, p
    ftn = {"play_action", "screen", "rpo", "motion", "blitzers", "box"}
    assert ftn <= set(play)


# ---- schema.cypher ---------------------------------------------------------------------------


def test_schema_statements_are_idempotent_and_named():
    stmts = schema_statements()
    assert stmts
    for s in stmts:
        assert re.match(r"CREATE (CONSTRAINT|INDEX) history_\w+ IF NOT EXISTS FOR", s), s
        assert "DROP" not in s.upper()
    names = [re.search(r"history_\w+", s).group(0) for s in stmts]
    assert len(names) == len(set(names))


def test_a_uniqueness_constraint_for_every_node_key():
    stmts = " ".join(schema_statements())
    for label, n in S.NODES.items():
        assert re.search(rf"FOR \(n:{label}\) REQUIRE n\.{n.key} IS UNIQUE", stmts), (
            f"no uniqueness constraint for {label}.{n.key}"
        )


def test_indexes_name_real_properties():
    for s in schema_statements():
        m = re.search(r"FOR \(n:(\w+)\) (?:ON \(([^)]*)\)|REQUIRE n\.(\w+) IS UNIQUE)", s)
        assert m, s
        label = m.group(1)
        assert label in S.NODES, s
        props = [p.strip().removeprefix("n.") for p in (m.group(2) or m.group(3)).split(",")]
        for p in props:
            assert p in S.NODES[label].columns(), f"{s}: {p} isn't a {label} property"


def test_the_indexes_the_phase_file_asks_for():
    text = " ".join(schema_statements())
    assert "FOR (n:Play) ON (n.season, n.week)" in text
    assert "FOR (n:Player) ON (n.name)" in text


def test_the_statements_have_no_comments_or_blank_pieces():
    for s in schema_statements():
        assert "//" not in s and s.strip() == s and s.count(";") == 0
    assert SCHEMA_FILE.exists()


def test_schema_statements_split_a_comment_only_file_to_nothing():
    assert schema_statements("// only a comment\n// and another\n") == []


def test_cypher_names_all_start_with_history_():
    # a different server from the weekly graph's, but a name that says which graph it is on
    for s in schema_statements():
        assert " history_" in s
