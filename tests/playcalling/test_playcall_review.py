"""Regression tests for the PC00 build review: FTN placeholder rows, the last_season metric
filter, stale build.json, through-week folders, typed empty tables, the W&B coverage guard,
FTN label whitespace and a deterministic FTN dedupe."""

from __future__ import annotations

import sys
import types
from dataclasses import replace
from pathlib import Path

import polars as pl
import pytest

from nflengine.paths import DataPaths
from nflengine.playcalling import build as B
from nflengine.playcalling import labels as L

PLAY_SCHEMA = {
    "game_id": pl.String, "play_id": pl.Float64, "season": pl.Int32, "week": pl.Int32,
    "season_type": pl.String, "game_date": pl.String, "order_sequence": pl.Float64,
    "posteam": pl.String, "defteam": pl.String, "home_team": pl.String, "qtr": pl.Float64,
    "half_seconds_remaining": pl.Float64, "down": pl.Float64, "ydstogo": pl.Float64,
    "yardline_100": pl.Float64, "goal_to_go": pl.Float64, "score_differential": pl.Float64,
    "wp": pl.Float64, "xpass": pl.Float64, "pass_oe": pl.Float64, "play_type": pl.String,
    "pass": pl.Float64, "qb_scramble": pl.Float64, "sack": pl.Float64,
    "aborted_play": pl.Float64, "pass_length": pl.String, "pass_location": pl.String,
    "air_yards": pl.Float64, "complete_pass": pl.Float64, "interception": pl.Float64,
    "run_location": pl.String, "run_gap": pl.String, "shotgun": pl.Float64,
    "no_huddle": pl.Float64, "yards_gained": pl.Float64, "epa": pl.Float64,
    "success": pl.Float64, "first_down": pl.Float64, "touchdown": pl.Float64,
    "passer_player_id": pl.String, "rusher_player_id": pl.String,
    "receiver_player_id": pl.String, "desc": pl.String, "two_point_attempt": pl.Float64,
}  # fmt: skip


def _game_id(season: int, week: int, away: str, home: str) -> str:
    return f"{season}_{week:02d}_{away}_{home}"


def _plays(season: int, games: list[tuple[int, str, str]], per_team: int = 4) -> pl.DataFrame:
    """`per_team` plays for each team in each (week, away, home) game: alternating dropbacks
    (passes) and designed runs, neutral situations."""
    rows = []
    for week, away, home in games:
        gid = _game_id(season, week, away, home)
        pid = 0
        for off, dfn in ((away, home), (home, away)):
            for i in range(per_team):
                pid += 1
                is_pass = i % 2 == 0
                rows.append(
                    {
                        "game_id": gid, "play_id": float(pid), "season": season, "week": week,
                        "season_type": "REG", "game_date": f"{season}-09-{week + 9:02d}",
                        "order_sequence": float(pid), "posteam": off, "defteam": dfn,
                        "home_team": home, "qtr": 1.0, "half_seconds_remaining": 1500.0,
                        "down": 1.0, "ydstogo": 10.0, "yardline_100": 60.0, "goal_to_go": 0.0,
                        "score_differential": 0.0, "wp": 0.5, "xpass": 0.55,
                        "pass_oe": 45.0 if is_pass else -55.0,
                        "play_type": "pass" if is_pass else "run",
                        "pass": 1.0 if is_pass else 0.0, "qb_scramble": 0.0, "sack": 0.0,
                        "aborted_play": 0.0, "pass_length": "short" if is_pass else None,
                        "pass_location": "left" if is_pass else None,
                        "air_yards": 8.0 if is_pass else None, "complete_pass": 1.0,
                        "interception": 0.0, "run_location": None if is_pass else "middle",
                        "run_gap": None, "shotgun": 1.0, "no_huddle": 0.0, "yards_gained": 5.0,
                        "epa": 0.1, "success": 1.0, "first_down": 0.0, "touchdown": 0.0,
                        "passer_player_id": None, "rusher_player_id": None,
                        "receiver_player_id": None, "desc": "x", "two_point_attempt": 0.0,
                    }
                )  # fmt: skip
    return pl.DataFrame(rows, schema=PLAY_SCHEMA)


def _ftn_row(game_id: str, play_id: float, **kw) -> dict:
    row = {
        "nflverse_game_id": game_id, "nflverse_play_id": int(play_id), "game_id": game_id,
        "play_id": float(play_id), "season": int(game_id[:4]), "week": int(game_id[5:7]),
        "qb_location": "S", "starting_hash": "L", "n_offense_backfield": 1, "n_defense_box": 6,
        "is_play_action": False, "is_screen_pass": False, "is_rpo": False, "is_motion": True,
        "is_trick_play": False, "is_qb_out_of_pocket": False, "n_blitzers": 1,
        "n_pass_rushers": 5,
    }  # fmt: skip
    return {**row, **kw}


FTN_SCHEMA = {
    "nflverse_game_id": pl.String, "nflverse_play_id": pl.Int32, "game_id": pl.String,
    "play_id": pl.Float64, "season": pl.Int32, "week": pl.Int32, "qb_location": pl.String,
    "starting_hash": pl.String, "n_offense_backfield": pl.Int32, "n_defense_box": pl.Int32,
    "is_play_action": pl.Boolean, "is_screen_pass": pl.Boolean, "is_rpo": pl.Boolean,
    "is_motion": pl.Boolean, "is_trick_play": pl.Boolean, "is_qb_out_of_pocket": pl.Boolean,
    "n_blitzers": pl.Int32, "n_pass_rushers": pl.Int32,
}  # fmt: skip


def _ftn(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=FTN_SCHEMA)


def _games(season: int, games: list[tuple[int, str, str]], done: bool = True) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [season] * len(games),
            "week": [w for w, _, _ in games],
            "game_id": [_game_id(season, w, a, h) for w, a, h in games],
            "game_type": ["REG"] * len(games),
            "result": [3 if done else None] * len(games),
        },
        schema={
            "season": pl.Int32,
            "week": pl.Int32,
            "game_id": pl.String,
            "game_type": pl.String,
            "result": pl.Int32,
        },  # fmt: skip
    )


def _inputs(plays: pl.DataFrame, games: pl.DataFrame, ftn=None, part=None) -> B.Inputs:
    return B.Inputs(
        plays=plays,
        ftn=ftn if ftn is not None else pl.DataFrame(),
        part=part if part is not None else pl.DataFrame(),
        pfr=pl.DataFrame(),
        games=games,
    )


SCHED = [(1, "KC", "BUF"), (2, "BUF", "KC")]


# --- 1. FTN placeholder rows -------------------------------------------------------------------
def test_ftn_placeholder_rows_are_not_charted() -> None:
    plays = _plays(2024, SCHED[:1])  # KC plays 1-4 (1, 3 dropbacks), BUF plays 5-8
    gid = _game_id(2024, 1, "KC", "BUF")
    off = {"qb_location": "0", "n_defense_box": 0, "is_motion": False, "n_blitzers": 0,
           "n_pass_rushers": 0, "starting_hash": "0"}  # fmt: skip
    ftn = _ftn(
        [
            _ftn_row(gid, 1, is_play_action=True),  # charted dropback: play-action, blitz
            _ftn_row(gid, 2),  # charted run
            _ftn_row(gid, 3, **off),  # placeholder dropback
            _ftn_row(gid, 4, **{**off, "qb_location": None, "n_defense_box": None}),
            _ftn_row(gid, 5, **{**off, "qb_location": ""}),  # placeholder dropback (BUF)
            _ftn_row(gid, 6, qb_location="0", n_defense_box=7),  # box charted: kept
        ]
    )
    e = B.enrich_plays(plays, ftn, pl.DataFrame())
    by_id = {r["play_id"]: r for r in e.to_dicts()}
    for pid in (3.0, 4.0, 5.0):
        r = by_id[pid]
        assert r["ftn_charted"] is False
        for c in ("play_action", "motion", "blitz", "screen", "qb_alignment", "box", "rushers"):
            assert r[c] is None, (pid, c)
    assert by_id[1.0]["ftn_charted"] and by_id[1.0]["play_action"] is True
    assert by_id[6.0]["ftn_charted"] and by_id[6.0]["qb_alignment"] is None
    assert by_id[6.0]["box"] == 7
    assert by_id[7.0]["ftn_charted"] is False  # no FTN row at all

    tg = B.team_game_sums(e, 2024).filter(pl.col("situation") == "all")

    def cell(team: str, side: str, metric: str) -> tuple[int, float]:
        r = tg.filter(
            (pl.col("team") == team) & (pl.col("side") == side) & (pl.col("metric") == metric)
        )
        return (r["den"].item(), r["num"].item()) if r.height else (0, 0.0)

    # KC's dropbacks: play 1 charted, play 3 a placeholder -> one counted dropback
    assert cell("KC", "offense", "play_action_rate") == (1, 1.0)
    assert cell("KC", "offense", "blitz_rate") == (1, 1.0)
    assert cell("BUF", "defense", "blitz_rate") == (1, 1.0)
    # motion counts every charted play: 1, 2 (KC); 4 is a placeholder
    assert cell("KC", "offense", "motion_rate") == (2, 2.0)
    # BUF's offense: play 5 a placeholder, 6 charted (a run), 7-8 not in FTN
    assert cell("BUF", "offense", "play_action_rate") == (0, 0.0)
    assert cell("BUF", "offense", "motion_rate") == (1, 1.0)
    cov = B.coverage(e, _games(2024, SCHED[:1]), 2024)
    assert cov["ftn_join_rate"] == round(3 / 8, 4)


# --- 8. FTN label whitespace -------------------------------------------------------------------
def test_ftn_labels_strip_whitespace() -> None:
    gid = _game_id(2024, 1, "KC", "BUF")
    ftn = _ftn(
        [
            _ftn_row(gid, 1, qb_location=" S", starting_hash=" L"),
            _ftn_row(gid, 2, qb_location="U ", starting_hash="M"),
            _ftn_row(gid, 3, qb_location="P", starting_hash="R "),
            _ftn_row(gid, 4, qb_location="X", starting_hash="0"),
        ]
    )
    out = B._ftn_labels(ftn).sort("play_id")
    assert out["qb_alignment"].to_list() == ["shotgun", "under_center", "pistol", None]
    assert out["hash"].to_list() == ["left", "middle", "right", None]


# --- 11. deterministic FTN dedupe --------------------------------------------------------------
def _curated(tmp_path: Path, season: int, ftn: pl.DataFrame) -> DataPaths:
    paths = DataPaths(tmp_path)
    (paths.curated / "plays").mkdir(parents=True)
    _plays(season, SCHED[:1]).write_parquet(paths.curated / "plays" / f"season={season}.parquet")
    _games(season, SCHED[:1]).write_parquet(paths.curated / "games.parquet")
    ftn.drop("game_id", "play_id").write_parquet(paths.curated / "ftn_plays.parquet")
    return paths


def test_ftn_dedupe_keeps_the_first_row(tmp_path: Path) -> None:
    gid = _game_id(2024, 1, "KC", "BUF")
    rows = [_ftn_row(gid, 1, is_play_action=True)]
    rows += [_ftn_row(gid, 1, is_play_action=False) for _ in range(30)]
    rows += [_ftn_row(gid, p) for p in range(2, 9)]
    paths = _curated(tmp_path, 2024, _ftn(rows))
    for _ in range(5):
        inp = B.load_inputs(paths, [2024], history=False, log=lambda *_: None)
        assert inp.ftn.height == 8
        first = inp.ftn.filter(pl.col("play_id") == 1.0)
        assert first["is_play_action"].to_list() == [True]
        assert inp.ftn["play_id"].to_list() == [float(p) for p in range(1, 9)]


# --- 2. the last_season window keeps only this season's metrics --------------------------------
def _part(season: int, games: list[tuple[int, str, str]], **labels) -> pl.DataFrame:
    keys = _plays(season, games).select("game_id", "play_id")
    return keys.with_columns(
        *(pl.lit(None, dtype=t).alias(c) for c, t in B.PART_COLUMNS.items() if c not in labels),
        *(pl.lit(v, dtype=B.PART_COLUMNS[c]).alias(c) for c, v in labels.items()),
    )


def test_last_season_has_no_history_metrics_after_participation_ends() -> None:
    last = L.PART_LAST
    plays = pl.concat([_plays(last, SCHED), _plays(last + 1, SCHED)])
    part = _part(last, SCHED, personnel="11", formation="shotgun", def_package="nickel")
    e = B.enrich_plays(plays, pl.DataFrame(), part)
    games = pl.concat([_games(last, SCHED), _games(last + 1, SCHED)])
    inp = _inputs(plays, games, part=part)
    prev = B.build_season(inp, e, last)
    assert prev.tend.filter(pl.col("history_only")).height > 0  # the history season has them
    sb = B.build_season(inp, e, last + 1)
    ls = sb.tend.filter(pl.col("window") == "last_season")
    assert ls.height > 0
    assert ls.filter(pl.col("history_only")).height == 0
    assert sb.tend.filter(pl.col("history_only")).height == 0
    assert sb.meta["history_metrics"] is False
    names = set(sb.tend["metric"].unique().to_list())
    assert all(L.METRIC_BY_NAME[m].exists_in(last + 1) for m in names)


def test_last_season_drops_metrics_absent_in_the_season() -> None:
    """NGS-era formation_singleback (to 2022) never reaches a 2023 row; 2023's own FTN-era
    formation_under_center is absent from 2022's rows."""
    s = L.NGS_ERA_LAST + 1
    plays = pl.concat([_plays(s - 1, SCHED), _plays(s, SCHED)])
    part = pl.concat(
        [_part(s - 1, SCHED, formation="singleback"), _part(s, SCHED, formation="under_center")]
    )
    e = B.enrich_plays(plays, pl.DataFrame(), part)
    inp = _inputs(plays, pl.concat([_games(s - 1, SCHED), _games(s, SCHED)]), part=part)
    cache: dict[int, pl.DataFrame] = {}
    old = B.build_season(inp, e, s - 1, None, cache)
    assert "formation_singleback" in old.tend["metric"].to_list()
    assert "formation_under_center" not in old.tend["metric"].to_list()
    assert "formation_singleback" in cache[s - 1]["metric"].to_list()  # in the S-1 sums ...
    sb = B.build_season(inp, e, s, None, cache)
    metrics = set(sb.tend["metric"].to_list())
    assert "formation_singleback" not in metrics  # ... but not in S's last_season window
    assert "formation_under_center" in metrics
    for m in sb.tend.filter(pl.col("window") == "last_season")["metric"].unique().to_list():
        assert L.METRIC_BY_NAME[m].exists_in(s), m


# --- 4. write_season and stale build.json ------------------------------------------------------
def _season_build(season: int = 2026, through_week: int | None = None) -> B.SeasonBuild:
    plays = _plays(season, SCHED)
    e = B.enrich_plays(plays, pl.DataFrame(), pl.DataFrame())
    return B.build_season(_inputs(plays, _games(season, SCHED)), e, season, through_week)


def test_write_season_removes_stale_build_json_on_failure(tmp_path: Path, monkeypatch) -> None:
    import nflengine.ops.lock as lock

    paths = DataPaths(tmp_path)
    sb = _season_build()
    out = B.season_dir(paths, sb.season)
    out.mkdir(parents=True)
    (out / "build.json").write_text('{"stale": true}', encoding="utf-8")
    real = lock.write_parquet_atomic
    calls = {"n": 0}

    def flaky(df, path, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real(df, path, **kw)

    monkeypatch.setattr(lock, "write_parquet_atomic", flaky)
    with pytest.raises(OSError, match="disk full"):
        B.write_season(paths, sb)
    assert not (out / "build.json").exists()
    assert (out / "plays_enriched.parquet").exists()

    monkeypatch.setattr(lock, "write_parquet_atomic", real)
    B.write_season(paths, sb)
    assert (out / "build.json").exists()
    assert all((out / f"{n}.parquet").exists() for n in B.FILES)


# --- 5. through-week builds get their own folder -----------------------------------------------
def test_build_dir() -> None:
    paths = DataPaths(Path("root"))
    assert B.build_dir(paths, 2026) == B.season_dir(paths, 2026)
    assert B.build_dir(paths, 2026, 5) == B.season_dir(paths, 2026) / "through_week05"
    assert B.build_dir(paths, 2026, 12).name == "through_week12"


def test_through_week_build_leaves_the_season_folder_alone(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path)
    season = B.season_dir(paths, 2026)
    season.mkdir(parents=True)
    (season / "build.json").write_text('{"live": true}', encoding="utf-8")
    (season / "team_tendencies.parquet").write_bytes(b"live")
    sb = _season_build(2026, through_week=1)
    assert sb.meta["through_week"] == 1 and sb.enriched["week"].max() == 1
    out = B.write_season(paths, sb)
    assert out == season / "through_week01"
    assert (out / "build.json").exists()
    assert all((out / f"{n}.parquet").exists() for n in B.FILES)
    assert (season / "build.json").read_text(encoding="utf-8") == '{"live": true}'
    assert (season / "team_tendencies.parquet").read_bytes() == b"live"
    assert sorted(p.name for p in season.iterdir()) == [
        "build.json", "team_tendencies.parquet", "through_week01",
    ]  # fmt: skip


# --- 6. typed empty tables ---------------------------------------------------------------------
def test_empty_tendencies_and_league_are_typed() -> None:
    empty_tg = B.team_game_sums(pl.DataFrame(schema={"season": pl.Int32}), 2026)
    idx = pl.DataFrame(
        schema={"team": pl.String, "game_id": pl.String, "week": pl.Int32, "game_no": pl.Int32}
    )
    for tend in (
        B.tendencies(empty_tg, idx, [], None, season=2026),
        B.tendencies(empty_tg, idx, [1, 2], None, season=2026),
        B.tendencies(empty_tg, idx, [1], empty_tg, season=2026),
    ):
        assert tend.height == 0
        assert tend.schema == pl.Schema(B.TEND_SCHEMA)
    league = B.league_table(pl.DataFrame(schema=B.TEND_SCHEMA))
    assert league.height == 0 and league.schema == pl.Schema(B.LEAGUE_SCHEMA)


def test_built_tables_match_the_typed_schemas() -> None:
    sb = _season_build()
    assert sb.tend.height > 0 and sb.league.height > 0
    assert sb.tend.schema == pl.Schema(B.TEND_SCHEMA)
    assert sb.league.schema == pl.Schema(B.LEAGUE_SCHEMA)


def test_a_season_with_no_games_writes_typed_files(tmp_path: Path) -> None:
    plays = _plays(2026, SCHED)
    e = B.enrich_plays(plays, pl.DataFrame(), pl.DataFrame())
    inp = _inputs(plays, _games(2026, SCHED))
    sb = B.build_season(inp, e, 2027)  # nothing scheduled, nothing played
    assert sb.weeks == [] and sb.tend.height == 0
    out = B.write_season(DataPaths(tmp_path), sb)
    assert pl.read_parquet(out / "team_tendencies.parquet").schema == pl.Schema(B.TEND_SCHEMA)
    assert pl.read_parquet(out / "league_tendencies.parquet").schema == pl.Schema(B.LEAGUE_SCHEMA)


# --- 6b. the W&B coverage guard (no real W&B) --------------------------------------------------
class _FakeRun:
    def __init__(self) -> None:
        self.logged: dict = {}
        self.summary: dict = {}
        self.exit_code: int | None = None
        self.url = "https://wandb.invalid/run"

    def log(self, d: dict) -> None:
        self.logged.update(d)

    def finish(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code


@pytest.fixture
def fake_wandb(monkeypatch):
    from nflengine import tracking

    run = _FakeRun()
    fake = types.SimpleNamespace(
        Table=lambda dataframe=None, **k: ("table", dataframe),
        plot=types.SimpleNamespace(line_series=lambda **k: ("line_series", k)),
    )
    monkeypatch.setitem(sys.modules, "wandb", fake)
    monkeypatch.setattr(tracking, "init_run", lambda *a, **k: run)
    monkeypatch.setattr(tracking, "dataset_version", lambda *a, **k: {})
    monkeypatch.setattr(tracking, "git_commit", lambda *a, **k: "abc")
    return run


def test_log_wandb_with_no_curated_games(fake_wandb, tmp_path: Path) -> None:
    plays = _plays(2026, SCHED)
    e = B.enrich_plays(plays, pl.DataFrame(), pl.DataFrame())
    sb = B.build_season(_inputs(plays, _games(2026, SCHED, done=False)), e.clear(), 2026)
    assert sb.meta["coverage"]["by_week"] == []
    url = B.log_wandb([sb], 2026, None, None, DataPaths(tmp_path))
    assert url == fake_wandb.url and fake_wandb.exit_code == 0
    assert "ftn/coverage_by_week" not in fake_wandb.logged
    assert "build/seasons" in fake_wandb.logged


def test_log_wandb_logs_coverage_when_present(fake_wandb, tmp_path: Path) -> None:
    sb = _season_build()
    slim = replace(sb, enriched=sb.enriched.clear(), tend=sb.tend.clear())
    B.log_wandb([slim], 2026, None, "agent", DataPaths(tmp_path))
    assert fake_wandb.exit_code == 0
    kind, df = fake_wandb.logged["ftn/coverage_by_week"]
    assert kind == "table" and df["week"].tolist() == [1, 2]
    assert fake_wandb.logged["s2026/plays"] == sb.enriched.height
