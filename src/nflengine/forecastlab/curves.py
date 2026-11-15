"""Outcome curves (FL01; documentation/forecast-lab/FL01-outcome-curves.md, D158): the whole
distribution behind a published player projection. Pure functions on numpy arrays and Polars
frames; files, W&B and the weekly commands are in `curves_runs.py`.

The idea: the player model publishes P10 / P50 / P90 (amounts) or a mean (counts). Instead of
assuming a bell curve, look at **analogs**: past walk-forward projections of the same stat that
looked like this one, and where their actual results landed.

- **Amounts** (yards): each analog's standardized miss is `z = (actual - P50) / w`, with `w =
  max(P90 - P10, w_floor)`; the row's curve is the empirical distribution of `round(P50 + z *
  w)` over its K nearest analogs, clipped at the stat's floor, on whole numbers. "Nearest" is
  measured on the projection (P50) alone, or on (P50, w) standardized.
- **Amounts, `analog_zero`** (the addendum of 2026-11-14): zero is its own outcome. An analog
  whose actual is exactly 0 stays at 0 (an atom, never smeared onto 1 or -1 by the rescaling);
  an analog with a negative actual keeps its own value; an analog with a positive actual maps
  as above but never below 1. So the mass at 0 is exactly the analogs' zero share.
- **Counts**: a negative binomial at the published mean with its spread fit on the nearest
  analogs (`nb`; `nb_cal` also scales the mean by what the analogs did), or the analogs' raw
  outcomes (`analog`).

Every curve lives on whole numbers `lo .. hi` (`lo` = the stat's floor): `cdf[i] = P(Y <=
lo + i)`, the last value 1.0, all mass above `hi` or below `lo` clipped onto the ends. A curve
is "if he plays": the chance he plays is FL00's.

Baselines on the same grid: **bell** (a normal through P10 / P90, counts: a Poisson at the
mean) and **own** (the player's own last 16 actuals, at least 6, else the bell).

Scores per row, all on the same grid: the CRPS (`sum_k (F(k) - 1[y <= k])^2`), the Brier score
of P(Y > mark) averaged over the marks, the chance of landing in the band around the projection
and whether it happened, and the expected histogram of the randomized PIT (10 bins).

Walk-forward: `walk_forward` builds a week's curves from rows **strictly before that week**
only (last week's actuals are known on a Tuesday); a test scrambles actuals from a week on and
checks that nothing at or before it moves.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import polars as pl
from scipy import special
from scipy.spatial import cKDTree

BELL_Z = 2.5631031310892007  # 2 * z(0.9): P10-P90 of a normal spans 2.563 sigma
OWN_MAX, OWN_MIN = 16, 6  # the own-history baseline: last 16 games at most, 6 at least
MIN_HIST = 100  # fewer earlier projections than this: the curve falls back to the bell curve
ALPHA_MIN, ALPHA_MAX = 1e-3, 5.0  # negative binomial overdispersion (var = mu + alpha mu^2)
PIT_BINS = 10
KINDS = ("amount", "count")
METHODS = ("analog", "analog_zero", "nb", "nb_cal")  # amounts: the first two; counts: the rest

# Whole-number grids reach this high (nothing above is reachable in the data; mass above is
# clipped onto the last value). The lower end is `CurveTarget.floor`.
GRID_HI: dict[str, int] = {
    "pass_yds-qb": 700,
    "rush_yds-qb": 250,
    "rush_yds-rb": 350,
    "scrim_yds-rb": 450,
    "rec_yds-wrte": 350,
    "receptions-wrte": 30,
    "targets-wrte": 40,
    "receptions-rb": 30,
    "carries-rb": 50,
    "tackles-lbs": 40,
}


def grid_bounds(key: str, floor: float) -> tuple[int, int]:
    return int(floor), GRID_HI[key]


def round_half_up(x: np.ndarray | float) -> np.ndarray:
    return np.floor(np.asarray(x, dtype=float) + 0.5)


# ---- settings ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Settings:
    """How one target's curve is built. `space` is what "similar" means: `point` (the
    projection: P50 for amounts, the mean for counts) or `point_w` (the projection and the
    P10-P90 width, standardized). `hist_seasons` 0 = every earlier season; n = the n seasons
    before the row's season plus the row's own season so far."""

    method: str = "analog"  # analog | analog_zero (amounts); analog | nb | nb_cal (counts)
    k: int = 1000
    space: str = "point"
    hist_seasons: int = 0
    w_floor: float = 5.0  # amounts: the narrowest width a miss is standardized by

    def __post_init__(self) -> None:
        if self.method not in METHODS:
            raise ValueError(f"unknown method {self.method!r}")
        if self.space not in ("point", "point_w"):
            raise ValueError(f"unknown space {self.space!r}")
        if self.k < 1 or self.hist_seasons < 0:
            raise ValueError("k must be >= 1 and hist_seasons >= 0")

    @property
    def label(self) -> str:
        hs = "all" if self.hist_seasons == 0 else f"last{self.hist_seasons}"
        return f"{self.method}-k{self.k}-{self.space}-{hs}-wf{self.w_floor:g}"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Settings:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Curve:
    cdf: np.ndarray  # (n, G): P(Y <= lo + i)
    n_analogs: np.ndarray  # (n,)


# ---- the baselines ------------------------------------------------------------------------------
def bell_cdf(
    kind: str, p50: np.ndarray, w: np.ndarray, mean: np.ndarray, lo: int, hi: int
) -> np.ndarray:
    """The baseline you'd compute by hand today, on the whole-number grid. Amounts: a normal
    centred on P50 with sigma = (P90 - P10) / 2.563, P(Y <= k) = Phi((k + 0.5 - P50) / sigma)
    (all mass below `lo` lands on `lo`). Counts: a Poisson at the mean."""
    ks = np.arange(lo, hi + 1)
    if kind == "amount":
        sigma = np.maximum(np.asarray(w, dtype=float) / BELL_Z, 1e-6)
        cdf = special.ndtr((ks[None, :] + 0.5 - np.asarray(p50)[:, None]) / sigma[:, None])
    else:
        mu = np.maximum(np.asarray(mean, dtype=float), 0.0)
        cdf = special.pdtr(np.maximum(ks, 0)[None, :], mu[:, None])
    cdf = np.asarray(cdf, dtype=float)
    cdf[:, -1] = 1.0
    return cdf


def own_cdf(
    prior: np.ndarray, fallback: np.ndarray, lo: int, hi: int
) -> tuple[np.ndarray, np.ndarray]:
    """The player's own earlier results as the distribution: `prior` is (n, OWN_MAX) of his
    last actuals (NaN where he has fewer), at least `OWN_MIN` of them, else the `fallback`
    curve (the bell curve). Returns (cdf, number of own games used; 0 = fallback)."""
    n, g = prior.shape[0], hi - lo + 1
    valid = ~np.isnan(prior)
    cnt = valid.sum(axis=1)
    use = cnt >= OWN_MIN
    vals = np.clip(np.nan_to_num(prior, nan=0.0), lo, hi).astype(np.int64) - lo
    flat = (np.arange(n)[:, None] * g + vals).ravel()
    counts = np.bincount(flat, weights=valid.ravel().astype(float), minlength=n * g).reshape(n, g)
    cdf = np.cumsum(counts, axis=1) / np.maximum(cnt, 1)[:, None]
    cdf[:, -1] = 1.0
    out = np.where(use[:, None], cdf, fallback)
    return out, np.where(use, cnt, 0)


def own_prior(hist: pl.DataFrame, players: np.ndarray | list[str]) -> np.ndarray:
    """(n, OWN_MAX) of each player's last actuals in `hist` (rows sorted by time; the caller
    passes only rows strictly before the week), oldest first, NaN-padded on the left."""
    players = list(players)
    out = np.full((len(players), OWN_MAX), np.nan)
    if hist.height == 0 or not players:
        return out
    last = hist.group_by("player_id", maintain_order=True).agg(
        pl.col("actual").tail(OWN_MAX).alias("a")
    )
    lookup = dict(zip(last["player_id"].to_list(), last["a"].to_list(), strict=True))
    for i, p in enumerate(players):
        a = lookup.get(p)
        if a:
            out[i, OWN_MAX - len(a) :] = a
    return out


# ---- the curves ---------------------------------------------------------------------------------
def _knn(
    hist_x: np.ndarray, query_x: np.ndarray, k: int, workers: int, ordered: bool = True
) -> np.ndarray:
    """Indices (n, min(k, m)) of the nearest history rows, nearest first. When every curve
    uses all `m` rows (`ordered` False and k >= m) the order doesn't matter and no search runs."""
    m = hist_x.shape[0]
    n = query_x.shape[0]
    k = min(k, m)
    if k == m and not ordered:
        return np.broadcast_to(np.arange(m), (n, m))
    tree = cKDTree(hist_x)
    _, idx = tree.query(query_x, k=k, workers=workers)
    return idx.reshape(n, k)


def _features(settings: Settings, point: np.ndarray, w: np.ndarray) -> np.ndarray:
    """The similarity space's coordinates: 1-D = the projection; 2-D = (projection, width);
    the caller divides by the history's own spread (`_scales`)."""
    if settings.space == "point":
        return point[:, None]
    return np.column_stack([point, w])


def _scales(settings: Settings, h_point: np.ndarray, h_w: np.ndarray) -> np.ndarray:
    if settings.space == "point":
        return np.ones(1)
    s = np.array([h_point.std(), h_w.std()])
    return np.where(s > 1e-9, s, 1.0)


@dataclass
class Hist:
    """The earlier projections (sorted by time) a week's curves may use."""

    season: np.ndarray
    point: np.ndarray  # P50 (amounts) / mean (counts)
    w: np.ndarray  # P90 - P10
    y: np.ndarray  # the actual


def _amount_cdf(
    s: Settings, idx: np.ndarray, h: Hist, q_point: np.ndarray, q_w: np.ndarray, lo: int, hi: int
) -> np.ndarray:
    n, k = idx.shape
    g = hi - lo + 1
    h_we = np.maximum(h.w, s.w_floor)
    h_z = (h.y - h.point) / h_we
    q_we = np.maximum(q_w, s.w_floor)
    out = np.floor(q_point[:, None] + h_z[idx] * q_we[:, None] + 0.5)
    if s.method == "analog_zero":
        # zero is its own outcome: a zero stays 0, a negative result keeps its value, and a
        # positive result never lands below 1 (the rescaling must not smear a zero onto 1)
        y = np.floor(h.y[idx] + 0.5)
        out = np.where(y > 0, np.maximum(out, 1.0), y)
    out = np.clip(out, lo, hi).astype(np.int64) - lo
    flat = (np.arange(n)[:, None] * g + out).ravel()
    counts = np.bincount(flat, minlength=n * g).reshape(n, g)
    cdf = np.cumsum(counts, axis=1) / k
    cdf[:, -1] = 1.0
    return cdf


def nb_cdf(mu: np.ndarray, alpha: np.ndarray, lo: int, hi: int) -> np.ndarray:
    """P(Y <= k) of a negative binomial with mean `mu` and variance mu + alpha mu^2, on
    whole numbers `lo .. hi` (`lo` >= 0); mass above `hi` lands on `hi`."""
    ks = np.arange(lo, hi + 1)
    r = 1.0 / np.asarray(alpha, dtype=float)
    mu = np.maximum(np.asarray(mu, dtype=float), 1e-6)
    p = r / (r + mu)
    # P(Y <= k) = I_p(r, k + 1), the regularized incomplete beta (real-valued r)
    cdf = np.asarray(special.betainc(r[:, None], ks[None, :] + 1.0, p[:, None]), dtype=float)
    cdf[:, -1] = 1.0
    return cdf


def _count_cdf(
    s: Settings, idx: np.ndarray, h: Hist, q_point: np.ndarray, lo: int, hi: int
) -> np.ndarray:
    n, k = idx.shape
    g = hi - lo + 1
    y = h.y[idx]
    if s.method == "analog":
        out = np.clip(y, lo, hi).astype(np.int64) - lo
        flat = (np.arange(n)[:, None] * g + out).ravel()
        counts = np.bincount(flat, minlength=n * g).reshape(n, g)
        cdf = np.cumsum(counts, axis=1) / k
        cdf[:, -1] = 1.0
        return cdf
    mu_h = h.point[idx]
    mu_row = np.maximum(q_point, 1e-6)
    if s.method == "nb_cal":
        ratio = y.sum(axis=1) / np.maximum(mu_h.sum(axis=1), 1e-9)
        ratio = np.clip(ratio, 0.25, 4.0)
        mu_h = mu_h * ratio[:, None]
        mu_row = mu_row * ratio
    num = ((y - mu_h) ** 2 - mu_h).sum(axis=1)
    den = (mu_h**2).sum(axis=1)
    alpha = np.clip(num / np.maximum(den, 1e-9), ALPHA_MIN, ALPHA_MAX)
    return nb_cdf(mu_row, alpha, lo, hi)


def make_curves(
    kind: str,
    settings_list: Sequence[Settings],
    h: Hist,
    q_point: np.ndarray,
    q_w: np.ndarray,
    *,
    lo: int,
    hi: int,
    q_season: int,
    workers: int = 1,
) -> dict[Settings, Curve]:
    """One curve per settings for every query row, from the history `h` (the caller has cut it
    to rows strictly before the query's week). Settings that differ only in `k` or `method`
    share one nearest-neighbour search. With fewer than `MIN_HIST` analogs there's no curve
    (the entry is missing: the caller uses the bell curve)."""
    out: dict[Settings, Curve] = {}
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if kind == "amount" and any(s.method not in ("analog", "analog_zero") for s in settings_list):
        raise ValueError("amount curves are built from analogs only")
    if kind == "count" and any(s.method == "analog_zero" for s in settings_list):
        raise ValueError("analog_zero is for amounts; counts are never negative")
    groups: dict[tuple[str, int, float], list[Settings]] = {}
    for s in settings_list:
        gk = (s.space, s.hist_seasons, s.w_floor if kind == "amount" else 0.0)
        groups.setdefault(gk, []).append(s)
    for (_, hs, _), members in groups.items():
        start = int(np.searchsorted(h.season, q_season - hs, side="left")) if hs else 0
        m = h.point.shape[0] - start
        if m < MIN_HIST:
            continue
        sub = Hist(h.season[start:], h.point[start:], h.w[start:], h.y[start:])
        s0 = members[0]
        if kind == "amount":
            hw_eff = np.maximum(sub.w, s0.w_floor)
            qw_eff = np.maximum(q_w, s0.w_floor)
        else:
            hw_eff, qw_eff = sub.w, q_w
        scale = _scales(s0, sub.point, hw_eff)
        hx = _features(s0, sub.point, hw_eff) / scale
        qx = _features(s0, q_point, qw_eff) / scale
        kmax = max(s.k for s in members)
        idx_all = _knn(hx, qx, kmax, workers, ordered=min(s.k for s in members) < m)
        for s in members:
            idx = idx_all[:, : min(s.k, m)]
            if kind == "amount":
                cdf = _amount_cdf(s, idx, sub, q_point, q_w, lo, hi)
            else:
                cdf = _count_cdf(s, idx, sub, q_point, lo, hi)
            out[s] = Curve(cdf, np.full(q_point.shape[0], idx.shape[1], dtype=np.int32))
    return out


# ---- the walk-forward ---------------------------------------------------------------------------
@dataclass
class Step:
    season: int
    week: int
    rows: pl.DataFrame  # the step's rows of the (sorted) frame
    curves: dict[Settings, Curve]
    bell: np.ndarray
    own: np.ndarray
    own_n: np.ndarray


def prepare(frame: pl.DataFrame) -> pl.DataFrame:
    """Sort a history frame (`season`, `week`, `player_id`, `p10`, `p50`, `p90`, `mean`,
    `actual`) by time; rows without an actual can't be history."""
    return frame.sort("season", "week", "player_id", maintain_order=True)


def hist_arrays(kind: str, frame: pl.DataFrame) -> Hist:
    point = frame["p50" if kind == "amount" else "mean"].to_numpy().astype(float)
    w = (frame["p90"] - frame["p10"]).to_numpy().astype(float)
    return Hist(
        frame["season"].to_numpy().astype(np.int64),
        point,
        w,
        frame["actual"].to_numpy().astype(float),
    )


def query_arrays(kind: str, rows: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    point = rows["p50" if kind == "amount" else "mean"].to_numpy().astype(float)
    w = (rows["p90"] - rows["p10"]).to_numpy().astype(float)
    return point, w


def step_curves(
    kind: str,
    settings_list: Sequence[Settings],
    hist: pl.DataFrame,
    rows: pl.DataFrame,
    *,
    lo: int,
    hi: int,
    workers: int = 1,
) -> tuple[dict[Settings, Curve], np.ndarray, np.ndarray, np.ndarray]:
    """Curves, bell and own baselines for `rows` (one week) from `hist` (sorted by time, every
    row strictly before that week). Rows whose settings have too little history get the bell
    curve (`n_analogs` 0)."""
    q_point, q_w = query_arrays(kind, rows)
    season = int(rows["season"][0])
    bell = bell_cdf(
        kind,
        rows["p50"].to_numpy().astype(float),
        q_w,
        rows["mean"].to_numpy().astype(float),
        lo,
        hi,
    )
    h = hist_arrays(kind, hist)
    curves = make_curves(
        kind, settings_list, h, q_point, q_w, lo=lo, hi=hi, q_season=season, workers=workers
    )
    for s in settings_list:
        if s not in curves:
            curves[s] = Curve(bell.copy(), np.zeros(rows.height, dtype=np.int32))
    own, own_n = own_cdf(own_prior(hist, rows["player_id"].to_list()), bell, lo, hi)
    return curves, bell, own, own_n


def walk_forward(
    frame: pl.DataFrame,
    kind: str,
    settings_list: Sequence[Settings],
    *,
    lo: int,
    hi: int,
    score_seasons: Sequence[int],
    workers: int = 1,
    max_weeks_per_season: int | None = None,
) -> Iterator[Step]:
    """Every (season, week) of `score_seasons` in time order: its curves from the rows
    strictly before it (all of `frame`, any season). `frame` needs `season`, `week`,
    `player_id`, `p10`, `p50`, `p90`, `mean`, `actual`."""
    fr = prepare(frame)
    seasons = fr["season"].to_numpy().astype(np.int64)
    weeks = fr["week"].to_numpy().astype(np.int64)
    t = seasons * 100 + weeks
    want = {int(x) for x in score_seasons}
    keys = sorted({int(x) for x in np.unique(t) if int(x) // 100 in want})
    taken: dict[int, int] = {}
    for key in keys:
        s_ = key // 100
        if max_weeks_per_season is not None:
            if taken.get(s_, 0) >= max_weeks_per_season:
                continue
            taken[s_] = taken.get(s_, 0) + 1
        lo_i = int(np.searchsorted(t, key, side="left"))
        hi_i = int(np.searchsorted(t, key, side="right"))
        rows = fr[lo_i:hi_i]
        hist = fr[:lo_i]
        curves, bell, own, own_n = step_curves(
            kind, settings_list, hist, rows, lo=lo, hi=hi, workers=workers
        )
        yield Step(s_, key % 100, rows, curves, bell, own, own_n)


# ---- the scores ---------------------------------------------------------------------------------
def _y_index(y: np.ndarray, lo: int, g: int) -> np.ndarray:
    return np.clip(round_half_up(y) - lo, 0, g - 1).astype(np.int64)


def crps(cdf: np.ndarray, y: np.ndarray, lo: int) -> np.ndarray:
    """CRPS of whole-number curves: sum over k of (F(k) - 1[y <= k])^2, per row."""
    g = cdf.shape[1]
    yi = _y_index(y, lo, g)
    ind = np.arange(g)[None, :] >= yi[:, None]
    return ((cdf - ind) ** 2).sum(axis=1)


def p_over(cdf: np.ndarray, marks: Sequence[float], lo: int) -> np.ndarray:
    """(n, marks): the curve's chance of MORE than each mark (marks are x.5)."""
    g = cdf.shape[1]
    ix = np.clip(np.floor(np.asarray(marks)).astype(np.int64) - lo, 0, g - 1)
    return 1.0 - cdf[:, ix]


def brier_marks(cdf: np.ndarray, y: np.ndarray, marks: Sequence[float], lo: int) -> np.ndarray:
    """Per row, the mean over the marks of (P(Y > mark) - 1[y > mark])^2."""
    po = p_over(cdf, marks, lo)
    hit = (np.asarray(y)[:, None] > np.asarray(marks)[None, :]).astype(float)
    return ((po - hit) ** 2).mean(axis=1)


def band_prob(cdf: np.ndarray, centre: np.ndarray, band: float, lo: int) -> np.ndarray:
    """P(|Y - centre| <= band) on whole numbers: F(centre + b) - F(centre - b - 1)."""
    g = cdf.shape[1]
    b = int(np.floor(band))
    n = cdf.shape[0]
    c = round_half_up(centre).astype(np.int64)
    up = np.clip(c + b - lo, 0, g - 1)
    dn = c - b - 1 - lo
    f_up = cdf[np.arange(n), up]
    f_dn = np.where(dn >= 0, cdf[np.arange(n), np.clip(dn, 0, g - 1)], 0.0)
    return np.clip(f_up - f_dn, 0.0, 1.0)


def band_hit(y: np.ndarray, centre: np.ndarray, band: float) -> np.ndarray:
    c = round_half_up(centre)
    return (np.abs(np.asarray(y, dtype=float) - c) <= np.floor(band)).astype(float)


def pit_rows(cdf: np.ndarray, y: np.ndarray, lo: int, bins: int = PIT_BINS) -> np.ndarray:
    """(n, bins): each row's share of the randomized PIT in each bin, as an expectation. The
    PIT of a whole-number outcome is uniform on (F(y - 1), F(y)]; a row spreads one unit over
    that interval (an impossible outcome, F(y) = F(y - 1), is a point)."""
    n, g = cdf.shape
    yi = _y_index(y, lo, g)
    b = cdf[np.arange(n), yi]
    a = np.where(yi > 0, cdf[np.arange(n), np.maximum(yi - 1, 0)], 0.0)
    edges = np.linspace(0.0, 1.0, bins + 1)
    ov = np.clip(
        np.minimum(b[:, None], edges[None, 1:]) - np.maximum(a[:, None], edges[None, :-1]), 0, None
    )
    span = b - a
    prop = ov / np.maximum(span, 1e-12)[:, None]
    point = np.zeros((n, bins))
    pb = np.clip((b * bins).astype(np.int64), 0, bins - 1)
    point[np.arange(n), pb] = 1.0
    return np.where((span > 1e-12)[:, None], prop, point)


def pit_ece(hist_rows: np.ndarray) -> float:
    """Mean absolute deviation of the 10 PIT bins' shares from uniform (0 = perfect)."""
    if hist_rows.shape[0] == 0:
        return float("nan")
    share = hist_rows.mean(axis=0)
    return float(np.abs(share - 1.0 / share.shape[0]).mean())


def p_ge1(cdf: np.ndarray, lo: int) -> np.ndarray:
    """The curve's chance of 1 or more: 1 - F(0) (`lo` <= 0 is the grid's first whole number)."""
    g = cdf.shape[1]
    return 1.0 - cdf[:, int(np.clip(-lo, 0, g - 1))]


@dataclass
class RowScores:
    crps: np.ndarray
    brier: np.ndarray
    p_band: np.ndarray
    p_over: np.ndarray  # (n, marks)
    pit: np.ndarray  # (n, bins)
    p_ge1: np.ndarray  # (n,): the chance of 1 or more (a diagnostic, not part of the ship rule)


def score_curve(
    cdf: np.ndarray, y: np.ndarray, centre: np.ndarray, band: float, marks: Sequence[float], lo: int
) -> RowScores:
    return RowScores(
        crps(cdf, y, lo),
        brier_marks(cdf, y, marks, lo),
        band_prob(cdf, centre, band, lo),
        p_over(cdf, marks, lo),
        pit_rows(cdf, y, lo),
        p_ge1(cdf, lo),
    )


def quantiles(cdf: np.ndarray, qs: Sequence[float], lo: int) -> np.ndarray:
    """(n, len(qs)): the smallest whole number with F >= q."""
    return np.stack([lo + (cdf >= q - 1e-12).argmax(axis=1) for q in qs], axis=1).astype(float)


def trim_cdf(row: np.ndarray) -> np.ndarray:
    """A curve without its flat tail: up to the first value that reaches 1 (the last value is
    exactly 1.0; readers treat everything beyond as 1)."""
    full = np.nonzero(row >= 1.0 - 1e-12)[0]
    end = int(full[0]) if full.size else row.shape[0] - 1
    out = row[: end + 1].copy()
    out[-1] = 1.0
    return out


def centre_of(kind: str, p50: np.ndarray, mean: np.ndarray) -> np.ndarray:
    """What the band is centred on: the projection (amounts: P50; counts: the mean), rounded."""
    return round_half_up(p50 if kind == "amount" else mean)


# ---- the ship rule ------------------------------------------------------------------------------
SHIP_RULE: dict[str, Any] = {
    "seasons_share": 0.8,  # CRPS below the bell curve's in at least 4 of 5 seasons
    "band_gap_pp": 2.0,  # the band's predicted chance within 2 points of how often it happened
}


def ship_verdict(
    pooled: dict[str, float],
    by_season: Sequence[dict[str, float]],
    rule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The pre-registered rule (FL01, 2026-11-14), each target on its own, on rows where the
    player played: (1) CRPS below the bell curve's pooled and in at least 80% of the seasons
    (4 of 5); (2) CRPS below the player's-own-history baseline's pooled; (3) the mean Brier
    score over the target's marks below the bell curve's pooled; (4) the band's predicted
    chance within 2 percentage points of how often it happened, pooled."""
    rule = {**SHIP_RULE, **(rule or {})}
    n_seasons = len(by_season)
    need = int(np.ceil(rule["seasons_share"] * n_seasons - 1e-9)) if n_seasons else 0
    won = sum(1 for r in by_season if r["crps_model"] < r["crps_bell"])
    gap = 100.0 * abs(pooled["band_pred"] - pooled["band_hit"])
    parts = {
        "crps_vs_bell": {
            "model": pooled["crps_model"],
            "bell": pooled["crps_bell"],
            "seasons_won": won,
            "seasons": n_seasons,
            "need": need,
            "ok": bool(pooled["crps_model"] < pooled["crps_bell"] and won >= need),
        },
        "crps_vs_own": {
            "model": pooled["crps_model"],
            "own": pooled["crps_own"],
            "ok": bool(pooled["crps_model"] < pooled["crps_own"]),
        },
        "brier_vs_bell": {
            "model": pooled["brier_model"],
            "bell": pooled["brier_bell"],
            "ok": bool(pooled["brier_model"] < pooled["brier_bell"]),
        },
        "band": {
            "predicted": pooled["band_pred"],
            "happened": pooled["band_hit"],
            "gap_pp": gap,
            "limit_pp": rule["band_gap_pp"],
            "ok": bool(gap <= rule["band_gap_pp"]),
        },
    }
    return {"ships": all(p["ok"] for p in parts.values()), "rule": parts}
