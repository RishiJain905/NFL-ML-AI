"""The game calendar (P07) over the real 2024-2026 schedules (`tests/fixtures/`, a column
subset of nflverse's schedules as of 2026-10-04: 2026 results stop at week 4's Thursday)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from nflengine.ops.calendar import (
    EASTERN,
    infer_season,
    plan_week,
    retry_deadline,
    slate,
    thanksgiving,
    with_kickoffs,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "schedules_2024_2026.csv"


@pytest.fixture(scope="module")
def sched() -> pl.DataFrame:
    return pl.read_csv(FIXTURE, schema_overrides={"gametime": pl.String, "gameday": pl.String})


def et(y: int, m: int, d: int, h: int = 10, mi: int = 0) -> dt.datetime:
    return dt.datetime(y, m, d, h, mi, tzinfo=EASTERN)


def test_kickoffs_are_eastern_converted_to_utc_across_dst(sched) -> None:
    k = with_kickoffs(sched)

    def kick(game_id: str) -> dt.datetime:
        return k.filter(pl.col("game_id") == game_id)["kickoff_utc"][0]

    # 2024 opener BAL@KC Thu 20:20 EDT = 00:20 UTC; 2024 week 12 MNF BAL@LAC 20:15 EST = 01:15
    assert kick("2024_01_BAL_KC") == dt.datetime(2024, 9, 6, 0, 20, tzinfo=dt.UTC)
    assert kick("2024_12_BAL_LAC") == dt.datetime(2024, 11, 26, 1, 15, tzinfo=dt.UTC)


def test_thanksgiving_dates() -> None:
    assert thanksgiving(2024) == dt.date(2024, 11, 28)
    assert thanksgiving(2025) == dt.date(2025, 11, 27)
    assert thanksgiving(2026) == dt.date(2026, 11, 26)


@pytest.mark.parametrize(
    ("season", "week", "expect", "absent"),
    [
        # week 1 2024: Thursday opener + Friday night in Sao Paulo
        (2024, 1, {"thursday", "friday", "international", "early_season"}, {"byes"}),
        # Thanksgiving week: three Thursday games + Black Friday
        (2024, 13, {"thursday", "thanksgiving", "black_friday"}, {"friday"}),
        (2025, 13, {"thanksgiving", "black_friday"}, set()),
        # Christmas 2024 fell on a Wednesday; Saturday games in week 17
        (2024, 17, {"christmas", "midweek", "saturday"}, set()),
        # London morning games + byes
        (2024, 5, {"international", "morning_kickoff", "byes"}, set()),
        # last regular-season week has Saturday games
        (2024, 18, {"final_week", "saturday"}, {"byes"}),
        # wild card weekend: the MIN@LA game moved to Glendale (neutral, not abroad)
        (2024, 19, {"playoffs", "saturday", "neutral_site"}, {"international", "byes"}),
        # the Super Bowl is a neutral site but never "international"
        (2024, 22, {"playoffs"}, {"neutral_site", "international"}),
        # 2025: nflverse lists the Dublin / London games under the home stadium; the
        # `game_venues` corrections in stadiums.yaml still find them abroad
        (2025, 4, {"international", "monday_doubleheader"}, set()),
        (2025, 1, {"international", "friday"}, set()),
        # 2026 week 1: a Wednesday (Eastern) game in Melbourne
        (2026, 1, {"midweek", "international", "early_season"}, set()),
    ],
)
def test_slate_special_cases(sched, season, week, expect, absent) -> None:
    sl = slate(sched, season, week)
    assert sl is not None
    assert expect <= set(sl.special), sl.special
    assert not (absent & set(sl.special)), sl.special


def test_slate_byes_and_neutral_sites(sched) -> None:
    sl = slate(sched, 2024, 5)
    assert sl.teams_on_bye == ("DET", "LAC", "PHI", "TEN")
    assert sl.international == ("NYJ@MIN",)
    wc = slate(sched, 2024, 19)
    assert wc.neutral_sites == ("MIN@LA",) and wc.international == ()
    assert wc.game_type == "WC" and wc.games == 6
    assert slate(sched, 2027, 1) is None


def test_tuesday_after_week_12_targets_thanksgiving_week(sched) -> None:
    p = plan_week(sched, et(2024, 11, 26), data_lag_hours=10)
    assert (p.season, p.week, p.previous_week, p.phase) == (2024, 13, 12, "regular")
    assert p.previous_complete and p.previous_final == p.previous_games == 13
    assert p.deadline == et(2024, 11, 28, 12, 30)  # Thanksgiving noon game
    assert p.hours_to_deadline == pytest.approx(50.5)
    assert p.started_games == 0 and not p.deadline_passed


def test_monday_night_not_final_yet_is_not_ready(sched) -> None:
    p = plan_week(sched, et(2024, 11, 25, 22), data_lag_hours=10)
    assert p.week == 13 and not p.previous_complete
    assert p.previous_missing == ("2024_12_BAL_LAC",)
    assert p.retry_until == et(2024, 11, 27, 18)  # Wednesday 18:00 ET
    assert not p.past_retry_window
    # the retry the next morning succeeds (MNF kickoff 20:15 + 10 h lag = 06:15)
    assert plan_week(sched, et(2024, 11, 26, 7), data_lag_hours=10).previous_complete
    # still missing after Wednesday 18:00 -> past the retry window (an alert)
    stuck = sched.with_columns(
        pl.when(pl.col("game_id") == "2024_12_BAL_LAC")
        .then(None)
        .otherwise(pl.col("result"))
        .alias("result")
    )
    p = plan_week(stuck, et(2024, 11, 27, 19))
    assert p.week == 13 and not p.previous_complete and p.past_retry_window


def test_live_mode_uses_results_only(sched) -> None:
    # without a data lag, a result in the snapshot is enough
    p = plan_week(sched, et(2024, 11, 25, 22))
    assert p.previous_complete and not p.simulated


def test_sunday_during_the_week_is_a_late_run(sched) -> None:
    p = plan_week(sched, et(2024, 11, 24, 15))
    assert p.week == 12 and p.deadline_passed and p.started_games > 0
    assert any("late run" in n for n in p.notes)


def test_week_one_has_nothing_to_wait_for_and_is_preseason(sched) -> None:
    p = plan_week(sched, et(2025, 9, 1))
    assert (p.season, p.week, p.phase, p.previous_week) == (2025, 1, "preseason", None)
    assert p.previous_complete and p.retry_until is None
    assert p.deadline == et(2025, 9, 4, 20, 20)


def test_playoffs_and_offseason(sched) -> None:
    p = plan_week(sched, et(2025, 1, 7), data_lag_hours=10)
    assert (p.season, p.week, p.phase) == (2024, 19, "playoffs")
    assert p.previous_complete and p.deadline == et(2025, 1, 11, 16, 30)
    assert plan_week(sched, et(2025, 1, 15)).week == 20
    after_sb = plan_week(sched, et(2025, 2, 11))
    assert after_sb.week is None and after_sb.phase == "offseason"
    assert plan_week(sched, et(2025, 6, 1)).phase == "offseason"


def test_next_playoff_round_not_in_schedule_yet(sched) -> None:
    # Sunday night after week 18: nflverse hasn't added the wild-card games yet
    no_wc = sched.filter(~((pl.col("season") == 2024) & (pl.col("week") > 18)))
    p = plan_week(no_wc, et(2025, 1, 6, 9), season=2024, data_lag_hours=10)
    assert (p.week, p.phase, p.slate) == (19, "playoffs", None)
    assert any("isn't in the schedule yet" in n for n in p.notes)


def test_season_inference(sched) -> None:
    assert infer_season(sched, et(2025, 1, 15)) == 2024  # playoffs belong to 2024
    assert infer_season(sched, et(2025, 6, 1)) == 2024  # offseason
    assert infer_season(sched, et(2025, 8, 30)) == 2025  # 10 days before the opener
    assert infer_season(sched, et(2026, 10, 6)) == 2026


def test_2026_week_5_tuesday_waits_for_week_4(sched) -> None:
    p = plan_week(sched, et(2026, 10, 6), data_lag_hours=10)
    assert (p.season, p.week, p.previous_week) == (2026, 5, 4)
    assert not p.previous_complete  # the fixture stops at week 4's Thursday result
    assert p.deadline == et(2026, 10, 8, 20, 15)
    assert p.slate.teams_on_bye == ("CAR", "KC")


def test_retry_deadline_is_next_wednesday_evening() -> None:
    mnf = et(2024, 11, 25, 20, 15).astimezone(dt.UTC)
    assert retry_deadline(mnf) == et(2024, 11, 27, 18)
    # a Wednesday Christmas game: the window runs to the following Wednesday
    xmas = et(2024, 12, 25, 16, 30).astimezone(dt.UTC)
    assert retry_deadline(xmas) == et(2025, 1, 1, 18)


def test_describe_and_dict_are_plain(sched) -> None:
    p = plan_week(sched, et(2024, 11, 26), data_lag_hours=10)
    text = "\n".join(p.describe())
    assert "2024 week 13" in text and "Thanksgiving" not in text  # tags stay lowercase
    assert "thanksgiving" in text and "Thu 2024-11-28 12:30 ET" in text
    d = p.as_dict()
    assert d["week"] == 13 and d["slate"]["games"] == 16 and d["previous_complete"]


def test_plan_for_a_week_the_calendar_did_not_pick(sched) -> None:
    from nflengine.ops.calendar import plan_for

    # re-running week 12 on the Tuesday of week 13: week 12's own deadline and readiness
    p = plan_for(sched, 2024, 12, et(2024, 11, 26), data_lag_hours=10)
    assert p.week == 12 and p.deadline == et(2024, 11, 21, 20, 15) and p.deadline_passed
    assert p.previous_week == 11 and p.previous_complete and p.started_games == 13
    missing = plan_for(sched, 2027, 3, et(2024, 11, 26))
    assert missing.slate is None and "isn't in the schedule" in missing.notes[0]


def test_retry_window_never_outlasts_the_first_kickoff(sched) -> None:
    # Christmas 2024: week 16 ended Monday, week 17 kicked off Wednesday 13:00 ET
    p = plan_week(sched, et(2024, 12, 24, 10), data_lag_hours=10)
    assert p.week == 17 and p.retry_until == et(2024, 12, 25, 13)
