"""Drift signals (P07, documentation/08 -> Drift signals and responses): each signal's ok /
alert / insufficient_data paths, the streak boundaries, partial windows, ECE on a known
example, freshness and checks, the alert titles and the replay."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from nflengine.digest.report_card import PRED_FILE, week_dir
from nflengine.ops import drift
from nflengine.ops.records import append_history

UTC = dt.UTC
CFG = dict(drift.DEFAULTS)  # rolling 4, streak 3, ece 0.05 over 64 games, window 4, rate 0.2


# ---- helpers ------------------------------------------------------------------------------------


def weekly(*rows: tuple[int, int, float, float]) -> pl.DataFrame:
    """rows: (week, games, brier_model, brier_elo)."""
    return pl.DataFrame(rows, schema=drift.WEEKLY_SCHEMA, orient="row")  # type: ignore[arg-type]


def flat(n_weeks: int, gap: float, games: int = 10, first: int = 1) -> pl.DataFrame:
    """`n_weeks` graded weeks where the model's Brier is Elo's + `gap` every week."""
    return weekly(*[(first + i, games, 0.20 + gap, 0.20) for i in range(n_weeks)])


def sb_rows(
    season: int, weeks: range, group: str, model: float, base: float, mode: str = "backtest"
) -> list[dict]:
    return [
        {
            "season": season,
            "week": w,
            "target": "t1",
            "position_group": group,
            "n_scored": 50,
            "mae_model": model,
            "mae_baseline": base,
            "mode": mode,
        }
        for w in weeks
    ]


def scoreboard(*parts: list[dict]) -> pl.DataFrame:
    return pl.DataFrame([r for p in parts for r in p])


def graded(rows: list[tuple[int, float, float, float]]) -> pl.DataFrame:
    """rows: (week, home_win, home_win_prob, elo_prob)."""
    return pl.DataFrame(
        rows,
        schema={
            "week": pl.Int32,
            "home_win": pl.Float64,
            "home_win_prob": pl.Float64,
            "elo_prob": pl.Float64,
        },
        orient="row",
    )


def ece_frame(spec: list[tuple[float, int, int]]) -> pl.DataFrame:
    """spec: (probability, games, home wins). Weeks are 1..n spread evenly."""
    rows = []
    for p, n, wins in spec:
        rows += [(1, 1.0 if i < wins else 0.0, p, 0.5) for i in range(n)]
    return graded(rows)


# ---- rolling windows ----------------------------------------------------------------------------


def test_rolling_windows_are_full_and_game_weighted():
    w = weekly((1, 10, 0.20, 0.30), (2, 30, 0.30, 0.30), (3, 10, 0.20, 0.30), (4, 10, 0.10, 0.30))
    out = drift.rolling_gaps(w, 4)
    assert len(out) == 1  # only week 4 has a full 4-week window
    assert out[0]["weeks"] == [1, 2, 3, 4] and out[0]["games"] == 60
    # (10*.2 + 30*.3 + 10*.2 + 10*.1) / 60
    assert out[0]["brier_model"] == pytest.approx(14 / 60)
    assert out[0]["brier_elo"] == pytest.approx(0.30)
    assert out[0]["gap"] == pytest.approx(14 / 60 - 0.30)


def test_partial_windows_only_when_asked():
    w = flat(3, 0.01)
    assert drift.rolling_gaps(w, 4) == []
    part = drift.rolling_gaps(w, 4, min_window=2)
    assert [g["weeks"] for g in part] == [[1, 2], [1, 2, 3]]


def test_missing_weeks_are_skipped_not_zero():
    w = weekly((1, 10, 0.2, 0.2), (2, 10, 0.2, 0.2), (5, 10, 0.3, 0.2), (6, 10, 0.3, 0.2))
    out = drift.rolling_gaps(w, 4)
    assert out[0]["weeks"] == [1, 2, 5, 6]  # a gap in the calendar doesn't shorten the window


# ---- game_vs_elo --------------------------------------------------------------------------------


def test_game_vs_elo_needs_window_plus_streak_minus_one_weeks():
    assert drift._needed(CFG) == 6
    s = drift.game_vs_elo_signal(flat(5, 0.05), CFG)
    assert s.status == "insufficient_data" and "5 graded week" in s.detail and "6" in s.detail
    assert s.value is None


def test_game_vs_elo_alert_at_the_streak_boundary():
    s = drift.game_vs_elo_signal(flat(6, 0.01), CFG)  # windows ending weeks 4, 5, 6: all behind
    assert s.status == "alert"
    assert s.value == pytest.approx(0.01)
    assert s.weeks == [1, 2, 3, 4, 5, 6]
    assert "3 of the last 3" in s.detail
    assert "Nothing is retuned automatically" in s.response


def test_game_vs_elo_one_good_window_clears_it():
    # the model is behind in weeks 1-4 (window 4 behind), then ahead enough in week 5 to pull
    # window 5 below Elo, then behind again: not three behind in a row
    rows = [(w, 10, 0.21, 0.20) for w in range(1, 5)] + [(5, 10, 0.05, 0.20), (6, 10, 0.21, 0.20)]
    s = drift.game_vs_elo_signal(weekly(*rows), CFG)
    assert s.status == "ok"
    assert "1 of the last 3" in s.detail  # windows: +0.01, -0.03, -0.03


def test_game_vs_elo_ahead_of_elo_is_ok_and_equal_is_not_worse():
    assert drift.game_vs_elo_signal(flat(8, -0.01), CFG).status == "ok"
    assert drift.game_vs_elo_signal(flat(8, 0.0), CFG).status == "ok"  # a tie is not "worse"


def test_game_vs_elo_margin_and_partial_window_options():
    assert drift.game_vs_elo_signal(flat(8, 0.004), {**CFG, "game_margin": 0.005}).status == "ok"
    # 4 graded weeks: no signal with full windows, an alert with 2-week partial windows
    assert drift.game_vs_elo_signal(flat(4, 0.02), CFG).status == "insufficient_data"
    s = drift.game_vs_elo_signal(flat(4, 0.02), {**CFG, "min_window_weeks": 2})
    assert s.status == "alert"


def test_game_vs_elo_no_graded_weeks():
    s = drift.game_vs_elo_signal(pl.DataFrame(schema=drift.WEEKLY_SCHEMA), CFG)
    assert s.status == "insufficient_data" and "0 graded week" in s.detail


# ---- calibration --------------------------------------------------------------------------------


def test_ece_known_example():
    # 10 games at 0.2 with 2 wins (gap 0) + 10 games at 0.8 with 6 wins (gap 0.2):
    # ECE = 0.5 * 0 + 0.5 * 0.2 = 0.1
    g = ece_frame([(0.2, 10, 2), (0.8, 10, 6)])
    s = drift.calibration_signal(g, {**CFG, "ece_min_games": 20})
    assert s.value == pytest.approx(0.1)
    assert s.status == "alert" and s.threshold == 0.05
    assert "0.100" in s.detail and "20 graded games" in s.detail


def test_ece_perfectly_calibrated_bins_is_ok():
    g = ece_frame([(0.25, 20, 5), (0.75, 20, 15)])
    s = drift.calibration_signal(g, {**CFG, "ece_min_games": 40})
    assert s.status == "ok" and s.value == pytest.approx(0.0, abs=1e-12)
    assert s.response == drift.NO_ACTION


def test_calibration_insufficient_below_min_games_and_exactly_at_it():
    g = ece_frame([(0.6, 63, 40)])
    s = drift.calibration_signal(g, CFG)
    assert s.status == "insufficient_data" and "63 graded game" in s.detail
    assert drift.calibration_signal(ece_frame([(0.6, 64, 40)]), CFG).status != "insufficient_data"


def test_calibration_response_says_there_is_no_layer_to_refit():
    s = drift.calibration_signal(ece_frame([(0.9, 100, 50)]), CFG)
    assert s.status == "alert"
    assert "no calibration layer" in s.response
    assert "D48" in s.response and "platt" in s.response and "isotonic" in s.response
    assert "backtest" in s.response


def test_ece_noise_is_seeded_and_reasonable():
    p = np.full(100, 0.6)
    a, a90 = drift.ece_noise(p)
    b, _ = drift.ece_noise(p)
    assert a == b  # same seed, same answer
    assert 0.03 < a < 0.15 and a90 >= a  # a perfect model's ECE on 100 games is not near zero
    big, _ = drift.ece_noise(np.full(5000, 0.6))
    assert big < a
    assert all(np.isnan(x) for x in drift.ece_noise(np.array([])))


def test_calibration_noise_adjust_raises_the_limit_to_chance():
    # 64 games at 0.6, 45 wins: ECE 0.1 against a 0.05 limit, but within chance for 64 games
    g = ece_frame([(0.6, 64, 45)])
    strict = drift.calibration_signal(g, CFG)
    lenient = drift.calibration_signal(g, {**CFG, "ece_noise_adjust": True})
    assert strict.status == "alert"
    assert lenient.status == "ok" and lenient.threshold > strict.threshold
    assert "chance level" in lenient.detail


# ---- player_vs_baseline -------------------------------------------------------------------------


def test_improvement_is_scale_free_across_targets_and_weighted_by_n():
    rows = pl.DataFrame(
        [
            # target a (yards): model 8 vs baseline 10 -> 20% better, 100 players
            {"week": 1, "target": "a", "n_scored": 100, "mae_model": 8.0, "mae_baseline": 10.0},
            # target b (counts): model 1.1 vs baseline 1.0 -> 10% worse, 100 players
            {"week": 1, "target": "b", "n_scored": 100, "mae_model": 1.1, "mae_baseline": 1.0},
        ]
    )
    assert drift.improvement_pct(rows) == pytest.approx(5.0)  # (20 - 10) / 2, not yards-dominated
    assert drift.improvement_pct(rows.head(0)) is None


def test_improvement_weights_weeks_by_scored_players():
    rows = pl.DataFrame(
        [
            {"week": 1, "target": "a", "n_scored": 30, "mae_model": 9.0, "mae_baseline": 10.0},
            {"week": 2, "target": "a", "n_scored": 10, "mae_model": 12.0, "mae_baseline": 10.0},
        ]
    )
    # model sum 30*9 + 10*12 = 390, baseline 400 -> 2.5%
    assert drift.improvement_pct(rows) == pytest.approx(2.5)


def test_player_signal_alert_when_behind_three_windows_in_a_row():
    sb = drift.prepare_scoreboard(scoreboard(sb_rows(2025, range(1, 7), "QB", 11.0, 10.0)), 2025, 7)
    s = drift.player_signal(sb, "QB", CFG)
    assert s.status == "alert" and s.group == "QB"
    assert s.value == pytest.approx(-10.0)
    assert "walk-forward backtest" in s.detail and "-10.0%" in s.detail
    assert "Check feature freshness" in s.response


def test_player_signal_ok_and_insufficient_and_unknown_group():
    sb = drift.prepare_scoreboard(scoreboard(sb_rows(2025, range(1, 7), "RB", 9.0, 10.0)), 2025, 7)
    assert drift.player_signal(sb, "RB", CFG).status == "ok"
    short = drift.prepare_scoreboard(
        scoreboard(sb_rows(2025, range(1, 6), "RB", 11.0, 10.0)), 2025, 6
    )
    s = drift.player_signal(short, "RB", CFG)
    assert s.status == "insufficient_data" and "5 scored week" in s.detail
    assert drift.player_signal(sb, "LB/S", CFG).status == "insufficient_data"
    assert drift.player_signal(pl.DataFrame(), "QB", CFG).status == "insufficient_data"


def test_player_signal_recovery_breaks_the_streak():
    rows = sb_rows(2025, range(1, 5), "QB", 11.0, 10.0) + sb_rows(
        2025, range(5, 7), "QB", 5.0, 10.0
    )
    sb = drift.prepare_scoreboard(scoreboard(rows), 2025, 7)
    assert drift.player_signal(sb, "QB", CFG).status == "ok"


def test_player_signal_says_live_or_backtest():
    rows = sb_rows(2026, range(1, 4), "QB", 11, 10) + sb_rows(
        2026, range(4, 8), "QB", 11, 10, "live"
    )
    sb = drift.prepare_scoreboard(scoreboard(rows), 2026, 8)
    s = drift.player_signal(sb, "QB", CFG)
    assert s.status == "alert" and "4 live weeks" in s.detail and "backtest" not in s.detail
    mixed = drift.prepare_scoreboard(scoreboard(rows), 2026, 6)  # weeks 1-5: window 2-5
    s2 = drift.player_signal(mixed, "QB", {**CFG, "min_window_weeks": 2})
    assert "walk-forward backtest" in s2.detail and "live" in s2.detail


def test_prepare_scoreboard_prefers_live_rows_and_drops_future_weeks():
    rows = sb_rows(2026, range(1, 4), "QB", 11, 10) + sb_rows(
        2026, range(3, 6), "QB", 8, 10, "live"
    )
    sb = drift.prepare_scoreboard(
        scoreboard(rows, sb_rows(2025, range(1, 3), "QB", 9, 10)), 2026, 5
    )
    assert sorted(sb["week"].to_list()) == [1, 2, 3, 4]  # week 5 is not graded for week 5's run
    wk3 = sb.filter(pl.col("week") == 3).row(0, named=True)
    assert wk3["mode"] == "live" and wk3["mae_model"] == 8  # live wins over the backtest row
    assert sb["season"].unique().to_list() == [2026]
    assert drift.prepare_scoreboard(None, 2026, 5).is_empty()


# ---- data_freshness -----------------------------------------------------------------------------


def fresh(source: str, age: float, stale: bool, ds: str = "pbp") -> dict:
    return {
        "source": source,
        "dataset": ds,
        "snapshot_date": "2026-10-01",
        "age_days": age,
        "stale": stale,
        "note": "",
    }


def test_freshness_not_reported_is_insufficient():
    assert drift.freshness_signal(None, CFG).status == "insufficient_data"
    assert drift.freshness_signal([], CFG).status == "insufficient_data"


def test_freshness_alert_names_the_stale_sources():
    s = drift.freshness_signal(
        [fresh("nflverse", 1, False), fresh("espn", 11, True, "injuries")], CFG
    )
    assert s.status == "alert" and s.value == 11 and s.threshold == 7
    assert "espn/injuries" in s.detail and "1 of 2" in s.detail
    assert "re-ingest" in s.response


def test_freshness_ok_reports_the_oldest():
    s = drift.freshness_signal([fresh("a", 2, False), fresh("b", 5, False)], CFG)
    assert s.status == "ok" and s.value == 5 and "all 2" in s.detail


# ---- checks -------------------------------------------------------------------------------------


def history(path: Path, rows: list[dict]) -> pl.DataFrame:
    out = pl.DataFrame()
    for i, r in enumerate(rows):
        base = {
            "season": 2026,
            "kind": "main",
            "run_id": f"2026100{i}T100000Z",
            "started": f"2026-10-0{i + 1}T10:00:00Z",
            "finished": f"2026-10-0{i + 1}T10:10:00Z",
            "status": "ok",
            "total_seconds": 600.0,
        }
        out = append_history(path, {**base, **r})
    return out


def test_checks_alert_above_the_failure_rate(tmp_path):
    h = history(
        tmp_path / "h.parquet",
        [
            {"week": 5, "checks_passed": True},
            {"week": 6, "checks_passed": False, "regenerated": True},
            {"week": 7, "checks_passed": True},
            {"week": 8, "checks_passed": True, "regenerated": True},
        ],
    )
    s = drift.checks_signal(h, 2026, 8, CFG)  # 1 of 4 = 25% > 20%
    assert s.status == "alert" and s.value == pytest.approx(0.25)
    assert "1 of the last 4" in s.detail and "2 needed a second writer pass" in s.detail
    assert s.weeks == [5, 6, 7, 8]
    assert "prompt" in s.response


def test_checks_ok_at_the_limit_and_when_all_pass(tmp_path):
    five = history(
        tmp_path / "h.parquet",
        [{"week": w, "checks_passed": w != 3} for w in range(1, 6)],
    )
    s = drift.checks_signal(
        five, 2026, 5, {**CFG, "checks_window_weeks": 5}
    )  # 1/5 = 20%: not above
    assert s.status == "ok" and s.value == pytest.approx(0.2)
    four_ok = five.filter(pl.col("week") >= 4)
    assert drift.checks_signal(four_ok, 2026, 5, {**CFG, "checks_window_weeks": 2}).status == "ok"


def test_checks_only_looks_at_the_window_and_ignores_non_runs_and_no_digest(tmp_path):
    rows = [{"week": 1, "checks_passed": False}]  # old failure, outside the window
    rows += [{"week": w, "checks_passed": True} for w in range(2, 6)]
    rows += [
        {"week": 6, "status": "not_ready", "checks_passed": False},  # a retry that did nothing
        {"week": 6, "checks_passed": None, "status": "failed"},  # no digest, no verdict
    ]
    h = history(tmp_path / "h.parquet", rows)
    s = drift.checks_signal(h, 2026, 6, CFG)
    assert s.status == "ok" and s.value == 0.0 and s.weeks == [2, 3, 4, 5]


def test_checks_uses_the_last_row_of_a_week_and_main_runs_only(tmp_path):
    rows = [
        {"week": 4, "checks_passed": False, "run_id": "a"},
        {"week": 4, "checks_passed": True, "run_id": "b", "started": "2026-10-09T10:00:00Z"},
        {"week": 5, "checks_passed": True},
        {"week": 6, "checks_passed": True},
        {"week": 7, "checks_passed": True},
        {"week": 7, "kind": "injury-update", "checks_passed": False},
    ]
    h = history(tmp_path / "h.parquet", rows)
    assert drift.checks_signal(h, 2026, 7, CFG).status == "ok"  # the failed first pass was redone


def test_checks_insufficient_without_a_full_window(tmp_path):
    h = history(tmp_path / "h.parquet", [{"week": 5, "checks_passed": False}])
    s = drift.checks_signal(h, 2026, 5, CFG)
    assert s.status == "insufficient_data" and "1 digest" in s.detail
    assert drift.checks_signal(None, 2026, 5, CFG).status == "insufficient_data"
    assert drift.checks_signal(pl.DataFrame(), 2026, 5, CFG).status == "insufficient_data"


# ---- evaluate_drift -----------------------------------------------------------------------------


def save_preds(root: Path, season: int, weeks: range, n: int, p_model: float, p_elo: float):
    """Saved primary predictions for `n` games a week; every home team wins."""
    games = []
    for w in weeks:
        rows = []
        for i in range(n):
            gid = f"{season}_{w:02d}_{i}"
            kick = dt.datetime(season, 9, 7, 17, tzinfo=UTC) + dt.timedelta(weeks=w - 1)
            rows.append(
                {
                    "season": season,
                    "week": w,
                    "game_id": gid,
                    "variant": "market",
                    "is_primary": True,
                    "home_win_prob": p_model,
                    "elo_prob": p_elo,
                    "market_prob": p_elo,
                    "pred_home_points": 24.0,
                    "pred_away_points": 20.0,
                    "kickoff_utc": kick,
                    "created_at": kick - dt.timedelta(days=5),
                }
            )
            games.append({"game_id": gid, "home_score": 27, "away_score": 20, "result": 7})
        d = week_dir(root, season, w)
        d.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows).write_parquet(d / PRED_FILE)
    return pl.DataFrame(games)


def by_name(signals):
    return {(s.name, s.group): s for s in signals}


def test_evaluate_drift_end_to_end_alerts(tmp_path):
    games = save_preds(tmp_path, 2026, range(1, 9), 8, p_model=0.6, p_elo=0.9)
    sb = scoreboard(
        sb_rows(2026, range(1, 5), "QB", 11, 10),
        sb_rows(2026, range(5, 9), "QB", 11, 10, "live"),
        sb_rows(2026, range(1, 9), "RB", 9, 10),
    )
    hist = history(
        tmp_path / "h.parquet", [{"week": w, "checks_passed": w != 8} for w in range(5, 9)]
    )
    sigs = drift.evaluate_drift(
        2026,
        9,
        run_root=tmp_path,
        games=games,
        scoreboard=sb,
        history=hist,
        freshness=[fresh("espn", 9, True)],
        cfg=CFG,
    )
    got = by_name(sigs)
    assert [s.name for s in sigs] == [
        "game_vs_elo",
        "calibration",
        *["player_vs_baseline"] * 5,
        "data_freshness",
        "checks",
    ]
    assert got["game_vs_elo", None].status == "alert"  # 0.6 vs 0.9 on home wins, 8 weeks
    assert got["calibration", None].status == "alert"  # 64 games, ECE 0.4
    assert got["player_vs_baseline", "QB"].status == "alert"
    assert got["player_vs_baseline", "RB"].status == "ok"
    assert got["player_vs_baseline", "EDGE/DL"].status == "insufficient_data"
    assert got["data_freshness", None].status == "alert"
    assert got["checks", None].status == "alert"  # 1 of 4 failed

    alerts = drift.drift_alerts(sigs, 2026, 9, cfg=CFG)
    assert {a.source for a in alerts} == {
        "drift:game_vs_elo",
        "drift:calibration",
        "drift:player_vs_baseline",
        "drift:data_freshness",
        "drift:checks",
    }
    assert all(a.level == "warn" for a in alerts)
    assert "2026 week 9: game model behind Elo for 3 weeks" in {a.title for a in alerts}
    assert "2026 week 9: QB projections behind the baseline for 3 weeks" in {
        a.title for a in alerts
    }


def test_evaluate_drift_grades_only_weeks_before_the_run_week(tmp_path):
    games = save_preds(tmp_path, 2026, range(1, 9), 8, p_model=0.6, p_elo=0.9)
    sigs = drift.evaluate_drift(
        2026, 8, run_root=tmp_path, games=games, scoreboard=pl.DataFrame(), cfg=CFG
    )
    cal = by_name(sigs)["calibration", None]
    # weeks 1-7 = 56 games: below the 64 minimum, so insufficient (week 8's games don't count)
    assert cal.status == "insufficient_data" and "56 graded" in cal.detail
    assert by_name(sigs)["game_vs_elo", None].weeks == [
        2,
        3,
        4,
        5,
        6,
        7,
    ]  # windows ending weeks 5-7


def test_evaluate_drift_ok_when_the_model_beats_elo(tmp_path):
    games = save_preds(tmp_path, 2026, range(1, 9), 8, p_model=0.9, p_elo=0.6)
    sigs = drift.evaluate_drift(
        2026, 9, run_root=tmp_path, games=games, scoreboard=pl.DataFrame(), cfg=CFG
    )
    got = by_name(sigs)
    assert got["game_vs_elo", None].status == "ok"
    assert got["data_freshness", None].status == "insufficient_data"  # nothing passed in
    assert got["checks", None].status == "insufficient_data"  # no history file
    assert drift.drift_alerts([got["game_vs_elo", None]], 2026, 9) == []


def test_evaluate_drift_post_kickoff_predictions_are_not_graded(tmp_path):
    games = save_preds(tmp_path, 2026, range(1, 9), 8, p_model=0.6, p_elo=0.9)
    # re-save week 3 with creation time after kickoff: those games are not graded
    d = week_dir(tmp_path, 2026, 3) / PRED_FILE
    late = pl.read_parquet(d).with_columns(
        (pl.col("kickoff_utc") + dt.timedelta(hours=1)).alias("created_at")
    )
    late.write_parquet(d)
    sigs = drift.evaluate_drift(
        2026, 9, run_root=tmp_path, games=games, scoreboard=pl.DataFrame(), cfg=CFG
    )
    cal = by_name(sigs)["calibration", None]
    assert "56 graded" in cal.detail and 3 not in cal.weeks


def test_evaluate_drift_unreadable_games_fail_soft(tmp_path):
    save_preds(tmp_path, 2026, range(1, 3), 4, p_model=0.6, p_elo=0.9)
    sigs = drift.evaluate_drift(
        2026,
        3,
        run_root=tmp_path,
        games=pl.DataFrame({"nope": [1]}),
        scoreboard=pl.DataFrame(),
        cfg=CFG,
    )
    got = by_name(sigs)
    assert got["game_vs_elo", None].status == "insufficient_data"
    assert "could not read" in got["calibration", None].detail


def test_a_game_saved_twice_counts_once(tmp_path):
    games = save_preds(tmp_path, 2026, range(1, 2), 4, p_model=0.6, p_elo=0.9)
    f = week_dir(tmp_path, 2026, 1) / PRED_FILE
    df = pl.read_parquet(f)
    pl.concat([df, df]).write_parquet(f)
    preds = pl.read_parquet(f)
    assert preds.height == 8
    assert drift.graded_games(preds, games).height == 4


# ---- alerts -------------------------------------------------------------------------------------


def test_drift_alerts_one_per_alert_signal_with_response_text():
    s = drift.calibration_signal(ece_frame([(0.9, 100, 50)]), CFG)
    alerts = drift.drift_alerts(
        [s, drift.freshness_signal([fresh("a", 1, False)], CFG), drift.freshness_signal(None, CFG)],
        2026,
        6,
    )
    assert len(alerts) == 1
    a = alerts[0]
    assert a.title == "2026 week 6: game probabilities poorly calibrated (ECE 0.400)"
    assert a.source == "drift:calibration" and a.level == "warn"
    assert s.detail in a.text and "no calibration layer" in a.text


# ---- replay -------------------------------------------------------------------------------------


def write_backtest(tmp_path: Path, seasons: dict[int, float]) -> SimpleNamespace:
    """A walk-forward game backtest where the model is `gap` worse (+) or better (-) than Elo."""
    rows = []
    for season, gap in seasons.items():
        for week in range(1, 11):
            for i in range(16):
                win = 1.0 if i % 2 == 0 else 0.0
                p_elo = 0.65 if win else 0.35
                p_model = min(max(p_elo + gap * (1 if win else -1) * -1, 0.01), 0.99)
                rows.append(
                    {
                        "season": season,
                        "week": week,
                        "game_type": "REG",
                        "home_win": win,
                        "home_win_prob": p_model,
                        "elo_prob": p_elo,
                    }
                )
        # a playoff game that must be ignored
        rows.append(
            {
                "season": season,
                "week": 19,
                "game_type": "SB",
                "home_win": 1.0,
                "home_win_prob": 0.01,
                "elo_prob": 0.99,
            }
        )
    d = tmp_path / "backtests" / "game" / "model_only"
    d.mkdir(parents=True)
    pl.DataFrame(rows).write_parquet(d / "predictions_games.parquet")
    return SimpleNamespace(runs=tmp_path)


def test_replay_fires_only_where_the_model_trails(tmp_path):
    paths = write_backtest(tmp_path, {2020: 0.1, 2021: -0.1})  # 2020 worse than Elo, 2021 better
    rep = drift.replay_drift([2020, 2021], paths=paths, cfg=CFG, players=False)
    assert set(rep["signal"].unique()) == {"game_vs_elo", "calibration"}
    assert rep["week"].min() == 2 and rep["week"].max() == 10  # playoff weeks ignored
    g = rep.filter(pl.col("signal") == "game_vs_elo")
    assert g.filter(pl.col("season") == 2021)["status"].is_in(["ok", "insufficient_data"]).all()
    bad = g.filter((pl.col("season") == 2020) & (pl.col("status") == "alert"))
    assert bad["week"].min() == 7  # graded weeks 1-6 are the first six: the earliest possible
    summ = drift.summarize_replay(rep)
    row = summ.filter((pl.col("signal") == "game_vs_elo") & (pl.col("season") == 2020)).row(
        0, named=True
    )
    assert row["alerts"] == bad.height and row["episodes"] == 1
    total = summ.filter((pl.col("signal") == "game_vs_elo") & pl.col("season").is_null())
    assert total["evaluated"].item() > 0


def test_count_episodes():
    assert drift.count_episodes([]) == 0
    assert drift.count_episodes([7, 8, 9, 12, 13, 17]) == 3
    assert drift.count_episodes([9, 7, 8]) == 1


# ---- P08: the chances (Sol review) --------------------------------------------------------------


def prob_rows(season: int, weeks: range, group: str, model: float, base: float) -> list[dict]:
    """A probability target's scoreboard rows: Brier only, MAE null (P08)."""
    return [
        {
            "season": season,
            "week": w,
            "target": "td",
            "position_group": group,
            "n_scored": 300,
            "mae_model": None,
            "mae_baseline": None,
            "brier_model": model,
            "brier_baseline": base,
            "mode": "backtest",
        }
        for w in weeks
    ]


def test_probability_targets_get_their_own_brier_signal(tmp_path):
    games = save_preds(tmp_path, 2026, range(1, 9), 8, p_model=0.6, p_elo=0.6)
    qb = [
        {**r, "brier_model": None, "brier_baseline": None}
        for r in sb_rows(2026, range(1, 9), "QB", 9, 10)
    ]
    cbs_rows = prob_rows(2026, range(1, 9), "CB/S", 0.08, 0.07)
    rb_rows = prob_rows(2026, range(1, 9), "RB", 0.15, 0.17)
    sb = pl.DataFrame([*qb, *cbs_rows, *rb_rows])
    got = by_name(
        drift.evaluate_drift(2026, 9, run_root=tmp_path, games=games, scoreboard=sb, cfg=CFG)
    )
    # the MAE signal never sees MAE-less rows: QB healthy, no CB/S MAE signal at all
    assert got["player_vs_baseline", "QB"].status == "ok"
    assert ("player_vs_baseline", "CB/S") not in got
    # the Brier twin: CB/S chances behind the rolling rate in 3 windows in a row -> alert
    cbs = got["player_prob_vs_baseline", "CB/S"]
    assert cbs.status == "alert" and cbs.value < 0 and "Brier score on its chances" in cbs.detail
    assert got["player_prob_vs_baseline", "RB"].status == "ok"
    titles = [a.title for a in drift.drift_alerts(list(got.values()), 2026, 9, cfg=CFG)]
    assert any("CB/S chances" in t for t in titles)


def test_brier_scoreboard_needs_the_columns():
    sb = scoreboard(sb_rows(2026, range(1, 5), "QB", 9, 10))  # a P06-era file: no Brier columns
    assert drift.prepare_scoreboard(sb, 2026, 5, "brier").is_empty()
    assert drift.improvement_pct(
        pl.DataFrame(prob_rows(2026, range(1, 3), "RB", 0.15, 0.20)), "brier"
    ) == pytest.approx(25.0)
