"""Report card: grade last week's **saved** predictions (documentation/06 -> Report card
details; documentation/08 -> Season scorecard).

- Reads `predictions_games.parquet` (and `watchlist.parquet`) from the previous week's run
  folder, never predictions recomputed later. The digest row of each game (`is_primary`)
  is graded.
- A prediction made at or after its game's kickoff is never graded (`not_graded`): the
  first live run of 2026 happened after the week-4 Thursday game, for example.
- Pick record: the side with probability > 50% (ties and exact 50% calls left out).
  Brier score next to Elo's on the same games (the market's too, for W&B only: the digest
  never quotes market numbers). Points error = mean absolute error of each team's score.
- Season to date: every saved week of the season before the digest week, plus calibration
  buckets on the favorite's probability.
- Player projections (P06): last week's watch-list picks projected vs actual vs range
  (`watch_lookback`), 2-3 accuracy-scoreboard highlights (`scoreboard_highlights`, always
  one weak spot) and the scorecard's `player_mae_vs_baseline` (see `digest/players.py`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.digest import format as F
from nflengine.digest.names import nickname
from nflengine.digest.payload import (
    BiggestMiss,
    CalibrationBucket,
    ReportCard,
    SeasonToDate,
)
from nflengine.digest.players import (
    scoreboard_highlights,
    side_tallies,
    watch_lookback,
    week_improvement,
)
from nflengine.models import metrics as M

PRED_FILE = "predictions_games.parquet"
WATCH_FILE = "watchlist.parquet"
CAL_BUCKETS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0))


def week_dir(run_root: Path, season: int, week: int) -> Path:
    return run_root / str(season) / f"week{week:02d}"


def load_saved(run_root: Path, season: int, weeks: Iterable[int], name: str) -> pl.DataFrame:
    frames = [
        pl.read_parquet(p) for w in weeks if (p := week_dir(run_root, season, w) / name).exists()
    ]
    return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()


def grade_games(preds: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """Digest rows of saved predictions joined to final scores; graded rows only.

    `games`: curated games (game_id, home_score, away_score, result). Adds `home_win`
    (1 / 0 / 0.5), `fav_prob`, `fav_won`, `pick_correct` (null for ties / 50% calls) and
    `graded` (final, and predicted before kickoff).
    """
    if preds.is_empty():
        return preds
    p = preds.filter(pl.col("is_primary")) if "is_primary" in preds.columns else preds
    g = games.select("game_id", "home_score", "away_score", "result")
    p = p.join(g, on="game_id", how="left")
    before = (
        pl.col("created_at") < pl.col("kickoff_utc") if "created_at" in p.columns else pl.lit(True)
    )
    p = p.with_columns(
        (pl.col("result").is_not_null() & before.fill_null(False)).alias("graded"),
        pl.when(pl.col("result") > 0)
        .then(1.0)
        .when(pl.col("result") < 0)
        .then(0.0)
        .otherwise(0.5)
        .alias("home_win"),
    )
    hp = pl.col("home_win_prob")
    return p.with_columns(
        pl.max_horizontal(hp, 1 - hp).alias("fav_prob"),
        pl.when(pl.col("home_win") == 0.5)
        .then(None)
        .when(hp > 0.5)
        .then(pl.col("home_win") == 1.0)
        .otherwise(pl.col("home_win") == 0.0)
        .alias("fav_won"),
        pl.when((pl.col("home_win") == 0.5) | (hp == 0.5))
        .then(None)
        .otherwise((hp > 0.5) == (pl.col("home_win") == 1.0))
        .alias("pick_correct"),
    )


def week_metrics(g: pl.DataFrame) -> dict[str, Any]:
    """Scorecard numbers for graded rows (one week or a season)."""
    g = g.filter(pl.col("graded"))
    picks = g.filter(pl.col("pick_correct").is_not_null())
    both = g.filter(pl.col("elo_prob").is_not_null())
    mkt = g.filter(pl.col("market_prob").is_not_null())
    pts = pl.concat(
        [
            (g["pred_home_points"] - g["home_score"]).abs(),
            (g["pred_away_points"] - g["away_score"]).abs(),
        ]
    )
    return {
        "games": g.height,
        "picks_correct": int(picks["pick_correct"].sum()) if picks.height else 0,
        "picks_total": picks.height,
        "brier_model": M.brier(both["home_win_prob"], both["home_win"]) if both.height else None,
        "brier_elo": M.brier(both["elo_prob"], both["home_win"]) if both.height else None,
        "brier_market": M.brier(mkt["market_prob"], mkt["home_win"]) if mkt.height else None,
        "logloss_model": M.log_loss(g["home_win_prob"], g["home_win"]) if g.height else None,
        "ece_model": M.ece(g["home_win_prob"], g["home_win"]) if g.height else None,
        "points_mae": float(pts.mean()) if g.height else None,
    }


def biggest_miss(g: pl.DataFrame) -> BiggestMiss | None:
    miss = g.filter(pl.col("graded") & (pl.col("fav_won") == False))  # noqa: E712
    if miss.is_empty():
        return None
    r = miss.sort(["fav_prob", "game_id"], descending=[True, False]).row(0, named=True)
    home_fav = r["home_win_prob"] > 0.5
    fav, win = (r["home_team"], r["away_team"]) if home_fav else (r["away_team"], r["home_team"])
    hi, lo = max(r["home_score"], r["away_score"]), min(r["home_score"], r["away_score"])
    return BiggestMiss(
        game_id=r["game_id"],
        game=f"{nickname(r['away_team'])} at {nickname(r['home_team'])}",
        home=r["home_team"],
        away=r["away_team"],
        favored=fav,
        favored_name=nickname(fav),
        prob=F.pct(r["fav_prob"], cap=True),
        winner=win,
        winner_name=nickname(win),
        final_score=f"{hi}{F.EN_DASH}{lo}",
    )


def calibration(g: pl.DataFrame) -> list[CalibrationBucket]:
    g = g.filter(pl.col("graded") & pl.col("fav_won").is_not_null())
    out = []
    for lo, hi in CAL_BUCKETS:
        b = g.filter((pl.col("fav_prob") >= lo) & (pl.col("fav_prob") < hi + 1e-9 * (hi == 1)))
        if b.height:
            out.append(
                CalibrationBucket(
                    bucket=F.bucket(lo, hi),
                    games=F.count(b.height),
                    favorite_wins=F.count(int(b["fav_won"].sum())),
                )
            )
    return out


def pre_kickoff(watch: pl.DataFrame) -> pl.DataFrame:
    """Watch-list picks made before their game started (lists saved before timestamps
    existed are kept as they are)."""
    if watch.is_empty() or not {"created_at", "kickoff_utc"} <= set(watch.columns):
        return watch
    return watch.filter(pl.col("created_at") < pl.col("kickoff_utc"))


def season_to_date(
    run_root: Path, season: int, week: int, games: pl.DataFrame, score_watch=None
) -> tuple[SeasonToDate | None, list[CalibrationBucket]]:
    """Totals over every saved, graded week of the season before `week` (None if none)."""
    season_preds = load_saved(run_root, season, range(1, week), PRED_FILE)
    if season_preds.is_empty():
        return None, []
    season_graded = grade_games(season_preds, games)
    done = season_graded.filter(pl.col("graded"))
    if done.is_empty():
        return None, []
    sm = week_metrics(season_graded)
    season_watch = pre_kickoff(load_saved(run_root, season, range(1, week), WATCH_FILE))
    sw = score_watch(season_watch) if (score_watch and season_watch.height) else pl.DataFrame()
    sw_played = sw.filter(pl.col("played")) if sw.height else sw
    std = SeasonToDate(
        weeks=F.count(done["week"].n_unique()),
        picks_correct=F.count(sm["picks_correct"]),
        picks_total=F.count(sm["picks_total"]),
        brier=F.brier(sm["brier_model"]),
        brier_elo=F.brier(sm["brier_elo"]),
        watchlist_hits=F.count(int(sw_played["hit"].sum())) if sw_played.height else None,
        watchlist_total=F.count(sw_played.height) if sw_played.height else None,
    )
    return std, calibration(season_graded)


@dataclass
class ReportCardResult:
    card: ReportCard
    scorecard_row: dict[str, Any] | None = None
    graded: pl.DataFrame = field(default_factory=pl.DataFrame)
    watch_scored: pl.DataFrame = field(default_factory=pl.DataFrame)


def build_report_card(
    run_root: Path,
    season: int,
    week: int,
    games: pl.DataFrame,
    score_watch=None,
    checks_passed: bool | None = None,
    *,
    scoreboard: pl.DataFrame | None = None,
    backtest_scoreboard: pl.DataFrame | None = None,
    mode: str = "live",
) -> ReportCardResult:
    """The report card for the digest of (season, week): grades week - 1.

    `score_watch(df) -> df` adds `played` / `hit` (and `inside` for model picks) to a saved
    watch list (`digest.players.WatchScorer`); injected so tests can run without the data
    drive. `scoreboard` (the live season's accuracy scoreboard) and `backtest_scoreboard`
    feed the player-projection highlights (P06; `players.scoreboard_highlights`) and the
    scorecard's `player_mae_vs_baseline`; without either, the card has no highlights.
    """
    highlights = (
        scoreboard_highlights(scoreboard, backtest_scoreboard, season, week, mode)
        if scoreboard is not None or backtest_scoreboard is not None
        else []
    )
    prev = week - 1
    if prev < 1:
        return ReportCardResult(
            ReportCard(
                status="first_week",
                note="week 1 has no earlier week to grade.",
                scoreboard_highlights=highlights,
            )
        )
    preds = load_saved(run_root, season, [prev], PRED_FILE)
    graded = grade_games(preds, games) if preds.height else preds
    n_graded = int(graded["graded"].sum()) if graded.height else 0
    std, calib = season_to_date(run_root, season, week, games, score_watch)
    if n_graded == 0:
        why = (
            f"no predictions were saved for week {prev}, so there is nothing to grade"
            if preds.is_empty()
            else f"the saved week {prev} predictions were all made after kickoff"
        )
        # the season so far still counts when one week is missing (Sol review)
        return ReportCardResult(
            ReportCard(
                status="no_saved_predictions",
                scored_week=prev,
                scored_week_display=F.count(prev),
                note=f"{why}; next week's report card grades this week's picks.",
                calibration=calib,
                season_to_date=std,
                scoreboard_highlights=highlights,
            )
        )
    wm = week_metrics(graded)
    watch = pre_kickoff(load_saved(run_root, season, [prev], WATCH_FILE))
    watch_scored = score_watch(watch) if (score_watch and watch.height) else pl.DataFrame()
    w_hits = w_total = w_inside = w_ranged = None
    if watch_scored.height:
        played = watch_scored.filter(pl.col("played"))
        w_total, w_hits = played.height, int(played["hit"].sum())
        if "inside" in played.columns:
            ranged = played.filter(pl.col("inside").is_not_null())
            w_ranged, w_inside = ranged.height, int(ranged["inside"].sum())
    lookback = watch_lookback(watch_scored) if "player" in watch_scored.columns else []
    sb = scoreboard if mode == "live" else backtest_scoreboard

    card = ReportCard(
        status="scored",
        scored_week=prev,
        scored_week_display=F.count(prev),
        picks_correct=F.count(wm["picks_correct"]),
        picks_total=F.count(wm["picks_total"]),
        brier=F.brier(wm["brier_model"]),
        brier_elo=F.brier(wm["brier_elo"]),
        points_mae=F.points_error(wm["points_mae"]),
        biggest_miss=biggest_miss(graded),
        watchlist_hits=F.count(w_hits) if w_total else None,
        watchlist_total=F.count(w_total) if w_total else None,
        not_graded=int(graded.height - n_graded),
        calibration=calib,
        season_to_date=std,
        scoreboard_highlights=highlights,
        watch_lookback=lookback,
        # only when every scored pick had a range (a mixed heuristic week shows none)
        watchlist_inside=F.count(w_inside) if w_ranged and w_ranged == w_total else None,
        watchlist_by_side=side_tallies(watch_scored),
    )
    row = {
        "season": season,
        "week": prev,
        **{k: wm[k] for k in ("games", "picks_correct", "picks_total")},
        **{k: wm[k] for k in ("brier_model", "brier_elo", "brier_market")},
        "logloss_model": wm["logloss_model"],
        "ece_model": wm["ece_model"],
        "points_mae": wm["points_mae"],
        "watchlist_hits": w_hits,
        "watchlist_total": w_total,
        "player_mae_vs_baseline": week_improvement(sb, season, prev, mode),
        "checks_passed": checks_passed,
        "not_graded": card.not_graded,
    }
    return ReportCardResult(card, row, graded, watch_scored)
