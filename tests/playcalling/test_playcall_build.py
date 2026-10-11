"""The play-calling build (PC00): enrich, team-game sums, the as-of windows, the writers.

A small synthetic league (4 teams, 2023 and 2024, a bye, a game without FTN, PFR rows for a few
games) is written as a fake `curated/` tree under tmp_path, so `load_inputs`, `enrich_plays`,
`build_season`, `write_season` and `run_build` all run for real - never on D:, never with W&B.
A pure-Python reference (`ref_sum`) recounts the plays from the raw rows, independent of Polars.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest
from polars.testing import assert_frame_equal

import nflengine.ops.lock as lock
import nflengine.playcalling.build as B
from nflengine.paths import DataPaths
from nflengine.playcalling import labels as L

SEASON, PREV = 2024, 2023
TEAMS = ("ARI", "ATL", "BAL", "BUF")
# (away, home) per week. 2024: ATL and BAL are on a bye in week 3 (5 games each by week 6).
SCHEDULE: dict[int, dict[int, list[tuple[str, str]]]] = {
    2023: {
        1: [("ATL", "ARI"), ("BUF", "BAL")],
        2: [("BAL", "ARI"), ("BUF", "ATL")],
        3: [("BUF", "ARI"), ("BAL", "ATL")],
    },
    2024: {
        1: [("ATL", "ARI"), ("BUF", "BAL")],
        2: [("BAL", "ARI"), ("BUF", "ATL")],
        3: [("BUF", "ARI")],
        4: [("ATL", "ARI"), ("BUF", "BAL")],
        5: [("BAL", "ARI"), ("BUF", "ATL")],
        6: [("BUF", "ARI"), ("BAL", "ATL")],
    },
}
G_W1 = "2024_01_ATL_ARI"  # ARI hosts ATL: the hand-counted game
G_W2 = "2024_02_BAL_ARI"  # the game that loses its FTN rows in the FTN-missing tests

PLAY_SCHEMA: dict[str, pl.DataType] = {
    **{c: pl.Float64 for c in B.PLAY_COLUMNS},
    **{
        c: pl.String
        for c in (
            "game_id",
            "season_type",
            "game_date",
            "posteam",
            "defteam",
            "home_team",
            "play_type",
            "pass_length",
            "pass_location",
            "run_location",
            "run_gap",
            "passer_player_id",
            "rusher_player_id",
            "receiver_player_id",
            "desc",
        )  # fmt: skip
    },
    "season": pl.Int32,
    "week": pl.Int32,
}
FTN_SCHEMA: dict[str, pl.DataType] = {
    "ftn_game_id": pl.Int32, "nflverse_game_id": pl.String, "season": pl.Int32, "week": pl.Int32,
    "ftn_play_id": pl.Int32, "nflverse_play_id": pl.Int32, "starting_hash": pl.String,
    "qb_location": pl.String, "n_offense_backfield": pl.Int32, "n_defense_box": pl.Int32,
    "is_no_huddle": pl.Boolean, "is_motion": pl.Boolean, "is_play_action": pl.Boolean,
    "is_screen_pass": pl.Boolean, "is_rpo": pl.Boolean, "is_trick_play": pl.Boolean,
    "is_qb_out_of_pocket": pl.Boolean, "is_interception_worthy": pl.Boolean,
    "is_throw_away": pl.Boolean, "read_thrown": pl.String, "is_catchable_ball": pl.Boolean,
    "is_contested_ball": pl.Boolean, "is_created_reception": pl.Boolean, "is_drop": pl.Boolean,
    "is_qb_sneak": pl.Boolean, "n_blitzers": pl.Int32, "n_pass_rushers": pl.Int32,
    "is_qb_fault_sack": pl.Boolean, "date_pulled": pl.Datetime("us", "UTC"),
}  # fmt: skip
PFR_SCHEMA: dict[str, pl.DataType] = {
    "game_id": pl.String, "pfr_game_id": pl.String, "season": pl.Int32, "week": pl.Int32,
    "game_type": pl.String, "team": pl.String, "opponent": pl.String,
    "pfr_player_name": pl.String, "pfr_player_id": pl.String, "times_blitzed": pl.Float64,
}  # fmt: skip
GAMES_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int32, "week": pl.Int32, "game_id": pl.String, "game_type": pl.String,
    "result": pl.Int32,
}  # fmt: skip

DEFAULT_PLAY: dict = {
    "play_id": 0, "season_type": "REG", "game_date": None, "order_sequence": 0, "qtr": 1,
    "half_seconds_remaining": 1500, "down": 1, "ydstogo": 10, "yardline_100": 75,
    "goal_to_go": 0, "score_differential": 0, "wp": 0.5, "xpass": 0.5, "pass_oe": 0.0,
    "play_type": "pass", "pass": 1, "qb_scramble": 0, "sack": 0, "aborted_play": 0,
    "pass_length": None, "pass_location": None, "air_yards": None, "complete_pass": 0,
    "interception": 0, "run_location": None, "run_gap": None, "shotgun": 1, "no_huddle": 0,
    "yards_gained": 0, "epa": 0.0, "success": 0, "first_down": 0, "touchdown": 0,
    "passer_player_id": "QB1", "rusher_player_id": None, "receiver_player_id": None,
    "desc": "play", "two_point_attempt": 0,
}  # fmt: skip


def _run(**over) -> dict:
    return {"play_type": "run", "pass": 0, "shotgun": 0, **over}


# The menu every offense runs each game, plus extras that vary with (week, team) so teams differ.
MENU: dict[str, dict] = {
    # 25-yard completion, deep left: dropback, attempt, deep shot, explosive pass
    "P1": dict(down=1, air_yards=25, pass_length="deep", pass_location="left", complete_pass=1,
               yards_gained=30, xpass=0.6, epa=1.0, success=1),
    # incompletion on 2nd & 8 (2nd_long)
    "P2": dict(down=2, ydstogo=8, air_yards=8, pass_length="short", pass_location="middle",
               yards_gained=0, xpass=0.7, epa=-0.5, success=0),
    # sack on 3rd & 8 (3rd_long): a dropback, not an attempt
    "P3": dict(down=3, ydstogo=8, sack=1, yards_gained=-7, xpass=0.9, epa=-2.0, success=0),
    # scramble: play_type run but a dropback; run_location / run_gap present, not a designed run
    "P4": dict(play_type="run", qb_scramble=1, run_location="left", run_gap="end",
               yards_gained=6, xpass=0.5, epa=0.3, success=1),
    # designed runs: middle for 12 (explosive), left end on 2nd & 3 in the two-minute drill,
    # right guard in the red zone
    "R1": _run(run_location="middle", yards_gained=12, xpass=0.4, epa=0.8, success=1),
    "R2": _run(down=2, ydstogo=3, qtr=2, half_seconds_remaining=100, run_location="left",
               run_gap="end", yards_gained=3, xpass=0.4, epa=0.1, success=1),
    "R3": _run(yardline_100=8, goal_to_go=1, run_location="right", run_gap="guard",
               yards_gained=2, xpass=0.4, epa=-0.1, success=0),
    # extras: a short completion, a run right tackle
    "E": dict(air_yards=12, pass_length="short", pass_location="right", complete_pass=1,
              yards_gained=5, xpass=0.5, epa=0.2, success=1),
    "X": _run(run_location="right", run_gap="tackle", yards_gained=4, xpass=0.4, epa=0.0),
    # not scrimmage plays: dropped by the scrimmage filter
    "K": dict(play_type="qb_kneel", **{"pass": 0}, yards_gained=-1, shotgun=0),
    "T": dict(two_point_attempt=1, air_yards=3, pass_length="short", pass_location="left"),
    "N": dict(play_type="no_play"),
    "U": dict(play_type="punt", **{"pass": 0}, shotgun=0),
}  # fmt: skip
SCRIMMAGE_TAGS = {"P1", "P2", "P3", "P4", "R1", "R2", "R3", "E", "X"}

# FTN charting per play tag: (qb_location, box, play_action, screen, rpo, motion, blitzers, rushers)
FTN_BY_TAG = {
    "P1": ("S", 6, True, False, False, True, 0, 4),
    "P2": ("S", 7, False, True, False, True, 2, 6),
    "P3": ("S", 6, False, False, False, False, 1, 5),
    "P4": ("P", 7, False, False, False, False, 0, 4),
    "R1": ("U", 8, False, False, True, True, 0, 0),
    "R2": ("U", 9, False, False, False, False, 0, 0),
    "R3": ("P", 0, False, False, False, False, 0, 0),  # charted, but the box was not counted
    "X": ("U", 7, False, False, False, False, 0, 0),
}  # fmt: skip


def n_extra(week: int, ti: int) -> tuple[int, int]:
    """(extra short passes, extra runs) an offense adds to the menu in a week."""
    return (week + ti) % 3, (week + 2 * ti) % 2


def menu_for(week: int, team: str) -> list[str]:
    e, r = n_extra(week, TEAMS.index(team))
    return ["P1", "P2", "P3", "P4", "R1", "R2", "R3", *["E"] * e, *["X"] * r, "K", "T", "N", "U"]


def ftn_row(
    game_id: str, play_id: int, season: int, week: int, tag: str, j: int = 0, **over
) -> dict:
    """A curated ftn_plays row for a play tag (the 'E' extras alternate play-action / blitz)."""
    if tag in FTN_BY_TAG:
        qb, box, pa, screen, rpo, motion, blitzers, rushers = FTN_BY_TAG[tag]
    elif tag == "E":
        qb, box, pa, screen, rpo, motion, blitzers, rushers = (
            "S", 6, j % 2 == 0, False, False, False, j % 2, 4,
        )  # fmt: skip
    else:  # kneel, two-point try, no play, punt: FTN's placeholder row
        qb, box, pa, screen, rpo, motion, blitzers, rushers = (
            "0",
            0,
            False,
            False,
            False,
            False,
            0,
            0,
        )
    row = {c: None for c in FTN_SCHEMA}
    row.update(
        ftn_game_id=1000 + week, nflverse_game_id=game_id, season=season, week=week,
        ftn_play_id=play_id + 5000, nflverse_play_id=play_id, starting_hash="0",
        qb_location=qb, n_offense_backfield=1, n_defense_box=box, is_no_huddle=False,
        is_motion=motion, is_play_action=pa, is_screen_pass=screen, is_rpo=rpo,
        is_trick_play=False, is_qb_out_of_pocket=False, is_interception_worthy=False,
        is_throw_away=False, read_thrown="0", is_catchable_ball=False, is_contested_ball=False,
        is_created_reception=False, is_drop=False, is_qb_sneak=False, n_blitzers=blitzers,
        n_pass_rushers=rushers, is_qb_fault_sack=False,
        date_pulled=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
    )  # fmt: skip
    row.update(over)
    return row


def game_id_of(season: int, week: int, away: str, home: str) -> str:
    return f"{season}_{week:02d}_{away}_{home}"


@dataclass
class League:
    plays: list[dict]
    ftn: list[dict]
    pfr: list[dict]
    games: list[dict]

    def clone(self) -> League:
        return copy.deepcopy(self)

    def without_ftn(self, *game_ids: str) -> League:
        out = self.clone()
        out.ftn = [f for f in out.ftn if f["nflverse_game_id"] not in game_ids]
        return out


def pfr_row(game_id: str, team: str, opp: str, blitzed: float, who: str = "p1") -> dict:
    season, week = int(game_id[:4]), int(game_id[5:7])
    return {
        "game_id": game_id, "pfr_game_id": game_id.lower(), "season": season, "week": week,
        "game_type": "REG", "team": team, "opponent": opp, "pfr_player_name": who,
        "pfr_player_id": f"{team}-{who}", "times_blitzed": blitzed,
    }  # fmt: skip


def make_league() -> League:
    plays: list[dict] = []
    ftn: list[dict] = []
    games: list[dict] = []
    for season, weeks in SCHEDULE.items():
        for week, pairs in weeks.items():
            date = (dt.date(season, 9, 1) + dt.timedelta(days=7 * (week - 1))).isoformat()
            for away, home in pairs:
                gid = game_id_of(season, week, away, home)
                games.append(dict(season=season, week=week, game_id=gid, game_type="REG", result=3))
                pid = 0
                for offense, defense in ((home, away), (away, home)):
                    j = 0
                    for tag in menu_for(week, offense):
                        pid += 1
                        over = MENU[tag[0] if tag[0] in "EX" else tag]
                        p = {**DEFAULT_PLAY, **over}
                        p.update(
                            game_id=gid, play_id=pid, order_sequence=pid, season=season,
                            week=week, game_date=date, posteam=offense, defteam=defense,
                            home_team=home,
                        )  # fmt: skip
                        p["_tag"] = tag
                        plays.append(p)
                        ftn.append(ftn_row(gid, pid, season, week, tag, j))
                        j += 1 if tag == "E" else 0
    pfr = [
        pfr_row("2024_01_ATL_ARI", "ARI", "ATL", 2.0, "a"),
        pfr_row("2024_01_ATL_ARI", "ARI", "ATL", 1.0, "b"),  # two passers: summed to 3
        pfr_row("2024_01_ATL_ARI", "ATL", "ARI", 1.0, "a"),
        pfr_row("2024_01_ATL_ARI", "ATL", "ARI", 2.0, "b"),
        pfr_row("2024_01_ATL_ARI", "ARI", "BUF", 9.0, "decoy"),  # a pair that did not play
        pfr_row("2024_02_BAL_ARI", "ARI", "BAL", 2.0),  # BAL's passers have no PFR row
        pfr_row("2023_01_ATL_ARI", "ARI", "ATL", 2.0),
        pfr_row("2023_01_ATL_ARI", "ATL", "ARI", 3.0),
    ]  # fmt: skip
    return League(plays, ftn, pfr, games)


def plays_frame(rows: list[dict]) -> pl.DataFrame:
    clean = [{c: r.get(c) for c in B.PLAY_COLUMNS} for r in rows]
    floats = {c for c, t in PLAY_SCHEMA.items() if t == pl.Float64}
    fixed = [
        {c: (float(v) if v is not None and c in floats else v) for c, v in r.items()} for r in clean
    ]
    return pl.DataFrame(fixed, schema=PLAY_SCHEMA)


SUNDAYS = {2023: dt.date(2023, 9, 10), 2024: dt.date(2024, 9, 8)}


def sunday_kickoff(season: int, week: int) -> dt.datetime:
    """Sunday 1:00 pm US Eastern of a week: FTN has it well before the Tuesday cutoff."""
    day = SUNDAYS[season] + dt.timedelta(days=7 * (week - 1))
    return dt.datetime(day.year, day.month, day.day, 13, tzinfo=ZoneInfo("America/New_York"))


def write_tree(
    root: Path,
    lg: League,
    drop_columns: dict[int, list[str]] | None = None,
    kickoffs: bool = True,
) -> DataPaths:
    """A fake curated tree: plays/season=S.parquet, ftn_plays, pfr_pass, games. Every game
    kicks off Sunday afternoon (`kickoffs=False`: a games table without `kickoff_utc`)."""
    cur = root / "curated"
    (cur / "plays").mkdir(parents=True, exist_ok=True)
    for season in sorted({p["season"] for p in lg.plays}):
        df = plays_frame([p for p in lg.plays if p["season"] == season])
        df = df.drop((drop_columns or {}).get(season, []))
        df.write_parquet(cur / "plays" / f"season={season}.parquet")
    pl.DataFrame(lg.ftn, schema=FTN_SCHEMA).write_parquet(cur / "ftn_plays.parquet")
    pl.DataFrame(lg.pfr, schema=PFR_SCHEMA).write_parquet(cur / "pfr_pass.parquet")
    if kickoffs:
        games = pl.DataFrame(
            [{**g, "kickoff_utc": sunday_kickoff(g["season"], g["week"])} for g in lg.games],
            schema={**GAMES_SCHEMA, "kickoff_utc": pl.Datetime("us", "UTC")},
        )
    else:
        games = pl.DataFrame(lg.games, schema=GAMES_SCHEMA)
    games.write_parquet(cur / "games.parquet")
    return DataPaths(root)


def quiet(*_a, **_k) -> None:
    return None


@dataclass
class World:
    lg: League
    paths: DataPaths
    inp: B.Inputs
    enriched: pl.DataFrame
    sb: B.SeasonBuild


def build_world(
    root: Path, lg: League, season: int = SEASON, through_week: int | None = None
) -> World:
    paths = write_tree(root, lg)
    inp = B.load_inputs(paths, [season], history=False, log=quiet)
    enriched = B.enrich_plays(inp.plays, inp.ftn, inp.part)
    sb = B.build_season(inp, enriched, season, through_week, {})
    return World(lg, paths, inp, enriched, sb)


@pytest.fixture(scope="module")
def league() -> League:
    return make_league()


@pytest.fixture(scope="module")
def world(tmp_path_factory, league) -> World:
    return build_world(tmp_path_factory.mktemp("pc_world"), league)


# --- the pure-Python reference ------------------------------------------------------------------
def ref_rows(lg: League, season: int) -> list[dict]:
    """Each scrimmage play as a plain dict of labels, counted by hand-written rules."""
    charted = {}
    for f in lg.ftn:
        placeholder = f["qb_location"] in ("", "0", None) and not f["n_defense_box"]
        if not placeholder:
            charted[(f["nflverse_game_id"], f["nflverse_play_id"])] = f
    out = []
    for p in lg.plays:
        if p["season"] != season or p["play_type"] not in ("pass", "run") or p["two_point_attempt"]:
            continue
        f = charted.get((p["game_id"], int(p["play_id"])))
        db = p["pass"] == 1
        att = p["play_type"] == "pass" and not p["sack"]
        des = p["play_type"] == "run" and not db
        loc, gap = p["run_location"], p["run_gap"]
        if des and loc == "middle":
            direction = "middle"
        elif des and loc in ("left", "right") and gap in ("end", "tackle", "guard"):
            direction = f"{loc}_{gap}"
        else:
            direction = None
        zone = (
            f"{p['pass_length']}_{p['pass_location']}"
            if att and p["pass_length"] in ("short", "deep") and p["pass_location"]
            else None
        )
        out.append(
            dict(
                game_id=p["game_id"], week=p["week"], offense=p["posteam"], defense=p["defteam"],
                db=db, att=att, des=des, direction=direction, zone=zone, f=f,
                air=p["air_yards"] if att else None, yards=p["yards_gained"], xpass=p["xpass"],
                down=p["down"], dist=p["ydstogo"], ytg=p["yardline_100"],
                diff=p["score_differential"], qtr=p["qtr"], half=p["half_seconds_remaining"],
                wp=p["wp"], shotgun=p["shotgun"], epa=p["epa"], success=p["success"],
            )
        )  # fmt: skip
    return out


def _neutral(r: dict) -> bool:
    return r["wp"] is not None and 0.2 <= r["wp"] <= 0.8 and r["down"] <= 3 and r["half"] > 120


REF_SITS = {
    "all": lambda r: True,
    "neutral": _neutral,
    "two_minute": lambda r: r["qtr"] in (2, 4) and r["half"] <= 120,
    "red_zone": lambda r: r["ytg"] <= 20,
    "3rd_long": lambda r: r["down"] == 3 and r["dist"] >= 7,
    "2nd_short": lambda r: r["down"] == 2 and r["dist"] <= 3,
    "tied": lambda r: r["diff"] == 0,
}
# metric -> (counts?, value)
REF_METRICS = {
    "dropback_rate": (lambda r: True, lambda r: float(r["db"])),
    "proe": (lambda r: r["xpass"] is not None, lambda r: float(r["db"]) - r["xpass"]),
    "shotgun_rate": (lambda r: r["shotgun"] is not None, lambda r: float(r["shotgun"])),
    "adot": (lambda r: r["att"] and r["air"] is not None, lambda r: r["air"]),
    "deep_shot_rate": (
        lambda r: r["att"] and r["air"] is not None,
        lambda r: float(r["air"] >= 20),
    ),
    "explosive_rate": (
        lambda r: r["yards"] is not None,
        lambda r: float((r["db"] and r["yards"] >= 20) or (r["des"] and r["yards"] >= 10)),
    ),
    "epa_per_play": (lambda r: r["epa"] is not None, lambda r: r["epa"]),
    "success_rate": (lambda r: r["success"] is not None, lambda r: float(r["success"])),
    "run_middle": (
        lambda r: r["des"] and r["direction"],
        lambda r: float(r["direction"] == "middle"),
    ),
    "run_left_end": (
        lambda r: r["des"] and r["direction"],
        lambda r: float(r["direction"] == "left_end"),
    ),
    "pass_deep_left": (lambda r: r["zone"], lambda r: float(r["zone"] == "deep_left")),
    "play_action_rate": (lambda r: r["f"] and r["db"], lambda r: float(r["f"]["is_play_action"])),
    "screen_rate": (lambda r: r["f"] and r["db"], lambda r: float(r["f"]["is_screen_pass"])),
    "blitz_rate": (lambda r: r["f"] and r["db"], lambda r: float(r["f"]["n_blitzers"] > 0)),
    "rushers_avg": (
        lambda r: r["f"] and r["db"] and r["f"]["n_pass_rushers"] > 0,
        lambda r: float(r["f"]["n_pass_rushers"]),
    ),
    "motion_rate": (lambda r: r["f"], lambda r: float(r["f"]["is_motion"])),
    "rpo_rate": (lambda r: r["f"], lambda r: float(r["f"]["is_rpo"])),
    "heavy_box_rate": (
        lambda r: r["f"] and r["f"]["n_defense_box"] > 0,
        lambda r: float(r["f"]["n_defense_box"] >= 8),
    ),
    "light_box_rate": (
        lambda r: r["f"] and r["f"]["n_defense_box"] > 0,
        lambda r: float(r["f"]["n_defense_box"] <= 6),
    ),
    "qb_shotgun_share": (lambda r: r["f"], lambda r: float(r["f"]["qb_location"] == "S")),
}  # fmt: skip


def ref_sum(
    rows: list[dict],
    team: str,
    side: str,
    metric: str,
    sit: str,
    games: set[str] | None = None,
) -> tuple[float, int]:
    counts, value = REF_METRICS[metric]
    num, den = 0.0, 0
    for r in rows:
        if r["offense" if side == "offense" else "defense"] != team:
            continue
        if games is not None and r["game_id"] not in games:
            continue
        if REF_SITS[sit](r) and counts(r):
            den += 1
            num += value(r)
    return num, den


def tend_lookup(tend: pl.DataFrame) -> dict[tuple, dict]:
    return {
        (r["as_of_week"], r["team"], r["side"], r["window"], r["metric"], r["situation"]): r
        for r in tend.iter_rows(named=True)
    }


def team_games(lg: League, season: int, team: str) -> list[tuple[int, str]]:
    """(week, game_id) of a team's games in a season, in order."""
    out = [
        (g["week"], g["game_id"])
        for g in lg.games
        if g["season"] == season and team in g["game_id"][8:].split("_")
    ]
    return sorted(out)


# --- enrich -------------------------------------------------------------------------------------
def _enrich(plays: list[dict], ftn: list[dict] | None = None) -> pl.DataFrame:
    rows = [
        {**DEFAULT_PLAY, "game_id": "2024_01_ATL_ARI", "season": 2024, "week": 1,
         "game_date": "2024-09-08", "posteam": "ARI", "defteam": "ATL", "home_team": "ARI",
         "play_id": i + 1, "order_sequence": i + 1, **p}
        for i, p in enumerate(plays)
    ]  # fmt: skip
    f = pl.DataFrame(ftn or [], schema=FTN_SCHEMA).with_columns(
        pl.col("nflverse_game_id").alias("game_id"),
        pl.col("nflverse_play_id").cast(pl.Float64).alias("play_id"),
    )
    return B.enrich_plays(plays_frame(rows), f, pl.DataFrame())


def _by_id(df: pl.DataFrame, play_id: int) -> dict:
    return df.filter(pl.col("play_id") == float(play_id)).row(0, named=True)


def test_enrich_play_labels() -> None:
    e = _enrich(
        [
            dict(MENU["P1"]),  # 1: deep completion
            dict(MENU["P3"]),  # 2: sack
            dict(MENU["P4"]),  # 3: scramble
            dict(MENU["R1"]),  # 4: designed run for 12
            dict(MENU["P2"]),  # 5: incompletion
            dict(MENU["K"]),  # 6: kneel (dropped)
            dict(MENU["T"]),  # 7: two-point try (dropped)
            dict(MENU["N"]),  # 8: no play (dropped)
            dict(MENU["U"]),  # 9: punt (dropped)
        ]
    )
    assert e["play_id"].to_list() == [1.0, 2.0, 3.0, 4.0, 5.0]
    p1, sack, scr, run, inc = (_by_id(e, i) for i in (1, 2, 3, 4, 5))

    assert (p1["dropback"], p1["pass_attempt"], p1["designed_run"], p1["call"]) == (
        True, True, False, "pass",
    )  # fmt: skip
    assert p1["deep_shot"] is True and p1["explosive"] is True
    assert (p1["pass_zone"], p1["pass_length"], p1["pass_location"]) == (
        "deep_left", "deep", "left",
    )  # fmt: skip
    assert p1["complete"] is True and p1["air_yards"] == 25.0

    # a sack is a dropback but not an attempt: no air yards, no pass labels, not a deep shot
    assert (sack["dropback"], sack["pass_attempt"], sack["sack"], sack["call"]) == (
        True, False, True, "pass",
    )  # fmt: skip
    assert sack["air_yards"] is None and sack["pass_zone"] is None
    assert sack["deep_shot"] is None and sack["complete"] is None
    assert sack["explosive"] is False  # -7 yards

    # a scramble: play_type run, but a dropback and not a designed run
    assert (scr["dropback"], scr["designed_run"], scr["pass_attempt"], scr["scramble"]) == (
        True, False, False, True,
    )  # fmt: skip
    assert scr["call"] == "pass" and scr["run_direction"] == "left_end"  # the label exists ...
    assert scr["explosive"] is False  # ... and 6 yards on a dropback is not 20

    assert (run["dropback"], run["designed_run"], run["call"]) == (False, True, "run")
    assert run["run_direction"] == "middle" and run["explosive"] is True  # a run of 12 >= 10
    assert run["pass_location"] is None and run["air_yards"] is None  # run: pass fields masked

    assert inc["deep_shot"] is False and inc["complete"] is False and inc["explosive"] is False
    assert inc["down_distance"] == "2nd_long" and inc["pass_zone"] == "short_middle"


def test_enrich_masks_fields_that_do_not_apply() -> None:
    # air yards on a sack and a run location on a pass are noise: they are masked, not kept
    e = _enrich(
        [
            {**MENU["P3"], "air_yards": 14, "pass_length": "short", "pass_location": "left"},
            {**MENU["P1"], "run_location": "middle", "run_gap": "guard"},
        ]
    )
    sack, att = _by_id(e, 1), _by_id(e, 2)
    assert (sack["air_yards"], sack["pass_length"], sack["pass_location"]) == (None, None, None)
    assert (att["run_location"], att["run_gap"], att["run_direction"]) == (None, None, None)


@pytest.mark.parametrize(
    ("kind", "yards", "explosive"),
    [
        ("dropback", 20, True),
        ("dropback", 19, False),
        ("dropback", 45, True),
        ("dropback", None, None),
        ("run", 10, True),
        ("run", 9, False),
        ("run", 60, True),
        ("run", -3, False),
        ("run", None, None),
        ("scramble", 12, False),  # a scramble is a dropback: 20 yards needed
        ("scramble", 20, True),
        ("sack", -8, False),
    ],
)
def test_enrich_explosive_edges(kind: str, yards: int | None, explosive: bool | None) -> None:
    base = {
        "dropback": MENU["P1"],
        "run": MENU["R1"],
        "scramble": MENU["P4"],
        "sack": MENU["P3"],
    }[kind]
    e = _enrich([{**base, "yards_gained": yards}])
    assert e["explosive"].to_list() == [explosive]


@pytest.mark.parametrize(
    ("air", "deep"), [(19, False), (20, True), (21, True), (-2, False), (None, None)]
)
def test_enrich_deep_shot_edges(air: int | None, deep: bool | None) -> None:
    e = _enrich([{**MENU["E"], "air_yards": air}])
    assert e["deep_shot"].to_list() == [deep]


def test_enrich_situation_labels_and_flags() -> None:
    e = _enrich(
        [
            {**MENU["P1"], "wp": 0.2, "yardline_100": 21, "score_differential": -9},
            {**MENU["R2"], "wp": None},
            {**MENU["P2"], "qtr": 4, "half_seconds_remaining": 121, "score_differential": 9},
            {**MENU["P2"], "qtr": 5, "half_seconds_remaining": 100, "down": None},
        ]
    )
    a, b, c, d = (_by_id(e, i) for i in (1, 2, 3, 4))
    assert (a["neutral"], a["field_zone"], a["score_state"]) == (True, "opp_half", "trail_9plus")
    assert (a["down_distance"], a["time_bucket"], a["two_minute"]) == ("1st_down", "q1", False)
    assert (b["neutral"], b["two_minute"], b["time_bucket"]) == (False, True, "q2_2min")
    assert (b["down_distance"], b["distance_bucket"]) == ("2nd_short", "short")
    assert (c["time_bucket"], c["two_minute"], c["score_state"]) == ("q4", False, "lead_9plus")
    assert (d["time_bucket"], d["two_minute"], d["down_distance"], d["neutral"]) == (
        "ot", False, None, False,
    )  # fmt: skip
    assert e["home"].to_list() == [True] * 4  # ARI is home
    assert e["offense"].unique().to_list() == ["ARI"] and e["defense"].unique().to_list() == ["ATL"]


def test_enrich_sorts_and_keeps_one_row_per_scrimmage_play() -> None:
    rows = [dict(MENU["P1"]), dict(MENU["R1"]), dict(MENU["P2"])]
    plays = [
        {**DEFAULT_PLAY, **p, "game_id": g, "season": 2024, "week": w, "game_date": "2024-09-08",
         "posteam": "ARI", "defteam": "ATL", "home_team": "ARI", "play_id": pid,
         "order_sequence": seq}
        for p, (g, w, pid, seq) in zip(
            rows,
            [("2024_02_X_Y", 2, 1, 1), ("2024_01_X_Y", 1, 2, 9), ("2024_01_X_Y", 1, 3, 4)],
            strict=True,
        )
    ]  # fmt: skip
    e = B.enrich_plays(
        plays_frame(plays),
        pl.DataFrame(schema={**FTN_SCHEMA, "game_id": pl.String, "play_id": pl.Float64}),
        pl.DataFrame(),
    )
    assert e.select("week", "play_id").rows() == [(1, 3.0), (1, 2.0), (2, 1.0)]  # by order_sequence
    assert e["ftn_charted"].to_list() == [False] * 3
    assert e["part_charted"].to_list() == [False] * 3


def _charted(plays: list[dict], ftn_over: list[dict]) -> pl.DataFrame:
    ftn = [ftn_row("2024_01_ATL_ARI", i + 1, 2024, 1, "P1", **o) for i, o in enumerate(ftn_over)]
    return _enrich(plays, ftn)


def test_ftn_labels_map_placeholders_to_null() -> None:
    plays = [dict(MENU["P1"]) for _ in range(9)]
    over = [
        {},  # 1: charted as is: qb S, box 6
        dict(qb_location="0", n_defense_box=0),  # 2: FTN's placeholder row: not charted at all
        dict(qb_location="0", n_defense_box=7),  # 3: charted; qb location unknown -> null
        dict(qb_location="S", n_defense_box=0),  # 4: charted; box not counted -> null, not 0
        dict(qb_location=" S", n_defense_box=6),  # 5: stray space
        dict(qb_location="U", starting_hash="L", n_blitzers=0),  # 6
        dict(qb_location="P", starting_hash="R", n_blitzers=3, n_pass_rushers=8),  # 7
        dict(qb_location=None, n_defense_box=None),  # 8: null placeholder
        dict(qb_location="", n_defense_box=0, starting_hash="M"),  # 9: blank placeholder
    ]
    e = _charted(plays, over)
    r = {i: _by_id(e, i) for i in range(1, 10)}
    assert [r[i]["ftn_charted"] for i in range(1, 10)] == [
        True, False, True, True, True, True, True, False, False,
    ]  # fmt: skip
    assert (r[1]["qb_alignment"], r[1]["box"], r[1]["hash"]) == ("shotgun", 6, None)  # '0' -> null
    for i in (2, 8, 9):  # an uncharted play carries no FTN label (not "False")
        assert all(
            r[i][c] is None
            for c in ("play_action", "screen", "rpo", "motion", "blitz", "box", "qb_alignment")
        ), i
    assert r[3]["qb_alignment"] is None and r[3]["box"] == 7
    assert r[4]["qb_alignment"] == "shotgun" and r[4]["box"] is None
    assert r[5]["qb_alignment"] == "shotgun"
    assert (r[6]["qb_alignment"], r[6]["hash"]) == ("under_center", "left")
    assert (r[7]["qb_alignment"], r[7]["hash"]) == ("pistol", "right")
    assert (r[7]["blitz"], r[7]["blitzers"], r[7]["rushers"]) == (True, 3, 8)
    assert r[6]["blitz"] is False
    # FTN labels do not leak onto plays FTN never saw
    assert e.filter(pl.col("ftn_charted"))["play_action"].null_count() == 0


def test_play_missing_from_ftn_is_not_charted() -> None:
    e = _enrich(
        [dict(MENU["P1"]), dict(MENU["P2"])], [ftn_row("2024_01_ATL_ARI", 1, 2024, 1, "P1")]
    )
    assert e["ftn_charted"].to_list() == [True, False]
    assert e["play_action"].to_list() == [True, None]


def test_participation_labels_join_by_game_and_play() -> None:
    from nflengine.playcalling.participation import OUTPUT_SCHEMA

    plays = plays_frame(
        [
            {**DEFAULT_PLAY, **MENU["P1"], "game_id": "2024_01_ATL_ARI", "season": 2024,
             "week": 1, "game_date": "d", "posteam": "ARI", "defteam": "ATL", "home_team": "ARI",
             "play_id": i, "order_sequence": i}
            for i in (1, 2)
        ]
    )  # fmt: skip
    part = pl.DataFrame(
        [{"game_id": "2024_01_ATL_ARI", "play_id": 2.0, "personnel": "11", "coverage": "cover_3"}],
        schema=OUTPUT_SCHEMA,
    )
    ftn = pl.DataFrame(schema={**FTN_SCHEMA, "game_id": pl.String, "play_id": pl.Float64})
    e = B.enrich_plays(plays, ftn, part)
    assert e["part_charted"].to_list() == [False, True]
    assert e["personnel"].to_list() == [None, "11"]
    assert e["coverage"].to_list() == [None, "cover_3"]


def test_every_metric_and_situation_evaluates_on_the_enriched_columns(world) -> None:
    """No metric or situation names a column the enriched table lacks (typo guard)."""
    e = world.enriched
    for m in (*L.METRICS, L.PFR_BLITZ):
        out = e.select(m.den().alias("d"), m.num().alias("n"))
        assert out.height == e.height or out.height == 1, m.name
    for s in L.SITUATIONS:
        assert e.select(s.expr().alias("s"))["s"].dtype == pl.Boolean, s.name


# --- load_inputs --------------------------------------------------------------------------------
def test_load_inputs_reads_what_a_build_needs(tmp_path, league) -> None:
    paths = write_tree(tmp_path, league)
    inp = B.load_inputs(paths, [SEASON], history=False, log=quiet)
    scrimmage = [p for p in league.plays if p["_tag"][0] in "PRXE" and p["_tag"] != "N"]
    assert inp.plays.height == len(scrimmage)
    assert inp.plays["season"].dtype == pl.Int32 and inp.plays["week"].dtype == pl.Int32
    assert inp.plays["wp"].dtype == pl.Float64 and inp.plays["down"].dtype == pl.Float64
    assert set(inp.plays["play_type"]) == {"pass", "run"}  # no kneels, no-plays, punts
    assert inp.plays["two_point_attempt"].sum() == 0
    assert set(inp.plays["season"]) == {PREV, SEASON}  # the prior season rides along
    assert inp.ftn["play_id"].dtype == pl.Float64 and "game_id" in inp.ftn.columns
    assert inp.ftn.select("game_id", "play_id").is_unique().all()
    assert set(inp.games["season"]) == {PREV, SEASON}
    assert inp.notes == []  # history=False: no participation note
    assert inp.part.is_empty()


def test_load_inputs_sums_pfr_blitzes_per_team_game_and_renames(tmp_path, league) -> None:
    inp = B.load_inputs(write_tree(tmp_path, league), [SEASON], history=False, log=quiet)
    assert set(inp.pfr.columns) == {"game_id", "offense", "defense", "pfr_blitzed"}
    got = {(r["game_id"], r["offense"], r["defense"]): r["pfr_blitzed"] for r in inp.pfr.to_dicts()}
    assert got[("2024_01_ATL_ARI", "ARI", "ATL")] == 3.0  # 2 + 1
    assert got[("2024_01_ATL_ARI", "ATL", "ARI")] == 3.0  # 1 + 2
    assert got[("2024_01_ATL_ARI", "ARI", "BUF")] == 9.0


def test_load_inputs_keeps_the_first_duplicate_ftn_row_and_drops_other_seasons(tmp_path, league):
    lg = league.clone()
    first = next(f for f in lg.ftn if f["nflverse_game_id"] == G_W1 and f["nflverse_play_id"] == 1)
    dup = {**first, "is_play_action": not first["is_play_action"], "ftn_play_id": 99999}
    old = {**first, "season": 2020, "nflverse_game_id": "2020_01_ATL_ARI"}
    lg.ftn += [dup, old]
    inp = B.load_inputs(write_tree(tmp_path, lg), [SEASON], history=False, log=quiet)
    row = inp.ftn.filter((pl.col("game_id") == G_W1) & (pl.col("play_id") == 1.0))
    assert row.height == 1 and row["is_play_action"].item() is first["is_play_action"]
    assert 2020 not in inp.ftn["season"].to_list()


def test_load_inputs_casts_numeric_columns_across_seasons(tmp_path, league) -> None:
    """goal_to_go is f64 in one season's file and i32 in another: both read as f64."""
    paths = write_tree(tmp_path, league)
    f = paths.curated / "plays" / f"season={PREV}.parquet"
    pl.read_parquet(f).with_columns(pl.col("goal_to_go").cast(pl.Int32)).write_parquet(f)
    inp = B.load_inputs(paths, [SEASON], history=False, log=quiet)
    assert inp.plays["goal_to_go"].dtype == pl.Float64
    assert inp.plays["season"].n_unique() == 2


def test_load_inputs_fails_soft_on_optional_sources(tmp_path, league) -> None:
    paths = write_tree(tmp_path, league)
    (paths.curated / "ftn_plays.parquet").unlink()
    (paths.curated / "pfr_pass.parquet").unlink()
    logged: list[str] = []
    inp = B.load_inputs(paths, [SEASON], history=True, log=logged.append)
    assert inp.ftn.is_empty() and inp.pfr.is_empty()
    assert "ftn_plays missing: FTN metrics empty" in inp.notes
    assert "pfr_pass missing: the PFR blitz cross-check is empty" in inp.notes
    assert "participation: no rows (history metrics empty)" in inp.notes
    assert len(logged) == len(inp.notes) == 3
    enriched = B.enrich_plays(inp.plays, inp.ftn, inp.part)  # nothing charted, still builds
    assert not enriched["ftn_charted"].any()
    tg = B.team_game_sums(enriched, SEASON, inp.pfr)
    assert not {"play_action_rate", "blitz_rate", "blitz_rate_pfr"} & set(tg["metric"])
    assert "dropback_rate" in set(tg["metric"])


def test_load_inputs_notes_a_missing_plays_column_and_nulls_it(tmp_path, league) -> None:
    paths = write_tree(tmp_path, league, drop_columns={SEASON: ["xpass"]})
    inp = B.load_inputs(paths, [SEASON], history=False, log=quiet)
    assert f"season={SEASON}: plays column xpass missing (null)" in inp.notes
    assert "xpass" in inp.plays.columns
    assert inp.plays.filter(pl.col("season") == SEASON)["xpass"].null_count() == (
        inp.plays.filter(pl.col("season") == SEASON).height
    )
    enriched = B.enrich_plays(inp.plays, inp.ftn, inp.part)
    tg = B.team_game_sums(enriched, SEASON, inp.pfr)
    proe = tg.filter(pl.col("metric") == "proe")
    assert set(proe["week"]) == set()  # no xpass in 2024 -> no PROE rows that season
    assert tg.filter(pl.col("metric") == "dropback_rate").height > 0


def test_load_inputs_survives_a_missing_two_point_column(tmp_path, league) -> None:
    """The filter reads two_point_attempt: a season file without it gets the note and a null."""
    paths = write_tree(tmp_path, league, drop_columns={SEASON: ["two_point_attempt"]})
    inp = B.load_inputs(paths, [SEASON], history=False, log=quiet)
    assert f"season={SEASON}: plays column two_point_attempt missing (null)" in inp.notes


def test_load_inputs_with_no_plays_raises(tmp_path, league) -> None:
    paths = write_tree(tmp_path, league)
    with pytest.raises(FileNotFoundError, match="no curated plays for seasons"):
        B.load_inputs(paths, [2019], history=False, log=quiet)


# --- team-game sums -----------------------------------------------------------------------------
def tg_value(tg: pl.DataFrame, gid: str, side: str, team: str, metric: str, sit: str = "all"):
    r = tg.filter(
        (pl.col("game_id") == gid)
        & (pl.col("side") == side)
        & (pl.col("team") == team)
        & (pl.col("metric") == metric)
        & (pl.col("situation") == sit)
    )
    return r.row(0, named=True) if r.height else None


def test_team_game_sums_hand_counted_game(world) -> None:
    """ARI's 2024 week 1 (home v ATL): 9 scrimmage plays, 5 dropbacks, 3 attempts."""
    tg = B.team_game_sums(world.enriched, SEASON, world.inp.pfr)
    expect = {  # (metric, situation): (num, den) counted by hand from MENU (see ref rows)
        ("dropback_rate", "all"): (5, 9),
        ("shotgun_rate", "all"): (5, 9),  # the passes and the scramble
        ("no_huddle_rate", "all"): (0, 9),
        ("adot", "all"): (25 + 8 + 12, 3),
        ("deep_shot_rate", "all"): (1, 3),
        ("explosive_rate", "all"): (2, 9),  # the 30-yard completion and the 12-yard run
        ("epa_per_play", "all"): (1.0 - 0.5 - 2.0 + 0.3 + 0.8 + 0.1 - 0.1 + 0.2 + 0.0, 9),
        ("success_rate", "all"): (5, 9),
        ("play_action_rate", "all"): (2, 5),  # P1 and the first extra pass
        ("blitz_rate", "all"): (2, 5),  # P2 (2 blitzers) and the sack (1)
        ("screen_rate", "all"): (1, 5),
        ("motion_rate", "all"): (3, 9),
        ("rpo_rate", "all"): (1, 9),
        ("rushers_avg", "all"): (4 + 6 + 5 + 4 + 4, 5),
        ("heavy_box_rate", "all"): (2, 8),  # the red-zone run's box was not counted
        ("light_box_rate", "all"): (3, 8),
        ("qb_shotgun_share", "all"): (4, 9),
        ("qb_pistol_share", "all"): (2, 9),
        ("qb_under_center_share", "all"): (3, 9),
        ("run_middle", "all"): (1, 4),  # designed runs only: the scramble is not one
        ("run_left_end", "all"): (1, 4),
        ("pass_deep_left", "all"): (1, 3),
        # neutral: 8 plays (all but the two-minute run); PROE = sum(dropback - xpass)
        ("proe", "neutral"): (0.4 + 0.3 + 0.1 + 0.5 - 0.4 - 0.4 + 0.5 - 0.4, 8),
        ("dropback_rate", "neutral"): (5, 8),
        ("dropback_rate", "two_minute"): (0, 1),
        ("dropback_rate", "red_zone"): (0, 1),
        ("dropback_rate", "3rd_long"): (1, 1),
        ("dropback_rate", "2nd_long"): (1, 1),
        ("dropback_rate", "2nd_short"): (0, 1),
        ("dropback_rate", "1st_down"): (3, 6),
        ("dropback_rate", "tied"): (5, 9),
    }
    assert len(expect) > 25
    for side, team, opp in (("offense", "ARI", "ATL"), ("defense", "ATL", "ARI")):
        for (metric, sit), (num, den) in expect.items():
            r = tg_value(tg, G_W1, side, team, metric, sit)
            assert r is not None, (side, metric, sit)
            assert r["den"] == den and r["num"] == pytest.approx(num), (side, metric, sit)
            assert r["opponent"] == opp and r["week"] == 1 and r["season"] == SEASON
    # nothing counted -> no row (no 4th downs, no trailing, no 2nd_medium plays in the game)
    assert tg_value(tg, G_W1, "offense", "ARI", "dropback_rate", "4th_down") is None
    assert tg_value(tg, G_W1, "offense", "ARI", "dropback_rate", "trail_1to8") is None
    assert tg_value(tg, G_W1, "offense", "ARI", "dropback_rate", "2nd_medium") is None
    # ATL's own offense that day: e = 2 extra passes, 1 extra run -> 10 plays, 6 dropbacks
    atl = tg_value(tg, G_W1, "offense", "ATL", "dropback_rate")
    assert (atl["num"], atl["den"]) == (6, 10)


def test_dropback_rate_all_counts_every_play_of_every_team_game(world) -> None:
    """Regression: `lit(True) & lit(True)` once summed to 1 per group, not the play count."""
    tg = B.team_game_sums(world.enriched, SEASON, world.inp.pfr)
    scrimmage = ref_rows(world.lg, SEASON)
    n_games = 0
    for g in world.lg.games:
        if g["season"] != SEASON:
            continue
        away, home = g["game_id"][8:].split("_")
        for offense, defense in ((home, away), (away, home)):
            plays = sum(
                1 for r in scrimmage if r["game_id"] == g["game_id"] and r["offense"] == offense
            )
            assert plays >= 7
            off = tg_value(tg, g["game_id"], "offense", offense, "dropback_rate")
            dfn = tg_value(tg, g["game_id"], "defense", defense, "dropback_rate")
            assert off["den"] == dfn["den"] == plays, (g["game_id"], offense)
            dropbacks = sum(
                1
                for r in scrimmage
                if r["game_id"] == g["game_id"] and r["offense"] == offense and r["db"]
            )
            assert off["num"] == dfn["num"] == dropbacks
            n_games += 1
    assert n_games == 22  # 11 games in 2024, two offenses each
    assert world.enriched.filter(pl.col("season") == SEASON).height == len(scrimmage)


def test_team_game_sums_match_the_reference_for_every_team_game(world) -> None:
    sit_ok = {s.name for s in L.SITUATIONS}
    for season in (PREV, SEASON):
        tg = B.team_game_sums(world.enriched, season, world.inp.pfr)
        rows = ref_rows(world.lg, season)
        got = {
            (r["game_id"], r["side"], r["team"], r["metric"], r["situation"]): (r["num"], r["den"])
            for r in tg.iter_rows(named=True)
            if r["metric"] in REF_METRICS and r["situation"] in REF_SITS
        }
        want = {}
        for g in world.lg.games:
            if g["season"] != season:
                continue
            away, home = g["game_id"][8:].split("_")
            for team in (away, home):
                for side in ("offense", "defense"):
                    for metric in REF_METRICS:
                        allowed = {s.name for s in L.METRIC_BY_NAME[metric].situations()}
                        for sit in REF_SITS:
                            assert sit in sit_ok
                            if sit not in allowed:
                                continue
                            num, den = ref_sum(rows, team, side, metric, sit, {g["game_id"]})
                            if den:
                                want[(g["game_id"], side, team, metric, sit)] = (num, den)
        assert set(got) == set(want), (season, sorted(set(got) ^ set(want))[:5])
        assert len(want) > 1000
        for k, (num, den) in want.items():
            assert got[k][1] == den, k
            assert got[k][0] == pytest.approx(num, abs=1e-9), k


def test_metric_pairs_follow_the_eras() -> None:
    def names(season: int) -> set[str]:
        return {m.name for m, _ in B.metric_pairs(season)}

    assert "play_action_rate" not in names(2021) and "play_action_rate" in names(2022)
    assert "personnel_11" in names(2016) and "personnel_11" in names(2025)
    assert "personnel_11" not in names(2026)  # participation is research data: no 2026 row
    assert "formation_singleback" in names(2022) and "formation_singleback" not in names(2023)
    assert "formation_under_center" in names(2023) and "cov_cover_9" not in names(2022)
    assert "dropback_rate" in names(2010)
    for season in (2016, 2022, 2024, 2026):
        pairs = [(m.name, s.name) for m, s in B.metric_pairs(season)]
        assert len(pairs) == len(set(pairs))
        for m, s in B.metric_pairs(season):
            assert s in m.situations() and m.exists_in(season)
        hist = {m.name for m in L.METRICS if m.history_only}
        assert {s for n, s in pairs if n in hist} <= {"all"}
    assert len(B.metric_pairs(2024)) < len(B.metric_pairs(2022))  # fewer eras of participation


def test_team_game_sums_schema_and_empty_season(world) -> None:
    tg = B.team_game_sums(world.enriched, SEASON, world.inp.pfr)
    assert tg.schema == pl.Schema(
        {
            "season": pl.Int32,
            "week": pl.Int32,
            "game_id": pl.String,
            "team": pl.String,
            "opponent": pl.String,
            "side": pl.String,
            "metric": pl.String,
            "situation": pl.String,
            "num": pl.Float64,
            "den": pl.Int64,
        }  # fmt: skip
    )
    assert (tg["den"] > 0).all()
    assert tg.select("game_id", "team", "side", "metric", "situation").is_unique().all()
    assert set(tg["side"]) == {"offense", "defense"}
    assert set(tg["week"]) == {1, 2, 3, 4, 5, 6}
    none = B.team_game_sums(world.enriched, 2019, world.inp.pfr)
    assert none.is_empty() and none.schema == tg.schema


def test_history_metrics_have_no_rows_without_participation(world) -> None:
    tg = B.team_game_sums(world.enriched, SEASON, world.inp.pfr)
    hist = {m.name for m in L.METRICS if m.history_only}
    assert not hist & set(tg["metric"])
    assert world.sb.meta["history_metrics"] is False


def test_team_game_table_is_the_sums_as_rates(world) -> None:
    table = B.team_game_table(world.sb.tg)
    assert table.height == world.sb.tg.height
    assert list(table.columns) == [
        "season", "week", "game_id", "team", "opponent", "side", "metric", "situation", "n",
        "value", "family", "unit", "source", "history_only",
    ]  # fmt: skip
    r = table.filter(
        (pl.col("game_id") == G_W1)
        & (pl.col("team") == "ARI")
        & (pl.col("side") == "offense")
        & (pl.col("metric") == "adot")
        & (pl.col("situation") == "all")
    ).row(0, named=True)
    assert (r["n"], r["value"], r["unit"], r["source"], r["family"]) == (
        3,
        15.0,
        "mean",
        "pbp",
        "core",
    )
    assert table.equals(
        table.sort("season", "week", "game_id", "side", "team", "metric", "situation")
    )


# --- PFR blitz cross-check ----------------------------------------------------------------------
def test_pfr_blitz_joins_on_game_offense_and_defense(world) -> None:
    tg = B.team_game_sums(world.enriched, SEASON, world.inp.pfr)
    pfr = tg.filter(pl.col("metric") == "blitz_rate_pfr")
    assert set(pfr["situation"]) == {"all"}
    # 2024 week 1: ARI's passers were blitzed 3 times on 5 dropbacks, ATL's 3 times on 6
    for side, team, num, den in (
        ("offense", "ARI", 3, 5),
        ("defense", "ATL", 3, 5),  # the blitzes ATL's defense sent against ARI
        ("offense", "ATL", 3, 6),
        ("defense", "ARI", 3, 6),
    ):
        r = tg_value(pfr, G_W1, side, team, "blitz_rate_pfr")
        assert (r["num"], r["den"]) == (num, den), (side, team)
    # week 2: only ARI's passers have a PFR row; BAL as offense has none -> no row for it
    assert tg_value(pfr, G_W2, "offense", "ARI", "blitz_rate_pfr")["num"] == 2
    assert tg_value(pfr, G_W2, "offense", "ARI", "blitz_rate_pfr")["den"] == 6
    assert tg_value(pfr, G_W2, "offense", "BAL", "blitz_rate_pfr") is None
    assert tg_value(pfr, G_W2, "defense", "ARI", "blitz_rate_pfr") is None
    # the decoy (ARI v BUF in an ATL-ARI game) matches no (game, offense, defense) and is ignored
    assert pfr.filter(pl.col("game_id") == G_W1).height == 4
    # games without any PFR row have none
    assert set(pfr["game_id"]) == {G_W1, G_W2}
    # FTN's blitz and PFR's are separate metrics (FTN: 2 / 5 for ARI that day)
    ftn = tg_value(tg, G_W1, "offense", "ARI", "blitz_rate")
    assert (ftn["num"], ftn["den"]) == (2, 5)


def test_pfr_blitz_in_the_previous_season_is_kept_for_last_season(world) -> None:
    prev = world.sb.tend.filter(
        (pl.col("window") == "last_season") & (pl.col("metric") == "blitz_rate_pfr")
    )
    assert set(prev["team"]) == {"ARI", "ATL"}  # only the 2023 week 1 game had PFR rows
    ari = prev.filter((pl.col("team") == "ARI") & (pl.col("side") == "offense")).row(0, named=True)
    rows = ref_rows(world.lg, PREV)
    dropbacks, _ = ref_sum(rows, "ARI", "offense", "dropback_rate", "all", {"2023_01_ATL_ARI"})
    assert dropbacks == 5 and ari["n"] == 5 and ari["value"] == pytest.approx(2.0 / 5)


# --- team index, as-of weeks --------------------------------------------------------------------
def test_team_index_numbers_each_teams_games_in_order(world) -> None:
    idx = B.team_index(world.enriched, SEASON)
    atl = idx.filter(pl.col("team") == "ATL").sort("game_no")
    assert atl["week"].to_list() == [1, 2, 4, 5, 6]  # the week-3 bye is simply absent
    assert atl["game_no"].to_list() == [1, 2, 3, 4, 5]
    ari = idx.filter(pl.col("team") == "ARI").sort("game_no")
    assert ari["week"].to_list() == [1, 2, 3, 4, 5, 6]
    assert idx.select("team", "game_id").is_unique().all()
    assert set(idx["team"]) == set(TEAMS)


def _games(*rows: tuple[int, int, str, int | None]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"season": s, "week": w, "game_id": g, "game_type": "REG", "result": r}
            for s, w, g, r in rows
        ],
        schema=GAMES_SCHEMA,
    )


def _plays_of(*game_ids: str, season: int = 2024) -> pl.DataFrame:
    return pl.DataFrame({"season": [season] * len(game_ids), "game_id": list(game_ids)})


def test_as_of_weeks_run_one_past_the_last_complete_week() -> None:
    g = _games((2024, 1, "a", 3), (2024, 1, "b", 3), (2024, 2, "c", 3), (2024, 3, "d", 3))
    e = _plays_of("a", "b", "c", "d")
    assert B.as_of_weeks(g, e, 2024) == [1, 2, 3, 4]


def test_as_of_weeks_stop_at_an_incomplete_week() -> None:
    g = _games(
        (2024, 1, "a", 3), (2024, 2, "b", 3), (2024, 3, "c", 3), (2024, 3, "d", None),
        (2024, 4, "e", None),
    )  # fmt: skip
    # week 3 has one unplayed game: weeks 1-2 are complete, so the last as-of week is 3
    assert B.as_of_weeks(g, _plays_of("a", "b", "c", "d"), 2024) == [1, 2, 3]
    # a first week that is not complete leaves only as-of week 1
    g1 = _games((2024, 1, "a", None), (2024, 2, "b", None))
    assert B.as_of_weeks(g1, _plays_of("a", "b"), 2024) == [1]


def test_as_of_weeks_stop_when_a_completed_game_is_missing_from_the_plays() -> None:
    g = _games((2024, 1, "a", 3), (2024, 2, "b", 3), (2024, 2, "c", 3), (2024, 3, "d", 3))
    # "c" finished (it has a result) but the curated plays lag: week 2 is not complete
    assert B.as_of_weeks(g, _plays_of("a", "b", "d"), 2024) == [1, 2]
    # plays of another season do not count
    assert B.as_of_weeks(g, _plays_of("a", "b", "c", "d", season=2023), 2024) == [1]


def test_as_of_weeks_through_week_caps_the_last_as_of_week() -> None:
    g = _games(*[(2024, w, f"g{w}", 3) for w in range(1, 7)])
    e = _plays_of(*[f"g{w}" for w in range(1, 7)])
    assert B.as_of_weeks(g, e, 2024) == list(range(1, 8))
    assert B.as_of_weeks(g, e, 2024, through_week=3) == [1, 2, 3, 4]
    assert B.as_of_weeks(g, e, 2024, through_week=6) == list(range(1, 8))
    assert B.as_of_weeks(g, e, 2024, through_week=40) == list(range(1, 8))
    assert B.as_of_weeks(g, e, 2024, through_week=0) == [1]


def test_as_of_weeks_empty_when_the_season_has_no_games() -> None:
    g = _games((2023, 1, "a", 3))
    assert B.as_of_weeks(g, _plays_of("a", season=2023), 2024) == []
    assert B.as_of_weeks(g.clear(), _plays_of(), 2024) == []


def test_as_of_weeks_of_the_league(world) -> None:
    assert world.sb.weeks == [1, 2, 3, 4, 5, 6, 7]
    assert B.as_of_weeks(world.inp.games, world.enriched, PREV) == [1, 2, 3, 4]
    assert B.as_of_weeks(world.inp.games, world.enriched, SEASON, through_week=2) == [1, 2, 3]


# --- the windows --------------------------------------------------------------------------------
PROBES = [
    ("dropback_rate", "all"),
    ("dropback_rate", "neutral"),
    ("dropback_rate", "red_zone"),
    ("proe", "neutral"),
    ("play_action_rate", "all"),
    ("blitz_rate", "all"),
    ("adot", "all"),
    ("explosive_rate", "all"),
    ("rushers_avg", "all"),
    ("run_middle", "all"),
]


def test_team_tendencies_schema(world) -> None:
    t = world.sb.tend
    assert t.schema == pl.Schema(B.TEND_SCHEMA)
    assert list(t.columns) == list(B.TEND_SCHEMA)
    assert set(t["window"]) == {"season", "last4", "last_season"}
    assert set(t["side"]) == {"offense", "defense"}
    assert set(t["as_of_week"]) == set(range(1, 8)) and set(t["season"]) == {SEASON}
    assert t.select("as_of_week", "team", "side", "window", "metric", "situation").is_unique().all()
    assert (t["n"] > 0).all()
    assert t.equals(
        t.sort("as_of_week", "side", "window", "metric", "situation", "team")
    )  # fmt: skip


def test_week_one_has_only_last_season_rows(world) -> None:
    """k = 0 games: no season-to-date and no last-4 row exists as of week 1."""
    wk1 = world.sb.tend.filter(pl.col("as_of_week") == 1)
    assert set(wk1["window"]) == {"last_season"}
    assert wk1.height > 0
    # from week 2 on the season window exists
    assert {"season", "last4"} <= set(world.sb.tend.filter(pl.col("as_of_week") == 2)["window"])


def test_a_week_w_row_never_counts_week_w(world) -> None:
    lg, tend = world.lg, world.sb.tend
    for w in range(2, 8):
        for team in TEAMS:
            played = [g for wk, g in team_games(lg, SEASON, team) if wk < w]
            rows = tend.filter(
                (pl.col("as_of_week") == w)
                & (pl.col("team") == team)
                & (pl.col("window") == "season")
                & (pl.col("side") == "offense")
            )
            assert rows["games"].unique().to_list() == [len(played)], (w, team)
            # the plays counted are exactly those of the games before week w
            r = rows.filter(
                (pl.col("metric") == "dropback_rate") & (pl.col("situation") == "all")
            ).row(0, named=True)
            _, den = ref_sum(
                ref_rows(lg, SEASON), team, "offense", "dropback_rate", "all", set(played)
            )
            assert r["n"] == den, (w, team)


def test_season_window_equals_the_reference_sums(world) -> None:
    rows = ref_rows(world.lg, SEASON)
    look = tend_lookup(world.sb.tend)
    for w in range(2, 8):
        for team in TEAMS:
            games = {g for wk, g in team_games(world.lg, SEASON, team) if wk < w}
            for side in ("offense", "defense"):
                for metric, sit in PROBES:
                    num, den = ref_sum(rows, team, side, metric, sit, games)
                    r = look.get((w, team, side, "season", metric, sit))
                    if den == 0:
                        assert r is None, (w, team, side, metric, sit)
                        continue
                    assert r is not None and r["n"] == den, (w, team, side, metric, sit)
                    assert r["value"] == pytest.approx(num / den, abs=1e-9)
                    assert r["games"] == len(games)


def test_last4_is_the_teams_last_four_games_not_the_last_four_weeks(world) -> None:
    rows = ref_rows(world.lg, SEASON)
    look = tend_lookup(world.sb.tend)
    seen_short = seen_full = seen_bye = False
    for w in range(2, 8):
        for team in TEAMS:
            gl = [(wk, g) for wk, g in team_games(world.lg, SEASON, team) if wk < w]
            last4 = {g for _, g in gl[-4:]}
            for metric, sit in PROBES:
                num, den = ref_sum(rows, team, "offense", metric, sit, last4)
                r = look.get((w, team, "offense", "last4", metric, sit))
                if den == 0:
                    assert r is None
                    continue
                assert r["n"] == den and r["value"] == pytest.approx(num / den, abs=1e-9)
                assert r["games"] == min(4, len(gl)), (w, team)
            seen_short |= len(gl) < 4
            seen_full |= len(gl) > 4
            seen_bye |= team == "ATL" and w >= 5
    assert seen_short and seen_full and seen_bye


def test_last4_hand_computed_around_a_bye(world) -> None:
    """ATL (bye in week 3) as of week 7 has played weeks 1, 2, 4, 5, 6: last 4 is 2, 4, 5, 6."""
    look = tend_lookup(world.sb.tend)
    season = look[(7, "ATL", "offense", "season", "dropback_rate", "all")]
    last4 = look[(7, "ATL", "offense", "last4", "dropback_rate", "all")]
    assert (season["games"], last4["games"]) == (5, 4)
    atl = [(w, g) for w, g in team_games(world.lg, SEASON, "ATL")]
    assert [w for w, _ in atl] == [1, 2, 4, 5, 6]
    # ATL's plays per game: 7 base + extra passes (week + 1) % 3 + extra runs (week + 2) % 2
    plays = {w: 7 + (w + 1) % 3 + (w + 2) % 2 for w, _ in atl}
    assert plays == {1: 10, 2: 7, 4: 9, 5: 8, 6: 8}
    assert season["n"] == sum(plays.values()) == 42
    assert last4["n"] == plays[2] + plays[4] + plays[5] + plays[6] == 32
    dropbacks = {w: 4 + (w + 1) % 3 for w in plays}
    assert dropbacks == {1: 6, 2: 4, 4: 6, 5: 4, 6: 5}
    assert last4["value"] == pytest.approx(19 / 32)  # 4 + 6 + 4 + 5 dropbacks
    # as of week 5 ATL has 3 games (weeks 1, 2, 4): the last-4 window is the whole season so far
    s5 = look[(5, "ATL", "offense", "season", "dropback_rate", "all")]
    l5 = look[(5, "ATL", "offense", "last4", "dropback_rate", "all")]
    assert (s5["games"], l5["games"]) == (3, 3) and s5["n"] == l5["n"] == 10 + 7 + 9
    assert s5["value"] == pytest.approx(l5["value"])
    # as of week 2 ATL has one game; the bye week 3 adds nothing as of week 4
    assert look[(2, "ATL", "offense", "season", "dropback_rate", "all")]["games"] == 1
    assert look[(4, "ATL", "offense", "season", "dropback_rate", "all")]["games"] == 2
    assert look[(3, "ATL", "offense", "season", "dropback_rate", "all")]["games"] == 2


def test_last_season_is_constant_and_is_the_whole_previous_season(world) -> None:
    prev_rows = ref_rows(world.lg, PREV)
    look = tend_lookup(world.sb.tend)
    for team in TEAMS:
        games = {g for _, g in team_games(world.lg, PREV, team)}
        assert len(games) == 3
        for metric, sit in PROBES:
            num, den = ref_sum(prev_rows, team, "offense", metric, sit, games)
            per_week = [
                look.get((w, team, "offense", "last_season", metric, sit)) for w in range(1, 8)
            ]
            if den == 0:
                assert all(r is None for r in per_week)
                continue
            first = per_week[0]
            assert first["n"] == den and first["games"] == 3
            assert first["value"] == pytest.approx(num / den, abs=1e-9)
            for r in per_week:  # the same row for every as-of week
                assert {k: r[k] for k in ("n", "games", "value", "league_value", "pct")} == {
                    k: first[k] for k in ("n", "games", "value", "league_value", "pct")
                }


def test_league_value_is_pooled_and_shared_by_both_sides(world) -> None:
    t = world.sb.tend
    grp = ["as_of_week", "window", "metric", "situation"]
    for side_rows in (t.filter(pl.col("side") == "offense"), t.filter(pl.col("side") == "defense")):
        pooled = side_rows.group_by(*grp).agg(
            (pl.col("value") * pl.col("n")).sum().alias("num"),
            pl.col("n").sum().alias("den"),
            pl.col("league_value").n_unique().alias("distinct"),
            pl.col("league_value").first().alias("lv"),
        )
        assert (pooled["distinct"] == 1).all()
        want = pooled["num"] / pooled["den"]
        assert ((pooled["lv"] - want).abs() < 1e-9).all()
    # offense and defense see the same plays, so the same league value
    # (last4 windows pool a different set of plays per side: a game in only one team's last 4)
    both = t.filter(pl.col("window") != "last4")
    off = (
        both.filter(pl.col("side") == "offense").group_by(*grp).agg(pl.col("league_value").first())
    )
    dfn = (
        both.filter(pl.col("side") == "defense").group_by(*grp).agg(pl.col("league_value").first())
    )
    j = off.join(dfn, on=grp, suffix="_d")
    assert j.height == off.height == dfn.height
    assert ((j["league_value"] - j["league_value_d"]).abs() < 1e-9).all()
    assert ((t["diff"] - (t["value"] - t["league_value"])).abs() < 1e-12).all()
    # and it is the reference's pooled rate over the weeks before as-of week 4
    rows = ref_rows(world.lg, SEASON)
    for metric, sit in PROBES:
        num = den = 0
        for team in TEAMS:
            games = {g for wk, g in team_games(world.lg, SEASON, team) if wk < 4}
            n, d = ref_sum(rows, team, "offense", metric, sit, games)
            num, den = num + n, den + d
        got = t.filter(
            (pl.col("as_of_week") == 4) & (pl.col("window") == "season")
            & (pl.col("side") == "offense") & (pl.col("metric") == metric)
            & (pl.col("situation") == sit)
        )["league_value"].unique().to_list()  # fmt: skip
        assert got == [pytest.approx(num / den)], (metric, sit)


def test_percentiles_rank_teams_within_each_group(world) -> None:
    t = world.sb.tend
    grp = ["as_of_week", "side", "window", "metric", "situation"]
    assert t["pct"].min() >= 0.0 and t["pct"].max() <= 100.0
    for key, part in t.group_by(*grp):
        part = part.sort("value")
        pct = part["pct"].to_list()
        assert pct == sorted(pct), key  # monotone in the value
        vals = part["value"].to_list()
        if len(vals) == 1:
            assert pct == [50.0], key
        elif len(set(vals)) == len(vals):  # no ties: the lowest is 0, the highest 100
            assert (pct[0], pct[-1]) == (0.0, 100.0), key
        for (a, pa), (b, pb) in zip(
            zip(vals, pct, strict=True), list(zip(vals, pct, strict=True))[1:], strict=False
        ):
            assert (pa == pb) == (a == b), key  # ties share a percentile
    assert t.filter(pl.col("metric") == "dropback_rate")["pct"].min() == 0.0
    assert t.filter(pl.col("metric") == "dropback_rate")["pct"].max() == 100.0


def _tg_row(game, team, opp, side, metric, num, den, week=1) -> dict:
    return dict(
        season=2024, week=week, game_id=game, team=team, opponent=opp, side=side,
        metric=metric, situation="all", num=float(num), den=den,
    )  # fmt: skip


def test_percentile_ties_and_a_single_team_group_by_hand() -> None:
    rates = {"X": (5, 10), "Y": (5, 10), "Z": (2, 10), "W": (9, 10)}
    tg = pl.DataFrame(
        [
            *[
                _tg_row(g, t, o, "offense", "dropback_rate", *rates[t])
                for g, t, o in (
                    ("g1", "X", "Y"),
                    ("g1", "Y", "X"),
                    ("g2", "Z", "W"),
                    ("g2", "W", "Z"),
                )
            ],
            _tg_row("g1", "X", "Y", "offense", "adot", 30, 3),  # only X ever throws an attempt
        ]
    )  # fmt: skip
    idx = pl.DataFrame(
        {
            "team": ["X", "Y", "Z", "W"], "game_id": ["g1", "g1", "g2", "g2"], "week": [1] * 4,
            "game_date": ["d"] * 4, "game_no": pl.Series([1] * 4, dtype=pl.Int32),
        }
    )  # fmt: skip
    t = B.tendencies(tg, idx, [1, 2], season=2024)
    assert set(t["as_of_week"]) == {2}  # no previous season, week 1 has no rows
    db = t.filter((pl.col("metric") == "dropback_rate") & (pl.col("window") == "season"))
    pct = dict(zip(db["team"], db["pct"], strict=True))
    assert pct == {"Z": 0.0, "X": 50.0, "Y": 50.0, "W": 100.0}  # the tied pair shares a rank
    assert db["league_value"].unique().to_list() == [pytest.approx(21 / 40)]
    adot = t.filter((pl.col("metric") == "adot") & (pl.col("window") == "season"))
    assert adot["team"].to_list() == ["X"] and adot["pct"].to_list() == [50.0]
    assert adot["value"].to_list() == [10.0] and adot["league_value"].to_list() == [10.0]
    assert adot["diff"].to_list() == [0.0] and adot["games"].to_list() == [1]


def test_tendencies_without_weeks_or_games_are_empty_with_a_schema() -> None:
    idx = pl.DataFrame(schema={"team": pl.String, "game_id": pl.String, "week": pl.Int32,
                               "game_date": pl.String, "game_no": pl.Int32})  # fmt: skip
    empty_tg = pl.DataFrame(
        schema=B.team_game_sums(pl.DataFrame(schema={"season": pl.Int32}), 1).schema
    )
    assert B.tendencies(empty_tg, idx, [], season=2024).schema == pl.Schema(B.TEND_SCHEMA)
    assert B.tendencies(empty_tg, idx, [1, 2], None, season=2024).schema == pl.Schema(B.TEND_SCHEMA)
    assert B.league_table(pl.DataFrame(schema=B.TEND_SCHEMA)).schema == pl.Schema(B.LEAGUE_SCHEMA)


def test_last_season_window_keeps_only_metrics_the_new_season_has(world) -> None:
    """A 2022 NGS-era metric (formation_singleback) is not carried on a 2023 row."""
    prev = pl.DataFrame(
        [
            _tg_row("2022_01_ARI_ATL", "ARI", "ATL", "offense", "dropback_rate", 3, 6),
            _tg_row("2022_01_ARI_ATL", "ARI", "ATL", "offense", "formation_singleback", 2, 6),
            _tg_row("2022_01_ARI_ATL", "ARI", "ATL", "offense", "cov_prevent", 1, 2),
        ]
    )
    for gone in ("formation_singleback", "cov_prevent"):
        assert L.METRIC_BY_NAME[gone].exists_in(2022)
        assert not L.METRIC_BY_NAME[gone].exists_in(2023)
    sb = B.build_season(world.inp, world.enriched, PREV, tg_cache={2022: prev})
    last = sb.tend.filter(pl.col("window") == "last_season")
    assert set(last["metric"]) == {"dropback_rate"}
    assert last.select("team", "n", "games").unique().rows() == [("ARI", 6, 1)]


# --- FTN missing --------------------------------------------------------------------------------
def test_a_game_without_ftn_shrinks_n_but_not_the_rate(tmp_path, league, world) -> None:
    lg = league.without_ftn(G_W2)  # ARI v BAL, week 2, has no FTN rows yet
    w = build_world(tmp_path, lg)
    full, cut = tend_lookup(world.sb.tend), tend_lookup(w.sb.tend)
    # ATL and BUF did not play that game: every FTN row of theirs is identical
    for team in ("ATL", "BUF"):
        for win in ("season", "last4"):
            for metric in ("play_action_rate", "blitz_rate", "rushers_avg", "motion_rate"):
                k = (7, team, "offense", win, metric, "all")
                assert full[k]["n"] == cut[k]["n"] and full[k]["value"] == cut[k]["value"], k
    # ARI lost that game's FTN plays: n shrinks by its dropbacks, the rate is over the rest
    rows = ref_rows(league, SEASON)
    others = {g for _, g in team_games(league, SEASON, "ARI")} - {G_W2}
    for metric in ("play_action_rate", "blitz_rate"):
        num, den = ref_sum(rows, "ARI", "offense", metric, "all", others)
        _, den_full = ref_sum(rows, "ARI", "offense", metric, "all")
        k = (7, "ARI", "offense", "season", metric, "all")
        assert den < den_full and full[k]["n"] == den_full
        assert cut[k]["n"] == den and cut[k]["value"] == pytest.approx(num / den)
    # a non-FTN metric does not care
    k = (7, "ARI", "offense", "season", "dropback_rate", "all")
    assert full[k] == cut[k]
    # the build records the gap
    cov = w.sb.meta["coverage"]
    assert cov["games_without_ftn"] == [G_W2] and cov["ftn_join_rate"] < 1.0
    assert world.sb.meta["coverage"]["games_without_ftn"] == []
    assert world.sb.meta["coverage"]["ftn_join_rate"] == 1.0
    assert 2 not in cov["ftn_weeks"] or cov["ftn_weeks"] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]  # BUF-ATL played it too
    assert cov["games_completed"] == 11 and cov["games_in_plays"] == 11


def test_as_of_weeks_do_not_wait_for_ftn(tmp_path, league) -> None:
    w = build_world(tmp_path, league.without_ftn(*[g["game_id"] for g in league.games]))
    assert w.sb.weeks == [1, 2, 3, 4, 5, 6, 7]  # FTN is optional: the build still runs
    assert w.sb.meta["coverage"]["ftn_join_rate"] == 0.0
    assert "play_action_rate" not in set(w.sb.tend["metric"])
    assert "dropback_rate" in set(w.sb.tend["metric"])


# --- leakage: the future never changes the past --------------------------------------------------
def perturb(lg: League, from_week: int) -> League:
    """Rewrite every 2024 play of week >= from_week (labels, wp, down, FTN flags) and add plays."""
    out = lg.clone()
    new_plays, new_ftn = [], []
    for i, p in enumerate(out.plays):
        if p["season"] != SEASON or p["week"] < from_week or p["_tag"] in "KTNU":
            continue
        if i % 2 == 0:
            if p["play_type"] == "pass" and not p["sack"]:
                p.update(play_type="run", run_location="middle", air_yards=None, **{"pass": 0})
            elif p["play_type"] == "run" and not p["pass"]:
                p.update(play_type="pass", air_yards=31, pass_length="deep", pass_location="right",
                         **{"pass": 1})  # fmt: skip
        if i % 3 == 0:
            p["wp"] = 0.97
        if i % 5 == 0:
            p["down"] = 4
        p["xpass"] = 0.01 * (i % 90)
        p["yards_gained"] = 45
    for f in out.ftn:
        if f["season"] == SEASON and f["week"] >= from_week:
            f.update(is_play_action=not f["is_play_action"], n_blitzers=3, is_screen_pass=True)
    for g in out.games:
        if g["season"] != SEASON or g["week"] < from_week:
            continue
        for offense, defense in (g["game_id"][8:].split("_")[::-1], g["game_id"][8:].split("_")):
            for k in range(6):  # six extra deep passes each, with FTN rows
                pid = 900 + k + (0 if offense == g["game_id"][-3:] else 10)
                p = {**DEFAULT_PLAY, **MENU["P1"], "game_id": g["game_id"], "play_id": pid,
                     "order_sequence": pid, "season": SEASON, "week": g["week"],
                     "game_date": "2024-12-01", "posteam": offense, "defteam": defense,
                     "home_team": g["game_id"][-3:], "_tag": "P1"}  # fmt: skip
                new_plays.append(p)
                new_ftn.append(ftn_row(g["game_id"], pid, SEASON, g["week"], "P2"))
    out.plays += new_plays
    out.ftn += new_ftn
    return out


@pytest.mark.parametrize("w", [1, 2, 3, 4, 5, 6])
def test_perturbing_week_w_and_later_leaves_as_of_w_and_earlier_untouched(
    tmp_path, league, world, w
) -> None:
    pert = build_world(tmp_path, perturb(league, w))
    keep = pl.col("as_of_week") <= w
    assert_frame_equal(world.sb.tend.filter(keep), pert.sb.tend.filter(keep))
    assert_frame_equal(world.sb.league.filter(keep), pert.sb.league.filter(keep))
    if w < 7:  # the perturbation is real: as-of week w + 1 now counts week w
        later = pl.col("as_of_week") == w + 1
        a, b = world.sb.tend.filter(later), pert.sb.tend.filter(later)
        assert a.height != b.height or not a.equals(b)
        assert pert.sb.enriched.height > world.sb.enriched.height


@pytest.mark.parametrize("through", [1, 2, 3, 5])
def test_a_truncated_build_equals_the_full_build_up_to_its_last_as_of_week(
    tmp_path, league, world, through
) -> None:
    cut = build_world(tmp_path, league, through_week=through)
    assert cut.sb.weeks == list(range(1, through + 2))
    assert cut.sb.meta["through_week"] == through
    assert cut.sb.meta["as_of_weeks"] == [1, through + 1]
    assert cut.sb.meta["last_complete_week"] == through
    keep = pl.col("as_of_week") <= through + 1
    assert_frame_equal(world.sb.tend.filter(keep), cut.sb.tend)
    assert_frame_equal(world.sb.league.filter(keep), cut.sb.league)
    assert cut.sb.enriched["week"].max() == through
    assert cut.sb.meta["rows"]["plays_enriched"] == cut.sb.enriched.height


def test_the_week_w_game_counts_only_from_the_next_as_of_week(world) -> None:
    """ARI plays at home in week 3 only against BUF: the week-3 row has 2 games, week 4 has 3."""
    look = tend_lookup(world.sb.tend)
    g3 = look[(3, "ARI", "offense", "season", "dropback_rate", "all")]
    g4 = look[(4, "ARI", "offense", "season", "dropback_rate", "all")]
    assert (g3["games"], g4["games"]) == (2, 3)
    week3_plays = 7 + (3 + 0) % 3 + (3 + 0) % 2
    assert g4["n"] - g3["n"] == week3_plays == 8


def test_build_season_meta(world) -> None:
    m = world.sb.meta
    assert m["schema_version"] == B.SCHEMA_VERSION == 1
    assert (m["season"], m["through_week"], m["as_of_weeks"], m["last_complete_week"]) == (
        SEASON, None, [1, 7], 6,
    )  # fmt: skip
    assert m["rows"] == {
        "plays_enriched": world.sb.enriched.height,
        "team_game_tendencies": world.sb.tg.height,
        "team_tendencies": world.sb.tend.height,
        "league_tendencies": world.sb.league.height,
    }
    assert m["coverage"]["plays"] == world.sb.enriched.height
    assert m["coverage"]["by_week"][0]["week"] == 1 and len(m["coverage"]["by_week"]) == 6
    assert m["coverage"]["ftn_join_rate"] == 1.0 and m["coverage"]["part_join_rate"] == 0.0
    assert world.sb.enriched["season"].unique().to_list() == [SEASON]
    json.dumps(m, default=str)  # build.json material: serialisable


def test_build_season_reuses_the_cache_but_never_caches_a_cut_off_build(world) -> None:
    cache: dict[int, pl.DataFrame] = {}
    B.build_season(world.inp, world.enriched, SEASON, through_week=2, tg_cache=cache)
    assert set(cache) == {PREV}  # the previous season only
    B.build_season(world.inp, world.enriched, SEASON, tg_cache=cache)
    assert set(cache) == {PREV, SEASON}


def test_league_table_pools_the_offense_rows(world) -> None:
    lt, t = world.sb.league, world.sb.tend
    assert lt.schema == pl.Schema(B.LEAGUE_SCHEMA)
    one = lt.filter(
        (pl.col("as_of_week") == 5) & (pl.col("window") == "season")
        & (pl.col("metric") == "blitz_rate") & (pl.col("situation") == "all")
    ).row(0, named=True)  # fmt: skip
    off = t.filter(
        (pl.col("as_of_week") == 5) & (pl.col("window") == "season") & (pl.col("side") == "offense")
        & (pl.col("metric") == "blitz_rate") & (pl.col("situation") == "all")
    )  # fmt: skip
    assert one["n"] == off["n"].sum() and one["teams"] == off.height == 4
    assert one["value"] == pytest.approx((off["value"] * off["n"]).sum() / off["n"].sum())
    assert lt.select("as_of_week", "window", "metric", "situation").is_unique().all()


def test_league_trend_and_weekly_rates_for_w_and_b(world) -> None:
    trend = B.league_trend([world.sb])
    row = trend.row(0, named=True)
    assert row["season"] == SEASON and row["through_week"] == 6
    rows = ref_rows(world.lg, SEASON)

    def pooled(metric: str, sit: str, weeks: set[int] | None = None) -> float:
        num = den = 0
        for team in TEAMS:
            games = {
                g for w, g in team_games(world.lg, SEASON, team) if weeks is None or w in weeks
            }
            n, d = ref_sum(rows, team, "offense", metric, sit, games)
            num, den = num + n, den + d
        return num / den

    assert row["play_action_rate"] == pytest.approx(pooled("play_action_rate", "all"))
    assert row["proe_neutral"] == pytest.approx(pooled("proe", "neutral"))
    assert row["blitz_rate"] == pytest.approx(pooled("blitz_rate", "all"))
    by_week = B.league_by_week(world.sb)
    for wk in (1, 4, 6):
        got = by_week.filter((pl.col("week") == wk) & (pl.col("metric") == "play_action_rate"))
        assert got["value"].to_list() == [pytest.approx(pooled("play_action_rate", "all", {wk}))]


# --- coverage -----------------------------------------------------------------------------------
def test_coverage_counts(world) -> None:
    cov = B.coverage(world.enriched, world.inp.games, SEASON)
    assert cov["games_completed"] == cov["games_in_plays"] == 11
    assert cov["ftn_weeks"] == [1, 2, 3, 4, 5, 6]
    assert cov["games_without_ftn"] == []
    wk3 = next(r for r in cov["by_week"] if r["week"] == 3)
    assert (wk3["games"], wk3["ftn_games"]) == (1, 1)  # the bye week has one game
    prev = B.coverage(world.enriched, world.inp.games, PREV)
    assert prev["games_completed"] == 6 and prev["plays"] == (
        world.enriched.filter(pl.col("season") == PREV).height
    )
    # before FTN existed nothing is "missing"
    assert B.coverage(world.enriched, world.inp.games, 2019)["games_without_ftn"] == []


# --- writers ------------------------------------------------------------------------------------
class Spy:
    """Records the order of the writes `write_season` makes."""

    def __init__(self, monkeypatch) -> None:
        self.order: list[str] = []
        real_parquet, real_json = lock.write_parquet_atomic, B._atomic_json

        def parquet(df, path, **kw):
            self.order.append(Path(path).name)
            return real_parquet(df, path, **kw)

        def js(path, doc):
            self.order.append(Path(path).name)
            return real_json(path, doc)

        monkeypatch.setattr(lock, "write_parquet_atomic", parquet)
        monkeypatch.setattr(B, "_atomic_json", js)


def leftovers(folder: Path) -> list[str]:
    return sorted(
        p.name for p in folder.iterdir() if p.name.endswith(".tmp") or p.name.startswith(".")
    )


def test_write_season_writes_four_tables_then_build_json(tmp_path, world, monkeypatch) -> None:
    spy = Spy(monkeypatch)
    out = B.write_season(DataPaths(tmp_path), world.sb, {"built_at": "t", "seconds": 1.5})
    assert out == tmp_path / "playcalling" / str(SEASON)
    assert spy.order == [f"{n}.parquet" for n in B.FILES] + ["build.json"]  # build.json last
    assert sorted(p.name for p in out.iterdir()) == sorted(
        [*(f"{n}.parquet" for n in B.FILES), "build.json"]
    )
    assert leftovers(out) == []
    doc = json.loads((out / "build.json").read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1 and doc["season"] == SEASON and doc["built_at"] == "t"
    assert doc["files"] == {n: f"{n}.parquet" for n in B.FILES}
    assert doc["definitions"] == L.definitions() and doc["metrics"] == L.catalogue()
    assert doc["rows"] == world.sb.meta["rows"] and doc["as_of_weeks"] == [1, 7]
    for n in B.FILES:
        assert pl.read_parquet(out / f"{n}.parquet").height == doc["rows"][n]
    tend = pl.read_parquet(out / "team_tendencies.parquet")
    assert_frame_equal(tend, world.sb.tend)
    assert list(pl.read_parquet(out / "league_tendencies.parquet").columns) == list(B.LEAGUE_SCHEMA)
    tgt = pl.read_parquet(out / "team_game_tendencies.parquet")
    assert tgt.height == world.sb.tg.height and "value" in tgt.columns
    plays = pl.read_parquet(out / "plays_enriched.parquet")
    assert plays.height == world.enriched.filter(pl.col("season") == SEASON).height
    for c in (
        "offense",
        "defense",
        "quarter",
        "down_distance",
        "field_zone",
        "neutral",
        "dropback",
        "deep_shot",
        "play_action",
        "blitz",
        "box",
        "qb_alignment",
        "xpass",
        "ftn_charted",
    ):
        assert c in plays.columns, c  # fmt: skip


def test_an_interrupted_write_leaves_no_build_json_and_no_temp_files(
    tmp_path, world, monkeypatch
) -> None:
    paths = DataPaths(tmp_path)
    out = B.write_season(paths, world.sb)
    assert (out / "build.json").exists()
    real = lock.write_parquet_atomic
    calls = []

    def flaky(df, path, **kw):
        calls.append(Path(path).name)
        if len(calls) == 3:
            raise OSError("disk went away")
        return real(df, path, **kw)

    monkeypatch.setattr(lock, "write_parquet_atomic", flaky)
    with pytest.raises(OSError, match="disk went away"):
        B.write_season(paths, world.sb)
    assert not (out / "build.json").exists()  # the old marker went first: no half-new build
    assert leftovers(out) == []


def test_through_week_builds_go_to_their_own_folder(tmp_path, world) -> None:
    paths = DataPaths(tmp_path)
    assert B.build_dir(paths, SEASON) == tmp_path / "playcalling" / "2024"
    assert B.build_dir(paths, SEASON, 5) == tmp_path / "playcalling" / "2024" / "through_week05"
    full = B.write_season(paths, world.sb)
    before = {p.name: p.read_bytes() for p in full.iterdir() if p.is_file()}
    cut = build_world(tmp_path / "cut", world.lg, through_week=3)
    folder = B.write_season(paths, cut.sb)
    assert folder == full / "through_week03"
    assert json.loads((folder / "build.json").read_text(encoding="utf-8"))["through_week"] == 3
    after = {p.name: p.read_bytes() for p in full.iterdir() if p.is_file()}
    assert after == before  # a simulation never replaces the tables the pages read


def test_run_build_end_to_end_without_wandb(tmp_path, league, monkeypatch) -> None:
    paths = write_tree(tmp_path, league)
    logged: list[str] = []
    monkeypatch.setattr(B, "log_wandb", lambda *a, **k: pytest.fail("W&B must not be called"))
    spy = Spy(monkeypatch)
    out = B.run_build(
        SEASON,
        history=[PREV],
        use_wandb=False,
        paths=paths,
        log=lambda m, *a, **k: logged.append(str(m)),
    )
    assert out["url"] is None and [r["season"] for r in out["seasons"]] == [PREV, SEASON]
    assert "participation: no rows (history metrics empty)" in out["notes"]
    for s in (PREV, SEASON):
        folder = tmp_path / "playcalling" / str(s)
        assert sorted(p.name for p in folder.iterdir()) == sorted(
            [*(f"{n}.parquet" for n in B.FILES), "build.json"]
        )
        assert leftovers(folder) == []
        doc = json.loads((folder / "build.json").read_text(encoding="utf-8"))
        assert doc["season"] == s and doc["built_at"] and doc["seconds"] >= 0
        assert doc["notes"] == out["notes"]
    res = {r["season"]: r for r in out["seasons"]}
    assert res[SEASON]["folder"] == str(tmp_path / "playcalling" / str(SEASON))
    assert res[SEASON]["as_of_weeks"] == [1, 7] and res[PREV]["as_of_weeks"] == [1, 4]
    # the 2023 build has no 2022 plays: no last_season rows, only the season and last-4 windows
    t23 = pl.read_parquet(tmp_path / "playcalling" / "2023" / "team_tendencies.parquet")
    assert set(t23["window"]) == {"season", "last4"}
    t24 = pl.read_parquet(tmp_path / "playcalling" / "2024" / "team_tendencies.parquet")
    assert set(t24["window"]) == {"season", "last4", "last_season"}
    # every season's folder is finished (build.json) after its four tables
    tables = [n for n in spy.order if n.endswith(".parquet")]
    assert len(tables) == 8 and spy.order.count("build.json") == 2
    assert spy.order.index("build.json") == 4
    assert any(m.startswith("enriched ") for m in logged) and any("-> " in m for m in logged)
    # building twice gives the same tables
    again = B.run_build(SEASON, history=[PREV], use_wandb=False, paths=paths, log=quiet)
    assert [r["rows"] for r in again["seasons"]] == [r["rows"] for r in out["seasons"]]
    assert_frame_equal(
        pl.read_parquet(tmp_path / "playcalling" / "2024" / "team_tendencies.parquet"), t24
    )


def test_run_build_through_week_only_cuts_the_requested_season(tmp_path, league) -> None:
    paths = write_tree(tmp_path, league)
    out = B.run_build(SEASON, 2, [PREV], use_wandb=False, paths=paths, log=quiet)
    by = {r["season"]: r for r in out["seasons"]}
    assert by[SEASON]["through_week"] == 2 and by[SEASON]["as_of_weeks"] == [1, 3]
    assert by[SEASON]["folder"].endswith("through_week02")
    assert by[PREV]["through_week"] is None and by[PREV]["as_of_weeks"] == [1, 4]
    assert (tmp_path / "playcalling" / "2024" / "through_week02" / "build.json").exists()
    assert not (
        tmp_path / "playcalling" / "2024" / "build.json"
    ).exists()  # the full one: untouched
    assert (tmp_path / "playcalling" / "2023" / "build.json").exists()


def test_run_build_rejects_seasons_before_the_first(tmp_path, league) -> None:
    paths = write_tree(tmp_path, league)
    with pytest.raises(ValueError, match=r"start in 2016; got \[2015\]"):
        B.run_build(SEASON, history=[2015, SEASON], use_wandb=False, paths=paths, log=quiet)
    with pytest.raises(ValueError, match="start in 2016"):
        B.run_build(2010, use_wandb=False, paths=paths, log=quiet)
    assert not (tmp_path / "playcalling").exists()  # nothing written


def test_run_build_without_plays_raises_before_writing(tmp_path, league) -> None:
    paths = write_tree(tmp_path, league)
    with pytest.raises(FileNotFoundError, match="no curated plays"):
        B.run_build(2019, use_wandb=False, paths=paths, log=quiet)
    assert not (tmp_path / "playcalling").exists()
