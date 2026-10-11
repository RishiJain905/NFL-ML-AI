"""PC01 fixtures: a synthetic `playcalling/<S>/` tree for the control room's Play calling reader.

Nothing here runs the real build. The tables are written directly, with the build's own schemas
(`build.TEND_SCHEMA`, the team-game table's columns) and the label registry's units, sources and
families, so a column the reader needs can't drift away from what `nfl playcalling build` writes
(`test_app_playcalling.py` also compares the schemas with the real files on the data root).

**The league is a permutation**, so the numbers are hand-checkable. For one (as-of week, side,
window, metric, situation) the 32 teams, in alphabetical order, take the ranks
p = (index + k) mod 32 for an offset k that depends on that key. A rate is `lo + step * p`
(shares 0.10 + 0.01 p, PROE -0.10 + 0.005 p, means 1.0 + 0.1 p), so

- the league value is `lo + step * 15.5`,
- a team's `diff` is `step * (p - 15.5)`, its percentile `100 p / 31`,
- the sample SD of the 32 values is `step * SD32` (SD32 = 9.3808..., the SD of 0..31), so a
  defense's matchup shift (its diff in SDs) is `(p - 15.5) / SD32`: p = 31 gives 1.65, p = 25
  gives 1.01, p = 24 gives 0.91, whatever the metric's unit.

`Tend.pin` overwrites a cell with exact numbers (KC's page is pinned by hand), `drop` removes one,
`add` appends a decoy row after the real ones (a `history_only` copy, a wrong situation).

The schedule is `games.parquet` (curated): weeks 1-4 have a few games, week 5 has 15 (KC and WAS
on a bye, one game without a kickoff time, one with a code that isn't a team), week 6 two.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.curate.teams import CANONICAL_TEAMS
from nflengine.paths import DataPaths
from nflengine.playcalling import build as B
from nflengine.playcalling import forecast as PF
from nflengine.playcalling import forecast_runs as FR
from nflengine.playcalling import labels as L

TEAMS = sorted(CANONICAL_TEAMS)
IDX = {t: i for i, t in enumerate(TEAMS)}
SD32 = statistics.stdev(range(32))  # 9.3808315...: the sample SD of the ranks 0..31
SIDES = ("offense", "defense")
NAN, INF = float("nan"), float("inf")

# the situation each headline metric is read in (labels.HEADLINE); everything else: all plays
READ_IN = {"proe": "neutral", "dropback_rate": "neutral"}
BASE = {"share": (0.10, 0.01), "over_expected": (-0.10, 0.005), "mean": (1.0, 0.1)}

TG_SCHEMA = {
    "season": pl.Int32, "week": pl.Int32, "game_id": pl.String, "team": pl.String,
    "opponent": pl.String, "side": pl.String, "metric": pl.String, "situation": pl.String,
    "n": pl.Int64, "value": pl.Float64, "family": pl.String, "unit": pl.String,
    "source": pl.String, "history_only": pl.Boolean,
}  # fmt: skip

PAGE_METRICS = [m.name for m in L.METRICS if not m.history_only] + [L.PFR_BLITZ.name]
PBP_ONLY_METRICS = [
    m for m in PAGE_METRICS if m != "blitz_rate_pfr" and L.METRIC_BY_NAME[m].source == "pbp"
]
SITUATIONAL = [m.name for m in L.METRICS if m.situational]
OTHER_SITUATIONS = [s.name for s in L.SITUATIONS if s.name not in ("all", "neutral")]
HISTORY_METRICS = [m.name for m in L.METRICS if m.history_only]


def meta(metric: str) -> tuple[str, str, bool]:
    m = L.METRIC_BY_NAME.get(metric) or (L.PFR_BLITZ if metric == L.PFR_BLITZ.name else None)
    assert m is not None, metric
    return m.unit, m.source, m.history_only


def read_in(metric: str) -> str:
    return READ_IN.get(metric, "all")


def page_pairs(metrics: list[str] | None = None) -> list[tuple[str, str]]:
    """Everything a team page can read: every metric in `all` and `neutral`, the six situational
    metrics in the other eighteen situations."""
    ms = metrics or PAGE_METRICS
    pairs = [(m, s) for m in ms for s in ("all", "neutral")]
    pairs += [(m, s) for m in SITUATIONAL if m in ms for s in OTHER_SITUATIONS]
    return pairs


def week_pairs(metrics: list[str]) -> list[tuple[str, str]]:
    """Just what the grid and the matchups read: each metric in its read-in situation, plus the
    other one as a decoy (a reader that ignores the situation would show the wrong number)."""
    return [(m, s) for m in metrics for s in ("all", "neutral")]


def history_pairs() -> list[tuple[str, str]]:
    return [(m, "all") for m in HISTORY_METRICS]


def offset(as_of: int, side: str, window: str, metric: str, situation: str) -> int:
    return sum(ord(c) for c in f"{as_of}|{side}|{window}|{metric}|{situation}") % 32


def rank(team: str, as_of: int, side: str, window: str, metric: str, situation: str) -> int:
    """p: 0 = the league's lowest value, 31 = its highest."""
    return (IDX[team] + offset(as_of, side, window, metric, situation)) % 32


def games_in(as_of: int, window: str) -> int:
    return {"season": as_of - 1, "last4": min(4, as_of - 1), "last_season": 17}[window]


def windows_for(as_of: int) -> tuple[str, ...]:
    return ("last_season",) if as_of == 1 else ("season", "last4", "last_season")


Key = tuple[int, str, str, str, str, str]  # as_of, team, side, window, metric, situation


class Tend:
    """One season's `team_tendencies` rows, editable by key before they're written."""

    def __init__(self, season: int):
        self.season = season
        self.rows: dict[Key, dict[str, Any]] = {}
        self.extra: list[dict[str, Any]] = []

    def fill(
        self,
        as_of: int,
        pairs: list[tuple[str, str]],
        *,
        sides: tuple[str, ...] = SIDES,
        windows: tuple[str, ...] | None = None,
        n: int = 200,
        teams: list[str] | None = None,
    ) -> Tend:
        for side in sides:
            for window in windows or windows_for(as_of):
                for metric, sit in pairs:
                    unit, source, hist = meta(metric)
                    lo, step = BASE[unit]
                    for team in teams or TEAMS:
                        p = rank(team, as_of, side, window, metric, sit)
                        self.rows[(as_of, team, side, window, metric, sit)] = {
                            "season": self.season,
                            "as_of_week": as_of,
                            "team": team,
                            "side": side,
                            "window": window,
                            "metric": metric,
                            "situation": sit,
                            "family": L.SITUATION_FAMILY[sit],
                            "n": n,
                            "games": games_in(as_of, window),
                            "value": lo + step * p,
                            "league_value": lo + step * 15.5,
                            "diff": step * (p - 15.5),
                            "pct": 100 * p / 31,
                            "unit": unit,
                            "source": source,
                            "history_only": hist,
                        }
        return self

    def key(self, team, side, as_of, window, metric, situation) -> Key:
        return (as_of, team, side, window, metric, situation)

    def get(self, team, side, as_of, window, metric, situation) -> dict[str, Any]:
        return self.rows[self.key(team, side, as_of, window, metric, situation)]

    def pin(self, team, side, as_of, window, metric, situation, **fields: Any) -> dict[str, Any]:
        """Overwrite a generated cell with exact numbers (n, games, value, league_value, ...)."""
        row = self.get(team, side, as_of, window, metric, situation)
        unknown = set(fields) - set(row)
        assert not unknown, unknown
        row.update(fields)
        return row

    def drop(self, team, side, as_of, window, metric, situation) -> None:
        del self.rows[self.key(team, side, as_of, window, metric, situation)]

    def add(self, **row: Any) -> dict[str, Any]:
        """A row appended after the generated ones (a decoy that shares a real row's key lands
        last, so a reader that doesn't filter it would let it win)."""
        unit, source, hist = meta(row["metric"])
        full = {
            "season": self.season,
            "family": L.SITUATION_FAMILY[row["situation"]],
            "n": 200,
            "games": 4,
            "value": 0.5,
            "league_value": 0.5,
            "diff": 0.0,
            "pct": 50.0,
            "unit": unit,
            "source": source,
            "history_only": hist,
            **row,
        }
        self.extra.append(full)
        return full

    def cells(self, as_of: int, window: str, metrics: list[str]) -> dict[tuple, dict[str, Any]]:
        """(team, side, metric) -> row, each metric in its read-in situation."""
        return {
            (t, s, m): self.rows[(as_of, t, s, window, m, read_in(m))]
            for t in TEAMS
            for s in SIDES
            for m in metrics
            if (as_of, t, s, window, m, read_in(m)) in self.rows
        }

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame([*self.rows.values(), *self.extra], schema=B.TEND_SCHEMA)

    def league_frame(self) -> pl.DataFrame:
        f = self.frame().filter(pl.col("side") == "offense")
        if f.is_empty():
            return pl.DataFrame(schema=B.LEAGUE_SCHEMA)
        return (
            f.group_by(
                "season",
                "as_of_week",
                "window",
                "metric",
                "situation",
                "family",
                "unit",
                "source",
                "history_only",
            )  # fmt: skip
            .agg(
                pl.col("n").sum(),
                pl.col("value").mean(),
                pl.len().cast(pl.Int32).alias("teams"),
            )
            .select(list(B.LEAGUE_SCHEMA))
            .cast(B.LEAGUE_SCHEMA)
        )


class TeamGames:
    """`team_game_tendencies`: one row per team x game x side x metric x situation."""

    def __init__(self, season: int):
        self.season = season
        self.rows: list[dict[str, Any]] = []

    def add(
        self, week, game_id, team, opponent, side, metric, situation, n, value, **over: Any
    ) -> TeamGames:
        unit, source, hist = meta(metric)
        self.rows.append(
            {
                "season": self.season,
                "week": week,
                "game_id": game_id,
                "team": team,
                "opponent": opponent,
                "side": side,
                "metric": metric,
                "situation": situation,
                "n": n,
                "value": value,
                "family": L.SITUATION_FAMILY[situation],
                "unit": unit,
                "source": source,
                "history_only": hist,
                **over,
            }
        )
        return self

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(self.rows, schema=TG_SCHEMA)


def build_doc(
    season: int,
    last: int,
    *,
    history: bool = False,
    waiting: tuple[str, ...] = (),
    built_at: str = "2026-10-11T00:15:21+00:00",
) -> dict[str, Any]:
    """The parts of `build.json` the reader looks at, in the build's own layout."""
    return {
        "schema_version": 1,
        "season": season,
        "through_week": None,
        "as_of_weeks": [1, last],
        "last_complete_week": last - 1,
        "coverage": {"plays": 34502, "ftn_join_rate": 0.9986, "games_without_ftn": list(waiting)},
        "rows": {"team_tendencies": 0},
        "history_metrics": history,
        "availability": {"as_of_week": last, "ftn_held_games": [], "pfr_held_games": 0},
        "built_at": built_at,
        "seconds": 1.0,
        "notes": [],
        "files": {
            n: f"{n}.parquet"
            for n in (
                "plays_enriched",
                "team_game_tendencies",
                "team_tendencies",
                "league_tendencies",
            )
        },
    }


def write_season(
    paths: DataPaths,
    tend: Tend,
    doc: dict[str, Any] | None,
    games: TeamGames | None = None,
    *,
    tables: bool = True,
) -> Path:
    """Write the season's tables, then build.json last (the build's own order). `doc=None`
    leaves the folder without a build.json: a build that is "not built". `tables=False`
    writes only build.json (the tables went missing)."""
    d = paths.playcalling / str(tend.season)
    d.mkdir(parents=True, exist_ok=True)
    if tables:
        tend.frame().write_parquet(d / "team_tendencies.parquet", compression="zstd")
        (games or TeamGames(tend.season)).frame().write_parquet(
            d / "team_game_tendencies.parquet", compression="zstd"
        )
        tend.league_frame().write_parquet(d / "league_tendencies.parquet", compression="zstd")
    if doc is not None:
        (d / "build.json").write_text(json.dumps(doc), encoding="utf-8")
    return d


# ---- the schedule (curated games) and the teams table ------------------------------------------

GAME_SCHEMA = {
    "game_id": pl.String, "season": pl.Int32, "week": pl.Int32, "away_team": pl.String,
    "home_team": pl.String, "kickoff_utc": pl.Datetime("us", "UTC"),
}  # fmt: skip
THU = dt.datetime(2026, 10, 9, 0, 15, tzinfo=dt.UTC)  # Thursday night, 8:15 pm ET
SUN_1 = dt.datetime(2026, 10, 11, 17, 0, tzinfo=dt.UTC)  # 1 pm ET
SUN_4 = dt.datetime(2026, 10, 11, 20, 5, tzinfo=dt.UTC)
SNF = dt.datetime(2026, 10, 12, 0, 20, tzinfo=dt.UTC)

# (away, home, kickoff): week 5 has KC and WAS on a bye, TB at TEN with no kickoff time, and a
# row whose code isn't a team (the reader leaves it out). Games sharing a kickoff sort by game id.
WEEK5: list[tuple[str, str, dt.datetime | None]] = [
    ("ARI", "ATL", THU),
    ("CAR", "CHI", SUN_1),
    ("BAL", "BUF", SUN_1),
    ("DAL", "DEN", SUN_1),
    ("CIN", "CLE", SUN_1),
    ("DET", "GB", SUN_1),
    ("HOU", "IND", SUN_4),
    ("JAX", "LV", SUN_4),
    ("LA", "LAC", SUN_4),
    ("MIA", "MIN", SUN_4),
    ("NE", "NO", SUN_4),
    ("NYG", "NYJ", SUN_4),
    ("PHI", "PIT", SNF),
    ("SEA", "SF", SNF),
    ("TB", "TEN", None),
    ("OAK", "WAS", SUN_1),  # not a team code: skipped
]
OTHER_WEEKS: dict[int, list[tuple[str, str, dt.datetime | None]]] = {
    1: [("KC", "LV", dt.datetime(2026, 9, 13, 17, 0, tzinfo=dt.UTC)),
        ("LA", "LAC", dt.datetime(2026, 9, 13, 20, 5, tzinfo=dt.UTC)),
        ("BAL", "BUF", dt.datetime(2026, 9, 13, 20, 25, tzinfo=dt.UTC))],
    2: [("BAL", "KC", dt.datetime(2026, 9, 20, 17, 0, tzinfo=dt.UTC)),
        ("MIN", "NO", dt.datetime(2026, 9, 20, 17, 0, tzinfo=dt.UTC))],
    3: [("MIN", "CHI", dt.datetime(2026, 9, 27, 17, 0, tzinfo=dt.UTC)),
        ("LA", "SEA", dt.datetime(2026, 9, 27, 20, 5, tzinfo=dt.UTC))],
    4: [("KC", "DEN", dt.datetime(2026, 10, 4, 17, 0, tzinfo=dt.UTC)),
        ("ATL", "NO", dt.datetime(2026, 10, 5, 0, 15, tzinfo=dt.UTC))],
    6: [("KC", "WAS", dt.datetime(2026, 10, 18, 17, 0, tzinfo=dt.UTC)),
        ("LAC", "LA", dt.datetime(2026, 10, 18, 20, 25, tzinfo=dt.UTC))],
}  # fmt: skip


def schedule(season: int = 2026) -> list[tuple[int, str, str, dt.datetime | None]]:
    out = [(5, a, h, k) for a, h, k in WEEK5]
    out += [(w, a, h, k) for w, games in OTHER_WEEKS.items() for a, h, k in games]
    return sorted(
        out,
        key=lambda r: (r[0], r[3] is None, r[3] or dt.datetime.min.replace(tzinfo=dt.UTC), r[1]),
    )


def game_id(season: int, week: int, away: str, home: str) -> str:
    return f"{season}_{week:02d}_{away}_{home}"


def write_games(
    paths: DataPaths, season: int = 2026, extra: list[dict[str, Any]] | None = None
) -> None:
    rows = [
        {
            "game_id": game_id(season, w, a, h),
            "season": season,
            "week": w,
            "away_team": a,
            "home_team": h,
            "kickoff_utc": k,
        }
        for w, a, h, k in schedule(season)
    ]
    pl.DataFrame([*rows, *(extra or [])], schema=GAME_SCHEMA).write_parquet(
        paths.curated / "games.parquet"
    )


def write_teams(paths: DataPaths) -> None:
    pl.DataFrame(
        {
            "team": TEAMS,
            "team_name": [f"{t} Team" for t in TEAMS],
            "team_nick": [f"{t}s" for t in TEAMS],
            "team_conf": ["AFC" if i < 16 else "NFC" for i in range(32)],
            "team_division": ["North"] * 32,
            "team_color": ["#112233"] * 32,
        }
    ).write_parquet(paths.curated / "teams.parquet")


# ---- KC's page, pinned by hand ----------------------------------------------------------------


def pin_cell(t: Tend, team, side, as_of, metric, sit, **windows: dict[str, Any]) -> None:
    """Pin a cell in each named window: `season=dict(n=..., value=..., ...)`."""
    for window, fields in windows.items():
        t.pin(team, side, as_of, window, metric, sit, **fields)


def pin_pcts(t: Tend, team, side, as_of, window, metrics: list[str], pct: float) -> None:
    for m in metrics:
        t.pin(team, side, as_of, window, m, read_in(m), pct=pct)


SUMMARY_POOL_OFFENSE = [
    "no_huddle_rate", "motion_rate", "qb_under_center_share", "qb_shotgun_share",
    "play_action_rate", "screen_rate", "rpo_rate", "deep_shot_rate", "adot",
]  # fmt: skip
SUMMARY_POOL_DEFENSE = [
    "rushers_avg", "light_box_rate", "heavy_box_rate", "proe", "play_action_rate",
    "deep_shot_rate", "explosive_rate",
]  # fmt: skip


def pin_kc(t: Tend) -> None:
    """KC's offense and MIN's defense at as-of week 5, with exact numbers (the tests' anchors)."""
    # the five-second summary reads the `season` window: PROE leads, then the two metrics
    # furthest from the middle that are neither small nor in between
    pin_pcts(t, "KC", "offense", 5, "season", SUMMARY_POOL_OFFENSE, 50.0)
    pin_cell(
        t, "KC", "offense", 5, "proe", "neutral",
        season=dict(n=177, games=4, value=0.037, league_value=-0.020, diff=0.057, pct=90.0),
        last4=dict(n=19, games=4, value=0.030, league_value=-0.019, diff=0.049, pct=80.0),
        last_season=dict(n=638, games=17, value=0.0365574, league_value=-0.0195, diff=0.0560574,
                         pct=90.3),
    )  # fmt: skip
    pin_cell(  # n = 19 and 20 either side of the edge; season's pct is high but it is small
        t, "KC", "offense", 5, "play_action_rate", "all",
        season=dict(n=19, games=4, value=0.15, league_value=0.23, diff=-0.08, pct=3.0),
        last4=dict(n=20, games=4, value=0.11, league_value=0.24, diff=-0.13, pct=2.0),
        last_season=dict(n=500, games=17, value=0.215, league_value=0.23, diff=-0.015, pct=40.0),
    )  # fmt: skip
    pin_cell(
        t,
        "KC",
        "offense",
        5,
        "rpo_rate",
        "all",
        season=dict(n=300, games=4, value=0.144, league_value=0.062, diff=0.082, pct=100.0),
    )
    pin_cell(
        t,
        "KC",
        "offense",
        5,
        "no_huddle_rate",
        "all",
        season=dict(n=300, games=4, value=0.025, league_value=0.099, diff=-0.074, pct=3.2),
    )
    pin_cell(  # a low percentile, but not as far from the middle as the two above
        t,
        "KC",
        "offense",
        5,
        "qb_under_center_share",
        "all",
        season=dict(n=300, games=4, value=0.188, league_value=0.337, diff=-0.149, pct=6.5),
    )
    pin_cell(  # a decoy: PROE in all plays must never stand in for the neutral number
        t,
        "KC",
        "offense",
        5,
        "proe",
        "all",
        season=dict(n=300, games=4, value=0.9, league_value=0.1, diff=0.8, pct=99.0),
    )
    pin_cell(
        t,
        "KC",
        "offense",
        5,
        "dropback_rate",
        "3rd_long",
        season=dict(n=42, games=4, value=0.923, league_value=0.9, diff=0.023, pct=55.0),
    )
    pin_cell(
        t,
        "KC",
        "offense",
        5,
        "play_action_rate",
        "1st_down",
        season=dict(n=120, games=4, value=0.39, league_value=0.37, diff=0.02, pct=61.0),
    )
    pin_cell(
        t,
        "KC",
        "offense",
        5,
        "run_right_end",
        "all",
        season=dict(n=60, games=4, value=0.0894, league_value=0.1043, diff=-0.0149, pct=41.9),
    )
    t.drop("KC", "offense", 5, "last4", "screen_rate", "all")  # a window with no row: None
    # MIN's defense: what offenses did against it, and its own blitz
    pin_pcts(t, "MIN", "defense", 5, "season", SUMMARY_POOL_DEFENSE, 50.0)
    pin_cell(
        t,
        "MIN",
        "defense",
        5,
        "blitz_rate",
        "all",
        season=dict(n=300, games=4, value=0.725, league_value=0.311, diff=0.414, pct=100.0),
    )
    pin_cell(
        t,
        "MIN",
        "defense",
        5,
        "proe",
        "neutral",
        season=dict(n=177, games=4, value=-0.089, league_value=-0.022, diff=-0.067, pct=0.0),
    )
    pin_cell(
        t,
        "MIN",
        "defense",
        5,
        "heavy_box_rate",
        "all",
        season=dict(n=20, games=4, value=0.058, league_value=0.057, diff=0.001, pct=88.0),
    )


def add_decoys(t: Tend) -> None:
    """`history_only` copies (research rows) of four real cells, written after the real rows: a
    reader that doesn't drop them lets them win (value 0.999, n 999)."""
    for team, side, as_of, metric, sit in (
        ("KC", "offense", 5, "proe", "neutral"),
        ("MIN", "defense", 5, "blitz_rate", "all"),
        ("DEN", "defense", 5, "play_action_rate", "all"),
        ("KC", "offense", 3, "proe", "neutral"),
    ):
        t.add(
            as_of_week=as_of, team=team, side=side, window="season", metric=metric,
            situation=sit, n=999, games=4, value=0.999, league_value=0.001, diff=0.998,
            pct=100.0, history_only=True,
        )  # fmt: skip


def pin_non_finite(t: Tend) -> None:
    """NaN and inf in the tables (a division by zero upstream): every one must reach the browser
    as null. LA's neutral PROE at week 5 (the grid, KC-style page), and two cells at week 3."""
    t.pin(
        "LA",
        "offense",
        5,
        "season",
        "proe",
        "neutral",
        value=NAN,
        league_value=INF,
        diff=-INF,
        pct=NAN,
    )
    t.pin("MIN", "defense", 3, "season", "proe", "neutral", value=NAN, diff=NAN, pct=NAN)
    t.pin("LA", "offense", 3, "season", "play_action_rate", "all", league_value=INF)


# KC's games: (week, game id, opponent, {metric: (situation, value, n)}); week 3 is a bye
KC_GAMES: list[tuple[int, str, str, dict[str, tuple[str, float, int]]]] = [
    (1, "2026_01_KC_LV", "LV", {
        "proe": ("neutral", 0.050, 40), "play_action_rate": ("all", 0.12, 30),
        "motion_rate": ("all", 0.50, 60), "qb_under_center_share": ("all", 0.20, 70),
        "deep_shot_rate": ("all", 0.10, 28), "epa_per_play": ("all", 0.15, 66),
    }),
    (2, "2026_02_BAL_KC", "BAL", {
        "proe": ("neutral", 0.010, 38), "play_action_rate": ("all", 0.20, 31),
        "motion_rate": ("all", 0.45, 62), "qb_under_center_share": ("all", 0.18, 68),
        "deep_shot_rate": ("all", 0.15, 30), "epa_per_play": ("all", -0.05, 64),
    }),
    (4, "2026_04_KC_DEN", "DEN", {
        "proe": ("neutral", NAN, 35), "play_action_rate": ("all", 0.18, 29),
        "motion_rate": ("all", 0.52, 61), "deep_shot_rate": ("all", 0.0, 27),
        "epa_per_play": ("all", 0.10, 65),
    }),  # no under-center row; PROE is NaN
]  # fmt: skip


def kc_games(season: int = 2026) -> TeamGames:
    tg = TeamGames(season)
    for week, gid, opp, metrics in KC_GAMES:
        for m, (sit, v, n) in metrics.items():
            tg.add(week, gid, "KC", opp, "offense", m, sit, n, v)
    # a decoy on the wrong situation, a history_only copy after the real row, another team, and
    # the defense side
    tg.add(1, "2026_01_KC_LV", "KC", "LV", "offense", "proe", "all", 50, 0.999)
    tg.add(1, "2026_01_KC_LV", "KC", "LV", "offense", "proe", "neutral", 40, 0.9, history_only=True)
    tg.add(1, "2026_01_KC_LV", "LV", "KC", "offense", "proe", "neutral", 41, 0.777)
    for week, gid, opp, v_blitz, v_proe in (
        (1, "2026_01_KC_LV", "LV", 0.31, -0.08),
        (2, "2026_02_BAL_KC", "BAL", 0.45, 0.01),
        (4, "2026_04_KC_DEN", "DEN", 0.28, -0.02),
    ):
        tg.add(week, gid, "KC", opp, "defense", "blitz_rate", "all", 33, v_blitz)
        tg.add(week, gid, "KC", opp, "defense", "proe", "neutral", 37, v_proe)
    return tg


# ---- worlds ---------------------------------------------------------------------------------

GRID_METRICS = [
    "proe", "play_action_rate", "motion_rate", "qb_under_center_share", "deep_shot_rate",
    "no_huddle_rate", "epa_per_play", "blitz_rate", "rushers_avg", "heavy_box_rate",
    "blitz_rate_pfr",
]  # fmt: skip
MATCHUP_METRICS = [
    "proe", "play_action_rate", "motion_rate", "screen_rate", "deep_shot_rate", "explosive_rate",
    "epa_per_play", "blitz_rate", "rushers_avg", "heavy_box_rate",
]  # fmt: skip
WEEK_METRICS = sorted(set(GRID_METRICS) | set(MATCHUP_METRICS))


@dataclass
class World:
    paths: DataPaths
    tend: dict[int, Tend] = field(default_factory=dict)
    tg: dict[int, TeamGames] = field(default_factory=dict)
    fc: dict[int, list[dict[str, Any]]] = field(default_factory=dict)  # PC02: week -> rows
    sb: list[dict[str, Any]] = field(default_factory=list)  # PC02: the scoreboard's rows


def make_world(
    paths: DataPaths,
    *,
    games: bool = True,
    teams_table: bool = True,
    waiting: tuple[str, ...] = ("2026_04_ATL_NO",),
    history_2026: bool = False,
    built_at: str = "2026-10-11T00:15:21+00:00",
    extra_games: list[dict[str, Any]] | None = None,
    pc02: bool = False,
) -> World:
    """The standard tree (`pc02=True` adds the forecasts, the scoreboard and the model: see
    `write_pc02`; the PC01 tests run without them):

    - 2026: as-of weeks 1-5 (rows for the full page at 5, the grid / matchup metrics at 1 and 3),
      KC's and MIN's cells pinned, decoys, non-finite numbers, KC's games, FTN waiting for
      `waiting`; no History;
    - 2025: as-of 1-23, History (participation) rows and the grid metrics; 2024, 2023: History;
    - 2022: History in its build.json but before the FTN-charted era (left out);
    - 2019: a pre-FTN build (play-by-play metrics and PFR only);
    - 2027: a folder with tables and no build.json (a build that never finished).
    """
    w = World(paths)

    t26 = Tend(2026)
    t26.fill(5, page_pairs())
    t26.fill(3, week_pairs(WEEK_METRICS))
    t26.fill(1, week_pairs(WEEK_METRICS))
    pin_kc(t26)
    pin_non_finite(t26)
    add_decoys(t26)
    w.tend[2026], w.tg[2026] = t26, kc_games(2026)
    write_season(
        paths, t26, build_doc(2026, 5, history=history_2026, waiting=waiting, built_at=built_at),
        w.tg[2026],
    )  # fmt: skip

    for season in (2025, 2024, 2023):
        t = Tend(season)
        t.fill(23, history_pairs(), windows=("season",))
        t.fill(23, week_pairs(WEEK_METRICS), windows=("season",))
        w.tend[season] = t
        w.tg[season] = TeamGames(season)
    # History: a small sample (route_screen, 2024), a NaN (time to throw, 2023) and decoys in 2025
    # (a play-by-play-style row on a participation metric, and a window the page doesn't read)
    w.tend[2024].pin("KC", "offense", 23, "season", "route_screen", "all", n=5)
    w.tend[2023].pin("KC", "offense", 23, "season", "time_to_throw_avg", "all", value=NAN)
    w.tend[2025].add(
        as_of_week=23, team="KC", side="offense", window="season", metric="personnel_11",
        situation="all", n=500, value=0.999, history_only=False,
    )  # fmt: skip
    w.tend[2025].add(
        as_of_week=23, team="KC", side="offense", window="last4", metric="personnel_12",
        situation="all", n=500, value=0.888, history_only=True,
    )  # fmt: skip
    for season in (2025, 2024, 2023):
        write_season(paths, w.tend[season], build_doc(season, 23, history=True))

    t22 = Tend(2022)
    t22.fill(22, history_pairs(), windows=("season",), teams=["KC"])
    w.tend[2022] = t22
    write_season(paths, t22, build_doc(2022, 22, history=True))

    t19 = Tend(2019)
    t19.fill(22, page_pairs(PBP_ONLY_METRICS + ["blitz_rate_pfr"]))
    w.tend[2019] = t19
    write_season(paths, t19, build_doc(2019, 22))

    t27 = Tend(2027)
    t27.fill(1, week_pairs(["proe"]), teams=["KC"])
    write_season(paths, t27, None)

    if games:
        write_games(paths, extra=extra_games)
    if teams_table:
        write_teams(paths)
    if pc02:
        write_pc02(paths, w)
    return w


def team_with_rank(p, as_of, side, window, metric, situation, avoid=()) -> str:
    """The team holding rank p (0 lowest, 31 highest) of a generated league."""
    return next(
        t for t in TEAMS
        if rank(t, as_of, side, window, metric, situation) == p and t not in avoid
    )  # fmt: skip


def write_custom_games(
    paths: DataPaths, rows: list[tuple[int, str, str, dt.datetime | None]], season: int = 2026
) -> None:
    """A schedule of your own: (week, away, home, kickoff)."""
    pl.DataFrame(
        [
            {
                "game_id": game_id(season, w, a, h),
                "season": season,
                "week": w,
                "away_team": a,
                "home_team": h,
                "kickoff_utc": k,
            }
            for w, a, h, k in rows
        ],
        schema=GAME_SCHEMA,
    ).write_parquet(paths.curated / "games.parquet")


# ---- PC02: forecasts, grades and the model -------------------------------------------------------
# What `nfl playcalling forecast | grade | backtest --promote` write (forecast_runs): per week the
# `week<NN>/forecast.parquet` (+ forecast.json), the season's `playcall_scoreboard.parquet`, and
# `models/playcall/production.json` + `<version>/params.json`. The columns are the runs' own lists
# (a test compares them). Every default row is a small, boring forecast (a shift of 0.3 points,
# under the pages' 1.5-point floor); the interesting ones are pinned by hand in PINS_*.
#
# Defaults per unit: share b2 0.20 (league 0.21, range +-0.05); PROE b2 +0.010 (league
# -0.020, +-0.06); mean b2 4.30 (league 4.20, +-0.5). forecast = b2 + shrink + opp + script;
# shrunk = b2 + shrink.

PC02_VERSION = "2019-2025_20261008T120000Z"
MADE_AT = dt.datetime(2026, 10, 6, 10, 5, tzinfo=dt.UTC)  # Tuesday morning: week 5's forecast
GRADED_AT = dt.datetime(2026, 10, 10, 16, 0, tzinfo=dt.UTC)  # Saturday, after Thursday's game
FC_TARGETS = (
    "proe",
    "run_middle",
    "play_action_rate",
    "blitz_rate",
    "rushers_avg",
    "deep_shot_rate",
)
SHIPPED_TARGETS = FC_TARGETS[:-1]  # deep_shot_rate did not ship
PBP_TARGETS = ("proe", "run_middle", "deep_shot_rate")  # the others wait for FTN's charting

_F, _I, _S, _B = pl.Float64, pl.Int64, pl.String, pl.Boolean
_DT = pl.Datetime("us", "UTC")
FC_SCHEMA = {
    "season": pl.Int32, "week": pl.Int32, "game_id": _S, "team": _S, "opponent": _S, "side": _S,
    "target": _S, "shipped": _B, "forecast": _F, "lo": _F, "hi": _F, "b1": _F, "b2": _F,
    "b3": _F, "b4": _F, "team_term": _F, "opponent_term": _F, "context": _S, "context_team": _S,
    "script_term": _F, "prior": _F, "strength": _F, "league": _F, "n_so_far": _F,
    "games_so_far": _I, "expected_plays": _F, "kickoff_utc": _DT, "spread": _F, "total": _F,
    "line_source": _S, "made_at": _DT, "after_kickoff": _B, "version": _S,
}  # fmt: skip
SB_SCHEMA = {
    "season": pl.Int32, "week": pl.Int32, "game_id": _S, "team": _S, "opponent": _S, "side": _S,
    "target": _S, "shipped": _B, "status": _S, "forecast": _F, "lo": _F, "hi": _F, "b1": _F,
    "b2": _F, "b3": _F, "b4": _F, "actual": _F, "n": _I, "in_range": _B, "err_model": _F,
    "err_b1": _F, "err_b2": _F, "err_b3": _F, "err_b4": _F, "after_kickoff": _B,
    "made_at": _DT, "graded_at": _DT, "version": _S,
}  # fmt: skip
assert list(FC_SCHEMA) == FR.FORECAST_COLUMNS, "the forecast fixture drifted from the runs"
assert list(SB_SCHEMA) == FR.SCOREBOARD_COLUMNS, "the scoreboard fixture drifted from the runs"

FC_UNIT = {
    "share": dict(b2=0.20, b1=0.21, half=0.05, shrink=0.001, opp=0.002, script=0.0),
    "over_expected": dict(b2=0.010, b1=-0.020, half=0.06, shrink=0.001, opp=0.002, script=0.001),
    "mean": dict(b2=4.30, b1=4.20, half=0.50, shrink=0.01, opp=0.02, script=0.0),
}  # fmt: skip
FC_GAMES = {  # the games each week's forecast covers (away, home)
    1: [("KC", "LV"), ("LA", "LAC"), ("BAL", "BUF")],
    3: [("MIN", "CHI"), ("LA", "SEA")],
    4: [("KC", "DEN"), ("ATL", "NO")],
    5: [("ARI", "ATL"), ("MIA", "MIN"), ("NE", "NO"), ("DAL", "DEN"), ("CAR", "CHI"),
        ("DET", "GB"), ("HOU", "IND")],
}  # fmt: skip


def g5(away: str, home: str, week: int = 5) -> str:
    return game_id(2026, week, away, home)


# week 5's pinned rows: (game id, team, target) -> overrides of the unit's defaults. z is the
# shift over the half-width of the 80% range; the week's listed shifts, best first:
#   NO blitz 1.10, MIN rushers 0.90 (MIN's PROE, 0.84, loses the team-game), DEN play-action
#   0.80, IND blitz 0.75 (a shift of exactly 1.5 points), CHI run_middle 0.56, GB PROE 0.50,
#   DET rushers 0.4545 (exactly 0.05), MIA PROE 0.4286;  cut by the cap of 8: NE blitz 0.4167,
#   HOU play-action 0.16, DAL rushers 0.109;  never candidates: DET PROE (1.49 points), CAR
#   rushers (0.0499), HOU blitz (a zero-width range), DAL PROE (no lower end).
PINS_5: dict[tuple[str, str, str], dict[str, Any]] = {
    (g5("NE", "NO"), "NO", "blitz_rate"): dict(b2=0.30, shrink=-0.045, opp=-0.010, half=0.05),
    (g5("MIA", "MIN"), "MIN", "rushers_avg"): dict(b2=4.30, shrink=0.05, opp=0.40, half=0.50),
    (g5("MIA", "MIN"), "MIN", "proe"): dict(
        b2=0.010, shrink=0.002, opp=0.030, script=0.010, half=0.05
    ),
    (g5("DAL", "DEN"), "DEN", "play_action_rate"): dict(b2=0.25, shrink=0.03, opp=0.01, half=0.05),
    (g5("HOU", "IND"), "IND", "blitz_rate"): dict(
        b2=0.25, shrink=-0.020, opp=0.005, half=0.02, n=70, games=2
    ),
    (g5("CAR", "CHI"), "CHI", "run_middle"): dict(b2=0.30, shrink=0.002, opp=-0.030, half=0.05),
    (g5("DET", "GB"), "GB", "proe"): dict(
        b2=0.005, shrink=0.001, opp=0.004, script=0.025, half=0.06
    ),
    (g5("DET", "GB"), "DET", "rushers_avg"): dict(b2=4.30, shrink=0.0, opp=0.05, lo=4.24, hi=4.46),
    (g5("MIA", "MIN"), "MIA", "proe"): dict(
        b2=0.020, shrink=0.0, opp=-0.010, script=-0.020, lo=-0.080, hi=0.060
    ),
    (g5("NE", "NO"), "NE", "blitz_rate"): dict(b2=0.28, shrink=0.0, opp=0.025, lo=0.245, hi=0.365),
    (g5("HOU", "IND"), "HOU", "play_action_rate"): dict(b2=0.22, shrink=0.0, opp=0.02, half=0.125),
    (g5("DAL", "DEN"), "DAL", "rushers_avg"): dict(
        b2=4.30, shrink=0.0, opp=0.06, lo=3.81, hi=4.91
    ),
    # never candidates
    (g5("DET", "GB"), "DET", "proe"): dict(
        b2=0.010, shrink=0.0009, opp=0.0140, script=0.0, lo=0.0245, hi=0.0253
    ),
    (g5("CAR", "CHI"), "CAR", "rushers_avg"): dict(
        b2=4.30, shrink=0.0, opp=0.0499, lo=4.349, hi=4.351
    ),
    (g5("HOU", "IND"), "HOU", "blitz_rate"): dict(b2=0.20, shrink=0.0, opp=0.10, lo=0.30, hi=0.30),
    (g5("DAL", "DEN"), "DAL", "proe"): dict(
        b2=0.010, shrink=0.0, opp=0.040, lo=float("nan"), hi=0.10
    ),
    # a target that did not ship: a huge shift that must never be read
    (g5("DAL", "DEN"), "DEN", "deep_shot_rate"): dict(b2=0.10, shrink=0.0, opp=0.20, half=0.01),
}  # fmt: skip
# the pre-game lines (a team's spread: + = favoured), the same on every row of a team's game
LINES = {
    (g5("MIA", "MIN"), "MIN"): (3.0, 44.0),
    (g5("MIA", "MIN"), "MIA"): (-3.0, 44.0),
    (g5("DET", "GB"), "GB"): (6.5, 47.5),
    (g5("DET", "GB"), "DET"): (-6.5, 47.5),
    # week 1: a half-point favourite (still named) and a one-point underdog (not a pick'em)
    (g5("LA", "LAC", 1), "LAC"): (0.5, 41.0),
    (g5("LA", "LAC", 1), "LA"): (-0.5, 41.0),
    (g5("BAL", "BUF", 1), "BUF"): (-1.0, 38.5),
    (g5("BAL", "BUF", 1), "BAL"): (1.0, 38.5),
}
# week 1: LV's blitz in a new season (no games yet: the comparison is last season's rate)
PINS_1: dict[tuple[str, str, str], dict[str, Any]] = {
    (g5("KC", "LV", 1), "LV", "blitz_rate"): dict(
        b2=0.28, shrink=-0.030, opp=0.010, half=0.05, n=0, games=0
    ),
    # the game script is what moves these two (the lines are in LINES)
    (g5("LA", "LAC", 1), "LAC", "proe"): dict(
        b2=0.010, shrink=0.0, opp=0.0, script=0.020, half=0.045
    ),
    (g5("BAL", "BUF", 1), "BUF", "proe"): dict(
        b2=0.010, shrink=0.0, opp=0.0, script=-0.025, half=0.05
    ),
}  # fmt: skip
# week 3: PROE at the 1.5-point floor (listed) and a hair under it (not), and the sample-size edge
PINS_3: dict[tuple[str, str, str], dict[str, Any]] = {
    (g5("MIN", "CHI", 3), "CHI", "proe"): dict(
        b2=0.010, shrink=0.0, opp=0.014, script=0.001, half=0.03, n=20
    ),
    (g5("MIN", "CHI", 3), "MIN", "proe"): dict(
        b2=0.010, shrink=0.0, opp=0.0139, script=0.001, half=0.03, n=19
    ),
    # how many games a team has played decides the wording: one is "few", three are "games"
    (g5("LA", "SEA", 3), "SEA", "blitz_rate"): dict(
        b2=0.25, shrink=-0.020, opp=0.005, half=0.02, n=30, games=1
    ),
    (g5("LA", "SEA", 3), "LA", "play_action_rate"): dict(
        b2=0.22, shrink=0.030, opp=0.005, half=0.05, games=3
    ),
}  # fmt: skip
# a graded tie (the forecast and season-to-date are equally far from what happened) in week 1,
# and a scoreboard row whose status the pages do not know, also in week 1
TIE_KEY = (g5("BAL", "BUF", 1), "BAL", "proe")
ACTUAL_1 = {TIE_KEY: 0.012}  # forecast 0.014, season to date 0.010: both 0.002 away
ODD_KEY = (g5("KC", "LV", 1), "LV", "run_middle")

# what happened: (game id, team, target) -> the rate (graded rows only)
ACTUAL_4 = {
    (g5("KC", "DEN", 4), "KC", "proe"): 0.050,
    (g5("KC", "DEN", 4), "KC", "play_action_rate"): 0.12,
    (g5("KC", "DEN", 4), "KC", "run_middle"): 0.21,
    (g5("KC", "DEN", 4), "KC", "blitz_rate"): 0.25,
    (g5("KC", "DEN", 4), "KC", "rushers_avg"): 4.60,
    (g5("KC", "DEN", 4), "DEN", "proe"): -0.010,
    (g5("KC", "DEN", 4), "DEN", "play_action_rate"): 0.30,
    (g5("KC", "DEN", 4), "DEN", "run_middle"): 0.15,
    (g5("KC", "DEN", 4), "DEN", "blitz_rate"): 0.18,
    (g5("KC", "DEN", 4), "DEN", "rushers_avg"): 4.10,
    (g5("ATL", "NO", 4), "ATL", "proe"): 0.020,
    (g5("ATL", "NO", 4), "ATL", "run_middle"): 0.25,
    (g5("ATL", "NO", 4), "NO", "proe"): 0.000,
    (g5("ATL", "NO", 4), "NO", "run_middle"): 0.19,
}  # fmt: skip
ACTUAL_5 = {  # Thursday night: the play-by-play targets are in, FTN's charting is not
    (g5("ARI", "ATL"), "ARI", "proe"): 0.030,
    (g5("ARI", "ATL"), "ARI", "run_middle"): 0.26,
    (g5("ARI", "ATL"), "ATL", "proe"): -0.020,
    (g5("ARI", "ATL"), "ATL", "run_middle"): 0.21,
}  # fmt: skip
UNSHIPPED_ACTUAL = 0.35  # what the unshipped deep-shot rows scored: a loud decoy


def _r(x: float) -> float:
    return round(x, 9)


def fc_row(week: int, gid: str, team: str, target: str, **over: Any) -> dict[str, Any]:
    """One forecast row from the unit's defaults and the overrides (b2, shrink, opp, script,
    half, lo, hi, forecast, n, games, shipped, after)."""
    t = PF.get_target(target)
    away, home = gid.split("_")[2:4]
    opp = home if team == away else away
    d = {**FC_UNIT[t.unit], **{k: v for k, v in over.items() if k in FC_UNIT[t.unit]}}
    team_term = d["b2"] + d["shrink"]
    f = over.get("forecast", team_term + d["opp"] + d["script"])
    kick = {game_id(2026, w, a, h): k for w, a, h, k in schedule()}.get(gid)
    lines = LINES.get((gid, team), (None, None))
    return {
        "season": 2026, "week": week, "game_id": gid, "team": team, "opponent": opp,
        "side": t.side, "target": target,
        "shipped": over.get("shipped", target != "deep_shot_rate"),
        "forecast": _r(f), "lo": over.get("lo", _r(f - d["half"])),
        "hi": over.get("hi", _r(f + d["half"])), "b1": d["b1"], "b2": d["b2"],
        "b3": _r(team_term), "b4": _r(team_term + d["opp"]), "team_term": _r(team_term),
        "opponent_term": d["opp"], "context": t.context,
        "context_team": home if t.context == "venue" else opp, "script_term": d["script"],
        "prior": _r(team_term), "strength": 600.0, "league": d["b1"],
        "n_so_far": float(over.get("n", 400)), "games_so_far": int(over.get("games", 4)),
        "expected_plays": 30.0, "kickoff_utc": kick, "spread": lines[0], "total": lines[1],
        "line_source": "nflverse_schedules",
        "made_at": MADE_AT, "after_kickoff": bool(over.get("after", False)),
        "version": PC02_VERSION,
    }  # fmt: skip


def fc_rows(week: int, pins: dict | None = None, *, after: bool = False) -> list[dict[str, Any]]:
    rows = []
    for away, home in FC_GAMES[week]:
        gid = g5(away, home, week)
        for team in (away, home):
            for target in FC_TARGETS:
                over = (pins or {}).get((gid, team, target), {})
                rows.append(fc_row(week, gid, team, target, after=after, **over))
    return sorted(rows, key=lambda r: (r["game_id"], r["side"], r["team"], r["target"]))


def decoy_rows(week: int = 5) -> list[dict[str, Any]]:
    """Rows a page must drop: a code that is no team, a target nobody forecasts, and a game the
    schedule does not have (a real team in it)."""
    base = fc_row(week, g5("MIA", "MIN"), "MIN", "proe", b2=0.0, opp=0.30, half=0.01)
    return [
        {**base, "team": "XYZ"},
        {**base, "target": "bogus_target"},
        {**base, "game_id": g5("KC", "WAS"), "team": "KC", "opponent": "WAS"},
    ]


def sb_rows(rows: list[dict[str, Any]], status, actuals: dict) -> list[dict[str, Any]]:
    """The scoreboard rows `run_grade` would write for these forecast rows: `status(row)`, and a
    graded row's actual from `actuals` (the unshipped deep shots get `UNSHIPPED_ACTUAL`)."""
    out = []
    for r in rows:
        st = status(r)
        row = {k: r[k] for k in SB_SCHEMA if k in r}
        row.update(status=st, actual=None, n=0, in_range=None, graded_at=GRADED_AT)
        row.update({f"err_{k}": None for k in ("model", "b1", "b2", "b3", "b4")})
        if st == "graded":
            a = actuals.get((r["game_id"], r["team"], r["target"]), UNSHIPPED_ACTUAL)
            row.update(actual=a, n=50, in_range=bool(r["lo"] <= a <= r["hi"]))
            row["err_model"] = _r(abs(r["forecast"] - a))
            for k in ("b1", "b2", "b3", "b4"):
                row[f"err_{k}"] = _r(abs(r[k] - a))
        out.append(row)
    return out


def model_doc() -> dict[str, Any]:
    """params.json: the backtest's record per target (the pages read nothing else of it)."""
    bt = {  # target: (model MAE, season-to-date MAE, wins, seasons, coverage, shipped)
        "proe": (0.0412, 0.0431, 5, 7, 0.8123, True),
        "run_middle": (0.1105, 0.1131, 5, 7, 0.7911, True),
        "play_action_rate": (0.0842, 0.0869, 3, 4, 0.8012, True),
        "blitz_rate": (0.0731, 0.0759, 3, 4, 0.8100, True),
        "rushers_avg": (0.512, 0.530, 3, 4, 0.7899, True),
        "deep_shot_rate": (0.0950, 0.0948, 2, 7, 0.8000, False),
    }  # fmt: skip
    return {
        "family": "playcall-model", "version": PC02_VERSION, "schema_version": 1,
        "shipped": [k for k, v in bt.items() if v[5]],
        "targets": {
            k: {
                "shipped": v[5], "fit_seasons": list(range(2019, 2026)),
                "backtest": {
                    "mae": {"model": v[0], "b2": v[1]}, "wins_vs_b2": v[2], "seasons": v[3],
                    "need": 3 if v[3] == 4 else 5, "coverage_80": v[4],
                },
            }
            for k, v in bt.items()
        },
    }  # fmt: skip


def forecast_meta(week: int, rows: list[dict[str, Any]], after: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1, "season": 2026, "week": week, "as_of_week": week,
        "cutoff_utc": "2026-10-07T03:59:59+00:00", "made_at": MADE_AT.isoformat(),
        "version": PC02_VERSION, "shipped": list(SHIPPED_TARGETS),
        "games": len(FC_GAMES[week]), "rows": len(rows), "games_after_kickoff": after,
        "backfill": bool(after), "lines": {"with_line": 0, "sources": []}, "curve": None,
        "notes": [],
    }  # fmt: skip


def write_forecast(paths: DataPaths, week: int, rows: list[dict[str, Any]], meta: dict) -> None:
    d = paths.playcalling / "2026" / f"week{week:02d}"
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=FC_SCHEMA).write_parquet(d / "forecast.parquet")
    (d / "forecast.json").write_text(json.dumps(meta), encoding="utf-8")


def write_model(paths: DataPaths, doc: dict[str, Any] | None = None, version: str | None = None):
    doc = doc or model_doc()
    version = version or doc["version"]
    root = paths.models / "playcall"
    (root / version).mkdir(parents=True, exist_ok=True)
    (root / version / "params.json").write_text(json.dumps(doc), encoding="utf-8")
    (root / "production.json").write_text(json.dumps({"version": version}), encoding="utf-8")


def write_scoreboard(paths: DataPaths, rows: list[dict[str, Any]], season: int = 2026) -> None:
    d = paths.playcalling / str(season)
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=SB_SCHEMA).write_parquet(d / "playcall_scoreboard.parquet")


def write_pc02(paths: DataPaths, w: World | None = None, *, scoreboard: bool = True) -> World:
    """Week 5 (the as-of week): a forecast of seven games, one of them (Thursday's) partly
    graded; week 4: a forecast made after the games (a backfill) and fully graded, FTN waiting
    for ATL at NO; week 3: a forecast nobody graded; week 1: the new season's. The model's
    record. Weeks 2 and 6 have no forecast."""
    w = w or World(paths)
    after4 = [g5(a, h, 4) for a, h in FC_GAMES[4]]
    w.fc[5] = fc_rows(5, PINS_5)
    w.fc[4] = fc_rows(4, after=True)
    w.fc[3] = fc_rows(3, PINS_3, after=True)
    w.fc[1] = fc_rows(1, PINS_1, after=True)
    for week, after in ((5, []), (4, after4), (3, [g5(a, h, 3) for a, h in FC_GAMES[3]]),
                        (1, [g5(a, h, 1) for a, h in FC_GAMES[1]])):  # fmt: skip
        rows = w.fc[week] + (decoy_rows() if week == 5 else [])
        write_forecast(paths, week, rows, forecast_meta(week, rows, after))
    thu = g5("ARI", "ATL")

    def status5(r):
        if r["game_id"] != thu:
            return "not_played"
        return "graded" if r["target"] in PBP_TARGETS else "waiting_ftn"

    def status4(r):
        if r["game_id"] == g5("KC", "DEN", 4) or r["target"] in PBP_TARGETS:
            return "graded"
        return "waiting_ftn"

    def key(r):
        return (r["game_id"], r["team"], r["target"])

    w.sb = sb_rows(w.fc[4], status4, ACTUAL_4) + sb_rows(w.fc[5], status5, ACTUAL_5)
    w.sb += sb_rows([r for r in w.fc[1] if key(r) == TIE_KEY], lambda r: "graded", ACTUAL_1)
    w.sb += sb_rows([r for r in w.fc[1] if key(r) == ODD_KEY], lambda r: "settled", {})
    if scoreboard:
        write_scoreboard(paths, w.sb)
    write_model(paths)
    return w


def finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)
