"""Shared helpers for the graph tests (P05): a tiny hand-built league for the table tests
(test_graph_tables.py, test_graph_builders.py) and a fake Neo4j driver for the loader / query
tests. A plain module, not a conftest: `from conftest import` in the other test dirs must keep
finding tests/conftest.py.

No files, no Neo4j. Four teams (KC, LV, SF, DEN) play every week:
2025 week 1 and 2026 weeks 1-3 are played, 2026 week 4 is the key's week W (the data holds
scores for it, as it would in a backtest of a past week, and the builders must not show them).
The key is the Tuesday of week 4 (2026-09-29 14:00 UTC).

Cast (`gsis_id`):
- 00-KCQB: KC's QB1 (box scores, snaps, dropbacks, drafted, thrown-to links)
- 00-KCQB2: KC's backup, the main QB of the week-3 game (most dropbacks)
- 00-KCWR: KC WR, on the reserve list at week 3, box score + snaps in week 1 (both sources)
- 00-KCTE: KC TE with snaps and no box score (a blocking TE); cut at week 3
- 00-LVWR: LV WR with a box score and no snaps row
- 00-SFQB: SF QB; 00-DENDB: DEN CB with snaps and no roster rows
- 00-TRD: traded LV -> KC (roster rows for both teams, a trade row)
- 00-ROOK: on a roster only (not in `players`: name and position come from the roster)
- 00-NEW: first roster row in week 4 (invisible); 00-NONAME: snaps row, nobody knows him
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

import polars as pl

from nflengine.graph import tables as T
from nflengine.graph.tables import GraphInputs, GraphKey

UTC = dt.UTC
S, F, I64 = pl.String, pl.Float64, pl.Int64
TS = pl.Datetime("us", "UTC")

KEY = GraphKey(2026, 4, dt.datetime(2026, 9, 29, 14, 0, tzinfo=UTC))  # Tuesday of week 4
BT_KEY = dataclasses.replace(KEY, mode="backtest")


# ---- tiny frame helpers -------------------------------------------------------------------------


def frame(rows: list[dict[str, Any]], schema: dict[str, Any]) -> pl.DataFrame:
    """Rows as dicts -> a typed frame; columns a row leaves out are null."""
    for r in rows:
        extra = set(r) - set(schema)
        assert not extra, f"unknown columns {extra}"
    return pl.DataFrame([{c: r.get(c) for c in schema} for r in rows], schema=schema)


def by(df: pl.DataFrame, *keys: str) -> dict[Any, dict[str, Any]]:
    """Rows keyed by a column (a tuple when several); asserts the key is unique."""
    out: dict[Any, dict[str, Any]] = {}
    for r in df.iter_rows(named=True):
        k = tuple(r[c] for c in keys)
        k = k[0] if len(keys) == 1 else k
        assert k not in out, f"duplicate key {k!r}"
        out[k] = r
    return out


def pairs(df: pl.DataFrame) -> set[tuple[str, str]]:
    return {(r["s"], r["e"]) for r in df.iter_rows(named=True)}


# ---- schemas ------------------------------------------------------------------------------------

TEAMS_SCHEMA = {
    "team": S,
    "team_name": S,
    "team_nick": S,
    "team_conf": S,
    "team_division": S,
}
GAMES_SCHEMA = {
    "game_id": S,
    "season": pl.Int32,
    "week": pl.Int32,
    "game_type": S,
    "kickoff_utc": TS,
    "home_team": S,
    "away_team": S,
    "home_score": pl.Int32,
    "away_score": pl.Int32,
    "completed": pl.Boolean,
    "roof": S,
    "surface": S,
    "temp": pl.Int32,
    "wind": pl.Int32,
    "neutral_site": pl.Boolean,
    "div_game": pl.Int32,
    "stadium_id": S,
    "stadium": S,
    "home_coach": S,
    "away_coach": S,
    "old_game_id": S,
    "home_qb_id": S,
    "away_qb_id": S,
    "home_qb_name": S,
    "away_qb_name": S,
}
PLAYERS_SCHEMA = {
    "gsis_id": S,
    "pfr_id": S,
    "display_name": S,
    "position": S,
    "position_group": S,
    "birth_date": pl.Date,
    "college_name": S,
    "rookie_season": pl.Int32,
    "draft_year": pl.Int32,
}
ROSTERS_SCHEMA = {
    "gsis_id": S,
    "team": S,
    "season": pl.Int32,
    "week": pl.Int32,
    "status": S,
    "full_name": S,
    "position": S,
}
PG_SCHEMA = {
    "player_id": S,
    "game_id": S,
    "team": S,
    "targets": F,
    "receptions": F,
    "receiving_yards": F,
    "receiving_tds": F,
    "carries": F,
    "rushing_yards": F,
    "rushing_tds": F,
    "attempts": F,
    "completions": F,
    "passing_yards": F,
    "passing_tds": F,
    "passing_interceptions": F,
    "passing_epa": F,
    "rushing_epa": F,
    "receiving_epa": F,
    "target_share": F,
    "def_sacks": F,
    "def_qb_hits": F,
    "def_tackles_solo": F,
    "def_tackle_assists": F,
    "def_interceptions": F,
    "def_pass_defended": F,
}
SNAPS_SCHEMA = {
    "gsis_id": S,
    "game_id": S,
    "team": S,
    "offense_snaps": F,
    "defense_snaps": F,
    "st_snaps": F,
    "offense_pct": F,
    "defense_pct": F,
}
PFR_SCHEMA = {"gsis_id": S, "game_id": S, "def_pressures": F}
NGS_SCHEMAS = {
    "receiving": {"player_gsis_id": S, "season": pl.Int32, "week": pl.Int32, "avg_separation": F},
    "passing": {
        "player_gsis_id": S,
        "season": pl.Int32,
        "week": pl.Int32,
        "avg_time_to_throw": F,
    },
    "rushing": {
        "player_gsis_id": S,
        "season": pl.Int32,
        "week": pl.Int32,
        "rush_yards_over_expected": F,
    },
}
PLAYS_SCHEMA = {
    "season": pl.Int32,
    "week": pl.Int32,
    "game_id": S,
    "play_id": F,
    "season_type": S,
    "posteam": S,
    "defteam": S,
    "play_type": S,
    "pass": F,
    "rush": F,
    "epa": F,
    "success": F,
    "qb_dropback": F,
    "qb_scramble": F,
    "two_point_attempt": F,
    "pass_attempt": F,
    "sack": F,
    "complete_pass": F,
    "pass_touchdown": F,
    "receiving_yards": F,
    "passer_player_id": S,
    "receiver_player_id": S,
    "passer_id": S,
    "qb_epa": F,
}
TEAM_GAMES_SCHEMA = {"game_id": S, "team": S, "penalties": F, "penalty_yards": F}
OFFICIALS_SCHEMA = {"game_id": S, "official_id": I64, "official_name": S, "position": S}
INJURIES_SCHEMA = {
    "gsis_id": S,
    "season": pl.Int32,
    "week": pl.Int32,
    "team": S,
    "report_status": S,
    "practice_status": S,
    "report_primary_injury": S,
    "date_modified": TS,
}
DEPTH_SCHEMA = {
    "gsis_id": S,
    "season": pl.Int32,
    "week": pl.Int32,
    "team": S,
    "position": S,
    "formation": S,
    "depth_rank": pl.Int32,
    "snap_date": pl.Date,
}
DRAFT_SCHEMA = {"gsis_id": S, "season": pl.Int32, "team": S, "round": pl.Int32, "pick": pl.Int32}
TRADES_SCHEMA = {
    "pfr_id": S,
    "pick_season": pl.Int32,
    "trade_date": pl.Date,
    "season": pl.Int32,
    "received": S,
    "gave": S,
}
VENUES_SCHEMA = {"game_id": S, "stadium_id": S}
STADIUMS_SCHEMA = {"stadium_id": S, "name": S}
EXPECTED_QB_SCHEMA = {"game_id": S, "home_qb_expected": S, "away_qb_expected": S}
TEAM_WEEKS_SCHEMA = {
    "season": pl.Int32,
    "week": pl.Int32,
    "team": S,
    "off_epa": F,
    "def_epa": F,
    "net_epa": F,
    "off_pass_epa": F,
    "off_rush_epa": F,
    "def_pass_epa": F,
    "def_rush_epa": F,
    "elo": F,
    "trend_delta": F,
    "direction": S,
    "perf_vs_expected": F,
    "net_epa_prev": F,
}
PREDICTIONS_SCHEMA = {
    "game_id": S,
    "model_version": S,
    "variant": S,
    "is_primary": pl.Boolean,
    "home_win_prob": F,
    "expected_margin": F,
    "pred_home_points": F,
    "pred_away_points": F,
    "confidence_label": S,
    "created_at": TS,
}
PUBLISHED_SCHEMA = {
    "season": pl.Int32,
    "week": pl.Int32,
    "insight_id": S,
    "insight_type": S,
    "entities": pl.List(S),
    "published_at": TS,
}

# ---- the league ---------------------------------------------------------------------------------

AFC_WEST = {"KC", "LV", "DEN"}
STADIUM = {"KC": "KAN00", "LV": "LAS00", "SF": "SFO00", "DEN": "DEN00"}
COACH = {"KC": "Andy Reid", "LV": "Rob Roe", "SF": "Kyle Shan", "DEN": "Sean Pay"}
# (season, week, away, home, home score, away score); every game is "completed" in the data
GAME_SPECS = [
    (2025, 1, "LV", "KC", 27, 20),
    (2025, 1, "DEN", "SF", 17, 24),
    (2026, 1, "LV", "KC", 24, 17),
    (2026, 1, "DEN", "SF", 20, 23),
    (2026, 2, "SF", "KC", 31, 28),
    (2026, 2, "DEN", "LV", 13, 16),
    (2026, 3, "DEN", "KC", 20, 20),  # a tie
    (2026, 3, "LV", "SF", 10, 17),
    (2026, 4, "SF", "KC", 30, 10),  # week W: the data has a result, the graph must not
    (2026, 4, "LV", "DEN", 9, 6),
]


def game_row(season, week, away, home, hs, as_, completed=True) -> dict[str, Any]:
    day = 10 if season == 2026 else 4
    kickoff = dt.datetime(season, 9, day, 17, tzinfo=UTC) + dt.timedelta(days=7 * (week - 1))
    both_afc = {home, away} <= AFC_WEST
    return {
        "game_id": f"{season}_{week:02d}_{away}_{home}",
        "season": season,
        "week": week,
        "game_type": "REG",
        "kickoff_utc": kickoff,
        "home_team": home,
        "away_team": away,
        "home_score": hs,
        "away_score": as_,
        "completed": completed,
        "roof": "dome" if home == "LV" else "outdoors",
        "surface": "grass" if (home == "KC" and season == 2025) else "turf",
        "temp": 70,
        "wind": 5,
        "neutral_site": True if (season, week, home) == (2025, 1, "SF") else None,
        "div_game": 1 if both_afc else (0 if home == "SF" else None),
        "stadium_id": STADIUM[home],
        "stadium": "Allegiant Stadium" if home == "LV" else None,
        "home_coach": COACH[home],
        "away_coach": COACH[away],
        "old_game_id": f"OLD{season}{week:02d}{home}",
        "home_qb_id": f"00-{home}QB",
        "away_qb_id": f"00-{away}QB",
        "home_qb_name": f"Name {home}",
        "away_qb_name": f"Name {away}",
    }


def games_frame(extra: list[dict[str, Any]] | None = None) -> pl.DataFrame:
    rows = [game_row(*spec) for spec in GAME_SPECS] + (extra or [])
    return frame(rows, GAMES_SCHEMA)


def play(game_id, posteam, defteam, kind, epa, *, success=None, passer=None, receiver=None, yds=0):
    """One play row. kind: pass (a completion), incomplete, sack, scramble, run, two_pt, no_play."""
    season, week = int(game_id[:4]), int(game_id[5:7])
    base = {
        "season": season,
        "week": week,
        "game_id": game_id,
        "play_id": 0.0,
        "season_type": "REG",
        "posteam": posteam,
        "defteam": defteam,
        "epa": epa,
        "success": float(epa > 0) if success is None else float(success),
        "two_point_attempt": 0.0,
        "sack": 0.0,
        "qb_scramble": 0.0,
        "complete_pass": 0.0,
        "pass_touchdown": 0.0,
        "receiving_yards": 0.0,
    }
    if kind in ("pass", "incomplete", "two_pt", "no_play"):
        base |= {
            "play_type": "no_play" if kind == "no_play" else "pass",
            "pass": 1.0,
            "rush": 0.0,
            "qb_dropback": 1.0,
            "pass_attempt": 1.0,
            "passer_player_id": passer,
            "passer_id": passer,
            "receiver_player_id": receiver,
            "qb_epa": epa,
        }
        if kind == "two_pt":
            base["two_point_attempt"] = 1.0
        if kind == "pass":
            base |= {"complete_pass": 1.0, "receiving_yards": float(yds)}
    elif kind == "sack":
        base |= {
            "play_type": "pass",
            "pass": 1.0,
            "rush": 0.0,
            "qb_dropback": 1.0,
            "pass_attempt": 0.0,
            "sack": 1.0,
            "passer_player_id": passer,
            "passer_id": passer,
            "qb_epa": epa,
        }
    elif kind == "scramble":
        base |= {
            "play_type": "run",
            "pass": 0.0,
            "rush": 1.0,
            "qb_dropback": 1.0,
            "qb_scramble": 1.0,
            "pass_attempt": 0.0,
            "passer_id": passer,
            "qb_epa": epa,
        }
    elif kind == "run":
        base |= {
            "play_type": "run",
            "pass": 0.0,
            "rush": 1.0,
            "qb_dropback": 0.0,
            "pass_attempt": 0.0,
        }
    else:
        raise ValueError(kind)
    return base


G1, G2, G3 = "2026_01_LV_KC", "2026_02_SF_KC", "2026_03_DEN_KC"
G25 = "2025_01_LV_KC"
GSF1 = "2026_01_DEN_SF"
GW = "2026_04_SF_KC"  # week W: KC hosts SF
Q, Q2, WR, TE = "00-KCQB", "00-KCQB2", "00-KCWR", "00-KCTE"


def plays_frame() -> pl.DataFrame:
    rows = [
        # 2025 week 1: KC offense only (2 targets to the WR, one caught)
        play(G25, "KC", "LV", "pass", 0.4, passer=Q, receiver=WR, yds=10),
        play(G25, "KC", "LV", "incomplete", -0.4, passer=Q, receiver=WR),
        play(G25, "LV", "KC", "run", 0.0),
        # 2026 week 1, KC offense: 5 dropbacks (1 scramble), plus a rush, a 2-pt try, a no-play
        play(G1, "KC", "LV", "pass", 0.5, passer=Q, receiver=WR, yds=15),
        play(G1, "KC", "LV", "pass", 2.0, passer=Q, receiver=WR, yds=30) | {"pass_touchdown": 1.0},
        play(G1, "KC", "LV", "incomplete", -0.5, passer=Q, receiver=TE),
        play(G1, "KC", "LV", "sack", -1.0, passer=Q),
        play(G1, "KC", "LV", "scramble", 0.3, passer=Q),
        play(G1, "KC", "LV", "run", 0.2),
        play(G1, "KC", "LV", "two_pt", 0.9, passer=Q, receiver=WR),
        play(G1, "KC", "LV", "no_play", 0.8, passer=Q, receiver=WR),
        play(G1, "LV", "KC", "run", -0.1, success=0),
        play(G1, "LV", "KC", "run", -0.3, success=1),
        # SF throws to somebody nobody knows
        play(GSF1, "SF", "DEN", "pass", 0.7, passer="00-SFQB", receiver="00-NOBODY", yds=9),
        play(GSF1, "DEN", "SF", "run", 0.1),
        # 2026 week 2: Q has 3 dropbacks, the backup 1 -> Q is the main QB
        play(G2, "KC", "SF", "pass", 0.6, passer=Q, receiver=TE, yds=12),
        play(G2, "KC", "SF", "incomplete", -0.4, passer=Q, receiver=TE),
        play(G2, "KC", "SF", "scramble", 0.1, passer=Q),
        play(G2, "KC", "SF", "pass", 0.1, passer=Q2, receiver=TE, yds=5),
        play(G2, "SF", "KC", "run", 0.0),
        # 2026 week 3: Q has 1 dropback, the backup 3 -> the backup is the main QB
        play(G3, "KC", "DEN", "pass", 0.2, passer=Q, receiver=WR, yds=8),
        play(G3, "KC", "DEN", "pass", 1.2, passer=Q2, receiver=WR, yds=20)
        | {"pass_touchdown": 1.0},
        play(G3, "KC", "DEN", "pass", 0.3, passer=Q2, receiver=WR, yds=5),
        play(G3, "KC", "DEN", "scramble", 0.1, passer=Q2),
        play(G3, "DEN", "KC", "run", 0.0),
    ]
    return frame(rows, PLAYS_SCHEMA)


def box_rows() -> list[dict[str, Any]]:
    def pg(pid, game, team, **kw):
        return {"player_id": pid, "game_id": game, "team": team} | kw

    qb = {"attempts": 30.0, "completions": 20.0, "passing_yards": 250.0, "passing_tds": 2.0}
    qb |= {"passing_interceptions": 1.0, "passing_epa": 5.5}
    return [
        pg(Q, G25, "KC", **qb),
        pg(Q, G1, "KC", **qb),
        pg(Q, G2, "KC", **qb),
        pg(Q, G3, "KC", **qb),
        pg(Q, GW, "KC", **qb),
        pg(Q2, G2, "KC", attempts=1.0, completions=1.0, passing_yards=5.0, passing_epa=0.1),
        pg(Q2, G3, "KC", attempts=2.0, completions=2.0, passing_yards=25.0, passing_epa=1.5),
        pg(Q2, GW, "KC", attempts=9.0),
        pg(
            WR,
            G1,
            "KC",
            targets=8.0,
            receptions=5.0,
            receiving_yards=60.0,
            receiving_tds=1.0,
            receiving_epa=2.0,
            target_share=0.25,
        ),
        pg(
            WR,
            G3,
            "KC",
            targets=4.0,
            receptions=3.0,
            receiving_yards=33.0,
            receiving_tds=0.0,
            receiving_epa=1.0,
            rushing_epa=0.5,
            carries=1.0,
            rushing_yards=3.0,
        ),
        pg(WR, GW, "KC", targets=99.0, receiving_yards=999.0, receiving_epa=9.0),
        pg("00-LVWR", G1, "LV", targets=6.0, receptions=4.0, receiving_yards=50.0),
        pg("00-LVWR", "2026_02_DEN_LV", "LV", targets=7.0),
        pg("00-LVWR", "2026_03_LV_SF", "LV", targets=3.0),
        pg("00-LVWR", "2026_04_LV_DEN", "LV", targets=50.0),
        pg("00-SFQB", GSF1, "SF", attempts=20.0),
        pg("00-SFQB", G2, "SF", attempts=25.0),
        pg("00-SFQB", "2026_03_LV_SF", "SF", attempts=28.0),
        pg(
            "00-DENDB",
            GSF1,
            "DEN",
            def_sacks=1.0,
            def_qb_hits=2.0,
            def_tackles_solo=3.0,
            def_tackle_assists=2.0,
            def_interceptions=0.0,
            def_pass_defended=1.0,
        ),
        pg("00-TRD", G1, "LV", targets=2.0),
        pg("00-TRD", "2026_02_DEN_LV", "LV", targets=1.0),
        pg("00-TRD", G3, "KC", targets=5.0),
        {"player_id": None, "game_id": G1, "team": "KC", "targets": 1.0},
    ]


def snaps_rows() -> list[dict[str, Any]]:
    def sn(pid, game, team, **kw):
        return {"gsis_id": pid, "game_id": game, "team": team} | kw

    return [
        sn(Q, G1, "KC", offense_snaps=60.0, offense_pct=1.0),
        sn(WR, G1, "KC", offense_snaps=55.0, offense_pct=0.8, st_snaps=0.0),
        # a blocking TE: snaps, no box score; the first row is the real one, the second a dup
        sn(TE, G1, "KC", offense_snaps=40.0, offense_pct=0.6, st_snaps=5.0),
        sn(TE, G1, "KC", offense_snaps=2.0, offense_pct=0.03, st_snaps=0.0),
        sn(TE, G2, "KC", offense_snaps=20.0, offense_pct=0.3),
        sn(TE, GW, "KC", offense_snaps=33.0, offense_pct=0.5),
        sn("00-DENDB", GSF1, "DEN", defense_snaps=60.0, defense_pct=0.9),
        sn("00-DENDB", "2026_02_DEN_LV", "DEN", defense_snaps=50.0, defense_pct=0.75),
        sn("00-DENDB", G3, "DEN", defense_snaps=40.0, defense_pct=0.6, st_snaps=8.0),
        sn("00-NONAME", G1, "LV", offense_snaps=10.0, offense_pct=0.15),
        sn(None, G1, "KC", offense_snaps=5.0),
    ]


def rosters_frame() -> pl.DataFrame:
    def r(pid, team, week, status="ACT", season=2026, name=None, pos=None):
        return {
            "gsis_id": pid,
            "team": team,
            "season": season,
            "week": week,
            "status": status,
            "full_name": name,
            "position": pos,
        }

    rows = [
        r(Q, "KC", 1, season=2025),
        *[r(Q, "KC", w) for w in (1, 2, 3, 4)],
        *[r(Q2, "KC", w) for w in (2, 3)],
        r(WR, "KC", 1),
        r(WR, "KC", 2),
        r(WR, "KC", 2),  # a duplicate week
        r(WR, "KC", 3, "RES"),
        r(WR, "KC", 4),  # week W: invisible
        r(TE, "KC", 1),
        r(TE, "KC", 2),
        r(TE, "KC", 3, "CUT"),
        r(TE, "KC", 4, "CUT"),
        *[r("00-LVWR", "LV", w) for w in (1, 2, 3)],
        r("00-SFQB", "SF", 1),
        r("00-SFQB", "SF", 2),
        r("00-SFQB", "SF", 3) | {"team": None},
        r("00-TRD", "LV", 1),
        r("00-TRD", "LV", 2),
        r("00-TRD", "LV", 3, "TRD"),
        r("00-TRD", "KC", 3),
        r("00-ROOK", "KC", 3, name="Rory Rook", pos="RB"),
        r("00-NEW", "KC", 4, name="Newt New", pos="WR"),
        r(None, "KC", 1),
    ]
    return frame(rows, ROSTERS_SCHEMA)


def players_frame() -> pl.DataFrame:
    def p(gid, pfr, name, pos, group, **kw):
        return {
            "gsis_id": gid,
            "pfr_id": pfr,
            "display_name": name,
            "position": pos,
            "position_group": group,
            "birth_date": dt.date(1999, 5, 17),
            "college_name": "State U",
            "rookie_season": 2020,
            "draft_year": 2020,
        } | kw

    return frame(
        [
            p(Q, "KayQu00", "Quinn Kay", "QB", "QB"),
            p(Q2, "BackBe00", "Ben Backup", "QB", "QB", rookie_season=2025),
            p(WR, "KaneWa00", "Walt Kane", "WR", "WR"),
            p(TE, "BlocTo00", "Tom Block", "TE", "TE"),
            p("00-LVWR", "VanceLe00", "Lee Vance", "WR", "WR"),
            p("00-SFQB", "FoxSa00", "Sam Fox", "QB", "QB"),
            p("00-DENDB", "BankDe00", "Dee Banks", "CB", "DB"),
            p("00-TRD", "DadeTr00", "Tray Dade", "WR", None),  # position group derived
            p("00-PICK", "PickDr00", "Dre Pick", "LB", "LB"),
        ],
        PLAYERS_SCHEMA,
    )


def injuries_frame() -> pl.DataFrame:
    def inj(pid, week, team, status, mod, practice=None, body=None, season=2026):
        return {
            "gsis_id": pid,
            "season": season,
            "week": week,
            "team": team,
            "report_status": status,
            "practice_status": practice,
            "report_primary_injury": body,
            "date_modified": mod,
        }

    def at(day, hour=12, month=9):
        return dt.datetime(2026, month, day, hour, tzinfo=UTC)

    rows = [
        inj(WR, 2, "KC", "Questionable", at(17), "Limited Participation", "Knee"),
        inj(WR, 2, "KC", "Out", at(18), "Did Not Participate", "Knee"),  # same player and game
        inj("00-LVWR", 1, "LV", "Out", None, body="Hamstring"),  # earlier week, undated
        inj(Q, 4, "KC", "Questionable", at(28, 20), "Limited Participation", "Ankle"),
        inj("00-SFQB", 4, "SF", "Out", at(30, 9)),  # after the run time
        inj("00-LVWR", 4, "LV", "Doubtful", None, body="Back"),  # week W, undated
        inj("00-DENDB", 4, "DEN", "Questionable", KEY.run_time),  # exactly at the run time
        inj(Q, 5, "KC", "Out", at(20)),  # a later week
        inj(None, 2, "KC", "Out", at(18)),
        inj(TE, 2, "XXX", "Out", at(18)),  # no such team game
    ]
    return frame(rows, INJURIES_SCHEMA)


def depth_frame() -> pl.DataFrame:
    def d(pid, team, week, pos, rank, snap=None, formation="Offense"):
        return {
            "gsis_id": pid,
            "season": 2026,
            "week": week,
            "team": team,
            "position": pos,
            "formation": formation,
            "depth_rank": rank,
            "snap_date": snap,
        }

    day = lambda n: dt.date(2026, 9, n)  # noqa: E731
    rows = [
        # KC week 3 (weekly feed, undated)
        d(Q, "KC", 3, "QB", 1),
        d(Q2, "KC", 3, "QB", 2),
        d(WR, "KC", 3, "WR", 1),
        d(WR, "KC", 3, "WR", 2),  # same player and slot: the better rank stays
        d(TE, "KC", 3, "KR", 1),  # special teams slots
        d(TE, "KC", 3, "LS", 1),
        d(WR, "KC", 3, "WR", 3, formation="Special Teams"),
        d(TE, "KC", 3, "", 1),
        d(TE, "KC", 3, None, 1),
        d(None, "KC", 3, "TE", 1),
        # DEN week 3: two daily snapshots, the latest wins
        d("00-ROOK", "DEN", 3, "WR", 1, day(20)),
        d("00-DENDB", "DEN", 3, "CB", 2, day(20)),
        d("00-DENDB", "DEN", 3, "CB", 1, day(22)),
        # KC week W: snapshots on the 28th, the run date (29th) and after it (30th)
        d(Q, "KC", 4, "QB", 1, day(28)),
        d(WR, "KC", 4, "WR", 1, day(28)),
        d(Q2, "KC", 4, "QB", 1, day(29)),
        d(Q, "KC", 4, "QB", 2, day(29)),
        d(WR, "KC", 4, "WR", 1, day(30)),
        # SF week W: undated
        d("00-SFQB", "SF", 4, "QB", 1),
        # KC week 5: after W, dated before the run
        d(Q, "KC", 5, "QB", 1, day(20)),
    ]
    return frame(rows, DEPTH_SCHEMA)


def trades_frame() -> pl.DataFrame:
    def t(pfr, date, received, gave, season=2026, pick_season=None):
        return {
            "pfr_id": pfr,
            "pick_season": pick_season,
            "trade_date": date,
            "season": season,
            "received": received,
            "gave": gave,
        }

    d = dt.date
    rows = [
        t("DadeTr00", d(2026, 9, 22), "KC", "LV"),
        t("PickDr00", d(2026, 9, 22), "SF", "DEN", pick_season=2019),  # a traded draft pick
        t("KayQu00", d(2026, 9, 30), "LV", "KC"),  # after the run date
        t("KayQu00", d(2017, 9, 1), "LV", "KC", season=2017),  # before the graph's seasons
        t("BlocTo00", d(2026, 9, 29), "KC", "SF"),  # on the run date
        t("FoxSa00", None, "KC", "SF"),  # no date
        t("", d(2026, 9, 1), "KC", "SF"),
        t(None, d(2026, 9, 1), "KC", "SF"),
        t("GhostGh00", d(2026, 9, 1), "KC", "SF"),  # not in `players`
    ]
    return frame(rows, TRADES_SCHEMA)


def team_week_rows() -> list[dict[str, Any]]:
    def tw(team, season, week, net, direction=None, **kw):
        return {
            "season": season,
            "week": week,
            "team": team,
            "off_epa": net / 2,
            "def_epa": -net / 2,
            "net_epa": net,
            "off_pass_epa": 0.1,
            "off_rush_epa": 0.0,
            "def_pass_epa": 0.0,
            "def_rush_epa": 0.0,
            "elo": 1500.0 + 10 * week,
            "trend_delta": 0.01,
            "direction": direction,
            "perf_vs_expected": 0.0,
            "net_epa_prev": net - 0.01,
        } | kw

    # deliberately not in order
    return [
        tw("KC", 2026, 2, 0.2, "up"),
        tw("SF", 2026, 1, 0.0),
        tw("KC", 2025, 18, 0.1),
        tw("KC", 2026, 1, 0.15),
        tw("KC", 2026, 4, 0.3, "up"),
        tw("LV", 2026, 4, -0.1, "down"),
        tw("KC", 2025, 17, 0.05),
        tw("KC", 2026, 3, 0.25),
        tw("SF", 2026, 2, 0.1),
    ]


def published_frame() -> pl.DataFrame:
    def pub(season, week, iid, typ="injury_ripple"):
        return {
            "season": season,
            "week": week,
            "insight_id": iid,
            "insight_type": typ,
            "entities": ["KC", "Walt Kane"],
            "published_at": dt.datetime(season, 9, 1, tzinfo=UTC),
        }

    return frame(
        [
            pub(2025, 17, "revenge:00-X:LV", "revenge"),
            pub(2026, 2, "injury_ripple:00-KCWR"),
            pub(2026, 3, "qb_change:KC:00-KCQB2", "qb_change"),
            pub(2026, 3, "qb_change:KC:00-KCQB2", "qb_change"),  # a repeat of the same key
            pub(2026, 4, "injury_ripple:00-KCQB"),  # this week (a re-run): not yet history
            pub(2026, 5, "injury_ripple:00-LATER"),
        ],
        PUBLISHED_SCHEMA,
    )


def make_inputs() -> GraphInputs:
    stadiums = frame([{"stadium_id": "KAN00", "name": "GEHA Field"}], STADIUMS_SCHEMA)
    games = games_frame()
    return GraphInputs(
        games=games,
        teams=frame(
            [
                {
                    "team": "KC",
                    "team_name": "Kansas City Chiefs",
                    "team_nick": "Chiefs",
                    "team_conf": "AFC",
                    "team_division": "AFC West",
                },
                {
                    "team": "LV",
                    "team_name": "Las Vegas Raiders",
                    "team_nick": "Raiders",
                    "team_conf": "AFC",
                    "team_division": "AFC West",
                },
                {
                    "team": "SF",
                    "team_name": "San Francisco 49ers",
                    "team_nick": "49ers",
                    "team_conf": "NFC",
                    "team_division": "NFC West",
                },
                {
                    "team": "DEN",
                    "team_name": "Denver Broncos",
                    "team_nick": "Broncos",
                    "team_conf": "AFC",
                    "team_division": "AFC West",
                },
                {
                    "team": "KC",
                    "team_name": "Kansas City Chiefs (dup)",
                    "team_nick": "Chiefs",
                    "team_conf": "AFC",
                    "team_division": "AFC West",
                },
            ],
            TEAMS_SCHEMA,
        ),  # fmt: skip
        players=players_frame(),
        rosters=rosters_frame(),
        player_games=frame(box_rows(), PG_SCHEMA),
        snaps=frame(snaps_rows(), SNAPS_SCHEMA),
        pfr_def=frame(
            [
                {"gsis_id": "00-DENDB", "game_id": GSF1, "def_pressures": 2.0},
                {"gsis_id": "00-DENDB", "game_id": GSF1, "def_pressures": 1.0},
                {"gsis_id": None, "game_id": GSF1, "def_pressures": 4.0},
            ],
            PFR_SCHEMA,
        ),
        ngs={
            "receiving": frame(
                [
                    {"player_gsis_id": WR, "season": 2026, "week": 1, "avg_separation": 3.1},
                    {"player_gsis_id": WR, "season": 2026, "week": 4, "avg_separation": 9.9},
                ],
                NGS_SCHEMAS["receiving"],
            ),
            "passing": frame(
                [
                    {"player_gsis_id": Q, "season": 2026, "week": 1, "avg_time_to_throw": 2.7},
                    {"player_gsis_id": Q, "season": 2026, "week": 2, "avg_time_to_throw": 2.5},
                ],
                NGS_SCHEMAS["passing"],
            ),
            "rushing": frame([], NGS_SCHEMAS["rushing"]),
        },
        plays=plays_frame(),
        team_games=frame(
            [
                {"game_id": G1, "team": "KC", "penalties": 5.0, "penalty_yards": 40.0},
                {"game_id": G1, "team": "LV", "penalties": 8.0, "penalty_yards": 70.0},
                {"game_id": G3, "team": "KC", "penalties": 3.0, "penalty_yards": 25.0},
            ],
            TEAM_GAMES_SCHEMA,
        ),
        officials=frame(
            [
                {
                    "game_id": "OLD202601KC",
                    "official_id": 101,
                    "official_name": "Ref One",
                    "position": "R",
                },
                {
                    "game_id": "OLD202601KC",
                    "official_id": 101,
                    "official_name": "Ref One",
                    "position": "R",
                },  # duplicate
                {
                    "game_id": "OLD202601KC",
                    "official_id": 102,
                    "official_name": "Ump Two",
                    "position": "U",
                },
                {
                    "game_id": "OLD202603KC",
                    "official_id": 101,
                    "official_name": "Ref One",
                    "position": "R",
                },
                {
                    "game_id": "OLD202604KC",
                    "official_id": 103,
                    "official_name": "Judge Three",
                    "position": "BJ",
                },  # week W
                {
                    "game_id": "OLD202601KC",
                    "official_id": None,
                    "official_name": "Nobody",
                    "position": "FJ",
                },
            ],
            OFFICIALS_SCHEMA,
        ),  # fmt: skip
        injuries=injuries_frame(),
        depth_charts=depth_frame(),
        draft_picks=frame(
            [
                {"gsis_id": Q, "season": 2020, "team": "KC", "round": 1, "pick": 10},
                {"gsis_id": "00-TRD", "season": 2021, "team": "LV", "round": 2, "pick": 40},
                {"gsis_id": WR, "season": 2027, "team": "KC", "round": 1, "pick": 1},
                {"gsis_id": None, "season": 2020, "team": "KC", "round": 7, "pick": 250},
            ],
            DRAFT_SCHEMA,
        ),
        trades=trades_frame(),
        venues=frame(
            [{"game_id": g, "stadium_id": STADIUM[g.split("_")[3]]} for g in games["game_id"]],
            VENUES_SCHEMA,
        ),
        stadiums=stadiums,
        expected_qbs=frame(
            [{"game_id": GW, "home_qb_expected": Q2, "away_qb_expected": "00-SFQB"}],
            EXPECTED_QB_SCHEMA,
        ),
        team_weeks=frame(team_week_rows(), TEAM_WEEKS_SCHEMA),
        predictions=frame(
            [
                {
                    "game_id": GW,
                    "model_version": "v0",
                    "variant": "main",
                    "is_primary": True,
                    "home_win_prob": 0.6,
                    "expected_margin": 2.0,
                    "pred_home_points": 24.0,
                    "pred_away_points": 22.0,
                    "confidence_label": "lean",
                    "created_at": dt.datetime(2026, 9, 29, 10, tzinfo=UTC),
                },
                {
                    "game_id": GW,
                    "model_version": "v0",
                    "variant": "alt",
                    "is_primary": False,
                    "home_win_prob": 0.55,
                    "expected_margin": 1.0,
                    "pred_home_points": 23.0,
                    "pred_away_points": 22.0,
                    "confidence_label": "toss-up",
                    "created_at": dt.datetime(2026, 9, 29, 10, tzinfo=UTC),
                },
            ],
            PREDICTIONS_SCHEMA,
        ),  # fmt: skip
        published=published_frame(),
    )


ALL_GAMES = {game_row(*spec)["game_id"] for spec in GAME_SPECS}
VISIBLE_GAMES = {
    game_row(*spec)["game_id"] for spec in GAME_SPECS if (spec[0], spec[1]) < (2026, 4)
}


def canon(df: pl.DataFrame) -> pl.DataFrame:
    """Sorted by every scalar column, so frames compare without caring about row order."""
    cols = [c for c, t in df.schema.items() if t.is_numeric() or t in (S, pl.Boolean, pl.Date, TS)]
    return df.sort(cols) if cols else df


def differing_tables(a: T.GraphTables, b: T.GraphTables) -> list[str]:
    """Names of the node / relationship tables that differ (row order ignored)."""
    bad = []
    for kind in ("nodes", "rels"):
        ta, tb = getattr(a, kind), getattr(b, kind)
        assert ta.keys() == tb.keys()
        bad += [f"{kind}:{k}" for k in ta if not canon(ta[k]).equals(canon(tb[k]))]
    return bad


# ---- a fake Neo4j driver ------------------------------------------------------------------------


class FakeRecord(dict):
    def data(self) -> dict[str, Any]:
        return dict(self)


class FakeResult:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = [FakeRecord(r) for r in records]

    def consume(self) -> None:
        return None

    def single(self) -> FakeRecord | None:
        return self.records[0] if self.records else None

    def __iter__(self):
        return iter(self.records)


class FakeTx:
    def __init__(self, driver: FakeDriver) -> None:
        self.driver = driver

    def run(self, query: str, **params: Any) -> FakeResult:
        return self.driver.record(query, params)


class FakeSession:
    def __init__(self, driver: FakeDriver) -> None:
        self.driver = driver

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def run(self, query: str, **params: Any) -> FakeResult:
        return self.driver.record(query, params)

    def execute_write(self, fn):
        return fn(FakeTx(self.driver))

    def execute_read(self, fn):
        return fn(FakeTx(self.driver))


class FakeDriver:
    """Records every query; `respond(query, params)` supplies the records a query returns.
    `fail` makes `session()` raise (a server that is down)."""

    def __init__(self, respond=None, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.respond = respond or (lambda query, params: [])
        self.fail = fail

    def session(self) -> FakeSession:
        if self.fail is not None:
            raise self.fail
        return FakeSession(self)

    def record(self, query: str, params: dict[str, Any]) -> FakeResult:
        self.calls.append((query, params))
        return FakeResult(self.respond(query, params))
