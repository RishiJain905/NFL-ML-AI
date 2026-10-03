"""QB status feature (documentation/04 -> B. Game model -> Features) on a hand-made league.

Four teams play a round robin (two games a week): 2024 and 2025 weeks 1-6 are regular
season and played; tests add a 2025 week-7 playoff game when they need one. By default
each team's QB1 (`ARI1`, ...) takes all 30 dropbacks of every game at a fixed EPA per
dropback; `starts` overrides a team-week with a list of (qb, dropbacks, epa per dropback),
the first one being the listed starter.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from nflengine.features.asof import AsOf
from nflengine.features.leakage import LeakageError, assert_future_invariant
from nflengine.features.qb import (
    OUTPUT_SCHEMA,
    QB_FEATURE_COLS,
    QBParams,
    actual_starters,
    game_starters,
    live_starters,
    qb_dropbacks,
    team_qb_features,
)

TEAMS = ["ARI", "ATL", "BAL", "BUF"]
PAIRINGS = [((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2))]
QUALITY = {"ARI1": 0.20, "ATL1": 0.10, "BAL1": 0.0, "BUF1": 0.05}
QUALITY |= {"ARI2": -0.20, "ATL2": -0.20, "BAL2": -0.10, "BUF2": -0.10}
SEASONS = (2024, 2025)
WEEKS = 6

Starts = dict[tuple[int, int, str], list[tuple[str, int, float]]]


def _kickoff(season: int, week: int, gi: int = 0) -> dt.datetime:
    # Thursday 2024-09-05 / 2025-09-04 style openers; the Tuesday of week 1 is 2 days earlier.
    first = {2024: dt.datetime(2024, 9, 5, 17, tzinfo=dt.UTC)}
    first[2025] = dt.datetime(2025, 9, 4, 17, tzinfo=dt.UTC)
    first[2026] = dt.datetime(2026, 9, 10, 17, tzinfo=dt.UTC)
    return first[season] + dt.timedelta(days=7 * (week - 1), hours=3 * gi)


def make_inputs(
    starts: Starts | None = None,
    playoff: tuple[str, str] | None = None,
    depth_w1: dict[tuple[int, str], str] | None = None,
) -> dict[str, pl.DataFrame]:
    """games, dropbacks and week-1 depth charts (undated, QB1 = QB1 unless `depth_w1`)."""
    starts = starts or {}
    games, dbs = [], []

    def add_game(season, week, gi, home, away, game_type="REG"):
        lines = {
            t: starts.get((season, week, t), [(f"{t}1", 30, QUALITY[f"{t}1"])])
            for t in (home, away)
        }
        gid = f"{season}_{week:02d}_{away}_{home}"
        games.append(
            {
                "game_id": gid,
                "season": season,
                "week": week,
                "game_type": game_type,
                "home_team": home,
                "away_team": away,
                "home_qb_id": lines[home][0][0],
                "away_qb_id": lines[away][0][0],
                "home_qb_name": f"Name {lines[home][0][0]}",
                "away_qb_name": f"Name {lines[away][0][0]}",
                "kickoff_utc": _kickoff(season, week, gi),
                "completed": True,
            }
        )
        for team, line in lines.items():
            for qb, n, epa in line:
                dbs.append(
                    {
                        "season": season,
                        "week": week,
                        "game_id": gid,
                        "team": team,
                        "player_id": qb,
                        "dropbacks": n,
                        "epa_sum": n * epa,
                    }
                )

    for season in SEASONS:
        for week in range(1, WEEKS + 1):
            for gi, (a, b) in enumerate(PAIRINGS[(week - 1) % 3]):
                home, away = (TEAMS[a], TEAMS[b]) if week % 2 else (TEAMS[b], TEAMS[a])
                add_game(season, week, gi, home, away)
    if playoff:
        add_game(2025, WEEKS + 1, 0, playoff[0], playoff[1], game_type="WC")
    depth_w1 = depth_w1 or {}
    depth = [
        {
            "season": s,
            "week": 1,
            "team": t,
            "position": "QB",
            "depth_rank": 1,
            "gsis_id": depth_w1.get((s, t), f"{t}1"),
        }
        for s in SEASONS
        for t in TEAMS
    ]
    i32 = [pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)]
    return {
        "games": pl.DataFrame(games).with_columns(i32),
        "dropbacks": pl.DataFrame(dbs).with_columns(i32),
        "depth_charts": pl.DataFrame(depth).with_columns(*i32, pl.col("depth_rank").cast(pl.Int32)),
    }


def all_keys(playoff: bool = False) -> list[AsOf]:
    keys = [AsOf(s, w) for s in SEASONS for w in range(1, WEEKS + 1)]
    return keys + ([AsOf(2025, WEEKS + 1)] if playoff else [])


def build(inp: dict[str, pl.DataFrame], keys=None, **kw) -> pl.DataFrame:
    return team_qb_features(
        inp["games"],
        inp["dropbacks"],
        keys or all_keys(),
        depth_charts=inp.get("depth_charts"),
        **kw,
    )


def row(df: pl.DataFrame, season: int, week: int, team: str) -> dict:
    hit = df.filter(
        (pl.col("season") == season) & (pl.col("week") == week) & (pl.col("team") == team)
    )
    assert hit.height == 1
    return hit.row(0, named=True)


# ---- inputs ------------------------------------------------------------------------------
def test_qb_dropbacks_keeps_dropbacks_only():
    base = {
        "season": 2025,
        "week": 1,
        "game_id": "g",
        "posteam": "ARI",
        "passer_id": "ARI1",
        "qb_dropback": 1.0,
        "play_type": "pass",
        "two_point_attempt": 0.0,
        "qb_epa": 1.0,
    }
    variants = [
        {},  # pass
        {"play_type": "run", "qb_epa": 0.5},  # scramble
        {"qb_epa": -2.0},  # sack
        {"two_point_attempt": 1.0, "qb_epa": 9.0},
        {"play_type": "no_play", "qb_epa": 9.0},
        {"passer_id": None, "qb_epa": 9.0},
        {"qb_dropback": 0.0, "play_type": "run", "qb_epa": 9.0},  # designed run
        {"qb_epa": None},
        {"passer_id": "ARI2", "qb_epa": 0.25},
    ]
    plays = pl.DataFrame(
        [base | v | {"play_id": float(i)} for i, v in enumerate(variants)],
        schema_overrides={"passer_id": pl.String, "qb_epa": pl.Float64},
    )
    out = qb_dropbacks(plays).sort("player_id")
    assert out["player_id"].to_list() == ["ARI1", "ARI2"]
    assert out["dropbacks"].to_list() == [3, 1]
    assert out["epa_sum"].to_list() == pytest.approx([-0.5, 0.25])
    assert out["team"].to_list() == ["ARI", "ARI"]


def test_params_from_config():
    p = QBParams.from_config({"half_life_weeks": 30, "prior_value": "-0.1"}, prior_dropbacks=100)
    assert (p.half_life_weeks, p.prior_value, p.prior_dropbacks) == (30.0, -0.1, 100.0)
    assert QBParams.from_config(None) == QBParams()
    with pytest.raises(ValueError, match="unknown qb settings"):
        QBParams.from_config({"half_life": 3})
    with pytest.raises(ValueError):
        QBParams(prior_dropbacks=0)


# ---- values and the Tuesday rule ---------------------------------------------------------
def test_output_shape_and_same_starter_gives_zero_adj():
    inp = make_inputs()
    out = build(inp)
    assert out.schema == pl.Schema(OUTPUT_SCHEMA)
    assert set(QB_FEATURE_COLS) <= set(out.columns)
    # Every team plays every week: 4 rows per key.
    assert out.height == 4 * len(all_keys())
    assert out["qb_adj"].abs().max() < 1e-12
    assert (out["qb_id"] == out["team"] + "1").all()
    first = out.filter((pl.col("season") == 2024) & (pl.col("week") == 1))
    assert first["qb_source"].unique().to_list() == ["depth_chart_w1"]
    later = out.filter(pl.col("week") > 1)
    assert later["qb_source"].unique().to_list() == ["last_game"]
    assert not later["qb_changed"].any()
    assert first["qb_changed"].is_null().all()  # no earlier game to compare with
    assert row(out, 2025, 3, "ARI")["qb_name"] == "Name ARI1"
    # The value tracks the QB's EPA per dropback, shrunk toward the prior.
    ari, bal = row(out, 2025, 6, "ARI"), row(out, 2025, 6, "BAL")
    assert QBParams().prior_value < ari["qb_value"] < QUALITY["ARI1"]
    assert ari["qb_value"] > bal["qb_value"]
    assert ari["qb_exp"] > row(out, 2024, 3, "ARI")["qb_exp"] > 0


def test_no_dropbacks_gives_prior_value():
    inp = make_inputs()
    params = QBParams(prior_value=-0.07)
    ov = pl.DataFrame(
        {"season": [2025], "week": [3], "team": ["BUF"], "qb_id": ["NEW"], "qb_source": ["x"]}
    )
    players = pl.DataFrame({"gsis_id": ["NEW"], "display_name": ["New Guy"]})
    out = build(inp, params=params, overrides=ov, players=players)
    r = row(out, 2025, 3, "BUF")
    assert r["qb_value"] == pytest.approx(-0.07)
    assert r["qb_exp"] == 0.0
    assert r["qb_source"] == "x"
    assert r["qb_name"] == "New Guy"
    assert r["qb_last_id"] == "BUF1"
    assert r["qb_changed"] is True
    assert r["qb_adj"] < 0


def test_backup_taking_over_in_game_is_next_expected_starter():
    # 2025 week 3: ATL1 is listed and gets hurt early; ATL2 takes 30 dropbacks.
    starts = {(2025, 3, "ATL"): [("ATL1", 5, 0.10), ("ATL2", 30, -0.20)]}
    inp = make_inputs(starts)
    out = build(inp)
    before, after = row(out, 2025, 3, "ATL"), row(out, 2025, 4, "ATL")
    assert before["qb_id"] == "ATL1"
    assert after["qb_id"] == "ATL2"
    assert after["qb_source"] == "last_game"
    assert after["qb_value"] < before["qb_value"]
    assert after["qb_adj"] < -0.03
    # Same main starter as the previous game: not a change by that definition.
    assert after["qb_changed"] is False
    gs = game_starters(inp["games"], inp["dropbacks"])
    week3 = gs.filter(
        (pl.col("season") == 2025) & (pl.col("week") == 3) & (pl.col("team") == "ATL")
    )
    assert week3["qb_id"].to_list() == ["ATL2"]


def test_star_returning_via_override_has_positive_adj():
    starts = {(2025, w, "ATL"): [("ATL2", 30, -0.20)] for w in (2, 3, 4)}
    inp = make_inputs(starts)
    tue = row(build(inp), 2025, 5, "ATL")
    assert tue["qb_id"] == "ATL2" and tue["qb_adj"] < 0
    ov = pl.DataFrame(
        {"season": [2025], "week": [5], "team": ["ATL"], "qb_id": ["ATL1"], "qb_source": ["m"]}
    )
    back = row(build(inp, overrides=ov), 2025, 5, "ATL")
    assert back["qb_id"] == "ATL1" and back["qb_source"] == "m"
    assert back["qb_last_id"] == "ATL2"
    assert back["qb_changed"] is True
    assert back["qb_adj"] > 0.02
    assert back["qb_baseline"] == pytest.approx(tue["qb_baseline"])


def test_actual_starters_oracle():
    starts = {(2025, 3, "ATL"): [("ATL1", 5, 0.10), ("ATL2", 30, -0.20)]}
    inp = make_inputs(starts)
    # Default: the listed starter (what a final injury update knew), not the in-game backup.
    ov = actual_starters(inp["games"], inp["dropbacks"])
    assert ov.height == 4 * len(all_keys())
    assert ov.equals(actual_starters(inp["games"]))  # dropbacks only fill missing listings
    r = row(build(inp, overrides=ov), 2025, 3, "ATL")
    assert (r["qb_id"], r["qb_source"], r["qb_last_id"]) == ("ATL1", "actual", "ATL1")
    # The old rule also foresees the in-game injury.
    ov = actual_starters(inp["games"], inp["dropbacks"], rule="most_dropbacks")
    r = row(build(inp, overrides=ov), 2025, 3, "ATL")
    assert (r["qb_id"], r["qb_source"], r["qb_last_id"]) == ("ATL2", "actual", "ATL1")
    assert r["qb_adj"] < 0
    with pytest.raises(ValueError, match="rule must be"):
        actual_starters(inp["games"], rule="best")
    with pytest.raises(ValueError, match="needs dropbacks"):
        actual_starters(inp["games"], rule="most_dropbacks")


def test_week1_depth_chart_rule():
    # BAL rests BAL1 in the last 2024 game; BAL3 (never played) is QB1 on the 2025 chart.
    starts = {(2024, WEEKS, "BAL"): [("BAL2", 30, -0.10)]}
    inp = make_inputs(starts, depth_w1={(2025, "BAL"): "BAL3"})
    r = row(build(inp), 2025, 1, "BAL")
    assert (r["qb_id"], r["qb_source"]) == ("BAL3", "depth_chart_w1")
    assert r["qb_value"] == pytest.approx(QBParams().prior_value)
    assert r["qb_changed"] is True  # vs BAL2, who started the last game

    # Dated charts (2025+ format) count only if published by the Tuesday of week 1.
    tuesday = dt.date(2025, 9, 2)  # the 2025 opener is Thursday 2025-09-04
    for snap, expect in ((tuesday, "BAL3"), (tuesday + dt.timedelta(days=2), "BAL1")):
        dated = inp | {
            "depth_charts": inp["depth_charts"].with_columns(pl.lit(snap).alias("snap_date"))
        }
        r = row(build(dated), 2025, 1, "BAL")
        assert r["qb_id"] == expect
        if expect == "BAL1":
            # No usable chart: last season's main starter, not the rested-week backup.
            assert r["qb_source"] == "last_season_main"
            assert r["qb_last_id"] == "BAL1"
    # No chart at all: same fallback.
    r = row(build(inp | {"depth_charts": None}), 2025, 1, "BAL")
    assert (r["qb_id"], r["qb_source"]) == ("BAL1", "last_season_main")


def test_first_playoff_game_uses_recent_starts():
    # ARI rests ARI1 in the last regular-season week, then plays a wild-card game.
    starts = {(2025, WEEKS, "ARI"): [("ARI2", 30, -0.20)]}
    inp = make_inputs(starts, playoff=("ARI", "ATL"))
    out = build(inp, keys=all_keys(playoff=True))
    wc = out.filter((pl.col("season") == 2025) & (pl.col("week") == WEEKS + 1))
    assert sorted(wc["team"].to_list()) == ["ARI", "ATL"]
    r = row(out, 2025, WEEKS + 1, "ARI")
    assert (r["qb_id"], r["qb_source"]) == ("ARI1", "recent_starts")
    assert r["qb_changed"] is True
    assert r["qb_adj"] > 0  # the last-game rule would have said ARI2 (negative)
    assert row(out, 2025, WEEKS + 1, "ATL")["qb_source"] == "recent_starts"


def test_unknown_starter_gives_null_value_and_zero_adj():
    inp = make_inputs()
    out = build(inp | {"depth_charts": None}, keys=[AsOf(2024, 1)])
    assert out.height == 4
    assert out["qb_id"].is_null().all() and out["qb_value"].is_null().all()
    assert (out["qb_adj"] == 0).all()


# ---- leakage and determinism -------------------------------------------------------------
@pytest.mark.parametrize("key", [AsOf(2024, 4), AsOf(2025, 1), AsOf(2025, 3)])
def test_future_invariant(key):
    starts = {(2025, 3, "ATL"): [("ATL1", 5, 0.10), ("ATL2", 30, -0.20)]}
    starts |= {(2025, 5, "BUF"): [("BUF2", 30, -0.10)]}
    inp = make_inputs(starts, playoff=("ARI", "BUF"))
    inputs = {k: inp[k] for k in ("games", "dropbacks", "depth_charts")}

    def run(d):
        return team_qb_features(
            d["games"], d["dropbacks"], all_keys(playoff=True), depth_charts=d["depth_charts"]
        )

    # The week-1 depth chart is preseason information (D44), so its ranks stay put;
    # everything else on/after the key (dropbacks, `completed`) is scrambled.
    checked = assert_future_invariant(run, inputs, key, protect={"depth_rank"})
    assert checked.height > 0


def test_future_invariant_catches_a_leak():
    inp = make_inputs()
    inputs = {"games": inp["games"], "dropbacks": inp["dropbacks"]}

    def leaky(d):
        # Expected starter from the key's own week: post-Tuesday information.
        return (
            actual_starters(d["games"], d["dropbacks"])
            .with_columns(pl.col("week").cast(pl.Int32))
            .join(
                d["dropbacks"].select(
                    "season", "week", "team", pl.col("player_id").alias("qb_id"), "epa_sum"
                ),
                on=["season", "week", "team", "qb_id"],
            )
        )

    with pytest.raises(LeakageError):
        assert_future_invariant(leaky, inputs, AsOf(2025, 3))


def test_deterministic():
    starts = {(2025, 3, "ATL"): [("ATL1", 5, 0.10), ("ATL2", 30, -0.20)]}
    inp = make_inputs(starts)
    a = build(inp)
    shuffled = inp | {"dropbacks": inp["dropbacks"].sample(fraction=1.0, shuffle=True, seed=3)}
    assert a.equals(build(shuffled))


# ---- live starters -----------------------------------------------------------------------
def test_live_starters_precedence():
    run = dt.date(2026, 10, 3)
    teams = [("AAA", "BBB", "A9", None), ("CCC", "DDD", None, None), ("EEE", "FFF", "E1", None)]
    games = pl.DataFrame(
        [
            {
                "game_id": f"2026_04_{a}_{h}",
                "season": 2026,
                "week": 4,
                "game_type": "REG",
                "home_team": h,
                "away_team": a,
                "home_qb_id": hq,
                "away_qb_id": aq,
                "home_qb_name": None,
                "away_qb_name": None,
                "kickoff_utc": _kickoff(2026, 4, gi),
                "completed": h == "EEE",
            }
            for gi, (h, a, hq, aq) in enumerate(teams)
        ],
        schema_overrides={
            "home_qb_id": pl.String,
            "away_qb_id": pl.String,
            "home_qb_name": pl.String,
            "away_qb_name": pl.String,
        },
    ).with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))

    def chart(week, team, ids, snap):
        return [
            {
                "season": 2026,
                "week": week,
                "team": team,
                "position": "QB",
                "depth_rank": r + 1,
                "gsis_id": g,
                "snap_date": snap,
            }
            for r, g in enumerate(ids)
        ]

    depth = pl.DataFrame(
        chart(4, "AAA", ["A1", "A2"], dt.date(2026, 10, 2))  # schedule (A9) wins
        + chart(4, "BBB", ["B1", "B2"], dt.date(2026, 10, 2))
        + chart(4, "BBB", ["B7", "B1"], dt.date(2026, 10, 5))  # published after the run
        + chart(4, "CCC", ["C1", "C2", "C3"], dt.date(2026, 10, 1))
        + chart(3, "DDD", ["D1", "D2"], dt.date(2026, 9, 27))  # only an older week
    ).with_columns(pl.col("season", "week", "depth_rank").cast(pl.Int32))
    injuries = pl.DataFrame(
        {
            "season": [2026, 2026, 2026, 2026],
            "week": [4, 4, 4, 3],
            "team": ["CCC", "CCC", "AAA", "BBB"],
            "gsis_id": ["C1", "C2", "A1", "B1"],
            "report_status": ["Out", "Doubtful", "Out", "Out"],  # BBB's is last week's
        }
    ).with_columns(pl.col("season", "week").cast(pl.Int32))

    out = live_starters(games, depth, injuries, 2026, 4, run)
    got = {r["team"]: (r["qb_id"], r["qb_source"]) for r in out.iter_rows(named=True)}
    assert got == {
        "AAA": ("A9", "schedule"),
        "BBB": ("B1", "depth_chart"),
        "CCC": ("C3", "injury_next"),
    }
    assert out.schema == pl.Schema(
        {"season": pl.Int32, "week": pl.Int32, "team": pl.String}
        | {"qb_id": pl.String, "qb_source": pl.String}
    )
    # A scheduled QB listed Out is replaced by the next healthy QB on the chart.
    hurt = injuries.with_columns(
        pl.when(pl.col("gsis_id") == "A1").then(pl.lit("A9")).otherwise("gsis_id").alias("gsis_id")
    )
    out = live_starters(games, depth, hurt, 2026, 4, run)
    assert out.filter(pl.col("team") == "AAA").row(0)[3:] == ("A1", "injury_next")
    # No unplayed games that week: no overrides.
    assert live_starters(games, None, None, 2026, 5, run).is_empty()


def test_live_starters_replaces_injured_tuesday_starter():
    # 2025 week 6 is unplayed; the schedule names nobody and there is no week-6 chart.
    inp = make_inputs()
    week6 = (pl.col("season") == 2025) & (pl.col("week") == WEEKS)
    games = inp["games"].with_columns(
        pl.when(week6).then(False).otherwise(pl.col("completed")).alias("completed"),
        *[
            pl.when(week6).then(None).otherwise(pl.col(c)).alias(c)
            for c in ("home_qb_id", "away_qb_id")
        ],
    )
    dropbacks = inp["dropbacks"].filter(~week6)
    w5 = pl.DataFrame(
        {
            "season": [2025, 2025],
            "week": [5, 5],
            "team": ["ATL", "ATL"],
            "position": ["QB", "QB"],
            "depth_rank": [1, 2],
            "gsis_id": ["ATL1", "ATL2"],
        }
    ).cast(inp["depth_charts"].schema)
    depth = pl.concat([inp["depth_charts"], w5])
    # ATL's Tuesday starter is Out (ATL2 is next on its latest chart); BAL's is Doubtful
    # with nobody behind him on file.
    injuries = pl.DataFrame(
        {
            "season": [2025, 2025],
            "week": [WEEKS, WEEKS],
            "gsis_id": ["ATL1", "BAL1"],
            "report_status": ["Out", "Doubtful"],
        }
    ).with_columns(pl.col("season", "week").cast(pl.Int32))
    run = dt.date(2025, 10, 11)

    # Without dropbacks the Tuesday starter is unknown here: no overrides (old behaviour).
    assert live_starters(games, depth, injuries, 2025, WEEKS, run).is_empty()
    out = live_starters(games, depth, injuries, 2025, WEEKS, run, dropbacks=dropbacks)
    # Healthy (or irreplaceable) Tuesday starters need no override.
    assert out.rows() == [(2025, WEEKS, "ATL", "ATL2", "injury_next")]

    feat = team_qb_features(
        games, dropbacks, [AsOf(2025, WEEKS)], depth_charts=depth, overrides=out
    )
    atl = row(feat, 2025, WEEKS, "ATL")
    assert (atl["qb_id"], atl["qb_last_id"], atl["qb_source"]) == ("ATL2", "ATL1", "injury_next")
    assert atl["qb_adj"] < 0
    assert row(feat, 2025, WEEKS, "BAL")["qb_id"] == "BAL1"
