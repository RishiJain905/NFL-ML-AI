"""`nfl` command-line entry point (documentation/02 -> Components).

P00 provides `doctor` and `wandb-smoke`; the other commands are placeholders
that later phases fill in.
"""

from __future__ import annotations

import math
import random
import time

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
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Walk-forward backtest of the game model vs home / Elo / market baselines."""
    from nflengine.models.backtest import SampleWeights
    from nflengine.models.game_runs import run_backtest

    weights = SampleWeights.for_current(current_weight) if current_weight else None
    res = run_backtest(
        _variant(variant),
        _parse_seasons(seasons),
        launched_by,
        log=console.print,
        qb_mode=qb_mode,
        weights=weights,
        win_method=win_method,
        calibration=calibration,
    )
    s = res.summary
    table = Table(title=f"backtest {variant} (pooled, {int(s['games'])} games)")
    for col in ("predictor", "brier", "log loss", "accuracy", "ECE"):
        table.add_column(col)
    for name in ("model", "elo", "market", "home"):
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
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """Refit on everything before week N, predict week N, save predictions_games.parquet."""
    from nflengine.models.game_runs import run_train

    out = run_train(season, week, launched_by, promote=promote, log=console.print)
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
    help="The weekly pipeline (manual in P04, scheduled in P07).",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
app.add_typer(weekly_app, name="weekly")


@weekly_app.command("run")
def weekly_run(
    season: int = typer.Option(..., help="Season."),
    week: int = typer.Option(..., help="The week to preview (week N; week N-1 must be done)."),
    from_step: str | None = typer.Option(
        None,
        help="Resume from this step: ingest | ready | curate | ratings | game | graph | digest.",
    ),
    promote: bool = typer.Option(
        False, "--promote", help="Give this week's game-model artifact the `production` alias."
    ),
    llm: str | None = typer.Option(
        None, "--llm", help="Override llm.provider for the digest step (e.g. placeholder)."
    ),
    launched_by: str | None = LAUNCHED_BY,
) -> None:
    """ingest -> readiness -> curate -> ratings -> game model -> graph -> digest (resumable)."""
    from nflengine.weekly import StepFailed, WeeklyOptions, run_weekly, state_path

    opts = WeeklyOptions(season, week, launched_by=launched_by, promote=promote, llm=llm)
    try:
        run_weekly(opts, from_step=from_step, log=console.print)
    except StepFailed as e:
        console.print(f"[red]{e}[/]")
        console.print(
            f"State: {state_path(season, week)}. Fix it, then resume with "
            f"`nfl weekly run --season {season} --week {week} --from-step {e.step}`."
        )
        raise typer.Exit(e.exit_code) from None
    console.print(f"[green]Weekly run for {season} week {week:02d} finished.[/]")


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
