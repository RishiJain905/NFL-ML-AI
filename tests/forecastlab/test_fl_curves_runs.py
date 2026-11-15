"""FL01: the backtest, the weekly curves and the grading, on a synthetic data root (no network,
no W&B, nothing from the real data root)."""

from __future__ import annotations

import json
import re
from datetime import timedelta

import numpy as np
import polars as pl
import pytest
from fl_curves_fixtures import (
    kickoff,
    make_root,
    synth_rows,
    write_live_week,
    write_production,
)

from nflengine.forecastlab import curves as C
from nflengine.forecastlab import curves_runs as R
from nflengine.forecastlab import schema as S
from nflengine.forecastlab.curves import Settings
from nflengine.paths import DataPaths

KEY = "rec_yds-wrte"
T = S.CURVE_BY_KEY[KEY]
SET = Settings("analog", 300, "point_w", 0)


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    """Small windows for the synthetic seasons; the target's settings pinned."""
    monkeypatch.setattr(
        R,
        "curves_cfg",
        lambda: {
            "history_from": 2017,
            "tune_seasons": "2019-2019",
            "report_seasons": "2021-2021",
            "settings": {KEY: SET.as_dict()},
        },
    )


def _scenario(tmp_path, season=2022, week=5):
    paths = make_root(tmp_path, KEY, seasons=range(2017, 2023), players=40)
    live = write_live_week(paths, KEY, season, week, players=40)
    write_production(paths, {KEY: SET.as_dict()}, [KEY])
    return paths, live


# ---- history ------------------------------------------------------------------------------------
def test_history_is_the_backtest_then_the_seasons_after_it(tmp_path):
    paths = make_root(tmp_path, KEY, seasons=range(2017, 2020), players=10)
    wf = paths.runs / "2020" / "player_walkforward.parquet"
    wf.parent.mkdir(parents=True)
    synth_rows(KEY, [2020], weeks=range(1, 6), players=10, seed=3).write_parquet(wf)
    h = R.load_history(paths, T, before=(2020, 4))
    seasons_weeks = set(zip(h["season"].to_list(), h["week"].to_list(), strict=True))
    assert (2019, 17) in seasons_weeks and (2017, 1) in seasons_weeks
    assert (2020, 3) in seasons_weeks and (2020, 4) not in seasons_weeks
    assert h.height == 3 * 17 * 10 + 3 * 10
    t = (h["season"] * 100 + h["week"]).to_list()
    assert t == sorted(t)  # in time order
    # a season the backtest already has is not read twice from a walk-forward file
    dup = paths.runs / "2019" / "player_walkforward.parquet"
    dup.parent.mkdir(parents=True)
    synth_rows(KEY, [2019], players=10, seed=9).write_parquet(dup)
    assert R.load_history(paths, T).filter(pl.col("season") == 2019).height == 17 * 10


def test_history_drops_rows_without_a_result_and_other_targets(tmp_path):
    paths = make_root(tmp_path, KEY, seasons=range(2017, 2019), players=10)
    f = paths.runs / "backtests" / "player" / KEY / "predictions_players.parquet"
    df = pl.read_parquet(f)
    junk = df.head(5).with_columns(pl.lit(None, dtype=pl.Float64).alias("actual"))
    other = df.head(5).with_columns(pl.lit("targets").alias("target"))
    pl.concat([df, junk, other]).write_parquet(f)
    assert R.load_history(paths, T).height == df.height  # only rows with a result, this target


def test_a_missing_backtest_says_what_to_run(tmp_path):
    with pytest.raises(FileNotFoundError, match="player backtest"):
        R.load_history(DataPaths(tmp_path / "empty"), T)


# ---- the backtest -------------------------------------------------------------------------------
def test_backtest_writes_graded_rows_and_a_verdict(tmp_path):
    paths = make_root(tmp_path, KEY, seasons=range(2017, 2022), players=40)
    res = R.run_backtest(targets=[KEY], use_wandb=False, paths=paths, log=lambda *_: None)
    e = res["targets"][KEY]
    assert e["rows"] == 40 * 17 and e["pooled"]["n"] == 40 * 17
    assert [r["season"] for r in e["by_season"]] == [2021]
    assert set(e["verdict"]["rule"]) == {"crps_vs_bell", "crps_vs_own", "brier_vs_bell", "band"}
    folder = paths.root / "forecastlab" / "backtests" / "curves" / KEY
    df = pl.read_parquet(folder / "predictions.parquet")
    assert df.height == 40 * 17
    schema_no_cdf = [c for c in S.CURVE_SCHEMA if c != "cdf"]
    assert df.columns[: len(schema_no_cdf)] == schema_no_cdf and "cdf" not in df.columns
    assert df["played"].all() and df["actual"].null_count() == 0
    assert set(df["season"].unique()) == {2021}
    summ = json.loads((folder / "summary.json").read_text())
    assert summ["pooled"]["n"] == df.height and summ["settings"]["k"] == 300
    # the file's per-row numbers are the ones the pooled summary averages
    assert df["crps_model"].mean() == pytest.approx(summ["pooled"]["crps_model"])
    assert df["p_band"].mean() == pytest.approx(summ["pooled"]["band_pred"])
    assert df["marks"][0].to_list()[0].keys() == {"mark", "p_over", "bell_p_over"}


def test_backtest_rows_match_a_direct_walk_forward(tmp_path):
    paths = make_root(tmp_path, KEY, seasons=range(2017, 2022), players=40)
    R.run_backtest(targets=[KEY], use_wandb=False, paths=paths, log=lambda *_: None)
    df = pl.read_parquet(
        paths.root / "forecastlab/backtests/curves" / KEY / "predictions.parquet"
    ).sort("week", "player_id")
    hist = R.load_history(paths, T)
    lo, hi = C.grid_bounds(KEY, T.floor)
    for st in C.walk_forward(hist, T.kind, [SET], lo=lo, hi=hi, score_seasons=[2021]):
        if st.week != 7:
            continue
        y = st.rows["actual"].to_numpy().astype(float)
        want = C.crps(st.curves[SET].cdf, y, lo)
        got = df.filter(pl.col("week") == 7).sort("player_id")["crps_model"].to_numpy()
        order = np.argsort(st.rows["player_id"].to_numpy())
        assert got == pytest.approx(want[order])


def test_tune_scores_every_candidate_and_ships_nothing(tmp_path):
    paths = make_root(tmp_path, KEY, seasons=range(2017, 2020), players=30)
    res = R.run_backtest(
        targets=[KEY], tune=True, use_wandb=False, paths=paths, log=lambda *_: None
    )
    e = res["targets"][KEY]
    assert res["seasons"] == [2019]
    # the chosen settings plus every candidate, best CRPS first
    assert len(e["tuning"]) == len(R.candidates("amount")) + 1
    crps = [r["crps"] for r in e["tuning"]]
    assert crps == sorted(crps) and e["best"] == e["tuning"][0]["settings"]
    assert {500, 1000, 2000, 4000} <= {r["settings"]["k"] for r in e["tuning"]}
    base = paths.root / "forecastlab" / "backtests" / "curves"
    assert (base / "_tuning" / "summary.json").exists()
    assert not (base / KEY).exists()  # a tuning run never writes a canonical backtest
    with pytest.raises(ValueError, match="reported backtest"):
        R.run_backtest(targets=[KEY], tune=True, promote=True, use_wandb=False, paths=paths)


def test_smoke_has_its_own_folder_and_few_weeks(tmp_path):
    paths = make_root(tmp_path, KEY, seasons=range(2017, 2022), players=30)
    res = R.run_backtest(
        targets=[KEY], smoke=True, use_wandb=False, paths=paths, log=lambda *_: None
    )
    assert res["targets"][KEY]["rows"] == 30 * R.SMOKE_WEEKS
    base = paths.root / "forecastlab" / "backtests" / "curves"
    assert (base / "_smoke" / KEY / "predictions.parquet").exists()
    assert not (base / KEY).exists()


def test_promote_writes_the_production_pointer(tmp_path):
    paths = make_root(tmp_path, KEY, seasons=range(2017, 2022), players=40)
    res = R.run_backtest(
        targets=[KEY], promote=True, use_wandb=False, paths=paths, log=lambda *_: None
    )
    ptr = json.loads(S.production_file(paths, "curves").read_text())
    assert re.fullmatch(R.VERSION_RE, ptr["version"]) and res["version"] == ptr["version"]
    ships = res["targets"][KEY]["verdict"]["ships"]
    assert ptr["shipped"] == ([KEY] if ships else [])
    assert ptr["params"][KEY] == SET.as_dict()
    params = json.loads((S.model_dir(paths, "curves") / ptr["version"] / "params.json").read_text())
    assert params["shipped"] == ptr["shipped"] and params["grids"][KEY] == [-15, 350]
    assert R.load_production(paths)["version"] == ptr["version"]


# ---- the weekly curves --------------------------------------------------------------------------
def test_predict_needs_a_promoted_model_and_a_projection_file(tmp_path):
    paths = make_root(tmp_path, KEY, seasons=range(2017, 2020), players=10)
    with pytest.raises(FileNotFoundError, match="promoted"):
        R.run_predict(2020, 3, use_wandb=False, paths=paths)
    write_production(paths, {KEY: SET.as_dict()}, [KEY])
    with pytest.raises(FileNotFoundError, match="player projections"):
        R.run_predict(2020, 3, use_wandb=False, paths=paths)


def test_predict_writes_the_contract(tmp_path):
    paths, live = _scenario(tmp_path)
    now = kickoff(2022, 5, 0) - timedelta(days=3)
    out = R.run_predict(2022, 5, use_wandb=False, paths=paths, now=now, log=lambda *_: None)
    f, meta = out["frame"], out["meta"]
    assert f.schema == pl.Schema(S.CURVE_SCHEMA)
    assert f.height == 40 and meta["rows"] == 40 and meta["games"] == 6
    for k in ("season", "week", "made_at", "rows", "games", "version", "shipped", "source_version",
              "source_created_at", "games_after_kickoff"):  # fmt: skip
        assert k in meta
    assert meta["shipped"] == [KEY] and meta["games_after_kickoff"] == []
    assert meta["source_version"] == "player-model-v1:2022-w05"
    assert meta["source_created_at"] == live["created_at"].max().isoformat()
    assert (
        json.loads((paths.root / "forecastlab/2022/week05/curves.json").read_text())["rows"] == 40
    )
    on_disk = pl.read_parquet(paths.root / "forecastlab/2022/week05/curves.parquet")
    assert on_disk.height == 40
    r = f.row(0, named=True)
    cdf = np.array(r["cdf"])
    assert cdf[-1] == 1.0 and (np.diff(cdf) >= -1e-12).all() and r["grid_lo"] == -15
    # the summaries are read off the same curve
    assert r["q50"] == -15 + int((cdf >= 0.5).argmax())
    for m in r["marks"]:
        k = int(np.floor(m["mark"])) + 15
        assert m["p_over"] == pytest.approx(1 - (cdf[k] if k < len(cdf) else 1.0))
    # the published projection is copied, never changed
    src = live.filter(pl.col("player_id") == r["player_id"]).row(0, named=True)
    assert (r["p10"], r["p50"], r["p90"], r["mean"]) == (
        src["p10"],
        src["p50"],
        src["p90"],
        src["mean"],
    )
    assert r["actual"] is None and r["played"] is None
    assert r["created_at"].isoformat().startswith(now.isoformat()[:19])
    # deterministic
    again = R.run_predict(2022, 5, use_wandb=False, paths=paths, now=now, log=lambda *_: None)
    assert again["frame"].equals(f)


def test_predict_reads_only_weeks_before_the_week(tmp_path):
    paths, _ = _scenario(tmp_path)
    now = kickoff(2022, 5, 0) - timedelta(days=3)
    a = R.run_predict(2022, 5, use_wandb=False, paths=paths, now=now, log=lambda *_: None)["frame"]
    f = paths.runs / "backtests" / "player" / KEY / "predictions_players.parquet"
    df = pl.read_parquet(f)
    late = (pl.col("season") == 2022) & (pl.col("week") >= 5)
    rng = np.random.default_rng(1)
    pl.concat(
        [
            df.filter(~late),
            df.filter(late).with_columns(
                pl.Series("actual", rng.integers(0, 300, df.filter(late).height).astype(float))
            ),
        ]
    ).write_parquet(f)
    b = R.run_predict(2022, 5, use_wandb=False, paths=paths, now=now, log=lambda *_: None)["frame"]
    assert a.equals(b)
    # but last week's results are used
    df2 = pl.read_parquet(f)
    prev = (pl.col("season") == 2022) & (pl.col("week") == 4)
    df2.with_columns(
        pl.when(prev).then(pl.col("actual") + 200.0).otherwise(pl.col("actual")).alias("actual")
    ).write_parquet(f)
    c = R.run_predict(2022, 5, use_wandb=False, paths=paths, now=now, log=lambda *_: None)["frame"]
    assert not a.equals(c)


def test_a_game_that_kicked_off_keeps_its_curves(tmp_path):
    paths, live = _scenario(tmp_path)
    path = paths.root / "forecastlab/2022/week05/curves.parquet"
    before = kickoff(2022, 5, 0) - timedelta(days=1)
    first = R.run_predict(2022, 5, use_wandb=False, paths=paths, now=before, log=lambda *_: None)
    assert first["meta"]["games_after_kickoff"] == []
    # the projections change (a new run of the week); then G0 and G3 (same kickoff) have started
    f = paths.run_dir(2022, 5) / "predictions_players.parquet"
    pl.read_parquet(f).with_columns(
        (pl.col("p50") * 2).alias("p50"), (pl.col("p90") * 2).alias("p90")
    ).write_parquet(f)
    after = kickoff(2022, 5, 0) + timedelta(hours=1)
    second = R.run_predict(2022, 5, use_wandb=False, paths=paths, now=after, log=lambda *_: None)
    m = second["meta"]
    assert m["games_kept"] == ["2022_05_G0", "2022_05_G3"] and m["games_after_kickoff"] == []
    new, old = second["frame"], first["frame"]
    kept = new.filter(pl.col("game_id").is_in(["2022_05_G0", "2022_05_G3"])).sort("player_id")
    was = old.filter(pl.col("game_id").is_in(["2022_05_G0", "2022_05_G3"])).sort("player_id")
    assert kept.height == was.height > 0 and kept.equals(was)  # exactly as they were
    fresh = new.filter(~pl.col("game_id").is_in(["2022_05_G0", "2022_05_G3"])).sort("player_id")
    unchanged = old.filter(~pl.col("game_id").is_in(["2022_05_G0", "2022_05_G3"])).sort("player_id")
    assert fresh.height == unchanged.height > 0
    assert (fresh["p50"] != unchanged["p50"]).all()  # games not yet played are made again
    assert fresh["created_at"].min() > unchanged["created_at"].max()
    assert pl.read_parquet(path).height == 40
    # no curves yet for a started game: made anyway and listed
    path.unlink()
    third = R.run_predict(2022, 5, use_wandb=False, paths=paths, now=after, log=lambda *_: None)
    assert third["meta"]["games_after_kickoff"] == ["2022_05_G0", "2022_05_G3"]
    assert third["meta"]["games_kept"] == [] and third["frame"].height == 40


def test_only_shipped_targets_get_curves(tmp_path):
    paths = make_root(tmp_path, KEY, seasons=range(2017, 2023), players=20)
    write_live_week(paths, KEY, 2022, 5, players=20)
    write_production(paths, {KEY: SET.as_dict()}, [])  # nothing shipped
    out = R.run_predict(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)
    assert out["frame"].height == 0 and out["meta"]["shipped"] == [] and out["meta"]["rows"] == 0


# ---- grading ------------------------------------------------------------------------------------
def _write_scored(paths, live, season=2022, week=5):
    """The production scoreboard's own graded file: 30 players played, 8 did not, 2 not graded."""
    keep = live.filter(pl.col("player_id") < "P038").sort("player_id")
    played = pl.Series("played", [i < 30 for i in range(keep.height)])
    actual = pl.Series("actual", [float(i * 3) if i < 30 else None for i in range(keep.height)])
    keep.drop("actual", "played").with_columns(played, actual).write_parquet(
        paths.run_dir(season, week) / "predictions_players_scored.parquet"
    )


def test_grade_fills_results_and_the_scoreboard(tmp_path):
    paths, live = _scenario(tmp_path)
    now = kickoff(2022, 5, 0) - timedelta(days=3)
    R.run_predict(2022, 5, use_wandb=False, paths=paths, now=now, log=lambda *_: None)
    _write_scored(paths, live)
    out = R.run_grade(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)
    s = out["summary"]
    assert s["source"] == "scored" and s["graded"] == 30 and s["not_played"] == 8
    assert s["waiting"] == 2
    cur = pl.read_parquet(paths.root / "forecastlab/2022/week05/curves.parquet")
    row = cur.filter(pl.col("player_id") == "P005").row(0, named=True)
    assert row["played"] is True and row["actual"] == 15.0
    assert cur.filter(pl.col("player_id") == "P031")["played"][0] is False
    assert cur.filter(pl.col("player_id") == "P031")["actual"][0] is None
    assert cur.filter(pl.col("player_id") == "P039")["played"][0] is None
    assert cur["cdf"].null_count() == 0  # the curves themselves are untouched
    board = pl.read_parquet(paths.root / "forecastlab/2022/curves_scoreboard.parquet")
    assert board.schema == pl.Schema(S.CURVE_SCORE_SCHEMA)
    r = board.row(0, named=True)
    assert (r["season"], r["week"], r["target_key"], r["n"], r["mode"]) == (
        2022,
        5,
        KEY,
        30,
        "live",
    )
    # the model's CRPS is the published curves' own, on the players who played
    g = cur.filter(pl.col("played").fill_null(False))
    lo = int(g["grid_lo"][0])
    want = C.crps(R.dense_cdf(g["cdf"], 350 - lo + 1), g["actual"].to_numpy(), lo).mean()
    assert r["crps_model"] == pytest.approx(want)
    assert r["band_hit"] == pytest.approx(
        C.band_hit(g["actual"].to_numpy(), C.round_half_up(g["p50"].to_numpy()), T.band).mean()
    )
    assert all(r[k] is not None for k in ("crps_bell", "crps_own", "brier_model", "pit_ece"))


def test_a_grade_rerun_gives_the_same_files(tmp_path):
    paths, live = _scenario(tmp_path)
    R.run_predict(
        2022, 5, use_wandb=False, paths=paths, now=kickoff(2022, 5, 0) - timedelta(days=3),
        log=lambda *_: None,
    )  # fmt: skip
    _write_scored(paths, live)
    R.run_grade(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)
    c1 = pl.read_parquet(paths.root / "forecastlab/2022/week05/curves.parquet")
    b1 = pl.read_parquet(paths.root / "forecastlab/2022/curves_scoreboard.parquet")
    R.run_grade(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)
    assert pl.read_parquet(paths.root / "forecastlab/2022/week05/curves.parquet").equals(c1)
    assert pl.read_parquet(paths.root / "forecastlab/2022/curves_scoreboard.parquet").equals(b1)


def test_grade_replaces_only_its_own_week(tmp_path):
    paths, live = _scenario(tmp_path)
    board = paths.root / "forecastlab/2022/curves_scoreboard.parquet"
    board.parent.mkdir(parents=True, exist_ok=True)
    other = pl.DataFrame(
        {"season": [2022], "week": [4], "target_key": [KEY], "n": [99], "mode": ["live"]}
    )
    S.conform(other, S.CURVE_SCORE_SCHEMA).write_parquet(board)
    R.run_predict(
        2022, 5, use_wandb=False, paths=paths, now=kickoff(2022, 5, 0) - timedelta(days=3),
        log=lambda *_: None,
    )  # fmt: skip
    _write_scored(paths, live)
    R.run_grade(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)
    got = pl.read_parquet(board).sort("week")
    assert got["week"].to_list() == [4, 5] and got["n"].to_list() == [99, 30]


def test_grade_falls_back_to_the_seasons_walk_forward_file(tmp_path):
    paths, live = _scenario(tmp_path)
    R.run_predict(
        2022, 5, use_wandb=False, paths=paths, now=kickoff(2022, 5, 0) - timedelta(days=3),
        log=lambda *_: None,
    )  # fmt: skip
    wf = live.filter(pl.col("player_id") < "P030").with_columns(
        pl.lit(True).alias("played"), pl.col("p50").round(0).alias("actual")
    )
    wf.write_parquet(paths.runs / "2022" / "player_walkforward.parquet")
    out = R.run_grade(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)
    s = out["summary"]
    # a game with rows there is done: a projected player missing from it didn't play
    assert s["source"] == "walkforward" and s["graded"] == 30 and s["not_played"] == 10
    assert s["waiting"] == 0


def test_grade_with_nothing_played_yet_writes_no_scoreboard_row(tmp_path):
    paths, live = _scenario(tmp_path)
    R.run_predict(
        2022, 5, use_wandb=False, paths=paths, now=kickoff(2022, 5, 0) - timedelta(days=3),
        log=lambda *_: None,
    )  # fmt: skip
    out = R.run_grade(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)
    assert out["summary"]["graded"] == 0 and out["summary"]["waiting"] == 40
    assert out["frame"].height == 0


# ---- the zero variant and a merged promotion (addendum, 2026-11-14) ----------------------
def test_variant_check_follows_the_pre_registered_rule(tmp_path):
    paths = make_root(tmp_path, KEY, seasons=range(2017, 2020), players=30)
    res = R.run_variant_check(targets=[KEY], use_wandb=False, paths=paths, log=lambda *_: None)
    assert list(res["targets"]) == [KEY] and res["seasons"] == [2019]
    e = res["targets"][KEY]
    # the same K, space and history as the chosen settings; only the method differs
    assert e["current"] == {**SET.as_dict(), "method": "analog"}
    assert e["variant"] == {**SET.as_dict(), "method": "analog_zero"}
    assert e["ok_crps"] == (e["crps_change_pct"] <= 0.1)
    assert e["ok_brier_ge1"] == (e["brier_ge1_variant"] < e["brier_ge1_current"])
    assert e["replace"] == (e["ok_crps"] and e["ok_brier_ge1"])
    assert e["crps_change_pct"] == pytest.approx(
        100 * (e["crps_variant"] - e["crps_current"]) / e["crps_current"]
    )
    assert "brier_ge1_bell" in e and "brier_ge1_own" in e
    f = paths.root / "forecastlab" / "backtests" / "curves" / "_tuning" / "zero_variant.json"
    assert json.loads(f.read_text())["targets"][KEY]["replace"] == e["replace"]
    # a count target is never part of it
    assert R.run_variant_check(targets=["receptions-wrte"], use_wandb=False, paths=paths,
                               log=lambda *_: None)["targets"] == {}  # fmt: skip


def test_backtest_pooled_scores_carry_the_one_or_more_diagnostic(tmp_path):
    paths = make_root(tmp_path, KEY, seasons=range(2017, 2022), players=30)
    res = R.run_backtest(targets=[KEY], use_wandb=False, paths=paths, log=lambda *_: None)
    p = res["targets"][KEY]["pooled"]
    for k in ("brier_ge1_model", "brier_ge1_bell", "brier_ge1_own", "ge1_pred", "ge1_hit"):
        assert k in p
    df = pl.read_parquet(paths.root / "forecastlab/backtests/curves" / KEY / "predictions.parquet")
    assert p["ge1_hit"] == pytest.approx((df["actual"] >= 1).mean())


def test_a_partial_promotion_keeps_the_targets_it_did_not_run(tmp_path):
    import time

    count_key = "receptions-wrte"
    paths = make_root(tmp_path, KEY, seasons=range(2017, 2022), players=40)
    make_root(tmp_path, count_key, seasons=range(2017, 2022), players=40)
    first = R.run_backtest(targets=[count_key], promote=True, use_wandb=False, paths=paths,
                           log=lambda *_: None)  # fmt: skip
    time.sleep(1.1)  # a new version stamp
    second = R.run_backtest(targets=[KEY], promote=True, use_wandb=False, paths=paths,
                            log=lambda *_: None)  # fmt: skip
    ptr = json.loads(S.production_file(paths, "curves").read_text())
    assert ptr["version"] == second["version"] != first["version"]
    # both targets' parameters are in the pointer; the one not re-run keeps its own
    assert set(ptr["params"]) == {KEY, count_key}
    assert ptr["params"][count_key] == R.settings_for(count_key).as_dict()
    assert ptr["params"][KEY] == SET.as_dict()
    # its shipped status is what its own run decided; the re-run decides only its target
    want = [
        k for k, r in ((KEY, second), (count_key, first)) if r["targets"][k]["verdict"]["ships"]
    ]
    assert ptr["shipped"] == [k for k in (t.key for t in S.CURVE_TARGETS) if k in want]
    # the version string is the latest promotion; `sources` says which one each target's
    # parameters and verdict come from
    assert ptr["sources"] == {count_key: first["version"], KEY: second["version"]}
    assert set(ptr["wandb_runs"]) == {KEY, count_key}  # a run recorded for every target
    params = json.loads((S.model_dir(paths, "curves") / ptr["version"] / "params.json").read_text())
    assert set(params["backtest"]) == {KEY, count_key} and set(params["grids"]) == {KEY, count_key}
    assert params["shipped"] == ptr["shipped"]


# ---- review fixes: late curves, grading in pieces, locks, `through` (2026-11-14) ----------
GAMES = [f"2022_05_G{g}" for g in range(6)]


def _scored_for(paths, live, games, season=2022, week=5):
    """The production scoreboard's graded file for the players of `games`: a player whose index
    ends in 9 did not play, the rest played and gained 3 x their index."""
    keep = live.filter(pl.col("game_id").is_in(sorted(games))).sort("player_id")
    idx = [int(p[1:]) for p in keep["player_id"]]
    played = pl.Series("played", [i % 10 != 9 for i in idx])
    actual = pl.Series("actual", [float(i * 3) if i % 10 != 9 else None for i in idx])
    keep.drop("actual", "played").with_columns(played, actual).write_parquet(
        paths.run_dir(season, week) / "predictions_players_scored.parquet"
    )
    return keep


def _board(paths):
    return pl.read_parquet(paths.root / "forecastlab/2022/curves_scoreboard.parquet")


def _curves(paths):
    return pl.read_parquet(paths.root / "forecastlab/2022/week05/curves.parquet")


def test_the_scoreboard_leaves_out_curves_made_after_kickoff(tmp_path):
    paths, live = _scenario(tmp_path)
    after = kickoff(2022, 5, 0) + timedelta(hours=1)  # G0 and G3 have kicked off
    first = R.run_predict(2022, 5, use_wandb=False, paths=paths, now=after, log=lambda *_: None)
    late = [GAMES[0], GAMES[3]]
    assert first["meta"]["games_after_kickoff"] == late
    # a second run keeps those rows, and they stay flagged (read off the rows: made at/after
    # their kickoff)
    again = R.run_predict(2022, 5, use_wandb=False, paths=paths, now=after, log=lambda *_: None)
    assert again["meta"]["games_kept"] == late and again["meta"]["games_after_kickoff"] == late
    _scored_for(paths, live, GAMES)
    out = R.run_grade(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)
    s = out["summary"]
    cur = _curves(paths)
    on_time = cur.filter(~pl.col("game_id").is_in(late))
    graded = cur.filter(pl.col("played").fill_null(False) & pl.col("actual").is_not_null())
    n_late = graded.filter(pl.col("game_id").is_in(late)).height
    assert n_late > 0 and s["excluded_after_kickoff"] == n_late
    assert s["games_after_kickoff"] == late and s["scored"] == s["graded"] - n_late
    # the late rows still get their results (they are facts) ...
    assert cur.filter(pl.col("game_id").is_in(late))["played"].null_count() == 0
    assert on_time["played"].null_count() == 0
    # ... but the score is the published curves of the games made in time, alone
    r = _board(paths).row(0, named=True)
    assert r["n"] == s["scored"]
    g = graded.filter(~pl.col("game_id").is_in(late))
    lo = int(g["grid_lo"][0])
    want = C.crps(R.dense_cdf(g["cdf"], 350 - lo + 1), g["actual"].to_numpy(), lo).mean()
    assert r["crps_model"] == pytest.approx(want)


def test_a_week_can_be_graded_in_pieces(tmp_path):
    paths, live = _scenario(tmp_path)
    R.run_predict(
        2022, 5, use_wandb=False, paths=paths, now=kickoff(2022, 5, 0) - timedelta(days=3),
        log=lambda *_: None,
    )  # fmt: skip
    # only three of the six games are in the grading source so far
    _scored_for(paths, live, GAMES[:3])
    s1 = R.run_grade(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)["summary"]
    c1 = _curves(paths)
    other = c1.filter(~pl.col("game_id").is_in(GAMES[:3]))
    assert s1["waiting"] == other.height > 0 and other["played"].null_count() == other.height
    b1 = _board(paths)
    assert b1.height == 1 and b1["n"][0] == s1["graded"] > 0
    # the other games arrive: a second grade fills the waiting rows ...
    _scored_for(paths, live, GAMES)
    s2 = R.run_grade(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)["summary"]
    c2 = _curves(paths)
    assert s2["waiting"] == 0 and c2["played"].null_count() == 0
    assert s2["graded"] == 36 > s1["graded"]  # 40 players, four of them (index ends in 9) sat
    # ... leaves what was graded as it was ...
    cols = ["player_id", "game_id", "actual", "played"]
    done = c1.filter(pl.col("game_id").is_in(GAMES[:3])).select(cols).sort("player_id", "game_id")
    now = c2.filter(pl.col("game_id").is_in(GAMES[:3])).select(cols).sort("player_id", "game_id")
    assert now.equals(done)
    # ... and the scoreboard has one set of rows for the week (replaced, not appended)
    b2 = _board(paths)
    assert b2.height == 1 and b2["n"][0] == s2["graded"]
    assert (b2["season"][0], b2["week"][0], b2["target_key"][0]) == (2022, 5, KEY)


def test_a_graded_result_is_kept_when_the_source_loses_it(tmp_path):
    paths, live = _scenario(tmp_path)
    R.run_predict(
        2022, 5, use_wandb=False, paths=paths, now=kickoff(2022, 5, 0) - timedelta(days=3),
        log=lambda *_: None,
    )  # fmt: skip
    _scored_for(paths, live, GAMES)
    R.run_grade(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)
    c1 = _curves(paths)
    _scored_for(paths, live, GAMES[:2])  # a source that knows fewer games than before
    s = R.run_grade(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)["summary"]
    assert _curves(paths).equals(c1) and s["waiting"] == 0


def test_predict_never_writes_results(tmp_path):
    paths, live = _scenario(tmp_path)
    # the projection file itself carries results (a game already played)
    live.with_columns(
        pl.lit(True).alias("played"), pl.col("p50").round(0).alias("actual")
    ).write_parquet(paths.run_dir(2022, 5) / "predictions_players.parquet")
    path = paths.root / "forecastlab/2022/week05/curves.parquet"
    for now in (kickoff(2022, 5, 0) - timedelta(days=1), kickoff(2022, 5, 0) + timedelta(hours=1)):
        path.unlink(missing_ok=True)  # the second time, the started games are made late
        out = R.run_predict(2022, 5, use_wandb=False, paths=paths, now=now, log=lambda *_: None)
        for f in (out["frame"], pl.read_parquet(path)):
            assert f.height == 40
            assert f["actual"].null_count() == 40 and f["played"].null_count() == 40
    # a started game's saved rows are kept exactly as they are, results and all
    _scored_for(paths, live, GAMES)
    R.run_grade(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)
    out = R.run_predict(
        2022, 5, use_wandb=False, paths=paths, now=kickoff(2022, 5, 0) + timedelta(hours=1),
        log=lambda *_: None,
    )  # fmt: skip
    kept = out["frame"].filter(pl.col("game_id").is_in([GAMES[0], GAMES[3]]))
    assert kept.height > 0 and kept["played"].null_count() == 0
    fresh = out["frame"].filter(~pl.col("game_id").is_in([GAMES[0], GAMES[3]]))
    assert fresh["played"].null_count() == fresh.height  # not started: made again, no results


def test_the_weeks_file_is_written_under_its_lock(tmp_path, monkeypatch):
    import contextlib

    from nflengine.ops import lock

    real_lock, real_write = lock.file_lock, lock.write_parquet_atomic
    held: list[str] = []
    writes: list[str] = []

    @contextlib.contextmanager
    def spy_lock(path, *a, **k):
        with real_lock(path, *a, **k):
            held.append(path.name)
            try:
                yield
            finally:
                held.remove(path.name)

    def spy_write(df, path, **k):
        if path.name == "curves.parquet":
            assert "curves.parquet" in held, "curves.parquet written outside its lock"
            writes.append(path.name)
        return real_write(df, path, **k)

    monkeypatch.setattr(lock, "file_lock", spy_lock)
    monkeypatch.setattr(lock, "write_parquet_atomic", spy_write)
    paths, live = _scenario(tmp_path)
    now = kickoff(2022, 5, 0) - timedelta(days=3)
    R.run_predict(2022, 5, use_wandb=False, paths=paths, now=now, log=lambda *_: None)
    _scored_for(paths, live, GAMES)
    R.run_grade(2022, 5, use_wandb=False, paths=paths, log=lambda *_: None)
    assert writes == ["curves.parquet", "curves.parquet"]  # the predict's and the grade's


def test_json_files_use_the_retrying_replace(tmp_path, monkeypatch):
    import os

    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError("a reader has the file open")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    R.atomic_json(tmp_path / "x.json", {"a": 1})
    assert json.loads((tmp_path / "x.json").read_text()) == {"a": 1} and len(calls) == 3


def test_through_is_the_newest_week_the_history_holds(tmp_path):
    paths, _ = _scenario(tmp_path)  # backtest history 2017-2022, weeks 1-17
    now = kickoff(2022, 5, 0) - timedelta(days=3)
    out = R.run_predict(2022, 5, use_wandb=False, paths=paths, now=now, log=lambda *_: None)
    assert out["meta"]["through"] == "2022-w04"
    # week 1: the last week of the previous season the history holds, never "w00"
    write_live_week(paths, KEY, 2022, 1, players=40)
    w1 = R.run_predict(
        2022, 1, use_wandb=False, paths=paths, now=kickoff(2022, 1, 0) - timedelta(days=3),
        log=lambda *_: None,
    )  # fmt: skip
    assert w1["meta"]["through"] == "2021-w17"
    # a history that stops earlier says so
    f = paths.runs / "backtests" / "player" / KEY / "predictions_players.parquet"
    df = pl.read_parquet(f)
    df.filter(~((pl.col("season") == 2021) & (pl.col("week") >= 15))).write_parquet(f)
    w1b = R.run_predict(
        2022, 1, use_wandb=False, paths=paths, now=kickoff(2022, 1, 0) - timedelta(days=3),
        log=lambda *_: None,
    )  # fmt: skip
    assert w1b["meta"]["through"] == "2021-w14"
    assert (
        json.loads((paths.root / "forecastlab/2022/week01/curves.json").read_text())["through"]
        == "2021-w14"
    )
