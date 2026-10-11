"""Participation parsing (PC00): both eras' strings, blanks, special teams, the loader."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from nflengine.paths import DataPaths
from nflengine.playcalling import labels
from nflengine.playcalling.participation import (
    OUTPUT_SCHEMA,
    load_participation,
    parse_participation,
)

NGS_11 = "1 RB, 1 TE, 3 WR"
FTN_11 = "1 C, 2 G, 1 QB, 1 RB, 2 T, 1 TE, 3 WR"
NGS_NICKEL = "4 DL, 2 LB, 5 DB"
FTN_NICKEL = "3 CB, 2 DE, 2 DT, 1 FS, 1 MLB, 1 OLB, 1 SS"


def _raw(rows: list[dict]) -> pl.DataFrame:
    base = {
        "nflverse_game_id": "2020_01_KC_HOU",
        "play_id": 1.0,
        "offense_formation": None,
        "offense_personnel": None,
        "defense_personnel": None,
        "defenders_in_box": None,
        "number_of_pass_rushers": None,
        "was_pressure": None,
        "time_to_throw": None,
        "route": None,
        "defense_man_zone_type": None,
        "defense_coverage_type": None,
    }
    schema = {
        "nflverse_game_id": pl.String,
        "play_id": pl.Float64,
        "offense_formation": pl.String,
        "offense_personnel": pl.String,
        "defense_personnel": pl.String,
        "defenders_in_box": pl.Int32,
        "number_of_pass_rushers": pl.Int32,
        "was_pressure": pl.Boolean,
        "time_to_throw": pl.Float64,
        "route": pl.String,
        "defense_man_zone_type": pl.String,
        "defense_coverage_type": pl.String,
    }
    filled = [{**base, "play_id": float(i + 1), **r} for i, r in enumerate(rows)]
    return pl.DataFrame(filled, schema=schema)


def _one(**row) -> dict:
    return parse_participation(_raw([row])).row(0, named=True)


# --- personnel ----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("string", "group", "extra_ol"),
    [
        (NGS_11, "11", False),
        ("2 RB, 1 TE, 2 WR", "21", False),
        ("1 RB, 2 TE, 2 WR", "12", False),
        ("0 RB, 1 TE, 4 WR", "01", False),
        ("1 RB, 0 TE, 4 WR", "10", False),
        ("2 QB, 1 RB, 1 TE, 2 WR", "11", False),  # 2-QB: QBs don't enter the group
        ("6 OL, 1 RB, 2 TE, 1 WR", "12", True),
        ("7 OL, 2 RB, 1 TE, 0 WR", "21", True),
        ("1 RB, 2 TE, 1 WR,1 DL", "12", False),  # no space after the comma; DL ignored
        (FTN_11, "11", False),
        ("1 C, 1 FB, 2 G, 1 QB, 1 RB, 2 T, 1 TE, 2 WR", "21", False),  # FB counts as a back
        ("1 C, 2 G, 1 QB, 2 T, 1 TE, 4 WR", "01", False),  # FTN: no RB written = 0
        ("1 C, 3 G, 1 QB, 1 RB, 2 T, 2 TE, 1 WR", "12", True),
        ("1 C, 2 G, 1 OL, 1 QB, 1 RB, 2 T, 1 TE, 2 WR", "11", True),
        ("2 QB, 1 C, 2 G, 1 RB, 2 T, 1 TE, 2 WR", "11", False),
        ("1 CB, 3 G, 1 QB, 1 RB, 2 T, 1 TE, 2 WR", "11", False),  # a CB at receiver
        ("  1 RB,  1 TE,  3 WR ", "11", False),  # extra whitespace
    ],
)
def test_personnel_group_and_extra_ol(string: str, group: str, extra_ol: bool) -> None:
    out = _one(offense_personnel=string)
    assert out["personnel"] == group
    assert out["extra_ol"] is extra_ol


@pytest.mark.parametrize(
    "string",
    [
        None,
        "",
        "   ",
        "1 RB, 1 TE, 3 XX",  # unknown position code
        "one RB, 1 TE",  # not "N POS"
        "6 OL, 0 RB, 2 TE, 0 WR,1 P,1 LS,1 K",  # NGS field goal
        "1 C, 3 G, 1 K, 1 LS, 1 P, 3 T, 1 TE",  # FTN field goal
        "1 RB, 1 TE, 1 WR,1 P,4 LB,1 LS,2 DB",  # NGS punt
        "1 FB, 1 FS, 2 ILB, 1 OLB, 2 RB, 1 SS, 2 TE, 1 WR",  # kick unit, no kicker listed
    ],
)
def test_personnel_null_when_blank_unparseable_or_special_teams(string: str | None) -> None:
    out = _one(offense_personnel=string, defense_personnel=NGS_NICKEL)
    assert out["personnel"] is None
    assert out["extra_ol"] is None


def test_special_teams_on_either_side_nulls_both_groups() -> None:
    out = _one(offense_personnel=FTN_11, defense_personnel="2 CB, 2 FS, 2 ILB, 2 OLB, 1 P, 1 SS")
    assert out["personnel"] is None and out["def_package"] is None


# --- defensive package --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("string", "package"),
    [
        ("4 DL, 3 LB, 4 DB", "base"),
        ("5 DL, 3 LB, 3 DB", "base"),
        (NGS_NICKEL, "nickel"),
        ("4 DL, 1 LB, 6 DB", "dime"),
        ("2 DL, 2 LB, 7 DB", "dime"),
        ("4 DL, 2 LB, 4 DB, 1 WR", "base"),  # a receiver on defense isn't counted
        ("2 CB, 2 DE, 2 DT, 1 FS, 2 ILB, 1 OLB, 1 SS", "base"),
        (FTN_NICKEL, "nickel"),
        ("4 CB, 2 DE, 1 DT, 1 FS, 1 ILB, 1 OLB, 1 SS", "dime"),
        ("2 CB, 1 DB, 2 DE, 1 DT, 1 FS, 2 ILB, 1 OLB, 1 SS", "nickel"),  # FTN "DB" counts
        ("2 CB, 3 DE, 1 FS, 1 ILB, 1 MLB, 1 OLB, 1 S, 1 SS", "nickel"),  # FTN "S" counts
        ("2 CB, 2 DE, 1 DT, 1 FS, 1 ILB, 1 OLB, 2 SAF, 1 SS", "dime"),
    ],
)
def test_def_package(string: str, package: str) -> None:
    assert _one(offense_personnel=NGS_11, defense_personnel=string)["def_package"] == package


@pytest.mark.parametrize("string", [None, "", "1 CB, 1 FS, 3 ILB, 1 K, 1 OLB, 1 RB, 1 SS, 1 WR"])
def test_def_package_null(string: str | None) -> None:
    assert _one(offense_personnel=NGS_11, defense_personnel=string)["def_package"] is None


# --- labels -------------------------------------------------------------------------------------
def test_labels_are_snake_and_blanks_null() -> None:
    raw = _raw(
        [
            {
                "offense_formation": "UNDER CENTER",
                "route": "HITCH/CURL",
                "defense_coverage_type": "2_MAN",
                "defense_man_zone_type": "MAN_COVERAGE",
            },
            {
                "offense_formation": "I_FORM",
                "route": "SHALLOW CROSS/DRAG",
                "defense_coverage_type": "COVER_1",
                "defense_man_zone_type": "ZONE_COVERAGE",
            },
            {
                "offense_formation": None,
                "route": "",
                "defense_coverage_type": None,
                "defense_man_zone_type": "",
            },
            {"route": "TEXAS/ANGLE", "defense_man_zone_type": "SOMETHING_ELSE"},
        ]
    )
    out = parse_participation(raw)
    assert out["formation"].to_list() == ["under_center", "i_form", None, None]
    assert out["target_route"].to_list() == [
        "hitch_curl",
        "shallow_cross_drag",
        None,
        "texas_angle",
    ]
    assert out["coverage"].to_list() == ["2_man", "cover_1", None, None]
    assert out["man_zone"].to_list() == ["man", "zone", None, None]


def test_label_vocabularies_match_labels_module() -> None:
    raw_routes = ["QUICK OUT", "HITCH/CURL", "IN/DIG", "SHALLOW CROSS/DRAG", "DEEP OUT",
                  "TEXAS/ANGLE", "GO", "FLAT", "ANGLE"]  # fmt: skip
    out = parse_participation(_raw([{"route": r} for r in raw_routes]))
    assert set(out["target_route"]) <= set(labels.ROUTES_FTN) | set(labels.ROUTES_NGS)
    forms = ["SHOTGUN", "UNDER CENTER", "PISTOL", "SINGLEBACK", "I_FORM", "EMPTY", "JUMBO",
             "WILDCAT"]  # fmt: skip
    out = parse_participation(_raw([{"offense_formation": f} for f in forms]))
    assert set(out["formation"]) == set(labels.FORMATIONS_FTN) | set(labels.FORMATIONS_NGS)
    covs = ["COVER_0", "COVER_1", "COVER_2", "2_MAN", "COVER_3", "COVER_4", "COVER_6", "COVER_9",
            "COMBO", "PREVENT", "BLOWN"]  # fmt: skip
    out = parse_participation(_raw([{"defense_coverage_type": c} for c in covs]))
    assert out["coverage"].to_list() == list(labels.COVERAGES)


# --- numbers ------------------------------------------------------------------------------------
def test_numbers_pass_through_and_impossible_values_null() -> None:
    raw = _raw(
        [
            {"defenders_in_box": 7, "number_of_pass_rushers": 4, "was_pressure": True,
             "time_to_throw": 2.6},
            {"defenders_in_box": 0, "number_of_pass_rushers": 0, "was_pressure": False},
            {"defenders_in_box": 11, "number_of_pass_rushers": 45},
            {},
        ]
    )  # fmt: skip
    out = parse_participation(raw)
    assert out["part_box"].to_list() == [7, None, 11, None]
    assert out["part_rushers"].to_list() == [4, None, None, None]
    assert out["pressure"].to_list() == [True, False, None, None]
    assert out["time_to_throw"].to_list() == [2.6, None, None, None]


# --- shape --------------------------------------------------------------------------------------
def test_schema_keys_and_duplicates() -> None:
    raw = _raw(
        [
            {"play_id": 5.0, "offense_personnel": NGS_11},
            {"play_id": 5.0, "offense_personnel": "2 RB, 1 TE, 2 WR"},  # duplicate: first kept
            {"play_id": 6.0, "offense_personnel": FTN_11, "defense_personnel": FTN_NICKEL},
            {"nflverse_game_id": "2023_01_DET_KC", "play_id": 5.0},
        ]
    )
    out = parse_participation(raw)
    assert out.schema == pl.Schema(OUTPUT_SCHEMA)
    assert list(out.columns) == [
        "game_id", "play_id", "formation", "personnel", "extra_ol", "def_package", "coverage",
        "man_zone", "target_route", "pressure", "time_to_throw", "part_box", "part_rushers",
    ]  # fmt: skip
    assert out.height == 3
    assert out.select("game_id", "play_id").is_unique().all()
    assert (
        out.filter(pl.col("play_id") == 5.0, pl.col("game_id") == "2020_01_KC_HOU")[
            "personnel"
        ].item()
        == "11"
    )


def test_empty_and_minimal_inputs() -> None:
    out = parse_participation(pl.DataFrame(schema={"season": pl.Int32}))
    assert out.height == 0 and out.schema == pl.Schema(OUTPUT_SCHEMA)
    # extra raw columns (names, lists, season) are dropped; integer play ids become floats
    raw = pl.DataFrame(
        {"nflverse_game_id": ["2016_01_A_B"], "play_id": [42], "season": [2016],
         "offense_names": [["x"]], "offense_personnel": [NGS_11]}
    )  # fmt: skip
    out = parse_participation(raw)
    assert out.schema == pl.Schema(OUTPUT_SCHEMA)
    assert out.row(0, named=True)["play_id"] == 42.0
    assert out.row(0, named=True)["personnel"] == "11"


# --- loader -------------------------------------------------------------------------------------
def _write(root: Path, snapshot: str, season: int, tag: str) -> None:
    d = root / "research" / "nflverse" / "participation" / f"snapshot={snapshot}"
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {"nflverse_game_id": [f"{season}_01_A_B"], "play_id": [1.0], "tag": [tag]}
    ).write_parquet(d / f"season={season}.parquet")


def test_load_newest_snapshot_wins_and_missing_seasons_skip(tmp_path: Path) -> None:
    _write(tmp_path, "2026-09-01", 2018, "old")
    _write(tmp_path, "2026-09-01", 2019, "old")
    _write(tmp_path, "2026-10-03", 2019, "new")
    paths = DataPaths(tmp_path)
    df = load_participation(paths, [2017, 2018, 2019])
    assert df["season"].dtype == pl.Int32
    assert sorted(zip(df["season"], df["tag"], strict=True)) == [(2018, "old"), (2019, "new")]
    assert load_participation(paths, [2018])["tag"].to_list() == ["old"]


def test_load_nothing_is_empty(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path)
    assert load_participation(paths, range(2016, 2026)).is_empty()
    _write(tmp_path, "2026-10-03", 2020, "x")
    assert load_participation(paths, [2015]).is_empty()
    assert parse_participation(load_participation(paths, [2015])).is_empty()


@pytest.mark.integration
def test_real_participation_parses() -> None:
    from nflengine.paths import ensure_data_root

    paths = ensure_data_root(create_dirs=False)
    raw = load_participation(paths, [labels.NGS_ERA_LAST, labels.PART_LAST])
    out = parse_participation(raw)
    assert out.height == raw.height > 0
    assert set(out["personnel"].drop_nulls().unique()) >= set(labels.PERSONNEL_GROUPS)
    assert set(out["def_package"].drop_nulls().unique()) == set(labels.DEF_PACKAGES)
    assert set(out["formation"].drop_nulls()) <= set(labels.FORMATIONS_NGS) | set(
        labels.FORMATIONS_FTN
    )
    assert set(out["coverage"].drop_nulls()) <= set(labels.COVERAGES)
