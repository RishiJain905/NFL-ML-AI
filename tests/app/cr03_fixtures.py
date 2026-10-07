"""CR03 fixtures: a week's full run records (week 5's shape), the season files, the backtest
files, and a fake W&B API. Imported directly (no conftest in subfolders)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from nflengine.paths import DataPaths

RUN_URL = "https://wandb.ai/ent/proj/runs/{}"
# must come back masked; a word after the value keeps the JSON scan exact (as CR01's fixture)
LEAKY = "api_key=SENTINEL-records-123 here"


def _drift(week: int) -> list[dict[str, Any]]:
    rows = [
        ("game_vs_elo", None, "insufficient_data", None, 0.0),
        ("calibration", None, "insufficient_data", None, 0.05),
        ("player_vs_baseline", "QB", "insufficient_data", None, 0.0),
        ("player_vs_baseline", "RB", "ok", 4.2, 0.0),
        ("data_freshness", None, "ok", 0.0, 7.0),
        ("checks", None, "insufficient_data", None, 0.2),
    ]
    return [
        {
            "name": n,
            "status": s,
            "value": v,
            "threshold": t,
            "detail": f"{n}: detail for week {week}",
            "response": "No action needed.",
            "group": g,
            "weeks": [week - 1],
        }
        for n, g, s, v, t in rows
    ]


def write_records(
    paths: DataPaths,
    season: int = 2026,
    week: int = 4,
    *,
    alerts: list[dict[str, Any]] | None = None,
    command: str = "nfl weekly run --auto --expect-week 4",
) -> None:
    """Add week 5-style records to a week written by `write_full_week`: a full run summary,
    the ingest manifest its ingest step names (`ingest-1.json`), the quality file of the same
    curate run, an events file, and the season files (history, scorecard, dashboard)."""
    run_dir = paths.run_dir(season, week)
    summary = {
        "season": season,
        "week": week,
        "kind": "main",
        "status": "ok",
        "on_time": True,
        "published": True,
        "launched_by": "rishi",
        "finished": "2026-10-04T05:02:43",
        "error": None,
        "steps": {
            k: {"status": "ok", "seconds": s, "this_run": True}
            for k, s in zip(
                ("ingest", "ready", "curate", "ratings", "game", "graph", "player", "digest"),
                (23.0, 0.0, 15.0, 3.0, 17.0, 267.0, 131.0, 533.0),
                strict=True,
            )
        },
        "degraded_steps": [],
        "failed_step": None,
        "freshness": [
            {
                "source": "nflverse",
                "dataset": "pbp",
                "snapshot_date": "2026-10-04",
                "age_days": 0,
                "newest_week": 3,
                "expected_week": 3,
                "status": "ok",
                "stale": False,
                "note": f"last ingest: ok; {LEAKY}",
            },
            {
                "source": "espn",
                "dataset": "news",
                "snapshot_date": "2026-09-20",
                "age_days": 14,
                "newest_week": None,
                "expected_week": None,
                "status": "ok",
                "stale": True,
                "note": "older than 7 days",
            },
        ],
        "stale_sources": ["espn/news"],
        "ingest": {
            "snapshot_date": "2026-10-04",
            "finished": "2026-10-04T03:18:42",
            "datasets": 3,
            "failed": ["espn/news"],
            "rows": {},
        },
        "quality": {
            "run_at": "2026-10-04T03:18:58",
            "checks": 3,
            "failed": [],
            "blocking_failed": [],
        },
        "models": {
            "game": [f"game-model-v0:{season}-w{week:02d}"],
            "player": [f"player-model-v1:{season}-w{week:02d}"],
            "team": [f"team-model-v1:{season}-w{week:02d}"],
        },
        "digest": {"checks_passed": True},
        "drift": _drift(week),
        "alerts": alerts or [],
    }
    (run_dir / "run_summary.json").write_text(json.dumps(summary))
    runs = paths.raw / "_runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "ingest-1.json").write_text(
        json.dumps(
            {
                "season": season,
                "snapshot_date": "2026-10-04",
                "finished": "2026-10-04T03:18:42",
                "results": [
                    {
                        "source": "nflverse",
                        "dataset": "pbp",
                        "status": "ok",
                        "rows": 8497,
                        "detail": "",
                    },
                    {
                        "source": "nflverse",
                        "dataset": "schedules",
                        "status": "ok",
                        "rows": 7548,
                        "detail": "",
                    },
                    {
                        "source": "espn",
                        "dataset": "news",
                        "status": "failed",
                        "rows": None,
                        "detail": LEAKY,
                    },
                ],
            }
        )
    )
    q = paths.curated / "_quality"
    q.mkdir(parents=True, exist_ok=True)
    (q / "latest.json").write_text(
        json.dumps(
            {
                "run_at": "2026-10-04T03:18:58",
                "results": [
                    {
                        "name": "games unique",
                        "level": "block",
                        "passed": True,
                        "detail": "0 duplicates",
                    },
                    {
                        "name": "plays unique",
                        "level": "block",
                        "passed": True,
                        "detail": "0 duplicates",
                    },
                    {"name": "weather coverage", "level": "warn", "passed": True, "detail": "97%"},
                ],
            }
        )
    )
    ev = run_dir / "events"
    ev.mkdir(exist_ok=True)
    (ev / "20261004T031815Z-ab12.jsonl").write_text(
        json.dumps(
            {
                "seq": 1,
                "t": "2026-10-04T03:18:17-04:00",
                "run_id": "20261004T031815Z-ab12",
                "type": "run_start",
                "kind": "weekly",
                "command": command,
                "season": season,
                "week": week,
                "launched_by": "rishi",
                "via": "control-room",
            }
        )
        + "\n"
    )
    pl.DataFrame(
        {
            "season": [season],
            "week": [week],
            "kind": ["main"],
            "run_id": ["20261004T071817Z"],
            "started": ["2026-10-04T07:18:17Z"],
            "finished": ["2026-10-04T09:02:43Z"],
            "status": ["ok"],
            "total_seconds": [989.0],
            "deadline": ["2026-10-09T00:15:00Z"],
            "on_time": [True],
            "hours_before_deadline": [47.5],
            "failed_step": [None],
            "degraded_steps": [None],
            "checks_passed": [True],
            "regenerated": [False],
            "banner": [False],
            "drift_alerts": [0],
            "launched_by": ["rishi"],
        },
        schema_overrides={"failed_step": pl.String, "degraded_steps": pl.String},
    ).write_parquet(paths.runs / str(season) / "pipeline_history.parquet")
    (paths.runs / str(season) / "dashboard.json").write_text(
        json.dumps(
            {
                "last_run_id": "dash0001",
                "last_run_week": week,
                "season": season,
                "title": f"{season} Season Dashboard",
                "url": "https://wandb.ai/ent/proj/reports/Dash--abc",
            }
        )
    )


def write_season_scorecard(paths: DataPaths, season: int, weeks: list[int]) -> None:
    pl.DataFrame(
        {
            "season": [season] * len(weeks),
            "week": weeks,
            "games": [15] * len(weeks),
            "picks_correct": [9] * len(weeks),
            "picks_total": [15] * len(weeks),
            "brier_model": [0.2246] * len(weeks),
            "brier_elo": [0.2114] * len(weeks),
            "brier_market": [0.2172] * len(weeks),
            "logloss_model": [0.63] * len(weeks),
            "ece_model": [0.15] * len(weeks),
            "points_mae": [5.6] * len(weeks),
            "watchlist_hits": [5] * len(weeks),
            "watchlist_total": [6] * len(weeks),
            "player_mae_vs_baseline": [5.6] * len(weeks),
            "checks_passed": [True] * len(weeks),
            "not_graded": [1] * len(weeks),
        }
    ).write_parquet(paths.runs / str(season) / "season_scorecard.parquet")


TEAMS = ("IND", "WAS", "KC", "BUF")


def write_ratings(
    paths: DataPaths, season: int = 2026, weeks: tuple[int, ...] = (1, 2, 3, 4)
) -> None:
    elo = {
        "IND": [1500, 1510, 1530, 1560],
        "WAS": [1520, 1515, 1500, 1480],
        "KC": [1600, 1605, 1610, 1612],
        "BUF": [1580, 1570, 1575, 1574],
    }
    rows = [
        {"season": season, "week": w, "team": t, "elo": float(elo[t][i]), "elo_games": i}
        for t in TEAMS
        for i, w in enumerate(weeks)
    ]
    pl.DataFrame(rows).write_parquet(paths.features / "team_elo.parquet")
    pl.DataFrame(
        [
            {
                "season": season,
                "week": w,
                "team": t,
                "net_epa": 0.1 * (i + 1),
                "off_epa": 0.05,
                "def_epa": -0.05,
                "net_pass_epa": 0.12,
                "net_rush_epa": 0.02,
            }
            for i, t in enumerate(TEAMS)
            for w in weeks
        ]
    ).write_parquet(paths.features / "team_ratings.parquet")


def write_backtests(paths: DataPaths, season: int = 2025) -> None:
    game = paths.runs / "backtests" / "game"
    for variant, brier in (("model_only", 0.2199), ("market", 0.2102)):
        (game / variant).mkdir(parents=True, exist_ok=True)
        (game / variant / "summary.json").write_text(
            json.dumps(
                {
                    "games": 2227,
                    "brier_model": brier,
                    "brier_elo": 0.2221,
                    "brier_market": 0.2104,
                    "accuracy_model": 0.6625,
                }
            )
        )
    probs = [0.7, 0.3, 0.6, 0.55, 0.8, 0.2, 0.65, 0.45]
    outcomes = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0]
    pl.DataFrame(
        {
            "season": [season] * 8,
            "week": [1, 1, 1, 1, 2, 2, 2, 2],
            "game_id": [f"g{i}" for i in range(8)],
            "home_win": outcomes,
            "home_win_prob": probs,
            "elo_prob": [0.6] * 8,
            "market_prob": [0.62, 0.35, None, 0.5, 0.75, 0.25, 0.6, 0.5],
            "market_ml_prob": [0.61] * 8,
        }
    ).write_parquet(game / "market" / "predictions_games.parquet")
    player = paths.runs / "backtests" / "player"
    for key, payload in (
        ("rec_yds-wrte", {"improvement_pct": 9.2}),
        ("pd-cbs", {"brier_improvement_pct": 8.8}),
    ):
        (player / key).mkdir(parents=True, exist_ok=True)
        (player / key / "summary.json").write_text(json.dumps(payload))
    pl.DataFrame(
        {
            "season": [season] * 4,
            "week": [1, 1, 2, 2],
            "target": ["rec_yds", "pass_yds", "rec_yds", "pass_yds"],
            "position_group": ["WR/TE", "QB", "WR/TE", "QB"],
            "n_scored": [100, 30, 100, 30],
            "mae_model": [20.0, 60.0, 21.0, 58.0],
            "mae_baseline": [22.0, 62.0, 22.0, 60.0],
            "improvement_pct": [9.1, 3.2, 4.5, 3.3],
            "mode": ["backtest"] * 4,
        }
    ).write_parquet(player / "scoreboard.parquet")
    ex = paths.runs / "digest-backtests" / "2025" / "week10"
    ex.mkdir(parents=True, exist_ok=True)
    (ex / "run_summary.json").write_text(
        json.dumps(
            {
                "alerts": [
                    {
                        "level": "warn",
                        "title": "2025 week 10: game probabilities poorly calibrated (ECE 0.053)",
                        "text": "season ECE 0.053 over 135 graded games (threshold 0.05).",
                        "source": "drift:calibration",
                    }
                ]
            }
        )
    )


# ---- a fake W&B API ------------------------------------------------------------------------


@dataclass
class FakeArtifact:
    name: str  # "game-model:v5"
    type: str
    version: str
    aliases: list[str]
    size: int = 1000
    created_at: str = "2026-10-07T00:28:36Z"
    logger: str | None = None
    calls: list[str] = field(default_factory=list)

    def logged_by(self) -> Any:
        self.calls.append(self.version)
        return FakeRunRef(self.logger) if self.logger else None


@dataclass
class FakeRunRef:
    id: str


@dataclass
class FakeRun:
    id: str
    name: str
    group: str
    job_type: str
    tags: list[str]
    created_at: str
    state: str = "finished"
    summary: dict[str, Any] = field(default_factory=dict)
    used: list[FakeArtifact] = field(default_factory=list)

    @property
    def url(self) -> str:
        return RUN_URL.format(self.id)

    def used_artifacts(self) -> list[FakeArtifact]:
        return self.used


class FakeApi:
    """Answers the calls `app/wandb_api.py` makes; `fail` makes every call raise."""

    def __init__(self, runs: list[FakeRun], artifacts: dict[str, list[FakeArtifact]]) -> None:
        self._runs = runs
        self._artifacts = artifacts
        self.fail: Exception | None = None
        self.fail_on: set[str] = set()  # methods that raise (`run` = a run's lineage)
        self.calls = 0

    def _check(self, method: str = "") -> None:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        if method in self.fail_on:
            raise TimeoutError("read timed out")

    def runs(self, project: str, filters: dict | None = None, order: str = "", per_page: int = 50):
        self._check()
        conds = (filters or {}).get("$and", [])
        out = []
        for r in self._runs:
            ok = True
            for c in conds:
                if "tags" in c and c["tags"] not in r.tags:
                    ok = False
                if "group" in c and c["group"] != r.group:
                    ok = False
                if "display_name" in c and c["display_name"] != r.name:
                    ok = False
            if ok:
                out.append(r)
        return sorted(out, key=lambda r: r.created_at, reverse=True)

    def run(self, path: str) -> FakeRun:
        self._check("run")
        rid = path.rsplit("/", 1)[-1]
        return next(r for r in self._runs if r.id == rid)

    def artifacts(self, typ: str, name: str):
        self._check()
        short = name.rsplit("/", 1)[-1]
        if short not in self._artifacts:
            raise ValueError(f"can't find artifact collection with name {short}")
        return list(self._artifacts[short])


def fake_wandb_world(season: int = 2026, week: int = 4) -> FakeApi:
    tag = f"week:{week:02d}"
    prev = f"week:{week - 1:02d}"
    st = f"season:{season}"
    game = [
        FakeArtifact(f"game-model:v{i}", "model", f"v{i}", al, logger=f"gm{i:06d}")
        for i, al in ((0, []), (1, [f"{season}-w{week:02d}", "production", "latest"]))
    ]
    graph = [
        FakeArtifact(
            "graph-results:v0",
            "graph",
            "v0",
            [f"{season}-w{week:02d}", "latest"],
            logger="gr123456",
        )
    ]
    digest = [
        FakeArtifact(
            "digest:v0", "digest", "v0", [f"{season}-w{week:02d}", "latest"], logger="dg123456"
        )
    ]
    player = [
        FakeArtifact(
            "player-model:v0",
            "model",
            "v0",
            [f"{season}-w{week:02d}", "production", "latest"],
            logger="pl123456",
        )
    ]
    runs = [
        FakeRun(
            "gm000000",
            f"train-{season}-w{week:02d}",
            "track1-game",
            "train",
            [st, tag],
            "2026-10-04T07:00:00Z",
        ),
        FakeRun(
            "gm000001",
            f"train-{season}-w{week:02d}",
            "track1-game",
            "train",
            [st, tag],
            "2026-10-04T07:19:14Z",
        ),
        FakeRun(
            "gr123456",
            f"graph-{season}-w{week:02d}",
            "track1-graph",
            "build",
            [st, tag, "live"],
            "2026-10-04T07:19:18Z",
        ),
        FakeRun(
            "sb000003",
            f"scoreboard-{season}-w{week - 1:02d}",
            "track1-player",
            "eval",
            [st, prev, "scoreboard"],
            "2026-10-04T07:22:00Z",
        ),
        FakeRun(
            "sb000004",
            f"scoreboard-{season}-w{week:02d}",
            "track1-player",
            "eval",
            [st, tag, "scoreboard"],
            "2026-10-11T07:22:00Z",
        ),
        FakeRun(
            "dg123456",
            f"digest-{season}-w{week:02d}",
            "weekly-pipeline",
            "main",
            [st, tag, "prod", f"note {LEAKY}"],
            "2026-10-04T09:02:11Z",
        ),
        FakeRun(
            "pp123456",
            f"pipeline-{season}-w{week:02d}",
            "weekly-pipeline",
            "pipeline",
            [st, tag],
            "2026-10-04T09:02:20Z",
            used=[game[1], graph[0], player[0], digest[0]],
        ),
        FakeRun(
            "re000001",
            "ratings-eval",
            "track1-ratings",
            "eval",
            [],
            "2026-10-03T00:00:00Z",
            summary={
                "objective_mse": 0.1041,
                "mse_base_last_season": 0.1194,
                "elo_brier": 0.2214,
                "games": 2895,
                "note": "x",
            },
        ),
    ]
    return FakeApi(
        runs, {"game-model": game, "graph-results": graph, "digest": digest, "player-model": player}
    )
