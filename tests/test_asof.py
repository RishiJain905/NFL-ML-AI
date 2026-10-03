import datetime as dt

import polars as pl
import pytest
from conftest import make_league

from nflengine.features.asof import (
    AsOf,
    as_of,
    asof_keys,
    before_expr,
    last_asof_week,
    week_cutoffs,
)
from nflengine.features.leakage import (
    LeakageError,
    assert_future_invariant,
    assert_inputs_before,
    assert_view_clean,
)


def test_asof_key_order_and_validation() -> None:
    assert AsOf(2025, 18) < AsOf(2026, 1) < AsOf(2026, 2)
    assert str(AsOf(2026, 5)) == "2026-w05"
    with pytest.raises(ValueError):
        AsOf(2026, 0)


def test_before_expr() -> None:
    df = pl.DataFrame({"season": [2025, 2026, 2026, 2026], "week": [20, 4, 5, 6]})
    out = df.filter(before_expr(AsOf(2026, 5)))
    assert out.rows() == [(2025, 20), (2026, 4)]


def test_current_season_totals_are_not_visible() -> None:
    # NGS week 0 = season totals: last season's are complete, this season's include the
    # future, so only the former may be read as of week 5.
    ngs = pl.DataFrame(
        {"season": [2025, 2026, 2026], "week": [0, 0, 3], "is_season_total": [True, True, False]}
    )
    key = AsOf(2026, 5)
    assert ngs.filter(before_expr(key)).rows() == [(2025, 0, True), (2026, 3, False)]
    games = pl.DataFrame(
        {"game_id": ["g"], "season": [2026], "week": [5], "kickoff_utc": [None]},
        schema_overrides={"kickoff_utc": pl.Datetime("us", "UTC")},
    )
    with pytest.raises(LeakageError):
        assert_inputs_before(ngs, key, games)


def test_last_asof_week_finished_in_progress_and_unstarted(league) -> None:
    games = league["games"]
    assert last_asof_week(games, 2020) == 6  # finished: every week gets a key
    assert last_asof_week(games, 2022) == 5  # week 5 still has an unplayed game
    assert last_asof_week(games, 2030) is None
    unstarted = games.filter(pl.col("season") == 2021).with_columns(
        pl.lit(False).alias("completed")
    )
    assert last_asof_week(unstarted, 2021) == 1
    keys = asof_keys(games, [2021, 2022])
    assert keys[0] == AsOf(2021, 1) and keys[-1] == AsOf(2022, 5) and len(keys) == 11


def test_cancelled_game_does_not_hold_a_season_open(league) -> None:
    g = league["games"].filter(pl.col("season") == 2020)
    # An early game that was never played (like 2022 BUF-CIN) while later weeks were.
    g = g.with_columns(
        pl.when((pl.col("week") == 2) & (pl.col("home_team") == "ATL"))
        .then(False)
        .otherwise(pl.col("completed"))
        .alias("completed")
    )
    assert last_asof_week(g, 2020) == 6


def test_view_filters_every_read_and_records_it(league) -> None:
    view = as_of(2021, 3, games=league["games"], plays=league["plays"], record=True)
    assert view.games()["week"].filter(view.games()["season"] == 2021).max() == 2
    plays = view.plays(columns=["epa"])
    assert set(plays.columns) >= {"epa", "season", "week", "game_id"}
    assert plays.filter(pl.col("season") == 2021)["week"].max() == 2
    cut = week_cutoffs(league["games"]).filter((pl.col("season") == 2021) & (pl.col("week") == 3))[
        "cutoff_utc"
    ][0]
    assert view.cutoff_utc == cut
    assert_view_clean(view)
    # A frame smuggled into the audit log with a future row is caught.
    view.reads.append(("smuggled", league["plays"].filter(pl.col("season") == 2022)))
    with pytest.raises(LeakageError, match="smuggled"):
        assert_view_clean(view)


def test_assert_inputs_before_uses_game_kickoffs(league) -> None:
    games = league["games"]
    key = AsOf(2021, 3)
    ok = league["plays"].filter(before_expr(key))
    assert_inputs_before(ok, key, games)
    # Rows dated only by game_id (no season/week) are checked through kickoff times.
    late_ids = games.filter((pl.col("season") == 2021) & (pl.col("week") == 3)).select("game_id")
    with pytest.raises(LeakageError, match="kick off"):
        assert_inputs_before(late_ids, key, games)
    with pytest.raises(LeakageError, match="no game_id"):
        assert_inputs_before(pl.DataFrame({"x": [1]}), key, games)


def _cum_mean(inputs):
    """A clean as-of builder: mean EPA of everything strictly before each week."""
    p = inputs["plays"]
    keys = p.select("season", "week").unique()
    rows = []
    for s, w in keys.iter_rows():
        past = p.filter(before_expr(AsOf(s, w)))
        rows.append({"season": s, "week": w, "x": past["epa"].mean() if past.height else 0.0})
    return pl.DataFrame(rows)


def _leaky_mean(inputs):
    """Includes the as-of week itself: must be caught."""
    p = inputs["plays"]
    rows = []
    for s, w in p.select("season", "week").unique().iter_rows():
        seen = p.filter(before_expr(AsOf(s, w + 1)))
        rows.append({"season": s, "week": w, "x": seen["epa"].mean()})
    return pl.DataFrame(rows)


def test_future_invariance_helper_passes_clean_and_catches_leaky() -> None:
    lg = make_league(seasons=(2020,), weeks=4, plays_per_side=5, in_progress=None)
    inputs = {"plays": lg["plays"]}
    out = assert_future_invariant(_cum_mean, inputs, AsOf(2020, 3), protect={"play_id"})
    assert out.height == 3
    with pytest.raises(LeakageError):
        assert_future_invariant(_leaky_mean, inputs, AsOf(2020, 3), protect={"play_id"})
    with pytest.raises(ValueError, match="nothing to scramble"):
        assert_future_invariant(_cum_mean, inputs, AsOf(2031, 1))


def test_kickoff_dates_are_timezone_aware(league) -> None:
    assert league["games"]["kickoff_utc"].dtype == pl.Datetime("us", "UTC")
    assert league["games"]["kickoff_utc"].min() > dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
