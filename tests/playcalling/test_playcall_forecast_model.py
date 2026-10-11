"""PC02: the forecast model (`playcalling/forecast.py`), the pure part.

A synthetic league (fake team codes, so the real coaching seed never matches) feeds
`observations` / `build_design` / `predict`. Three kinds of check:

- an independent plain-Python reference (`playcall_forecast_fixtures.Ref`) against the
  vectorised predictions, for every kind and target family (script, FTN, mean, two-metric);
- the properties the phase file asks for, each on a league small enough to compute by hand:
  as-of leakage, FTN availability, shrinkage limits, the opponent's sign, the game script,
  b1 / b2, a mid-season head-coach change;
- scoring, ranges and the ship rule on hand-made arrays.
"""

from __future__ import annotations

import ast
import datetime as dt
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import playcall_forecast_fixtures as X
import polars as pl
import pytest
from playcall_forecast_fixtures import P_SCRIPT, P

import nflengine.playcalling.build as B
import nflengine.playcalling.forecast as F
import nflengine.playcalling.labels as L

SEASON = 2024
TOL = {"rtol": 1e-9, "atol": 1e-9}


# --- fixtures -------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def league() -> X.League:
    return X.random_league(
        seed=7,
        changed=[
            (2023, "AAA", "offense", True),
            (2024, "BBB", "defense", True),
            (2024, "DDD", "offense", True),
        ],  # fmt: skip
        mid=[(2024, "CCC", 5), (2023, "EEE", 4)],
    )


def ref_for(lg: X.League, key: str, p: dict, **kw) -> X.Ref:
    t = F.get_target(key)
    changed = {
        (r["season"], r["team"]): r["changed"]
        for r in lg.changed.filter(pl.col("side") == t.side).iter_rows(named=True)
    }
    mid = {(r["season"], r["team"]): r["change_week"] for r in lg.midseason.iter_rows(named=True)}
    obs = X.ref_obs(t, lg.tg, lg.games, lg.curves)
    return X.Ref(obs, t, p, changed=changed, mid=mid, **kw)


def pbp_counts(rates: dict[str, float], n: int = 50, opp_eff: dict[str, float] | None = None):
    """k = rate * n exactly (rates chosen so it's whole); a defense's `opp_eff` shifts it."""
    opp_eff = opp_eff or {}

    def counts(key, season, week, team, opp):
        return round((rates.get(team, 0.2) + opp_eff.get(opp, 0.0)) * n), n

    return counts


DEEP = F.get_target("deep_shot_rate")
BLITZ = F.get_target("blitz_rate")
DROPBACK = F.get_target("dropback_rate")
PROE = F.get_target("proe")


def small_league(
    teams=X.TEAMS6, seasons=(SEASON,), weeks=4, targets=(DEEP,), counts=None, **game_kw
):
    sched = X.schedule(teams, seasons, weeks)
    games = X.game_rows(sched, **game_kw)
    return games, X.tg_frame(games, targets, counts or pbp_counts({}))


def row_of_game(d: F.Design, gid: str, team: str) -> int:
    r = d.rows.with_row_index("_i").filter((pl.col("game_id") == gid) & (pl.col("team") == team))
    assert r.height == 1
    return int(r["_i"][0])


# --- the targets, helpers -------------------------------------------------------------------------
def test_parse_seasons_forms() -> None:
    assert F.parse_seasons("2019-2023") == [2019, 2020, 2021, 2022, 2023]
    assert F.parse_seasons("2019,2021") == [2019, 2021]
    assert F.parse_seasons(" 2019-2020 , 2023,, ") == [2019, 2020, 2023]
    assert F.parse_seasons([2021, 2019, 2019]) == [2019, 2021]
    assert F.parse_seasons("") == []


def test_get_target_and_select_targets() -> None:
    assert F.get_target("blitz_rate") is BLITZ
    with pytest.raises(KeyError, match="unknown play-calling target 'nope'"):
        F.get_target("nope")
    assert [t.key for t in F.select_targets(None)] == list(F.TARGET_KEYS)
    assert [t.key for t in F.select_targets([])] == list(F.TARGET_KEYS)
    assert [t.key for t in F.select_targets(["proe", "motion_rate"])] == ["proe", "motion_rate"]


def test_registry_lines_up_with_the_pc00_metrics() -> None:
    names = {m.name: m for m in L.METRICS}
    assert len(set(F.TARGET_KEYS)) == len(F.TARGETS)
    for t in F.TARGETS:
        assert t.source in ("pbp", "ftn"), "participation is research only (D14)"
        for m in t.metrics:
            assert m in names, m
            met = names[m]
            assert met.source == t.source and met.unit == t.unit and not met.history_only
            assert t.situation in {s.name for s in met.situations()}, (t.key, t.situation)
        if t.source == "ftn":
            assert t.first_season == L.FTN_FIRST
            assert all(names[m].first_season == t.first_season for m in t.metrics)
        else:
            assert t.first_season == B.FIRST_SEASON
        assert t.lo < t.hi
    assert {t.key for t in F.TARGETS if t.side == "defense"} == {
        "blitz_rate", "rushers_avg", "heavy_box_rate"
    }  # fmt: skip
    assert {t.key for t in F.TARGETS if t.script} == {"proe", "dropback_rate"}
    # run location is charted by the home stats crew: those targets group by venue, not opponent
    assert {t.key for t in F.TARGETS if t.context == "venue"} == {"run_middle", "run_outside"}
    assert {t.context for t in F.TARGETS} == {"opponent", "venue"}
    assert F.get_target("run_outside").metrics == ("run_left_end", "run_right_end")
    assert F.get_target("rushers_avg").unit == "mean" and not F.get_target("rushers_avg").share


@pytest.mark.parametrize("module", ["forecast.py", "forecast_runs.py"])
def test_no_production_models_and_no_research_data_are_imported(module: str) -> None:
    """D107 / D14: the forecast never imports the production models, features or participation."""
    src = Path(F.__file__).with_name(module).read_text(encoding="utf-8")
    mods = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods += [f"{node.module}.{a.name}" for a in node.names]
    banned = ("nflengine.models", "nflengine.features", "nflengine.playcalling.participation")
    assert not [m for m in mods if m.startswith(banned)], mods


def test_forecast_settings_layering(monkeypatch) -> None:
    import nflengine.settings as settings

    before = {k: (dict(v) if isinstance(v, dict) else v) for k, v in F.DEFAULTS.items()}
    monkeypatch.setattr(
        settings,
        "get_config",
        lambda: SimpleNamespace(
            playcalling={"forecast": {"ref_seasons": 5, "ship": {"seasons_pbp": 4}}}
        ),
    )
    cfg = F.forecast_settings({"ship": {"seasons_ftn": 2}, "spread_bandwidth": 4.0})
    assert cfg["ref_seasons"] == 5 and cfg["spread_bandwidth"] == 4.0
    assert cfg["ship"] == {"seasons_pbp": 4, "seasons_ftn": 2}  # merged, not replaced
    assert cfg["pre_change_weight"] == F.DEFAULTS["pre_change_weight"]
    assert before == F.DEFAULTS  # no mutation of the defaults
    monkeypatch.setattr(settings, "get_config", lambda: SimpleNamespace(playcalling={}))
    assert F.forecast_settings() == before


def test_the_settings_file_has_every_forecast_key() -> None:
    cfg = F.forecast_settings()
    assert set(F.DEFAULTS) <= set(cfg)
    assert cfg["ship"]["seasons_pbp"] == 5 and cfg["ship"]["seasons_ftn"] == 3  # D130
    assert F.parse_seasons(cfg["report_seasons"])[0] == 2019


def test_parameter_maps_round_trip_and_start_inside_the_bounds() -> None:
    for name, spec in F.PARAMS.items():
        assert spec.name == name and spec.lo < spec.hi
        for s in spec.start:
            assert spec.lo <= s <= spec.hi, name
        for v in (0.1, 0.5, 0.9) if name.startswith("rho") else (3.0, 120.0):
            if name in ("lam", "b_spread", "b_total"):
                continue
            assert spec.to_model(spec.to_opt(v)) == pytest.approx(v)
    assert 0 < F.PARAMS["rho_same"].to_model(0.0) < 1  # a logistic: shrinkage in (0, 1)
    assert F.PARAMS["kappa"].to_model(0.0) == pytest.approx(1.0)  # log scale
    assert F.kind_params(DEEP, "b1") == () and F.kind_params(DEEP, "b2") == ()
    assert F.kind_params(DEEP, "b3") == F.TEAM_PARAMS
    assert F.kind_params(DEEP, "b4") == F.TEAM_PARAMS + F.OPP_PARAMS
    assert F.kind_params(DEEP, "model") == F.TEAM_PARAMS + F.OPP_PARAMS  # no script term
    assert F.kind_params(DROPBACK, "model") == (
        F.TEAM_PARAMS + F.OPP_PARAMS + F.SCRIPT_PARAMS
    )  # fmt: skip
    assert F.kind_params(PROE, "b4") == F.TEAM_PARAMS + F.OPP_PARAMS


# --- loaders and observations ---------------------------------------------------------------------
def test_load_team_games_reads_pc00_files_and_skips_unbuilt_seasons(tmp_path) -> None:
    games, tg = small_league(seasons=(2023, SEASON), targets=(DEEP, BLITZ))
    paths = X.write_tree(tmp_path, games, tg)
    # a decoy: another metric, another situation, in the same file
    f = paths.playcalling / str(SEASON) / "team_game_tendencies.parquet"
    base = pl.read_parquet(f)
    decoy = base.head(5).with_columns(pl.lit("shotgun_rate").alias("metric"))
    decoy2 = base.head(5).with_columns(pl.lit("two_minute").alias("situation"))
    pl.concat([base, decoy, decoy2]).write_parquet(f)

    got = F.load_team_games(paths, [2021, 2023, SEASON, SEASON])  # 2021 has no build
    assert set(got["season"].to_list()) == {2023, SEASON}
    assert set(got["metric"].unique()) == {"deep_shot_rate", "blitz_rate"}
    assert set(got["situation"].unique()) == {"all"}
    assert got.schema == pl.Schema(X.TG_SCHEMA)
    key = ["season", "week", "game_id", "team", "metric"]
    exp = tg.sort(key)
    got = got.sort(key)
    assert got.height == exp.height
    np.testing.assert_allclose(got["num"].to_numpy(), exp["num"].to_numpy(), atol=1e-9)
    assert got["den"].to_list() == exp["den"].to_list()

    only = F.load_team_games(paths, [SEASON], targets=[BLITZ])
    assert set(only["metric"].unique()) == {"blitz_rate"}
    none = F.load_team_games(paths, [1999])
    assert none.is_empty() and none.schema == pl.Schema(X.TG_SCHEMA)


def test_observations_sum_metrics_drop_empty_games_and_round_shares() -> None:
    def row(metric, num, den, game="g1", team="AAA", side="offense", sit="all"):
        return dict(season=2024, week=1, game_id=game, team=team, opponent="BBB", side=side,
                    metric=metric, situation=sit, num=num, den=den)  # fmt: skip

    outside = F.get_target("run_outside")
    tg = pl.DataFrame(
        [
            row("run_left_end", 3.0, 20), row("run_right_end", 4.0 - 1e-10, 20),  # float noise
            row("run_left_end", 0.0, 0, game="g2"), row("run_right_end", 0.0, 0, game="g2"),
            row("run_left_end", 9.0, 20, game="g3", side="defense"),  # the other side
            row("run_left_end", 9.0, 20, game="g4", sit="neutral"),  # the other situation
            row("shotgun_rate", 9.0, 20, game="g5"),  # another metric
        ],
        schema=X.TG_SCHEMA,
    )  # fmt: skip
    o = F.observations(tg, outside)
    assert o.height == 1  # g2 has no designed runs; g3 / g4 / g5 are not this target
    r = o.row(0, named=True)
    assert (r["game_id"], r["team"], r["opponent"]) == ("g1", "AAA", "BBB")
    assert r["k"] == 7.0 and r["n"] == 20  # whole plays: the float noise is rounded off

    rush = F.get_target("rushers_avg")
    tg2 = pl.DataFrame([row("rushers_avg", 123.4567, 30, side="defense")], schema=X.TG_SCHEMA)
    assert F.observations(tg2, rush)["k"][0] == pytest.approx(123.4567)  # a mean is not rounded
    assert list(o.columns) == ["season", "week", "game_id", "team", "opponent", "k", "n"]


def test_observations_refuse_metrics_with_different_denominators() -> None:
    def row(metric, den):
        return dict(season=2024, week=1, game_id="g1", team="AAA", opponent="BBB",
                    side="offense", metric=metric, situation="all", num=1.0, den=den)  # fmt: skip

    tg = pl.DataFrame([row("run_left_end", 20), row("run_right_end", 21)], schema=X.TG_SCHEMA)
    with pytest.raises(ValueError, match="different denominators"):
        F.observations(tg, F.get_target("run_outside"))


def test_a_defense_target_belongs_to_the_defending_team() -> None:
    games, tg = small_league(targets=(BLITZ,), weeks=1)
    o = F.observations(tg, BLITZ)
    assert set(o["team"]) == set(X.TEAMS6) and o.height == 6
    assert F.observations(tg, DEEP).is_empty()  # an offense target finds none


def test_load_games_keeps_playoffs_drops_preseason_and_types_the_columns(tmp_path) -> None:
    rows = X.game_rows([(SEASON, 1, "AAA", "BBB"), (SEASON, 2, "AAA", "CCC")])
    rows[1]["game_type"] = "PRE"
    rows += X.game_rows([(SEASON, 19, "CCC", "BBB")])
    rows[-1]["game_type"] = "WC"
    X.write_tree(tmp_path, rows, pl.DataFrame(schema=X.TG_SCHEMA))
    from nflengine.paths import DataPaths

    g = F.load_games(DataPaths(tmp_path))
    assert sorted(g["week"].to_list()) == [1, 19]
    assert g.schema["season"] == pl.Int32 and g.schema["week"] == pl.Int32
    assert g.schema["kickoff_utc"] == pl.Datetime("us", "UTC")


def test_load_games_without_the_optional_columns_fails_closed(tmp_path) -> None:
    """A games table with no kickoff_utc (or lines, coaches) still builds a design; an FTN
    target holds the previous week's charting back (the rule fails closed)."""
    lg = X.random_league(seed=3, weeks=5, targets=[BLITZ, DEEP])
    cur = tmp_path / "curated"
    cur.mkdir()
    lg.games_df().drop("kickoff_utc", "spread_line", "total_line", "home_coach").write_parquet(
        cur / "games.parquet"
    )
    from nflengine.paths import DataPaths

    g = F.load_games(DataPaths(tmp_path))
    assert g.schema["kickoff_utc"] == pl.Datetime("us", "UTC")
    assert g["kickoff_utc"].null_count() == g.height
    d = F.build_design(BLITZ, F.observations(lg.tg, BLITZ), g, {}, lg.changed, lg.midseason, [2023])
    n_all = F.build_design(
        DEEP, F.observations(lg.tg, DEEP), g, {}, lg.changed, lg.midseason, [2023]
    )
    gi = X.grid_of(d, 2023, "AAA", 4)
    assert d.a["G0"][gi] == 2  # weeks 1-2 only: week 3's FTN waits a week
    assert n_all.a["G0"][X.grid_of(n_all, 2023, "AAA", 4)] == 3  # play-by-play has week 3


# --- current_lines, team_view ---------------------------------------------------------------------
def test_current_lines_prefers_the_schedule_then_odds_then_espn() -> None:
    games = pl.DataFrame(
        {
            "game_id": ["a", "b", "c", "d"],
            "spread_line": [3.0, None, None, None],
            "total_line": [44.0, None, 40.0, None],
        }
    )
    lines = pl.DataFrame(
        {
            "game_id": ["a", "b", "b", "b", "c", "c", "d"],
            "source": ["odds_api", "odds_api", "odds_api", "espn", "espn", "espn", "x"],
            "home_spread": [9.0, 1.0, 3.0, 7.0, 5.0, 6.0, 2.0],
            "total": [50.0, 45.0, 47.0, 49.0, 41.0, 43.0, 1.0],
        }
    )
    out = F.current_lines(games, lines).sort("game_id")
    assert out["game_id"].to_list() == ["a", "b", "c", "d"]
    # a: the schedule's. b: the Odds API median of 1, 3 (2.0). c: ESPN's median (5.5). d: none.
    assert out["spread_line"].to_list() == [3.0, 2.0, 5.5, None]
    assert out["line_source"].to_list() == ["nflverse_schedules", "odds_api", "espn", None]
    assert out["total_line"].to_list() == [44.0, 46.0, 40.0, None]  # a, b (45, 47), c keeps own


def test_current_lines_without_a_lines_table() -> None:
    games = pl.DataFrame({"game_id": ["a", "b"], "spread_line": [3.0, None],
                          "total_line": [44.0, None]})  # fmt: skip
    for lines in (None, pl.DataFrame(), pl.DataFrame({"game_id": ["a"]})):
        out = F.current_lines(games, lines)
        assert out["line_source"].to_list() == ["nflverse_schedules", None]
        assert out["spread_line"].to_list() == [3.0, None]


def test_team_view_gives_each_team_its_own_spread() -> None:
    g = X.games_frame(
        X.game_rows([(SEASON, 3, "AAA", "BBB")], spread=lambda g: 6.5, total=lambda g: 47.0)
    )
    v = F.team_view(g).sort("team")
    assert v["team"].to_list() == ["AAA", "BBB"]
    assert v["opponent"].to_list() == ["BBB", "AAA"]
    assert v["spread"].to_list() == [-6.5, 6.5]  # + = favoured: the home team is favoured by 6.5
    assert v["total"].to_list() == [47.0, 47.0]
    assert v["is_home"].to_list() == [False, True]
    assert (
        F.team_view(X.games_frame(X.game_rows([(SEASON, 3, "AAA", "BBB")])))["spread"].null_count()
        == 2
    )


# --- coaches --------------------------------------------------------------------------------------
def test_read_seed_cleans_a_hand_made_file_and_survives_a_bad_one(tmp_path) -> None:
    assert F.read_seed(tmp_path / "missing.csv").is_empty()
    good = tmp_path / "seed.csv"
    good.write_text(
        "# comment\ncoach,team,season,role\n Ann Smith , kc ,2024, oc \n"
        "Bob,KC,2024,DC\n,KC,2024,OC\n",
        encoding="utf-8",
    )
    s = F.read_seed(good)
    assert s.rows() == [("Ann Smith", "KC", 2024, "OC"), ("Bob", "KC", 2024, "DC")]
    assert s.schema == pl.Schema(
        {"coach": pl.String, "team": pl.String, "season": pl.Int32, "role": pl.String}
    )
    bad = tmp_path / "bad.csv"
    bad.write_text("coach,team\nx,KC\n", encoding="utf-8")  # no season / role columns
    out = F.read_seed(bad)
    assert out.is_empty() and out.columns == ["coach", "team", "season", "role"]
    garbage = tmp_path / "garbage.csv"
    garbage.write_text("coach,team,season,role\nx,KC,notayear,OC\n", encoding="utf-8")
    assert F.read_seed(garbage).is_empty()


def coach_games():
    """AAA: HC x -> y between seasons. BBB: same HC, OC changes. CCC: no seed rows.
    DDD: HC fired in 2024 week 6 (interim z)."""
    team_coach = {
        (2023, "AAA"): lambda w: "x", (2024, "AAA"): lambda w: "y",
        (2023, "BBB"): lambda w: "b", (2024, "BBB"): lambda w: "b",
        (2023, "CCC"): lambda w: "c", (2024, "CCC"): lambda w: "c",
        (2023, "DDD"): lambda w: "d", (2024, "DDD"): lambda w: "d" if w < 6 else "z",
    }  # fmt: skip
    sched = X.schedule(("AAA", "BBB", "CCC", "DDD"), (2023, 2024), 8)
    return X.game_rows(sched, coach=lambda s, w, t: team_coach[(s, t)](w))


def test_coach_changes_flags_head_coach_and_coordinator_changes() -> None:
    games = X.games_frame(coach_games())
    seed = pl.DataFrame(
        [
            ("o1", "BBB", 2023, "OC"), ("o2", "BBB", 2024, "OC"),  # BBB's OC changes
            ("d1", "BBB", 2023, "DC"), ("d1", "BBB", 2024, "DC"),  # BBB's DC stays
            ("p1", "CCC", 2024, "OC"),  # CCC: only this year known -> a "change" from nothing
        ],
        schema=["coach", "team", "season", "role"], orient="row",
    ).with_columns(pl.col("season").cast(pl.Int32))  # fmt: skip
    changed, mid = F.coach_changes(games, seed, None)
    flag = {
        (r["season"], r["team"], r["side"]): r["changed"] for r in changed.iter_rows(named=True)
    }
    # a new head coach changes both sides
    assert flag[(2024, "AAA", "offense")] and flag[(2024, "AAA", "defense")]
    assert not flag[(2023, "AAA", "offense")]  # no 2022 games: nothing to compare
    # the coordinator the seed names
    assert flag[(2024, "BBB", "offense")] is True and flag[(2024, "BBB", "defense")] is False
    # the seed knows only one of the two seasons: a different (empty) staff counts as a change
    assert flag[(2024, "CCC", "offense")] is True and flag[(2024, "CCC", "defense")] is False
    # DDD's firing is mid-season, so the season-to-season flag stays False
    assert not flag[(2024, "DDD", "offense")] and not flag[(2024, "DDD", "defense")]
    assert mid.rows() == [(2024, "DDD", 6)]
    assert mid.schema["change_week"] == pl.Int32


def test_coach_changes_without_a_seed_counts_only_head_coaches() -> None:
    games = X.games_frame(coach_games())
    empty_seed = F.read_seed(Path("does-not-exist.csv"))
    changed, mid = F.coach_changes(games, empty_seed, None)
    flag = {
        (r["season"], r["team"], r["side"]): r["changed"] for r in changed.iter_rows(named=True)
    }
    assert flag[(2024, "AAA", "offense")] and not flag[(2024, "BBB", "offense")]
    assert not flag[(2024, "CCC", "offense")]
    assert mid.height == 1


def test_head_coach_fixes_apply_from_their_week_and_a_later_fix_wins() -> None:
    games = X.games_frame(X.game_rows(
        X.schedule(("AAA", "BBB"), (SEASON,), 8), coach=lambda s, w, t: f"{t}-nflverse"
    ))  # fmt: skip
    fixes = pl.DataFrame(
        {
            "season": [SEASON, SEASON, SEASON],
            "team": ["AAA", "AAA", "BBB"],
            "from_week": [3, 6, 8],
            "coach": ["fix-a", "fix-b", "fix-c"],
        }
    ).with_columns(pl.col("season").cast(pl.Int32), pl.col("from_week").cast(pl.Int32))
    hc = F.head_coaches(games, fixes)
    by = {(r["team"], r["week"]): r["hc"] for r in hc.iter_rows(named=True)}
    assert by[("AAA", 2)] == "AAA-nflverse"
    assert by[("AAA", 3)] == by[("AAA", 5)] == "fix-a"
    assert by[("AAA", 6)] == by[("AAA", 8)] == "fix-b"
    assert by[("BBB", 7)] == "BBB-nflverse" and by[("BBB", 8)] == "fix-c"
    assert hc.height == 16
    plain = F.head_coaches(games, None)
    assert set(plain["hc"]) == {"AAA-nflverse", "BBB-nflverse"}
    # a mid-season fix makes the change week
    _, mid = F.coach_changes(games, F.read_seed(Path("none.csv")), fixes)
    assert sorted(mid.rows()) == [(SEASON, "AAA", 3), (SEASON, "BBB", 8)]


# --- the script curve -----------------------------------------------------------------------------
def test_script_features_are_zero_for_missing_lines() -> None:
    c = X.linear_curve(SEASON)
    sp = np.array([np.nan, 10.0, -7.0])
    for t, slope in ((PROE, -0.003), (DROPBACK, -0.004)):
        np.testing.assert_allclose(c.x_spread(t, sp), [0.0, slope * 10, slope * -7], atol=1e-12)
    np.testing.assert_allclose(c.x_baseline(sp), [0.0, -0.02, 0.014], atol=1e-12)
    np.testing.assert_allclose(c.x_total(np.array([np.nan, 55.0, 35.0]), 10.0), [0.0, 1.0, -1.0])
    # beyond the grid the curve holds its edge value
    assert c.x_spread(DROPBACK, np.array([45.0]))[0] == pytest.approx(-0.004 * 30)
    assert c.x_spread(DROPBACK, np.array([-45.0]))[0] == pytest.approx(0.004 * 30)
    # a non-script target never asks, but the helper treats it like dropback rate
    assert c.x_spread(DEEP, np.array([10.0]))[0] == pytest.approx(-0.04)


def test_script_curve_round_trips_through_a_dict() -> None:
    c = X.linear_curve(SEASON)
    d = c.to_dict()
    assert set(d) == set(F.ScriptCurve.__dataclass_fields__)
    c2 = F.ScriptCurve.from_dict({**d, "extra": 1})
    assert c2.to_dict() == d and c2.grid == c.grid
    assert len(c.grid) == len(c.db) == len(c.proe) == len(c.xp) == len(F.SPREAD_GRID) == 121


def test_kernel_curve_is_a_gaussian_weighted_mean_with_a_far_fallback() -> None:
    spread = np.array([0.0, 0.0, 10.0, np.nan])
    value = np.array([1.0, 1.0, 0.0, 5.0])  # the NaN-spread row is dropped
    out = F.kernel_curve(spread, value, 2.5)
    assert len(out) == len(F.SPREAD_GRID)
    at0 = out[F.SPREAD_GRID.index(0.0)]
    w = np.exp(-0.5 * (np.array([0.0, 0.0, 10.0]) / 2.5) ** 2)
    assert at0 == pytest.approx(float((w * np.array([1.0, 1.0, 0.0])).sum() / w.sum()))
    assert out[F.SPREAD_GRID.index(10.0)] < 0.5 < at0  # nearer the 10-point game
    # nothing within reach of 30 points: the plain mean (of the usable rows)
    assert out[F.SPREAD_GRID.index(30.0)] == pytest.approx(2.0 / 3.0)


def test_curve_from_plays_builds_the_three_curves_and_their_means() -> None:
    ref = pl.DataFrame(
        {
            "season": [2023, 2023, 2023, 2022],
            "game_id": ["a", "a", "a", "b"],
            "spread": [3.0, 3.0, 3.0, -3.0],
            "total": [40.0, 40.0, 40.0, 50.0],
            "xpass": [0.5, 0.5, 0.6, 0.6],
            "neutral": [True, False, True, True],
        }
    )
    p = np.array([0.6, 0.4, 0.7, 0.5])
    c = F.curve_from_plays(2024, ref, p, "stub model", 2.5)
    assert c.season == 2024 and c.model == "stub model" and c.n_plays == 4
    assert c.ref_seasons == [2022, 2023]
    assert c.db_mean == pytest.approx(0.55) and c.xp_mean == pytest.approx(0.55)
    assert c.proe_mean == pytest.approx((0.1 + (0.7 - 0.6) + (0.5 - 0.6)) / 3)  # neutral plays only
    # one total per game (40 and 50), not per play (3 plays at 40, 1 at 50)
    assert c.total_mean == pytest.approx(45.0)
    # the curves are Gaussian-weighted means of the plays' values around each spread
    spread = np.array([3.0, 3.0, 3.0, -3.0])
    xp = np.array([0.5, 0.5, 0.6, 0.6])
    neutral = np.array([True, False, True, True])
    for g in (3.0, -3.0, 0.0):
        w = np.exp(-0.5 * ((spread - g) / 2.5) ** 2)
        i = F.SPREAD_GRID.index(g)
        assert c.db[i] == pytest.approx(float((w * p).sum() / w.sum()))
        assert c.xp[i] == pytest.approx(float((w * xp).sum() / w.sum()))
        wn = w[neutral]
        assert c.proe[i] == pytest.approx(float((wn * (p - xp)[neutral]).sum() / wn.sum()))


# --- the design: shapes ---------------------------------------------------------------------------
def test_design_arrays_line_up(league) -> None:
    d = league.design("deep_shot_rate", seasons=[2023, 2024])
    a = d.a
    assert d.rows.height == len(d.y) == len(d.n) == len(a["f_gT"]) == len(a["f_gO"])
    assert int(a["G"][0]) == d.grid.height
    assert (d.rows["season"].is_in([2023, 2024])).all()
    assert d.rows.height == 2 * 8 * 10  # 2 seasons x 8 teams x 10 weeks
    # y is the game's rate; n > 0 everywhere in the synthetic league
    np.testing.assert_allclose(d.y, d.rows["k"].to_numpy() / d.rows["n"].to_numpy())
    # every forecast row has a grid row of its own team and its opponent at its own week
    gT = d.grid[a["f_gT"].tolist()]
    assert gT["team"].to_list() == d.rows["team"].to_list()
    assert gT["as_of_week"].to_list() == d.rows["week"].to_list()
    gO = d.grid[a["f_gO"].tolist()]
    assert gO["team"].to_list() == d.rows["opponent"].to_list()
    assert d.grid["gi"].to_list() == list(range(d.grid.height))
    assert set(d.meta) >= {"seasons", "cutoffs"}


def test_a_game_without_play_counts_has_no_rate() -> None:
    games, tg = small_league(weeks=2)
    tg = tg.filter(pl.col("game_id") != games[0]["game_id"])  # one game unobserved
    d = X.make_design(DEEP, tg, games, seasons=[SEASON], forecast_rows=X.forecast_rows_frame(
        [(SEASON, 1, games[0]["game_id"], games[0]["home_team"], games[0]["away_team"])]
    ))  # fmt: skip
    assert d.rows.height == 1 and d.n[0] == 0 and np.isnan(d.y[0])


# --- the reference match --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "key",
    [
        "deep_shot_rate",
        "blitz_rate",
        "rushers_avg",
        "dropback_rate",
        "proe",
        "run_middle",
        "run_outside",
    ],
)
@pytest.mark.parametrize("params", [P, P_SCRIPT], ids=["plain", "script"])
def test_predictions_match_an_independent_reference(league, key, params) -> None:
    """Every kind, every row of a 3-season league with byes of information (the first season
    has no prior), coordinator flags, a mid-season firing and null lines."""
    d = league.design(key, seasons=[2022, 2023, 2024])
    for kind in F.KINDS:
        ref = ref_for(league, key, params)
        got = F.predict(d, kind, params)
        want = X.ref_forecasts(d, ref, kind, league.curves, league.games)
        assert np.isnan(got).sum() == np.isnan(want).sum() == 8, (key, kind)  # 2022 week 1
        np.testing.assert_allclose(got, want, equal_nan=True, **TOL, err_msg=f"{key} {kind}")
    # the first week of the first season has nothing to forecast from
    first = (d.rows["season"] == 2022) & (d.rows["week"] == 1)
    assert np.isnan(F.predict(d, "b3", params)[first.to_numpy()]).all()


def test_script_params_move_only_the_model_of_a_script_target(league) -> None:
    d = league.design("dropback_rate", seasons=[2023, 2024])
    for kind in ("b1", "b2", "b3", "b4"):
        np.testing.assert_allclose(
            F.predict(d, kind, P_SCRIPT), F.predict(d, kind, P), equal_nan=True, **TOL
        )
    assert not np.allclose(F.predict(d, "model", P_SCRIPT), F.predict(d, "model", P), atol=1e-4)
    # a target without the script term ignores b_spread / b_total entirely
    d2 = league.design("blitz_rate", seasons=[2023, 2024])
    np.testing.assert_allclose(
        F.predict(d2, "model", P_SCRIPT), F.predict(d2, "model", P), equal_nan=True, **TOL
    )


def test_forecasts_are_clipped_to_the_target_bounds(league) -> None:
    d = league.design("deep_shot_rate", seasons=[2023, 2024])
    wild = {**P, "lam": 6.0, "kappa_opp": 1.0, "rho_opp": 0.99, "kappa": 1e-6}
    for kind in ("b3", "b4", "model"):
        f = F.predict(d, kind, wild)
        assert np.nanmin(f) >= 0.0 and np.nanmax(f) <= 1.0
    # kappa -> 0 makes the shrunk rate the raw season-to-date rate (a share is in [0, 1])
    dr = league.design("rushers_avg", seasons=[2023, 2024])
    f = F.predict(dr, "model", wild)
    assert np.nanmin(f) >= 1.0 and np.nanmax(f) <= 11.0


# --- as-of leakage --------------------------------------------------------------------------------
def splice(a: X.League, b: X.League, season: int, from_week: int, *, lines_from: int) -> X.League:
    """`a` with `season`'s games from `from_week` on replaced by `b`'s: counts always, lines
    from `lines_from` on (a week-W forecast may legitimately use week W's own lines)."""
    games = []
    for ga, gb in zip(a.games, b.games, strict=True):
        assert ga["game_id"] == gb["game_id"] and ga["kickoff_utc"] == gb["kickoff_utc"]
        g = dict(ga)
        if ga["season"] == season and ga["week"] >= lines_from:
            g["spread_line"], g["total_line"] = gb["spread_line"], gb["total_line"]
        if ga["season"] == season and ga["week"] >= from_week:
            g["result"] = 99
        games.append(g)
    swap = (pl.col("season") == season) & (pl.col("week") >= from_week)
    tg = pl.concat([a.tg.filter(~swap), b.tg.filter(swap)])
    return X.League(games, tg, a.changed, a.midseason, a.curves, a.teams, a.seasons)


@pytest.fixture(scope="module")
def other_league() -> X.League:
    return X.random_league(seed=99, changed=[], mid=[])


ALL_KEYS = [t.key for t in F.TARGETS]


@pytest.mark.parametrize("week", [3, 6])
@pytest.mark.parametrize("key", ALL_KEYS)
def test_a_week_w_forecast_uses_nothing_from_week_w_on(league, other_league, key, week) -> None:
    """Scramble every count of week >= W (and the lines of weeks after W) and no forecast of
    a game up to week W moves, for any kind. For a script target week W's own lines are
    pre-game information and stay (they feed only the script term)."""
    scrambled = splice(league, other_league, SEASON, week, lines_from=week + 1)
    d1 = league.design(key, seasons=[2023, SEASON])
    d2 = scrambled.design(key, seasons=[2023, SEASON])
    assert d1.rows.select("game_id", "team").equals(d2.rows.select("game_id", "team"))
    up_to = ((d1.rows["season"] < SEASON) | (d1.rows["week"] <= week)).to_numpy()
    after = ~up_to
    for kind in F.KINDS:
        p1, p2 = F.predict(d1, kind, P_SCRIPT), F.predict(d2, kind, P_SCRIPT)
        np.testing.assert_allclose(p1[up_to], p2[up_to], equal_nan=True, **TOL, err_msg=kind)
        if kind in ("b2", "b3", "model"):  # the later weeks did see the scrambled games
            assert not np.allclose(p1[after], p2[after], equal_nan=True, atol=1e-6)
    assert not np.allclose(d1.y[after], d2.y[after], equal_nan=True)  # the actuals did change


@pytest.mark.parametrize("key", ["deep_shot_rate", "blitz_rate", "dropback_rate", "proe"])
def test_week_w_lines_feed_only_the_script_term(league, other_league, key) -> None:
    week = 5
    t = F.get_target(key)
    scrambled = splice(league, other_league, SEASON, week, lines_from=week)  # week W's too
    d1 = league.design(key, seasons=[SEASON])
    d2 = scrambled.design(key, seasons=[SEASON])
    rows = (d1.rows["week"] == week).to_numpy()
    for kind in ("b1", "b2", "b3", "b4", "model"):
        p1, p2 = F.predict_parts(d1, kind, P_SCRIPT), F.predict_parts(d2, kind, P_SCRIPT)
        for part in ("team", "opponent", "prior", "strength", "league"):
            np.testing.assert_allclose(
                getattr(p1, part)[rows], getattr(p2, part)[rows], equal_nan=True, **TOL
            )
        same = np.allclose(p1.forecast[rows], p2.forecast[rows], equal_nan=True, **TOL)
        has_script = (kind == "model" and t.script) or (kind == "b1" and key == "dropback_rate")
        assert same != has_script, (key, kind)  # only the script term sees week W's lines


@pytest.mark.parametrize("key", ["deep_shot_rate", "blitz_rate", "dropback_rate"])
def test_the_live_path_equals_the_backtest_path(league, key) -> None:
    """A live forecast has no week-W rows in its inputs at all; the backtest has them. They
    must agree row for row (`forecast_rows` with the week's games vs everything observed)."""
    week = 6
    full = league.design(key, seasons=[2023, SEASON])
    rows = (
        full.rows.filter((pl.col("season") == SEASON) & (pl.col("week") == week))
        .select("season", "week", "game_id", "team", "opponent")
    )  # fmt: skip
    live_tg = league.tg.filter((pl.col("season") < SEASON) | (pl.col("week") < week))
    live = X.make_design(
        F.get_target(key), live_tg, league.games, curves=league.curves, changed=league.changed,
        mid=league.midseason, seasons=[SEASON], forecast_rows=rows,
    )  # fmt: skip
    assert live.rows.height == rows.height and np.isnan(live.y).all()
    sel = ((full.rows["season"] == SEASON) & (full.rows["week"] == week)).to_numpy()
    assert live.rows.select("game_id", "team").equals(
        full.rows.filter(pl.lit(sel)).select("game_id", "team")
    )
    for kind in F.KINDS:
        np.testing.assert_allclose(
            F.predict(live, kind, P_SCRIPT), F.predict(full, kind, P_SCRIPT)[sel], **TOL,
            err_msg=kind,
        )  # fmt: skip


# --- FTN availability -----------------------------------------------------------------------------
def ftn_counts(key, season, week, team, opp):
    n = 40 + week
    return n // 4 + X.TEAMS6.index(team), n


def availability_league(kickoff=None, weeks=5):
    sched = X.schedule(X.TEAMS6, (SEASON,), weeks)
    games = X.game_rows(sched, kickoff=kickoff)
    return games, X.tg_frame(games, (BLITZ, DEEP), ftn_counts)


def sums(d: F.Design, team: str, as_of: int) -> tuple[float, float, float]:
    gi = X.grid_of(d, SEASON, team, as_of)
    return float(d.a["K0"][gi]), float(d.a["N0"][gi]), float(d.a["G0"][gi])


def games_of(games, team, before):
    return [g for g in games if team in (g["home_team"], g["away_team"]) and g["week"] < before]


def test_monday_night_charting_waits_for_the_next_week() -> None:
    """A week-3 Monday-night game (kickoff + 48 h falls after Tuesday's end): not in the
    FTN target's as-of-4 sums, but in play-by-play's; from as-of 5 both have it."""

    def kickoff(g):
        if g["week"] == 3 and "AAA" in (g["home_team"], g["away_team"]):
            return X.monday_kickoff(SEASON, 3)
        return X.sunday_kickoff(SEASON, g["week"])

    games, tg = availability_league(kickoff)
    mnf = games_of(games, "AAA", 4)[-1]
    assert mnf["week"] == 3
    other = mnf["home_team"] if mnf["away_team"] == "AAA" else mnf["away_team"]
    dtft = X.make_design(BLITZ, tg, games, seasons=[SEASON])
    dpbp = X.make_design(DEEP, tg, games, seasons=[SEASON])
    for team in ("AAA", other):  # both sides of the Monday night game
        assert sums(dtft, team, 4)[2] == 2 and sums(dpbp, team, 4)[2] == 3, team
    # a team that played Sunday keeps its week-3 charting
    sunday = next(t for t in X.TEAMS6 if t not in ("AAA", other))
    assert sums(dtft, sunday, 4)[2] == 3
    # by hand: the Monday game is excluded from N0 / K0 by exactly its own counts
    n3 = 40 + 3
    assert sums(dpbp, "AAA", 4)[1] - sums(dtft, "AAA", 4)[1] == n3
    assert sums(dpbp, "AAA", 4)[0] - sums(dtft, "AAA", 4)[0] == n3 // 4 + 0
    # the next week it has arrived
    assert sums(dtft, "AAA", 5)[2] == sums(dpbp, "AAA", 5)[2] == 4
    # and the exclusion reaches the league and the opponent residuals too
    g_aaa = X.grid_of(dtft, SEASON, "AAA", 4)
    mask = dtft.a["r_og"] == g_aaa
    assert mask.sum() == 2  # AAA's two earlier games seen from its opponents' side


def test_sunday_games_are_visible_by_tuesday_night() -> None:
    """Sunday 1 pm and Sunday night kickoffs + 48 h are inside Tuesday: nothing is held."""
    games, tg = availability_league(
        lambda g: X.sunday_kickoff(SEASON, g["week"], 20, 20 if g["week"] % 2 else 0)
    )
    d = X.make_design(BLITZ, tg, games, seasons=[SEASON])
    dp = X.make_design(DEEP, tg, games, seasons=[SEASON])
    for team in X.TEAMS6:
        for w in (2, 3, 4, 5):
            assert sums(d, team, w) == sums(dp, team, w), (team, w)


def test_the_cutoff_is_capped_at_the_next_weeks_first_kickoff() -> None:
    """If week 4 opens on a Tuesday evening, the as-of-4 view ends there: a Sunday-night game
    (kickoff + 48 h = Tuesday 20:20) is not charted yet, a 1 pm one (Tuesday 13:00) is."""
    tuesday = X.sunday_kickoff(SEASON, 3, 19, 0) + dt.timedelta(days=2)  # Tuesday 19:00 ET
    first_w4 = X.game_id(SEASON, 4, *X.schedule(X.TEAMS6, (SEASON,), 4)[-3][2:])

    def kickoff(g):
        if g["game_id"] == first_w4:
            return tuesday
        if g["week"] == 3 and "BBB" in (g["home_team"], g["away_team"]):
            return X.sunday_kickoff(SEASON, 3, 20, 20)  # Sunday night
        return X.sunday_kickoff(SEASON, g["week"])

    games, tg = availability_league(kickoff, weeks=4)
    assert min(g["kickoff_utc"] for g in games if g["week"] == 4) == tuesday
    d = X.make_design(BLITZ, tg, games, seasons=[SEASON])
    dp = X.make_design(DEEP, tg, games, seasons=[SEASON])
    cut = d.meta["cutoffs"].filter(pl.col("as_of_week") == 4)["cutoff_utc"][0]
    assert cut == tuesday  # capped: the end of that Tuesday would be five hours later
    assert sums(d, "BBB", 4)[2] == 2 and sums(dp, "BBB", 4)[2] == 3  # held back
    one_pm = next(
        t for t in X.TEAMS6
        if all("BBB" not in (g["home_team"], g["away_team"]) for g in games_of(games, t, 4)[-1:])
        and games_of(games, t, 4)[-1]["week"] == 3
    )  # fmt: skip
    assert sums(d, one_pm, 4)[2] == 3  # a 1 pm game made it


def test_a_game_without_a_kickoff_holds_its_charting_for_a_week() -> None:
    def kickoff(g):
        if g["week"] == 3 and "AAA" in (g["home_team"], g["away_team"]):
            return None
        if g["week"] == 2 and "AAA" in (g["home_team"], g["away_team"]):
            return None
        return X.sunday_kickoff(SEASON, g["week"])

    games, tg = availability_league(kickoff)
    d = X.make_design(BLITZ, tg, games, seasons=[SEASON])
    dp = X.make_design(DEEP, tg, games, seasons=[SEASON])
    # as of 4: week 3's game waits (week >= W - 1) but week 2's, also unknown, is old enough
    assert sums(d, "AAA", 4)[2] == 2 and sums(dp, "AAA", 4)[2] == 3
    assert sums(d, "AAA", 3)[2] == 1 and sums(dp, "AAA", 3)[2] == 2  # week 2's waits as of 3
    assert sums(d, "AAA", 5)[2] == sums(dp, "AAA", 5)[2] == 4  # by 5 both have arrived


def test_with_no_kickoffs_at_all_only_last_weeks_charting_is_held() -> None:
    games, tg = availability_league(lambda g: None)
    d = X.make_design(BLITZ, tg, games, seasons=[SEASON])
    dp = X.make_design(DEEP, tg, games, seasons=[SEASON])
    for team in X.TEAMS6:
        for w in (2, 3, 4, 5):
            assert sums(d, team, w)[2] == w - 2 and sums(dp, team, w)[2] == w - 1, (team, w)
    assert d.meta["cutoffs"]["cutoff_utc"].null_count() == d.meta["cutoffs"].height


def test_charting_availability_changes_the_ftn_forecast_but_not_play_by_play() -> None:
    def kickoff(g):
        return (
            X.monday_kickoff(SEASON, g["week"])
            if g["week"] == 3
            else X.sunday_kickoff(SEASON, g["week"])
        )

    held, tg = availability_league(kickoff)
    normal, _ = availability_league()
    for key, differs in (("blitz_rate", True), ("deep_shot_rate", False)):
        t = F.get_target(key)
        a = X.make_design(t, tg, held, seasons=[SEASON])
        b = X.make_design(t, tg, normal, seasons=[SEASON])
        wk4 = (a.rows["week"] == 4).to_numpy()
        same = np.allclose(F.predict(a, "b2", P)[wk4], F.predict(b, "b2", P)[wk4])
        assert same != differs
        # week 5 sees everything either way
        wk5 = (a.rows["week"] == 5).to_numpy()
        np.testing.assert_allclose(F.predict(a, "b3", P)[wk5], F.predict(b, "b3", P)[wk5], **TOL)


# --- shrinkage limits -----------------------------------------------------------------------------
R23 = {"AAA": 0.10, "BBB": 0.14, "CCC": 0.08, "DDD": 0.12, "EEE": 0.16, "FFF": 0.20}


def late_team_league(last_season: bool):
    """2024: weeks 1-2 played by five teams (FFF idles), FFF's first game is week 3."""
    sched = []
    if last_season:
        sched += X.schedule(X.TEAMS6, (2023,), 3)
    sched += [
        (SEASON, 1, "BBB", "AAA"), (SEASON, 1, "DDD", "CCC"),
        (SEASON, 2, "CCC", "AAA"), (SEASON, 2, "EEE", "BBB"),
        (SEASON, 3, "AAA", "FFF"), (SEASON, 3, "BBB", "DDD"),
    ]  # fmt: skip
    games = X.game_rows(sched)

    def counts(key, s, w, team, opp):
        if s == 2023:
            return round(R23[team] * 50), 50
        if w >= 3:
            return None
        return 7, 50  # every 2024 team-game before week 3: 14%

    return games, X.tg_frame(games, (DEEP,), counts)


def fffs_design(last_season: bool, changed=()):
    games, tg = late_team_league(last_season)
    rows = X.forecast_rows_frame([(SEASON, 3, X.game_id(SEASON, 3, "AAA", "FFF"), "FFF", "AAA")])
    return X.make_design(DEEP, tg, games, changed=X.changed_frame(changed), seasons=[SEASON],
                         forecast_rows=rows)  # fmt: skip


def test_no_games_so_far_gives_the_prior_league_plus_rho_times_the_gap() -> None:
    d = fffs_design(True)
    lp = sum(R23.values()) / 6  # last season's league rate, 50 plays each
    gap = R23["FFF"] - lp
    lcur = (2000 * lp + 8 * 7) / (2000 + 8 * 50)  # blended with the 8 team-games so far
    want = lcur + P["rho_same"] * gap
    parts = F.predict_parts(d, "b3", P)
    assert parts.forecast[0] == pytest.approx(want, abs=1e-12)
    assert parts.team[0] == pytest.approx(want) and parts.prior[0] == pytest.approx(want)
    assert parts.strength[0] == pytest.approx(P["kappa"])  # kappa + 0 plays
    assert parts.league[0] == pytest.approx(lcur)
    # the same prior is the forecast of b4 / the model with a neutral opponent term (lam = 0)
    for kind in ("b4", "model"):
        assert F.predict(d, kind, {**P, "lam": 0.0})[0] == pytest.approx(want, abs=1e-12)
    # a new play-caller regresses toward the league harder
    dn = fffs_design(True, changed=[(SEASON, "FFF", "offense", True)])
    assert F.predict(dn, "b3", P)[0] == pytest.approx(lcur + P["rho_new"] * gap, abs=1e-12)
    assert F.predict(dn, "b3", P)[0] < F.predict(d, "b3", P)[0]  # FFF is above the league
    # 'changed' on the other side (defense) is not this target's
    dd = fffs_design(True, changed=[(SEASON, "FFF", "defense", True)])
    assert F.predict(dd, "b3", P)[0] == pytest.approx(want, abs=1e-12)


def test_no_games_and_no_last_season_gives_the_league_rate() -> None:
    d = fffs_design(False)
    for kind in ("b1", "b2", "b3"):
        assert F.predict(d, kind, P)[0] == pytest.approx(56 / 400, abs=1e-12), kind
    games, tg = late_team_league(False)
    nan_week1 = X.make_design(  # no data anywhere: nothing to forecast from
        DEEP, tg, games, seasons=[SEASON],
        forecast_rows=X.forecast_rows_frame(
            [(SEASON, 1, X.game_id(SEASON, 1, "BBB", "AAA"), "AAA", "BBB")]
        ),
    )  # fmt: skip
    for kind in F.KINDS:
        assert np.isnan(F.predict(nan_week1, kind, P)[0]), kind


def test_a_team_with_many_plays_is_forecast_at_its_own_rate() -> None:
    rates = {"AAA": 0.70, "BBB": 0.50, "CCC": 0.20, "DDD": 0.35, "EEE": 0.10, "FFF": 0.55}
    games, tg = small_league(weeks=4, counts=pbp_counts(rates, n=2000))
    d = X.make_design(DEEP, tg, games, seasons=[SEASON])
    wk = (d.rows["week"] == 4).to_numpy()
    teams = d.rows["team"].to_list()
    own = F.predict(d, "b2", P)
    b3 = F.predict(d, "b3", P)
    for i in np.flatnonzero(wk):
        assert own[i] == pytest.approx(rates[teams[i]], abs=1e-12)
        # 3 games x 2000 plays against kappa = 150: the prior's weight is 150 / 6150
        assert abs(b3[i] - own[i]) < P["kappa"] / (P["kappa"] + 6000)
        assert abs(b3[i] - own[i]) < 0.025 * abs(rates[teams[i]] - 0.4)  # (league is ~0.4)


def test_kappa_to_zero_is_the_raw_rate_and_to_infinity_the_prior() -> None:
    rates = {"AAA": 0.70, "BBB": 0.50, "CCC": 0.20, "DDD": 0.35, "EEE": 0.10, "FFF": 0.55}
    games, tg = small_league(weeks=4, counts=pbp_counts(rates, n=50))
    d = X.make_design(DEEP, tg, games, seasons=[SEASON])
    wk = (d.rows["week"] == 4).to_numpy()
    b2 = F.predict(d, "b2", P)[wk]
    np.testing.assert_allclose(F.predict(d, "b3", {**P, "kappa": 1e-9})[wk], b2, atol=1e-8)
    # kappa -> infinity: every team at the league rate (no last season: the league so far)
    far = F.predict_parts(d, "b3", {**P, "kappa": 1e15})
    lg_rate = far.league[wk]
    np.testing.assert_allclose(far.forecast[wk], lg_rate, atol=1e-9)
    assert np.ptp(far.forecast[wk]) < 1e-9 and np.ptp(b2) > 0.3
    # the shrunk rate is always between the prior and the raw rate
    mid = F.predict_parts(d, "b3", P)
    lo = np.minimum(far.forecast[wk], b2) - 1e-12
    hi = np.maximum(far.forecast[wk], b2) + 1e-12
    assert ((mid.forecast[wk] >= lo) & (mid.forecast[wk] <= hi)).all()
    assert (mid.strength[wk] == P["kappa"] + 150).all()  # 3 games x 50 plays


# --- the opponent ---------------------------------------------------------------------------------
def opp_design(opp_eff: dict[str, float], p: dict, weeks: int = 4):
    """Every offense runs 20% deep shots, +opp_eff against that defense; hypothetical week-5
    games put AAA against DDD, BBB and FFF."""
    games, tg = small_league(weeks=weeks, counts=pbp_counts({}, opp_eff=opp_eff))
    games, rows = X.extra_games(  # AAA hosts DDD, BBB and FFF: three scheduled games
        games, [(SEASON, 5, opp, "AAA") for opp in ("DDD", "BBB", "FFF")]
    )
    rows = rows.filter(pl.col("team") == "AAA")
    return X.make_design(DEEP, tg, games, seasons=[SEASON], forecast_rows=rows)


def game_of(d: F.Design, away: str, team: str = "AAA") -> int:
    """The row of `team` in the week-5 game it hosts against `away`."""
    return row_of_game(d, X.game_id(SEASON, 5, away, "AAA"), team)


def test_a_leaky_defense_pushes_the_next_offense_up_and_a_stingy_one_down() -> None:
    d = opp_design({"DDD": 0.08, "FFF": -0.08}, P)
    for kind in ("b4", "model"):
        parts = F.predict_parts(d, kind, P)
        leaky, middle, stingy = (parts.opponent[game_of(d, o)] for o in ("DDD", "BBB", "FFF"))
        assert leaky > 0 > stingy and leaky > middle > stingy, (kind, leaky, middle, stingy)
        f = parts.forecast
        assert f[game_of(d, "DDD")] > f[game_of(d, "BBB")] > f[game_of(d, "FFF")]
        # all three share AAA's own rate; they differ only by lam * the opponent's effect
        assert np.ptp(parts.team) == 0.0
        np.testing.assert_allclose(f - parts.team, parts.opponent, atol=1e-12)
    # with b3 (no opponent term) all three are the same
    assert np.ptp(F.predict(d, "b3", P)) == 0.0
    # lam = 0 switches the term off; a negative lam flips its sign
    zero = F.predict_parts(d, "b4", {**P, "lam": 0.0})
    assert np.all(zero.opponent == 0.0)
    flipped = F.predict_parts(d, "b4", {**P, "lam": -0.5})
    on = F.predict_parts(d, "b4", P)
    np.testing.assert_allclose(flipped.opponent, -0.5 / P["lam"] * on.opponent, atol=1e-12)
    # doubling lam doubles the term
    two = F.predict_parts(d, "b4", {**P, "lam": 2 * P["lam"]})
    np.testing.assert_allclose(two.opponent, 2 * on.opponent, atol=1e-12)


def test_the_opponent_effect_is_adjusted_for_the_offenses_it_faced() -> None:
    """DDD met only the two 60% offenses and EEE only the two 20% ones. Each offense runs
    its own rate against everyone, so neither defense moved anyone: effect 0 (not +-0.2)."""
    rate = {"HH1": 0.6, "HH2": 0.6, "LL1": 0.2, "LL2": 0.2, "DDD": 0.3, "EEE": 0.3}
    sched = [
        (SEASON, 1, "DDD", "HH1"), (SEASON, 1, "EEE", "LL1"), (SEASON, 1, "LL2", "HH2"),
        (SEASON, 2, "DDD", "HH2"), (SEASON, 2, "EEE", "LL2"), (SEASON, 2, "LL1", "HH1"),
        (SEASON, 3, "DDD", "HH1"), (SEASON, 3, "EEE", "LL1"), (SEASON, 3, "LL2", "HH2"),
        (SEASON, 4, "DDD", "HH2"), (SEASON, 4, "EEE", "LL2"), (SEASON, 4, "LL1", "HH1"),
        (SEASON, 5, "DDD", "LL1"), (SEASON, 5, "EEE", "HH1"), (SEASON, 5, "HH2", "LL2"),
    ]  # fmt: skip
    games = X.game_rows(sched)

    def make(held_down: float):
        def counts(key, s, w, team, opp):
            if w >= 5:
                return None
            r = rate[team] + (held_down if opp == "DDD" and team in ("HH1", "HH2") else 0.0)
            return round(r * 50), 50

        return X.tg_frame(games, (DEEP,), counts)

    wk5 = [g for g in games if g["week"] == 5]
    rows = X.forecast_rows_frame(
        [
            (SEASON, 5, g["game_id"], team, opp)
            for g in wk5
            for team, opp in ((g["home_team"], g["away_team"]), (g["away_team"], g["home_team"]))
        ]  # fmt: skip
    )
    tiny = {**P, "kappa": 1e-9, "kappa_opp": 1e-9, "lam": 1.0}
    d = X.make_design(DEEP, make(0.0), games, seasons=[SEASON], forecast_rows=rows)
    parts = F.predict_parts(d, "b4", tiny)
    ddd = row_of_game(d, wk5[0]["game_id"], "LL1")  # LL1 plays DDD in week 5
    eee = row_of_game(d, wk5[1]["game_id"], "HH1")  # HH1 plays EEE
    assert wk5[0]["home_team"] == "LL1" and wk5[1]["home_team"] == "HH1"
    assert parts.opponent[ddd] == pytest.approx(0.0, abs=1e-9)
    assert parts.opponent[eee] == pytest.approx(0.0, abs=1e-9)
    # a defense that held the strong offenses 10 points under their own rate did something:
    # their games average 55% in 2 of 4 games (post = 55%) -> -2.5 plays each of 4 games
    held = X.make_design(DEEP, make(-0.10), games, seasons=[SEASON], forecast_rows=rows)
    ph = F.predict_parts(held, "b4", tiny)
    # HH1 / HH2 each played DDD twice at 50% and LL-teams at 60%: post = (25+30+25+30)/200
    # = 55%; the DDD games are 25 - 50 * 0.55 = -2.5 plays; E = -10 / 200 = -5 points
    assert ph.opponent[row_of_game(held, wk5[0]["game_id"], "LL1")] == pytest.approx(
        -0.05, abs=1e-9
    )


def test_the_opponent_term_is_shrunk_toward_last_seasons_effect() -> None:
    """With no games yet this season the effect is rho_opp * last season's effect."""
    sched = X.schedule(X.TEAMS6, (2023, SEASON), 4)
    games = X.game_rows(sched)
    tg = X.tg_frame(
        [g for g in games if g["season"] == 2023],
        (DEEP,),
        pbp_counts({}, opp_eff={"DDD": 0.08}),
    )
    wk1 = [g for g in games if g["season"] == SEASON and g["week"] == 1]
    assert [(g["away_team"], g["home_team"]) for g in wk1][-1] == ("DDD", "EEE")
    rows = X.forecast_rows_frame(
        [
            (SEASON, 1, wk1[2]["game_id"], "EEE", "DDD"),  # DDD's visitor
            (SEASON, 1, wk1[0]["game_id"], "BBB", "AAA"),  # an ordinary matchup
        ]
    )
    d = X.make_design(DEEP, tg, games, seasons=[SEASON], forecast_rows=rows)
    parts = F.predict_parts(d, "b4", {**P, "lam": 1.0})
    vs_ddd = parts.opponent[X.row_of(d, SEASON, 1, "EEE")]
    vs_bbb = parts.opponent[X.row_of(d, SEASON, 1, "BBB")]
    # DDD's four 2023 opponents each ran 14 on a team rate of 22% (the other 3 games at 20%):
    # +3 plays of 50 -> +6 points. No 2024 game yet, so the effect is rho_opp times that.
    assert vs_ddd == pytest.approx(P["rho_opp"] * 0.06, abs=1e-12)
    assert vs_bbb < vs_ddd
    more = F.predict_parts(d, "b4", {**P, "lam": 1.0, "rho_opp": 0.7})
    assert more.opponent[X.row_of(d, SEASON, 1, "EEE")] == pytest.approx(0.7 * 0.06, abs=1e-12)
    # kappa_opp does not matter while there are no games (the prior is all there is)
    flat = F.predict_parts(d, "b4", {**P, "lam": 1.0, "kappa_opp": 5.0})
    assert flat.opponent[X.row_of(d, SEASON, 1, "EEE")] == pytest.approx(vs_ddd, abs=1e-12)


def test_venue_targets_group_by_the_home_team_not_the_opponent() -> None:
    """Run location is charted by the home stats crew: CCC's building (it hosts every week)
    calls 8 points more middle runs, whoever visits. So a game AT CCC gets the bump for both
    teams, and a game CCC plays away does not."""
    mid = F.get_target("run_middle")
    games, _ = small_league(weeks=4)
    assert {g["home_team"] for g in games if g["week"] <= 4} >= {"CCC"}

    def counts(key, season, week, team, opp):
        game = next(
            g for g in games
            if g["week"] == week and {team, opp} == {g["home_team"], g["away_team"]}
        )  # fmt: skip
        return round((0.30 + (0.08 if game["home_team"] == "CCC" else 0.0)) * 50), 50

    tg = X.tg_frame(games, (mid,), counts)
    games, rows = X.extra_games(
        games, [(SEASON, 5, "BBB", "CCC"), (SEASON, 5, "CCC", "AAA"), (SEASON, 5, "DDD", "FFF")]
    )
    d = X.make_design(mid, tg, games, seasons=[SEASON], forecast_rows=rows)
    parts = F.predict_parts(d, "b4", {**P, "lam": 1.0})
    at_ccc = X.game_id(SEASON, 5, "BBB", "CCC")
    away = X.game_id(SEASON, 5, "CCC", "AAA")
    other = X.game_id(SEASON, 5, "DDD", "FFF")
    bump = parts.opponent[row_of_game(d, at_ccc, "BBB")]
    assert bump > 0.01  # the venue's effect, shrunk
    assert parts.opponent[row_of_game(d, at_ccc, "CCC")] == pytest.approx(bump, abs=1e-12)
    # CCC visiting AAA: AAA's building. The opponent being CCC doesn't matter for this target
    assert parts.opponent[row_of_game(d, away, "CCC")] < bump / 2
    assert parts.opponent[row_of_game(d, other, "DDD")] < bump / 2
    # the same data as an opponent target (the visitor's identity) would not give both teams
    # of one game the same term
    assert mid.context == "venue" and DEEP.context == "opponent"


def test_a_venue_target_falls_back_to_the_opponent_for_games_it_cannot_place() -> None:
    """A game missing from the schedule has no home team: its context is the opponent (for the
    observations and for the rows to forecast), so the design still builds."""
    from dataclasses import replace

    venue = F.get_target("run_middle")
    twin = replace(venue, context="opponent")
    games, _ = small_league(weeks=4)
    tg = X.tg_frame(games, (venue,), pbp_counts({"AAA": 0.4}, opp_eff={"DDD": 0.1}))
    rows = X.forecast_rows_frame(
        [(SEASON, 5, "G1", "AAA", "DDD"), (SEASON, 5, "G2", "BBB", "EEE")]
    )  # games that are in nobody's schedule
    a = X.make_design(venue, tg, [], seasons=[SEASON], forecast_rows=rows)
    b = X.make_design(twin, tg, [], seasons=[SEASON], forecast_rows=rows)
    assert a.rows["ctx"].to_list() == ["DDD", "EEE"]
    for kind in F.KINDS:
        np.testing.assert_allclose(F.predict(a, kind, P), F.predict(b, kind, P), **TOL)
    assert F.predict_parts(a, "b4", P).opponent[0] > 0  # DDD's opponents ran above their rates
    # with the schedule known the venue is used (CCC hosts every week here)
    known, _ = small_league(weeks=4)
    rows2 = X.forecast_rows_frame(
        [(SEASON, 4, g["game_id"], g["away_team"], g["home_team"]) for g in known if g["week"] == 4]
    )
    c = X.make_design(venue, tg, known, seasons=[SEASON], forecast_rows=rows2)
    assert c.rows["ctx"].to_list() == [g["home_team"] for g in known if g["week"] == 4] or set(
        c.rows["ctx"]
    ) <= set(X.TEAMS6)
    home = {g["game_id"]: g["home_team"] for g in known}
    assert all(r["ctx"] == home[r["game_id"]] for r in c.rows.iter_rows(named=True))


# --- the game script ------------------------------------------------------------------------------
def script_league(spread_of, total_of=None, weeks=4, counts=None):
    sched = X.schedule(X.TEAMS6, (SEASON,), weeks + 1)
    games = X.game_rows(sched, spread=spread_of, total=total_of)
    tg = X.tg_frame([g for g in games if g["week"] <= weeks], (DROPBACK,), counts)
    rows = X.forecast_rows_frame(
        [
            (SEASON, weeks + 1, g["game_id"], team, opp)
            for g in games
            if g["week"] == weeks + 1
            for team, opp in ((g["home_team"], g["away_team"]), (g["away_team"], g["home_team"]))
        ]  # fmt: skip
    )
    curves = {SEASON: X.linear_curve(SEASON)}
    return games, tg, rows, curves


def test_a_favourites_season_is_not_double_counted() -> None:
    """FFF is always a 10-point favourite and, as the script says (-0.004 per point), throws
    4 points less: 46% against 50%. Its opponents get +4. With the script term the team rate
    is script-adjusted first, so everyone is a 50% team and the forecast is 50% - 4 = 46%."""

    def spread_of(g):
        if g["home_team"] == "FFF":
            return 10.0
        return -10.0 if g["away_team"] == "FFF" else 0.0

    def counts(key, s, w, team, opp):
        if team == "FFF":
            return 23, 50
        return (27, 50) if opp == "FFF" else (25, 50)

    games, tg, rows, curves = script_league(spread_of, counts=counts)
    d = X.make_design(DROPBACK, tg, games, curves=curves, seasons=[SEASON], forecast_rows=rows)
    p = {**P, "lam": 0.0, "b_spread": 1.0, "b_total": 0.0}
    fff = next(i for i, r in enumerate(d.rows.iter_rows(named=True)) if r["team"] == "FFF")
    parts = F.predict_parts(d, "model", p)
    assert parts.team[fff] == pytest.approx(0.50, abs=1e-12)  # script-adjusted: no favourite effect
    assert parts.script[fff] == pytest.approx(-0.04, abs=1e-12)
    assert parts.forecast[fff] == pytest.approx(0.46, abs=1e-12)
    # counted twice it would be 0.42; counted not at all (b4) it is the shrunk raw rate
    assert abs(parts.forecast[fff] - 0.42) > 0.03
    b3 = F.predict(d, "b3", p)[fff]
    assert 0.46 < b3 < 0.50  # raw 46% pulled toward the league
    # the underdog side of the same game: FFF's opponent is +10 underdog -> +4
    opp_row = next(i for i, r in enumerate(d.rows.iter_rows(named=True))
                   if r["opponent"] == "FFF")  # fmt: skip
    assert parts.forecast[opp_row] == pytest.approx(0.54, abs=1e-12)
    # the league and every team as seen by the model are script-free
    np.testing.assert_allclose(parts.league, 0.5, atol=1e-12)
    # no script weight: back to b4
    off = F.predict(d, "model", {**p, "b_spread": 0.0})
    np.testing.assert_allclose(off, F.predict(d, "b4", p), **TOL)


def test_the_total_line_term_is_per_ten_points_from_the_curves_mean() -> None:
    games, tg, rows, curves = script_league(
        lambda g: 0.0,
        total_of=lambda g: 55.0 if g["week"] == 5 else 45.0,
        counts=lambda key, s, w, team, opp: (25, 50),
    )
    d = X.make_design(DROPBACK, tg, games, curves=curves, seasons=[SEASON], forecast_rows=rows)
    np.testing.assert_allclose(d.a["f_x2"], 1.0)  # (55 - 45) / 10
    np.testing.assert_allclose(d.a["f_x1"], 0.0)
    p = {**P, "b_total": 0.03, "b_spread": 0.0}
    parts = F.predict_parts(d, "model", p)
    np.testing.assert_allclose(parts.script, 0.03, atol=1e-12)
    np.testing.assert_allclose(
        parts.forecast, F.predict(d, "b4", p) + 0.03, atol=1e-12
    )  # history had a 45-point total: no adjustment of the team term
    # the other curve scale: total_scale in the settings
    d2 = X.make_design(DROPBACK, tg, games, curves=curves, seasons=[SEASON], forecast_rows=rows,
                       settings={"total_scale": 5.0})  # fmt: skip
    np.testing.assert_allclose(d2.a["f_x2"], 2.0)


def test_games_without_a_line_have_no_script() -> None:
    games, tg, rows, curves = script_league(
        lambda g: None if "AAA" in (g["home_team"], g["away_team"]) else 7.0,
        total_of=lambda g: None if "AAA" in (g["home_team"], g["away_team"]) else 50.0,
        counts=lambda key, s, w, team, opp: (25, 50),
    )
    d = X.make_design(DROPBACK, tg, games, curves=curves, seasons=[SEASON], forecast_rows=rows)
    no_line = d.rows["spread"].is_null().to_numpy()
    assert no_line.any() and (~no_line).any()
    assert (d.a["f_x1"][no_line] == 0).all() and (d.a["f_x2"][no_line] == 0).all()
    assert (d.a["f_xb"][no_line] == 0).all()
    assert (d.a["f_x1"][~no_line] != 0).all() and (d.a["f_x2"][~no_line] != 0).all()
    assert np.isfinite(F.predict(d, "model", P_SCRIPT)).all()  # no NaN from a missing line


def test_a_target_without_the_script_term_has_no_script_features() -> None:
    games, _, rows, curves = script_league(
        lambda g: 7.0, total_of=lambda g: 60.0, counts=lambda *a: (10, 50)
    )
    tg = X.tg_frame([g for g in games if g["week"] <= 4], (DEEP,), lambda *a: (10, 50))
    d = X.make_design(DEEP, tg, games, curves=curves, seasons=[SEASON], forecast_rows=rows)
    assert (d.a["f_x1"] == 0).all() and (d.a["f_x2"] == 0).all() and (d.a["f_xb"] == 0).all()
    assert (d.a["X1"] == 0).all() and (d.a["X1L"] == 0).all()
    assert (F.predict_parts(d, "model", P_SCRIPT).script == 0).all()


def test_with_script_weights_zero_the_model_is_b4(league) -> None:
    for key in ("dropback_rate", "proe"):
        d = league.design(key, seasons=[2023, 2024])
        zero = {**P_SCRIPT, "b_spread": 0.0, "b_total": 0.0}
        np.testing.assert_allclose(
            F.predict(d, "model", zero), F.predict(d, "b4", zero), equal_nan=True, **TOL
        )


# --- b1 and b2 ------------------------------------------------------------------------------------
def test_b2_in_week_one_is_last_seasons_rate_and_b1_the_leagues() -> None:
    sched = X.schedule(X.TEAMS6, (2023, SEASON), 3)
    games = X.game_rows(sched)

    def counts(key, s, w, team, opp):
        return (round(R23[team] * 50), 50) if s == 2023 else (7, 50)

    tg = X.tg_frame(games, (DEEP,), counts)
    d = X.make_design(DEEP, tg, games, seasons=[SEASON])
    wk1 = (d.rows["week"] == 1).to_numpy()
    teams = d.rows["team"].to_list()
    b2, b1 = F.predict(d, "b2", P), F.predict(d, "b1", P)
    lp = sum(R23.values()) / 6
    for i in np.flatnonzero(wk1):
        assert b2[i] == pytest.approx(R23[teams[i]], abs=1e-12)
        assert b1[i] == pytest.approx(lp, abs=1e-12)
    # from week 2 on b2 is the season-to-date rate (14% for everyone), not last season's
    wk2 = (d.rows["week"] == 2).to_numpy()
    np.testing.assert_allclose(b2[wk2], 0.14, atol=1e-12)
    # and b1 is last season's league rate blended with this season's so far
    np.testing.assert_allclose(b1[wk2], (2000 * lp + 6 * 7) / (2000 + 6 * 50), atol=1e-12)


def test_b2_is_the_unweighted_raw_rate_even_after_a_coaching_change() -> None:
    games, tg = small_league(weeks=6, counts=pbp_counts({"CCC": 0.1, "AAA": 0.4}))
    d = X.make_design(DEEP, tg, games, mid=X.mid_frame([(SEASON, "CCC", 3)]), seasons=[SEASON])
    i = X.row_of(d, SEASON, 6, "CCC")
    assert F.predict(d, "b2", P)[i] == pytest.approx(0.1, abs=1e-12)
    gi = X.grid_of(d, SEASON, "CCC", 6)
    assert d.a["N"][gi] < d.a["N0"][gi]  # the model's team term is weighted, b2 is not


def test_b1_for_dropback_rate_adds_the_baseline_script_and_for_proe_does_not() -> None:
    games, tg, rows, curves = script_league(
        lambda g: 10.0 if g["home_team"] == "AAA" else (-10.0 if g["away_team"] == "AAA" else 0.0),
        counts=lambda key, s, w, team, opp: (29, 50),
    )
    d = X.make_design(DROPBACK, tg, games, curves=curves, seasons=[SEASON], forecast_rows=rows)
    parts = F.predict_parts(d, "b1", P_SCRIPT)
    i = next(i for i, r in enumerate(d.rows.iter_rows(named=True)) if r["team"] == "AAA")
    spread = d.rows["spread"][i]
    assert abs(spread) == 10.0
    xb = -0.002 * spread  # the xpass-curve script: the league's situational baseline
    assert parts.script[i] == pytest.approx(xb, abs=1e-12)
    assert parts.forecast[i] == pytest.approx(0.58 + xb, abs=1e-12)  # league 29/50 + script
    assert parts.opponent[i] == 0 and parts.team[i] == pytest.approx(0.58)
    # b1 ignores the model's script weights and its lines are not adjusted
    np.testing.assert_allclose(F.predict(d, "b1", P), F.predict(d, "b1", P_SCRIPT), **TOL)
    # PROE: b1 is the league's PROE, no script
    tg2 = X.tg_frame([g for g in games if g["week"] <= 4], (PROE,), lambda *a: (1.5, 50))
    d2 = X.make_design(PROE, tg2, games, curves=curves, seasons=[SEASON], forecast_rows=rows)
    np.testing.assert_allclose(F.predict(d2, "b1", P), 0.03, atol=1e-12)
    assert (F.predict_parts(d2, "b1", P).script == 0).all()


# --- a mid-season head-coach change ---------------------------------------------------------------
def mid_design(settings=None, mid=((SEASON, "CCC", 4),)):
    games, tg = small_league(
        weeks=8, counts=lambda key, s, w, team, opp: (w + X.TEAMS6.index(team), 50)
    )
    return games, X.make_design(DEEP, tg, games, mid=X.mid_frame(mid), seasons=[SEASON],
                                settings=settings)  # fmt: skip


def test_games_before_a_firing_count_half_but_only_from_the_change_week() -> None:
    _, d = mid_design()
    ci = X.TEAMS6.index("CCC")

    def kn(team, w):
        gi = X.grid_of(d, SEASON, team, w)
        return float(d.a["K"][gi]), float(d.a["N"][gi]), float(d.a["K0"][gi]), float(d.a["N0"][gi])

    # before the change week nothing is weighted
    for w in (2, 3):
        k, n, k0, n0 = kn("CCC", w)
        assert (k, n) == (k0, n0) and n0 == 50 * (w - 1)
    # as of the change week (4): weeks 1-3 are before it -> half; as of 5: week 4 counts fully
    k, n, k0, n0 = kn("CCC", 4)
    assert n0 == 150 and n == 75
    assert k0 == sum(w + ci for w in (1, 2, 3)) and k == pytest.approx(k0 / 2)
    k, n, k0, n0 = kn("CCC", 5)
    assert n0 == 200 and n == pytest.approx(0.5 * 150 + 50)
    assert k == pytest.approx(0.5 * sum(w + ci for w in (1, 2, 3)) + (4 + ci))
    k, n, k0, n0 = kn("CCC", 8)
    assert n == pytest.approx(75 + 4 * 50) and n0 == 350
    # other teams are untouched
    for team in ("AAA", "BBB", "DDD"):
        k, n, k0, n0 = kn(team, 8)
        assert (k, n) == (k0, n0)
    # the weight is a setting
    _, d2 = mid_design({"pre_change_weight": 0.25})
    gi = X.grid_of(d2, SEASON, "CCC", 5)
    assert d2.a["N"][gi] == pytest.approx(0.25 * 150 + 50)


def test_the_firing_weights_the_opponent_effect_of_that_defense_too() -> None:
    """The residual rows of games against the fired coach's team are weighted by the same rule."""
    _, d = mid_design()
    g5 = X.grid_of(d, SEASON, "CCC", 5)
    sel = d.a["r_og"] == g5
    assert sel.sum() == 4  # 4 earlier opponents' games against CCC
    assert d.a["r_w"][sel].sum() == pytest.approx(3 * 0.5 + 1.0)  # weeks 1-3 half, week 4 full
    g3 = X.grid_of(d, SEASON, "CCC", 3)
    assert (d.a["r_w"][d.a["r_og"] == g3] == 1.0).all()  # nothing before the change week yet
    # a team that was not fired has weight 1 on every residual row
    other = X.grid_of(d, SEASON, "AAA", 8)
    assert (d.a["r_w"][d.a["r_og"] == other] == 1.0).all()


def test_a_firing_moves_the_team_term_toward_the_new_regime() -> None:
    games, tg = small_league(
        weeks=8, counts=lambda key, s, w, team, opp: (round((0.1 if w < 5 else 0.5) * 50), 50)
    )
    plain = X.make_design(DEEP, tg, games, seasons=[SEASON])
    fired = X.make_design(DEEP, tg, games, mid=X.mid_frame([(SEASON, "CCC", 5)]), seasons=[SEASON])
    for w in (6, 8):
        i = X.row_of(plain, SEASON, w, "CCC")
        # the new regime throws 50%: down-weighting the 10% games raises the forecast
        assert F.predict(fired, "b3", P)[i] > F.predict(plain, "b3", P)[i]
    j = X.row_of(plain, SEASON, 3, "CCC")
    assert F.predict(fired, "b3", P)[j] == pytest.approx(F.predict(plain, "b3", P)[j])  # before


# --- the objective and the fit --------------------------------------------------------------------
def test_the_objective_is_the_play_weighted_squared_error(league) -> None:
    d = league.design("deep_shot_rate", seasons=[2023, 2024])
    mask = (d.rows["season"] == 2024).to_numpy()
    for kind in ("b1", "b2", "b3", "model"):
        f = F.predict(d, kind, P)
        m = mask & np.isfinite(f) & np.isfinite(d.y) & (d.n > 0)
        want = float(sum(n * (a - y) ** 2 for n, a, y in zip(d.n[m], f[m], d.y[m], strict=True))
                     / d.n[m].sum())  # fmt: skip
        assert F.objective(d, kind, P, mask) == pytest.approx(want, rel=1e-12)
    assert F.objective(d, "b3", P, np.zeros(len(d.y), dtype=bool)) == float("inf")
    assert F.objective(d, "b3", P, mask & np.isnan(d.y)) == float("inf")


def test_fit_kind_without_parameters_just_scores(league) -> None:
    d = league.design("deep_shot_rate", seasons=[2023, 2024])
    mask = (d.rows["season"] == 2023).to_numpy()
    for kind in ("b1", "b2"):
        fit = F.fit_kind(d, kind, mask)
        assert fit["params"] == {} and fit["rows"] == int(mask.sum())
        assert fit["loss"] == pytest.approx(F.objective(d, kind, {}, mask))


def test_fit_kind_improves_on_the_start_and_stays_in_bounds(league) -> None:
    d = league.design("deep_shot_rate", seasons=[2023, 2024])
    mask = (d.rows["season"] == 2023).to_numpy()
    fit = F.fit_kind(d, "model", mask)
    names = F.kind_params(d.target, "model")
    assert set(fit["params"]) == set(names)
    start = {s.name: s.to_model(s.start[0]) for s in map(F.PARAMS.get, names)}
    assert fit["loss"] <= F.objective(d, "model", start, mask) + 1e-12
    assert fit["loss"] == pytest.approx(F.objective(d, "model", fit["params"], mask), rel=1e-5)
    assert 0 < fit["params"]["rho_same"] < 1 and 0 < fit["params"]["rho_new"] < 1
    assert fit["params"]["kappa"] > 0 and fit["params"]["kappa_opp"] > 0
    assert -1.0 <= fit["params"]["lam"] <= 6.0
    assert fit["rows"] == int(mask.sum()) and isinstance(fit["at_bound"], list)
    assert F.fit_kind(d, "model", mask)["params"] == fit["params"]  # deterministic


def test_fit_recovers_a_planted_opponent_effect_and_a_planted_team_effect() -> None:
    """Persistent team differences -> a small kappa (trust the team's own rate); defenses that
    move everyone -> a clearly positive lam and b4 beating b3."""
    teams = X.TEAMS8
    rng = np.random.default_rng(5)
    team_eff = {s: {t: float(rng.normal(0, 0.03)) for t in teams} for s in (2022, 2023, 2024)}
    opp_eff = {t: float(rng.normal(0, 0.05)) for t in teams}
    sched = X.schedule(teams, (2022, 2023, 2024), 10)
    games = X.game_rows(sched)

    def counts(key, s, w, team, opp):
        n = 60
        mu = 0.2 + team_eff[s][team] + opp_eff[opp]
        return float(rng.binomial(n, mu)), n

    tg = X.tg_frame(games, (DEEP,), counts)
    d = X.make_design(DEEP, tg, games, seasons=[2023, 2024])
    mask = np.ones(len(d.y), dtype=bool)
    f3, f4 = F.fit_kind(d, "b3", mask), F.fit_kind(d, "b4", mask)
    assert f4["loss"] < f3["loss"]
    assert f4["params"]["lam"] > 0.3
    assert f3["loss"] < F.fit_kind(d, "b2", mask)["loss"]  # shrinkage beats the raw rate


# --- scoring --------------------------------------------------------------------------------------
def test_score_by_hand() -> None:
    t = DEEP
    y = np.array([0.5, 0.2, np.nan, 0.4, 0.3])
    n = np.array([10.0, 20.0, 5.0, 0.0, 8.0])
    k = np.array([5.0, 4.0, 1.0, 0.0, 2.4])
    f = np.array([0.4, 0.5, 0.5, 0.5, np.nan])
    s = F.score(t, y, n, k, f)
    # rows 0 and 1 only: row 2 has no actual, row 3 no plays, row 4 no forecast
    assert s["games"] == 2 and s["plays"] == 30.0
    assert s["mae"] == pytest.approx(0.2)  # (0.1 + 0.3) / 2: each game once
    assert s["wmae"] == pytest.approx((10 * 0.1 + 20 * 0.3) / 30)  # by plays
    assert s["rmse"] == pytest.approx(math.sqrt((0.1**2 + 0.3**2) / 2))
    assert s["bias"] == pytest.approx((-0.1 + 0.3) / 2)
    brier = (5 * 0.6**2 + 5 * 0.4**2 + 4 * 0.5**2 + 16 * 0.5**2) / 30
    assert s["brier"] == pytest.approx(brier)  # every play scored at the forecast rate
    ll = -(5 * math.log(0.4) + 5 * math.log(0.6) + 4 * math.log(0.5) + 16 * math.log(0.5)) / 30
    assert s["logloss"] == pytest.approx(ll)


def test_score_of_a_mean_has_no_per_play_scores_and_extremes_are_clipped() -> None:
    rush = F.get_target("rushers_avg")
    s = F.score(rush, np.array([4.0, 5.0]), np.array([30.0, 30.0]), np.array([120.0, 150.0]),
                np.array([4.5, 4.5]))  # fmt: skip
    assert "brier" not in s and "logloss" not in s and s["mae"] == pytest.approx(0.5)
    # a forecast of exactly 0 or 1 is clipped, so the log loss stays finite
    sh = F.score(DEEP, np.array([0.0, 1.0]), np.array([10.0, 10.0]), np.array([0.0, 10.0]),
                 np.array([1.0, 0.0]))  # fmt: skip
    assert sh["brier"] == pytest.approx(1.0, abs=1e-3)
    # clipped at 1e-4: every play is either 1e-4 or 0.9999 away from the truth
    assert sh["logloss"] == pytest.approx(-math.log(1e-4))
    empty = F.score(DEEP, np.array([np.nan]), np.array([5.0]), np.array([1.0]), np.array([0.2]))
    assert empty == {"games": 0, "plays": 0.0}


def test_calibration_bins_are_equal_count_and_play_weighted() -> None:
    f = np.arange(20) / 20.0 + 0.01
    y = f + np.where(np.arange(20) % 2 == 0, 0.05, -0.01)
    n = np.where(np.arange(20) % 4 == 0, 10.0, 30.0)
    rows, err = F.calibration(DEEP, y, n, f, bins=10)
    assert len(rows) == 10 and all(r["games"] == 2 for r in rows)
    order = np.argsort(f)
    total_err = 0.0
    for i, r in enumerate(rows):
        idx = order[2 * i : 2 * i + 2]
        w = n[idx]
        assert r["bin"] == i
        assert r["forecast"] == pytest.approx(float((w * f[idx]).sum() / w.sum()))
        assert r["actual"] == pytest.approx(float((w * y[idx]).sum() / w.sum()))
        assert r["plays"] == w.sum()
        total_err += w.sum() * abs(r["forecast"] - r["actual"])
    assert err == pytest.approx(total_err / n.sum())
    assert [r["forecast"] for r in rows] == sorted(r["forecast"] for r in rows)
    # too few rows for the bins
    assert F.calibration(DEEP, y[:5], n[:5], f[:5], bins=10) == (
        [],
        pytest.approx(float("nan"), nan_ok=True),
    )
    # NaN rows are ignored
    f2 = f.copy()
    f2[0] = np.nan
    rows2, _ = F.calibration(DEEP, y, n, f2, bins=5)
    assert sum(r["games"] for r in rows2) == 19


def test_calibration_bins_are_by_forecast_not_by_actual() -> None:
    """Forecasts rise while the actuals fall: the low bin holds the low forecasts (and the high
    actuals), so the curve slopes the wrong way."""
    f = np.array([0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80])
    y = f[::-1].copy()
    n = np.full(8, 20.0)
    rows, err = F.calibration(DEEP, y, n, f, bins=2)
    assert [r["forecast"] for r in rows] == pytest.approx([0.25, 0.65])
    assert [r["actual"] for r in rows] == pytest.approx([0.65, 0.25])  # low forecasts, high actuals
    assert err == pytest.approx(0.40)


def test_part_of_season() -> None:
    assert [F.part_of(w) for w in (1, 4, 5, 10, 11, 18, 22)] == [
        "weeks 1-4", "weeks 1-4", "weeks 5-10", "weeks 5-10", "weeks 11+", "weeks 11+", "weeks 11+",
    ]  # fmt: skip


# --- the ship rule --------------------------------------------------------------------------------
def by_season(model_maes, b2_maes, first=2019):
    return {
        first + i: {"model": {"mae": a}, "b2": {"mae": b}}
        for i, (a, b) in enumerate(zip(model_maes, b2_maes, strict=True))
    }


def pooled(model, b3):
    return {"model": {"mae": model}, "b3": {"mae": b3}}


def test_the_pbp_rule_needs_five_of_seven_and_not_worse_than_the_shrunk_rate() -> None:
    wins5 = by_season([0.10] * 5 + [0.12] * 2, [0.11] * 7)
    v = F.ship_verdict(DEEP, wins5, pooled(0.1, 0.11))
    assert v["wins_vs_b2"] == 5 and v["need"] == 5 and v["seasons"] == 7
    assert v["rule_a"] and v["rule_b"] and v["ships"] is True and v["target"] == "deep_shot_rate"
    assert len(v["by_season"]) == 7 and v["by_season"][0]["win"] is True
    wins4 = by_season([0.10] * 4 + [0.12] * 3, [0.11] * 7)
    v4 = F.ship_verdict(DEEP, wins4, pooled(0.1, 0.11))
    assert v4["wins_vs_b2"] == 4 and not v4["rule_a"] and not v4["ships"]
    assert v4["rule_b"]  # rule (b) alone is fine
    # rule (b): worse than the shrunk team rate -> stays descriptive, however many wins
    vb = F.ship_verdict(DEEP, wins5, pooled(0.1101, 0.1100))
    assert vb["rule_a"] and not vb["rule_b"] and not vb["ships"]
    # rule (b) is exact: equal passes, a hair worse (0.00003, the same at 4 decimals) fails
    assert F.ship_verdict(DEEP, wins5, pooled(0.11001, 0.11001))["rule_b"] is True
    assert F.ship_verdict(DEEP, wins5, pooled(0.11000, 0.11001))["rule_b"] is True
    hair = F.ship_verdict(DEEP, wins5, pooled(0.11004, 0.11001))
    assert hair["rule_a"] and hair["rule_b"] is False and hair["ships"] is False


def test_ties_at_four_decimals_are_not_wins() -> None:
    # model vs season-to-date b2: 0.1234 vs 0.1234 (x3: a tie), 0.1235 vs 0.1234 (a loss),
    # 0.1234 vs 0.1235 (a win by 0.0001)
    maes = [0.12341, 0.12342, 0.12343, 0.12346, 0.12340, 0.12340, 0.12340]
    b2s = [0.12344, 0.12344, 0.12344, 0.12344, 0.12346, 0.12346, 0.12346]
    v = F.ship_verdict(DEEP, by_season(maes, b2s), pooled(0.1, 0.1))
    assert [w["win"] for w in v["by_season"]] == [False] * 4 + [True] * 3
    assert v["wins_vs_b2"] == 3 and not v["ships"]
    five = F.ship_verdict(
        DEEP, by_season([0.12340] * 5 + [0.2] * 2, [0.12346] * 7), pooled(0.1, 0.1)
    )
    assert five["wins_vs_b2"] == 5 and five["ships"]
    # identical forecasts never win, however many seasons
    flat = by_season([0.5] * 7, [0.5] * 7)
    assert F.ship_verdict(DEEP, flat, pooled(0.5, 0.5))["wins_vs_b2"] == 0
    # a clear but tiny improvement (0.0001) wins; 0.00004 below does not
    tiny = F.ship_verdict(DEEP, by_season([0.1000] * 7, [0.1001] * 7), pooled(0.1, 0.1))
    assert tiny["wins_vs_b2"] == 7 and tiny["ships"]
    tie = F.ship_verdict(DEEP, by_season([0.10000] * 7, [0.10004] * 7), pooled(0.1, 0.1))
    assert tie["wins_vs_b2"] == 0 and not tie["ships"]


def test_ftn_targets_need_three_of_four() -> None:
    four = by_season([0.1, 0.1, 0.1, 0.2], [0.11] * 4, first=2022)
    v = F.ship_verdict(BLITZ, four, pooled(0.1, 0.1))
    assert v["need"] == 3 and v["wins_vs_b2"] == 3 and v["ships"]
    two = by_season([0.1, 0.1, 0.2, 0.2], [0.11] * 4, first=2022)
    assert not F.ship_verdict(BLITZ, two, pooled(0.1, 0.1))["ships"]
    # the rule is configurable
    assert F.ship_verdict(BLITZ, two, pooled(0.1, 0.1), {"seasons_ftn": 2, "seasons_pbp": 5})[
        "ships"
    ]
    assert F.ship_verdict(DEEP, two, pooled(0.1, 0.1), {"seasons_ftn": 2, "seasons_pbp": 2})[
        "ships"
    ]


def test_seasons_missing_a_score_are_skipped() -> None:
    bs = by_season([0.1] * 3, [0.2] * 3)
    bs[2030] = {"model": {}, "b2": {"mae": 0.3}}  # no model MAE (nothing scored)
    bs[2031] = {"b2": {"mae": 0.3}}
    v = F.ship_verdict(BLITZ, bs, pooled(0.1, 0.1))
    assert v["seasons"] == 3 and v["wins_vs_b2"] == 3 and v["ships"]
    missing = F.ship_verdict(DEEP, bs, {"model": {}, "b3": {"mae": 0.1}})
    assert missing["rule_b"] is False and not missing["ships"]
    empty = F.ship_verdict(DEEP, {}, {})
    assert empty["seasons"] == 0 and not empty["ships"] and empty["by_season"] == []


# --- ranges ---------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def big_league() -> X.League:
    return X.random_league(
        seed=21, teams=X.TEAMS10, seasons=(2022, 2023, 2024), weeks=17, null_lines=0.0,
        targets=[DEEP, BLITZ, F.get_target("rushers_avg")],
    )  # fmt: skip


def parts_and_mask(d, kind="model"):
    parts = F.predict_parts(d, kind, P)
    mask = np.isfinite(parts.forecast) & np.isfinite(d.y) & (d.n > 0)
    return parts, mask


@pytest.mark.parametrize("key", ["deep_shot_rate", "blitz_rate", "rushers_avg"])
def test_the_range_covers_80_percent_of_its_own_fitting_rows(big_league, key) -> None:
    d = big_league.design(key, seasons=[2023, 2024])
    parts, mask = parts_and_mask(d)
    rng = F.fit_range(d, parts, mask)
    assert mask.sum() > 300
    assert rng["c"] >= 0 and rng["b"] >= 0  # NNLS
    assert rng["q_lo"] <= 0 <= rng["q_hi"]
    lo, hi = F.predict_range(d, parts, rng)
    inside = (d.y[mask] >= lo[mask]) & (d.y[mask] <= hi[mask])
    assert inside.mean() == pytest.approx(0.80, abs=0.015)
    t = d.target
    assert (lo >= t.lo - 1e-12).all() and (hi <= t.hi + 1e-12).all() and (lo <= hi).all()
    # fewer plays -> a wider range; the range around a forecast is the forecast +- its quantiles
    lo10, hi10 = F.predict_range(d, parts, rng, np.full(len(d.y), 10.0))
    lo90, hi90 = F.predict_range(d, parts, rng, np.full(len(d.y), 90.0))
    ok = mask & (d.n > 0)
    assert ((hi10 - lo10)[ok] >= (hi90 - lo90)[ok] - 1e-12).all()
    assert ((hi10 - lo10)[ok] > (hi90 - lo90)[ok]).mean() > 0.9


def test_the_range_fit_never_takes_a_negative_variance_term(league) -> None:
    """Big samples are the noisy games here, so an unconstrained fit would give the sampling
    term a negative weight; the fit (non-negative least squares) sets it to zero and keeps the
    constant."""
    d = league.design("deep_shot_rate", seasons=[2024])
    m = len(d.y)
    n = np.where(np.arange(m) % 2 == 0, 10.0, 100.0)
    sign = np.where(np.arange(m) % 4 < 2, 1.0, -1.0)
    d.a["f_n"] = n
    d.a["f_y"] = 0.3 + sign * np.where(n == 10, 0.01, 0.10)
    f = np.full(m, 0.3)
    parts = F.Parts(f, f, np.zeros(m), np.zeros(m), f, np.full(m, 1000.0), f)
    rng = F.fit_range(d, parts, np.ones(m, dtype=bool))
    assert rng["c"] == 0.0 and rng["b"] > 0.0
    assert rng["b"] == pytest.approx(np.mean((d.y - f) ** 2), rel=1e-6)  # the constant alone


def test_a_small_sample_falls_back_to_the_normal_quantiles(big_league) -> None:
    d = big_league.design("deep_shot_rate", seasons=[2024])
    parts, mask = parts_and_mask(d)
    few = mask & (np.cumsum(mask) <= 15)
    rng = F.fit_range(d, parts, few)
    assert rng["q_lo"] == pytest.approx(-F.RANGE_Z) and rng["q_hi"] == pytest.approx(F.RANGE_Z)
    assert pytest.approx(1.2815515655446004) == F.RANGE_Z


def test_ranges_are_clipped_to_the_target_bounds(big_league) -> None:
    d = big_league.design("deep_shot_rate", seasons=[2024])
    parts, _ = parts_and_mask(d)
    wide = {"c": 50.0, "b": 0.5, "q_lo": -1.3, "q_hi": 1.3}
    lo, hi = F.predict_range(d, parts, wide)
    assert (lo == 0.0).any() and (hi == 1.0).any()  # a share can't leave [0, 1]
    assert (lo >= 0).all() and (hi <= 1).all()
    dr = big_league.design("rushers_avg", seasons=[2024])
    pr, _ = parts_and_mask(dr)
    lor, hir = F.predict_range(dr, pr, {"c": 0.0, "b": 100.0, "q_lo": -1.3, "q_hi": 1.3})
    assert lor.min() == 1.0 and hir.max() == 11.0  # rushers live in [1, 11]
    # the proe range is bounded by +-1
    dp = big_league.design("deep_shot_rate", seasons=[2024])
    assert F.get_target("proe").lo == -1.0 and F.get_target("proe").hi == 1.0
    assert np.isfinite(F.expected_plays(dp)).all() and (F.expected_plays(dp) > 0).all()


def test_the_range_follows_the_variance_formula_and_widens_with_less_certainty() -> None:
    """var = c * u * (1/n + 1/strength) + b, u = f(1 - f) for a share, 1 for a mean."""
    games, tg = small_league(weeks=2)
    d = X.make_design(DEEP, tg, games, seasons=[SEASON])
    f = np.array([0.30, 0.30, 0.30, 0.30])
    z = np.zeros(4)
    strength = np.array([50.0, 500.0, 50.0, 500.0])
    n = np.array([20.0, 20.0, 80.0, 80.0])
    parts = F.Parts(f, f, z, z, f, strength, f)
    rng = {"c": 1.5, "b": 0.0004, "q_lo": -1.2, "q_hi": 1.4}
    lo, hi = F.predict_range(d, parts, rng, n)
    sd = np.sqrt(1.5 * 0.3 * 0.7 * (1 / n + 1 / strength) + 0.0004)
    np.testing.assert_allclose(lo, f - 1.2 * sd, atol=1e-12)  # an asymmetric pair of quantiles
    np.testing.assert_allclose(hi, f + 1.4 * sd, atol=1e-12)
    width = hi - lo
    assert width[0] > width[1] > width[3] and width[0] > width[2] > width[3]  # n and strength
    # a mean is not scaled by f(1 - f)
    rush = F.get_target("rushers_avg")
    assert F._range_basis(rush, np.array([4.0]), np.array([10.0]), np.array([100.0]))[0] == (
        pytest.approx(0.1 + 0.01)
    )
    assert F._range_basis(DEEP, np.array([0.5]), np.array([10.0]), np.array([100.0]))[0] == (
        pytest.approx(0.25 * 0.11)
    )
    # fewer than one play counts as one (no division by zero)
    assert np.isfinite(
        F._range_basis(DEEP, np.array([0.5]), np.array([0.0]), np.array([0.0]))
    ).all()


def test_expected_plays_is_the_season_average_then_last_seasons_then_the_leagues() -> None:
    sched = X.schedule(X.TEAMS6, (2023, SEASON), 3)
    games = X.game_rows(sched)

    def counts(key, s, w, team, opp):
        return 5, (30 if s == 2023 else 10 * w)

    tg = X.tg_frame(games, (DEEP,), counts)
    d = X.make_design(DEEP, tg, games, seasons=[SEASON])
    ep = F.expected_plays(d)
    for i, r in enumerate(d.rows.iter_rows(named=True)):
        if r["week"] == 1:
            assert ep[i] == 30.0  # no games yet: last season's per-game average
        elif r["week"] == 2:
            assert ep[i] == 10.0  # one game of 10 plays
        else:
            assert ep[i] == 15.0  # (10 + 20) / 2


def test_expected_plays_falls_back_through_the_league_as_of_the_week() -> None:
    """A team with no games and no last season is expected to have the league's plays per
    team-game as of its week; with none yet, last season's league; with none, 30. Never the
    design's own actual plays (those are the future)."""
    sched = [*X.schedule(X.TEAMS6, (2023,), 3), *X.schedule(X.TEAMS6, (SEASON,), 3)]
    sched += [(SEASON, 1, "ZZZ", "AAA"), (SEASON, 3, "YYY", "BBB")]  # ZZZ debuts week 1, YYY week 3
    games = X.game_rows(sched)

    def counts(key, s, w, team, opp):
        return 5, (50 if s == 2023 else 20 + 10 * w)  # 2024: 30 plays in week 1, 40 in week 2

    tg = X.tg_frame([g for g in games if g["week"] <= 3], (DEEP,), counts)
    d = X.make_design(DEEP, tg, games, seasons=[SEASON])
    ep = F.expected_plays(d)

    def at(team, week):
        return ep[X.row_of(d, SEASON, week, team)]

    assert at("ZZZ", 1) == 50.0  # no games, no last season; no 2024 games yet -> 2023's league
    # YYY at week 3: the league's 2024 plays per team-game over weeks 1-2 (week 1 has four games
    # with ZZZ's, 8 team-games of 30 plays; week 2 six team-games of 40): 480 / 14
    assert at("YYY", 3) == pytest.approx(480 / 14)
    # with nothing at all to go on (no earlier season either): 30
    first = X.make_design(DEEP, tg.filter(pl.col("season") == SEASON), games, seasons=[SEASON])
    zz = F.expected_plays(first)[X.row_of(first, SEASON, 1, "ZZZ")]
    assert zz == F.DEFAULT_PLAYS == 30.0
    # an established team uses its own: CCC's last season (50) in week 1, this season's after
    assert at("CCC", 1) == 50.0 and at("CCC", 3) == pytest.approx((30 + 40) / 2)


def test_the_range_never_uses_the_plays_of_the_week_or_later() -> None:
    """Change the plays of every game from week 3 on (a different number of plays, not just
    different outcomes): no earlier game's expected plays or 80% range moves."""
    teams = X.TEAMS6

    def make(late_n: int):
        games = X.game_rows(X.schedule(teams, (2024,), 6))

        def counts(key, s, w, team, opp):
            return 5, (30 if w < 3 else late_n)

        return games, X.tg_frame(games, (DEEP,), counts)

    g1, tg1 = make(30)
    g2, tg2 = make(90)
    d1 = X.make_design(DEEP, tg1, g1, seasons=[SEASON])
    d2 = X.make_design(DEEP, tg2, g2, seasons=[SEASON])
    early = (d1.rows["week"] <= 3).to_numpy()  # week 3's own plays are the future of its forecast
    np.testing.assert_allclose(F.expected_plays(d1)[early], F.expected_plays(d2)[early])
    assert not np.allclose(F.expected_plays(d1)[~early], F.expected_plays(d2)[~early])
    rng = {"c": 1.0, "b": 0.0004, "q_lo": -1.28, "q_hi": 1.28}
    for kind in ("b3", "model"):
        lo1, hi1 = F.predict_range(d1, F.predict_parts(d1, kind, P), rng)
        lo2, hi2 = F.predict_range(d2, F.predict_parts(d2, kind, P), rng)
        np.testing.assert_allclose(lo1[early], lo2[early], equal_nan=True, **TOL)
        np.testing.assert_allclose(hi1[early], hi2[early], equal_nan=True, **TOL)
