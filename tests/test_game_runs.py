"""Weekly production path (`nfl train game`) with W&B and the data drive mocked."""

import json

import polars as pl
import pytest
from test_game_model import MF, TF, synth

from nflengine.models import game_runs
from nflengine.paths import DataPaths

SEASON, WEEK = 2015, 6


class FakeRun:
    def __init__(self):
        self.summary: dict = {}
        self.logged: list = []
        self.artifacts: list = []
        self.url = "https://example.invalid/run"

    def log(self, data):
        self.logged.append(data)

    def log_artifact(self, art, aliases):
        self.artifacts.append((art.name, list(aliases)))

    def finish(self):
        pass


def live_frame() -> pl.DataFrame:
    """Six seasons of synthetic games; SEASON is in progress (weeks >= WEEK unplayed)."""
    f = synth(n_seasons=6)
    future = (pl.col("season") == SEASON) & (pl.col("week") >= WEEK)
    outcome = ["margin", "total", "home_score", "away_score", "home_win"]
    return f.with_columns(
        [pl.when(future).then(None).otherwise(pl.col(c)).alias(c) for c in outcome]
    ).with_columns(
        pl.col("margin").is_not_null().alias("completed"),
        pl.lit("QB A").alias("home_qb_name"),
        pl.lit("QB B").alias("away_qb_name"),
        pl.lit("schedule").alias("home_qb_source"),
        pl.lit("schedule").alias("away_qb_source"),
    )


@pytest.fixture
def mocked(tmp_path, monkeypatch):
    paths = DataPaths(tmp_path)
    for d in paths.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    frame = live_frame()
    frame.select("game_id", "season", "week", "completed", "kickoff_utc").write_parquet(
        paths.curated / "games.parquet"
    )
    runs: list[FakeRun] = []
    state = {"frame": frame}

    def fake_init_run(*args, **kwargs):
        runs.append(FakeRun())
        return runs[-1]

    monkeypatch.setattr(game_runs, "ensure_data_root", lambda: paths)
    monkeypatch.setattr(game_runs, "load_frame", lambda *a, **k: state["frame"])
    monkeypatch.setattr(game_runs, "init_run", fake_init_run)
    monkeypatch.setattr(game_runs, "dataset_version", lambda *a: {"pbp_snapshot": None})
    real = game_runs.model_config
    monkeypatch.setattr(
        game_runs,
        "model_config",
        lambda variant="model_only", **kw: real(
            variant,
            margin_features=MF + (("spread_line",) if variant == "market" else ()),
            total_features=TF + (("total_line",) if variant == "market" else ()),
            **kw,
        ),
    )
    return paths, runs, state


def test_train_writes_predictions_models_and_artifact(mocked) -> None:
    paths, runs, _ = mocked
    out = game_runs.run_train(SEASON, launched_by="agent", log=lambda *_: None)
    pred_path = paths.run_dir(SEASON, WEEK) / "predictions_games.parquet"
    assert out["predictions"] == pred_path and pred_path.exists()
    t = pl.read_parquet(pred_path)
    assert set(t["week"].to_list()) == {WEEK} and set(t["season"].to_list()) == {SEASON}
    assert t.filter(pl.col("is_primary")).height == t["game_id"].n_unique()
    assert set(t["trained_through"].to_list()) == {f"{SEASON}-w{WEEK - 1:02d}"}
    meta = json.loads(
        (paths.models / "game-model" / f"{SEASON}-w{WEEK:02d}" / "meta.json").read_text()
    )
    assert meta["trained_through"] == f"{SEASON}-w{WEEK - 1:02d}"
    assert (paths.models / "game-model" / f"{SEASON}-w{WEEK:02d}" / "market.joblib").exists()
    assert runs[-1].artifacts == [("game-model", [f"{SEASON}-w{WEEK:02d}"])]  # not promoted
    out = game_runs.run_train(SEASON, WEEK, launched_by="agent", promote=True, log=lambda *_: None)
    assert runs[-1].artifacts == [("game-model", [f"{SEASON}-w{WEEK:02d}", "production"])]


def test_train_ignores_rows_after_the_predicted_week(mocked) -> None:
    _, _, state = mocked
    base = game_runs.run_train(SEASON, WEEK, log=lambda *_: None)["table"]
    later = (pl.col("season") > SEASON) | ((pl.col("season") == SEASON) & (pl.col("week") > WEEK))
    state["frame"] = state["frame"].with_columns(
        [
            pl.when(later).then(pl.col(c) * -7 + 3).otherwise(pl.col(c)).alias(c)
            for c in ("net_diff", "elo_diff", "spread_line", "total_line", "off_sum")
        ]
    )
    again = game_runs.run_train(SEASON, WEEK, log=lambda *_: None)["table"]
    cols = ["game_id", "variant", "home_win_prob", "expected_margin", "pred_total"]
    assert (
        base.select(cols)
        .sort("game_id", "variant")
        .equals(again.select(cols).sort("game_id", "variant"))
    )


def test_train_refuses_a_week_that_is_not_ready(mocked) -> None:
    with pytest.raises(ValueError, match="isn't predictable yet"):
        game_runs.run_train(SEASON, WEEK + 2, log=lambda *_: None)
