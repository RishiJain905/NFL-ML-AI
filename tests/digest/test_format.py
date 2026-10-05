"""Number formatting happens in one place, and its output always parses back (P04)."""

from __future__ import annotations

import pytest

from nflengine.digest import format as F
from nflengine.digest.facts import text_atoms


@pytest.mark.parametrize(
    "num, display",
    [
        (F.pct(0.643), "64%"),
        (F.pct(0.645), "65%"),  # half up, not banker's rounding
        (F.pct(0.999, cap=True), "99%"),
        (F.pct(0.002, cap=True), "1%"),
        (F.points(20.5), "21"),
        (F.margin(-2.6, "DEN", "KC"), "Chiefs by 3"),
        (F.margin(7.6, "BUF", "NE"), "Bills by 8"),
        (F.margin(0.4, "BUF", "NE"), "even"),
        (F.brier(0.20125), "0.201"),
        (F.points_error(7.46), "7.5 points"),
        (F.epa(0.07), "+0.07 EPA per play"),
        (F.epa(-0.049), "-0.05 EPA per play"),
        (F.epa(-0.003), "0.00 EPA per play"),  # P09: never "-0.00"
        (F.epa(0.004), "0.00 EPA per play"),
        (F.pct_points(-0.04), "0.0 points"),
        (F.pct_points_change(7.04), "up 7.0 points"),  # P09: direction, never a bare sign
        (F.pct_points_change(-12.25), "down 12.3 points"),
        (F.stat(-0.004, "EPA per dropback", "epa"), "0.00 EPA per dropback"),
        (F.driver_change("pass defense", 0.058), "0.06 more EPA per dropback allowed"),
        (F.driver_change("pass defense", -0.03), "0.03 fewer EPA per dropback allowed"),
        (F.driver_change("run offense", 0.035), "0.04 more EPA per rush on offense"),
        (F.metric(4.13, "yards"), "4.1 yards"),
        (F.metric(2.914, "seconds"), "2.91 seconds"),
        (F.metric(0.412, "rate"), "41%"),
        (F.metric(4.25, "pct_points"), "+4.3 points"),
        (F.metric(-0.84, "yards_per_carry"), "-0.8 rush yards over expected per carry"),
        (F.epa_change(-0.049, 3), "down 0.05 EPA per play over the last 3 weeks"),
        (F.metric(1.26, "count_avg"), "1.3"),
        (F.ordinal(3), "3rd"),
        (F.ordinal(11), "11th"),
        (F.ordinal(22), "22nd"),
        (F.amount(61.3, "receiving yards"), "61 receiving yards"),
        (F.weeks(3), "3 weeks"),
    ],
)
def test_displays(num, display: str) -> None:
    assert num.display == display


def test_bucket_uses_en_dash() -> None:
    assert F.bucket(0.6, 0.7) == "60–70%"


@pytest.mark.parametrize(
    "text, atoms",
    [
        ("64%", ["64%"]),
        ("64 %", ["64%"]),
        ("64 percent", ["64%"]),
        ("52–118", ["52", "118"]),
        ("52 - 118", ["52", "118"]),
        ("11 of 15", ["11", "15"]),
        ("+0.07 EPA per play", ["0.07"]),
        ("−0.05", ["0.05"]),
        ("the 3rd-weakest", ["3rd"]),
        ("5+ targets", ["5"]),
        ("1,234 yards", ["1234"]),
        ("49ers and v0 and 2026_04_KC_LV", []),
        ("4.1.", ["4.1"]),
    ],
)
def test_number_atoms(text: str, atoms: list[str]) -> None:
    assert F.number_atoms(text) == atoms


def test_every_formatter_output_parses_to_its_own_atoms() -> None:
    nums = [
        F.pct(0.64),
        F.margin(-3.2, "DEN", "KC"),
        F.brier(0.2213),
        F.driver_change("pass offense", 0.031),
        F.metric(0.27, "rate"),
        F.amount(84, "receiving yards"),
        F.ordinal(28),
    ]
    for n in nums:
        assert text_atoms(n.display), n.display
    # a percent range also allows its first end with the % sign
    assert {"60", "70%", "60%"} <= text_atoms(F.bucket(0.6, 0.7))
