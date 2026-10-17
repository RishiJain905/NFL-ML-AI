"""PC03 on the real Big Data Bowl 2026 files (read only: nothing is written under the data root).

`uv run nfl playcalling library --download --weeks 1-18` put the labels and the 18 weeks of
input / output tracking under the data root's `bdb/2026/raw/`; `nfl playcalling library` built
the concept library at its `playcalling/bdb_library/`. These tests read both. They
skip when the files aren't on this computer (Rishi has to join the competition on Kaggle).
The data root comes from the settings module (`ensure_data_root`), as in every real-data test;
the tests never open the env file. The data never leaves this
computer: nothing here copies it, and the rebuild test writes into a tmp folder.

Run with `uv run pytest -m integration tests/playcalling/test_playcall_bdb_real.py`.
"""

from __future__ import annotations

import hashlib
import json
import math
import re

import polars as pl
import pytest

from nflengine.curate.teams import CANONICAL_TEAMS
from nflengine.paths import DataPaths, DataRootError, ensure_data_root
from nflengine.playcalling import bdb
from nflengine.playcalling import diagrams as D

pytestmark = pytest.mark.integration

LIBRARY_CONCEPTS = {"screen", "play_action_deep", "cover_1", "cover_3", "2_man"}
W = 160 / 3
TEAMS = set(CANONICAL_TEAMS)


@pytest.fixture(scope="module")
def paths() -> DataPaths:
    try:
        p = ensure_data_root(create_dirs=False)
    except DataRootError:
        pytest.skip("the data root isn't available on this computer")
    if not (bdb.raw_dir(p) / "supplementary_data.csv").is_file():
        pytest.skip("the Big Data Bowl 2026 labels aren't downloaded on this computer")
    if not bdb.tracking_weeks(p):
        pytest.skip("no week of Big Data Bowl 2026 tracking (input + output) on this computer")
    return p


@pytest.fixture(scope="module")
def raw(paths) -> bdb.Raw:
    return bdb.load_raw(paths)


@pytest.fixture(scope="module")
def cand(raw) -> pl.DataFrame:
    return bdb.classify(raw.supp)


@pytest.fixture(scope="module")
def supp(raw) -> dict:
    return {(int(r["game_id"]), int(r["play_id"])): r for r in raw.supp.iter_rows(named=True)}


# ---- the files -----------------------------------------------------------------------------------


def test_the_downloaded_files_are_the_ones_the_manifest_names(paths, raw):
    d = bdb.raw_dir(paths)
    man = json.loads((d / "manifest.json").read_text("utf-8"))["files"]
    names = ["supplementary_data.csv", *bdb.week_files(raw.weeks)]
    assert set(names) <= set(man)
    for n in names:
        entry = man[n]
        assert (d / n).stat().st_size == entry["bytes"], n
        assert entry["competition"] == bdb.COMPETITION and entry["kaggle_name"].endswith(n)
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
    # the labels file is small enough to hash every run: it is the file that was downloaded
    h = hashlib.sha256((d / "supplementary_data.csv").read_bytes()).hexdigest()
    assert h == man["supplementary_data.csv"]["sha256"]
    assert not any(k in json.dumps(man).lower() for k in ("token", "password", "authorization"))


def test_all_eighteen_weeks_have_both_tracking_files(paths, raw):
    assert raw.weeks == list(range(1, 19)) == list(range(bdb.WEEKS[0], bdb.WEEKS[1] + 1))


def test_the_labels_and_the_tracking_have_the_columns_the_reader_uses(paths, raw):
    d = bdb.raw_dir(paths)
    inp = pl.read_csv(d / bdb.INPUT.format(season=bdb.SEASON, week=1), n_rows=2000)
    out = pl.read_csv(d / bdb.OUTPUT.format(season=bdb.SEASON, week=1), n_rows=2000)
    need_in = {
        "game_id", "play_id", "nfl_id", "frame_id", "play_direction", "absolute_yardline_number",
        "player_position", "player_side", "player_role", "x", "y", "num_frames_output",
        "ball_land_x", "ball_land_y",
    }  # fmt: skip
    assert need_in <= set(inp.columns), need_in - set(inp.columns)
    assert {"game_id", "play_id", "nfl_id", "frame_id", "x", "y"} <= set(out.columns)
    need_supp = {
        "game_id", "play_id", "week", "home_team_abbr", "visitor_team_abbr", "play_description",
        "possession_team", "defensive_team", "pass_result", "pass_length", "play_action",
        "route_of_targeted_receiver", "team_coverage_type", "penalty_yards",
        "play_nullified_by_penalty", "yards_gained", "expected_points_added", "quarter",
        "game_clock", "down", "yards_to_go", "offense_formation", "receiver_alignment",
    }  # fmt: skip
    assert need_supp <= set(raw.supp.columns), need_supp - set(raw.supp.columns)
    assert set(inp["player_side"]) <= {"Offense", "Defense"}
    assert set(inp["player_role"]) <= {
        "Passer", "Targeted Receiver", "Other Route Runner", "Defensive Coverage",
    }  # fmt: skip
    assert set(inp["play_direction"]) <= {"left", "right"}


# ---- the labels ----------------------------------------------------------------------------------


def test_the_labels_are_pass_plays_of_2023_weeks_1_to_18(raw):
    s = raw.supp
    # weeks 1-13 are 2023 games; weeks 14-18 hold both December 2023 and December 2024 games
    # (the tracking files are all named 2023)
    assert set(s["season"]) == {2023, 2024}
    assert set(s.filter(pl.col("week") <= 13)["season"]) == {2023}
    assert set(s.filter(pl.col("week") >= 14)["season"]) == {2023, 2024}  # both seasons
    assert s["week"].min() == 1 and s["week"].max() == 18
    assert set(s["pass_result"].drop_nulls()) == {"C", "I", "IN"}  # a passing edition: no runs
    assert 15_000 < s.height < 25_000 and s["game_id"].n_unique() > 300
    assert s.select("game_id", "play_id").n_unique() == s.height  # one row per play


def test_the_labels_classify_into_the_five_passing_concepts_only(cand, raw):
    assert 8_000 < cand.height < 15_000
    assert set(cand["concept"]) == LIBRARY_CONCEPTS
    assert set(cand["week"]) == set(range(1, 19))
    per = {
        r["concept"]: r["n"]
        for r in cand.group_by("concept").agg(pl.len().alias("n")).iter_rows(named=True)
    }
    assert min(per.values()) > 100 and per["2_man"] < per["cover_3"]
    good = cand["good"].mean()
    assert 0.3 < good < 0.7
    # every candidate is a clean thrown pass
    joined = cand.join(
        raw.supp.select(
            "game_id", "play_id", "pass_result", "penalty_yards", "play_nullified_by_penalty"
        ),
        on=["game_id", "play_id"],
    )
    assert set(joined["pass_result"]) <= {"C", "I", "IN"}
    assert joined["penalty_yards"].fill_null(0).abs().max() == 0
    assert (
        not joined["play_nullified_by_penalty"]
        .cast(pl.String)
        .str.to_uppercase()
        .is_in(["Y", "TRUE"])
        .any()
    )


def test_pick_spreads_the_library_over_the_season_and_the_games(cand, raw):
    chosen = bdb.pick(cand, raw.weeks, 10)
    assert chosen.height == 50 and set(chosen["concept"]) == LIBRARY_CONCEPTS
    for _, sub in chosen.group_by("concept"):
        assert sub.height == 10 and sub["game_id"].n_unique() == 10  # one play per game
        assert sub["week"].n_unique() >= 5  # the weeks take turns
    again = bdb.pick(cand.reverse(), raw.weeks, 10)  # the order of the rows doesn't matter
    key = ["concept", "game_id", "play_id"]
    assert chosen.sort(key).equals(again.sort(key))


# ---- every candidate play: it builds, or is cleanly skipped --------------------------------------


def check_real(d: dict, supp_row: dict) -> None:
    assert d["kind"] == "real" and d["label"].startswith("Real play: 20")
    assert d["label"] != D.LABEL and d["version"] == D.VERSION
    play = d["play"]
    assert play["type"] == "pass" and play["play_id"] == supp_row["play_id"]
    assert re.fullmatch(r"20\d\d_\d\d_[A-Z]{2,3}_[A-Z]{2,3}", play["game_id"])
    assert play["result"]["kind"] in ("complete", "incomplete", "interception")
    qbs = [p for p in d["players"] if p["role"] == "qb"]
    assert qbs and any(p["target"] for p in d["players"])
    assert 5 <= len(d["players"]) <= 24
    los = d["field"]["los"]
    for p in d["players"]:
        times = [k[0] for k in p["track"]]
        assert times[0] == 0.0 and all(a < b for a, b in zip(times, times[1:], strict=False)), p[
            "id"
        ]
        assert all(math.isfinite(v) for k in p["track"] for v in k)
        assert not p["approx"] and not p["rusher"]
    bt = [b[0] for b in d["ball"]]
    assert bt[0] == 0.0 and all(a < b for a, b in zip(bt, bt[1:], strict=False))
    qb = next(p for p in qbs if p["track"][0] == d["ball"][0][:3])  # the passer holds the ball
    assert d["ball"][0][:3] == qb["track"][0]  # the ball starts with the passer
    # orientation: the offense stands behind the line and the defense in front of it
    off = [p["track"][0][1] for p in d["players"] if p["side"] == "offense"]
    dfn = [p["track"][0][1] for p in d["players"] if p["side"] == "defense"]
    assert sum(off) / len(off) < sum(dfn) / len(dfn)
    assert qb["track"][0][1] <= los + 0.5
    assert d["phases"][-1]["t"] == d["ball"][-1][0] <= d["duration"]
    assert -10 <= d["field"]["view"][0] < d["field"]["view"][1] <= 110


@pytest.mark.parametrize("week", range(1, 19))
def test_every_candidate_play_of_the_week_builds_or_is_cleanly_skipped(
    paths, raw, cand, supp, week
):
    wk = cand.filter(pl.col("week") == week)
    inp, out = bdb.read_frames(paths, week, wk.select("game_id", "play_id").unique())
    by_in = inp.partition_by("game_id", "play_id", as_dict=True)
    by_out = out.partition_by("game_id", "play_id", as_dict=True)
    built = none = untracked = 0
    for n, r in enumerate(wk.iter_rows(named=True)):
        key = (r["game_id"], r["play_id"])
        gi = by_in.get((key[0], key[1]))
        if gi is None:  # the labels have the play, the tracking files don't
            untracked += 1
            continue
        d = bdb.real_diagram(supp[key], gi, by_out.get(key, out.head(0)), r["concept"])
        if d is None:
            none += 1
            continue
        built += 1
        check_real(d, supp[key])
        if n % 25 == 0:
            json.dumps(d, allow_nan=False)
    assert built + none + untracked == wk.height
    assert none <= 2, f"week {week}: {none} plays could not be drawn"
    assert built > 0.5 * wk.height or week >= 14  # the late weeks have untracked plays


def test_the_tracking_files_miss_some_late_plays_the_labels_have(paths, raw, cand):
    """About 21 % of the candidate plays (weeks 14-18) have no tracking rows, none before 14."""
    missing = {}
    for week in (1, 7, 13, 14, 18):
        wk = cand.filter(pl.col("week") == week).select("game_id", "play_id").unique()
        inp, _ = bdb.read_frames(paths, week, wk)
        missing[week] = wk.height - inp.select("game_id", "play_id").n_unique()
    assert missing[1] == missing[7] == missing[13] == 0
    print(f"[bdb] candidate plays without tracking: {missing}")


# ---- the library built on this computer ----------------------------------------------------------


@pytest.fixture(scope="module")
def library(paths):
    lib = bdb.library_dir(paths)
    if not (lib / "index.json").is_file():
        pytest.skip("the concept library isn't built on this computer")
    return lib, json.loads((lib / "index.json").read_text("utf-8"))


def examples(index):
    return [(c, e) for c, v in index["concepts"].items() for e in v["examples"]]


def test_the_library_is_the_five_concepts_with_real_plays_each(library):
    lib, index = library
    assert (index["edition"], index["competition"], index["season"]) == (
        "2026", bdb.COMPETITION, 2023,
    )  # fmt: skip
    assert index["weeks"] == sorted(set(index["weeks"])) and set(index["weeks"]) <= set(
        range(1, 19)
    )
    assert index["note"] == bdb.NOTE
    assert set(index["concepts"]) == LIBRARY_CONCEPTS
    for concept, entry in index["concepts"].items():
        n = len(entry["examples"])
        assert entry["name"] == D.CONCEPTS[concept]["name"] and 3 <= n <= 40, concept
        files = [e["file"] for e in entry["examples"]]
        assert len(set(files)) == n and all(re.fullmatch(r"\d{10}_\d{1,5}\.json", f) for f in files)
        for e in entry["examples"]:
            assert (lib / concept / e["file"]).is_file() and e["label"].startswith("2023 week")
    on_disk = {p.relative_to(lib).as_posix() for p in lib.rglob("*.json")}
    assert on_disk == {"index.json"} | {f"{c}/{e['file']}" for c, e in examples(index)}


def test_every_library_file_is_a_real_play_of_its_concept(library, supp):
    lib, index = library
    weeks = set()
    for concept, e in examples(index):
        d = json.loads((lib / concept / e["file"]).read_text("utf-8"))
        gid, pid = (int(x) for x in e["file"].removesuffix(".json").split("_"))
        check_real(d, supp[(gid, pid)])
        assert d["concept"] == d["play"]["concept"] == concept and d["play"]["play_id"] == pid
        assert e["label"] == bdb.example_label(d)
        weeks.add(d["play"]["week"])
        assert d["play"]["offense"] in TEAMS and d["play"]["defense"] in TEAMS
    assert len(weeks) >= 5  # the examples come from many weeks


def test_the_library_is_what_the_current_code_builds_from_the_raw_files(
    paths, library, monkeypatch, tmp_path
):
    """Deterministic and up to date: rebuilt into a tmp folder from the real raw files, the library
    is the one on this computer, file for file. (Red after a code change = rebuild it with
    `uv run nfl playcalling library`.)"""
    lib, index = library
    tp = DataPaths(tmp_path)
    for d in tp.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    real_raw = bdb.raw_dir(paths)
    monkeypatch.setattr(bdb, "raw_dir", lambda p, edition=bdb.EDITION: real_raw)
    log: list[str] = []
    per = max(len(v["examples"]) for v in index["concepts"].values())
    new = bdb.build_library(tp, per_concept=per, weeks=index["weeks"], log=log.append)
    assert not [s for s in log if "skipped" in s]
    strip = lambda i: {k: v for k, v in i.items() if k != "built_at"}  # noqa: E731
    assert strip(new) == strip(index)
    mine = bdb.library_dir(tp)
    for concept, e in examples(index):
        assert (mine / concept / e["file"]).read_bytes() == (lib / concept / e["file"]).read_bytes()
    assert not list(bdb.raw_dir(paths).glob(".*part"))  # the raw folder is untouched


def test_nothing_was_written_into_the_raw_folder_by_a_build(paths):
    d = bdb.raw_dir(paths)
    names = sorted(p.name for p in d.iterdir())
    assert names == sorted(
        ["manifest.json", "supplementary_data.csv", *bdb.week_files(range(1, 19))]
    )


# ---- the season of a real play is the label's, and one passer per play (red until fixed) ---------


@pytest.mark.parametrize("week", [1, 13, 14, 15, 18])
def test_the_season_of_a_real_play_is_the_one_in_the_labels(paths, raw, cand, supp, week):
    """Weeks 14-18 are the 2024 season, whatever the tracking files are called."""
    wk = cand.filter(pl.col("week") == week).head(40)
    inp, out = bdb.read_frames(paths, week, wk.select("game_id", "play_id").unique())
    by_in = inp.partition_by("game_id", "play_id", as_dict=True)
    by_out = out.partition_by("game_id", "play_id", as_dict=True)
    seen = 0
    for r in wk.iter_rows(named=True):
        key = (r["game_id"], r["play_id"])
        if key not in by_in:
            continue
        d = bdb.real_diagram(supp[key], by_in[key], by_out.get(key, out.head(0)), r["concept"])
        if d is None:
            continue
        season = int(supp[key]["season"])
        seen += 1
        assert d["play"]["season"] == season
        assert d["label"] == f"Real play: {season} week {week}, Big Data Bowl data"
        assert d["play"]["game_id"].startswith(f"{season}_{week:02d}_")
        assert d["play"]["game_label"].endswith(f"{season} week {week}")
        assert bdb.example_label(d).startswith(f"{season} week {week} ")
    assert seen > 5


def test_a_play_has_one_qb_the_passer_even_when_a_second_qb_is_on_the_field(paths, raw, cand, supp):
    """Taysom Hill is positioned QB on 163 plays where he isn't the passer."""
    hill = []
    for week in range(1, 19):
        inp = pl.read_csv(
            bdb.raw_dir(paths) / bdb.INPUT.format(season=bdb.SEASON, week=week),
            columns=["game_id", "play_id", "player_position", "player_role"],
        )
        q = inp.filter((pl.col("player_position") == "QB") & (pl.col("player_role") != "Passer"))
        hill += q.select("game_id", "play_id").unique().head(3).iter_rows()
        if len(hill) >= 6:
            break
    assert hill
    wk = {}
    for gid, pid in hill:
        week = int(supp[(gid, pid)]["week"])
        wk.setdefault(week, []).append((gid, pid))
    for week, keys in wk.items():
        k = pl.DataFrame(keys, schema=["game_id", "play_id"], orient="row")
        inp, out = bdb.read_frames(paths, week, k)
        for gid, pid in keys:
            sel = (pl.col("game_id") == gid) & (pl.col("play_id") == pid)
            d = bdb.real_diagram(supp[(gid, pid)], inp.filter(sel), out.filter(sel), "cover_3")
            if d is not None:
                assert sum(p["role"] == "qb" for p in d["players"]) == 1, (gid, pid)
