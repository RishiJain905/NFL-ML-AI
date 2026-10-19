"""`lab.run_lab` and the three GDS jobs (AE03) against a scripted driver that returns canned GDS
rows: the refusals, the Cypher that was sent (seeds, one thread, read-only, projections dropped),
the files written and `lab.json`, and W&B (a failing one is ignored). No Neo4j, no real GDS."""

from __future__ import annotations

import json
import re
import sys
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
from history_fakes import no_real_docker  # noqa: F401  (the safety net)
from history_gds_fakes import ScriptedDriver
from test_history_lab_pure import fam_row, members, write_snaps

from nflengine.history_graph import lab as L
from nflengine.history_graph import state
from nflengine.history_graph.connections import people_path
from nflengine.paths import DataPaths

QUIET = lambda *_: None  # noqa: E731
DERIVE = {"status": "ok", "build_id": "B1", "derive_id": "D1", "first_season": 2018,
          "through_season": 2025, "season": 2026, "through_week": 3}  # fmt: skip

# players of the neighbours job: id, name, position, group, snaps (the people index), embedding
PLAYERS = [
    ("p1", "Pat One", "QB", "QB", 5000, [1.0, 0.0, 0.0]),
    ("p2", "Pat Two", "QB", "QB", 4000, [1.0, 1.0, 0.0]),
    ("p3", "Pat Three", "QB", "QB", 3000, [0.0, 1.0, 0.0]),
    ("p4", "Wes Four", "WR", "WR", 2500, [1.0, 0.0, 0.0]),
    ("p5", "Wes Five", "WR", "WR", 2000, [0.0, 0.0, 1.0]),
    ("p6", "Rob Six", "RB", "RB", 90, [1.0, 1.0, 1.0]),  # under 100 snaps: not projected
]


@pytest.fixture
def world(tmp_path):
    paths = DataPaths(tmp_path)
    d = paths.neo4j_history
    d.mkdir(parents=True, exist_ok=True)
    (d / "build.json").write_text(
        json.dumps({"status": "ok", "build_id": "B1", "first_season": 2018, "through_season": 2025})
    )
    (d / "append.json").write_text(json.dumps({"status": "ok", "build_id": "B1", "season": 2026,
                                               "through_week": 3}))  # fmt: skip
    (d / "derive.json").write_text(json.dumps(DERIVE))
    people = pl.DataFrame(
        [(pid, "KC", ["KC"], 2018, 2025, snaps) for pid, _n, _pos, _g, snaps, _e in PLAYERS],
        schema={"id": pl.String, "team": pl.String, "teams": pl.List(pl.String),
                "first_season": pl.Int64, "last_season": pl.Int64, "snaps": pl.Int64},
        orient="row",
    )  # fmt: skip
    people.write_parquet(people_path(paths))
    return paths


def lab_files(paths) -> set[str]:
    d = paths.neo4j_history / "lab"
    return {p.name for p in d.iterdir()} if d.exists() else set()


def neighbours_driver() -> ScriptedDriver:
    d = ScriptedDriver()
    d.on("gds.graph.drop", [{"graphName": "x"}])
    d.on("gds.graph.project", [{"nodes": 5, "rels": 8}])
    d.on(
        "gds.fastRP.stream",
        [
            {"id": pid, "embedding": emb}
            for pid, _n, _pos, _g, snaps, emb in PLAYERS
            if snaps >= 100
        ],
    )
    d.on("gds.fastRP.mutate", [])
    d.on("gds.knn.stream", [
        {"a": "p1", "b": "p4", "similarity": 1.0},
        {"a": "p1", "b": "p2", "similarity": 0.7071},
        {"a": "p1", "b": "p3", "similarity": 0.7071},  # a tie with p2: the id decides
        {"a": "p2", "b": "p1", "similarity": 0.7071},
        {"a": "p4", "b": "p1", "similarity": 1.0},
    ])  # fmt: skip
    d.on("MATCH (n:Player) RETURN 'player' AS kind", [
        {"kind": "player", "id": pid, "name": name, "position": pos, "grp": grp}
        for pid, name, pos, grp, _s, _e in PLAYERS] + [
        {"kind": "coach", "id": "andy-reid", "name": "Andy Reid", "position": "coach",
         "grp": "COACH"}])  # fmt: skip
    d.on(
        "UNWIND $pairs", [{"s": "p1", "e": "p2", "snaps": 900}, {"s": "p1", "e": "p4", "snaps": 0}]
    )
    return d


class TestRefusals:
    def run(self, paths, jobs=None, **kw):
        d = ScriptedDriver()
        with pytest.raises(L.HistoryLabError) as e:
            L.run_lab(jobs, paths=paths, driver=d, use_wandb=False, log=QUIET, **kw)
        assert d.calls == []  # nothing reached the graph
        return str(e.value)

    def test_an_unknown_job(self, world):
        assert self.run(world, ["neighbours", "bogus"]) == (
            "unknown job 'bogus': one of neighbours, families, chemistry"
        )
        assert lab_files(world) == set()  # and nothing was written, not even the known job's

    def test_an_unknown_job_is_named_before_anything_else_is_checked(self, tmp_path):
        paths = DataPaths(tmp_path)  # no build, no derive, nothing
        assert "unknown job 'x'" in self.run(paths, ["x"])

    WEEKLY = (
        "the weekly run (or the injury update / Update play calling) holds the weekly lock: "
        "run the lab after it"
    )

    def test_the_weekly_lock_blocks_the_lab(self, world, monkeypatch):
        monkeypatch.setattr("nflengine.history_graph.build.weekly_lock_held", lambda paths: True)
        assert self.run(world) == self.WEEKLY
        assert lab_files(world) == set()

    def test_the_real_weekly_lock_blocks_it_too(self, world):
        from nflengine.ops.lock import lock_path, run_lock

        with run_lock(lock_path(world.runs), "nfl weekly run --auto"):
            assert self.run(world, ["chemistry"]) == self.WEEKLY

    def test_the_weekly_lock_is_named_before_a_busy_history_graph(self, world, monkeypatch):
        state.mark_unfinished(world, "derive", "B1")  # busy as well
        monkeypatch.setattr("nflengine.history_graph.build.weekly_lock_held", lambda paths: True)
        assert self.run(world) == self.WEEKLY

    def test_an_unknown_job_is_named_before_the_weekly_lock(self, world, monkeypatch):
        monkeypatch.setattr("nflengine.history_graph.build.weekly_lock_held", lambda paths: True)
        assert "unknown job 'x'" in self.run(world, ["x"])

    def test_without_the_weekly_lock_the_lab_runs(self, world, monkeypatch):
        monkeypatch.setattr("nflengine.history_graph.build.weekly_lock_held", lambda paths: False)
        out = L.run_lab(["chemistry"], paths=world, driver=TestChemistry().driver(),
                        use_wandb=False, log=QUIET)  # fmt: skip
        assert out["chemistry"]["status"] == "ok"

    def test_a_busy_graph(self, world):
        from nflengine.ops.lock import run_lock

        with run_lock(state.lock_path(world), "nfl history-graph append"):
            assert "build, append or derive is running" in self.run(world, ["families"])

    def test_an_unfinished_run_is_busy_too(self, world):
        state.mark_unfinished(world, "derive", "B1")
        assert "didn't finish" in self.run(world)

    def test_no_derive_yet(self, world):
        (world.neo4j_history / "derive.json").unlink()
        assert self.run(world) == "no person links yet: run `nfl history-graph derive` first"

    def test_a_failed_derive_is_no_derive(self, world):
        (world.neo4j_history / "derive.json").write_text(json.dumps({**DERIVE, "status": "failed"}))
        assert "no person links yet" in self.run(world)

    def test_a_derive_of_another_build_is_no_derive(self, world):
        (world.neo4j_history / "derive.json").write_text(json.dumps({**DERIVE, "build_id": "OLD"}))
        assert "no person links yet" in self.run(world)

    def test_no_build_at_all(self, tmp_path):
        assert "no person links yet" in self.run(DataPaths(tmp_path))


class TestNeighbours:
    def run(self, world, **kw):
        d = neighbours_driver()
        out = L.run_lab(["neighbours"], paths=world, driver=d, use_wandb=False, log=QUIET, **kw)
        return d, out["neighbours"]

    def test_the_entry(self, world):
        d, e = self.run(world)
        assert e["status"] == "ok" and e["job"] == "neighbours" and e["derive_id"] == "D1"
        assert (e["build_id"], e["through_season"], e["season"], e["through_week"]) == (
            "B1", 2025, 2026, 3)  # fmt: skip
        assert e["ran_at"].startswith("2026-")
        assert e["players"] == 5  # the ids GDS returned (p6 had under 100 snaps)
        assert e["projection"] == {"nodes": 5, "rels": 8}
        assert e["params"] == {**L.NEIGHBOURS, "seed": 42}
        assert set(e["seconds"]) == {"fastrp", "knn", "total"}
        assert len(e["explained_variance_2d"]) == 2 and 0 < sum(e["explained_variance_2d"]) <= 1
        # 5 overall rows (what the KNN gave) + 6 for the three QBs and 2 for the two WRs, exact
        assert "_frames" not in e and e["rows"] == 13

    def test_the_files_are_written_and_listed(self, world):
        self.run(world)
        assert lab_files(world) == {"neighbours.parquet", "neighbours_map.parquet", "lab.json"}
        lab = json.loads((world.neo4j_history / "lab" / "lab.json").read_text())
        assert set(lab) == {"neighbours"} and lab["neighbours"]["derive_id"] == "D1"
        assert L.read_lab(world)["neighbours"]["players"] == 5

    def test_the_neighbours_table_by_hand(self, world):
        self.run(world)
        nb = pl.read_parquet(world.neo4j_history / "lab" / "neighbours.parquet")
        assert nb.columns == ["scope", "player_id", "rank", "neighbour_id", "similarity",
                              "snaps_together", "name", "position", "grp", "team", "snaps",
                              "neighbour_name", "neighbour_position", "neighbour_grp",
                              "neighbour_team", "neighbour_snaps"]  # fmt: skip
        over = {(r["player_id"], r["rank"]): r for r in nb.filter(pl.col("scope") == "overall")
                .to_dicts()}  # fmt: skip
        # p1's overall neighbours: p4 (1.0), then the tie p2 / p3 at 0.7071 by id
        assert [over[("p1", i)]["neighbour_id"] for i in (1, 2, 3)] == ["p4", "p2", "p3"]
        assert over[("p1", 1)]["similarity"] == 1.0 and over[("p1", 2)]["similarity"] == 0.7071
        # snaps together come from the TEAMMATE links GDS's pairs asked about: p1-p2 900, p1-p4
        # 0 (a TEAMMATE row with 0), p1-p3 none (never together): 0
        assert over[("p1", 2)]["snaps_together"] == 900
        assert over[("p1", 3)]["snaps_together"] == 0
        # the link is read both ways: p2's neighbour p1 has the same 900
        assert over[("p2", 1)]["snaps_together"] == 900
        # names, positions and the people index's team / snaps are joined on both ends
        r = over[("p1", 2)]
        assert (r["name"], r["position"], r["grp"], r["team"], r["snaps"]) == (
            "Pat One", "QB", "QB", "KC", 5000)  # fmt: skip
        assert (r["neighbour_name"], r["neighbour_grp"], r["neighbour_snaps"]) == (
            "Pat Two", "QB", 4000)  # fmt: skip

    def test_the_position_scope_is_exact_and_within_the_group(self, world):
        self.run(world)
        nb = pl.read_parquet(world.neo4j_history / "lab" / "neighbours.parquet")
        pos = nb.filter(pl.col("scope") == "position")
        # QBs p1..p3 and WRs p4, p5 (p6 isn't projected): each has the others of his group only
        assert set(zip(pos["player_id"], pos["neighbour_id"], strict=True)) == {
            ("p1", "p2"), ("p1", "p3"), ("p2", "p1"), ("p2", "p3"), ("p3", "p1"), ("p3", "p2"),
            ("p4", "p5"), ("p5", "p4")}  # fmt: skip
        assert pos.filter(pl.col("player_id") == pl.col("neighbour_id")).height == 0
        p1 = pos.filter(pl.col("player_id") == "p1").sort("rank")
        assert p1["neighbour_id"].to_list() == ["p2", "p3"] and p1["similarity"][0] == 0.7071
        assert p1["rank"].to_list() == [1, 2]

    def test_the_map_is_the_pca_of_the_embeddings(self, world):
        self.run(world)
        mp = pl.read_parquet(world.neo4j_history / "lab" / "neighbours_map.parquet")
        assert mp["player_id"].to_list() == ["p1", "p2", "p3", "p4", "p5"]
        emb = np.array([p[5] for p in PLAYERS[:5]])
        want = L._pca2(emb)
        assert np.allclose(mp["x"].to_numpy(), want[:, 0].round(4))
        assert np.allclose(mp["y"].to_numpy(), want[:, 1].round(4))
        assert {"name", "position", "grp", "team", "snaps"} <= set(mp.columns)
        assert mp.filter(pl.col("player_id") == "p4")["grp"][0] == "WR"

    def test_coaches_are_not_in_the_players_tables(self, world):
        self.run(world)
        nb = pl.read_parquet(world.neo4j_history / "lab" / "neighbours.parquet")
        assert "andy-reid" not in set(nb["player_id"]) | set(nb["neighbour_id"])

    def test_the_projection_keeps_only_players_with_enough_snaps_and_is_dropped_after(self, world):
        d, _ = self.run(world)
        (proj,) = d.params_of("gds.graph.project")
        assert sorted(proj["keep"]) == ["p1", "p2", "p3", "p4", "p5"] and proj["cw"] == 3.0
        assert proj["n"] == "lab-neighbours-D1"
        drops = [p["n"] for p in d.params_of("gds.graph.drop")]
        # before it, after the use, and `run_lab`'s guaranteed drop (a no-op here)
        assert drops == ["lab-neighbours-D1"] * 3
        order = [q for q, _ in d.calls]
        i_knn = next(i for i, q in enumerate(order) if "gds.knn.stream" in q)
        i_drops = [i for i, q in enumerate(order) if "gds.graph.drop" in q]
        assert i_drops[1] == i_knn + 1  # the job drops it as soon as the KNN has answered

    def test_the_seed_runs_every_random_step_on_one_thread(self, world):
        d, e = self.run(world, seed=7)
        for step in ("gds.fastRP.stream", "gds.fastRP.mutate", "gds.knn.stream"):
            (q,) = d.queries(step)
            assert "randomSeed: $seed" in q and "concurrency: 1" in q
            assert d.params_of(step)[0]["seed"] == 7
        assert e["params"]["seed"] == 7
        assert d.params_of("gds.fastRP.stream")[0]["dim"] == 128

    def test_the_default_seed_is_42(self, world):
        d, e = self.run(world)
        assert d.params_of("gds.knn.stream")[0]["seed"] == 42 == L.SEED == e["params"]["seed"]

    def test_the_pairs_asked_for_snaps_together_are_ordered_and_unique(self, world):
        d, _ = self.run(world)
        (pairs,) = d.params_of("UNWIND $pairs")
        got = pairs["pairs"]
        assert all(p["s"] < p["e"] for p in got)
        assert len({(p["s"], p["e"]) for p in got}) == len(got)
        assert {("p1", "p2"), ("p1", "p4"), ("p1", "p3")} <= {(p["s"], p["e"]) for p in got}

    def test_the_database_is_only_read(self, world):
        d, _ = self.run(world)
        text = "\n".join(q for q, _ in d.calls)
        assert not re.search(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP)\b", text)
        assert "gds.graph.project" in text  # (the in-memory catalog is not the database)

    def test_no_write_query_runs_with_the_gds_write_mode(self, world):
        d, _ = self.run(world)
        assert not [q for q, _ in d.calls if ".write(" in q or "Write" in q]

    def test_a_driver_error_leaves_no_files_and_the_projection_is_the_callers_problem(self, world):
        d = neighbours_driver()
        d.rules.insert(0, ("gds.knn.stream", RuntimeError("GDS exploded")))
        with pytest.raises(RuntimeError):
            L.run_lab(["neighbours"], paths=world, driver=d, use_wandb=False, log=QUIET)
        assert lab_files(world) == set()


def family_world(tmp_path):
    """The league of test_history_lab_pure with the files `run_lab` needs."""
    from test_history_lab_pure import A, write_state

    paths = DataPaths(tmp_path)
    write_state(paths)
    (paths.neo4j_history / "derive.json").write_text(json.dumps(DERIVE))
    rows = [(p, "KC", s, 1, n, 0, 0) for p, s, n in A]
    for pid in ["d1", "d2", "d3", "d4", "d5"]:
        rows += [(pid, "DEN", 2020, 1, 0, 500, 0), (pid, "DEN", 2021, 1, 0, 400, 100)]
    write_snaps(paths, rows)
    return paths


COMMUNITIES = (
    [(1, "player", p) for p in ("a1", "a2", "a3", "a4")]
    + [(1, "coach", "c1")]
    + [(2, "player", p) for p in ["d1", "d2", "d3", "d4", "d5"]]
    + [(9, "player", "z1"), (9, "player", "z2")]
)


def families_driver(communities=COMMUNITIES) -> ScriptedDriver:
    m = members(*communities)
    d = ScriptedDriver()
    d.on("gds.graph.drop", [{"graphName": "x"}])
    d.on("gds.graph.project", [{"nodes": 12, "rels": 40}])
    d.on("gds.leiden.stream", [{"kind": r["kind"], "id": r["id"], "community": r["community"]}
                               for r in m.to_dicts()])  # fmt: skip
    d.on("gds.leiden.stats", [{"communityCount": 3, "modularity": 0.912345, "ranLevels": 4}])
    d.on("MATCH (n:Player) RETURN 'player' AS kind", [
        {"kind": r["kind"], "id": r["id"], "name": r["name"], "position": "WR", "grp": "WR"}
        for r in m.to_dicts()])  # fmt: skip
    return d


class TestFamilies:
    def run(self, tmp_path, **kw):
        paths = family_world(tmp_path)
        d = families_driver()
        out = L.run_lab(["families"], paths=paths, driver=d, use_wandb=False, log=QUIET, **kw)
        return paths, d, out["families"]

    def test_the_entry(self, tmp_path):
        _, _, e = self.run(tmp_path)
        assert e["job"] == "families" and e["status"] == "ok"
        assert e["communities"] == 3 and e["modularity"] == 0.9123 and e["levels"] == 4
        assert e["projection"] == {"nodes": 12, "rels": 40}
        # communities of five or more members: 1 (5 members), 2 (5); community 9 has two
        assert e["families"] == 2 and e["largest"] == 5 and e["median_size"] == 5.0
        assert e["params"] == {**L.FAMILIES, "seed": 42}
        assert set(e["seconds"]) == {"leiden", "total"}

    def test_the_files(self, tmp_path):
        paths, _, _ = self.run(tmp_path)
        assert lab_files(paths) == {"families.parquet", "family_members.parquet", "lab.json"}
        fam = pl.read_parquet(paths.neo4j_history / "lab" / "families.parquet")
        assert fam["name"].to_list() == ["2019-2022 KC offense", "2020-2021 DEN defense"]
        assert fam_row(fam, 1)["size"] == 5
        mem = pl.read_parquet(paths.neo4j_history / "lab" / "family_members.parquet")
        assert set(mem["community"]) == {1, 2}  # the small community's members are not kept
        assert {"snaps", "snaps_total", "kind", "id", "name"} <= set(mem.columns)

    def test_the_cypher_is_seeded_single_threaded_and_labelled(self, tmp_path):
        _, d, e = self.run(tmp_path, seed=11)
        for step in ("gds.leiden.stream", "gds.leiden.stats"):
            (q,) = d.queries(step)
            assert "randomSeed: $seed" in q and "concurrency: 1" in q and "gamma: $gamma" in q
            p = d.params_of(step)[0]
            assert p["seed"] == 11 and p["gamma"] == 16.0 and p["levels"] == 10
            assert p["theta"] == 0.01
        (proj,) = d.queries("gds.graph.project")
        assert "MATCH (a:Player|Coach)-[r:TEAMMATE|COACHED|WORKED_WITH]->(b)" in proj
        assert "log10(1.0 + toFloat(coalesce(r.snaps, 0)))" in proj
        assert [p["n"] for p in d.params_of("gds.graph.drop")] == ["lab-families-D1"] * 3
        assert e["params"]["seed"] == 11

    def test_no_snap_file_fails_the_job_cleanly(self, tmp_path):
        paths = family_world(tmp_path)
        (paths.curated / "snaps.parquet").unlink()
        with pytest.raises(FileNotFoundError):
            L.run_lab(["families"], paths=paths, driver=families_driver(), use_wandb=False,
                      log=QUIET)  # fmt: skip
        assert "families.parquet" not in lab_files(paths) and "lab.json" not in lab_files(paths)


class TestChemistry:
    DUOS = [
        {"qb_id": "q1", "qb": "QB One", "receiver_id": "r1", "receiver": "Rec One",
         "position": "WR", "targets": 200, "catches": 130, "yards": 1600, "touchdowns": 12,
         "epa": 40.0, "seasons": [2019, 2020], "teams": ["KC"]},
        {"qb_id": "q1", "qb": "QB One", "receiver_id": "r2", "receiver": "Rec Two",
         "position": "TE", "targets": 150, "catches": 90, "yards": 900, "touchdowns": 6,
         "epa": 30.0, "seasons": [2021], "teams": ["KC"]},
        {"qb_id": "q2", "qb": "QB Two", "receiver_id": "r1", "receiver": "Rec One",
         "position": "WR", "targets": 300, "catches": 150, "yards": 1800, "touchdowns": 9,
         "epa": 60.0, "seasons": [2022, 2023], "teams": ["DEN"]},
        {"qb_id": "q2", "qb": "QB Two", "receiver_id": "r3", "receiver": "Rec Three",
         "position": "RB", "targets": 100, "catches": 60, "yards": 400, "touchdowns": 1,
         "epa": -5.0, "seasons": [2022], "teams": ["DEN"]},
    ]  # fmt: skip
    RANKS = [
        {"player_id": "r1", "name": "Rec One", "position": "WR", "grp": "WR", "score": 2.123456,
         "receivers": 0, "qbs": 2},
        {"player_id": "r2", "name": "Rec Two", "position": "TE", "grp": "TE", "score": 0.5,
         "receivers": 0, "qbs": 1},
        {"player_id": "q1", "name": "QB One", "position": "QB", "grp": "QB", "score": 0.3,
         "receivers": 2, "qbs": 0},
        {"player_id": "q2", "name": "QB Two", "position": "QB", "grp": "QB", "score": 0.9,
         "receivers": 2, "qbs": 0},
        {"player_id": "r3", "name": "Rec Three", "position": "RB", "grp": "RB", "score": 0.5,
         "receivers": 0, "qbs": 1},
    ]  # fmt: skip

    def driver(self):
        d = ScriptedDriver()
        d.on("gds.graph.drop", [{"graphName": "x"}])
        d.on("MATCH (q:Player)-[c:CONNECTION]->(r:Player) WHERE c.targets >= $m", self.DUOS)
        d.on("gds.graph.project", [{"nodes": 5, "rels": 4}])
        d.on("gds.pageRank.stream", self.RANKS)
        return d

    def run(self, world, **kw):
        d = self.driver()
        out = L.run_lab(["chemistry"], paths=world, driver=d, use_wandb=False, log=QUIET, **kw)
        return d, out["chemistry"]

    def test_the_duos_ranked_by_epa_per_target(self, world):
        self.run(world)
        duos = pl.read_parquet(world.neo4j_history / "lab" / "chemistry_duos.parquet")
        # epa per target: q2-r1 60 / 300, q1-r1 40 / 200 and q1-r2 30 / 150 are all 0.2 (ties:
        # more targets first), q2-r3 -5 / 100 = -0.05
        want = [("q2", "r1", 0.2, 1), ("q1", "r1", 0.2, 2), ("q1", "r2", 0.2, 3),
                ("q2", "r3", -0.05, 4)]  # fmt: skip
        assert list(zip(duos["qb_id"], duos["receiver_id"], duos["epa_per_target"], duos["rank"],
                        strict=True)) == want  # fmt: skip
        r = duos.filter((pl.col("qb_id") == "q1") & (pl.col("receiver_id") == "r1")).to_dicts()[0]
        assert r["catch_rate"] == 0.65 and r["yards_per_target"] == 8.0
        assert (r["first_season"], r["last_season"]) == (2019, 2020)
        assert r["touchdowns"] == 12 and r["teams"] == ["KC"]

    def test_the_minimum_volume_is_a_parameter_of_the_query(self, world):
        d, e = self.run(world)
        assert d.params_of("MATCH (q:Player)-[c:CONNECTION]->(r:Player)")[0] == {"m": 100}
        assert e["params"] == {**L.CHEMISTRY, "seed": 42} and e["duos"] == 4

    def test_pagerank_by_role(self, world):
        d, e = self.run(world)
        pr = pl.read_parquet(world.neo4j_history / "lab" / "chemistry_pagerank.parquet")
        assert e["ranked"] == 5 and "role" in pr.columns
        roles = {r["player_id"]: (r["role"], r["rank"], r["score"]) for r in pr.to_dicts()}
        # quarterbacks (position group QB) and receivers are ranked apart, by score
        assert roles["q2"] == ("qb", 1, 0.9) and roles["q1"] == ("qb", 2, 0.3)
        assert roles["r1"] == ("receiver", 1, 2.12346)  # the score is rounded to 5 places
        assert {roles["r2"][1], roles["r3"][1]} == {2, 3}  # a tie (0.5): ordinal, both ranked
        assert roles["r2"][0] == roles["r3"][0] == "receiver"
        p = d.params_of("gds.pageRank.stream")[0]
        assert p == {"n": "lab-chemistry-D1", "d": 0.85, "it": 40}
        (q,) = d.queries("gds.pageRank.stream")
        assert "concurrency: 1" in q and "relationshipWeightProperty: 'w'" in q

    def test_the_projection_is_the_connection_graph_weighted_by_targets(self, world):
        d, _ = self.run(world)
        (q,) = d.queries("gds.graph.project")
        assert "MATCH (a:Player)-[r:CONNECTION]->(b:Player)" in q and "toFloat(r.targets)" in q
        assert [p["n"] for p in d.params_of("gds.graph.drop")] == ["lab-chemistry-D1"] * 3

    def test_the_files(self, world):
        self.run(world)
        assert lab_files(world) == {"chemistry_duos.parquet", "chemistry_pagerank.parquet",
                                    "lab.json"}  # fmt: skip


class TestRunLab:
    def test_a_run_of_one_job_keeps_the_others_entries(self, world):
        L.run_lab(["neighbours"], paths=world, driver=neighbours_driver(), use_wandb=False,
                  log=QUIET)  # fmt: skip
        d = TestChemistry().driver()
        L.run_lab(["chemistry"], paths=world, driver=d, use_wandb=False, log=QUIET)
        lab = L.read_lab(world)
        assert set(lab) == {"neighbours", "chemistry"}
        assert L.lab_status(world).keys() == {"neighbours", "chemistry"}

    def test_a_rerun_replaces_the_entry(self, world):
        for _ in range(2):
            L.run_lab(["neighbours"], paths=world, driver=neighbours_driver(), use_wandb=False,
                      log=QUIET)  # fmt: skip
        assert list(L.read_lab(world)) == ["neighbours"]

    def test_jobs_run_in_the_order_given(self, world, monkeypatch):
        order = []
        monkeypatch.setitem(
            L._JOB_FUNCS, "neighbours", lambda *a, **k: order.append("n") or {"seconds": {}}
        )
        monkeypatch.setitem(
            L._JOB_FUNCS, "chemistry", lambda *a, **k: order.append("c") or {"seconds": {}}
        )
        L.run_lab(["chemistry", "neighbours"], paths=world, driver=ScriptedDriver(),
                  use_wandb=False, log=QUIET)  # fmt: skip
        assert order == ["c", "n"]

    def test_all_jobs_are_the_default(self, world, monkeypatch):
        seen = []
        for job in L.JOBS:
            monkeypatch.setitem(L._JOB_FUNCS, job, lambda *a, _j=job, **k: seen.append(_j)
                                or {"seconds": {}})  # fmt: skip
        L.run_lab(paths=world, driver=ScriptedDriver(), use_wandb=False, log=QUIET)
        assert seen == list(L.JOBS)

    def test_the_driver_it_opens_it_closes(self, world, monkeypatch):
        d = neighbours_driver()
        monkeypatch.setattr("nflengine.history_graph.client.get_history_driver",
                            lambda cfg=None, **k: d)  # fmt: skip
        L.run_lab(["neighbours"], paths=world, use_wandb=False, log=QUIET)
        assert d.closed

    def test_a_driver_it_was_given_stays_open(self, world):
        d = neighbours_driver()
        L.run_lab(["neighbours"], paths=world, driver=d, use_wandb=False, log=QUIET)
        assert not d.closed

    def test_the_lab_is_current_for_the_derive_it_ran_on(self, world):
        L.run_lab(["neighbours"], paths=world, driver=neighbours_driver(), use_wandb=False,
                  log=QUIET)  # fmt: skip
        assert L.read_lab(world)["neighbours"]["derive_id"] == "D1"


class TestTheGraphHoldsStill:
    """Sol's AE03 review: one lab or benchmark at a time (their own lock, not the history
    lock), results written only if the person links didn't change during the job, and a job's
    projection dropped even when it fails."""

    def test_a_second_lab_is_refused_while_one_runs(self, world):
        from nflengine.ops.lock import run_lock

        d = ScriptedDriver()
        with (
            run_lock(state.lab_lock_path(world), "nfl history-graph bench-paths"),
            pytest.raises(L.HistoryLabError, match="another lab or path benchmark"),
        ):
            L.run_lab(["chemistry"], paths=world, driver=d, use_wandb=False, log=QUIET)
        assert d.calls == [] and lab_files(world) == set()

    def test_ask_keeps_answering_while_the_lab_runs(self, world, monkeypatch):
        from nflengine.ops.lock import is_locked

        seen = []

        def job(*a, **k):
            seen.append((state.history_busy(world), is_locked(state.lab_lock_path(world))))
            return {"seconds": {}}

        monkeypatch.setitem(L._JOB_FUNCS, "chemistry", job)
        L.run_lab(["chemistry"], paths=world, driver=ScriptedDriver(), use_wandb=False, log=QUIET)
        assert seen == [(False, True)]  # not busy for Ask; the lab's own lock held
        assert not is_locked(state.lab_lock_path(world))  # and released after

    @pytest.mark.parametrize("change", ["new_derive", "derive_running"])
    def test_a_derive_during_the_job_means_nothing_is_written(self, world, monkeypatch, change):
        real = L._JOB_FUNCS["chemistry"]

        def job(*a, **k):
            res = real(*a, **k)
            if change == "new_derive":
                (world.neo4j_history / "derive.json").write_text(
                    json.dumps({**DERIVE, "derive_id": "D2"})
                )
            else:
                state.mark_unfinished(world, "derive", "B1")
            return res

        monkeypatch.setitem(L._JOB_FUNCS, "chemistry", job)
        with pytest.raises(L.HistoryLabError) as e:
            L.run_lab(["chemistry"], paths=world, driver=TestChemistry().driver(),
                      use_wandb=False, log=QUIET)  # fmt: skip
        assert str(e.value) == L.CHANGED_TEXT
        assert lab_files(world) == set()  # no parquet, no lab.json

    def test_an_earlier_jobs_results_stay_when_a_later_one_sees_a_change(self, world, monkeypatch):
        real = L._JOB_FUNCS["chemistry"]

        def job(*a, **k):
            (world.neo4j_history / "derive.json").write_text(
                json.dumps({**DERIVE, "derive_id": "D2"})
            )
            return real(*a, **k)

        monkeypatch.setitem(L._JOB_FUNCS, "chemistry", job)
        d = neighbours_driver()
        d.on("MATCH (q:Player)-[c:CONNECTION]->(r:Player) WHERE c.targets >= $m",
             TestChemistry.DUOS)  # fmt: skip
        d.on("gds.pageRank.stream", TestChemistry.RANKS)
        with pytest.raises(L.HistoryLabError):
            L.run_lab(["neighbours", "chemistry"], paths=world, driver=d, use_wandb=False,
                      log=QUIET)  # fmt: skip
        assert set(L.read_lab(world)) == {"neighbours"}  # D1's, honestly labelled
        assert "chemistry_duos.parquet" not in lab_files(world)

    def test_a_failed_job_still_drops_its_projection(self, world, monkeypatch):
        def job(session, *a, **k):
            session.run("CALL gds.graph.project('lab-chemistry-D1', ...)").consume()
            raise RuntimeError("GDS ran out of memory")

        monkeypatch.setitem(L._JOB_FUNCS, "chemistry", job)
        d = ScriptedDriver()
        d.on("gds.graph.drop", [{"graphName": "x"}])
        d.on("gds.graph.project", [{"nodes": 5, "rels": 4}])
        with pytest.raises(RuntimeError):
            L.run_lab(["chemistry"], paths=world, driver=d, use_wandb=False, log=QUIET)
        assert [p["n"] for p in d.params_of("gds.graph.drop")] == ["lab-chemistry-D1"]
        assert lab_files(world) == set()


class FakeRun:
    def __init__(self, **kw):
        self.kw = kw
        self.logged: list[dict] = []
        self.summary_data: dict = {}
        self.summary = SimpleNamespace(update=lambda d: self.summary_data.update(d))
        self.url = "https://wandb.example/lab"
        self.finished = False

    def log(self, d):
        self.logged.append(d)

    def finish(self, exit_code=None):
        self.finished = True


class TestWandb:
    @pytest.fixture
    def wb(self, monkeypatch):
        runs: list[FakeRun] = []
        monkeypatch.setattr("nflengine.tracking.init_run",
                            lambda **kw: runs.append(FakeRun(**kw)) or runs[-1])  # fmt: skip
        monkeypatch.setattr("nflengine.tracking.git_commit", lambda: "abc1234")
        tables: list = []
        plots: list = []
        fake = SimpleNamespace(
            Table=lambda dataframe=None: tables.append(dataframe) or ("table", len(tables)),
            plot=SimpleNamespace(
                scatter=lambda t, x, y, title=None: plots.append(("scatter", x, y, title)) or "s",
                bar=lambda t, label, value, title=None: (
                    plots.append(("bar", label, value, title)) or "b"
                ),
            ),
        )
        monkeypatch.setitem(sys.modules, "wandb", fake)
        return SimpleNamespace(runs=runs, tables=tables, plots=plots)

    def test_a_neighbours_run(self, world, wb):
        out = L.run_lab(["neighbours"], paths=world, driver=neighbours_driver(), use_wandb=True,
                        launched_by="agent", log=QUIET)  # fmt: skip
        (run,) = wb.runs
        assert run.kw["job_type"] == "history-graph-lab" and run.kw["group"] == "history-graph"
        assert run.kw["tags"] == ["history-graph", "lab", "neighbours"]
        assert run.kw["name"] == "history-graph-lab-neighbours" and run.kw["launched_by"] == "agent"
        cfg = run.kw["config"]
        assert cfg["job"] == "neighbours" and cfg["derive_id"] == "D1" and cfg["seed"] == 42
        assert cfg["dimension"] == 128 and cfg["git_commit"] == "abc1234"
        keys = {k for d in run.logged for k in d}
        assert keys == {"neighbours/neighbours", "neighbours/map", "neighbours/map_2d"}
        assert wb.plots == [("scatter", "x", "y", "Players, FastRP -> PCA")]
        assert run.summary_data["players"] == 5 and "time/fastrp" in run.summary_data
        assert "season" not in run.summary_data  # the number a run summary doesn't need
        assert run.finished and out["neighbours"]["wandb_url"] == "https://wandb.example/lab"
        assert L.read_lab(world)["neighbours"]["wandb_url"] == "https://wandb.example/lab"

    def test_a_families_run_logs_a_bar_chart_of_sizes(self, tmp_path, wb):
        paths = family_world(tmp_path)
        L.run_lab(["families"], paths=paths, driver=families_driver(), use_wandb=True, log=QUIET)
        (run,) = wb.runs
        assert {k for d in run.logged for k in d} == {"families/families", "families/members",
                                                      "families/sizes"}  # fmt: skip
        assert wb.plots == [("bar", "name", "size", "Football families by size")]
        assert run.summary_data["modularity"] == 0.9123 and run.summary_data["time/leiden"] >= 0

    def test_a_chemistry_run_logs_both_tables(self, world, wb):
        L.run_lab(["chemistry"], paths=world, driver=TestChemistry().driver(), use_wandb=True,
                  log=QUIET)  # fmt: skip
        (run,) = wb.runs
        assert {k for d in run.logged for k in d} == {"chemistry/duos", "chemistry/pagerank"}
        assert wb.plots == []

    def test_smoke_runs_go_to_the_smoke_group(self, world, wb):
        L.run_lab(["chemistry"], paths=world, driver=TestChemistry().driver(), use_wandb=True,
                  smoke=True, log=QUIET)  # fmt: skip
        assert wb.runs[0].kw["group"] == "history-graph-smoke" and "smoke" in wb.runs[0].kw["tags"]

    def test_tables_hold_text_not_lists(self, world, wb):
        L.run_lab(["chemistry"], paths=world, driver=TestChemistry().driver(), use_wandb=True,
                  log=QUIET)  # fmt: skip
        duos = wb.tables[0]
        assert not any(str(t).startswith("list") for t in duos.dtypes.astype(str))  # no list column
        assert duos["teams"].tolist()[0] in ("KC", "DEN")

    def test_a_broken_wandb_never_sinks_a_job(self, world, monkeypatch):
        def boom(**kw):
            raise RuntimeError("wandb is down SECRET-LOG-TEXT")

        monkeypatch.setattr("nflengine.tracking.init_run", boom)
        monkeypatch.setattr("nflengine.tracking.git_commit", lambda: "x")
        lines: list[str] = []
        out = L.run_lab(["chemistry"], paths=world, driver=TestChemistry().driver(),
                        use_wandb=True, log=lines.append)  # fmt: skip
        assert out["chemistry"]["status"] == "ok" and out["chemistry"]["wandb_url"] is None
        assert any("W&B off for lab chemistry (RuntimeError)" in x for x in lines)
        assert not any("SECRET-LOG-TEXT" in x for x in lines)
        assert "chemistry_duos.parquet" in lab_files(world)  # the files were written
        assert L.read_lab(world)["chemistry"]["wandb_url"] is None

    def test_a_run_that_cannot_log_is_ignored(self, world, wb, monkeypatch):
        def bad_log(self_, d):
            raise RuntimeError("cannot log")

        monkeypatch.setattr(FakeRun, "log", bad_log)
        out = L.run_lab(["chemistry"], paths=world, driver=TestChemistry().driver(),
                        use_wandb=True, log=QUIET)  # fmt: skip
        assert out["chemistry"]["status"] == "ok" and out["chemistry"]["wandb_url"] is None

    def test_no_wandb_means_no_run(self, world, wb):
        L.run_lab(["chemistry"], paths=world, driver=TestChemistry().driver(), use_wandb=False,
                  log=QUIET)  # fmt: skip
        assert wb.runs == []
