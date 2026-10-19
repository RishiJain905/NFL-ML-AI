"""Regression tests for blank ids (AE02 smoke build): rosters_weekly had a row whose gsis_id was
whitespace, not null; it became an empty `:START_ID` in rels_PLAYED_FOR.csv and the bulk import
failed ("start:null ... is missing START_ID field", exit 70). A blank id must never become a
node, a relationship or an empty key field in any CSV; a blank or repeated node key must stop
the export with a clear error before any CSV is written."""

from __future__ import annotations

import csv
from pathlib import Path

import polars as pl
import pytest
from history_world import (
    DB_LV,
    DE_KC,
    GA,
    LB_LV,
    QB_KC,
    RB_KC,
    RB_LV,
    TE_KC,
    install_world,
    load,
)

from nflengine.history_graph import export as X
from nflengine.history_graph import schema as S


def edit_play(w, game: str, pid: int, **changes):
    for r in w.plays[GAME_YEAR[game]]:
        if r["game_id"] == game and r["play_id"] == pid:
            r.update(changes)
            return
    raise AssertionError("no such play")


GAME_YEAR = {GA: 2024}


def roster(team, pid, season=2024, week=1, name="Ghost"):
    return {
        "season": season,
        "week": week,
        "team": team,
        "gsis_id": pid,
        "status": "ACT",
        "position": "WR",
        "full_name": name,
    }


def read_csv(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


@pytest.fixture
def messy(tmp_path, monkeypatch):
    """The league with blank ids planted everywhere an id is read."""
    w = install_world(tmp_path, monkeypatch)
    # rosters_weekly: whitespace, empty, a tab, null and a padded real id
    w.rosters += [
        roster("KC", " ", name="Ghost One"),
        roster("KC", "", name="Ghost Two"),
        roster("LV", "\t", name="Ghost Three"),
        roster("KC", None, name="Ghost Four"),
        roster("KC", f" {RB_KC} "),  # the same man as RB_KC, padded: one link, not two
    ]
    # player_games: a blank player id
    w.player_games.append(
        {"player_id": " ", "game_id": GA, "team": "KC", "season": 2024, "week": 1}
    )
    # plays: blank role ids, and one padded real id
    edit_play(w, GA, 4, receiver_player_id="   ")  # the target is nobody
    edit_play(w, GA, 6, rusher_player_id=" ")  # the carrier is nobody
    edit_play(w, GA, 3, assist_tackle_3_player_id="\t", half_sack_2_player_id=" ")
    edit_play(w, GA, 8, sack_player_id=f" {DE_KC} ")  # padded: still the sacker
    # participation: a blank id inside a list, a trailing separator, a padded id, a blank position
    part = w.part["2026-10-03"][2024]
    for r in part:
        if r["play_id"] == 5:
            r["offense_players"] = f"{QB_KC}; ;{TE_KC}"
            r["offense_positions"] = "QB; ;TE"
            r["defense_players"] = f" {DB_LV} ;"
            r["defense_positions"] = "CB;"
        if r["play_id"] == 6:  # the RB's position is blank: the roster week stands in
            r["offense_positions"] = "QB; "
    w.write()
    return w


class TestBlankIds:
    def test_no_blank_node_and_no_blank_relationship_end(self, messy):
        fr = load(messy)
        for label, df in fr.nodes.items():
            k = df[S.key_of(label)]
            assert k.null_count() == 0, label
            if label != "Season":
                assert (k.str.strip_chars() == "").sum() == 0, label
        for rtype, df in fr.rels.items():
            for c in ("s", "e"):
                if df.schema[c] == pl.String:
                    assert df[c].null_count() == 0, (rtype, c)
                    assert (df[c].str.strip_chars() == "").sum() == 0, (rtype, c)
                    assert (df[c] == df[c].str.strip_chars()).all(), f"padded id in {rtype}.{c}"

    def test_whitespace_roster_ids_make_no_player_and_no_played_for(self, messy):
        fr = load(messy)
        ids = set(fr.nodes["Player"]["player_id"])
        assert not any(i.strip() == "" for i in ids)
        assert not set(fr.nodes["Player"]["name"].drop_nulls().to_list()) & {
            "Ghost One",
            "Ghost Two",
            "Ghost Three",
            "Ghost Four",
        }
        assert fr.rels["PLAYED_FOR"].height == 25  # the clean league's number
        # the padded roster row is RB_KC again: one link whose weeks are not doubled
        rb = fr.rels["PLAYED_FOR"].filter((pl.col("s") == RB_KC) & (pl.col("season") == 2024))
        assert rb.height == 1 and rb["weeks"][0].to_list() == [1, 2, 3]

    def test_a_blank_player_games_id_does_not_count(self, messy):
        fr = load(messy)
        assert fr.nodes["Player"].height == 17

    def test_blank_role_ids_make_no_role_but_padded_ones_still_do(self, messy):
        fr = load(messy)

        def ends(r):
            return {(s, e) for s, e in fr.rels[r].select("s", "e").iter_rows() if e.startswith(GA)}

        assert not any(e == f"{GA}:4" for _, e in ends("TARGETED"))  # blank target
        assert not any(e == f"{GA}:6" for _, e in ends("RUSHED"))  # blank carrier
        assert (DE_KC, f"{GA}:8") in ends("SACKED")  # " 00-0010 " is 00-0010
        tackles = {s for s, e in ends("TACKLED") if e == f"{GA}:3"}
        assert tackles == {LB_LV, "00-0006", DB_LV}  # the blank tackler and half-sacker add none
        assert len([1 for _, e in ends("SACKED") if e == f"{GA}:9"]) == 2

    def test_blank_participation_ids_and_positions(self, messy):
        fr = load(messy)
        of = fr.rels["ON_FIELD"].filter(pl.col("e") == f"{GA}:5")
        got = {r["s"]: (r["side"], r["position"]) for r in of.iter_rows(named=True)}
        assert got == {
            QB_KC: ("offense", "QB"),
            TE_KC: ("offense", "TE"),
            DB_LV: ("defense", "CB"),
        }
        # play 6: the RB's list position is blank, so his roster position that week is used
        rb = fr.rels["ON_FIELD"].filter((pl.col("e") == f"{GA}:6") & (pl.col("s") == RB_LV))
        assert rb["position"].to_list() == ["RB"]
        assert fr.rels["ON_FIELD"].height == 28  # no link lost or gained

    def test_blank_rows_are_not_counted_as_dropped_relationships(self, messy):
        assert load(messy).dropped == {
            "OFFENSE": 1,
            "OFF_COORD": 1,
            "participation_plays_not_in_pbp": 1,
        }

    def test_no_empty_key_field_in_any_csv(self, messy, tmp_path):
        fr = load(messy)
        out = tmp_path / "csv"
        written = X.write_csvs(fr, out)
        assert len(written) == len(S.NODES) + len(S.RELATIONSHIPS)
        for name, info in written.items():
            rows = read_csv(out / info["file"])
            header, body = rows[0], rows[1:]
            assert header == S.csv_header(name)
            keys = 2 if info["kind"] == "relationships" else 1
            if name in S.NODES and S.key_type(name) != S.STRING:
                keys = 2  # Season: the :ID and its typed copy
            for i, r in enumerate(body, start=2):
                assert len(r) == len(header), f"{info['file']} line {i}"
                for j in range(keys):
                    assert r[j].strip() != "", f"empty {header[j]} in {info['file']} line {i}"
                    assert r[j] == r[j].strip(), f"padded key in {info['file']} line {i}"

    def test_a_clean_league_is_unchanged_by_the_cleaning(self, tmp_path, monkeypatch):
        clean = load(install_world(tmp_path, monkeypatch))
        assert (
            clean.expected_counts()["Player"] == 17 and clean.expected_counts()["PLAYED_FOR"] == 25
        )


class TestCleanId:
    def test_strips_and_nulls_blanks(self):
        df = pl.DataFrame({"a": [" x ", "", "  ", "\t", None, "00-0001"]})
        out = df.with_columns(X.clean_id("a"))["a"].to_list()
        assert out == ["x", None, None, None, None, "00-0001"]

    def test_casts_to_text(self):
        df = pl.DataFrame({"a": [1, 2]})
        assert df.with_columns(X.clean_id("a"))["a"].to_list() == ["1", "2"]


class TestKeyChecks:
    def test_a_blank_team_code_stops_the_export(self, tmp_path, monkeypatch):
        w = install_world(tmp_path, monkeypatch)
        w.teams.append(
            {
                "team": " ",
                "team_name": "Nobody",
                "team_nick": "None",
                "team_conf": "AFC",
                "team_division": "AFC West",
            }
        )
        w.write()
        with pytest.raises(ValueError, match="Team keys are blank or repeated"):
            load(w)

    def test_a_null_team_code_stops_the_export(self, tmp_path, monkeypatch):
        w = install_world(tmp_path, monkeypatch)
        w.teams.append({"team": None, "team_name": "Nobody"})
        w.write()
        with pytest.raises(ValueError, match="Team keys are blank or repeated"):
            load(w)

    def test_a_repeated_node_key_stops_the_export(self, tmp_path, monkeypatch):
        w = install_world(tmp_path, monkeypatch)
        real = X._players_frame
        monkeypatch.setattr(X, "_players_frame", lambda inp, ids: pl.concat([real(inp, ids)] * 2))
        with pytest.raises(ValueError, match="Player keys are blank or repeated"):
            load(w)

    def test_a_repeated_play_key_stops_the_export(self, tmp_path, monkeypatch):
        w = install_world(tmp_path, monkeypatch)
        real = X._plays_frame
        monkeypatch.setattr(X, "_plays_frame", lambda inp: pl.concat([real(inp)] * 2))
        with pytest.raises(ValueError, match="Play keys are blank or repeated"):
            load(w)

    def test_the_error_names_the_label_and_never_a_value(self, tmp_path, monkeypatch):
        w = install_world(tmp_path, monkeypatch)
        w.teams.append({"team": " ", "team_name": "Sentinel Name"})
        w.write()
        with pytest.raises(ValueError) as e:
            load(w)
        assert "Sentinel" not in str(e.value)
