"""The published-insight log (P05): novelty that survives the weekly rebuild.

The log is a small Parquet file; a re-run of a week replaces that week's rows, and the next
rebuild reads weeks W-3..W-1 of the same season back as `PublishedInsight` nodes.
"""

from __future__ import annotations

import datetime as dt
import types
from pathlib import Path

import polars as pl
import pytest
from graph_world import KEY, FakeDriver

from nflengine.digest import format as F
from nflengine.digest.payload import GraphInsight, GraphPerson
from nflengine.graph import insights as gi
from nflengine.graph import published as pub
from nflengine.graph import tables as T

UTC = dt.UTC
AT = dt.datetime(2026, 9, 29, 15, 0, tzinfo=UTC)


def item(
    iid: str,
    typ: str = "revenge",
    game: str = "g1",
    teams: tuple[str, ...] = ("KC", "LV"),
    people: tuple[str, ...] = ("Rex Revenge",),
) -> GraphInsight:
    return GraphInsight(
        insight_id=iid,
        insight_type=typ,  # type: ignore[arg-type]
        section="matchup_risk" if typ in ("injury_ripple", "qb_change") else "non_obvious",
        strength=0.5,
        graph_query=gi.QUERY_OF[typ],
        game_id=game,
        teams=list(teams),
        people=[GraphPerson(player=n, player_id=f"id-{n}", team="KC", role="out") for n in people],
        headline="h",
        sample=F.text_num("x"),
    )


def ids_of(log: pl.DataFrame, season: int, week: int) -> list[str]:
    rows = log.filter((pl.col("season") == season) & (pl.col("week") == week))
    return sorted(rows["insight_id"].to_list())


# ---- the log file -------------------------------------------------------------------------------


def test_the_log_lives_next_to_the_run_folders(tmp_path: Path) -> None:
    assert pub.log_path(tmp_path) == tmp_path / "published_insights.parquet"
    assert pub.NOVELTY_WEEKS == gi.NOVELTY_WEEKS == 3


def test_reading_a_missing_log_gives_an_empty_typed_frame(tmp_path: Path) -> None:
    log = pub.read_log(tmp_path / "nope.parquet")
    assert log.is_empty()
    assert log.schema == pl.Schema(pub.SCHEMA)


def test_record_creates_the_file_and_its_folder(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "digest-backtests" / "published_insights.parquet"
    out = pub.record(path, 2026, 4, [item("revenge:p:LV")], AT)
    assert path.exists()
    assert out.height == 1
    assert pub.read_log(path).equals(out)


def test_record_writes_one_row_per_picked_item(tmp_path: Path) -> None:
    path = tmp_path / "log.parquet"
    picks = [
        item("revenge:00-R1:LV", "revenge", "g1", ("KC", "LV"), ("Rex Revenge",)),
        item("qb_change:KC:00-Q2", "qb_change", "g2", ("KC", "SF"), ("Ben Backup", "Quinn Kay")),
    ]
    log = pub.record(path, 2026, 4, picks, AT)
    assert log.schema == pl.Schema(pub.SCHEMA)
    rows = {r["insight_id"]: r for r in log.iter_rows(named=True)}
    assert set(rows) == {"revenge:00-R1:LV", "qb_change:KC:00-Q2"}
    qb = rows["qb_change:KC:00-Q2"]
    assert (qb["season"], qb["week"], qb["insight_type"]) == (2026, 4, "qb_change")
    assert (qb["section"], qb["game_id"]) == ("matchup_risk", "g2")
    assert qb["entities"] == ["KC", "SF", "Ben Backup", "Quinn Kay"]  # teams, then people
    assert qb["published_at"] == AT


def test_entities_are_the_teams_then_the_people_names() -> None:
    ins = item("x", teams=("KC", "SF", "LV"), people=("A One", "B Two"))
    assert pub.entities(ins) == ["KC", "SF", "LV", "A One", "B Two"]
    assert pub.entities(item("y", people=())) == ["KC", "LV"]


def test_record_sorts_by_season_week_and_id(tmp_path: Path) -> None:
    path = tmp_path / "log.parquet"
    pub.record(path, 2026, 4, [item("b"), item("a")], AT)
    pub.record(path, 2025, 17, [item("z")], AT)
    out = pub.record(path, 2026, 3, [item("c")], AT)
    keys = list(zip(out["season"], out["week"], out["insight_id"], strict=True))
    assert keys == [(2025, 17, "z"), (2026, 3, "c"), (2026, 4, "a"), (2026, 4, "b")]


def test_a_rerun_adds_to_the_weeks_rows_and_keeps_the_others(tmp_path: Path) -> None:
    """Published is published: a re-run's picks join the week's rows (Sol review)."""
    path = tmp_path / "log.parquet"
    pub.record(path, 2026, 3, [item("w3-a"), item("w3-b")], AT)
    pub.record(path, 2025, 4, [item("old-season")], AT)  # week 4 of another season
    pub.record(path, 2026, 4, [item("first-run")], AT)
    later = AT + dt.timedelta(hours=2)
    out = pub.record(path, 2026, 4, [item("second-run-a"), item("second-run-b")], later)
    assert ids_of(out, 2026, 4) == ["first-run", "second-run-a", "second-run-b"]
    assert ids_of(out, 2026, 3) == ["w3-a", "w3-b"]
    assert ids_of(out, 2025, 4) == ["old-season"]  # same week number, other season
    wk4 = out.filter(pl.col("week") == 4, pl.col("season") == 2026)
    assert set(wk4["published_at"]) == {AT, later}
    again = pub.record(path, 2026, 4, [item("first-run")], later)  # logged twice: one row
    assert ids_of(again, 2026, 4) == ["first-run", "second-run-a", "second-run-b"]
    assert pub.read_log(path).equals(again)  # what was returned is what was written


def test_a_rerun_with_no_picks_keeps_what_was_published(tmp_path: Path) -> None:
    """Week 4 published X; a re-run after X's game kicked off picks nothing. X must stay in
    the log, or week 5 could publish it again (Sol review)."""
    path = tmp_path / "log.parquet"
    pub.record(path, 2026, 3, [item("keep")], AT)
    pub.record(path, 2026, 4, [item("x")], AT)
    out = pub.record(path, 2026, 4, [], AT)
    assert ids_of(out, 2026, 4) == ["x"] and ids_of(out, 2026, 3) == ["keep"]
    assert "x" in pub.recent_from_log(out, 2026, 5)
    only = pub.record(tmp_path / "other.parquet", 2026, 4, [], AT)  # a first run with nothing
    assert only.is_empty() and only.schema == pl.Schema(pub.SCHEMA)


def test_record_takes_any_iterable(tmp_path: Path) -> None:
    out = pub.record(tmp_path / "log.parquet", 2026, 4, (item(i) for i in "ab"), AT)
    assert ids_of(out, 2026, 4) == ["a", "b"]


# ---- the novelty window -------------------------------------------------------------------------


def window_log() -> pl.DataFrame:
    rows = [(2026, w, f"w{w}") for w in range(1, 8)] + [(2025, 3, "last-season")]
    return pl.DataFrame(
        {
            "season": [r[0] for r in rows],
            "week": [r[1] for r in rows],
            "insight_id": [r[2] for r in rows],
        }
    )


@pytest.mark.parametrize(
    "week, expected",
    [
        (5, ["w2", "w3", "w4"]),  # W-3 .. W-1; not W itself, not W-4
        (4, ["w1", "w2", "w3"]),
        (3, ["w1", "w2"]),
        (2, ["w1"]),
        (1, []),
        (7, ["w4", "w5", "w6"]),
    ],
)
def test_recent_ids_are_the_three_weeks_before_the_week(week: int, expected: list[str]) -> None:
    assert pub.recent_from_log(window_log(), 2026, week) == expected


def test_recent_ids_stay_inside_the_season() -> None:
    log = window_log()
    assert "last-season" not in pub.recent_from_log(log, 2026, 5)
    assert pub.recent_from_log(log, 2025, 5) == ["last-season"]
    assert pub.recent_from_log(log, 2027, 2) == []  # a new season starts clean


def test_recent_ids_are_unique_and_sorted() -> None:
    log = pl.DataFrame(
        {
            "season": [2026] * 4,
            "week": [3, 4, 4, 2],
            "insight_id": ["b", "a", "b", "c"],
        }
    )
    assert pub.recent_from_log(log, 2026, 5) == ["a", "b", "c"]


def test_recent_ids_from_an_empty_log() -> None:
    assert pub.recent_from_log(pl.DataFrame(schema=pub.SCHEMA), 2026, 5) == []
    assert pub.recent_from_log(pl.DataFrame(), 2026, 5) == []


def test_recent_ids_from_a_recorded_log(tmp_path: Path) -> None:
    path = tmp_path / "log.parquet"
    for week, name in ((1, "a"), (2, "b"), (3, "c"), (4, "d")):
        pub.record(path, 2026, week, [item(name)], AT)
    assert pub.recent_from_log(pub.read_log(path), 2026, 5) == ["b", "c", "d"]
    assert pub.recent_from_log(pub.read_log(path), 2026, 4) == ["a", "b", "c"]


def test_a_published_story_is_not_picked_again_within_three_weeks(tmp_path: Path) -> None:
    """The loop the log exists for: publish week 3, build week 5, `select` skips the repeat."""
    path = tmp_path / "log.parquet"
    story = item("revenge:00-R1:LV", game="g1")
    pub.record(path, 2026, 3, [story], AT)
    recent = pub.recent_from_log(pub.read_log(path), 2026, 5)
    games = {"g1": gi.GameInfo("g1", "KC", "LV", AT + dt.timedelta(days=5))}
    sel = gi.select([story], games, AT, recent_ids=recent)
    assert sel.skipped["novelty"] == [story.insight_id] and sel.picked == []
    later = pub.recent_from_log(pub.read_log(path), 2026, 7)  # four weeks on: fair game again
    assert gi.select([story], games, AT, recent_ids=later).picked != []


# ---- the log as PublishedInsight nodes ----------------------------------------------------------


def test_the_log_loads_as_nodes_with_the_same_keys_the_live_graph_gets(tmp_path: Path) -> None:
    path = tmp_path / "log.parquet"
    picks = [item("revenge:00-R1:LV"), item("qb_change:KC:00-Q2", "qb_change")]
    pub.record(path, 2026, 3, picks, AT)
    inputs = types.SimpleNamespace(published=pub.read_log(path))
    nodes = T.published_nodes(inputs, KEY)  # type: ignore[arg-type]
    driver = FakeDriver()
    assert pub.write_nodes(driver, 2026, 3, picks, AT) == 2
    written = driver.calls[0][1]["rows"]
    assert sorted(nodes["key"].to_list()) == sorted(r["key"] for r in written)
    node = {r["insight_id"]: r for r in nodes.iter_rows(named=True)}["revenge:00-R1:LV"]
    row = {r["insight_id"]: r for r in written}["revenge:00-R1:LV"]
    assert (node["type"], node["season"], node["week"], node["entities"]) == (
        row["type"],
        row["season"],
        row["week"],
        row["entities"],
    )


def test_write_nodes_merges_this_weeks_picks_into_the_graph() -> None:
    driver = FakeDriver()
    n = pub.write_nodes(driver, 2026, 4, [item("revenge:00-R1:LV")], AT)
    assert n == 1
    query, params = driver.calls[0]
    assert query == pub.MERGE_QUERY and "MERGE (pi:PublishedInsight {key: r.key})" in query
    assert params["rows"] == [
        {
            "key": "2026-w4:revenge:00-R1:LV",
            "insight_id": "revenge:00-R1:LV",
            "type": "revenge",
            "entities": ["KC", "LV", "Rex Revenge"],
            "season": 2026,
            "week": 4,
            "published_at": AT,
        }
    ]


def test_write_nodes_with_nothing_to_write_never_opens_a_session() -> None:
    driver = FakeDriver(fail=RuntimeError("must not connect"))
    assert pub.write_nodes(driver, 2026, 4, [], AT) == 0
    assert driver.calls == []


def test_recent_from_graph_asks_for_the_same_window() -> None:
    def respond(query: str, params: dict) -> list[dict]:
        return [{"id": "a"}, {"id": "b"}]

    driver = FakeDriver(respond)
    assert pub.recent_from_graph(driver, 2026, 5) == ["a", "b"]
    query, params = driver.calls[0]
    assert query == pub.RECENT_QUERY
    assert params == {"season": 2026, "week": 5, "weeks": pub.NOVELTY_WEEKS}
    assert "pi.week < $week" in query and "pi.week >= $week - $weeks" in query
