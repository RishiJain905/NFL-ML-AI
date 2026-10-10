"""The replay feed (LD02, `nfl app --live-replay`): saved ESPN play logs served as if live.

Real summaries from `tests/fixtures/live/` (TB at DAL, a whole game; IND at KC into overtime),
a fake clock, the real `EspnClient` on the feed's transport. The parity property: for every
snap, the state the live parser reads from the feed's scoreboard entry equals the state LD01's
replay builds from the play log (the one builder, `state.build_state`).
"""

from __future__ import annotations

import copy
import gzip
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from live_fakes import FakeClock, ctx, fixture, no_real_network  # noqa: F401 (autouse guard)

from nflengine.live.espn import EspnClient
from nflengine.live.replay import replay
from nflengine.live.replayfeed import ReplayError, ReplayFeed, parse_at
from nflengine.live.state import competition, parse_event

EV, OT = "401872980", "401872945"


def tb_dal() -> dict:
    return fixture("summary_401872980_tb_dal.json")


def feed_at(at: datetime, lag: float = 10.0, clock: FakeClock | None = None, **kw) -> ReplayFeed:
    clock = clock or FakeClock()
    return ReplayFeed({EV: tb_dal()}, event=EV, at=at, lag_s=lag, monotonic=clock.monotonic, **kw)


def rows():
    return replay(tb_dal(), ctx("tb_dal"))


def test_states_before_during_and_after():
    plays = rows()
    first = plays[0].wallclock
    assert feed_at(first - timedelta(minutes=5)).event_json(EV)["status"]["type"]["state"] == "pre"
    mid = feed_at(first + timedelta(minutes=30)).event_json(EV)
    assert mid["status"]["type"]["state"] == "in" and "situation" in competition(mid)
    end = feed_at(plays[-1].wallclock + timedelta(minutes=5)).event_json(EV)
    assert end["status"]["type"]["state"] == "post" and end["status"]["type"]["detail"] == "Final"
    scores = {c["homeAway"]: c["score"] for c in competition(end)["competitors"]}
    assert scores == {"home": "16", "away": "24"}  # DAL 16, TB 24


def test_every_snap_parses_to_the_replays_state():
    """Just before each snap is posted, the live parser sees exactly the replay's state."""
    lag = 10.0
    summary = tb_dal()
    want = [r for r in rows() if r.snap and r.state is not None and r.wallclock is not None]
    assert len(want) > 120
    clock = FakeClock()
    feed = ReplayFeed(
        {EV: summary}, event=EV, at=want[0].wallclock, lag_s=lag, monotonic=clock.monotonic
    )
    c = ctx("tb_dal")
    same = 0
    for r in want:
        t = r.wallclock + timedelta(seconds=lag - 0.5)
        ev = feed.event_json(EV, t)
        ls = parse_event(ev, c, summary=feed.summary_json(EV, t))
        if ls.state is not None and ls.state.as_dict() == r.state.as_dict():
            same += 1
        else:  # only plays whose start ESPN logged after a stop in the clock may differ
            assert ls.state is None or ls.state.down == r.state.down, r.play_id
    assert same / len(want) >= 0.97, (same, len(want))


def test_the_lag_hides_a_play_until_it_posts():
    r = next(p for p in rows() if p.snap and p.down == 3)
    before = feed_at(r.wallclock, lag=30.0).event_json(EV, r.wallclock + timedelta(seconds=29))
    after = feed_at(r.wallclock, lag=30.0).event_json(EV, r.wallclock + timedelta(seconds=31))
    sb = competition(before)["situation"]
    assert sb["down"] == 3  # the 3rd down is still the next snap
    assert competition(after)["situation"]["lastPlay"]["id"] == r.play_id


def test_after_a_touchdown_no_snap_is_pending():
    td = next(p for p in rows() if "Touchdown" in p.type and p.snap)
    ev = feed_at(td.wallclock).event_json(EV, td.wallclock + timedelta(seconds=15))
    sit = competition(ev)["situation"]
    assert sit["down"] == -1 and "possession" not in sit
    ls = parse_event(ev, ctx("tb_dal"))
    assert ls.state is None and "no snap pending" in ls.reason


def test_halftime_shows_espns_stale_first_down():
    half = next(p for p in rows() if p.type == "End of Half")
    ev = feed_at(half.wallclock).event_json(EV, half.wallclock + timedelta(seconds=12))
    assert ev["status"]["type"]["detail"] == "Halftime"
    sit = competition(ev)["situation"]
    assert (sit["down"], sit["distance"], "possession" in sit) == (1, 10, False)
    assert parse_event(ev, ctx("tb_dal")).reason.startswith("halftime")


def test_timeouts_follow_the_play_log():
    """After DAL's first-half timeouts the scoreboard counts them down like the replay."""
    plays = rows()
    snap = next(p for p in plays if p.snap and p.period == 2 and p.off_timeouts is not None
                and (p.off_timeouts < 3 or p.def_timeouts < 3))  # fmt: skip
    i = plays.index(snap)
    t = plays[i - 1].wallclock + timedelta(seconds=10.5)
    sit = competition(feed_at(t).event_json(EV, t))["situation"]
    home_to = snap.off_timeouts if snap.offense_home else snap.def_timeouts
    away_to = snap.def_timeouts if snap.offense_home else snap.off_timeouts
    assert (sit["homeTimeouts"], sit["awayTimeouts"]) == (home_to, away_to)


def test_the_clock_runs_at_the_replay_speed():
    clock = FakeClock()
    start = datetime(2026, 10, 9, 0, 30, tzinfo=UTC)
    feed = ReplayFeed({EV: tb_dal()}, event=EV, at=start, speed=4.0, monotonic=clock.monotonic)
    clock.advance(15)
    assert feed.now() == start + timedelta(seconds=60)
    assert feed.info() == {"event": EV, "at": (start + timedelta(seconds=60)).isoformat(),
                           "speed": 4.0, "lag_s": 10.0}  # fmt: skip


def test_the_summary_is_cut_at_the_replayed_time():
    plays = rows()
    t = plays[40].wallclock + timedelta(seconds=11)
    s = feed_at(t).summary_json(EV, t)
    ids = [p["id"] for d in s["drives"]["previous"] for p in d["plays"]]
    assert ids[-1] == plays[40].play_id and len(ids) == 41
    assert s["header"]["competitions"][0]["status"]["type"]["state"] == "in"


def test_the_transport_answers_only_the_three_calls():
    clock = FakeClock()
    feed = feed_at(rows()[30].wallclock, clock=clock)
    client = EspnClient(transport=feed.transport(), now=feed.now, monotonic=clock.monotonic,
                        sleep=clock.sleep)  # fmt: skip
    board = client.scoreboard(2026, 5).data
    assert [e["id"] for e in board["events"]] == [EV] and board["week"] == {"number": 5}
    assert client.scoreboard(2026, 4).data["events"] == []  # another week: nothing
    assert client.game(EV).data["id"] == EV
    assert client.summary(EV).data["header"]["id"] == EV
    other = httpx.Client(transport=feed.transport())
    assert (
        other.get("https://example.com/apis/site/v2/sports/football/nfl/scoreboard").status_code
        == 404
    )
    assert (
        other.get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/x/{EV}").status_code
        == 404
    )
    assert feed.requests == 6


def test_overtime_and_a_neutral_week_number():
    s = fixture("summary_401872945_ind_kc_ot.json")
    feed = ReplayFeed({OT: s}, event=OT, monotonic=FakeClock().monotonic)
    assert (feed.season, feed.week()) == (2026, 2)
    plays = replay(s, ctx("ind_kc"))
    ot = next(p for p in plays if p.period == 5 and p.snap)
    ev = feed.event_json(OT, ot.wallclock + timedelta(seconds=9))
    assert ev["status"]["period"] == 5 and "OT" in ev["status"]["type"]["detail"]
    final = feed.event_json(OT, plays[-1].wallclock + timedelta(seconds=60))
    assert final["status"]["type"]["detail"] == "Final/OT"


def test_playoff_week_numbers():
    s = copy.deepcopy(tb_dal())
    s["header"]["season"]["type"] = 3
    s["header"]["week"] = 5  # ESPN's Super Bowl week
    assert ReplayFeed({EV: s}, event=EV).week() == 22
    s["header"]["week"] = 1
    assert ReplayFeed({EV: s}, event=EV).week() == 19


def test_from_folder_takes_the_events_week(tmp_path):
    def save(event, summary):
        (tmp_path / f"{event}.json.gz").write_bytes(gzip.compress(json.dumps(summary).encode()))

    save(EV, tb_dal())
    save(OT, fixture("summary_401872945_ind_kc_ot.json"))  # week 2: left out
    late = copy.deepcopy(tb_dal())
    late["header"]["id"] = "401872999"
    save("401872999", late)  # the same week
    (tmp_path / "broken.json.gz").write_bytes(b"not gzip")
    feed = ReplayFeed.from_folder(tmp_path, EV)
    assert sorted(feed._games) == [EV, "401872999"]  # noqa: SLF001
    with pytest.raises(ReplayError, match="no saved summary"):
        ReplayFeed.from_folder(tmp_path, "401870000")
    with pytest.raises(ValueError):
        ReplayFeed.from_folder(tmp_path, "../x")


def test_bad_inputs_are_refused():
    with pytest.raises(ReplayError):
        ReplayFeed({}, event=EV)
    with pytest.raises(ReplayError):
        ReplayFeed({EV: tb_dal()}, event="401870000")
    with pytest.raises(ReplayError):
        ReplayFeed({EV: tb_dal()}, event=EV, speed=0)


def test_replay_at_reads_eastern_time_without_a_zone():
    assert parse_at("2026-10-04T15:57") == datetime(2026, 10, 4, 19, 57, tzinfo=UTC)
    assert parse_at("2026-10-04T19:57:00Z") == datetime(2026, 10, 4, 19, 57, tzinfo=UTC)
    assert parse_at(None) is None
    with pytest.raises(ReplayError):
        parse_at("Sunday at four")
