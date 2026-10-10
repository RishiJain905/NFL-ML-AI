"""Replay a game from ESPN's summary as if live (LD01).

Each play's `start` is the situation it was snapped from, so stepping through a summary gives
every state the live parser would have seen, for any game, at any time. The summary carries
no timeouts remaining: they come from ESPN's "Timeout #N by TEAM" rows (3 per half; 2 in a
regular-season overtime; 3 per two-period half of playoff overtime), the way nflfastR /
nfl4th read them, plus a lost challenge ("X challenged ..., and the play was Upheld"), which
costs the challenger one after the play. The score before a play is the last scoring play's
(ESPN's `awayScore` / `homeScore` are after the play, the try included). A scoring play's
clock is ESPN's clock at the score, so its snap clock is estimated (`scoring_start_clock`).

ESPN play ids are the event id followed by nflverse's `play_id` (`40187298040` = event
401872980, play 40), which is how the parity check joins the two sources.
"""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nflengine.curate.teams import normalize_team
from nflengine.live.espn import EspnClient, check_event_id
from nflengine.live.schema import GameState
from nflengine.live.state import (
    ADMIN_TYPES,
    GameContext,
    Snap,
    build_state,
    clock_seconds,
    event_teams,
    opening_receiver,
    parse_time,
    plain,
    summary_plays,
    yards_to_goal,
)

TIMEOUT_RE = re.compile(r"Timeout\s*#\s*(\d)\s+by\s+([A-Z]{2,4})\b", re.IGNORECASE)
# a lost challenge costs the challenger a timeout ("Indianapolis challenged the first down
# ruling, and the play was Upheld. (Timeout #1.)"; the "(Timeout #N.)" is sometimes missing)
CHALLENGE_RE = re.compile(r"([A-Z][A-Za-z']*(?: [A-Z][A-Za-z']*)*) challenged\b[^.]*?(?i:upheld)")
CHALLENGE_TO_RE = re.compile(r"\(Timeout\s*#\s*(\d)\.?\)", re.IGNORECASE)


def summary_state(summary: dict[str, Any]) -> str:
    """ESPN's game state in a summary: pre / in / post."""
    comp = ((summary.get("header") or {}).get("competitions") or [{}])[0] or {}
    return str(((comp.get("status") or {}).get("type") or {}).get("state") or "pre")


def summary_path(folder: Path, event_id: str) -> Path:
    return folder / f"{check_event_id(event_id)}.json.gz"


def load_summary(
    event_id: str,
    folder: Path,
    client: EspnClient | None = None,
    refresh: bool = False,
) -> tuple[dict[str, Any], str]:
    """A game's summary and where it came from ("saved" or "ESPN").

    A finished game's summary is kept under `folder` (`{NFL_DATA_ROOT}/live/summaries/`) so a
    replay or the parity check doesn't ask ESPN twice; `refresh` asks again (ESPN corrects
    plays after a game now and then). A game still running is never saved. Live data is a
    view, not a source: these files never feed curated tables."""
    path = summary_path(folder, event_id)
    if path.exists() and not refresh:
        return json.loads(gzip.decompress(path.read_bytes())), "saved"
    own = client is None
    client = client or EspnClient()
    try:
        data = client.summary(event_id).data
    finally:
        if own:
            client.close()
    if summary_state(data) == "post":
        folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(json.dumps(data).encode("utf-8")))
        tmp.replace(path)
    return data, "ESPN"


def timeout_window(period: int, playoffs: bool) -> tuple[str, int]:
    """(window, timeouts each team starts it with): halves in regulation, the overtime period
    in the regular season (2 each), two-period halves in playoff overtime (3 each)."""
    if period <= 2:
        return "h1", 3
    if period <= 4:
        return "h2", 3
    if not playoffs:
        return "ot", 2
    return f"ot{(period - 5) // 2}", 3


_PLAY_WORDS = (
    " pass", " rush", " up the middle", " left end", " right end", " left tackle",
    " right tackle", " left guard", " right guard", " punts", "field goal is", " sacked",
    " scrambles", " kneels", " spiked", " kicks",
)  # fmt: skip


def pre_snap_penalty(text: str) -> bool:
    """A penalty before the snap (false start, delay of game, encroachment ...): "No Play" and
    no play described before the first "PENALTY on" (formation notes in parentheses and
    injury updates don't count). A holding call on a run is "No Play" too, but its run is
    described first, so it is a snap."""
    x = f" {text.lower()} "
    if "no play" not in x or "penalty" not in x:
        return False
    before = x.split("penalty", 1)[0]
    return not any(w in before for w in _PLAY_WORDS)


def choice_of(type_text: str, text: str) -> str:
    """What the offense did: go (a run or a pass, fakes included), fg, punt, kneel, spike, or
    penalty (a penalty that wiped the play out: ESPN says "No Play").

    The text decides before ESPN's type: a play wiped out by a penalty is often typed as the
    play that was run ("Rush", "Pass Incompletion", "Punt": 73 snaps in 2026 weeks 1-5, all
    `no_play` in nflverse), and a punt with a fumbled return is typed "Fumble Recovery
    (Opponent)". A fake punt or field goal ("(Punt formation) ... runs") has no " punts " /
    "field goal is" in its text, so it stays a go. When a replay review reversed the call,
    ESPN keeps the first ruling's text (often "- No Play.") and adds the final one after
    "REVERSED": only the text after the last "reversed" counts. Pass the full text (not a
    shortened one): a "No Play" can come at the end of a long description."""
    t, x = type_text.lower(), f" {text.lower()} "
    if "reversed" in x:
        x = " " + x.rsplit("reversed", 1)[1]
    if "no play" in x:
        return "penalty"
    if "field goal" in t or "field goal is" in x:
        return "fg"
    if ("punt" in t and "(punt formation)" not in x) or " punts " in x:
        return "punt"
    if "kneels" in x:
        return "kneel"
    if "spiked" in x:
        return "spike"
    return "go"


@dataclass
class ReplayPlay:
    """One ESPN play with the state it started from (offense's side)."""

    play_id: str
    nflverse_play_id: int | None
    sequence: int
    period: int
    clock: float | None
    display_clock: str | None
    type: str
    text: str
    wallclock: datetime | None
    snap: bool  # a down was being played from this state (not a timeout, kickoff, quarter end)
    offense: str | None = None
    defense: str | None = None
    down: int | None = None
    distance: int | None = None
    yard_line: int | None = None  # ESPN's raw value (from ESPN's home team's goal line)
    yardline_100: int | None = None
    home_score: int = 0  # before the play (nflverse's home and away)
    away_score: int = 0
    off_timeouts: int | None = None  # before the play
    def_timeouts: int | None = None
    choice: str | None = None  # go / fg / punt / kneel / spike / penalty
    converted: bool | None = None  # a go on 3rd / 4th down: first down or touchdown
    state: GameState | None = None
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    offense_home: bool | None = None  # the offense is nflverse's home team

    @property
    def score_diff(self) -> int | None:
        if self.offense is None:
            return None
        return (self.home_score - self.away_score) * (1 if self.offense_home else -1)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.as_dict() if self.state is not None else None
        d["wallclock"] = self.wallclock.isoformat() if self.wallclock else None
        d["score_diff"] = self.score_diff
        return d


def team_names(header: dict[str, Any]) -> dict[str, str]:
    """ESPN's team names (city, nickname, full name, code) -> canonical code; a name both teams
    share ("New York" in NYG-NYJ) is left out."""
    out: dict[str, str | None] = {}
    for c in ((header.get("competitions") or [{}])[0] or {}).get("competitors") or []:
        team = c.get("team") or {}
        code = normalize_team(team.get("abbreviation"))
        for k in ("displayName", "location", "name", "nickname", "abbreviation"):
            name = team.get(k)
            if name and code:
                out[name] = code if out.get(name, code) == code else None
    return {k: v for k, v in out.items() if v is not None}


def scoring_start_clock(
    end_clock: float, anchor: tuple[int, float, datetime | None, bool] | None, period: int,
    wallclock: datetime | None,
) -> float:  # fmt: skip
    """The snap clock of a scoring play: ESPN gives a scoring play the clock **at the score**
    (the end of the play), not at its snap. `anchor` = (period, clock, wallclock, stopped) of
    the last snap, team timeout or two-minute warning: after a stopped clock the snap is at
    that clock; after a snap the clock can't have run longer than the real time between the
    two snaps (`wallclock`), so the estimate is the tightest bound, never below the end clock
    (509 / 552 scoring snaps of 2026 weeks 1-4 within 5 s of nflverse, against 423 for ESPN's
    clock as is; LD01 parity)."""
    if anchor is None or anchor[0] != period:
        return end_clock
    _, clock, at, stopped = anchor
    if stopped:
        return max(end_clock, clock)
    if at is None or wallclock is None:
        return end_clock
    return min(clock, max(end_clock, clock - (wallclock - at).total_seconds()))


def _converted(p: dict[str, Any], offense_id: str, type_text: str) -> bool:
    end = p.get("end") or {}
    if "touchdown" in type_text.lower() and str((end.get("team") or {}).get("id")) == offense_id:
        return "interception" not in type_text.lower() and "fumble" not in type_text.lower()
    return str((end.get("team") or {}).get("id")) == offense_id and end.get("down") == 1


def replay(summary: dict[str, Any], ctx: GameContext) -> list[ReplayPlay]:
    """Every play of a summary, in order, with the state it started from (a `GameState` for
    every snap the engine can score)."""
    header = summary.get("header") or {}
    event_id = str(header.get("id") or "")
    teams = event_teams(header)
    code_of = {tid: t["team"] for tid, t in teams.items()}
    espn_home = {tid: t["home_away"] == "home" for tid, t in teams.items()}
    indoor = ((summary.get("gameInfo") or {}).get("venue") or {}).get("indoor")
    opening = opening_receiver(summary)
    espn_home_code = next((t["team"] for t in teams.values() if t["home_away"] == "home"), None)
    names = team_names(header)
    used: dict[tuple[str, str], int] = {}  # (window, team) -> timeouts used
    home_score = away_score = 0
    anchor: tuple[int, float, datetime | None, bool] | None = None  # see scoring_start_clock
    out: list[ReplayPlay] = []
    for p in summary_plays(summary):
        pid = str(p.get("id"))
        ttype = str((p.get("type") or {}).get("text") or "")
        raw_text = str(p.get("text") or "")
        text = plain(raw_text, 400) or ""
        period = int((p.get("period") or {}).get("number") or 0)
        clk = p.get("clock") or {}
        clock = clock_seconds(clk.get("value")) if clk.get("value") is not None else None
        if clock is None:
            clock = clock_seconds(clk.get("displayValue"))
        wallclock = parse_time(p.get("wallclock"))
        st = p.get("start") or {}
        try:
            seq = int(p.get("sequenceNumber"))
        except (TypeError, ValueError):
            seq = len(out)
        nfl_id = int(pid[len(event_id) :]) if event_id and pid.startswith(event_id) else None
        row = ReplayPlay(
            play_id=pid,
            nflverse_play_id=nfl_id,
            sequence=seq,
            period=period,
            clock=clock,
            display_clock=plain(clk.get("displayValue"), 10),
            type=ttype,
            text=text,
            wallclock=wallclock,
            snap=False,
            home_score=home_score,
            away_score=away_score,
        )
        down = st.get("down")
        tid = str((st.get("team") or {}).get("id") or "")
        is_snap = (
            ttype not in ADMIN_TYPES
            and "kickoff" not in ttype.lower()
            and down in (1, 2, 3, 4)
            and tid in teams
            and st.get("yardLine") is not None
        )
        window, start_to = timeout_window(period, ctx.playoffs)
        end_clock = clock
        if is_snap and clock is not None and p.get("scoringPlay"):
            row.clock = clock = scoring_start_clock(clock, anchor, period, wallclock)
        if is_snap and clock is not None:
            offense = code_of[tid]
            defense = next(c for t, c in code_of.items() if t != tid)
            off_home = offense == ctx.home
            row.snap = True
            row.offense_home = off_home
            row.offense, row.defense = offense, defense
            row.down, row.distance = int(down), int(st.get("distance") or 0)
            row.yard_line = int(st["yardLine"])
            row.yardline_100 = yards_to_goal(row.yard_line, espn_home[tid])
            # ESPN logs a "Timeout #4" now and then (2026_04_DEN_SF): never below 0
            row.off_timeouts = max(0, start_to - used.get((window, offense), 0))
            row.def_timeouts = max(0, start_to - used.get((window, defense), 0))
            snap = Snap(
                offense=offense,
                offense_is_espn_home=espn_home[tid],
                period=period,
                clock=clock,
                down=row.down,
                distance=row.distance,
                yard_line=row.yard_line,
                off_score=home_score if off_home else away_score,
                def_score=away_score if off_home else home_score,
                off_timeouts=row.off_timeouts,
                def_timeouts=row.def_timeouts,
            )
            row.state, row.reason, row.warnings = build_state(
                ctx,
                snap,
                opening_receiver=opening,
                indoor_hint=bool(indoor) if indoor is not None else None,
            )
            if end_clock is not None and clock != end_clock:
                row.warnings.append(
                    f"ESPN's clock {row.display_clock} is the score's: the snap's estimated"
                )
            row.choice = choice_of(ttype, raw_text)
            if row.choice == "go" and row.down in (3, 4):
                row.converted = _converted(p, tid, ttype)
        if ttype == "Timeout":
            m = TIMEOUT_RE.search(text)
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
        if clock is not None:
            if is_snap:
                anchor = (period, clock, wallclock, False)
            elif ttype in ("Timeout", "Two-minute warning") and "official" not in text.lower():
                anchor = (period, clock, wallclock, True)  # the clock waits for the snap
        out.append(row)
        # the score after this play is the score before the next one. Only scoring plays move
        # it: ESPN shows 0-0 on some timeouts and two-minute warnings and, now and then, a
        # stray score on another play (all 9,366 snaps of 2026 weeks 1-4 agree with nflverse)
        hs, aws = p.get("homeScore"), p.get("awayScore")
        scores = p.get("scoringPlay") if "scoringPlay" in p else ttype not in ADMIN_TYPES
        if scores and isinstance(hs, int | float) and isinstance(aws, int | float):
            if espn_home_code in (None, ctx.home):
                home_score, away_score = int(hs), int(aws)
            else:  # ESPN's home is nflverse's away (a neutral site)
                home_score, away_score = int(aws), int(hs)
    return out


def decision_plays(plays: Iterable[ReplayPlay], downs: Iterable[int] = (3, 4)) -> list[ReplayPlay]:
    """The 3rd / 4th downs a replay scores: snaps with a state. Left out: plays a penalty wiped
    out (the coach's choice shows on the play that follows) and kneels and spikes (clock
    plays, not a go / kick / punt decision; 20 of ~2,540 in 2026 weeks 1-5)."""
    want = set(downs)
    skip = ("penalty", "kneel", "spike")
    return [
        p
        for p in plays
        if p.snap and p.state is not None and p.down in want and p.choice not in skip
    ]
