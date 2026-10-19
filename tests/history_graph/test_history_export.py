"""The history graph's export (AE02): curated + research files -> one frame per node label and
relationship type. A hand-built league (`history_world.py`) with three seasons: 2019 (NGS era),
2024 (FTN era: position lists, FTN charting) and 2026 (the current season: no participation).
Every expected number is worked out from the fixture rows, not from the code under test."""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest
from history_world import (
    DB_LV,
    DE_KC,
    DT_LV,
    G19,
    GA,
    GB,
    GC,
    GD,
    K_KC,
    LB_LV,
    OLB_KC,
    QB_KC,
    QB_LV,
    RB_KC,
    RB_LV,
    ROOKIE,
    TE_KC,
    UNK_FIELD,
    UNK_RECV,
    UNK_RUSH,
    WR_KC,
    WR_LV,
    install_world,
    load,
    part_row,
)

from nflengine.history_graph import export as X
from nflengine.history_graph import schema as S


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    w = install_world(tmp_path_factory.mktemp("hg_export"), mp)
    yield w
    mp.undo()


@pytest.fixture(scope="module")
def fr(world):
    return load(world)


def one(df: pl.DataFrame, **where: Any) -> dict[str, Any]:
    d = df
    for k, v in where.items():
        d = d.filter(pl.col(k) == v)
    assert d.height == 1, f"{where} matched {d.height} rows"
    return d.row(0, named=True)


def play(fr, game: str, pid: int) -> dict[str, Any]:
    return one(fr.nodes["Play"], play_key=f"{game}:{pid}")


def pairs(fr, rtype: str, game: str | None = None) -> set[tuple]:
    df = fr.rels[rtype]
    if game:
        df = df.filter(pl.col("e").str.starts_with(game + ":"))
    return {(s, e) for s, e in df.select("s", "e").iter_rows()}


# ---- shape -----------------------------------------------------------------------------------


class TestShape:
    def test_every_frame_has_the_specs_columns_in_order(self, fr):
        assert set(fr.nodes) == set(S.NODES)
        assert set(fr.rels) == set(S.RELATIONSHIPS)
        for label, df in fr.nodes.items():
            assert df.columns == S.NODES[label].columns(), label
        for rtype, df in fr.rels.items():
            assert df.columns == S.RELATIONSHIPS[rtype].columns(), rtype

    def test_every_frame_has_the_specs_dtypes(self, fr):
        want = {
            S.LONG: pl.Int64,
            S.DOUBLE: pl.Float64,
            S.BOOL: pl.Boolean,
            S.STRING: pl.String,
            S.LONGS: pl.List(pl.Int64),
        }
        for label, df in fr.nodes.items():
            spec = S.NODES[label]
            types = {spec.key: S.key_type(label), **spec.props}
            for c, t in types.items():
                assert df.schema[c] == want[t], f"{label}.{c}: {df.schema[c]} vs {t}"
        for rtype, df in fr.rels.items():
            spec = S.RELATIONSHIPS[rtype]
            for c, t in spec.props.items():
                assert df.schema[c] == want[t], f"{rtype}.{c}: {df.schema[c]} vs {t}"
            assert df.schema["s"] == pl.String, rtype
            assert df.schema["e"] == (pl.Int64 if rtype == "IN_SEASON" else pl.String), rtype

    def test_counts_worked_out_by_hand(self, fr):
        assert fr.expected_counts() == {
            # nodes
            "Season": 3,
            "Team": 3,
            "Coach": 11,
            "Game": 5,
            "Drive": 13,
            "Play": 34,
            "Player": 17,
            # structure: 34 plays; one has a team that isn't a team (OFFENSE dropped)
            "IN_SEASON": 5,
            "HOME_TEAM": 5,
            "AWAY_TEAM": 5,
            "IN_GAME": 13,
            "IN_DRIVE": 34,
            "OFFENSE": 33,
            "DEFENSE": 34,
            # participation: 2024 has 18 links (a repeated row and a play pbp lacks
            # aside), 2019 has 10
            "ON_FIELD": 28,
            # roles: 2019 1+1 / 2024 5+3+... see the TestRoles comments
            "PASSED": 12,
            "RUSHED": 10,
            "TARGETED": 9,
            "CAUGHT": 5,
            "INTERCEPTED": 1,
            "SACKED": 3,
            "TACKLED": 7,
            "PLAYED_FOR": 25,
            "HEAD_COACH": 8,
            "OFF_COORD": 2,
            "DEF_COORD": 2,
            "ST_COORD": 1,
        }

    def test_dangling_relationships_are_dropped_and_counted(self, fr):
        assert fr.dropped == {
            "OFFENSE": 1,  # the punt by team XXX
            "OFF_COORD": 1,  # the seed's coordinator for team XYZ
            "participation_plays_not_in_pbp": 1,  # participation play 999
        }

    def test_no_relationship_points_at_a_missing_node(self, fr):
        keys = {label: set(df[S.key_of(label)].to_list()) for label, df in fr.nodes.items()}
        for rtype, df in fr.rels.items():
            spec = S.RELATIONSHIPS[rtype]
            assert set(df["s"].to_list()) <= keys[spec.start], rtype
            assert set(df["e"].to_list()) <= keys[spec.end], rtype

    def test_node_keys_are_unique_and_not_blank(self, fr):
        for label, df in fr.nodes.items():
            k = df[S.key_of(label)]
            assert k.null_count() == 0 and k.n_unique() == k.len(), label
            if label != "Season":
                assert (k.str.strip_chars() == "").sum() == 0, label

    def test_no_duplicate_relationships(self, fr):
        for rtype, df in fr.rels.items():
            keys = ["s", "e", "season"] if rtype in S.SEASON_TYPES else ["s", "e"]
            assert df.unique(keys).height == df.height, rtype

    def test_the_only_note_is_the_current_seasons_missing_participation(self, fr, world):
        # 2026's participation isn't published yet: a season loads it when its file exists
        # and says so when it doesn't
        assert fr.notes == ["participation missing for [2026]"]
        assert load(world, seasons=(2019, 2024)).notes == []


# ---- plays -----------------------------------------------------------------------------------


class TestPlays:
    def test_play_keys_are_game_id_colon_integer_play_id(self, fr):
        keys = set(fr.nodes["Play"]["play_key"])
        assert f"{GA}:3" in keys and f"{GA}:16" in keys
        assert not any(k.endswith((".0", ".5")) for k in keys)
        assert (
            fr.nodes["Play"]
            .filter(
                pl.col("play_key") != pl.col("game_id") + ":" + pl.col("play_id").cast(pl.String)
            )
            .is_empty()
        )

    def test_timeouts_and_rows_without_a_type_are_not_plays(self, fr):
        keys = set(fr.nodes["Play"]["play_key"])
        for gone in (f"{GA}:1", f"{GA}:14", f"{GA}:17", f"{GB}:1", f"{G19}:1", f"{GC}:1"):
            assert gone not in keys
        # game A: plays 2-13, 15 and 16 (14 plays); the no_play penalty (12) stays
        assert {k for k in keys if k.startswith(GA + ":")} == {
            f"{GA}:{i}" for i in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16)
        }

    def test_plays_per_season(self, fr):
        by = fr.nodes["Play"].group_by("season").len().sort("season")
        assert by.rows() == [(2019, 4), (2024, 22), (2026, 8)]

    def test_a_complete_pass(self, fr):
        p = play(fr, GA, 3)
        assert p["game_id"] == GA and p["play_id"] == 3 and p["season"] == 2024 and p["week"] == 1
        assert (p["quarter"], p["clock"], p["down"], p["distance"]) == (1, "15:00", 1, 10)
        assert (p["yards_to_goal"], p["goal_to_go"], p["score_diff"], p["wp"]) == (
            75,
            False,
            0,
            0.55,
        )
        assert (p["offense"], p["defense"], p["play_type"]) == ("KC", "LV", "pass")
        assert (p["yards"], p["epa"], p["success"], p["first_down"]) == (12, 0.5, True, True)
        assert p["dropback"] and p["pass_attempt"] and p["complete"] and not p["sack"]
        assert not p["interception"] and not p["scramble"] and not p["touchdown"]
        assert (p["air_yards"], p["yards_after_catch"]) == (8.0, 4.0)
        assert (p["pass_length"], p["pass_location"]) == ("short", "left")
        assert p["shotgun"] is True and p["no_huddle"] is False
        assert p["order"] == 30.0 and p["season_type"] == "REG" and p["seconds_left"] is None

    def test_pass_and_run_fields_stay_on_their_own_play_type(self, fr):
        # play 3 is a pass whose row also had a run location / gap; play 6 a run with a pass length
        p3, p6 = play(fr, GA, 3), play(fr, GA, 6)
        assert p3["run_location"] is None and p3["run_gap"] is None
        assert p6["pass_length"] is None and p6["pass_location"] is None
        assert (p6["run_location"], p6["run_gap"]) == ("left", "end")
        assert p6["air_yards"] is None and p6["complete"] is None

    def test_a_sack_is_a_dropback_but_not_a_pass_attempt(self, fr):
        p = play(fr, GA, 8)
        assert p["sack"] and p["dropback"] and not p["pass_attempt"]
        assert p["complete"] is None and p["air_yards"] is None and p["yards"] == -7

    def test_a_scramble_is_a_run_play_with_the_dropback_flag(self, fr):
        p = play(fr, GA, 7)
        assert p["play_type"] == "run" and p["scramble"] and p["dropback"]
        assert not p["pass_attempt"] and p["run_location"] == "middle"

    def test_a_spike_is_an_incomplete_pass_attempt(self, fr):
        p = play(fr, GB, 9)
        assert p["play_type"] == "qb_spike"
        assert p["pass_attempt"] is True and p["complete"] is False
        assert p["dropback"] is True and p["sack"] is False
        assert p["air_yards"] == 0.0 and p["pass_location"] == "middle"

    def test_pass_attempt_is_the_box_scores_pass_type_or_spike_and_not_a_sack(self, fr):
        att = fr.nodes["Play"].filter(pl.col("pass_attempt"))
        assert set(att["play_type"].unique()) == {"pass", "qb_spike"}
        assert fr.nodes["Play"].filter(pl.col("pass_attempt") & pl.col("sack")).is_empty()
        # not a run, a kneel, a punt or a no_play, even with pass-like flags set
        for k in (6, 7, 10, 11, 12):
            assert play(fr, GA, k)["pass_attempt"] is False, k
        # attempts in the league: game A 3, 4, 5, 13; G19 2; B 5, 6, 9; C 2, 4; D 2
        assert att.height == 4 + 1 + 3 + 2 + 1

    def test_the_other_play_types(self, fr):
        types = {k: play(fr, GA, k)["play_type"] for k in (2, 10, 11, 12, 15, 16)}
        assert types == {
            2: "kickoff",
            10: "qb_kneel",
            11: "punt",
            12: "no_play",
            15: "extra_point",
            16: "field_goal",
        }

    def test_flags_keep_null_where_the_source_was_null(self, fr):
        p4 = play(fr, GA, 4)  # shotgun 0, no_huddle missing, success 0
        assert p4["shotgun"] is False and p4["no_huddle"] is None
        assert p4["success"] is False
        p2 = play(fr, GA, 2)  # a kickoff: no EPA success at all
        assert p2["success"] is None and p2["epa"] is None
        assert p2["first_down"] is False  # a missing 0/1 flag counts as false

    def test_the_two_point_flag_and_the_penalty_flag(self, fr):
        assert play(fr, GA, 13)["two_point"] is True
        assert play(fr, GA, 3)["two_point"] is False
        assert play(fr, GA, 12)["penalty"] is True

    def test_descriptions_lose_line_breaks_and_tabs_but_keep_commas_and_quotes(self, fr):
        d = play(fr, GA, 3)["description"]
        assert "\n" not in d and "\t" not in d and "\r" not in d
        assert d == (
            'P.Quarterback pass short left to W.Receiver for 12 yards, "first down"; tackled '
            "by L.Linebacker and D.Tackle."
        )

    def test_offense_and_defense_are_the_team_codes(self, fr):
        p = play(fr, GA, 2)  # a kickoff: the offense is the receiving team (nflverse)
        assert (p["offense"], p["defense"]) == ("LV", "KC")

    def test_the_play_frame_is_one_row_per_key(self, fr):
        assert fr.nodes["Play"]["play_key"].n_unique() == 34


# ---- roles -----------------------------------------------------------------------------------


class TestRoles:
    """Game A (2024 week 1): plays 3-5 KC pass (3 complete, 4 incomplete, 5 intercepted), 6 an LV
    run, 7 an LV scramble, 8 a sack, 9 a half sack, 10 a kneel, 11 a punt, 12 a no_play, 13 a
    two-point try. The roles must equal the box score: none on 12 and 13."""

    def test_passed_is_the_passer_of_an_attempt_or_a_sack_never_a_scramble(self, fr):
        assert pairs(fr, "PASSED", GA) == {
            (QB_KC, f"{GA}:3"),
            (QB_KC, f"{GA}:4"),
            (QB_KC, f"{GA}:5"),
            (QB_LV, f"{GA}:8"),
            (QB_LV, f"{GA}:9"),
        }

    def test_rushed_is_the_ball_carrier_scrambles_and_kneels_included(self, fr):
        assert pairs(fr, "RUSHED", GA) == {
            (RB_LV, f"{GA}:6"),
            (QB_LV, f"{GA}:7"),
            (QB_LV, f"{GA}:10"),
        }

    def test_targeted_only_on_pass_attempts_not_sacks(self, fr):
        assert pairs(fr, "TARGETED", GA) == {
            (WR_KC, f"{GA}:3"),
            (WR_KC, f"{GA}:4"),
            (TE_KC, f"{GA}:5"),
        }

    def test_a_spike_has_a_passer_but_no_target_and_no_catch(self, fr):
        assert (QB_KC, f"{GB}:9") in pairs(fr, "PASSED")
        assert f"{GB}:9" not in {e for _, e in pairs(fr, "TARGETED")}
        assert f"{GB}:9" not in {e for _, e in pairs(fr, "CAUGHT")}
        assert f"{GB}:9" not in {e for _, e in pairs(fr, "RUSHED")}

    def test_caught_only_on_completions(self, fr):
        assert pairs(fr, "CAUGHT", GA) == {(WR_KC, f"{GA}:3")}

    def test_intercepted_is_the_defender(self, fr):
        assert pairs(fr, "INTERCEPTED") == {(DB_LV, f"{GA}:5")}

    def test_a_sack_is_passed_and_sacked_and_a_half_sack_says_so(self, fr):
        sacked = {(r["s"], r["e"]): r["half"] for r in fr.rels["SACKED"].iter_rows(named=True)}
        assert sacked == {
            (DE_KC, f"{GA}:8"): False,
            (DE_KC, f"{GA}:9"): True,
            (OLB_KC, f"{GA}:9"): True,
        }
        assert (QB_LV, f"{GA}:8") in pairs(fr, "PASSED")

    def test_tackle_kinds_and_one_relationship_per_player_play_type(self, fr):
        t = {(r["s"], r["e"]): r["kind"] for r in fr.rels["TACKLED"].iter_rows(named=True)}
        on3 = {k: v for k, v in t.items() if k[1] == f"{GA}:3"}
        # LB_LV is in solo_tackle_1 and assist_tackle_2: one link, the first-listed kind (solo);
        # the blank id in solo_tackle_2 is no tackler
        assert on3 == {
            (LB_LV, f"{GA}:3"): "solo",
            (DT_LV, f"{GA}:3"): "with_assist",
            (DB_LV, f"{GA}:3"): "assist",
        }
        assert t[(OLB_KC, f"{GA}:2")] == "solo"  # tackles on kickoffs and punts count too
        assert t[(DE_KC, f"{GA}:6")] == "solo"
        assert t[(OLB_KC, f"{GA}:7")] == "solo"
        assert t[(OLB_KC, f"{GA}:11")] == "solo"
        assert len(t) == 7

    def test_no_role_on_a_no_play_or_a_two_point_try(self, fr):
        for rtype in S.ROLE_TYPES:
            ends = set(fr.rels[rtype]["e"])
            assert f"{GA}:12" not in ends, rtype  # the penalty no_play
            assert f"{GA}:13" not in ends, rtype  # the two-point try

    def test_role_counts_equal_the_hand_count_over_all_three_seasons(self, fr):
        by_season = {
            r: fr.rels[r]
            .with_columns(pl.col("e").str.slice(0, 4).alias("season"))
            .group_by("season")
            .len()
            .sort("season")
            .rows()
            for r in ("PASSED", "RUSHED", "TARGETED", "CAUGHT")
        }
        assert by_season == {
            "PASSED": [("2019", 1), ("2024", 8), ("2026", 3)],
            "RUSHED": [("2019", 2), ("2024", 5), ("2026", 3)],
            "TARGETED": [("2019", 1), ("2024", 5), ("2026", 3)],
            "CAUGHT": [("2019", 1), ("2024", 2), ("2026", 2)],
        }

    def test_players_the_tables_do_not_know_still_get_their_role(self, fr):
        assert (UNK_RECV, f"{GB}:5") in pairs(fr, "TARGETED")
        assert (UNK_RECV, f"{GB}:5") in pairs(fr, "CAUGHT")
        assert (UNK_RUSH, f"{GB}:7") in pairs(fr, "RUSHED")
        p = one(fr.nodes["Player"], player_id=UNK_RECV)
        assert p["name"] is None and p["position"] is None

    def test_every_role_points_at_a_real_player_and_play(self, fr):
        players = set(fr.nodes["Player"]["player_id"])
        plays = set(fr.nodes["Play"]["play_key"])
        for rtype in S.ROLE_TYPES:
            assert set(fr.rels[rtype]["s"]) <= players
            assert set(fr.rels[rtype]["e"]) <= plays


# ---- participation ---------------------------------------------------------------------------


class TestOnField:
    def of(self, fr, game, pid):
        df = fr.rels["ON_FIELD"].filter(pl.col("e") == f"{game}:{pid}")
        return {r["s"]: (r["side"], r["position"]) for r in df.iter_rows(named=True)}

    def test_positions_come_from_the_lists_in_the_ftn_era(self, fr):
        assert self.of(fr, GA, 3) == {
            QB_KC: ("offense", "QB"),
            RB_KC: ("offense", "RB"),
            WR_KC: ("offense", "WR"),
            DT_LV: ("defense", "DT"),
            LB_LV: ("defense", "ILB"),
            UNK_FIELD: ("defense", "S"),
        }

    def test_without_a_list_the_roster_week_then_the_player_table_stand_in(self, fr):
        # play 4 has no position lists: QB_KC / WR_KC from the week-1 roster, DB_LV from the
        # LV roster (CB), and the id nobody knows has no position
        assert self.of(fr, GA, 4) == {
            QB_KC: ("offense", "QB"),
            WR_KC: ("offense", "WR"),
            DB_LV: ("defense", "CB"),
            UNK_FIELD: ("defense", None),
        }

    def test_2019_has_no_lists_roster_position_beats_the_player_table(self, fr):
        on = self.of(fr, G19, 2)
        assert on[WR_KC] == ("offense", "TE")  # the week-1 roster says TE, `players` says WR
        assert on[QB_KC] == ("offense", "QB")  # no 2019 week-1 roster row: `players`
        assert on[RB_KC] == ("offense", "RB")
        assert on[WR_LV] == ("defense", "WR")
        assert on[DT_LV] == ("defense", "DT")  # only in `players`
        assert self.of(fr, G19, 5) == {
            RB_LV: ("offense", "RB"),
            QB_LV: ("offense", "QB"),
            WR_KC: ("defense", "TE"),
        }

    def test_a_no_play_snap_has_its_players_on_the_field(self, fr):
        assert self.of(fr, GA, 12) == {QB_KC: ("offense", "QB"), LB_LV: ("defense", "ILB")}

    def test_a_repeated_participation_row_makes_one_set_of_links(self, fr):
        df = fr.rels["ON_FIELD"]
        assert df.unique(["s", "e"]).height == df.height
        assert df.filter(pl.col("e") == f"{GA}:12").height == 2

    def test_a_participation_play_pbp_does_not_have_is_dropped(self, fr):
        assert f"{GA}:999" not in set(fr.rels["ON_FIELD"]["e"])

    def test_counts_per_season(self, fr):
        by = (
            fr.rels["ON_FIELD"]
            .with_columns(pl.col("e").str.slice(0, 4).alias("season"))
            .group_by("season")
            .len()
            .sort("season")
        )
        assert by.rows() == [("2019", 10), ("2024", 18)]  # 3+... see the fixture comments

    def test_the_player_on_the_field_only_is_a_player_node(self, fr):
        p = one(fr.nodes["Player"], player_id=UNK_FIELD)
        assert p["name"] is None

    def test_sides_are_offense_or_defense(self, fr):
        assert set(fr.rels["ON_FIELD"]["side"]) == {"offense", "defense"}

    def test_participation_labels_are_on_participation_plays_only(self, fr):
        plays = fr.nodes["Play"]
        have = set(plays.filter(pl.col("part"))["play_key"])
        assert have == {f"{GA}:{i}" for i in (3, 4, 5, 6, 12)} | {f"{G19}:{i}" for i in (2, 3, 5)}
        # everything else: the flag and every label are null (never False / an empty string)
        rest = plays.filter(~pl.col("play_key").is_in(list(have)))
        labels = (
            "part",
            "formation",
            "personnel",
            "extra_ol",
            "def_package",
            "coverage",
            "man_zone",
            "target_route",
            "pressure",
            "time_to_throw",
            "part_box",
            "part_rushers",
        )
        for c in labels:
            assert rest[c].null_count() == rest.height, c

    def test_labels_of_a_ftn_era_play(self, fr):
        p = play(fr, GA, 3)
        assert (p["formation"], p["personnel"], p["extra_ol"], p["def_package"]) == (
            "shotgun",
            "11",
            False,
            "nickel",
        )
        assert (p["coverage"], p["man_zone"], p["target_route"]) == ("cover_3", "zone", "slant")
        assert (p["pressure"], p["time_to_throw"], p["part_box"], p["part_rushers"]) == (
            True,
            2.5,
            6,
            4,
        )

    def test_a_blank_route_and_a_missing_coverage_are_null(self, fr):
        p = play(fr, GA, 5)
        assert p["target_route"] is None and p["coverage"] is None and p["man_zone"] is None
        assert p["formation"] == "shotgun"

    def test_man_coverage_and_the_label_rewriting(self, fr):
        p = play(fr, GA, 4)
        assert (p["formation"], p["coverage"], p["man_zone"]) == ("empty", "cover_0", "man")

    def test_an_ngs_era_play_gets_its_own_vocabulary(self, fr):
        p = play(fr, G19, 2)  # "1 RB, 1 TE, 3 WR" / "4 DL, 3 LB, 4 DB"
        assert (p["personnel"], p["extra_ol"], p["def_package"]) == ("11", False, "base")

    def test_the_newest_snapshot_of_a_season_wins(self, fr):
        # an older snapshot of 2024 says the formation was "OLD"
        assert play(fr, GA, 3)["formation"] == "shotgun"

    def test_the_current_season_never_has_participation(self, fr):
        p26 = fr.nodes["Play"].filter(pl.col("season") == 2026)
        assert p26["part"].null_count() == p26.height
        assert "2026" not in {e[:4] for e in fr.rels["ON_FIELD"]["e"].to_list()}


class TestFtn:
    def test_a_charted_play_has_its_labels(self, fr):
        p = play(fr, GA, 3)
        assert p["ftn"] is True and p["play_action"] is True and p["motion"] is True
        assert (p["screen"], p["rpo"]) == (False, False)
        assert (p["blitzers"], p["rushers"], p["blitz"], p["box"]) == (1, 5, True, 6)
        assert p["qb_alignment"] == "shotgun"

    def test_ftns_play_flags_ride_along_on_charted_plays(self, fr):
        # AE02's eval: the history graph lacked FTN's QB sneak (and drop, throw-away, out of
        # pocket), so "how many QB sneaks" was answered by a guess
        p = play(fr, GA, 3)
        assert (p["out_of_pocket"], p["drop"], p["qb_sneak"], p["throw_away"]) == (
            True, True, False, False,
        )  # fmt: skip
        g = play(fr, GC, 2)
        assert (g["drop"], g["qb_sneak"]) == (False, False)
        for c in ("qb_sneak", "drop", "throw_away", "out_of_pocket"):
            assert play(fr, GA, 4)[c] is None, c  # a placeholder row: not charted
            assert play(fr, G19, 2)[c] is None, c  # before FTN

    def test_a_placeholder_row_counts_as_not_charted(self, fr):
        p = play(fr, GA, 4)  # FTN's row: QB location '0', box 0, every flag false
        for c in (
            "ftn",
            "play_action",
            "screen",
            "rpo",
            "motion",
            "blitzers",
            "rushers",
            "blitz",
            "box",
            "qb_alignment",
        ):
            assert p[c] is None, c

    def test_ftn_labels_reach_the_current_season_too(self, fr):
        p = play(fr, GC, 2)
        assert p["ftn"] is True and p["screen"] is True and p["qb_alignment"] == "under_center"
        assert p["box"] == 7 and p["blitz"] is False
        assert play(fr, GC, 4)["ftn"] is None

    def test_plays_ftn_did_not_have_and_pre_2022_seasons_have_none(self, fr):
        assert play(fr, GA, 5)["ftn"] is None
        assert play(fr, G19, 2)["ftn"] is None
        assert play(fr, GB, 5)["box"] is None

    def test_the_ftn_flag_is_true_or_null_never_false(self, fr):
        assert fr.nodes["Play"].filter(pl.col("ftn") == False).is_empty()  # noqa: E712
        assert fr.nodes["Play"]["ftn"].sum() == 2  # game A play 3 and game C play 2


# ---- drives, games ---------------------------------------------------------------------------


class TestDrives:
    def drive(self, fr, game, n):
        return one(fr.nodes["Drive"], drive_key=f"{game}:{n}")

    def test_a_drive_belongs_to_the_first_non_kickoff_offense(self, fr):
        # drive 1 of game A starts with a kickoff "possessed" by LV; KC runs the first play
        assert self.drive(fr, GA, 1)["team"] == "KC"
        assert self.drive(fr, GB, 1)["team"] == "KC"
        assert self.drive(fr, GA, 2)["team"] == "LV"

    def test_a_drive_of_only_kickoffs_falls_back_to_the_kickoffs_team(self, fr):
        assert self.drive(fr, GB, 2)["team"] == "DEN"

    def test_drive_properties_take_the_first_value_the_drive_has(self, fr):
        d = self.drive(fr, GA, 1)  # result is only on play 5, the rest on play 3
        assert d["result"] == "Turnover" and d["plays"] == 3 and d["first_downs"] == 1
        assert (d["quarter"], d["start"], d["end"], d["time_of_possession"]) == (
            1,
            "LV 25",
            "KC 40",
            "1:30",
        )
        assert (d["season"], d["week"], d["drive"], d["game_id"]) == (2024, 1, 1, GA)
        assert d["scored"] is False and self.drive(fr, GA, 3)["scored"] is True

    def test_drive_counts(self, fr):
        by = fr.nodes["Drive"].group_by("season").len().sort("season")
        assert by.rows() == [(2019, 2), (2024, 7), (2026, 4)]

    def test_in_drive_links_every_play_to_its_drive(self, fr):
        links = pairs(fr, "IN_DRIVE", GA)
        assert (f"{GA}:2", f"{GA}:1") in links and (f"{GA}:5", f"{GA}:1") in links
        assert (f"{GA}:8", f"{GA}:2") in links and (f"{GA}:16", f"{GA}:3") in links
        assert (f"{GB}:4", f"{GB}:2") in {(s, e) for s, e in fr.rels["IN_DRIVE"].iter_rows()}
        # the timeout (row 14) had a drive number but isn't a play: nothing links it
        assert (f"{GA}:14", f"{GA}:3") not in links

    def test_every_play_is_in_exactly_one_drive(self, fr):
        assert fr.rels["IN_DRIVE"]["s"].n_unique() == fr.nodes["Play"].height

    def test_in_game_links_drives_to_their_game(self, fr):
        links = {(s, e) for s, e in fr.rels["IN_GAME"].iter_rows()}
        assert (f"{GA}:1", GA) in links and (f"{GD}:2", GD) in links
        assert len(links) == 13


class TestGamesAndTeams:
    def test_game_rows(self, fr):
        g = one(fr.nodes["Game"], game_id=GA)
        assert (g["season"], g["week"], g["game_type"], g["gameday"]) == (
            2024,
            1,
            "REG",
            "2024-09-08",
        )
        assert (g["home_team"], g["away_team"], g["home_score"], g["away_score"]) == (
            "KC",
            "LV",
            27,
            20,
        )
        assert g["overtime"] is False and g["stadium"] == "Arrowhead" and g["roof"] == "outdoors"
        assert (g["home_coach"], g["away_coach"]) == ("Andy Reid", "Antonio Pierce")

    def test_head_coach_fixes_are_applied_to_the_games(self, fr):
        assert (
            one(fr.nodes["Game"], game_id=GC)["away_coach"] == "Pete Carroll"
        )  # was "Wrong Coach"
        assert one(fr.nodes["Game"], game_id=GD)["home_coach"] == "Interim Joe"  # from week 2

    def test_structure_relationships(self, fr):
        assert {(s, e) for s, e in fr.rels["IN_SEASON"].iter_rows()} == {
            (G19, 2019),
            (GA, 2024),
            (GB, 2024),
            (GC, 2026),
            (GD, 2026),
        }
        assert (GA, "KC") in pairs(fr, "HOME_TEAM") and (GA, "LV") in pairs(fr, "AWAY_TEAM")
        assert (GD, "DEN") in pairs(fr, "HOME_TEAM") and (GD, "KC") in pairs(fr, "AWAY_TEAM")

    def test_offense_and_defense_relationships(self, fr):
        assert (f"{GA}:3", "KC") in pairs(fr, "OFFENSE") and (f"{GA}:3", "LV") in pairs(
            fr, "DEFENSE"
        )
        assert (f"{GA}:2", "LV") in pairs(fr, "OFFENSE")  # the kickoff's receiving team
        assert f"{GB}:8" not in {s for s, _ in fr.rels["OFFENSE"].iter_rows()}  # team XXX
        assert (f"{GB}:8", "DEN") in pairs(fr, "DEFENSE")

    def test_team_nodes(self, fr):
        t = one(fr.nodes["Team"], team_id="KC")
        assert t["name"] == "Kansas City Chiefs" and t["nickname"] == "Chiefs"
        assert (t["conference"], t["division"]) == ("AFC", "AFC West")
        assert set(fr.nodes["Team"]["team_id"]) == {"KC", "LV", "DEN"}

    def test_season_nodes(self, fr):
        assert fr.nodes["Season"].columns == ["season"]
        assert fr.nodes["Season"]["season"].to_list() == [2019, 2024, 2026]


# ---- rosters, coaches ------------------------------------------------------------------------


class TestPlayedFor:
    def pf(self, fr, pid, team, season):
        return one(fr.rels["PLAYED_FOR"], s=pid, e=team, season=season)

    def test_weeks_games_and_last_status(self, fr):
        r = self.pf(fr, QB_KC, "KC", 2024)
        assert r["weeks"] == [1, 2, 3] and (r["first_week"], r["last_week"]) == (1, 3)
        assert r["games"] == 2 and r["status_last"] == "ACT"

    def test_games_are_box_score_games_for_that_team_distinct(self, fr):
        assert self.pf(fr, WR_KC, "KC", 2024)["games"] == 2  # a duplicated row in game A
        assert self.pf(fr, RB_KC, "KC", 2024)["games"] == 0  # on the roster, no box score
        assert self.pf(fr, QB_KC, "KC", 2019)["weeks"] == [2]
        assert self.pf(fr, QB_KC, "KC", 2019)["games"] == 1  # games and weeks are independent

    def test_cut_players_have_no_link_but_traded_players_have_one_per_team(self, fr):
        assert "00-0021" not in set(fr.rels["PLAYED_FOR"]["s"])  # CUT every week
        lv = self.pf(fr, RB_LV, "LV", 2024)  # the week-2 LV row is TRD: excluded
        kc = self.pf(fr, RB_LV, "KC", 2024)
        assert lv["weeks"] == [1] and lv["games"] == 1
        assert kc["weeks"] == [2, 3] and kc["games"] == 1

    def test_a_player_not_in_the_player_table_still_has_his_link(self, fr):
        r = self.pf(fr, ROOKIE, "KC", 2024)
        assert r["weeks"] == [2, 3] and r["games"] == 0
        p = one(fr.nodes["Player"], player_id=ROOKIE)
        assert (p["name"], p["position"]) == ("Rick Rookie", "WR")  # from the roster

    def test_one_link_per_player_team_season(self, fr):
        df = fr.rels["PLAYED_FOR"]
        assert df.unique(["s", "e", "season"]).height == df.height == 25
        assert df.group_by("season").len().sort("season").rows() == [
            (2019, 4),
            (2024, 15),
            (2026, 6),
        ]

    def test_players_with_nothing_to_link_are_not_nodes(self, fr):
        ids = set(fr.nodes["Player"]["player_id"])
        assert "00-0021" not in ids  # in `players`, CUT on a roster, in no play
        assert len(ids) == 17
        assert {K_KC, UNK_FIELD, UNK_RECV, UNK_RUSH, ROOKIE} <= ids

    def test_player_node_properties(self, fr):
        p = one(fr.nodes["Player"], player_id=QB_KC)
        assert (p["name"], p["position"], p["position_group"]) == ("Pat Quarterback", "QB", "QB")


class TestCoaches:
    def test_head_coach_links_count_games_and_weeks(self, fr):
        hc = {
            (r["s"], r["e"], r["season"]): (r["games"], r["first_week"], r["last_week"])
            for r in fr.rels["HEAD_COACH"].iter_rows(named=True)
        }
        assert hc == {
            ("andy-reid", "KC", 2019): (1, 1, 1),
            ("jon-gruden", "LV", 2019): (1, 1, 1),
            ("andy-reid", "KC", 2024): (2, 1, 2),
            ("antonio-pierce", "LV", 2024): (1, 1, 1),
            ("sean-payton", "DEN", 2024): (1, 2, 2),
            ("andy-reid", "KC", 2026): (2, 1, 2),
            ("pete-carroll", "LV", 2026): (1, 1, 1),
            ("interim-joe", "DEN", 2026): (1, 2, 2),
        }

    def test_the_corrected_coaches_replace_the_schedules(self, fr):
        ids = set(fr.nodes["Coach"]["coach_id"])
        assert "wrong-coach" not in ids
        assert {"pete-carroll", "interim-joe"} <= ids

    def test_coordinators_come_from_the_seed_for_the_built_seasons_only(self, fr):
        def rels(t):
            return {(r["s"], r["e"], r["season"]) for r in fr.rels[t].iter_rows(named=True)}

        assert rels("OFF_COORD") == {("eric-bieniemy", "KC", 2019), ("eric-bieniemy", "KC", 2024)}
        assert rels("DEF_COORD") == {("bob-sutton", "KC", 2019), ("joe-defense", "LV", 2026)}
        assert rels("ST_COORD") == {("dave-toub", "KC", 2019)}
        # the 2017 row is outside the built seasons; the HC row isn't a coordinator
        assert "old-timer" not in set(fr.nodes["Coach"]["coach_id"])

    def test_coach_ids_are_name_slugs_and_names_are_kept(self, fr):
        c = one(fr.nodes["Coach"], coach_id="andy-reid")
        assert c["name"] == "Andy Reid"
        assert len(fr.nodes["Coach"]) == 11


class TestCoachNamesThatSlugToNothing:
    """A coach name made only of punctuation slugs to "" (a blank key): it is dropped, the
    export carries on."""

    def test_such_a_head_coach_is_skipped_not_fatal(self, tmp_path, monkeypatch):
        w = install_world(tmp_path, monkeypatch)
        for g in w.games:
            if g["game_id"] == GB:
                g["away_coach"] = "???"  # Sean Payton's only 2024 game
        w.write()
        fr = load(w)
        ids = set(fr.nodes["Coach"]["coach_id"])
        assert "" not in ids and "sean-payton" not in ids
        assert fr.nodes["Coach"].height == 10 and fr.rels["HEAD_COACH"].height == 7
        assert (fr.rels["HEAD_COACH"]["s"].str.strip_chars() == "").sum() == 0
        assert one(fr.nodes["Game"], game_id=GB)["away_coach"] == "???"  # the game keeps its text

    def test_such_a_seed_coach_is_skipped_not_fatal(self, tmp_path, monkeypatch):
        w = install_world(tmp_path, monkeypatch)
        w.seed += [
            {"coach": "!!!", "team": "KC", "season": 2024, "role": "DC"},
            {"coach": "---", "team": "LV", "season": 2024, "role": "OC"},
        ]
        w.write()
        fr = load(w)
        assert "" not in set(fr.nodes["Coach"]["coach_id"])
        assert fr.nodes["Coach"].height == 11  # the clean league's number
        assert fr.rels["DEF_COORD"].height == 2 and fr.rels["OFF_COORD"].height == 2
        assert fr.dropped == {
            "OFFENSE": 1,
            "OFF_COORD": 1,
            "participation_plays_not_in_pbp": 1,
        }  # (they are filtered, not counted as dangling)


# ---- reading the files -----------------------------------------------------------------------


class TestLoadInputs:
    def test_through_week_limits_the_last_season_only(self, world):
        inp = X.load_inputs(world.paths, [2024, 2026], through_week=1, log=lambda *_: None)
        weeks = inp.plays.group_by("season").agg(pl.col("week").max()).sort("season")
        assert weeks.rows() == [(2024, 2), (2026, 1)]  # 2024 is a finished season: all weeks
        r26 = inp.rosters.filter(pl.col("season") == 2026)
        assert r26["week"].max() == 1

    def test_the_current_season_alone_through_week_one(self, world):
        f = load(world, seasons=(2026,), through_week=1, participation=False)
        assert f.nodes["Play"]["game_id"].unique().to_list() == [GC]
        assert f.nodes["Game"].height == 1 and f.nodes["Play"].height == 5
        assert f.rels["PLAYED_FOR"]["weeks"].list.max().max() == 1

    def test_participation_can_be_left_out(self, world):
        f = load(world, seasons=(2024,), participation=False)
        assert f.rels["ON_FIELD"].is_empty()
        assert f.nodes["Play"]["part"].null_count() == f.nodes["Play"].height

    def test_a_season_without_plays_is_a_clear_error(self, world):
        with pytest.raises(FileNotFoundError, match="no curated plays for 2025"):
            X.load_inputs(world.paths, [2024, 2025], log=lambda *_: None)

    def test_no_seasons_is_an_error(self, world):
        with pytest.raises(ValueError, match="no seasons"):
            X.load_inputs(world.paths, [], log=lambda *_: None)

    def test_missing_participation_is_noted_not_fatal(self, tmp_path, monkeypatch):
        w = install_world(tmp_path, monkeypatch)
        for f in (w.paths.research / "nflverse" / "participation").glob("snapshot=*/*.parquet"):
            f.unlink()  # 2018-2025 participation is expected for 2024, and it isn't there
        inp = X.load_inputs(w.paths, [2024], log=lambda *_: None)
        assert inp.on_field.is_empty()
        assert any("participation missing for [2024]" in n for n in inp.notes)
        f = load(w, seasons=(2024,))
        assert f.rels["ON_FIELD"].is_empty() and f.notes

    def test_a_missing_ftn_file_is_noted_not_fatal(self, world, tmp_path, monkeypatch):
        w = install_world(tmp_path, monkeypatch)
        (w.paths.curated / "ftn_plays.parquet").unlink()
        f = load(w, seasons=(2024,))
        assert f.notes == ["ftn_plays missing: no FTN labels"]
        assert f.nodes["Play"]["ftn"].null_count() == f.nodes["Play"].height

    def test_the_curated_files_are_not_modified(self, world):
        before = {p: p.stat().st_mtime_ns for p in world.paths.root.rglob("*") if p.is_file()}
        load(world)
        after = {p: p.stat().st_mtime_ns for p in world.paths.root.rglob("*") if p.is_file()}
        assert before == after


class TestParticipationWindow:
    """Participation is read for every built season that has a file (2016 on); a season whose
    file isn't there gets a note, not an error. The weekly append never reads it."""

    def test_the_window_starts_in_2016_and_has_no_upper_cap(self):
        assert X.PART_FIRST == 2016
        assert not hasattr(X, "PART_LAST")  # the file's presence decides

    def test_a_built_season_inside_the_window_reads_its_participation(self, world):
        inp = X.load_inputs(world.paths, [2019, 2024], log=lambda *_: None)
        assert set(inp.part["game_id"].unique()) == {G19, GA}

    def test_a_2026_participation_file_is_loaded_when_it_exists(self, tmp_path, monkeypatch):
        w = install_world(tmp_path, monkeypatch)
        row = part_row(GC, 2, [QB_KC, WR_KC], [DB_LV], ["QB", "WR"], ["CB"])
        w.part["2027-02-10"] = {2026: [row]}
        w.write()
        notes: list[str] = []
        inp = X.load_inputs(w.paths, [2026], log=notes.append)
        assert inp.notes == [] and notes == []
        assert inp.part["game_id"].to_list() == [GC]
        assert inp.on_field.height == 3
        fr = load(w, seasons=(2026,))
        p = play(fr, GC, 2)
        assert p["part"] is True and p["formation"] == "shotgun"
        assert fr.rels["ON_FIELD"].height == 3
        assert fr.dropped == {} and fr.notes == []

    def test_the_append_never_reads_participation_even_when_the_file_exists(
        self, tmp_path, monkeypatch
    ):
        w = install_world(tmp_path, monkeypatch)
        w.part["2027-02-10"] = {2026: [part_row(GC, 2, [QB_KC], [DB_LV], ["QB"], ["CB"])]}
        w.write()
        inp = X.load_inputs(w.paths, [2026], participation=False, log=lambda *_: None)
        assert inp.part.is_empty() and inp.on_field.is_empty() and inp.notes == []

    def test_a_season_without_its_file_gets_a_note(self, world):
        notes: list[str] = []
        inp = X.load_inputs(world.paths, [2024, 2026], log=notes.append)
        assert inp.notes == ["participation missing for [2026]"]
        assert any("participation missing for [2026]" in n for n in notes)
        assert inp.part["game_id"].unique().to_list() == [GA]  # 2024 still loads
