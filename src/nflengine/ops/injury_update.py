"""The Saturday injury update (`nfl weekly injury-update`; documentation/02 -> Weekly schedule,
06 -> Digest layout and Delivery; plan P07). Run by hand (D71).

Final injury designations come out on Friday, after Tuesday's digest. The update:

1. **Gate:** `flags.injury_update_enabled`, and the week's main run exists
   (`predictions_games.parquet` + `watchlist.parquet`; `predictions_players.parquet` optional).
2. **Before state:** the curated injury report + reserve list for the week, read *before*
   re-ingesting (a same-day snapshot is overwritten in place).
3. **Re-ingest** what moves between Tuesday and Saturday (`INGEST_SOURCES`,
   `NFLVERSE_DATASETS`: injuries, rosters, depth charts, schedules, ESPN injuries / news /
   scoreboard odds, the Odds API), then re-curate with the blocking quality checks. Ratings
   are not rebuilt: no games were played since Tuesday.
4. **Re-predict** games and players with `run_train(..., output="update")`: writes
   `predictions_games_update.parquet` / `predictions_players_update.parquet` in the run folder
   and nothing else (no models, W&B artifacts, aliases, status file or scoreboard rows).
5. **Compare** main vs update for every game that hasn't kicked off at update time (games
   that have get no new numbers and are left out): home win probability, predicted score,
   expected QBs; and every watch-list player: report status then (the watch list's own
   `injury_status`) vs now, and his new projection.
6. **Materiality** (doc 06): an unstarted game's home win probability moved by
   `injury_update.min_prob_move` (5 points) or more, or (with `watch_status_change`) a
   watch-list player's status changed.
7. **Write** `runs/<season>/week<NN>/injury_update.json` always; when material, a short
   code-written addendum (no LLM: every line is a change code can state exactly) to
   `reports/<season>/week<NN>-injury-update.md` and `injury_update.md` in the run folder.
8. **W&B** (optional): one `weekly-pipeline` / `injury-update` run with the summary, the game
   and watch-list tables and the `injury-update` artifact (type `digest`).

**Why a game moved** (`reasons`), in this order: an expected-QB change, a market line move
of half a point or more, then up to `MAX_PLAYERS_PER_TEAM` players per team whose status
worsened since the before state to Doubtful / Out / IR, most important first. Importance is
the player's average snap share (offense or defense, whichever is larger) over his last 3
games this season, from the curated `snaps`: a starter on either side of the ball plays
most snaps, a backup listed Out barely registers, and it needs no position-specific rules.

**Status scale.** The report status is `injuries.report_status` mapped exactly as the
player features map it (`features.player.STATUS_ORD`: Questionable / Doubtful / Out, anything
else = not listed), which is how the watch list's `injury_status` was filled (via
`avail_status` in `models.player_runs.assemble`). IR is not on the injury report: a player
moved to a reserve list (`rosters_weekly.status == 'RES'`) since the before state shows "IR".
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from nflengine.paths import DataPaths, ensure_data_root
from nflengine.settings import get_config
from nflengine.tracking import git_commit, init_run

ET = ZoneInfo("America/New_York")
KIND = "injury-update"
JSON_FILE = "injury_update.json"
MD_FILE = "injury_update.md"
GAMES_FILE = "predictions_games.parquet"
PLAYERS_FILE = "predictions_players.parquet"
WATCH_FILE = "watchlist.parquet"
INGEST_SOURCES = ["nflverse", "espn", "odds_api"]
NFLVERSE_DATASETS = ["injuries", "rosters_weekly", "depth_charts", "schedules"]
REPORT_STATUSES = ("Questionable", "Doubtful", "Out")  # as features.player.STATUS_ORD
SEVERITY = {None: 0, "Questionable": 1, "Doubtful": 2, "Out": 3, "IR": 4}
WORSE_FROM = 2  # Doubtful or worse counts as a reason a game moved


def material_status_change(then: str | None, now: str | None) -> bool:
    """A watch-list status change that matters (D73): one involving Doubtful, Out or IR
    (Questionable -> Doubtful, Doubtful -> Out, Out -> cleared, a move to IR ...). A plain
    Questionable tag added or removed isn't one: Tuesday's watch list usually predates the
    week's report, and most Questionable players play."""
    if then == now:
        return False
    return max(SEVERITY.get(then, 0), SEVERITY.get(now, 0)) >= WORSE_FROM


MAX_PLAYERS_PER_TEAM = 3
LINE_MOVE = 0.5  # points of spread
EPS = 1e-9  # 0.60 - 0.55 is 0.04999...: a move "at" the threshold counts


def _open_run(*args: Any, **kwargs: Any) -> Any:
    return init_run(*args, **kwargs)


class InjuryUpdateError(RuntimeError):
    """The update can't run (disabled, no main run) or a required step failed."""


@dataclass
class InjuryUpdateResult:
    material: bool
    report_path: Path | None
    summary_path: Path
    diff: dict[str, Any]
    url: str | None
    notes: list[str] = field(default_factory=list)
    started: dt.datetime | None = None
    finished: dt.datetime | None = None


# ---- inputs ------------------------------------------------------------------------------------


def _rule() -> tuple[bool, float, bool]:
    cfg = get_config()
    iu = cfg.injury_update or {}
    return (
        bool(cfg.flags.get("injury_update_enabled", False)),
        float(iu.get("min_prob_move", 0.05)),
        bool(iu.get("watch_status_change", True)),
    )


def check_main_run(run_dir: Path, season: int, week: int) -> None:
    missing = [f for f in (GAMES_FILE, WATCH_FILE) if not (run_dir / f).exists()]
    if missing:
        raise InjuryUpdateError(
            f"no main run for {season} week {week}: {', '.join(missing)} missing in {run_dir}. "
            "Run `nfl weekly run` first; the update compares against its numbers."
        )


def _scan(paths: DataPaths, table: str) -> pl.LazyFrame | None:
    p = paths.curated / f"{table}.parquet"
    return pl.scan_parquet(p) if p.exists() else None


def injury_state(paths: DataPaths, season: int, week: int) -> pl.DataFrame:
    """Each listed player's status for the week: `player_id, player, team, position,
    report_status` (Questionable / Doubtful / Out / null), `reserve` (on a reserve list) and
    `status` (`IR` when reserve, else the report status)."""
    schema = {
        "player_id": pl.String,
        "player": pl.String,
        "team": pl.String,
        "position": pl.String,
        "report_status": pl.String,
        "reserve": pl.Boolean,
    }
    wk = (pl.col("season") == season) & (pl.col("week") == week)
    inj = pl.DataFrame(schema=schema)
    if (lf := _scan(paths, "injuries")) is not None:
        inj = (
            lf.filter(wk & pl.col("gsis_id").is_not_null())
            .select(
                pl.col("gsis_id").alias("player_id"),
                pl.col("full_name").alias("player"),
                "team",
                "position",
                pl.when(pl.col("report_status").is_in(list(REPORT_STATUSES)))
                .then(pl.col("report_status"))
                .alias("report_status"),
                pl.lit(False).alias("reserve"),
            )
            .collect()
            .unique("player_id", keep="last", maintain_order=True)
            .cast(schema)  # type: ignore[arg-type]
        )
    res = pl.DataFrame(schema=schema)
    if (lf := _scan(paths, "rosters_weekly")) is not None:
        res = (
            lf.filter(wk & (pl.col("status") == "RES") & pl.col("gsis_id").is_not_null())
            .select(
                pl.col("gsis_id").alias("player_id"),
                pl.col("full_name").alias("player"),
                "team",
                "position",
                pl.lit(None, dtype=pl.String).alias("report_status"),
                pl.lit(True).alias("reserve"),
            )
            .collect()
            .unique("player_id", keep="last", maintain_order=True)
            .cast(schema)  # type: ignore[arg-type]
        )
    both = pl.concat([inj, res])
    out = both.group_by("player_id", maintain_order=True).agg(
        pl.col("player").drop_nulls().first(),
        pl.col("team").drop_nulls().first(),
        pl.col("position").drop_nulls().first(),
        pl.col("report_status").drop_nulls().first(),
        pl.col("reserve").any(),
    )
    return out.with_columns(
        pl.when(pl.col("reserve"))
        .then(pl.lit("IR"))
        .otherwise(pl.col("report_status"))
        .alias("status")
    )


def snap_shares(paths: DataPaths, season: int, week: int, n_games: int = 3) -> dict[str, float]:
    """player_id -> average snap share (offense or defense, the larger) over his last
    `n_games` games of `season` before `week`."""
    lf = _scan(paths, "snaps")
    if lf is None:
        return {}
    df = (
        lf.filter(
            (pl.col("season") == season) & (pl.col("week") < week) & pl.col("gsis_id").is_not_null()
        )
        .select(
            "gsis_id",
            "week",
            pl.max_horizontal(
                pl.col("offense_pct").fill_null(0), pl.col("defense_pct").fill_null(0)
            ).alias("share"),
        )
        .collect()
        .sort("gsis_id", "week")
        .group_by("gsis_id")
        .agg(pl.col("share").tail(n_games).mean())
    )
    return dict(zip(df["gsis_id"].to_list(), df["share"].to_list(), strict=True))


def snapshot_info(paths: DataPaths) -> dict[str, Any]:
    """Newest raw snapshot (date + when its file was written, local time) of the injury
    sources the update re-pulls."""
    out: dict[str, Any] = {}
    for source, dataset in (("nflverse", "injuries"), ("espn", "injuries")):
        base = paths.raw / source / dataset
        snaps = (
            sorted(p.name.split("=", 1)[1] for p in base.glob("snapshot=*"))
            if base.exists()
            else []
        )
        info: dict[str, Any] = {"snapshot": snaps[-1] if snaps else None, "written_at": None}
        if snaps:
            m = base / f"snapshot={snaps[-1]}" / "manifest.json"
            if m.exists():
                files = json.loads(m.read_text()).get("files", {})
                times = [f.get("written_at") for f in files.values() if f.get("written_at")]
                info["written_at"] = max(times) if times else None
        out[f"{source}_{dataset}"] = info
    return out


# ---- comparison --------------------------------------------------------------------------------


def _primary(df: pl.DataFrame) -> dict[str, dict[str, Any]]:
    return {r["game_id"]: r for r in df.filter(pl.col("is_primary")).iter_rows(named=True)}


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


def worsened(before: pl.DataFrame, after: pl.DataFrame) -> pl.DataFrame:
    """Players whose status is Doubtful or worse now and worse than in `before`."""
    sev = pl.col("status").replace_strict(SEVERITY, default=0, return_dtype=pl.Int32)
    a = after.with_columns(sev.alias("sev"))
    b = before.select("player_id", sev.alias("sev_before"), pl.col("status").alias("before"))
    j = a.join(b, on="player_id", how="left").with_columns(pl.col("sev_before").fill_null(0))
    return j.filter((pl.col("sev") >= WORSE_FROM) & (pl.col("sev") > pl.col("sev_before")))


def _spread_text(spread: float | None, home: str, away: str) -> str:
    from nflengine.digest.names import nickname

    if spread is None:
        return "no line"
    if abs(spread) < 0.25:
        return "pick'em"
    team = home if spread > 0 else away
    return f"{nickname(team)} by {abs(spread):g}"


def compare_games(
    main: pl.DataFrame,
    update: pl.DataFrame,
    cutoff: dt.datetime,
    min_move: float,
    before: pl.DataFrame,
    after: pl.DataFrame,
    shares: dict[str, float],
) -> tuple[list[dict[str, Any]], list[str]]:
    """(one dict per unstarted game, the game ids left out because they kicked off)."""
    from nflengine.digest.names import nickname

    m, u = _primary(main), _primary(update)
    hurt = worsened(before, after)
    games, excluded = [], []
    far = dt.datetime.max.replace(tzinfo=dt.UTC)
    for gid, r in sorted(m.items(), key=lambda kv: (kv[1]["kickoff_utc"] or far, kv[0])):
        if r["kickoff_utc"] is not None and r["kickoff_utc"] <= cutoff:
            excluded.append(gid)
            continue
        n = u.get(gid)
        if n is None:
            continue
        home, away = r["home_team"], r["away_team"]
        move = float(n["home_win_prob"]) - float(r["home_win_prob"])
        qb = {
            side: (r[f"{side}_qb_name"], n[f"{side}_qb_name"])
            for side in ("home", "away")
            if (r[f"{side}_qb_name"] or None) != (n[f"{side}_qb_name"] or None)
        }
        reasons = [
            f"{nickname(home if side == 'home' else away)} QB: {a or 'unknown'} -> {b or 'unknown'}"
            for side, (a, b) in qb.items()
        ]
        s0, s1 = _f(r.get("spread_line")), _f(n.get("spread_line"))
        line_moved = (s0 is None) != (s1 is None) or (
            s0 is not None and s1 is not None and abs(s1 - s0) >= LINE_MOVE
        )
        if line_moved:
            reasons.append(
                f"line: {_spread_text(s0, home, away)} -> {_spread_text(s1, home, away)}"
            )
        players = []
        for team in (away, home):
            t = hurt.filter(pl.col("team") == team).with_columns(
                pl.col("player_id")
                .replace_strict(shares, default=0.0, return_dtype=pl.Float64)
                .alias("snap_share")
            )
            t = t.sort(["snap_share", "player"], descending=[True, False]).head(
                MAX_PLAYERS_PER_TEAM
            )
            for p in t.iter_rows(named=True):
                players.append(
                    {
                        "team": team,
                        "player_id": p["player_id"],
                        "player": p["player"],
                        "position": p["position"],
                        "before": p["before"],
                        "now": p["status"],
                        "snap_share": round(float(p["snap_share"]), 3),
                    }
                )
                reasons.append(f"{p['player']} ({p['position']}, {nickname(team)}) {p['status']}")
        games.append(
            {
                "game_id": gid,
                "home_team": home,
                "away_team": away,
                "kickoff_utc": r["kickoff_utc"].isoformat() if r["kickoff_utc"] else None,
                "home_win_prob_then": round(float(r["home_win_prob"]), 4),
                "home_win_prob_now": round(float(n["home_win_prob"]), 4),
                "prob_move": round(move, 4),
                "moved": abs(move) >= min_move - EPS,
                "pred_home_points_then": round(float(r["pred_home_points"]), 2),
                "pred_away_points_then": round(float(r["pred_away_points"]), 2),
                "pred_home_points_now": round(float(n["pred_home_points"]), 2),
                "pred_away_points_now": round(float(n["pred_away_points"]), 2),
                "home_qb_then": r["home_qb_name"],
                "home_qb_now": n["home_qb_name"],
                "away_qb_then": r["away_qb_name"],
                "away_qb_now": n["away_qb_name"],
                "qb_change": bool(qb),
                "spread_then": s0,
                "spread_now": s1,
                "variant_then": r.get("variant"),
                "variant_now": n.get("variant"),
                "players": players,
                "reasons": reasons,
            }
        )
    return games, excluded


def _projection(row: dict[str, Any]) -> float | None:
    """The reader-facing projection, as the digest shows it: the mean for counts, P50
    otherwise (`player_watch.projection_expr`)."""
    if row.get("kind") == "count" and row.get("mean") is not None:
        return float(row["mean"])
    return _f(row.get("p50"))


def compare_watch(
    watch: pl.DataFrame,
    update_players: pl.DataFrame | None,
    before: pl.DataFrame,
    after: pl.DataFrame,
    cutoff: dt.datetime,
) -> tuple[list[dict[str, Any]], int]:
    """(one dict per watch-list player whose game hasn't kicked off, how many were left out
    because it had). `update_players` None = players weren't re-projected."""
    b = {r["player_id"]: r for r in before.iter_rows(named=True)}
    a = {r["player_id"]: r for r in after.iter_rows(named=True)}
    proj: dict[tuple[str, str, str], dict[str, Any]] = {}
    if update_players is not None:
        for r in update_players.iter_rows(named=True):
            proj[(r["player_id"], r["game_id"], r["target"])] = r
    has_status = "injury_status" in watch.columns
    rows, skipped = [], 0
    for w in watch.iter_rows(named=True):
        if w.get("kickoff_utc") is not None and w["kickoff_utc"] <= cutoff:
            skipped += 1
            continue
        pid = w["player_id"]
        then = w["injury_status"] if has_status else (b.get(pid) or {}).get("report_status")
        then = then if then in REPORT_STATUSES else None
        now_row = a.get(pid) or {}
        now = now_row.get("report_status")
        if now_row.get("reserve"):
            now = "IR"
        new = proj.get((pid, w["game_id"], w["target"])) if update_players is not None else None
        rows.append(
            {
                "player_id": pid,
                "player": w.get("player"),
                "team": w.get("team"),
                "position": w.get("position"),
                "game_id": w["game_id"],
                "target": w["target"],
                "target_label": w.get("target_label"),
                "unit": w.get("unit"),
                "status_then": then,
                "status_now": now,
                "status_changed": then != now,
                "status_material": material_status_change(then, now),
                "projection_then": _projection(w),
                "projection_now": _projection(new) if new else None,
                "reprojected": update_players is not None,
                "still_projected": None if update_players is None else new is not None,
            }
        )
    return rows, skipped


# ---- addendum ----------------------------------------------------------------------------------


def _et(ts: dt.datetime) -> str:
    t = ts.astimezone(ET)
    return f"{t:%a %b} {t.day}, {t.hour % 12 or 12}:{t:%M %p} ET"


def _local_to_et(text: str | None) -> str | None:
    if not text:
        return None
    try:
        return _et(dt.datetime.fromisoformat(text).astimezone())
    except ValueError:
        return None


def render_addendum(payload: dict[str, Any]) -> str:
    """The code-written addendum (markdown); numbers formatted as the digest formats them."""
    from nflengine.digest import format as F
    from nflengine.digest.build import matchup
    from nflengine.digest.names import nickname

    season, week = payload["season"], payload["week"]
    diff = payload["diff"]
    snap = payload["snapshots"]["after"].get("nflverse_injuries", {})
    pulled = _local_to_et(snap.get("written_at"))
    head = f"_Updated {_et(dt.datetime.fromisoformat(payload['update_time_utc']))}"
    if snap.get("snapshot"):
        head += f" · injury report snapshot {snap['snapshot']}"
        head += f" (pulled {pulled})" if pulled else ""
    lines = [f"# Injury update: {season} week {week}", "", head + "_", ""]
    lines += ["## What changed since Tuesday's digest", ""]
    shown = [g for g in diff["games"] if g["moved"] or g["qb_change"]]
    min_move = F.points(100 * payload["rule"]["min_prob_move"]).display
    if shown:
        lines += [
            "| Game | Home win % (then → now) | Predicted score (then → now) | Why |",
            "|---|---|---|---|",
        ]
        for g in shown:
            p0 = F.pct(g["home_win_prob_then"], cap=True).display
            p1 = F.pct(g["home_win_prob_now"], cap=True).display
            s0 = (
                f"{F.points(g['pred_away_points_then']).display}–"
                f"{F.points(g['pred_home_points_then']).display}"
            )
            s1 = (
                f"{F.points(g['pred_away_points_now']).display}–"
                f"{F.points(g['pred_home_points_now']).display}"
            )
            why = "; ".join(r.replace(" -> ", " → ") for r in g["reasons"]) or "no single cause"
            lines.append(
                f"| {matchup(g['home_team'], g['away_team'])} | {p0} → {p1} | {s0} → {s1} | {why} |"
            )
        lines += ["", "_Scores are away–home, as in the digest._", ""]
    else:
        lines += [f"No win probability moved {min_move} points or more.", ""]
    changes = [w for w in diff["watch"] if w["status_changed"] or w["still_projected"] is False]
    lines += ["## Players to watch", ""]
    if changes:
        lines += ["| Player | Status (then → now) | Projection (then → now) |", "|---|---|---|"]
        for w in changes:
            label, unit = w["target_label"] or w["target"], w["unit"] or "yards"
            p0 = (
                F.stat(w["projection_then"], label, unit).display
                if w["projection_then"] is not None
                else "–"
            )
            if not w["reprojected"]:
                p1 = "not re-projected"
            elif w["projection_now"] is None:
                p1 = "no longer projected"
            else:
                p1 = F.stat(w["projection_now"], label, unit).display
            who = f"{w['player']} ({nickname(w['team'])})"
            st = f"{w['status_then'] or 'not listed'} → {w['status_now'] or 'not listed'}"
            lines.append(f"| {who} | {st} | {p0} → {p1} |")
        lines.append("")
    else:
        lines += ["No watch-list player's status changed.", ""]
    excluded = diff["excluded_started"]
    if excluded:
        names = ", ".join(diff["excluded_names"])
        lines += [f"_Already kicked off, not compared: {names}._", ""]
    lines.append(
        "_Next week's report card still grades Tuesday's numbers; this update doesn't change them._"
    )
    return "\n".join(lines) + "\n"


# ---- W&B ---------------------------------------------------------------------------------------


def _log_wandb(run, payload: dict[str, Any], files: list[Path], season: int, week: int) -> None:
    import wandb

    diff, s = payload["diff"], payload["diff"]["summary"]
    run.summary.update({**s, **{f"seconds_{k}": v for k, v in payload["timings"].items()}})
    if diff["games"]:
        cols = [
            "game_id",
            "home_team",
            "away_team",
            "home_win_prob_then",
            "home_win_prob_now",
            "prob_move",
            "moved",
            "qb_change",
            "home_qb_then",
            "home_qb_now",
            "away_qb_then",
            "away_qb_now",
        ]
        g = pl.DataFrame(
            [{k: x[k] for k in cols} | {"reasons": "; ".join(x["reasons"])} for x in diff["games"]]
        )
        run.log({"injury_update/games": wandb.Table(dataframe=g.to_pandas())})
    if diff["watch"]:
        w = pl.DataFrame(diff["watch"])
        run.log({"injury_update/watch": wandb.Table(dataframe=w.to_pandas())})
    art = wandb.Artifact(
        KIND,
        type="digest",
        description=f"Saturday injury update, {season} week {week}",
        metadata={"material": payload["material"], **s},
    )
    for f in files:
        art.add_file(str(f))
    run.log_artifact(art, aliases=[f"{season}-w{week:02d}"])


# ---- the run -----------------------------------------------------------------------------------


def run_injury_update(
    season: int,
    week: int,
    *,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    use_wandb: bool = True,
    ingest: bool = True,
    players: bool = True,
    now: dt.datetime | None = None,
) -> InjuryUpdateResult:
    """Re-pull injuries / news / lines, re-predict the week's unstarted games and players, and
    publish an addendum only if something material changed (see the module doc)."""
    started = dt.datetime.now(dt.UTC)
    enabled, min_move, watch_rule = _rule()
    if not enabled:
        raise InjuryUpdateError(
            "the injury update is disabled (flags.injury_update_enabled in config/settings.yaml)"
        )
    paths = ensure_data_root()
    run_dir = paths.run_dir(season, week)
    check_main_run(run_dir, season, week)
    notes: list[str] = []
    timings: dict[str, float] = {}
    run, url = None, None
    if use_wandb:
        try:
            run = _open_run(
                "weekly-pipeline",
                KIND,
                config={
                    "season": season,
                    "week": week,
                    "ingest": ingest,
                    "players": players,
                    "min_prob_move": min_move,
                    "watch_status_change": watch_rule,
                    "git_commit": git_commit(),
                },
                tags=["p07", KIND, f"season:{season}", f"week:{week:02d}"],
                launched_by=launched_by,
                name=f"{KIND}-{season}-w{week:02d}",
            )
        except Exception as e:
            notes.append(f"W&B run not opened ({type(e).__name__})")
            log(f"[yellow]W&B run not opened ({type(e).__name__}); the update goes on[/]")
    try:
        before = injury_state(paths, season, week)  # before re-ingesting (same-day overwrite)
        snaps_before = snapshot_info(paths)
        if ingest:
            notes += _ingest_and_curate(season, log, timings)
        else:
            notes.append("ingest skipped: before and after use the same curated data")
        after = injury_state(paths, season, week)
        snaps_after = snapshot_info(paths)

        from nflengine.models import game_runs, player_runs

        t = time.perf_counter()
        out = game_runs.run_train(season, week, launched_by, log=log, output="update")
        timings["games"] = round(time.perf_counter() - t, 2)
        update_players = None
        if players:
            t = time.perf_counter()
            try:
                res = player_runs.run_train(
                    season, week, launched_by, log=log, use_wandb=False, output="update"
                )
                update_players = pl.read_parquet(res["predictions"])
            except Exception as e:  # fail-soft, as the weekly player step: games still compare
                notes.append(
                    f"player refit failed ({type(e).__name__}); watch list not re-projected"
                )
                log(f"player refit failed ({type(e).__name__}); watch list not re-projected")
            timings["players"] = round(time.perf_counter() - t, 2)
        else:
            notes.append("players not re-projected (--no-players)")

        # the save-time rule (P06 Sol review): a game that kicked off while the models
        # refit gets no new numbers, so the cutoff is taken after the refits
        cutoff = now or dt.datetime.now(dt.UTC)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=dt.UTC)
        t = time.perf_counter()
        main_games = pl.read_parquet(run_dir / GAMES_FILE)
        games, excluded = compare_games(
            main_games,
            pl.read_parquet(out["predictions"]),
            cutoff,
            min_move,
            before,
            after,
            snap_shares(paths, season, week),
        )
        watch, watch_skipped = compare_watch(
            pl.read_parquet(run_dir / WATCH_FILE), update_players, before, after, cutoff
        )
        diff = _diff(games, excluded, watch, watch_skipped, main_games)
        s = diff["summary"]
        timings["compare"] = round(time.perf_counter() - t, 2)
        material = s["games_moved"] > 0 or (watch_rule and s["watch_status_changes"] > 0)
        s["material"] = material
        finished = dt.datetime.now(dt.UTC)
        timings["total"] = round((finished - started).total_seconds(), 2)
        payload = {
            "season": season,
            "week": week,
            "kind": KIND,
            "material": material,
            "rule": {"min_prob_move": min_move, "watch_status_change": watch_rule},
            "update_time_utc": cutoff.isoformat(timespec="seconds"),
            "started": started.isoformat(timespec="seconds"),
            "finished": finished.isoformat(timespec="seconds"),
            "launched_by": launched_by,
            "options": {"ingest": ingest, "players": players},
            "snapshots": {"before": snaps_before, "after": snaps_after},
            "diff": diff,
            "timings": timings,
            "notes": notes,
            "files": {"games_update": str(out["predictions"])},
        }
        report_path = None
        files = []
        if material:
            text = render_addendum(payload)
            report_path = paths.report_path(season, week, kind=KIND)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(text, encoding="utf-8")
            (run_dir / MD_FILE).write_text(text, encoding="utf-8")
            payload["files"]["report"] = str(report_path)
            files.append(run_dir / MD_FILE)
            log(f"material change: addendum -> {report_path}")
        else:
            log(
                f"nothing material changed ({s['games_compared']} games compared, max move "
                f"{s['max_prob_move']:.3f}, {s['watch_status_changes']} watch-list status "
                "changes): no addendum"
            )
            old = paths.report_path(season, week, kind=KIND)
            if old.exists():  # published earlier: never withdrawn silently
                notes.append(f"an earlier addendum is still published: {old}")
                log(f"note: an earlier addendum is still published at {old}")
        summary_path = run_dir / JSON_FILE
        summary_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        files.insert(0, summary_path)
        if run is not None:
            try:
                _log_wandb(run, payload, files, season, week)
                url = run.url
                run.finish()
            except Exception as e:  # the files are written: never fail the update over W&B
                notes.append(f"W&B logging incomplete ({type(e).__name__})")
                log(f"[yellow]W&B logging incomplete ({type(e).__name__})[/]")
                with contextlib.suppress(Exception):
                    run.finish(exit_code=1)
            run = None
    except BaseException as e:
        if run is not None:
            with contextlib.suppress(Exception):
                run.summary.update({"error": type(e).__name__})  # never the message (secrets)
                run.finish(exit_code=1)
        raise
    return InjuryUpdateResult(
        material=material,
        report_path=report_path,
        summary_path=summary_path,
        diff=diff,
        url=url,
        notes=notes,
        started=started,
        finished=finished,
    )


def _ingest_and_curate(
    season: int, log: Callable[[str], None], timings: dict[str, float]
) -> list[str]:
    from nflengine.curate.build import build_all
    from nflengine.curate.quality import BLOCK, run_quality_checks
    from nflengine.ingest.runner import run_ingest

    t = time.perf_counter()
    results, _ = run_ingest(
        season=season, sources=INGEST_SOURCES, nflverse_datasets=NFLVERSE_DATASETS, log=log
    )
    timings["ingest"] = round(time.perf_counter() - t, 2)
    if any(r.status == "failed" and r.source == "nflverse" for r in results):
        bad = [r.dataset for r in results if r.status == "failed" and r.source == "nflverse"]
        raise InjuryUpdateError(f"nflverse re-ingest failed: {bad}")
    notes = [
        f"optional source failed: {r.source}/{r.dataset}" for r in results if r.status == "failed"
    ]
    t = time.perf_counter()
    build_all(log=log)
    blocked = [c.name for c in run_quality_checks() if not c.passed and c.level == BLOCK]
    timings["curate"] = round(time.perf_counter() - t, 2)
    if blocked:
        raise InjuryUpdateError(f"blocking quality checks failed: {blocked}")
    return notes


def _diff(
    games: list[dict[str, Any]],
    excluded: list[str],
    watch: list[dict[str, Any]],
    watch_skipped: int,
    main_games: pl.DataFrame,
) -> dict[str, Any]:
    from nflengine.digest.build import matchup

    names = {
        r["game_id"]: matchup(r["home_team"], r["away_team"])
        for r in main_games.filter(pl.col("is_primary")).iter_rows(named=True)
    }
    moves = [abs(g["prob_move"]) for g in games]
    return {
        "games": games,
        "excluded_started": excluded,
        "excluded_names": [names.get(g, g) for g in excluded],
        "watch": watch,
        "watch_excluded_started": watch_skipped,
        "summary": {
            "games_compared": len(games),
            "games_moved": sum(g["moved"] for g in games),
            "max_prob_move": round(max(moves), 4) if moves else 0.0,
            "qb_changes": sum(g["qb_change"] for g in games),
            "watch_compared": len(watch),
            "watch_status_changes": sum(w["status_material"] for w in watch),
            "watch_status_changes_any": sum(w["status_changed"] for w in watch),
            "watch_dropped": sum(w["still_projected"] is False for w in watch),
            "started_games_excluded": len(excluded),
        },
    }
