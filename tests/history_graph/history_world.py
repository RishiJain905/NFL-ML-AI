"""A tiny league for the history-graph tests (AE02): curated + research files under a temp data
root, in the layout `history_graph.export.load_inputs` reads. A plain module, not a conftest
(`from conftest import` in the other test dirs must keep finding tests/conftest.py).

Three seasons: 2019 (the NGS era: participation without position lists), 2024 (the FTN era:
position lists, FTN charting, a participation play pbp doesn't have) and 2026 (the current
season: no participation). Three teams (KC, LV, DEN) and the games

    G19 2019_01_LV_KC   wk 1   GA 2024_01_LV_KC  wk 1 (the rich one)   GB 2024_02_DEN_KC wk 2
    GC  2026_01_LV_KC   wk 1   GD 2026_02_KC_DEN wk 2

Cast (`gsis_id`):
    00-0001 QB_KC   00-0002 RB_KC   00-0003 WR_KC   00-0004 TE_KC   00-0005 WR_LV   00-0006 DT_LV
    00-0007 LB_LV   00-0008 QB_LV   00-0009 DB_LV   00-0010 DE_KC   00-0011 OLB_KC 00-0012 RB_LV
    00-0013 K_KC    00-0020 roster-only rookie (not in `players`)    00-0021 on KC's roster as CUT
    00-0030 / 00-0031 unknown to `players` and to the rosters (a receiver and a rusher)
    00-0099 on the field in participation only
Expected counts are worked out by hand in the tests (the comments at each play say why).

`World.write()` writes every file from the row lists, so a test edits `world.plays[2026]`
(or any other list) and writes again: the "next week's data" of an append.
"""

from __future__ import annotations

import copy
import dataclasses
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.history_graph import export as X
from nflengine.paths import DataPaths

# ---- ids -------------------------------------------------------------------------------------
QB_KC, RB_KC, WR_KC, TE_KC = "00-0001", "00-0002", "00-0003", "00-0004"
WR_LV, DT_LV, LB_LV, QB_LV, DB_LV = "00-0005", "00-0006", "00-0007", "00-0008", "00-0009"
DE_KC, OLB_KC, RB_LV, K_KC = "00-0010", "00-0011", "00-0012", "00-0013"
ROOKIE, CUT = "00-0020", "00-0021"
UNK_RECV, UNK_RUSH, UNK_FIELD = "00-0030", "00-0031", "00-0099"

G19, GA, GB = "2019_01_LV_KC", "2024_01_LV_KC", "2024_02_DEN_KC"
GC, GD = "2026_01_LV_KC", "2026_02_KC_DEN"
GAME_META = {G19: (2019, 1), GA: (2024, 1), GB: (2024, 2), GC: (2026, 1), GD: (2026, 2)}

PLAY_SCHEMA: dict[str, Any] = {
    **{c: pl.String for c in X.STRING_COLUMNS},
    **{c: pl.Float64 for c in X.FLOAT_COLUMNS},
    **{c: pl.Int32 for c in X.INT_COLUMNS},
}
GAMES_SCHEMA = {
    "game_id": pl.String,
    "season": pl.Int32,
    "game_type": pl.String,
    "week": pl.Int32,
    "gameday": pl.String,
    "away_team": pl.String,
    "away_score": pl.Int32,
    "home_team": pl.String,
    "home_score": pl.Int32,
    "overtime": pl.Int32,
    "stadium": pl.String,
    "roof": pl.String,
    "away_coach": pl.String,
    "home_coach": pl.String,
}
TEAMS_SCHEMA = {
    c: pl.String for c in ("team", "team_name", "team_nick", "team_conf", "team_division")
}
PLAYERS_SCHEMA = {c: pl.String for c in ("gsis_id", "display_name", "position", "position_group")}
ROSTER_SCHEMA = {
    "season": pl.Int32,
    "week": pl.Int32,
    "team": pl.String,
    "gsis_id": pl.String,
    "status": pl.String,
    "position": pl.String,
    "full_name": pl.String,
}
PG_SCHEMA = {
    "player_id": pl.String,
    "game_id": pl.String,
    "team": pl.String,
    "season": pl.Int32,
    "week": pl.Int32,
}
FTN_SCHEMA = {
    "ftn_game_id": pl.Int32,
    "nflverse_game_id": pl.String,
    "season": pl.Int32,
    "week": pl.Int32,
    "nflverse_play_id": pl.Int32,
    "starting_hash": pl.String,
    "qb_location": pl.String,
    "n_offense_backfield": pl.Int32,
    "n_defense_box": pl.Int32,
    "is_no_huddle": pl.Boolean,
    "is_motion": pl.Boolean,
    "is_play_action": pl.Boolean,
    "is_screen_pass": pl.Boolean,
    "is_rpo": pl.Boolean,
    "is_trick_play": pl.Boolean,
    "is_qb_out_of_pocket": pl.Boolean,
    "is_throw_away": pl.Boolean,
    "is_drop": pl.Boolean,
    "is_qb_sneak": pl.Boolean,
    "n_blitzers": pl.Int32,
    "n_pass_rushers": pl.Int32,
}
PART_SCHEMA = {
    "nflverse_game_id": pl.String,
    "play_id": pl.Float64,
    "offense_formation": pl.String,
    "offense_personnel": pl.String,
    "defense_personnel": pl.String,
    "defense_coverage_type": pl.String,
    "defense_man_zone_type": pl.String,
    "route": pl.String,
    "was_pressure": pl.Boolean,
    "time_to_throw": pl.Float64,
    "defenders_in_box": pl.Int32,
    "number_of_pass_rushers": pl.Int32,
    "offense_players": pl.String,
    "defense_players": pl.String,
    "offense_positions": pl.String,
    "defense_positions": pl.String,
}


def frame(rows: list[dict[str, Any]], schema: dict[str, Any]) -> pl.DataFrame:
    """Rows as dicts -> a typed frame; columns a row leaves out are null; numbers are coerced."""
    for r in rows:
        extra = set(r) - set(schema)
        assert not extra, f"unknown columns {sorted(extra)}"
    cols = {}
    for c, t in schema.items():
        vals = []
        for r in rows:
            v = r.get(c)
            if v is not None and t in (pl.Float64,):
                v = float(v)
            elif v is not None and t in (pl.Int32, pl.Int64):
                v = int(v)
            vals.append(v)
        cols[c] = pl.Series(c, vals, dtype=t)
    return pl.DataFrame(cols)


# ---- plays -----------------------------------------------------------------------------------


def play(
    game: str,
    pid: int,
    ptype: str | None,
    off: str | None,
    dfn: str | None,
    drive: int | None,
    **kw: Any,
) -> dict[str, Any]:
    """One curated plays row (only the columns the exporter reads)."""
    season, week = GAME_META[game]
    row: dict[str, Any] = {
        "game_id": game,
        "play_id": pid,
        "season": season,
        "week": week,
        "season_type": "REG",
        "qtr": 1,
        "time": "15:00",
        "order_sequence": pid * 10,
        "play_type": ptype,
        "posteam": off,
        "defteam": dfn,
        "fixed_drive": drive,
    }
    row.update(kw)
    extra = set(row) - set(PLAY_SCHEMA)
    assert not extra, f"unknown play columns {sorted(extra)}"
    return row


def _pass(game, pid, drive, off, dfn, **kw):
    return play(game, pid, "pass", off, dfn, drive, **{"pass": 1}, **kw)


def plays_2019() -> list[dict[str, Any]]:
    return [
        play(G19, 1, None, None, None, None),  # game start: not a play
        _pass(
            G19,
            2,
            1,
            "KC",
            "LV",
            passer_player_id=QB_KC,
            receiver_player_id=WR_KC,
            complete_pass=1,
            yards_gained=7,
            epa=0.3,
            success=1,
            air_yards=5,
            down=1,
            ydstogo=10,
            drive_play_count=3,
            drive_first_downs=1,
            drive_quarter_start=1,
            fixed_drive_result="Punt",
            drive_start_yard_line="KC 25",
            drive_end_yard_line="KC 40",
            drive_time_of_possession="1:10",
            drive_ended_with_score=0,
        ),
        play(G19, 3, "run", "KC", "LV", 1, rusher_player_id=RB_KC, yards_gained=3, epa=-0.1),
        play(G19, 4, "punt", "KC", "LV", 1),
        play(
            G19,
            5,
            "run",
            "LV",
            "KC",
            2,
            rusher_player_id=RB_LV,
            yards_gained=2,
            epa=-0.2,
            fixed_drive_result="Punt",
            drive_play_count=1,
        ),
    ]


def plays_ga() -> list[dict[str, Any]]:
    d1 = dict(
        drive_play_count=3,
        drive_first_downs=1,
        drive_quarter_start=1,
        drive_start_yard_line="LV 25",
        drive_end_yard_line="KC 40",
        drive_time_of_possession="1:30",
        drive_ended_with_score=0,
    )
    d2 = dict(
        drive_play_count=6,
        drive_first_downs=0,
        drive_quarter_start=2,
        drive_start_yard_line="LV 20",
        drive_end_yard_line="LV 45",
        drive_time_of_possession="3:00",
        drive_ended_with_score=0,
    )
    d3 = dict(
        drive_play_count=4,
        drive_first_downs=0,
        drive_quarter_start=3,
        drive_start_yard_line="KC 30",
        drive_end_yard_line="LV 10",
        drive_time_of_possession="2:00",
        drive_ended_with_score=1,
    )
    desc3 = (
        'P.Quarterback pass short left to W.Receiver for 12 yards, "first down"; '
        "tackled\nby L.Linebacker\tand D.Tackle."
    )
    return [
        play(GA, 1, None, None, None, None),  # game start
        # 2: a kickoff the receiving team (LV) "possesses"; KC fumbles the return and takes over
        play(GA, 2, "kickoff", "LV", "KC", 1, solo_tackle_1_player_id=OLB_KC),
        # 3: complete pass, three kinds of tackle; the same man twice (solo wins), one blank id
        _pass(
            GA,
            3,
            1,
            "KC",
            "LV",
            down=1,
            ydstogo=10,
            yardline_100=75,
            goal_to_go=0,
            score_differential=0,
            wp=0.55,
            yards_gained=12,
            epa=0.5,
            success=1,
            first_down=1,
            touchdown=0,
            penalty=0,
            two_point_attempt=0,
            sack=0,
            qb_scramble=0,
            complete_pass=1,
            interception=0,
            fumble_lost=0,
            air_yards=8,
            yards_after_catch=4,
            pass_length="short",
            pass_location="left",
            run_location="left",
            run_gap="end",
            shotgun=1,
            no_huddle=0,
            desc=desc3,
            passer_player_id=QB_KC,
            receiver_player_id=WR_KC,
            solo_tackle_1_player_id=LB_LV,
            assist_tackle_1_player_id=DB_LV,
            assist_tackle_2_player_id=LB_LV,
            tackle_with_assist_1_player_id=DT_LV,
            solo_tackle_2_player_id="",
            **d1,
        ),
        _pass(
            GA,
            4,
            1,
            "KC",
            "LV",
            down=2,
            ydstogo=10,
            yardline_100=63,
            yards_gained=0,
            epa=-0.8,
            success=0,
            complete_pass=0,
            air_yards=20,
            pass_length="deep",
            pass_location="right",
            shotgun=0,
            passer_player_id=QB_KC,
            receiver_player_id=WR_KC,
        ),
        _pass(
            GA,
            5,
            1,
            "KC",
            "LV",
            down=3,
            ydstogo=10,
            yardline_100=63,
            yards_gained=0,
            epa=-4.0,
            success=0,
            complete_pass=0,
            interception=1,
            air_yards=15,
            pass_length="deep",
            pass_location="middle",
            passer_player_id=QB_KC,
            receiver_player_id=TE_KC,
            interception_player_id=DB_LV,
            fixed_drive_result="Turnover",
        ),
        play(
            GA,
            6,
            "run",
            "LV",
            "KC",
            2,
            down=1,
            ydstogo=10,
            yards_gained=4,
            epa=0.1,
            success=1,
            run_location="left",
            run_gap="end",
            pass_length="short",
            rusher_player_id=RB_LV,
            solo_tackle_1_player_id=DE_KC,
            **d2,
        ),
        # 7: a scramble (nflverse: a run by the passer): RUSHED, not PASSED
        play(
            GA,
            7,
            "run",
            "LV",
            "KC",
            2,
            **{"pass": 1},
            qb_scramble=1,
            yards_gained=6,
            epa=0.9,
            success=1,
            run_location="middle",
            passer_player_id=QB_LV,
            rusher_player_id=QB_LV,
            solo_tackle_1_player_id=OLB_KC,
        ),
        # 8: a sack (PASSED + SACKED); 9: a half sack
        _pass(
            GA,
            8,
            2,
            "LV",
            "KC",
            sack=1,
            yards_gained=-7,
            epa=-2.0,
            success=0,
            complete_pass=0,
            air_yards=None,
            passer_player_id=QB_LV,
            sack_player_id=DE_KC,
        ),
        _pass(
            GA,
            9,
            2,
            "LV",
            "KC",
            sack=1,
            yards_gained=-6,
            epa=-1.5,
            success=0,
            passer_player_id=QB_LV,
            half_sack_1_player_id=DE_KC,
            half_sack_2_player_id=OLB_KC,
        ),
        play(GA, 10, "qb_kneel", "LV", "KC", 2, yards_gained=-1, epa=-0.1, rusher_player_id=QB_LV),
        play(
            GA, 11, "punt", "LV", "KC", 2, solo_tackle_1_player_id=OLB_KC, fixed_drive_result="Punt"
        ),
        # 12: a no_play (penalty): the pass is wiped out, so no role, no tackle
        _pass(
            GA,
            12,
            3,
            "KC",
            "LV",
            penalty=1,
            complete_pass=0,
            passer_player_id=QB_KC,
            receiver_player_id=WR_KC,
            solo_tackle_1_player_id=LB_LV,
            **d3,
        )
        | {"play_type": "no_play"},
        # 13: a two-point try that was good: not in the box score, so no role
        _pass(
            GA,
            13,
            3,
            "KC",
            "LV",
            two_point_attempt=1,
            complete_pass=1,
            passer_player_id=QB_KC,
            receiver_player_id=WR_KC,
            solo_tackle_1_player_id=LB_LV,
        ),
        # 14: a timeout: a no_play without an offense, not a play
        play(GA, 14, "no_play", None, None, 3),
        play(GA, 15, "extra_point", "KC", "LV", 3),
        play(GA, 16, "field_goal", "KC", "LV", 3, fixed_drive_result="Field goal"),
        play(GA, 17, None, None, None, None),  # end of quarter: not a play
    ]


def plays_gb() -> list[dict[str, Any]]:
    return [
        play(GB, 1, None, None, None, None),
        # a kickoff "possessed" by DEN, then KC runs: the drive is KC's
        play(GB, 2, "kickoff", "DEN", "KC", 1),
        play(
            GB,
            3,
            "run",
            "KC",
            "DEN",
            1,
            rusher_player_id=RB_KC,
            yards_gained=5,
            epa=0.2,
            fixed_drive_result="Touchdown",
            drive_ended_with_score=1,
        ),
        # a drive of one kickoff: its team is the kickoff's
        play(GB, 4, "kickoff", "DEN", "KC", 2),
        _pass(
            GB,
            5,
            3,
            "KC",
            "DEN",
            passer_player_id=QB_KC,
            receiver_player_id=UNK_RECV,
            complete_pass=1,
            yards_gained=9,
            epa=0.6,
            success=1,
            air_yards=6,
        ),
        _pass(
            GB,
            6,
            3,
            "KC",
            "DEN",
            passer_player_id=QB_KC,
            receiver_player_id=WR_KC,
            complete_pass=0,
            epa=-0.5,
            success=0,
            air_yards=12,
        ),
        play(GB, 7, "run", "DEN", "KC", 4, rusher_player_id=UNK_RUSH, yards_gained=1, epa=-0.3),
        # a spike (nflverse play_type qb_spike): the box score counts it as an incomplete attempt
        play(
            GB,
            9,
            "qb_spike",
            "KC",
            "DEN",
            3,
            **{"pass": 1},
            complete_pass=0,
            passer_player_id=QB_KC,
            yards_gained=0,
            epa=-0.4,
            success=0,
            air_yards=0,
            pass_location="middle",
        ),
        # a team code that is not a team: OFFENSE for this play is dropped and counted
        play(GB, 8, "punt", "XXX", "DEN", 4),
    ]


def plays_2026() -> list[dict[str, Any]]:
    return [
        play(GC, 1, None, None, None, None),
        _pass(
            GC,
            2,
            1,
            "KC",
            "LV",
            passer_player_id=QB_KC,
            receiver_player_id=WR_KC,
            complete_pass=1,
            yards_gained=11,
            epa=0.4,
            success=1,
            air_yards=7,
            down=1,
            ydstogo=10,
            drive_play_count=4,
            fixed_drive_result="Punt",
        ),
        play(GC, 3, "run", "KC", "LV", 1, rusher_player_id=RB_KC, yards_gained=4, epa=0.0),
        _pass(
            GC,
            4,
            1,
            "KC",
            "LV",
            passer_player_id=QB_KC,
            receiver_player_id=WR_KC,
            complete_pass=0,
            epa=-0.7,
            success=0,
            air_yards=14,
        ),
        play(GC, 5, "punt", "KC", "LV", 1),
        play(GC, 6, "run", "LV", "KC", 2, rusher_player_id=RB_LV, yards_gained=6, epa=0.3),
        play(GD, 1, None, None, None, None),
        _pass(
            GD,
            2,
            1,
            "KC",
            "DEN",
            passer_player_id=QB_KC,
            receiver_player_id=TE_KC,
            complete_pass=1,
            yards_gained=15,
            epa=1.0,
            success=1,
            air_yards=9,
        ),
        play(GD, 3, "run", "DEN", "KC", 2, rusher_player_id=UNK_RUSH, yards_gained=2, epa=-0.2),
        play(GD, 4, "punt", "DEN", "KC", 2),
    ]


# ---- the other tables ------------------------------------------------------------------------


def game_row(
    gid: str,
    away: str,
    home: str,
    away_coach: str,
    home_coach: str,
    away_score: int = 20,
    home_score: int = 27,
) -> dict[str, Any]:
    season, week = GAME_META[gid]
    return {
        "game_id": gid,
        "season": season,
        "game_type": "REG",
        "week": week,
        "gameday": f"{season}-09-{7 + week:02d}",
        "away_team": away,
        "away_score": away_score,
        "home_team": home,
        "home_score": home_score,
        "overtime": 0,
        "stadium": "Arrowhead",
        "roof": "outdoors",
        "away_coach": away_coach,
        "home_coach": home_coach,
    }


def games_rows() -> list[dict[str, Any]]:
    return [
        game_row(G19, "LV", "KC", "Jon Gruden", "Andy Reid"),
        game_row(GA, "LV", "KC", "Antonio Pierce", "Andy Reid"),
        game_row(GB, "DEN", "KC", "Sean Payton", "Andy Reid"),
        # nflverse still has last season's coaches for two 2026 teams; the fixes file corrects them
        game_row(GC, "LV", "KC", "Wrong Coach", "Andy Reid"),
        game_row(GD, "KC", "DEN", "Andy Reid", "Sean Payton"),
    ]


def teams_rows() -> list[dict[str, Any]]:
    t = [
        ("KC", "Kansas City Chiefs", "Chiefs"),
        ("LV", "Las Vegas Raiders", "Raiders"),
        ("DEN", "Denver Broncos", "Broncos"),
    ]
    return [
        {"team": a, "team_name": n, "team_nick": k, "team_conf": "AFC", "team_division": "AFC West"}
        for a, n, k in t
    ]


def players_rows() -> list[dict[str, Any]]:
    p = [
        (QB_KC, "Pat Quarterback", "QB", "QB"),
        (RB_KC, "Ray Runner", "RB", "RB"),
        (WR_KC, "Wes Receiver", "WR", "WR"),
        (TE_KC, "Tom End", "TE", "TE"),
        (WR_LV, "Will Raider", "WR", "WR"),
        (DT_LV, "Dee Tackle", "DT", "DL"),
        (LB_LV, "Lou Linebacker", "ILB", "LB"),
        (QB_LV, "Quinn Vegas", "QB", "QB"),
        (DB_LV, "Dee Back", "CB", "DB"),
        (DE_KC, "Dan Edge", "DE", "DL"),
        (OLB_KC, "Ollie Backer", "OLB", "LB"),
        (RB_LV, "Rob Raider", "RB", "RB"),
        (K_KC, "Kit Kicker", "K", "SPEC"),
        (CUT, "Cal Cut", "WR", "WR"),
    ]
    return [dict(zip(PLAYERS_SCHEMA, r, strict=True)) for r in p]


def _r(season, week, team, pid, pos, name, status="ACT"):
    return {
        "season": season,
        "week": week,
        "team": team,
        "gsis_id": pid,
        "status": status,
        "position": pos,
        "full_name": name,
    }


def rosters_rows() -> list[dict[str, Any]]:
    # 2019: week 1 lists WR_KC as a TE (the roster beats `players`); QB_KC is there in week 2
    rows = [
        _r(2019, 1, "KC", WR_KC, "TE", "Wes Receiver"),
        _r(2019, 1, "KC", RB_KC, "RB", "Ray Runner"),
        _r(2019, 1, "LV", WR_LV, "WR", "Will Raider"),
        _r(2019, 2, "KC", QB_KC, "QB", "Pat Quarterback"),
    ]
    kc = [
        (QB_KC, "QB", "Pat Quarterback"),
        (RB_KC, "RB", "Ray Runner"),
        (WR_KC, "WR", "Wes Receiver"),
        (TE_KC, "TE", "Tom End"),
        (DE_KC, "DE", "Dan Edge"),
        (OLB_KC, "OLB", "Ollie Backer"),
        (K_KC, "K", "Kit Kicker"),
    ]
    lv = [
        (WR_LV, "WR", "Will Raider"),
        (DT_LV, "DT", "Dee Tackle"),
        (LB_LV, "ILB", "Lou Linebacker"),
        (QB_LV, "QB", "Quinn Vegas"),
        (DB_LV, "CB", "Dee Back"),
    ]
    for wk in (1, 2, 3):  # 2024: weeks 1-3 (week 3 has no game)
        rows += [_r(2024, wk, "KC", p, pos, n) for p, pos, n in kc]
        rows += [_r(2024, wk, "LV", p, pos, n) for p, pos, n in lv]
        rows.append(_r(2024, wk, "KC", CUT, "WR", "Cal Cut", status="CUT"))
    rows += [_r(2024, wk, "KC", ROOKIE, "WR", "Rick Rookie") for wk in (2, 3)]
    # RB_LV: LV in week 1, traded in week 2 (a TRD row at LV, then KC)
    rows += [
        _r(2024, 1, "LV", RB_LV, "RB", "Rob Raider"),
        _r(2024, 2, "LV", RB_LV, "RB", "Rob Raider", status="TRD"),
        _r(2024, 2, "KC", RB_LV, "RB", "Rob Raider"),
        _r(2024, 3, "KC", RB_LV, "RB", "Rob Raider"),
    ]
    for wk in (1, 2):  # 2026
        rows += [_r(2026, wk, "KC", p, pos, n) for p, pos, n in kc[:4]]
        rows += [
            _r(2026, wk, "KC", ROOKIE, "WR", "Rick Rookie"),
            _r(2026, wk, "LV", RB_LV, "RB", "Rob Raider"),
        ]
    return rows


def player_games_rows() -> list[dict[str, Any]]:
    def g(pid, gid, team):
        season, week = GAME_META[gid]
        return {"player_id": pid, "game_id": gid, "team": team, "season": season, "week": week}

    return [
        g(QB_KC, G19, "KC"),
        g(WR_KC, G19, "KC"),
        g(RB_KC, G19, "KC"),
        g(QB_KC, GA, "KC"),
        g(QB_KC, GB, "KC"),
        g(WR_KC, GA, "KC"),
        g(WR_KC, GA, "KC"),  # a duplicated row: one game
        g(WR_KC, GB, "KC"),
        g(WR_LV, GA, "LV"),
        g(RB_LV, GA, "LV"),
        g(RB_LV, GB, "KC"),
        g(QB_KC, GC, "KC"),
        g(QB_KC, GD, "KC"),
        g(WR_KC, GC, "KC"),
        g(RB_LV, GC, "LV"),
    ]


def ftn_rows() -> list[dict[str, Any]]:
    def row(gid, pid, qb, box, **kw):
        season, week = GAME_META[gid]
        return {
            "ftn_game_id": 1,
            "nflverse_game_id": gid,
            "season": season,
            "week": week,
            "nflverse_play_id": pid,
            "starting_hash": "M",
            "qb_location": qb,
            "n_offense_backfield": 1,
            "n_defense_box": box,
            "is_no_huddle": False,
            "is_motion": False,
            "is_play_action": False,
            "is_screen_pass": False,
            "is_rpo": False,
            "is_trick_play": False,
            "is_qb_out_of_pocket": False,
            "is_throw_away": False,
            "is_drop": False,
            "is_qb_sneak": False,
            "n_blitzers": 0,
            "n_pass_rushers": 4,
            **kw,
        }

    ph = dict(n_pass_rushers=0)  # a placeholder row: FTN did not chart the play
    return [
        row(
            GA,
            3,
            "S",
            6,
            is_motion=True,
            is_play_action=True,
            n_blitzers=1,
            n_pass_rushers=5,
            is_qb_out_of_pocket=True,
            is_drop=True,
        ),
        row(GA, 4, "0", 0, **ph),
        row(GC, 2, "U", 7, is_screen_pass=True),
        row(GC, 4, "0", 0, **ph),
    ]


def seed_rows() -> list[dict[str, Any]]:
    return [
        {"coach": "Eric Bieniemy", "team": "KC", "season": 2019, "role": "OC"},
        {"coach": "Bob Sutton", "team": "KC", "season": 2019, "role": "DC"},
        {"coach": "Dave Toub", "team": "KC", "season": 2019, "role": "ST"},
        {"coach": "Eric Bieniemy", "team": "KC", "season": 2024, "role": "OC"},
        {"coach": "Joe Defense", "team": "LV", "season": 2026, "role": "DC"},
        {"coach": "Old Timer", "team": "KC", "season": 2017, "role": "OC"},  # not a built season
        {"coach": "Andy Reid", "team": "KC", "season": 2024, "role": "HC"},  # not a coordinator
        {"coach": "Ghost Coach", "team": "XYZ", "season": 2024, "role": "OC"},  # not a team
    ]


def fixes_rows() -> list[dict[str, Any]]:
    return [
        {"season": 2026, "team": "LV", "from_week": 1, "coach": "Pete Carroll"},
        {"season": 2026, "team": "DEN", "from_week": 2, "coach": "Interim Joe"},
    ]


# ---- participation (research) ----------------------------------------------------------------

OFF_FTN = "1 C, 2 G, 1 QB, 1 RB, 2 T, 1 TE, 3 WR"  # FTN era: every position is counted
DEF_FTN = "3 CB, 2 DE, 2 DT, 1 FS, 1 MLB, 1 OLB, 1 SS"
OFF_NGS = "1 RB, 1 TE, 3 WR"
DEF_NGS = "4 DL, 3 LB, 4 DB"


def part_row(
    gid: str,
    pid: int,
    off: list[str],
    dfn: list[str],
    off_pos: list[str] | None = None,
    def_pos: list[str] | None = None,
    positions: bool = True,
    **kw: Any,
) -> dict[str, Any]:
    row = {
        "nflverse_game_id": gid,
        "play_id": pid,
        "offense_formation": "SHOTGUN",
        "offense_personnel": OFF_FTN if positions else OFF_NGS,
        "defense_personnel": DEF_FTN if positions else DEF_NGS,
        "defense_coverage_type": "COVER_3",
        "defense_man_zone_type": "ZONE_COVERAGE",
        "route": "SLANT",
        "was_pressure": False,
        "time_to_throw": 2.5,
        "defenders_in_box": 6,
        "number_of_pass_rushers": 4,
        "offense_players": ";".join(off),
        "defense_players": ";".join(dfn),
    }
    if positions:
        row["offense_positions"] = ";".join(off_pos) if off_pos else None
        row["defense_positions"] = ";".join(def_pos) if def_pos else None
    row.update(kw)
    return row


def part_2024() -> list[dict[str, Any]]:
    return [
        part_row(
            GA,
            3,
            [QB_KC, RB_KC, WR_KC],
            [DT_LV, LB_LV, UNK_FIELD],
            ["QB", "RB", "WR"],
            ["DT", "ILB", "S"],
            was_pressure=True,
        ),
        # no position lists on this play: the roster week / `players` stand in; the id nobody
        # knows has no position at all
        part_row(
            GA,
            4,
            [QB_KC, WR_KC],
            [DB_LV, UNK_FIELD],
            offense_formation="EMPTY",
            defense_coverage_type="COVER_0",
            defense_man_zone_type="MAN_COVERAGE",
        ),
        part_row(
            GA,
            5,
            [QB_KC, TE_KC],
            [DB_LV],
            ["QB", "TE"],
            ["CB"],
            route="",
            defense_coverage_type=None,
            defense_man_zone_type=None,
        ),
        part_row(GA, 6, [QB_LV, RB_LV], [DE_KC], ["QB", "RB"], ["DE"]),
        part_row(GA, 12, [QB_KC], [LB_LV], ["QB"], ["ILB"]),  # a no_play snap
        part_row(GA, 12, [QB_KC], [LB_LV], ["QB"], ["ILB"]),  # a repeated row: one set of links
        part_row(GA, 999, [QB_KC], [LB_LV], ["QB"], ["ILB"]),  # a play pbp does not have
    ]


def part_2024_older() -> list[dict[str, Any]]:
    """An older snapshot of the same season: the newer one must win."""
    return [part_row(GA, 3, [QB_KC], [DT_LV], ["QB"], ["DT"], offense_formation="OLD")]


def part_2019() -> list[dict[str, Any]]:
    kw = dict(positions=False)
    return [
        part_row(G19, 2, [QB_KC, WR_KC, RB_KC], [WR_LV, DT_LV], **kw),
        part_row(G19, 3, [RB_KC], [WR_LV], **kw),
        part_row(G19, 5, [RB_LV, QB_LV], [WR_KC], **kw),
    ]


# ---- the world -------------------------------------------------------------------------------


@dataclasses.dataclass
class World:
    paths: DataPaths
    plays: dict[int, list[dict[str, Any]]]
    games: list[dict[str, Any]]
    teams: list[dict[str, Any]]
    players: list[dict[str, Any]]
    rosters: list[dict[str, Any]]
    player_games: list[dict[str, Any]]
    ftn: list[dict[str, Any]]
    seed: list[dict[str, Any]]
    fixes: list[dict[str, Any]]
    part: dict[str, dict[int, list[dict[str, Any]]]]  # snapshot date -> season -> rows

    @property
    def seed_csv(self) -> Path:
        return self.paths.root / "config" / "coaching_seed.csv"

    @property
    def fixes_csv(self) -> Path:
        return self.paths.root / "config" / "head_coach_fixes.csv"

    def copy_rows(self) -> World:
        return dataclasses.replace(
            self,
            **{
                f.name: copy.deepcopy(getattr(self, f.name))
                for f in dataclasses.fields(self)
                if f.name != "paths"
            },
        )

    def write(self) -> World:
        cur = self.paths.curated
        (cur / "plays").mkdir(parents=True, exist_ok=True)
        for season, rows in self.plays.items():
            frame(rows, PLAY_SCHEMA).write_parquet(cur / "plays" / f"season={season}.parquet")
        frame(self.games, GAMES_SCHEMA).write_parquet(cur / "games.parquet")
        frame(self.teams, TEAMS_SCHEMA).write_parquet(cur / "teams.parquet")
        frame(self.players, PLAYERS_SCHEMA).write_parquet(cur / "players.parquet")
        frame(self.rosters, ROSTER_SCHEMA).write_parquet(cur / "rosters_weekly.parquet")
        frame(self.player_games, PG_SCHEMA).write_parquet(cur / "player_games.parquet")
        frame(self.ftn, FTN_SCHEMA).write_parquet(cur / "ftn_plays.parquet")
        for date, seasons in self.part.items():
            d = self.paths.research / "nflverse" / "participation" / f"snapshot={date}"
            d.mkdir(parents=True, exist_ok=True)
            for season, rows in seasons.items():
                schema = PART_SCHEMA
                if rows and "offense_positions" not in rows[0]:
                    schema = {k: v for k, v in PART_SCHEMA.items() if not k.endswith("_positions")}
                frame(rows, schema).write_parquet(d / f"season={season}.parquet")
        self.seed_csv.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            self.seed,
            schema={"coach": pl.String, "team": pl.String, "season": pl.Int32, "role": pl.String},
        ).write_csv(self.seed_csv)
        pl.DataFrame(
            self.fixes,
            schema={
                "season": pl.Int32,
                "team": pl.String,
                "from_week": pl.Int32,
                "coach": pl.String,
            },
        ).write_csv(self.fixes_csv)
        return self


def make_world(root: Path) -> World:
    """The files of the league under `root` (a data root); not yet written: call `.write()`."""
    return World(
        paths=DataPaths(root),
        plays={2019: plays_2019(), 2024: plays_ga() + plays_gb(), 2026: plays_2026()},
        games=games_rows(),
        teams=teams_rows(),
        players=players_rows(),
        rosters=rosters_rows(),
        player_games=player_games_rows(),
        ftn=ftn_rows(),
        seed=seed_rows(),
        fixes=fixes_rows(),
        part={
            "2026-09-01": {2024: part_2024_older()},
            "2026-10-03": {2019: part_2019(), 2024: part_2024()},
        },
    )


def install_world(root: Path, monkeypatch) -> World:
    """Write the league and point the coaching seed / head-coach fixes at its own copies, so a
    test never reads the repo's config files."""
    from nflengine.graph import tables_extra as TX

    world = make_world(root).write()
    monkeypatch.setattr(TX, "seed_path", lambda: world.seed_csv)
    monkeypatch.setattr(TX, "coach_fixes_path", lambda: world.fixes_csv)
    return world


def load(world: World, seasons=(2019, 2024, 2026), **kw):
    """`load_inputs` + `build_frames` for the world, quietly."""
    from nflengine.history_graph.export import build_frames, load_inputs

    quiet = kw.pop("log", lambda *_: None)
    inp = load_inputs(world.paths, list(seasons), log=quiet, **kw)
    return build_frames(inp, log=quiet)
