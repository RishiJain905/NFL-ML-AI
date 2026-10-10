"""LD02 review regressions (Game day): what the cross-review of the backend asked for.

One test (or a few) per decision: poisoned ESPN strings never reach the browser (S1), cross-site
GETs are refused (S2), odd ESPN values give a 502 and not a 500 (S3), the ESPN client does its
sleeping and its HTTP calls outside its lock (C1), a check validates its event against the board
it already has (C2), the seen-based "behind" (C3), the model-version / bundle-failure behaviour of
the team context with the real `ContextCache` (C4), the phase when ESPN is down (C5), the repeat
key (C6), a game the schedule doesn't know (C7), a closing app with a summary thread alive (C8),
the summaries kept (C9), the live week in the answer (C10), the replay feed's bad files and the
`nfl app` flags (C11 / C12), the Refresh button, and a JSON-vs-TypeScript contract test over every
live endpoint and state.

The scenes come from `test_app_live.py` (`World`: a replayed TB at DAL on a fake clock, the real
`EspnClient`, the LD00 stub engine, a fake team-context cache). Nothing reaches the network (the
`no_real_network` guard) and nothing is written outside tmp_path.
"""

from __future__ import annotations

import copy
import gzip
import json
import re
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "live"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_app_live as T  # noqa: E402  (the scenes: World, FakeCache, the replay moments)
import test_live_context as TLC  # noqa: E402  (a real team context, for the contract test)
from app_helpers import make_client, make_paths  # noqa: E402
from live_fakes import ctx, fixture, no_real_network  # noqa: E402, F401 (autouse guard)
from live_stubs import make_models  # noqa: E402

from nflengine.app.readers.live import LiveRefused, LiveService  # noqa: E402
from nflengine.live.context import ContextCache  # noqa: E402
from nflengine.live.decide import Engine  # noqa: E402
from nflengine.live.espn import EspnClient  # noqa: E402
from nflengine.live.replay import replay  # noqa: E402
from nflengine.live.replayfeed import ReplayError, ReplayFeed  # noqa: E402

EV, LATE = T.EV, T.LATE
GAMES = "/api/live/2026/5/games"
KEY = "sk-or-v1-abcdefabcdef0123456789"  # shaped like a credential: scrub() masks it
BAD = f"X<b>{KEY}"  # not digits, not a team code, not a play id
UNEXPECTED = "ESPN sent something unexpected for this game. Try again shortly."


# --- helpers -----------------------------------------------------------------------------------
def events_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The events of a scoreboard answer: its `events`, or the answer itself (one game)."""
    return data.get("events") or [data]


def poison(w: T.World, mutate, only=lambda url: "/scoreboard" in url) -> None:
    """Edit ESPN's scoreboard answers on their way to the client (the summary is left alone)."""
    inner = w.feed.transport()

    def handle(request: httpx.Request) -> httpx.Response:
        w.urls.append(str(request.url))
        resp = inner.handle_request(request)
        if not only(str(request.url)):
            return resp
        data = json.loads(resp.content)
        return httpx.Response(resp.status_code, json=mutate(data) or data)

    w.client._http._transport = httpx.MockTransport(handle)  # noqa: SLF001


def espn_down(w: T.World, status: int = 500) -> None:
    w.client._http._transport = httpx.MockTransport(lambda r: httpx.Response(status))  # noqa: SLF001


def with_summary(w: T.World, summary: dict[str, Any], lag: float = T.LAG) -> None:
    """Replace the replayed TB at DAL play log, keeping the replayed moment."""
    w.feed = ReplayFeed(
        {EV: summary, LATE: T.late_summary()},
        event=EV,
        at=w.feed.now(),
        lag_s=lag,
        monotonic=w.clock.monotonic,
    )
    w.live.replay = w.feed
    inner = w.feed.transport()

    def handle(request: httpx.Request) -> httpx.Response:
        w.urls.append(str(request.url))
        return inner.handle_request(request)

    w.client._http._transport = httpx.MockTransport(handle)  # noqa: SLF001


def edit_curated(w: T.World, table: str, edit) -> None:
    path = w.paths.curated / f"{table}.parquet"
    edit(pl.read_parquet(path)).write_parquet(path)


def drop_tb_at_dal(w: T.World) -> None:
    """The schedule no longer knows TB at DAL (ESPN still lists it)."""
    edit_curated(w, "games", lambda df: df.filter(pl.col("game_id") != "2026_05_TB_DAL"))
    edit_curated(w, "espn_scoreboard", lambda df: df.filter(pl.col("espn_event_id") != EV))


def set_summary_wp(summary: dict[str, Any], home_win: float) -> dict[str, Any]:
    """ESPN's `winprobability` for every play (the trimmed fixtures dropped it)."""
    out = copy.deepcopy(summary)
    out["winprobability"] = [
        {"playId": p["id"], "homeWinPercentage": home_win, "tiePercentage": 0.0}
        for d in out["drives"]["previous"]
        for p in d["plays"]
    ]
    return out


def all_strings(o: Any):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from all_strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from all_strings(v)


# --- S1: ESPN strings that aren't text never reach the browser raw -----------------------------
def test_poisoned_team_codes_play_ids_and_event_ids_are_dropped(tmp_path):
    w = T.World(tmp_path, T.THIRD)

    def mutate(data):
        for ev in events_of(data):
            comp = ev["competitions"][0]
            for c in comp["competitors"]:
                c["team"]["abbreviation"] = f"{BAD}{c['homeAway']}"
            sit = comp.get("situation")
            if sit and sit.get("lastPlay"):
                sit["lastPlay"]["id"] = BAD
        for ev in data.get("events") or []:
            if ev["id"] == LATE:
                ev["id"] = BAD  # an event id that isn't digits
        return data

    poison(w, mutate)
    listed = w.get(GAMES)
    assert listed.status_code == 200, listed.text
    assert KEY not in listed.text
    body = listed.json()
    assert [g["event"] for g in body["games"]] == [EV], (
        "an event whose id isn't digits is not listed"
    )
    dal = body["games"][0]
    assert (dal["home"], dal["away"]) == ("DAL", "TB"), "the curated codes"
    assert dal["possession"] is None, "an ESPN code that isn't 2-4 capitals is dropped"
    assert dal["last_play"]["id"] is None, "a play id that isn't digits is dropped"
    checked = w.call()
    assert checked.status_code == 200, checked.text
    assert KEY not in checked.text
    one = checked.json()
    assert one["offense"] is None and one["defense"] is None
    assert one["game"]["possession"] is None and one["game"]["last_play"]["id"] is None


def test_a_game_the_schedule_doesnt_know_shows_question_marks_for_poisoned_codes(tmp_path):
    w = T.World(tmp_path, T.THIRD)
    drop_tb_at_dal(w)

    def mutate(data):
        for ev in events_of(data):
            ev["date"] = BAD
            comp = ev["competitions"][0]
            comp["date"] = BAD
            for c in comp["competitors"]:
                c["team"]["abbreviation"] = f"{BAD}{c['homeAway']}"
        return data

    poison(w, mutate)
    r = w.get(GAMES)
    assert r.status_code == 200, r.text
    assert KEY not in r.text
    g = next(g for g in r.json()["games"] if g["event"] == EV)
    assert g["game_id"] is None
    assert (g["home"], g["away"]) == ("?", "?")
    assert KEY not in (g["kickoff"] or "")


def test_the_last_pass_cleans_every_text_in_the_bodies(tmp_path):
    w = T.World(tmp_path, T.FOURTH)
    root = str(w.paths.root)
    dirty = f"\x1b[31m note {KEY} at {root}\\x.txt"

    def mutate(data):
        for ev in events_of(data):
            comp = ev["competitions"][0]
            comp["status"]["type"]["detail"] = dirty
            comp["status"]["displayClock"] = dirty
            sit = comp.get("situation")
            if sit:
                sit["downDistanceText"] = dirty
                sit["lastPlay"]["text"] = dirty
                sit["lastPlay"]["type"] = {"text": dirty}
        return data

    poison(w, mutate)
    for text in (w.get(GAMES).text, w.call().text, w.call(LATE).text):
        assert KEY not in text and root not in text
        assert "\\u001b" not in text, "control characters are gone (JSON would show \\u001b)"


def test_the_context_body_is_cleaned_too(tmp_path):
    w = T.World(tmp_path, T.THIRD)
    root = str(w.paths.root)

    class Leaky(T.FakeCache):
        def get(self, season, week, **kw):
            data = super().get(season, week, **kw)
            data["teams"]["DAL"]["coach"] = f"Coach {KEY}"
            data["teams"]["DAL"]["note"] = f"{root}\\cache\\x"
            data["through"] = f"weeks {KEY}"
            return data

    w.live._context_cache = Leaky()  # noqa: SLF001
    r = w.get(f"{GAMES}/{EV}/context?offense=DAL")
    assert r.status_code == 200, r.text
    assert KEY not in r.text and root not in r.text


# --- S2: another site's page can't set a check off ---------------------------------------------
@pytest.mark.parametrize("site", ["cross-site", "same-site", "Cross-Site"])
def test_cross_site_gets_are_refused_before_anything_happens(tmp_path, site):
    w = T.World(tmp_path, T.THIRD)
    headers = {"Sec-Fetch-Site": site}
    for path in (GAMES, f"{GAMES}/{EV}/call", f"{GAMES}/{EV}/context?offense=DAL"):
        r = w.http.get(path, headers=headers)
        assert r.status_code == 403, path
        err = r.json()["error"]
        assert err["code"] == "not_allowed"
        assert EV not in err["message"] and "401" not in err["message"], "fixed text"
    assert w.urls == [], "no ESPN request, no engine"
    assert w.cache.gets == [] and w.cache.warms == []


@pytest.mark.parametrize("site", ["same-origin", "none", None])
def test_the_pages_own_requests_pass(tmp_path, site):
    w = T.World(tmp_path, T.THIRD)
    headers = {} if site is None else {"Sec-Fetch-Site": site}
    assert w.http.get(GAMES, headers=headers).status_code == 200
    assert w.http.get(f"{GAMES}/{EV}/call", headers=headers).status_code == 200
    assert w.http.get(f"{GAMES}/{EV}/context?offense=DAL", headers=headers).status_code == 200


def test_the_other_endpoints_are_not_touched_by_the_site_rule(tmp_path):
    w = T.World(tmp_path, T.THIRD)
    r = w.http.get("/api/meta", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200


# --- S3: odd ESPN values are a 502, never a 500 ------------------------------------------------
def test_a_non_numeric_period_is_a_502_in_the_list_and_in_a_check(tmp_path, caplog):
    bad = "p3r1od-zz"

    def mutate(data):
        for ev in events_of(data):
            ev["competitions"][0]["status"]["period"] = bad
        return data

    for name, call in (("list", False), ("check", True)):
        w = T.World(tmp_path / name, T.THIRD)
        poison(w, mutate)
        r = w.call() if call else w.get(GAMES)
        assert r.status_code == 502, (name, r.text)
        err = r.json()["error"]
        assert err["code"] == "espn_unexpected" and err["message"] == UNEXPECTED
        assert bad not in r.text
    assert bad not in caplog.text, "logged by exception type, not by value"


@pytest.mark.parametrize(
    "how",
    ["down_string", "no_competitions", "competitors_none", "situation_list"],
)
def test_other_odd_values_never_become_a_500(tmp_path, how):
    def mutate(data):
        for ev in events_of(data):
            if how == "down_string":
                sit = ev["competitions"][0].get("situation")
                if sit:
                    sit["down"] = "x"
            elif how == "no_competitions":
                del ev["competitions"]
            elif how == "competitors_none":
                ev["competitions"][0]["competitors"] = None
            elif how == "situation_list":
                ev["competitions"][0]["situation"] = ["not", "a", "dict"]
        return data

    w = T.World(tmp_path, T.THIRD)
    poison(w, mutate)
    for r in (w.get(GAMES), w.call()):
        assert r.status_code in (200, 502), (how, r.status_code, r.text)
        if r.status_code == 502:
            err = r.json()["error"]
            assert (err["code"], err["message"]) == ("espn_unexpected", UNEXPECTED)


# --- C1: the ESPN client sleeps and waits for HTTP outside its lock ----------------------------
class Wire:
    """A fake ESPN: records when each request went out, and can be slow for some URLs."""

    def __init__(self, delays: dict[str, float] | None = None):
        self.delays = delays or {}
        self.sent: list[tuple[float, str]] = []
        self._lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        with self._lock:
            self.sent.append((time.monotonic(), url))
        for part, delay in self.delays.items():
            if part in url:
                time.sleep(delay)
        return httpx.Response(200, json={"url": url})

    def times(self, part: str) -> list[float]:
        return [t for t, u in self.sent if part in u]


def real_client(wire: Wire, **kw) -> EspnClient:
    return EspnClient(transport=httpx.MockTransport(wire), **kw)


def in_thread(fn, *args) -> tuple[threading.Thread, list]:
    out: list = []

    def run() -> None:
        out.append(fn(*args))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def test_a_slow_game_does_not_hold_up_another_games_request():
    wire = Wire({"/scoreboard/111": 1.0})
    client = real_client(wire, host_interval=0.05)
    slow, _ = in_thread(client.game, "111")
    time.sleep(0.15)  # game A's request is out and waiting for its answer
    t0 = time.perf_counter()
    got = client.game("222")
    waited = time.perf_counter() - t0
    assert got.data["url"].endswith("/scoreboard/222")
    assert waited < 0.5, f"game B waited {waited:.2f} s for game A's slow answer"
    assert slow.is_alive(), "game A's request was still in flight when B was answered"
    slow.join(5)
    client.close()


def test_a_second_caller_for_a_url_in_flight_waits_for_that_answer():
    wire = Wire({"/scoreboard/111": 0.6})
    client = real_client(wire)
    first, a = in_thread(client.game, "111")
    time.sleep(0.1)
    second, b = in_thread(client.game, "111")
    first.join(5)
    second.join(5)
    assert client.requests == 1 and len(wire.sent) == 1, "one request for both callers"
    assert a[0].data == b[0].data
    assert sorted([a[0].cached, b[0].cached]) == [False, True], "the waiter's answer is cached=True"
    assert a[0].fetched_at == b[0].fetched_at
    client.close()


def test_the_spacing_rules_hold_between_threads():
    wire = Wire()
    client = real_client(wire, min_interval=0.6, host_interval=0.2)
    jobs = [
        in_thread(client.game, "111"),
        in_thread(client.summary, "111"),  # the same game: 2 s rule (here 0.6 s)
        in_thread(client.game, "222"),
    ]
    for t, _ in jobs:
        t.join(10)
    assert all(out for _, out in jobs) and len(wire.sent) == 3
    times = sorted(t for t, _ in wire.sent)
    assert all(b - a >= 0.2 * 0.8 for a, b in zip(times, times[1:], strict=False)), times
    game_a, summary_a = wire.times("/scoreboard/111")[0], wire.times("/summary")[0]
    assert abs(game_a - summary_a) >= 0.6 * 0.8, "one game's requests stay min_interval apart"
    client.close()


def test_a_memory_answer_does_not_wait_behind_a_caller_sleeping_out_the_rule():
    wire = Wire()
    client = real_client(wire, min_interval=1.0, host_interval=0.05)
    assert client.game("111").cached is False
    sleeper, _ = in_thread(client.summary, "111")  # must wait ~1 s for game 111's clock
    time.sleep(0.2)
    t0 = time.perf_counter()
    again = client.game("111")
    waited = time.perf_counter() - t0
    assert again.cached is True and waited < 0.3, f"a memory answer waited {waited:.2f} s"
    sleeper.join(5)
    client.close()


# --- C2: a check uses the board it has ---------------------------------------------------------
def test_a_check_doesnt_re_ask_the_scoreboard_for_a_board_over_30_s_old(tmp_path):
    w = T.World(tmp_path, T.THIRD)
    w.games()
    assert w.asked("scoreboard") == 1
    w.jump(40)
    r = w.call()
    assert r.status_code == 200, r.text
    assert w.asked("scoreboard") == 1, "the event is in the board it already has"
    assert w.asked("game") == 1
    w.jump(40)
    ctx_r = w.get(f"{GAMES}/{EV}/context?offense=DAL")
    assert ctx_r.status_code == 200 and w.asked("scoreboard") == 1


def test_an_unknown_event_refreshes_the_board_once_then_is_a_404(tmp_path):
    w = T.World(tmp_path, T.THIRD)
    w.games()
    w.jump(3)
    r = w.call("401872999")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_in_week")
    assert w.asked("scoreboard") == 2, "only an unknown id asks ESPN again"
    assert w.asked("game") == 0


# --- C3: "ESPN is behind" from the app's own sightings -----------------------------------------
def long_gap_before_a_second_half_snap():
    """(the play two before, the play before, the snap): a 3rd / 4th down in the 2nd half that
    follows a gap of 75 s or more, the play before it itself 5 s or more after the one before
    (the two-minute warning, play 173 of TB at DAL)."""
    rows = replay(fixture("summary_401872980_tb_dal.json"), ctx("tb_dal"))
    for i in range(2, len(rows)):
        r = rows[i]
        gap = (r.wallclock - rows[i - 1].wallclock).total_seconds()
        lead = (rows[i - 1].wallclock - rows[i - 2].wallclock).total_seconds()
        if r.snap and r.down in (3, 4) and r.period >= 3 and gap >= 75 and lead >= 5:
            return rows[i - 2], rows[i - 1], r
    raise AssertionError("no long gap before a second-half snap in the fixture")


def test_behind_from_two_sightings_and_no_summary(tmp_path):
    pp, prev, snap = long_gap_before_a_second_half_snap()
    lag = T.LAG
    seen_first = pp.wallclock + timedelta(seconds=lag + 1)  # the list sees the play before
    seen_next = prev.wallclock + timedelta(seconds=lag + 1)  # ...then the last play it will see
    w = T.World(tmp_path, seen_first)
    w.live.refresh_s = 1.0
    w.games()
    w.jump((seen_next - seen_first).total_seconds())
    w.games()  # the list watched this play appear
    w.jump(60)  # ESPN has nothing new: the snap is 94 s after this play
    body = w.call().json()
    lp = body["game"]["last_play"]
    assert lp["id"] == prev.play_id and lp["age_from"] == "seen"
    assert 55 <= lp["age_s"] <= 70, lp
    assert body["kind"] in ("third", "fourth")
    assert body["behind"]["likely"] is True
    assert f"ESPN still shows {body['game']['situation']}" in body["behind"]["text"]
    assert w.asked("summary") == 0, "no summary was needed for this"


def test_one_sighting_is_not_enough_to_claim_an_age(tmp_path):
    _, prev, _ = long_gap_before_a_second_half_snap()
    w = T.World(tmp_path, prev.wallclock + timedelta(seconds=T.LAG + 1))
    w.live.refresh_s = 1.0
    w.games()
    w.jump(60)
    body = w.call().json()
    lp = body["game"]["last_play"]
    assert lp["age_s"] is None and lp["age_from"] is None and body["behind"] is None


# --- C4: the team context and the model bundle (the real ContextCache) -------------------------
def counting_cache(folder: Path):
    builds: list[str | None] = []

    def build(season, week, **kw):
        builds.append(kw.get("model_version"))
        return {
            "schema": 1, "season": season, "week": week, "this_season": season,
            "last_season": season - 1, "through_week": week - 1, "through": "x",
            "computed_at": "t", "model_version": kw.get("model_version"),
            "teams": {"DAL": {"team": "DAL"}, "TB": {"team": "TB"}}, "league": {},
        }  # fmt: skip

    return ContextCache(folder, build), builds


def context_world(tmp_path: Path, loader, pointer: dict[str, str]):
    w = T.World(tmp_path, T.THIRD)
    cache, builds = counting_cache(w.paths.cache / "control-room" / "live" / "context")
    loads: list[int] = []

    def counted(paths):
        loads.append(1)
        return loader(paths)

    w.live._engine_loader = counted  # noqa: SLF001
    w.live._context_cache = cache  # noqa: SLF001
    w.live._models_version = lambda paths: pointer["v"]  # noqa: SLF001
    return w, builds, loads


def ask_context(w: T.World) -> httpx.Response:
    r = w.get(f"{GAMES}/{EV}/context?offense=DAL")
    assert r.status_code == 200, r.text
    return r


def test_a_bundle_that_fails_to_load_is_tried_once_and_the_context_built_once(tmp_path):
    def broken(_paths):
        raise RuntimeError("bad bundle")

    w, builds, loads = context_world(tmp_path, broken, {"v": "v1"})
    for _ in range(3):
        ask_context(w)
    assert len(loads) == 1, "a failed load is remembered, not retried on every request"
    assert builds == [None], "the cache is given the version it was built with (none)"
    for _ in range(2):  # anything else that needs the engine within the minute: the same answer
        with pytest.raises(LiveRefused) as e:
            w.live.engine(w.paths)
        assert e.value.code == "no_models"
    assert len(loads) == 1
    w.jump(61)  # a minute later the bundle is tried again
    with pytest.raises(LiveRefused):
        w.live.engine(w.paths)
    assert len(loads) == 2
    ask_context(w)
    assert builds == [None], "it failed again: still the same context"


def test_a_fixed_bundle_rebuilds_the_context_with_its_version(tmp_path):
    state = {"ok": False}

    def loader(_paths):
        if not state["ok"]:
            raise RuntimeError("bad bundle")
        return Engine(make_models(), None, threads=1), "v1"

    w, builds, loads = context_world(tmp_path, loader, {"v": "v1"})
    ask_context(w)
    assert builds == [None]
    state["ok"] = True
    w.jump(61)
    w.live.engine(w.paths)  # a check (or the list's warm-up) finds the bundle loads now
    ask_context(w)
    ask_context(w)
    assert len(loads) == 2 and builds == [None, "v1"], "the new context knows the bundle"


def test_a_repromotion_while_the_app_holds_the_old_bundle_does_not_rebuild_forever(tmp_path):
    pointer = {"v": "v1"}
    w, builds, loads = context_world(
        tmp_path, lambda _p: (Engine(make_models(), None, threads=1), "v1"), pointer
    )
    ask_context(w)
    pointer["v"] = "v2"  # `nfl live train --promote` while the app runs
    for _ in range(3):
        ask_context(w)
    assert builds == ["v1"], "the engine in memory is v1: the context is v1's until a restart"
    assert len(loads) == 1


def test_the_context_is_built_once_for_a_healthy_bundle(tmp_path):
    w, builds, loads = context_world(
        tmp_path, lambda _p: (Engine(make_models(), None, threads=1), "v1"), {"v": "v1"}
    )
    for _ in range(3):
        ask_context(w)
    assert builds == ["v1"] and len(loads) == 1


# --- C5: the phase when ESPN is down comes from the kickoffs -----------------------------------
@pytest.mark.parametrize(
    ("at", "phase"),
    [
        (T.PRE, "before"),  # nothing kicked off yet (TB at DAL 00:15)
        (T.FOURTH, "live"),  # a game kicked off 38 minutes ago
        (T.utc(10, 10, 12, 0), "between"),  # TB at DAL is over, IND at KC is days away
        (T.utc(10, 12, 5, 0), "final"),  # the last game kicked off 00:20: 4.5 h are up
    ],
)
def test_espn_down_phase_comes_from_the_kickoffs(tmp_path, at, phase):
    w = T.World(tmp_path, at)
    espn_down(w)
    body = w.games()
    assert body["feed"] is None and body["feed_error"], "the list says ESPN didn't answer"
    assert body["phase"] == phase
    assert body["games"], "the schedule's slate stands in"


# --- C6: the repeat key has no clock ------------------------------------------------------------
def test_a_ticking_clock_is_not_a_new_play(tmp_path):
    w = T.World(tmp_path, T.FOURTH)
    ticks = {"n": 0}

    def mutate(data):
        ticks["n"] += 1
        for ev in events_of(data):
            if "situation" in ev["competitions"][0] or ev["competitions"][0]["status"]:
                ev["competitions"][0]["status"]["displayClock"] = f"9:{59 - ticks['n']:02d}"
        return data

    poison(w, mutate, only=lambda url: "/scoreboard/" in url)
    first = w.call().json()
    w.jump(4)
    again = w.call().json()
    assert first["game"]["clock"] != again["game"]["clock"], "the scoreboard clock ticked"
    assert again["repeat"]["same"] is True
    assert again["repeat"]["text"] == "No new play since your last check (4 s ago)."
    w.jump(40)  # the 4th down is snapped and posted: a new play
    later = w.call().json()
    assert later["repeat"]["same"] is False


# --- C7 / gap 6: a game ESPN lists that the schedule doesn't know ------------------------------
def test_an_unscheduled_game_is_listed_in_espns_words_and_refused_for_a_check(tmp_path):
    w = T.World(tmp_path, T.THIRD)
    drop_tb_at_dal(w)
    body = w.games()
    g = next(g for g in body["games"] if g["event"] == EV)
    assert g["game_id"] is None
    assert (g["home"], g["away"], g["state"]) == ("DAL", "TB", "in")
    assert g["decision_down"] is False
    assert all(v is None for k, v in g["pregame"].items()), "no pre-game facts for it"
    other = next(g for g in body["games"] if g["event"] == LATE)
    assert other["game_id"] == "2026_05_IND_KC"
    r = w.call()
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_in_schedule")
    r = w.get(f"{GAMES}/{EV}/context?offense=DAL")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_in_schedule")
    assert w.cache.gets == []


# --- C8: closing the app with a summary thread alive --------------------------------------------
def test_close_with_a_summary_job_alive_prints_no_traceback(tmp_path, monkeypatch):
    rows = replay(fixture("summary_401872980_tb_dal.json"), ctx("tb_dal"))
    first = next(i for i, r in enumerate(rows) if r.snap and r.period == 3 and r.down == 3)
    at = rows[first - 1].wallclock + timedelta(seconds=T.LAG + 1)  # a 2nd-half 3rd down
    paths = make_paths(tmp_path)
    T.write_curated(paths)
    feed = ReplayFeed(
        {EV: fixture("summary_401872980_tb_dal.json"), LATE: T.late_summary()},
        event=EV,
        at=at,
        lag_s=T.LAG,
    )
    client = EspnClient(
        transport=feed.transport(), now=feed.now, min_interval=0.3, host_interval=0.05
    )
    live = LiveService(
        client=client,
        replay=feed,
        engine_loader=lambda _p: (Engine(make_models(), None, threads=1), "stub-v1"),
        models_version=lambda _p: "stub-v1",
        context_cache=T.FakeCache(),
        background=True,
    )
    http = make_client(tmp_path, live=live, live_replay=feed)
    errors: list[str] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(repr(args.exc_value)))
    assert http.get(GAMES).status_code == 200
    assert http.get(f"{GAMES}/{EV}/call").status_code == 200  # a summary refresh is scheduled
    assert live._summary_jobs, "the refresh job is waiting its turn"  # noqa: SLF001
    live.close()  # the app is shutting down while that job waits its turn (0.3 s)
    deadline = time.monotonic() + 2.0  # the job wakes after ~0.3 s and meets a closed client
    while time.monotonic() < deadline and not errors:
        time.sleep(0.05)
    time.sleep(0.2)
    assert errors == []


# --- C9: the summaries kept --------------------------------------------------------------------
def test_a_finished_games_summary_is_dropped_and_at_most_16_are_kept(tmp_path):
    w = T.World(tmp_path, T.THIRD)
    w.games()
    assert w.call().status_code == 200  # the 1st half: the play log is fetched
    assert EV in w.live._summaries  # noqa: SLF001
    # the game ends: the next list sees it final
    w.jump((T.POST - w.feed.now()).total_seconds())
    w.games()
    assert w.call().status_code == 200  # a check on the finished game
    assert EV not in w.live._summaries  # noqa: SLF001
    # and a flood of games never keeps more than 16
    w2 = T.World(tmp_path / "many", T.THIRD)
    now = w2.clock.monotonic()
    for i in range(20):
        w2.live._summaries[f"9{i:02d}"] = ({}, now - 100 + i)  # noqa: SLF001
    w2.games()
    assert w2.call().status_code == 200
    kept = w2.live._summaries  # noqa: SLF001
    assert len(kept) <= 16 and EV in kept, "the newest stay"


# --- C10: the live week is in the answer ---------------------------------------------------------
def test_the_live_week_is_named_in_every_list(tmp_path):
    w = T.World(tmp_path, T.THIRD)
    here = {"season": 2026, "week": 5}
    assert w.games()["live_week"] == here
    assert w.get("/api/live/2026/4/games").json()["live_week"] == here  # a past week points here
    assert w.get("/api/live/2026/6/games").json()["live_week"] == here  # and so does a future one


def test_no_live_week_once_the_season_is_over(tmp_path):
    paths = make_paths(tmp_path)
    T.write_curated(paths)
    live = LiveService(now=lambda: T.utc(10, 17, 0, 0), background=False)
    http = make_client(tmp_path, live=live)
    body = http.get(GAMES).json()
    assert body["live_week"] is None and body["phase"] == "past" and body["is_current"] is False


# --- the Refresh button ------------------------------------------------------------------------
def test_refresh_re_asks_espn_only_when_the_list_is_over_10_s_old(tmp_path):
    w = T.World(tmp_path, T.THIRD)
    w.games()
    assert w.asked("scoreboard") == 1
    w.jump(5)
    assert w.get(f"{GAMES}?refresh=1").status_code == 200
    assert w.asked("scoreboard") == 1, "under 10 s: the server's board stands"
    w.jump(6)
    w.games()
    assert w.asked("scoreboard") == 1, "a plain list is kept 30 s"
    assert w.get(f"{GAMES}?refresh=1").status_code == 200
    assert w.asked("scoreboard") == 2, "over 10 s old: Refresh asks again"
    w.jump(31)
    w.games()
    assert w.asked("scoreboard") == 3, "and the 30 s rule still holds for a plain list"


def test_refresh_on_a_past_week_never_asks_espn(tmp_path):
    w = T.World(tmp_path, T.THIRD)
    assert w.get("/api/live/2026/4/games?refresh=1").status_code == 200
    assert w.urls == []


# --- gap 8: ESPN's own win probability -----------------------------------------------------------
def test_espns_win_probability_is_from_each_teams_side(tmp_path):
    summary = set_summary_wp(fixture("summary_401872980_tb_dal.json"), 0.62)
    # DAL (ESPN's and nflverse's home team) has the ball
    w = T.World(tmp_path / "home", T.THIRD)
    with_summary(w, summary)
    g = w.games()["games"][0]
    assert g["event"] == EV and g["espn_home_wp"] == pytest.approx(0.62)
    body = w.call().json()
    assert body["offense"] == "DAL" and body["espn_offense_wp"] == pytest.approx(0.62)
    # TB (the away team) has the ball: its chance is the rest
    rows = replay(fixture("summary_401872980_tb_dal.json"), ctx("tb_dal"))
    i = next(  # a TB snap that follows its previous play by 25 s or more (time to check)
        i
        for i in range(1, len(rows))
        if rows[i].snap
        and not rows[i].offense_home
        and (rows[i].wallclock - rows[i - 1].wallclock).total_seconds() >= 25
    )
    prev = rows[i - 1]
    away = T.World(tmp_path / "away", prev.wallclock + timedelta(seconds=T.LAG + 1))
    with_summary(away, summary)
    body = away.call().json()
    assert body["offense"] == "TB" and body["espn_offense_wp"] == pytest.approx(0.38)
    assert body["game"]["espn_home_wp"] == pytest.approx(0.62)


def test_a_neutral_site_where_espn_and_nflverse_disagree_on_the_home_team(tmp_path):
    """ESPN's home is DAL, nflverse's is TB (a neutral site): ESPN's 62% for DAL is 38% for
    nflverse's home team, and 62% for DAL on offense all the same."""
    w = T.World(tmp_path, T.THIRD)
    edit_curated(
        w,
        "games",
        lambda df: df.with_columns(
            pl.when(pl.col("game_id") == "2026_05_TB_DAL")
            .then(pl.lit("TB"))
            .otherwise(pl.col("home_team"))
            .alias("home_team"),
            pl.when(pl.col("game_id") == "2026_05_TB_DAL")
            .then(pl.lit("DAL"))
            .otherwise(pl.col("away_team"))
            .alias("away_team"),
            pl.when(pl.col("game_id") == "2026_05_TB_DAL")
            .then(True)
            .otherwise(pl.col("neutral_site"))
            .alias("neutral_site"),
        ),
    )
    with_summary(w, set_summary_wp(fixture("summary_401872980_tb_dal.json"), 0.62))
    g = w.games()["games"][0]
    assert (g["home"], g["away"]) == ("TB", "DAL")
    assert g["espn_home_wp"] == pytest.approx(0.38), "the chance of nflverse's home team"
    body = w.call().json()
    assert body["offense"] == "DAL"
    assert body["espn_offense_wp"] == pytest.approx(0.62)
    assert any("ESPN lists DAL at home" in x for x in body["warnings"])


# --- C11: bad files in the replay folder --------------------------------------------------------
def save(folder: Path, event: str, blob: bytes) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{event}.json.gz").write_bytes(blob)


def good_gz() -> bytes:
    return gzip.compress(json.dumps(fixture("summary_401872980_tb_dal.json")).encode())


def test_other_bad_summaries_are_skipped(tmp_path):
    save(tmp_path, EV, good_gz())
    other = copy.deepcopy(fixture("summary_401872980_tb_dal.json"))
    other["header"]["id"] = "401872999"
    save(tmp_path, "401872999", gzip.compress(json.dumps(other).encode()))
    save(tmp_path, "401872111", good_gz()[: len(good_gz()) // 2])  # cut short: EOFError
    save(tmp_path, "401872222", b"not gzip at all")
    save(tmp_path, "401872333", gzip.compress(b"{not json"))
    feed = ReplayFeed.from_folder(tmp_path, EV)
    assert [e["id"] for e in feed.scoreboard_json()["events"]] == [EV, "401872999"]


def test_a_json_list_instead_of_a_summary_is_skipped_too(tmp_path):
    save(tmp_path, EV, good_gz())
    save(tmp_path, "401872444", gzip.compress(b'["a", "list"]'))
    feed = ReplayFeed.from_folder(tmp_path, EV)
    assert [e["id"] for e in feed.scoreboard_json()["events"]] == [EV]


@pytest.mark.parametrize(
    "blob",
    [good_gz()[: len(good_gz()) // 2], b"not gzip at all", gzip.compress(b"{not json")],
    ids=["truncated", "not-gzip", "bad-json"],
)
def test_an_unreadable_main_summary_is_a_replay_error(tmp_path, blob):
    save(tmp_path, EV, blob)
    with pytest.raises(ReplayError):
        ReplayFeed.from_folder(tmp_path, EV)


def test_serve_exits_3_with_a_message_when_the_main_summary_is_cut_short(tmp_path, monkeypatch):
    import uvicorn

    import nflengine.app.serve as serve_mod
    import nflengine.paths as P

    paths = make_paths(tmp_path)
    monkeypatch.setattr(P, "ensure_data_root", lambda *a, **k: paths)
    started: list[Any] = []

    class FakeServer:
        def __init__(self, config):
            started.append(config)

        def run(self):
            pass

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    save(paths.live_data / "summaries", EV, good_gz()[:1000])
    lines: list[str] = []
    code = serve_mod.serve(
        dev=True,
        open_browser=False,
        log=lines.append,
        live_replay={"event": EV, "at": None, "speed": 1.0, "lag_s": 10.0},
    )
    assert code == serve_mod.EXIT_NO_REPLAY == 3
    assert any("replay" in x.lower() for x in lines) and not started


# --- C12: replay flags without --live-replay ----------------------------------------------------
@pytest.mark.parametrize(
    "flags",
    [
        ["--replay-speed", "4"],
        ["--replay-lag", "20"],
        ["--replay-at", "2026-10-04T15:57"],
        ["--replay-speed", "4", "--replay-lag", "20"],
    ],
)
def test_replay_flags_need_live_replay(monkeypatch, flags):
    from typer.testing import CliRunner

    import nflengine.app.serve as serve_mod
    from nflengine.cli import app as cli

    started: list[Any] = []
    monkeypatch.setattr(serve_mod, "serve", lambda **kw: started.append(kw) or 0)
    r = CliRunner().invoke(cli, ["app", "--no-browser", *flags])
    assert r.exit_code != 0 and not started
    assert "live-replay" in r.output.lower()


def test_replay_flags_with_live_replay_reach_serve(monkeypatch):
    from typer.testing import CliRunner

    import nflengine.app.serve as serve_mod
    from nflengine.cli import app as cli

    seen: dict[str, Any] = {}
    monkeypatch.setattr(serve_mod, "serve", lambda **kw: seen.update(kw) or 0)
    r = CliRunner().invoke(
        cli,
        ["app", "--no-browser", "--live-replay", EV, "--replay-speed", "4", "--replay-lag", "20"],
    )
    assert r.exit_code == 0, r.output
    assert seen["live_replay"]["speed"] == 4.0 and seen["live_replay"]["lag_s"] == 20.0


# --- the contract: every live answer against web/src/api/types.ts -------------------------------
TYPES_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "api" / "types.ts"
CHILD: dict[tuple[str, str], tuple[str, bool]] = {  # (interface, field) -> (interface, is a list)
    ("LiveGamesResponse", "games"): ("LiveGame", True),
    ("LiveGamesResponse", "feed"): ("LiveFeed", False),
    ("LiveGamesResponse", "models"): ("LiveModels", False),
    ("LiveGame", "last_play"): ("LiveLastPlay", False),
    ("LiveGame", "pregame"): ("LivePregame", False),
    ("LiveCallResponse", "game"): ("LiveGame", False),
    ("LiveCallResponse", "feed"): ("LiveFeed", False),
    ("LiveCallResponse", "models"): ("LiveModels", False),
    ("LiveCallResponse", "fourth"): ("LiveFourth", False),
    ("LiveCallResponse", "third"): ("LiveThird", False),
    ("LiveFourth", "options"): ("LiveOption", True),
    ("LiveThird", "table"): ("LiveTableRow", True),
    ("LiveContextResponse", "team"): ("LiveContextTeam", False),
    ("LiveContextTeam", "go_rate"): ("LiveMeasure", False),
    ("LiveContextTeam", "fourth_conv"): ("LiveMeasure", False),
    ("LiveContextTeam", "short_conv"): ("LiveMeasure", False),
    ("LiveContextTeam", "red_zone_td"): ("LiveMeasure", False),
    ("LiveContextTeam", "coach_go"): ("LiveMeasure", False),
    ("LiveMeasure", "this"): ("LiveStat", False),
    ("LiveMeasure", "last"): ("LiveStat", False),
    ("LiveMeasure", "league_this"): ("LiveStat", False),
    ("LiveMeasure", "league_last"): ("LiveStat", False),
    ("LiveKickerBand", "this"): ("LiveStat", False),
    ("LiveKickerBand", "last"): ("LiveStat", False),
    ("LiveKickerBand", "league_last"): ("LiveStat", False),
}


def ts_text() -> str:
    return TYPES_TS.read_text(encoding="utf-8")


def ts_alias(name: str) -> set[str] | None:
    m = re.search(rf"export type {name} = ([^;]+);", ts_text())
    return set(re.findall(r"'([^']+)'", m.group(1))) if m else None


def ts_interface(name: str) -> dict[str, list]:
    """field -> [type text, optional, the keys of an inline object type (or None)]."""
    m = re.search(rf"export interface {name} \{{\n(.*?)\n\}}", ts_text(), re.S)
    assert m, f"{name} is not in types.ts"
    fields: dict[str, list] = {}
    current = None
    for line in m.group(1).splitlines():
        f = re.match(r"^  (\w+)(\?)?: (.*?);?(?:\s*//.*)?$", line)
        if f:
            ty = f.group(3).strip()
            keys: list[str] | None = None
            if ty.startswith("{") and "}" in ty:  # one-line inline object
                inner = ty[1 : ty.index("}")]
                keys = re.findall(r"(\w+)\??:", inner)
            elif ty == "{":  # a multi-line inline object: its closing line adds `| null`, `[]`
                keys = []
                current = f.group(1)
            fields[f.group(1)] = [ty, bool(f.group(2)), keys]
            continue
        inner_line = re.match(r"^    (\w+)\??:", line)
        if current and inner_line:
            fields[current][2].append(inner_line.group(1))
            continue
        closing = re.match(r"^  \}(.*?);?(?:\s*//.*)?$", line)
        if current and closing:
            fields[current][0] += "}" + closing.group(1)
            current = None
    return fields


def type_ok(value: Any, ty: str, optional: bool) -> bool:
    if value is None:
        return optional or "null" in ty
    if isinstance(value, bool):
        return "boolean" in ty
    if isinstance(value, int | float):
        return "number" in ty
    literals = set(re.findall(r"'([^']+)'", ty))
    if isinstance(value, str):
        if literals and not re.search(r"\bstring\b|\bLive[A-Z]\w*\b", ty):
            return value in literals
        if ty.strip() in ("LiveChoice", "LiveChoice | null"):
            return value in ts_alias("LiveChoice")
        if ty.strip() in ("LiveLabel", "LiveLabel | null"):
            return value in ts_alias("LiveLabel")
        return "string" in ty or bool(re.search(r"\bLive[A-Z]\w*\b", ty))
    if isinstance(value, list):
        return "[]" in ty or "Array" in ty
    return ty.startswith("{") or "Record<" in ty or bool(re.search(r"\bLive[A-Z]\w*\b", ty))


def check_shape(obj: Any, name: str, where: str, problems: list[str]) -> None:
    fields = ts_interface(name)
    if not isinstance(obj, dict):
        problems.append(f"{where}: expected an object for {name}, got {type(obj).__name__}")
        return
    for k in sorted(set(obj) - set(fields)):
        problems.append(f"{where}: field {k!r} is not in {name}")
    for k, (ty, optional, keys) in fields.items():
        if k not in obj:
            if not optional:
                problems.append(f"{where}: {name}.{k} is missing")
            continue
        v = obj[k]
        if not type_ok(v, ty, optional):
            problems.append(f"{where}.{k}: {v!r} does not fit {ty}")
        if keys is not None and isinstance(v, dict) and set(v) != set(keys):
            problems.append(f"{where}.{k}: keys {sorted(v)} != {sorted(keys)}")
        child = CHILD.get((name, k))
        if child and v is not None:
            iface, is_list = child
            if is_list:
                for i, item in enumerate(v):
                    check_shape(item, iface, f"{where}.{k}[{i}]", problems)
            else:
                check_shape(v, iface, f"{where}.{k}", problems)


def check_games(body: dict[str, Any], where: str, problems: list[str]) -> None:
    check_shape(body, "LiveGamesResponse", where, problems)


def check_call(body: dict[str, Any], where: str, problems: list[str]) -> None:
    check_shape(body, "LiveCallResponse", where, problems)
    for row in (body.get("third") or {}).get("table") or []:
        if set(row["wp"]) != {"go", "fg", "punt"}:
            problems.append(f"{where}: a table row's wp has {sorted(row['wp'])}")
    options = (body.get("fourth") or {}).get("options")
    if options is not None and [o["choice"] for o in options] != ["go", "fg", "punt"]:
        problems.append(f"{where}: the options are not go, fg, punt in order")


def check_context(body: dict[str, Any], where: str, problems: list[str]) -> None:
    check_shape(body, "LiveContextResponse", where, problems)
    team = body["team"]
    for key, iface in (("kicker", "kicker"), ("punter", "punter")):
        block = team.get(key)
        if block is not None:
            wanted = ts_interface("LiveContextTeam")[iface][2]
            if set(block) != set(wanted):
                problems.append(f"{where}.team.{key}: {sorted(block)} != {sorted(wanted)}")
    for band in (team.get("kicker") or {}).get("bands", []):
        check_shape(band, "LiveKickerBand", f"{where}.team.kicker.bands", problems)
    if team.get("punter"):
        for k in ("this", "last", "league_this", "league_last"):
            check_shape(team["punter"][k], "LivePunting", f"{where}.team.punter.{k}", problems)
    wanted = ts_interface("LiveContextTeam")["coach_go_by_distance"][2]
    for row in team.get("coach_go_by_distance", []):
        if set(row) != set(wanted):
            problems.append(f"{where}.team.coach_go_by_distance: {sorted(row)} != {sorted(wanted)}")
        for k in ("this", "last", "league_last"):
            check_shape(row[k], "LiveStat", f"{where}.team.coach_go_by_distance.{k}", problems)


class RealShapeCache(T.FakeCache):
    """A team context built by the real `build_context` on the LD02 context fixtures."""

    def __init__(self, data: dict[str, Any]):
        super().__init__()
        self.data = data

    def get(self, season, week, *, paths, engine_factory, model_version=None):
        self.gets.append((season, week))
        return self.data


@pytest.fixture(scope="module")
def real_context(tmp_path_factory):
    from nflengine.live.context import build_context

    root = tmp_path_factory.mktemp("realctx")
    paths = TLC.write_curated(root / "data", TLC.build())
    fixes = root / "fixes.csv"
    fixes.write_text(TLC.FIXES_CSV, encoding="utf-8")
    return build_context(
        2026, 3, paths=paths, engine=TLC.FakeEngine(), model_version="v-test",
        fixes_path=fixes, now=TLC.NOW,
    )  # fmt: skip


def test_the_ts_parser_sees_the_live_types():
    """A guard on the guard: the interfaces this test reads are there."""
    assert {"event", "game_id", "pregame", "decision_down"} <= set(ts_interface("LiveGame"))
    assert ts_alias("LiveChoice") == {"go", "fg", "punt"}
    assert ts_interface("LiveGamesResponse")["live_week"][2] == ["season", "week"]


def test_every_live_answer_matches_the_typescript_types(tmp_path, real_context):
    problems: list[str] = []
    moments = {
        "third": T.THIRD, "fourth": T.FOURTH, "second": T.SECOND, "pre": T.PRE,
        "post": T.POST, "halftime": T.HALFTIME, "after_td": T.AFTER_TD,
    }  # fmt: skip
    for name, at in moments.items():
        w = T.World(tmp_path / name, at)
        w.live._context_cache = RealShapeCache(real_context)  # noqa: SLF001
        check_games(w.games(), f"{name}.games", problems)
        for n in (1, 2):  # the second check is a repeat
            r = w.call()
            assert r.status_code == 200, (name, r.text)
            check_call(r.json(), f"{name}.call{n}", problems)
        r = w.call(LATE)
        assert r.status_code == 200, (name, r.text)
        check_call(r.json(), f"{name}.call(late)", problems)
        if name in ("third", "fourth"):
            r = w.get(f"{GAMES}/{EV}/context?offense=DAL")
            assert r.status_code == 200, r.text
            check_context(r.json(), f"{name}.context", problems)
    # a past and a future week, the schedule's slate
    w = T.World(tmp_path / "weeks", T.THIRD)
    for wk in (4, 6):
        check_games(w.get(f"/api/live/2026/{wk}/games").json(), f"week{wk}", problems)
    # ESPN down: the list with feed_error, a check with a stale answer
    d = T.World(tmp_path / "down", T.FOURTH)
    d.call()
    espn_down(d, 503)
    d.jump(3)
    stale = d.call().json()
    assert stale["feed"]["stale"] is True
    check_call(stale, "stale.call", problems)
    d2 = T.World(tmp_path / "down2", T.FOURTH)
    espn_down(d2)
    listed = d2.games()
    assert listed["feed"] is None and listed["feed_error"]
    check_games(listed, "espn_down.games", problems)
    # a game the schedule doesn't know
    u = T.World(tmp_path / "unknown", T.THIRD)
    drop_tb_at_dal(u)
    check_games(u.games(), "unscheduled.games", problems)
    # the replay's own block and a neutral-site flip with ESPN's win probability
    n = T.World(tmp_path / "wp", T.THIRD)
    with_summary(n, set_summary_wp(fixture("summary_401872980_tb_dal.json"), 0.62))
    check_games(n.games(), "wp.games", problems)
    check_call(n.call().json(), "wp.call", problems)
    assert not problems, "\n".join(problems)
