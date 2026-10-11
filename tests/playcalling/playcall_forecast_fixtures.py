"""Shared builders for the PC02 forecast tests (no conftest, no __init__: imported by basename).

- A synthetic league: games (kickoffs, lines, coaches) and the PC00 team-game sums, as the
  `num` / `den` frame `forecast.load_team_games` returns, for any of `forecast.TARGETS`.
- `Ref`: a plain-Python reference of the forecast (b1 / b2 / b3 / b4 / model), written from the
  module docstring's formulas with loops and dicts instead of the design's grid arrays, so the
  vectorised code is checked against an independent computation.
- Writers for a fake `playcalling/` + `curated/` tree on a tmp `DataPaths`.

Nothing here touches D: or the network.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

import nflengine.playcalling.forecast as F
from nflengine.paths import DataPaths

ET = ZoneInfo("America/New_York")
TEAMS8 = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH")
TEAMS6 = TEAMS8[:6]
TEAMS10 = (*TEAMS8, "III", "JJJ")
SUNDAYS = {
    2020: dt.date(2020, 9, 13),
    2021: dt.date(2021, 9, 12),
    2022: dt.date(2022, 9, 11),
    2023: dt.date(2023, 9, 10),
    2024: dt.date(2024, 9, 8),
    2025: dt.date(2025, 9, 7),
}

# model-scale parameters (what `fit_kind` returns / params.json stores)
P = {
    "kappa": 150.0,
    "rho_same": 0.55,
    "rho_new": 0.2,
    "kappa_opp": 400.0,
    "rho_opp": 0.35,
    "lam": 0.8,
    "b_spread": 0.0,
    "b_total": 0.0,
}
P_SCRIPT = {**P, "b_spread": 0.9, "b_total": 0.03}

# each predictor has its own fitted parameters (b3 and b4 differ from the model's `P_SCRIPT`)
KIND_TWEAK = {
    "b3": {"kappa": 120.0, "rho_same": 0.5, "rho_new": 0.15},
    "b4": {"kappa": 130.0, "rho_same": 0.52, "rho_new": 0.22, "kappa_opp": 350.0, "rho_opp": 0.3,
           "lam": 0.6},
}  # fmt: skip

GAME_SCHEMA = {
    "season": pl.Int32,
    "week": pl.Int32,
    "game_id": pl.String,
    "game_type": pl.String,
    "home_team": pl.String,
    "away_team": pl.String,
    "result": pl.Int32,
    "spread_line": pl.Float64,
    "total_line": pl.Float64,
    "home_coach": pl.String,
    "away_coach": pl.String,
    "kickoff_utc": pl.Datetime("us", "UTC"),
    "gameday": pl.String,
}
TG_SCHEMA = {
    "season": pl.Int32,
    "week": pl.Int32,
    "game_id": pl.String,
    "team": pl.String,
    "opponent": pl.String,
    "side": pl.String,
    "metric": pl.String,
    "situation": pl.String,
    "num": pl.Float64,
    "den": pl.Int64,
}
CHANGED_SCHEMA = {
    "season": pl.Int32,
    "team": pl.String,
    "side": pl.String,
    "changed": pl.Boolean,
}
MID_SCHEMA = {"season": pl.Int32, "team": pl.String, "change_week": pl.Int32}


# --- schedule, kickoffs, games -------------------------------------------------------------------
def circle_rounds(teams: Sequence[str]) -> list[list[tuple[str, str]]]:
    t = list(teams)
    n = len(t)
    out = []
    for _ in range(n - 1):
        out.append([(t[i], t[n - 1 - i]) for i in range(n // 2)])
        t = [t[0], t[-1], *t[1:-1]]
    return out


def schedule(teams: Sequence[str], seasons: Sequence[int], weeks: int) -> list[tuple]:
    """(season, week, away, home) for every game: a round robin repeated, home side alternating."""
    rounds = circle_rounds(teams)
    out = []
    for s in seasons:
        for w in range(1, weeks + 1):
            for i, (a, b) in enumerate(rounds[(w - 1 + s) % len(rounds)]):
                home, away = (a, b) if (w + i + s) % 2 == 0 else (b, a)
                out.append((s, w, away, home))
    return out


def game_id(season: int, week: int, away: str, home: str) -> str:
    return f"{season}_{week:02d}_{away}_{home}"


def sunday_kickoff(season: int, week: int, hour: int = 13, minute: int = 0) -> dt.datetime:
    day = SUNDAYS[season] + dt.timedelta(days=7 * (week - 1))
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET).astimezone(dt.UTC)


def monday_kickoff(season: int, week: int) -> dt.datetime:
    return sunday_kickoff(season, week, 20, 15) + dt.timedelta(days=1)


def game_rows(
    sched: Sequence[tuple],
    *,
    spread: Callable[[dict], float | None] | None = None,
    total: Callable[[dict], float | None] | None = None,
    kickoff: Callable[[dict], dt.datetime | None] | None = None,
    played: Callable[[dict], bool] | None = None,
    coach: Callable[[int, int, str], str] | None = None,
) -> list[dict]:
    """One dict per game. `spread` is the home spread (+ = home favoured), as nflverse's
    `spread_line`; every callable gets the game dict built so far."""
    rows = []
    for s, w, away, home in sched:
        g = {
            "season": s,
            "week": w,
            "game_id": game_id(s, w, away, home),
            "game_type": "REG",
            "home_team": home,
            "away_team": away,
            "result": 3,
            "spread_line": None,
            "total_line": None,
            "home_coach": f"{home}-hc",
            "away_coach": f"{away}-hc",
            "kickoff_utc": sunday_kickoff(s, w),
            "gameday": (SUNDAYS[s] + dt.timedelta(days=7 * (w - 1))).isoformat(),
        }
        if coach:
            g["home_coach"], g["away_coach"] = coach(s, w, home), coach(s, w, away)
        if kickoff:
            g["kickoff_utc"] = kickoff(g)
        if spread:
            g["spread_line"] = spread(g)
        if total:
            g["total_line"] = total(g)
        if played and not played(g):
            g["result"] = None
        rows.append(g)
    return rows


def games_frame(rows: Sequence[dict]) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=GAME_SCHEMA)


def extra_games(
    games: list[dict], extra: Sequence[tuple], **game_kw
) -> tuple[list[dict], pl.DataFrame]:
    """`games` plus unplayed games `(season, week, away, home)`, and the forecast rows (both
    teams of each extra game) for them. A forecast row needs a game in the schedule."""
    new = game_rows(extra, **game_kw)
    for g in new:
        g["result"] = None
    rows = []
    for g in new:
        rows.append((g["season"], g["week"], g["game_id"], g["home_team"], g["away_team"]))
        rows.append((g["season"], g["week"], g["game_id"], g["away_team"], g["home_team"]))
    return [*games, *new], forecast_rows_frame(rows)


def clone(rows: Sequence[dict]) -> list[dict]:
    return [dict(r) for r in rows]


# --- team-game sums ------------------------------------------------------------------------------
def _split(k: float, parts: int) -> list[float]:
    if parts == 1:
        return [float(k)]
    first = float(math.floor(k / 2))
    return [first, float(k) - first]


def tg_frame(
    games: Sequence[dict],
    targets: Sequence[F.Target],
    counts: Callable[[str, int, int, str, str], tuple[float, int] | None],
) -> pl.DataFrame:
    """The `num` / `den` frame (`load_team_games`' shape). `counts(target, season, week, team,
    opponent)` -> (k, n) or None; `team` is whose call it is (the defense, for a defense target)."""
    rows = []
    for g in games:
        for team, opp in (
            (g["home_team"], g["away_team"]),
            (g["away_team"], g["home_team"]),
        ):
            for t in targets:
                c = counts(t.key, g["season"], g["week"], team, opp)
                if c is None:
                    continue
                k, n = c
                for metric, kk in zip(t.metrics, _split(k, len(t.metrics)), strict=True):
                    rows.append(
                        {
                            "season": g["season"],
                            "week": g["week"],
                            "game_id": g["game_id"],
                            "team": team,
                            "opponent": opp,
                            "side": t.side,
                            "metric": metric,
                            "situation": t.situation,
                            "num": kk,
                            "den": int(n),
                        }
                    )
    return pl.DataFrame(rows, schema=TG_SCHEMA)


def pc00_frame(tg: pl.DataFrame) -> pl.DataFrame:
    """The same rows in PC00's `team_game_tendencies` shape (`n`, `value`)."""
    return tg.select(
        "season",
        "week",
        "game_id",
        "team",
        "opponent",
        "side",
        "metric",
        "situation",
        pl.col("den").alias("n"),
        pl.when(pl.col("den") > 0).then(pl.col("num") / pl.col("den")).alias("value"),
    )


def changed_frame(rows: Sequence[tuple] = ()) -> pl.DataFrame:
    """(season, team, side, changed) rows."""
    return pl.DataFrame(
        [dict(zip(("season", "team", "side", "changed"), r, strict=True)) for r in rows],
        schema=CHANGED_SCHEMA,
    )


def mid_frame(rows: Sequence[tuple] = ()) -> pl.DataFrame:
    """(season, team, change_week) rows."""
    return pl.DataFrame(
        [dict(zip(("season", "team", "change_week"), r, strict=True)) for r in rows],
        schema=MID_SCHEMA,
    )


# --- script curves -------------------------------------------------------------------------------
def linear_curve(
    season: int,
    *,
    db_slope: float = -0.004,
    proe_slope: float = -0.003,
    xp_slope: float = -0.002,
    db_mean: float = 0.55,
    proe_mean: float = 0.0,
    xp_mean: float = 0.55,
    total_mean: float = 45.0,
) -> F.ScriptCurve:
    """A curve linear in the spread: x_spread = slope * spread exactly (inside +-30)."""
    g = list(F.SPREAD_GRID)
    return F.ScriptCurve(
        season=season,
        grid=g,
        db=[db_mean + db_slope * x for x in g],
        proe=[proe_mean + proe_slope * x for x in g],
        xp=[xp_mean + xp_slope * x for x in g],
        db_mean=db_mean,
        proe_mean=proe_mean,
        xp_mean=xp_mean,
        total_mean=total_mean,
        ref_seasons=[season - 3, season - 2, season - 1],
        model="test curve",
        n_plays=1000,
    )


def flat_curve(season: int, **kw) -> F.ScriptCurve:
    return linear_curve(season, db_slope=0.0, proe_slope=0.0, xp_slope=0.0, **kw)


# --- a random league -----------------------------------------------------------------------------
BASE_RATE = {
    "proe": 0.0,
    "dropback_rate": 0.58,
    "deep_shot_rate": 0.12,
    "run_middle": 0.30,
    "run_outside": 0.25,
    "play_action_rate": 0.23,
    "screen_rate": 0.08,
    "motion_rate": 0.40,
    "blitz_rate": 0.30,
    "rushers_avg": 4.3,
    "heavy_box_rate": 0.20,
}
EFFECT_SD = {"share": 0.04, "mean": 0.15, "over_expected": 0.03}


@dataclass
class League:
    games: list[dict]
    tg: pl.DataFrame
    changed: pl.DataFrame
    midseason: pl.DataFrame
    curves: dict[int, F.ScriptCurve]
    teams: tuple[str, ...]
    seasons: tuple[int, ...]

    def games_df(self) -> pl.DataFrame:
        return games_frame(self.games)

    def design(self, key: str, seasons: Sequence[int] | None = None, **kw) -> F.Design:
        return make_design(
            F.get_target(key),
            self.tg,
            self.games,
            curves=kw.pop("curves", self.curves),
            changed=kw.pop("changed", self.changed),
            mid=kw.pop("mid", self.midseason),
            seasons=seasons or list(self.seasons),
            **kw,
        )


def random_league(
    seed: int = 7,
    teams: Sequence[str] = TEAMS8,
    seasons: Sequence[int] = (2022, 2023, 2024),
    weeks: int = 10,
    null_lines: float = 0.06,
    targets: Sequence[F.Target] = F.TARGETS,
    changed: Sequence[tuple] = (),
    mid: Sequence[tuple] = (),
) -> League:
    rng = np.random.default_rng(seed)
    sched = schedule(teams, seasons, weeks)

    def line(lo: int, hi: int) -> float | None:
        v = float(rng.integers(lo, hi + 1))
        return None if rng.random() < null_lines else v + (0.5 if rng.random() < 0.3 else 0.0)

    games = game_rows(
        sched,
        spread=lambda g: line(-10, 10),
        total=lambda g: line(36, 54),
    )
    effects: dict[tuple, dict[str, float]] = {}
    for t in targets:
        sd = EFFECT_SD[t.unit]
        for s in seasons:
            for kind in ("team", "opp"):
                effects[(t.key, s, kind)] = {tm: float(rng.normal(0, sd)) for tm in teams}

    def counts(key: str, s: int, w: int, team: str, opp: str):
        t = F.get_target(key)
        n = int(rng.integers(18, 46))
        mu = BASE_RATE[key] + effects[(key, s, "team")][team] + 0.5 * effects[(key, s, "opp")][opp]
        if t.unit == "share":
            return float(rng.binomial(n, float(np.clip(mu, 0.03, 0.97)))), n
        if t.unit == "mean":
            return float(n * mu + rng.normal(0, 0.5)), n
        return float(n * mu + rng.normal(0, 0.8)), n

    tg = tg_frame(games, targets, counts)
    return League(
        games,
        tg,
        changed_frame(changed),
        mid_frame(mid),
        {s: linear_curve(s) for s in seasons},
        tuple(teams),
        tuple(seasons),
    )


def make_design(
    target: F.Target,
    tg: pl.DataFrame,
    games: Sequence[dict],
    *,
    curves: dict[int, F.ScriptCurve] | None = None,
    changed: pl.DataFrame | None = None,
    mid: pl.DataFrame | None = None,
    seasons: Sequence[int],
    forecast_rows: pl.DataFrame | None = None,
    settings: dict | None = None,
) -> F.Design:
    obs = F.observations(tg, target)
    return F.build_design(
        target,
        obs,
        games_frame(games),
        curves or {},
        changed if changed is not None else changed_frame(),
        mid if mid is not None else mid_frame(),
        seasons,
        forecast_rows=forecast_rows,
        settings=settings,
    )


def forecast_rows_frame(rows: Sequence[tuple]) -> pl.DataFrame:
    """(season, week, game_id, team, opponent) rows for `forecast_rows=`."""
    return pl.DataFrame(
        [
            dict(zip(("season", "week", "game_id", "team", "opponent"), r, strict=True))
            for r in rows
        ],
        schema={
            "season": pl.Int32,
            "week": pl.Int32,
            "game_id": pl.String,
            "team": pl.String,
            "opponent": pl.String,
        },
    )


def row_of(d: F.Design, season: int, week: int, team: str) -> int:
    r = d.rows.with_row_index("_i").filter(
        (pl.col("season") == season) & (pl.col("week") == week) & (pl.col("team") == team)
    )
    assert r.height == 1, (season, week, team, r.height)
    return int(r["_i"][0])


def grid_of(d: F.Design, season: int, team: str, as_of: int) -> int:
    r = d.grid.filter(
        (pl.col("season") == season) & (pl.col("team") == team) & (pl.col("as_of_week") == as_of)
    )
    assert r.height == 1, (season, team, as_of, r.height)
    return int(r["gi"][0])


def predict_at(d: F.Design, kind: str, params: dict, season: int, week: int, team: str) -> float:
    return float(F.predict(d, kind, params)[row_of(d, season, week, team)])


# --- the reference forecast ----------------------------------------------------------------------
@dataclass(frozen=True)
class Ob:
    season: int
    week: int
    game_id: str
    team: str
    opp: str
    k: float
    n: float
    x1: float = 0.0
    x2: float = 0.0
    ctx: str = ""  # the second term's group: the opponent, or the venue (the home team)


def ref_obs(
    t: F.Target,
    tg: pl.DataFrame,
    games: Sequence[dict],
    curves: dict[int, F.ScriptCurve],
    total_scale: float = 10.0,
) -> list[Ob]:
    """One `Ob` per team-game of the target, with the game's script features."""
    lines = {}
    home = {g["game_id"]: g["home_team"] for g in games}
    for g in games:
        lines[(g["game_id"], g["home_team"])] = (g["spread_line"], g["total_line"])
        sp = g["spread_line"]
        lines[(g["game_id"], g["away_team"])] = (None if sp is None else -sp, g["total_line"])
    out = []
    for r in F.observations(tg, t).iter_rows(named=True):
        x1 = x2 = 0.0
        c = curves.get(r["season"])
        if t.script and c is not None:
            sp, to = lines.get((r["game_id"], r["team"]), (None, None))
            x1 = float(c.x_spread(t, np.array([np.nan if sp is None else sp]))[0])
            x2 = float(c.x_total(np.array([np.nan if to is None else to]), total_scale)[0])
        out.append(
            Ob(
                r["season"],
                r["week"],
                r["game_id"],
                r["team"],
                r["opponent"],
                float(r["k"]),
                float(r["n"]),
                x1,
                x2,
                home[r["game_id"]] if t.context == "venue" else r["opponent"],
            )
        )
    return out


def _nan() -> float:
    return float("nan")


def _fin(x: float) -> bool:
    return x is not None and math.isfinite(x)


@dataclass(eq=False)
class Ref:
    """The forecast in plain Python. `visible(ob, as_of_week)` says whether a game before the
    as-of week has reached the forecast (default: yes); `changed` / `mid` are dicts."""

    obs: list[Ob]
    target: F.Target
    p: dict
    kl: float = 2000.0
    pre_w: float = 0.5
    changed: dict = field(default_factory=dict)  # (season, team) -> bool
    mid: dict = field(default_factory=dict)  # (season, team) -> change week
    visible: Callable[[Ob, int], bool] = lambda ob, w: True  # noqa: E731

    # script parameters apply to the model only
    def _bs(self, kind: str) -> float:
        return float(self.p.get("b_spread", 0.0)) if kind == "model" and self.target.script else 0.0

    def _bt(self, kind: str) -> float:
        return float(self.p.get("b_total", 0.0)) if kind == "model" and self.target.script else 0.0

    def adj(self, ob: Ob, kind: str) -> float:
        return ob.k - ob.n * (self._bs(kind) * ob.x1 + self._bt(kind) * ob.x2)

    def vis(self, s: int, w: int) -> list[Ob]:
        return [o for o in self.obs if o.season == s and o.week < w and self.visible(o, w)]

    def w_team(self, o: Ob, w: int) -> float:
        cw = self.mid.get((o.season, o.team))
        return self.pre_w if cw is not None and cw <= w and o.week < cw else 1.0

    def w_opp(self, o: Ob, w: int) -> float:
        if self.target.context == "venue":  # a venue's stats crew doesn't change with its coach
            return 1.0
        cw = self.mid.get((o.season, o.ctx))
        return self.pre_w if cw is not None and cw <= w and o.week < cw else 1.0

    def _rate(self, rows: list[Ob], kind: str) -> float:
        n = sum(o.n for o in rows)
        return sum(self.adj(o, kind) for o in rows) / n if n > 0 else _nan()

    def league_prev(self, s: int, kind: str) -> float:
        return self._rate([o for o in self.obs if o.season == s - 1], kind)

    def team_prev(self, s: int, team: str, kind: str) -> float:
        return self._rate([o for o in self.obs if o.season == s - 1 and o.team == team], kind)

    def league(self, s: int, w: int, kind: str) -> float:
        vis = self.vis(s, w)
        kl_ = sum(self.adj(o, kind) for o in vis)
        nl = sum(o.n for o in vis)
        lp = self.league_prev(s, kind)
        if _fin(lp):
            return (self.kl * lp + kl_) / (self.kl + nl)
        return kl_ / nl if nl > 0 else _nan()

    @cache  # noqa: B019
    def post(self, s: int, team: str, w: int, kind: str) -> float:
        lc = self.league(s, w, kind)
        lp = self.league_prev(s, kind)
        rp = self.team_prev(s, team, kind)
        rho = self.p["rho_new"] if self.changed.get((s, team)) else self.p["rho_same"]
        m0 = lc + rho * (rp - lp) if _fin(rp) and _fin(lp) else lc
        mine = [o for o in self.vis(s, w) if o.team == team]
        k = sum(self.w_team(o, w) * self.adj(o, kind) for o in mine)
        n = sum(self.w_team(o, w) * o.n for o in mine)
        kappa = self.p["kappa"]
        return (kappa * m0 + k) / (kappa + n)

    def effect(self, s: int, ctx: str, w: int, kind: str) -> float:
        esum = en = 0.0
        for o in self.vis(s, w):
            if o.ctx != ctx:
                continue
            res = self.adj(o, kind) - o.n * self.post(s, o.team, w, kind)
            if not math.isfinite(res):
                res = 0.0
            wt = self.w_opp(o, w)
            esum += wt * res
            en += wt * o.n
        pnum = pden = 0.0
        for o in self.obs:
            if o.season != s - 1 or o.ctx != ctx:
                continue
            rp = self.team_prev(s, o.team, kind)
            res = self.adj(o, kind) - o.n * rp
            pnum += res if math.isfinite(res) else 0.0
            pden += o.n
        eprev = pnum / pden if pden > 0 else 0.0
        ko, ro = self.p["kappa_opp"], self.p["rho_opp"]
        return (ko * ro * eprev + esum) / (ko + en)

    def clip(self, x: float) -> float:
        return float("nan") if not math.isfinite(x) else min(max(x, self.target.lo), self.target.hi)

    def forecast(
        self, kind: str, s: int, w: int, team: str, ctx: str, x1: float = 0.0, x2: float = 0.0,
        xb: float = 0.0,
    ) -> float:  # fmt: skip
        if kind == "b1":
            return self.clip(self.league(s, w, "b1") + xb)
        if kind == "b2":
            mine = [o for o in self.vis(s, w) if o.team == team]
            n = sum(o.n for o in mine)
            if n > 0:
                return sum(o.k for o in mine) / n
            last = self.team_prev(s, team, "b2")
            return last if _fin(last) else self.league(s, w, "b1")
        post = self.post(s, team, w, kind)
        if kind == "b3":
            return self.clip(post)
        opp_term = self.p["lam"] * self.effect(s, ctx, w, kind)
        scr = self._bs(kind) * x1 + self._bt(kind) * x2
        return self.clip(post + opp_term + scr)


def ref_forecasts(
    d: F.Design, ref: Ref, kind: str, curves: dict[int, F.ScriptCurve], games: Sequence[dict],
    total_scale: float = 10.0,
) -> np.ndarray:  # fmt: skip
    """`ref.forecast` for every row of the design (the script features from the curve)."""
    t = d.target
    lines = {}
    home = {g["game_id"]: g["home_team"] for g in games}
    for g in games:
        lines[(g["game_id"], g["home_team"])] = (g["spread_line"], g["total_line"])
        sp = g["spread_line"]
        lines[(g["game_id"], g["away_team"])] = (None if sp is None else -sp, g["total_line"])
    out = []
    for r in d.rows.iter_rows(named=True):
        x1 = x2 = xb = 0.0
        c = curves.get(r["season"])
        sp, to = lines.get((r["game_id"], r["team"]), (None, None))
        spa = np.array([np.nan if sp is None else sp])
        if c is not None and t.script:
            x1 = float(c.x_spread(t, spa)[0])
            x2 = float(c.x_total(np.array([np.nan if to is None else to]), total_scale)[0])
            if t.key == "dropback_rate":
                xb = float(c.x_baseline(spa)[0])
        ctx = home[r["game_id"]] if t.context == "venue" else r["opponent"]
        out.append(ref.forecast(kind, r["season"], r["week"], r["team"], ctx, x1, x2, xb))
    return np.array(out, dtype=float)


# --- a fake tree on a tmp DataPaths --------------------------------------------------------------
def write_tree(
    root: Path,
    games: Sequence[dict],
    tg: pl.DataFrame,
    *,
    build: dict[int, dict] | None = None,
    keep: Callable[[pl.DataFrame], pl.Expr] | None = None,
    lines: pl.DataFrame | None = None,
) -> DataPaths:
    """curated/games.parquet, playcalling/<S>/team_game_tendencies.parquet (+ build.json when
    `build` has the season). `keep(tg)` -> a filter expression drops rows (a live week)."""
    cur = root / "curated"
    cur.mkdir(parents=True, exist_ok=True)
    games_frame(games).write_parquet(cur / "games.parquet")
    if lines is not None:
        lines.write_parquet(cur / "lines.parquet")
    frame = tg.filter(keep(tg)) if keep else tg
    for s in sorted(set(frame["season"].to_list())):
        folder = root / "playcalling" / str(s)
        folder.mkdir(parents=True, exist_ok=True)
        pc00_frame(frame.filter(pl.col("season") == s)).write_parquet(
            folder / "team_game_tendencies.parquet"
        )
    for s, doc in (build or {}).items():
        folder = root / "playcalling" / str(s)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "build.json").write_text(json.dumps(doc), encoding="utf-8")
    return DataPaths(root)


VERSION = (
    "2019-2023_20261011T221755Z"  # <first>-<last>_<UTC stamp>: the only shape load_model takes
)
FIT_SEASONS = (2019, 2020, 2021, 2022, 2023)  # a live 2024 forecast is out of sample


def model_doc(
    keys: Sequence[str],
    *,
    version: str = VERSION,
    fit_seasons: Sequence[int] = FIT_SEASONS,
    shipped: Sequence[str] = (),
    params: dict | None = None,
    rng: dict | None = None,
) -> dict:
    """A params.json for `run_forecast` (model-scale parameters, a fixed range)."""
    p = dict(params or P_SCRIPT)
    r = dict(rng or {"c": 0.8, "b": 0.0004, "q_lo": -1.25, "q_hi": 1.3})
    targets = {}
    for k in keys:
        t = F.get_target(k)
        targets[k] = {
            "label": t.label,
            "short": t.short,
            "side": t.side,
            "unit": t.unit,
            "per": t.per,
            "shipped": k in shipped,
            "fit_seasons": list(fit_seasons),
            "kinds": {
                kind: {
                    "params": {
                        n: {**p, **KIND_TWEAK.get(kind, {})}[n] for n in F.kind_params(t, kind)
                    },
                    "loss": 0.01,
                }
                for kind in ("b3", "b4", "model")
            },
            "range": r,
        }
    return {
        "family": "playcall-model",
        "version": version,
        "schema_version": F.SCHEMA_VERSION,
        "settings": F.forecast_settings(),
        "targets": targets,
        "shipped": list(shipped),
        "backtest_run": None,
    }


def write_model(paths: DataPaths, doc: dict, *, pointer: bool = True) -> Path:
    folder = paths.models / "playcall" / doc["version"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "params.json").write_text(json.dumps(doc), encoding="utf-8")
    if pointer:
        (paths.models / "playcall" / "production.json").write_text(
            json.dumps({"version": doc["version"]}), encoding="utf-8"
        )
    return folder


def quiet(*_a, **_k) -> None:
    return None
