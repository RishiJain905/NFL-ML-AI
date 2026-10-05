"""The season dashboard (documentation/08 -> Season scorecard, "2026 Season Dashboard"; P07).

Two parts:

1. `log_season_dashboard(season, week)` runs after every weekly run. It opens one W&B run
   (group `season-dashboard`, job type `dashboard`, name `season-<season>-w<NN>`) that logs
   the whole season to date as line series, then tags that run `dashboard-current` and
   removes the tag from the season's older dashboard runs.
2. `build_report(season)` creates the W&B Report "<season> Season Dashboard" once. Its
   panels read only the run tagged `dashboard-current`, so the report shows the newest
   weekly run's series without anyone editing it.

Series (all standard line plots, so they render in the W&B mobile app):

- x = the **graded** week (`dash/week`; the run of week N grades weeks before N):
  `game/*` (Brier model / Elo / market weekly and cumulative, rolling Brier gap vs Elo, pick
  accuracy, points error), `watch/*` (watch-list hit rate), `cal/*` (season ECE and the ECE
  of a perfectly calibrated model), `player/*` (MAE improvement over the rolling baseline per
  position group: weekly, cumulative, rolling window).
- x = predicted probability (`calcurve/predicted`): the season-to-date calibration curve.
- x = the **run** week (`pipe/week`): `pipe/*` pipeline health from `pipeline_history.parquet`
  (on time, hours before the first kickoff, run minutes, checks passed, regenerated, degraded
  steps, drift alerts, cumulative on-time and check-pass rates).

Logging fails soft: any problem (W&B unreachable, a missing file) is logged as one line and
`log_season_dashboard` returns None; it never raises into the weekly pipeline.
"""

from __future__ import annotations

import contextlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.digest.report_card import PRED_FILE, load_saved
from nflengine.digest.run import read_games, season_series
from nflengine.models import metrics as M
from nflengine.models.player_schema import GROUP_SLUG, GROUPS
from nflengine.ops import drift
from nflengine.ops.records import history_path, latest_runs, read_history
from nflengine.paths import DataPaths, ensure_data_root

GROUP = "season-dashboard"
GROUP_SMOKE = "season-dashboard-smoke"
CURRENT_TAG = "dashboard-current"
DASHBOARD_FILE = "dashboard.json"  # runs/<season>/dashboard.json: the report URL + last run

# scorecard curve (digest.run.season_series) -> dashboard name
SCORECARD_KEYS = {
    "season/brier_model": "game/brier_model",
    "season/brier_elo": "game/brier_elo",
    "season/brier_market": "game/brier_market",
    "season/cum_brier_model": "game/cum_brier_model",
    "season/cum_brier_elo": "game/cum_brier_elo",
    "season/cum_brier_market": "game/cum_brier_market",
    "season/pick_accuracy": "game/pick_accuracy",
    "season/cum_pick_accuracy": "game/cum_pick_accuracy",
    "season/points_mae": "game/points_mae",
    "season/watchlist_hit_rate": "watch/hit_rate",
    "season/cum_watchlist_hit_rate": "watch/cum_hit_rate",
}


@dataclass
class DashboardData:
    """Everything one dashboard run logs (built without W&B, so it can be tested)."""

    season: int
    week: int  # the run week; graded through week - 1
    graded_weeks: list[int] = field(default_factory=list)
    points: list[dict[str, float]] = field(default_factory=list)  # x = `dash/week`
    curve: pl.DataFrame = field(default_factory=pl.DataFrame)  # season-to-date reliability bins
    pipeline: list[dict[str, float]] = field(default_factory=list)  # x = `pipe/week`
    summary: dict[str, Any] = field(default_factory=dict)


def _num(x: Any) -> float | None:
    """A finite float, or None (W&B would drop or choke on NaN)."""
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def _put(rows: dict[int, dict[str, float]], week: int, **kv: Any) -> None:
    row = rows.setdefault(int(week), {"dash/week": int(week)})
    row.update({k: v for k, v in ((k, _num(v)) for k, v in kv.items()) if v is not None})


# ---- the data ---------------------------------------------------------------------------------


def pipeline_points(history: pl.DataFrame | None, season: int, week: int, kind: str) -> list[dict]:
    """One point per run week (x = `pipe/week`) from the last run of each week, with the
    cumulative on-time and check-pass rates."""
    if history is None or history.is_empty():
        return []
    runs = latest_runs(history, kind).filter(
        (pl.col("season") == season) & (pl.col("week") <= week)
    )
    out: list[dict] = []
    on_time = on_total = ok = ok_total = 0
    for r in runs.iter_rows(named=True):
        p: dict[str, float] = {"pipe/week": int(r["week"])}
        if r["on_time"] is not None:
            on_total += 1
            on_time += int(r["on_time"])
            p["pipe/on_time"] = float(r["on_time"])
            p["pipe/cum_on_time_rate"] = on_time / on_total
        if r["checks_passed"] is not None:
            ok_total += 1
            ok += int(r["checks_passed"])
            p["pipe/checks_passed"] = float(r["checks_passed"])
            p["pipe/cum_checks_pass_rate"] = ok / ok_total
        if r["hours_before_deadline"] is not None:
            p["pipe/hours_before_deadline"] = float(r["hours_before_deadline"])
        if r["total_seconds"] is not None:
            p["pipe/run_minutes"] = float(r["total_seconds"]) / 60
        if r["regenerated"] is not None:
            p["pipe/regenerated"] = float(r["regenerated"])
        degraded = [s for s in (r["degraded_steps"] or "").split(",") if s.strip()]
        p["pipe/degraded_steps"] = float(len(degraded))
        if r["drift_alerts"] is not None:
            p["pipe/drift_alerts"] = float(r["drift_alerts"])
        out.append(p)
    return out


def build_dashboard_data(
    season: int,
    week: int,
    *,
    run_root: Path | None = None,
    paths: DataPaths | None = None,
    kind: str = "main",
    games: pl.DataFrame | None = None,
    scorecard: pl.DataFrame | None = None,
    scoreboard: pl.DataFrame | None = None,
    history: pl.DataFrame | None = None,
    cfg: dict | None = None,
) -> DashboardData:
    """The season to date for the run of `week` (graded weeks before it). Reads the same
    files as the digest and the drift checks; a missing file leaves its series out."""
    cfg = drift.resolve_cfg(cfg)
    memo: dict[str, DataPaths] = {}

    def get_paths() -> DataPaths:
        if "p" not in memo:
            memo["p"] = paths if paths is not None else ensure_data_root()
        return memo["p"]

    root = run_root if run_root is not None else get_paths().runs
    rows: dict[int, dict[str, float]] = {}
    data = DashboardData(season, week)

    # game model + watch list: the season scorecard (so the dashboard and the digest agree)
    if scorecard is None:
        sc_path = root / str(season) / "season_scorecard.parquet"
        scorecard = pl.read_parquet(sc_path) if sc_path.exists() else pl.DataFrame()
    if scorecard.height:
        sc = scorecard.filter((pl.col("season") == season) & (pl.col("week") < week))
        for pt in season_series(sc) if sc.height else []:
            _put(
                rows,
                int(pt["season/week"]),
                **{SCORECARD_KEYS[k]: v for k, v in pt.items() if k in SCORECARD_KEYS},
            )

    # rolling gap vs Elo and the calibration series: the graded saved predictions
    graded = pl.DataFrame()
    try:
        preds = load_saved(root, season, range(1, week), PRED_FILE)
        res = games if games is not None else read_games(get_paths())
        graded = drift.graded_games(preds, res)
    except Exception:  # noqa: BLE001 - these series are optional
        graded = pl.DataFrame()
    if graded.height:
        weekly = drift.weekly_brier(graded)
        for g in drift.rolling_gaps(weekly, int(cfg["rolling_weeks"]), drift.min_window_weeks(cfg)):
            _put(rows, g["week"], **{"game/rolling_gap_vs_elo": g["gap"]})
        graded_weeks = sorted(graded["week"].unique().to_list())
        for w in graded_weeks:
            upto = graded.filter(pl.col("week") <= w)
            floor, _ = drift.ece_noise(upto["home_win_prob"].to_numpy())
            _put(
                rows,
                w,
                **{
                    "cal/ece": M.ece(upto["home_win_prob"], upto["home_win"]),
                    "cal/ece_chance": floor,
                    "cal/games": upto.height,
                },
            )
        data.curve = M.reliability(graded["home_win_prob"], graded["home_win"])
        data.summary["season_ece"] = M.ece(graded["home_win_prob"], graded["home_win"])
        data.summary["games_graded"] = graded.height
        data.summary["ece_chance"] = drift.ece_noise(graded["home_win_prob"].to_numpy())[0]

    # player model: the accuracy scoreboard
    try:
        raw = (
            scoreboard
            if scoreboard is not None
            else drift.scoreboard_from_disk(season, root, get_paths)
        )
        sb = drift.prepare_scoreboard(raw, season, week)
    except Exception:  # noqa: BLE001
        sb = pl.DataFrame()
    if sb.height:
        window = int(cfg["rolling_weeks"])
        for g in [
            *GROUPS,
            *[x for x in sb["position_group"].unique().to_list() if x not in GROUPS],
        ]:
            slug = GROUP_SLUG.get(g, g.lower().replace("/", "_"))
            for s in drift.group_series(sb, g, 1):
                _put(rows, s["week"], **{f"player/improvement_{slug}": s["improvement"]})
            for s in drift.group_series(sb, g, None):
                _put(rows, s["week"], **{f"player/cum_improvement_{slug}": s["improvement"]})
            for s in drift.group_series(sb, g, window, drift.min_window_weeks(cfg)):
                _put(rows, s["week"], **{f"player/rolling_improvement_{slug}": s["improvement"]})
        weeks = sorted(sb["week"].unique().to_list())
        live_weeks = set(sb.filter(pl.col("mode") == "live")["week"].to_list())
        # "all" = the player projections: the team stat totals (P08, `TEAM` rows in the same
        # file) have their own `player/*_team` series but never enter the players' number
        sbp = sb.filter(pl.col("position_group") != "TEAM")
        for w in weeks:
            _put(
                rows,
                w,
                **{
                    "player/improvement_all": drift.improvement_pct(
                        sbp.filter(pl.col("week") == w)
                    ),
                    "player/cum_improvement_all": drift.improvement_pct(
                        sbp.filter(pl.col("week") <= w)
                    ),
                    "player/live": float(w in live_weeks),
                },
            )
        data.summary["player_cum_improvement_all"] = drift.improvement_pct(sbp)
        data.summary["player_weeks"] = len(weeks)
        data.summary["player_live_weeks"] = len(live_weeks)

    data.points = [rows[w] for w in sorted(rows)]
    data.graded_weeks = sorted(rows)

    # pipeline health
    try:
        hist = history if history is not None else read_history(history_path(root, season))
    except Exception:  # noqa: BLE001
        hist = None
    data.pipeline = pipeline_points(hist, season, week, kind)
    if data.pipeline:
        last = data.pipeline[-1]
        for k in ("pipe/cum_on_time_rate", "pipe/cum_checks_pass_rate"):
            if k in last:
                data.summary[k.removeprefix("pipe/")] = last[k]
        data.summary["pipeline_runs"] = len(data.pipeline)

    # headline numbers (the latest value of each cumulative series)
    for k in (
        "game/cum_brier_model",
        "game/cum_brier_elo",
        "game/cum_brier_market",
        "game/cum_pick_accuracy",
    ):
        vals = [p[k] for p in data.points if k in p]
        if vals:
            data.summary[k.removeprefix("game/")] = vals[-1]
    data.summary["through_week"] = week - 1
    data.summary["graded_weeks"] = len([p for p in data.points if "game/brier_model" in p])
    return data


# ---- logging to W&B -----------------------------------------------------------------------------


def _log_data(run: Any, data: DashboardData) -> None:
    import wandb

    run.define_metric("dash/week")
    run.define_metric("pipe/week")
    run.define_metric("calcurve/predicted")
    for prefix in ("game", "watch", "cal", "player"):
        run.define_metric(f"{prefix}/*", step_metric="dash/week")
    run.define_metric("pipe/*", step_metric="pipe/week")
    run.define_metric("calcurve/*", step_metric="calcurve/predicted")
    for point in data.points:
        run.log(point)
    for point in data.pipeline:
        run.log(point)
    if data.curve.height:
        c = data.curve
        for r in c.iter_rows(named=True):
            run.log(
                {
                    "calcurve/predicted": r["mean_prob"],
                    "calcurve/observed": r["mean_outcome"],
                    "calcurve/perfect": r["mean_prob"],
                }
            )
        table = wandb.Table(
            data=[
                [r["lo"], r["hi"], r["n"], r["mean_prob"], r["mean_outcome"]]
                for r in c.iter_rows(named=True)
            ],
            columns=["bin_from", "bin_to", "games", "predicted", "observed"],
        )
        run.log(
            {
                "calibration_curve": table,
                "calibration_curve_chart": wandb.plot.line(
                    table, "predicted", "observed", title="Calibration curve (season to date)"
                ),
            }
        )
    for k, v in data.summary.items():
        run.summary[k] = v


def mark_current(entity: str, project: str, run_id: str, season: int, group: str = GROUP) -> int:
    """Tag `run_id` `dashboard-current` and take the tag off the season's other dashboard runs
    (the report only reads the tagged run). Returns how many older runs were untagged."""
    import wandb

    api = wandb.Api()
    new = api.run(f"{entity}/{project}/{run_id}")
    if CURRENT_TAG not in new.tags:
        new.tags = [*new.tags, CURRENT_TAG]
        new.update()
    older = api.runs(
        f"{entity}/{project}",
        filters={
            "$and": [
                {"group": group},
                {"tags": CURRENT_TAG},
                {"tags": f"season:{season}"},
            ]
        },
    )
    n = 0
    for r in older:
        if r.id != run_id:
            r.tags = [t for t in r.tags if t != CURRENT_TAG]
            r.update()
            n += 1
    return n


def log_season_dashboard(
    season: int,
    week: int,
    *,
    launched_by: str | None = None,
    run_root: Path | None = None,
    kind: str = "main",
    log: Callable[[str], None] = print,
    smoke: bool | None = None,
) -> str | None:
    """Log the season to date (graded through `week - 1`) as one W&B run and make it the run
    the "<season> Season Dashboard" report shows. Returns the run URL, or None when logging
    failed (a line says why; nothing is raised).

    `run_root`: live runs folder (default) or a simulation's (`digest-backtests`). A
    simulation, or `smoke=True`, logs to group `season-dashboard-smoke` with tag `smoke`
    (and gets `dashboard-current` only among the smoke runs), so it never reaches the real
    report.
    """
    run = None
    try:
        from nflengine.tracking import dataset_version, git_commit, init_run

        paths = ensure_data_root()
        root = run_root if run_root is not None else paths.runs
        if smoke is None:
            smoke = Path(root).resolve() != paths.runs.resolve()
        data = build_dashboard_data(season, week, run_root=root, paths=paths, kind=kind)
        tags = ["p07", "dashboard", f"season:{season}", f"week:{week:02d}"]
        name = f"season-{season}-w{week:02d}"
        if smoke:
            tags.append("smoke")
            name = f"smoke-{name}"
        run = init_run(
            group=GROUP_SMOKE if smoke else GROUP,
            job_type="dashboard",
            config={
                "season": season,
                "week": week,
                "through_week": week - 1,
                "kind": kind,
                "source": "simulation" if smoke else "live",
                "git_commit": git_commit(),
                "dataset_version": dataset_version(paths),
            },
            tags=tags,
            launched_by=launched_by,
            name=name,
        )
        _log_data(run, data)
        url, run_id, entity, project = run.url, run.id, run.entity, run.project
        run.finish()
        run = None
        label = "season dashboard (smoke)" if smoke else "season dashboard"
        try:
            n = mark_current(entity, project, run_id, season, GROUP_SMOKE if smoke else GROUP)
            log(f"{label}: {url} (now the report's run; untagged {n} older)")
        except Exception as e:  # noqa: BLE001
            log(
                f"{label}: {url} logged, but could not mark it current "
                f"({type(e).__name__}); the report may still show an older run."
            )
        if not smoke:
            _remember_run(paths, season, week, run_id, url)
        return url
    except Exception as e:  # noqa: BLE001 - fail soft: never raise into the weekly pipeline
        log(f"season dashboard: skipped ({type(e).__name__})")  # never the message (secrets)
        if run is not None:
            with contextlib.suppress(Exception):
                run.finish(exit_code=1)
        return None


def dashboard_file(paths: DataPaths, season: int) -> Path:
    return paths.runs / str(season) / DASHBOARD_FILE


def read_dashboard_info(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def write_dashboard_info(path: Path, **update: Any) -> dict[str, Any]:
    info = {**read_dashboard_info(path), **update}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def _remember_run(paths: DataPaths, season: int, week: int, run_id: str, url: str) -> None:
    with contextlib.suppress(OSError):
        write_dashboard_info(
            dashboard_file(paths, season), last_run_id=run_id, last_run_url=url, last_run_week=week
        )


# ---- the W&B report -----------------------------------------------------------------------------

W = "dash/week"  # x for the graded-week series
PIPE = "pipe/week"  # x for the run-week series


def _plot(title: str, y: list[str], *, x: str = W, ytitle: str | None = None, **kw: Any) -> dict:
    return {"title": title, "x": x, "y": y, "title_y": ytitle, **kw}


def _group_metrics(prefix: str) -> list[str]:
    return [f"player/{prefix}_{GROUP_SLUG[g]}" for g in GROUPS]


def report_sections(cfg: dict[str, Any]) -> list[dict]:
    """The report's sections: a heading, how to read them, and the line plots (specs)."""
    roll, streak = cfg["rolling_weeks"], cfg["streak_weeks"]
    return [
        {
            "title": "Game model",
            "text": (
                "One point per **graded week** (the run of week N grades weeks before N), from the "
                "season scorecard, the same numbers as the digest's report card. **Brier** is the "
                "average squared miss of the win probability: lower is better and 0.25 is a coin "
                "flip. The model should sit below Elo and near the market. For reference, over the "
                "2018-2025 walk-forward backtests the model averaged 0.2199, Elo 0.2221 and the "
                "market 0.2104, and single weeks ranged from about 0.17 to 0.32, so only a run of "
                "bad weeks matters. The gap chart is the drift check's input: it is the "
                f"{roll}-week game-weighted Brier of the model minus Elo's. Above zero the model "
                f"is behind; {streak} windows in a row above zero raises a drift alert."
            ),
            "plots": [
                _plot(
                    "Brier by week: model, Elo, market (lower is better)",
                    ["game/brier_model", "game/brier_elo", "game/brier_market"],
                    ytitle="Brier",
                ),
                _plot(
                    "Season-to-date Brier (lower is better)",
                    ["game/cum_brier_model", "game/cum_brier_elo", "game/cum_brier_market"],
                    ytitle="Brier",
                ),
                _plot(
                    f"Rolling {roll}-week Brier gap, model minus Elo (above 0 = behind Elo)",
                    ["game/rolling_gap_vs_elo"],
                    ytitle="Brier gap",
                ),
                _plot(
                    "Pick accuracy: each week and season to date",
                    ["game/pick_accuracy", "game/cum_pick_accuracy"],
                    ytitle="share of winners called",
                ),
                _plot("Points error (average miss on each team's score)", ["game/points_mae"]),
            ],
        },
        {
            "title": "Calibration",
            "text": (
                "Does a 70% call win about 70% of the time? In the **curve**, games are put in ten "
                "probability bins; each point is a bin's average predicted probability (across) "
                "against how often the home team really won (up). Points on the diagonal are "
                "calibrated. With about 16 games a week the bins are small, so early-season "
                "points wobble. **ECE** is the average gap between the two, weighted by bin size. "
                "Even a perfectly calibrated model shows an ECE of about 0.08 on one season's "
                "games, so read the ECE next to the 'chance' line (what a perfect model would "
                "show on the same games). The drift alert fires above the higher of "
                f"{cfg['ece_max']} and the 90th percentile of that chance level (D75): about 0.16 "
                "after five weeks, 0.08 by week 18. Only a season ECE that stays clearly above "
                "the chance line is a calibration problem. "
                "The v0 model has no calibration layer to refit (D48); the response to a real "
                "alert is to check the walk-forward backtest before turning one on."
            ),
            "plots": [
                _plot(
                    "Calibration curve, season to date (on the diagonal = calibrated)",
                    ["calcurve/observed", "calcurve/perfect"],
                    x="calcurve/predicted",
                    ytitle="home team won (share)",
                    xtitle="predicted home win probability",
                    range_x=(0, 1),
                    range_y=(0, 1),
                ),
                _plot(
                    "Season ECE against the chance level (a perfect model's ECE)",
                    ["cal/ece", "cal/ece_chance"],
                    ytitle="ECE",
                ),
                _plot("Graded games so far", ["cal/games"], ytitle="games"),
            ],
        },
        {
            "title": "Watch list",
            "text": (
                "A watch-list pick **hits** when the player beats his own rolling baseline. A hit "
                "rate means little without the base rate: yardage is right-skewed, so even all "
                "eligible players beat their baseline only about 43% of the time (2024-2025). "
                "The model's picks should land above that line over a season; one week of 10-20 "
                "picks swings a lot."
            ),
            "plots": [
                _plot(
                    "Watch-list hit rate: each week and season to date",
                    ["watch/hit_rate", "watch/cum_hit_rate"],
                    ytitle="share of picks that hit",
                )
            ],
        },
        {
            "title": "Player model",
            "text": (
                "How much lower the model's average miss (MAE) is than the player's rolling "
                "baseline, in percent: 5 means the model misses by 5% less. Below zero the model "
                "is worse than the baseline. Each target is compared on its own scale and the "
                "targets in a position group are averaged by how many players were scored. "
                "Walk-forward weeks run about 4-8% better in 2025 and early 2026. "
                "Weeks 1-3 of 2026 are walk-forward backtest rows (the live pipeline started in "
                f"week 4); `player/live` is 0 for those weeks and 1 for live ones. The {roll}-week "
                f"chart is the drift check's input: a group below zero for {streak} windows in a "
                "row raises an alert."
            ),
            "plots": [
                _plot(
                    "MAE improvement over the baseline, by week and position group (%)",
                    _group_metrics("improvement"),
                    ytitle="% better than baseline",
                ),
                _plot(
                    "MAE improvement, season to date (%)",
                    _group_metrics("cum_improvement"),
                    ytitle="% better than baseline",
                ),
                _plot(
                    f"MAE improvement, rolling {roll} weeks (%): the drift check's input",
                    _group_metrics("rolling_improvement"),
                    ytitle="% better than baseline",
                ),
                _plot(
                    "All groups together (%)",
                    ["player/improvement_all", "player/cum_improvement_all"],
                    ytitle="% better than baseline",
                ),
                _plot("Live (1) or walk-forward backtest (0) rows", ["player/live"]),
            ],
        },
        {
            "title": "Pipeline health",
            "text": (
                "One point per **run week** (the week the run was for), from the pipeline history. "
                "**On time** is 1 when the digest was published before the week's first kickoff. "
                "Hours before the deadline should stay comfortably positive. **Checks passed** is "
                "1 when the digest's final checks passed; **regenerated** is 1 when the writer "
                "needed a second pass; degraded steps count fail-soft steps that fell back (a "
                "graph without Neo4j, for example). More than "
                f"{cfg['checks_fail_rate_max']:.0%} failed digests over "
                f"{cfg['checks_window_weeks']} weeks raises a drift alert."
            ),
            "plots": [
                _plot(
                    "Delivered before the first kickoff (1 = on time) and the running rate",
                    ["pipe/on_time", "pipe/cum_on_time_rate"],
                    x=PIPE,
                ),
                _plot("Hours before the deadline", ["pipe/hours_before_deadline"], x=PIPE),
                _plot("Run time (minutes)", ["pipe/run_minutes"], x=PIPE),
                _plot(
                    "Digest checks passed (1 = yes) and the running rate",
                    ["pipe/checks_passed", "pipe/cum_checks_pass_rate"],
                    x=PIPE,
                ),
                _plot(
                    "Second writer pass, degraded steps, drift alerts",
                    ["pipe/regenerated", "pipe/degraded_steps", "pipe/drift_alerts"],
                    x=PIPE,
                ),
            ],
        },
    ]


HEADLINES = [
    ("Season Brier: model", "cum_brier_model"),
    ("Season Brier: Elo", "cum_brier_elo"),
    ("Season Brier: market", "cum_brier_market"),
    ("Season pick accuracy", "cum_pick_accuracy"),
    ("Season ECE", "season_ece"),
    ("Player MAE vs baseline (%)", "player_cum_improvement_all"),
    ("On-time rate", "cum_on_time_rate"),
    ("Checks pass rate", "cum_checks_pass_rate"),
]


def _report_url(report: Any) -> str:
    """The report's address with forward slashes (the library joins it with os.path on Windows)."""
    return str(report.url).replace("\\", "/")


def report_filters(season: int, group: str = GROUP) -> str:
    return f"Group = '{group}' and Tags in ['{CURRENT_TAG}'] and Tags in ['season:{season}']"


def build_report(
    season: int,
    *,
    log: Callable[[str], None] = print,
    smoke: bool = False,
    cfg: dict | None = None,
    url: str | None = None,
) -> str:
    """Create the W&B Report "<season> Season Dashboard", or update it in place when
    `runs/<season>/dashboard.json` already has its URL. Returns the report URL.

    The report is a live query: every panel reads the runs of group `season-dashboard`
    tagged `dashboard-current` and `season:<season>` (one run, the newest weekly run), shows
    only that run, and so redraws by itself each time `log_season_dashboard` moves the tag.
    Re-run this only to change the layout or text. `smoke=True` builds "SMOKE ..." from the
    smoke group and neither reads nor writes `dashboard.json`. `url` updates that report in
    place instead of the one in `dashboard.json`.
    """
    import wandb_workspaces.reports.v2 as wr

    from nflengine.tracking import _wandb_env

    cfg = drift.resolve_cfg(cfg)
    project, entity, paths = _wandb_env()
    if not entity:
        import wandb

        entity = wandb.Api().default_entity
    group = GROUP_SMOKE if smoke else GROUP
    title = f"{'SMOKE ' if smoke else ''}{season} Season Dashboard"
    info_path = dashboard_file(paths, season)
    info = {} if smoke else read_dashboard_info(info_path)

    runset = wr.Runset(
        entity=entity,
        project=project,
        name="Current dashboard run",
        filters=report_filters(season, group),
        order=[wr.OrderBy("CreatedTimestamp", ascending=False)],
    )

    def panel(spec: dict, i: int):
        return wr.LinePlot(
            title=spec["title"],
            x=spec["x"],
            y=spec["y"],
            title_x=spec.get("xtitle"),
            title_y=spec.get("title_y"),
            range_x=spec.get("range_x", (None, None)),
            range_y=spec.get("range_y", (None, None)),
            max_runs_to_show=1,
            legend_position="south",
            layout=wr.Layout(x=(i % 2) * 12, y=(i // 2) * 8, w=12, h=8),
        )

    blocks: list[Any] = [
        wr.MarkdownBlock(
            text=(
                f"The {season} season so far, as the digest has been scoring itself. "
                "Every chart reads **one** run, the newest weekly dashboard run "
                f"(tag `{CURRENT_TAG}`), so this page redraws by itself after each weekly run. "
                "Charts use the **graded week** on the x-axis (the run of week N grades the weeks "
                "before N); the pipeline charts use the **run week**. "
                "Nothing here changes a model: drift alerts only tell a person to look."
            )
        ),
        wr.H2("Season at a glance"),
        wr.PanelGrid(
            runsets=[runset],
            panels=[
                wr.ScalarChart(
                    title=label,
                    metric=key,
                    groupby_aggfunc="mean",
                    layout=wr.Layout(x=(i % 4) * 6, y=(i // 4) * 4, w=6, h=4),
                )
                for i, (label, key) in enumerate(HEADLINES)
            ],
        ),
    ]
    for sec in report_sections(cfg):
        blocks += [
            wr.H2(sec["title"]),
            wr.MarkdownBlock(text=sec["text"]),
            wr.PanelGrid(
                runsets=[runset],
                panels=[panel(s, i) for i, s in enumerate(sec["plots"])],
            ),
        ]

    description = (
        "Season-to-date scorecard of the NFL digest: game model, calibration, watch list, "
        "player model and pipeline health. Updates by itself after each weekly run."
    )
    url = url or info.get("url")
    report = None
    if url:
        try:
            report = wr.Report.from_url(url)
            report.title, report.description, report.blocks = title, description, blocks
            report.save()
            log(f"season dashboard report updated: {_report_url(report)}")
        except Exception as e:  # noqa: BLE001 - e.g. the report was deleted: make a new one
            log(f"season dashboard report: could not update {url} ({type(e).__name__}); creating")
            report = None
    if report is None:
        report = wr.Report(
            project=project, entity=entity, title=title, description=description, blocks=blocks
        )
        report.save()
        log(f"season dashboard report created: {_report_url(report)}")
    if not smoke:
        write_dashboard_info(
            info_path,
            season=season,
            title=title,
            url=_report_url(report),
            report_id=report.id,
            entity=entity,
            project=project,
            filters=report_filters(season, group),
        )
    return _report_url(report)


def check_report(
    season: int, *, url: str | None = None, smoke: bool = False
) -> list[dict[str, Any]]:
    """Read the report back and count, for the run it shows, the points behind every panel.

    One dict per plotted metric: `section`, `panel`, `metric`, `points` (history rows that
    have both the metric and the panel's x value; for a scalar tile, 1 when the run's summary
    has the key). A panel with 0 points renders empty: expected for the pipeline panels until
    `pipeline_history.parquet` exists, and for the calibration and game panels before a week
    has been graded. Also resolves the run the report's filters pick (tag `dashboard-current`).
    """
    import wandb
    import wandb_workspaces.reports.v2 as wr

    from nflengine.tracking import _wandb_env

    project, entity, paths = _wandb_env()
    api = wandb.Api()
    entity = entity or api.default_entity
    group = GROUP_SMOKE if smoke else GROUP
    url = url or read_dashboard_info(dashboard_file(paths, season)).get("url")
    if not url:
        raise ValueError(f"no report URL for {season}: run build_report first")
    report = wr.Report.from_url(url)
    runs = list(
        api.runs(
            f"{entity}/{project}",
            filters={
                "$and": [{"group": group}, {"tags": CURRENT_TAG}, {"tags": f"season:{season}"}]
            },
            order="-created_at",
        )
    )
    if not runs:
        return [{"section": "-", "panel": "-", "metric": "no current run", "points": 0}]
    run = runs[0]
    out: list[dict[str, Any]] = []
    section = ""
    for block in report.blocks:
        if isinstance(block, wr.H2):
            section = str(block.text)
        if not isinstance(block, wr.PanelGrid):
            continue
        for panel in block.panels:
            if isinstance(panel, wr.ScalarChart):
                key = getattr(panel.metric, "name", panel.metric)
                out.append(
                    {
                        "section": section,
                        "panel": panel.title,
                        "metric": key,
                        "points": int(run.summary.get(key) is not None),
                    }
                )
            elif isinstance(panel, wr.LinePlot):
                x = getattr(panel.x, "name", panel.x)
                for y in panel.y:
                    key = getattr(y, "name", y)
                    n = sum(1 for _ in run.scan_history(keys=[x, key]))
                    out.append(
                        {"section": section, "panel": panel.title, "metric": key, "points": n}
                    )
    return out
