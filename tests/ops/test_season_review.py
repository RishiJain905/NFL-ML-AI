"""The end-of-season review (P10): what it reads, what it computes, how it words the weekly
lines, and that every section still renders when its input is missing. Small synthetic seasons
in `tmp_path`: no network, no data drive, no W&B."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import polars as pl
import pytest

from nflengine.digest.report_card import PRED_FILE, WATCH_FILE, week_dir
from nflengine.digest.run import SCORECARD_SCHEMA
from nflengine.ops import season_review as SR
from nflengine.ops.records import RUN_SUMMARY_FILE, append_history, history_path
from nflengine.paths import DataPaths, DataRootError

UTC = dt.UTC
SEASON = 2026
KICK0 = dt.datetime(2026, 9, 10, 17, tzinfo=UTC)
TEAMS = ["KC", "BUF", "SF", "DAL", "PHI", "MIA", "DET", "BAL"]  # four games a week
PROBS = [0.55, 0.65, 0.75, 0.85]  # home win probability of game i (the home team is the favorite)
ELO, MARKET = 0.6, 0.62

SECTIONS = [
    "# 2026 season review",
    "## How this review was made",
    "## The season at a glance",
    "## Game model, Elo and the market",
    "## Calibration",
    "## Player projections: the accuracy scoreboard",
    "## Watch list",
    "## Digest checks",
    "## Pipeline uptime",
    "## Best and worst calls",
    "## For Rishi: keep, cut or rebuild for next season",
]


# ---- fixtures -----------------------------------------------------------------------------------


def kickoff(week: int) -> dt.datetime:
    return KICK0 + dt.timedelta(weeks=week - 1)


def home_wins(week: int, i: int) -> bool:
    return (week + i) % 3 != 0


def game_id(week: int, i: int, season: int = SEASON) -> str:
    return f"{season}_{week:02d}_{TEAMS[2 * i + 1]}_{TEAMS[2 * i]}"


def make_games(weeks, game_type: str = "REG", season: int = SEASON) -> pl.DataFrame:
    rows = []
    for w in weeks:
        for i in range(4):
            win = home_wins(w, i)
            hs, aws = (27, 20) if win else (17, 24)
            rows.append(
                {
                    "game_id": game_id(w, i, season),
                    "season": season,
                    "week": w,
                    "game_type": game_type,
                    "home_team": TEAMS[2 * i],
                    "away_team": TEAMS[2 * i + 1],
                    "home_score": hs,
                    "away_score": aws,
                    "result": hs - aws,
                    "kickoff_utc": kickoff(w),
                }
            )
    return pl.DataFrame(rows).with_columns(pl.col("week").cast(pl.Int32))


def save_preds(root: Path, weeks, *, game_type="REG", season=SEASON, saved_after_kickoff=False):
    """The week's saved predictions (one primary row per game), at `root/<season>/weekNN`."""
    for w in weeks:
        rows = []
        for i in range(4):
            made = kickoff(w) + dt.timedelta(hours=1) if saved_after_kickoff else None
            rows.append(
                {
                    "season": season,
                    "week": w,
                    "game_id": game_id(w, i, season),
                    "game_type": game_type,
                    "kickoff_utc": kickoff(w),
                    "home_team": TEAMS[2 * i],
                    "away_team": TEAMS[2 * i + 1],
                    "is_primary": True,
                    "home_win_prob": PROBS[i],
                    "elo_prob": ELO,
                    "market_prob": MARKET,
                    "pred_home_points": 24.0 + i,
                    "pred_away_points": 20.0,
                    "created_at": made or kickoff(w) - dt.timedelta(days=5),
                }
            )
        d = week_dir(root, season, w)
        d.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows).write_parquet(d / PRED_FILE)


def expected(weeks) -> dict:
    """Season numbers worked out directly from the fixture's outcomes."""
    games = [(w, i) for w in weeks for i in range(4)]
    y = [1.0 if home_wins(w, i) else 0.0 for w, i in games]
    p = [PROBS[i] for _, i in games]
    n = len(games)
    return {
        "games": n,
        "brier_model": sum((a - b) ** 2 for a, b in zip(p, y, strict=True)) / n,
        "brier_elo": sum((ELO - b) ** 2 for b in y) / n,
        "brier_market": sum((MARKET - b) ** 2 for b in y) / n,
        "correct": int(sum(y)),  # the home team is always the pick
    }


def save_watch(root: Path, weeks, season: int = SEASON):
    for w in weeks:
        rows = [
            {
                "season": season,
                "week": w,
                "game_id": game_id(w, i, season),
                "kickoff_utc": kickoff(w),
                "created_at": kickoff(w) - dt.timedelta(days=5),
                "player_id": f"p{i}",
                "player": f"Player {i}",
                "team": TEAMS[2 * i],
                "position": "WR",
                "group": "WR/TE",
                "target": "rec_yds",
                "target_label": "receiving yards",
                "kind": "amount",
                "unit": "yards",
                "p10": 40.0,
                "p50": 70.0,
                "p90": 100.0,
                "mean": 70.0,
                "baseline": 60.0 + i,
                "source": "model",
                "side": "offense" if i < 2 else "defense",
            }
            for i in range(4)
        ]
        d = week_dir(root, season, w)
        d.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows).write_parquet(d / WATCH_FILE)


def fake_scorer(watch: pl.DataFrame) -> pl.DataFrame:
    """Even picks land at 90 (a hit, inside the range), odd ones at 40 (below baseline, inside)."""
    idx = pl.int_range(pl.len())
    return watch.with_columns(
        pl.when(idx % 2 == 0).then(90.0).otherwise(40.0).alias("actual")
    ).with_columns(
        pl.lit(True).alias("played"),
        (pl.col("actual") > pl.col("baseline")).alias("hit"),
        ((pl.col("actual") >= pl.col("p10")) & (pl.col("actual") <= pl.col("p90"))).alias("inside"),
    )


def sb_row(week, target, group, mode, model=9.0, base=10.0, n=40, brier=None, season=SEASON):
    return {
        "season": season,
        "week": week,
        "target": target,
        "position_group": group,
        "target_label": target.replace("_", " "),
        "n_scored": n,
        "n_not_played": 0,
        "mae_model": None if brier else model,
        "mae_baseline": None if brier else base,
        "improvement_pct": None if brier else 100 * (base - model) / base,
        "coverage_80": None if brier else 0.8,
        "brier_model": brier[0] if brier else None,
        "brier_baseline": brier[1] if brier else None,
        "calibration_ece": 0.05 if brier else None,
        "mode": mode,
    }


def save_scoreboard(path: Path, weeks, live_from: int = 3):
    rows = []
    for w in weeks:
        mode = "live" if w >= live_from else "backtest"
        rows += [
            sb_row(w, "pass_yds", "QB", mode, 9.0 if mode == "live" else 9.5),
            sb_row(w, "rush_yds", "RB", mode, 11.0, 10.0),
            sb_row(w, "td", "RB", mode, brier=(0.18, 0.20)),
            sb_row(w, "team_pts", "TEAM", mode, 8.0, 10.0),
        ]
    rows.append(sb_row(live_from, "pass_yds", "QB", "backtest", 5.0, 10.0))  # a live row wins
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(path)


def save_scorecard(path: Path, weeks, hits=(3, 4)):
    rows = []
    for w in weeks:
        e = expected([w])
        rows.append(
            {
                "season": SEASON,
                "week": w,
                "games": 4,
                "picks_correct": e["correct"],
                "picks_total": 4,
                "brier_model": e["brier_model"],
                "brier_elo": e["brier_elo"],
                "brier_market": e["brier_market"],
                "points_mae": 5.0,
                "watchlist_hits": hits[0],
                "watchlist_total": hits[1],
                "checks_passed": True,
                "not_graded": 0,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=SCORECARD_SCHEMA).write_parquet(path)


def hist_row(root: Path, week: int, season: int = SEASON, **kw):
    base = {
        "season": season,
        "week": week,
        "kind": "main",
        "run_id": f"r{week}",
        "started": f"2026-10-{week:02d}T10:00:00Z",
        "status": "ok",
    }
    append_history(history_path(root, season), {**base, **kw})


def published(**kw):
    """A run that published on time, 20 h before the first kickoff, checks passed first time."""
    return {
        "on_time": True,
        "hours_before_deadline": 20.0,
        "total_seconds": 600.0,
        "checks_passed": True,
        "regenerated": False,
        "banner": False,
        "drift_alerts": 0,
        **kw,
    }


def write_week_files(
    root: Path, week: int, *, checks=None, digest=True, summary=None, season=SEASON
):
    d = week_dir(root, season, week)
    d.mkdir(parents=True, exist_ok=True)
    if digest:
        (d / "digest.md").write_text("# digest", encoding="utf-8")
    if checks is not None:
        (d / "checks.json").write_text(json.dumps(checks), encoding="utf-8")
    if summary is not None:
        (d / RUN_SUMMARY_FILE).write_text(json.dumps(summary), encoding="utf-8")


def checks_json(passed=True, regenerated=False, banner=False, first_failed=()):
    return {
        "passed": passed,
        "regenerated": regenerated,
        "banner": banner,
        "first_attempt": {"failed": list(first_failed)},
        "final": {"failed": [] if passed else ["entity_binding"]},
    }


@pytest.fixture()
def paths(tmp_path) -> DataPaths:
    return DataPaths(tmp_path / "data")


@pytest.fixture()
def full(paths):
    """Six graded weeks with every record: scoreboard (live from week 3), scorecard, watch
    lists, history for weeks 1-6 (week 4 regenerated) and the week folders' own files."""
    root = paths.runs
    weeks = range(1, 7)
    save_preds(root, weeks)
    save_watch(root, weeks)
    save_scoreboard(root / str(SEASON) / "accuracy_scoreboard.parquet", weeks)
    save_scorecard(root / str(SEASON) / "season_scorecard.parquet", weeks)
    for w in weeks:
        extra = {"regenerated": True} if w == 4 else {}
        hist_row(root, w, **published(**extra))
        write_week_files(
            root,
            w,
            checks=checks_json(
                regenerated=w == 4, first_failed=["entity_binding"] if w == 4 else []
            ),
        )
    make_games(weeks).write_parquet(_games_path(paths))
    return paths


def _games_path(paths: DataPaths) -> Path:
    paths.curated.mkdir(parents=True, exist_ok=True)
    return paths.curated / "games.parquet"


def build(paths, **kw) -> SR.SeasonReview:
    kw.setdefault("score_watch", fake_scorer)
    kw.setdefault("as_of", dt.datetime(2026, 12, 1, tzinfo=UTC))
    return SR.build_season_review(SEASON, paths=paths, **kw)


# ---- a full review ------------------------------------------------------------------------------


def test_full_review_has_every_section_in_order(full):
    md = SR.render_review(build(full))
    positions = [md.index(s) for s in SECTIONS]
    assert positions == sorted(positions)
    assert "No graded" not in md and "No watch-list" not in md and "No pipeline" not in md
    assert "Traceback" not in md


def test_season_numbers_equal_the_numbers_worked_out_by_hand(full):
    rv = build(full)
    e = expected(range(1, 7))
    t = rv.totals
    assert rv.through_week == 6 and t["games"] == e["games"] == 24
    assert t["brier_model"] == pytest.approx(e["brier_model"])
    assert t["brier_elo"] == pytest.approx(e["brier_elo"])
    assert t["brier_market"] == pytest.approx(e["brier_market"])
    assert t["picks_correct"] == e["correct"] and t["picks_total"] == 24
    assert t["points_mae"] is not None
    h = rv.headline
    assert h["games"] == 24 and h["weeks"] == 6 and h["week_range"] == "weeks 1–6"
    assert h["pick_accuracy"] == pytest.approx(e["correct"] / 24)
    assert h["weeks_published"] == h["weeks_expected"] == 6
    assert h["on_time"] == h["timed"] == 6
    assert h["first_try"] == 5 and h["checked"] == 6


def test_weekly_table_has_one_row_per_graded_week(full):
    rv = build(full)
    assert rv.weekly["week"].to_list() == [1, 2, 3, 4, 5, 6]
    w2 = rv.weekly.filter(pl.col("week") == 2).row(0, named=True)
    e = expected([2])
    assert w2["games"] == 4 and w2["brier_model"] == pytest.approx(e["brier_model"])
    assert w2["picks_correct"] == e["correct"]
    # the model has the better Brier than Elo in some weeks only: the counts add up
    assert rv.wins["weeks"] == 6
    assert (
        rv.wins["beat_elo"] == rv.weekly.filter(pl.col("brier_model") < pl.col("brier_elo")).height
    )
    md = SR.render_review(rv)
    assert "lower is better" in md.split("## Game model")[1].split("## Calibration")[0]


def test_calibration_section_reports_ece_against_the_chance_level(full):
    rv = build(full)
    c = rv.calibration
    assert c["games"] == 24 and c["ece"] is not None and 0 < c["chance"] < 1
    assert c["verdict"] == "few"  # 24 games is below the 64-game minimum
    assert rv.reliability["n"].sum() == 24
    md = SR.render_review(rv)
    assert "Season ECE is **" in md and "Fewer than 64 games" in md
    assert "| Predicted home win |" in md


def test_calibration_verdicts():
    cfg = {"ece_min_games": 4}
    row = {"home_win": 1.0, "home_win_prob": 0.9}
    good = pl.DataFrame([{"home_win": float(i % 10 < 9), "home_win_prob": 0.9} for i in range(100)])
    info, _ = SR._calibration(good, cfg)
    assert info["verdict"] == "within"
    bad = pl.DataFrame([{**row, "home_win": 0.0, "home_win_prob": 0.9}] * 100)
    info, _ = SR._calibration(bad, cfg)
    assert info["verdict"] == "above" and info["ece"] == pytest.approx(0.9)
    info, curve = SR._calibration(pl.DataFrame(), cfg)
    assert info == {} and curve.is_empty()


# ---- player scoreboard: live and backtest rows --------------------------------------------------


def test_live_and_backtest_scoreboard_rows_stay_apart(full):
    rv = build(full)
    assert rv.scoreboard_weeks == {"live": [3, 4, 5, 6], "backtest": [1, 2]}
    groups = rv.groups
    live_qb = groups.filter((pl.col("mode") == "live") & (pl.col("position_group") == "QB"))
    back_qb = groups.filter((pl.col("mode") == "backtest") & (pl.col("position_group") == "QB"))
    # live QB: 9 against 10 = +10% in every live week (the backtest duplicate of week 3 loses)
    assert live_qb["improvement"][0] == pytest.approx(10.0) and live_qb["weeks"][0] == 4
    assert back_qb["improvement"][0] == pytest.approx(5.0) and back_qb["weeks"][0] == 2
    live_rb = groups.filter((pl.col("mode") == "live") & (pl.col("position_group") == "RB"))
    assert live_rb["improvement"][0] == pytest.approx(-10.0)  # model 11 against 10
    assert live_rb["brier_improvement"][0] == pytest.approx(10.0)  # 0.18 against 0.20
    md = SR.render_review(rv)
    assert "Live rows: projections saved before kickoff, scored afterwards; weeks 3–6" in md
    assert "Backtest rows: walk-forward re-runs, labelled and not live; weeks 1–2" in md
    assert "Player scoreboard: weeks 3–6 are live, weeks 1–2 are walk-forward backtest." in md


def test_team_rows_have_their_own_table_and_stay_out_of_the_player_numbers(full):
    rv = build(full)
    assert rv.team["target"].to_list() == ["team_pts", "team_pts"]  # one per mode
    assert "TEAM" not in rv.players["position_group"].to_list()
    # players only: QB +10% and RB -10% on equal numbers of projections average to 0%; the
    # TEAM rows (+20%) are not in it
    live_all = rv.groups.filter(
        (pl.col("mode") == "live") & (pl.col("position_group") == "All player groups")
    )
    assert live_all["improvement"][0] == pytest.approx(0.0, abs=1e-9)
    assert rv.headline["player_improvement"] == pytest.approx(0.0, abs=1e-9)
    assert rv.headline["player_mode"] == "live"
    md = SR.render_review(rv)
    assert "### Team stat totals" in md
    assert "| Rows | Group | Target |" in md


def test_weekly_table_marks_each_week_live_or_backtest(full):
    rv = build(full)
    modes = dict(zip(rv.weekly["week"], rv.weekly["player_mode"], strict=True))
    assert modes == {1: "backtest", 2: "backtest", 3: "live", 4: "live", 5: "live", 6: "live"}
    assert "| Players vs baseline |" in SR.render_review(rv)


def test_backtest_only_scoreboard_is_labelled_not_live(paths):
    save_preds(paths.runs, range(1, 3))
    save_scoreboard(paths.runs / str(SEASON) / "accuracy_scoreboard.parquet", range(1, 3), 99)
    make_games(range(1, 3)).write_parquet(_games_path(paths))
    rv = build(paths)
    assert rv.headline["player_mode"] == "backtest"
    md = SR.render_review(rv)
    assert "only walk-forward backtest rows" in md and "no live week is scored yet" in md
    assert "(walk-forward backtest rows only)" in md


# ---- watch list ---------------------------------------------------------------------------------


def test_watch_list_is_rescored_per_week_and_by_side(full):
    rv = build(full)
    info = rv.watch_info
    assert info["source"] == "re-scored saved picks"
    # four picks a week; the even ones (2 of 4) land at 90, above every baseline of 60-63
    assert info["picks"] == 24 and info["hits"] == 12 and info["rate"] == pytest.approx(0.5)
    assert info["inside"] == info["ranged"] == 24
    sides = {r["side"]: (r["picks"], r["hits"]) for r in rv.watch_sides.iter_rows(named=True)}
    assert sides == {"offense": (12, 6), "defense": (12, 6)}
    md = SR.render_review(rv)
    assert "**50.0%** (12 of 24 picks that played)" in md and "about 43%" in md


def test_biggest_watch_hits_and_misses_are_ranked_in_range_widths(full):
    rv = build(full)
    # a hit lands 30 above the baseline of 60 (the first pick), over a range 60 wide: 0.5
    assert rv.watch_hits["beat"].max() == pytest.approx((90 - 60) / 60)
    assert rv.watch_hits["beat"].min() > 0 and rv.watch_misses["beat"].max() < 0
    # the odd picks land at 40: the pick with the highest baseline (63) misses by the most
    worst = rv.watch_misses.row(0, named=True)
    assert worst["beat"] == pytest.approx((40 - 63) / 60) and worst["player"] == "Player 3"
    assert len(rv.watch_hits) == SR.N_CALLS and len(rv.watch_misses) == SR.N_CALLS
    md = SR.render_review(rv)
    assert "biggest watch-list hits" in md and "biggest watch-list misses" in md


def test_watch_list_falls_back_to_the_scorecard_when_scoring_fails(full):
    def broken(_):
        raise RuntimeError("player history unavailable")

    rv = build(full, score_watch=broken)
    assert rv.watch_info["source"] == "season scorecard"
    assert rv.watch_info["picks"] == 6 * 4 and rv.watch_info["hits"] == 6 * 3
    assert rv.watch_hits.is_empty() and rv.watch_misses.is_empty()
    assert any("could not be re-scored (RuntimeError)" in n for n in rv.notes)
    assert "player history unavailable" not in "\n".join(rv.notes)  # the type only
    assert "biggest watch-list" not in SR.render_review(rv)


def test_watch_counts_that_differ_from_the_scorecard_are_noted(full):
    save_scorecard(full.runs / str(SEASON) / "season_scorecard.parquet", range(1, 7), hits=(1, 4))
    rv = build(full)
    assert any(
        "Watch-list counts differ for weeks 1–6" in n and "12 of 24" in n and "6 of 24" in n
        for n in rv.notes
    )


# ---- games: playoffs, late picks, best and worst calls ------------------------------------------


def test_playoff_weeks_are_split_from_the_regular_season(paths):
    save_preds(paths.runs, range(1, 3))
    save_preds(paths.runs, [19, 20], game_type="WC")
    pl.concat([make_games(range(1, 3)), make_games([19, 20], "WC")]).write_parquet(
        _games_path(paths)
    )
    rv = build(paths)
    assert rv.through_week == 20
    assert rv.split["part"].to_list() == ["Regular season", "Playoffs", "All games"]
    reg, post, both = rv.split.iter_rows(named=True)
    assert (reg["games"], post["games"], both["games"]) == (8, 8, 16)
    assert post["brier_model"] == pytest.approx(expected([19, 20])["brier_model"])
    md = SR.render_review(rv)
    assert "### Regular season and playoffs" in md and "| 19 (WC) |" in md


def test_no_playoff_split_without_playoff_games(full):
    rv = build(full)
    assert rv.split.is_empty()
    assert "Regular season and playoffs" not in SR.render_review(rv)


def test_picks_saved_after_kickoff_are_not_graded(paths):
    save_preds(paths.runs, [1])
    save_preds(paths.runs, [2], saved_after_kickoff=True)
    make_games(range(1, 3)).write_parquet(_games_path(paths))
    rv = build(paths)
    assert rv.through_week == 1 and rv.totals["games"] == 4 and rv.totals["late"] == 4
    assert "4 games not graded: the pick was saved after kickoff." in SR.render_review(rv)


def test_best_and_worst_calls(full):
    rv = build(full)
    # the favorite is always the home team; game 3 (85%) is the most confident pick
    right, wrong = rv.sure_right, rv.sure_wrong
    assert len(right) == len(wrong) == SR.N_CALLS
    assert right["fav_prob"].to_list() == sorted(right["fav_prob"].to_list(), reverse=True)
    assert wrong["fav_prob"].to_list() == sorted(wrong["fav_prob"].to_list(), reverse=True)
    assert right["fav_prob"].max() == pytest.approx(0.85)
    assert right["result"].min() > 0 and wrong["result"].max() < 0  # home won / home lost
    # margin: predicted home margin is 4 + i points; the miss is |4 + i - result|
    miss = rv.margin_misses
    assert miss["miss"].to_list() == sorted(miss["miss"].to_list(), reverse=True)
    top = miss.row(0, named=True)
    assert top["miss"] == pytest.approx(abs(top["pred_margin"] - top["result"]))
    md = SR.render_review(rv)
    assert "### The 5 most confident correct picks" in md
    assert "### The 5 most confident wrong picks" in md
    assert "### The 5 biggest margin misses" in md
    assert "| Week | Game | Pick | Win chance | Result |" in md
    # games read "away at home" with nicknames, and the result names the winner
    assert re.search(r"\| \d \| \w+ at \w+ \| \w+ \| \d\d% \| \w+ won \d\d–\d\d \|", md)


def test_through_week_cuts_everything_off(full):
    rv = build(full, through_week=3)
    assert rv.through_week == 3 and rv.totals["games"] == 12
    assert rv.weekly["week"].to_list() == [1, 2, 3]
    assert rv.scoreboard_weeks == {"live": [3], "backtest": [1, 2]}
    assert rv.pipeline["week"].to_list() == [1, 2, 3, 4]  # the run of week 4 grades week 3
    assert rv.watch_info["picks"] == 12


# ---- digest checks ------------------------------------------------------------------------------


def test_digest_checks_split_first_time_regenerated_and_banner(paths):
    root = paths.runs
    cases = {
        1: (published(), checks_json()),
        2: (published(regenerated=True), checks_json(regenerated=True, first_failed=["length"])),
        3: (
            published(checks_passed=False, regenerated=True, banner=True),
            checks_json(False, True, True, ["entity_binding", "length"]),
        ),
        4: (published(), checks_json()),
    }
    for w, (row, checks) in cases.items():
        hist_row(root, w, **row)
        write_week_files(root, w, checks=checks)
    rv = build(paths)
    c = rv.checks
    assert c["weeks"] == 4
    assert (c["first_time"], c["regenerated"], c["banner"]) == ([1, 4], [2], [3])
    assert dict(c["first_failed"]) == {"length": 2, "entity_binding": 1}
    md = SR.render_review(rv)
    assert "| Passed first time | 2 | 50% | 1, 4 |" in md
    assert "| Passed after one regeneration | 1 | 25% | 2 |" in md
    assert "| Went out with the warning banner | 1 | 25% | 3 |" in md
    assert "`length` x2" in md


def test_checks_fall_back_to_the_week_files_without_a_history(paths):
    write_week_files(paths.runs, 4, checks=checks_json(regenerated=True))
    write_week_files(paths.runs, 5, checks=checks_json())
    rv = build(paths)
    assert rv.checks["regenerated"] == [4] and rv.checks["first_time"] == [5]
    assert "(from each week's `checks.json`)" in SR.render_review(rv)


# ---- pipeline uptime and the weekly lines -------------------------------------------------------


def test_pipeline_uptime_counts_late_degraded_failed_and_missed_weeks(paths):
    root = paths.runs
    hist_row(root, 1, **published(hours_before_deadline=30.0))
    hist_row(root, 2, **published(on_time=False, hours_before_deadline=-3.0))
    hist_row(root, 3, status="degraded", degraded_steps="graph", **published())
    hist_row(root, 4, status="failed", failed_step="game")
    hist_row(
        root, 4, status="not_ready", started="2026-10-09T10:00:00Z"
    )  # a retry that did nothing
    # week 5 has no record at all; week 6 published
    hist_row(root, 6, **published(drift_alerts=2))
    make_games(range(1, 7)).write_parquet(_games_path(paths))
    rv = build(paths)
    s = rv.pipeline_stats
    assert (s["expected"], s["published"], s["recorded"], s["missed"]) == (6, 4, 5, 1)
    assert (s["timed"], s["on_time"], s["degraded"], s["failed"]) == (4, 3, 1, 1)
    assert s["median_hours"] == pytest.approx(20.0)  # 30, -3, 20, 20, 20: middle of four timed
    assert s["drift_alerts"] == 2
    assert s["runs"] == {"ok": 3, "degraded": 1, "failed": 1, "not_ready": 1}
    md = SR.render_review(rv)
    assert "| Weeks published | 4 of 6 |" in md
    assert "| Published on time (before the first kickoff) | 3 of 4 (75%) |" in md
    assert "| Not published: no run recorded | 1 |" in md


def test_a_live_week_that_never_ran_counts_as_missed_once_its_kickoff_has_passed(paths):
    root = paths.runs
    for w in (1, 2):
        hist_row(root, w, **published())
    make_games(range(1, 6)).write_parquet(_games_path(paths))
    before = SR.week_lines(SEASON, paths=paths, as_of=kickoff(3) - dt.timedelta(hours=1))
    assert [x[:7] for x in before] == ["week 01", "week 02"]
    after = SR.week_lines(SEASON, paths=paths, as_of=kickoff(4) + dt.timedelta(hours=1))
    assert after[2:] == [
        "week 03: not published (no run recorded)",
        "week 04: not published (no run recorded)",
    ]


def test_week_lines_wording(paths):
    root = paths.runs
    hist_row(root, 5, **published(hours_before_deadline=58.22))
    hist_row(root, 6, **published(on_time=False, hours_before_deadline=-2.5, regenerated=True))
    hist_row(
        root,
        6,
        **published(
            on_time=False,
            hours_before_deadline=-2.5,
            regenerated=True,
            drift_alerts=1,
            started="2026-10-09T10:00:00Z",
        ),
    )
    hist_row(root, 7, status="failed", failed_step="game")
    hist_row(root, 8, **published(hours_before_deadline=10.0, checks_passed=False, banner=True))
    hist_row(
        root,
        9,
        status="degraded",
        degraded_steps="graph,player",
        **published(hours_before_deadline=3.0, drift_alerts=2),
    )
    hist_row(root, 11, **published(on_time=None, hours_before_deadline=None))
    hist_row(root, 12, status="failed")
    write_week_files(
        root,
        6,
        summary={
            "kind": "main",
            "alerts": [
                {
                    "level": "warn",
                    "title": "2026 week 6: game model behind Elo for 3 weeks",
                    "source": "drift:game_vs_elo",
                },
                {"level": "info", "title": "something else", "source": "readiness"},
            ],
        },
    )
    write_week_files(root, 10, checks=checks_json())  # a digest and no run record
    lines = SR.week_lines(
        SEASON,
        paths=paths,
        games=make_games(range(1, 14)),
        as_of=dt.datetime(2026, 12, 1, tzinfo=UTC),
    )
    assert lines == [
        "week 05: published ✓ on time (58.2 h before kickoff), checks passed first time, "
        "0 drift alerts",
        "week 06: published ✓ late (2.5 h after the first kickoff), checks passed after one "
        "regeneration, 1 drift alert (game model behind Elo for 3 weeks)",
        "week 07: not published (failed at game)",
        "week 08: published ✓ on time (10.0 h before kickoff), checks failed, went out with the "
        "warning banner, 0 drift alerts",
        "week 09: published ✓ on time (3.0 h before kickoff), checks passed first time, "
        "degraded: graph, player, 2 drift alerts",
        "week 10: published ✓ (digest found, no run record), checks passed first time",
        "week 11: published ✓ (no deadline recorded), checks passed first time, 0 drift alerts",
        "week 12: not published (the run failed)",
    ]


def test_week_lines_for_a_season_with_no_records_is_empty(paths):
    assert SR.week_lines(SEASON, paths=paths) == []


def test_alert_titles_come_from_each_weeks_run_summary(paths):
    hist_row(paths.runs, 4, **published(drift_alerts=1))
    write_week_files(
        paths.runs,
        4,
        summary={
            "kind": "main",
            "alerts": [{"level": "warn", "title": "2026 week 4: stale data source", "source": "x"}],
        },
    )
    # a summary of another kind (a Saturday injury update) is not this run's
    hist_row(paths.runs, 5, **published())
    write_week_files(paths.runs, 5, summary={"kind": "injury-update", "alerts": [{"title": "no"}]})
    rv = build(paths)
    assert rv.pipeline_stats["summaries"] == 1  # the other kind's summary is not read
    assert rv.alerts == [{"week": 4, "level": "warn", "source": "x", "title": "stale data source"}]
    md = SR.render_review(rv)
    assert "| 4 | warn | stale data source |" in md


def test_a_history_of_another_kind_is_explained(paths):
    hist_row(paths.runs, 4, kind="simulation", **published())
    rv = build(paths)  # kind "main" finds nothing
    assert rv.pipeline.is_empty()
    assert any("kind `simulation`" not in n and "simulation" in n for n in rv.notes)
    assert "build the review with kind=`simulation`" in "\n".join(rv.notes)
    sim = build(paths, kind="simulation")
    assert sim.pipeline_stats["published"] == 1


# ---- the scorecard cross-check -----------------------------------------------------------------


def test_scorecard_gaps_and_differences_are_noted(full):
    sc_path = full.runs / str(SEASON) / "season_scorecard.parquet"
    sc = pl.read_parquet(sc_path)
    sc = sc.filter(~pl.col("week").is_in([2, 6])).with_columns(
        pl.when(pl.col("week") == 3).then(0.5).otherwise(pl.col("brier_model")).alias("brier_model")
    )
    sc.write_parquet(sc_path)
    rv = build(full)
    notes = "\n".join(rv.notes)
    assert (
        "Weeks 2, 6 are graded here from the saved predictions but not in the season scorecard"
        in notes
    )
    assert "differs from the scorecard's by more than 0.0005 in week 3" in notes
    # the numbers themselves come from the saved predictions, not the scorecard
    assert rv.totals["brier_model"] == pytest.approx(expected(range(1, 7))["brier_model"])


def test_scorecard_is_not_needed_for_the_game_numbers(full):
    (full.runs / str(SEASON) / "season_scorecard.parquet").unlink()
    rv = build(full)
    assert rv.totals["games"] == 24
    assert "season_scorecard.parquet" in rv.missing


# ---- simulations and where the review is written ------------------------------------------------


def test_a_simulation_root_reads_its_own_records(paths):
    sim = paths.runs / "digest-backtests"
    save_preds(sim, range(1, 4))
    for w in (2, 3):
        hist_row(sim, w, kind="simulation", **published())
        write_week_files(sim, w, checks=checks_json())
    # no live scoreboard in a simulation: the backtest scoreboard is read
    save_scoreboard(paths.runs / "backtests" / "player" / "scoreboard.parquet", range(1, 9), 99)
    make_games(range(1, 4)).write_parquet(_games_path(paths))
    rv = build(paths, run_root=sim, kind="simulation")
    assert rv.live is False and rv.run_root.endswith("runs/digest-backtests")
    assert rv.through_week == 3 and rv.totals["games"] == 12
    assert rv.scoreboard_weeks == {"live": [], "backtest": [1, 2, 3]}  # cut at the graded week
    assert rv.pipeline_stats["recorded"] == 2
    md = SR.render_review(rv)
    assert "from simulation and backtest digests under" in md and "backtest player scoreboard" in md


def test_write_review_default_paths(full):
    out = SR.write_review(SEASON, paths=full, score_watch=fake_scorer)
    assert out == full.reports / "2026" / "season-review.md"
    assert out.read_text(encoding="utf-8").startswith("# 2026 season review")
    sim = full.runs / "digest-backtests"
    save_preds(sim, [1])
    out = SR.write_review(SEASON, paths=full, run_root=sim, kind="simulation")
    assert out == full.reports / "backtests" / "2026" / "season-review.md"
    custom = full.root / "elsewhere" / "review.md"
    assert SR.write_review(SEASON, custom, paths=full, score_watch=fake_scorer) == custom
    assert custom.exists()


# ---- nothing to read ----------------------------------------------------------------------------


EMPTY_LINES = {
    "## The season at a glance": "No graded weeks yet.",
    "## Game model, Elo and the market": "No graded weeks yet.",
    "## Calibration": "No graded games yet, so there is nothing to check.",
    "## Player projections: the accuracy scoreboard": "No player scoreboard rows yet.",
    "## Watch list": "No watch-list picks have been graded yet.",
    "## Digest checks": "No digest checks recorded yet.",
    "## Pipeline uptime": "No pipeline runs recorded yet.",
    "## Best and worst calls": "No graded games yet.",
}


def section(md: str, title: str) -> str:
    start = md.index(title)
    nxt = md.find("\n## ", start + 1)
    return md[start : nxt if nxt != -1 else len(md)]


def test_a_season_with_no_records_renders_every_section(paths):
    rv = build(paths)
    md = SR.render_review(rv)
    for title, line in EMPTY_LINES.items():
        assert line in section(md, title), title
    for s in SECTIONS:
        assert s in md
    assert "Not found:" in md and "Traceback" not in md
    assert rv.through_week is None and rv.week_lines == []


def test_a_bare_review_object_renders(paths):
    md = SR.render_review(SR.SeasonReview(2030))
    for title, line in EMPTY_LINES.items():
        assert line in section(md.replace("2026", "2030"), title.replace("2026", "2030"))
    assert "# 2030 season review" in md


def test_an_unresolvable_data_root_gives_a_note_not_a_traceback(monkeypatch):
    def no_root(*a, **k):
        raise DataRootError("no drive")

    monkeypatch.setattr(SR, "ensure_data_root", no_root)
    rv = SR.build_season_review(SEASON)  # no run_root, no paths
    md = SR.render_review(rv)
    assert "The data root could not be resolved (DataRootError)" in md
    assert "no drive" not in md
    assert SR.week_lines(SEASON) == []


def test_only_games_graded_leaves_the_other_sections_empty(paths):
    save_preds(paths.runs, range(1, 4))
    make_games(range(1, 4)).write_parquet(_games_path(paths))
    md = SR.render_review(build(paths))
    assert "| Brier score, model |" in md
    for title in ("## Player projections: the accuracy scoreboard", "## Watch list"):
        assert EMPTY_LINES[title] in section(md, title)
    for title in ("## Digest checks", "## Pipeline uptime"):
        assert EMPTY_LINES[title] in section(md, title)
    # the glance table says what is missing instead of inventing numbers
    glance = section(md, "## The season at a glance")
    assert "| Watch-list hit rate | - | no graded picks |" in glance
    assert "| Weeks published | - | no runs recorded |" in glance


def test_only_a_pipeline_history_is_enough_for_the_uptime_section(paths):
    hist_row(paths.runs, 4, **published())
    md = SR.render_review(build(paths))
    assert "| Weeks published | 1 of 1 |" in section(md, "## Pipeline uptime")
    assert EMPTY_LINES["## Game model, Elo and the market"] in section(
        md, "## Game model, Elo and the market"
    )


def test_unreadable_files_are_reported_by_type_not_message(paths):
    d = paths.runs / str(SEASON)
    d.mkdir(parents=True)
    (d / "season_scorecard.parquet").write_text("not a parquet file", encoding="utf-8")
    (d / "pipeline_history.parquet").write_text("secret=abc", encoding="utf-8")
    rv = build(paths)
    text = "\n".join(rv.missing)
    assert "season_scorecard.parquet (unreadable:" in text
    assert "pipeline_history.parquet (unreadable:" in text
    assert "secret=abc" not in text


def test_a_saved_file_without_the_optional_columns_still_grades(paths):
    """Older saved predictions have no market or Elo columns, and no predicted points."""
    root = paths.runs
    save_preds(root, [1])
    p = week_dir(root, SEASON, 1) / PRED_FILE
    pl.read_parquet(p).drop(
        "market_prob", "elo_prob", "pred_home_points", "pred_away_points", "game_type"
    ).write_parquet(p)
    make_games([1]).write_parquet(_games_path(paths))
    rv = build(paths)
    t = rv.totals
    assert t["games"] == 4 and t["points_mae"] is None and t["brier_market"] is None
    md = SR.render_review(rv)
    assert "| Market | - | 0 |" in md


def test_week_ranges_read_naturally():
    assert SR._weeks_text([3, 1, 2, 4, 9, 11, 12]) == "1–4, 9, 11–12"
    assert SR._weeks_text([]) == "none"
    assert SR._weeks_label([5]) == "week 5" and SR._weeks_label([1, 2, 3]) == "weeks 1–3"


def test_the_alerts_section_says_when_there_are_no_summaries_or_no_alerts(paths):
    hist_row(paths.runs, 4, **published())
    assert "No run summaries were found, so the alerts are unknown." in SR.render_review(
        build(paths)
    )
    write_week_files(paths.runs, 4, summary={"kind": "main", "alerts": []})
    assert "No alerts in the 1 run summary." in SR.render_review(build(paths))


# ---- Sol review (P10) ---------------------------------------------------------------------------


def test_an_outage_after_the_last_graded_week_still_counts_as_missed(paths):
    """Graded through week 2 and no runs in weeks 4-5: the default review must still show
    weeks 4-5 as missed (a bound derived from the graded weeks used to hide them)."""
    root = paths.runs
    save_preds(root, range(1, 3))
    for w in (1, 2, 3):
        hist_row(root, w, **published())
    make_games(range(1, 7)).write_parquet(_games_path(paths))
    rv = build(paths, as_of=kickoff(5) + dt.timedelta(hours=1))
    assert rv.through_week == 2
    assert rv.pipeline_stats["missed"] == 2
    assert [x for x in rv.week_lines if "not published" in x] == [
        "week 04: not published (no run recorded)",
        "week 05: not published (no run recorded)",
    ]
    # an explicit through_week still bounds the pipeline sections
    cut = build(paths, through_week=2, as_of=kickoff(5) + dt.timedelta(hours=1))
    assert cut.pipeline_stats["missed"] == 0


def test_a_weeks_player_number_never_mixes_live_and_backtest_rows():
    sb = pl.DataFrame(
        [
            sb_row(3, "pass_yds", "QB", "live", 9.0, 10.0),  # live: +10%
            sb_row(3, "rush_yds", "RB", "backtest", 11.0, 10.0),  # walk-forward: -10%
            sb_row(4, "rush_yds", "RB", "backtest", 11.0, 10.0),  # backtest only
        ]
    )
    out = SR._with_player_columns(pl.DataFrame({"week": [3, 4]}), sb)
    got = dict(
        zip(
            out["week"],
            zip(out["player_improvement"], out["player_mode"], strict=True),
            strict=True,
        )
    )
    assert got[3] == (pytest.approx(10.0), "live")
    assert got[4] == (pytest.approx(-10.0), "backtest")
