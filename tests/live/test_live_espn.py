"""LD01: the ESPN client. Only `httpx.MockTransport` and a fake clock: no test reaches the network
(an autouse guard makes httpx's real transport raise). Covers the safety rules of
documentation/live-decisions/README.md §5: one allowed host, 5 s timeout, one request per game
every 2 s (and 0.5 s between any two), back-off 2 -> 60 s, stale answers instead of crashes."""

from __future__ import annotations

import gzip
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from live_fakes import FakeClock, fixture, no_real_network  # noqa: F401  (autouse guard)

from nflengine.live import espn
from nflengine.live.espn import (
    ALLOWED_HOST,
    BASE,
    USER_AGENT,
    EspnClient,
    EspnError,
    Fetched,
    HostNotAllowed,
    check_event_id,
    check_url,
    espn_week,
)

EVENT = "401872980"
GAME_URL = f"{BASE}/scoreboard/{EVENT}"


class Server:
    """A fake ESPN: answers from a script (the last item repeats) and records every request.
    An item is an exception to raise or a function (request -> fresh response)."""

    def __init__(self, *script):
        self.script = list(script)
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item(request)

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.seen]


def ok(data=None):
    body = {"ok": True} if data is None else data
    return lambda request: httpx.Response(200, json=body)


def status(code: int, **kw):
    return lambda request: httpx.Response(code, **kw)


def make(server: Server, clock: FakeClock, **kw) -> EspnClient:
    return EspnClient(
        transport=httpx.MockTransport(server),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now=clock.now,
        **kw,
    )


# --- the allowlist ----------------------------------------------------------------------------
def test_check_url_passes_the_one_allowed_url_shape():
    url = f"{BASE}/scoreboard?dates=2026&seasontype=2&week=5"
    assert check_url(url) == url
    assert ALLOWED_HOST == "site.api.espn.com"
    assert BASE.startswith("https://site.api.espn.com/")


@pytest.mark.parametrize(
    "url",
    [
        "http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",  # not https
        "https://example.com/apis/site/v2/sports/football/nfl/scoreboard",  # another host
        "https://site.api.espn.com:8443/apis/site/v2/sports/football/nfl/scoreboard",  # a port
        "https://user:pw@site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
        "https://site.api.espn.com@evil.example/apis/site/v2/sports/football/nfl/scoreboard",
        "https://site.api.espn.com.evil.example/apis/site/v2/sports/football/nfl/scoreboard",
        "https://evil.example/https://site.api.espn.com/apis/site",
        "https://api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",  # a sibling host
        "//site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",  # no scheme
        "ftp://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
        "site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
        "/apis/site/v2/sports/football/nfl/scoreboard",
        "",
    ],
)
def test_check_url_refuses_every_other_address(url):
    with pytest.raises(HostNotAllowed) as exc:
        check_url(url)
    assert isinstance(exc.value, EspnError)  # callers catch one error type
    assert "evil" not in str(exc.value)  # the refusal message doesn't echo the address back


def test_a_refused_host_never_reaches_the_transport(monkeypatch):
    clock = FakeClock()
    server = Server(ok())
    client = make(server, clock)
    with pytest.raises(HostNotAllowed):
        client._get("event:1", "https://evil.example/apis/site/v2/sports/football/nfl/x", {})
    with pytest.raises(HostNotAllowed):
        client._get("event:1", "http://site.api.espn.com/apis/site/v2/x", {"a": 1})
    # the public calls build their URL from BASE: point it at another host and nothing is sent
    monkeypatch.setattr(espn, "BASE", "https://evil.example/apis/site/v2/sports/football/nfl")
    with pytest.raises(HostNotAllowed):
        client.game(EVENT)
    with pytest.raises(HostNotAllowed):
        client.summary(EVENT)
    with pytest.raises(HostNotAllowed):
        client.scoreboard(2026, 5)
    assert server.seen == []
    assert client.requests == 0
    assert clock.sleeps == []


def test_a_redirect_is_not_followed_even_to_the_allowed_host():
    for code in (301, 302, 307, 308):
        server = Server(status(code, headers={"Location": "https://evil.example/x"}))
        client = make(server, FakeClock())
        with pytest.raises(EspnError) as exc:
            client.game(EVENT)
        assert f"redirect to another address refused (HTTP {code})" in str(exc.value)
        assert server.urls == [GAME_URL]  # one request; the Location was never fetched


def test_a_redirect_after_a_good_answer_gives_the_stale_answer():
    clock = FakeClock()
    good = fixture("event_401872980_tb_dal_final.json")
    server = Server(ok(good), status(302, headers={"Location": "https://evil.example/"}))
    client = make(server, clock)
    first = client.game(EVENT)
    clock.advance(3)
    second = client.game(EVENT)
    assert second.stale and not second.cached
    assert "redirect" in second.error
    assert second.data == first.data
    assert len(server.seen) == 2


# --- event ids and the URLs the client builds --------------------------------------------------
@pytest.mark.parametrize("good", ["401872980", 401872980, " 401872980 ", "1", "123456789012"])
def test_check_event_id_accepts_digits(good):
    assert check_event_id(good) == str(good).strip()


@pytest.mark.parametrize(
    "bad",
    ["", " ", "abc", "40187298a", "../x", "401872980/summary", "401872980?x=1", "-1", "1.5",
     "1234567890123", "4018 72980", None],
)  # fmt: skip
def test_check_event_id_refuses_anything_else(bad):
    with pytest.raises(ValueError):
        check_event_id(bad)


def test_check_event_id_is_ascii_digits_only():
    """Full-width digits (U+FF10..) match `\\d` in Python; event ids are ASCII digits only."""
    with pytest.raises(ValueError):
        check_event_id("４０１８７２９８０")


def test_a_bad_event_id_sends_nothing():
    clock = FakeClock()
    server = Server(ok())
    client = make(server, clock)
    for call in (client.game, client.summary, client.next_allowed_in):
        with pytest.raises(ValueError):
            call("../../up")
    assert server.seen == []


def test_the_three_calls_build_their_urls_from_the_base():
    clock = FakeClock()
    server = Server(ok())
    client = make(server, clock)
    client.scoreboard()
    client.scoreboard(2026, 5)
    client.game(EVENT)
    client.summary(EVENT)
    assert server.urls == [
        f"{BASE}/scoreboard",
        f"{BASE}/scoreboard?dates=2026&seasontype=2&week=5",
        f"{BASE}/scoreboard/{EVENT}",
        f"{BASE}/summary?event={EVENT}",
    ]
    assert {r.method for r in server.seen} == {"GET"}
    assert all(r.content == b"" for r in server.seen)
    assert {r.url.host for r in server.seen} == {ALLOWED_HOST}
    assert {r.url.scheme for r in server.seen} == {"https"}


def test_the_answer_carries_the_url_fetch_time_and_data():
    clock = FakeClock()
    data = {"id": EVENT, "n": [1, 2]}
    client = make(Server(ok(data)), clock)
    got = client.game(EVENT)
    assert isinstance(got, Fetched)
    assert got.data == data
    assert got.url == GAME_URL
    assert got.fetched_at == clock.now()
    assert got.fetched_at.tzinfo is UTC
    assert got.ms >= 0
    assert (got.cached, got.stale, got.error) == (False, False, None)
    assert got.age_s(clock.now() + timedelta(seconds=14)) == 14.0


@pytest.mark.parametrize(
    ("season", "week", "stype", "eweek"),
    [
        (2026, 1, 2, 1),
        (2026, 18, 2, 18),  # the last regular-season week since 2021
        (2026, 19, 3, 1),  # wild card
        (2026, 20, 3, 2),  # divisional
        (2026, 21, 3, 3),  # conference championships
        (2026, 22, 3, 5),  # the Super Bowl (ESPN's week 4 is the Pro Bowl)
        (2021, 19, 3, 1),
        (2021, 22, 3, 5),
        (2020, 17, 2, 17),  # 17 regular-season weeks before 2021
        (2020, 18, 3, 1),
        (2020, 19, 3, 2),
        (2020, 20, 3, 3),
        (2020, 21, 3, 5),
        (2019, 21, 3, 5),
    ],
)
def test_espn_week_maps_nflverse_weeks_to_espns_season_types(season, week, stype, eweek):
    assert espn_week(season, week) == (stype, eweek)


def test_the_scoreboard_url_uses_espns_playoff_numbering():
    clock = FakeClock()
    server = Server(ok())
    client = make(server, clock)
    client.scoreboard(2026, 22)
    client.scoreboard(2020, 18)
    assert server.urls == [
        f"{BASE}/scoreboard?dates=2026&seasontype=3&week=5",
        f"{BASE}/scoreboard?dates=2020&seasontype=3&week=1",
    ]


# --- what is sent ------------------------------------------------------------------------------
def test_the_request_names_the_project_and_asks_for_gzip_without_credentials():
    clock = FakeClock()
    server = Server(ok())
    client = make(server, clock)
    client.game(EVENT)
    headers = server.seen[0].headers
    assert headers["user-agent"] == USER_AGENT
    assert "nfl-analytics-engine" in USER_AGENT
    assert headers["accept-encoding"] == "gzip"
    assert headers["accept"] == "application/json"
    for name in ("authorization", "cookie", "x-api-key", "referer", "proxy-authorization"):
        assert name not in headers


def test_a_gzip_answer_is_decoded():
    body = gzip.compress(json.dumps({"events": [{"id": EVENT}]}).encode())

    def gzipped(request):
        return httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(body)
        )

    client = make(Server(gzipped), FakeClock())
    assert client.scoreboard().data == {"events": [{"id": EVENT}]}


def test_the_documented_limits_are_the_defaults():
    client = EspnClient(transport=httpx.MockTransport(Server(ok())))
    assert (client.timeout, client.min_interval, client.host_interval) == (5.0, 2.0, 0.5)
    assert (client.backoff_start, client.backoff_max) == (2.0, 60.0)
    assert client._http.timeout == httpx.Timeout(5.0)
    assert client._http.follow_redirects is False


# --- the 2 s clock per game and the 0.5 s between any two requests ------------------------------
def test_a_summary_right_after_a_game_check_waits_out_their_shared_2_seconds():
    clock = FakeClock()
    server = Server(ok())
    client = make(server, clock)
    client.game(EVENT)
    assert clock.sleeps == []  # the first request goes out at once
    client.summary(EVENT)  # another URL, so no in-memory answer: it has to wait
    assert clock.sleeps == [2.0]
    assert server.urls == [GAME_URL, f"{BASE}/summary?event={EVENT}"]
    assert clock.t == 1002.0
    client.game(EVENT)  # the summary reset the shared clock: the game call waits again
    assert clock.sleeps == [2.0, 2.0]
    assert len(server.seen) == 3


def test_a_different_game_waits_only_the_half_second_between_requests():
    clock = FakeClock()
    server = Server(ok())
    client = make(server, clock)
    client.game("1")
    client.game("2")
    assert clock.sleeps == [0.5]
    client.game("3")
    assert clock.sleeps == [0.5, 0.5]
    clock.advance(0.25)
    client.game("4")
    assert clock.sleeps == [0.5, 0.5, 0.25]  # only what is left of the half second
    clock.advance(1.0)
    client.game("5")
    assert clock.sleeps == [0.5, 0.5, 0.25]  # a long enough gap needs no wait
    assert len(server.seen) == 5


def test_the_scoreboard_has_no_2_second_clock_shared_with_other_weeks():
    clock = FakeClock()
    client = make(Server(ok()), clock)
    client.scoreboard(2026, 4)
    client.scoreboard(2026, 5)  # a different URL and clock key: only the host interval
    assert clock.sleeps == [0.5]


def test_an_answer_younger_than_2_seconds_comes_from_memory_without_a_request():
    clock = FakeClock()
    server = Server(ok({"n": 1}), ok({"n": 2}))
    client = make(server, clock)
    first = client.game(EVENT)
    clock.advance(1.75)
    again = client.game(EVENT)
    assert again.cached and not again.stale and again.error is None
    assert again.data == {"n": 1}
    assert again.fetched_at == first.fetched_at  # still the original fetch time
    assert len(server.seen) == 1
    assert clock.sleeps == []
    clock.advance(0.25)  # now exactly 2.0 s after the request
    fresh = client.game(EVENT)
    assert not fresh.cached
    assert fresh.data == {"n": 2}
    assert len(server.seen) == 2
    assert clock.sleeps == []


def test_next_allowed_in_counts_down_the_per_game_clock():
    clock = FakeClock()
    client = make(Server(ok()), clock)
    assert client.next_allowed_in(EVENT) == 0.0
    client.game(EVENT)
    assert client.next_allowed_in(EVENT) == 2.0
    clock.advance(0.5)
    assert client.next_allowed_in(EVENT) == 1.5
    assert client.next_allowed_in("999") == 0.0  # another game is free
    client.summary(EVENT)  # shares the clock; it slept the rest out and restarted it
    assert client.next_allowed_in(EVENT) == 2.0
    clock.advance(5)
    assert client.next_allowed_in(EVENT) == 0.0


def test_threads_share_one_clock_and_one_memory():
    server = Server(ok({"n": 1}))
    client = make(server, FakeClock())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: client.game(EVENT), range(16)))
    assert len(server.seen) == 1  # the lock serialises the calls; the rest are memory hits
    assert sum(1 for r in results if not r.cached) == 1
    assert all(r.data == {"n": 1} for r in results)


# --- failures: back-off, stale answers, readable errors ------------------------------------------
def test_back_off_doubles_from_2_to_60_seconds_and_a_success_resets_it():
    clock = FakeClock()
    server = Server(status(500))
    client = make(server, clock)
    delays = []
    for _ in range(8):
        with pytest.raises(EspnError):
            client.game(EVENT)
        delays.append(client.backing_off_for())
        clock.advance(client.backing_off_for())  # wait exactly the back-off out
    assert delays == [2, 4, 8, 16, 32, 60, 60, 60]
    assert len(server.seen) == 8
    server.script = [ok()]  # ESPN recovers
    client.game(EVENT)
    assert client.backing_off_for() == 0.0
    server.script = [status(500)]
    clock.advance(5)
    again = client.game(EVENT)  # a failure after a good answer: stale, and the back-off restarts
    assert again.stale
    assert client.backing_off_for() == 2.0


def test_back_off_survives_a_very_long_outage():
    """Sol review: 2.0 ** 1024 overflowed after ~1,025 failures in a row (about 17 hours of a
    dead feed); the client must keep backing off 60 s and keep serving errors, not crash."""
    clock = FakeClock()
    client = make(Server(status(500)), clock)
    client._fails = 5000  # as if the feed had been down for days
    with pytest.raises(EspnError):
        client.game(EVENT)
    assert client.backing_off_for() == 60.0


def test_no_request_goes_out_while_backing_off():
    clock = FakeClock()
    server = Server(status(503))
    client = make(server, clock)
    with pytest.raises(EspnError) as first:
        client.game(EVENT)
    assert "HTTP 503" in str(first.value)
    clock.advance(1.0)
    with pytest.raises(EspnError) as waiting:
        client.game(EVENT)
    assert "backing off" in str(waiting.value)
    assert "next try in 1 s" in str(waiting.value)
    assert "HTTP 503" in str(waiting.value)  # and it says why
    with pytest.raises(EspnError):
        client.summary(EVENT)  # any call is held back, not only the failing one
    with pytest.raises(EspnError):
        client.game("999")
    assert len(server.seen) == 1
    clock.advance(1.0)  # 2 s after the failure
    server.script = [ok()]
    assert client.game(EVENT).data == {"ok": True}
    assert len(server.seen) == 2


def test_after_a_good_answer_a_failure_gives_the_old_answer_marked_stale_with_the_reason():
    clock = FakeClock()
    good = fixture("event_401872973_ten_bal_4th_and_10.json")
    server = Server(ok(good), status(500))
    client = make(server, clock)
    first = client.game(EVENT)
    clock.advance(14)
    stale = client.game(EVENT)
    assert stale.stale is True
    assert stale.cached is False
    assert stale.error == "HTTP 500"
    assert stale.data == good
    assert stale.fetched_at == first.fetched_at  # "last good state 14 s ago"
    assert stale.age_s(clock.now()) == 14.0
    clock.advance(0.5)
    held = client.game(EVENT)  # inside the back-off window: stale again, with no request
    assert held.stale
    assert held.error.startswith("backing off after an error, next try in")
    assert "HTTP 500" in held.error
    assert held.data == good
    assert len(server.seen) == 2
    clock.advance(10)
    server.script = [ok({"fresh": 1})]
    back = client.game(EVENT)
    assert (back.stale, back.cached, back.error) == (False, False, None)
    assert back.data == {"fresh": 1}


def test_only_the_failing_url_goes_stale():
    clock = FakeClock()
    server = Server(ok({"game": 1}), status(500))
    client = make(server, clock)
    client.game(EVENT)
    clock.advance(3)
    with pytest.raises(EspnError):  # the summary was never fetched: nothing to fall back to
        client.summary(EVENT)


@pytest.mark.parametrize(
    ("script", "message"),
    [
        (status(404), "HTTP 404"),
        (status(429), "HTTP 429"),
        (status(500), "HTTP 500"),
        (status(200, text="<html>maintenance</html>"), "not JSON"),
        (status(200, content=b""), "not JSON"),
        (status(200, json=[1, 2, 3]), "not a JSON object"),
        (status(200, json="text"), "not a JSON object"),
        (status(200, content=b"null"), "not a JSON object"),
        (httpx.ReadTimeout("slow"), "no answer within 5 s"),
        (httpx.ConnectTimeout("slow"), "no answer within 5 s"),
        (httpx.ConnectError("refused"), "connection failed (ConnectError)"),
        (httpx.RemoteProtocolError("bad"), "connection failed (RemoteProtocolError)"),
    ],
)
def test_without_an_earlier_answer_a_failure_raises_a_short_readable_error(script, message):
    client = make(Server(script), FakeClock())
    with pytest.raises(EspnError) as exc:
        client.game(EVENT)
    assert str(exc.value) == f"ESPN didn't answer ({message})"
    assert "https" not in str(exc.value)  # nothing but the reason


def test_the_timeout_message_follows_the_configured_timeout():
    client = make(Server(httpx.ReadTimeout("slow")), FakeClock(), timeout=3.0)
    with pytest.raises(EspnError, match=r"no answer within 3 s"):
        client.game(EVENT)


def test_an_unexpected_programming_error_is_not_swallowed():
    client = make(Server(RuntimeError("bug")), FakeClock())
    with pytest.raises(RuntimeError, match="bug"):
        client.game(EVENT)


def test_a_failed_request_still_counts_against_the_per_game_clock():
    server = Server(status(500), ok())
    client = make(server, FakeClock())
    with pytest.raises(EspnError):
        client.game(EVENT)
    assert client.next_allowed_in(EVENT) == 2.0
    assert client.requests == 1


def test_the_default_client_cannot_reach_the_network_in_tests():
    client = EspnClient()  # no fake transport: the guard in live_fakes stops the real one
    try:
        with pytest.raises(AssertionError, match="reach the network"):
            client.game(EVENT)
    finally:
        client.close()


# --- housekeeping -----------------------------------------------------------------------------
def test_the_client_is_a_context_manager_that_closes_its_connection():
    with make(Server(ok()), FakeClock()) as client:
        assert not client._http.is_closed
        client.game(EVENT)
    assert client._http.is_closed


def test_age_is_measured_from_the_fetch_time():
    got = Fetched(
        data={}, url=GAME_URL, fetched_at=datetime(2020, 1, 1, 12, 0, 0, tzinfo=UTC), ms=1.0
    )
    assert got.age_s(datetime(2020, 1, 1, 12, 0, 30, tzinfo=UTC)) == 30.0
    assert got.age_s() > 0  # against the real clock when none is given
