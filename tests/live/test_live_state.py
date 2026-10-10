"""LD01: ESPN's JSON -> the decision engine's `GameState` (live/state.py).

The expected values are worked out by hand from the saved ESPN answers in
`tests/fixtures/live/` (real answers, trimmed; see the README there) and from football rules:
yards to goal from `yardLine` and whose goal line it counts from, the score and timeouts from
the offense's side, the spread from the offense's side, nflverse's clock convention. Nothing
reaches the network or the data drive."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

import polars as pl
import pytest
from live_fakes import (
    ctx,
    fixture,
    no_real_network,  # noqa: F401  (autouse guard)
    summary_upto,
)

from nflengine.live.schema import GameState
from nflengine.live.state import (
    Contexts,
    GameContext,
    LiveState,
    Snap,
    build_state,
    clock_seconds,
    competition,
    event_teams,
    game_clock,
    last_real_play,
    load_contexts,
    opening_receiver,
    parse_event,
    parse_time,
    plain,
    play_times,
    pre_game_lines,
    summary_plays,
    yards_to_goal,
)
from nflengine.paths import DataPaths

NO_SNAP = "between plays: no snap pending (a try, a kickoff or a TV timeout)"
FETCHED = datetime(2026, 10, 10, 15, 30, 0, tzinfo=UTC)


def week5_event(event_id: str) -> dict:
    return next(e for e in fixture("scoreboard_week5.json")["events"] if e["id"] == event_id)


# --- plain ------------------------------------------------------------------------------------
def test_plain_removes_control_characters_and_collapses_whitespace():
    assert plain("a\x00b\x07c\x1bd\x7fe") == "a b c d e"
    assert plain("  two \n\n lines\t and \r\n tabs  ") == "two lines and tabs"
    assert plain("\x0b\x0cx") == "x"
    assert plain("   ") == ""
    assert plain("") == ""


def test_plain_returns_none_for_none_and_text_for_everything_else():
    assert plain(None) is None
    assert plain(12) == "12"
    assert plain(["a", "b"]) == "['a', 'b']"


def test_plain_caps_the_length_with_an_ellipsis():
    assert plain("x" * 300) == "x" * 300
    out = plain("x" * 301)
    assert len(out) == 300
    assert out == "x" * 299 + "…"
    assert plain("abcdefghij", limit=5) == "abcd…"
    assert plain("abcde", limit=5) == "abcde"


def test_plain_never_interprets_html_it_only_cleans_control_characters():
    text = '<b>bold</b> & <script>alert("x")</script> &lt;i&gt; <img src=x onerror=alert(1)>'
    assert plain(text) == text  # kept as inert text; the app shows it as text, never as HTML


# --- clock_seconds, game_clock, yards_to_goal, parse_time --------------------------------------
@pytest.mark.parametrize(
    ("display", "seconds"),
    [
        ("12:36", 756.0),
        ("0:21", 21.0),
        ("15:00", 900.0),
        ("0:00", 0.0),
        (":42", 42.0),
        (" 7:04 ", 424.0),
        ("10:00", 600.0),
        ("0:21.5", 21.0),
        (651.0, 651.0),
        (651, 651.0),
        (0.0, 0.0),
        (None, None),
        ("", None),
        ("abc", None),
        ("12", None),
        ("1:234", None),
        ("12:3x", None),
        ("Halftime", None),
    ],
)
def test_clock_seconds_reads_espns_forms(display, seconds):
    assert clock_seconds(display) == seconds


@pytest.mark.parametrize(
    ("period", "clock", "game", "half", "ot"),
    [
        (1, 900, 3600, 1800, False),  # the opening kickoff
        (1, 0, 2700, 900, False),
        (2, 900, 2700, 900, False),
        (2, 0, 1800, 0, False),  # the end of the first half
        (3, 900, 1800, 1800, False),  # the second-half kickoff
        (3, 130, 1030, 1030, False),
        (3, 0, 900, 900, False),
        (4, 900, 900, 900, False),
        (4, 451, 451, 451, False),
        (4, 0, 0, 0, False),
        (5, 600, 600, 600, True),  # overtime: both clocks are the overtime clock
        (5, 0, 0, 0, True),
        (6, 300, 300, 300, True),
    ],
)
def test_game_clock_follows_nflverses_convention(period, clock, game, half, ot):
    assert game_clock(period, float(clock)) == (float(game), float(half), ot)


@pytest.mark.parametrize(
    ("yard_line", "offense_is_espn_home", "to_goal"),
    [
        (86, False, 86),  # TEN at its own 14, away: "4th & 10 at TEN 14" is 86 from BAL's goal
        (95, True, 5),  # DAL at the BAL 5, DAL the ESPN home team: "3rd & Goal at BAL 5"
        (2, False, 2),  # SF at the LAR 2, SF away: "3rd & Goal at LAR 2"
        (98, True, 2),  # PHI at the LA 2, PHI home
        (30, True, 70),  # DAL at its own 30
        (65, False, 65),
        (50, True, 50),
        (50, False, 50),
        (100, True, 0),
        (0, False, 0),
    ],
)
def test_yards_to_goal_counts_from_the_home_goal_line(yard_line, offense_is_espn_home, to_goal):
    assert yards_to_goal(yard_line, offense_is_espn_home) == to_goal


def test_yards_to_goal_takes_numbers_stored_as_floats():
    assert yards_to_goal(86.0, False) == 86
    assert yards_to_goal(95.0, True) == 5


def test_parse_time_reads_espns_iso_forms_as_aware_utc():
    assert parse_time("2026-10-09T00:15:54Z") == datetime(2026, 10, 9, 0, 15, 54, tzinfo=UTC)
    assert parse_time("2026-10-09T03:27Z") == datetime(2026, 10, 9, 3, 27, tzinfo=UTC)
    assert parse_time("2026-10-09T03:27") == datetime(2026, 10, 9, 3, 27, tzinfo=UTC)
    assert parse_time("2026-10-09T00:15:54+00:00").tzinfo is not None
    for junk in (None, "", "yesterday", "2026-13-45T00:00Z"):
        assert parse_time(junk) is None


# --- ESPN JSON helpers -------------------------------------------------------------------------
def test_event_teams_maps_espn_ids_to_canonical_codes_and_scores():
    teams = event_teams(fixture("event_401872980_tb_dal_final.json"))
    assert teams == {
        "6": {"team": "DAL", "espn": "DAL", "home_away": "home", "score": 16},
        "27": {"team": "TB", "espn": "TB", "home_away": "away", "score": 24},
    }


def test_event_teams_normalizes_espns_wsh_and_lar_codes():
    nyg_wsh = event_teams(week5_event("401872988"))
    assert {t["espn"]: t["team"] for t in nyg_wsh.values()} == {"WSH": "WAS", "NYG": "NYG"}
    buf_lar = event_teams(week5_event("401872994"))
    assert {t["espn"]: t["team"] for t in buf_lar.values()} == {"LAR": "LA", "BUF": "BUF"}
    assert next(t for t in buf_lar.values() if t["team"] == "LA")["home_away"] == "home"


def test_event_teams_survives_missing_and_odd_fields():
    event = {
        "competitions": [
            {
                "competitors": [
                    {"id": "1", "homeAway": "home", "team": {"abbreviation": "KC"}, "score": ""},
                    {"id": "2", "homeAway": "away", "team": {"abbreviation": "DEN"}, "score": "x"},
                    {"homeAway": "away", "team": {"id": "3", "abbreviation": "LV"}},
                    {"id": "4", "team": {}},  # no abbreviation: skipped
                    {"team": {"abbreviation": "NE"}},  # no id: skipped
                ]
            }
        ]
    }
    teams = event_teams(event)
    assert set(teams) == {"1", "2", "3"}
    assert [teams[k]["score"] for k in ("1", "2", "3")] == [0, 0, 0]
    assert teams["3"]["team"] == "LV"  # the id fell back to the team's own
    assert event_teams({}) == {}
    assert event_teams({"competitions": []}) == {}


def test_competition_is_the_first_competition_or_empty():
    assert competition({"competitions": [{"a": 1}, {"a": 2}]}) == {"a": 1}
    assert competition({"competitions": []}) == {}
    assert competition({"competitions": [None]}) == {}
    assert competition({}) == {}


def test_summary_plays_are_sorted_by_sequence_and_deduplicated():
    def play(pid, seq, **kw):
        return {"id": pid, "sequenceNumber": seq, **kw}

    summary = {
        "drives": {
            "previous": [
                {"plays": [play("a", "300"), play("b", "100")]},
                {"plays": [play("c", "200"), play("noseq", None), play("bad", "x")]},
                {"plays": [{"sequenceNumber": "5"}]},  # no id: dropped
            ],
            "current": {"plays": [play("c", "200", text="newer copy"), play("d", "400")]},
        }
    }
    got = summary_plays(summary)
    assert [p["id"] for p in got] == ["b", "c", "a", "d", "bad", "noseq"]
    assert next(p for p in got if p["id"] == "c")["text"] == "newer copy"  # the current drive wins
    assert summary_plays({}) == []
    assert summary_plays({"drives": {"previous": None, "current": None}}) == []


def test_opening_receiver_is_the_team_that_did_not_kick_off():
    assert opening_receiver(fixture("summary_401872980_tb_dal.json")) == "DAL"  # TB kicked
    assert opening_receiver(fixture("summary_401872945_ind_kc_ot.json")) == "IND"  # KC kicked


def test_opening_receiver_ignores_later_kickoffs_and_unknown_kickers():
    def kickoff(period, kicker, pid):
        return {
            "id": pid,
            "sequenceNumber": pid,
            "type": {"text": "Kickoff"},
            "period": {"number": period},
            "start": {"team": {"id": kicker}},
        }

    header = fixture("event_401872980_tb_dal_final.json")
    base = {"header": {"competitions": header["competitions"]}}

    def with_plays(*plays):
        return {**base, "drives": {"previous": [{"plays": list(plays)}]}}

    assert opening_receiver(with_plays(kickoff(2, "6", "1"))) is None  # only a 2nd-quarter kick
    assert opening_receiver(with_plays(kickoff(1, "99", "1"))) is None  # kicker isn't in the game
    assert opening_receiver(with_plays(kickoff(1, "27", "1"), kickoff(1, "6", "2"))) == "DAL"
    assert opening_receiver(with_plays()) is None
    assert opening_receiver({}) is None


def test_last_real_play_skips_timeouts_period_ends_and_the_warning():
    summary = fixture("summary_401872966_ari_nyg_cut_at_timeout.json")
    last = summary_plays(summary)[-1]
    assert last["type"]["text"] == "Official Timeout"
    real = last_real_play(summary)
    assert real["id"] == "4018729663022"  # NYG's touchdown
    assert real["type"]["text"] == "Passing Touchdown"
    final = fixture("summary_401872980_tb_dal.json")  # ends: kneel, kneel, "END GAME"
    assert last_real_play(final)["id"] == "4018729804233"
    assert last_real_play({}) is None


def test_play_times_maps_play_ids_to_their_snap_times():
    times = play_times(fixture("summary_401872980_tb_dal.json"))
    assert len(times) == 183
    assert times["40187298040"] == datetime(2026, 10, 9, 0, 15, 54, tzinfo=UTC)
    assert times["4018729801092"] == datetime(2026, 10, 9, 0, 54, 5, tzinfo=UTC)
    assert all(t.tzinfo is not None for t in times.values())
    assert play_times({}) == {}


# --- pre-game lines ------------------------------------------------------------------------------
def k(day: str, hhmm: str = "17:00") -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00+00:00")


def lines_row(game, source, snap, spread, total=45.0):
    return {
        "game_id": game, "source": source, "snapshot_date": snap,
        "home_spread": spread, "total": total,
    }  # fmt: skip


def test_pre_game_lines_prefers_nflverse_then_the_odds_api_median_then_espn():
    games = pl.DataFrame(
        {
            "game_id": ["g1", "g2", "g3", "g4", "g5", "g6", "g7", "g8"],
            "kickoff_utc": [
                k("2026-10-04"), k("2026-10-04"), k("2026-10-04"), k("2026-10-04"),
                k("2026-10-04"), k("2026-10-04"), k("2026-10-04"), k("2026-10-09", "00:15"),
            ],
        }
    )  # fmt: skip
    lines = pl.DataFrame(
        [
            # g1: nflverse's line wins over books and ESPN; its own row may be the closing line,
            # snapshotted on the kickoff day
            lines_row("g1", "nflverse_schedules", "2026-10-04", -2.5, 44.5),
            lines_row("g1", "odds_api", "2026-10-02", -3.0, 45.0),
            lines_row("g1", "espn", "2026-10-02", -2.0, 43.5),
            # g2: the median of three books before kickoff day; a row from kickoff day is out
            lines_row("g2", "odds_api", "2026-10-02", -2.5, 44.0),
            lines_row("g2", "odds_api", "2026-10-02", -3.0, 45.0),
            lines_row("g2", "odds_api", "2026-10-03", -3.5, 46.0),
            lines_row("g2", "odds_api", "2026-10-04", -10.0, 60.0),
            lines_row("g2", "espn", "2026-10-02", -1.0, 40.0),
            # g3: books only on kickoff day (ignored), so ESPN's line is the one
            lines_row("g3", "odds_api", "2026-10-04", -7.0, 50.0),
            lines_row("g3", "espn", "2026-10-03", -1.0, 41.5),
            # g4: everything snapshotted after kickoff: no line at all
            lines_row("g4", "odds_api", "2026-10-05", -4.0, 47.0),
            lines_row("g4", "espn", "2026-10-04", -4.0, 47.0),
            # g5: nflverse's row has no spread, so the books are used
            lines_row("g5", "nflverse_schedules", "2026-10-04", None, 44.0),
            lines_row("g5", "odds_api", "2026-10-01", -6.0, 48.0),
            # g6: an even number of books: the median is the middle of the two
            lines_row("g6", "odds_api", "2026-10-02", -3.0, 44.0),
            lines_row("g6", "odds_api", "2026-10-02", -4.0, 45.0),
            # a game that isn't in `games` is dropped; g7 has no rows at all
            lines_row("g99", "odds_api", "2026-10-02", -3.0, 44.0),
            # g8: a Thursday-night game (kickoff 00:15 UTC on the 9th = 20:15 ET on the 8th):
            # snapshot dates are Eastern, so the 8th is game day (a row saved that night may be
            # an in-game line: Sol review) and only the 7th counts; a row with no date can't be
            # shown to be pre-kickoff
            lines_row("g8", "odds_api", "2026-10-07", -8.5, 47.5),
            lines_row("g8", "odds_api", "2026-10-08", -3.0, 40.0),
            lines_row("g8", "odds_api", "2026-10-09", -3.0, 40.0),
            lines_row("g8", "odds_api", None, -1.0, 30.0),
        ]
    )
    got = pre_game_lines(lines, games).sort("game_id")
    assert got.columns == ["game_id", "pre_spread", "pre_total", "line_source"]
    rows = {r["game_id"]: r for r in got.iter_rows(named=True)}
    assert set(rows) == {"g1", "g2", "g3", "g5", "g6", "g8"}
    assert (rows["g1"]["pre_spread"], rows["g1"]["pre_total"]) == (-2.5, 44.5)
    assert rows["g1"]["line_source"] == "nflverse_schedules"
    assert (rows["g2"]["pre_spread"], rows["g2"]["pre_total"]) == (-3.0, 45.0)
    assert rows["g2"]["line_source"] == "odds_api"
    assert (rows["g3"]["pre_spread"], rows["g3"]["pre_total"]) == (-1.0, 41.5)
    assert rows["g3"]["line_source"] == "espn"
    assert (rows["g5"]["pre_spread"], rows["g5"]["line_source"]) == (-6.0, "odds_api")
    assert (rows["g6"]["pre_spread"], rows["g6"]["pre_total"]) == (-3.5, 44.5)
    assert (rows["g8"]["pre_spread"], rows["g8"]["pre_total"]) == (-8.5, 47.5)


def test_pre_game_lines_with_no_usable_rows_is_empty():
    games = pl.DataFrame({"game_id": ["g1"], "kickoff_utc": [k("2026-10-04")]})
    lines = pl.DataFrame([lines_row("g1", "odds_api", "2026-10-04", -3.0)])
    got = pre_game_lines(lines, games)
    assert got.height == 0
    assert got.columns == ["game_id", "pre_spread", "pre_total", "line_source"]


# --- Contexts: event id -> our game_id ---------------------------------------------------------
def games_frame() -> pl.DataFrame:
    rows = [
        ("2026_05_TB_DAL", 5, "DAL", "TB", k("2026-10-09", "00:15")),
        ("2026_01_SF_LA", 1, "LA", "SF", k("2026-09-11", "00:35")),
        ("2026_05_NYG_WAS", 5, "WAS", "NYG", k("2026-10-11")),
        ("2026_14_NYG_WAS", 14, "NYG", "WAS", k("2026-12-06", "18:00")),
        ("2026_19_BUF_DEN", 19, "DEN", "BUF", k("2027-01-10", "21:30")),
    ]
    return pl.DataFrame(
        {
            "game_id": [r[0] for r in rows],
            "season": [2026] * len(rows),
            "week": [r[1] for r in rows],
            "game_type": ["REG", "REG", "REG", "REG", "WC"],
            "home_team": [r[2] for r in rows],
            "away_team": [r[3] for r in rows],
            "kickoff_utc": [r[4] for r in rows],
            "roof": ["closed", "dome", "outdoors", "outdoors", None],
            "neutral_site": [False, True, False, False, False],
            "pre_spread": [8.5, 3.5, 3.5, None, -2.0],
            "pre_total": [47.5, 47.5, 43.5, None, 40.5],
            "line_source": ["games", "nflverse_schedules", "odds_api", None, "espn"],
            "ctx_wind": [None, None, 11.0, None, 15.0],
            "ctx_temp": [None, None, 55.0, None, 30.0],
        }
    )


def event_on(base: dict, date: str) -> dict:
    out = copy.deepcopy(base)
    out["date"] = date
    out["competitions"][0]["date"] = date
    return out


def test_game_id_for_uses_the_event_map_first():
    contexts = Contexts(games_frame(), {"401872980": "2026_05_TB_DAL", "777": "2026_01_SF_LA"})
    assert contexts.game_id_for("401872980") == "2026_05_TB_DAL"
    assert contexts.game_id_for(401872980) == "2026_05_TB_DAL"  # type: ignore[arg-type]
    # the map wins even when the event's own teams say something else
    wrong_event = fixture("event_401872980_tb_dal_final.json")
    assert contexts.game_id_for("777", wrong_event) == "2026_01_SF_LA"
    assert contexts.game_id_for("555") is None  # unknown and no event to match


def test_game_id_for_falls_back_to_teams_and_date():
    contexts = Contexts(games_frame(), {})
    event = fixture("event_401872980_tb_dal_final.json")
    assert contexts.game_id_for("401872980", event) == "2026_05_TB_DAL"
    assert contexts.context("401872980", event).game_id == "2026_05_TB_DAL"


def test_game_id_for_maps_espns_wsh_and_lar_codes_to_ours():
    contexts = Contexts(games_frame(), {})
    assert contexts.game_id_for("401872988", week5_event("401872988")) == "2026_05_NYG_WAS"
    sf_lar = fixture("event_401872657_sf_lar_neutral_3rd_and_goal.json")
    assert contexts.game_id_for("401872657", sf_lar) == "2026_01_SF_LA"


def test_game_id_for_picks_the_nearest_of_two_meetings_of_the_same_teams():
    contexts = Contexts(games_frame(), {})
    base = week5_event("401872988")  # NYG at WSH
    assert contexts.game_id_for("1", event_on(base, "2026-10-11T17:00Z")) == "2026_05_NYG_WAS"
    # the December meeting: nflverse has NYG at home, ESPN's event still names the same two teams
    assert contexts.game_id_for("1", event_on(base, "2026-12-06T18:00Z")) == "2026_14_NYG_WAS"


def test_game_id_for_allows_36_hours_between_espns_date_and_ours_and_no_more():
    contexts = Contexts(games_frame(), {})
    base = fixture("event_401872980_tb_dal_final.json")  # kickoff 2026-10-09T00:15Z
    assert contexts.game_id_for("1", event_on(base, "2026-10-10T12:15Z")) == "2026_05_TB_DAL"
    assert contexts.game_id_for("1", event_on(base, "2026-10-10T12:16Z")) is None
    assert contexts.game_id_for("1", event_on(base, "2026-10-07T12:15Z")) == "2026_05_TB_DAL"
    assert contexts.game_id_for("1", event_on(base, "2026-10-07T12:14Z")) is None
    assert contexts.game_id_for("1", event_on(base, "2026-12-25T00:00Z")) is None


def test_game_id_for_ignores_which_team_nflverse_calls_home():
    games = games_frame()
    swapped = games.with_columns(
        pl.when(pl.col("game_id") == "2026_01_SF_LA")
        .then(pl.lit("SF"))
        .otherwise(pl.col("home_team"))
        .alias("home_team"),
        pl.when(pl.col("game_id") == "2026_01_SF_LA")
        .then(pl.lit("LA"))
        .otherwise(pl.col("away_team"))
        .alias("away_team"),
    )  # ESPN: LAR home, SF away; this table: SF home
    sf_lar = fixture("event_401872657_sf_lar_neutral_3rd_and_goal.json")
    assert Contexts(swapped, {}).game_id_for("1", sf_lar) == "2026_01_SF_LA"


def test_game_id_for_returns_none_when_nothing_matches():
    contexts = Contexts(games_frame(), {})
    assert contexts.game_id_for("1", week5_event("401872981")) is None  # PHI-JAX isn't here
    assert contexts.game_id_for("1", {"id": "1", "competitions": [{"competitors": []}]}) is None
    assert contexts.game_id_for("1", {}) is None


def test_context_carries_the_games_pre_game_facts():
    contexts = Contexts(games_frame(), {"1": "2026_05_NYG_WAS", "2": "2026_19_BUF_DEN"})
    got = contexts.context("1")
    assert got == GameContext(
        season=2026, home="WAS", away="NYG", game_id="2026_05_NYG_WAS", week=5,
        home_spread=3.5, total=43.5, line_source="odds_api", roof="outdoors",
        neutral_site=False, playoffs=False, kickoff_utc=k("2026-10-11"), wind=11.0, temp=55.0,
    )  # fmt: skip
    playoff = contexts.context("2")
    assert playoff.playoffs is True  # any game type but REG
    assert playoff.roof is None
    sf = Contexts(games_frame(), {"3": "2026_01_SF_LA"}).context("3")
    assert sf.neutral_site is True
    assert contexts.context("404") is None


# --- load_contexts: the curated files, read only -----------------------------------------------
def write_curated(root, *, lines=True, weather=True, scoreboard=True):
    cur = root / "curated"
    cur.mkdir(parents=True)

    def game(gid, season, week, home, away, kick, roof, spread, total, temp, wind, stadium):
        return {
            "game_id": gid, "season": season, "game_type": "REG", "week": week,
            "home_team": home, "away_team": away, "kickoff_utc": kick, "roof": roof,
            "neutral_site": False, "spread_line": spread, "total_line": total,
            "temp": temp, "wind": wind, "stadium": stadium,
        }  # fmt: skip

    att, mt = "AT&T Stadium", "M&T Bank Stadium"
    # AT&T Stadium: closed in three of its four 2025 games, so "closed" is its usual roof
    d25 = [("2025-09-07", "closed", None, None), ("2025-09-21", "closed", None, None),
           ("2025-10-05", "closed", None, None), ("2025-10-19", "open", 70, 4)]  # fmt: skip
    rows = [
        game(f"2025_{i:02d}_X_DAL", 2025, i, "DAL", "X", k(day), roof, 3.0, 44.0, t, w, att)
        for i, (day, roof, t, w) in enumerate(d25, start=1)
    ]
    rows += [
        game("2026_05_TB_DAL", 2026, 5, "DAL", "TB", k("2026-10-09", "00:15"), None, 8.5, 47.5,
             None, None, att),
        game("2026_04_TEN_BAL", 2026, 4, "BAL", "TEN", k("2026-10-04"), "outdoors", 11.5, 42.5,
             62, 6, mt),
    ]  # fmt: skip
    pl.DataFrame(rows).write_parquet(cur / "games.parquet")
    if lines:
        pl.DataFrame(
            [
                lines_row("2026_04_TEN_BAL", "odds_api", "2026-10-02", 12.0, 43.0),
                lines_row("2026_04_TEN_BAL", "odds_api", "2026-10-03", 12.5, 43.0),
                lines_row("2026_04_TEN_BAL", "odds_api", "2026-10-04", 99.0, 99.0),
            ]
        ).write_parquet(cur / "lines.parquet")
    if weather:
        pl.DataFrame(
            {
                "game_id": ["2026_05_TB_DAL", "2026_05_TB_DAL", "2026_04_TEN_BAL"],
                "temp_f": [70.0, 72.0, 90.0],
                "wind_mph": [3.0, 5.0, 20.0],
                "pulled_at": [k("2026-10-07"), k("2026-10-08"), k("2026-10-03")],
            }
        ).write_parquet(cur / "weather_forecasts.parquet")
    if scoreboard:
        pl.DataFrame(
            {
                "espn_event_id": ["401872980", "401872973", "401800000", "401872999"],
                "game_id": ["2026_05_TB_DAL", "2026_04_TEN_BAL", "2025_01_A_DAL", None],
                "season": [2026, 2026, 2025, 2026],
            }
        ).write_parquet(cur / "espn_scoreboard.parquet")
    return DataPaths(root)


def test_load_contexts_joins_lines_roof_weather_and_event_ids(tmp_path):
    contexts = load_contexts(2026, write_curated(tmp_path))
    assert contexts.games.height == 2  # this season only
    assert contexts.event_map == {
        "401872980": "2026_05_TB_DAL",
        "401872973": "2026_04_TEN_BAL",
    }  # not the 2025 game, not the row without a game_id
    tb_dal = contexts.context("401872980")
    # no line rows: nflverse's own schedule line; roof empty until played: the stadium's usual one
    assert (tb_dal.home_spread, tb_dal.total, tb_dal.line_source) == (8.5, 47.5, "games")
    assert tb_dal.roof == "closed"
    assert (tb_dal.wind, tb_dal.temp) == (5.0, 72.0)  # not played: the newest forecast
    assert tb_dal.kickoff_utc == k("2026-10-09", "00:15")
    assert (tb_dal.season, tb_dal.week, tb_dal.home, tb_dal.away) == (2026, 5, "DAL", "TB")
    ten_bal = contexts.context("401872973")
    # books' median before kickoff day (the 99.0 row from kickoff day is ignored)
    assert (ten_bal.home_spread, ten_bal.total, ten_bal.line_source) == (12.25, 43.0, "odds_api")
    assert ten_bal.roof == "outdoors"
    assert (ten_bal.wind, ten_bal.temp) == (6.0, 62.0)  # nflverse's reading beats the forecast


def test_load_contexts_works_with_only_the_games_file(tmp_path):
    contexts = load_contexts(
        2026, write_curated(tmp_path, lines=False, weather=False, scoreboard=False)
    )
    assert contexts.event_map == {}
    got = contexts.context("1", fixture("event_401872980_tb_dal_final.json"))
    assert got.game_id == "2026_05_TB_DAL"
    assert (got.home_spread, got.total, got.line_source) == (8.5, 47.5, "games")
    assert (got.wind, got.temp) == (None, None)
    assert got.roof == "closed"


# --- build_state: the shared builder -------------------------------------------------------------
def game_ctx(**kw) -> GameContext:
    base = dict(
        season=2026, home="DAL", away="TB", game_id="2026_05_TB_DAL", week=5,
        home_spread=8.5, total=47.5, roof="outdoors", wind=8.0, temp=60.0,
    )  # fmt: skip
    return GameContext(**{**base, **kw})


def snap(**kw) -> Snap:
    base = dict(
        offense="TB", offense_is_espn_home=False, period=3, clock=600.0, down=4, distance=3,
        yard_line=40, off_score=10, def_score=17, off_timeouts=2, def_timeouts=3,
    )  # fmt: skip
    return Snap(**{**base, **kw})


def test_build_state_from_the_away_offenses_side():
    state, reason, warnings = build_state(game_ctx(), snap(), opening_receiver="DAL")
    assert (reason, warnings) == (None, [])
    assert state == GameState(
        season=2026, score_diff=-7, game_seconds=1500.0, half_seconds=1500.0, down=4, ydstogo=3,
        yardline_100=40, off_timeouts=2, def_timeouts=3, home=-1, receive_2h_ko=0,
        spread=-8.5, total=47.5, roof="outdoors", ot=False, playoffs=False, wind=8.0, temp=60.0,
    )  # fmt: skip


def test_build_state_from_the_home_offenses_side():
    s = snap(offense="DAL", offense_is_espn_home=True, yard_line=40, off_score=17, def_score=10)
    state, reason, warnings = build_state(game_ctx(), s, opening_receiver="DAL")
    assert (reason, warnings) == (None, [])
    assert state.yardline_100 == 60  # the home goal line is the far end for DAL's yard line 40
    assert (state.score_diff, state.home, state.spread) == (7, 1, 8.5)


def test_the_spread_is_the_offenses_whichever_side_is_nominally_home():
    away = build_state(game_ctx(), snap())[0]
    home = build_state(game_ctx(), snap(offense="DAL", offense_is_espn_home=True))[0]
    assert (away.spread, home.spread) == (-8.5, 8.5)  # DAL favoured by 8.5; TB the underdog
    flipped = game_ctx(home="TB", away="DAL", home_spread=-8.5)  # same game, other nominal home
    tb = build_state(flipped, snap())[0]
    assert tb.spread == -8.5
    assert tb.home == 1  # (TB would be the nominal home team here)


def test_a_neutral_site_has_no_home_team():
    ctx_n = game_ctx(neutral_site=True)
    assert build_state(ctx_n, snap())[0].home == 0
    assert build_state(ctx_n, snap(offense="DAL", offense_is_espn_home=True))[0].home == 0


@pytest.mark.parametrize("down", [-1, 0, 5, None])
def test_build_state_needs_a_real_down(down):
    state, reason, _ = build_state(game_ctx(), snap(down=down))
    assert state is None
    assert reason == f"no down to check (ESPN shows down {down})"


@pytest.mark.parametrize(
    ("yard_line", "espn_home"),
    [(0, False), (100, True), (100, False), (0, True), (-5, False), (130, False)],
)
def test_build_state_refuses_a_yard_line_that_is_not_on_the_field(yard_line, espn_home):
    offense = "DAL" if espn_home else "TB"
    s = snap(offense=offense, offense_is_espn_home=espn_home, yard_line=yard_line)
    state, reason, _ = build_state(game_ctx(), s)
    assert state is None
    assert reason == f"ESPN's yard line {yard_line} isn't a spot on the field"


@pytest.mark.parametrize(
    ("yard_line", "espn_home", "to_goal"),
    [(1, False, 1), (99, False, 99), (1, True, 99), (99, True, 1)],
)
def test_the_goal_line_and_the_far_end_are_the_edges_of_the_field(yard_line, espn_home, to_goal):
    offense = "DAL" if espn_home else "TB"
    s = snap(offense=offense, offense_is_espn_home=espn_home, yard_line=yard_line, distance=1)
    state, reason, _ = build_state(game_ctx(), s)
    assert reason is None
    assert state.yardline_100 == to_goal


def test_a_distance_of_zero_is_one_inch_with_a_warning():
    for bad in (0, -3):
        state, reason, warnings = build_state(game_ctx(), snap(distance=bad))
        assert reason is None
        assert state.ydstogo == 1
        assert warnings == [f"ESPN's distance was {bad}: treated as 1 (inches)"]


def test_a_distance_beyond_the_goal_line_becomes_goal_to_go_with_a_warning():
    s = snap(down=3, distance=10, yard_line=4)  # TB at the DAL 4: 4 yards to go
    state, reason, warnings = build_state(game_ctx(), s)
    assert reason is None
    assert (state.ydstogo, state.yardline_100) == (4, 4)
    assert warnings == ["distance 10 beyond the goal line: goal to go (4)"]
    exact, _, none = build_state(game_ctx(), snap(down=3, distance=4, yard_line=4))
    assert (exact.ydstogo, none) == (4, [])  # 3rd & Goal at the 4 is normal


def test_build_state_refuses_an_offense_that_isnt_in_the_game():
    state, reason, _ = build_state(game_ctx(), snap(offense="KC"))
    assert state is None
    assert reason == "ESPN's offense KC isn't in TB at DAL"


def test_a_missing_line_or_total_is_assumed_and_said():
    state, _, warnings = build_state(game_ctx(home_spread=None), snap())
    assert (state.spread, state.total) == (0.0, 47.5)
    assert warnings == ["no pre-game line: spread 0 assumed"]
    state, _, warnings = build_state(game_ctx(total=None), snap())
    assert (state.spread, state.total) == (-8.5, 44.0)
    assert warnings == ["no pre-game total: 44 assumed"]
    state, _, warnings = build_state(game_ctx(home_spread=None, total=None), snap())
    assert (state.spread, state.total) == (0.0, 44.0)
    assert len(warnings) == 2


def test_missing_timeouts_default_to_three_and_say_so():
    state, _, warnings = build_state(game_ctx(), snap(off_timeouts=1, def_timeouts=None))
    assert (state.off_timeouts, state.def_timeouts) == (1, 3)  # the known one is kept
    assert warnings == ["timeouts unknown: 3 each assumed"]
    state, _, warnings = build_state(game_ctx(), snap(off_timeouts=None, def_timeouts=None))
    assert (state.off_timeouts, state.def_timeouts) == (3, 3)
    assert warnings == ["timeouts unknown: 3 each assumed"]


def test_missing_timeouts_in_overtime_follow_the_overtime_rules():
    """Sol review: each team starts a regular-season overtime with 2 timeouts (3 in the
    playoffs), so an unknown count in OT is 2, and a count above 2 can't be right."""
    ot = snap(period=5, clock=420.0, off_timeouts=None, def_timeouts=None)
    state, _, warnings = build_state(game_ctx(), ot)
    assert state.ot and (state.off_timeouts, state.def_timeouts) == (2, 2)
    assert warnings == ["timeouts unknown: 2 each assumed"]
    state, _, _ = build_state(game_ctx(), snap(period=5, clock=420.0, off_timeouts=3))
    assert state.off_timeouts == 2
    state, _, warnings = build_state(game_ctx(playoffs=True), ot)
    assert (state.off_timeouts, state.def_timeouts) == (3, 3)
    assert warnings == ["timeouts unknown: 3 each assumed"]


def test_timeouts_are_kept_between_zero_and_three():
    state, _, warnings = build_state(game_ctx(), snap(off_timeouts=-1, def_timeouts=5))
    assert (state.off_timeouts, state.def_timeouts) == (0, 3)
    assert warnings == []


@pytest.mark.parametrize(
    ("period", "clock", "game", "half"),
    [(1, 900, 3600, 1800), (2, 0, 1800, 0), (3, 900, 1800, 1800), (4, 5, 5, 5), (4, 0, 0, 0)],
)
def test_build_state_clocks_at_the_edges_of_each_period(period, clock, game, half):
    state, reason, _ = build_state(
        game_ctx(), snap(period=period, clock=float(clock)), opening_receiver="DAL"
    )
    assert reason is None
    assert (state.game_seconds, state.half_seconds, state.ot) == (float(game), float(half), False)


def test_overtime_uses_the_overtime_clock_for_both_and_gets_no_second_half_kick():
    state, reason, warnings = build_state(game_ctx(), snap(period=5, clock=593.0))
    assert reason is None
    assert (state.game_seconds, state.half_seconds, state.ot) == (593.0, 593.0, True)
    assert state.receive_2h_ko == 0
    assert warnings == []  # no "opening kickoff unknown" in overtime


def test_overtime_lengths_regular_season_10_minutes_playoffs_15():
    state, reason, _ = build_state(game_ctx(), snap(period=5, clock=601.0))
    assert state is None
    assert reason.startswith("ESPN's state didn't pass the checks: ")
    playoff = game_ctx(playoffs=True)
    state, reason, _ = build_state(playoff, snap(period=5, clock=900.0))
    assert reason is None
    assert (state.ot, state.playoffs, state.game_seconds) == (True, True, 900.0)
    state, reason, _ = build_state(playoff, snap(period=6, clock=300.0))  # a second OT period
    assert (reason, state.ot, state.game_seconds) == (None, True, 300.0)


def test_a_clock_beyond_the_period_is_a_reason_not_a_crash():
    state, reason, _ = build_state(
        game_ctx(), snap(clock=901.0, period=1), opening_receiver="DAL"
    )  # more than 3600 seconds left in the game
    assert state is None
    assert reason.startswith("ESPN's state didn't pass the checks: ")
    state, reason, _ = build_state(game_ctx(), snap(off_score=95, def_score=0))
    assert state is None
    assert "score_diff" in reason


def test_who_gets_the_second_half_kickoff():
    first_half = snap(period=2, clock=300.0)
    state, _, warnings = build_state(game_ctx(), first_half, opening_receiver="DAL")
    assert (state.receive_2h_ko, warnings) == (1, [])  # DAL received first: TB gets the 2nd half
    state, _, warnings = build_state(game_ctx(), first_half, opening_receiver="TB")
    assert (state.receive_2h_ko, warnings) == (0, [])
    state, _, warnings = build_state(game_ctx(), first_half)  # not known
    assert state.receive_2h_ko == 1
    assert warnings == ["opening kickoff unknown: the offense assumed to get the 2nd-half kick"]
    second_half = snap(period=3, clock=300.0)
    state, _, warnings = build_state(game_ctx(), second_half, opening_receiver="DAL")
    assert (state.receive_2h_ko, warnings) == (0, [])  # only meaningful in the first half
    state, _, warnings = build_state(game_ctx(), second_half)
    assert (state.receive_2h_ko, warnings) == (0, [])


@pytest.mark.parametrize(
    ("roof", "hint", "indoor"),
    [
        ("outdoors", None, False),
        ("open", None, False),
        ("dome", None, True),
        ("closed", None, True),
        ("CLOSED", None, True),
        (None, True, True),  # no roof in nflverse yet: ESPN's venue says indoor
        (None, False, False),
        (None, None, False),
        ("outdoors", True, False),  # a known roof beats the hint
    ],
)
def test_roof_decides_whether_weather_counts(roof, hint, indoor):
    state, _, _ = build_state(game_ctx(roof=roof), snap(), indoor_hint=hint)
    if indoor:
        assert (state.wind, state.temp) == (None, None)
        assert state.roof in ("dome", "closed", "CLOSED")
    else:
        assert (state.wind, state.temp) == (8.0, 60.0)


def test_a_roofless_game_with_an_indoor_hint_is_closed():
    state, _, _ = build_state(game_ctx(roof=None), snap(), indoor_hint=True)
    assert state.roof == "closed"
    assert build_state(game_ctx(roof=None), snap(), indoor_hint=None)[0].roof == "outdoors"


# --- parse_event on the saved scoreboard answers -------------------------------------------------
def test_4th_and_10_on_its_own_14_in_the_third_quarter():
    # TEN (ESPN's away team) has the ball at its own 14: 86 yards from BAL's goal line.
    live = parse_event(
        fixture("event_401872973_ten_bal_4th_and_10.json"), ctx("ten_bal"), fetched_at=FETCHED
    )
    assert (live.event_id, live.game_id, live.status) == ("401872973", "2026_04_TEN_BAL", "in")
    assert (live.period, live.clock, live.display_clock) == (3, 130.0, "2:10")
    assert live.detail == "2:10 - 3rd Quarter"
    assert (live.home, live.away, live.home_score, live.away_score) == ("BAL", "TEN", 24, 10)
    assert (live.offense, live.defense, live.down, live.distance) == ("TEN", "BAL", 4, 10)
    assert live.yardline_100 == 86
    assert (live.home_timeouts, live.away_timeouts) == (3, 3)
    assert live.situation_text == "4th & 10 at TEN 14"
    assert live.last_play_id == "4018729733044"
    assert live.last_play_type == "Rush"
    assert live.last_play_text.startswith("(Shotgun) C.Ward scrambles right end to TEN 15")
    assert live.espn_home_wp == 0.9798  # BAL's win probability, ESPN's home side
    assert (live.source, live.reason, live.warnings) == ("situation", None, [])
    assert live.fetched_at == FETCHED
    assert live.last_play_at is None  # no summary was given
    assert live.is_decision is True
    assert live.state == GameState(
        season=2026, score_diff=-14, game_seconds=1030.0, half_seconds=1030.0, down=4,
        ydstogo=10, yardline_100=86, off_timeouts=3, def_timeouts=3, home=-1, receive_2h_ko=0,
        spread=-11.5, total=42.5, roof="outdoors", ot=False, playoffs=False, wind=6.0, temp=62.0,
    )  # fmt: skip


def test_4th_and_goal_for_the_home_team_counts_from_the_other_end():
    # PHI (ESPN's home team) at the LA 2: yardLine 98 from PHI's own goal line = 2 to go.
    live = parse_event(fixture("event_401872970_lar_phi_4th_and_goal.json"), ctx("la_phi"))
    assert (live.home, live.away) == ("PHI", "LA")  # ESPN's LAR is our LA
    assert (live.home_score, live.away_score) == (20, 10)
    assert (live.offense, live.defense, live.down, live.distance) == ("PHI", "LA", 4, 2)
    assert live.yardline_100 == 2
    assert live.situation_text == "4th & Goal at LAR 2"
    assert (live.home_timeouts, live.away_timeouts) == (2, 3)
    assert live.warnings == []
    assert live.state == GameState(
        season=2026, score_diff=10, game_seconds=451.0, half_seconds=451.0, down=4, ydstogo=2,
        yardline_100=2, off_timeouts=2, def_timeouts=3, home=1, receive_2h_ko=0,
        spread=-3.5, total=42.5, roof="outdoors", wind=9.0, temp=62.0,
    )  # fmt: skip
    assert live.is_decision


def test_3rd_and_goal_at_a_neutral_site_where_espns_away_team_has_the_ball():
    # Melbourne: SF (away) at the LAR 2, 3rd & Goal; no home field; roof "dome" ignores weather.
    event = fixture("event_401872657_sf_lar_neutral_3rd_and_goal.json")
    live = parse_event(event, ctx("sf_la"), opening="SF")
    assert (live.home, live.away, live.offense, live.defense) == ("LA", "SF", "SF", "LA")
    assert (live.down, live.distance, live.yardline_100) == (3, 2, 2)
    assert live.warnings == []
    assert live.state == GameState(
        season=2026, score_diff=0, game_seconds=3124.0, half_seconds=1324.0, down=3, ydstogo=2,
        yardline_100=2, off_timeouts=3, def_timeouts=3, home=0, receive_2h_ko=0,
        spread=-3.5, total=47.5, roof="dome", wind=None, temp=None,
    )  # fmt: skip
    # the kickoff wasn't known: the offense is assumed to get the second-half kick, and it says so
    unknown = parse_event(event, ctx("sf_la"))
    assert unknown.state.receive_2h_ko == 1
    assert unknown.warnings == [
        "opening kickoff unknown: the offense assumed to get the 2nd-half kick"
    ]
    assert parse_event(event, ctx("sf_la"), opening="LA").state.receive_2h_ko == 1


def test_3rd_and_goal_with_the_nominal_home_team_on_offense_at_a_neutral_site():
    # BAL vs DAL (neutral): DAL is ESPN's home team and has the ball at the BAL 5 (yardLine 95).
    event = fixture("event_401872960_bal_dal_neutral_3rd_and_goal.json")
    live = parse_event(event, ctx("bal_dal"))
    assert (live.offense, live.defense, live.down, live.distance) == ("DAL", "BAL", 3, 5)
    assert live.yardline_100 == 5
    assert (live.home_score, live.away_score) == (28, 31)
    assert (live.home_timeouts, live.away_timeouts) == (0, 1)  # DAL used all three
    assert live.last_play_type == "Timeout"
    assert live.warnings == []
    assert live.state == GameState(
        season=2026, score_diff=-3, game_seconds=14.0, half_seconds=14.0, down=3, ydstogo=5,
        yardline_100=5, off_timeouts=0, def_timeouts=1, home=0, receive_2h_ko=0,
        spread=-3.0, total=53.5, roof="open", wind=7.0, temp=77.0,
    )  # fmt: skip


def test_espn_and_nflverse_disagreeing_about_the_home_team_is_handled_and_said():
    # Same snap, but nflverse lists BAL as the home team: the yard line still follows ESPN's
    # (DAL home), the score, timeouts and win probability are re-sided, and a warning is added.
    event = fixture("event_401872960_bal_dal_neutral_3rd_and_goal.json")
    swapped = ctx("bal_dal", home="BAL", away="DAL", home_spread=3.0)  # BAL favoured by 3
    live = parse_event(event, swapped)
    assert (live.home, live.away, live.home_score, live.away_score) == ("BAL", "DAL", 31, 28)
    assert (live.home_timeouts, live.away_timeouts) == (1, 0)
    assert live.espn_home_wp == pytest.approx(1 - 0.5481)  # DAL's 54.8% is BAL's 45.2%
    assert live.warnings == ["ESPN lists DAL at home, nflverse BAL: yard lines use ESPN's"]
    straight = parse_event(event, ctx("bal_dal"))
    assert live.yardline_100 == straight.yardline_100 == 5  # unchanged by who's "home"
    assert live.state == straight.state  # a neutral game: the offense's view is identical
    assert live.state.spread == -3.0  # DAL is the 3-point underdog either way
    assert straight.espn_home_wp == pytest.approx(0.5481)


def test_3rd_and_1_on_its_own_37_with_a_timeout_already_used():
    # TB at its own 37 (away team: 63 to the CIN goal line) right after "Timeout #1 by TB".
    event = fixture("event_401872925_tb_cin_3rd_and_1.json")
    live = parse_event(event, ctx("tb_cin"), opening="CIN")
    assert (live.offense, live.down, live.distance, live.yardline_100) == ("TB", 3, 1, 63)
    assert (live.home_timeouts, live.away_timeouts) == (3, 2)
    assert live.last_play_type == "Timeout"
    assert live.last_play_text == "Timeout #1 by TB at 14:23."
    assert live.state == GameState(
        season=2026, score_diff=0, game_seconds=3563.0, half_seconds=1763.0, down=3, ydstogo=1,
        yardline_100=63, off_timeouts=2, def_timeouts=3, home=-1, receive_2h_ko=1,
        spread=-3.5, total=50.5, roof="outdoors", wind=7.0, temp=85.0,
    )  # fmt: skip
    assert live.is_decision


def test_the_opening_receiver_comes_from_the_summary_when_not_given():
    # the synthetic TV timeout below; TB@DAL's summary says DAL received: TB gets the 2nd half
    event = tv_timeout_event()
    summary = summary_upto(fixture("summary_401872980_tb_dal.json"), "4018729801092")
    live = parse_event(event, ctx("tb_dal"), summary=summary)
    assert live.state.receive_2h_ko == 1
    assert "opening kickoff unknown" not in " ".join(live.warnings)
    explicit = parse_event(event, ctx("tb_dal"), summary=summary, opening="TB")
    assert explicit.state.receive_2h_ko == 0  # an explicit opening wins


def tv_timeout_event() -> dict:
    """TB@DAL at the "Official Timeout" after DAL's punt (12:26, 2nd quarter, 7-7): the game's
    real scoreboard entry with the status and situation ESPN shows at a TV timeout (copied from
    the real ARI@NYG one: down -1, distance 0, no possession, a junk yard line)."""
    event = fixture("event_401872980_tb_dal_final.json")
    status = {
        "clock": 746.0, "displayClock": "12:26", "period": 2,
        "type": {
            "id": "2", "name": "STATUS_IN_PROGRESS", "state": "in", "completed": False,
            "description": "In Progress", "detail": "12:26 - 2nd Quarter",
            "shortDetail": "12:26 - 2nd",
        },
    }  # fmt: skip
    comp = event["competitions"][0]
    event["status"] = comp["status"] = status
    for c in comp["competitors"]:
        c["score"] = "7"
        c.pop("winner", None)
    comp["situation"] = {
        "down": -1, "yardLine": 35, "distance": 0, "isRedZone": False,
        "homeTimeouts": 2, "awayTimeouts": 3,  # DAL (home) has used one
        "lastPlay": {
            "id": "4018729801092", "type": {"text": "Official Timeout"},
            "text": "Official Timeout at 12:26.",
        },
    }  # fmt: skip
    return event


def test_a_tv_timeout_with_the_summary_gives_the_next_snap_from_the_last_real_play():
    # ESPN shows no down; the summary's last real play is DAL's punt, which ended 1st & 10 for
    # TB at TB's own 3 (yardLine 97, TB the away team: 97 yards to go).
    summary = summary_upto(fixture("summary_401872980_tb_dal.json"), "4018729801092")
    live = parse_event(tv_timeout_event(), ctx("tb_dal"), summary=summary, fetched_at=FETCHED)
    assert live.source == "summary"
    assert (live.offense, live.defense, live.down, live.distance) == ("TB", "DAL", 1, 10)
    assert live.yardline_100 == 97
    assert live.last_play_at == datetime(2026, 10, 9, 0, 54, 5, tzinfo=UTC)
    assert live.reason is None
    assert live.is_decision is False  # 1st down
    assert live.state == GameState(
        season=2026, score_diff=0, game_seconds=2546.0, half_seconds=746.0, down=1, ydstogo=10,
        yardline_100=97, off_timeouts=3, def_timeouts=2, home=-1, receive_2h_ko=1,
        spread=-8.5, total=47.5, roof="closed", wind=None, temp=None,
    )  # fmt: skip


def test_a_summary_older_than_the_scoreboard_is_not_used_for_the_next_snap():
    """Sol review: the scoreboard and the summary are separate requests. A summary that stops
    before the scoreboard's last play (here at DAL's punt, before the TV timeout) must not
    invent a state from an earlier play."""
    summary = summary_upto(fixture("summary_401872980_tb_dal.json"), "4018729801059")
    live = parse_event(tv_timeout_event(), ctx("tb_dal"), summary=summary)
    assert live.state is None and live.down is None
    assert "the summary hasn't caught up with the scoreboard yet" in live.reason


def test_a_summary_newer_than_the_scoreboard_is_cut_at_the_scoreboards_last_play():
    """Plays the summary already has after the scoreboard's `lastPlay` are ignored: the state
    is the one the scoreboard's moment had (1st & 10 for TB at its own 3, not a later snap)."""
    summary = summary_upto(fixture("summary_401872980_tb_dal.json"), "4018729801178")
    live = parse_event(tv_timeout_event(), ctx("tb_dal"), summary=summary)
    assert live.source == "summary"
    assert (live.offense, live.down, live.distance, live.yardline_100) == ("TB", 1, 10, 97)


def test_a_tv_timeout_without_the_summary_has_no_state_and_says_why():
    live = parse_event(tv_timeout_event(), ctx("tb_dal"))
    assert live.state is None
    assert live.reason == NO_SNAP
    assert (live.offense, live.down, live.yardline_100) == (None, None, None)
    assert live.source == "situation"
    assert live.last_play_type == "Official Timeout"
    assert live.is_decision is False


@pytest.mark.parametrize(
    ("name", "event_file", "ctx_name", "score"),
    [
        ("ARI@NYG", "event_401872966_ari_nyg_timeout_after_td.json", "ari_nyg", (27, 24)),
        ("ATL@GB", "event_401872948_atl_gb_timeout_after_td.json", "atl_gb", (7, 24)),
    ],
)
def test_a_timeout_after_a_touchdown_has_no_pending_snap_with_or_without_a_summary(
    name, event_file, ctx_name, score
):
    # down -1, distance 0, a junk yard line, no possession: a kickoff comes next (the try is
    # part of the touchdown row in ESPN's data)
    event = fixture(event_file)
    sit = event["competitions"][0]["situation"]
    assert sit["down"] == -1 and sit["distance"] == 0 and "possession" not in sit
    live = parse_event(event, ctx(ctx_name))
    assert (live.home_score, live.away_score) == score
    assert (live.state, live.reason, live.offense, live.down) == (None, NO_SNAP, None, None)
    assert live.last_play_type == "Official Timeout"
    assert live.status == "in"
    assert not live.is_decision


def test_a_timeout_after_a_touchdown_stays_that_way_with_a_summary_cut_at_that_moment():
    event = fixture("event_401872966_ari_nyg_timeout_after_td.json")
    summary = fixture("summary_401872966_ari_nyg_cut_at_timeout.json")
    live = parse_event(event, ctx("ari_nyg"), summary=summary, fetched_at=FETCHED)
    # the last real play (the touchdown) ended with no down: still no snap pending
    assert (live.state, live.reason, live.source) == (None, NO_SNAP, "situation")
    assert live.last_play_at == datetime(2026, 10, 4, 19, 16, 26, tzinfo=UTC)


def test_halftime_says_the_second_half_starts_with_a_kickoff():
    live = parse_event(fixture("event_401872930_dal_nyg_halftime.json"), ctx("dal_nyg"))
    assert live.state is None
    assert live.reason == "halftime: the second half starts with a kickoff"
    assert live.status == "in"  # ESPN still calls it in progress
    assert live.detail == "Halftime"
    assert (live.period, live.clock) == (2, 0.0)
    assert (live.home_score, live.away_score) == (14, 7)
    assert live.last_play_type == "End of Half"
    assert live.situation_text is None
    assert live.offense is None  # the situation block left no possession (and a stale 1st & 10)
    assert not live.is_decision


def test_the_end_of_regulation_and_of_the_game_are_named():
    event = fixture("event_401872930_dal_nyg_halftime.json")
    for last, reason in [
        ("End of Regulation", "end of regulation: overtime starts with a kickoff"),
        ("End of Game", "the game is over"),
    ]:
        edited = copy.deepcopy(event)
        edited["competitions"][0]["situation"]["lastPlay"]["type"]["text"] = last
        assert parse_event(edited, ctx("dal_nyg")).reason == reason


def test_a_finished_game_has_no_situation_and_is_over():
    live = parse_event(fixture("event_401872980_tb_dal_final.json"), ctx("tb_dal"))
    assert (live.status, live.reason) == ("post", "the game is over")
    assert live.state is None
    assert (live.home_score, live.away_score) == (16, 24)
    assert (live.period, live.display_clock, live.detail) == (4, "0:00", "Final")
    assert (live.home_timeouts, live.away_timeouts) == (None, None)


def test_a_game_that_hasnt_started_says_so_and_maps_wsh_and_lar():
    live = parse_event(week5_event("401872981"), ctx("phi_jax"))
    assert (live.status, live.reason, live.state) == ("pre", "the game hasn't started", None)
    assert live.period == 0
    assert live.detail == "Sun, October 11th at 9:30 AM EDT"
    assert (live.home, live.away, live.home_score, live.away_score) == ("JAX", "PHI", 0, 0)
    nyg_wsh = parse_event(
        week5_event("401872988"),
        GameContext(season=2026, home="WAS", away="NYG", game_id="2026_05_NYG_WAS"),
    )
    assert (nyg_wsh.home, nyg_wsh.status, nyg_wsh.warnings) == ("WAS", "pre", [])  # no mismatch
    buf_lar = parse_event(week5_event("401872994"), GameContext(season=2026, home="LA", away="BUF"))
    assert buf_lar.warnings == []  # LAR is LA


def test_an_event_for_other_teams_than_the_context_gives_no_state():
    live = parse_event(
        fixture("event_401872973_ten_bal_4th_and_10.json"),
        GameContext(season=2026, home="KC", away="DEN", home_spread=-3.0, total=45.0),
    )
    assert live.state is None
    assert live.reason == "ESPN's offense TEN isn't in DEN at KC"


def test_yardstonendzone_is_never_read():
    # ESPN's own yardsToEndzone is wrong about one snap in ten (0 on timeouts, mirrored on home
    # punts): the parser must derive yards to goal from yardLine and possession alone.
    event = fixture("event_401872973_ten_bal_4th_and_10.json")
    event["competitions"][0]["situation"]["yardsToEndzone"] = 14  # the mirrored value
    assert parse_event(event, ctx("ten_bal")).yardline_100 == 86
    event["competitions"][0]["situation"]["yardsToEndzone"] = 0
    assert parse_event(event, ctx("ten_bal")).state.yardline_100 == 86


def test_a_distance_beyond_the_goal_line_in_the_feed_is_clamped_with_a_warning():
    event = fixture("event_401872970_lar_phi_4th_and_goal.json")
    event["competitions"][0]["situation"]["distance"] = 10  # 2 yards from the goal line
    live = parse_event(event, ctx("la_phi"))
    assert live.state.ydstogo == 2
    assert live.warnings == ["distance 10 beyond the goal line: goal to go (2)"]


def test_a_missing_line_is_assumed_and_said():
    live = parse_event(
        fixture("event_401872973_ten_bal_4th_and_10.json"),
        ctx("ten_bal", home_spread=None, total=None),
    )
    assert (live.state.spread, live.state.total) == (0.0, 44.0)
    assert live.warnings == ["no pre-game line: spread 0 assumed", "no pre-game total: 44 assumed"]


def test_missing_timeouts_in_the_feed_default_to_three_and_say_so():
    event = fixture("event_401872973_ten_bal_4th_and_10.json")
    sit = event["competitions"][0]["situation"]
    del sit["homeTimeouts"]
    sit["awayTimeouts"] = 1
    live = parse_event(event, ctx("ten_bal"))
    assert (live.home_timeouts, live.away_timeouts) == (None, 1)
    assert (live.state.off_timeouts, live.state.def_timeouts) == (1, 3)  # TEN away has 1
    assert live.warnings == ["timeouts unknown: 3 each assumed"]


def test_a_possession_for_a_team_not_in_the_game_is_no_pending_snap():
    event = fixture("event_401872973_ten_bal_4th_and_10.json")
    event["competitions"][0]["situation"]["possession"] = "999"
    live = parse_event(event, ctx("ten_bal"))
    assert (live.state, live.reason) == (None, NO_SNAP)


def test_the_clock_comes_from_the_display_clock_when_the_number_is_missing():
    event = fixture("event_401872970_lar_phi_4th_and_goal.json")
    for holder in (event["competitions"][0]["status"], event["status"]):
        holder.pop("clock")
    live = parse_event(event, ctx("la_phi"))
    assert (live.clock, live.display_clock) == (451.0, "7:31")
    assert live.state.game_seconds == 451.0
    for holder in (event["competitions"][0]["status"], event["status"]):
        holder.pop("displayClock")
    assert parse_event(event, ctx("la_phi")).clock == 0.0


def test_the_status_may_sit_on_the_event_instead_of_the_competition():
    event = fixture("event_401872973_ten_bal_4th_and_10.json")
    del event["competitions"][0]["status"]
    live = parse_event(event, ctx("ten_bal"))
    assert (live.status, live.period, live.clock) == ("in", 3, 130.0)
    assert live.state.game_seconds == 1030.0


def test_espns_text_comes_back_as_inert_plain_text():
    event = fixture("event_401872973_ten_bal_4th_and_10.json")
    last = event["competitions"][0]["situation"]["lastPlay"]
    last["text"] = "<script>alert(1)</script>\x00 bad\x07 text\n\n  here"
    live = parse_event(event, ctx("ten_bal"))
    assert live.last_play_text == "<script>alert(1)</script> bad text here"
    assert "\x00" not in live.last_play_text


def test_live_state_serialises_to_json_with_isoformat_times():
    summary = summary_upto(fixture("summary_401872980_tb_dal.json"), "4018729801092")
    live = parse_event(tv_timeout_event(), ctx("tb_dal"), summary=summary, fetched_at=FETCHED)
    back = json.loads(json.dumps(live.as_dict()))
    assert back["fetched_at"] == "2026-10-10T15:30:00+00:00"
    assert back["last_play_at"] == "2026-10-09T00:54:05+00:00"
    assert back["state"]["yardline_100"] == 97
    assert back["source"] == "summary"
    assert GameState.from_dict(back["state"]) == live.state
    over = parse_event(fixture("event_401872980_tb_dal_final.json"), ctx("tb_dal"))
    assert json.loads(json.dumps(over.as_dict()))["state"] is None
    assert isinstance(over, LiveState)
