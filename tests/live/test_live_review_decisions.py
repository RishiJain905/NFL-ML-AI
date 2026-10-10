"""LD03: which 4th downs are decisions (`review.decisions_frame`), what happened on them
(`review.outcome`, `text_choice`), the curated loader (`load_season`, coach fixes) and the hash a
stored review is stamped with (`week_hash`, `fixes_hash`).

Everything runs on a hand-made curated folder in `tmp_path` (`live_review_fixtures.World`): one
tiny game per scenario, so the "next snap" rules (a wiped-out down that was replayed or not) are
read from exactly the plays written here. No data drive, no network, no W&B.
"""

from __future__ import annotations

import math

import polars as pl
import pytest
from live_fakes import no_real_network  # noqa: F401  (autouse guard)
from live_review_fixtures import World, season_world

from nflengine.live import review

PUNT_WIPED = (
    "(Punt formation) J.Smith punts 40 yards to AWY 20, Center-X. PENALTY on AWY, Roughing the "
    "Kicker, 15 yards, enforced at HOM 40 - No Play."
)
GO_WIPED = (
    "T.Brady pass short right to C.Davis for 5 yards. PENALTY on AWY, Defensive Holding, 5 "
    "yards, enforced at AWY 40 - No Play."
)
FG_WIPED = (
    "(Field Goal formation) 45 yard field goal is No Good, Wide Left. PENALTY on AWY, Offside, "
    "5 yards, enforced at AWY 27 - No Play."
)
REVERSED = (
    "J.Smith punts 40 yards to AWY 20, Center-X. PENALTY on AWY, Roughing the Kicker, 15 yards "
    "- No Play. The Replay Official reviewed the penalty, and the ruling on the field was "
    "REVERSED. T.Brady pass short right to C.Davis for 6 yards. PENALTY on HOM, Offensive "
    "Holding, 10 yards - No Play."
)
PRESNAP = "(Shotgun) PENALTY on HOM, False Start, 5 yards, enforced at HOM 40 - No Play."
DELAY = "(Punt formation) PENALTY on HOM, Delay of Game, 5 yards, enforced at HOM 40 - No Play."


# --- the scenarios: one tiny game each ------------------------------------------------------------
def build_scenarios() -> tuple[World, dict[str, tuple[str, int]]]:
    w = World()
    at: dict[str, tuple[str, int]] = {}
    count = [0]

    def new(label: str) -> str:
        count[0] += 1
        return w.game(f"2025_01_S{count[0]:02d}_{label}", 1)

    g = new("run_go")
    at["run_go"] = (g, w.go(g, "HOM", 2, 40, converted=False, gained=1))
    w.snap(g, "AWY", 60)

    g = new("pass_go")
    at["pass_go"] = (g, w.go(g, "HOM", 6, 40, converted=True, gained=7, ptype="pass"))
    w.snap(g, "HOM", 33)

    g = new("field_goal")  # the game's last snap: no "next snap"
    at["field_goal"] = (g, w.kick(g, "HOM", 8, 22))

    g = new("punt")
    at["punt"] = (g, w.punt(g, "HOM", 9, 60))
    w.snap(g, "AWY", 80)

    g = new("fake_punt")  # nflverse types a fake as the run it was
    desc = "(Punt formation) J.Smith runs left end for 12 yards (T.Jones)."
    at["fake_punt"] = (g, w.go(g, "HOM", 9, 60, converted=True, gained=12, desc=desc))
    w.snap(g, "HOM", 48)

    g = new("fake_fg")
    desc = "(Field Goal formation) J.Smith pass short right to X for 9 yards."
    at["fake_fg"] = (g, w.go(g, "HOM", 8, 22, converted=True, gained=9, ptype="pass", desc=desc))
    w.snap(g, "HOM", 13)

    g = new("aborted_punt")  # a fumbled snap in punt formation: the call was the punt
    desc = "(Punt formation) Snap to J.Smith FUMBLED, recovered by AWY at HOM 45."
    at["aborted_punt"] = (
        g,
        w.go(g, "HOM", 9, 60, converted=False, gained=-8, desc=desc, aborted_play=1),
    )
    w.snap(g, "AWY", 45)

    g = new("aborted_fg")
    desc = "(Field Goal formation) Snap to J.Smith FUMBLED, recovered by AWY."
    at["aborted_fg"] = (
        g,
        w.go(g, "HOM", 8, 22, converted=False, gained=-5, ptype="pass", desc=desc, aborted_play=1),
    )
    w.snap(g, "AWY", 60)

    g = new("aborted_no_formation")  # a botched snap on a sneak is still a go
    desc = "J.Doe up the middle FUMBLES snap, recovered by AWY."
    at["aborted_no_formation"] = (
        g,
        w.go(g, "HOM", 1, 30, converted=False, gained=-2, desc=desc, aborted_play=1),
    )
    w.snap(g, "AWY", 70)

    g = new("wiped_punt")  # roughing the kicker: the same team's 1st down next, not replayed
    at["wiped_punt"] = (g, w.add(g, "HOM", "no_play", 4, 9, 60, desc=PUNT_WIPED))
    w.snap(g, "HOM", 45)

    g = new("wiped_go")  # a defensive penalty on a go: a first down
    at["wiped_go"] = (g, w.add(g, "HOM", "no_play", 4, 3, 40, desc=GO_WIPED))
    w.snap(g, "HOM", 35)

    g = new("wiped_fg")
    at["wiped_fg"] = (g, w.add(g, "HOM", "no_play", 4, 8, 27, desc=FG_WIPED))
    w.snap(g, "HOM", 22)

    g = new("wiped_reversed")  # only the final ruling's text counts
    at["wiped_reversed"] = (g, w.add(g, "HOM", "no_play", 4, 9, 60, desc=REVERSED))
    w.snap(g, "HOM", 50)

    g = new("wiped_no_next")  # nothing follows (the half ended): still a decision
    at["wiped_no_next"] = (g, w.add(g, "HOM", "no_play", 4, 9, 60, desc=PUNT_WIPED))

    g = new("replayed")  # the wiped-out down is played again: the replay is the decision
    desc = (
        "K.Cousins pass incomplete short left. PENALTY on HOM, Offensive Holding, 10 yards "
        "- No Play."
    )
    at["wiped_replayed"] = (g, w.add(g, "HOM", "no_play", 4, 2, 40, desc=desc))
    at["replayed_punt"] = (g, w.punt(g, "HOM", 12, 50))
    w.snap(g, "AWY", 80)

    g = new("presnap")  # a false start, then the punt
    at["presnap_replayed"] = (g, w.add(g, "HOM", "no_play", 4, 2, 40, desc=PRESNAP))
    at["presnap_punt"] = (g, w.punt(g, "HOM", 7, 45))
    w.snap(g, "AWY", 80)

    g = new("presnap_alone")  # a pre-snap penalty that was not replayed is still no decision
    at["presnap_alone"] = (g, w.add(g, "HOM", "no_play", 4, 2, 40, desc=DELAY))
    w.snap(g, "AWY", 55)

    g = new("kneel")
    at["kneel"] = (g, w.add(g, "HOM", "qb_kneel", 4, 1, 40))
    g = new("spike")
    at["spike"] = (g, w.add(g, "HOM", "qb_spike", 4, 1, 40))

    g = new("no_spread")
    at["no_spread"] = (g, w.punt(g, "HOM", 9, 60, spread_line=None))
    g = new("no_total")
    at["no_total"] = (g, w.punt(g, "HOM", 9, 60, total_line=None))
    return w, at


CHOICE = {
    "run_go": "go", "pass_go": "go", "field_goal": "fg", "punt": "punt", "fake_punt": "go",
    "fake_fg": "go", "aborted_punt": "punt", "aborted_fg": "fg", "aborted_no_formation": "go",
    "wiped_punt": "punt", "wiped_go": "go", "wiped_fg": "fg", "wiped_reversed": "go",
    "wiped_no_next": "punt", "replayed_punt": "punt", "presnap_punt": "punt",
}  # fmt: skip
NOT_DECISIONS = [
    "wiped_replayed", "presnap_replayed", "presnap_alone", "kneel", "spike", "no_spread",
    "no_total",
]  # fmt: skip
FLAGS = {  # (fake, aborted, wiped); everything else (False, False, False)
    "fake_punt": (True, False, False), "fake_fg": (True, False, False),
    "aborted_punt": (False, True, False), "aborted_fg": (False, True, False),
    "wiped_punt": (False, False, True), "wiped_go": (False, False, True),
    "wiped_fg": (False, False, True), "wiped_reversed": (False, False, True),
    "wiped_no_next": (False, False, True),
}  # fmt: skip


@pytest.fixture(scope="module")
def scenarios(tmp_path_factory):
    w, at = build_scenarios()
    plays = w.load(tmp_path_factory.mktemp("scenarios"))
    return at, review.decisions_frame(plays)


def row_of(scenarios, label: str) -> dict | None:
    at, frame = scenarios
    gid, pid = at[label]
    part = frame.filter((pl.col("game_id") == gid) & (pl.col("play_id") == pid))
    assert part.height <= 1
    return part.row(0, named=True) if part.height else None


@pytest.mark.parametrize("label", sorted(CHOICE))
def test_the_call_is_read_from_the_play(scenarios, label):
    r = row_of(scenarios, label)
    assert r is not None, f"{label} should be a decision"
    assert r["choice"] == CHOICE[label]
    assert (r["fake"], r["aborted"], r["wiped"]) == FLAGS.get(label, (False, False, False))


@pytest.mark.parametrize("label", NOT_DECISIONS)
def test_these_4th_downs_are_not_decisions(scenarios, label):
    assert row_of(scenarios, label) is None


def test_the_decisions_are_exactly_these(scenarios):
    at, frame = scenarios
    want = {at[label] for label in CHOICE}
    got = set(
        zip(frame["game_id"].to_list(), frame["play_id"].cast(pl.Int64).to_list(), strict=True)
    )
    assert got == {(g, int(p)) for g, p in want}
    assert frame["choice"].null_count() == 0


def test_the_next_snap_is_the_next_one_of_the_same_game(scenarios):
    r = row_of(scenarios, "punt")
    assert (r["next_pos"], r["next_down"], r["next_yl"]) == ("AWY", 1, 80)
    last = row_of(scenarios, "field_goal")
    assert last["next_pos"] is None and last["next_down"] is None, "no peeking into another game"
    assert row_of(scenarios, "wiped_punt")["next_pos"] == "HOM"
    assert row_of(scenarios, "replayed_punt")["next_pos"] == "AWY"


def test_a_fake_is_a_go_and_not_the_kick_it_looked_like(scenarios):
    assert row_of(scenarios, "fake_punt")["play_type"] == "run"
    assert row_of(scenarios, "fake_fg")["play_type"] == "pass"


def test_a_reversed_review_uses_the_final_ruling_only(scenarios):
    # the first ruling's text says "punts"; the final one is a pass: a go
    assert row_of(scenarios, "wiped_reversed")["choice"] == "go"
    assert review.text_choice(REVERSED) == "go"
    assert review.text_choice(REVERSED.split("REVERSED.")[0]) == "punt"


def test_a_week_without_4th_downs_has_the_columns_and_no_rows(tmp_path):
    w = World()
    g = w.game("2025_01_X_Y", 1)
    w.snap(g, "HOM", 60)
    frame = review.decisions_frame(w.load(tmp_path))
    assert frame.height == 0
    assert {"choice", "fake", "aborted", "wiped"} <= set(frame.columns)


def test_an_empty_frame_comes_back_empty():
    empty = pl.DataFrame()
    assert review.decisions_frame(empty).is_empty()


# --- text_choice ----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "want"),
    [
        (None, "go"),
        ("", "go"),
        ("J.Smith punts 40 yards to AWY 20", "punt"),
        ("(Punt formation) J.Smith runs left end for 12 yards", "go"),
        ("J.Elliott 47 yard field goal is GOOD", "fg"),
        ("J.Elliott 45 yard field goal is No Good, Wide Left", "fg"),
        ("(Field Goal formation) J.Smith pass short right", "go"),  # a fake, not a kick
        ("T.Brady pass incomplete short left", "go"),
        ("J.Smith punts 40 yards. REVERSED. T.Brady pass incomplete", "go"),
        ("T.Brady pass incomplete. REVERSED. J.Smith punts 40 yards", "punt"),
        ("J.Elliott 47 yard field goal is GOOD. Reversed. J.Smith punts 40 yards", "punt"),
    ],
)
def test_text_choice(text, want):
    assert review.text_choice(text) == want


FAKE_FG_WIPED = (
    "(Field Goal formation) J.Smith pass short right to X for 9 yards. PENALTY on AWY, "
    "Defensive Holding, 5 yards, enforced at AWY 22 - No Play."
)


def test_a_wiped_out_fake_field_goal_is_a_go():
    """LD01's rule: only "field goal is" is a kick; a "(Field Goal formation)" note is a fake's."""
    assert review.text_choice(FAKE_FG_WIPED) == "go"
    assert review.text_choice(FAKE_FG_WIPED.replace("Field Goal", "Punt")) == "go"  # fake punt: ok


def test_a_wiped_out_fake_field_goal_in_a_game(tmp_path):
    w = World()
    g = w.game("2025_01_A_B", 1)
    pid = w.add(g, "HOM", "no_play", 4, 8, 22, desc=FAKE_FG_WIPED)
    w.snap(g, "HOM", 17)  # the same team's 1st down: not replayed
    frame = review.decisions_frame(w.load(tmp_path))
    assert frame.filter(pl.col("play_id") == pid)["choice"].to_list() == ["go"]


# --- outcome --------------------------------------------------------------------------------------
def go_row(**kw) -> dict:
    return {
        "choice": "go",
        "posteam": "HOM",
        "defteam": "AWY",
        "ydstogo": 3,
        "yards_gained": 1,
        **kw,
    }


def test_a_go_that_scored_converted_or_failed():
    assert review.outcome(go_row(touchdown=1, td_team="HOM")) == (True, "Touchdown")
    assert review.outcome(go_row(fourth_down_converted=1, yards_gained=7)) == (
        True,
        "Converted (+7 yds)",
    )
    assert review.outcome(go_row(fourth_down_converted=1, yards_gained=None)) == (
        True,
        "Converted",
    )
    assert review.outcome(go_row()) == (False, "Stopped (+1 yds, needed 3)")
    assert review.outcome(go_row(yards_gained=-2)) == (False, "Stopped (-2 yds, needed 3)")
    assert review.outcome(go_row(yards_gained=None)) == (False, "Stopped")


def test_a_go_that_turned_the_ball_over():
    assert review.outcome(go_row(interception=1)) == (False, "Intercepted")
    pick_six = go_row(interception=1, touchdown=1, td_team="AWY")
    assert review.outcome(pick_six) == (False, "Intercepted (returned for a TD)")
    assert review.outcome(go_row(fumble_lost=1)) == (False, "Fumble lost")
    scoop = go_row(fumble_lost=1, touchdown=1, td_team="AWY")
    assert review.outcome(scoop) == (False, "Fumble lost (returned for a TD)")
    assert review.outcome(go_row(safety=1)) == (False, "Safety")
    assert review.outcome(go_row(aborted=True)) == (False, "Botched snap")


def test_a_fake_names_the_formation_it_came_from():
    punt = go_row(fake=True, desc="(Punt formation) J.Smith runs left end", yards_gained=12)
    fg = go_row(fake=True, desc="(Field Goal formation) pass to X", fourth_down_converted=1)
    assert review.outcome({**punt, "fourth_down_converted": 1}) == (
        True,
        "Fake punt: Converted (+12 yds)",
    )
    assert review.outcome({**fg, "yards_gained": 9}) == (
        True,
        "Fake field goal: Converted (+9 yds)",
    )
    assert review.outcome({**punt, "touchdown": 1, "td_team": "HOM"}) == (
        True,
        "Fake punt: Touchdown",
    )
    assert review.outcome(punt) == (False, "Fake punt: Stopped (+12 yds, needed 3)")


def kick_row(result: str | None, **kw) -> dict:
    return {
        "choice": "fg", "posteam": "HOM", "defteam": "AWY", "yardline_100": 30,
        "kick_distance": 48.0, "field_goal_result": result, **kw,
    }  # fmt: skip


def test_a_field_goal_made_missed_blocked_or_botched():
    assert review.outcome(kick_row("made")) == (True, "Good from 48")
    assert review.outcome(kick_row("missed")) == (False, "Missed from 48")
    assert review.outcome(kick_row("blocked")) == (False, "Blocked from 48")
    run_back = kick_row("blocked", touchdown=1, td_team="AWY")
    assert review.outcome(run_back) == (False, "Blocked from 48 (returned for a TD)")
    assert review.outcome(kick_row(None, aborted=True)) == (False, "Botched snap")
    # no kick distance: the yards to goal + the 18 from the snap to the kick
    assert review.outcome(kick_row("made", kick_distance=None)) == (True, "Good from 48")


def punt_row(**kw) -> dict:
    return {"choice": "punt", "posteam": "HOM", "defteam": "AWY", **kw}


def test_a_punt_says_where_the_ball_went():
    assert review.outcome(punt_row(next_pos="AWY", next_yl=80.0)) == (None, "AWY ball at own 20")
    assert review.outcome(punt_row(next_pos="AWY", next_yl=71.0)) == (None, "AWY ball at own 29")
    # received in the kicking team's half (a long return or a penalty): named by that side
    assert review.outcome(punt_row(next_pos="AWY", next_yl=33.0)) == (None, "AWY ball at HOM 33")
    assert review.outcome(punt_row(next_pos="AWY", next_yl=49.0)) == (None, "AWY ball at HOM 49")
    assert review.outcome(punt_row(next_pos="AWY", next_yl=50.0)) == (None, "AWY ball at midfield")
    assert review.outcome(punt_row(next_pos="AWY", next_yl=51.0)) == (None, "AWY ball at own 49")
    assert review.outcome(punt_row(next_pos="HOM", next_yl=40.0)) == (
        None,
        "Punt: kicking team kept it",
    )
    assert review.outcome(punt_row()) == (None, "Punt")
    assert review.outcome(punt_row(punt_blocked=1)) == (None, "Punt blocked")
    blocked_td = punt_row(punt_blocked=1, touchdown=1, td_team="AWY")
    assert review.outcome(blocked_td) == (None, "Punt blocked (returned for a TD)")
    assert review.outcome(punt_row(touchdown=1, td_team="AWY")) == (None, "Punt returned for a TD")
    assert review.outcome(punt_row(aborted=True)) == (None, "Botched punt snap")


@pytest.mark.parametrize("choice", ["go", "fg", "punt"])
def test_a_wiped_out_down_that_gave_a_first_down(choice):
    row = {"choice": choice, "posteam": "HOM", "defteam": "AWY", "wiped": True,
           "next_pos": "HOM", "next_down": 1.0}  # fmt: skip
    success, text = review.outcome(row)
    assert text == "Wiped out by a penalty: first down"
    assert success is (True if choice == "go" else None)


@pytest.mark.parametrize("choice", ["go", "fg", "punt"])
def test_a_wiped_out_down_that_gave_the_ball_away(choice):
    row = {"choice": choice, "posteam": "HOM", "defteam": "AWY", "wiped": True,
           "next_pos": "AWY", "next_down": 1.0}  # fmt: skip
    success, text = review.outcome(row)
    assert text == "Wiped out by a penalty: AWY ball"
    assert success is (False if choice == "go" else None)


def test_a_wiped_out_down_with_nothing_after_it():
    row = {"choice": "go", "posteam": "HOM", "defteam": "AWY", "wiped": True}
    assert review.outcome(row) == (None, "Wiped out by a penalty")
    # the same team's next snap, but not a 1st down: nothing is claimed
    row = {**row, "next_pos": "HOM", "next_down": 2.0}
    assert review.outcome(row) == (None, "Wiped out by a penalty")


# --- small helpers --------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("qtr", "half", "want"),
    [
        (1, 1200.0, "5:00"),
        (1, 1800.0, "15:00"),
        (2, 600.0, "10:00"),
        (3, 1500.0, "10:00"),
        (4, 65.0, "1:05"),
        (4, 0.0, "0:00"),
        (5, 400.0, "6:40"),  # overtime: the half clock is the overtime clock
        (1, 890.0, "0:00"),  # never negative
        (3, None, None),
        (3, float("nan"), None),
    ],
)
def test_the_quarter_clock(qtr, half, want):
    assert review._clock(qtr, half) == want  # noqa: SLF001


def test_whole_numbers():
    assert review._i(None) is None  # noqa: SLF001
    assert review._i(float("nan")) is None  # noqa: SLF001
    assert review._i(2.6) == 3  # noqa: SLF001
    assert review._i(7) == 7  # noqa: SLF001


@pytest.mark.parametrize(
    ("season", "trained", "want"),
    [(2025, [2010, 2025], True), (2026, [2010, 2025], False), (2025, None, False),
     (2025, [], False)],
)  # fmt: skip
def test_in_sample(season, trained, want):
    assert review.in_sample(season, trained) is want


# --- load_season ----------------------------------------------------------------------------------
def test_load_season_adds_the_coach_the_kickoff_and_the_state(tmp_path):
    w = season_world()
    w.add("2025_01_AWY_HOM", None, "kickoff", None, None, None)  # no possession: no coach
    plays = w.load(tmp_path)
    assert plays["season"].unique().to_list() == [2025]
    home = plays.filter(pl.col("posteam") == "HOM")["coach"].unique().to_list()
    away = plays.filter(pl.col("posteam") == "AWY")["coach"].unique().to_list()
    assert home == ["Home Coach"] and away == ["Away Coach"], "week 2's home team is AWY"
    assert plays.filter(pl.col("posteam").is_null())["coach"].null_count() == 1
    assert {"score_diff", "spread", "total", "home", "kickoff_utc", "aborted_play",
            "fourth_down_converted", "posteam_score", "i"} <= set(plays.columns)  # fmt: skip
    p1 = plays.filter((pl.col("game_id") == "2025_01_AWY_HOM") & (pl.col("play_id") == 1)).row(
        0, named=True
    )
    assert p1["spread"] == 3.0 and p1["home"] == 1, "HOM is the home team, favored by 3"
    away_play = plays.filter(
        (pl.col("game_id") == "2025_01_AWY_HOM") & (pl.col("play_id") == 5)
    ).row(0, named=True)
    assert away_play["spread"] == -3.0 and away_play["home"] == -1
    assert plays["kickoff_utc"].null_count() == 0


def test_load_season_applies_the_coach_fixes_from_their_week(tmp_path):
    fixes = tmp_path / "fixes.csv"
    fixes.write_text(
        "# hand-checked\nseason,team,from_week,coach,source\n2025,HOM,2,Interim Coach,test\n",
        encoding="utf-8",
    )
    plays = season_world().load(tmp_path / "data", fixes)
    hom = plays.filter(pl.col("posteam") == "HOM")
    assert hom.filter(pl.col("week") == 1)["coach"].unique().to_list() == ["Home Coach"]
    assert hom.filter(pl.col("week") == 2)["coach"].unique().to_list() == ["Interim Coach"]
    awy = plays.filter(pl.col("posteam") == "AWY")["coach"].unique().to_list()
    assert awy == ["Away Coach"]


def test_load_season_leaves_out_the_preseason_and_keeps_the_playoffs(tmp_path):
    w = World()
    for gid, week, kind in (("2025_01_A_B", 1, "REG"), ("2025_00_C_D", 1, "PRE"),
                            ("2025_19_E_F", 19, "POST")):  # fmt: skip
        w.game(gid, week, season_type=kind)
        w.punt(gid, "HOM", 9, 60)
    plays = w.load(tmp_path)
    assert sorted(plays["game_id"].unique().to_list()) == ["2025_01_A_B", "2025_19_E_F"]
    assert review.decisions_frame(plays).filter(pl.col("playoffs")).height == 1


def test_a_season_without_curated_plays_is_an_empty_frame(tmp_path):
    paths = World().write(tmp_path)
    assert review.load_season(2030, paths, tmp_path / "none.csv").height == 0


# --- week_hash and fixes_hash ---------------------------------------------------------------------
def season_plays(tmp_path, edit=None):
    w = season_world()
    if edit:
        edit(w)
    return w.load(tmp_path)


def test_the_same_rows_hash_alike(tmp_path):
    a = review.week_hash(season_plays(tmp_path / "a"), [1])
    b = review.week_hash(season_plays(tmp_path / "b"), [1])
    assert a == b and len(a) == 24 and int(a, 16) >= 0
    assert review.week_hash(season_plays(tmp_path / "c"), [2]) != a, "another week"


def test_row_order_does_not_matter(tmp_path):
    plays = season_plays(tmp_path)
    shuffled = plays.sample(fraction=1.0, shuffle=True, seed=3)
    assert shuffled["play_id"].to_list() != plays["play_id"].to_list()
    assert review.week_hash(shuffled, [1, 2]) == review.week_hash(plays, [1, 2])


@pytest.mark.parametrize(
    "edit",
    [
        lambda w: w.rows[0].update(desc="a different play text"),
        lambda w: w.rows[0].update(yards_gained=9.0),
        lambda w: w.rows[0].update(aborted_play=1.0),  # an extra column
        lambda w: w.rows[0].update(home_coach="Someone Else"),  # an extra column
        lambda w: w.rows[0].update(posteam_score=99.0),
        lambda w: w.add("2025_01_AWY_HOM", "HOM", "run", 1, 10, 50),  # one more row
        lambda w: w.rows.pop(0),  # one row fewer
    ],
    ids=["desc", "yards", "aborted", "coach", "score", "added", "removed"],
)
def test_any_changed_value_or_row_changes_the_hash(tmp_path, edit):
    before = review.week_hash(season_plays(tmp_path / "a"), [1])
    after = review.week_hash(season_plays(tmp_path / "b", edit), [1])
    assert after != before


def test_another_weeks_rows_do_not_change_this_weeks_hash(tmp_path):
    def edit(w):
        w.add("2025_02_HOM_AWY", "HOM", "run", 1, 10, 50)

    a, b = season_plays(tmp_path / "a"), season_plays(tmp_path / "b", edit)
    assert review.week_hash(a, [1]) == review.week_hash(b, [1])
    assert review.week_hash(a, [1, 2]) != review.week_hash(b, [1, 2])


def test_the_hash_of_nothing_is_a_value_too(tmp_path):
    empty = review.week_hash(pl.DataFrame(), [1])
    assert len(empty) == 24
    assert empty != review.week_hash(season_plays(tmp_path), [1])
    assert review.week_hash(season_plays(tmp_path / "x"), [9]) == review.week_hash(
        season_plays(tmp_path / "y"), [8]
    ), "weeks with no rows hash alike"


def test_fixes_hash_follows_the_file(tmp_path):
    f = tmp_path / "fixes.csv"
    assert review.fixes_hash(f) is None, "no file"
    f.write_text("season,team,from_week,coach\n", encoding="utf-8")
    a = review.fixes_hash(f)
    assert a is not None and len(a) == 16
    f.write_text("season,team,from_week,coach\n2025,HOM,2,X\n", encoding="utf-8")
    assert review.fixes_hash(f) != a
    assert not math.isnan(int(a, 16))


# --- nflverse's play text (Sol / cross-review follow-up) -------------------------------------
def test_play_text_loses_control_characters_and_is_cut_at_a_word_with_an_ellipsis():
    assert (
        review.cap_text("(4:16) punts\x1b[31m 63 yards\nto MIA 6")
        == "(4:16) punts [31m 63 yards to MIA 6"
    )
    assert review.cap_text(None) == ""
    words = " ".join(["REVERSED"] * 60)  # 539 characters
    out = review.cap_text(words)
    assert len(out) <= review.DESC_CAP and out.endswith("…")
    assert out[:-1].split(" ")[-1] == "REVERSED", "cut between two words, never inside one"
    assert review.cap_text("x" * review.DESC_CAP) == "x" * review.DESC_CAP
