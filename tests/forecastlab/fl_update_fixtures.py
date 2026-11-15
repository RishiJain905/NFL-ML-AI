"""A tmp tree for the Forecast lab update (FL02, D158): the curated games, the two models'
`production.json`, a week's availability and curves files, the published player projections, the
curated snap counts, the injury update's file, the live scoreboards.

The clock is Tuesday 2026-10-06 10:00 ET (14:00 UTC), week 5, the same moment the control-room
tests pin (`tests/app/app_helpers.py::TUESDAY`). Week 4 is final (a Thursday game, a Sunday game
and Monday night, which kicked off Tuesday 00:15 UTC); week 5 kicks off Thursday night, then two
Sunday games. The week's Tuesday run published at 11:00 UTC on a tree where the curated tables
were refreshed at 10:00 UTC.

`FLTree` writes only what a test asks for. Every file goes where `forecastlab/schema.py` says it
does; their contents are one row, because the plan reads a file's existence, its meta json's
times and the scoreboards' `week` column, nothing else. Nothing here reads D: or runs a model.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.forecastlab import schema as S
from nflengine.paths import DataPaths

UTC = dt.UTC
H = dt.timedelta(hours=1)
DAY = 24 * H
SEASON, WEEK = 2026, 5
NOW = dt.datetime(2026, 10, 6, 14, 0, tzinfo=UTC)  # Tuesday 10 am ET
SAT = dt.datetime(2026, 10, 10, 16, 0, tzinfo=UTC)  # Saturday noon ET

SNAPS = NOW - 4 * H  # 10:00Z: the weekly run's curate step rebuilt the snap counts
PROJ = NOW - 3 * H  # 11:00Z: the week's published player projections
PUBLISHED = NOW - 3 * H  # 11:00Z: the week's digest was published (the run's last step)
MADE = NOW - 2 * H  # 12:00Z: the Tuesday view and the curves were made, after all three
LATER = dt.datetime(2026, 10, 13, 14, 0, tzinfo=UTC)  # the next Tuesday: the calendar's week 6

G4 = ("2026_04_BAL_KC", "2026_04_DEN_LV", "2026_04_ATL_NO")
G5 = ("2026_05_ARI_ATL", "2026_05_MIA_MIN", "2026_05_NE_NO")
K4 = (
    dt.datetime(2026, 10, 1, 0, 15, tzinfo=UTC),
    dt.datetime(2026, 10, 4, 17, 0, tzinfo=UTC),
    dt.datetime(2026, 10, 6, 0, 15, tzinfo=UTC),  # Monday night
)
K5 = (
    dt.datetime(2026, 10, 9, 0, 15, tzinfo=UTC),  # Thursday night
    dt.datetime(2026, 10, 11, 17, 0, tzinfo=UTC),
    dt.datetime(2026, 10, 11, 20, 5, tzinfo=UTC),
)

# (week, game id, away, home, kickoff, result)
GAMES: list[tuple[int, str, str, str, dt.datetime | None, int | None]] = [
    (4, G4[0], "BAL", "KC", K4[0], 3),
    (4, G4[1], "DEN", "LV", K4[1], -7),
    (4, G4[2], "ATL", "NO", K4[2], 10),
    (5, G5[0], "ARI", "ATL", K5[0], None),
    (5, G5[1], "MIA", "MIN", K5[1], None),
    (5, G5[2], "NE", "NO", K5[2], None),
]

GAME_SCHEMA = {
    "game_id": pl.String, "season": pl.Int32, "week": pl.Int32, "away_team": pl.String,
    "home_team": pl.String, "kickoff_utc": pl.Datetime("us", "UTC"), "result": pl.Int32,
}  # fmt: skip

SHIPPED = ("rec_yds-wrte", "receptions-wrte")

Rows = list[tuple[str, bool | None]]  # a week file's (game id, played) rows


def game_ids(week: int) -> tuple[str, ...]:
    """The fixture's game ids of a week (other weeks get one made-up game)."""
    return {4: G4, 5: G5}.get(week, (f"2026_{week:02d}_AAA_BBB",))


def ungraded(week: int) -> Rows:
    """One row per game of the week, no result yet (`played` null): a file just made."""
    return [(g, None) for g in game_ids(week)]


def graded(week: int) -> Rows:
    """One row per game of the week, all of them graded (`played` filled)."""
    return [(g, True) for g in game_ids(week)]


def iso(t: dt.datetime) -> str:
    return t.astimezone(UTC).isoformat()


def touch(path: Path, when: dt.datetime) -> Path:
    """Set a file's modified time (what the plan compares)."""
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


@dataclass
class FLTree:
    paths: DataPaths
    season: int = SEASON

    # ---- where things go (all through the contract's own functions) -----------------------
    def week_dir(self, week: int) -> Path:
        d = S.week_dir(self.paths, self.season, week)
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def season_dir(self) -> Path:
        d = S.season_dir(self.paths, self.season)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- the curated tables -------------------------------------------------------------
    def games(self, rows=None) -> FLTree:
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

    def snaps(self, modified: dt.datetime | None = SNAPS) -> FLTree:
        """`curated/snaps.parquet` (one row), its mtime set to `modified`. The plan does not
        read it (every curate rewrites it): tests use it to show that."""
        f = self.paths.curated / "snaps.parquet"
        f.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"game_id": ["x"]}).write_parquet(f)
        if modified is not None:
            touch(f, modified)
        return self

    # ---- the models ---------------------------------------------------------------------
    def production(
        self,
        family: str,
        *,
        shipped: tuple[str, ...] | None = SHIPPED,
        version: str = "v1",
        raw: str | None = None,
    ) -> FLTree:
        """`models/forecastlab/<family>/production.json`; `raw` writes that text instead."""
        f = S.production_file(self.paths, family)
        f.parent.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            f.write_text(raw, encoding="utf-8")
        else:
            doc: dict[str, Any] = {"version": version}
            if shipped is not None:
                doc["shipped"] = list(shipped)
            f.write_text(json.dumps(doc), encoding="utf-8")
        return self

    def models(self) -> FLTree:
        return self.production("availability", shipped=None).production("curves")

    # ---- a week's files -----------------------------------------------------------------
    def availability(
        self,
        week: int,
        views: dict[str, dt.datetime | None] | None = None,
        *,
        parquet: bool = True,
        meta: bool | str = True,
        rows: Rows | None = None,
    ) -> FLTree:
        """`availability.parquet` and `availability.json` of a week. `views`: {view: made_at};
        `meta`: False writes no json, a string writes that text instead; `rows`: the file's
        (game id, played) rows (default: one ungraded row per game of the week)."""
        folder = self.week_dir(week)
        if parquet:
            rows = ungraded(week) if rows is None else rows
            self.rows_file(folder / "availability.parquet", rows, view="tuesday")
        if isinstance(meta, str):
            (folder / "availability.json").write_text(meta, encoding="utf-8")
        elif meta:
            doc = {"week": week, "views": {v: {"made_at": iso(t)} for v, t in (views or {}).items()
                                           if t is not None}}  # fmt: skip
            (folder / "availability.json").write_text(json.dumps(doc), encoding="utf-8")
        return self

    def curves(
        self,
        week: int,
        made_at: dt.datetime | None = MADE,
        *,
        parquet: bool = True,
        meta: bool | str = True,
        rows: Rows | None = None,
    ) -> FLTree:
        folder = self.week_dir(week)
        if parquet:
            rows = ungraded(week) if rows is None else rows
            self.rows_file(folder / "curves.parquet", rows, target_key="rec_yds-wrte")
        if isinstance(meta, str):
            (folder / "curves.json").write_text(meta, encoding="utf-8")
        elif meta:
            doc = {"week": week, "made_at": iso(made_at) if made_at else None}
            (folder / "curves.json").write_text(json.dumps(doc), encoding="utf-8")
        return self

    @staticmethod
    def rows_file(path: Path, rows: Rows, **extra: str) -> None:
        """A week file with the two columns the grade reads (`game_id`, `played`) and a
        player id per row; `extra` adds constant text columns (`view`, `target_key`)."""
        df = pl.DataFrame(
            {
                "game_id": [g for g, _ in rows],
                "player_id": [f"p{i}" for i in range(len(rows))],
                "played": [p for _, p in rows],
                **{k: [v] * len(rows) for k, v in extra.items()},
            },
            schema_overrides={"game_id": pl.String, "played": pl.Boolean},
        )
        df.write_parquet(path)

    def damage(self, week: int, family: str, how: str) -> FLTree:
        """Replace a week file with one the grade can't use: `garbage` (not a parquet),
        `empty` (no rows), `no_played` (no `played` column), `no_game_id` (no `game_id`)."""
        f = self.week_dir(week) / f"{family}.parquet"
        if how == "garbage":
            f.write_bytes(b"not a parquet file")
        elif how == "empty":
            pl.DataFrame(
                schema={"game_id": pl.String, "player_id": pl.String, "played": pl.Boolean}
            ).write_parquet(f)
        elif how == "no_played":
            pl.DataFrame({"game_id": list(game_ids(week)), "player_id": ["a"] * len(game_ids(week))}
                         ).write_parquet(f)  # fmt: skip
        elif how == "no_game_id":
            pl.DataFrame({"player_id": ["a"], "played": [True]}).write_parquet(f)
        else:
            raise ValueError(how)
        return self

    def drop(self, week: int, *names: str) -> FLTree:
        """Delete files of a week's folder (`availability.json`, `curves.parquet`, ...)."""
        for name in names:
            (S.week_dir(self.paths, self.season, week) / name).unlink()
        return self

    # ---- what the weekly run and the injury update leave ---------------------------------
    def projections(self, week: int, modified: dt.datetime | None = PROJ) -> FLTree:
        """`runs/<S>/week<NN>/predictions_players.parquet`, its mtime set to `modified`."""
        run_dir = self.paths.run_dir(self.season, week)
        run_dir.mkdir(parents=True, exist_ok=True)
        f = run_dir / "predictions_players.parquet"
        pl.DataFrame({"player_id": ["p1"]}).write_parquet(f)
        if modified is not None:
            touch(f, modified)
        return self

    def report(
        self, week: int, modified: dt.datetime | None = PUBLISHED, kind: str = "digest"
    ) -> FLTree:
        """The week's published digest (`reports/<S>/week<NN>-digest.md`), its mtime set to
        `modified`: a later one than a view means the week was run again."""
        f = self.paths.report_path(self.season, week, kind=kind)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# NFL digest: {self.season} week {week}\n", encoding="utf-8")
        if modified is not None:
            touch(f, modified)
        return self

    def injury_update(self, week: int, modified: dt.datetime) -> FLTree:
        run_dir = self.paths.run_dir(self.season, week)
        run_dir.mkdir(parents=True, exist_ok=True)
        f = run_dir / "injury_update.json"
        f.write_text(json.dumps({"material": False}), encoding="utf-8")
        touch(f, modified)
        return self

    # ---- the live scoreboards -----------------------------------------------------------
    def scoreboard(self, family: str, weeks: list[int | None]) -> FLTree:
        """`<family>_scoreboard.parquet`: one row per entry of `weeks` (None: a null week)."""
        df = pl.DataFrame({"week": weeks}, schema={"week": pl.Int32})
        df.write_parquet(self.season_dir / f"{family}_scoreboard.parquet")
        return self


def up_to_date(paths: DataPaths) -> FLTree:
    """Everything done for week 5 on Tuesday: both models promoted, week 5's Tuesday view and
    curves made after the snap counts, the projections and the published digest, week 4's views
    and curves graded (every row of its files has its result)."""
    t = FLTree(paths).games().models().snaps().projections(5).report(5)
    t.availability(5, {"tuesday": MADE}).curves(5, MADE)
    t.availability(4, {"tuesday": MADE - 6 * 24 * H, "saturday": MADE - 3 * 24 * H},
                   rows=graded(4))  # fmt: skip
    t.curves(4, MADE - 3 * 24 * H, rows=graded(4))
    return t


def ungrade(t: FLTree, week: int, *families: str) -> FLTree:
    """Make a week's files of the given families (default: both) ungraded again."""
    for fam in families or ("availability", "curves"):
        if fam == "availability":
            t.availability(week, rows=ungraded(week), meta=True, views={"tuesday": MADE - DAY})
        else:
            t.curves(week, MADE - DAY, rows=ungraded(week))
    return t


def only_curves_due(paths: DataPaths) -> FLTree:
    """The Tuesday tree where only week 5's curves are missing."""
    return up_to_date(paths).drop(5, "curves.parquet", "curves.json")


# ---- reading a plan --------------------------------------------------------------------------
def by_step(steps) -> dict[str, Any]:
    return {s.step: s for s in steps}


def due_steps(steps) -> list[str]:
    return [s.step for s in steps if s.due]


def tree_state(root: Path) -> dict[str, tuple[str, int]]:
    """Every file (sha256, mtime) and folder under `root`: what "wrote nothing" compares."""
    out: dict[str, tuple[str, int]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_dir():
            out[rel + "/"] = ("dir", 0)
        else:
            out[rel] = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
    return out
