"""Drift checks (documentation/08 -> Drift signals and responses; P07, decision D71).

Five signals, evaluated for the run of week N, which grades weeks before N. They raise
alerts and never change anything: no retuning, no refit, no model swap.

- `game_vs_elo`: saved primary game predictions + results. Alert when the model is behind
  Elo on the rolling `rolling_weeks` Brier in each of the last `streak_weeks` windows.
- `calibration`: the same graded games, season to date. Alert when the ECE of the shown
  probabilities is above `ece_max`.
- `player_vs_baseline` (one signal per position group): the accuracy scoreboard. Alert when
  the model's MAE is behind the rolling baseline's, same windows as the games.
- `data_freshness`: the freshness list the caller passes. Alert when any source is stale.
- `checks`: `pipeline_history.parquet`. Alert when more than `checks_fail_rate_max` of the
  last `checks_window_weeks` digests failed their final checks.

Windows, in one place (`rolling_gaps`):
- A *graded week* is a week before N with at least one graded game (a saved primary
  prediction made before kickoff, with a final score). Missing weeks are skipped, not zero.
- The window at graded week k holds graded weeks k - rolling_weeks + 1 ... k, weighted by
  games. Early in the season a partial window (fewer than `rolling_weeks` weeks) is **not**
  used: it needs `min_window_weeks` weeks (default = `rolling_weeks`).
- The streak is over consecutive windows, so a signal needs `min_window_weeks +
  streak_weeks - 1` graded weeks (6 with the defaults) before it can alert; below that it is
  `insufficient_data`.

`replay_drift` runs the same functions week by week over the walk-forward backtests, to
measure how often each alert would have fired in past seasons (the false-alarm rate).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nflengine.digest.players import BACKTEST_SCOREBOARD, LIVE_SCOREBOARD
from nflengine.digest.report_card import PRED_FILE, grade_games, load_saved
from nflengine.digest.run import read_games
from nflengine.models import metrics as M
from nflengine.models.player_schema import GROUPS
from nflengine.ops.records import Alert, DriftSignal, history_path, latest_runs, read_history
from nflengine.paths import DataPaths, ensure_data_root
from nflengine.settings import get_config

# Used where `config/settings.yaml` -> `drift` lacks a key (and by tests).
DEFAULTS: dict[str, Any] = {
    "rolling_weeks": 4,
    "streak_weeks": 3,
    "ece_max": 0.05,
    "ece_min_games": 64,
    "stale_days": 7,
    "checks_window_weeks": 4,
    "checks_fail_rate_max": 0.20,
}
# Optional keys (read with `.get`, not in settings.yaml): `min_window_weeks` (default
# `rolling_weeks`: no partial windows), `game_margin` (Brier gap that counts as "worse",
# default 0), `player_margin` (percent of MAE that counts as "worse", default 0),
# `ece_noise_adjust` (default false: when true the ECE limit is raised to the 90th
# percentile of what a perfectly calibrated model shows at the same number of games).

WEEKLY_SCHEMA = {
    "week": pl.Int32,
    "games": pl.Int64,
    "brier_model": pl.Float64,
    "brier_elo": pl.Float64,
}
SB_COLS = ("season", "week", "target", "position_group", "n_scored", "mae_model", "mae_baseline")

R_GAME = (
    "Investigate the features and the data (QB changes, injuries, stale ratings, a bad "
    "week of inputs). Nothing is retuned automatically."
)
R_CALIBRATION = (
    "The v0 game model has no calibration layer to refit (game_model.calibration is none, "
    "D48), so check calibration on the walk-forward backtest (`nfl backtest game`) before "
    "switching game_model.calibration to platt or isotonic. Nothing changes automatically."
)
R_PLAYER = (
    "Check feature freshness (snap counts, NGS and PFR lag) and role changes for this "
    "position group. Nothing is retuned automatically."
)
R_FRESH = (
    "The digest footer already notes it. Check `nfl data-status`, then re-ingest the stale "
    "source (`nfl weekly run --from-step ingest`)."
)
R_CHECKS = (
    "Revisit the prompt files or the check rules (digest-checks skill) and look at which "
    "checks failed in the last digests."
)
NO_ACTION = "No action needed."


def resolve_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """The drift thresholds: `cfg` (or `settings.yaml` -> `drift`) over `DEFAULTS`."""
    given = cfg if cfg is not None else get_config().drift
    return {**DEFAULTS, **(given or {})}


def min_window_weeks(cfg: dict[str, Any]) -> int:
    return int(cfg.get("min_window_weeks") or cfg["rolling_weeks"])


def _needed(cfg: dict[str, Any]) -> int:
    """Graded weeks before a streak of rolling windows can exist."""
    return min_window_weeks(cfg) + int(cfg["streak_weeks"]) - 1


def _weeks(ws: Sequence[int]) -> str:
    ws = sorted(ws)
    if not ws:
        return "none"
    if ws == list(range(ws[0], ws[-1] + 1)):
        return f"{ws[0]}" if len(ws) == 1 else f"{ws[0]}-{ws[-1]}"
    return ", ".join(str(w) for w in ws)


def _signal(name: str, status: str, **kw: Any) -> DriftSignal:
    kw.setdefault("value", None)
    kw.setdefault("threshold", None)
    kw.setdefault("response", NO_ACTION)
    return DriftSignal(name=name, status=status, **kw)


# ---- games: Brier vs Elo, calibration -----------------------------------------------------------


def graded_games(preds: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """Graded rows of saved predictions: `week, home_win, home_win_prob, elo_prob`.

    Same grading as the digest's report card (`grade_games`: primary rows, made before
    kickoff, final score), so these numbers agree with the season scorecard. A game saved
    twice (a re-run of its week) counts once, the latest row.
    """
    cols = ["week", "home_win", "home_win_prob", "elo_prob"]
    if preds.is_empty():
        return pl.DataFrame(schema={"week": pl.Int32, **dict.fromkeys(cols[1:], pl.Float64)})
    g = grade_games(preds, games).filter(pl.col("graded") & pl.col("home_win_prob").is_not_null())
    if "created_at" in g.columns:
        g = g.sort("created_at")
    return g.unique(subset=["game_id"], keep="last", maintain_order=True).select(cols)


def weekly_brier(graded: pl.DataFrame) -> pl.DataFrame:
    """Per graded week: games, model Brier, Elo Brier, on the games that have both."""
    both = graded.filter(pl.col("home_win_prob").is_not_null() & pl.col("elo_prob").is_not_null())
    if both.is_empty():
        return pl.DataFrame(schema=WEEKLY_SCHEMA)
    return (
        both.group_by("week")
        .agg(
            pl.len().alias("games"),
            ((pl.col("home_win_prob") - pl.col("home_win")) ** 2).mean().alias("brier_model"),
            ((pl.col("elo_prob") - pl.col("home_win")) ** 2).mean().alias("brier_elo"),
        )
        .sort("week")
    )


def rolling_gaps(weekly: pl.DataFrame, window: int, min_window: int | None = None) -> list[dict]:
    """Rolling game-weighted Brier at every graded week that has a full window.

    One dict per graded week: `week`, `weeks` (the window), `games`, `brier_model`,
    `brier_elo`, `gap` (model - Elo; positive = the model is worse).
    """
    min_window = window if min_window is None else min_window
    rows = weekly.sort("week").to_dicts()
    out = []
    for i in range(len(rows)):
        win = rows[max(0, i - window + 1) : i + 1]
        if len(win) < min_window:
            continue
        n = sum(r["games"] for r in win)
        model = sum(r["games"] * r["brier_model"] for r in win) / n
        elo = sum(r["games"] * r["brier_elo"] for r in win) / n
        out.append(
            {
                "week": rows[i]["week"],
                "weeks": [r["week"] for r in win],
                "games": n,
                "brier_model": model,
                "brier_elo": elo,
                "gap": model - elo,
            }
        )
    return out


def game_vs_elo_signal(weekly: pl.DataFrame, cfg: dict[str, Any]) -> DriftSignal:
    """Alert when the model is behind Elo in each of the last `streak_weeks` rolling windows."""
    name = "game_vs_elo"
    streak = int(cfg["streak_weeks"])
    margin = float(cfg.get("game_margin") or 0.0)
    graded_weeks = weekly["week"].to_list()
    gaps = rolling_gaps(weekly, int(cfg["rolling_weeks"]), min_window_weeks(cfg))
    if len(gaps) < streak:
        return _signal(
            name,
            "insufficient_data",
            threshold=margin,
            weeks=graded_weeks,
            detail=(
                f"{len(graded_weeks)} graded week(s) so far; the check needs {_needed(cfg)} "
                f"({min_window_weeks(cfg)}-week window, {streak} windows in a row)."
            ),
        )
    last = gaps[-streak:]
    behind = sum(g["gap"] > margin for g in last)
    now = last[-1]
    looked = sorted({w for g in last for w in g["weeks"]})
    detail = (
        f"over graded weeks {_weeks(now['weeks'])} ({now['games']} games) the model's Brier is "
        f"{now['brier_model']:.4f} against Elo's {now['brier_elo']:.4f}; it was behind Elo in "
        f"{behind} of the last {streak} rolling windows."
    )
    alert = behind == streak
    return _signal(
        name,
        "alert" if alert else "ok",
        value=now["gap"],
        threshold=margin,
        detail=detail,
        response=R_GAME if alert else NO_ACTION,
        weeks=looked,
    )


def ece_noise(probs: np.ndarray, n_sims: int = 100, seed: int = 0) -> tuple[float, float]:
    """(mean, 90th percentile) of the ECE a perfectly calibrated model would show on games
    with these probabilities, by simulating outcomes (seeded, so it is repeatable). It is the
    noise floor: ECE on a few dozen games is far above zero even for a perfect model."""
    p = np.asarray(probs, dtype=float)
    if p.size == 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    sims = [M.ece(p, (rng.random(p.size) < p).astype(float)) for _ in range(n_sims)]
    return float(np.mean(sims)), float(np.quantile(sims, 0.9))


def calibration_signal(graded: pl.DataFrame, cfg: dict[str, Any]) -> DriftSignal:
    """Season-to-date ECE of the shown probabilities (10 equal-width bins) against `ece_max`."""
    name = "calibration"
    n = graded.height
    weeks = sorted(graded["week"].unique().to_list()) if n else []
    need = int(cfg["ece_min_games"])
    if n < need:
        return _signal(
            name,
            "insufficient_data",
            threshold=float(cfg["ece_max"]),
            weeks=weeks,
            detail=(
                f"{n} graded game(s) so far; season ECE is noise below {need} "
                f"(about {need // 16} weeks)."
            ),
        )
    ece = M.ece(graded["home_win_prob"], graded["home_win"])
    floor, floor90 = ece_noise(graded["home_win_prob"].to_numpy())
    limit = float(cfg["ece_max"])
    note = f"threshold {limit:g}"
    if cfg.get("ece_noise_adjust") and floor90 > limit:
        limit = floor90
        note = f"threshold {limit:.3f}, the chance level at this size"
    alert = ece > limit
    return _signal(
        name,
        "alert" if alert else "ok",
        value=ece,
        threshold=limit,
        detail=(
            f"season ECE of the shown probabilities is {ece:.3f} over {n} graded games "
            f"({note}; a perfectly calibrated model averages {floor:.3f} at this size)."
        ),
        response=R_CALIBRATION if alert else NO_ACTION,
        weeks=weeks,
    )


# ---- players: MAE vs the rolling baseline -------------------------------------------------------


def prepare_scoreboard(sb: pl.DataFrame | None, season: int, week: int) -> pl.DataFrame:
    """Scoreboard rows of `season` for weeks before `week`, scored, one per (week, target,
    group): live rows win over walk-forward backtest rows of the same week. Adds `mode`
    (`backtest` when the file has none)."""
    if sb is None or sb.is_empty():
        return pl.DataFrame()
    df = sb.with_columns(
        (pl.col("mode") if "mode" in sb.columns else pl.lit("backtest")).alias("mode")
    )
    df = df.filter(
        (pl.col("season") == season)
        & (pl.col("week") < week)
        & (pl.col("n_scored").fill_null(0) > 0)
        & pl.col("mae_model").is_not_null()
        & pl.col("mae_baseline").is_not_null()
    )
    if df.is_empty():
        return df
    return (
        df.with_columns((pl.col("mode") == "live").alias("_live"))
        .sort("_live")
        .unique(subset=["season", "week", "target", "position_group"], keep="last")
        .drop("_live")
        .sort("week", "target")
    )


def improvement_pct(rows: pl.DataFrame) -> float | None:
    """Percent by which the model's MAE beats the baseline's over `rows`.

    Targets are compared on their own scale (a carry is not a yard): per target,
    (baseline MAE - model MAE) / baseline MAE with each week's MAE weighted by its scored
    projections; the group's number is the mean over its targets weighted by projections.
    Positive = the model is better (the scoreboard's `improvement_pct` convention).
    """
    if rows.is_empty():
        return None
    n = pl.col("n_scored").cast(pl.Float64)
    per = (
        rows.group_by("target")
        .agg(
            n.sum().alias("n"),
            (n * pl.col("mae_model")).sum().alias("m"),
            (n * pl.col("mae_baseline")).sum().alias("b"),
        )
        .filter(pl.col("b") > 0)
    )
    if per.is_empty():
        return None
    return float(100 * ((per["b"] - per["m"]) / per["b"] * per["n"]).sum() / per["n"].sum())


def group_series(
    sb: pl.DataFrame, group: str, window: int | None, min_window: int | None = None
) -> list[dict]:
    """The group's improvement at every graded week, over the last `window` graded weeks
    (`None` = season to date). One dict per week: `week`, `weeks`, `improvement`, `n`, `live`
    (live weeks in the window), `backtest` (walk-forward weeks in the window)."""
    g = sb.filter(pl.col("position_group") == group) if sb.height else sb
    if g.is_empty():
        return []
    weeks = sorted(g["week"].unique().to_list())
    modes = g.group_by("week").agg((pl.col("mode") == "live").any().alias("live"))
    live_by_week = dict(zip(modes["week"].to_list(), modes["live"].to_list(), strict=True))
    out = []
    for i in range(len(weeks)):
        win = weeks[max(0, i - window + 1) : i + 1] if window else weeks[: i + 1]
        if min_window and len(win) < min_window:
            continue
        rows = g.filter(pl.col("week").is_in(win))
        imp = improvement_pct(rows)
        if imp is None:
            continue
        n_live = sum(live_by_week[w] for w in win)
        out.append(
            {
                "week": weeks[i],
                "weeks": win,
                "improvement": imp,
                "n": int(rows["n_scored"].sum()),
                "live": n_live,
                "backtest": len(win) - n_live,
            }
        )
    return out


def player_signal(sb: pl.DataFrame, group: str, cfg: dict[str, Any]) -> DriftSignal:
    """Alert when the group's model MAE is behind the rolling baseline's in each of the last
    `streak_weeks` rolling windows."""
    name = "player_vs_baseline"
    streak = int(cfg["streak_weeks"])
    margin = float(cfg.get("player_margin") or 0.0)
    series = group_series(sb, group, int(cfg["rolling_weeks"]), min_window_weeks(cfg))
    n_weeks = sb.filter(pl.col("position_group") == group)["week"].n_unique() if sb.height else 0
    if len(series) < streak:
        return _signal(
            name,
            "insufficient_data",
            group=group,
            threshold=margin,
            detail=(
                f"{group}: {n_weeks} scored week(s) so far; the check needs {_needed(cfg)} "
                f"({min_window_weeks(cfg)}-week window, {streak} windows in a row)."
            ),
        )
    last = series[-streak:]
    behind = sum(s["improvement"] < -margin for s in last)
    now = last[-1]
    kinds = []
    if now["backtest"]:
        kinds.append(f"{now['backtest']} walk-forward backtest week{'s' * (now['backtest'] != 1)}")
    if now["live"]:
        kinds.append(f"{now['live']} live week{'s' * (now['live'] != 1)}")
    alert = behind == streak
    return _signal(
        name,
        "alert" if alert else "ok",
        group=group,
        value=now["improvement"],
        threshold=margin,
        detail=(
            f"{group}: over scored weeks {_weeks(now['weeks'])} ({' + '.join(kinds)}, "
            f"{now['n']} projections) the model's MAE is {now['improvement']:+.1f}% against the "
            f"rolling baseline's; it was behind in {behind} of the last {streak} rolling windows."
        ),
        response=R_PLAYER if alert else NO_ACTION,
        weeks=sorted({w for s in last for w in s["weeks"]}),
    )


# ---- freshness, checks --------------------------------------------------------------------------


def freshness_signal(freshness: list[dict] | None, cfg: dict[str, Any]) -> DriftSignal:
    name = "data_freshness"
    limit = float(cfg["stale_days"])
    if not freshness:
        return _signal(
            name,
            "insufficient_data",
            threshold=limit,
            detail="no source freshness was reported for this run.",
        )
    ages = [float(f["age_days"]) for f in freshness if f.get("age_days") is not None]
    stale = [f for f in freshness if f.get("stale")]
    if stale:
        names = ", ".join(
            f"{f.get('source')}/{f.get('dataset')} "
            + (
                f"({f.get('snapshot_date')}, {f.get('age_days')} days)"
                if f.get("age_days") is not None
                else f"({f.get('note') or 'no snapshot'})"
            )
            for f in stale[:4]
        )
        more = f" and {len(stale) - 4} more" if len(stale) > 4 else ""
        return _signal(
            name,
            "alert",
            value=max(
                (float(f["age_days"]) for f in stale if f.get("age_days") is not None), default=None
            ),
            threshold=limit,
            detail=f"{len(stale)} of {len(freshness)} source(s) are stale: {names}{more}.",
            response=R_FRESH,
        )
    oldest = f"; the oldest is {max(ages):g} days old" if ages else ""
    return _signal(
        name,
        "ok",
        value=max(ages) if ages else None,
        threshold=limit,
        detail=f"all {len(freshness)} source(s) are fresh{oldest}.",
    )


def checks_signal(
    history: pl.DataFrame | None, season: int, week: int, cfg: dict[str, Any]
) -> DriftSignal:
    """Share of the last `checks_window_weeks` main digests whose final checks failed."""
    name = "checks"
    window = int(cfg["checks_window_weeks"])
    limit = float(cfg["checks_fail_rate_max"])
    runs = pl.DataFrame()
    if history is not None and not history.is_empty():
        runs = latest_runs(history, "main").filter(
            (
                (pl.col("season") < season)
                | ((pl.col("season") == season) & (pl.col("week") <= week))
            )
            & pl.col("checks_passed").is_not_null()
        )
    runs = runs.tail(window)
    if runs.height < window:
        return _signal(
            name,
            "insufficient_data",
            threshold=limit,
            weeks=runs["week"].to_list() if runs.height else [],
            detail=f"{runs.height} digest(s) with a checks verdict so far; needs {window}.",
        )
    failed = int((~runs["checks_passed"]).sum())
    rate = failed / runs.height
    regen = runs["regenerated"].fill_null(False).sum() if "regenerated" in runs.columns else 0
    alert = rate > limit
    return _signal(
        name,
        "alert" if alert else "ok",
        value=rate,
        threshold=limit,
        detail=(
            f"{failed} of the last {runs.height} digests failed their final checks "
            f"({rate:.0%}, limit {limit:.0%}); {int(regen)} needed a second writer pass."
        ),
        response=R_CHECKS if alert else NO_ACTION,
        weeks=runs["week"].to_list(),
    )


# ---- evaluate -----------------------------------------------------------------------------------


def scoreboard_from_disk(
    season: int, run_root: Path, paths: Callable[[], DataPaths]
) -> pl.DataFrame | None:
    """The season's live scoreboard (it holds walk-forward rows for weeks before the first
    live one); for a run root without one (a simulation) the backtest scoreboard."""
    live = run_root / str(season) / LIVE_SCOREBOARD
    if live.exists():
        return pl.read_parquet(live)
    back = paths().runs.joinpath(*BACKTEST_SCOREBOARD)
    return pl.read_parquet(back) if back.exists() else None


def evaluate_drift(
    season: int,
    week: int,
    *,
    run_root: Path | None = None,
    paths: DataPaths | None = None,
    freshness: list[dict] | None = None,
    history: pl.DataFrame | None = None,
    cfg: dict | None = None,
    games: pl.DataFrame | None = None,
    scoreboard: pl.DataFrame | None = None,
) -> list[DriftSignal]:
    """The drift signals for the run of `week` (graded through week - 1).

    `run_root`: live runs folder (default `paths.runs`) or a simulation's
    (`paths.runs / "digest-backtests"`). `freshness`: one dict per source (`source`,
    `dataset`, `snapshot_date`, `age_days`, `stale`, `note`); None leaves `data_freshness`
    at `insufficient_data`. `history`: the pipeline history (default: read from the run
    root). `games` / `scoreboard` replace the curated results / the accuracy scoreboard
    (tests, replays). A data source that can't be read leaves its signals at
    `insufficient_data` instead of raising.
    """
    cfg = resolve_cfg(cfg)
    memo: dict[str, DataPaths] = {}

    def get_paths() -> DataPaths:
        if "p" not in memo:
            memo["p"] = paths if paths is not None else ensure_data_root()
        return memo["p"]

    root = run_root if run_root is not None else get_paths().runs
    read_err: dict[str, str] = {}

    # games
    graded = pl.DataFrame()
    try:
        preds = load_saved(root, season, range(1, week), PRED_FILE)
        res = games if games is not None else read_games(get_paths())
        graded = graded_games(preds, res)
    except Exception as e:  # noqa: BLE001 - fail soft: report the kind, never the message
        read_err["games"] = type(e).__name__
    weekly = weekly_brier(graded) if graded.height else pl.DataFrame(schema=WEEKLY_SCHEMA)
    signals = [game_vs_elo_signal(weekly, cfg), calibration_signal(graded, cfg)]
    if "games" in read_err:
        for s in signals:
            s.detail = f"could not read the saved predictions or results ({read_err['games']})."

    # players
    sb = pl.DataFrame()
    try:
        raw = (
            scoreboard if scoreboard is not None else scoreboard_from_disk(season, root, get_paths)
        )
        sb = prepare_scoreboard(raw, season, week)
    except Exception as e:  # noqa: BLE001
        read_err["players"] = type(e).__name__
    groups = list(GROUPS) + [
        g for g in (sb["position_group"].unique().to_list() if sb.height else []) if g not in GROUPS
    ]
    for g in groups:
        s = player_signal(sb, g, cfg)
        if "players" in read_err:
            s.detail = f"{g}: could not read the accuracy scoreboard ({read_err['players']})."
        signals.append(s)

    signals.append(freshness_signal(freshness, cfg))

    hist = history
    if hist is None:
        try:
            hist = read_history(history_path(root, season))
        except Exception:  # noqa: BLE001 - an unreadable history leaves `checks` without data
            hist = None
    signals.append(checks_signal(hist, season, week, cfg))
    return signals


TITLES: dict[str, Callable[[DriftSignal, dict[str, Any]], str]] = {
    "game_vs_elo": lambda s, c: f"game model behind Elo for {c['streak_weeks']} weeks",
    "calibration": lambda s, c: f"game probabilities poorly calibrated (ECE {s.value:.3f})",
    "player_vs_baseline": lambda s, c: (
        f"{s.group} projections behind the baseline for {c['streak_weeks']} weeks"
    ),
    "data_freshness": lambda s, c: "stale data source",
    "checks": lambda s, c: f"digest checks failing ({s.value:.0%} of recent runs)",
}


def drift_alerts(
    signals: list[DriftSignal], season: int, week: int, *, cfg: dict | None = None
) -> list[Alert]:
    """One `warn` Alert per signal whose status is `alert` (source `drift:<name>`)."""
    cfg = resolve_cfg(cfg)
    out = []
    for s in signals:
        if s.status != "alert":
            continue
        what = TITLES.get(s.name, lambda s, c: s.name)(s, cfg)
        out.append(
            Alert(
                level="warn",
                title=f"{season} week {week}: {what}",
                text=f"{s.detail} {s.response}",
                source=f"drift:{s.name}",
            )
        )
    return out


# ---- replay over past seasons -------------------------------------------------------------------

REPLAY_SCHEMA = {
    "season": pl.Int32,
    "week": pl.Int32,  # the run week: graded through week - 1
    "signal": pl.String,
    "group": pl.String,
    "status": pl.String,
    "value": pl.Float64,
    "threshold": pl.Float64,
    "graded_weeks": pl.Int32,
    "n": pl.Int32,  # graded games (game signals) or projections in the window (player)
    "noise": pl.Float64,  # calibration: the ECE of a perfectly calibrated model (mean)
    "noise_p90": pl.Float64,
}


def replay_drift(
    seasons: list[int],
    *,
    variant: str = "model_only",
    paths: DataPaths | None = None,
    cfg: dict | None = None,
    players: bool = True,
) -> pl.DataFrame:
    """Replay `game_vs_elo`, `calibration` and `player_vs_baseline` week by week.

    Games come from the canonical walk-forward backtest `runs/backtests/game/<variant>`
    (regular season only; `model_only` is the honest one, `market` uses closing lines), the
    player rows from the P06 walk-forward scoreboard. Each run week N of a season is
    evaluated on weeks before N, exactly as `evaluate_drift` does live. One row per (season,
    run week, signal, group); `summarize_replay` turns it into alert rates.
    """
    cfg = resolve_cfg(cfg)
    paths = paths or ensure_data_root()
    bt = pl.read_parquet(paths.runs / "backtests" / "game" / variant / "predictions_games.parquet")
    bt = bt.filter((pl.col("game_type") == "REG") & pl.col("season").is_in(seasons))
    sb_all = None
    sb_path = paths.runs.joinpath(*BACKTEST_SCOREBOARD)
    if players and sb_path.exists():
        sb_all = pl.read_parquet(sb_path)
    rows: list[dict] = []
    for season in sorted(seasons):
        g_all = bt.filter(pl.col("season") == season).select(
            "week", "home_win", "home_win_prob", "elo_prob"
        )
        if g_all.is_empty():
            continue
        last = int(g_all["week"].max())
        for run_week in range(2, last + 1):
            graded = g_all.filter(pl.col("week") < run_week)
            weekly = weekly_brier(graded)
            sigs = [game_vs_elo_signal(weekly, cfg), calibration_signal(graded, cfg)]
            if sb_all is not None:
                sb = prepare_scoreboard(sb_all, season, run_week)
                sigs += [player_signal(sb, g, cfg) for g in GROUPS]
            for s in sigs:
                noise = noise90 = None
                if s.name == "calibration" and s.status != "insufficient_data":
                    noise, noise90 = ece_noise(graded["home_win_prob"].to_numpy())
                rows.append(
                    {
                        "season": season,
                        "week": run_week,
                        "signal": s.name,
                        "group": s.group,
                        "status": s.status,
                        "value": s.value,
                        "threshold": s.threshold,
                        "graded_weeks": len(s.weeks),
                        "n": graded.height if s.name != "player_vs_baseline" else None,
                        "noise": noise,
                        "noise_p90": noise90,
                    }
                )
    return pl.DataFrame(rows, schema=REPLAY_SCHEMA)


def count_episodes(weeks: Sequence[int]) -> int:
    """Runs of consecutive weeks: an alert that stays on for 4 weeks is one episode."""
    ws = sorted(weeks)
    return sum(1 for i, w in enumerate(ws) if i == 0 or w != ws[i - 1] + 1)


def summarize_replay(replay: pl.DataFrame) -> pl.DataFrame:
    """False-alarm view of a replay: per signal and group (and season; `season` null = all
    seasons together), the run weeks that could be evaluated, how many alerted, the share,
    the separate episodes, and which run weeks alerted."""
    key = ["signal", "group"]
    body = replay.filter(pl.col("status") != "insufficient_data")
    rows = []
    for (sig, grp), part in body.group_by(key, maintain_order=True):
        for season in [*sorted(part["season"].unique().to_list()), None]:
            p = part if season is None else part.filter(pl.col("season") == season)
            a = p.filter(pl.col("status") == "alert")
            eps = sum(
                count_episodes(a.filter(pl.col("season") == s)["week"].to_list())
                for s in a["season"].unique().to_list()
            )
            rows.append(
                {
                    "signal": sig,
                    "group": grp,
                    "season": season,
                    "evaluated": p.height,
                    "alerts": a.height,
                    "alert_share": a.height / p.height,
                    "episodes": eps,
                    "alert_weeks": sorted(a["week"].to_list()),
                }
            )
    schema = {
        "signal": pl.String,
        "group": pl.String,
        "season": pl.Int32,
        "evaluated": pl.Int64,
        "alerts": pl.Int64,
        "alert_share": pl.Float64,
        "episodes": pl.Int64,
        "alert_weeks": pl.List(pl.Int32),
    }
    return pl.DataFrame(rows, schema=schema).sort(*key, "season", nulls_last=True)
