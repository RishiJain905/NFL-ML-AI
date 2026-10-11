"""Files, W&B and commands for the play-calling forecast (PC02, D129): `nfl playcalling
backtest | forecast | grade`.

- `run_backtest`: walk-forward over the reported seasons: every team-game forecast as of its
  week by the model and the four baselines, the parameters fit on the tuning window; metrics
  by season and part of season, calibration, the 80% range's coverage, the pre-registered
  ship verdict (D130); one `playcall-backtest` W&B run with live curves; `--promote` refits
  the parameters on every reported season, saves `models/playcall/<version>/` and moves the
  `playcall-model` artifact's `production` alias (and `models/playcall/production.json`).
- `run_forecast`: one week's forecast for every game, from the production parameters, as of
  the week -> `playcalling/<S>/week<NN>/forecast.parquet` + `forecast.json`; W&B
  `playcall-forecast`.
- `run_grade`: a forecast vs what happened -> `playcalling/<S>/playcall_scoreboard.parquet`
  (one row per game, team and target); W&B `playcall-grade`.

Not a weekly-run step (D107): runbook lines after Tuesday's play-calling build.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nflengine.paths import DataPaths, ensure_data_root
from nflengine.playcalling import build as B
from nflengine.playcalling import forecast as F
from nflengine.playcalling import labels as L

FAMILY = "playcall-model"
VERSION_RE = r"^\d{4}-\d{4}_\d{8}T\d{6}Z$"  # <first>-<last>_<UTC stamp>: never a path
GROUP = "play-calling"
SMOKE_GROUP = "play-calling-smoke"
FORECAST_FILE = "forecast.parquet"
SCOREBOARD_FILE = "playcall_scoreboard.parquet"


def model_root(paths: DataPaths) -> Path:
    return paths.models / "playcall"


def backtest_root(paths: DataPaths) -> Path:
    return paths.playcalling / "backtests"


def week_dir(paths: DataPaths, season: int, week: int) -> Path:
    return B.season_dir(paths, season) / f"week{week:02d}"


# --- inputs ---------------------------------------------------------------------------------------
@dataclass
class Inputs:
    tg: pl.DataFrame
    games: pl.DataFrame
    changed: pl.DataFrame
    midseason: pl.DataFrame
    notes: list[str]


def load_inputs(paths: DataPaths, seasons: Iterable[int], lines: bool = False) -> Inputs:
    """The team-game sums of `seasons` (and the season before each), the games (with the
    `lines` table's current lines merged in when `lines`), and the coaching changes."""
    from nflengine.live.context import coach_fixes

    seasons = sorted(set(seasons))
    need = sorted({s for x in seasons for s in (x - 1, x) if s >= B.FIRST_SEASON})
    tg = F.load_team_games(paths, need)
    notes = [f"no play-calling build for {s}" for s in need if s not in set(
        tg["season"].unique().to_list())]  # fmt: skip
    games = F.load_games(paths)
    if lines:
        lf = paths.curated / "lines.parquet"
        ln = pl.read_parquet(lf) if lf.exists() else None
        cur = F.current_lines(games, ln)
        games = games.drop("spread_line", "total_line").join(cur, on="game_id", how="left")
    seed = F.read_seed()
    if seed.is_empty():
        notes.append("no coaching seed: only head-coach changes count as a new play-caller")
    changed, mid = F.coach_changes(games, seed, coach_fixes())
    return Inputs(tg=tg, games=games, changed=changed, midseason=mid, notes=notes)


# --- the game-script curves ---------------------------------------------------------------------
def load_curves(
    paths: DataPaths,
    seasons: Iterable[int],
    *,
    live_season: int | None = None,
    settings: dict[str, Any] | None = None,
    log: Callable[..., None] = print,
) -> dict[int, F.ScriptCurve]:
    """One script curve per season. A finished season's comes from LD00's `wp` + `pass` models
    refit on 2010 .. S-1 with LD00's own code and settings (walk-forward: nothing from S on);
    `live_season`'s from the promoted LD00 bundle (fit on 2010 .. S-1 too). Reference plays:
    the `ref_seasons` seasons before S."""
    from nflengine.live import data as LD
    from nflengine.live import models as LM
    from nflengine.live import schema as LS

    cfg = F.forecast_settings(settings)
    nref = int(cfg["ref_seasons"])
    bw = float(cfg["spread_bandwidth"])
    seasons = sorted(set(seasons))
    if not seasons:
        return {}
    t0 = time.perf_counter()
    plays = LD.with_state(LD.load_plays(range(2010, max(seasons)), paths=paths))
    wpf, _ = LD.wp_frame(plays)
    pf = LD.pass_frame(plays).with_columns(
        L.neutral(pl.col("wp"), pl.col("down"), pl.col("half_seconds_remaining")).alias("neutral")
    )
    log(f"script curves: {pf.height:,} plays 2010-{max(seasons) - 1} "
        f"({time.perf_counter() - t0:.1f} s)")  # fmt: skip
    mcfg = LM.settings()
    th, seed = int(mcfg["threads"]), int(mcfg["seed"])
    out: dict[int, F.ScriptCurve] = {}
    prod = None
    for s in seasons:
        ref = pf.filter(pl.col("season").is_between(s - nref, s - 1))
        if ref.is_empty():
            continue
        t = time.perf_counter()
        if live_season is not None and s == live_season:
            from nflengine.live.train import load_production, production_folder

            prod = prod or load_production(paths)
            wp_model, pass_model = prod.wp, prod.pass_
            label = f"LD00 production {production_folder(paths).name}"
        else:
            fit = pl.col("season") < s
            wp_model = LM.fit_wp(wpf.filter(fit), mcfg, th, seed)
            pfit = pf.filter(fit)
            pfit = pfit.with_columns(
                pl.Series("wp", wp_model.predict(LM.matrix(pfit, LS.WP_FEATURES)))
            )
            pass_model = LM.fit_pass(pfit, mcfg, th, seed)
            label = f"LD00 wp + pass refit on 2010-{s - 1}"
        x = ref.with_columns(pl.Series("wp", wp_model.predict(LM.matrix(ref, LS.WP_FEATURES))))
        p = pass_model.predict(LM.matrix(x, LS.PASS_FEATURES))
        out[s] = F.curve_from_plays(s, ref, p, label, bw)
        log(f"  {s}: {label}, {ref.height:,} reference plays ({time.perf_counter() - t:.1f} s)")
    return out


def live_season_of(paths: DataPaths, season: int) -> int | None:
    """`season` when LD00's promoted bundle was fit without it (its curve comes from that
    bundle), else None (the curve refits LD00's models on the seasons before it)."""
    try:
        from nflengine.live.train import production_folder

        meta = json.loads((production_folder(paths) / "meta.json").read_text(encoding="utf-8"))
        fit = [int(s) for s in meta.get("seasons", [])]
    except (FileNotFoundError, ValueError, KeyError):
        return None
    return season if fit and season > max(fit) else None


# --- the backtest ---------------------------------------------------------------------------------
@dataclass
class TargetResult:
    target: F.Target
    seasons: list[int]
    tune: list[int]
    fits: dict[str, dict[str, Any]]
    rng: dict[str, float]
    preds: pl.DataFrame
    by_season: dict[int, dict[str, dict[str, float]]]
    by_part: dict[str, dict[str, dict[str, float]]]
    pooled: dict[str, dict[str, float]]
    test: dict[str, dict[str, float]]
    calib: list[dict[str, float]]
    ece: float
    coverage: float
    coverage_by_season: dict[int, float]
    verdict: dict[str, Any]


def _scores(t: F.Target, sub: pl.DataFrame) -> dict[str, dict[str, float]]:
    y, n, k = (sub[c].to_numpy() for c in ("actual", "n", "k"))
    return {kind: F.score(t, y, n.astype(float), k, sub[kind].to_numpy()) for kind in F.KINDS}


def evaluate_target(
    t: F.Target,
    inp: Inputs,
    curves: dict[int, F.ScriptCurve],
    seasons: Sequence[int],
    tune: Sequence[int],
    cfg: dict[str, Any],
) -> TargetResult | None:
    """Fit the predictors on `tune`, forecast every team-game of `seasons` as of its week,
    and score them on one cohort (rows where every predictor and the actual exist)."""
    ss = [s for s in seasons if s >= t.first_season]
    tn = [s for s in tune if s in ss]
    if not ss or not tn:
        return None
    obs = F.observations(inp.tg, t)
    d = F.build_design(t, obs, inp.games, curves, inp.changed, inp.midseason, ss, settings=cfg)
    seas = d.rows["season"].to_numpy()
    tune_mask = np.isin(seas, tn)
    fits = {kind: F.fit_kind(d, kind, tune_mask, cfg) for kind in F.KINDS}
    parts = {kind: F.predict_parts(d, kind, fits[kind]["params"], cfg) for kind in F.KINDS}
    cohort = np.isfinite(d.y) & (d.n > 0)
    for kind in F.KINDS:
        cohort &= np.isfinite(parts[kind].forecast)
    rng = F.fit_range(d, parts["model"], tune_mask & cohort)
    lo, hi = F.predict_range(d, parts["model"], rng)
    pm = parts["model"]
    preds = d.rows.select(
        "season",
        "week",
        "game_id",
        "team",
        "opponent",
        "game_type",
        "spread",
        "total",
        pl.col("ctx").alias("context_team"),
    ).with_columns(
        pl.lit(t.key).alias("target"),
        pl.Series("k", d.a["f_k"]),
        pl.Series("n", d.n),
        pl.Series("actual", d.y),
        *[pl.Series(kind, parts[kind].forecast) for kind in F.KINDS],
        pl.Series("lo", lo),
        pl.Series("hi", hi),
        pl.Series("team_term", pm.team),
        pl.Series("opponent_term", pm.opponent),
        pl.Series("script_term", pm.script),
        pl.Series("prior", pm.prior),
        pl.Series("strength", pm.strength),
        pl.Series("league", pm.league),
        pl.Series("expected_plays", F.expected_plays(d)),
        pl.Series("cohort", cohort),
        pl.Series("tuning", tune_mask),
    )
    c = preds.filter(pl.col("cohort"))
    by_season = {
        int(s): _scores(t, c.filter(pl.col("season") == s)) for s in sorted(set(c["season"]))
    }
    by_part = {
        name: _scores(t, c.filter(pl.col("week").is_between(a, b))) for a, b, name in F.PARTS
    }
    pooled = _scores(t, c)
    test = _scores(t, c.filter(~pl.col("tuning")))
    calib, ece = F.calibration(
        t, c["actual"].to_numpy(), c["n"].to_numpy().astype(float), c["model"].to_numpy()
    )
    inr = (c["actual"] >= c["lo"]) & (c["actual"] <= c["hi"])
    cov = float(inr.mean()) if c.height else float("nan")
    cov_s = {int(s): float(inr.filter(c["season"] == s).mean()) for s in sorted(set(c["season"]))}
    verdict = F.ship_verdict(t, by_season, pooled, cfg["ship"])
    return TargetResult(t, ss, tn, fits, rng, preds, by_season, by_part, pooled, test, calib,
                        ece, cov, cov_s, verdict)  # fmt: skip


def proe_play_scores(paths: DataPaths, preds: pl.DataFrame) -> dict[str, Any]:
    """PROE's per-play scores: each neutral play's pass chance = its `xpass` + the game's PROE
    forecast (clipped to 0.001-0.999), Brier and log loss per predictor, and the model's
    per-play calibration (10 equal-count bins)."""
    p = preds.filter((pl.col("target") == "proe") & pl.col("cohort"))
    if p.is_empty():
        return {}
    frames = []
    for s in sorted(set(p["season"].to_list())):
        f = B.season_dir(paths, int(s)) / "plays_enriched.parquet"
        if not f.exists():
            continue
        frames.append(
            pl.scan_parquet(f)
            .filter(pl.col("neutral").fill_null(False) & pl.col("xpass").is_not_null())
            .select(
                "game_id",
                pl.col("offense").alias("team"),
                "xpass",
                pl.col("dropback").cast(pl.Float64).alias("y"),
            )  # fmt: skip
            .collect()
        )
    if not frames:
        return {}
    plays = pl.concat(frames).join(p.select("game_id", "team", *F.KINDS), on=["game_id", "team"])
    y = plays["y"].to_numpy()
    out: dict[str, Any] = {"plays": int(plays.height)}
    for kind in F.KINDS:
        q = np.clip(plays["xpass"].to_numpy() + plays[kind].to_numpy(), 1e-3, 1 - 1e-3)
        out[kind] = {
            "brier": float(np.mean((y - q) ** 2)),
            "logloss": float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q))),
        }
    q = np.clip(plays["xpass"].to_numpy() + plays["model"].to_numpy(), 1e-3, 1 - 1e-3)
    rows, err = F.calibration(F.get_target("dropback_rate"), y, np.ones_like(y), q)
    out["calibration"] = rows
    out["ece"] = err
    return out


def _wb_table(rows: list[dict[str, Any]]):
    import wandb

    if not rows:
        return wandb.Table(columns=["empty"], data=[])
    cols = list(rows[0].keys())
    return wandb.Table(columns=cols, data=[[r.get(c) for c in cols] for r in rows])


def _metric_rows(results: list[TargetResult]) -> tuple[list[dict], list[dict]]:
    seas, part = [], []
    for r in results:
        for s, m in r.by_season.items():
            for kind, sc in m.items():
                seas.append({"target": r.target.key, "season": s, "kind": kind, **sc})
        for name, m in r.by_part.items():
            for kind, sc in m.items():
                part.append({"target": r.target.key, "part": name, "kind": kind, **sc})
    return seas, part


def log_backtest(
    run, results: list[TargetResult], curves: dict[int, F.ScriptCurve], proe: dict[str, Any]
) -> None:
    """Live curves (one `bt/step` per week in time order, cumulative game-level MAE of every
    predictor per target), then the tables and charts the model card reads."""
    import wandb

    run.define_metric("bt/step")
    run.define_metric("bt/*", step_metric="bt/step")
    steps = sorted(
        {(int(s), int(w)) for r in results for s, w in
         r.preds.filter(pl.col("cohort")).select("season", "week").unique().iter_rows()}
    )  # fmt: skip
    sums = {r.target.key: {k: 0.0 for k in F.KINDS} for r in results}
    cnt = {r.target.key: 0 for r in results}
    by_step = {
        r.target.key: {
            (int(s), int(w)): g
            for (s, w), g in r.preds.filter(pl.col("cohort")).group_by(["season", "week"])
        }
        for r in results
    }
    for i, (s, w) in enumerate(steps, start=1):
        row: dict[str, Any] = {"bt/step": i, "bt/season": s, "bt/week": w}
        for r in results:
            key = r.target.key
            g = by_step[key].get((s, w))
            if g is None or g.is_empty():
                continue
            a = g["actual"].to_numpy()
            for kind in F.KINDS:
                e = np.abs(g[kind].to_numpy() - a)
                sums[key][kind] += float(e.sum())
                if kind in ("model", "b2"):
                    row[f"bt/{key}/week_mae_{kind}"] = float(e.mean())
            cnt[key] += g.height
            for kind in F.KINDS:
                row[f"bt/{key}/mae_{kind}"] = sums[key][kind] / cnt[key]
        run.log(row)

    seas, part = _metric_rows(results)
    ship = [{k: v for k, v in r.verdict.items() if k != "by_season"} for r in results]
    params = [
        {"target": r.target.key, "kind": kind, **fit["params"], "loss": fit["loss"],
         "at_bound": ",".join(fit.get("at_bound") or [])}
        for r in results for kind, fit in r.fits.items() if fit["params"]
    ]  # fmt: skip
    cov = [
        {"target": r.target.key, "season": s, "coverage": c}
        for r in results for s, c in r.coverage_by_season.items()
    ]  # fmt: skip
    crv = [
        {"season": s, "model": c.model, "ref_seasons": f"{c.ref_seasons[0]}-{c.ref_seasons[-1]}",
         "plays": c.n_plays, "dropback_at_-7": float(np.interp(-7, c.grid, c.db)),
         "dropback_at_0": float(np.interp(0, c.grid, c.db)),
         "dropback_at_+7": float(np.interp(7, c.grid, c.db)), "total_mean": c.total_mean}
        for s, c in sorted(curves.items())
    ]  # fmt: skip
    out: dict[str, Any] = {
        "backtest/by_season_rows": _wb_table(seas),
        "backtest/by_part_rows": _wb_table(part),
        "backtest/ship_rows": _wb_table(ship),
        "backtest/params_rows": _wb_table(params),
        "backtest/coverage_rows": _wb_table(cov),
        "backtest/script_curve_rows": _wb_table(crv),
    }
    for r in results:
        key = r.target.key
        ss = sorted(r.by_season)
        out[f"mae_vs_baseline/{key}"] = wandb.plot.line_series(
            xs=ss,
            ys=[
                [r.by_season[s][kind].get("mae", float("nan")) for s in ss]
                for kind in ("model", "b2", "b3", "b4", "b1")
            ],  # fmt: skip
            keys=[F.KIND_LABELS[k] for k in ("model", "b2", "b3", "b4", "b1")],
            title=f"{key}: game-level MAE by season (lower is better)",
            xname="season",
        )
        if r.calib:
            xs = [c["forecast"] for c in r.calib]
            out[f"calibration/{key}"] = wandb.plot.line_series(
                xs=xs,
                ys=[[c["actual"] for c in r.calib], xs],
                keys=["actual (play-weighted)", "perfect"],
                title=f"{key}: forecast vs actual, 10 equal-count bins",
                xname="forecast",
            )
    if proe.get("calibration"):
        xs = [c["forecast"] for c in proe["calibration"]]
        out["calibration/proe_per_play"] = wandb.plot.line_series(
            xs=xs,
            ys=[[c["actual"] for c in proe["calibration"]], xs],
            keys=["actual", "perfect"],
            title="PROE per play: xpass + forecast vs dropbacks, 10 bins",
            xname="pass chance",
        )
    run.log(out)
    summ: dict[str, Any] = {}
    for r in results:
        key = r.target.key
        pm, p2, p3 = (r.pooled[k].get("mae") for k in ("model", "b2", "b3"))
        summ.update({
            f"{key}/mae_model": pm, f"{key}/mae_b2": p2, f"{key}/mae_b3": p3,
            f"{key}/mae_b1": r.pooled["b1"].get("mae"), f"{key}/mae_b4": r.pooled["b4"].get("mae"),
            f"{key}/improvement_vs_b2_pct": 100 * (p2 - pm) / p2 if pm and p2 else None,
            f"{key}/wins_vs_b2": r.verdict["wins_vs_b2"], f"{key}/seasons": r.verdict["seasons"],
            f"{key}/ships": r.verdict["ships"], f"{key}/coverage_80": r.coverage,
            f"{key}/ece": r.ece, f"{key}/games": r.pooled["model"].get("games"),
            f"{key}/test_mae_model": r.test["model"].get("mae"),
            f"{key}/test_mae_b2": r.test["b2"].get("mae"),
        })  # fmt: skip
        if r.target.share:
            summ[f"{key}/brier_model"] = r.pooled["model"].get("brier")
            summ[f"{key}/brier_b2"] = r.pooled["b2"].get("brier")
    for kind in F.KINDS:
        if kind in proe:
            summ[f"proe/per_play_brier_{kind}"] = proe[kind]["brier"]
            summ[f"proe/per_play_logloss_{kind}"] = proe[kind]["logloss"]
    summ["shipped"] = ",".join(r.target.key for r in results if r.verdict["ships"])
    run.summary.update(summ)


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
    return x


def _atomic_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(_jsonable(doc), indent=1), encoding="utf-8")
    os.replace(tmp, path)


def result_doc(r: TargetResult) -> dict[str, Any]:
    return {
        "target": r.target.key,
        "seasons": r.seasons,
        "tune_seasons": r.tune,
        "fits": r.fits,
        "range": r.rng,
        "by_season": r.by_season,
        "by_part": r.by_part,
        "pooled": r.pooled,
        "test": r.test,
        "calibration": r.calib,
        "ece": r.ece,
        "coverage_80": r.coverage,
        "coverage_by_season": r.coverage_by_season,
        "verdict": r.verdict,
    }


def run_backtest(
    seasons: Sequence[int] | None = None,
    tune: Sequence[int] | None = None,
    targets: Iterable[str] | None = None,
    *,
    promote: bool = False,
    use_wandb: bool = True,
    launched_by: str | None = None,
    smoke: bool = False,
    save: bool = True,
    paths: DataPaths | None = None,
    log: Callable[..., None] = print,
) -> dict[str, Any]:
    """`nfl playcalling backtest`: fit on the tuning window, forecast every team-game of the
    reported seasons as of its week, score, verdicts (D130); `promote` refits on every reported
    season and makes the shipped targets production."""
    paths = paths or ensure_data_root()
    cfg = F.forecast_settings()
    seasons = sorted(set(seasons or F.parse_seasons(cfg["report_seasons"])))
    tune = sorted(set(tune or F.parse_seasons(cfg["tune_seasons"])))
    if not set(tune) <= set(seasons):
        raise ValueError("the tuning seasons must be inside the reported seasons")
    tl = F.select_targets(targets)
    t0 = time.perf_counter()
    inp = load_inputs(paths, seasons)
    for note in inp.notes:
        log(f"[yellow]{note}[/]")
    curves = load_curves(paths, range(min(seasons) - 1, max(seasons) + 1), settings=cfg, log=log)
    results: list[TargetResult] = []
    for t in tl:
        r = evaluate_target(t, inp, curves, seasons, tune, cfg)
        if r is None:
            log(f"[yellow]{t.key}: no data in {seasons[0]}-{seasons[-1]}; skipped[/]")
            continue
        v = r.verdict
        log(f"{t.key}: MAE model {r.pooled['model']['mae']:.4f} vs season-to-date "
            f"{r.pooled['b2']['mae']:.4f} (shrunk {r.pooled['b3']['mae']:.4f}); wins "
            f"{v['wins_vs_b2']}/{v['seasons']} (need {v['need']}); 80% range covers "
            f"{r.coverage:.1%} -> {'SHIPS' if v['ships'] else 'stays descriptive'}")  # fmt: skip
        results.append(r)
    if not results:
        raise ValueError(f"no selected target has data in {seasons[0]}-{seasons[-1]}")
    proe = proe_play_scores(paths, pl.concat([r.preds for r in results], how="diagonal_relaxed"))
    label = "smoke" if smoke else f"{seasons[0]}-{seasons[-1]}"
    folder = backtest_root(paths) / label
    created = datetime.now(UTC).replace(microsecond=0)
    version = f"{seasons[0]}-{seasons[-1]}_{created.strftime('%Y%m%dT%H%M%SZ')}"
    doc = {
        "schema_version": F.SCHEMA_VERSION,
        "label": label,
        "created_at": created.isoformat(),
        "seasons": seasons,
        "tune_seasons": tune,
        "settings": cfg,
        "targets": {r.target.key: result_doc(r) for r in results},
        "proe_per_play": proe,
        "curves": {s: c.to_dict() for s, c in curves.items()},
        "notes": inp.notes,
        "seconds": round(time.perf_counter() - t0, 1),
    }
    run = None
    url = None
    if use_wandb:
        from nflengine import tracking

        run = tracking.init_run(
            group=SMOKE_GROUP if smoke else GROUP,
            job_type="playcall-backtest",
            config={
                "seasons": seasons,
                "tune_seasons": tune,
                "targets": [t.key for t in tl],
                "settings": cfg,
                "schema_version": F.SCHEMA_VERSION,
                "dataset_version": tracking.dataset_version(paths),
                "git_commit": tracking.git_commit(),
                "curves": {
                    s: {"model": c.model, "ref_seasons": c.ref_seasons} for s, c in curves.items()
                },
            },  # fmt: skip
            tags=["pc02", f"seasons:{seasons[0]}-{seasons[-1]}", *(["smoke"] if smoke else [])],
            launched_by=launched_by,
            name=f"playcall-backtest-{label}",
        )
        url = run.url
    try:
        if run is not None:
            log_backtest(run, results, curves, proe)
        if save:
            from nflengine.ops.lock import write_parquet_atomic

            preds = pl.concat([r.preds for r in results], how="diagonal_relaxed")
            write_parquet_atomic(preds, folder / "predictions.parquet", compression="zstd")
            _atomic_json(folder / "summary.json", {**doc, "wandb_run": url})
            log(f"backtest -> {folder}")
        shipped = [r.target.key for r in results if r.verdict["ships"]]
        model_doc = None
        if promote and not smoke:
            if not shipped:
                raise RuntimeError("no target ships by the rule (D130): not promoted; ask Rishi")
            model_doc = train_and_save(paths, results, inp, curves, cfg, version, url, log)
            if run is not None:
                _log_artifact(run, paths, version, model_doc, promote=True)
            _write_pointer(paths, version, url, shipped)
            log(f"promoted: {FAMILY}:{version} is production ({', '.join(shipped)})")
    finally:
        if run is not None:
            run.finish()
    return {
        "results": results,
        "shipped": [r.target.key for r in results if r.verdict["ships"]],
        "folder": folder if save else None,
        "url": url,
        "version": version if promote and not smoke else None,
        "proe_per_play": proe,
    }


# --- the model folder ---------------------------------------------------------------------------
def train_and_save(
    paths: DataPaths,
    results: list[TargetResult],
    inp: Inputs,
    curves: dict[int, F.ScriptCurve],
    cfg: dict[str, Any],
    version: str,
    url: str | None,
    log: Callable[..., None] = print,
) -> dict[str, Any]:
    """The production parameters: every predictor refit on all of a target's reported seasons
    (the backtest fit them on the tuning window only), the range refit the same way; saved to
    `models/playcall/<version>/params.json` with the backtest's verdicts."""
    targets: dict[str, Any] = {}
    for r in results:
        t = r.target
        obs = F.observations(inp.tg, t)
        d = F.build_design(t, obs, inp.games, curves, inp.changed, inp.midseason, r.seasons,
                           settings=cfg)  # fmt: skip
        allm = np.ones(d.rows.height, dtype=bool)
        fits = {kind: F.fit_kind(d, kind, allm, cfg) for kind in F.KINDS}
        parts = F.predict_parts(d, "model", fits["model"]["params"], cfg)
        cohort = np.isfinite(d.y) & (d.n > 0) & np.isfinite(parts.forecast)
        rng = F.fit_range(d, parts, cohort)
        targets[t.key] = {
            "label": t.label,
            "short": t.short,
            "side": t.side,
            "context": t.context,
            "unit": t.unit,
            "per": t.per,
            "shipped": bool(r.verdict["ships"]),
            "fit_seasons": r.seasons,
            "kinds": {k: {"params": f["params"], "loss": f["loss"]} for k, f in fits.items()},
            "range": rng,
            "tuning_fit": {k: f["params"] for k, f in r.fits.items()},
            "backtest": {
                "mae": {k: r.pooled[k].get("mae") for k in F.KINDS},
                "wins_vs_b2": r.verdict["wins_vs_b2"],
                "seasons": r.verdict["seasons"],
                "need": r.verdict["need"],
                "coverage_80": r.coverage,
            },
        }
        log(f"  {t.key}: refit on {r.seasons[0]}-{r.seasons[-1]}: {fits['model']['params']}")
    doc = {
        "family": FAMILY,
        "version": version,
        "schema_version": F.SCHEMA_VERSION,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "settings": cfg,
        "targets": targets,
        "shipped": [k for k, v in targets.items() if v["shipped"]],
        "backtest_run": url,
    }
    folder = model_root(paths) / version
    _atomic_json(folder / "params.json", doc)
    card = Path(__file__).resolve().parents[3] / "documentation" / "model_cards"
    card = card / "playcall-forecast.md"
    if card.exists():
        (folder / "MODEL_CARD.md").write_text(card.read_text(encoding="utf-8"), encoding="utf-8")
    log(f"model -> {folder}")
    return doc


def _log_artifact(run, paths: DataPaths, version: str, doc: dict[str, Any], promote: bool):
    import wandb

    card = model_root(paths) / version / "MODEL_CARD.md"
    art = wandb.Artifact(
        FAMILY,
        type="model",
        description=card.read_text(encoding="utf-8") if card.exists() else version,
        metadata={
            "version": version,
            "shipped": doc["shipped"],
            "targets": {
                k: {"shipped": v["shipped"], "fit_seasons": v["fit_seasons"]}
                for k, v in doc["targets"].items()
            },
        },  # fmt: skip
    )
    art.add_dir(str(model_root(paths) / version))
    run.log_artifact(art, aliases=[version, *(["production"] if promote else [])])


def _write_pointer(paths: DataPaths, version: str, url: str | None, shipped: list[str]) -> None:
    _atomic_json(
        model_root(paths) / "production.json",
        {
            "version": version,
            "promoted_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "wandb_run": url,
            "shipped": shipped,
        },
    )


def load_model(paths: DataPaths, version: str | None = None) -> dict[str, Any]:
    """The production (or a named) version's `params.json`. A version is
    `<first>-<last>_<UTC stamp>`; anything else (a path, `..`) is refused before it names a
    folder (Sol review, D131)."""
    import re

    root = model_root(paths)
    if version is None:
        ptr = root / "production.json"
        if not ptr.exists():
            raise FileNotFoundError(
                "no promoted playcall-model yet: run `nfl playcalling backtest --promote`"
            )
        version = json.loads(ptr.read_text(encoding="utf-8")).get("version")
    if not isinstance(version, str) or not re.fullmatch(VERSION_RE, version):
        raise ValueError("not a playcall-model version (expected <first>-<last>_<UTC stamp>)")
    f = (root / version / "params.json").resolve()
    if not f.is_relative_to(root.resolve()):
        raise ValueError("not a playcall-model version (outside models/playcall)")
    if not f.exists():
        raise FileNotFoundError(f"playcall-model {version} has no params.json")
    return json.loads(f.read_text(encoding="utf-8"))


# --- the weekly forecast ------------------------------------------------------------------------
def read_build(paths: DataPaths, season: int) -> dict[str, Any]:
    f = B.season_dir(paths, season) / "build.json"
    if not f.exists():
        raise FileNotFoundError(
            f"no play-calling tables for {season}: run `nfl playcalling build --season {season}`"
        )
    return json.loads(f.read_text(encoding="utf-8"))


FORECAST_COLUMNS = [
    "season", "week", "game_id", "team", "opponent", "side", "target", "shipped", "forecast",
    "lo", "hi", "b1", "b2", "b3", "b4", "team_term", "opponent_term", "context", "context_team",
    "script_term", "prior",
    "strength", "league", "n_so_far", "games_so_far", "expected_plays", "kickoff_utc", "spread",
    "total", "line_source", "made_at", "after_kickoff", "version",
]  # fmt: skip


def forecast_frame(
    t: F.Target,
    tp: dict[str, Any],
    inp: Inputs,
    curves: dict[int, F.ScriptCurve],
    rows: pl.DataFrame,
    season: int,
    cfg: dict[str, Any],
) -> pl.DataFrame:
    """One target's forecast rows for `rows` (season, week, game_id, team, opponent)."""
    obs = F.observations(inp.tg, t)
    d = F.build_design(t, obs, inp.games, curves, inp.changed, inp.midseason, [season],
                       forecast_rows=rows, settings=cfg)  # fmt: skip
    kinds = tp["kinds"]
    parts = {k: F.predict_parts(d, k, kinds.get(k, {}).get("params", {}), cfg) for k in F.KINDS}
    pm = parts["model"]
    lo, hi = F.predict_range(d, pm, tp["range"])
    gT = d.a["f_gT"]
    return d.rows.select(
        "season",
        "week",
        "game_id",
        "team",
        "opponent",
        "kickoff_utc",
        "spread",
        "total",
        pl.lit(t.context).alias("context"),
        pl.col("ctx").alias("context_team"),
    ).with_columns(
        pl.lit(t.side).alias("side"),
        pl.lit(t.key).alias("target"),
        pl.lit(bool(tp.get("shipped"))).alias("shipped"),
        pl.Series("forecast", pm.forecast),
        pl.Series("lo", lo),
        pl.Series("hi", hi),
        *[pl.Series(k, parts[k].forecast) for k in ("b1", "b2", "b3", "b4")],
        pl.Series("team_term", pm.team),
        pl.Series("opponent_term", pm.opponent),
        pl.Series("script_term", pm.script),
        pl.Series("prior", pm.prior),
        pl.Series("strength", pm.strength),
        pl.Series("league", pm.league),
        pl.Series("n_so_far", d.a["N0"][gT]),
        pl.Series("games_so_far", d.a["G0"][gT].astype(np.int64)),
        pl.Series("expected_plays", F.expected_plays(d)),
    )


def run_forecast(
    season: int,
    week: int,
    *,
    use_wandb: bool = True,
    launched_by: str | None = None,
    smoke: bool = False,
    version: str | None = None,
    now: datetime | None = None,
    paths: DataPaths | None = None,
    log: Callable[..., None] = print,
) -> dict[str, Any]:
    """`nfl playcalling forecast`: every game of the week, every target, as of the week, from
    the production parameters -> `playcalling/<S>/week<NN>/forecast.parquet` (+ .json)."""
    paths = paths or ensure_data_root()
    model = load_model(paths, version)
    fit = sorted({int(s) for t in model["targets"].values() for s in t.get("fit_seasons", [])})
    if season in fit:  # in-sample: the parameters learned from this season's own games
        raise ValueError(
            f"playcall-model {model['version']} was fit on {fit[0]}-{fit[-1]}, {season} included:"
            f" a {season} forecast would be in-sample. For a past season read the backtest's "
            "walk-forward predictions (playcalling/backtests/<seasons>/predictions.parquet)"
        )
    cfg = F.forecast_settings(model.get("settings"))
    bj = read_build(paths, season)
    span = bj.get("as_of_weeks") or []
    if not span or week < 1 or week > int(span[-1]):
        last = span[-1] if span else "none"
        raise ValueError(
            f"{season} week {week} isn't in the play-calling tables (newest as-of week: {last});"
            f" run `nfl playcalling build --season {season}` after the earlier weeks are in"
        )
    inp = load_inputs(paths, [season], lines=True)
    gw = inp.games.filter((pl.col("season") == season) & (pl.col("week") == week))
    if gw.is_empty():
        raise ValueError(f"no games in {season} week {week}")
    rows = F.team_view(gw).select("season", "week", "game_id", "team", "opponent")
    live = live_season_of(paths, season)
    curves = load_curves(paths, [season - 1, season], live_season=live, settings=cfg, log=log)
    made_at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    frames = []
    for t in F.TARGETS:
        tp = model["targets"].get(t.key)
        if tp is None or season < t.first_season:
            continue
        frames.append(forecast_frame(t, tp, inp, curves, rows, season, cfg))
    if not frames:
        raise ValueError("the production model has no target for this season")
    fc = pl.concat(frames, how="diagonal_relaxed").join(
        gw.select("game_id", "line_source"), on="game_id", how="left"
    )
    fc = (
        fc.with_columns(
            pl.lit(made_at).cast(pl.Datetime("us", "UTC")).alias("made_at"),
            (pl.col("kickoff_utc") <= pl.lit(made_at).cast(pl.Datetime("us", "UTC")))
            .fill_null(False)
            .alias("after_kickoff"),
            pl.lit(model["version"]).alias("version"),
        )
        .select(FORECAST_COLUMNS)
        .sort("game_id", "side", "team", "target")
    )
    folder = week_dir(paths, season, week)
    from nflengine.ops.lock import write_parquet_atomic

    write_parquet_atomic(fc, folder / FORECAST_FILE)
    cut = B.as_of_cutoffs(inp.games, season, [week])["cutoff_utc"][0]
    late = sorted(set(fc.filter(pl.col("after_kickoff"))["game_id"].to_list()))
    meta = {
        "schema_version": F.SCHEMA_VERSION,
        "season": season,
        "week": week,
        "as_of_week": week,
        "cutoff_utc": cut.isoformat() if cut is not None else None,
        "made_at": made_at.isoformat(),
        "version": model["version"],
        "shipped": model.get("shipped", []),
        "games": gw.height,
        "rows": fc.height,
        "games_after_kickoff": late,
        "backfill": bool(late),
        "lines": {
            "with_line": int(gw["spread_line"].is_not_null().sum()),
            "sources": sorted({s for s in gw["line_source"].to_list() if s}),
        },
        "curve": curves[season].to_dict() if season in curves else None,
        "notes": inp.notes,
    }
    _atomic_json(folder / "forecast.json", meta)
    log(f"{season} week {week}: {gw.height} games, {fc.height} forecasts -> {folder}"
        + (f" ({len(late)} games had already kicked off: a backfill)" if late else ""))  # fmt: skip
    url = None
    if use_wandb:
        url = _log_forecast_run(fc, meta, launched_by, smoke, paths)
    return {"frame": fc, "meta": meta, "folder": folder, "url": url}


def _log_forecast_run(fc: pl.DataFrame, meta: dict, launched_by, smoke, paths) -> str | None:
    import wandb

    from nflengine import tracking

    s, w = meta["season"], meta["week"]
    run = tracking.init_run(
        group=SMOKE_GROUP if smoke else GROUP,
        job_type="playcall-forecast",
        config={
            "season": s,
            "week": w,
            "version": meta["version"],
            "made_at": meta["made_at"],
            "cutoff_utc": meta["cutoff_utc"],
            "backfill": meta["backfill"],
            "git_commit": tracking.git_commit(),
            "dataset_version": tracking.dataset_version(paths),
        },  # fmt: skip
        tags=[
            "pc02",
            f"season:{s}",
            f"week:{w:02d}",
            *(["backfill"] if meta["backfill"] else []),
            *(["smoke"] if smoke else []),
        ],  # fmt: skip
        launched_by=launched_by,
        name=f"playcall-forecast-{s}-w{w:02d}",
    )
    try:
        show = fc.filter(pl.col("shipped")).select(
            "game_id", "team", "opponent", "target", "forecast", "lo", "hi", "b2", "b3",
            "opponent_term", "script_term", "after_kickoff",
        )  # fmt: skip
        run.log({"forecast/rows": wandb.Table(dataframe=show.to_pandas())})
        run.summary.update({"games": meta["games"], "rows": meta["rows"],
                            "shipped_rows": show.height,
                            "games_after_kickoff": len(meta["games_after_kickoff"])})  # fmt: skip
        return run.url
    finally:
        run.finish()


# --- grading --------------------------------------------------------------------------------------
SCOREBOARD_COLUMNS = [
    "season", "week", "game_id", "team", "opponent", "side", "target", "shipped", "status",
    "forecast", "lo", "hi", "b1", "b2", "b3", "b4", "actual", "n", "in_range", "err_model",
    "err_b1", "err_b2", "err_b3", "err_b4", "after_kickoff", "made_at", "graded_at", "version",
]  # fmt: skip


def run_grade(
    season: int,
    week: int,
    *,
    use_wandb: bool = True,
    launched_by: str | None = None,
    smoke: bool = False,
    now: datetime | None = None,
    paths: DataPaths | None = None,
    log: Callable[..., None] = print,
) -> dict[str, Any]:
    """`nfl playcalling grade`: the week's forecast vs what the games showed (the play-calling
    tables' team-game rows) -> the season's `playcall_scoreboard.parquet` (the week's rows
    replaced). A game not played yet, or whose FTN charting hasn't landed, is left `waiting`
    and graded by a later run."""
    paths = paths or ensure_data_root()
    f = week_dir(paths, season, week) / FORECAST_FILE
    if not f.exists():
        raise FileNotFoundError(
            f"no forecast for {season} week {week}: run `nfl playcalling forecast --season "
            f"{season} --week {week}` first"
        )
    fc = pl.read_parquet(f)
    bj = read_build(paths, season)
    no_ftn = set((bj.get("coverage") or {}).get("games_without_ftn") or [])
    tg = F.load_team_games(paths, [season])
    acts = []
    for t in F.TARGETS:
        o = F.observations(tg.filter(pl.col("week") == week), t)
        acts.append(o.select("game_id", "team", pl.lit(t.key).alias("target"),
                             pl.col("k"), pl.col("n").alias("n_actual")))  # fmt: skip
    act = pl.concat(acts)
    games = F.load_games(paths).filter((pl.col("season") == season) & (pl.col("week") == week))
    done = set(games.filter(pl.col("result").is_not_null())["game_id"].to_list())
    built = set(tg.filter(pl.col("week") == week)["game_id"].unique().to_list())
    src = {t.key: t.source for t in F.TARGETS}
    g = fc.join(act, on=["game_id", "team", "target"], how="left").with_columns(
        pl.col("n_actual").fill_null(0).alias("n"),
        pl.col("target").replace_strict(src, default="pbp").alias("_src"),
    )
    status = (
        pl.when(pl.col("n") > 0).then(pl.lit("graded"))
        .when(~pl.col("game_id").is_in(list(done))).then(pl.lit("not_played"))
        .when(~pl.col("game_id").is_in(list(built))).then(pl.lit("waiting_build"))
        .when((pl.col("_src") == "ftn") & pl.col("game_id").is_in(list(no_ftn)))
        .then(pl.lit("waiting_ftn"))
        .otherwise(pl.lit("no_plays"))
    )  # fmt: skip
    graded_at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    g = g.with_columns(
        status.alias("status"),
        pl.when(pl.col("n") > 0).then(pl.col("k") / pl.col("n")).alias("actual"),
    ).with_columns(
        ((pl.col("actual") >= pl.col("lo")) & (pl.col("actual") <= pl.col("hi"))).alias("in_range"),
        (pl.col("forecast") - pl.col("actual")).abs().alias("err_model"),
        *[(pl.col(k) - pl.col("actual")).abs().alias(f"err_{k}") for k in ("b1", "b2", "b3", "b4")],
        pl.lit(graded_at).cast(pl.Datetime("us", "UTC")).alias("graded_at"),
    ).select(SCOREBOARD_COLUMNS)  # fmt: skip
    from nflengine.ops.lock import file_lock, write_parquet_atomic

    path = B.season_dir(paths, season) / SCOREBOARD_FILE
    with file_lock(path):
        old = pl.read_parquet(path) if path.exists() else None
        if old is not None:
            old = old.filter(~((pl.col("season") == season) & (pl.col("week") == week)))
            g = pl.concat([old, g], how="diagonal_relaxed")
        g = g.sort("season", "week", "game_id", "side", "team", "target")
        write_parquet_atomic(g, path)
    wk = g.filter((pl.col("season") == season) & (pl.col("week") == week))
    summary = week_summary(wk)
    counts = dict(wk.group_by("status").len().iter_rows())
    log(f"{season} week {week}: graded {counts.get('graded', 0)} of {wk.height} rows "
        f"({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))}) -> {path}")  # fmt: skip
    url = None
    if use_wandb:
        url = _log_grade_run(wk, summary, season, week, launched_by, smoke, paths)
    return {"frame": wk, "summary": summary, "counts": counts, "path": path, "url": url}


def week_summary(rows: pl.DataFrame) -> dict[str, Any]:
    """Per target (and over the shipped ones): graded rows, MAE of the forecast and of each
    baseline, how often the forecast beat the season-to-date rate, the 80% range's coverage."""
    gr = rows.filter(pl.col("status") == "graded")
    out: dict[str, Any] = {"graded": gr.height, "rows": rows.height, "targets": {}}

    means = [t.key for t in F.TARGETS if t.unit == "mean"]

    def summ(x: pl.DataFrame, overall: bool = False) -> dict[str, Any]:
        if x.is_empty():
            return {"graded": 0}
        # the overall MAE over the targets in points only (shares, PROE): rushers are in rushers
        pts = x.filter(~pl.col("target").is_in(means)) if overall else x
        return {
            "graded": x.height,
            **{
                f"mae_{k}": float(pts[f"err_{k}"].mean()) if pts.height else None
                for k in ("model", "b1", "b2", "b3", "b4")
            },
            "beat_b2": int((x["err_model"] < x["err_b2"]).sum()),
            "in_range": float(x["in_range"].mean()),
        }

    for (key,), x in gr.group_by(["target"], maintain_order=True):
        out["targets"][key] = summ(x)
    out["shipped"] = summ(gr.filter(pl.col("shipped")), overall=True)
    return out


def _log_grade_run(wk, summary, season, week, launched_by, smoke, paths) -> str | None:
    import wandb

    from nflengine import tracking

    run = tracking.init_run(
        group=SMOKE_GROUP if smoke else GROUP,
        job_type="playcall-grade",
        config={
            "season": season,
            "week": week,
            "git_commit": tracking.git_commit(),
            "dataset_version": tracking.dataset_version(paths),
        },  # fmt: skip
        tags=["pc02", f"season:{season}", f"week:{week:02d}", *(["smoke"] if smoke else [])],
        launched_by=launched_by,
        name=f"playcall-grade-{season}-w{week:02d}",
    )
    try:
        rows = [{"target": k, **v} for k, v in summary["targets"].items()]
        run.log(
            {
                "grade/targets_rows": _wb_table(rows),
                "grade/rows": wandb.Table(dataframe=wk.drop("made_at", "graded_at").to_pandas()),
            }
        )
        run.summary.update(
            {
                "graded": summary["graded"],
                "rows": summary["rows"],
                **{f"shipped/{k}": v for k, v in summary["shipped"].items()},
            }
        )
        return run.url
    finally:
        run.finish()
