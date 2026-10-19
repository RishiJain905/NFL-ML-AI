"""A hand-made mini league for the derive / connections / lab tests (AE03): small export frames
(what `export.build_frames` returns) and snap counts, with every derived count worked out by hand
in `test_history_derive_frames.py`. A plain module (no conftest in a test subfolder).

The cast (ids are short on purpose; strings compare as Neo4j and DuckDB compare them):

    offense (KC)   o1 QB   o2 WR   o3 RB   o4 T (an OL)        zz  not a Player node (ON_FIELD only)
    defense (LV)   d1 CB   d2 DE   d3 ILB          t1 t2 WRs of the tie rule (g2)
    coaching side  q1 QB   q2 DL   q3 special teamer   q4 plays only on defense
                   q5 (2026: no staff)
                   n1 n2 on DEN's 2024 team
    coaches        hc (KC head coach, weeks 1-8)   oc (KC offensive coordinator, 2024 + 2025)
                   dc (KC defensive coordinator, 2024 + 2025)   stc (KC special teams, 2024)
                   both (DEN head coach AND offensive coordinator, 2024)
                   ghost (KC offensive coordinator 2024, not a Coach node)
    teams          KC LV DEN

Plays (`g1` is KC 2024 at home: KC on offense unless noted; `g2` is LV 2024 vs KC; `g3` is KC 2025
vs DEN):
    g1:1 pass o1 -> o2  complete  10 yds  epa 0.12345
    g1:2 pass o1 -> o2  incomplete (7 yds recorded: never counted)  epa -0.4
    g1:3 run  (o3)      5 yds
    g1:4 pass o1 -> o2  complete  20 yds  touchdown  epa 2.0  (zz is on the field too)
    g1:5 pass o1 -> o2  complete   8 yds  touchdown but the fumble was lost  epa -1.0
    g1:6 two-point try (o1, o2 on the field; no roles)
    g1:7 punt (o3, o4 on the field)
    g1:8 pass o1 -> o2  incomplete, yards and epa null
    g1:9 pass o1 -> o3  complete  5 yds  epa 0.3
    g1:10 pass o1 -> o1 (a passer is never his own target)
    g1:11 pass o1 -> "unk" (a target the graph doesn't hold)
    g1:12 punt: o3 and o4 are listed on the *defense* side (participation and the possession
          team disagree on a kick: the stint's team must still be KC, the team they played for
          most often in g1)
    g2:1 LV run (KC is on defense: counts for KC's plays, not for anyone's roles); t1 and t2 are
          on LV's side of it
    g2:2 KC punt: t1 and t2 are on KC's side (so each has one LV snap and one KC snap in g2: a
          tie, which goes to the team code that sorts first: KC)
    g3:1 DEN pass o1 -> o2 complete 12 yds epa 0.5 (2025: both were traded to DEN)
"""

from __future__ import annotations

from types import SimpleNamespace

import polars as pl

PLAYERS = [("o1", "QB"), ("o2", "WR"), ("o3", "RB"), ("o4", "T"), ("d1", "CB"), ("d2", "DE"),
           ("d3", "ILB"), ("q1", "QB"), ("q2", "DL"), ("q3", "K"), ("q4", "LB"), ("q5", "WR"),
           ("n1", "WR"), ("n2", "K"), ("t1", "WR"), ("t2", "WR")]  # fmt: skip
COACHES = ["hc", "oc", "dc", "stc", "both"]
TEAMS = ["KC", "LV", "DEN"]
# each mini game's week (the Play and Game nodes carry one; the staff links count by week)
GAME_WEEKS = {"g1": 1, "g2": 2, "g3": 1}

PLAY_SCHEMA = {
    "play_key": pl.String,
    "game_id": pl.String,
    "season": pl.Int64,
    "offense": pl.String,
    "defense": pl.String,
    "play_type": pl.String,
    "two_point": pl.Boolean,
    "complete": pl.Boolean,
    "yards": pl.Int64,
    "epa": pl.Float64,
    "touchdown": pl.Boolean,
    "fumble_lost": pl.Boolean,
}


def _play(key, season, off, dfn, ptype, complete=None, yards=None, epa=None, td=False, fl=False,
          two=False):  # fmt: skip
    return {"play_key": key, "game_id": key.split(":")[0], "season": season, "offense": off,
            "defense": dfn,
            "play_type": ptype, "two_point": two, "complete": complete, "yards": yards,
            "epa": epa, "touchdown": td, "fumble_lost": fl}  # fmt: skip


def plays() -> list[dict]:
    return [
        _play("g1:1", 2024, "KC", "LV", "pass", True, 10, 0.12345),
        _play("g1:2", 2024, "KC", "LV", "pass", False, 7, -0.4),
        _play("g1:3", 2024, "KC", "LV", "run", None, 5, 0.1),
        _play("g1:4", 2024, "KC", "LV", "pass", True, 20, 2.0, td=True),
        _play("g1:5", 2024, "KC", "LV", "pass", True, 8, -1.0, td=True, fl=True),
        _play("g1:6", 2024, "KC", "LV", "pass", True, 2, 0.9, two=True),
        _play("g1:7", 2024, "KC", "LV", "punt"),
        _play("g1:8", 2024, "KC", "LV", "pass", False, None, None),
        _play("g1:9", 2024, "KC", "LV", "pass", True, 5, 0.3),
        _play("g1:10", 2024, "KC", "LV", "pass", True, 3, 0.1),
        _play("g1:11", 2024, "KC", "LV", "pass", True, 4, 0.2),
        _play("g1:12", 2024, "KC", "LV", "punt"),
        _play("g2:1", 2024, "LV", "KC", "run", None, 3, 0.0),
        _play("g2:2", 2024, "KC", "LV", "punt"),
        _play("g3:1", 2025, "DEN", "KC", "pass", True, 12, 0.5),
    ]


def on_field() -> list[dict]:
    rows: list[dict] = []

    def of(play: str, side: str, *players: tuple[str, str]) -> None:
        rows.extend({"s": p, "e": play, "side": side, "position": pos} for p, pos in players)

    off4 = [("o1", "QB"), ("o2", "WR"), ("o3", "RB"), ("o4", "T")]
    def3 = [("d1", "CB"), ("d2", "DE"), ("d3", "ILB")]
    for k in ("g1:1", "g1:2", "g1:5"):
        of(k, "offense", *off4)
        of(k, "defense", *def3)
    of("g1:3", "offense", ("o1", "QB"), ("o3", "RB"), ("o4", "T"))
    of("g1:3", "defense", *def3)
    of("g1:4", "offense", *off4, ("zz", "WR"))
    of("g1:4", "defense", *def3)
    of("g1:6", "offense", ("o1", "QB"), ("o2", "WR"))
    of("g1:6", "defense", ("d1", "CB"))
    of("g1:7", "offense", ("o3", "RB"), ("o4", "T"))
    of("g1:7", "defense", ("d1", "CB"))
    of("g1:8", "offense", ("o1", "QB"), ("o2", "WR"))
    of("g1:8", "defense", ("d2", "DE"))
    of("g1:12", "defense", ("o3", "RB"), ("o4", "T"))
    of("g2:1", "offense", ("t1", "WR"), ("t2", "WR"))
    of("g2:2", "offense", ("t1", "WR"), ("t2", "WR"))
    of("g3:1", "offense", ("o1", "QB"), ("o2", "WR"))  # DEN's side in 2025
    of("g3:1", "defense", ("d1", "CB"))  # KC's
    return rows


def _pairs(rows):
    return pl.DataFrame(rows, schema={"s": pl.String, "e": pl.String}, orient="row")


def _staff(rows, with_weeks=False):
    schema = {"s": pl.String, "e": pl.String, "season": pl.Int64}
    if with_weeks:
        schema |= {"games": pl.Int64, "first_week": pl.Int64, "last_week": pl.Int64}
    return pl.DataFrame(rows, schema=schema, orient="row")


def make_frames(
    plays_rows, on_field_rows, passed=(), targeted=(), players=PLAYERS, coaches=COACHES,
    teams=TEAMS, head=(), off=(), dfn=(), st=(), games=(),
) -> SimpleNamespace:  # fmt: skip
    """`.nodes` and `.rels` as `export.build_frames` makes them (only the columns the derive
    reads, plus names and positions for the people index)."""
    nodes = {
        "Play": pl.DataFrame(plays_rows, schema=PLAY_SCHEMA),
        "Player": pl.DataFrame(
            [(pid, f"Player {pid.upper()}", pos) for pid, pos in players],
            schema={"player_id": pl.String, "name": pl.String, "position": pl.String},
            orient="row",
        ),
        "Coach": pl.DataFrame(
            [(c, f"Coach {c.upper()}") for c in coaches],
            schema={"coach_id": pl.String, "name": pl.String},
            orient="row",
        ),  # fmt: skip
        "Team": pl.DataFrame({"team_id": list(teams)}, schema={"team_id": pl.String}),
        "Game": pl.DataFrame(
            list(games),
            schema={
                "game_id": pl.String,
                "season": pl.Int64,
                "home_team": pl.String,
                "away_team": pl.String,
            },
            orient="row",
        ),  # fmt: skip
    }
    rels = {
        "ON_FIELD": pl.DataFrame(
            on_field_rows,
            schema={"s": pl.String, "e": pl.String, "side": pl.String, "position": pl.String},
        ),
        "PASSED": _pairs(list(passed)),
        "TARGETED": _pairs(list(targeted)),
        "HEAD_COACH": _staff(list(head), True),
        "OFF_COORD": _staff(list(off)),
        "DEF_COORD": _staff(list(dfn)),
        "ST_COORD": _staff(list(st)),
    }
    for name in ("Play", "Game"):
        if "week" not in nodes[name].columns:
            nodes[name] = nodes[name].with_columns(
                pl.col("game_id").str.split(":").list.first()
                .replace_strict(GAME_WEEKS, default=1, return_dtype=pl.Int64).alias("week")
            )  # fmt: skip
    return SimpleNamespace(nodes=nodes, rels=rels, dropped={}, notes=[])


def mini_frames() -> SimpleNamespace:
    passed = [("o1", k) for k in ("g1:1", "g1:2", "g1:4", "g1:5", "g1:8", "g1:9", "g1:10",
                                  "g1:11", "g3:1")]  # fmt: skip
    targeted = [("o2", "g1:1"), ("o2", "g1:2"), ("o2", "g1:4"), ("o2", "g1:5"), ("o2", "g1:8"),
                ("o3", "g1:9"), ("o1", "g1:10"), ("unk", "g1:11"), ("o2", "g3:1")]  # fmt: skip
    return make_frames(
        plays(), on_field(), passed, targeted,
        head=[("hc", "KC", 2024, 8, 1, 8), ("both", "DEN", 2024, 17, 1, 18)],
        off=[("oc", "KC", 2024), ("oc", "KC", 2025), ("both", "DEN", 2024), ("ghost", "KC", 2024)],
        dfn=[("dc", "KC", 2024), ("dc", "KC", 2025)],
        st=[("stc", "KC", 2024)],
        games=[("g1", 2024, "KC", "LV"), ("g2", 2024, "LV", "KC"), ("g3", 2025, "KC", "DEN")],
    )  # fmt: skip


SNAP_SCHEMA = {"p": pl.String, "team": pl.String, "season": pl.Int64, "week": pl.Int64,
               "off": pl.Int64, "def": pl.Int64, "st": pl.Int64}  # fmt: skip


def mini_snaps() -> pl.DataFrame:
    """What `derive.read_snaps` returns for the mini league: (player, team, season, week, off,
    def, st). The last two rows are the ones `derived_frames` drops (an unknown player, and a
    player on a team the graph doesn't hold)."""
    rows = [
        ("q1", "KC", 2024, 1, 60, 0, 0),
        ("q1", "KC", 2024, 2, 50, 0, 0),
        ("q1", "KC", 2024, 9, 40, 0, 0),  # after the head coach's last week (8)
        ("q1", "KC", 2025, 1, 30, 0, 0),
        ("q2", "KC", 2024, 1, 0, 80, 10),
        ("q2", "KC", 2024, 2, 0, 20, 0),
        ("q3", "KC", 2024, 1, 0, 0, 30),
        ("q4", "KC", 2024, 1, 0, 5, 0),
        ("q5", "KC", 2026, 1, 99, 0, 0),  # a season nobody coached in the staff tables
        ("n1", "DEN", 2024, 1, 100, 40, 0),
        ("n1", "DEN", 2024, 3, 0, 0, 10),
        ("n2", "DEN", 2024, 1, 0, 0, 7),
        ("ghost_p", "KC", 2024, 1, 10, 0, 0),
        ("q1", "XXX", 2024, 1, 5, 0, 0),
    ]
    return pl.DataFrame(rows, schema=SNAP_SCHEMA, orient="row")
