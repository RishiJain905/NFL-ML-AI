"""Shared helpers for the live-decision unit tests (LD00): stub models, synthetic frames, hand-made
plays. A plain module, not a conftest (repo rule); the test files do `from live_stubs import ...`
(pytest puts this folder on `sys.path`). No disk, no network, no W&B."""

from __future__ import annotations

import functools
import json
import math
from contextlib import contextmanager

import numpy as np
import polars as pl

from nflengine.live import models as M
from nflengine.live import schema
from nflengine.live.decide import Engine
from nflengine.live.models import FGModel, KickoffModel, LiveModels, NextPossession, PuntModel
from nflengine.live.schema import GameState

WP_IDX = {f: i for i, f in enumerate(schema.WP_FEATURES)}


# --- random valid states ----------------------------------------------------------------------
def random_state(rng: np.random.Generator) -> GameState:
    """A state that satisfies `check_state`, over every clock regime, season and roof."""
    ot = bool(rng.random() < 0.12)
    season = int(rng.integers(2010, 2027))
    if ot:
        gs = float(rng.integers(0, schema.ot_minutes(season) * 60 + 1))
        hs = gs
    elif rng.random() < 0.5:  # first half: game = half + 1800
        gs = float(rng.integers(1801, 3601))
        hs = gs - 1800
    else:
        gs = float(rng.integers(0, 1801))
        hs = gs
    yl = int(rng.integers(1, 100))
    roofs = ["outdoors", "dome", "closed", "open", "DOME", None]
    roof = roofs[int(rng.integers(0, len(roofs)))]
    return GameState(
        season=season,
        score_diff=int(rng.integers(-30, 31)),
        game_seconds=gs,
        half_seconds=hs,
        down=int(rng.integers(1, 5)),
        ydstogo=int(rng.integers(1, min(yl, 30) + 1)),
        yardline_100=yl,
        off_timeouts=int(rng.integers(0, 4)),
        def_timeouts=int(rng.integers(0, 4)),
        home=int(rng.integers(-1, 2)),
        receive_2h_ko=int(rng.integers(0, 2)),
        spread=round(float(rng.uniform(-14, 14)), 1),
        total=round(float(rng.uniform(30, 60)), 1),
        roof=roof,
        ot=ot,
        playoffs=bool(rng.random() < 0.1),
        wind=None if rng.random() < 0.4 else float(rng.integers(0, 25)),
        temp=None if rng.random() < 0.4 else float(rng.integers(10, 90)),
    )


# --- stub models for the decision engine ------------------------------------------------------
def stub_wp_value(diff: float, gs: float, yl: float, ot: bool = False) -> float:
    """The stub wp model: a smooth logistic of the score difference (its weight grows as time
    runs out), a flat bonus for having the ball and field position (closer to the goal = better)."""
    elapsed = 1.0 if ot else (3600.0 - gs) / 3600.0
    z = diff * (0.08 + 0.5 * elapsed) + 0.5 + 0.01 * (50.0 - yl)
    return 1.0 / (1.0 + math.exp(-z))


class StubWP:
    """Duck-typed `lgb.Booster` for `wp`: `predict(X, num_threads=1)` over `WP_FEATURES` columns."""

    def predict(self, X, num_threads: int = 1, **_kw) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        diff, gs = X[:, WP_IDX["score_diff"]], X[:, WP_IDX["game_seconds"]]
        yl, ot = X[:, WP_IDX["yardline_100"]], X[:, WP_IDX["ot"]]
        elapsed = np.where(ot > 0, 1.0, (3600.0 - gs) / 3600.0)
        z = diff * (0.08 + 0.5 * elapsed) + 0.5 + 0.01 * (50.0 - yl)
        return 1.0 / (1.0 + np.exp(-z))


class StubGain:
    """Duck-typed `lgb.Booster` for `gain`: the same distribution for every row. `dist` maps a
    gain in yards (or "td") to its probability."""

    def __init__(self, dist: dict):
        self.dist = dist
        self.row = np.zeros(schema.N_GAIN_CLASSES)
        for k, p in dist.items():
            self.row[schema.TD_CLASS if k == "td" else int(k) - schema.GAIN_MIN] += p
        assert abs(self.row.sum() - 1.0) < 1e-12, "stub gain distribution must sum to 1"

    def predict(self, X, num_threads: int = 1, **_kw) -> np.ndarray:
        return np.tile(self.row, (len(X), 1))


class StubPass:
    def __init__(self, p: float = 0.55):
        self.p = p

    def predict(self, X, num_threads: int = 1, **_kw) -> np.ndarray:
        return np.full(len(X), self.p)


def stub_fg() -> FGModel:
    """P(make) = logistic(5.22 - 0.09 d): 50% at 58 yards, 84% at 40, 25% at 70."""
    return FGModel(coef=[-0.9] + [0.0] * 11, intercept=5.22, wind_fill=8.0)


def one_hot(spot: int | None, p: float = 1.0) -> np.ndarray:
    v = np.zeros(99)
    if spot is not None:
        v[spot - 1] = p
    return v


def simple_punt(net: int = 40, ret_td: float = 0.0, keep: float = 0.0) -> PuntModel:
    """A net-`net`-yard punt: the receiver starts at min(80, 100 - yl + net) yards to goal (a
    touchback is the 20); `keep` = the kicking team keeps it at that spot's mirror."""
    by = []
    for yl in range(1, 100):
        spot = min(80, 100 - yl + net)
        by.append(
            NextPossession(
                recv=one_hot(spot, 1.0 - ret_td - keep),
                ret_td=ret_td,
                keep=one_hot(100 - spot, keep),
            )
        )
    return PuntModel(by, [1.0] * 99)


def simple_kickoff(spot: int = 75, ret_td: float = 0.0, keep: float = 0.0, era: str = "tb35"):
    one = NextPossession(
        recv=one_hot(spot, 1.0 - ret_td - keep), ret_td=ret_td, keep=one_hot(60, keep)
    )
    return KickoffModel({era: one}, {era: 100})


def make_models(
    *,
    gain: dict | None = None,
    fg: FGModel | None = None,
    punt: PuntModel | None = None,
    kickoff: KickoffModel | None = None,
    pat: dict | None = None,
    boots: list[dict] | None = None,
) -> LiveModels:
    """A `LiveModels` made of stubs. `boots` = one gain distribution per bootstrap refit (the fg
    refits all reuse the main fg model, so the shared `wind_fill` rule holds)."""
    fg = fg or stub_fg()
    boots = boots or []
    return LiveModels(
        wp=StubWP(),
        gain=StubGain(gain or {0: 0.5, 5: 0.5}),
        fg=fg,
        punt=punt or simple_punt(),
        kickoff=kickoff or simple_kickoff(),
        pass_=StubPass(),
        pat=pat or {"short": {"xp": 0.99, "two": 0.48}, "long": {"xp": 0.94, "two": 0.48}},
        gain_boot=[StubGain(d) for d in boots],
        fg_boot=[fg] * len(boots),
        meta={"version": "stub"},
    )


@contextmanager
def engine(models: LiveModels, cfg: dict | None = None):
    eng = Engine(models, cfg, threads=2)
    try:
        yield eng
    finally:
        eng.close()


# --- small configs and synthetic training frames ----------------------------------------------
def small_cfg() -> dict:
    """The code defaults with tiny, single-threaded, fast models."""
    cfg = json.loads(json.dumps(M.DEFAULTS))
    cfg["wp"].update(num_leaves=7, n_estimators=40, min_data_in_leaf=30)
    cfg["gain"].update(num_leaves=4, n_estimators=3, min_data_in_leaf=30)
    cfg["pass"].update(num_leaves=7, n_estimators=20, min_data_in_leaf=30)
    cfg["punt"].update(min_effective_n=50.0)
    cfg.update(n_boot=2, threads=1, seed=7)
    return cfg


def make_wp_rows(rng: np.random.Generator, n: int, season: int = 2020) -> pl.DataFrame:
    """Synthetic wp rows: every `WP_FEATURES` column + `win`, which depends on the score
    difference (more as time runs out) and the spread (less as time runs out)."""
    gs = rng.uniform(0, 3600, n)
    first = gs > 1800
    hs = np.where(first, gs - 1800, gs)
    share = (3600.0 - gs) / 3600.0
    decay = np.exp(-4.0 * share)
    diff = rng.integers(-21, 22, n).astype(float)
    spread = np.clip(rng.normal(0, 6, n), -14, 14)
    # every term of the model's logistic base has a positive true weight (it refuses a negative
    # one on a monotone term), and the score matters more as time runs out
    z = 0.04 * diff + 0.004 * diff / decay + 0.08 * spread + 0.1 * spread * decay
    win = (rng.random(n) < 1.0 / (1.0 + np.exp(-z))).astype(int)
    return pl.DataFrame(
        {
            "score_diff": diff,
            "diff_time_ratio": diff / decay,
            "spread": spread,
            "spread_time": spread * decay,
            "game_seconds": gs,
            "half_seconds": hs,
            "yardline_100": rng.integers(1, 100, n).astype(float),
            "down": rng.integers(1, 5, n).astype(float),
            "ydstogo": rng.integers(1, 11, n).astype(float),
            "off_timeouts": rng.integers(0, 4, n).astype(float),
            "def_timeouts": rng.integers(0, 4, n).astype(float),
            "home": rng.integers(-1, 2, n).astype(float),
            "receive_2h_ko": (first & (rng.random(n) < 0.5)).astype(float),
            "ot": np.zeros(n),
            "era": np.full(n, float(schema.era_of(season))),
            "indoor": rng.integers(0, 2, n).astype(float),
            "win": win,
        }
    )


def synthetic_frames(
    seasons: list[int], n: int = 400, seed: int = 3, absurd: bool = False
) -> dict[str, pl.DataFrame]:
    """The frames `fit_all` takes (`wp`, `gain`, `fg`, `punt`, `kickoff`, `pass`, `plays`), `n`
    rows per frame per season. `absurd=True` flips every label (a "held-out season" that would
    wreck any fit it leaked into)."""
    rng = np.random.default_rng(seed)
    out: dict[str, list[pl.DataFrame]] = {k: [] for k in
        ("wp", "gain", "fg", "punt", "kickoff", "pass", "plays")}  # fmt: skip
    for season in seasons:
        era = float(schema.era_of(season))
        wp = make_wp_rows(rng, n, season)
        wp = wp.with_columns(((1 - pl.col("win")) if absurd else pl.col("win")).alias("win"))
        out["wp"].append(wp.with_columns(pl.lit(season).alias("season")))
        # pass: the wp columns + is_pass
        pas = make_wp_rows(rng, n, season).drop("win")
        is_pass = (rng.random(n) < 0.3 + 0.1 * (pas["down"].to_numpy() >= 3)).astype(int)
        pas = pas.with_columns(
            pl.Series("is_pass", 1 - is_pass if absurd else is_pass), pl.lit(season).alias("season")
        )
        out["pass"].append(pas)
        # gain: 3rd / 4th downs
        yl = rng.integers(2, 99, n)
        gain = np.clip(np.round(rng.normal(4, 6, n)), -10, 65).astype(int)
        cls = np.where(gain >= yl, schema.TD_CLASS, gain - schema.GAIN_MIN)
        total = rng.uniform(38, 52, n)
        spread = rng.uniform(-7, 7, n)
        out["gain"].append(
            pl.DataFrame(
                {
                    "down": rng.integers(3, 5, n).astype(float),
                    "ydstogo": rng.integers(1, 11, n).astype(float),
                    "yardline_100": yl.astype(float),
                    "spread": spread,
                    "total": total,
                    "team_total": total / 2 + spread / 2,
                    "era": np.full(n, era),
                    "indoor": rng.integers(0, 2, n).astype(float),
                    "gain_class": np.full(n, schema.TD_CLASS) if absurd else cls,
                    "season": np.full(n, season),
                }
            )
        )
        # fg: the logit falls with distance and wind
        dist = rng.uniform(20, 65, n)
        indoor = rng.integers(0, 2, n)
        wind = np.where(indoor == 1, np.nan, rng.uniform(0, 20, n))
        temp = np.where(indoor == 1, np.nan, rng.uniform(25, 85, n))
        z = 5.2 - 0.09 * dist - 0.06 * np.nan_to_num(wind)
        make = (rng.random(n) < 1.0 / (1.0 + np.exp(-z))).astype(int)
        out["fg"].append(
            pl.DataFrame(
                {
                    "distance": dist,
                    "era": np.full(n, era),
                    "indoor": indoor.astype(float),
                    "wind_out": pl.Series(wind).fill_nan(None),
                    "temp_out": pl.Series(temp).fill_nan(None),
                    "make": np.zeros(n, dtype=int) if absurd else make,
                    "season": np.full(n, season),
                }
            )
        )
        # punt / kickoff: the next possession
        ys = rng.integers(25, 96, n)
        outcome = rng.choice(["recv", "keep", "ret_td"], n, p=[0.95, 0.03, 0.02])
        spot = rng.integers(55, 90, n)
        out["punt"].append(
            pl.DataFrame(
                {
                    "yardline_100": ys,
                    "outcome": np.full(n, "ret_td") if absurd else outcome,
                    "spot": spot,
                    "season": np.full(n, season),
                }
            ).with_columns(
                pl.when(pl.col("outcome") == "ret_td")
                .then(None)
                .otherwise(pl.col("spot"))
                .alias("spot")
            )
        )
        ko_era = schema.kickoff_era(season)
        out["kickoff"].append(
            pl.DataFrame(
                {
                    "onside": rng.random(n) < 0.03,
                    "ko_era": np.full(n, ko_era),
                    "outcome": np.full(n, "ret_td") if absurd else outcome,
                    "spot": spot,
                    "season": np.full(n, season),
                }
            ).with_columns(
                pl.when(pl.col("outcome") == "ret_td")
                .then(None)
                .otherwise(pl.col("spot"))
                .alias("spot")
            )
        )
        # plays: the try after a touchdown (extra points and two-point tries)
        xp_ok = rng.random(60) < 0.94
        two_ok = rng.random(20) < 0.48
        xp_res = ["failed" if absurd else ("good" if ok else "failed") for ok in xp_ok]
        two_res = ["failure" if absurd else ("success" if ok else "failure") for ok in two_ok]
        out["plays"].append(
            pl.DataFrame(
                {
                    "season": [season] * 80,
                    "extra_point_attempt": [1.0] * 60 + [0.0] * 20,
                    "extra_point_result": xp_res + [None] * 20,
                    "two_point_attempt": [0.0] * 60 + [1.0] * 20,
                    "two_point_conv_result": [None] * 60 + two_res,
                },
                schema_overrides={
                    "extra_point_result": pl.String,
                    "two_point_conv_result": pl.String,
                },
            )
        )
    return {k: pl.concat(v) for k, v in out.items()}


FIT_SEASONS = [2018, 2019, 2020]


@functools.lru_cache(maxsize=1)
def fitted_bundle() -> LiveModels:
    """A small real bundle (all six models + 2 bootstrap refits) fitted on synthetic frames;
    cached so the test modules fit it once."""
    frames = synthetic_frames(FIT_SEASONS, n=500)
    models, _ = M.fit_all(frames, FIT_SEASONS, small_cfg(), n_boot=2, log=lambda _s: None)
    return models


# --- hand-made plays (data.py) ----------------------------------------------------------------
PLAY_SCHEMA: dict[str, pl.DataType] = {
    "game_id": pl.String, "play_id": pl.Float64, "order_sequence": pl.Float64,
    "season": pl.Int32, "season_type": pl.String, "week": pl.Int32, "qtr": pl.Float64,
    "down": pl.Float64, "ydstogo": pl.Int32, "yardline_100": pl.Float64,
    "posteam": pl.String, "defteam": pl.String, "home_team": pl.String, "away_team": pl.String,
    "play_type": pl.String, "yards_gained": pl.Float64, "touchdown": pl.Float64,
    "td_team": pl.String, "first_down_penalty": pl.Float64, "penalty_team": pl.String,
    "penalty_yards": pl.Float64, "game_seconds_remaining": pl.Float64,
    "half_seconds_remaining": pl.Float64, "score_differential": pl.Float64,
    "posteam_timeouts_remaining": pl.Float64, "defteam_timeouts_remaining": pl.Float64,
    "spread_line": pl.Float64, "total_line": pl.Float64, "roof": pl.String, "temp": pl.Float64,
    "wind": pl.Float64, "result": pl.Float64, "pass": pl.Float64,
    "two_point_attempt": pl.Float64, "field_goal_result": pl.String, "kick_distance": pl.Float64,
    "kicker_player_id": pl.String, "extra_point_attempt": pl.Float64,
    "extra_point_result": pl.String, "two_point_conv_result": pl.String, "safety": pl.Float64,
    "desc": pl.String, "wp": pl.Float64, "vegas_wp": pl.Float64, "xpass": pl.Float64,
    "home_opening_kickoff": pl.Float64, "punt_blocked": pl.Float64, "first_down": pl.Float64,
    "fumble_lost": pl.Float64, "interception": pl.Float64, "neutral_site": pl.Boolean,
    "i": pl.Int64,
}  # fmt: skip

PLAY_DEFAULTS = {
    "game_id": "G1", "season": 2025, "season_type": "REG", "week": 1, "qtr": 3.0,
    "posteam": "HOM", "defteam": "AWY", "home_team": "HOM", "away_team": "AWY",
    "game_seconds_remaining": 1500.0, "half_seconds_remaining": 1500.0,
    "score_differential": 0.0, "posteam_timeouts_remaining": 3.0,
    "defteam_timeouts_remaining": 3.0, "spread_line": 3.0, "total_line": 44.0,
    "roof": "outdoors", "result": 7.0, "two_point_attempt": 0.0, "neutral_site": False,
}  # fmt: skip


def plays_frame(rows: list[dict]) -> pl.DataFrame:
    """Plays from partial dicts (missing columns null, `PLAY_DEFAULTS` for the common ones); `i`
    = the play's index in its game, in the order given, unless set."""
    seen: dict[str, int] = {}
    full = []
    for r in rows:
        row = {**PLAY_DEFAULTS, **r}
        gid = row["game_id"]
        row.setdefault("i", seen.get(gid, 0))
        seen[gid] = row["i"] + 1
        row.setdefault("play_id", float(row["i"] + 1))
        row.setdefault("order_sequence", float(row["i"] + 1))
        full.append({c: row.get(c) for c in PLAY_SCHEMA})
        unknown = set(row) - set(PLAY_SCHEMA)
        assert not unknown, f"unknown play columns {unknown}"
    return pl.DataFrame(full, schema=PLAY_SCHEMA)
