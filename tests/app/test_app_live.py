"""Game day (LD02): the live endpoints on a replayed game.

ESPN is a `ReplayFeed` over the saved TB at DAL play log (2026 week 5, `tests/fixtures/live/`)
through the real `EspnClient`, on a fake clock (the client's 2 s rule moves the replay too).
The decision engine runs on the LD00 stub models; the team context is a fake cache. Nothing
reaches the network (the `no_real_network` guard from `live_fakes` makes httpx's real
transport raise).
"""

from __future__ import annotations

import copy
import hashlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "live"))

from app_helpers import make_client, make_paths  # noqa: E402
from live_fakes import FakeClock, fixture, no_real_network  # noqa: E402, F401 (autouse guard)
from live_stubs import make_models  # noqa: E402

from nflengine.app.readers.live import (  # noqa: E402
    BEHIND_S,
    NO_MODELS,
    LiveService,
    gain_text,
    spread_text,
)
from nflengine.live.decide import Engine  # noqa: E402
from nflengine.live.espn import ALLOWED_HOST, EspnClient  # noqa: E402
from nflengine.live.replayfeed import ReplayFeed  # noqa: E402

EV = "401872980"  # TB at DAL, 2026 week 5 (Thursday night): a finished game, replayed
LATE = "401872945"  # IND at KC's play log (week 2), moved onto week 5's Sunday night
LAG = 10.0
DAY = timedelta(days=21)  # 2026-09-21 -> 2026-10-12


def utc(month: int, day: int, h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, month, day, h, m, s, tzinfo=UTC)


# moments of the TB at DAL replay (each play shows up LAG s after its snap)
PRE = utc(10, 9, 0, 10)
THIRD = utc(10, 9, 0, 17, 48)  # DAL 3rd & 2 at its 38 (snapped 00:17:39)
SECOND = utc(10, 9, 0, 18, 14)  # DAL 2nd down after a run (snapped 00:18:38)
AFTER_TD = utc(10, 9, 0, 23, 4)  # a rushing touchdown at 00:22:49: the try and the kickoff next
FOURTH = utc(10, 9, 0, 53, 10)  # DAL 4th & 10 at its 44 (snapped 00:53:29; they punted)
HALFTIME = utc(10, 9, 1, 40)  # "End of Half" logged at 01:39:48
POST = utc(10, 9, 4, 0)


def late_summary() -> dict[str, Any]:
    """IND at KC moved to 2026 week 5, Sunday night: every time three weeks later."""
    s = copy.deepcopy(fixture("summary_401872945_ind_kc_ot.json"))
    s["header"]["week"] = 5
    s["header"]["competitions"][0]["date"] = "2026-10-12T00:20Z"
    for d in s["drives"]["previous"]:
        for p in d["plays"]:
            if p.get("wallclock"):
                t = datetime.fromisoformat(p["wallclock"].replace("Z", "+00:00")) + DAY
                p["wallclock"] = t.strftime("%Y-%m-%dT%H:%M:%SZ")
    return s


def write_curated(paths) -> None:
    def k(s: str) -> datetime:
        return datetime.fromisoformat(s)

    games = pl.DataFrame(
        {
            "game_id": ["2026_04_ARI_NYG", "2026_05_TB_DAL", "2026_05_IND_KC", "2026_06_DAL_PHI"],
            "season": [2026] * 4,
            "week": [4, 5, 5, 6],
            "game_type": ["REG"] * 4,
            "home_team": ["NYG", "DAL", "KC", "PHI"],
            "away_team": ["ARI", "TB", "IND", "DAL"],
            "kickoff_utc": [
                k("2026-10-04T17:00:00+00:00"),
                k("2026-10-09T00:15:00+00:00"),
                k("2026-10-12T00:20:00+00:00"),
                k("2026-10-16T00:15:00+00:00"),
            ],
            "roof": ["outdoors", "closed", "outdoors", "outdoors"],
            "neutral_site": [False] * 4,
            "spread_line": [-2.5, 8.5, 6.0, 3.0],
            "total_line": [44.5, 47.5, 46.5, 45.5],
            "temp": [63, None, 71, None],
            "wind": [9, None, 10, None],
            "stadium": ["MetLife Stadium", "AT&T Stadium", "Arrowhead", "Lincoln Financial Field"],
            "home_score": [24, 31, None, None],
            "away_score": [27, 20, None, None],
        }
    ).with_columns(pl.col("kickoff_utc").dt.convert_time_zone("UTC"))
    games.write_parquet(paths.curated / "games.parquet")
    pl.DataFrame(
        {
            "espn_event_id": ["401872966", EV, LATE],
            "game_id": ["2026_04_ARI_NYG", "2026_05_TB_DAL", "2026_05_IND_KC"],
            "season": [2026, 2026, 2026],
        }
    ).write_parquet(paths.curated / "espn_scoreboard.parquet")
    run = paths.run_dir(2026, 5)
    run.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "game_id": ["2026_05_TB_DAL", "2026_05_TB_DAL", "2026_05_IND_KC"],
            "is_primary": [True, False, True],
            "home_win_prob": [0.761, 0.70, 0.66],
        }
    ).write_parquet(run / "predictions_games.parquet")


class FakeCache:
    """The team-context cache: a fixed answer, and a record of what was asked."""

    def __init__(self) -> None:
        self.gets: list[tuple[int, int]] = []
        self.warms: list[tuple[int, int]] = []

    def get(self, season, week, *, paths, engine_factory, model_version=None):
        self.gets.append((season, week))
        stat = {"num": 3, "den": 4, "rate": 0.75}
        measure = {"this": stat, "last": stat, "league_this": stat, "league_last": stat}
        team = {"team": "DAL", "coach": "Brian Schottenheimer", "go_rate": measure}
        return {
            "schema": 1,
            "season": season,
            "week": week,
            "this_season": season,
            "last_season": season - 1,
            "through_week": week - 1,
            "through": "2026 weeks 1-4 and the 2025 season",
            "computed_at": "2026-10-08T12:00:00+00:00",
            "model_version": "stub-v1",
            "teams": {"DAL": team, "TB": {**team, "team": "TB", "coach": "Todd Bowles"}},
        }

    def warm(self, season, week, *, paths, engine_factory, model_version=None):
        self.warms.append((season, week))


class World:
    """One app on the replayed week: the feed, the client, the service and a TestClient."""

    def __init__(self, tmp_path: Path, at: datetime, *, engine: bool = True, lag: float = LAG):
        self.clock = FakeClock()
        self.paths = make_paths(tmp_path)
        write_curated(self.paths)
        self.feed = ReplayFeed(
            {EV: fixture("summary_401872980_tb_dal.json"), LATE: late_summary()},
            event=EV,
            at=at,
            lag_s=lag,
            monotonic=self.clock.monotonic,
        )
        self.urls: list[str] = []
        inner = self.feed.transport()

        def record(request: httpx.Request) -> httpx.Response:
            self.urls.append(str(request.url))
            return inner.handle_request(request)

        self.transport = httpx.MockTransport(record)
        self.client = EspnClient(
            transport=self.transport,
            now=self.feed.now,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
        )
        self.cache = FakeCache()

        def load_engine(_paths):
            if not engine:
                raise FileNotFoundError("no promoted live-decision models yet")
            return Engine(make_models(), None, threads=1), "stub-v1"

        self.live = LiveService(
            client=self.client,
            replay=self.feed,
            engine_loader=load_engine,
            models_version=lambda _p: "stub-v1" if engine else None,
            context_cache=self.cache,
            monotonic=self.clock.monotonic,
            background=False,
        )
        self.http = make_client(tmp_path, live=self.live, live_replay=self.feed)

    def get(self, path: str) -> httpx.Response:
        return self.http.get(path)

    def games(self) -> dict[str, Any]:
        r = self.get("/api/live/2026/5/games")
        assert r.status_code == 200, r.text
        return r.json()

    def call(self, event: str = EV) -> httpx.Response:
        return self.get(f"/api/live/2026/5/games/{event}/call")

    def jump(self, seconds: float) -> None:
        self.clock.advance(seconds)

    def asked(self, kind: str) -> int:
        """How many ESPN requests of a kind: scoreboard (the list), game, summary."""
        if kind == "summary":
            return sum("/summary" in u for u in self.urls)
        if kind == "game":
            return sum("/scoreboard/" in u for u in self.urls)
        return sum(u.split("?")[0].endswith("/scoreboard") for u in self.urls)


# --- the week's games -------------------------------------------------------------------------
def test_the_list_is_one_scoreboard_call_kept_30_s(tmp_path):
    w = World(tmp_path, THIRD)
    body = w.games()
    assert body["phase"] == "live" and body["is_current"] is True
    assert body["replay"]["event"] == EV and body["refresh_s"] == 30
    dal = next(g for g in body["games"] if g["event"] == EV)
    assert (dal["home"], dal["away"], dal["state"]) == ("DAL", "TB", "in")
    assert dal["possession"] == "DAL" and dal["down"] == 3 and dal["distance"] == 2
    assert dal["situation"] == "3rd & 2 at DAL 38" and dal["decision_down"] is True
    assert dal["pregame"]["home_win_prob"] == pytest.approx(0.761)  # the primary row
    assert dal["pregame"]["spread_text"] == "DAL -8.5" and dal["pregame"]["total"] == 47.5
    late = next(g for g in body["games"] if g["event"] == LATE)
    assert late["state"] == "pre" and late["last_play"] is None
    assert body["next_kickoff"] == late["kickoff"]
    assert (w.asked("scoreboard"), w.asked("game"), w.asked("summary")) == (1, 0, 0)
    w.jump(10)
    w.games()
    assert w.asked("scoreboard") == 1  # kept 30 s
    w.jump(25)
    w.games()
    assert w.asked("scoreboard") == 2
    assert w.cache.warms == [(2026, 5)] * 3  # the team context warms in the background


def test_phases_before_between_final(tmp_path):
    assert World(tmp_path / "a", PRE).games()["phase"] == "before"
    w = World(tmp_path / "b", POST)
    body = w.games()
    assert body["phase"] == "between"  # TB at DAL final, Sunday night's game to come
    dal = next(g for g in body["games"] if g["event"] == EV)
    assert (dal["state"], dal["home_score"], dal["away_score"]) == ("post", 16, 24)
    end = World(tmp_path / "c", utc(10, 12, 9, 0)).games()
    assert end["phase"] == "final" and end["next_kickoff"] is None


def test_past_and_future_weeks_never_ask_espn(tmp_path):
    w = World(tmp_path, THIRD)
    past = w.get("/api/live/2026/4/games").json()
    assert past["phase"] == "past" and past["feed"] is None
    assert past["games"][0]["state"] == "post" and past["games"][0]["home_score"] == 24
    future = w.get("/api/live/2026/6/games").json()
    assert future["phase"] == "future" and future["games"][0]["state"] == "pre"
    assert future["games"][0]["pregame"]["spread_text"] == "PHI -3"
    assert w.urls == []


def test_models_missing_is_said_plainly(tmp_path):
    w = World(tmp_path, THIRD, engine=False)
    assert w.games()["models"] == {"available": False, "version": None, "message": NO_MODELS}
    r = w.call()
    assert r.status_code == 503 and r.json()["error"]["code"] == "no_models"


# --- a check ----------------------------------------------------------------------------------
def test_a_third_down_check_has_the_if_stopped_table(tmp_path):
    w = World(tmp_path, THIRD)
    w.games()
    r = w.call()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "third" and body["offense"] == "DAL" and body["defense"] == "TB"
    third = body["third"]
    assert 0 <= third["convert"] <= 1 and 0 <= third["pass_prob"] <= 1
    rows = third["table"]
    assert [r["ydstogo"] for r in rows] == list(range(1, 11))  # 4th & 1 ... 4th & 10
    no_gain = next(r for r in rows if r["gain"] == 0)
    assert no_gain["ydstogo"] == 2 and no_gain["gain_text"] == "no gain"
    assert no_gain["situation"] == "4th & 2 at DAL 38"
    assert rows[0]["gain_text"] == "a 1-yard gain" and rows[-1]["gain_text"] == "a 8-yard loss"
    assert all(set(r["wp"]) == {"go", "fg", "punt"} for r in rows)
    assert body["feed"]["stale"] is False and body["feed"]["as_of"].startswith("2026-10-09T00:1")
    assert body["models"]["version"] == "stub-v1"


def test_a_fourth_down_call_has_three_options_in_order(tmp_path):
    w = World(tmp_path, FOURTH)
    w.games()
    body = w.call().json()
    assert body["kind"] == "fourth" and body["game"]["situation"] == "4th & 10 at DAL 44"
    f = body["fourth"]
    assert [o["choice"] for o in f["options"]] == ["go", "fg", "punt"]
    assert [o["name"] for o in f["options"]] == ["Go for it", "Field goal", "Punt"]
    assert sum(o["best"] for o in f["options"]) == 1
    best = next(o for o in f["options"] if o["best"])
    assert best["choice"] == f["best"] and f["best_name"] == best["name"]
    assert f["gap"] >= 0 and 0 <= f["convert"] <= 1 and f["fg_distance"] > 0


def test_no_call_on_early_downs_before_or_after_the_game(tmp_path):
    w = World(tmp_path / "a", SECOND)
    w.games()
    body = w.call().json()
    assert body["kind"] == "none" and "checks are for 3rd and 4th downs" in body["reason"]
    assert body["fourth"] is None and body["third"] is None
    for at, words in ((PRE, "hasn't started"), (POST, "is over"), (HALFTIME, "Halftime")):
        w = World(tmp_path / at.strftime("%H%M"), at)
        w.games()
        body = w.call().json()
        assert body["kind"] == "none" and words in body["reason"], body["reason"]


def test_no_snap_pending_reads_the_summary_at_once(tmp_path):
    w = World(tmp_path, AFTER_TD)
    w.games()
    body = w.call().json()
    assert body["kind"] == "none" and "no snap pending" in body["reason"]
    assert w.asked("summary") == 1  # down = -1: the next snap from the play log, right away


def test_first_half_checks_fetch_the_opening_kickoff_once(tmp_path):
    w = World(tmp_path, THIRD)
    w.games()
    w.call()
    assert w.asked("summary") == 1  # who received the opening kickoff (the 2nd-half kick)
    w.jump(5)
    w.call()
    assert w.asked("summary") == 1  # known now
    assert w.asked("game") == 2


def test_espn_down_and_stale_answers(tmp_path):
    w = World(tmp_path, FOURTH)
    w.games()
    good = w.call().json()
    w.client._http._transport = httpx.MockTransport(lambda req: httpx.Response(503))  # noqa: SLF001
    w.jump(3)
    stale = w.call().json()
    assert stale["feed"]["stale"] is True and stale["feed"]["error"] == "HTTP 503"
    assert stale["feed"]["as_of"] == good["feed"]["as_of"]  # the last good answer, said so
    assert stale["kind"] == "fourth"
    w2 = World(tmp_path / "b", FOURTH)
    w2.client._http._transport = httpx.MockTransport(lambda req: httpx.Response(500))  # noqa: SLF001
    r = w2.call()
    assert r.status_code == 503 and r.json()["error"]["code"] == "espn_unavailable"
    assert "ESPN didn't answer" in r.json()["error"]["message"]
    lst = w2.games()
    assert lst["feed_error"] and lst["phase"] == "live" and lst["games"]  # the schedule, mid-game


def test_bad_events_and_weeks_are_refused(tmp_path):
    w = World(tmp_path, THIRD)
    for path, status, code in (
        ("/api/live/2026/5/games/12ab/call", 422, "bad_request"),
        ("/api/live/2026/5/games/40187298000000/call", 422, "bad_request"),
        ("/api/live/2026/5/games/401872999/call", 404, "not_in_week"),
        ("/api/live/2026/4/games/401872966/call", 409, "not_live"),
        ("/api/live/2026/23/games", 422, "bad_request"),
        (f"/api/live/2026/5/games/{EV}/context?offense=dal", 422, "bad_request"),
        (f"/api/live/2026/5/games/{EV}/context?offense=KC", 422, "bad_request"),
    ):
        r = w.get(path)
        assert (r.status_code, r.json()["error"]["code"]) == (status, code), path
        assert "12ab" not in r.text  # fixed text, never the input echoed


def test_a_repeat_check_says_no_new_play(tmp_path):
    w = World(tmp_path, FOURTH)
    w.games()
    first = w.call().json()
    assert first["repeat"] == {"same": False, "last_check_at": None, "text": None}
    w.jump(4)
    again = w.call().json()
    assert again["repeat"]["same"] is True
    assert again["repeat"]["text"] == "No new play since your last check (4 s ago)."


def test_behind_only_when_the_last_play_is_old(tmp_path):
    w = World(tmp_path, THIRD)
    w.games()
    w.call()  # fetches the play log (first half): snap times known
    body = w.call().json()
    lp = body["game"]["last_play"]
    assert lp["age_from"] == "snap" and lp["age_s"] < BEHIND_S and body["behind"] is None
    # ESPN 60 s behind: the 2nd-down play is still its newest, over a minute old
    slow = World(tmp_path / "slow", THIRD + timedelta(seconds=32), lag=60.0)
    slow.games()
    slow.call()
    body = slow.call().json()
    assert body["game"]["last_play"]["age_s"] > BEHIND_S
    assert body["behind"]["likely"] is True and "ESPN still shows 3rd & 2" in body["behind"]["text"]


def test_the_context_is_the_offenses_from_the_cache(tmp_path):
    w = World(tmp_path, THIRD)
    r = w.get(f"/api/live/2026/5/games/{EV}/context?offense=DAL")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["team"]["team"] == "DAL" and body["week"] == 5 and body["through_week"] == 4
    assert w.cache.gets == [(2026, 5)]


# --- the rules ---------------------------------------------------------------------------------
def test_only_espns_host_is_ever_asked(tmp_path):
    w = World(tmp_path, THIRD)
    w.games()
    w.call()
    w.get(f"/api/live/2026/5/games/{EV}/context?offense=TB")
    assert w.urls and all(httpx.URL(u).host == ALLOWED_HOST for u in w.urls)
    assert all(u.startswith("https://") for u in w.urls)


def test_nothing_is_fetched_until_the_click(tmp_path):
    """Exit criterion: the list is one scoreboard call; a game is asked only on a check."""
    w = World(tmp_path, FOURTH)
    for _ in range(3):
        w.games()
    assert (w.asked("game"), w.asked("summary")) == (0, 0)
    w.call()
    assert w.asked("game") == 1


def test_espn_text_is_cleaned(tmp_path):
    w = World(tmp_path, FOURTH)
    root = str(w.paths.root)
    leaky = f"\x1b[31m<b>run</b> api_key=sk-or-v1-abcdefabcdef0123456789 at {root}\\x.txt ok"
    summ = fixture("summary_401872980_tb_dal.json")
    for d in summ["drives"]["previous"]:
        for p in d["plays"]:
            if p["id"] == "4018729801059":  # the play before the 4th down
                p["text"] = leaky
    w.feed = ReplayFeed({EV: summ}, event=EV, at=FOURTH, lag_s=LAG, monotonic=w.clock.monotonic)
    w.live.replay = w.feed
    w.client._http._transport = w.feed.transport()  # noqa: SLF001
    body = w.call().text
    assert "sk-or-v1-abcdef" not in body and "\x1b" not in body and root not in body
    assert "<b>run</b>" in body  # plain text: the browser renders it as text, never as HTML


def test_nothing_is_written_outside_the_live_cache(tmp_path):
    w = World(tmp_path, FOURTH)

    def hashes() -> dict[str, str]:
        out = {}
        for p in sorted(tmp_path.rglob("*")):
            if p.is_file() and "control-room" not in p.parts:
                out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        return out

    before = hashes()
    w.games()
    w.call()
    w.get(f"/api/live/2026/5/games/{EV}/context?offense=DAL")
    w.get("/api/live/2026/4/games")
    assert hashes() == before


def test_the_default_app_never_reaches_espn_without_data(tmp_path):
    """make_client's own Game day (no curated schedule): a past week, no request at all."""
    c = make_client(tmp_path)
    body = c.get("/api/live/2026/5/games").json()
    assert body["phase"] == "past" and body["games"] == []
    assert c.get("/api/live/2026/5/games/401872980/call").status_code == 409


def test_live_week_runs_until_six_hours_after_its_last_kickoff(tmp_path):
    paths = make_paths(tmp_path)
    write_curated(paths)
    at = {"t": utc(10, 12, 3, 0)}  # Monday night: week 5's last game kicked off at 00:20
    live = LiveService(now=lambda: at["t"], background=False)
    assert live.live_week(paths, 2026) == (2026, 5)
    at["t"] = utc(10, 12, 6, 21)
    assert live.live_week(paths, 2026) == (2026, 6)
    at["t"] = utc(10, 17, 0, 0)
    assert live.live_week(paths, 2026) is None  # the season in this fixture is over


@pytest.mark.parametrize(
    ("home", "away", "line", "text"),
    [("PHI", "LA", 3.5, "PHI -3.5"), ("PHI", "LA", -3.0, "LA -3"), ("A", "B", 0.0, "Pick'em")],
)
def test_spread_text(home, away, line, text):
    assert spread_text(home, away, line) == text
    assert spread_text(home, away, None) is None


def test_gain_text():
    assert (gain_text(0), gain_text(3), gain_text(-2)) == (
        "no gain",
        "a 3-yard gain",
        "a 2-yard loss",
    )


# --- nfl app --live-replay ----------------------------------------------------------------------
def test_replay_flags_reach_serve(monkeypatch):
    from typer.testing import CliRunner

    import nflengine.app.serve as serve_mod
    from nflengine.cli import app as cli

    seen = {}
    monkeypatch.setattr(serve_mod, "serve", lambda **kw: seen.update(kw) or 0)
    r = CliRunner().invoke(
        cli,
        ["app", "--no-browser", "--live-replay", EV, "--replay-speed", "4", "--replay-lag", "20"],
    )
    assert r.exit_code == 0, r.output
    assert seen["live_replay"] == {"event": EV, "at": None, "speed": 4.0, "lag_s": 20.0}
    r = CliRunner().invoke(cli, ["app", "--replay-at", "2026-10-04T15:57"])
    assert r.exit_code != 0


def test_serve_starts_a_replay_or_says_why_not(tmp_path, monkeypatch):
    import gzip
    import json

    import uvicorn

    import nflengine.app.serve as serve_mod
    import nflengine.app.server as srv
    import nflengine.paths as P

    paths = make_paths(tmp_path)
    monkeypatch.setattr(P, "ensure_data_root", lambda *a, **k: paths)
    started = []

    class FakeServer:
        def __init__(self, config):
            started.append(config)

        def run(self):
            pass

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    built = []
    real = srv.create_app
    monkeypatch.setattr(srv, "create_app", lambda s: built.append(s) or real(s))
    lines: list[str] = []
    replay = {"event": EV, "at": None, "speed": 1.0, "lag_s": 10.0}
    code = serve_mod.serve(dev=True, open_browser=False, log=lines.append, live_replay=replay)
    assert code == serve_mod.EXIT_NO_REPLAY and "no saved summary" in lines[-1] and not started
    folder = paths.live_data / "summaries"
    folder.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(fixture("summary_401872980_tb_dal.json")).encode()
    (folder / f"{EV}.json.gz").write_bytes(gzip.compress(blob))
    lines.clear()
    code = serve_mod.serve(dev=True, open_browser=False, log=lines.append, live_replay=replay)
    assert code == serve_mod.EXIT_OK and started
    assert built[-1].live_replay is not None and built[-1].live_replay.week() == 5
    assert any("Live replay: Game day serves 2026 week 5" in x for x in lines)
