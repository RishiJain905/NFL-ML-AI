"""Game model v0: heads, sigma, calibration, score/margin consistency, production table."""

import datetime as dt

import numpy as np
import polars as pl
import pytest

from nflengine.features.asof import AsOf
from nflengine.models.backtest import SampleWeights, walk_forward, week_keys
from nflengine.models.game_model import (
    GameModelConfig,
    GameWeekModel,
    coefficients,
    feature_hash,
    fit_calibrator,
    fit_game_model,
)
from nflengine.models.game_runs import (
    assemble_predictions,
    confidence_label,
    pooled_summary,
)

MF = ("net_diff", "elo_diff", "hfa")
TF = ("off_sum", "league_ppg")


def synth(n_seasons: int = 6, per_week: int = 14, weeks: int = 17, seed: int = 3) -> pl.DataFrame:
    """Margins from known effects: 40 points per unit net_diff, 2.5 home field, sd 13."""
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(2010, 2010 + n_seasons):
        for w in range(1, weeks + 1):
            for i in range(per_week):
                net = rng.normal(0, 0.12)
                elo = net * 3 + rng.normal(0, 0.3)
                hfa = 0.0 if i == 0 else 1.0
                off = rng.normal(0, 0.1)
                mean_margin = 40 * net + 2.5 * hfa
                margin = float(np.round(mean_margin + rng.normal(0, 13)))
                total = float(max(3, np.round(44 + 60 * off + rng.normal(0, 10))))
                spread = float(np.round(mean_margin * 2) / 2) if i != 1 else None
                rows.append(
                    {
                        "season": s,
                        "week": w,
                        "game_id": f"{s}_{w:02d}_{i}",
                        "game_type": "REG",
                        "kickoff_utc": dt.datetime(s, 9, 10, tzinfo=dt.UTC)
                        + dt.timedelta(days=7 * w, minutes=i),
                        "home_team": f"H{i}",
                        "away_team": f"A{i}",
                        "neutral_site": hfa == 0.0,
                        "net_diff": net,
                        "elo_diff": elo,
                        "hfa": hfa,
                        "off_sum": off,
                        "league_ppg": 22.0,
                        "spread_line": spread,
                        "total_line": 44.0,
                        "margin": margin,
                        "total": total,
                        "home_score": (total + margin) / 2,
                        "away_score": (total - margin) / 2,
                        "home_win": 1.0 if margin > 0 else (0.0 if margin < 0 else 0.5),
                        "elo_prob": 0.5,
                        "elo_margin": 0.0,
                        "market_ml_prob": 0.5,
                        "market_source": "nflverse_schedules",
                        "pts_base_home": 22.0,
                        "pts_base_away": 22.0,
                        "pts_base_total": 44.0,
                        "mkt_home_points": 22.0,
                        "mkt_away_points": 22.0,
                    }
                )
    return pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


def cfg(**kw) -> GameModelConfig:
    return GameModelConfig(margin_features=MF, total_features=TF, **kw)


def test_margin_head_recovers_effects_and_scores_match_margin() -> None:
    f = synth()
    fit = fit_game_model(f, np.ones(f.height), cfg())
    co = {
        r["feature"]: r["coef"]
        for r in coefficients(fit).iter_rows(named=True)
        if r["head"] == "margin"
    }
    assert co["hfa"] == pytest.approx(2.5, abs=0.8)
    assert co["net_diff"] + 3 * co["elo_diff"] == pytest.approx(40, rel=0.15)
    p = fit.predict(f.head(50))
    gap = p["pred_home_points"] - p["pred_away_points"] - p["expected_margin"]
    assert gap.abs().max() < 1e-9
    assert ((p["pred_home_points"] + p["pred_away_points"]) - p["pred_total"]).abs().max() < 1e-9
    assert p["sigma"][0] == 13.5  # no walk-forward history -> default


def test_neutral_equal_teams_predict_zero_margin() -> None:
    f = synth()
    fit = fit_game_model(f, np.ones(f.height), cfg())
    even = f.head(1).with_columns(
        pl.lit(0.0).alias("net_diff"),
        pl.lit(0.0).alias("elo_diff"),
        pl.lit(0.0).alias("hfa"),
        pl.lit(True).alias("neutral_site"),
    )
    p = fit.predict(even)
    assert p["expected_margin"][0] == pytest.approx(0.0)
    assert p["home_win_prob"][0] == pytest.approx(0.5)
    assert p["home_base_prob"][0] == 0.5


def test_walk_forward_sigma_comes_from_earlier_residuals() -> None:
    f = synth()
    keys = week_keys(f, [2013, 2014, 2015])
    preds = walk_forward(
        f,
        keys,
        GameWeekModel(cfg()),
        label=["margin", "total", "home_win"],
        weights=SampleWeights(),
        min_train_rows=100,
    )
    first = preds.filter(pl.col("season") == 2013)["sigma"]
    assert first[0] == 13.5
    late = preds.filter(pl.col("season") == 2015)["sigma"]
    assert 11 < late[-1] < 15 and late[-1] != 13.5
    # every week's sigma equals the RMSE of the residuals predicted before it
    wk = preds.filter((pl.col("season") == 2015) & (pl.col("week") == 9))
    before = preds.filter(
        (pl.col("season") < 2015) | ((pl.col("season") == 2015) & (pl.col("week") < 9))
    )
    rmse = float(np.sqrt(((before["margin"] - before["expected_margin"]) ** 2).mean()))
    assert wk["sigma"][0] == pytest.approx(rmse)
    s = pooled_summary(preds)
    assert s["score_margin_max_gap"] < 1e-9
    assert s["brier_model"] < 0.25


def test_market_variant_needs_a_line() -> None:
    f = synth()
    mk = cfg().with_variant("market")
    assert "spread_line" in mk.mfeats and "total_line" in mk.tfeats
    fit = fit_game_model(f.filter(pl.col("spread_line").is_not_null()), np.ones(f.height - 102), mk)
    p = fit.predict(f.head(14))
    assert p.filter(pl.col("game_id").str.ends_with("_1"))["home_win_prob"].null_count() == 1
    assert p["home_win_prob"].null_count() == 1


def test_logistic_head_and_calibration() -> None:
    f = synth()
    hist = f.with_columns(
        pl.lit(0.5).alias("home_win_prob_raw"), pl.lit(10.0).alias("expected_margin")
    )
    fit = fit_game_model(
        f, np.ones(f.height), cfg(win_method="logistic", calibration="platt"), history=hist
    )
    assert fit.logit_model is not None and fit.calibrator is not None
    p = fit.predict(f.head(20))["home_win_prob"].to_numpy()
    assert np.all((p > 0) & (p < 1))
    rng = np.random.default_rng(1)
    raw = rng.uniform(0.05, 0.95, 4000)
    y = (rng.uniform(size=raw.size) < 0.5 + 0.5 * (raw - 0.5)).astype(float)  # overconfident
    cal = fit_calibrator("platt", raw, y)
    assert abs(cal(np.array([0.9]))[0] - 0.7) < 0.05
    assert fit_calibrator("none", raw, y) is None
    iso = fit_calibrator("isotonic", raw, y)
    assert 0.6 < iso(np.array([0.9]))[0] < 0.8


def test_config_validation_and_hash() -> None:
    with pytest.raises(ValueError):
        GameModelConfig(variant="vegas")
    with pytest.raises(ValueError):
        GameModelConfig(calibration="magic")
    with pytest.raises(ValueError):
        GameModelConfig.from_config({"unknown_key": 1})
    c = GameModelConfig.from_config({"win_method": "logistic", "margin_features": list(MF)})
    assert c.mfeats == MF and c.win_method == "logistic"
    assert feature_hash(["b", "a"]) == feature_hash(["a", "b"])


def test_production_table_marks_primary_rows_and_fallback() -> None:
    f = synth(n_seasons=3)
    train = f.filter(pl.col("season") < 2012)
    week = f.filter((pl.col("season") == 2012) & (pl.col("week") == 1)).with_columns(
        pl.lit("Some QB").alias("home_qb_name"), pl.lit(None, dtype=pl.String).alias("away_qb_name")
    )
    w = np.ones(train.height)
    preds = {
        "model_only": fit_game_model(train, w, cfg()).predict(week),
        "market": fit_game_model(
            train.filter(pl.col("spread_line").is_not_null()),
            np.ones(train.filter(pl.col("spread_line").is_not_null()).height),
            cfg().with_variant("market"),
        ).predict(week),
    }
    t = assemble_predictions(
        preds, week, "game-model-v0:2012-w01", {"model_only": "h1", "market": "h2"}, "2011-w17"
    )
    prim = t.filter(pl.col("is_primary"))
    assert prim.height == week.height  # exactly one digest row per game
    nol = prim.filter(pl.col("game_id") == "2012_01_1").row(0, named=True)
    assert nol["variant"] == "model_only" and nol["market_fallback"] is True
    assert (prim.filter(pl.col("game_id") != "2012_01_1")["variant"] == "market").all()
    assert ((t["home_win_prob"] + t["away_win_prob"] - 1).abs() < 1e-12).all()
    assert t["home_qb_name"].drop_nulls().unique().to_list() == ["Some QB"]
    assert t.height == 2 * week.height - 1


def test_confidence_labels() -> None:
    assert confidence_label(0.52) == "toss-up"
    assert confidence_label(0.4) == "lean"
    assert confidence_label(0.75) == "solid"
    assert confidence_label(0.05) == "strong"
    assert confidence_label(None) is None


def test_harness_with_game_model_never_trains_on_the_predicted_week() -> None:
    f = synth(n_seasons=2)
    seen = []

    class Spy(GameWeekModel):
        def __call__(self, train, weights, test, history):
            seen.append(
                (
                    train["season"].max(),
                    int(train.filter(pl.col("season") == test["season"][0])["week"].max() or 0),
                    int(test["week"][0]),
                )
            )
            return super().__call__(train, weights, test, history)

    walk_forward(f, [AsOf(2011, 5)], Spy(cfg()), label=["margin", "total", "home_win"])
    assert seen == [(2011, 4, 5)]


def test_backtest_label_keeps_research_runs_apart() -> None:
    from nflengine.models.game_runs import backtest_label

    assert backtest_label("model_only") == "model_only"
    assert backtest_label("model_only", win_method=None, calibration=None) == "model_only"
    assert backtest_label("model_only", "actual") == "model_only_qb-actual"
    lbl = backtest_label("market", calibration="platt", weights=SampleWeights.for_current(5))
    assert lbl == "market_calibration-platt_weights-5-1.5-1"


def test_incomplete_market_row_never_becomes_the_digest_row() -> None:
    f = synth(n_seasons=3)
    train = f.filter(pl.col("season") < 2012)
    week = f.filter((pl.col("season") == 2012) & (pl.col("week") == 1)).with_columns(
        # game _2: spread but no total; game _3: total but no spread
        pl.when(pl.col("game_id") == "2012_01_2")
        .then(None)
        .otherwise(pl.col("total_line"))
        .alias("total_line"),
        pl.when(pl.col("game_id") == "2012_01_3")
        .then(None)
        .otherwise(pl.col("spread_line"))
        .alias("spread_line"),
    )
    lined = train.filter(pl.col("spread_line").is_not_null())
    preds = {
        "model_only": fit_game_model(train, np.ones(train.height), cfg()).predict(week),
        "market": fit_game_model(
            lined, np.ones(lined.height), cfg().with_variant("market")
        ).predict(week),
    }
    raw = preds["market"].filter(pl.col("game_id") == "2012_01_2")
    assert raw["home_win_prob"][0] is not None and raw["pred_total"][0] is None  # the hazard
    t = assemble_predictions(preds, week, "v", {"model_only": "h1", "market": "h2"}, "2011-w17")
    prim = t.filter(pl.col("is_primary"))
    assert prim.height == week.height
    for gid in ("2012_01_1", "2012_01_2", "2012_01_3"):
        row = prim.filter(pl.col("game_id") == gid).row(0, named=True)
        assert row["variant"] == "model_only" and row["market_fallback"] is True
    assert prim.select("pred_home_points", "pred_away_points", "pred_total").null_count().row(
        0
    ) == (0, 0, 0)


def test_baselines_are_scored_on_the_same_games_as_the_model() -> None:
    from nflengine.models.game_runs import pooled_summary

    f = synth(n_seasons=4)
    keys = week_keys(f, [2013])
    preds = walk_forward(
        f, keys, GameWeekModel(cfg()), label=["margin", "total", "home_win"], min_train_rows=100
    )
    full = pooled_summary(preds)
    # knock out the market baseline on half the games: every metric must drop those games
    holed = preds.with_columns(
        pl.when(pl.int_range(pl.len()) % 2 == 0)
        .then(None)
        .otherwise(pl.col("market_prob"))
        .alias("market_prob")
    )
    s = pooled_summary(holed)
    kept = holed.filter(pl.col("market_prob").is_not_null() & pl.col("spread_line").is_not_null())
    assert 0 < s["games"] == kept.height < full["games"]
    assert s["games_dropped"] == full["games"] + full["games_dropped"] - s["games"]
    assert s["brier_model"] == pytest.approx(
        float(((kept["home_win_prob"] - kept["home_win"]) ** 2).mean())
    )
    assert s["brier_gain_vs_market"] == pytest.approx(s["brier_market"] - s["brier_model"])
