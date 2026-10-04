"""Player model core (P06): the data layer, the own-stat / usage / team / ripple /
availability families, baselines, target frames, the LightGBM model and the run helpers.

The synthetic league comes from `test_player_efficiency.make_world` (4 teams, 2 seasons,
8 weeks; weeks 7-8 of 2022 unplayed) with an injury report, rosters and game features
added here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from test_player_efficiency import assert_same, changed_columns, make_world, scrambled, tk

from nflengine.features import player as PF
from nflengine.features.leakage import LeakageError
from nflengine.features.player_data import asof_join, player_history, tkey
from nflengine.models import player_model as M
from nflengine.models import player_runs as R
from nflengine.models.backtest import AsOf, SampleWeights, walk_forward
from nflengine.models.player_schema import (
    PRED_SCHEMA,
    SCOREBOARD_SCHEMA,
    TARGETS,
    conform,
    get_target,
    pool_expr,
    score_predictions,
)

SAME_WEEK_FRAMES = ("injuries", "rosters", "game_features")  # pre-game info for its own week


def _augment(inp):
    """Injury report, rosters and game features (expected QBs, market points)."""
    rng = np.random.default_rng(3)
    games = inp.games
    inj, ros = [], []
    for g in games.iter_rows(named=True):
        for team in (g["home_team"], g["away_team"]):
            for suf in ("WR1", "RB", "LB"):
                status = rng.choice(["Out", "Questionable", None, None])
                inj.append(
                    {
                        "season": g["season"],
                        "week": g["week"],
                        "team": team,
                        "gsis_id": f"{team}_{suf}",
                        "position": suf,
                        "full_name": f"{team} {suf}",
                        "report_status": status,
                        "practice_status": rng.choice(
                            [
                                "Did Not Participate In Practice",
                                "Limited Participation in Practice",
                                "Full Participation in Practice",
                            ]
                        ),
                        "report_primary_injury": "knee",
                    }
                )
            ros.append(
                {
                    "season": g["season"],
                    "week": g["week"],
                    "team": team,
                    "gsis_id": f"{team}_QB",
                    "position": "QB",
                    "depth_chart_position": "QB",
                    "status": "ACT",
                    "full_name": f"{team} QB",
                    "years_exp": 3,
                    "rookie_year": 2019,
                }
            )
    gf = games.select(
        "game_id",
        "season",
        "week",
        "home_team",
        "away_team",
        (pl.col("home_team") + "_QB").alias("home_qb_id"),
        (pl.col("away_team") + "_QB").alias("away_qb_id"),
        pl.lit(24.0).alias("mkt_home_points"),
        pl.lit(20.0).alias("mkt_away_points"),
        pl.lit(4.0).alias("spread_line"),
        pl.lit(44.0).alias("total_line"),
    )
    return replace(
        inp,
        injuries=pl.DataFrame(inj).with_columns(pl.col("season", "week").cast(pl.Int32)),
        rosters=pl.DataFrame(ros).with_columns(pl.col("season", "week").cast(pl.Int32)),
        game_features=gf.with_columns(pl.col("season", "week").cast(pl.Int32)),
    )


@pytest.fixture(scope="module")
def world():
    inp, hist, rows = make_world()
    inp = _augment(inp)
    rows = rows.join(
        inp.games.select("game_id", "kickoff_utc"), on="game_id", how="left"
    ).with_columns(pl.col("pgroup").alias("position"), pl.col("player_id").alias("player"))
    return inp, hist, rows.select(PF.ROW_COLS)


def _context(inp) -> pl.DataFrame:
    g = inp.games.select(
        "game_id",
        "home_team",
        "away_team",
        pl.lit(23.0).alias("pred_home_points"),
        pl.lit(21.0).alias("pred_away_points"),
        pl.lit(2.0).alias("expected_margin"),
    )
    return PF.game_context(g)


def _mine(inp, hist, rows):
    eq = PF.expected_qbs_from_games(inp.game_features)
    out = rows
    for f in (
        PF.usage_features(hist, rows),
        PF.team_features(inp, rows, _context(inp)),
        PF.ripple_features(inp, hist, rows, eq),
        PF.availability_features(inp, hist, rows),
    ):
        assert f.height == rows.height
        out = out.join(f, on=["player_id", "game_id"], how="left")
    return out


def _feature_cols(df: pl.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith(("use_", "team_", "rip_", "avail_"))]


# ---- data layer -----------------------------------------------------------------------------


def test_tkey_is_continuous_across_seasons() -> None:
    df = pl.DataFrame({"season": [2025, 2025, 2026], "week": [18, 22, 1]})
    t = df.select(tkey().alias("t"))["t"].to_list()
    assert t[0] < t[1] < t[2] and t[2] - t[1] == 1


def test_asof_join_is_strict_and_lag_waits_a_week() -> None:
    hist = pl.DataFrame({"p": ["a", "a", "a"], "t": [10, 11, 12], "v": [1.0, 2.0, 3.0]})
    rows = pl.DataFrame({"p": ["a", "a", "a"], "t": [11, 12, 13]})
    j0 = asof_join(rows, hist, by="p")
    assert j0["v"].to_list() == [1.0, 2.0, 3.0]  # strictly before: t=11 sees t=10 only
    j1 = asof_join(rows, hist, by="p", lag=1)
    assert j1["v"].to_list() == [None, 1.0, 2.0]  # a week-late source
    assert j1["t_hist"].to_list() == [None, 10, 11]


def test_history_flags_and_main_qb(world) -> None:
    inp, hist, _ = world
    qbs = hist.filter(pl.col("pgroup") == "QB")
    # one main QB per team-game
    per = qbs.filter(pl.col("main_qb")).group_by("game_id", "team").len()
    assert per["len"].max() == 1
    assert hist.filter(pool_expr("WR/TE"))["pgroup"].unique().sort().to_list() == ["TE", "WR"]
    assert set(hist.filter(pool_expr("EDGE/DL"))["pgroup"].unique()) <= {"DL", "LB"}


# ---- own stat + baselines -------------------------------------------------------------------


def _toy_hist() -> pl.DataFrame:
    """One WR: 3 games in 2025 (10, 20, 30 yards), 4 in 2026 (40, 50, 60, 70)."""
    recs = []
    for s, w, y in [
        (2025, 1, 10),
        (2025, 2, 20),
        (2025, 3, 30),
        (2026, 1, 40),
        (2026, 2, 50),
        (2026, 3, 60),
        (2026, 4, 70),
    ]:
        recs.append(
            {
                "player_id": "p",
                "game_id": f"{s}_{w}",
                "season": s,
                "week": w,
                "pgroup": "WR",
                "played_off": True,
                "played_def": False,
                "main_qb": False,
                "receiving_yards": float(y),
                "offense_pct": 0.8,
                "defense_pct": None,
            }
        )
    return pl.DataFrame(recs).with_columns(
        pl.col("season", "week").cast(pl.Int32), tkey().alias("t")
    )


def test_own_features_are_strictly_before_and_season_scoped() -> None:
    h = _toy_hist()
    tgt = get_target("rec_yds")[0]
    rows = pl.DataFrame(
        {
            "player_id": ["p", "p", "p"],
            "game_id": ["2026_1", "2026_3", "2026_5"],
            "season": [2026, 2026, 2026],
            "week": [1, 3, 5],
        }
    ).with_columns(pl.col("season", "week").cast(pl.Int32), tkey().alias("t"))
    own = PF.own_features(h, rows, tgt)
    r = {g: d for g, d in zip(own["game_id"], own.iter_rows(named=True), strict=True)}
    assert r["2026_1"]["own_n_season"] == 0 and r["2026_1"]["own_std"] is None
    assert r["2026_1"]["own_ls"] == pytest.approx(20.0)  # last season's mean
    assert r["2026_3"]["own_std"] == pytest.approx(45.0)  # weeks 1-2 only
    assert r["2026_3"]["own_l1"] == pytest.approx(50.0)
    assert r["2026_5"]["own_l4_season"] == pytest.approx(55.0)
    assert r["2026_5"]["own_n_career"] == 7


def test_rolling_baseline_formula() -> None:
    frame = pl.DataFrame(
        {
            "season": [2026, 2026, 2026, 2026],
            "own_n_season": [0.0, 4.0, 0.0, 1.0],
            "own_l4_season": [None, 55.0, None, 30.0],
            "own_std": [None, 55.0, None, 30.0],
            "own_ls": [20.0, 20.0, None, None],
            "own_n_career": [3.0, 7.0, 0.0, 1.0],
            "own_ewm": [25.0, 50.0, None, 30.0],
            "use_snap_l1": [0.8, 0.8, None, 0.9],
        }
    ).with_columns(pl.col("season").cast(pl.Int32))
    roles = pl.DataFrame(
        {"snap_bucket": ["none", "80-100"], "role_avg": [12.0, 40.0], "season": [2026, 2026]}
    ).with_columns(pl.col("season").cast(pl.Int32))
    b = PF.baselines(frame, roles)
    assert b["baseline"][0] == pytest.approx(20.0)  # no game this season: last season
    assert b["baseline_source"][0] == "last_season"
    assert b["baseline"][1] == pytest.approx((4 * 55 + PF.K_LAST * 20) / (4 + PF.K_LAST))
    assert b["baseline"][2] == pytest.approx(12.0)  # no history: role average
    assert b["baseline_source"][2] == "role"
    assert b["baseline"][3] == pytest.approx((1 * 30 + 2 * 40) / 3)  # thin: blended with role


# ---- leakage: every family I own -------------------------------------------------------------


def _scramble_world(inp, cut: int):
    inp2 = scrambled(inp, cut)
    later = scrambled(inp2, cut + 1, frames=SAME_WEEK_FRAMES)
    return later


def test_core_families_future_invariance(world) -> None:
    inp, hist, rows = world
    base = _mine(inp, hist, rows)
    cols = _feature_cols(base)
    for cut in (tk(2021, 6), tk(2022, 3), tk(2022, 5)):
        inp2 = _scramble_world(inp, cut)
        out = _mine(inp2, player_history(inp2), rows)
        before = rows["t"] <= cut
        assert before.sum() > 50
        assert_same(base.filter(before), out.filter(before), cols)
        after = (rows["t"] > cut) & (rows["t"] <= tk(2022, 6))
        assert changed_columns(base.filter(after), out.filter(after), cols), "scramble had no teeth"


@pytest.mark.parametrize("key", ["rec_yds-wrte", "tackles-lbs", "pass_yds-qb", "pressures-edge"])
def test_target_frame_future_invariance(world, key) -> None:
    inp, hist, rows = world
    tgt = next(t for t in TARGETS if t.key == key)
    tgt = replace(tgt, start_season=2021)
    feats = _mine(inp, hist, rows)
    base = PF.target_frame(feats, hist, tgt)
    cut = tk(2022, 4)
    inp2 = _scramble_world(inp, cut)
    hist2 = player_history(inp2)
    out = PF.target_frame(_mine(inp2, hist2, rows), hist2, tgt)
    cols = [c for c in base.columns if c.startswith("own_")] + ["baseline", "baseline_role"]
    # pool membership of a cut-week row is part of its label (did he play / start?), so
    # the rows before the cut must match exactly and the cut week's shared rows must too
    b = base.filter(pl.col("t") < cut).sort("player_id", "game_id")
    o = out.filter(pl.col("t") < cut).sort("player_id", "game_id")
    assert b.height > 20 and b.height == o.height
    assert_same(b, o, cols)
    keys = ["player_id", "game_id"]
    at_b = base.filter(pl.col("t") == cut)
    at_o = out.filter(pl.col("t") == cut).join(at_b.select(keys), on=keys).sort(keys)
    at_b = at_b.join(at_o.select(keys), on=keys).sort(keys)
    assert_same(at_b, at_o, cols)


def test_target_frame_pools_and_upcoming_rows(world) -> None:
    inp, hist, rows = world
    feats = _mine(inp, hist, rows)
    qb = PF.target_frame(feats, hist, replace(get_target("pass_yds")[0], start_season=2021))
    played = qb.filter(pl.col("y").is_not_null())
    assert played.height and played["pgroup"].unique().to_list() == ["QB"]
    # unplayed weeks (2022 w7-8) are kept as rows to predict, without a label
    up = qb.filter(pl.col("t") >= tk(2022, 7))
    assert up.height and up["y"].null_count() == up.height


def test_availability_reads_its_own_week_report(world) -> None:
    inp, hist, rows = world
    av = PF.availability_features(inp, hist, rows).join(
        rows.select("player_id", "game_id", "season", "week"), on=["player_id", "game_id"]
    )
    rep = inp.injuries.filter(pl.col("report_status") == "Out").select(
        pl.col("gsis_id").alias("player_id"), "season", "week"
    )
    hit = av.join(rep, on=["player_id", "season", "week"])
    assert hit.height and (hit["avail_status"] == 3.0).all()


def test_upcoming_rows_drop_out_players_and_keep_expected_qb(world) -> None:
    inp, hist, _ = world
    season, week = 2022, 7
    qbs = pl.DataFrame({"team": ["ARI"], "qb_id": ["ARI_QB"]})
    up = PF.upcoming_rows(inp, hist, season, week, qbs)
    out = PF.unavailable(inp, season, week)
    assert not set(up["player_id"]) & out
    assert up.filter(pl.col("pgroup") == "QB")["player_id"].to_list() == ["ARI_QB"]
    assert (up["t"] == tk(season, week)).all()


# ---- model -----------------------------------------------------------------------------------


def test_nb_dispersion_and_quantiles() -> None:
    rng = np.random.default_rng(0)
    mu = np.full(20000, 4.0)
    y = rng.negative_binomial(2.0, 2.0 / (2.0 + 4.0), size=mu.size).astype(float)
    r = M.nb_dispersion(y, mu)
    assert 1.6 < r < 2.4
    q = M.count_quantiles(mu[:3], r)
    assert q.shape == (3, 3) and (q[:, 0] <= q[:, 1]).all() and (q[:, 1] <= q[:, 2]).all()
    # Poisson data: no overdispersion -> Poisson
    yp = rng.poisson(4.0, size=mu.size).astype(float)
    assert M.nb_dispersion(yp, mu) > 20


def test_count_tail_targets_eighty_percent() -> None:
    rng = np.random.default_rng(1)
    mu = rng.uniform(1, 6, 20000)
    y = rng.poisson(mu).astype(float)
    a = M.count_tail(y, mu, M.MAX_DISPERSION)
    q = M.count_quantiles(mu, M.MAX_DISPERSION, (a, 1 - a))
    cov = ((y >= q[:, 0]) & (y <= q[:, 1])).mean()
    assert abs(cov - 0.8) < 0.05


def test_conformal_shift_reaches_coverage() -> None:
    rng = np.random.default_rng(2)
    y = rng.normal(0, 10, 5000)
    lo, hi = np.full(y.size, -5.0), np.full(y.size, 5.0)  # far too narrow
    s = M.conformal_shift(lo, hi, y)
    cov = ((y >= lo - s) & (y <= hi + s)).mean()
    assert s > 0 and abs(cov - 0.8) < 0.02


def _toy_frame(n_seasons: int = 3, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    recs = []
    for s in range(2020, 2020 + n_seasons):
        for w in range(1, 9):
            for i in range(60):
                x = rng.normal()
                recs.append(
                    {
                        "season": s,
                        "week": w,
                        "player_id": f"p{i}",
                        "game_id": f"{s}_{w}_{i}",
                        "own_l4": x,
                        "use_snap_l1": rng.uniform(),
                        "baseline": 5 + 2 * x,
                        "y": float(max(0, rng.poisson(max(0.1, 5 + 2 * x)))),
                    }
                )
    return pl.DataFrame(recs).with_columns(pl.col("season", "week").cast(pl.Int32))


@pytest.mark.parametrize("key", ["rec_yds-wrte", "tackles-lbs"])
def test_week_model_runs_in_the_harness(key) -> None:
    tgt = next(t for t in TARGETS if t.key == key)
    frame = _toy_frame()
    cfg = M.PlayerModelConfig(n_estimators=30, min_data_in_leaf=20, num_threads=1)
    model = M.PlayerWeekModel(tgt, ["own_l4", "use_snap_l1"], cfg, explain=True)
    keys = [AsOf(2022, w) for w in range(1, 9)]
    out = walk_forward(frame, keys, model, label="y", weights=SampleWeights(), min_train_rows=100)
    assert out.height == 8 * 60
    assert (out["p10"] <= out["p50"]).all() and (out["p50"] <= out["p90"]).all()
    assert len(out["_contrib"][0]) == 2
    if tgt.kind == "count":
        assert (out["p50"] == out["p50"].round()).all()


def test_walk_forward_history_seed_must_be_before_the_keys() -> None:
    frame = _toy_frame(2)
    tgt = get_target("tackles")[0]
    cfg = M.PlayerModelConfig(n_estimators=10, min_data_in_leaf=20, num_threads=1)
    model = M.PlayerWeekModel(tgt, ["own_l4"], cfg)
    late = pl.DataFrame({"season": [2021], "week": [5], "y": [1.0], "mean": [1.0]}).with_columns(
        pl.col("season", "week").cast(pl.Int32)
    )
    with pytest.raises(LeakageError):
        walk_forward(frame, [AsOf(2021, 3)], model, label="y", history=late, min_train_rows=10)


# ---- run helpers ----------------------------------------------------------------------------


def _preds(n: int = 6) -> pl.DataFrame:
    df = pl.DataFrame(
        {
            "season": [2025] * n,
            "week": [3] * n,
            "game_id": [f"g{i % 2}" for i in range(n)],
            "player_id": [f"p{i}" for i in range(n)],
            "target": ["rec_yds"] * n,
            "group": ["WR/TE"] * n,
            "target_label": ["receiving yards"] * n,
            "p10": [0.0] * n,
            "p50": [50.0] * n,
            "p90": [100.0] * n,
            "baseline": [40.0] * n,
            "baseline_p50": [40.0] * n,
            "actual": [60.0, 30.0, 120.0, None, 55.0, 45.0][:n],
            "played": [True, True, True, False, True, True][:n],
        }
    )
    return conform(df)


def test_scoreboard_rows_and_upsert(tmp_path: Path) -> None:
    rows = R.scoreboard_rows(_preds(), "backtest")
    assert rows.columns == list(SCOREBOARD_SCHEMA)
    r = rows.row(0, named=True)
    assert r["n_scored"] == 5 and r["n_not_played"] == 1
    assert r["mae_model"] == pytest.approx((10 + 20 + 70 + 5 + 5) / 5)
    assert r["mae_baseline"] == pytest.approx((20 + 10 + 80 + 15 + 5) / 5)
    assert r["coverage_80"] == pytest.approx(4 / 5)
    assert r["improvement_pct"] == pytest.approx(100 * (26 - 22) / 26)
    path = tmp_path / "sb.parquet"
    R.upsert_scoreboard(path, rows)
    again = R.upsert_scoreboard(path, rows)
    assert again.height == 1  # same key replaced, not duplicated


def test_tuesdays_are_tuesday_1400_utc() -> None:
    games = pl.DataFrame(
        {
            "season": [2025, 2025],
            "week": [5, 5],
            "kickoff_utc": [
                dt.datetime(2025, 10, 3, 0, 15, tzinfo=dt.UTC),  # Thursday 20:15 ET
                dt.datetime(2025, 10, 5, 17, 0, tzinfo=dt.UTC),
            ],
        }
    )
    t = R.tuesdays(games)["created_at"][0]
    assert t == dt.datetime(2025, 9, 30, 14, 0, tzinfo=dt.UTC)


def test_keep_started_players_keeps_pre_kickoff_rows(tmp_path: Path) -> None:
    now = dt.datetime(2026, 10, 4, 18, 0, tzinfo=dt.UTC)
    old = conform(
        pl.DataFrame(
            {
                "game_id": ["g1", "g2"],
                "player_id": ["a", "b"],
                "p50": [1.0, 2.0],
                "kickoff_utc": [now - dt.timedelta(hours=1), now + dt.timedelta(hours=3)],
                "created_at": [now - dt.timedelta(days=1)] * 2,
            }
        )
    )
    path = tmp_path / "p.parquet"
    old.write_parquet(path)
    new = conform(
        pl.DataFrame(
            {
                "game_id": ["g1", "g2"],
                "player_id": ["a", "b"],
                "p50": [9.0, 9.0],
                "kickoff_utc": old["kickoff_utc"],
                "created_at": [now, now],
            }
        )
    )
    out, kept = R.keep_started_players(new, path, now)
    assert kept == 1
    assert dict(zip(out["game_id"], out["p50"], strict=True)) == {"g1": 1.0, "g2": 9.0}


def test_score_predictions_fills_actual_only_for_pool_games(world) -> None:
    _, hist, _ = world
    played = hist.filter(pool_expr("WR/TE")).head(3)
    preds = conform(
        played.select("player_id", "game_id", "season", "week").with_columns(
            pl.lit("rec_yds").alias("target"), pl.lit("WR/TE").alias("group")
        )
    )
    extra = conform(
        pl.DataFrame(
            {
                "player_id": ["nobody"],
                "game_id": [played["game_id"][0]],
                "target": ["rec_yds"],
                "group": ["WR/TE"],
            }
        )
    )
    out = score_predictions(pl.concat([preds, extra]), hist)
    assert out["played"].to_list() == [True, True, True, False]
    assert out["actual"].to_list()[:3] == played["receiving_yards"].to_list()
    assert out["actual"][3] is None


def test_summarize_reports_improvement_and_rank_correlation() -> None:
    p = _preds().with_columns(
        pl.Series("outperformance", [10.0, -10.0, 30.0, 0.0, 5.0, 2.0]),
        pl.lit("rolling").alias("baseline_source"),
    )
    s = R.summarize(p)
    assert s["n_scored"] == 5 and s["improvement_pct"] == pytest.approx(100 * 4 / 26)


def test_pred_schema_conform_keeps_columns() -> None:
    out = conform(pl.DataFrame({"player_id": ["a"], "p50": [1]}))
    assert out.columns == list(PRED_SCHEMA) and out["p50"].dtype == pl.Float64


def test_weekly_player_step_degrades_on_a_failed_refit(monkeypatch, tmp_path) -> None:
    """A failed refit degrades the step and records it, so the digest won't read an older
    projection file of the same week (Sol review)."""
    import nflengine.models.player_runs as runs
    import nflengine.models.player_schema as schema
    from nflengine import weekly

    scored: list = []
    monkeypatch.setattr(
        runs,
        "score_weeks",
        lambda season, weeks, *a, **k: (
            scored.append(list(weeks)) or pl.DataFrame(schema=SCOREBOARD_SCHEMA)
        ),
    )
    written: dict = {}
    monkeypatch.setattr(
        schema, "write_status", lambda run_dir, status, detail="": written.update(status=status)
    )

    def boom(*a, **k):
        raise RuntimeError("no rows")

    monkeypatch.setattr(runs, "run_train", boom)
    with pytest.raises(weekly.StepDegraded) as e:
        weekly._player(weekly.WeeklyOptions(2026, 5), log=lambda *_: None)
    assert "heuristic watch list" in e.value.detail
    assert written == {"status": "degraded"}
    assert scored == [[1, 2, 3, 4]]  # every earlier week is re-scored (late PFR pressures)


def test_player_status_roundtrip(tmp_path) -> None:
    from nflengine.models.player_schema import read_status, write_status

    assert read_status(tmp_path) is None
    write_status(tmp_path, "degraded", "RuntimeError")
    assert read_status(tmp_path) == "degraded"
    write_status(tmp_path, "ok")
    assert read_status(tmp_path) == "ok"


def test_tuning_respects_the_label_lag(monkeypatch) -> None:
    """Sol review: the tuning objective must not train on a week-late label either."""
    import nflengine.models.player_runs as runs

    seen: list[int] = []
    real = runs.fit_player_model

    def spy(train, weights, target, features, config, **k):
        last = train.sort("season", "week").row(-1, named=True)
        seen.append((last["season"] - 2000) * 22 + last["week"])
        return real(train, weights, target, features, config, **k)

    monkeypatch.setattr(runs, "fit_player_model", spy)
    monkeypatch.setattr(runs, "TUNE_SEASONS", (2022,))
    monkeypatch.setattr(runs, "MIN_TRAIN_ROWS", 10)
    tgt = get_target("pressures")[0]
    frame = _toy_frame()
    cfg = M.PlayerModelConfig(n_estimators=5, min_data_in_leaf=20, num_threads=1)
    runs.tune_objective(frame, tgt, ["own_l4"], cfg)
    keys = [(2022 - 2000) * 22 + w for w in range(1, 9) if w % 2 == 1]
    assert seen and all(t < k - 1 for t, k in zip(seen, keys, strict=False))


def test_open_usage_counts_each_absent_teammate_once(world) -> None:
    """Sol review: a teammate who missed the last game AND is ruled out vacates his share
    once (`open_*`), while the two model features each count him. (The synthetic league
    has carry shares but no target shares, so the absent player is the running back.)"""
    inp, hist, rows = world
    season, week = 2022, 6
    t_now = tk(season, week)
    last = (
        hist.filter((pl.col("team") == "ARI") & (pl.col("t") < t_now))
        .select("game_id", "t")
        .unique()
        .sort("t", descending=True)["game_id"][0]
    )
    h = hist.filter(~((pl.col("player_id") == "ARI_RB") & (pl.col("game_id") == last)))
    inj = inp.injuries.filter(
        ~((pl.col("gsis_id") == "ARI_RB") & (pl.col("season") == season) & (pl.col("week") == week))
    )
    inj = pl.concat(
        [
            inj,
            inj.head(1).with_columns(
                pl.lit("ARI_RB").alias("gsis_id"),
                pl.lit("ARI").alias("team"),
                pl.lit(season).cast(pl.Int32).alias("season"),
                pl.lit(week).cast(pl.Int32).alias("week"),
                pl.lit("Out").alias("report_status"),
            ),
        ]
    )
    inp2 = replace(inp, injuries=inj)
    eq = PF.expected_qbs_from_games(inp2.game_features)
    r = rows.filter((pl.col("team") == "ARI") & (pl.col("t") == t_now))
    rip = PF.ripple_features(inp2, h, r, eq).join(
        r.select("player_id", "game_id"), on=["player_id", "game_id"]
    )
    mate = rip.filter(pl.col("player_id") == "ARI_WR2").row(0, named=True)
    assert mate["rip_vacated_car"] > 0 and mate["rip_out_car"] > 0
    assert mate["open_car"] == pytest.approx(mate["rip_vacated_car"])  # once, not twice
    assert "open_car" not in M.feature_columns(rip)  # not a model feature


def test_every_feature_has_a_readable_phrase(world) -> None:
    """Drivers in the digest use `features/descriptions.yaml`; a missing phrase would show a
    raw column name."""
    from nflengine.features.player_efficiency import efficiency_features
    from nflengine.features.player_opponent import opponent_features

    inp, hist, rows = world
    feats = _mine(inp, hist, rows)
    feats = feats.join(efficiency_features(inp, hist, rows), on=["player_id", "game_id"])
    feats = feats.join(opponent_features(inp, hist, rows), on=["player_id", "game_id"])
    tf = M.add_position_flags(
        PF.target_frame(feats, hist, replace(get_target("rec_yds")[0], start_season=2021))
    )
    phrases = R.load_phrases()
    missing = [c for c in M.feature_columns(tf) if c not in phrases]
    assert not missing, missing
    assert not any(ch.isdigit() for p in phrases.values() for ch in p), "phrases carry no numbers"


def test_conform_empty_and_score_adds_result_columns(world) -> None:
    assert conform(pl.DataFrame()).height == 0
    _, hist, _ = world
    row = hist.filter(pool_expr("LB/S")).head(1)
    bare = row.select("player_id", "game_id").with_columns(
        pl.lit("tackles").alias("target"), pl.lit("LB/S").alias("group")
    )
    out = score_predictions(bare, hist)
    assert out["played"].to_list() == [True]
    assert out["actual"].to_list() == row["tackles"].to_list()


def test_history_is_unique_per_player_week_with_ids(world) -> None:
    _, hist, _ = world
    assert hist["player_id"].null_count() == 0
    assert hist.group_by("player_id", "t").len()["len"].max() == 1


def test_count_baseline_is_scored_as_a_median() -> None:
    """D65: a count's P50 is a median, so the baseline it is scored against is the median of
    the same distribution at the baseline mean (a median beats a mean on MAE by itself)."""
    tgt = get_target("pressures")[0]
    b = pl.Series([0.4, 0.7, 2.6, None])
    med = M.baseline_p50(b, tgt, M.MAX_DISPERSION).to_list()
    assert med[:3] == [0.0, 1.0, 2.0] and med[3] is None  # Poisson(0.7): P(0) < 0.5
    amt = get_target("rec_yds")[0]
    assert M.baseline_p50(b, amt, 1.0).to_list() == b.to_list()


def test_week_late_label_is_not_trained_on() -> None:
    """Pressures (PFR) publish a week late: at key N the model must not see week N-1 labels."""
    tgt = get_target("pressures")[0]
    assert tgt.label_lag == 1
    seen: dict[str, int] = {}

    class Spy(M.PlayerWeekModel):
        pass

    def fake_fit(train, weights, target, features, config, history=None, **k):
        seen["max_week"] = int(train.filter(pl.col("season") == 2022)["week"].max())
        seen["n"] = train.height
        assert weights.shape[0] == train.height
        return M.fit_player_model.__wrapped__(train, weights, target, features, config, history)

    import nflengine.models.player_model as pm

    real = pm.fit_player_model
    fake_fit.__wrapped__ = real  # type: ignore[attr-defined]
    pm.fit_player_model = fake_fit
    try:
        frame = _toy_frame()
        cfg = M.PlayerModelConfig(n_estimators=10, min_data_in_leaf=20, num_threads=1)
        model = Spy(tgt, ["own_l4"], cfg)
        walk_forward(frame, [AsOf(2022, 5)], model, label="y", min_train_rows=10)
    finally:
        pm.fit_player_model = real
    assert seen["max_week"] == 3  # week 4 (the latest) dropped at the week-5 key


def test_upcoming_rows_bring_back_a_regular_from_ir(world) -> None:
    inp, hist, _ = world
    season, week = 2022, 7
    # ARI_TE missed ARI's last 3 games (drop him from those) but is on the active roster
    t_now = tk(season, week)
    last3 = (
        hist.filter((pl.col("team") == "ARI") & (pl.col("t") < t_now))
        .select("game_id", "t")
        .unique()
        .sort("t", descending=True)
        .head(3)["game_id"]
        .to_list()
    )
    h = hist.filter(~((pl.col("player_id") == "ARI_TE") & pl.col("game_id").is_in(last3)))
    qbs = pl.DataFrame({"team": ["ARI"], "qb_id": ["ARI_QB"]})
    without = PF.upcoming_rows(inp, h, season, week, qbs)
    assert "ARI_TE" not in without["player_id"].to_list()
    roster = pl.concat(
        [
            inp.rosters,
            inp.rosters.filter((pl.col("season") == season) & (pl.col("week") == week))
            .head(1)
            .with_columns(pl.lit("ARI_TE").alias("gsis_id"), pl.lit("ARI").alias("team")),
        ]
    )
    back = PF.upcoming_rows(
        inp.__class__(**{**inp.__dict__, "rosters": roster}), h, season, week, qbs
    )
    assert "ARI_TE" in back["player_id"].to_list()
