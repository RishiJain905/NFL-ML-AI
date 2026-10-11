"""PC00 on the real 2025 data (read only: nothing is written, nothing goes to W&B).

Builds 2025 in memory from the curated tables on D: (`load_inputs` -> `enrich_plays` ->
`build_season`; no participation, no files) and checks it against the published numbers.
Run with `uv run pytest -m integration tests/playcalling/test_playcall_real.py`.
"""

from __future__ import annotations

import polars as pl
import pytest
from polars.testing import assert_frame_equal

import nflengine.playcalling.build as B
from nflengine.paths import ensure_data_root

pytestmark = pytest.mark.integration

SEASON = 2025
REG_AS_OF = 19  # as-of week 19 = regular season only (weeks 1-18); 19-23 add the playoffs

# rbsdm.com "Pass Over Expected" tab, 2025 regular season (win probability 20-80%, all downs,
# no two-minute exclusion), read on 2026-10-10.
RBSDM_TOP5 = {"ARI", "KC", "LA", "CIN", "NE"}
RBSDM_BOTTOM5 = {"NYJ", "BAL", "SEA", "DET", "ATL"}


@pytest.fixture(scope="module")
def real() -> dict:
    paths = ensure_data_root(create_dirs=False)
    inp = B.load_inputs(paths, [SEASON], history=False, log=lambda *a, **k: None)
    enriched = B.enrich_plays(inp.plays, inp.ftn, inp.part)
    return {"inp": inp, "enriched": enriched, "sb": B.build_season(inp, enriched, SEASON)}


def _league(sb: B.SeasonBuild, metric: str, situation: str, as_of: int | None = None) -> float:
    as_of = as_of or sb.weeks[-1]
    row = sb.league.filter(
        (pl.col("as_of_week") == as_of)
        & (pl.col("window") == "season")
        & (pl.col("metric") == metric)
        & (pl.col("situation") == situation)
    )
    assert row.height == 1
    return float(row["value"][0])


def test_2025_league_play_action_and_blitz_match_the_published_numbers(real) -> None:
    """FTN's 2025 league rates: play-action 23% of dropbacks, blitz (1+ blitzer) 29%."""
    sb = real["sb"]
    assert sb.weeks[-1] >= REG_AS_OF
    assert _league(sb, "play_action_rate", "all") == pytest.approx(0.23, abs=0.01)
    assert _league(sb, "blitz_rate", "all") == pytest.approx(0.29, abs=0.01)
    # the regular season alone gives the same answer
    assert _league(sb, "play_action_rate", "all", REG_AS_OF) == pytest.approx(0.23, abs=0.01)
    assert _league(sb, "blitz_rate", "all", REG_AS_OF) == pytest.approx(0.29, abs=0.01)


def test_2025_ftn_charts_every_scrimmage_play(real) -> None:
    cov = real["sb"].meta["coverage"]
    # 0.9986 on 2026-10-10: FTN leaves a few plays as placeholder rows (dropped, not charted)
    assert cov["ftn_join_rate"] >= 0.99
    assert cov["games_without_ftn"] == []
    assert cov["ftn_weeks"] == list(range(1, 23))
    assert cov["games_in_plays"] == cov["games_completed"] == 285  # 272 regular season + 13 playoff
    assert cov["part_join_rate"] == 0.0  # participation was not loaded here


def test_2025_regular_season_has_17_games_for_every_team(real) -> None:
    t = real["sb"].tend.filter(
        (pl.col("as_of_week") == REG_AS_OF)
        & (pl.col("window") == "season")
        & (pl.col("side") == "offense")
        & (pl.col("metric") == "dropback_rate")
        & (pl.col("situation") == "all")
    )
    assert t.height == 32 and t["games"].unique().to_list() == [17]
    assert t["pct"].min() == 0.0 and t["pct"].max() == 100.0


def test_2025_neutral_proe_extremes_match_rbsdm(real) -> None:
    """Regular season only (as-of week 19). Our neutral rule (downs 1-3, last two minutes out)
    is not rbsdm's PROE-tab filter, so the sets may differ by one team."""
    t = real["sb"].tend.filter(
        (pl.col("as_of_week") == REG_AS_OF)
        & (pl.col("window") == "season")
        & (pl.col("side") == "offense")
        & (pl.col("metric") == "proe")
        & (pl.col("situation") == "neutral")
    )
    assert t.height == 32
    ranked = t.sort("value", descending=True)["team"].to_list()
    top5, bottom5 = set(ranked[:5]), set(ranked[-5:])
    assert len(top5 & RBSDM_TOP5) >= 4, (sorted(top5), sorted(RBSDM_TOP5))
    assert len(bottom5 & RBSDM_BOTTOM5) >= 4, (sorted(bottom5), sorted(RBSDM_BOTTOM5))
    assert ranked[0] in RBSDM_TOP5 and ranked[-1] in RBSDM_BOTTOM5  # ARI and NYJ


def test_2025_league_neutral_proe_is_small_but_not_exactly_zero(real) -> None:
    """nflfastR's xpass is a fixed model: 2025 passed less than it expected, about -2 points."""
    proe = _league(real["sb"], "proe", "neutral", REG_AS_OF)
    assert -0.04 < proe < 0.0, proe
    assert 0.55 < _league(real["sb"], "dropback_rate", "neutral", REG_AS_OF) < 0.60


def test_a_build_through_week_10_equals_the_full_build_up_to_as_of_week_11(real) -> None:
    """The leakage check on real data: the season and last-4 windows (and last season) of every
    as-of week up to 11 are identical whether or not weeks 11+ exist."""
    full = real["sb"]
    cut = B.build_season(real["inp"], real["enriched"], SEASON, through_week=10)
    assert cut.weeks == list(range(1, 12))
    keep = pl.col("as_of_week") <= 11
    a, b = full.tend.filter(keep), cut.tend
    assert a.height == b.height > 100_000
    assert set(b["window"]) == {"season", "last4", "last_season"}
    assert_frame_equal(a, b)
    assert_frame_equal(full.league.filter(keep), cut.league)
    assert cut.enriched["week"].max() == 10


def test_2025_last_season_window_is_all_of_2024(real) -> None:
    enriched, sb = real["enriched"], real["sb"]
    prev = enriched.filter(pl.col("season") == SEASON - 1)
    for team in ("KC", "ARI", "NYJ"):
        mine = prev.filter(pl.col("offense") == team)
        row = sb.tend.filter(
            (pl.col("team") == team)
            & (pl.col("side") == "offense")
            & (pl.col("window") == "last_season")
            & (pl.col("metric") == "dropback_rate")
            & (pl.col("situation") == "all")
            & (pl.col("as_of_week") == 1)
        ).row(0, named=True)
        assert row["n"] == mine.height
        assert row["value"] == pytest.approx(mine["dropback"].mean())
        games = prev.filter((pl.col("offense") == team) | (pl.col("defense") == team))["game_id"]
        assert row["games"] == games.n_unique()
    # and it does not move with the as-of week
    flat = sb.tend.filter(
        (pl.col("window") == "last_season")
        & (pl.col("team") == "KC")
        & (pl.col("side") == "offense")
        & (pl.col("metric") == "dropback_rate")
        & (pl.col("situation") == "all")
    )
    assert flat["value"].n_unique() == 1 and flat["as_of_week"].n_unique() == 23
