"""`derive.derived_frames` and `derive.read_snaps` on small hand-made data (AE03, D147).

The league is `history_mini.py`; every expected number below is worked out by hand from its
play list (the comments say how). Nothing reaches Neo4j, Docker, W&B or the data root.
"""

from __future__ import annotations

import polars as pl
import pytest
from history_fakes import no_real_docker  # noqa: F401  (the safety net)
from history_mini import SNAP_SCHEMA, make_frames, mini_frames, mini_snaps

from nflengine.history_graph import derive as D
from nflengine.history_graph import schema as S
from nflengine.history_graph.export import _POLARS


@pytest.fixture(scope="module")
def derived():
    res, dropped = D.derived_frames(mini_frames(), mini_snaps())
    return res, dropped


def row(df: pl.DataFrame, s: str, e: str) -> dict:
    hit = df.filter((pl.col("s") == s) & (pl.col("e") == e))
    assert hit.height == 1, f"{s} -> {e}: {hit.height} rows"
    return hit.to_dicts()[0]


def ends(df: pl.DataFrame) -> set[tuple[str, str]]:
    return set(zip(df["s"].to_list(), df["e"].to_list(), strict=True))


class TestShape:
    def test_every_type_has_exactly_the_schemas_columns_and_types(self, derived):
        res, _ = derived
        assert list(res) == list(S.DERIVED)
        for rtype, spec in S.DERIVED.items():
            df = res[rtype]
            assert df.columns == spec.columns(), rtype
            for prop, t in spec.props.items():
                assert df.schema[prop] == _POLARS[t], (rtype, prop)
            assert df.schema["s"] == pl.String and df.schema["e"] == pl.String

    def test_rows_are_sorted_and_one_per_pair(self, derived):
        res, _ = derived
        for rtype, df in res.items():
            assert df.height == df.select("s", "e").unique().height, rtype
            assert df.select("s", "e").equals(df.select("s", "e").sort("s", "e")), rtype

    def test_the_row_counts(self, derived):
        res, _ = derived
        assert {k: v.height for k, v in res.items()} == {
            "TEAMMATE": 10,
            "CONNECTION": 2,
            "OPPONENT": 6,
            "COACHED": 11,
            "WORKED_WITH": 6,
        }

    def test_what_was_dropped_is_counted(self, derived):
        _, dropped = derived
        assert dropped == {
            "snaps_rows_unknown": 2,  # the unknown player and the unknown team
            "TEAMMATE": 4,  # zz with the four offensive players on g1:4
            "CONNECTION": 1,  # q1 threw to "unk" (g1:11)
            "OPPONENT": 1,  # zz (a WR) against d1 on g1:4
            "COACHED": 1,  # ghost (not a Coach node) -> q1
            "WORKED_WITH": 4,  # ghost with dc, hc, oc, stc
        }


class TestTeammate:
    """Players on the field together on the same side of the same snaps (all snaps: punts and a
    two-point try too), one link per pair, the smaller id first."""

    def test_snaps_per_pair_worked_out_by_hand(self, derived):
        tm = derived[0]["TEAMMATE"]
        # offense: g1:1,2,5 have o1 o2 o3 o4; g1:3 has o1 o3 o4; g1:4 has o1 o2 o3 o4 (+ zz);
        # g1:6 (a two-point try) o1 o2; g1:7 (a punt) o3 o4; g1:8 o1 o2; g1:12 o3 o4 (listed on
        # the defense side: still together)
        want = {
            ("o1", "o2"): 7,  # g1:1,2,4,5,6,8 = 6, plus g3:1 in 2025 = 1
            ("o1", "o3"): 5,  # g1:1,2,3,4,5
            ("o1", "o4"): 5,
            ("o2", "o3"): 4,  # g1:1,2,4,5
            ("o2", "o4"): 4,
            ("o3", "o4"): 7,  # g1:1,2,3,4,5,7,12
            ("d1", "d2"): 5,  # defense on g1:1..5
            ("d1", "d3"): 5,
            ("d2", "d3"): 5,
            ("t1", "t2"): 2,  # g2:1 and g2:2
        }
        assert {(r["s"], r["e"]): r["snaps"] for r in tm.to_dicts()} == want

    def test_the_two_sides_of_a_snap_are_never_teammates(self, derived):
        tm = ends(derived[0]["TEAMMATE"])
        for o in ("o1", "o2", "o3", "o4"):
            for d in ("d1", "d2", "d3"):
                assert (o, d) not in tm and (d, o) not in tm

    def test_the_smaller_id_is_always_first(self, derived):
        tm = derived[0]["TEAMMATE"]
        assert tm.filter(pl.col("s") >= pl.col("e")).height == 0
        # the same on frames whose rows list the larger id first
        fr = mini_frames()
        fr.rels["ON_FIELD"] = fr.rels["ON_FIELD"].reverse()
        res, _ = D.derived_frames(fr, mini_snaps())
        assert res["TEAMMATE"].equals(tm)

    def test_the_stints_are_one_per_season_and_team_in_season_order(self, derived):
        r = row(derived[0]["TEAMMATE"], "o1", "o2")
        assert r["seasons"] == [2024, 2025] and r["teams"] == ["DEN", "KC"]  # sorted, distinct
        assert (r["first_season"], r["last_season"]) == (2024, 2025)
        assert r["stint_seasons"] == [2024, 2025]
        assert r["stint_teams"] == ["KC", "DEN"]  # in stint order, not sorted
        assert r["stint_snaps"] == [6, 1] and sum(r["stint_snaps"]) == r["snaps"]

    def test_the_stints_of_one_pair_add_up_to_its_total_everywhere(self, derived):
        for r in derived[0]["TEAMMATE"].to_dicts():
            assert sum(r["stint_snaps"]) == r["snaps"]
            assert len(r["stint_seasons"]) == len(r["stint_teams"]) == len(r["stint_snaps"])

    def test_a_players_team_is_the_one_he_played_most_in_the_game(self, derived):
        # g1:12 lists o3 and o4 on the defense side (the play's defense is LV): the stint is
        # still KC, the team both played for in g1, and no LV stint appears
        r = row(derived[0]["TEAMMATE"], "o3", "o4")
        assert r["teams"] == ["KC"] and r["stint_teams"] == ["KC"] and r["stint_snaps"] == [7]

    def test_a_tie_goes_to_the_team_code_that_sorts_first(self, derived):
        # t1 and t2 have one snap for LV (g2:1) and one for KC (g2:2) in g2: KC for both
        r = row(derived[0]["TEAMMATE"], "t1", "t2")
        assert r["teams"] == ["KC"] and r["stint_snaps"] == [2]

    def test_defenders_carry_their_own_team(self, derived):
        r = row(derived[0]["TEAMMATE"], "d1", "d2")
        assert r["teams"] == ["LV"] and r["seasons"] == [2024]

    def test_players_the_graph_doesnt_hold_get_no_links(self, derived):
        assert not {s for s, e in ends(derived[0]["TEAMMATE"])} & {"zz"}
        assert not {e for s, e in ends(derived[0]["TEAMMATE"])} & {"zz"}

    def test_a_player_alone_on_a_side_has_no_link(self):
        fr = make_frames(
            [_play("g1:1")],
            [{"s": "o1", "e": "g1:1", "side": "offense", "position": "QB"},
             {"s": "d1", "e": "g1:1", "side": "defense", "position": "CB"}],
            players=[("o1", "QB"), ("d1", "CB")], teams=["KC", "LV"],
        )  # fmt: skip
        res, _ = D.derived_frames(fr, _no_snaps())
        assert res["TEAMMATE"].height == 0  # opposite sides: opponents, not teammates
        assert ends(res["OPPONENT"]) == {("o1", "d1")}


def _play(key="g1:1", ptype="pass", two=False, season=2024, off="KC", dfn="LV"):
    return {
        "play_key": key, "game_id": key.split(":")[0], "season": season, "offense": off,
        "defense": dfn, "play_type": ptype, "two_point": two, "complete": None, "yards": None,
        "epa": None, "touchdown": None, "fumble_lost": None,
    }  # fmt: skip


def _no_snaps() -> pl.DataFrame:
    return mini_snaps().head(0)


SKILL = ("WR", "TE", "RB", "FB", "HB")
DBS = ("DB", "CB", "S", "SS", "FS", "SAF")
DLS = ("DL", "DE", "DT", "NT")
LBS = ("LB", "OLB", "ILB", "MLB")
OLS = ("OL", "T", "G", "C", "OT", "OG")
POSITION_PAIRS = [
    *[(o, d, True) for o in SKILL for d in DBS],  # skill vs DB
    *[(o, d, False) for o in SKILL for d in (*DLS, *LBS)],  # skill vs DL / LB: no
    *[(o, d, True) for o in OLS for d in DLS],  # OL vs DL
    *[(o, d, False) for o in OLS for d in (*LBS, *DBS)],  # OL vs LB / DB: no
    *[("QB", d, True) for d in (*DLS, *LBS, *DBS)],  # the QB vs everyone on defense
    *[(o, d, False) for o in SKILL for d in ("K", "P", "LS")],  # nobody meets a specialist
    ("K", "CB", False), ("P", "DE", False), ("LS", "LB", False), ("OL", "K", False),
    ("QB", "K", False), ("QB", "P", False),
    (None, "CB", False), ("WR", None, False), ("QB", None, False), ("OL", None, False),
]  # fmt: skip


class TestOpponent:
    """Offense -> defense on the same scrimmage plays; only skill vs DB, OL vs DL, QB vs any
    defender."""

    def test_plays_per_matchup_worked_out_by_hand(self, derived):
        op = derived[0]["OPPONENT"]
        # scrimmage plays with ON_FIELD: g1:1,2,3,4,5,8 and g3:1 (g1:6 is a two-point try and g1:7
        # a punt: neither counts)
        want = {
            ("o1", "d1"): 6,  # QB vs CB: g1:1..5 (d1 isn't on g1:8) + g3:1
            ("o1", "d2"): 6,  # QB vs DE: g1:1..5 + g1:8
            ("o1", "d3"): 5,  # QB vs ILB: g1:1..5
            ("o2", "d1"): 5,  # WR vs CB: g1:1,2,4,5 + g3:1 (g1:6 is the two-point try)
            ("o3", "d1"): 5,  # RB vs CB: g1:1..5 (g1:7 is a punt)
            ("o4", "d2"): 5,  # T vs DE: g1:1..5
        }
        assert {(r["s"], r["e"]): r["plays"] for r in op.to_dicts()} == want

    def test_the_link_runs_offense_to_defense_whatever_the_ids(self, derived):
        op = derived[0]["OPPONENT"]
        assert all(s.startswith("o") and e.startswith("d") for s, e in ends(op))
        assert row(op, "o1", "d1")["seasons"] == [2024, 2025]

    def test_a_two_point_try_and_a_punt_never_count(self, derived):
        # o2 (WR) was on the field against d1 (CB) in the two-point try g1:6, o3 against d1 in the
        # punt g1:7: both would add one
        op = derived[0]["OPPONENT"]
        assert row(op, "o2", "d1")["plays"] == 5 and row(op, "o3", "d1")["plays"] == 5

    def test_the_matchups_that_arent_in_the_set_are_absent(self, derived):
        op = ends(derived[0]["OPPONENT"])
        assert ("o4", "d3") not in op  # OL vs LB
        assert ("o4", "d1") not in op  # OL vs DB
        assert ("o2", "d2") not in op and ("o2", "d3") not in op  # skill vs DL / LB
        assert ("o3", "d2") not in op and ("o3", "d3") not in op

    def test_the_qb_meets_every_kind_of_defender(self, derived):
        op = ends(derived[0]["OPPONENT"])
        assert {("o1", "d1"), ("o1", "d2"), ("o1", "d3")} <= op

    @pytest.mark.parametrize(("off_pos", "def_pos", "linked"), POSITION_PAIRS)
    def test_every_position_pair(self, off_pos, def_pos, linked):
        fr = make_frames(
            [_play("g1:1", "run")],
            [{"s": "a", "e": "g1:1", "side": "offense", "position": off_pos},
             {"s": "b", "e": "g1:1", "side": "defense", "position": def_pos}],
            players=[("a", "X"), ("b", "Y")], teams=["KC", "LV"],
        )  # fmt: skip
        res, _ = D.derived_frames(fr, _no_snaps())
        assert (res["OPPONENT"].height == 1) is linked, (off_pos, def_pos)
        if linked:
            r = res["OPPONENT"].to_dicts()[0]
            assert (r["s"], r["e"], r["plays"]) == ("a", "b", 1)

    @pytest.mark.parametrize("ptype", ["punt", "kickoff", "field_goal", "extra_point", "no_play",
                                        "qb_kneel", "qb_spike", None])  # fmt: skip
    def test_only_passes_and_runs_count(self, ptype):
        fr = make_frames(
            [_play("g1:1", ptype)],
            [{"s": "a", "e": "g1:1", "side": "offense", "position": "QB"},
             {"s": "b", "e": "g1:1", "side": "defense", "position": "CB"}],
            players=[("a", "X"), ("b", "Y")], teams=["KC", "LV"],
        )  # fmt: skip
        res, _ = D.derived_frames(fr, _no_snaps())
        assert res["OPPONENT"].height == 0
        assert res["TEAMMATE"].height == 0  # one player on each side: nobody on a side with him

    @pytest.mark.parametrize("two", [True, False, None])
    def test_a_two_point_try_is_not_a_scrimmage_play(self, two):
        fr = make_frames(
            [_play("g1:1", "pass", two=two)],
            [{"s": "a", "e": "g1:1", "side": "offense", "position": "QB"},
             {"s": "b", "e": "g1:1", "side": "defense", "position": "CB"}],
            players=[("a", "X"), ("b", "Y")], teams=["KC", "LV"],
        )  # fmt: skip
        res, _ = D.derived_frames(fr, _no_snaps())
        assert res["OPPONENT"].height == (1 if not two else 0)  # a null flag counts as no try

    def test_the_position_of_the_snap_decides_not_the_roster_one(self, derived):
        # o4 is a T on every snap; change his position to LB on the snaps: no more OL vs DL
        fr = mini_frames()
        of = fr.rels["ON_FIELD"].with_columns(
            pl.when(pl.col("s") == "o4").then(pl.lit("LB")).otherwise(pl.col("position"))
            .alias("position")
        )  # fmt: skip
        fr.rels["ON_FIELD"] = of
        res, _ = D.derived_frames(fr, mini_snaps())
        assert ("o4", "d2") not in ends(res["OPPONENT"])


class TestConnection:
    """QB -> receiver on one play: PASSED by one player and TARGETED at another."""

    def test_the_main_link_by_hand(self, derived):
        r = row(derived[0]["CONNECTION"], "o1", "o2")
        # targets: g1:1,2,4,5,8 and g3:1 = 6; catches (complete): g1:1,4,5 and g3:1 = 4
        assert r["targets"] == 6 and r["catches"] == 4
        # yards on completions only: 10 + 20 + 8 + 12 = 50 (g1:2's 7 yards are on an incompletion)
        assert r["yards"] == 50
        # touchdowns: g1:4 only (g1:5 is a touchdown with a lost fumble; g3:1 isn't one)
        assert r["touchdowns"] == 1
        # epa: 0.12345 - 0.4 + 2.0 - 1.0 + 0 (g1:8's null) + 0.5 = 1.22345, rounded to 3 places
        assert r["epa"] == 1.223
        assert r["seasons"] == [2024, 2025] and r["teams"] == ["DEN", "KC"]
        assert (r["first_season"], r["last_season"]) == (2024, 2025)

    def test_a_second_receiver(self, derived):
        r = row(derived[0]["CONNECTION"], "o1", "o3")
        assert (r["targets"], r["catches"], r["yards"], r["touchdowns"], r["epa"]) == (
            1, 1, 5, 0, 0.3)  # fmt: skip
        assert r["seasons"] == [2024] and r["teams"] == ["KC"]

    def test_a_passer_is_never_his_own_target(self, derived):
        assert ("o1", "o1") not in ends(derived[0]["CONNECTION"])  # g1:10

    def test_a_target_the_graph_doesnt_hold_is_dropped(self, derived):
        assert not any(e == "unk" for _s, e in ends(derived[0]["CONNECTION"]))

    def test_a_pass_without_a_target_makes_no_link(self):
        fr = make_frames(
            [_play("g1:1")], [], passed=[("a", "g1:1")], players=[("a", "QB")], teams=["KC", "LV"]
        )
        res, _ = D.derived_frames(fr, _no_snaps())
        assert res["CONNECTION"].height == 0

    def test_a_target_without_a_passer_makes_no_link(self):
        fr = make_frames(
            [_play("g1:1")], [], targeted=[("a", "g1:1")], players=[("a", "WR")], teams=["KC"]
        )
        res, _ = D.derived_frames(fr, _no_snaps())
        assert res["CONNECTION"].height == 0

    def test_the_touchdown_rule_and_null_flags(self):
        plays = [
            {**_play("g1:1"), "complete": True, "yards": 30, "touchdown": True,
             "fumble_lost": None, "epa": 1.0},
            {**_play("g1:2"), "complete": True, "yards": 40, "touchdown": True,
             "fumble_lost": True, "epa": 1.0},
            {**_play("g1:3"), "complete": None, "yards": 50, "touchdown": True,
             "fumble_lost": False, "epa": 1.0},
            {**_play("g1:4"), "complete": True, "yards": None, "touchdown": None,
             "fumble_lost": False, "epa": None},
        ]  # fmt: skip
        keys = [p["play_key"] for p in plays]
        fr = make_frames(
            plays, [], passed=[("q", k) for k in keys], targeted=[("r", k) for k in keys],
            players=[("q", "QB"), ("r", "WR")], teams=["KC", "LV"],
        )  # fmt: skip
        res, _ = D.derived_frames(fr, _no_snaps())
        r = res["CONNECTION"].to_dicts()[0]
        # touchdowns: g1:1 (a null fumble flag is not a lost fumble); not g1:2 (lost fumble), not
        # g1:3 (not complete), not g1:4 (no touchdown flag)
        assert r["touchdowns"] == 1
        assert r["targets"] == 4 and r["catches"] == 3 and r["yards"] == 70  # a null yard is 0
        assert r["epa"] == 3.0

    def test_epa_is_rounded_to_three_places(self):
        plays = [{**_play(f"g1:{i}"), "complete": True, "epa": e}
                 for i, e in enumerate((0.1, 0.2, 0.0004, 0.0004), start=1)]  # fmt: skip
        keys = [p["play_key"] for p in plays]
        fr = make_frames(
            plays, [], passed=[("q", k) for k in keys], targeted=[("r", k) for k in keys],
            players=[("q", "QB"), ("r", "WR")], teams=["KC"],
        )  # fmt: skip
        res, _ = D.derived_frames(fr, _no_snaps())
        assert res["CONNECTION"]["epa"].to_list() == [0.301]  # 0.3008 rounded, a float sum's noise


class TestCoached:
    """Coach -> player while the player took snaps in the coach's unit (curated snap counts):
    the head coach in his weeks, all units; OC offense; DC defense; ST special teams."""

    def test_the_links(self, derived):
        co = derived[0]["COACHED"]
        assert ends(co) == {
            ("hc", "q1"), ("hc", "q2"), ("hc", "q3"), ("hc", "q4"),
            ("oc", "q1"),
            ("dc", "q2"), ("dc", "q4"),
            ("stc", "q2"), ("stc", "q3"),
            ("both", "n1"), ("both", "n2"),
        }  # fmt: skip

    def test_the_head_coach_counts_only_his_weeks_and_every_unit(self, derived):
        co = derived[0]["COACHED"]
        # q1: weeks 1 and 2 (60 + 50) but not week 9 (after his last week, 8)
        assert row(co, "hc", "q1")["snaps"] == 110
        # q2: 80 defense + 10 special teams + 20 defense = 110, all three units
        assert row(co, "hc", "q2")["snaps"] == 110
        assert row(co, "hc", "q3")["snaps"] == 30 and row(co, "hc", "q4")["snaps"] == 5

    def test_the_head_coachs_weeks_bound_the_snaps_inclusively(self):
        def cut(first, last):
            fr = mini_frames()
            fr.rels["HEAD_COACH"] = fr.rels["HEAD_COACH"].with_columns(
                pl.when(pl.col("s") == "hc").then(pl.lit(first)).otherwise(pl.col("first_week"))
                .alias("first_week"),
                pl.when(pl.col("s") == "hc").then(pl.lit(last)).otherwise(pl.col("last_week"))
                .alias("last_week"),
            )  # fmt: skip
            res, _ = D.derived_frames(fr, mini_snaps())
            return {(r["s"], r["e"]): r["snaps"] for r in res["COACHED"].to_dicts()}

        assert cut(1, 1)[("hc", "q1")] == 60  # week 1 only
        assert cut(2, 2)[("hc", "q1")] == 50  # week 2 only
        assert cut(9, 9)[("hc", "q1")] == 40  # week 9 only
        assert ("hc", "q1") not in cut(3, 8)  # q1 has no snaps in weeks 3-8

    def test_a_coordinator_counts_every_week_but_only_his_unit(self, derived):
        co = derived[0]["COACHED"]
        assert row(co, "oc", "q1")["snaps"] == 180  # offense: 60 + 50 + 40 in 2024, 30 in 2025
        assert row(co, "dc", "q2")["snaps"] == 100  # defense only: 80 + 20 (not the 10 on teams)
        assert row(co, "stc", "q2")["snaps"] == 10  # special teams only
        assert row(co, "stc", "q3")["snaps"] == 30
        assert row(co, "dc", "q4")["snaps"] == 5

    def test_nobody_is_coached_by_a_unit_he_took_no_snaps_in(self, derived):
        co = ends(derived[0]["COACHED"])
        assert ("oc", "q2") not in co and ("oc", "q3") not in co and ("oc", "q4") not in co
        assert ("dc", "q1") not in co and ("dc", "q3") not in co
        assert ("stc", "q1") not in co and ("stc", "q4") not in co
        assert derived[0]["COACHED"].filter(pl.col("snaps") <= 0).height == 0

    def test_a_season_without_a_staff_row_has_no_link(self, derived):
        assert not any(e == "q5" for _s, e in ends(derived[0]["COACHED"]))  # q5: 2026 only

    def test_a_coach_with_two_roles_in_a_team_season_counts_once(self, derived):
        r = row(derived[0]["COACHED"], "both", "n1")
        # head coach: 100 + 40 + 10 = 150 snaps, offensive coordinator: 100. One stint per
        # role with its own snaps (a role asked for alone gets its own count, Sol's AE03
        # review), and the link's total counts each snap once: 150, not 250
        assert r["snaps"] == 150
        assert r["roles"] == ["HC", "OC"] and r["stint_roles"] == ["HC", "OC"]
        assert r["stint_snaps"] == [150, 100] and r["stint_seasons"] == [2024, 2024]
        assert r["stint_teams"] == ["DEN", "DEN"] and r["teams"] == ["DEN"]

    def test_a_role_with_no_snaps_is_left_out_of_the_roles(self, derived):
        r = row(derived[0]["COACHED"], "both", "n2")  # 7 special-teams snaps: the HC only
        assert r["roles"] == ["HC"] and r["stint_roles"] == ["HC"] and r["snaps"] == 7

    def test_stints_follow_the_seasons(self, derived):
        r = row(derived[0]["COACHED"], "oc", "q1")
        assert r["seasons"] == [2024, 2025] and r["teams"] == ["KC"]
        assert r["stint_seasons"] == [2024, 2025] and r["stint_teams"] == ["KC", "KC"]
        assert r["stint_roles"] == ["OC", "OC"] and r["stint_snaps"] == [150, 30]
        assert sum(r["stint_snaps"]) == r["snaps"]

    def test_the_players_team_must_be_the_coachs_team(self, derived):
        # q1 also had 5 snaps "for XXX" in 2024 (an unknown team): dropped, never coached by KC's
        # staff for them (hc -> q1 stays 110, not 115)
        assert row(derived[0]["COACHED"], "hc", "q1")["snaps"] == 110

    def test_a_coach_the_graph_doesnt_hold_is_dropped(self, derived):
        assert not any(s == "ghost" for s, _e in ends(derived[0]["COACHED"]))


class TestRolesAndTenure:
    """Sol's AE03 review: COACHED keeps each role's own snaps and counts a snap once in the
    total; WORKED_WITH counts only the weeks both coaches were there."""

    def snaps(self, *rows):
        return pl.DataFrame(list(rows), schema=SNAP_SCHEMA, orient="row")

    def test_two_coordinator_roles_add_up_their_units_once(self):
        # one coach is both the OC and the ST coordinator: offense 60 + special teams 9
        fr = make_frames([], [], players=[("p", "WR")], coaches=["x"], teams=["KC"],
                         off=[("x", "KC", 2024)], st=[("x", "KC", 2024)])  # fmt: skip
        res, _ = D.derived_frames(fr, self.snaps(("p", "KC", 2024, 1, 60, 0, 9)))
        r = res["COACHED"].to_dicts()[0]
        assert r["stint_roles"] == ["OC", "ST"] and r["stint_snaps"] == [60, 9]
        assert r["snaps"] == 69  # the max of the two (60) undercounted him

    def test_a_head_coach_who_left_mid_season_counts_only_his_weeks(self):
        # HC weeks 1-2, then an interim (weeks 3-4); the OC stays all season
        fr = make_frames([], [], players=[("p", "WR")], coaches=["hc", "int", "oc"],
                         teams=["KC"],
                         head=[("hc", "KC", 2024, 2, 1, 2), ("int", "KC", 2024, 2, 3, 4)],
                         off=[("oc", "KC", 2024)])  # fmt: skip
        res, _ = D.derived_frames(fr, self.snaps(*[("p", "KC", 2024, w, 10, 0, 0)
                                                    for w in (1, 2, 3, 4)]))  # fmt: skip
        co = {(x["s"], x["e"]): x for x in res["COACHED"].to_dicts()}
        assert co[("hc", "p")]["snaps"] == 20 and co[("int", "p")]["snaps"] == 20
        assert co[("oc", "p")]["snaps"] == 40

    def test_staff_links_count_only_the_weeks_both_were_there(self):
        plays_rows = [
            {"play_key": f"w{w}:{i}", "game_id": f"w{w}", "season": 2024,
             "offense": "KC", "defense": "LV", "play_type": "run", "two_point": False,
             "complete": None, "yards": 1, "epa": 0.0, "touchdown": False,
             "fumble_lost": False}
            for w in (1, 2, 3, 4) for i in range(w)  # week w: w plays
        ]  # fmt: skip
        fr = make_frames(
            plays_rows, [], players=[], coaches=["hc", "int", "oc"], teams=["KC", "LV"],
            head=[("hc", "KC", 2024, 2, 1, 2), ("int", "KC", 2024, 2, 3, 4)],
            off=[("oc", "KC", 2024)],
            games=[(f"w{w}", 2024, "KC", "LV") for w in (1, 2, 3, 4)],
        )  # fmt: skip
        fr.nodes["Play"] = fr.nodes["Play"].with_columns(
            pl.col("play_key").str.slice(1, 1).cast(pl.Int64).alias("week")
        )
        fr.nodes["Game"] = fr.nodes["Game"].with_columns(
            pl.col("game_id").str.slice(1, 1).cast(pl.Int64).alias("week")
        )
        res, _ = D.derived_frames(fr, _no_snaps())
        ww = {(x["s"], x["e"]): x for x in res["WORKED_WITH"].to_dicts()}
        # the fired head coach and the interim never overlapped: no link
        assert ("hc", "int") not in ww
        # the OC with each: only that head coach's weeks (1 + 2 plays; 3 + 4 plays)
        assert (ww[("hc", "oc")]["snaps"], ww[("hc", "oc")]["games"]) == (3, 2)
        assert (ww[("int", "oc")]["snaps"], ww[("int", "oc")]["games"]) == (7, 2)


class TestWorkedWith:
    def test_pairs_by_hand(self, derived):
        ww = derived[0]["WORKED_WITH"]
        # KC 2024 staff: dc, hc, oc, stc (and the ghost, dropped): 6 pairs among the four; KC
        # 2025 staff: dc, oc. DEN's coach `both` is alone on his staff: no pair
        assert ends(ww) == {("dc", "hc"), ("dc", "oc"), ("dc", "stc"), ("hc", "oc"),
                            ("hc", "stc"), ("oc", "stc")}  # fmt: skip
        assert ww.filter(pl.col("s") >= pl.col("e")).height == 0  # the smaller id first

    def test_snaps_are_the_teams_plays_and_games_its_games(self, derived):
        ww = derived[0]["WORKED_WITH"]
        # KC's 2024 plays: offense g1:1..g1:12 (12) + g2:2 (1), defense g2:1 (1) = 14, in 2 games
        r = row(ww, "hc", "oc")
        assert (r["snaps"], r["games"]) == (14, 2)
        assert r["seasons"] == [2024] and r["teams"] == ["KC"]
        assert (r["first_season"], r["last_season"]) == (2024, 2024)

    def test_roles_are_start_then_end_in_stint_order(self, derived):
        ww = derived[0]["WORKED_WITH"]
        assert row(ww, "hc", "oc")["stint_roles"] == ["HC|OC"]  # hc is the start
        assert row(ww, "dc", "hc")["stint_roles"] == ["DC|HC"]  # dc is the start (smaller id)
        assert row(ww, "oc", "stc")["stint_roles"] == ["OC|ST"]
        assert row(ww, "dc", "stc")["stint_roles"] == ["DC|ST"]

    def test_a_pair_on_two_staffs_has_two_stints(self, derived):
        r = row(derived[0]["WORKED_WITH"], "dc", "oc")
        # 2024: 14 plays, 2 games; 2025: KC's one play (as the defense of g3:1), 1 game
        assert r["snaps"] == 15 and r["games"] == 3
        assert r["seasons"] == [2024, 2025] and r["stint_seasons"] == [2024, 2025]
        assert r["stint_teams"] == ["KC", "KC"] and r["stint_roles"] == ["DC|OC", "DC|OC"]

    def test_a_coach_alone_on_a_staff_has_no_pair(self, derived):
        assert not any("both" in (s, e) for s, e in ends(derived[0]["WORKED_WITH"]))

    def test_a_team_without_plays_or_games_has_zero_snaps_and_games(self):
        fr = make_frames(
            [], [], coaches=["a", "b"], teams=["KC"], players=[],
            off=[("a", "KC", 2024)], dfn=[("b", "KC", 2024)],
        )  # fmt: skip
        res, _ = D.derived_frames(fr, _no_snaps())
        r = res["WORKED_WITH"].to_dicts()[0]
        assert (r["s"], r["e"], r["snaps"], r["games"]) == ("a", "b", 0, 0)
        assert r["stint_roles"] == ["OC|DC"]

    def test_a_coach_the_graph_doesnt_hold_is_dropped(self, derived):
        assert not any("ghost" in (s, e) for s, e in ends(derived[0]["WORKED_WITH"]))


class TestEmptyAndMissing:
    def test_no_participation_gives_empty_frames_with_the_right_columns(self):
        fr = mini_frames()
        fr.rels["ON_FIELD"] = fr.rels["ON_FIELD"].head(0)
        res, dropped = D.derived_frames(fr, mini_snaps())
        assert res["TEAMMATE"].height == 0 and res["OPPONENT"].height == 0
        for rtype in ("TEAMMATE", "OPPONENT"):
            assert res[rtype].columns == S.DERIVED[rtype].columns()
            for prop, t in S.DERIVED[rtype].props.items():
                assert res[rtype].schema[prop] == _POLARS[t]
        # the links that don't come from participation are all still there
        assert res["CONNECTION"].height == 2 and res["COACHED"].height == 11
        assert "TEAMMATE" not in dropped

    def test_no_snap_counts_means_no_coaching_links(self):
        res, dropped = D.derived_frames(mini_frames(), mini_snaps().head(0))
        assert res["COACHED"].height == 0 and dropped["snaps_rows_unknown"] == 0
        assert res["WORKED_WITH"].height == 6  # a staff link needs no snaps


class TestReadSnaps:
    """`derive.read_snaps`: curated snap counts per (player, team, season, week)."""

    def write(self, paths, rows):
        schema = {"gsis_id": pl.String, "game_id": pl.String, "team": pl.String,
                  "season": pl.Int32, "week": pl.Int32, "offense_snaps": pl.Int32,
                  "defense_snaps": pl.Int32, "st_snaps": pl.Int32, "other": pl.String}  # fmt: skip
        paths.curated.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows, schema=schema, orient="row").write_parquet(
            paths.curated / "snaps.parquet"
        )

    @pytest.fixture
    def paths(self, tmp_path):
        from nflengine.paths import DataPaths

        return DataPaths(tmp_path)

    def test_the_columns_and_the_sums(self, paths):
        self.write(paths, [
            ("p1", "g1", "KC", 2024, 1, 60, 0, 5, "x"),
            ("p1", "g2", "KC", 2024, 1, 10, 0, 2, "x"),  # a second game in the same week: summed
            ("p1", "g3", "KC", 2024, 2, 50, 0, 0, "x"),
        ])  # fmt: skip
        out = D.read_snaps(paths, [2024], None, None).sort("week")
        assert out.columns == ["p", "team", "season", "week", "off", "def", "st"]
        assert out.schema == {"p": pl.String, "team": pl.String, "season": pl.Int64,
                              "week": pl.Int64, "off": pl.Int64, "def": pl.Int64,
                              "st": pl.Int64}  # fmt: skip
        assert out.rows() == [("p1", "KC", 2024, 1, 70, 0, 7), ("p1", "KC", 2024, 2, 50, 0, 0)]

    def test_a_duplicated_player_game_row_counts_once(self, paths):
        self.write(paths, [
            ("p1", "g1", "KC", 2024, 1, 60, 0, 5, "x"),
            ("p1", "g1", "KC", 2024, 1, 60, 0, 5, "y"),  # the curated-data quirk: same row twice
        ])  # fmt: skip
        out = D.read_snaps(paths, [2024], None, None)
        assert out.rows() == [("p1", "KC", 2024, 1, 60, 0, 5)]

    def test_rows_without_a_gsis_id_are_dropped(self, paths):
        self.write(paths, [
            (None, "g1", "KC", 2024, 1, 60, 0, 5, "x"),
            ("p1", "g1", "KC", 2024, 1, 10, 0, 0, "x"),
        ])  # fmt: skip
        assert D.read_snaps(paths, [2024], None, None)["p"].to_list() == ["p1"]

    def test_null_snap_counts_are_zero(self, paths):
        self.write(paths, [("p1", "g1", "KC", 2024, 1, None, 7, None, "x")])
        assert D.read_snaps(paths, [2024], None, None).rows() == [("p1", "KC", 2024, 1, 0, 7, 0)]

    def test_only_the_asked_seasons(self, paths):
        self.write(paths, [
            ("p1", "g1", "KC", 2017, 1, 1, 0, 0, "x"),
            ("p1", "g2", "KC", 2024, 1, 2, 0, 0, "x"),
            ("p1", "g3", "KC", 2026, 1, 4, 0, 0, "x"),
        ])  # fmt: skip
        assert D.read_snaps(paths, [2024], None, None)["season"].to_list() == [2024]

    def test_the_appended_season_is_cut_at_its_week(self, paths):
        self.write(paths, [
            ("p1", "g1", "KC", 2025, 18, 1, 0, 0, "x"),  # a finished season: every week
            ("p1", "g2", "KC", 2026, 1, 2, 0, 0, "x"),
            ("p1", "g3", "KC", 2026, 5, 4, 0, 0, "x"),
            ("p1", "g4", "KC", 2026, 6, 8, 0, 0, "x"),  # after the appended week
        ])  # fmt: skip
        out = D.read_snaps(paths, [2025, 2026], 2026, 5).sort("season", "week")
        assert out.select("season", "week").rows() == [(2025, 18), (2026, 1), (2026, 5)]
        # without a cut every week of the season is there
        assert D.read_snaps(paths, [2025, 2026], None, None).height == 4

    def test_a_player_on_two_teams_in_a_season_has_two_rows(self, paths):
        self.write(paths, [
            ("p1", "g1", "KC", 2024, 1, 10, 0, 0, "x"),
            ("p1", "g2", "DEN", 2024, 2, 20, 0, 0, "x"),
        ])  # fmt: skip
        assert D.read_snaps(paths, [2024], None, None).sort("week")["team"].to_list() == [
            "KC", "DEN"]  # fmt: skip


class TestCoverage:
    BUILD = {"first_season": 2018, "through_season": 2025}

    def test_a_build_alone(self):
        assert D.coverage(self.BUILD, None) == {
            "seasons": list(range(2018, 2026)), "season": None, "through_week": None}  # fmt: skip

    def test_an_appended_season_adds_itself_with_its_week(self):
        app = {"season": 2026, "through_week": 5}
        cov = D.coverage(self.BUILD, app)
        assert cov["seasons"] == [*range(2018, 2026), 2026]
        assert (cov["season"], cov["through_week"]) == (2026, 5)

    def test_an_append_with_no_week_is_not_an_append(self):
        assert D.coverage(self.BUILD, {"season": 2026, "through_week": 0})["season"] is None
        assert D.coverage(self.BUILD, {"season": 2026})["seasons"] == list(range(2018, 2026))

    def test_an_append_of_a_built_season_does_not_repeat_it(self):
        cov = D.coverage(self.BUILD, {"season": 2025, "through_week": 3})
        assert cov["seasons"] == list(range(2018, 2026)) and cov["through_week"] == 3
