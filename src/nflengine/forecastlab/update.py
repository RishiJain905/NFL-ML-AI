"""The Forecast lab update: what's due after a weekly run or an injury update (FL02, D158).

`nfl forecastlab update --auto` works out what is due for the calendar's week W and runs only
that, in order:

1. `availability`: the availability and role view(s) of week W. The **Tuesday** view once the
   week's run is published; the **Saturday** view once the week's injury update has run. A
   view is made again when its source is newer, never for a game that has kicked off.
2. `curves`: week W's outcome curves, from the week's published player projections.
3. `grade`: the last weeks' availability and curves against what happened: a week once it is
   final, and again while rows of its final games still wait for results (snap counts can
   land after the score); the three weeks before W are looked at.

The control room starts it as a follow-up to **Run week N** and to the Saturday injury update
(the runner's fifth kind, `forecastlab_update`); the Availability and Outcomes tabs have an
**Update** button for the same command. It holds the weekly-run lock, so it never overlaps a
weekly run, a resume, an injury update or a play-calling update. Not a weekly-run step (D107):
a failure here never touches the published week.

`plan_update` only reads files (the app's pre-flight calls it); `run_update` does the work.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.forecastlab import schema as S
from nflengine.paths import DataPaths

STEPS = ("availability", "curves", "grade")
GRADE_BACK = 3  # the grade step looks this many weeks back (a missed or partly graded week)
GAMES_SCHEMA: dict[str, Any] = {
    "game_id": pl.Utf8,
    "season": pl.Int64,
    "week": pl.Int64,
    "game_type": pl.Utf8,
    "kickoff_utc": pl.Datetime("us", "UTC"),
    "result": pl.Float64,
}


@dataclass(frozen=True)
class Step:
    step: str  # one of STEPS
    label: str  # "Availability for week 11 (Saturday view)"
    due: bool
    reason: str  # why it's due, or why it's skipped
    week: int | None = None
    views: tuple[str, ...] = ()  # availability: the views to make
    parts: tuple[str, ...] = ()  # grade: the families to grade
    jobs: tuple[tuple[int, str], ...] = ()  # grade: every (week, family) to grade

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StepResult:
    step: str
    status: str  # ok | failed | skipped
    seconds: float
    detail: str
    urls: list[str] = field(default_factory=list)


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
    """The season's games (week, kickoff, result); empty without a curated table. Never
    holds the file: a curate replaces it (D100)."""
    f = paths.curated / "games.parquet"
    empty = pl.DataFrame(schema=GAMES_SCHEMA)
    try:
        import pyarrow.parquet as pq

        names = set(pq.read_schema(f).names)
        if "season" not in names:
            return empty
        t = pq.read_table(
            f,
            columns=[c for c in GAMES_SCHEMA if c in names],
            filters=[("season", "=", season)],
            memory_map=False,
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
    """Free text for a step's detail: the data root made relative before the cap, then
    scrubbed (D132's rule)."""
    from nflengine.paths import relative_paths
    from nflengine.weekly import scrub

    return scrub(relative_paths("" if text is None else str(text), paths.root), limit)


def _err(e: BaseException) -> str:
    """An exception as a plain sentence. A `KeyError` is worded as a missing name: the
    scrubber reads the text `KeyError: x` as a secret-looking `key: value` and would
    mask what was missing (the test worker's find)."""
    if isinstance(e, KeyError):
        return f"LookupError: missing {e.args[0]!r}" if e.args else "LookupError: missing name"
    return f"{type(e).__name__}: {e}"


def production(paths: DataPaths, family: str) -> dict[str, Any] | None:
    """The family's promoted model (`production.json`), or None."""
    return _json(S.production_file(paths, family))


def week_meta(paths: DataPaths, season: int, week: int, family: str) -> dict[str, Any] | None:
    """`availability.json` / `curves.json` of a week, only when its parquet file exists."""
    folder = S.week_dir(paths, season, week)
    if not (folder / f"{family}.parquet").exists():
        return None
    return _json(folder / f"{family}.json") or {}


def grade_state(
    paths: DataPaths, season: int, week: int, family: str, final_games: set[str]
) -> tuple[bool, int]:
    """(never graded, rows waiting) for a week's file. A row is waiting when its game is
    final and it has no result yet: the grade fills results game by game, and snap counts
    can land after a game's score does (the review's find: a partial grade must not count as
    done). Never graded: no row has a result at all. A file that can't be read is neither."""
    f = S.week_dir(paths, season, week) / f"{family}.parquet"
    try:
        df = S.read_parquet(f, columns=["game_id", "played"])
    except Exception:  # noqa: BLE001 - no file, or not one of ours
        return False, 0
    if df.is_empty():
        return False, 0
    never = df.filter(pl.col("played").is_not_null()).height == 0
    waiting = df.filter(
        pl.col("played").is_null() & pl.col("game_id").is_in(sorted(final_games))
    ).height
    return never, waiting


def kickoffs(games: pl.DataFrame, week: int, now: dt.datetime) -> tuple[int, int]:
    """(games of the week, how many have kicked off by `now`)."""
    wk = games.filter(pl.col("week") == week)
    started = wk.filter(pl.col("kickoff_utc").is_not_null() & (pl.col("kickoff_utc") <= now))
    return wk.height, started.height


def week_final(games: pl.DataFrame, week: int) -> bool:
    wk = games.filter(pl.col("week") == week)
    return wk.height > 0 and wk.filter(pl.col("result").is_null()).height == 0


def injury_update_time(paths: DataPaths, season: int, week: int) -> dt.datetime | None:
    """When the week's Saturday injury update last ran (None: it hasn't)."""
    return _mtime(paths.run_dir(season, week) / "injury_update.json")


# --- the plan -------------------------------------------------------------------------------
def plan_update(paths: DataPaths, season: int, week: int, now: dt.datetime) -> list[Step]:
    """What `run_update` would do for week `week` right now, step by step (read-only)."""
    now = now if now.tzinfo else now.replace(tzinfo=dt.UTC)
    prev = week - 1 if week > 1 else None
    games = _games(paths, season)
    n_games, n_started = kickoffs(games, week, now)
    open_games = n_games - n_started
    run_dir = paths.run_dir(season, week)
    out: list[Step] = []

    # 1. availability and role
    label = f"Availability for week {week}"
    prod = production(paths, "availability")
    meta = week_meta(paths, season, week, "availability")
    views_meta = (meta or {}).get("views")
    made = {
        v: _time(d.get("made_at"))
        for v, d in (views_meta.items() if isinstance(views_meta, dict) else ())
        if isinstance(d, dict)
    }
    sat_at = injury_update_time(paths, season, week)
    # the week's published digest: a later one means the week was run again (--force)
    published_at = _mtime(paths.report_path(season, week))
    if prod is None:
        av = Step("availability", label, False,
                  "no promoted availability model yet", week)  # fmt: skip
    elif n_games == 0:
        av = Step("availability", label, False, f"no week-{week} games in the schedule yet", week)
    else:
        views: list[str] = []
        why: list[str] = []
        if made.get("tuesday") is None:
            views.append("tuesday")
            why.append("no Tuesday view yet")
        elif (
            open_games
            and sat_at is None
            and published_at is not None
            and published_at > made["tuesday"]
        ):
            views.append("tuesday")
            why.append("the week was run again after the Tuesday view")
        if sat_at is not None:
            if made.get("saturday") is None:
                views.append("saturday")
                why.append("the injury update has run: the Saturday view isn't made yet")
            elif open_games and made["saturday"] < sat_at:
                views.append("saturday")
                why.append("the injury update ran again after the Saturday view")
        if views and not open_games and meta is not None:
            why_kept = f"week {week}'s games have all kicked off: the saved views stay"
            av = Step("availability", label, False, why_kept, week)
        elif views:
            late = " (some games have started: their rows are kept or marked)" if n_started else ""
            av = Step("availability", f"{label} ({' + '.join(v.title() for v in views)} view)",
                      True, "; ".join(why) + late, week, tuple(views))  # fmt: skip
        else:
            have = " and ".join(v.title() for v in S.VIEWS if made.get(v) is not None)
            av = Step("availability", label, False, f"up to date ({have} view)", week)
    out.append(av)

    # 2. outcome curves
    label = f"Outcome curves for week {week}"
    prod = production(paths, "curves")
    meta = week_meta(paths, season, week, "curves")
    preds = run_dir / "predictions_players.parquet"
    preds_at = _mtime(preds)
    made_at = _time((meta or {}).get("made_at")) if isinstance(meta, dict) else None
    if prod is None:
        cv = Step("curves", label, False,
                  "no promoted curves model yet (`nfl forecastlab backtest`)", week)  # fmt: skip
    elif not (prod.get("shipped") or []):
        cv = Step("curves", label, False, "no curve target passed its ship rule", week)
    elif preds_at is None:
        cv = Step("curves", label, False,
                  f"week {week} has no player projections yet (Run week {week})", week)  # fmt: skip
    elif meta is None or made_at is None:  # no file, or one whose meta never got written
        late = (
            " (some games have started: they're marked as made after kickoff)" if n_started else ""
        )
        cv = Step("curves", label, True, f"no curves for week {week} yet{late}", week)
    elif preds_at > made_at and open_games:
        cv = Step("curves", label, True, "the player projections are newer than the curves", week)
    else:
        cv = Step("curves", label, False, f"week {week}'s curves are up to date", week)
    out.append(cv)

    # 3. grade the week just played
    if prev is None:
        gr = Step("grade", "Grade last week", False, "week 1: nothing to grade")
    else:
        # the last GRADE_BACK weeks, oldest first: a week nobody updated during, or one whose
        # snap counts landed late, is still picked up (the review's find)
        jobs: list[tuple[int, str]] = []
        why: list[str] = []
        not_final: list[int] = []
        has_any = False
        for w in range(max(1, week - GRADE_BACK), week):
            fams = [f for f in S.FAMILIES if week_meta(paths, season, w, f) is not None]
            if not fams:
                continue
            has_any = True
            wk = games.filter(pl.col("week") == w)
            final = set(wk.filter(pl.col("result").is_not_null())["game_id"].to_list())
            states = {f: grade_state(paths, season, w, f, final) for f in fams}
            if not week_final(games, w):
                # first grades wait for the whole week; rows of its final games already
                # graded once are topped up
                todo = [f for f in fams if not states[f][0] and states[f][1]]
                if not todo:
                    if any(states[f][0] for f in fams):
                        not_final.append(w)
                    continue
            else:
                todo = [f for f in fams if states[f][0] or states[f][1]]
            if todo:
                jobs += [(w, f) for f in todo]
                fresh = [f for f in todo if states[f][0]]
                late = [f for f in todo if not states[f][0]]
                if fresh:
                    why.append(f"week {w}'s {' and '.join(fresh)} aren't graded yet")
                if late:
                    n = sum(states[f][1] for f in late)
                    rows_ = "row" if n == 1 else "rows"
                    why.append(
                        f"week {w}'s {' and '.join(late)} had {n} {rows_} waiting for results"
                    )
        weeks = sorted({w for w, _ in jobs})
        label = (
            f"Grade week {prev}"
            if not weeks
            else f"Grade week {weeks[0]}"
            if len(weeks) == 1
            else f"Grade weeks {', '.join(str(w) for w in weeks)}"
        )
        if jobs:
            gr = Step("grade", label, True, "; ".join(why), weeks[-1],
                      parts=tuple(f for f in S.FAMILIES if any(f == j[1] for j in jobs)),
                      jobs=tuple(jobs))  # fmt: skip
        elif not has_any:
            gr = Step("grade", label, False, f"week {prev} has nothing to grade", prev)
        elif not_final:
            gr = Step("grade", label, False,
                      f"week {not_final[-1]} isn't final in the data yet", prev)  # fmt: skip
        else:
            gr = Step("grade", label, False, f"week {prev} is graded", prev)
    out.append(gr)
    return out


def due(steps: list[Step]) -> list[Step]:
    return [s for s in steps if s.due]


# --- running it -----------------------------------------------------------------------------
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
    """Run the due steps in order. Each step stands alone: a failed one never stops the
    others. The models' own `run_predict` keeps the rows of games that have kicked off
    (`now` = `clock()` right before the step: a run started minutes before a kickoff can reach
    the step after it)."""
    from nflengine.forecastlab import availability_runs as A
    from nflengine.forecastlab import curves_runs as C

    def now() -> dt.datetime:
        return (clock or (lambda: dt.datetime.now(dt.UTC)))()

    common: dict[str, Any] = dict(
        use_wandb=use_wandb, launched_by=launched_by, log=log, paths=paths
    )
    todo = {s.step: s for s in due(steps)}
    results: list[StepResult] = []
    for name in STEPS:
        s = todo.get(name)
        if s is None:
            continue
        if on_step:
            on_step(name, s.label)
        t0 = time.perf_counter()
        urls: list[str] = []
        bits: list[str] = []
        errors: list[str] = []
        try:
            if name == "availability":
                for view in s.views:  # a failed view never loses the one already made
                    try:
                        o = A.run_predict(season, week, view=view, now=now(), **common)
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{view} view: {_err(e)}")
                        continue
                    if o.get("url"):
                        urls.append(o["url"])
                    late = (o.get("meta") or {}).get("games_after_kickoff") or []
                    bits.append(
                        f"{view} view: {o['frame'].height} players"
                        + (f" ({len(late)} games after kickoff)" if late else "")
                    )
                detail = "; ".join(bits)
            elif name == "curves":
                o = C.run_predict(season, week, now=now(), **common)
                if o.get("url"):
                    urls.append(o["url"])
                late = (o.get("meta") or {}).get("games_after_kickoff") or []
                detail = f"{o['frame'].height} curves" + (
                    f" ({len(late)} games after kickoff)" if late else ""
                )
            else:  # grade: one family failing never blocks the other
                jobs = s.jobs or tuple((int(s.week or week - 1), f) for f in s.parts)
                for w, fam in jobs:
                    fn = A.run_grade if fam == "availability" else C.run_grade
                    try:
                        o = fn(season, w, **common)
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"week {w} {fam}: {_err(e)}")
                        continue
                    if o.get("url"):
                        urls.append(o["url"])
                    left = (o.get("summary") or {}).get("waiting") or 0
                    bits.append(
                        f"week {w} {fam}: {o['frame'].height} rows"
                        + (f" ({left} still waiting)" if left else "")
                    )
                detail = f"graded ({'; '.join(bits)})" if bits else ""
            if errors:
                done = f"done: {detail}; " if bits else ""
                text = _detail(paths, f"{done}failed: {'; '.join(errors)}", 300)
                r = StepResult(name, "failed", round(time.perf_counter() - t0, 1), text, urls)
            else:
                r = StepResult(name, "ok", round(time.perf_counter() - t0, 1), detail, urls)
        except Exception as e:  # each step's own errors are plain sentences
            r = StepResult(name, "failed", round(time.perf_counter() - t0, 1),
                           _detail(paths, _err(e), 300))  # fmt: skip
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
