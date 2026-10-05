"""`nfl weekly run`: the weekly pipeline (P04; calendar, lock and run records in P07).

Named, idempotent steps, in order:

| step | what it does |
|---|---|
| ingest | pull every source into today's snapshots (`nfl ingest`) |
| ready | readiness check: every game of week N-1 is final and in play-by-play (exit 3 if not) |
| curate | rebuild curated tables + quality checks (`nfl curate`); a blocking check fails the run |
| ratings | team ratings, Elo, trends (`nfl ratings build`) |
| game | refit + predict week N (`nfl train game`) |
| graph | rebuild the Neo4j graph + query library (`nfl graph build`); fail-soft (P05) |
| player | score week N-1 (`nfl scoreboard`), project week N (`nfl train player`), graph |
| digest | payload -> LLM -> checks -> render (`nfl digest`) |

Ingest comes before the readiness check because the check reads the newest raw snapshots
(documentation/02 lists "readiness -> ingest"; D52). Each step records its status in
`runs/<season>/week<NN>/weekly_run.json`; `--from-step <name>` resumes from a failed step.
A fail-soft step that can't do its job raises `StepDegraded`: it is recorded as `degraded`
and the run goes on (the graph: Neo4j down -> the digest goes out without its graph
sections, with a banner; the player model: a failed refit -> the digest's watch list falls
back to the labelled P04 heuristic).

P07 (manual-first, D71) wraps the steps in `run_pipeline`: `--auto` works out the season and
week from the calendar (`ops/calendar.py`), skips a week already published, exits 3 ("not
ready") when week N-1 isn't final, holds a lock so two runs never overlap (exit 4), starts
Neo4j if it's down (`ops/services.py`), and after every run writes `run_summary.json`, a
`pipeline_history.parquet` row, the `weekly-pipeline` / `pipeline` W&B run, drift checks
and the season dashboard (`ops/summary.py`, `ops/drift.py`, `ops/dashboard.py`). With
`--as-of` in the past it simulates the run at that moment (readiness from kickoff + a data
lag, the digest as a backtest), which is how the retries and special weeks are proven.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nflengine.paths import ensure_data_root

STEPS = ("ingest", "ready", "curate", "ratings", "game", "graph", "player", "digest")


class StepFailed(RuntimeError):
    def __init__(self, step: str, detail: str, exit_code: int = 1):
        super().__init__(f"step {step!r} failed: {detail}")
        self.step, self.detail, self.exit_code = step, detail, exit_code


class StepDegraded(RuntimeError):
    """A fail-soft step couldn't do its job; the pipeline continues without it."""

    def __init__(self, step: str, detail: str):
        super().__init__(f"step {step!r} degraded: {detail}")
        self.step, self.detail = step, detail


@dataclass
class WeeklyOptions:
    season: int
    week: int
    launched_by: str | None = None
    promote: bool = False
    llm: str | None = None  # override llm.provider for the digest step (e.g. placeholder)
    as_of: dt.datetime | None = None  # simulations: the moment the run pretends to start
    kind: str = "main"  # main | simulation (records.KINDS)


def _ingest(o: WeeklyOptions, log: Callable[[str], None]) -> str:
    from nflengine.ingest.runner import run_ingest

    results, manifest = run_ingest(season=o.season, log=log)
    bad = [f"{r.source}/{r.dataset}" for r in results if r.status == "failed"]
    if any(r.status == "failed" and r.source == "nflverse" for r in results):
        raise StepFailed("ingest", f"nflverse failed: {bad}")
    return f"{len(results)} datasets ({len(bad)} optional failures); manifest {manifest}"


def _ready(o: WeeklyOptions, log: Callable[[str], None]) -> str:
    if o.week <= 1:
        return "week 1: nothing to wait for"
    from nflengine.curate.readiness import check_ready

    report = check_ready(o.season, o.week - 1)
    if not report.ready:
        raise StepFailed("ready", report.summary(), exit_code=3)
    return report.summary()


def _curate(o: WeeklyOptions, log: Callable[[str], None]) -> str:
    from nflengine.curate.build import build_all
    from nflengine.curate.quality import BLOCK, run_quality_checks

    built = build_all(log=log)
    blocked = [c.name for c in run_quality_checks() if not c.passed and c.level == BLOCK]
    if blocked:
        raise StepFailed("curate", f"blocking quality checks failed: {blocked}")
    return f"{len(built)} tables; quality checks pass"


def _ratings(o: WeeklyOptions, log: Callable[[str], None]) -> str:
    from nflengine.models.ratings_build import rating_params, run_build

    run_build(rating_params(), log=log, with_evidence=True)
    return "team_ratings, team_elo, team_trends, team_trend_drivers rebuilt"


def _game(o: WeeklyOptions, log: Callable[[str], None]) -> str:
    from nflengine.models.game_runs import run_train

    out = run_train(o.season, o.week, o.launched_by, promote=o.promote, log=log)
    return f"predictions {out['predictions']}; W&B {out['url']}; aliases {out['aliases']}"


def _graph(o: WeeklyOptions, log: Callable[[str], None]) -> str:
    from nflengine.graph.build import run_graph_build
    from nflengine.ops.services import ensure_neo4j
    from nflengine.settings import get_config

    ops = get_config().ops
    up = ensure_neo4j(
        log,
        float(ops.get("neo4j_start_timeout_s", 180)),
        float(ops.get("docker_start_timeout_s", 240)),
    )
    # always run the fail-soft build, even when the start failed: it records `unavailable`
    # in graph_results.json, so the digest never reuses an older ok file (Sol review)
    started = f"{up.detail}; " if up.action != "already_up" else ""
    res = run_graph_build(
        o.season, o.week, mode="live", launched_by=o.launched_by, fail_soft=True, log=log
    )
    if res.status != "ok":
        why = res.error if up.ok else f"{up.detail} ({res.error})"
        raise StepDegraded("graph", f"{why}; the digest skips its graph sections")
    return f"{started}{res.summary}; W&B {res.url}"


def _player(o: WeeklyOptions, log: Callable[[str], None]) -> str:
    """Score the season's saved projections (every earlier week: PFR pressures arrive a
    week late), refit + project week N, write the projections into the graph. Scoring and
    the graph write never stop the step; a failed refit degrades it and records it in
    `player_status.json`, so the digest uses the labelled heuristic watch list instead of
    an older projection file (Sol review)."""
    from nflengine.models.player_runs import run_train, score_weeks
    from nflengine.models.player_schema import write_status
    from nflengine.paths import ensure_data_root

    notes: list[str] = []
    if o.week > 1:
        try:
            board = score_weeks(o.season, range(1, o.week), o.launched_by, log=log)
            live = board.filter(board["mode"] == "live").height
            notes.append(f"scoreboard: {live} live rows through week {o.week - 1}")
        except Exception as e:  # scoring is bookkeeping: never block the projections
            notes.append(f"scoreboard skipped ({type(e).__name__})")
            log(f"[yellow]scoreboard skipped ({type(e).__name__})[/]")
    try:
        out = run_train(o.season, o.week, o.launched_by, promote=o.promote, log=log)
    except Exception as e:
        write_status(ensure_data_root().run_dir(o.season, o.week), "degraded", type(e).__name__)
        raise StepDegraded(
            "player", f"{type(e).__name__}; the digest uses the heuristic watch list"
        ) from e
    notes.append(f"{out['table'].height} projections -> {out['predictions']}; W&B {out['url']}")
    teams = _team_fit(o, log, notes)
    table = _consistency(o, out["table"], teams, Path(out["predictions"]), log, notes)
    try:
        from nflengine.graph.projections import write_projections

        res = write_projections(table, log=log)
        notes.append(f"graph: {getattr(res, 'summary', res)}")
    except Exception as e:  # the graph is optional (P05 fail-soft rule)
        notes.append(f"graph write skipped ({type(e).__name__})")
    return "; ".join(notes)


def _team_fit(o: WeeklyOptions, log: Callable[[str], None], notes: list[str]):
    """P08 team stat totals (the shipped `team_model.live_targets`): score the season's
    saved team projections, then refit + project week N into `predictions_teams.parquet`.
    Never stops the step (a failure is a note): the digest doesn't read team projections.
    Returns the week's team projections, or None."""
    try:
        from nflengine.models.team_model import live_team_targets

        if not live_team_targets():
            return None
        from nflengine.models.team_runs import run_train as team_train
        from nflengine.models.team_runs import score_weeks as team_score

        if o.week > 1:
            try:
                team_score(o.season, range(1, o.week), o.launched_by, log=log)
            except Exception as e:  # bookkeeping only
                notes.append(f"team scoreboard skipped ({type(e).__name__})")
        res = team_train(o.season, o.week, o.launched_by, promote=o.promote, log=log)
        notes.append(f"team: {res['table'].height} projections -> {res['predictions']}")
        return res["table"]
    except Exception as e:
        notes.append(f"team fit skipped ({type(e).__name__})")
        log(f"[yellow]team fit skipped ({type(e).__name__})[/]")
        return None


def _consistency(
    o: WeeklyOptions,
    players,
    teams,
    pred_path: Path,
    log: Callable[[str], None],
    notes: list[str],
):
    """P08 consistency layer (`models/consistency.py`, settings `consistency`) on the
    week's new projections (games not kicked off yet; saved rows of started games are never
    touched): receptions <= targets, optionally receiving yards toward the team's passing
    yards; the `inconsistency/*` numbers go to `consistency.json` (the run summary and the
    pipeline W&B run read it). The player file is rewritten only when a row changed.
    Fail-soft: on any error the projections stay as the refit wrote them."""
    import polars as pl

    from nflengine.ops.summary import CONSISTENCY_FILE
    from nflengine.settings import get_config

    try:
        from nflengine.models.consistency import apply_consistency

        cfg = get_config().consistency or {}
        now = dt.datetime.now(dt.UTC)
        fresh = players.filter(pl.col("kickoff_utc") > now)
        if fresh.is_empty():
            return players
        adj, summary = apply_consistency(
            fresh,
            teams,
            receptions=bool(cfg.get("receptions", True)),
            rec_yds_anchor=cfg.get("rec_yds_anchor"),
            strength=float(cfg.get("strength", 0.5)),
        )
        changed = int(
            summary.get("inconsistency/rec_gt_tgt_adjusted_rows", 0)
            + summary.get("inconsistency/rec_yds_adjusted_rows", 0)
        )
        table = players
        if changed:
            from nflengine.ops.lock import write_parquet_atomic

            adjusted = pl.concat(
                [players.filter(pl.col("kickoff_utc") <= now), adj.select(players.columns)],
                how="vertical_relaxed",
            )
            write_parquet_atomic(adjusted, pred_path, compression="zstd")
            table = adjusted  # committed: from here on the file holds the clamped rows
    except Exception as e:
        notes.append(f"consistency skipped ({type(e).__name__})")
        log(f"[yellow]consistency skipped ({type(e).__name__})[/]")
        return players
    # the record is bookkeeping: a failed write never undoes the committed table (the graph
    # and the digest must see the same projections; Sol review)
    gap = summary.get("inconsistency/gap_recv_qb_median_abs")
    note = f"consistency: {changed} rows adjusted" + (
        f", receivers vs QB median gap {100 * gap:.1f}%" if gap is not None else ""
    )
    try:
        record = {
            "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "adjusted_rows": changed,
            "rec_yds_anchor": cfg.get("rec_yds_anchor"),
            "summary": summary,
        }
        rec_path = pred_path.parent / CONSISTENCY_FILE
        tmp = rec_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, indent=2))
        tmp.replace(rec_path)
    except Exception as e:
        note += f" (record not written: {type(e).__name__})"
    notes.append(note)
    return table


def _digest(o: WeeklyOptions, log: Callable[[str], None]) -> str:
    from nflengine.digest.run import run_digest

    res = run_digest(
        o.season, o.week, mode="live", launched_by=o.launched_by, log=log, provider=o.llm
    )
    passed = res.synthesis.final.passed
    return f"{res.report_path} (checks {'passed' if passed else 'FAILED'}); W&B {res.url}"


STEP_FUNCS: dict[str, Callable[[WeeklyOptions, Callable[[str], None]], str]] = {
    "ingest": _ingest,
    "ready": _ready,
    "curate": _curate,
    "ratings": _ratings,
    "game": _game,
    "graph": _graph,
    "player": _player,
    "digest": _digest,
}


def read_state(path: Path) -> dict[str, Any] | None:
    """The week's `weekly_run.json`, or None if it's missing or unreadable."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_state(path: Path, state: dict[str, Any]) -> None:
    """Atomic: a crash mid-write never leaves a truncated state file."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def state_path(season: int, week: int) -> Path:
    return ensure_data_root().run_dir(season, week) / "weekly_run.json"


def scrub(text: Any, limit: int = 400) -> str:
    """`ops.summary.scrub` (imported lazily: the ops package isn't needed to run steps)."""
    from nflengine.ops.summary import scrub as _scrub

    return _scrub(text, limit)


def escape(text: str) -> str:
    """Escape rich markup in free text before printing it inside a style tag."""
    from rich.markup import escape as _escape

    return _escape(text)


def _now() -> str:
    """Local time with its UTC offset (P07: earlier files have naive local times)."""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def run_weekly(
    opts: WeeklyOptions,
    from_step: str | None = None,
    log: Callable[[str], None] = print,
    funcs: dict[str, Callable[[WeeklyOptions, Callable[[str], None]], str]] | None = None,
    state_file: Path | None = None,
    steps: tuple[str, ...] = STEPS,
) -> dict[str, Any]:
    """Run the steps from `from_step` (default: the first) to the end; stop at a failure."""
    funcs = funcs or STEP_FUNCS
    if from_step is not None and from_step not in steps:
        raise ValueError(f"unknown step {from_step!r}; steps: {', '.join(steps)}")
    path = state_file or state_path(opts.season, opts.week)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = read_state(path) if path.exists() else None
    if state is None and path.exists():  # unreadable (a crash mid-write before P07's fix)
        path.replace(path.with_suffix(".json.corrupt"))
        log(f"[yellow]{path.name} was unreadable: kept as .json.corrupt, starting fresh[/]")
    state = state or {"season": opts.season, "week": opts.week}
    state.setdefault("steps", {})
    state["last_started"] = _now()
    start = steps.index(from_step) if from_step else 0
    for name in steps[start:]:
        log(f"[bold]== {name} ==[/]")
        state["steps"][name] = {"status": "running", "started": _now()}
        _write_state(path, state)
        try:
            detail = funcs[name](opts, log)
        except StepDegraded as e:
            e.detail = scrub(e.detail)
            state["steps"][name].update(status="degraded", finished=_now(), detail=e.detail)
            _write_state(path, state)
            log(f"[yellow]{name}: degraded ({escape(e.detail)}); continuing[/]")
            continue
        except StepFailed as e:
            e.detail = scrub(e.detail)
            state["steps"][name].update(status="failed", finished=_now(), detail=e.detail)
            _write_state(path, state)
            raise
        except Exception as e:
            detail = scrub(f"{type(e).__name__}: {e}")
            state["steps"][name].update(status="failed", finished=_now(), detail=detail)
            _write_state(path, state)
            raise StepFailed(name, detail) from e
        state["steps"][name].update(status="ok", finished=_now(), detail=detail)
        _write_state(path, state)
        log(f"[green]{name}: {detail}[/]")
    state["last_finished"] = _now()
    _write_state(path, state)
    return state


# ---- P07: calendar-driven runs, simulations and run records -----------------------------------

SIM_STEPS = ("ready", "digest")
EXIT_OK, EXIT_FAILED, EXIT_NOT_READY, EXIT_LOCKED, EXIT_NO_DRIVE = 0, 1, 3, 4, 5


@dataclass
class PipelineOutcome:
    status: str  # records.RUN_STATUSES, plus "planned" (dry run) and "idle" (nothing to run)
    exit_code: int
    season: int | None
    week: int | None
    message: str
    plan: Any = None  # ops.calendar.WeekPlan
    summary_path: Path | None = None
    url: str | None = None
    alerts: list[Any] | None = None


def _sim_funcs(plan) -> dict[str, Callable[[WeeklyOptions, Callable[[str], None]], str]]:
    """Steps of a simulated run (`--as-of` in the past): readiness from the calendar's time
    travel (kickoff + data lag), then the week's digest as a backtest (as-of data, the
    canonical walk-forward predictions, the graph as of that moment; documentation/06)."""

    def digest(o: WeeklyOptions, log: Callable[[str], None]) -> str:
        from nflengine.digest.run import run_digest

        res = run_digest(
            o.season,
            o.week,
            mode="backtest",
            run_time=o.as_of,
            launched_by=o.launched_by,
            provider=o.llm or "placeholder",
            use_wandb=False,
            log=log,
            graph="off",  # a backtest graph build would wipe the live graph (review)
        )
        passed = res.synthesis.final.passed
        return f"{res.report_path} (checks {'passed' if passed else 'FAILED'})"

    return {"ready": _calendar_ready(plan), "digest": digest}


def _calendar_ready(plan) -> Callable[[WeeklyOptions, Callable[[str], None]], str]:
    """A `ready` step answered by the calendar: simulations (kickoff + data lag) and the live
    `--auto` pre-check (the freshly refreshed schedule still lacks week N-1 results), so a
    retry that can't succeed costs seconds and no full ingest (Odds API quota)."""

    def ready(o: WeeklyOptions, log: Callable[[str], None]) -> str:
        if plan.slate is None:  # e.g. the next playoff round isn't in the schedule yet
            raise StepFailed(
                "ready", f"{o.season} week {o.week}'s games aren't in the schedule yet", 3
            )
        when = o.as_of or plan.now
        how = "simulated" if plan.simulated else "schedule results"
        line = (
            f"{o.season} week {plan.previous_week}: {plan.previous_final}/"
            f"{plan.previous_games} games final as of {when:%Y-%m-%d %H:%M} UTC ({how})"
        )
        if not plan.previous_complete:
            raise StepFailed("ready", f"{line}; missing {list(plan.previous_missing)}", 3)
        return line

    return ready


def already_published(state_file: Path, report: Path | None) -> bool:
    """The week's digest step finished ok (and, live, its report file exists)."""
    if not state_file.exists():
        return False
    try:
        st = json.loads(state_file.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    ok = (st.get("steps", {}).get("digest") or {}).get("status") == "ok"
    return ok and (report is None or report.exists())


def refresh_schedule(season: int, log: Callable[[str], None] = print) -> None:
    """Re-ingest only nflverse's schedule (the calendar's input)."""
    from nflengine.ingest.runner import run_ingest

    run_ingest(season=season, sources=["nflverse"], nflverse_datasets=["schedules"], log=log)


def parse_as_of(text: str) -> dt.datetime:
    """`--as-of`: ISO date-time; without an offset it means US Eastern (the schedule's
    clock), e.g. `2025-11-04T10:00` = Tuesday 10:00 ET."""
    from nflengine.ops.calendar import EASTERN

    t = dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=EASTERN)
    return t.astimezone(dt.UTC)


def run_pipeline(
    season: int | None = None,
    week: int | None = None,
    *,
    auto: bool = False,
    as_of: dt.datetime | None = None,
    dry_run: bool = False,
    from_step: str | None = None,
    launched_by: str | None = None,
    promote: bool | None = None,
    llm: str | None = None,
    force: bool = False,
    use_wandb: bool = True,
    log: Callable[[str], None] = print,
    funcs: dict[str, Callable[[WeeklyOptions, Callable[[str], None]], str]] | None = None,
    schedules: Any = None,
    post_steps: bool = True,
    now: dt.datetime | None = None,
) -> PipelineOutcome:
    """One weekly run with the P07 wrapping (see the module docstring). Raises
    `DataRootError` if the data drive is missing (the CLI turns it into exit 5).

    Order: plan (read-only) → dry run returns here → **take the lock** → schedule refreshes
    (they write snapshots, so only under the lock) → resolve the week → already published?
    → steps → run records. Runs that stop before the steps (idle, already done, locked) write
    no records; everything else, a not-ready included, goes through `_post_run`."""
    from nflengine.ops.calendar import load_schedules, plan_week
    from nflengine.ops.lock import LockHeld, lock_path, run_lock
    from nflengine.ops.records import utc_now
    from nflengine.settings import get_config

    paths = ensure_data_root()  # DataRootError: stop before writing anything
    cfg = get_config()
    ops = cfg.ops
    real_now = now or utc_now()  # `now`: tests pin the clock
    if as_of is not None and as_of > real_now + dt.timedelta(minutes=5):
        raise ValueError("--as-of can't be in the future")
    simulate = as_of is not None and as_of < real_now - dt.timedelta(hours=1)
    when = as_of or real_now
    schedules = schedules if schedules is not None else load_schedules(paths)
    lag = float(ops.get("data_lag_hours", 10)) if simulate else None
    plan = plan_week(schedules, when, season=None if auto else season, data_lag_hours=lag)
    if dry_run:  # read-only: no refresh, no lock
        res = _resolve(plan, schedules, auto, season, week, when, lag, simulate, cfg, log)
        if isinstance(res, PipelineOutcome):
            return res
        plan, season, week = res
        steps = SIM_STEPS if simulate else STEPS
        start = steps.index(from_step) if from_step else 0
        ready = plan.previous_complete and plan.slate is not None
        msg = f"dry run: would run {', '.join(steps[start:])} for {season} week {week:02d}" + (
            "" if ready else f"; week {plan.previous_week} isn't final yet (exit 3)"
        )
        log(msg)
        code = EXIT_OK if ready else EXIT_NOT_READY
        return PipelineOutcome("planned", code, season, week, msg, plan)

    if auto:
        command = "nfl weekly run --auto" + (f" --as-of {as_of.isoformat()}" if simulate else "")
    else:
        command = f"nfl weekly run --season {season} --week {week}"
    lock = run_lock(lock_path(paths.runs), command)
    try:
        held = lock.__enter__()
    except LockHeld as e:
        msg = str(e)
        log(f"[red]{escape(msg)}[/]")
        return PipelineOutcome("locked", EXIT_LOCKED, season, week, msg, plan)
    try:
        if held.took_over is not None:
            log(f"[yellow]a run that ended without cleaning up left: {held.took_over}[/]")
        live_auto = auto and not simulate
        if live_auto and plan.week is not None and plan.slate is None:
            # e.g. Sunday night after week 18: the wild-card games aren't in our schedule
            # snapshot yet. Refresh just the schedule (seconds), then look again.
            log("the week's games aren't in the schedule snapshot: refreshing the schedule ...")
            plan, schedules = _refresh_and_replan(plan, schedules, paths, when, None, log)
        res = _resolve(plan, schedules, auto, season, week, when, lag, simulate, cfg, log)
        if isinstance(res, PipelineOutcome):
            return res
        plan, season, week = res
        kind = "simulation" if simulate else "main"
        run_root = paths.runs / "digest-backtests" if simulate else paths.runs
        run_dir = run_root / str(season) / f"week{week:02d}"
        state_file = run_dir / "weekly_run.json"
        report = None if simulate else paths.report_path(season, week)
        if auto and from_step is None and not force and already_published(state_file, report):
            msg = f"{season} week {week:02d} is already published; nothing to do"
            log(f"[green]{msg}[/]")
            return PipelineOutcome("already_done", EXIT_OK, season, week, msg, plan)
        steps = SIM_STEPS if simulate else STEPS
        precheck = plan.slate is None  # the week isn't scheduled: a recorded not-ready
        if live_auto and from_step is None and not precheck and not plan.previous_complete:
            log("the schedule says last week isn't final: refreshing only the schedule ...")
            plan, schedules = _refresh_and_replan(plan, schedules, paths, when, season, log)
            precheck = not plan.previous_complete  # still not final: no full ingest
        if precheck:
            steps = ("ready",)
        if promote is None:
            promote = bool(auto and ops.get("promote_auto", True))
        opts = WeeklyOptions(
            season, week, launched_by, promote, llm, as_of=as_of if simulate else None, kind=kind
        )
        started = utc_now()
        status, code, error = "ok", EXIT_OK, None
        state: dict[str, Any] | None = None
        try:
            state = run_weekly(
                opts,
                from_step=from_step,
                log=log,
                funcs={
                    **(_sim_funcs(plan) if simulate else STEP_FUNCS),
                    **(funcs or {}),
                    # the pre-check's answer wins: the week isn't final / scheduled
                    **({"ready": _calendar_ready(plan)} if precheck else {}),
                },
                state_file=state_file,
                steps=steps,
            )
        except StepFailed as e:
            status = "not_ready" if e.exit_code == EXIT_NOT_READY else "failed"
            code, error = e.exit_code, e.detail
        except Exception as e:  # outside any step (state file, bad options)
            status, code, error = "failed", EXIT_FAILED, scrub(f"{type(e).__name__}: {e}")
        if state is None:
            state = read_state(state_file)  # None if missing or unreadable (Sol 7)
        if status == "ok" and state is not None:
            degraded = [
                n
                for n, s in state.get("steps", {}).items()
                if s.get("status") == "degraded" and _ran_since(s, started)
            ]
            status = "degraded" if degraded else "ok"
        outcome = PipelineOutcome(
            status, code, season, week, error or f"{season} week {week:02d}: {status}", plan
        )
        if post_steps:
            try:
                _post_run(
                    outcome,
                    paths=paths,
                    kind=kind,
                    run_root=run_root,
                    run_dir=run_dir,
                    state=state,
                    started=started,
                    now=when,
                    launched_by=launched_by,
                    as_of=as_of if simulate else None,
                    from_step=from_step,
                    error=error,
                    took_over=held.took_over,
                    use_wandb=use_wandb,
                    log=log,
                )
            except Exception as e:  # the records never change the run's result
                log(f"[yellow]run records incomplete ({type(e).__name__})[/]")
        return outcome
    finally:
        lock.__exit__(None, None, None)


def _refresh_and_replan(plan, schedules, paths, when, season, log):
    """Refresh only nflverse's schedule (under the lock), then plan again. On a failed
    refresh the old schedule and plan stand (the not-ready path covers it)."""
    from nflengine.ops.calendar import load_schedules, plan_week

    try:
        refresh_schedule(plan.season, log)
        schedules = load_schedules(paths)
        plan = plan_week(schedules, when, season=season)
    except Exception as e:
        log(f"[yellow]schedule refresh failed ({type(e).__name__})[/]")
    return plan, schedules


def _resolve(plan, schedules, auto, season, week, when, lag, simulate, cfg, log):
    """(plan, season, week) for the run, or an `idle` outcome (offseason). The plan is
    re-made for the requested week when a manual run asks for another one."""
    from nflengine.ops.calendar import plan_for

    if auto:
        season, week = plan.season, plan.week
        if week is None:
            for line in plan.describe():
                log(line)
            return PipelineOutcome("idle", EXIT_OK, season, None, "offseason: nothing to run", plan)
        if not simulate and season != cfg.seasons.current:
            raise ValueError(
                f"the calendar says season {season} but settings.yaml has seasons.current "
                f"{cfg.seasons.current}: update it (P10 pre-season) before running --auto"
            )
    if season is None or week is None:
        raise ValueError("give --season and --week, or --auto")
    if plan.week != week or plan.season != season:
        log(f"[yellow]note: the calendar's week is {plan.week}; running week {week} as asked[/]")
        plan = plan_for(schedules, season, week, when, data_lag_hours=lag)
    for line in plan.describe():
        log(line)
    return plan, season, week


def _ran_since(step: dict, started: dt.datetime) -> bool:
    try:
        t = dt.datetime.fromisoformat(step["started"])
    except (KeyError, TypeError, ValueError):
        return False
    if t.tzinfo is None:  # files from before P07 hold naive local times
        t = t.astimezone()
    return t >= started - dt.timedelta(seconds=1)


def _post_run(
    outcome: PipelineOutcome,
    *,
    paths,
    kind: str,
    run_root: Path,
    run_dir: Path,
    state: dict | None,
    started: dt.datetime,
    now: dt.datetime,
    launched_by: str | None,
    as_of: dt.datetime | None,
    from_step: str | None,
    error: str | None,
    took_over: dict | None,
    use_wandb: bool,
    log: Callable[[str], None],
) -> None:
    """Run records after every run: summary, history, drift, alerts, W&B, dashboard. Each
    part fails soft and never changes the run's result."""
    import polars as pl

    from nflengine.ops.records import Alert, append_history, history_path, read_history, utc_now
    from nflengine.ops.summary import (
        build_summary,
        freshness,
        history_row,
        log_pipeline_run,
        write_summary,
    )
    from nflengine.settings import get_config

    cfg = get_config()
    season, week, plan = outcome.season, outcome.week, outcome.plan
    tag = f"{season} week {week}"
    alerts: list[Any] = []
    fresh = None
    if kind == "main":
        try:
            fresh = freshness(paths, season, week, now, int(cfg.drift.get("stale_days", 7)))
        except Exception as e:
            log(f"[yellow]freshness skipped ({type(e).__name__})[/]")
    hist_file = history_path(run_root, season)
    finished = utc_now()
    common = dict(
        kind=kind,
        season=season,
        week=week,
        status=outcome.status,
        exit_code=outcome.exit_code,
        started=started,
        finished=finished,
        deadline_clock=(as_of + (finished - started)) if as_of else None,
        run_dir=run_dir,
        paths=paths,
        plan=plan,
        state=state,
        launched_by=launched_by,
        as_of=as_of,
        from_step=from_step,
        error=error,
        fresh=fresh,
    )
    draft = build_summary(**common)
    drift: list[Any] = []
    try:
        from nflengine.ops.drift import drift_alerts, evaluate_drift
        from nflengine.ops.records import HISTORY_SCHEMA

        current = pl.DataFrame([history_row(draft)], schema=HISTORY_SCHEMA)
        history = pl.concat([read_history(hist_file).cast(HISTORY_SCHEMA), current])
        if kind == "simulation":  # a simulated season's runs play the part of the main runs
            history = history.with_columns(kind=pl.lit("main"))
        drift = evaluate_drift(
            season,
            week,
            run_root=run_root,
            paths=paths,
            freshness=fresh,
            history=history,
        )
        alerts += drift_alerts(drift, season, week)
    except Exception as e:  # drift is advice: never fail the run over it
        log(f"[yellow]drift checks skipped ({type(e).__name__})[/]")
    ran = {n: s for n, s in (state or {}).get("steps", {}).items() if _ran_since(s, started)}
    if outcome.status == "failed":
        step = next((n for n, s in ran.items() if s.get("status") == "failed"), None)
        where = f" at step {step}" if step else ""
        alerts.append(
            Alert(
                "error",
                f"{tag}: weekly run failed{where}",
                f"The run stopped{where}; nothing was published. Fix the cause, then resume "
                f"with --from-step {step or 'ingest'} (documentation/runbook.md).",
                f"step:{step or 'pipeline'}",
            )
        )
    elif (
        outcome.status == "not_ready"
        and plan is not None
        and plan.past_retry_window
        and plan.slate is None
    ):
        alerts.append(
            Alert(
                "error",
                f"{tag}: games still not in the schedule",
                f"nflverse hasn't added week {week}'s games (the next playoff round) to the "
                "schedule and the retry window has ended. Check nflverse's status "
                "(documentation/runbook.md).",
                "readiness",
            )
        )
    elif outcome.status == "not_ready" and plan is not None and plan.past_retry_window:
        alerts.append(
            Alert(
                "error",
                f"{tag}: week {plan.previous_week} data still missing",
                "nflverse still hasn't published every game of the previous week and the "
                "retry window has ended (Wednesday 18:00 ET, or the first kickoff if earlier). "
                "The digest can't go out until the week is final (documentation/runbook.md).",
                "readiness",
            )
        )
    for n, s in ran.items():
        if s.get("status") == "degraded":
            alerts.append(
                Alert("warn", f"{tag}: {n} step degraded", str(s.get("detail")), f"step:{n}")
            )
    if took_over is not None:
        alerts.append(Alert("info", f"{tag}: stale lock taken over", str(took_over), "lock"))
    d = draft.get("digest") or {}
    if d and not d.get("checks_passed"):
        failed = ", ".join(d.get("failed_checks") or [])
        alerts.append(
            Alert(
                "warn",
                f"{tag}: digest checks failed",
                f"The digest went out with the warning banner after one regeneration ({failed}).",
                "step:digest",
            )
        )
    for a in alerts:  # one place: everything below prints or stores scrubbed text
        a.title, a.text = scrub(a.title, 160), scrub(a.text, 800)
    summary = build_summary(**common, drift=drift, alerts=alerts)
    try:
        outcome.summary_path = write_summary(run_dir, summary)
        append_history(hist_file, history_row(summary))
    except Exception as e:
        log(f"[yellow]run summary not written ({type(e).__name__})[/]")
    if use_wandb:
        outcome.url = log_pipeline_run(
            summary, alerts_enabled=bool(cfg.ops.get("wandb_alerts", True)), log=log
        )
        if outcome.url:
            log(f"W&B pipeline run: {outcome.url}")
        if kind == "main" and summary.get("published"):
            try:
                from nflengine.ops.dashboard import (
                    dashboard_file,
                    log_season_dashboard,
                    read_dashboard_info,
                )

                newest = read_dashboard_info(dashboard_file(paths, season)).get("last_run_week")
                if newest is not None and week < int(newest):
                    log(f"season dashboard left on week {newest} (this run is week {week})")
                else:
                    url = log_season_dashboard(season, week, launched_by=launched_by, log=log)
                    if url:
                        log(f"W&B season dashboard run: {url}")
            except Exception as e:
                log(f"[yellow]season dashboard skipped ({type(e).__name__})[/]")
    outcome.alerts = alerts
    for a in alerts:
        style = {"error": "red", "warn": "yellow"}.get(a.level, "cyan")
        log(f"[{style}]ALERT ({a.level}) {escape(a.title)}: {escape(a.text)}[/]")
