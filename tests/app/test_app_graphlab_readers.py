"""The Graph lab's file readers (AE03, `app/readers/graphlab.py`) on files under a tmp data root:
status, the name search, the lab views, the benchmark and the modification-time cache. No Neo4j,
no data root of Rishi's."""

from __future__ import annotations

import os
import time
from pathlib import Path

import polars as pl
import pytest
from app_helpers import make_paths
from graphlab_fixtures import (
    BENCH_DOC,
    BRADY,
    DERIVE,
    KELCE,
    MAHOMES,
    NOBODY,
    PEOPLE_SCHEMA,
    D,
    lab_dir,
    lab_entry,
    put_json,
    write_benchmark,
    write_chemistry,
    write_everything,
    write_families,
    write_history,
    write_lab_json,
    write_neighbours,
)

from nflengine.app.readers import graphlab as G
from nflengine.history_graph.connections import parse_person
from nflengine.history_graph.state import mark_unfinished


@pytest.fixture(autouse=True)
def _fresh_cache():
    G._cache.clear()
    yield
    G._cache.clear()


@pytest.fixture
def paths(tmp_path):
    return make_paths(tmp_path)


def bump(path: Path, seconds: float = 10.0) -> None:
    """A later modification time (a rewrite inside one clock tick keeps the old one)."""
    t = path.stat().st_mtime + seconds
    os.utime(path, (t, t))


class TestStatus:
    def test_before_anything_is_built(self, paths):
        st = G.get_status(paths)
        assert st == {"history": None, "busy": False, "unfinished": None, "links": None,
                      "lab": {"neighbours": None, "families": None, "chemistry": None},
                      "benchmark": None}  # fmt: skip

    def test_with_everything(self, paths):
        write_everything(paths)
        st = G.get_status(paths)
        assert st["history"]["label"] == "history graph 2018-2025 + 2026 week 5"
        assert st["busy"] is False and st["unfinished"] is None
        assert st["links"]["derive_id"] == D and st["links"]["label"] == (
            "person links 2018-2025 + 2026 week 5")  # fmt: skip
        assert set(st["lab"]) == {"neighbours", "families", "chemistry"}
        assert all(v["current"] is True for v in st["lab"].values())
        assert st["benchmark"]["derive_id"] == D

    def test_an_unfinished_run_is_busy_and_named(self, paths):
        write_history(paths)
        mark_unfinished(paths, "derive", "B1")
        st = G.get_status(paths)
        assert st["busy"] is True and st["unfinished"] == "derive"

    def test_an_unreadable_marker_is_busy_but_unnamed(self, paths):
        write_history(paths)
        (paths.neo4j_history / ".history.unfinished").write_text("garbage", encoding="utf-8")
        st = G.get_status(paths)
        assert st["busy"] is True and st["unfinished"] is None

    def test_the_benchmark_says_when_it_ran(self, paths):
        # the benchmark file has `started_at` and `seconds` (no `ran_at`): the status must still
        # say when it ran, or the page can't show "ran <date>"
        write_history(paths)
        write_benchmark(paths)
        assert G.get_status(paths)["benchmark"]["ran_at"] == BENCH_DOC["started_at"]

    def test_a_benchmark_file_that_has_a_ran_at_keeps_it(self, paths):
        write_history(paths)
        write_benchmark(paths, {**BENCH_DOC, "ran_at": "2026-10-19T00:00:00+00:00"})
        assert G.get_status(paths)["benchmark"]["ran_at"] == "2026-10-19T00:00:00+00:00"


class TestAsOf:
    def test_the_person_links_label(self, paths):
        write_history(paths)
        a = G.links_asof(paths)
        assert a["label"] == "person links 2018-2025 + 2026 week 5"
        assert (a["through_season"], a["season"], a["week"]) == (2025, 2026, 5)
        assert a["counts"]["TEAMMATE"] == 237000 and a["derived_at"].startswith("2026-")

    def test_a_derive_without_an_appended_week(self, paths):
        write_history(paths)
        put_json(
            paths.neo4j_history / "derive.json", {**DERIVE, "through_week": None, "season": None}
        )
        a = G.links_asof(paths)
        assert a["label"] == "person links 2018-2025" and a["week"] is None

    def test_the_label_names_the_first_season_it_covers(self, paths):
        write_history(paths)
        put_json(paths.neo4j_history / "derive.json", {**DERIVE, "first_season": 2022})
        assert G.links_asof(paths)["label"].startswith("person links 2022-2025")

    def test_no_derive_no_links(self, paths):
        write_history(paths, derive=False)
        assert G.links_asof(paths) is None

    def test_a_failed_or_foreign_derive_is_no_links(self, paths):
        write_history(paths)
        put_json(paths.neo4j_history / "derive.json", {**DERIVE, "status": "failed"})
        assert G.links_asof(paths) is None
        put_json(paths.neo4j_history / "derive.json", {**DERIVE, "build_id": "OTHER"})
        assert G.links_asof(paths) is None

    def test_a_lab_run_is_current_only_on_the_same_derive(self, paths):
        write_history(paths)
        write_lab_json(
            paths,
            {
                "families": lab_entry("families", communities=3),
                "chemistry": lab_entry("chemistry", derive_id="OLD", duos=1),
            },
        )
        assert G.lab_asof(paths, "families")["current"] is True
        assert G.lab_asof(paths, "chemistry")["current"] is False
        assert G.lab_asof(paths, "neighbours") is None

    def test_a_lab_run_is_never_current_without_person_links(self, paths):
        write_history(paths, derive=False)
        write_lab_json(paths, {"families": lab_entry("families")})
        assert G.lab_asof(paths, "families")["current"] is False

    def test_the_lab_summary_has_only_the_numbers_the_run_has(self, paths):
        write_history(paths)
        write_lab_json(
            paths,
            {
                "families": lab_entry(
                    "families", communities=3, families=2, modularity=0.9, secret_thing=1
                )
            },
        )
        a = G.lab_asof(paths, "families")
        assert a["summary"] == {"communities": 3, "families": 2, "modularity": 0.9}
        assert a["params"] == {"seed": 42} and a["wandb_url"] == "https://wandb.example/families"
        assert a["ran_at"] and a["derive_id"] == D


class TestPeople:
    def test_not_ready_without_an_index(self, paths):
        write_history(paths, people=False)
        out = G.get_people(paths, "mahomes")
        assert out["ready"] is False and out["people"] == [] and out["query"] == "mahomes"
        assert out["links"]["derive_id"] == D

    def test_not_ready_before_anything(self, paths):
        out = G.get_people(paths, "mahomes")
        assert out["ready"] is False and out["links"] is None

    def test_a_search(self, paths):
        write_history(paths)
        out = G.get_people(paths, "Patrick Mahomes")
        assert out["ready"] is True
        assert [p["id"] for p in out["people"]] == [f"player:{MAHOMES}"]
        assert out["people"][0] == {
            "id": f"player:{MAHOMES}", "kind": "player", "name": "Patrick Mahomes",
            "position": "QB", "team": "KC", "seasons": "2018-2025", "links": 40}  # fmt: skip

    def test_people_without_links_are_never_offered(self, paths):
        write_history(paths)
        assert G.get_people(paths, "Nobody Links")["people"] == []

    def test_the_kind_filter(self, paths):
        write_history(paths)
        assert [p["id"] for p in G.get_people(paths, "Andy Reid", kind="coach")["people"]] == [
            "coach:andy-reid"
        ]
        assert G.get_people(paths, "Andy Reid", kind="player")["people"] == []
        # any other kind is no filter
        assert [p["id"] for p in G.get_people(paths, "Andy Reid", kind="team")["people"]] == [
            "coach:andy-reid"
        ]

    def test_the_limit_is_clamped(self, paths):
        write_history(paths)
        rows = [("player", f"00-{9000000 + i:07d}", f"Common Name {i:02d}", "WR", "KC", ["KC"],
                 2018, 2025, 100, 5) for i in range(30)]  # fmt: skip
        pl.DataFrame(rows, schema=PEOPLE_SCHEMA, orient="row").write_parquet(
            paths.neo4j_history / "people.parquet"
        )
        assert G.MAX_PEOPLE == 20
        assert len(G.get_people(paths, "Common Name", limit=5)["people"]) == 5
        assert len(G.get_people(paths, "Common Name", limit=20)["people"]) == 20
        assert len(G.get_people(paths, "Common Name", limit=25)["people"]) == 20  # capped
        assert len(G.get_people(paths, "Common Name", limit=10_000)["people"]) == 20
        assert len(G.get_people(paths, "Common Name", limit=0)["people"]) == 1  # at least one
        assert len(G.get_people(paths, "Common Name", limit=-4)["people"]) == 1

    def test_a_short_query_finds_nobody(self, paths):
        write_history(paths)
        assert G.get_people(paths, "m")["people"] == [] and G.get_people(paths, "")["people"] == []

    def test_a_missing_season_range_is_none(self, paths):
        write_history(paths)
        hit = G.get_people(paths, "Bill Belichick")["people"][0]
        assert hit["seasons"] is None and hit["kind"] == "coach"


class TestKnownPerson:
    def test_known_and_unknown(self, paths):
        write_history(paths)
        assert G.known_person(paths, parse_person(f"player:{MAHOMES}")) is True
        assert G.known_person(paths, parse_person("coach:andy-reid")) is True
        assert G.known_person(paths, parse_person("player:00-0099999")) is False
        assert G.known_person(paths, parse_person("coach:nobody-here")) is False

    def test_a_person_with_no_links_is_still_in_the_index(self, paths):
        write_history(paths)
        assert G.known_person(paths, parse_person(f"player:{NOBODY}")) is True

    def test_the_kind_must_match(self, paths):
        write_history(paths)
        # a coach's slug as a player and a player's id as a coach are different people
        assert (
            G.known_person(paths, type("R", (), {"kind": "player", "id": "andy-reid"})()) is False
        )
        assert G.known_person(paths, type("R", (), {"kind": "coach", "id": MAHOMES})()) is False

    def test_no_index_knows_nobody(self, paths):
        write_history(paths, people=False)
        assert G.known_person(paths, parse_person(f"player:{MAHOMES}")) is False


class TestPersonFacts:
    def test_team_and_seasons_by_kind_and_id(self, paths):
        write_history(paths)
        facts = G.person_facts(paths)
        assert facts[("player", MAHOMES)] == {"team": "KC", "seasons": "2018-2025"}
        assert facts[("coach", "andy-reid")] == {"team": "KC", "seasons": "2018-2026"}
        assert facts[("coach", "bill-belichick")] == {"team": "NE", "seasons": None}
        assert len(facts) == 6

    def test_a_single_season_is_one_year_not_a_range(self, paths):
        write_history(paths)
        # NOBODY played 2018 only
        assert G.person_facts(paths)[("player", NOBODY)]["seasons"] == "2018"
        assert G._span(2018, 2018) == "2018" and G._span(2018, None) == "2018"
        assert G._span(2018, 2025) == "2018-2025"

    def test_without_an_index(self, paths):
        write_history(paths, people=False)
        assert G.person_facts(paths) == {}

    def test_the_facts_follow_a_new_index(self, paths):
        write_history(paths)
        assert G.person_facts(paths)[("player", BRADY)]["team"] == "TB"
        f = paths.neo4j_history / "people.parquet"
        df = pl.read_parquet(f).with_columns(
            pl.when(pl.col("id") == BRADY)
            .then(pl.lit("LV"))
            .otherwise(pl.col("team"))
            .alias("team")
        )
        df.write_parquet(f)
        bump(f)
        assert G.person_facts(paths)[("player", BRADY)]["team"] == "LV"

    def test_facts_built_from_a_frame_replaced_meanwhile_arent_kept(self, paths, monkeypatch):
        # Sol's AE03 review: the frame was read, then a new derive replaced the file before the
        # facts were stamped; they must be keyed on the frame's own stamp, so the next request
        # rebuilds them from the new file
        write_history(paths)
        f = paths.neo4j_history / "people.parquet"
        old = G._stamped_parquet(f)
        df = pl.read_parquet(f).with_columns(
            pl.when(pl.col("id") == BRADY).then(pl.lit("LV")).otherwise(pl.col("team"))
            .alias("team")
        )  # fmt: skip
        df.write_parquet(f)
        bump(f)
        real, racing = G._stamped_parquet, [True]
        monkeypatch.setattr(G, "_stamped_parquet",
                            lambda path: old if racing[0] else real(path))  # fmt: skip
        assert G.person_facts(paths)[("player", BRADY)]["team"] == "TB"  # the race
        racing[0] = False
        assert G.person_facts(paths)[("player", BRADY)]["team"] == "LV"


class TestNeighbours:
    def test_none_until_the_job_has_run(self, paths):
        write_history(paths)
        assert G.get_neighbours(paths, MAHOMES) is None
        assert G.get_neighbours_map(paths) is None

    def test_none_with_only_one_of_the_two_files(self, paths):
        write_history(paths)
        write_neighbours(paths)
        (lab_dir(paths) / "neighbours_map.parquet").unlink()
        assert G.get_neighbours(paths, MAHOMES) is None
        G._cache.clear()
        write_neighbours(paths)
        (lab_dir(paths) / "neighbours.parquet").unlink()
        assert G.get_neighbours(paths, MAHOMES) is None

    def test_a_players_neighbours_in_rank_order(self, paths):
        write_everything(paths)
        out = G.get_neighbours(paths, MAHOMES)
        assert out["found"] is True and out["player_id"] == MAHOMES
        assert out["player"] == {"player_id": MAHOMES, "name": "Patrick Mahomes",
                                 "position": "QB", "grp": "QB", "team": "KC", "snaps": 5000,
                                 "x": 1.23456, "y": -0.5}  # fmt: skip
        assert [r["rank"] for r in out["position"]] == [1, 2]
        assert [r["neighbour_id"] for r in out["position"]] == ["00-0000001", BRADY]
        assert [r["neighbour_id"] for r in out["overall"]] == [KELCE, "00-0000001"]
        assert set(out["overall"][0]) == {"rank", "neighbour_id", "neighbour_name",
                                          "neighbour_position", "neighbour_team", "similarity",
                                          "snaps_together"}  # fmt: skip
        assert out["overall"][0]["snaps_together"] == 8372
        assert out["asof"]["current"] is True

    def test_a_player_who_is_not_in_the_map(self, paths):
        write_everything(paths)
        out = G.get_neighbours(paths, "00-0099999")
        assert out["found"] is False and out["player"] is None
        assert out["position"] == [] and out["overall"] == [] and out["asof"] is not None

    def test_only_his_own_rows(self, paths):
        write_everything(paths)
        out = G.get_neighbours(paths, KELCE)
        assert [r["neighbour_id"] for r in out["position"]] == ["00-0000002"] and out[
            "overall"
        ] == []

    def test_the_map(self, paths):
        write_everything(paths)
        out = G.get_neighbours_map(paths)
        assert [p["player_id"] for p in out["points"]] == [MAHOMES, KELCE, BRADY]
        assert out["points"][0] == {"player_id": MAHOMES, "name": "Patrick Mahomes", "grp": "QB",
                                    "team": "KC", "x": 1.235, "y": -0.5}  # fmt: skip
        assert out["asof"]["derive_id"] == D


class TestFamilies:
    def test_none_until_the_job_has_run(self, paths):
        write_history(paths)
        assert G.get_families(paths) is None

    def test_the_list_without_members(self, paths):
        write_everything(paths)
        out = G.get_families(paths)
        assert [f["community"] for f in out["families"]] == [1, 2]
        assert out["families"][0]["name"] == "2019-2022 KC offense"
        assert out["families"][0]["top_players"] == ["Patrick Mahomes", "Travis Kelce"]
        assert out["families"][1]["top_coaches"] is None
        assert out["family"] is None and out["members"] is None and out["person_found"] is None
        assert out["asof"]["current"] is True

    def test_a_family_with_its_members(self, paths):
        write_everything(paths)
        out = G.get_families(paths, family=1)
        assert out["family"] == 1 and out["person_found"] is None
        assert [m["id"] for m in out["members"]] == [MAHOMES, KELCE, "andy-reid"]
        assert set(out["members"][0]) == {"kind", "id", "name", "position", "snaps", "snaps_total"}
        assert out["members"][0]["snaps"] == 3900 and out["members"][0]["snaps_total"] == 5000
        assert out["members"][2]["snaps"] is None  # a coach has none of his own

    def test_a_family_that_isnt_there_has_an_empty_member_list(self, paths):
        write_everything(paths)
        out = G.get_families(paths, family=999)
        assert out["family"] == 999 and out["members"] == []

    def test_a_person_picks_his_family(self, paths):
        write_everything(paths)
        out = G.get_families(paths, person=f"player:{KELCE}")
        assert out["family"] == 1 and out["person_found"] is True and len(out["members"]) == 3
        coach = G.get_families(paths, person="coach:andy-reid")
        assert coach["family"] == 1 and coach["person_found"] is True

    def test_a_person_in_no_family(self, paths):
        write_everything(paths)
        out = G.get_families(paths, person=f"player:{NOBODY}")
        assert out["family"] is None and out["members"] is None and out["person_found"] is False

    def test_a_person_wins_over_a_family(self, paths):
        write_everything(paths)
        out = G.get_families(paths, family=2, person=f"player:{MAHOMES}")
        assert out["family"] == 1

    def test_the_kind_must_match_the_person(self, paths):
        write_everything(paths)
        # a coach's slug asked as a player is nobody's family
        assert G.get_families(paths, person="player:andy-reid")["person_found"] is False
        assert G.get_families(paths, person="team:KC")["person_found"] is False

    def test_members_are_capped(self, paths):
        write_history(paths)
        write_lab_json(paths, {"families": lab_entry("families")})
        write_families(paths, extra_members=500)
        out = G.get_families(paths, family=1)
        assert len(out["members"]) == G.MAX_FAMILY_MEMBERS == 400

    def test_an_older_members_file_without_snaps_total(self, paths):
        write_history(paths)
        write_families(paths, snaps_total=False)
        out = G.get_families(paths, family=1)
        assert "snaps_total" not in out["members"][0] and out["members"][0]["snaps"] == 3900


class TestChemistry:
    def test_none_until_the_job_has_run(self, paths):
        write_history(paths)
        assert G.get_chemistry(paths) is None

    def test_the_duos_by_epa_per_target_with_the_default_minimum(self, paths):
        write_everything(paths)
        out = G.get_chemistry(paths)
        assert out["min_targets"] == 100
        # Reserve has 90 targets: under the minimum; the rest by EPA per target (0.2, 0.3, 0.1, 0)
        assert [d["receiver"] for d in out["duos"]] == [
            "Tyreek Hill", "Travis Kelce", "Mike Evans", "Cameron Brate"]  # fmt: skip
        d = out["duos"][1]
        assert (d["targets"], d["catch_rate"], d["yards_per_target"], d["epa_per_target"]) == (
            1000, 0.7, 8.0, 0.2)  # fmt: skip
        assert d["teams"] == ["KC"] and (d["first_season"], d["last_season"]) == (2018, 2025)
        assert "seasons" not in d  # (first and last are there instead)

    def test_a_higher_minimum_drops_the_small_duos(self, paths):
        write_everything(paths)
        out = G.get_chemistry(paths, min_targets=350)
        assert [d["receiver"] for d in out["duos"]] == ["Tyreek Hill", "Travis Kelce"]
        assert out["min_targets"] == 350

    def test_one_qb(self, paths):
        write_everything(paths)
        out = G.get_chemistry(paths, qb=BRADY)
        assert {d["qb_id"] for d in out["duos"]} == {BRADY} and len(out["duos"]) == 2
        assert G.get_chemistry(paths, qb="00-0099999")["duos"] == []

    def test_the_limit_is_clamped(self, paths):
        write_everything(paths)
        assert len(G.get_chemistry(paths, limit=2)["duos"]) == 2
        assert len(G.get_chemistry(paths, limit=0)["duos"]) == 1  # at least one
        assert G.MAX_DUOS == 200

    def test_the_pagerank_tables(self, paths):
        write_everything(paths)
        out = G.get_chemistry(paths)
        assert [r["rank"] for r in out["receivers"]] == list(range(1, 26))  # the top 25 of 30
        assert [r["rank"] for r in out["qbs"]] == list(range(1, 16))  # the top 15 of 20
        assert set(out["receivers"][0]) == {"rank", "player_id", "name", "position", "score",
                                            "receivers", "qbs"}  # fmt: skip
        assert out["receivers"][0]["player_id"] == "r01"
        assert out["asof"]["current"] is True


class TestBenchmark:
    def test_none_until_it_has_run(self, paths):
        write_history(paths)
        assert G.get_benchmark(paths) is None

    def test_none_with_only_half_of_it(self, paths):
        write_history(paths)
        write_benchmark(paths, rows=False)
        assert G.get_benchmark(paths) is None
        G._cache.clear()
        (lab_dir(paths) / "path_benchmark.json").unlink()
        write_benchmark(paths, doc_file=False)
        assert G.get_benchmark(paths) is None

    def test_the_runs_facts_and_its_rows(self, paths):
        write_history(paths)
        write_benchmark(paths)
        out = G.get_benchmark(paths)
        assert out["derive_id"] == D and out["seed"] == 42 and out["agrees_all"] is True
        assert out["started_at"] == BENCH_DOC["started_at"] and out["seconds"] == 812.5
        assert [s["label"] for s in out["sizes"]] == ["1000", "full"]
        assert out["machine"]["cpu_count"] == 8 and out["setup"][0]["step"] == "load"
        assert [r["algorithm"] for r in out["rows"]] == ["cypher-shortest", "cypher-shortest",
                                                         "floyd-warshall-numba"]  # fmt: skip
        skipped = out["rows"][2]
        assert skipped["runs"] == 0 and skipped["median_ms"] is None and skipped["agrees"] is None
        assert skipped["note"].startswith("predicted")

    def test_the_raw_tables_stay_in_the_file(self, paths):
        write_history(paths)
        write_benchmark(paths)
        out = G.get_benchmark(paths)
        for dropped in ("table", "times_ms", "gds_procedures", "results"):
            assert dropped not in out

    def test_a_results_key_from_an_older_file_is_dropped_too(self, paths):
        write_history(paths)
        write_benchmark(paths, {**BENCH_DOC, "results": [1], "rows": ["x"]})
        out = G.get_benchmark(paths)
        assert "results" not in out and out["rows"] != ["x"] and len(out["rows"]) == 3


class TestCache:
    def test_a_new_file_is_picked_up(self, paths):
        write_history(paths)
        assert G.get_chemistry(paths) is None
        write_lab_json(paths, {"chemistry": lab_entry("chemistry")})
        write_chemistry(paths)
        assert G.get_chemistry(paths) is not None  # no restart, no cache clearing

    def test_a_rewritten_file_is_re_read_when_its_time_changes(self, paths):
        write_everything(paths)
        assert len(G.get_chemistry(paths)["duos"]) == 4
        f = lab_dir(paths) / "chemistry_duos.parquet"
        pl.read_parquet(f).head(2).write_parquet(f)
        bump(f)
        assert len(G.get_chemistry(paths)["duos"]) == 2

    def test_an_unchanged_time_keeps_the_cached_frame(self, paths):
        write_everything(paths)
        G.get_chemistry(paths)
        f = lab_dir(paths) / "chemistry_duos.parquet"
        t = f.stat().st_mtime
        pl.read_parquet(f).head(1).write_parquet(f)
        os.utime(f, (t, t))  # the same modification time: the cache answers
        assert len(G.get_chemistry(paths)["duos"]) == 4

    def test_a_derive_json_change_is_seen_at_once(self, paths):
        write_everything(paths)
        assert G.get_status(paths)["lab"]["families"]["current"] is True
        put_json(paths.neo4j_history / "derive.json", {**DERIVE, "derive_id": "NEW"})
        assert G.get_status(paths)["lab"]["families"]["current"] is False  # not cached

    def test_the_file_is_not_held_open(self, paths):
        # a derive replaces people.parquet while the app runs (Windows refuses to replace an
        # open file): after a read the file can be replaced at once
        write_history(paths)
        G.get_people(paths, "Patrick Mahomes")
        f = paths.neo4j_history / "people.parquet"
        tmp = f.with_name("people.new")
        pl.read_parquet(f).write_parquet(tmp)
        os.replace(tmp, f)  # would raise PermissionError on Windows if it were mapped
        assert f.exists()

    def test_every_read_asks_for_no_memory_map(self, paths, monkeypatch):
        # the rule (D100): never hold a file the next derive or lab run has to replace. Whatever
        # polars does on this platform, the reader must say so
        write_everything(paths)
        seen: list = []
        real = pl.read_parquet

        def spy(*args, **kwargs):
            seen.append(kwargs.get("memory_map"))
            return real(*args, **kwargs)

        monkeypatch.setattr(pl, "read_parquet", spy)
        G.get_people(paths, "Patrick Mahomes")
        G.get_neighbours(paths, MAHOMES)
        G.get_families(paths, family=1)
        G.get_chemistry(paths)
        G.get_benchmark(paths)
        assert seen and all(m is False for m in seen), seen

    def test_reading_is_fast_when_cached(self, paths):
        write_everything(paths)
        G.get_chemistry(paths)
        t = time.perf_counter()
        for _ in range(50):
            G.get_chemistry(paths)
        assert time.perf_counter() - t < 5.0


class TestCleanTree:
    def test_strings_are_cleaned_everywhere(self, paths):
        root = str(paths.root)
        body = {"a": f"see {root}/neo4j-history/x", "b": ["ok", {"c": f"{root}/raw/y"}],
                "n": 3, "f": 1.5, "none": None, "flag": True}  # fmt: skip
        out = G.clean_tree(body, paths)
        assert root not in str(out) and "neo4j-history" in out["a"]
        assert out["n"] == 3 and out["f"] == 1.5 and out["none"] is None and out["flag"] is True
        assert out["b"][0] == "ok"

    def test_a_long_string_is_capped_at_400(self, paths):
        assert len(G.clean_tree("x" * 5000, paths)) <= 400

    def test_a_key_shaped_text_is_masked(self, paths):
        out = G.clean_tree({"note": "the key is sk-or-v1-PLANTEDPLANTEDPLANTED1234 ok"}, paths)
        assert "PLANTED" not in out["note"]

    def test_tuples_become_lists(self, paths):
        assert G.clean_tree(("a", ("b",)), paths) == ["a", ["b"]]
