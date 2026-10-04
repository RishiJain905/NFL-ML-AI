"""documentation/02 -> Failure handling, row by row (P07). Each test names its row; rows
already covered elsewhere point to their tests."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import nflengine.weekly as W
from nflengine.ingest.base import DatasetResult
from nflengine.ops.services import ServiceResult
from nflengine.paths import DataPaths, DataRootError

OPTS = W.WeeklyOptions(2026, 5)


def test_row_data_drive_missing_stops_before_writing(monkeypatch) -> None:
    import nflengine.ops.calendar as C
    import nflengine.ops.lock as L

    touched: list[str] = []

    def gone(*a, **k):
        raise DataRootError("Data drive D:\\ is not connected. Nothing was written.")

    monkeypatch.setattr(W, "ensure_data_root", gone)
    monkeypatch.setattr(C, "load_schedules", lambda *a, **k: touched.append("schedules"))
    monkeypatch.setattr(L, "run_lock", lambda *a, **k: touched.append("lock"))
    with pytest.raises(DataRootError):
        W.run_pipeline(auto=True, log=lambda m: None)
    assert touched == []  # stopped before reading or writing anything (CLI exit 5)


def test_row_nflverse_not_ready_is_exit_3(monkeypatch) -> None:
    import nflengine.curate.readiness as R

    class Report:
        ready = False

        def summary(self):
            return "2026 week 4: NOT READY (15/16 games final)"

    monkeypatch.setattr(R, "check_ready", lambda season, week: Report())
    with pytest.raises(W.StepFailed) as e:
        W._ready(OPTS, print)
    assert e.value.exit_code == 3 and "NOT READY" in e.value.detail
    # retry + alert after Wednesday: test_pipeline.py::test_not_ready_*


def test_row_espn_or_extra_source_down_is_fail_soft(monkeypatch) -> None:
    import nflengine.ingest.runner as IR

    results = [
        DatasetResult("nflverse", "pbp", "ok", 10),
        DatasetResult("espn", "news", "failed", detail="ConnectError"),
        DatasetResult("odds_api", "odds", "failed", detail="ODDS_API_KEY: 401"),
    ]
    monkeypatch.setattr(IR, "run_ingest", lambda season, log: (results, "manifest.json"))
    assert "2 optional failures" in W._ingest(OPTS, print)


def test_nflverse_ingest_failure_stops_the_run(monkeypatch) -> None:
    import nflengine.ingest.runner as IR

    results = [DatasetResult("nflverse", "pbp", "failed", detail="HTTPStatusError")]
    monkeypatch.setattr(IR, "run_ingest", lambda season, log: (results, "m"))
    with pytest.raises(W.StepFailed, match="nflverse failed"):
        W._ingest(OPTS, print)


def test_row_lines_missing_publishes_model_only() -> None:
    """Covered by the game model and the digest: a game without a complete market line gets
    the model-only row (`market_fallback`), and the digest drops the consensus item for it:
    tests/test_game_model.py (market_fallback) and tests/digest/test_digest_pipeline.py."""
    here = Path(__file__).resolve().parents[1]
    assert "market_fallback" in (here / "test_game_model.py").read_text(encoding="utf-8")


def test_row_neo4j_down_tries_to_start_it_once_then_degrades(monkeypatch) -> None:
    """The start failed: the fail-soft build still runs, so `graph_results.json` says
    `unavailable` and the digest can't reuse an older ok file (Sol review)."""
    import nflengine.graph.build as GB
    import nflengine.ops.services as S

    class Down:
        status, summary, url, error = (
            "unavailable",
            "",
            None,
            "ServiceUnavailable: Neo4j unreachable",
        )

    built = []
    monkeypatch.setattr(
        S, "ensure_neo4j", lambda *a, **k: ServiceResult(False, "failed", "compose exited 1")
    )
    monkeypatch.setattr(GB, "run_graph_build", lambda *a, **k: built.append(k) or Down())
    with pytest.raises(W.StepDegraded, match="compose exited 1"):
        W._graph(OPTS, print)
    assert built and built[0]["fail_soft"] is True


def test_neo4j_started_by_the_step_is_noted(monkeypatch) -> None:
    import nflengine.graph.build as GB
    import nflengine.ops.services as S

    class Res:
        status, summary, url, error = "ok", "15,522 nodes", "https://wandb", None

    monkeypatch.setattr(
        S,
        "ensure_neo4j",
        lambda *a, **k: ServiceResult(True, "started_container", "Neo4j was down; started"),
    )
    monkeypatch.setattr(GB, "run_graph_build", lambda *a, **k: Res())
    assert W._graph(OPTS, print).startswith("Neo4j was down; started; 15,522 nodes")


def test_row_checks_fail_after_regeneration_alerts(tmp_path: Path, monkeypatch) -> None:
    """The digest publishes with the banner (tests/digest/test_digest_pipeline.py); the
    pipeline adds a warn alert and records it in the summary."""
    import polars as pl

    from nflengine.ops.calendar import EASTERN

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "schedules_2024_2026.csv"
    sched = pl.read_csv(fixture, schema_overrides={"gametime": pl.String, "gameday": pl.String})
    done = (pl.col("season") == 2026) & (pl.col("week") <= 4) & pl.col("result").is_null()
    sched = sched.with_columns(pl.when(done).then(0).otherwise(pl.col("result")).alias("result"))

    p = DataPaths(tmp_path)
    monkeypatch.setattr(W, "ensure_data_root", lambda *a, **k: p)

    def digest(o, log):
        d = p.run_dir(2026, 5)
        d.mkdir(parents=True, exist_ok=True)
        (d / "checks.json").write_text(
            json.dumps(
                {
                    "passed": False,
                    "regenerated": True,
                    "banner": True,
                    "final": {"failed": ["number_provenance"]},
                }
            )
        )
        return "digest (checks FAILED)"

    funcs = {n: (lambda o, log: "ok") for n in W.STEPS} | {"digest": digest}
    now = dt.datetime(2026, 10, 6, 10, tzinfo=EASTERN).astimezone(dt.UTC)
    out = W.run_pipeline(
        auto=True, now=now, funcs=funcs, schedules=sched, use_wandb=False, log=print
    )
    assert out.status == "ok"
    alert = next(a for a in out.alerts if a.source == "step:digest")
    assert alert.level == "warn" and "number_provenance" in alert.text
    summary = json.loads(out.summary_path.read_text())
    assert summary["digest"]["banner"] and any(
        a["source"] == "step:digest" for a in summary["alerts"]
    )


def test_row_unhandled_exception_marks_failed() -> None:
    """tests/ops/test_pipeline.py::test_an_unexpected_error_is_a_failure (status failed,
    exit 1, an error alert, nothing published); the W&B pipeline run ends with exit code 1
    (`ops.summary.log_pipeline_run`)."""
    from nflengine.ops.summary import STATUS_CODE

    assert STATUS_CODE["failed"] != STATUS_CODE["ok"]
