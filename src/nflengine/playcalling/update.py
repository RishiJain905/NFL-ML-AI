"""The play-calling update: everything due after a weekly run, in one command (D132).

`nfl playcalling update --auto` (the control room's **Update play calling** button on the Play
calls tab) works out what is due for the calendar's week W and runs only that, in order:

1. `ftn`: Monday night's FTN charting (`nfl ingest --sources nflverse --datasets ftn_charting`,
   then the curated tables and their quality checks), when an earlier game the tables still
   list without FTN kicked off at least `FTN_DUE_HOURS` ago (FTN lands 35-51 h after a game);
2. `build`: the play-calling tables (`nfl playcalling build`) when they don't reach week W yet,
   the curated plays are newer than the last build, or FTN was just refreshed;
3. `review`: the 4th-down decision review of week W-1 (`nfl live review --wandb`) when it
   hasn't been written yet;
4. `grade`: week W-1's play-calling forecast (`nfl playcalling grade`) when it has a forecast
   and isn't fully graded (rows waiting for FTN, a re-made forecast, or new tables);
5. `forecast`: week W's forecast (`nfl playcalling forecast`) when there's none yet, or when
   the tables are newer and none of the week's games has kicked off.

Tuesday after the weekly run that is usually build + review + grade + forecast; Wednesday
afternoon ftn + build + grade. It holds the weekly-run lock (it can re-curate), so it never
overlaps a weekly run, a resume or an injury update. Not a weekly-run step (D107).

`plan_update` only reads files (the app's pre-flight calls it); `run_update` does the work.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.paths import DataPaths
from nflengine.playcalling import build as B

STEPS = ("ftn", "build", "review", "grade", "forecast")
FTN_DUE_HOURS = 30  # FTN lands 35-51 h after kickoff (PC00); try from 30 h on
FTN_GIVE_UP_HOURS = 7 * 24  # still missing a week after kickoff: FTN won't chart it
WAITING = ("waiting_ftn", "waiting_build", "not_played")
GAMES_SCHEMA: dict[str, Any] = {
    "game_id": pl.Utf8,
    "season": pl.Int64,
    "week": pl.Int64,
    "away_team": pl.Utf8,
    "home_team": pl.Utf8,
    "kickoff_utc": pl.Datetime("us", "UTC"),
    "result": pl.Float64,
}


@dataclass(frozen=True)
class Step:
    step: str  # one of STEPS
    label: str  # "Grade week 5's forecast"
    due: bool
    reason: str  # why it's due, or why it's skipped
    week: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- reading what's there (no writes) -------------------------------------------------------
def _json(path: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _time(text: Any) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def _mtime(path: Path) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC)
    except OSError:
        return None


def _games(paths: DataPaths, season: int) -> pl.DataFrame:
    """The season's games (week, kickoff, teams, result); empty without a curated table."""
    f = paths.curated / "games.parquet"
    empty = pl.DataFrame(schema=GAMES_SCHEMA)  # typed, so the plan's selects and filters work
    try:
        import pyarrow.parquet as pq

        names = set(pq.read_schema(f).names)
        if "season" not in names:
            return empty
        t = pq.read_table(
            f,
            columns=[c for c in GAMES_SCHEMA if c in names],
            filters=[("season", "=", season)],
            memory_map=False,  # never hold the file: a curate replaces it (D100)
        )
    except Exception:  # noqa: BLE001 - no table / an older one: no games
        return empty
    g = pl.from_arrow(t)
    if not isinstance(g, pl.DataFrame) or g.is_empty():
        return empty
    for c, dtype in GAMES_SCHEMA.items():
        if c not in g.columns:
            g = g.with_columns(pl.lit(None, dtype=dtype).alias(c))
    return g.with_columns(pl.col("kickoff_utc").cast(pl.Datetime("us", "UTC")))


def _detail(paths: DataPaths, text: object, limit: int) -> str:
    """Free text for a step's detail: the data root made relative **before** the cap, then
    scrubbed and capped with an ellipsis (a slice through the root's path would leave a
    fragment no later clean-up can match; Sol review, D132)."""
    from nflengine.paths import relative_paths
    from nflengine.weekly import scrub

    return scrub(relative_paths("" if text is None else str(text), paths.root), limit)


def _label(game_id: str, games: pl.DataFrame) -> str:
    r = games.filter(pl.col("game_id") == game_id)
    if r.height:
        row = r.row(0, named=True)
        return f"{row['away_team']} at {row['home_team']}"
    return "a game"


def _scoreboard(paths: DataPaths, season: int, week: int) -> pl.DataFrame:
    f = B.season_dir(paths, season) / "playcall_scoreboard.parquet"
    try:
        import pyarrow.parquet as pq

        names = set(pq.read_schema(f).names)
        t = pq.read_table(
            f,
            columns=[
                c for c in ("week", "status", "made_at", "version", "graded_at") if c in names
            ],
            filters=[("week", "=", week)],
            memory_map=False,
        )
    except Exception:  # noqa: BLE001 - none yet
        return pl.DataFrame()
    df = pl.from_arrow(t)
    return df if isinstance(df, pl.DataFrame) else pl.DataFrame()


def _forecast_meta(paths: DataPaths, season: int, week: int) -> dict[str, Any] | None:
    folder = B.season_dir(paths, season) / f"week{week:02d}"
    if not (folder / "forecast.parquet").exists():
        return None
    return _json(folder / "forecast.json")


def _have_model(paths: DataPaths) -> bool:
    return (paths.models / "playcall" / "production.json").exists()


def _review_done(paths: DataPaths, season: int, week: int) -> bool:
    from nflengine.live.review import week_dir

    return (week_dir(paths, season, week) / "decision_review.json").exists()


def _regraded(sb: pl.DataFrame, fm: dict[str, Any]) -> bool:
    """The forecast file was re-made after the grade: its `made_at` (or model version) isn't
    the one the scoreboard's rows graded (the reader then shows them ungraded, D131)."""
    made = _time(fm.get("made_at"))
    if made is None or "made_at" not in sb.columns:
        return False
    graded = {t for t in sb["made_at"].to_list() if t is not None}
    versions = set(sb["version"].to_list()) if "version" in sb.columns else set()
    return any(t != made for t in graded) or (
        fm.get("version") is not None and bool(versions) and versions != {fm.get("version")}
    )


def _graded_before(sb: pl.DataFrame, built_at: dt.datetime | None) -> bool:
    """The week's grade is older than the play-calling tables: a rebuild whose re-grade failed
    (or a build run by hand) left actuals from the old tables (Sol review, D132)."""
    if built_at is None or "graded_at" not in sb.columns:
        return False
    stamps = [_time(t) for t in sb["graded_at"].to_list() if t is not None]
    stamps = [t for t in stamps if t is not None]
    return bool(stamps) and min(stamps) < built_at


# --- the plan -------------------------------------------------------------------------------
def plan_update(paths: DataPaths, season: int, week: int, now: dt.datetime) -> list[Step]:
    """What `run_update` would do for week `week` right now, step by step (read-only)."""
    now = now if now.tzinfo else now.replace(tzinfo=dt.UTC)
    prev = week - 1 if week > 1 else None
    games = _games(paths, season)
    bj = _json(B.season_dir(paths, season) / "build.json")
    built_at = _time(bj.get("built_at")) if bj else None
    weeks = (bj or {}).get("as_of_weeks") or []
    as_of = int(weeks[1]) if isinstance(weeks, list) and len(weeks) == 2 else None
    out: list[Step] = []

    # 1. FTN: an earlier game still without FTN that kicked off long enough ago
    waiting = [
        g
        for g in ((bj or {}).get("coverage") or {}).get("games_without_ftn") or []
        if isinstance(g, str)
    ]
    ko = {
        r["game_id"]: r["kickoff_utc"]
        for r in games.select("game_id", "week", "kickoff_utc").iter_rows(named=True)
        if r["week"] is not None and r["week"] < week
    }
    earlier = [g for g in waiting if g in ko]
    # a game still without FTN a week after kickoff never gets it: stop asking for a refresh
    gone = [
        g
        for g in earlier
        if ko[g] is not None and ko[g] <= now - dt.timedelta(hours=FTN_GIVE_UP_HOURS)
    ]
    pending = [g for g in earlier if g not in gone]
    ripe = [
        g for g in pending if ko[g] is not None and ko[g] <= now - dt.timedelta(hours=FTN_DUE_HOURS)
    ]
    names = ", ".join(_label(g, games) for g in (ripe or pending or gone)[:3])
    if ripe:
        ftn = Step("ftn", "Refresh FTN charting", True,
                   f"{names} should have FTN by now (it lands ~2 days after a game)")  # fmt: skip
    elif pending:
        ftn = Step("ftn", "Refresh FTN charting", False,
                   f"FTN for {names} usually lands about two days after kickoff: try again "
                   "Wednesday afternoon")  # fmt: skip
    elif gone:
        ftn = Step("ftn", "Refresh FTN charting", False,
                   f"no FTN for {names} a week after kickoff: its plays count without "
                   "it")  # fmt: skip
    else:
        ftn = Step("ftn", "Refresh FTN charting", False, "every earlier game has its FTN charting")
    out.append(ftn)

    # 2. the play-calling tables
    plays = paths.curated / "plays" / f"season={season}.parquet"
    plays_at = _mtime(plays)
    if bj is None:
        build = Step("build", "Build the play-calling tables", True, f"no {season} tables yet")
    elif as_of is None or as_of < week:
        build = Step("build", "Build the play-calling tables", True,
                     f"the tables reach week {as_of or '?'}; week {week} needs week {week - 1}'s "
                     "games")  # fmt: skip
    elif ftn.due:
        build = Step("build", "Build the play-calling tables", True, "after the FTN refresh")
    elif plays_at is not None and built_at is not None and plays_at > built_at:
        build = Step("build", "Build the play-calling tables", True,
                     "the curated plays are newer than the last build")  # fmt: skip
    else:
        build = Step("build", "Build the play-calling tables", False,
                     f"up to date (as of week {as_of})")  # fmt: skip
    out.append(build)
    fresh = build.due  # new tables this run

    # 3. the decision review of the week just played
    if prev is None:
        review = Step("review", "4th-down decision review", False, "week 1: no week played yet")
    elif _review_done(paths, season, prev):
        review = Step("review", f"Week {prev}'s 4th-down decision review", False,
                      f"week {prev} is already reviewed", prev)  # fmt: skip
    else:
        review = Step("review", f"Week {prev}'s 4th-down decision review", True,
                      f"week {prev} hasn't been reviewed yet", prev)  # fmt: skip
    out.append(review)

    # 4. grade the week just played
    if prev is None:
        grade = Step("grade", "Grade last week's forecast", False, "week 1: nothing to grade")
    else:
        fm = _forecast_meta(paths, season, prev)
        sb = _scoreboard(paths, season, prev) if fm is not None else pl.DataFrame()
        label = f"Grade week {prev}'s forecast"
        if fm is None:
            grade = Step("grade", label, False, f"week {prev} has no forecast to grade", prev)
        elif sb.is_empty():
            grade = Step("grade", label, True, f"week {prev} isn't graded yet", prev)
        elif sb.filter(pl.col("status").is_in(list(WAITING))).height:
            n = sb.filter(pl.col("status").is_in(list(WAITING))).height
            grade = Step("grade", label, True,
                         f"{n} of week {prev}'s rows were still waiting", prev)  # fmt: skip
        elif _regraded(sb, fm):
            grade = Step("grade", label, True,
                         f"week {prev}'s forecast was made again after grading", prev)  # fmt: skip
        elif _graded_before(sb, built_at):
            grade = Step("grade", label, True,
                         f"the tables were rebuilt after week {prev}'s grade", prev)  # fmt: skip
        elif fresh:
            grade = Step("grade", label, True, "with the new tables", prev)
        else:
            grade = Step("grade", label, False, f"week {prev} is graded", prev)
    out.append(grade)

    # 5. this week's forecast
    label = f"Forecast week {week}"
    wk = games.filter(pl.col("week") == week)
    started = wk.filter(pl.col("kickoff_utc").is_not_null() & (pl.col("kickoff_utc") <= now))
    fm = _forecast_meta(paths, season, week)
    if not _have_model(paths):
        fc = Step("forecast", label, False,
                  "no promoted playcall-model (`nfl playcalling backtest`)", week)  # fmt: skip
    elif wk.is_empty():
        fc = Step("forecast", label, False, f"no week-{week} games in the schedule yet", week)
    elif fm is None:
        late = (
            " (some games have started: it's marked as made after kickoff)"
            if started.height
            else ""
        )
        fc = Step("forecast", label, True, f"no forecast for week {week} yet{late}", week)
    elif started.height:
        fc = Step("forecast", label, False, kept_forecast(paths, season, week, now) or "", week)
    elif fresh or (
        built_at is not None and (made := _time(fm.get("made_at"))) is not None and made < built_at
    ):
        fc = Step("forecast", label, True, "the tables are newer than the forecast", week)
    else:
        fc = Step("forecast", label, False, f"week {week}'s forecast is up to date", week)
    out.append(fc)
    return out


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def due(steps: list[Step]) -> list[Step]:
    return [s for s in steps if s.due]


# --- running it -----------------------------------------------------------------------------
@dataclass
class StepResult:
    step: str
    status: str  # ok | failed | skipped
    seconds: float
    detail: str


def _ftn_refresh(paths: DataPaths, season: int, week: int, log: Callable[..., None]) -> str:
    """FTN only (no Odds API credits), then the curated tables and their quality checks (this
    week's finished games may lack play-by-play: exempt, as in the injury update, D126)."""
    from nflengine.curate.build import build_all
    from nflengine.curate.quality import BLOCK, run_quality_checks
    from nflengine.ingest.runner import run_ingest

    results, _ = run_ingest(season=season, sources=["nflverse"],
                            nflverse_datasets=["ftn_charting"], seasons=[season],
                            log=log)  # fmt: skip
    failed = [r for r in results if r.status == "failed"]
    if failed:
        raise RuntimeError(f"the FTN pull failed ({_detail(paths, failed[0].detail, 120)})")
    # a "partial" or empty pull can still miss this season: only its own file counts
    ftn = [r for r in results if r.dataset == "ftn_charting"]
    if not ftn or f"season={season}" not in (ftn[0].parts or []):
        why = _detail(paths, ftn[0].detail, 120) if ftn and ftn[0].detail else "no rows"
        raise RuntimeError(f"the FTN pull got nothing for {season} ({why})")
    built = build_all(log=log)
    checks = run_quality_checks(pbp_pending_week=(season, week))
    blocked = [c.name for c in checks if not c.passed and c.level == BLOCK]
    if blocked:
        raise RuntimeError(f"blocking quality checks failed: {blocked}")
    return f"FTN pulled, {len(built)} curated tables rebuilt, quality checks pass"


def kept_forecast(paths: DataPaths, season: int, week: int, now: dt.datetime) -> str | None:
    """Why week `week`'s saved forecast must stay: it exists and one of the week's games has
    kicked off by `now` (re-made, it would become a backfill). None when it may be (re)made.
    `run_update` asks again right before the forecast: a run started minutes before
    Thursday's kickoff can reach that step after it (Sol review, D132)."""
    now = now if now.tzinfo else now.replace(tzinfo=dt.UTC)
    fm = _forecast_meta(paths, season, week)
    if fm is None:
        return None
    games = _games(paths, season)
    started = games.filter(
        (pl.col("week") == week)
        & pl.col("kickoff_utc").is_not_null()
        & (pl.col("kickoff_utc") <= now)
    )
    if started.is_empty():
        return None
    return (
        f"week {week}'s games have started: the forecast made "
        f"{str(fm.get('made_at') or '')[:10]} stays"
    )


def run_update(
    season: int,
    week: int,
    *,
    steps: list[Step],
    use_wandb: bool = True,
    launched_by: str | None = None,
    log: Callable[..., None] = print,
    paths: DataPaths,
    on_step: Callable[[str, str], None] | None = None,
    on_step_end: Callable[[StepResult], None] | None = None,
    clock: Callable[[], dt.datetime] | None = None,
) -> list[StepResult]:
    """Run the due steps in order. A failed FTN refresh or build skips the steps that need
    their output (grade, forecast); a failed review or grade never stops the others. The
    forecast is skipped when a game of the week has kicked off by the time it's reached and a
    forecast exists (`kept_forecast`, with `clock`)."""
    from nflengine.live.review import run_review
    from nflengine.playcalling.build import run_build
    from nflengine.playcalling.forecast_runs import run_forecast, run_grade

    todo = {s.step: s for s in due(steps)}
    results: list[StepResult] = []
    broken: str | None = None  # a failed step whose output later steps need
    for name in STEPS:
        s = todo.get(name)
        if s is None:
            continue
        if on_step:
            on_step(name, s.label)
        t0 = time.perf_counter()
        kept = (
            kept_forecast(paths, season, week, (clock or _utcnow)())
            if name == "forecast" and not broken
            else None
        )
        if broken and name in ("build", "grade", "forecast"):
            r = StepResult(name, "skipped", 0.0, f"skipped: the {broken} step failed")
        elif kept:
            r = StepResult(name, "skipped", 0.0, f"skipped: {kept}")
        else:
            try:
                if name == "ftn":
                    detail = _ftn_refresh(paths, season, week, log)
                elif name == "build":
                    out = run_build(season, use_wandb=use_wandb, launched_by=launched_by, log=log,
                                    paths=paths)  # fmt: skip
                    w = out["seasons"][0]["as_of_weeks"] if out["seasons"] else None
                    detail = f"tables as of weeks {w[0]}-{w[1]}" if w else "tables built"
                elif name == "review":
                    rev = run_review(season, [int(s.week or week - 1)], use_wandb=use_wandb,
                                     launched_by=launched_by, log=log, paths=paths)  # fmt: skip
                    detail = (
                        f"week {s.week} reviewed"
                        if rev
                        else f"week {s.week} has no curated plays yet: nothing reviewed"
                    )
                elif name == "grade":
                    g = run_grade(season, int(s.week or week - 1), use_wandb=use_wandb,
                                  launched_by=launched_by, log=log, paths=paths)  # fmt: skip
                    c = g["counts"]
                    detail = f"graded {c.get('graded', 0)} of {g['frame'].height} rows" + (
                        f", {sum(v for k, v in c.items() if k in WAITING)} still waiting"
                        if any(k in WAITING for k in c)
                        else ""
                    )
                else:  # forecast
                    f = run_forecast(season, week, use_wandb=use_wandb, launched_by=launched_by,
                                     log=log, paths=paths)  # fmt: skip
                    meta = f["meta"]
                    detail = f"{meta['games']} games, {meta['rows']} forecasts" + (
                        f" ({len(meta['games_after_kickoff'])} made after kickoff)"
                        if meta["games_after_kickoff"]
                        else ""
                    )
                r = StepResult(name, "ok", round(time.perf_counter() - t0, 1), detail)
            except Exception as e:  # each step's own errors are plain sentences
                r = StepResult(name, "failed", round(time.perf_counter() - t0, 1),
                               _detail(paths, f"{type(e).__name__}: {e}", 300))  # fmt: skip
                if name in ("ftn", "build"):
                    broken = name
        log(f"{name}: {r.status} ({r.seconds:.0f} s): {r.detail}")
        results.append(r)
        if on_step_end:
            on_step_end(r)
    return results


def summary(results: list[StepResult]) -> tuple[str, str]:
    """(status, one-line message) for the run's end."""
    if not results:
        return "ok", "nothing was due"
    failed = [r for r in results if r.status == "failed"]
    done = [r.step for r in results if r.status == "ok"]
    status = "failed" if failed else "ok"
    msg = (f"done: {', '.join(done)}" if done else "nothing done") + (
        f"; failed: {', '.join(r.step for r in failed)}" if failed else ""
    )
    return status, msg
