"""Leakage test helpers (documentation/04 -> Leakage rules, rule 6).

Used by the tests of every feature family. Two complementary checks:

- `assert_inputs_before(frame, key, games)`: every input row comes from a game that
  kicked off before the first game of `key`'s week. Use it on the frames a builder read
  (an `AsOfView(record=True)` keeps them; `assert_view_clean` checks them all).
- `assert_future_invariant(build, inputs, key)`: scrambles every input row from `key`'s
  week onward, rebuilds, and checks the outputs for keys up to `key` didn't change. This
  covers bulk builders that compute many weeks in one pass (ratings, Elo, trends).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import polars as pl

from nflengine.features.asof import AsOf, AsOfView, before_expr

KEY_COLS = ("season", "week")


class LeakageError(AssertionError):
    """A feature input or output depends on data from on/after the as-of week."""


def _cutoff(key: AsOf, games: pl.DataFrame):
    week = games.filter((pl.col("season") == key.season) & (pl.col("week") == key.week))
    return week["kickoff_utc"].min() if week.height else None


def assert_inputs_before(
    frame: pl.DataFrame, key: AsOf, games: pl.DataFrame, label: str = "input"
) -> None:
    """Fail if any row of `frame` comes from `key`'s week or later.

    Rows are dated through `game_id` (joined to `games.kickoff_utc`) when the frame has
    one, otherwise through (season, week). Frames with neither can't be checked and fail.
    """
    if frame.is_empty():
        return
    if not set(KEY_COLS) <= set(frame.columns) and "game_id" not in frame.columns:
        raise LeakageError(f"{label}: no game_id or season/week column to date rows by")
    if set(KEY_COLS) <= set(frame.columns):
        late = frame.filter(~before_expr(key))
        if late.height:
            first = late.select(KEY_COLS).unique().sort(KEY_COLS).row(0)
            raise LeakageError(
                f"{label}: {late.height} rows on/after {key} (first: season {first[0]}, "
                f"week {first[1]})"
            )
    cutoff = _cutoff(key, games)
    if "game_id" in frame.columns and cutoff is not None:
        dated = (
            frame.select("game_id")
            .unique()
            .join(games.select("game_id", "kickoff_utc"), on="game_id", how="left")
        )
        late = dated.filter(pl.col("kickoff_utc") >= cutoff)
        if late.height:
            raise LeakageError(
                f"{label}: {late.height} games kick off at/after the {key} cutoff "
                f"({cutoff}), e.g. {late['game_id'][0]}"
            )


def assert_view_clean(view: AsOfView) -> None:
    """Every frame an `AsOfView(record=True)` handed out is strictly before its key."""
    if not view.record:
        raise ValueError("create the view with record=True to audit its reads")
    games = view.all_games if view.all_games is not None else view._games_all()
    for name, frame in view.reads:
        assert_inputs_before(frame, view.key, games, label=name)


def _scramble_future(df: pl.DataFrame, key: AsOf, protect: set[str]) -> pl.DataFrame:
    """Replace every numeric value (except keys/ids) in rows on/after `key` with junk."""
    if not set(KEY_COLS) <= set(df.columns):
        return df
    future = ~before_expr(key)
    cols = [
        c
        for c, dtype in df.schema.items()
        if c not in protect and c not in KEY_COLS and (dtype.is_numeric() or dtype == pl.Boolean)
    ]
    junk = []
    for c in cols:
        if df.schema[c] == pl.Boolean:
            junk.append(pl.when(future).then(~pl.col(c)).otherwise(pl.col(c)).alias(c))
        else:
            # Large, sign-flipped and shifted: any dependence on it shows in the output.
            junk.append(
                pl.when(future)
                .then((-pl.col(c) * 7 + 13).cast(df.schema[c], strict=False))
                .otherwise(pl.col(c))
                .alias(c)
            )
    return df.with_columns(junk)


def assert_future_invariant(
    build: Callable[[Mapping[str, pl.DataFrame]], pl.DataFrame],
    inputs: Mapping[str, pl.DataFrame],
    key: AsOf,
    *,
    protect: set[str] | frozenset[str] = frozenset(),
    output_keys: tuple[str, str] = KEY_COLS,
    sort_by: list[str] | None = None,
    tol: float = 1e-9,
) -> pl.DataFrame:
    """Outputs as of `key` (and earlier) must not change when the future changes.

    `build(inputs)` returns a frame with `output_keys` (as-of season, week) columns. Every
    input frame with season/week columns has its numeric values from `key`'s week onward
    scrambled (columns in `protect`, such as ids and team indexes, stay). Rows of the
    output with (season, week) <= key must match the unscrambled build. Returns the
    checked rows of the original build.
    """
    season_col, week_col = output_keys
    upto = (pl.col(season_col) < key.season) | (
        (pl.col(season_col) == key.season) & (pl.col(week_col) <= key.week)
    )
    base = build(inputs).filter(upto)
    scrambled = {k: _scramble_future(v, key, set(protect)) for k, v in inputs.items()}
    if all(scrambled[k].equals(inputs[k]) for k in inputs):
        raise ValueError("nothing to scramble: no input rows on/after the key")
    again = build(scrambled).filter(upto)
    order = sort_by or [
        *output_keys,
        *(c for c in base.columns if c not in output_keys and base.schema[c] == pl.String),
    ]
    base, again = base.sort(order), again.sort(order)
    if base.height == 0:
        raise ValueError(f"build produced no rows up to {key}")
    if base.height != again.height or base.columns != again.columns:
        raise LeakageError(f"output shape changed when the future changed (as of {key})")
    for c in base.columns:
        a, b = base[c], again[c]
        if a.dtype.is_numeric():
            diff = (a.cast(pl.Float64) - b.cast(pl.Float64)).abs()
            same_null = a.is_null() == b.is_null()
            if not same_null.all() or (diff.fill_null(0) > tol).any():
                raise LeakageError(f"column '{c}' as of <= {key} depends on future rows")
        elif not a.equals(b):
            raise LeakageError(f"column '{c}' as of <= {key} depends on future rows")
    return base
