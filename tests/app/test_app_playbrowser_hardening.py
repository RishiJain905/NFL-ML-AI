"""PC03: the lead's hardening after the test worker's last observations.

- `bdb.download` keeps a Kaggle name's file name only and refuses one that could point outside
  `raw/` (a backslash path, '.', '..', a drive letter);
- the clip reader cleans every string of a library file, not only the play's description.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import playbrowser_fixtures as F  # noqa: F401  (puts tests/playcalling on the path)
import pytest
from app_helpers import make_client, make_paths
from test_app_playbrowser_clip import clip_url, get, lib_world

from nflengine.ops.summary import scrub
from nflengine.playcalling import bdb


@pytest.mark.parametrize("name", ["a/..", "folder\\..", "C:evil.csv", "x/", "."])
def test_a_download_name_that_isnt_a_plain_file_is_refused(tmp_path: Path, name: str):
    paths = make_paths(tmp_path)
    calls: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, content=b"x")

    with pytest.raises(bdb.KaggleError, match="isn't a plain file"):
        bdb.download(
            paths,
            [name],
            auth=("", "testtoken"),
            transport=httpx.MockTransport(answer),
            log=lambda s: None,
        )
    # nothing outside raw/ and no download request for it
    assert not any("download" in c for c in calls)
    assert not (bdb.raw_dir(paths).parent / "evil.csv").exists()
    assert not (tmp_path / "evil.csv").exists()


@pytest.mark.parametrize(
    "name",
    ["folder\\train\\input_2023_w01.csv", "..\\..\\input_2023_w01.csv", "a/b/input_2023_w01.csv"],
)
def test_a_backslash_folder_keeps_the_file_name_inside_raw(tmp_path: Path, name: str):
    paths = make_paths(tmp_path)
    done = bdb.download(
        paths,
        [name],
        auth=("", "testtoken"),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"a,b\n1,2\n")),
        log=lambda s: None,
    )
    assert [d["name"] for d in done] == ["input_2023_w01.csv"]
    assert (bdb.raw_dir(paths) / "input_2023_w01.csv").read_bytes() == b"a,b\n1,2\n"


def test_every_string_of_a_clip_is_cleaned(tmp_path: Path):
    paths, client, index = lib_world(tmp_path, per_concept=1)
    concept = next(iter(index["concepts"]))
    name = index["concepts"][concept]["examples"][0]["file"]
    f = bdb.library_dir(paths) / concept / name
    d = json.loads(f.read_text(encoding="utf-8"))
    leak = f"see {paths.root}\\bdb\\x api_key=sk-test123 more"
    d["known"].append(leak)
    d["play"]["game_label"] = leak
    d["phases"][0]["text"] = leak
    f.write_text(json.dumps(d), encoding="utf-8")
    body = get(make_client(tmp_path), clip_url(concept))
    text = json.dumps(body)
    assert str(paths.root) not in text and "sk-test123" not in text
    assert scrub(text, limit=10**9) == text
