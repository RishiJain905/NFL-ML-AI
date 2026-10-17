"""PC03 "See a real one" on the real data root (read only: nothing is written under the data root).

The concept library built under the data root's `playcalling/bdb_library/` is read through the app:
every example of every concept answers 200 with the file's play, the answers fit the TypeScript
types (`RealClipResponse`), and a real 2025 reconstruction's "See a real one" button leads to a
clip. Skipped when the library isn't built on this computer. The data root comes from the
settings module (`ensure_data_root`); the tests never open the env file.

Run with `uv run pytest -m integration tests/app/test_app_playbrowser_clip_real.py`.
"""

from __future__ import annotations

import json
from collections import defaultdict

import playbrowser_fixtures as F  # noqa: F401  (puts tests/playcalling on the path)
import polars as pl
import pytest
import test_app_playbrowser as PB
import test_app_playcalling as T
from app_helpers import make_client

from nflengine.ops.summary import scrub
from nflengine.paths import DataRootError, ensure_data_root
from nflengine.playcalling import bdb
from nflengine.playcalling import diagrams as D

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    try:
        paths = ensure_data_root(create_dirs=False)
    except DataRootError:
        pytest.skip("the data root isn't available on this computer")
    lib = bdb.library_dir(paths)
    if not (lib / "index.json").is_file():
        pytest.skip("the concept library isn't built on this computer")
    index = json.loads((lib / "index.json").read_text("utf-8"))
    client = make_client(tmp_path_factory.mktemp("pc03realclip"), data_root=lambda: paths)
    return paths, client, index, lib


@pytest.fixture(scope="module")
def contract():
    return PB.Contract(T.pc01_source() + "\n" + PB.pc03_source())


def clip(client, concept, example=None):
    q = "" if example is None else f"?example={example}"
    r = client.get(f"/api/playcalling/concepts/{concept}{q}")
    assert r.status_code == 200, (concept, example, r.text[:300])
    return r.json()


def test_every_example_of_every_concept_opens_and_fits_the_types(real, contract):
    paths, client, index, lib = real
    seen: dict = defaultdict(set)
    problems: list[str] = []
    opened = 0
    for concept, entry in index["concepts"].items():
        n = len(entry["examples"])
        for i, e in enumerate(entry["examples"]):
            b = clip(client, concept, i)
            assert b["status"] == "ok" and b["index"] == i and len(b["examples"]) == n
            assert [x["label"] for x in b["examples"]] == [x["label"] for x in entry["examples"]]
            d = b["diagram"]
            on_disk = json.loads((lib / concept / e["file"]).read_text("utf-8"))
            for key in ("players", "ball", "marks", "phases", "field", "known", "label", "kind"):
                assert d[key] == on_disk[key], (concept, i, key)
            assert d["kind"] == "real" and d["label"].startswith("Real play: 20")
            assert d["label"] != D.LABEL
            problems += T.check_answer(contract, b, "RealClipResponse", f"{concept}#{i}", seen)
            opened += 1
    assert opened >= 15 and not problems, "\n".join(problems[:20])
    for field in contract.fields_of("RealClipResponse"):
        if field != "message":  # null whenever the clip is ok
            assert "value" in seen[("RealClipResponse", field)], field


def test_no_real_clip_answer_has_text_that_looks_like_a_secret_or_a_path(real):
    paths, client, index, lib = real
    for concept in index["concepts"]:
        for i in (0, 4, 9):
            text = client.get(f"/api/playcalling/concepts/{concept}?example={i}").text
            assert scrub(text, limit=10**9) == text, (concept, i)
            assert str(paths.root) not in text and paths.root.as_posix() not in text
            json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(AssertionError(c)))


def test_runs_and_rpos_have_no_real_example_on_the_real_library(real):
    _, client, index, _ = real
    for concept in ("rpo", "inside_zone", "outside_zone"):
        assert concept not in index["concepts"]
        b = clip(client, concept)
        assert b["status"] == "none" and b["diagram"] is None and b["examples"] == []


def test_a_real_2025_reconstructions_button_leads_to_a_clip(real):
    """A 2025 Cover-3 dropback has `real_example`; its count is what the clip lists; a 2025 run
    has none."""
    paths, client, index, lib = real
    plays = paths.playcalling / "2025" / "plays_enriched.parquet"
    if not plays.is_file():
        pytest.skip("no 2025 plays")
    df = pl.read_parquet(plays, memory_map=False)
    c3 = df.filter(
        (pl.col("coverage") == "cover_3")
        & pl.col("pass_attempt")
        & ~pl.col("screen").fill_null(False)
        & ~pl.col("rpo").fill_null(False)
        & ~(pl.col("play_action").fill_null(False) & pl.col("deep_shot").fill_null(False))
    ).head(1)
    row = c3.row(0, named=True)
    d = client.get(f"/api/playcalling/plays/{row['game_id']}/{int(row['play_id'])}/diagram").json()[
        "diagram"
    ]
    assert d["concept"] == "cover_3"
    n = len(index["concepts"]["cover_3"]["examples"])
    assert d["real_example"] == {
        "concept": "cover_3",
        "name": "Cover 3",
        "match": "a dropback against Cover 3",
        "count": n,
    }
    assert len(clip(client, "cover_3")["examples"]) == n
    run = (
        df.filter(pl.col("run_location").is_not_null() & ~pl.col("pass_attempt"))
        .head(1)
        .row(0, named=True)
    )
    rd = client.get(f"/api/playcalling/plays/{run['game_id']}/{int(run['play_id'])}/diagram").json()
    assert rd["diagram"]["real_example"] is None
