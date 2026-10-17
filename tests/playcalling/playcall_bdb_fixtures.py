"""PC03 fixtures: a SYNTHETIC Big Data Bowl 2026 raw folder (`bdb/2026/raw/`) for the concept
library tests. Nothing here is real BDB data and nothing comes from the network: the columns are
BDB 2026 Analytics' documented ones (snake_case) as `playcalling/bdb.py` reads them:

- `supplementary_data.csv`: one row per pass play (game_id, play_id, week, teams, the labels);
- `input_2023_wWW.csv`: every tracked player, 10 frames a second until the throw (player_side,
  player_role Passer / Targeted Receiver / Other Route Runner / Defensive Coverage, the
  position, play_direction, absolute_yardline_number, num_frames_output, ball_land_x / y);
- `output_2023_wWW.csv`: frames after the throw for some players (game_id, play_id, nfl_id,
  frame_id, x, y).

A tracked play has 1 passer, 1 target, 3 other route runners and 7 coverage defenders (the
linemen aren't tracked in this edition). The offense stands behind the line of scrimmage on the
side it comes from (`play_direction`), the target runs towards where the ball lands, and the
ball is not tracked. Player ids: offense 1001-1005, defense 2001-2007.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

SEASON = 2023
N_IN = 15  # input frames: the throw is the last one, at t = 1.4 s
N_OUT = 12  # output frames: 1.2 s in the air
LOS_X = 70.0  # absolute x of the line (field coordinates 0-120)
LAND_DEPTH = 12.0  # yards downfield where the ball lands
LAND_Y = 20.0

PASSER, TARGET = 1001, 1003
OFFENSE = {
    1001: ("QB", "Passer"),
    1002: ("RB", "Other Route Runner"),
    1003: ("WR", "Targeted Receiver"),
    1004: ("WR", "Other Route Runner"),
    1005: ("TE", "Other Route Runner"),
}
DEFENSE = {
    2001: "CB", 2002: "CB", 2003: "FS", 2004: "SS", 2005: "MLB", 2006: "OLB", 2007: "OLB",
}  # fmt: skip
OUT_PLAYERS = (1003, 2001, 2002, 2003)  # who is tracked after the throw


def supp_row(gid: int, pid: int, week: int, away: str, home: str, **over: Any) -> dict[str, Any]:
    """One supplementary_data.csv row: a complete 8-yard pass against Cover 3 zone."""
    return {
        "game_id": gid,
        "season": SEASON,
        "week": week,
        "home_team_abbr": home,
        "visitor_team_abbr": away,
        "play_id": pid,
        "play_description": "(13:25) 1-QB pass short middle to 3-WR for 8 yards (9-Tackler).",
        "quarter": 2,
        "game_clock": "13:25",
        "down": 1,
        "yards_to_go": 10,
        "possession_team": away,
        "defensive_team": home,
        "pass_result": "C",
        "pass_length": 8,
        "route_of_targeted_receiver": "GO",
        "play_action": "N",
        "team_coverage_man_zone": "ZONE_COVERAGE",
        "team_coverage_type": "COVER_3_ZONE",
        "offense_formation": "SHOTGUN",
        "receiver_alignment": "2x2",
        "penalty_yards": None,
        "play_nullified_by_penalty": "N",
        "yards_gained": 8,
        "expected_points_added": 0.5,
        **over,
    }


def tracking(
    gid: int,
    pid: int,
    direction: str = "right",
    ayl: float = LOS_X,
    n_in: int = N_IN,
    n_out: int = N_OUT,
    passer: bool = True,
    target: bool = True,
    land: bool = True,
    out_players: tuple[int, ...] = OUT_PLAYERS,
    positions: dict[int, str] | None = None,
    land_depth: float = LAND_DEPTH,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(input rows, output rows) of one play. A right-moving offense stands at x < ayl (the line),
    a left-moving one at x > ayl; the target runs towards the landing spot, everybody drifts."""
    s = -1.0 if direction == "right" else 1.0  # which side of the line the offense is on
    fwd = -s  # downfield: +x for a right-moving play
    land_x = ayl + fwd * land_depth
    inp: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    pos_over = positions or {}

    def start(nid: int, k: int) -> tuple[float, float]:
        if nid in OFFENSE:  # a shotgun line: the passer 5 yards deep, the others up front
            depth = 5.0 if nid == PASSER else 1.0
            return ayl + s * depth, 30.0 - 4.0 * k
        k -= len(OFFENSE)
        return ayl - s * (3.0 + 2.0 * (k % 3)), 12.0 + 5.0 * k

    for k, nid in enumerate([*OFFENSE, *DEFENSE]):
        if nid == PASSER and not passer:
            continue
        if nid == TARGET and not target:
            continue
        x0, y0 = start(nid, k)
        if nid in OFFENSE:
            pos, role = OFFENSE[nid]
            side = "offense"
        else:
            pos, role, side = DEFENSE[nid], "Defensive Coverage", "defense"
        pos = pos_over.get(nid, pos)
        speed = 0.6 if nid == TARGET else 0.1
        for f in range(1, n_in + 1):
            inp.append(
                {
                    "game_id": gid,
                    "play_id": pid,
                    "player_to_predict": nid in out_players,
                    "nfl_id": nid,
                    "frame_id": f,
                    "play_direction": direction,
                    "absolute_yardline_number": int(ayl),
                    "player_name": f"Player {nid}",
                    "player_position": pos,
                    "player_side": side,
                    "player_role": role,
                    "x": round(x0 + fwd * speed * (f - 1), 2),
                    "y": round(y0, 2),
                    "num_frames_output": n_out,
                    "ball_land_x": round(land_x, 2) if land else None,
                    "ball_land_y": LAND_Y if land else None,
                }
            )
        if nid in out_players:
            xe = x0 + fwd * speed * (n_in - 1)
            tx, ty = (land_x, LAND_Y) if nid == TARGET else (xe + fwd * 0.5, y0)
            for f in range(1, n_out + 1):
                a = f / n_out
                out.append(
                    {
                        "game_id": gid,
                        "play_id": pid,
                        "nfl_id": nid,
                        "frame_id": f,
                        "x": round(xe + (tx - xe) * a, 2),
                        "y": round(y0 + (ty - y0) * a, 2),
                    }
                )
    return inp, out


class Synth:
    """Collects plays and their tracking, then writes the raw folder."""

    def __init__(self, raw: Path) -> None:
        self.raw = raw
        self.supp: list[dict[str, Any]] = []
        self.inp: dict[int, list[dict[str, Any]]] = {}
        self.out: dict[int, list[dict[str, Any]]] = {}

    def play(
        self,
        gid: int,
        pid: int,
        week: int,
        away: str,
        home: str,
        tracked: bool = True,
        **over: Any,
    ) -> Synth:
        """`over` goes to the supplementary row, except the `track_*` keys (to `tracking`)."""
        track = {
            k.removeprefix("track_"): over.pop(k) for k in list(over) if k.startswith("track_")
        }
        self.supp.append(supp_row(gid, pid, week, away, home, **over))
        if tracked:
            i, o = tracking(gid, pid, **track)
            self.inp.setdefault(week, []).extend(i)
            self.out.setdefault(week, []).extend(o)
        return self

    def write(self, only_supp: bool = False, weeks: tuple[int, ...] | None = None) -> Path:
        self.raw.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(self.supp, infer_schema_length=None).write_csv(
            self.raw / "supplementary_data.csv"
        )
        if only_supp:
            return self.raw
        for wk, rows in self.inp.items():
            if weeks is not None and wk not in weeks:
                continue
            pl.DataFrame(rows, infer_schema_length=None).write_csv(
                self.raw / f"input_{SEASON}_w{wk:02d}.csv"
            )
            pl.DataFrame(self.out[wk], infer_schema_length=None).write_csv(
                self.raw / f"output_{SEASON}_w{wk:02d}.csv"
            )
        return self.raw


G1, G2, G3 = 2023090700, 2023091000, 2023091700


def standard_world(raw: Path) -> Synth:
    """Three games over two weeks with every concept the library knows (some twice), plus plays
    the library must leave out: a penalty, a sack, a run-like row, a play with no concept, one
    with no tracked target."""
    s = Synth(raw)
    # week 1, game 1: a screen that is also Cover 3, and a play-action deep shot against Cover 1
    s.play(G1, 101, 1, "KC", "DET", route_of_targeted_receiver="SCREEN", pass_length=-2)
    s.play(
        G1, 102, 1, "KC", "DET", play_action="Y", pass_length=25, team_coverage_type="COVER_1_MAN",
        team_coverage_man_zone="MAN_COVERAGE", yards_gained=25, track_direction="left",
    )  # fmt: skip
    # week 1, game 2: a short play-action (not deep), a 2-man pass, a bad Cover 3 incompletion
    s.play(
        G2, 103, 1, "BUF", "NYJ", play_action="Y", pass_length=19, team_coverage_type="COVER_2_MAN"
    )
    s.play(
        G2, 105, 1, "BUF", "NYJ", pass_result="I", yards_gained=0, expected_points_added=-0.4,
        play_description="(9:00) 1-QB pass incomplete short left to 3-WR.",
    )  # fmt: skip
    # week 2, game 3: an interception against Cover 3, a left-moving Cover 3 completion
    s.play(
        G3, 106, 2, "KC", "NYJ", pass_result="IN", yards_gained=0, expected_points_added=-1.5,
        play_description="(4:00) 1-QB pass INTERCEPTED by 9-X at NYJ 40.",
    )  # fmt: skip
    s.play(G3, 110, 2, "KC", "NYJ", track_direction="left", track_ayl=30.0)
    # left out: nullified by a penalty, penalty yards, a sack, a scramble, no concept, no result
    s.play(G1, 104, 1, "KC", "DET", play_nullified_by_penalty="Y")
    s.play(G1, 111, 1, "KC", "DET", penalty_yards=10)
    s.play(G1, 107, 1, "KC", "DET", pass_result="S", yards_gained=-7)
    s.play(G1, 112, 1, "KC", "DET", pass_result="R", yards_gained=6)
    s.play(G1, 108, 1, "KC", "DET", team_coverage_type="COVER_2_ZONE")
    s.play(G1, 113, 1, "KC", "DET", pass_result=None, team_coverage_type=None, tracked=False)
    # picked but not drawable: the target isn't tracked
    s.play(G1, 109, 1, "KC", "DET", track_target=False, expected_points_added=0.1)
    return s
