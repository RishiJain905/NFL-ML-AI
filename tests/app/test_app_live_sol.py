"""LD02 regressions from Sol's review of the Game day backend.

(1) Malformed ESPN numbers and teams never become a 500 (an overflowing score, a live situation
with only one team). (2) The ESPN client's spacing rules hold between real threads when a waiter
wakes up late, when another request fails while it waits, and when one game's wait would delay
another game. (3) A second caller for a URL already on its way shares that answer or failure,
however long ESPN takes. (6) The replay feed shows the timeouts ESPN has posted, not the ones the
next snap will have used.

Real threads and a real clock where the rule is about time (small intervals, 0.5-1.5 s each);
the TB at DAL scenes of `test_app_live.World` otherwise. Nothing reaches the network.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "live"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_app_live as T  # noqa: E402  (the scenes: World and the replay moments)
from live_fakes import FakeClock, ctx, fixture, no_real_network  # noqa: E402, F401 (autouse guard)

from nflengine.live.espn import EspnClient, EspnError  # noqa: E402
from nflengine.live.replayfeed import ReplayFeed  # noqa: E402
from nflengine.live.state import competition, parse_event  # noqa: E402

EV = T.EV
GAMES = "/api/live/2026/5/games"
UNEXPECTED = "ESPN sent something unexpected for this game. Try again shortly."


# --- helpers -----------------------------------------------------------------------------------
def events_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    return data.get("events") or [data]


def poison_raw(w: T.World, mutate, only=lambda url: "/scoreboard" in url) -> None:
    """Edit ESPN's scoreboard answers; the JSON may carry `Infinity` / `NaN` (Python's json
    reads and writes them, as ESPN-side bugs might)."""
    inner = w.feed.transport()

    def handle(request: httpx.Request) -> httpx.Response:
        w.urls.append(str(request.url))
        resp = inner.handle_request(request)
        if not only(str(request.url)):
            return resp
        data = mutate(json.loads(resp.content))
        return httpx.Response(
            resp.status_code,
            content=json.dumps(data).encode(),
            headers={"content-type": "application/json"},
        )

    w.client._http._transport = httpx.MockTransport(handle)  # noqa: SLF001


def not_a_500(r: httpx.Response, what: str) -> None:
    assert r.status_code in (200, 502), (what, r.status_code, r.text)
    if r.status_code == 502:
        err = r.json()["error"]
        assert (err["code"], err["message"]) == ("espn_unexpected", UNEXPECTED), (what, err)


# --- (1) malformed numbers and teams -------------------------------------------------------------
@pytest.mark.parametrize(
    "score",
    ["1e309", "-1e309", "inf", "nan", float("inf"), float("-inf"), float("nan")],
    ids=["1e309", "-1e309", "inf-str", "nan-str", "inf", "-inf", "nan"],
)
def test_an_overflowing_score_is_read_as_zero_never_a_500(tmp_path, score):
    w = T.World(tmp_path, T.FOURTH)  # 7-7 in the 2nd quarter

    def mutate(data):
        for ev in events_of(data):
            for c in ev["competitions"][0]["competitors"]:
                if c["homeAway"] == "home":
                    c["score"] = score
        return data

    poison_raw(w, mutate)
    listed = w.get(GAMES)
    not_a_500(listed, "list")
    if listed.status_code == 200:
        g = next((g for g in listed.json()["games"] if g["event"] == EV), None)
        if g is not None:  # (the game may also be skipped)
            assert g["home_score"] == 0 and g["away_score"] == 7
    checked = w.call()
    not_a_500(checked, "check")
    if checked.status_code == 200:
        game = checked.json()["game"]
        assert game["home_score"] == 0 and game["away_score"] == 7


FIELDS = {
    "period": lambda comp, sit: comp["status"].__setitem__("period", float("inf")),
    "clock": lambda comp, sit: comp["status"].__setitem__("clock", float("inf")),
    "down": lambda comp, sit: sit and sit.__setitem__("down", float("inf")),
    "distance": lambda comp, sit: sit and sit.__setitem__("distance", float("inf")),
    "yard_line": lambda comp, sit: sit and sit.__setitem__("yardLine", float("inf")),
    "home_timeouts": lambda comp, sit: sit and sit.__setitem__("homeTimeouts", float("inf")),
    "away_timeouts": lambda comp, sit: sit and sit.__setitem__("awayTimeouts", float("-inf")),
    "last_play_probability": lambda comp, sit: (
        sit and sit["lastPlay"].__setitem__("probability", {"homeWinPercentage": float("inf")})
    ),
}


@pytest.mark.parametrize("field", sorted(FIELDS))
def test_an_infinite_number_anywhere_in_a_live_situation_is_200_or_502(tmp_path, field):
    w = T.World(tmp_path, T.THIRD)

    def mutate(data):
        for ev in events_of(data):
            comp = ev["competitions"][0]
            FIELDS[field](comp, comp.get("situation"))
        return data

    poison_raw(w, mutate)
    not_a_500(w.get(GAMES), f"{field}: list")
    not_a_500(w.call(), f"{field}: check")


def keep_only_the_team_with_the_ball(data):
    for ev in events_of(data):
        comp = ev["competitions"][0]
        sit = comp.get("situation")
        if sit and sit.get("possession"):
            comp["competitors"] = [
                c for c in comp["competitors"] if str(c["id"]) == str(sit["possession"])
            ]
    return data


def test_a_situation_with_one_team_is_a_reason_not_a_500(tmp_path):
    w = T.World(tmp_path, T.THIRD)  # DAL has the ball, 3rd & 2
    poison_raw(w, keep_only_the_team_with_the_ball)
    listed = w.get(GAMES)
    assert listed.status_code == 200, listed.text  # the list still answers
    checked = w.call()
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["kind"] == "none" and body["fourth"] is None and body["third"] is None
    assert "missing a team" in body["reason"].lower(), body["reason"]
    assert body["offense"] is None or body["offense"] == "DAL"


def test_a_situation_with_one_team_in_the_first_half_checks_too(tmp_path):
    """A repeat of the check (the play log is fetched once, the opening kickoff is known)."""
    w = T.World(tmp_path, T.THIRD)
    poison_raw(w, keep_only_the_team_with_the_ball)
    for _ in range(2):
        r = w.call()
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "none"
        w.jump(3)


@pytest.mark.parametrize(
    "boom", [OverflowError, StopIteration, ValueError, TypeError, KeyError, AttributeError]
)
def test_any_of_these_errors_while_parsing_is_a_502_with_the_fixed_text(
    tmp_path, monkeypatch, boom
):
    """Whatever the parser trips on, the list and the check answer 502 `espn_unexpected` and
    say nothing about the error."""
    import nflengine.app.readers.live as live_mod

    w = T.World(tmp_path, T.THIRD)

    def trip(*args, **kwargs):
        raise boom("secret detail 4242")

    monkeypatch.setattr(live_mod, "parse_event", trip)
    for r in (w.get(GAMES), w.call()):
        assert r.status_code == 502, r.text
        err = r.json()["error"]
        assert (err["code"], err["message"]) == ("espn_unexpected", UNEXPECTED)
        assert "4242" not in r.text and boom.__name__ not in r.text


# --- (2) the client's spacing, measured from the sends that really happened ----------------------
class Recorder:
    """A fake ESPN on the real clock: when each request really went out, and a plan for what it
    answers: `plan(url, n)` -> (seconds to take, HTTP status) for the n-th request of that URL."""

    def __init__(self, plan=lambda url, n: (0.0, 200)):
        self.plan = plan
        self.sent: list[tuple[float, str]] = []
        self._count: dict[str, int] = {}
        self._lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        with self._lock:
            self.sent.append((time.monotonic(), url))
            n = self._count[url] = self._count.get(url, 0) + 1
        delay, status = self.plan(url, n)
        if delay:
            time.sleep(delay)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"url": url})

    def times(self, part: str) -> list[float]:
        return [t for t, u in self.sent if part in u]


def client_on(rec: Recorder, **kw) -> EspnClient:
    return EspnClient(transport=httpx.MockTransport(rec), **kw)


def in_thread(fn, *args) -> tuple[threading.Thread, dict[str, Any]]:
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["ok"] = fn(*args)
        except BaseException as e:  # noqa: BLE001
            box["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, box


def test_a_request_waiting_out_the_rule_does_not_send_once_back_off_began():
    rec = Recorder(lambda url, n: (0.0, 500) if "/scoreboard/222" in url else (0.0, 200))
    client = client_on(rec, min_interval=0.6, host_interval=0.05)
    client.game("111")  # t = 0
    waiter, box = in_thread(client.summary, "111")  # must wait ~0.6 s for game 111's clock
    time.sleep(0.2)
    with pytest.raises(EspnError):
        client.game("222")  # ESPN fails on another game: back-off begins
    waiter.join(5)
    assert isinstance(box.get("err"), EspnError), box
    assert rec.times("/summary") == [], "the waiter woke into a back-off and did not send"
    assert client.requests == 2
    client.close()


def test_a_waiter_woken_into_a_back_off_gets_its_stale_answer():
    rec = Recorder(lambda url, n: (0.0, 500) if "/scoreboard/222" in url else (0.0, 200))
    client = client_on(rec, min_interval=0.5, host_interval=0.05)
    first = client.game("111")  # t = 0: this answer will be the stale one
    client.summary("111")  # waits for the clock: sent at ~0.5 s
    waiter, box = in_thread(client.game, "111")  # its answer is old now; the clock isn't ready
    time.sleep(0.15)
    with pytest.raises(EspnError):
        client.game("222")  # fails while the waiter sleeps
    waiter.join(5)
    got = box["ok"]
    assert got.stale is True and got.cached is False
    assert got.data == first.data and "HTTP 500" in (got.error or "")
    assert len(rec.times("/scoreboard/111")) == 1, "no second request for game 111"
    client.close()


def test_a_late_waker_cannot_collapse_the_spacing_between_two_real_sends():
    rec = Recorder()
    once = threading.Event()

    def clumsy_sleep(seconds: float) -> None:  # the first sleeper oversleeps by 0.6 s
        extra = 0.0 if once.is_set() else 0.6
        once.set()
        time.sleep(seconds + extra)

    client = client_on(rec, min_interval=0.5, host_interval=0.5, sleep=clumsy_sleep)
    client.game("111")  # t = 0
    jobs = [in_thread(client.summary, "111"), in_thread(client.game, "222")]
    for t, _ in jobs:
        t.join(10)
    assert all("ok" in box for _, box in jobs), [box for _, box in jobs]
    times = sorted(t for t, _ in rec.sent)
    assert len(times) == 3
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert all(g >= 0.5 * 0.9 for g in gaps), f"real sends only {gaps} s apart"
    client.close()


def test_one_games_wait_doesnt_push_another_games_first_request_back():
    rec = Recorder()
    client = client_on(rec, min_interval=1.0, host_interval=0.5)
    client.game("111")  # t = 0
    waiter, box = in_thread(client.summary, "111")  # waits until ~1.0 s for game 111's clock
    time.sleep(0.1)
    t0 = time.perf_counter()
    client.game("222")  # a different game: only the 0.5 s host spacing applies
    asked_for = time.perf_counter() - t0
    waiter.join(5)
    assert "ok" in box
    a, b = rec.times("/scoreboard/111")[0], rec.times("/scoreboard/222")[0]
    s = rec.times("/summary")[0]
    assert 0.45 <= b - a <= 0.85, f"game 222 went out {b - a:.2f} s after game 111"
    assert asked_for < 0.9, f"game 222 waited {asked_for:.2f} s"
    assert s - a >= 0.95, "the summary still waits out game 111's own clock"
    assert s - b >= 0.45, "and keeps the host spacing from game 222's real send"
    client.close()


# --- (3) a request in flight is shared, however long ESPN takes ----------------------------------
def test_a_slow_answer_is_shared_even_past_the_2_s_rule():
    """The real numbers: min_interval 2 s and an answer that takes 2.5 s. The old code started
    a second request because the first was already 'old' when it finished."""
    rec = Recorder(lambda url, n: (2.5, 200))
    client = client_on(rec)
    first, a = in_thread(client.game, "111")
    time.sleep(0.2)
    second, b = in_thread(client.game, "111")
    first.join(10)
    second.join(10)
    assert len(rec.sent) == 1 and client.requests == 1, "no second request"
    assert a["ok"].data == b["ok"].data and a["ok"].fetched_at == b["ok"].fetched_at
    assert sorted([a["ok"].cached, b["ok"].cached]) == [False, True]
    assert b["ok"].cached is True and b["ok"].stale is False
    client.close()


def test_three_waiters_share_one_slow_answer():
    rec = Recorder(lambda url, n: (0.9, 200))
    client = client_on(rec, min_interval=0.4)
    jobs = []
    for _ in range(3):
        jobs.append(in_thread(client.game, "111"))
        time.sleep(0.1)
    for t, _ in jobs:
        t.join(10)
    assert client.requests == 1
    assert sorted(box["ok"].cached for _, box in jobs) == [False, True, True]
    client.close()


def test_an_in_flight_failure_is_shared_too_without_an_earlier_answer():
    rec = Recorder(lambda url, n: (0.5, 500))
    client = client_on(rec, min_interval=0.3)
    first, a = in_thread(client.game, "111")
    time.sleep(0.1)
    second, b = in_thread(client.game, "111")
    first.join(10)
    second.join(10)
    assert isinstance(a.get("err"), EspnError) and isinstance(b.get("err"), EspnError)
    assert len(rec.sent) == 1 and client.requests == 1, "the waiter didn't try again"
    client.close()


def test_an_in_flight_failure_is_shared_with_the_stale_answer():
    rec = Recorder(lambda url, n: (0.0, 200) if n == 1 else (0.6, 500))
    client = client_on(rec, min_interval=0.3)
    earlier = client.game("111")
    time.sleep(0.35)  # that answer is old: the next call sends again
    first, a = in_thread(client.game, "111")
    time.sleep(0.1)
    second, b = in_thread(client.game, "111")
    first.join(10)
    second.join(10)
    for box in (a, b):
        got = box["ok"]
        assert got.stale is True and got.data == earlier.data and "HTTP 500" in (got.error or "")
    assert client.requests == 2, "one good request, one failing request shared by both"
    client.close()


# --- (6) the replay shows the timeouts ESPN has posted ------------------------------------------
def tb_dal_feed(lag: float = 10.0) -> ReplayFeed:
    return ReplayFeed(
        {EV: fixture("summary_401872980_tb_dal.json")},
        event=EV,
        at=datetime(2026, 10, 9, 0, 0, tzinfo=UTC),
        lag_s=lag,
        monotonic=FakeClock().monotonic,
    )


def oct9(clock: str) -> datetime:
    h, m, s = (int(x) for x in clock.split(":"))
    return datetime(2026, 10, 9, h, m, s, tzinfo=UTC)


def timeouts_at(feed: ReplayFeed, clock: str) -> tuple[int, int]:
    sit = competition(feed.event_json(EV, oct9(clock)))["situation"]
    return sit["homeTimeouts"], sit["awayTimeouts"]


# DAL (ESPN's home team) calls Timeout #1 at 00:39:05, #2 at 01:03:06, #3 at 01:38:38 (the 1st
# half) and two in the 4th quarter (03:15:58 and 03:17:10); TB calls one (02:48:34). With a lag
# of 10 s each shows up 10 s later.
TIMEOUT_MOMENTS = [
    ("00:38:45", (3, 3), "before DAL's first timeout is even called"),
    ("00:39:14", (3, 3), "called at 00:39:05, ESPN hasn't posted it yet"),
    ("00:39:16", (2, 3), "posted"),
    ("01:03:10", (2, 3), "the second, not posted yet"),
    ("01:03:20", (1, 3), "posted"),
    ("01:38:44", (1, 3), "the third, not posted yet"),
    ("01:38:50", (0, 3), "posted"),
    ("01:54:08", (3, 3), "the second half's kickoff has posted: both teams back to 3"),
    ("02:48:40", (3, 3), "TB's timeout, not posted yet"),
    ("02:48:50", (3, 2), "TB's timeout posted"),
    ("03:16:03", (3, 2), "DAL's first in the 4th quarter, not posted yet"),
    ("03:16:10", (2, 2), "posted"),
    ("03:17:15", (2, 2), "its second, not posted yet"),
    ("03:17:22", (1, 2), "posted"),
]


@pytest.mark.parametrize(
    ("clock", "want", "why"), TIMEOUT_MOMENTS, ids=[m[0] for m in TIMEOUT_MOMENTS]
)
def test_timeouts_are_the_ones_espn_has_posted(clock, want, why):
    assert timeouts_at(tb_dal_feed(), clock) == want, why


def test_a_longer_lag_holds_a_timeout_back_longer():
    feed = tb_dal_feed(lag=40.0)
    assert timeouts_at(feed, "00:39:30") == (3, 3), "40 s lag: posted at 00:39:45"
    assert timeouts_at(feed, "00:39:50") == (2, 3)


def test_the_live_parser_reads_the_same_counts():
    feed = tb_dal_feed()
    game = ctx("tb_dal")
    before = parse_event(feed.event_json(EV, oct9("00:38:45")), game)
    after = parse_event(feed.event_json(EV, oct9("00:39:16")), game)
    assert (before.home_timeouts, before.away_timeouts) == (3, 3)
    assert (after.home_timeouts, after.away_timeouts) == (2, 3)


def test_the_list_shows_a_timeout_only_once_espn_has_posted_it(tmp_path):
    w = T.World(tmp_path, oct9("00:38:45"))
    g = w.games()["games"][0]
    assert (g["event"], g["state"]) == (EV, "in")
    assert (g["home_timeouts"], g["away_timeouts"]) == (3, 3)
    w.jump(31)  # 00:39:16: the board is over 30 s old and the timeout has posted
    g = w.games()["games"][0]
    assert (g["home_timeouts"], g["away_timeouts"]) == (2, 3)
