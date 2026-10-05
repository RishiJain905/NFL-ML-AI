"""The P08 graph additions (no Neo4j): tables, GDS jobs and converters.

- Tables (`graph/tables_extra.py`): FTN play action per team-game and its as-of rule (a
  backtest drops week W-1), a QB's style per game, usage profiles (shares, vectors, sample
  floors, future invariance) and the optional coaching seed (missing / malformed / good).
- GDS (`graph/gds.py`) with a fake driver: PageRank shares, the hub and the network without
  him, KNN top-k, fail-soft (GDS missing, one job failing, no driver text in the result).
- Converters (`graph/insights_advanced.py`): code-made wording, direction words, owners,
  confidence and stable ids for Q5 tree, Q6, Q7, Q9 and the two Q10 stories.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from graph_world import (
    BT_KEY,
    KEY,
    PLAYS_SCHEMA,
    SNAPS_SCHEMA,
    FakeDriver,
    differing_tables,
    frame,
    make_inputs,
    pairs,
    play,
)

from nflengine.digest import format as F
from nflengine.graph import gds as G
from nflengine.graph import insights as gi
from nflengine.graph import insights_advanced as ga
from nflengine.graph import queries as Q
from nflengine.graph import tables as T
from nflengine.graph import tables_extra as X
from nflengine.graph.tables import GraphInputs, GraphKey, build_tables

UTC = dt.UTC
SEASON = 2026
GAME = "2026_04_SF_KC"
FTN_SCHEMA = {
    "game_id": pl.String,
    "play_id": pl.Float64,
    "season": pl.Int32,
    "week": pl.Int32,
    "is_play_action": pl.Boolean,
}


@pytest.fixture(scope="module")
def inp() -> GraphInputs:
    return make_inputs()


def no_numbers(text: str) -> bool:
    return not F.number_atoms(text)


# ---- FTN play action -----------------------------------------------------------------------------


def _dropbacks(game: str, team: str, opp: str, n: int, start: int = 1) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        r = play(game, team, opp, "pass", 0.1, passer="00-KCQB", receiver="00-KCWR", yds=5)
        r["play_id"] = float(start + i)
        out.append(r)
    return out


def _ftn(game: str, n: int, pa: int, start: int = 1) -> list[dict[str, Any]]:
    season, week = int(game[:4]), int(game[5:7])
    return [
        {
            "game_id": game,
            "play_id": float(start + i),
            "season": season,
            "week": week,
            "is_play_action": i < pa,
        }
        for i in range(n)
    ]


def test_visible_ftn_drops_week_w_always_and_week_w_minus_1_in_a_backtest() -> None:
    ftn = frame(
        _ftn("2026_02_SF_KC", 2, 1) + _ftn("2026_03_DEN_KC", 2, 1) + _ftn(GAME, 2, 1),
        FTN_SCHEMA,
    )
    assert set(X.visible_ftn(ftn, KEY)["week"]) == {2, 3}  # live: what the snapshot has
    assert set(X.visible_ftn(ftn, BT_KEY)["week"]) == {2}  # Tuesday: week 3 not out yet
    assert X.visible_ftn(ftn.clear(), KEY).is_empty()


def test_play_action_rate_per_team_game_needs_enough_charted_dropbacks(
    inp: GraphInputs,
) -> None:
    g2, g3 = "2026_02_SF_KC", "2026_03_DEN_KC"
    plays = frame(_dropbacks(g2, "KC", "SF", 12) + _dropbacks(g3, "KC", "DEN", 8), PLAYS_SCHEMA)
    ftn = frame(_ftn(g2, 12, 3) + _ftn(g3, 8, 8), FTN_SCHEMA)
    out = X.play_action_rates(dataclasses.replace(inp, plays=plays, ftn=ftn), KEY)
    rows = {(r["game_id"], r["team_id"]): r for r in out.iter_rows(named=True)}
    assert set(rows) == {(g2, "KC")}  # g3 has 8 charted dropbacks < PA_MIN_DROPBACKS
    assert rows[(g2, "KC")]["pa_rate"] == pytest.approx(0.25)
    assert rows[(g2, "KC")]["pa_dropbacks"] == 12


def test_played_in_carries_pa_rate_and_a_backtest_lacks_last_weeks(inp: GraphInputs) -> None:
    g2, g3 = "2026_02_SF_KC", "2026_03_DEN_KC"
    plays = pl.concat(
        [
            inp.plays,
            frame(
                _dropbacks(g2, "KC", "SF", 10, 100) + _dropbacks(g3, "KC", "DEN", 10, 200),
                PLAYS_SCHEMA,
            ),
        ]
    )
    ftn = frame(_ftn(g2, 10, 5, 100) + _ftn(g3, 10, 2, 200), FTN_SCHEMA)
    live = build_tables(dataclasses.replace(inp, plays=plays, ftn=ftn), KEY)
    bt = build_tables(dataclasses.replace(inp, plays=plays, ftn=ftn), BT_KEY)
    rate = {
        (r["e"], r["s"]): r.get("pa_rate") for r in live.rels["PLAYED_IN"].iter_rows(named=True)
    }
    assert rate[(g2, "KC")] == pytest.approx(0.5) and rate[(g3, "KC")] == pytest.approx(0.2)
    bt_rate = {
        (r["e"], r["s"]): r.get("pa_rate") for r in bt.rels["PLAYED_IN"].iter_rows(named=True)
    }
    assert bt_rate[(g2, "KC")] == pytest.approx(0.5) and bt_rate[(g3, "KC")] is None


def test_future_ftn_rows_change_no_table(inp: GraphInputs) -> None:
    base = build_tables(inp, KEY)
    ftn = frame(_ftn(GAME, 30, 30), FTN_SCHEMA)
    assert differing_tables(base, build_tables(dataclasses.replace(inp, ftn=ftn), KEY)) == []


# ---- QB style per game ---------------------------------------------------------------------------


def test_qb_style_counts_scrambles_and_air_yards_on_attempts() -> None:
    g = "2026_02_SF_KC"
    rows = [
        play(g, "KC", "SF", "pass", 0.5, passer="00-KCQB", receiver="00-KCWR", yds=12)
        | {"air_yards": 10.0},
        play(g, "KC", "SF", "incomplete", -0.3, passer="00-KCQB", receiver="00-KCWR")
        | {"air_yards": 20.0},
        play(g, "KC", "SF", "pass", 0.1, passer="00-KCQB", receiver="00-KCTE"),  # no air yards
        play(g, "KC", "SF", "scramble", 0.4, passer="00-KCQB"),
        play(g, "KC", "SF", "sack", -1.0, passer="00-KCQB") | {"air_yards": 50.0},
        play(g, "KC", "SF", "two_pt", 0.9, passer="00-KCQB", receiver="00-KCWR")
        | {"air_yards": 2.0},
    ]
    out = X.qb_style_stats(frame(rows, PLAYS_SCHEMA))
    r = out.row(0, named=True)
    assert (r["pid"], r["game_id"]) == ("00-KCQB", g)
    assert r["scrambles"] == 1
    assert (r["air_yards"], r["air_att"]) == (30.0, 2)  # no sack, no 2-pt, no null air yards


def test_appeared_in_gets_qb_style_only_for_visible_games(inp: GraphInputs) -> None:
    gt = build_tables(inp, KEY)
    rows = {(r["s"], r["e"]): r for r in gt.rels["APPEARED_IN"].iter_rows(named=True)}
    assert rows[("00-KCQB", "2026_01_LV_KC")]["scrambles"] == 1
    assert rows[("00-KCQB2", "2026_03_DEN_KC")]["scrambles"] == 1
    assert ("00-KCQB", GAME) not in rows


# ---- usage profiles ------------------------------------------------------------------------------

WR_A, TE_B, WR_C, WR_D = "00-A", "00-B", "00-C", "00-D"


def _usage_plays() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pid = 0

    def tgt(game, rec, air, yds, yl=50.0):
        nonlocal pid
        pid += 1
        r = play(game, "KC", "LV", "pass" if yds else "incomplete", 0.1, passer="00-KCQB",
                 receiver=rec, yds=yds)  # fmt: skip
        r |= {"play_id": float(pid), "air_yards": air, "yardline_100": yl}
        rows.append(r)

    def carry(game, rb, yds, yl=50.0):
        nonlocal pid
        pid += 1
        r = play(game, "KC", "LV", "run", 0.0)
        r |= {"play_id": float(pid), "rusher_player_id": rb, "rushing_yards": yds,
              "yardline_100": yl}  # fmt: skip
        rows.append(r)

    for wk in range(1, 9):  # 2025: 8 games
        g = f"2025_{wk:02d}_LV_KC"
        for i in range(6):
            tgt(g, WR_A, 12.0, 15, yl=10.0 if i == 0 else 50.0)
        for _ in range(5):
            tgt(g, TE_B, 5.0, 8)
        if wk <= 2:  # 2 games only: under the past floor
            tgt(g, WR_D, 30.0, 40)
        carry(g, "00-RB", 4.0)
    for wk in range(1, 4):  # 2026 weeks 1-3 (before W = 4)
        g = f"2026_{wk:02d}_LV_KC"
        for _ in range(5):
            tgt(g, WR_C, 9.0, 11)
        tgt(g, WR_A, 3.0, 2)
    # week W and later: must change nothing
    for _ in range(10):
        tgt("2026_04_LV_KC", WR_C, 40.0, 50)
    return rows


def _usage_inputs(inp: GraphInputs, extra: list[dict[str, Any]] | None = None) -> GraphInputs:
    rows = _usage_plays() + (extra or [])
    snaps = frame(
        [
            {
                "gsis_id": WR_A,
                "game_id": f"2025_{wk:02d}_LV_KC",
                "team": "KC",
                "offense_snaps": 60.0,
                "offense_pct": 0.9,
            }  # fmt: skip
            for wk in range(1, 9)
        ],
        SNAPS_SCHEMA,
    )
    return dataclasses.replace(inp, plays=frame(rows, PLAYS_SCHEMA), snaps=snaps)


PLAYERS = pl.DataFrame(
    {
        "player_id": [WR_A, TE_B, WR_C, WR_D, "00-RB"],
        "name": ["A", "B", "C", "D", "R"],
        "position_group": ["WR", "TE", "WR", "WR", "RB"],
    }
)


def test_usage_profiles_shares_floors_and_vectors(inp: GraphInputs) -> None:
    nodes, rel = X.usage_profiles(_usage_inputs(inp), KEY, PLAYERS)
    got = {r["key"]: r for r in nodes.iter_rows(named=True)}
    # D has 2 games in 2025 (floor 8); A's 2026 has 3 targets (floor 12); the RB 8 carries
    assert set(got) == {f"{WR_A}:2025", f"{TE_B}:2025", f"{WR_C}:2026"}
    a = got[f"{WR_A}:2025"]
    assert a["target_share"] == pytest.approx(48 / 90, abs=1e-4)  # D's 2 targets count too
    assert a["adot"] == pytest.approx(12.0)
    assert a["rz_share"] == pytest.approx(1.0)  # every red-zone target was his
    assert a["snap_share"] == pytest.approx(0.9)
    assert a["games"] == 8 and a["current"] is False and a["group"] == "WR"
    c = got[f"{WR_C}:2026"]
    assert c["current"] is True and c["games"] == 3 and c["snap_share"] is None
    assert c["target_share"] == pytest.approx(15 / 18, abs=1e-4)
    # vectors: one z-score per feature of the group, centered within the group
    assert len(a["vector"]) == len(X.RECEIVER_FEATURES)
    for i in range(len(X.RECEIVER_FEATURES)):
        assert a["vector"][i] + c["vector"][i] == pytest.approx(0.0, abs=1e-3)
    assert got[f"{TE_B}:2025"]["vector"] == [0.0] * len(X.RECEIVER_FEATURES)  # alone: z = 0
    assert a["yds_rank"] == 1 and a["basis"] == ",".join(X.RECEIVER_FEATURES)
    assert pairs(rel) == {(WR_A, f"{WR_A}:2025"), (TE_B, f"{TE_B}:2025"), (WR_C, f"{WR_C}:2026")}


def test_usage_profiles_ignore_plays_at_or_after_week_w(inp: GraphInputs) -> None:
    base, _ = X.usage_profiles(_usage_inputs(inp), KEY, PLAYERS)
    later = [
        play("2026_05_LV_KC", "KC", "LV", "pass", 0.1, passer="00-KCQB", receiver=WR_C, yds=99)
        | {"play_id": 999.0, "air_yards": 60.0}
    ]
    again, _ = X.usage_profiles(_usage_inputs(inp, later), KEY, PLAYERS)
    assert base.equals(again)


def test_usage_profiles_need_plays_with_receivers(inp: GraphInputs) -> None:
    nodes, rel = X.usage_profiles(dataclasses.replace(inp, plays=pl.DataFrame()), KEY, PLAYERS)
    assert nodes.is_empty() and rel.is_empty()


def test_build_tables_loads_usage_profiles_and_has_profile(inp: GraphInputs) -> None:
    gt = build_tables(inp, KEY)
    assert "UsageProfile" in gt.nodes and "HAS_PROFILE" in gt.rels
    assert gt.expected_counts()["node:UsageProfile"] == gt.nodes["UsageProfile"].height


# ---- the coaching seed ---------------------------------------------------------------------------


def test_seed_missing_is_no_seed(tmp_path: Path) -> None:
    assert X.read_coaching_seed(tmp_path / "nope.csv").is_empty()


def test_seed_template_with_only_a_header_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "seed.csv"
    p.write_text("# a comment\ncoach,team,season,role,head_coach\n", encoding="utf-8")
    assert X.read_coaching_seed(p).is_empty()


def test_the_shipped_template_is_header_only() -> None:
    from nflengine.settings import CONFIG_DIR

    p = CONFIG_DIR / "coaching_seed.example.csv"
    assert p.exists()
    assert X.read_coaching_seed(p).is_empty()


def test_a_malformed_seed_is_logged_and_skipped(tmp_path: Path) -> None:
    p = tmp_path / "seed.csv"
    p.write_text("name,team\nX,KC\n", encoding="utf-8")
    logs: list[str] = []
    assert X.read_coaching_seed(p, log=logs.append).is_empty()
    assert logs and "lacks columns" in logs[0]
    p.write_text("coach,team,season,role\nX,KC,not-a-year,OC\n", encoding="utf-8")
    logs.clear()
    assert X.read_coaching_seed(p, log=logs.append).is_empty()
    assert logs and "unreadable" in logs[0] and "not-a-year" not in logs[0]


def _seed(tmp_path: Path) -> pl.DataFrame:
    p = tmp_path / "seed.csv"
    p.write_text(
        "coach,team,season,role,head_coach\n"
        "Matt Nagy,KC,2025,OC,\n"  # boss from the schedule: Andy Reid
        "Matt Nagy,KC,2026,OC,\n"
        "Kyle Shan,LV,2016,OC,Mike Old\n"  # before the graph: the boss is given
        "Sean Pay,KC,2025,QB coach,\n"  # not a coordinator, still a WORKED_UNDER
        "Late Guy,KC,2027,DC,\n",  # a future season: invisible
        encoding="utf-8",
    )
    return X.read_coaching_seed(p)


def test_seed_frames_coordinators_bosses_and_visibility(inp: GraphInputs, tmp_path: Path) -> None:
    gt = build_tables(dataclasses.replace(inp, coaching_seed=_seed(tmp_path)), KEY)
    coord = {
        (r["s"], r["e"], r["season"]): r["role"]
        for r in gt.rels["COORDINATOR_OF"].iter_rows(named=True)
    }
    assert coord == {
        ("matt-nagy", "KC", 2025): "OC",
        ("matt-nagy", "KC", 2026): "OC",
        ("kyle-shan", "LV", 2016): "OC",
    }
    under = {(r["s"], r["e"]): r for r in gt.rels["WORKED_UNDER"].iter_rows(named=True)}
    assert set(under) == {
        ("matt-nagy", "andy-reid"),
        ("kyle-shan", "mike-old"),
        ("sean-pay", "andy-reid"),
    }
    assert under[("matt-nagy", "andy-reid")]["seasons"] == [2025, 2026]
    assert under[("matt-nagy", "andy-reid")]["teams"] == ["KC"]
    coaches = set(gt.nodes["Coach"]["coach_id"])
    assert {"matt-nagy", "mike-old", "andy-reid"} <= coaches and "late-guy" not in coaches
    assert gt.dropped["WORKED_UNDER"] == 0 and gt.dropped["COORDINATOR_OF"] == 0


def test_no_seed_gives_empty_seed_relationships(inp: GraphInputs) -> None:
    gt = build_tables(inp, KEY)
    assert gt.rels["COORDINATOR_OF"].is_empty() and gt.rels["WORKED_UNDER"].is_empty()


# ---- GDS jobs (fake driver) ----------------------------------------------------------------------


def _gds_respond(*, fail_pass: Exception | None = None, written: dict[str, Any] | None = None):
    def respond(query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if "gds.version()" in query:
            return [{"v": "2.13.13"}]
        if "gds.graph.list()" in query:
            return [{"names": []}]
        if "AS out_source" in query and "q0" not in params:  # the library's Q0
            return [
                {"team": "KC", "player_id": "WR", "position_group": "WR"},
                {"team": "KC", "player_id": "OL1", "position_group": "OL"},
                {"team": "LV", "player_id": "ZZ", "position_group": "WR"},
            ]
        if "RETURN DISTINCT t.team_id" in query:
            if fail_pass:
                raise fail_pass
            return [{"team": "KC"}]
        if "gds.graph.project($name, q, r" in query:
            return [{"nodes": 3, "rels": 2}]
        if "gds.pageRank.stream" in query:
            assert params["config"]["relationshipTypes"] == ["KC"]
            rows = (
                [(1, "QB", "QB", 1.0), (3, "TE", "TE", 1.0)]
                if params["name"].endswith("-this-week")
                else [(1, "QB", "QB", 2.0), (2, "WR", "WR", 1.5), (3, "TE", "TE", 0.5)]
            )
            return [
                {"nodeId": n, "player_id": pid, "name": pid[0], "grp": grp, "score": sc}
                for n, pid, grp, sc in rows
                if n in params["ids"]
            ]
        if "gds.degree.stream" in query:
            assert params["team"] == "KC"
            return [
                {"nodeId": 1, "score": 30.0},
                {"nodeId": 2, "score": 20.0},
                {"nodeId": 3, "score": 10.0},
            ]
        if "count(CASE WHEN u.current" in query:
            return [{"cur": 1, "past": 5}] if params["g"] == "WR" else [{"cur": 0, "past": 5}]
        if "gds.knn.filtered.stream" in query:
            return [
                {
                    "player_id": "C",
                    "other_id": "X",
                    "other_season": 2021,
                    "other_team": "CIN",
                    "basis": "b",
                    "similarity": 0.9,
                },  # fmt: skip
                {
                    "player_id": "C",
                    "other_id": "X",
                    "other_season": 2020,
                    "other_team": "CIN",
                    "basis": "b",
                    "similarity": 0.85,
                },  # fmt: skip
                {
                    "player_id": "C",
                    "other_id": "Y",
                    "other_season": 2019,
                    "other_team": "LA",
                    "basis": "b",
                    "similarity": 0.8,
                },  # fmt: skip
                {
                    "player_id": "C",
                    "other_id": "Z",
                    "other_season": 2022,
                    "other_team": "KC",
                    "basis": "b",
                    "similarity": 0.7,
                },  # fmt: skip
            ]
        if written is not None and "PASS_CENTRALITY" in query and "UNWIND" in query:
            written["centrality"] = params["rows"]
        if written is not None and "SIMILAR_TO" in query and "UNWIND" in query:
            written["similar"] = params["rows"]
        return []

    return respond


def test_shares_leave_qbs_out_and_rank_receivers() -> None:
    sh = G._shares({"Q": (2.0, "QB"), "W": (1.5, "WR"), "T": (0.5, "TE")})
    assert sh == {"W": (0.75, 1), "T": (0.25, 2)}
    assert G._shares({}) == {}


def test_pass_network_writes_shares_the_hub_and_this_weeks_network() -> None:
    written: dict[str, Any] = {}
    drv = FakeDriver(respond=_gds_respond(written=written))
    info = G.pass_network(drv, KEY)
    # Q0's out WR is in KC's network; its OL and LV's WR (no LV network) are ignored
    assert info["teams"] == 1 and info["written"] == 3 and info["out"] == 1
    assert info["hubs"]["KC"] == {"hub_id": "WR", "hub": "W", "share": 0.75}
    assert G.pass_network(FakeDriver(respond=lambda q, p: []), KEY)["written"] == 0
    rows = {r["player_id"]: r for r in written["centrality"]}
    assert rows["WR"]["props"]["rank"] == 1 and rows["WR"]["props"]["share"] == 0.75
    assert rows["TE"]["props"]["share_this_week"] == 1.0
    assert rows["TE"]["props"]["rank_this_week"] == 1
    assert rows["WR"]["props"]["out_this_week"] is True
    assert rows["TE"]["props"]["out_this_week"] is False
    assert "share_this_week" not in rows["WR"]["props"]
    assert rows["QB"]["props"]["role"] == "passer" and "share" not in rows["QB"]["props"]
    assert {r["props"]["hub_id"] for r in rows.values()} == {"WR"}
    assert rows["WR"]["props"]["degree"] == 20.0 and rows["WR"]["season"] == KEY.season
    # each projection is dropped after use, the second one without the hub
    drops = [p["n"] for q, p in drv.calls if "gds.graph.drop" in q]
    assert drops == ["nfl-pass", "nfl-pass-this-week"]
    project = [p for q, p in drv.calls if "gds.graph.project($name, q, r" in q]
    assert [p["exclude"] for p in project] == [[], ["KC:WR"]]
    # this week's network is run for the players who will play only
    pr = [p for q, p in drv.calls if "gds.pageRank.stream" in q]
    assert [sorted(p["ids"]) for p in pr] == [[1, 2, 3], [1, 3]]


def test_player_similarity_keeps_the_top_k_per_player() -> None:
    written: dict[str, Any] = {}
    drv = FakeDriver(respond=_gds_respond(written=written))
    info = G.player_similarity(drv, KEY)
    assert info["per_group"] == {"WR": 3, "TE": 0, "RB": 0}
    rows = written["similar"]
    assert [(r["other_id"], r["other_season"], r["props"]["rank"]) for r in rows] == [
        ("X", 2021, 1),
        ("X", 2020, 2),
        ("Y", 2019, 3),
    ]
    assert rows[0]["props"]["score"] == 0.9 and rows[0]["season"] == KEY.season
    knn = [p["config"] for q, p in drv.calls if "gds.knn.filtered.stream" in q]
    assert knn and knn[0]["sourceNodeFilter"] == "Current" and knn[0]["targetNodeFilter"] == "Past"
    assert knn[0]["nodeProperties"] == {"vector": "EUCLIDEAN"} and knn[0]["concurrency"] == 1
    assert [p["n"] for q, p in drv.calls if "gds.graph.drop" in q] == ["nfl-usage-WR"]


def test_run_gds_jobs_ok() -> None:
    res = G.run_gds_jobs(FakeDriver(respond=_gds_respond()), KEY, log=lambda m: None)
    assert res.status == "ok" and res.version == "2.13.13"
    assert set(res.jobs) == {"pass_network", "player_similarity"}
    assert all(j["status"] == "ok" for j in res.jobs.values())
    assert "pass_network ok" in res.summary
    d = res.to_dict()
    assert d["status"] == "ok" and d["jobs"]["pass_network"]["written"] == 3


SENTINEL = "SENTINEL-server-text-must-not-leak"


def test_one_failed_job_is_partial_and_the_other_job_still_runs() -> None:
    logs: list[str] = []
    drv = FakeDriver(respond=_gds_respond(fail_pass=RuntimeError(SENTINEL)))
    res = G.run_gds_jobs(drv, KEY, log=logs.append)
    assert res.status == "partial"
    assert res.jobs["pass_network"] == {
        "status": "failed",
        "seconds": res.jobs["pass_network"]["seconds"],
        "error": "RuntimeError",
    }
    assert res.jobs["player_similarity"]["status"] == "ok"
    assert SENTINEL not in str(res.to_dict()) and not any(SENTINEL in m for m in logs)
    # leftovers are dropped before the jobs and after each one
    assert sum("gds.graph.list()" in q for q, _ in drv.calls) == 3


def test_gds_missing_is_unavailable_and_never_raises() -> None:
    def respond(query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        raise RuntimeError(SENTINEL)

    logs: list[str] = []
    res = G.run_gds_jobs(FakeDriver(respond=respond), KEY, log=logs.append)
    assert res.status == "unavailable" and res.error == "RuntimeError" and not res.jobs
    assert SENTINEL not in str(res.to_dict()) and not any(SENTINEL in m for m in logs)
    down = G.run_gds_jobs(FakeDriver(fail=OSError(SENTINEL)), KEY, log=logs.append)
    assert down.status == "unavailable" and down.error == "OSError: Neo4j unreachable"


def test_library_queries_run_with_unrecognized_notifications_off() -> None:
    drv = FakeDriver()
    Q.run_query(drv, "q5_coaching_tree", 2026, 4)
    assert drv.session_configs == [Q.QUIET]


# ---- converters ----------------------------------------------------------------------------------


def ft_row(**kw: Any) -> dict[str, Any]:
    r = {
        "insight_type": "former_teammates",
        "game_id": "2024_14_ATL_MIN",
        "team": "ATL",
        "opponent": "MIN",
        "qb_id": "QB1",
        "qb": "Kirk Cousins",
        "qb_source": "last_game",
        "receiver_id": "WR1",
        "receiver": "Justin Jefferson",
        "receiver_position": "WR",
        "targets": 620,
        "catches": 420,
        "yards": 6402,
        "tds": 34,
        "games_together": 60,
        "seasons": [2023, 2020, 2021, 2022],
        "teams_together": ["MIN"],
        "last_season": 2023,
        "games_now": 12,
        "target_share_now": 0.284,
        "sample_size": 620,
        "strength": 0.9,
    }
    return r | kw


def test_former_teammates_wording_owners_and_confidence() -> None:
    ins = ga._former_teammates(ft_row(), 2024)
    assert ins.insight_id == "former_teammates:QB1:WR1" and ins.section == "non_obvious"
    assert ins.headline == (
        "Kirk Cousins (Falcons QB) faces Justin Jefferson (Vikings WR), a receiver he used to "
        "throw to."
    )
    assert ins.facts[0].text == (
        "Kirk Cousins targeted Justin Jefferson 620 times for the Vikings in 2020, 2021, 2022 "
        "and 2023: 420 catches, 6,402 yards and 34 touchdowns"
    )
    assert ins.facts[0].owners == ["QB1", "WR1"]
    assert ins.facts[1].text == (
        "This season Justin Jefferson has had 28% of the Vikings' targets in the 12 games he "
        "played for them"
    )
    assert ins.facts[1].owners == ["WR1", "MIN"]
    assert ins.confidence == "medium" and not ins.note
    assert {p.role for p in ins.people} == {"former teammate"}
    assert all(p.role in gi.SUBJECT_ROLES for p in ins.people)
    low = ga._former_teammates(ft_row(targets=45, catches=30, yards=300, tds=1), 2024)
    assert low.confidence == "low" and "only 45 times" in low.note
    assert low.note.startswith("A small sample")  # a hedge phrase the checks know


def style_row(**kw: Any) -> dict[str, Any]:
    r = {
        "insight_type": "style_matchup",
        "style": "scramble",
        "game_id": "2025_10_DET_WAS",
        "team": "DET",
        "opponent": "WAS",
        "qb_id": "QB9",
        "qb": "Jayden Daniels",
        "qb_source": "last_game",
        "qb_dropbacks": 946,
        "qb_value": 0.1268,
        "cut": 0.0677,
        "def_epa_style": 0.0961,
        "def_epa_other": -0.0856,
        "lg_epa_style": 0.0399,
        "lg_epa_other": -0.0204,
        "gap": 0.1214,
        "n_style": 15,
        "n_other": 31,
        "lg_n_style": 416,
        "lg_n_other": 994,
        "since": 2023,
        "sample_size": 15,
        "strength": 0.612,
    }
    return r | kw


def test_style_matchup_direction_comes_from_the_gap() -> None:
    ins = ga._style_matchup(style_row(), 2025)
    assert ins.insight_id == "style_matchup:DET:scramble:QB9"
    assert "fared worse than usual against scrambling QBs" in ins.headline
    assert ins.facts[0].text.startswith("Jayden Daniels has scrambled on 12.7% of his dropbacks")
    assert "do so on 6.8% or more" in ins.facts[0].text and ins.facts[0].owners == ["QB9"]
    assert ins.facts[1].text == (
        "Since the start of the 2023 season the Lions defense allowed +0.10 EPA per play in 15 "
        "games against scrambling QBs, against -0.09 EPA per play in 31 games against other QBs"
    )
    assert ins.facts[2].text.endswith("the Lions defense has done worse than usual against "
                                      "scrambling QBs")  # fmt: skip
    assert [p.role for p in ins.people] == ["opposing QB"]
    better = ga._style_matchup(style_row(gap=-0.08, style="deep", qb_value=9.1, cut=8.4), 2025)
    assert "better than usual against deep-passing QBs" in better.headline
    assert "9.1 air yards per pass attempt" in better.facts[0].text
    assert ga._style_matchup(style_row(n_style=5), 2025).confidence == "low"


def test_play_action_matchup_is_its_own_type() -> None:
    r = style_row(
        insight_type="play_action",
        style="play_action",
        offense_games=7,
        offense_rate=0.2763,
        cut=0.245,
        team="DEN",
        opponent="LV",
    )
    for k in ("qb", "qb_id", "qb_source", "qb_dropbacks", "qb_value"):
        r.pop(k)
    ins = ga._play_action(r, 2025)
    assert ins.insight_type == "play_action" and ins.insight_id == "play_action:DEN:play_action:LV"
    assert ins.graph_query == "q7_play_action" and not ins.people
    assert ins.facts[0].text.startswith("The Raiders have used play action on 27.6% of their")
    assert ins.facts[0].owners == ["LV"]
    assert "the Raiders offense this week" in ins.brief


def ref_row(**kw: Any) -> dict[str, Any]:
    r = {
        "insight_type": "officiating",
        "game_id": "2025_10_DET_WAS",
        "team": "WAS",
        "opponent": "DET",
        "official_id": "690",
        "referee": "Clay Martin",
        "ref_games": 40,
        "ref_pen": 15.9,
        "ref_yds": 130.2,
        "ref_split": -1.2,
        "ref_split_games": 38,
        "lg_games": 800,
        "lg_pen": 13.1,
        "lg_yds": 110.0,
        "lg_split": -0.3,
        "pen_gap": 2.8,
        "split_gap": -0.9,
        "first_season": 2023,
        "sample_size": 40,
        "strength": 0.45,
    }
    return r | kw


def test_officiating_is_low_confidence_and_names_the_referee() -> None:
    ins = ga._officiating(ref_row(), 2025)
    assert ins.insight_id == "officiating:690" and ins.confidence == "low" and ins.note
    assert (
        ins.headline
        == "Clay Martin referees this game; his crews have called more penalties than average."
    )
    assert ins.facts[0].text == (
        "In the 40 games Clay Martin refereed since the start of the 2023 season, the two teams "
        "drew 15.9 penalties per game and 130.2 penalty yards per game, against a league "
        "average of 13.1 penalties per game and 110.0 penalty yards per game"
    )
    assert ins.facts[1].text == (
        "In his games away from neutral sites, home teams drew 1.2 penalties per game fewer "
        "than visiting teams, against 0.3 penalties per game fewer league-wide"
    )
    assert ins.people[0].player_id == "official:690" and ins.people[0].team == ""
    split = ga._officiating(ref_row(pen_gap=0.4, split_gap=1.6, ref_split=1.3), 2025)
    assert "flagged home teams more often than usual" in split.headline


def tree_row(**kw: Any) -> dict[str, Any]:
    r = {
        "insight_type": "coaching_tree",
        "kind": "coordinator_vs_boss",
        "game_id": "2026_04_SF_KC",
        "team": "KC",
        "opponent": "SF",
        "coach_id": "matt-nagy",
        "coach": "Matt Nagy",
        "role": "OC",
        "other_id": "kyle-shan",
        "other": "Kyle Shan",
        "steps": [
            {"from": "Matt Nagy", "to": "Kyle Shan", "seasons": [2016, 2017], "teams": ["SF"]}
        ],
        "sample_size": 2,
        "strength": 0.7,
    }
    return r | kw


def test_coaching_tree_kinds() -> None:
    ins = ga._coaching_tree(tree_row(), 2026)
    assert ins is not None
    assert ins.headline == (
        "Matt Nagy, the Chiefs' offensive coordinator, faces Kyle Shan's 49ers: he used to work "
        "under Kyle Shan."
    )
    assert ins.facts[0].text == "Matt Nagy worked under Kyle Shan with the 49ers in 2016 and 2017"
    assert ins.insight_id == "coaching_tree:coordinator_vs_boss:matt-nagy:kyle-shan"
    two = tree_row(
        kind="head_coach_tree",
        role="HC",
        coach="Andy Reid",
        coach_id="andy-reid",
        steps=[
            {"from": "Andy Reid", "to": "Mentor M", "seasons": [1995], "teams": []},
            {"from": "Kyle Shan", "to": "Mentor M", "seasons": [2010], "teams": ["DEN"]},
        ],
    )
    t = ga._coaching_tree(two, 2026)
    assert t is not None and "two steps apart in one coaching tree, through Mentor M" in t.headline
    assert [f.text for f in t.facts] == [
        "Andy Reid worked under Mentor M in 1995",
        "Kyle Shan worked under Mentor M with the Broncos in 2010",
    ]
    assert ga._coaching_tree(tree_row(steps=[]), 2026) is None


def hub_row(**kw: Any) -> dict[str, Any]:
    r = {
        "insight_type": "network_hub",
        "game_id": "2025_10_CLE_NYJ",
        "team": "NYJ",
        "opponent": "CLE",
        "hub_id": "H1",
        "hub": "Garrett Wilson",
        "hub_position": "WR",
        "out_source": "last_week",
        "status": "Out",
        "body_part": "Knee",
        "hub_share": 0.208,
        "hub_rank": 1,
        "hub_targets": 56.0,
        "also_out": [],
        "hub_games": 6,
        "next_id": "N1",
        "next": "Mason Taylor",
        "next_position": "TE",
        "next_share": 0.161,
        "next_rank": 2,
        "next_share_without": 0.2097,
        "next_targets": 42.0,
        "receivers": 12,
        "team_targets": 226.0,
        "team_games": 8,
        "sample_size": 8,
        "strength": 0.574,
    }
    return r | kw


def test_network_hub_wording_and_roles() -> None:
    ins = ga._network_hub(hub_row(), 2025)
    assert ins.insight_id == "network_hub:H1" and ins.confidence == "medium"
    assert ins.headline.startswith(
        "Garrett Wilson (Jets WR), the center of the Jets' passing network"
    )
    assert ins.facts[0].text == (
        "On the Jets' passing network this season (who targeted whom, weighted by targets), "
        "Garrett Wilson has the largest PageRank share of any receiver, 21%, from 56 targets "
        "in 6 games"
    )
    assert ins.facts[1].text == (
        "Without Garrett Wilson (out this week), Mason Taylor leads that network with 21% of "
        "the PageRank share, up from 16% with everyone in it (42 targets so far this season)"
    )
    assert [p.role for p in ins.people] == ["out", "next in network"]
    assert "not who will be targeted this week" in ins.note
    this_week = ga._network_hub(hub_row(out_source="this_week", team_games=3), 2025)
    assert "is listed out for this week (knee)" in this_week.headline
    assert this_week.confidence == "low"
    second = ga._network_hub(hub_row(hub_rank=2, also_out=["A B", "C D"]), 2025)
    assert "the 2nd-most central receiver of the Jets' passing network" in second.headline
    assert "has the 2nd-largest PageRank share among its receivers, 21%" in second.facts[0].text
    assert second.facts[1].text.startswith("Without Garrett Wilson, A B and C D (out")


def comp_row(**kw: Any) -> dict[str, Any]:
    r = {
        "insight_type": "usage_comp",
        "game_id": "2025_10_BAL_MIN",
        "team": "BAL",
        "opponent": "MIN",
        "player_id": "P1",
        "player": "Zay Flowers",
        "position": "WR",
        "grp": "WR",
        "rookie_season": 2023,
        "young": False,
        "games": 8,
        "targets": 60,
        "carries": 4,
        "target_share": 0.2899,
        "air_yards_share": 0.3282,
        "adot": 9.2167,
        "rz_share": 0.1739,
        "snap_share": 0.8675,
        "carry_share": 0.0212,
        "other_id": "O1",
        "other": "Puka Nacua",
        "other_season": 2023,
        "other_team": "LA",
        "other_games": 17,
        "other_targets": 160,
        "other_carries": 12,
        "other_target_share": 0.2759,
        "other_air_yards_share": 0.32,
        "other_adot": 9.1,
        "other_rz_share": 0.19,
        "other_snap_share": 0.87,
        "other_carry_share": 0.03,
        "other_rec_yds": 1486,
        "other_scrimmage_yds": 1575,
        "other_yds_rank": 4,
        "score": 0.7384,
        "group_median": 0.61,
        "basis": "target_share",
        "sample_size": 8,
        "strength": 0.62,
    }
    return r | kw


def test_usage_comp_is_a_comparison_never_a_projection() -> None:
    ins = ga._usage_comp(comp_row(), 2025)
    assert ins.insight_id == "usage_comp:P1"  # one story per player, whoever he matches
    assert ins.headline == (
        "Zay Flowers (Ravens WR) is being used much like Puka Nacua was in 2023, a top-4 season "
        "for a WR."
    )
    assert ins.facts[0].text == (
        "In 8 games this season, Zay Flowers has had 29% of the Ravens' targets, 33% of their "
        "air yards, an average depth of target of 9.2 yards, 17% of their red-zone targets and "
        "87% of offensive snaps"
    )
    assert ins.facts[0].owners == ["P1", "BAL"] and ins.facts[1].owners == ["O1"]
    assert ins.facts[2].text == (
        "Puka Nacua finished 2023 with 1,486 receiving yards, 4th-most among wide receivers "
        "that season"
    )
    assert "not a projection" in ins.note and "not a projection" in ins.brief
    for text in [ins.headline, *(f.text for f in ins.facts), ins.brief]:
        assert "will" not in text.split()
    assert [p.role for p in ins.people] == ["usage comparison", "comparison"]
    rb = ga._usage_comp(
        comp_row(grp="RB", position="RB", young=True, carry_share=0.7, other_carry_share=0.76),
        2025,
    )
    assert ", in his first two seasons," in rb.headline and "a running back" in rb.headline
    assert rb.facts[0].text.startswith(
        "In 8 games this season, Zay Flowers has had 70% of the Ravens' designed carries"
    )
    assert "1,575 scrimmage yards" in rb.facts[2].text
    top = ga._usage_comp(comp_row(other_yds_rank=1), 2025)
    assert top.headline.endswith("in 2023, the most productive season by a WR that year.")
    assert top.facts[2].text.endswith("receiving yards, the most among wide receivers that season")
    assert "1st" not in top.brief


@pytest.mark.parametrize(
    ("name", "row"),
    [
        ("q6_former_teammates", ft_row()),
        ("q7_style_matchup", style_row()),
        ("q9_officiating", ref_row()),
        ("q5_coaching_tree", tree_row()),
        ("q10_network_hub", hub_row()),
        ("q10_usage_comp", comp_row()),
    ],
)
def test_new_converters_make_valid_non_obvious_items(name: str, row: dict[str, Any]) -> None:
    ins = gi.CONVERTERS[name](row, 2025)
    assert ins is not None and ins.section == "non_obvious"
    assert ins.insight_type in gi.SECTION_TYPES["non_obvious"]
    assert gi.QUERY_OF[ins.insight_type] == ins.graph_query
    assert ins.brief and ins.facts and ins.teams[:2] == [row["team"], row["opponent"]]
    # every fact names at least one of its owners' entities or teams (owners are real keys)
    assert all(f.owners for f in ins.facts)


def test_new_types_compete_in_the_non_obvious_slot_and_subjects_are_used_once() -> None:
    games = {
        g: gi.GameInfo(g, h, a, dt.datetime(2025, 11, 9, 18, tzinfo=UTC))
        for g, h, a in [
            ("2024_14_ATL_MIN", "MIN", "ATL"),
            ("2025_10_BAL_MIN", "MIN", "BAL"),
            ("2025_10_DET_WAS", "WAS", "DET"),
        ]
    }
    ft = ga._former_teammates(ft_row(strength=0.9), 2024)
    comp = ga._usage_comp(comp_row(strength=0.8), 2025)
    # the same receiver again as a usage comparison in another game: one item per subject
    dup = ga._usage_comp(
        comp_row(
            strength=0.85, player_id="WR1", player="Justin Jefferson", game_id="2025_10_DET_WAS"
        ),
        2025,
    )
    sel = gi.select([ft, comp, dup], games, dt.datetime(2025, 11, 4, 14, tzinfo=UTC))
    ids = [c.insight_id for c in sel.non_obvious]
    assert ids[0] == ft.insight_id
    assert dup.insight_id not in ids and dup.insight_id in sel.skipped["duplicate_player"]


def test_graph_key_is_still_frozen_and_visibility_docstring_names_p08_rules() -> None:
    assert dataclasses.is_dataclass(GraphKey)
    doc = T.__doc__ or ""
    for rule in ("Officials (P08)", "FTN play action (P08", "Coaching seed (P08", "GDS results"):
        assert rule in doc
