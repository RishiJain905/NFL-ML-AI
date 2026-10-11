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


def make_world(
    paths: DataPaths,
    *,
    games: bool = True,
    teams_table: bool = True,
    waiting: tuple[str, ...] = ("2026_04_ATL_NO",),
    history_2026: bool = False,
    built_at: str = "2026-10-11T00:15:21+00:00",
    extra_games: list[dict[str, Any]] | None = None,
) -> World:
    """The standard tree:

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


def finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)
