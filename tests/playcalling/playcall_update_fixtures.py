"""A tmp tree for the play-calling update (D132): games, build.json, forecasts, a scoreboard.

The clock is Tuesday 2026-10-06 10:00 ET (14:00 UTC), week 5. Week 4 had a Thursday game, Sunday
games and a Monday-night game (kickoff Tue 00:15 UTC = Mon 8:15 pm ET), so on the Tuesday of the
weekly run it was 13.75 h old (FTN not due), on Wednesday 18:00 UTC it is 41.75 h old (due).

`Tree` writes only what a test asks for; every writer is idempotent. Nothing here reads D: or
runs a step: `plan_update` is read-only and the steps are stubbed by the tests.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.paths import DataPaths

SEASON, WEEK = 2026, 5
NOW = dt.datetime(2026, 10, 6, 14, 0, tzinfo=dt.UTC)  # Tuesday 10 am ET
UTC = dt.UTC
MNF = dt.datetime(2026, 10, 6, 0, 15, tzinfo=UTC)  # Monday night, week 4
SUN = dt.datetime(2026, 10, 4, 17, 0, tzinfo=UTC)  # Sunday 1 pm ET, week 4
THU = dt.datetime(2026, 10, 1, 0, 15, tzinfo=UTC)  # Thursday night, week 4
VERSION = "2019-2025_20261001T120000Z"
BUILT = dt.datetime(2026, 10, 6, 12, 0, tzinfo=UTC)  # the tables were built Tuesday 8 am ET

G_THU = "2026_04_BAL_KC"
G_SUN = "2026_04_DEN_LV"
G_MNF = "2026_04_ATL_NO"

# week 4's games, then week 5's (kickoffs after the clock), week 1's for the first-week cases
GAMES: list[tuple[int, str, str, str, dt.datetime | None, int | None]] = [
    (4, G_THU, "BAL", "KC", THU, 3),
    (4, G_SUN, "DEN", "LV", SUN, -7),
    (4, G_MNF, "ATL", "NO", MNF, 10),
    (5, "2026_05_ARI_ATL", "ARI", "ATL", dt.datetime(2026, 10, 9, 0, 15, tzinfo=UTC), None),
    (5, "2026_05_MIA_MIN", "MIA", "MIN", dt.datetime(2026, 10, 11, 17, 0, tzinfo=UTC), None),
    (5, "2026_05_NE_NO", "NE", "NO", dt.datetime(2026, 10, 11, 20, 5, tzinfo=UTC), None),
]
G5_FIRST = dt.datetime(2026, 10, 9, 0, 15, tzinfo=UTC)

GAME_SCHEMA = {
    "game_id": pl.String, "season": pl.Int32, "week": pl.Int32, "away_team": pl.String,
    "home_team": pl.String, "kickoff_utc": pl.Datetime("us", "UTC"), "result": pl.Int32,
}  # fmt: skip


def iso(t: dt.datetime) -> str:
    return t.astimezone(UTC).isoformat()


@dataclass
class Tree:
    paths: DataPaths
    season: int = SEASON
    notes: dict[str, Any] = field(default_factory=dict)

    # ---- the tables ----------------------------------------------------------------------
    @property
    def pc(self) -> Path:
        return self.paths.playcalling / str(self.season)

    def games(self, rows=None) -> Tree:
        rows = GAMES if rows is None else rows
        df = pl.DataFrame(
            [
                {"game_id": gid, "season": self.season, "week": w, "away_team": a,
                 "home_team": h, "kickoff_utc": k, "result": r}
                for w, gid, a, h, k, r in rows
            ],
            schema=GAME_SCHEMA,
        )  # fmt: skip
        self.paths.curated.mkdir(parents=True, exist_ok=True)
        df.write_parquet(self.paths.curated / "games.parquet")
        return self

    def build(
        self,
        *,
        as_of: int | None = 5,
        built_at: dt.datetime | None = BUILT,
        waiting: tuple[str, ...] = (),
        raw: dict[str, Any] | None = None,
    ) -> Tree:
        """`playcalling/<S>/build.json` as the build writes the parts the update reads."""
        doc: dict[str, Any] = {
            "schema_version": 1,
            "season": self.season,
            "coverage": {"games_without_ftn": list(waiting)},
        }
        if as_of is not None:
            doc["as_of_weeks"] = [1, as_of]
        if built_at is not None:
            doc["built_at"] = iso(built_at)
        doc.update(raw or {})
        self.pc.mkdir(parents=True, exist_ok=True)
        (self.pc / "build.json").write_text(json.dumps(doc), encoding="utf-8")
        return self

    def plays(self, modified: dt.datetime | None) -> Tree:
        """`curated/plays/season=S.parquet` (one row), its mtime set to `modified`."""
        f = self.paths.curated / "plays" / f"season={self.season}.parquet"
        f.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"game_id": ["x"]}).write_parquet(f)
        if modified is not None:
            os.utime(f, (modified.timestamp(), modified.timestamp()))
        return self

    def model(self, version: str = VERSION) -> Tree:
        root = self.paths.models / "playcall"
        root.mkdir(parents=True, exist_ok=True)
        (root / "production.json").write_text(json.dumps({"version": version}), encoding="utf-8")
        return self

    def forecast(
        self,
        week: int,
        *,
        made_at: dt.datetime | None = BUILT - dt.timedelta(hours=1),
        version: str = VERSION,
        parquet: bool = True,
        meta: bool = True,
    ) -> Tree:
        folder = self.pc / f"week{week:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        if parquet:
            pl.DataFrame({"game_id": ["x"], "target": ["proe"]}).write_parquet(
                folder / "forecast.parquet"
            )
        if meta:
            doc = {"week": week, "version": version,
                   "made_at": iso(made_at) if made_at else None}  # fmt: skip
            (folder / "forecast.json").write_text(json.dumps(doc), encoding="utf-8")
        return self

    def scoreboard(self, rows: list[tuple[int, str, dt.datetime, str]]) -> Tree:
        """(week, status, made_at, version) rows of `playcall_scoreboard.parquet`."""
        df = pl.DataFrame(
            [{"week": w, "status": s, "made_at": m, "version": v} for w, s, m, v in rows],
            schema={"week": pl.Int32, "status": pl.String,
                    "made_at": pl.Datetime("us", "UTC"), "version": pl.String},
        )  # fmt: skip
        self.pc.mkdir(parents=True, exist_ok=True)
        df.write_parquet(self.pc / "playcall_scoreboard.parquet")
        return self

    def review(self, week: int) -> Tree:
        from nflengine.live.review import week_dir

        folder = week_dir(self.paths, self.season, week)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "decision_review.json").write_text("{}", encoding="utf-8")
        return self


FORECAST_MADE = BUILT - dt.timedelta(hours=1)  # Tuesday 7 am ET: before the 8 am build


def graded_rows(
    week: int, made_at: dt.datetime = FORECAST_MADE, version: str = VERSION, n: int = 3
):
    return [(week, "graded", made_at, version) for _ in range(n)]


def up_to_date_tree(paths: DataPaths) -> Tree:
    """Everything done for week 5 on Tuesday: tables built (as of 5), week 4 reviewed and graded
    from this forecast, week 5's forecast made after the build, nothing waiting for FTN."""
    t = Tree(paths).games().plays(BUILT - dt.timedelta(hours=1)).model()
    t.build(as_of=5, built_at=BUILT)
    made = BUILT + dt.timedelta(minutes=5)
    t.forecast(4, made_at=FORECAST_MADE).scoreboard(graded_rows(4, FORECAST_MADE))
    t.forecast(5, made_at=made).review(4)
    return t


def due_steps(steps) -> list[str]:
    return [s.step for s in steps if s.due]


def by_step(steps) -> dict[str, Any]:
    return {s.step: s for s in steps}
