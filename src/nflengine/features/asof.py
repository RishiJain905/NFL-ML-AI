"""As-of framework (documentation/04 -> Leakage rules).

Every feature builder works "as of" a (season, week) key: it may only use games that
were played **strictly before** that week. `AsOf(2026, 5)` means "the Tuesday before
week 5": weeks 1-4 of 2026 and every earlier season are visible, nothing else.

Two ways to use it:
- `as_of(season, week)` returns an `AsOfView`, a read-only window over the curated
  tables that filters every read to games before the key. Feature builders that take a
  view cannot see the future by accident, and with `record=True` the view keeps every
  frame it handed out so tests can check them (`assert_view_clean` in `leakage.py`).
- Bulk builders (ratings, Elo, trends) compute many weeks in one pass for speed. They
  use `before_expr` / `asof_keys` directly and are covered by the future-invariance
  test in `leakage.py` instead.

Week-based on purpose (D44): a live run for week N waits until week N-1 is complete (the
readiness check, with retries), so a game counts from the week after its scheduled week
even when it was played late (a postponed Wednesday game). Sources that publish later
than Tuesday carry their own lag (PFR: one week, in `trend_evidence`).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from functools import cached_property

import polars as pl

from nflengine.paths import DataPaths, ensure_data_root


@dataclass(frozen=True, order=True)
class AsOf:
    """Data available before week `week` of `season` (Tuesday-of-week-N view)."""

    season: int
    week: int

    def __post_init__(self) -> None:
        if self.week < 1:
            raise ValueError(f"week must be >= 1, got {self.week}")

    def __str__(self) -> str:
        return f"{self.season}-w{self.week:02d}"


def before_expr(key: AsOf, season_col: str = "season", week_col: str = "week") -> pl.Expr:
    """True for rows from weeks strictly before `key` (any earlier season, or an
    earlier week of the same season).

    Week 0 of the key's own season is excluded: in NGS it holds **season totals**, which
    include weeks on/after the key. Earlier seasons' totals are complete and allowed.
    """
    s, w = pl.col(season_col), pl.col(week_col)
    return (s < key.season) | ((s == key.season) & (w >= 1) & (w < key.week))


def week_cutoffs(games: pl.DataFrame) -> pl.DataFrame:
    """First kickoff per (season, week): the moment week-N data must stop."""
    return games.group_by("season", "week").agg(pl.col("kickoff_utc").min().alias("cutoff_utc"))


def last_asof_week(games: pl.DataFrame, season: int) -> int | None:
    """Latest as-of week that can be computed for `season`.

    A finished season gets one key per week with games (weeks 1..last). For a season in
    progress it is the first week that still has an unplayed game: its own games are
    excluded, and every week before it is complete. A season that hasn't started gets
    week 1 only (the preseason prior). None when the season has no games.

    An unplayed game that kicked off before the latest completed game was cancelled or
    moved (2022 BUF-CIN) and doesn't hold the season open.
    """
    g = games.filter(pl.col("season") == season)
    if g.is_empty():
        return None
    done = g.filter(pl.col("completed"))
    if done.is_empty():
        return 1
    latest = done["kickoff_utc"].max()
    pending = g.filter(~pl.col("completed") & (pl.col("kickoff_utc") > latest))["week"]
    return int(pending.min()) if pending.len() else int(g["week"].max())


def asof_keys(games: pl.DataFrame, seasons: list[int] | range) -> list[AsOf]:
    """Every (season, week) as-of key for `seasons`, in time order."""
    keys: list[AsOf] = []
    for season in seasons:
        last = last_asof_week(games, season)
        if last is not None:
            keys += [AsOf(season, w) for w in range(1, last + 1)]
    return keys


@dataclass
class AsOfView:
    """Read-only window over the curated data as of `key`.

    `games` / `plays` / `table` only ever return rows from weeks strictly before the key.
    Pass `games` (and `plays`) frames to work without the data drive, e.g. in tests.
    """

    key: AsOf
    paths: DataPaths | None = None
    all_games: pl.DataFrame | None = None
    all_plays: pl.DataFrame | None = None
    record: bool = False
    reads: list[tuple[str, pl.DataFrame]] = field(default_factory=list)

    # ---- sources -------------------------------------------------------------------
    @property
    def _paths(self) -> DataPaths:
        if self.paths is None:
            self.paths = ensure_data_root()
        return self.paths

    def _source(self, name: str) -> pl.DataFrame:
        return pl.read_parquet(self._paths.curated / f"{name}.parquet")

    def _games_all(self) -> pl.DataFrame:
        if self.all_games is None:
            self.all_games = self._source("games")
        return self.all_games

    def _keep(self, name: str, df: pl.DataFrame) -> pl.DataFrame:
        if self.record:
            self.reads.append((name, df))
        return df

    # ---- the as-of boundary ----------------------------------------------------------
    @cached_property
    def cutoff_utc(self) -> dt.datetime | None:
        """First kickoff of the key's week (None when that week has no games, e.g. the
        week after a season's last game)."""
        g = self._games_all().filter(
            (pl.col("season") == self.key.season) & (pl.col("week") == self.key.week)
        )
        return g["kickoff_utc"].min() if g.height else None

    # ---- reads ------------------------------------------------------------------------
    def games(self) -> pl.DataFrame:
        """Games strictly before the key (completed or not: a game from an earlier week
        that was never played, such as a cancellation, still shows with no result)."""
        return self._keep("games", self._games_all().filter(before_expr(self.key)))

    def plays(self, columns: list[str] | None = None, min_season: int | None = None):
        """Play-by-play strictly before the key (lazy scan, collected here)."""
        if self.all_plays is not None:
            lf = self.all_plays.lazy()
        else:
            glob = (self._paths.curated / "plays" / "*.parquet").as_posix()
            lf = pl.scan_parquet(glob)
        lf = lf.filter(before_expr(self.key))
        if min_season is not None:
            lf = lf.filter(pl.col("season") >= min_season)
        if columns:
            needed = list(dict.fromkeys([*columns, "season", "week", "game_id"]))
            lf = lf.select(needed)
        return self._keep("plays", lf.collect())

    def table(self, name: str, season_col: str = "season", week_col: str = "week") -> pl.DataFrame:
        """Any curated table keyed by season + week, filtered to weeks before the key."""
        df = self._source(name).filter(before_expr(self.key, season_col, week_col))
        return self._keep(name, df)


def as_of(
    season: int,
    week: int,
    paths: DataPaths | None = None,
    games: pl.DataFrame | None = None,
    plays: pl.DataFrame | None = None,
    record: bool = False,
) -> AsOfView:
    """The data view a live run would have had on the Tuesday before `week`."""
    return AsOfView(AsOf(season, week), paths, games, plays, record)
