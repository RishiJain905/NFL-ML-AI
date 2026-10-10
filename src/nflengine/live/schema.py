"""The live-decision contract (LD00): the game state, eras, rules by season and feature lists.

Every model and the decision engine read a `GameState` from the **offense's** side: the team
with the ball about to snap. LD01 builds it from ESPN's feed (`live/state.py`); the backtests
build it from nflverse play-by-play (`live/data.py`).

Rules eras come from the LD00 probe (2026-10-10, `documentation/live-decisions/README.md` §8):
- touchback after a kickoff at the 20 (to 2015), the 25 (2016-2023), the 30 (2024, the new
  kickoff) and the 35 (2025+); the receiving team's mean start after a score moved from its
  own 23 (2010-15) to 25.4 (2018-23), 29.8 (2024) and 30.6 (2025);
- the extra point moved back in 2015 (99.3% made before, 93-96% since);
- overtime: 15 minutes to 2016, 10 minutes from 2017; from 2025 both teams get the ball in the
  regular season too (no 2025 OT game ended on the first possession; five did in 2024).
"""

from __future__ import annotations

import dataclasses
import math
import numbers
from dataclasses import dataclass

# --- eras -------------------------------------------------------------------------------------
# `era` is the coarse "how the game is played" bucket the learned models see (wp, gain, fg,
# pass). Each bucket holds at least two seasons, so a leave-one-season-out fit always has rows
# from the held-out season's era. Rule changes with a known mechanism (kickoff, extra point,
# overtime) are handled by explicit rules below, not by this feature.
ERA_STARTS = (2010, 2015, 2018, 2021, 2024)


def era_of(season: int) -> int:
    """0 = 2010-14, 1 = 2015-17, 2 = 2018-20, 3 = 2021-23, 4 = 2024+."""
    era = 0
    for i, start in enumerate(ERA_STARTS):
        if season >= start:
            era = i
    return era


def kickoff_era(season: int) -> str:
    """Where a kickoff touchback is spotted: tb20 (to 2015), tb25 (2016-23), tb30 (2024, the new
    kickoff), tb35 (2025+)."""
    if season <= 2015:
        return "tb20"
    if season <= 2023:
        return "tb25"
    if season == 2024:
        return "tb30"
    return "tb35"


def pat_era(season: int) -> str:
    """The extra point: `short` (a 20-yard kick, to 2014) or `long` (from the 15, 2015+)."""
    return "short" if season <= 2014 else "long"


def ot_minutes(season: int, playoffs: bool = False) -> int:
    """An overtime period: 15 minutes to 2016 and in the playoffs, 10 in the regular season
    from 2017."""
    return 15 if season <= 2016 or playoffs else 10


def ot_both_possess(season: int, playoffs: bool = False) -> bool:
    """Both teams get the ball in overtime: playoffs from 2022, regular season from 2025."""
    return season >= 2025 or (playoffs and season >= 2022)


INDOOR_ROOFS = ("dome", "closed")


def indoor(roof: str | None) -> int:
    return int((roof or "").lower() in INDOOR_ROOFS)


# --- the state --------------------------------------------------------------------------------
@dataclass(frozen=True)
class GameState:
    """One moment of a game from the offense's side (the team about to snap).

    `home` is +1 when the offense is at home, -1 when away, 0 at a neutral site, so the
    defense's view is simply `-home`. `spread` is the pre-game point spread **from the offense's
    side** (+ = the offense was favored; nflverse's `spread_line` is from the home side).
    `game_seconds` / `half_seconds` follow nflverse: in overtime both are the overtime clock.
    `receive_2h_ko` is 1 when the offense receives the second-half kickoff (first half only).
    Weather is optional; None means unknown (indoor games ignore it).
    """

    season: int
    score_diff: int
    game_seconds: float
    half_seconds: float
    down: int
    ydstogo: int
    yardline_100: int
    off_timeouts: int = 3
    def_timeouts: int = 3
    home: int = 0
    receive_2h_ko: int = 0
    spread: float = 0.0
    total: float = 44.0
    roof: str = "outdoors"
    ot: bool = False
    playoffs: bool = False
    wind: float | None = None
    temp: float | None = None

    def __post_init__(self) -> None:
        problems = check_state(self)
        if problems:
            raise ValueError("bad game state: " + "; ".join(problems))
        # whole-number floats (JSON 6.0) and numpy numbers become plain ints / floats
        for name in INT_FIELDS:
            object.__setattr__(self, name, int(getattr(self, name)))
        for name in NUMBER_FIELDS:
            object.__setattr__(self, name, float(getattr(self, name)))
        for name in ("wind", "temp"):
            v = getattr(self, name)
            object.__setattr__(self, name, None if v is None else float(v))

    @property
    def era(self) -> int:
        return era_of(self.season)

    @property
    def first_half(self) -> bool:
        """In the first half `game_seconds = half_seconds + 1800` (00:00 of the second quarter
        and the second half's opening snap both have 1800 game seconds left)."""
        return (not self.ot) and abs(self.game_seconds - self.half_seconds - 1800) <= 1

    def replace(self, **kw) -> GameState:
        return dataclasses.replace(self, **kw)

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> GameState:
        names = {f.name for f in dataclasses.fields(cls)}
        unknown = sorted(set(d) - names)
        if unknown:
            raise ValueError(f"unknown game-state fields: {', '.join(unknown)}")
        return cls(**d)


INT_FIELDS = (
    "season", "score_diff", "down", "ydstogo", "yardline_100", "off_timeouts", "def_timeouts",
    "home", "receive_2h_ko",
)  # fmt: skip
NUMBER_FIELDS = ("game_seconds", "half_seconds", "spread", "total")


def _type_problems(s: GameState) -> list[str]:
    out = []
    for name in INT_FIELDS:
        v = getattr(s, name)
        if isinstance(v, bool) or not isinstance(v, numbers.Real) or v != v or int(v) != v:
            out.append(f"{name} must be a whole number (got {v!r})")
    for name in NUMBER_FIELDS:
        v = getattr(s, name)
        if isinstance(v, bool) or not isinstance(v, numbers.Real) or v != v:
            out.append(f"{name} must be a number (got {v!r})")
    for name in ("wind", "temp"):
        v = getattr(s, name)
        if v is not None and (isinstance(v, bool) or not isinstance(v, numbers.Real) or v != v):
            out.append(f"{name} must be a number or null (got {v!r})")
    return out


def check_state(s: GameState) -> list[str]:
    """Input checks shared by every model: what a state must satisfy to be scored."""
    out = _type_problems(s)
    if out:
        return out
    if s.season < 2010 or s.season > 2100:
        out.append(f"season {s.season} outside 2010+")
    if s.down not in (1, 2, 3, 4):
        out.append(f"down {s.down} not 1-4")
    if not 1 <= s.yardline_100 <= 99:
        out.append(f"yardline_100 {s.yardline_100} not 1-99")
    if s.ydstogo < 1 or s.ydstogo > 99:
        out.append(f"ydstogo {s.ydstogo} not 1-99")
    elif s.ydstogo > s.yardline_100:
        out.append(f"ydstogo {s.ydstogo} beyond the goal line ({s.yardline_100} to go)")
    limit = ot_minutes(s.season, s.playoffs) * 60 if s.ot else 3600
    if not 0 <= s.game_seconds <= limit:
        out.append(f"game_seconds {s.game_seconds} not 0-{limit}")
    if not 0 <= s.half_seconds <= (limit if s.ot else 1800):
        out.append(f"half_seconds {s.half_seconds} out of range")
    if not s.ot:
        gap = s.game_seconds - s.half_seconds
        first = abs(gap - 1800) <= 1  # first half: game = half + 1800
        second = abs(gap) <= 1 and s.game_seconds <= 1800
        if not (first or second):
            out.append("half_seconds doesn't match game_seconds")
    for name in ("off_timeouts", "def_timeouts"):
        if getattr(s, name) not in (0, 1, 2, 3):
            out.append(f"{name} not 0-3")
    if s.home not in (-1, 0, 1):
        out.append("home must be -1, 0 or 1")
    if s.receive_2h_ko not in (0, 1):
        out.append("receive_2h_ko must be 0 or 1")
    if abs(s.spread) > 30:
        out.append(f"spread {s.spread} beyond 30")
    if not 20 <= s.total <= 80:
        out.append(f"total {s.total} not 20-80")
    if abs(s.score_diff) > 80:
        out.append(f"score_diff {s.score_diff} beyond 80")
    return out


# --- feature lists ----------------------------------------------------------------------------
def elapsed_share(game_seconds: float, ot: bool = False) -> float:
    """Share of regulation played (overtime counts as all of it)."""
    return 1.0 if ot else (3600.0 - game_seconds) / 3600.0


# nflfastR's WP terms (opensourcefootball 2020-09-28): the spread fades with time, the score
# difference matters more as time runs out.
WP_FEATURES = [
    "score_diff",
    "diff_time_ratio",
    "spread",
    "spread_time",
    "game_seconds",
    "half_seconds",
    "yardline_100",
    "down",
    "ydstogo",
    "off_timeouts",
    "def_timeouts",
    "home",
    "receive_2h_ko",
    "ot",
    "era",
    "indoor",
]
# + = WP rises with the feature (LightGBM `monotone_constraints`)
WP_MONOTONE = {"score_diff": 1, "diff_time_ratio": 1, "spread": 1, "spread_time": 1}

GAIN_FEATURES = [
    "down",
    "ydstogo",
    "yardline_100",
    "spread",
    "total",
    "team_total",
    "era",
    "indoor",
]
# optional (backtest decides, LD00): opponent-adjusted EPA ratings as of the week, read from
# the published `features/team_ratings.parquet` (never written)
GAIN_RATING_FEATURES = ["off_epa", "def_epa_opp"]

PASS_FEATURES = [
    "down",
    "ydstogo",
    "yardline_100",
    "score_diff",
    "game_seconds",
    "half_seconds",
    "off_timeouts",
    "def_timeouts",
    "wp",
    "spread",
    "era",
    "indoor",
    "home",
]

# --- the yards-gained classes (nfl4th's 76 + a touchdown class) -------------------------------
GAIN_MIN = -10
GAIN_MAX = 65
GAIN_VALUES = list(range(GAIN_MIN, GAIN_MAX + 1))  # class i = GAIN_MIN + i yards
TD_CLASS = len(GAIN_VALUES)  # 76
N_GAIN_CLASSES = TD_CLASS + 1  # 77


def gain_class(yards: float, touchdown: bool) -> int:
    if touchdown:
        return TD_CLASS
    return int(min(max(round(yards), GAIN_MIN), GAIN_MAX)) - GAIN_MIN


# --- field goals ------------------------------------------------------------------------------
FG_SNAP_YARDS = 18  # kick distance = yards to the goal line + 18 (90% of 2010-2026 kicks)
FG_MISS_SPOT_YARDS = 8  # a miss goes to the spot of the kick (the hold) or the 20
FG_MAX_DISTANCE = 70  # longer tries are not offered
FG_HINGES = (30, 40, 50, 60)  # piecewise-linear logit in distance (fg model)

SECONDS_PER_PLAY = 6.0  # clock run-off per play in the decision engine (nfl4th)


def state_features(s: GameState) -> dict[str, float]:
    """Every model input derived from one state (the training frames compute the same)."""
    share = elapsed_share(s.game_seconds, s.ot)
    decay = math.exp(-4.0 * share)
    return {
        "score_diff": float(s.score_diff),
        "diff_time_ratio": s.score_diff / decay,
        "spread": float(s.spread),
        "spread_time": s.spread * decay,
        "game_seconds": float(s.game_seconds),
        "half_seconds": float(s.half_seconds),
        "yardline_100": float(s.yardline_100),
        "down": float(s.down),
        "ydstogo": float(s.ydstogo),
        "off_timeouts": float(s.off_timeouts),
        "def_timeouts": float(s.def_timeouts),
        "home": float(s.home),
        "receive_2h_ko": float(s.receive_2h_ko),
        "ot": float(s.ot),
        "era": float(s.era),
        "indoor": float(indoor(s.roof)),
        "total": float(s.total),
        "team_total": s.total / 2.0 + s.spread / 2.0,
    }
