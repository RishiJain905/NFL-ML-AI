"""Availability and role (FL00): data loading, files, backtests, the weekly predict and grade,
W&B and promotion. The model itself is `forecastlab/availability.py`; the contract (file
layout, schemas) is `forecastlab/schema.py`.

- `run_backtest`: the walk-forward over the tuning or the reported window, per view; saves
  `backtests/availability/<label>/predictions.parquet` + `summary.json`, the verdicts of the
  pre-registered rule, and (`promote`) the model folder and `production.json`.
- `run_predict`: one week, one view, refit on every earlier week, with the promoted settings
  and calibration; writes the week's `availability.parquet`, `inherit.parquet`,
  `availability.json`. Rows of a game that has kicked off are never re-made.
- `run_grade`: fills `played` / `actual_*` for the week's games with snap counts and upserts
  `<S>/availability_scoreboard.parquet`.

Curated files are read whole with `schema.read_parquet` (no memory map, no held handle), once
per call. Nothing here writes outside `forecastlab/` and `models/forecastlab/availability/`.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nflengine.forecastlab import availability as A
from nflengine.forecastlab import schema
from nflengine.paths import DataPaths, ensure_data_root

FAMILY = "availability"
ARTIFACT = "availability-model"
SMOKE_GROUP = "forecast-lab-smoke"
VERSION_RE = r"\d{4}-\d{4}_\d{8}T\d{6}Z"
SCHEMA_VERSION = 1

COLS = {
    "games": [
        "game_id",
        "season",
        "week",
        "game_type",
        "home_team",
        "away_team",
        "kickoff_utc",
        "completed",
    ],
    "snaps": [
        "game_id",
        "season",
        "week",
        "player",
        "position",
        "team",
        "offense_snaps",
        "offense_pct",
        "defense_snaps",
        "defense_pct",
        "gsis_id",
    ],
    "injuries": ["season", "week", "gsis_id", "team", "report_status", "practice_status"],
    "rosters_weekly": ["season", "week", "team", "gsis_id", "status", "position"],
    "player_games": ["player_id", "game_id", "target_share"],
}
PLAY_COLS = ["game_id", "posteam", "rusher_player_id", "rush", "two_point_attempt"]
RESULT_COLS = ("played", "actual_snap", "actual_tgt_share", "actual_carry_share")


# --- inputs ---------------------------------------------------------------------------------
def load_tables(paths: DataPaths, from_season: int) -> dict[str, pl.DataFrame]:
    """The curated tables FL00 reads, each read whole once (D100), from `from_season` on."""
    cur = paths.curated
    out: dict[str, pl.DataFrame] = {}
    for name, cols in COLS.items():
        df = schema.read_parquet(cur / f"{name}.parquet", columns=cols)
        if "season" in df.columns:
            df = df.filter(pl.col("season") >= from_season)
        out[name] = df
    parts = []
    for f in sorted((cur / "plays").glob("season=*.parquet")):
        try:
            s = int(f.stem.split("=", 1)[1])
        except ValueError:
            continue
        if s < from_season:
            continue
        parts.append(A.rush_counts(schema.read_parquet(f, columns=PLAY_COLS)))
    out["rushes"] = (
        pl.concat(parts)
        if parts
        else A.rush_counts(pl.DataFrame(schema={c: pl.String for c in PLAY_COLS}))
    )
    return out


def load_prepared(paths: DataPaths, cfg: dict[str, Any]) -> A.Prepared:
    t = load_tables(paths, int(cfg["train_from"]) - 1)
    return A.prepare(
        t["games"], t["snaps"], t["injuries"], t["rosters_weekly"], t["player_games"], t["rushes"]
    )


def reg_keys(prep: A.Prepared, first: int, last_t: int) -> pl.DataFrame:
    return (
        prep.tg.filter(
            (pl.col("game_type") == "REG") & (pl.col("season") >= first) & (pl.col("t") <= last_t)
        )
        .select("season", "week")
        .unique()
    )


# --- small file helpers ---------------------------------------------------------------------
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
    return x


def _atomic_json(path: Path, doc: dict[str, Any]) -> None:
    """Write next to `path`, then replace it in one step, retried while a reader holds the
    file (`fsutil.replace_file`, Windows); a failure raises and leaves no temp file behind."""
    from nflengine.fsutil import replace_file

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(_jsonable(doc), indent=1), encoding="utf-8")
        replace_file(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def _write_parquet(df: pl.DataFrame, path: Path) -> None:
    from nflengine.ops.lock import write_parquet_atomic

    path.parent.mkdir(parents=True, exist_ok=True)
    write_parquet_atomic(df, path)


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _stamp(dt_: datetime) -> str:
    return dt_.strftime("%Y%m%dT%H%M%SZ")


def _view_label(view: str, seasons: list[int], *, tune: bool, smoke: bool, canonical: bool) -> str:
    if smoke:
        return f"{view}-smoke"
    if tune:
        return f"{view}-tune"
    return view if canonical else f"{view}-{seasons[0]}-{seasons[-1]}"


# --- W&B ------------------------------------------------------------------------------------
class LiveLogger:
    """Per-week `bt/<view>/*` points (week and cumulative) while the walk-forward runs."""

    def __init__(self, run, view: str, scored: Sequence[int]):
        self.run, self.view, self.scored = run, view, set(scored)
        self.step = 0
        self.seen: list[pl.DataFrame] = []

    def __call__(self, block: pl.DataFrame) -> None:
        if self.run is None:
            return
        b = block.filter(pl.col("season").is_in(list(self.scored)))
        for (s, w), wk in sorted(b.group_by("season", "week"), key=lambda kv: kv[0]):
            self.seen.append(wk)
            cum = pl.concat(self.seen, how="diagonal_relaxed")
            self.step += 1
            d: dict[str, Any] = {"bt/step": self.step}
            for tag, frame in (("week", wk), ("cum", cum)):
                for o in schema.AVAIL_OUTPUTS:
                    sc = A.score_output(frame, o)
                    if sc["n"] == 0:
                        continue
                    k = "brier" if o == "play" else "mae"
                    d[f"bt/{self.view}/{o}_{k}_model_{tag}"] = sc[f"{k}_model"]
                    d[f"bt/{self.view}/{o}_{k}_baseline_{tag}"] = sc[f"{k}_baseline"]
                    if o == "snap":
                        d[f"bt/{self.view}/snap_coverage_80_{tag}"] = sc["coverage_80"]
            d[f"bt/{self.view}/season"] = int(s)
            d[f"bt/{self.view}/week"] = int(w)
            self.run.log(d)


def _wb_table(rows: list[dict[str, Any]]):
    import wandb

    if not rows:
        return wandb.Table(columns=["empty"], data=[])
    cols = list(rows[0].keys())
    return wandb.Table(columns=cols, data=[[_jsonable(r.get(c)) for c in cols] for r in rows])


def _importance(m: A.ViewModels) -> list[dict[str, Any]]:
    out = []
    for name, booster, kind in (
        ("play", m.play, "play"),
        ("snap_p50", m.snap[0.5], "snap"),
        ("tgt_share", m.tgt, "share"),
        ("carry_share", m.car, "share"),
    ):
        feats = A.feature_list(m.view, kind)
        gain = booster.feature_importance(importance_type="gain")
        tot = float(gain.sum()) or 1.0
        for f, g in zip(feats, gain, strict=True):
            out.append({"model": name, "feature": f, "gain_share": float(g) / tot})
    return out


def _log_view(run, view: str, res: dict[str, Any], preds: pl.DataFrame, last: A.ViewModels | None):
    import wandb

    pooled, by_season, ver = res["pooled"], res["by_season"], res["verdict"]
    summ: dict[str, Any] = {}
    for o, d in pooled.items():
        for k, v in d.items():
            summ[f"{view}/{o}/{k}"] = v
        summ[f"{view}/{o}/ships"] = ver[o]["ships"]
        summ[f"{view}/{o}/wins"] = ver[o]["wins"]
    run.summary.update(_jsonable(summ))
    rows = []
    for s in by_season:
        for o in schema.AVAIL_OUTPUTS:
            d = s[o]
            k = "brier" if o == "play" else "mae"
            rows.append(
                {
                    "season": s["season"],
                    "output": o,
                    "n": d["n"],
                    "model": d.get(f"{k}_model"),
                    "baseline": d.get(f"{k}_baseline"),
                    "coverage_80": d.get("coverage_80"),
                    "ece_q10": d.get("ece_q10"),
                }
            )
    vrows = [{"output": o, **{k: v for k, v in d.items()}} for o, d in ver.items()]
    c = A.cohort(preds, "play")
    rel = A.reliability(c["p_play"].to_numpy(), c["played"].cast(pl.Float64).to_numpy())
    relb = A.reliability(c["base_p_play"].to_numpy(), c["played"].cast(pl.Float64).to_numpy())
    data = {
        f"{view}/by_season_rows": _wb_table(rows),
        f"{view}/verdict_rows": _wb_table(vrows),
        f"{view}/reliability_bins": _wb_table(
            [{"who": "model", **r} for r in rel] + [{"who": "baseline", **r} for r in relb]
        ),
    }
    data[f"{view}/reliability"] = wandb.plot.line_series(
        xs=[r["p_mean"] for r in rel],
        ys=[[r["y_mean"] for r in rel], [r["p_mean"] for r in rel]],
        keys=["played (model deciles)", "perfect"],
        title=f"{view}: chance he plays, by decile",
        xname="predicted",
    )
    if last is not None:
        data[f"{view}/importance_rows"] = _wb_table(_importance(last))
    sample = preds.filter(pl.col("at_risk")).sort("season", "week", "game_id", "player_id")
    if sample.height > 2000:
        sample = sample.sample(2000, seed=7).sort("season", "week", "game_id", "player_id")
    keep = [
        "season",
        "week",
        "game_id",
        "team",
        "player",
        "pgroup",
        "report_status",
        "practice_status",
        "prev_status",
        "roster_status",
        "p_play",
        "base_p_play",
        "played",
        "snap_p10",
        "snap_p50",
        "snap_p90",
        "base_snap",
        "actual_snap",
    ]
    data[f"{view}/at_risk_sample"] = _wb_table(sample.select(keep).to_dicts())
    run.log(data)


# --- the backtest ---------------------------------------------------------------------------
def view_result(preds: pl.DataFrame) -> dict[str, Any]:
    sc = A.score(preds)
    return {"pooled": sc["pooled"], "by_season": sc["by_season"], "verdict": A.verdicts(sc)}


def run_backtest(
    *,
    seasons: list[int] | None = None,
    views: tuple[str, ...] = schema.VIEWS,
    tune: bool = False,
    use_wandb: bool = True,
    launched_by: str | None = None,
    smoke: bool = False,
    promote: bool = False,
    log: Callable[..., None] = print,
    paths: DataPaths | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The walk-forward backtest (FL00). Default: the reported window (settings
    `report_seasons`); `tune`: the tuning window. `smoke`: one season, W&B group
    `forecast-lab-smoke`, nothing promoted. `promote` (reported run only): the model folder,
    `production.json` and the `availability-model` artifact with alias `production`, with the
    outputs that passed the rule per view. `overrides`: research settings (their own label)."""
    paths = paths or ensure_data_root()
    cfg = A.settings(overrides)
    window = A.parse_seasons(cfg["tune_seasons"] if tune else cfg["report_seasons"])
    if smoke and seasons is None:
        window = window[-1:]
    scored = sorted(set(seasons)) if seasons else window
    canonical = (
        not tune
        and not smoke
        and not overrides
        and scored == A.parse_seasons(cfg["report_seasons"])
    )
    if promote and (tune or smoke or not canonical):
        raise ValueError("promote only from the canonical reported run (no tune, smoke, overrides)")
    walk_from = int(cfg["walk_from"]) if not smoke else max(int(cfg["walk_from"]), scored[0] - 2)
    walk = list(range(min(walk_from, scored[0]), scored[-1] + 1))
    t0 = time.perf_counter()
    created = _now()
    version = f"{scored[0]}-{scored[-1]}_{_stamp(created)}"
    log(
        f"availability backtest: views {', '.join(views)}; walk-forward {walk[0]}-{walk[-1]}, "
        f"scored {scored[0]}-{scored[-1]}; refit per {cfg['refit']}"
    )
    prep = load_prepared(paths, cfg)
    last_t = int(A.tkey(scored[-1], 22))
    rows = A.build_rows(prep, reg_keys(prep, int(cfg["train_from"]), last_t), game_types=["REG"])
    log(f"  universe: {rows.height:,} rows ({time.perf_counter() - t0:.0f} s)")
    run = None
    url = None
    if use_wandb:
        from nflengine import tracking

        tag = "smoke" if smoke else ("tune" if tune else "report")
        run = tracking.init_run(
            group=SMOKE_GROUP if smoke else schema.WANDB_GROUP,
            job_type="availability-backtest",
            config={
                "family": FAMILY,
                "seasons_scored": scored,
                "walk_forward": [walk[0], walk[-1]],
                "views": list(views),
                "settings": cfg,
                "features": {
                    v: {k: A.feature_list(v, k) for k in ("play", "snap", "share")} for v in views
                },
                "schema_version": SCHEMA_VERSION,
                "dataset_version": tracking.dataset_version(paths),
                "git_commit": tracking.git_commit(),
            },
            tags=["fl00", FAMILY, f"seasons:{scored[0]}-{scored[-1]}", tag],
            launched_by=launched_by,
            name=f"availability-backtest-{tag}-{scored[0]}-{scored[-1]}",
        )
        run.define_metric("bt/step")
        run.define_metric("bt/*", step_metric="bt/step")
        url = run.url
    out: dict[str, Any] = {"views": {}, "version": None, "url": url}
    all_preds: dict[str, pl.DataFrame] = {}
    try:
        for view in views:
            frame = A.add_features(
                prep, rows, view, week_roster_on=bool(cfg.get("saturday_week_roster", True))
            )
            lastm: list[A.ViewModels] = []

            def keep_last(m: A.ViewModels, box: list[A.ViewModels] = lastm) -> None:
                box[:] = [m]

            logger = LiveLogger(run, view, scored)
            preds = A.walk_forward(
                frame, view, cfg, walk, on_block=logger, on_model=keep_last, log=log
            )
            all_preds[view] = preds
            sp = preds.filter(pl.col("season").is_in(scored))
            res = view_result(sp)
            out["views"][view] = res
            for o, v in res["verdict"].items():
                pooled = res["pooled"][o]
                k = "brier" if o == "play" else "mae"
                log(
                    f"  {view} {o}: model {pooled[f'{k}_model']:.5f} vs baseline "
                    f"{pooled[f'{k}_baseline']:.5f}, wins {v['wins']}/{v['seasons']} (need "
                    f"{v['need']}) -> {'SHIPS' if v['ships'] else 'does not ship'}"
                )
            if run is not None:
                _log_view(run, view, res, sp, lastm[0] if lastm else None)
            label = _view_label(view, scored, tune=tune, smoke=smoke, canonical=canonical)
            folder = schema.backtest_dir(paths, FAMILY, label)
            _write_parquet(
                A.to_schema(sp, view=view, version=f"backtest-{label}"),
                folder / "predictions.parquet",
            )
            _atomic_json(
                folder / "summary.json",
                {
                    "family": FAMILY,
                    "label": label,
                    "view": view,
                    "schema_version": SCHEMA_VERSION,
                    "created_at": created,
                    "seasons_scored": scored,
                    "walk_forward": walk,
                    "tune": tune,
                    "smoke": smoke,
                    "settings": cfg,
                    "wandb_run": url,
                    "features": {k: A.feature_list(view, k) for k in ("play", "snap", "share")},
                    **res,
                },
            )
            log(f"  {view}: backtest -> {folder}")
        if promote:
            shipped = {
                v: [o for o, d in out["views"][v]["verdict"].items() if d["ships"]] for v in views
            }
            doc = promote_model(paths, cfg, prep, rows, all_preds, shipped, version, url, log)
            if run is not None:
                _log_artifact(run, paths, version, doc)
            out["version"] = version
        if run is not None:
            run.summary["seconds"] = round(time.perf_counter() - t0, 1)
    finally:
        if run is not None:
            run.finish()
    log(f"availability backtest done in {time.perf_counter() - t0:.0f} s")
    return out


def promote_model(
    paths: DataPaths,
    cfg: dict[str, Any],
    prep: A.Prepared,
    rows: pl.DataFrame,
    preds: dict[str, pl.DataFrame],
    shipped: dict[str, list[str]],
    version: str,
    url: str | None,
    log: Callable[..., None] = print,
) -> dict[str, Any]:
    """The model folder: the settings, the feature lists, each view's calibration for the next
    season (Platt layer and range shifts fitted on the last `calib_seasons` of the reported
    walk-forward), the boosters refit on every labelled row (for inspection: the weekly predict
    refits on every earlier week with these settings), the verdicts; then `production.json`."""
    folder = schema.model_dir(paths, FAMILY) / version
    folder.mkdir(parents=True, exist_ok=True)
    views: dict[str, Any] = {}
    for view, pr in preds.items():
        nxt = int(pr["season"].max()) + 1
        platt, shifts = A.calibrators(pr, nxt, int(A.tkey(nxt, 0)), cfg)
        frame = A.add_features(
            prep, rows, view, week_roster_on=bool(cfg.get("saturday_week_roster", True))
        )
        train = frame.filter(
            pl.col("played").is_not_null() & (pl.col("season") >= int(cfg["train_from"]))
        )
        m = A.fit_view(train, view, cfg, platt=platt, shifts=shifts)
        for name, b in (
            ("play", m.play),
            ("snap_p10", m.snap[0.1]),
            ("snap_p50", m.snap[0.5]),
            ("snap_p90_reflected", m.snap[0.9]),
            ("tgt_share", m.tgt),
            ("carry_share", m.car),
        ):
            (folder / f"{view}_{name}.txt").write_text(b.model_to_string(), encoding="utf-8")
        views[view] = {
            "platt": list(platt),
            "shifts": shifts,
            "calibrated_for": nxt,
            "trained_through": m.trained_through,
            "shipped": shipped.get(view, []),
            "features": {k: A.feature_list(view, k) for k in ("play", "snap", "share")},
        }
        log(
            f"  {view}: Platt {platt[0]:.3f} / {platt[1]:.3f}, range shifts {shifts['lo']:+.3f} / "
            f"{shifts['hi']:+.3f}; shipped: {', '.join(shipped.get(view, [])) or 'nothing'}"
        )
    doc = {
        "family": FAMILY,
        "version": version,
        "schema_version": SCHEMA_VERSION,
        "created_at": _now(),
        "settings": cfg,
        "views": views,
        "shipped": shipped,
        "backtest_run": url,
    }
    _atomic_json(folder / "model.json", doc)
    cards = Path(__file__).resolve().parents[3] / "documentation" / "model_cards"
    card = cards / "availability-and-role.md"
    if card.exists():
        (folder / "MODEL_CARD.md").write_text(card.read_text(encoding="utf-8"), encoding="utf-8")
    _atomic_json(
        schema.production_file(paths, FAMILY),
        {
            "version": version,
            "promoted_at": _now(),
            "wandb_run": url,
            "shipped": shipped,
        },
    )
    log(f"promoted: {ARTIFACT}:{version} is production -> {folder}")
    return doc


def _log_artifact(run, paths: DataPaths, version: str, doc: dict[str, Any]) -> None:
    import wandb

    folder = schema.model_dir(paths, FAMILY) / version
    card = folder / "MODEL_CARD.md"
    art = wandb.Artifact(
        ARTIFACT,
        type="model",
        description=card.read_text(encoding="utf-8") if card.exists() else version,
        metadata=_jsonable({"version": version, "shipped": doc["shipped"]}),
    )
    art.add_dir(str(folder))
    run.log_artifact(art, aliases=[version, "production"])


def load_production(paths: DataPaths, version: str | None = None) -> dict[str, Any]:
    """The production (or a named) version's `model.json`."""
    root = schema.model_dir(paths, FAMILY)
    if version is None:
        ptr = schema.production_file(paths, FAMILY)
        if not ptr.exists():
            raise FileNotFoundError(
                "no promoted availability model yet: run `nfl forecastlab backtest --promote`"
            )
        version = json.loads(ptr.read_text(encoding="utf-8")).get("version")
    if not isinstance(version, str) or not re.fullmatch(VERSION_RE, version):
        raise ValueError("not an availability model version (expected <first>-<last>_<UTC stamp>)")
    f = (root / version / "model.json").resolve()
    if not f.is_relative_to(root.resolve()) or not f.exists():
        raise FileNotFoundError(f"availability model {version} has no model.json")
    return json.loads(f.read_text(encoding="utf-8"))


# --- the weekly predict ---------------------------------------------------------------------
def _read_existing(path: Path, sch: dict[str, pl.DataType]) -> pl.DataFrame:
    if not path.exists():
        return pl.DataFrame(schema=sch)
    return schema.conform(schema.read_parquet(path), sch)


def predict_week(
    prep: A.Prepared, model: dict[str, Any], season: int, week: int, view: str
) -> tuple[pl.DataFrame, pl.DataFrame, A.ViewModels]:
    """Refit `view` on every labelled row before (season, week) with the production settings
    and calibration, and forecast the week's rows. -> (predicted rows, inherit, models)."""
    cfg = A._merge(A.DEFAULTS, model["settings"])
    vm = model["views"][view]
    t_key = int(A.tkey(season, week))
    keys = reg_keys(prep, int(cfg["train_from"]), t_key - 1)
    keys = pl.concat(
        [
            keys,
            pl.DataFrame(
                {"season": [season], "week": [week]}, schema={"season": pl.Int32, "week": pl.Int32}
            ),
        ]
    ).unique()
    rows = A.build_rows(prep, keys)
    # training: earlier regular-season weeks; the week itself: any game type
    rows = rows.filter(
        (
            (pl.col("t") < t_key)
            & pl.col("game_id").is_in(
                prep.tg.filter(pl.col("game_type") == "REG")["game_id"].to_list()
            )
        )
        | (pl.col("t") == t_key)
    )
    frame = A.add_features(
        prep, rows, view, week_roster_on=bool(cfg.get("saturday_week_roster", True))
    )
    train = frame.filter(
        (pl.col("t") < t_key)
        & pl.col("played").is_not_null()
        & (pl.col("season") >= int(cfg["train_from"]))
    )
    test = frame.filter(pl.col("t") == t_key)
    if test.height == 0:
        raise ValueError(f"no rows to forecast for {season} week {week}")
    m = A.fit_view(
        train,
        view,
        cfg,
        platt=tuple(vm["platt"]),
        shifts=vm["shifts"],
        rates_train=train.filter(pl.col("season") < season),
    )
    pred = A.predict_view(m, test, at_risk_below=float(cfg["at_risk_below"]))
    inh = A.inherit_table(m, pred, top=int(cfg.get("inherit_top", 5)))
    return pred, inh, m


def run_predict(
    season: int,
    week: int,
    *,
    view: str,
    use_wandb: bool = True,
    launched_by: str | None = None,
    log: Callable[..., None] = print,
    paths: DataPaths | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One week's availability forecast for one view (FL00): refit on every earlier week with the
    production settings, forecast, write `forecastlab/<S>/week<NN>/availability.parquet` (the
    other view's rows kept), `inherit.parquet` and `availability.json`.

    Never re-makes a row whose game has kicked off at `now` (default: the current time): if the
    week's file already has rows for that game (any view) they stay exactly as they are and
    this view makes none; if it has none, they are made and the game is listed in
    `games_after_kickoff`."""
    if view not in schema.VIEWS:
        raise ValueError(f"unknown view {view!r}")
    paths = paths or ensure_data_root()
    now = (now or _now()).astimezone(UTC)
    t0 = time.perf_counter()
    model = load_production(paths)
    cfg = A._merge(A.DEFAULTS, model["settings"])
    prep = load_prepared(paths, cfg)
    pred, inh, m = predict_week(prep, model, season, week, view)
    folder = schema.week_dir(paths, season, week)
    f_av, f_in, f_meta = (
        folder / "availability.parquet",
        folder / "inherit.parquet",
        folder / "availability.json",
    )
    version = model["version"]
    from nflengine.ops.lock import file_lock

    folder.mkdir(parents=True, exist_ok=True)
    with file_lock(f_av):
        old = _read_existing(f_av, schema.AVAIL_SCHEMA)
        old_in = _read_existing(f_in, schema.INHERIT_SCHEMA)
        kicked = set(pred.filter(pl.col("kickoff_utc") <= now)["game_id"].unique().to_list())
        have = set(old["game_id"].unique().to_list())
        keep_games = sorted(kicked & have)  # kicked off and already made: untouched
        late = sorted(kicked - have)  # kicked off, never made: made now, flagged
        # a forecast never carries results, even for a game already played (results come only
        # from `run_grade`)
        new = (
            A.to_schema(pred, view=view, version=version, created_at=now)
            .with_columns(
                pl.lit(m.trained_through).alias("trained_through"),
                *[pl.lit(None, dtype=schema.AVAIL_SCHEMA[c]).alias(c) for c in RESULT_COLS],
            )
            .filter(~pl.col("game_id").is_in(keep_games))
        )
        new_in = schema.conform(
            inh.with_columns(pl.lit(view).alias("view")), schema.INHERIT_SCHEMA
        ).filter(~pl.col("game_id").is_in(keep_games))
        made = set(new["game_id"].unique().to_list())
        # keep: other views' rows; this view's rows of games not re-made now
        keep_old = old.filter((pl.col("view") != view) | ~pl.col("game_id").is_in(list(made)))
        keep_old_in = old_in.filter((pl.col("view") != view) | ~pl.col("game_id").is_in(list(made)))
        av = pl.concat([keep_old, new]).sort("game_id", "team", "view", "pgroup", "player_id")
        ih = pl.concat([keep_old_in, new_in]).sort(
            "game_id", "team", "view", "kind", "out_player_id", "rank"
        )
        _write_parquet(av, f_av)
        _write_parquet(ih, f_in)
        meta = json.loads(f_meta.read_text(encoding="utf-8")) if f_meta.exists() else {}
        views_meta = dict(meta.get("views", {}))
        views_meta[view] = {
            "made_at": now,
            "rows": new.height,
            "games": len(made),
            "trained_through": m.trained_through,
            "version": version,
            # this view's games made after kickoff (marked in the tab) / kept as they were
            "games_after_kickoff": late,
            "games_kept_after_kickoff": keep_games,
        }
        # every game whose rows (any view) were made after its kickoff
        gak = sorted({g for d in views_meta.values() for g in d.get("games_after_kickoff", [])})
        shipped = model.get("shipped", {})
        meta = {
            "family": FAMILY,
            "schema_version": SCHEMA_VERSION,
            "season": season,
            "week": week,
            "views": views_meta,
            "version": version,
            "trained_through": m.trained_through,
            "shipped": shipped,
            "games_after_kickoff": gak,
            "rows": av.height,
        }
        # the parquet files are already replaced: a meta that can't be written must fail loudly
        # (a stale availability.json would describe rows that aren't in the file)
        try:
            _atomic_json(f_meta, meta)
        except OSError as e:
            raise RuntimeError(
                f"availability {view} {season} week {week}: the forecast files were written but "
                f"availability.json could not be ({type(e).__name__}); it is stale: run the "
                "predict again"
            ) from e
    secs = round(time.perf_counter() - t0, 1)
    log(
        f"availability {view} {season} week {week}: {new.height} rows for {len(made)} games, "
        f"{ih.filter(pl.col('view') == view).height} inherit rows; kept {len(keep_games)} games "
        f"after kickoff, made {len(late)} after kickoff ({secs} s) -> {folder}"
    )
    meta["seconds"] = secs
    url = None
    if use_wandb:
        url = _log_predict(new, meta, view, launched_by, paths)
    return {"meta": meta, "frame": new, "inherit": new_in, "url": url}


def _log_predict(frame: pl.DataFrame, meta: dict, view: str, launched_by, paths) -> str | None:
    from nflengine import tracking

    run = tracking.init_run(
        group=schema.WANDB_GROUP,
        job_type="availability-predict",
        config={
            "family": FAMILY,
            "season": meta["season"],
            "week": meta["week"],
            "view": view,
            "version": meta["version"],
            "trained_through": meta["trained_through"],
            "git_commit": tracking.git_commit(),
        },
        tags=["fl00", FAMILY, f"season:{meta['season']}", view],
        launched_by=launched_by,
        name=f"availability-predict-{meta['season']}-w{meta['week']:02d}-{view}",
    )
    try:
        risk = frame.filter(pl.col("at_risk")).sort("p_play")
        run.summary.update(
            {
                "rows": frame.height,
                "at_risk": risk.height,
                "games": meta["views"][view]["games"],
                "mean_p_play": float(frame["p_play"].mean() or 0.0),
            }
        )
        cols = [
            "game_id",
            "team",
            "player",
            "pgroup",
            "report_status",
            "practice_status",
            "prev_status",
            "p_play",
            "base_p_play",
            "snap_p50",
            "base_snap",
        ]
        run.log({"at_risk_rows": _wb_table(risk.select(cols).head(500).to_dicts())})
        return run.url
    finally:
        run.finish()


# --- grading --------------------------------------------------------------------------------
def late_games(meta_path: Path) -> dict[str, list[str]]:
    """{view: games that view made after their kickoff}, from the week's availability.json
    (`views.<view>.games_after_kickoff`; an older file with only the flat list applies it to
    every view)."""
    if not meta_path.exists():
        return {}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    views = meta.get("views") or {}
    out = {v: list(d.get("games_after_kickoff") or []) for v, d in views.items()}
    flat = meta.get("games_after_kickoff")
    if isinstance(flat, list) and flat and not any(out.values()):
        out = {v: list(flat) for v in (views or dict.fromkeys(schema.VIEWS))}
    return {v: g for v, g in out.items() if g}


def run_grade(
    season: int,
    week: int,
    *,
    use_wandb: bool = True,
    launched_by: str | None = None,
    log: Callable[..., None] = print,
    paths: DataPaths | None = None,
) -> dict[str, Any]:
    """Fill `played` / `actual_*` in the week's availability file for its games that have snap
    counts, and replace the week's rows of `<S>/availability_scoreboard.parquet`.

    - Rows of a game without snap counts yet stay null (`summary["waiting"]`); a later grade of
      the same week fills them and replaces the week's scoreboard rows.
    - The scoreboard leaves out every game a view made after its kickoff
      (`availability.json` -> `views.<view>.games_after_kickoff`): such a row isn't a forecast.
      Their results are still filled in the week's file (`summary["excluded_after_kickoff"]`).
    - A re-run on the same data writes the same files (a scoreboard row keeps its `graded_at`
      while its numbers don't change)."""
    paths = paths or ensure_data_root()
    folder = schema.week_dir(paths, season, week)
    f_av = folder / "availability.parquet"
    if not f_av.exists():
        raise FileNotFoundError(f"no availability forecast for {season} week {week}")
    t = load_tables(paths, season)
    app = A.appearances(
        t["snaps"].filter(pl.col("season") == season),
        t["player_games"],
        t["rushes"],
    )
    snapped = set(app["game_id"].unique().to_list())
    prep = A.Prepared(
        tg=pl.DataFrame(),
        app=app,
        stats=pl.DataFrame(),
        inj=pl.DataFrame(),
        ros=pl.DataFrame(),
        snapped_games=snapped,
    )
    from nflengine.ops.lock import file_lock

    with file_lock(f_av):
        cur = schema.conform(schema.read_parquet(f_av), schema.AVAIL_SCHEMA)
        lab = A.add_labels(prep, cur.with_columns(A.side_of(pl.col("pgroup")).alias("side")))
        graded = schema.conform(lab, schema.AVAIL_SCHEMA).sort(
            "game_id", "team", "view", "pgroup", "player_id"
        )
        _write_parquet(graded, f_av)
    now = _now()
    late = late_games(folder / "availability.json")
    is_late = pl.lit(False)
    for v, games in late.items():
        is_late = is_late | ((pl.col("view") == v) & pl.col("game_id").is_in(games))
    scored = graded.filter(pl.col("played").is_not_null())
    excluded = scored.filter(is_late).height
    sb = A.scoreboard_rows(scored.filter(~is_late), "live", now)
    path = schema.season_dir(paths, season) / "availability_scoreboard.parquet"
    with file_lock(path):
        old = _read_existing(path, schema.AVAIL_SCORE_SCHEMA)
        key = ["season", "week", "view", "output", "group"]
        num = ["n", "brier_model", "brier_baseline", "mae_model", "mae_baseline", "coverage_80"]
        prev = old.filter((pl.col("season") == season) & (pl.col("week") == week))
        if sb.height and prev.height:
            j = sb.join(
                prev.select(*key, *num, pl.col("graded_at").alias("_g")),
                on=key,
                how="left",
                suffix="_old",
            )
            same = pl.lit(True)
            for c in num:
                a, b = pl.col(c), pl.col(f"{c}_old")
                same = same & (
                    (a.is_null() & b.is_null()) | ((a - b).abs() < 1e-12).fill_null(False)
                )
            sb = j.with_columns(
                pl.when(same & pl.col("_g").is_not_null())
                .then(pl.col("_g"))
                .otherwise(pl.col("graded_at"))
                .alias("graded_at")
            ).select(sb.columns)
        rest = old.filter(~((pl.col("season") == season) & (pl.col("week") == week)))
        board = pl.concat([rest, schema.conform(sb, schema.AVAIL_SCORE_SCHEMA)]).sort(*key)
        _write_parquet(board, path)
    n_games = graded.filter(pl.col("played").is_not_null())["game_id"].n_unique()
    summary: dict[str, Any] = {
        "season": season,
        "week": week,
        "games_graded": n_games,
        "games": graded["game_id"].n_unique(),
        "rows": graded.height,
        # rows whose game has no snap counts yet: a later grade of the week fills them
        "waiting": graded.filter(pl.col("played").is_null()).height,
        # graded rows left out of the scoreboard: made after their game kicked off
        "excluded_after_kickoff": excluded,
    }
    for r in sb.filter(pl.col("group") == "ALL").iter_rows(named=True):
        k = "brier" if r["output"] == "play" else "mae"
        summary[f"{r['view']}/{r['output']}/{k}_model"] = r[f"{k}_model"]
        summary[f"{r['view']}/{r['output']}/{k}_baseline"] = r[f"{k}_baseline"]
        summary[f"{r['view']}/{r['output']}/n"] = r["n"]
    log(
        f"availability grade {season} week {week}: {n_games} of {summary['games']} games graded; "
        f"{summary['waiting']} rows waiting for snap counts; {excluded} rows made after kickoff "
        "left out of the scoreboard"
    )
    url = None
    if use_wandb and n_games:
        from nflengine import tracking

        run = tracking.init_run(
            group=schema.WANDB_GROUP,
            job_type="availability-grade",
            config={
                "family": FAMILY,
                "season": season,
                "week": week,
                "git_commit": tracking.git_commit(),
            },
            tags=["fl00", FAMILY, f"season:{season}"],
            launched_by=launched_by,
            name=f"availability-grade-{season}-w{week:02d}",
        )
        try:
            run.summary.update(_jsonable(summary))
            run.log({"scoreboard_rows": _wb_table(sb.drop("graded_at").to_dicts())})
            url = run.url
        finally:
            run.finish()
    return {"summary": summary, "frame": graded, "url": url}
