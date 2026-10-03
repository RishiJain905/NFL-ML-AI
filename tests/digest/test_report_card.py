"""The report card grades the previous week's saved predictions (P04 exit criterion)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from nflengine.digest.report_card import (
    PRED_FILE,
    WATCH_FILE,
    build_report_card,
    grade_games,
    week_dir,
)

UTC = dt.UTC
KICK = dt.datetime(2025, 10, 19, 17, 0, tzinfo=UTC)


def preds(week: int, rows: list[tuple], created: dt.datetime | None = None) -> pl.DataFrame:
    """rows: (game_id, home, away, home_win_prob, elo_prob, pred_home, pred_away)."""
    created = created or KICK - dt.timedelta(days=5)
    return pl.DataFrame(
        {
            "season": [2025] * len(rows),
            "week": [week] * len(rows),
            "game_id": [r[0] for r in rows],
            "home_team": [r[1] for r in rows],
            "away_team": [r[2] for r in rows],
            "variant": ["market"] * len(rows),
            "is_primary": [True] * len(rows),
            "home_win_prob": [r[3] for r in rows],
            "elo_prob": [r[4] for r in rows],
            "market_prob": [r[3] for r in rows],
            "pred_home_points": [r[5] for r in rows],
            "pred_away_points": [r[6] for r in rows],
            "kickoff_utc": [KICK] * len(rows),
            "created_at": [created] * len(rows),
        }
    )


def games(rows: list[tuple]) -> pl.DataFrame:
    """rows: (game_id, home_score, away_score)."""
    return pl.DataFrame(
        {
            "game_id": [r[0] for r in rows],
            "home_score": [r[1] for r in rows],
            "away_score": [r[2] for r in rows],
            "result": [r[1] - r[2] for r in rows],
        }
    )


def save(root: Path, week: int, df: pl.DataFrame, name: str = PRED_FILE) -> None:
    d = week_dir(root, 2025, week)
    d.mkdir(parents=True, exist_ok=True)
    df.write_parquet(d / name)


W7 = [
    ("g1", "BUF", "NYJ", 0.78, 0.70, 28.0, 17.0),  # BUF loses: the biggest miss
    ("g2", "KC", "DEN", 0.60, 0.55, 24.0, 20.0),  # KC wins
    ("g3", "SF", "LA", 0.40, 0.50, 20.0, 23.0),  # LA wins
]
R7 = [("g1", 17, 24), ("g2", 27, 20), ("g3", 20, 26)]


def test_first_week_and_missing_predictions(tmp_path: Path) -> None:
    g = games(R7)
    assert build_report_card(tmp_path, 2025, 1, g).card.status == "first_week"
    r = build_report_card(tmp_path, 2025, 8, g)
    assert r.card.status == "no_saved_predictions" and r.scorecard_row is None
    assert "week 7" in r.card.note


def test_scores_saved_week(tmp_path: Path) -> None:
    save(tmp_path, 7, preds(7, W7))
    r = build_report_card(tmp_path, 2025, 8, games(R7))
    c = r.card
    assert c.status == "scored" and c.scored_week == 7
    assert (c.picks_correct.value, c.picks_total.value) == (2, 3)
    expected = ((0.78 - 0) ** 2 + (0.60 - 1) ** 2 + (0.40 - 0) ** 2) / 3
    assert c.brier.value == pytest.approx(expected, abs=1e-4)
    assert c.biggest_miss.favored == "BUF" and c.biggest_miss.winner == "NYJ"
    assert c.biggest_miss.final_score == "24–17"
    assert c.points_mae.value == pytest.approx((11 + 7 + 3 + 0 + 0 + 3) / 6, abs=1e-2)
    assert r.scorecard_row["week"] == 7 and r.scorecard_row["games"] == 3


def test_predictions_made_after_kickoff_are_not_graded(tmp_path: Path) -> None:
    late = preds(7, W7[:1], created=KICK + dt.timedelta(hours=1))
    save(tmp_path, 7, pl.concat([late, preds(7, W7[1:])]))
    c = build_report_card(tmp_path, 2025, 8, games(R7)).card
    assert c.picks_total.value == 2 and c.not_graded == 1
    assert c.biggest_miss is None  # the BUF miss was predicted after kickoff

    save(tmp_path, 7, preds(7, W7, created=KICK + dt.timedelta(hours=1)))
    c = build_report_card(tmp_path, 2025, 8, games(R7)).card
    assert c.status == "no_saved_predictions" and "after kickoff" in c.note


def test_two_consecutive_weeks_season_to_date(tmp_path: Path) -> None:
    """Week 9's card grades exactly the saved week-8 file; season totals add week 7."""
    w8 = [("h1", "MIA", "NE", 0.55, 0.52, 21.0, 20.0)]
    save(tmp_path, 7, preds(7, W7))
    save(tmp_path, 8, preds(8, w8))
    g = games([*R7, ("h1", 30, 10)])
    c9 = build_report_card(tmp_path, 2025, 9, g).card
    assert c9.scored_week == 8 and c9.picks_total.value == 1 and c9.picks_correct.value == 1
    s = c9.season_to_date
    assert (s.weeks.value, s.picks_correct.value, s.picks_total.value) == (2, 3, 4)
    assert sum(b.games.value for b in c9.calibration) == 4


def test_watch_list_hits(tmp_path: Path) -> None:
    save(tmp_path, 7, preds(7, W7))
    watch = pl.DataFrame({"player_id": ["a", "b", "c"], "baseline": [50.0, 40.0, 30.0]})
    save(tmp_path, 7, watch, WATCH_FILE)

    def scorer(df: pl.DataFrame) -> pl.DataFrame:
        actual = {"a": 70.0, "b": 10.0, "c": None}
        return df.with_columns(
            pl.col("player_id").replace_strict(actual, return_dtype=pl.Float64).alias("actual")
        ).with_columns(
            pl.col("actual").is_not_null().alias("played"),
            (pl.col("actual") > pl.col("baseline")).alias("hit"),
        )

    c = build_report_card(tmp_path, 2025, 8, games(R7), score_watch=scorer).card
    assert (c.watchlist_hits.value, c.watchlist_total.value) == (1, 2)  # c didn't play


def test_grade_games_handles_ties() -> None:
    g = grade_games(preds(7, W7[:1]), games([("g1", 20, 20)]))
    row = g.row(0, named=True)
    assert row["home_win"] == 0.5 and row["pick_correct"] is None and row["fav_won"] is None


def test_missing_previous_week_keeps_season_totals(tmp_path: Path) -> None:
    """Sol #12: week 7 missing, week 6 saved -> the week-8 card still shows the season."""
    w6 = [("f1", "BUF", "NYJ", 0.7, 0.6, 24.0, 17.0)]
    save(tmp_path, 6, preds(6, w6))
    c = build_report_card(tmp_path, 2025, 8, games([*R7, ("f1", 20, 10)])).card
    assert c.status == "no_saved_predictions"
    assert c.season_to_date is not None and c.season_to_date.picks_total.value == 1
    assert sum(b.games.value for b in c.calibration) == 1


def test_watch_picks_made_after_kickoff_are_not_graded(tmp_path: Path) -> None:
    """Sol #2: a Saturday pick for a game played Thursday never counts."""
    from nflengine.digest.report_card import pre_kickoff

    w = pl.DataFrame(
        {
            "player_id": ["a", "b"],
            "created_at": [KICK - dt.timedelta(days=1), KICK + dt.timedelta(hours=1)],
            "kickoff_utc": [KICK, KICK],
        }
    )
    assert pre_kickoff(w)["player_id"].to_list() == ["a"]
