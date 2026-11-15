"""The Forecast lab update's plan (FL02, D158): `plan_update` rule by rule.

Everything runs on a tmp tree (`fl_update_fixtures.py`) at a pinned clock; the plan only reads
files, so nothing here runs a model. The three steps are availability (the Tuesday and Saturday
views), curves and grade; each case below pins the reason a step is due, or skipped.
"""

from __future__ import annotations

import datetime as dt

import fl_update_fixtures as F
import polars as pl
import pytest
from fl_update_fixtures import (
    K5,
    MADE,
    NOW,
    FLTree,
    H,
    by_step,
    due_steps,
    only_curves_due,
    ungrade,
    up_to_date,
)

import nflengine.forecastlab.update as U
from nflengine.forecastlab import schema as S
from nflengine.paths import DataPaths

LATE_AVAIL = " (some games have started: their rows are kept or marked)"
LATE_CURVES = " (some games have started: they're marked as made after kickoff)"
DAY = 24 * H


@pytest.fixture
def paths(tmp_path) -> DataPaths:
    return DataPaths(tmp_path)


def plan(paths, week=5, now=NOW):
    return by_step(U.plan_update(paths, F.SEASON, week, now))


def games_after(rows_k5):
    """Week 4 as usual and week 5 with the given kickoffs (None: not in the schedule yet)."""
    rows = [r for r in F.GAMES if r[0] == 4]
    for (w, gid, a, h, _k, r), k in zip([x for x in F.GAMES if x[0] == 5], rows_k5, strict=True):
        rows.append((w, gid, a, h, k, r))
    return rows


# ---- the plan: nothing due, order, shape ----------------------------------------------------


def test_a_finished_week_has_nothing_due(paths) -> None:
    up_to_date(paths)
    steps = U.plan_update(paths, 2026, 5, NOW)
    assert [s.step for s in steps] == list(U.STEPS) == ["availability", "curves", "grade"]
    assert not U.due(steps) and due_steps(steps) == []
    s = by_step(steps)
    assert (s["availability"].label, s["availability"].reason) == (
        "Availability for week 5", "up to date (Tuesday view)",
    )  # fmt: skip
    assert (s["curves"].label, s["curves"].reason) == (
        "Outcome curves for week 5", "week 5's curves are up to date",
    )  # fmt: skip
    assert (s["grade"].label, s["grade"].reason, s["grade"].week) == (
        "Grade week 4", "week 4 is graded", 4,
    )  # fmt: skip
    assert s["availability"].week == s["curves"].week == 5


def test_due_keeps_the_plans_order(paths) -> None:
    FLTree(paths).games().models().projections(5)  # nothing made, week 4 has nothing to grade
    steps = U.plan_update(paths, 2026, 5, NOW)
    assert [s.step for s in U.due(steps)] == ["availability", "curves"]


def test_a_step_is_a_plain_frozen_record(paths) -> None:
    up_to_date(paths).drop(5, "availability.json", "availability.parquet")
    av = U.plan_update(paths, 2026, 5, NOW)[0]
    assert av.to_dict() == {
        "step": "availability", "label": "Availability for week 5 (Tuesday view)", "due": True,
        "reason": "no Tuesday view yet", "week": 5, "views": ("tuesday",), "parts": (), "jobs": (),
    }  # fmt: skip
    with pytest.raises(AttributeError):
        av.due = False  # type: ignore[misc]


def test_planning_reads_and_never_writes(paths) -> None:
    t = up_to_date(paths)
    t.injury_update(5, NOW - H)
    ungrade(t, 4, "curves")  # week 4's curves ungraded: grade is due too
    before = F.tree_state(paths.root)
    assert due_steps(U.plan_update(paths, 2026, 5, NOW + 40 * H)) != []
    U.plan_update(paths, 2026, 6, NOW + 9 * DAY)
    assert F.tree_state(paths.root) == before


def test_planning_an_empty_data_root_creates_nothing(tmp_path) -> None:
    steps = U.plan_update(DataPaths(tmp_path), 2026, 5, NOW)
    assert due_steps(steps) == []  # no model, no games: nothing to do
    assert list(tmp_path.rglob("*")) == []  # not even a forecastlab/ folder


def test_a_naive_clock_is_utc(paths) -> None:
    up_to_date(paths).drop(5, "availability.json", "availability.parquet")
    t = K5[0] + H  # Thursday night is under way
    aware = plan(paths, now=t)["availability"]
    assert aware.due and aware.reason.endswith(LATE_AVAIL)
    naive = t.replace(tzinfo=None)
    assert plan(paths, now=naive)["availability"] == aware


# ---- availability: models, games -------------------------------------------------------------


def test_no_promoted_availability_model(paths) -> None:
    up_to_date(paths).drop(5, "availability.json", "availability.parquet")
    S.production_file(paths, "availability").unlink()
    av = plan(paths)["availability"]
    assert (av.due, av.reason) == (False, "no promoted availability model yet")
    assert (av.label, av.week, av.views) == ("Availability for week 5", 5, ())
    assert plan(paths)["curves"].due is False  # the other family has its model: nothing due, ok


def test_each_family_reports_its_own_missing_model(paths) -> None:
    up_to_date(paths).drop(5, "availability.json", "availability.parquet")
    FLTree(paths).drop(5, "curves.json", "curves.parquet")  # both would be due with a model
    S.production_file(paths, "availability").unlink()
    S.production_file(paths, "curves").unlink()
    s = plan(paths)
    assert s["availability"].reason == "no promoted availability model yet"
    assert s["curves"].reason == "no promoted curves model yet (`nfl forecastlab backtest`)"
    assert not s["availability"].due and not s["curves"].due


def test_no_games_in_the_schedule_yet(paths) -> None:
    up_to_date(paths).drop(5, "availability.json", "availability.parquet")
    F.FLTree(paths).games([r for r in F.GAMES if r[0] == 4])  # week 5 isn't scheduled yet
    av = plan(paths)["availability"]
    assert (av.due, av.reason) == (False, "no week-5 games in the schedule yet")
    # nor is there a games table at all
    (paths.curated / "games.parquet").unlink()
    assert plan(paths)["availability"].reason == "no week-5 games in the schedule yet"


def test_only_this_seasons_games_count(paths) -> None:
    t = up_to_date(paths).drop(5, "availability.json", "availability.parquet")
    other = [(w, gid, a, h, k, r) for w, gid, a, h, k, r in F.GAMES if w == 5]
    FLTree(paths, season=2025).games(other)  # a table of 2025 week-5 games only
    assert t.season == 2026
    assert plan(paths)["availability"].reason == "no week-5 games in the schedule yet"


# ---- availability: the Tuesday view ----------------------------------------------------------


def test_no_tuesday_view_yet(paths) -> None:
    up_to_date(paths).drop(5, "availability.json", "availability.parquet")
    av = plan(paths)["availability"]
    assert av.due and av.views == ("tuesday",) and av.parts == ()
    assert av.label == "Availability for week 5 (Tuesday view)"
    assert av.reason == "no Tuesday view yet" and av.week == 5  # none of the games has started


def test_a_week_file_without_views_still_has_no_tuesday_view(paths) -> None:
    up_to_date(paths).availability(5, {})  # the parquet is there, the json lists no view
    av = plan(paths)["availability"]
    assert (av.due, av.views, av.reason) == (True, ("tuesday",), "no Tuesday view yet")


def test_tuesday_view_made_and_nothing_new_is_up_to_date(paths) -> None:
    up_to_date(paths)
    av = plan(paths)["availability"]
    assert (av.due, av.reason, av.views) == (False, "up to date (Tuesday view)", ())


@pytest.mark.parametrize(
    ("published", "due"),
    [
        (MADE + dt.timedelta(seconds=1), True),
        (MADE, False),  # the same moment is not newer
        (MADE - dt.timedelta(seconds=1), False),
        (None, False),  # no published digest at all
    ],
)
def test_the_week_run_again_after_the_tuesday_view(paths, published, due) -> None:
    t = up_to_date(paths)
    paths.report_path(2026, 5).unlink()
    if published is not None:
        t.report(5, published)
    av = plan(paths)["availability"]
    assert av.due is due
    if due:
        assert (av.views, av.reason) == (
            ("tuesday",),
            "the week was run again after the Tuesday view",
        )
        assert av.label == "Availability for week 5 (Tuesday view)"
    else:
        assert av.reason == "up to date (Tuesday view)"


@pytest.mark.parametrize("snaps", [MADE + H, MADE + 30 * DAY, None])
def test_newer_snap_counts_no_longer_remake_the_tuesday_view(paths, snaps) -> None:
    """Every curate rewrites `snaps.parquet`: its time says nothing about new data, so the plan
    doesn't read it (a missing table or a newer one change nothing)."""
    t = up_to_date(paths)
    (paths.curated / "snaps.parquet").unlink()
    if snaps is not None:
        t.snaps(snaps)
    av = plan(paths)["availability"]
    assert (av.due, av.reason) == (False, "up to date (Tuesday view)")


def test_a_rerun_week_is_ignored_once_every_game_has_kicked_off(paths) -> None:
    up_to_date(paths).report(5, MADE + H)
    assert plan(paths)["availability"].due is True
    av = plan(paths, now=K5[2] + H)["availability"]
    assert (av.due, av.reason) == (False, "up to date (Tuesday view)")


def test_a_rerun_week_with_some_games_started_says_so(paths) -> None:
    up_to_date(paths).report(5, MADE + H)
    av = plan(paths, now=K5[0] + H)["availability"]
    assert av.due and av.reason == "the week was run again after the Tuesday view" + LATE_AVAIL


def test_only_the_weeks_own_digest_counts(paths) -> None:
    """Another week's digest, or the Saturday addendum (`...-injury-update.md`), is not the
    run that made the Tuesday view."""
    t = up_to_date(paths)
    t.report(4, MADE + H).report(6, MADE + H).report(5, MADE + H, kind="injury-update")
    assert plan(paths)["availability"].due is False


def test_a_rerun_week_does_not_remake_the_tuesday_view_after_the_injury_update(paths) -> None:
    """After the injury update the Saturday view is the one that's due; the Tuesday view, made
    before the report existed, is left as it is."""
    up_to_date(paths).report(5, MADE + H).injury_update(5, MADE + 2 * H)
    av = plan(paths)["availability"]
    assert av.views == ("saturday",)
    assert "Tuesday" not in av.reason


# ---- availability: the Saturday view ---------------------------------------------------------


def test_the_injury_update_makes_the_saturday_view_due(paths) -> None:
    up_to_date(paths).injury_update(5, MADE + 2 * H)
    av = plan(paths)["availability"]
    assert av.due and av.views == ("saturday",)
    assert av.label == "Availability for week 5 (Saturday view)"
    assert av.reason == "the injury update has run: the Saturday view isn't made yet"


@pytest.mark.parametrize(
    ("update", "due"),
    [
        (MADE + 3 * H, True),  # the injury update ran again after the Saturday view
        (MADE + dt.timedelta(seconds=1), True),
        (MADE, False),
        (MADE - 3 * H, False),
    ],
)
def test_the_injury_update_ran_again(paths, update, due) -> None:
    t = up_to_date(paths)
    t.availability(5, {"tuesday": MADE - 2 * H, "saturday": MADE}).injury_update(5, update)
    av = plan(paths)["availability"]
    assert av.due is due
    if due:
        assert (av.views, av.reason) == (
            ("saturday",),
            "the injury update ran again after the Saturday view",
        )
    else:
        assert av.reason == "up to date (Tuesday and Saturday view)"


def test_both_views_can_be_due_at_once(paths) -> None:
    up_to_date(paths).drop(5, "availability.json", "availability.parquet")
    FLTree(paths).injury_update(5, NOW - H)
    av = plan(paths)["availability"]
    assert av.due and av.views == ("tuesday", "saturday")
    assert av.label == "Availability for week 5 (Tuesday + Saturday view)"
    assert av.reason == (
        "no Tuesday view yet; the injury update has run: the Saturday view isn't made yet"
    )


def test_a_saturday_view_without_a_tuesday_one_asks_for_the_tuesday_view_too(paths) -> None:
    up_to_date(paths).availability(5, {"saturday": MADE}).injury_update(5, MADE - H)
    av = plan(paths)["availability"]
    assert (av.due, av.views, av.reason) == (True, ("tuesday",), "no Tuesday view yet")


# ---- availability: games that have kicked off ------------------------------------------------


def test_some_games_started_says_so(paths) -> None:
    up_to_date(paths).injury_update(5, K5[0])
    av = plan(paths, now=K5[0] + H)["availability"]  # Thursday night is under way
    assert av.due and av.views == ("saturday",)
    assert av.reason == ("the injury update has run: the Saturday view isn't made yet" + LATE_AVAIL)


@pytest.mark.parametrize(
    ("offset", "late"),
    [(dt.timedelta(seconds=-1), False), (dt.timedelta(0), True), (dt.timedelta(seconds=1), True)],
)
def test_a_game_at_the_clocks_moment_has_started(paths, offset, late) -> None:
    up_to_date(paths).injury_update(5, MADE + H)
    av = plan(paths, now=K5[0] + offset)["availability"]
    assert av.due is True and av.reason.endswith(LATE_AVAIL) is late


def test_every_game_kicked_off_leaves_the_saved_views_alone(paths) -> None:
    up_to_date(paths).injury_update(5, MADE + H)
    sunday_night = K5[2] + 4 * H
    av = plan(paths, now=sunday_night)["availability"]
    assert av.due is False and av.views == ()
    assert av.reason == "week 5's games have all kicked off: the saved views stay"
    assert av.label == "Availability for week 5"


def test_a_game_without_a_kickoff_time_has_not_started(paths) -> None:
    up_to_date(paths).injury_update(5, MADE + H)
    FLTree(paths).games(games_after([K5[0], K5[1], None]))
    av = plan(paths, now=K5[2] + 4 * H)["availability"]  # two games played, one has no time yet
    assert (av.due, av.views) == (True, ("saturday",))
    assert av.reason.endswith(LATE_AVAIL)


def test_a_week_with_no_views_at_all_is_still_made_after_the_games(paths) -> None:
    """No file at all: the model still makes a view and marks the rows made after kickoff."""
    up_to_date(paths).drop(5, "availability.json", "availability.parquet")
    av = plan(paths, now=K5[2] + 4 * H)["availability"]
    assert (av.due, av.views) == (True, ("tuesday",))
    assert av.reason == "no Tuesday view yet" + LATE_AVAIL


def test_a_week_file_without_its_json_stays_after_the_games(paths) -> None:
    up_to_date(paths).drop(5, "availability.json")
    av = plan(paths, now=K5[2] + 4 * H)["availability"]
    assert av.due is False
    assert av.reason == "week 5's games have all kicked off: the saved views stay"


# ---- curves ----------------------------------------------------------------------------------


def test_no_promoted_curves_model(paths) -> None:
    only_curves_due(paths)
    S.production_file(paths, "curves").unlink()
    cv = plan(paths)["curves"]
    assert (cv.due, cv.week) == (False, 5)
    assert cv.reason == "no promoted curves model yet (`nfl forecastlab backtest`)"
    assert cv.label == "Outcome curves for week 5"


@pytest.mark.parametrize("shipped", [(), None])
def test_no_curve_target_passed_its_ship_rule(paths, shipped) -> None:
    only_curves_due(paths).production("curves", shipped=shipped)  # [] or no `shipped` key
    cv = plan(paths)["curves"]
    assert (cv.due, cv.reason) == (False, "no curve target passed its ship rule")


def test_no_player_projections_yet(paths) -> None:
    only_curves_due(paths)
    (paths.run_dir(2026, 5) / "predictions_players.parquet").unlink()
    cv = plan(paths)["curves"]
    assert (cv.due, cv.reason) == (False, "week 5 has no player projections yet (Run week 5)")


def test_no_curves_made_yet(paths) -> None:
    only_curves_due(paths)
    cv = plan(paths)["curves"]
    assert (cv.due, cv.reason, cv.week) == (True, "no curves for week 5 yet", 5)
    assert cv.views == () and cv.parts == ()


def test_curves_made_after_some_games_started_are_marked_so(paths) -> None:
    only_curves_due(paths)
    cv = plan(paths, now=K5[0] + H)["curves"]
    assert (cv.due, cv.reason) == (True, "no curves for week 5 yet" + LATE_CURVES)
    # all of them, too: the model still makes the (marked) curves once
    cv = plan(paths, now=K5[2] + H)["curves"]
    assert (cv.due, cv.reason) == (True, "no curves for week 5 yet" + LATE_CURVES)


@pytest.mark.parametrize(
    ("projections", "due"),
    [
        (MADE + dt.timedelta(seconds=1), True),
        (MADE, False),  # the same moment is not newer
        (MADE - H, False),
    ],
)
def test_projections_newer_than_the_curves(paths, projections, due) -> None:
    up_to_date(paths).projections(5, projections)
    cv = plan(paths)["curves"]
    assert cv.due is due
    assert cv.reason == (
        "the player projections are newer than the curves" if due
        else "week 5's curves are up to date"
    )  # fmt: skip


def test_newer_projections_are_ignored_once_every_game_has_kicked_off(paths) -> None:
    up_to_date(paths).projections(5, MADE + H)
    assert plan(paths)["curves"].due is True
    cv = plan(paths, now=K5[2] + H)["curves"]
    assert (cv.due, cv.reason) == (False, "week 5's curves are up to date")


def test_curves_without_their_json_were_never_finished(paths) -> None:
    """A parquet with no meta behind it is an interrupted write: the step is due again."""
    up_to_date(paths).drop(5, "curves.json")
    cv = plan(paths)["curves"]
    assert (cv.due, cv.reason, cv.week) == (True, "no curves for week 5 yet", 5)
    # also once every game has kicked off (the model marks the rows made after kickoff)
    cv = plan(paths, now=K5[2] + H)["curves"]
    assert (cv.due, cv.reason) == (True, "no curves for week 5 yet" + LATE_CURVES)


@pytest.mark.parametrize(
    "meta",
    ["", "{not json", "[]", '"made"', "{}", '{"made_at": null}', '{"made_at": 5}',
     '{"made_at": "last tuesday"}', '{"made_at": ["2026-10-06T12:00:00Z"]}'],
)  # fmt: skip
def test_curves_whose_json_has_no_readable_made_at_were_never_finished(paths, meta) -> None:
    up_to_date(paths).curves(5, meta=meta)
    cv = plan(paths)["curves"]
    assert (cv.due, cv.reason) == (True, "no curves for week 5 yet")


def test_curves_with_a_readable_made_at_are_judged_by_it(paths) -> None:
    up_to_date(paths).curves(5, meta='{"made_at": "2026-10-06T12:00:00Z"}')
    assert plan(paths)["curves"].due is False
    FLTree(paths).projections(5, MADE + H)
    assert plan(paths)["curves"].reason == "the player projections are newer than the curves"


def test_the_curves_checks_run_in_a_fixed_order(paths) -> None:
    """No model beats everything; then the ship rule; then projections; then the files."""
    t = FLTree(paths).games().production("curves", shipped=())
    assert plan(paths)["curves"].reason == "no curve target passed its ship rule"
    t.production("curves")
    assert plan(paths)["curves"].reason == "week 5 has no player projections yet (Run week 5)"
    t.projections(5)
    assert plan(paths)["curves"].reason == "no curves for week 5 yet"
    S.production_file(paths, "curves").unlink()
    assert plan(paths)["curves"].reason.startswith("no promoted curves model yet")


# ---- grade -----------------------------------------------------------------------------------
# The grade step looks at the GRADE_BACK weeks before the planned one, oldest first. A (week,
# family) is a job when the week is final and the family was never graded or has rows waiting
# (a row with no result whose game is final); for a week that isn't final yet only a family
# graded once with rows waiting is topped up. The scoreboard no longer says anything.


def jobs_of(paths, week=5, now=NOW) -> tuple[tuple[int, str], ...]:
    return plan(paths, week=week, now=now)["grade"].jobs


def final_games_but(*game_ids: str) -> list:
    """The fixture's games, with no result for the given game ids."""
    return [(w, g, a, h, k, None if g in game_ids else r) for w, g, a, h, k, r in F.GAMES]


def games_with_results(*weeks: int) -> list:
    """The fixture's games, week 5 and later given a result too (when `weeks` names them)."""
    return [(w, g, a, h, k, 7 if (w in weeks and r is None) else r) for w, g, a, h, k, r in F.GAMES]


def test_week_one_has_nothing_to_grade(paths) -> None:
    up_to_date(paths)
    g = plan(paths, week=1)["grade"]
    assert (g.due, g.label, g.reason, g.week) == (
        False,
        "Grade last week",
        "week 1: nothing to grade",
        None,
    )
    assert g.jobs == () and g.parts == ()


def test_nothing_to_grade_when_the_last_weeks_have_no_files(paths) -> None:
    FLTree(paths).games().models().projections(5)
    g = plan(paths)["grade"]
    assert (g.due, g.reason, g.week) == (False, "week 4 has nothing to grade", 4)
    assert g.label == "Grade week 4" and g.parts == () and g.jobs == ()


def test_last_week_is_not_final_yet(paths) -> None:
    t = ungrade(up_to_date(paths), 4)
    t.games(final_games_but(F.G4[2]))  # Monday night has no result yet
    g = plan(paths)["grade"]
    assert (g.due, g.reason, g.week) == (False, "week 4 isn't final in the data yet", 4)
    assert g.label == "Grade week 4" and g.jobs == ()
    t.games(final_games_but()[3:])  # last week is gone from the table altogether
    assert plan(paths)["grade"].reason == "week 4 isn't final in the data yet"


def test_both_families_ungraded_and_final_are_due_together(paths) -> None:
    ungrade(up_to_date(paths), 4)
    g = plan(paths)["grade"]
    assert g.due and g.parts == ("availability", "curves") and g.week == 4
    assert g.jobs == ((4, "availability"), (4, "curves"))
    assert g.label == "Grade week 4"
    assert g.reason == "week 4's availability and curves aren't graded yet"


@pytest.mark.parametrize("family", ["availability", "curves"])
def test_one_family_graded_and_the_other_not(paths, family) -> None:
    ungrade(up_to_date(paths), 4, family)
    g = plan(paths)["grade"]
    assert g.due and g.parts == (family,) and g.jobs == ((4, family),)
    assert g.reason == f"week 4's {family} aren't graded yet"


def test_both_graded(paths) -> None:
    up_to_date(paths)
    g = plan(paths)["grade"]
    assert (g.due, g.reason, g.parts, g.jobs) == (False, "week 4 is graded", (), ())
    assert (g.week, g.label) == (4, "Grade week 4")


def test_only_a_family_that_made_last_weeks_file_is_graded(paths) -> None:
    t = ungrade(up_to_date(paths), 4)
    t.drop(4, "curves.parquet", "curves.json")
    g = plan(paths)["grade"]
    assert g.parts == ("availability",) and g.jobs == ((4, "availability"),)
    assert g.reason == "week 4's availability aren't graded yet"


def write_scoreboards(paths, kind: str) -> None:
    """Both families' `<family>_scoreboard.parquet`, of one of the shapes a season can have."""
    for fam in S.FAMILIES:
        f = S.season_dir(paths, 2026) / f"{fam}_scoreboard.parquet"
        f.parent.mkdir(parents=True, exist_ok=True)
        if kind == "damaged":
            f.write_bytes(b"junk")
            continue
        weeks = {
            "claims week 4": [4, 4],
            "claims other weeks": [1, 2, 3],
            "null week": [None, None],
            "empty": [],
        }
        if kind == "text week":
            pl.DataFrame({"week": ["4", "four"]}).write_parquet(f)
        elif kind in weeks:
            pl.DataFrame({"week": weeks[kind]}, schema={"week": pl.Int32}).write_parquet(f)


@pytest.mark.parametrize(
    "kind",
    ["absent", "claims week 4", "claims other weeks", "null week", "text week", "damaged", "empty"],
)
def test_the_scoreboard_no_longer_says_whether_a_week_is_graded(paths, kind) -> None:
    """Graded is what the week's own file says (`played` filled), never the scoreboard."""
    t = up_to_date(paths)  # week 4's rows are graded...
    write_scoreboards(paths, kind)
    assert plan(paths)["grade"].due is False
    ungrade(t, 4)  # ...and now they are not, under the same scoreboard
    g = plan(paths)["grade"]
    assert g.due is True and g.parts == ("availability", "curves")


def test_grade_needs_neither_a_model_nor_the_clock(paths) -> None:
    ungrade(up_to_date(paths), 4, "availability")
    S.production_file(paths, "availability").unlink()
    S.production_file(paths, "curves").unlink()
    for now in (NOW - 30 * DAY, NOW, NOW + 30 * DAY):
        g = plan(paths, now=now)["grade"]
        assert (g.due, g.parts) == (True, ("availability",))


def test_grade_looks_at_the_week_before_the_one_asked_for(paths) -> None:
    up_to_date(paths)
    g = plan(paths, week=6)["grade"]  # week 5 made both files, hasn't been played yet
    assert (g.due, g.week, g.label) == (False, 5, "Grade week 5")
    assert g.reason == "week 5 isn't final in the data yet"
    assert plan(paths, week=5)["grade"].week == 4


# ---- grade: looking back, and rows that waited ------------------------------------------------


def test_the_grade_looks_three_weeks_back() -> None:
    assert U.GRADE_BACK == 3


def test_a_week_nobody_updated_during_is_graded_two_updates_later(paths) -> None:
    """Weeks 4 and 5 were both played with no update in between: the update of week 6 takes
    both, oldest first, and says so."""
    t = ungrade(up_to_date(paths), 4)
    ungrade(t, 5)
    t.games(games_with_results(5))
    g = plan(paths, week=6)["grade"]
    assert g.due is True and g.label == "Grade weeks 4, 5"
    assert g.jobs == ((4, "availability"), (4, "curves"), (5, "availability"), (5, "curves"))
    assert g.week == 5 and g.parts == ("availability", "curves")  # the newest job week
    assert g.reason == (
        "week 4's availability and curves aren't graded yet; "
        "week 5's availability and curves aren't graded yet"
    )


def test_only_the_missed_family_of_an_older_week_is_graded(paths) -> None:
    t = ungrade(up_to_date(paths), 4, "curves")
    t.games(games_with_results(5))
    FLTree(paths).availability(5, rows=F.graded(5)).curves(5, rows=F.graded(5))
    g = plan(paths, week=6)["grade"]  # week 5 is done; week 4's curves were missed
    assert g.jobs == ((4, "curves"),) and g.parts == ("curves",) and g.week == 4


def test_a_single_older_week_reads_as_a_week_not_weeks(paths) -> None:
    """One job week is "Grade week N", the older one too; several are "Grade weeks 4, 5"."""
    t = ungrade(up_to_date(paths), 4, "curves")
    t.games(games_with_results(5))
    FLTree(paths).availability(5, rows=F.graded(5)).curves(5, rows=F.graded(5))
    assert plan(paths, week=6)["grade"].label == "Grade week 4"
    ungrade(t, 5)  # week 5 missed too: now it is two weeks
    assert plan(paths, week=6)["grade"].label == "Grade weeks 4, 5"
    ungrade(t, 4, "availability")
    assert plan(paths, week=5)["grade"].label == "Grade week 4"


def test_a_week_more_than_three_back_is_left_alone(paths) -> None:
    t = ungrade(up_to_date(paths), 4)
    t.drop(5, "availability.json", "availability.parquet", "curves.json", "curves.parquet")
    assert jobs_of(paths, week=7) != ()  # week 4 is the third week back from week 7
    g = plan(paths, week=8)["grade"]  # and the fourth from week 8
    assert (g.due, g.jobs, g.week) == (False, (), 7)
    assert g.reason == "week 7 has nothing to grade"


def test_the_weeks_between_are_each_looked_at(paths) -> None:
    t = ungrade(up_to_date(paths), 4)
    FLTree(paths).availability(6, rows=F.ungraded(6)).curves(6, rows=F.ungraded(6))
    t.games([*F.GAMES, (6, "2026_06_AAA_BBB", "AAA", "BBB", F.K5[2] + 7 * DAY, 3)])
    assert [j[0] for j in jobs_of(paths, week=7)] == [4, 4, 6, 6]  # week 5 isn't final: it waits


@pytest.mark.parametrize("both", [True, False])
def test_a_week_graded_in_pieces_is_listed_again_with_the_rows_that_waited(paths, both) -> None:
    """Monday night's snap counts landed late: its rows stayed null. The next plan says which
    family still has rows waiting and how many; once they're filled it's graded."""
    t = up_to_date(paths)
    rows = [(F.G4[0], True), (F.G4[1], True), (F.G4[2], None), (F.G4[2], None), (F.G4[2], None)]
    t.curves(4, rows=rows)
    if both:
        t.availability(4, {"tuesday": MADE}, rows=[(F.G4[0], True), (F.G4[2], None)])
    g = plan(paths)["grade"]
    assert g.due is True
    if both:
        assert g.jobs == ((4, "availability"), (4, "curves")) and g.parts == (
            "availability",
            "curves",
        )
        assert g.reason == "week 4's availability and curves had 4 rows waiting for results"
    else:
        assert (
            g.jobs == ((4, "curves"),)
            and g.reason == "week 4's curves had 3 rows waiting for results"
        )
    assert g.label == "Grade week 4" and g.week == 4
    t.curves(4, rows=F.graded(4)).availability(4, {"tuesday": MADE}, rows=F.graded(4))
    g = plan(paths)["grade"]
    assert (g.due, g.reason, g.jobs) == (False, "week 4 is graded", ())


def test_a_waiting_row_needs_a_final_game(paths) -> None:
    """Rows of a game with no result yet aren't waiting (there is nothing to fill them with)."""
    t = up_to_date(paths)
    t.games(final_games_but(F.G4[2]))  # Monday night not final: week 4 isn't final either
    t.curves(4, rows=[(F.G4[0], True), (F.G4[2], None)])
    g = plan(paths)["grade"]
    assert g.due is False and g.reason == "week 4 is graded"
    t.curves(4, rows=[(F.G4[0], True), (F.G4[1], None), (F.G4[2], None)])  # Sunday's are waiting
    g = plan(paths)["grade"]
    assert (g.due, g.jobs) == (True, ((4, "curves"),))
    assert g.reason == "week 4's curves had 1 row waiting for results"


def test_a_week_that_is_not_final_tops_up_only_what_was_graded_once(paths) -> None:
    t = ungrade(up_to_date(paths), 4)
    t.games(final_games_but(F.G4[2]))
    # availability was graded for Thursday's game, curves never: the first grade waits
    t.availability(4, {"tuesday": MADE}, rows=[(F.G4[0], True), (F.G4[1], None), (F.G4[2], None)])
    g = plan(paths)["grade"]
    assert g.jobs == ((4, "availability"),)
    assert g.reason == "week 4's availability had 1 row waiting for results"


def test_a_week_whose_every_row_is_null_waits_for_the_whole_week(paths) -> None:
    t = ungrade(up_to_date(paths), 4)
    t.games(final_games_but(F.G4[2]))  # two games have results, Monday night doesn't
    g = plan(paths)["grade"]
    assert (g.due, g.jobs, g.parts) == (False, (), ())
    assert g.reason == "week 4 isn't final in the data yet"
    t.games()  # Monday night is final
    g = plan(paths)["grade"]
    assert g.due is True and g.jobs == ((4, "availability"), (4, "curves"))


def test_the_reason_names_a_fresh_family_and_a_late_one_of_different_weeks(paths) -> None:
    t = up_to_date(paths)
    t.games(games_with_results(5))
    t.curves(4, rows=[(F.G4[0], True), (F.G4[1], None), (F.G4[2], None)])  # 2 rows waiting
    ungrade(t, 5)
    g = plan(paths, week=6)["grade"]
    assert g.jobs == ((4, "curves"), (5, "availability"), (5, "curves"))
    assert g.reason == (
        "week 4's curves had 2 rows waiting for results; "
        "week 5's availability and curves aren't graded yet"
    )
    assert g.label == "Grade weeks 4, 5" and g.week == 5
    assert g.parts == ("availability", "curves")  # distinct, in the families' order


# ---- grade_state ------------------------------------------------------------------------------


def test_grade_state_is_never_graded_and_rows_waiting(paths) -> None:
    g1, g2, g3 = F.G4
    FLTree(paths).availability(4, rows=[(g1, True), (g2, None), (g3, None), (g3, None)])

    def state(final: set[str]) -> tuple[bool, int]:
        return U.grade_state(paths, 2026, 4, "availability", final)

    assert state({g1, g2}) == (False, 1)  # graded once; g2's row waits, g3's game isn't final
    assert state({g1, g2, g3}) == (False, 3)
    assert state(set()) == (False, 0)
    FLTree(paths).availability(4, rows=[(g1, None), (g2, None)])
    assert state({g1}) == (True, 1)  # nothing graded yet, one row's game is final
    assert state(set()) == (True, 0)
    FLTree(paths).availability(4, rows=[(g1, False)])  # `played` false is graded (he sat)
    assert state({g1}) == (False, 0)


def test_grade_state_of_a_file_that_cannot_be_used_is_neither(paths) -> None:
    t = FLTree(paths)
    final = set(F.G4)
    assert U.grade_state(paths, 2026, 4, "availability", final) == (False, 0)  # no file
    for how in ("garbage", "empty", "no_played", "no_game_id"):
        t.availability(4, rows=F.ungraded(4)).damage(4, "availability", how)
        assert U.grade_state(paths, 2026, 4, "availability", final) == (False, 0), how


# ---- the grade step with damaged week files ---------------------------------------------------


@pytest.mark.parametrize("how", ["garbage", "empty", "no_played", "no_game_id"])
def test_a_week_file_the_grade_cannot_read_is_neither_and_never_raises(paths, how) -> None:
    t = up_to_date(paths)
    t.damage(4, "availability", how)
    ungrade(t, 4, "curves")
    steps = U.plan_update(paths, 2026, 5, NOW)
    assert [s.step for s in steps] == list(U.STEPS)
    g = by_step(steps)["grade"]
    assert g.jobs == ((4, "curves"),)  # only the readable family is graded
    t.damage(4, "curves", how)
    g = plan(paths)["grade"]
    assert (g.due, g.jobs) == (False, ())  # neither graded nor waiting: nothing to do


# ---- damaged and missing files never raise ---------------------------------------------------


def three(paths, week=5, now=NOW) -> list[str]:
    steps = U.plan_update(paths, F.SEASON, week, now)
    assert [s.step for s in steps] == ["availability", "curves", "grade"]
    return [s.reason for s in steps]


@pytest.mark.parametrize("junk", ["", "{", '{"version": ', "null", "[]", '"text"', "42"])
def test_a_damaged_production_json_is_no_model(paths, junk) -> None:
    only_curves_due(paths)
    FLTree(paths).production("availability", raw=junk).production("curves", raw=junk)
    r = three(paths)
    assert r[0] == "no promoted availability model yet"
    assert r[1].startswith("no promoted curves model yet")


def test_a_damaged_week_json_is_a_view_that_was_never_made(paths) -> None:
    up_to_date(paths).availability(5, meta="{not json")
    av = plan(paths)["availability"]
    assert (av.due, av.views, av.reason) == (True, ("tuesday",), "no Tuesday view yet")
    FLTree(paths).availability(5, meta='{"views": {"tuesday": {"made_at": "last tuesday"}}}')
    assert plan(paths)["availability"].reason == "no Tuesday view yet"  # a time nobody can read


def test_a_damaged_games_table_is_an_empty_schedule(paths) -> None:
    up_to_date(paths).drop(5, "availability.json", "availability.parquet")
    (paths.curated / "games.parquet").write_bytes(b"garbage")
    r = three(paths)
    assert r[0] == "no week-5 games in the schedule yet"
    assert r[2] == "week 4 is graded"  # the files say so before the schedule is asked


def test_a_missing_curated_table_is_not_an_error(paths) -> None:
    t = up_to_date(paths)
    ungrade(t, 4, "curves")
    (paths.curated / "games.parquet").unlink()
    (paths.curated / "snaps.parquet").unlink()
    r = three(paths)
    assert r[0] == "no week-5 games in the schedule yet"
    assert r[2] == "week 4 isn't final in the data yet"


def test_an_older_games_table_without_some_columns(paths) -> None:
    ungrade(up_to_date(paths), 4, "curves")
    pl.DataFrame({"game_id": ["a"], "season": [2026], "week": [5]}).write_parquet(
        paths.curated / "games.parquet"
    )  # no kickoff, no result: the games exist, none started, week 4 isn't there
    s = plan(paths)
    assert s["availability"].due is False and s["curves"].due is False
    assert s["grade"].reason == "week 4 isn't final in the data yet"
    pl.DataFrame({"game_id": ["a"], "week": [5]}).write_parquet(paths.curated / "games.parquet")
    assert plan(paths)["availability"].reason == "no week-5 games in the schedule yet"  # no season


@pytest.mark.parametrize(
    "views",
    [
        '["tuesday"]', '"tuesday"', "5", "null", "true", "[]", "{}",
        '{"tuesday": "2026-10-06T12:00:00Z"}', '{"tuesday": null}', '{"tuesday": 5}',
        '{"tuesday": ["x"]}', '{"tuesday": {"made_at": ["x"]}}', '{"tuesday": {"made_at": 5}}',
    ],
)  # fmt: skip
def test_a_json_of_the_wrong_shape_never_raises_and_makes_no_view(paths, views) -> None:
    up_to_date(paths).availability(5, meta=f'{{"views": {views}}}')
    steps = U.plan_update(paths, 2026, 5, NOW)
    assert [s.step for s in steps] == list(U.STEPS)
    av = by_step(steps)["availability"]
    assert (av.due, av.views, av.reason) == (True, ("tuesday",), "no Tuesday view yet")


def test_an_entry_of_the_wrong_shape_does_not_hide_a_good_one(paths) -> None:
    meta = '{"views": {"tuesday": {"made_at": "2026-10-06T12:00:00Z"}, "saturday": "junk"}}'
    up_to_date(paths).availability(5, meta=meta).injury_update(5, MADE + H)
    av = plan(paths)["availability"]  # Tuesday's view stands, Saturday's isn't a view
    assert (av.views, av.reason) == (
        ("saturday",),
        "the injury update has run: the Saturday view isn't made yet",
    )


@pytest.mark.parametrize("junk", ["[]", "5", "{not json", '{"week": 5, "views": "x"}'])
def test_the_curves_and_production_json_of_the_wrong_shape_never_raise_either(paths, junk) -> None:
    up_to_date(paths).curves(5, meta=junk)
    FLTree(paths).production("curves", raw=junk)
    assert [s.step for s in U.plan_update(paths, 2026, 5, NOW)] == list(U.STEPS)


# ---- the small readers -----------------------------------------------------------------------


def _games(rows) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=U.GAMES_SCHEMA, orient="row")


def test_kickoffs_counts_the_weeks_games_and_those_started() -> None:
    t = dt.datetime(2026, 10, 9, 0, 15, tzinfo=dt.UTC)
    g = _games(
        [
            ("a", 2026, 5, "REG", t, None),
            ("b", 2026, 5, "REG", t + H, None),
            ("c", 2026, 5, "REG", None, None),  # no time yet: not started
            ("d", 2026, 4, "REG", t - 7 * DAY, 3.0),
        ]
    )
    assert U.kickoffs(g, 5, t) == (3, 1)
    assert U.kickoffs(g, 5, t - dt.timedelta(microseconds=1)) == (3, 0)
    assert U.kickoffs(g, 5, t + 2 * H) == (3, 2)
    assert U.kickoffs(g, 4, t) == (1, 1)
    assert U.kickoffs(g, 9, t) == (0, 0)


def test_week_final_needs_a_result_for_every_game() -> None:
    t = dt.datetime(2026, 10, 9, 0, 15, tzinfo=dt.UTC)
    g = _games(
        [
            ("a", 2026, 4, "REG", t, 3.0),
            ("b", 2026, 4, "REG", t, 0.0),  # a tie is a result
            ("c", 2026, 5, "REG", t, 3.0),
            ("d", 2026, 5, "REG", t, None),
        ]
    )
    assert U.week_final(g, 4) is True
    assert U.week_final(g, 5) is False
    assert U.week_final(g, 6) is False  # a week that isn't there isn't final
    assert U.week_final(_games([]), 4) is False


def test_the_curated_games_are_read_for_one_season(paths) -> None:
    FLTree(paths).games()
    FLTree(paths, season=2025).games([(3, "2025_03_A_B", "A", "B", NOW, 7)])
    g = U._games(paths, 2026)  # the second write replaced the table: only 2025 is in it now
    assert g.is_empty()
    assert set(g.columns) == set(U.GAMES_SCHEMA)
    assert U._games(paths, 2025).height == 1


def test_injury_update_time_is_the_files_modified_time(paths) -> None:
    assert U.injury_update_time(paths, 2026, 5) is None
    FLTree(paths).injury_update(5, MADE)
    t = U.injury_update_time(paths, 2026, 5)
    assert t is not None and abs((t - MADE).total_seconds()) < 1 and t.tzinfo is not None
    assert U.injury_update_time(paths, 2026, 6) is None


def test_production_and_week_meta_readers(paths) -> None:
    t = up_to_date(paths)
    assert U.production(paths, "curves") == {"version": "v1", "shipped": list(F.SHIPPED)}
    assert U.production(paths, "availability") == {"version": "v1"}
    assert U.week_meta(paths, 2026, 5, "curves") == {"week": 5, "made_at": F.iso(MADE)}
    t.drop(5, "curves.json")
    assert U.week_meta(paths, 2026, 5, "curves") == {}  # the file's there, its json isn't
    t.drop(5, "curves.parquet")
    assert U.week_meta(paths, 2026, 5, "curves") is None
    assert U.week_meta(paths, 2026, 17, "availability") is None


def test_the_plan_reads_only_the_modified_time_of_the_projections_and_the_digest(paths) -> None:
    """A half-written file (rewritten just now, so newer than the views) never raises."""
    up_to_date(paths)
    (paths.run_dir(2026, 5) / "predictions_players.parquet").write_bytes(b"x")
    paths.report_path(2026, 5).write_bytes(b"\xff\xfe not text")
    assert due_steps(U.plan_update(paths, 2026, 5, NOW)) == ["availability", "curves"]


def test_the_plan_does_not_read_the_snap_counts_at_all(paths) -> None:
    up_to_date(paths)
    before = plan(paths)
    (paths.curated / "snaps.parquet").write_bytes(b"x")  # rewritten just now, and garbage
    assert plan(paths) == before
    (paths.curated / "snaps.parquet").unlink()
    assert plan(paths) == before
