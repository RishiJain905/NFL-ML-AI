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
- player stats with their label ("84 receiving yards", "5.3 tackles"); a projection's gap to
  its baseline and a driver's effect in words ("23 receiving yards above his baseline",
  "puts the projection 9 receiving yards above a typical player in his group"), never a bare
  sign (P06)
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
    # `+ 0.0` turns -0.0 into 0.0: a value that rounds to zero must never print "-0.00"
    return math.floor(abs(x) * q + 0.5) / q * (1 if x >= 0 else -1) + 0.0


def _int(x: float) -> int:
    return int(_round_half_up(x))


def ordinal_str(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


# ---- Num makers -------------------------------------------------------------------------------


def count(n: int | float) -> Num:
    return Num(value=int(n), display=str(int(n)))


def count_stat(n: int | float, label: str) -> Num:
    """A count with its unit: 9, "pressures" -> "9 pressures"; 1 -> "1 pressure"."""
    k = int(n)
    word = label[:-1] if k == 1 and label.endswith("s") else label
    return Num(value=k, display=f"{k} {word}")


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


def per_game(x: float, unit: str) -> Num:
    """A per-game average with one decimal: (8.24, "targets") -> "8.2 targets per game"."""
    return Num(value=round(float(x), 3), display=f"{_round_half_up(x, 1):.1f} {unit} per game")


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
    r = _round_half_up(x, 2)
    return Num(value=round(float(x), 4), display=f"{_signed(r, 2)} {unit}")


def _signed(r: float, decimals: int) -> str:
    """ "+0.12" / "-0.12"; a rounded zero has no sign ("0.00", P09: "-0.00 EPA per play")."""
    return f"{r:+.{decimals}f}" if r else f"{0:.{decimals}f}"


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
    return Num(value=round(float(x), 3), display=f"{_signed(_round_half_up(x, 1), 1)} points")


def pct_points_change(x: float) -> Num:
    """A rate's change worded with its direction, never a bare sign (P09 fact-check: "+7.0
    points" of pressure allowed read as good): 7.04 -> "up 7.0 points"."""
    r = _round_half_up(x, 1)
    text = f"{'up' if r > 0 else 'down'} {abs(r):.1f} points" if r else "0.0 points (no change)"
    return Num(value=round(float(x), 3), display=text)


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


# ---- player projections (P06) -------------------------------------------------------------------


def _stat_text(x: float, unit: str, *, signed: bool = True) -> str:
    """The number part of a player stat: yards whole, counts with one decimal unless whole,
    EPA with 2 decimals (and a sign unless `signed=False`)."""
    if unit == "epa":
        r = _round_half_up(x, 2)
        return _signed(r, 2) if signed else f"{abs(r):.2f}"
    if unit == "count":
        r = _round_half_up(x, 1)
        return str(int(r)) if r == int(r) else f"{r:.1f}"
    return str(_int(x))


def _label(label: str, text: str) -> str:
    """'1 tackles' -> '1 tackle', '1 carries' -> '1 carry' (labels are plural)."""
    if text != "1" or not label.endswith("s"):
        return label
    return label[:-3] + "y" if label.endswith("ies") else label[:-1]


def stat(x: float, label: str, unit: str = "yards") -> Num:
    """A player stat with its label (projections, baselines, actuals): (84.2, "receiving
    yards") -> "84 receiving yards"; counts keep one decimal unless whole ("5.3 tackles",
    "6 tackles"); EPA has a sign and 2 decimals ("+0.12 EPA per dropback")."""
    text = _stat_text(x, unit)
    return Num(value=round(float(x), 3), display=f"{text} {_label(label, text)}")


def stat_range(lo: float, hi: float, label: str, unit: str = "yards") -> Num:
    """A projection's P10-P90 range: "52–118 receiving yards" (value: the width)."""
    a, b = _stat_text(lo, unit), _stat_text(hi, unit)
    return Num(value=round(float(hi) - float(lo), 3), display=f"{a}{EN_DASH}{b} {label}")


def vs_baseline(diff: float, label: str, unit: str = "yards") -> Num:
    """Projection minus baseline in words, so no sign is left to read:
    "23 receiving yards above his baseline", "0.4 tackles below his baseline", or "level
    with his baseline" when it rounds to 0."""
    text = _stat_text(abs(diff), unit, signed=False)
    if float(text) == 0:
        return Num(value=round(float(diff), 3), display="level with his baseline")
    word = "above" if diff > 0 else "below"
    return Num(
        value=round(float(diff), 3), display=f"{text} {_label(label, text)} {word} his baseline"
    )


def driver_effect(contribution: float, label: str, unit: str = "yards") -> Num | None:
    """A SHAP driver's contribution in words, against what SHAP measures it from (the
    model's average prediction, i.e. a typical player in his position group, never his own
    baseline or form): "puts the projection 9 receiving yards above a typical player in his
    group" / "... 0.4 tackles below ..."; None when it rounds to 0 (nothing to say).
    The P06 fact-check found "his recent form raises the projection" read as "he's in form"
    for a player whose recent games were below his own baseline."""
    text = _stat_text(abs(contribution), unit, signed=False)
    if float(text) == 0:
        return None
    word = "above" if contribution > 0 else "below"
    return Num(
        value=round(float(contribution), 3),
        display=(
            f"puts the projection {text} {_label(label, text)} {word} a typical player in his group"
        ),
    )


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
