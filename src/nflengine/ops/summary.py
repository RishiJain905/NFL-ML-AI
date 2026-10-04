"""The record of one weekly run (P07; documentation/08 -> What each weekly pipeline run logs).

After every `nfl weekly run` (success, failure, "not ready" or a simulation) the pipeline
writes:

- `run_summary.json` in the week's run folder: the calendar plan, every step's status and
  time, data freshness, ingest and quality-check counts, model versions, the digest's
  checks, drift signals and alerts;
- one row in `pipeline_history.parquet` (`ops/records.py`);
- one W&B run, group `weekly-pipeline`, job type `pipeline` (`simulation` for `--as-of`
  runs), name `pipeline-<season>-w<NN>`, with the same numbers, tables, lineage to the
  week's artifacts and (if `ops.wandb_alerts`) W&B alerts.

All of it fails soft: a broken summary or an unreachable W&B never changes the run's result.
Text that reaches files or W&B goes through `scrub()` (URL query strings and secret-looking
`key=value` pairs removed), on top of the step-level sanitising (D44 / P05 `safe_error`).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.ops.records import (
    RUN_SUMMARY_FILE,
    Alert,
    DriftSignal,
    iso,
)

_URL_QUERY = re.compile(r"(\b[a-z][\w+.-]*://[^\s?'\"<>]+)\?[^\s'\"<>]*", re.I)
_URL_USERINFO = re.compile(r"(\b[a-z][\w+.-]*://)[^/@\s'\"<>]+@", re.I)
_AUTH_SCHEME = re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{6,}")
_SECRET_KV = re.compile(
    r"(?i)([\"']?)([\w-]*(?:key|token|secret|password|passwd|auth|credential)[\w-]*)\1"
    r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
# key-shaped strings with no name in front: OpenRouter / OpenAI style, W&B, GitHub, long hex
_KEY_SHAPES = re.compile(
    r"\b(?:sk-[\w-]{8,}|wandb_v1_[\w-]{8,}|gh[pousr]_[A-Za-z0-9]{16,}|[A-Fa-f0-9]{32,})\b"
)


def scrub(text: Any, limit: int = 400) -> str:
    """Make a free-text detail safe for files and W&B: drop URL query strings (the Odds API
    key travels as one) and URL user:password parts, mask `Bearer <token>`, secret-looking
    `name=value` / `"name": "value"` pairs and bare key-shaped strings, cap the length."""
    s = "" if text is None else str(text)
    s = _URL_QUERY.sub(r"\1?…", s)
    s = _URL_USERINFO.sub(r"\1***@", s)
    s = _AUTH_SCHEME.sub(lambda m: f"{m.group(1)} ***", s)
    s = _SECRET_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(1)}{m.group(3)}***", s)
    s = _KEY_SHAPES.sub("***", s)
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ---- data freshness ------------------------------------------------------------------------

# label -> (curated table, SQL for the newest week of the season)
CONTENT_WEEKS: dict[str, str] = {
    "play-by-play": "SELECT max(week) FROM plays WHERE season = ?",
    "player stats": "SELECT max(week) FROM player_games WHERE season = ?",
    "snap counts": "SELECT max(week) FROM snaps WHERE season = ?",
    "injury reports": "SELECT max(week) FROM injuries WHERE season = ?",
    "depth charts": "SELECT max(week) FROM depth_charts WHERE season = ?",
}


def _latest_manifest(paths) -> dict | None:
    runs = sorted((paths.raw / "_runs").glob("ingest-*.json"))
    if not runs:
        return None
    try:
        return json.loads(runs[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _recent_results(paths, manifests: int = 10) -> list[dict]:
    """The newest ingest result per dataset over the last few run manifests, so a partial
    ingest (the Saturday update, a schedule refresh) doesn't hide the other datasets."""
    out: dict[tuple[str, str], dict] = {}
    for path in sorted((paths.raw / "_runs").glob("ingest-*.json"))[-manifests:][::-1]:
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for r in m.get("results", []):
            out.setdefault((r.get("source"), r.get("dataset")), r)
    return list(out.values())


def freshness(
    paths, season: int, week: int | None, now: dt.datetime, stale_days: int = 7
) -> list[dict[str, Any]]:
    """One row per ingested dataset (snapshot age) plus one per weekly-content source (newest
    week in the curated data vs the week before the target). `stale` marks a dataset whose
    newest snapshot is older than `stale_days`, or content more than one week behind."""
    from nflengine.ingest.base import SnapshotStore

    rows: list[dict[str, Any]] = []
    store, research = SnapshotStore(paths.raw), SnapshotStore(paths.research)
    seen: set[tuple[str, str]] = set()
    for r in _recent_results(paths):
        key = (r.get("source"), r.get("dataset"))
        if key in seen or not all(key):
            continue
        seen.add(key)
        dates = store.snapshots(*key)
        research_only = not dates and bool(research.snapshots(*key))
        if research_only:  # e.g. nflverse participation: research/, never read live
            dates = research.snapshots(*key)
        snap = dates[-1] if dates else None
        age = (now.date() - dt.date.fromisoformat(snap)).days if snap else None
        status = r.get("status")
        stale = status != "skipped" and not research_only and (age is None or age > stale_days)
        note = f"last ingest: {status}" + ("; research only" if research_only else "")
        if status == "failed":
            note += f" ({scrub(r.get('detail'), 120)})"
        rows.append(
            {
                "source": key[0],
                "dataset": key[1],
                "snapshot_date": snap,
                "age_days": age,
                "newest_week": None,
                "expected_week": None,
                "status": status,
                "stale": bool(stale),
                "note": note,
            }
        )
    expected = (week - 1) if week else None
    weeks: dict[str, int | None] = {}
    try:
        import duckdb

        con = duckdb.connect(str(paths.curated / "nfl.duckdb"), read_only=True)
        try:
            for label, sql in CONTENT_WEEKS.items():
                try:
                    v = con.execute(sql, [season]).fetchone()
                    weeks[label] = int(v[0]) if v and v[0] is not None else None
                except Exception:
                    weeks[label] = None
        finally:
            con.close()
        from nflengine.digest.under_hood import source_weeks

        weeks.update(source_weeks(season, paths))
    except Exception as e:  # freshness is bookkeeping: never fail the run over it
        rows.append(
            {
                "source": "curated",
                "dataset": "(all)",
                "snapshot_date": None,
                "age_days": None,
                "newest_week": None,
                "expected_week": expected,
                "status": "unavailable",
                "stale": False,
                "note": f"content weeks unavailable ({type(e).__name__})",
            }
        )
    for label, newest in weeks.items():
        behind = (
            expected is not None and expected >= 1 and (newest is None or expected - newest > 1)
        )
        lag = "" if newest is None or expected is None else f"{expected - newest} week(s) behind"
        rows.append(
            {
                "source": "content",
                "dataset": label,
                "snapshot_date": None,
                "age_days": None,
                "newest_week": newest,
                "expected_week": expected,
                "status": "ok" if not behind else "behind",
                "stale": bool(behind),
                "note": (lag if lag and not lag.startswith("0") else "current")
                if newest is not None
                else "no weeks yet",
            }
        )
    return rows


# ---- the summary ---------------------------------------------------------------------------


def _aware(t: dt.datetime) -> dt.datetime:
    return t.astimezone() if t.tzinfo is None else t  # naive = local (pre-P07 files)


def _seconds(step: dict) -> float | None:
    try:
        a = _aware(dt.datetime.fromisoformat(step["started"]))
        b = _aware(dt.datetime.fromisoformat(step["finished"]))
    except (KeyError, TypeError, ValueError):
        return None
    return round((b - a).total_seconds(), 1)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _quality(paths) -> dict[str, Any] | None:
    q = _read_json(paths.curated / "_quality" / "latest.json")
    if not q:
        return None
    res = q.get("results", [])
    failed = [r["name"] for r in res if not r.get("passed")]
    return {
        "run_at": q.get("run_at"),
        "checks": len(res),
        "failed": failed,
        "blocking_failed": [
            r["name"] for r in res if not r.get("passed") and r.get("level") == "block"
        ],
    }


def _ingest(paths) -> dict[str, Any] | None:
    m = _latest_manifest(paths)
    if not m:
        return None
    res = m.get("results", [])
    return {
        "snapshot_date": m.get("snapshot_date"),
        "finished": m.get("finished"),
        "datasets": len(res),
        "failed": [f"{r['source']}/{r['dataset']}" for r in res if r.get("status") == "failed"],
        "rows": {f"{r['source']}/{r['dataset']}": r.get("rows") for r in res},
    }


def _digest(run_dir: Path, ran: bool) -> dict[str, Any] | None:
    """The digest's checks, only if the digest step ran in this run (else a stale file)."""
    if not ran:
        return None
    checks = _read_json(run_dir / "checks.json")
    payload = _read_json(run_dir / "payload.json") or {}
    if checks is None:
        return None
    final = checks.get("final") or {}
    meta = payload.get("meta") or {}
    return {
        "checks_passed": bool(checks.get("passed")),
        "failed_checks": list(final.get("failed") or []),
        "warnings": list(final.get("warnings") or []),
        "regenerated": bool(checks.get("regenerated")),
        "banner": bool(checks.get("banner")),
        "writers": checks.get("writers"),
        "market_data_used": meta.get("market_data_used"),
        "graph_status": meta.get("graph_status"),
        "model_versions": meta.get("model_versions"),
    }


def _model_versions(run_dir: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, file in (
        ("game", "predictions_games.parquet"),
        ("player", "predictions_players.parquet"),
    ):
        p = run_dir / file
        if p.exists():
            try:
                v = pl.read_parquet(p, columns=["model_version"])["model_version"].unique()
                out[name] = sorted(str(x) for x in v.to_list() if x is not None)
            except Exception:
                out[name] = None
    return out


def build_summary(
    *,
    kind: str,
    season: int,
    week: int | None,
    status: str,
    exit_code: int,
    started: dt.datetime,
    finished: dt.datetime,
    run_dir: Path | None,
    paths,
    plan: Any = None,
    state: dict | None = None,
    launched_by: str | None = None,
    as_of: dt.datetime | None = None,
    from_step: str | None = None,
    error: str | None = None,
    fresh: list[dict] | None = None,
    drift: list[DriftSignal] | None = None,
    alerts: list[Alert] | None = None,
    extra: dict[str, Any] | None = None,
    deadline_clock: dt.datetime | None = None,
) -> dict[str, Any]:
    """Everything about one weekly run, as plain JSON. `deadline_clock` is the moment the
    run finished on the simulated clock (`--as-of` + elapsed); live it's `finished`."""
    steps: dict[str, Any] = {}
    run_started = iso(started)
    for name, st in (state or {}).get("steps", {}).items():
        # weekly_run.json keeps earlier runs' steps: only this run's count
        this_run = _step_in_run(st, started)
        steps[name] = {
            "status": st.get("status"),
            "seconds": _seconds(st),
            "detail": scrub(st.get("detail")),
            "this_run": this_run,
        }
    digest_ran = steps.get("digest", {}).get("this_run") and steps["digest"]["status"] == "ok"
    deadline = getattr(plan, "deadline", None)
    published = bool(digest_ran)
    hours_before = (
        round((deadline - (deadline_clock or finished)).total_seconds() / 3600, 2)
        if deadline is not None and published
        else None
    )
    out: dict[str, Any] = {
        "season": season,
        "week": week,
        "kind": kind,
        "run_id": started.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ"),
        "started": run_started,
        "finished": iso(finished),
        "total_seconds": round((finished - started).total_seconds(), 1),
        "status": status,
        "exit_code": exit_code,
        "error": scrub(error) if error else None,
        "launched_by": launched_by,
        "as_of": iso(as_of) if as_of else None,
        "from_step": from_step,
        "plan": plan.as_dict() if plan is not None else None,
        "deadline": iso(deadline),
        "published": published,
        "on_time": (hours_before > 0) if hours_before is not None else None,
        "hours_before_deadline": hours_before,
        "steps": steps,
        "degraded_steps": [
            n for n, s in steps.items() if s["this_run"] and s["status"] == "degraded"
        ],
        "failed_step": next(
            (n for n, s in steps.items() if s["this_run"] and s["status"] == "failed"), None
        ),
        "freshness": fresh,
        "stale_sources": [f"{r['source']}/{r['dataset']}" for r in (fresh or []) if r.get("stale")],
        "ingest": _ingest(paths) if steps.get("ingest", {}).get("this_run") else None,
        "quality": _quality(paths) if steps.get("curate", {}).get("this_run") else None,
        "models": _model_versions(run_dir) if run_dir is not None else {},
        "digest": _digest(run_dir, bool(digest_ran)) if run_dir is not None else None,
        "drift": [s.as_dict() for s in (drift or [])],
        "alerts": [
            {**a.as_dict(), "title": scrub(a.title, 160), "text": scrub(a.text, 800)}
            for a in (alerts or [])
        ],
    }
    if extra:
        out.update(extra)
    return out


def _step_in_run(step: dict, started: dt.datetime) -> bool:
    try:
        t = dt.datetime.fromisoformat(step["started"])
    except (KeyError, TypeError, ValueError):
        return False
    if t.tzinfo is None:  # weekly_run.json writes naive local time
        t = t.astimezone()
    return t >= started.replace(microsecond=0) - dt.timedelta(seconds=1)


def write_summary(run_dir: Path, summary: dict[str, Any]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / RUN_SUMMARY_FILE
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return path


def history_row(summary: dict[str, Any]) -> dict[str, Any]:
    d = summary.get("digest") or {}
    return {
        "season": summary["season"],
        "week": summary["week"],
        "kind": summary["kind"],
        "run_id": summary["run_id"],
        "started": summary["started"],
        "finished": summary["finished"],
        "status": summary["status"],
        "total_seconds": summary["total_seconds"],
        "deadline": summary["deadline"],
        "on_time": summary["on_time"],
        "hours_before_deadline": summary["hours_before_deadline"],
        "failed_step": summary["failed_step"],
        "degraded_steps": ",".join(summary["degraded_steps"]) or None,
        "checks_passed": d.get("checks_passed"),
        "regenerated": d.get("regenerated"),
        "banner": d.get("banner"),
        "drift_alerts": sum(1 for s in summary["drift"] if s.get("status") == "alert"),
        "launched_by": summary.get("launched_by"),
    }


# ---- W&B -----------------------------------------------------------------------------------

STATUS_CODE = {"ok": 0, "degraded": 1, "not_ready": 2, "failed": 3, "already_done": 4, "locked": 5}


def log_pipeline_run(
    summary: dict[str, Any],
    *,
    alerts_enabled: bool = True,
    log: Callable[[str], None] = print,
) -> str | None:
    """One W&B run for the whole weekly run (fail-soft: returns None if W&B is down)."""
    try:
        import wandb

        from nflengine.tracking import init_run
    except Exception:
        return None
    season, week, kind = summary["season"], summary["week"], summary["kind"]
    job = "simulation" if kind == "simulation" else "pipeline"
    tag = f"{season}-w{week:02d}" if week else f"{season}"
    try:
        run = init_run(
            "weekly-pipeline",
            job,
            config={
                "season": season,
                "week": week,
                "kind": kind,
                "as_of": summary.get("as_of"),
                "from_step": summary.get("from_step"),
                "deadline": summary.get("deadline"),
                "phase": (summary.get("plan") or {}).get("phase"),
                "special": ((summary.get("plan") or {}).get("slate") or {}).get("special"),
            },
            tags=["p07", job, f"season:{season}", f"week:{week or 0:02d}", summary["status"]],
            launched_by=summary.get("launched_by"),
            name=f"{'sim' if job == 'simulation' else 'pipeline'}-{tag}",
        )
    except Exception as e:
        log(f"[yellow]W&B pipeline run skipped ({type(e).__name__})[/]")
        return None
    failed = summary["status"] == "failed"
    try:
        flat: dict[str, Any] = {
            "status": summary["status"],
            "status_code": STATUS_CODE.get(summary["status"], -1),
            "exit_code": summary["exit_code"],
            "total_minutes": round(summary["total_seconds"] / 60, 2),
            "published": summary["published"],
            "on_time": summary["on_time"],
            "hours_before_deadline": summary["hours_before_deadline"],
            "failed_step": summary["failed_step"],
            "degraded_steps": ",".join(summary["degraded_steps"]),
            "stale_sources": len(summary["stale_sources"]),
            "drift_alerts": sum(1 for s in summary["drift"] if s["status"] == "alert"),
            "alerts": len(summary["alerts"]),
        }
        for name, st in summary["steps"].items():
            if st["this_run"]:
                flat[f"step/{name}_seconds"] = st["seconds"]
                flat[f"step/{name}_status"] = st["status"]
        ing = summary.get("ingest") or {}
        if ing:
            flat["ingest/datasets"] = ing.get("datasets")
            flat["ingest/failed"] = len(ing.get("failed") or [])
        q = summary.get("quality") or {}
        if q:
            flat["quality/checks"] = q.get("checks")
            flat["quality/failed"] = len(q.get("failed") or [])
        d = summary.get("digest") or {}
        if d:
            flat.update(
                {
                    "checks_passed": d.get("checks_passed"),
                    "regenerated": d.get("regenerated"),
                    "banner": d.get("banner"),
                    "market_data_used": d.get("market_data_used"),
                    "graph_status": d.get("graph_status"),
                }
            )
        for s in summary["drift"]:
            key = s["name"] + (f"/{s['group']}" if s.get("group") else "")
            flat[f"drift/{key}"] = s["status"]
            if s.get("value") is not None:
                flat[f"drift/{key}_value"] = s["value"]
        run.summary.update({k: v for k, v in flat.items() if v is not None})
        steps = [
            [n, st["status"], st["seconds"], st["this_run"], st["detail"]]
            for n, st in summary["steps"].items()
        ]
        run.log(
            {
                "steps": wandb.Table(
                    columns=["step", "status", "seconds", "this_run", "detail"], data=steps
                )
            }
        )
        timed = [[n, st["seconds"]] for n, st in summary["steps"].items() if st["this_run"]]
        if timed:
            run.log(
                {
                    "step_seconds": wandb.plot.bar(
                        wandb.Table(columns=["step", "seconds"], data=timed),
                        "step",
                        "seconds",
                        title="Seconds per step",
                    )
                }
            )
        if summary.get("freshness"):
            cols = ["source", "dataset", "snapshot_date", "age_days", "newest_week"]
            cols += ["expected_week", "status", "stale", "note"]
            run.log(
                {
                    "freshness": wandb.Table(
                        columns=cols,
                        data=[[r.get(c) for c in cols] for r in summary["freshness"]],
                    )
                }
            )
        if summary["drift"]:
            cols = ["name", "group", "status", "value", "threshold", "detail", "response"]
            run.log(
                {
                    "drift": wandb.Table(
                        columns=cols, data=[[s.get(c) for c in cols] for s in summary["drift"]]
                    )
                }
            )
        if kind != "simulation":
            _use_artifacts(run, summary, log)
        if alerts_enabled and kind != "simulation":  # a past week never pages anyone
            for a in summary["alerts"]:
                try:
                    level = {"info": "INFO", "warn": "WARN", "error": "ERROR"}[a["level"]]
                    run.alert(
                        title=scrub(a["title"], 60),
                        text=scrub(a["text"], 800),
                        level=getattr(wandb.AlertLevel, level),
                        wait_duration=0,
                    )
                except Exception as e:
                    log(f"[yellow]W&B alert not sent ({type(e).__name__})[/]")
        url = run.url
    except Exception as e:
        log(f"[yellow]W&B pipeline logging incomplete ({type(e).__name__})[/]")
        url = getattr(run, "url", None)
    finally:
        with contextlib.suppress(Exception):
            run.finish(exit_code=1 if failed else 0)
    return url


def _use_artifacts(run, summary: dict[str, Any], log: Callable[[str], None]) -> None:
    """Lineage: the week's artifacts this run produced (or used), by their week alias."""
    season, week = summary["season"], summary["week"]
    if not week:
        return
    alias = f"{season}-w{week:02d}"
    wanted = {
        "game": "game-model",
        "graph": "graph-results",
        "player": "player-model",
        "digest": "digest",
    }
    for step, name in wanted.items():
        st = summary["steps"].get(step) or {}
        if st.get("status") != "ok":
            continue
        try:
            run.use_artifact(f"{name}:{alias}")
        except Exception:
            log(f"[yellow]lineage: {name}:{alias} not found[/]")
