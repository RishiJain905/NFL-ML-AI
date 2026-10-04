"""Tests for `features/player_opponent.py` (P06): leakage, lag, over-expectation sign, ratings.

The synthetic league and the scrambling helper come from `test_player_efficiency.py`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import polars as pl
import pytest

from nflengine.features.player_data import player_history
from nflengine.features.player_opponent import (
    ALLOWED,
    LATE_FEATURES,
    OE_STATS,
    OPP_FEATURES,
    RATINGS,
    opponent_features,
)

try:  # pytest puts the tests directory on sys.path
    from test_player_efficiency import (
        BOX_FRAMES,
        LATE_FRAMES,
        T0,
        assert_same,
        changed_columns,
        empty_inputs,
        make_world,
        scrambled,
        tk,
    )
except ImportError:  # pragma: no cover  (package-style layout)
    from tests.test_player_efficiency import (
        BOX_FRAMES,
        LATE_FRAMES,
        T0,
        assert_same,
        changed_columns,
        empty_inputs,
        make_world,
        scrambled,
        tk,
    )


@pytest.fixture(scope="module")
def world():
    return make_world()


# ---- shape ------------------------------------------------------------------------------------


def test_columns_dtypes_and_order(world):
    inp, hist, rows = world
    out = opponent_features(inp, hist, rows)
    assert out.columns == ["player_id", "game_id", *OPP_FEATURES]
    assert out.height == rows.height
    assert out["player_id"].equals(rows["player_id"])
    assert out["game_id"].equals(rows["game_id"])
    assert all(out.schema[c] == pl.Float64 for c in OPP_FEATURES)
    assert len(set(OPP_FEATURES)) == len(OPP_FEATURES)
    assert all(f.startswith("opp_") for f in OPP_FEATURES)
    assert set(LATE_FEATURES) <= set(OPP_FEATURES)
    assert set(ALLOWED.values()) <= {*OE_STATS, *[f"{s}_oe" for s in OE_STATS]}


def test_row_order_is_kept_for_shuffled_rows(world):
    inp, hist, rows = world
    base = opponent_features(inp, hist, rows)
    shuffled = rows.sample(fraction=1.0, shuffle=True, seed=4)
    out = opponent_features(inp, hist, shuffled)
    assert out["player_id"].equals(shuffled["player_id"])
    merged = out.join(base, on=["player_id", "game_id"], suffix="_b")
    for c in OPP_FEATURES:
        assert merged[c].equals(merged[f"{c}_b"]), c


def test_every_row_gets_features_even_for_upcoming_games(world):
    inp, hist, rows = world
    out = opponent_features(inp, hist, rows).join(
        rows.select("player_id", "game_id", "t"), on=["player_id", "game_id"]
    )
    week8 = out.filter(pl.col("t") == tk(2022, 8))
    assert week8.height == 32
    for c in OPP_FEATURES:
        assert week8[c].null_count() == 0, c
    # the first season has too little history for the rolling means
    first = out.filter(pl.col("t") == tk(2021, 1))
    assert first["opp_rec_yds_allowed_wr_l8"].null_count() == first.height
    assert first["opp_def_pass_epa"].null_count() == 0  # a rating row exists from week 1


# ---- leakage ----------------------------------------------------------------------------------


def test_future_invariance(world):
    """Scramble every numeric input of games at t >= T (all frames; rating rows from T + 1):
    the opponent features of rows with t <= T don't move; those after T do."""
    inp, hist, rows = world
    cut = tk(2022, 4)
    base = opponent_features(inp, hist, rows)
    inp2 = scrambled(inp, cut)
    out = opponent_features(inp2, player_history(inp2), rows)
    before = rows["t"] <= cut
    assert before.sum() > 100
    assert_same(base.filter(before), out.filter(before), OPP_FEATURES)
    after = rows["t"] > cut
    assert len(changed_columns(base.filter(after), out.filter(after), OPP_FEATURES)) > 20


def test_future_invariance_at_other_cuts(world):
    inp, hist, rows = world
    base = opponent_features(inp, hist, rows)
    for cut in (tk(2021, 5), tk(2022, 1), tk(2022, 6)):
        inp2 = scrambled(inp, cut, seed=cut)
        out = opponent_features(inp2, player_history(inp2), rows)
        before = rows["t"] <= cut
        assert_same(base.filter(before), out.filter(before), OPP_FEATURES)


def test_pfr_and_ftn_arrive_a_week_late(world):
    """PFR / FTN of week T-1 stay out of week T and reach week T+1; box-score rates of week
    T-1 reach week T."""
    inp, hist, rows = world
    cut = tk(2022, 4)
    base = opponent_features(inp, hist, rows)
    early = [c for c in OPP_FEATURES if c not in LATE_FEATURES]
    t = rows["t"]

    late_in = scrambled(inp, cut - 1, cut - 1, frames=LATE_FRAMES)
    late = opponent_features(late_in, player_history(late_in), rows)
    assert_same(base.filter(t <= cut), late.filter(t <= cut), OPP_FEATURES)
    assert_same(base, late, early)
    changed = changed_columns(base.filter(t == cut + 1), late.filter(t == cut + 1), LATE_FEATURES)
    assert set(changed) == set(LATE_FEATURES)

    box_in = scrambled(inp, cut - 1, cut - 1, frames=BOX_FRAMES)
    box = opponent_features(box_in, player_history(box_in), rows)
    assert_same(base.filter(t < cut), box.filter(t < cut), OPP_FEATURES)
    assert len(changed_columns(base.filter(t == cut), box.filter(t == cut), early)) > 10


def test_rating_rows_are_the_rows_own_week_and_never_later(world):
    """A rating row (season, week) is built from earlier weeks (D44): a row at that week reads
    it directly; a later rating row is scrambled without moving earlier rows."""
    inp, hist, rows = world
    out = opponent_features(inp, hist, rows).join(
        rows.select("player_id", "game_id", "season", "week", "opponent"),
        on=["player_id", "game_id"],
    )
    tr = inp.team_ratings.select(
        pl.col("season"),
        pl.col("week"),
        pl.col("team").alias("opponent"),
        "off_epa",
        "def_rush_epa",
    )
    # weeks with a rating row (everything up to 2022 week 7): the same-week row
    same = out.join(tr, on=["season", "week", "opponent"], suffix="_r")
    no_row = (pl.col("season") == 2022) & (pl.col("week") == 8)
    assert same.height == out.height - out.filter(no_row).height
    assert same["opp_off_epa"].to_list() == pytest.approx(same["off_epa"].to_list())
    assert same["opp_def_rush_epa"].to_list() == pytest.approx(same["def_rush_epa"].to_list())
    # week 8 of 2022 has no rating row: it falls back to the team's latest earlier row (week 7)
    wk8 = out.filter((pl.col("season") == 2022) & (pl.col("week") == 8))
    wk7 = tr.filter((pl.col("season") == 2022) & (pl.col("week") == 7)).select(
        "opponent", pl.col("off_epa").alias("off_epa_7")
    )
    j = wk8.join(wk7, on="opponent")
    assert j.height == wk8.height
    assert j["opp_off_epa"].to_list() == pytest.approx(j["off_epa_7"].to_list())
    assert set(RATINGS.values()) <= set(OPP_FEATURES)


# ---- values (hand-built league) ---------------------------------------------------------------

_HIST_SCHEMA = {
    "game_id": pl.String,
    "team": pl.String,
    "opponent": pl.String,
    "t": pl.Int32,
    "pgroup": pl.String,
    "main_qb": pl.Boolean,
    **dict.fromkeys(
        (
            "team_plays",
            "team_dropbacks",
            "team_rushes",
            "receiving_yards",
            "targets",
            "rushing_yards",
            "carries",
            "passing_yards",
            "qb_epa_sum",
            "dropbacks",
            "sacks_suffered",
        ),
        pl.Float64,
    ),
}


def mk_league(
    weeks: int,
    wr_yds: Callable[[str, int], float] = lambda team, w: 100.0,
    sacks: Callable[[str, int], float] = lambda team, w: 2.0,
) -> pl.DataFrame:
    """Two teams, A and B, play each other every week. Per team-game: a WR (`wr_yds`), the main
    QB (35 dropbacks, `sacks`), a RB; the team totals are 60 plays, 35 dropbacks, 25 rushes."""
    recs = []
    for w in range(1, weeks + 1):
        for team, opp in (("A", "B"), ("B", "A")):
            base = {
                "game_id": f"g{w}",
                "team": team,
                "opponent": opp,
                "t": T0 + w,
                "team_plays": 60.0,
                "team_dropbacks": 35.0,
                "team_rushes": 25.0,
                "main_qb": False,
            }
            recs.append(
                {**base, "pgroup": "WR", "targets": 8.0, "receiving_yards": wr_yds(team, w)}
            )
            recs.append(
                {
                    **base,
                    "pgroup": "QB",
                    "main_qb": True,
                    "passing_yards": 250.0,
                    "qb_epa_sum": 10.0,
                    "dropbacks": 35.0,
                    "sacks_suffered": sacks(team, w),
                }
            )
            recs.append({**base, "pgroup": "RB", "carries": 20.0, "rushing_yards": 90.0})
    return pl.DataFrame(recs, schema=_HIST_SCHEMA).with_columns(
        pl.col(c).fill_null(0.0) for c, d in _HIST_SCHEMA.items() if d == pl.Float64
    )


def orows(opponent: str, weeks: Iterable[int], pid: str = "x") -> pl.DataFrame:
    ts = [T0 + w for w in weeks]
    return pl.DataFrame(
        {
            "player_id": [pid] * len(ts),
            "game_id": [f"r{t}" for t in ts],
            "opponent": [opponent] * len(ts),
            "t": pl.Series(ts, dtype=pl.Int32),
        }
    )


def ofeature(inp, hist, rows, name: str) -> dict[int, float | None]:
    out = opponent_features(inp, hist, rows)
    return dict(zip(rows["t"].to_list(), out[name].to_list(), strict=True))


def test_over_expectation_is_positive_for_a_defense_that_gives_up_more():
    """A's receivers gain 100 a game, except 160 in week 6 against B. B's `_oe` for the week-7
    row: A's own mean before weeks 4, 5, 6 is 100 -> oe 0, 0, +60 (weeks 1-3 have no expectation
    yet), mean 20. A, facing B's flat 100, sits at exactly 0."""
    h = mk_league(6, wr_yds=lambda team, w: 160.0 if (team == "A" and w == 6) else 100.0)
    inp = empty_inputs()
    rows = pl.concat([orows("B", [7], "xb"), orows("A", [7], "xa")])
    oe = opponent_features(inp, h, rows)
    by = dict(zip(rows["opponent"], oe.iter_rows(named=True), strict=True))
    assert by["B"]["opp_rec_yds_allowed_wr_oe_l8"] == pytest.approx(20.0)
    assert by["A"]["opp_rec_yds_allowed_wr_oe_l8"] == pytest.approx(0.0)
    # raw allowed: the mean over B's six games
    assert by["B"]["opp_rec_yds_allowed_wr_l8"] == pytest.approx((500 + 160) / 6)
    assert by["A"]["opp_rec_yds_allowed_wr_l8"] == pytest.approx(100.0)


def test_over_expectation_flips_sign_with_the_defense():
    """The same league with the extra yards in the earlier weeks: B's expectation then already
    includes them, so a flat 100 afterwards is below it (negative `_oe`)."""
    h = mk_league(7, wr_yds=lambda team, w: 160.0 if (team == "A" and w <= 3) else 100.0)
    out = ofeature(empty_inputs(), h, orows("B", [8]), "opp_rec_yds_allowed_wr_oe_l8")
    # B faced A's yards 160 x3 then 100 x4; A's expectation before weeks 4-7: mean of prior games
    exp = [160.0, (3 * 160 + 100) / 4, (3 * 160 + 200) / 5, (3 * 160 + 300) / 6]
    assert out[T0 + 8] == pytest.approx(sum(100.0 - e for e in exp) / 4)
    assert out[T0 + 8] < 0


def test_expectation_needs_three_games_and_never_includes_the_current_one():
    h = mk_league(4, wr_yds=lambda team, w: 100.0 * w)  # a rising offense
    rows = orows("B", [5])
    # only week 4 has an expectation (mean of weeks 1-3 = 200) and 3 values are needed
    assert ofeature(empty_inputs(), h, rows, "opp_rec_yds_allowed_wr_oe_l8")[T0 + 5] is None
    h = mk_league(6, wr_yds=lambda team, w: 100.0 * w)
    got = ofeature(empty_inputs(), h, orows("B", [7]), "opp_rec_yds_allowed_wr_oe_l8")
    # weeks 4-6: yards 400/500/600 against expectations 200/250/300
    assert got[T0 + 7] == pytest.approx(((400 - 200) + (500 - 250) + (600 - 300)) / 3)


def test_rates_are_sum_over_sum_with_the_pfr_lag():
    h = mk_league(6, sacks=lambda team, w: float(w))
    inp = empty_inputs()
    inp.pfr_pass = pl.DataFrame(
        {
            "game_id": [f"g{w}" for w in range(1, 7) for _ in range(2)],
            "team": ["A", "B"] * 6,
            "times_pressured": [float(w) for w in range(1, 7) for _ in range(2)],
            "times_blitzed": [10.0] * 12,
        }
    )
    out = opponent_features(inp, h, orows("B", [3, 4, 5, 6, 7, 8]))
    t = out["game_id"].to_list()
    sack = dict(zip(t, out["opp_def_sack_rate_l8"].to_list(), strict=True))
    pres = dict(zip(t, out["opp_def_pressure_rate_l8"].to_list(), strict=True))
    blitz = dict(zip(t, out["opp_def_blitz_rate_l8"].to_list(), strict=True))
    off_sack = dict(zip(t, out["opp_off_sack_rate_l8"].to_list(), strict=True))
    # box score: B's defense forced A's sacks 1, 2, 3, ... over 35 dropbacks a game; the first
    # row with 100+ dropbacks (3 games) is week 4
    assert sack[f"r{T0 + 3}"] is None
    assert sack[f"r{T0 + 4}"] == pytest.approx(6 / 105)
    assert sack[f"r{T0 + 5}"] == pytest.approx(10 / 140)
    assert sack[f"r{T0 + 7}"] == pytest.approx(21 / 210)
    # PFR a week late: row at week 7 sees games before week 6 (weeks 1-5)
    assert pres[f"r{T0 + 4}"] is None  # week 1-2 only: 70 dropbacks < 100
    assert pres[f"r{T0 + 5}"] == pytest.approx((1 + 2 + 3) / 105)  # weeks 1-3
    assert pres[f"r{T0 + 7}"] == pytest.approx(15 / 175)  # weeks 1-5
    assert pres[f"r{T0 + 8}"] == pytest.approx(21 / 210)  # weeks 1-6
    assert blitz[f"r{T0 + 7}"] == pytest.approx(50 / 175)
    # the offense side of the same opponent (B as an offense) uses the same sums
    assert off_sack[f"r{T0 + 7}"] == pytest.approx(21 / 210)


def test_tempo_features():
    h = mk_league(6)
    out = opponent_features(empty_inputs(), h, orows("B", [3, 5, 7]))
    by = dict(zip(out["game_id"].to_list(), out.iter_rows(named=True), strict=True))
    assert by[f"r{T0 + 3}"]["opp_off_pass_rate_l8"] is None  # 2 games x 60 plays < 200
    assert by[f"r{T0 + 5}"]["opp_off_pass_rate_l8"] == pytest.approx(35 / 60)
    assert by[f"r{T0 + 7}"]["opp_off_rush_rate_l8"] == pytest.approx(25 / 60)
    assert by[f"r{T0 + 7}"]["opp_off_plays_per_game_l8"] == pytest.approx(60.0)


def test_ratings_fall_back_to_the_latest_earlier_row():
    h = mk_league(2)
    inp = empty_inputs()
    inp.team_ratings = pl.DataFrame(
        {
            "season": pl.Series([2022, 2022, 2022], dtype=pl.Int32),
            "week": pl.Series([1, 2, 4], dtype=pl.Int32),
            "team": ["B", "B", "B"],
            "off_epa": [0.1, 0.2, 0.4],
            "def_epa": [0.0] * 3,
            "off_pass_epa": [0.0] * 3,
            "def_pass_epa": [0.0] * 3,
            "off_rush_epa": [0.0] * 3,
            "def_rush_epa": [0.0] * 3,
        }
    )
    got = ofeature(inp, h, orows("B", [1, 2, 3, 4, 6]), "opp_off_epa")
    # a row reads the rating of its own week; week 3 has none: the week-2 row; week 6: week 4
    assert [got[T0 + w] for w in (1, 2, 3, 4, 6)] == pytest.approx([0.1, 0.2, 0.2, 0.4, 0.4])
    assert ofeature(inp, h, orows("A", [2]), "opp_off_epa")[T0 + 2] is None  # no rows for A


def test_empty_sources_give_null_columns():
    h = mk_league(3)
    out = opponent_features(empty_inputs(), h, orows("B", [4]))
    assert out.columns == ["player_id", "game_id", *OPP_FEATURES]
    for c in (*LATE_FEATURES, *RATINGS.values()):
        assert out[c].null_count() == 1, c  # opp_def_pressure_rate etc.: no PFR / FTN / ratings
    assert out["opp_rec_yds_allowed_wr_l8"].item() == pytest.approx(100.0)


# ---- brute force: an independent re-implementation on the synthetic league --------------------


def _mean_if(vals: list[float | None], min_n: int) -> float | None:
    got = [v for v in vals if v is not None]
    return sum(got) / len(got) if len(got) >= min_n else None


def test_matches_a_naive_implementation(world):
    inp, hist, rows = world
    out = opponent_features(inp, hist, rows)

    # team-game table: the offense's totals per (team, game)
    tg: dict[tuple[str, str], dict] = {}
    for r in hist.iter_rows(named=True):
        d = tg.setdefault(
            (r["team"], r["game_id"]),
            {
                "team": r["team"],
                "opp": r["opponent"],
                "t": r["t"],
                "plays": r["team_plays"],
                "db": r["team_dropbacks"],
                "rushes": r["team_rushes"],
                "wr_yds": 0.0,
                "rb_rush_yds": 0.0,
                "qb_yds": 0.0,
                "qb_epa": 0.0,
                "qb_db": 0.0,
                "sacks": 0.0,
            },
        )
        if r["pgroup"] == "WR":
            d["wr_yds"] += r["receiving_yards"]
        if r["pgroup"] == "RB":
            d["rb_rush_yds"] += r["rushing_yards"]
        if r["main_qb"]:
            d["qb_yds"] += r["passing_yards"]
            d["qb_epa"] += r["qb_epa_sum"] or 0.0
            d["qb_db"] += r["dropbacks"]
        d["sacks"] += r["sacks_suffered"]
    for d in tg.values():
        d["qb_epa_db"] = d["qb_epa"] / d["qb_db"] if d["qb_db"] > 0 else None
    pfr: dict[tuple[str, str], float] = {}
    for r in inp.pfr_pass.iter_rows(named=True):
        key = (r["team"], r["game_id"])
        pfr[key] = pfr.get(key, 0.0) + r["times_pressured"]
    charted = {(r["nflverse_game_id"], r["nflverse_play_id"]): r for r in inp.ftn.to_dicts()}
    ftn: dict[tuple[str, str], list[float]] = {}
    for p in inp.plays.iter_rows(named=True):
        f = charted.get((p["game_id"], int(p["play_id"])))
        if p["qb_dropback"] == 1 and f is not None:
            acc = ftn.setdefault((p["posteam"], p["game_id"]), [0.0, 0.0])
            acc[0] += 1
            acc[1] += float(f["n_blitzers"] > 0)

    by_team: dict[str, list[dict]] = {}
    for d in sorted(tg.values(), key=lambda x: x["t"]):
        by_team.setdefault(d["team"], []).append(d)
    for games in by_team.values():  # the offense's own mean over its previous 8 games
        for i, d in enumerate(games):
            for s in ("wr_yds", "qb_epa_db"):
                prev = [g[s] for g in games[max(0, i - 8) : i]]
                exp = _mean_if(prev, 3)
                d[f"{s}_oe"] = None if exp is None or d[s] is None else d[s] - exp
    for (team, gid), d in tg.items():
        d["gid"] = gid
        d["pfr"] = pfr.get((team, gid))
        f = ftn.get((team, gid))
        d["ftn_db"], d["ftn_blitz"] = (f[0], f[1]) if f else (None, None)
    by_def: dict[str, list[dict]] = {}
    for d in sorted(tg.values(), key=lambda x: x["t"]):
        by_def.setdefault(d["opp"], []).append(d)
    ratings = {
        (r["team"], (r["season"] - 2000) * 22 + r["week"]): r for r in inp.team_ratings.to_dicts()
    }

    def window(games: list[dict], t: int, lag: int) -> list[dict]:
        return [g for g in games if g["t"] < t - lag][-8:]

    def ratio(games, num, den, min_den) -> float | None:
        have = [g for g in games if g[num] is not None and g[den] is not None]
        total = sum(g[den] for g in have)
        return sum(g[num] for g in have) / total if have and total >= min_den else None

    def check(got, want, label) -> None:
        if want is None:
            assert got is None, f"{label}: {got} vs None"
        else:
            assert got == pytest.approx(want, rel=1e-9, abs=1e-9), label

    for rr, ff in zip(rows.iter_rows(named=True), out.iter_rows(named=True), strict=True):
        opp, t = rr["opponent"], rr["t"]
        dg = window(by_def.get(opp, []), t, 0)
        og = window(by_team.get(opp, []), t, 0)
        check(
            ff["opp_rec_yds_allowed_wr_oe_l8"],
            _mean_if([g["wr_yds_oe"] for g in dg], 3),
            "wr oe",
        )
        check(
            ff["opp_pass_epa_allowed_qb_oe_l8"],
            _mean_if([g["qb_epa_db_oe"] for g in dg], 3),
            "qb epa oe",
        )
        check(
            ff["opp_rush_yds_allowed_rb_l8"],
            _mean_if([g["rb_rush_yds"] for g in dg], 3),
            "rb rush",
        )
        check(ff["opp_def_sack_rate_l8"], ratio(dg, "sacks", "db", 100), "def sack rate")
        check(ff["opp_off_sack_rate_l8"], ratio(og, "sacks", "db", 100), "off sack rate")
        check(ff["opp_off_pass_rate_l8"], ratio(og, "db", "plays", 200), "pass rate")
        check(ff["opp_off_rush_rate_l8"], ratio(og, "rushes", "plays", 200), "rush rate")
        dl = window(by_def.get(opp, []), t, 1)
        ol = window(by_team.get(opp, []), t, 1)
        check(ff["opp_def_pressure_rate_l8"], ratio(dl, "pfr", "db", 100), "def pressure")
        check(ff["opp_off_pressure_rate_l8"], ratio(ol, "pfr", "db", 100), "off pressure")
        check(ff["opp_def_blitz_rate_ftn_l8"], ratio(dl, "ftn_blitz", "ftn_db", 100), "ftn blitz")
        rt = [r for (team, rt_t), r in ratings.items() if team == opp and rt_t <= t]
        latest = max((r for r in rt), key=lambda r: (r["season"], r["week"]), default=None)
        check(ff["opp_off_pass_epa"], latest["off_pass_epa"] if latest else None, "rating")
    assert out["opp_def_blitz_rate_ftn_l8"].drop_nulls().len() > 20
    assert out["opp_rec_yds_allowed_wr_oe_l8"].drop_nulls().len() > 100
