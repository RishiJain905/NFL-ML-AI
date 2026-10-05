"""P08 wiring in the weekly `player` step: the fail-soft team fit and the consistency layer
(only unstarted games touched, atomic rewrite, consistency.json), and the run summary's
consistency section."""

import datetime as dt
import json
from pathlib import Path

import polars as pl
import pytest

import nflengine.weekly as W
from nflengine.models.player_schema import conform
from nflengine.ops import summary as S
from nflengine.settings import AppConfig

NOW = dt.datetime.now(dt.UTC).replace(microsecond=0)


def _rows(game: str, kick: dt.datetime, rec_p50: float, tgt_p50: float) -> list[dict]:
    base = {
        "season": 2026,
        "week": 5,
        "game_id": game,
        "kickoff_utc": kick,
        "created_at": NOW - dt.timedelta(hours=1),
        "player_id": "wr1",
        "player": "A Receiver",
        "team": "AAA",
        "opponent": "BBB",
        "group": "WR/TE",
        "kind": "count",
        "baseline": 4.0,
    }
    return [
        {**base, "target": "receptions", "p10": 2.0, "p50": rec_p50, "p90": 8.0, "mean": rec_p50},
        {**base, "target": "targets", "p10": 3.0, "p50": tgt_p50, "p90": 9.0, "mean": tgt_p50},
    ]


def _players() -> pl.DataFrame:
    future = NOW + dt.timedelta(days=2)
    past = NOW - dt.timedelta(days=1)
    rows = _rows("g_future", future, 7.0, 6.0) + _rows("g_started", past, 7.0, 6.0)
    return conform(pl.DataFrame(rows))


@pytest.fixture
def cfg(monkeypatch):
    def set_cfg(**kw):
        c = AppConfig(consistency={"receptions": True, "rec_yds_anchor": None, **kw})
        monkeypatch.setattr("nflengine.settings.get_config", lambda: c)

    set_cfg()
    return set_cfg


def test_consistency_adjusts_only_unstarted_games_and_records(tmp_path, cfg):
    path = tmp_path / "predictions_players.parquet"
    players = _players()
    players.write_parquet(path)
    notes: list[str] = []
    out = W._consistency(W.WeeklyOptions(2026, 5), players, None, path, print, notes)
    rec = out.filter(pl.col("target") == "receptions")
    fut = rec.filter(pl.col("game_id") == "g_future").row(0, named=True)
    old = rec.filter(pl.col("game_id") == "g_started").row(0, named=True)
    assert fut["p50"] == 6.0  # clamped to targets
    assert old["p50"] == 7.0  # a started game's saved row is never touched
    assert out.height == players.height and out.columns == players.columns
    saved = pl.read_parquet(path)
    assert (
        saved.filter((pl.col("target") == "receptions") & (pl.col("game_id") == "g_future"))[
            "p50"
        ].item()
        == 6.0
    )
    rec_json = json.loads((tmp_path / S.CONSISTENCY_FILE).read_text())
    assert rec_json["adjusted_rows"] == 1
    assert "inconsistency/rec_gt_tgt_pairs" in rec_json["summary"]
    assert notes and notes[0].startswith("consistency: 1 rows adjusted")


def test_consistency_leaves_the_file_alone_when_nothing_changes(tmp_path, cfg):
    cfg(receptions=False)
    path = tmp_path / "predictions_players.parquet"
    players = _players()
    players.write_parquet(path)
    before = path.stat().st_mtime_ns
    out = W._consistency(W.WeeklyOptions(2026, 5), players, None, path, print, [])
    assert out.equals(players)
    assert path.stat().st_mtime_ns == before
    assert json.loads((tmp_path / S.CONSISTENCY_FILE).read_text())["adjusted_rows"] == 0


def test_consistency_is_fail_soft(tmp_path, cfg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret-looking text that must not be echoed")

    monkeypatch.setattr("nflengine.models.consistency.apply_consistency", boom)
    path = tmp_path / "predictions_players.parquet"
    players = _players()
    notes: list[str] = []
    out = W._consistency(W.WeeklyOptions(2026, 5), players, None, path, print, notes)
    assert out.equals(players)
    assert notes == ["consistency skipped (RuntimeError)"]


def test_team_fit_skips_without_shipped_targets_and_never_raises(monkeypatch):
    monkeypatch.setattr("nflengine.models.team_model.live_team_targets", lambda *a, **k: ())
    notes: list[str] = []
    assert W._team_fit(W.WeeklyOptions(2026, 5), print, notes) is None and notes == []

    monkeypatch.setattr("nflengine.models.team_model.live_team_targets", lambda *a, **k: ("x",))

    def boom(*a, **k):
        raise ValueError("nope")

    monkeypatch.setattr("nflengine.models.team_runs.run_train", boom)
    monkeypatch.setattr("nflengine.models.team_runs.score_weeks", lambda *a, **k: None)
    assert W._team_fit(W.WeeklyOptions(2026, 5), print, notes) is None
    assert notes == ["team fit skipped (ValueError)"]


def test_run_summary_reads_consistency_and_team_versions(tmp_path):
    (tmp_path / S.CONSISTENCY_FILE).write_text(
        json.dumps({"adjusted_rows": 2, "summary": {"inconsistency/gap_recv_qb_median_abs": 0.07}})
    )
    pl.DataFrame({"model_version": ["team-model-v1:2026-w05"]}).write_parquet(
        tmp_path / "predictions_teams.parquet"
    )
    assert S._consistency(tmp_path)["adjusted_rows"] == 2
    assert S._model_versions(tmp_path)["team"] == ["team-model-v1:2026-w05"]
    (tmp_path / S.CONSISTENCY_FILE).write_text("{not json")
    assert S._consistency(tmp_path) is None


def test_player_report_card_ignores_team_stat_rows():
    from nflengine.digest.players import _mode_rows, week_improvement
    from nflengine.models.player_schema import SCOREBOARD_SCHEMA

    def row(target: str, group: str, mae_m: float, mae_b: float) -> dict:
        return {
            "season": 2026,
            "week": 4,
            "target": target,
            "position_group": group,
            "target_label": target,
            "n_scored": 400,
            "n_not_played": 0,
            "mae_model": mae_m,
            "mae_baseline": mae_b,
            "improvement_pct": 100 * (mae_b - mae_m) / mae_b,
            "coverage_80": 0.8,
            "mode": "live",
        }

    sb = pl.DataFrame(
        [row("rec_yds", "WR/TE", 16.0, 18.0), row("pass_yds", "TEAM", 50.0, 100.0)],
        schema_overrides={k: v for k, v in SCOREBOARD_SCHEMA.items() if k != "brier_model"},
    )
    assert _mode_rows(sb, "live")["position_group"].to_list() == ["WR/TE"]
    # the scorecard's player number is about players only (the team row would pull it to 30%)
    assert week_improvement(sb, 2026, 4, "live") == pytest.approx(100 * 2 / 18, rel=1e-6)


def test_placeholder_uses_full_names_when_a_last_name_is_shared_anywhere(monkeypatch):
    import nflengine.digest.llm.placeholder as PH

    payload = {
        "players_to_watch": [
            {"player_id": "a", "player": "Chris Jones"},
            {"player_id": "b", "player": "Patrick Mahomes"},
        ],
        "under_the_hood": [],
    }
    # alone, "Jones" is unique among the writer's own players ...
    monkeypatch.setattr(PH, "_payload_players", lambda p: set())
    assert PH._short_names(payload) == {"a": "Jones", "b": "Mahomes"}
    # ... but the entity check also knows Mac Jones from the game table (2025 w13 failure)
    monkeypatch.setattr(PH, "_payload_players", lambda p: {"Mac Jones", "Patrick Mahomes"})
    assert PH._short_names(payload) == {"a": "Chris Jones", "b": "Mahomes"}


def test_scorecard_player_number_stays_on_the_p06_targets():
    from nflengine.digest.players import week_improvement
    from nflengine.models.player_schema import SCOREBOARD_SCHEMA

    def row(target: str, group: str, imp: float, n: int) -> dict:
        return {
            "season": 2026,
            "week": 5,
            "target": target,
            "position_group": group,
            "n_scored": n,
            "improvement_pct": imp,
            "mode": "live",
        }

    sb = pl.DataFrame(
        [row("rec_yds", "WR/TE", 9.0, 100), row("cov_yds", "CB/S", -50.0, 1000)],
        schema_overrides={k: SCOREBOARD_SCHEMA[k] for k in ("season", "week", "n_scored")},
    )
    assert week_improvement(sb, 2026, 5, "live") == pytest.approx(9.0)


def test_one_bad_query_row_never_sinks_the_graph_candidates(monkeypatch):
    import nflengine.graph.insights as I

    def boom(row, season):
        raise TypeError("unsupported format string passed to NoneType.__format__")

    monkeypatch.setitem(I.CONVERTERS, "q_test_bad", boom)
    errors: dict[str, str] = {}
    out = I.candidates({"q_test_bad": [{"x": None}, {"x": 1}]}, 2026, errors=errors)
    assert out == []
    assert errors == {"q_test_bad": "TypeError"}  # the type only, never the message text


# ---- Sol review fixes (P08) ---------------------------------------------------------------------


def test_file_lock_waits_and_never_revokes_a_live_holder(tmp_path):
    from nflengine.ops.lock import file_lock

    target = tmp_path / "scoreboard.parquet"
    with file_lock(target):
        with pytest.raises(TimeoutError), file_lock(target, timeout=0.2):
            pass  # a second holder can't get in, and the first keeps its lock
        assert (tmp_path / "scoreboard.parquet.lock").exists()
    with file_lock(target, timeout=0.2):  # released on exit: free again
        pass


def test_atomic_parquet_write_keeps_the_old_file_on_failure(tmp_path, monkeypatch):
    from nflengine.ops.lock import write_parquet_atomic

    path = tmp_path / "predictions_teams.parquet"
    write_parquet_atomic(pl.DataFrame({"x": [1, 2]}), path)
    good = pl.DataFrame({"x": [3]})

    def boom(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", boom)
    with pytest.raises(OSError):
        write_parquet_atomic(good, path)
    monkeypatch.undo()
    assert pl.read_parquet(path)["x"].to_list() == [1, 2]  # the saved rows survive
    assert [p.name for p in tmp_path.iterdir()] == ["predictions_teams.parquet"]  # no tmp left


def test_upsert_scoreboard_is_locked_and_atomic(tmp_path):
    from nflengine.models.player_runs import upsert_scoreboard
    from nflengine.models.player_schema import SCOREBOARD_SCHEMA

    row = {c: None for c in SCOREBOARD_SCHEMA} | {
        "season": 2026, "week": 1, "target": "td", "position_group": "RB", "mode": "live",
        "n_scored": 10, "brier_model": 0.15, "brier_baseline": 0.17,
    }  # fmt: skip
    path = tmp_path / "accuracy_scoreboard.parquet"
    out = upsert_scoreboard(path, pl.DataFrame([row], schema=SCOREBOARD_SCHEMA))
    out = upsert_scoreboard(path, pl.DataFrame([row | {"week": 2}], schema=SCOREBOARD_SCHEMA))
    assert out.height == 2 and pl.read_parquet(path).height == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "accuracy_scoreboard.parquet",
        "accuracy_scoreboard.parquet.lock",
    ]


def test_consistency_record_failure_keeps_the_committed_table(tmp_path, cfg, monkeypatch):
    path = tmp_path / "predictions_players.parquet"
    players = _players()
    players.write_parquet(path)

    def boom(*a, **k):
        raise PermissionError("read-only")

    monkeypatch.setattr(W.json, "dumps", boom)  # the record can't be written
    notes: list[str] = []
    out = W._consistency(W.WeeklyOptions(2026, 5), players, None, path, print, notes)
    monkeypatch.undo()
    fut = out.filter((pl.col("target") == "receptions") & (pl.col("game_id") == "g_future"))
    assert fut["p50"].item() == 6.0  # the clamp committed to disk is what the step returns
    assert pl.read_parquet(path).equals(out)
    assert notes == ["consistency: 1 rows adjusted (record not written: PermissionError)"]


def test_fitted_params_record_everything_outside_the_boosters():
    from types import SimpleNamespace

    from nflengine.models.player_model import Calibrator
    from nflengine.models.player_runs import fitted_params

    fit = SimpleNamespace(
        shift=1.5, dispersion=8.7, tail=0.125, calibrator=Calibrator("platt", 0.44, -0.1, n=4000)
    )
    got = fitted_params(fit)
    assert got == {
        "shift": 1.5,
        "dispersion": 8.7,
        "tail": 0.125,
        "calibrator": {"kind": "platt", "slope": 0.44, "intercept": -0.1, "n": 4000},
    }
    assert fitted_params(SimpleNamespace(shift=0, dispersion=1, tail=0.1))["calibrator"] is None


def test_dashboard_player_all_excludes_team_rows():
    from nflengine.ops import dashboard, drift

    rows = []
    for w in (1, 2):
        rows.append(
            {
                "season": 2026,
                "week": w,
                "target": "rec_yds",
                "position_group": "WR/TE",
                "n_scored": 100,
                "mae_model": 9.0,
                "mae_baseline": 10.0,
                "mode": "backtest",
            }
        )
        rows.append(
            {
                "season": 2026,
                "week": w,
                "target": "pass_yds",
                "position_group": "TEAM",
                "n_scored": 100,
                "mae_model": 50.0,
                "mae_baseline": 100.0,
                "mode": "backtest",
            }
        )
    sb = pl.DataFrame(rows)
    data = dashboard.build_dashboard_data(
        2026, 3, run_root=Path("."), scorecard=pl.DataFrame(), scoreboard=sb,
        history=pl.DataFrame(), cfg=drift.DEFAULTS,
    )  # fmt: skip
    assert data.summary["player_cum_improvement_all"] == pytest.approx(10.0)  # players only
