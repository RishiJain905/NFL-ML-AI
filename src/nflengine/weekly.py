"""`nfl weekly run`: the manual weekly pipeline (plan P04; scheduled in P07).

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

    res = run_graph_build(
        o.season, o.week, mode="live", launched_by=o.launched_by, fail_soft=True, log=log
    )
    if res.status != "ok":
        raise StepDegraded("graph", f"{res.error}; the digest skips its graph sections")
    return f"{res.summary}; W&B {res.url}"


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
            log(f"[yellow]scoreboard skipped: {type(e).__name__}: {e}[/]")
    try:
        out = run_train(o.season, o.week, o.launched_by, promote=o.promote, log=log)
    except Exception as e:
        write_status(ensure_data_root().run_dir(o.season, o.week), "degraded", type(e).__name__)
        raise StepDegraded(
            "player", f"{type(e).__name__}: {e}; the digest uses the heuristic watch list"
        ) from e
    notes.append(f"{out['table'].height} projections -> {out['predictions']}; W&B {out['url']}")
    try:
        from nflengine.graph.projections import write_projections

        res = write_projections(out["table"], log=log)
        notes.append(f"graph: {getattr(res, 'summary', res)}")
    except Exception as e:  # the graph is optional (P05 fail-soft rule)
        notes.append(f"graph write skipped ({type(e).__name__})")
    return "; ".join(notes)


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


def state_path(season: int, week: int) -> Path:
    return ensure_data_root().run_dir(season, week) / "weekly_run.json"


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def run_weekly(
    opts: WeeklyOptions,
    from_step: str | None = None,
    log: Callable[[str], None] = print,
    funcs: dict[str, Callable[[WeeklyOptions, Callable[[str], None]], str]] | None = None,
    state_file: Path | None = None,
) -> dict[str, Any]:
    """Run the steps from `from_step` (default: the first) to the end; stop at a failure."""
    funcs = funcs or STEP_FUNCS
    if from_step is not None and from_step not in STEPS:
        raise ValueError(f"unknown step {from_step!r}; steps: {', '.join(STEPS)}")
    path = state_file or state_path(opts.season, opts.week)
    path.parent.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = (
        json.loads(path.read_text())
        if path.exists()
        else {"season": opts.season, "week": opts.week}
    )
    state.setdefault("steps", {})
    state["last_started"] = _now()
    start = STEPS.index(from_step) if from_step else 0
    for name in STEPS[start:]:
        log(f"[bold]== {name} ==[/]")
        state["steps"][name] = {"status": "running", "started": _now()}
        path.write_text(json.dumps(state, indent=2))
        try:
            detail = funcs[name](opts, log)
        except StepDegraded as e:
            state["steps"][name].update(status="degraded", finished=_now(), detail=e.detail)
            path.write_text(json.dumps(state, indent=2))
            log(f"[yellow]{name}: degraded ({e.detail}); continuing[/]")
            continue
        except StepFailed as e:
            state["steps"][name].update(status="failed", finished=_now(), detail=e.detail)
            path.write_text(json.dumps(state, indent=2))
            raise
        except Exception as e:
            state["steps"][name].update(
                status="failed", finished=_now(), detail=f"{type(e).__name__}: {e}"
            )
            path.write_text(json.dumps(state, indent=2))
            raise StepFailed(name, f"{type(e).__name__}: {e}") from e
        state["steps"][name].update(status="ok", finished=_now(), detail=detail)
        path.write_text(json.dumps(state, indent=2))
        log(f"[green]{name}: {detail}[/]")
    state["last_finished"] = _now()
    path.write_text(json.dumps(state, indent=2))
    return state
