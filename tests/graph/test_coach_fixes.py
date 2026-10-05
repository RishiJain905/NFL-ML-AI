"""Head-coach corrections (P10): `config/head_coach_fixes.csv` read fail-soft and applied to
the schedules' coaches from a week on."""

from __future__ import annotations

import polars as pl

from nflengine.graph import tables_extra as X


def _games() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [2025, 2025, 2025, 2026, 2026],
            "week": [6, 7, 8, 1, 1],
            "home_team": ["TEN", "KC", "TEN", "ATL", "NO"],
            "away_team": ["LV", "TEN", "IND", "TB", "ATL"],
            "home_coach": [
                "Brian Callahan",
                "Andy Reid",
                "Brian Callahan",
                "Raheem Morris",
                "K. M.",
            ],
            "away_coach": [
                "Pete Carroll",
                "Brian Callahan",
                "Shane Steichen",
                "Todd Bowles",
                "Raheem Morris",
            ],
        }
    )


def test_apply_coach_fixes_from_a_week_on_both_sides() -> None:
    fixes = pl.DataFrame(
        {
            "season": [2025, 2026],
            "team": ["TEN", "ATL"],
            "from_week": [7, 1],
            "coach": ["Mike McCoy", "Kevin Stefanski"],
        },
        schema_overrides={"season": pl.Int32, "from_week": pl.Int32},
    )
    out = X.apply_coach_fixes(_games(), fixes)
    assert out["home_coach"].to_list() == [
        "Brian Callahan",  # week 6: before the change
        "Andy Reid",
        "Mike McCoy",
        "Kevin Stefanski",
        "K. M.",
    ]
    assert out["away_coach"].to_list() == [
        "Pete Carroll",
        "Mike McCoy",  # TEN away in week 7
        "Shane Steichen",
        "Todd Bowles",
        "Kevin Stefanski",
    ]
    assert X.apply_coach_fixes(_games(), fixes.clear()).equals(_games())


def test_read_coach_fixes_is_fail_soft(tmp_path) -> None:
    assert X.read_coach_fixes(tmp_path / "none.csv").is_empty()
    bad = tmp_path / "bad.csv"
    bad.write_text("season,team\nx,y\n")
    logged: list[str] = []
    assert X.read_coach_fixes(bad, log=logged.append).is_empty() and logged
    good = tmp_path / "good.csv"
    good.write_text(
        "# comment\nseason,team,from_week,coach,source\n2026, atl ,1, Kevin Stefanski ,x\n"
    )
    df = X.read_coach_fixes(good)
    assert df.to_dicts() == [
        {"season": 2026, "team": "ATL", "from_week": 1, "coach": "Kevin Stefanski"}
    ]


def test_the_shipped_fixes_file_parses() -> None:
    df = X.read_coach_fixes()
    assert df.height >= 6
    assert {"ATL", "ARI", "BUF", "LV"} <= set(df.filter(pl.col("season") == 2026)["team"])
