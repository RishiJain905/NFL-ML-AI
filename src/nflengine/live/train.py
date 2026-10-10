"""Train, save, log and promote the live-decision models (LD00): `nfl live train`.

The fit goes to `{NFL_DATA_ROOT}/models/live-decisions/<version>/` (every model, `meta.json`,
`checks.json`) and to W&B as the artifact `live-decision-models` (group `live-decisions`, job
type `live-train`). **Promotion** moves this artifact's own `production` alias and writes
`models/live-decisions/production.json`, the pointer the live code loads from disk (the app
reads files on D:, not the alias). No other model's alias or file moves (D107).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nflengine.live import data as D
from nflengine.live import models as M
from nflengine.live import schema
from nflengine.live.decide import Engine, decide_settings
from nflengine.live.schema import GameState
from nflengine.paths import DataPaths, ensure_data_root

FAMILY = "live-decision-models"
GROUP = "live-decisions"
CARD = Path(__file__).resolve().parents[3] / "documentation" / "model_cards" / f"{FAMILY}.md"

# The hand-made states of the phase file (LD00 -> Tests) with the call each must get.
CHECKS: list[tuple[str, GameState, str]] = [
    ("4th & 1 at the opponent's 40, tied, Q2", GameState(2025, 0, 2400, 600, 4, 1, 40), "go"),
    ("4th & 15 at the own 20, tied, Q2", GameState(2025, 0, 2400, 600, 4, 15, 80), "punt"),
    ("40-yard field goal, down 2, 5 s left", GameState(2025, -2, 5, 5, 4, 5, 22), "fg"),
]
TIMING_STATES = [
    ("3rd & 6 at the opponent's 45, down 3, 10:00 Q4", GameState(2025, -3, 600, 600, 3, 6, 45)),
    ("3rd & 10 at the own 25, tied, 10:00 Q3", GameState(2025, 0, 1500, 1500, 3, 10, 75)),
]
TIMING_BUDGET_MS = 20.0


def root(paths: DataPaths | None = None) -> Path:
    return (paths or ensure_data_root()).models / "live-decisions"


def load_frames(seasons: list[int], gain_ratings: bool = False, paths=None) -> dict:
    plays = D.with_state(D.load_plays(seasons, paths=paths))
    wp, _ = D.wp_frame(plays)
    gain = D.gain_frame(plays)
    if gain_ratings:
        gain = D.with_ratings(gain, paths=paths)
    return {
        "wp": wp,
        "gain": gain,
        "fg": D.fg_frame(plays),
        "punt": D.punt_frame(plays),
        "kickoff": D.kickoff_frame(plays),
        "pass": D.pass_frame(plays),
        "plays": plays,
    }


def gate_check(eng: Engine, states: list[GameState]) -> dict[str, Any]:
    """Re-check the bootstrap gate (D113) on real 4th downs: with every call bootstrapped, no
    call at least `boot_gap` wide may fall below the Confident share."""
    if not eng.m.gain_boot:
        return {"states": len(states), "ok": False, "skipped": "no bootstrap refits"}
    gap, conf = eng.cfg["boot_gap"], eng.cfg["confident"]
    wide = flipped = 0
    worst = 1.0
    for s in states:
        d = eng.fourth_down(s, bootstrap="all")
        if d.gap >= gap:
            wide += 1
            worst = min(worst, d.boot_share)
            flipped += d.boot_share < conf
    return {"states": len(states), "wide": wide, "below_confident": flipped,
            "worst_share": worst, "ok": flipped == 0}  # fmt: skip


def sample_fourth_downs(plays, season: int, n: int = 400, seed: int = 1) -> list[GameState]:
    rows = D.fourth_frame(plays.filter(plays["season"] == season))
    out = []
    for r in rows.sample(min(n, rows.height), seed=seed).iter_rows(named=True):
        try:
            out.append(D.state_of_row(r))
        except ValueError:
            continue
    return out


def run_checks(
    models: M.LiveModels, repeats: int = 15, gate_states: list[GameState] | None = None
) -> dict[str, Any]:
    """The hand-made decisions, the timing budget and the bootstrap gate on a fitted bundle."""
    eng = Engine(models, decide_settings(), threads=4)
    try:
        calls = []
        for name, s, want in CHECKS:
            d = eng.fourth_down(s)
            calls.append(
                {"state": name, "want": want, "got": d.best, "ok": d.best == want,
                 "gap": round(d.gap, 4), "label": d.label, "ms": d.ms,
                 "wp": {k: (round(v, 4) if v is not None else None) for k, v in d.wp.items()}}
            )  # fmt: skip
        timing = []
        for name, s in TIMING_STATES:
            eng.third_down(s)  # warm-up
            ms = [eng.third_down(s).ms for _ in range(repeats)]
            timing.append(
                {"state": name, "rows": len(eng.third_down(s).table),
                 "median_ms": round(statistics.median(ms), 2), "max_ms": round(max(ms), 2)}
            )  # fmt: skip
        gate = gate_check(eng, gate_states) if gate_states else None
    finally:
        eng.close()
    return {
        "gate": gate,
        "calls": calls,
        "calls_ok": all(c["ok"] for c in calls),
        "timing": timing,
        "timing_ok": all(t["median_ms"] < TIMING_BUDGET_MS for t in timing),
        "budget_ms": TIMING_BUDGET_MS,
    }


def _curve_callback(run, name: str) -> Callable:
    """Log a LightGBM fit's training loss live: `<name>/train_<metric>` against `<name>/iter`."""
    run.define_metric(f"{name}/*", step_metric=f"{name}/iter")

    def cb(env) -> None:
        for _, metric, value, _ in env.evaluation_result_list:
            run.log({f"{name}/iter": env.iteration + 1, f"{name}/train_{metric}": value})

    cb.order = 30
    return cb


def run_train(
    seasons: list[int] | None = None,
    promote: bool = False,
    n_boot: int | None = None,
    use_wandb: bool = True,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    paths: DataPaths | None = None,
) -> dict[str, Any]:
    from nflengine import tracking
    from nflengine.settings import get_config

    paths = paths or ensure_data_root()
    live_cfg = get_config().live or {}
    if seasons is None:
        a, b = str(live_cfg.get("train_seasons", "2010-2025")).split("-")
        seasons = list(range(int(a), int(b) + 1))
    cfg = M.settings()
    if n_boot is not None:
        cfg["n_boot"] = n_boot
    if cfg["n_boot"] < 0:
        raise ValueError("n_boot can't be negative")
    if promote and cfg["n_boot"] < 1:
        raise ValueError("--promote needs bootstrap refits (the confidence label): n_boot >= 1")
    created = dt.datetime.now(dt.UTC).replace(microsecond=0)
    version = f"{min(seasons)}-{max(seasons)}_{created.strftime('%Y%m%dT%H%M%SZ')}"
    folder = root(paths) / version
    log(f"live-decision models {version}: fitting on {min(seasons)}-{max(seasons)}")
    t0 = time.perf_counter()
    frames = load_frames(seasons, paths=paths)
    config = {
        "family": FAMILY,
        "version": version,
        "seasons": seasons,
        "settings": cfg,
        "features": {
            "wp": schema.WP_FEATURES,
            "gain": M.gain_features(False),
            "pass": schema.PASS_FEATURES,
        },
        "dataset_version": tracking.dataset_version(paths),
        "git_commit": tracking.git_commit(),
    }
    run = None
    if use_wandb:
        run = tracking.init_run(
            GROUP, "live-train", config=config, tags=["ld00", "train"], launched_by=launched_by,
            name=f"live-train-{version}",
        )  # fmt: skip
    try:
        callbacks = {n: [_curve_callback(run, n)] for n in ("wp", "gain", "pass")} if run else None
        models, rep = M.fit_all(frames, seasons, cfg, log=log, callbacks=callbacks)
        models.meta.update(
            {"version": version, "created": created.isoformat(), "git_commit": config["git_commit"],
             "dataset_version": config["dataset_version"], "seconds": rep.seconds,
             "ties": D.wp_frame(frames["plays"])[1]}
        )  # fmt: skip
        files = M.save(models, folder)
        checks = run_checks(models, gate_states=sample_fourth_downs(frames["plays"], max(seasons)))
        (folder / "checks.json").write_text(json.dumps(checks, indent=1), encoding="utf-8")
        log(
            f"checks: calls {'ok' if checks['calls_ok'] else 'FAILED'}, timing "
            + ", ".join(f"{t['median_ms']} ms ({t['rows']} rows)" for t in checks["timing"])
        )
        gate_ok = checks["gate"] is None or checks["gate"]["ok"]
        log(f"bootstrap gate: {checks['gate']}")
        if promote and not (checks["calls_ok"] and checks["timing_ok"] and gate_ok):
            raise RuntimeError(
                "not promoted: the decision checks, the timing budget or the bootstrap gate failed"
            )
        aliases: list[str] = []
        url = None
        if run is not None:
            import wandb

            run.summary.update(
                {"rows": rep.rows, "seconds": rep.seconds, "checks_ok": checks["calls_ok"],
                 "timing_ok": checks["timing_ok"], "version": version,
                 **{f"timing/{i}_median_ms": t["median_ms"]
                    for i, t in enumerate(checks["timing"])}}
            )  # fmt: skip
            run.log(
                {"checks/calls": wandb.Table(
                    columns=["state", "want", "got", "ok", "gap", "label", "ms"],
                    data=[[c["state"], c["want"], c["got"], c["ok"], c["gap"], c["label"], c["ms"]]
                          for c in checks["calls"]])}
            )  # fmt: skip
            art = wandb.Artifact(
                FAMILY,
                type="model",
                description=CARD.read_text(encoding="utf-8") if CARD.exists() else version,
                metadata={k: v for k, v in models.meta.items() if k != "settings"},
            )
            art.add_dir(str(folder))
            aliases = [version, *(["production"] if promote else [])]
            run.log_artifact(art, aliases=aliases)
            url = run.url
        if promote:
            pointer = {
                "version": version,
                "promoted_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
                "wandb_run": url,
                "seasons": seasons,
            }
            target = root(paths) / "production.json"
            tmp = target.with_name(f".production.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(pointer, indent=1), encoding="utf-8")
            os.replace(tmp, target)  # atomic: a reader never sees half a pointer
            log(f"promoted: {FAMILY}:{version} is production")
    finally:
        if run is not None:
            run.finish()
    log(f"models -> {folder} ({len(files)} files, {time.perf_counter() - t0:.0f} s)")
    return {
        "version": version,
        "folder": folder,
        "checks": checks,
        "aliases": aliases,
        "url": url,
        "promoted": promote,
    }


def production_folder(paths: DataPaths | None = None) -> Path:
    """The promoted bundle's folder (from `production.json`)."""
    pointer = root(paths) / "production.json"
    if not pointer.exists():
        raise FileNotFoundError(
            "no promoted live-decision models yet: run `nfl live train --promote`"
        )
    version = json.loads(pointer.read_text(encoding="utf-8"))["version"]
    return root(paths) / version


def load_production(paths: DataPaths | None = None) -> M.LiveModels:
    return M.load(production_folder(paths))
