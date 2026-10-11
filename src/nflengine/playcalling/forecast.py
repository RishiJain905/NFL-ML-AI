"""The play-calling forecast (PC02, D129): each team's rates in its next game, against its opponent.

For every game of a week, each team's play-calling rates (`TARGETS`) are forecast **as of the
week** (what a run on the Tuesday night before it could see, D123: play-by-play of every earlier
game, FTN 48 hours after kickoff), then graded after the games. For a team T facing an opponent
O in week W of season S, on the rate scale:

    forecast = team + lam * opponent + script

- **team**: an empirical-Bayes rate. Its prior is the league's rate (season to date as of W,
  blended with last season's) plus `rho` times T's gap to the league last season (`rho_new`
  when T's coordinator or head coach changed, `rho_same` otherwise), worth `kappa` plays; this
  season's plays update it: (kappa * m0 + k) / (kappa + n). With no plays the forecast is the
  prior; with many it approaches T's own rate.
- **opponent**: what offenses (for a defense's calls: defenses) did against O beyond their own
  rates (each game's count minus its plays times that team's `team` rate as of W), summed over
  O's games before W and shrunk toward `rho_opp` times last season's (worth `kappa_opp` plays).
  "Adjusted for the offenses it faced": a defense that met pass-happy offenses isn't charged
  for their passing.
- **script** (PROE and dropback rate only): `b_spread` times the game script the pre-game spread
  implies, scored through LD00's `pass` model (the plays of the last three seasons, weighted by
  how close their game's spread was to this one), plus `b_total` times the total line's gap to
  the league's (per 10 points). Every observed game is script-adjusted before it enters the
  team term, so a favourite's season isn't counted twice.

Baselines (the phase file's four): `b1` the league rate (for dropback rate, plus `xpass` on the
expected game script), `b2` the team's season-to-date rate (its last-season rate in week 1),
`b3` the shrunk team rate, `b4` the shrunk rate + opponent. `b3`, `b4` and the model each have
their own parameters, fit on the tuning window by minimizing the per-play squared error.

Everything here is pure (frames in, frames / arrays out); `forecast_runs.py` reads and writes
files and W&B. No research data (participation) is ever read (D14); the production models are
never imported (D107).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nflengine.playcalling import build as B
from nflengine.playcalling import labels as L

SCHEMA_VERSION = 1
KINDS = ("b1", "b2", "b3", "b4", "model")
KIND_LABELS = {
    "b1": "league rate",
    "b2": "season to date",
    "b3": "shrunk team rate",
    "b4": "shrunk + opponent",
    "model": "forecast",
}
PARTS = ((1, 4, "weeks 1-4"), (5, 10, "weeks 5-10"), (11, 99, "weeks 11+"))
RANGE_LEVEL = 0.80
RANGE_Z = 1.2815515655446004  # two-sided 80% of a normal
GAME_TYPES = ("REG", "WC", "DIV", "CON", "SB")

# Defaults; settings.yaml `playcalling.forecast` overrides (see `forecast_settings`).
DEFAULTS: dict[str, Any] = {
    "tune_seasons": "2019-2023",  # FTN targets: the same window from 2022
    "report_seasons": "2019-2025",
    "ref_seasons": 3,  # the script curve's reference plays: the last 3 seasons
    "spread_bandwidth": 2.5,  # points (Gaussian kernel over the offense's spread)
    "total_scale": 10.0,  # b_total is per 10 points of total line
    "league_prior_plays": 2000.0,  # last season's league rate is worth ~a week of plays
    "pre_change_weight": 0.5,  # a game before a mid-season head-coach change counts half
    "ship": {"seasons_pbp": 5, "seasons_ftn": 3},
}


def forecast_settings(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Code defaults < settings.yaml `playcalling.forecast` < `overrides`."""
    from nflengine.settings import get_config

    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for src in ((get_config().playcalling or {}).get("forecast") or {}, overrides or {}):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k].update(v)
            else:
                out[k] = v
    return out


def parse_seasons(text: str | Sequence[int]) -> list[int]:
    """'2019-2023' -> [2019, ..., 2023]; '2019,2021' -> [2019, 2021]; a list passes through."""
    if not isinstance(text, str):
        return sorted({int(s) for s in text})
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(out)


# --- the targets ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class Target:
    key: str  # the PC00 metric name when it's one metric (the pages line them up)
    label: str
    short: str
    side: str  # whose call it is: offense | defense
    metrics: tuple[str, ...]  # summed (a share of the same denominator)
    situation: str  # all | neutral
    unit: str  # share | mean | over_expected
    source: str  # pbp | ftn
    script: bool = False  # the game-script term (spread + total)
    lo: float = 0.0  # clip bounds
    hi: float = 1.0
    per: str = "plays"  # the denominator, in words
    # the second term's group: the opponent (what it allows / draws), or the venue (the game's
    # home team: its stats crew charts run location, D129)
    context: str = "opponent"

    @property
    def first_season(self) -> int:
        """First season with data (FTN from 2022); the first forecast season is a year later
        for a last-season prior, but FTN targets are scored from 2022 (league-only prior)."""
        return L.FTN_FIRST if self.source == "ftn" else B.FIRST_SEASON

    @property
    def share(self) -> bool:
        return self.unit == "share"

    @property
    def integer_counts(self) -> bool:
        return self.unit == "share"


TARGETS: tuple[Target, ...] = (
    Target("proe", "Pass rate over expected, neutral situations", "PROE", "offense", ("proe",),
           "neutral", "over_expected", "pbp", script=True, lo=-1.0, hi=1.0,
           per="neutral plays"),
    Target("dropback_rate", "Dropback rate, all plays", "Dropback rate", "offense",
           ("dropback_rate",), "all", "share", "pbp", script=True),
    Target("deep_shot_rate", f"Deep shots ({L.DEEP_SHOT_AIR_YARDS}+ air yards) per attempt",
           "Deep shots", "offense", ("deep_shot_rate",), "all", "share", "pbp",
           per="pass attempts"),
    Target("run_middle", "Designed runs up the middle", "Runs up the middle", "offense",
           ("run_middle",), "all", "share", "pbp", per="designed runs", context="venue"),
    Target("run_outside", "Designed runs outside the tackles (end runs)", "Runs outside",
           "offense", ("run_left_end", "run_right_end"), "all", "share", "pbp",
           per="designed runs", context="venue"),
    Target("play_action_rate", "Play-action per dropback", "Play-action", "offense",
           ("play_action_rate",), "all", "share", "ftn", per="dropbacks"),
    Target("screen_rate", "Screens per dropback", "Screens", "offense", ("screen_rate",), "all",
           "share", "ftn", per="dropbacks"),
    Target("motion_rate", "Pre-snap motion per play", "Motion", "offense", ("motion_rate",),
           "all", "share", "ftn"),
    Target("blitz_rate", "Blitz per dropback (FTN: 1+ blitzer)", "Blitz", "defense",
           ("blitz_rate",), "all", "share", "ftn", per="dropbacks faced"),
    Target("rushers_avg", "Pass rushers per dropback", "Pass rushers", "defense", ("rushers_avg",),
           "all", "mean", "ftn", lo=1.0, hi=11.0, per="dropbacks faced"),
    Target("heavy_box_rate", f"Heavy box ({L.HEAVY_BOX_MIN} or more) per play", "Heavy box",
           "defense", ("heavy_box_rate",), "all", "share", "ftn", per="plays faced"),
)  # fmt: skip
TARGET_KEYS = tuple(t.key for t in TARGETS)


def get_target(key: str) -> Target:
    for t in TARGETS:
        if t.key == key:
            return t
    raise KeyError(f"unknown play-calling target {key!r}; known: {', '.join(TARGET_KEYS)}")


def select_targets(keys: Iterable[str] | None) -> list[Target]:
    return list(TARGETS) if not keys else [get_target(k) for k in keys]


# --- parameters ----------------------------------------------------------------------------------
# name -> (lower, upper) on the optimizer's scale, and the map to the model's scale
@dataclass(frozen=True)
class ParamSpec:
    name: str
    lo: float
    hi: float
    start: tuple[float, ...]
    to_model: Callable[[float], float]
    to_opt: Callable[[float], float]


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


PARAMS: dict[str, ParamSpec] = {
    "kappa": ParamSpec("kappa", 0.0, 10.5, (math.log(100), math.log(600)), math.exp, math.log),
    "rho_same": ParamSpec("rho_same", -6.0, 6.0, (_logit(0.6),), _sig, _logit),
    "rho_new": ParamSpec("rho_new", -6.0, 6.0, (_logit(0.3),), _sig, _logit),
    "kappa_opp": ParamSpec("kappa_opp", 0.0, 11.5, (math.log(500),), math.exp, math.log),
    "rho_opp": ParamSpec("rho_opp", -6.0, 6.0, (_logit(0.3),), _sig, _logit),
    "lam": ParamSpec("lam", -1.0, 6.0, (0.5, 1.0), float, float),
    "b_spread": ParamSpec("b_spread", -3.0, 5.0, (1.0,), float, float),
    "b_total": ParamSpec("b_total", -0.5, 0.5, (0.0,), float, float),
}
TEAM_PARAMS = ("kappa", "rho_same", "rho_new")
OPP_PARAMS = ("kappa_opp", "rho_opp", "lam")
SCRIPT_PARAMS = ("b_spread", "b_total")


def kind_params(target: Target, kind: str) -> tuple[str, ...]:
    """The parameters a predictor fits (b1 / b2 have none)."""
    if kind == "b3":
        return TEAM_PARAMS
    if kind == "b4" or (kind == "model" and not target.script):
        return TEAM_PARAMS + OPP_PARAMS
    if kind == "model":
        return TEAM_PARAMS + OPP_PARAMS + SCRIPT_PARAMS
    return ()


# --- inputs ------------------------------------------------------------------------------------
def load_team_games(paths, seasons: Iterable[int], targets: Sequence[Target] = TARGETS):
    """The PC00 team-game sums every target reads (`team_game_tendencies` of each season that
    has a build), as `num` / `den`; seasons without a folder are skipped."""
    metrics = sorted({m for t in targets for m in t.metrics})
    sits = sorted({t.situation for t in targets})
    frames = []
    for s in sorted(set(seasons)):
        f = B.season_dir(paths, s) / "team_game_tendencies.parquet"
        if not f.exists():
            continue
        frames.append(
            pl.scan_parquet(f)
            .filter(pl.col("metric").is_in(metrics) & pl.col("situation").is_in(sits))
            .select(
                "season",
                "week",
                "game_id",
                "team",
                "opponent",
                "side",
                "metric",
                "situation",
                "n",
                "value",
            )  # fmt: skip
            .collect()
        )
    schema = {"season": pl.Int32, "week": pl.Int32, "game_id": pl.String, "team": pl.String,
              "opponent": pl.String, "side": pl.String, "metric": pl.String,
              "situation": pl.String, "num": pl.Float64, "den": pl.Int64}  # fmt: skip
    if not frames:
        return pl.DataFrame(schema=schema)
    tg = pl.concat(frames, how="diagonal_relaxed")
    return tg.select(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        "game_id", "team", "opponent", "side", "metric", "situation",
        (pl.col("value") * pl.col("n")).cast(pl.Float64).alias("num"),
        pl.col("n").cast(pl.Int64).alias("den"),
    )  # fmt: skip


def observations(tg: pl.DataFrame, target: Target) -> pl.DataFrame:
    """One row per team-game for a target: (season, week, game_id, team, opponent, k, n).
    `team` is whose call it is (the defense for a defense target)."""
    x = tg.filter(
        (pl.col("side") == target.side)
        & pl.col("metric").is_in(list(target.metrics))
        & (pl.col("situation") == target.situation)
    )
    keys = ["season", "week", "game_id", "team", "opponent"]
    x = x.group_by(keys).agg(
        pl.col("num").sum().alias("k"),
        pl.col("den").max().alias("n"),
        pl.col("den").min().alias("_nmin"),
    )
    bad = x.filter(pl.col("n") != pl.col("_nmin"))
    if bad.height:
        raise ValueError(f"{target.key}: the summed metrics have different denominators")
    x = x.drop("_nmin").filter(pl.col("n") > 0)
    if target.integer_counts:  # value * n of a share: whole plays
        x = x.with_columns(pl.col("k").round(0))
    return x.sort(keys)


def load_games(paths) -> pl.DataFrame:
    """Curated games (regular season + playoffs) with the columns the forecast reads."""
    cols = ["game_id", "season", "game_type", "week", "home_team", "away_team", "home_score",
            "away_score", "result", "spread_line", "total_line", "home_coach", "away_coach",
            "kickoff_utc", "gameday"]  # fmt: skip
    g = pl.read_parquet(paths.curated / "games.parquet")
    typed = {
        "kickoff_utc": pl.Datetime("us", "UTC"),
        "spread_line": pl.Float64,
        "total_line": pl.Float64,
        "result": pl.Float64,
    }
    for c in cols:
        if c not in g.columns:  # a missing column is all null (no kickoff: FTN fails closed)
            g = g.with_columns(pl.lit(None, dtype=typed.get(c, pl.String)).alias(c))
    g = g.select(cols).with_columns(
        pl.col("kickoff_utc").cast(pl.Datetime("us", "UTC")),
        *[pl.col(c).cast(pl.Float64) for c in ("spread_line", "total_line", "result")],
    )
    return g.filter(pl.col("game_type").is_in(GAME_TYPES)).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


def current_lines(games: pl.DataFrame, lines: pl.DataFrame | None) -> pl.DataFrame:
    """One line per game: the schedule's (nflverse), then the Odds API median across books,
    then ESPN (the order of `features.game.current_lines`, D40; copied, D107)."""
    out = games.select("game_id", "spread_line", "total_line")
    if (
        lines is None
        or lines.is_empty()
        or not {"game_id", "source", "home_spread", "total"} <= set(lines.columns)
    ):
        return out.with_columns(
            pl.when(pl.col("spread_line").is_not_null()).then(pl.lit("nflverse_schedules"))
            .otherwise(None).alias("line_source")
        )  # fmt: skip
    agg = lines.group_by("game_id").agg(
        *[
            pl.col(c).filter(pl.col("source") == src).median().alias(f"{c}_{src}")
            for c in ("home_spread", "total")
            for src in ("nflverse_schedules", "odds_api", "espn")
        ]
    )
    j = out.join(agg, on="game_id", how="left")
    order = ("nflverse_schedules", "odds_api", "espn")
    src = pl.when(pl.col("spread_line").is_not_null()).then(pl.lit("nflverse_schedules"))
    for s in order:
        src = src.when(pl.col(f"home_spread_{s}").is_not_null()).then(pl.lit(s))
    return j.select(
        "game_id",
        pl.coalesce(["spread_line", *[f"home_spread_{s}" for s in order]]).alias("spread_line"),
        pl.coalesce(["total_line", *[f"total_{s}" for s in order]]).alias("total_line"),
        src.otherwise(None).alias("line_source"),
    )


def team_view(games: pl.DataFrame) -> pl.DataFrame:
    """Two rows per game, one per team: the team's spread (its expected margin: + = favoured),
    the total line and the kickoff."""
    keep = ["season", "week", "game_id", "game_type", "kickoff_utc", "total_line"]
    home = games.select(
        *keep, pl.col("home_team").alias("team"), pl.col("away_team").alias("opponent"),
        pl.col("spread_line").alias("spread"), pl.lit(True).alias("is_home"),
    )  # fmt: skip
    away = games.select(
        *keep, pl.col("away_team").alias("team"), pl.col("home_team").alias("opponent"),
        (-pl.col("spread_line")).alias("spread"), pl.lit(False).alias("is_home"),
    )  # fmt: skip
    return pl.concat([home, away]).rename({"total_line": "total"})


# --- coaches -------------------------------------------------------------------------------------
def read_seed(path: Path | None = None) -> pl.DataFrame:
    """The coaching seed (`config/coaching_seed.csv`, P08): (coach, team, season, role). A
    missing or malformed file is an empty frame (then only head-coach changes count)."""
    from nflengine.settings import CONFIG_DIR

    path = path or CONFIG_DIR / "coaching_seed.csv"
    empty = pl.DataFrame(
        schema={"coach": pl.String, "team": pl.String, "season": pl.Int32, "role": pl.String}
    )
    try:
        if not path.exists():
            return empty
        df = pl.read_csv(path, comment_prefix="#", infer_schema_length=0)
        df = df.rename({c: c.strip().lower() for c in df.columns})
        return df.select(
            pl.col("coach").str.strip_chars(),
            pl.col("team").str.strip_chars().str.to_uppercase(),
            pl.col("season").str.strip_chars().cast(pl.Int32),
            pl.col("role").str.strip_chars().str.to_uppercase(),
        ).filter(pl.col("coach").is_not_null() & (pl.col("coach") != ""))
    except Exception:  # a hand-made file never sinks a forecast
        return empty


def head_coaches(games: pl.DataFrame, fixes: pl.DataFrame | None) -> pl.DataFrame:
    """(season, team, week, hc): each team's head coach by week, nflverse's corrected by
    `config/head_coach_fixes.csv` (D92: a later `from_week` wins from its week on)."""
    rows = pl.concat(
        [
            games.select(
                "season",
                "week",
                pl.col("home_team").alias("team"),
                pl.col("home_coach").alias("hc"),
            ),
            games.select(
                "season",
                "week",
                pl.col("away_team").alias("team"),
                pl.col("away_coach").alias("hc"),
            ),
        ]  # fmt: skip
    ).with_columns(pl.col("hc").cast(pl.String))
    if fixes is not None and not fixes.is_empty():
        for r in fixes.sort("season", "team", "from_week").iter_rows(named=True):
            hit = (
                (pl.col("season") == r["season"])
                & (pl.col("team") == r["team"])
                & (pl.col("week") >= r["from_week"])
            )
            rows = rows.with_columns(
                pl.when(hit).then(pl.lit(r["coach"])).otherwise("hc").alias("hc")
            )
    return rows.unique(["season", "team", "week"], keep="first").sort("season", "team", "week")


def coach_changes(
    games: pl.DataFrame, seed: pl.DataFrame, fixes: pl.DataFrame | None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """`changed`: (season, team, side, changed) - the play-caller's side changed since last
    season: the head coach at the season's first game differs from last season's last, or the
    seed's OC (offense) / DC (defense) names differ. `midseason`: (season, team, change_week) -
    the first week a team's head coach differs from its week-1 coach (an in-season firing)."""
    hc = head_coaches(games, fixes)
    first = hc.group_by("season", "team").agg(
        pl.col("hc").first().alias("hc_first"), pl.col("hc").last().alias("hc_last")
    )
    prev = first.select(
        (pl.col("season") + 1).cast(pl.Int32).alias("season"), "team",
        pl.col("hc_last").alias("hc_prev"),
    )  # fmt: skip
    roles = seed.group_by("team", "season", "role").agg(
        pl.col("coach").sort().str.join("|").alias("names")
    )
    out = []
    for side, role in (("offense", "OC"), ("defense", "DC")):
        r = roles.filter(pl.col("role") == role).select("team", "season", "names")
        rp = r.select("team", (pl.col("season") + 1).cast(pl.Int32).alias("season"),
                      pl.col("names").alias("prev_names"))  # fmt: skip
        x = (
            first.join(prev, on=["season", "team"], how="left")
            .join(r, on=["season", "team"], how="left")
            .join(rp, on=["season", "team"], how="left")
        )
        hc_changed = (pl.col("hc_first") != pl.col("hc_prev")).fill_null(False)
        seed_known = pl.col("names").is_not_null() | pl.col("prev_names").is_not_null()
        co_changed = seed_known & (
            pl.col("names").fill_null("") != pl.col("prev_names").fill_null("")
        )
        out.append(
            x.select(
                "season",
                "team",
                pl.lit(side).alias("side"),
                (hc_changed | co_changed).alias("changed"),
            )  # fmt: skip
        )
    changed = pl.concat(out)
    mid = (
        hc.join(first.select("season", "team", "hc_first"), on=["season", "team"])
        .filter(pl.col("hc") != pl.col("hc_first"))
        .group_by("season", "team")
        .agg(pl.col("week").min().cast(pl.Int32).alias("change_week"))
    )
    return changed, mid


# --- the game-script curve -----------------------------------------------------------------------
@dataclass
class ScriptCurve:
    """For one season: the league's expected dropback rate as a function of the offense's
    pre-game spread, from the last `ref_seasons` seasons' plays (Gaussian kernel over their
    game's spread), scored through LD00's `pass` model; plus `xpass` on the same plays (the
    league situational baseline) and the pass model minus `xpass` on neutral plays (PROE's
    script). `*_mean` are the plain means over the reference plays, `total_mean` the mean total
    line of their games."""

    season: int
    grid: list[float]
    db: list[float]
    proe: list[float]
    xp: list[float]
    db_mean: float
    proe_mean: float
    xp_mean: float
    total_mean: float
    ref_seasons: list[int]
    model: str
    n_plays: int

    def x_spread(self, target: Target, spread: np.ndarray) -> np.ndarray:
        g = np.asarray(self.grid)
        if target.key == "proe":
            v = np.interp(spread, g, np.asarray(self.proe)) - self.proe_mean
        else:
            v = np.interp(spread, g, np.asarray(self.db)) - self.db_mean
        return np.where(np.isfinite(spread), v, 0.0)

    def x_baseline(self, spread: np.ndarray) -> np.ndarray:
        v = np.interp(spread, np.asarray(self.grid), np.asarray(self.xp)) - self.xp_mean
        return np.where(np.isfinite(spread), v, 0.0)

    def x_total(self, total: np.ndarray, scale: float) -> np.ndarray:
        return np.where(np.isfinite(total), (total - self.total_mean) / scale, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ScriptCurve:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


SPREAD_GRID = [round(x, 2) for x in np.arange(-30.0, 30.01, 0.5)]


def kernel_curve(spread: np.ndarray, value: np.ndarray, bandwidth: float) -> list[float]:
    """The Gaussian-kernel mean of `value` at each grid spread (rows with a NaN dropped)."""
    ok = np.isfinite(spread) & np.isfinite(value)
    s, v = spread[ok], value[ok]
    out = []
    for g in SPREAD_GRID:
        w = np.exp(-0.5 * ((s - g) / bandwidth) ** 2)
        tot = w.sum()
        out.append(float((w * v).sum() / tot) if tot > 1e-9 else float(np.mean(v)))
    return out


def curve_from_plays(
    season: int, ref: pl.DataFrame, pass_prob: np.ndarray, model: str, bandwidth: float
) -> ScriptCurve:
    """`ref`: the reference plays (LD00's pass frame) with `spread` (the offense's), `total`,
    `xpass`, `neutral` and `game_id`; `pass_prob`: the pass model on them."""
    s = ref["spread"].cast(pl.Float64).to_numpy()
    p = np.asarray(pass_prob, dtype=float)
    xp = ref["xpass"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
    neu = ref["neutral"].fill_null(False).to_numpy()
    d = p - xp
    totals = ref.group_by("game_id").agg(pl.col("total").first())["total"].cast(pl.Float64)
    return ScriptCurve(
        season=season,
        grid=list(SPREAD_GRID),
        db=kernel_curve(s, p, bandwidth),
        proe=kernel_curve(s[neu], d[neu], bandwidth),
        xp=kernel_curve(s, xp, bandwidth),
        db_mean=float(np.mean(p)),
        proe_mean=float(np.nanmean(d[neu])),
        xp_mean=float(np.nanmean(xp)),
        total_mean=float(totals.drop_nulls().mean() or 0.0),
        ref_seasons=sorted({int(x) for x in ref["season"].unique().to_list()}),
        model=model,
        n_plays=int(ref.height),
    )


# --- the design: everything a prediction needs, as arrays ---------------------------------------
@dataclass
class Design:
    """One target over some seasons. `rows` are the team-games to forecast (with `k`, `n`
    when played); the grid holds each (season, team, as-of week) the rows need."""

    target: Target
    rows: pl.DataFrame
    grid: pl.DataFrame
    a: dict[str, np.ndarray] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def y(self) -> np.ndarray:
        return self.a["f_y"]

    @property
    def n(self) -> np.ndarray:
        return self.a["f_n"]


def _script_columns(
    view: pl.DataFrame, target: Target, curves: dict[int, ScriptCurve], total_scale: float
) -> pl.DataFrame:
    """`view` (season, ..., spread, total) + x1 (spread script), x2 (total), xb (baseline 1's
    xpass script); zeros for a target without the script term or a game without a line."""
    if view.is_empty():
        return view.with_columns(
            pl.lit(0.0).alias("x1"), pl.lit(0.0).alias("x2"), pl.lit(0.0).alias("xb")
        )
    parts = []
    for (season,), part in view.group_by(["season"], maintain_order=True):
        c = curves.get(int(season))
        sp = part["spread"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
        to = part["total"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
        if c is None or not target.script:
            x1 = np.zeros(part.height)
            x2 = np.zeros(part.height)
            xb = c.x_baseline(sp) if c is not None and target.key == "dropback_rate" else x1
        else:
            x1 = c.x_spread(target, sp)
            x2 = c.x_total(to, total_scale)
            xb = c.x_baseline(sp) if target.key == "dropback_rate" else np.zeros(part.height)
        parts.append(
            part.with_columns(pl.Series("x1", x1), pl.Series("x2", x2), pl.Series("xb", xb))
        )
    return pl.concat(parts)


def build_design(
    target: Target,
    obs: pl.DataFrame,
    games: pl.DataFrame,
    curves: dict[int, ScriptCurve],
    changed: pl.DataFrame,
    midseason: pl.DataFrame,
    seasons: Sequence[int],
    *,
    forecast_rows: pl.DataFrame | None = None,
    settings: dict[str, Any] | None = None,
) -> Design:
    """The arrays behind every prediction for `target` in `seasons`.

    `obs`: `observations(...)` for these seasons and the one before each (the prior).
    `games`: curated games (team view, kickoffs, lines; lines already merged for a live week).
    `forecast_rows`: (season, week, game_id, team, opponent) to forecast; default every
    observed team-game of `seasons` (the backtest). An as-of week W sees a game of week < W
    when its source is available by W's cutoff (D123: FTN 48 h after kickoff; a game without
    a kickoff time holds its FTN a week)."""
    cfg = forecast_settings(settings)
    seasons = sorted(set(seasons))
    view = _script_columns(team_view(games), target, curves, float(cfg["total_scale"]))
    vkeys = ["season", "week", "game_id", "team", "opponent"]
    vcols = [*vkeys, "kickoff_utc", "game_type", "spread", "total", "x1", "x2", "xb", "is_home"]
    venue = pl.when(pl.col("is_home")).then(pl.col("team")).otherwise(pl.col("opponent"))
    ctx = venue if target.context == "venue" else pl.col("opponent")
    view = view.select(vcols).with_columns(ctx.alias("ctx")).drop("is_home")
    ob = obs.join(view.drop("week"), on=["season", "game_id", "team", "opponent"], how="left")
    ob = ob.with_columns(
        pl.col("x1").fill_null(0.0),
        pl.col("x2").fill_null(0.0),
        pl.col("xb").fill_null(0.0),
        pl.col("ctx").fill_null(pl.col("opponent")),  # a game missing from `games`: no venue
    )
    if forecast_rows is None:
        frows = ob.filter(pl.col("season").is_in(seasons)).select(*vkeys, "k", "n")
    else:
        frows = forecast_rows.select(vkeys).join(ob.select(*vkeys, "k", "n"), on=vkeys, how="left")
    frows = frows.join(view.drop("week"), on=["season", "game_id", "team", "opponent"],
                       how="left").with_columns(
        pl.col("x1").fill_null(0.0), pl.col("x2").fill_null(0.0), pl.col("xb").fill_null(0.0),
        pl.col("n").fill_null(0).cast(pl.Int64),
        pl.col("ctx").fill_null(pl.col("opponent")),
    ).sort(vkeys)  # fmt: skip

    # as-of weeks per season and their cutoffs
    weeks = frows.select("season", pl.col("week").alias("as_of_week")).unique()
    cut_frames = []
    for (s,), w in weeks.group_by(["season"]):
        cut_frames.append(
            B.as_of_cutoffs(games, int(s), sorted(w["as_of_week"].to_list())).with_columns(
                pl.lit(int(s), dtype=pl.Int32).alias("season")
            )
        )
    cuts = (
        pl.concat(cut_frames)
        if cut_frames
        else pl.DataFrame(
            schema={
                "as_of_week": pl.Int32,
                "cutoff_utc": pl.Datetime("us", "UTC"),
                "season": pl.Int32,
            }  # fmt: skip
        )
    )

    # contributions: each observed game before W that W could see
    cur = ob.filter(pl.col("season").is_in(seasons)).join(weeks, on="season")
    cur = cur.filter(pl.col("week") < pl.col("as_of_week")).join(
        cuts, on=["season", "as_of_week"], how="left"
    )
    if target.source == "ftn":
        late = pl.col("kickoff_utc") + pl.duration(hours=L.FTN_LAG_HOURS) > pl.col("cutoff_utc")
        unknown = (pl.col("kickoff_utc").is_null() | pl.col("cutoff_utc").is_null()) & (
            pl.col("week") >= pl.col("as_of_week") - 1
        )
        cur = cur.filter(~(late.fill_null(False) | unknown))
    pre_w = float(cfg["pre_change_weight"])
    mid = midseason.select("season", "team", "change_week")

    def weight(team_col: str) -> pl.Expr:
        cw = pl.col(f"_cw_{team_col}")
        return (
            pl.when(cw.is_not_null() & (cw <= pl.col("as_of_week")) & (pl.col("week") < cw))
            .then(pre_w)
            .otherwise(1.0)
        )

    cur = (
        cur.join(mid.rename({"change_week": "_cw_team"}), on=["season", "team"], how="left")
        .join(
            mid.rename({"team": "ctx", "change_week": "_cw_ctx"}),
            on=["season", "ctx"],
            how="left",
        )
        .with_columns(
            weight("team").alias("w_team"),
            # a venue's stats crew doesn't change with its head coach
            (weight("ctx") if target.context == "opponent" else pl.lit(1.0)).alias("w_opp"),
        )
    )

    # the grid: every (season, team, as-of week) a row or a residual needs
    gkeys = ["season", "team", "as_of_week"]
    need = pl.concat(
        [
            frows.select("season", "team", pl.col("week").alias("as_of_week")),
            frows.select("season", pl.col("ctx").alias("team"), pl.col("week").alias("as_of_week")),
            cur.select("season", "team", "as_of_week"),
            cur.select("season", pl.col("ctx").alias("team"), "as_of_week"),
        ]  # fmt: skip
    ).unique()
    grid = need.sort(gkeys).with_row_index("gi")
    side = target.side
    chg = changed.filter(pl.col("side") == side).select("season", "team", "changed")
    grid = grid.join(chg, on=["season", "team"], how="left").with_columns(
        pl.col("changed").fill_null(False)
    )
    st = grid.select("season", "team").unique().sort("season", "team").with_row_index("st")
    grid = grid.join(st, on=["season", "team"], how="left")
    lw = grid.select("season", "as_of_week").unique().sort("season", "as_of_week")
    lw = lw.with_row_index("lw")
    ls = lw.select("season").unique().sort("season").with_row_index("ls")
    lw = lw.join(ls, on="season")
    grid = grid.join(lw, on=["season", "as_of_week"], how="left").sort("gi")
    G = grid.height

    def by_grid(df: pl.DataFrame, cols: dict[str, pl.Expr]) -> dict[str, np.ndarray]:
        agg = df.group_by(gkeys).agg(**cols)
        j = grid.select("gi", *gkeys).join(agg, on=gkeys, how="left").sort("gi")
        return {k: j[k].fill_null(0.0).cast(pl.Float64).to_numpy() for k in cols}

    wt = pl.col("w_team")
    a: dict[str, np.ndarray] = by_grid(
        cur,
        {
            "K": (wt * pl.col("k")).sum(),
            "N": (wt * pl.col("n")).sum(),
            "X1": (wt * pl.col("n") * pl.col("x1")).sum(),
            "X2": (wt * pl.col("n") * pl.col("x2")).sum(),
            "K0": pl.col("k").sum(),
            "N0": pl.col("n").sum(),
            "G0": pl.len(),
        },
    )
    # the league as of each week (every available game, unweighted)
    lg = cur.group_by("season", "as_of_week").agg(
        pl.col("k").sum().alias("KL"),
        pl.col("n").sum().alias("NL"),
        (pl.col("n") * pl.col("x1")).sum().alias("X1L"),
        (pl.col("n") * pl.col("x2")).sum().alias("X2L"),
        pl.len().alias("GL"),
    )
    lgj = lw.join(lg, on=["season", "as_of_week"], how="left").sort("lw")
    for c in ("KL", "NL", "X1L", "X2L", "GL"):
        a[c] = lgj[c].fill_null(0.0).cast(pl.Float64).to_numpy()
    a["lw_ls"] = lgj["ls"].cast(pl.Int64).to_numpy()

    # last season: each team's full season (prior) and the league's
    prev = ob.with_columns((pl.col("season") + 1).cast(pl.Int32).alias("season"))
    prev = prev.filter(pl.col("season").is_in(seasons))
    pt = prev.group_by("season", "team").agg(
        pl.col("k").sum().alias("Kp"),
        pl.col("n").sum().alias("Np"),
        (pl.col("n") * pl.col("x1")).sum().alias("X1p"),
        (pl.col("n") * pl.col("x2")).sum().alias("X2p"),
        pl.len().alias("Gp"),
    )
    stj = st.join(pt, on=["season", "team"], how="left").sort("st")
    for c in ("Kp", "Np", "X1p", "X2p", "Gp"):
        a[c] = stj[c].fill_null(0.0).cast(pl.Float64).to_numpy()
    pl_ = prev.group_by("season").agg(
        pl.col("k").sum().alias("KLp"),
        pl.col("n").sum().alias("NLp"),
        (pl.col("n") * pl.col("x1")).sum().alias("X1Lp"),
        (pl.col("n") * pl.col("x2")).sum().alias("X2Lp"),
        pl.len().alias("GLp"),
    )
    lsj = ls.join(pl_, on="season", how="left").sort("ls")
    for c in ("KLp", "NLp", "X1Lp", "X2Lp", "GLp"):
        a[c] = lsj[c].fill_null(0.0).cast(pl.Float64).to_numpy()
    a["g_st"] = grid["st"].cast(pl.Int64).to_numpy()
    a["g_lw"] = grid["lw"].cast(pl.Int64).to_numpy()
    a["g_ls"] = grid["ls"].cast(pl.Int64).to_numpy()
    a["g_changed"] = grid["changed"].to_numpy().astype(bool)

    # residual rows: a game of T before W, for its context's effect (the opponent O: what O
    # allows; the venue: what its stats crew charts)
    gidx = grid.select(*gkeys, "gi")
    r = cur.join(gidx.rename({"gi": "r_tg"}), on=gkeys, how="left").join(
        gidx.rename({"team": "ctx", "gi": "r_og"}),
        on=["season", "ctx", "as_of_week"],
        how="left",
    )
    a["r_tg"] = r["r_tg"].cast(pl.Int64).to_numpy()
    a["r_og"] = r["r_og"].cast(pl.Int64).to_numpy()
    for c, src in (("r_k", "k"), ("r_n", "n"), ("r_x1", "x1"), ("r_x2", "x2"), ("r_w", "w_opp")):
        a[c] = r[src].cast(pl.Float64).to_numpy()
    # last season's residual rows: O's games of S-1 (T's prior aggregates = its S-1 season)
    sti = st.select("season", "team", "st")
    pr = prev.join(sti.rename({"st": "p_tst"}), on=["season", "team"], how="inner").join(
        sti.rename({"team": "ctx", "st": "p_st"}), on=["season", "ctx"], how="inner"
    )
    a["p_tst"] = pr["p_tst"].cast(pl.Int64).to_numpy()
    a["p_st"] = pr["p_st"].cast(pl.Int64).to_numpy()
    for c, src in (("p_k", "k"), ("p_n", "n"), ("p_x1", "x1"), ("p_x2", "x2")):
        a[c] = pr[src].cast(pl.Float64).to_numpy()

    # the rows to forecast
    f = frows.join(
        gidx.rename({"as_of_week": "week", "gi": "f_gT"}), on=["season", "team", "week"],
        how="left",
    ).join(
        gidx.rename({"team": "ctx", "as_of_week": "week", "gi": "f_gO"}),
        on=["season", "ctx", "week"], how="left",
    ).sort(vkeys)  # fmt: skip
    a["f_gT"] = f["f_gT"].cast(pl.Int64).to_numpy()
    a["f_gO"] = f["f_gO"].cast(pl.Int64).to_numpy()
    for c, src in (("f_x1", "x1"), ("f_x2", "x2"), ("f_xb", "xb")):
        a[c] = f[src].cast(pl.Float64).to_numpy()
    a["f_n"] = f["n"].cast(pl.Float64).to_numpy()
    a["f_k"] = f["k"].cast(pl.Float64).fill_null(float("nan")).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        a["f_y"] = np.where(a["f_n"] > 0, a["f_k"] / a["f_n"], np.nan)
    a["G"] = np.array([G])
    return Design(
        target=target,
        rows=f.drop("f_gT", "f_gO"),
        grid=grid,
        a=a,
        meta={"seasons": seasons, "cutoffs": cuts},
    )


# --- predictions ---------------------------------------------------------------------------------
def _div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)


@dataclass
class Parts:
    """A prediction's pieces per forecast row (the pages explain a forecast with them)."""

    forecast: np.ndarray
    team: np.ndarray  # the team term
    opponent: np.ndarray  # lam * the opponent's effect
    script: np.ndarray  # b_spread * x1 + b_total * x2
    prior: np.ndarray  # the team's prior (m0)
    strength: np.ndarray  # kappa + n (plays behind the team term)
    league: np.ndarray  # the league rate as of the week


def predict_parts(d: Design, kind: str, p: dict[str, float], cfg: dict[str, Any] | None = None):
    """The forecast of one predictor for every row of the design (NaN where undefined, e.g.
    an FTN target's first week with no league rate yet)."""
    a = d.a
    t = d.target
    cfg = cfg or DEFAULTS
    kl = float(cfg.get("league_prior_plays", DEFAULTS["league_prior_plays"]))
    script = kind == "model" and t.script
    bs = float(p.get("b_spread", 0.0)) if script else 0.0
    bt = float(p.get("b_total", 0.0)) if script else 0.0

    # the league as of each week, blended with last season's
    KLa = a["KL"] - bs * a["X1L"] - bt * a["X2L"]
    Lp = _div(a["KLp"] - bs * a["X1Lp"] - bt * a["X2Lp"], a["NLp"])  # per season
    Lp_w = Lp[a["lw_ls"]]
    Lcur = np.where(
        np.isfinite(Lp_w), (kl * np.nan_to_num(Lp_w) + KLa) / (kl + a["NL"]), _div(KLa, a["NL"])
    )
    f_lw = a["g_lw"][a["f_gT"]]
    league = Lcur[f_lw]
    # the same league rate without any script adjustment (the baselines)
    Lp_raw = _div(a["KLp"], a["NLp"])[a["lw_ls"]]
    Lraw = np.where(
        np.isfinite(Lp_raw),
        (kl * np.nan_to_num(Lp_raw) + a["KL"]) / (kl + a["NL"]),
        _div(a["KL"], a["NL"]),
    )[f_lw]

    if kind == "b1":
        xb = a["f_xb"] if t.key == "dropback_rate" else np.zeros_like(Lraw)
        fc = Lraw + xb
        z = np.zeros_like(fc)
        return Parts(_clip(fc, t), Lraw, z, xb, Lraw, z, Lraw)
    if kind == "b2":
        gT = a["f_gT"]
        st = a["g_st"][gT]
        own = _div(a["K0"][gT], a["N0"][gT])
        last = _div(a["Kp"][st], a["Np"][st])
        fc = np.where(np.isfinite(own), own, np.where(np.isfinite(last), last, Lraw))
        z = np.zeros_like(fc)
        return Parts(fc, fc, z, z, fc, a["N0"][gT], Lraw)

    # the team term (b3, b4, model)
    kappa = float(p["kappa"])
    Kadj = a["K"] - bs * a["X1"] - bt * a["X2"]
    r_prev = _div(a["Kp"] - bs * a["X1p"] - bt * a["X2p"], a["Np"])  # per st
    Lc_g = Lcur[a["g_lw"]]
    Lp_g = Lp[a["g_ls"]]
    rp_g = r_prev[a["g_st"]]
    rho = np.where(a["g_changed"], float(p["rho_new"]), float(p["rho_same"]))
    has_prev = np.isfinite(rp_g) & np.isfinite(Lp_g)
    m0 = np.where(has_prev, Lc_g + rho * (np.nan_to_num(rp_g) - np.nan_to_num(Lp_g)), Lc_g)
    post = (kappa * m0 + Kadj) / (kappa + a["N"])

    gT, gO = a["f_gT"], a["f_gO"]
    team = post[gT]
    opp = np.zeros_like(team)
    if kind in ("b4", "model"):
        G = int(a["G"][0])
        res = (a["r_k"] - bs * a["r_n"] * a["r_x1"] - bt * a["r_n"] * a["r_x2"]) - a["r_n"] * post[
            a["r_tg"]
        ]
        res = np.where(np.isfinite(res), res, 0.0)
        Esum = np.bincount(a["r_og"], weights=a["r_w"] * res, minlength=G)
        En = np.bincount(a["r_og"], weights=a["r_w"] * a["r_n"], minlength=G)
        pres = (a["p_k"] - bs * a["p_n"] * a["p_x1"] - bt * a["p_n"] * a["p_x2"]) - a[
            "p_n"
        ] * r_prev[a["p_tst"]]
        pres = np.where(np.isfinite(pres), pres, 0.0)
        nst = len(a["Kp"])
        e_num = np.bincount(a["p_st"], weights=pres, minlength=nst)
        e_den = np.bincount(a["p_st"], weights=a["p_n"], minlength=nst)
        eprev = np.nan_to_num(_div(e_num, e_den))
        ko, ro = float(p["kappa_opp"]), float(p["rho_opp"])
        E = (ko * ro * eprev[a["g_st"]] + Esum) / (ko + En)
        opp = float(p["lam"]) * E[gO]
    scr = bs * a["f_x1"] + bt * a["f_x2"]
    fc = team + opp + scr
    return Parts(_clip(fc, t), team, opp, scr, m0[gT], kappa + a["N"][gT], league)


def _clip(x: np.ndarray, t: Target) -> np.ndarray:
    return np.where(np.isfinite(x), np.clip(x, t.lo, t.hi), np.nan)


def predict(d: Design, kind: str, p: dict[str, float], cfg: dict[str, Any] | None = None):
    return predict_parts(d, kind, p, cfg).forecast


# --- fitting -------------------------------------------------------------------------------------
def objective(d: Design, kind: str, p: dict[str, float], mask: np.ndarray, cfg=None) -> float:
    """Per-play squared error: sum of n * (forecast - game rate)^2 / sum of n (for a share,
    the per-play Brier score up to a constant)."""
    f = predict(d, kind, p, cfg)
    m = mask & np.isfinite(f) & np.isfinite(d.y) & (d.n > 0)
    if not m.any():
        return float("inf")
    return float(np.sum(d.n[m] * (f[m] - d.y[m]) ** 2) / np.sum(d.n[m]))


def fit_kind(
    d: Design, kind: str, mask: np.ndarray, cfg: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Fit one predictor's parameters on the rows in `mask` (L-BFGS-B from a few starts on the
    optimizer's scale). Returns {"params": ..., "loss": ..., "rows": ...}."""
    from scipy.optimize import minimize

    names = kind_params(d.target, kind)
    if not names:
        return {"params": {}, "loss": objective(d, kind, {}, mask, cfg), "rows": int(mask.sum())}
    specs = [PARAMS[n] for n in names]

    def to_p(theta: np.ndarray) -> dict[str, float]:
        return {s.name: s.to_model(float(v)) for s, v in zip(specs, theta, strict=True)}

    def obj(theta: np.ndarray) -> float:
        v = objective(d, kind, to_p(theta), mask, cfg)
        return v if math.isfinite(v) else 1e9

    starts = []
    for i in range(max(len(s.start) for s in specs)):
        starts.append(np.array([s.start[min(i, len(s.start) - 1)] for s in specs]))
    best = None
    for x0 in starts:
        res = minimize(obj, x0, method="L-BFGS-B", bounds=[(s.lo, s.hi) for s in specs],
                       options={"maxiter": 400})  # fmt: skip
        if best is None or res.fun < best.fun:
            best = res
    assert best is not None
    params = to_p(best.x)
    return {
        "params": {k: round(v, 6) for k, v in params.items()},
        "loss": float(best.fun),
        "rows": int(mask.sum()),
        "at_bound": [
            s.name for s, v in zip(specs, best.x, strict=True) if min(v - s.lo, s.hi - v) < 1e-3
        ],  # fmt: skip
    }


# --- the range ----------------------------------------------------------------------------------
DEFAULT_PLAYS = 30.0  # the last fallback for a game's plays (no team or league history yet)


def expected_plays(d: Design) -> np.ndarray:
    """The plays a team's next game will likely have for this target: its average so far this
    season, else last season's, else the league's per team-game as of the week (what the week
    could see), else last season's league, else `DEFAULT_PLAYS`. Never the rows' own actual
    plays: a forecast's range uses nothing from its week or later (Sol review, D131)."""
    a = d.a
    gT = a["f_gT"]
    st = a["g_st"][gT]
    lw = a["g_lw"][gT]
    own = _div(a["N0"][gT], a["G0"][gT])
    last = _div(a["Np"][st], a["Gp"][st])
    league_now = _div(a["NL"], a["GL"])[lw]
    league_last = _div(a["NLp"], a["GLp"])[a["lw_ls"]][lw]
    lg = np.where(
        np.isfinite(league_now),
        league_now,
        np.where(np.isfinite(league_last), league_last, DEFAULT_PLAYS),
    )
    return np.where(np.isfinite(own), own, np.where(np.isfinite(last), last, lg))


def _range_basis(t: Target, f: np.ndarray, n: np.ndarray, strength: np.ndarray) -> np.ndarray:
    u = f * (1 - f) if t.share else np.ones_like(f)
    with np.errstate(divide="ignore", invalid="ignore"):
        return u * (1.0 / np.maximum(n, 1.0) + 1.0 / np.maximum(strength, 1.0))


def _range_sd(d: Design, parts: Parts, rng: dict[str, float], n: np.ndarray) -> np.ndarray:
    var = rng["c"] * _range_basis(d.target, parts.forecast, n, parts.strength) + rng["b"]
    return np.sqrt(np.maximum(var, 0.0))


def fit_range(d: Design, parts: Parts, mask: np.ndarray) -> dict[str, float]:
    """The predictive spread of a game's rate around the forecast: var = c * u * (1/n + 1/
    (kappa + n_so_far)) + b, u = f(1 - f) for a share (1 for a mean): sampling noise in the
    game, the posterior's own uncertainty (the beta posterior's variance for a share) and
    real game-to-game movement `b`. Least squares on the squared errors of the tuning rows
    (c, b >= 0). The range's two ends are then the 10th and 90th percentiles of the tuning
    rows' errors in those units, with the plays a forecast expects (a skewed rate, like heavy
    boxes, gets a lopsided range): 80% of the tuning games land inside by construction, so
    the later seasons' coverage is the honest check."""
    from scipy.optimize import nnls

    f = parts.forecast
    m = mask & np.isfinite(f) & np.isfinite(d.y) & (d.n > 0)
    h = _range_basis(d.target, f[m], d.n[m], parts.strength[m])
    r2 = (d.y[m] - f[m]) ** 2
    A = np.column_stack([h, np.ones_like(h)])
    (c, b), _ = nnls(A, r2)
    rng = {"c": float(c), "b": float(b), "q_lo": -RANGE_Z, "q_hi": RANGE_Z}
    sd = _range_sd(d, parts, rng, expected_plays(d))[m]
    ok = sd > 0
    if ok.sum() >= 20:
        z = (d.y[m][ok] - f[m][ok]) / sd[ok]
        rng["q_lo"] = float(np.quantile(z, (1 - RANGE_LEVEL) / 2))
        rng["q_hi"] = float(np.quantile(z, 1 - (1 - RANGE_LEVEL) / 2))
    return rng


def predict_range(d: Design, parts: Parts, rng: dict[str, float], n: np.ndarray | None = None):
    """(lo, hi): the 80% range of the game's rate, for `n` plays (default the expected)."""
    n = expected_plays(d) if n is None else n
    f = parts.forecast
    sd = _range_sd(d, parts, rng, n)
    t = d.target
    lo = f + float(rng.get("q_lo", -RANGE_Z)) * sd
    hi = f + float(rng.get("q_hi", RANGE_Z)) * sd
    return np.clip(lo, t.lo, t.hi), np.clip(hi, t.lo, t.hi)


# --- scoring -------------------------------------------------------------------------------------
def score(
    t: Target, y: np.ndarray, n: np.ndarray, k: np.ndarray, f: np.ndarray
) -> dict[str, float]:
    """Game-level MAE (each team-game counts once), play-weighted MAE, RMSE, and for a share
    the per-play Brier and log loss (every play of the game scored at the forecast rate)."""
    m = np.isfinite(f) & np.isfinite(y) & (n > 0)
    out: dict[str, float] = {"games": int(m.sum()), "plays": float(n[m].sum())}
    if not m.any():
        return out
    e = f[m] - y[m]
    out["mae"] = float(np.mean(np.abs(e)))
    out["wmae"] = float(np.sum(n[m] * np.abs(e)) / np.sum(n[m]))
    out["rmse"] = float(np.sqrt(np.mean(e**2)))
    out["bias"] = float(np.mean(e))
    if t.share:
        p = np.clip(f[m], 1e-4, 1 - 1e-4)
        kk, nn = k[m], n[m]
        out["brier"] = float(np.sum(kk * (1 - p) ** 2 + (nn - kk) * p**2) / np.sum(nn))
        out["logloss"] = float(-np.sum(kk * np.log(p) + (nn - kk) * np.log(1 - p)) / np.sum(nn))
    return out


def calibration(t: Target, y: np.ndarray, n: np.ndarray, f: np.ndarray, bins: int = 10):
    """Equal-count bins of the forecast: mean forecast vs the pooled actual rate (play-weighted),
    and the play-weighted calibration error."""
    m = np.isfinite(f) & np.isfinite(y) & (n > 0)
    if m.sum() < bins:
        return [], float("nan")
    order = np.argsort(f[m], kind="stable")
    ff, yy, nn = f[m][order], y[m][order], n[m][order]
    edges = np.linspace(0, len(ff), bins + 1).astype(int)
    rows = []
    err = 0.0
    for i in range(bins):
        s = slice(edges[i], edges[i + 1])
        w = nn[s]
        if w.sum() <= 0:
            continue
        mf = float(np.sum(w * ff[s]) / w.sum())
        my = float(np.sum(w * yy[s]) / w.sum())
        rows.append({"bin": i, "forecast": mf, "actual": my, "games": int(len(w)),
                     "plays": float(w.sum())})  # fmt: skip
        err += float(w.sum()) * abs(mf - my)
    return rows, err / float(nn.sum())


def part_of(week: int) -> str:
    for lo, hi, name in PARTS:
        if lo <= week <= hi:
            return name
    return PARTS[-1][2]


def ship_verdict(
    t: Target,
    by_season: dict[int, dict[str, dict[str, float]]],
    pooled: dict[str, dict],
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The pre-registered rule (D130): (a) model MAE < season-to-date MAE in >= 5 of 7 seasons
    (FTN targets: >= 3 of 4), a tie at 4 decimals not counting as a win; (b) pooled model MAE
    no worse than the shrunk team rate's, compared exactly (no rounding: Sol review, D131)."""
    rules = rules or DEFAULTS["ship"]
    need = int(rules["seasons_ftn"] if t.source == "ftn" else rules["seasons_pbp"])
    wins = []
    for s, m in sorted(by_season.items()):
        a, b = m.get("model", {}).get("mae"), m.get("b2", {}).get("mae")
        if a is None or b is None:
            continue
        wins.append({"season": s, "win": round(a, 4) < round(b, 4), "model": a, "b2": b})
    n_wins = sum(w["win"] for w in wins)
    pm, p3 = pooled.get("model", {}).get("mae"), pooled.get("b3", {}).get("mae")
    rule_b = pm is not None and p3 is not None and pm <= p3  # exact (Sol review, D131)
    return {
        "target": t.key,
        "seasons": len(wins),
        "wins_vs_b2": n_wins,
        "need": need,
        "rule_a": n_wins >= need,
        "rule_b": bool(rule_b),
        "ships": bool(n_wins >= need and rule_b),
        "by_season": wins,
    }
