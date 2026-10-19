"""The bulk import's CSVs (AE02): `export.write_csvs` on the fixture league. Headers equal
`schema.csv_header`, ids and counts line up, blanks are empty fields, lists are `;`-joined,
and text with commas, quotes and non-ASCII characters round-trips through Python's csv module."""

from __future__ import annotations

import csv
import dataclasses
from pathlib import Path

import polars as pl
import pytest
from history_world import GA, QB_KC, install_world, load

from nflengine.history_graph import export as X
from nflengine.history_graph import schema as S


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    w = install_world(tmp_path_factory.mktemp("hg_csv_world"), mp)
    fr = load(w)
    out = tmp_path_factory.mktemp("hg_csv_out")
    written = X.write_csvs(fr, out)
    mp.undo()
    return fr, out, written


def read(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


def test_one_file_per_label_and_type_with_the_documented_names(built):
    fr, out, written = built
    assert set(written) == set(S.NODES) | set(S.RELATIONSHIPS)
    assert {p.name for p in out.glob("*.csv")} == {f["file"] for f in written.values()}
    for name, info in written.items():
        prefix = "nodes_" if name in S.NODES else "rels_"
        assert info["file"] == f"{prefix}{name}.csv"
        assert info["kind"] == ("nodes" if name in S.NODES else "relationships")


def test_every_header_equals_the_schemas(built):
    _, out, written = built
    for name, info in written.items():
        assert read(out / info["file"])[0] == S.csv_header(name), name


def test_row_counts_equal_the_frames_and_every_row_has_every_field(built):
    fr, out, written = built
    expected = fr.expected_counts()
    for name, info in written.items():
        rows = read(out / info["file"])
        assert info["rows"] == expected[name] == len(rows) - 1, name
        assert {len(r) for r in rows} == {len(rows[0])}, name


def test_the_season_file_writes_its_key_twice(built):
    _, out, _ = built
    assert read(out / "nodes_Season.csv") == [
        [":ID(Season)", "season:long"],
        ["2019", "2019"],
        ["2024", "2024"],
        ["2026", "2026"],
    ]


def test_in_season_ends_match_the_season_ids(built):
    _, out, _ = built
    ids = {r[0] for r in read(out / "nodes_Season.csv")[1:]}
    assert {r[1] for r in read(out / "rels_IN_SEASON.csv")[1:]} == ids


def test_ids_are_unique_and_relationship_ends_exist(built):
    _, out, written = built
    ids = {}
    for label in S.NODES:
        col = [r[0] for r in read(out / written[label]["file"])[1:]]
        assert len(col) == len(set(col)), label
        ids[label] = set(col)
    for rtype, spec in S.RELATIONSHIPS.items():
        rows = read(out / written[rtype]["file"])[1:]
        assert {r[0] for r in rows} <= ids[spec.start], rtype
        assert {r[1] for r in rows} <= ids[spec.end], rtype


def test_the_play_key_is_game_colon_play_id(built):
    _, out, _ = built
    keys = {r[0] for r in read(out / "nodes_Play.csv")[1:]}
    assert f"{GA}:3" in keys and f"{GA}:14" not in keys


def test_lists_are_semicolon_joined(built):
    _, out, _ = built
    rows = read(out / "rels_PLAYED_FOR.csv")
    h = rows[0]
    r = next(
        r
        for r in rows[1:]
        if r[0] == QB_KC and r[1] == "KC" and r[h.index("season:long")] == "2024"
    )
    assert r[h.index("weeks:long[]")] == "1;2;3"


def test_nulls_are_empty_fields_and_booleans_and_numbers_are_plain(built):
    _, out, _ = built
    rows = read(out / "nodes_Play.csv")
    h = rows[0]
    r = next(r for r in rows[1:] if r[0] == f"{GA}:3")
    assert r[h.index("success:boolean")] == "true"
    assert r[h.index("sack:boolean")] == "false"
    assert r[h.index("epa:double")] == "0.5"
    assert r[h.index("yards:long")] == "12"
    assert r[h.index("play_id:long")] == "3"
    assert r[h.index("ftn:boolean")] == "true"
    r2 = next(r for r in rows[1:] if r[0] == f"{GA}:7")  # a scramble: no FTN, no air yards
    assert r2[h.index("ftn:boolean")] == "" and r2[h.index("air_yards:double")] == ""
    # no scientific notation, no "NaN" / "null" text anywhere in a numeric column
    for r in rows[1:]:
        for c in ("epa:double", "wp:double", "order:double", "air_yards:double"):
            v = r[h.index(c)]
            assert v == "" or v.lstrip("-").replace(".", "", 1).isdigit(), (r[0], c, v)


def test_a_description_with_commas_quotes_and_semicolons_round_trips(built):
    fr, out, _ = built
    rows = read(out / "nodes_Play.csv")
    h = rows[0]
    r = next(r for r in rows[1:] if r[0] == f"{GA}:3")
    want = fr.nodes["Play"].filter(pl.col("play_key") == f"{GA}:3")["description"][0]
    assert r[h.index("description:string")] == want
    assert ", " in want and '"first down"' in want and ";" in want


def test_the_raw_file_quotes_the_field_and_doubles_its_quotes(built):
    _, out, _ = built
    text = (out / "nodes_Play.csv").read_text(encoding="utf-8")
    assert (
        '"P.Quarterback pass short left to W.Receiver for 12 yards, ""first down""; tackled' in text
    )


def test_files_are_utf8_with_unix_line_ends(built):
    _, out, written = built
    for info in written.values():
        raw = (out / info["file"]).read_bytes()
        raw.decode("utf-8")
        assert b"\r" not in raw, info["file"]
        assert not raw.startswith(b"\xef\xbb\xbf"), "a BOM would corrupt the first header"


def _frames_with(fr, label, **changes):
    nodes = dict(fr.nodes)
    nodes[label] = nodes[label].with_columns(**changes)
    return dataclasses.replace(fr, nodes=nodes)


def test_blank_strings_become_empty_fields(built, tmp_path):
    fr, _, _ = built
    spaced = _frames_with(fr, "Play", description=pl.lit("   "))
    X.write_csvs(spaced, tmp_path)
    rows = read(tmp_path / "nodes_Play.csv")
    i = rows[0].index("description:string")
    assert {r[i] for r in rows[1:]} == {""}


def test_non_ascii_text_round_trips(built, tmp_path):
    fr, _, _ = built
    name = "José Ñuñez – O'Brien, Jr."
    changed = _frames_with(
        fr,
        "Player",
        name=pl.when(pl.col("player_id") == QB_KC).then(pl.lit(name)).otherwise(pl.col("name")),
    )
    X.write_csvs(changed, tmp_path)
    rows = read(tmp_path / "nodes_Player.csv")
    r = next(r for r in rows[1:] if r[0] == QB_KC)
    assert r[rows[0].index("name:string")] == name


def test_old_csvs_are_removed_first_other_files_stay(built, tmp_path):
    fr, _, _ = built
    (tmp_path / "nodes_Old.csv").write_text("x", encoding="utf-8")
    (tmp_path / "rels_GONE.csv").write_text("x", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("keep", encoding="utf-8")
    X.write_csvs(fr, tmp_path)
    names = {p.name for p in tmp_path.iterdir()}
    assert "nodes_Old.csv" not in names and "rels_GONE.csv" not in names
    assert "notes.txt" in names and "nodes_Play.csv" in names


def test_the_folder_is_created(built, tmp_path):
    fr, _, _ = built
    out = tmp_path / "a" / "b"
    X.write_csvs(fr, out)
    assert (out / "nodes_Play.csv").exists()


def test_writing_twice_gives_the_same_bytes(built, tmp_path):
    fr, out, written = built
    X.write_csvs(fr, tmp_path)
    for name, info in written.items():
        a = (out / info["file"]).read_bytes()
        b = (tmp_path / info["file"]).read_bytes()
        if name in ("TACKLED", "PASSED", "RUSHED", "TARGETED", "CAUGHT", "SACKED", "INTERCEPTED"):
            # row order inside a role file is not part of the contract; the rows are
            assert sorted(a.splitlines()) == sorted(b.splitlines()), name
        else:
            assert a == b, name


def test_the_import_args_list_exactly_these_files(built, tmp_path):
    from nflengine.history_graph import docker as D
    from nflengine.history_graph.config import HistoryConfig

    _, out, written = built
    args = D.import_args(HistoryConfig(), "img", out, written)
    listed = [a for a in args if a.startswith(("--nodes=", "--relationships="))]
    assert len(listed) == len(written)
    for name, info in written.items():
        flag = "--nodes" if info["kind"] == "nodes" else "--relationships"
        assert f"{flag}={name}=/import/{info['file']}" in listed
