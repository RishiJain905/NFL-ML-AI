"""The live-decision backtest (LD00): metrics, the gain baseline, the leakage check, the ship
rules, and a whole leave-one-season-out run on a tiny synthetic league (no data root, no
network, no W&B)."""

from __future__ import annotations

import json
import math

import numpy as np
import polars as pl
import pytest

from nflengine.live import backtest as B
from nflengine.live import models as M
from nflengine.live import schema
from nflengine.paths import DataPaths

SEASONS = (2018, 2019, 2020, 2021)
SMALL = {
    "wp": {"n_estimators": 15, "min_data_in_leaf": 20},
    "gain": {"n_estimators": 4, "min_data_in_leaf": 20},
    "pass": {"n_estimators": 15, "min_data_in_leaf": 20},
    "punt": {"min_effective_n": 20.0},
    "threads": 1,
}


# --- a tiny synthetic league ----------------------------------------------------------------------
def _sig(x):
    return 1.0 / (1.0 + np.exp(-x))


def _snaps(rng, season: int, n: int) -> dict[str, np.ndarray]:
    """Offense-side state columns for `n` snaps of one season (consistent clocks)."""
    gs = rng.integers(0, 3600, n).astype(float)
    hs = np.where(gs > 1800, gs - 1800, gs)
    qtr = np.select([gs > 2700, gs > 1800, gs > 900], [1, 2, 3], 4)
    diff = rng.integers(-17, 18, n)
    spread = rng.integers(-9, 10, n).astype(float)
    share = (3600.0 - gs) / 3600.0
    decay = np.exp(-4.0 * share)
    yl = rng.integers(5, 95, n)
    down = rng.integers(1, 5, n)
    togo = np.minimum(rng.integers(1, 15, n), yl)
    return {
        "season": np.full(n, season),
        "game_id": np.array([f"{season}_{i // 40:03d}" for i in range(n)]),
        "play_id": np.arange(n),
        "qtr": qtr,
        "score_diff": diff,
        "diff_time_ratio": diff / decay,
        "spread": spread,
        "spread_time": spread * decay,
        "game_seconds": gs,
        "half_seconds": hs,
        "yardline_100": yl,
        "down": down,
        "ydstogo": togo,
        "off_timeouts": rng.integers(0, 4, n),
        "def_timeouts": rng.integers(0, 4, n),
        "home": rng.choice([-1, 1], n),
        "receive_2h_ko": np.where(gs > 1800, rng.integers(0, 2, n), 0),
        "ot": np.zeros(n, dtype=int),
        "era": np.full(n, schema.era_of(season)),
        "indoor": rng.integers(0, 2, n),
        "total": rng.uniform(38, 52, n),
    }


def synth_frames(seasons=SEASONS, seed: int = 3) -> tuple[dict[str, pl.DataFrame], dict]:
    rng = np.random.default_rng(seed)
    parts: dict[str, list[pl.DataFrame]] = {
        k: [] for k in ("wp", "gain", "fg", "punt", "kickoff", "pass", "plays", "fourth")
    }
    for s in seasons:
        sn = _snaps(rng, s, 600)
        z = 0.12 * sn["score_diff"] * (1 + (3600 - sn["game_seconds"]) / 1800) + 0.08 * sn["spread"]
        win = (rng.random(600) < _sig(z)).astype(np.int8)
        vegas = np.clip(_sig(z + rng.normal(0, 0.3, 600)), 0.001, 0.999)
        vegas = [None if i % 25 == 0 else float(v) for i, v in enumerate(vegas)]
        parts["wp"].append(pl.DataFrame({**sn, "win": win, "vegas_wp": vegas}))

        p = pl.DataFrame(sn).with_columns(
            pl.Series("is_pass", (rng.random(600) < 0.3 + 0.04 * sn["ydstogo"]).astype(np.int8)),
            pl.Series(
                "xpass",
                [
                    None if i % 30 == 0 else float(v)
                    for i, v in enumerate(rng.uniform(0.2, 0.9, 600))
                ],
            ),
            pl.Series("wp", rng.uniform(0, 1, 600)),  # nflfastR's; the backtest replaces it
        )
        parts["pass"].append(p)

        g = _snaps(rng, s, 500)
        g["down"] = rng.integers(3, 5, 500)
        yards = np.round(rng.normal(4.0, 6.0, 500)).astype(int)
        td = yards >= g["yardline_100"]
        yards = np.minimum(yards, g["yardline_100"] - 1)
        g["team_total"] = g["total"] / 2 + g["spread"] / 2
        parts["gain"].append(
            pl.DataFrame(g).with_columns(
                pl.Series(
                    "gain_class",
                    [schema.gain_class(y, t) for y, t in zip(yards, td, strict=True)],
                    dtype=pl.Int32,
                ),
                pl.Series("converted", ((yards >= g["ydstogo"]) | td).astype(np.int8)),
            )
        )

        d = rng.integers(19, 60, 200).astype(float)
        parts["fg"].append(
            pl.DataFrame(
                {
                    "season": np.full(200, s),
                    "game_id": [f"{s}_{i // 5:03d}" for i in range(200)],
                    "play_id": np.arange(200),
                    "distance": d,
                    "make": (rng.random(200) < _sig(5.0 - 0.1 * d)).astype(np.int8),
                    "era": np.full(200, schema.era_of(s)),
                    "indoor": rng.integers(0, 2, 200),
                    "wind_out": [None if i % 3 else float(rng.integers(0, 20)) for i in range(200)],
                    "temp_out": [
                        None if i % 4 else float(rng.integers(20, 90)) for i in range(200)
                    ],
                }
            )
        )

        for name, n, start in (("punt", 150, 0), ("kickoff", 150, 1000)):
            out = rng.choice(["recv", "recv", "recv", "recv", "keep", "ret_td"], n)
            spot = np.where(out == "ret_td", 0, rng.integers(55, 90, n))
            df = pl.DataFrame(
                {
                    "season": np.full(n, s),
                    "game_id": [f"{s}_{i // 5:03d}" for i in range(n)],
                    "play_id": start + np.arange(n),
                    "yardline_100": rng.integers(35, 90, n),
                    "outcome": out,
                    "spot": [
                        None if o == "ret_td" else int(v) for o, v in zip(out, spot, strict=True)
                    ],
                }
            )
            if name == "kickoff":
                df = df.with_columns(
                    pl.Series("onside", np.arange(n) % 50 == 0),
                    pl.lit(schema.kickoff_era(s)).alias("ko_era"),
                )
            parts[name].append(df)

        parts["plays"].append(
            pl.DataFrame(
                {
                    "season": np.full(100, s),
                    "game_id": [f"{s}_{i // 5:03d}" for i in range(100)],
                    "extra_point_attempt": np.r_[np.ones(80), np.zeros(20)].astype(int),
                    "extra_point_result": ["good"] * 76 + ["failed"] * 4 + [None] * 20,
                    "two_point_attempt": np.r_[np.zeros(80), np.ones(20)].astype(int),
                    "two_point_conv_result": [None] * 80 + ["success"] * 10 + ["failure"] * 10,
                }
            )
        )

        f = _snaps(rng, s, 30)
        f["down"] = np.full(30, 4)
        f["ydstogo"][:2] = f["yardline_100"][:2] + 1  # two states that fail the input checks
        f["choice"] = rng.choice(["go", "fg", "punt"], 30)
        f["playoffs"] = np.zeros(30, dtype=bool)
        f["wind"] = [None] * 30
        f["temp"] = [None] * 30
        parts["fourth"].append(pl.DataFrame(f))
    frames = {k: pl.concat(v, how="vertical_relaxed") for k, v in parts.items()}
    return frames, {"rows": frames["wp"].height, "tie_rows": 0, "tie_games": 0}


@pytest.fixture
def league(monkeypatch):
    frames, counts = synth_frames()
    calls = []

    def fake_load(seasons, gain_ratings=False, paths=None):
        calls.append(sorted(seasons))
        return {k: v.clone() for k, v in frames.items()}, counts

    monkeypatch.setattr(B, "load_frames", fake_load)
    monkeypatch.setattr(B.models, "settings", lambda overrides=None: _cfg(overrides))
    return frames, calls


def _cfg(overrides=None):
    out = json.loads(json.dumps(M.DEFAULTS))
    for k, v in (overrides or {}).items():
        if isinstance(v, dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


# --- metrics --------------------------------------------------------------------------------------
def test_brier_log_loss_and_ece_known_values():
    p, y = [0.2, 0.8, 0.5, 0.5], [0, 1, 1, 0]
    assert B.brier(p, y) == pytest.approx((0.04 + 0.04 + 0.25 + 0.25) / 4)
    assert B.log_loss(p, y) == pytest.approx(-(2 * math.log(0.8) + 2 * math.log(0.5)) / 4)
    # perfectly calibrated bins -> 0; one bin off by 0.3 on half the rows -> 0.15
    assert B.ece([0.5, 0.5], [1, 0]) == pytest.approx(0.0)
    assert B.ece([0.5, 0.5, 0.2, 0.2], [1, 0, 1, 0]) == pytest.approx(0.15)
    # log loss clips: a certain miss costs -log(1e-6), not infinity
    assert B.log_loss([0.0], [1]) == pytest.approx(-math.log(B.EPS))
    assert math.isnan(B.brier([], []))


def test_reliability_uses_twenty_equal_width_bins():
    r = B.reliability([0.0, 0.049, 0.05, 0.999, 1.0], [0, 0, 1, 1, 1])
    assert r["bin"].to_list() == [0, 1, 19]
    assert r["n"].to_list() == [2, 1, 2]
    assert r["bin_lo"].to_list() == pytest.approx([0.0, 0.05, 0.95])


def test_auc_needs_both_classes():
    assert B.auc([0.1, 0.9], [0, 1]) == pytest.approx(1.0)
    assert math.isnan(B.auc([0.1, 0.9], [1, 1]))


def test_groupings():
    df = pl.DataFrame(
        {
            "score_diff": [0, -8, 8, 9, -21],
            "qtr": [1.0, 2.0, 4.0, 5.0, 2.0],  # nflverse stores quarters as floats
            "half_seconds": [900, 120, 30, 300, 121],
        }
    ).with_columns(
        B.score_state_expr().alias("ss"),
        B.quarter_expr().alias("q"),
        B.two_minute_expr().alias("tm"),
    )
    assert df["ss"].to_list() == ["tied", "1-8", "1-8", "9+", "9+"]
    assert df["q"].to_list() == ["Q1", "Q2", "Q4", "OT", "Q2"]
    assert df["tm"].to_list() == [None, "Q2 last 2:00", "Q4 last 2:00", None, None]
    bands = pl.DataFrame({"distance": [29.0, 30, 49, 50, 54, 55, 63]}).select(B.fg_band_expr())
    assert bands.to_series().to_list() == ["<=29", "30-39", "40-49", "50-54", "50-54", "55+", "55+"]


# --- the gain baseline ----------------------------------------------------------------------------
def test_conversion_baseline_cells_and_fallbacks():
    rows = (
        [(1, 0, 1)] * 30 + [(1, 0, 0)] * 10  # distance 1, era 0: 30/40 = 0.75
        + [(1, 1, 1)] * 5  # distance 1, era 1: 5 rows (< 30) -> distance-only 35/45
        + [(25, 0, 1)] * 20 + [(30, 0, 0)] * 20  # 21+ bucket: 0.5 (era 0 has 40 rows)
    )  # fmt: skip
    train = pl.DataFrame(rows, schema=["ydstogo", "era", "converted"], orient="row")
    predict = B.conversion_baseline(train)
    test = pl.DataFrame({"ydstogo": [1, 1, 22, 7, 1], "era": [0, 1, 0, 0, 4]})
    got = predict(test)
    overall = (30 + 5 + 20) / 85
    assert got == pytest.approx([0.75, 35 / 45, 0.5, overall, 35 / 45])


# --- leakage --------------------------------------------------------------------------------------
def _leak_frames():
    return {
        "wp": pl.DataFrame({"season": [2019, 2019, 2020], "game_id": ["a", "b", "c"]}),
        "fg": pl.DataFrame({"season": [2019, 2020, 2020]}),
    }


def test_check_no_leak_passes_a_clean_fold():
    B.check_no_leak(_leak_frames(), 2020, [2019], {"wp": 2, "fg": 1}, [2019])


@pytest.mark.parametrize(
    "kw, match",
    [
        ({"fit_seasons": [2019, 2020]}, "among the fit seasons"),
        ({"model_seasons": [2019, 2020]}, "record season 2020"),
        ({"fit_rows": {"wp": 3, "fg": 1}}, "the fit saw 3 rows"),
    ],
)
def test_check_no_leak_raises(kw, match):
    args = {"fit_seasons": [2019], "fit_rows": {"wp": 2, "fg": 1}, "model_seasons": [2019], **kw}
    with pytest.raises(B.LeakageError, match=match):
        B.check_no_leak(_leak_frames(), 2020, **args)


def test_check_no_leak_catches_a_shared_game():
    frames = {"wp": pl.DataFrame({"season": [2019, 2020], "game_id": ["x", "x"]})}
    with pytest.raises(B.LeakageError, match="shares a game"):
        B.check_no_leak(frames, 2020, [2019])


# --- the ship rules -------------------------------------------------------------------------------
def _by_season(gain_wins=9, fg_wins=9, pass_wins=9, seasons=B.HELD_OUT):
    n = len(seasons)
    return pl.DataFrame(
        {
            "season": seasons,
            "gain_ll": [0.5 if i < gain_wins else 0.6 for i in range(n)],
            "gain_ll_base": [0.55] * n,
            "fg_ll": [0.3 if i < fg_wins else 0.4 for i in range(n)],
            "fg_ll_base": [0.35] * n,
            "pass_ll": [0.5 if i < pass_wins else 0.6 for i in range(n)],
            "pass_ll_xpass": [0.55] * n,
        }
    )


GOOD = {"wp_ece": 0.010, "wp_brier": 0.1602, "wp_brier_vegas": 0.1582}


def test_ship_verdict_passes_at_the_edges():
    v = B.ship_verdict(_by_season(), GOOD)
    assert v["wp_pass"] and v["gain_pass"] and v["fg_pass"] and v["pass_pass"]
    assert v["gain_seasons_won"] == 9 and v["seasons_needed"] == 9
    assert v["full_backtest"] and v["all_pass"]


@pytest.mark.parametrize(
    "pooled, kw, failed",
    [
        ({**GOOD, "wp_ece": 0.0101}, {}, "wp_pass"),
        ({**GOOD, "wp_brier": 0.16021}, {}, "wp_pass"),
        (GOOD, {"gain_wins": 8}, "gain_pass"),
        (GOOD, {"fg_wins": 8}, "fg_pass"),
        (GOOD, {"pass_wins": 8}, "pass_pass"),
    ],
)
def test_ship_verdict_misses(pooled, kw, failed):
    v = B.ship_verdict(_by_season(**kw), pooled)
    assert not v[failed] and not v["all_pass"]


def test_ship_verdict_ties_and_missing_values_are_not_wins():
    df = _by_season().with_columns(
        pl.when(pl.col("season") == 2014).then(0.55).otherwise(pl.col("gain_ll")).alias("gain_ll"),
        pl.when(pl.col("season") == 2015)
        .then(None)
        .otherwise(pl.col("gain_ll_base"))
        .alias("gain_ll_base"),
        pl.when(pl.col("season") == 2016)
        .then(float("nan"))
        .otherwise(pl.col("fg_ll_base"))
        .alias("fg_ll_base"),
    )
    v = B.ship_verdict(df, GOOD)
    assert v["gain_seasons_won"] == 7  # 2014 a tie, 2015 missing
    assert v["fg_seasons_won"] == 8  # NaN baseline is not a win (Polars sorts NaN above all)


def test_ship_verdict_needs_the_full_run():
    v = B.ship_verdict(_by_season(seasons=[2024, 2025], gain_wins=2, fg_wins=2, pass_wins=2), GOOD)
    assert v["seasons_needed"] == 2 and v["rules_1_4_pass"]
    assert not v["full_backtest"] and not v["all_pass"]


def test_ratings_verdict():
    main = _by_season()
    better = main.with_columns(pl.col("gain_ll") - 0.01)
    v = B.ratings_verdict(main, better, {"gain_ll": 0.5}, {"gain_ll": 0.49})
    assert v["seasons_won"] == 12 and v["keep"]
    v = B.ratings_verdict(main, better, {"gain_ll": 0.5}, {"gain_ll": 0.5})
    assert not v["keep"]  # not better pooled
    worse = main.with_columns(
        pl.when(pl.col("season") < 2018)
        .then(pl.col("gain_ll") + 0.01)
        .otherwise(pl.col("gain_ll") - 0.01)
    )
    assert not B.ratings_verdict(main, worse, {"gain_ll": 0.5}, {"gain_ll": 0.4})["keep"]


# --- the whole run --------------------------------------------------------------------------------
def test_run_backtest_end_to_end(league, tmp_path):
    frames, calls = league
    seen = []
    real_fit = M.fit_all

    def spy(fr, seasons, **kw):
        seen.append(sorted(seasons))
        assert kw["n_boot"] == 0
        return real_fit(fr, seasons, **kw)

    import nflengine.live.backtest as mod

    mod.models.fit_all = spy
    try:
        out = B.run_backtest(
            seasons=[2020, 2021],
            train_seasons=SEASONS,
            use_wandb=False,
            paths=DataPaths(tmp_path),
            overrides=SMALL,
            log=lambda m: None,
        )
    finally:
        mod.models.fit_all = real_fit
    assert calls == [list(SEASONS)]
    assert seen == [[2018, 2019, 2021], [2018, 2019, 2020]]
    assert [r["season"] for r in out["by_season"]] == [2020, 2021]
    row = out["by_season"][0]
    assert row["dec_skipped"] == 2 and row["dec_n"] == 28
    assert row["wp_dropped_no_vegas"] == 24 and row["wp_n"] == 576
    for key in ("wp_brier", "wp_brier_vegas", "wp_ece", "gain_ll", "gain_ll_base", "gain_ll_3rd",
                "gain_ll_4th", "gain_mll", "fg_ll", "fg_ll_base", "pass_ll", "pass_ll_xpass",
                "pass_auc", "pass_auc_xpass", "punt_start_pred", "punt_start_actual",
                "kick_start_pred", "kick_start_actual", "go_rate_coach", "go_rate_bot", "agree",
                "toss_up_share", "wp_cost_total"):  # fmt: skip
        assert math.isfinite(row[key]), key
    assert row["wp_cost_total"] >= 0.0 and row["agree"] <= 1.0
    assert out["ship"]["seasons"] == 2 and not out["ship"]["all_pass"]
    assert out["label"] == "main" and "wandb" not in out

    folder = tmp_path / "runs" / "backtests" / "live-decisions" / "main"
    files = sorted(p.name for p in folder.iterdir())
    assert files == [
        "decisions.parquet", "predictions_fg.parquet", "predictions_gain.parquet",
        "predictions_kickoff.parquet", "predictions_pass.parquet", "predictions_punt.parquet",
        "predictions_wp.parquet", "summary.json",
    ]  # fmt: skip
    for f in files[:-1]:
        assert set(pl.read_parquet(folder / f)["season"].unique()) == {2020, 2021}, f
    dec = pl.read_parquet(folder / "decisions.parquet")
    priced = dec.filter(pl.col("cost").is_not_null())
    best_wp = priced.select(pl.max_horizontal("wp_go", "wp_fg", "wp_punt")).to_series()
    assert (priced["cost"] >= -1e-12).all()
    assert (best_wp - priced["cost"]).to_list() == pytest.approx(
        [r[f"wp_{r['choice']}"] for r in priced.iter_rows(named=True)]
    )
    saved = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert saved["ship"]["seasons"] == 2 and len(saved["by_season"]) == 2
    assert set(saved["tables"]) >= {
        "wp/calibration", "wp/calibration_by_quarter", "wp/by_quarter", "wp/by_score_state",
        "gain/conversion_by_distance", "fg/by_distance", "decisions/by_season",
    }  # fmt: skip
    # the kickoff scoring leaves onside kicks out, as the fit does
    kick = pl.read_parquet(folder / "predictions_kickoff.parquet")
    assert (
        kick.height
        == frames["kickoff"].filter(pl.col("season").is_in([2020, 2021]) & ~pl.col("onside")).height
    )


def test_run_backtest_stops_on_a_leaking_fit(league, monkeypatch, tmp_path):
    real_fit = M.fit_all

    def leaky(fr, seasons, **kw):
        return real_fit(fr, sorted(set(seasons) | {2021}), **kw)

    monkeypatch.setattr(B.models, "fit_all", leaky)
    with pytest.raises(B.LeakageError):
        B.run_backtest(
            seasons=[2021], train_seasons=SEASONS, use_wandb=False, save=False,
            paths=DataPaths(tmp_path), overrides=SMALL, log=lambda m: None,
        )  # fmt: skip
    assert not (tmp_path / "runs").exists()


BT_KEYS = [
    "bt/wp_brier", "bt/wp_brier_vegas", "bt/gain_ll", "bt/gain_ll_base", "bt/fg_ll",
    "bt/pass_ll_xpass", "bt/go_rate_coach", "bt/wp_cost_total", "bt/gain_wins_cum",
]  # fmt: skip
END_KEYS = [
    "wp/calibration", "wp/calibration_table", "wp/calibration_by_quarter", "wp/by_quarter",
    "wp/by_score_state", "gain/conversion_by_distance", "gain/conversion_by_distance_table",
    "fg/by_distance", "decisions/go_rate_vs_bot", "decisions/go_rate_vs_bot_table",
    "decisions/by_season", "decisions/costliest", "backtest/by_season",
]  # fmt: skip


class FakeRun:
    def __init__(self):
        self.logged, self.metrics, self.summary, self.finished = [], [], {}, None
        self.id, self.url = "abc123", "https://wandb.example/run"

    def define_metric(self, name, step_metric=None):
        self.metrics.append((name, step_metric))

    def log(self, d):
        self.logged.append(d)

    def finish(self, exit_code=0):
        self.finished = exit_code


def test_run_backtest_logs_live_to_wandb(league, monkeypatch, tmp_path):
    from nflengine import tracking

    run, init = FakeRun(), {}

    def fake_init(**kw):
        init.update(kw)
        return run

    monkeypatch.setattr(tracking, "init_run", fake_init)
    monkeypatch.setattr(tracking, "dataset_version", lambda paths=None: {"pbp_snapshot": "x"})
    monkeypatch.setattr(tracking, "git_commit", lambda: "abc")
    out = B.run_backtest(
        seasons=[2020, 2021], train_seasons=SEASONS, smoke=False, save=False, use_wandb=True,
        launched_by="agent", paths=DataPaths(tmp_path), overrides=SMALL, log=lambda m: None,
    )  # fmt: skip
    assert init["group"] == "live-decisions" and init["job_type"] == "live-backtest"
    assert init["tags"] == ["ld00", "loso"] and init["launched_by"] == "agent"
    assert init["config"]["held_out_seasons"] == [2020, 2021]
    assert init["config"]["settings"]["wp"]["n_estimators"] == 15
    assert ("bt/*", "bt/season") in run.metrics
    points = [d for d in run.logged if "bt/season" in d]
    assert [d["bt/season"] for d in points] == [2020, 2021]
    for key in BT_KEYS:
        assert key in points[0], key
    final = run.logged[-1]
    for key in END_KEYS:
        assert key in final, key
    assert "ship/all_pass" in run.summary and "ship/gain_seasons_won" in run.summary
    assert "pooled/wp_brier" in run.summary
    assert run.finished == 0 and out["wandb"]["id"] == "abc123"


def test_run_backtest_finishes_the_run_on_error(league, monkeypatch, tmp_path):
    from nflengine import tracking

    run = FakeRun()
    monkeypatch.setattr(tracking, "init_run", lambda **kw: run)
    monkeypatch.setattr(tracking, "dataset_version", lambda paths=None: {})
    monkeypatch.setattr(tracking, "git_commit", lambda: "abc")

    def boom(*a, **kw):
        raise RuntimeError("fit failed")

    monkeypatch.setattr(B.models, "fit_all", boom)
    with pytest.raises(RuntimeError, match="fit failed"):
        B.run_backtest(
            seasons=[2021], train_seasons=SEASONS, save=False, paths=DataPaths(tmp_path),
            overrides=SMALL, log=lambda m: None, smoke=True,
        )  # fmt: skip
    assert run.finished == 1


def test_smoke_holds_out_2024_and_2025(league, monkeypatch, tmp_path):
    seen = []

    def fake_fit(fr, seasons, **kw):
        seen.append(sorted(seasons))
        raise RuntimeError("stop")

    monkeypatch.setattr(B.models, "fit_all", fake_fit)
    with pytest.raises(RuntimeError):
        B.run_backtest(
            seasons=[2020], train_seasons=SEASONS, smoke=True, use_wandb=False, save=False,
            paths=DataPaths(tmp_path), log=lambda m: None,
        )  # fmt: skip
    assert seen == [list(SEASONS)]  # first fold = 2024, not in the synthetic seasons
