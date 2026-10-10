"""LD01 CLI: `nfl live call --event | games | replay | latency | parity` and the `show` helpers,
through typer's runner with a fake ESPN client, a hand-made schedule and LD00's stub engine.
No data root, no network, no W&B."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import polars as pl
import pytest
from live_stubs import make_models
from typer.testing import CliRunner

import nflengine.cli as cli
from nflengine.cli import app
from nflengine.live import espn as E
from nflengine.live import replay as R
from nflengine.live import show
from nflengine.live import state as S
from nflengine.live.decide import Engine
from nflengine.live.espn import EspnError, Fetched
from nflengine.paths import DataPaths

runner = CliRunner()
EV = "401872973"
NOW = datetime(2026, 10, 4, 19, 18, 35, tzinfo=UTC)


def flat(text: str) -> str:
    return " ".join(re.sub(r"[│╭╮╰╯─┃┏┓┗┛━┌┐└┘├┤┬┴┼]", " ", text).split())


def event(
    state: str = "in",
    period: int = 3,
    clock: float = 130.0,
    down: int | None = 4,
    distance: int = 10,
    yard_line: int = 86,
    possession: str | None = "10",
    last_text: str = "C.Ward scrambles right end to TEN 15 for 1 yard.",
) -> dict:
    """A scoreboard entry shaped like ESPN's (TEN at BAL, 2026 week 4)."""
    sit: dict = {"homeTimeouts": 3, "awayTimeouts": 2, "lastPlay": {
        "id": "4018729733044", "type": {"text": "Rush"}, "text": last_text,
        "probability": {"homeWinPercentage": 0.9798},
    }}  # fmt: skip
    if down is not None:
        sit.update({"down": down, "distance": distance, "yardLine": yard_line})
    if possession is not None:
        sit["possession"] = possession
    return {
        "id": EV,
        "date": "2026-10-04T17:00Z",
        "season": {"year": 2026, "type": 2},
        "competitions": [
            {
                "competitors": [
                    {
                        "id": "33",
                        "homeAway": "home",
                        "score": "24",
                        "team": {"id": "33", "abbreviation": "BAL"},
                    },
                    {
                        "id": "10",
                        "homeAway": "away",
                        "score": "10",
                        "team": {"id": "10", "abbreviation": "TEN"},
                    },
                ],  # fmt: skip
                "status": {
                    "clock": clock,
                    "displayClock": f"{int(clock) // 60}:{int(clock) % 60:02d}",
                    "period": period,
                    "type": {"state": state, "detail": f"{int(clock) // 60}:00 - 3rd Quarter"},
                },
                "situation": sit if state == "in" else {},
                "venue": {"indoor": False},
            }
        ],
    }


def contexts() -> S.Contexts:
    games = pl.DataFrame(
        {
            "game_id": ["2026_04_TEN_BAL"],
            "season": [2026],
            "week": [4],
            "game_type": ["REG"],
            "home_team": ["BAL"],
            "away_team": ["TEN"],
            "kickoff_utc": [datetime(2026, 10, 4, 17, 0, tzinfo=UTC)],
            "roof": ["outdoors"],
            "neutral_site": [False],
            "pre_spread": [11.5],
            "pre_total": [44.5],
            "line_source": ["nflverse_schedules"],
            "ctx_wind": [8.0],
            "ctx_temp": [60.0],
        }
    )
    return S.Contexts(games, {EV: "2026_04_TEN_BAL"})


class FakeClient:
    """Stands in for `EspnClient`: canned answers, counts calls, never touches the network."""

    answers: dict = {}
    calls: list = []

    def __init__(self, *a, **k) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass

    def close(self) -> None:
        pass

    def _answer(self, key: str):
        FakeClient.calls.append(key)
        a = FakeClient.answers.get(key)
        if isinstance(a, Exception):
            raise a
        if isinstance(a, Fetched):
            return a
        return Fetched(data=a, url=f"https://site.api.espn.com/{key}", fetched_at=NOW, ms=50.0)

    def game(self, ev):
        return self._answer(f"game:{ev}")

    def summary(self, ev):
        return self._answer(f"summary:{ev}")

    def scoreboard(self, season=None, week=None):
        return self._answer("scoreboard")

    def next_allowed_in(self, ev) -> float:
        return 0.0


@pytest.fixture
def fake(monkeypatch):
    FakeClient.answers, FakeClient.calls = {}, []
    monkeypatch.setattr(cli.console, "_width", 250)  # no wrapping inside tables and long lines
    monkeypatch.setattr(E, "EspnClient", FakeClient)
    monkeypatch.setattr(S, "load_contexts", lambda season, paths=None: contexts())
    monkeypatch.setattr(cli, "_live_engine", lambda version=None: Engine(make_models(), threads=1))
    return FakeClient


# --- nfl live call -----------------------------------------------------------------------------
def test_call_needs_exactly_one_source(fake) -> None:
    r = runner.invoke(app, ["live", "call"])
    assert r.exit_code != 0 and "exactly one of --state or --event" in flat(r.output)
    r = runner.invoke(app, ["live", "call", "--event", EV, "--state", "{}"])
    assert r.exit_code != 0


def test_call_rejects_a_bad_event_id(fake) -> None:
    r = runner.invoke(app, ["live", "call", "--event", "12ab"])
    assert r.exit_code != 0 and "not an ESPN event id" in flat(r.output)
    assert fake.calls == [], "nothing is fetched for a bad id"


def test_call_on_a_live_fourth_down(fake) -> None:
    fake.answers[f"game:{EV}"] = event()
    r = runner.invoke(app, ["live", "call", "--event", EV, "--no-bootstrap"])
    out = flat(r.output)
    assert r.exit_code == 0, r.output
    assert "TEN 10 at BAL 24" in out
    assert "TEN ball, 4th & 10 at TEN 14" in out
    assert "timeouts TEN 2, BAL 3" in out
    assert "Call:" in out and "win probability for TEN" in out
    assert fake.calls == [f"game:{EV}"], "no summary needed in the second half"


def test_call_json_carries_the_state_and_the_call(fake) -> None:
    fake.answers[f"game:{EV}"] = event()
    r = runner.invoke(app, ["live", "call", "--event", EV, "--no-bootstrap", "--json"])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)
    st = d["live"]["state"]
    assert st["down"] == 4 and st["ydstogo"] == 10 and st["yardline_100"] == 86
    assert st["score_diff"] == -14 and st["home"] == -1 and st["spread"] == -11.5
    assert st["off_timeouts"] == 2 and st["def_timeouts"] == 3
    assert d["call"]["best"] in ("go", "fg", "punt")
    assert d["espn"]["stale"] is False


def test_call_on_a_third_down_shows_the_table(fake) -> None:
    fake.answers[f"game:{EV}"] = event(down=3, distance=4, yard_line=60)
    r = runner.invoke(app, ["live", "call", "--event", EV, "--no-bootstrap"])
    out = flat(r.output)
    assert r.exit_code == 0, r.output
    assert "3rd down: convert" in out and "If they're stopped short" in out
    assert "4th & 1 at" in out


def test_call_first_half_reads_the_summary_for_the_opening_kickoff(fake) -> None:
    fake.answers[f"game:{EV}"] = event(period=1, clock=600.0)
    fake.answers[f"summary:{EV}"] = {"header": {}, "drives": {"previous": []}}
    r = runner.invoke(app, ["live", "call", "--event", EV, "--no-bootstrap"])
    assert r.exit_code == 0, r.output
    assert fake.calls == [f"game:{EV}", f"summary:{EV}"]
    assert "opening kickoff unknown" in flat(r.output)


def test_call_before_kickoff_and_after_the_game(fake) -> None:
    fake.answers[f"game:{EV}"] = event(state="pre")
    r = runner.invoke(app, ["live", "call", "--event", EV])
    assert r.exit_code == 0 and "Not a 3rd or 4th down: the game hasn't started" in flat(r.output)
    fake.answers[f"game:{EV}"] = event(state="post")
    r = runner.invoke(app, ["live", "call", "--event", EV])
    assert r.exit_code == 0 and "the game is over" in flat(r.output)


def test_call_on_a_first_down_says_so(fake) -> None:
    fake.answers[f"game:{EV}"] = event(down=1, distance=10, yard_line=40)
    r = runner.invoke(app, ["live", "call", "--event", EV])
    assert r.exit_code == 0 and "Not a 3rd or 4th down: down 1" in flat(r.output)


def test_call_when_espn_fails_without_an_earlier_answer(fake) -> None:
    fake.answers[f"game:{EV}"] = EspnError("ESPN didn't answer (no answer within 5 s)")
    r = runner.invoke(app, ["live", "call", "--event", EV])
    assert r.exit_code == 1 and "no answer within 5 s" in flat(r.output)


def test_call_says_when_the_answer_is_stale(fake) -> None:
    old = datetime(2026, 10, 4, 19, 18, 0, tzinfo=UTC)
    fake.answers[f"game:{EV}"] = Fetched(
        data=event(), url="u", fetched_at=old, ms=1.0, stale=True, error="HTTP 503"
    )
    r = runner.invoke(app, ["live", "call", "--event", EV, "--no-bootstrap"])
    out = flat(r.output)
    assert r.exit_code == 0 and "ESPN didn't answer (HTTP 503)" in out
    assert "s ago" in out


def test_call_prints_espn_text_literally(fake) -> None:
    """ESPN text is data: Rich markup inside a play description is printed, not rendered."""
    fake.answers[f"game:{EV}"] = event(last_text="[bold red]BOOM[/bold red] <b>x</b>")
    r = runner.invoke(app, ["live", "call", "--event", EV, "--no-bootstrap"])
    assert r.exit_code == 0, r.output
    assert "[bold red]BOOM[/bold red] <b>x</b>" in r.output


# --- nfl live games ----------------------------------------------------------------------------
def test_games_lists_the_week(fake) -> None:
    fake.answers["scoreboard"] = {
        "season": {"year": 2026},
        "week": {"number": 4},
        "events": [event(), event(state="pre")],
    }
    r = runner.invoke(app, ["live", "games"])
    out = flat(r.output)
    assert r.exit_code == 0, r.output
    assert "2026 week 4" in out and "2026_04_TEN_BAL" in out and "TEN at BAL" in out
    r = runner.invoke(app, ["live", "games", "--json"])
    rows = json.loads(r.output)
    assert [x["state"] for x in rows] == ["in", "pre"]
    assert rows[0]["ball"] == "TEN" and rows[0]["score"] == "10-24" and rows[1]["score"] == ""


def test_games_prints_malformed_espn_fields_literally(fake) -> None:
    """Sol review: an event id or a team code with Rich markup must not break or style the
    table (`[/]` alone raises MarkupError when rendered as markup)."""
    bad = event()
    bad["id"] = "4018[/]x"
    bad["competitions"][0]["competitors"][1]["team"]["abbreviation"] = "[bold]Z\x07"
    fake.answers["scoreboard"] = {"season": {"year": 2026}, "week": {"number": 4}, "events": [bad]}
    r = runner.invoke(app, ["live", "games"])
    assert r.exit_code == 0, r.output
    assert "4018[/]x" in r.output and "[bold]Z at BAL" in r.output
    assert "\x07" not in r.output


def test_games_when_espn_is_down(fake) -> None:
    fake.answers["scoreboard"] = EspnError("ESPN didn't answer (HTTP 503)")
    r = runner.invoke(app, ["live", "games"])
    assert r.exit_code == 1 and "HTTP 503" in flat(r.output)


# --- nfl live replay ---------------------------------------------------------------------------
def summary() -> dict:
    """TEN at BAL: a 3rd down, a 4th down (punt), then the game ends."""

    def play(pid, seq, ttype, text, down, dist, yl, team, end, q=3, clock="2:10", hs=24, aws=10):
        return {
            "id": f"{EV}{pid}", "sequenceNumber": str(seq), "type": {"text": ttype},
            "text": text, "period": {"number": q}, "clock": {"displayValue": clock},
            "homeScore": hs, "awayScore": aws, "wallclock": "2026-10-04T19:10:00Z",
            "start": {"down": down, "distance": dist, "yardLine": yl, "team": {"id": team}},
            "end": end,
        }  # fmt: skip

    plays = [
        play(40, 1, "Kickoff", "kicks", 0, 0, 35, "33", {"down": 1, "team": {"id": "10"}}, q=1),
        play(3020, 2, "Rush", "C.Ward scrambles for 1 yard", 3, 11, 85, "10",
             {"down": 4, "distance": 10, "yardLine": 86, "team": {"id": "10"}}),
        play(3044, 3, "Punt", "J.Stonehouse punts 50 yards", 4, 10, 86, "10",
             {"down": 1, "distance": 10, "yardLine": 40, "team": {"id": "33"}}, clock="2:05"),
    ]  # fmt: skip
    return {
        "header": {
            "id": EV,
            "season": {"year": 2026},
            "competitions": [
                {
                    "status": {"type": {"state": "post"}},
                    "competitors": event()["competitions"][0]["competitors"],
                }
            ],
        },
        "drives": {"previous": [{"plays": plays}]},
    }


@pytest.fixture
def replay_fake(fake, monkeypatch, tmp_path):
    monkeypatch.setattr(
        R, "load_summary", lambda ev, folder, client=None, refresh=False: (summary(), "saved")
    )
    monkeypatch.setattr("nflengine.paths.ensure_data_root", lambda *a, **k: DataPaths(tmp_path))
    return fake


def test_replay_scores_every_third_and_fourth_down(replay_fake) -> None:
    r = runner.invoke(app, ["live", "replay", "--event", EV])
    out = flat(r.output)
    assert r.exit_code == 0, r.output
    assert "2026_04_TEN_BAL" in out
    assert "3rd & 11 at TEN 15" in out and "4th & 10 at TEN 14" in out
    assert "punt" in out and "4th downs: 1;" in out


def test_replay_fourth_downs_only_and_json(replay_fake) -> None:
    r = runner.invoke(app, ["live", "replay", "--event", EV, "--downs", "4", "--json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.output)
    assert len(rows) == 1 and rows[0]["play"]["down"] == 4
    assert rows[0]["play"]["nflverse_play_id"] == 3044 and rows[0]["play"]["choice"] == "punt"
    assert rows[0]["call"]["best"] in ("go", "fg", "punt")


# --- nfl live latency / parity -----------------------------------------------------------------
def test_latency_passes_the_event_and_a_2s_floor(monkeypatch, tmp_path) -> None:
    seen = {}

    def fake_run(ev, minutes, folder, poll_s, log):
        seen.update(ev=ev, minutes=minutes, folder=folder, poll_s=poll_s)
        return {"plays": 0}

    monkeypatch.setattr("nflengine.live.latency.run_latency", fake_run)
    monkeypatch.setattr("nflengine.paths.ensure_data_root", lambda *a, **k: DataPaths(tmp_path))
    r = runner.invoke(app, ["live", "latency", "--event", EV, "--minutes", "5", "--poll", "0.5"])
    assert r.exit_code == 0, r.output
    assert seen == {
        "ev": EV,
        "minutes": 5.0,
        "folder": tmp_path / "live" / "latency",
        "poll_s": 2.0,
    }


def test_parity_command_calls_both_checks(monkeypatch) -> None:
    import types

    calls = {}
    mod = types.SimpleNamespace(
        run_parity=lambda season, weeks, log=print: calls.update(p=(season, weeks)) or {"x": 1},
        decision_parity=lambda evs, log=print: calls.update(d=evs) or {"y": 2},
    )
    monkeypatch.setitem(__import__("sys").modules, "nflengine.live.parity", mod)
    r = runner.invoke(app, ["live", "parity", "--weeks", "1-3", "--decisions", "1,2"])
    assert r.exit_code == 0, r.output
    assert calls == {"p": (2026, [1, 2, 3]), "d": ["1", "2"]}


# --- show --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("ytg", "want"), [(86, "TEN 14"), (14, "BAL 14"), (50, "50"), (99, "TEN 1"), (1, "BAL 1")]
)
def test_spot(ytg, want) -> None:
    assert show.spot(ytg, "TEN", "BAL") == want


def test_down_text_goal_to_go() -> None:
    assert show.down_text(3, 2, 2, "SF", "LA") == "3rd & Goal at LA 2"
    assert show.down_text(4, 10, 86, "TEN", "BAL") == "4th & 10 at TEN 14"


def test_clock_text() -> None:
    assert show.clock_text(3, 130) == "Q3 2:10"
    assert show.clock_text(5, 600) == "OT 10:00" and show.clock_text(6, 5) == "OT2 0:05"


@pytest.mark.parametrize(
    ("ttype", "text", "want"),
    [
        ("Pass Reception", "x pass short", "pass"),
        ("Sack", "x sacked", "pass"),
        ("Rush", "J.Daniels scrambles left end", "pass"),
        ("Rush", "S.Tucker up the middle", "run"),
        ("Penalty", "D.Prescott pass incomplete. PENALTY on TB", "pass"),
        ("Penalty", "J.Williams left tackle. PENALTY on TB", "run"),
    ],
)
def test_play_kind(ttype, text, want) -> None:
    assert show.play_kind(ttype, text) == want
