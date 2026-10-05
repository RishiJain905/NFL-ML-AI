"""Player model, P08 additions: the new targets and labels, probability targets (binary model +
calibration layer), event probabilities of counts, the `cvg_*` coverage family for CB/S, and
the guarantee that none of it changes a P06 target's rows, features or projections.

Uses the synthetic league of `test_player_efficiency.make_world` and the augmentation of
`test_player_core`.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import polars as pl
import pytest
from scipy import stats
from test_player_core import _augment, _context
from test_player_efficiency import (
    LATE_FRAMES,
    assert_same,
    changed_columns,
    make_world,
    scrambled,
    tk,
)

from nflengine.features import player as PF
from nflengine.features.player_coverage import CVG_FEATURES, LATE_FEATURES, coverage_features
from nflengine.features.player_data import player_history
from nflengine.models import player_model as M
from nflengine.models import player_runs as R
from nflengine.models.backtest import AsOf, SampleWeights, walk_forward
from nflengine.models.player_schema import (
    P06_KEYS,
    PRED_SCHEMA,
    SCOREBOARD_SCHEMA,
    TARGET_BY_KEY,
    TARGETS,
    conform,
    event_expr,
    event_threshold,
    live_targets,
)

P08_KEYS = [t.key for t in TARGETS if t.phase == "p08"]


@pytest.fixture(scope="module")
def world():
    inp, hist, rows = make_world()
    inp = _augment(inp)
    rows = rows.join(
        inp.games.select("game_id", "kickoff_utc"), on="game_id", how="left"
    ).with_columns(pl.col("pgroup").alias("position"), pl.col("player_id").alias("player"))
    return inp, hist, rows.select(PF.ROW_COLS)


# ---- the contract ----------------------------------------------------------------------------


def test_p08_targets_are_registered_and_p06_stays_the_default() -> None:
    assert len(P06_KEYS) == 11 and all(TARGET_BY_KEY[k].phase == "p06" for k in P06_KEYS)
    assert len(P08_KEYS) == 12
    assert {t.group for t in TARGETS if t.phase == "p08"} >= {"CB/S", "QB", "EDGE/DL"}
    assert [t.key for t in live_targets(P06_KEYS)] == list(P06_KEYS)
    assert [t.key for t in live_targets(["td-rb", "sacks-edge"])] == ["td-rb", "sacks-edge"]
    with pytest.raises(ValueError):
        live_targets(["nope-qb"])
    prob = [t for t in TARGETS if t.kind == "prob"]
    assert {t.key for t in prob} == {"td-rb", "td-wrte", "int-cbs", "pd-cbs"}
    assert all(t.unit == "prob" and not t.main for t in prob)


def test_event_threshold_is_per_target() -> None:
    df = pl.DataFrame(
        {
            "target": ["sacks", "pass_tds", "td", "rec_yds"],
            "group": ["EDGE/DL", "QB", "RB", "WR/TE"],
            "actual": [0.5, 0.0, 1.0, 40.0],
        }
    )
    out = df.with_columns(event_threshold().alias("thr"), event_expr().alias("ev"))
    assert out["thr"].to_list() == [0.5, 1.0, 0.5, 1.0]
    assert out["ev"].to_list() == [1.0, 0.0, 1.0, 1.0]  # a half-sack is a credited sack


# ---- labels in the history table -------------------------------------------------------------


def test_history_has_the_yes_no_labels_and_completions(world) -> None:
    inp, hist, _ = world
    for c in ("any_td", "def_int_any", "pd_any", "pfr_completions_allowed"):
        assert c in hist.columns, c
    skill = hist.filter(pl.col("pgroup").is_in(["RB", "WR", "TE"]))
    want = ((skill["rushing_tds"] + skill["receiving_tds"]) >= 1).cast(pl.Float64)
    assert skill["any_td"].equals(want) and skill["any_td"].n_unique() == 2
    d = hist.filter(pl.col("pgroup").is_in(["S", "DL", "LB"]))
    assert d["def_int_any"].equals((d["def_interceptions"] >= 1).cast(pl.Float64))
    assert d["pd_any"].equals((d["def_pass_defended"] >= 1).cast(pl.Float64))
    # PFR coverage columns of a published team-game are zero-filled, never null
    played = d.filter(pl.col("played_def"))
    assert played["pfr_completions_allowed"].null_count() == 0


def test_history_survives_a_pfr_table_without_completions(world) -> None:
    inp, _, _ = world
    old = replace(inp, pfr_def=inp.pfr_def.drop("def_completions_allowed"))
    h = player_history(old)
    assert h["pfr_completions_allowed"].null_count() == h.height  # null column, no crash
    assert h["pfr_targets_allowed"].null_count() < h.height


# ---- calibration layer -----------------------------------------------------------------------


def _miscalibrated(n: int = 20000, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    p = rng.uniform(0.04, 0.5, n)
    true = 1 / (1 + np.exp(-(1.5 * np.log(p / (1 - p)) - 0.3)))
    return p, (rng.random(n) < true).astype(float)


def test_platt_recovers_a_known_miscalibration() -> None:
    p, y = _miscalibrated()
    cal = M.fit_calibrator(p, y, "platt")
    assert cal.kind == "platt" and cal.slope == pytest.approx(1.5, abs=0.12)
    assert cal.intercept == pytest.approx(-0.3, abs=0.12)
    out = cal(p)
    assert np.mean((out - y) ** 2) < np.mean((p - y) ** 2)
    assert (out > 0).all() and (out < 1).all()


def test_isotonic_is_monotone_and_inside_zero_one() -> None:
    p, y = _miscalibrated()
    cal = M.fit_calibrator(p, y, "isotonic")
    grid = np.linspace(0.0, 1.0, 200)
    out = cal(grid)
    assert cal.kind == "isotonic" and (np.diff(out) >= -1e-12).all()
    assert out.min() > 0 and out.max() < 1
    assert np.mean((cal(p) - y) ** 2) < np.mean((p - y) ** 2)


def test_calibrator_is_the_identity_without_enough_history() -> None:
    p, y = _miscalibrated(400)  # burn-in: too few rows
    assert M.fit_calibrator(p, y, "platt").kind == "none"
    p, y = _miscalibrated(5000)
    assert M.fit_calibrator(p, np.zeros_like(y), "platt").kind == "none"  # no events
    assert M.fit_calibrator(p, y, "none").kind == "none"
    ident = M.Calibrator()
    assert ident(np.array([0.2, 0.7])) == pytest.approx([0.2, 0.7])


def test_unknown_calibration_is_rejected() -> None:
    with pytest.raises(ValueError):
        M.PlayerModelConfig(calibration="magic")


# ---- event probabilities ---------------------------------------------------------------------


def test_event_probs_follow_the_negative_binomial() -> None:
    mu = np.array([0.2, 0.9, 1.6])
    r = 3.0
    ge1, ge2 = M.event_probs(mu, r)
    nb = stats.nbinom(r, r / (r + mu))
    assert ge1 == pytest.approx(1 - nb.cdf(0)) and ge2 == pytest.approx(1 - nb.cdf(1))
    assert (ge2 < ge1).all() and (np.diff(ge1) > 0).all()
    p1, p2 = M.event_probs(mu, M.MAX_DISPERSION)  # Poisson
    assert p1 == pytest.approx(1 - np.exp(-mu)) and (p2 < p1).all()
    assert M.event_probs(np.array([0.0]), 2.0)[0][0] < 1e-3


def test_baseline_p_ge1_by_kind() -> None:
    b = pl.Series("baseline", [0.0, 0.3, None, 1.4])
    prob = TARGET_BY_KEY["td-rb"]
    assert M.baseline_p_ge1(b, prob, 5.0).to_list()[:2] == [0.0, 0.3]
    assert M.baseline_p_ge1(b, prob, 5.0)[3] == 1.0  # a rate is clipped to 1
    cnt = TARGET_BY_KEY["pass_tds-qb"]
    got = M.baseline_p_ge1(b, cnt, 5.0)
    assert got[1] == pytest.approx(M.event_probs(np.array([0.3]), 5.0)[0][0])
    assert got[2] is None
    cal = M.Calibrator("platt", slope=1.0, intercept=1.0)  # a layer shifts the baseline too
    assert M.baseline_p_ge1(b, cnt, 5.0, cal)[1] == pytest.approx(cal(got.to_numpy()[1:2])[0])
    assert M.baseline_p_ge1(b, TARGET_BY_KEY["rush_yds-rb"], 5.0).null_count() == 4
    assert M.baseline_p_ge1(b, TARGET_BY_KEY["carries-rb"], 5.0).null_count() == 4  # no events
    assert M.baseline_p50(b, prob, 5.0).null_count() == 4


# ---- the models in the harness ---------------------------------------------------------------


def _toy(kind: str, n_seasons: int = 3, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    recs = []
    for s in range(2020, 2020 + n_seasons):
        for w in range(1, 9):
            for i in range(120):
                x = rng.normal()
                rate = 1 / (1 + np.exp(-(-1.0 + 0.9 * x)))
                y = float(rng.random() < rate) if kind == "prob" else float(rng.poisson(0.2 + rate))
                recs.append(
                    {
                        "season": s,
                        "week": w,
                        "player_id": f"p{i}",
                        "game_id": f"{s}_{w}_{i}",
                        "own_l4": x,
                        "use_snap_l1": rng.uniform(),
                        "baseline": float(rate if kind == "prob" else 0.2 + rate) * 0.9,
                        "y": y,
                    }
                )
    return pl.DataFrame(recs).with_columns(pl.col("season", "week").cast(pl.Int32))


def _run(target_key: str, monkeypatch, kind: str, cal: str = "platt"):
    monkeypatch.setattr(M, "MIN_CAL_ROWS", 500)
    monkeypatch.setattr(M, "MIN_CAL_EVENTS", 20)
    monkeypatch.setattr(M, "MIN_HISTORY", 300)
    tgt = TARGET_BY_KEY[target_key]
    cfg = M.PlayerModelConfig(
        n_estimators=40, min_data_in_leaf=30, num_threads=1, calibration=cal, calibration_seasons=2
    )
    model = M.PlayerWeekModel(tgt, ["own_l4", "use_snap_l1"], cfg, explain=True)
    keys = [AsOf(2022, w) for w in range(1, 9)]
    out = walk_forward(
        _toy(kind), keys, model, label="y", weights=SampleWeights(), min_train_rows=100
    )
    return model, out


def test_probability_target_in_the_harness(monkeypatch) -> None:
    model, out = _run("td-rb", monkeypatch, "prob")
    assert out.height == 8 * 120
    assert out["p_ge1"].is_between(0, 1).all() and out["mean"].equals(out["p_ge1"])
    assert out["p10"].is_nan().all() and out["p50"].is_nan().all() and out["p90"].is_nan().all()
    assert out["p_ge2"].is_nan().all()
    assert out["baseline_p50"].null_count() == out.height
    assert out["baseline_p_ge1"].to_list() == pytest.approx(out["baseline"].clip(0, 1).to_list())
    assert "_p_raw" in out.columns and out["_p_raw"].is_between(0, 1).all()
    assert len(out["_contrib"][0]) == 2
    # the first weeks have no history to calibrate on (raw = calibrated); later ones do
    first, last = out.filter(pl.col("week") == 1), out.filter(pl.col("week") == 8)
    assert first["p_ge1"].equals(first["_p_raw"])
    assert not last["p_ge1"].equals(last["_p_raw"])
    assert model.last is not None and model.last.calibrator.kind == "platt"
    assert model.last.calibrator.n >= 500


def test_probability_calibration_never_sees_the_key_week(monkeypatch) -> None:
    """The calibrator of week w is fitted on the walk-forward rows before it: changing week w's
    own outcomes can't move week w's probabilities."""
    monkeypatch.setattr(M, "MIN_CAL_ROWS", 500)
    monkeypatch.setattr(M, "MIN_CAL_EVENTS", 20)
    frame = _toy("prob")
    cfg = M.PlayerModelConfig(n_estimators=30, min_data_in_leaf=30, num_threads=1)
    tgt = TARGET_BY_KEY["int-cbs"]
    keys = [AsOf(2022, w) for w in range(1, 7)]

    def go(f: pl.DataFrame) -> pl.DataFrame:
        model = M.PlayerWeekModel(tgt, ["own_l4", "use_snap_l1"], cfg)
        return walk_forward(f, keys, model, label="y", min_train_rows=100)

    base = go(frame)
    flipped = frame.with_columns(
        pl.when((pl.col("season") == 2022) & (pl.col("week") == 6))
        .then(1.0 - pl.col("y"))
        .otherwise(pl.col("y"))
        .alias("y")
    )
    out = go(flipped)
    w6 = pl.col("week") == 6
    assert base.filter(w6)["p_ge1"].to_list() == out.filter(w6)["p_ge1"].to_list()


def test_isotonic_option_runs(monkeypatch) -> None:
    model, out = _run("pd-cbs", monkeypatch, "prob", cal="isotonic")
    assert model.last is not None and model.last.calibrator.kind == "isotonic"
    assert out["p_ge1"].is_between(0, 1).all()


def test_event_count_in_the_harness(monkeypatch) -> None:
    model, out = _run("pass_tds-qb", monkeypatch, "count")
    assert out["p_ge1"].is_between(0, 1).all() and (out["p_ge2"] <= out["p_ge1"]).all()
    fit = model.last
    assert fit is not None
    last = out.filter(pl.col("week") == 8)
    mu = last["mean"].to_numpy()
    want = M.event_probs(mu, fit.dispersion)
    assert last["_p_raw"].to_numpy() == pytest.approx(want[0])  # the NB's own P(>= 1)
    assert fit.calibrator.kind == "platt"  # recalibrated on its earlier walk-forward values
    assert last["p_ge1"].to_numpy() == pytest.approx(fit.calibrator(want[0]))
    assert last["p_ge2"].to_numpy() == pytest.approx(np.minimum(want[1], last["p_ge1"].to_numpy()))
    # the baseline goes through the same function at its own mean
    base = M.event_probs(np.clip(last["baseline"].to_numpy(), 1e-6, None), fit.dispersion)[0]
    assert last["baseline_p_ge1"].to_numpy() == pytest.approx(fit.calibrator(base))
    assert last["baseline_p_ge1"].null_count() == 0
    # P06 counts and amounts carry no event probabilities
    _, plain = _run("carries-rb", monkeypatch, "count")
    assert plain["p_ge1"].is_nan().all() and plain["baseline_p_ge1"].null_count() == plain.height


def test_probability_drivers_are_on_the_probability_scale(monkeypatch) -> None:
    model, out = _run("td-wrte", monkeypatch, "prob")
    c = np.vstack(out["_contrib"].to_list())
    assert np.isfinite(c).all()
    # a first-order conversion: no single driver can move a probability by more than 1
    assert np.abs(c).max() < 1.0
    # own_l4 is the feature the toy outcome depends on: its SHAP value rises with its value
    x = out.join(_toy("prob").select("player_id", "game_id", "own_l4"), on=["player_id", "game_id"])
    assert np.corrcoef(x["own_l4"].to_numpy(), c[:, 0])[0, 1] > 0.5
    assert np.abs(c[:, 0]).mean() > np.abs(c[:, 1]).mean()


def test_feature_columns_by_target(world) -> None:
    frame = pl.DataFrame({c: [1.0] for c in ("own_l4", "cvg_opp_adot_l8", "opp_x", "week")})
    assert "cvg_opp_adot_l8" not in M.feature_columns(frame)
    assert "cvg_opp_adot_l8" not in M.feature_columns(frame, TARGET_BY_KEY["rec_yds-wrte"])
    assert "cvg_opp_adot_l8" in M.feature_columns(frame, TARGET_BY_KEY["cov_tgt-cbs"])
    assert "cvg_opp_adot_l8" in M.feature_columns(frame, TARGET_BY_KEY["int-cbs"])


# ---- run helpers: assemble, scoreboard, summaries -------------------------------------------


def _assembled(target_key: str, monkeypatch, kind: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    _, out = _run(target_key, monkeypatch, kind)
    tgt = TARGET_BY_KEY[target_key]
    frame = out.select("season", "week", "player_id", "game_id").with_columns(
        pl.col("player_id").alias("player"),
        pl.lit("ARI").alias("team"),
        pl.lit("ATL").alias("opponent"),
        pl.lit(True).alias("home"),
        pl.lit("RB").alias("position"),
        pl.lit(tgt.group.split("/")[0]).alias("pgroup"),
        pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("kickoff_utc"),
        pl.lit(10).alias("own_n_career"),
        pl.lit(5).alias("own_n_season"),
        pl.lit(0.8).alias("use_snap_l2"),
        pl.lit("rolling").alias("baseline_source"),
        pl.lit(0.2).alias("baseline_season_mean"),
        pl.lit(0.2).alias("baseline_role"),
        pl.lit(0.0).alias("avail_status"),
        pl.lit(0.0).alias("open_tgt"),
        pl.lit(0.0).alias("open_car"),
    )
    import datetime as dt

    p = R.assemble(
        out.drop("_contrib"),
        frame,
        tgt,
        model_version="t",
        fhash="h",
        trained_through="x",
        created_at=dt.datetime(2022, 9, 1, tzinfo=dt.UTC),
    )
    return p, out


def test_assemble_a_probability_target(monkeypatch) -> None:
    p, _ = _assembled("td-rb", monkeypatch, "prob")
    assert p.columns == list(PRED_SCHEMA)
    assert (p["kind"] == "prob").all() and (p["unit"] == "prob").all()
    assert p["p10"].null_count() == p.height and p["p90"].null_count() == p.height
    assert p["p50"].null_count() == p.height and p["baseline_p50"].null_count() == p.height
    assert p["p_ge2"].null_count() == p.height and p["p_ge1"].null_count() == 0
    assert p["mean"].equals(p["p_ge1"])
    assert p["outperformance"].to_list() == pytest.approx((p["mean"] - p["baseline"]).to_list())
    assert set(p["confidence"].unique()) <= {"low", "medium", "high"}
    assert (p["confidence"] == "high").any()  # no range to call wide: history decides


def test_assemble_keeps_p06_columns_null_for_the_new_ones(monkeypatch) -> None:
    p, _ = _assembled("carries-rb", monkeypatch, "count")
    assert p["p_ge1"].null_count() == p.height and p["baseline_p_ge1"].null_count() == p.height
    assert p["p50"].null_count() == 0 and p["p10"].null_count() == 0


def _scored_mix(monkeypatch) -> pl.DataFrame:
    parts = []
    for key, kind in (("td-rb", "prob"), ("pass_tds-qb", "count"), ("carries-rb", "count")):
        p, _ = _assembled(key, monkeypatch, kind)
        parts.append(p)
    return pl.concat(parts, how="vertical_relaxed")


def test_scoreboard_rows_carry_brier_for_event_targets(monkeypatch) -> None:
    rows = R.scoreboard_rows(_scored_mix(monkeypatch), "backtest")
    assert rows.columns == list(SCOREBOARD_SCHEMA)
    by = {r["target"]: r for r in rows.filter(pl.col("week") == 8).iter_rows(named=True)}
    td, tds, car = by["td"], by["pass_tds"], by["carries"]
    for r in (td, tds):
        assert r["brier_model"] is not None and r["brier_baseline"] is not None
        assert 0 <= r["calibration_ece"] <= 1
    assert td["mae_model"] is None and td["coverage_80"] is None and td["improvement_pct"] is None
    assert tds["mae_model"] is not None  # an event count keeps its MAE
    assert car["brier_model"] is None and car["mae_model"] is not None


def test_summarize_and_ship_rule_for_a_probability_target(monkeypatch) -> None:
    p, _ = _assembled("td-rb", monkeypatch, "prob")
    s = R.summarize(p)
    assert "mae_model" not in s and "coverage_80" not in s
    for k in ("brier_model", "brier_baseline", "ece_model", "event_rate", "brier_climatology"):
        assert k in s
    assert s["event_rate"] == pytest.approx(float(p["actual"].mean()))
    assert s["brier_model"] < s["brier_climatology"] * 1.05
    per = R.by_season(p)
    wins = R.season_wins(TARGET_BY_KEY["td-rb"], per)
    assert wins["seasons_beating_baseline"] == wins["seasons_beating_brier"]


def test_summarize_an_event_count_has_both_families(monkeypatch) -> None:
    p, _ = _assembled("pass_tds-qb", monkeypatch, "count")
    s = R.summarize(p)
    assert "mae_model" in s and "coverage_80" in s and "brier_model" in s
    wins = R.season_wins(TARGET_BY_KEY["pass_tds-qb"], R.by_season(p))
    assert {"seasons_beating_baseline", "seasons_beating_brier"} <= set(wins)
    plain, _ = _assembled("carries-rb", monkeypatch, "count")
    assert "brier_model" not in R.summarize(plain)


def test_ship_rule_cases() -> None:
    prob = TARGET_BY_KEY["td-rb"]
    good = {
        "brier_model": 0.16,
        "brier_baseline": 0.18,
        "seasons_beating_brier": 6.0,
        "ece_model": 0.012,
        "ece_baseline": 0.08,
    }
    assert R.ship_rule(prob, good)["ship_pass"] == 1.0
    assert R.ship_rule(prob, {**good, "brier_model": 0.19})["ship_brier_better"] == 0.0
    assert R.ship_rule(prob, {**good, "seasons_beating_brier": 4.0})["ship_pass"] == 0.0
    # the ECE bar is the baseline's when it is above 0.02, and 0.02 when it is below
    assert R.ship_rule(prob, {**good, "ece_model": 0.05})["ship_ece"] == 1.0
    assert R.ship_rule(prob, {**good, "ece_model": 0.03, "ece_baseline": 0.01})["ship_ece"] == 0.0

    amount = TARGET_BY_KEY["rush_yds-qb"]
    a = {
        "mae_model": 9.0,
        "mae_baseline": 10.0,
        "seasons_beating_baseline": 6.0,
        "coverage_80": 0.81,
    }
    assert R.ship_rule(amount, a)["ship_pass"] == 1.0
    assert R.ship_rule(amount, {**a, "coverage_80": 0.9})["ship_coverage"] == 0.0
    assert R.ship_rule(amount, {**a, "coverage_80": 0.74})["ship_pass"] == 0.0
    assert R.ship_rule(amount, {**a, "mae_model": 11.0})["ship_mae_better"] == 0.0

    event = TARGET_BY_KEY["ints-qb"]
    e = {**good, "improvement_pct": -0.3}
    r = R.ship_rule(event, e)
    assert r["ship_pass"] == 1.0 and "ship_coverage" not in r  # MAE only has to be no worse
    assert R.ship_rule(event, {**e, "improvement_pct": -0.8})["ship_mae_not_worse"] == 0.0


def test_live_logger_logs_brier_for_a_probability_target(monkeypatch) -> None:
    class FakeRun:
        def __init__(self) -> None:
            self.logged: list[dict] = []

        def define_metric(self, *a, **k) -> None:
            pass

        def log(self, d: dict) -> None:
            self.logged.append(d)

    _, out = _run("td-rb", monkeypatch, "prob")
    run = FakeRun()
    lg = R.LiveLogger(run, [2022], lambda d: d, target=TARGET_BY_KEY["td-rb"])
    for w in (1, 2, 3):
        lg(AsOf(2022, w), out.filter(pl.col("week") == w))
    last = run.logged[-1]
    assert last["bt/step"] == 3
    for k in ("bt/brier_model", "bt/brier_baseline", "bt/cum_brier_model", "bt/cum_brier_baseline"):
        assert k in last, k
    assert "bt/mae_model" not in last
    # an event count logs both families; a plain count only MAE
    _, ev = _run("pass_tds-qb", monkeypatch, "count")
    r2 = FakeRun()
    lg2 = R.LiveLogger(r2, [2022], lambda d: d, target=TARGET_BY_KEY["pass_tds-qb"])
    lg2(AsOf(2022, 5), ev.filter(pl.col("week") == 5))
    assert "bt/mae_model" in r2.logged[-1] and "bt/cum_brier_model" in r2.logged[-1]
    _, pl_ = _run("carries-rb", monkeypatch, "count")
    r3 = FakeRun()
    lg3 = R.LiveLogger(r3, [2022], lambda d: d, target=TARGET_BY_KEY["carries-rb"])
    lg3(AsOf(2022, 5), pl_.filter(pl.col("week") == 5))
    assert "bt/mae_model" in r3.logged[-1] and "bt/cum_brier_model" not in r3.logged[-1]


def test_reliability_tables_use_deciles(monkeypatch) -> None:
    p, _ = _assembled("td-rb", monkeypatch, "prob")
    rel = R.reliability_tables(p)
    assert set(rel["predictor"].unique()) == {"model", "rolling baseline"}
    m = rel.filter(pl.col("predictor") == "model")
    assert m.height == 10 and (np.diff(m["mean_prob"].to_numpy()) >= 0).all()
    assert abs(m["n"].max() - m["n"].min()) <= 1  # equal-count bins
    assert R.reliability_tables(_assembled("carries-rb", monkeypatch, "count")[0]).is_empty()


def test_tune_objective_scores_a_probability_target_on_brier(monkeypatch) -> None:
    monkeypatch.setattr(R, "TUNE_SEASONS", (2021, 2022))
    monkeypatch.setattr(R, "MIN_TRAIN_ROWS", 100)
    frame = _toy("prob")
    cfg = M.PlayerModelConfig(n_estimators=20, min_data_in_leaf=30, num_threads=1)
    res = R.tune_objective(frame, TARGET_BY_KEY["td-rb"], ["own_l4", "use_snap_l1"], cfg)
    assert {"tune/brier_model", "tune/brier_baseline", "tune/improvement_pct"} <= set(res)
    assert 0 < res["tune/brier_model"] < 0.3


def test_model_config_ignores_live_targets_and_applies_code_defaults(monkeypatch) -> None:
    from nflengine.settings import get_config

    cfg = get_config()
    pm = dict(cfg.player_model or {})
    pm["live_targets"] = ["td-rb"]
    # settings.yaml's per_target entries win over the code defaults: drop the two under test
    per = {k: v for k, v in (pm.get("per_target") or {}).items() if k not in ("td-rb", "td-wrte")}
    pm["per_target"] = per
    monkeypatch.setattr(cfg, "player_model", pm, raising=False)
    monkeypatch.setitem(R.TARGET_DEFAULTS, "td-rb", {"num_leaves": 5})
    assert R.model_config(TARGET_BY_KEY["td-rb"]).num_leaves == 5
    assert R.model_config(TARGET_BY_KEY["td-wrte"]).num_leaves != 5


# ---- the coverage family ---------------------------------------------------------------------


def test_coverage_columns_dtypes_and_order(world) -> None:
    inp, hist, rows = world
    out = coverage_features(inp, hist, rows)
    assert out.columns == ["player_id", "game_id", *CVG_FEATURES]
    assert out.height == rows.height
    assert out["player_id"].equals(rows["player_id"]) and out["game_id"].equals(rows["game_id"])
    assert all(out.schema[c] == pl.Float64 for c in CVG_FEATURES)
    assert all(f.startswith("cvg_") for f in CVG_FEATURES) and set(LATE_FEATURES) <= set(
        CVG_FEATURES
    )
    shuffled = rows.sample(fraction=1.0, shuffle=True, seed=3)
    again = coverage_features(inp, hist, shuffled)
    assert again["player_id"].equals(shuffled["player_id"])
    merged = again.join(out, on=["player_id", "game_id"], suffix="_b")
    for c in CVG_FEATURES:
        assert merged[c].equals(merged[f"{c}_b"]), c


def test_coverage_matches_a_naive_implementation(world) -> None:
    """Targets, depth, completion rate and yards per target of the opposing offense, and the
    same for the passes against the row's own defense, over the 8 games before the row."""
    inp, hist, rows = world
    out = coverage_features(inp, hist, rows).join(
        rows.select("player_id", "game_id", "team", "opponent", "t"), on=["player_id", "game_id"]
    )
    tg = hist.group_by("game_id", "team").agg(
        pl.col("opponent").first(),
        pl.col("t").first(),
        pl.col("targets").sum().alias("tgt"),
        pl.col("receptions").sum().alias("rec"),
        pl.col("receiving_yards").sum().alias("yds"),
        pl.col("receiving_air_yards").sum().alias("air"),
    )

    def naive(by: str, key: str, t: int) -> dict[str, float] | None:
        g = tg.filter((pl.col(by) == key) & (pl.col("t") < t)).sort("t").tail(8)
        if g.height < 3:
            return None
        tgt = g["tgt"].sum()
        return {
            "targets": tgt / g.height,
            "adot": g["air"].sum() / tgt,
            "cmp": g["rec"].sum() / tgt,
            "ypt": g["yds"].sum() / tgt,
        }

    checked = 0
    for r in out.filter(pl.col("t") >= tk(2022, 5)).head(60).iter_rows(named=True):
        for by, key, pre in (
            ("team", r["opponent"], "cvg_opp"),
            ("opponent", r["team"], "cvg_own"),
        ):
            want = naive(by, key, r["t"])
            assert want is not None
            for name, col in (
                ("targets", "targets_l8"),
                ("adot", "adot_l8"),
                ("cmp", "cmp_pct_l8"),
                ("ypt", "ypt_l8"),
            ):
                assert r[f"{pre}_{col}"] == pytest.approx(want[name]), (pre, col)
            checked += 1
    assert checked > 50


def test_coverage_opponent_and_own_sides_differ(world) -> None:
    """A team's passing offense and the passes against its defense are different series: the
    synthetic league gives every team different totals, so the two columns must disagree."""
    inp, hist, rows = world
    out = coverage_features(inp, hist, rows).join(
        rows.select("player_id", "game_id", "t"), on=["player_id", "game_id"]
    )
    late = out.filter(pl.col("t") >= tk(2022, 5)).drop_nulls(
        ["cvg_opp_targets_l8", "cvg_own_targets_l8"]
    )
    assert late.height > 20
    assert not late["cvg_opp_targets_l8"].equals(late["cvg_own_targets_l8"])


def test_coverage_future_invariance(world) -> None:
    inp, hist, rows = world
    base = coverage_features(inp, hist, rows)
    for cut in (tk(2021, 6), tk(2022, 3), tk(2022, 5)):
        inp2 = scrambled(inp, cut, seed=cut)
        out = coverage_features(inp2, player_history(inp2), rows)
        before = rows["t"] <= cut
        assert before.sum() > 50
        assert_same(base.filter(before), out.filter(before), CVG_FEATURES)
        after = (rows["t"] > cut) & (rows["t"] <= tk(2022, 6))
        assert changed_columns(base.filter(after), out.filter(after), CVG_FEATURES), "no teeth"


def test_coverage_pfr_pressure_arrives_a_week_late(world) -> None:
    inp, hist, rows = world
    cut = tk(2022, 4)
    t = rows["t"]
    base = coverage_features(inp, hist, rows)
    late_in = scrambled(inp, cut - 1, cut - 1, frames=LATE_FRAMES)
    late = coverage_features(late_in, player_history(late_in), rows)
    assert_same(base.filter(t <= cut), late.filter(t <= cut), CVG_FEATURES)
    changed = changed_columns(base.filter(t == cut + 1), late.filter(t == cut + 1), CVG_FEATURES)
    assert set(changed) == set(LATE_FEATURES)


def test_coverage_rates_need_enough_targets(world) -> None:
    inp, hist, rows = world
    out = coverage_features(inp, hist, rows).join(
        rows.select("player_id", "game_id", "t"), on=["player_id", "game_id"]
    )
    first = out.filter(pl.col("t") <= tk(2021, 2))  # fewer than 3 earlier games
    assert first["cvg_opp_targets_l8"].null_count() == first.height
    assert first["cvg_own_ypt_l8"].null_count() == first.height


def test_every_coverage_feature_has_a_readable_phrase() -> None:
    from nflengine.models.player_runs import load_phrases

    phrases = load_phrases()
    assert not [c for c in CVG_FEATURES if c not in phrases]
    assert not any(ch.isdigit() for c in CVG_FEATURES for ch in phrases[c])


# ---- P06 rows, features and projections don't move -----------------------------------------


def _with_cb(world):
    """The synthetic league with the safeties relabelled cornerbacks: CB rows exist, and they
    are the only difference between the two row sets."""
    inp, hist, rows = world
    is_s = pl.col("player_id").str.ends_with("_S")
    relabel = pl.when(is_s).then(pl.lit("CB")).otherwise(pl.col("pgroup")).alias("pgroup")
    return inp, hist.with_columns(relabel), rows.with_columns(relabel)


def test_cb_rows_do_not_change_p06_features(world) -> None:
    inp, hist, rows = _with_cb(world)
    eq = PF.expected_qbs_from_games(inp.game_features)
    ctx = _context(inp)
    p06 = PF.target_pgroups([TARGET_BY_KEY[k] for k in P06_KEYS])
    assert "CB" not in p06 and "CB" in PF.target_pgroups()
    rows_p06 = rows.filter(pl.col("pgroup").is_in(p06))
    rows_all = rows.filter(pl.col("pgroup").is_in(PF.target_pgroups()))
    assert rows_all.height > rows_p06.height  # CB rows exist
    kw = {"context": ctx, "expected_qbs": eq, "log": lambda *_: None}
    f06 = PF.build_features(inp, hist, rows_p06, coverage=False, **kw)
    fall = PF.build_features(inp, hist, rows_all, coverage=True, **kw)
    assert not [c for c in f06.columns if c.startswith("cvg_")]
    assert [c for c in fall.columns if c.startswith("cvg_")] == list(CVG_FEATURES)
    keys = ["player_id", "game_id"]
    shared = f06.columns
    a = f06.sort(keys)
    b = fall.join(rows_p06.select(keys), on=keys, how="semi").sort(keys).select(shared)
    assert a.height == b.height > 100
    assert a.equals(b)  # bit-identical, not just close
    # and a P06 target's frame (rows, label, own_*, baselines) is identical too
    for key in ("rec_yds-wrte", "tackles-lbs", "pressures-edge"):
        tgt = replace(TARGET_BY_KEY[key], start_season=2021)
        ta = PF.target_frame(f06, hist, tgt).sort(keys)
        tb = PF.target_frame(fall, hist, tgt).sort(keys).select(ta.columns)
        assert ta.equals(tb), key
        assert M.feature_columns(ta, TARGET_BY_KEY[key]) == M.feature_columns(
            tb, TARGET_BY_KEY[key]
        )


def test_history_and_upcoming_rows_by_group(world) -> None:
    inp, hist, _ = _with_cb(world)
    p06 = PF.target_pgroups([TARGET_BY_KEY[k] for k in P06_KEYS])
    allr, only = PF.history_rows(hist), PF.history_rows(hist, p06)
    assert set(allr["pgroup"]) - set(only["pgroup"]) == {"CB"}
    assert allr.filter(pl.col("pgroup") != "CB").sort("t", "player_id").equals(only)
    qbs = pl.DataFrame({"team": ["ARI"], "qb_id": ["ARI_QB"]})
    up_all = PF.upcoming_rows(inp, hist, 2022, 7, qbs)
    up_p06 = PF.upcoming_rows(inp, hist, 2022, 7, qbs, pgroups=p06)
    assert set(up_all["pgroup"]) - set(up_p06["pgroup"]) == {"CB"}
    keys = ["player_id", "game_id"]
    assert up_all.filter(pl.col("pgroup") != "CB").sort(keys).equals(up_p06.sort(keys))
    no_qb = PF.upcoming_rows(inp, hist, 2022, 7, qbs, pgroups=["WR", "TE"])
    assert set(no_qb["pgroup"]) <= {"WR", "TE"}


def test_p06_week_model_output_is_unchanged_by_the_p08_code(monkeypatch) -> None:
    """A P06 target through the harness: same columns it always had plus the null event ones;
    the projection columns don't depend on the calibration settings."""
    monkeypatch.setattr(M, "MIN_HISTORY", 300)
    frame = _toy("count")
    tgt = TARGET_BY_KEY["carries-rb"]
    keys = [AsOf(2022, w) for w in range(1, 6)]

    def go(cal: str) -> pl.DataFrame:
        cfg = M.PlayerModelConfig(
            n_estimators=30, min_data_in_leaf=30, num_threads=1, calibration=cal
        )
        model = M.PlayerWeekModel(tgt, ["own_l4", "use_snap_l1"], cfg)
        return walk_forward(frame, keys, model, label="y", min_train_rows=100)

    a, b = go("platt"), go("none")
    for c in ("p10", "p50", "p90", "mean", "baseline_p50"):
        assert a[c].equals(b[c]), c
    assert a["p_ge1"].is_nan().all() and a["p_ge2"].is_nan().all()


def test_conform_keeps_the_new_columns() -> None:
    assert {"p_ge1", "p_ge2", "baseline_p_ge1"} <= set(PRED_SCHEMA)
    df = conform(pl.DataFrame({"season": [2025], "p_ge1": [0.3]}))
    assert df["p_ge1"].to_list() == [0.3] and df["baseline_p_ge1"].null_count() == 1


# ---- the backtest scoreboard under parallel chains -------------------------------------------


def _board_rows(target: str, n: int) -> pl.DataFrame:
    rows = pl.DataFrame(
        {
            "season": [2025] * n,
            "week": list(range(1, n + 1)),
            "target": [target] * n,
            "position_group": ["RB"] * n,
            "target_label": [target] * n,
            "n_scored": [30] * n,
            "n_not_played": [0] * n,
            "mode": ["backtest"] * n,
        }
    )
    return rows.select(
        [
            pl.col(c).cast(t) if c in rows.columns else pl.lit(None, dtype=t).alias(c)
            for c, t in SCOREBOARD_SCHEMA.items()
        ]
    )


def test_parallel_upserts_keep_every_chains_rows(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "scoreboard.parquet"
    targets = [f"t{i}" for i in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda t: R.upsert_scoreboard(path, _board_rows(t, 8)), targets))
    out = pl.read_parquet(path)
    assert out.height == 6 * 8 and set(out["target"]) == set(targets)
    from nflengine.ops.lock import file_lock

    with file_lock(path, timeout=0.5):  # the OS lock is free again (its file stays, by design)
        pass


def test_a_dead_holders_scoreboard_lock_is_released_never_stolen(tmp_path) -> None:
    """Sol review (P08): the old lock was broken after 120 s, so a waiter could take it from
    a live writer. The OS lock is released only when its holder ends."""
    import subprocess
    import sys
    import time

    from nflengine.ops.lock import file_lock

    path = tmp_path / "scoreboard.parquet"
    flag = tmp_path / "held"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time, pathlib\n"
            "from nflengine.ops.lock import file_lock\n"
            "with file_lock(pathlib.Path(sys.argv[1])):\n"
            "    pathlib.Path(sys.argv[2]).write_text('1')\n"
            "    time.sleep(60)\n",
            str(path),
            str(flag),
        ]
    )
    try:
        t0 = time.monotonic()
        while not flag.exists():
            assert time.monotonic() - t0 < 60, "the child never took the lock"
            time.sleep(0.05)
        with pytest.raises(TimeoutError), file_lock(path, timeout=0.3):
            pass  # a live holder keeps its lock
    finally:
        child.kill()  # dies holding it, like a crashed chain
        child.wait()
    with file_lock(path, timeout=10):  # the OS released it with the process
        pass


def test_backtest_table_row_by_kind() -> None:
    prob = R.backtest_table_row(
        TARGET_BY_KEY["td-rb"],
        {
            "n_scored": 10382.0,
            "brier_model": 0.1638,
            "brier_baseline": 0.1759,
            "brier_improvement_pct": 6.88,
            "ece_model": 0.0106,
        },
    )
    assert prob == ["td-rb", "10,382", "Brier 0.1638", "Brier 0.1759", "+6.9%", "ECE 0.011"]
    amount = R.backtest_table_row(
        TARGET_BY_KEY["rush_yds-qb"],
        {
            "n_scored": 3733.0,
            "mae_model": 11.64,
            "mae_baseline": 11.87,
            "improvement_pct": 1.9,
            "coverage_80": 0.8,
        },
    )
    assert amount == ["rush_yds-qb", "3,733", "11.640", "11.870", "+1.9%", "80%"]


def test_score_predictions_fills_p08_labels(world) -> None:
    from nflengine.models.player_schema import score_predictions

    _, hist, _ = world
    rb = hist.filter((pl.col("pgroup") == "RB") & pl.col("played_off")).head(3)
    s = hist.filter((pl.col("pgroup") == "S") & pl.col("played_def")).head(3)
    qb = hist.filter(pl.col("main_qb")).head(2)
    preds = conform(
        pl.concat(
            [
                rb.select("player_id", "game_id").with_columns(
                    pl.lit("td").alias("target"), pl.lit("RB").alias("group")
                ),
                s.select("player_id", "game_id").with_columns(
                    pl.lit("int").alias("target"), pl.lit("CB/S").alias("group")
                ),
                s.select("player_id", "game_id").with_columns(
                    pl.lit("cov_cmp").alias("target"), pl.lit("CB/S").alias("group")
                ),
                qb.select("player_id", "game_id").with_columns(
                    pl.lit("rush_yds").alias("target"), pl.lit("QB").alias("group")
                ),
            ]
        )
    )
    out = score_predictions(preds, hist)
    assert out["played"].all()
    by = {t: out.filter(pl.col("target") == t) for t in ("td", "int", "cov_cmp", "rush_yds")}
    assert by["td"]["actual"].to_list() == rb["any_td"].to_list()
    assert by["int"]["actual"].to_list() == s["def_int_any"].to_list()
    assert by["cov_cmp"]["actual"].to_list() == s["pfr_completions_allowed"].to_list()
    assert by["rush_yds"]["actual"].to_list() == qb["rushing_yards"].to_list()  # QB, not RB


def test_target_features_leave_p06_calls_on_the_one_argument_form(monkeypatch) -> None:
    """The P06 targets still go through `feature_columns(frame)`: other modules' tests patch it
    with a one-argument function, and nothing about those models may depend on the target."""
    frame = pl.DataFrame({c: [1.0] for c in ("own_l4", "cvg_opp_adot_l8", "week")})
    seen: list[tuple] = []
    monkeypatch.setattr(R, "feature_columns", lambda *a: seen.append(a) or ["own_l4"])
    assert R.target_features(frame, TARGET_BY_KEY["tackles-lbs"]) == ["own_l4"]
    assert len(seen[-1]) == 1  # (frame,) only
    assert R.target_features(frame, TARGET_BY_KEY["int-cbs"]) == ["own_l4"]
    assert len(seen[-1]) == 2  # the CB/S group passes its target for the cvg_ family


def test_live_scoreboard_curves_include_brier_skill(monkeypatch) -> None:
    rows = _board_rows("td", 1).with_columns(
        pl.lit(0.16).alias("brier_model"),
        pl.lit(0.18).alias("brier_baseline"),
        pl.lit(0.01).alias("calibration_ece"),
        pl.lit("live").alias("mode"),
        pl.lit("RB").alias("position_group"),
        pl.lit(4).cast(pl.Int32).alias("week"),
    )
    logged: list[dict] = []

    class FakeRun:
        url = "https://wandb.example/run"

        def define_metric(self, *a, **k) -> None:
            pass

        def log(self, d: dict) -> None:
            logged.append(d)

        def finish(self) -> None:
            pass

    monkeypatch.setattr(R, "init_run", lambda *a, **k: FakeRun())
    R.log_scoreboard(2025, 4, rows, "agent")
    point = logged[0]
    assert point["scoreboard/brier_skill_td_rb"] == pytest.approx(100 * 0.02 / 0.18)
    assert not any(k.startswith("scoreboard/improvement") for k in point)  # no MAE for a TD chance
