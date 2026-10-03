"""Elo (P02): hand-checked arithmetic, season reversion, leak-freeness and scoring."""

import datetime as dt
import math

import polars as pl
import pytest

from nflengine.features.asof import AsOf
from nflengine.features.leakage import LeakageError, assert_future_invariant
from nflengine.models.elo import EloParams, elo_games, elo_metrics, team_elo

GAME_SCHEMA = {
    "game_id": pl.String,
    "season": pl.Int32,
    "week": pl.Int32,
    "game_type": pl.String,
    "home_team": pl.String,
    "away_team": pl.String,
    "home_score": pl.Int32,
    "away_score": pl.Int32,
    "result": pl.Int32,
    "neutral_site": pl.Boolean,
    "completed": pl.Boolean,
    "kickoff_utc": pl.Datetime("us", "UTC"),
}


def game(
    season: int,
    week: int,
    home: str,
    away: str,
    home_score: int | None,
    away_score: int | None,
    *,
    neutral: bool = False,
    game_type: str = "REG",
    hours: int = 0,
) -> dict:
    """One `games` row; kickoff is Sept 1 of the season + (week - 1) weeks + `hours`."""
    played = home_score is not None
    return {
        "game_id": f"{season}_{week:02d}_{away}_{home}",
        "season": season,
        "week": week,
        "game_type": game_type,
        "home_team": home,
        "away_team": away,
        "home_score": home_score,
        "away_score": away_score,
        "result": home_score - away_score if played else None,
        "neutral_site": neutral,
        "completed": played,
        "kickoff_utc": dt.datetime(season, 9, 1, 17, tzinfo=dt.UTC)
        + dt.timedelta(weeks=week - 1, hours=hours),
    }


def games_frame(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=GAME_SCHEMA)


# Arithmetic for "KC at home beats DAL 27-20, both start at 1505, hfa 48", done by hand:
P_HOME = 1 / (1 + 10 ** (-48 / 400))  # elo_diff = 48
MULT_HOME_WIN_7 = math.log(7 + 1) * 2.2 / (48 * 0.001 + 2.2)  # winner (home) edge = +48
SHIFT_1 = 20 * MULT_HOME_WIN_7 * (1 - P_HOME)


def test_first_game_hand_checked() -> None:
    out = elo_games(games_frame(game(2020, 1, "KC", "DAL", 27, 20))).row(0, named=True)
    assert out["home_elo_pre"] == 1505.0 and out["away_elo_pre"] == 1505.0
    assert out["elo_diff"] == pytest.approx(48.0, abs=1e-9)
    assert out["home_win_prob"] == pytest.approx(1 / (1 + 10**-0.12), abs=1e-9)
    assert out["home_win_prob"] == pytest.approx(0.5686413918836677, abs=1e-9)
    assert out["home_result"] == 1.0 and out["completed"] is True
    # ln(8) * 2.2 / 2.248 = 2.03504...; shift = 20 * mult * (1 - p) = 17.5566...
    mult = MULT_HOME_WIN_7
    assert mult == pytest.approx(2.035040654668878, abs=1e-12)
    assert out["home_elo_post"] == pytest.approx(1505 + SHIFT_1, abs=1e-9)
    assert out["away_elo_post"] == pytest.approx(1505 - SHIFT_1, abs=1e-9)
    assert out["home_elo_post"] == pytest.approx(1522.5566460851624, abs=1e-9)
    assert out["away_elo_post"] == pytest.approx(1487.4433539148376, abs=1e-9)


def test_away_win_margin_multiplier_uses_the_winners_edge() -> None:
    # Road team wins by 3: its edge is -48, so the multiplier is shrunk, not boosted.
    out = elo_games(games_frame(game(2020, 1, "KC", "DAL", 17, 20))).row(0, named=True)
    mult = math.log(3 + 1) * 2.2 / (-48 * 0.001 + 2.2)
    shift = 20 * mult * (0 - P_HOME)
    assert out["home_result"] == 0.0
    assert out["home_elo_post"] == pytest.approx(1505 + shift, abs=1e-9)
    assert out["away_elo_post"] == pytest.approx(1505 - shift, abs=1e-9)
    assert out["home_elo_post"] == pytest.approx(1505 - 16.11774703670007, abs=1e-9)


def test_mov_off_uses_unit_multiplier() -> None:
    out = elo_games(
        games_frame(game(2020, 1, "KC", "DAL", 27, 20)), EloParams(mov=False, k=30.0)
    ).row(0, named=True)
    assert out["home_elo_post"] == pytest.approx(1505 + 30 * (1 - P_HOME), abs=1e-9)


def test_neutral_site_has_no_home_field_and_tie_scores_half() -> None:
    g1 = game(2020, 1, "KC", "DAL", 27, 20)
    g2 = game(2020, 2, "KC", "DAL", 17, 17, neutral=True)  # a tie at a neutral site
    first, second = elo_games(games_frame(g1, g2)).rows(named=True)
    r_kc, r_dal = first["home_elo_post"], first["away_elo_post"]
    assert second["home_elo_pre"] == pytest.approx(r_kc, abs=1e-9)
    assert second["elo_diff"] == pytest.approx(r_kc - r_dal, abs=1e-9)  # no +48
    p = 1 / (1 + 10 ** (-(r_kc - r_dal) / 400))
    assert second["home_win_prob"] == pytest.approx(p, abs=1e-9)
    assert second["home_result"] == 0.5 and second["neutral_site"] is True
    # tie: margin multiplier is ln 2 (|margin| 0 is treated as 1, winner edge 0)
    shift = 20 * math.log(2) * (0.5 - p)
    assert second["home_elo_post"] == pytest.approx(r_kc + shift, abs=1e-9)
    assert second["away_elo_post"] == pytest.approx(r_dal - shift, abs=1e-9)
    assert shift < 0  # the stronger team is expected to win, so a tie costs it points


def test_season_reversion_and_team_elo_snapshots() -> None:
    g20 = game(2020, 1, "KC", "DAL", 27, 20)
    g21 = game(2021, 1, "KC", "DAL", 24, 10)
    g = games_frame(g20, g21)
    r_kc, r_dal = 1505 + SHIFT_1, 1505 - SHIFT_1
    revert = 1 / 3
    kc21 = 1505 + (1 - revert) * (r_kc - 1505)
    dal21 = 1505 + (1 - revert) * (r_dal - 1505)

    games_out = elo_games(g).rows(named=True)
    assert games_out[1]["home_elo_pre"] == pytest.approx(kc21, abs=1e-9)
    assert games_out[1]["away_elo_pre"] == pytest.approx(dal21, abs=1e-9)
    # a custom reversion factor
    half = elo_games(g, EloParams(revert=0.5)).row(1, named=True)
    assert half["home_elo_pre"] == pytest.approx(1505 + 0.5 * (r_kc - 1505), abs=1e-9)

    keys = [AsOf(2020, 1), AsOf(2020, 2), AsOf(2021, 1), AsOf(2021, 2)]
    te = team_elo(g, keys)
    assert te.schema == {
        "season": pl.Int32,
        "week": pl.Int32,
        "team": pl.String,
        "elo": pl.Float64,
        "elo_games": pl.Int32,
    }
    assert te.height == 8  # two teams per key
    got = {(r["season"], r["week"], r["team"]): (r["elo"], r["elo_games"]) for r in te.to_dicts()}
    assert got[(2020, 1, "KC")] == (1505.0, 0)
    assert got[(2020, 1, "DAL")] == (1505.0, 0)
    assert got[(2020, 2, "KC")] == (pytest.approx(r_kc, abs=1e-9), 1)  # no reversion yet
    assert got[(2020, 2, "DAL")] == (pytest.approx(r_dal, abs=1e-9), 1)
    assert got[(2021, 1, "KC")] == (pytest.approx(kc21, abs=1e-9), 0)  # reverted, none played
    assert got[(2021, 1, "DAL")] == (pytest.approx(dal21, abs=1e-9), 0)
    post_kc = games_out[1]["home_elo_post"]
    assert got[(2021, 2, "KC")] == (pytest.approx(post_kc, abs=1e-9), 1)


def test_team_first_seen_later_starts_at_the_mean() -> None:
    g = games_frame(
        game(2020, 1, "KC", "DAL", 27, 20),
        game(2021, 1, "KC", "DAL", 24, 10),
        game(2021, 1, "BUF", "MIA", 31, 14, hours=3),
    )
    te = team_elo(g, [AsOf(2020, 1), AsOf(2021, 1)])
    assert set(te.filter(pl.col("season") == 2020)["team"]) == {"KC", "DAL"}
    new = te.filter((pl.col("season") == 2021) & pl.col("team").is_in(["BUF", "MIA"]))
    assert new["elo"].to_list() == [1505.0, 1505.0] and new["elo_games"].to_list() == [0, 0]


def test_unplayed_and_cancelled_games_do_not_update() -> None:
    g = games_frame(
        game(2020, 1, "KC", "DAL", 27, 20),
        game(2020, 2, "KC", "DAL", None, None),  # cancelled / not played
        game(2020, 3, "DAL", "KC", 10, 13),
    )
    w1, w2, w3 = elo_games(g).rows(named=True)
    assert w2["completed"] is False and w2["home_result"] is None
    assert w2["home_elo_post"] is None and w2["away_elo_post"] is None
    assert w2["home_elo_pre"] == pytest.approx(w1["home_elo_post"], abs=1e-9)
    assert w2["away_elo_pre"] == pytest.approx(w1["away_elo_post"], abs=1e-9)
    assert 0 < w2["home_win_prob"] < 1
    assert w3["away_elo_pre"] == pytest.approx(w1["home_elo_post"], abs=1e-9)  # KC unchanged
    te = team_elo(g, [AsOf(2020, 3)]).filter(pl.col("team") == "KC")
    assert te["elo_games"].to_list() == [1]


def test_season_with_no_completed_games_still_gets_reverted_rows() -> None:
    g = games_frame(
        game(2020, 1, "KC", "DAL", 27, 20),
        game(2021, 1, "KC", "DAL", None, None),
    )
    reverted = 1505 + (2 / 3) * SHIFT_1
    te = team_elo(g, [AsOf(2021, 1), AsOf(2021, 3)])
    assert te.height == 4
    kc = te.filter(pl.col("team") == "KC")
    assert kc["elo"].to_list() == [pytest.approx(reverted, abs=1e-9)] * 2
    assert kc["elo_games"].to_list() == [0, 0]
    row = elo_games(g).row(1, named=True)
    assert row["home_elo_pre"] == pytest.approx(reverted, abs=1e-9)
    assert row["home_result"] is None and row["home_elo_post"] is None


def test_input_without_required_columns_is_rejected() -> None:
    with pytest.raises(ValueError, match="kickoff_utc"):
        elo_games(games_frame(game(2020, 1, "KC", "DAL", 27, 20)).drop("kickoff_utc"))


# ---------------------------------------------------------------------------------------
# A small league: 4 teams, 3 seasons x 4 weeks; the last two weeks of 2022 are unplayed.


def league() -> pl.DataFrame:
    rows = []
    for season in (2020, 2021, 2022):
        for week in (1, 2, 3, 4):
            for i, (a, b) in enumerate((("KC", "DAL"), ("BUF", "MIA"))):
                home, away = (a, b) if (week + i) % 2 == 0 else (b, a)
                hs = 14 + (3 * season + 5 * week + 7 * i) % 17
                as_ = 10 + (5 * season + 3 * week + 11 * i) % 13
                if (season, week, i) == (2021, 2, 0):
                    hs = as_ = 17  # a tie
                if season == 2022 and week >= 3:
                    hs = as_ = None
                rows.append(game(season, week, home, away, hs, as_, hours=3 * i))
    return games_frame(*rows)


KEYS = [AsOf(s, w) for s in (2020, 2021, 2022) for w in range(1, 6)]


def test_team_elo_matches_the_pre_game_ratings_of_elo_games() -> None:
    g = league()
    te = team_elo(g, KEYS)
    assert te.height == 4 * len(KEYS)
    by_key = {(r["season"], r["week"], r["team"]): r["elo"] for r in te.to_dicts()}
    eg = elo_games(g)
    assert eg.height == g.height
    for r in eg.to_dicts():
        key = (r["season"], r["week"])
        assert by_key[(*key, r["home_team"])] == pytest.approx(r["home_elo_pre"], abs=1e-9)
        assert by_key[(*key, r["away_team"])] == pytest.approx(r["away_elo_pre"], abs=1e-9)
    # row order of the input doesn't matter: the walk sorts by kickoff, then game_id
    shuffled = g.sample(fraction=1.0, shuffle=True, seed=7)
    assert elo_games(shuffled).equals(eg)
    assert team_elo(shuffled, KEYS).equals(te)


def test_team_elo_is_invariant_to_games_on_or_after_the_key() -> None:
    def build(inputs):
        return team_elo(inputs["games"], KEYS)

    for key in (AsOf(2020, 3), AsOf(2021, 1), AsOf(2021, 3)):
        checked = assert_future_invariant(build, {"games": league()}, key, protect={"game_id"})
        assert checked.height > 0


def test_leakage_helper_catches_a_builder_that_peeks_at_the_key_week() -> None:
    def peeks(inputs):
        later = [AsOf(k.season, k.week + 1) for k in KEYS]  # ratings AFTER the week
        return team_elo(inputs["games"], later).with_columns(pl.col("week") - 1)

    with pytest.raises(LeakageError):
        assert_future_invariant(peeks, {"games": league()}, AsOf(2021, 3))


# ---------------------------------------------------------------------------------------
# elo_metrics


def probs(*rows: tuple) -> pl.DataFrame:
    """(season, game_type, completed, p, result) -> the columns elo_metrics reads."""
    return pl.DataFrame(
        rows,
        schema={
            "season": pl.Int32,
            "game_type": pl.String,
            "completed": pl.Boolean,
            "home_win_prob": pl.Float64,
            "home_result": pl.Float64,
        },
        orient="row",
    )


def test_elo_metrics_hand_checked() -> None:
    df = probs(
        (2020, "REG", True, 0.7, 1.0),  # right pick
        (2020, "REG", True, 0.6, 0.0),  # wrong pick
        (2021, "REG", True, 0.4, 0.5),  # tie: scored at 0.5, no accuracy
        (2021, "REG", False, 0.9, None),  # not played: ignored
        (2019, "REG", True, 0.9, 1.0),  # season not asked for
    )
    out = elo_metrics(df, [2020, 2021])
    assert out.columns == ["scope", "season", "games", "brier", "log_loss", "accuracy"]
    assert out["scope"].to_list() == ["season", "season", "pooled"]
    assert out["season"].to_list() == [2020, 2021, None]
    assert out["games"].to_list() == [2, 1, 3]

    ll_a, ll_b = -math.log(0.7), -math.log(1 - 0.6)
    ll_c = -(0.5 * math.log(0.4) + 0.5 * math.log(0.6))
    s20, s21, pooled = out.rows(named=True)
    assert s20["brier"] == pytest.approx((0.3**2 + 0.6**2) / 2, abs=1e-9)  # (0.09 + 0.36) / 2
    assert s20["log_loss"] == pytest.approx((ll_a + ll_b) / 2, abs=1e-9)
    assert s20["accuracy"] == 0.5
    assert s21["brier"] == pytest.approx(0.1**2, abs=1e-9)
    assert s21["log_loss"] == pytest.approx(ll_c, abs=1e-9)
    assert s21["accuracy"] is None  # only a tie
    assert pooled["brier"] == pytest.approx((0.09 + 0.36 + 0.01) / 3, abs=1e-9)
    assert pooled["log_loss"] == pytest.approx((ll_a + ll_b + ll_c) / 3, abs=1e-9)
    assert pooled["accuracy"] == 0.5  # 1 of 2 non-tie games


def test_elo_metrics_filters_game_types_and_clips_probabilities() -> None:
    df = probs(
        (2020, "REG", True, 0.7, 1.0),
        (2020, "DIV", True, 1.0, 0.0),  # a certain, wrong forecast
    )
    reg = elo_metrics(df, [2020], game_types=["REG"])
    assert reg["games"].to_list() == [1, 1]
    assert reg["brier"].to_list() == [pytest.approx(0.09, abs=1e-9)] * 2
    both = elo_metrics(df, [2020])
    assert both.filter(pl.col("scope") == "pooled")["games"].item() == 2
    # p = 1 is clipped to 1 - 1e-6 so the log loss is large but finite
    expected = (-math.log(0.7) + -math.log(1e-6)) / 2
    assert both["log_loss"][0] == pytest.approx(expected, abs=1e-9)
    assert both["accuracy"][0] == 0.5


def test_elo_metrics_on_real_walk_output_is_sane() -> None:
    out = elo_metrics(elo_games(league()), [2020, 2021, 2022])
    assert out["games"].to_list() == [8, 8, 4, 20]  # 2022 weeks 3-4 are unplayed
    assert all(0.0 <= b <= 1.0 for b in out["brier"])


# ---------------------------------------------------------------------------------------
# EloParams


def test_params_defaults_and_from_config() -> None:
    d = EloParams()
    assert (d.k, d.hfa, d.mean, d.mov, d.start_season) == (20.0, 48.0, 1505.0, True, 2002)
    assert d.revert == pytest.approx(1 / 3)
    assert EloParams.from_config(None) == d
    assert EloParams.from_config({}) == d
    p = EloParams.from_config({"k": 25, "hfa": 65, "mov": False, "start_season": "2005"})
    assert p == EloParams(k=25.0, hfa=65.0, mov=False, start_season=2005)
    assert isinstance(p.k, float) and isinstance(p.start_season, int)


def test_params_unknown_or_invalid_keys_raise() -> None:
    with pytest.raises(ValueError, match="unknown Elo parameter.*kk"):
        EloParams.from_config({"k": 20, "kk": 1})
    with pytest.raises(ValueError, match="mov"):
        EloParams.from_config({"mov": "yes"})
    with pytest.raises(ValueError, match="revert"):
        EloParams.from_config({"revert": 1.5})
    with pytest.raises(ValueError, match="k must"):
        EloParams(k=-1)
