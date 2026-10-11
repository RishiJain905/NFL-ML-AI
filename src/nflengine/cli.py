"""`nfl` command-line entry point (documentation/02 -> Components).

P00 provides `doctor` and `wandb-smoke`; the other commands are placeholders
that later phases fill in.
"""

from __future__ import annotations

import math
import random
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    help="NFL Analytics Engine: weekly NFL digest + Big Data Bowl research.",
    no_args_is_help=True,
    add_completion=False,
    # Never render local variables in tracebacks: frames can hold raw secrets.
    pretty_exceptions_show_locals=False,
)
console = Console()

STATUS_STYLE = {"ok": "[green]OK[/]", "warn": "[yellow]WARN[/]", "fail": "[red]FAIL[/]"}


@app.command()
def doctor(
    init_data_root: bool = typer.Option(
        False, "--init-data-root", help="Create the NFL_DATA_ROOT folder if it does not exist."
    ),
) -> None:
    """Check data drive, env vars (set / not set), Neo4j, W&B, nflreadpy and the LLM."""
    from nflengine.doctor import FAIL, run_checks

    checks = run_checks(init_data_root=init_data_root)
    table = Table(title="nfl doctor")
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    for c in checks:
        table.add_row(c.name, STATUS_STYLE[c.status], c.detail)
    console.print(table)
    if any(c.status == FAIL for c in checks):
        raise typer.Exit(1)


@app.command("wandb-smoke")
def wandb_smoke(
    steps: int = typer.Option(50, min=1, help="Number of fake training steps to log."),
    delay: float = typer.Option(0.15, help="Seconds between steps (so the chart fills live)."),
    launched_by: str | None = typer.Option(None, help="rishi | agent (auto-detected if omitted)."),
) -> None:
    """Log a fake training curve + table to W&B to prove tracking works."""
    from nflengine.tracking import init_run

    run = init_run(
        group="smoke-test",
        job_type="smoke",
        config={"steps": steps, "delay": delay},
        tags=["p00"],
        launched_by=launched_by,
    )
    console.print(f"W&B run: [link={run.url}]{run.url}[/link]")
    rng = random.Random(0)
    for step in range(steps):
        train = 1.2 * math.exp(-step / 12) + 0.15 + rng.gauss(0, 0.02)
        val = 1.25 * math.exp(-step / 14) + 0.20 + rng.gauss(0, 0.03)
        run.log({"train/loss": train, "val/loss": val, "step": step})
        time.sleep(delay)

    import wandb

    table = wandb.Table(columns=["check", "value"])
    table.add_data("steps_logged", steps)
    table.add_data("final_val_loss", round(val, 4))
    run.log({"smoke_summary": table})
    run.summary["final_val_loss"] = val
    run.finish()
    console.print("[green]Smoke run finished.[/]")


def _parse_seasons(value: str | None) -> list[int] | None:
    """'2018-2025' or '2024,2025' or '2026' -> list of seasons."""
    if not value:
        return None
    out: list[int] = []
    for chunk in value.split(","):
        if "-" in chunk:
            a, b = chunk.split("-")
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(chunk))
    return out


def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


@app.command()
def ingest(
    season: int | None = typer.Option(None, help="Current season (default: config)."),
    sources: str | None = typer.Option(
        None, help="Comma list: nflverse,espn,ngs_site,weather,odds_api (default: all)."
    ),
    datasets: str | None = typer.Option(None, help="Comma list of nflverse datasets only."),
    seasons: str | None = typer.Option(None, help="Seasons for nflverse, e.g. 2018-2025."),
    refresh_history: bool = typer.Option(
        False, "--refresh-history", help="Re-pull completed seasons too (normally pulled once)."
    ),
    check_ready: bool = typer.Option(
        False, "--check-ready", help="Only check whether --week is complete in the data."
    ),
    week: int | None = typer.Option(None, help="Week for --check-ready."),
) -> None:
    """Pull every data source into dated Parquet snapshots on D: (P01)."""
    if check_ready:
        from nflengine.curate.readiness import check_ready as _check

        report = _check(season, week)
        style = "green" if report.ready else "yellow"
        console.print(f"[{style}]{report.summary()}[/]")
        raise typer.Exit(0 if report.ready else 3)

    from nflengine.ingest.runner import run_ingest

    results, manifest = run_ingest(
        season=season,
        sources=_csv(sources),
        nflverse_datasets=_csv(datasets),
        seasons=_parse_seasons(seasons),
        refresh_history=refresh_history,
        log=console.print,
    )
    table = Table(title="ingest summary")
    for col in ("source", "dataset", "status", "rows", "detail"):
        table.add_column(col, overflow="fold")
    style = {"ok": "green", "partial": "yellow", "skipped": "dim", "failed": "red"}
    for r in results:
        table.add_row(
            r.source, r.dataset, f"[{style[r.status]}]{r.status}[/]", f"{r.rows:,}", r.detail[:90]
        )
    console.print(table)
    console.print(f"Run manifest: {manifest}")
    if any(r.status == "failed" and r.source == "nflverse" for r in results):
        raise typer.Exit(1)


@app.command()
def curate(
    skip_checks: bool = typer.Option(False, "--skip-checks", help="Build tables only."),
) -> None:
    """Build curated tables + DuckDB views from the latest snapshots, then run quality checks."""
    from nflengine.curate.build import build_all
    from nflengine.curate.quality import BLOCK, run_quality_checks

    built = build_all(log=console.print)
    console.print(f"[green]Curated {len(built)} tables.[/]")
    if skip_checks:
        return
    checks = run_quality_checks()
    table = Table(title="data-quality checks")
    for col in ("check", "level", "result", "detail"):
        table.add_column(col, overflow="fold")
    for c in checks:
        res = (
            "[green]pass[/]"
            if c.passed
            else ("[red]FAIL[/]" if c.level == BLOCK else "[yellow]warn[/]")
        )
        table.add_row(c.name, c.level, res, c.detail)
    console.print(table)
    if any(not c.passed and c.level == BLOCK for c in checks):
        raise typer.Exit(1)


@app.command("data-status")
def data_status() -> None:
    """Newest snapshot / season / week per dataset, row counts, join rates, disk use."""
    from nflengine.curate.status import render_status

    console.print(render_status())


# ---- the control room (CR00, documentation/control-room/) ---------------------------------


@app.command("app")
def control_room(
    port: int = typer.Option(8765, min=1024, max=65535, help="Port on 127.0.0.1."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open a browser tab."),
    dev: bool = typer.Option(
        False, "--dev", help="API only, for `npm --prefix web run dev` (Vite on port 5173)."
    ),
    rehearsal: bool = typer.Option(
        False,
        "--rehearsal",
        help="The Run button rehearses the newest published week (`nfl weekly rehearse`) into "
        "rehearsals/control-room instead of running the live week.",
    ),
    fail_at: str | None = typer.Option(
        None,
        "--fail-at",
        hidden=True,  # CR02's failure-and-resume proof: only with --rehearsal
    ),
    live_replay: str | None = typer.Option(
        None,
        "--live-replay",
        help="LD02: Game day serves this finished game's week (ESPN event id, saved under "
        "live/summaries/) as if live, so the tab can be tried outside game times.",
    ),
    replay_at: str | None = typer.Option(
        None, "--replay-at", help="Where the replay starts (ISO time; no zone = US Eastern)."
    ),
    replay_speed: float = typer.Option(1.0, "--replay-speed", min=0.1, max=60.0),
    replay_lag: float = typer.Option(
        10.0, "--replay-lag", min=0.0, max=300.0, help="Seconds ESPN takes to post a play."
    ),
) -> None:
    """Open the control room: a local web app for the weekly pipeline (127.0.0.1 only)."""
    from nflengine.app.preflight import REHEARSAL_STEPS
    from nflengine.app.serve import serve

    if fail_at is not None and (not rehearsal or fail_at not in REHEARSAL_STEPS):
        raise typer.BadParameter(
            f"--fail-at needs --rehearsal and one of {', '.join(REHEARSAL_STEPS)}"
        )
    replay = None
    if live_replay is not None:
        replay = {"event": live_replay, "at": replay_at, "speed": replay_speed, "lag_s": replay_lag}
    elif replay_at is not None or replay_speed != 1.0 or replay_lag != 10.0:
        raise typer.BadParameter("--replay-at, --replay-speed and --replay-lag need --live-replay")
    raise typer.Exit(
        serve(
            port=port,
            open_browser=not no_browser,
            dev=dev,
            rehearsal=rehearsal,
            fail_at=fail_at,
            log=print,
            live_replay=replay,
        )
    )


# ---- team ratings, Elo, trend (P02) -------------------------------------------------------

ratings_app = typer.Typer(
    help="Team ratings, Elo and trend (P02): build tables, tune, evaluate, validate trend.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
app.add_typer(ratings_app, name="ratings")

HALF_LIFE = typer.Option(None, help="Recency half-life in weeks (default: settings.yaml).")
PRIOR_REG = typer.Option(None, help="Pull of last season's rating toward average, 0-1.")
RIDGE_ALPHA = typer.Option(None, help="Shrinkage strength in recency-weighted plays.")
QB_REG = typer.Option(None, help="Extra offense pull after a week-1 QB change, 0-1.")
EVAL_SEASONS_OPT = typer.Option("2015-2025", help="Walk-forward evaluation seasons.")
LAUNCHED_BY = typer.Option(None, help="rishi | agent (auto-detected if omitted).")
# CR02: set by the control room when it launches a command (its run id names the events
# file, and every W&B run gets a `via:control-room` tag, D103). Hidden: not for people.
APP_RUN = typer.Option(None, "--app-run", hidden=True)


def _app_run(app_run: str | None) -> str | None:
    """Check `--app-run` and mark this process as launched by the control room."""
    if app_run is None:
        return None
    from nflengine.ops.events import valid_run_id
    from nflengine.tracking import set_launch_via

    if not valid_run_id(app_run):
        raise typer.BadParameter("--app-run takes a run id like 20261006T140012Z-a1b2")
    set_launch_via("control-room")
    return app_run


def _params(half_life, prior_regression, ridge_alpha, qb_change_regression):
    from nflengine.models.ratings_build import rating_params

    return rating_params(
        half_life_weeks=half_life,
        prior_regression=prior_regression,
        ridge_alpha=ridge_alpha,
        qb_change_regression=qb_change_regression,
    )


@ratings_app.command("build")
def ratings_build(
    half_life: float | None = HALF_LIFE,
    prior_regression: float | None = PRIOR_REG,
    ridge_alpha: float | None = RIDGE_ALPHA,
    qb_change_regression: float | None = QB_REG,
    evidence: bool = typer.Option(True, help="Add trend evidence fields (QB, injuries, ...)."),
) -> None:
    """Write team_ratings, team_elo, team_trends, team_trend_drivers to features/ on D:."""
    from nflengine.models.ratings_build import run_build

    params = _params(half_life, prior_regression, ridge_alpha, qb_change_regression)
    run_build(params, log=console.print, with_evidence=evidence)


@ratings_app.command("tune")
def ratings_tune(
    half_lives: str | None = typer.Option(None, help="Comma list, e.g. 4,8,16."),
    prior_regressions: str | None = typer.Option(None, help="Comma list, e.g. 0,0.1,0.33."),
    ridge_alphas: str | None = typer.Option(None, help="Comma list, e.g. 100,250,400."),
    qb_change_regressions: str | None = typer.Option(None, help="Comma list (default: off)."),
    seasons: str = EVAL_SEASONS_OPT,
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Grid sweep over half-life x prior pull x ridge alpha, logged as a W&B sweep."""
    from nflengine.models.ratings_runs import grid_from_options, grid_size, run_tune

    grid = grid_from_options(
        half_life_weeks=half_lives,
        prior_regression=prior_regressions,
        ridge_alpha=ridge_alphas,
        qb_change_regression=qb_change_regressions,
    )
    console.print(f"Grid ({grid_size(grid)} runs): {grid}")
    df = run_tune(grid, _parse_seasons(seasons), launched_by, log=console.print)
    if df.height:
        cols = [*grid, "objective_mse", "mse_w1_3", "improvement_vs_base_last_season"]
        console.print(df.select(cols).head(10))


@ratings_app.command("eval")
def ratings_eval(
    half_life: float | None = HALF_LIFE,
    prior_regression: float | None = PRIOR_REG,
    ridge_alpha: float | None = RIDGE_ALPHA,
    qb_change_regression: float | None = QB_REG,
    seasons: str = EVAL_SEASONS_OPT,
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Walk-forward check of one configuration vs baselines, plus the Elo Brier score."""
    from nflengine.models.ratings_runs import run_eval

    params = _params(half_life, prior_regression, ridge_alpha, qb_change_regression)
    s = run_eval(params, _parse_seasons(seasons), launched_by, log=console.print)
    for k in (
        "objective_mse",
        "mse_base_last_season",
        "mse_base_to_date",
        "mse_w1_3",
        "elo_brier",
        "elo_brier_reg",
    ):
        console.print(f"  {k}: {s[k]:.5f}")


@ratings_app.command("validate-trend")
def ratings_validate_trend(
    half_life: float | None = HALF_LIFE,
    prior_regression: float | None = PRIOR_REG,
    ridge_alpha: float | None = RIDGE_ALPHA,
    qb_change_regression: float | None = QB_REG,
    seasons: str = EVAL_SEASONS_OPT,
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Does trend_delta predict the next 3 games beyond the rating? (decides D06)"""
    from nflengine.models.ratings_runs import run_validate_trend

    params = _params(half_life, prior_regression, ridge_alpha, qb_change_regression)
    s = run_validate_trend(params, _parse_seasons(seasons), launched_by, log=console.print)
    for k, v in s.items():
        if k != "url":
            console.print(f"  {k}: {v:.5f}" if isinstance(v, float) else f"  {k}: {v}")


# ---- game model (P03) -------------------------------------------------------------------------

features_app = typer.Typer(
    help="Feature tables with as-of guarantees (P03+).",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
backtest_app = typer.Typer(
    help="Walk-forward backtests logged live to W&B (P03+).",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
train_app = typer.Typer(
    help="Weekly production fits: refit, predict the week, save + log the model (P03+).",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
app.add_typer(features_app, name="features")
app.add_typer(backtest_app, name="backtest")
app.add_typer(train_app, name="train")

VARIANT_OPT = typer.Option("model-only", help="model-only | market")
GAME_SEASONS_OPT = typer.Option("2018-2025", help="Reported walk-forward seasons.")


def _variant(value: str) -> str:
    v = value.strip().lower().replace("-", "_")
    if v not in ("model_only", "market"):
        raise typer.BadParameter("variant must be model-only or market")
    return v


@features_app.command("game")
def features_game() -> None:
    """Build features/game_features.parquet (one row per game, as of its week)."""
    from nflengine.models.game_runs import load_frame, write_feature_table

    frame = load_frame(log=console.print)
    out = write_feature_table(frame)
    console.print(f"[green]game features: {frame.height:,} games -> {out}[/]")


@backtest_app.command("game")
def backtest_game(
    variant: str = VARIANT_OPT,
    seasons: str = GAME_SEASONS_OPT,
    current_weight: float | None = typer.Option(
        None, help="Current-season sample weight (default: settings.yaml)."
    ),
    win_method: str | None = typer.Option(None, help="margin | logistic"),
    calibration: str | None = typer.Option(None, help="none | platt | isotonic"),
    qb_mode: str = typer.Option(
        "tuesday", help="tuesday (what a Tuesday run knows) | actual (research oracle)."
    ),
    version: str = typer.Option(
        "v0", help="v0 (ridge, P03) | v1 (LightGBM, P08; scores v0 on the same games)."
    ),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Walk-forward backtest of the game model vs home / Elo / market baselines."""
    from nflengine.models.backtest import SampleWeights
    from nflengine.models.game_runs import VERSIONS, run_backtest

    if version not in VERSIONS:
        raise typer.BadParameter(f"version must be one of {VERSIONS}")
    weights = SampleWeights.for_current(current_weight) if current_weight else None
    extra = {"win_method": win_method} if version == "v0" else {}
    res = run_backtest(
        _variant(variant),
        _parse_seasons(seasons),
        launched_by,
        log=console.print,
        qb_mode=qb_mode,
        version=version,
        weights=weights,
        calibration=calibration,
        **extra,
    )
    s = res.summary
    table = Table(title=f"backtest {version} {variant} (pooled, {int(s['games'])} games)")
    for col in ("predictor", "brier", "log loss", "accuracy", "ECE"):
        table.add_column(col)
    names = ("model", "v0", "elo", "market", "home") if "brier_v0" in s else (
        "model", "elo", "market", "home"
    )  # fmt: skip
    for name in names:
        table.add_row(
            name,
            f"{s[f'brier_{name}']:.4f}",
            f"{s[f'log_loss_{name}']:.4f}",
            f"{s[f'accuracy_{name}']:.3f}",
            f"{s[f'ece_{name}']:.4f}",
        )
    console.print(table)
    console.print(
        f"margin MAE {s['mae_margin_model']:.2f} (market {s['mae_margin_market']:.2f}); "
        f"points MAE per team {s['mae_points_model']:.2f} (rolling {s['mae_points_rolling']:.2f},"
        f" market {s['mae_points_market']:.2f}); total MAE {s['mae_total_model']:.2f}"
    )
    if "brier_diff_vs_v0" in s:
        console.print(
            f"v1 - v0 Brier {s['brier_diff_vs_v0']:+.4f} (95% {s['brier_diff_vs_v0_lo95']:+.4f} "
            f"to {s['brier_diff_vs_v0_hi95']:+.4f}); better in "
            f"{int(s['seasons_beating_v0'])} of {int(s['seasons'])} seasons"
        )
    console.print(f"predictions saved -> {res.saved_to}")


@backtest_app.command("game-weights")
def backtest_game_weights(
    weights: str = typer.Option("1,2,3,5", help="Current-season weights to compare."),
    variant: str = VARIANT_OPT,
    seasons: str = GAME_SEASONS_OPT,
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """W&B sweep over the current-season sample weight (pooled Brier per value)."""
    from nflengine.models.game_runs import run_weight_sweep

    values = [float(v) for v in weights.split(",") if v.strip()]
    df = run_weight_sweep(
        values, _variant(variant), _parse_seasons(seasons), launched_by, log=console.print
    )
    if df.height:
        cols = [c for c in df.columns if c.startswith(("current_season", "brier_model"))]
        console.print(df.select(cols))


@train_app.command("game")
def train_game(
    season: int = typer.Option(..., help="Season to predict."),
    week: int | None = typer.Option(None, help="Week to predict (default: the next one)."),
    promote: bool = typer.Option(
        False, "--promote", help="Also give the W&B artifact the `production` alias."
    ),
    version: str | None = typer.Option(
        None, help="v0 | v1 (default: settings.yaml game_model.version, the production model)."
    ),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Refit on everything before week N, predict week N, save predictions_games.parquet."""
    from nflengine.models.game_runs import run_train

    out = run_train(season, week, launched_by, promote=promote, log=console.print, version=version)
    t = out["table"].filter(out["table"]["is_primary"])
    table = Table(title=f"{season} week {t['week'][0]:02d} predictions (digest rows)")
    for col in ("game", "home win %", "margin", "score", "variant", "QBs"):
        table.add_column(col)

    def num(v: float | None, spec: str) -> str:
        return "?" if v is None else format(v, spec)

    for r in t.iter_rows(named=True):
        p = r["home_win_prob"]
        table.add_row(
            f"{r['away_team']} @ {r['home_team']}",
            "?" if p is None else f"{100 * p:.0f}%",
            num(r["expected_margin"], "+.1f"),
            f"{r['home_team']} {num(r['pred_home_points'], '.0f')} - "
            f"{num(r['pred_away_points'], '.0f')} {r['away_team']}",
            r["variant"] + (" (no line)" if r["market_fallback"] else ""),
            f"{r['away_qb_name'] or '?'} @ {r['home_qb_name'] or '?'}",
        )
    console.print(table)
    console.print(f"W&B: {out['url']}  aliases {out['aliases']}")


# ---- player model (P06) ----------------------------------------------------------------------

PLAYER_SEASONS_OPT = typer.Option("2019-2025", help="Reported walk-forward seasons.")
TARGET_OPT = typer.Option(
    ...,
    help="Target name (rec_yds, receptions ...), key (receptions-rb) or `all`; "
    "see models/player_schema.py TARGETS.",
)
GROUP_OPT = typer.Option(
    None, help="Only this position group (QB, RB, WR/TE, EDGE/DL, LB/S, CB/S)."
)
TEAM_TARGET_OPT = typer.Option(
    ...,
    help="pass_yds | rush_yds | sacks_made | sacks_taken | takeaways | all "
    "(models/team_model.py TEAM_TARGETS).",
)


@features_app.command("player")
def features_player() -> None:
    """Build features/player_features.parquet (one row per player-game, as of its week)."""
    from nflengine.models.player_runs import build_player_data, write_feature_table

    data = build_player_data(log=console.print)
    out = write_feature_table(data.feats)
    console.print(
        f"[green]player features: {data.feats.height:,} rows x {len(data.feats.columns)} "
        f"columns -> {out}[/]"
    )


@backtest_app.command("player")
def backtest_player(
    target: str = TARGET_OPT,
    group: str | None = GROUP_OPT,
    seasons: str = PLAYER_SEASONS_OPT,
    threads: int | None = typer.Option(None, help="LightGBM threads per fit (default 4)."),
    save: bool = typer.Option(True, help="Save predictions + scoreboard rows on D:."),
    smoke: bool = typer.Option(False, "--smoke", help="Tag the run `smoke` and don't save."),
    no_market: bool = typer.Option(
        False, "--no-market", help="Research variant without the closing-line features."
    ),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Walk-forward backtest of player targets vs the rolling baseline (live W&B curves)."""
    from nflengine.models.player_runs import load_saved_data, run_backtest
    from nflengine.models.player_schema import get_target

    data = load_saved_data(log=console.print)
    reported = _parse_seasons(seasons) or [2019, 2025]
    table = Table(title="player backtests (reported seasons, pooled)")
    for col in ("model", "n", "MAE model", "MAE baseline", "improvement", "80% range"):
        table.add_column(col)
    for t in get_target(target, group):
        res = run_backtest(
            t,
            data,
            reported,
            launched_by,
            log=console.print,
            save=save and not smoke,
            tags=(["smoke"] if smoke else []) + (["no-market"] if no_market else []),
            num_threads=threads,
            no_market=no_market,
        )
        s = res.summary
        table.add_row(
            t.key,
            f"{int(s.get('n_scored', 0)):,}",
            f"{s.get('mae_model', float('nan')):.3f}",
            f"{s.get('mae_baseline', float('nan')):.3f}",
            f"{s.get('improvement_pct', float('nan')):+.1f}%",
            f"{100 * s.get('coverage_80', float('nan')):.0f}%",
        )
        console.print(f"{t.key}: W&B {res.url}; saved -> {res.saved_to}")
    console.print(table)


tune_app = typer.Typer(
    help="Hyperparameter sweeps (P06+).", no_args_is_help=True, pretty_exceptions_show_locals=False
)
app.add_typer(tune_app, name="tune")


@tune_app.command("game")
def tune_game(
    variant: str = VARIANT_OPT,
    seasons: str = typer.Option("2013-2017", help="Tuning window (never the reported seasons)."),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """W&B grid sweep over the game model v1 tree settings (P08), scored by walk-forward
    Brier on the tuning window, with v0 scored on the same games."""
    from nflengine.models.game_runs import run_tune_v1

    window = _parse_seasons(seasons)
    df = run_tune_v1(variant=_variant(variant), tune_seasons=window, launched_by=launched_by,
                     log=console.print)  # fmt: skip
    if df.height:
        cols = [
            c
            for c in df.columns
            if c in ("base", "num_leaves", "n_estimators", "min_data_in_leaf")
            or c in ("brier_model", "brier_v0", "ece_model", "brier_diff_vs_v0")
        ]
        console.print(df.select(cols).head(10))


@tune_app.command("team")
def tune_team(
    target: str = TEAM_TARGET_OPT,
    threads: int | None = typer.Option(None, help="LightGBM threads per fit."),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Grid over the team models' tree settings, scored on 2017-2018 only (P08)."""
    from nflengine.models.team_model import get_team_target
    from nflengine.models.team_runs import run_tune

    for t in get_team_target(target):
        df = run_tune(t, launched_by=launched_by, log=console.print, num_threads=threads)
        console.print(df.head(5))


@tune_app.command("player")
def tune_player(
    target: str = TARGET_OPT,
    group: str | None = GROUP_OPT,
    threads: int | None = typer.Option(None, help="LightGBM threads per fit."),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """W&B grid sweep over the tree settings, scored on a 2017-2018 walk-forward."""
    from nflengine.models.player_runs import load_saved_data, run_tune
    from nflengine.models.player_schema import get_target

    data = load_saved_data(log=console.print)
    for t in get_target(target, group):
        df = run_tune(t, data, launched_by=launched_by, log=console.print, num_threads=threads)
        console.print(df.head(5))


@backtest_app.command("team")
def backtest_team(
    target: str = TEAM_TARGET_OPT,
    seasons: str = PLAYER_SEASONS_OPT,
    threads: int | None = typer.Option(None, help="LightGBM threads per fit."),
    save: bool = typer.Option(True, help="Save predictions + scoreboard rows on D:."),
    smoke: bool = typer.Option(False, "--smoke", help="Tag the run `smoke` and don't save."),
    no_market: bool = typer.Option(
        False, "--no-market", help="Research variant without the closing-line features."
    ),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Walk-forward backtest of team stat totals vs their baselines (P08, live W&B curves)."""
    from nflengine.models.team_model import get_team_target
    from nflengine.models.team_runs import build_team_data, run_backtest

    data = build_team_data(log=console.print)
    reported = _parse_seasons(seasons) or [2019, 2025]
    table = Table(title="team backtests (reported seasons, pooled)")
    for col in ("model", "n", "MAE model", "MAE baseline", "improvement", "80% range", "ship"):
        table.add_column(col)
    for t in get_team_target(target):
        res = run_backtest(
            t,
            data,
            reported,
            launched_by,
            log=console.print,
            save=save and not smoke,
            tags=(["smoke"] if smoke else []) + (["no-market"] if no_market else []),
            num_threads=threads,
            no_market=no_market,
        )
        s = res.summary
        table.add_row(
            t.key,
            f"{int(s.get('n_scored', 0)):,}",
            f"{s.get('mae_model', float('nan')):.3f}",
            f"{s.get('mae_baseline', float('nan')):.3f}",
            f"{s.get('improvement_pct', float('nan')):+.1f}%",
            f"{100 * s.get('coverage_80', float('nan')):.0f}%",
            "pass" if s.get("ship/pass") else "fail",
        )
        console.print(f"{t.key}: W&B {res.url}; saved -> {res.saved_to}")
    console.print(table)


@train_app.command("team")
def train_team(
    season: int = typer.Option(..., help="Season to predict."),
    week: int | None = typer.Option(None, help="Week to predict (default: the next one)."),
    promote: bool = typer.Option(
        False, "--promote", help="Also give the W&B artifact the `production` alias."
    ),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Refit the shipped team stat targets (`team_model.live_targets`) and project week N
    into predictions_teams.parquet (P08; the weekly `player` step does this too)."""
    from nflengine.models.team_runs import run_train

    out = run_train(season, week, launched_by, promote=promote, log=console.print)
    t = out["table"].sort("game_id", "team", "target")
    table = Table(title=f"{season} week {week or ''}: team projections")
    for col in ("team", "vs", "stat", "projection", "80% range", "baseline"):
        table.add_column(col)
    for r in t.head(40).iter_rows(named=True):
        table.add_row(
            r["team"],
            r.get("opponent") or "",
            r["target"],
            f"{r['p50']:.0f}" if r["p50"] is not None else "?",
            f"{r['p10']:.0f}-{r['p90']:.0f}" if r["p10"] is not None else "?",
            f"{r['baseline']:.0f}" if r["baseline"] is not None else "?",
        )
    console.print(table)
    console.print(f"-> {out['predictions']}; W&B: {out['url']}  aliases {out['aliases']}")


@app.command("consistency")
def consistency_eval(
    seasons: str = typer.Option("2019-2025", help="Backtest seasons to measure."),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Measure the consistency layer on the walk-forward backtests (receptions vs targets,
    receivers' yards vs the QB's vs the team's passing yards; P08). Changes no projection."""
    from nflengine.models.consistency import run_eval

    window = _parse_seasons(seasons) or [2019, 2025]
    res = run_eval(seasons=tuple(range(window[0], window[-1] + 1)), launched_by=launched_by,
                   log=console.print)  # fmt: skip
    console.print(res.get("by_season"))
    console.print(f"decision: {res.get('decision')}; W&B {res.get('url')}")


@train_app.command("player")
def train_player(
    season: int = typer.Option(..., help="Season to predict."),
    week: int | None = typer.Option(None, help="Week to predict (default: the next one)."),
    promote: bool = typer.Option(
        False, "--promote", help="Also give the W&B artifact the `production` alias."
    ),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Refit every player target through week N-1, project week N, save
    predictions_players.parquet."""
    from nflengine.models.player_runs import run_train

    out = run_train(season, week, launched_by, promote=promote, log=console.print)
    t = out["table"].filter(out["table"]["is_main"]).sort("outperf_z", descending=True)
    table = Table(title=f"{season} week {week}: biggest projected jumps (main stat per group)")
    for col in ("player", "team", "vs", "stat", "projection", "80% range", "baseline", "conf"):
        table.add_column(col)
    for r in t.head(15).iter_rows(named=True):
        table.add_row(
            r["player"] or r["player_id"],
            r["team"],
            r["opponent"],
            r["target_label"],
            f"{r['p50']:.1f}" if r["kind"] == "amount" else f"{r['mean']:.1f}",
            f"{r['p10']:.0f}-{r['p90']:.0f}",
            f"{r['baseline']:.1f}" if r["baseline"] is not None else "?",
            r["confidence"],
        )
    console.print(table)
    console.print(f"W&B: {out['url']}  aliases {out['aliases']}")


@app.command()
def scoreboard(
    season: int = typer.Option(..., help="Season."),
    week: int = typer.Option(..., help="The played week whose projections to score."),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Score a played week's saved player projections and update the accuracy scoreboard."""
    from nflengine.models.player_runs import score_week

    board = score_week(season, week, launched_by, log=console.print)
    console.print(board.filter(board["week"] == week))


# ---- digest + weekly pipeline (P04) ----------------------------------------------------------


@app.command()
def digest(
    season: int = typer.Option(..., help="Season of the digest."),
    week: int | None = typer.Option(None, help="Week of the digest (the upcoming slate)."),
    weeks: str | None = typer.Option(None, help="Several weeks, e.g. 7-10 (backtests)."),
    backtest: bool = typer.Option(
        False, "--backtest", help="Produce past weeks as if live on their Tuesday (as-of data)."
    ),
    no_wandb: bool = typer.Option(False, "--no-wandb", help="Skip the W&B run (local tests)."),
    llm: str | None = typer.Option(
        None, "--llm", help="Override llm.provider for this run (e.g. placeholder)."
    ),
    graph: str = typer.Option(
        "auto",
        "--graph",
        help="Graph sections: auto (backtest builds, live reads / builds if missing) | "
        "build | read | off.",
    ),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Payload -> LLM -> checks -> render -> reports/<season>/week<NN>-digest.md on D:."""
    from nflengine.digest.run import run_digest

    if (week is None) == (weeks is None):
        raise typer.BadParameter("give exactly one of --week or --weeks")
    todo = [week] if week is not None else _parse_seasons(weeks) or []
    failed = 0
    for w in todo:
        res = run_digest(
            season,
            w,
            mode="backtest" if backtest else "live",
            launched_by=launched_by,
            provider=llm,
            use_wandb=not no_wandb,
            log=console.print,
            graph=graph,
        )
        final = res.synthesis.final
        if res.synthesis.fallback_notes:
            console.print(f"[yellow]writer fallback: {res.synthesis.fallback_notes}[/]")
        style = "green" if final.passed else "red"
        console.print(
            f"[{style}]{season} week {w:02d}: checks "
            f"{'passed' if final.passed else 'FAILED ' + ', '.join(final.failed)}"
            f"{' (regenerated)' if res.synthesis.regenerated else ''}"
            f"{'; warnings: ' + ', '.join(final.warnings) if final.warnings else ''}[/]"
        )
        console.print(f"  digest: {res.report_path}")
        failed += not final.passed
    if failed:
        raise typer.Exit(2)


weekly_app = typer.Typer(
    help="The weekly pipeline (P04; calendar, lock, run records and the injury update in P07).",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
app.add_typer(weekly_app, name="weekly")


@weekly_app.command("run")
def weekly_run(
    season: int | None = typer.Option(None, help="Season (omit with --auto)."),
    week: int | None = typer.Option(
        None, help="The week to preview (week N; week N-1 must be done). Omit with --auto."
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Work out the season and week from the calendar; skip a week already published; "
        "exit 3 if the previous week isn't final yet (P07).",
    ),
    as_of: str | None = typer.Option(
        None,
        "--as-of",
        help="Pretend the run starts at this moment (ISO; no offset = US Eastern). In the "
        "past it simulates the week: readiness from kickoff + data lag, the digest as a "
        "backtest.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the calendar plan and what would run; run nothing."
    ),
    from_step: str | None = typer.Option(
        None,
        help="Resume from: ingest | ready | curate | ratings | game | graph | player | digest.",
    ),
    promote: bool | None = typer.Option(
        None,
        "--promote/--no-promote",
        help="Give this week's game-model and player-model artifacts the `production` alias "
        "(default: yes with --auto, no otherwise).",
    ),
    force: bool = typer.Option(
        False, "--force", help="With --auto: run even if the week is already published."
    ),
    llm: str | None = typer.Option(
        None, "--llm", help="Override llm.provider for the digest step (e.g. placeholder)."
    ),
    no_wandb: bool = typer.Option(
        False, "--no-wandb", help="Skip the pipeline and dashboard W&B runs (steps still log)."
    ),
    expect_week: int | None = typer.Option(
        None,
        "--expect-week",
        min=1,
        max=22,
        help="With --auto: stop before any step (exit 6, nothing written) unless the "
        "calendar's week is this one (CR02; the control room's Run button sends it).",
    ),
    launched_by: str | None = LAUNCHED_BY,
    app_run: str | None = APP_RUN,
) -> None:
    """ingest -> readiness -> curate -> ratings -> game -> graph -> player -> digest
    (resumable), then the run summary, drift checks and dashboard. Exit codes: 0 done
    (or nothing to do), 1 failed, 2 usage error, 3 not ready (retry later), 4 another run
    holds the lock, 5 data drive missing, 6 the calendar's week isn't --expect-week."""
    from nflengine.paths import DataRootError
    from nflengine.weekly import EXIT_NO_DRIVE, escape, parse_as_of, run_pipeline

    if not auto and (season is None or week is None):
        raise typer.BadParameter("give --season and --week, or --auto")
    if expect_week is not None and not auto:
        raise typer.BadParameter("--expect-week only works with --auto")
    run_id = _app_run(app_run)
    try:
        when = parse_as_of(as_of) if as_of else None
        out = run_pipeline(
            season,
            week,
            auto=auto,
            as_of=when,
            dry_run=dry_run,
            from_step=from_step,
            launched_by=launched_by,
            promote=promote,
            llm=llm,
            force=force,
            use_wandb=not no_wandb,
            log=console.print,
            expect_week=expect_week,
            run_id=run_id,
            via="control-room" if run_id else None,
        )
    except DataRootError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(EXIT_NO_DRIVE) from None
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None
    style = {
        "ok": "green",
        "already_done": "green",
        "idle": "green",
        "planned": "cyan",
        "week_mismatch": "red",
    }
    console.print(f"[{style.get(out.status, 'yellow')}]{out.status}: {escape(out.message)}[/]")
    if out.status in ("failed", "not_ready") and out.week is not None:
        if out.status == "failed":
            console.print(
                f"Fix it, then resume with `nfl weekly run --season {out.season} --week "
                f"{out.week} --from-step <step>` (documentation/runbook.md)."
            )
        else:
            console.print("Not ready: run it again later (the week before isn't final yet).")
    if out.summary_path:
        console.print(f"Run summary: {out.summary_path}")
    raise typer.Exit(out.exit_code)


@weekly_app.command("status")
def weekly_status(
    as_of: str | None = typer.Option(
        None, "--as-of", help="Look at the calendar at this moment (ISO; no offset = ET)."
    ),
) -> None:
    """What the calendar says now, the target week's step states, the lock and recent runs."""
    import json

    from nflengine.ops.calendar import load_schedules, plan_week
    from nflengine.ops.lock import is_locked, lock_path, read_holder
    from nflengine.ops.records import history_path, read_history
    from nflengine.paths import DataRootError, ensure_data_root
    from nflengine.weekly import EXIT_NO_DRIVE, parse_as_of

    try:
        paths = ensure_data_root()
    except DataRootError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(EXIT_NO_DRIVE) from None
    plan = plan_week(load_schedules(paths), parse_as_of(as_of) if as_of else None)
    for line in plan.describe():
        console.print(line)
    lp = lock_path(paths.runs)
    console.print(f"lock: {'held by ' + json.dumps(read_holder(lp)) if is_locked(lp) else 'free'}")
    for wk in [w for w in (plan.previous_week, plan.week) if w]:
        state_file = paths.run_dir(plan.season, wk) / "weekly_run.json"
        if not state_file.exists():
            console.print(f"week {wk}: no weekly run yet")
            continue
        steps = json.loads(state_file.read_text()).get("steps", {})
        line = ", ".join(f"{n} {s.get('status')}" for n, s in steps.items())
        console.print(f"week {wk}: {line or 'no steps'}")
    hist = read_history(history_path(paths.runs, plan.season))
    if hist.height:
        console.print("recent runs:")
        cols = ["week", "kind", "status", "finished", "on_time", "drift_alerts"]
        for r in hist.tail(5).select(cols).iter_rows(named=True):
            console.print("  " + ", ".join(f"{k} {v}" for k, v in r.items()))


@weekly_app.command("injury-update")
def weekly_injury_update(
    season: int | None = typer.Option(None, help="Season (omit with --auto)."),
    week: int | None = typer.Option(None, help="The week whose digest to update."),
    auto: bool = typer.Option(
        False, "--auto", help="The week the calendar is in (the slate about to be played)."
    ),
    no_ingest: bool = typer.Option(
        False, "--no-ingest", help="Use the data already on D: (no re-ingest / re-curate)."
    ),
    no_players: bool = typer.Option(
        False, "--no-players", help="Skip the player refit (~1 min); games and statuses only."
    ),
    no_wandb: bool = typer.Option(False, "--no-wandb", help="Skip the W&B run."),
    expect_week: int | None = typer.Option(
        None,
        "--expect-week",
        min=1,
        max=22,
        help="With --auto: stop before anything (exit 6) unless the calendar's week is this one "
        "(CR02; the control room's Saturday button sends it).",
    ),
    launched_by: str | None = LAUNCHED_BY,
    app_run: str | None = APP_RUN,
) -> None:
    """Saturday injury update: re-ingest injuries, news and lines, re-predict the week, and
    publish a short addendum only if something material changed (documentation/06). Exit
    codes: 0 done, 1 failed, 4 another run holds the lock, 5 data drive missing, 6 the
    calendar's week isn't --expect-week."""
    from nflengine.ops import events
    from nflengine.ops.calendar import load_schedules, plan_week
    from nflengine.ops.injury_update import run_injury_update
    from nflengine.ops.lock import LockHeld, lock_path, run_lock
    from nflengine.ops.records import append_history, history_path, iso, utc_now
    from nflengine.paths import DataRootError, ensure_data_root
    from nflengine.weekly import EXIT_LOCKED, EXIT_NO_DRIVE, EXIT_WEEK_MISMATCH, escape, scrub

    if expect_week is not None and not auto:
        raise typer.BadParameter("--expect-week only works with --auto")
    run_id = _app_run(app_run)

    try:
        # no standard folders created before the --expect-week guard (Sol review); after it
        # the update creates them as before
        paths = ensure_data_root(create_dirs=expect_week is None)
    except DataRootError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(EXIT_NO_DRIVE) from None
    if auto:
        plan = plan_week(load_schedules(paths))
        season, week = plan.season, plan.week
        if expect_week is not None and week != expect_week:  # before any write (Sol review)
            found = f"week {week}" if week is not None else "no week (offseason)"
            console.print(
                f"[red]the calendar's week is {found}, not week {expect_week} (--expect-week): "
                "nothing was run[/]"
            )
            raise typer.Exit(EXIT_WEEK_MISMATCH)
        if week is None:
            console.print("offseason: nothing to update")
            raise typer.Exit(0)
        if expect_week is not None:
            paths = ensure_data_root()
    if season is None or week is None:
        raise typer.BadParameter("give --season and --week, or --auto")
    hist = history_path(paths.runs, season)
    started = utc_now()

    def record(status: str, finished=None) -> None:  # inside the lock: never races a run
        finished = finished or utc_now()
        append_history(
            hist,
            {
                "season": season,
                "week": week,
                "kind": "injury-update",
                "run_id": started.strftime("%Y%m%dT%H%M%SZ"),
                "started": iso(started),
                "finished": iso(finished),
                "status": status,
                "total_seconds": round((finished - started).total_seconds(), 1),
                "launched_by": launched_by,
                "drift_alerts": 0,
            },
        )

    command = f"nfl weekly injury-update --season {season} --week {week}"
    rid = run_id or events.new_run_id(started)
    try:
        with (
            run_lock(lock_path(paths.runs), command, run_id=rid),
            events.writing(events.events_dir(paths.run_dir(season, week)) / f"{rid}.jsonl", rid),
        ):
            log = events.tee(console.print)  # each console line is also a `log` event (CR02)
            events.run_start(
                kind="injury_update",
                command="nfl weekly injury-update --auto" if auto else command,
                season=season,
                week=week,
                steps=["injury_update"],
                launched_by=launched_by,
                via="control-room" if run_id else None,
            )
            events.step_start("injury_update")
            try:
                res = run_injury_update(
                    season,
                    week,
                    launched_by=launched_by,
                    log=log,
                    use_wandb=not no_wandb,
                    ingest=not no_ingest,
                    players=not no_players,
                )
            except Exception as e:
                record("failed")
                why = scrub(f"{type(e).__name__}: {e}")
                events.step_end(
                    "injury_update", "failed", (utc_now() - started).total_seconds(), why
                )
                events.run_end(status="failed", exit_code=1, message=f"injury update failed: {why}")
                raise
            degraded = any("failed" in n or "incomplete" in n for n in res.notes)
            record("degraded" if degraded else "ok", res.finished)
            status = "degraded" if degraded else "ok"
            outcome = (
                "material change: addendum published"
                if res.material
                else "nothing material changed: no addendum published"
            )
            events.step_end(
                "injury_update", status, (res.finished - started).total_seconds(), outcome
            )
            events.run_end(status=status, exit_code=0, message=outcome, material=res.material)
    except LockHeld as e:
        console.print(f"[red]{escape(str(e))}[/]")
        raise typer.Exit(EXIT_LOCKED) from None
    except Exception as e:  # the update's own errors are plain sentences; others are scrubbed
        console.print(f"[red]injury update failed: {escape(scrub(f'{type(e).__name__}: {e}'))}[/]")
        raise typer.Exit(1) from None
    if res.material:
        console.print(f"[green]material change: addendum published -> {res.report_path}[/]")
    else:
        console.print("[green]nothing material changed: no addendum published[/]")
    console.print(f"Details: {res.summary_path}")
    if res.url:
        console.print(f"W&B: {res.url}")


def _week_range(text: str) -> list[int]:
    """`19-22` -> [19, 20, 21, 22]; `1` -> [1]; `19,21` -> [19, 21]."""
    weeks: list[int] = []
    for part in text.split(","):
        a, _, b = part.strip().partition("-")
        weeks += list(range(int(a), int(b or a) + 1))
    return weeks


@weekly_app.command("rehearse")
def weekly_rehearse(
    season: int = typer.Option(..., help="Season of the week(s) to rehearse."),
    weeks: str = typer.Option(
        ..., "--week", "--weeks", help="A week, a range or a list: 1, 19-22, 19,21."
    ),
    steps: str = typer.Option(
        "ready,game,player,digest", help="Steps to run (a subset of ready,game,player,digest)."
    ),
    llm: str = typer.Option("placeholder", "--llm", help="The digest writer (default: templates)."),
    out: str | None = typer.Option(
        None, help="Rehearsal folder (default: <data root>/rehearsals/<season>)."
    ),
    at: str | None = typer.Option(
        None,
        help="Pin the clock here (ISO; no offset = ET; one week only). Default: 10:00 ET on "
        "the Tuesday before the week's first kickoff.",
    ),
    fresh: bool = typer.Option(False, "--fresh", help="Wipe the rehearsal folder first."),
    app_run: str | None = APP_RUN,
    fail_at: str | None = typer.Option(None, "--fail-at", hidden=True),
) -> None:
    """Run the live Tuesday steps on a past or current week in a scratch copy (P10): the
    playoff check, the pre-season week-1 dry run. Reads the real data; writes only under the
    rehearsal folder; no W&B, no graph write, no run records. Exit 1 if a week failed, 4 if
    another rehearsal is using the folder."""
    from nflengine.ops.lock import LockHeld
    from nflengine.ops.rehearsal import run_rehearsal
    from nflengine.paths import DataRootError
    from nflengine.weekly import EXIT_LOCKED, EXIT_NO_DRIVE, escape, parse_as_of

    todo = _week_range(weeks)
    if at and len(todo) > 1:
        raise typer.BadParameter("--at pins one week; rehearse one week at a time with it")
    run_id = _app_run(app_run)
    if run_id and len(todo) > 1:
        raise typer.BadParameter("--app-run rehearses one week")
    failed = 0
    try:
        for i, wk in enumerate(todo):
            res = run_rehearsal(
                season,
                wk,
                steps=[s.strip() for s in steps.split(",") if s.strip()],
                llm=llm,
                root=Path(out) if out else None,
                at=parse_as_of(at) if at else None,
                fresh=fresh and i == 0,
                log=console.print,
                run_id=run_id,
                via="control-room" if run_id else None,
                fail_at=fail_at,
            )
            style = {"ok": "green", "degraded": "yellow"}.get(res.status, "red")
            detail = f" ({escape(res.error)})" if res.error else ""
            console.print(f"[{style}]rehearsal {season} week {wk:02d}: {res.status}{detail}[/]")
            if res.report:
                console.print(f"  digest: {res.report}")
            failed += res.status == "failed"
    except DataRootError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(EXIT_NO_DRIVE) from None
    except LockHeld as e:
        console.print(f"[red]{escape(str(e))}[/]")
        raise typer.Exit(EXIT_LOCKED) from None
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(2) from None
    if failed:
        raise typer.Exit(1)


# ---- season review (P10) --------------------------------------------------------------------

season_app = typer.Typer(
    help="The season so far and the end-of-season review (P10).",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
app.add_typer(season_app, name="season")


def _season_root(simulations: bool) -> Path | None:
    from nflengine.paths import ensure_data_root

    return ensure_data_root().runs / "digest-backtests" if simulations else None


@season_app.command("weeks")
def season_weeks(
    season: int = typer.Option(..., help="Season."),
    simulations: bool = typer.Option(
        False, "--simulations", help="Read the --as-of simulations' records instead."
    ),
) -> None:
    """One line per week (published, on time, checks, drift alerts): the PROGRESS season log."""
    from nflengine.ops.season_review import week_lines

    kind = "simulation" if simulations else "main"
    lines = week_lines(season, run_root=_season_root(simulations), kind=kind)
    for line in lines or [f"no weekly runs recorded for {season}"]:
        console.print(line, markup=False, soft_wrap=True)


@season_app.command("review")
def season_review(
    season: int = typer.Option(..., help="Season."),
    out: str | None = typer.Option(
        None,
        help="Output file (default: <data root>/reports/<season>/season-review.md); at the "
        "end of the season: documentation/reviews/<season>-season-review.md.",
    ),
    through_week: int | None = typer.Option(None, help="Stop at this graded week."),
    simulations: bool = typer.Option(
        False, "--simulations", help="Review the --as-of simulations' records instead."
    ),
) -> None:
    """The season review from the run records: game model vs Elo vs market, calibration, the
    accuracy scoreboard per target, the watch list, digest checks, pipeline uptime, best and
    worst calls, and an empty keep / cut / rebuild section for Rishi."""
    from nflengine.ops.season_review import write_review

    path = write_review(
        season,
        Path(out) if out else None,
        run_root=_season_root(simulations),
        through_week=through_week,
        kind="simulation" if simulations else "main",
    )
    console.print(f"[green]season review -> {path}[/]")


# ---- season dashboard (P07) -----------------------------------------------------------------

dashboard_app = typer.Typer(
    help="The W&B season dashboard (P07): one run per weekly run + a W&B Report.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
app.add_typer(dashboard_app, name="dashboard")


@dashboard_app.command("update")
def dashboard_update(
    season: int = typer.Option(..., help="Season."),
    week: int = typer.Option(..., help="The week of the newest weekly run."),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Log the season-to-date dashboard run (the weekly run does this by itself)."""
    from nflengine.ops.dashboard import log_season_dashboard

    url = log_season_dashboard(season, week, launched_by=launched_by, log=console.print)
    console.print(f"dashboard run: {url}" if url else "[yellow]dashboard run not logged[/]")


@dashboard_app.command("build")
def dashboard_build(season: int = typer.Option(..., help="Season.")) -> None:
    """Create (or update in place) the W&B Report "<season> Season Dashboard"."""
    from nflengine.ops.dashboard import build_report

    console.print(f"report: {build_report(season, log=console.print)}")


# ---- knowledge graph (P05) ---------------------------------------------------------------------

graph_app = typer.Typer(
    help="The Neo4j knowledge graph: weekly rebuild and the query library (P05).",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
app.add_typer(graph_app, name="graph")


@graph_app.command("build")
def graph_build(
    season: int = typer.Option(..., help="Season."),
    week: int = typer.Option(..., help="The week the graph is built for (as of its run)."),
    backtest: bool = typer.Option(
        False, "--backtest", help="Build as of that week's Tuesday (past weeks, leakage-free)."
    ),
    no_wandb: bool = typer.Option(False, "--no-wandb", help="Skip the W&B run."),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Wipe -> schema -> load -> model outputs -> queries -> graph_results.json."""
    from nflengine.graph.build import run_graph_build

    res = run_graph_build(
        season,
        week,
        mode="backtest" if backtest else "live",
        use_wandb=not no_wandb,
        launched_by=launched_by,
        fail_soft=True,  # errors are reported sanitized (type + reason), never raw driver text
        log=console.print,
    )
    if res.status != "ok":
        console.print(f"[red]graph build failed: {res.error}[/]")
        console.print(f"results: {res.results_path}")
        raise typer.Exit(2)
    table = Table(title=f"Graph {season} week {week:02d} ({res.key.mode})")
    table.add_column("label / type")
    table.add_column("count", justify="right")
    for k, v in res.counts.items():
        table.add_row(k, f"{v:,}")
    console.print(table)
    sel = res.selection
    for c in sel.picked if sel else []:
        console.print(f"[bold]{c.section}[/] {c.insight_type} ({c.strength:.2f}): {c.headline}")
    console.print(f"results: {res.results_path}")
    if res.url:
        console.print(f"W&B: {res.url}")


@graph_app.command("query")
def graph_query(
    name: str = typer.Argument(..., help="Query name, e.g. q2_injury_ripple."),
    season: int = typer.Option(..., help="Season."),
    week: int = typer.Option(..., help="Week."),
    limit: int = typer.Option(10, min=1, help="Rows to print."),
) -> None:
    """Run one library query against the current graph and print its rows (read-only)."""
    import json

    from nflengine.graph.client import get_driver
    from nflengine.graph.queries import run_query

    driver = get_driver()
    try:
        res = run_query(driver, name, season, week)
    finally:
        driver.close()
    if res.error:
        console.print(f"[red]{res.error}[/]")
        raise typer.Exit(1)
    console.print(f"{name}: {len(res.rows)} rows in {res.seconds:.2f} s")
    for row in res.rows[:limit]:
        console.print_json(json.dumps(row, default=str))


@graph_app.command("coaching-seed")
def graph_coaching_seed(
    first: int = typer.Option(2006, help="First season."),
    last: int | None = typer.Option(None, help="Last season (default: seasons.current)."),
    refresh: bool = typer.Option(
        False, "--refresh", help="Re-fetch the current season's staff pages (else cached)."
    ),
    out: str | None = typer.Option(None, help="Output CSV (default: config/coaching_seed.csv)."),
) -> None:
    """Build the coaching seed (coordinators per team-season) from Wikipedia (P08, D86)."""
    import datetime as dt
    from pathlib import Path

    import polars as pl

    from nflengine.graph import coaching_seed as CS
    from nflengine.graph.tables_extra import seed_path
    from nflengine.paths import ensure_data_root
    from nflengine.settings import get_config

    paths = ensure_data_root()
    current = int(get_config().seasons.current)
    last = last or current
    seed, stats = CS.build_seed(
        range(first, last + 1),
        current,
        paths.cache_http / "wikipedia_staff",
        refresh=refresh,
        log=console.print,
    )
    games = pl.read_parquet(paths.curated / "games.parquet", columns=["home_coach", "away_coach"])
    coaches = set(games["home_coach"].drop_nulls()) | set(games["away_coach"].drop_nulls())
    from nflengine.graph.tables_extra import read_coach_fixes

    coaches |= set(read_coach_fixes(log=console.print)["coach"])  # P10 corrections
    for name, known in CS.check_names(seed, coaches):
        console.print(f"[yellow]name check: '{name}' looks like nflverse's '{known}'[/]")
    target = Path(out) if out else seed_path()
    path = CS.write_seed(seed, target, built=dt.date.today().isoformat(), stats=stats)
    console.print(f"[green]coaching seed: {seed.height:,} rows -> {path} ({stats})[/]")


# --- live decisions (LD00+, documentation/live-decisions/): the 3rd / 4th-down bot ------------
live_app = typer.Typer(
    help="Live decisions: the 3rd- and 4th-down bot's models (LD00), the live feed (LD01+), "
    "the decision review (LD03).",
    no_args_is_help=True,
)
app.add_typer(live_app, name="live")


@live_app.command("train")
def live_train(
    seasons: str | None = typer.Option(None, help="Fit seasons (default: live.train_seasons)."),
    promote: bool = typer.Option(
        False, "--promote", help="Move live-decision-models' own `production` alias here."
    ),
    n_boot: int | None = typer.Option(None, help="Bootstrap refits (default: settings)."),
    no_wandb: bool = typer.Option(False, "--no-wandb", help="Don't log to W&B."),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Fit the six live-decision models, save them on D:, log `live-decision-models`."""
    from nflengine.live.train import run_train

    out = run_train(
        seasons=_parse_seasons(seasons),
        promote=promote,
        n_boot=n_boot,
        use_wandb=not no_wandb,
        launched_by=launched_by,
        log=console.print,
    )
    ok = out["checks"]["calls_ok"] and out["checks"]["timing_ok"]
    console.print(
        f"[{'green' if ok else 'yellow'}]{out['version']}: checks {'ok' if ok else 'FAILED'}[/]"
    )
    if out["url"]:
        console.print(f"W&B: {out['url']}")


@live_app.command("backtest")
def live_backtest(
    seasons: str | None = typer.Option(
        None, help="Held-out seasons (default: live.backtest_seasons)."
    ),
    ratings: bool = typer.Option(
        False, "--ratings", help="Research variant: the gain model with team ratings."
    ),
    smoke: bool = typer.Option(False, "--smoke", help="Two seasons, tagged smoke."),
    no_wandb: bool = typer.Option(False, "--no-wandb", help="Don't log to W&B."),
    save: bool = typer.Option(True, help="Save predictions + summary on D:."),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Leave-one-season-out backtest of every live-decision model (W&B `live-backtest`)."""
    from nflengine.live.backtest import run_backtest
    from nflengine.settings import get_config

    held = _parse_seasons(
        seasons or str((get_config().live or {}).get("backtest_seasons", "2014-2025"))
    )
    out = run_backtest(
        seasons=held,
        gain_ratings=ratings,
        use_wandb=not no_wandb,
        launched_by=launched_by,
        smoke=smoke,
        save=save,
        log=console.print,
    )
    ship = out.get("ship", {})
    console.print(f"ship rules: {ship}")


def _live_engine(version: str | None = None):
    """The LD00 decision engine on the production bundle (or a named model folder)."""
    from nflengine.live import models as LM
    from nflengine.live import train as LT
    from nflengine.live.decide import Engine, decide_settings

    try:
        models = LM.load(LT.root() / version) if version else LT.load_production()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from e
    return Engine(models, decide_settings())


@live_app.command("call")
def live_call(
    state: str | None = typer.Option(None, help="A game state as JSON (schema.GameState fields)."),
    event: str | None = typer.Option(
        None, "--event", help="An ESPN event id: fetch the game now and score its next snap."
    ),
    version: str | None = typer.Option(None, help="A model folder name (default: production)."),
    no_bootstrap: bool = typer.Option(False, "--no-bootstrap", help="Skip the confidence label."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON (with --event)."),
) -> None:
    """Score one state (`--state`) or a game right now (`--event`): a 4th-down call, or a
    3rd-down check with its if-stopped table."""
    import json as _json

    from nflengine.live.schema import GameState

    if (state is None) == (event is None):
        raise typer.BadParameter("give exactly one of --state or --event")
    if event is not None:
        _live_call_event(event, version, not no_bootstrap, json_out)
        return
    try:
        s = GameState.from_dict(_json.loads(state))
    except (ValueError, TypeError) as e:
        raise typer.BadParameter(str(e)) from e
    if s.down not in (3, 4):
        raise typer.BadParameter("`nfl live call` scores 3rd and 4th downs")
    eng = _live_engine(version)
    try:
        out = (eng.fourth_down if s.down == 4 else eng.third_down)(s, bootstrap=not no_bootstrap)
    finally:
        eng.close()
    console.print_json(_json.dumps(out.as_dict(), default=float))


def _live_call_event(event: str, version: str | None, bootstrap: bool, json_out: bool) -> None:
    import json as _json

    from rich.markup import escape

    from nflengine.live import show
    from nflengine.live.espn import EspnClient, EspnError, check_event_id
    from nflengine.live.state import competition, load_contexts, parse_event

    try:
        ev = check_event_id(event)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    with EspnClient() as client:
        try:
            f = client.game(ev)
        except EspnError as e:
            console.print(f"[red]{escape(str(e))}[/]")
            raise typer.Exit(1) from e
        season = int((f.data.get("season") or {}).get("year") or 0)
        try:
            ctx = load_contexts(season).context(ev, f.data) if season else None
        except FileNotFoundError:
            ctx = None
        if ctx is None:
            console.print(f"[red]event {ev} isn't a game in the curated schedule[/]")
            raise typer.Exit(1)
        comp = competition(f.data)
        status = ((comp.get("status") or {}).get("type") or {}).get("state")
        down = (comp.get("situation") or {}).get("down")
        period = int((comp.get("status") or {}).get("period") or 0)
        summary = None
        if status == "in" and (period <= 2 or down not in (1, 2, 3, 4)):
            # the opening kickoff (first half) or the next snap (no down on the scoreboard)
            try:
                s = client.summary(ev)
                summary = None if s.stale else s.data
            except EspnError:
                summary = None
        ls = parse_event(f.data, ctx, fetched_at=f.fetched_at, summary=summary)
    call = None
    if ls.is_decision:
        eng = _live_engine(version)
        try:
            st = ls.state
            call = (eng.fourth_down if st.down == 4 else eng.third_down)(st, bootstrap=bootstrap)
        finally:
            eng.close()
    if json_out:
        out = {
            "live": ls.as_dict(),
            "call": call.as_dict() if call is not None else None,
            "espn": {"stale": f.stale, "error": f.error, "age_s": round(f.age_s(), 1)},
        }
        console.print_json(_json.dumps(out, default=str))
        return
    if f.stale:
        console.print(
            f"[yellow]ESPN didn't answer ({escape(f.error or '')}); "
            f"showing its answer from {f.age_s():.0f} s ago[/]"
        )
    for line in show.header_lines(ls, f.age_s()):
        console.print(escape(line))
    for w in ls.warnings:
        console.print(f"[yellow]note: {escape(w)}[/]")
    if call is None:
        why = ls.reason or (f"down {ls.down}" if ls.down else "no down")
        console.print(f"[cyan]Not a 3rd or 4th down: {escape(why)}.[/]")
        return
    lines = (
        show.fourth_lines(call, ls.offense or "the offense")
        if ls.state.down == 4
        else show.third_lines(call, ls.offense or "", ls.defense or "")
    )
    for line in lines:
        console.print(escape(line))


@live_app.command("games")
def live_games(
    week: int | None = typer.Option(None, help="Week (default: ESPN's current week)."),
    season: int | None = typer.Option(None, help="Season (default: seasons.current)."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """This week's games from ESPN: status, score, clock, possession, down and distance."""
    import json as _json

    from rich.markup import escape

    from nflengine.live import show
    from nflengine.live.espn import EspnClient, EspnError
    from nflengine.live.state import load_contexts
    from nflengine.settings import get_config

    if week is not None and season is None:
        season = get_config().seasons.current
    with EspnClient() as client:
        try:
            f = client.scoreboard(season, week)
        except EspnError as e:
            console.print(f"[red]{escape(str(e))}[/]")
            raise typer.Exit(1) from e
    year = int((f.data.get("season") or {}).get("year") or season or 0)
    try:
        ctxs = load_contexts(year)
    except FileNotFoundError:
        ctxs = None
    ids = {}
    for e in f.data.get("events") or []:
        ids[str(e.get("id"))] = ctxs.game_id_for(str(e.get("id")), e) if ctxs else None
    rows = show.game_rows(f.data, ids)
    if json_out:
        console.print_json(_json.dumps(rows, default=str))
        return
    wk = (f.data.get("week") or {}).get("number")
    title = f"ESPN: {year} week {wk} (as of {show.local(f.fetched_at)})"
    table = Table(title=escape(title))
    cols = ("event", "game_id", "matchup", "status", "score", "ball", "situation")
    for col in ("event", "game", "matchup", "status", "score", "ball", "situation"):
        table.add_column(col)
    for r in rows:  # everything here came from ESPN: escaped, never Rich markup
        table.add_row(*(escape(str(r[c] if r[c] is not None else "?")) for c in cols))
    console.print(table)


@live_app.command("replay")
def live_replay(
    event: str = typer.Option(..., "--event", help="An ESPN event id (a finished game)."),
    downs: str = typer.Option("3,4", help="Which downs: 3,4 or 4."),
    refresh: bool = typer.Option(False, "--refresh", help="Ask ESPN again (no saved summary)."),
    bootstrap: bool = typer.Option(False, "--bootstrap", help="Add the confidence labels."),
    version: str | None = typer.Option(None, help="A model folder name (default: production)."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Every 3rd / 4th down of a game from ESPN's summary, the bot's call next to what
    happened (finished games' summaries are kept under live/summaries/)."""
    import json as _json

    from rich.markup import escape

    from nflengine.live import show
    from nflengine.live.espn import EspnClient, EspnError, check_event_id
    from nflengine.live.replay import decision_plays, load_summary, replay, summary_state
    from nflengine.live.state import load_contexts
    from nflengine.paths import ensure_data_root

    try:
        ev = check_event_id(event)
        want = [int(d) for d in downs.split(",")]
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    paths = ensure_data_root(create_dirs=False)
    with EspnClient() as client:
        try:
            summ, source = load_summary(ev, paths.live_data / "summaries", client, refresh)
        except EspnError as e:
            console.print(f"[red]{escape(str(e))}[/]")
            raise typer.Exit(1) from e
    header = summ.get("header") or {}
    season = int((header.get("season") or {}).get("year") or 0)
    ctx = load_contexts(season, paths).context(ev, header) if season else None
    if ctx is None:
        console.print(f"[red]event {ev} isn't a game in the curated schedule[/]")
        raise typer.Exit(1)
    plays = decision_plays(replay(summ, ctx), want)
    eng = _live_engine(version)
    rows = []
    try:
        for p in plays:
            call = (eng.fourth_down if p.down == 4 else eng.third_down)(
                p.state, bootstrap=bootstrap
            )
            rows.append((p, call, show.replay_row(p, call)))
    finally:
        eng.close()
    if json_out:
        out = [
            {"play": p.as_dict(), "call": c.as_dict(), "row": {k: v for k, v in r.items()}}
            for p, c, r in rows
        ]
        console.print_json(_json.dumps(out, default=str))
        return
    state = summary_state(summ)
    title = f"{ctx.away} at {ctx.home} ({ctx.game_id}; ESPN {state}, summary {source})"
    table = Table(title=escape(title))
    for col in ("play", "when", "team", "situation", "score", "bot", "WP go/FG/punt", "happened"):
        table.add_column(col)
    cols = ("play", "when", "offense", "situation", "score", "bot", "wp", "happened")
    for _, _, r in rows:
        agree = r.get("agree")
        mark = "" if agree is None else (" [green]=[/]" if agree else " [red]x[/]")
        cells = [escape(str(r[c])) for c in cols]
        cells[5] += mark
        table.add_row(*cells)
    console.print(table)
    fourth = [r for _, _, r in rows if r.get("agree") is not None]
    if fourth:
        same = sum(bool(r["agree"]) for r in fourth)
        console.print(f"4th downs: {len(fourth)}; the bot's call matched the coach's on {same}.")


@live_app.command("latency")
def live_latency(
    event: str = typer.Option(..., "--event", help="An ESPN event id (a game about to start)."),
    minutes: float = typer.Option(200.0, help="How long to watch (it stops after the final)."),
    poll: float = typer.Option(2.0, help="Seconds between checks (2 s is the minimum)."),
) -> None:
    """Measure ESPN's lag on a live game: log each play's first sighting against its snap
    time to live/latency/<event>.jsonl and print the median, p90 and worst case."""
    from rich.markup import escape

    from nflengine.live.espn import check_event_id
    from nflengine.live.latency import latency_path, run_latency
    from nflengine.paths import ensure_data_root

    try:
        ev = check_event_id(event)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    folder = ensure_data_root(create_dirs=False).live_data / "latency"
    console.print(f"watching {ev} for up to {minutes:g} min -> {latency_path(folder, ev)}")
    stats = run_latency(
        ev, minutes, folder, poll_s=max(2.0, poll), log=lambda m: console.print(escape(m))
    )
    console.print(stats)


@live_app.command("parity")
def live_parity(
    season: int = typer.Option(2026, help="Season."),
    weeks: str = typer.Option("1-5", help="Weeks, e.g. 1-5."),
    decisions: str | None = typer.Option(
        None, help="ESPN event ids (comma list): also score their 4th downs from both sources."
    ),
) -> None:
    """ESPN's replayed 3rd / 4th-down states vs nflverse play-by-play (LD01's parity check)."""
    from nflengine.live.parity import decision_parity, run_parity

    report = run_parity(season, _parse_seasons(weeks) or [], log=console.print)
    console.print({k: v for k, v in report.items() if not isinstance(v, list | dict)})
    if decisions:
        out = decision_parity(_csv(decisions) or [], log=console.print)
        console.print({k: v for k, v in out.items() if not isinstance(v, list | dict)})


@live_app.command("review")
def live_review(
    season: int = typer.Option(..., help="Season."),
    week: int | None = typer.Option(None, help="One finished week."),
    weeks: str | None = typer.Option(None, help="Several weeks, e.g. 1-4 (instead of --week)."),
    use_wandb: bool = typer.Option(
        False, "--wandb", help="Log one `decision-review` W&B run per week."
    ),
    smoke: bool = typer.Option(
        False, "--smoke", help="W&B: a logging check (group live-decisions-smoke, tag smoke)."
    ),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Review finished weeks' 4th downs: the bot's call against the coach's (LD03).

    Writes live/<S>/week<NN>/decision_review.* and live/<S>/season_review.* on D:. Not a
    weekly-run step: run it after Tuesday's weekly run for the week just played."""
    from rich.markup import escape
    from rich.table import Table

    from nflengine.live.review import run_review

    chosen = _parse_seasons(weeks) or ([week] if week is not None else None)
    if not chosen:
        console.print("[red]Give --week N or --weeks A-B.[/]")
        raise typer.Exit(2)
    try:
        out = run_review(
            season,
            chosen,
            use_wandb=use_wandb,
            launched_by=launched_by,
            log=console.print,
            smoke=smoke,
        )
    except FileNotFoundError as e:
        console.print(f"[red]{escape(str(e))}[/]")
        raise typer.Exit(1) from e
    if not out:
        console.print("[yellow]Nothing reviewed: no curated plays for those weeks yet.[/]")
        raise typer.Exit(3)
    last = out[-1]
    hl = last["payload"]["highlights"]
    for name in ("boldest", "costliest"):
        p = hl.get(name)
        if p:
            console.print(
                f"{name}: {escape(p['posteam'])} ({escape(p['coach'] or '?')}), Q{p['qtr']} "
                f"{p['clock']}, {escape(p['situation'])}: coach {p['choice']}, bot {p['best']} "
                f"({p['label']}), cost {100 * (p['cost'] or 0):.1f} pts, {escape(p['result'])}"
            )
    board = last["tables"]["leaderboard"]
    t = Table(title=f"{season} through week {last['week']}: go rate where the bot says go")
    for col in ("#", "coach", "team", "go spots", "went", "rate", "wins given up"):
        t.add_column(col)
    for r in board[:5] + board[-5:] if len(board) > 10 else board:
        t.add_row(
            str(r["rank"]), escape(r["coach"]), escape("/".join(r["teams"] or [])),
            str(r["go_spots"]), str(r["went_in_go_spots"]),
            "-" if r["go_rate_spots"] is None else f"{r['go_rate_spots']:.0%}",
            f"{r['wp_lost']:.2f}",
        )  # fmt: skip
    console.print(t)


# --- play calling (PC00+, documentation/play-calling/): team tendencies -----------------------
playcalling_app = typer.Typer(
    help="Play calling: enriched plays and team tendency tables, as of each week (PC00).",
    no_args_is_help=True,
)
app.add_typer(playcalling_app, name="playcalling")


@playcalling_app.command("build")
def playcalling_build(
    season: int = typer.Option(..., help="Season (e.g. 2026)."),
    through_week: int | None = typer.Option(
        None, help="Use games through this week only (as-of weeks up to W + 1)."
    ),
    history: str | None = typer.Option(
        None,
        help="Also build these seasons, e.g. 2016-2025, or `all` (playcalling.history_seasons).",
    ),
    no_wandb: bool = typer.Option(False, "--no-wandb", help="Don't log to W&B."),
    smoke: bool = typer.Option(
        False, "--smoke", help="W&B: a logging check (group play-calling-smoke, tag smoke)."
    ),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Build playcalling/<S>/ on D: (enriched plays, team / league tendencies, build.json).

    Not a weekly-run step (D107): run it after Tuesday's weekly run, and again on Wednesday
    after an FTN refresh for Monday night's game (runbook -> Play calling)."""
    from rich.markup import escape

    from nflengine.playcalling.build import run_build
    from nflengine.settings import get_config

    if history == "all":
        history = str(get_config().playcalling.get("history_seasons", "2016-2025"))
    try:
        out = run_build(
            season,
            through_week,
            _parse_seasons(history) or [],
            use_wandb=not no_wandb,
            launched_by=launched_by,
            log=console.print,
            smoke=smoke,
        )
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]{escape(str(e))}[/]")
        raise typer.Exit(1) from e
    t = Table(title="play-calling build")
    for col in ("season", "as-of weeks", "plays", "tendency rows", "FTN", "participation"):
        t.add_column(col)
    for r in out["seasons"]:
        w = r["as_of_weeks"]
        t.add_row(
            str(r["season"]), f"{w[0]}-{w[1]}" if w else "-",
            f"{r['rows']['plays_enriched']:,}", f"{r['rows']['team_tendencies']:,}",
            f"{r['coverage']['ftn_join_rate']:.1%}", f"{r['coverage']['part_join_rate']:.1%}",
        )  # fmt: skip
    console.print(t)
    missing = [g for r in out["seasons"] for g in r["coverage"]["games_without_ftn"]]
    if missing:
        console.print(f"[yellow]games without FTN yet: {escape(', '.join(missing[:12]))}[/]")
    if out["url"]:
        console.print(f"W&B: {out['url']}")


PLACEHOLDERS: dict[str, tuple[str, str]] = {}


def _register_placeholder(name: str, phase: str, summary: str) -> None:
    def placeholder() -> None:
        console.print(f"[yellow]`nfl {name}` is not implemented yet (planned in {phase}).[/]")
        raise typer.Exit(1)

    placeholder.__doc__ = f"{summary} [{phase}: not implemented yet]"
    app.command(name=name)(placeholder)


for _name, (_phase, _summary) in PLACEHOLDERS.items():
    _register_placeholder(_name, _phase, _summary)


if __name__ == "__main__":
    app()
