"""The decision engine (LD00): go / field goal / punt on 4th down, and the 3rd-down check.

For a 4th down it returns the offense's win probability after each choice:
- **go** = sum over yards gained g of P(g) x WP(the state after): a first down where the
  play ends, a touchdown (the try at its era's extra-point rate, then the kickoff), or the
  other team's ball at the spot (a safety from the own end zone);
- **field goal** = P(make) x WP(+3, then the kickoff) + P(miss) x WP(their ball at the spot of
  the kick, 8 yards behind the line, or their 20);
- **punt** = sum over where the next possession starts of P x WP(that state).

Every state after a choice is scored by the `wp` model, except where football's rules decide:
time runs out (the score decides; at halftime the second-half kickoff follows), or the team
with the ball leads with a first down and can kneel out the clock. Each play takes 6 s.

The confidence label comes from the bootstrap refits of `gain` and `fg`: **Confident** when the
best choice wins in >= 90% of them, **Lean** at 60-90%, **Toss-up** below 60% or when the gap is
under 1 win-probability point (Brill, Yurko & Wyner, arXiv 2311.03490).
"""

from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from nflengine.live import schema
from nflengine.live.models import LiveModels, NextPossession, conversion_prob
from nflengine.live.schema import GameState

SAFETY_START = 60  # after a safety the free kick gives the other team about its own 40
KICKOFF_POINTS = 12  # a next-possession distribution is compressed to this many spots
PUNT_POINTS = 12
DEFAULT_DECIDE = {"toss_up_gap": 0.01, "confident": 0.90, "lean": 0.60, "boot_gap": 0.05}
OPTIONS = ("go", "fg", "punt")
_GAIN_INT = np.asarray(schema.GAIN_VALUES, dtype=int)
NAMES = {"go": "Go for it", "fg": "Field goal", "punt": "Punt"}


def decide_settings() -> dict[str, float]:
    from nflengine.settings import get_config

    return {**DEFAULT_DECIDE, **((get_config().live or {}).get("decide") or {})}


# --- results ----------------------------------------------------------------------------------
@dataclass
class Decision:
    """A 4th-down call from the offense's side. Win probabilities are 0-1; `gap` is the best
    option's lead over the next one in the same units (0.01 = one point)."""

    state: GameState
    wp: dict[str, float | None]
    best: str
    gap: float
    label: str | None
    boot_share: float | None
    convert: float
    fg_make: float | None
    fg_distance: float
    punt_start: float | None  # the receiving team's expected start (yards from its own goal)
    wp_now: float
    ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.as_dict()
        return d


@dataclass
class TableRow:
    """One row of the 3rd-down "if they're stopped short" table."""

    gain: int
    ydstogo: int
    yardline_100: int
    best: str
    gap: float
    label: str | None
    wp: dict[str, float | None]


@dataclass
class ThirdDown:
    state: GameState
    convert: float
    pass_prob: float
    wp_now: float
    table: list[TableRow] = field(default_factory=list)
    note: str | None = None
    ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.as_dict()
        return d


# --- leaves: what a state after a choice is worth -------------------------------------------
# A leaf is ("t", wp_offense) for a state the rules settle, or ("m", row, flip) for a state
# the wp model scores (row = its index in the batch; flip = the defense has the ball, so the
# offense's WP is 1 - the model's). An outcome is a list of (weight, leaf).


class _Batch:
    """The states one call needs from the wp model, deduplicated, scored in one predict."""

    def __init__(self, s0: GameState):
        self.s0 = s0
        self.rows: list[list[float]] = []
        self.index: dict[tuple, int] = {}
        self.era = float(s0.era)
        self.indoor = float(schema.indoor(s0.roof))
        self.p: np.ndarray | None = None

    def possession(
        self, ball: str, yl: int, diff_o: float, gs: float, hs: float, fresh: bool = False
    ) -> tuple:
        """A first down for `ball` ('o' the offense, 'd' the defense) at `yl` yards to its goal,
        with the offense's score difference `diff_o` and the clock after the play. `fresh`: a
        new half, both teams back to 3 timeouts (Sol review, LD00)."""
        s0 = self.s0
        yl = int(min(max(yl, 1), 99))
        own = 1 if ball == "o" else -1
        diff = diff_o * own
        if fresh:
            t_ball = t_other = 3
        else:
            t_ball = s0.off_timeouts if ball == "o" else s0.def_timeouts
            t_other = s0.def_timeouts if ball == "o" else s0.off_timeouts
            k = kneel_out(s0, ball, diff_o, gs)
            if k is not None:
                return ("t", k)
        key = (ball, yl, diff_o, gs, hs, fresh)
        i = self.index.get(key)
        if i is None:
            i = len(self.rows)
            self.index[key] = i
            r2h = s0.receive_2h_ko if ball == "o" else (1 - s0.receive_2h_ko)
            first_half = (not s0.ot) and gs > hs + 1  # gs = hs + 1800 in the first half
            self.rows.append(
                wp_row(
                    diff=diff, gs=gs, hs=hs, yl=yl, down=1, togo=min(10, yl),
                    off_to=t_ball, def_to=t_other, home=s0.home * own,
                    r2h=r2h if first_half else 0, spread=s0.spread * own, ot=s0.ot,
                    era=self.era, indoor=self.indoor,
                )
            )  # fmt: skip
        return ("m", i, ball == "d")

    def evaluate(self, models: LiveModels) -> None:
        self.p = models.wp_prob(np.asarray(self.rows)) if self.rows else np.zeros(0)

    def value(self, outcome: list[tuple[float, tuple]]) -> float:
        total = 0.0
        for w, leaf in outcome:
            if leaf[0] == "t":
                total += w * leaf[1]
            else:
                p = self.p[leaf[1]]
                total += w * ((1.0 - p) if leaf[2] else p)
        return total


def kneel_out(s0: GameState, ball: str, diff_o: float, gs: float) -> float | None:
    """nfl4th's kneel-out rule: in the second half, a team with a first down and the lead can
    kneel out the clock if three kneels (2 s each) and the play clock between them (40 s,
    less each timeout the other team still has) use it all up. Returns the offense's WP (1 or
    0) when that happens, else None."""
    if s0.ot or gs > 1800:
        return None
    lead = diff_o if ball == "o" else -diff_o
    other_to = s0.def_timeouts if ball == "o" else s0.off_timeouts
    if lead > 0 and gs <= 3 * 2 + 40 * max(0, 3 - other_to):
        return 1.0 if ball == "o" else 0.0
    return None


def wp_row(*, diff, gs, hs, yl, down, togo, off_to, def_to, home, r2h, spread, ot, era, indoor):
    """One wp-model feature row (the order of `schema.WP_FEATURES`; same formulas as
    `schema.state_features`, a test checks)."""
    share = 1.0 if ot else (3600.0 - gs) / 3600.0
    decay = math.exp(-4.0 * share)
    return [
        float(diff), diff / decay, float(spread), spread * decay, float(gs), float(hs),
        float(yl), float(down), float(togo), float(off_to), float(def_to), float(home),
        float(r2h), float(ot), float(era), float(indoor),
    ]  # fmt: skip


def compress(np_: NextPossession, k: int) -> list[tuple[str, int, float]]:
    """A next-possession distribution as at most `k` receiving spots (equal-probability bins,
    each at its probability-weighted mean spot) + up to 3 kept-ball spots + the return
    touchdown. WP is smooth in the spot, so the error is far below a tenth of a point."""
    sup = np_.support()
    out: list[tuple[str, int, float]] = []
    for kind, n in (("recv", k), ("keep", 3)):
        pts = sorted((s, p) for o, s, p in sup if o == kind)
        mass = sum(p for _, p in pts)
        if not pts:
            continue
        step, acc, cur_w, cur_s = mass / n, 0.0, 0.0, 0.0
        for s, p in pts:
            cur_w += p
            cur_s += s * p
            acc += p
            if acc >= step * (len([o for o in out if o[0] == kind]) + 1) - 1e-12:
                out.append((kind, int(round(cur_s / cur_w)), cur_w))
                cur_w = cur_s = 0.0
        if cur_w > 0:
            out.append((kind, int(round(cur_s / cur_w)), cur_w))
    out += [(o, s, p) for o, s, p in sup if o == "ret_td"]
    return out


class Engine:
    """Scores 4th-down calls and 3rd-down checks with one bundle of models (in memory)."""

    def __init__(self, models: LiveModels, cfg: dict[str, float] | None = None, threads: int = 4):
        self.m = models
        self.cfg = {**DEFAULT_DECIDE, **(cfg or {})}
        self._kick: dict[int, list] = {}
        self._punt: dict[int, list] = {}
        if len(models.gain_boot) != len(models.fg_boot):
            raise ValueError(
                f"the bundle has {len(models.gain_boot)} gain and {len(models.fg_boot)} fg "
                "bootstrap refits: the confidence label needs the same number of each"
            )
        self._pool = ThreadPoolExecutor(max_workers=threads) if models.gain_boot else None
        if models.fg_boot:
            if any(m.wind_fill != models.fg.wind_fill for m in models.fg_boot):
                raise ValueError("fg bootstrap refits must share the main model's wind_fill")
            self._fg_coef = np.array([m.coef for m in models.fg_boot])
            self._fg_int = np.array([m.intercept for m in models.fg_boot])

    # distributions, compressed and cached
    def kickoff_support(self, season: int) -> list[tuple[str, int, float]]:
        if season not in self._kick:
            self._kick[season] = compress(self.m.kickoff.at(season), KICKOFF_POINTS)
        return self._kick[season]

    def punt_support(self, yl: int) -> list[tuple[str, int, float]]:
        if yl not in self._punt:
            self._punt[yl] = compress(self.m.punt.at(yl), PUNT_POINTS)
        return self._punt[yl]

    # --- the states after a play -------------------------------------------------------------
    def _clock(self, s: GameState, seconds: float = schema.SECONDS_PER_PLAY):
        return max(0.0, s.game_seconds - seconds), max(0.0, s.half_seconds - seconds)

    def _after(self, b: _Batch, s: GameState, ball: str, yl: int, diff_o: float, gs, hs) -> list:
        """The outcome when `ball` gets a first down at `yl` after a play that left (gs, hs) on
        the clock: the rules first (game over, overtime over, halftime), else a possession."""
        gs, hs = self._next_period(s, diff_o, gs, hs)
        rule = self._rule(b, s, ball, diff_o, gs, hs)
        return rule if rule is not None else [(1.0, b.possession(ball, yl, diff_o, gs, hs))]

    @staticmethod
    def _next_period(s: GameState, diff_o: float, gs, hs) -> tuple[float, float]:
        """Playoff overtime doesn't end tied: a period that runs out with the score level goes on
        into a new 15-minute period (Sol review, LD00)."""
        if s.ot and s.playoffs and gs <= 0 and diff_o == 0:
            minutes = schema.ot_minutes(s.season, playoffs=True) * 60.0
            return minutes, minutes
        return gs, hs

    @staticmethod
    def _final(diff_o: float) -> tuple:
        return ("t", 1.0 if diff_o > 0 else 0.0 if diff_o < 0 else 0.5)

    def _rule(self, b: _Batch, s: GameState, ball: str, diff_o: float, gs, hs) -> list | None:
        """The outcome when football's rules settle a first down for `ball` regardless of the
        spot (time is up, halftime, a kneel-out), else None. `gs` / `hs` have been through
        `_next_period` already."""
        if (s.ot or not s.first_half) and gs <= 0:
            return [(1.0, self._final(diff_o))]
        if not s.ot and s.first_half and hs <= 0:
            return self._halftime(b, s, diff_o)
        k = kneel_out(s, ball, diff_o, gs)
        return [(1.0, ("t", k))] if k is not None else None

    def _halftime(self, b: _Batch, s: GameState, diff_o: float) -> list:
        """The second-half kickoff to the team that receives it; both teams back to 3
        timeouts."""
        recv = "o" if s.receive_2h_ko else "d"
        return self._kickoff_to(b, s, recv, diff_o, 1800.0, 1800.0, fresh=True)

    def _try_parts(self, s: GameState) -> list[tuple[float, int]]:
        """The try after a touchdown at the era's extra-point rate: (probability, points)."""
        xp = self.m.pat_rates(s.season)["xp"]
        return [(xp, 7), (1 - xp, 6)]

    def _kickoff_to(
        self, b: _Batch, s: GameState, recv: str, diff_o: float, gs, hs, fresh: bool = False
    ) -> list:
        """A kickoff received by `recv`, from the season's kickoff distribution."""
        kick = "d" if recv == "o" else "o"
        sign = 1 if recv == "o" else -1  # the receiver's points move diff_o this way
        out = []
        for kind, spot, p in self.kickoff_support(s.season):
            if kind == "recv":
                out.append((p, b.possession(recv, spot, diff_o, gs, hs, fresh)))
            elif kind == "keep":
                out.append((p, b.possession(kick, spot, diff_o, gs, hs, fresh)))
            else:  # a return touchdown + the try, then the other team gets it back
                mean = int(round(self.m.kickoff.at(s.season).mean_recv_spot()))
                for w, pts in self._try_parts(s):
                    leaf = b.possession(kick, mean, diff_o + pts * sign, gs, hs, fresh)
                    out.append((p * w, leaf))
        return out

    def _then_kick(self, b: _Batch, s: GameState, d: float, gs, hs, recv: str) -> list:
        """After a score that left the offense's difference at `d`: the game ends if time is
        up (a tied playoff overtime goes on), the half ends, or `recv` receives the kickoff."""
        gs, hs = self._next_period(s, d, gs, hs)
        if (s.ot or not s.first_half) and gs <= 0:
            return [(1.0, self._final(d))]
        if not s.ot and s.first_half and hs <= 0:
            return self._halftime(b, s, d)
        return self._kickoff_to(b, s, recv, d, gs, hs)

    def _score_then_kick(self, b: _Batch, s: GameState, diff_o: float, gs, hs, td: bool) -> list:
        """The offense scores (a touchdown with the try at its era's rate, or a field goal),
        then kicks off, unless the clock has run out."""
        parts = [(w, diff_o + pts) for w, pts in self._try_parts(s)] if td else [(1.0, diff_o + 3)]
        out = []
        for w, d in parts:
            out += [(w * q, leaf) for q, leaf in self._then_kick(b, s, d, gs, hs, "d")]
        return out

    # --- the three choices -------------------------------------------------------------------
    def _spots(self, b: _Batch, s: GameState, ball: str, diff_o: float, gs, hs, spots) -> tuple:
        """`_after` for many spots at once (the same rules for every spot): ("o", outcome) when
        the rules settle it, else ("m", row indices, flip) / ("t", value) per `possession`."""
        gs, hs = self._next_period(s, diff_o, gs, hs)
        rule = self._rule(b, s, ball, diff_o, gs, hs)
        if rule is not None:
            return ("o", rule)  # game over, overtime over, halftime or a kneel-out
        leaves = [b.possession(ball, int(y), diff_o, gs, hs) for y in spots]
        return ("m", np.array([lf[1] for lf in leaves], dtype=int), ball == "d")

    @staticmethod
    def _values(b: _Batch, handle: tuple, n: int) -> np.ndarray:
        if handle[0] == "o":
            return np.full(n, b.value(handle[1]))
        p = b.p[handle[1]]
        return (1.0 - p) if handle[2] else p

    def _go_plan(self, b: _Batch, s: GameState) -> dict:
        """Going for it, by gain class: converted (a first down where the play ends), stopped
        (their ball at the spot), a safety, or a touchdown."""
        gs, hs = self._clock(s)
        d = s.score_diff
        y2 = s.yardline_100 - _GAIN_INT  # where the ball ends, in yards to the goal
        conv = (s.ydstogo <= _GAIN_INT) & (y2 >= 1)  # y2 <= 0: legal_gain moved it to the TD
        stopped = s.ydstogo > _GAIN_INT
        safety = stopped & (y2 >= 100)
        field_ = stopped & ~safety
        plan = {
            "conv": (conv, self._spots(b, s, "o", d, gs, hs, y2[conv])),
            "stopped": (field_, self._spots(b, s, "d", d, gs, hs, 100 - y2[field_])),
            "td": self._score_then_kick(b, s, d, gs, hs, td=True),
        }
        if safety.any():
            spots = np.full(int(safety.sum()), SAFETY_START)
            plan["safety"] = (safety, self._spots(b, s, "d", d - 2, gs, hs, spots))
        return plan

    def _go_values(self, b: _Batch, plan: dict) -> np.ndarray:
        v = np.zeros(schema.N_GAIN_CLASSES)
        body = v[: schema.TD_CLASS]
        for key in ("conv", "stopped", "safety"):
            if key in plan:
                mask, handle = plan[key]
                if mask.any():
                    body[mask] = self._values(b, handle, int(mask.sum()))
        v[schema.TD_CLASS] = b.value(plan["td"])
        return v

    def _fg_outcomes(self, b: _Batch, s: GameState) -> tuple[list, list]:
        gs, hs = self._clock(s)
        make = self._score_then_kick(b, s, s.score_diff, gs, hs, td=False)
        spot = s.yardline_100 + schema.FG_MISS_SPOT_YARDS
        their = 80 if spot <= 20 else 100 - spot
        miss = self._after(b, s, "d", their, s.score_diff, gs, hs)
        return make, miss

    def _punt_outcome(self, b: _Batch, s: GameState) -> list:
        gs, hs = self._clock(s)
        out = []
        for kind, spot, p in self.punt_support(s.yardline_100):
            if kind == "recv":
                out += [
                    (p * q, leaf) for q, leaf in self._after(b, s, "d", spot, s.score_diff, gs, hs)
                ]
            elif kind == "keep":
                out += [
                    (p * q, leaf) for q, leaf in self._after(b, s, "o", spot, s.score_diff, gs, hs)
                ]
            else:  # returned for a touchdown: the other team's try, then their kickoff
                for w, pts in self._try_parts(s):
                    d = s.score_diff - pts
                    out += [(p * w * q, leaf) for q, leaf in self._then_kick(b, s, d, gs, hs, "o")]
        return out

    # --- public -----------------------------------------------------------------------------
    def _gain_matrix(self, states: list[GameState]) -> np.ndarray:
        feats = self.m.gain_features
        rows = [schema.state_features(s) for s in states]
        missing = [f for f in feats if f not in rows[0]] if rows else []
        if missing:
            raise ValueError(f"the gain model needs {missing}, which a GameState lacks")
        return np.array([[r[f] for f in feats] for r in rows], dtype=np.float64)

    def _fg_design(self, states: list[GameState]) -> tuple[np.ndarray, np.ndarray]:
        d = np.array([s.yardline_100 + schema.FG_SNAP_YARDS for s in states], dtype=float)
        X = self.m.fg.design(
            d,
            [s.era for s in states],
            [schema.indoor(s.roof) for s in states],
            [s.wind for s in states],
            [s.temp for s in states],
        )
        return d, X

    def _fg_make(self, states: list[GameState], model) -> np.ndarray:
        d, X = self._fg_design(states)
        p = 1.0 / (1.0 + np.exp(-(X @ np.asarray(model.coef) + model.intercept)))
        return np.where(d > schema.FG_MAX_DISTANCE, 0.0, p)

    def _fg_boot(self, states: list[GameState]) -> np.ndarray:
        """Every fg bootstrap refit at once: (len(states), n_boot)."""
        d, X = self._fg_design(states)
        p = 1.0 / (1.0 + np.exp(-(X @ self._fg_coef.T + self._fg_int)))
        return np.where(d[:, None] > schema.FG_MAX_DISTANCE, 0.0, p)

    def _boot(self, states: list[GameState]) -> tuple[list[np.ndarray], list[np.ndarray]]:
        if not self.m.gain_boot:
            return [], []
        X = self._gain_matrix(states)
        gains = list(
            self._pool.map(lambda i: self.m.gain_probs(X, boot=i), range(len(self.m.gain_boot)))
        )
        fg = self._fg_boot(states)
        return gains, [fg[:, b] for b in range(fg.shape[1])]

    def _decide_many(
        self, states: list[GameState], bootstrap: bool | str, b: _Batch | None = None
    ) -> list[Decision]:
        """`bootstrap`: False (no label beyond the gap rule), True (the bootstrap refits score
        only the calls closer than `boot_gap`; wider ones are Confident, D113) or "all"."""
        if not states:
            return []
        b = b or _Batch(states[0])
        plans = []
        for s in states:
            fg_ok = s.yardline_100 + schema.FG_SNAP_YARDS <= schema.FG_MAX_DISTANCE
            go = self._go_plan(b, s)
            make, miss = self._fg_outcomes(b, s) if fg_ok else ([], [])
            punt = self._punt_outcome(b, s)
            plans.append((s, fg_ok, go, make, miss, punt))
        X = self._gain_matrix(states)
        p_gain = self.m.gain_probs(X)
        p_fg = self._fg_make(states, self.m.fg)
        F = [schema.state_features(s) for s in states]
        wp_now = self.m.wp_prob(np.array([[f[k] for k in schema.WP_FEATURES] for f in F]))
        b.evaluate(self.m)
        rows = []
        for i, (_s, fg_ok, go, make, miss, punt) in enumerate(plans):
            cls_val = self._go_values(b, go)
            v_make = b.value(make) if fg_ok else None
            v_miss = b.value(miss) if fg_ok else None
            v_punt = b.value(punt)
            wp = {
                "go": float(p_gain[i] @ cls_val),
                "fg": float(p_fg[i] * v_make + (1 - p_fg[i]) * v_miss) if fg_ok else None,
                "punt": float(v_punt),
            }
            ranked = sorted(((v, k) for k, v in wp.items() if v is not None), reverse=True)
            best, gap = ranked[0][1], ranked[0][0] - ranked[1][0]
            rows.append((wp, best, gap, cls_val, v_make, v_miss, v_punt, fg_ok))
        shares: dict[int, float] = {}
        if bootstrap and self.m.gain_boot:
            close = [
                i for i, r in enumerate(rows) if bootstrap == "all" or r[2] < self.cfg["boot_gap"]
            ]
            if close:
                boots_g, boots_f = self._boot([states[i] for i in close])
                for j, i in enumerate(close):
                    _, best, _, cls_val, v_make, v_miss, v_punt, fg_ok = rows[i]
                    wins = 0
                    for g_b, f_b in zip(boots_g, boots_f, strict=True):
                        vb = {"go": float(g_b[j] @ cls_val), "punt": v_punt}
                        if fg_ok:
                            vb["fg"] = float(f_b[j] * v_make + (1 - f_b[j]) * v_miss)
                        wins += max(vb, key=vb.get) == best
                    shares[i] = wins / len(boots_g)
        out = []
        for i, (wp, best, gap, *_rest) in enumerate(rows):
            s = states[i]
            fg_ok = rows[i][7]
            share = shares.get(i)
            if bootstrap and self.m.gain_boot and i not in shares:
                label = "Toss-up" if gap < self.cfg["toss_up_gap"] else "Confident"
            else:
                label = self.label(gap, share)
            out.append(
                Decision(
                    state=s, wp=wp, best=best, gap=float(gap),
                    label=label, boot_share=share,
                    convert=float(conversion_prob(p_gain[i : i + 1], np.array([s.ydstogo]))[0]),
                    fg_make=float(p_fg[i]) if fg_ok else None,
                    fg_distance=float(s.yardline_100 + schema.FG_SNAP_YARDS),
                    punt_start=100 - self.m.punt.at(s.yardline_100).mean_recv_spot(),
                    wp_now=float(wp_now[i]),
                )
            )  # fmt: skip
        return out

    def label(self, gap: float, share: float | None) -> str | None:
        if gap < self.cfg["toss_up_gap"]:
            return "Toss-up"
        if share is None:
            return None
        if share >= self.cfg["confident"]:
            return "Confident"
        if share >= self.cfg["lean"]:
            return "Lean"
        return "Toss-up"

    def fourth_down(self, s: GameState, bootstrap: bool | str = True) -> Decision:
        if s.down != 4:
            raise ValueError("fourth_down() needs a 4th-down state")
        t = time.perf_counter()
        d = self._decide_many([s], bootstrap)[0]
        d.ms = round((time.perf_counter() - t) * 1000, 2)
        return d

    def fourth_downs(self, states: list[GameState], bootstrap: bool = False) -> list[Decision]:
        """Many independent 4th downs (backtests): one wp batch per state."""
        return [self._decide_many([s], bootstrap)[0] for s in states]

    def third_down(self, s: GameState, bootstrap: bool | str = True) -> ThirdDown:
        """The chance they convert, the chance they pass, and for every gain short of the
        line to gain the 4th-down call they'd face."""
        if s.down != 3:
            raise ValueError("third_down() needs a 3rd-down state")
        t = time.perf_counter()
        feats = schema.state_features(s)
        wp_now = float(self.m.wp_prob(np.array([[feats[f] for f in schema.WP_FEATURES]]))[0])
        p = self.m.gain_probs(self._gain_matrix([s]))
        convert = float(conversion_prob(p, np.array([s.ydstogo]))[0])
        feats["wp"] = wp_now
        pass_prob = float(self.m.pass_prob(np.array([[feats[f] for f in schema.PASS_FEATURES]]))[0])
        gs, hs = self._clock(s)
        out = ThirdDown(state=s, convert=convert, pass_prob=pass_prob, wp_now=wp_now)
        if (
            (s.ot and gs <= 0)
            or (not s.ot and s.first_half and hs <= 0)
            or (not s.ot and not s.first_half and gs <= 0)
        ):
            out.note = "The clock runs out on this play: there is no 4th down to plan."
        else:
            fourth = []
            for g in range(schema.GAIN_MIN, s.ydstogo):
                yl4 = s.yardline_100 - g
                if yl4 > 99:
                    continue
                fourth.append(
                    s.replace(down=4, ydstogo=s.ydstogo - g, yardline_100=yl4,
                              game_seconds=gs, half_seconds=hs)
                )  # fmt: skip
            for d in self._decide_many(fourth, bootstrap):
                st = d.state
                out.table.append(
                    TableRow(
                        gain=s.yardline_100 - st.yardline_100, ydstogo=st.ydstogo,
                        yardline_100=st.yardline_100, best=d.best, gap=d.gap, label=d.label,
                        wp=d.wp,
                    )
                )  # fmt: skip
        out.ms = round((time.perf_counter() - t) * 1000, 2)
        return out

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False)
