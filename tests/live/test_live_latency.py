"""LD01 latency logger: `run_latency` on a scripted game (fake client, fake clocks) and
`summarize` on hand-made logs. No network, no data root."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from nflengine.live import latency as L
from nflengine.live.espn import EspnError, Fetched

EV = "401872981"
T0 = datetime(2026, 10, 11, 13, 30, 0, tzinfo=UTC)


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def game(state: str, play: str | None = None, ptype: str = "Rush", sit: str = "") -> dict:
    s: dict = {}
    if play:
        s = {"lastPlay": {"id": play, "type": {"text": ptype}, "text": "x"},
             "downDistanceText": sit, "down": 2}  # fmt: skip
    return {"id": EV, "competitions": [{"status": {"type": {"state": state}}, "situation": s}]}


def summary(plays: list[tuple[str, float, str, int]]) -> dict:
    """(play id, snap second, type, start down)"""
    return {
        "drives": {
            "previous": [
                {
                    "plays": [
                        {
                            "id": pid,
                            "sequenceNumber": str(i),
                            "type": {"text": t},
                            "start": {"down": d},
                            "wallclock": at(w).isoformat(),
                        }
                        for i, (pid, w, t, d) in enumerate(plays)
                    ]  # fmt: skip
                }
            ]
        }
    }


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def mono(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s

    def now(self) -> datetime:
        return at(self.t)


class Scripted:
    """`game()` answers from a script keyed by the fake clock; `summary()` grows over time."""

    def __init__(self, clock: Clock, games: list[tuple[float, object]], summaries) -> None:
        self.clock, self.games, self.summaries = clock, games, summaries
        self.calls: list[str] = []

    def _at(self, script):
        cur = None
        for t, ans in script:
            if self.clock.t >= t:
                cur = ans
        return cur

    def game(self, ev):
        self.calls.append("game")
        ans = self._at(self.games)
        if isinstance(ans, Exception):
            raise ans
        return Fetched(data=ans, url="u", fetched_at=self.clock.now(), ms=50.0)

    def summary(self, ev):
        self.calls.append("summary")
        return Fetched(data=self._at(self.summaries), url="u", fetched_at=self.clock.now(), ms=80)

    def next_allowed_in(self, ev) -> float:
        return 0.0

    def close(self) -> None:
        pass


PLAYS = [
    ("p40", 0.0, "Kickoff", 0),
    ("p1", 60.0, "Rush", 1),
    ("p2", 100.0, "Pass Reception", 3),
    ("p3", 140.0, "Punt", 4),
    ("p4", 180.0, "Rush", 1),
]


def run(tmp_path, games, summaries, minutes=10.0):
    clock = Clock()
    client = Scripted(clock, games, summaries)
    logs: list[str] = []
    stats = L.run_latency(
        EV, minutes, tmp_path, client=client, monotonic=clock.mono, sleep=clock.sleep,
        now=clock.now, log=logs.append,
    )  # fmt: skip
    rows = [json.loads(x) for x in (tmp_path / f"{EV}.jsonl").read_text().splitlines()]
    return stats, rows, client, logs


def test_a_scripted_game(tmp_path) -> None:
    games = [
        (0, game("pre")),
        (60, game("in", "p40", "Kickoff")),
        (70, game("in", "p1", sit="2nd & 6 at PHI 30")),  # p1 snapped at 60: seen at 70
        (112, game("in", "p2", "Pass Reception", "4th & 2")),  # p2 at 100: seen at 112
        (190, game("in", "p4")),  # p3 (the punt) never shown as lastPlay
        (200, game("post", "p4")),
    ]
    summaries = [(0, summary(PLAYS[:1])), (150, summary(PLAYS))]
    stats, rows, client, logs = run(tmp_path, games, summaries)
    kinds = [r["kind"] for r in rows]
    assert kinds[0] == "start" and kinds[-1] == "end"
    seen = {r["play_id"]: r for r in rows if r["kind"] == "game"}
    assert set(seen) == {"p40", "p1", "p2", "p4"}, "each play logged once, the first time"
    assert seen["p1"]["seen_at"] == at(70).isoformat()
    assert seen["p2"]["situation"] == "4th & 2"
    assert {r["play_id"] for r in rows if r["kind"] == "summary"} == {p[0] for p in PLAYS}
    # snaps: p1, p2, p3, p4 (the kickoff has no down); p3 was never the scoreboard's lastPlay
    assert stats["plays"] == 3 and stats["plays_missed"] == 1
    assert stats["lag_median_s"] == 10.0  # lags 10 (p1), 12 (p2), 10 (p4)
    assert stats["lag_max_s"] == 12.0 and stats["worst_play"]["play_id"] == "p2"
    # p2 led to the 4th down (p3 at 140): seen at 112, 28 s before the snap
    assert stats["fourth_down_lead_median_s"] == 28.0
    assert stats["fourth_down_lead_positive_share"] == 1.0
    assert stats == {k: v for k, v in rows[-1].items() if k not in ("kind", "at")}
    # pre-game polls every 30 s, then 2 s; it stopped a minute after the final
    assert client.calls.count("game") < 200 and 200 + L.FINAL_GRACE_S <= client.clock.t < 265


def test_stops_at_the_time_limit(tmp_path) -> None:
    stats, rows, client, _ = run(
        tmp_path, [(0, game("in", "p1"))], [(0, summary(PLAYS[:2]))], minutes=1.0
    )
    assert 60 <= client.clock.t <= 63
    assert rows[-1]["kind"] == "end" and stats["plays"] == 1


def test_errors_are_logged_once_and_it_keeps_going(tmp_path) -> None:
    games = [(0, EspnError("ESPN didn't answer (HTTP 503)")), (20, game("in", "p1"))]
    stats, rows, _, logs = run(tmp_path, games, [(0, summary(PLAYS[:2]))], minutes=1.0)
    errs = [r for r in rows if r["kind"] == "error"]
    assert len(errs) == 1 and "HTTP 503" in errs[0]["error"]
    assert any(r["kind"] == "game" and r["play_id"] == "p1" for r in rows)


def test_appends_to_an_earlier_log(tmp_path) -> None:
    run(tmp_path, [(0, game("in", "p1"))], [(0, summary(PLAYS[:2]))], minutes=0.1)
    run(tmp_path, [(0, game("in", "p1"))], [(0, summary(PLAYS[:2]))], minutes=0.1)
    rows = (tmp_path / f"{EV}.jsonl").read_text().splitlines()
    assert sum(json.loads(r)["kind"] == "start" for r in rows) == 2


def test_summarize_keeps_the_first_sighting_and_skips_admin_plays(tmp_path) -> None:
    p = tmp_path / "x.jsonl"
    lines = [
        {"kind": "summary", "play_id": "a", "type": "Rush", "down": 3,
         "wallclock": at(0).isoformat(), "seen_at": at(30).isoformat()},
        {"kind": "summary", "play_id": "t", "type": "Timeout", "down": 4,
         "wallclock": at(20).isoformat(), "seen_at": at(30).isoformat()},
        {"kind": "summary", "play_id": "b", "type": "Punt", "down": 4,
         "wallclock": at(40).isoformat(), "seen_at": at(60).isoformat()},
        {"kind": "game", "play_id": "a", "seen_at": at(9).isoformat()},
        {"kind": "game", "play_id": "a", "seen_at": at(7).isoformat()},
        {"kind": "game", "play_id": "t", "seen_at": at(21).isoformat()},
        {"kind": "game", "play_id": "b", "seen_at": at(48).isoformat()},
        "not json",
    ]  # fmt: skip
    p.write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines))
    s = L.summarize(p)
    assert s["plays"] == 2 and s["lag_median_s"] == 7.5  # a: 7 (the earliest), b: 8
    assert s["summary_lag_median_s"] == 25.0  # a: 30, b: 20
    assert s["fourth_down_lead_median_s"] == 33.0  # a seen at 7, b (a 4th down) snapped at 40
    assert s["lead_positive_share"] == 1.0


def test_summarize_on_an_empty_log(tmp_path) -> None:
    p = tmp_path / "e.jsonl"
    p.write_text("")
    s = L.summarize(p)
    assert s["plays"] == 0 and s["lag_median_s"] is None and s["worst_play"] is None


def test_bad_event_id_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError):
        L.run_latency("../x", 1.0, tmp_path, client=object())


# --- Sol review fixes (LD01) -------------------------------------------------------------------
def test_pre_snap_penalties_are_not_snaps(tmp_path) -> None:
    """A delay of game on 4th down isn't a snap: it isn't counted, and the 'next snap' after
    the 3rd-down play is the punt a minute later (TB@DAL plays 1590 / 1620)."""
    p = tmp_path / "x.jsonl"
    rows = [
        {"kind": "summary", "play_id": "a", "type": "Rush", "down": 3, "pre_snap": False,
         "wallclock": at(0).isoformat(), "seen_at": at(20).isoformat()},
        {"kind": "summary", "play_id": "dog", "type": "Penalty", "down": 4, "pre_snap": True,
         "wallclock": at(40).isoformat(), "seen_at": at(60).isoformat()},
        {"kind": "summary", "play_id": "punt", "type": "Punt", "down": 4, "pre_snap": False,
         "wallclock": at(100).isoformat(), "seen_at": at(120).isoformat()},
        {"kind": "game", "play_id": "a", "seen_at": at(10).isoformat()},
        {"kind": "game", "play_id": "dog", "seen_at": at(45).isoformat()},
        {"kind": "game", "play_id": "punt", "seen_at": at(108).isoformat()},
    ]  # fmt: skip
    p.write_text("\n".join(json.dumps(r) for r in rows))
    s = L.summarize(p)
    assert s["plays"] == 2 and s["plays_missed"] == 0
    assert s["fourth_down_lead_median_s"] == 90.0  # punt at 100 - a seen at 10


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("(Field Goal formation) PENALTY on TB, Delay of Game, 5 yards, enforced at DAL 43 - "
         "No Play.", True),
        ("PENALTY on TB-L.Goedeke, False Start, 5 yards, enforced at DAL 16 - No Play.", True),
        ("** Injury Update: TB-T.Wirfs has returned to the game.  PENALTY on TB-B.Bredeson, "
         "False Start, 5 yards, enforced at TB 35 - No Play.", True),
        ("(Shotgun) J.Daniels pass incomplete. PENALTY on TB-B.Bredeson, Offensive Holding, 10 "
         "yards, enforced at TB 48 - No Play.", False),
        ("S.Tucker up the middle to DAL 33 for 4 yards. PENALTY on TB, Holding - No Play.", False),
        ("B.Anger punts 53 yards to TB 3. PENALTY on DAL, Holding, declined.", False),
    ],
)  # fmt: skip
def test_pre_snap_penalty(text, want) -> None:
    from nflengine.live.replay import pre_snap_penalty

    assert pre_snap_penalty(text) is want


def test_the_first_summary_sighting_survives_a_restart(tmp_path) -> None:
    """Restarting re-logs every summary play; the earliest sighting must stay (else a play
    first seen at +20 s would show +100 s of summary lag)."""
    p = tmp_path / "x.jsonl"
    rows = [
        {"kind": "summary", "play_id": "a", "type": "Rush", "down": 3,
         "wallclock": at(0).isoformat(), "seen_at": at(20).isoformat()},
        {"kind": "game", "play_id": "a", "seen_at": at(8).isoformat()},
        {"kind": "start"},
        {"kind": "summary", "play_id": "a", "type": "Rush", "down": 3,
         "wallclock": at(0).isoformat(), "seen_at": at(100).isoformat()},
    ]  # fmt: skip
    p.write_text("\n".join(json.dumps(r) for r in rows))
    assert L.summarize(p)["summary_lag_median_s"] == 20.0


def test_ctrl_c_still_writes_the_report(tmp_path) -> None:
    clock = Clock()

    class Interrupting(Scripted):
        def game(self, ev):
            if self.clock.t >= 80:
                raise KeyboardInterrupt
            return super().game(ev)

    client = Interrupting(clock, [(0, game("in", "p1")), (70, game("in", "p2"))],
                          [(0, summary(PLAYS))])  # fmt: skip
    logs: list[str] = []
    stats = L.run_latency(
        EV, 10.0, tmp_path, client=client, monotonic=clock.mono, sleep=clock.sleep,
        now=clock.now, log=logs.append,
    )  # fmt: skip
    rows = [json.loads(x) for x in (tmp_path / f"{EV}.jsonl").read_text().splitlines()]
    assert stats["interrupted"] is True and rows[-1]["kind"] == "end"
    assert rows[-1]["interrupted"] is True and stats["plays"] == 2
    assert any("Ctrl+C" in m for m in logs)
