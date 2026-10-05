"""The end-of-season review and the weekly log lines (P10; plans/P10-season-operations.md).

`build_season_review` collects, from the records a season leaves behind, everything the
review discusses. `render_review` turns it into one Markdown file for a person to read
(plain language, short sentences, "lower is better" wherever a score is). `week_lines` is the
one-line-per-week log for PROGRESS. Nothing here trains, predicts, runs a pipeline step or
talks to W&B: it only reads files, so it is safe to run at any time.

Where the numbers come from, and why:

- **Games** are graded from the *saved* predictions (`runs/<season>/week<NN>/
  predictions_games.parquet`) against the curated final scores, with the report card's own
  functions (`grade_games`, `week_metrics`), so they equal what each weekly digest quoted. The
  season scorecard is **not** the source: it grades week N at the run of week N + 1, so it
  never holds the season's last week (nothing runs after it), nor a week no digest run
  followed, and it has no per-game rows (best and worst calls, the playoff split, the
  reliability bins). It is read for a reconciliation note, and as the watch-list fallback.
- **Player projections** come from the accuracy scoreboard (`drift.prepare_scoreboard`: one
  row per week, target and group; a live row wins over a walk-forward backtest row of the
  same week). Live and backtest rows are never mixed in one number.
- **Watch list**: the saved picks (`watchlist.parquet`, only those made before kickoff) are
  re-scored against what the players did (`digest.players.WatchScorer`, read-only), so the
  last week is covered too. Where that can't run (no data drive), the scorecard's counts are
  used and the biggest hits and misses are left out.
- **Pipeline**: the pipeline history (`ops/records.latest_runs`: one row per week), each week's
  `run_summary.json` (alerts), and `checks.json` / `digest.md` in the week folders. A week with
  a digest but no history row (an older digest backtest) counts as published with unknown
  timing.

Every input may be missing (mid-season, a fresh season, an unplugged drive): the section that
needs it says so in one line and the rest still renders. Failures are reported by type only,
never by message (a message can carry a secret; D44).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.digest import format as F
from nflengine.digest.names import nickname
from nflengine.digest.players import LIVE_SCOREBOARD, WatchScorer
from nflengine.digest.report_card import (
    PRED_FILE,
    WATCH_FILE,
    grade_games,
    load_saved,
    pre_kickoff,
    week_dir,
    week_metrics,
)
from nflengine.digest.run import read_games
from nflengine.models import metrics as M
from nflengine.models.player_schema import GROUPS
from nflengine.ops import drift
from nflengine.ops.records import RUN_SUMMARY_FILE, history_path, latest_runs, utc_now
from nflengine.paths import DataPaths, ensure_data_root

REVIEW_FILE = "season-review.md"
SCORECARD_FILE = "season_scorecard.parquet"
CHECKS_FILE = "checks.json"
DIGEST_FILE = "digest.md"
N_CALLS = 5  # rows in each best / worst list
MAX_WEEK = 30  # above any week of a season (22 with the playoffs)

# Numbers the season dashboard report quotes (ops/dashboard.py -> report_sections), kept here so
# the review reads against the same yardsticks.
COIN_FLIP_BRIER = 0.25
REF_BRIER = {"model": 0.2199, "Elo": 0.2221, "market": 0.2104}  # 2018-2025 walk-forward
WATCH_BASE_RATE = 0.43  # all eligible players beating their own baseline (2024-2025)

GROUP_ORDER = [*GROUPS, "CB/S", "TEAM"]
MODES = ("live", "backtest")
_UNREAD = "no data drive"  # note text when the data root can't be resolved

WEEKLY_SCHEMA: dict[str, pl.DataType] = {
    "week": pl.Int32(),
    "game_types": pl.String(),
    "games": pl.Int64(),
    "late": pl.Int64(),  # saved after kickoff (not graded)
    "picks_correct": pl.Int64(),
    "picks_total": pl.Int64(),
    "brier_model": pl.Float64(),
    "brier_elo": pl.Float64(),
    "brier_market": pl.Float64(),
    "points_mae": pl.Float64(),
    "player_improvement": pl.Float64(),
    "player_mode": pl.String(),
}
PIPE_SCHEMA: dict[str, pl.DataType] = {
    "week": pl.Int32(),
    "source": pl.String(),  # run record | digest only | none
    "published": pl.Boolean(),
    "status": pl.String(),
    "on_time": pl.Boolean(),
    "hours_before": pl.Float64(),  # before the first kickoff; negative = late
    "failed_step": pl.String(),
    "degraded_steps": pl.String(),
    "checks_passed": pl.Boolean(),
    "regenerated": pl.Boolean(),
    "banner": pl.Boolean(),
    "drift_alerts": pl.Int32(),
    "minutes": pl.Float64(),
}
# Columns the report card's metrics read; a saved file from before one existed gets nulls.
GAME_COLS: dict[str, pl.DataType] = {
    "elo_prob": pl.Float64(),
    "market_prob": pl.Float64(),
    "pred_home_points": pl.Float64(),
    "pred_away_points": pl.Float64(),
    "game_type": pl.String(),
    "home_team": pl.String(),
    "away_team": pl.String(),
}


@dataclass
class SeasonReview:
    """Everything the review shows. Every field has an empty default, so a season with no
    records still builds (and renders) without a special case."""

    season: int
    through_week: int | None = None  # the last graded week
    kind: str = "main"
    live: bool = True  # run records of the live pipeline (False: simulation / backtest digests)
    run_root: str = ""
    generated: str = ""
    read: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # reconciliation notes, data quirks
    # at a glance (see `_headline`)
    headline: dict[str, Any] = field(default_factory=dict)
    # game model vs Elo vs market
    weekly: pl.DataFrame = field(default_factory=pl.DataFrame)
    totals: dict[str, Any] = field(default_factory=dict)
    split: pl.DataFrame = field(default_factory=pl.DataFrame)  # regular season / playoffs / all
    wins: dict[str, int] = field(default_factory=dict)
    # calibration
    calibration: dict[str, Any] = field(default_factory=dict)
    reliability: pl.DataFrame = field(default_factory=pl.DataFrame)
    # player accuracy scoreboard
    scoreboard_weeks: dict[str, list[int]] = field(default_factory=dict)  # mode -> weeks
    players: pl.DataFrame = field(default_factory=pl.DataFrame)  # per target and mode
    groups: pl.DataFrame = field(default_factory=pl.DataFrame)  # per group and mode
    team: pl.DataFrame = field(default_factory=pl.DataFrame)  # TEAM rows, per target and mode
    # watch list
    watch: pl.DataFrame = field(default_factory=pl.DataFrame)  # per week
    watch_info: dict[str, Any] = field(default_factory=dict)
    watch_sides: pl.DataFrame = field(default_factory=pl.DataFrame)
    watch_hits: pl.DataFrame = field(default_factory=pl.DataFrame)
    watch_misses: pl.DataFrame = field(default_factory=pl.DataFrame)
    # digest checks and pipeline
    checks: dict[str, Any] = field(default_factory=dict)
    pipeline: pl.DataFrame = field(default_factory=pl.DataFrame)  # one row per expected week
    pipeline_stats: dict[str, Any] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    week_lines: list[str] = field(default_factory=list)
    # best and worst calls
    sure_right: pl.DataFrame = field(default_factory=pl.DataFrame)
    sure_wrong: pl.DataFrame = field(default_factory=pl.DataFrame)
    margin_misses: pl.DataFrame = field(default_factory=pl.DataFrame)


# ---- context and small helpers -------------------------------------------------------------------


class _Ctx:
    """The run root, the data paths (resolved only when something needs them) and the
    bookkeeping of what was read or missing."""

    def __init__(self, season: int, run_root: Path | None, paths: DataPaths | None):
        self.season = season
        self._run_root = Path(run_root) if run_root is not None else None
        self._paths = paths
        self.read: list[str] = []
        self.missing: list[str] = []
        self.notes: list[str] = []

    def paths(self) -> DataPaths:
        if self._paths is None:
            self._paths = ensure_data_root()
        return self._paths

    @property
    def root(self) -> Path:
        return self._run_root if self._run_root is not None else self.paths().runs

    @property
    def live(self) -> bool:
        """True for the live runs folder, False for a simulation's (`digest-backtests`)."""
        if self._run_root is None:
            return True
        try:
            return self._run_root.resolve() == self.paths().runs.resolve()
        except Exception:  # noqa: BLE001 - no data root: go by the folder name
            return self._run_root.name != "digest-backtests"

    @property
    def season_dir(self) -> Path:
        return self.root / str(self.season)

    def parquet(self, path: Path, label: str) -> pl.DataFrame | None:
        if not path.exists():
            self.missing.append(label)
            return None
        try:
            df = pl.read_parquet(path)
        except Exception as e:  # noqa: BLE001 - a truncated or locked file
            self.missing.append(f"{label} (unreadable: {type(e).__name__})")
            return None
        self.read.append(label)
        return df


def _ok(x: Any) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def _clean(x: Any) -> Any:
    """NaN -> None, so a table cell is either a number or '-'."""
    return x if _ok(x) else None


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _utc(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def _f(x: Any, nd: int = 3) -> str:
    return f"{x:.{nd}f}" if _ok(x) else "-"


def _pc(x: Any, nd: int = 0) -> str:
    return f"{100 * x:.{nd}f}%" if _ok(x) else "-"


def _signed_pct(x: Any) -> str:
    """A percent that already is one (+4.5 -> "+4.5%")."""
    return f"{x:+.1f}%" if _ok(x) else "-"


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _weeks_text(weeks: Iterable[int]) -> str:
    ws = sorted({int(w) for w in weeks})
    if not ws:
        return "none"
    runs: list[str] = []
    start = prev = ws[0]
    for w in [*ws[1:], None]:
        if w is not None and w == prev + 1:
            prev = w
            continue
        runs.append(str(start) if start == prev else f"{start}{F.EN_DASH}{prev}")
        if w is not None:
            start = prev = w
    return ", ".join(runs)


def _weeks_label(weeks: Iterable[int]) -> str:
    """ "week 5" or "weeks 3-13, 15" (lower case; `.capitalize()` it to start a sentence)."""
    ws = sorted({int(w) for w in weeks})
    return f"{'week' if len(ws) == 1 else 'weeks'} {_weeks_text(ws)}"


def _table(headers: list[str], rows: list[list[Any]], align: str | None = None) -> list[str]:
    """A Markdown table; `align` is one letter per column (`l` / `r`), default left then right."""
    align = align or "l" + "r" * (len(headers) - 1)

    def line(cells: Iterable[Any]) -> str:
        return "| " + " | ".join(str(c).replace("|", "\\|") for c in cells) + " |"

    sep = "|" + "|".join(" ---: " if a == "r" else " --- " for a in align) + "|"
    return [line(headers), sep, *[line(r) for r in rows], ""]


def _cfg(cfg: dict | None) -> dict[str, Any]:
    try:
        return drift.resolve_cfg(cfg)
    except Exception:  # noqa: BLE001 - settings unreadable: the built-in thresholds
        return dict(drift.DEFAULTS)


# ---- games ---------------------------------------------------------------------------------------


def _saved_weeks(season_dir: Path, bound: int | None) -> list[int]:
    """Weeks that have a run folder (at most `bound`), in order."""
    if not season_dir.exists():
        return []
    weeks = []
    for p in season_dir.glob("week[0-9][0-9]"):
        if p.is_dir() and (bound is None or int(p.name[4:]) <= bound):
            weeks.append(int(p.name[4:]))
    return sorted(weeks)


def _grade_season(preds: pl.DataFrame, games: pl.DataFrame | None) -> pl.DataFrame:
    """The digest rows of the saved predictions joined to final scores (`grade_games`), the
    latest save of each game. Includes ungraded rows (`graded` false: no result yet, or saved
    after kickoff)."""
    if preds.is_empty() or games is None or games.is_empty():
        return pl.DataFrame()
    p = preds.with_columns(
        pl.lit(None, dtype=t).alias(c) for c, t in GAME_COLS.items() if c not in preds.columns
    )
    g = grade_games(p, games)
    if "created_at" in g.columns:
        g = g.sort("created_at")
    return g.unique(subset=["game_id"], keep="last", maintain_order=True)


def _metrics(g: pl.DataFrame) -> dict[str, Any]:
    """`report_card.week_metrics`, tolerating a file whose predicted points are all null."""
    try:
        return week_metrics(g)
    except TypeError:
        g = g.with_columns(
            pl.col("home_score").cast(pl.Float64).alias("pred_home_points"),
            pl.col("away_score").cast(pl.Float64).alias("pred_away_points"),
        )
        return {**week_metrics(g), "points_mae": None}


def _weekly_table(graded_all: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for w in sorted(graded_all.filter(pl.col("graded"))["week"].unique().to_list()):
        wk = graded_all.filter(pl.col("week") == w)
        m = _metrics(wk)
        types = sorted(wk.filter(pl.col("graded"))["game_type"].drop_nulls().unique().to_list())
        rows.append(
            {
                "week": w,
                "game_types": "/".join(types),
                "games": m["games"],
                "late": wk.filter(pl.col("result").is_not_null() & ~pl.col("graded")).height,
                "picks_correct": m["picks_correct"],
                "picks_total": m["picks_total"],
                "brier_model": _clean(m["brier_model"]),
                "brier_elo": _clean(m["brier_elo"]),
                "brier_market": _clean(m["brier_market"]),
                "points_mae": _clean(m["points_mae"]),
            }
        )
    return pl.DataFrame(rows, schema=WEEKLY_SCHEMA)


def _split_table(graded: pl.DataFrame) -> pl.DataFrame:
    """Regular season, playoffs and all games, when the graded games include playoff ones."""
    kind = pl.col("game_type").fill_null("REG")
    reg, post = graded.filter(kind == "REG"), graded.filter(kind != "REG")
    if post.is_empty():
        return pl.DataFrame()
    rows = []
    for label, part in (("Regular season", reg), ("Playoffs", post), ("All games", graded)):
        if part.is_empty():
            continue
        m = _metrics(part)
        rows.append(
            {
                "part": label,
                "weeks": part["week"].n_unique(),
                "games": m["games"],
                "picks_correct": m["picks_correct"],
                "picks_total": m["picks_total"],
                "brier_model": _clean(m["brier_model"]),
                "brier_elo": _clean(m["brier_elo"]),
                "brier_market": _clean(m["brier_market"]),
                "points_mae": _clean(m["points_mae"]),
            }
        )
    return pl.DataFrame(rows)


def _calibration(graded: pl.DataFrame, cfg: dict[str, Any]) -> tuple[dict[str, Any], pl.DataFrame]:
    if graded.is_empty():
        return {}, pl.DataFrame()
    p, y = graded["home_win_prob"], graded["home_win"]
    ece = M.ece(p, y)
    floor, floor90 = drift.ece_noise(p.to_numpy())
    need = int(cfg["ece_min_games"])
    games = graded.height
    if games < need:
        verdict = "few"
    elif _ok(ece) and _ok(floor90) and ece <= floor90:
        verdict = "within"
    else:
        verdict = "above"
    info = {
        "games": games,
        "ece": _clean(ece),
        "chance": _clean(floor),
        "chance_p90": _clean(floor90),
        "min_games": need,
        "verdict": verdict,
    }
    return info, M.reliability(p, y)


def _calls(graded: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """The most confident correct picks, the most confident wrong picks, and the games where
    the predicted margin missed the real one by the most."""
    if graded.is_empty():
        return pl.DataFrame(), pl.DataFrame(), pl.DataFrame()
    cols = [
        "week",
        "game_id",
        "home_team",
        "away_team",
        "home_win_prob",
        "fav_prob",
        "home_score",
        "away_score",
        "result",
    ]
    picks = graded.filter(pl.col("pick_correct").is_not_null()).select(*cols, "pick_correct")
    order = {"by": ["fav_prob", "game_id"], "descending": [True, False]}
    right = picks.filter(pl.col("pick_correct")).sort(**order).head(N_CALLS)
    wrong = picks.filter(~pl.col("pick_correct")).sort(**order).head(N_CALLS)
    margin = (
        graded.filter(pl.col("pred_home_points").is_not_null() & pl.col("result").is_not_null())
        .select(
            *cols, (pl.col("pred_home_points") - pl.col("pred_away_points")).alias("pred_margin")
        )
        .with_columns((pl.col("pred_margin") - pl.col("result")).abs().alias("miss"))
        .sort(["miss", "game_id"], descending=[True, False])
        .head(N_CALLS)
    )
    return right, wrong, margin


# ---- players -------------------------------------------------------------------------------------

SB_KEY = ["season", "week", "target", "position_group"]
OPTIONAL_SB = (
    "target_label",
    "mae_model",
    "mae_baseline",
    "coverage_80",
    "brier_model",
    "brier_baseline",
    "calibration_ece",
)


def _scoreboard_rows(raw: pl.DataFrame | None, season: int, limit: int) -> pl.DataFrame:
    """The season's scoreboard rows for weeks before `limit`, one per (week, target, group): the
    rows with an MAE and the rows with a Brier score (P08 chances), live over backtest."""
    parts = [
        df
        for m in ("mae", "brier")
        if (df := drift.prepare_scoreboard(raw, season, limit, m)).height
    ]
    if not parts:
        return pl.DataFrame()
    sb = pl.concat(parts, how="diagonal_relaxed").unique(
        subset=SB_KEY, keep="first", maintain_order=True
    )
    # columns an older file may lack (the P08 chance columns, the range coverage, the label)
    absent = [c for c in OPTIONAL_SB if c not in sb.columns]
    sb = sb.with_columns(
        pl.lit(None, dtype=pl.String if c == "target_label" else pl.Float64).alias(c)
        for c in absent
    )
    return sb.sort("week", "position_group", "target")


def _group_rank(g: str) -> int:
    return GROUP_ORDER.index(g) if g in GROUP_ORDER else len(GROUP_ORDER)


def _improvement(rows: pl.DataFrame, metric: str = "mae") -> float | None:
    """`drift.improvement_pct` on the rows that have the metric (it expects them filtered)."""
    m_col, b_col = drift.METRICS[metric]
    if rows.is_empty() or m_col not in rows.columns:
        return None
    rows = rows.filter(pl.col(m_col).is_not_null() & pl.col(b_col).is_not_null())
    return _clean(drift.improvement_pct(rows, metric))


def _by_target(sb: pl.DataFrame) -> pl.DataFrame:
    """Per (mode, group, target): scored projections, the n-weighted MAE of the model and of the
    baseline, how much better the model is, the 80% range coverage, and the Brier / ECE of
    the chances. Each average uses only the weeks that have that number."""
    n = pl.col("n_scored").cast(pl.Float64)

    def wmean(c: str) -> pl.Expr:
        return (pl.col(c) * n).sum() / n.filter(pl.col(c).is_not_null()).sum()

    have = [c for c in ("brier_model", "brier_baseline", "calibration_ece") if c in sb.columns]
    out = sb.group_by("mode", "position_group", "target", "target_label").agg(
        pl.col("week").n_unique().alias("weeks"),
        n.sum().cast(pl.Int64).alias("n"),
        wmean("mae_model").alias("mae_model"),
        wmean("mae_baseline").alias("mae_baseline"),
        wmean("coverage_80").alias("coverage"),
        *[wmean(c).alias(c) for c in have],
    )
    for c in ("brier_model", "brier_baseline", "calibration_ece"):
        if c not in out.columns:
            out = out.with_columns(pl.lit(None, pl.Float64).alias(c))
    out = out.with_columns(
        pl.when((pl.col("mae_baseline") > 0) & pl.col("mae_model").is_not_null())
        .then(100 * (pl.col("mae_baseline") - pl.col("mae_model")) / pl.col("mae_baseline"))
        .alias("improvement"),
        pl.col("position_group")
        .replace_strict({g: _group_rank(g) for g in GROUP_ORDER}, default=99, return_dtype=pl.Int32)
        .alias("_rank"),
        pl.col("mode").replace_strict({"live": 0, "backtest": 1}, default=2).alias("_mode"),
    )
    return out.sort("_mode", "_rank", "target").drop("_rank", "_mode")


def _by_group(sb: pl.DataFrame) -> pl.DataFrame:
    """Per (mode, group): how much better than the baseline (MAE, and Brier for the chances)."""
    rows = []
    for mode in MODES:
        m = sb.filter(pl.col("mode") == mode)
        if m.is_empty():
            continue
        names = sorted(m["position_group"].unique().to_list(), key=_group_rank)
        parts = [(g, m.filter(pl.col("position_group") == g)) for g in names]
        players = m.filter(pl.col("position_group") != "TEAM")
        if players.height and len(names) > 1:
            parts.append(("All player groups", players))
        for g, part in parts:
            rows.append(
                {
                    "mode": mode,
                    "position_group": g,
                    "weeks": part["week"].n_unique(),
                    "n": int(part["n_scored"].sum()),
                    "improvement": _improvement(part),
                    "brier_improvement": _improvement(part, "brier"),
                }
            )
    schema = {
        "mode": pl.String,
        "position_group": pl.String,
        "weeks": pl.Int64,
        "n": pl.Int64,
        "improvement": pl.Float64,
        "brier_improvement": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema)


# ---- watch list ----------------------------------------------------------------------------------


def _stat(x: Any, unit: Any) -> str:
    """A projected or actual stat in its own unit: yards whole, counts and chances to a tenth."""
    if not _ok(x):
        return "-"
    return f"{x:.2f}" if unit == "epa" else (f"{x:.0f}" if unit in (None, "yards") else f"{x:.1f}")


def _watch_picks(played: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Model picks that played, ranked by how far the result landed from the player's own
    baseline, in widths of the pick's 80% range (so yards and tackles compare): the biggest
    beats (hits) and the biggest shortfalls (misses). Heuristic picks have no range."""
    need = ["p10", "p50", "p90", "baseline", "actual"]
    if played.is_empty() or not set(need) <= set(played.columns):
        return pl.DataFrame(), pl.DataFrame()
    m = played.filter(
        pl.all_horizontal([pl.col(c).is_not_null() for c in need]) & (pl.col("p90") > pl.col("p10"))
    )
    if m.is_empty():
        return pl.DataFrame(), pl.DataFrame()
    projection = pl.col("p50")
    if {"kind", "mean"} <= set(m.columns):  # a count's expected value, not its coarse median
        projection = (
            pl.when((pl.col("kind") == "count") & pl.col("mean").is_not_null())
            .then(pl.col("mean"))
            .otherwise(pl.col("p50"))
        )
    m = m.with_columns(
        ((pl.col("actual") - pl.col("baseline")) / (pl.col("p90") - pl.col("p10"))).alias("beat"),
        projection.alias("projection"),
    )
    cols = [
        c
        for c in (
            "week",
            "player",
            "team",
            "position",
            "target_label",
            "unit",
            "baseline",
            "projection",
            "p10",
            "p90",
            "actual",
            "inside",
            "beat",
        )
        if c in m.columns
    ]
    hits = m.filter(pl.col("beat") > 0).sort("beat", descending=True).head(N_CALLS).select(cols)
    misses = m.filter(pl.col("beat") < 0).sort("beat").head(N_CALLS).select(cols)
    return hits, misses


def _watch_section(
    ctx: _Ctx,
    rv: SeasonReview,
    weeks: list[int],
    score_watch: Callable[[pl.DataFrame], pl.DataFrame] | None,
    scorecard: pl.DataFrame | None,
) -> None:
    """Fill the watch-list fields: re-scored saved picks, else the scorecard's counts."""
    watch = pre_kickoff(load_saved(ctx.root, ctx.season, weeks, WATCH_FILE))
    scored = pl.DataFrame()
    if watch.height:
        try:
            scorer = score_watch if score_watch is not None else WatchScorer(ctx.paths())
            scored = scorer(watch)
            if not {"played", "hit"} <= set(scored.columns):
                raise ValueError("unscored")
        except Exception as e:  # noqa: BLE001
            ctx.notes.append(
                f"The saved watch-list picks could not be re-scored ({type(e).__name__}); the "
                "scorecard's counts are used, without the biggest hits and misses."
            )
            scored = pl.DataFrame()
    sc_rows = pl.DataFrame()
    if scorecard is not None and scorecard.height and "watchlist_total" in scorecard.columns:
        sc_rows = scorecard.filter(pl.col("watchlist_total").fill_null(0) > 0)
    if scored.height:
        ctx.read.append(f"{WATCH_FILE} (re-scored)")
        played = scored.filter(pl.col("played").fill_null(False))
        hit = pl.col("hit").fill_null(False)
        has_inside = "inside" in played.columns
        by_week = (
            played.group_by("week")
            .agg(
                pl.len().alias("picks"),
                hit.sum().cast(pl.Int64).alias("hits"),
                (pl.col("inside").fill_null(False).sum() if has_inside else pl.lit(0)).alias(
                    "inside"
                ),
                (pl.col("inside").is_not_null().sum() if has_inside else pl.lit(0)).alias("ranged"),
            )
            .sort("week")
        )
        rv.watch = by_week
        rv.watch_info = {"source": "re-scored saved picks"}
        if "side" in played.columns:
            rv.watch_sides = (
                played.filter(pl.col("side").is_not_null())
                .group_by("side")
                .agg(pl.len().alias("picks"), hit.sum().cast(pl.Int64).alias("hits"))
                .sort("side", descending=True)  # offense before defense
            )
        rv.watch_hits, rv.watch_misses = _watch_picks(played)
        if sc_rows.height:  # the scorecard is what the digests quoted: say if it disagrees
            common = by_week.filter(pl.col("week").is_in(sc_rows["week"].to_list()))
            sc_hits, sc_total = (
                int(sc_rows["watchlist_hits"].sum()),
                int(sc_rows["watchlist_total"].sum()),
            )
            if (int(common["hits"].sum()), int(common["picks"].sum())) != (sc_hits, sc_total):
                ctx.notes.append(
                    f"Watch-list counts differ for {_weeks_label(sc_rows['week'].to_list())}: "
                    f"{int(common['hits'].sum())} of {int(common['picks'].sum())} when the saved "
                    f"picks are scored again now, {sc_hits} of {sc_total} in the scorecard. The "
                    "scorecard keeps what that week's digest run recorded; the re-score uses "
                    "the picks saved in each week folder and today's player stats."
                )
    elif sc_rows.height:
        rv.watch = sc_rows.select(
            "week",
            pl.col("watchlist_total").alias("picks"),
            pl.col("watchlist_hits").alias("hits"),
            pl.lit(0).alias("inside"),
            pl.lit(0).alias("ranged"),
        ).sort("week")
        rv.watch_info = {"source": "season scorecard"}
    if rv.watch.height:
        picks, hits = int(rv.watch["picks"].sum()), int(rv.watch["hits"].sum())
        rv.watch_info |= {
            "picks": picks,
            "hits": hits,
            "rate": hits / picks if picks else None,
            "inside": int(rv.watch["inside"].sum()),
            "ranged": int(rv.watch["ranged"].sum()),
        }


# ---- digest checks and the pipeline --------------------------------------------------------------


def _kickoffs(games: pl.DataFrame | None, season: int) -> dict[int, dt.datetime]:
    """The first kickoff of each week of the season."""
    if (
        games is None
        or games.is_empty()
        or not {"season", "week", "kickoff_utc"} <= set(games.columns)
    ):
        return {}
    g = games.filter(pl.col("season") == season).group_by("week").agg(pl.col("kickoff_utc").min())
    return {int(w): _utc(k) for w, k in zip(g["week"], g["kickoff_utc"], strict=True) if k}


def _pipeline_rows(
    ctx: _Ctx,
    kind: str,
    bound: int | None,
    kickoffs: dict[int, dt.datetime],
    as_of: dt.datetime | None,
) -> tuple[pl.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    """One row per expected run week (the pipeline history, else the week's own files), the
    alerts of each week's `run_summary.json`, and an info dict: `runs` (all runs of the kind by
    status, retries included), `summaries` (run summaries read) and `first_failed` (which
    checks failed first time).

    Expected weeks run from the first week with any record to the last, minus weeks the schedule
    doesn't have; with `as_of` the range grows to weeks whose first kickoff has passed (so a
    live week that never ran shows as missed)."""
    season = ctx.season
    hist = ctx.parquet(history_path(ctx.root, season), "pipeline_history.parquet")
    runs: dict[int, dict[str, Any]] = {}
    counts: Counter = Counter()
    if hist is not None and hist.height:
        try:
            mine = hist.filter(pl.col("season") == season)
            of_kind = mine.filter(pl.col("kind") == kind)
            if bound is not None:
                of_kind = of_kind.filter(pl.col("week") <= bound)
            counts = Counter(of_kind["status"].to_list())
            for r in latest_runs(mine, kind).iter_rows(named=True):
                runs[int(r["week"])] = r
            if not runs and mine.height:
                kinds = sorted(mine["kind"].unique().to_list())
                ctx.notes.append(
                    f"The pipeline history holds runs of kind {', '.join(kinds)} and none of "
                    f"kind `{kind}`; build the review with kind=`{kinds[0]}` to see them."
                )
        except Exception as e:  # noqa: BLE001 - an old file without the current columns
            ctx.notes.append(f"The pipeline history could not be read ({type(e).__name__}).")
            runs, counts = {}, Counter()
    digest_weeks = {
        w
        for w in _saved_weeks(ctx.season_dir, bound)
        if (week_dir(ctx.root, season, w) / DIGEST_FILE).exists()
    }
    runs = {w: r for w, r in runs.items() if bound is None or w <= bound}
    recorded = set(runs) | digest_weeks
    weeks: list[int] = []
    if recorded:
        first, last = min(recorded), max(recorded)
        if as_of is not None:
            passed = [w for w, k in kickoffs.items() if k < as_of and (bound is None or w <= bound)]
            last = max([last, *passed])
        weeks = [
            w for w in range(first, last + 1) if not kickoffs or w in kickoffs or w in recorded
        ]

    rows: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    first_failed: Counter = Counter()
    summaries = 0
    for w in weeks:  # (each row below is completed to the full schema when appended)
        folder = week_dir(ctx.root, season, w)
        checks = _read_json(folder / CHECKS_FILE) or {}
        r = runs.get(w)
        if r is not None:
            published = r["on_time"] is not None or r["checks_passed"] is not None
            row = {
                "week": w,
                "source": "run record",
                "published": published,
                "status": r["status"],
                "on_time": r["on_time"],
                "hours_before": r["hours_before_deadline"],
                "failed_step": r["failed_step"],
                "degraded_steps": r["degraded_steps"],
                "checks_passed": r["checks_passed"],
                "regenerated": r["regenerated"],
                "banner": r["banner"],
                "drift_alerts": r["drift_alerts"],
                "minutes": r["total_seconds"] / 60 if r["total_seconds"] is not None else None,
            }
            if published and row["checks_passed"] is None and checks:
                row["checks_passed"] = checks.get("passed")
                row["regenerated"], row["banner"] = checks.get("regenerated"), checks.get("banner")
        elif w in digest_weeks:
            row = {
                "week": w,
                "source": "digest only",
                "published": True,
                "checks_passed": checks.get("passed"),
                "regenerated": checks.get("regenerated"),
                "banner": checks.get("banner"),
            }
        else:
            row = {"week": w, "source": "none", "published": False}
        rows.append({k: row.get(k) for k in PIPE_SCHEMA})
        if row["published"]:
            first_failed.update((checks.get("first_attempt") or {}).get("failed") or [])
        summary = _read_json(folder / RUN_SUMMARY_FILE)
        if summary and summary.get("kind", kind) == kind:
            summaries += 1
            for a in summary.get("alerts") or []:
                alerts.append(
                    {
                        "week": w,
                        "level": a.get("level"),
                        "source": a.get("source") or "",
                        "title": _short_title(str(a.get("title") or ""), season, w),
                    }
                )
    frame = pl.DataFrame(rows, schema=PIPE_SCHEMA)
    return (
        frame,
        alerts,
        {"runs": dict(counts), "summaries": summaries, "first_failed": first_failed},
    )


def _short_title(title: str, season: int, week: int) -> str:
    """An alert title without its "2026 week 5: " prefix (the table already has the week)."""
    prefix = f"{season} week {week}: "
    return title[len(prefix) :] if title.startswith(prefix) else title


def _pipeline_stats(
    frame: pl.DataFrame, run_counts: dict[str, int], summaries: int
) -> dict[str, Any]:
    pub = frame.filter(pl.col("published"))
    timed = pub.filter(pl.col("on_time").is_not_null())
    hours = [h for h in timed["hours_before"].to_list() if _ok(h)]
    return {
        "expected": frame.height,
        "published": pub.height,
        "recorded": frame.filter(pl.col("source") == "run record").height,
        "missed": frame.filter(pl.col("source") == "none").height,
        "timed": timed.height,
        "on_time": int(timed["on_time"].sum()) if timed.height else 0,
        "median_hours": statistics.median(hours) if hours else None,
        "degraded": frame.filter(pl.col("status") == "degraded").height,
        "failed": frame.filter((pl.col("status") == "failed") & ~pl.col("published")).height,
        "drift_alerts": int(frame["drift_alerts"].fill_null(0).sum()),
        "runs": dict(run_counts),
        "summaries": summaries,
    }


def _checks_stats(frame: pl.DataFrame, first_failed: Counter) -> dict[str, Any]:
    """Digest checks over the published weeks that have a verdict: passed first time, passed
    after the one regeneration, or went out with the warning banner (final checks failed)."""
    verdict = frame.filter(pl.col("published") & pl.col("checks_passed").is_not_null())
    if verdict.is_empty():
        return {}
    banner = ~pl.col("checks_passed") | pl.col("banner").fill_null(False)
    regen = pl.col("regenerated").fill_null(False)
    first = verdict.filter(~banner & ~regen)["week"].to_list()
    after = verdict.filter(~banner & regen)["week"].to_list()
    flagged = verdict.filter(banner)["week"].to_list()
    return {
        "weeks": verdict.height,
        "first_time": first,
        "regenerated": after,
        "banner": flagged,
        "first_failed": first_failed.most_common(),
        "source": sorted(verdict["source"].unique().to_list()),
    }


# ---- week lines ----------------------------------------------------------------------------------


def _week_line(r: dict[str, Any], titles: list[str]) -> str:
    head = f"week {int(r['week']):02d}:"
    if not r["published"]:
        if r["source"] == "none":
            return f"{head} not published (no run recorded)"
        if r["failed_step"]:
            why = f"failed at {r['failed_step']}"
        else:
            why = "the run failed" if r["status"] == "failed" else "no digest was written"
        return f"{head} not published ({why})"
    hours = r["hours_before"]
    if r["source"] == "digest only":
        lead = "published ✓ (digest found, no run record)"
    elif r["on_time"] is None:
        lead = "published ✓ (no deadline recorded)"
    elif r["on_time"]:
        lead = "published ✓ on time" + (f" ({hours:.1f} h before kickoff)" if _ok(hours) else "")
    else:
        late = f" ({abs(hours):.1f} h after the first kickoff)" if _ok(hours) else ""
        lead = "published ✓ late" + (late or " (after the first kickoff)")
    parts = [lead]
    if r["checks_passed"] is None:
        parts.append("no checks verdict")
    elif not r["checks_passed"] or r["banner"]:
        parts.append("checks failed, went out with the warning banner")
    elif r["regenerated"]:
        parts.append("checks passed after one regeneration")
    else:
        parts.append("checks passed first time")
    if r["status"] == "degraded":
        steps = r["degraded_steps"] or "a step"
        parts.append(f"degraded: {steps.replace(',', ', ')}")
    if r["drift_alerts"] is not None:
        n = int(r["drift_alerts"])
        text = _plural(n, "drift alert")
        shown = "; ".join(titles).replace("(", "[").replace(")", "]")
        parts.append(f"{text} ({shown})" if titles else text)
    return f"{head} " + ", ".join(parts)


def _lines_from(frame: pl.DataFrame, alerts: list[dict[str, Any]]) -> list[str]:
    out = []
    for r in frame.iter_rows(named=True):
        titles = [
            a["title"]
            for a in alerts
            if a["week"] == r["week"] and a["source"].startswith("drift:")
        ]
        out.append(_week_line(r, titles))
    return out


def _default_as_of(ctx: _Ctx, as_of: dt.datetime | None) -> dt.datetime | None:
    """`as_of`, else now for the live runs folder (weeks whose first kickoff has passed should
    have run) and None for a simulation (its weeks are not tied to today's date)."""
    if as_of is not None:
        return _utc(as_of)
    return utc_now() if ctx.live else None


def _try_games(ctx: _Ctx, games: pl.DataFrame | None) -> pl.DataFrame | None:
    if games is not None:
        return games
    try:
        games = read_games(ctx.paths())
        ctx.read.append("curated games.parquet (final scores)")
        return games
    except Exception as e:  # noqa: BLE001
        ctx.notes.append(
            f"The final scores could not be read ({type(e).__name__}), so no game was graded."
        )
        return None


def week_lines(
    season: int,
    *,
    run_root: Path | None = None,
    paths: DataPaths | None = None,
    kind: str = "main",
    as_of: dt.datetime | None = None,
    games: pl.DataFrame | None = None,
) -> list[str]:
    """One plain line per run week for the PROGRESS log, e.g. `week 05: published ✓ on time
    (58.2 h before kickoff), checks passed first time, 0 drift alerts`. A week with a digest
    but no history row says so; a week in the range with neither is `not published (no run
    recorded)`."""
    ctx = _Ctx(season, run_root, paths)
    try:
        ctx.root  # noqa: B018 - resolves the data root (may raise)
    except Exception:  # noqa: BLE001
        return []
    try:
        games = games if games is not None else read_games(ctx.paths())
    except Exception:  # noqa: BLE001 - without the schedule only recorded weeks are listed
        games = None
    frame, alerts, _ = _pipeline_rows(
        ctx, kind, None, _kickoffs(games, season), _default_as_of(ctx, as_of)
    )
    return _lines_from(frame, alerts)


# ---- build ---------------------------------------------------------------------------------------


def _player_headline(groups: pl.DataFrame) -> tuple[float | None, str | None]:
    """The players-vs-baseline percent over all player groups (the TEAM rows are not players):
    the live rows when there are any, else the backtest rows."""
    if groups.is_empty():
        return None, None
    for mode in MODES:
        rows = groups.filter((pl.col("mode") == mode) & (pl.col("position_group") != "TEAM"))
        if rows.is_empty():
            continue
        overall = rows.filter(pl.col("position_group") == "All player groups")
        pick = overall if overall.height else rows.head(1)  # one group has no "all" row
        if pick["improvement"][0] is not None:
            return pick["improvement"][0], mode
    return None, None


def _headline(rv: SeasonReview) -> dict[str, Any]:
    t, c, w = rv.totals, rv.calibration, rv.watch_info
    imp, mode = _player_headline(rv.groups)
    ps, ch = rv.pipeline_stats, rv.checks
    return {
        "games": t.get("games"),
        "weeks": t.get("weeks"),
        "week_range": _weeks_label(rv.weekly["week"].to_list()) if rv.weekly.height else None,
        "brier_model": t.get("brier_model"),
        "brier_elo": t.get("brier_elo"),
        "brier_market": t.get("brier_market"),
        "picks_correct": t.get("picks_correct"),
        "picks_total": t.get("picks_total"),
        "pick_accuracy": t.get("pick_accuracy"),
        "points_mae": t.get("points_mae"),
        "ece": c.get("ece"),
        "ece_chance": c.get("chance"),
        "watch_hits": w.get("hits"),
        "watch_picks": w.get("picks"),
        "watch_rate": w.get("rate"),
        "player_improvement": _clean(imp),
        "player_mode": mode,
        "weeks_published": ps.get("published"),
        "weeks_expected": ps.get("expected"),
        "on_time": ps.get("on_time"),
        "timed": ps.get("timed"),
        "first_try": len(ch.get("first_time", [])) if ch else None,
        "checked": ch.get("weeks") if ch else None,
    }


def build_season_review(
    season: int,
    *,
    run_root: Path | None = None,
    paths: DataPaths | None = None,
    through_week: int | None = None,
    kind: str = "main",
    as_of: dt.datetime | None = None,
    games: pl.DataFrame | None = None,
    score_watch: Callable[[pl.DataFrame], pl.DataFrame] | None = None,
    cfg: dict | None = None,
) -> SeasonReview:
    """Collect the review of `season` from the run records under `run_root`.

    `run_root`: the live runs folder (default `paths.runs`) or a simulation's
    (`paths.runs / "digest-backtests"`; its history holds `kind="simulation"` runs).
    `through_week`: the last graded week to include (default: the last graded week found in
    the saved predictions, the scorecard or the scoreboard); pipeline and digest sections
    cover the runs up to the week after it, because the run of week N grades week N - 1.
    `as_of`: weeks whose first kickoff is before it count as expected runs, so a live week that
    never ran shows as missed (default: now for the live folder, none for a simulation).
    `games` (curated results) and `score_watch` (a watch-list scorer) replace what is read
    from the data drive: tests, and a review built without it.
    """
    ctx = _Ctx(season, run_root, paths)
    explicit_through = through_week is not None
    rv = SeasonReview(season=season, kind=kind, generated=utc_now().strftime("%Y-%m-%d %H:%M UTC"))
    try:
        root = ctx.root
    except Exception as e:  # noqa: BLE001 - DataRootError: no drive, no env setting
        rv.notes.append(f"The data root could not be resolved ({type(e).__name__}).")
        rv.missing.append(_UNREAD)
        _finish(rv, ctx)
        return rv
    rv.run_root = str(root).replace("\\", "/")
    rv.live = ctx.live
    cfg = _cfg(cfg)
    games = _try_games(ctx, games)

    # games: the saved predictions graded against the final scores
    saved = [
        w
        for w in _saved_weeks(ctx.season_dir, through_week)
        if (week_dir(root, season, w) / PRED_FILE).exists()
    ]
    preds = load_saved(root, season, saved, PRED_FILE)
    if saved:
        ctx.read.append(f"{PRED_FILE} of {_plural(len(saved), 'week folder')}")
    else:
        ctx.missing.append(PRED_FILE)
    graded_all = _grade_season(preds, games)
    graded = (
        graded_all.filter(pl.col("graded") & pl.col("home_win_prob").is_not_null())
        if graded_all.height
        else pl.DataFrame()
    )

    scorecard = ctx.parquet(ctx.season_dir / SCORECARD_FILE, SCORECARD_FILE)
    if scorecard is not None:
        scorecard = scorecard.filter(
            (pl.col("season") == season) & (pl.col("week") <= (through_week or MAX_WEEK))
        )

    # player accuracy scoreboard (the live file also holds walk-forward rows of earlier weeks;
    # a simulation falls back to the backtest scoreboard)
    try:
        raw = drift.scoreboard_from_disk(season, root, ctx.paths)
    except Exception as e:  # noqa: BLE001
        raw = None
        ctx.missing.append(f"accuracy scoreboard (unreadable: {type(e).__name__})")
    try:
        sb = _scoreboard_rows(raw, season, (through_week or MAX_WEEK) + 1)
    except Exception as e:  # noqa: BLE001 - a file from before the current columns
        sb = pl.DataFrame()
        ctx.notes.append(f"The accuracy scoreboard could not be read ({type(e).__name__}).")
    if raw is not None:
        live_file = ctx.season_dir / LIVE_SCOREBOARD
        ctx.read.append(LIVE_SCOREBOARD if live_file.exists() else "backtest player scoreboard")
    elif not any("scoreboard" in m for m in ctx.missing):
        ctx.missing.append(LIVE_SCOREBOARD)

    # through week: the last graded week of the games or the scorecard; only without either
    # does the scoreboard decide (a simulation's backtest scoreboard covers the whole season)
    if through_week is None:
        found = [
            *(graded["week"].to_list() if graded.height else []),
            *(scorecard["week"].to_list() if scorecard is not None and scorecard.height else []),
        ] or (sb.filter(pl.col("position_group") != "TEAM")["week"].to_list() if sb.height else [])
        through_week = max(found) if found else None
        if through_week is not None and sb.height:
            sb = sb.filter(pl.col("week") <= through_week)
    rv.through_week = through_week
    # the pipeline sections stop at the run after `through_week` only when it was asked for:
    # a derived bound would hide an outage (no graded week after week 2, no runs in weeks 4-5;
    # Sol review, P10), so by default every week with a record or a passed kickoff counts
    run_bound = through_week + 1 if explicit_through and through_week is not None else None

    if graded.height:
        _fill_games(rv, graded_all, graded, cfg)
    _reconcile_scorecard(ctx, rv, scorecard, graded_all)
    if sb.height:
        _fill_players(rv, sb)
    _watch_section(
        ctx,
        rv,
        [w for w in saved if through_week is None or w <= through_week],
        score_watch,
        scorecard,
    )

    # pipeline and digest checks
    frame, alerts, info = _pipeline_rows(
        ctx, kind, run_bound, _kickoffs(games, season), _default_as_of(ctx, as_of)
    )
    rv.pipeline, rv.alerts = frame, alerts
    if frame.height:
        rv.pipeline_stats = _pipeline_stats(frame, info["runs"], info["summaries"])
        rv.checks = _checks_stats(frame, info["first_failed"])
        rv.week_lines = _lines_from(frame, alerts)
    if rv.weekly.height and rv.groups.height:  # the weekly table's player column
        rv.weekly = _with_player_columns(rv.weekly, sb)
    rv.headline = _headline(rv)
    _finish(rv, ctx)
    return rv


def _finish(rv: SeasonReview, ctx: _Ctx) -> None:
    rv.read, rv.missing, rv.notes = ctx.read, ctx.missing, [*rv.notes, *ctx.notes]


def _fill_games(
    rv: SeasonReview, graded_all: pl.DataFrame, graded: pl.DataFrame, cfg: dict[str, Any]
) -> None:
    rv.weekly = _weekly_table(graded_all)
    m = _metrics(graded)
    rv.totals = {
        **m,
        "weeks": graded["week"].n_unique(),
        "first_week": int(graded["week"].min()),
        "last_week": int(graded["week"].max()),
        "late": graded_all.filter(pl.col("result").is_not_null() & ~pl.col("graded")).height,
        "n_elo": graded.filter(pl.col("elo_prob").is_not_null()).height,
        "n_market": graded.filter(pl.col("market_prob").is_not_null()).height,
        "pick_accuracy": m["picks_correct"] / m["picks_total"] if m["picks_total"] else None,
    }
    w = rv.weekly
    rv.wins = {
        "weeks": w.filter(
            pl.col("brier_model").is_not_null() & pl.col("brier_elo").is_not_null()
        ).height,
        "beat_elo": w.filter(pl.col("brier_model") < pl.col("brier_elo")).height,
        "market_weeks": w.filter(
            pl.col("brier_model").is_not_null() & pl.col("brier_market").is_not_null()
        ).height,
        "beat_market": w.filter(pl.col("brier_model") < pl.col("brier_market")).height,
    }
    rv.split = _split_table(graded)
    rv.calibration, rv.reliability = _calibration(graded, cfg)
    rv.sure_right, rv.sure_wrong, rv.margin_misses = _calls(graded)


def _fill_players(rv: SeasonReview, sb: pl.DataFrame) -> None:
    rv.scoreboard_weeks = {
        m: sorted(sb.filter(pl.col("mode") == m)["week"].unique().to_list()) for m in MODES
    }
    by_target = _by_target(sb)
    rv.groups = _by_group(sb)
    rv.team = by_target.filter(pl.col("position_group") == "TEAM")
    rv.players = by_target.filter(pl.col("position_group") != "TEAM")


def _with_player_columns(weekly: pl.DataFrame, sb: pl.DataFrame) -> pl.DataFrame:
    """Each graded week's players-vs-baseline percent (the dashboard's `player/improvement_all`)
    and whether that week's rows are live or walk-forward backtest."""
    if sb.is_empty():
        return weekly
    players = sb.filter(pl.col("position_group") != "TEAM")
    imp: dict[int, float | None] = {}
    mode: dict[int, str] = {}
    for w in weekly["week"].to_list():
        part = players.filter(pl.col("week") == w)
        if part.is_empty():
            continue
        # one mode per week: the live rows when the week has any, never a live / backtest mix
        # (the live file also holds walk-forward rows of the same weeks; Sol review, P10)
        live = part.filter(pl.col("mode") == "live")
        imp[w] = _improvement(live if live.height else part)
        mode[w] = "live" if live.height else "backtest"
    if not mode:
        return weekly
    return weekly.with_columns(
        pl.col("week")
        .replace_strict(imp, default=None, return_dtype=pl.Float64)
        .alias("player_improvement"),
        pl.col("week")
        .replace_strict(mode, default=None, return_dtype=pl.String)
        .alias("player_mode"),
    )


def _reconcile_scorecard(
    ctx: _Ctx, rv: SeasonReview, scorecard: pl.DataFrame | None, graded_all: pl.DataFrame
) -> None:
    """Say where the season scorecard and the saved predictions graded now disagree."""
    if scorecard is None or scorecard.is_empty() or graded_all.is_empty():
        return
    card = {int(r["week"]): r for r in scorecard.iter_rows(named=True)}
    mine = {int(r["week"]): r for r in rv.weekly.iter_rows(named=True)}
    only_graded = sorted(set(mine) - set(card))
    only_card = sorted(set(card) - set(mine))
    if only_graded:
        ctx.notes.append(
            f"{_weeks_label(only_graded).capitalize()} "
            f"{'is' if len(only_graded) == 1 else 'are'} graded here from the saved predictions "
            "but not in the season scorecard (it grades a week at the next week's run, so the "
            "season's last week, or a week no digest run followed, is never in it)."
        )
    if only_card:
        ctx.notes.append(
            f"{_weeks_label(only_card).capitalize()} "
            f"{'is' if len(only_card) == 1 else 'are'} in the season scorecard but could not be "
            "graded from the saved predictions here."
        )
    off = [
        w
        for w in sorted(set(mine) & set(card))
        if _ok(card[w]["brier_model"])
        and _ok(mine[w]["brier_model"])
        and abs(card[w]["brier_model"] - mine[w]["brier_model"]) > 5e-4
    ]
    if off:
        ctx.notes.append(
            "The model's Brier score differs from the scorecard's by more than 0.0005 in "
            f"{_weeks_label(off)} (a re-run or a revised score since the digest quoted it)."
        )


# ---- render --------------------------------------------------------------------------------------


def _beat_word(model: Any, other: Any, name: str) -> str | None:
    if not (_ok(model) and _ok(other)):
        return None
    gap = abs(model - other)
    if round(gap, 4) == 0:
        return f"The model matched {name} ({_f(model, 4)})."
    verb = "beat" if model < other else "trailed"
    return f"The model {verb} {name} by {gap:.4f} ({_f(model, 4)} against {_f(other, 4)})."


def _head_section(rv: SeasonReview) -> list[str]:
    where = "the live weekly runs" if rv.live else "simulation and backtest digests"
    lines = [
        f"# {rv.season} season review",
        "",
        (
            f"Graded through week {rv.through_week}."
            if rv.totals.get("games")
            else "No graded week yet."
        )
        + f" Built {rv.generated}.",
        "",
        "## How this review was made",
        "",
        f"- Command: `nfl season review --season {rv.season}`, from {where}"
        + (f" under `{rv.run_root}`." if rv.run_root else "."),
        f"- Run records of kind `{rv.kind}`.",
    ]
    if rv.read:
        lines.append(f"- Read: {'; '.join(rv.read)}.")
    if rv.missing:
        lines.append(f"- Not found: {'; '.join(rv.missing)}.")
    lines += [
        "- **Live** rows were made before kickoff by the weekly run and scored afterwards. "
        "**Backtest** (walk-forward) rows re-run weeks as if live, for example the weeks before "
        "the pipeline went live; they are labelled and never counted as live.",
    ]
    if rv.scoreboard_weeks.get("backtest") and rv.scoreboard_weeks.get("live"):
        lines.append(
            f"- Player scoreboard: {_weeks_label(rv.scoreboard_weeks['live'])} "
            f"{'is' if len(rv.scoreboard_weeks['live']) == 1 else 'are'} live, "
            f"{_weeks_label(rv.scoreboard_weeks['backtest'])} "
            f"{'is' if len(rv.scoreboard_weeks['backtest']) == 1 else 'are'} "
            "walk-forward backtest."
        )
    elif rv.scoreboard_weeks.get("backtest"):
        lines.append(
            f"- Player scoreboard: only walk-forward backtest rows "
            f"({_weeks_label(rv.scoreboard_weeks['backtest'])}); no live week is scored yet."
        )
    lines += [f"- Note: {n}" for n in rv.notes] + [""]
    return lines


def _glance_section(rv: SeasonReview) -> list[str]:
    h = rv.headline
    lines = ["## The season at a glance", ""]
    if not h or not h.get("games"):
        # nothing graded: still show what the other records say
        lines.append("No graded weeks yet.")
        extra = _glance_rows(h, with_games=False) if h else []
        return [*lines, ""] + (
            _table(["Measure", "So far", "Read it as"], extra, "lll") if extra else []
        )
    return lines + _table(["Measure", "So far", "Read it as"], _glance_rows(h), "lll")


def _glance_rows(h: dict[str, Any], with_games: bool = True) -> list[list[Any]]:
    rows: list[list[Any]] = []
    if with_games:
        rows += [
            [
                "Games graded",
                f"{h['games']} in {_plural(h['weeks'], 'week')}",
                h["week_range"],
            ],
            ["Brier score, model", _f(h["brier_model"], 4), "lower is better; 0.25 is a coin flip"],
            ["Brier score, Elo", _f(h["brier_elo"], 4), "the simple rating baseline"],
            [
                "Brier score, market",
                _f(h["brier_market"], 4),
                "closing lines; the hard one to beat",
            ],
            [
                "Pick accuracy",
                f"{_pc(h['pick_accuracy'], 1)} ({h['picks_correct']} of {h['picks_total']})",
                "winners called",
            ],
            [
                "Points error",
                f"{_f(h['points_mae'], 1)} points",
                "average miss on each team's score; lower is better",
            ],
            [
                "Calibration (ECE)",
                f"{_f(h['ece'])} (chance level {_f(h['ece_chance'])})",
                "lower is better; compare it with the chance level",
            ],
        ]
    if h.get("watch_picks"):
        rows.append(
            [
                "Watch-list hit rate",
                f"{_pc(h['watch_rate'], 1)} ({h['watch_hits']} of {h['watch_picks']})",
                f"a hit beats the player's own baseline; all eligible players do about "
                f"{_pc(WATCH_BASE_RATE)}",
            ]
        )
    else:
        rows.append(["Watch-list hit rate", "-", "no graded picks"])
    if h.get("player_improvement") is not None:
        label = "live rows" if h["player_mode"] == "live" else "walk-forward backtest rows only"
        rows.append(
            [
                "Player projections vs baseline",
                f"{_signed_pct(h['player_improvement'])} ({label})",
                "how much lower the model's average miss is; positive is better",
            ]
        )
    else:
        rows.append(["Player projections vs baseline", "-", "no scored weeks"])
    if h.get("weeks_expected"):
        on = (
            f", {h['on_time']} of {h['timed']} on time"
            if h.get("timed")
            else ", timing not recorded"
        )
        rows.append(
            [
                "Weeks published",
                f"{h['weeks_published']} of {h['weeks_expected']}{on}",
                "before the first kickoff",
            ]
        )
    else:
        rows.append(["Weeks published", "-", "no runs recorded"])
    if h.get("checked"):
        rows.append(
            [
                "Digest first-try pass rate",
                f"{_pc(h['first_try'] / h['checked'])} ({h['first_try']} of {h['checked']})",
                "automated checks passed with no regeneration",
            ]
        )
    else:
        rows.append(["Digest first-try pass rate", "-", "no checks recorded"])
    return rows


def _games_section(rv: SeasonReview) -> list[str]:
    lines = ["## Game model, Elo and the market", ""]
    if rv.weekly.is_empty():
        return [*lines, "No graded weeks yet.", ""]
    t = rv.totals
    lines += [
        "The **Brier score** is the average squared miss of the win probability. Lower is better, "
        f"and {COIN_FLIP_BRIER} is what a coin flip scores. For scale, over the 2018-2025 "
        f"walk-forward backtests the model averaged {REF_BRIER['model']}, Elo "
        f"{REF_BRIER['Elo']} and the market {REF_BRIER['market']}.",
        "",
    ]
    lines += _table(
        ["", "Brier score (lower is better)", "Games"],
        [
            ["Model", _f(t["brier_model"], 4), t["n_elo"]],
            ["Elo", _f(t["brier_elo"], 4), t["n_elo"]],
            ["Market", _f(t["brier_market"], 4), t["n_market"]],
        ],
    )
    said = [
        _beat_word(t["brier_model"], t["brier_elo"], "Elo"),
        _beat_word(t["brier_model"], t["brier_market"], "the market"),
    ]
    lines += [" ".join(s for s in said if s), ""] if any(said) else []
    wins = rv.wins
    if wins.get("weeks"):
        text = (
            "Week by week, the model had the lower Brier score than Elo in "
            f"{wins['beat_elo']} of {wins['weeks']} weeks"
        )
        if wins.get("market_weeks"):
            text += f" and than the market in {wins['beat_market']} of {wins['market_weeks']}"
        lines += [text + ".", ""]
    if t.get("late"):
        lines += [
            f"{_plural(t['late'], 'game')} not graded: the pick was saved after kickoff.",
            "",
        ]
    lines += ["### Week by week", ""]
    rows = []
    has_players = rv.weekly["player_improvement"].null_count() < rv.weekly.height
    for r in rv.weekly.iter_rows(named=True):
        row = [
            r["week"]
            if not r["game_types"] or r["game_types"] == "REG"
            else f"{r['week']} ({r['game_types']})",
            r["games"],
            _f(r["brier_model"], 4),
            _f(r["brier_elo"], 4),
            _f(r["brier_market"], 4),
            f"{r['picks_correct']} of {r['picks_total']}",
            _pc(r["picks_correct"] / r["picks_total"]) if r["picks_total"] else "-",
            _f(r["points_mae"], 1),
        ]
        if has_players:
            row.append(
                f"{_signed_pct(r['player_improvement'])} ({r['player_mode']})"
                if r["player_mode"]
                else "-"
            )
        rows.append(row)
    head = [
        "Week",
        "Games",
        "Brier model",
        "Brier Elo",
        "Brier market",
        "Picks right",
        "Accuracy",
        "Points error",
    ]
    lines += _table(head + (["Players vs baseline"] if has_players else []), rows)
    if rv.split.height:
        lines += ["### Regular season and playoffs", ""]
        lines += _table(
            [
                "",
                "Weeks",
                "Games",
                "Brier model",
                "Brier Elo",
                "Brier market",
                "Accuracy",
                "Points error",
            ],
            [
                [
                    r["part"],
                    r["weeks"],
                    r["games"],
                    _f(r["brier_model"], 4),
                    _f(r["brier_elo"], 4),
                    _f(r["brier_market"], 4),
                    f"{_pc(r['picks_correct'] / r['picks_total'], 1) if r['picks_total'] else '-'}",
                    _f(r["points_mae"], 1),
                ]
                for r in rv.split.iter_rows(named=True)
            ],
        )
    return lines


def _calibration_section(rv: SeasonReview) -> list[str]:
    lines = ["## Calibration", ""]
    c = rv.calibration
    if not c:
        return [*lines, "No graded games yet, so there is nothing to check.", ""]
    lines += [
        "Does a 70% call win about 70% of the time? **ECE** is the average gap between the "
        "predicted probability and how often the home team really won, weighted by how many "
        "games sit in each bin. Lower is better. It is never zero on a real season: even a "
        "perfectly calibrated model shows some, so read it next to the chance level.",
        "",
        f"Season ECE is **{_f(c['ece'])}** over {c['games']} games. A perfectly calibrated "
        f"model would show about {_f(c['chance'])} on games like these, and under "
        f"{_f(c['chance_p90'])} nine times out of ten.",
        "",
    ]
    verdict = {
        "few": f"Fewer than {c['min_games']} games: too few for the ECE to mean much yet.",
        "within": "The ECE is within what chance alone would produce: no sign of miscalibration.",
        "above": "The ECE is above what chance alone would produce: look at the bins below.",
    }[c["verdict"]]
    lines += [verdict, ""]
    if rv.reliability.height:
        rows = [
            [
                f"{_pc(r['lo'])}{F.EN_DASH}{_pc(r['hi'])}",
                r["n"],
                _pc(r["mean_prob"], 1),
                _pc(r["mean_outcome"], 1),
                f"{100 * (r['mean_outcome'] - r['mean_prob']):+.1f} pts",
            ]
            for r in rv.reliability.iter_rows(named=True)
        ]
        lines += _table(
            [
                "Predicted home win",
                "Games",
                "Average predicted",
                "Home team won",
                "Gap",
            ],
            rows,
        )
    return lines


def _target_rows(df: pl.DataFrame, with_mode: bool = False) -> list[list[Any]]:
    rows = []
    for r in df.iter_rows(named=True):
        row = [
            r["position_group"],
            r["target_label"] or r["target"],
            r["n"],
            _f(r["mae_model"], 2),
            _f(r["mae_baseline"], 2),
            _signed_pct(r["improvement"]),
            _pc(r["coverage"]),
            _f(r["brier_model"], 3),
            _f(r["brier_baseline"], 3),
            _f(r["calibration_ece"], 3),
        ]
        rows.append(([r["mode"]] if with_mode else []) + row)
    return rows


TARGET_HEAD = [
    "Group",
    "Target",
    "Scored",
    "MAE model",
    "MAE baseline",
    "Better by",
    "80% range held",
    "Brier model",
    "Brier baseline",
    "ECE",
]


def _players_section(rv: SeasonReview) -> list[str]:
    lines = ["## Player projections: the accuracy scoreboard", ""]
    if rv.players.is_empty() and rv.team.is_empty():
        return [*lines, "No player scoreboard rows yet.", ""]
    lines += [
        "**MAE** is the average miss in the stat's own units; lower is better. **Better by** is "
        "how much lower the model's MAE is than the player's rolling baseline (the number to "
        "beat); positive means the model is better. **80% range held** is the share of results "
        "inside the projected 80% range, which should be near 80%. Targets that are chances "
        "(a touchdown, a sack) have a **Brier score** (lower is better) and an ECE instead of, "
        "or next to, an MAE.",
        "",
    ]
    sub = {
        "live": "Live rows: projections saved before kickoff, scored afterwards",
        "backtest": "Backtest rows: walk-forward re-runs, labelled and not live",
    }
    for mode in MODES:
        weeks = rv.scoreboard_weeks.get(mode) or []
        if not weeks:
            continue
        lines += [f"### {sub[mode]}; {_weeks_label(weeks)}", ""]
        g = rv.groups.filter((pl.col("mode") == mode) & (pl.col("position_group") != "TEAM"))
        if g.height:
            lines += _table(
                [
                    "Position group",
                    "Weeks",
                    "Projections scored",
                    "MAE better by",
                    "Chances (Brier) better by",
                ],
                [
                    [
                        r["position_group"],
                        r["weeks"],
                        r["n"],
                        _signed_pct(r["improvement"]),
                        _signed_pct(r["brier_improvement"]),
                    ]
                    for r in g.iter_rows(named=True)
                ],
            )
        part = rv.players.filter(pl.col("mode") == mode)
        if part.height:
            lines += _table(TARGET_HEAD, _target_rows(part), "llrrrrrrrr")
    if rv.team.height:
        lines += [
            "### Team stat totals",
            "",
            "The same scoreboard for the team stat totals (not part of the player numbers above).",
            "",
        ]
        lines += _table(
            ["Rows", *TARGET_HEAD], _target_rows(rv.team, with_mode=True), "lllrrrrrrrr"
        )
    return lines


def _watch_section_md(rv: SeasonReview) -> list[str]:
    lines = ["## Watch list", ""]
    info = rv.watch_info
    if not info.get("picks"):
        return [*lines, "No watch-list picks have been graded yet.", ""]
    lines += [
        f"A pick **hits** when the player beats their own rolling baseline. The season hit rate is "
        f"**{_pc(info['rate'], 1)}** ({info['hits']} of {info['picks']} picks that played). "
        f"For scale: yardage is right-skewed, so even all eligible players beat their baseline "
        f"only about {_pc(WATCH_BASE_RATE)} of the time; the picks should land above that over a "
        "season, and a week of 10 to 20 picks swings a lot.",
        "",
    ]
    if info.get("ranged"):
        lines += [
            f"The projected 80% range held the result in {info['inside']} of {info['ranged']} "
            f"model picks ({_pc(info['inside'] / info['ranged'])}).",
            "",
        ]
    lines += [f"Source: {info['source']}.", ""]
    rows = [
        [
            r["week"],
            r["picks"],
            r["hits"],
            _pc(r["hits"] / r["picks"]) if r["picks"] else "-",
            _pc(r["inside"] / r["ranged"]) if r["ranged"] else "-",
        ]
        for r in rv.watch.iter_rows(named=True)
    ]
    lines += _table(["Week", "Picks scored", "Hits", "Hit rate", "Inside the 80% range"], rows)
    if rv.watch_sides.height:
        lines += ["By side:", ""]
        lines += _table(
            ["Side", "Picks scored", "Hits", "Hit rate"],
            [
                [r["side"], r["picks"], r["hits"], _pc(r["hits"] / r["picks"])]
                for r in rv.watch_sides.iter_rows(named=True)
            ],
        )
    return lines


def _checks_section(rv: SeasonReview) -> list[str]:
    lines = ["## Digest checks", ""]
    c = rv.checks
    if not c:
        return [*lines, "No digest checks recorded yet.", ""]
    n = c["weeks"]
    lines += [
        f"Every digest runs automated checks (numbers, names, banned language, length). If one "
        f"fails, the writer gets one regeneration; if the checks still fail the digest goes out "
        f"with a warning banner. {_plural(n, 'digest')} had a verdict"
        + (" (from each week's `checks.json`)" if c["source"] == ["digest only"] else "")
        + ".",
        "",
    ]
    rows = [
        [
            "Passed first time",
            len(c["first_time"]),
            _pc(len(c["first_time"]) / n),
            _weeks_text(c["first_time"]),
        ],
        [
            "Passed after one regeneration",
            len(c["regenerated"]),
            _pc(len(c["regenerated"]) / n),
            _weeks_text(c["regenerated"]),
        ],
        [
            "Went out with the warning banner",
            len(c["banner"]),
            _pc(len(c["banner"]) / n),
            _weeks_text(c["banner"]),
        ],
    ]
    lines += _table(["Outcome", "Weeks", "Share", "Which weeks"], rows, "lrrl")
    if c["first_failed"]:
        top = ", ".join(f"`{name}` x{k}" for name, k in c["first_failed"])
        lines += [f"Checks that failed on the first attempt: {top}.", ""]
    return lines


def _pipeline_section(rv: SeasonReview) -> list[str]:
    lines = ["## Pipeline uptime", ""]
    s = rv.pipeline_stats
    if not s:
        return [*lines, "No pipeline runs recorded yet.", ""]
    lines += [
        "One row per week from the first week that has a record to the latest. A week with a "
        "digest but no pipeline record (an older backtest digest) counts as published with "
        "unknown timing. A week with neither counts as missed.",
        "",
    ]
    timing = (
        f"{s['on_time']} of {s['timed']} ({_pc(s['on_time'] / s['timed'])})" if s["timed"] else "-"
    )
    median = f"{s['median_hours']:.1f} h" if s["median_hours"] is not None else "-"
    runs = ", ".join(f"{k} {v}" for k, v in sorted(s["runs"].items())) or "-"
    lines += _table(
        ["Measure", "Value"],
        [
            ["Weeks published", f"{s['published']} of {s['expected']}"],
            ["With a pipeline record", s["recorded"]],
            ["Published on time (before the first kickoff)", timing],
            ["Median time before the first kickoff", median],
            ["Published with a degraded step (fell back)", s["degraded"]],
            ["Not published: the run failed", s["failed"]],
            ["Not published: no run recorded", s["missed"]],
            ["All runs including retries, by status", runs],
            ["Drift alerts raised (signals in the alert state)", s["drift_alerts"]],
        ],
        "lr",
    )
    if rv.alerts:
        lines += ["Alerts from each week's run summary:", ""]
        lines += _table(
            ["Week", "Level", "Alert"],
            [[a["week"], a["level"] or "-", a["title"]] for a in rv.alerts],
            "rll",
        )
    elif s["summaries"]:
        n = _plural(s["summaries"], "run summary", "run summaries")
        lines += [f"No alerts in the {n}.", ""]
    else:
        lines += ["No run summaries were found, so the alerts are unknown.", ""]
    lines += ["### Week by week", ""]
    lines += [f"- {line}" for line in rv.week_lines] + [""]
    return lines


def _game_label(r: dict[str, Any]) -> str:
    return f"{nickname(r['away_team'])} at {nickname(r['home_team'])}"


def _result_text(r: dict[str, Any]) -> str:
    hs, aws = int(r["home_score"]), int(r["away_score"])
    if r["result"] > 0:
        return f"{nickname(r['home_team'])} won {hs}{F.EN_DASH}{aws}"
    if r["result"] < 0:
        return f"{nickname(r['away_team'])} won {aws}{F.EN_DASH}{hs}"
    return f"tied {hs}{F.EN_DASH}{aws}"


def _picks_rows(df: pl.DataFrame) -> list[list[Any]]:
    out = []
    for r in df.iter_rows(named=True):
        fav = r["home_team"] if r["home_win_prob"] > 0.5 else r["away_team"]
        out.append(
            [
                r["week"],
                _game_label(r),
                nickname(fav),
                F.pct(r["fav_prob"], cap=True).display,
                _result_text(r),
            ]
        )
    return out


def _calls_section(rv: SeasonReview) -> list[str]:
    lines = ["## Best and worst calls", ""]
    if rv.sure_right.is_empty() and rv.sure_wrong.is_empty() and rv.margin_misses.is_empty():
        return [*lines, "No graded games yet.", ""]
    head = ["Week", "Game", "Pick", "Win chance", "Result"]
    if rv.sure_right.height:
        lines += [f"### The {N_CALLS} most confident correct picks", ""]
        lines += _table(head, _picks_rows(rv.sure_right), "rllrl")
    if rv.sure_wrong.height:
        lines += [f"### The {N_CALLS} most confident wrong picks", ""]
        lines += _table(head, _picks_rows(rv.sure_wrong), "rllrl")
    if rv.margin_misses.height:
        lines += [
            f"### The {N_CALLS} biggest margin misses",
            "",
            "The predicted margin (predicted home points minus away points) against the real one; "
            "lower is better.",
            "",
        ]
        rows = [
            [
                r["week"],
                _game_label(r),
                F.margin(r["pred_margin"], r["home_team"], r["away_team"]).display,
                F.margin(float(r["result"]), r["home_team"], r["away_team"]).display,
                f"{r['miss']:.1f} points",
            ]
            for r in rv.margin_misses.iter_rows(named=True)
        ]
        lines += _table(["Week", "Game", "Predicted", "Actual", "Miss"], rows, "rllll")
    for title, df in (
        ("biggest watch-list hits", rv.watch_hits),
        ("biggest watch-list misses", rv.watch_misses),
    ):
        if df.is_empty():
            continue
        lines += [
            f"### The {len(df)} {title}",
            "",
            "Ranked by how far the result landed from the player's own baseline, in widths of "
            "the pick's 80% range (so yards and tackles compare).",
            "",
        ]
        lines += _table(
            ["Week", "Player", "Stat", "Baseline", "Projected", "80% range", "Actual"],
            [
                [
                    r["week"],
                    f"{r['player']} ({r.get('team') or '-'})",
                    r.get("target_label") or "-",
                    _stat(r["baseline"], r.get("unit")),
                    _stat(r.get("projection"), r.get("unit")),
                    f"{_stat(r['p10'], r.get('unit'))}{F.EN_DASH}{_stat(r['p90'], r.get('unit'))}",
                    _stat(r["actual"], r.get("unit")),
                ]
                for r in df.iter_rows(named=True)
            ],
            "rllrrrr",
        )
    return lines


QUESTIONS = [
    "Which numbers in this review would have changed a decision this season, and which "
    "charts or sections did you never open?",
    "Game model against Elo and the market: did the model earn its place, or would Elo (or "
    "the market) have done as well? What is the first thing to change?",
    "Player projections: which targets beat their baseline with a trustworthy 80% range, "
    "and which should be cut or rebuilt?",
    "Was the weekly digest worth reading? What would you keep, cut or reorder?",
    "Which weeks needed hands-on repair, and what would have prevented each one?",
    "What deserves the offseason: re-tuning, new data, the Big Data Bowl track, scheduling?",
]


def _discussion_section() -> list[str]:
    lines = ["## For Rishi: keep, cut or rebuild for next season", ""]
    lines += [
        "Left empty on purpose; fill it in during the review.",
        "",
        *[f"{i}. {q}" for i, q in enumerate(QUESTIONS, 1)],
        "",
        "Decisions:",
        "",
        "- Keep:",
        "- Cut:",
        "- Rebuild:",
        "",
    ]
    return lines


def render_review(review: SeasonReview) -> str:
    """The review as Markdown: ten sections, each readable on its own."""
    sections = [
        _head_section(review),
        _glance_section(review),
        _games_section(review),
        _calibration_section(review),
        _players_section(review),
        _watch_section_md(review),
        _checks_section(review),
        _pipeline_section(review),
        _calls_section(review),
        _discussion_section(),
    ]
    return "\n".join(line for sec in sections for line in sec).rstrip() + "\n"


def write_review(season: int, out: Path | None = None, **kw: Any) -> Path:
    """Build the review and write it. Default path: `reports/<season>/season-review.md` for the
    live runs; a simulation's goes to `reports/backtests/<season>/season-review.md` so it can
    never replace the real one. `kw` goes to `build_season_review`."""
    review = build_season_review(season, **kw)
    if out is None:
        ctx = _Ctx(season, kw.get("run_root"), kw.get("paths"))
        reports = ctx.paths().reports
        out = (
            reports / str(season) / REVIEW_FILE
            if ctx.live
            else reports / "backtests" / str(season) / REVIEW_FILE
        )
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_review(review), encoding="utf-8")
    return out
