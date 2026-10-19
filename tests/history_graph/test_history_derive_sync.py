"""The derive's sync (AE03): the graph's person links are read, compared with the computed ones
and only the difference is written (`derive.diff / want_index / have_index / sync`), against the
fake Neo4j of `history_fakes.py`. The links come from the hand-made league (`history_mini.py`).
"""

from __future__ import annotations

import polars as pl
import pytest
from history_fakes import FakeGraph, no_real_docker  # noqa: F401  (the safety net)
from history_mini import PLAYERS, mini_frames, mini_snaps

from nflengine.history_graph import derive as D
from nflengine.history_graph import schema as S

QUIET = lambda *_: None  # noqa: E731


@pytest.fixture(scope="module")
def frames_out():
    res, _ = D.derived_frames(mini_frames(), mini_snaps())
    return res


def new_graph() -> FakeGraph:
    """The people the links join; no links yet."""
    g = FakeGraph()
    for pid, pos in [*PLAYERS, ("zz", "WR")]:
        g.nodes["Player"][pid] = {"player_id": pid, "position": pos}
    for c in ("hc", "oc", "dc", "stc", "both", "ghost"):
        g.nodes["Coach"][c] = {"coach_id": c}
    return g


def link_rows(g: FakeGraph, rtype: str) -> dict[tuple, dict]:
    return {(r["s"], r["e"]): r["p"] for r in g.rels[rtype]}


def synced(frames_out) -> FakeGraph:
    g = new_graph()
    D.sync(g, frames_out, 100, QUIET)
    g.writes.clear()
    return g


class TestDiff:
    def test_create_update_and_delete(self):
        want = {("a", "b"): 1, ("a", "c"): 2, ("a", "d"): 3}
        have = {("a", "b"): 1, ("a", "c"): 9, ("a", "x"): 4}
        assert D.diff(want, have) == {
            "create": [("a", "d")], "update": [("a", "c")], "delete": [("a", "x")]}  # fmt: skip

    def test_equal_indexes_have_nothing_to_do(self):
        idx = {("a", "b"): 1}
        assert D.diff(idx, dict(idx)) == {"create": [], "update": [], "delete": []}

    def test_a_duplicate_in_the_graph_is_never_equal(self):
        # `have_index` marks a pair that has two links with -1: an update (the fingerprint of
        # a real link is never -1... a hash could be, but not for these tuples)
        assert D.diff({("a", "b"): 5}, {("a", "b"): -1})["update"] == [("a", "b")]


class TestFingerprint:
    def test_lists_and_tuples_are_one_value_and_order_counts(self):
        a = D._fingerprint({"seasons": [2024, 2025], "teams": ["KC"]})
        assert a == D._fingerprint({"seasons": (2024, 2025), "teams": ("KC",)})
        assert a != D._fingerprint({"seasons": [2025, 2024], "teams": ["KC"]})

    def test_nulls_and_blank_text_are_dropped_like_the_graph_stores_them(self):
        assert D._fingerprint({"a": 1, "b": None}) == D._fingerprint({"a": 1})
        assert D._fingerprint({"a": 1, "b": float("nan")}) == D._fingerprint({"a": 1})
        assert D._fingerprint({"a": 1, "b": ""}) == D._fingerprint({"a": 1})

    def test_zero_and_false_are_values(self):
        assert D._fingerprint({"a": 0}) != D._fingerprint({})
        assert D._fingerprint({"a": False}) != D._fingerprint({})

    def test_a_changed_value_changes_it(self):
        assert D._fingerprint({"snaps": 5}) != D._fingerprint({"snaps": 6})

    def test_want_index_is_keyed_by_the_pair(self, frames_out):
        idx = D.want_index(frames_out["TEAMMATE"])
        assert len(idx) == frames_out["TEAMMATE"].height
        assert ("o1", "o2") in idx and ("o2", "o1") not in idx


class TestHaveIndex:
    def test_reads_every_link_of_the_type_with_its_properties(self, frames_out):
        g = synced(frames_out)
        idx = D.have_index(g, "TEAMMATE")
        assert idx == D.want_index(frames_out["TEAMMATE"])

    def test_a_second_link_between_the_same_pair_is_marked(self, frames_out):
        g = synced(frames_out)
        g.rels["TEAMMATE"].append(dict(g.rels["TEAMMATE"][0]))
        idx = D.have_index(g, "TEAMMATE")
        key = (g.rels["TEAMMATE"][0]["s"], g.rels["TEAMMATE"][0]["e"])
        assert idx[key] == -1

    def test_a_type_that_isnt_there_yet_is_empty(self):
        assert D.have_index(new_graph(), "COACHED") == {}

    def test_links_with_missing_nodes_are_not_read(self, frames_out):
        g = synced(frames_out)
        del g.nodes["Player"]["o1"]  # a real MATCH finds no link at a node that isn't there
        assert not any("o1" in k for k in D.have_index(g, "TEAMMATE"))


class TestSync:
    def test_a_first_run_creates_every_link(self, frames_out):
        g = new_graph()
        res = D.sync(g, frames_out, 100, QUIET)
        assert res["check"] == {}
        assert res["changes"] == {t: {"create": df.height} for t, df in frames_out.items()}
        for rtype, df in frames_out.items():
            assert len(g.rels[rtype]) == df.height
        # the stored properties are the frame's, nulls dropped, lists as lists
        r = link_rows(g, "TEAMMATE")[("o1", "o2")]
        assert r == {"snaps": 7, "seasons": [2024, 2025], "teams": ["DEN", "KC"],
                     "first_season": 2024, "last_season": 2025, "stint_seasons": [2024, 2025],
                     "stint_teams": ["KC", "DEN"], "stint_snaps": [6, 1]}  # fmt: skip

    def test_a_second_run_writes_nothing(self, frames_out):
        g = synced(frames_out)
        res = D.sync(g, frames_out, 100, QUIET)
        assert res == {"changes": {}, "check": {}}
        assert g.n_writes() == 0
        assert len(g.rels["TEAMMATE"]) == frames_out["TEAMMATE"].height

    def test_a_clean_type_is_read_once_and_a_written_one_twice(self, frames_out):
        g = synced(frames_out)
        g.queries.clear()
        D.sync(g, frames_out, 100, QUIET)
        reads = [q for q in g.queries if "properties(x) AS p" in q]
        assert len(reads) == len(S.DERIVED)  # nothing written: the read before is the check
        # one stale link in one type: that type is read again after writing
        g.rels["TEAMMATE"].append({"s": "o1", "e": "d1", "p": {"snaps": 1}})
        g.queries.clear()
        D.sync(g, frames_out, 100, QUIET)
        reads = [q for q in g.queries if "properties(x) AS p" in q]
        assert len(reads) == len(S.DERIVED) + 1
        assert sum("TEAMMATE" in q for q in reads) == 2

    def test_a_changed_property_is_an_update_old_link_out_new_in(self, frames_out):
        g = synced(frames_out)
        link_rows(g, "TEAMMATE")[("o1", "o3")]["snaps"] = 999  # the graph is out of date
        res = D.sync(g, frames_out, 100, QUIET)
        assert res["changes"] == {"TEAMMATE": {"update": 1}} and res["check"] == {}
        assert g.writes == [("unlink TEAMMATE", 1), ("link TEAMMATE", 1)]  # out, then in
        assert link_rows(g, "TEAMMATE")[("o1", "o3")]["snaps"] == 5
        assert len(g.rels["TEAMMATE"]) == frames_out["TEAMMATE"].height

    def test_a_property_the_graph_lacks_is_an_update(self, frames_out):
        g = synced(frames_out)
        del link_rows(g, "CONNECTION")[("o1", "o3")]["epa"]
        assert D.sync(g, frames_out, 100, QUIET)["changes"] == {"CONNECTION": {"update": 1}}

    def test_a_stale_link_is_deleted(self, frames_out):
        g = synced(frames_out)
        g.rels["COACHED"].append({"s": "hc", "e": "q5", "p": {"snaps": 10}})
        res = D.sync(g, frames_out, 100, QUIET)
        assert res["changes"] == {"COACHED": {"delete": 1}} and res["check"] == {}
        assert g.writes == [("unlink COACHED", 1)]
        assert ("hc", "q5") not in link_rows(g, "COACHED")

    def test_a_duplicate_link_between_the_same_pair_is_replaced_by_one(self, frames_out):
        g = synced(frames_out)
        first = dict(g.rels["OPPONENT"][0])
        g.rels["OPPONENT"].append({"s": first["s"], "e": first["e"], "p": dict(first["p"])})
        before = len(g.rels["OPPONENT"])
        res = D.sync(g, frames_out, 100, QUIET)
        assert res["changes"] == {"OPPONENT": {"update": 1}} and res["check"] == {}
        assert len(g.rels["OPPONENT"]) == before - 1 == frames_out["OPPONENT"].height
        assert g.writes == [("unlink OPPONENT", 1), ("link OPPONENT", 1)]

    def test_the_check_after_writing_finds_what_didnt_stick(self, frames_out):
        g = new_graph()
        g.lossy_types = {"TEAMMATE"}  # the CREATEs of this type silently do nothing
        res = D.sync(g, frames_out, 100, QUIET)
        assert res["check"] == {"TEAMMATE": {"create": frames_out["TEAMMATE"].height}}
        assert res["changes"]["TEAMMATE"] == {"create": frames_out["TEAMMATE"].height}
        assert len(g.rels["CONNECTION"]) == frames_out["CONNECTION"].height  # the rest is fine

    def test_writes_go_in_batches(self, frames_out):
        g = new_graph()
        D.sync(g, {"TEAMMATE": frames_out["TEAMMATE"]}, 3, QUIET)
        assert [n for kind, n in g.writes if kind == "link TEAMMATE"] == [3, 3, 3, 1]

    def test_a_link_to_a_person_the_graph_lacks_is_never_created(self, frames_out):
        g = new_graph()
        del g.nodes["Player"]["o2"]
        res = D.sync(g, {"TEAMMATE": frames_out["TEAMMATE"]}, 100, QUIET)
        # o2's links can't be written (a MATCH finds no node), and the check says so
        assert res["check"]["TEAMMATE"]["create"] == 3  # (o1,o2) (o2,o3) (o2,o4)
        assert not any("o2" in k for k in link_rows(g, "TEAMMATE"))

    def test_every_write_query_is_one_of_the_three_shapes(self, frames_out):
        g = new_graph()
        D.sync(g, frames_out, 100, QUIET)
        g.rels["TEAMMATE"].append({"s": "o1", "e": "d1", "p": {}})
        link_rows(g, "COACHED")[("hc", "q1")]["snaps"] = 1
        D.sync(g, frames_out, 100, QUIET)
        writes = [q for q in g.queries if q.startswith("UNWIND")]
        assert writes and all("DELETE x" in q or "CREATE (a)-[x:" in q for q in writes)
        assert not any("DETACH" in q or "MERGE" in q or "SET n" in q for q in g.queries)

    def test_the_log_says_what_each_type_wrote(self, frames_out):
        lines: list[str] = []
        D.sync(new_graph(), frames_out, 100, lines.append)
        assert len(lines) == len(S.DERIVED)
        assert any(line == "history graph derive: TEAMMATE 10 links, 10 writes" for line in lines)
        lines.clear()
        D.sync(synced(frames_out), frames_out, 100, lines.append)
        assert all(line.endswith(" 0 writes") for line in lines)


class TestPureHelpers:
    def test_summary_keeps_the_part_a_document_needs(self):
        doc = {"status": "ok", "derive_id": "X", "counts": {"TEAMMATE": 1}, "changes": {},
               "seconds": {"total": 1.0}, "check": {}, "dropped": {"a": 1},
               "derived_at": "t"}  # fmt: skip
        assert D.summary(doc) == {"status": "ok", "derive_id": "X", "counts": {"TEAMMATE": 1},
                                  "changes": {}, "seconds": {"total": 1.0}}  # fmt: skip
        assert D.summary(None) is None and D.summary({}) is None

    def test_wandb_metrics(self):
        doc = {"counts": {"TEAMMATE": 9, "COACHED": 8},
               "changes": {"TEAMMATE": {"create": 9}, "COACHED": {"update": 2, "delete": 1}},
               "seconds": {"compute": 0.6, "total": 0.7}}  # fmt: skip
        assert D.wandb_metrics(doc) == {
            "derive/count/TEAMMATE": 9,
            "derive/count/COACHED": 8,
            "derive/writes": 12,
            "derive/time/compute": 0.6,
            "derive/time/total": 0.7,
        }
        assert D.wandb_metrics(None) == {} and D.wandb_metrics({}) == {}
        assert D.wandb_metrics({"counts": {"OPPONENT": 3}}) == {
            "derive/count/OPPONENT": 3, "derive/writes": 0}  # fmt: skip

    def test_the_position_classes_and_matchups_are_what_d147_says(self):
        assert D.MATCHUPS == {"skill": ("db",), "ol": ("dl",), "qb": ("dl", "lb", "db")}
        assert set(D.POSITION_CLASSES) == {"skill", "qb", "ol", "dl", "lb", "db"}
        assert D.UNITS == {"HC": ("off", "def", "st"), "OC": ("off",), "DC": ("def",),
                           "ST": ("st",)}  # fmt: skip
        assert D.STAFF_TYPES == {"HEAD_COACH": "HC", "OFF_COORD": "OC", "DEF_COORD": "DC",
                                 "ST_COORD": "ST"}  # fmt: skip

    def test_the_unit_snaps_sql_follows_the_units_table(self):
        sql = D._unit_snaps_sql()
        assert "WHEN 'HC' THEN sn.off + sn.def + sn.st" in sql
        assert "WHEN 'OC' THEN sn.off " in sql and "WHEN 'DC' THEN sn.def " in sql
        assert "WHEN 'ST' THEN sn.st " in sql

    def test_the_dataframe_helpers_chunk(self):
        df = pl.DataFrame({"s": ["a"] * 5, "e": list("vwxyz")})
        assert [r["e"] for r in D._rows(df, chunk=2)] == list("vwxyz")
