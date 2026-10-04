"""The player model in the digest (plan P06; documentation/06 -> Players to watch, Report card;
documentation/11 -> The accuracy scoreboard).

- **Players to watch / tough spots** (`player_watch`): this week's `predictions_players.
  parquet` from the run folder (live: the weekly `player` step writes it; backtest: the run
  materializes it from the walk-forward files) -> `models.player_watch` selection ->
  payload items. Returns None when the week has no projections: the digest then falls back
  to the P04 heuristic, labelled `heuristic`, so the pipeline never breaks.
- **Scoring saved picks** (`WatchScorer`): model picks through `player_schema.
  score_predictions` on the season's player history (loaded once per season), heuristic
  picks through `watchlist.score_watchlist`; a week can hold either.
- **Report card** (`watch_lookback`, `scoreboard_highlights`, `week_improvement`): last
  week's picks projected vs actual vs range, and 2-3 lines on how the projections are doing
  (always one weak spot). Live numbers only ever come from live scoreboard rows; until a
  live week is scored, the card says so and quotes the walk-forward backtests labelled as
  backtests.

Every number goes through `digest/format.py`; every text here is code-made and owned by the
player (picks, look-back) or by the model (highlights) in `facts.py`.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.digest import format as F
from nflengine.digest.build import matchup
from nflengine.digest.checks import banned_terms
from nflengine.digest.names import nickname
from nflengine.digest.payload import LookbackItem, SideTally, WatchItem
from nflengine.models.player_schema import TARGETS, conform, score_predictions
from nflengine.models.player_watch import (
    N_DEFENSE,
    N_OFFENSE,
    N_TOUGH,
    select_watchlist,
    tough_spots,
)
from nflengine.paths import DataPaths

PLAYER_PRED_FILE = "predictions_players.parquet"
LIVE_SCOREBOARD = "accuracy_scoreboard.parquet"  # runs/<season>/
BACKTEST_SCOREBOARD = ("backtests", "player", "scoreboard.parquet")  # under runs/
HEURISTIC_VERSION = "heuristic-v0 (no player projections this week)"
DRIVERS_MAX = 3
MIN_SCORED = 20  # a target needs this many scored projections before a highlight names it
COVERAGE_OK = (0.72, 0.88)  # P06 exit criterion for the P10-P90 range
THIN_SEASON_GAMES = 3  # a rolling baseline from fewer games this season gets a note
MIN_DRIVER_SHARE = 0.2  # a driver is shown only if it is >= 20% of the gap to baseline
NO_DRIVER = "no single factor stands out"
LATE_TARGETS = ("pressures",)  # PFR stats arrive a week late (features lag them, D44)
CONFIDENCES = ("low", "medium", "high")
_SHARED_LABELS = {t.label for t in TARGETS if sum(u.label == t.label for u in TARGETS) > 1}


# ---- this week's picks ------------------------------------------------------------------------


def load_player_predictions(path: Path) -> pl.DataFrame | None:
    """The week's projections (`PRED_SCHEMA`), or None when the file is missing or empty."""
    if not path.exists():
        return None
    df = pl.read_parquet(path)
    return conform(df) if df.height else None


def model_version(preds: pl.DataFrame) -> str:
    versions = sorted(set(preds["model_version"].drop_nulls().to_list()))
    return versions[-1] if versions else "player-model (unversioned)"


def _num_ok(x: Any) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def driver_texts(
    drivers: list[dict] | None, label: str, unit: str, direction: float = 0.0
) -> list[str]:
    """SHAP drivers as code-made phrases: "<phrase> (puts the projection 9 receiving yards
    above a typical player in his group)". Dropped: a driver that rounds to 0, a phrase
    with a banned word (it would fail the banned-language check), and, with a `direction`
    (the pick's gap to baseline), a driver smaller than `MIN_DRIVER_SHARE` of that gap (a
    3-yard driver can't stand for a 29-yard gap: P06 fact-check). Drivers pushing the same
    way as the gap come first."""
    out: list[str] = []
    ordered = sorted(
        drivers or [],
        key=lambda d: direction * (d.get("contribution") or 0.0) < 0,  # stable: same way first
    )
    floor = MIN_DRIVER_SHARE * abs(direction)
    for d in ordered:
        phrase = (d.get("phrase") or "").strip()
        c = d.get("contribution")
        if not phrase or not _num_ok(c) or banned_terms(phrase) or abs(float(c)) < floor:
            continue
        effect = F.driver_effect(float(c), label, unit)
        if effect is not None:
            out.append(f"{phrase} ({effect.display})")
    return out[:DRIVERS_MAX]


def center(r: dict) -> float:
    """The projection shown: P50 for amounts; for counts the expected value (`mean`, one
    decimal), because a small count's median is a coarse whole number (the contract's
    `outperformance` is mean - baseline for counts too)."""
    if r.get("kind") == "count" and _num_ok(r.get("mean")):
        return float(r["mean"])
    return float(r["p50"])


def baseline_note(r: dict) -> str | None:
    """A code-made note when the baseline rests on little (fewer than 3 games this season,
    or a last-season / role baseline). It says where the baseline comes from, never why the
    projection differs: SHAP drivers explain the projection against the model's average,
    not against his baseline, so a thin baseline is context the drivers can't give."""
    source = r.get("baseline_source")
    n = r.get("games_season")
    games = None
    if _num_ok(n):
        games = f"{F.count(n).display} game{'s' if int(n) != 1 else ''} this season"
    late = f" ({r['target']} data arrives a week late)" if r.get("target") in LATE_TARGETS else ""
    if source == "role":
        return "his baseline is the average for players in his role (little history of his own)"
    if source == "last_season":
        return (
            "his baseline comes mostly from last season" + (f" ({games})" if games else "") + late
        )
    if games and int(n) < THIN_SEASON_GAMES:
        return f"his baseline comes from {games}{late}"
    return None


def _role_note(r: dict) -> str | None:
    if not r.get("role_change") or r.get("role_ok"):
        return None
    vs = r.get("vacated_share")
    if _num_ok(vs) and vs > 0:
        return (
            "a bigger role this week: regular teammates who missed his team's last game or "
            f"are ruled out this week had {F.share(vs).display} of his position group's usage"
        )
    return "a bigger role this week: a regular teammate in his position group is out"


def build_model_watch(rows: pl.DataFrame) -> list[WatchItem]:
    """Payload items from `select_watchlist` / `tough_spots` rows (source `model`)."""
    out = []
    for r in rows.iter_rows(named=True):
        label, unit = r["target_label"], r["unit"] or "yards"
        mid, base = center(r), float(r["baseline"])
        diff = r["outperformance"] if _num_ok(r.get("outperformance")) else mid - base
        home, away = (r["team"], r["opponent"]) if r["home"] else (r["opponent"], r["team"])
        status = r.get("injury_status")
        conf = r.get("confidence")
        drivers = driver_texts(r.get("drivers"), label, unit, float(diff))
        out.append(
            WatchItem(
                player=r["player"],
                player_id=r["player_id"],
                team=r["team"],
                team_name=nickname(r["team"]),
                position=r.get("position") or r.get("pgroup") or "",
                opponent=r["opponent"],
                opponent_name=nickname(r["opponent"]),
                game_id=r["game_id"],
                matchup=matchup(home, away),
                target=label,
                group=r["group"],
                side=r.get("side"),
                baseline=F.stat(base, label, unit),
                projection=F.stat(mid, label, unit),
                interval=F.stat_range(float(r["p10"]), float(r["p90"]), label, unit),
                vs_baseline=F.vs_baseline(float(diff), label, unit),
                baseline_note=baseline_note(r),
                role_note=_role_note(r),
                injury_note=(
                    f"listed {status.lower()} on this week's injury report" if status else None
                ),
                confidence=conf if conf in CONFIDENCES else "low",
                source="model",
                drivers=drivers,
                driver_note=None if drivers else NO_DRIVER,
            )
        )
    return out


def stamp_model_watch(picks: pl.DataFrame, run_time: dt.datetime) -> pl.DataFrame:
    """The picks as saved for next week's report card: `created_at` = when the pick was
    published (this digest run; graded only if before kickoff), the projection's own time
    kept as `projected_at`."""
    return picks.rename({"created_at": "projected_at"}).with_columns(
        pl.lit(run_time).cast(pl.Datetime("us", "UTC")).alias("created_at"),
        pl.lit("model").alias("source"),
    )


@dataclass
class PlayerWatch:
    frame: pl.DataFrame  # the picks as saved (`watchlist.parquet`)
    items: list[WatchItem]
    tough: list[WatchItem]
    version: str


@dataclass(frozen=True)
class WatchSizes:
    """How many picks the digest shows (`config/settings.yaml` → `digest`): 10 on offense,
    10 on defense, 5 tough spots (D70); the heuristic fallback keeps its single list of 8."""

    offense: int = N_OFFENSE
    defense: int = N_DEFENSE
    tough: int = N_TOUGH
    heuristic: int = 8

    @classmethod
    def from_config(cls, digest: dict[str, Any] | None) -> WatchSizes:
        d = digest or {}

        def get(key: str, default: int) -> int:
            v = d.get(key)
            return int(v) if v is not None else default

        return cls(
            offense=get("watchlist_offense", N_OFFENSE),
            defense=get("watchlist_defense", N_DEFENSE),
            tough=get("tough_spots", N_TOUGH),
            heuristic=get("watchlist_size", 8),
        )


DEFAULT_SIZES = WatchSizes()


def model_watch(
    preds: pl.DataFrame,
    run_time: dt.datetime,
    *,
    sizes: WatchSizes = DEFAULT_SIZES,
    followed: Collection[str] = (),
) -> PlayerWatch:
    picks = select_watchlist(
        preds, sizes.offense, sizes.defense, followed=followed, run_time=run_time
    )
    tough = tough_spots(preds, sizes.tough, run_time=run_time, exclude=picks["player_id"].to_list())
    return PlayerWatch(
        stamp_model_watch(picks, run_time),
        build_model_watch(picks),
        build_model_watch(tough),
        model_version(preds),
    )


def player_watch(
    run_dir: Path,
    run_time: dt.datetime,
    *,
    sizes: WatchSizes = DEFAULT_SIZES,
    followed: Collection[str] = (),
) -> PlayerWatch | None:
    """This week's model picks and tough spots from `run_dir/predictions_players.parquet`;
    None when there are no projections (the caller falls back to the heuristic)."""
    preds = load_player_predictions(run_dir / PLAYER_PRED_FILE)
    if preds is None:
        return None
    return model_watch(preds, run_time, sizes=sizes, followed=followed)


# ---- scoring saved picks ----------------------------------------------------------------------


RESULT_COLS = ("actual", "played", "hit", "inside", "played_game")


def score_watch_rows(
    watch: pl.DataFrame,
    history: Callable[[int], pl.DataFrame],
    heuristic: Callable[[pl.DataFrame], pl.DataFrame] | None = None,
) -> pl.DataFrame:
    """Add `actual`, `played` (played **and** scored: a pressure count PFR hasn't published
    yet is not scored), `hit` (actual > baseline), `inside` (P10 <= actual <= P90, model
    picks) and `played_game` to saved picks of either source. `history(season)` returns
    `features.player_data.player_history` rows for that season."""
    if watch.is_empty():
        return watch.with_columns(
            pl.lit(None, pl.Float64).alias("actual"),
            *[pl.lit(None, pl.Boolean).alias(c) for c in RESULT_COLS[1:]],
        )
    is_model = (
        (pl.col("source") == "model").fill_null(False)
        if "source" in watch.columns
        else pl.lit(False)
    )
    parts = []
    model = watch.filter(is_model)
    if model.height:
        seasons = sorted(set(model["season"].drop_nulls().to_list()))
        hist = pl.concat([history(int(s)) for s in seasons], how="diagonal_relaxed")
        for c, t in (("actual", pl.Float64), ("played", pl.Boolean)):
            if c not in model.columns:
                model = model.with_columns(pl.lit(None, t).alias(c))
        scored = score_predictions(model.drop("hit", "inside", "played_game", strict=False), hist)
        ok = pl.col("played").fill_null(False) & pl.col("actual").is_not_null()
        parts.append(
            scored.with_columns(
                pl.col("played").fill_null(False).alias("played_game"),
                ok.alias("_ok"),
            )
            .with_columns(
                pl.col("_ok").alias("played"),
                pl.when(pl.col("_ok")).then(pl.col("actual") > pl.col("baseline")).alias("hit"),
                pl.when(pl.col("_ok"))
                .then((pl.col("actual") >= pl.col("p10")) & (pl.col("actual") <= pl.col("p90")))
                .alias("inside"),
            )
            .drop("_ok")
        )
    heur = watch.filter(~is_model)
    if heur.height and heuristic is not None:
        h = heuristic(heur.drop(*RESULT_COLS, strict=False))
        parts.append(h.with_columns(pl.col("played").alias("played_game")))
    if not parts:
        return score_watch_rows(watch.clear(), history)
    return pl.concat(parts, how="diagonal_relaxed")


class WatchScorer:
    """`score_watch` for `build_report_card`: scores saved picks of either source, loading
    each season's player history once (~4 s) however many weeks are graded."""

    def __init__(
        self,
        paths: DataPaths,
        history: Callable[[int], pl.DataFrame] | None = None,
    ):
        self.paths = paths
        self._history = history
        self._cache: dict[int, pl.DataFrame] = {}

    def history(self, season: int) -> pl.DataFrame:
        if season not in self._cache:
            if self._history is not None:
                self._cache[season] = self._history(season)
            else:
                from nflengine.features.player_data import load_inputs, player_history

                inp = load_inputs(self.paths, first_season=season, last_season=season, log=_quiet)
                self._cache[season] = player_history(inp)
        return self._cache[season]

    def __call__(self, watch: pl.DataFrame) -> pl.DataFrame:
        from nflengine.digest.watchlist import score_watchlist

        return score_watch_rows(watch, self.history, lambda df: score_watchlist(df, self.paths))


def _quiet(_: str) -> None:
    return None


# ---- report card: last week's picks ------------------------------------------------------------


def _vs_word(actual: float, baseline: float) -> str:
    if actual > baseline:
        return "above"
    return "below" if actual < baseline else "level with"


def _range_word(actual: float, lo: float, hi: float) -> str:
    if actual < lo:
        return "below the range"
    return "above the range" if actual > hi else "inside the range"


def lookback_text(r: dict) -> str:
    """One saved pick against what happened, all code-made: "projected 84 receiving yards,
    range 52–118 receiving yards → actual 97 receiving yards: inside the range, above his
    baseline of 61 receiving yards"."""
    model = r.get("source") == "model" and _num_ok(r.get("p50"))
    label = r.get("target_label") or r.get("target") or ""
    unit = (r.get("unit") or "yards") if model else "yards"
    base = F.stat(float(r["baseline"]), label, unit).display
    head = ""
    if model:
        head = (
            f"projected {F.stat(center(r), label, unit).display}, range "
            f"{F.stat_range(float(r['p10']), float(r['p90']), label, unit).display} → "
        )
    if not r.get("played"):
        if r.get("played_game"):
            return f"{head}no result yet (the stat isn't published), so not scored"
        return f"{head}did not play, so not scored"
    actual = float(r["actual"])
    tail = f"{_vs_word(actual, float(r['baseline']))} his baseline of {base}"
    shown = F.stat(actual, label, unit).display
    if model:
        return f"{head}actual {shown}: {_range_word(actual, r['p10'], r['p90'])}, {tail}"
    return f"actual {shown}, {tail}"


def watch_lookback(scored: pl.DataFrame) -> list[LookbackItem]:
    """Last week's picks, projected vs actual vs range (`score_watch_rows` output), in their
    published order."""
    if scored.is_empty():
        return []
    df = scored
    if "side" in df.columns:
        df = df.with_columns((pl.col("side") == "defense").fill_null(False).alias("_def"))
        df = df.sort("_def", "rank", nulls_last=True) if "rank" in df.columns else df.sort("_def")
    elif "rank" in df.columns:
        df = df.sort("rank", nulls_last=True)
    out = []
    for r in df.iter_rows(named=True):
        played = bool(r.get("played"))
        model = r.get("source") == "model" and _num_ok(r.get("p50"))
        label = r.get("target_label") or r.get("target") or ""
        unit = (r.get("unit") or "yards") if model else "yards"
        status = ""
        if not played:
            status = "no result yet" if r.get("played_game") else "did not play"
        side = r.get("side") if r.get("side") in ("offense", "defense") else None
        out.append(
            LookbackItem(
                player=r["player"],
                player_id=r["player_id"],
                team=r["team"],
                team_name=nickname(r["team"]),
                position=r.get("position") or "",
                target=r.get("target_label") or r.get("target") or "",
                text=lookback_text(r),
                played=played,
                hit=r.get("hit") if played else None,
                inside=r.get("inside") if played else None,
                side=side,
                projection=F.stat(center(r), label, unit) if model else None,
                interval=(
                    F.stat_range(float(r["p10"]), float(r["p90"]), label, unit) if model else None
                ),
                actual=F.stat(float(r["actual"]), label, unit) if played else None,
                status=status,
            )
        )
    return out


def side_tallies(scored: pl.DataFrame) -> list[SideTally]:
    """Scored picks and hits per side (offense first); [] for a list without sides."""
    if scored.is_empty() or "side" not in scored.columns:
        return []
    played = scored.filter(pl.col("played").fill_null(False))
    out = []
    for side in ("offense", "defense"):
        part = played.filter(pl.col("side") == side)
        if part.height:
            out.append(
                SideTally(
                    side=side,  # type: ignore[arg-type]
                    hits=F.count(int(part["hit"].fill_null(False).sum())),
                    total=F.count(part.height),
                )
            )
    return out


# ---- report card: the accuracy scoreboard -------------------------------------------------------


def read_scoreboards(paths: DataPaths, season: int) -> tuple[pl.DataFrame | None, ...]:
    """(live season scoreboard, backtest scoreboard); None where the file doesn't exist."""
    live = paths.runs / str(season) / LIVE_SCOREBOARD
    back = paths.runs.joinpath(*BACKTEST_SCOREBOARD)
    return tuple(pl.read_parquet(p) if p.exists() else None for p in (live, back))


def _name(label: str, group: str) -> str:
    return f"{label} ({group})" if label in _SHARED_LABELS else label


def _aggregate(sb: pl.DataFrame) -> pl.DataFrame:
    """Per target: projections scored, n-weighted MAE (model, baseline), improvement % and
    P10-P90 coverage."""
    n = pl.col("n_scored").cast(pl.Float64)
    agg = (
        sb.filter(pl.col("n_scored").fill_null(0) > 0)
        .group_by("target", "position_group", "target_label")
        .agg(
            n.sum().alias("n"),
            (pl.col("mae_model") * n).sum().alias("_m"),
            (pl.col("mae_baseline") * n).sum().alias("_b"),
            (pl.col("coverage_80") * n).sum().alias("_c"),
            pl.struct("season", "week").n_unique().alias("weeks"),
        )
        .filter(pl.col("n") >= MIN_SCORED)
    )
    return agg.with_columns(
        (100 * (pl.col("_b") - pl.col("_m")) / pl.col("_b")).alias("improvement"),
        (pl.col("_c") / pl.col("n")).alias("coverage"),
    ).sort("improvement", "target", "position_group", descending=[True, False, False])


def _highlight_lines(agg: pl.DataFrame, scope: str) -> list[str]:
    """Best target, weakest target (always), and the range coverage."""
    if agg.is_empty():
        return []
    rows = agg.to_dicts()
    best, weak = rows[0], rows[-1]
    lines = []
    shown = F.pct(abs(best["improvement"]) / 100)
    if best["improvement"] > 0 and shown.display != "0%":
        name = _name(best["target_label"], best["position_group"])
        lines.append(f"{name} projections beat the rolling baseline by {shown.display} {scope}")
    if weak is not best or not lines:
        name = _name(weak["target_label"], weak["position_group"])
        gap = F.pct(abs(weak["improvement"]) / 100)
        if gap.display == "0%":
            lines.append(f"{name}: about even with the rolling baseline, not yet better {scope}")
        elif weak["improvement"] < 0:
            lines.append(
                f"{name}: not yet better than the rolling baseline ({gap.display} worse) {scope}"
            )
        else:
            lines.append(
                f"{name}: the smallest gain, {gap.display} better than the rolling baseline {scope}"
            )
    off = max(rows, key=lambda r: abs(r["coverage"] - 0.8))
    lo, hi = COVERAGE_OK
    if not lo <= off["coverage"] <= hi:
        word = "narrow" if off["coverage"] < lo else "wide"
        name = _name(off["target_label"], off["position_group"])
        lines.append(
            f"{name} ranges are too {word}: {F.pct(off['coverage']).display} of results fell "
            f"inside the 80% range {scope}"
        )
    else:
        cov = float(agg["_c"].sum() / agg["n"].sum())
        lines.append(f"the 80% ranges held {F.pct(cov).display} of results {scope}")
    return lines


def _seasons(df: pl.DataFrame) -> str:
    lo, hi = int(df["season"].min()), int(df["season"].max())
    return str(lo) if lo == hi else f"{lo}{F.EN_DASH}{hi}"


def _mode_rows(sb: pl.DataFrame | None, mode: str) -> pl.DataFrame:
    if sb is None or sb.is_empty():
        return pl.DataFrame()
    return sb.filter(pl.col("mode") == mode) if "mode" in sb.columns else sb


def scoreboard_highlights(
    live: pl.DataFrame | None,
    backtest: pl.DataFrame | None,
    season: int,
    week: int,
    mode: str = "live",
) -> list[str]:
    """2-3 code-made lines on the player projections for the digest of (season, week), from
    weeks before `week` (documentation/11). Live digests use live rows only; until a live
    week is scored they say so and quote the walk-forward backtests, labelled. A backtest
    digest uses its season's backtest rows before `week` (earlier seasons until then)."""
    rows = _mode_rows(live if mode == "live" else backtest, mode)
    this = (
        rows.filter((pl.col("season") == season) & (pl.col("week") < week)) if rows.height else rows
    )
    agg = _aggregate(this) if this.height else pl.DataFrame()
    if agg.height:
        weeks = F.weeks(int(this.select(pl.struct("season", "week").n_unique()).item()))
        return _highlight_lines(agg, f"so far this season ({weeks.display} scored)")
    back = _mode_rows(backtest, "backtest")
    if mode == "live":
        note = "no live week of player projections has been scored yet"
        earlier = back
        scope = "in walk-forward backtests"
    else:
        note = "no week of player projections has been scored yet this season"
        earlier = back.filter(pl.col("season") < season) if back.height else back
        scope = "in earlier backtest seasons"
    agg = _aggregate(earlier) if earlier.height else pl.DataFrame()
    if agg.is_empty():
        return [note]
    return [note, *_highlight_lines(agg, f"{scope} ({_seasons(earlier)})")[:2]]


def week_improvement(
    sb: pl.DataFrame | None, season: int, week: int, mode: str = "live"
) -> float | None:
    """The scorecard's `player_mae_vs_baseline` for a graded week: the n-weighted mean of the
    scoreboard's `improvement_pct` over that week's targets (positive = the model's MAE is
    lower than the rolling baseline's). Only rows of `mode`: the live season file also holds
    walk-forward re-runs of earlier weeks (`mode == "backtest"`), never quoted as live."""
    sb = _mode_rows(sb, mode)
    if sb.is_empty():
        return None
    w = sb.filter(
        (pl.col("season") == season)
        & (pl.col("week") == week)
        & pl.col("improvement_pct").is_not_null()
        & (pl.col("n_scored").fill_null(0) > 0)
    )
    if w.is_empty():
        return None
    n = w["n_scored"].cast(pl.Float64)
    return float((w["improvement_pct"] * n).sum() / n.sum())
