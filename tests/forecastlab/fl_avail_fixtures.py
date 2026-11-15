"""A small synthetic league for the FL00 availability tests (no real data, no network).

Four teams, three seasons (2014-2016) of six regular-season weeks. Every team carries a QB pair,
two RBs, three WRs, two TEs, three DL, two LB, two CB and two S. Each week a few players are on
the injury report (Questionable / Out) and some of them sit; inactive players are `INA` on that
week's roster; one player per team goes on reserve (`RES`) for good in 2015 week 3.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nflengine.forecastlab import availability as A

TEAMS = ("KC", "BUF", "NE", "MIA")
SEASONS = (2014, 2015, 2016)
WEEKS = 6
ROSTER = (
    ("QB", 1, 1.0),
    ("QB", 2, 0.0),
    ("RB", 1, 0.65),
    ("RB", 2, 0.35),
    ("WR", 1, 0.9),
    ("WR", 2, 0.8),
    ("WR", 3, 0.45),
    ("TE", 1, 0.85),
    ("TE", 2, 0.3),
    ("DE", 1, 0.8),
    ("DT", 2, 0.6),
    ("DE", 3, 0.35),
    ("LB", 1, 0.95),
    ("LB", 2, 0.5),
    ("CB", 1, 0.98),
    ("CB", 2, 0.7),
    ("FS", 1, 1.0),
    ("SS", 2, 0.9),
)
DEF = {"DE", "DT", "LB", "CB", "FS", "SS"}

SMALL_CFG: dict[str, Any] = {
    "train_from": 2014,
    "tune_seasons": "2015-2015",
    "report_seasons": "2016-2016",
    "walk_from": 2015,
    "min_calib_rows": 50,
    "threads": 1,
    "play": {"num_leaves": 4, "rounds": 15, "min_data_in_leaf": 5, "learning_rate": 0.2},
    "snap": {"num_leaves": 4, "rounds": 15, "min_data_in_leaf": 5, "learning_rate": 0.2},
    "share": {
        "num_leaves": 4,
        "rounds": 15,
        "min_data_in_leaf": 5,
        "learning_rate": 0.2,
        "objective": "l2",
    },
}


def small_cfg(**over: Any) -> dict[str, Any]:
    return A._merge(A._merge(A.DEFAULTS, SMALL_CFG), over)


def pid(team: str, pos: str, i: int) -> str:
    return f"00-{team}-{pos}{i}"


def kickoff(season: int, week: int, game: int) -> datetime:
    return datetime(season, 9, 7, 17, 0, tzinfo=UTC) + timedelta(weeks=week - 1, hours=3 * game)


def make_league(seed: int = 3, seasons: tuple[int, ...] = SEASONS) -> dict[str, pl.DataFrame]:
    """The curated tables FL00 reads: games, snaps, injuries, rosters_weekly, player_games and
    the play-by-play columns `rush_counts` needs."""
    rng = np.random.default_rng(seed)
    games, snaps, inj, ros, pg, plays = [], [], [], [], [], []
    for season in seasons:
        for week in range(1, WEEKS + 1):
            order = list(TEAMS)
            if week % 2 == 0:
                order = [order[0], order[2], order[1], order[3]]
            if week % 3 == 0:
                order = [order[0], order[3], order[1], order[2]]
            for gi in range(2):
                home, away = order[2 * gi], order[2 * gi + 1]
                gid = f"{season}_{week:02d}_{away}_{home}"
                games.append(
                    {
                        "game_id": gid,
                        "season": season,
                        "week": week,
                        "game_type": "REG",
                        "home_team": home,
                        "away_team": away,
                        "kickoff_utc": kickoff(season, week, gi),
                        "completed": True,
                    }
                )
                for team, opp in ((home, away), (away, home)):
                    _team_game(rng, season, week, gid, team, opp, snaps, inj, ros, pg, plays)
    tables = {
        "games": pl.DataFrame(games).with_columns(
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            pl.col("kickoff_utc").cast(pl.Datetime("us", "UTC")),
        ),
        "snaps": pl.DataFrame(snaps).with_columns(
            pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
        ),
        "injuries": pl.DataFrame(
            inj,
            schema={
                "season": pl.Int32,
                "week": pl.Int32,
                "gsis_id": pl.String,
                "team": pl.String,
                "report_status": pl.String,
                "practice_status": pl.String,
            },
        ),
        "rosters_weekly": pl.DataFrame(ros).with_columns(
            pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
        ),
        "player_games": pl.DataFrame(pg),
        "plays": pl.DataFrame(
            plays,
            schema={
                "season": pl.Int32,
                "game_id": pl.String,
                "posteam": pl.String,
                "rusher_player_id": pl.String,
                "rush": pl.Float64,
                "two_point_attempt": pl.Float64,
            },
        ),
    }
    return tables


def _team_game(rng, season, week, gid, team, opp, snaps, inj, ros, pg, plays) -> None:
    played: dict[str, float] = {}
    qb1_out = False
    for pos, i, base in ROSTER:
        p = pid(team, pos, i)
        reserve = (pos, i) == ("WR", 2) and (season, week) >= (2015, 3)
        status = None
        if reserve:
            ros.append(_ros(season, week, team, p, "RES", pos))
            continue
        u = rng.random()
        if u < 0.06:
            status = "Out"
        elif u < 0.14:
            status = "Questionable"
        sits = status == "Out" or (status == "Questionable" and rng.random() < 0.35)
        if pos == "QB" and i == 1 and sits:
            qb1_out = True
        if status is not None:
            prac = (
                "Did Not Participate In Practice"
                if status == "Out"
                else ("Limited Participation in Practice")
            )
            inj.append(
                {
                    "season": season,
                    "week": week,
                    "gsis_id": p,
                    "team": team,
                    "report_status": status,
                    "practice_status": prac,
                }
            )
        share = base
        if pos == "QB" and i == 2:
            share = 1.0 if qb1_out else 0.0
        if base < 0.5 and rng.random() < 0.15:
            sits = True  # a healthy scratch
        if sits or share == 0.0:
            ros.append(_ros(season, week, team, p, "INA" if sits else "ACT", pos))
            continue
        pct = float(np.clip(share + rng.normal(0, 0.08), 0.02, 1.0))
        played[p] = pct
        ros.append(_ros(season, week, team, p, "ACT", pos))
        d = pos in DEF
        snaps.append(
            {
                "game_id": gid,
                "season": season,
                "week": week,
                "player": p,
                "position": pos,
                "team": team,
                "opponent": opp,
                "offense_snaps": 0.0 if d else round(pct * 65),
                "offense_pct": 0.0 if d else pct,
                "defense_snaps": round(pct * 65) if d else 0.0,
                "defense_pct": pct if d else 0.0,
                "gsis_id": p,
            }
        )
    rec = [p for p in played if "-WR" in p or "-TE" in p or "-RB" in p]
    if rec:
        w = rng.dirichlet(np.ones(len(rec)) * 2) * np.array([played[p] for p in rec])
        w = w / w.sum()
        for p, s in zip(rec, w, strict=True):
            pg.append({"player_id": p, "game_id": gid, "target_share": float(s)})
    car = [p for p in played if "-RB" in p or "-QB" in p]
    for p in car:
        n = int(rng.poisson(14 * played[p] if "-RB" in p else 2))
        plays.extend(
            {
                "season": season,
                "game_id": gid,
                "posteam": team,
                "rusher_player_id": p,
                "rush": 1.0,
                "two_point_attempt": 0.0,
            }
            for _ in range(n)
        )


def _ros(season, week, team, p, status, pos) -> dict[str, Any]:
    return {
        "season": season,
        "week": week,
        "team": team,
        "gsis_id": p,
        "status": status,
        "position": {"DE": "DL", "DT": "DL", "FS": "S", "SS": "S"}.get(pos, pos),
    }


def prepared(tables: dict[str, pl.DataFrame]) -> A.Prepared:
    return A.prepare(
        tables["games"],
        tables["snaps"],
        tables["injuries"],
        tables["rosters_weekly"],
        tables["player_games"],
        A.rush_counts(tables["plays"]),
    )


def write_root(root: Path, tables: dict[str, pl.DataFrame]) -> None:
    """A data root with the curated files (and `plays/season=YYYY.parquet`)."""
    cur = root / "curated"
    (cur / "plays").mkdir(parents=True, exist_ok=True)
    for name in ("games", "snaps", "injuries", "rosters_weekly", "player_games"):
        tables[name].write_parquet(cur / f"{name}.parquet")
    for (s,), part in tables["plays"].group_by("season"):
        part.drop("season").write_parquet(cur / "plays" / f"season={s}.parquet")
