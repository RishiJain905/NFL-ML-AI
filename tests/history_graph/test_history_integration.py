"""The history graph against the real server and the real curated data (AE02). Written by the
test worker, run by the lead:

    uv run pytest -m integration tests/history_graph/test_history_integration.py

Needs the history container up on Bolt 7688 and a finished build (`build.json`, status ok); each
test is skipped (not failed) when either is missing. Every query below is read-only except
`TestAppend`, which runs the weekly append twice on the live graph (a normal
`nfl history-graph append`; it takes the history lock and writes append.json).

What is checked:
- the counts in Neo4j equal `build.json`'s (the bulk import) and, with the appended season,
  `build.json` + `append.json`;
- play, game and drive counts per season equal the curated tables;
- targets / receptions / sacks / interceptions per 2024 player equal the box-score tables
  (curated `player_games`), and one known quarterback -> receiver total equals the plays;
- participation sits on 2018-2025 plays only; every play has its drive, offense and defense;
- constraints and indexes exist and are online; the server is the community 5.26 image;
- running the append twice: the second run plans and writes nothing.

Known difference (2024_15_MIA_HOU play 2274): nflverse calls a fake punt a `no_play`, the box score
counts a carry and a solo tackle; rushes and tackles may differ for that play's two players only.
"""

from __future__ import annotations

import re
import time
from typing import Any

import polars as pl
import pytest

from nflengine.history_graph import schema as S
from nflengine.history_graph import state
from nflengine.history_graph.build import graph_counts

pytestmark = pytest.mark.integration

MAHOMES, KELCE = "00-0033873", "00-0030506"
FAKE_PUNT = "2024_15_MIA_HOU"  # play 2274: no_play in nflverse, a carry + a tackle in the box score
WRITE = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP|LOAD|FOREACH)\b", re.I)


@pytest.fixture(scope="module")
def driver():
    from nflengine.history_graph.client import get_history_driver, history_up

    if not history_up():
        pytest.skip("the history graph isn't up on Bolt 7688 (`uv run nfl history-graph start`)")
    drv = get_history_driver()
    yield drv
    drv.close()


@pytest.fixture(scope="module")
def paths():
    from nflengine.paths import ensure_data_root

    return ensure_data_root(create_dirs=False)


@pytest.fixture(scope="module")
def build(paths) -> dict[str, Any]:
    b = state.read_build(paths)
    if not b:
        pytest.skip("no finished history-graph build (build.json missing or not ok)")
    return b


@pytest.fixture(scope="module")
def append(paths, build) -> dict[str, Any] | None:
    return state.read_append(paths, build)


def q(driver, query: str, **params) -> list[dict[str, Any]]:
    """A read-only query (anything that could write is refused here)."""
    assert not WRITE.search(query), f"not a read-only query: {query}"
    with driver.session() as s:
        return [r.data() for r in s.run(query, **params)]


def one(driver, query: str, **params) -> Any:
    rows = q(driver, query, **params)
    assert len(rows) == 1 and len(rows[0]) == 1, rows
    return next(iter(rows[0].values()))


def curated_plays(paths, season: int, through_week: int | None = None) -> pl.DataFrame:
    df = pl.read_parquet(
        paths.curated / "plays" / f"season={season}.parquet",
        columns=["game_id", "play_id", "week", "play_type", "posteam", "fixed_drive"],
    ).filter(
        pl.col("play_type").is_not_null()
        & pl.col("posteam").is_not_null()
        & pl.col("play_id").is_not_null()
    )
    if through_week is not None:
        df = df.filter(pl.col("week") <= through_week)
    return df


# ---- the server ------------------------------------------------------------------------------


class TestServer:
    def test_it_is_the_history_server_on_the_history_port(self, driver):
        from nflengine.history_graph.config import history_config

        assert history_config().bolt_uri.endswith(":7688")
        row = q(driver, "CALL dbms.components() YIELD name, versions, edition RETURN *")[0]
        assert row["name"] == "Neo4j Kernel" and row["edition"] == "community"
        assert row["versions"][0].startswith("5.26")

    def test_constraints_exist_for_every_node_key(self, driver):
        rows = q(
            driver,
            "SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties, type "
            "RETURN name, labelsOrTypes[0] AS label, properties[0] AS prop, type",
        )
        have = {(r["label"], r["prop"]) for r in rows if r["type"] == "UNIQUENESS"}
        for label, n in S.NODES.items():
            assert (label, n.key) in have, f"no uniqueness constraint on {label}.{n.key}"
        assert all(r["name"].startswith("history_") for r in rows)

    def test_every_history_index_is_online(self, driver):
        rows = q(
            driver,
            "SHOW INDEXES YIELD name, state, type "
            "WHERE name STARTS WITH 'history_' RETURN name, state",
        )
        assert rows, "no history_ indexes"
        assert {r["state"] for r in rows} == {"ONLINE"}
        names = {r["name"] for r in rows}
        assert {"history_play_season_week", "history_player_name"} <= names

    def test_only_the_history_labels_exist(self, driver):
        labels = {r["label"] for r in q(driver, "CALL db.labels() YIELD label RETURN label")}
        assert labels == set(S.NODES)  # nothing of the weekly graph's schema is in this server


# ---- counts ----------------------------------------------------------------------------------


class TestCounts:
    def test_the_bulk_imported_part_equals_build_json(self, driver, build):
        t = int(build["through_season"])
        counts = build["counts"]
        nodes = {
            "Game": "MATCH (n:Game) WHERE n.season <= $t RETURN count(n) AS c",
            "Drive": "MATCH (n:Drive) WHERE n.season <= $t RETURN count(n) AS c",
            "Play": "MATCH (n:Play) WHERE n.season <= $t RETURN count(n) AS c",
            "Season": "MATCH (n:Season) WHERE n.season <= $t RETURN count(n) AS c",
        }
        for label, query in nodes.items():
            assert one(driver, query, t=t) == counts[label], label
        # participation exists only for the imported seasons
        assert one(driver, "MATCH ()-[r:ON_FIELD]->() RETURN count(r)") == counts["ON_FIELD"]
        for rtype in ("PLAYED_FOR", "HEAD_COACH", "OFF_COORD", "DEF_COORD", "ST_COORD"):
            got = one(driver, f"MATCH ()-[r:{rtype}]->() WHERE r.season <= $t RETURN count(r)", t=t)
            assert got == counts[rtype], rtype
        for rtype in ("PASSED", "RUSHED", "TARGETED", "CAUGHT", "INTERCEPTED", "SACKED", "TACKLED"):
            got = one(
                driver, f"MATCH ()-[r:{rtype}]->(p:Play) WHERE p.season <= $t RETURN count(r)", t=t
            )
            assert got == counts[rtype], rtype

    def test_teams_players_coaches_are_at_least_the_builds(self, driver, build):
        c = graph_counts(driver, ["Team", "Player", "Coach"])
        assert c["Team"] == build["counts"]["Team"] == 32
        assert c["Player"] >= build["counts"]["Player"] and c["Coach"] >= build["counts"]["Coach"]

    def test_the_whole_graph_is_the_build_plus_the_appended_season(self, driver, build, append):
        c = graph_counts(driver)
        if not append:
            pytest.skip("no append onto this build yet")
        for label in ("Game", "Drive", "Play"):
            assert c[label] == build["counts"][label] + append["counts"][label], label
        assert c["Season"] == build["counts"]["Season"] + 1

    def test_no_dangling_end_anywhere(self, driver):
        assert (
            one(driver, "MATCH (p:Play) WHERE NOT (p)-[:IN_DRIVE]->(:Drive) RETURN count(p)") == 0
        )
        assert one(driver, "MATCH (p:Play) WHERE NOT (p)-[:OFFENSE]->(:Team) RETURN count(p)") == 0
        assert one(driver, "MATCH (p:Play) WHERE NOT (p)-[:DEFENSE]->(:Team) RETURN count(p)") == 0
        assert one(driver, "MATCH (d:Drive) WHERE NOT (d)-[:IN_GAME]->(:Game) RETURN count(d)") == 0
        assert (
            one(driver, "MATCH (g:Game) WHERE NOT (g)-[:IN_SEASON]->(:Season) RETURN count(g)") == 0
        )

    def test_the_dropped_rows_are_the_known_ones(self, build):
        # participation plays pbp doesn't have are the only thing dropped on the real data
        assert set(build.get("dropped") or {}) <= {"participation_plays_not_in_pbp"}


class TestAgainstCurated:
    def test_plays_games_and_drives_per_season(self, driver, paths, build, append):
        seasons = list(range(int(build["first_season"]), int(build["through_season"]) + 1))
        want_week: dict[int, int | None] = {s: None for s in seasons}
        if append:
            want_week[int(append["season"])] = int(append["through_week"])
        for season, week in want_week.items():
            df = curated_plays(paths, season, week)
            plays = one(driver, "MATCH (p:Play {season: $s}) RETURN count(p)", s=season)
            games = one(driver, "MATCH (g:Game {season: $s}) RETURN count(g)", s=season)
            drives = one(driver, "MATCH (d:Drive {season: $s}) RETURN count(d)", s=season)
            assert plays == df.height, f"{season} plays"
            assert games == df["game_id"].n_unique(), f"{season} games"
            assert (
                drives == df.drop_nulls("fixed_drive").select("game_id", "fixed_drive").n_unique()
            ), season

    def test_play_counts_by_type_for_one_season(self, driver, paths):
        df = curated_plays(paths, 2024)
        want = dict(df.group_by("play_type").len().rows())
        got = {
            r["t"]: r["c"]
            for r in q(
                driver, "MATCH (p:Play {season: 2024}) RETURN p.play_type AS t, count(p) AS c"
            )
        }
        assert got == want

    def test_a_known_quarterback_to_receiver_total(self, driver, paths):
        plays = pl.read_parquet(
            paths.curated / "plays" / "season=2024.parquet",
            columns=[
                "passer_player_id",
                "receiver_player_id",
                "play_type",
                "sack",
                "complete_pass",
                "two_point_attempt",
            ],
        ).filter(
            (pl.col("passer_player_id") == MAHOMES)
            & (pl.col("receiver_player_id") == KELCE)
            & pl.col("play_type").is_in(["pass", "qb_spike"])
            & (pl.col("sack").fill_null(0) != 1)
            & (pl.col("two_point_attempt").fill_null(0) != 1)
        )
        targets, catches = plays.height, int(plays["complete_pass"].sum())
        assert 100 < targets < 200
        got = q(
            driver,
            "MATCH (q:Player {player_id: $q})-[:PASSED]->(p:Play {season: 2024}) "
            "MATCH (r:Player {player_id: $r})-[:TARGETED]->(p) RETURN count(p) AS t",
            q=MAHOMES,
            r=KELCE,
        )
        assert got[0]["t"] == targets
        caught = one(
            driver,
            "MATCH (q:Player {player_id: $q})-[:PASSED]->(p:Play {season: 2024}) "
            "MATCH (r:Player {player_id: $r})-[:CAUGHT]->(p) RETURN count(p)",
            q=MAHOMES,
            r=KELCE,
        )
        assert caught == catches

    def test_kelce_is_mahomes_top_2024_target(self, driver):
        rows = q(
            driver,
            "MATCH (q:Player {player_id: $q})-[:PASSED]->(p:Play {season: 2024}) "
            "MATCH (r:Player)-[:TARGETED]->(p) RETURN r.player_id AS id, count(p) AS n "
            "ORDER BY n DESC LIMIT 1",
            q=MAHOMES,
        )
        assert rows[0]["id"] == KELCE


def box(paths, season: int, cols: list[str]) -> pl.DataFrame:
    return (
        pl.read_parquet(
            paths.curated / "player_games.parquet", columns=["player_id", "season", *cols]
        )
        .filter(pl.col("season") == season)
        .group_by("player_id")
        .agg(*(pl.col(c).sum() for c in cols))
    )


def graph_by_player(driver, rtype: str, season: int, weight: str = "1") -> dict[str, float]:
    rows = q(
        driver,
        f"MATCH (a:Player)-[r:{rtype}]->(p:Play {{season: $s}}) "
        f"RETURN a.player_id AS id, sum({weight}) AS n",
        s=season,
    )
    return {r["id"]: r["n"] for r in rows}


class TestAgainstTheBoxScore:
    """The roles must equal the box score ("count() of a role equals the box-score total")."""

    @pytest.mark.parametrize(
        "rtype,col",
        [("TARGETED", "targets"), ("CAUGHT", "receptions"), ("INTERCEPTED", "def_interceptions")],
    )
    def test_per_player_totals_2024(self, driver, paths, rtype, col):
        want = {
            r["player_id"]: r[col] for r in box(paths, 2024, [col]).iter_rows(named=True) if r[col]
        }
        got = graph_by_player(driver, rtype, 2024)
        diff = {
            k: (got.get(k, 0), want.get(k, 0))
            for k in set(got) | set(want)
            if got.get(k, 0) != want.get(k, 0)
        }
        assert diff == {}, (
            f"{rtype}: {len(diff)} players differ, e.g. {dict(list(diff.items())[:5])}"
        )

    def test_sacks_2024_half_sacks_count_a_half(self, driver, paths):
        want = {
            r["player_id"]: r["def_sacks"]
            for r in box(paths, 2024, ["def_sacks"]).iter_rows(named=True)
            if r["def_sacks"]
        }
        got = graph_by_player(driver, "SACKED", 2024, "CASE WHEN r.half THEN 0.5 ELSE 1.0 END")
        diff = {
            k: (got.get(k, 0), want.get(k, 0))
            for k in set(got) | set(want)
            if abs(got.get(k, 0) - want.get(k, 0)) > 1e-9
        }
        assert diff == {}, f"{len(diff)} players differ, e.g. {dict(list(diff.items())[:5])}"

    def test_rushes_2024_except_the_one_fake_punt(self, driver, paths):
        want = {
            r["player_id"]: r["carries"]
            for r in box(paths, 2024, ["carries"]).iter_rows(named=True)
            if r["carries"]
        }
        got = graph_by_player(driver, "RUSHED", 2024)
        diff = {
            k: got.get(k, 0) - want.get(k, 0)
            for k in set(got) | set(want)
            if got.get(k, 0) != want.get(k, 0)
        }
        # the box score counts a carry on 2024_15_MIA_HOU play 2274 that nflverse calls a no_play
        assert len(diff) <= 1 and all(v == -1 for v in diff.values()), diff

    def test_tackles_2024_except_the_one_fake_punt(self, driver, paths):
        cols = ["def_tackles_solo", "def_tackle_assists", "def_tackles_with_assist"]
        df = box(paths, 2024, cols)
        want = {
            r["player_id"]: r["def_tackles_solo"]
            + r["def_tackle_assists"]
            + r["def_tackles_with_assist"]
            for r in df.iter_rows(named=True)
        }
        want = {k: v for k, v in want.items() if v}
        got = graph_by_player(driver, "TACKLED", 2024)
        diff = {
            k: got.get(k, 0) - want.get(k, 0)
            for k in set(got) | set(want)
            if got.get(k, 0) != want.get(k, 0)
        }
        # measured 2026-10-19: 3 of ~1,400 tacklers one short: the fake punt above (00-0039881),
        # and two (00-0034678, 00-0037259) where nflverse's box score and its play-by-play
        # disagree by a tackle with no no_play or two-point play involved
        assert len(diff) <= 3 and all(v == -1 for v in diff.values()), diff


# ---- the content -----------------------------------------------------------------------------


class TestContent:
    def test_participation_sits_on_2018_2025_plays_only(self, driver, build):
        t = int(build["through_season"])
        assert (
            one(driver, "MATCH (p:Play) WHERE p.part = true AND p.season > $t RETURN count(p)", t=t)
            == 0
        )
        assert (
            one(
                driver,
                "MATCH (p:Play) WHERE p.season > $t AND p.formation IS NOT NULL RETURN count(p)",
                t=t,
            )
            == 0
        )

    def test_participation_plays_per_season_equal_the_research_files(self, driver, paths, build):
        from nflengine.ingest.base import SnapshotStore

        parts = SnapshotStore(paths.research).latest_parts("nflverse", "participation")
        for season in range(int(build["first_season"]), int(build["through_season"]) + 1):
            part = pl.read_parquet(
                parts[f"season={season}"], columns=["nflverse_game_id", "play_id"]
            )
            keys = (
                part.rename({"nflverse_game_id": "game_id"})
                .with_columns(pl.col("play_id").cast(pl.Int64))
                .unique()
            )
            plays = curated_plays(paths, season).select("game_id", pl.col("play_id").cast(pl.Int64))
            want = plays.join(keys, on=["game_id", "play_id"], how="inner").height
            got = one(
                driver, "MATCH (p:Play {season: $s}) WHERE p.part = true RETURN count(p)", s=season
            )
            assert got == want, season

    def test_on_field_links_only_participation_plays(self, driver):
        assert (
            one(driver, "MATCH ()-[:ON_FIELD]->(p:Play) WHERE p.part IS NULL RETURN count(p)") == 0
        )

    def test_ftn_labels_are_2022_on(self, driver):
        assert (
            one(
                driver, "MATCH (p:Play) WHERE p.season < 2022 AND p.ftn IS NOT NULL RETURN count(p)"
            )
            == 0
        )
        assert (
            one(driver, "MATCH (p:Play {season: 2024}) WHERE p.ftn = true RETURN count(p)") > 1_000
        )

    def test_the_position_of_a_quarterback_on_the_field(self, driver):
        rows = q(
            driver,
            "MATCH (:Player {player_id: $q})-[r:ON_FIELD]->(p:Play {season: 2024}) "
            "RETURN r.position AS pos, count(*) AS n ORDER BY n DESC LIMIT 1",
            q=MAHOMES,
        )
        assert rows and rows[0]["pos"] == "QB" and rows[0]["n"] > 500

    def test_a_scramble_is_a_rush_not_a_pass(self, driver):
        n = one(
            driver,
            "MATCH (a:Player)-[:PASSED]->(p:Play {season: 2024, scramble: true}) RETURN count(p)",
        )
        assert n == 0

    def test_a_team_has_one_head_coach_per_season_game(self, driver):
        rows = q(
            driver,
            "MATCH (c:Coach)-[r:HEAD_COACH {season: 2024}]->(t:Team {team_id: 'KC'}) "
            "RETURN c.name AS name, r.games AS games",
        )
        assert [r["name"] for r in rows] == ["Andy Reid"] and rows[0]["games"] >= 17

    def test_every_drive_has_a_team_that_played_in_its_game(self, driver):
        bad = one(
            driver,
            "MATCH (d:Drive {season: 2024})-[:IN_GAME]->(g:Game) "
            "WHERE d.team <> g.home_team AND d.team <> g.away_team RETURN count(d)",
        )
        assert bad == 0

    def test_the_documented_walkthrough_query_is_fast(self, driver):
        t0 = time.monotonic()
        rows = q(
            driver,
            "MATCH (q:Player {player_id: $q})-[:PASSED]->(p:Play {season: 2024}) "
            "MATCH (r:Player)-[:TARGETED]->(p) "
            "RETURN r.name AS receiver, count(p) AS targets ORDER BY targets DESC LIMIT 5",
            q=MAHOMES,
        )
        assert len(rows) == 5 and rows[0]["receiver"] == "Travis Kelce"
        assert time.monotonic() - t0 < 5.0


# ---- the append (WRITES to the live graph) ---------------------------------------------------


class TestAppend:
    """A normal `nfl history-graph append`, twice. The first may write (if the curated plays moved
    on since the last append); the second must plan and write nothing."""

    def test_running_the_append_twice_the_second_has_no_changes(self, driver, paths, build):
        from nflengine.history_graph.append import run_append
        from nflengine.history_graph.config import history_config
        from nflengine.settings import get_config

        if state.history_busy(paths):
            pytest.skip("a build or append is running")
        season = get_config().seasons.current
        if season <= int(build["through_season"]):
            pytest.skip("the current season is inside the bulk import")
        cfg = history_config()
        first = run_append(
            season, paths=paths, cfg=cfg, driver=driver, use_wandb=False, log=lambda *_: None
        )
        assert first["status"] == "ok" and first["check"] == {}
        counts = graph_counts(driver)
        second = run_append(
            season, paths=paths, cfg=cfg, driver=driver, use_wandb=False, log=lambda *_: None
        )
        assert second["status"] == "ok" and second["changes"] == {} and second["check"] == {}
        assert graph_counts(driver) == counts
        assert state.history_asof(paths)["season"] == season
