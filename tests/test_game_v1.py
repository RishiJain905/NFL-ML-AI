"""Game model v1 (P08): the extra features (injury load, weather, trailing home edge) and
the LightGBM model (monotone, boosted from v0, v0 carried on the same rows)."""

import datetime as dt

import numpy as np
import polars as pl
import pytest

from nflengine.features.game_extra import (
    INJURY_COLS,
    game_weather,
    position_group,
    team_injury_load,
    trailing_home_edge,
)
from nflengine.models.backtest import SampleWeights, walk_forward, week_keys
from nflengine.models.game_model import GameModelConfig, GameWeekModel
from nflengine.models.game_model_v1 import (
    GameModelV1Config,
    GameWeekModelV1,
    feature_hash,
    fit_game_model_v1,
)
from nflengine.models.game_runs import (
    backtest_label,
    config_for,
    paired_bootstrap,
    pooled_summary,
    probs_of,
)

# ---- injury load -------------------------------------------------------------------------------


def _games(n: int = 6, season: int = 2024) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": [f"g{k}" for k in range(1, n + 1)],
            "season": [season] * n,
            "week": list(range(1, n + 1)),
            "home_team": ["AAA"] * n,
            "away_team": [f"O{k}" for k in range(1, n + 1)],
        }
    )


def _snap(pid: str, game: str, pos: str, off: float = 0.0, de: float = 0.0) -> dict:
    return {
        "gsis_id": pid,
        "game_id": game,
        "team": "AAA",
        "position": pos,
        "offense_pct": off,
        "defense_pct": de,
    }


def _load(snaps: list[dict], games: pl.DataFrame) -> pl.DataFrame:
    out = team_injury_load(pl.DataFrame(snaps), games)
    return out.filter(pl.col("team") == "AAA").sort("week")


def test_position_group_maps_combined_labels():
    df = pl.DataFrame({"p": ["FB/D", "OT", "cb", "QB", "XX", "OLB"]})
    got = df.select(position_group(pl.col("p")))["p"].to_list()
    assert got == ["FB", "OL", "CB", "QB", None, "LB"]


def test_injury_load_counts_a_regular_missing_from_the_last_game():
    g = _games()
    snaps = []
    for k in (1, 2, 3, 5):  # the tackle misses game 4
        snaps.append(_snap("ol1", f"g{k}", "T", off=0.9))
    for k in range(1, 7):
        snaps.append(_snap("wr1", f"g{k}", "WR", off=0.8))
    load = _load(snaps, g)
    by_week = dict(zip(load["week"].to_list(), load["inj_ol"].to_list(), strict=True))
    assert by_week[5] == pytest.approx(0.9)  # absent in game 4 -> load for game 5
    assert by_week[4] == 0.0 and by_week[6] == 0.0
    assert load["inj_skill"].sum() == 0.0
    assert by_week[1] == 0.0 and by_week[2] == 0.0  # no history yet


def test_injury_load_needs_two_regular_games_and_a_recent_one():
    g = _games(8)
    snaps = [_snap("lb1", "g1", "LB", de=0.9)]  # one regular game, then gone
    # a corner who was a regular in games 1-2, a backup (20%) in 3-5, absent from 6
    snaps += [_snap("cb1", f"g{k}", "CB", de=0.95) for k in (1, 2)]
    snaps += [_snap("cb1", f"g{k}", "CB", de=0.2) for k in (3, 4, 5)]
    load = _load(snaps, g)
    assert load["inj_front"].sum() == 0.0  # never a regular
    w = dict(zip(load["week"].to_list(), load["inj_secondary"].to_list(), strict=True))
    assert w[4] == 0.0  # still playing (20%) in game 3: not absent
    assert w[7] == 0.0  # absent in game 6, but not a regular in games 3-5 any more


def test_injury_load_ignores_qbs_and_uses_the_side_share():
    g = _games()
    snaps = [_snap("qb1", f"g{k}", "QB", off=1.0) for k in (1, 2, 3)]
    snaps += [_snap("de1", f"g{k}", "DE", off=0.9, de=0.0) for k in (1, 2, 3)]  # offense only
    load = _load(snaps, g)
    assert load.select(INJURY_COLS).to_numpy().sum() == 0.0


def test_injury_load_is_future_invariant():
    g = _games(8)
    rng = np.random.default_rng(0)
    snaps = [
        _snap(f"p{i}", f"g{k}", pos, off=float(rng.uniform(0.3, 1)), de=float(rng.uniform(0.3, 1)))
        for i, pos in enumerate(["T", "G", "WR", "TE", "DE", "LB", "CB", "S"])
        for k in range(1, 9)
        if rng.uniform() > 0.2
    ]
    base = _load(snaps, g)
    later = [s for s in snaps if s["game_id"] not in ("g6", "g7", "g8")]  # drop future games
    cut = _load(later, g)
    a = base.filter(pl.col("week") <= 6).select(INJURY_COLS)
    b = cut.filter(pl.col("week") <= 6).select(INJURY_COLS)
    assert a.equals(b)  # a game's load only reads games before it


# ---- weather -----------------------------------------------------------------------------------


def test_weather_buckets_actual_vs_forecast():
    games = pl.DataFrame(
        {
            "game_id": ["dome", "windy", "calm", "unknown", "future"],
            "roof": ["dome", "outdoors", "outdoors", "outdoors", "outdoors"],
            "completed": [True, True, True, True, False],
            "wind": [None, 18.0, 5.0, None, None],
            "temp": [None, 20.0, 60.0, None, None],
        }
    )
    fc = pl.DataFrame(
        {
            "game_id": ["future", "future", "windy"],
            "wind_mph": [3.0, 16.0, 1.0],
            "temp_f": [50.0, 30.0, 70.0],
            "pulled_at": [
                dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
                dt.datetime(2026, 10, 2, tzinfo=dt.UTC),
                dt.datetime(2026, 10, 2, tzinfo=dt.UTC),
            ],
        }
    )
    w = game_weather(games, fc).sort("game_id")
    got = {r["game_id"]: (r["wind_15"], r["cold_32"]) for r in w.iter_rows(named=True)}
    assert got["dome"] == (0.0, 0.0)
    assert got["windy"] == (1.0, 1.0)  # played: the actual reading, never the forecast
    assert got["calm"] == (0.0, 0.0)
    assert got["unknown"] == (None, None)
    assert got["future"] == (1.0, 1.0)  # unplayed: the latest forecast


def test_trailing_home_edge_uses_only_earlier_seasons():
    rows = []
    for s, m in ((2020, 4), (2021, 2), (2022, 0), (2023, 10)):
        rows.append((s, "REG", False, 20 + m, 20, True))
        rows.append((s, "REG", True, 40, 0, True))  # neutral: left out
    games = pl.DataFrame(
        rows,
        schema=["season", "game_type", "neutral_site", "home_score", "away_score", "completed"],
        orient="row",
    )
    e = dict(trailing_home_edge(games, seasons=3).iter_rows())
    assert 2020 not in e
    assert e[2021] == pytest.approx(4.0)
    assert e[2023] == pytest.approx((4 + 2 + 0) / 3)  # 2023's own games never count


# ---- the v1 model ------------------------------------------------------------------------------


def synth(n_seasons: int = 6, per_week: int = 14, weeks: int = 17, seed: int = 5) -> pl.DataFrame:
    """v0's synthetic league plus the v1 columns; an injury edge adds 3 points per unit."""
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(2010, 2010 + n_seasons):
        for w in range(1, weeks + 1):
            for i in range(per_week):
                net = rng.normal(0, 0.12)
                elo = net * 3 + rng.normal(0, 0.3)
                inj = float(rng.normal(0, 0.8))
                hfa = 0.0 if i == 0 else 1.0
                off = rng.normal(0, 0.1)
                mean_margin = 40 * net + 2.5 * hfa + 3 * inj
                margin = float(np.round(mean_margin + rng.normal(0, 13)))
                total = float(max(3, np.round(44 + 60 * off + rng.normal(0, 10))))
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
                        "qb_adj_diff": 0.0,
                        "inj_off_edge": inj,
                        "inj_def_edge": 0.0,
                        "off_sum": off,
                        "def_sum": 0.0,
                        "qb_adj_sum": 0.0,
                        "league_ppg": 22.0,
                        "pts_base_total": 44.0,
                        "roof_dome": 0.0,
                        "spread_line": float(np.round(mean_margin * 2) / 2),
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
                        "mkt_home_points": 22.0,
                        "mkt_away_points": 22.0,
                    }
                )
    return pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


V0 = GameModelConfig(
    margin_features=("net_diff", "elo_diff", "hfa"),
    total_features=("off_sum", "league_ppg"),
    weights=SampleWeights(1, 1, 1),
)
MF = ("net_diff", "elo_diff", "hfa", "inj_off_edge")
TF = ("off_sum", "league_ppg", "def_sum")


def _v1(**kw) -> GameModelV1Config:
    base = {
        "margin_features": MF,
        "total_features": TF,
        "weights": SampleWeights(1, 1, 1),
        "v0": V0,
        "n_estimators": 80,
        "min_data_in_leaf": 50,
        "num_threads": 1,
    }
    return GameModelV1Config(**{**base, **kw})


def _split(df: pl.DataFrame, season: int = 2015):
    return df.filter(pl.col("season") < season), df.filter(pl.col("season") == season)


def test_v1_predicts_v0_columns_and_consistent_scores():
    train, test = _split(synth())
    fit = fit_game_model_v1(train, np.ones(train.height), _v1())
    p = fit.predict(test)
    for c in ("home_win_prob", "expected_margin", "pred_total", "v0_prob", "v0_margin"):
        assert p[c].null_count() == 0, c
    gap = (p["pred_home_points"] - p["pred_away_points"] - p["expected_margin"]).abs().max()
    assert gap < 1e-9
    assert p["sigma"][0] == pytest.approx(13.5)  # no history yet: the default


def test_v1_without_trees_is_v0():
    train, test = _split(synth())
    fit = fit_game_model_v1(train, np.ones(train.height), _v1(n_estimators=0))
    p = fit.predict(test)
    assert (p["expected_margin"] - p["v0_margin"]).abs().max() < 1e-9


def test_v1_learns_a_signal_v0_leaves_out():
    df = synth(n_seasons=8, per_week=16)
    train, test = _split(df, 2017)
    fit = fit_game_model_v1(train, np.ones(train.height), _v1(n_estimators=300))
    p = fit.predict(test)
    y = test["margin"].to_numpy()
    mae1 = np.mean(np.abs(y - p["expected_margin"].to_numpy()))
    mae0 = np.mean(np.abs(y - p["v0_margin"].to_numpy()))
    assert mae1 < mae0  # the injury edge is real in this league and v0 can't see it


def test_v1_margin_is_monotone_in_net_diff_and_injury_edge():
    train, test = _split(synth())
    fit = fit_game_model_v1(train, np.ones(train.height), _v1(n_estimators=200))
    row = test.head(1)
    for feat in ("net_diff", "inj_off_edge"):
        grid = np.linspace(-1.5, 1.5, 25) * (0.12 if feat == "net_diff" else 1.0)
        frames = pl.concat([row.with_columns(pl.lit(float(v)).alias(feat)) for v in grid])
        m = fit.predict(frames)["expected_margin"].to_numpy()
        assert np.all(np.diff(m) >= -1e-9), feat


def test_v1_from_zero_base_boosts_from_the_average():
    train, test = _split(synth())
    fit = fit_game_model_v1(train, np.ones(train.height), _v1(base="none", n_estimators=200))
    p = fit.predict(test)
    assert abs(p["pred_total"].mean() - test["total"].mean()) < 5  # not stuck near 0


def test_walk_forward_v1_carries_v0_exactly():
    df = synth()
    keys = week_keys(df, [2014, 2015])
    w = SampleWeights(1, 1, 1)
    v1 = walk_forward(df, keys, GameWeekModelV1(_v1()), label=["margin", "total", "home_win"],
                      weights=w, min_train_rows=200)  # fmt: skip
    v0 = walk_forward(df, keys, GameWeekModel(V0), label=["margin", "total", "home_win"],
                      weights=w, min_train_rows=200)  # fmt: skip
    j = v1.select("game_id", "v0_prob", "v0_margin").join(
        v0.select("game_id", "home_win_prob", "expected_margin"), on="game_id"
    )
    assert j.height == v0.height
    assert (j["v0_prob"] - j["home_win_prob"]).abs().max() < 1e-9  # same sigma history too
    assert (j["v0_margin"] - j["expected_margin"]).abs().max() < 1e-9
    s = pooled_summary(v1.filter(pl.col("season") == 2015))
    assert "brier_v0" in s and "brier_gain_vs_v0" in s and "beats_v0" in s
    b = paired_bootstrap(v1.filter(pl.col("season") == 2015), draws=200)
    assert b["brier_diff_vs_v0_lo95"] <= b["brier_diff_vs_v0"] <= b["brier_diff_vs_v0_hi95"]


def test_market_variant_needs_the_spread():
    df = synth()
    df = df.with_columns(
        pl.when(pl.col("game_id").str.ends_with("_3")).then(None).otherwise(pl.col("spread_line"))
        .alias("spread_line")
    )  # fmt: skip
    train, test = _split(df)
    cfg = _v1(variant="market", v0=GameModelConfig(
        variant="market", margin_features=("net_diff", "elo_diff", "hfa", "spread_line"),
        total_features=("off_sum", "league_ppg", "total_line"), weights=SampleWeights(1, 1, 1),
    ))  # fmt: skip
    assert "spread_line" in cfg.mfeats and "total_line" in cfg.tfeats
    fit = fit_game_model_v1(
        train.drop_nulls("spread_line"), np.ones(train.drop_nulls("spread_line").height), cfg
    )  # noqa: E501
    p = fit.predict(test)
    missing = p.filter(pl.col("game_id").str.ends_with("_3"))
    assert missing["expected_margin"].null_count() == missing.height  # no line, no market row


def test_config_rejects_unknown_settings_and_bad_values():
    with pytest.raises(ValueError, match="unknown"):
        GameModelV1Config.from_config({"leaves": 3})
    with pytest.raises(ValueError):
        GameModelV1Config(base="trees")
    with pytest.raises(ValueError):
        GameModelV1Config(calibration="magic")
    a, b = _v1(), _v1(n_estimators=10)
    assert feature_hash(a) == feature_hash(b)  # settings aren't features
    assert feature_hash(a) != feature_hash(_v1(margin_features=("net_diff", "hfa")))


def test_runs_helpers_for_versions():
    assert backtest_label("model_only") == "model_only"  # the canonical v0 folder
    assert backtest_label("market", version="v1") == "v1_market"
    assert probs_of(pl.DataFrame({"v0_prob": [0.5]}))["v0"] == "v0_prob"
    assert "v0" not in probs_of(pl.DataFrame({"x": [1]}))
    assert isinstance(config_for("v1", "market"), GameModelV1Config)
    assert isinstance(config_for("v0"), GameModelConfig)
    with pytest.raises(ValueError):
        config_for("v2")
