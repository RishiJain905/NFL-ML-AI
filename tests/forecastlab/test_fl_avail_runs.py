"""FL00 availability runs on a synthetic data root: backtest + promote, the weekly predict (the
kickoff rule) and the grade (re-runs write the same files). No W&B, no real data."""

from __future__ import annotations

import json
from datetime import timedelta

import polars as pl
import pytest
from fl_avail_fixtures import kickoff, make_league, prepared, small_cfg, write_root

from nflengine.forecastlab import availability as A
from nflengine.forecastlab import availability_runs as R
from nflengine.forecastlab import schema
from nflengine.paths import DataPaths


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    r = tmp_path_factory.mktemp("fl00root")
    write_root(r, make_league())
    return DataPaths(r)


@pytest.fixture(scope="module")
def promoted(root):
    mp = pytest.MonkeyPatch()
    mp.setattr(A, "settings", lambda overrides=None: A._merge(small_cfg(), overrides or {}))
    try:
        out = R.run_backtest(use_wandb=False, promote=True, paths=root, log=lambda *a: None)
    finally:
        mp.undo()
    return out


def test_backtest_writes_graded_predictions_and_promotes(root, promoted):
    assert set(promoted["views"]) == set(schema.VIEWS)
    for view in schema.VIEWS:
        res = promoted["views"][view]
        assert set(res["verdict"]) == set(schema.AVAIL_OUTPUTS)
        folder = schema.backtest_dir(root, "availability", view)
        df = pl.read_parquet(folder / "predictions.parquet")
        assert list(df.columns) == list(schema.AVAIL_SCHEMA)
        assert df["season"].unique().to_list() == [2016]
        assert df["played"].null_count() == 0
        summ = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        assert summ["seasons_scored"] == [2016] and "verdict" in summ
    prod = json.loads(schema.production_file(root, "availability").read_text(encoding="utf-8"))
    assert prod["version"] == promoted["version"]
    assert set(prod["shipped"]) == set(schema.VIEWS)
    model = R.load_production(root)
    assert set(model["views"]) == set(schema.VIEWS)


def test_promote_refuses_research_runs(root):
    with pytest.raises(ValueError):
        R.run_backtest(use_wandb=False, promote=True, tune=True, paths=root, log=lambda *a: None)


def test_predict_never_remakes_a_game_after_kickoff(root, promoted):
    S, W = 2016, 5
    early = kickoff(S, W, 0) - timedelta(days=4)
    a = R.run_predict(
        S, W, view="tuesday", use_wandb=False, paths=root, now=early, log=lambda *a: None
    )
    games = sorted(a["frame"]["game_id"].unique().to_list())
    assert len(games) == 2 and a["meta"]["games_after_kickoff"] == []
    folder = schema.week_dir(root, S, W)
    first = pl.read_parquet(folder / "availability.parquet")
    # Saturday's view runs after the first game kicked off: that game keeps its Tuesday rows only
    between = kickoff(S, W, 0) + timedelta(hours=1)
    b = R.run_predict(
        S, W, view="saturday", use_wandb=False, paths=root, now=between, log=lambda *a: None
    )
    after = pl.read_parquet(folder / "availability.parquet")
    started = first.filter(pl.col("kickoff_utc") <= between)["game_id"].unique().to_list()
    assert len(started) == 1 and started[0] in games
    assert after.filter(pl.col("game_id") == started[0]).equals(
        first.filter(pl.col("game_id") == started[0])
    )
    assert b["frame"].filter(pl.col("game_id") == started[0]).height == 0
    assert set(after["view"].unique().to_list()) == {"tuesday", "saturday"}
    meta = json.loads((folder / "availability.json").read_text(encoding="utf-8"))
    assert set(meta["views"]) == {"tuesday", "saturday"}
    assert meta["views"]["saturday"]["games_kept_after_kickoff"] == started
    assert (
        meta["shipped"]
        == json.loads(schema.production_file(root, "availability").read_text(encoding="utf-8"))[
            "shipped"
        ]
    )
    # a re-run of Tuesday's view after kickoff keeps both games' Tuesday rows untouched
    late = kickoff(S, W, 1) + timedelta(hours=1)
    R.run_predict(S, W, view="tuesday", use_wandb=False, paths=root, now=late, log=lambda *a: None)
    again = pl.read_parquet(folder / "availability.parquet")
    assert again.filter(pl.col("view") == "tuesday").equals(
        first.filter(pl.col("view") == "tuesday")
    )


def _no_results(df: pl.DataFrame) -> bool:
    return all(df[c].null_count() == df.height for c in R.RESULT_COLS)


def test_predict_after_kickoff_without_rows_flags_the_game(root, promoted):
    S, W = 2016, 6
    now = kickoff(S, W, 0) + timedelta(hours=1)
    out = R.run_predict(
        S, W, view="saturday", use_wandb=False, paths=root, now=now, log=lambda *a: None
    )
    late = out["meta"]["games_after_kickoff"]
    assert out["meta"]["views"]["saturday"]["games_after_kickoff"] == late
    assert len(late) == 1
    assert out["frame"]["game_id"].n_unique() == 2
    inh = pl.read_parquet(schema.week_dir(root, S, W) / "inherit.parquet")
    assert list(inh.columns) == list(schema.INHERIT_SCHEMA)
    # every game here is already played, yet a forecast never carries results
    saved = pl.read_parquet(schema.week_dir(root, S, W) / "availability.parquet")
    assert _no_results(out["frame"]) and _no_results(saved)


def test_grade_leaves_games_made_after_kickoff_out_of_the_score(root, promoted):
    S, W = 2016, 6  # the Saturday view of the first game was made after its kickoff (above)
    folder = schema.week_dir(root, S, W)
    if not (folder / "availability.parquet").exists():
        now = kickoff(S, W, 0) + timedelta(hours=1)
        R.run_predict(
            S, W, view="saturday", use_wandb=False, paths=root, now=now, log=lambda *a: None
        )
    late = R.late_games(folder / "availability.json")["saturday"]
    g = R.run_grade(S, W, use_wandb=False, paths=root, log=lambda *a: None)
    rows = g["frame"]
    assert rows["played"].null_count() == 0  # results are facts: filled for the late game too
    n_late = rows.filter(pl.col("game_id").is_in(late)).height
    assert n_late > 0 and g["summary"]["excluded_after_kickoff"] == n_late
    board = pl.read_parquet(schema.season_dir(root, S) / "availability_scoreboard.parquet")
    wk = board.filter(
        (pl.col("week") == W)
        & (pl.col("view") == "saturday")
        & (pl.col("output") == "play")
        & (pl.col("group") == "ALL")
    )
    assert wk["n"].to_list() == [rows.height - n_late]


def test_predict_is_deterministic(root, promoted):
    S, W = 2016, 4
    now = kickoff(S, W, 0) - timedelta(days=4)
    a = R.run_predict(
        S, W, view="saturday", use_wandb=False, paths=root, now=now, log=lambda *a: None
    )["frame"]
    b = R.run_predict(
        S, W, view="saturday", use_wandb=False, paths=root, now=now, log=lambda *a: None
    )["frame"]
    assert a.equals(b)


def test_grade_in_pieces_and_reruns_write_the_same_files(root, promoted):
    S, W = 2016, 3
    now = kickoff(S, W, 0) - timedelta(days=4)
    for view in schema.VIEWS:
        R.run_predict(S, W, view=view, use_wandb=False, paths=root, now=now, log=lambda *a: None)
    folder = schema.week_dir(root, S, W)
    assert _no_results(pl.read_parquet(folder / "availability.parquet"))
    board = schema.season_dir(root, S) / "availability_scoreboard.parquet"
    key = ["season", "week", "view", "output", "group"]
    # 1. one game's snap counts haven't arrived yet: its rows wait
    snaps_f = root.curated / "snaps.parquet"
    snaps = pl.read_parquet(snaps_f)
    games = sorted(pl.read_parquet(folder / "availability.parquet")["game_id"].unique().to_list())
    lagging = games[0]
    snaps.filter(pl.col("game_id") != lagging).write_parquet(snaps_f)
    try:
        g0 = R.run_grade(S, W, use_wandb=False, paths=root, log=lambda *a: None)
    finally:
        snaps.write_parquet(snaps_f)
    waiting = g0["frame"].filter(pl.col("game_id") == lagging).height
    assert g0["summary"]["waiting"] == waiting > 0
    assert g0["frame"].filter(pl.col("game_id") == lagging)["played"].null_count() == waiting
    assert g0["summary"]["games_graded"] == 1
    b0 = pl.read_parquet(board).filter(pl.col("week") == W)
    # 2. they arrive: the next grade fills them and replaces the week's scoreboard rows
    g1 = R.run_grade(S, W, use_wandb=False, paths=root, log=lambda *a: None)
    assert g1["summary"]["waiting"] == 0
    assert g1["frame"]["played"].null_count() == 0
    assert g1["summary"]["games_graded"] == 2
    b1 = pl.read_parquet(board)
    wk = b1.filter(pl.col("week") == W)
    assert wk.select(key).is_duplicated().sum() == 0
    n0 = b0.filter((pl.col("output") == "play") & (pl.col("group") == "ALL"))["n"].sum()
    n1 = wk.filter((pl.col("output") == "play") & (pl.col("group") == "ALL"))["n"].sum()
    assert n1 == g1["frame"].height and n0 < n1
    # the grade's actuals are the labels the backtest uses (never the forecast columns)
    lab = A.build_rows(prepared(make_league())).filter(
        (pl.col("season") == S) & (pl.col("week") == W)
    )
    j = g1["frame"].join(
        lab.select("game_id", "player_id", *[pl.col(c).alias(f"{c}_l") for c in R.RESULT_COLS]),
        on=["game_id", "player_id"],
    )
    assert j.height == g1["frame"].height
    for c in R.RESULT_COLS:
        a, b = j[c], j[f"{c}_l"]
        assert (a.is_null() == b.is_null()).all(), c
        if a.dtype == pl.Boolean:
            assert a.eq_missing(b).all(), c
        else:
            assert ((a - b).abs().fill_null(0.0) < 1e-12).all(), c
    # 3. a re-run on the same data writes the same files
    f1 = pl.read_parquet(folder / "availability.parquet")
    R.run_grade(S, W, use_wandb=False, paths=root, log=lambda *a: None)
    assert pl.read_parquet(board).equals(b1)
    assert pl.read_parquet(folder / "availability.parquet").equals(f1)
