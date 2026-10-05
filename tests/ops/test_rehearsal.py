"""Rehearsals (P10): the pinned clock, the data-root redirect, `run_rehearsal` with fake steps
(nothing live written, W&B off, the clock and env restored, the folder seeded and chained),
the wipe guard and the CLI week ranges."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import polars as pl
import pytest

import nflengine.ops.rehearsal as R
import nflengine.paths as P
import nflengine.weekly as W
from nflengine import clock
from nflengine.cli import _week_range
from nflengine.paths import DataPaths

KICKOFF = dt.datetime(2026, 1, 10, 21, 30, tzinfo=dt.UTC)  # Saturday of a wild-card weekend
TUESDAY = dt.datetime(2026, 1, 6, 14, 0, tzinfo=dt.UTC)  # 10:00 ET the Tuesday before


# ---- the clock and the redirect -----------------------------------------------------------------


def test_clock_is_real_unless_pinned() -> None:
    assert not clock.is_pinned()
    before = dt.datetime.now(dt.UTC).replace(microsecond=0)
    assert clock.utc_now() >= before
    with clock.pinned(dt.datetime(2026, 1, 6, 14, 0)) as at:  # naive = UTC
        assert clock.is_pinned()
        assert at == TUESDAY and clock.utc_now() == TUESDAY
        with clock.pinned(KICKOFF):
            assert clock.utc_now() == KICKOFF
        assert clock.utc_now() == TUESDAY  # nested pins restore the outer one
    assert not clock.is_pinned()


def test_redirect_data_root_returns_the_redirect_and_restores(tmp_path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    paths = R.RehearsalPaths(tmp_path / "scratch", live=live)
    with P.redirect_data_root(paths) as got:
        assert got is paths
        assert P.ensure_data_root() is paths
        assert P.ensure_data_root(live).root == live  # an explicit root is never redirected
        assert paths.features.is_dir() and paths.runs.is_dir()
    assert P._redirect is None


def test_rehearsal_paths_read_live_and_write_scratch(tmp_path) -> None:
    p = R.RehearsalPaths(tmp_path / "s", live=tmp_path / "live")
    for name in ("raw", "curated", "research", "bdb", "cache", "neo4j_data"):
        assert str(getattr(p, name)).startswith(str(tmp_path / "live")), name
    assert p.cache_nflreadpy == tmp_path / "live" / "cache" / "nflreadpy"
    for name in ("features", "models", "runs", "reports", "wandb"):
        assert getattr(p, name).parent == tmp_path / "s", name
    assert p.run_dir(2025, 19) == tmp_path / "s" / "runs" / "2025" / "week19"
    assert p.report_path(2025, 19).parent == tmp_path / "s" / "reports" / "2025"


# ---- run_rehearsal -----------------------------------------------------------------------------


@pytest.fixture
def live(tmp_path: Path, monkeypatch) -> DataPaths:
    """A tiny live data root: one wild-card game, feature tables, canonical backtests."""
    p = DataPaths(tmp_path / "live")
    for d in (p.curated, p.features, p.runs / "backtests" / "player" / "x", p.reports):
        d.mkdir(parents=True)
    pl.DataFrame(
        {"season": [2025], "week": [19], "game_id": ["2025_19_X_Y"], "kickoff_utc": [KICKOFF]}
    ).write_parquet(p.curated / "games.parquet")
    pl.DataFrame({"a": [1]}).write_parquet(p.features / "team_ratings.parquet")
    pl.DataFrame({"a": [1]}).write_parquet(p.runs / "backtests" / "player" / "x" / "p.parquet")
    pl.DataFrame({"a": [1]}).write_parquet(p.runs / "published_insights.parquet")
    monkeypatch.setattr(R, "ensure_data_root", lambda *a, **k: p)
    return p


def _fakes(seen: list[dict], fail: dict[str, Exception] | None = None) -> dict:
    fail = fail or {}

    def make(name):
        def step(o: W.WeeklyOptions, log) -> str:
            paths = P.ensure_data_root()
            seen.append(
                {
                    "step": name,
                    "now": clock.utc_now(),
                    "paths": type(paths).__name__,
                    "wandb": os.environ.get("WANDB_MODE"),
                    "graph": o.graph,
                    "use_wandb": o.use_wandb,
                    "promote": o.promote,
                    "llm": o.llm,
                }
            )
            if name in fail:
                raise fail[name]
            if name == "digest":
                out = paths.report_path(o.season, o.week)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text("# digest\n", encoding="utf-8")
            return f"{name} ok"

        return step

    return {n: make(n) for n in R.STEPS}


def test_rehearsal_runs_the_steps_in_scratch_with_the_clock_pinned(live, tmp_path) -> None:
    seen: list[dict] = []
    wandb_before = os.environ.get("WANDB_MODE")
    res = R.run_rehearsal(2025, 19, funcs=_fakes(seen), log=lambda *_: None)
    root = live.root / "rehearsals" / "2025"
    assert res.status == "ok" and res.root == root and res.error is None
    assert [s["step"] for s in seen] == list(R.STEPS)
    assert {s["now"] for s in seen} == {TUESDAY}  # the Tuesday before the first kickoff
    assert {s["paths"] for s in seen} == {"RehearsalPaths"}
    assert {s["wandb"] for s in seen} == {"disabled"}
    assert not any(s["graph"] or s["use_wandb"] or s["promote"] for s in seen)
    assert {s["llm"] for s in seen} == {"placeholder"}
    # outputs only in the rehearsal folder; the live run / report folders untouched
    assert res.report == root / "reports" / "2025" / "week19-digest.md"
    assert not (live.runs / "2025").exists() and not any(live.reports.iterdir())
    record = json.loads((root / "runs" / "2025" / "week19" / R.RESULT_FILE).read_text())
    assert record["status"] == "ok" and record["steps"]["game"]["detail"] == "game ok"
    state = json.loads((root / "runs" / "2025" / "week19" / "weekly_run.json").read_text())
    assert set(state["steps"]) == set(R.STEPS)
    # seeded once: features, the canonical backtests, the novelty record; a marker
    assert (root / "features" / "team_ratings.parquet").exists()
    assert (root / "runs" / "backtests" / "player" / "x" / "p.parquet").exists()
    assert (root / "runs" / "published_insights.parquet").exists()
    assert (root / R.MARKER).exists()
    # everything restored afterwards
    assert not clock.is_pinned() and P._redirect is None
    assert os.environ.get("WANDB_MODE") == wandb_before


def test_rehearsal_steps_subset_keeps_pipeline_order(live) -> None:
    seen: list[dict] = []
    res = R.run_rehearsal(
        2025, 19, steps=["digest", "game"], funcs=_fakes(seen), log=lambda *_: None
    )
    assert [s["step"] for s in seen] == ["game", "digest"] and res.status == "ok"
    with pytest.raises(ValueError, match="can't be rehearsed"):
        R.run_rehearsal(2025, 19, steps=["ratings"], funcs=_fakes([]), log=lambda *_: None)


def test_rehearsal_failure_and_degraded(live) -> None:
    seen: list[dict] = []
    res = R.run_rehearsal(
        2025,
        19,
        funcs=_fakes(seen, {"player": W.StepFailed("player", "boom")}),
        log=lambda *_: None,
    )
    assert res.status == "failed" and res.error == "player: boom" and res.report is None
    assert [s["step"] for s in seen] == ["ready", "game", "player"]
    assert res.steps["player"]["status"] == "failed"
    assert not clock.is_pinned() and P._redirect is None

    res = R.run_rehearsal(
        2025,
        19,
        funcs=_fakes([], {"player": W.StepDegraded("player", "no rows")}),
        log=lambda *_: None,
    )
    assert res.status == "degraded" and res.steps["digest"]["status"] == "ok"


def test_rehearsal_at_overrides_the_tuesday(live) -> None:
    seen: list[dict] = []
    at = dt.datetime(2026, 1, 9, 12, 0, tzinfo=dt.UTC)
    R.run_rehearsal(2025, 19, at=at, funcs=_fakes(seen), log=lambda *_: None)
    assert {s["now"] for s in seen} == {at}


def test_fresh_wipes_only_a_marked_folder(live, tmp_path) -> None:
    R.run_rehearsal(2025, 19, funcs=_fakes([]), log=lambda *_: None)
    root = live.root / "rehearsals" / "2025"
    (root / "leftover.txt").write_text("x")
    R.run_rehearsal(2025, 19, fresh=True, funcs=_fakes([]), log=lambda *_: None)
    assert not (root / "leftover.txt").exists() and (root / R.MARKER).exists()

    other = tmp_path / "not-a-rehearsal"
    other.mkdir()
    (other / "keep.txt").write_text("x")
    with pytest.raises(ValueError, match="isn't a rehearsal folder"):
        R.run_rehearsal(2025, 19, root=other, fresh=True, funcs=_fakes([]), log=lambda *_: None)
    assert (other / "keep.txt").exists()


# ---- the weekly steps honour the rehearsal switches ------------------------------------------


def test_digest_step_passes_graph_off_and_no_wandb(monkeypatch) -> None:
    import nflengine.digest.run as run_mod

    seen: dict = {}

    class Res:
        report_path = Path("x.md")
        url = None

        class synthesis:  # noqa: N801
            class final:  # noqa: N801
                passed = True

    def fake(season, week, **kw):
        seen.update(kw)
        return Res()

    monkeypatch.setattr(run_mod, "run_digest", fake)
    W._digest(W.WeeklyOptions(2025, 19, graph=False, use_wandb=False), log=lambda *_: None)
    assert seen["graph"] == "off" and seen["use_wandb"] is False and seen["mode"] == "live"
    W._digest(W.WeeklyOptions(2026, 5), log=lambda *_: None)
    assert seen["graph"] == "auto" and seen["use_wandb"] is True


def test_week_range() -> None:
    assert _week_range("1") == [1]
    assert _week_range("19-22") == [19, 20, 21, 22]
    assert _week_range("19, 21") == [19, 21]


# ---- Sol review (P10): the rehearsal folder can never overlap live data ------------------


def test_a_folder_overlapping_live_data_is_refused_before_anything_is_written(live, tmp_path):
    for bad in (
        live.root,  # the live root itself
        live.root.parent,  # a folder that contains it
        live.runs,  # a live folder
        live.runs / "digest-backtests",  # inside one
        live.features / "x",
    ):
        with pytest.raises(ValueError):
            R.run_rehearsal(2025, 19, root=bad, funcs=_fakes([]), log=lambda *_: None)
        assert not (bad / R.MARKER).exists()
    for bad in (live.root, live.runs):  # --fresh never wipes them either
        with pytest.raises(ValueError):
            R.run_rehearsal(2025, 19, root=bad, fresh=True, funcs=_fakes([]), log=lambda *_: None)
    assert (live.runs / "backtests" / "player" / "x" / "p.parquet").exists()
    # outside the live root, or under rehearsals/: fine
    ok = R.run_rehearsal(
        2025, 19, root=tmp_path / "elsewhere", funcs=_fakes([]), log=lambda *_: None
    )
    assert ok.status == "ok" and (tmp_path / "elsewhere" / R.MARKER).exists()


def test_a_non_empty_unmarked_folder_is_refused_and_left_alone(live, tmp_path):
    other = tmp_path / "mine"
    other.mkdir()
    (other / "notes.txt").write_text("x")
    with pytest.raises(ValueError, match="isn't a rehearsal folder"):
        R.run_rehearsal(2025, 19, root=other, funcs=_fakes([]), log=lambda *_: None)
    assert not (other / R.MARKER).exists() and (other / "notes.txt").exists()


def test_a_failed_rehearsal_never_reports_an_older_digest(live):
    first = R.run_rehearsal(2025, 19, funcs=_fakes([]), log=lambda *_: None)
    assert first.report is not None and first.report.exists()
    again = R.run_rehearsal(
        2025,
        19,
        funcs=_fakes([], {"player": W.StepFailed("player", "boom")}),
        log=lambda *_: None,
    )
    assert again.status == "failed" and again.report is None
    assert set(again.steps) == {"ready", "game", "player"}  # not the earlier run's digest
    only = R.run_rehearsal(2025, 19, steps=["game"], funcs=_fakes([]), log=lambda *_: None)
    assert only.report is None and set(only.steps) == {"game"}
