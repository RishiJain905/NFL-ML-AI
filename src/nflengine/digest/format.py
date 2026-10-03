"""Every number the digest shows is formatted here, and only here (P04 pitfall: number
formatting is the main source of check false positives).

Each helper returns a `Num` (raw value + display string). The checks parse the display
strings with `number_atoms`, using the same rules they apply to the prose, so a number
that comes out of this module always passes provenance.

Conventions:
- probabilities and rates as whole percents ("64%"); win probabilities capped to 1-99%
- points as whole numbers; margins as "Chiefs by 3" ("even" under half a point)
- Brier scores with 3 decimals; EPA with 2 decimals and a sign
- ranges with an en dash ("60–70%"); the checks treat any dash the same
"""

from __future__ import annotations

import math
import re

from nflengine.digest.names import nickname
from nflengine.digest.payload import Num

EN_DASH = "–"


def _ok(x: float | int | None) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def _round_half_up(x: float, ndigits: int = 0) -> float:
    q = 10**ndigits
    return math.floor(abs(x) * q + 0.5) / q * (1 if x >= 0 else -1)


def _int(x: float) -> int:
    return int(_round_half_up(x))


def ordinal_str(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


# ---- Num makers -------------------------------------------------------------------------------


def count(n: int | float) -> Num:
    return Num(value=int(n), display=str(int(n)))


def ordinal(n: int) -> Num:
    return Num(value=int(n), display=ordinal_str(int(n)))


def pct(p: float, *, cap: bool = False) -> Num:
    """0.643 -> "64%". `cap` keeps win probabilities within 1-99%."""
    v = _int(100 * p)
    if cap:
        v = min(max(v, 1), 99)
    return Num(value=round(float(p), 4), display=f"{v}%")


def share(p: float) -> Num:
    """A usage share (0-1) as a whole percent: 0.27 -> "27%"."""
    return pct(p)


def points(x: float) -> Num:
    return Num(value=round(float(x), 2), display=str(_int(x)))


def margin(home_margin: float, home: str, away: str) -> Num:
    """Expected home margin -> "Chiefs by 3" (the favored team's nickname)."""
    m = _int(abs(home_margin))
    if m == 0:
        return Num(value=round(float(home_margin), 2), display="even")
    team = home if home_margin > 0 else away
    return Num(value=round(float(home_margin), 2), display=f"{nickname(team)} by {m}")


def brier(x: float) -> Num:
    return Num(value=round(float(x), 4), display=f"{_round_half_up(x, 3):.3f}")


def amount(x: float, unit: str) -> Num:
    """A whole-number amount with its unit: (61.3, "receiving yards") -> "61 receiving yards"."""
    return Num(value=round(float(x), 2), display=f"{_int(x)} {unit}")


def weeks(n: int) -> Num:
    return Num(value=int(n), display=f"{int(n)} week{'s' if int(n) != 1 else ''}")


def points_error(x: float) -> Num:
    return Num(value=round(float(x), 2), display=f"{_round_half_up(x, 1):.1f} points")


def epa(x: float, unit: str = "EPA per play") -> Num:
    return Num(value=round(float(x), 4), display=f"{_round_half_up(x, 2):+.2f} {unit}")


def epa_change(delta: float, n_weeks: int) -> Num:
    """A rating *change*, worded so it can't be read as a level:
    -0.049 over 3 weeks -> "down 0.05 EPA per play over the last 3 weeks"."""
    word = "up" if delta > 0 else "down"
    size = f"{abs(_round_half_up(delta, 2)):.2f}"
    return Num(
        value=round(float(delta), 4),
        display=f"{word} {size} EPA per play over the last {n_weeks} weeks",
    )


def driver_change(unit: str, delta: float) -> Num:
    """A rating part's 3-week move in words.

    `delta` is the raw rating change: for offense + = better; for defense the rating is
    EPA **allowed**, so + = worse ("0.06 more EPA per dropback allowed").
    """
    per = "dropback" if unit.startswith("pass") else "rush"
    size = f"{abs(_round_half_up(delta, 2)):.2f}"
    if unit.endswith("defense"):
        word = "more" if delta > 0 else "fewer"
        text = f"{size} {word} EPA per {per} allowed"
    else:
        word = "more" if delta > 0 else "fewer"
        text = f"{size} {word} EPA per {per} on offense"  # says which unit (fact-check)
    return Num(value=round(float(delta), 4), display=text)


def yards(x: float, *, signed: bool = False, unit: str = "yards") -> Num:
    r = _round_half_up(x, 1)
    text = f"{r:+.1f}" if signed else f"{r:.1f}"
    return Num(value=round(float(x), 3), display=f"{text} {unit}")


def seconds(x: float) -> Num:
    return Num(value=round(float(x), 3), display=f"{_round_half_up(x, 2):.2f} seconds")


def pct_points(x: float) -> Num:
    return Num(value=round(float(x), 3), display=f"{_round_half_up(x, 1):+.1f} points")


METRIC_UNITS = ("yards", "seconds", "rate", "pct_points", "yards_per_carry", "yards_per_catch")


def metric(x: float, unit: str) -> Num:
    """Format a tracking / charting stat by its unit key (under-the-hood items)."""
    if unit == "yards":
        return yards(x)
    if unit == "seconds":
        return seconds(x)
    if unit == "rate":
        return pct(x)
    if unit == "pct_points":
        return pct_points(x)
    if unit == "yards_per_carry":  # complete phrase: GLM dropped "over expected" otherwise
        return yards(x, signed=True, unit="rush yards over expected per carry")
    if unit == "yards_per_catch":
        return yards(x, signed=True, unit="YAC over expected per catch")
    if unit == "count":
        return count(x)
    if unit == "count_avg":
        return Num(value=round(float(x), 3), display=f"{_round_half_up(x, 1):.1f}")
    raise ValueError(f"unknown unit {unit!r}")


def bucket(lo: float, hi: float) -> str:
    return f"{_int(100 * lo)}{EN_DASH}{_int(100 * hi)}%"


def text_num(display: str, value: float | int | None = None) -> Num:
    return Num(value=value, display=display)


# ---- parsing (shared with the checks) -----------------------------------------------------------

DASHES = "‐‑‒–—―−"
_DASH_RE = re.compile(f"[{DASHES}]")
# A number not glued to a word ("49ers", "v0", "2026_04_KC_LV" are not numbers), with an
# optional percent sign / word or ordinal suffix.
NUMBER_RE = re.compile(
    r"(?<![\w.])[+\-]?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?P<suffix>\s?%|\s?percent\b|\s?pct\b|st\b|nd\b|rd\b|th\b)?"
    r"(?![A-Za-z_\d]|\.\d)"
)


def normalize_text(text: str) -> str:
    """Unify dashes and non-breaking spaces before matching numbers or words."""
    return _DASH_RE.sub("-", text).replace(" ", " ").replace(" ", " ")


def number_atoms(text: str) -> list[str]:
    """Every number in a text as a normalized atom: sign dropped, thousands commas
    dropped, "%"/"percent" -> "%", ordinals kept ("28th"). "52-118" -> ["52", "118"];
    "11 of 15" -> ["11", "15"]."""
    out: list[str] = []
    for m in NUMBER_RE.finditer(normalize_text(text)):
        num = m.group("num").replace(",", "")
        if num.startswith("."):  # ".999" is 0.999: a bare decimal must not dodge the checks
            num = "0" + num
        suf = (m.group("suffix") or "").strip().lower()
        if suf in ("%", "percent", "pct"):
            num += "%"
        elif suf:
            num += suf
        out.append(num)
    return out
