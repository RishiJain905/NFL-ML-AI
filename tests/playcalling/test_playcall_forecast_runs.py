"""PC02: the files, the backtest and the weekly commands (`playcalling/forecast_runs.py`).

Everything runs on a fake `curated/` + `playcalling/` + `models/` tree under tmp_path (never D:,
never W&B): `load_curves` (which refits LD00's models) is replaced by linear curves, and the
ship rule is forced where a test needs a promotion. The numbers a forecast file holds are
checked against the independent reference in `playcall_forecast_fixtures`.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass

import numpy as np
import playcall_forecast_fixtures as X
import polars as pl
import pytest
from polars.testing import assert_frame_equal

import nflengine.playcalling.build as B
import nflengine.playcalling.forecast as F
import nflengine.playcalling.forecast_runs as fr
from nflengine.paths import DataPaths

SEASON, WEEK = 2024, 6
DEEP = F.get_target("deep_shot_rate")
BLITZ = F.get_target("blitz_rate")
DROPBACK = F.get_target("dropback_rate")
TARGETS = (DEEP, BLITZ, DROPBACK)
KEYS = [t.key for t in TARGETS]
ET = X.ET
# Friday noon after a Thursday-night game of week 6; the other games are on Sunday the 13th
NOW_MID = dt.datetime(2024, 10, 11, 12, 0, tzinfo=ET)
NOW_BEFORE = dt.datetime(2024, 10, 9, 12, 0, tzinfo=ET)
NOW_AFTER = dt.datetime(2024, 10, 20, 12, 0, tzinfo=ET)


def curves_for(seasons) -> dict[int, F.ScriptCurve]:
    return {s: X.linear_curve(s) for s in seasons}


@pytest.fixture(autouse=True)
def stub_curves(monkeypatch):
    """The LD00 refit is slow and not what is under test."""
    monkeypatch.setattr(fr, "load_curves", lambda paths, seasons, **k: curves_for(seasons))


# --- a world on disk ------------------------------------------------------------------------------
@dataclass
class World:
    paths: DataPaths
    league: X.League
    games: list[dict]
    week: int
    tg_live: pl.DataFrame

    def forecast(self, week: int | None = None, **kw):
        return fr.run_forecast(
            SEASON, self.week if week is None else week, paths=self.paths, log=X.quiet,
            **{"use_wandb": False, "now": NOW_MID, **kw},
        )  # fmt: skip

    def grade(self, week: int | None = None, **kw):
        return fr.run_grade(
            SEASON, self.week if week is None else week, paths=self.paths, log=X.quiet,
            **{"use_wandb": False, "now": NOW_AFTER, **kw},
        )  # fmt: skip


def thursday_game(games: list[dict], week: int = WEEK) -> dict:
    return next(g for g in games if g["season"] == SEASON and g["week"] == week)


def make_world(
    root,
    *,
    week: int = WEEK,
    weeks: int = 10,
    seed: int = 5,
    model_keys=KEYS,
    shipped=("blitz_rate",),
    thursday: bool = True,
) -> World:
    lg = X.random_league(
        seed=seed, teams=X.TEAMS8, seasons=(2023, SEASON), weeks=weeks, targets=TARGETS,
        null_lines=0.0,
    )  # fmt: skip
    games = X.clone(lg.games)
    for g in games:
        if g["season"] == SEASON and g["week"] >= week:
            g["result"] = None  # not played yet
    if thursday:
        g = thursday_game(games, week)
        g["kickoff_utc"] = dt.datetime(2024, 10, 10, 20, 15, tzinfo=ET).astimezone(dt.UTC)
    live = (pl.col("season") < SEASON) | (pl.col("week") < week)
    paths = X.write_tree(
        root, games, lg.tg, keep=lambda tg: live,
        build={SEASON: {"as_of_weeks": [1, week], "coverage": {"games_without_ftn": []}}},
    )  # fmt: skip
    X.write_model(paths, X.model_doc(model_keys, shipped=shipped))
    return World(paths, lg, games, week, lg.tg.filter(live))


@pytest.fixture
def world(tmp_path) -> World:
    return make_world(tmp_path)


def doc_params(t: F.Target, kind: str = "model") -> dict:
    return X.model_doc(KEYS)["targets"][t.key]["kinds"][kind]["params"]


# --- load_model, read_build, load_inputs ----------------------------------------------------------
VERSION_A, VERSION_B = "2019-2022_20261001T000000Z", "2019-2023_20261002T000000Z"


def test_load_model_reads_the_production_pointer(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    with pytest.raises(FileNotFoundError, match="no promoted playcall-model yet"):
        fr.load_model(paths)
    doc = X.model_doc(KEYS, version=VERSION_A)
    X.write_model(paths, doc, pointer=False)
    with pytest.raises(FileNotFoundError, match="no promoted playcall-model yet"):
        fr.load_model(paths)  # a folder is not a promotion
    assert fr.load_model(paths, VERSION_A)["version"] == VERSION_A  # but it can be asked for
    X.write_model(paths, X.model_doc(KEYS, version=VERSION_B))  # promotes B
    assert fr.load_model(paths)["version"] == VERSION_B
    assert fr.load_model(paths, VERSION_A)["version"] == VERSION_A
    # a well-formed version whose folder is gone
    ghost = "2019-2024_20261003T000000Z"
    (paths.models / "playcall" / "production.json").write_text(
        json.dumps({"version": ghost}), encoding="utf-8"
    )
    with pytest.raises(FileNotFoundError, match=f"{ghost} has no params.json"):
        fr.load_model(paths)


@pytest.mark.parametrize(
    "version",
    [
        "../x", "..\\x", "../../outside", "C:/Windows/x", "/etc/passwd", "\\\\server\\share",
        "", "v1", "ghost", "2019-2023", "2019-2023_20261002T000000", "x2019-2023_20261002T000000Z",
        "2019-2023_20261002T000000Z/../x", 20262002, None, ["a"],
        "2019-2023_20261002T000000Z\n",
    ],
)  # fmt: skip
def test_load_model_refuses_a_version_that_could_name_a_path(tmp_path, version) -> None:
    """The pointer (or --version) is checked against the stamp shape before it names a folder:
    nothing outside models/playcall is ever opened (Sol review)."""
    paths = DataPaths(tmp_path)
    X.write_model(paths, X.model_doc(KEYS, version=VERSION_A))
    outside = tmp_path / "x"
    outside.mkdir()
    (outside / "params.json").write_text(json.dumps(X.model_doc(KEYS)), encoding="utf-8")
    # as the pointer
    (paths.models / "playcall" / "production.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="not a playcall-model version"):
        fr.load_model(paths)
    # as the named version
    if isinstance(version, str):
        with pytest.raises(ValueError, match="not a playcall-model version"):
            fr.load_model(paths, version)


def test_a_version_that_looks_right_but_resolves_outside_is_refused(tmp_path, monkeypatch) -> None:
    paths = DataPaths(tmp_path)
    X.write_model(paths, X.model_doc(KEYS, version=VERSION_A))
    real = fr.Path.resolve
    outside = tmp_path / "elsewhere" / "params.json"
    monkeypatch.setattr(
        fr.Path,
        "resolve",
        lambda self, *a, **k: outside if self.name == "params.json" else real(self),
    )  # a symlink-like folder: the stamp is right, the file isn't under models/playcall
    with pytest.raises(ValueError, match="outside models/playcall"):
        fr.load_model(paths, VERSION_A)


def test_the_file_locations(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    assert fr.model_root(paths) == paths.models / "playcall"
    assert fr.backtest_root(paths) == paths.playcalling / "backtests"
    assert fr.week_dir(paths, 2026, 4) == paths.playcalling / "2026" / "week04"
    assert fr.week_dir(paths, 2026, 12).name == "week12"
    assert fr.FORECAST_FILE == "forecast.parquet"
    assert fr.SCOREBOARD_FILE == "playcall_scoreboard.parquet"
    with pytest.raises(FileNotFoundError, match="nfl playcalling build --season 2026"):
        fr.read_build(paths, 2026)


def test_load_inputs_reads_two_seasons_and_the_coach_changes(world) -> None:
    inp = fr.load_inputs(world.paths, [SEASON])
    assert set(inp.tg["season"].unique()) == {2023, SEASON}
    assert inp.games["kickoff_utc"].dtype == pl.Datetime("us", "UTC")
    assert set(inp.changed.columns) == {"season", "team", "side", "changed"}
    assert inp.midseason.columns == ["season", "team", "change_week"]
    assert inp.notes == []
    assert inp.games.height == len(world.games)
    # the season before has no build: a note, not an error
    (world.paths.playcalling / "2023" / "team_game_tendencies.parquet").unlink()
    inp2 = fr.load_inputs(world.paths, [SEASON])
    assert "no play-calling build for 2023" in inp2.notes
    assert set(inp2.tg["season"].unique()) == {SEASON}


def test_load_inputs_merges_the_current_lines_for_a_live_week(tmp_path) -> None:
    w = make_world(tmp_path)
    games = X.clone(w.games)
    target = next(g for g in games if g["season"] == SEASON and g["week"] == WEEK)
    target["spread_line"] = target["total_line"] = None  # the schedule has no line yet
    lines = pl.DataFrame(
        {
            "game_id": [target["game_id"]] * 3,
            "source": ["odds_api", "odds_api", "espn"],
            "home_spread": [2.0, 4.0, 9.0],
            "total": [41.0, 45.0, 60.0],
        }
    )
    X.write_tree(
        tmp_path, games, w.league.tg, lines=lines,
        keep=lambda tg: (pl.col("season") < SEASON) | (pl.col("week") < WEEK),
    )  # fmt: skip
    paths = DataPaths(tmp_path)
    plain = fr.load_inputs(paths, [SEASON])
    live = fr.load_inputs(paths, [SEASON], lines=True)
    row = lambda f: f.filter(pl.col("game_id") == target["game_id"]).row(0, named=True)  # noqa: E731
    assert row(plain.games)["spread_line"] is None
    assert row(live.games)["spread_line"] == 3.0 and row(live.games)["total_line"] == 43.0
    assert row(live.games)["line_source"] == "odds_api"  # the median across the book's rows
    other = next(g for g in games if g["week"] == WEEK - 1 and g["season"] == SEASON)
    assert row_of(live.games, other["game_id"])["line_source"] == "nflverse_schedules"


def row_of(frame: pl.DataFrame, gid: str) -> dict:
    return frame.filter(pl.col("game_id") == gid).row(0, named=True)


def test_live_season_of_reads_the_promoted_ld00_bundle(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    assert fr.live_season_of(paths, 2026) is None  # no LD00 bundle
    live = paths.models / "live-decisions"
    (live / "v1").mkdir(parents=True)
    (live / "production.json").write_text(json.dumps({"version": "v1"}), encoding="utf-8")
    (live / "v1" / "meta.json").write_text(json.dumps({"seasons": [2010, 2025]}), encoding="utf-8")
    assert fr.live_season_of(paths, 2026) == 2026  # fit without 2026: its curve is the bundle's
    assert fr.live_season_of(paths, 2025) is None  # the bundle saw 2025: refit instead
    assert fr.live_season_of(paths, 2019) is None
    (live / "v1" / "meta.json").write_text(json.dumps({"seasons": []}), encoding="utf-8")
    assert fr.live_season_of(paths, 2026) is None


# --- run_forecast ---------------------------------------------------------------------------------
def test_run_forecast_writes_the_weeks_forecast(world) -> None:
    out = world.forecast()
    folder = world.paths.playcalling / str(SEASON) / "week06"
    assert out["folder"] == folder and out["url"] is None
    fc = pl.read_parquet(folder / "forecast.parquet")
    assert_frame_equal(fc, out["frame"])
    assert fc.columns == fr.FORECAST_COLUMNS
    assert fc.height == 4 * 2 * len(KEYS)  # 4 games x 2 teams x 3 targets
    assert fc.group_by("target").len()["len"].to_list() == [8, 8, 8]
    assert fc["version"].unique().to_list() == [X.VERSION]
    # only the model's shipped target is flagged
    assert set(fc.filter(pl.col("shipped"))["target"]) == {"blitz_rate"}
    assert set(fc.filter(~pl.col("shipped"))["target"]) == {"deep_shot_rate", "dropback_rate"}
    # the side is the target's: a blitz is the defense's call
    assert set(fc.filter(pl.col("target") == "blitz_rate")["side"]) == {"defense"}
    assert set(fc.filter(pl.col("target") == "deep_shot_rate")["side"]) == {"offense"}
    assert fc["context"].unique().to_list() == ["opponent"]
    assert fc["context_team"].to_list() == fc["opponent"].to_list()
    assert fc.select("season", "week").unique().rows() == [(SEASON, WEEK)]
    assert fc.sort("game_id", "side", "team", "target").equals(fc)  # written sorted
    # a fully usable forecast: ranges bracket it, inside the target's bounds
    for t in TARGETS:
        x = fc.filter(pl.col("target") == t.key)
        assert x["forecast"].is_finite().all() and (x["lo"] <= x["forecast"]).all()
        assert (
            (x["forecast"] <= x["hi"]).all() and (x["lo"] >= t.lo).all() and (x["hi"] <= t.hi).all()
        )
    # the meta file
    meta = json.loads((folder / "forecast.json").read_text(encoding="utf-8"))
    assert meta["season"] == SEASON and meta["week"] == WEEK and meta["as_of_week"] == WEEK
    assert meta["games"] == 4 and meta["rows"] == fc.height and meta["version"] == X.VERSION
    assert meta["shipped"] == ["blitz_rate"] and meta["schema_version"] == F.SCHEMA_VERSION
    assert meta["lines"] == {"with_line": 4, "sources": ["nflverse_schedules"]}
    assert meta["curve"]["season"] == SEASON and meta["notes"] == []
    assert meta["made_at"] == NOW_MID.astimezone(dt.UTC).isoformat()
    # the as-of cutoff: the end of the Tuesday (ET) before the week
    assert meta["cutoff_utc"].startswith("2024-10-09T03:59:59")
    assert not list(folder.glob(".*.tmp"))  # atomic writes leave nothing behind


def test_the_forecast_matches_the_reference_row_by_row(world) -> None:
    fc = world.forecast()["frame"]
    games = world.games
    doc = X.model_doc(KEYS)
    assert world.league.changed.is_empty()
    for t in TARGETS:
        kp = {k: doc["targets"][t.key]["kinds"][k]["params"] for k in ("b3", "b4", "model")}
        assert kp["b3"]["kappa"] != kp["model"]["kappa"]  # each predictor has its own parameters
        obs = X.ref_obs(t, world.tg_live, games, world.league.curves)
        refs = {
            "model": X.Ref(obs, t, kp["model"]),
            "b3": X.Ref(obs, t, kp["b3"]),
            "b4": X.Ref(obs, t, kp["b4"]),
            "b1": X.Ref(obs, t, kp["b3"]),
            "b2": X.Ref(obs, t, kp["b3"]),
        }
        c = world.league.curves[SEASON]
        x = fc.filter(pl.col("target") == t.key)
        assert x.height == 8
        for r in x.iter_rows(named=True):
            spread = np.array([np.nan if r["spread"] is None else r["spread"]])
            tot = np.array([np.nan if r["total"] is None else r["total"]])
            x1 = float(c.x_spread(t, spread)[0]) if t.script else 0.0
            x2 = float(c.x_total(tot, 10.0)[0]) if t.script else 0.0
            xb = float(c.x_baseline(spread)[0]) if t.key == "dropback_rate" else 0.0
            args = (SEASON, WEEK, r["team"], r["context_team"], x1, x2, xb)
            for kind, col in (
                ("model", "forecast"), ("b1", "b1"), ("b2", "b2"), ("b3", "b3"), ("b4", "b4")
            ):  # fmt: skip
                want = refs[kind].forecast(kind, *args)
                assert r[col] == pytest.approx(want, abs=1e-9), (t.key, r["team"], kind)
            # the pieces add up
            assert r["forecast"] == pytest.approx(
                min(max(r["team_term"] + r["opponent_term"] + r["script_term"], t.lo), t.hi)
            )
            # the plays behind it, by hand: this team's earlier games of the target
            mine = [o for o in obs if o.season == SEASON and o.team == r["team"] and o.week < WEEK]
            assert r["n_so_far"] == sum(o.n for o in mine)
            assert r["games_so_far"] == len(mine) == 5


def test_the_range_is_the_documented_formula(world) -> None:
    fc = world.forecast()["frame"]
    rng = X.model_doc(KEYS)["targets"]["deep_shot_rate"]["range"]
    for r in fc.filter(pl.col("target") == "deep_shot_rate").iter_rows(named=True):
        f = r["forecast"]
        sd = math.sqrt(
            rng["c"] * f * (1 - f) * (1 / r["expected_plays"] + 1 / r["strength"]) + rng["b"]
        )
        assert r["lo"] == pytest.approx(max(f + rng["q_lo"] * sd, 0.0), abs=1e-12)
        assert r["hi"] == pytest.approx(min(f + rng["q_hi"] * sd, 1.0), abs=1e-12)
        assert r["strength"] == pytest.approx(
            150 + r["n_so_far"]
        )  # kappa + plays so far (no coach change)


def test_after_kickoff_marks_games_that_had_started(world) -> None:
    thu = thursday_game(world.games)
    mid = world.forecast(now=NOW_MID)
    fc = mid["frame"]
    assert set(fc.filter(pl.col("after_kickoff"))["game_id"]) == {thu["game_id"]}
    assert fc.filter(pl.col("after_kickoff")).height == 2 * len(KEYS)
    assert mid["meta"]["games_after_kickoff"] == [thu["game_id"]] and mid["meta"]["backfill"]
    before = world.forecast(now=NOW_BEFORE)
    assert not before["frame"]["after_kickoff"].any()
    assert before["meta"]["games_after_kickoff"] == [] and before["meta"]["backfill"] is False
    after = world.forecast(now=NOW_AFTER)
    assert after["frame"]["after_kickoff"].all() and len(after["meta"]["games_after_kickoff"]) == 4
    # the forecast itself doesn't depend on when it was run
    cols = [c for c in fr.FORECAST_COLUMNS if c not in ("made_at", "after_kickoff")]
    assert_frame_equal(before["frame"].select(cols), after["frame"].select(cols))
    # a kickoff exactly at `now` counts as started
    exact = world.forecast(now=thu["kickoff_utc"])
    assert set(exact["frame"].filter(pl.col("after_kickoff"))["game_id"]) == {thu["game_id"]}


def test_a_naive_clock_is_not_accepted_silently_and_microseconds_are_dropped(world) -> None:
    out = world.forecast(now=NOW_MID.replace(microsecond=999_999))
    assert out["meta"]["made_at"] == NOW_MID.astimezone(dt.UTC).isoformat()


def test_a_backfill_equals_the_live_forecast(tmp_path) -> None:
    """Forecasting week 4 after the season is in (every later game in the tables) gives the
    same numbers as forecasting it with only weeks 1-3 on disk."""
    live = make_world(tmp_path / "live", week=4)
    full = make_world(tmp_path / "full", week=4)
    games = X.clone(full.games)
    for g in games:
        g["result"] = 3
    X.write_tree(  # the same league, but every week's results and counts are there
        tmp_path / "full", games, full.league.tg,
        build={SEASON: {"as_of_weeks": [1, 11], "coverage": {"games_without_ftn": []}}},
    )  # fmt: skip
    assert full.tg_live.height < full.league.tg.height
    a = live.forecast(week=4, now=NOW_AFTER)["frame"]
    b = full.forecast(week=4, now=NOW_AFTER)["frame"]
    assert a.height == b.height == 4 * 2 * len(KEYS)
    # the only difference a later result makes is which games read as finished (not the numbers)
    assert_frame_equal(a, b)


def test_run_forecast_refuses_weeks_the_tables_do_not_have(world) -> None:
    with pytest.raises(
        ValueError, match=r"isn't in the play-calling tables \(newest as-of week: 6\)"
    ):
        world.forecast(week=7)
    with pytest.raises(ValueError, match="isn't in the play-calling tables"):
        world.forecast(week=0)
    assert not (world.paths.playcalling / str(SEASON) / "week07").exists()
    # an empty build
    (world.paths.playcalling / str(SEASON) / "build.json").write_text(
        json.dumps({"as_of_weeks": []}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="newest as-of week: none"):
        world.forecast()
    # no build at all
    (world.paths.playcalling / str(SEASON) / "build.json").unlink()
    with pytest.raises(FileNotFoundError, match="nfl playcalling build --season 2024"):
        world.forecast()
    assert not (world.paths.playcalling / str(SEASON) / "week06").exists()


def test_run_forecast_needs_games_and_a_promoted_model(world, tmp_path) -> None:
    (world.paths.playcalling / str(SEASON) / "build.json").write_text(
        json.dumps({"as_of_weeks": [1, 25]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="no games in 2024 week 25"):
        world.forecast(week=25)
    # no production pointer -> the same plain message as load_model
    (world.paths.models / "playcall" / "production.json").unlink()
    with pytest.raises(FileNotFoundError, match="no promoted playcall-model yet"):
        world.forecast()
    # a named version does not need the pointer
    out = world.forecast(version=X.VERSION)
    assert out["meta"]["version"] == X.VERSION


def test_a_model_without_the_targets_of_the_season_is_an_error(tmp_path) -> None:
    w = make_world(tmp_path, model_keys=["blitz_rate"], shipped=())
    assert w.forecast()["frame"]["target"].unique().to_list() == ["blitz_rate"]
    # an FTN target before FTN existed is skipped; with nothing left it is an error
    lg = X.random_league(seed=2, teams=X.TEAMS6, seasons=(2020, 2021), weeks=6, targets=(BLITZ,))
    paths = X.write_tree(
        tmp_path / "old", lg.games, lg.tg,
        build={2021: {"as_of_weeks": [1, 6], "coverage": {}}},
    )  # fmt: skip
    old_fit = range(2016, 2020)
    X.write_model(paths, X.model_doc(["blitz_rate"], fit_seasons=old_fit))
    with pytest.raises(ValueError, match="the production model has no target for this season"):
        fr.run_forecast(2021, 4, use_wandb=False, paths=paths, log=X.quiet, now=NOW_MID)
    X.write_model(paths, X.model_doc(["blitz_rate", "deep_shot_rate"], fit_seasons=old_fit))
    lg2 = X.random_league(seed=2, teams=X.TEAMS6, seasons=(2020, 2021), weeks=6,
                          targets=(BLITZ, DEEP))  # fmt: skip
    X.write_tree(
        tmp_path / "old", lg2.games, lg2.tg, build={2021: {"as_of_weeks": [1, 6], "coverage": {}}}
    )
    out = fr.run_forecast(2021, 4, use_wandb=False, paths=paths, log=X.quiet, now=NOW_MID)
    assert out["frame"]["target"].unique().to_list() == ["deep_shot_rate"]


def test_run_forecast_logs_a_line_and_returns_no_url_without_wandb(world) -> None:
    lines: list[str] = []
    out = fr.run_forecast(
        SEASON, WEEK, use_wandb=False, paths=world.paths, now=NOW_MID, log=lines.append
    )
    assert out["url"] is None
    assert len(lines) == 1 and "2024 week 6: 4 games, 24 forecasts" in lines[0]
    assert "had already kicked off: a backfill" in lines[0]


# --- run_grade ------------------------------------------------------------------------------------
def advance(
    w: World, *, unplayed=(), no_build=(), no_ftn_rows=(), no_deep=(), listed_no_ftn=None
) -> None:
    """The week has been played: its results and counts are in the tables, except as asked."""
    games = X.clone(w.games)
    for g in games:
        if g["season"] == SEASON and g["week"] <= w.week:
            g["result"] = None if g["game_id"] in unplayed else 3
    drop = pl.col("game_id").is_in(list(unplayed) + list(no_build))
    drop = drop | (pl.col("game_id").is_in(list(no_ftn_rows)) & (pl.col("metric") == "blitz_rate"))
    for gid, team in no_deep:
        drop = drop | (
            (pl.col("game_id") == gid) & (pl.col("team") == team)
            & (pl.col("metric") == "deep_shot_rate")
        )  # fmt: skip
    cover = list(no_ftn_rows) if listed_no_ftn is None else list(listed_no_ftn)
    X.write_tree(
        w.paths.root, games, w.league.tg.filter((pl.col("week") <= w.week) & ~drop),
        build={SEASON: {"as_of_weeks": [1, w.week + 1], "coverage": {"games_without_ftn": cover}}},
    )  # fmt: skip
    w.games = games


def week_games(w: World) -> list[str]:
    return [g["game_id"] for g in w.games if g["season"] == SEASON and g["week"] == w.week]


def test_run_grade_statuses(world) -> None:
    world.forecast()
    g1, g2, g3, g4 = week_games(world)
    team_x = next(g for g in world.games if g["game_id"] == g1)["home_team"]
    team_y = next(g for g in world.games if g["game_id"] == g4)["home_team"]
    advance(
        world,
        unplayed=[g2],
        no_build=[g3],
        no_ftn_rows=[g4],
        no_deep=[(g1, team_x), (g4, team_y)],
    )  # g1: all fine but one team has no deep shots; g4: no FTN, and no deep shots for one
    out = world.grade()
    wk = out["frame"]
    assert wk.columns == fr.SCOREBOARD_COLUMNS
    assert (
        wk.height == 24
        and out["path"] == world.paths.playcalling / str(SEASON) / fr.SCOREBOARD_FILE
    )
    status = {(r["game_id"], r["team"], r["target"]): r["status"] for r in wk.iter_rows(named=True)}
    for r in wk.iter_rows(named=True):
        gid, tgt = r["game_id"], r["target"]
        want = "graded"
        if gid == g1 and tgt == "deep_shot_rate" and r["team"] == team_x:
            want = "no_plays"  # played and built, but this team threw nothing deep
        elif gid == g2:
            want = "not_played"
        elif gid == g3:
            want = "waiting_build"
        elif gid == g4 and tgt == "blitz_rate":
            want = "waiting_ftn"  # charting not landed yet; the play-by-play targets are graded
        elif gid == g4 and tgt == "deep_shot_rate" and r["team"] == team_y:
            want = "no_plays"  # play-by-play is in: nothing deep to grade is not an FTN wait
        assert status[(gid, r["team"], tgt)] == want, (gid, r["team"], tgt)
    counts = out["counts"]
    # 2 teams x 3 targets = 6 rows a game: g1 5 graded + 1 no_plays, g2 6 not_played,
    # g3 6 waiting_build, g4 3 graded + 1 no_plays + 2 waiting_ftn (both teams' blitz)
    assert counts == {"graded": 5 + 3, "not_played": 6, "waiting_build": 6, "no_plays": 2,
                      "waiting_ftn": 2}  # fmt: skip
    assert sum(counts.values()) == 24
    # a graded row: the game's actual rate, the errors and the range, by hand
    for t in TARGETS:
        ob = {(o.game_id, o.team): o for o in X.ref_obs(t, world.league.tg, world.games, {})}
        for r in wk.filter((pl.col("target") == t.key) & (pl.col("status") == "graded")).iter_rows(
            named=True
        ):
            o = ob[(r["game_id"], r["team"])]
            assert r["n"] == o.n and r["actual"] == pytest.approx(o.k / o.n)
            assert r["in_range"] == (r["lo"] <= r["actual"] <= r["hi"])
            assert r["err_model"] == pytest.approx(abs(r["forecast"] - r["actual"]))
            for k in ("b1", "b2", "b3", "b4"):
                assert r[f"err_{k}"] == pytest.approx(abs(r[k] - r["actual"]))
    # an ungraded row has no actual and no error, and still carries the forecast
    waiting = wk.filter(pl.col("status") != "graded")
    assert waiting["actual"].null_count() == waiting.height
    assert waiting["in_range"].null_count() == waiting.height
    assert waiting["err_model"].null_count() == waiting.height
    assert waiting["forecast"].null_count() == 0 and (waiting["n"] == 0).all()
    assert wk["graded_at"].unique().to_list() == [NOW_AFTER.astimezone(dt.UTC)]
    # on disk
    sb = pl.read_parquet(out["path"])
    assert_frame_equal(sb, wk)
    assert sb.sort("season", "week", "game_id", "side", "team", "target").equals(sb)


def test_a_waiting_ftn_game_not_on_the_coverage_list_is_no_plays(world) -> None:
    world.forecast()
    g1, g2, g3, g4 = week_games(world)
    advance(world, no_ftn_rows=[g4], listed_no_ftn=[])  # the build did not list it
    wk = world.grade()["frame"]
    blitz = wk.filter((pl.col("game_id") == g4) & (pl.col("target") == "blitz_rate"))
    assert blitz["status"].to_list() == ["no_plays", "no_plays"]
    assert set(
        wk.filter((pl.col("game_id") == g4) & (pl.col("target") != "blitz_rate"))["status"]
    ) == {"graded"}
    # listed, but for a pbp target that is irrelevant: only FTN targets wait for FTN
    advance(world, no_build=[g3], listed_no_ftn=[g3])
    wk2 = world.grade()["frame"]
    assert set(wk2.filter(pl.col("game_id") == g3)["status"]) == {"waiting_build"}


def test_regrading_replaces_the_weeks_rows_and_keeps_the_others(world) -> None:
    w = world
    f5 = w.forecast(week=5, now=NOW_AFTER)["frame"]  # a backfill of week 5
    w.forecast(week=6)
    g1, g2, g3, g4 = week_games(w)
    # week 5 is fully played; week 6 only partly
    advance(w, unplayed=[g2, g3, g4])
    # advance() rewrote week 5's counts too (everything <= 6 that exists)
    first5 = w.grade(week=5, now=NOW_MID)
    assert first5["counts"] == {"graded": f5.height}
    sb5 = pl.read_parquet(first5["path"])
    assert sb5["week"].unique().to_list() == [5]
    first6 = w.grade(week=6, now=NOW_MID)
    sb = pl.read_parquet(first6["path"])
    assert sorted(sb["week"].unique().to_list()) == [5, 6] and sb.height == 24 + f5.height
    assert first6["counts"]["not_played"] == 18 and first6["counts"]["graded"] == 6
    # the other games get played; grade week 6 again, later
    advance(w)
    again = w.grade(week=6, now=NOW_AFTER)
    sb2 = pl.read_parquet(again["path"])
    assert again["counts"] == {"graded": 24} and sb2.height == sb.height  # replaced, not appended
    assert (
        sb2.filter(pl.col("week") == 6).group_by("game_id", "team", "target").len()["len"].max()
        == 1
    )
    # week 5's rows are exactly as they were
    assert_frame_equal(sb2.filter(pl.col("week") == 5), sb.filter(pl.col("week") == 5))
    assert sb2.filter(pl.col("week") == 5)["graded_at"].unique().to_list() == [
        NOW_MID.astimezone(dt.UTC)
    ]
    assert sb2.filter(pl.col("week") == 6)["graded_at"].unique().to_list() == [
        NOW_AFTER.astimezone(dt.UTC)
    ]
    # grading the same state twice changes nothing but the stamp
    third = w.grade(week=6, now=NOW_AFTER)
    assert_frame_equal(pl.read_parquet(third["path"]), sb2)


def test_run_grade_needs_a_forecast_and_a_build(world) -> None:
    with pytest.raises(FileNotFoundError, match="no forecast for 2024 week 6"):
        world.grade()
    world.forecast()
    (world.paths.playcalling / str(SEASON) / "build.json").unlink()
    with pytest.raises(FileNotFoundError, match="nfl playcalling build --season 2024"):
        world.grade()
    assert not (world.paths.playcalling / str(SEASON) / fr.SCOREBOARD_FILE).exists()


def test_run_grade_logs_and_returns_the_summary(world) -> None:
    world.forecast()
    advance(world)
    lines: list[str] = []
    out = fr.run_grade(SEASON, WEEK, use_wandb=False, paths=world.paths, now=NOW_AFTER,
                       log=lines.append)  # fmt: skip
    assert out["url"] is None and out["summary"]["graded"] == 24 and out["summary"]["rows"] == 24
    assert "graded 24 of 24 rows (graded 24)" in lines[0]
    assert set(out["summary"]["targets"]) == set(KEYS)
    assert out["summary"]["shipped"]["graded"] == 8  # blitz_rate only


def hand_scoreboard() -> pl.DataFrame:
    def row(target, shipped, status, model, b1, b2, b3, b4, in_range):
        return {
            "target": target,
            "shipped": shipped,
            "status": status,
            "err_model": model,
            "err_b1": b1,
            "err_b2": b2,
            "err_b3": b3,
            "err_b4": b4,
            "in_range": in_range,
        }

    return pl.DataFrame(
        [
            row("deep_shot_rate", True, "graded", 0.02, 0.05, 0.04, 0.03, 0.025, True),
            row("deep_shot_rate", True, "graded", 0.06, 0.05, 0.03, 0.04, 0.05, False),
            row("deep_shot_rate", True, "graded", 0.03, 0.05, 0.03, 0.03, 0.03, True),  # a tie
            row("deep_shot_rate", True, "waiting_ftn", None, None, None, None, None, None),
            row("rushers_avg", True, "graded", 0.50, 0.9, 0.6, 0.55, 0.52, True),
            row("blitz_rate", False, "graded", 0.10, 0.12, 0.11, 0.10, 0.10, True),
            row("blitz_rate", False, "not_played", None, None, None, None, None, None),
        ],
        schema={
            "target": pl.String, "shipped": pl.Boolean, "status": pl.String,
            "err_model": pl.Float64, "err_b1": pl.Float64, "err_b2": pl.Float64,
            "err_b3": pl.Float64, "err_b4": pl.Float64, "in_range": pl.Boolean,
        },
    )  # fmt: skip


def test_week_summary_by_hand() -> None:
    s = fr.week_summary(hand_scoreboard())
    assert s["graded"] == 5 and s["rows"] == 7
    deep = s["targets"]["deep_shot_rate"]
    assert deep["graded"] == 3
    assert deep["mae_model"] == pytest.approx(0.11 / 3) and deep["mae_b1"] == pytest.approx(0.05)
    assert deep["mae_b2"] == pytest.approx(0.10 / 3) and deep["mae_b3"] == pytest.approx(0.10 / 3)
    assert deep["mae_b4"] == pytest.approx(0.105 / 3)
    assert deep["beat_b2"] == 1  # 0.02 < 0.04; 0.06 > 0.03; 0.03 = 0.03 is not a win
    assert deep["in_range"] == pytest.approx(2 / 3)
    assert s["targets"]["rushers_avg"]["mae_model"] == pytest.approx(0.5)
    assert s["targets"]["blitz_rate"] == {
        "graded": 1, "mae_model": 0.1, "mae_b1": 0.12, "mae_b2": 0.11, "mae_b3": 0.1,
        "mae_b4": 0.1, "beat_b2": 1, "in_range": 1.0,
    }  # fmt: skip
    # the shipped total: rows of shipped targets; MAE in points only (rushers are a count),
    # but the wins and the range count every shipped row
    sh = s["shipped"]
    assert sh["graded"] == 4
    assert sh["mae_model"] == pytest.approx(0.11 / 3)  # deep shots only
    assert sh["mae_b2"] == pytest.approx(0.10 / 3)
    assert sh["beat_b2"] == 2  # deep 0.02 < 0.04 and rushers 0.50 < 0.60
    assert sh["in_range"] == pytest.approx(3 / 4)
    # nothing graded: an empty summary that the CLI can test with .get("graded")
    none = fr.week_summary(hand_scoreboard().filter(pl.col("status") != "graded"))
    assert none["graded"] == 0 and none["targets"] == {} and none["shipped"] == {"graded": 0}
    only_mean = fr.week_summary(hand_scoreboard().filter(pl.col("target") == "rushers_avg"))
    assert only_mean["shipped"]["mae_model"] is None and only_mean["shipped"]["beat_b2"] == 1


# --- evaluate_target, run_backtest, the model folder ----------------------------------------------
@pytest.fixture(scope="module")
def bt_league() -> X.League:
    return X.random_league(
        seed=11, teams=X.TEAMS8, seasons=(2022, 2023, 2024), weeks=12, targets=TARGETS
    )  # fmt: skip


def bt_inputs(lg: X.League) -> fr.Inputs:
    return fr.Inputs(lg.tg, lg.games_df(), lg.changed, lg.midseason, [])


def test_evaluate_target_scores_one_cohort(bt_league) -> None:
    lg = bt_league
    cfg = F.forecast_settings()
    r = fr.evaluate_target(DEEP, bt_inputs(lg), lg.curves, [2022, 2023, 2024], [2022, 2023], cfg)
    assert r is not None and r.seasons == [2022, 2023, 2024] and r.tune == [2022, 2023]
    p = r.preds
    assert p.height == 8 * 12 * 3
    assert set(F.KINDS) <= set(p.columns) and {"lo", "hi", "cohort", "tuning"} <= set(p.columns)
    # the cohort: every predictor and the actual are there (2022's first week has no prior)
    fin = np.isfinite(p["actual"].to_numpy()) & (p["n"].to_numpy() > 0)
    for kind in F.KINDS:
        fin &= np.isfinite(p[kind].to_numpy())
    assert p["cohort"].to_list() == fin.tolist()
    first = p.filter((pl.col("season") == 2022) & (pl.col("week") == 1))
    assert not first["cohort"].any() and first["model"].is_nan().all()
    assert p.filter(pl.col("cohort")).height == p.height - 8
    assert p["tuning"].to_list() == p["season"].is_in([2022, 2023]).to_list()
    c = p.filter(pl.col("cohort"))
    # the scores are over the cohort only
    assert sorted(r.by_season) == [2022, 2023, 2024]
    for s in (2022, 2023, 2024):
        n = c.filter(pl.col("season") == s).height
        assert all(r.by_season[s][k]["games"] == n for k in F.KINDS), s
    assert r.pooled["model"]["games"] == c.height
    assert sum(m["model"]["games"] for m in r.by_part.values()) == c.height
    assert list(r.by_part) == [name for _, _, name in F.PARTS]
    assert r.test["model"]["games"] == c.filter(~pl.col("tuning")).height
    # the same cohort for every predictor, so the MAE are comparable
    assert len({r.pooled[k]["games"] for k in F.KINDS}) == 1
    y = c["actual"].to_numpy()
    assert r.pooled["b2"]["mae"] == pytest.approx(float(np.mean(np.abs(c["b2"].to_numpy() - y))))
    # the range's coverage is that of the cohort
    inside = ((c["actual"] >= c["lo"]) & (c["actual"] <= c["hi"])).mean()
    assert r.coverage == pytest.approx(inside)
    assert set(r.coverage_by_season) == {2022, 2023, 2024}
    # the parameters were fit on the tuning seasons only
    d = X.make_design(DEEP, lg.tg, lg.games, curves=lg.curves, changed=lg.changed,
                      mid=lg.midseason, seasons=[2022, 2023, 2024])  # fmt: skip
    mask = d.rows["season"].is_in([2022, 2023]).to_numpy()
    assert r.fits["b3"]["params"] == F.fit_kind(d, "b3", mask, cfg)["params"]
    assert r.fits["b3"]["params"] != F.fit_kind(d, "b3", np.ones_like(mask), cfg)["params"]
    assert r.fits["b1"]["params"] == {} and r.rng["q_lo"] <= 0 <= r.rng["q_hi"]
    assert r.verdict == F.ship_verdict(DEEP, r.by_season, r.pooled, cfg["ship"])
    assert r.verdict["seasons"] == 3 and len(r.calib) == 10 and math.isfinite(r.ece)
    # the pieces of a prediction are carried for the pages
    m = c.filter(pl.col("target") == "deep_shot_rate")
    np.testing.assert_allclose(
        m["model"].to_numpy(),
        np.clip((m["team_term"] + m["opponent_term"] + m["script_term"]).to_numpy(), 0.0, 1.0),
        atol=1e-12,
    )


def test_evaluate_target_respects_the_first_season_of_ftn_data(bt_league) -> None:
    lg = bt_league
    cfg = F.forecast_settings()
    r = fr.evaluate_target(
        BLITZ, bt_inputs(lg), lg.curves, [2020, 2021, 2022, 2023], [2021, 2022], cfg
    )
    assert r is not None and r.seasons == [2022, 2023] and r.tune == [2022]
    # nothing to evaluate: no season with data, or no tuning season among them
    assert (
        fr.evaluate_target(BLITZ, bt_inputs(lg), lg.curves, [2019, 2020, 2021], [2020], cfg) is None
    )
    assert (
        fr.evaluate_target(BLITZ, bt_inputs(lg), lg.curves, [2023, 2024], [2020, 2021], cfg) is None
    )


def _verdict_that_ships(t, by_season, pooled, rules=None):
    return {"target": t.key, "seasons": len(by_season), "wins_vs_b2": len(by_season),
            "need": 1, "rule_a": True, "rule_b": True, "ships": True, "by_season": []}  # fmt: skip


@pytest.fixture
def bt_paths(tmp_path, bt_league) -> DataPaths:
    lg = bt_league
    return X.write_tree(
        tmp_path, lg.games, lg.tg,
        build={SEASON: {"as_of_weeks": [1, 12], "coverage": {"games_without_ftn": []}}},
    )  # fmt: skip


def run_bt(paths, **kw):
    return fr.run_backtest(
        kw.pop("seasons", [2023, 2024]), kw.pop("tune", [2023]),
        kw.pop("targets", ["deep_shot_rate", "blitz_rate"]),
        paths=paths, log=X.quiet, **{"use_wandb": False, **kw},
    )  # fmt: skip


def test_run_backtest_saves_the_summary_and_predictions(bt_paths) -> None:
    out = run_bt(bt_paths)
    folder = bt_paths.playcalling / "backtests" / "2023-2024"
    assert out["folder"] == folder and out["url"] is None and out["version"] is None
    assert [r.target.key for r in out["results"]] == ["deep_shot_rate", "blitz_rate"]
    text = (folder / "summary.json").read_text(encoding="utf-8")

    def boom(x):
        raise AssertionError(f"{x} in the summary: not valid JSON for the pages")

    doc = json.loads(text, parse_constant=boom)
    assert doc["seasons"] == [2023, 2024] and doc["tune_seasons"] == [2023]
    assert set(doc["targets"]) == {"deep_shot_rate", "blitz_rate"}
    assert doc["targets"]["deep_shot_rate"]["pooled"]["model"]["games"] > 0
    assert doc["wandb_run"] is None and doc["proe_per_play"] == {}
    assert set(doc["curves"]) == {"2022", "2023", "2024"}  # the prior season's too
    preds = pl.read_parquet(folder / "predictions.parquet")
    assert preds.height == sum(r.preds.height for r in out["results"])
    assert set(preds["target"]) == {"deep_shot_rate", "blitz_rate"}
    assert not (bt_paths.models / "playcall").exists()  # no promotion unless asked


def test_run_backtest_without_saving_or_with_bad_seasons(bt_paths) -> None:
    out = run_bt(bt_paths, save=False)
    assert out["folder"] is None and not (bt_paths.playcalling / "backtests").exists()
    with pytest.raises(ValueError, match="tuning seasons must be inside the reported seasons"):
        run_bt(bt_paths, tune=[2019])
    with pytest.raises(KeyError, match="unknown play-calling target"):
        run_bt(bt_paths, targets=["nope"])
    # a target with no data in the window is skipped (the others still run)
    out2 = run_bt(bt_paths, targets=["deep_shot_rate", "blitz_rate"], seasons=[2021, 2022, 2023],
                  tune=[2022], save=False)  # fmt: skip
    assert [r.target.key for r in out2["results"]] == ["deep_shot_rate", "blitz_rate"]
    assert out2["results"][1].seasons == [2022, 2023]  # FTN charting starts in 2022


def test_a_backtest_with_nothing_to_evaluate_says_so(bt_paths) -> None:
    with pytest.raises(ValueError, match="no selected target has data in 2020-2021"):
        run_bt(bt_paths, seasons=[2020, 2021], tune=[2020], targets=["blitz_rate"])
    assert not (bt_paths.playcalling / "backtests").exists()


def test_a_backtest_that_ships_nothing_cannot_promote(bt_paths, monkeypatch) -> None:
    def never(t, by_season, pooled, rules=None):
        return {**_verdict_that_ships(t, by_season, pooled), "ships": False, "rule_a": False}

    monkeypatch.setattr(F, "ship_verdict", never)
    with pytest.raises(RuntimeError, match=r"no target ships by the rule \(D130\)"):
        run_bt(bt_paths, promote=True)
    assert not (bt_paths.models / "playcall").exists()  # nothing half-written
    assert run_bt(bt_paths)["shipped"] == []


def test_promotion_writes_the_model_folder_the_forecast_reads(bt_paths, monkeypatch) -> None:
    monkeypatch.setattr(F, "ship_verdict", _verdict_that_ships)
    out = run_bt(bt_paths, promote=True, seasons=[2022, 2023], tune=[2022])
    version = out["version"]
    assert re.fullmatch(fr.VERSION_RE, version)  # the shape load_model accepts
    assert version.startswith("2022-2023_") and out["shipped"] == ["deep_shot_rate", "blitz_rate"]
    root = bt_paths.models / "playcall"
    ptr = json.loads((root / "production.json").read_text(encoding="utf-8"))
    assert ptr["version"] == version and ptr["shipped"] == out["shipped"]
    doc = fr.load_model(bt_paths)
    assert doc["family"] == "playcall-model" and doc["version"] == version
    assert doc["shipped"] == ["deep_shot_rate", "blitz_rate"]
    for key in ("deep_shot_rate", "blitz_rate"):
        tp = doc["targets"][key]
        # refit on every reported season (the backtest fit 2022 only)
        assert tp["fit_seasons"] == [2022, 2023] and tp["shipped"] is True
        assert set(tp["kinds"]) == set(F.KINDS)
        assert set(tp["kinds"]["model"]["params"]) == set(F.kind_params(F.get_target(key), "model"))
        assert set(tp["range"]) >= {"c", "b", "q_lo", "q_hi"}
        assert tp["backtest"]["mae"]["model"] > 0 and "coverage_80" in tp["backtest"]
        assert (
            tp["tuning_fit"]["b3"]
            == next(r for r in out["results"] if r.target.key == key).fits["b3"]["params"]
        )
    r = next(r for r in out["results"] if r.target.key == "deep_shot_rate")
    assert doc["targets"]["deep_shot_rate"]["kinds"]["b3"]["params"] != r.fits["b3"]["params"]
    # the saved model is what run_forecast reads, end to end: 2024 is out of its sample
    fc = fr.run_forecast(SEASON, 8, use_wandb=False, paths=bt_paths, log=X.quiet, now=NOW_AFTER)
    assert set(fc["frame"]["target"]) == {"deep_shot_rate", "blitz_rate"}
    assert fc["frame"]["shipped"].all() and fc["frame"]["version"].unique().to_list() == [version]
    # ... and a season it was fit on is refused: that forecast would be in-sample
    with pytest.raises(ValueError, match=r"fit on 2022-2023, 2023 included.*in-sample"):
        fr.run_forecast(2023, 8, use_wandb=False, paths=bt_paths, log=X.quiet, now=NOW_AFTER)
    assert not (bt_paths.playcalling / "2023" / "week08").exists()


def test_a_smoke_backtest_never_promotes(bt_paths, monkeypatch) -> None:
    monkeypatch.setattr(F, "ship_verdict", _verdict_that_ships)
    out = run_bt(bt_paths, promote=True, smoke=True)
    assert out["version"] is None and out["folder"].name == "smoke"
    assert not (bt_paths.models / "playcall").exists()


# --- small pieces ---------------------------------------------------------------------------------
def test_jsonable_and_atomic_json(tmp_path) -> None:
    doc = {
        1: np.float64("nan"), "a": [np.int64(3), np.bool_(True), (np.float32(1.5), float("inf"))],
        "b": {"c": float("-inf"), "d": None, "e": "x"},
    }  # fmt: skip
    assert fr._jsonable(doc) == {
        "1": None, "a": [3, True, [1.5, None]], "b": {"c": None, "d": None, "e": "x"},
    }  # fmt: skip
    path = tmp_path / "deep" / "x.json"
    fr._atomic_json(path, doc)
    assert json.loads(path.read_text(encoding="utf-8"))["a"] == [3, True, [1.5, None]]
    assert [p.name for p in path.parent.iterdir()] == ["x.json"]


def test_proe_play_scores_by_hand(tmp_path) -> None:
    paths = DataPaths(tmp_path)
    folder = B.season_dir(paths, 2024)
    folder.mkdir(parents=True)
    plays = pl.DataFrame(
        {
            "game_id": ["g1", "g1", "g1", "g1", "g2", "g3"],
            "offense": ["AAA", "AAA", "AAA", "BBB", "AAA", "AAA"],
            "xpass": [0.50, 0.60, 0.40, 0.70, 0.30, 0.50],
            "dropback": [True, False, True, True, False, True],
            "neutral": [True, True, False, True, True, True],
        }
    )
    plays.write_parquet(folder / "plays_enriched.parquet")
    kinds = {"b1": 0.0, "b2": 0.02, "b3": 0.04, "b4": 0.06, "model": 0.10}
    preds = pl.DataFrame(
        {
            "target": ["proe", "proe", "proe", "deep_shot_rate"],
            "cohort": [True, True, False, True],
            "season": [2024] * 4,
            "game_id": ["g1", "g1", "g3", "g1"],
            "team": ["AAA", "BBB", "AAA", "AAA"],
            **{k: [v, v, v, 0.9] for k, v in kinds.items()},
        }
    )
    out = fr.proe_play_scores(paths, preds)
    # g1 / AAA: neutral plays 1 and 2 (the third is not neutral); g1 / BBB: one play;
    # g2 is not in the forecast, g3's forecast is not in the cohort
    assert out["plays"] == 3
    y = np.array([1.0, 0.0, 1.0])
    xp = np.array([0.5, 0.6, 0.7])
    for kind, v in kinds.items():
        q = np.clip(xp + v, 1e-3, 1 - 1e-3)
        assert out[kind]["brier"] == pytest.approx(float(np.mean((y - q) ** 2)))
        assert out[kind]["logloss"] == pytest.approx(
            float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))
        )
    assert out["ece"] >= 0 or math.isnan(out["ece"])
    assert fr.proe_play_scores(paths, preds.filter(pl.col("target") != "proe")) == {}
    assert fr.proe_play_scores(DataPaths(tmp_path / "nowhere"), preds) == {}


# --- more edges: the range's ends, a firing, a missing line, a partial promotion ------------------
def test_an_actual_exactly_on_the_edge_of_the_range_is_in_range(world) -> None:
    world.forecast()
    g1 = week_games(world)[0]
    advance(world)
    ob = {o.team: o for o in X.ref_obs(DEEP, world.league.tg, world.games, {}) if o.game_id == g1}
    ta, tb = sorted(ob)
    lo_edge, hi_edge = ob[ta].k / ob[ta].n, ob[tb].k / ob[tb].n
    fc_path = world.paths.playcalling / str(SEASON) / "week06" / fr.FORECAST_FILE
    deep = (pl.col("game_id") == g1) & (pl.col("target") == "deep_shot_rate")
    fc = pl.read_parquet(fc_path).with_columns(
        pl.when(deep & (pl.col("team") == ta)).then(lo_edge).otherwise(pl.col("lo")).alias("lo"),
        pl.when(deep & (pl.col("team") == tb)).then(hi_edge).otherwise(pl.col("hi")).alias("hi"),
    )
    fc.write_parquet(fc_path)
    wk = world.grade()["frame"].filter(deep)
    got = {r["team"]: r for r in wk.iter_rows(named=True)}
    assert got[ta]["actual"] == lo_edge == got[ta]["lo"] and got[ta]["in_range"] is True
    assert got[tb]["actual"] == hi_edge == got[tb]["hi"] and got[tb]["in_range"] is True


def test_a_firing_weights_the_team_term_but_not_the_plays_shown(tmp_path) -> None:
    """AAA's head coach changes in week 4: its games before it count half in the forecast's team
    term (the strength), but `n_so_far` is the plain count of its plays."""
    w = make_world(tmp_path)
    games = X.clone(w.games)
    for g in games:
        if g["season"] == SEASON and g["week"] >= 4:
            for side in ("home", "away"):
                if g[f"{side}_team"] == "AAA":
                    g[f"{side}_coach"] = "AAA-interim"
    X.games_frame(games).write_parquet(w.paths.curated / "games.parquet")
    fc = w.forecast()["frame"]
    t = DEEP
    obs = X.ref_obs(t, w.tg_live, games, w.league.curves)
    mine = [o for o in obs if o.season == SEASON and o.team == "AAA" and o.week < WEEK]
    before = sum(o.n for o in mine if o.week < 4)
    after = sum(o.n for o in mine if o.week >= 4)
    row = fc.filter((pl.col("team") == "AAA") & (pl.col("target") == "deep_shot_rate")).row(
        0, named=True
    )
    assert row["n_so_far"] == before + after  # what the page shows: the plays so far
    assert row["strength"] == pytest.approx(150 + 0.5 * before + after)  # kappa + weighted plays
    assert row["games_so_far"] == len(mine) == 5
    # nobody else is down-weighted
    other = fc.filter((pl.col("team") == "BBB") & (pl.col("target") == "deep_shot_rate")).row(
        0, named=True
    )
    assert other["strength"] == pytest.approx(150 + other["n_so_far"])
    # the weight is the one the model was fit with (its saved settings), not today's setting
    doc = X.model_doc(KEYS, shipped=("blitz_rate",))
    doc["settings"] = {**doc["settings"], "pre_change_weight": 0.25}
    X.write_model(w.paths, doc)
    row2 = (
        w.forecast()["frame"]
        .filter((pl.col("team") == "AAA") & (pl.col("target") == "deep_shot_rate"))
        .row(0, named=True)
    )
    assert row2["strength"] == pytest.approx(150 + 0.25 * before + after)


def test_the_meta_counts_games_with_a_line(tmp_path) -> None:
    w = make_world(tmp_path)
    games = X.clone(w.games)
    week6 = [g for g in games if g["season"] == SEASON and g["week"] == WEEK]
    week6[0]["spread_line"] = None  # one game without a spread yet
    X.games_frame(games).write_parquet(w.paths.curated / "games.parquet")
    out = w.forecast()
    assert out["meta"]["lines"] == {"with_line": 3, "sources": ["nflverse_schedules"]}
    row = out["frame"].filter(pl.col("game_id") == week6[0]["game_id"]).row(0, named=True)
    assert row["spread"] is None and row["line_source"] is None
    assert out["frame"].filter(pl.col("game_id") == week6[1]["game_id"])["line_source"][0] == (
        "nflverse_schedules"
    )


def test_a_promotion_marks_only_the_targets_that_shipped(bt_paths, monkeypatch) -> None:
    def only_deep(t, by_season, pooled, rules=None):
        return {**_verdict_that_ships(t, by_season, pooled), "ships": t.key == "deep_shot_rate"}

    monkeypatch.setattr(F, "ship_verdict", only_deep)
    out = run_bt(bt_paths, promote=True, seasons=[2022, 2023], tune=[2022])
    assert out["shipped"] == ["deep_shot_rate"]
    doc = fr.load_model(bt_paths)
    assert doc["shipped"] == ["deep_shot_rate"]
    assert doc["targets"]["deep_shot_rate"]["shipped"] is True
    assert doc["targets"]["blitz_rate"]["shipped"] is False
    ptr = json.loads((bt_paths.models / "playcall" / "production.json").read_text("utf-8"))
    assert ptr["shipped"] == ["deep_shot_rate"]
    fc = fr.run_forecast(SEASON, 8, use_wandb=False, paths=bt_paths, log=X.quiet, now=NOW_AFTER)
    flags = fc["frame"].group_by("target").agg(pl.col("shipped").all().alias("s"))
    assert dict(flags.iter_rows()) == {"deep_shot_rate": True, "blitz_rate": False}


# --- the W&B logging, against a fake run (no wandb.init, no network, nothing on D:) ------------
class FakeRun:
    url = "https://wandb.ai/x/nfl-analytics-engine/runs/fake"

    def __init__(self, kw: dict) -> None:
        self.kw = kw
        self.metrics: list[tuple] = []
        self.logged: list[dict] = []
        self.artifacts: list[tuple] = []
        self.summary = self
        self.data: dict = {}
        self.finished = 0

    def update(self, d: dict) -> None:  # run.summary.update
        self.data.update(d)

    def define_metric(self, *a, **k) -> None:
        self.metrics.append((a, k))

    def log(self, row: dict) -> None:
        self.logged.append(row)

    def log_artifact(self, art, aliases=()) -> None:
        self.artifacts.append((art, list(aliases)))

    def finish(self) -> None:
        self.finished += 1


@pytest.fixture
def fake_wandb(monkeypatch) -> list[FakeRun]:
    from nflengine import tracking

    runs: list[FakeRun] = []

    def init_run(**kw):
        runs.append(FakeRun(kw))
        return runs[-1]

    monkeypatch.setattr(tracking, "init_run", init_run)
    return runs


def test_forecast_and_grade_log_their_runs(world, fake_wandb) -> None:
    out = world.forecast(use_wandb=True, launched_by="agent")
    (run,) = fake_wandb
    assert out["url"] == run.url and run.finished == 1
    assert run.kw["group"] == "play-calling" and run.kw["job_type"] == "playcall-forecast"
    assert run.kw["name"] == "playcall-forecast-2024-w06" and run.kw["launched_by"] == "agent"
    assert run.kw["tags"] == ["pc02", "season:2024", "week:06", "backfill"]  # Thursday had started
    assert run.kw["config"]["version"] == X.VERSION and run.kw["config"]["backfill"] is True
    # only the shipped target's rows are shown in the W&B table
    table = run.logged[0]["forecast/rows"]
    assert len(table.data) == 8  # blitz_rate: 4 games x 2 teams
    assert run.data == {"games": 4, "rows": 24, "shipped_rows": 8, "games_after_kickoff": 1}
    # smoke runs go to their own group, with a tag
    world.forecast(use_wandb=True, smoke=True, now=NOW_BEFORE)
    assert fake_wandb[1].kw["group"] == "play-calling-smoke"
    assert fake_wandb[1].kw["tags"] == ["pc02", "season:2024", "week:06", "smoke"]
    # grading
    advance(world)
    g = world.grade(use_wandb=True)
    run = fake_wandb[2]
    assert g["url"] == run.url and run.finished == 1
    assert run.kw["job_type"] == "playcall-grade" and run.kw["name"] == "playcall-grade-2024-w06"
    assert run.kw["tags"] == ["pc02", "season:2024", "week:06"]
    assert set(run.logged[0]) == {"grade/targets_rows", "grade/rows"}
    assert run.data["graded"] == 24 and run.data["rows"] == 24
    assert run.data["shipped/graded"] == 8  # the shipped target's rows
    assert "shipped/mae_model" in run.data and "shipped/in_range" in run.data


def test_a_failed_logging_step_still_finishes_the_run(world, fake_wandb, monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("W&B is down")

    monkeypatch.setattr(fr, "_log_forecast_run", boom)
    with pytest.raises(RuntimeError, match="W&B is down"):
        world.forecast(use_wandb=True)
    # the forecast itself was written before the logging step
    assert (world.paths.playcalling / str(SEASON) / "week06" / fr.FORECAST_FILE).is_file()


def test_the_backtest_logs_live_curves_tables_and_its_summary(bt_paths, fake_wandb) -> None:
    out = run_bt(bt_paths, use_wandb=True, launched_by="agent", targets=["deep_shot_rate"])
    assert [r.target.key for r in out["results"]] == ["deep_shot_rate"]
    (run,) = fake_wandb
    assert out["url"] == run.url and run.finished == 1
    kw = run.kw
    assert kw["group"] == "play-calling" and kw["job_type"] == "playcall-backtest"
    assert kw["name"] == "playcall-backtest-2023-2024" and kw["launched_by"] == "agent"
    assert kw["tags"] == ["pc02", "seasons:2023-2024"]
    cfg = kw["config"]
    assert cfg["seasons"] == [2023, 2024] and cfg["tune_seasons"] == [2023]
    assert cfg["targets"] == ["deep_shot_rate"] and cfg["schema_version"] == F.SCHEMA_VERSION
    assert set(cfg["curves"]) == {2022, 2023, 2024} and "git_commit" in cfg
    # live curves: one row per (season, week), cumulative MAEs that end at the pooled ones
    assert run.metrics[0][0] == ("bt/step",)
    steps = [row for row in run.logged if "bt/step" in row]
    r = out["results"][0]
    cohort = r.preds.filter(pl.col("cohort")).select("season", "week").unique()
    assert len(steps) == cohort.height
    assert [row["bt/step"] for row in steps] == list(range(1, len(steps) + 1))
    order = sorted((row["bt/season"], row["bt/week"]) for row in steps)
    assert [(row["bt/season"], row["bt/week"]) for row in steps] == order  # in time order
    last = steps[-1]
    for kind in F.KINDS:
        assert last[f"bt/deep_shot_rate/mae_{kind}"] == pytest.approx(r.pooled[kind]["mae"])
    assert "bt/deep_shot_rate/week_mae_model" in last and "bt/deep_shot_rate/week_mae_b2" in last
    # the tables and charts logged once at the end
    final = next(row for row in run.logged if "backtest/ship_rows" in row)
    wanted = {
        "backtest/by_season_rows", "backtest/by_part_rows", "backtest/params_rows",
        "backtest/coverage_rows", "backtest/script_curve_rows",
        "mae_vs_baseline/deep_shot_rate", "calibration/deep_shot_rate",
    }  # fmt: skip
    assert wanted <= set(final)
    assert len(final["backtest/script_curve_rows"].data) == 3  # one curve per season
    # the summary the model card reads
    d = run.data
    assert d["deep_shot_rate/mae_model"] == pytest.approx(r.pooled["model"]["mae"])
    assert d["deep_shot_rate/mae_b2"] == pytest.approx(r.pooled["b2"]["mae"])
    assert d["deep_shot_rate/wins_vs_b2"] == r.verdict["wins_vs_b2"]
    assert d["deep_shot_rate/improvement_vs_b2_pct"] == pytest.approx(
        100 * (r.pooled["b2"]["mae"] - r.pooled["model"]["mae"]) / r.pooled["b2"]["mae"]
    )
    assert d["deep_shot_rate/brier_model"] == pytest.approx(r.pooled["model"]["brier"])
    assert d["shipped"] == ",".join(out["shipped"])


def test_a_promotion_logs_the_model_artifact_with_the_production_alias(
    bt_paths, fake_wandb, monkeypatch
) -> None:
    monkeypatch.setattr(F, "ship_verdict", _verdict_that_ships)
    out = run_bt(bt_paths, promote=True, use_wandb=True)
    (run,) = fake_wandb
    ((art, aliases),) = run.artifacts
    assert aliases == [out["version"], "production"]
    assert art.name.startswith("playcall-model") and art.type == "model"
    assert art.metadata["version"] == out["version"]
    assert art.metadata["shipped"] == ["deep_shot_rate", "blitz_rate"]
    assert "params.json" in set(art.manifest.entries)  # the saved model folder is what it holds
    # an ordinary backtest logs no artifact at all
    run_bt(bt_paths, use_wandb=True)
    assert fake_wandb[1].artifacts == []


# --- Sol's review (D131): in-sample forecasts and versions that name paths ---------------------
def test_a_forecast_for_a_season_the_model_was_fit_on_is_refused(world) -> None:
    doc = X.model_doc(KEYS, shipped=("blitz_rate",), fit_seasons=range(2019, 2025))
    X.write_model(world.paths, doc)
    with pytest.raises(ValueError) as err:
        world.forecast()
    msg = str(err.value)
    assert "was fit on 2019-2024, 2024 included" in msg and "in-sample" in msg
    assert "backtest" in msg and "predictions.parquet" in msg  # where a past season's live
    assert X.VERSION in msg
    assert not (world.paths.playcalling / str(SEASON) / "week06").exists()  # nothing written
    # the season may sit anywhere in the fit (first, middle, last), not just at its end
    for fit in ([2024, 2025], [2019, 2024, 2026], range(2019, 2027), [2024]):
        X.write_model(world.paths, X.model_doc(KEYS, shipped=("blitz_rate",), fit_seasons=fit))
        with pytest.raises(ValueError, match="in-sample"):
            world.forecast()
    # any target's fit seasons count; a model fit through the season before is fine
    mixed = X.model_doc(KEYS, shipped=("blitz_rate",))
    mixed["targets"]["blitz_rate"]["fit_seasons"] = [2022, 2024]
    X.write_model(world.paths, mixed)
    with pytest.raises(ValueError, match="in-sample"):
        world.forecast()
    X.write_model(world.paths, X.model_doc(KEYS, shipped=("blitz_rate",)))
    assert world.forecast()["frame"].height == 24


@pytest.mark.parametrize("version", ["..\\x", "../x", "..", "C:\\temp\\params", "/etc/x", "v1"])
def test_run_forecast_refuses_a_version_that_names_a_path(world, version) -> None:
    outside = world.paths.models / "x"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "params.json").write_text(json.dumps(X.model_doc(KEYS)), encoding="utf-8")
    with pytest.raises(ValueError, match="not a playcall-model version"):
        world.forecast(version=version)
    assert not (world.paths.playcalling / str(SEASON) / "week06").exists()
