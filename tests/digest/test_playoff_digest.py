"""Playoff digests (P10): trends and under-the-hood limited to the teams still playing, the
previous playoff round as "last week", and the round's name in the title."""

from __future__ import annotations

from types import SimpleNamespace

import polars as pl
from test_under_hood import LAST, SEASON, WEEK, _league

from nflengine.digest import build
from nflengine.digest.render import header
from nflengine.digest.under_hood import build_under_hood

# ---- digest -------------------------------------------------------------------------------------


def test_build_trends_keeps_only_the_teams_still_playing() -> None:
    trends = pl.DataFrame(
        {
            "team": ["LV", "NYJ", "GB", "PHI"],
            "direction": ["up", "up", "down", "down"],
            "trend_delta": [0.12, 0.11, -0.10, -0.09],
            "net_epa": [0.0, -0.1, -0.04, -0.02],
        }
    )
    drivers = pl.DataFrame(
        schema={"team": pl.String, "rank": pl.Int64, "part": pl.String, "delta": pl.Float64}
    )
    assert [t.team for t in build.build_trends(trends, drivers)] == ["LV", "NYJ", "GB", "PHI"]
    out = build.build_trends(trends, drivers, teams=["NYJ", "PHI"])
    assert [t.team for t in out] == ["NYJ", "PHI"]


def _as_playoff_round(tables: dict[str, pl.DataFrame]) -> dict[str, pl.DataFrame]:
    """The league with last week (2025 week 4) relabelled as a playoff round."""
    last = (pl.col("season") == SEASON) & (pl.col("week") == LAST)
    out = dict(tables)
    out["ngs_receiving"] = tables["ngs_receiving"].with_columns(
        pl.when(last).then(pl.lit("POST")).otherwise(pl.col("season_type")).alias("season_type")
    )
    out["pfr_def"] = tables["pfr_def"].with_columns(
        pl.when(last).then(pl.lit("WC")).otherwise(pl.col("game_type")).alias("game_type")
    )
    return out


def test_under_hood_reads_the_previous_playoff_round_and_the_slate_teams() -> None:
    league = _league()
    regular = build_under_hood(SEASON, WEEK, league)
    post = _as_playoff_round(league)
    assert build_under_hood(SEASON, WEEK, post).is_empty()  # regular season only: nothing
    playoffs = build_under_hood(SEASON, WEEK, post, playoffs=True)
    assert playoffs.equals(regular)  # the playoff rows count as last week
    kc = build_under_hood(SEASON, WEEK, post, playoffs=True, teams=["kc", "PHI"])
    assert kc.height and set(kc["team"]) <= {"KC", "PHI"}
    assert "00-A" in kc["player_id"].to_list()  # KC's planted jump survives the filter


def test_title_names_the_playoff_round() -> None:
    meta = SimpleNamespace(
        season=2025,
        week=19,
        mode="live",
        sources={},
        run_time="2026-01-06T14:00:00+00:00",
        data_through="2025-W18",
        playoff_round="Wild Card round",
    )
    assert header(SimpleNamespace(meta=meta)).startswith(
        "# NFL digest: 2025 Wild Card round (week 19)"
    )
    meta.playoff_round = None
    assert header(SimpleNamespace(meta=meta)).startswith("# NFL digest: 2025 week 19\n")


def test_source_weeks_count_playoff_weeks_and_ngs_super_bowl_week(tmp_path) -> None:
    """Freshness (P10, Sol review): NGS / PFR / FTN playoff weeks count, NGS's Super Bowl
    week 23 reads as 22, so a conference-week run doesn't call the sources 2 weeks behind."""
    import duckdb

    from nflengine.digest.under_hood import source_weeks
    from nflengine.paths import DataPaths

    paths = DataPaths(tmp_path)
    paths.curated.mkdir(parents=True)
    con = duckdb.connect(str(paths.curated / "nfl.duckdb"))
    frames = {
        "ngs_passing": pl.DataFrame(
            {
                "season": [2025, 2025, 2025],
                "season_type": ["REG", "POST", "POST"],
                "week": [18, 21, 23],
                "is_season_total": [False, False, False],
            }
        ),
        "pfr_pass": pl.DataFrame(
            {"season": [2025, 2025], "game_type": ["REG", "CON"], "week": [18, 21]}
        ),
        "games": pl.DataFrame(
            {"game_id": ["a", "b"], "season": [2025, 2025], "game_type": ["REG", "DIV"]}
        ),
        "ftn_plays": pl.DataFrame({"nflverse_game_id": ["a", "b"], "week": [18, 20]}),
    }
    for name, df in frames.items():
        con.register("df", df.to_arrow())
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM df")
        con.unregister("df")
    con.close()
    assert source_weeks(2025, paths) == {"NGS": 22, "PFR": 21, "FTN": 20}
    # 17-week seasons (before 2021): NGS's Super Bowl is week 22, nflverse's 21 (Sol follow-up)
    con = duckdb.connect(str(paths.curated / "nfl.duckdb"))
    con.execute(
        "INSERT INTO ngs_passing VALUES (2020, 'REG', 17, false), (2020, 'POST', 22, false)"
    )
    con.close()
    assert source_weeks(2020, paths)["NGS"] == 21


def test_highlights_never_repeat_a_game_on_a_small_slate() -> None:
    """A one-game Super Bowl has no "most lopsided" / "closest" rankings (GLM wrote "the same
    game is the most lopsided call and the closest game"); 2-6 games share none; a full
    regular week keeps 3 + 2."""
    from conftest import make_payload

    from nflengine.digest import format as F

    base = make_payload().games[0]

    def games(n: int):
        return [
            base.model_copy(
                update={"game_id": f"g{i}", "home_win_prob": F.pct(0.5 + 0.45 * i / max(n, 1))}
            )
            for i in range(n)
        ]

    for n, sizes in ((1, (0, 0)), (2, (1, 1)), (4, (2, 2)), (6, (3, 2)), (16, (3, 2))):
        h = build.build_highlights(games(n))
        assert (len(h.most_lopsided), len(h.closest)) == sizes, n
        ids = [x.game_id for x in h.most_lopsided] + [x.game_id for x in h.closest]
        assert len(ids) == len(set(ids)), n
