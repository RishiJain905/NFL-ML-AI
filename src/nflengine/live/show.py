"""Plain-text views of the live feed for the terminal (`nfl live ...`, LD01).

Pure functions from the parsed state and the engine's answers to short lines and table rows,
so the CLI only prints. ESPN's own text arrives here already through `state.plain`; the CLI
escapes it before Rich renders anything (a play text must never become markup).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nflengine.live.decide import NAMES, Decision, ThirdDown
from nflengine.live.replay import ReplayPlay
from nflengine.live.state import LiveState, clock_seconds, competition, event_teams, plain

ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


def spot(yardline_100: int, offense: str, defense: str) -> str:
    """Where the ball is, in the broadcast's words: "DAL 35" (the side of the field) or "50"."""
    if yardline_100 == 50:
        return "50"
    if yardline_100 > 50:
        return f"{offense} {100 - yardline_100}"
    return f"{defense} {yardline_100}"


def down_text(down: int, distance: int, yardline_100: int, offense: str, defense: str) -> str:
    """ "4th & 2 at DAL 35", "3rd & Goal at DAL 2"."""
    togo = "Goal" if distance >= yardline_100 else str(distance)
    return f"{ORDINAL.get(down, down)} & {togo} at {spot(yardline_100, offense, defense)}"


def clock_text(period: int, clock: float | None) -> str:
    q = f"Q{period}" if period <= 4 else ("OT" if period == 5 else f"OT{period - 4}")
    if clock is None:
        return q
    return f"{q} {int(clock) // 60}:{int(clock) % 60:02d}"


def pct(x: float | None, digits: int = 0) -> str:
    return "-" if x is None else f"{100 * x:.{digits}f}%"


def local(t: datetime | None) -> str:
    return "-" if t is None else t.astimezone().strftime("%H:%M:%S")


def header_lines(ls: LiveState, age_s: float | None = None) -> list[str]:
    """The game line, the situation and the "as of" stamp."""
    lines = [
        f"{ls.away} {ls.away_score} at {ls.home} {ls.home_score} · "
        f"{ls.detail or clock_text(ls.period, ls.clock)}"
    ]
    if ls.offense and ls.down and ls.yardline_100 is not None:
        sit = down_text(ls.down, ls.distance or 1, ls.yardline_100, ls.offense, ls.defense or "")
        tos = f"timeouts {ls.away} {_n(ls.away_timeouts)}, {ls.home} {_n(ls.home_timeouts)}"
        src = " (next snap from the summary)" if ls.source == "summary" else ""
        lines.append(f"{ls.offense} ball, {sit}{src} · {tos}")
    stamp = f"as of {local(ls.fetched_at)}"
    if age_s is not None:
        stamp += f" (ESPN answered {age_s:.0f} s ago)"
    if ls.last_play_text:
        when = f", snapped {local(ls.last_play_at)}" if ls.last_play_at else ""
        stamp += f" · last play{when}: {ls.last_play_text}"
    lines.append(stamp)
    if ls.espn_home_wp is not None:
        lines.append(f"ESPN's own win probability for {ls.home}: {pct(ls.espn_home_wp)}")
    return lines


def _n(x: int | None) -> str:
    return "?" if x is None else str(x)


def fourth_lines(d: Decision, offense: str = "the offense") -> list[str]:
    """The 4th-down call: the choice, its label, the WP after each choice and the odds behind."""
    label = f" ({d.label})" if d.label else ""
    out = [
        f"Call: {NAMES[d.best]}{label}, {100 * d.gap:.1f} win-probability points over the next best"
    ]
    for opt in ("go", "fg", "punt"):
        wp = d.wp.get(opt)
        if wp is None:
            continue
        mark = " <" if opt == d.best else ""
        out.append(f"  {NAMES[opt]:<11} {pct(wp, 1):>6} win probability for {offense}{mark}")
    odds = [f"convert {pct(d.convert)}"]
    if d.fg_make is not None:
        odds.append(f"field goal from {d.fg_distance:.0f} yd {pct(d.fg_make)}")
    if d.punt_start is not None:
        odds.append(f"after a punt they start at their own {d.punt_start:.0f}")
    out.append("  " + " · ".join(odds) + f" · now {pct(d.wp_now, 1)} · {d.ms:.0f} ms")
    return out


def third_lines(t: ThirdDown, offense: str, defense: str) -> list[str]:
    """The 3rd-down check: convert %, pass % and the if-stopped table (every gain short of the
    line to gain, plus losses of 5 and 10)."""
    out = [
        f"3rd down: convert {pct(t.convert)}, pass {pct(t.pass_prob)}; "
        f"win probability now {pct(t.wp_now, 1)} · {t.ms:.0f} ms"
    ]
    if t.note:
        out.append(f"  {t.note}")
        return out
    out.append("  If they're stopped short:")
    for r in sorted(t.table, key=lambda r: -r.gain):
        if r.gain < 0 and r.gain not in (-5, -10):
            continue  # keep the table short
        where = down_text(4, r.ydstogo, r.yardline_100, offense, defense)
        label = f" ({r.label})" if r.label else ""
        out.append(f"    {where:<22} {NAMES[r.best]}{label}, +{100 * r.gap:.1f}")
    return out


def play_kind(type_text: str, text: str) -> str:
    """pass (dropbacks: sacks and scrambles too, nflverse's `pass`) or run."""
    t, x = type_text.lower(), text.lower()
    if any(k in t for k in ("pass", "sack", "interception")) or "scrambles" in x:
        return "pass"
    if "penalty" in t and any(k in x for k in (" pass ", "sacked", "scrambles")):
        return "pass"
    return "run"


def outcome_text(p: ReplayPlay) -> str:
    """What happened on a replayed 3rd / 4th down."""
    t = p.type.lower()
    if p.choice == "fg":
        if "good" in t:
            return "field goal, good"
        if "block" in t:
            return "field goal, blocked"
        return "field goal, missed"
    if p.choice == "punt":
        return "punt"
    if p.choice in ("kneel", "spike"):
        return p.choice
    kind = play_kind(p.type, p.text)
    if p.converted is None:
        return kind
    return f"{kind}, {'converted' if p.converted else 'stopped'}"


def replay_row(p: ReplayPlay, call: Decision | ThirdDown | None) -> dict[str, Any]:
    """One row of `nfl live replay`."""
    s = p.state
    assert s is not None and p.offense and p.defense
    row: dict[str, Any] = {
        "play": p.nflverse_play_id,
        "when": clock_text(p.period, p.clock),
        "offense": p.offense,
        "situation": down_text(
            p.down or 0, p.distance or 1, p.yardline_100 or 0, p.offense, p.defense
        ),
        "score": f"{s.score_diff:+d}",
        "happened": outcome_text(p),
    }
    if isinstance(call, Decision):
        label = f" ({call.label})" if call.label else ""
        row["bot"] = f"{NAMES[call.best]}{label}"
        row["wp"] = " / ".join(pct(call.wp.get(o), 0) for o in ("go", "fg", "punt"))
        row["agree"] = {"go": "go", "fg": "fg", "punt": "punt"}.get(p.choice or "") == call.best
    elif isinstance(call, ThirdDown):
        row["bot"] = f"convert {pct(call.convert)} · pass {pct(call.pass_prob)}"
        row["wp"] = pct(call.wp_now, 0)
        row["agree"] = None
    return row


def game_rows(scoreboard: dict[str, Any], game_ids: dict[str, str | None]) -> list[dict[str, Any]]:
    """`nfl live games`: one row per event (status, score, clock, down and distance). Every
    string from ESPN goes through `plain` (the CLI escapes it too)."""
    out = []
    for ev in scoreboard.get("events") or []:
        comp = competition(ev)
        st = (comp.get("status") or ev.get("status") or {}).get("type") or {}
        teams = event_teams(ev)
        home = next((t for t in teams.values() if t["home_away"] == "home"), {})
        away = next((t for t in teams.values() if t["home_away"] == "away"), {})
        sit = comp.get("situation") or {}
        poss = plain(teams.get(str(sit.get("possession") or ""), {}).get("team"), 8)
        a_code = plain(away.get("team"), 8) or "?"
        h_code = plain(home.get("team"), 8) or "?"
        state = st.get("state") or "pre"
        status = plain(st.get("shortDetail") or st.get("detail"), 40) or state
        clock = clock_seconds((comp.get("status") or {}).get("clock"))
        out.append(
            {
                "event": plain(ev.get("id"), 20) or "?",
                "game_id": game_ids.get(str(ev.get("id"))),
                "matchup": f"{a_code} at {h_code}",
                "state": state,
                "status": status,
                "score": "" if state == "pre" else f"{away.get('score', 0)}-{home.get('score', 0)}",
                "ball": poss or "",
                "situation": plain(sit.get("downDistanceText"), 40) or "",
                "clock": clock,
                "kickoff": ev.get("date"),
            }
        )
    return out
