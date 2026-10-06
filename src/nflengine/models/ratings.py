"""Team ratings: opponent-adjusted EPA per play (documentation/04 -> A. Team ratings).

For each split (all plays, dropbacks, designed runs) and metric (EPA, success rate):

    y_play = mu + off[posteam] + def[defteam] + hfa * home + noise

fit by weighted ridge on the season's plays from weeks before the as-of week:

- **Recency:** a play from k weeks before the as-of week weighs d^(k-1), with
  d = 0.5 ** (1 / half_life_weeks).
- **Garbage time** (win probability < 0.05 or > 0.95) weighs `garbage_weight`.
- **Shrinkage with a fading preseason prior:** every coefficient is pulled toward a
  target with strength `ridge_alpha` (in weighted plays). A team's target starts the
  season at last season's final (full-season, unweighted) rating pulled
  `prior_regression` of the way to average (further on offense when the week-1 QB1
  differs from last season's main starter) and fades toward average at the same
  half-life as the data. So week 1 *is* the prior, current-season plays take over as
  they accumulate, and late in the season it's a plain ridge toward the league average.
- **Home field** is held at the mean of the previous 3 seasons' full-season estimates
  (in-season estimates are mostly noise).

`def` is EPA (or success) **allowed**, so lower is better; `net = off - def`. Ratings are
relative to the league average (team effects sum to zero each week).

Everything is solved from per-week sufficient statistics (X'WX, X'Wy over 66 columns), so
a full 2010-2026 pass takes about a second and tuning can sweep many configurations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any

import numpy as np
import polars as pl

from nflengine.curate.teams import CANONICAL_TEAMS
from nflengine.features.asof import last_asof_week

TEAMS: tuple[str, ...] = CANONICAL_TEAMS
N_TEAMS = len(TEAMS)
# Design columns: intercept, home field, 32 offense effects, 32 defense effects.
I_MU, I_HFA, OFF0 = 0, 1, 2
DEF0 = OFF0 + N_TEAMS
N_COEF = DEF0 + N_TEAMS

SPLITS = ("all", "pass", "rush")  # all plays; dropbacks (incl. sacks, scrambles); designed runs
METRICS = ("epa", "sr")

RATING_PLAY_COLS = [
    "game_id",
    "season",
    "week",
    "season_type",
    "posteam",
    "defteam",
    "home_team",
    "location",
    "play_type",
    "two_point_attempt",
    "qb_dropback",
    "epa",
    "success",
    "wp",
]


def col_name(side: str, split: str, metric: str) -> str:
    """'off', 'all', 'epa' -> 'off_epa'; 'def', 'pass', 'sr' -> 'def_pass_sr'."""
    return f"{side}_{metric}" if split == "all" else f"{side}_{split}_{metric}"


@dataclass(frozen=True)
class RatingParams:
    half_life_weeks: float = 4.0
    prior_regression: float = 0.33  # pull of last season's rating toward average
    ridge_alpha: float = 150.0  # shrinkage strength, in recency-weighted plays
    qb_change_regression: float = 0.0  # extra pull on offense after a week-1 QB change
    garbage_weight: float = 0.25  # weight of plays with wp < garbage_wp or > 1 - garbage_wp
    garbage_wp: float = 0.05

    def __post_init__(self) -> None:
        if self.half_life_weeks <= 0:
            raise ValueError("half_life_weeks must be > 0")
        if self.ridge_alpha <= 0:
            raise ValueError("ridge_alpha must be > 0")
        for name in ("prior_regression", "qb_change_regression", "garbage_weight"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if not 0 <= self.garbage_wp < 0.5:
            raise ValueError("garbage_wp must be in [0, 0.5)")

    @property
    def decay(self) -> float:
        return 0.5 ** (1.0 / self.half_life_weeks)

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any] | None = None, **overrides: Any) -> RatingParams:
        """From `settings.yaml` -> ratings, then any non-None keyword overrides."""
        known = {f.name for f in fields(cls)}
        values = dict(cfg or {})
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"unknown ratings settings: {sorted(unknown)}")
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**{k: float(v) for k, v in values.items()})

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


# ---- inputs ---------------------------------------------------------------------------


def rating_plays(
    plays: pl.DataFrame | pl.LazyFrame, neutral_games: list[str] | None = None
) -> pl.DataFrame:
    """Scrimmage plays used for ratings: real passes and runs with an EPA.

    Drops kneels, spikes and no-plays (their own play types), two-point tries and plays
    without a canonical offense/defense. Dropbacks include sacks and scrambles.
    """
    teams = list(TEAMS)
    return (
        plays.lazy()
        .filter(
            pl.col("play_type").is_in(["pass", "run"])
            & pl.col("epa").is_not_null()
            & (pl.col("two_point_attempt").fill_null(0) == 0)
            & pl.col("posteam").is_in(teams)
            & pl.col("defteam").is_in(teams)
        )
        .select(
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            "game_id",
            pl.col("posteam").alias("off"),
            pl.col("defteam").alias("def"),
            (
                (pl.col("posteam") == pl.col("home_team"))
                & (pl.col("location").fill_null("Home") != "Neutral")
                # the curated neutral_site rule (D99): a "home" game abroad isn't one
                & ~pl.col("game_id").is_in(neutral_games or [])
            ).alias("home"),
            (pl.col("qb_dropback").fill_null(0) == 1).alias("dropback"),
            pl.col("epa").cast(pl.Float64),
            pl.col("success").fill_null(0).cast(pl.Float64).alias("sr"),
            pl.col("wp").cast(pl.Float64),
        )
        .collect()
    )


def play_weights(rp: pl.DataFrame, garbage_weight: float, garbage_wp: float) -> pl.Series:
    garbage = ((pl.col("wp") < garbage_wp) | (pl.col("wp") > 1 - garbage_wp)).fill_null(False)
    return rp.select(pl.when(garbage).then(garbage_weight).otherwise(1.0).alias("w")).to_series()


def week1_tuesdays(games: pl.DataFrame) -> pl.DataFrame:
    """(season, tuesday): the US Eastern Tuesday on or before each season's first kickoff."""
    first = (
        games.filter(pl.col("week") == 1)
        .group_by("season")
        .agg(pl.col("kickoff_utc").min().dt.convert_time_zone("America/New_York").dt.date())
    )
    day = pl.col("kickoff_utc")
    back = (day.dt.weekday() - 2) % 7  # ISO weekday: Tuesday = 2
    return first.select(
        pl.col("season").cast(pl.Int32), (day - pl.duration(days=back)).alias("tuesday")
    )


def qb_changes(games: pl.DataFrame, depth_charts: pl.DataFrame | None) -> pl.DataFrame:
    """Did the week-1 QB1 change from last season's main starter? One row per season-team.

    Last season's main starter = most regular-season starts (latest start breaks ties).
    The week-1 QB1 comes from the week-1 depth chart. Dated charts (the 2025+ daily
    snapshots, `snap_date`) only count if published by the Tuesday of week 1 (US Eastern),
    what a Tuesday run would have seen; the weekly feed (to 2024) is undated and treated
    as published before week 1. Unknown -> False.
    """
    schema = {
        "season": pl.Int32,
        "team": pl.String,
        "qb_prev_id": pl.String,
        "qb_new_id": pl.String,
        "qb_changed": pl.Boolean,
    }
    if depth_charts is None or depth_charts.is_empty():
        return pl.DataFrame(schema=schema)
    starts = pl.concat(
        [
            games.filter((pl.col("game_type") == "REG") & pl.col("completed")).select(
                "season",
                "kickoff_utc",
                pl.col(f"{side}_team").alias("team"),
                pl.col(f"{side}_qb_id").alias("qb"),
            )
            for side in ("home", "away")
        ]
    ).drop_nulls("qb")
    prev = (
        starts.group_by("season", "team", "qb")
        .agg(pl.len().alias("n"), pl.col("kickoff_utc").max().alias("last"))
        .sort(["season", "team", "n", "last"], descending=[False, False, True, True])
        .group_by("season", "team", maintain_order=True)
        .first()
        .select(
            (pl.col("season") + 1).cast(pl.Int32).alias("season"),
            "team",
            pl.col("qb").alias("qb_prev_id"),
        )
    )
    qb1 = depth_charts.filter(
        (pl.col("week") == 1) & (pl.col("position") == "QB") & (pl.col("depth_rank") == 1)
    )
    if "snap_date" in qb1.columns:
        qb1 = qb1.join(week1_tuesdays(games), on="season", how="left").filter(
            pl.col("snap_date").is_null() | (pl.col("snap_date") <= pl.col("tuesday"))
        )
    new = (
        qb1.sort("season", "team", "gsis_id")
        .group_by("season", "team", maintain_order=True)
        .first()
        .select(pl.col("season").cast(pl.Int32), "team", pl.col("gsis_id").alias("qb_new_id"))
    )
    return (
        prev.join(new, on=["season", "team"], how="full", coalesce=True)
        .with_columns(
            (
                pl.col("qb_prev_id").is_not_null()
                & pl.col("qb_new_id").is_not_null()
                & (pl.col("qb_prev_id") != pl.col("qb_new_id"))
            ).alias("qb_changed")
        )
        .select(list(schema))
        .cast(schema)
    )


@dataclass
class WeekSystem:
    """Weighted normal equations for one (season, split, week) of games."""

    A: np.ndarray  # X'WX
    b: dict[str, np.ndarray]  # metric -> X'Wy
    n_off: np.ndarray  # unweighted plays per team on offense
    n_def: np.ndarray
    w_off: np.ndarray  # garbage-weighted plays per team on offense
    w_def: np.ndarray


@dataclass
class RatingInputs:
    """Everything `compute_ratings` needs, prepared once (the slow part)."""

    weeks: dict[tuple[int, str], dict[int, WeekSystem]]
    seasons: list[int]
    last_week: dict[int, int]  # last as-of week to output per season
    finished: dict[int, bool]  # season over (its full-season rating seeds the next prior)
    qb_changed: dict[int, np.ndarray]  # per team: week-1 QB change before this season
    garbage_weight: float
    garbage_wp: float


def _week_system(cells: pl.DataFrame) -> WeekSystem:
    off = cells["off_idx"].to_numpy()
    dfn = cells["def_idx"].to_numpy()
    w = cells["w"].to_numpy()
    rows = np.arange(len(off))
    X = np.zeros((len(off), N_COEF))
    X[:, I_MU] = 1.0
    X[:, I_HFA] = cells["home"].cast(pl.Float64).to_numpy()
    X[rows, OFF0 + off] = 1.0
    X[rows, DEF0 + dfn] = 1.0
    A = X.T @ (X * w[:, None])
    b = {m: X.T @ cells[f"sum_{m}"].to_numpy() for m in METRICS}
    n = cells["n"].to_numpy().astype(float)
    return WeekSystem(
        A=A,
        b=b,
        n_off=np.bincount(off, weights=n, minlength=N_TEAMS),
        n_def=np.bincount(dfn, weights=n, minlength=N_TEAMS),
        w_off=np.bincount(off, weights=w, minlength=N_TEAMS),
        w_def=np.bincount(dfn, weights=w, minlength=N_TEAMS),
    )


def prepare_inputs(
    rp: pl.DataFrame,
    games: pl.DataFrame,
    depth_charts: pl.DataFrame | None,
    params: RatingParams,
    seasons: Sequence[int] | None = None,
    splits: Sequence[str] = SPLITS,
) -> RatingInputs:
    """Aggregate plays into per-week normal equations (garbage-time weights applied)."""
    seasons = sorted(seasons or rp["season"].unique().to_list())
    idx = pl.DataFrame({"team": list(TEAMS), "i": list(range(N_TEAMS))})
    w = play_weights(rp, params.garbage_weight, params.garbage_wp)
    base = (
        rp.with_columns(w)
        .filter(pl.col("season").is_in(seasons))
        .join(idx.rename({"team": "off", "i": "off_idx"}), on="off")
        .join(idx.rename({"team": "def", "i": "def_idx"}), on="def")
        .with_columns(
            pl.when(pl.col("dropback"))
            .then(pl.lit("pass"))
            .otherwise(pl.lit("rush"))
            .alias("split"),
            (pl.col("epa") * pl.col("w")).alias("wepa"),
            (pl.col("sr") * pl.col("w")).alias("wsr"),
        )
    )
    keys = ["season", "week", "off_idx", "def_idx", "home"]
    agg = [
        pl.len().alias("n"),
        pl.col("w").sum(),
        pl.col("wepa").sum().alias("sum_epa"),
        pl.col("wsr").sum().alias("sum_sr"),
    ]
    frames = []
    if "all" in splits:
        frames.append(base.group_by(keys).agg(agg).with_columns(pl.lit("all").alias("split")))
    parts = [s for s in splits if s != "all"]
    if parts:
        by = base.filter(pl.col("split").is_in(parts)).group_by([*keys, "split"]).agg(agg)
        frames.append(by.select(frames[0].columns) if frames else by)
    # Sorted so every run sums in the same order: bit-for-bit reproducible ratings.
    cells = pl.concat(frames).sort(["season", "split", "week", "off_idx", "def_idx", "home"])

    weeks: dict[tuple[int, str], dict[int, WeekSystem]] = {}
    for (season, split, week), part in cells.partition_by(
        ["season", "split", "week"], as_dict=True
    ).items():
        weeks.setdefault((int(season), str(split)), {})[int(week)] = _week_system(part)

    qb = qb_changes(games, depth_charts)
    last_week, finished, qb_changed = {}, {}, {}
    for season in seasons:
        lw = last_asof_week(games, season)
        g = games.filter(pl.col("season") == season)
        last_week[season] = lw or 1
        max_week = int(g["week"].max()) if g.height else 0
        top = g.filter(pl.col("week") == max_week)
        finished[season] = bool(lw == max_week and top.height and top["completed"].all())
        changed = dict(
            qb.filter(pl.col("season") == season).select("team", "qb_changed").iter_rows()
        )
        qb_changed[season] = np.array([bool(changed.get(t, False)) for t in TEAMS])
    return RatingInputs(
        weeks=weeks,
        seasons=seasons,
        last_week=last_week,
        finished=finished,
        qb_changed=qb_changed,
        garbage_weight=params.garbage_weight,
        garbage_wp=params.garbage_wp,
    )


# ---- solve ------------------------------------------------------------------------------


def _center(beta: np.ndarray) -> np.ndarray:
    """Make team effects sum to zero, moving the means into the intercept."""
    beta = beta.copy()
    for start in (OFF0, DEF0):
        m = beta[start : start + N_TEAMS].mean()
        beta[start : start + N_TEAMS] -= m
        beta[I_MU] += m
    return beta


def _prior_target(final: np.ndarray | None, params: RatingParams, qb: np.ndarray) -> np.ndarray:
    """Week-1 shrinkage target from last season's full-season coefficients."""
    target = np.zeros(N_COEF)
    if final is None:
        return target
    keep = 1.0 - params.prior_regression
    target[I_MU] = final[I_MU]
    target[OFF0:DEF0] = final[OFF0:DEF0] * keep * np.where(qb, 1 - params.qb_change_regression, 1)
    target[DEF0:] = final[DEF0:] * keep
    return target


@dataclass
class RatingResult:
    keys: list[tuple[int, int]]  # (season, week) per as-of key, in order
    coefs: dict[tuple[str, str], np.ndarray]  # (split, metric) -> (K, N_COEF)
    n_off: np.ndarray  # (K, 32) unweighted plays this season before the key, offense
    n_def: np.ndarray
    prior_weight: np.ndarray  # (K, 32) approximate share of the prior in the estimate
    finals: dict[int, dict[tuple[str, str], np.ndarray]]  # season -> full-season coefficients


HFA_SEASONS = 3  # in-season home field = mean of the last 3 full-season estimates
HFA_PIN = 1e9  # penalty that holds the in-season home-field coefficient at that value


def compute_ratings(
    inp: RatingInputs,
    params: RatingParams,
    splits: Sequence[str] = SPLITS,
    metrics: Sequence[str] = METRICS,
) -> RatingResult:
    """Ratings as of every week of every season in `inp`, chaining preseason priors.

    Home field is noisy within a season (EPA/play home edge swings about +-0.04 from
    year to year), so in-season it is held at the mean of the previous seasons'
    full-season estimates. Each finished season also gets an unweighted full-season fit
    (ridge toward average, free home field): that is the "final rating" that seeds the
    next season's prior and its home-field history.
    """
    if (params.garbage_weight, params.garbage_wp) != (inp.garbage_weight, inp.garbage_wp):
        raise ValueError("inputs were prepared with different garbage-time settings")
    d, alpha = params.decay, params.ridge_alpha
    eye = np.eye(N_COEF) * alpha
    keys: list[tuple[int, int]] = []
    coefs: dict[tuple[str, str], list[np.ndarray]] = {(s, m): [] for s in splits for m in metrics}
    n_off, n_def, prior_w = [], [], []
    finals: dict[int, dict[tuple[str, str], np.ndarray]] = {}
    prev_final: dict[tuple[str, str], np.ndarray] | None = None
    hfa_hist: dict[tuple[str, str], list[float]] = {(s, m): [] for s in splits for m in metrics}

    for season in inp.seasons:
        out_weeks = inp.last_week[season]
        season_final: dict[tuple[str, str], np.ndarray] = {}
        for split in splits:
            weeks = inp.weeks.get((season, split), {})
            A = np.zeros((N_COEF, N_COEF))
            b = {m: np.zeros(N_COEF) for m in metrics}
            cnt_off, cnt_def = np.zeros(N_TEAMS), np.zeros(N_TEAMS)
            w_off, w_def = np.zeros(N_TEAMS), np.zeros(N_TEAMS)
            targets, pens = {}, {}
            for m in metrics:
                last = prev_final.get((split, m)) if prev_final else None
                targets[m] = _prior_target(last, params, inp.qb_changed[season])
                pens[m] = eye.copy()
                if hist := hfa_hist[(split, m)][-HFA_SEASONS:]:
                    targets[m][I_HFA] = float(np.mean(hist))
                    pens[m][I_HFA, I_HFA] = HFA_PIN
            for week in range(1, out_weeks + 1):
                if week > 1:
                    A *= d
                    w_off *= d
                    w_def *= d
                    for m in metrics:
                        b[m] *= d
                    if (ws := weeks.get(week - 1)) is not None:
                        A += ws.A
                        cnt_off += ws.n_off
                        cnt_def += ws.n_def
                        w_off += ws.w_off
                        w_def += ws.w_def
                        for m in metrics:
                            b[m] += ws.b[m]
                fade = d ** (week - 1)
                for m in metrics:
                    target = targets[m].copy()
                    target[OFF0:] *= fade
                    beta = np.linalg.solve(A + pens[m], b[m] + pens[m] @ target)
                    coefs[(split, m)].append(_center(beta))
                if split == splits[0]:
                    keys.append((season, week))
                    n_off.append(cnt_off.copy())
                    n_def.append(cnt_def.copy())
                    # ~ share of last season's rating still in the estimate (1 in week 1).
                    shrink = (alpha / (alpha + w_off) + alpha / (alpha + w_def)) / 2
                    prior_w.append(shrink * fade)
            if inp.finished[season]:
                A_all = sum((ws.A for ws in weeks.values()), np.zeros((N_COEF, N_COEF)))
                for m in metrics:
                    b_all = sum((ws.b[m] for ws in weeks.values()), np.zeros(N_COEF))
                    full = _center(np.linalg.solve(A_all + eye, b_all))
                    hfa_hist[(split, m)].append(float(full[I_HFA]))
                    season_final[(split, m)] = full
        if inp.finished[season]:
            finals[season] = season_final
            prev_final = season_final
        else:
            prev_final = None  # an unfinished season can't seed the next one
    return RatingResult(
        keys=keys,
        coefs={k: np.vstack(v) if v else np.zeros((0, N_COEF)) for k, v in coefs.items()},
        n_off=np.vstack(n_off) if n_off else np.zeros((0, N_TEAMS)),
        n_def=np.vstack(n_def) if n_def else np.zeros((0, N_TEAMS)),
        prior_weight=np.vstack(prior_w) if prior_w else np.zeros((0, N_TEAMS)),
        finals=finals,
    )


def ratings_frame(res: RatingResult, qb: pl.DataFrame | None = None) -> pl.DataFrame:
    """The `team_ratings` table: one row per (season, as-of week, team)."""
    k = len(res.keys)
    seasons = np.repeat([s for s, _ in res.keys], N_TEAMS)
    weeks = np.repeat([w for _, w in res.keys], N_TEAMS)
    cols: dict[str, Any] = {
        "season": pl.Series(seasons, dtype=pl.Int32),
        "week": pl.Series(weeks, dtype=pl.Int32),
        "team": list(TEAMS) * k,
    }
    for (split, metric), B in res.coefs.items():
        off = B[:, OFF0:DEF0].ravel()
        dfn = B[:, DEF0:].ravel()
        cols[col_name("off", split, metric)] = off
        cols[col_name("def", split, metric)] = dfn
        cols[col_name("net", split, metric)] = off - dfn
        if split == "all":
            cols[f"mu_{metric}"] = np.repeat(B[:, I_MU], N_TEAMS)
            cols[f"hfa_{metric}"] = np.repeat(B[:, I_HFA], N_TEAMS)
    cols["off_plays"] = res.n_off.ravel().astype(np.int64)
    cols["def_plays"] = res.n_def.ravel().astype(np.int64)
    cols["plays_observed"] = cols["off_plays"] + cols["def_plays"]
    cols["prior_weight"] = res.prior_weight.ravel()
    df = pl.DataFrame(cols)
    if qb is not None and qb.height:
        df = df.join(
            qb.select("season", "team", pl.col("qb_changed").alias("qb_change_prior")),
            on=["season", "team"],
            how="left",
        ).with_columns(pl.col("qb_change_prior").fill_null(False))
    return df


def net_lookup(res: RatingResult, split: str = "all", metric: str = "epa") -> pl.DataFrame:
    """Compact (season, week, team, net, hfa) frame for objective scoring."""
    B = res.coefs[(split, metric)]
    k = len(res.keys)
    return pl.DataFrame(
        {
            "season": pl.Series(np.repeat([s for s, _ in res.keys], N_TEAMS), dtype=pl.Int32),
            "week": pl.Series(np.repeat([w for _, w in res.keys], N_TEAMS), dtype=pl.Int32),
            "team": list(TEAMS) * k,
            "net": (B[:, OFF0:DEF0] - B[:, DEF0:]).ravel(),
            "hfa": np.repeat(B[:, I_HFA], N_TEAMS),
        }
    )
