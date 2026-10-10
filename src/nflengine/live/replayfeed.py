"""A finished week served as if live (LD02: `nfl app --live-replay EVENT`).

`ReplayFeed` answers the three ESPN calls the live code makes (the week's scoreboard, one
game's scoreboard entry, a summary) from saved summaries (`{NFL_DATA_ROOT}/live/summaries/`),
cut at a moving **replayed now**: the replay starts at `at` (default: 30 s before the event's
first snap) and runs at `speed` x real time. ESPN's lag is simulated: a play shows up `lag_s`
seconds after its snap (LD01 saw 8-84 s live).

It plugs into the real client, `EspnClient(transport=feed.transport(), now=feed.now)`, so the
client's rules (one host, 2 s per game, back-off) and every parser run exactly as on a game
night. Nothing is fetched from ESPN and nothing is written.

How an answer is built at time t (the plays ESPN would have posted by then):
- **pre** before the first visible play, **post** once the last play is visible (ESPN's final
  summary), else **in**;
- the scoreboard entry's `situation` describes the **next snap**: the next non-admin play's
  `start` (down, distance, ESPN's `yardLine`, possession) with its clock, the score before it
  and both teams' timeouts as `replay.replay` counts them (LD01's parity-checked rules); a
  kickoff or a try next gives `down = -1` and no possession, as the real feed does; "End of
  Half" shows ESPN's stale 1st & 10 with no possession;
- `lastPlay` is the newest visible play (admin rows included, as on ESPN), with ESPN's own
  win probability from the summary's `winprobability`;
- the summary is cut to the visible plays (no `wallclock` beyond t).
"""

from __future__ import annotations

import copy
import gzip
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from nflengine.curate.teams import normalize_team
from nflengine.live.espn import ALLOWED_HOST, POST_WEEKS, check_event_id
from nflengine.live.replay import (
    CHALLENGE_RE,
    CHALLENGE_TO_RE,
    TIMEOUT_RE,
    ReplayPlay,
    replay,
    team_names,
    timeout_window,
)
from nflengine.live.state import ADMIN_TYPES, GameContext, parse_time, plain, summary_plays

PATH = "/apis/site/v2/sports/football/nfl"
DEFAULT_LAG_S = 10.0
LEAD_IN_S = 30.0  # the default start: half a minute before the event's first snap


class ReplayError(ValueError):
    """The replay can't start (no saved summary, an unknown event): the message is safe to show."""


@dataclass
class _Game:
    """One saved summary, prepared once: plays in order with an effective time each."""

    event: str
    summary: dict[str, Any]
    plays: list[dict[str, Any]]
    times: list[datetime]  # each play's wallclock (a play without one takes the previous one's)
    rows: dict[str, ReplayPlay]  # play id -> the replay row (state before the play)
    home_id: str
    away_id: str
    wp: dict[str, float]  # play id -> ESPN's home win probability after it
    used: list[dict[tuple[str, str], int]]  # after each play: (window, team) -> timeouts used
    home: str  # ESPN's home / away codes (canonical)
    away: str
    playoffs: bool

    @property
    def first(self) -> datetime | None:
        return self.times[0] if self.times else None


def _prepare(event: str, summary: dict[str, Any]) -> _Game:
    header = summary.get("header") or {}
    comp = (header.get("competitions") or [{}])[0] or {}
    sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    home, away = sides.get("home") or {}, sides.get("away") or {}
    home_id = str(home.get("id") or (home.get("team") or {}).get("id") or "")
    away_id = str(away.get("id") or (away.get("team") or {}).get("id") or "")
    # ESPN's own sides: the feed answers in ESPN's terms, so the replay rows use ESPN's home
    ctx = GameContext(
        season=int((header.get("season") or {}).get("year") or 0),
        home=normalize_team((home.get("team") or {}).get("abbreviation")) or "HOME",
        away=normalize_team((away.get("team") or {}).get("abbreviation")) or "AWAY",
        playoffs=int((header.get("season") or {}).get("type") or 2) == 3,
    )
    plays = summary_plays(summary)
    times = effective_times([parse_time(p.get("wallclock")) for p in plays], comp.get("date"))
    rows = {r.play_id: r for r in replay(summary, ctx)}
    wp = {}
    for w in summary.get("winprobability") or []:
        if w.get("playId") is not None and isinstance(w.get("homeWinPercentage"), int | float):
            wp[str(w["playId"])] = float(w["homeWinPercentage"])
    used = timeouts_used(plays, header, ctx)
    return _Game(
        event, summary, plays, times, rows, home_id, away_id, wp, used, ctx.home, ctx.away,
        ctx.playoffs,
    )  # fmt: skip


def timeouts_used(
    plays: list[dict[str, Any]], header: dict[str, Any], ctx: GameContext
) -> list[dict[tuple[str, str], int]]:
    """Timeouts used per (window, team) after each play, by replay's own rules ("Timeout #N
    by TEAM" rows; a lost challenge costs one). A live answer reads the counts after the last
    play ESPN has posted, never from a later row (Sol review, LD02)."""
    names = team_names(header)
    used: dict[tuple[str, str], int] = {}
    out: list[dict[tuple[str, str], int]] = []
    for p in plays:
        ttype = str((p.get("type") or {}).get("text") or "")
        raw_text = str(p.get("text") or "")
        period = int((p.get("period") or {}).get("number") or 0)
        window, _ = timeout_window(period, ctx.playoffs)
        if ttype == "Timeout":
            m = TIMEOUT_RE.search(plain(raw_text, 400) or "")
            if m:
                team = normalize_team(m.group(2).upper())
                if team in (ctx.home, ctx.away):
                    used[(window, team)] = max(used.get((window, team), 0), int(m.group(1)))
        else:
            ch = CHALLENGE_RE.search(raw_text)
            team = names.get(ch.group(1)) if ch else None
            if team in (ctx.home, ctx.away):
                n = CHALLENGE_TO_RE.search(raw_text[ch.end() :])
                had = used.get((window, team), 0)
                used[(window, team)] = max(had, int(n.group(1))) if n else had + 1
        out.append(dict(used))
    return out


def effective_times(raw: list[datetime | None], date: Any = None) -> list[datetime]:
    """When ESPN posted each play, never going back in time: a play without a `wallclock` takes
    the previous one's (leading ones the first known), and a slip is evened out: a time later
    than the next play's is that play's time (one 2026 "Official Timeout" row is a day late),
    an earlier one the previous play's."""
    first = next((t for t in raw if t is not None), None)
    nxt: list[datetime | None] = [None] * len(raw)
    upcoming: datetime | None = None
    for i in range(len(raw) - 1, -1, -1):
        nxt[i] = upcoming
        upcoming = raw[i] if raw[i] is not None else upcoming
    out: list[datetime] = []
    last = first or parse_time(date) or datetime(2000, 1, 1, tzinfo=UTC)
    for t, n in zip(raw, nxt, strict=True):
        t = t or last
        if n is not None and t > n:
            t = n
        last = max(last, t)
        out.append(last)
    return out


def _status(period: int, clock: float, state: str, detail: str, short: str) -> dict[str, Any]:
    minutes, seconds = divmod(int(round(clock)), 60)
    return {
        "clock": float(clock),
        "displayClock": f"{minutes}:{seconds:02d}",
        "period": period,
        "type": {
            "state": state,
            "completed": state == "post",
            "description": {"pre": "Scheduled", "in": "In Progress", "post": "Final"}[state],
            "detail": detail,
            "shortDetail": short,
        },
    }


ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


def _period_name(period: int) -> str:
    return (
        f"{ORD[period]} Quarter" if period in ORD else ("OT" if period == 5 else f"{period - 4}OT")
    )


class ReplayFeed:
    """Saved summaries of one week, replayed from `at` at `speed` x real time."""

    def __init__(
        self,
        summaries: dict[str, dict[str, Any]],
        *,
        event: str,
        at: datetime | None = None,
        speed: float = 1.0,
        lag_s: float = DEFAULT_LAG_S,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if not summaries:
            raise ReplayError("no saved summaries to replay")
        self.event = check_event_id(event)
        if self.event not in summaries:
            raise ReplayError(f"event {self.event} has no saved summary")
        if speed <= 0:
            raise ReplayError("the replay speed must be above 0")
        self._games = {ev: _prepare(ev, s) for ev, s in summaries.items()}
        main = self._games[self.event]
        header = main.summary.get("header") or {}
        self.season = int((header.get("season") or {}).get("year") or 0)
        self.season_type = int((header.get("season") or {}).get("type") or 2)
        self.espn_week = _week_number(header.get("week"))
        start = at or ((main.first or datetime.now(UTC)) - timedelta(seconds=LEAD_IN_S))
        self.start = start if start.tzinfo else start.replace(tzinfo=UTC)
        self.speed = float(speed)
        self.lag_s = float(lag_s)
        self._mono = monotonic
        self._mono0 = monotonic()
        self._lock = threading.Lock()
        self.requests = 0

    # -- loading ---------------------------------------------------------------------------------
    @classmethod
    def from_folder(
        cls,
        folder: Path,
        event: str,
        *,
        at: datetime | None = None,
        speed: float = 1.0,
        lag_s: float = DEFAULT_LAG_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> ReplayFeed:
        """The event's week: every saved summary of the same season, season type and week."""
        ev = check_event_id(event)
        path = folder / f"{ev}.json.gz"
        if not path.exists():
            raise ReplayError(
                f"event {ev} has no saved summary: run `uv run nfl live replay --event {ev}` once"
            )
        try:
            main = _read(path)
        except Exception as e:  # noqa: BLE001 - a truncated or foreign file: say so plainly
            raise ReplayError(
                f"event {ev}'s saved summary can't be read ({type(e).__name__})"
            ) from e
        want = _week_of(main)
        summaries = {ev: main}
        for f in sorted(folder.glob("*.json.gz")):
            other = f.name.split(".")[0]
            if other == ev or not other.isdigit():
                continue
            try:
                s = _read(f)
            except Exception:  # noqa: BLE001 - a broken file of another game is skipped
                continue
            if _week_of(s) == want:
                summaries[other] = s
        return cls(summaries, event=ev, at=at, speed=speed, lag_s=lag_s, monotonic=monotonic)

    # -- the replayed clock ----------------------------------------------------------------------
    def now(self) -> datetime:
        return self.start + timedelta(seconds=(self._mono() - self._mono0) * self.speed)

    def week(self) -> int:
        """nflverse's week number of the replayed week."""
        if self.season_type != 3:
            return self.espn_week
        last_reg = 18 if self.season >= 2021 else 17
        back = {v: k for k, v in POST_WEEKS.items()}
        return last_reg + 1 + back.get(self.espn_week, self.espn_week - 1)

    def info(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "at": self.now().isoformat(),
            "speed": self.speed,
            "lag_s": self.lag_s,
        }

    # -- ESPN's answers at time t ----------------------------------------------------------------
    def _visible(self, g: _Game, t: datetime) -> int:
        """How many of the game's plays ESPN would have posted by t."""
        cut = t - timedelta(seconds=self.lag_s)
        n = 0
        for when in g.times:
            if when > cut:
                break
            n += 1
        return n

    def event_json(self, event: str, t: datetime | None = None) -> dict[str, Any]:
        """One game's scoreboard entry (`scoreboard/{event}` shape) at t."""
        g = self._games[event]
        t = t or self.now()
        header = g.summary.get("header") or {}
        comp_h = (header.get("competitions") or [{}])[0] or {}
        n = self._visible(g, t)
        vis = g.plays[:n]
        home_score = away_score = 0
        for p in vis:
            if p.get("scoringPlay") and isinstance(p.get("homeScore"), int | float):
                home_score, away_score = int(p["homeScore"]), int(p.get("awayScore") or 0)
        competitors = []
        for c in comp_h.get("competitors") or []:
            side = c.get("homeAway")
            competitors.append(
                {
                    "id": str(c.get("id")),
                    "homeAway": side,
                    "score": str(home_score if side == "home" else away_score),
                    "team": {
                        k: (c.get("team") or {}).get(k)
                        for k in ("id", "abbreviation", "displayName", "location", "name")
                    },
                }
            )
        comp: dict[str, Any] = {
            "id": event,
            "date": comp_h.get("date"),
            "neutralSite": comp_h.get("neutralSite"),
            "venue": {
                "indoor": ((g.summary.get("gameInfo") or {}).get("venue") or {}).get("indoor")
            },
            "competitors": competitors,
        }
        if n == 0:
            comp["status"] = _status(0, 0.0, "pre", "Scheduled", "Scheduled")
        elif n == len(g.plays):
            final = (
                "Final/OT" if int((vis[-1].get("period") or {}).get("number") or 4) > 4 else "Final"
            )
            comp["status"] = _status(4, 0.0, "post", final, final)
        else:
            comp["status"], comp["situation"] = self._live(g, n)
        out = {
            "id": event,
            "date": header.get("competitions", [{}])[0].get("date") if header else None,
            "season": {"year": self.season, "type": self.season_type},
            "week": {"number": self.espn_week},
            "competitions": [comp],
            "status": comp["status"],
        }
        return out

    def _live(self, g: _Game, n: int) -> tuple[dict[str, Any], dict[str, Any]]:
        last = g.plays[n - 1]
        ltype = str((last.get("type") or {}).get("text") or "")
        lid = str(last.get("id"))
        lperiod = int((last.get("period") or {}).get("number") or 1)
        last_play = {
            "id": lid,
            "text": last.get("text"),
            "type": {"text": ltype},
            "team": {"id": str(((last.get("start") or {}).get("team") or {}).get("id") or "")},
            "start": {"yardLine": (last.get("start") or {}).get("yardLine")},
            "end": {"yardLine": (last.get("end") or {}).get("yardLine")},
        }
        if lid in g.wp:
            last_play["probability"] = {"homeWinPercentage": g.wp[lid]}
        # the next play ESPN hasn't posted: its start is the state of the next snap
        nxt = next(
            (
                p
                for p in g.plays[n:]
                if str((p.get("type") or {}).get("text") or "") not in ADMIN_TYPES
            ),
            None,
        )
        row = g.rows.get(str(nxt.get("id"))) if nxt is not None else None
        # timeouts after the plays ESPN has posted, in the window of the next snap (a new half
        # starts at 3 each); never a later row's counts (Sol review, LD02)
        period_now = int(((nxt or last).get("period") or {}).get("number") or lperiod)
        window, start = timeout_window(period_now, g.playoffs)
        used = g.used[n - 1]
        h_to = max(0, start - used.get((window, g.home), 0))
        a_to = max(0, start - used.get((window, g.away), 0))
        situation: dict[str, Any] = {
            "homeTimeouts": h_to,
            "awayTimeouts": a_to,
            "lastPlay": last_play,
        }
        if ltype == "End of Half":
            situation.update({"down": 1, "distance": 10, "yardLine": 65})
            return _status(2, 0.0, "in", "Halftime", "Halftime"), situation
        if row is not None and row.snap and row.clock is not None:
            st = nxt.get("start") or {}
            situation.update(
                {
                    "down": int(st.get("down")),
                    "distance": int(st.get("distance") or 0),
                    "yardLine": int(st.get("yardLine")),
                    "downDistanceText": st.get("downDistanceText"),
                    "possessionText": st.get("possessionText"),
                    "possession": str((st.get("team") or {}).get("id")),
                    "isRedZone": bool(row.yardline_100 is not None and row.yardline_100 <= 20),
                }
            )
            period, clock = row.period, float(row.clock)
        else:
            # a try or a kickoff next (or nothing left): no snap pending
            end = last.get("end") or {}
            situation.update({"down": -1, "distance": 0, "yardLine": end.get("yardLine")})
            period = lperiod
            clock = _clock_of(last)
        minutes, seconds = divmod(int(round(clock)), 60)
        detail = f"{minutes}:{seconds:02d} - {_period_name(period)}"
        short = f"{minutes}:{seconds:02d} - {ORD.get(period, 'OT')}"
        return _status(period, clock, "in", detail, short), situation

    def scoreboard_json(self, t: datetime | None = None) -> dict[str, Any]:
        """The replayed week's scoreboard (`scoreboard?dates=..&seasontype=..&week=..`)."""
        t = t or self.now()
        events = sorted(
            self._games,
            key=lambda ev: (self._games[ev].first or datetime.max.replace(tzinfo=UTC), ev),
        )
        return {
            "season": {"year": self.season, "type": self.season_type},
            "week": {"number": self.espn_week},
            "events": [self.event_json(ev, t) for ev in events],
        }

    def summary_json(self, event: str, t: datetime | None = None) -> dict[str, Any]:
        """The game's summary with only the plays posted by t."""
        g = self._games[event]
        t = t or self.now()
        n = self._visible(g, t)
        keep = {str(p.get("id")) for p in g.plays[:n]}
        out = {k: v for k, v in g.summary.items() if k not in ("drives", "winprobability")}
        out["header"] = copy.deepcopy(g.summary.get("header") or {})
        comp = (out["header"].get("competitions") or [{}])[0]
        comp["status"] = self.event_json(event, t)["status"]
        previous = []
        for d in (g.summary.get("drives") or {}).get("previous") or []:
            plays = [p for p in d.get("plays") or [] if str(p.get("id")) in keep]
            if plays:
                previous.append({**d, "plays": plays})
        out["drives"] = {"previous": previous}
        out["winprobability"] = [
            w for w in g.summary.get("winprobability") or [] if str(w.get("playId")) in keep
        ]
        return out

    # -- the fake transport ----------------------------------------------------------------------
    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            with self._lock:
                self.requests += 1
            url = urlsplit(str(request.url))
            if url.netloc != ALLOWED_HOST or not url.path.startswith(PATH):
                return httpx.Response(404, json={"error": "not in the replay"})
            rest = url.path[len(PATH) :].strip("/")
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            t = self.now()
            if rest == "scoreboard":
                if q and (
                    str(q.get("dates")) != str(self.season)
                    or str(q.get("seasontype")) != str(self.season_type)
                    or str(q.get("week")) != str(self.espn_week)
                ):
                    return httpx.Response(200, json={"season": {"year": self.season}, "events": []})
                return httpx.Response(200, json=self.scoreboard_json(t))
            if rest.startswith("scoreboard/"):
                ev = rest.split("/", 1)[1]
                if ev in self._games:
                    return httpx.Response(200, json=self.event_json(ev, t))
                return httpx.Response(404, json={"error": "no such event"})
            if rest == "summary" and q.get("event") in self._games:
                return httpx.Response(200, json=self.summary_json(q["event"], t))
            return httpx.Response(404, json={"error": "not in the replay"})

        return httpx.MockTransport(handle)


def _read(path: Path) -> dict[str, Any]:
    data = json.loads(gzip.decompress(path.read_bytes()))
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def _week_of(summary: dict[str, Any]) -> tuple[int, int, int]:
    header = summary.get("header") or {}
    season = header.get("season") or {}
    return (
        int(season.get("year") or 0),
        int(season.get("type") or 0),
        _week_number(header.get("week")),
    )


def _week_number(w: Any) -> int:
    """A summary's `header.week` is a number; a scoreboard's `week` is `{"number": N}`."""
    if isinstance(w, dict):
        w = w.get("number")
    try:
        return int(w or 0)
    except (TypeError, ValueError):
        return 0


def _clock_of(play: dict[str, Any]) -> float:
    from nflengine.live.state import clock_seconds

    clk = play.get("clock") or {}
    v = clock_seconds(clk.get("value")) if clk.get("value") is not None else None
    return float(v if v is not None else clock_seconds(clk.get("displayValue")) or 0.0)


def parse_at(text: str | None) -> datetime | None:
    """`--replay-at`: an ISO time; without a zone it's US Eastern (game times are)."""
    if not text:
        return None
    try:
        t = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise ReplayError("--replay-at must be an ISO time, e.g. 2026-10-04T15:57") from e
    if t.tzinfo is None:
        from zoneinfo import ZoneInfo

        t = t.replace(tzinfo=ZoneInfo("America/New_York"))
    return t.astimezone(UTC)
