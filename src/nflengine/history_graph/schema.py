"""The history graph's schema (AE02, D112, D145): every node label and relationship type, its
key, and its properties with their types. The export (`export.py`) builds one frame per entry,
the bulk import's CSV headers come from here (`csv_header`), and the weekly append MERGEs on
the same keys. `schema.cypher` holds the constraints and indexes.

Who's who:
- **Season** `season`; **Team** `team_id` (the 32 canonical codes); **Player** `player_id`
  (GSIS id); **Coach** `coach_id` (a name slug, as in the weekly graph: "andy-reid").
- **Game** `game_id`; **Drive** `drive_key` = "<game_id>:<drive>" (nflverse `fixed_drive`);
  **Play** `play_key` = "<game_id>:<play_id>".

What's a Play: every curated `plays` row with a `play_type` and an offense (runs, passes,
kicks, punts, kneels, spikes and `no_play` penalty snaps); timeouts (`no_play` rows without an
offense) and the rows without a type (game start, end of a quarter, two-minute warnings)
aren't plays. Participation labels (formation, personnel,
coverage, routes, pressure) are **research data** (nflverse participation, 2018-2025, published
after each season): they're on a Play only for those seasons, never for the current one.

Who did what (pbp roles): `PASSED` (the passer on a dropback that wasn't a scramble, and on
a spike: attempts and sacks; a sack is `Play.sack`), `RUSHED` (the ball carrier: designed
runs, scrambles, kneels), `TARGETED` (the intended receiver of a pass attempt; a spike is an
attempt without one), `CAUGHT` (a completed target), `INTERCEPTED`, `SACKED {half}` (the
defender who made the sack; `half` for a shared one) and `TACKLED {kind}` (solo | assist |
with_assist). Roles are only on plays that count in the box score: never on `no_play` rows or
two-point tries, so `count()` of a role equals the box-score total (checked on every 2024
player: targets, receptions, attempts, sacks and interceptions equal; 1 carry and 2 solo
tackles of the season differ, a fake punt nflverse marks `no_play` but counts).
`ON_FIELD {side, position}` (participation) is on every snap, `no_play` too.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Neo4j bulk-import types (neo4j-admin normalises int to long)
LONG, DOUBLE, BOOL, STRING, LONGS = "long", "double", "boolean", "string", "long[]"


@dataclass(frozen=True)
class NodeSpec:
    label: str
    key: str  # unique id property (the bulk import's :ID, a uniqueness constraint)
    props: dict[str, str] = field(default_factory=dict)  # every property but the key

    def columns(self) -> list[str]:
        return [self.key, *self.props]


@dataclass(frozen=True)
class RelSpec:
    type: str
    start: str  # node label
    end: str
    props: dict[str, str] = field(default_factory=dict)

    def columns(self) -> list[str]:
        return ["s", "e", *self.props]  # s / e = the start and end nodes' keys


PLAY_PROPS: dict[str, str] = {
    # where and when
    "game_id": STRING,
    "play_id": LONG,
    "season": LONG,
    "week": LONG,
    "season_type": STRING,  # REG | POST
    "quarter": LONG,  # 5 = overtime
    "clock": STRING,  # the game clock, "15:00"
    "seconds_left": LONG,  # in the game (nflverse game_seconds_remaining)
    "order": DOUBLE,  # order in the game (nflverse order_sequence)
    "offense": STRING,  # team codes (also the OFFENSE / DEFENSE relationships);
    "defense": STRING,  # on a kickoff the offense is the receiving team (nflverse)
    "down": LONG,
    "distance": LONG,  # yards to go
    "yards_to_goal": LONG,  # 1-99 (nflverse yardline_100)
    "goal_to_go": BOOL,
    "score_diff": LONG,  # offense minus defense, before the play
    "wp": DOUBLE,  # the offense's win probability before the play (nflverse, no Vegas)
    # what happened
    "play_type": STRING,  # pass | run | punt | field_goal | extra_point | kickoff | qb_kneel |
    #                      qb_spike | no_play
    "description": STRING,
    "yards": LONG,  # yards gained
    "epa": DOUBLE,
    "success": BOOL,  # EPA > 0
    "first_down": BOOL,
    "touchdown": BOOL,
    "penalty": BOOL,
    "two_point": BOOL,  # a two-point try
    "dropback": BOOL,  # pass attempts, sacks and scrambles (nflverse `pass`)
    "pass_attempt": BOOL,  # a pass thrown, as the box score counts it: sacks out, spikes in
    "sack": BOOL,
    "scramble": BOOL,
    "complete": BOOL,  # on pass attempts
    "interception": BOOL,
    "fumble_lost": BOOL,
    "air_yards": DOUBLE,  # on pass attempts
    "yards_after_catch": DOUBLE,
    "pass_length": STRING,  # short | deep (16+ air yards), on attempts
    "pass_location": STRING,  # left | middle | right
    "run_location": STRING,  # left | middle | right
    "run_gap": STRING,  # end | tackle | guard (null on middle runs)
    "shotgun": BOOL,
    "no_huddle": BOOL,
    # FTN charting (2022 on; placeholder rows FTN didn't chart are left out: PC00)
    "ftn": BOOL,  # FTN charted the play
    "play_action": BOOL,
    "screen": BOOL,
    "rpo": BOOL,
    "motion": BOOL,
    "blitzers": LONG,
    "rushers": LONG,  # FTN pass rushers
    "blitz": BOOL,  # blitzers > 0
    "box": LONG,  # defenders in the box (FTN)
    "qb_alignment": STRING,  # shotgun | under_center | pistol
    "qb_sneak": BOOL,
    "drop": BOOL,  # the targeted receiver dropped a catchable ball
    "throw_away": BOOL,
    "out_of_pocket": BOOL,  # the QB left the pocket
    # participation (research only: 2018-2025; PC00's parser, `playcalling.participation`)
    "part": BOOL,  # participation has the play
    "formation": STRING,
    "personnel": STRING,  # "11" = 1 back, 1 tight end
    "extra_ol": BOOL,  # 6+ offensive linemen
    "def_package": STRING,  # base | nickel | dime
    "coverage": STRING,  # cover_0 | cover_1 | cover_2 | cover_2_man | cover_3 | cover_4 | ...
    "man_zone": STRING,  # man | zone
    "target_route": STRING,  # the targeted receiver's route
    "pressure": BOOL,
    "time_to_throw": DOUBLE,
    "part_box": LONG,
    "part_rushers": LONG,
}

NODES: dict[str, NodeSpec] = {
    n.label: n
    for n in (
        NodeSpec("Season", "season"),
        NodeSpec(
            "Team",
            "team_id",
            {"name": STRING, "nickname": STRING, "conference": STRING, "division": STRING},
        ),
        NodeSpec(
            "Player",
            "player_id",
            {"name": STRING, "position": STRING, "position_group": STRING},
        ),
        NodeSpec("Coach", "coach_id", {"name": STRING}),
        NodeSpec(
            "Game",
            "game_id",
            {
                "season": LONG,
                "week": LONG,
                "game_type": STRING,  # REG | WC | DIV | CON | SB
                "gameday": STRING,  # yyyy-mm-dd (US Eastern)
                "home_team": STRING,
                "away_team": STRING,
                "home_score": LONG,
                "away_score": LONG,
                "overtime": BOOL,
                "stadium": STRING,
                "roof": STRING,
                "home_coach": STRING,  # head coaches, with config/head_coach_fixes.csv
                "away_coach": STRING,
            },
        ),
        NodeSpec(
            "Drive",
            "drive_key",
            {
                "game_id": STRING,
                "season": LONG,
                "week": LONG,
                "drive": LONG,  # nflverse fixed_drive: 1, 2, ... in the game
                "team": STRING,  # the offense
                "result": STRING,  # Touchdown | Field goal | Punt | Turnover | ...
                "plays": LONG,
                "first_downs": LONG,
                "quarter": LONG,  # the quarter it started in
                "start": STRING,  # "KC 25"
                "end": STRING,
                "time_of_possession": STRING,  # "2:31"
                "scored": BOOL,
            },
        ),
        NodeSpec("Play", "play_key", PLAY_PROPS),
    )
}

RELATIONSHIPS: dict[str, RelSpec] = {
    r.type: r
    for r in (
        RelSpec("IN_SEASON", "Game", "Season"),
        RelSpec("HOME_TEAM", "Game", "Team"),
        RelSpec("AWAY_TEAM", "Game", "Team"),
        RelSpec("IN_GAME", "Drive", "Game"),
        RelSpec("IN_DRIVE", "Play", "Drive"),
        RelSpec("OFFENSE", "Play", "Team"),
        RelSpec("DEFENSE", "Play", "Team"),
        # participation (research, 2018-2025): every snap; position = the player's position on
        # the play (2023 on, FTN) or his roster position that week (2018-2022, NGS: no
        # per-play positions)
        RelSpec("ON_FIELD", "Player", "Play", {"side": STRING, "position": STRING}),
        RelSpec("PASSED", "Player", "Play"),
        RelSpec("RUSHED", "Player", "Play"),
        RelSpec("TARGETED", "Player", "Play"),
        RelSpec("CAUGHT", "Player", "Play"),
        RelSpec("INTERCEPTED", "Player", "Play"),
        RelSpec("SACKED", "Player", "Play", {"half": BOOL}),
        RelSpec("TACKLED", "Player", "Play", {"kind": STRING}),
        # rosters_weekly (2018 on, without CUT / retired / traded-away rows: the weekly graph's
        # rule); weeks = roster weeks, games = box-score games for that team
        RelSpec(
            "PLAYED_FOR",
            "Player",
            "Team",
            {
                "season": LONG,
                "weeks": LONGS,
                "first_week": LONG,
                "last_week": LONG,
                "games": LONG,
                "status_last": STRING,
            },
        ),
        RelSpec(
            "HEAD_COACH",
            "Coach",
            "Team",
            {"season": LONG, "games": LONG, "first_week": LONG, "last_week": LONG},
        ),
        # the coaching seed (config/coaching_seed.csv, D86): coordinators per season
        RelSpec("OFF_COORD", "Coach", "Team", {"season": LONG}),
        RelSpec("DEF_COORD", "Coach", "Team", {"season": LONG}),
        RelSpec("ST_COORD", "Coach", "Team", {"season": LONG}),
    )
}

ROLE_TYPES = ("PASSED", "RUSHED", "TARGETED", "CAUGHT", "INTERCEPTED", "SACKED", "TACKLED")
SEASON_TYPES = ("PLAYED_FOR", "HEAD_COACH", "OFF_COORD", "DEF_COORD", "ST_COORD")
SEED_ROLES = {"OC": "OFF_COORD", "DC": "DEF_COORD", "ST": "ST_COORD"}
NOT_ON_TEAM = ("CUT", "RET", "UFA", "RFA", "TRD", "TRC", "TRT")  # graph/tables.py's rule


def key_of(label: str) -> str:
    return NODES[label].key


def key_type(label: str) -> str:
    """The key's own type (Season's is a number; the others are text)."""
    return LONG if label == "Season" else STRING


def csv_header(name: str) -> list[str]:
    """The bulk import's header for a node label or relationship type:
    `play_key:ID(Play)`, `season:long`, `:START_ID(Player)`, `weeks:long[]` ...

    A named `:ID` column is stored as text, so a numeric key (Season's) gets an unnamed
    `:ID(Season)` column (not stored) followed by its own typed column: the CSV writer
    (`export.write_csvs`) writes that key twice."""
    if name in NODES:
        n = NODES[name]
        props = [f"{p}:{t}" for p, t in n.props.items()]
        if key_type(n.label) != STRING:
            return [f":ID({n.label})", f"{n.key}:{key_type(n.label)}", *props]
        return [f"{n.key}:ID({n.label})", *props]
    r = RELATIONSHIPS[name]
    return [f":START_ID({r.start})", f":END_ID({r.end})", *(f"{p}:{t}" for p, t in r.props.items())]
