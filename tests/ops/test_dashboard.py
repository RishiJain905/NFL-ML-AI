"""The season dashboard (P07): the series it logs, how the current run is marked, that the
report's panels only use series that exist, and that logging fails soft. No network: W&B,
`init_run` and the reports API are replaced by fakes."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from nflengine.digest.report_card import PRED_FILE, week_dir
from nflengine.digest.run import SCORECARD_SCHEMA
from nflengine.ops import dashboard as D
from nflengine.ops import drift
from nflengine.ops.records import append_history

UTC = dt.UTC
CFG = dict(drift.DEFAULTS)


# ---- fakes and helpers --------------------------------------------------------------------------


class FakeRun:
    def __init__(self, fail_on_log: bool = False):
        self.url = "https://wandb.ai/ent/proj/runs/run1"
        self.id, self.entity, self.project = "run1", "ent", "proj"
        self.metrics: list[tuple[str, str | None]] = []
        self.logged: list[dict] = []
        self.summary: dict = {}
        self.finished: int | None | str = "no"
        self.fail_on_log = fail_on_log

    def define_metric(self, name, step_metric=None):
        self.metrics.append((name, step_metric))

    def log(self, data):
        if self.fail_on_log:
            raise RuntimeError("network down")
        self.logged.append(data)

    def finish(self, exit_code=None):
        self.finished = exit_code


class FakeRunRecord:
    def __init__(self, run_id: str, tags: list[str]):
        self.id, self.tags, self.updated = run_id, list(tags), 0

    def update(self):
        self.updated += 1


class FakeApi:
    records: dict[str, FakeRunRecord] = {}
    last_filters: dict | None = None

    def run(self, path):
        return self.records[path.rsplit("/", 1)[-1]]

    def runs(self, path, filters=None, **_):
        FakeApi.last_filters = filters
        want = {x["tags"] for x in filters["$and"] if "tags" in x}
        return [r for r in self.records.values() if want <= set(r.tags)]


def scorecard(weeks: list[int], games: int = 16) -> pl.DataFrame:
    rows = [
        {
            "season": 2026,
            "week": w,
            "games": games,
            "picks_correct": 10,
            "picks_total": 16,
            "brier_model": 0.20 + 0.01 * w,
            "brier_elo": 0.22,
            "brier_market": 0.21,
            "points_mae": 8.0,
            "watchlist_hits": 4,
            "watchlist_total": 10,
            "checks_passed": True,
            "not_graded": 0,
        }
        for w in weeks
    ]
    return pl.DataFrame(rows, schema=SCORECARD_SCHEMA)


def save_preds(root: Path, season: int, weeks: range, n: int = 8) -> pl.DataFrame:
    """Saved predictions for `n` games a week (home team always wins) and their results."""
    games = []
    for w in weeks:
        rows = []
        for i in range(n):
            gid = f"{season}_{w:02d}_{i}"
            kick = dt.datetime(season, 9, 7, 17, tzinfo=UTC) + dt.timedelta(weeks=w - 1)
            rows.append(
                {
                    "season": season,
                    "week": w,
                    "game_id": gid,
                    "is_primary": True,
                    "home_win_prob": 0.7,
                    "elo_prob": 0.6,
                    "kickoff_utc": kick,
                    "created_at": kick - dt.timedelta(days=5),
                }
            )
            games.append({"game_id": gid, "home_score": 27, "away_score": 20, "result": 7})
        d = week_dir(root, season, w)
        d.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows).write_parquet(d / PRED_FILE)
    return pl.DataFrame(games)


def sb_rows(season: int, weeks: range, group: str, model: float, base: float, mode="backtest"):
    return [
        {
            "season": season,
            "week": w,
            "target": "t",
            "position_group": group,
            "n_scored": 40,
            "mae_model": model,
            "mae_baseline": base,
            "mode": mode,
        }
        for w in weeks
    ]


def history(path: Path, rows: list[dict]) -> pl.DataFrame:
    out = pl.DataFrame()
    for i, r in enumerate(rows):
        base = {
            "season": 2026,
            "kind": "main",
            "run_id": f"r{i}",
            "started": f"2026-10-0{i + 1}T10:00:00Z",
            "status": "ok",
        }
        out = append_history(path, {**base, **r})
    return out


def full_data(tmp_path: Path) -> D.DashboardData:
    """A run for week 9 with every kind of series."""
    games = save_preds(tmp_path, 2026, range(1, 9))
    sb = pl.DataFrame(
        sb_rows(2026, range(1, 4), "QB", 9, 10)
        + sb_rows(2026, range(4, 9), "QB", 9, 10, "live")
        + sb_rows(2026, range(1, 9), "RB", 11, 10)
        + sb_rows(2026, range(1, 9), "WR/TE", 9, 10)
        + sb_rows(2026, range(1, 9), "EDGE/DL", 9, 10)
        + sb_rows(2026, range(1, 9), "LB/S", 9, 10)
    )
    hist = history(
        tmp_path / "h.parquet",
        [
            {
                "week": w,
                "on_time": True,
                "hours_before_deadline": 20.0,
                "total_seconds": 600.0,
                "checks_passed": True,
                "regenerated": False,
                "drift_alerts": 0,
            }
            for w in range(4, 9)
        ],
    )
    return D.build_dashboard_data(
        2026,
        9,
        run_root=tmp_path,
        games=games,
        scorecard=scorecard(list(range(1, 9))),
        scoreboard=sb,
        history=hist,
        cfg=CFG,
    )


def keys_of(data: D.DashboardData) -> set[str]:
    k = {key for p in data.points for key in p} | {key for p in data.pipeline for key in p}
    k |= {"calcurve/predicted", "calcurve/observed", "calcurve/perfect"}
    return k | set(data.summary)


# ---- the series ---------------------------------------------------------------------------------


def test_only_weeks_before_the_run_week_are_plotted(tmp_path):
    games = save_preds(tmp_path, 2026, range(1, 9))
    data = D.build_dashboard_data(
        2026,
        5,
        run_root=tmp_path,
        games=games,
        scorecard=scorecard(list(range(1, 9))),
        cfg=CFG,
        scoreboard=pl.DataFrame(),
    )
    assert [p["dash/week"] for p in data.points] == [1, 2, 3, 4]
    assert data.summary["through_week"] == 4


def test_scorecard_curves_are_renamed_and_cumulative_is_game_weighted(tmp_path):
    sc = pl.concat([scorecard([1], games=10), scorecard([2], games=30)]).sort("week")
    data = D.build_dashboard_data(
        2026,
        3,
        run_root=tmp_path,
        scorecard=sc,
        games=pl.DataFrame(),
        scoreboard=pl.DataFrame(),
        cfg=CFG,
    )
    w1, w2 = data.points
    assert w1["game/brier_model"] == pytest.approx(0.21)
    assert w2["game/brier_model"] == pytest.approx(0.22)
    assert w2["game/cum_brier_model"] == pytest.approx((10 * 0.21 + 30 * 0.22) / 40)
    assert w2["game/brier_elo"] == 0.22 and w2["game/cum_brier_market"] == pytest.approx(0.21)
    assert w2["game/cum_pick_accuracy"] == pytest.approx(10 / 16)
    assert w2["watch/hit_rate"] == pytest.approx(0.4)
    assert w2["watch/cum_hit_rate"] == pytest.approx(0.4)
    assert not any(k.startswith("season/") for p in data.points for k in p)
    assert data.summary["cum_brier_model"] == pytest.approx(w2["game/cum_brier_model"])


def test_rolling_gap_only_with_a_full_window(tmp_path):
    games = save_preds(tmp_path, 2026, range(1, 9))
    data = D.build_dashboard_data(
        2026,
        9,
        run_root=tmp_path,
        games=games,
        scorecard=pl.DataFrame(),
        cfg=CFG,
        scoreboard=pl.DataFrame(),
    )
    gap_weeks = [p["dash/week"] for p in data.points if "game/rolling_gap_vs_elo" in p]
    assert gap_weeks == [4, 5, 6, 7, 8]  # a 4-week window needs four graded weeks
    # every home team wins: model 0.7 is better than Elo's 0.6, so the gap is negative
    assert data.points[3]["game/rolling_gap_vs_elo"] == pytest.approx(0.09 - 0.16)


def test_calibration_series_curve_and_ece(tmp_path):
    games = save_preds(tmp_path, 2026, range(1, 4))
    data = D.build_dashboard_data(
        2026,
        4,
        run_root=tmp_path,
        games=games,
        scorecard=pl.DataFrame(),
        cfg=CFG,
        scoreboard=pl.DataFrame(),
    )
    last = data.points[-1]
    # 24 games at 0.7, all won: ECE = |0.7 - 1.0| = 0.3
    assert last["cal/ece"] == pytest.approx(0.3) and last["cal/games"] == 24
    assert 0 < last["cal/ece_chance"] < 0.3
    assert data.curve.height == 1
    row = data.curve.row(0, named=True)
    assert row["mean_prob"] == pytest.approx(0.7) and row["mean_outcome"] == 1.0
    assert data.summary["season_ece"] == pytest.approx(0.3)
    assert data.summary["games_graded"] == 24


def test_player_series_per_group_weekly_cumulative_rolling_and_live(tmp_path):
    data = full_data(tmp_path)
    by_week = {p["dash/week"]: p for p in data.points}
    # QB: model 9 vs baseline 10 -> +10% every week, weeks 1-3 backtest, 4-8 live
    assert by_week[2]["player/improvement_qb"] == pytest.approx(10.0)
    assert by_week[5]["player/cum_improvement_qb"] == pytest.approx(10.0)
    assert by_week[4]["player/rolling_improvement_qb"] == pytest.approx(10.0)
    assert "player/rolling_improvement_qb" not in by_week[3]  # no full window yet
    assert by_week[2]["player/live"] == 0.0 and by_week[6]["player/live"] == 1.0
    # RB is behind the baseline: model 11 vs 10 -> -10%
    assert by_week[8]["player/improvement_rb"] == pytest.approx(-10.0)
    for slug in ("qb", "rb", "wrte", "edge", "lbs"):
        assert f"player/cum_improvement_{slug}" in by_week[8]
    assert "player/improvement_all" in by_week[8]
    assert data.summary["player_live_weeks"] == 5 and data.summary["player_weeks"] == 8


def test_pipeline_points_cumulative_rates_and_counts(tmp_path):
    h = history(
        tmp_path / "h.parquet",
        [
            {"week": 4, "on_time": True, "checks_passed": True, "total_seconds": 600.0},
            {
                "week": 5,
                "on_time": False,
                "checks_passed": False,
                "regenerated": True,
                "hours_before_deadline": -3.0,
                "degraded_steps": "graph,player",
                "drift_alerts": 2,
            },
            {
                "week": 5,
                "on_time": True,
                "checks_passed": True,
                "hours_before_deadline": 12.0,
                "started": "2026-10-09T10:00:00Z",
                "regenerated": False,
            },  # the re-run replaces it
            {"week": 6, "status": "not_ready"},  # did nothing: not a point
            {"week": 7, "kind": "injury-update", "on_time": False},  # another kind
            {"week": 9, "on_time": None, "checks_passed": None, "status": "failed"},
        ],
    )
    pts = D.pipeline_points(h, 2026, 8, "main")
    assert [p["pipe/week"] for p in pts] == [4, 5]  # week 9 is after the run week
    assert pts[0]["pipe/cum_on_time_rate"] == 1.0 and pts[0]["pipe/run_minutes"] == 10.0
    assert pts[1]["pipe/on_time"] == 1.0 and pts[1]["pipe/cum_on_time_rate"] == 1.0
    assert pts[1]["pipe/hours_before_deadline"] == 12.0 and pts[1]["pipe/degraded_steps"] == 0
    # a run that published nothing leaves on_time / checks and their rates empty
    pts = D.pipeline_points(h, 2026, 9, "main")
    assert [p["pipe/week"] for p in pts] == [4, 5, 9]
    assert "pipe/on_time" not in pts[2] and "pipe/cum_on_time_rate" not in pts[2]
    assert pts[2]["pipe/degraded_steps"] == 0
    assert D.pipeline_points(h, 2026, 8, "injury-update")[0]["pipe/on_time"] == 0.0
    assert D.pipeline_points(None, 2026, 8, "main") == []
    assert D.pipeline_points(pl.DataFrame(), 2026, 8, "main") == []


def test_degraded_runs_count_their_steps(tmp_path):
    h = history(
        tmp_path / "h.parquet",
        [{"week": 5, "status": "degraded", "degraded_steps": "graph,player", "drift_alerts": 2}],
    )
    pt = D.pipeline_points(h, 2026, 5, "main")[0]
    assert pt["pipe/degraded_steps"] == 2 and pt["pipe/drift_alerts"] == 2


def test_missing_inputs_leave_series_out_instead_of_failing(tmp_path):
    data = D.build_dashboard_data(
        2026,
        5,
        run_root=tmp_path,
        scoreboard=pl.DataFrame(),
        games=pl.DataFrame({"x": [1]}),
        cfg=CFG,
    )
    assert data.points == [] and data.pipeline == [] and data.curve.is_empty()
    assert data.summary["through_week"] == 4


def test_nan_values_are_dropped():
    rows: dict = {}
    D._put(rows, 3, **{"a": float("nan"), "b": 1.5, "c": None})
    assert rows == {3: {"dash/week": 3, "b": 1.5}}


# ---- report and series agree --------------------------------------------------------------------


def test_every_report_metric_is_a_series_the_run_logs(tmp_path):
    data = full_data(tmp_path)
    logged = keys_of(data)
    missing = []
    for sec in D.report_sections(CFG):
        for plot in sec["plots"]:
            for k in [plot["x"], *plot["y"]]:
                if k not in logged:
                    missing.append((sec["title"], k))
    assert not missing
    assert all(key in logged for _, key in D.HEADLINES)


def test_logged_series_use_the_step_metrics_the_report_plots_against():
    run = FakeRun()
    data = D.DashboardData(2026, 6, points=[{"dash/week": 4, "game/brier_model": 0.2}])
    D._log_data(run, data)
    assert ("dash/week", None) in run.metrics
    assert ("game/*", "dash/week") in run.metrics and ("player/*", "dash/week") in run.metrics
    assert ("pipe/*", "pipe/week") in run.metrics
    assert ("calcurve/*", "calcurve/predicted") in run.metrics
    assert run.logged == [{"dash/week": 4, "game/brier_model": 0.2}]


def test_calibration_curve_is_logged_as_points_and_a_table(tmp_path):
    games = save_preds(tmp_path, 2026, range(1, 4))
    data = D.build_dashboard_data(
        2026,
        4,
        run_root=tmp_path,
        games=games,
        scorecard=pl.DataFrame(),
        cfg=CFG,
        scoreboard=pl.DataFrame(),
    )
    run = FakeRun()
    D._log_data(run, data)
    curve = [x for x in run.logged if "calcurve/predicted" in x]
    assert curve == [
        {
            "calcurve/predicted": pytest.approx(0.7),
            "calcurve/observed": 1.0,
            "calcurve/perfect": pytest.approx(0.7),
        }
    ]
    assert "calibration_curve" in run.logged[-1] and "calibration_curve_chart" in run.logged[-1]
    assert run.summary["season_ece"] == pytest.approx(0.3)


# ---- marking the current run --------------------------------------------------------------------


def test_mark_current_tags_the_new_run_and_untags_the_older_ones(monkeypatch):
    FakeApi.records = {
        "old1": FakeRunRecord("old1", ["season:2026", D.CURRENT_TAG, "dashboard"]),
        "new": FakeRunRecord("new", ["season:2026", "dashboard"]),
    }
    monkeypatch.setattr("wandb.Api", FakeApi)
    n = D.mark_current("ent", "proj", "new", 2026)
    assert n == 1
    assert D.CURRENT_TAG in FakeApi.records["new"].tags
    assert D.CURRENT_TAG not in FakeApi.records["old1"].tags
    assert {"group": D.GROUP} in FakeApi.last_filters["$and"]
    assert {"tags": "season:2026"} in FakeApi.last_filters["$and"]


def test_mark_current_leaves_other_seasons_alone(monkeypatch):
    FakeApi.records = {
        "s25": FakeRunRecord("s25", ["season:2025", D.CURRENT_TAG]),
        "new": FakeRunRecord("new", ["season:2026"]),
    }
    monkeypatch.setattr("wandb.Api", FakeApi)
    assert D.mark_current("ent", "proj", "new", 2026) == 0
    assert D.CURRENT_TAG in FakeApi.records["s25"].tags


# ---- log_season_dashboard -----------------------------------------------------------------------


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """Replace data root, `init_run` and the W&B API; returns (paths, runs, init_calls)."""
    paths = SimpleNamespace(runs=tmp_path / "runs", wandb=tmp_path / "wandb")
    (paths.runs / "2026").mkdir(parents=True)
    runs: list[FakeRun] = []
    calls: list[dict] = []

    def init_run(**kw):
        calls.append(kw)
        run = FakeRun()
        runs.append(run)
        return run

    monkeypatch.setattr(D, "ensure_data_root", lambda *a, **k: paths)
    monkeypatch.setattr("nflengine.tracking.init_run", init_run)
    monkeypatch.setattr("nflengine.tracking.git_commit", lambda: "abc123")
    monkeypatch.setattr("nflengine.tracking.dataset_version", lambda p=None: {"pbp_snapshot": "x"})
    FakeApi.records = {"run1": FakeRunRecord("run1", ["season:2026"])}
    monkeypatch.setattr("wandb.Api", FakeApi)
    scorecard([1, 2]).write_parquet(paths.runs / "2026" / "season_scorecard.parquet")
    pl.DataFrame(sb_rows(2026, range(1, 3), "QB", 9, 10)).write_parquet(
        paths.runs / "2026" / "accuracy_scoreboard.parquet"
    )
    return paths, runs, calls


def test_log_season_dashboard_live_run(wired):
    paths, runs, calls = wired
    lines: list[str] = []
    url = D.log_season_dashboard(2026, 4, launched_by="agent", log=lines.append)
    assert url == "https://wandb.ai/ent/proj/runs/run1"
    kw = calls[0]
    assert kw["group"] == "season-dashboard" and kw["job_type"] == "dashboard"
    assert kw["name"] == "season-2026-w04" and kw["launched_by"] == "agent"
    assert {"dashboard", "season:2026", "week:04", "p07"} <= set(kw["tags"])
    assert "smoke" not in kw["tags"]
    assert kw["config"]["through_week"] == 3 and kw["config"]["git_commit"] == "abc123"
    run = runs[0]
    assert run.finished is None  # finished normally
    assert any("game/brier_model" in x for x in run.logged)
    assert D.CURRENT_TAG in FakeApi.records["run1"].tags
    info = json.loads((paths.runs / "2026" / "dashboard.json").read_text())
    assert info["last_run_url"] == url and info["last_run_week"] == 4
    assert "now the report's run" in lines[-1]


def test_log_season_dashboard_simulation_goes_to_the_smoke_group(wired, tmp_path):
    paths, runs, calls = wired
    sim_root = tmp_path / "sim"
    (sim_root / "2026").mkdir(parents=True)
    scorecard([1, 2]).write_parquet(sim_root / "2026" / "season_scorecard.parquet")
    url = D.log_season_dashboard(2026, 4, run_root=sim_root, log=lambda s: None)
    assert url
    kw = calls[0]
    assert kw["group"] == "season-dashboard-smoke" and "smoke" in kw["tags"]
    assert kw["name"] == "smoke-season-2026-w04"
    assert not (paths.runs / "2026" / "dashboard.json").exists()  # the live file is untouched
    assert {"group": "season-dashboard-smoke"} in FakeApi.last_filters["$and"]


def test_log_season_dashboard_fails_soft_when_wandb_is_down(wired, monkeypatch):
    lines: list[str] = []

    def boom(**kw):
        raise ConnectionError("https://user:SECRET123@api.wandb.ai/graphql?key=abc failed")

    monkeypatch.setattr("nflengine.tracking.init_run", boom)
    assert D.log_season_dashboard(2026, 4, log=lines.append) is None
    assert len(lines) == 1 and "skipped" in lines[0] and "ConnectionError" in lines[0]
    assert "SECRET123" not in lines[0] and "key=abc" not in lines[0]  # type only (Sol review)


def test_log_season_dashboard_finishes_a_half_logged_run_as_failed(wired, monkeypatch):
    paths, runs, calls = wired
    broken = FakeRun(fail_on_log=True)
    monkeypatch.setattr("nflengine.tracking.init_run", lambda **kw: broken)
    lines: list[str] = []
    assert D.log_season_dashboard(2026, 4, log=lines.append) is None
    assert broken.finished == 1 and "skipped" in lines[-1]


def test_log_season_dashboard_still_returns_the_url_when_tagging_fails(wired, monkeypatch):
    class BadApi:
        def __init__(self):
            raise RuntimeError("api down: token=SECRET456")

    monkeypatch.setattr("wandb.Api", BadApi)
    lines: list[str] = []
    url = D.log_season_dashboard(2026, 4, log=lines.append)
    assert url and "could not mark it current" in lines[-1]
    assert "SECRET456" not in lines[-1] and "RuntimeError" in lines[-1]


def test_log_season_dashboard_nothing_to_plot_is_still_a_run(wired):
    paths, runs, calls = wired
    (paths.runs / "2026" / "season_scorecard.parquet").unlink()
    (paths.runs / "2026" / "accuracy_scoreboard.parquet").unlink()
    # no backtest scoreboard on disk either: the run is created with just its config
    url = D.log_season_dashboard(2026, 4, log=lambda s: None)
    assert url and runs[0].logged == []
    assert runs[0].summary["through_week"] == 3


# ---- the report ---------------------------------------------------------------------------------


@pytest.fixture()
def report_env(monkeypatch, tmp_path):
    import wandb_workspaces.reports.v2 as wr

    paths = SimpleNamespace(runs=tmp_path / "runs")
    saved: list = []

    def save(self, *a, **k):
        self.id = self.id or "rep1"
        saved.append(self)

    monkeypatch.setattr(wr.Report, "save", save)
    monkeypatch.setattr("nflengine.tracking._wandb_env", lambda: ("proj", "ent", paths))
    monkeypatch.setattr(D, "_report_url", lambda r: f"https://wandb.ai/ent/proj/reports/{r.id}")
    return wr, paths, saved


def test_build_report_creates_filtered_panels_and_remembers_the_url(report_env):
    wr, paths, saved = report_env
    lines: list[str] = []
    url = D.build_report(2026, log=lines.append, cfg=CFG)
    assert url == "https://wandb.ai/ent/proj/reports/rep1"
    rep = saved[0]
    assert rep.title == "2026 Season Dashboard"
    grids = [b for b in rep.blocks if isinstance(b, wr.PanelGrid)]
    assert len(grids) == 1 + len(D.report_sections(CFG))  # the headline tiles + each section
    want = (
        "Group = 'season-dashboard' and Tags in ['dashboard-current'] and Tags in ['season:2026']"
    )
    for g in grids:
        assert g.runsets[0].filters == want
        assert g.runsets[0].order[0].ascending is False
    plots = [p for g in grids for p in g.panels if isinstance(p, wr.LinePlot)]
    assert plots and all(p.max_runs_to_show == 1 for p in plots)
    headings = [b.text for b in rep.blocks if isinstance(b, wr.H2)]
    assert headings == [
        "Season at a glance",
        "Game model",
        "Calibration",
        "Watch list",
        "Player model",
        "Pipeline health",
    ]
    info = json.loads((paths.runs / "2026" / "dashboard.json").read_text())
    assert info["url"] == url and info["report_id"] == "rep1" and info["season"] == 2026


def test_build_report_updates_in_place_when_the_url_is_known(report_env):
    wr, paths, saved = report_env
    D.write_dashboard_info(
        D.dashboard_file(paths, 2026), url="https://wandb.ai/ent/proj/reports/old"
    )
    existing = SimpleNamespace(
        title="old", description="", blocks=[], id="old", save=lambda: saved.append("saved")
    )
    seen: list[str] = []

    def from_url(url):
        seen.append(url)
        return existing

    wr.Report.from_url = staticmethod(from_url)
    url = D.build_report(2026, log=lambda s: None, cfg=CFG)
    assert seen == ["https://wandb.ai/ent/proj/reports/old"]
    assert saved == ["saved"]  # the existing report was saved, no new one created
    assert existing.title == "2026 Season Dashboard" and len(existing.blocks) > 5
    assert url.endswith("/old")


def test_build_report_creates_a_new_one_when_the_old_is_gone(report_env):
    wr, paths, saved = report_env
    D.write_dashboard_info(D.dashboard_file(paths, 2026), url="https://wandb.ai/x/y/reports/gone")

    def from_url(url):
        raise RuntimeError("not found")

    wr.Report.from_url = staticmethod(from_url)
    lines: list[str] = []
    url = D.build_report(2026, log=lines.append, cfg=CFG)
    assert url.endswith("/rep1") and any("creating" in x for x in lines)


def test_smoke_report_uses_the_smoke_group_and_leaves_the_live_file_alone(report_env):
    wr, paths, saved = report_env
    D.write_dashboard_info(
        D.dashboard_file(paths, 2025), url="https://wandb.ai/ent/proj/reports/live"
    )
    D.build_report(2025, smoke=True, log=lambda s: None, cfg=CFG)
    rep = saved[0]
    assert rep.title == "SMOKE 2025 Season Dashboard"
    grid = next(b for b in rep.blocks if isinstance(b, wr.PanelGrid))
    assert grid.runsets[0].filters.startswith("Group = 'season-dashboard-smoke'")
    info = D.read_dashboard_info(D.dashboard_file(paths, 2025))
    assert info["url"].endswith("/live")  # not overwritten by the smoke report


def test_report_text_uses_the_configured_thresholds():
    text = " ".join(s["text"] for s in D.report_sections({**CFG, "ece_max": 0.07}))
    assert "0.07" in text and "20%" in text and "4 weeks" in text
