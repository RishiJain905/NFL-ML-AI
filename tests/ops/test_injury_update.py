"""Saturday injury update (P07): gating, materiality, kickoff exclusion, reasons, files, W&B,
and the `output="update"` mode of both `run_train` functions. Synthetic data under
`tmp_path`; ingest, curation, the refits and W&B are mocked."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from nflengine.ops import injury_update as IU
from nflengine.paths import DataPaths

SEASON, WEEK = 2026, 6
NOW = dt.datetime(2026, 10, 17, 14, 0, tzinfo=dt.UTC)  # Saturday 10:00 ET
SUN = dt.datetime(2026, 10, 18, 17, 0, tzinfo=dt.UTC)
THU = dt.datetime(2026, 10, 16, 0, 15, tzinfo=dt.UTC)  # already played


def game(gid, home, away, prob, *, ko=SUN, hqb="Home QB", aqb="Away QB", spread=3.0, hp=24.0):
    return {
        "game_id": gid,
        "kickoff_utc": ko,
        "home_team": home,
        "away_team": away,
        "variant": "market",
        "is_primary": True,
        "home_win_prob": prob,
        "pred_home_points": hp,
        "pred_away_points": 20.0,
        "spread_line": spread,
        "home_qb_name": hqb,
        "away_qb_name": aqb,
    }


def games_frame(rows: list[dict]) -> pl.DataFrame:
    # a non-primary row with a wild number: must never be compared
    extra = {**rows[0], "variant": "model_only", "is_primary": False, "home_win_prob": 0.01}
    return pl.DataFrame([*rows, extra]).with_columns(
        pl.col("kickoff_utc").cast(pl.Datetime("us", "UTC"))
    )


MAIN_GAMES = [
    game("g_chi", "CHI", "NYJ", 0.59, hqb="Case Keenum", aqb="Geno Smith"),
    game("g_buf", "BUF", "NE", 0.70),
    game("g_thu", "CLE", "PIT", 0.40, ko=THU),
]

WATCH = [
    {
        "player_id": "w1",
        "player": "Jake Hansen",
        "team": "BUF",
        "position": "LB",
        "game_id": "g_buf",
        "kickoff_utc": SUN,
        "target": "tackles",
        "target_label": "tackles",
        "kind": "count",
        "unit": "count",
        "p50": 5.0,
        "mean": 5.29,
        "injury_status": "Questionable",
    },
    {
        "player_id": "w2",
        "player": "Braelon Allen",
        "team": "NYJ",
        "position": "RB",
        "game_id": "g_chi",
        "kickoff_utc": SUN,
        "target": "rush_yds",
        "target_label": "rushing yards",
        "kind": "amount",
        "unit": "yards",
        "p50": 36.9,
        "mean": 36.9,
        "injury_status": None,
    },
    {
        "player_id": "w3",
        "player": "Thursday Guy",
        "team": "PIT",
        "position": "WR",
        "game_id": "g_thu",
        "kickoff_utc": THU,
        "target": "rec_yds",
        "target_label": "receiving yards",
        "kind": "amount",
        "unit": "yards",
        "p50": 50.0,
        "mean": 50.0,
        "injury_status": None,
    },
]

UPDATE_PLAYERS = [
    {"player_id": "w1", "game_id": "g_buf", "target": "tackles", "kind": "count",
     "p50": 5.0, "mean": 5.3},
    {"player_id": "w2", "game_id": "g_chi", "target": "rush_yds", "kind": "amount",
     "p50": 41.2, "mean": 41.2},
]  # fmt: skip


def injuries(rows: list[tuple[str, str, str, str, str | None]]) -> pl.DataFrame:
    """(gsis_id, name, team, position, report_status) for the week."""
    return pl.DataFrame(
        [
            {"season": SEASON, "week": WEEK, "gsis_id": g, "full_name": n, "team": t,
             "position": p, "report_status": s}
            for g, n, t, p, s in rows
        ],
        schema={"season": pl.Int32, "week": pl.Int32, "gsis_id": pl.String,
                "full_name": pl.String, "team": pl.String, "position": pl.String,
                "report_status": pl.String},
    )  # fmt: skip


BASE_INJURIES = [("w1", "Jake Hansen", "BUF", "LB", "Questionable")]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def world(tmp_path, monkeypatch):
    paths = DataPaths(tmp_path)
    for d in paths.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    run_dir = paths.run_dir(SEASON, WEEK)
    run_dir.mkdir(parents=True)
    games_frame(MAIN_GAMES).write_parquet(run_dir / "predictions_games.parquet")
    pl.DataFrame(WATCH).with_columns(
        pl.col("kickoff_utc").cast(pl.Datetime("us", "UTC"))
    ).write_parquet(run_dir / "watchlist.parquet")
    injuries(BASE_INJURIES).write_parquet(paths.curated / "injuries.parquet")
    pl.DataFrame(
        {"season": [SEASON], "week": [WEEK], "gsis_id": ["x"], "full_name": ["X"],
         "team": ["BUF"], "position": ["WR"], "status": ["ACT"]},
        schema_overrides={"season": pl.Int32, "week": pl.Int32},
    ).write_parquet(paths.curated / "rosters_weekly.parquet")  # fmt: skip
    pl.DataFrame(
        {
            "season": [SEASON] * 4,
            "week": [3, 4, 5, 5],
            "gsis_id": ["dj", "dj", "dj", "rb2"],
            "offense_pct": [0.9, 0.95, 0.92, 0.2],
            "defense_pct": [None, None, None, None],
        },
        schema_overrides={"season": pl.Int32, "week": pl.Int32, "defense_pct": pl.Float64},
    ).write_parquet(paths.curated / "snaps.parquet")

    cfg = SimpleNamespace(
        flags={"injury_update_enabled": True},
        injury_update={"min_prob_move": 0.05, "watch_status_change": True},
    )
    monkeypatch.setattr(IU, "ensure_data_root", lambda: paths)
    monkeypatch.setattr(IU, "get_config", lambda: cfg)
    monkeypatch.setattr(IU, "git_commit", lambda: "test")

    from nflengine.models import game_runs, player_runs

    state = {"games": MAIN_GAMES, "players": UPDATE_PLAYERS, "calls": []}

    def fake_games(season, week, launched_by=None, log=print, output="main", **kw):
        assert output == "update"
        state["calls"].append("games")
        out = run_dir / "predictions_games_update.parquet"
        games_frame(state["games"]).write_parquet(out)
        return {"predictions": out}

    def fake_players(season, week, launched_by=None, log=print, output="main", **kw):
        assert output == "update" and kw.get("use_wandb") is False
        state["calls"].append("players")
        out = run_dir / "predictions_players_update.parquet"
        pl.DataFrame(
            state["players"],
            schema={"player_id": pl.String, "game_id": pl.String, "target": pl.String,
                    "kind": pl.String, "p50": pl.Float64, "mean": pl.Float64},
        ).write_parquet(out)  # fmt: skip
        return {"predictions": out}

    monkeypatch.setattr(game_runs, "run_train", fake_games)
    monkeypatch.setattr(player_runs, "run_train", fake_players)
    return SimpleNamespace(paths=paths, run_dir=run_dir, cfg=cfg, state=state)


def run(**kw):
    kw = {"ingest": False, "use_wandb": False, "now": NOW, "log": lambda *_: None, **kw}
    return IU.run_injury_update(SEASON, WEEK, **kw)


def report(w) -> Path:
    return w.paths.report_path(SEASON, WEEK, kind="injury-update")


# ---- gating -----------------------------------------------------------------------------------


def test_refuses_when_disabled(world) -> None:
    world.cfg.flags["injury_update_enabled"] = False
    with pytest.raises(IU.InjuryUpdateError, match="disabled"):
        run()


def test_refuses_without_the_main_run(world) -> None:
    (world.run_dir / "watchlist.parquet").unlink()
    with pytest.raises(IU.InjuryUpdateError, match="watchlist.parquet"):
        run()
    assert world.state["calls"] == []


# ---- materiality ------------------------------------------------------------------------------


def test_nothing_changed_writes_json_but_no_report(world) -> None:
    res = run()
    assert not res.material and res.report_path is None
    assert not report(world).exists() and not (world.run_dir / "injury_update.md").exists()
    saved = json.loads(res.summary_path.read_text())
    assert saved["material"] is False
    assert saved["diff"]["summary"]["games_compared"] == 2
    assert world.state["calls"] == ["games", "players"]


def test_move_just_under_the_threshold_is_not_material(world) -> None:
    world.state["games"] = [{**MAIN_GAMES[0], "home_win_prob": 0.59 - 0.049}, *MAIN_GAMES[1:]]
    res = run()
    assert not res.material and not report(world).exists()
    assert res.diff["summary"]["games_moved"] == 0
    assert res.diff["summary"]["max_prob_move"] == pytest.approx(0.049)


def test_move_at_the_threshold_is_material(world) -> None:
    world.state["games"] = [MAIN_GAMES[0], {**MAIN_GAMES[1], "home_win_prob": 0.65}, MAIN_GAMES[2]]
    res = run()
    assert res.material and res.report_path == report(world)
    text = report(world).read_text(encoding="utf-8")
    assert (world.run_dir / "injury_update.md").read_text(encoding="utf-8") == text
    assert "Patriots at Bills" in text and "70% → 65%" in text
    assert "Tuesday's numbers" in text
    assert len(text.split()) < 200


def test_watch_status_change_alone_is_material(world) -> None:
    injuries([("w1", "Jake Hansen", "BUF", "LB", "Out")]).write_parquet(
        world.paths.curated / "injuries.parquet"
    )
    world.state["players"] = UPDATE_PLAYERS[1:]  # Out players aren't projected
    res = run()
    assert res.material and res.diff["summary"]["games_moved"] == 0
    w1 = next(w for w in res.diff["watch"] if w["player_id"] == "w1")
    assert (w1["status_then"], w1["status_now"], w1["still_projected"]) == (
        "Questionable",
        "Out",
        False,
    )
    text = report(world).read_text(encoding="utf-8")
    assert "Questionable → Out" in text and "5.3 tackles → no longer projected" in text
    assert "No win probability moved 5 points or more." in text


def test_status_rule_can_be_switched_off(world) -> None:
    world.cfg.injury_update["watch_status_change"] = False
    injuries([("w1", "Jake Hansen", "BUF", "LB", "Out")]).write_parquet(
        world.paths.curated / "injuries.parquet"
    )
    assert not run().material


def test_started_games_are_excluded(world) -> None:
    # Thursday's game "moves" 30 points, but it kicked off: no new numbers, not compared
    world.state["games"] = [*MAIN_GAMES[:2], {**MAIN_GAMES[2], "home_win_prob": 0.70}]
    res = run()
    assert not res.material
    assert res.diff["excluded_started"] == ["g_thu"]
    assert res.diff["excluded_names"] == ["Steelers at Browns"]
    assert {g["game_id"] for g in res.diff["games"]} == {"g_chi", "g_buf"}
    assert res.diff["watch_excluded_started"] == 1
    assert "w3" not in {w["player_id"] for w in res.diff["watch"]}


# ---- reasons + the before state ----------------------------------------------------------------


def test_reasons_name_the_qb_change_and_the_most_used_injured_players(world, monkeypatch) -> None:
    """The before state is read before the re-ingest; players whose status worsened rank by
    recent snap share, at most 3 per team."""
    import nflengine.curate.build as build
    import nflengine.curate.quality as quality
    import nflengine.ingest.runner as runner

    pulled: dict = {}

    def fake_ingest(**kw):
        pulled.update(kw)
        return [SimpleNamespace(source="nflverse", dataset="injuries", status="ok")], "m.json"

    newer = [
        *BASE_INJURIES,
        ("dj", "D.J. Moore", "CHI", "WR", "Out"),
        ("rb2", "Backup Back", "CHI", "RB", "Doubtful"),
        ("q1", "Mild Case", "CHI", "TE", "Questionable"),  # not worse enough to be a reason
        ("ol1", "Line Man", "CHI", "T", "Out"),
        ("ol2", "Other Line", "CHI", "G", "Out"),
    ]
    monkeypatch.setattr(runner, "run_ingest", fake_ingest)
    monkeypatch.setattr(
        build,
        "build_all",
        lambda log=print: (
            injuries(newer).write_parquet(world.paths.curated / "injuries.parquet") or ["injuries"]
        ),
    )
    monkeypatch.setattr(quality, "run_quality_checks", lambda: [])
    world.state["games"] = [
        {
            **MAIN_GAMES[0],
            "home_win_prob": 0.48,
            "home_qb_name": "Tyson Bagent",
            "spread_line": 1.0,
        },
        *MAIN_GAMES[1:],
    ]
    res = run(ingest=True)
    assert pulled["nflverse_datasets"] == IU.NFLVERSE_DATASETS
    assert set(pulled["sources"]) == {"nflverse", "espn", "odds_api"}
    chi = next(g for g in res.diff["games"] if g["game_id"] == "g_chi")
    assert chi["moved"] and chi["qb_change"]
    assert chi["reasons"][0] == "Bears QB: Case Keenum -> Tyson Bagent"
    assert chi["reasons"][1] == "line: Bears by 3 -> Bears by 1"
    assert [p["player"] for p in chi["players"]] == ["D.J. Moore", "Backup Back", "Line Man"]
    text = report(world).read_text(encoding="utf-8")
    assert "Case Keenum → Tyson Bagent" in text and "59% → 48%" in text
    assert "20–24 → 20–24" in text  # away–home, as in the digest


def test_failed_nflverse_ingest_stops_the_update(world, monkeypatch) -> None:
    import nflengine.ingest.runner as runner

    monkeypatch.setattr(
        runner,
        "run_ingest",
        lambda **kw: (
            [SimpleNamespace(source="nflverse", dataset="injuries", status="failed")],
            "",
        ),
    )
    with pytest.raises(IU.InjuryUpdateError, match="re-ingest failed"):
        run(ingest=True)
    assert world.state["calls"] == []


def test_ir_move_counts_as_a_status_change(world) -> None:
    pl.DataFrame(
        {"season": [SEASON], "week": [WEEK], "gsis_id": ["w2"], "full_name": ["Braelon Allen"],
         "team": ["NYJ"], "position": ["RB"], "status": ["RES"]},
        schema_overrides={"season": pl.Int32, "week": pl.Int32},
    ).write_parquet(world.paths.curated / "rosters_weekly.parquet")  # fmt: skip
    state = IU.injury_state(world.paths, SEASON, WEEK)
    assert state.filter(pl.col("player_id") == "w2")["status"].to_list() == ["IR"]
    res = run()
    w2 = next(w for w in res.diff["watch"] if w["player_id"] == "w2")
    # on a reserve list now (whatever the before state said): a watch-list pick on IR won't
    # play, so it counts (review: a second update the same day must not lose it)
    assert w2["status_now"] == "IR" and w2["status_material"] and res.material
    w = IU.compare_watch(
        pl.read_parquet(world.run_dir / "watchlist.parquet"),
        None,
        state.filter(pl.col("player_id") != "w2"),  # Tuesday: not on a reserve list
        state,
        NOW,
    )[0]
    w2 = next(x for x in w if x["player_id"] == "w2")
    assert (w2["status_now"], w2["status_changed"], w2["still_projected"]) == ("IR", True, None)


# ---- files and W&B ------------------------------------------------------------------------------


def test_main_files_are_never_touched(world) -> None:
    files = ["predictions_games.parquet", "watchlist.parquet"]
    before = {f: sha(world.run_dir / f) for f in files}
    world.state["games"] = [{**MAIN_GAMES[0], "home_win_prob": 0.30}, *MAIN_GAMES[1:]]
    assert run().material
    assert {f: sha(world.run_dir / f) for f in files} == before


def test_no_players_option_skips_the_player_refit(world) -> None:
    res = run(players=False)
    assert world.state["calls"] == ["games"]
    assert all(w["reprojected"] is False for w in res.diff["watch"])
    assert any("--no-players" in n for n in res.notes)


class FakeRun:
    def __init__(self):
        self.summary: dict = {}
        self.logged: list = []
        self.artifacts: list = []
        self.exit_code = None
        self.url = "https://example.invalid/run"

    def log(self, data):
        self.logged.append(data)

    def log_artifact(self, art, aliases):
        self.artifacts.append((art.name, art.type, list(aliases)))

    def finish(self, exit_code=None):
        self.exit_code = exit_code if exit_code is not None else 0


def test_wandb_run_summary_tables_and_artifact(world, monkeypatch) -> None:
    runs: list[tuple[dict, FakeRun]] = []

    def fake_init(*args, **kw):
        runs.append((kw | {"group": args[0], "job_type": args[1]}, FakeRun()))
        return runs[-1][1]

    monkeypatch.setattr(IU, "init_run", fake_init)
    world.state["games"] = [{**MAIN_GAMES[0], "home_win_prob": 0.50}, *MAIN_GAMES[1:]]
    res = run(use_wandb=True, launched_by="agent")
    kw, r = runs[0]
    assert (kw["group"], kw["job_type"], kw["name"]) == (
        "weekly-pipeline",
        "injury-update",
        "injury-update-2026-w06",
    )
    assert {"p07", "injury-update", "season:2026", "week:06"} <= set(kw["tags"])
    assert r.summary["material"] is True and r.summary["games_moved"] == 1
    assert r.summary["started_games_excluded"] == 1 and "seconds_total" in r.summary
    assert {k for d in r.logged for k in d} == {"injury_update/games", "injury_update/watch"}
    assert r.artifacts == [("injury-update", "digest", ["2026-w06"])]
    assert r.exit_code == 0 and res.url == r.url


def test_wandb_run_marked_failed_without_the_message(world, monkeypatch) -> None:
    from nflengine.models import game_runs

    r = FakeRun()
    monkeypatch.setattr(IU, "init_run", lambda *a, **k: r)

    def boom(*a, **k):
        raise RuntimeError("secret-looking detail")

    monkeypatch.setattr(game_runs, "run_train", boom)
    with pytest.raises(RuntimeError):
        run(use_wandb=True)
    assert r.exit_code == 1 and r.summary == {"error": "RuntimeError"}


# ---- run_train(output="update") ----------------------------------------------------------------


@pytest.fixture
def game_mocked(tmp_path, monkeypatch):
    from test_game_model import MF, TF
    from test_game_runs import FakeRun as GameRun
    from test_game_runs import live_frame

    from nflengine.models import game_runs

    paths = DataPaths(tmp_path)
    for d in paths.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    frame = live_frame()
    frame.select("game_id", "season", "week", "completed", "kickoff_utc").write_parquet(
        paths.curated / "games.parquet"
    )
    runs: list = []
    monkeypatch.setattr(game_runs, "ensure_data_root", lambda: paths)
    monkeypatch.setattr(game_runs, "load_frame", lambda *a, **k: frame)
    monkeypatch.setattr(game_runs, "init_run", lambda *a, **k: runs.append(GameRun()) or runs[-1])
    monkeypatch.setattr(game_runs, "dataset_version", lambda *a: {"pbp_snapshot": None})
    real = game_runs.model_config
    monkeypatch.setattr(
        game_runs,
        "model_config",
        lambda variant="model_only", **kw: real(
            variant,
            margin_features=MF + (("spread_line",) if variant == "market" else ()),
            total_features=TF + (("total_line",) if variant == "market" else ()),
            **kw,
        ),
    )
    return paths, runs


def test_game_run_train_update_writes_only_the_update_file(game_mocked) -> None:
    from test_game_runs import SEASON as S
    from test_game_runs import WEEK as W

    from nflengine.models import game_runs

    paths, runs = game_mocked
    out = game_runs.run_train(S, W, log=lambda *_: None, output="update")
    rd = paths.run_dir(S, W)
    assert out["predictions"] == rd / "predictions_games_update.parquet"
    assert out["models"] is None and out["url"] is None and out["aliases"] == []
    assert not (rd / "predictions_games.parquet").exists()
    assert not (paths.models / "game-model").exists() and runs == []
    # with a main run: its file, models and artifact stay as they were
    game_runs.run_train(S, W, log=lambda *_: None)
    main = rd / "predictions_games.parquet"
    model_dir = paths.models / "game-model" / f"{S}-w{W:02d}"
    snap = {p.name: sha(p) for p in [main, *model_dir.iterdir()]}
    n_runs = len(runs)
    upd = game_runs.run_train(S, W, log=lambda *_: None, output="update")
    assert {p.name: sha(p) for p in [main, *model_dir.iterdir()]} == snap
    assert len(runs) == n_runs
    cols = ["game_id", "variant", "home_win_prob"]
    assert upd["table"].select(cols).equals(pl.read_parquet(main).select(cols))
    with pytest.raises(ValueError, match="never promotes"):
        game_runs.run_train(S, W, promote=True, output="update")
    with pytest.raises(ValueError, match="output"):
        game_runs.run_train(S, W, output="weekly")


def test_player_run_train_update_writes_only_the_update_file(tmp_path, monkeypatch) -> None:
    from test_player_core import _toy_frame

    from nflengine.models import player_model as M
    from nflengine.models import player_runs as R
    from nflengine.models import player_schema
    from nflengine.models.player_schema import conform, get_target

    paths = DataPaths(tmp_path)
    for d in paths.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    season, week = 2022, 8
    future = dt.datetime.now(dt.UTC) + dt.timedelta(days=2)
    games = pl.DataFrame(
        {
            "season": [season] * 8,
            "week": list(range(1, 9)),
            "completed": [True] * 7 + [False],
            "kickoff_utc": [future - dt.timedelta(weeks=8 - w) for w in range(1, 9)],
        },
        schema_overrides={"kickoff_utc": pl.Datetime("us", "UTC")},
    )
    games.write_parquet(paths.curated / "games.parquet")
    toy = _toy_frame()
    target = get_target("tackles")[0]

    def fake_assemble(preds, frame, target, *, created_at, **kw):
        out = preds.select("season", "week", "player_id", "game_id", "p10", "p50", "p90")
        out = out.with_columns(
            pl.lit(future).cast(pl.Datetime("us", "UTC")).alias("kickoff_utc"),
            pl.lit(target.name).alias("target"),
        )
        if isinstance(created_at, dt.datetime):
            out = out.with_columns(
                pl.lit(created_at).cast(pl.Datetime("us", "UTC")).alias("created_at")
            )
        return conform(out)

    def never(*a, **k):
        raise AssertionError("the update must not write this")

    monkeypatch.setattr(R, "ensure_data_root", lambda: paths)
    monkeypatch.setattr(
        R, "build_player_data", lambda *a, **k: SimpleNamespace(inp=SimpleNamespace(games=games))
    )
    monkeypatch.setattr(R, "target_data", lambda data, t: toy)
    monkeypatch.setattr(R, "feature_columns", lambda frame: ["own_l4", "use_snap_l1"])
    monkeypatch.setattr(
        R,
        "model_config",
        lambda t=None, **kw: M.PlayerModelConfig(
            n_estimators=20, min_data_in_leaf=20, num_threads=1
        ),
    )
    monkeypatch.setattr(R, "MIN_TRAIN_ROWS", 100)
    monkeypatch.setattr(R, "assemble", fake_assemble)
    monkeypatch.setattr(R, "init_run", never)
    monkeypatch.setattr(R, "upsert_scoreboard", never)
    monkeypatch.setattr(player_schema, "write_status", never)

    out = R.run_train(season, week, targets=[target], log=lambda *_: None, output="update")
    rd = paths.run_dir(season, week)
    assert out["predictions"] == rd / "predictions_players_update.parquet"
    assert pl.read_parquet(out["predictions"]).height == 60
    assert sorted(p.name for p in rd.iterdir()) == ["predictions_players_update.parquet"]
    assert not (paths.models / "player-model").exists()
    assert not (paths.runs / str(season) / "player_walkforward.parquet").exists()
    assert out["models"] is None and out["url"] is None and out["aliases"] == []
    with pytest.raises(ValueError, match="never promotes"):
        R.run_train(season, week, promote=True, output="update")


@pytest.mark.parametrize(
    ("then", "now", "material"),
    [
        (None, "Questionable", False),  # a Saturday Questionable tag alone isn't material
        ("Questionable", None, False),
        ("Questionable", "Doubtful", True),
        ("Doubtful", "Out", True),
        ("Out", None, True),  # cleared to play
        (None, "IR", True),
        ("Out", "Out", False),
    ],
)
def test_material_status_change_rule(then, now, material) -> None:
    assert IU.material_status_change(then, now) is material


def test_wandb_outage_never_stops_the_update(world, monkeypatch) -> None:
    def down(*a, **k):
        raise ConnectionError("W&B unreachable")

    monkeypatch.setattr(IU, "init_run", down)
    res = run(use_wandb=True)
    assert res.url is None and any("W&B run not opened" in n for n in res.notes)
    assert res.summary_path.exists()
