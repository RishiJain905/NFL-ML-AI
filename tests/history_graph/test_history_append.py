"""The weekly append (AE02): the diff functions on small fake data, then `sync` / `run_append`
against a fake driver that holds nodes and relationships in Python dicts (`history_fakes.py`).
The graph before an append is the bulk import of 2019 + 2024 (loaded from the CSVs the export
writes, so the CSV semantics are part of the test); the append brings in the 2026 season."""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

import polars as pl
import pytest
from history_fakes import FakeGraph, FakeNeo4jError, diff_graphs
from history_world import (
    GAME_META,
    GC,
    GD,
    LB_LV,
    QB_KC,
    RB_LV,
    TE_KC,
    WR_KC,
    game_row,
    install_world,
    load,
    play,
)

from nflengine.history_graph import append as A
from nflengine.history_graph import export as X
from nflengine.history_graph import schema as S
from nflengine.history_graph import state
from nflengine.history_graph.config import HistoryConfig
from nflengine.ops.lock import run_lock

CFG = HistoryConfig()
QUIET = lambda *_: None  # noqa: E731


# ---- the pure functions ----------------------------------------------------------------------


class TestClean:
    def test_nan_and_blank_text_become_none_everything_else_is_kept(self):
        assert A._clean(float("nan")) is None
        assert A._clean("") is None and A._clean("   ") is None and A._clean("\t\n") is None
        assert A._clean(None) is None
        assert A._clean(0.0) == 0.0 and A._clean(0) == 0 and A._clean(False) is False
        assert A._clean([1, 2]) == [1, 2] and A._clean("a") == "a"
        assert A._clean(" x ") == " x "

    def test_props_drop_nulls_nan_and_blank_text_but_keep_false_and_zero(self):
        row = {
            "a": 1,
            "b": None,
            "c": float("nan"),
            "d": False,
            "e": 0,
            "f": "",
            "g": 2.5,
            "h": " ",
        }
        assert A._props(row) == {"a": 1, "d": False, "e": 0, "g": 2.5}
        assert A._props(row, drop=("a", "g")) == {"d": False, "e": 0}


class TestRelId:
    @pytest.mark.parametrize("rtype", S.SEASON_TYPES)
    def test_season_owned_types_are_identified_by_their_season_too(self, rtype):
        assert A._rel_id(rtype, "s", "e", {"season": 2024, "games": 3}) == ("s", "e", 2024)
        assert A._rel_id(rtype, "s", "e", {"season": 2025}) != A._rel_id(
            rtype, "s", "e", {"season": 2024}
        )

    @pytest.mark.parametrize("rtype", [t for t in S.RELATIONSHIPS if t not in S.SEASON_TYPES])
    def test_other_types_are_identified_by_their_ends(self, rtype):
        assert A._rel_id(rtype, "s", "e", {"kind": "solo", "half": True}) == ("s", "e")


class TestRows:
    def test_node_rows_are_keyed_by_the_labels_key_and_drop_nulls(self):
        df = pl.DataFrame(
            {
                "team_id": ["KC", "LV"],
                "name": ["Chiefs", None],
                "nickname": [None, None],
                "conference": ["AFC", "AFC"],
                "division": [None, "W"],
            }
        )
        rows = A.node_rows(df, "Team")
        assert rows == {
            "KC": {"team_id": "KC", "name": "Chiefs", "conference": "AFC"},
            "LV": {"team_id": "LV", "conference": "AFC", "division": "W"},
        }

    def test_node_rows_use_the_numeric_season_key(self):
        assert A.node_rows(pl.DataFrame({"season": [2026]}), "Season") == {2026: {"season": 2026}}

    def test_rel_rows_carry_ends_and_properties_and_use_the_rel_id(self):
        df = pl.DataFrame(
            {
                "s": ["p1", "p1"],
                "e": ["KC", "KC"],
                "season": [2025, 2026],
                "weeks": [[1], [1, 2]],
                "first_week": [1, 1],
                "last_week": [1, 2],
                "games": [1, 2],
                "status_last": ["ACT", None],
            }
        )
        rows = A.rel_rows(df, "PLAYED_FOR")
        assert set(rows) == {("p1", "KC", 2025), ("p1", "KC", 2026)}  # one per season
        s, e, p = rows[("p1", "KC", 2026)]
        assert (s, e) == ("p1", "KC")
        assert p == {"season": 2026, "weeks": [1, 2], "first_week": 1, "last_week": 2, "games": 2}

    def test_rel_rows_for_a_plain_type(self):
        df = pl.DataFrame({"s": ["p"], "e": ["g:1"], "kind": ["solo"]})
        assert A.rel_rows(df, "TACKLED") == {("p", "g:1"): ("p", "g:1", {"kind": "solo"})}


class TestDiffNodes:
    def test_create_update_delete(self):
        want = {1: {"k": 1, "v": "a"}, 2: {"k": 2, "v": "new"}, 4: {"k": 4}}
        have = {1: {"k": 1, "v": "a"}, 2: {"k": 2, "v": "old"}, 3: {"k": 3}}
        d = A.diff_nodes(want, have, delete=True)
        assert d == {"create": [{"k": 4}], "update": [{"k": 2, "v": "new"}], "delete": [3]}

    def test_shared_nodes_are_never_deleted(self):
        d = A.diff_nodes({1: {"k": 1}}, {1: {"k": 1}, 9: {"k": 9}}, delete=False)
        assert d == {"create": [], "update": [], "delete": []}

    def test_a_property_that_vanished_is_an_update(self):
        d = A.diff_nodes({1: {"k": 1}}, {1: {"k": 1, "v": "x"}}, delete=False)
        assert d["update"] == [{"k": 1}]

    def test_equal_numbers_of_different_types_are_equal(self):
        # Neo4j returns a double 5.0 for what Polars calls 5.0; ints stay ints
        assert A.diff_nodes({1: {"k": 1, "x": 5.0}}, {1: {"k": 1, "x": 5}}, True)["update"] == []

    def test_lists_compare_by_value(self):
        want = {1: {"k": 1, "w": [1, 2]}}
        assert A.diff_nodes(want, {1: {"k": 1, "w": [1, 2]}}, True)["update"] == []
        assert A.diff_nodes(want, {1: {"k": 1, "w": [1, 2, 3]}}, True)["update"] == [want[1]]

    def test_empty_in_empty_out(self):
        assert A.diff_nodes({}, {}, True) == {"create": [], "update": [], "delete": []}


class TestDiffRels:
    def test_create_update_delete(self):
        want = {
            ("a", "b"): ("a", "b", {"kind": "solo"}),
            ("a", "c"): ("a", "c", {"kind": "assist"}),
            ("a", "d"): ("a", "d", {}),
        }
        have = {("a", "b"): {"kind": "assist"}, ("a", "c"): {"kind": "assist"}, ("a", "z"): {}}
        d = A.diff_rels(want, have)
        assert d["create"] == [("a", "d", {})]
        assert d["update"] == [("a", "b", {"kind": "solo"})]
        assert d["delete"] == [("a", "z")]

    def test_season_types_with_two_seasons_do_not_collide(self):
        w = A.rel_rows(pl.DataFrame({"s": ["c"], "e": ["KC"], "season": [2026]}), "OFF_COORD")
        have = {("c", "KC", 2025): {"season": 2025}}  # last season's link is another relationship
        d = A.diff_rels(w, have)
        assert len(d["create"]) == 1 and d["delete"] == [("c", "KC", 2025)]

    def test_nothing_to_do(self):
        w = {("a", "b"): ("a", "b", {"k": 1})}
        assert A.diff_rels(w, {("a", "b"): {"k": 1}}) == {"create": [], "update": [], "delete": []}


def test_changes_lists_only_the_non_empty_operations():
    plan = {
        "Play": {"create": [1, 2], "update": [], "delete": [3]},
        "Game": {"create": [], "update": [], "delete": []},
        "TACKLED": {"create": [], "update": [1], "delete": []},
    }
    assert A.changes(plan) == {"Play": {"create": 2, "delete": 1}, "TACKLED": {"update": 1}}
    assert A.changes({}) == {}


# ---- the sync against a fake graph -----------------------------------------------------------


class Env(SimpleNamespace):
    def append(self, season=2026, **kw):
        kw.setdefault("use_wandb", False)
        kw.setdefault("log", QUIET)
        return A.run_append(season, paths=self.paths, cfg=CFG, driver=self.graph, **kw)

    def clean_graph(self, seasons=(2019, 2024, 2026), tmp=None):
        """The graph a bulk import of `seasons` would store, from the world's current files."""
        fr = load(self.world, seasons=seasons)
        out = (tmp or self.paths.root) / f"csv-{'-'.join(map(str, seasons))}"
        written = X.write_csvs(fr, out)
        return FakeGraph().load_csv(out, written)


def write_build(paths, **over):
    doc = {
        "status": "ok",
        "build_id": "B1",
        "first_season": 2019,
        "through_season": 2024,
        "seasons": [2019, 2024],
        **over,
    }
    p = state.build_path(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")
    return doc


@pytest.fixture
def env(tmp_path, monkeypatch):
    w = install_world(tmp_path, monkeypatch)
    fr = load(w, seasons=(2019, 2024))
    out = tmp_path / "bulk-csv"
    written = X.write_csvs(fr, out)
    graph = FakeGraph().load_csv(out, written)
    write_build(w.paths)
    return Env(world=w, paths=w.paths, graph=graph, bulk=fr)


FIRST = {
    # what the 2026 season adds to the 2019 + 2024 graph (worked out from the fixture rows)
    "Season": {"create": 1},
    "Coach": {"create": 3},  # pete-carroll, interim-joe, joe-defense (Andy Reid is already there)
    "Game": {"create": 2},
    "Drive": {"create": 4},
    "Play": {"create": 8},
    "IN_SEASON": {"create": 2},
    "HOME_TEAM": {"create": 2},
    "AWAY_TEAM": {"create": 2},
    "IN_GAME": {"create": 4},
    "IN_DRIVE": {"create": 8},
    "OFFENSE": {"create": 8},
    "DEFENSE": {"create": 8},
    "PASSED": {"create": 3},
    "RUSHED": {"create": 3},
    "TARGETED": {"create": 3},
    "CAUGHT": {"create": 2},
    "PLAYED_FOR": {"create": 6},
    "HEAD_COACH": {"create": 3},
    "DEF_COORD": {"create": 1},
}


class TestFirstAppend:
    def test_it_creates_exactly_the_seasons_entities(self, env):
        doc = env.append()
        assert doc["status"] == "ok" and doc["changes"] == FIRST
        assert doc["check"] == {} and doc["dropped"] == {}

    def test_the_result_equals_a_bulk_import_of_all_three_seasons(self, env):
        env.append()
        full = env.clean_graph()
        assert diff_graphs(env.graph, full) == {}
        assert env.graph.counts() == full.counts()

    def test_counts_equal_the_exports_expected_counts(self, env):
        doc = env.append()
        assert env.graph.counts() == load(env.world).expected_counts()
        assert (
            doc["counts"] == load(env.world, seasons=(2026,), participation=False).expected_counts()
        )

    def test_older_seasons_are_never_touched(self, env):
        def old_part(label):
            return {
                k: dict(v)
                for k, v in env.graph.nodes[label].items()
                if v.get("season") in (2019, 2024)
            }

        before = {label: old_part(label) for label in ("Game", "Drive", "Play")}
        rels_before = {
            t: list(env.graph.rels[t]) for t in ("ON_FIELD", "TACKLED", "INTERCEPTED", "SACKED")
        }
        env.append()
        for label, nodes in before.items():
            assert old_part(label) == nodes
        for t, rels in rels_before.items():
            assert env.graph.rels[t] == rels, t  # (no 2026 links of these types)
        assert env.graph.seasons_asked == {2026}  # no query ever asked for another season

    def test_append_json_and_state(self, env):
        doc = env.append()
        saved = json.loads(state.append_path(env.paths).read_text(encoding="utf-8"))
        assert saved["status"] == "ok" and saved["build_id"] == "B1"
        assert (saved["season"], saved["through_week"], saved["weeks"]) == (2026, 2, [1, 2])
        assert (saved["games"], saved["plays"]) == (2, 8)
        assert saved["changes"] == doc["changes"] and saved["check"] == {}
        assert set(saved["seconds"]) == {"export", "sync", "total"}
        asof = state.history_asof(env.paths)
        assert asof["label"] == "history graph 2019-2024 + 2026 week 2"
        assert (asof["season"], asof["week"]) == (2026, 2)

    def test_schema_statements_are_applied_before_anything_else(self, env):
        env.append()
        assert env.graph.queries[0].startswith("CREATE CONSTRAINT")
        assert len(env.graph.schema_statements) == 15 or len(env.graph.schema_statements) > 7
        assert all("IF NOT EXISTS" in s for s in env.graph.schema_statements)

    def test_participation_is_not_appended(self, env):
        env.append()
        assert not [r for r in env.graph.rels["ON_FIELD"] if str(r["e"]).startswith("2026")]
        plays26 = [p for p in env.graph.nodes["Play"].values() if p["season"] == 2026]
        assert plays26 and not any("part" in p or "formation" in p for p in plays26)

    def test_through_week_one_appends_only_week_one(self, env):
        doc = env.append(through_week=1)
        assert doc["weeks"] == [1] and doc["games"] == 1 and doc["plays"] == 5
        assert {p["week"] for p in env.graph.nodes["Play"].values() if p["season"] == 2026} == {1}
        weeks = [r["p"]["weeks"] for r in env.graph.rels["PLAYED_FOR"] if r["p"]["season"] == 2026]
        assert all(w == [1] for w in weeks)

    def test_the_default_week_is_the_newest_week_with_plays(self, env):
        assert env.append()["through_week"] == 2

    def test_the_default_season_is_the_current_one(self, env, monkeypatch):
        monkeypatch.setattr(
            "nflengine.settings.get_config",
            lambda: SimpleNamespace(seasons=SimpleNamespace(current=2026)),
        )
        doc = A.run_append(
            None, paths=env.paths, cfg=CFG, driver=env.graph, use_wandb=False, log=QUIET
        )
        assert doc["season"] == 2026

    def test_the_drivers_session_is_not_closed_by_run_append_when_given(self, env):
        env.append()
        assert env.graph.closed is False  # the caller's driver stays open

    def test_batches_respect_the_batch_size(self, env):
        cfg = HistoryConfig(batch_size=3)
        A.run_append(2026, paths=env.paths, cfg=cfg, driver=env.graph, use_wandb=False, log=QUIET)
        assert max(n for _, n in env.graph.writes) <= 3
        assert diff_graphs(env.graph, env.clean_graph()) == {}


class TestSecondAppend:
    def test_a_second_run_plans_and_writes_nothing(self, env):
        env.append()
        writes_before = env.graph.n_writes()
        snap = env.graph.snapshot()
        doc = env.append()
        assert doc["changes"] == {} and doc["check"] == {} and doc["status"] == "ok"
        assert env.graph.n_writes() == writes_before  # not a single write transaction
        assert env.graph.snapshot() == snap

    def test_nan_in_the_data_does_not_make_every_run_an_update(self, env):
        # NaN != NaN: a naive diff would rewrite the play on every run
        for r in env.world.plays[2026]:
            if r["game_id"] == GC and r["play_id"] == 2:
                r["epa"] = float("nan")
        env.world.write()
        env.append()
        p = env.graph.nodes["Play"][f"{GC}:2"]
        assert "epa" not in p  # no NaN in the graph
        assert math.isnan(
            pl.read_parquet(env.paths.curated / "plays" / "season=2026.parquet")["epa"][1]
        )
        n = env.graph.n_writes()
        assert env.append()["changes"] == {} and env.graph.n_writes() == n


def edit(env, game, pid, **changes):
    for r in env.world.plays[GAME_META[game][0]]:
        if r["game_id"] == game and r["play_id"] == pid:
            r.update(changes)
            env.world.write()
            return
    raise AssertionError("no such play")


class TestChangesBetweenRuns:
    def test_a_corrected_epa_is_one_play_update(self, env):
        env.append()
        edit(env, GC, 2, epa=0.45)
        doc = env.append()
        assert doc["changes"] == {"Play": {"update": 1}}
        assert env.graph.nodes["Play"][f"{GC}:2"]["epa"] == 0.45
        assert diff_graphs(env.graph, env.clean_graph()) == {}

    def test_a_dropped_play_is_deleted_with_its_relationships(self, env):
        env.append()
        n_before = env.graph.counts()
        env.world.plays[2026] = [
            r for r in env.world.plays[2026] if not (r["game_id"] == GC and r["play_id"] == 5)
        ]
        env.world.write()
        doc = env.append()
        assert doc["changes"] == {"Play": {"delete": 1}}  # its links went with it (DETACH)
        assert f"{GC}:5" not in env.graph.nodes["Play"]
        n = env.graph.counts()
        assert n["Play"] == n_before["Play"] - 1
        for t in ("IN_DRIVE", "OFFENSE", "DEFENSE"):
            assert n[t] == n_before[t] - 1, t
        assert diff_graphs(env.graph, env.clean_graph()) == {}

    def test_an_added_tackle_is_one_new_relationship(self, env):
        env.append()
        edit(env, GC, 3, solo_tackle_1_player_id=LB_LV)
        doc = env.append()
        assert doc["changes"] == {"TACKLED": {"create": 1}}
        assert [r["p"] for r in env.graph.rels["TACKLED"] if r["e"] == f"{GC}:3"] == [
            {"kind": "solo"}
        ]
        assert diff_graphs(env.graph, env.clean_graph()) == {}

    def test_a_tackle_that_changes_kind_is_replaced_not_duplicated(self, env):
        env.append()
        edit(env, GC, 3, solo_tackle_1_player_id=LB_LV)
        env.append()
        edit(env, GC, 3, solo_tackle_1_player_id=None, assist_tackle_1_player_id=LB_LV)
        doc = env.append()
        assert doc["changes"] == {"TACKLED": {"update": 1}}
        rels = [r for r in env.graph.rels["TACKLED"] if r["e"] == f"{GC}:3"]
        assert len(rels) == 1 and rels[0]["p"] == {"kind": "assist"}

    def test_a_removed_tackle_is_a_relationship_delete(self, env):
        env.append()
        edit(env, GC, 3, solo_tackle_1_player_id=LB_LV)
        env.append()
        edit(env, GC, 3, solo_tackle_1_player_id=None)
        assert env.append()["changes"] == {"TACKLED": {"delete": 1}}
        assert not [r for r in env.graph.rels["TACKLED"] if r["e"] == f"{GC}:3"]

    def test_a_new_roster_week_updates_the_played_for_link(self, env):
        env.append()
        env.world.rosters.append(
            {
                "season": 2026,
                "week": 3,
                "team": "KC",
                "gsis_id": QB_KC,
                "status": "ACT",
                "position": "QB",
                "full_name": "Pat Quarterback",
            }
        )
        env.world.write()
        doc = env.append(through_week=3)
        assert doc["changes"] == {"PLAYED_FOR": {"update": 1}}
        rels = [
            r for r in env.graph.rels["PLAYED_FOR"] if r["s"] == QB_KC and r["p"]["season"] == 2026
        ]
        assert len(rels) == 1  # the old link went out, the new came in: still one
        assert rels[0]["p"]["weeks"] == [1, 2, 3] and rels[0]["p"]["last_week"] == 3
        assert diff_graphs(env.graph, env.clean_graph()) == {}  # (the clean graph reads week 3 too)

    def test_a_new_target_replaces_the_old_one(self, env):
        env.append()
        edit(env, GC, 2, receiver_player_id=TE_KC)
        doc = env.append()
        assert doc["changes"] == {
            "TARGETED": {"create": 1, "delete": 1},
            "CAUGHT": {"create": 1, "delete": 1},
        }
        ends = {(r["s"], r["e"]) for r in env.graph.rels["TARGETED"]}
        assert (TE_KC, f"{GC}:2") in ends and (WR_KC, f"{GC}:2") not in ends
        assert diff_graphs(env.graph, env.clean_graph()) == {}

    def test_a_corrected_score_is_one_game_update(self, env):
        env.append()
        for g in env.world.games:
            if g["game_id"] == GC:
                g["home_score"] = 99
        env.world.write()
        assert env.append()["changes"] == {"Game": {"update": 1}}
        assert env.graph.nodes["Game"][GC]["home_score"] == 99

    def test_a_new_week_adds_only_the_new_week(self, env, monkeypatch):
        env.append(through_week=2)
        gid = "2026_03_DEN_LV"
        monkeypatch.setitem(GAME_META, gid, (2026, 3))
        env.world.games.append(game_row(gid, "DEN", "LV", "Sean Payton", "Pete Carroll"))
        env.world.plays[2026] += [
            play(gid, 1, None, None, None, None),
            play(gid, 2, "run", "LV", "DEN", 1, rusher_player_id=RB_LV, yards_gained=3, epa=0.1),
            play(gid, 3, "punt", "LV", "DEN", 1),
        ]
        env.world.write()
        doc = env.append()  # default: the newest week, 3
        assert doc["through_week"] == 3 and doc["weeks"] == [1, 2, 3]
        assert doc["changes"]["Game"] == {"create": 1}
        assert doc["changes"]["Drive"] == {"create": 1}
        assert doc["changes"]["Play"] == {"create": 2}
        for t in ("Play", "Game", "Drive"):
            assert "update" not in doc["changes"][t] and "delete" not in doc["changes"][t]
        assert diff_graphs(env.graph, env.clean_graph()) == {}

    def test_going_back_to_an_earlier_week_removes_the_later_one(self, env):
        env.append(through_week=2)
        doc = env.append(through_week=1)
        assert doc["changes"]["Game"] == {"delete": 1}  # week 2's game GD
        assert doc["changes"]["Drive"] == {"delete": 2} and doc["changes"]["Play"] == {"delete": 3}
        assert GD not in env.graph.nodes["Game"]
        assert {p["week"] for p in env.graph.nodes["Play"].values() if p["season"] == 2026} == {1}
        # the same graph as appending week 1 straight away (shared players / coaches that only
        # week 2 used are left, by design: they may be used by other seasons)
        fr = load(env.world, seasons=(2019, 2024, 2026), through_week=1)
        out = env.paths.root / "csv-wk1"
        other = FakeGraph().load_csv(out, X.write_csvs(fr, out))
        assert diff_graphs(env.graph, other, ignore_unlinked={"Player", "Coach", "Team"}) == {}

    def test_shared_nodes_are_left_when_nothing_uses_them_any_more(self, env):
        env.append(through_week=2)
        n_coach = env.graph.counts()["Coach"]
        env.append(through_week=1)  # interim-joe coached week 2 only
        assert env.graph.counts()["Coach"] == n_coach  # coaches are shared: never deleted

    def test_deletes_run_before_creates(self, env, monkeypatch):
        env.append(through_week=2)
        gid = "2026_03_DEN_LV"
        monkeypatch.setitem(GAME_META, gid, (2026, 3))
        env.world.games.append(game_row(gid, "DEN", "LV", "Sean Payton", "Pete Carroll"))
        env.world.plays[2026] = [r for r in env.world.plays[2026] if r["game_id"] != GD] + [
            play(gid, 2, "run", "LV", "DEN", 1, rusher_player_id=RB_LV, yards_gained=3, epa=0.1)
        ]
        env.world.write()
        n = env.graph.n_writes()
        env.append(through_week=3)
        kinds = [k for k, _ in env.graph.writes[n:]]
        first_create = next(i for i, k in enumerate(kinds) if k.startswith("create"))
        assert all(k.startswith("delete") for k in kinds[:first_create]) and first_create > 0
        # node deletes (Play, Drive, Game) come before any node create
        assert kinds.index("delete Play") < kinds.index("delete Drive") < kinds.index("delete Game")


# ---- failing halfway -------------------------------------------------------------------------


class TestPartialFailure:
    @pytest.mark.parametrize("after", [0, 1, 2, 4, 6, 9, 12, 15, 18])
    def test_a_run_that_dies_halfway_is_finished_by_the_next_one(self, env, after):
        env.graph.fail_after_writes = after
        with pytest.raises(FakeNeo4jError):
            env.append()
        assert not state.append_path(env.paths).exists()  # no append.json for a failed run
        from nflengine.ops.lock import is_locked

        assert not is_locked(state.lock_path(env.paths))  # the lock was released ...
        # ... but the season may be half-written: the marker stays, so Ask counts the graph as
        # busy until a good run (Sol, AE02)
        assert state.read_unfinished(env.paths)["what"] == "append"
        assert state.history_busy(env.paths)
        env.graph.fail_after_writes = None
        doc = env.append()
        assert doc["status"] == "ok" and doc["check"] == {}
        assert state.read_unfinished(env.paths) is None and not state.history_busy(env.paths)
        assert diff_graphs(env.graph, env.clean_graph()) == {}  # no duplicate relationship either
        assert env.append()["changes"] == {}

    def test_a_run_that_dies_in_a_later_update_is_finished_too(self, env):
        env.append()
        edit(env, GC, 2, epa=0.45)
        edit(env, GC, 3, solo_tackle_1_player_id=LB_LV)
        env.graph.fail_after_writes = env.graph.n_writes()  # the very next write fails
        with pytest.raises(FakeNeo4jError):
            env.append()
        env.graph.fail_after_writes = None
        assert env.append()["status"] == "ok"
        assert diff_graphs(env.graph, env.clean_graph()) == {}

    def test_a_failed_run_leaves_the_last_good_append_json(self, env):
        env.append(through_week=1)
        old = state.append_path(env.paths).read_text(encoding="utf-8")
        edit(env, GC, 2, epa=0.45)
        env.graph.fail_after_writes = env.graph.n_writes()
        with pytest.raises(FakeNeo4jError):
            env.append(through_week=2)
        assert state.append_path(env.paths).read_text(encoding="utf-8") == old

    def test_the_graph_that_still_differs_after_the_writes_is_an_error(self, env, monkeypatch):
        # a write the server silently doesn't apply (a MATCH finding no node) must not pass
        real = FakeGraph._write

        def lossy(self, q, params):
            if "CREATE (a)-[x:CAUGHT]->(b)" in q:
                self.writes.append(("link CAUGHT", len(params["rows"])))
                return real.__globals__["_Result"]([])
            return real(self, q, params)

        monkeypatch.setattr(FakeGraph, "_write", lossy)
        with pytest.raises(A.HistoryAppendError, match="still differs from the export.*CAUGHT"):
            env.append()
        doc = json.loads(state.append_path(env.paths).read_text(encoding="utf-8"))
        assert doc["status"] == "failed" and "CAUGHT" in doc["check"]
        assert state.read_append(env.paths) is None  # a failed append doesn't count for Ask


# ---- refusals --------------------------------------------------------------------------------


class TestRefusals:
    def test_no_build_yet(self, env):
        state.build_path(env.paths).unlink()
        with pytest.raises(A.HistoryAppendError, match="no history-graph build yet"):
            env.append()
        assert env.graph.queries == []  # nothing was asked of the server

    def test_a_failed_build_does_not_count(self, env):
        write_build(env.paths, status="failed")
        with pytest.raises(A.HistoryAppendError, match="no history-graph build yet"):
            env.append()

    @pytest.mark.parametrize("season", [2019, 2023, 2024])
    def test_a_bulk_imported_season_is_refused(self, env, season):
        with pytest.raises(A.HistoryAppendError, match="in the bulk import.*rebuild"):
            env.append(season)
        assert env.graph.queries == []

    def test_a_season_without_plays(self, env):
        with pytest.raises(A.HistoryAppendError, match="no curated plays for 2027 yet"):
            env.append(2027)

    def test_a_season_whose_file_has_no_weeks(self, env):
        env.world.plays[2026] = []
        env.world.write()
        with pytest.raises(A.HistoryAppendError, match="no curated plays for 2026"):
            env.append()

    def test_the_weekly_lock_blocks_it(self, env):
        from nflengine.ops.lock import lock_path

        with (
            run_lock(lock_path(env.paths.runs), "nfl weekly run --auto"),
            pytest.raises(A.HistoryAppendError, match="holds the weekly lock"),
        ):
            env.append()
        assert env.graph.queries == [] and env.graph.writes == []

    def test_the_history_lock_blocks_it(self, env):
        with run_lock(state.lock_path(env.paths), "nfl history-graph build"):
            assert state.history_busy(env.paths)
            with pytest.raises(
                A.HistoryAppendError, match="another history-graph build or append is running"
            ):
                env.append()
        assert env.graph.writes == []
        assert env.append()["status"] == "ok"  # released: now it runs

    def test_the_lock_is_released_after_a_success_and_after_an_error(self, env):
        env.append()
        assert not state.history_busy(env.paths)
        with pytest.raises(A.HistoryAppendError):
            env.append(2024)
        assert not state.history_busy(env.paths)

    def test_a_caller_that_holds_the_lock_can_ask_for_locked_mode(self, env):
        from nflengine.ops.lock import lock_path

        with (
            run_lock(state.lock_path(env.paths), "the build"),
            run_lock(lock_path(env.paths.runs), "weekly"),
        ):
            assert env.append(locked=True)["status"] == "ok"  # the build checked both already


# ---- W&B -------------------------------------------------------------------------------------


class FakeRun:
    def __init__(self, **kw):
        self.kw = kw
        self.logged: list[dict] = []
        self.summary = SimpleNamespace(update=lambda d: self.summary_data.update(d))
        self.summary_data: dict = {}
        self.url = "https://wandb.example/run"
        self.exit_code = None

    def log(self, d):
        self.logged.append(d)

    def finish(self, exit_code=None):
        self.exit_code = exit_code


class TestWandb:
    def test_config_is_plain_and_the_run_is_logged(self, env, monkeypatch):
        runs: list[FakeRun] = []
        monkeypatch.setattr(
            "nflengine.tracking.init_run", lambda **kw: runs.append(FakeRun(**kw)) or runs[-1]
        )
        monkeypatch.setattr("nflengine.tracking.git_commit", lambda: "abc1234")
        doc = env.append(use_wandb=True, launched_by="agent")
        (run,) = runs
        assert run.kw["job_type"] == "history-graph-append" and run.kw["group"] == "history-graph"
        assert run.kw["launched_by"] == "agent" and "history-graph" in run.kw["tags"]
        assert set(run.kw["config"]) == {"season", "through_week", "build_id", "git_commit"}
        assert run.kw["config"]["season"] == 2026
        assert run.summary_data["status"] == "ok" and run.summary_data["plays"] == 8
        assert run.exit_code == 0 and doc["wandb_url"] == "https://wandb.example/run"
        assert run.summary_data["writes"] == sum(sum(v.values()) for v in FIRST.values())

    def test_the_smoke_flag_changes_the_group(self, env, monkeypatch):
        runs: list[FakeRun] = []
        monkeypatch.setattr(
            "nflengine.tracking.init_run", lambda **kw: runs.append(FakeRun(**kw)) or runs[-1]
        )
        monkeypatch.setattr("nflengine.tracking.git_commit", lambda: "x")
        env.append(use_wandb=True, smoke=True)
        assert runs[0].kw["group"] == "history-graph-smoke" and "smoke" in runs[0].kw["tags"]

    def test_a_broken_wandb_never_sinks_the_append(self, env, monkeypatch):
        def boom(**kw):
            raise RuntimeError("wandb is down SECRET-LOG-TEXT")

        monkeypatch.setattr("nflengine.tracking.init_run", boom)
        monkeypatch.setattr("nflengine.tracking.git_commit", lambda: "x")
        logs: list[str] = []
        doc = env.append(use_wandb=True, log=logs.append)
        assert doc["status"] == "ok" and "wandb_url" not in doc
        assert any("W&B off for this append (RuntimeError)" in x for x in logs)
        assert not any("SECRET-LOG-TEXT" in x for x in logs)  # the type only

    def test_use_wandb_false_never_touches_tracking(self, env, monkeypatch):
        def boom(**kw):
            raise AssertionError("W&B must not be used")

        monkeypatch.setattr("nflengine.tracking.init_run", boom)
        assert env.append(use_wandb=False)["status"] == "ok"


# ---- reading ---------------------------------------------------------------------------------


class TestNewestWeek:
    def test_newest_week(self, env):
        assert A.newest_week(env.paths, 2026) == 2
        assert A.newest_week(env.paths, 2024) == 2
        assert A.newest_week(env.paths, 2019) == 1

    def test_no_file_is_none(self, env):
        assert A.newest_week(env.paths, 2030) is None


class TestReads:
    def test_read_season_nodes_filters_on_the_season_property(self, env):
        got = A.read_season_nodes(env.graph, "Play", 2019)
        assert set(got) == {f"2019_01_LV_KC:{i}" for i in (2, 3, 4, 5)}

    def test_read_nodes_by_key_in_batches(self, env):
        got = A.read_nodes(env.graph, "Team", ["KC", "LV", "NOPE"], batch=2)
        assert set(got) == {"KC", "LV"}

    def test_owned_relationships_by_who_owns_them(self, env):
        by_start = A.read_owned_rels(env.graph, "OFFENSE", 2019)
        assert set(by_start) == {
            (f"2019_01_LV_KC:{i}", t) for i, t in ((2, "KC"), (3, "KC"), (4, "KC"), (5, "LV"))
        }
        by_end = A.read_owned_rels(env.graph, "RUSHED", 2019)  # owned by the Play at the end
        assert set(by_end) == {(RB_LV, "2019_01_LV_KC:5"), ("00-0002", "2019_01_LV_KC:3")}
        by_prop = A.read_owned_rels(env.graph, "PLAYED_FOR", 2019)  # owned by x.season
        assert {k[2] for k in by_prop} == {2019} and len(by_prop) == 4
        assert A.read_owned_rels(env.graph, "PLAYED_FOR", 2026) == {}


class TestBlankStrings:
    def test_a_blank_string_is_no_property_in_the_append_either(self, env):
        edit(env, GC, 4, pass_length="", pass_location="  ")
        env.append()
        p = env.graph.nodes["Play"][f"{GC}:4"]
        assert "pass_length" not in p and "pass_location" not in p
        assert diff_graphs(env.graph, env.clean_graph()) == {}

    def test_a_blank_string_does_not_make_every_run_an_update(self, env):
        edit(env, GC, 4, pass_length="")
        env.append()
        n = env.graph.n_writes()
        assert env.append()["changes"] == {} and env.graph.n_writes() == n


# ---- Sol's review (AE02): the marker, the start under the lock, a rewind's games -------------


class TestSolFixes:
    def test_a_good_append_leaves_no_marker(self, env):
        env.append()
        assert state.read_unfinished(env.paths) is None

    def test_an_unfinished_build_blocks_the_append(self, env):
        state.mark_unfinished(env.paths, "build", "B1")
        n = env.graph.n_writes()
        with pytest.raises(A.HistoryAppendError, match="didn't finish"):
            env.append()
        assert env.graph.n_writes() == n
        assert state.read_unfinished(env.paths)["what"] == "build"

    def test_an_unfinished_append_is_finished_by_the_next_one(self, env):
        state.mark_unfinished(env.paths, "append", "B1", 2026)
        assert env.append()["status"] == "ok"
        assert state.read_unfinished(env.paths) is None

    @pytest.mark.parametrize("build_id, season", [("B1", 2025), ("B0", 2026), (None, None)])
    def test_only_the_same_unfinished_append_may_be_finished(self, env, build_id, season):
        # Sol's verification: an append of another season (or onto another build) mustn't clear
        # a half-written one
        state.mark_unfinished(env.paths, "append", build_id, season)
        n = env.graph.n_writes()
        with pytest.raises(A.HistoryAppendError, match="didn't finish"):
            env.append()
        assert env.graph.n_writes() == n and state.read_unfinished(env.paths) is not None

    def test_build_json_is_read_under_the_lock(self, env, monkeypatch):
        from nflengine.ops.lock import is_locked

        seen = []
        real = state.read_build
        monkeypatch.setattr(
            state,
            "read_build",
            lambda paths: seen.append(is_locked(state.lock_path(paths))) or real(paths),
        )
        env.append()
        assert seen and all(seen)  # never before the lock (Sol: a build could finish between)

    def test_the_marker_names_the_season_and_the_build(self, env, monkeypatch):
        notes = []
        real = state.mark_unfinished
        monkeypatch.setattr(
            state, "mark_unfinished", lambda *a, **k: notes.append((a, k)) or real(*a, **k)
        )
        env.append()
        ((args, _),) = notes
        assert args[1:] == ("append", "B1", 2026)

    def test_the_start_runs_inside_the_history_lock(self, env, monkeypatch):
        from nflengine.history_graph.docker import ServiceResult
        from nflengine.ops.lock import is_locked

        seen = {}
        monkeypatch.setattr(
            "nflengine.history_graph.client.history_up", lambda cfg=None, timeout=5.0: False
        )

        def fake_start(cfg, log):
            seen["locked"] = is_locked(state.lock_path(env.paths))
            return ServiceResult(True, "started", "history graph started")

        monkeypatch.setattr("nflengine.history_graph.docker.start", fake_start)
        assert env.append(start=True)["status"] == "ok"
        assert seen == {"locked": True}

    def test_a_start_that_fails_stops_the_append_before_any_write(self, env, monkeypatch):
        from nflengine.history_graph.docker import ServiceResult

        monkeypatch.setattr(
            "nflengine.history_graph.client.history_up", lambda cfg=None, timeout=5.0: False
        )
        monkeypatch.setattr(
            "nflengine.history_graph.docker.start",
            lambda cfg, log: ServiceResult(False, "failed", "the Docker engine isn't running"),
        )
        n = env.graph.n_writes()
        with pytest.raises(A.HistoryAppendError, match="Docker engine"):
            env.append(start=True)
        assert env.graph.n_writes() == n and state.read_unfinished(env.paths) is None

    def test_a_rewind_counts_only_the_games_of_the_weeks_kept(self, env):
        inp = X.load_inputs(env.paths, [2026], through_week=1, participation=False, log=QUIET)
        assert inp.player_games.height and inp.player_games["week"].max() <= 1
        env.append(through_week=2)
        env.append(through_week=1)
        fr = load(env.world, seasons=(2019, 2024, 2026), through_week=1)
        want = {(r["s"], r["e"]): r["games"] for r in fr.rels["PLAYED_FOR"].to_dicts()
                if r["season"] == 2026}  # fmt: skip
        got = {
            (k[0], k[1]): p["games"]
            for k, p in A.read_owned_rels(env.graph, "PLAYED_FOR", 2026).items()
        }
        assert got == want
