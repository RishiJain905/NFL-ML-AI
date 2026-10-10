"""LD01: replaying a finished game from ESPN's summary (live/replay.py).

Two real games, trimmed (tests/fixtures/live/README.md):
- TB@DAL (2026 week 5, 183 plays): every expectation below is worked out by hand from the
  play-by-play text and football rules (the score before a play, the timeouts called, whose
  goal line a yard line counts from ...); nflverse doesn't have the game yet.
- IND@KC (2026 week 2, 213 plays, overtime): also checked against **nflverse's play-by-play**
  for the same plays; the nflverse numbers below are literals copied from the curated `plays`
  table (play_id = the ESPN id minus the event id).
No test reaches the network or the data drive."""

from __future__ import annotations

import gzip
import json
from types import SimpleNamespace

import httpx
import pytest
from live_fakes import (
    FakeClock,
    ctx,
    fixture,
    no_real_network,  # noqa: F401  (autouse guard)
)

from nflengine.live import replay as R
from nflengine.live.espn import BASE, EspnClient
from nflengine.live.replay import (
    ReplayPlay,
    choice_of,
    decision_plays,
    load_summary,
    replay,
    summary_path,
    summary_state,
    timeout_window,
)
from nflengine.live.schema import GameState
from nflengine.live.state import opening_receiver

TB_DAL = "summary_401872980_tb_dal.json"
IND_KC = "summary_401872945_ind_kc_ot.json"


@pytest.fixture(scope="module")
def tb_dal() -> list[ReplayPlay]:
    return replay(fixture(TB_DAL), ctx("tb_dal"))


@pytest.fixture(scope="module")
def ind_kc() -> list[ReplayPlay]:
    return replay(fixture(IND_KC), ctx("ind_kc"))


def types_at(rows, indexes) -> set[str]:
    return {rows[i].type for i in indexes}


# --- TB@DAL: what is a snap ----------------------------------------------------------------------
KICKOFFS = [0, 13, 36, 87, 93, 97, 112, 124, 160]
OFFICIAL_TIMEOUTS = [12, 35, 43, 48, 56, 68, 96, 104, 111, 117, 123, 130, 148, 159, 166]
TEAM_TIMEOUTS = [29, 51, 90, 146, 177, 179]
END_PERIOD = [32, 134]
TWO_MINUTE = [78, 172]


def test_tb_dal_has_147_snaps_out_of_183_rows(tb_dal):
    # 183 rows = 147 snaps + 9 kickoffs + 27 rows that aren't plays: 15 TV timeouts, 6 team
    # timeouts, 2 quarter ends, the half, the game's end and 2 two-minute warnings
    assert len(tb_dal) == 183
    assert sum(r.snap for r in tb_dal) == 147
    assert sum(r.state is not None for r in tb_dal) == 147  # every snap got a state
    assert [r.reason for r in tb_dal if r.snap and r.state is None] == []


def test_kickoffs_timeouts_quarter_ends_and_the_warning_are_not_snaps(tb_dal):
    assert types_at(tb_dal, KICKOFFS) == {"Kickoff"}
    assert types_at(tb_dal, OFFICIAL_TIMEOUTS) == {"Official Timeout"}
    assert types_at(tb_dal, TEAM_TIMEOUTS) == {"Timeout"}
    assert types_at(tb_dal, END_PERIOD) == {"End Period"}
    assert types_at(tb_dal, TWO_MINUTE) == {"Two-minute warning"}
    assert tb_dal[92].type == "End of Half"
    assert tb_dal[182].type == "End of Game"
    not_plays = KICKOFFS + OFFICIAL_TIMEOUTS + TEAM_TIMEOUTS + END_PERIOD + TWO_MINUTE + [92, 182]
    assert len(not_plays) == 36 and len(set(not_plays)) == 36
    for i in not_plays:
        row = tb_dal[i]
        assert row.snap is False
        assert (row.state, row.choice, row.offense, row.down, row.converted) == (
            None, None, None, None, None,
        )  # fmt: skip
    # ESPN gives these rows a down and a yard line (a two-minute warning even shows "4th & 9"):
    # they are still not plays to score
    assert tb_dal[78].snap is False


def test_the_rows_keep_espns_order_and_ids(tb_dal):
    assert [r.sequence for r in tb_dal] == sorted(r.sequence for r in tb_dal)
    assert tb_dal[0].play_id == "40187298040"
    assert tb_dal[0].nflverse_play_id == 40  # the id minus the event id (401872980)
    assert tb_dal[11].play_id == "401872980322"
    assert tb_dal[11].nflverse_play_id == 322
    assert tb_dal[182].nflverse_play_id == 4255
    assert tb_dal[42].wallclock.isoformat() == "2026-10-09T00:53:29+00:00"


def test_every_snap_is_on_the_field_with_a_down_and_distance(tb_dal):
    for r in tb_dal:
        if r.snap:
            assert r.down in (1, 2, 3, 4)
            assert 1 <= r.distance <= r.yardline_100 <= 99
            assert r.state.down == r.down
            assert r.state.yardline_100 == r.yardline_100
            assert r.offense in ("DAL", "TB") and r.defense in ("DAL", "TB")
            assert r.offense != r.defense


# --- TB@DAL: yards to goal, never ESPN's yardsToEndzone ---------------------------------------
@pytest.mark.parametrize(
    ("index", "offense", "raw_yard_line", "espn_yards_to_endzone", "to_goal"),
    [
        (42, "DAL", 44, 44, 56),  # DAL punts from its own 44: ESPN says 44 to go, it is 56
        (55, "DAL", 53, 53, 47),  # punting from TB's 47
        (73, "DAL", 18, 18, 82),  # punting from its own 18
        (47, "TB", 92, 92, 92),  # away team: the raw yard line already counts from DAL's goal
        (3, "DAL", 38, 62, 62),  # a home snap that ESPN got right
    ],
)
def test_yards_to_goal_comes_from_yard_line_and_possession(
    tb_dal, index, offense, raw_yard_line, espn_yards_to_endzone, to_goal
):
    summary = fixture(TB_DAL)
    plays = {p["id"]: p for d in summary["drives"]["previous"] for p in d["plays"]}
    raw = plays[tb_dal[index].play_id]["start"]
    assert (raw["yardLine"], raw["yardsToEndzone"]) == (raw_yard_line, espn_yards_to_endzone)
    row = tb_dal[index]
    assert (row.offense, row.yard_line, row.yardline_100) == (offense, raw_yard_line, to_goal)
    assert row.state.yardline_100 == to_goal


# --- TB@DAL: the score before each play ----------------------------------------------------------
def test_the_score_before_a_play_excludes_the_play(tb_dal):
    # ESPN's scores on a scoring row are after the play, the extra point included
    assert (tb_dal[11].home_score, tb_dal[11].away_score) == (0, 0)  # DAL's TD + kick -> 7-0
    assert (tb_dal[12].home_score, tb_dal[12].away_score) == (7, 0)  # the TV timeout after it
    assert (tb_dal[34].home_score, tb_dal[34].away_score) == (7, 0)  # TB's TD + kick -> 7-7
    assert (tb_dal[35].home_score, tb_dal[35].away_score) == (7, 7)
    assert (tb_dal[86].home_score, tb_dal[86].away_score) == (7, 7)  # DAL's field goal -> 10-7
    assert (tb_dal[95].home_score, tb_dal[95].away_score) == (10, 7)  # TB's TD -> 10-14
    assert (tb_dal[158].home_score, tb_dal[158].away_score) == (10, 24)  # DAL TD, 2-pt fails
    assert (tb_dal[159].home_score, tb_dal[159].away_score) == (16, 24)
    assert (tb_dal[182].home_score, tb_dal[182].away_score) == (16, 24)  # final 24-16


def test_score_difference_is_from_the_offenses_side(tb_dal):
    # (row, offense, score difference): DAL is the nominal home team, TB the visitor
    for index, offense, diff in [
        (1, "DAL", 0),
        (16, "TB", -7),  # TB trails 7-0
        (41, "DAL", 0),  # 7-7
        (100, "DAL", -4),  # TB leads 14-10
        (115, "TB", 11),  # TB leads 21-10
        (135, "TB", 14),  # 24-10
        (156, "DAL", -14),
        (164, "TB", 8),  # 24-16
        (173, "DAL", -8),
    ]:
        assert tb_dal[index].offense == offense
        assert tb_dal[index].score_diff == diff
        assert tb_dal[index].state.score_diff == diff


# --- TB@DAL: timeouts from "Timeout #N by TEAM" -----------------------------------------------
@pytest.mark.parametrize(
    ("index", "offense", "off_timeouts", "def_timeouts", "why"),
    [
        (1, "DAL", 3, 3, "the first snap"),
        (30, "TB", 3, 2, "right after DAL's timeout #1 (1:07, Q1)"),
        (54, "DAL", 1, 3, "DAL has used #1 and #2 (Q1 1:07, Q2 9:37)"),
        (88, "TB", 3, 1, "DAL's #1 and #2 are gone, its #3 comes at 0:01"),
        (91, "TB", 3, 0, "right after DAL's #3 (Q2 0:01)"),
        (94, "TB", 3, 3, "the second half: everyone has three again"),
        (147, "TB", 2, 3, "4th & 1 right after TB's timeout #1 (Q4 8:36)"),
        (164, "TB", 2, 3, "TB used one in Q4; DAL's come later"),
        (178, "TB", 2, 2, "TB has used one; right after DAL's #1 (Q4 1:11)"),
        (180, "TB", 2, 1, "right after DAL's #2 (Q4 1:04)"),
    ],
)
def test_timeouts_are_counted_per_half_from_the_timeout_rows(
    tb_dal, index, offense, off_timeouts, def_timeouts, why
):
    row = tb_dal[index]
    assert row.offense == offense, why
    assert (row.off_timeouts, row.def_timeouts) == (off_timeouts, def_timeouts), why
    assert (row.state.off_timeouts, row.state.def_timeouts) == (off_timeouts, def_timeouts)


def test_the_timeout_row_itself_is_not_a_snap_but_changes_the_next_one(tb_dal):
    assert tb_dal[29].type == "Timeout"
    assert tb_dal[29].text == "Timeout #1 by DAL at 01:07."
    assert tb_dal[29].off_timeouts is None
    assert tb_dal[30].def_timeouts == 2


# --- TB@DAL: the second-half kickoff -----------------------------------------------------------
def test_the_opening_kickoff_decides_who_receives_in_the_second_half(tb_dal):
    # TB kicked off to start the game, so DAL received and TB gets the second-half kickoff
    assert opening_receiver(fixture(TB_DAL)) == "DAL"
    first_half = [r for r in tb_dal if r.snap and r.period <= 2]
    assert len(first_half) > 60
    assert {r.state.receive_2h_ko for r in first_half if r.offense == "DAL"} == {0}
    assert {r.state.receive_2h_ko for r in first_half if r.offense == "TB"} == {1}
    assert all("opening kickoff unknown" not in " ".join(r.warnings) for r in first_half)
    second_half = [r for r in tb_dal if r.snap and r.period >= 3]
    assert {r.state.receive_2h_ko for r in second_half} == {0}  # only the first half has one


# --- TB@DAL: what the offense did ----------------------------------------------------------------
def test_choices_of_the_147_snaps(tb_dal):
    counts: dict[str, int] = {}
    for r in tb_dal:
        if r.snap:
            counts[r.choice] = counts.get(r.choice, 0) + 1
    # 18 penalties that wiped the play out ("... - No Play."), 7 punts, 2 field goals (both
    # good), 2 kneel-downs, and 118 runs and passes
    assert counts == {"go": 118, "penalty": 18, "punt": 7, "fg": 2, "kneel": 2}


@pytest.mark.parametrize(
    ("index", "choice"),
    [
        (42, "punt"),
        (47, "punt"),  # a punt with a holding penalty after it: the punt stood
        (86, "fg"),
        (122, "fg"),
        (180, "kneel"),
        (181, "kneel"),
        (23, "penalty"),  # a sack nullified by defensive holding: "- No Play."
        (66, "penalty"),  # (Field Goal formation) delay of game on 4th & 5: "- No Play."
        (7, "penalty"),  # a run nullified by a defensive offside
        (57, "go"),  # a run with a 15-yard face mask added on after it: the play counts
        (3, "go"),
    ],
)
def test_what_the_offense_did(tb_dal, index, choice):
    assert tb_dal[index].choice == choice


@pytest.mark.parametrize(
    ("index", "converted"),
    [
        (3, True),  # 3rd & 2, pass for 8
        (16, True),  # 3rd & 4, scramble for 5
        (142, True),  # 3rd & 1, run for 4
        (156, True),  # 3rd & 2, run for 5
        (173, True),  # 3rd & 1, run for 2
        (41, False),  # 3rd & 10, incomplete
        (103, False),  # 3rd & 3, intercepted
        (145, False),  # 3rd & 3, pass for 2 -> 4th & 1
        (129, False),  # 4th & 3, incomplete: turnover on downs
        (147, False),  # 4th & 1 at the 1, no gain: turnover on downs
        (1, None),  # a 1st down isn't a conversion attempt
        (42, None),  # a punt isn't a "go"
        (23, None),  # a penalty that wiped the play out
    ],
)
def test_a_go_on_third_or_fourth_down_says_whether_it_converted(tb_dal, index, converted):
    assert tb_dal[index].converted is converted


# --- TB@DAL: the 3rd / 4th downs that get scored ----------------------------------------------
def test_decision_plays_are_third_and_fourth_downs_without_wiped_out_plays(tb_dal):
    got = decision_plays(tb_dal)
    # 26 third and 12 fourth-down snaps, 4 of them wiped out by a "No Play" penalty
    assert len(got) == 34
    assert {r.down for r in got} == {3, 4}
    assert all(r.snap and r.state is not None and r.choice != "penalty" for r in got)
    indexes = [tb_dal.index(r) for r in got]
    assert indexes == sorted(indexes)
    assert not {23, 64, 135, 66} & set(indexes)  # the penalties are left out


def test_decision_plays_can_be_limited_to_some_downs(tb_dal):
    fourth = decision_plays(tb_dal, (4,))
    assert [tb_dal.index(r) for r in fourth] == [42, 47, 55, 67, 73, 79, 86, 122, 129, 147, 165]
    assert len(decision_plays(tb_dal, [3])) == 23
    assert len(decision_plays(tb_dal, iter([3, 4]))) == 34  # any iterable
    # 109 snaps on 1st and 2nd down, 14 wiped out by a penalty and 2 kneel-downs left out
    assert len(decision_plays(tb_dal, (1, 2))) == 93
    assert decision_plays(tb_dal, ()) == []
    assert decision_plays([], (3, 4)) == []
    assert decision_plays(iter(tb_dal[:3])) == []  # a kickoff and two early downs


def test_decision_plays_need_a_snap_with_a_state():
    def row(**kw):
        base = dict(
            play_id="1", nflverse_play_id=1, sequence=1, period=1, clock=100.0,
            display_clock="1:40", type="Rush", text="x", wallclock=None, snap=True, down=4,
            choice="go",
        )  # fmt: skip
        return ReplayPlay(**{**base, **kw})

    state = GameState(
        season=2026, score_diff=0, game_seconds=3000.0, half_seconds=1200.0, down=4,
        ydstogo=2, yardline_100=40,
    )  # fmt: skip
    good = row(state=state)
    assert decision_plays([good]) == [good]
    assert decision_plays([row(state=None)]) == []  # a snap the engine couldn't build
    assert decision_plays([row(state=state, snap=False)]) == []
    assert decision_plays([row(state=state, choice="penalty")]) == []
    assert decision_plays([row(state=state, choice="kneel")]) == []  # clock plays aren't calls
    assert decision_plays([row(state=state, choice="spike")]) == []
    assert decision_plays([row(state=state, down=2)]) == []
    assert decision_plays([row(state=state, down=2)], (2,)) == [row(state=state, down=2)]


def test_a_scoring_plays_snap_clock_is_estimated_not_espns_clock_at_the_score(tb_dal):
    # ESPN stamps a scoring play with the clock at the score; the engine needs the snap's
    td = tb_dal[11]  # DAL's touchdown on 2nd & 1, shown at 10:51; the play before was at 10:58
    assert td.display_clock == "10:51"
    assert 651.0 <= td.clock <= 658.0  # never below the clock at the score, never above the
    # previous snap's (the clock only runs down)
    assert td.state.game_seconds == 2700.0 + td.clock
    run = tb_dal[110]  # TB's touchdown in Q3, shown at 7:31; the play before was at 8:13
    assert run.display_clock == "7:31"
    assert 451.0 < run.clock <= 493.0
    assert run.state.game_seconds == 900.0 + run.clock
    assert any("is the score's" in w for w in run.warnings)  # it says the clock was estimated
    plain_play = tb_dal[10]  # an ordinary snap keeps ESPN's clock and has no warning
    assert (plain_play.display_clock, plain_play.clock, plain_play.warnings) == ("10:58", 658.0, [])


# --- TB@DAL: the nominal home team is nflverse's, not ESPN's ----------------------------------
def test_a_neutral_game_where_espn_and_nflverse_disagree_on_home_replays_the_same_states():
    summary = fixture(TB_DAL)
    straight = replay(summary, ctx("tb_dal", neutral_site=True))
    other_home = ctx("tb_dal", neutral_site=True, home="TB", away="DAL", home_spread=-8.5)
    swapped = replay(summary, other_home)
    assert len(straight) == len(swapped)
    for a, b in zip(straight, swapped, strict=True):
        assert (a.snap, a.offense, a.yardline_100, a.score_diff) == (
            b.snap, b.offense, b.yardline_100, b.score_diff,
        )  # fmt: skip
        assert a.state == b.state  # the offense's view doesn't depend on who is "home"
        if a.snap:
            assert a.offense_home == (a.offense == "DAL")
            assert b.offense_home == (b.offense == "TB")
    # the scores are re-sided: nflverse's home team (TB) first
    assert (swapped[158].home_score, swapped[158].away_score) == (24, 10)
    assert (straight[158].home_score, straight[158].away_score) == (10, 24)
    assert swapped[16].state.home == 0  # a neutral site has no home team


def test_replay_does_not_change_the_summary_it_reads():
    summary = fixture(TB_DAL)
    before = json.dumps(summary, sort_keys=True)
    replay(summary, ctx("tb_dal"))
    assert json.dumps(summary, sort_keys=True) == before


def test_espns_play_text_is_cleaned_and_capped_but_stays_plain_text():
    summary = fixture(TB_DAL)
    plays = {p["id"]: p for d in summary["drives"]["previous"] for p in d["plays"]}
    plays["401872980116"]["text"] = "<b>bold</b>\x00 \x07 &amp; " + "word " * 200
    plays["4018729801012"]["text"] = "<script>x</script>  pass\n\tcomplete"
    rows = {r.play_id: r for r in replay(summary, ctx("tb_dal"))}
    long = rows["401872980116"]
    assert long.text.startswith("<b>bold</b> &amp; word word")  # not interpreted, not escaped
    assert len(long.text) == 400 and long.text.endswith("…")
    assert "\x00" not in long.text and "\x07" not in long.text
    assert not any(ord(c) < 32 for c in long.text)
    assert rows["4018729801012"].text == "<script>x</script> pass complete"


def test_a_wiped_out_play_with_a_long_text_is_still_a_penalty():
    # replay() used to cut the text to 400 characters before reading it (no real play is that
    # long: 11,976 saved plays, the longest has 521 characters, every keyword before 400)
    summary = fixture(TB_DAL)
    plays = {p["id"]: p for d in summary["drives"]["previous"] for p in d["plays"]}
    note = "** Injury Update: DAL-S.Revel has returned to the game. " * 9  # 495 characters
    play = plays["401872980615"]  # the sack nullified by defensive holding (row 23)
    play["text"] = note + play["text"]
    rows = replay(summary, ctx("tb_dal"))
    assert rows[23].play_id == "401872980615"
    assert len(play["text"]) > 400
    assert rows[23].choice == "penalty"


def test_replay_play_serialises_for_the_cli(tb_dal):
    row = tb_dal[16]
    back = json.loads(json.dumps(row.as_dict()))
    assert back["score_diff"] == -7
    assert back["wallclock"] == row.wallclock.isoformat()
    assert back["state"]["down"] == 3
    assert back["choice"] == "go" and back["converted"] is True
    kickoff = json.loads(json.dumps(tb_dal[0].as_dict()))
    assert kickoff["state"] is None and kickoff["score_diff"] is None
    assert tb_dal[0].score_diff is None


# --- IND@KC (overtime), checked against nflverse ----------------------------------------------
def test_ind_kc_counts(ind_kc):
    # 213 rows = 163 snaps + 15 kickoffs + 35 rows that aren't plays
    assert len(ind_kc) == 213
    assert sum(r.snap for r in ind_kc) == 163
    assert sum(r.state is not None for r in ind_kc) == 163
    assert len(decision_plays(ind_kc)) == 40  # 29 third and 16 fourth-down snaps, 5 wiped out
    assert len(decision_plays(ind_kc, (4,))) == 15
    assert ind_kc[176].type == "End of Regulation"
    assert ind_kc[176].snap is False
    assert ind_kc[177].type == "Kickoff" and ind_kc[177].snap is False


# nflverse 2026_02_IND_KC: (play_id, offense, down, ydstogo, yardline_100, score_differential,
# game_seconds_remaining, offense timeouts left, defense timeouts left). Plays chosen away from
# one stretch of Q4 where ESPN's feed lost IND's timeout row (see the next test).
NFLVERSE = [
    (109, "IND", 3, 1, 56, 0, 3524, 3, 3),
    (282, "IND", 3, 4, 14, 0, 3274, 3, 3),
    (508, "KC", 3, 9, 28, -7, 3077, 3, 3),
    (531, "KC", 4, 9, 28, -7, 3073, 3, 3),  # a field goal try: scoring plays are estimated
    (813, "IND", 4, 1, 49, -3, 2847, 2, 3),  # IND called a timeout earlier in the half
    (1158, "KC", 4, 3, 33, 0, 2567, 3, 2),
    (2182, "KC", 4, 10, 80, -3, 1846, 2, 1),
    (2584, "KC", 4, 1, 1, -3, 1386, 3, 3),  # 4th & goal, a touchdown pass
    (3945, "IND", 3, 4, 51, 0, 40, 1, 3),
    (4046, "KC", 3, 2, 76, 0, 16, 2, 1),
    (4199, "KC", 1, 10, 68, 0, 593, 2, 2),  # the first snap of overtime
    (4241, "KC", 3, 12, 70, 0, 518, 2, 2),
    (4455, "KC", 4, 9, 13, 0, 313, 2, 2),  # an overtime field goal try
    (4667, "IND", 4, 8, 15, -3, 195, 1, 2),  # nflverse: no_play (delay of game)
    (4698, "IND", 4, 13, 20, -3, 195, 1, 2),
    (4843, "KC", 3, 1, 46, 0, 90, 2, 0),
    (4875, "KC", 1, 10, 24, 0, 42, 1, 0),
    (4908, "KC", 2, 8, 22, 0, 1, 0, 0),  # the winning field goal try with a second left
]


@pytest.mark.parametrize(
    ("play", "offense", "down", "to_go", "to_goal", "diff", "seconds", "off_to", "def_to"),
    NFLVERSE,
)
def test_ind_kc_states_match_nflverse(
    ind_kc, play, offense, down, to_go, to_goal, diff, seconds, off_to, def_to
):
    row = next(r for r in ind_kc if r.nflverse_play_id == play)
    assert row.snap
    assert (row.offense, row.down, row.distance, row.yardline_100) == (
        offense, down, to_go, to_goal,
    )  # fmt: skip
    assert row.score_diff == diff
    assert (row.off_timeouts, row.def_timeouts) == (off_to, def_to)
    st = row.state
    assert (st.score_diff, st.down, st.ydstogo, st.yardline_100) == (diff, down, to_go, to_goal)
    assert (st.off_timeouts, st.def_timeouts) == (off_to, def_to)
    assert abs(st.game_seconds - seconds) <= 5  # the parity check's tolerance


def test_overtime_is_ten_minutes_with_two_timeouts_each_whatever_regulation_left(ind_kc):
    ot = [r for r in ind_kc if r.snap and r.period == 5]
    assert len(ot) == 25  # 36 overtime rows: 3 kickoffs, 7 timeouts, the warning, the end
    assert all(r.state.ot for r in ot)
    assert all(r.state.game_seconds == r.state.half_seconds <= 600 for r in ot)
    first = ot[0]
    assert (first.display_clock, first.offense) == ("9:53", "KC")
    # both teams used all three regulation timeouts by the end of the 4th quarter ("#3 by KC at
    # 0:03", "#3 by IND at 0:03") but overtime starts with two each
    assert [(r.off_timeouts, r.def_timeouts) for r in ot[:3]] == [(2, 2)] * 3
    assert all(r.state.receive_2h_ko == 0 for r in ot)
    assert not any("opening kickoff unknown" in w for r in ot for w in r.warnings)


def test_playoff_overtime_gives_three_timeouts_each_in_a_two_period_half():
    # the same game as if it were a playoff game: 15-minute periods and three timeouts each
    rows = replay(fixture(IND_KC), ctx("ind_kc", playoffs=True))
    first = next(r for r in rows if r.snap and r.period == 5)
    assert (first.off_timeouts, first.def_timeouts) == (3, 3)
    assert first.state.playoffs is True and first.state.ot is True
    by_id = {r.nflverse_play_id: r for r in rows}
    assert (by_id[4667].off_timeouts, by_id[4667].def_timeouts) == (2, 3)  # after IND's #1
    assert (by_id[4908].off_timeouts, by_id[4908].def_timeouts) == (1, 1)  # after KC's #2


def test_overtime_timeouts_follow_the_overtime_rows(ind_kc):
    # IND: #1 at 3:15 and #2 at 1:30; KC: #1 at 0:42 and #2 at 0:01
    by_id = {r.nflverse_play_id: r for r in ind_kc}
    assert (by_id[4667].off_timeouts, by_id[4667].def_timeouts) == (1, 2)  # IND 4th & 8, after #1
    assert (by_id[4843].off_timeouts, by_id[4843].def_timeouts) == (2, 0)  # KC; IND has none left
    assert (by_id[4875].off_timeouts, by_id[4875].def_timeouts) == (1, 0)  # after KC's #1
    assert (by_id[4908].off_timeouts, by_id[4908].def_timeouts) == (0, 0)  # after KC's #2


def test_regulation_timeouts_are_per_half_across_a_game_with_overtime(ind_kc):
    q4 = {r.nflverse_play_id: r for r in ind_kc if r.period == 4}
    assert (q4[3945].off_timeouts, q4[3945].def_timeouts) == (1, 3)  # IND called #1 and #2
    assert (q4[4046].off_timeouts, q4[4046].def_timeouts) == (2, 1)  # after KC's #1, IND's #2


def test_the_opening_receiver_and_second_half_kickoff_in_ind_kc(ind_kc):
    assert opening_receiver(fixture(IND_KC)) == "IND"  # KC kicked off
    first = [r for r in ind_kc if r.snap and r.period <= 2]
    assert {r.state.receive_2h_ko for r in first if r.offense == "IND"} == {0}
    assert {r.state.receive_2h_ko for r in first if r.offense == "KC"} == {1}


def test_scoring_snaps_are_within_five_seconds_of_nflverse(ind_kc):
    # nflverse game_seconds_remaining of the snap vs the estimate, for the scoring plays above
    by_id = {r.nflverse_play_id: r for r in ind_kc}
    for play, nv_seconds in [(531, 3073), (1004, 2692), (1397, 2390), (2584, 1386), (4455, 313),
                             (4698, 195), (4908, 1)]:  # fmt: skip
        assert abs(by_id[play].state.game_seconds - nv_seconds) <= 5, play


def test_non_scoring_snaps_keep_espns_clock_exactly_as_nflverse_has_it(ind_kc):
    # only a scoring play's clock is "at the score"; every other snap's ESPN clock is the clock
    # at the start of the play, which is nflverse's game_seconds_remaining to the second
    scoring = {
        p["id"]
        for d in fixture(IND_KC)["drives"]["previous"]
        for p in d["plays"]
        if p.get("scoringPlay")
    }
    by_id = {r.nflverse_play_id: r for r in ind_kc}
    checked = 0
    for play, *_, seconds, _off, _def in NFLVERSE:
        row = by_id[play]
        if row.play_id in scoring:
            continue
        assert row.state.game_seconds == seconds, play
        assert row.warnings == [], play
        checked += 1
    assert checked >= 10


def test_a_feed_gap_in_ind_kc_is_where_the_replay_differs_from_nflverse(ind_kc):
    # Q4 9:38: nflverse has IND calling a timeout before this snap, but ESPN's feed has no
    # "Timeout" row for it (the play's text is the odd "Daniel Jones Pass Complete for 5 Yds ...")
    # so the replay still shows 3. The next numbered row (IND's "#2" at 0:40) corrects the count.
    row = next(r for r in ind_kc if r.nflverse_play_id == 3402)
    assert row.text.startswith("Daniel Jones Pass Complete for 5 Yds")
    assert row.off_timeouts == 3  # nflverse: 2
    later = next(r for r in ind_kc if r.nflverse_play_id == 3945)
    assert later.off_timeouts == 1  # nflverse: 1


# --- choice_of on texts -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("type_text", "text", "choice"),
    [
        ("Rush", "J.Williams up the middle to TB 29 for 21 yards (T.Smith).", "go"),
        ("Pass Reception", "(Shotgun) D.Prescott pass short left to G.Pickens to DAL 46.", "go"),
        ("Pass Incompletion", "D.Prescott pass incomplete short middle to B.Spann-Ford.", "go"),
        ("Sack", "(Shotgun) J.Daniels sacked at DAL 48 for -11 yards (M.Lawrence).", "go"),
        ("Passing Touchdown", "(Shotgun) D.Prescott pass deep left to G.Pickens, TOUCHDOWN.", "go"),
        ("Pass Interception Return", "D.Prescott pass intended for R.Flournoy INTERCEPTED.", "go"),
        # a fake punt is typed "Rush" and says so in the text: it is a run
        ("Rush", "(Punt formation) M.Araiza right end to KC 42 for 12 yards (C.Heyward).", "go"),
        ("Punt", "B.Anger punts 53 yards to TB 3, Center-T.Sieg, out of bounds.", "punt"),
        ("Blocked Punt", "A.Punter punts, BLOCKED by X.", "punt"),
        ("Muffed Punt Recovery (Opponent)", "R.Dixon punts 40 yards, MUFFS.", "punt"),
        (
            "Punt Return Touchdown",
            "R.Dixon punts 40 yards. K.Turpin for 60 yards, TOUCHDOWN.",
            "punt",
        ),
        ("Field Goal Good", "B.Aubrey 41 yard field goal is GOOD, Center-T.Sieg.", "fg"),
        ("Field Goal Missed", "B.Aubrey 51 yard field goal is No Good, Wide Left.", "fg"),
        ("Blocked Field Goal", "B.Aubrey 48 yard field goal is BLOCKED.", "fg"),
        ("Rush", "J.Daniels kneels to TB 31 for -1 yards.", "kneel"),
        ("Rush", "J.DANIELS KNEELS to TB 30 for -1 yards.", "kneel"),
        ("Pass Incompletion", "(No Huddle) T.Lawrence spiked the ball to stop the clock.", "spike"),
        # a penalty that wiped the play out: ESPN says "No Play" and types it "Penalty"
        (
            "Penalty",
            "(Shotgun) PENALTY on DAL-R.Gary, Neutral Zone Infraction - No Play.",
            "penalty",
        ),
        ("Penalty", "(Punt formation) PENALTY on NE, Delay of Game, 5 yards - No Play.", "penalty"),
        (
            "Penalty",
            "(Field Goal formation) PENALTY on TB, Delay of Game, 5 yards - NO PLAY",
            "penalty",
        ),
        # a penalty after a play that stood: the play still counts
        (
            "Penalty",
            "R.Dixon punts 41 yards to TB 49. PENALTY on DAL-S.James, Holding, 10 yards.",
            "punt",
        ),
        ("Penalty", "B.Aubrey 41 yard field goal is GOOD. PENALTY on TB, Offside, declined.", "fg"),
        (
            "Penalty",
            "J.Williams up the middle for 3 yards. PENALTY on TB, Face Mask, 15 yards.",
            "go",
        ),
        (
            "Rush",
            "B.Irving up the middle to TB 12 for 2 yards. PENALTY on DAL, Face Mask, 15 yards.",
            "go",
        ),
    ],
)
def test_choice_of_reads_the_play_type_and_text(type_text, text, choice):
    assert choice_of(type_text, text) == choice


@pytest.mark.parametrize(
    ("type_text", "text"),
    [
        (
            "Pass Incompletion",
            "(Shotgun) B.Young pass incomplete short middle to J.Coker (K.Daniels).PENALTY on "
            "CAR-R.Hunt, Illegal Use of Hands, 10 yards, enforced at ATL 21 - No Play.",
        ),
        (
            "Rush",
            "(Shotgun) C.Hubbard left guard to CAR 47 for 6 yards (D.Deablo).PENALTY on "
            "CAR-L.Fortner, Offensive Holding, 10 yards, enforced at CAR 41 - No Play.",
        ),
        (
            "Pass Reception",
            "(Shotgun) B.Mayfield pass short middle to C.Godwin to TB 40 for 9 yards "
            "(C.Schwesinger; R.Hickman).PENALTY on TB-B.Bredeson, Offensive Holding, 10 yards, "
            "enforced at TB 42 - No Play.",
        ),
        (
            "Sack",
            "(Shotgun) M.Mariota sacked at WAS 28 for -7 yards (D.Winters).PENALTY on "
            "DAL-D.Bland, Defensive Holding, 5 yards, enforced at WAS 35 - No Play.",
        ),
        (
            "Punt",
            "C.Waitman punts 46 yards to MIA 11, Center-J.Weeks, fair catch by M.Washington."
            "Penalty on SF-J.Weeks, Offensive Holding, offsetting, enforced at SF 43 - No Play.",
        ),
    ],
    ids=["incompletion", "rush", "reception", "sack", "punt"],
)
def test_a_play_wiped_out_by_a_penalty_is_a_penalty_whatever_espn_calls_the_play(type_text, text):
    # ESPN types 73 such snaps of the saved games (weeks 1-5) by the play that was run; nflverse
    # calls 72 of them no_play
    assert choice_of(type_text, text) == "penalty"


def test_a_punt_with_a_fumbled_return_is_still_a_punt():
    text = (
        "C.Waitman punts 61 yards to DEN 28, Center-J.Weeks. M.Mims to DEN 29 for 1 yard "
        "(S.Neal). FUMBLES (S.Neal), RECOVERED by SF-S.Neal at DEN 28."
    )
    # ESPN types it "Fumble Recovery (Opponent)"; nflverse: punt
    assert choice_of("Fumble Recovery (Opponent)", text) == "punt"


@pytest.mark.parametrize(
    ("type_text", "text"),
    [
        (
            "Pass Interception Return",
            "(Shotgun) D.Watson pass deep middle intended for D.Boston INTERCEPTED by "
            "R.Spears-Jennings [J.Sawyer] at PIT 23. R.Spears-Jennings to PIT 23 for no gain "
            "(D.Boston).PENALTY on PIT-D.Everette, Defensive Pass Interference, 42 yards, "
            "enforced at CLV 35 - No Play.The Replay Official reviewed the pass was not tipped "
            "ruling, and the play was REVERSED.(Shotgun) D.Watson pass deep middle intended for "
            "D.Boston INTERCEPTED by R.Spears-Jennings (J.Sawyer) [J.Sawyer] at PIT 23. "
            "R.Spears-Jennings to PIT 23 for no gain (D.Boston).",
        ),
        (
            "Penalty",
            "(Shotgun) J.Allen scrambles right end to LAC 32 for 8 yards (A.Mesidor; C.Hart). "
            "FUMBLES (A.Mesidor), RECOVERED by LAC-T.Tuipulotu at LAC 27.PENALTY on "
            "LAC-R.Shelley, Defensive Holding, 5 yards, enforced at LAC 40 - No Play.The Replay "
            "Official reviewed the runner was not down by contact ruling, and the play was "
            "REVERSED.(Shotgun) J.Allen scrambles right end to LAC 32 for 8 yards (C.Hart; "
            "A.Mesidor).PENALTY on LAC-R.Shelley, Defensive Holding, 5 yards, enforced at LAC 32.",
        ),
    ],
    ids=["interception", "run"],
)
def test_a_play_the_replay_official_put_back_is_not_a_wiped_out_play(type_text, text):
    # when a review reverses the call ESPN keeps the first ruling's "- No Play." in the text and
    # appends the final one: the text after "REVERSED" decides (nflverse: pass / run)
    assert choice_of(type_text, text) == "go"


# --- the timeout window -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("period", "playoffs", "window"),
    [
        (1, False, ("h1", 3)),
        (2, False, ("h1", 3)),
        (3, False, ("h2", 3)),
        (4, False, ("h2", 3)),
        (5, False, ("ot", 2)),  # regular-season overtime: two each, one period
        (6, False, ("ot", 2)),
        (1, True, ("h1", 3)),
        (4, True, ("h2", 3)),
        (5, True, ("ot0", 3)),  # playoff overtime: three each per two-period half
        (6, True, ("ot0", 3)),
        (7, True, ("ot1", 3)),
        (8, True, ("ot1", 3)),
    ],
)
def test_timeout_window(period, playoffs, window):
    assert timeout_window(period, playoffs) == window


# --- load_summary -----------------------------------------------------------------------------
class FakeClient:
    """Stands in for `EspnClient`: serves a prepared summary and records how it is used."""

    made: list[FakeClient] = []

    def __init__(self, data=None):
        self.data = data
        self.calls: list[str] = []
        self.closed = False
        FakeClient.made.append(self)

    def summary(self, event_id):
        self.calls.append(event_id)
        if isinstance(self.data, Exception):
            raise self.data
        return SimpleNamespace(data=json.loads(json.dumps(self.data)))

    def close(self):
        self.closed = True


def test_summary_state_reads_espns_game_state():
    assert summary_state(fixture(TB_DAL)) == "post"
    assert summary_state(fixture("summary_401872966_ari_nyg_cut_at_timeout.json")) == "in"
    assert summary_state({}) == "pre"
    assert summary_state({"header": {"competitions": [{}]}}) == "pre"


def test_summary_path_checks_the_event_id(tmp_path):
    assert summary_path(tmp_path, "401872980") == tmp_path / "401872980.json.gz"
    for bad in ("../401872980", "abc", "", "401872980/x"):
        with pytest.raises(ValueError):
            summary_path(tmp_path, bad)


def test_a_finished_game_is_saved_once_then_read_from_disk(tmp_path):
    folder = tmp_path / "summaries"
    data = fixture(TB_DAL)
    client = FakeClient(data)
    got, source = load_summary("401872980", folder, client)
    assert source == "ESPN"
    assert got == data
    path = folder / "401872980.json.gz"
    assert json.loads(gzip.decompress(path.read_bytes())) == data
    assert sorted(p.name for p in folder.iterdir()) == ["401872980.json.gz"]  # no .tmp left
    again, source = load_summary("401872980", folder, client)
    assert (source, again) == ("saved", data)
    assert client.calls == ["401872980"]  # the second call never asked ESPN
    assert client.closed is False  # a client the caller owns stays open


def test_refresh_asks_espn_again_and_rewrites_the_file(tmp_path):
    data = fixture(TB_DAL)
    load_summary("401872980", tmp_path, FakeClient(data))
    corrected = fixture(TB_DAL)
    corrected["drives"]["previous"][0]["plays"][1]["text"] = "corrected by ESPN"
    client = FakeClient(corrected)
    got, source = load_summary("401872980", tmp_path, client, refresh=True)
    assert source == "ESPN"
    assert client.calls == ["401872980"]
    saved = json.loads(gzip.decompress((tmp_path / "401872980.json.gz").read_bytes()))
    assert saved["drives"]["previous"][0]["plays"][1]["text"] == "corrected by ESPN"
    assert got == corrected


@pytest.mark.parametrize("name", ["summary_401872966_ari_nyg_cut_at_timeout.json"])
def test_a_game_still_running_is_never_saved(tmp_path, name):
    folder = tmp_path / "summaries"
    got, source = load_summary("401872966", folder, FakeClient(fixture(name)))
    assert source == "ESPN"
    assert summary_state(got) == "in"
    assert not folder.exists()  # not even the folder
    pre = {"header": {"competitions": [{"status": {"type": {"state": "pre"}}}]}}
    load_summary("401872999", folder, FakeClient(pre))
    assert not folder.exists()


def test_a_saved_file_is_read_without_any_client(tmp_path):
    data = {"header": {"id": "1"}, "marker": "from disk"}
    (tmp_path / "401872980.json.gz").write_bytes(gzip.compress(json.dumps(data).encode()))
    got, source = load_summary("401872980", tmp_path)  # no client: none is created or needed
    assert (got, source) == (data, "saved")


def test_a_client_made_here_is_closed_even_when_espn_fails(tmp_path, monkeypatch):
    FakeClient.made.clear()
    monkeypatch.setattr(R, "EspnClient", lambda: FakeClient(fixture(TB_DAL)))
    got, source = load_summary("401872980", tmp_path)
    assert source == "ESPN" and summary_state(got) == "post"
    assert len(FakeClient.made) == 1
    assert FakeClient.made[0].closed is True
    FakeClient.made.clear()
    monkeypatch.setattr(R, "EspnClient", lambda: FakeClient(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        load_summary("401872981", tmp_path)
    assert FakeClient.made[0].closed is True
    assert not (tmp_path / "401872981.json.gz").exists()


def test_load_summary_through_the_real_client_and_a_fake_transport(tmp_path):
    seen = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=fixture(TB_DAL))

    clock = FakeClock()
    client = EspnClient(
        transport=httpx.MockTransport(serve),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now=clock.now,
    )
    got, source = load_summary("401872980", tmp_path, client)
    assert seen == [f"{BASE}/summary?event=401872980"]
    assert source == "ESPN"
    assert len(replay(got, ctx("tb_dal"))) == 183
    assert (tmp_path / "401872980.json.gz").exists()


def test_load_summary_refuses_an_event_id_that_is_not_digits(tmp_path):
    client = FakeClient({})
    with pytest.raises(ValueError):
        load_summary("../../x", tmp_path, client)
    assert client.calls == []
