"""Placeholder watch list (nflengine.digest.watchlist) on a hand-made six-team league.

Teams AAA..FFF play each other in fixed pairs (AAA at BBB, CCC at DDD, EEE at FFF) every
week. 2025 weeks 1-4 are played, week 5 (the previewed week) is scheduled; 2024 weeks 1-4
exist for the last-season fallback. Week-5 defense ranks (1 = weakest, 6 teams):

    pass: AAA 1, BBB 2, CCC 3, DDD 4, EEE 5, FFF 6  (weakness share 1.0 .. 0.0)
    run:  FFF 1, EEE 2, DDD 3, CCC 4, BBB 5, AAA 6

so a WR on BBB (faces AAA) sees pass rank 1, an RB on BBB sees run rank 6, and so on.
Ratings rows of other weeks hold different values on purpose: only the as-of row for the
previewed week may be used.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl
import pytest

from nflengine.digest.watchlist import (
    BLEND_PSEUDO_GAMES,
    FOLLOWED_BOOST,
    OUTPUT_SCHEMA,
    RESULT_SCHEMA,
    _candidates,
    build_watchlist,
    score_results,
    score_watchlist,
    unavailable_players,
)

SEASON, WEEK = 2025, 5
SLATE = [("AAA", "BBB"), ("CCC", "DDD"), ("EEE", "FFF")]  # (away, home)
TEAMS = [t for pair in SLATE for t in pair]
PASS_EPA = {"AAA": 0.10, "BBB": 0.06, "CCC": 0.02, "DDD": -0.02, "EEE": -0.06, "FFF": -0.10}
RUSH_EPA = dict(zip(TEAMS, reversed(list(PASS_EPA.values())), strict=True))


@dataclass
class Player:
    pid: str
    team: str
    pos: str
    lines: dict[tuple[int, int], dict] = field(default_factory=dict)

    def play(
        self,
        season: int,
        weeks,
        *,
        ts: float = 0.0,
        car: int = 0,
        snap: float = 0.8,
        rec: float = 0.0,
        rush: float = 0.0,
        stats: bool = True,
        snaps: bool = True,
    ) -> Player:
        for w in weeks:
            self.lines[(season, w)] = dict(
                ts=ts, car=car, snap=snap, rec=rec, rush=rush, stats=stats, snaps=snaps
            )
        return self


def wr(pid: str, team: str, before: float, recent: float, **kw) -> Player:
    """A receiver whose target share goes `before` (weeks 1-2) -> `recent` (weeks 3-4)."""
    return (
        Player(pid, team, kw.pop("pos", "WR"))
        .play(SEASON, [1, 2], ts=before, **kw)
        .play(SEASON, [3, 4], ts=recent, **kw)
    )


def rb(pid: str, team: str, before: int, recent: int, **kw) -> Player:
    """A back whose carries go `before` -> `recent` per game (weeks 1-2 -> 3-4)."""
    return (
        Player(pid, team, "RB")
        .play(SEASON, [1, 2], car=before, **kw)
        .play(SEASON, [3, 4], car=recent, **kw)
    )


_GAME_SCHEMA = {
    "game_id": pl.String,
    "season": pl.Int32,
    "week": pl.Int32,
    "game_type": pl.String,
    "home_team": pl.String,
    "away_team": pl.String,
    "completed": pl.Boolean,
}
_RATING_SCHEMA = {
    "season": pl.Int32,
    "week": pl.Int32,
    "team": pl.String,
    "def_pass_epa": pl.Float64,
    "def_rush_epa": pl.Float64,
}
_PG_SCHEMA = {
    "player_id": pl.String,
    "player_display_name": pl.String,
    "position": pl.String,
    "team": pl.String,
    "season": pl.Int32,
    "week": pl.Int32,
    "game_id": pl.String,
    "carries": pl.Int32,
    "targets": pl.Int32,
    "target_share": pl.Float64,
    "receiving_yards": pl.Float64,
    "rushing_yards": pl.Float64,
}
_SNAP_SCHEMA = {
    "gsis_id": pl.String,
    "game_id": pl.String,
    "season": pl.Int32,
    "week": pl.Int32,
    "team": pl.String,
    "position": pl.String,
    "player": pl.String,
    "offense_snaps": pl.Float64,
    "offense_pct": pl.Float64,
}


def _frames(
    players: list[Player],
    *,
    week: int = WEEK,
    future: int = 0,
    drop: tuple[tuple[int, int, str], ...] = (),
) -> dict[str, pl.DataFrame]:
    """games / player_games / snaps / ratings for the league (weeks up to `week` + future)."""
    games, gid = [], {}
    for season, last in ((SEASON - 1, 4), (SEASON, week + future)):
        for w in range(1, last + 1):
            for away, home in SLATE:
                if (season, w, away) in drop:
                    continue
                game_id = f"{season}_{w:02d}_{away}_{home}"
                gid[(season, w, away)] = gid[(season, w, home)] = game_id
                games.append(
                    {
                        "game_id": game_id,
                        "season": season,
                        "week": w,
                        "game_type": "REG",
                        "home_team": home,
                        "away_team": away,
                        "completed": season < SEASON or w < week,
                    }
                )
    pgs, sns = [], []
    for p in players:
        for (season, w), ln in p.lines.items():
            game_id = gid.get((season, w, p.team))
            if game_id is None or w > week + future:
                continue
            if ln["stats"]:
                pgs.append(
                    {
                        "player_id": p.pid,
                        "player_display_name": f"Name {p.pid}",
                        "position": p.pos,
                        "team": p.team,
                        "season": season,
                        "week": w,
                        "game_id": game_id,
                        "carries": ln["car"],
                        "targets": round(ln["ts"] * 30),
                        "target_share": ln["ts"],
                        "receiving_yards": ln["rec"],
                        "rushing_yards": ln["rush"],
                    }
                )
            if ln["snaps"]:
                sns.append(
                    {
                        "gsis_id": p.pid,
                        "game_id": game_id,
                        "season": season,
                        "week": w,
                        "team": p.team,
                        "position": p.pos,
                        "player": f"Name {p.pid}",
                        "offense_snaps": round(ln["snap"] * 60.0),
                        "offense_pct": ln["snap"],
                    }
                )
    ratings = []
    for w in range(1, week + future + 1):
        for t in TEAMS:
            if w < week:  # stale rows: the opposite ordering
                row = (-PASS_EPA[t], -RUSH_EPA[t])
            elif w == week:
                row = (PASS_EPA[t], RUSH_EPA[t])
            else:  # future rows: everyone terrible
                row = (9.0, 9.0)
            ratings.append(
                {
                    "season": SEASON,
                    "week": w,
                    "team": t,
                    "def_pass_epa": row[0],
                    "def_rush_epa": row[1],
                }
            )
    out = {
        "games": pl.DataFrame(games, schema=_GAME_SCHEMA),
        "player_games": pl.DataFrame(pgs, schema=_PG_SCHEMA),
        "snaps": pl.DataFrame(sns, schema=_SNAP_SCHEMA),
        "ratings": pl.DataFrame(ratings, schema=_RATING_SCHEMA),
    }
    return out


def _run(players: list[Player], *, week: int = WEEK, n_items: int = 10, drop=(), **kw):
    f = _frames(players, week=week, drop=drop)
    return build_watchlist(
        f["games"], f["player_games"], f["snaps"], f["ratings"], SEASON, week, n_items=n_items, **kw
    )


def _row(df: pl.DataFrame, pid: str) -> dict:
    return df.filter(pl.col("player_id") == pid).row(0, named=True)


def _ids(df: pl.DataFrame) -> list[str]:
    return df["player_id"].to_list()


# ---- ranking -----------------------------------------------------------------------------------
def test_ranks_by_usage_increase_and_drops_flat_players():
    players = [
        wr("big", "CCC", 0.10, 0.35),
        wr("small", "CCC", 0.20, 0.25),
        wr("flat", "CCC", 0.25, 0.25),
        wr("down", "CCC", 0.30, 0.20),
    ]
    out = _run(players)
    assert _ids(out) == ["big", "small"]  # flat (0) and down (< 0) don't qualify
    assert out["rank"].to_list() == [1, 2]
    big = _row(out, "big")
    assert big["usage_metric"] == "target share"
    assert big["usage_before"] == pytest.approx(0.10)
    assert big["usage_recent"] == pytest.approx(0.35)
    assert big["usage_before_source"] == "season"
    assert big["recent_games"] == 2
    assert big["snap_share_recent"] == pytest.approx(0.8)
    assert out["score"][0] > out["score"][1] > 0
    assert out.columns == list(OUTPUT_SCHEMA)
    assert out.schema == pl.Schema(OUTPUT_SCHEMA)


def test_output_fields_describe_the_matchup():
    back = rb("r", "EEE", 8, 14, rush=50.0, rec=10.0)
    out = _run([wr("a", "AAA", 0.1, 0.3, rec=60.0), back, rb("r_o", "EEE", 12, 10)])
    a, r = _row(out, "a"), _row(out, "r")
    assert (a["opponent"], a["home"], a["game_id"]) == ("BBB", False, "2025_05_AAA_BBB")
    assert (r["opponent"], r["home"], r["game_id"]) == ("FFF", False, "2025_05_EEE_FFF")
    assert (a["season"], a["week"], a["confidence"], a["source"]) == (2025, 5, "low", "heuristic")
    assert (a["target"], a["target_col"]) == ("receiving yards", "receiving_yards")
    assert (r["target"], r["target_col"]) == ("scrimmage yards", "scrimmage_yards")
    assert a["position"] == "WR" and r["position"] == "RB"
    assert a["player"] == "Name a"
    # baseline: mean of the target stat over the last (up to 4) games
    assert a["baseline"] == pytest.approx(60.0) and a["baseline_games"] == 4
    assert r["baseline"] == pytest.approx(60.0)  # rushing 50 + receiving 10


def test_ties_break_on_player_id():
    players = [wr("p_b", "CCC", 0.1, 0.3), wr("p_a", "CCC", 0.1, 0.3)]
    assert _ids(_run(players)) == ["p_a", "p_b"]
    assert _ids(_run(players, n_items=1)) == ["p_a"]
    assert _ids(_run(players[::-1], n_items=1)) == ["p_a"]


def test_rb_uses_the_larger_of_carry_share_and_target_share_gain():
    # carry share 10/20 -> 14/24 (+0.083); target share 0.05 -> 0.30 (+0.25): targets win
    receiving_back = rb("rb_t", "EEE", 10, 14).play(SEASON, [1, 2], car=10, ts=0.05)
    receiving_back.play(SEASON, [3, 4], car=14, ts=0.30)
    other = rb("rb_o", "EEE", 10, 10)
    out = _run([receiving_back, other])
    row = _row(out, "rb_t")
    assert row["usage_metric"] == "target share"
    assert row["usage_before"] == pytest.approx(0.05)
    assert row["usage_recent"] == pytest.approx(0.30)
    # without the target-share bump the carry share is the metric
    plain = _run([rb("rb_c", "EEE", 10, 14), rb("rb_o", "EEE", 10, 10)])
    row = _row(plain, "rb_c")
    assert row["usage_metric"] == "carry share"
    assert row["usage_before"] == pytest.approx(0.5)
    assert row["usage_recent"] == pytest.approx(14 / 24)


def test_blocking_tight_end_with_snaps_but_no_stat_rows_counts_as_zero_usage():
    te = (
        Player("te", "CCC", "TE")
        .play(SEASON, [1, 2], snap=0.7, stats=False)
        .play(SEASON, [3, 4], ts=0.30, snap=0.7)
    )
    row = _row(_run([te]), "te")
    assert row["usage_before"] == pytest.approx(0.0)
    assert row["usage_recent"] == pytest.approx(0.30)


def test_low_snap_share_players_are_not_candidates():
    out = _run([wr("part_timer", "CCC", 0.05, 0.30, snap=0.2), wr("starter", "CCC", 0.1, 0.2)])
    assert _ids(out) == ["starter"]


# ---- opponent weakness ---------------------------------------------------------------------------
def test_weak_opponent_defense_multiplies_the_score():
    # same usage jump; BBB's WR faces AAA (pass rank 1, share 1.0), EEE's faces FFF (rank 6, 0.0)
    out = _run([wr("vs_weak", "BBB", 0.15, 0.30), wr("vs_strong", "EEE", 0.15, 0.30)])
    weak, strong = _row(out, "vs_weak"), _row(out, "vs_strong")
    assert (weak["opp_def_rank"], strong["opp_def_rank"]) == (1, 6)
    assert weak["opp_def_unit"] == strong["opp_def_unit"] == "pass defense"
    assert weak["score"] / strong["score"] == pytest.approx((0.5 + 1.0) / (0.5 + 0.0))
    assert _ids(out) == ["vs_weak", "vs_strong"]


def test_receivers_use_pass_defense_and_backs_use_run_defense():
    out = _run([wr("rec", "BBB", 0.1, 0.3), rb("back", "BBB", 8, 14), rb("back2", "BBB", 12, 10)])
    rec, back = _row(out, "rec"), _row(out, "back")
    assert (rec["opp_def_rank"], rec["opp_def_unit"]) == (1, "pass defense")  # AAA: worst pass D
    assert (back["opp_def_rank"], back["opp_def_unit"]) == (6, "run defense")  # AAA: best run D


def test_only_the_as_of_ratings_row_of_the_previewed_week_is_used():
    # the stale (earlier-week) rows hold the opposite ordering and the future rows are all 9.0
    out = _run([wr("rec", "BBB", 0.1, 0.3)])
    assert _row(out, "rec")["opp_def_rank"] == 1


def test_followed_team_gets_a_modest_boost():
    players = [wr("mine", "BBB", 0.15, 0.30), wr("other", "BBB", 0.15, 0.30, pos="TE")]
    base = _run(players)
    boosted = _run(players, followed=["BBB"])
    ratio = _row(boosted, "mine")["score"] / _row(base, "mine")["score"]
    assert ratio == pytest.approx(FOLLOWED_BOOST)
    # a followed team's weak signal does not beat a much stronger one from an unfollowed team
    mixed = _run(
        [
            wr("strong", "BBB", 0.10, 0.40),
            wr("meh", "CCC", 0.20, 0.22),
            wr("flat", "CCC", 0.2, 0.2),
        ],
        followed=["CCC"],
    )
    assert _ids(mixed)[0] == "strong"


# ---- selection -----------------------------------------------------------------------------------
def test_at_most_two_players_per_team():
    players = [
        wr("a1", "AAA", 0.10, 0.40),
        wr("a2", "AAA", 0.10, 0.38),
        wr("a3", "AAA", 0.10, 0.36, pos="TE"),
        wr("c1", "CCC", 0.10, 0.20),
        rb("r1", "EEE", 8, 14),
        rb("r2", "EEE", 12, 10),
    ]
    out = _run(players, n_items=4)
    assert out.height == 4
    assert out["team"].value_counts()["count"].max() <= 2
    assert sorted(_ids(out)) == ["a1", "a2", "c1", "r1"]
    assert out["rank"].to_list() == [1, 2, 3, 4]
    assert out["score"].to_list() == sorted(out["score"].to_list(), reverse=True)


def test_best_back_is_kept_even_when_receivers_score_higher():
    players = [
        wr("w1", "AAA", 0.10, 0.40),
        wr("w2", "BBB", 0.10, 0.38),
        wr("w3", "CCC", 0.10, 0.36),
        wr("w4", "DDD", 0.10, 0.34),
        rb("r1", "EEE", 10, 14),
        rb("r2", "EEE", 10, 10),
    ]
    cands = _candidates(*_pool(players))
    assert cands.filter(pl.col("position") == "RB")["score"].max() < cands["score"].sort()[-3]
    out = _run(players, n_items=3)
    assert "r1" in _ids(out)
    assert out["position"].value_counts().filter(pl.col("position") == "WR")["count"][0] == 2


def test_best_receiver_is_kept_even_when_backs_score_higher():
    # the receiver group has a big decliner, so its small riser has a low z score
    players = [
        wr("w_up", "DDD", 0.20, 0.21),
        wr("w_down", "CCC", 0.40, 0.10),
        rb("r1", "AAA", 10, 20),
        rb("r1b", "AAA", 10, 10),
        rb("r2", "BBB", 10, 19),
        rb("r2b", "BBB", 10, 10),
        rb("r3", "CCC", 10, 18),
        rb("r3b", "CCC", 10, 10),
    ]
    cands = _candidates(*_pool(players))
    rb_scores = cands.filter(pl.col("position") == "RB")["score"].sort(descending=True)
    assert cands.filter(pl.col("position") == "WR")["score"].max() < rb_scores[2]
    out = _run(players, n_items=3)
    assert "w_up" in _ids(out) and out.filter(pl.col("position") == "RB").height == 2


def _pool(players: list[Player], week: int = WEEK):
    f = _frames(players, week=week)
    return f["games"], f["player_games"], f["snaps"], f["ratings"], SEASON, week


# ---- who is eligible ----------------------------------------------------------------------------
def test_player_who_missed_his_teams_last_game_is_excluded():
    def with_2024(pid: str) -> Player:  # a last-season history, so only the last game rule bites
        return Player(pid, "CCC", "WR").play(SEASON - 1, [1, 2, 3, 4], ts=0.10)

    out = _run(
        [
            with_2024("played").play(SEASON, [1, 2], ts=0.10).play(SEASON, [3, 4], ts=0.30),
            with_2024("no_row").play(SEASON, [1, 2, 3], ts=0.30),  # nothing in week 4
            with_2024("inactive")
            .play(SEASON, [1, 2], ts=0.10)
            .play(SEASON, [3], ts=0.30)
            .play(SEASON, [4], snap=0.0, stats=False),  # a snap row with zero offensive snaps
        ]
    )
    assert _ids(out) == ["played"]


def test_bye_week_uses_the_teams_most_recent_game_before_the_week():
    # AAA and BBB are off in week 4: AAA's last game is week 3, so a week-3 player still counts,
    # but the recent window is then weeks 2-3 and week 1 is the only earlier game of the season
    riser = (
        Player("aaa_wr", "AAA", "WR")
        .play(SEASON - 1, [1, 2, 3, 4], ts=0.10)
        .play(SEASON, [1], ts=0.10)
        .play(SEASON, [2, 3], ts=0.30)
    )
    out = _run([riser, wr("ccc_wr", "CCC", 0.10, 0.30)], drop=((SEASON, 4, "AAA"),))
    row = _row(out, "aaa_wr")
    assert row["usage_before_source"] == "last_season"  # only one earlier game this season
    assert row["usage_recent"] == pytest.approx(0.30)
    assert row["usage_before"] == pytest.approx(0.10)
    assert "ccc_wr" in _ids(out)


def test_team_on_a_bye_in_the_previewed_week_has_no_candidates():
    out = _run([wr("a", "AAA", 0.1, 0.3), wr("c", "CCC", 0.1, 0.3)], drop=((SEASON, 5, "AAA"),))
    assert _ids(out) == ["c"]


def test_players_unavailable_per_the_injury_report_are_dropped():
    risers = {t: wr(f"r_{t}", t, 0.10, 0.30) for t in ("AAA", "BBB", "CCC", "DDD", "EEE")}
    players = list(risers.values())
    injuries = pl.DataFrame(
        {
            "gsis_id": ["r_BBB", "r_CCC", "r_DDD", "r_AAA"],
            "season": [2025, 2025, 2025, 2025],
            "week": [5, 5, 5, 4],
            "report_status": ["Out", "Doubtful", "Questionable", "Out"],
        }
    )
    rosters = pl.DataFrame(
        {
            "gsis_id": ["r_EEE", "r_AAA"],
            "season": [2025, 2025],
            "week": [5, 5],
            "status": ["RES", "ACT"],
        }
    )
    kw = dict(injuries=injuries, rosters=rosters)
    assert sorted(_ids(_run(players))) == sorted(f"r_{t}" for t in risers)  # not asked: all in
    # week-5 report: Out (BBB), Doubtful (CCC) and reserve list (EEE) drop; Questionable stays
    assert sorted(_ids(_run(players, injury_week=5, **kw))) == ["r_AAA", "r_DDD"]
    # the week-4 report only drops its own Out player
    assert sorted(_ids(_run(players, injury_week=4, **kw))) == ["r_BBB", "r_CCC", "r_DDD", "r_EEE"]
    assert unavailable_players(injuries, rosters, 2025, 5) == {"r_BBB", "r_CCC", "r_EEE"}
    assert unavailable_players(None, None, 2025, 5) == set()


# ---- windows: last-season fallback, baseline blend ----------------------------------------------
def _veteran(pid: str = "vet", **kw) -> Player:
    """40 receiving yards a game and a 0.10 target share in 2024; 80 yards in 2025 weeks 1-2."""
    return (
        Player(pid, "CCC", "WR")
        .play(SEASON - 1, [1, 2, 3, 4], ts=0.10, rec=40.0)
        .play(SEASON, [1, 2], ts=0.25, rec=80.0)
    )


def test_last_season_average_is_the_before_window_early_in_the_season():
    out = _run([_veteran()], week=3)
    row = _row(out, "vet")
    assert row["usage_before_source"] == "last_season"
    assert row["usage_before"] == pytest.approx(0.10)
    assert row["usage_recent"] == pytest.approx(0.25)
    assert row["recent_games"] == 2
    # two games this season: baseline is just their mean (no blend)
    assert (row["baseline"], row["baseline_games"]) == (pytest.approx(80.0), 2)


def test_baseline_blends_with_last_season_when_under_two_games_this_season():
    row = _row(_run([_veteran()], week=2), "vet")
    assert (row["recent_games"], row["baseline_games"]) == (1, 1)
    assert row["usage_before_source"] == "last_season"
    blended = (1 * 80.0 + BLEND_PSEUDO_GAMES * 40.0) / (1 + BLEND_PSEUDO_GAMES)
    assert row["baseline"] == pytest.approx(blended)


def test_player_without_a_before_window_is_skipped():
    rookie = Player("rookie", "CCC", "WR").play(SEASON, [1, 2], ts=0.30)  # no 2024, 2 games
    one_game_last_year = (
        Player("thin", "CCC", "WR").play(SEASON - 1, [1], ts=0.05).play(SEASON, [1, 2], ts=0.30)
    )
    assert _run([rookie, one_game_last_year, _veteran()], week=3)["player_id"].to_list() == ["vet"]


def test_season_before_window_is_used_once_two_earlier_games_exist():
    row = _row(_run([wr("w", "CCC", 0.10, 0.30)]), "w")
    assert row["usage_before_source"] == "season"
    assert row["baseline_games"] == 4  # last four games this season
    assert row["usage_before"] == pytest.approx(0.10)  # weeks 1-2, not last season


def test_baseline_uses_at_most_the_last_four_games_of_this_season():
    p = (
        Player("long", "CCC", "WR")
        .play(SEASON, [1, 2], ts=0.10, rec=500.0)  # old games outside the baseline window
        .play(SEASON, [3, 4, 5, 6], ts=0.20, rec=50.0)
        .play(SEASON, [7, 8], ts=0.35, rec=70.0)
    )
    f = _frames([p], week=9)
    out = build_watchlist(f["games"], f["player_games"], f["snaps"], f["ratings"], SEASON, 9)
    row = _row(out, "long")
    assert row["baseline_games"] == 4
    assert row["baseline"] == pytest.approx((50.0 + 50.0 + 70.0 + 70.0) / 4)


# ---- empty case ---------------------------------------------------------------------------------
def test_empty_frame_with_the_schema_when_nothing_qualifies():
    week1 = _run([wr("a", "CCC", 0.1, 0.3)], week=1)  # no earlier games this season
    assert week1.is_empty() and week1.schema == pl.Schema(OUTPUT_SCHEMA)
    flat = _run([wr("flat", "CCC", 0.2, 0.2)])
    assert flat.is_empty() and flat.schema == pl.Schema(OUTPUT_SCHEMA)
    assert _run([]).schema == pl.Schema(OUTPUT_SCHEMA)


# ---- scoring a saved list -----------------------------------------------------------------------
def test_score_results_hit_and_not_played_logic():
    ids = ["hit", "miss", "rb_tie", "rb_hit", "dnp", "zero_snaps", "snap_only", "box_only"]
    watch = pl.DataFrame(
        {
            "player_id": ids,
            "game_id": ["g"] * len(ids),
            "target_col": ["receiving_yards"] * 2
            + ["scrimmage_yards"] * 2
            + ["receiving_yards"] * 4,
            "baseline": [60.0, 60.0, 70.0, 69.5, 60.0, 60.0, 10.0, 20.0],
        }
    )
    box = pl.DataFrame(
        {
            "player_id": ["hit", "miss", "rb_tie", "rb_hit", "zero_snaps", "box_only", "other"],
            "game_id": ["g"] * 6 + ["other_game"],
            "receiving_yards": [90.0, 30.0, 20.0, 20.0, 5.0, 25.0, 99.0],
            "rushing_yards": [0.0, 0.0, 50.0, 50.0, 0.0, 0.0, 0.0],
        }
    )
    snaps = pl.DataFrame(
        {
            "gsis_id": ["hit", "miss", "rb_tie", "rb_hit", "zero_snaps", "snap_only", None],
            "game_id": ["g"] * 7,
            "offense_snaps": [40.0, 40.0, 30.0, 30.0, 0.0, 30.0, 12.0],
        },
        schema={"gsis_id": pl.String, "game_id": pl.String, "offense_snaps": pl.Float64},
    )
    out = score_results(watch, box, snaps).sort("player_id")
    got = {r["player_id"]: r for r in out.iter_rows(named=True)}
    assert (got["hit"]["actual"], got["hit"]["played"], got["hit"]["hit"]) == (90.0, True, True)
    assert (got["miss"]["actual"], got["miss"]["hit"]) == (30.0, False)
    assert (got["rb_tie"]["actual"], got["rb_tie"]["hit"]) == (70.0, False)  # equal is not a hit
    assert (got["rb_hit"]["actual"], got["rb_hit"]["hit"]) == (70.0, True)  # rush + receiving
    for pid in ("dnp", "zero_snaps"):  # no rows at all / a box row but zero offensive snaps
        assert (got[pid]["actual"], got[pid]["played"], got[pid]["hit"]) == (None, False, None)
    assert (got["snap_only"]["actual"], got["snap_only"]["played"]) == (
        0.0,
        True,
    )  # played, no stats
    assert got["snap_only"]["hit"] is False
    assert (got["box_only"]["actual"], got["box_only"]["played"], got["box_only"]["hit"]) == (
        25.0,
        True,
        True,
    )
    assert out.columns == [*watch.columns, *RESULT_SCHEMA]
    assert out.height == watch.height  # one row per watched player
    assert out.schema["hit"] == pl.Boolean and out.schema["actual"] == pl.Float64


def test_score_watchlist_of_an_empty_list_needs_no_data():
    out = score_watchlist(pl.DataFrame(schema=OUTPUT_SCHEMA))
    assert out.is_empty()
    assert out.columns == [*OUTPUT_SCHEMA, *RESULT_SCHEMA]
    assert out.schema["actual"] == pl.Float64 and out.schema["hit"] == pl.Boolean


def test_scoring_a_built_list_end_to_end():
    players = [
        wr("a", "CCC", 0.10, 0.30, rec=60.0).play(SEASON, [5], ts=0.3, rec=75.0),
        rb("r", "EEE", 8, 14, rush=50.0),
        rb("r_o", "EEE", 12, 10),
    ]
    watch = _run(players)
    assert {"a", "r"} <= set(_ids(watch))
    results = _frames(players, future=1)  # now with the week-5 box score
    in_week = {k: results[k].filter(pl.col("week") == WEEK) for k in ("player_games", "snaps")}
    out = score_results(watch, in_week["player_games"], in_week["snaps"])
    a, r = _row(out, "a"), _row(out, "r")
    assert (a["actual"], a["played"], a["hit"]) == (75.0, True, True)  # 75 > 60
    assert (r["actual"], r["played"], r["hit"]) == (None, False, None)  # no week-5 rows


# ---- leakage ------------------------------------------------------------------------------------
def _scramble(df: pl.DataFrame, *, week: int, strict: bool = False) -> pl.DataFrame:
    """Junk every numeric / boolean value in 2025 rows from `week` on (`strict`: after it)."""
    future = (pl.col("season") == SEASON) & (
        (pl.col("week") > week) if strict else (pl.col("week") >= week)
    )
    junk = []
    for c, dtype in df.schema.items():
        if c in ("season", "week"):
            continue
        if dtype == pl.Boolean:
            junk.append(pl.when(future).then(~pl.col(c)).otherwise(pl.col(c)).alias(c))
        elif dtype.is_numeric():
            flipped = (-pl.col(c) * 7 + 13).cast(dtype, strict=False)
            junk.append(pl.when(future).then(flipped).otherwise(pl.col(c)).alias(c))
    return df.with_columns(junk)


def _league() -> list[Player]:
    # everyone also has (loud) results for weeks 5 and 6 that a leaky build would pick up
    players = [
        wr("w1", "AAA", 0.10, 0.30, rec=70.0),
        wr("w2", "BBB", 0.20, 0.30, rec=55.0),
        wr("te", "CCC", 0.08, 0.20, pos="TE", rec=35.0),
        rb("r1", "DDD", 8, 14, rush=60.0, rec=15.0),
        rb("r1b", "DDD", 12, 10, rush=30.0),
        rb("r2", "EEE", 10, 15, rush=70.0),
        rb("r2b", "EEE", 10, 9),
        wr("fa", "FFF", 0.1, 0.1),
    ]
    for p in players:
        p.play(SEASON, [5, 6], ts=0.95, car=60, snap=0.99, rec=300.0, rush=300.0)
    return players


def test_output_does_not_depend_on_anything_from_the_previewed_week_on():
    players = _league()
    f = _frames(players, week=WEEK, future=2)
    injuries = pl.DataFrame(
        {"gsis_id": ["w2"], "season": [2025], "week": [5], "report_status": ["Out"]}
    )
    rosters = pl.DataFrame({"gsis_id": ["r2b"], "season": [2025], "week": [5], "status": ["RES"]})

    def build(g, pg, sn, rt):
        return build_watchlist(
            g,
            pg,
            sn,
            rt,
            SEASON,
            WEEK,
            n_items=6,
            followed=["EEE"],
            injuries=injuries,
            rosters=rosters,
            injury_week=WEEK,
        )

    base = build(f["games"], f["player_games"], f["snaps"], f["ratings"])
    assert base.height >= 4 and "w2" not in _ids(base)

    # the same inputs without any result from week 5 on (the schedule and as-of ratings stay)
    past = build(
        f["games"].filter(pl.col("week") <= WEEK),
        f["player_games"].filter(pl.col("week") < WEEK),
        f["snaps"].filter(pl.col("week") < WEEK),
        f["ratings"].filter(pl.col("week") <= WEEK),
    )
    assert base.equals(past)

    # ... and with every numeric value from week >= N scrambled (ratings: only weeks after N,
    # since the as-of row for week N is built from earlier games and is allowed)
    scrambled = build(
        _scramble(f["games"], week=WEEK),
        _scramble(f["player_games"], week=WEEK),
        _scramble(f["snaps"], week=WEEK),
        _scramble(f["ratings"], week=WEEK, strict=True),
    )
    assert base.equals(scrambled)

    # the check has teeth: scrambling the as-of ratings row or the past games does change it
    assert not base.equals(
        build(f["games"], f["player_games"], f["snaps"], _scramble(f["ratings"], week=WEEK))
    )
    assert not base.equals(
        build(f["games"], _scramble(f["player_games"], week=4), f["snaps"], f["ratings"])
    )


def test_usage_windows_only_count_games_with_his_current_team():
    """P04 fact-check: a traded player's earlier share belongs to another offense."""
    before_trade = Player("mover", "AAA", "WR").play(SEASON, [1, 2], ts=0.05)
    after_trade = Player("mover", "CCC", "WR").play(SEASON, [3, 4], ts=0.30)
    # no same-team "before" games this season or last: skipped, not compared across teams
    assert "mover" not in _ids(_run([before_trade, after_trade]))
    # with two earlier games for his current team, those are the comparison
    stayed = Player("mover", "CCC", "WR").play(SEASON - 1, [1, 2], ts=0.12)
    row = _row(_run([before_trade, after_trade, stayed]), "mover")
    assert row["usage_before_source"] == "last_season"
    assert row["usage_before"] == pytest.approx(0.12)  # CCC last season, not AAA's 0.05
