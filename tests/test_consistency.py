"""The consistency layer (P08): receptions <= targets, receivers vs QB vs team passing yards,
the inconsistency summary, the optional team-level adjustment and the adjust-or-log rule.

Frames are hand-built player predictions (the columns `consistency` reads from the player
`PRED_SCHEMA`) so every number can be checked by hand.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from nflengine.models import consistency as C

GAME = "2024_01_AAA_BBB"


def row(
    target: str,
    group: str,
    player: str,
    p50: float,
    *,
    team: str = "BBB",
    game: str = GAME,
    p10: float | None = None,
    p90: float | None = None,
    mean: float | None = None,
    actual: float | None = 10.0,
    baseline: float = 5.0,
    season: int = 2024,
    kind: str | None = None,
) -> dict:
    kind = kind or ("count" if target in ("receptions", "targets") else "amount")
    p10 = p50 * 0.4 if p10 is None else p10
    p90 = p50 * 2.0 if p90 is None else p90
    out = p50 - baseline
    return {
        "season": season,
        "week": 1,
        "game_id": game,
        "team": team,
        "opponent": "AAA" if team == "BBB" else "BBB",
        "player_id": player,
        "group": group,
        "target": target,
        "kind": kind,
        "p10": p10,
        "p50": p50,
        "p90": p90,
        "mean": (p50 if kind == "amount" else p50) if mean is None else mean,
        "actual": actual,
        "played": actual is not None,
        "baseline": baseline,
        "outperformance": out,
        "outperf_z": out / 4.0,
    }


def frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def swanson(p10: float, p50: float, p90: float) -> float:
    return 0.3 * p10 + 0.4 * p50 + 0.3 * p90


# ---- the mean estimate -----------------------------------------------------------------------


def test_mean_estimate_is_swanson_for_amounts_and_the_mean_for_counts() -> None:
    f = frame(
        [
            row("rec_yds", "WR/TE", "a", 20.0, p10=4.0, p90=60.0),
            row("targets", "WR/TE", "a", 5.0, p10=2.0, p90=9.0, mean=5.4),
        ]
    )
    est = f.select(C.mean_estimate().alias("m"))["m"].to_list()
    assert est[0] == pytest.approx(swanson(4.0, 20.0, 60.0)) and est[0] > 20.0  # skewed: > P50
    assert est[1] == 5.4


# ---- receptions <= targets ---------------------------------------------------------------------


def rec_tgt(rec: dict, tgt: dict, player: str = "w1") -> list[dict]:
    r = row("receptions", "WR/TE", player, rec.pop("p50"), **rec)
    t = row("targets", "WR/TE", player, tgt.pop("p50"), **tgt)
    return [r, t]


def test_receptions_are_clamped_to_targets_level_by_level() -> None:
    rows = rec_tgt(
        {"p10": 2.0, "p50": 5.0, "p90": 9.0, "mean": 5.2},
        {"p10": 3.0, "p50": 4.0, "p90": 8.0, "mean": 4.9},
    )
    rows += rec_tgt(  # consistent pair
        {"p10": 1.0, "p50": 3.0, "p90": 6.0, "mean": 3.1},
        {"p10": 2.0, "p50": 5.0, "p90": 9.0, "mean": 5.0},
        player="w2",
    )
    f = frame(rows)
    out, st = C.reconcile_receptions(f)
    assert out.shape == f.shape and out.columns == f.columns
    got = out.filter((pl.col("target") == "receptions") & (pl.col("player_id") == "w1")).row(
        0, named=True
    )
    assert (got["p10"], got["p50"], got["p90"], got["mean"]) == (2.0, 4.0, 8.0, 4.9)
    # only receptions of the violating player changed; every other row is identical
    keep = ~((pl.col("target") == "receptions") & (pl.col("player_id") == "w1"))
    assert out.filter(keep).equals(f.filter(keep))
    assert st["pairs"] == 2 and st["adjusted_rows"] == 1
    assert (st["viol_p50"], st["viol_mean"], st["viol_p10"], st["viol_p90"]) == (1, 1, 0, 1)
    assert st["share_mean"] == 0.5 and st["median_excess_p50"] == pytest.approx(1.0)
    # the derived columns follow the new mean; the scale (4.0) is recovered from the frame
    assert got["outperformance"] == pytest.approx(4.9 - 5.0)
    assert got["outperf_z"] == pytest.approx((4.9 - 5.0) / 4.0)


def test_log_only_reports_without_touching_the_frame() -> None:
    f = frame(rec_tgt({"p50": 6.0, "mean": 6.0}, {"p50": 4.0, "mean": 4.0}))
    out, st = C.reconcile_receptions(f, adjust=False)
    assert out.equals(f) and st["viol_p50"] == 1 and st["adjusted_rows"] == 0


def test_receptions_without_targets_rows_are_left_alone() -> None:
    f = frame([row("receptions", "WR/TE", "w1", 5.0), row("rec_yds", "WR/TE", "w1", 30.0)])
    out, st = C.reconcile_receptions(f)
    assert out.equals(f) and st == {"pairs": 0.0, "adjusted_rows": 0.0}


def test_nothing_to_adjust_returns_the_same_frame() -> None:
    f = frame(rec_tgt({"p50": 3.0, "mean": 3.0}, {"p50": 5.0, "mean": 5.0}))
    out, st = C.reconcile_receptions(f)
    assert out.equals(f) and st["adjusted_rows"] == 0.0 and st["share_p50"] == 0.0


# ---- receivers vs QB vs team --------------------------------------------------------------------


def passing_game(
    wr: tuple[float, float, float] = (60.0, 40.0, 20.0),
    rb: tuple[float, float] = (30.0, 12.0),
    qb: float = 250.0,
    game: str = GAME,
    team: str = "BBB",
    actuals: tuple[float, ...] = (70.0, 35.0, 30.0, 15.0, 260.0),
) -> list[dict]:
    kw = {"game": game, "team": team}
    a_wr1, a_wr2, a_rb_sc, a_rb_ru, a_qb = actuals
    return [
        row("rec_yds", "WR/TE", f"{team}1", wr[0], actual=a_wr1, **kw),
        row("rec_yds", "WR/TE", f"{team}2", wr[1], actual=a_wr2, **kw),
        row("rec_yds", "WR/TE", f"{team}3", wr[2], actual=0.0, **kw),
        row("scrim_yds", "RB", f"{team}rb", rb[0] + rb[1], actual=a_rb_sc, **kw),
        row("rush_yds", "RB", f"{team}rb", rb[1], actual=a_rb_ru, **kw),
        row("pass_yds", "QB", f"{team}qb", qb, actual=a_qb, **kw),
    ]


def team_frame(
    team_p50: float, actual: float | None, game: str = GAME, team: str = "BBB"
) -> pl.DataFrame:
    r = row("pass_yds", "TEAM", "", team_p50, actual=actual, game=game, team=team)
    r.pop("player_id")
    return frame([r])


def test_receiving_table_sums_and_gaps() -> None:
    p = frame(passing_game())
    t = team_frame(255.0, 262.0)
    tab = C.team_receiving_table(p, t)
    assert tab.height == 1
    r = tab.row(0, named=True)
    # WR/TE medians and expected values (Swanson, p10 = 0.4 p50, p90 = 2 p50 -> 1.12 * p50)
    assert r["wrte_p50"] == pytest.approx(120.0) and r["wrte_mean"] == pytest.approx(1.12 * 120.0)
    assert r["rb_p50"] == pytest.approx(30.0) and r["rb_mean"] == pytest.approx(1.12 * 30.0)
    assert r["recv_p50"] == pytest.approx(150.0) and r["recv_mean"] == pytest.approx(168.0)
    assert r["qb_p50"] == 250.0 and r["qb_mean"] == pytest.approx(280.0)
    assert r["team_p50"] == 255.0 and r["team_mean"] == pytest.approx(285.6)
    # relative gaps (a - b) / b, medians are biased low, means much less
    assert r["gap_recv_qb_p50"] == pytest.approx((150 - 250) / 250)
    assert r["gap_recv_qb"] == pytest.approx((168 - 280) / 280)
    assert r["gap_qb_team"] == pytest.approx((280 - 285.6) / 285.6)
    assert r["gap_recv_team"] == pytest.approx((168 - 285.6) / 285.6)
    # actuals: receivers 70 + 35 + 0 + (30 - 15) = 120, the QB threw for 260, the team for 262
    assert r["recv_act"] == pytest.approx(120.0) and r["qb_act"] == 260.0
    assert r["gap_act_recv_qb"] == pytest.approx((120 - 260) / 260)
    assert r["gap_act_qb_team"] == pytest.approx((260 - 262) / 262)
    assert (r["season"], r["opponent"]) == (2024, "AAA")


def test_rb_receiving_is_floored_at_zero_per_back() -> None:
    rows = passing_game(rb=(0.0, 0.0))
    rows[3] = row("scrim_yds", "RB", "BBBrb", 8.0, actual=5.0, game=GAME, team="BBB")
    rows[4] = row("rush_yds", "RB", "BBBrb", 11.0, actual=3.0, game=GAME, team="BBB")
    tab = C.team_receiving_table(frame(rows), None)
    assert tab["rb_p50"][0] == 0.0 and tab["rb_mean"][0] == 0.0  # 8 - 11 < 0
    assert tab["rb_act"][0] == pytest.approx(2.0)  # actuals are not floored


def test_receiving_table_without_team_rows_has_null_team_columns() -> None:
    tab = C.team_receiving_table(frame(passing_game()), None)
    assert tab["team_mean"].null_count() == 1 and tab["gap_recv_team"].null_count() == 1
    assert tab["gap_recv_qb"][0] is not None


def test_gap_stats_and_summary_keys() -> None:
    rows = []
    for i, scale in enumerate((1.0, 0.7, 1.4)):
        wr = tuple(x * scale for x in (60.0, 40.0, 20.0))
        rows += passing_game(wr=wr, game=f"g{i}", actuals=(70.0, 35.0, 30.0, 15.0, 120.0))
    tab = C.team_receiving_table(frame(rows), None)
    st = C.gap_stats(tab)
    assert st["inconsistency/team_games"] == 3.0
    gaps = tab["gap_recv_qb"].to_list()
    assert st["inconsistency/gap_recv_qb_median_abs"] == pytest.approx(np.median(np.abs(gaps)))
    assert st["inconsistency/gap_recv_qb_bias"] == pytest.approx(np.mean(gaps))
    assert st["inconsistency/gap_recv_qb_share_gt10"] == pytest.approx(np.mean(np.abs(gaps) > 0.10))
    assert "inconsistency/gap_recv_team_median_abs" not in st  # no team rows: nothing to report
    summ = C.inconsistency_summary(
        tab,
        {
            "pairs": 10.0,
            "adjusted_rows": 2.0,
            "share_mean": 0.2,
            "median_excess_p50": 1.0,
            "viol_p50": 3.0,
        },
    )
    assert summ["inconsistency/rec_gt_tgt_share_mean"] == 0.2
    assert summ["inconsistency/rec_gt_tgt_pairs"] == 10.0
    assert summ["inconsistency/rec_gt_tgt_adjusted_rows"] == 2.0
    assert "inconsistency/rec_gt_tgt_viol_p50" not in summ  # counts of violations stay internal
    assert all(isinstance(v, float) for v in summ.values())


# ---- the team-level adjustment -----------------------------------------------------------------


def wrte_mean(p: pl.DataFrame) -> float:
    return float(p.filter(pl.col("target") == "rec_yds").select(C.mean_estimate()).sum().item())


def test_reconcile_rec_yds_moves_the_receivers_total_to_the_anchor() -> None:
    p = frame(passing_game())
    tab = C.team_receiving_table(p, team_frame(255.0, 262.0))
    team_mean, rb_mean = tab["team_mean"][0], tab["rb_mean"][0]
    # full strength, no clipping: the receivers' expected total meets the anchor's
    out, st = C.reconcile_rec_yds(p, tab, anchor="team", strength=1.0, clip=(0.1, 10.0))
    assert wrte_mean(out) + rb_mean == pytest.approx(team_mean)
    assert st["adjusted_rows"] == 3
    # half strength: half the distance
    half, _ = C.reconcile_rec_yds(p, tab, anchor="team", strength=0.5, clip=(0.1, 10.0))
    start = wrte_mean(p) + rb_mean
    assert wrte_mean(half) + rb_mean == pytest.approx((start + team_mean) / 2)
    # only WR/TE rec_yds rows changed
    other = pl.col("target") != "rec_yds"
    assert out.filter(other).equals(p.filter(other))
    assert out.shape == p.shape
    # the whole distribution is scaled together, so the quantiles keep their order
    assert (out["p10"] <= out["p50"]).all() and (out["p50"] <= out["p90"]).all()
    # the watch list ranks on outperf_z: it must follow the new P50 (scale 4.0 in these frames)
    rec = out.filter(pl.col("target") == "rec_yds")
    assert rec["outperformance"].to_list() == pytest.approx(
        (rec["p50"] - rec["baseline"]).to_list()
    )
    assert rec["outperf_z"].to_list() == pytest.approx(
        ((rec["p50"] - rec["baseline"]) / 4.0).to_list()
    )
    assert not rec["outperf_z"].equals(p.filter(pl.col("target") == "rec_yds")["outperf_z"])


def test_reconcile_rec_yds_clips_the_scale_and_skips_games_without_an_anchor() -> None:
    p = frame(passing_game(qb=900.0) + passing_game(game="g2", team="CCC"))
    tab = C.team_receiving_table(p, None)
    out, st = C.reconcile_rec_yds(p, tab, anchor="qb", strength=1.0)
    scaled = out.filter((pl.col("game_id") == GAME) & (pl.col("target") == "rec_yds"))
    orig = p.filter((pl.col("game_id") == GAME) & (pl.col("target") == "rec_yds"))
    assert (scaled["p50"] / orig["p50"]).to_list() == pytest.approx([C.K_CLIP[1]] * 3)
    none, st2 = C.reconcile_rec_yds(p, tab, anchor="team")  # no team rows: nothing to anchor on
    assert none.equals(p) and st2["adjusted_rows"] == 0
    with pytest.raises(ValueError):
        C.reconcile_rec_yds(p, tab, anchor="coach")


def test_adjustment_effect_and_the_adjust_or_log_rule() -> None:
    """Receivers 40% under the QB total: scaling them up brings every WR/TE closer."""
    rows = []
    for season in (2022, 2023, 2024, 2025):
        for g in range(6):
            gid = f"{season}_{g}"
            rows += [
                {**r, "season": season}
                for r in passing_game(
                    wr=(36.0, 24.0, 12.0),
                    rb=(18.0, 7.0),
                    qb=250.0,
                    game=gid,
                    actuals=(100.0, 66.0, 33.0, 19.0, 250.0),
                )
            ]
    p = frame(rows)
    p = p.with_columns(
        pl.when(pl.col("player_id") == "BBB3")
        .then(33.0)
        .when(pl.col("player_id") == "BBB1")
        .then(100.0)
        .when(pl.col("player_id") == "BBB2")
        .then(66.0)
        .otherwise(pl.col("actual"))
        .alias("actual")
    )
    tab = C.team_receiving_table(p, None)
    eff = C.adjustment_effect(p, tab, anchors=("qb",), strengths=(0.5, 1.0))
    assert eff["anchor"].to_list() == ["qb", "qb"]
    assert (eff["mae_change_pct"] < 0).all()
    assert eff["seasons_improved"].to_list() == [4.0, 4.0]
    assert eff["mae_after"][1] < eff["mae_after"][0] < eff["mae_before"][0]
    verdict = C.decide(eff, min_seasons=3, max_cov_shift=1.0)
    assert verdict["adjust"] and verdict["best"]["anchor"] == "qb"
    assert verdict["best"]["strength"] == 1.0  # the gain is linear here: only 1.0 keeps 90%

    worse = eff.with_columns((-pl.col("mae_change_pct")).alias("mae_change_pct"))
    no = C.decide(worse, min_seasons=3, max_cov_shift=1.0)
    assert not no["adjust"] and "no configuration" in no["reason"] and "best" in no
    assert not C.decide(pl.DataFrame())["adjust"]
    # a coverage shift beyond the limit also keeps it log-only
    assert not C.decide(eff.with_columns(pl.lit(0.5).alias("coverage_after")), min_seasons=3)[
        "adjust"
    ]


def test_decide_prefers_the_team_anchor_then_the_lightest_strength() -> None:
    def e(anchor, strength, change):
        return {
            "anchor": anchor,
            "strength": strength,
            "mae_before": 1.0,
            "mae_after": 1 + change / 100,
            "mae_change_pct": change,
            "seasons_improved": 7.0,
            "seasons": 7.0,
            "coverage_before": 0.8,
            "coverage_after": 0.8,
            "rows_adjusted": 10.0,
        }

    eff = pl.DataFrame(
        [
            e("qb", 1.0, -0.9),
            e("team", 0.25, -0.30),
            e("team", 0.5, -0.45),
            e("team", 1.0, -0.47),
        ]
    )
    best = C.decide(eff)["best"]
    assert (best["anchor"], best["strength"]) == ("team", 0.5)  # 0.45 >= 90% of the best 0.47


def test_apply_consistency_is_log_only_for_team_yards_by_default() -> None:
    rows = passing_game() + rec_tgt({"p50": 6.0, "mean": 6.0}, {"p50": 4.0, "mean": 4.0})
    p = frame(rows)
    t = team_frame(255.0, 262.0)
    out, summ = C.apply_consistency(p, t)
    assert out.filter(pl.col("target") == "rec_yds").equals(p.filter(pl.col("target") == "rec_yds"))
    rec = out.filter(pl.col("target") == "receptions")
    assert rec["p50"][0] == 4.0 and summ["inconsistency/rec_gt_tgt_adjusted_rows"] == 1.0
    assert "inconsistency/gap_recv_team_median_abs" in summ
    adj, s2 = C.apply_consistency(p, t, rec_yds_anchor="team", strength=1.0)
    assert not adj.filter(pl.col("target") == "rec_yds").equals(
        p.filter(pl.col("target") == "rec_yds")
    )
    assert s2["inconsistency/rec_yds_adjusted_rows"] == 3.0
    # the summary describes the projections before any team-level adjustment
    assert {k: v for k, v in s2.items() if "rec_yds" not in k} == summ
    off, s3 = C.apply_consistency(p, t, receptions=False)
    assert off.filter(pl.col("target") == "receptions")["p50"][0] == 6.0
    assert s3["inconsistency/rec_gt_tgt_adjusted_rows"] == 0.0


def test_evaluate_runs_end_to_end_on_small_frames() -> None:
    rows = []
    for season in (2023, 2024):
        for g in range(3):
            rows += [{**r, "season": season} for r in passing_game(game=f"{season}_{g}")]
            rows += [
                {**r, "season": season, "game_id": f"{season}_{g}"}
                for r in rec_tgt(
                    {"p50": 5.0, "mean": 5.0, "actual": 3.0}, {"p50": 4.0, "mean": 4.0}, f"w{g}"
                )
            ]
    p = frame(rows)
    t = frame(
        [
            {**team_frame(255.0, 262.0).row(0, named=True), "game_id": f"{s}_{g}", "season": s}
            for s in (2023, 2024)
            for g in range(3)
        ]
    )
    res = C.evaluate(p, t)
    assert res["by_season"]["season"].to_list() == [2023, 2024]
    assert res["table"].height == 6
    pooled = res["pooled"]
    assert pooled["inconsistency/team_games"] == 6.0
    assert "inconsistency/after/gap_recv_team_median_abs" in pooled
    assert pooled["inconsistency/rec_gt_tgt_adjusted_rows"] == 6.0
    assert pooled["inconsistency/receptions_seasons_worse"] == 0.0
    assert pooled["inconsistency/receptions_seasons_improved"] == 2.0
    assert pooled["inconsistency/receptions_mae_change_pct"] < 0
    assert res["effects"].height == 6 and res["effect_by_season"].height == 2
    # scaling toward the anchor leaves a smaller gap than the projections had
    assert (
        pooled["inconsistency/after/gap_recv_team_median_abs"]
        < pooled["inconsistency/gap_recv_team_median_abs"]
    )


# ---- the evaluation run -------------------------------------------------------------------------


class FakeRun:
    def __init__(self):
        self.summary: dict = {}
        self.logged: list = []
        self.finished = False
        self.url = "https://example.invalid/consistency"

    def log(self, data):
        self.logged.append(data)

    def define_metric(self, *a, **k):
        pass

    def finish(self):
        self.finished = True


def write_backtests(root, team_root, seasons=(2023, 2024)) -> pl.DataFrame:
    rows, team_rows = [], []
    for season in seasons:
        for g in range(3):
            gid = f"{season}_{g}"
            rows += [{**r, "season": season} for r in passing_game(game=gid)]
            rows += [
                {**r, "season": season, "game_id": gid}
                for r in rec_tgt({"p50": 5.0, "mean": 5.0}, {"p50": 4.0, "mean": 4.0}, f"w{g}")
            ]
            t = team_frame(255.0, 262.0).row(0, named=True)
            team_rows.append({**t, "game_id": gid, "season": season})
    p = frame(rows)
    for key in C.P06_KEYS:
        target, group = key.rsplit("-", 1)
        grp = {"qb": "QB", "rb": "RB", "wrte": "WR/TE"}[group]
        d = root / key
        d.mkdir(parents=True)
        p.filter((pl.col("target") == target) & (pl.col("group") == grp)).write_parquet(
            d / "predictions_players.parquet"
        )
    d = team_root / "pass_yds-team"
    d.mkdir(parents=True)
    frame(team_rows).write_parquet(d / "predictions_teams.parquet")
    return p


@pytest.fixture
def backtests(tmp_path, monkeypatch):
    from nflengine import tracking
    from nflengine.paths import DataPaths

    root, team_root = tmp_path / "player", tmp_path / "team"
    write_backtests(root, team_root)
    runs: list[FakeRun] = []
    monkeypatch.setattr("nflengine.paths.ensure_data_root", lambda *a, **k: DataPaths(tmp_path))
    monkeypatch.setattr(tracking, "dataset_version", lambda *a: {"pbp_snapshot": None})
    monkeypatch.setattr(tracking, "git_commit", lambda: "test")

    def fake_init_run(*args, **kwargs):
        runs.append(FakeRun())
        runs[-1].init = (args, kwargs)
        return runs[-1]

    monkeypatch.setattr(tracking, "init_run", fake_init_run)
    return root, team_root, runs


def test_run_eval_saves_results_and_logs_one_run(backtests, tmp_path) -> None:
    import json

    root, team_root, runs = backtests
    save = tmp_path / "out"
    res = C.run_eval(
        root=root,
        team_root=team_root,
        seasons=(2023, 2024),
        launched_by="agent",
        log=lambda *_: None,
        save_dir=save,
    )
    assert (save / "team_games.parquet").exists() and (save / "effects.parquet").exists()
    summary = json.loads((save / "summary.json").read_text())
    assert summary["inconsistency/team_games"] == 6.0 and "decision" in summary
    assert res["url"] == runs[-1].url and runs[-1].finished
    args, kwargs = runs[-1].init
    assert args[:2] == ("track1-player", "eval") and kwargs["tags"] == ["p08", "consistency"]
    run = runs[-1]
    assert run.summary["inconsistency/gap_recv_qb_median_abs"] == pytest.approx(
        res["pooled"]["inconsistency/gap_recv_qb_median_abs"]
    )
    assert [d["cons/season"] for d in run.logged if "cons/season" in d] == [2023, 2024]
    final = run.logged[-1]
    assert {"by_season", "adjustment_effects", "adjustment_mae_change"} <= set(final)
    assert any(k.startswith("hist/") for k in final)


def test_run_eval_without_wandb_or_team_rows(backtests, tmp_path) -> None:
    root, team_root, runs = backtests
    (team_root / "pass_yds-team" / "predictions_teams.parquet").unlink()
    res = C.run_eval(
        root=root,
        team_root=team_root,
        seasons=(2023, 2024),
        log=lambda *_: None,
        use_wandb=False,
        save_dir=tmp_path / "o2",
    )
    assert res["url"] is None and runs == []
    assert (
        res["effects"].filter(pl.col("anchor") == "team").is_empty()
    )  # no team model, no team anchor
    assert not res["decision"]["adjust"] or res["decision"]["best"]["anchor"] == "qb"
