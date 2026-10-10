"""LD03: the review's files (`write_week` / `read_week`, `write_season` / `read_season`), the store
that serves them (`ReviewStore`: memory, the official files, the app's cache folder, a build; one
build per key; background writes), the runner behind `nfl live review` (`run_review`), the command
itself and the W&B run (`log_wandb`).

Everything runs on the hand-made season world (`live_review_fixtures`) in `tmp_path` with the stub
engine; nothing is written to the data drive, the network is guarded, and W&B is a fake run (the
real `wandb.Table` / `wandb.plot` objects are built, offline). Hand-worked numbers are in
`live_review_fixtures`' docstring.
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import polars as pl
import pytest
import wandb
from live_fakes import no_real_network  # noqa: F401  (autouse guard)
from live_review_fixtures import factory, season_engine, season_world
from typer.testing import CliRunner

from nflengine import cli, tracking
from nflengine.cli import app
from nflengine.live import review

runner = CliRunner()
approx = pytest.approx


def make_env(tmp_path, *, with_week4: bool = False) -> SimpleNamespace:
    w = season_world(with_week4=with_week4)
    paths = w.write(tmp_path / "data")
    fixes = tmp_path / "fixes.csv"
    fixes.write_text("season,team,from_week,coach\n", encoding="utf-8")
    engine = season_engine()
    return SimpleNamespace(
        w=w, paths=paths, fixes=fixes, engine=engine, fac=factory(engine), root=tmp_path
    )


def store_of(env, **kw) -> review.ReviewStore:
    return review.ReviewStore(fixes_path=env.fixes, **kw)


def fresh_factory(version: str = "v1"):
    engine = season_engine()
    return engine, factory(engine, version)


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


def week_folder(env, week: int = 1):
    return env.paths.live_data / "2025" / f"week{week:02d}"


# --- the files ------------------------------------------------------------------------------------
def test_a_week_file_round_trips_when_the_stamp_matches(env, tmp_path):
    rv = store_of(env).week(env.paths, 2025, 1, env.fac, "v1")
    folder = tmp_path / "out"
    written = review.write_week(folder, rv)
    assert [p.name for p in written] == ["decision_review.parquet", "decision_review.json"]
    assert sorted(p.name for p in folder.iterdir()) == [
        "decision_review.json",
        "decision_review.parquet",
    ]
    got = review.read_week(folder, 2025, 1, rv.meta["stamp"])
    assert got is not None and got.source == "file"
    assert got.rows.to_dicts() == rv.rows.to_dicts() and got.rows.schema == rv.rows.schema
    assert got.meta["stamp"] == rv.meta["stamp"] and got.meta["decisions"] == 6


@pytest.mark.parametrize(
    "ask",
    [
        lambda rv: (2024, 1, rv.meta["stamp"]),
        lambda rv: (2025, 2, rv.meta["stamp"]),
        lambda rv: (2025, 1, {**rv.meta["stamp"], "model_version": "other"}),
        lambda rv: (2025, 1, {**rv.meta["stamp"], "data": "0" * 24}),
        lambda rv: (2025, 1, {**rv.meta["stamp"], "fixes": "different"}),
        lambda rv: (2025, 1, {**rv.meta["stamp"], "schema": review.SCHEMA_VERSION + 1}),
    ],
    ids=["season", "week", "version", "data", "fixes", "schema"],
)
def test_a_stored_week_is_used_only_for_the_stamp_it_was_built_with(env, tmp_path, ask):
    rv = store_of(env).week(env.paths, 2025, 1, env.fac, "v1")
    review.write_week(tmp_path / "out", rv)
    season, week, want = ask(rv)
    assert review.read_week(tmp_path / "out", season, week, want) is None


def test_a_missing_week_file_is_a_miss(tmp_path):
    assert review.read_week(tmp_path / "nothing", 2025, 1, {}) is None


def test_a_season_file_round_trips_and_checks_its_stamp(env, tmp_path):
    store = store_of(env)
    tables, _ = store.season(env.paths, 2025, 2, env.fac, "v1")
    folder = tmp_path / "season"
    pq, js = review.write_season(folder, tables)
    assert (pq.name, js.name) == ("season_review.parquet", "season_review.json")
    board = pl.read_parquet(pq)
    assert board["coach"].to_list() == ["Home Coach", "Away Coach"] and "teams" in board.columns
    want = tables["stamp"]
    got = review.read_season(folder, 2025, 2, want)
    assert got is not None and got["through_week"] == 2 and got["decisions"] == 9
    assert got["leaderboard"][0]["coach"] == "Home Coach"
    assert review.read_season(folder, 2025, 3, want) is None, "another week"
    assert review.read_season(folder, 2024, 2, want) is None, "another season"
    assert review.read_season(folder, 2025, 2, {**want, "data": "x"}) is None
    assert review.read_season(tmp_path / "none", 2025, 2, want) is None


def test_a_season_file_without_decisions_is_still_written_and_read(tmp_path):
    tables = {
        "schema": review.SCHEMA_VERSION, "season": 2025, "through_week": 3, "stamp": {"schema": 1},
        "leaderboard": [],
    }  # fmt: skip
    review.write_season(tmp_path, tables)
    assert pl.read_parquet(tmp_path / "season_review.parquet").height == 0
    assert review.read_season(tmp_path, 2025, 3, {"schema": 1}) is not None


@pytest.mark.parametrize(
    "doc",
    [
        "{not json",
        "[]",
        json.dumps(
            {"schema": 99, "season": 2025, "through_week": 2, "stamp": {}, "leaderboard": []}
        ),
        json.dumps(
            {"schema": 1, "season": 2025, "through_week": 2, "stamp": {}, "leaderboard": {}}
        ),
    ],
    ids=["corrupt", "list", "schema", "leaderboard-type"],
)
def test_a_bad_season_file_is_a_miss(tmp_path, doc):
    (tmp_path / "season_review.json").write_text(doc, encoding="utf-8")
    assert review.read_season(tmp_path, 2025, 2, {}) is None


def test_the_newest_stored_week_is_read_from_the_season_file(tmp_path):
    assert review._stored_through(tmp_path) is None  # noqa: SLF001
    (tmp_path / "season_review.json").write_text('{"through_week": 7}', encoding="utf-8")
    assert review._stored_through(tmp_path) == 7  # noqa: SLF001
    (tmp_path / "season_review.json").write_text("{nope", encoding="utf-8")
    assert review._stored_through(tmp_path) is None  # noqa: SLF001
    (tmp_path / "season_review.json").write_text("{}", encoding="utf-8")
    assert review._stored_through(tmp_path) is None  # noqa: SLF001


# --- the store: one week --------------------------------------------------------------------------
def test_a_store_without_folders_builds_a_week_once_and_remembers_it(env):
    store = store_of(env)
    rv = store.week(env.paths, 2025, 1, env.fac, "v1")
    assert rv.source == "computed" and rv.rows.height == 6 and rv.meta["model_version"] == "v1"
    assert env.engine.calls == [(6, True)] and len(env.fac.calls) == 1
    again = store.week(env.paths, 2025, 1, env.fac, "v1")
    assert again.rows is rv.rows and again.source == "memory", "the same build, from memory"
    assert len(env.engine.calls) == 1 and len(env.fac.calls) == 1
    assert not env.paths.live_data.exists(), "nothing is written without a folder to write to"


def test_the_store_knows_which_weeks_have_plays(env, tmp_path):
    store = store_of(env)
    assert store.weeks(env.paths, 2025) == [1, 2]
    assert store.weeks(env.paths, 2030) == []
    assert store.plays(env.paths, 2030).height == 0
    assert store.plays(env.paths, 2025) is store.plays(env.paths, 2025), "read once"


def test_the_official_store_writes_the_files_and_any_store_reads_them(env):
    rv = store_of(env, official=True).week(env.paths, 2025, 1, env.fac, "v1")
    folder = week_folder(env)
    assert sorted(p.name for p in folder.iterdir()) == [
        "decision_review.json", "decision_review.parquet",
    ]  # fmt: skip
    meta = json.loads((folder / "decision_review.json").read_text(encoding="utf-8"))
    assert meta["stamp"] == rv.meta["stamp"] and meta["decisions"] == 6 and meta["season"] == 2025
    assert pl.read_parquet(folder / "decision_review.parquet").columns == review.ROW_COLUMNS
    engine, fac = fresh_factory()
    got = store_of(env).week(env.paths, 2025, 1, fac, "v1")  # an app-style store
    assert got.source == "file" and engine.calls == [] and fac.calls == []
    assert got.rows.to_dicts() == rv.rows.to_dicts()


def test_the_apps_store_writes_only_its_cache_folder(env, tmp_path):
    cache = tmp_path / "cache" / "review"
    store_of(env, cache_folder=cache).week(env.paths, 2025, 1, env.fac, "v1")
    assert (cache / "2025-w01" / "decision_review.parquet").exists()
    assert (cache / "2025-w01" / "decision_review.json").exists()
    assert not env.paths.live_data.exists(), "the official folder is the command's"
    engine, fac = fresh_factory()
    got = store_of(env, cache_folder=cache).week(env.paths, 2025, 1, fac, "v1")
    assert got.source == "cache" and engine.calls == []


def test_the_official_store_ignores_a_cache_folder(env, tmp_path):
    cache = tmp_path / "cache"
    store_of(env, official=True, cache_folder=cache).week(env.paths, 2025, 1, env.fac, "v1")
    assert week_folder(env).exists() and not cache.exists()


def test_an_official_file_is_read_before_the_cache(env, tmp_path):
    cache = tmp_path / "cache"
    store_of(env, cache_folder=cache).week(env.paths, 2025, 1, env.fac, "v1")
    store_of(env, official=True).week(env.paths, 2025, 1, env.fac, "v1")
    engine, fac = fresh_factory()
    got = store_of(env, cache_folder=cache).week(env.paths, 2025, 1, fac, "v1")
    assert got.source == "file" and engine.calls == []


def corrupt_json(folder):
    (folder / "decision_review.json").write_text("{not json", encoding="utf-8")


def json_list(folder):
    (folder / "decision_review.json").write_text("[]", encoding="utf-8")


def edit_meta(**changes):
    def edit(folder):
        path = folder / "decision_review.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        for k, v in changes.items():
            meta[k] = v
        path.write_text(json.dumps(meta), encoding="utf-8")

    return edit


def edit_stamp(**changes):
    def edit(folder):
        path = folder / "decision_review.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        meta["stamp"] = {**meta["stamp"], **changes}
        path.write_text(json.dumps(meta), encoding="utf-8")

    return edit


def corrupt_parquet(folder):
    (folder / "decision_review.parquet").write_bytes(b"this is not a parquet file")


def empty_parquet(folder):
    (folder / "decision_review.parquet").write_bytes(b"")


def drop_a_column(folder):
    path = folder / "decision_review.parquet"
    pl.read_parquet(path).drop("desc").write_parquet(path)


def reorder_columns(folder):
    path = folder / "decision_review.parquet"
    df = pl.read_parquet(path)
    df.select(df.columns[::-1]).write_parquet(path)


@pytest.mark.parametrize(
    "damage",
    [
        corrupt_json, json_list, edit_meta(schema=99), edit_meta(season=2024), edit_meta(week=7),
        edit_stamp(model_version="old"), edit_stamp(data="0" * 24), edit_stamp(fixes="old"),
        lambda folder: (folder / "decision_review.json").unlink(),
        corrupt_parquet, empty_parquet, drop_a_column, reorder_columns,
        lambda folder: (folder / "decision_review.parquet").unlink(),
    ],
    ids=["json-corrupt", "json-list", "schema", "season", "week", "stamp-version", "stamp-data",
         "stamp-fixes", "json-missing", "parquet-corrupt", "parquet-empty", "parquet-column",
         "parquet-order", "parquet-missing"],
)  # fmt: skip
def test_a_damaged_or_stale_file_is_a_miss_and_is_rebuilt(env, damage):
    store_of(env, official=True).week(env.paths, 2025, 1, env.fac, "v1")
    damage(week_folder(env))
    engine, fac = fresh_factory()
    rv = store_of(env, official=True).week(env.paths, 2025, 1, fac, "v1")
    assert rv.source == "computed" and rv.rows.height == 6 and engine.calls == [(6, True)]
    engine2, fac2 = fresh_factory()
    healed = store_of(env).week(env.paths, 2025, 1, fac2, "v1")
    assert healed.source == "file" and engine2.calls == [], "the rebuilt file is good"


def test_a_new_model_version_rebuilds_the_week(env):
    store_of(env, official=True).week(env.paths, 2025, 1, env.fac, "v1")
    engine, fac = fresh_factory("v2")
    rv = store_of(env, official=True).week(env.paths, 2025, 1, fac, "v2")
    assert rv.source == "computed" and rv.meta["stamp"]["model_version"] == "v2"
    assert engine.calls == [(6, True)]
    meta = json.loads((week_folder(env) / "decision_review.json").read_text(encoding="utf-8"))
    assert meta["stamp"]["model_version"] == "v2"


def test_changed_curated_plays_rebuild_the_week(env):
    store_of(env, official=True).week(env.paths, 2025, 1, env.fac, "v1")
    env.w.rows[0]["desc"] = "a play text that is different from before"
    env.w.write(env.root / "data")
    engine, fac = fresh_factory()
    rv = store_of(env, official=True).week(env.paths, 2025, 1, fac, "v1")
    assert rv.source == "computed" and engine.calls == [(6, True)]


def test_changed_coach_fixes_rebuild_the_week(env):
    store_of(env, official=True).week(env.paths, 2025, 1, env.fac, "v1")
    env.fixes.write_text(
        "season,team,from_week,coach\n2025,HOM,1,Interim Coach\n", encoding="utf-8"
    )
    engine, fac = fresh_factory()
    rv = store_of(env, official=True).week(env.paths, 2025, 1, fac, "v1")
    assert rv.source == "computed"
    assert set(rv.rows.filter(pl.col("posteam") == "HOM")["coach"].to_list()) == {"Interim Coach"}


def test_a_running_store_notices_changed_plays(env):
    store = store_of(env)
    first = store.week(env.paths, 2025, 1, env.fac, "v1")
    env.w.rows[0]["desc"] = "a play text that is different from before"
    env.w.write(env.root / "data")
    second = store.week(env.paths, 2025, 1, env.fac, "v1")
    assert second is not first and len(env.engine.calls) == 2


def test_a_cache_that_cannot_be_written_does_not_fail_the_review(env, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a folder should be", encoding="utf-8")
    store = store_of(env, cache_folder=blocker / "cache")
    rv = store.week(env.paths, 2025, 1, env.fac, "v1")
    assert rv.rows.height == 6
    again = store.week(env.paths, 2025, 1, env.fac, "v1")
    assert again.rows is rv.rows and again.source == "memory", "still served from memory"


# --- the store: writes on a thread, one build per key ---------------------------------------------
def test_the_apps_cache_is_written_on_a_background_thread_and_flush_waits_for_it(
    env, tmp_path, monkeypatch
):
    gate = threading.Event()
    real = review.write_week
    threads: list[str] = []

    def slow(folder, rv):
        threads.append(threading.current_thread().name)
        gate.wait(5)
        return real(folder, rv)

    monkeypatch.setattr(review, "write_week", slow)
    cache = tmp_path / "cache"
    store = store_of(env, cache_folder=cache, background_writes=True)
    rv = store.week(env.paths, 2025, 1, env.fac, "v1")  # returns while the write waits
    assert rv.rows.height == 6
    assert not (cache / "2025-w01" / "decision_review.json").exists()
    gate.set()
    store.flush(5)
    assert (cache / "2025-w01" / "decision_review.json").exists()
    assert threads == ["live-review-write"]


def test_the_official_store_writes_in_the_calling_thread_even_if_background_is_asked(
    env, monkeypatch
):
    threads: list[str] = []
    real = review.write_week

    def spy(folder, rv):
        threads.append(threading.current_thread().name)
        return real(folder, rv)

    monkeypatch.setattr(review, "write_week", spy)
    store_of(env, official=True, background_writes=True).week(env.paths, 2025, 1, env.fac, "v1")
    assert threads == [threading.current_thread().name], "no background thread: it exits next"
    assert week_folder(env).joinpath("decision_review.json").exists()


def test_two_threads_asking_for_one_week_build_it_once(env):
    calls: list[int] = []

    def slow_factory():
        calls.append(1)
        time.sleep(0.3)
        return env.engine, "v1"

    store = store_of(env)
    barrier = threading.Barrier(2)
    got: list[review.WeekReview] = []

    def ask():
        barrier.wait(5)
        got.append(store.week(env.paths, 2025, 1, slow_factory, "v1"))

    threads = [threading.Thread(target=ask) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(got) == 2 and got[0].rows is got[1].rows, "one build, shared"
    assert sorted(g.source for g in got) == ["computed", "memory"]
    assert len(calls) == 1 and env.engine.calls == [(6, True)]


def two_threads_ask_for_the_season(env, calls):
    """Both threads pass the memory check before the first one has finished (the factory is
    slow, and every week is built on the way)."""

    def slow_factory():
        calls.append(1)
        time.sleep(0.2)
        return env.engine, "v1"

    store = store_of(env)
    barrier = threading.Barrier(2)
    got: list[tuple[dict, str]] = []

    def ask():
        barrier.wait(5)
        got.append(store.season(env.paths, 2025, 2, slow_factory, "v1"))

    threads = [threading.Thread(target=ask) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    return got


def test_two_threads_asking_for_one_season_table_score_each_week_once(env):
    got = two_threads_ask_for_the_season(env, [])
    assert len(got) == 2 and got[0][0] == got[1][0]
    assert env.engine.calls == [(6, True), (3, True)], "each week once, nothing twice"


def test_two_threads_asking_for_one_season_table_compute_it_once(env, monkeypatch):
    built: list[int] = []
    real = review.season_tables

    def counting(*a, **kw):
        built.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(review, "season_tables", counting)
    got = two_threads_ask_for_the_season(env, [])
    assert sorted(src for _, src in got) == ["computed", "memory"]
    assert len(built) == 1


# --- the store: the season ------------------------------------------------------------------------
def test_the_season_tables_reuse_the_week_reviews(env):
    store = store_of(env, official=True)
    store.week(env.paths, 2025, 1, env.fac, "v1")
    store.week(env.paths, 2025, 2, env.fac, "v1")
    assert env.engine.calls == [(6, True), (3, True)]
    tables, src = store.season(env.paths, 2025, 2, env.fac, "v1")
    assert src == "computed" and tables["through_week"] == 2 and tables["weeks"] == [1, 2]
    assert env.engine.calls == [(6, True), (3, True)], "no 4th down was scored again"
    assert tables["decisions"] == 9 and tables["league"]["decisions"] == 9


def test_the_season_asks_for_the_weeks_it_is_missing(env):
    tables, src = store_of(env).season(env.paths, 2025, 2, env.fac, "v1")
    assert src == "computed" and tables["decisions"] == 9
    assert env.engine.calls == [(6, True), (3, True)]


def test_the_official_season_file_and_when_it_is_overwritten(env):
    store = store_of(env, official=True)
    season_json = env.paths.live_data / "2025" / "season_review.json"
    tables, src = store.season(env.paths, 2025, 2, env.fac, "v1")
    assert src == "computed"
    doc = json.loads(season_json.read_text(encoding="utf-8"))
    assert doc["through_week"] == 2 and doc["stamp"] == tables["stamp"]
    assert pl.read_parquet(season_json.with_suffix(".parquet"))["coach"].to_list() == [
        "Home Coach", "Away Coach",
    ]  # fmt: skip
    again, src = store.season(env.paths, 2025, 2, env.fac, "v1")
    assert src == "memory" and again is tables
    # an earlier week after a later one: answered, but the stored file keeps the later one
    early, src = store.season(env.paths, 2025, 1, env.fac, "v1")
    assert src == "computed" and early["weeks"] == [1] and early["decisions"] == 6
    assert json.loads(season_json.read_text(encoding="utf-8"))["through_week"] == 2
    # a later week replaces it
    later, _ = store.season(env.paths, 2025, 3, env.fac, "v1")
    assert later["through_week"] == 3 and later["weeks"] == [1, 2]
    assert json.loads(season_json.read_text(encoding="utf-8"))["through_week"] == 3


def test_a_fresh_store_reads_the_official_season_file(env):
    store_of(env, official=True).season(env.paths, 2025, 2, env.fac, "v1")
    engine, fac = fresh_factory()
    tables, src = store_of(env).season(env.paths, 2025, 2, fac, "v1")
    assert src == "file" and engine.calls == [] and fac.calls == []
    assert tables["leaderboard"][0]["coach"] == "Home Coach"
    # the other through-week isn't in that file
    _, src = store_of(env).season(env.paths, 2025, 1, fac, "v1")
    assert src == "computed"


def test_the_apps_season_tables_live_in_its_cache(env, tmp_path):
    cache = tmp_path / "cache"
    store_of(env, cache_folder=cache).season(env.paths, 2025, 2, env.fac, "v1")
    folder = cache / "2025-season-w02"
    assert (folder / "season_review.json").exists() and (folder / "season_review.parquet").exists()
    assert (cache / "2025-w01" / "decision_review.json").exists(), "and so are the weeks"
    assert not env.paths.live_data.exists()
    engine, fac = fresh_factory()
    _, src = store_of(env, cache_folder=cache).season(env.paths, 2025, 2, fac, "v1")
    assert src == "cache" and engine.calls == []


def test_changed_plays_or_a_new_model_rebuild_the_season_tables(env):
    store_of(env, official=True).season(env.paths, 2025, 2, env.fac, "v1")
    engine, fac = fresh_factory("v2")
    tables, src = store_of(env, official=True).season(env.paths, 2025, 2, fac, "v2")
    assert src == "computed" and tables["model_version"] == "v2"
    env.w.rows[0]["desc"] = "a play text that is different from before"
    env.w.write(env.root / "data")
    engine3, fac3 = fresh_factory("v2")
    _, src = store_of(env, official=True).season(env.paths, 2025, 2, fac3, "v2")
    assert src == "computed" and engine3.calls != []


def test_the_season_of_a_year_without_plays(env):
    tables, src = store_of(env).season(env.paths, 2030, 3, env.fac, "v1")
    assert src == "computed" and tables["weeks"] == [] and tables["decisions"] == 0
    assert tables["leaderboard"] == [] and tables["calibration"] == {
        "wp": None, "conversion": None, "fg": None,
    }  # fmt: skip


# --- run_review (the command's runner) ------------------------------------------------------------
def test_the_runner_reviews_each_week_and_skips_the_ones_without_plays(env):
    lines: list[str] = []
    out = review.run_review(
        2025, [2, 1, 3], paths=env.paths, engine_factory=env.fac, log=lines.append
    )
    assert [e["week"] for e in out] == [1, 2], "in week order; week 3 has no plays"
    assert lines[0] == (
        "week 1: 6 decisions (computed); coaches go 3, the bot 4; agree 3; toss-ups 1; "
        "wins given up 0.17"
    )
    assert lines[1] == (
        "week 2: 3 decisions (computed); coaches go 1, the bot 2; agree 2; toss-ups 0; "
        "wins given up 0.05"
    )
    assert lines[2] == "week 3: no curated plays for 2025 week 3 yet: skipped"
    first, second = out
    assert first["source"] == "computed" and first["wandb"] is None
    assert first["payload"]["summary"]["decisions"] == 6 and first["tables"]["through_week"] == 1
    assert second["tables"]["through_week"] == 2 and second["tables"]["decisions"] == 9
    assert sorted(p.name for p in (env.paths.live_data / "2025").iterdir()) == [
        "season_review.json", "season_review.parquet", "week01", "week02",
    ]  # fmt: skip
    doc = json.loads((env.paths.live_data / "2025" / "season_review.json").read_text("utf-8"))
    assert doc["through_week"] == 2


def test_running_the_command_again_reads_the_files_it_wrote(env):
    review.run_review(2025, [1, 2], paths=env.paths, engine_factory=env.fac, log=lambda s: None)
    engine, fac = fresh_factory()
    lines: list[str] = []
    out = review.run_review(2025, [1, 2], paths=env.paths, engine_factory=fac, log=lines.append)
    assert [e["source"] for e in out] == ["file", "file"] and engine.calls == []
    assert "(file)" in lines[0]


def test_the_runner_with_nothing_to_review(env):
    lines: list[str] = []
    out = review.run_review(2025, [9], paths=env.paths, engine_factory=env.fac, log=lines.append)
    assert out == [] and lines == ["week 9: no curated plays for 2025 week 9 yet: skipped"]
    assert not env.paths.live_data.exists()


def test_the_runner_logs_a_wandb_run_per_week(env, monkeypatch):
    seen: list[tuple[tuple, dict]] = []

    def fake_log(*a, **kw):
        seen.append((a, kw))
        return {"id": "abc", "url": "https://wandb.example/runs/abc"}

    monkeypatch.setattr(review, "log_wandb", fake_log)
    lines: list[str] = []
    out = review.run_review(
        2025, [1, 2], paths=env.paths, engine_factory=env.fac, log=lines.append,
        use_wandb=True, launched_by="agent", smoke=True,
    )  # fmt: skip
    assert [a[:2] for a, _ in seen] == [(2025, 1), (2025, 2)]
    for _, kw in seen:
        assert kw["launched_by"] == "agent" and kw["smoke"] is True and kw["paths"] is env.paths
    assert [e["wandb"]["id"] for e in out] == ["abc", "abc"]
    assert all(line.endswith("; W&B https://wandb.example/runs/abc") for line in lines)


# --- the command ----------------------------------------------------------------------------------
@pytest.fixture
def wide(monkeypatch):
    monkeypatch.setattr(cli.console, "_width", 250)  # no wrapping inside tables and long lines


@pytest.fixture
def entries(env):
    return review.run_review(
        2025, [1, 2], paths=env.paths, engine_factory=env.fac, log=lambda s: None
    )


def patch_runner(monkeypatch, result=None, error=None):
    calls: list[tuple[tuple, dict]] = []

    def fake(*a, **kw):
        calls.append((a, kw))
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(review, "run_review", fake)
    return calls


def test_the_command_needs_a_week(wide, monkeypatch):
    calls = patch_runner(monkeypatch, [])
    r = runner.invoke(app, ["live", "review", "--season", "2025"])
    assert r.exit_code == 2 and "Give --week N or --weeks A-B" in r.output and calls == []


def test_the_command_prints_the_highlights_and_the_leaderboard(wide, monkeypatch, entries):
    calls = patch_runner(monkeypatch, entries)
    r = runner.invoke(app, ["live", "review", "--season", "2025", "--weeks", "1-2"])
    assert r.exit_code == 0, r.output
    ((args, kw),) = calls
    assert args == (2025, [1, 2])
    assert (kw["use_wandb"], kw["launched_by"], kw["smoke"]) == (False, None, False)
    assert callable(kw["log"])
    # week 2 is the last one: its costliest call (AWY punted on 4th & 5; the bot says go, 5 points)
    assert (
        "costliest: AWY (Away Coach), Q3 10:00, 4th & 5 at 50: coach punt, bot go (Confident), "
        "cost 5.0 pts, HOM ball at own 20"
    ) in r.output
    assert "boldest" not in r.output, "no go in week 2 was a real choice"
    assert "2025 through week 2: go rate where the bot says go" in r.output
    assert "Home Coach" in r.output and "Away Coach" in r.output
    assert "50%" in r.output and "33%" in r.output and "0.19" in r.output


def test_the_boldest_call_is_printed_when_there_is_one(wide, monkeypatch, entries):
    patch_runner(monkeypatch, entries[:1])  # week 1 is the last
    r = runner.invoke(app, ["live", "review", "--season", "2025", "--week", "1"])
    assert r.exit_code == 0, r.output
    assert (
        "boldest: AWY (Away Coach), Q3 10:00, 4th & 8 at HOM 45: coach go, bot punt (Lean), "
        "cost 8.0 pts, Stopped (+2 yds, needed 8)"
    ) in r.output
    assert "costliest: AWY (Away Coach)" in r.output


def test_weeks_win_over_a_single_week_and_the_flags_pass_through(wide, monkeypatch, entries):
    calls = patch_runner(monkeypatch, entries)
    r = runner.invoke(
        app,
        ["live", "review", "--season", "2025", "--week", "5", "--weeks", "3-4", "--wandb",
         "--smoke", "--launched-by", "agent"],
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    ((args, kw),) = calls
    assert args == (2025, [3, 4])
    assert (kw["use_wandb"], kw["launched_by"], kw["smoke"]) == (True, "agent", True)
    calls.clear()
    runner.invoke(app, ["live", "review", "--season", "2025", "--week", "5"])
    assert calls[0][0] == (2025, [5])


def test_the_command_says_when_nothing_was_reviewed(wide, monkeypatch):
    patch_runner(monkeypatch, [])
    r = runner.invoke(app, ["live", "review", "--season", "2025", "--week", "9"])
    assert r.exit_code == 3 and "Nothing reviewed" in r.output


def test_the_command_reports_missing_models_without_a_traceback(wide, monkeypatch):
    patch_runner(monkeypatch, error=FileNotFoundError("no promoted bundle [bold red]x[/]"))
    r = runner.invoke(app, ["live", "review", "--season", "2025", "--week", "1"])
    assert r.exit_code == 1
    assert "no promoted bundle [bold red]x[/]" in r.output, "printed as text, not as markup"
    assert "Traceback" not in r.output


def fake_entry(n_coaches: int, **row_changes) -> dict:
    board = [
        {"rank": i, "coach": f"Coach {i:02d}", "teams": [f"T{i:02d}"], "go_spots": 10 - i,
         "went_in_go_spots": 5, "go_rate_spots": 1 - i / 20, "wp_lost": i / 10, **row_changes}
        for i in range(1, n_coaches + 1)
    ]  # fmt: skip
    return {
        "week": 3,
        "payload": {"highlights": {"boldest": None, "costliest": None}},
        "tables": {"leaderboard": board},
    }


def test_a_long_leaderboard_shows_the_top_five_and_the_bottom_five(wide, monkeypatch):
    patch_runner(monkeypatch, [fake_entry(12)])
    r = runner.invoke(app, ["live", "review", "--season", "2025", "--week", "3"])
    assert r.exit_code == 0, r.output
    for shown in (1, 2, 3, 4, 5, 8, 9, 10, 11, 12):
        assert f"Coach {shown:02d}" in r.output
    assert "Coach 06" not in r.output and "Coach 07" not in r.output
    patch_runner(monkeypatch, [fake_entry(10)])
    r = runner.invoke(app, ["live", "review", "--season", "2025", "--week", "3"])
    assert "Coach 06" in r.output, "ten rows or fewer: all of them"


def test_coach_names_are_text_and_a_missing_rate_is_a_dash(wide, monkeypatch):
    entry = fake_entry(1, coach="[red]Boom[/red]", teams=None, go_rate_spots=None)
    patch_runner(monkeypatch, [entry])
    r = runner.invoke(app, ["live", "review", "--season", "2025", "--week", "3"])
    assert r.exit_code == 0, r.output
    assert "[red]Boom[/red]" in r.output


# --- the W&B run ----------------------------------------------------------------------------------
class FakeRun:
    id = "run1"
    url = "https://wandb.example/team/proj/runs/run1"

    def __init__(self):
        self.logged: list[dict] = []
        self.summary_updates: list[dict] = []
        self.finished: list[int] = []
        self.summary = SimpleNamespace(update=self.summary_updates.append)

    def log(self, data):
        self.logged.append(data)

    def finish(self, exit_code=0):
        self.finished.append(exit_code)


@pytest.fixture
def fake_wandb(monkeypatch):
    run = FakeRun()
    init: dict = {}

    def init_run(**kw):
        init.update(kw)
        return run

    monkeypatch.setattr(tracking, "init_run", init_run)
    monkeypatch.setattr(tracking, "dataset_version", lambda paths: {"pbp_snapshot": "2025-12-31"})
    monkeypatch.setattr(tracking, "git_commit", lambda: "abc1234")
    return SimpleNamespace(run=run, init=init)


def review_of(env, week=1):
    store = store_of(env)
    rv = store.week(env.paths, 2025, week, env.fac, "v1")
    tables, _ = store.season(env.paths, 2025, week, env.fac, "v1")
    payload = review.week_payload(rv, review.week_games(env.paths, 2025, week))
    return rv, tables, payload


def test_the_wandb_run_is_a_decision_review_with_the_documented_tags(env, fake_wandb):
    rv, tables, payload = review_of(env)
    out = review.log_wandb(2025, 1, rv, tables, payload, launched_by="agent", paths=env.paths)
    assert out == {"id": "run1", "url": FakeRun.url}
    kw = fake_wandb.init
    assert (kw["group"], kw["job_type"], kw["name"]) == (
        "live-decisions", "decision-review", "decision-review-2025-w01",
    )  # fmt: skip
    assert kw["launched_by"] == "agent"
    assert kw["tags"] == ["ld03", "season:2025", "week:01", "in-sample"]
    cfg = kw["config"]
    assert (cfg["season"], cfg["week"], cfg["through_week"]) == (2025, 1, 1)
    assert (cfg["model"], cfg["model_version"], cfg["in_sample"]) == (
        "live-decision-models", "v1", True,
    )  # fmt: skip
    assert cfg["trained_seasons"] == [2010, 2025] and cfg["review_schema"] == review.SCHEMA_VERSION
    assert cfg["definitions"] == review.DEFINITIONS
    assert (
        cfg["dataset_version"] == {"pbp_snapshot": "2025-12-31"} and cfg["git_commit"] == "abc1234"
    )
    assert fake_wandb.run.finished == [0]


def test_a_season_after_the_training_window_has_no_in_sample_tag_and_smoke_has_its_own_group(
    env, fake_wandb
):
    rv, tables, payload = review_of(env)
    rv.meta["in_sample"] = False
    review.log_wandb(2025, 1, rv, tables, payload, launched_by=None, paths=env.paths, smoke=True)
    kw = fake_wandb.init
    assert kw["tags"] == ["ld03", "season:2025", "week:01", "smoke"]
    assert kw["group"] == "live-decisions-smoke" and kw["config"]["in_sample"] is False


def test_the_wandb_run_logs_the_tables_charts_and_scalars(env, fake_wandb):
    rv, tables, payload = review_of(env)
    review.log_wandb(2025, 1, rv, tables, payload, launched_by=None, paths=env.paths)
    (logged,) = fake_wandb.run.logged
    for key in (
        "review/decisions", "review/costliest", "season/leaderboard", "season/wp_calibration",
        "season/wp_calibration_bins", "season/conversion", "season/conversion_rows",
        "season/fg_table", "season/trend", "season/go_rate_by_week",
    ):  # fmt: skip
        assert key in logged, key
    assert logged["week"] == 1
    decisions = logged["review/decisions"]
    assert isinstance(decisions, wandb.Table) and len(decisions.data) == 6
    assert {"choice", "best", "edge", "cost", "wp_go", "wp_fg", "wp_punt", "result"} <= set(
        decisions.columns
    )
    assert len(logged["review/costliest"].data) == 3, "three costly calls in week 1"
    board = logged["season/leaderboard"]
    assert len(board.data) == 2 and "coach" in board.columns
    teams = board.data[0][board.columns.index("teams")]
    assert teams == "HOM", "the team list is joined for the table"
    # scalars: week/* and season/*, numbers only
    scalars = {k: v for k, v in logged.items() if k.startswith(("week/", "season/"))
               and isinstance(v, int | float)}  # fmt: skip
    assert scalars["week/decisions"] == 6 and scalars["week/go_rate_coach"] == approx(0.5)
    assert scalars["week/go_rate_bot"] == approx(0.6667) and scalars["week/agree_rate"] == approx(
        0.5
    )
    assert scalars["week/wp_lost"] == approx(0.17) and scalars["week/toss_ups"] == 1
    assert scalars["week/go_spots"] == 3 and scalars["week/went_in_go_spots"] == 1
    assert scalars["week/costliest_cost"] == approx(0.08) and scalars[
        "week/boldest_convert"
    ] == approx(0.30)
    assert scalars["season/decisions"] == 6 and scalars["season/go_rate_spots"] == approx(
        1 / 3, abs=1e-4
    )
    assert scalars["season/wp_snaps"] > 0 and "season/wp_brier" in scalars
    assert scalars["season/conv4_n"] == 3 and "season/conv3_n" in scalars
    assert scalars["season/fg_made"] == 1.0
    assert fake_wandb.run.summary_updates == [scalars], "the same numbers go to the run summary"
    assert fake_wandb.run.finished == [0]


def test_a_week_without_decisions_logs_only_what_it_has(env, fake_wandb):
    plays = review.load_season(2025, env.paths, env.fixes)
    rv = review.build_week(
        2025, 9, plays, env.engine, model_version="v1", trained_seasons=[2010, 2025], fixes=None
    )
    payload = review.week_payload(rv, None)
    tables = {"through_week": 9, "decisions": 0, "leaderboard": [], "league": {}, "trend": [],
              "calibration": {"wp": None, "conversion": None, "fg": None}}  # fmt: skip
    review.log_wandb(2025, 9, rv, tables, payload, launched_by=None, paths=env.paths)
    (logged,) = fake_wandb.run.logged
    assert "review/decisions" not in logged and "review/costliest" not in logged
    assert "season/leaderboard" not in logged and "season/wp_calibration" not in logged
    assert logged["week/decisions"] == 0 and "week/costliest_cost" not in logged
    assert fake_wandb.run.finished == [0]


def test_a_failure_while_logging_finishes_the_run_as_failed(env, fake_wandb, monkeypatch):
    rv, tables, payload = review_of(env)

    def boom(*a, **kw):
        raise RuntimeError("the table broke")

    monkeypatch.setattr(wandb, "Table", boom)
    with pytest.raises(RuntimeError, match="the table broke"):
        review.log_wandb(2025, 1, rv, tables, payload, launched_by=None, paths=env.paths)
    assert fake_wandb.run.finished == [1] and fake_wandb.run.logged == []


def test_the_scalars_drop_what_is_missing_or_not_a_number():
    payload = {
        "summary": {"decisions": 4, "coach": {"go": 1, "fg": 1, "punt": 2},
                    "bot": {"go": 2, "fg": 0, "punt": 2}, "agree": 2, "toss_ups": 1,
                    "go_spots": 2, "went_in_go_spots": 1, "wp_lost": 0.1, "fakes": 0,
                    "wiped": 1, "skipped": 0},
        "highlights": {"costliest": {"cost": 0.08}, "boldest": None},
    }  # fmt: skip
    tables = {
        "decisions": 10,
        "league": {"go_rate": 0.5, "go_rate_spots": None, "agree_rate": float("nan"),
                   "wp_lost": 0.2},
        "calibration": {
            "wp": {"brier": 0.2, "brier_vegas": 0.19, "ece": None, "snaps": 100, "games": 3},
            "conversion": {"overall": [{"down": 3, "n": 9, "actual": 0.6, "pred": 0.55},
                                       {"down": 4, "n": 0, "actual": None, "pred": None}]},
            "fg": None,
        },
    }  # fmt: skip
    out = review._scalar_metrics(payload, tables)  # noqa: SLF001
    assert out["week/decisions"] == 4 and out["week/go_rate_coach"] == 0.25
    assert out["week/go_rate_bot"] == 0.5 and out["week/agree_rate"] == 0.5
    assert out["season/decisions"] == 10 and out["season/go_rate_coach"] == 0.5
    assert out["season/wp_brier"] == 0.2 and out["season/wp_snaps"] == 100
    assert out["season/conv3_actual"] == 0.6 and out["season/conv4_n"] == 0
    assert out["week/costliest_cost"] == 0.08 and out["week/wiped"] == 1
    for dropped in ("season/go_rate_spots", "season/agree_rate", "season/wp_ece",
                    "season/conv4_actual", "season/fg_made", "week/boldest_convert"):  # fmt: skip
        assert dropped not in out, dropped
    assert all(isinstance(v, int | float) for v in out.values())
