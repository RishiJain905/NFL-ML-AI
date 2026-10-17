"""PC03 on the real data root (read only: nothing is written, no network, no W&B).

The phase's exit criterion: "any 2026 play of any team can be browsed and played as a
reconstruction". These tests build a diagram for EVERY play in
the data root's `playcalling/2026/plays_enriched.parquet` and 2025's (and a sample of 2019's, the
pre-FTN era with participation only), check each against `check_diagram` (11 v 11, sorted keys,
in bounds, the label, the honesty rules), and then walk the app: one list per 2026 team and the
diagram of every play it lists.

Run with `uv run pytest -m integration tests/playcalling/test_playcall_diagrams_real.py -s`.
The data root comes from the settings module (`ensure_data_root`); the tests never open the
env file, and they skip without the data root or the tables.
"""

from __future__ import annotations

import functools
import sys
import time
from pathlib import Path

import polars as pl
import pytest
from playcall_diagram_fixtures import check_diagram, marks, players

# the app's test helpers live in tests/app (a sibling folder, not on the path by default)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from app_helpers import make_client  # noqa: E402

from nflengine.curate.teams import CANONICAL_TEAMS
from nflengine.paths import DataPaths, DataRootError, ensure_data_root
from nflengine.playcalling import diagrams as D

pytestmark = pytest.mark.integration


@functools.lru_cache(maxsize=1)
def data_paths() -> DataPaths | None:
    try:
        return ensure_data_root(create_dirs=False)
    except DataRootError:
        return None


def plays_path(season: int) -> Path:
    p = data_paths()
    if p is None:
        pytest.skip("the data root isn't available on this computer")
    return p.playcalling / str(season) / "plays_enriched.parquet"


@pytest.fixture(scope="module")
def paths() -> DataPaths:
    if not plays_path(2026).is_file():
        pytest.skip("the 2026 play-calling tables aren't on this computer")
    return data_paths()


def load(season: int) -> pl.DataFrame:
    if not plays_path(season).is_file():
        pytest.skip(f"no enriched plays for {season}")
    return pl.read_parquet(plays_path(season), memory_map=False)


def qb_index(df: pl.DataFrame) -> dict[tuple[str, str], list[str]]:
    """The passers of each (game, offense), the way the reader finds a QB run."""
    out: dict[tuple[str, str], set[str]] = {}
    for g, o, q in df.select("game_id", "offense", "passer_id").iter_rows():
        if q:
            out.setdefault((g, o), set()).add(q)
    return {k: sorted(v) for k, v in out.items()}


def sweep(df: pl.DataFrame, label: str) -> dict:
    """Build every row's diagram; return the failures and the counts."""
    qbs = qb_index(df)
    failures: list[str] = []
    seen = {"pass": 0, "run": 0, "sack": 0, "scramble": 0, "aborted": 0, "dropback": 0}
    unthrown = uncarried = 0
    t0 = time.perf_counter()
    n = 0
    for row in df.iter_rows(named=True):
        n += 1
        who = f"{row['game_id']}#{row['play_id']:.0f}"
        try:
            d = D.diagram(row, qbs.get((row["game_id"], row["offense"]), ()))
            check_diagram(d, row)
        except Exception as e:  # noqa: BLE001 - report which play
            failures.append(f"{who}: {type(e).__name__}: {str(e)[:200]}")
            continue
        kind = d["play"]["type"]
        seen[kind] += 1
        # a located pass is thrown somewhere; a designed run has a carrier
        if kind == "pass" and row["air_yards"] is not None and row["pass_location"] is not None:
            unthrown += not marks(d, "throw")
        if kind == "run":
            uncarried += sum(p["carrier"] for p in d["players"]) != 1
    secs = time.perf_counter() - t0
    print(f"\n[{label}] {n} plays in {secs:.1f} s ({1000 * secs / n:.2f} ms each) {seen}")
    return {
        "failures": failures,
        "seen": seen,
        "unthrown": unthrown,
        "uncarried": uncarried,
        "n": n,
    }


def report(res: dict) -> str:
    shown = "\n".join(res["failures"][:8])
    return f"{len(res['failures'])} of {res['n']} plays failed:\n{shown}"


@pytest.mark.parametrize("season", [2026, 2025])
def test_every_play_of_the_season_builds_a_valid_reconstruction(paths, season):
    df = load(season)
    res = sweep(df, str(season))
    assert res["n"] == df.height and not res["failures"], report(res)
    assert res["unthrown"] == 0 and res["uncarried"] == 0
    assert res["seen"]["pass"] > 1000 and res["seen"]["run"] > 1000  # real mix
    assert res["seen"]["sack"] > 20 and res["seen"]["scramble"] > 5


def test_a_sample_of_2019_the_pre_ftn_era_builds_too(paths):
    df = load(2019)
    sample = pl.concat(
        [
            df.gather_every(7),  # a spread over the season
            df.filter(pl.col("sack") | pl.col("scramble") | pl.col("aborted")).head(300),
            df.filter(pl.col("touchdown") | pl.col("interception")).head(300),
        ]
    ).unique(subset=["game_id", "play_id"])
    res = sweep(sample, "2019 sample")
    assert not res["failures"], report(res)
    assert res["unthrown"] == 0 and res["uncarried"] == 0


def test_every_play_says_which_sources_it_used_and_the_label(paths):
    df = load(2025).gather_every(11)
    for row in df.iter_rows(named=True):
        d = D.diagram(row)
        assert d["label"] == D.LABEL
        assert ("FTN charting (via nflverse)" in d["sources"]) == bool(row["ftn_charted"])
        assert ("nflverse participation (research data)" in d["sources"]) == bool(
            row["part_charted"]
        )
        if not row["ftn_charted"]:
            assert d["field"]["hash"] is None


def test_no_2026_play_claims_personnel_or_coverage(paths):
    df = load(2026).gather_every(13)
    for row in df.iter_rows(named=True):
        d = D.diagram(row)
        assert not any(k.startswith("Coverage") or "personnel:" in k for k in d["known"])
        assert any("February 2027" in u for u in d["unknown"])


def test_the_defense_never_gets_a_touchdown_the_offense_did_not_score(paths):
    """nflverse flags a pick-six or a fumble return `touchdown`; the diagram must not."""
    df = load(2025).filter(
        pl.col("touchdown") & (pl.col("interception") | pl.col("desc").str.contains("FUMBLE"))
    )
    assert df.height > 20
    for row in df.iter_rows(named=True):
        r = D.result_of(row)
        if row["interception"]:
            assert r["touchdown"] is False, row["desc"]
        d = D.diagram(row)
        if r["touchdown"] is False:
            assert all(k[1] < 100.5 for p in players(d, "qb", "offense") for k in p["track"][:-1])


# ---- through the app -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client(paths, tmp_path_factory):
    return make_client(tmp_path_factory.mktemp("pc03real"), data_root=lambda: paths)


def test_every_2026_team_lists_its_plays_and_every_listed_play_answers_a_diagram(paths, client):
    df = load(2026)
    total_listed = 0
    t0 = time.perf_counter()
    for team in sorted(CANONICAL_TEAMS):
        r = client.get(f"/api/playcalling/teams/{team}/plays?season=2026")
        assert r.status_code == 200, (team, r.text[:200])
        body = r.json()
        assert body["status"] == "ok", (team, body["message"])
        assert body["label"] == D.LABEL and body["season"] == 2026
        weeks = [w["week"] for w in body["weeks"]]
        assert weeks == sorted(weeks) and body["week"] == weeks[-1], team  # the newest week
        want = df.filter((pl.col("offense") == team) & (pl.col("week") == body["week"])).height
        assert body["counts"] == {"total": want, "shown": want} and len(body["plays"]) == want
        assert body["game"]["plays"] == want
        for play in body["plays"]:
            assert play["offense"] == team and play["game_id"] == body["game"]["game_id"]
            dr = client.get(f"/api/playcalling/plays/{play['game_id']}/{play['play_id']}/diagram")
            assert dr.status_code == 200, (team, play["game_id"], play["play_id"], dr.text[:200])
            d = dr.json()["diagram"]
            assert d["label"] == D.LABEL and d["play"]["play_id"] == play["play_id"]
            assert len(players(d, side="offense")) == 11 and len(players(d, side="defense")) == 11
            assert d["play"]["type"] == play["type"]
            assert d["play"]["result"] == play["result"]
            total_listed += 1
    print(f"\n[app] 32 team lists and {total_listed} diagrams in {time.perf_counter() - t0:.1f} s")
    assert total_listed > 1500


def test_every_week_of_every_2026_team_lists_and_the_plays_add_up(paths, client):
    df = load(2026)
    per_game = {
        (r["game_id"], r["offense"]): r["n"]
        for r in df.group_by("game_id", "offense").agg(pl.len().alias("n")).iter_rows(named=True)
    }
    seen = 0
    for team in sorted(CANONICAL_TEAMS):
        body = client.get(f"/api/playcalling/teams/{team}/plays?season=2026").json()
        for w in body["weeks"]:
            one = client.get(f"/api/playcalling/teams/{team}/plays?season=2026&week={w['week']}")
            b = one.json()
            assert b["status"] == "ok" and b["week"] == w["week"]
            assert b["counts"]["total"] == per_game[(w["game_id"], team)] == w["plays"]
            ids = [p["play_id"] for p in b["plays"]]
            assert len(ids) == len(set(ids))
            seen += 1
    assert seen >= 32 * 4
    # the defense side lists what was run against the team
    kc = client.get("/api/playcalling/teams/KC/plays?season=2026&side=defense").json()
    assert kc["status"] == "ok" and kc["side"] == "defense"
    assert all(p["defense"] == "KC" for p in kc["plays"])


def test_the_2025_season_and_the_filters_work_on_the_real_tables(paths, client):
    r = client.get("/api/playcalling/teams/KC/plays?season=2025&week=5&type=pass&down=3")
    b = r.json()
    assert r.status_code == 200 and b["status"] == "ok" and b["week"] == 5
    assert b["counts"]["shown"] == len(b["plays"]) <= b["counts"]["total"]
    assert all(
        p["down"] == 3 and p["type"] in ("pass", "sack", "scramble", "dropback") for p in b["plays"]
    )
    d = client.get(
        f"/api/playcalling/plays/{b['plays'][0]['game_id']}/{b['plays'][0]['play_id']}/diagram"
    ).json()["diagram"]
    assert d["play"]["season"] == 2025
    assert "nflverse participation (research data)" in d["sources"]
