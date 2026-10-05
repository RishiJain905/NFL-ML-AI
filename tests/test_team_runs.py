"""Team stat model and runs (P08): the walk-forward model, the metrics and ship rule, the
backtest outputs, the weekly fit and the live scoreboard, with W&B and the data drive mocked.

The data is the synthetic league of `test_team_stats` (no signal in the numbers, so nothing is
expected to beat its baseline: the tests check contracts, not accuracy).
"""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import polars as pl
import pytest
from test_team_stats import PLAYED_THROUGH, make_world

from nflengine.features import team_stats as TS
from nflengine.features.asof import AsOf
from nflengine.models import team_model as TM
from nflengine.models import team_runs as R
from nflengine.models.backtest import SampleWeights, walk_forward
from nflengine.models.player_model import PlayerModelConfig, event_probs
from nflengine.models.player_schema import SCOREBOARD_SCHEMA
from nflengine.paths import DataPaths

SEASONS = tuple(range(2086, 2094))  # far future: every kickoff is after the real "now"
SEASON = SEASONS[-1]
REPORT = SEASONS[-2:]
BY_NAME = {t.name: t for t in TM.TEAM_TARGETS}


class FakeRun:
    def __init__(self):
        self.summary = FakeSummary()
        self.logged: list = []
        self.artifacts: list = []
        self.finished = False
        self.url = "https://example.invalid/run"

    def log(self, data):
        self.logged.append(data)

    def define_metric(self, *args, **kwargs):
        pass

    def log_artifact(self, art, aliases):
        self.artifacts.append((art.name, list(aliases)))

    def finish(self):
        self.finished = True


class FakeSummary(dict):
    def update(self, other=None, **kw):  # noqa: D102
        super().update(other or {}, **kw)


def small_config(target, **overrides):
    values = {
        "num_leaves": 3,
        "min_data_in_leaf": 3,
        "n_estimators": 8,
        "num_threads": 1,
        "weights": SampleWeights(3.0, 1.5, 1.0),
        **{k: v for k, v in overrides.items() if k != "num_threads" and v is not None},
    }
    return PlayerModelConfig(**values)


@pytest.fixture(scope="module")
def world():
    return make_world(seed=4, seasons=SEASONS)


@pytest.fixture(scope="module")
def data(world) -> R.TeamData:
    feats = TS.build_team_features(world, first_season=SEASONS[0] + 1)
    return R.TeamData(feats, world)


@pytest.fixture
def mocked(tmp_path, monkeypatch, data, world):
    paths = DataPaths(tmp_path)
    for d in paths.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    runs: list[FakeRun] = []

    def fake_init_run(*args, **kwargs):
        runs.append(FakeRun())
        return runs[-1]

    monkeypatch.setattr(R, "ensure_data_root", lambda: paths)
    monkeypatch.setattr(R, "init_run", fake_init_run)
    monkeypatch.setattr(R, "dataset_version", lambda *a: {"pbp_snapshot": None})
    monkeypatch.setattr(R, "git_commit", lambda: "test")
    monkeypatch.setattr(R, "model_config", small_config)
    monkeypatch.setattr(R, "MIN_TRAIN_ROWS", 20)
    return paths, runs


# ---- the model: distributions, config, harness contract ---------------------------------------


def test_poisson_deviance_values() -> None:
    assert TM.poisson_deviance(np.array([2.0]), np.array([2.0])) == pytest.approx(0.0)
    assert TM.poisson_deviance(np.array([0.0]), np.array([1.5])) == pytest.approx(3.0)
    d = TM.poisson_deviance(np.array([3.0]), np.array([1.0]))
    assert d == pytest.approx(2 * (3 * np.log(3) - 2))
    assert np.isnan(TM.poisson_deviance(np.array([]), np.array([])))
    assert TM.poisson_deviance(np.array([1.0, np.nan]), np.array([1.0, 1.0])) == pytest.approx(0.0)


def test_count_event_probability_is_the_player_models(data) -> None:
    """Without walk-forward history the calibrator is the identity: `p_ge1` is the negative
    binomial's P(>= 1) at the model's mean, and the baseline's goes through the same
    dispersion."""
    t = BY_NAME["sacks_made"]
    frame, feats = R.target_frame(data, t)
    train = frame.filter(pl.col("y").is_not_null() & (pl.col("season") < SEASON))
    test = frame.filter((pl.col("season") == SEASON) & (pl.col("week") == 3))
    out = TM.TeamWeekModel(t, feats, small_config(t))(
        train, np.ones(train.height), test, pl.DataFrame()
    )
    r = float(out["range_param"][0])
    assert out["p_ge1"].to_numpy() == pytest.approx(event_probs(out["mean"].to_numpy(), r)[0])
    assert out["baseline_p_ge1"].to_numpy() == pytest.approx(
        event_probs(out["baseline"].to_numpy(), r)[0]
    )
    assert "p_ge2" not in out.columns and "_p_raw" in out.columns


def test_live_team_targets_come_from_the_settings(monkeypatch) -> None:
    import types

    from nflengine import settings

    cfg = types.SimpleNamespace(team_model={})
    monkeypatch.setattr(settings, "get_config", lambda: cfg)
    assert TM.live_team_targets(()) == ()
    got = TM.live_team_targets(["takeaways-team", "pass_yds-team"])
    assert [t.key for t in got] == ["pass_yds-team", "takeaways-team"]  # in TEAM_TARGETS order
    with pytest.raises(ValueError, match="unknown live team target keys"):
        TM.live_team_targets(["rec_yds-wrte"])
    assert TM.live_team_targets() == ()  # nothing is live until settings team_model.live_targets
    cfg.team_model = {"live_targets": ["rush_yds-team"]}
    assert [t.key for t in TM.live_team_targets()] == ["rush_yds-team"]
    # the settings block may carry `live_targets` next to the LightGBM settings
    cfg = TM.team_config(BY_NAME["rush_yds"], {"live_targets": ["rush_yds-team"], "num_leaves": 6})
    assert cfg.num_leaves == 6


def test_count_prob_table_bins() -> None:
    t = TM.count_prob_table(np.array([0.05, 0.07, 0.95]), np.array([0.0, 1.0, 1.0]))
    assert t["n"].to_list() == [2, 1] and t["observed"].to_list() == [0.5, 1.0]


def test_team_targets_and_config_layers() -> None:
    assert [t.key for t in TM.TEAM_TARGETS] == [
        "pass_yds-team",
        "rush_yds-team",
        "sacks_made-team",
        "sacks_taken-team",
        "takeaways-team",
    ]
    assert TM.get_team_target("takeaways")[0].kind == "count"
    assert len(TM.get_team_target("all")) == 5
    with pytest.raises(ValueError):
        TM.get_team_target("rec_yds")
    t = BY_NAME["pass_yds"]
    base = TM.team_config(t)
    assert (base.num_leaves, base.n_estimators) == (4, 200)
    over = TM.team_config(
        t,
        {"num_leaves": 5, "per_target": {"pass_yds-team": {"n_estimators": 77}}},
        SampleWeights(2.0, 1.5, 1.0),
        seed=9,
    )
    assert (over.num_leaves, over.n_estimators, over.seed) == (5, 77, 9)
    assert over.weights.current_season == 2.0
    with pytest.raises(ValueError, match="unknown player_model settings"):
        TM.team_config(t, {"leaves": 3})


@pytest.mark.parametrize("name", ["pass_yds", "sacks_made"])
def test_week_model_output_contract(data, name) -> None:
    t = BY_NAME[name]
    frame, feats = R.target_frame(data, t)
    key = AsOf(SEASON, 3)
    train = frame.filter(pl.col("y").is_not_null() & (pl.col("season") < SEASON))
    test = frame.filter((pl.col("season") == SEASON) & (pl.col("week") == 3))
    out = TM.TeamWeekModel(t, feats, small_config(t))(
        train, np.ones(train.height), test, pl.DataFrame()
    )
    assert out.height == test.height
    assert (out["game_id"] == test["game_id"]).all() and (out["team"] == test["team"]).all()
    assert (out["p10"] <= out["p50"]).all() and (out["p50"] <= out["p90"]).all()
    assert out["baseline_p50"].null_count() == 0
    if t.kind == "count":
        assert out["p_ge1"].is_between(0, 1).all() and out["baseline_p_ge1"].is_between(0, 1).all()
        assert (out["p50"] == out["p50"].round(0)).all()  # whole numbers
        assert out["range_tail"].null_count() == 0
    else:
        assert "p_ge1" not in out.columns and {"_p10_raw", "_p90_raw"} <= set(out.columns)
    assert key.season == SEASON


def test_features_for_the_model_leave_out_other_targets(data) -> None:
    frame, feats = R.target_frame(data, BY_NAME["takeaways"])
    assert "base_takeaways" in feats and not any("pass_yds" in c for c in feats)
    _, nm = R.target_frame(data, BY_NAME["takeaways"], no_market=True)
    assert not any(c.startswith("mkt_") for c in nm) and len(nm) == len(feats) - 4
    assert frame["season"].min() >= TS.TRAIN_START


# ---- metrics, ship rule, scoreboard rows -------------------------------------------------------


def scored_frame(kind: str = "count", n: int = 80, better: bool = True) -> pl.DataFrame:
    rng = np.random.default_rng(1)
    y = rng.poisson(2.0, n).astype(float)
    base = np.full(n, 2.0)
    mean = y * 0.5 + 1.0 if better else base
    p50 = np.floor(mean + 0.5) if kind == "count" else mean
    return pl.DataFrame(
        {
            "season": [2024] * (n // 2) + [2025] * (n - n // 2),
            "week": [1 + i % 4 for i in range(n)],
            "target": ["sacks_made" if kind == "count" else "pass_yds"] * n,
            "target_label": ["x"] * n,
            "kind": [kind] * n,
            "actual": y,
            "played": [True] * n,
            "baseline": base,
            "baseline_p50": np.full(n, 2.0),
            "p10": p50 - 2,
            "p50": p50,
            "p90": p50 + 2,
            "mean": mean,
            "p_ge1": np.clip(mean / 4, 0.05, 0.95) if kind == "count" else [None] * n,
            "baseline_p_ge1": np.full(n, 0.8) if kind == "count" else [None] * n,
            "outperformance": mean - base,
        },
        schema_overrides={"p_ge1": pl.Float64, "baseline_p_ge1": pl.Float64},
    )


def test_summarize_pools_model_and_baseline_on_the_same_rows() -> None:
    f = scored_frame()
    s = R.summarize(f)
    y = f["actual"].to_numpy()
    assert s["n_scored"] == 80
    assert s["mae_model"] == pytest.approx(np.abs(y - f["p50"].to_numpy()).mean())
    assert s["mae_baseline"] == pytest.approx(np.abs(y - 2.0).mean())
    assert s["improvement_pct"] == pytest.approx(
        100 * (s["mae_baseline"] - s["mae_model"]) / s["mae_baseline"]
    )
    assert s["deviance_model"] == pytest.approx(TM.poisson_deviance(y, f["mean"].to_numpy()))
    assert {"brier_p_ge1", "ece_p_ge1", "share_ge1", "spearman_outperformance"} <= set(s)
    assert 0 <= s["coverage_80"] <= 1
    unplayed = f.with_columns(pl.lit(None, dtype=pl.Boolean).alias("played"))
    assert R.summarize(unplayed) == {"n_scored": 0.0}
    amount = R.summarize(scored_frame("amount"))
    assert "deviance_model" not in amount and "brier_p_ge1" not in amount


def test_by_season_and_ship_rule() -> None:
    good = scored_frame(better=True)
    per = R.by_season(good)
    assert per["season"].to_list() == [2024, 2025]
    pooled = R.summarize(good)
    verdict = R.ship_rule(BY_NAME["sacks_made"], pooled, per)
    assert verdict["seasons"] == 2.0
    # two seasons can never reach the 5 that the rule asks for
    assert verdict["ship/seasons_ok"] == 0.0 and verdict["ship/pass"] == 0.0

    def fake(pooled_over, seasons_better):
        per = pl.DataFrame(
            {
                "mae_model": [1.0 if i < seasons_better else 2.0 for i in range(7)],
                "mae_baseline": [1.5] * 7,
            }
        )
        p = {
            "mae_model": 1.0,
            "mae_baseline": 1.5,
            "coverage_80": 0.8,
            "deviance_model": 1.0,
            "deviance_baseline": 1.1,
            **pooled_over,
        }
        return R.ship_rule(BY_NAME["takeaways"], p, per)

    assert fake({}, 5)["ship/pass"] == 1.0
    assert fake({}, 4)["ship/pass"] == 0.0  # only 4 of 7 seasons
    assert fake({"coverage_80": 0.70}, 7)["ship/coverage_ok"] == 0.0
    assert fake({"coverage_80": 0.90}, 7)["ship/coverage_ok"] == 0.0
    assert fake({"mae_model": 1.6}, 7)["ship/beats_baseline"] == 0.0
    # takeaways also need a lower Poisson deviance than the league average
    assert fake({"deviance_model": 1.2}, 7)["ship/deviance_ok"] == 0.0
    assert fake({"deviance_model": 1.2}, 7)["ship/pass"] == 0.0
    amount = R.ship_rule(
        BY_NAME["pass_yds"], {"mae_model": 1, "mae_baseline": 2, "coverage_80": 0.8}, per
    )
    assert "ship/deviance_ok" not in amount


def test_scoreboard_rows_contract() -> None:
    f = scored_frame()
    rows = R.scoreboard_rows(f, "backtest")
    assert rows.schema == pl.Schema(SCOREBOARD_SCHEMA)
    assert rows["position_group"].unique().to_list() == ["TEAM"]
    assert rows["mode"].unique().to_list() == ["backtest"]
    assert rows.height == f.select("season", "week").unique().height
    assert rows["brier_model"].null_count() == 0 and rows["calibration_ece"].null_count() == 0
    amounts = R.scoreboard_rows(scored_frame("amount"), "live")
    assert amounts["brier_model"].null_count() == amounts.height
    # unplayed rows are left out
    assert R.scoreboard_rows(
        f.with_columns(pl.lit(None, dtype=pl.Boolean).alias("played")), "live"
    ).is_empty()


# ---- backtest outputs ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["pass_yds", "takeaways"])
def test_backtest_writes_predictions_summary_and_scoreboard(mocked, data, name) -> None:
    paths, runs = mocked
    t = BY_NAME[name]
    res = R.run_backtest(
        t, data=data, report_seasons=REPORT, log=lambda *_: None, launched_by="agent"
    )
    folder = paths.runs / "backtests" / "team" / t.key
    assert res.saved_to == folder
    p = pl.read_parquet(folder / R.PRED_FILE)
    assert list(p.columns[: len(R.TEAM_PRED_SCHEMA)]) == list(R.TEAM_PRED_SCHEMA)
    seed_cols = {"_p10_raw", "_p90_raw"} if t.kind == "amount" else {"_p_raw"}
    assert seed_cols <= set(p.columns) and {"range_param", "n_train"} <= set(p.columns)
    assert p["played"].all() and p["actual"].null_count() == 0
    assert set(p["season"].unique()) <= set(range(REPORT[0] - R.BURN_IN, REPORT[-1] + 1))
    summary = json.loads((folder / "summary.json").read_text())
    for k in ("mae_model", "mae_baseline", "coverage_80", "ship/pass", "seasons_beating_baseline"):
        assert k in summary
    assert summary["config"]["target"] == name and summary["config"]["market_features"] is True
    board = pl.read_parquet(paths.runs / "backtests" / "team" / "scoreboard.parquet")
    assert set(board["season"]) == set(REPORT) and board["position_group"].unique().to_list() == [
        "TEAM"
    ]
    run = runs[-1]
    assert run.finished and "ship/pass" in run.summary
    curves = [d for d in run.logged if "bt/step" in d]
    assert curves and {"bt/mae_model", "bt/mae_baseline", "bt/cum_improvement_pct"} <= set(
        curves[0]
    )
    assert ("bt/cum_deviance_model" in curves[0]) == (t.kind == "count")
    steps = [d["bt/step"] for d in curves]
    assert steps == sorted(steps) and steps[0] == 1
    final = runs[-1].logged[-1]
    assert {"by_season", "accuracy_scoreboard", "mae_by_season", "predictions"} <= set(final)
    assert ("reliability_p_ge1" in final) == (t.kind == "count")
    assert res.url == run.url


def test_no_market_variant_has_its_own_folder_and_keeps_the_scoreboard(mocked, data) -> None:
    paths, runs = mocked
    t = BY_NAME["rush_yds"]
    R.run_backtest(t, data=data, report_seasons=REPORT, log=lambda *_: None, use_wandb=False)
    board = paths.runs / "backtests" / "team" / "scoreboard.parquet"
    before = board.read_bytes()
    res = R.run_backtest(
        t, data=data, report_seasons=REPORT, log=lambda *_: None, use_wandb=False, no_market=True
    )
    assert res.saved_to.name == "rush_yds-team_nomarket"
    cfg = json.loads((res.saved_to / "summary.json").read_text())["config"]
    assert cfg["market_features"] is False and not any(
        c.startswith("mkt_") for c in cfg["features"]
    )
    assert board.read_bytes() == before  # research variants never touch the canonical scoreboard
    assert (
        R.backtest_label(t, no_market=True, num_leaves=4) == "rush_yds-team_nomarket_num_leaves-4"
    )
    assert runs == []  # use_wandb=False starts no run


def test_backtest_predictions_ignore_the_future(mocked, data) -> None:
    """Scramble every label and feature after a week: that week and the earlier ones must
    predict the same."""
    t = BY_NAME["pass_yds"]
    cut = (REPORT[0], 3)
    base = R.run_backtest(
        t, data=data, report_seasons=REPORT, log=lambda *_: None, use_wandb=False, save=False
    ).preds
    late = (pl.col("season") > cut[0]) | ((pl.col("season") == cut[0]) & (pl.col("week") > cut[1]))
    junk = [
        pl.when(late).then(pl.col(c) * -3.0 + 11.0).otherwise(pl.col(c)).alias(c)
        for c, dtype in data.feats.schema.items()
        if dtype == pl.Float64 and c not in ("season", "week")
    ]
    scrambled = R.TeamData(data.feats.with_columns(junk), data.inp)
    again = R.run_backtest(
        t, data=scrambled, report_seasons=REPORT, log=lambda *_: None, use_wandb=False, save=False
    ).preds
    upto = ~late
    cols = ["season", "week", "game_id", "team", "p10", "p50", "p90", "mean"]
    a = base.filter(upto).select(cols).sort("game_id", "team")
    b = again.filter(upto).select(cols).sort("game_id", "team")
    assert a.height > 20 and a.equals(b)
    assert not base.filter(late).select("p50").equals(again.filter(late).select("p50"))


def test_walk_forward_history_never_reaches_the_key(data) -> None:
    t = BY_NAME["takeaways"]
    frame, feats = R.target_frame(data, t)
    model = TM.TeamWeekModel(t, feats, small_config(t))
    keys = [AsOf(SEASON, w) for w in (2, 3)]
    out = walk_forward(frame, keys, model, label="y", min_train_rows=20)
    assert out["week"].unique().sort().to_list() == [2, 3]


# ---- the weekly fit ----------------------------------------------------------------------------


@pytest.fixture
def live(mocked, monkeypatch, world):
    """The in-progress season with its week-6 first game already kicked off."""
    paths, runs = mocked
    g = world.games.filter(pl.col("season") == SEASON, pl.col("week") == PLAYED_THROUGH + 1)
    first = g.sort("kickoff_utc")["kickoff_utc"][0]
    now = dt.datetime.now(dt.UTC)
    shift = (now - dt.timedelta(hours=2)) - first
    shifted_games = world.games.with_columns(pl.col("kickoff_utc") + shift)
    w = TS.TeamInputs(
        games=shifted_games,
        box=world.box,
        pace=world.pace,
        ratings=world.ratings,
        game_features=world.game_features,
        context=world.context,
        forecast=world.forecast,
        last_season=SEASON,
    )
    feats = TS.build_team_features(w, first_season=SEASONS[0] + 1)
    shifted_games.write_parquet(paths.curated / "games.parquet")
    monkeypatch.setattr(R, "build_team_data", lambda *a, **k: R.TeamData(feats, w))
    return paths, runs, feats


def test_train_writes_projections_models_and_artifact(live, tmp_path) -> None:
    paths, runs, feats = live
    run_dir = tmp_path / "scratch" / str(SEASON) / "week06"
    model_dir = tmp_path / "scratch_models"
    out = R.run_train(
        SEASON,
        launched_by="agent",
        targets=TM.TEAM_TARGETS,
        log=lambda *_: None,
        run_dir=run_dir,
        model_dir=model_dir,
        backtest_root=tmp_path / "no_backtest",
    )
    assert out["predictions"] == run_dir / R.PRED_FILE and out["models"] == model_dir
    t = pl.read_parquet(out["predictions"])
    assert list(t.columns) == list(R.TEAM_PRED_SCHEMA)
    assert t["week"].unique().to_list() == [PLAYED_THROUGH + 1]
    # the game that kicked off two hours ago gets no projection; the later one does
    assert t["game_id"].n_unique() == 1 and (t["kickoff_utc"] > t["created_at"]).all()
    assert t["target"].n_unique() == 5 and t["actual"].null_count() == t.height
    assert (t["created_at"] < t["kickoff_utc"]).all()
    assert (model_dir / "meta.json").exists() and (model_dir / "pass_yds-team-q50.txt").exists()
    meta = json.loads((model_dir / "meta.json").read_text())
    assert meta["model_version"] == f"team-model-v1:{SEASON}-w06"
    assert runs[-1].artifacts == [("team-model", [f"{SEASON}-w06"])]  # not promoted
    # the walk-forward rows of the earlier weeks and their scoreboard rows sit next to the week
    season_dir = run_dir.parent
    wf = pl.read_parquet(season_dir / "team_walkforward.parquet")
    assert wf["week"].max() == PLAYED_THROUGH and wf["actual"].null_count() == 0
    board = pl.read_parquet(season_dir / R.SCOREBOARD_FILE)
    assert board["mode"].unique().to_list() == ["backtest"]
    # and nothing went to the data root's live folders
    assert not (paths.runs / str(SEASON)).exists()


def test_train_rerun_keeps_saved_pre_kickoff_rows_of_started_games(live, tmp_path) -> None:
    _, _, feats = live
    run_dir = tmp_path / "scratch" / str(SEASON) / "week06"
    kw = dict(
        targets=TM.TEAM_TARGETS,
        log=lambda *_: None,
        run_dir=run_dir,
        model_dir=tmp_path / "m",
        backtest_root=tmp_path / "nb",
        use_wandb=False,
    )
    first = R.run_train(SEASON, **kw)["table"]
    started_game = feats.filter(
        (pl.col("season") == SEASON) & (pl.col("week") == PLAYED_THROUGH + 1)
    ).sort("kickoff_utc")["game_id"][0]
    assert started_game not in first["game_id"].to_list()
    # pretend that game's projection was saved before it kicked off
    saved = (
        first.head(5)
        .with_columns(
            pl.lit(started_game).alias("game_id"),
            (pl.col("kickoff_utc") - pl.duration(days=3)).alias("kickoff_utc"),
        )
        .with_columns((pl.col("kickoff_utc") - pl.duration(hours=5)).alias("created_at"))
    )
    pd = pl.concat([first, saved]).sort("game_id")
    pd.write_parquet(run_dir / R.PRED_FILE)
    out = R.run_train(SEASON, **kw)
    kept = pl.read_parquet(out["predictions"])
    assert out["kept"] == 1
    assert started_game in kept["game_id"].to_list()
    assert kept.filter(pl.col("game_id") == started_game).height == 5


def test_train_needs_targets_until_the_ship_decision_names_them(live, monkeypatch) -> None:
    monkeypatch.setattr(TM, "live_team_targets", lambda keys=None: ())
    monkeypatch.setattr(R, "live_team_targets", lambda keys=None: ())
    with pytest.raises(ValueError, match="no team targets to project"):
        R.run_train(SEASON, log=lambda *_: None, use_wandb=False)


def test_train_refuses_a_week_that_is_not_ready(live) -> None:
    with pytest.raises(ValueError, match="isn't predictable yet"):
        R.run_train(
            SEASON,
            PLAYED_THROUGH + 3,
            targets=TM.TEAM_TARGETS,
            log=lambda *_: None,
            use_wandb=False,
        )


def test_train_can_promote_only_when_asked(live, tmp_path) -> None:
    _, runs, _ = live
    kw = dict(
        targets=TM.TEAM_TARGETS,
        log=lambda *_: None,
        run_dir=tmp_path / "s" / str(SEASON) / "week06",
        model_dir=tmp_path / "m",
        backtest_root=tmp_path / "nb",
    )
    R.run_train(SEASON, promote=True, **kw)
    assert runs[-1].artifacts == [("team-model", [f"{SEASON}-w06", "production"])]


# ---- the live scoreboard -----------------------------------------------------------------------


def test_score_weeks_scores_saved_pre_kickoff_rows_only(mocked, data, tmp_path) -> None:
    t = BY_NAME["sacks_taken"]
    preds = R.run_backtest(
        t, data=data, report_seasons=REPORT, log=lambda *_: None, use_wandb=False, save=False
    ).preds
    week = 4
    w = preds.filter((pl.col("season") == SEASON) & (pl.col("week") == week))
    season_dir = tmp_path / "scratch_season"
    folder = season_dir / f"week{week:02d}"
    folder.mkdir(parents=True)
    # saved the day before kickoff, results not known yet
    saved = w.with_columns(
        (pl.col("kickoff_utc") - pl.duration(days=2)).alias("created_at"),
        pl.lit(None, dtype=pl.Float64).alias("actual"),
        pl.lit(None, dtype=pl.Boolean).alias("played"),
    )
    late = w.head(2).with_columns(
        (pl.col("kickoff_utc") + pl.duration(hours=1)).alias("created_at"),
        pl.lit(None, dtype=pl.Float64).alias("actual"),
        pl.lit(None, dtype=pl.Boolean).alias("played"),
    )
    pl.concat([saved, late]).write_parquet(folder / R.PRED_FILE)
    board_path = tmp_path / "scratch_board.parquet"
    board = R.score_weeks(
        SEASON,
        [week, week + 1],
        season_dir=season_dir,
        board_path=board_path,
        data=data,
        use_wandb=False,
        log=lambda *_: None,
    )
    row = board.row(0, named=True)
    assert board.height == 1 and row["mode"] == "live" and row["week"] == week
    assert row["n_scored"] == w.height  # the two rows made after kickoff were left out
    scored = pl.read_parquet(folder / "predictions_teams_scored.parquet")
    truth = data.feats.filter((pl.col("season") == SEASON) & (pl.col("week") == week))
    j = scored.join(truth.select("game_id", "team", "sacks_taken"), on=["game_id", "team"])
    assert (j["actual"] == j["sacks_taken"]).all() and j["played"].all()
    # scoring again is idempotent
    again = R.score_weeks(
        SEASON, [week], season_dir=season_dir, board_path=board_path, data=data,
        use_wandb=False, log=lambda *_: None,
    )  # fmt: skip
    assert again.height == 1
    assert R.score_weeks(
        SEASON, [9], season_dir=season_dir, board_path=board_path, data=data, use_wandb=False,
        log=lambda *_: None,
    ).is_empty()  # fmt: skip


def test_keep_started_teams_without_a_saved_file(tmp_path) -> None:
    p = pl.DataFrame({"game_id": ["a"]})
    out, n = R.keep_started_teams(p, tmp_path / "missing.parquet", dt.datetime.now(dt.UTC))
    assert out.equals(p) and n == 0


def test_seed_history_only_takes_earlier_seasons(tmp_path) -> None:
    t = BY_NAME["pass_yds"]
    folder = tmp_path / t.key
    folder.mkdir()
    rows = pl.DataFrame(
        {
            "season": [2021, 2022, 2023, 2024],
            "week": [1] * 4,
            "game_id": list("abcd"),
            "team": ["X"] * 4,
            "p10": [1.0] * 4,
            "p50": [2.0] * 4,
            "p90": [3.0] * 4,
            "mean": [2.0] * 4,
            "actual": [2.0] * 4,
            "_p10_raw": [1.0] * 4,
            "_p90_raw": [3.0] * 4,
        }
    )
    rows.write_parquet(folder / R.PRED_FILE)
    h = R._seed_history(tmp_path, t, 2024)
    assert h["season"].to_list() == [2022, 2023]  # BURN_IN seasons back, never the season itself
    assert {"y", "_p10_raw", "_p90_raw"} <= set(h.columns)
    assert R._seed_history(tmp_path / "none", t, 2024).is_empty()


# ---- tuning ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "score"), [("pass_yds", "tune/mae_model"), ("takeaways", "tune/deviance_model")]
)
def test_tune_scores_only_the_tune_seasons_and_picks_on_the_right_metric(
    mocked, monkeypatch, data, name, score
) -> None:
    _, runs = mocked
    monkeypatch.setattr(R, "TUNE_SEASONS", (SEASONS[2], SEASONS[3]))
    seen: list[int] = []
    real = R.fit_player_model

    def spy(train, w, target, feats, config, **kw):
        seen.append(int(train["season"].max()))
        return real(train, w, target, feats, config, **kw)

    monkeypatch.setattr(R, "fit_player_model", spy)
    t = BY_NAME[name]
    grid = {"num_leaves": [3, 4], "n_estimators": [4, 8]}
    out = R.run_tune(t, data=data, grid=grid, log=lambda *_: None)
    assert out.height == 4 and out[score].is_sorted()
    assert R.tune_score(t) == score
    assert max(seen) <= SEASONS[3]  # nothing after the tune seasons was fitted on
    run = runs[-1]
    assert run.finished
    points = [d for d in run.logged if "tune/step" in d]
    assert [d["tune/step"] for d in points] == [1, 2, 3, 4]
    assert {"tune/num_leaves", "tune/n_estimators", "tune/mae_model"} <= set(points[0])
    assert ("tune/deviance_model" in points[0]) == (t.kind == "count")
    assert run.summary["best/num_leaves"] == out["num_leaves"][0]
    assert "tune_results" in run.logged[-1]
    quiet = R.run_tune(t, data=data, grid=grid, log=lambda *_: None, use_wandb=False)
    assert quiet.equals(out) and len(runs) == 1
