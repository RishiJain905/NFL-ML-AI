"""Files, backtests, W&B and the weekly commands for the outcome curves (FL01, D158):
`nfl forecastlab backtest | predict | grade` (the CLI is wired by the lead).

- `run_backtest`: walk-forward over the reported seasons (or, with `tune=True`, the candidate
  settings on the tuning window only): every scored player-game of the player model's own
  walk-forward predictions gets a curve from the projections strictly before its week, scored
  against the two baselines on the same rows; the pre-registered ship rule (D158) per target;
  `backtests/curves/<target>/predictions.parquet` + `summary.json`; one `curves-backtest` W&B
  run with live curves. `promote=True` writes `models/forecastlab/curves/<version>/` and
  `production.json` and moves the `curves-model` artifact's `production` alias.
- `run_predict`: the week's curves for every projection of a shipped target, from the
  production settings -> `forecastlab/<S>/week<NN>/curves.parquet` + `curves.json`.
- `run_grade`: the week's curves vs what happened -> the same file with `actual` / `played`
  filled and `forecastlab/<S>/curves_scoreboard.parquet`.

Reads published files only (D107), each once per process and never held open: the walk-forward
history is `runs/backtests/player/<key>/predictions_players.parquet` plus, for seasons after the
last backtested one, each season's `runs/<S>/player_walkforward.parquet`; the live projections
are `runs/<S>/week<NN>/predictions_players.parquet`; the grading source is the week's
`predictions_players_scored.parquet` (the production scoreboard's own actuals), else the
season's walk-forward file. Writes only under `forecastlab/` and `models/forecastlab/curves/`.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nflengine.forecastlab import curves as C
from nflengine.forecastlab import schema as S
from nflengine.forecastlab.curves import Settings
from nflengine.forecastlab.schema import (
    CURVE_SCHEMA,
    CURVE_SCORE_SCHEMA,
    CURVE_TARGETS,
    CurveTarget,
)
from nflengine.fsutil import replace_file
from nflengine.paths import DataPaths, ensure_data_root
from nflengine.settings import get_config

FAMILY = "curves"
ARTIFACT = "curves-model"
GROUP = S.WANDB_GROUP
SMOKE_GROUP = f"{S.WANDB_GROUP}-smoke"
PRED_FILE = "predictions_players.parquet"
SCORED_FILE = "predictions_players_scored.parquet"
WALK_FILE = "player_walkforward.parquet"
CURVES_FILE = "curves.parquet"
META_FILE = "curves.json"
SCOREBOARD_FILE = "curves_scoreboard.parquet"
QS = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
SMOKE_TARGETS = ("rec_yds-wrte", "receptions-wrte")
SMOKE_WEEKS = 3
VERSION_RE = r"^\d{4}-\d{4}_\d{8}T\d{6}Z$"  # <first>-<last>_<UTC stamp>: never a path
HIST_COLS = [
    "season", "week", "game_id", "kickoff_utc", "created_at", "player_id", "player", "team",
    "opponent", "position", "group", "target", "p10", "p50", "p90", "mean", "actual", "played",
    "model_version",
]  # fmt: skip

# The settings the tuning window chose (FL01 tuning table in the phase file), one per target.
# The amount targets use `analog_zero` (the 2026-11-14 addendum: zero is its own outcome),
# chosen on the tuning window with the K and the space of the first tuning.
# Config `forecastlab.curves.settings.<key>` overrides one (a dict of `Settings` fields).
CHOSEN: dict[str, Settings] = {
    "pass_yds-qb": Settings("analog_zero", 1000, "point_w", 0),
    "rush_yds-qb": Settings("analog_zero", 500, "point_w", 0),
    "rush_yds-rb": Settings("analog_zero", 500, "point_w", 0),
    "scrim_yds-rb": Settings("analog_zero", 500, "point_w", 0),
    "rec_yds-wrte": Settings("analog_zero", 1000, "point", 0),
    "receptions-wrte": Settings("nb", 2000, "point", 0),
    "targets-wrte": Settings("nb_cal", 1000, "point", 0),
    "receptions-rb": Settings("nb_cal", 1000, "point", 0),
    "carries-rb": Settings("nb", 500, "point", 0),
    "tackles-lbs": Settings("nb_cal", 1000, "point", 0),
}


# ---- settings -----------------------------------------------------------------------------------
def curves_cfg() -> dict[str, Any]:
    cfg = dict((get_config().forecastlab or {}).get("curves") or {})
    cfg.setdefault("history_from", 2017)
    cfg.setdefault("tune_seasons", "2019-2020")
    cfg.setdefault("report_seasons", "2021-2025")
    return cfg


def parse_seasons(spec: Any) -> list[int]:
    """`2019-2020`, `2019`, 2019 or a list -> sorted season numbers."""
    if isinstance(spec, int):
        return [spec]
    if isinstance(spec, (list, tuple)):
        return sorted({int(x) for x in spec})
    parts = str(spec).strip().split("-")
    if len(parts) == 2:
        return list(range(int(parts[0]), int(parts[1]) + 1))
    return [int(parts[0])]


def settings_for(key: str, cfg: dict[str, Any] | None = None) -> Settings:
    cfg = curves_cfg() if cfg is None else cfg
    over = (cfg.get("settings") or {}).get(key)
    return Settings.from_dict(over) if over else CHOSEN[key]


def candidates(kind: str) -> list[Settings]:
    """The settings compared on the tuning window: how many analogs, what "similar" means,
    how far back history goes (and, for counts, the curve's form)."""
    ks, hs = (500, 1000, 2000, 4000), (0, 4)
    if kind == "amount":
        return [Settings("analog", k, sp, h) for k in ks for sp in ("point", "point_w") for h in hs]
    out = [Settings(m, k, "point", h) for m in ("nb", "nb_cal") for k in ks for h in hs]
    out += [Settings("analog", k, sp, h) for k in ks for sp in ("point", "point_w") for h in hs]
    return out


def select_targets(keys: Sequence[str] | None) -> list[CurveTarget]:
    if not keys:
        return list(CURVE_TARGETS)
    unknown = [k for k in keys if k not in S.CURVE_BY_KEY]
    if unknown:
        raise ValueError(f"unknown curve targets {unknown}; see schema.CURVE_TARGETS")
    return [S.CURVE_BY_KEY[k] for k in keys]


# ---- reading ------------------------------------------------------------------------------------
_CACHE: dict[tuple[str, int, int, tuple[str, ...]], pl.DataFrame] = {}


def read_cached(path: Path, columns: Sequence[str] | None = None) -> pl.DataFrame:
    """`schema.read_parquet` once per process per (file, version): the whole file in memory,
    never held open (D100). A file that changes on disk is read again."""
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size, tuple(columns or ()))
    if key not in _CACHE:
        for k in [k for k in _CACHE if k[0] == key[0]]:
            del _CACHE[k]
        _CACHE[key] = S.read_parquet(path, list(columns) if columns else None)
    return _CACHE[key]


def backtest_file(paths: DataPaths, key: str) -> Path:
    return paths.runs / "backtests" / "player" / key / PRED_FILE


def load_history(
    paths: DataPaths, t: CurveTarget, before: tuple[int, int] | None = None
) -> pl.DataFrame:
    """Every walk-forward projection of the target that has its result: the player model's
    backtest (2017-2025) plus, for later seasons, each season's `player_walkforward.parquet`.
    `before=(season, week)` keeps only rows strictly before that week. Sorted by time."""
    cfg = curves_cfg()
    pieces = []
    f = backtest_file(paths, t.key)
    last_bt = 0
    if f.exists():
        df = read_cached(f, HIST_COLS)
        pieces.append(df)
        last_bt = int(df["season"].max())
    if paths.runs.exists():
        for d in sorted(paths.runs.iterdir()):
            if d.name.isdigit() and int(d.name) > last_bt and (d / WALK_FILE).exists():
                pieces.append(read_cached(d / WALK_FILE, HIST_COLS))
    if not pieces:
        raise FileNotFoundError(
            f"no walk-forward history for {t.key}: {f} is missing (run the player backtest)"
        )
    df = pl.concat(pieces, how="vertical_relaxed").filter(
        (pl.col("target") == t.name)
        & (pl.col("group") == t.group)
        & pl.col("played").fill_null(False)
        & pl.col("actual").is_not_null()
        & pl.col("p50").is_not_null()
        & pl.col("p10").is_not_null()
        & pl.col("p90").is_not_null()
        & (pl.col("season") >= int(cfg["history_from"]))
    )
    if before is not None:
        s, w = before
        df = df.filter((pl.col("season") < s) | ((pl.col("season") == s) & (pl.col("week") < w)))
    return C.prepare(df.unique(["player_id", "game_id"], keep="last", maintain_order=True))


# ---- scoring a target over a window -------------------------------------------------------------
@dataclass
class Evaluation:
    target: CurveTarget
    lo: int
    hi: int
    rows: pl.DataFrame  # the scored rows, in step order
    model: dict[Settings, C.RowScores]
    bell: C.RowScores
    own: C.RowScores
    own_n: np.ndarray
    n_analogs: dict[Settings, np.ndarray]
    quantiles: dict[Settings, np.ndarray] = field(default_factory=dict)
    seconds: float = 0.0


def _cat(parts: list[C.RowScores]) -> C.RowScores:
    return C.RowScores(
        np.concatenate([p.crps for p in parts]),
        np.concatenate([p.brier for p in parts]),
        np.concatenate([p.p_band for p in parts]),
        np.concatenate([p.p_over for p in parts]),
        np.concatenate([p.pit for p in parts]),
        np.concatenate([p.p_ge1 for p in parts]),
    )


def evaluate(
    t: CurveTarget,
    hist: pl.DataFrame,
    settings_list: Sequence[Settings],
    seasons: Sequence[int],
    *,
    workers: int = 4,
    max_weeks: int | None = None,
    keep: Sequence[Settings] = (),
    on_step: Callable[[int, int, np.ndarray, dict[str, C.RowScores]], None] | None = None,
    primary: Settings | None = None,
) -> Evaluation:
    """Walk forward over `seasons`: each week's rows get a curve from the rows strictly before
    it, per settings, and the two baselines; every curve scored on the same whole-number grid.
    `keep` settings also store their quantiles. `on_step(season, week, y, {name: scores})`
    sees the `primary` settings, the bell and the own-history curves after each week."""
    lo, hi = C.grid_bounds(t.key, t.floor)
    t0 = time.perf_counter()
    rows, own_n = [], []
    model: dict[Settings, list[C.RowScores]] = {s: [] for s in settings_list}
    bell, own = [], []
    n_an: dict[Settings, list[np.ndarray]] = {s: [] for s in settings_list}
    quant: dict[Settings, list[np.ndarray]] = {s: [] for s in keep}
    primary = primary or (settings_list[0] if settings_list else None)
    for st in C.walk_forward(
        hist, t.kind, list(settings_list), lo=lo, hi=hi, score_seasons=list(seasons),
        workers=workers, max_weeks_per_season=max_weeks,
    ):  # fmt: skip
        y = st.rows["actual"].to_numpy().astype(float)
        centre = C.centre_of(
            t.kind,
            st.rows["p50"].to_numpy().astype(float),
            st.rows["mean"].to_numpy().astype(float),
        )
        rows.append(st.rows)
        own_n.append(st.own_n)
        sc: dict[str, C.RowScores] = {}
        for s in settings_list:
            rs = C.score_curve(st.curves[s].cdf, y, centre, t.band, t.marks, lo)
            model[s].append(rs)
            n_an[s].append(st.curves[s].n_analogs)
            if s == primary:
                sc["model"] = rs
        for s in keep:
            quant[s].append(C.quantiles(st.curves[s].cdf, QS, lo))
        b = C.score_curve(st.bell, y, centre, t.band, t.marks, lo)
        o = C.score_curve(st.own, y, centre, t.band, t.marks, lo)
        bell.append(b)
        own.append(o)
        sc["bell"], sc["own"] = b, o
        if on_step is not None:
            on_step(st.season, st.week, y, sc)
    if not rows:
        raise ValueError(f"{t.key}: no rows to score in seasons {list(seasons)}")
    return Evaluation(
        t, lo, hi, pl.concat(rows),
        {s: _cat(v) for s, v in model.items()}, _cat(bell), _cat(own), np.concatenate(own_n),
        {s: np.concatenate(v) for s, v in n_an.items()},
        {s: np.vstack(v) for s, v in quant.items()},
        time.perf_counter() - t0,
    )  # fmt: skip


def _mean_scores(ev: Evaluation, mask: np.ndarray, s: Settings) -> dict[str, float]:
    t = ev.target
    rows = ev.rows.filter(pl.Series(mask))
    y = rows["actual"].to_numpy().astype(float)
    centre = C.centre_of(
        t.kind, rows["p50"].to_numpy().astype(float), rows["mean"].to_numpy().astype(float)
    )
    hit = C.band_hit(y, centre, t.band)
    ge1 = (y >= 1).astype(float)  # on the masked rows
    m, b, o = ev.model[s], ev.bell, ev.own

    def sub(x: np.ndarray) -> np.ndarray:
        return x[mask]

    return {
        "n": int(mask.sum()),
        "crps_model": float(sub(m.crps).mean()),
        "crps_bell": float(sub(b.crps).mean()),
        "crps_own": float(sub(o.crps).mean()),
        "brier_model": float(sub(m.brier).mean()),
        "brier_bell": float(sub(b.brier).mean()),
        "brier_own": float(sub(o.brier).mean()),
        "band_pred": float(sub(m.p_band).mean()),
        "band_pred_bell": float(sub(b.p_band).mean()),
        "band_pred_own": float(sub(o.p_band).mean()),
        "band_hit": float(hit.mean()),
        "pit_ece": C.pit_ece(sub(m.pit)),
        "pit_ece_bell": C.pit_ece(sub(b.pit)),
        "pit_ece_own": C.pit_ece(sub(o.pit)),
        "own_share": float((sub(ev.own_n) > 0).mean()),
        "analogs_mean": float(sub(ev.n_analogs[s]).mean()),
        # the "1 or more" diagnostic (addendum, 2026-11-14): a Brier score of P(Y >= 1)
        "brier_ge1_model": float(((sub(m.p_ge1) - ge1) ** 2).mean()),
        "brier_ge1_bell": float(((sub(b.p_ge1) - ge1) ** 2).mean()),
        "brier_ge1_own": float(((sub(o.p_ge1) - ge1) ** 2).mean()),
        "ge1_pred": float(sub(m.p_ge1).mean()),
        "ge1_hit": float(ge1.mean()),
    }


def summarize(ev: Evaluation, s: Settings) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Pooled scores and one row per season for the settings `s`."""
    allm = np.ones(ev.rows.height, dtype=bool)
    pooled = _mean_scores(ev, allm, s)
    seas = ev.rows["season"].to_numpy()
    by_season = [
        {"season": int(x), **_mean_scores(ev, seas == x, s)} for x in sorted(set(seas.tolist()))
    ]
    return pooled, by_season


def tuning_table(ev: Evaluation) -> list[dict[str, Any]]:
    """Every candidate's pooled scores on the tuning window, best CRPS first."""
    allm = np.ones(ev.rows.height, dtype=bool)
    out = []
    for s in ev.model:
        r = _mean_scores(ev, allm, s)
        out.append({
            "settings": s.as_dict(),
            "label": s.label,
            "crps": r["crps_model"],
            "crps_vs_bell_pct": 100 * (r["crps_bell"] - r["crps_model"]) / r["crps_bell"],
            "brier": r["brier_model"],
            "band_gap_pp": 100 * abs(r["band_pred"] - r["band_hit"]),
        })  # fmt: skip
    return sorted(out, key=lambda r: r["crps"])


def reliability(ev: Evaluation, s: Settings, bins: int = 10) -> list[dict[str, Any]]:
    """The marks' reliability: every (row, mark) pair's predicted chance of "over" in 10
    equal-width bins against how often it happened, for the model and the bell curve."""
    t = ev.target
    y = ev.rows["actual"].to_numpy().astype(float)
    over = (y[:, None] > np.asarray(t.marks)[None, :]).astype(float)
    out = []
    for name, p in (("model", ev.model[s].p_over), ("bell", ev.bell.p_over)):
        b = np.clip((p * bins).astype(int), 0, bins - 1).ravel()
        pf, of = p.ravel(), over.ravel()
        for i in range(bins):
            m = b == i
            out.append({
                "target": t.key, "source": name, "bin": i,
                "lo": i / bins, "hi": (i + 1) / bins, "n": int(m.sum()),
                "predicted": float(pf[m].mean()) if m.any() else None,
                "happened": float(of[m].mean()) if m.any() else None,
            })  # fmt: skip
    return out


# ---- frames -------------------------------------------------------------------------------------
def _marks_series(t: CurveTarget, p_over: np.ndarray, bell_over: np.ndarray) -> pl.Series:
    marks = list(t.marks)
    data = [
        [
            {"mark": marks[j], "p_over": float(p_over[i, j]), "bell_p_over": float(bell_over[i, j])}
            for j in range(len(marks))
        ]
        for i in range(p_over.shape[0])
    ]
    return pl.Series("marks", data, dtype=pl.List(S.MARK))


def curve_frame(
    t: CurveTarget,
    rows: pl.DataFrame,
    cdf: np.ndarray,
    bell: np.ndarray,
    n_analogs: np.ndarray,
    *,
    lo: int,
    created_at: datetime | None,
    model_version: str,
    with_cdf: bool,
) -> pl.DataFrame:
    """`CURVE_SCHEMA` rows for `rows` (published projections: season, week, game_id,
    kickoff_utc, player columns, p10 / p50 / p90 / mean, `model_version` of the player model,
    any actual / played are ignored) and their curves."""
    p50 = rows["p50"].to_numpy().astype(float)
    mean = rows["mean"].to_numpy().astype(float)
    centre = C.centre_of(t.kind, p50, mean)
    q = C.quantiles(cdf, QS, lo)
    n = rows.height
    df = rows.select(
        "season",
        "week",
        "game_id",
        "kickoff_utc",
        "player_id",
        "player",
        "team",
        "opponent",
        "position",
        "group",
        "p10",
        "p50",
        "p90",
        "mean",
        pl.col("model_version").alias("source_version"),
    ).with_columns(  # fmt: skip
        pl.lit(created_at).alias("created_at"),
        pl.lit(t.name).alias("target"),
        pl.lit(t.key).alias("target_key"),
        pl.lit(t.label).alias("target_label"),
        pl.lit(t.kind).alias("kind"),
        pl.lit(lo, dtype=pl.Int32).alias("grid_lo"),
        *[pl.Series(f"q{int(round(x * 100)):02d}", q[:, i]) for i, x in enumerate(QS)],
        pl.lit(t.band).alias("band"),
        pl.Series("p_band", C.band_prob(cdf, centre, t.band, lo)),
        pl.Series("bell_p_band", C.band_prob(bell, centre, t.band, lo)),
        _marks_series(t, C.p_over(cdf, t.marks, lo), C.p_over(bell, t.marks, lo)),
        pl.Series("n_analogs", n_analogs.astype(np.int32)),
        pl.lit(model_version).alias("model_version"),
        # a made curve never carries a result, whatever the projection file holds: results
        # are filled by `run_grade` only
        pl.lit(None, dtype=pl.Float64).alias("actual"),
        pl.lit(None, dtype=pl.Boolean).alias("played"),
    )
    if with_cdf:
        df = df.with_columns(
            pl.Series("cdf", [C.trim_cdf(cdf[i]) for i in range(n)], dtype=pl.List(pl.Float64))
        )
    return S.conform(df, CURVE_SCHEMA if with_cdf else _no_cdf_schema())


def _no_cdf_schema() -> dict[str, pl.DataType]:
    return {k: v for k, v in CURVE_SCHEMA.items() if k != "cdf"}


def dense_cdf(col: pl.Series, lo_len: int | None = None) -> np.ndarray:
    """A `cdf` column (trimmed lists) as an (n, G) matrix, padded with 1.0."""
    lists = col.to_list()
    g = max((len(x) for x in lists), default=1) if lo_len is None else lo_len
    out = np.ones((len(lists), g))
    for i, x in enumerate(lists):
        out[i, : len(x)] = x
    return out


EXTRA_COLS = (
    "crps_model", "crps_bell", "crps_own", "brier_model", "brier_bell", "brier_own",
    "own_p_band", "own_n",
)  # fmt: skip


def backtest_frame(ev: Evaluation, s: Settings, version: str) -> pl.DataFrame:
    """The target's graded walk-forward rows: `CURVE_SCHEMA` without `cdf`, then the per-row
    scores of the model and both baselines (extra columns)."""
    t = ev.target
    rows = ev.rows
    # the backtest keeps per-row numbers (quantiles, scores), not the curves: too large
    q = ev.quantiles[s]
    m, b, o = ev.model[s], ev.bell, ev.own
    n = rows.height
    df = rows.select(
        "season",
        "week",
        "game_id",
        "kickoff_utc",
        "player_id",
        "player",
        "team",
        "opponent",
        "position",
        "group",
        "p10",
        "p50",
        "p90",
        "mean",
        pl.col("model_version").alias("source_version"),
        "actual",
        "played",
    ).with_columns(  # fmt: skip
        pl.lit(None, dtype=S.UTC).alias("created_at"),
        pl.lit(t.name).alias("target"),
        pl.lit(t.key).alias("target_key"),
        pl.lit(t.label).alias("target_label"),
        pl.lit(t.kind).alias("kind"),
        pl.lit(ev.lo, dtype=pl.Int32).alias("grid_lo"),
        *[pl.Series(f"q{int(round(x * 100)):02d}", q[:, i]) for i, x in enumerate(QS)],
        pl.lit(t.band).alias("band"),
        pl.Series("p_band", m.p_band),
        pl.Series("bell_p_band", b.p_band),
        _marks_series(t, m.p_over, b.p_over),
        pl.Series("n_analogs", ev.n_analogs[s].astype(np.int32)),
        pl.lit(f"curves-backtest:{version}").alias("model_version"),
        pl.Series("crps_model", m.crps),
        pl.Series("crps_bell", b.crps),
        pl.Series("crps_own", o.crps),
        pl.Series("brier_model", m.brier),
        pl.Series("brier_bell", b.brier),
        pl.Series("brier_own", o.brier),
        pl.Series("own_p_band", o.p_band),
        pl.Series("own_n", ev.own_n.astype(np.int32)),
    )
    assert df.height == n
    schema = {
        **_no_cdf_schema(),
        **{c: pl.Float64() for c in EXTRA_COLS if c != "own_n"},
        "own_n": pl.Int32(),
    }
    return S.conform(df, schema)


# ---- JSON / files -------------------------------------------------------------------------------
def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.floating, float)):
        v = float(x)
        return v if np.isfinite(v) else None
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, datetime):
        return x.isoformat()
    if isinstance(x, Settings):
        return x.as_dict()
    return x


def atomic_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(_jsonable(doc), indent=1), encoding="utf-8")
    replace_file(tmp, path)  # retried while a reader has the file open (Windows)


def model_root(paths: DataPaths) -> Path:
    return S.model_dir(paths, FAMILY)


def load_production(paths: DataPaths) -> dict[str, Any]:
    f = S.production_file(paths, FAMILY)
    if not f.exists():
        raise FileNotFoundError(
            "no promoted curves model yet: run `nfl forecastlab backtest --promote` (FL01)"
        )
    doc = json.loads(f.read_text(encoding="utf-8"))
    v = doc.get("version")
    if not isinstance(v, str) or not re.fullmatch(VERSION_RE, v):
        raise ValueError("production.json: not a curves model version (<first>-<last>_<UTC stamp>)")
    return doc


# ---- the backtest -------------------------------------------------------------------------------
def _wb_table(rows: list[dict[str, Any]]):
    import wandb

    if not rows:
        return wandb.Table(columns=["empty"], data=[])
    cols = list(rows[0].keys())
    return wandb.Table(columns=cols, data=[[_jsonable(r.get(c)) for c in cols] for r in rows])


class _Live:
    """The live `bt/*` curves: one point per scored week of each target (`bt/step` counts
    them), cumulative scores of the model, the bell curve and the own-history curve."""

    def __init__(self, run, key: str, step0: int) -> None:
        self.run, self.key, self.step = run, key, step0
        self.n = 0.0
        self.acc = {k: 0.0 for k in ("crps_m", "crps_b", "crps_o", "br_m", "br_b", "pb", "hit")}

    def __call__(self, season: int, week: int, y: np.ndarray, sc: dict[str, C.RowScores]) -> None:
        self.step += 1
        m, b, o = sc.get("model"), sc["bell"], sc["own"]
        self.n += len(y)
        a = self.acc
        a["crps_b"] += float(b.crps.sum())
        a["crps_o"] += float(o.crps.sum())
        a["br_b"] += float(b.brier.sum())
        point: dict[str, Any] = {
            "bt/step": self.step,
            f"bt/{self.key}/crps_bell": a["crps_b"] / self.n,
            f"bt/{self.key}/crps_own": a["crps_o"] / self.n,
            f"bt/{self.key}/brier_bell": a["br_b"] / self.n,
        }
        if m is not None:
            a["crps_m"] += float(m.crps.sum())
            a["br_m"] += float(m.brier.sum())
            point[f"bt/{self.key}/crps_model"] = a["crps_m"] / self.n
            point[f"bt/{self.key}/brier_model"] = a["br_m"] / self.n
            point[f"bt/{self.key}/week_crps_model"] = float(m.crps.mean())
        point[f"bt/{self.key}/week_crps_bell"] = float(b.crps.mean())
        self.run.log(point)


def run_backtest(
    *,
    targets: list[str] | None = None,
    seasons: list[int] | None = None,
    tune: bool = False,
    use_wandb: bool = True,
    launched_by: str | None = None,
    smoke: bool = False,
    promote: bool = False,
    log: Callable[..., None] = print,
    paths: DataPaths | None = None,
) -> dict[str, Any]:
    """`nfl forecastlab backtest`: see the module docstring. `tune` scores every candidate of
    `candidates(kind)` on the tuning window only and names the best per target (it ships
    nothing and writes only `backtests/curves/_tuning/summary.json`). Otherwise the chosen
    settings (`settings_for`) are scored over `seasons` (default: the reported window) and each
    target gets the pre-registered verdict. `smoke` is a few weeks of two targets in the
    smoke W&B group and its own folder."""
    paths = paths or ensure_data_root()
    cfg = curves_cfg()
    tune_seasons = parse_seasons(cfg["tune_seasons"])
    report = parse_seasons(cfg["report_seasons"])
    if promote and (tune or smoke):
        raise ValueError("only the reported backtest can be promoted (not tune / smoke)")
    score_seasons = sorted(seasons) if seasons else (tune_seasons if tune else report)
    if smoke and not seasons:
        score_seasons = score_seasons[:1]
    tl = select_targets(targets if targets else (SMOKE_TARGETS if smoke else None))
    max_weeks = SMOKE_WEEKS if smoke else None
    t_start = time.perf_counter()
    created = datetime.now(UTC).replace(microsecond=0)
    version = f"{score_seasons[0]}-{score_seasons[-1]}_{created.strftime('%Y%m%dT%H%M%SZ')}"
    kind = "tune" if tune else ("smoke" if smoke else "reported")

    run, url = None, None
    if use_wandb:
        from nflengine import tracking

        run = tracking.init_run(
            group=SMOKE_GROUP if smoke else GROUP,
            job_type="curves-backtest",
            config={
                "mode": kind,
                "seasons": score_seasons,
                "targets": [t.key for t in tl],
                "settings": {t.key: settings_for(t.key, cfg).as_dict() for t in tl},
                "history_from": cfg["history_from"],
                "tune_seasons": tune_seasons,
                "report_seasons": report,
                "ship_rule": C.SHIP_RULE,
                "dataset_version": tracking.dataset_version(paths),
                "git_commit": tracking.git_commit(),
            },
            tags=["fl01", "curves", kind, f"seasons:{score_seasons[0]}-{score_seasons[-1]}"],
            launched_by=launched_by,
            name=f"curves-backtest-{kind}-{score_seasons[0]}-{score_seasons[-1]}",
        )
        run.define_metric("bt/step")
        run.define_metric("bt/*", step_metric="bt/step")
        url = run.url
    out: dict[str, Any] = {"targets": {}, "version": None, "url": url}
    evals: dict[str, tuple[Evaluation, Settings]] = {}
    try:
        step = 0
        for t in tl:
            hist = load_history(paths, t)
            chosen = settings_for(t.key, cfg)
            sl = [chosen, *[s for s in candidates(t.kind) if s != chosen]] if tune else [chosen]
            live = _Live(run, t.key, step) if run is not None else None
            ev = evaluate(
                t, hist, sl, score_seasons, workers=4, max_weeks=max_weeks, keep=[chosen],
                on_step=live, primary=chosen,
            )  # fmt: skip
            step = live.step if live is not None else step
            pooled, by_season = summarize(ev, chosen)
            verdict = C.ship_verdict(pooled, by_season)
            entry: dict[str, Any] = {
                "pooled": pooled,
                "by_season": by_season,
                "verdict": verdict,
                "settings": chosen.as_dict(),
                "rows": ev.rows.height,
                "seconds": round(ev.seconds, 1),
            }
            if tune:
                table = tuning_table(ev)
                entry["tuning"] = table
                entry["best"] = table[0]["settings"]
                log(
                    f"{t.key}: tuning {score_seasons[0]}-{score_seasons[-1]}, {ev.rows.height:,} "
                    f"rows, {len(table)} candidates in {ev.seconds:.0f} s: best {table[0]['label']}"
                    f" ({table[0]['crps_vs_bell_pct']:+.2f}% CRPS vs the bell curve; "
                    f"bell {pooled['crps_bell']:.4f}, own {pooled['crps_own']:.4f})"
                )
            else:
                entry["reliability"] = reliability(ev, chosen)
                log(
                    f"{t.key}: CRPS model {pooled['crps_model']:.4f} / bell "
                    f"{pooled['crps_bell']:.4f} / own {pooled['crps_own']:.4f}; seasons won "
                    f"{verdict['rule']['crps_vs_bell']['seasons_won']}/{len(by_season)}; Brier "
                    f"{pooled['brier_model']:.4f} vs {pooled['brier_bell']:.4f}; band "
                    f"{pooled['band_pred']:.3f} vs {pooled['band_hit']:.3f} happened -> "
                    f"{'SHIPS' if verdict['ships'] else 'does not ship'}"
                )
            out["targets"][t.key] = entry
            evals[t.key] = (ev, chosen)
        out["seconds"] = round(time.perf_counter() - t_start, 1)
        out["mode"] = kind
        out["seasons"] = score_seasons
        if run is not None:
            _log_backtest(run, out, evals, tune=tune)
        if tune:
            atomic_json(
                S.backtest_dir(paths, "curves", "_tuning") / "summary.json",
                {"seasons": score_seasons, "created_at": created, "wandb_run": url,
                 "targets": out["targets"]},
            )  # fmt: skip
            log(f"tuning table -> {S.backtest_dir(paths, 'curves', '_tuning')}")
        else:
            _write_backtests(paths, out, evals, version, created, url, smoke, log)
            if promote:
                shipped = [k for k, v in out["targets"].items() if v["verdict"]["ships"]]
                _promote(paths, out, evals, version, created, url, shipped, run, log)
                out["version"] = version
    finally:
        if run is not None:
            run.finish()
    return out


def _write_backtests(
    paths: DataPaths,
    out: dict[str, Any],
    evals: dict[str, tuple[Evaluation, Settings]],
    version: str,
    created: datetime,
    url: str | None,
    smoke: bool,
    log: Callable[..., None],
) -> None:
    from nflengine.ops.lock import write_parquet_atomic

    for key, (ev, s) in evals.items():
        folder = S.backtest_dir(paths, "curves", f"_smoke/{key}" if smoke else key)
        write_parquet_atomic(backtest_frame(ev, s, version), folder / "predictions.parquet",
                             compression="zstd")  # fmt: skip
        e = out["targets"][key]
        atomic_json(
            folder / "summary.json",
            {
                "target": key,
                "version": version,
                "created_at": created,
                "seasons": out["seasons"],
                "settings": s.as_dict(),
                "history_from": curves_cfg()["history_from"],
                "pooled": e["pooled"],
                "by_season": e["by_season"],
                "verdict": e["verdict"],
                "reliability": e.get("reliability"),
                "wandb_run": url,
            },
        )
    log(f"backtests -> {S.backtest_dir(paths, 'curves', '_smoke' if smoke else '')}")


def _promote(
    paths: DataPaths,
    out: dict[str, Any],
    evals: dict[str, tuple[Evaluation, Settings]],
    version: str,
    created: datetime,
    url: str | None,
    shipped: list[str],
    run,
    log: Callable[..., None],
) -> None:
    """`models/forecastlab/curves/<version>/params.json` and `production.json`: which targets
    ship and the settings each uses (the curves are built from the walk-forward history files at
    run time, so these settings are the whole model).

    A run of only some targets (the addendum's amount-only re-run) is merged into the previous
    production pointer: the targets it did not run keep their parameters, their shipped status
    and their backtest numbers. `version` is the latest promotion; `sources` names, per target,
    the version (and `wandb_runs` the W&B run) whose backtest produced its parameters and
    verdict."""
    prev: dict[str, Any] = {}
    prev_doc: dict[str, Any] = {}
    pf = S.production_file(paths, FAMILY)
    if pf.exists():
        prev = json.loads(pf.read_text(encoding="utf-8"))
        old = model_root(paths) / str(prev.get("version")) / "params.json"
        if old.exists():
            prev_doc = json.loads(old.read_text(encoding="utf-8"))
    ran = set(evals)
    order = [t.key for t in CURVE_TARGETS]
    params = {**(prev.get("params") or {}), **{k: s.as_dict() for k, (_, s) in evals.items()}}
    params = {k: params[k] for k in order if k in params}
    kept = set(prev.get("shipped") or [])
    shipped_all = [k for k in order if (k in ran and k in shipped) or (k not in ran and k in kept)]
    prev_ver = prev.get("version")
    sources = {
        **{k: prev_ver for k in (prev.get("params") or {})},
        **(prev.get("sources") or {}),
        **{k: version for k in ran},
    }
    runs = {
        **{k: prev.get("wandb_run") for k in (prev.get("params") or {})},
        **(prev.get("wandb_runs") or {}),
        **{k: url for k in ran},
    }
    grids = {**(prev_doc.get("grids") or {})}
    grids.update({k: list(C.grid_bounds(k, ev.target.floor)) for k, (ev, _) in evals.items()})
    backtest = {**(prev_doc.get("backtest") or {})}
    for k, v in out["targets"].items():
        backtest[k] = {
            "pooled": v["pooled"],
            "verdict": {p: r["ok"] for p, r in v["verdict"]["rule"].items()},
        }
    doc = {
        "family": FAMILY,
        "version": version,
        "created_at": created,
        "history_from": curves_cfg()["history_from"],
        "seasons": out["seasons"],
        "shipped": shipped_all,
        "params": params,
        "sources": sources,
        "wandb_runs": runs,
        "grids": grids,
        "backtest": backtest,
        "wandb_run": url,
    }
    folder = model_root(paths) / version
    atomic_json(folder / "params.json", doc)
    atomic_json(
        pf,
        {
            "version": version,
            "promoted_at": created,
            "shipped": shipped_all,
            "params": params,
            "sources": sources,
            "wandb_runs": runs,
            "wandb_run": url,
        },
    )
    if run is not None:
        import wandb

        card = Path(__file__).resolve().parents[3] / "documentation" / "model_cards"
        card = card / "outcome-curves.md"
        art = wandb.Artifact(
            ARTIFACT,
            type="model",
            description=card.read_text(encoding="utf-8") if card.exists() else version,
            metadata={
                "version": version,
                "shipped": shipped_all,
                "seasons": out["seasons"],
                "sources": sources,
            },
        )
        art.add_dir(str(folder))
        run.log_artifact(art, aliases=[version, "production"])
    log(
        f"promoted: {ARTIFACT}:{version} is production (ships: {', '.join(shipped_all) or 'none'}; "
        f"this run: {', '.join(sorted(ran))})"
    )


def _log_backtest(
    run, out: dict[str, Any], evals: dict[str, tuple[Evaluation, Settings]], tune: bool
) -> None:
    import wandb

    tables: dict[str, Any] = {}
    pooled_rows, season_rows, verdict_rows, rel_rows, pit_rows, tune_rows = [], [], [], [], [], []
    for key, e in out["targets"].items():
        pooled_rows.append({"target": key, **e["pooled"]})
        season_rows += [{"target": key, **r} for r in e["by_season"]]
        v = e["verdict"]
        verdict_rows.append({
            "target": key, "ships": v["ships"],
            **{f"{p}_ok": r["ok"] for p, r in v["rule"].items()},
            "seasons_won": v["rule"]["crps_vs_bell"]["seasons_won"],
            "band_gap_pp": v["rule"]["band"]["gap_pp"],
        })  # fmt: skip
        ev, s = evals[key]
        for name, rs in (("model", ev.model[s]), ("bell", ev.bell), ("own", ev.own)):
            share = rs.pit.mean(axis=0)
            pit_rows += [
                {"target": key, "source": name, "bin": i, "share": float(x), "uniform": 0.1}
                for i, x in enumerate(share)
            ]
        if tune:
            tune_rows += [
                {"target": key, "rank": i + 1, **{k: v_ for k, v_ in r.items() if k != "settings"}}
                for i, r in enumerate(e["tuning"])
            ]
        else:
            rel_rows += e["reliability"]
    tables["backtest/pooled_rows"] = _wb_table(pooled_rows)
    tables["backtest/by_season_rows"] = _wb_table(season_rows)
    tables["backtest/verdict_rows"] = _wb_table(verdict_rows)
    tables["backtest/pit_rows"] = _wb_table(pit_rows)
    if tune:
        tables["backtest/tuning_rows"] = _wb_table(tune_rows)
    else:
        tables["backtest/reliability_rows"] = _wb_table(rel_rows)
        for key in out["targets"]:
            m = {r["bin"]: r for r in rel_rows if r["target"] == key and r["source"] == "model"}
            bl = {r["bin"]: r for r in rel_rows if r["target"] == key and r["source"] == "bell"}
            bins = [i for i in sorted(m) if m[i]["n"] and bl.get(i, {}).get("n")]
            if bins:
                tables[f"reliability/{key}"] = wandb.plot.line_series(
                    xs=[(i + 0.5) / 10 for i in bins],
                    ys=[
                        [m[i]["happened"] for i in bins],
                        [bl[i]["happened"] for i in bins],
                        [(i + 0.5) / 10 for i in bins],
                    ],
                    keys=["curve", "bell curve", "perfect"],
                    title=f"{key}: chance of going over a mark (10 bins) vs how often it did",
                    xname="predicted chance",
                )  # fmt: skip
        for key, e in out["targets"].items():
            seas = [r["season"] for r in e["by_season"]]
            tables[f"crps_by_season/{key}"] = wandb.plot.line_series(
                xs=seas,
                ys=[
                    [r[c] for r in e["by_season"]] for c in ("crps_model", "crps_bell", "crps_own")
                ],
                keys=["curve", "bell curve", "own history"],
                title=f"{key}: CRPS by season (lower is better)",
                xname="season",
            )
    run.log(tables)
    summ: dict[str, Any] = {}
    for key, e in out["targets"].items():
        p = e["pooled"]
        summ.update({
            f"{key}/crps_model": p["crps_model"], f"{key}/crps_bell": p["crps_bell"],
            f"{key}/crps_own": p["crps_own"],
            f"{key}/crps_vs_bell_pct": 100 * (p["crps_bell"] - p["crps_model"]) / p["crps_bell"],
            f"{key}/brier_model": p["brier_model"], f"{key}/brier_bell": p["brier_bell"],
            f"{key}/band_pred": p["band_pred"], f"{key}/band_hit": p["band_hit"],
            f"{key}/pit_ece": p["pit_ece"], f"{key}/pit_ece_bell": p["pit_ece_bell"],
            f"{key}/rows": p["n"], f"{key}/ships": e["verdict"]["ships"],
        })  # fmt: skip
        if tune:
            summ[f"{key}/best"] = e["tuning"][0]["label"]
    if not tune:
        summ["shipped"] = ",".join(k for k, e in out["targets"].items() if e["verdict"]["ships"])
    run.summary.update(summ)


# ---- the zero variant (addendum, pre-registered 2026-11-14 22:27 UTC) ---------------------
def run_variant_check(
    *,
    variant: str = "analog_zero",
    targets: list[str] | None = None,
    use_wandb: bool = True,
    launched_by: str | None = None,
    log: Callable[..., None] = print,
    paths: DataPaths | None = None,
) -> dict[str, Any]:
    """The variant against the current `analog` method on the tuning window only (2019-2020),
    each amount target with its chosen K, similarity space, history window and width floor.
    The variant replaces the current method for a target if (1) its pooled CRPS is no worse
    than the current method's by more than 0.1% and (2) its Brier score for "1 or more yards"
    is lower than the current method's. Writes `backtests/curves/_tuning/zero_variant.json`;
    nothing is promoted and no reported season is touched."""
    paths = paths or ensure_data_root()
    cfg = curves_cfg()
    seasons = parse_seasons(cfg["tune_seasons"])
    tl = [t for t in select_targets(targets) if t.kind == "amount"]
    run, url = None, None
    if use_wandb:
        from nflengine import tracking

        run = tracking.init_run(
            group=GROUP,
            job_type="curves-backtest",
            config={
                "mode": "tune",
                "addendum": variant,
                "seasons": seasons,
                "targets": [t.key for t in tl],
                "rule": {"crps_worse_by_more_than_pct": 0.1, "brier_ge1": "lower than current"},
                "dataset_version": tracking.dataset_version(paths),
                "git_commit": tracking.git_commit(),
            },
            tags=[
                "fl01",
                "curves",
                "tune",
                "addendum",
                variant,
                f"seasons:{seasons[0]}-{seasons[-1]}",
            ],
            launched_by=launched_by,
            name=f"curves-variant-{variant}-tune-{seasons[0]}-{seasons[-1]}",
        )
        url = run.url
    out: dict[str, Any] = {"variant": variant, "seasons": seasons, "targets": {}, "url": url}
    try:
        for t in tl:
            hist = load_history(paths, t)
            base = replace(settings_for(t.key, cfg), method="analog")
            var = replace(base, method=variant)
            ev = evaluate(t, hist, [base, var], seasons, workers=4)
            pb, _ = summarize(ev, base)
            pv, _ = summarize(ev, var)
            change = 100.0 * (pv["crps_model"] - pb["crps_model"]) / pb["crps_model"]
            ok_crps = change <= 0.1
            ok_brier = pv["brier_ge1_model"] < pb["brier_ge1_model"]
            out["targets"][t.key] = {
                "current": base.as_dict(),
                "variant": var.as_dict(),
                "rows": int(pb["n"]),
                "crps_current": pb["crps_model"],
                "crps_variant": pv["crps_model"],
                "crps_bell": pb["crps_bell"],
                "crps_change_pct": change,
                "brier_ge1_current": pb["brier_ge1_model"],
                "brier_ge1_variant": pv["brier_ge1_model"],
                "brier_ge1_bell": pb["brier_ge1_bell"],
                "brier_ge1_own": pb["brier_ge1_own"],
                "ge1_pred_current": pb["ge1_pred"],
                "ge1_pred_variant": pv["ge1_pred"],
                "ge1_hit": pb["ge1_hit"],
                "ok_crps": bool(ok_crps),
                "ok_brier_ge1": bool(ok_brier),
                "replace": bool(ok_crps and ok_brier),
            }
            log(
                f"{t.key}: CRPS {pb['crps_model']:.4f} -> {pv['crps_model']:.4f} ({change:+.3f}%); "
                f"Brier(>=1) {pb['brier_ge1_model']:.5f} -> {pv['brier_ge1_model']:.5f} "
                f"(bell {pb['brier_ge1_bell']:.5f}) -> "
                f"{'REPLACE' if ok_crps and ok_brier else 'keep the current method'}"
            )
        atomic_json(
            S.backtest_dir(paths, "curves", "_tuning") / "zero_variant.json",
            {"created_at": datetime.now(UTC), "wandb_run": url, **out},
        )
        if run is not None:
            rows = [
                {"target": k, **{a: b for a, b in v.items() if not isinstance(b, dict)}}
                for k, v in out["targets"].items()
            ]
            run.log({"variant/comparison_rows": _wb_table(rows)})
            keys = (
                "crps_change_pct",
                "brier_ge1_current",
                "brier_ge1_variant",
                "brier_ge1_bell",
                "replace",
            )
            run.summary.update({f"{k}/{a}": v[a] for k, v in out["targets"].items() for a in keys})
    finally:
        if run is not None:
            run.finish()
    return out


# ---- the weekly curves --------------------------------------------------------------------------
def week_dir(paths: DataPaths, season: int, week: int) -> Path:
    return S.week_dir(paths, season, week)


def run_predict(
    season: int,
    week: int,
    *,
    use_wandb: bool = True,
    launched_by: str | None = None,
    log: Callable[..., None] = print,
    paths: DataPaths | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """`nfl forecastlab predict`: the week's curves for every projection of a shipped target,
    from rows strictly before the week -> `curves.parquet` + `curves.json`. A game that has
    kicked off (`kickoff_utc <= now`) is never re-made: curves the file already holds for it
    stay exactly as they are; a game with none gets curves anyway and is listed in
    `games_after_kickoff` (the tab marks them as made after kickoff)."""
    paths = paths or ensure_data_root()
    t0 = time.perf_counter()
    prod = load_production(paths)
    shipped = list(prod.get("shipped") or [])
    params = prod.get("params") or {}
    made_at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    f = paths.run_dir(season, week) / PRED_FILE
    if not f.exists():
        raise FileNotFoundError(
            f"no player projections for {season} week {week}: {f} (run the week first)"
        )
    proj = S.read_parquet(f)
    src_versions = sorted({str(v) for v in proj["model_version"].unique().to_list() if v})
    src_created = proj["created_at"].max() if proj.height else None
    frames, hist_n, hist_last = [], {}, []
    for key in shipped:
        t = S.CURVE_BY_KEY[key]
        sub = proj.filter(
            (pl.col("target") == t.name)
            & (pl.col("group") == t.group)
            & pl.col("p10").is_not_null()
            & pl.col("p50").is_not_null()
            & pl.col("p90").is_not_null()
            & pl.col("mean").is_not_null()
        )
        if sub.is_empty():
            continue
        s = Settings.from_dict(params[key])
        hist = load_history(paths, t, before=(season, week))
        hist_n[key] = hist.height
        if hist.height:  # sorted by time: the newest (season, week) the history holds
            hist_last.append((int(hist["season"][-1]), int(hist["week"][-1])))
        lo, hi = C.grid_bounds(key, t.floor)
        rows = sub.sort("player_id", "game_id")
        curves, bell, _, _ = C.step_curves(t.kind, [s], hist, rows, lo=lo, hi=hi, workers=2)
        c = curves[s]
        frames.append(
            curve_frame(
                t,
                rows,
                c.cdf,
                bell,
                c.n_analogs,
                lo=lo,
                created_at=made_at,
                model_version=prod["version"],
                with_cdf=True,
            )  # fmt: skip
        )
    new = pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame(schema=CURVE_SCHEMA)
    path = week_dir(paths, season, week) / CURVES_FILE
    started = set(
        proj.filter(pl.col("kickoff_utc") <= pl.lit(made_at).cast(S.UTC))["game_id"].to_list()
    )
    through = None
    if hist_last:
        ts, tw = max(hist_last)
        through = f"{ts}-w{tw:02d}"
    from nflengine.ops.lock import file_lock, write_parquet_atomic

    path.parent.mkdir(parents=True, exist_ok=True)
    # the week's file is read, merged and written under one lock: a hand-run predict and an
    # update's grade never drop each other's rows or results
    with file_lock(path):
        kept_games: list[str] = []
        if started:
            old = S.read_parquet(path) if path.exists() else pl.DataFrame(schema=CURVE_SCHEMA)
            kept_games = sorted(started & set(old["game_id"].to_list()))
            if kept_games:
                new = pl.concat(
                    [
                        new.filter(~pl.col("game_id").is_in(kept_games)),
                        S.conform(old.filter(pl.col("game_id").is_in(kept_games)), CURVE_SCHEMA),
                    ],
                    how="vertical_relaxed",
                )
        new = new.sort("game_id", "target_key", "player_id")
        # made after kickoff: read off the rows themselves, so a game made late stays flagged
        # on every later run (its saved rows are kept)
        late = sorted(
            new.filter(pl.col("created_at") >= pl.col("kickoff_utc"))["game_id"].unique().to_list()
        )
        write_parquet_atomic(new, path, compression="zstd")
        meta = {
            "season": season,
            "week": week,
            "made_at": made_at,
            "rows": new.height,
            "games": int(new["game_id"].n_unique()) if new.height else 0,
            "version": prod["version"],
            "shipped": shipped,
            "source_version": (
                src_versions[0] if len(src_versions) == 1 else ", ".join(src_versions)
            ),
            "source_created_at": src_created,
            "games_after_kickoff": late,
            "games_kept": kept_games,
            "rows_by_target": {k: int(v) for k, v in new.group_by("target_key").len().iter_rows()},
            "history_rows": hist_n,
            # the newest (season, week) the history really holds; null when it holds nothing
            "through": through,
            "seconds": round(time.perf_counter() - t0, 1),
        }
        atomic_json(week_dir(paths, season, week) / META_FILE, meta)
    log(
        f"{season} week {week}: {new.height} curves for {meta['games']} games "
        f"({', '.join(shipped) or 'no shipped target'}) in {meta['seconds']} s -> {path}"
        + (f"; {len(late)} game(s) already kicked off, made late" if late else "")
        + (f"; kept {len(kept_games)} started game(s) as saved" if kept_games else "")
    )
    url = None
    if use_wandb and new.height:
        url = _log_predict_run(new, meta, launched_by, paths)
    return {"meta": _jsonable(meta), "frame": new, "url": url}


def _log_predict_run(
    frame: pl.DataFrame, meta: dict[str, Any], launched_by: str | None, paths: DataPaths
) -> str | None:
    from nflengine import tracking

    s, w = meta["season"], meta["week"]
    run = tracking.init_run(
        group=GROUP,
        job_type="curves-predict",
        config={
            "season": s,
            "week": w,
            "version": meta["version"],
            "shipped": meta["shipped"],
            "source_version": meta["source_version"],
            "dataset_version": tracking.dataset_version(paths),
            "git_commit": tracking.git_commit(),
        },
        tags=["fl01", "curves", f"season:{s}", f"week:{w:02d}"],
        launched_by=launched_by,
        name=f"curves-predict-{s}-w{w:02d}",
    )
    try:
        gaps = []
        for r in frame.select(
            "player",
            "team",
            "opponent",
            "target_key",
            "p50",
            "mean",
            "p_band",
            "bell_p_band",
            "marks",
        ).iter_rows(named=True):
            m = max(r["marks"], key=lambda x: abs(x["p_over"] - x["bell_p_over"]))
            gaps.append({
                "player": r["player"], "team": r["team"], "target": r["target_key"],
                "projection": r["p50"] if r["target_key"].split("-")[0] in
                ("pass_yds", "rush_yds", "scrim_yds", "rec_yds") else r["mean"],
                "mark": m["mark"], "curve_over": m["p_over"], "bell_over": m["bell_p_over"],
                "gap": abs(m["p_over"] - m["bell_p_over"]),
            })  # fmt: skip
        gaps.sort(key=lambda r: -r["gap"])
        run.log({"curves/biggest_gaps": _wb_table(gaps[:50])})
        run.summary.update(
            {"rows": meta["rows"], "games": meta["games"], "seconds": meta["seconds"],
             **{f"rows/{k}": v for k, v in meta["rows_by_target"].items()}}
        )  # fmt: skip
        return run.url
    finally:
        run.finish()


# ---- grading ------------------------------------------------------------------------------------
def week_actuals(paths: DataPaths, season: int, week: int) -> tuple[pl.DataFrame, str]:
    """(player_id, game_id, target, group, played, actual) of the week and where it came from:
    the production scoreboard's own graded file (`predictions_players_scored.parquet`: every
    projected player, `played` false for a player who didn't play), else the season's
    walk-forward file (only players who played; a game with rows there is done, so a projected
    player missing from it didn't play)."""
    keys = ["player_id", "game_id", "target", "group", "played", "actual"]
    sc = paths.run_dir(season, week) / SCORED_FILE
    if sc.exists():
        df = S.read_parquet(sc)
        return df.select(keys), "scored"
    wf = paths.runs / str(season) / WALK_FILE
    if wf.exists():
        df = read_cached(wf, ["season", "week", *keys])
        df = df.filter((pl.col("week") == week) & (pl.col("season") == season))
        return df.select(keys), "walkforward"
    empty = {k: pl.String for k in keys}
    empty.update(played=pl.Boolean, actual=pl.Float64)
    return pl.DataFrame(schema=empty), "none"


def fill_actuals(cur: pl.DataFrame, act: pl.DataFrame, source: str) -> pl.DataFrame:
    """The curves frame with `actual` / `played` from `act`. What the source doesn't know yet
    stays as it was (null on a first grade; a result already filled is a fact and is kept), so
    a week can be graded in pieces: a later grade fills the games the source gained."""
    j = cur.rename({"played": "_old_played", "actual": "_old_actual"}).join(
        act.rename({"played": "_played", "actual": "_actual"}),
        on=["player_id", "game_id", "target", "group"],
        how="left",
    )
    if source == "walkforward":
        done = set(act["game_id"].to_list())
        new_played = (
            pl.when(pl.col("_played").is_not_null())
            .then(pl.col("_played"))
            .when(pl.col("game_id").is_in(list(done)))
            .then(pl.lit(False))
        )
    else:
        new_played = pl.col("_played")
    played = pl.coalesce(new_played, pl.col("_old_played"))
    j = j.with_columns(
        played.alias("played"),
        pl.when(played).then(pl.coalesce(pl.col("_actual"), pl.col("_old_actual"))).alias("actual"),
    ).drop("_played", "_actual", "_old_played", "_old_actual")
    return S.conform(j, CURVE_SCHEMA)


def run_grade(
    season: int,
    week: int,
    *,
    use_wandb: bool = True,
    launched_by: str | None = None,
    log: Callable[..., None] = print,
    paths: DataPaths | None = None,
) -> dict[str, Any]:
    """`nfl forecastlab grade`: fill `actual` / `played` in the week's `curves.parquet` and
    upsert the week's rows of `curves_scoreboard.parquet` (one per target: the published
    curve and both baselines on the players who played). A re-run gives the same files."""
    paths = paths or ensure_data_root()
    path = week_dir(paths, season, week) / CURVES_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"no curves for {season} week {week}: run `nfl forecastlab predict --season "
            f"{season} --week {week}` first"
        )
    from nflengine.ops.lock import file_lock, write_parquet_atomic

    act, source = week_actuals(paths, season, week)
    with file_lock(path):  # read, fill, write: a predict never drops graded results
        cur = fill_actuals(S.read_parquet(path), act, source)
        write_parquet_atomic(cur, path, compression="zstd")
    # a curve made after its game kicked off is not a forecast: it keeps its results (they are
    # facts) but is left out of the live score
    late = set(
        cur.filter(pl.col("created_at") >= pl.col("kickoff_utc"))["game_id"].unique().to_list()
    )
    is_graded = pl.col("played").fill_null(False) & pl.col("actual").is_not_null()
    graded_rows = cur.filter(is_graded)
    excluded = int(graded_rows.filter(pl.col("game_id").is_in(sorted(late))).height)
    rows = []
    for key in sorted(cur["target_key"].unique().to_list()):
        t = S.CURVE_BY_KEY[key]
        sub = graded_rows.filter(
            (pl.col("target_key") == key) & ~pl.col("game_id").is_in(sorted(late))
        )
        if sub.is_empty():
            continue
        lo, hi = C.grid_bounds(key, t.floor)
        y = sub["actual"].to_numpy().astype(float)
        p50 = sub["p50"].to_numpy().astype(float)
        mean = sub["mean"].to_numpy().astype(float)
        w_ = (sub["p90"] - sub["p10"]).to_numpy().astype(float)
        centre = C.centre_of(t.kind, p50, mean)
        bell = C.bell_cdf(t.kind, p50, w_, mean, lo, hi)
        hist = load_history(paths, t, before=(season, week))
        own, _ = C.own_cdf(C.own_prior(hist, sub["player_id"].to_list()), bell, lo, hi)
        model = dense_cdf(sub["cdf"], hi - lo + 1)
        m = C.score_curve(model, y, centre, t.band, t.marks, lo)
        b = C.score_curve(bell, y, centre, t.band, t.marks, lo)
        o = C.score_curve(own, y, centre, t.band, t.marks, lo)
        rows.append({
            "season": season, "week": week, "target_key": key, "n": sub.height,
            "crps_model": float(m.crps.mean()), "crps_bell": float(b.crps.mean()),
            "crps_own": float(o.crps.mean()),
            "brier_model": float(m.brier.mean()), "brier_bell": float(b.brier.mean()),
            "brier_own": float(o.brier.mean()),
            "band_pred": float(m.p_band.mean()),
            "band_hit": float(C.band_hit(y, centre, t.band).mean()),
            "pit_ece": C.pit_ece(m.pit), "mode": "live",
        })  # fmt: skip
    board_path = S.season_dir(paths, season) / SCOREBOARD_FILE
    graded_at = datetime.now(UTC).replace(microsecond=0)
    with file_lock(board_path):
        old = S.read_parquet(board_path) if board_path.exists() else None
        prev = {}
        if old is not None:
            for r in old.filter(pl.col("week") == week).iter_rows(named=True):
                prev[r["target_key"]] = r
        new_rows = []
        for r in rows:
            p = prev.get(r["target_key"])
            same = p is not None and all(_same(p.get(k), r[k]) for k in r if k not in ("mode",))
            new_rows.append({**r, "graded_at": p["graded_at"] if same else graded_at})
        frame = (
            S.conform(pl.DataFrame(new_rows), CURVE_SCORE_SCHEMA)
            if new_rows
            else pl.DataFrame(schema=CURVE_SCORE_SCHEMA)
        )
        if old is not None:
            keep = old.filter(pl.col("week") != week)
            frame = pl.concat([S.conform(keep, CURVE_SCORE_SCHEMA), frame], how="vertical_relaxed")
        frame = frame.sort("season", "week", "target_key")
        if new_rows or old is not None:
            write_parquet_atomic(frame, board_path, compression="zstd")
    week_rows = frame.filter(pl.col("week") == week)
    summary = {
        "season": season,
        "week": week,
        "source": source,
        "rows": cur.height,
        "graded": graded_rows.height,
        "excluded_after_kickoff": excluded,
        "scored": graded_rows.height - excluded,
        "games_after_kickoff": sorted(late),
        "not_played": int(cur.filter(pl.col("played") == False).height),  # noqa: E712
        "waiting": int(cur["played"].is_null().sum()),
        "targets": {r["target_key"]: r for r in week_rows.iter_rows(named=True)},
    }
    log(
        f"{season} week {week}: graded {summary['graded']} of {cur.height} curves from the "
        f"{source} actuals ({summary['not_played']} did not play, {summary['waiting']} waiting)"
        f" -> {board_path}"
    )
    url = None
    if use_wandb and week_rows.height:
        url = _log_grade_run(week_rows, summary, launched_by, paths)
    return {"summary": _jsonable(summary), "frame": week_rows, "url": url}


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is b
        return bool(np.isclose(a, b, rtol=0, atol=1e-12, equal_nan=True))
    return bool(a == b)


def _log_grade_run(
    week_rows: pl.DataFrame, summary: dict[str, Any], launched_by: str | None, paths: DataPaths
) -> str | None:
    import wandb

    from nflengine import tracking

    s, w = summary["season"], summary["week"]
    run = tracking.init_run(
        group=GROUP,
        job_type="curves-grade",
        config={
            "season": s,
            "week": w,
            "source": summary["source"],
            "git_commit": tracking.git_commit(),
            "dataset_version": tracking.dataset_version(paths),
        },
        tags=["fl01", "curves", f"season:{s}", f"week:{w:02d}"],
        launched_by=launched_by,
        name=f"curves-grade-{s}-w{w:02d}",
    )
    try:
        run.log(
            {"grade/targets_rows": wandb.Table(dataframe=week_rows.drop("graded_at").to_pandas())}
        )
        run.summary.update(
            {"graded": summary["graded"], "rows": summary["rows"],
             "not_played": summary["not_played"], "waiting": summary["waiting"]}
        )  # fmt: skip
        return run.url
    finally:
        run.finish()
