"""Missing values in the derive's inputs (AE03): a play with no defense team, a player with no
position, a season-less row. A person link's stint always names a team (the evidence text would
say "None 2019" otherwise) and nothing crashes."""

from __future__ import annotations

import polars as pl
import pytest
from history_fakes import no_real_docker  # noqa: F401  (the safety net)
from history_mini import make_frames

from nflengine.history_graph import derive as D


def play(key, off="KC", dfn="LV", ptype="pass"):
    return {
        "play_key": key, "game_id": key.split(":")[0], "season": 2024, "offense": off,
        "defense": dfn, "play_type": ptype, "two_point": False, "complete": None, "yards": None,
        "epa": None, "touchdown": False, "fumble_lost": False,
    }  # fmt: skip


def on(play_key, side, *players):
    return [{"s": p, "e": play_key, "side": side, "position": pos} for p, pos in players]


def no_snaps() -> pl.DataFrame:
    return pl.DataFrame(
        schema={"p": pl.String, "team": pl.String, "season": pl.Int64, "week": pl.Int64,
                "off": pl.Int64, "def": pl.Int64, "st": pl.Int64}
    )  # fmt: skip


PLAYERS = [("o1", "QB"), ("o2", "WR"), ("d1", "CB"), ("d2", "DE")]


def derive(plays, rows):
    fr = make_frames(plays, rows, players=PLAYERS, teams=["KC", "LV"], coaches=[])
    return D.derived_frames(fr, no_snaps())[0]


def teams_in(links: pl.DataFrame) -> list:
    return [t for ts in links["teams"].to_list() for t in ts] + [
        t for ts in links["stint_teams"].to_list() for t in ts
    ]


# A play whose offense or defense team is missing must never give a stint with no team (Neo4j
# can't store a null in a list property, so the sync would fail). The real graph has no such play
# (0 of the 374k Play nodes, checked 2026-10-19); `derived_frames` ignores such rows when it picks
# a player's team in a game, and a player with no team in a game makes no stint there.


class TestNullTeams:
    def test_a_play_without_a_defense_team_gives_no_team_less_stint(self):
        rows = on("g1:1", "offense", ("o1", "QB"), ("o2", "WR")) + on(
            "g1:1", "defense", ("d1", "CB"), ("d2", "DE")
        )
        res = derive([play("g1:1", dfn=None)], rows)
        assert None not in teams_in(res["TEAMMATE"])  # (the pair may be left out; never "None")

    def test_a_tie_between_a_known_team_and_a_missing_one_goes_to_the_known_team(self):
        # d1 and d2 have one snap for a missing defense team and one for LV in the same game
        rows = []
        for key in ("g1:1", "g1:2"):
            rows += on(key, "defense", ("d1", "CB"), ("d2", "DE"))
        res = derive([play("g1:1", dfn=None), play("g1:2", dfn="LV")], rows)
        t = res["TEAMMATE"]
        assert None not in teams_in(t)
        assert t.height == 1 and t["teams"].to_list() == [["LV"]]

    def test_a_play_without_an_offense_team_is_not_a_play_of_that_team(self):
        rows = on("g1:1", "offense", ("o1", "QB"), ("o2", "WR"))
        res = derive([play("g1:1", off=None)], rows)
        assert None not in teams_in(res["TEAMMATE"])


class TestOtherMissing:
    def test_a_missing_position_is_never_a_matchup_end(self):
        rows = on("g1:1", "offense", ("o1", None)) + on("g1:1", "defense", ("d1", None))
        res = derive([play("g1:1")], rows)
        assert res["OPPONENT"].height == 0

    @pytest.mark.parametrize("ptype", [None, "no_play"])
    def test_plays_without_a_usable_type_still_make_teammates(self, ptype):
        rows = on("g1:1", "offense", ("o1", "QB"), ("o2", "WR"))
        res = derive([play("g1:1", ptype=ptype)], rows)
        assert res["TEAMMATE"].height == 1 and res["OPPONENT"].height == 0

    def test_an_on_field_row_for_a_play_the_graph_lacks_is_ignored(self):
        rows = on("g9:9", "offense", ("o1", "QB"), ("o2", "WR"))
        res = derive([play("g1:1")], rows)
        assert res["TEAMMATE"].height == 0
