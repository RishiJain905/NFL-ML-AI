"""LD01 parity check and the replay rules it found: no network, no D: data (synthetic ESPN
summaries, small Polars frames, a temporary data root)."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import polars as pl
import pytest

from nflengine.live import parity as P
from nflengine.live.espn import EspnClient
from nflengine.live.replay import ReplayPlay, replay, scoring_start_clock, team_names
from nflengine.live.schema import GameState
from nflengine.live.state import GameContext
from nflengine.paths import DataPaths

EV = "401999001"
KC = {"id": "12", "abbreviation": "KC", "displayName": "Kansas City Chiefs",
      "location": "Kansas City", "name": "Chiefs"}  # fmt: skip
IND = {"id": "11", "abbreviation": "IND", "displayName": "Indianapolis Colts",
       "location": "Indianapolis", "name": "Colts"}  # fmt: skip
T0 = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)


# --- synthetic ESPN summaries -----------------------------------------------------------------
def play(n, ttype, text="", *, q=1, clock="10:00", down=None, dist=None, yl=None, team=None,
         home=0, away=0, scoring=False, wall=None):  # fmt: skip
    p = {
        "id": f"{EV}{n}",
        "sequenceNumber": str(n * 100),
        "type": {"text": ttype},
        "text": text,
        "period": {"number": q},
        "clock": {"displayValue": clock},
        "homeScore": home,
        "awayScore": away,
        "scoringPlay": scoring,
        "wallclock": (T0 + timedelta(seconds=wall)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if wall is not None
        else None,
        "start": {},
    }
    if down is not None:
        p["start"] = {"down": down, "distance": dist, "yardLine": yl, "team": {"id": team}}
    return p


def summary(plays, home=KC, away=IND):
    return {
        "header": {
            "id": EV,
            "season": {"year": 2026},
            "competitions": [
                {
                    "competitors": [
                        {"id": home["id"], "homeAway": "home", "team": home, "score": "0"},
                        {"id": away["id"], "homeAway": "away", "team": away, "score": "0"},
                    ],
                    "status": {"type": {"state": "post"}},
                }
            ],
        },
        "gameInfo": {"venue": {"indoor": False}},
        "drives": {"previous": [{"plays": plays}]},
    }


KICKOFF = play(1, "Kickoff", "kicks", clock="15:00", down=0, team="12")  # KC kicks, IND receives
CTX = GameContext(season=2026, home="KC", away="IND", game_id="2026_02_IND_KC", week=2,
                  home_spread=3.0, total=47.0, roof="outdoors")  # fmt: skip


def by_id(rows: list[ReplayPlay]) -> dict[int, ReplayPlay]:
    return {r.nflverse_play_id: r for r in rows}


# --- replay rules found by the parity check ---------------------------------------------------
def test_live_parity_score_moves_only_on_scoring_plays():
    plays = [
        KICKOFF,
        play(2, "Rush", "run", down=1, dist=10, yl=25, team="11", home=0, away=7, scoring=True),
        play(3, "Two-minute warning", "Two-Minute Warning", home=0, away=0),
        play(4, "Rush", "run", down=3, dist=4, yl=30, team="11", home=3, away=7),  # stray score
        play(5, "Pass Incompletion", "incomplete", down=4, dist=4, yl=30, team="11", away=7),
    ]
    rows = by_id(replay(summary(plays), CTX))
    assert rows[4].away_score == 7 and rows[4].home_score == 0  # not 0-0 from the warning
    assert rows[5].home_score == 0 and rows[5].away_score == 7  # the stray 3 is ignored
    assert rows[5].state.score_diff == 7  # IND (away) leads by 7


def test_live_parity_lost_challenge_costs_a_timeout_after_the_play():
    text = "J.Taylor up the middle. Indianapolis challenged the first down ruling, and the play was Upheld."  # noqa: E501
    plays = [
        KICKOFF,
        play(2, "Rush", text, down=3, dist=2, yl=40, team="12"),  # KC offense, IND challenges
        play(3, "Rush", "run", down=1, dist=10, yl=45, team="12"),
        play(4, "Rush", "run", down=3, dist=1, yl=50, team="12"),
    ]
    rows = by_id(replay(summary(plays), CTX))
    assert rows[2].def_timeouts == 3  # charged after the challenged play
    assert rows[4].def_timeouts == 2 and rows[4].off_timeouts == 3


def test_live_parity_challenge_timeout_number_and_reversals():
    up = "pass. New York Jets challenged the ruling, and the play was Upheld. (Timeout #2.)"
    rev = "pass. Indianapolis challenged the ruling, and the play was REVERSED. pass incomplete"
    nyj = {"id": "20", "abbreviation": "NYJ", "displayName": "New York Jets",
           "location": "New York", "name": "Jets"}  # fmt: skip
    ctx = GameContext(season=2026, home="KC", away="NYJ", game_id="x")
    plays = [
        play(1, "Kickoff", "kicks", clock="15:00", down=0, team="12"),
        play(2, "Pass Reception", up, down=3, dist=5, yl=40, team="12"),
        play(3, "Rush", "run", down=3, dist=5, yl=45, team="12"),
    ]
    rows = by_id(replay(summary(plays, away=nyj), ctx))
    assert rows[3].def_timeouts == 1  # "(Timeout #2.)": two used
    rows = by_id(
        replay(
            summary(
                [
                    KICKOFF,
                    play(2, "Pass", rev, down=3, dist=5, yl=40, team="12"),
                    play(3, "Rush", "run", down=3, dist=5, yl=45, team="12"),
                ]
            ),  # fmt: skip
            CTX,
        )
    )
    assert rows[3].def_timeouts == 3  # a reversed challenge costs nothing


def test_live_parity_team_names_drop_shared_city():
    nyg = {"id": "19", "abbreviation": "NYG", "displayName": "New York Giants",
           "location": "New York", "name": "Giants"}  # fmt: skip
    nyj = {"id": "20", "abbreviation": "NYJ", "displayName": "New York Jets",
           "location": "New York", "name": "Jets"}  # fmt: skip
    names = team_names(summary([], home=nyg, away=nyj)["header"])
    assert names["New York Giants"] == "NYG" and names["Jets"] == "NYJ"
    assert "New York" not in names


def test_live_parity_timeout_four_never_below_zero():
    plays = [KICKOFF] + [
        play(10 + i, "Timeout", f"Timeout #{i} by IND at 01:0{i}.", q=2, clock=f"1:0{i}")
        for i in (1, 2, 3, 4)
    ]
    plays.append(play(20, "Rush", "run", q=2, clock="0:50", down=3, dist=3, yl=40, team="12"))
    rows = by_id(replay(summary(plays), CTX))
    assert rows[20].def_timeouts == 0
    assert rows[20].state.def_timeouts == 0


def test_live_parity_scoring_start_clock_rules():
    assert scoring_start_clock(260.0, None, 1, None) == 260.0
    assert scoring_start_clock(260.0, (2, 300.0, T0, False), 1, T0) == 260.0  # other quarter
    # after a timeout the clock waits for the snap
    assert scoring_start_clock(16.0, (4, 21.0, T0, True), 4, T0 + timedelta(minutes=2)) == 21.0
    # after a snap: the clock can't run longer than the real time between the snaps
    later = T0 + timedelta(seconds=25)
    assert scoring_start_clock(260.0, (1, 300.0, T0, False), 1, later) == 275.0
    assert scoring_start_clock(260.0, (1, 300.0, T0, False), 1, T0 + timedelta(minutes=3)) == 260
    assert scoring_start_clock(260.0, (1, 300.0, None, False), 1, later) == 260.0


def test_live_parity_replay_estimates_scoring_snap_clock():
    plays = [
        KICKOFF,
        play(2, "Rush", "run", clock="5:00", down=2, dist=8, yl=30, team="12", wall=0),
        play(
            3,
            "Passing Touchdown",
            "pass for 26 yards, TOUCHDOWN",
            clock="4:29",
            down=3,
            dist=4,
            yl=26,
            team="12",
            home=7,
            scoring=True,
            wall=25,
        ),  # fmt: skip
        play(4, "Timeout", "Timeout #1 by IND at 00:21.", q=4, clock="0:21"),
        play(
            5,
            "Field Goal Good",
            "field goal is GOOD",
            q=4,
            clock="0:16",
            down=4,
            dist=1,
            yl=64,
            team="12",
            home=10,
            scoring=True,
            wall=900,
        ),  # fmt: skip
    ]
    rows = by_id(replay(summary(plays), CTX))
    assert rows[3].clock == 275.0 and rows[3].display_clock == "4:29"
    assert any("score's" in w for w in rows[3].warnings)
    assert rows[5].clock == 21.0
    assert rows[5].state.game_seconds == 21.0


# --- the comparison ---------------------------------------------------------------------------
def rp(n, *, down=3, dist=5, ytg=40, diff=0, q=1, clock=600.0, off=3, de=3, ttype="Rush",
       snap=True, offense="KC"):  # fmt: skip
    """A replayed ESPN play (no GameState: the raw fields are compared)."""
    home = offense == "KC"
    return ReplayPlay(
        play_id=f"{EV}{n}", nflverse_play_id=n, sequence=n, period=q, clock=clock,
        display_clock=None, type=ttype, text=f"espn {n}", wallclock=None, snap=snap,
        offense=offense if snap else None, down=down if snap else None,
        distance=dist if snap else None, yardline_100=ytg if snap else None,
        home_score=max(diff, 0) if home else max(-diff, 0),
        away_score=max(-diff, 0) if home else max(diff, 0),
        off_timeouts=off if snap else None, def_timeouts=de if snap else None,
        offense_home=home if snap else None,
    )  # fmt: skip


def nv(n, *, down=3, dist=5, ytg=40, diff=0, q=1, clock=600.0, off=3, de=3, ptype="run",
       desc="", two=0, team="KC"):  # fmt: skip
    return {
        "game_id": "G", "play_id": n, "week": 2, "qtr": q, "down": down, "ydstogo": dist,
        "yardline_100": ytg, "posteam": team, "play_type": ptype,
        "quarter_seconds_remaining": clock, "score_differential": diff,
        "posteam_timeouts_remaining": off, "defteam_timeouts_remaining": de,
        "two_point_attempt": two, "desc": desc or f"nflverse {n}",
    }  # fmt: skip


def frames(espn_plays, nv_rows):
    return P.espn_rows(espn_plays, "G"), pl.DataFrame(nv_rows)


def test_live_parity_compare_classifies_every_mismatch():
    challenged = "pass short. Indianapolis challenged the ruling, and the play was Upheld."
    espn = [
        rp(10),  # equal
        rp(20, clock=597.0),  # 3 s apart: equal
        rp(30, clock=590.0, ttype="Passing Touchdown"),  # scoring play clock
        rp(40, clock=590.0),  # other clock
        rp(50, dist=6, ytg=41),  # spot
        rp(60, de=3),  # nflverse charged the challenge on the play itself
        rp(70, off=2),  # a timeout one feed doesn't have
        rp(80, diff=3),  # score
        rp(90, down=4),  # down
        rp(100, down=2),  # not a 3rd / 4th down on ESPN: nflverse-only
        rp(110),  # ESPN only: nflverse has it as a 1st down
        rp(120, ttype="End Period", snap=False),  # ESPN typed it as an admin row
        rp(130, ttype="Penalty"),  # a pre-snap penalty counts (no_play)
    ]
    rows = [
        nv(10), nv(20), nv(30), nv(40), nv(50), nv(60, de=2, desc=challenged), nv(70),
        nv(80), nv(90), nv(100), nv(110, down=1, dist=10), nv(120), nv(130, ptype="no_play"),
        nv(140, ptype="pass", two=1),  # a two-point try: not a start state
        nv(150, ptype="kickoff", down=None),
        nv(160, down=4),  # ESPN has no such play
    ]  # fmt: skip
    comp = P.compare_states(*frames(espn, rows))
    cat = dict(zip(comp["play_id"], comp["category"], strict=True))
    assert cat[10] is None and cat[20] is None and cat[130] is None
    assert cat[30] == "clock_scoring_play" and cat[40] == "clock_other"
    assert cat[50] == "spot" and cat[60] == "timeouts_challenge_play" and cat[70] == "timeouts_log"
    assert cat[80] == "score" and cat[90] == "offense_or_down"
    assert cat[100] == "nflverse_only" and cat[110] == "espn_only" and cat[120] == "nflverse_only"
    assert cat[160] == "nflverse_only" and 140 not in cat and 150 not in cat
    notes = dict(zip(comp["play_id"], comp["note"], strict=True))
    assert notes[110] == "nflverse has play 110 as run, down 1 & 10 at 40 yards to go"
    assert notes[120] == "ESPN has play 120 as 'End Period' (not a snap)"
    assert notes[100] == "ESPN has play 100 as 'Rush' (a snap, down 2)"
    assert notes[160] == "ESPN has no play 160"
    f50 = comp.filter(pl.col("play_id") == 50)["fields"].to_list()[0]
    assert f50 == ["distance", "yardline_100"]


def test_live_parity_summarize_counts_and_target():
    espn = [rp(i) for i in range(1, 50)] + [rp(50, off=2), rp(60)]
    rows = [nv(i) for i in range(1, 51)] + [nv(70)]
    rep = P.summarize(P.compare_states(*frames(espn, rows)), examples=1)
    assert rep["states_compared"] == 50 and rep["states_equal"] == 49
    # the gate counts the two one-sided states as not equal: 49 / 52 fails
    assert rep["states_total"] == 52 and rep["share_equal"] == pytest.approx(49 / 52)
    assert rep["share_equal_overlap"] == pytest.approx(0.98) and not rep["passed"]
    assert rep["field_mismatches"]["off_timeouts"] == 1
    assert rep["field_mismatches"]["clock"] == 0
    assert rep["categories"]["timeouts_log"]["count"] == 1
    assert rep["espn_only"] == 1 and rep["nflverse_only"] == 1
    assert len(rep["one_sided"]) == 2
    ex = rep["categories"]["timeouts_log"]["examples"][0]
    assert ex["espn"] == {"off_timeouts": 2} and ex["nflverse"] == {"off_timeouts": 3}


def test_live_parity_a_feed_that_drops_states_fails_the_gate():
    """Sol review: 1 matching state and 99 nflverse states ESPN lost is 1%, not 100%."""
    rows = [nv(i) for i in range(1, 101)]
    rep = P.summarize(P.compare_states(*frames([rp(1)], rows)))
    assert rep["share_equal_overlap"] == 1.0
    assert rep["share_equal"] == pytest.approx(0.01) and not rep["passed"]
    assert rep["nflverse_only"] == 99


def test_live_parity_compare_handles_empty_espn():
    comp = P.compare_states(P.espn_rows([], "G"), pl.DataFrame([nv(1)]))
    rep = P.summarize(comp)
    assert rep["states_compared"] == 0 and rep["share_equal_overlap"] is None
    assert rep["share_equal"] == 0.0 and not rep["passed"]  # the one state ESPN lacks counts
    assert rep["nflverse_only"] == 1


# --- decision parity --------------------------------------------------------------------------
def gs(**kw) -> GameState:
    base = dict(season=2026, score_diff=0, game_seconds=900.0, half_seconds=900.0, down=4,
                ydstogo=2, yardline_100=40)  # fmt: skip
    return GameState(**(base | kw))


def test_live_parity_state_diff_as_the_engine_sees_it():
    assert P.state_diff(gs(roof="dome"), gs(roof="closed")) == []  # both indoor
    assert P.state_diff(gs(), gs(roof="dome")) == ["indoor"]
    assert P.state_diff(gs(wind=5.0), gs()) == ["wind"]
    assert P.state_diff(gs(), gs(game_seconds=896.0, half_seconds=896.0)) == [
        "game_seconds",
        "half_seconds",
    ]


@dataclass
class FakeCall:
    best: str
    wp: dict


class FakeEngine:
    def fourth_downs(self, states, bootstrap=False):
        assert bootstrap is False
        out = []
        for s in states:
            go = 0.5 + s.score_diff / 100
            fg = None if s.yardline_100 > 45 else 0.48
            out.append(FakeCall("go" if go > 0.49 else "punt", {"go": go, "fg": fg, "punt": 0.49}))
        return out


def test_live_parity_compare_calls_lists_fields_and_deltas():
    pairs = [
        ({"play_id": 1, "choice": "punt", "desc": "punts"}, gs(), gs()),
        ({"play_id": 2, "choice": "go", "desc": "run"}, gs(score_diff=-2), gs(score_diff=0)),
        ({"play_id": 3, "choice": "punt", "desc": "punts"}, gs(yardline_100=50), gs()),
    ]
    rows = P.compare_calls(pairs, FakeEngine())
    assert [r["agree"] for r in rows] == [True, False, True]
    assert rows[0]["fields"] == [] and rows[0]["dwp"]["go"] == 0
    assert rows[1]["fields"] == ["score_diff"]
    assert rows[1]["nflverse_best"] == "punt" and rows[1]["espn_best"] == "go"
    assert rows[1]["dwp"]["go"] == pytest.approx(0.02)
    assert rows[2]["dwp"]["fg"] is None  # no field goal from the nflverse spot
    assert P._max_abs(rows, "go") == pytest.approx(0.02)
    assert P.compare_calls([], FakeEngine()) == []


# --- run_parity on a temporary data root ------------------------------------------------------
def no_network(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
    raise AssertionError(f"the parity check asked ESPN for {request.url}")


def write_root(tmp_path, *, with_plays: bool = True) -> DataPaths:
    paths = DataPaths(root=tmp_path)
    cur = paths.curated
    (cur / "plays").mkdir(parents=True)
    now = datetime.now(UTC)
    games = pl.DataFrame(
        {
            "game_id": ["2026_02_IND_KC", "2026_02_A_B", "2026_02_C_D"],
            "season": [2026] * 3,
            "week": [2] * 3,
            "game_type": ["REG"] * 3,
            "home_team": ["KC", "B", "D"],
            "away_team": ["IND", "A", "C"],
            "kickoff_utc": [T0, now + timedelta(days=2), T0],
            "roof": ["outdoors"] * 3,
            "neutral_site": [False] * 3,
            "spread_line": [3.0] * 3,
            "total_line": [47.0] * 3,
            "temp": [70, 70, 70],
            "wind": [5, 5, 5],
            "stadium": ["Arrowhead", "X", "Y"],
        }
    )
    games.write_parquet(cur / "games.parquet")
    pl.DataFrame(
        {
            "espn_event_id": [EV, "401999002"],
            "game_id": ["2026_02_IND_KC", "2026_02_A_B"],
            "season": [2026, 2026],
        }  # fmt: skip
    ).write_parquet(cur / "espn_scoreboard.parquet")
    if with_plays:
        rows = [nv(3, team="KC") | {"game_id": "2026_02_IND_KC"},
                nv(4, team="KC", down=4, ptype="punt") | {"game_id": "2026_02_IND_KC"}]  # fmt: skip
        pl.DataFrame(rows).with_columns(pl.lit(2026).alias("season")).write_parquet(
            cur / "plays" / "season=2026.parquet"
        )
    folder = paths.live_data / "summaries"
    folder.mkdir(parents=True)
    plays = [
        KICKOFF,
        play(3, "Rush", "run", clock="10:00", down=3, dist=5, yl=60, team="12"),
        play(4, "Punt", "punts", clock="9:20", down=4, dist=3, yl=62, team="12"),
    ]
    (folder / f"{EV}.json.gz").write_bytes(gzip.compress(json.dumps(summary(plays)).encode()))
    return paths


def test_live_parity_run_parity_end_to_end(tmp_path):
    paths = write_root(tmp_path)
    client = EspnClient(transport=httpx.MockTransport(no_network))
    logs: list[str] = []
    rep = P.run_parity(2026, [2], paths=paths, client=client, log=logs.append)
    assert rep["games_compared"] == 1 and rep["states_compared"] == 2
    assert rep["states_equal"] == 1  # play 4: nflverse 4th & 5 at 40, ESPN 4th & 3 at 38
    assert rep["categories"]["spot"]["count"] == 1
    reasons = {s["game_id"]: s["reason"] for s in rep["skipped"]}
    assert reasons == {"2026_02_A_B": "not played yet", "2026_02_C_D": "no ESPN event id"}
    assert (paths.live_data / "parity" / "2026-w02-w02.json").exists()
    assert pl.read_parquet(paths.live_data / "parity" / "2026-w02-w02.parquet").height == 2
    assert client.requests == 0
    assert "1 / 2 equal" in logs[-1]


def test_live_parity_game_without_nflverse_rows_is_skipped(tmp_path, monkeypatch):
    paths = write_root(tmp_path, with_plays=False)
    pl.DataFrame([nv(1) | {"game_id": "other"}]).with_columns(
        pl.lit(2026).alias("season")
    ).write_parquet(paths.curated / "plays" / "season=2026.parquet")

    def offline(_paths):
        raise RuntimeError("offline")

    monkeypatch.setattr(P, "configure_tool_env", offline)
    client = EspnClient(transport=httpx.MockTransport(no_network))
    rep = P.run_parity(2026, [2], paths=paths, client=client, log=lambda _m: None)
    assert rep["games_compared"] == 0 and rep["share_equal"] is None
    reasons = {s["game_id"]: s["reason"] for s in rep["skipped"]}
    assert reasons["2026_02_IND_KC"] == "no nflverse play-by-play yet"
