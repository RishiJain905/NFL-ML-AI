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


PLACEHOLDERS = {
    "features": ("P03", "Build game-model feature tables with as-of guarantees."),
    "train": ("P03", "Fit models for a given week."),
    "backtest": ("P03", "Walk-forward backtests logged to W&B."),
    "graph": ("P05", "Rebuild the Neo4j graph and run the query library."),
    "digest": ("P04", "Build the payload and write the weekly digest."),
    "weekly": ("P04", "Run the full weekly pipeline."),
}


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
