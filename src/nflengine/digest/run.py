"""`nfl digest`: payload -> LLM -> checks -> render -> D: (documentation/06; plan P04).

Two modes share one code path:
- **live**: reads this week's `predictions_games.parquet` from `nfl train game` in
  `runs/<season>/week<NN>/`, grades last week's saved files, writes the digest to
  `reports/<season>/week<NN>-digest.md` and logs a `weekly-pipeline` / `main` W&B run.
- **backtest**: produces a past week as if live on that week's Tuesday. The week's game
  predictions come from the canonical walk-forward backtests
  (`runs/backtests/game/{market,model_only}/`), materialized into
  `runs/digest-backtests/<season>/week<NN>/` for the week and every earlier week of the
  season, so the report card reads "saved" files exactly as it does live. Output goes to
  `reports/backtests/<season>/week<NN>-digest.md`; W&B group `digest-dev`, job `backtest`.

Each run folder keeps `payload.json`, `raw_llm_output.json`, `checks.json`, `digest.md` and
`watchlist.parquet` (next week's report card grades it), plus the season's
`season_scorecard.parquet` one level up.

Graph sections (P05): `digest/graph_sections.py` reads (or builds, fail-soft) the week's
`graph_results.json`; the picks fill *Matchup / risk to watch* and *Non-obvious insights*
and are added to the published-insight log after the digest is written.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.digest import build
from nflengine.digest import format as F
from nflengine.digest.checks import Lexicon
from nflengine.digest.facts import build_fact_index
from nflengine.digest.graph_sections import (
    GraphState,
    fixed_graph_sections,
    graph_state,
    record_published,
)
from nflengine.digest.llm import get_llm
from nflengine.digest.llm.base import LLMClient
from nflengine.digest.payload import Meta, Payload
from nflengine.digest.prompt import load_prompt
from nflengine.digest.render import FooterInfo, render_digest
from nflengine.digest.report_card import (
    PRED_FILE,
    WATCH_FILE,
    ReportCardResult,
    build_report_card,
    week_dir,
)
from nflengine.digest.synthesize import Synthesis, synthesize
from nflengine.paths import DataPaths, ensure_data_root
from nflengine.settings import get_config, load_followed_teams

MODES = ("live", "backtest")
BACKTEST_LABELS = {"market": "market", "model_only": "model_only"}
EARLY_WEEKS = 3
SCORECARD_SCHEMA = {
    "season": pl.Int32,
    "week": pl.Int32,
    "games": pl.Int32,
    "picks_correct": pl.Int32,
    "picks_total": pl.Int32,
    "brier_model": pl.Float64,
    "brier_elo": pl.Float64,
    "brier_market": pl.Float64,
    "logloss_model": pl.Float64,
    "ece_model": pl.Float64,
    "points_mae": pl.Float64,
    "watchlist_hits": pl.Int32,
    "watchlist_total": pl.Int32,
    "player_mae_vs_baseline": pl.Float64,
    "checks_passed": pl.Boolean,
    "not_graded": pl.Int32,
}


# ---- context ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class DigestContext:
    season: int
    week: int
    mode: str
    run_time: dt.datetime  # tz-aware UTC
    paths: DataPaths
    writer: str = "placeholder"  # the configured LLM provider (names backtest reports)

    @property
    def run_root(self) -> Path:
        return self.paths.runs if self.mode == "live" else self.paths.runs / "digest-backtests"

    @property
    def run_dir(self) -> Path:
        return week_dir(self.run_root, self.season, self.week)

    @property
    def report_path(self) -> Path:
        if self.mode == "live":
            return self.paths.report_path(self.season, self.week)
        # one file per writer, so placeholder and real-LLM backtests sit side by side
        name = f"week{self.week:02d}-digest-{self.writer}.md"
        return self.paths.reports / "backtests" / str(self.season) / name

    @property
    def scorecard_path(self) -> Path:
        return self.run_root / str(self.season) / "season_scorecard.parquet"


def tuesday_before(games: pl.DataFrame, season: int, week: int) -> dt.datetime:
    """14:00 UTC (10:00 Eastern) on the Tuesday before the week's first kickoff."""
    wk = games.filter((pl.col("season") == season) & (pl.col("week") == week))
    if wk.is_empty():
        raise ValueError(f"no games for {season} week {week}")
    first = wk["kickoff_utc"].min()
    day = (first - dt.timedelta(hours=5)).date()  # US Eastern calendar day, near enough
    tuesday = day - dt.timedelta(days=(day.weekday() - 1) % 7)
    return dt.datetime.combine(tuesday, dt.time(14, 0), tzinfo=dt.UTC)


def make_context(
    season: int,
    week: int,
    mode: str = "live",
    run_time: dt.datetime | None = None,
    paths: DataPaths | None = None,
    games: pl.DataFrame | None = None,
    writer: str = "placeholder",
) -> DigestContext:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    paths = paths or ensure_data_root()
    if run_time is None:
        if mode == "live":
            run_time = dt.datetime.now(dt.UTC).replace(microsecond=0)
        else:
            games = games if games is not None else read_games(paths)
            run_time = tuesday_before(games, season, week)
    if run_time.tzinfo is None:
        run_time = run_time.replace(tzinfo=dt.UTC)
    return DigestContext(season, week, mode, run_time, paths, writer)


def read_games(paths: DataPaths) -> pl.DataFrame:
    return pl.read_parquet(paths.curated / "games.parquet")


# ---- backtest predictions ---------------------------------------------------------------------


def materialize_backtest_predictions(
    ctx: DigestContext, games: pl.DataFrame, overwrite: bool = False
) -> list[Path]:
    """Write `predictions_games.parquet` for weeks 1..N of the backtest season from the
    canonical walk-forward backtests, stamped with each week's Tuesday run time."""
    from nflengine.models.game_runs import FAMILY, MODEL_VERSION, assemble_predictions

    base = ctx.paths.runs / "backtests" / "game"
    preds = {
        v: pl.read_parquet(base / label / PRED_FILE).filter(pl.col("season") == ctx.season)
        for v, label in BACKTEST_LABELS.items()
    }
    hashes = {}
    for v, label in BACKTEST_LABELS.items():
        summary = base / label / "summary.json"
        cfg = json.loads(summary.read_text()).get("config", {}) if summary.exists() else {}
        hashes[v] = str(cfg.get("feature_hash", "unknown"))
    feats = pl.read_parquet(ctx.paths.features / "game_features.parquet").filter(
        pl.col("season") == ctx.season
    )
    written = []
    for w in range(1, ctx.week + 1):
        out = week_dir(ctx.run_root, ctx.season, w) / PRED_FILE
        if out.exists() and not overwrite and w != ctx.week:
            continue
        week_preds = {v: p.filter(pl.col("week") == w) for v, p in preds.items()}
        if week_preds["model_only"].is_empty():
            continue
        frame_week = feats.filter(pl.col("week") == w)
        table = assemble_predictions(
            week_preds,
            frame_week,
            f"{FAMILY}-{MODEL_VERSION}:backtest",
            hashes,
            f"{ctx.season}-w{w - 1:02d}",
        )
        stamp = tuesday_before(games, ctx.season, w)
        table = table.with_columns(pl.lit(stamp).dt.cast_time_unit("us").alias("created_at"))
        out.parent.mkdir(parents=True, exist_ok=True)
        table.write_parquet(out, compression="zstd")
        written.append(out)
    return written


# ---- inputs -----------------------------------------------------------------------------------


def _feature(paths: DataPaths, name: str, season: int, week: int) -> pl.DataFrame:
    path = paths.features / f"{name}.parquet"
    if not path.exists():
        return pl.DataFrame()
    return pl.read_parquet(path).filter((pl.col("season") == season) & (pl.col("week") == week))


def lexicon(paths: DataPaths, season: int) -> Lexicon:
    path = paths.curated / "rosters_weekly.parquet"
    if not path.exists():
        return Lexicon()
    names = (
        pl.scan_parquet(path)
        .filter(pl.col("season").is_in([season - 1, season]))
        .select("full_name")
        .unique()
        .collect()["full_name"]
        .drop_nulls()
        .to_list()
    )
    return Lexicon(set(names))


def _injury_snapshot(paths: DataPaths) -> str | None:
    from nflengine.ingest.base import SnapshotStore

    store = SnapshotStore(paths.raw)
    dates = store.snapshots("espn", "injuries") or store.snapshots("nflverse", "injuries")
    return dates[-1] if dates else None


def _espn_team_map(paths: DataPaths) -> dict[str, str]:
    path = paths.curated / "espn_injuries.parquet"
    if not path.exists():
        return {}
    df = pl.read_parquet(path, columns=["team_espn_id", "team"]).drop_nulls().unique()
    return dict(zip(df["team_espn_id"].to_list(), df["team"].to_list(), strict=False))


def _sources(ctx: DigestContext, log: Callable[[str], None]) -> dict[str, str]:
    from nflengine.digest.under_hood import source_weeks
    from nflengine.tracking import dataset_version

    prev = ctx.week - 1
    out: dict[str, str] = {}
    if ctx.mode == "live":
        dv = dataset_version(ctx.paths)
        if dv.get("pbp_snapshot"):
            out["play-by-play snapshot"] = str(dv["pbp_snapshot"])
    try:
        weeks = source_weeks(ctx.season, ctx.paths)
    except Exception as e:  # fail soft: the footer just says less
        log(f"[yellow]source freshness unavailable: {type(e).__name__}[/]")
        weeks = {}
    for src, wk in weeks.items():
        shown = min(wk, prev) if wk is not None else None
        out[src] = f"through week {shown}" if shown else "no weeks yet"
    return out


# ---- payload ----------------------------------------------------------------------------------


@dataclass
class Built:
    payload: Payload
    report: ReportCardResult
    watch: pl.DataFrame
    preds: pl.DataFrame


def build_payload(
    ctx: DigestContext,
    games: pl.DataFrame,
    log: Callable[[str], None] = print,
    graph: GraphState | None = None,
) -> Built:
    from nflengine.digest.under_hood import select_under_hood
    from nflengine.digest.watchlist import placeholder_watchlist, score_watchlist

    s, w, paths = ctx.season, ctx.week, ctx.paths
    followed = load_followed_teams()
    pred_path = ctx.run_dir / PRED_FILE
    if not pred_path.exists():
        raise FileNotFoundError(
            f"no game predictions for {s} week {w} at {pred_path}: "
            f"run `nfl train game --season {s} --week {w}` first"
        )
    preds = pl.read_parquet(pred_path)

    prev_checks = week_dir(ctx.run_root, s, w - 1) / "checks.json"
    prev_passed = (
        json.loads(prev_checks.read_text()).get("passed") if prev_checks.exists() else None
    )
    report = build_report_card(
        ctx.run_root,
        s,
        w,
        games,
        score_watch=lambda df: score_watchlist(df, paths),
        checks_passed=prev_passed,
    )
    game_items = build.build_games(preds, ctx.run_time)
    trends = build.build_trends(
        _feature(paths, "team_trends", s, w), _feature(paths, "team_trend_drivers", s, w), followed
    )
    uh = select_under_hood(s, w, paths, followed=followed)
    watch = placeholder_watchlist(
        s,
        w,
        paths,
        n_items=int(get_config().digest.get("watchlist_size") or 8),
        followed=followed,
        injury_week=w if ctx.mode == "live" else None,
    )
    watch = stamp_watch(watch, games, ctx.run_time)
    # only picks whose game hasn't started: a Saturday run must not "predict" Thursday
    watch = watch.filter(pl.col("kickoff_utc").is_null() | (pl.col("kickoff_utc") > ctx.run_time))
    upcoming = [g for g in game_items if g.status == "upcoming"]
    started = [g.matchup for g in game_items if g.status == "started"]
    teams = {g.home for g in game_items} | {g.away for g in game_items}
    news: list = []
    if ctx.mode == "live":
        try:
            news_df = pl.read_parquet(paths.curated / "espn_news.parquet")
            news = build.build_news(news_df, ctx.run_time, _espn_team_map(paths), teams)
        except Exception as e:  # ESPN is optional: fail soft
            log(f"[yellow]news skipped: {type(e).__name__}[/]")
    primary = preds.filter(pl.col("is_primary"))
    graph = graph or GraphState("off")
    meta = Meta(
        season=s,
        week=w,
        run_id=f"{s}-w{w:02d}-{ctx.mode}",
        mode=ctx.mode,  # type: ignore[arg-type]
        run_time=ctx.run_time.isoformat(),
        season_display=F.count(s),
        week_display=F.count(w),
        prev_week_display=F.count(max(w - 1, 0)),
        data_through=f"{s}-W{w - 1:02d}" if w > 1 else f"{s - 1} season",
        injury_snapshot=_injury_snapshot(paths) if ctx.mode == "live" else None,
        market_data_used=bool((primary["variant"] == "market").any()),
        model_versions={
            "game model": str(primary["model_version"][0]) if primary.height else "n/a",
            "players to watch": "heuristic-v0 (until P06)",
        },
        sources=_sources(ctx, log),
        followed_teams=followed,
        early_season=w <= EARLY_WEEKS,
        graph_status=graph.status,  # type: ignore[arg-type]
        graph_note=graph.note,
    )
    payload = Payload(
        meta=meta,
        report_card=report.card,
        games=upcoming,
        started_games=started,
        game_highlights=build.build_highlights(upcoming),
        team_trends=trends,
        under_the_hood=build.build_under_hood(uh),
        players_to_watch=build.build_watch(watch),
        graph_insights=graph.picked,
        qb_changes=graph.qb_changes,
        starters_out=graph.starters_out,
        graph_more=graph.more,
        news=news,
    )
    return Built(payload, report, watch, preds)


def fixed_sections(payload: Payload) -> dict[str, str]:
    """Sections code writes itself. A report card with nothing to grade is one fixed
    sentence: GLM padded it with an invented flourish in the first live digest."""
    rc = payload.report_card
    if rc.status != "scored" and rc.note:
        return {"report_card": f"Report card: {rc.note}"}
    return {}


# ---- watch list timestamps ----------------------------------------------------------------------


def stamp_watch(watch: pl.DataFrame, games: pl.DataFrame, run_time: dt.datetime) -> pl.DataFrame:
    """Add `created_at` (the run time) and the game's `kickoff_utc`, so the report card
    grades only picks made before kickoff (Sol review)."""
    ko = games.select("game_id", "kickoff_utc")
    out = (
        watch.join(ko, on="game_id", how="left")
        if watch.height
        else watch.with_columns(pl.lit(None, pl.Datetime("us", "UTC")).alias("kickoff_utc"))
    )
    return out.with_columns(
        pl.lit(run_time).cast(pl.Datetime("us", "UTC")).alias("created_at"),
        pl.col("kickoff_utc").cast(pl.Datetime("us", "UTC")),
    )


def save_watch(watch: pl.DataFrame, path: Path, run_time: dt.datetime) -> pl.DataFrame:
    """Write this run's watch list, keeping earlier saved picks whose game has already
    started and that were made before kickoff (a re-run can't destroy gradable picks)."""
    out = watch
    if path.exists():
        old = pl.read_parquet(path)
        if {"created_at", "kickoff_utc"} <= set(old.columns):
            keep = old.filter(
                (pl.col("kickoff_utc") <= run_time)
                & (pl.col("created_at") < pl.col("kickoff_utc"))
                & ~pl.col("player_id").is_in(watch["player_id"].to_list())
            )
            if keep.height:
                out = pl.concat([keep, watch], how="diagonal_relaxed")
    out.write_parquet(path)
    return out


# ---- scorecard --------------------------------------------------------------------------------


def update_scorecard(path: Path, row: dict[str, Any] | None) -> pl.DataFrame:
    """Upsert one (season, week) row into the season scorecard on D:."""
    old = pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=SCORECARD_SCHEMA)
    if row is None:
        return old
    new = pl.DataFrame([{k: row.get(k) for k in SCORECARD_SCHEMA}], schema=SCORECARD_SCHEMA)
    old = old.filter(~((pl.col("season") == row["season"]) & (pl.col("week") == row["week"])))
    out = pl.concat([old.cast(SCORECARD_SCHEMA), new]).sort("season", "week")  # type: ignore[arg-type]
    path.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(path)
    return out


# ---- the run ----------------------------------------------------------------------------------


@dataclass
class DigestResult:
    ctx: DigestContext
    payload: Payload
    synthesis: Synthesis
    digest_path: Path
    report_path: Path
    scorecard: pl.DataFrame
    url: str | None


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def run_digest(
    season: int,
    week: int,
    *,
    mode: str = "live",
    run_time: dt.datetime | None = None,
    launched_by: str | None = None,
    llm: LLMClient | None = None,
    provider: str | None = None,
    use_wandb: bool = True,
    log: Callable[[str], None] = print,
    graph: str = "auto",
) -> DigestResult:
    """`provider` overrides `llm.provider` (e.g. "placeholder" for a side-by-side backtest).
    `graph`: auto | build | read | off (see `digest/graph_sections.py`)."""
    paths = ensure_data_root()
    games = read_games(paths)
    llm = llm or get_llm(provider)
    ctx = make_context(season, week, mode, run_time, paths, games, writer=llm.name)
    if mode == "backtest":
        written = materialize_backtest_predictions(ctx, games)
        log(f"backtest predictions ready ({len(written)} week files written)")
    state = graph_state(
        ctx,
        games,
        graph,
        use_wandb=use_wandb and mode == "live",
        launched_by=launched_by,
        log=log,
    )
    if state.status == "unavailable":
        log(f"[yellow]graph sections skipped: {state.note}[/]")
    log(f"building the payload for {season} week {week} ({mode}, as of {ctx.run_time}) ...")
    built = build_payload(ctx, games, log, graph=state)
    payload = built.payload
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    (ctx.run_dir / "payload.json").write_text(payload.to_json(), encoding="utf-8")

    cfg = get_config()
    budgets = {k: int(v) for k, v in (cfg.digest.get("word_budgets") or {}).items()}
    prompt = load_prompt()
    fallback = get_llm("placeholder") if llm.name != "placeholder" else None
    facts = build_fact_index(payload)
    log(f"writing the prose with {llm.name} ({llm.model}) ...")
    synth = synthesize(
        payload,
        facts,
        llm,
        prompt,
        budgets,
        lexicon(paths, season),
        fallback=fallback,
        fixed={**fixed_sections(payload), **fixed_graph_sections(state)},
        enabled_phases=state.enabled_phases,
    )
    for note in synth.fallback_notes:
        log(f"[yellow]{note}[/]")
    calls = list(getattr(llm, "calls", []))
    _write_json(
        ctx.run_dir / "raw_llm_output.json",
        {
            "provider": llm.name,
            "model": llm.model,
            "writers": synth.writers,
            "fallback_notes": synth.fallback_notes,
            "calls": calls,
            "attempts": synth.attempts,
        },
    )
    _write_json(ctx.run_dir / "checks.json", synth.checks_json())
    final_writer = synth.final_writer
    info = FooterInfo(
        checks_passed=synth.final.passed,
        failed_checks=synth.final.failed,
        warnings=synth.final.warnings,
        regenerated=synth.regenerated,
        prompt_hash=prompt.hash,
        llm_name=final_writer,
        llm_model=llm.model if final_writer == llm.name else "templates (fallback)",
        fallback=bool(synth.fallback_notes) and final_writer != llm.name,
    )
    md = render_digest(payload, synth.sections, info, synth.banner)
    digest_path = ctx.run_dir / "digest.md"
    digest_path.write_text(md, encoding="utf-8")
    ctx.report_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.report_path.write_text(md, encoding="utf-8")
    save_watch(built.watch, ctx.run_dir / WATCH_FILE, ctx.run_time)
    record_published(ctx, state, log, synth.sections)
    scorecard = update_scorecard(ctx.scorecard_path, built.report.scorecard_row)
    log(f"digest -> {ctx.report_path} (checks {'passed' if synth.final.passed else 'FAILED'})")

    url = None
    if use_wandb:
        url = log_digest_run(ctx, payload, synth, prompt.hash, llm, scorecard, launched_by, calls)
        log(f"W&B: {url}")
    return DigestResult(ctx, payload, synth, digest_path, ctx.report_path, scorecard, url)


def season_series(scorecard: pl.DataFrame) -> list[dict[str, float]]:
    """One point per graded week: weekly and cumulative Brier (model / Elo / market), pick
    accuracy, points error and watch-list hit rate (the doc 08 season-dashboard curves)."""
    points: list[dict[str, float]] = []
    picks = correct = w_hits = w_total = 0
    sums = {"brier_model": 0.0, "brier_elo": 0.0, "brier_market": 0.0}
    # games behind each metric: a week without a market Brier must not add to its divisor
    covered = dict.fromkeys(sums, 0)
    for r in scorecard.sort("week").iter_rows(named=True):
        n = int(r["games"] or 0)
        if not n:
            continue
        picks += int(r["picks_total"] or 0)
        correct += int(r["picks_correct"] or 0)
        w_hits += int(r["watchlist_hits"] or 0)
        w_total += int(r["watchlist_total"] or 0)
        p: dict[str, float] = {"season/week": int(r["week"])}
        for k in sums:
            if r[k] is not None:
                sums[k] += float(r[k]) * n
                covered[k] += n
                p[f"season/{k}"] = float(r[k])
                p[f"season/cum_{k}"] = sums[k] / covered[k]
        if r["picks_total"]:
            p["season/pick_accuracy"] = r["picks_correct"] / r["picks_total"]
        if picks:
            p["season/cum_pick_accuracy"] = correct / picks
        if r["points_mae"] is not None:
            p["season/points_mae"] = float(r["points_mae"])
        if r["watchlist_total"]:
            p["season/watchlist_hit_rate"] = r["watchlist_hits"] / r["watchlist_total"]
        if w_total:
            p["season/cum_watchlist_hit_rate"] = w_hits / w_total
        points.append(p)
    return points


def log_digest_charts(run, scorecard: pl.DataFrame, word_counts: dict, results: list) -> None:
    """Chart panels for a digest run (one-shot, so nothing else draws charts): the season so
    far as `season/*` lines over graded weeks (standard panels: they render on mobile too),
    plus bar charts of words per section and issues per check."""
    import wandb

    run.define_metric("season/week")
    run.define_metric("season/*", step_metric="season/week")
    for point in season_series(scorecard):
        run.log(point)
    budgets = get_config().digest.get("word_budgets") or {}
    words = wandb.Table(
        data=[[s, int(n), int(budgets.get(s, 0))] for s, n in word_counts.items()],
        columns=["section", "words", "budget"],
    )
    issues = wandb.Table(
        data=[[r.name, len(r.issues)] for r in results], columns=["check", "issues"]
    )
    run.log(
        {
            "words_per_section": wandb.plot.bar(words, "section", "words", title="Words"),
            "check_issue_counts": wandb.plot.bar(issues, "check", "issues", title="Check issues"),
        }
    )


def log_digest_run(
    ctx: DigestContext,
    payload: Payload,
    synth: Synthesis,
    prompt_hash: str,
    llm: LLMClient,
    scorecard: pl.DataFrame,
    launched_by: str | None,
    calls: list[dict[str, Any]] | None = None,
) -> str | None:
    """One W&B run per digest (documentation/08 -> What each weekly pipeline run logs)."""
    import wandb

    from nflengine.tracking import dataset_version, git_commit, init_run

    live = ctx.mode == "live"
    tag = f"{ctx.season}-w{ctx.week:02d}"
    run = init_run(
        "weekly-pipeline" if live else "digest-dev",
        "main" if live else "backtest",
        config={
            "season": ctx.season,
            "week": ctx.week,
            "mode": ctx.mode,
            "run_time": ctx.run_time.isoformat(),
            "prompt_hash": prompt_hash,
            "llm_provider": llm.name,
            "llm_model": llm.model,
            "word_budgets": get_config().digest.get("word_budgets"),
            "followed_teams": payload.meta.followed_teams,
            "git_commit": git_commit(),
            "dataset_version": dataset_version(ctx.paths),
        },
        tags=[
            "p05",
            "digest",
            f"season:{ctx.season}",
            f"week:{ctx.week:02d}",
            f"llm:{llm.name}",
            f"graph:{payload.meta.graph_status}",
        ]
        + (["prod"] if live else ["backtest"]),
        launched_by=launched_by,
        name=f"digest-{tag}" + ("" if live else f"-backtest-{llm.name}"),
    )
    try:
        final = synth.final
        summary: dict[str, Any] = {
            "checks_passed": final.passed,
            "checks_failed": ",".join(final.failed),
            "checks_warnings": ",".join(final.warnings),
            "regenerated": synth.regenerated,
            "banner": synth.banner is not None,
            "market_data_used": payload.meta.market_data_used,
            "graph_status": payload.meta.graph_status,
            "graph_items": len(payload.graph_insights),
            "graph_more_items": len(payload.graph_more),
            "qb_changes": len(payload.qb_changes),
            "starters_out": len(payload.starters_out),
            "games": len(payload.games),
            "team_trends": len(payload.team_trends),
            "under_the_hood_items": len(payload.under_the_hood),
            "watchlist_items": len(payload.players_to_watch),
            "news_items": len(payload.news),
            "words_total": sum(final.word_counts.values()),
            "report_card_status": payload.report_card.status,
            "llm/final_writer": synth.final_writer,
            "llm/fallback": bool(synth.fallback_notes),
        }
        calls = calls or []
        if calls:
            num = lambda k: sum(float(c.get(k) or 0) for c in calls)  # noqa: E731
            summary.update(
                {
                    "llm/calls": len(calls),
                    "llm/latency_s": num("latency_s"),
                    "llm/prompt_tokens": num("prompt_tokens"),
                    "llm/completion_tokens": num("completion_tokens"),
                    "llm/reasoning_tokens": num("reasoning_tokens"),
                    "llm/cost_usd": num("cost"),
                    "llm/providers": ",".join(str(c.get("provider")) for c in calls),
                }
            )
        summary.update({f"words/{k}": v for k, v in final.word_counts.items()})
        for r in final.results:
            summary[f"check/{r.name}"] = int(r.passed)
            summary[f"check_issues/{r.name}"] = len(r.issues)
        rc = payload.report_card
        if rc.status == "scored":
            summary.update(
                {
                    "rc/picks_correct": rc.picks_correct.value,
                    "rc/picks_total": rc.picks_total.value,
                    "rc/brier_model": rc.brier.value,
                    "rc/brier_elo": rc.brier_elo.value,
                    "rc/points_mae": rc.points_mae.value,
                }
            )
        run.summary.update(summary)
        log_digest_charts(run, scorecard, final.word_counts, final.results)
        issues = [
            [r.name, r.level, i.section, i.token, i.reason, i.sentence[:200]]
            for r in final.results
            for i in r.issues
        ]
        run.log(
            {
                "season_scorecard": wandb.Table(dataframe=scorecard.to_pandas()),
                "check_issues": wandb.Table(
                    columns=["check", "level", "section", "token", "reason", "sentence"],
                    data=issues,
                ),
                "game_outlook": wandb.Table(
                    columns=["game", "away %", "home %", "score", "margin", "call"],
                    data=[
                        [
                            f"{g.away} @ {g.home}",
                            g.away_win_prob.display,
                            g.home_win_prob.display,
                            f"{g.predicted_score.away.display}-{g.predicted_score.home.display}",
                            g.expected_margin.display,
                            g.confidence,
                        ]
                        for g in payload.games
                    ],
                ),
            }
        )
        art = wandb.Artifact(
            "digest" if live else "digest-backtest",
            type="digest",
            description=f"{ctx.season} week {ctx.week} digest ({ctx.mode})",
            metadata={"prompt_hash": prompt_hash, "checks_passed": final.passed},
        )
        for name in ("payload.json", "raw_llm_output.json", "checks.json", "digest.md"):
            art.add_file(str(ctx.run_dir / name))
        run.log_artifact(art, aliases=[tag if live else f"{tag}-{llm.name}"])
        url = run.url
    finally:
        run.finish()
    return url
