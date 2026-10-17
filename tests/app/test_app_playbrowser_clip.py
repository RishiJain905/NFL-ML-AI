"""PC03: "See a real one", backend: `GET /api/playcalling/concepts/{concept}?example=N`
(`playbrowser.get_real_clip`): a real Big Data Bowl play of a concept from the library on this
computer (`playcalling/bdb_library/`), in the same diagram shape as a reconstruction.

The library is built by `bdb.build_library` on a SYNTHETIC raw folder (`playcall_bdb_fixtures`:
made-up BDB 2026 plays and tracking), so the files the route reads are the files the build
writes. The route takes a concept (a key of `diagrams.CONCEPTS`) and an example number; the file
names come only from the library's own index and only when they match `<gameId>_<playId>.json`.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import playbrowser_fixtures as F  # noqa: F401  (puts tests/playcalling on the path)
import playcall_bdb_fixtures as BF
import pytest
import test_app_playbrowser as PB
import test_app_playcalling as T
from app_helpers import make_client, make_paths
from fastapi.testclient import TestClient

import nflengine.app.readers.playbrowser as R
from nflengine.ops.summary import scrub
from nflengine.playcalling import bdb
from nflengine.playcalling import diagrams as D

NO_CONCEPT = "No such concept in the Big Data Bowl library."
FAIL_422 = T.FAIL_422
LIBRARY_CONCEPTS = ["screen", "play_action_deep", "cover_1", "cover_3", "2_man"]
NOT_IN_LIBRARY = ["rpo", "inside_zone", "outside_zone"]


def clip_url(concept: str, example: int | str | None = None) -> str:
    return f"/api/playcalling/concepts/{concept}" + (
        f"?example={example}" if example is not None else ""
    )


def get(client: TestClient, url: str, status: int = 200) -> dict[str, Any]:
    r = client.get(url)
    assert r.status_code == status, (url, r.status_code, r.text[:300])
    return r.json()


def lib_world(tmp_path: Path, per_concept: int = 3):
    """A data root with a built library; returns (paths, client, index)."""
    paths = make_paths(tmp_path)
    BF.standard_world(bdb.raw_dir(paths)).write()
    index = bdb.build_library(paths, per_concept=per_concept, log=lambda s: None)
    return paths, make_client(tmp_path), index


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return lib_world(tmp_path_factory.mktemp("pc03clip"))


def lib_file(paths, concept: str, name: str) -> Path:
    return bdb.library_dir(paths) / concept / name


def set_index(paths, index: dict[str, Any]) -> None:
    (bdb.library_dir(paths) / "index.json").write_text(json.dumps(index), encoding="utf-8")


# ---- the answers ---------------------------------------------------------------------------------


def test_a_clip_is_the_library_file_with_the_concepts_header_and_its_example_list(world):
    paths, client, index = world
    b = get(client, clip_url("cover_3"))
    assert b["status"] == "ok" and b["message"] is None
    assert (b["concept"], b["name"], b["match"]) == (
        "cover_3", "Cover 3", "a dropback against Cover 3",
    )  # fmt: skip
    assert b["note"] == bdb.NOTE
    want = index["concepts"]["cover_3"]["examples"]
    assert b["examples"] == [{"index": i, "label": e["label"]} for i, e in enumerate(want)]
    assert len(want) == 3 and b["index"] == 0
    d = b["diagram"]
    on_disk = json.loads(lib_file(paths, "cover_3", want[0]["file"]).read_text("utf-8"))
    for key in (
        "players",
        "ball",
        "marks",
        "phases",
        "field",
        "known",
        "label",
        "kind",
        "duration",
    ):
        assert d[key] == on_disk[key], key
    assert d["kind"] == "real" and d["label"].startswith("Real play: 2023 week")
    assert d["concept"] == "cover_3" and d["real_example"] is None


@pytest.mark.parametrize("example", [0, 1, 2])
def test_each_example_number_opens_that_example(world, example):
    paths, client, index = world
    files = [e["file"] for e in index["concepts"]["cover_3"]["examples"]]
    b = get(client, clip_url("cover_3", example))
    assert b["index"] == example and len(b["examples"]) == 3
    gid, pid = files[example].removesuffix(".json").split("_")
    assert b["diagram"]["play"]["play_id"] == int(pid)
    assert b["diagram"]["play"]["game_id"].startswith("2023_")


@pytest.mark.parametrize(("asked", "shown"), [(3, 2), (50, 2), (99, 2), (0, 0)])
def test_an_example_past_the_end_is_clamped_to_the_last(world, asked, shown):
    _, client, _ = world
    b = get(client, clip_url("cover_3", asked))
    assert b["status"] == "ok" and b["index"] == shown


def test_a_concept_with_one_example_clamps_everything_to_it(world):
    _, client, _ = world
    for example in (0, 1, 99):
        assert get(client, clip_url("screen", example))["index"] == 0


def test_the_reader_clamps_a_negative_example_to_the_first(world):
    """The route refuses a negative number; the reader still clamps if it is called with one."""
    paths, _, _ = world
    for example in (-1, -50, 0):
        assert R.get_real_clip(paths, "cover_3", example)["index"] == 0
    assert R.get_real_clip(paths, "cover_3", 10**6)["index"] == 2


@pytest.mark.parametrize("concept", LIBRARY_CONCEPTS)
def test_every_library_concept_answers_ok_with_a_real_diagram(world, concept):
    _, client, index = world
    b = get(client, clip_url(concept))
    assert b["status"] == "ok" and b["diagram"]["kind"] == "real"
    assert b["name"] == D.CONCEPTS[concept]["name"] and b["match"] == D.CONCEPTS[concept]["match"]
    assert b["diagram"]["concept"] == concept
    assert len(b["examples"]) == len(index["concepts"][concept]["examples"])
    assert len(b["diagram"]["players"]) == 12 and b["diagram"]["label"] != D.LABEL


@pytest.mark.parametrize("concept", NOT_IN_LIBRARY)
def test_runs_and_rpos_have_no_real_example(world, concept):
    _, client, _ = world
    b = get(client, clip_url(concept))
    assert b["status"] == "none" and b["message"] == "No real example of this concept yet."
    assert b["examples"] == [] and b["index"] is None and b["diagram"] is None
    assert b["name"] == D.CONCEPTS[concept]["name"]


@pytest.mark.parametrize("concept", sorted(D.CONCEPTS))
def test_without_a_library_every_concept_says_how_to_build_it(tmp_path, concept):
    make_paths(tmp_path)
    b = get(make_client(tmp_path), clip_url(concept))
    assert b["status"] == "no_library" and R.LIBRARY_CMD in b["message"]
    assert b["examples"] == [] and b["index"] is None and b["diagram"] is None
    assert b["note"] == R.NOTE and "2026" in R.NOTE and "non-commercial" in R.NOTE
    assert (b["concept"], b["name"]) == (concept, D.CONCEPTS[concept]["name"])


@pytest.mark.parametrize(
    "index", ['{"concepts": []}', "[]", "not json", '{"nope": 1}', '{"concepts": null}']
)
def test_a_broken_index_is_no_library(tmp_path, index):
    paths = make_paths(tmp_path)
    d = bdb.library_dir(paths)
    d.mkdir(parents=True)
    (d / "index.json").write_text(index, encoding="utf-8")
    assert get(make_client(tmp_path), clip_url("screen"))["status"] == "no_library"


# ---- the concept and the example number ----------------------------------------------------------


@pytest.mark.parametrize("concept", ["kickoff", "nope", "screen2", "ab", "a_b_c", "play_action"])
def test_a_well_formed_concept_the_library_does_not_know_is_a_fixed_404_that_does_not_echo_it(
    world, concept
):
    assert concept not in D.CONCEPTS
    r = world[1].get(clip_url(concept))
    assert r.status_code == 404
    assert r.json()["error"] == {"code": "not_found", "message": NO_CONCEPT}
    assert concept not in r.text


@pytest.mark.parametrize(
    "concept",
    ["SCREEN", "Screen", "a", "x" * 31, "inside-zone", "screen.json", "screen%20", "sc ree"],
)
def test_a_malformed_concept_is_a_422_with_fixed_text(world, concept):
    r = world[1].get(clip_url(concept))
    assert r.status_code == 422, concept
    assert r.json()["error"] == {"code": "bad_request", "message": FAIL_422}
    if len(concept) > 3:  # a short one is inside the JSON envelope
        assert concept not in r.text.replace(FAIL_422, "")


@pytest.mark.parametrize(
    "concept", ["..", "../x", "screen/../../x", "%2e%2e%2f", "screen%2f..%2f..%2findex"]
)
def test_path_tricks_in_the_concept_never_reach_a_file(world, concept):
    r = world[1].get(clip_url(concept))
    assert r.status_code in (404, 422), concept
    assert "SECRET" not in r.text and "built_at" not in r.text and "competition" not in r.text


@pytest.mark.parametrize("example", ["-1", "100", "x", "1.5", "", "1e1", "99999999999"])
def test_a_bad_example_number_is_a_422(world, example):
    r = world[1].get(clip_url("screen", example))
    assert r.status_code == 422, example
    assert r.json()["error"]["message"] == FAIL_422
    if example:
        assert example not in r.text.replace(FAIL_422, "")


# ---- file names: the index decides, and only a <gameId>_<playId>.json inside the concept's folder

BAD_NAMES = [
    "../../secret.json",
    "..\\secret.json",
    "../screen/2023090700_101.json",
    "/abs/2023090700_101.json",
    "C:\\x\\2023090700_101.json",
    "2023090700_101.json/../../secret.json",
    "2023090700_101.JSON",
    "2023090700_101.json.bak",
    "20230907_101.json",  # an eight-digit game id
    "2023090700_1234567.json",  # a seven-digit play id
    "2023090700_.json",
    "index.json",
    "",
    " 2023090700_101.json",
    "2023090700_101",
    "screen/2023090700_101.json",
]
MARK = "SECRET-MARKER"


def marked_clip(paths, play_id: int = 101) -> dict[str, Any]:
    """A valid-looking real clip that says SECRET-MARKER: planted where it must not be read."""
    return {
        "kind": "real",
        "play": {"play_id": play_id, "desc": MARK, "offense": "KC", "defense": "DET"},
        "label": MARK,
    }


def test_only_names_of_the_right_shape_are_listed_and_never_read_outside_the_concepts_folder(
    tmp_path, monkeypatch
):
    paths, client, index = lib_world(tmp_path)
    lib = bdb.library_dir(paths)
    good = index["concepts"]["screen"]["examples"][0]
    # plant marked clips everywhere a hostile name could point
    for target in (
        lib / "secret.json",
        paths.playcalling / "secret.json",
        paths.root / "secret.json",
        lib / "screen" / "secret.json",
        lib / "cover_3" / "2023090700_101.json.bak",
        lib / "index.json.bak",
    ):
        target.write_text(json.dumps(marked_clip(paths)), encoding="utf-8")
    # the same valid name in ANOTHER concept's folder (cover_3 has 2023090700_101.json already)
    (lib / "cover_1" / "2023090700_999.json").write_text(
        json.dumps(marked_clip(paths, 999)), "utf-8"
    )
    index["concepts"]["screen"]["examples"] = [
        *({"file": n, "label": f"bad {k}"} for k, n in enumerate(BAD_NAMES)),
        {"file": None, "label": "no file"},
        {"label": "no file key"},
        "not a dict",
        {"file": 7, "label": "int"},
        good,
    ]
    set_index(paths, index)
    read: list[Path] = []
    real_read_json = R.read_json

    def spy(path):
        read.append(Path(path))
        return real_read_json(path)

    monkeypatch.setattr(R, "read_json", spy)
    b = get(client, clip_url("screen"))
    b99 = get(client, clip_url("screen", 99))
    assert [e["label"] for e in b["examples"]] == [good["label"]]  # only the valid one
    assert b["status"] == "ok" and b["index"] == 0 and b99["index"] == 0
    assert MARK not in json.dumps(b) + json.dumps(b99)
    folders = {p.parent for p in read if p.name != "index.json"}
    assert folders == {lib / "screen"}, read  # nothing outside the concept's own folder
    assert all(".." not in p.parts and str(p).count("..") == 0 for p in read)
    assert (lib / "index.json") in read


def test_a_valid_name_whose_file_is_in_another_concepts_folder_is_not_found(tmp_path):
    paths, client, index = lib_world(tmp_path)
    lib = bdb.library_dir(paths)
    (lib / "2_man" / "2023091000_103.json").write_text(json.dumps(marked_clip(paths)), "utf-8")
    index["concepts"]["screen"]["examples"] = [{"file": "2023091000_103.json", "label": "x"}]
    set_index(paths, index)  # screen/ has no such file; 2_man/ does
    b = get(client, clip_url("screen"))
    assert b["status"] == "none" and b["message"] == "That example can't be read."
    assert (
        b["examples"] == [{"index": 0, "label": "x"}]
        and b["diagram"] is None
        and b["index"] is None
    )
    assert MARK not in json.dumps(b)


@pytest.mark.parametrize(
    "content",
    [None, "not json", "[]", "7", '{"kind": "real"}', '{"kind": "reconstruction", "play": {}}',
     '{"kind": "real", "play": []}', '{"kind": "real", "play": null}', '{"play": {"desc": "x"}}'],
)  # fmt: skip
def test_an_example_that_cannot_be_read_says_so_and_the_list_stays(tmp_path, content):
    paths, client, index = lib_world(tmp_path)
    name = index["concepts"]["screen"]["examples"][0]["file"]
    target = lib_file(paths, "screen", name)
    if content is None:
        target.unlink()
    else:
        target.write_text(content, encoding="utf-8")
    b = get(client, clip_url("screen"))
    assert b["status"] == "none" and b["message"] == "That example can't be read."
    assert len(b["examples"]) == 1 and b["diagram"] is None and b["index"] is None


@pytest.mark.parametrize(
    "entry",
    [
        None,
        "x",
        5,
        [],
        {"examples": None},
        {"examples": "oops"},
        {"examples": []},
        {"examples": [1, 2]},
    ],
)
def test_a_concept_entry_with_no_usable_examples_is_none_not_an_error(tmp_path, entry):
    paths, client, index = lib_world(tmp_path)
    index["concepts"]["screen"] = entry
    set_index(paths, index)
    b = get(client, clip_url("screen"))
    assert b["status"] == "none" and b["examples"] == [] and b["diagram"] is None


def test_labels_are_cleaned_capped_and_defaulted(tmp_path):
    paths, client, index = lib_world(tmp_path)
    ex = index["concepts"]["cover_3"]["examples"]
    ex[0]["label"] = f"{tmp_path.as_posix()}/x api_key=sk-test123 tail"
    ex[1]["label"] = "y" * 400
    ex[2].pop("label")
    set_index(paths, index)
    b = get(client, clip_url("cover_3"))
    labels = [e["label"] for e in b["examples"]]
    assert (
        "sk-test123" not in labels[0]
        and str(tmp_path) not in labels[0]
        and "api_key=***" in labels[0]
    )
    assert len(labels[1]) == 160 and labels[1].endswith("\u2026")
    assert labels[2] == "Example 3"


# ---- text that reaches the browser ---------------------------------------------------------------


def test_the_clips_text_is_cleaned_and_its_team_codes_are_the_projects(tmp_path):
    paths, client, index = lib_world(tmp_path)
    name = index["concepts"]["screen"]["examples"][0]["file"]
    f = lib_file(paths, "screen", name)
    d = json.loads(f.read_text("utf-8"))
    d["play"]["desc"] = (
        f"pass for 8 yards. api_key=sk-test123 more Authorization: Bearer abcdef0123456789 tail "
        f"{tmp_path.as_posix()}/secret.csv {tmp_path}\\raw\\x.csv"
    )
    d["play"]["offense"], d["play"]["defense"] = "LAR", "ZZZ"
    f.write_text(json.dumps(d), encoding="utf-8")
    idx = json.loads((bdb.library_dir(paths) / "index.json").read_text("utf-8"))
    idx["note"] = f"note api_key=sk-note999 here {tmp_path.as_posix()}/n"
    set_index(paths, idx)
    r = client.get(clip_url("screen"))
    body = r.text
    for bad in ("sk-test123", "abcdef0123456789", "sk-note999", str(tmp_path), tmp_path.as_posix()):
        assert bad not in body, bad
    assert scrub(body, limit=10**9) == body
    play = r.json()["diagram"]["play"]
    assert (play["offense"], play["defense"]) == ("LA", None)
    assert "api_key=***" in play["desc"] and "api_key=***" in r.json()["note"]


def test_a_note_missing_from_the_index_falls_back_to_the_readers_terms_line(tmp_path):
    paths, client, index = lib_world(tmp_path)
    index.pop("note")
    set_index(paths, index)
    assert get(client, clip_url("screen"))["note"] == R.NOTE
    index["note"] = ""
    set_index(paths, index)
    assert get(client, clip_url("screen"))["note"] == R.NOTE


def test_no_clip_carries_a_nan_or_a_path(world):
    paths, client, index = world
    for concept in sorted(D.CONCEPTS):
        text = client.get(clip_url(concept)).text
        json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(AssertionError(c)))
        assert str(paths.root) not in text and paths.root.as_posix() not in text
        assert scrub(text, limit=10**9) == text, concept


# ---- safety --------------------------------------------------------------------------------------


def test_the_route_writes_nothing(tmp_path):
    paths, client, _ = lib_world(tmp_path)
    before = PB.file_tree(paths.root)
    for concept in sorted(D.CONCEPTS) + ["nope", "SCREEN"]:
        for q in (None, 0, 1, 99, 100):
            client.get(clip_url(concept, q))
    assert PB.file_tree(paths.root) == before


def test_a_rebuild_can_replace_the_library_while_the_app_is_open(tmp_path):
    paths, client, _ = lib_world(tmp_path)
    get(client, clip_url("cover_3"))
    bdb.build_library(paths, per_concept=2, log=lambda s: None)  # swaps the folder under the app
    b = get(client, clip_url("cover_3"))
    assert b["status"] == "ok" and len(b["examples"]) == 2


def test_a_missing_data_drive_is_the_standard_503(tmp_path):
    from nflengine.paths import DataRootError

    def gone():
        raise DataRootError("Data drive D:\\ is not connected (NFL_DATA_ROOT=D:/nfl-ml-data).")

    r = make_client(tmp_path, data_root=gone).get(clip_url("screen"))
    assert (r.status_code, r.json()["error"]["code"]) == (503, "data_root_missing")


# ---- the button and the clip agree ---------------------------------------------------------------


def test_the_diagrams_real_example_counts_what_the_clip_can_open(tmp_path):
    """`real_example.count` is the number of examples the clip lists (invalid names in the index
    count for neither), and a reconstruction with no usable example has no button."""
    paths, client, index = lib_world(tmp_path)
    plays = pl_rows(paths)
    index["concepts"]["cover_3"]["examples"].extend(
        [{"file": "../../x.json", "label": "bad"}, {"file": "nope", "label": "bad"}]
    )
    index["concepts"]["screen"]["examples"] = [{"file": "../../x.json", "label": "bad"}]
    set_index(paths, index)
    c3 = get(client, PB.diagram_url("2026_04_KC_DEN", plays["cover_3"]))["diagram"]
    assert c3["concept"] == "cover_3"
    assert c3["real_example"]["count"] == len(get(client, clip_url("cover_3"))["examples"]) == 3
    sc = get(client, PB.diagram_url("2026_04_KC_DEN", plays["screen"]))["diagram"]
    assert sc["concept"] == "screen" and sc["real_example"] is None
    assert get(client, clip_url("screen"))["status"] == "none"


def pl_rows(paths) -> dict[str, int]:
    """Two KC week-4 plays for the app tree: one Cover 3 pass, one screen."""
    rows = [
        F.simple(1, 1, coverage="cover_3", part_charted=True, personnel="11", extra_ol=False),
        F.simple(2, 1, screen=True),
    ]
    F.write_plays(paths, 2026, F.rows_in(F.G4, "KC", "DEN", False, rows))
    return {"cover_3": 1, "screen": 2}


# ---- the contract with web/src/api/types.ts ------------------------------------------------------


def test_every_clip_answer_matches_the_typescript_types(world, tmp_path):
    contract = PB.Contract(T.pc01_source() + "\n" + PB.pc03_source())
    seen: dict = defaultdict(set)
    problems: list[str] = []
    answers = []
    for concept in sorted(D.CONCEPTS):
        for q in (None, 1):
            answers.append((clip_url(concept, q), get(world[1], clip_url(concept, q))))
    empty = make_client(tmp_path / "empty")
    answers.append(("no library", get(empty, clip_url("screen"))))
    for where, body in answers:
        problems += T.check_answer(contract, body, "RealClipResponse", where, seen)
    assert not problems, "\n".join(problems[:30])
    for field in contract.fields_of("RealClipResponse"):
        assert "value" in seen[("RealClipResponse", field)], (
            f"RealClipResponse.{field} never non-null"
        )
    for field in ("index", "diagram"):
        assert "null" in seen[("RealClipResponse", field)], field
    assert "null" in seen[("RealClipResponse", "message")]
    # the diagram inside is a PlayDiagram of kind 'real'
    kinds = {b["diagram"]["kind"] for _, b in answers if b["diagram"]}
    assert kinds == {"real"}
    assert set(contract.fields_of("RealClipResponse")) == {
        "status", "message", "concept", "name", "match", "note", "examples", "index", "diagram",
    }  # fmt: skip


def test_the_contract_check_catches_a_drifting_clip(world):
    contract = PB.Contract(T.pc01_source() + "\n" + PB.pc03_source())
    body = get(world[1], clip_url("screen"))
    seen: dict = defaultdict(set)
    assert T.check_answer(contract, body, "RealClipResponse", "ok", seen) == []
    bad = json.loads(json.dumps(body))
    bad["status"] = "maybe"
    bad["surprise"] = 1
    del bad["note"]
    bad["examples"][0]["index"] = "0"
    bad["diagram"]["kind"] = "tracked"
    bad["diagram"]["players"][0]["track"][0] = [1.0]
    text = "\n".join(T.check_answer(contract, bad, "RealClipResponse", "bad", seen))
    for needle in (
        "'maybe'",
        "field 'surprise' is not in RealClipResponse",
        "RealClipResponse.note is missing",
        "'0' is not a number",
        "'tracked'",
        "track[0]: 1 items for a 3-tuple",
    ):
        assert needle in text, needle


def test_the_example_files_are_what_the_build_writes(world):
    """The route and the build agree on names: every listed example is a file the build wrote."""
    paths, client, index = world
    for concept, entry in index["concepts"].items():
        for e in entry["examples"]:
            assert re.fullmatch(r"\d{10}_\d{1,5}\.json", e["file"])
            assert lib_file(paths, concept, e["file"]).is_file()
        b = get(client, clip_url(concept))
        assert [x["label"] for x in b["examples"]] == [e["label"] for e in entry["examples"]]


def test_a_trailing_newline_is_not_part_of_a_valid_example_name(tmp_path):
    """`$` matches before a final newline: `<gameId>_<playId>.json` + a newline is no name."""
    paths, client, index = lib_world(tmp_path)
    name = index["concepts"]["screen"]["examples"][0]["file"]
    index["concepts"]["screen"]["examples"] = [{"file": name + "\n", "label": "newline"}]
    set_index(paths, index)
    assert R._examples(index, "screen") == []
    assert get(client, clip_url("screen"))["status"] == "none"


@pytest.mark.parametrize("tail", ["%0A", "%0a", "%00", "%0D%0A"])
def test_control_characters_after_a_valid_game_id_or_concept_are_not_accepted(world, tail):
    r = world[1].get(f"/api/playcalling/plays/2026_04_KC_DEN{tail}/10/diagram")
    assert r.status_code in (404, 422), tail
    r = world[1].get(f"/api/playcalling/concepts/screen{tail}")
    assert r.status_code in (404, 422), tail


def test_every_text_of_a_library_file_is_cleaned_not_only_the_description(tmp_path):
    """Sol, PC03: the known lines, the phase and mark texts, the labels and the game label come
    from the Big Data Bowl's labels through our code; they are cleaned like every file text."""
    paths, client, index = lib_world(tmp_path)
    name = index["concepts"]["screen"]["examples"][0]["file"]
    f = lib_file(paths, "screen", name)
    d = json.loads(f.read_text("utf-8"))
    poison = {
        "sk-known999": ("known", 0, "api_key=sk-known999 x"),
        "tk-appr888": ("approximate", 0, "token=tk-appr888 y"),
        "un777": ("unknown", 0, f"{tmp_path.as_posix()}/u secret=un777 tail"),
        "src222": ("sources", 0, "token=src222 s"),
    }
    for _, (key, i, text) in poison.items():
        d[key][i] = text
    d["phases"][1]["text"] = "password=ph666 z"
    d["marks"][0]["text"] = "api_key=sk-mark555 w"
    d["play"]["game_label"] = "Authorization: Bearer glabel123456 t"
    d["play"]["result"]["text"] = "api_key=sk-res444 r"
    d["label"] = "secret=lbl333 q"
    before = json.loads(json.dumps(d["players"])), json.loads(json.dumps(d["ball"]))
    f.write_text(json.dumps(d), encoding="utf-8")
    r = client.get(clip_url("screen"))
    body = r.text
    secrets = ("sk-known999", "tk-appr888", "un777", "ph666", "sk-mark555", "glabel123456")
    for secret in (*secrets, "sk-res444", "lbl333", "src222", str(tmp_path), tmp_path.as_posix()):
        assert secret not in body, secret
    assert scrub(body, limit=10**9) == body
    got = r.json()["diagram"]
    assert got["known"][0] == "api_key=*** x" and got["phases"][1]["text"] == "password=*** z"
    assert got["label"] == "secret=*** q"
    # the numbers are untouched
    assert (got["players"], got["ball"]) == before
