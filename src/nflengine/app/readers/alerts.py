"""The Alerts page (CR03): the season's alerts, every run's drift states, when each drift
signal can first fire, and the example alert.

- Alerts and drift states: every run week's `run_summary.json` (`alerts`, `drift`).
- Investigation notes: `documentation/plans/PROGRESS.md` → "Season log (<season>)", the
  sub-bullets under each week's line (notes stay in PROGRESS; the app never edits them).
- When a signal can first fire: `settings.yaml` → `drift` and where the season's graded data
  starts (the first run week for the game signals; the scoreboard's first week for the player
  signals, whose walk-forward weeks count).
- The example: the 2025 week-10 simulation's calibration alert (P07), which led to D75.
"""

from __future__ import annotations

import re
from typing import Any

import polars as pl

from nflengine.app.readers.common import clean, read_json, read_parquet, read_text
from nflengine.app.readers.models import REPO_ROOT
from nflengine.paths import DataPaths

PROGRESS = REPO_ROOT / "documentation" / "plans" / "PROGRESS.md"
WEEK_LINE = re.compile(r"^- week (\d{1,2}):")
EXAMPLE_NOTE = (
    "A perfectly calibrated model averages an ECE of 0.082 on that many games, so the flat 0.05 "
    "limit sat below the noise. Decision D75: the limit became the noise level, falling from "
    "about 0.16 after five weeks to 0.08 by week 18. Nothing retunes automatically."
)
SIGNAL_LABEL = {
    "game_vs_elo": "Brier vs Elo over rolling 4-week windows",
    "calibration": "Season ECE vs the noise level",
    "player_vs_baseline": "MAE vs the rolling baseline, per group",
    "player_prob_vs_baseline": "Chances' Brier vs the rolling rate, per group (P08)",
    "data_freshness": "Snapshots older than 7 days, sources a week behind",
    "checks": "Share of digests that failed their checks",
}


def season_notes(season: int, text: str | None = None) -> dict[int, list[str]]:
    """Week -> the indented notes under its line in PROGRESS.md's season log."""
    text = text if text is not None else read_text(PROGRESS)
    if not text:
        return {}
    notes: dict[int, list[str]] = {}
    inside = False
    week = None
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.strip().startswith(f"## Season log ({season})")
            week = None
            continue
        if not inside:
            continue
        m = WEEK_LINE.match(line)
        if m:
            week = int(m.group(1))
            continue
        if week is not None and line.startswith(("  -", "  *", "    ")) and line.strip():
            notes.setdefault(week, []).append(
                clean(line.strip().lstrip("-* ").strip(), None, 600) or ""
            )
        elif line.strip() and not line.startswith(" "):
            week = None
    return notes


def _next_week_after(done: list[int], needed: int, last: int | None) -> int | None:
    """The run week whose input first holds `needed` items, one more item per run week from
    now on (the `needed`-th item's week + 1 when it's already in; a run grades the weeks
    before it)."""
    if len(done) >= needed:
        return done[needed - 1] + 1
    if last is None:
        return None
    return last + 1 + (needed - len(done))


def first_weeks(
    paths: DataPaths,
    season: int,
    first_run: int | None,
    current_week: int | None,
    sched: pl.DataFrame | None = None,
) -> dict[str, tuple[str, int | None, str]]:
    """When each drift signal can first leave "insufficient data", from the same inputs and
    rules the evaluator uses (`ops.drift`; Sol review, CR03): run week W sees the graded weeks
    before W; the checks window counts the run's own verdict.

    - `game_vs_elo`: the season scorecard's graded weeks; `drift._needed` of them;
    - `calibration`: graded games (`ece_min_games`), then the schedule's games per week;
    - `player_vs_baseline` / `player_prob_vs_baseline`: per group, the scoreboard's scored
      weeks for that metric (MAE rows / Brier rows, `drift.prepare_scoreboard`), the earliest
      group;
    - `checks`: the history's digest verdicts (latest main run per week), the window counting
      the current run's own;
    - `data_freshness`: every run."""
    from nflengine.ops import drift
    from nflengine.ops.records import latest_runs

    cfg = drift.resolve_cfg(None)
    needed = drift._needed(cfg)
    out: dict[str, tuple[str, int | None, str]] = {}
    # graded weeks (game_vs_elo, calibration): the season scorecard, as the dashboard reads it
    sc = read_parquet(
        paths.runs / str(season) / "season_scorecard.parquet", ["season", "week", "games"]
    )
    graded: list[tuple[int, int]] = []
    if sc is not None and sc.height:
        rows = sc.filter((pl.col("season") == season) & (pl.col("games").fill_null(0) > 0))
        graded = sorted((int(r["week"]), int(r["games"])) for r in rows.iter_rows(named=True))
    last_graded = graded[-1][0] if graded else (first_run - 1 if first_run else None)
    w = _next_week_after([g[0] for g in graded], needed, last_graded)
    if w is not None:
        out["game_vs_elo"] = (f"{needed} graded weeks", w, f"week {w}'s run")
    min_games = int(cfg.get("ece_min_games", 64))
    total, w, estimated = 0, None, False
    for week, games in graded:
        total += games
        if total >= min_games:
            w = week + 1
            break
    if w is None and last_graded is not None:
        future: list[tuple[int, int]] = []
        if sched is not None and {"season", "week", "game_id"} <= set(sched.columns):
            per = (
                sched.filter((pl.col("season") == season) & (pl.col("week") > last_graded))
                .group_by("week")
                .agg(pl.col("game_id").n_unique().alias("n"))
                .sort("week")
            )
            future = [(int(r["week"]), int(r["n"])) for r in per.iter_rows(named=True)]
        week = last_graded
        for wk, n in future or [(last_graded + i, 16) for i in range(1, 20)]:
            total += n
            week = wk
            if total >= min_games:
                break
        w, estimated = week + 1, True
    if w is not None:
        out["calibration"] = (
            f"{min_games} graded games",
            w,
            f"about week {w}'s run" if estimated else f"week {w}'s run",
        )
    # the player signals: per group, the scored weeks of that metric
    sb = read_parquet(paths.runs / str(season) / "accuracy_scoreboard.parquet")
    for name, metric in (("player_vs_baseline", "mae"), ("player_prob_vs_baseline", "brier")):
        prepared = (
            drift.prepare_scoreboard(sb, season, 99, metric) if sb is not None else pl.DataFrame()
        )
        best = None
        for g in prepared["position_group"].unique().to_list() if prepared.height else []:
            weeks = sorted(
                prepared.filter(pl.col("position_group") == g)["week"].unique().to_list()
            )
            gw = _next_week_after(weeks, needed, weeks[-1] if weeks else None)
            if gw is not None and (best is None or gw < best):
                best = gw
        if best is not None:
            out[name] = (f"{needed} scored weeks", best, f"week {best}'s run")
    # checks: the digests' verdicts, the window including the run's own
    n = int(cfg.get("checks_window_weeks", 4))
    hist = read_parquet(paths.runs / str(season) / "pipeline_history.parquet")
    verdicts: list[int] = []
    if hist is not None and hist.height:
        runs = latest_runs(hist, "main").filter(
            (pl.col("season") == season) & pl.col("checks_passed").is_not_null()
        )
        verdicts = sorted(int(x) for x in runs["week"].to_list())
    if len(verdicts) >= n:
        w = verdicts[n - 1]
    else:
        base = verdicts[-1] if verdicts else ((current_week or first_run or 1) - 1)
        w = base + (n - len(verdicts))
    out["checks"] = (f"{n} digests with a checks verdict", w, f"week {w}'s run")
    if first_run is not None:
        out["data_freshness"] = ("any run", first_run, "every run")
    return out


def _alert(a: dict[str, Any], season: int, week: int, notes: list[str], paths: DataPaths) -> dict:
    return {
        "season": season,
        "week": week,
        "level": clean(a.get("level"), None, 20) or "warn",
        "title": clean(a.get("title"), paths, 200) or "",
        "text": clean(a.get("text"), paths, 1200) or "",
        "source": clean(a.get("source"), None, 60) or "",
        "run_url": None,
        "notes": notes,
    }


WORST = {"alert": 3, "ok": 1, "insufficient_data": 0}


def get_alerts(
    paths: DataPaths,
    season: int,
    current_week: int | None,
    pipeline_urls: dict[int, str],
    sched: pl.DataFrame | None = None,
) -> dict[str, Any]:
    notes = season_notes(season)
    alerts: list[dict[str, Any]] = []
    runs = []
    latest_drift: list[dict[str, Any]] = []
    first_run = None
    for w in range(1, 23):
        run_dir = paths.run_dir(season, w)
        if first_run is None and (run_dir / "weekly_run.json").exists():
            first_run = w
        s = read_json(run_dir / "run_summary.json")
        if not s:
            continue
        drift_rows = [d for d in s.get("drift") or [] if isinstance(d, dict)]
        for a in s.get("alerts") or []:
            if isinstance(a, dict):
                item = _alert(a, season, w, notes.get(w, []), paths)
                item["run_url"] = pipeline_urls.get(w)
                alerts.append(item)
        runs.append(
            {
                "week": w,
                "alerts": len(s.get("alerts") or []),
                "drift": [
                    {
                        "name": clean(d.get("name"), None, 60) or "",
                        "group": clean(d.get("group"), None, 20),
                        "status": clean(d.get("status"), None, 30) or "",
                    }
                    for d in drift_rows
                ],
            }
        )
        latest_drift = drift_rows
    alerts.sort(key=lambda a: -a["week"])
    firsts = first_weeks(paths, season, first_run, current_week, sched)
    signals = []
    for name, label in SIGNAL_LABEL.items():
        rows = [d for d in latest_drift if d.get("name") == name]
        worst = max(rows, key=lambda d: WORST.get(str(d.get("status")), 2), default=None)
        needs, first, first_text = firsts.get(name, ("", None, "not known yet"))
        signals.append(
            {
                "name": name,
                "label": label,
                "needs": needs,
                "first_week": first,
                "first_text": first_text,
                "status_now": clean((worst or {}).get("status"), None, 30),
                "detail_now": clean((worst or {}).get("detail"), paths, 400),
            }
        )
    example = None
    ex = read_json(paths.runs / "digest-backtests" / "2025" / "week10" / "run_summary.json")
    for a in (ex or {}).get("alerts") or []:
        if isinstance(a, dict) and "calibration" in str(a.get("source")):
            example = _alert(a, 2025, 10, [], paths)
            break
    return {
        "season": season,
        "current_week": current_week,
        "alerts": alerts,
        "signals": signals,
        "runs": runs,
        "example": example,
        "example_note": EXAMPLE_NOTE if example else None,
    }


def first_run_week(paths: DataPaths, season: int) -> int | None:
    for w in range(1, 23):
        if (paths.run_dir(season, w) / "weekly_run.json").exists():
            return w
    return None
