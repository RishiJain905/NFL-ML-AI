"""The play-calling update (D132): `plan_update` rule by rule, `run_update`'s order and its
skip-after-failure rules, `summary`, and the FTN refresh step.

Everything runs on a tmp tree (`playcall_update_fixtures.py`) at a pinned clock; the five steps
are stubbed where `run_update` imports them, so nothing here ingests, builds, grades, forecasts,
reviews or logs to W&B.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from types import SimpleNamespace

import playcall_update_fixtures as U
import polars as pl
import pytest
from playcall_update_fixtures import (
    BUILT,
    FORECAST_MADE,
    G_MNF,
    G_SUN,
    G_THU,
    MNF,
    NOW,
    VERSION,
    Tree,
    by_step,
    due_steps,
    graded_rows,
    up_to_date_tree,
)

import nflengine.playcalling.update as P
from nflengine.paths import DataPaths

H = dt.timedelta(hours=1)


@pytest.fixture
def paths(tmp_path) -> DataPaths:
    return DataPaths(tmp_path)


def plan(paths, week=5, now=NOW):
    return by_step(P.plan_update(paths, U.SEASON, week, now))


# ---- the plan: nothing due, order, shape ---------------------------------------------------------


def test_a_finished_week_has_nothing_due(paths) -> None:
    up_to_date_tree(paths)
    steps = P.plan_update(paths, 2026, 5, NOW)
    assert (
        [s.step for s in steps] == list(P.STEPS) == ["ftn", "build", "review", "grade", "forecast"]
    )
    assert not P.due(steps) and due_steps(steps) == []
    s = by_step(steps)
    assert s["ftn"].reason == "every earlier game has its FTN charting"
    assert s["build"].reason == "up to date (as of week 5)"
    assert (s["review"].reason, s["review"].week) == ("week 4 is already reviewed", 4)
    assert (s["grade"].reason, s["grade"].week) == ("week 4 is graded", 4)
    assert (s["forecast"].reason, s["forecast"].week) == ("week 5's forecast is up to date", 5)
    assert s["review"].label == "Week 4's 4th-day"[:0] + "Week 4's 4th-down decision review"
    assert s["grade"].label == "Grade week 4's forecast"
    assert s["forecast"].label == "Forecast week 5" and s["build"].week is None


def test_a_step_is_a_plain_frozen_record(paths) -> None:
    up_to_date_tree(paths)
    s = P.plan_update(paths, 2026, 5, NOW)[2]
    assert s.to_dict() == {
        "step": "review", "label": "Week 4's 4th-down decision review", "due": False,
        "reason": "week 4 is already reviewed", "week": 4,
    }  # fmt: skip
    with pytest.raises(AttributeError):
        s.due = True  # type: ignore[misc]


def test_planning_reads_and_never_writes(paths) -> None:
    t = up_to_date_tree(paths)
    t.build(waiting=(G_MNF,))

    def tree() -> dict[str, str]:
        return {
            str(p.relative_to(paths.root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths.root.rglob("*"))
            if p.is_file()
        }

    before = tree()
    mtimes = {p: p.stat().st_mtime_ns for p in paths.root.rglob("*") if p.is_file()}
    P.plan_update(paths, 2026, 5, NOW + 40 * H)
    assert tree() == before
    assert {p: p.stat().st_mtime_ns for p in paths.root.rglob("*") if p.is_file()} == mtimes


def test_a_naive_clock_is_utc(paths) -> None:
    t = up_to_date_tree(paths)
    t.build(waiting=(G_MNF,))
    naive = (MNF + 30 * H).replace(tzinfo=None)
    aware = plan(paths, now=MNF + 30 * H)["ftn"]
    assert plan(paths, now=naive)["ftn"] == aware and aware.due


# ---- step 1: FTN ---------------------------------------------------------------------------------


def waiting_tree(paths, *ids) -> Tree:
    t = up_to_date_tree(paths)
    t.build(waiting=ids or (G_MNF,))
    return t


def test_monday_nights_charting_is_not_due_on_the_tuesday(paths) -> None:
    waiting_tree(paths)
    ftn = plan(paths)["ftn"]  # 13.75 h after kickoff
    assert not ftn.due
    assert ftn.reason == (
        "FTN for ATL at NO usually lands about two days after kickoff: try again "
        "Wednesday afternoon"
    )
    assert ftn.label == "Refresh FTN charting" and ftn.week is None


@pytest.mark.parametrize(
    ("after", "due"),
    [
        (dt.timedelta(hours=29, minutes=59, seconds=59), False),
        (dt.timedelta(hours=30), True),  # the edge: a game exactly FTN_DUE_HOURS old
        (dt.timedelta(hours=30, seconds=1), True),
        (dt.timedelta(hours=42), True),
    ],
)
def test_ftn_is_due_from_thirty_hours_after_kickoff(paths, after, due) -> None:
    waiting_tree(paths)
    assert P.FTN_DUE_HOURS == 30
    ftn = plan(paths, now=MNF + after)["ftn"]
    assert ftn.due is due
    if due:
        assert ftn.reason == ("ATL at NO should have FTN by now (it lands ~2 days after a game)")


GONE = "no FTN for BAL at KC a week after kickoff: its plays count without it"


@pytest.mark.parametrize(
    ("after", "due", "reason"),
    [
        (dt.timedelta(hours=167, minutes=59, seconds=59), True, None),  # a second short: ripe
        (dt.timedelta(hours=168), False, GONE),  # the edge: a week old is given up on
        (dt.timedelta(hours=168, seconds=1), False, GONE),
        (dt.timedelta(hours=400), False, GONE),
    ],
)
def test_ftn_is_given_up_on_a_week_after_kickoff(paths, after, due, reason) -> None:
    t = up_to_date_tree(paths)
    t.build(waiting=(G_THU,))
    assert P.FTN_GIVE_UP_HOURS == 7 * 24
    ftn = plan(paths, now=U.THU + after)["ftn"]
    assert ftn.due is due
    ripe = "BAL at KC should have FTN by now (it lands ~2 days after a game)"
    assert ftn.reason == (reason or ripe)


def test_a_game_given_up_on_does_not_force_a_build(paths) -> None:
    t = up_to_date_tree(paths)
    t.build(waiting=(G_THU,))
    s = plan(paths, now=U.THU + 200 * H)
    assert s["ftn"].reason == GONE and not s["ftn"].due
    assert s["build"].reason == "up to date (as of week 5)"
    assert due_steps(s.values()) == []  # nothing: the plays count without that game's charting


def test_a_game_given_up_on_and_one_that_is_ripe_asks_for_the_ripe_one(paths) -> None:
    t = up_to_date_tree(paths)
    t.build(waiting=(G_THU, G_SUN))
    now = U.THU + 169 * H  # the Thursday game is gone, the Sunday game is 80 h old
    ftn = plan(paths, now=now)["ftn"]
    assert ftn.due
    assert ftn.reason == "DEN at LV should have FTN by now (it lands ~2 days after a game)"
    assert "BAL at KC" not in ftn.reason  # only the game that can still arrive is named
    s = plan(paths, now=now)
    assert s["build"].reason == "after the FTN refresh"  # and the refresh still drives the rest


def test_a_game_given_up_on_and_one_still_pending_waits_for_the_pending_one(paths) -> None:
    late = (4, "2026_04_CAR_CHI", "CAR", "CHI", U.THU + 164 * H, 3)  # 5 h old at `now`
    t = up_to_date_tree(paths).games([*U.GAMES, late])
    t.build(waiting=(G_THU, "2026_04_CAR_CHI"))
    ftn = plan(paths, now=U.THU + 169 * H)["ftn"]
    assert not ftn.due
    assert ftn.reason == (
        "FTN for CAR at CHI usually lands about two days after kickoff: try again "
        "Wednesday afternoon"
    )
    assert "BAL at KC" not in ftn.reason


def test_only_three_given_up_games_are_named(paths) -> None:
    rows = [
        *U.GAMES,
        (4, "2026_04_CAR_CHI", "CAR", "CHI", U.THU, 3),
        (4, "2026_04_DAL_DEN", "DAL", "DEN", U.THU, 3),
        (4, "2026_04_GB_DET", "GB", "DET", U.THU, 3),
    ]
    t = up_to_date_tree(paths).games(rows)
    t.build(waiting=(G_THU, "2026_04_CAR_CHI", "2026_04_DAL_DEN", "2026_04_GB_DET"))
    ftn = plan(paths, now=U.THU + 300 * H)["ftn"]
    assert not ftn.due
    assert ftn.reason == (
        "no FTN for BAL at KC, CAR at CHI, DAL at DEN a week after kickoff: its plays count "
        "without it"
    )


def test_a_game_without_a_kickoff_time_is_never_given_up_on(paths) -> None:
    rows = [(w, g, a, h, None if g == G_THU else k, r) for w, g, a, h, k, r in U.GAMES]
    t = up_to_date_tree(paths).games(rows)
    t.build(waiting=(G_THU,))
    ftn = plan(paths, now=U.THU + 900 * H)["ftn"]
    assert not ftn.due and ftn.reason.startswith("FTN for BAL at KC usually lands")


def test_a_due_ftn_refresh_drags_the_build_grade_and_forecast_with_it(paths) -> None:
    waiting_tree(paths)
    s = plan(paths, now=MNF + 42 * H)
    assert due_steps(s.values()) == ["ftn", "build", "grade", "forecast"]
    assert s["build"].reason == "after the FTN refresh"
    assert s["grade"].reason == "with the new tables"
    assert s["forecast"].reason == "the tables are newer than the forecast"
    assert not s["review"].due  # the review needs only the plays
    # the same tree an hour before it is ripe: nothing
    assert not P.due(list(plan(paths, now=MNF + 29 * H).values()))


def test_only_ripe_games_are_named_and_at_most_three(paths) -> None:
    rows = [
        *U.GAMES,
        (4, "2026_04_CAR_CHI", "CAR", "CHI", U.SUN, 3),
        (4, "2026_04_DAL_DEN", "DAL", "DEN", U.SUN, 3),
        (4, "2026_04_GB_DET", "GB", "DET", U.SUN, 3),
    ]
    t = up_to_date_tree(paths).games(rows)
    t.build(waiting=(G_THU, G_SUN, "2026_04_CAR_CHI", "2026_04_DAL_DEN", G_MNF))
    ftn = plan(paths, now=NOW)["ftn"]  # the Thursday and Sunday games are old, Monday's is not
    assert ftn.due and "ATL at NO" not in ftn.reason
    assert ftn.reason.startswith("BAL at KC, DEN at LV, CAR at CHI should have FTN")
    assert "DAL at DEN" not in ftn.reason  # a fourth name is cut
    # with only the unripe one listed it is the "try again" text
    t.build(waiting=(G_MNF,))
    assert not plan(paths, now=NOW)["ftn"].due


def test_games_that_cannot_be_waited_for_are_ignored(paths) -> None:
    t = up_to_date_tree(paths)
    # a game this week (not earlier), one the schedule doesn't have, a non-string
    t.build(waiting=("2026_05_ARI_ATL", "2026_04_ZZZ_YYY"), raw={})
    assert not plan(paths, now=NOW + 99 * H)["ftn"].due
    t.build(waiting=(), raw={"coverage": {"games_without_ftn": [None, 7, G_MNF]}})
    ftn = plan(paths, now=MNF + 40 * H)["ftn"]
    assert ftn.due and "ATL at NO" in ftn.reason


def test_a_game_without_a_kickoff_time_is_never_ripe(paths) -> None:
    rows = [(w, g, a, h, None if g == G_MNF else k, r) for w, g, a, h, k, r in U.GAMES]
    t = up_to_date_tree(paths).games(rows)
    t.build(waiting=(G_MNF,))
    ftn = plan(paths, now=NOW + 400 * H)["ftn"]
    assert not ftn.due and ftn.reason.startswith("FTN for ATL at NO usually lands")


def test_week_one_has_no_earlier_game_to_wait_for(paths) -> None:
    t = up_to_date_tree(paths)
    t.build(waiting=(G_MNF,))
    assert not plan(paths, week=1, now=NOW + 99 * H)["ftn"].due


# ---- step 2: the tables --------------------------------------------------------------------------


def test_without_a_build_everything_is_built(paths) -> None:
    Tree(paths).games().model()
    s = plan(paths)
    assert s["build"].due and s["build"].reason == "no 2026 tables yet"
    assert s["forecast"].due  # a model and no forecast


@pytest.mark.parametrize(
    ("as_of", "reason"),
    [
        (4, "the tables reach week 4; week 5 needs week 4's games"),
        (1, "the tables reach week 1; week 5 needs week 4's games"),
        (None, "the tables reach week ?; week 5 needs week 4's games"),
    ],
)
def test_tables_that_stop_short_of_the_week_are_rebuilt(paths, as_of, reason) -> None:
    t = up_to_date_tree(paths)
    t.build(as_of=as_of)
    b = plan(paths)["build"]
    assert b.due and b.reason == reason


def test_a_malformed_span_counts_as_no_span(paths) -> None:
    up_to_date_tree(paths).build(as_of=None, raw={"as_of_weeks": [1]})
    assert plan(paths)["build"].due
    up_to_date_tree(paths).build(as_of=None, raw={"as_of_weeks": "1-5"})
    assert plan(paths)["build"].due


def test_tables_past_the_week_are_fine(paths) -> None:
    up_to_date_tree(paths).build(as_of=19)  # a simulation or a later build
    assert not plan(paths)["build"].due


def test_newer_plays_than_the_build_rebuild_it(paths) -> None:
    t = up_to_date_tree(paths)
    t.plays(BUILT + dt.timedelta(seconds=2))
    b = plan(paths)["build"]
    assert b.due and b.reason == "the curated plays are newer than the last build"
    t.plays(BUILT)  # the same instant: not newer
    assert not plan(paths)["build"].due
    t.plays(BUILT - dt.timedelta(seconds=2))
    assert not plan(paths)["build"].due
    # no plays file, or no build time to compare with: nothing to say
    (paths.curated / "plays" / "season=2026.parquet").unlink()
    assert not plan(paths)["build"].due
    t.plays(BUILT + 5 * H).build(built_at=None)
    assert not plan(paths)["build"].due


def test_a_rebuilt_table_set_makes_the_grade_and_forecast_due(paths) -> None:
    t = up_to_date_tree(paths)
    t.plays(BUILT + 3 * H)
    s = plan(paths)
    assert due_steps(s.values()) == ["build", "grade", "forecast"]
    assert s["grade"].reason == "with the new tables"
    assert s["forecast"].reason == "the tables are newer than the forecast"


def test_a_damaged_build_file_is_no_build(paths) -> None:
    t = up_to_date_tree(paths)
    (t.pc / "build.json").write_text("{not json", encoding="utf-8")
    assert plan(paths)["build"].reason == "no 2026 tables yet"
    (t.pc / "build.json").write_text("[1, 2]", encoding="utf-8")
    assert plan(paths)["build"].due


# ---- step 3: the decision review -----------------------------------------------------------------


def test_a_week_that_has_not_been_reviewed_is_reviewed(paths) -> None:
    t = up_to_date_tree(paths)
    from nflengine.live.review import week_dir

    (week_dir(paths, 2026, 4) / "decision_review.json").unlink()
    r = plan(paths)["review"]
    assert r.due and r.week == 4
    assert r.reason == "week 4 hasn't been reviewed yet"
    assert r.label == "Week 4's 4th-down decision review"
    # week 4's review file is what counts: week 5's would not
    t.review(5)
    assert plan(paths)["review"].due


def test_week_one_has_no_review_and_no_grade(paths) -> None:
    t = up_to_date_tree(paths).model()
    s = plan(paths, week=1)
    assert (s["review"].due, s["review"].reason, s["review"].week) == (
        False, "week 1: no week played yet", None,
    )  # fmt: skip
    assert (s["grade"].due, s["grade"].reason) == (False, "week 1: nothing to grade")
    assert s["review"].label == "4th-down decision review" and s["grade"].label == (
        "Grade last week's forecast"
    )
    del t


# ---- step 4: grading last week's forecast --------------------------------------------------------


def test_no_forecast_no_grade(paths) -> None:
    t = up_to_date_tree(paths)
    for name in ("forecast.parquet", "forecast.json"):
        (t.pc / "week04" / name).unlink()
    g = plan(paths)["grade"]
    assert not g.due and g.reason == "week 4 has no forecast to grade" and g.week == 4


def test_a_forecast_file_without_its_meta_is_no_forecast(paths) -> None:
    t = up_to_date_tree(paths)
    (t.pc / "week04" / "forecast.json").unlink()
    assert plan(paths)["grade"].reason == "week 4 has no forecast to grade"


def test_a_forecast_that_was_never_graded_is_graded(paths) -> None:
    t = up_to_date_tree(paths)
    (t.pc / "playcall_scoreboard.parquet").unlink()
    g = plan(paths)["grade"]
    assert g.due and g.reason == "week 4 isn't graded yet"
    # an empty scoreboard for the week is the same; another week's rows are not week 4's
    t.scoreboard(graded_rows(3))
    assert plan(paths)["grade"].reason == "week 4 isn't graded yet"


@pytest.mark.parametrize("status", ["waiting_ftn", "waiting_build", "not_played"])
def test_rows_still_waiting_are_graded_again(paths, status) -> None:
    t = up_to_date_tree(paths)
    t.scoreboard([*graded_rows(4, n=5), (4, status, FORECAST_MADE, VERSION),
                  (4, status, FORECAST_MADE, VERSION)])  # fmt: skip
    g = plan(paths)["grade"]
    assert g.due and g.reason == "2 of week 4's rows were still waiting"


def test_rows_with_no_plays_are_not_waiting(paths) -> None:
    t = up_to_date_tree(paths)
    t.scoreboard([*graded_rows(4, n=5), (4, "no_plays", FORECAST_MADE, VERSION)])
    assert not plan(paths)["grade"].due


def test_a_forecast_made_again_after_grading_is_graded_again(paths) -> None:
    t = up_to_date_tree(paths)
    t.forecast(4, made_at=FORECAST_MADE + 2 * H)  # re-made: the scoreboard graded the 7 am one
    g = plan(paths)["grade"]
    assert g.due and g.reason == "week 4's forecast was made again after grading"
    # a different model version does it too
    t.forecast(4, made_at=FORECAST_MADE, version="2019-2025_20261005T000000Z")
    assert plan(paths)["grade"].due
    # back to the graded one: not due
    t.forecast(4, made_at=FORECAST_MADE)
    assert not plan(paths)["grade"].due
    # one row of the old forecast among current ones is enough to regrade
    t.scoreboard([*graded_rows(4, n=3), (4, "graded", FORECAST_MADE - H, VERSION)])
    assert plan(paths)["grade"].due


def test_a_forecast_without_a_made_time_cannot_be_regraded(paths) -> None:
    t = up_to_date_tree(paths)
    t.forecast(4, made_at=None)
    assert not plan(paths)["grade"].due


def test_the_order_of_the_grade_reasons(paths) -> None:
    """Waiting rows are named before a re-made forecast, which is named before the new tables."""
    t = up_to_date_tree(paths)
    t.plays(BUILT + 3 * H)  # a build is due
    t.forecast(4, made_at=FORECAST_MADE + 2 * H)  # re-made
    t.scoreboard([*graded_rows(4, n=3), (4, "waiting_ftn", FORECAST_MADE, VERSION)])
    assert plan(paths)["grade"].reason == "1 of week 4's rows were still waiting"
    t.scoreboard(graded_rows(4, n=3))
    assert plan(paths)["grade"].reason == "week 4's forecast was made again after grading"
    t.forecast(4, made_at=FORECAST_MADE)
    assert plan(paths)["grade"].reason == "with the new tables"


# ---- step 5: this week's forecast ----------------------------------------------------------------


def test_no_model_no_forecast_however_much_is_missing(paths) -> None:
    t = up_to_date_tree(paths)
    (paths.models / "playcall" / "production.json").unlink()
    (t.pc / "week05" / "forecast.json").unlink()
    f = plan(paths)["forecast"]
    assert not f.due
    assert f.reason == "no promoted playcall-model (`nfl playcalling backtest`)" and f.week == 5


def test_a_week_with_no_games_is_not_forecast(paths) -> None:
    up_to_date_tree(paths).games([r for r in U.GAMES if r[0] != 5])
    f = plan(paths)["forecast"]
    assert not f.due and f.reason == "no week-5 games in the schedule yet"


def test_a_missing_forecast_is_made(paths) -> None:
    t = up_to_date_tree(paths)
    for name in ("forecast.parquet", "forecast.json"):
        (t.pc / "week05" / name).unlink()
    f = plan(paths)["forecast"]
    assert f.due and f.reason == "no forecast for week 5 yet" and f.week == 5
    assert f.label == "Forecast week 5"
    # a parquet with no meta, or a meta with no parquet, is still no forecast
    t.forecast(5, meta=False)
    assert plan(paths)["forecast"].due
    (t.pc / "week05" / "forecast.json").write_text("{}", encoding="utf-8")
    (t.pc / "week05" / "forecast.parquet").unlink()
    assert plan(paths)["forecast"].reason == "no forecast for week 5 yet"


def test_a_forecast_for_games_that_have_started_is_made_but_marked(paths) -> None:
    t = up_to_date_tree(paths)
    for name in ("forecast.parquet", "forecast.json"):
        (t.pc / "week05" / name).unlink()
    f = plan(paths, now=U.G5_FIRST + dt.timedelta(minutes=1))["forecast"]
    assert f.due
    assert f.reason == (
        "no forecast for week 5 yet (some games have started: it's marked as made after kickoff)"
    )


def test_a_forecast_is_never_re_made_after_a_kickoff(paths) -> None:
    t = up_to_date_tree(paths)
    t.forecast(5, made_at=BUILT - 3 * H)  # older than the tables
    t.plays(BUILT + 3 * H)  # and a rebuild is due
    started = U.G5_FIRST + dt.timedelta(minutes=1)
    f = plan(paths, now=started)["forecast"]
    assert not f.due
    assert f.reason == "week 5's games have started: the forecast made 2026-10-06 stays"
    # exactly at the kickoff counts as started; a second before does not
    assert not plan(paths, now=U.G5_FIRST)["forecast"].due
    before = plan(paths, now=U.G5_FIRST - dt.timedelta(seconds=1))["forecast"]
    assert before.due and before.reason == "the tables are newer than the forecast"


def test_a_forecast_older_than_the_tables_is_re_made(paths) -> None:
    t = up_to_date_tree(paths)
    t.forecast(5, made_at=BUILT - dt.timedelta(minutes=1))  # made before the 8 am build
    f = plan(paths)["forecast"]
    assert f.due and f.reason == "the tables are newer than the forecast"
    t.forecast(5, made_at=BUILT)  # the same instant: not older
    assert not plan(paths)["forecast"].due
    t.forecast(5, made_at=None)  # no time to compare
    assert not plan(paths)["forecast"].due


def test_a_build_that_is_due_makes_the_forecast_due(paths) -> None:
    t = up_to_date_tree(paths)
    t.build(as_of=4)
    assert plan(paths)["forecast"].due
    t.build(as_of=5)
    assert not plan(paths)["forecast"].due


def test_week_one_forecasts_without_a_previous_week(paths) -> None:
    rows = [(1, "2026_01_KC_LV", "KC", "LV", dt.datetime(2026, 9, 13, 17, 0, tzinfo=U.UTC), None)]
    t = Tree(paths).games(rows).model().build(as_of=1, built_at=BUILT)
    t.plays(BUILT - H)
    s = plan(paths, week=1, now=dt.datetime(2026, 9, 8, 14, 0, tzinfo=U.UTC))
    assert due_steps(s.values()) == ["forecast"]
    assert s["forecast"].reason == "no forecast for week 1 yet"


def test_damaged_forecast_and_scoreboard_files_do_not_break_the_plan(paths) -> None:
    t = up_to_date_tree(paths)
    (t.pc / "week05" / "forecast.json").write_text("not json", encoding="utf-8")
    (t.pc / "playcall_scoreboard.parquet").write_bytes(b"junk")
    s = plan(paths)
    assert s["forecast"].due and s["forecast"].reason == "no forecast for week 5 yet"
    assert s["grade"].due and s["grade"].reason == "week 4 isn't graded yet"


def test_a_tree_without_a_schedule_still_plans(paths) -> None:
    Tree(paths).model().build(as_of=5, built_at=BUILT)
    s = plan(paths)
    assert not s["forecast"].due and s["forecast"].reason == "no week-5 games in the schedule yet"
    assert not s["ftn"].due
    assert due_steps(s.values()) == ["review"]  # last week's review is the one thing missing


@pytest.mark.parametrize("shape", ["no_rows", "no_season_column", "other_season", "garbage"])
def test_a_schedule_with_nothing_for_this_season_still_plans(paths, shape) -> None:
    t = Tree(paths).model().build(as_of=5, built_at=BUILT)
    f = paths.curated / "games.parquet"
    f.parent.mkdir(parents=True, exist_ok=True)
    if shape == "no_rows":
        pl.DataFrame(schema=U.GAME_SCHEMA).write_parquet(f)
    elif shape == "no_season_column":
        pl.DataFrame({"game_id": ["x"], "week": [5]}).write_parquet(f)
    elif shape == "other_season":
        t.season = 2025
        t.games()
        t.season = 2026
    else:
        f.write_bytes(b"not a parquet file")
    s = plan(paths)
    assert not s["forecast"].due and s["forecast"].reason == "no week-5 games in the schedule yet"
    assert not s["ftn"].due and s["ftn"].reason == "every earlier game has its FTN charting"
    # the same with FTN waiting for a game the (missing) schedule can't place
    t.build(waiting=(G_MNF,))
    assert not plan(paths, now=MNF + 99 * H)["ftn"].due


# ---- run_update ----------------------------------------------------------------------------------


class Stubs:
    """The step functions `run_update` imports, replaced where it imports them."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.fail: dict[str, Exception] = {}
        self.review_result: list = [{"week": 4}]
        self.counts = {"graded": 20, "waiting_ftn": 4}
        self.meta = {"games": 4, "rows": 24, "games_after_kickoff": ["g"]}
        self.build_span: list[int] | None = [1, 5]

        def make(name, ret):
            def fn(*a, **k):
                self.calls.append((name, a, k))
                if name in self.fail:
                    raise self.fail[name]
                return ret() if callable(ret) else ret

            return fn

        import nflengine.live.review as review_mod
        import nflengine.playcalling.build as build_mod
        import nflengine.playcalling.forecast_runs as runs_mod

        monkeypatch.setattr(
            build_mod, "run_build",
            make("build", lambda: {"seasons": [{"as_of_weeks": self.build_span}] if self.build_span
                                   else []}),
        )  # fmt: skip
        monkeypatch.setattr(review_mod, "run_review", make("review", lambda: self.review_result))
        monkeypatch.setattr(
            runs_mod, "run_grade",
            make("grade", lambda: {"counts": self.counts, "frame": pl.DataFrame({"x": range(24)})}),
        )  # fmt: skip
        monkeypatch.setattr(runs_mod, "run_forecast", make("forecast", lambda: {"meta": self.meta}))
        monkeypatch.setattr(P, "_ftn_refresh", make("ftn", "FTN pulled, 9 curated tables rebuilt"))

    @property
    def order(self) -> list[str]:
        return [c[0] for c in self.calls]

    def call(self, name):
        return next(c for c in self.calls if c[0] == name)


@pytest.fixture
def stubs(monkeypatch) -> Stubs:
    return Stubs(monkeypatch)


def all_due(paths) -> list[P.Step]:
    """A plan with every step due (week 5, weeks 4's things missing, FTN ripe)."""
    Tree(paths).games().model().forecast(4, made_at=FORECAST_MADE).build(waiting=(G_MNF,))
    steps = P.plan_update(paths, 2026, 5, MNF + 42 * H)
    assert due_steps(steps) == ["ftn", "build", "review", "grade", "forecast"]
    return steps


def run(paths, steps, **kw):
    seen: list[tuple[str, str]] = []
    ended: list[P.StepResult] = []
    lines: list[str] = []
    kw.setdefault("use_wandb", False)
    kw.setdefault("clock", lambda: NOW)  # Tuesday: before week 5's games
    results = P.run_update(
        2026, 5, steps=steps, log=lambda *a, **k: lines.append(str(a[0])), paths=paths,
        on_step=lambda name, label: seen.append((name, label)), on_step_end=ended.append, **kw,
    )  # fmt: skip
    return results, seen, ended, lines


def test_the_due_steps_run_in_order_with_their_arguments(paths, stubs) -> None:
    steps = all_due(paths)
    results, seen, ended, lines = run(paths, steps, use_wandb=True, launched_by="rishi")
    assert stubs.order == ["ftn", "build", "review", "grade", "forecast"]
    assert [r.step for r in results] == [r.step for r in ended] == stubs.order
    assert [r.status for r in results] == ["ok"] * 5
    # what each step was called with
    _, a, k = stubs.call("ftn")
    assert a[1:3] == (2026, 5)
    _, a, k = stubs.call("build")
    assert a == (2026,) and k["use_wandb"] is True and k["launched_by"] == "rishi"
    assert k["paths"] == paths
    _, a, k = stubs.call("review")
    assert a == (2026, [4]) and k["use_wandb"] is True and k["paths"] == paths
    _, a, k = stubs.call("grade")
    assert a == (2026, 4) and k["launched_by"] == "rishi"
    _, a, k = stubs.call("forecast")
    assert a == (2026, 5) and k["paths"] == paths
    # the start callback gets the plan's label
    assert seen == [(s.step, s.label) for s in steps]
    assert [r.detail for r in results] == [
        "FTN pulled, 9 curated tables rebuilt",
        "tables as of weeks 1-5",
        "week 4 reviewed",
        "graded 20 of 24 rows, 4 still waiting",
        "4 games, 24 forecasts (1 made after kickoff)",
    ]
    assert all(r.seconds >= 0 for r in results)
    assert lines[0].startswith("ftn: ok (") and lines[-1].endswith("(1 made after kickoff)")
    assert (
        len(lines) == 5
        and lines[1].startswith("build: ok (")
        and "tables as of weeks 1-5" in lines[1]
    )


def test_only_the_due_steps_run_whatever_order_the_plan_is_in(paths, stubs) -> None:
    steps = all_due(paths)
    keep = {"forecast", "build"}
    shuffled = [P.Step(s.step, s.label, s.step in keep, s.reason, s.week) for s in reversed(steps)]
    results, seen, *_ = run(paths, shuffled)
    assert stubs.order == ["build", "forecast"]  # STEPS order, not the list's
    assert [r.step for r in results] == ["build", "forecast"] and [n for n, _ in seen] == [
        "build", "forecast",
    ]  # fmt: skip
    assert run(paths, [P.Step(s.step, s.label, False, s.reason, s.week) for s in steps])[0] == []


def test_step_details_for_the_quiet_outcomes(paths, stubs) -> None:
    steps = all_due(paths)
    stubs.review_result = []
    stubs.counts = {"graded": 24}
    stubs.meta = {"games": 4, "rows": 24, "games_after_kickoff": []}
    stubs.build_span = None
    results, *_ = run(paths, steps)
    d = {r.step: r.detail for r in results}
    assert d["review"] == "week 4 has no curated plays yet: nothing reviewed"
    assert d["grade"] == "graded 24 of 24 rows"
    assert d["forecast"] == "4 games, 24 forecasts"
    assert d["build"] == "tables built"
    stubs.counts = {"graded": 10, "not_played": 8, "waiting_build": 2, "no_plays": 4}
    assert run(paths, steps)[0][3].detail == "graded 10 of 24 rows, 10 still waiting"


def test_a_failed_ftn_refresh_skips_what_needs_its_tables(paths, stubs) -> None:
    steps = all_due(paths)
    stubs.fail["ftn"] = RuntimeError("the FTN pull failed (HTTP 503)")
    results, seen, ended, lines = run(paths, steps)
    assert stubs.order == ["ftn", "review"]  # the review needs only the plays
    by = {r.step: r for r in results}
    assert by["ftn"].status == "failed" and by["ftn"].detail == (
        "RuntimeError: the FTN pull failed (HTTP 503)"
    )
    for name in ("build", "grade", "forecast"):
        assert by[name].status == "skipped" and by[name].detail == "skipped: the ftn step failed"
        assert by[name].seconds == 0.0
    assert by["review"].status == "ok"
    assert [r.step for r in results] == list(P.STEPS)  # every due step reported, in order
    assert [n for n, _ in seen] == list(P.STEPS) and len(ended) == 5
    assert "build: skipped (0 s): skipped: the ftn step failed" in lines


def test_a_failed_build_skips_grade_and_forecast_but_not_the_review(paths, stubs) -> None:
    steps = all_due(paths)
    stubs.fail["build"] = ValueError("no curated plays for seasons [2026]")
    results, *_ = run(paths, steps)
    assert stubs.order == ["ftn", "build", "review"]
    s = {r.step: (r.status, r.detail) for r in results}
    assert s["build"] == ("failed", "ValueError: no curated plays for seasons [2026]")
    assert s["grade"] == ("skipped", "skipped: the build step failed")
    assert s["forecast"] == ("skipped", "skipped: the build step failed")
    assert s["review"][0] == "ok" and s["ftn"][0] == "ok"


def test_a_failed_review_never_stops_the_others(paths, stubs) -> None:
    steps = all_due(paths)
    stubs.fail["review"] = RuntimeError("ESPN down")
    results, *_ = run(paths, steps)
    assert stubs.order == list(P.STEPS)
    assert [r.status for r in results] == ["ok", "ok", "failed", "ok", "ok"]


def test_a_failed_grade_still_lets_the_forecast_run(paths, stubs) -> None:
    steps = all_due(paths)
    stubs.fail["grade"] = FileNotFoundError("no forecast for 2026 week 4: run it first")
    results, *_ = run(paths, steps)
    assert stubs.order == list(P.STEPS)
    assert [r.status for r in results] == ["ok", "ok", "ok", "failed", "ok"]
    assert results[3].detail.startswith("FileNotFoundError: no forecast for 2026 week 4")


def test_a_failed_forecast_is_just_reported(paths, stubs) -> None:
    steps = all_due(paths)
    stubs.fail["forecast"] = ValueError("the production model has no target for this season")
    results, *_ = run(paths, steps)
    assert [r.status for r in results] == ["ok", "ok", "ok", "ok", "failed"]


def test_a_failure_only_skips_steps_that_were_due(paths, stubs) -> None:
    steps = all_due(paths)
    only = [P.Step(s.step, s.label, s.step in ("build", "review"), s.reason, s.week) for s in steps]
    stubs.fail["build"] = RuntimeError("boom")
    results, *_ = run(paths, only)
    assert [(r.step, r.status) for r in results] == [("build", "failed"), ("review", "ok")]


def test_error_text_is_one_scrubbed_sentence_of_bounded_length(paths, stubs) -> None:
    steps = all_due(paths)
    stubs.fail["build"] = RuntimeError("login failed: api_key=abc123 x " + "y" * 600)
    results, *_ = run(paths, steps)
    detail = next(r.detail for r in results if r.step == "build")
    assert detail.startswith("RuntimeError: login failed: ") and "abc123" not in detail
    assert len(detail) <= 300


def test_the_callbacks_are_optional(paths, stubs) -> None:
    steps = all_due(paths)
    results = P.run_update(2026, 5, steps=steps, use_wandb=False, log=lambda *a, **k: None,
                           paths=paths)  # fmt: skip
    assert len(results) == 5


# ---- summary -------------------------------------------------------------------------------------


def res(step, status):
    return P.StepResult(step, status, 1.0, "")


def test_summary() -> None:
    assert P.summary([]) == ("ok", "nothing was due")
    assert P.summary([res("build", "ok"), res("forecast", "ok")]) == ("ok", "done: build, forecast")
    assert P.summary([res("build", "ok"), res("review", "failed"), res("grade", "ok")]) == (
        "failed", "done: build, grade; failed: review",
    )  # fmt: skip
    assert P.summary([res("ftn", "failed"), res("build", "skipped"), res("grade", "skipped")]) == (
        "failed", "nothing done; failed: ftn",
    )  # fmt: skip
    assert P.summary([res("ftn", "failed"), res("review", "failed")]) == (
        "failed", "nothing done; failed: ftn, review",
    )  # fmt: skip


# ---- the FTN refresh itself ----------------------------------------------------------------------


def ftn_result(status: str = "ok", parts=("season=2026",), detail: str = ""):
    """One `DatasetResult`-shaped line of `run_ingest`'s answer for FTN."""
    return SimpleNamespace(source="nflverse", dataset="ftn_charting", status=status,
                           parts=list(parts), detail=detail)  # fmt: skip


class Refresh:
    def __init__(self, monkeypatch) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.ingest = [ftn_result()]
        self.checks = [SimpleNamespace(name="pbp", passed=True, level="block")]
        self.tables = ["games", "plays", "ftn_plays"]
        import nflengine.curate.build as cb
        import nflengine.curate.quality as cq
        import nflengine.ingest.runner as ir

        self.block = cq.BLOCK

        def run_ingest(*a, **k):
            self.calls.append(("ingest", a, k))
            return self.ingest, None

        def build_all(*a, **k):
            self.calls.append(("curate", a, k))
            return self.tables

        def run_quality_checks(*a, **k):
            self.calls.append(("quality", a, k))
            return self.checks

        monkeypatch.setattr(ir, "run_ingest", run_ingest)
        monkeypatch.setattr(cb, "build_all", build_all)
        monkeypatch.setattr(cq, "run_quality_checks", run_quality_checks)


def test_the_ftn_refresh_pulls_ftn_only_then_curates_and_checks(paths, monkeypatch) -> None:
    r = Refresh(monkeypatch)
    lines: list[str] = []
    out = P._ftn_refresh(paths, 2026, 5, lines.append)
    assert out == "FTN pulled, 3 curated tables rebuilt, quality checks pass"
    assert [c[0] for c in r.calls] == ["ingest", "curate", "quality"]
    _, _, k = r.calls[0]
    assert k["sources"] == ["nflverse"] and k["nflverse_datasets"] == ["ftn_charting"]
    assert k["seasons"] == [2026] and k["season"] == 2026  # this season only (Sol review)
    assert "odds" not in str(k).lower()  # no Odds API credits
    assert r.calls[2][2] == {"pbp_pending_week": (2026, 5)}  # this week's finished games exempt


def test_a_failed_ftn_pull_stops_before_curating(paths, monkeypatch) -> None:
    r = Refresh(monkeypatch)
    r.ingest = [ftn_result("failed", parts=(), detail="HTTP 503 " + "x" * 300)]
    with pytest.raises(RuntimeError, match=r"the FTN pull failed \(HTTP 503 x+\u2026\)") as e:
        P._ftn_refresh(paths, 2026, 5, print)
    assert len(str(e.value)) < 160  # the detail is cut
    assert [c[0] for c in r.calls] == ["ingest"]


def test_a_blocking_quality_failure_is_an_error_but_a_warning_is_not(paths, monkeypatch) -> None:
    r = Refresh(monkeypatch)
    r.checks = [
        SimpleNamespace(name="ftn_join", passed=False, level="warn"),
        SimpleNamespace(name="pbp", passed=True, level=r.block),
        SimpleNamespace(name="games_unique", passed=False, level=r.block),
    ]
    with pytest.raises(RuntimeError, match=r"blocking quality checks failed: \['games_unique'\]"):
        P._ftn_refresh(paths, 2026, 5, print)
    r.checks = [SimpleNamespace(name="ftn_join", passed=False, level="warn")]
    assert P._ftn_refresh(paths, 2026, 5, print).endswith("quality checks pass")


def test_nothing_here_touches_a_real_data_root(paths) -> None:
    """Guard for the file: the tmp tree is the only root any test above used."""
    assert str(paths.root) != "D:/nfl-ml-data" and Path(paths.root).exists()
    assert U.SEASON == 2026 and isinstance(NOW, dt.datetime)


# ---- Sol review (D132): kickoff at run time, the root before the cap, stale grades, FTN pulls ----


def test_a_forecast_is_kept_when_the_kickoff_passes_during_the_run(paths, stubs) -> None:
    """Planned before Thursday's kickoff (the tables are newer than the forecast), reached after
    it: the saved forecast stays and the step says why; nothing calls run_forecast."""
    t = Tree(paths).games().model().plays(BUILT - H).build(as_of=5, built_at=BUILT)
    t.forecast(4, made_at=FORECAST_MADE).scoreboard(graded_rows(4)).review(4)
    t.forecast(5, made_at=BUILT - H)  # made before the last build
    before = U.G5_FIRST - dt.timedelta(minutes=5)
    steps = P.plan_update(paths, 2026, 5, before)
    assert due_steps(steps) == ["forecast"]
    results, seen, ended, lines = run(paths, steps, clock=lambda: U.G5_FIRST)
    (r,) = results
    assert (r.step, r.status) == ("forecast", "skipped")
    assert r.detail == "skipped: week 5's games have started: the forecast made 2026-10-06 stays"
    assert "forecast" not in stubs.order and ended == results
    # a second earlier, it would have been re-made
    results, *_ = run(paths, steps, clock=lambda: U.G5_FIRST - dt.timedelta(seconds=1))
    assert results[0].status == "ok" and stubs.order == ["forecast"]


def test_kept_forecast_only_guards_an_existing_forecast(paths) -> None:
    Tree(paths).games()
    assert P.kept_forecast(paths, 2026, 5, U.G5_FIRST) is None  # none yet: a backfill may be made
    Tree(paths).forecast(5, made_at=BUILT)
    assert P.kept_forecast(paths, 2026, 5, U.G5_FIRST - dt.timedelta(seconds=1)) is None
    assert "have started" in (P.kept_forecast(paths, 2026, 5, U.G5_FIRST) or "")
    naive = U.G5_FIRST.replace(tzinfo=None)  # a naive clock is UTC
    assert P.kept_forecast(paths, 2026, 5, naive) is not None


def test_a_step_error_loses_the_data_root_before_the_cap(paths, stubs) -> None:
    """A path under the root that crosses the 300-character cap must not leave a fragment."""
    steps = all_due(paths)
    where = paths.root / "playcalling" / "2026" / "playcall_scoreboard.parquet"
    stubs.fail["grade"] = PermissionError("x" * 280 + f" {where} is open in another program")
    results, *_ = run(paths, steps)
    detail = next(r.detail for r in results if r.step == "grade")
    root = str(paths.root)
    assert root not in detail and root.replace("\\", "/") not in detail
    assert root[:8] not in detail and root[:8].replace("\\", "/") not in detail
    assert len(detail) <= 300 and detail.endswith("\u2026")


def test_a_short_step_error_names_the_file_below_the_root(paths, stubs) -> None:
    steps = all_due(paths)
    where = paths.root / "playcalling" / "2026" / "playcall_scoreboard.parquet"
    stubs.fail["grade"] = PermissionError(f"{where} is open")
    results, *_ = run(paths, steps)
    detail = next(r.detail for r in results if r.step == "grade")
    assert detail == "PermissionError: playcalling/2026/playcall_scoreboard.parquet is open"


def scoreboard_graded_at(paths, graded_at: dt.datetime) -> None:
    """Week 4's graded rows with a `graded_at` stamp (the real scoreboard has one)."""
    df = pl.DataFrame(
        [{"week": 4, "status": "graded", "made_at": FORECAST_MADE, "version": VERSION,
          "graded_at": graded_at} for _ in range(3)],
        schema={"week": pl.Int32, "status": pl.String, "made_at": pl.Datetime("us", "UTC"),
                "version": pl.String, "graded_at": pl.Datetime("us", "UTC")},
    )  # fmt: skip
    df.write_parquet(paths.playcalling / "2026" / "playcall_scoreboard.parquet")


def test_a_grade_older_than_the_tables_is_due_again(paths) -> None:
    """A rebuild whose re-grade failed: the next plan still grades (Sol review)."""
    up_to_date_tree(paths)
    scoreboard_graded_at(paths, BUILT - dt.timedelta(minutes=1))
    s = plan(paths)["grade"]
    assert s.due and s.reason == "the tables were rebuilt after week 4's grade"
    scoreboard_graded_at(paths, BUILT)  # graded the second the tables were built: fine
    assert not plan(paths)["grade"].due
    scoreboard_graded_at(paths, BUILT + H)
    s = plan(paths)["grade"]
    assert not s.due and s.reason == "week 4 is graded"


def test_a_partial_ftn_pull_without_this_season_is_an_error(paths, monkeypatch) -> None:
    r = Refresh(monkeypatch)
    r.ingest = [ftn_result("partial", parts=("season=2025",), detail="2026: HTTPError: 503")]
    with pytest.raises(RuntimeError, match=r"the FTN pull got nothing for 2026 \(2026: HTTPError"):
        P._ftn_refresh(paths, 2026, 5, print)
    assert [c[0] for c in r.calls] == ["ingest"]  # nothing curated
    r.calls.clear()
    r.ingest = [ftn_result("ok", parts=())]  # an empty current season is a note, not a problem
    with pytest.raises(RuntimeError, match=r"got nothing for 2026 \(no rows\)"):
        P._ftn_refresh(paths, 2026, 5, print)
    r.calls.clear()
    r.ingest = []
    with pytest.raises(RuntimeError, match="got nothing for 2026"):
        P._ftn_refresh(paths, 2026, 5, print)
