"""Training charts on the weekly refits (visibility only): recording the training loss never
changes a booster, only the chosen week's fit records, and the charts log fail-soft."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from test_player_core import _toy_frame

from nflengine.models import player_model as M
from nflengine.models.backtest import AsOf, SampleWeights, walk_forward
from nflengine.models.player_schema import TARGETS
from nflengine.models.training_charts import (
    TrainingEntry,
    coefficient_rows,
    log_coefficients,
    log_training_charts,
    train_curve,
)

FEATS = ["own_l4", "use_snap_l1"]
CFG = M.PlayerModelConfig(n_estimators=25, min_data_in_leaf=20, num_threads=1)


def _target(key: str):
    return next(t for t in TARGETS if t.key == key)


def _train(kind: str) -> pl.DataFrame:
    frame = _toy_frame().filter(pl.col("season") < 2022)
    if kind == "prob":
        frame = frame.with_columns((pl.col("y") >= 6).cast(pl.Float64).alias("y"))
    return frame


@pytest.mark.parametrize("key", ["rec_yds-wrte", "tackles-lbs", "td-rb"])
def test_recording_never_changes_a_booster(key) -> None:
    target = _target(key)
    train = _train(target.kind)
    w = np.ones(train.height)
    plain = M.fit_player_model(train, w, target, FEATS, CFG)
    evals: dict = {}
    recorded = M.fit_player_model(train, w, target, FEATS, CFG, evals=evals)
    assert plain.boosters.keys() == recorded.boosters.keys()
    for name, b in plain.boosters.items():
        assert b.model_to_string() == recorded.boosters[name].model_to_string()
        metric, curve = train_curve(evals, name)
        assert len(curve) == CFG.n_estimators and all(np.isfinite(curve))
    expected = {"amount": {"q10", "q50", "q90"}, "count": {"mean"}, "prob": {"mean"}}
    assert set(evals) == expected[target.kind]


def test_only_the_chosen_weeks_fit_records() -> None:
    target = _target("rec_yds-wrte")
    frame = _toy_frame()
    keys = [AsOf(2022, w) for w in range(1, 9)]

    def run(record_at):
        model = M.PlayerWeekModel(target, FEATS, CFG)
        model.record_at = record_at
        out = walk_forward(
            frame, keys, model, label="y", weights=SampleWeights(), min_train_rows=100
        )
        return model, out

    plain, out_plain = run(None)
    rec, out_rec = run((2022, 8))
    assert plain.last_evals is None and set(rec.last_evals) == {"q10", "q50", "q90"}
    assert out_plain.equals(out_rec)  # the projections are the same with recording on
    for name, b in plain.last.boosters.items():
        assert b.model_to_string() == rec.last.boosters[name].model_to_string()
    early, _ = run((2022, 3))  # recorded at week 3, not overwritten by the later fits
    assert early.last_evals is not None


class FakeRun:
    def __init__(self) -> None:
        self.logged: list[dict] = []
        self.metrics: list[tuple] = []
        self.summary: dict = {}

    def define_metric(self, name, step_metric=None):
        self.metrics.append((name, step_metric))

    def log(self, data):
        self.logged.append(dict(data))


def _entry(key: str) -> TrainingEntry:
    target = _target(key)
    train = _train(target.kind)
    evals: dict = {}
    fit = M.fit_player_model(train, np.ones(train.height), target, FEATS, CFG, evals=evals)
    return TrainingEntry(target, fit, evals, FEATS, CFG)


def test_log_training_charts() -> None:
    run, lines = FakeRun(), []
    n = log_training_charts(run, [_entry("rec_yds-wrte"), _entry("tackles-lbs")], lines.append)
    assert n == 4  # three quantile boosters + one count booster
    assert ("train_loss/*", "lgb/round") in run.metrics
    rounds = [d for d in run.logged if "lgb/round" in d]
    assert len(rounds) == CFG.n_estimators and rounds[0]["lgb/round"] == 1
    assert {"train_loss/rec_yds_wrte/q50", "train_loss/tackles_lbs/mean"} <= set(rounds[-1])
    last = run.logged[-1]
    assert {"feature_importance/rec_yds_wrte", "training_summary", "feature_importance"} <= set(
        last
    )
    summary = last["training_summary"].get_dataframe()
    assert set(summary["booster"]) == {"q10", "q50", "q90", "mean"}
    assert (summary["rounds"] == CFG.n_estimators).all()
    assert summary["loss_final_round"].le(summary["loss_first_round"]).all()  # it learned
    assert run.summary["train/boosters"] == 4 and "4 boosters" in lines[-1]


def test_training_charts_never_fail_a_step() -> None:
    broken = TrainingEntry(_target("rec_yds-wrte"), SimpleNamespace(), None, FEATS, CFG)
    run, lines = FakeRun(), []
    assert log_training_charts(run, [broken], lines.append) == 0
    assert "not logged" in lines[-1]
    assert log_coefficients(run, {"x": object()}, lines.append) == 0  # no linear heads


def _game_fit():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(200, 2)) * [3.0, 50.0]
    y = 2 * x[:, 0] + 0.1 * x[:, 1] + rng.normal(size=200)
    margin = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(x, y)
    total = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(x, y + 40)
    cfg = SimpleNamespace(mfeats=["net_diff", "elo_diff"], tfeats=["off_sum", "def_sum"])
    return SimpleNamespace(margin_model=margin, total_model=total, config=cfg), margin


def test_game_coefficients() -> None:
    fit, margin = _game_fit()
    coef = coefficient_rows({"model_only": fit})
    m = coef.filter(pl.col("head") == "margin")
    assert m["points_per_sd"].to_list() == pytest.approx(list(margin[-1].coef_))
    scale = margin[0].scale_
    assert m["points_per_unit"].to_list() == pytest.approx(list(margin[-1].coef_ / scale))
    run, lines = FakeRun(), []
    assert log_coefficients(run, {"model_only": fit}, lines.append) == 4
    assert {
        "coefficients",
        "coefficients/model_only_margin",
        "coefficients/model_only_total",
    } <= set(run.logged[-1])
