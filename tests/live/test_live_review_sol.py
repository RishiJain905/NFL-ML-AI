"""The Sol review's findings on the LD03 decision review (D121), one regression test each."""

from __future__ import annotations

import threading

import polars as pl
import pytest
from live_fakes import no_real_network  # noqa: F401  (autouse guard)
from live_review_fixtures import G1, World, factory, season_engine, season_world

from nflengine.app.readers.live import _LockedEngine
from nflengine.live import review
from nflengine.paths import DataPaths

NO_FIXES = "no_fixes.csv"


def store_for(tmp_path, **kw):
    return review.ReviewStore(fixes_path=tmp_path / NO_FIXES, **kw)


# --- (3) a pre-snap penalty between a wiped play and the next snap ------------------------------
def test_a_wiped_go_still_counts_when_a_pre_snap_penalty_comes_before_the_next_snap(tmp_path):
    w = World()
    w.game(G1, 1)
    w.add(
        G1, "HOM", "no_play", 4, 6, 40,
        desc="(Shotgun) J.Doe pass short right to X.Ray for 7 yards. PENALTY on AWY, "
        "Defensive Offside, 5 yards, enforced at AWY 40 - No Play.",
    )  # fmt: skip
    w.add(
        G1, "HOM", "no_play", 4, 1, 35,
        desc="(Run formation) PENALTY on AWY, Neutral Zone Infraction, 5 yards, enforced at "
        "AWY 35 - No Play.",
    )  # fmt: skip
    w.snap(G1, "HOM", 30)
    frame = review.decisions_frame(w.load(tmp_path))
    assert frame["play_id"].to_list() == [1], "the go is a decision; the neutral-zone snap isn't"
    assert frame["choice"].to_list() == ["go"] and frame["wiped"].to_list() == [True]
    assert frame["next_down"].to_list() == [1.0], "the next snap that was run"


# --- (15) a punt that ends the half --------------------------------------------------------------
def test_a_punt_that_ends_the_half_doesnt_describe_the_next_halfs_snap(tmp_path):
    w = World()
    w.game(G1, 1)
    w.punt(G1, "HOM", 9, 60, qtr=2, half_seconds_remaining=4.0, game_seconds_remaining=1804.0)
    w.snap(G1, "HOM", 75, qtr=3)  # HOM receives the second-half kickoff
    frame = review.decisions_frame(w.load(tmp_path))
    row = frame.row(0, named=True)
    assert row["next_same_half"] is False
    assert review.outcome(row) == (None, "Punt"), "not 'kicking team kept it'"
    later = {**row, "next_same_half": True, "next_pos": "AWY", "next_yl": 80.0}
    assert review.outcome(later) == (None, "AWY ball at own 20")


# --- (2) the conversion calibration counts attempts only ----------------------------------------
@pytest.mark.parametrize(
    ("ptype", "desc", "aborted", "attempt"),
    [
        ("run", "J.Doe up the middle for 3 yards", 0, True),
        ("pass", "(Punt formation) Direct snap to X.Ray. X.Ray pass short for 9 yards", 0, True),
        ("run", "(Punt formation) J.Smith FUMBLES (Aborted) at HOM 30", 1, False),
        ("no_play", "(Run formation) PENALTY on AWY, Neutral Zone Infraction, 5 yards - No Play.",
         0, False),
        ("no_play", "J.Smith punts 44 yards to AWY 10. PENALTY on AWY, Roughing the Kicker, 15 "
         "yards - No Play.", 0, False),
        ("no_play", "(Shotgun) J.Doe pass incomplete deep left to X.Ray. PENALTY on AWY, "
         "Defensive Holding, 5 yards - No Play.", 0, True),
    ],
    ids=["run", "fake-punt", "aborted-punt", "pre-snap", "roughing-the-kicker", "wiped-go"],
)  # fmt: skip
def test_only_real_attempts_enter_the_conversion_calibration(ptype, desc, aborted, attempt):
    df = pl.DataFrame({"play_type": [ptype], "desc": [desc], "aborted_play": [float(aborted)]})
    assert df.select(review._attempt_expr()).item() is attempt


# --- (4) a coach fix reloads the frame ------------------------------------------------------------
def test_a_coach_fix_reloads_the_plays_and_changes_the_stamp(tmp_path):
    paths = season_world().write(tmp_path)
    fixes = tmp_path / "fixes.csv"
    store = review.ReviewStore(fixes_path=fixes)
    coach = lambda: store.plays(paths, 2025).filter(pl.col("posteam") == "HOM")["coach"][0]  # noqa: E731
    before = store.week_stamp(paths, 2025, 1, "v1")
    assert coach() == "Home Coach"
    fixes.write_text("season,team,from_week,coach\n2025,HOM,1,New Coach\n", encoding="utf-8")
    assert coach() == "New Coach", "the cached frame isn't reused under the new fixes"
    assert store.week_stamp(paths, 2025, 1, "v1") != before
    rv = store.week(paths, 2025, 1, factory(season_engine()), "v1")
    assert "New Coach" in rv.rows["coach"].to_list()


# --- (5) a schedule correction changes the stamp -------------------------------------------------
def test_a_neutral_site_correction_changes_the_stamp(tmp_path):
    w = season_world()
    paths = w.write(tmp_path)
    store = store_for(tmp_path)
    before = store.week_stamp(paths, 2025, 1, "v1")
    w.games[G1]["neutral_site"] = True
    w.write(tmp_path)
    after = store.week_stamp(paths, 2025, 1, "v1")
    assert after["data"] != before["data"], "the state's `home` changed: rebuild"


# --- (7) one engine and one version for a season build -------------------------------------------
def test_a_season_build_scores_every_week_with_one_engine_and_version(tmp_path):
    paths = season_world().write(tmp_path)
    store = store_for(tmp_path)
    make = factory(season_engine(), version="v2")
    tables, src = store.season(paths, 2025, 2, make, "v1")  # asked v1, the loaded bundle is v2
    assert src == "computed" and len(make.calls) == 1, "one engine for the whole build"
    assert tables["model_version"] == "v2"
    for wk in (1, 2):
        assert store.cached_week(paths, 2025, wk, "v2").meta["model_version"] == "v2"


# --- (1) the calibration runs under the shared engine's lock ------------------------------------
def test_the_calibration_runs_under_the_shared_engines_lock(tmp_path, monkeypatch):
    paths = season_world().write(tmp_path)
    lock = threading.Lock()
    locked = _LockedEngine(season_engine(), lock)
    assert locked.hold() is lock
    seen: list[bool] = []
    real = review.calibration

    def spy(plays, models):
        seen.append(lock.locked())
        return real(plays, models)

    monkeypatch.setattr(review, "calibration", spy)
    store_for(tmp_path).season(paths, 2025, 2, lambda: (locked, "v1"), "v1")
    assert seen == [True]
    assert not lock.locked(), "released afterwards"


# --- (8) a parquet from another write is a miss ---------------------------------------------------
def test_a_parquet_paired_with_another_writes_json_reads_as_a_miss(tmp_path):
    paths = season_world().write(tmp_path)
    store = store_for(tmp_path)
    make = factory(season_engine())
    one = store.week(paths, 2025, 1, make, "v1")
    two = store.week(paths, 2025, 2, make, "v1")
    folder = tmp_path / "out"
    review.write_week(folder, one)
    assert review.read_week(folder, 2025, 1, one.meta["stamp"]) is not None
    two.rows.write_parquet(folder / "decision_review.parquet")  # a different write's rows
    assert review.read_week(folder, 2025, 1, one.meta["stamp"]) is None


# --- (9) a hash stays with the frame it was computed from ----------------------------------------
def test_a_hash_is_kept_with_the_generation_it_was_computed_from(tmp_path):
    w = season_world()
    paths = w.write(tmp_path)
    store = store_for(tmp_path)
    old = store._entry(paths, 2025)
    first = store._hash(paths, 2025, (1,))
    assert old.hashes[(1,)] == first
    w.rows[0]["yards_gained"] = 9.0  # a corrected play
    w.write(tmp_path)
    new = store._entry(paths, 2025)
    assert new is not old and (1,) not in new.hashes
    assert store._hash(paths, 2025, (1,)) != first


# --- (11) the CLI's files: a failed write is an error ---------------------------------------------
def test_an_official_write_that_fails_raises(tmp_path):
    paths = season_world().write(tmp_path)
    (tmp_path / "live").write_text("a file where the live folder should be", encoding="utf-8")
    store = store_for(tmp_path, official=True)
    with pytest.raises(OSError):
        store.week(paths, 2025, 1, factory(season_engine()), "v1")


def test_the_apps_cache_write_that_fails_is_still_best_effort(tmp_path):
    paths: DataPaths = season_world().write(tmp_path)
    (tmp_path / "blocked").write_text("x", encoding="utf-8")
    store = store_for(tmp_path, cache_folder=tmp_path / "blocked" / "cache")
    assert store.week(paths, 2025, 1, factory(season_engine()), "v1").rows.height == 6
