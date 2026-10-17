"""Reconstructed play diagrams (PC03): one play's geometry, rebuilt from the charting data.

There is no public player tracking for 2024-2026 games (D110), so a diagram is a playbook card
drawn from what the play-by-play, FTN and (2016-2025) participation record about the play:
- **known** (drawn where the data puts it): the line of scrimmage, the first-down line, the hash,
  the QB's alignment, how many stood in the backfield and in the box, how many rushed, motion,
  play-action / screen / RPO, the throw's direction and depth, the run's lane, the gain;
- **approximate** (generic spots, marked `approx`): where receivers line up (from the receiver
  count, the personnel when known, and the formation), where the off-ball defenders stand
  (corners over the widest receivers, safeties by the coverage shell when known), the catch
  spot within its third of the field, the target's path, the run after the line;
- **unknown** (never drawn): routes other than the target's, coverage assignments, blocking.

Every answer carries `LABEL`; the browser shows it on the player at all times.

Coordinates: `x` in yards from the offense's own goal line (0) to the opponent's (100), end
zones at -10..0 and 100..110, the offense always moving towards +x; `y` in yards from the
offense's **left** sideline (0) to its right (53.33), so an SVG with x to the right and y down
draws the field as the QB sees it, turned 90 degrees. Each moving thing has keyframes `[t, x,
y]` (the ball `[t, x, y, h]`, h its height for the arc); the browser interpolates linearly.

Pure: a row of `plays_enriched` (a dict) in, a JSON-ready dict out. The reader
(`app/readers/playbrowser.py`) reads the files and cleans the play's text.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from nflengine.curate.teams import TEAM_MAP

VERSION = 1
LABEL = "Reconstructed from charting data, not player tracking"
FIELD_WIDTH = 160 / 3  # 53.33 yards
MIDDLE_Y = FIELD_WIDTH / 2
# NFL hash marks: 70 ft 9 in from each sideline. FTN's hash is read from the offense's view.
HASH_Y = {"left": 23.58, "middle": MIDDLE_Y, "right": FIELD_WIDTH - 23.58}
# Where a throw to the left / middle / right lands across the field (generic: the gamebook
# records the third, not the spot).
PASS_Y = {"left": 10.5, "middle": MIDDLE_Y, "right": FIELD_WIDTH - 10.5}
# A run's lane at the line, as an offset from the ball (negative = the offense's left).
LANE_OFFSET = {"middle": 0.0, "guard": 1.3, "tackle": 2.6, "end": 5.5}
SCRAMBLE_OFFSET = {"left": -6.0, "middle": 0.0, "right": 6.0}
OL_SPACING = 1.3  # yards between linemen
LAST_PARTICIPATION = 2025  # nflverse participation on D: (2026's arrives about February 2027)
# Spots checked against 2023 tracking (Big Data Bowl 2026, 14,107 targeted passes joined to
# nflverse + FTN; the guide §10.3 has the numbers): the QB's depth at the snap by FTN's
# alignment (medians 1.6 / 5.0 / 5.1) and at the throw (8.3 / 7.8 / 7.5)
QB_DEPTH = {"under_center": 1.5, "shotgun": 5.0, "pistol": 5.0}
DROP_DEPTH = {"under_center": 8.3, "shotgun": 7.8, "pistol": 7.5}
# receivers from their sideline: the widest median 10.7, every WR / TE median 17.4
WIDE_Y, SLOT_Y, SLOT2_Y = 10.5, 16.0, 20.0
# defenders' depth at the snap: DL 0.6, LB 4.3-4.4, CB 4.8, nickel / S 4.8, SS 10.2, FS 11.5
DL_X, LB_X, CB_X, OVERHANG_X, SAFETY_X = 0.7, 4.4, 5.0, 5.0, 11.0
# Deep defenders by coverage shell (participation, 2018-2025); others: no shell drawn.
SHELL_DEEP = {
    "cover_0": 0,
    "cover_1": 1,
    "cover_3": 1,
    "cover_2": 2,
    "cover_4": 2,
    "cover_6": 2,
    "2_man": 2,
}
POSITION_NAME = {"RB": "running back", "WR": "wide receiver", "TE": "tight end", "QB": "QB"}
COVERAGE_NAME = {
    "cover_0": "Cover 0",
    "cover_1": "Cover 1",
    "cover_2": "Cover 2",
    "2_man": "2-man",
    "cover_3": "Cover 3",
    "cover_4": "Cover 4",
    "cover_6": "Cover 6",
    "cover_9": "Cover 9",
    "combo": "a combination coverage",
    "prevent": "prevent",
    "blown": "a blown coverage",
}

# The timeline, in seconds (a 3-4 s animation; not the play's real clock).
SNAP_T = 0.8
MOTION_T = (0.15, 0.7)
EXCHANGE_S = 0.2  # the snap reaching the QB
DROP_S = 0.7
FAKE_S = 0.45  # a play-action fake
RUSH_S = 1.0
HOLD_S = 0.6  # the last frame stays a moment

# "See a real one": a play's Big Data Bowl concept (PC03's library), in priority order.
CONCEPTS: dict[str, dict[str, str]] = {
    "screen": {"name": "Screen pass", "match": "a screen"},
    "rpo": {"name": "Run-pass option (RPO)", "match": "an RPO"},
    "play_action_deep": {
        "name": "Play-action deep shot",
        "match": "a play-action throw 20+ air yards",
    },
    "inside_zone": {"name": "Inside zone run", "match": "a run inside the tackles"},
    "outside_zone": {"name": "Outside zone run", "match": "a run outside the tackles"},
    "cover_1": {"name": "Cover 1", "match": "a dropback against Cover 1"},
    "cover_3": {"name": "Cover 3", "match": "a dropback against Cover 3"},
    "2_man": {"name": "2-man", "match": "a dropback against 2-man"},
}

_CLOCK_PREFIX = re.compile(r"^\s*\(\s*\d{0,2}:\d{2}\s*\)\s*")  # '(14:56) ', '(:05) '
_ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
# what only the play-by-play text says (plays_enriched has no fumble or formation columns)
_RECOVERED = re.compile(r"RECOVERED by ([A-Z]{2,3})-", re.IGNORECASE)
_FAKE_KICK = re.compile(r"\((?:Punt|Field Goal) formation\)", re.IGNORECASE)
_DIRECT_SNAP = re.compile(r"Direct snap", re.IGNORECASE)


# ---- small helpers ------------------------------------------------------------------------------
def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _i(x: Any) -> int | None:
    v = _f(x)
    return int(round(v)) if v is not None else None


def _b(x: Any) -> bool | None:
    return None if x is None else bool(x)


def _s(x: Any) -> str | None:
    return x.strip() if isinstance(x, str) and x.strip() else None


def _r(v: float) -> float:
    return round(float(v), 2)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _y(v: float) -> float:
    return _clamp(v, 0.5, FIELD_WIDTH - 0.5)


def _x(v: float) -> float:
    """Inside the field's back lines (a goal-line stand puts the safeties in the end zone)."""
    return _clamp(v, -9.5, 109.5)


# ---- the play's text (shared with the play list) -------------------------------------------------
def spot_text(yards_to_goal: Any, offense: Any, defense: Any) -> str | None:
    """'ARI 30' (own half), '50', 'LAC 43' (the opponent's half)."""
    ytg = _i(yards_to_goal)
    if ytg is None:
        return None
    if ytg == 50:
        return "50"
    return f"{offense} {100 - ytg}" if ytg > 50 else f"{defense} {ytg}"


def clock_text(quarter: Any, half_seconds: Any) -> str | None:
    """The game clock in the quarter ('5:32'), from the seconds left in the half."""
    q, hs = _i(quarter), _i(half_seconds)
    if q is None or hs is None:
        return None
    s = hs - 900 if q in (1, 3) else hs
    s = int(_clamp(s, 0, 900))
    return f"{s // 60}:{s % 60:02d}"


def quarter_text(quarter: Any) -> str | None:
    q = _i(quarter)
    return None if q is None else ("OT" if q >= 5 else f"Q{q}")


def down_text(down: Any, distance: Any, goal_to_go: Any = False) -> str | None:
    d, togo = _i(down), _i(distance)
    if d not in _ORD:
        return None
    if goal_to_go:
        return f"{_ORD[d]} & Goal"
    return f"{_ORD[d]} & {togo}" if togo is not None else _ORD[d]


def clean_desc(desc: Any) -> str | None:
    """The play-by-play text without its leading clock ('(14:56) ')."""
    s = _s(desc)
    return _CLOCK_PREFIX.sub("", s) if s else None


def play_kind(row: dict[str, Any]) -> str:
    """'run' (designed), 'pass' (an attempt), 'sack', 'scramble', 'aborted' or 'dropback'.
    A fumbled snap the QB picked up and threw (or was sacked on) is that play; 'aborted' is a
    snap that went nowhere else."""
    if row.get("aborted") and not (
        row.get("pass_attempt") or row.get("sack") or row.get("scramble")
    ):
        return "aborted"
    if row.get("sack"):
        return "sack"
    if row.get("pass_attempt"):  # before scramble: a QB who scrambled, then threw, passed
        return "pass"
    if row.get("scramble"):
        return "scramble"
    if row.get("dropback"):
        return "dropback"
    return "run"


def _yards(v: float | None) -> str:
    if v is None:
        return "no gain recorded"
    n = int(round(v))
    if n == 0:
        return "no gain"
    return f"+{n}" if n > 0 else f"−{abs(n)}"


def air_text(air: float | None) -> str | None:
    """'22 air yards', '1 air yard', 'at the line', '6 yards behind the line'."""
    if air is None:
        return None
    n = int(round(air))
    if n > 0:
        return f"{n} air yard{'s' if n != 1 else ''}"
    if n == 0:
        return "at the line"
    return f"{abs(n)} yard{'s' if n != -1 else ''} behind the line"


def _team(code: Any) -> str | None:
    """A team code as the project's canonical one (OAK -> LV ...), for comparing texts."""
    s = _s(code)
    return TEAM_MAP.get(s, s) if s else None


def fumble_lost(row: dict[str, Any]) -> bool:
    """A fumble the defense recovered, read from the play text: the last 'RECOVERED by XXX-'
    names a team other than the offense (a self-recovery says 'and recovers'). Codes are
    compared canonically: a 2019 text says OAK where the row may already say LV."""
    desc = _s(row.get("desc")) or ""
    if "FUMBLE" not in desc.upper():
        return False
    found = _RECOVERED.findall(desc)
    return bool(found) and _team(found[-1]) != _team(row.get("offense"))


def offense_touchdown(row: dict[str, Any]) -> bool:
    """nflverse's `touchdown` is 1 for a defensive score too (a pick-six, a fumble return):
    the offense scored only when it kept the ball."""
    return bool(row.get("touchdown")) and not row.get("interception") and not fumble_lost(row)


def result_of(row: dict[str, Any]) -> dict[str, Any]:
    """What happened, in a word and a short sentence (the list and the diagram say the same)."""
    kind = play_kind(row)
    y = _f(row.get("yards_gained"))
    lost = fumble_lost(row)
    td = offense_touchdown(row)
    fd = bool(row.get("first_down"))
    air = _f(row.get("air_yards"))
    if kind == "aborted":
        res, text = "aborted", "Aborted snap"
    elif kind == "sack":
        res, text = "sack", f"Sacked, {_yards(y)}"
    elif kind == "scramble":
        res, text = "scramble", f"Scramble, {_yards(y)}"
    elif kind == "pass":
        if row.get("interception"):
            res, text = "interception", "Intercepted"
        elif row.get("complete"):
            res = "complete"
            text = f"Complete, {_yards(y)}"
            if air is not None:
                text += f" ({air_text(air)})"
        else:
            res, text = "incomplete", "Incomplete"
    elif kind == "dropback":
        res, text = "dropback", f"Dropback, {_yards(y)}"
    else:
        res, text = "run", f"Run, {_yards(y)}"
    if lost:
        text += ", fumble lost"
    elif td:
        text += ", touchdown"
    elif fd and res not in ("incomplete", "interception", "sack"):
        text += ", first down"
    return {
        "kind": res,
        "yards": _r(y) if y is not None else None,
        "first_down": fd,
        "touchdown": td,
        "turnover": res == "interception" or lost,
        "fumble_lost": lost,
        "text": text,
    }


TAG_ORDER = (
    "play_action", "screen", "rpo", "deep_shot", "motion", "no_huddle", "trick_play",
    "fake_kick", "direct_snap",
)  # fmt: skip
TAG_NAME = {
    "play_action": "Play-action",
    "screen": "Screen",
    "rpo": "RPO",
    "deep_shot": "Deep shot",
    "motion": "Motion",
    "no_huddle": "No huddle",
    "trick_play": "Trick play",
    "fake_kick": "Fake kick",
    "direct_snap": "Direct snap",
}


def tags_of(row: dict[str, Any]) -> list[str]:
    desc = _s(row.get("desc")) or ""
    text = {
        "fake_kick": bool(_FAKE_KICK.search(desc)) and not row.get("aborted"),
        "direct_snap": bool(_DIRECT_SNAP.search(desc)),
    }
    return [t for t in TAG_ORDER if row.get(t) is True or text.get(t)]


def concept_of(row: dict[str, Any]) -> str | None:
    """The Big Data Bowl concept a play is "the same idea" as, or None (README §3)."""
    kind = play_kind(row)
    if row.get("screen") is True:
        return "screen"
    if row.get("rpo") is True:
        return "rpo"
    if kind == "pass" and row.get("play_action") is True and row.get("deep_shot") is True:
        return "play_action_deep"
    if kind == "run":
        loc, gap = _s(row.get("run_location")), _s(row.get("run_gap"))
        if loc == "middle" or gap == "guard":
            return "inside_zone"
        if gap in ("tackle", "end"):
            return "outside_zone"
        return None
    cov = _s(row.get("coverage"))
    if kind in ("pass", "sack", "scramble", "dropback") and cov in ("cover_1", "cover_3", "2_man"):
        return cov
    return None


def summary(row: dict[str, Any]) -> dict[str, Any]:
    """One play's row in the play browser (no geometry)."""
    off, dfn = row.get("offense"), row.get("defense")
    return {
        "game_id": row.get("game_id"),
        "play_id": _i(row.get("play_id")),
        "week": _i(row.get("week")),
        "offense": off,
        "defense": dfn,
        "quarter": _i(row.get("quarter")),
        "quarter_text": quarter_text(row.get("quarter")),
        "clock": clock_text(row.get("quarter"), row.get("half_seconds")),
        "down": _i(row.get("down")),
        "distance": _i(row.get("distance")),
        "down_text": down_text(row.get("down"), row.get("distance"), row.get("goal_to_go")),
        "yards_to_goal": _i(row.get("yards_to_goal")),
        "spot": spot_text(row.get("yards_to_goal"), off, dfn),
        "type": play_kind(row),
        "tags": tags_of(row),
        "result": result_of(row),
        "epa": _r(e) if (e := _f(row.get("epa"))) is not None else None,
        "explosive": bool(row.get("explosive")),
        "charted": bool(row.get("ftn_charted")),
        "concept": concept_of(row),
        "desc": clean_desc(row.get("desc")),
    }


# ---- the geometry --------------------------------------------------------------------------------
@dataclass
class _Player:
    id: str
    side: str
    role: str  # ol | qb | back | receiver | dl | lb | db
    label: str
    x: float
    y: float
    approx: bool = False
    keys: list[list[float]] = field(default_factory=list)
    rusher: bool = False
    target: bool = False
    carrier: bool = False

    def __post_init__(self) -> None:
        self.x, self.y = _x(self.x), _y(self.y)

    def at(self, t: float, x: float, y: float) -> None:
        """Add a keyframe. Times are compared rounded, as stored: a key at the last key's
        time replaces it and an earlier one is ignored, so times are strictly ascending."""
        if not self.keys:
            self.keys.append([0.0, _r(self.x), _r(self.y)])
        t = _r(t)
        if t < self.keys[-1][0]:
            return
        if t == self.keys[-1][0]:
            self.keys[-1] = [t, _r(_x(x)), _r(_y(y))]
        else:
            self.keys.append([t, _r(_x(x)), _r(_y(y))])

    def last(self) -> tuple[float, float, float]:
        if not self.keys:
            return 0.0, self.x, self.y
        t, x, y = self.keys[-1]
        return t, x, y

    def out(self) -> dict[str, Any]:
        keys = self.keys or [[0.0, _r(self.x), _r(self.y)]]
        return {
            "id": self.id,
            "side": self.side,
            "role": self.role,
            "label": self.label,
            "approx": self.approx,
            "rusher": self.rusher,
            "target": self.target,
            "carrier": self.carrier,
            "track": keys,
        }


@dataclass
class _Facts:
    known: list[str] = field(default_factory=list)
    approx: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


def _personnel(p: Any) -> tuple[int, int] | None:
    """'11' -> (1 back, 1 tight end); participation writes f'{RB + FB}{TE}'."""
    s = _s(p)
    if s is None or len(s) != 2 or not s.isdigit():
        return None
    return int(s[0]), int(s[1])


def _spread(center: float, n: int, gap: float) -> list[float]:
    return [center + (k - (n - 1) / 2) * gap for k in range(n)]


class _Diagram:
    def __init__(
        self,
        row: dict[str, Any],
        qb_ids: Iterable[str] = (),
        positions: dict[str, str] | None = None,
    ) -> None:
        self.row = row
        self.qb_ids = {q for q in qb_ids if q}
        self.positions = {k: v for k, v in (positions or {}).items() if k and v}
        self.facts = _Facts()
        self.players: list[_Player] = []
        self.ball: list[list[float]] = []
        self.marks: list[dict[str, Any]] = []
        self.phases: list[dict[str, Any]] = [{"name": "presnap", "t": 0.0, "text": ""}]
        ytg = _i(row.get("yards_to_goal"))
        self.los = float(100 - ytg) if ytg is not None else 50.0
        self.season = _i(row.get("season"))
        self.ftn = bool(row.get("ftn_charted"))
        self.part = bool(row.get("part_charted"))
        self.kind = play_kind(row)

    # -- helpers
    def add(self, *a: Any, **k: Any) -> _Player:
        p = _Player(*a, **k)
        self.players.append(p)
        return p

    def mark(
        self,
        kind: str,
        points: list[tuple[float, float]],
        t: float,
        approx: bool,
        text: str | None = None,
    ) -> None:
        self.marks.append(
            {
                "kind": kind,
                "points": [[_r(_x(x)), _r(_y(y))] for x, y in points],
                "t": _r(t),
                "approx": approx,
                "text": text,
            }
        )

    def phase(self, name: str, t: float, text: str) -> None:
        self.phases.append({"name": name, "t": _r(t), "text": text})

    def ball_at(self, t: float, x: float, y: float, h: float = 0.0) -> None:
        t = _r(t)  # compared as stored (see _Player.at)
        if self.ball and t < self.ball[-1][0]:
            return
        if self.ball and t == self.ball[-1][0]:
            self.ball[-1] = [t, _r(_x(x)), _r(_y(y)), _r(h)]
        else:
            self.ball.append([t, _r(_x(x)), _r(_y(y)), _r(h)])

    # -- pre-snap
    def setup(self) -> None:
        row, f = self.row, self.facts
        hash_ = _s(row.get("hash")) if self.ftn else None
        if hash_ in HASH_Y:
            self.ball_y = HASH_Y[hash_]
            f.known.append(f"The ball on the {hash_} hash (FTN)")
        else:
            self.ball_y = MIDDLE_Y
            hash_ = None
            f.approx.append("The ball drawn in the middle of the field (the hash isn't charted)")
        self.hash = hash_
        # the wide side of the field: away from the hash the ball is on
        self.field_side = "left" if hash_ == "right" else "right"

        align = _s(row.get("qb_alignment")) if self.ftn else None
        self.align_known = True
        if align in QB_DEPTH:
            f.known.append(f"QB {align.replace('_', ' ')} (FTN)")
        else:
            sg = row.get("shotgun")
            align = "shotgun" if sg else "under_center"
            if sg is None:
                self.align_known = False
                f.approx.append("QB drawn under center (the alignment isn't recorded)")
            else:
                f.known.append(
                    f"QB {'in the shotgun or pistol' if sg else 'under center'} (play-by-play)"
                )
        self.align = align

        pers = _personnel(row.get("personnel")) if self.part else None
        extra_ol = row.get("extra_ol") if self.part else None
        n_ol = 6 if extra_ol is True else 5
        if pers is not None:
            f.known.append(
                f"{row.get('personnel')} personnel: {pers[0]} back{'s' if pers[0] != 1 else ''}, "
                f"{pers[1]} tight end{'s' if pers[1] != 1 else ''}"
                + (", 6 linemen" if n_ol == 6 else "")
                + " (participation, research data)"
            )
        elif self.season is not None and self.season > LAST_PARTICIPATION:
            f.approx.append(
                "5 offensive linemen at standard splits (a sixth isn't charted until this "
                "season's personnel data comes, after it)"
            )
        else:
            f.approx.append(
                "5 offensive linemen at standard splits (no personnel recorded for this play)"
            )

        back_n = _i(row.get("backfield")) if self.ftn else None
        self.backs_known = back_n is not None and 0 <= back_n <= 4
        if back_n is not None and 0 <= back_n <= 4:
            f.known.append(f"{back_n} in the backfield besides the QB (FTN)")
        else:
            back_n = min(pers[0], 3) if pers is not None else 1
            f.approx.append(
                f"{back_n} back{'s' if back_n != 1 else ''} drawn "
                + ("from the personnel" if pers is not None else "(the backfield isn't charted)")
            )
        n_rec = max(0, 11 - n_ol - 1 - back_n)
        tes = min(pers[1], n_rec) if pers is not None else 0
        self._offense(n_ol, back_n, n_rec, tes, pers is not None)
        self._defense()
        self._motion()

    def _offense(self, n_ol: int, back_n: int, n_rec: int, tes: int, typed: bool) -> None:
        los, by = self.los, self.ball_y
        names = ["LT", "LG", "C", "RG", "RT"] if n_ol == 5 else ["LT", "LG", "C", "RG", "RT", "OL"]
        ys = _spread(by, 5, OL_SPACING)
        if n_ol == 6:  # the extra lineman on the wide side
            ys.append(by + (3 if self.field_side == "right" else -3) * OL_SPACING)
        self.ol = [
            self.add(
                f"o_{n.lower()}",
                "offense",
                "ol",
                n,
                los - 0.6,
                y,
                approx=(n != "C" or not self.hash),
            )
            for n, y in zip(names, ys, strict=True)
        ]
        qx = los - QB_DEPTH[self.align]
        self.qb = self.add(
            "o_qb", "offense", "qb", "QB", qx, by, approx=not (self.hash and self.align_known)
        )
        self.facts.approx.append(
            "The linemen beside the center, at standard 1.3-yard splits"
            + ("" if self.hash else "; the center and the QB with the ball, in the middle")
        )
        self.backs: list[_Player] = []
        if back_n:
            # 2023 tracking: the single back 7.5 behind the line under center and in the
            # pistol, 5.1 deep and 2.5 beside the QB in the shotgun; an offset fullback 4.3 deep
            # and 2.1 over; a wing back 2.7 deep and 4-4.6 wide
            fs = 1.0 if self.field_side == "right" else -1.0
            if self.align == "under_center":
                spots = {
                    1: [(7.5, 0.0)],
                    2: [(4.3, 2.1 * fs), (7.5, 0.0)],
                    3: [(4.3, -2.1), (4.3, 2.1), (7.5, 0.0)],
                }
            elif self.align == "pistol":
                spots = {
                    1: [(7.5, 0.0)],
                    2: [(2.7, 4.0 * fs), (7.5, 0.0)],
                    3: [(2.7, -4.0), (2.7, 4.0), (7.5, 0.0)],
                }
            else:
                side = -2.5 * fs  # the back to the boundary
                spots = {
                    1: [(5.0, side)],
                    2: [(5.0, -2.5), (5.0, 2.5)],
                    3: [(5.0, -2.5), (5.0, 2.5), (2.7, 4.6 * fs)],
                }
            for k, (dx, dy) in enumerate(spots[min(back_n, 3)] + [(7.5, 0.0)] * max(0, back_n - 3)):
                self.backs.append(
                    self.add(
                        f"o_rb{k + 1}", "offense", "back", "RB", los - dx, by + dy, approx=True
                    )
                )
        # receivers: tight ends inline first, then wide / slot alternating, the wide side first
        self.receivers: list[_Player] = []
        field_sgn = 1 if self.field_side == "right" else -1
        sides = [field_sgn, -field_sgn]
        inline = [by + s * (2.5 * OL_SPACING + 1.3) for s in sides]
        if n_ol == 6:
            inline = [y + s * OL_SPACING for y, s in zip(inline, sides, strict=True)]
        wide = {1: FIELD_WIDTH - WIDE_Y, -1: WIDE_Y}
        slot = {1: FIELD_WIDTH - SLOT_Y, -1: SLOT_Y}
        slot2 = {1: FIELD_WIDTH - SLOT2_Y, -1: SLOT2_Y}
        rec_spots: list[tuple[str, float, float]] = []
        for k in range(tes):
            rec_spots.append(("TE", los - 0.6, inline[k % 2] + (k // 2) * sides[k % 2] * 1.2))
        wr_spots = [
            (los - 0.5, wide[sides[0]]),
            (los - 0.5, wide[sides[1]]),
            (los - 1.2, slot[sides[0]]),
            (los - 1.2, slot[sides[1]]),
            (los - 1.2, slot2[sides[0]]),
        ]
        for k in range(n_rec - tes):
            x, y = wr_spots[k % len(wr_spots)]
            rec_spots.append(("WR" if typed else "R", x, y))
        for k, (lbl, x, y) in enumerate(rec_spots):
            self.receivers.append(
                self.add(f"o_r{k + 1}", "offense", "receiver", lbl, x, y, approx=True)
            )
        if n_rec:
            self.facts.approx.append(
                f"Where the {n_rec} receiver{'s' if n_rec != 1 else ''} line up: generic wide, "
                "slot and inline spots"
                + ("" if typed else " (tight ends and receivers aren't told apart this season)")
            )

    def _defense(self) -> None:
        row, f, los, by = self.row, self.facts, self.los, self.ball_y
        box = _i(row.get("box")) if self.ftn else None
        src = "FTN"
        if box is None and self.part:
            box, src = _i(row.get("part_box")), "participation"
        if box is not None and 1 <= box <= 11:
            f.known.append(f"{box} defenders in the box ({src})")
            box_known = True
        else:
            box, box_known = 7, False
            f.approx.append("7 defenders drawn in the box (the box count isn't charted)")
        dl_n = min(4, box) if box <= 7 else (5 if box == 8 else 6)
        lb_n = box - dl_n
        self.dl = [
            self.add(f"d_dl{k + 1}", "defense", "dl", "DL", los + DL_X, y, approx=True)
            for k, y in enumerate(_spread(by, dl_n, 1.7))
        ]
        self.lb = [
            self.add(f"d_lb{k + 1}", "defense", "lb", "LB", los + LB_X, y, approx=True)
            for k, y in enumerate(_spread(by, lb_n, 3.2))
        ]
        if box_known:
            f.approx.append(
                f"Where the {box} box defenders stand: {dl_n} on the line, {lb_n} off it "
                "(the count is charted, the split and the spots aren't)"
            )
        off_n = 11 - box
        cov = _s(row.get("coverage")) if self.part else None
        deep_n = SHELL_DEEP.get(cov or "")
        if cov:
            mz = _s(row.get("man_zone"))
            f.known.append(
                f"Coverage: {COVERAGE_NAME.get(cov, cov)}"
                + (f", {mz}" if mz in ("man", "zone") else "")
                + " (participation, research data)"
            )
        if deep_n is None:
            deep_n = min(2, max(0, off_n - 2))
            shell_txt = "two deep safeties as a generic shell"
        else:
            shell_txt = f"the safeties by the {COVERAGE_NAME[cov]} shell"
        # corners over the widest receiver on each side, then safeties, then overhangs
        wides = sorted(self.receivers, key=lambda p: p.y)
        self.dbs: list[_Player] = []
        corner_spots: list[float] = []
        if off_n >= 1:
            corner_spots.append((wides[0].y + 0.8) if wides else 8.0)
        if off_n >= 2:
            corner_spots.append((wides[-1].y - 0.8) if len(wides) > 1 else FIELD_WIDTH - 8.0)
        for k, y in enumerate(corner_spots):
            self.dbs.append(
                self.add(f"d_cb{k + 1}", "defense", "db", "CB", los + CB_X, y, approx=True)
            )
        left = off_n - len(corner_spots)
        deep_n = min(deep_n, left)
        for k, y in enumerate([MIDDLE_Y] if deep_n == 1 else _spread(MIDDLE_Y, deep_n, 18.0)):
            self.dbs.append(
                self.add(f"d_s{k + 1}", "defense", "db", "S", los + SAFETY_X, y, approx=True)
            )
        rest = left - deep_n
        slots = sorted(
            (p for p in self.receivers if p not in (wides[:1] + wides[-1:])),
            key=lambda p: abs(p.y - by),
        )
        for k in range(rest):
            y = slots[k].y if k < len(slots) else by + (8.0 if k % 2 == 0 else -8.0)
            self.dbs.append(
                self.add(f"d_db{k + 1}", "defense", "db", "DB", los + OVERHANG_X, y, approx=True)
            )
        if off_n:
            f.approx.append(
                f"Where the {off_n} defenders outside the box stand: corners over the widest "
                f"receivers, {shell_txt}"
            )
        self.box_known = box_known

    def _motion(self) -> None:
        row, f = self.row, self.facts
        motion = row.get("motion") if self.ftn else None
        self.motion = motion is True
        if motion is None:
            if self.season is not None and self.season < 2022:
                f.unknown.append("Motion before the snap (not charted before 2022)")
            return
        f.known.append(
            "Motion before the snap (FTN)" if motion else "No motion before the snap (FTN)"
        )
        if not motion or not self.receivers:
            return
        movers = sorted(self.receivers, key=lambda p: (p.label == "TE", abs(p.y - self.ball_y)))
        m = movers[0]
        to_y = m.y + (6.0 if m.y < self.ball_y else -6.0)  # towards the ball
        m.at(MOTION_T[0], m.x, m.y)
        m.at(MOTION_T[1], m.x, to_y)
        self.mark(
            "motion",
            [(m.x, m.y), (m.x, to_y)],
            MOTION_T[0],
            True,
            "Motion (who moved isn't charted)",
        )
        f.approx.append("Who went in motion, and where")
        self.phase("motion", MOTION_T[0], "Motion before the snap")

    # -- after the snap
    def snap(self) -> None:
        los, by = self.los, self.ball_y
        self.ball_at(0.0, los, by, 0.0)
        self.ball_at(SNAP_T, los, by, 0.0)
        # only what the data says: drawn defaults (a box of 7, one back) stay out of the text
        bits = [f"QB {self.align.replace('_', ' ')}"] if self.align_known else []
        if self.backs_known:
            n = len(self.backs)
            bits.append("empty backfield" if n == 0 else f"{n} back{'s' if n != 1 else ''}")
        if self.box_known:
            bits.append(f"{len(self.dl) + len(self.lb)} in the box")
        if self.hash:
            bits.append(f"ball on the {self.hash} hash")
        self.phases[0]["text"] = (
            "Before the snap: " + ", ".join(bits) if bits else "Before the snap"
        )
        self.phase("snap", SNAP_T, "Snap")
        qx, qy = self.qb.x, self.qb.y
        t_qb = SNAP_T + EXCHANGE_S
        self.ball_at(t_qb, qx, qy, 1.0)
        self.t_qb = t_qb

    def _rushers(self, target: tuple[float, float], t_end: float) -> int | None:
        row, f = self.row, self.facts
        r = _i(row.get("rushers")) if self.ftn else None
        src = "FTN"
        if r is None and self.part:
            r, src = _i(row.get("part_rushers")), "participation"
        if r is None or not 0 < r <= 11:
            f.unknown.append("How many rushed the passer")
            return None
        f.known.append(f"{r} pass rushers ({src})")
        pool = (
            self.dl
            + sorted(self.lb, key=lambda p: abs(p.y - self.ball_y))
            + sorted(self.dbs, key=lambda p: (p.label != "DB", abs(p.y - self.ball_y)))
        )
        tx, ty = target
        for p in pool[:r]:
            p.rusher = True
            # stop a step short of the QB, from the rusher's side
            dx, dy = tx - p.x, ty - p.y
            d = math.hypot(dx, dy) or 1.0
            stop = 2.0 / d  # rushers fan around the QB instead of stacking on him
            p.at(SNAP_T, p.x, p.y)
            p.at(t_end, tx - dx * stop, ty - dy * stop)
        if r > len(self.dl) + len(self.lb):
            f.approx.append("Which defenders from outside the box rushed")
        return r

    def _ol_steps(self, run: bool, t: float, lane_y: float | None = None) -> None:
        for p in self.ol:
            p.at(SNAP_T, p.x, p.y)
            if run:
                dy = 0.4 if lane_y is not None and lane_y > p.y else -0.4
                p.at(t, p.x + 1.0, p.y + (dy if lane_y is not None else 0.0))
            else:
                p.at(t, p.x - 1.2, p.y)

    def _drop(self, quick: bool) -> tuple[float, float, float]:
        """The QB's drop (with a play-action fake first): returns (t_set, x, y)."""
        row, by = self.row, self.ball_y
        q = self.qb
        t = self.t_qb
        q.at(SNAP_T, q.x, q.y)
        if row.get("play_action") is True and self.backs:
            b = self.backs[-1]
            mesh = (self.los - 3.5, by + (0.8 if b.y >= by else -0.8))
            q.at(t, q.x, q.y)
            q.at(t + FAKE_S, mesh[0], mesh[1])
            b.at(SNAP_T, b.x, b.y)
            b.at(t + FAKE_S, mesh[0] + 0.3, mesh[1] + (0.9 if b.y >= by else -0.9))
            b.at(t + FAKE_S + 0.5, self.los + 0.5, mesh[1] + (1.5 if b.y >= by else -1.5))
            self.mark("fake", [(q.x, q.y), mesh], t, True, "Play-action fake")
            self.phase("fake", t, "Play-action fake")
            self.ball_at(t + FAKE_S, mesh[0], mesh[1], 1.0)
            t += FAKE_S
        depth = DROP_DEPTH[self.align] - (1.0 if quick else 0.0)
        x = self.los - depth
        y = by
        if row.get("out_of_pocket") is True:
            side = _s(row.get("pass_location")) or _s(row.get("run_location")) or self.field_side
            y = by + (-7.0 if side == "left" else 7.0 if side == "right" else 0.0)
            self.facts.known.append("The QB left the pocket (FTN)")
        t_set = t + DROP_S
        q.at(t_set, x, y)
        self.ball_at(t_set, x, y, 1.0)
        return t_set, x, y

    def _pos(self, player_id: Any) -> str | None:
        """A player's position group from the rosters ('QB', 'RB', 'WR', 'TE' ...), if known."""
        pid = _s(player_id)
        pos = self.positions.get(pid) if pid else None
        return "RB" if pos == "FB" else pos

    def _target(self, cx: float, cy: float, behind: bool) -> _Player | None:
        """The receiver drawn as the target: of the targeted player's position when the rosters
        know it (a back, a tight end, a wide receiver), else by the throw's depth; then the one
        nearest the catch spot across the field. The spot is generic either way."""
        pos = self._pos(self.row.get("receiver_id"))
        recv = list(self.receivers)
        if pos == "RB":
            pool = list(self.backs) or recv
        elif pos == "TE":
            pool = [p for p in recv if p.label == "TE"] or recv or list(self.backs)
        elif pos == "WR":
            pool = [p for p in recv if p.label != "TE"] or recv or list(self.backs)
        else:
            pool = recv + (list(self.backs) if behind or not recv else [])
        if pos in POSITION_NAME:
            self.facts.known.append(
                f"The target was a {POSITION_NAME[pos]} (play-by-play, rosters)"
            )
        if not pool:
            return None
        target = min(pool, key=lambda p: (abs(p.last()[2] - cy), p.role != "receiver"))
        if pos in ("WR", "TE", "RB") and target.label == "R":
            target.label = pos  # who was targeted is known even where receiver types aren't
        return target

    def _route(
        self, p: _Player, cx: float, cy: float, t0: float, t1: float
    ) -> list[tuple[float, float]]:
        """The target's path from its spot to the catch: a stem and a break shaped by the route
        name when participation has it (2016-2025), else a straight line. Approximate."""
        _, x0, y0 = p.last()
        route = _s(self.row.get("target_route")) if self.part else None
        los = self.los
        pts: list[tuple[float, float]] = [(x0, y0)]
        if route in ("go", "fade", "seam"):
            pts.append((cx, cy))
        elif route in ("post", "corner"):
            brk = max(los + 6.0, cx - 7.0)
            pts += [(brk, y0), (cx, cy)]
        elif route in ("slant",):
            pts += [(los + 1.5, y0), (cx, cy)]
        elif route in ("hitch", "hitch_curl", "curl"):
            pts += [(cx + 1.5, y0), (cx, cy)]
        elif route in (
            "out",
            "in",
            "in_dig",
            "quick_out",
            "deep_out",
            "cross",
            "shallow_cross_drag",
            "angle",
            "texas_angle",
        ):
            pts += [(cx, y0), (cx, cy)]
        elif route in ("flat", "swing", "screen"):
            pts += [(x0, cy), (cx, cy)]
        elif route == "wheel":
            pts += [(los, cy), (cx, cy)]
        else:
            pts.append((cx, cy))
        # keyframes spread over the path's length
        seg = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:], strict=False)]
        total = sum(seg) or 1.0
        acc = 0.0
        p.at(SNAP_T, x0, y0)
        for (x, y), s in zip(pts[1:], seg, strict=True):
            if s < 1e-6:  # a zero-length leg (a back already at the catch depth)
                continue
            acc += s
            p.at(t0 + (t1 - t0) * acc / total, x, y)
        name = route.replace("_", " ") if route else None
        if name:
            self.facts.known.append(f"The target's route: {name} (participation, research data)")
        return pts

    def _throw(self, t_rel: float, qx: float, qy: float, cx: float, cy: float) -> float:
        dist = math.hypot(cx - qx, cy - qy)
        flight = 0.35 + dist / 45.0
        peak = min(6.0, 1.0 + dist / 8.0)
        for k in range(9):
            s = k / 8
            self.ball_at(
                t_rel + flight * s,
                qx + (cx - qx) * s,
                qy + (cy - qy) * s,
                1.0 + 4 * peak * s * (1 - s),
            )
        return t_rel + flight

    def _end(self, t: float, x: float, y: float, text: str) -> None:
        self.mark("end", [(x, y)], t, False, text)
        self.phase("end", t, text)

    def after_snap(self) -> None:
        kind, row = self.kind, self.row
        if kind == "aborted":
            self.facts.known.append("The snap was aborted (play-by-play)")
            self.phase("end", self.t_qb, "Aborted snap")
            return
        if kind == "run":
            self._run()
        else:
            self._dropback()
        y = _f(row.get("yards_gained"))
        if y is not None and kind != "pass":
            self.facts.known.append(f"The gain: {_yards(y)} (play-by-play)")

    def _run(self) -> None:
        row, f, los, by = self.row, self.facts, self.los, self.ball_y
        loc, gap = _s(row.get("run_location")), _s(row.get("run_gap"))
        if loc == "middle":
            off, where = 0.0, "up the middle"
        elif loc in ("left", "right") and gap in LANE_OFFSET:
            sgn = -1 if loc == "left" else 1
            off, where = sgn * LANE_OFFSET[gap], f"{loc} {gap}"
        elif loc in ("left", "right"):
            off, where = (-3.5 if loc == "left" else 3.5), loc
        else:
            off, where = 0.0, None
        if where:
            f.known.append(f"The run's lane: {where} (play-by-play)")
        else:
            f.unknown.append("The run's direction (not recorded for this play)")
        lane = (los + 0.5, by + off)
        rusher = _s(row.get("rusher_id"))
        pos = self._pos(rusher)
        # a QB run: the rosters say QB, or the carrier threw a pass this game and isn't listed
        # as a back or receiver (a running back's halfback pass doesn't make his runs QB runs)
        qb_run = rusher is not None and (
            pos == "QB" or (rusher in self.qb_ids and pos not in ("RB", "WR"))
        )
        # a receiver's carry (a jet sweep, an end-around): the motion man, else the receiver
        # nearest the lane; the rosters say who carried, the data doesn't say how he got there
        sweeper = None
        split_back = pos == "RB" and not self.backs  # an empty set: the back lined up wide
        if not qb_run and (pos in ("WR", "TE") or split_back) and self.receivers:
            moved = [p for p in self.receivers if len(p.keys) > 1]
            sweeper = (
                moved[0]
                if moved
                else min(self.receivers, key=lambda p: abs(p.last()[2] - (by + off)))
            )
        if pos in POSITION_NAME and not qb_run:
            f.known.append(f"The ball carrier was a {POSITION_NAME[pos]} (play-by-play, rosters)")
        y = _f(row.get("yards_gained")) or 0.0
        end_x = _clamp(los + y, -9.5, 109.5)
        if offense_touchdown(row):
            end_x = max(end_x, 100.5)
        end_y = lane[1] + (0.25 * (MIDDLE_Y - lane[1]) if abs(y) > 5 else 0.0)
        q = self.qb
        q.at(SNAP_T, q.x, q.y)
        if qb_run or (not self.backs and sweeper is None):
            carrier, t_lane = q, self.t_qb + 0.5
            q.at(self.t_qb, q.x, q.y)
            self.phase("carry", self.t_qb, "The QB keeps it")
            if qb_run:
                f.known.append("The QB carried it (play-by-play)")
        else:
            carrier = sweeper if sweeper is not None else self.backs[-1]
            mesh = (los - 3.0, by + (0.6 if off >= 0 else -0.6))
            t_mesh = self.t_qb + 0.4
            q.at(self.t_qb, q.x, q.y)
            q.at(t_mesh, mesh[0] + 0.2, mesh[1] - (0.6 if off >= 0 else -0.6))
            _, cx0, cy0 = carrier.last()
            carrier.at(SNAP_T, cx0, cy0)
            carrier.at(t_mesh, *mesh)
            self.ball_at(t_mesh, *mesh, 1.0)
            self.mark("handoff", [mesh], t_mesh, True, "Handoff")
            self.phase("handoff", t_mesh, "Handoff")
            q.at(t_mesh + 0.6, mesh[0] - 1.5, mesh[1] - (2.0 if off >= 0 else -2.0))
            t_lane = t_mesh + 0.45
        carrier.carrier = True
        via = (min(lane[0], end_x), lane[1])  # a loss stops behind the line (Sol, PC03)
        carrier.at(t_lane, *via)
        self.ball_at(t_lane, *via, 1.0)
        t_end = t_lane + max(0.5, abs(end_x - via[0]) / 9.0)
        carrier.at(t_end, end_x, end_y)
        self.ball_at(t_end, end_x, end_y, 1.0)
        self._ol_steps(True, t_lane, lane[1])
        self.mark(
            "lane", [(los - 1.5, lane[1]), lane], t_lane, False, f"Run {where}" if where else None
        )
        self.mark("run", [via, (end_x, end_y)], t_lane, True, None)
        f.approx.append("The ball carrier's path after the line (straight to where the play ended)")
        self._end(t_end, end_x, end_y, result_of(row)["text"])

    def _dropback(self) -> None:
        row, f, los = self.row, self.facts, self.los
        air = _f(row.get("air_yards"))
        loc = _s(row.get("pass_location"))
        quick = air is not None and air <= 5 and row.get("screen") is not True
        t_set, qx, qy = self._drop(quick)
        self._ol_steps(False, t_set)
        f.approx.append("The QB's drop and the linemen's steps: generic")
        y = _f(row.get("yards_gained")) or 0.0
        q = self.qb
        if self.kind == "sack":
            sx = los + y
            t_sack = t_set + 0.6
            self._rushers((sx, qy), t_sack)
            q.at(t_sack, sx, qy)
            self.ball_at(t_sack, sx, qy, 1.0)
            self.mark("sack", [(sx, qy)], t_sack, False, result_of(row)["text"])
            self.phase("end", t_sack, result_of(row)["text"])
            return
        self._rushers((qx, qy), t_set + 0.4)
        if self.kind == "scramble":
            loc_r = _s(row.get("run_location")) or "middle"
            lane_y = self.ball_y + SCRAMBLE_OFFSET.get(loc_r, 0.0)
            ex = _clamp(los + y, -9.5, 109.5)
            if offense_touchdown(row):
                ex = max(ex, 100.5)
            t_line = t_set + 0.7
            t_end = t_line + max(0.5, abs(ex - los) / 9.0)
            q.carrier = True
            line_x = min(los + 0.5, ex)  # a scramble for a loss stops behind the line
            q.at(t_line, line_x, lane_y)
            q.at(t_end, ex, lane_y)
            self.ball_at(t_line, line_x, lane_y, 1.0)
            self.ball_at(t_end, ex, lane_y, 1.0)
            self.phase("scramble", t_set, f"The QB scrambles {loc_r}")
            self.mark("run", [(qx, qy), (line_x, lane_y), (ex, lane_y)], t_set, True, None)
            f.known.append(f"A scramble to the {loc_r} (play-by-play)")
            f.approx.append("The scramble's path")
            self._end(t_end, ex, lane_y, result_of(row)["text"])
            return
        if self.kind != "pass" or air is None or loc not in PASS_Y:
            f.unknown.append("Where the ball went (no direction or depth recorded)")
            self._end(t_set + 0.3, qx, qy, result_of(row)["text"])
            return
        cx = _clamp(los + air, -9.5, 109.5)
        cy = PASS_Y[loc]
        if air < 0:  # behind the line: towards the flat on that side
            cy = self.ball_y + (-9.0 if loc == "left" else 9.0 if loc == "right" else 0.0)
        length = _s(row.get("pass_length"))
        f.known.append(
            f"The throw: {air_text(air)}, {length + ' ' if length else ''}{loc} (play-by-play)"
        )
        f.approx.append("The catch spot within its third of the field")
        t_rel = t_set + 0.25
        target = self._target(cx, cy, air <= 1)
        t_catch = self._throw(t_rel, qx, qy, cx, cy)
        self.phase("throw", t_rel, f"Throw: {air_text(air)}, {loc}")
        if target is not None:
            target.target = True
            self._route(target, cx, cy, SNAP_T + 0.1, t_catch)
            f.approx.append("The target's path to the ball")
        self.mark("throw", [(qx, qy), (cx, cy)], t_rel, False, None)
        res = result_of(row)
        if res["kind"] == "complete":
            ex = _clamp(los + y, -9.5, 109.5)
            if offense_touchdown(row):
                ex = max(ex, 100.5)
            ey = cy + 0.3 * (MIDDLE_Y - cy) if abs(ex - cx) > 3 else cy
            t_end = t_catch + max(0.4, abs(ex - cx) / 9.0)
            self.phase("catch", t_catch, f"Caught at the {self._spot(cx)}")
            if target is not None:
                target.carrier = True
                target.at(t_end, ex, ey)
            self.ball_at(t_end, ex, ey, 1.0)
            if abs(ex - cx) > 0.5:
                self.mark("run", [(cx, cy), (ex, ey)], t_catch, True, None)
                f.approx.append("The run after the catch (straight to where the play ended)")
            self._end(t_end, ex, ey, res["text"])
            f.known.append(f"The gain: {_yards(y)} (play-by-play)")
        elif res["kind"] == "interception":
            self.mark("interception", [(cx, cy)], t_catch, False, "Intercepted")
            self.phase("end", t_catch, "Intercepted")
            f.unknown.append("The interception return")
        else:
            self.ball_at(t_catch + 0.3, cx + 1.5, cy, 0.0)
            self.mark("incomplete", [(cx, cy)], t_catch, False, "Incomplete")
            self.phase("end", t_catch, "Incomplete")

    def _spot(self, x: float) -> str:
        ytg = int(round(100 - x))
        if ytg <= 0:
            return "goal line"
        return spot_text(ytg, self.row.get("offense"), self.row.get("defense")) or "spot"

    # -- the answer
    def build(self) -> dict[str, Any]:
        self.setup()
        self.snap()
        self.after_snap()
        row, f = self.row, self.facts
        if self.part and _f(row.get("time_to_throw")) is not None and self.kind in ("pass", "sack"):
            f.known.append(f"Time to throw: {float(row['time_to_throw']):.2f} s (participation)")
        tags = tags_of(row)
        if row.get("aborted") and self.kind != "aborted":
            f.known.append("A fumbled snap, picked up by the QB (play-by-play)")
            f.approx.append("The fumbled snap itself isn't drawn")
        if "fake_kick" in tags:
            f.known.append("A fake from a punt or field-goal formation (play-by-play)")
            f.approx.append("The kicking formation is drawn as a standard offense")
        if row.get("aborted") and _FAKE_KICK.search(_s(row.get("desc")) or ""):
            f.known.append(
                "From a punt or field-goal formation, with a fumbled snap (play-by-play)"
            )
            f.approx.append("The kicking formation is drawn as a standard offense")
        if "direct_snap" in tags:
            f.approx.append("A direct snap to another player, drawn as a snap to the QB")
        if fumble_lost(row):
            f.known.append("A fumble the defense recovered (play-by-play)")
            f.unknown.append("Where the fumble happened and the return")
        f.unknown += [
            "Routes of the receivers who weren't targeted",
            "Coverage assignments (who covered whom)",
            "Blocking assignments",
            "Where the defenders went after the snap (beyond the rushers)",
        ]
        if not self.part:
            if self.season is not None and self.season >= 2026:
                f.unknown.append(
                    "Personnel and the coverage shell (this season's participation data is "
                    "published after the season, about February 2027)"
                )
            else:
                f.unknown.append("Personnel and the coverage shell")
        duration = max(
            [k[0] for p in self.players for k in p.keys]
            + [k[0] for k in self.ball]
            + [p["t"] for p in self.phases]
        )
        xs = [k[1] for p in self.players for k in (p.keys or [[0, p.x, p.y]])] + [
            k[1] for k in self.ball
        ]
        fd = None if row.get("goal_to_go") else _i(row.get("distance"))
        first_down = min(100.0, self.los + fd) if fd is not None else None
        lo = min(min(xs) - 3.0, self.los - 12.0)
        hi = max(max(xs) + 4.0, self.los + 20.0, (first_down or 0) + 3.0)
        sources = ["nflverse play-by-play"]
        if self.ftn:
            sources.append("FTN charting (via nflverse)")
        if self.part:
            sources.append("nflverse participation (research data)")
        return {
            "version": VERSION,
            "kind": "reconstruction",
            "label": LABEL,
            "play": summary(row),
            "field": {
                "los": _r(self.los),
                "first_down": _r(first_down) if first_down is not None else None,
                "goal": 100.0,
                "ball_y": _r(self.ball_y),
                "hash": self.hash,
                "width": _r(FIELD_WIDTH),
                "view": [_r(_clamp(lo, -10.0, 110.0)), _r(_clamp(hi, -10.0, 110.0))],
            },
            "players": [p.out() for p in self.players],
            "ball": self.ball,
            "marks": self.marks,
            "phases": sorted(self.phases, key=lambda p: p["t"]),
            "duration": _r(duration + HOLD_S),
            "known": list(dict.fromkeys(f.known)),
            "approximate": list(dict.fromkeys(f.approx)),
            "unknown": list(dict.fromkeys(f.unknown)),
            "sources": sources,
            "concept": concept_of(row),
        }


def diagram(
    row: dict[str, Any],
    qb_ids: Iterable[str] = (),
    positions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One play's reconstruction. `row` is a `plays_enriched` row as a dict; `qb_ids` are the
    offense's passers in that game (a designed run by one of them is a QB run); `positions`
    maps player ids to position groups from the rosters ('RB', 'WR', 'TE', 'QB'), so the
    target and the ball carrier are drawn as the right kind of player (optional)."""
    return _Diagram(row, qb_ids, positions).build()
