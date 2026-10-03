"""Selection for "Last week under the hood" (digest/under_hood.py) on a synthetic league.

27 generic WRs (one per team) have steady NGS receiving numbers in 2024 and 2025 weeks
1-6; the digest is for 2025 week 5, so week 4 is "last week" and weeks 5-6 (plus the 2025
week-0 season totals) are the future. Planted players:
- A (KC): separation 1.0 every week, 3.2 last week (the biggest jump, mid-pool value);
- B (BUF): separation 1.1 every week, 3.15 last week (a slightly smaller jump);
- E (DAL): a 15-yard YAC above expectation last week on only 2 catches (below the 3+ floor);
- F (NYG): no 2025 games before last week (norm from 2024 only);
- G (PHI): one 2025 game before last week (norm blends 2025 and 2024).
16 pass rushers have PFR pressure counts that cycle 0-3 by week.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from nflengine.digest import under_hood as uh
from nflengine.digest.under_hood import (
    METRIC_BY_KEY,
    OUTPUT_SCHEMA,
    build_under_hood,
    ordinal,
    rank_note,
    select_under_hood,
)
from nflengine.features.asof import AsOf
from nflengine.features.leakage import assert_future_invariant

SEASON, WEEK = 2025, 5
LAST = WEEK - 1
PLANTED_TEAMS = {"00-A": "KC", "00-B": "BUF", "00-E": "DAL", "00-F": "NYG", "00-G": "PHI"}
ALL_TEAMS = ["ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB"]
ALL_TEAMS += ["HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG"]
ALL_TEAMS += ["NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS"]
GENERIC_TEAMS = [t for t in ALL_TEAMS if t not in PLANTED_TEAMS.values()]
WEEKS = [(2024, w) for w in range(1, 7)] + [(2025, w) for w in range(1, 7)]


def _rec(season, week, pid, team, sep, cushion=6.0, yac=0.5, targets=6, receptions=4, total=False):
    return {
        "season": season,
        "season_type": "REG",
        "week": 0 if total else week,
        "player_gsis_id": pid,
        "player_display_name": f"Player {pid}",
        "player_position": "WR",
        "team": team,
        "targets": targets,
        "receptions": receptions,
        "avg_separation": sep,
        "avg_cushion": cushion,
        "avg_yac_above_expectation": yac,
        "is_season_total": total,
    }


def _league(e_receptions: int = 2) -> dict[str, pl.DataFrame]:
    rng = np.random.default_rng(11)
    rec = []
    for i, team in enumerate(GENERIC_TEAMS):
        pid = f"00-W{i:02d}"
        sep_mean = 2 + 2 * i / (len(GENERIC_TEAMS) - 1)
        cush_mean = 5 + 2 * ((i * 11) % len(GENERIC_TEAMS)) / (len(GENERIC_TEAMS) - 1)
        yac_mean = -1 + 3 * ((i * 7) % len(GENERIC_TEAMS)) / (len(GENERIC_TEAMS) - 1)
        for season, week in WEEKS:
            rec.append(
                _rec(
                    season,
                    week,
                    pid,
                    team,
                    sep_mean + rng.normal(0, 0.1),
                    cush_mean + rng.normal(0, 0.1),
                    yac_mean + rng.normal(0, 0.1),
                )
            )
        rec.append(_rec(SEASON, 0, pid, team, 9.9, 9.9, 9.9, 60, 40, total=True))
    for season, week in WEEKS:
        last = (season, week) == (SEASON, LAST)
        rec.append(_rec(season, week, "00-A", "KC", 3.2 if last else 1.0))
        rec.append(_rec(season, week, "00-B", "BUF", 3.15 if last else 1.1))
        rec.append(
            _rec(season, week, "00-E", "DAL", 3.0, yac=15.0 if last else 0.5,
                 receptions=e_receptions if last else 4)
        )  # fmt: skip
        if season == 2024 or week >= LAST:
            rec.append(_rec(season, week, "00-F", "NYG", 3.0 if season == 2024 else 3.4))
        if season == 2024 or week in (2, LAST) or week > LAST:
            rec.append(_rec(season, week, "00-G", "PHI", 3.5 if last else 2.5))

    pfr, players = [], []
    for j in range(16):
        pid, team = f"00-R{j:02d}", GENERIC_TEAMS[j]
        players.append(
            {"gsis_id": pid, "display_name": f"Rusher {j}", "position": "DE",
             "position_group": "DL"}
        )  # fmt: skip
        for season, week in WEEKS:
            pfr.append(
                {
                    "game_id": f"{season}_{week:02d}_{team}",
                    "season": season,
                    "week": week,
                    "game_type": "REG",
                    "team": team,
                    "gsis_id": pid,
                    "pfr_player_name": f"Rusher {j}",
                    "def_pressures": float((j + week) % 4),
                }
            )
    for row in rec:
        pid = row["player_gsis_id"]
        players.append(
            {"gsis_id": pid, "display_name": f"Name {pid}", "position": "WR",
             "position_group": "WR"}
        )  # fmt: skip
    return {
        "ngs_receiving": pl.DataFrame(rec),
        "pfr_def": pl.DataFrame(pfr),
        "players": pl.DataFrame(players).unique("gsis_id", keep="first", maintain_order=True),
    }


@pytest.fixture(scope="module")
def league() -> dict[str, pl.DataFrame]:
    return _league()


def _visible(tables: dict[str, pl.DataFrame]) -> dict[str, pl.DataFrame]:
    key = AsOf(SEASON, WEEK)
    return {
        n: df.filter(uh.before_expr(key)) if "week" in df.columns else df
        for n, df in tables.items()
    }


def _candidates(tables: dict[str, pl.DataFrame]) -> pl.DataFrame:
    return uh._candidates(uh.metric_rows(_visible(tables)), SEASON, WEEK)


def _cand(cands: pl.DataFrame, pid: str, metric: str) -> dict:
    rows = cands.filter((pl.col("player_id") == pid) & (pl.col("metric") == metric))
    assert rows.height == 1, f"{pid} {metric}: {rows.height} rows"
    return rows.to_dicts()[0]


# ---- schema and empty cases ------------------------------------------------------------------
def test_week1_returns_empty_frame_with_full_schema(league):
    out = build_under_hood(SEASON, 1, league)
    assert out.height == 0
    assert dict(out.schema) == OUTPUT_SCHEMA
    # The public loader returns before touching the data drive.
    assert dict(select_under_hood(SEASON, 1).schema) == OUTPUT_SCHEMA


def test_missing_last_week_gives_empty_frame(league):
    tables = {k: v.filter(pl.col("week") != LAST) if "week" in v.columns else v
              for k, v in league.items()}  # fmt: skip
    out = build_under_hood(SEASON, WEEK, tables)
    assert out.height == 0 and dict(out.schema) == OUTPUT_SCHEMA


def test_output_schema_and_values(league):
    out = build_under_hood(SEASON, WEEK, league)
    assert dict(out.schema) == OUTPUT_SCHEMA
    assert 3 <= out.height <= 5
    assert out["rank"].to_list() == list(range(1, out.height + 1))
    assert (out["source_week"] == LAST).all()
    assert out["score"].is_sorted(descending=True)
    assert set(out["kind"]) <= {"riser", "faller", "standout"}
    assert set(out["team"]) <= set(ALL_TEAMS)


# ---- volume filter, norms and ranking -------------------------------------------------------
def test_min_volume_filter_keeps_low_volume_rows_out_of_the_pool():
    below = _candidates(_league(e_receptions=2))
    assert below.filter(
        (pl.col("player_id") == "00-E") & (pl.col("metric") == "avg_yac_above_expectation")
    ).is_empty()
    out = build_under_hood(SEASON, WEEK, _league(e_receptions=2))
    assert not ((out["player_id"] == "00-E") & (out["metric"] == "avg_yac_above_expectation")).any()

    # Control: with 3 catches the same 15-yard week is in the pool and gets picked.
    at_floor = _candidates(_league(e_receptions=3))
    row = _cand(at_floor, "00-E", "avg_yac_above_expectation")
    assert row["pool_rank"] == 1
    out = build_under_hood(SEASON, WEEK, _league(e_receptions=3))
    assert ((out["player_id"] == "00-E") & (out["metric"] == "avg_yac_above_expectation")).any()


def test_norm_sources(league):
    pool = uh._pool(uh.metric_rows(_visible(league)), SEASON, WEEK)
    a = _cand(pool, "00-A", "avg_separation")
    assert a["norm_source"] == "season" and a["norm_games"] == 3
    assert a["norm"] == pytest.approx(1.0)
    assert a["change"] == pytest.approx(2.2)
    f = _cand(pool, "00-F", "avg_separation")
    assert f["norm_source"] == "last_season" and f["norm_games"] == 6
    assert f["norm"] == pytest.approx(3.0)
    g = _cand(pool, "00-G", "avg_separation")
    assert g["norm_source"] == "season+last_season" and g["norm_games"] == 7
    assert g["norm"] == pytest.approx(2.5)  # 1 game of 2.5 blended with 2024's 2.5
    # Pressures: every game counts toward the norm, zeros included ((j + week) % 4, weeks 1-3).
    r = _cand(pool, "00-R03", "pressures")
    assert r["norm"] == pytest.approx(1.0) and r["norm_games"] == 3
    assert r["value"] == pytest.approx(3.0)


def test_bigger_change_vs_norm_ranks_higher(league):
    cands = _candidates(league)
    a = _cand(cands, "00-A", "avg_separation")
    b = _cand(cands, "00-B", "avg_separation")
    assert a["kind"] == b["kind"] == "riser"
    assert a["up_rank"] == 1 and b["up_rank"] == 2
    # Neither is a standout: both sit mid-pool on last week's value.
    assert a["s"] == 0 and b["s"] == 0
    assert a["score"] > b["score"]
    out = build_under_hood(SEASON, WEEK, league)
    ranks = dict(zip(out["player_id"], out["rank"], strict=True))
    assert ranks["00-A"] < ranks["00-B"]


def test_at_most_one_item_per_player_and_two_per_metric(league):
    out = build_under_hood(SEASON, WEEK, league, max_items=10, min_items=10)
    assert out["player_id"].n_unique() == out.height
    assert out.group_by("metric").len()["len"].max() <= 2


# ---- selection rules on hand-made candidates ---------------------------------------------------
def _picks(rows: list[tuple[str, str, str, float]], max_items=5, min_items=3) -> list[tuple]:
    cands = pl.DataFrame(rows, schema=["player_id", "metric", "source", "score"], orient="row")
    picks = uh._select(cands, max_items, min_items)
    return [(p["player_id"], p["metric"]) for p in picks]


def test_select_caps_per_player_and_metric():
    picks = _picks(
        [
            ("p1", "m1", "NGS", 3.0),
            ("p1", "m2", "PFR", 2.9),  # same player: skipped
            ("p2", "m1", "NGS", 2.8),
            ("p3", "m1", "NGS", 2.7),  # third m1: skipped
            ("p4", "m2", "PFR", 2.0),
        ]
    )
    assert picks == [("p1", "m1"), ("p2", "m1"), ("p4", "m2")]


def test_select_thresholds_and_min_items():
    rows = [("p1", "m1", "NGS", 2.0), ("p2", "m2", "PFR", 1.4), ("p3", "m3", "NGS", 1.2)]
    rows += [("p4", "m4", "NGS", 0.9)]
    # Below MIN_SCORE only to reach min_items, never below FLOOR.
    assert _picks(rows, min_items=3) == [("p1", "m1"), ("p2", "m2"), ("p3", "m3")]
    assert _picks(rows, min_items=2) == [("p1", "m1"), ("p2", "m2")]
    assert _picks(rows, min_items=5) == [("p1", "m1"), ("p2", "m2"), ("p3", "m3")]


def test_select_adds_a_second_source_when_one_exists():
    rows = [(f"p{i}", f"m{i}", "NGS", 3.0 - i / 10) for i in range(5)]
    rows += [("q1", "m9", "FTN", 1.2), ("q2", "m9", "PFR", 0.5)]
    picks = _picks(rows, max_items=3)
    assert picks == [("p0", "m0"), ("p1", "m1"), ("q1", "m9")]  # last NGS pick replaced
    picks = _picks(rows[:2] + rows[-2:], max_items=5)
    assert picks == [("p0", "m0"), ("p1", "m1"), ("q1", "m9")]  # appended
    # No other source above FLOOR: stays single-source.
    assert _picks(rows[:5] + rows[-1:], max_items=3) == [("p0", "m0"), ("p1", "m1"), ("p2", "m2")]


def test_select_ties_break_by_player_id():
    picks = _picks([("pb", "m1", "NGS", 2.0), ("pa", "m2", "NGS", 2.0)], min_items=1)
    assert picks == [("pa", "m2"), ("pb", "m1")]


# ---- text ---------------------------------------------------------------------------------------
def test_ordinals():
    got = [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111)]
    assert got == ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd"] + [
        "101st",
        "111th",
    ]


def test_rank_note_text():
    sep, ttt = METRIC_BY_KEY["avg_separation"], METRIC_BY_KEY["avg_time_to_throw"]
    prs = METRIC_BY_KEY["pressures"]
    standout = {"kind": "standout", "pool": "WR", "side": "top", "side_rank": 1}
    assert rank_note(standout, sep) == "highest among WRs with 5+ targets"
    assert rank_note(standout | {"side_rank": 2}, sep) == "2nd-highest among WRs with 5+ targets"
    low = {"kind": "standout", "pool": "QB", "side": "bottom", "side_rank": 2}
    assert rank_note(low, ttt) == "2nd-lowest among QBs with 20+ attempts"
    most = {"kind": "standout", "pool": "PR", "side": "top", "side_rank": 1}
    assert rank_note(most, prs) == "most among pass rushers with 3+ pressures"
    riser = {"kind": "riser", "pool": "TE", "up_rank": 2}
    assert (
        rank_note(riser, sep) == "2nd-biggest jump over his own average among TEs with 5+ targets"
    )
    faller = {"kind": "faller", "pool": "WR", "down_rank": 1}
    assert rank_note(faller, sep) == "biggest drop below his own average among WRs with 5+ targets"


def test_rank_notes_in_output_have_integers_only(league):
    out = build_under_hood(SEASON, WEEK, league)
    notes = dict(zip(out["player_id"], out["rank_note"], strict=True))
    assert notes["00-A"] == "biggest jump over his own average among WRs with 5+ targets"
    assert notes["00-B"] == "2nd-biggest jump over his own average among WRs with 5+ targets"
    for note in out["rank_note"]:
        assert "." not in note


# ---- followed teams ---------------------------------------------------------------------------
def test_followed_boost_only_reorders_close_items(league):
    base = build_under_hood(SEASON, WEEK, league)
    score = dict(zip(base["player_id"], base["score"], strict=True))
    assert score["00-A"] > score["00-B"] > score["00-A"] / uh.FOLLOWED_BOOST  # close pair

    boosted = build_under_hood(SEASON, WEEK, league, followed=["buf"])
    rank = dict(zip(boosted["player_id"], boosted["rank"], strict=True))
    assert rank["00-B"] < rank["00-A"]

    top = base.row(0, named=True)
    far = base.filter(pl.col("score") * uh.FOLLOWED_BOOST < top["score"]).row(0, named=True)
    boosted = build_under_hood(SEASON, WEEK, league, followed=[far["team"]])
    rank = dict(zip(boosted["player_id"], boosted["rank"], strict=True))
    assert rank[top["player_id"]] < rank[far["player_id"]]


# ---- leakage ---------------------------------------------------------------------------------
def test_future_rows_do_not_change_the_output(league):
    key = AsOf(SEASON, WEEK)

    def build(tables):
        return build_under_hood(SEASON, WEEK, tables).with_columns(
            pl.lit(SEASON).alias("season"), pl.lit(WEEK).alias("week")
        )

    checked = assert_future_invariant(build, league, key, sort_by=["rank"])
    assert checked.height >= 3
