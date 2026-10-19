"""The lab's own computations without GDS (AE03): naming football families, the exact
same-position nearest neighbours, the 2-D PCA and the small helpers (`lab.py`). Counts are worked
out by hand in the comments."""

from __future__ import annotations

import json

import numpy as np
import polars as pl
import pytest
from history_fakes import no_real_docker  # noqa: F401  (the safety net)

from nflengine.history_graph import lab as L
from nflengine.paths import DataPaths

SNAP_SCHEMA = {"gsis_id": pl.String, "game_id": pl.String, "team": pl.String,
               "season": pl.Int32, "week": pl.Int32, "offense_snaps": pl.Int32,
               "defense_snaps": pl.Int32, "st_snaps": pl.Int32}  # fmt: skip


def write_snaps(paths: DataPaths, rows) -> None:
    out = []
    for i, (pid, team, season, week, off, dfn, st) in enumerate(rows):
        out.append((pid, f"g{i}", team, season, week, off, dfn, st))
    paths.curated.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(out, schema=SNAP_SCHEMA, orient="row").write_parquet(
        paths.curated / "snaps.parquet"
    )


def write_state(paths: DataPaths, build=True, append=True) -> None:
    d = paths.neo4j_history
    d.mkdir(parents=True, exist_ok=True)
    if build:
        (d / "build.json").write_text(
            json.dumps({"status": "ok", "build_id": "B1", "first_season": 2018,
                        "through_season": 2025}), encoding="utf-8")  # fmt: skip
    if append:
        (d / "append.json").write_text(
            json.dumps({"status": "ok", "build_id": "B1", "season": 2026, "through_week": 3}),
            encoding="utf-8",
        )


def members(*groups):
    """`(community, kind, id)` -> the frame `job_families` hands to `_name_families`."""
    rows = [{"kind": kind, "id": pid, "community": c, "name": f"{kind.title()} {pid.upper()}",
             "position": "WR", "grp": "WR"} for c, kind, pid in groups]  # fmt: skip
    return pl.DataFrame(
        rows, schema={"kind": pl.String, "id": pl.String, "community": pl.Int64,
                      "name": pl.String, "position": pl.String, "grp": pl.String}
    )  # fmt: skip


# The league of this file's tests ----------------------------------------------------------------
# community 1: a1..a4 and the coach c1: KC's offense 2019-2022
#   KC totals per season of its members: 2018 449 (a1)  [just under a quarter of the peak]
#   2019: a1 600 + a2 500 + a3 400 + a4 10 = 1510; 2020: 700 + 600 + 500 = 1800 (the peak);
#   2021: 650 + 550 + 500 = 1700; 2022: a2 450 [exactly a quarter of the peak: kept]
#   a4 also had 300 snaps for LV in 2019 (not the team of the era); a1 has 100 snaps in 2026 week
#   2 (the appended season, through week 3) and 77 in week 5 (after the cut: not read)
# community 2 and 4: five DEN defenders each, 2020-2021 (the same name: numbered)
# community 3: five LV players in 2021 with 300 offense and 250 defense snaps (neither unit
#   twice the other: "core")
# community 5: three players (below the minimum size of 5: dropped)
# community 6: five coaches and nobody with snaps: "unnamed"
A = [("a1", 2018, 449), ("a1", 2019, 600), ("a1", 2020, 700), ("a1", 2021, 650),
     ("a2", 2019, 500), ("a2", 2020, 600), ("a2", 2021, 550), ("a2", 2022, 450),
     ("a3", 2019, 400), ("a3", 2020, 500), ("a3", 2021, 500), ("a4", 2019, 10)]  # fmt: skip


@pytest.fixture
def league(tmp_path):
    paths = DataPaths(tmp_path)
    write_state(paths)
    rows = [(p, "KC", s, 1, n, 0, 0) for p, s, n in A]
    rows += [("a4", "LV", 2019, 1, 300, 0, 0), ("a1", "KC", 2026, 2, 100, 0, 0),
             ("a1", "KC", 2026, 5, 77, 0, 0)]  # fmt: skip
    for ids in ("d1 d2 d3 d4 d5", "e1 e2 e3 e4 e5"):
        for pid in ids.split():
            rows += [(pid, "DEN", 2020, 1, 0, 500, 0), (pid, "DEN", 2021, 1, 0, 400, 100)]
    for pid in ["f1", "f2", "f3", "f4", "f5"]:
        rows.append((pid, "LV", 2021, 1, 300, 250, 0))
    for pid in ["g1", "g2", "g3"]:
        rows.append((pid, "NE", 2020, 1, 100, 0, 0))
    write_snaps(paths, rows)
    groups = [(1, "player", p) for p in ("a1", "a2", "a3", "a4")] + [(1, "coach", "c1")]
    groups += [(2, "player", p) for p in ["d1", "d2", "d3", "d4", "d5"]]
    groups += [(3, "player", p) for p in ["f1", "f2", "f3", "f4", "f5"]]
    groups += [(4, "player", p) for p in ["e1", "e2", "e3", "e4", "e5"]]
    groups += [(5, "player", p) for p in ["g1", "g2", "g3"]]
    groups += [(6, "coach", p) for p in ["h1", "h2", "h3", "h4", "h5"]]
    return paths, members(*groups)


def fam_row(fam: pl.DataFrame, community: int) -> dict:
    hit = fam.filter(pl.col("community") == community)
    assert hit.height == 1, community
    return hit.to_dicts()[0]


class TestNameFamilies:
    def test_the_names_by_hand(self, league):
        paths, m = league
        fam, _ = L._name_families(paths, m, 5)
        names = {r["community"]: r["name"] for r in fam.to_dicts()}
        assert names == {
            1: "2019-2022 KC offense",  # 2018 (449 < 450) is out, 2022 (450 = a quarter) is in
            2: "2020-2021 DEN defense (1)",  # the same name twice: numbered, lower community first
            4: "2020-2021 DEN defense (2)",
            3: "2021 LV core",  # one season: no range; 300 offense vs 250 defense: neither 2x
            6: "unnamed",  # nobody in it has snaps: no team-era
        }

    def test_the_biggest_team_is_the_one_with_the_most_member_snaps(self, league):
        paths, m = league
        fam, _ = L._name_families(paths, m, 5)
        r = fam_row(fam, 1)
        # KC: 449 + 1510 + 1800 + 1700 + 450 + 100 (2026 week 2) = 6009; LV: a4's 300
        assert r["team"] == "KC" and (r["first_season"], r["last_season"]) == (2019, 2022)

    def test_the_era_threshold_is_a_quarter_of_the_peak_season_inclusive(self, league):
        paths, m = league
        # make 2022 one snap short of a quarter of 1800 (449 < 450): it drops out of the era
        rows = pl.read_parquet(paths.curated / "snaps.parquet")
        rows = rows.with_columns(
            pl.when((pl.col("gsis_id") == "a2") & (pl.col("season") == 2022))
            .then(449).otherwise(pl.col("offense_snaps")).cast(pl.Int32).alias("offense_snaps")
        )  # fmt: skip
        rows.write_parquet(paths.curated / "snaps.parquet")
        fam, _ = L._name_families(paths, m, 5)
        assert fam_row(fam, 1)["name"] == "2019-2021 KC offense"

    def test_a_season_of_the_peak_always_counts_and_the_range_may_have_gaps_closed(self, league):
        paths, m = league
        fam, _ = L._name_families(paths, m, 5)
        r = fam_row(fam, 1)
        assert r["first_season"] <= 2020 <= r["last_season"]  # the peak season is inside

    def test_the_unit_is_offense_defense_or_core(self, league):
        paths, m = league
        fam, _ = L._name_families(paths, m, 5)
        assert fam_row(fam, 1)["unit"] == "offense"  # 0 defense snaps: off >= 2 * def
        assert fam_row(fam, 2)["unit"] == "defense"  # def 900 vs off 0 for each defender
        assert fam_row(fam, 3)["unit"] == "core"  # 300 vs 250
        assert fam_row(fam, 6)["unit"] == "core"  # no snaps at all: not offense or defense

    @pytest.mark.parametrize(
        ("off", "dfn", "unit"),
        [(200, 100, "offense"), (199, 100, "core"), (100, 200, "defense"), (100, 199, "core"),
         (0, 0, "offense"), (100, 100, "core")],
    )  # fmt: skip
    def test_the_unit_rule_at_the_edges(self, tmp_path, off, dfn, unit):
        paths = DataPaths(tmp_path)
        write_state(paths, append=False)
        pids = [f"x{i}" for i in range(5)]
        write_snaps(paths, [(p, "KC", 2020, 1, off, dfn, 0) for p in pids])
        fam, _ = L._name_families(paths, members(*[(1, "player", p) for p in pids]), 5)
        assert fam_row(fam, 1)["unit"] == unit
        # (0, 0): every member has zero snaps, so the "era" has a team but no snaps
        assert fam_row(fam, 1)["name"] == f"2020 KC {unit}"

    def test_sizes_players_and_coaches(self, league):
        paths, m = league
        fam, _ = L._name_families(paths, m, 5)
        r = fam_row(fam, 1)
        assert (r["size"], r["players"], r["coaches"]) == (5, 4, 1)
        r6 = fam_row(fam, 6)
        assert (r6["size"], r6["players"], r6["coaches"]) == (5, 0, 5)

    def test_families_under_the_minimum_size_are_dropped_with_their_members(self, league):
        paths, m = league
        fam, mem = L._name_families(paths, m, 5)
        assert set(fam["community"]) == {1, 2, 3, 4, 6}  # community 5 has 3 members
        assert set(mem["community"]) == {1, 2, 3, 4, 6}
        fam3, mem3 = L._name_families(paths, m, 3)
        assert 5 in set(fam3["community"]) and 5 in set(mem3["community"])
        fam6, mem6 = L._name_families(paths, m, 6)
        assert fam6.height == 0 and mem6.height == 0
        assert fam6.columns == fam.columns and mem6.columns == mem.columns

    def test_the_families_are_sorted_by_size_then_community(self, league):
        paths, m = league
        fam, _ = L._name_families(paths, m, 3)
        assert fam["community"].to_list() == [1, 2, 3, 4, 6, 5]  # five of 5 (by id), then 3

    def test_the_top_players_and_coaches(self, league):
        paths, m = league
        fam, _ = L._name_families(paths, m, 5)
        r = fam_row(fam, 1)
        # by snaps in the era (KC 2019-2022): a2 2,100, a1 1,950, a3 1,400, a4 10
        assert r["top_players"] == ["Player A2", "Player A1", "Player A3", "Player A4"]
        assert r["top_coaches"] == ["Coach C1"]
        assert fam_row(fam, 6)["top_players"] is None
        assert fam_row(fam, 6)["top_coaches"] == [f"Coach H{i}" for i in range(1, 4)]  # at most 3

    def test_at_most_five_top_players(self, league):
        paths, m = league
        fam, _ = L._name_families(paths, m, 5)
        assert len(fam_row(fam, 2)["top_players"]) == 5

    def test_member_snaps_in_the_era_and_in_all(self, league):
        paths, m = league
        _, mem = L._name_families(paths, m, 5)
        one = {r["id"]: r for r in mem.filter(pl.col("community") == 1).to_dicts()}
        # a1: KC 2019-2021 600 + 700 + 650 = 1,950 in the era; 449 more in 2018 and 100 in 2026
        # week 2 (week 5 is after the appended week): 2,499 in all
        assert (one["a1"]["snaps"], one["a1"]["snaps_total"]) == (1950, 2499)
        assert (one["a2"]["snaps"], one["a2"]["snaps_total"]) == (2100, 2100)  # all in the era
        assert (one["a4"]["snaps"], one["a4"]["snaps_total"]) == (10, 310)  # 300 for LV outside
        # a coach has no snaps of his own: null, never 0
        assert one["c1"]["snaps"] is None and one["c1"]["snaps_total"] is None

    def test_members_are_sorted_players_first_by_era_snaps(self, league):
        paths, m = league
        _, mem = L._name_families(paths, m, 5)
        one = mem.filter(pl.col("community") == 1)
        assert one["id"].to_list() == ["a2", "a1", "a3", "a4", "c1"]
        assert mem["community"].to_list() == sorted(mem["community"].to_list())

    def test_a_player_without_snaps_has_zero_not_null(self, league):
        paths, m = league
        extra = pl.concat([m, members((1, "player", "nosnaps"))])
        _, mem = L._name_families(paths, extra, 5)
        r = mem.filter(pl.col("id") == "nosnaps").to_dicts()[0]
        assert r["snaps"] == 0 and r["snaps_total"] == 0

    def test_the_appended_season_is_cut_at_its_week(self, league):
        paths, m = league
        # without the append the 2026 snaps aren't read at all: a1's total drops to 2,399
        (paths.neo4j_history / "append.json").unlink()
        _, mem = L._name_families(paths, m, 5)
        a1 = mem.filter(pl.col("id") == "a1").to_dicts()[0]
        assert a1["snaps_total"] == 2399

    def test_no_build_json_still_names_families_from_the_default_seasons(self, league):
        paths, m = league
        (paths.neo4j_history / "build.json").unlink()
        fam, _ = L._name_families(paths, m, 5)
        assert fam_row(fam, 1)["name"] == "2019-2022 KC offense"  # 2018-2025 by default

    def test_the_frames_keep_their_columns(self, league):
        paths, m = league
        fam, mem = L._name_families(paths, m, 5)
        assert fam.columns == ["community", "size", "players", "coaches", "team", "first_season",
                               "last_season", "top_players", "top_coaches", "unit",
                               "name"]  # fmt: skip
        assert mem.columns == ["kind", "id", "community", "name", "position", "grp", "snaps_total",
                               "snaps"]  # fmt: skip


class TestExactKnn:
    def meta(self, ids, grps):
        return pl.DataFrame({"id": ids, "grp": grps},
                            schema={"id": pl.String, "grp": pl.String})  # fmt: skip

    def test_the_neighbours_by_hand(self):
        ids = ["p1", "p2", "p3"]
        emb = np.array([[1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
        out = L._exact_knn(ids, emb, self.meta(ids, ["QB"] * 3), 5)
        rows = {(r["a"], r["rank"]): (r["b"], round(r["similarity"], 4)) for r in out.to_dicts()}
        assert rows == {
            ("p1", 1): ("p2", 0.7071), ("p1", 2): ("p3", 0.0),
            ("p2", 1): ("p1", 0.7071), ("p2", 2): ("p3", 0.7071),  # a tie: the first index first
            ("p3", 1): ("p2", 0.7071), ("p3", 2): ("p1", 0.0),
        }  # fmt: skip
        assert set(out["scope"]) == {"position"}
        assert out.schema == {"a": pl.String, "rank": pl.Int64, "b": pl.String,
                              "similarity": pl.Float64, "scope": pl.String}  # fmt: skip

    def test_only_the_same_position_group(self):
        # p1 and p4 point the same way (cosine 1) but one is a QB and one a WR
        ids = ["p1", "p2", "p3", "p4"]
        emb = np.array([[1.0, 0.0], [0.0, 1.0], [0.3, 1.0], [1.0, 0.0]])
        out = L._exact_knn(ids, emb, self.meta(ids, ["QB", "QB", "WR", "WR"]), 5)
        by = {(r["a"], r["b"]) for r in out.to_dicts()}
        assert by == {("p1", "p2"), ("p2", "p1"), ("p3", "p4"), ("p4", "p3")}
        assert ("p1", "p4") not in by and ("p4", "p1") not in by

    def test_a_player_is_never_his_own_neighbour_even_with_a_twin(self):
        ids = ["p1", "p2", "p3"]
        emb = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])  # p1 and p2 are identical
        out = L._exact_knn(ids, emb, self.meta(ids, ["QB"] * 3), 5)
        assert out.filter(pl.col("a") == pl.col("b")).height == 0
        top = out.filter((pl.col("a") == "p1") & (pl.col("rank") == 1)).to_dicts()[0]
        assert top["b"] == "p2" and round(top["similarity"], 6) == 1.0  # the twin, not himself

    @pytest.mark.parametrize("k", [1, 2, 3, 10])
    def test_k_caps_the_neighbours_per_player(self, k):
        ids = [f"p{i}" for i in range(5)]
        emb = np.random.default_rng(0).normal(size=(5, 4))
        out = L._exact_knn(ids, emb, self.meta(ids, ["QB"] * 5), k)
        per = out.group_by("a").len()["len"].to_list()
        assert per == [min(k, 4)] * 5  # never more than the other four

    def test_ranks_run_from_one_and_similarity_falls(self):
        ids = [f"p{i}" for i in range(6)]
        emb = np.random.default_rng(1).normal(size=(6, 8))
        out = L._exact_knn(ids, emb, self.meta(ids, ["QB"] * 6), 4)
        for _a, grp in out.group_by("a"):
            g = grp.sort("rank")
            assert g["rank"].to_list() == [1, 2, 3, 4]
            sims = g["similarity"].to_list()
            assert sims == sorted(sims, reverse=True)

    def test_a_group_of_one_and_an_unknown_group_have_no_neighbours(self):
        ids = ["p1", "p2", "p3", "p4"]
        emb = np.eye(4)
        out = L._exact_knn(ids, emb, self.meta(ids, ["QB", "QB", "RB", None]), 5)
        assert set(out["a"]) == {"p1", "p2"}  # p3 is alone in RB; p4's group is unknown

    def test_players_missing_from_the_meta_are_skipped(self):
        ids = ["p1", "p2", "p3"]
        emb = np.eye(3)
        out = L._exact_knn(ids, emb, self.meta(["p1", "p2"], ["QB", "QB"]), 5)
        assert set(out["a"]) == {"p1", "p2"} and set(out["b"]) == {"p1", "p2"}

    def test_a_zero_vector_has_zero_similarity_not_nan(self):
        ids = ["p1", "p2", "p3"]
        emb = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
        out = L._exact_knn(ids, emb, self.meta(ids, ["QB"] * 3), 5)
        sims = out["similarity"].to_list()
        assert not any(np.isnan(s) for s in sims)
        z = out.filter(pl.col("a") == "p1").to_dicts()
        assert all(abs(r["similarity"]) < 1e-9 for r in z)

    def test_the_scale_of_an_embedding_doesnt_matter(self):
        ids = ["p1", "p2"]
        out = L._exact_knn(ids, np.array([[1.0, 2.0], [10.0, 20.0]]), self.meta(ids, ["QB"] * 2), 1)
        assert round(out["similarity"][0], 9) == 1.0

    def test_nobody_in_a_group_gives_an_empty_frame_with_the_columns(self):
        out = L._exact_knn([], np.zeros((0, 3)), self.meta([], []), 5)
        assert out.height == 0 and out.columns == ["a", "rank", "b", "similarity", "scope"]


class TestPca:
    def test_a_line_has_one_component(self):
        t = np.linspace(-3, 3, 7)
        x = np.stack([t, 2 * t, 0 * t], axis=1)
        xy = L._pca2(x)
        assert xy.shape == (7, 2)
        assert np.allclose(xy[:, 1], 0.0, atol=1e-9)
        assert np.allclose(np.abs(xy[:, 0]), np.abs(t) * np.sqrt(5), atol=1e-9)  # |(1, 2, 0)|

    def test_the_output_is_centred_and_decorrelated(self):
        x = np.random.default_rng(3).normal(size=(200, 6)) * np.array([5, 3, 1, 1, 1, 1])
        xy = L._pca2(x)
        assert np.allclose(xy.mean(axis=0), 0.0, atol=1e-9)
        cov = np.cov(xy.T)
        assert abs(cov[0, 1]) < 1e-9 and cov[0, 0] >= cov[1, 1]

    def test_planar_data_keeps_its_distances(self):
        rng = np.random.default_rng(4)
        flat = rng.normal(size=(20, 2))
        basis = np.array([[1.0, 2.0, 0.5, 0.0], [0.0, 1.0, -1.0, 3.0]])
        x = flat @ basis + 7.0  # a plane in 4-D, off the origin
        xy = L._pca2(x)
        d_in = np.linalg.norm(x[:, None] - x[None], axis=2)
        d_out = np.linalg.norm(xy[:, None] - xy[None], axis=2)
        assert np.allclose(d_in, d_out, atol=1e-8)

    def test_the_explained_variance_of_two_components(self):
        t = np.linspace(-1, 1, 11)
        line = np.stack([t, t], axis=1)
        v = L._pca_var(line)
        assert v.shape == (2,) and abs(v[0] - 1.0) < 1e-9 and abs(v[1]) < 1e-9
        iso = np.random.default_rng(5).normal(size=(500, 4))
        v2 = L._pca_var(iso)
        assert 0 < v2.sum() <= 1.0 and v2[0] >= v2[1]


class TestHelpers:
    def test_the_job_list(self):
        assert L.JOBS == ("neighbours", "families", "chemistry") and L.SEED == 42

    def test_for_table_turns_lists_into_text(self):
        df = pl.DataFrame({"a": [[1, 2], [3]], "b": [["x", "y"], []], "c": [1, 2]})
        out = L._for_table(df)
        assert out["a"].to_list() == ["1, 2", "3"] and out["b"].to_list() == ["x, y", ""]
        assert out["c"].to_list() == [1, 2]

    def test_a_parquet_write_is_atomic_and_leaves_no_temp_file(self, tmp_path):
        target = tmp_path / "lab" / "x.parquet"
        L._write_parquet(pl.DataFrame({"a": [1]}), target)
        assert pl.read_parquet(target)["a"].to_list() == [1]
        L._write_parquet(pl.DataFrame({"a": [2]}), target)  # replaced in place
        assert pl.read_parquet(target)["a"].to_list() == [2]
        assert [p.name for p in target.parent.iterdir()] == ["x.parquet"]

    def test_read_lab_is_empty_before_the_first_run(self, tmp_path):
        assert L.read_lab(DataPaths(tmp_path)) == {}
        assert L.lab_status(DataPaths(tmp_path)) == {}

    def test_lab_status_lists_the_jobs_that_have_run(self, tmp_path):
        paths = DataPaths(tmp_path)
        (paths.neo4j_history / "lab").mkdir(parents=True)
        doc = {"families": {"derive_id": "D1", "ran_at": "t", "through_season": 2025,
                            "season": 2026, "through_week": 5, "params": {"x": 1}},
               "other": {"derive_id": "Z"}}  # fmt: skip
        (paths.neo4j_history / "lab" / "lab.json").write_text(json.dumps(doc), encoding="utf-8")
        assert L.lab_status(paths) == {"families": {
            "derive_id": "D1", "ran_at": "t", "through_season": 2025, "season": 2026,
            "through_week": 5}}  # fmt: skip

    def test_the_lab_files_live_under_neo4j_history_lab(self, tmp_path):
        paths = DataPaths(tmp_path)
        assert L.lab_path(paths, "a.parquet") == paths.neo4j_history / "lab" / "a.parquet"
        assert L.lab_dir(paths) == paths.neo4j_history / "lab"

    def test_the_parameters_are_the_documented_ones(self):
        assert L.NEIGHBOURS["dimension"] == 128 and L.NEIGHBOURS["k"] == 10
        assert L.NEIGHBOURS["min_snaps"] == 100 and L.NEIGHBOURS["connection_weight"] == 3.0
        assert L.FAMILIES["gamma"] == 16.0 and L.FAMILIES["min_size"] == 5
        assert L.CHEMISTRY["min_targets"] == 100
