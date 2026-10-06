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
        help="The Run button (CR02) runs `nfl weekly rehearse` instead of the live week.",
    ),
) -> None:
    """Open the control room: a local web app for the weekly pipeline (127.0.0.1 only)."""
    from nflengine.app.serve import serve

    raise typer.Exit(
        serve(port=port, open_browser=not no_browser, dev=dev, rehearsal=rehearsal, log=print)
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
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """ingest -> readiness -> curate -> ratings -> game -> graph -> player -> digest
    (resumable), then the run summary, drift checks and dashboard. Exit codes: 0 done
    (or nothing to do), 1 failed, 2 usage error, 3 not ready (retry later), 4 another run
    holds the lock, 5 data drive missing."""
    from nflengine.paths import DataRootError
    from nflengine.weekly import EXIT_NO_DRIVE, escape, parse_as_of, run_pipeline

    if not auto and (season is None or week is None):
        raise typer.BadParameter("give --season and --week, or --auto")
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
        )
    except DataRootError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(EXIT_NO_DRIVE) from None
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None
    style = {"ok": "green", "already_done": "green", "idle": "green", "planned": "cyan"}
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
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Saturday injury update: re-ingest injuries, news and lines, re-predict the week, and
    publish a short addendum only if something material changed (documentation/06)."""
    from nflengine.ops.calendar import load_schedules, plan_week
    from nflengine.ops.injury_update import run_injury_update
    from nflengine.ops.lock import LockHeld, lock_path, run_lock
    from nflengine.ops.records import append_history, history_path, iso, utc_now
    from nflengine.paths import DataRootError, ensure_data_root
    from nflengine.weekly import EXIT_LOCKED, EXIT_NO_DRIVE, escape, scrub

    try:
        paths = ensure_data_root()
    except DataRootError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(EXIT_NO_DRIVE) from None
    if auto:
        plan = plan_week(load_schedules(paths))
        season, week = plan.season, plan.week
        if week is None:
            console.print("offseason: nothing to update")
            raise typer.Exit(0)
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

    try:
        with run_lock(
            lock_path(paths.runs), f"nfl weekly injury-update --season {season} --week {week}"
        ):
            try:
                res = run_injury_update(
                    season,
                    week,
                    launched_by=launched_by,
                    log=console.print,
                    use_wandb=not no_wandb,
                    ingest=not no_ingest,
                    players=not no_players,
                )
            except Exception:
                record("failed")
                raise
            degraded = any("failed" in n or "incomplete" in n for n in res.notes)
            record("degraded" if degraded else "ok", res.finished)
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
) -> None:
    """Run the live Tuesday steps on a past or current week in a scratch copy (P10): the
    playoff check, the pre-season week-1 dry run. Reads the real data; writes only under the
    rehearsal folder; no W&B, no graph write, no run records. Exit 1 if a week failed."""
    from nflengine.ops.rehearsal import run_rehearsal
    from nflengine.paths import DataRootError
    from nflengine.weekly import EXIT_NO_DRIVE, escape, parse_as_of

    todo = _week_range(weeks)
    if at and len(todo) > 1:
        raise typer.BadParameter("--at pins one week; rehearse one week at a time with it")
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
