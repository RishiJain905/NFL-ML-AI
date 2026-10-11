"""Play-calling labels (PC00): bucket edges, the neutral rule, directions, the metric registry."""

from __future__ import annotations

import json

import polars as pl
import pytest

from nflengine.playcalling import labels as L

INT, FLT, STR = pl.Int64, pl.Float64, pl.String


def ev(expr: pl.Expr, **cols: tuple[pl.DataType, list]) -> list:
    """Evaluate one expression over columns given as `name=(dtype, values)`."""
    df = pl.DataFrame({k: pl.Series(k, v, dtype=t) for k, (t, v) in cols.items()})
    return df.select(expr.alias("x"))["x"].to_list()


def one(expr: pl.Expr, **cols: tuple[pl.DataType, object]):
    return ev(expr, **{k: (t, [v]) for k, (t, v) in cols.items()})[0]


# --- scrimmage filter ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("play_type", "two_point", "keep"),
    [
        ("pass", 0, True),
        ("run", 0, True),
        ("pass", None, True),  # a null flag is not a two-point try
        ("run", None, True),
        ("pass", 1, False),
        ("run", 1, False),
        ("qb_kneel", 0, False),
        ("qb_spike", 0, False),
        ("no_play", 0, False),
        ("punt", 0, False),
        ("field_goal", 0, False),
        ("kickoff", 0, False),
        ("extra_point", 0, False),
        (None, 0, False),
    ],
)
def test_scrimmage_filter(play_type: str | None, two_point: int | None, keep: bool) -> None:
    out = one(L.scrimmage_filter(), play_type=(STR, play_type), two_point_attempt=(FLT, two_point))
    assert bool(out) is keep


# --- distance, down & distance, field zone, score -----------------------------------------------
@pytest.mark.parametrize(
    ("distance", "bucket"),
    [
        (1, "short"),
        (3, "short"),
        (4, "medium"),
        (6, "medium"),
        (7, "long"),
        (30, "long"),
        (None, None),
    ],
)
def test_distance_bucket_edges(distance: int | None, bucket: str | None) -> None:
    assert one(L.distance_bucket(pl.col("d")), d=(INT, distance)) == bucket


@pytest.mark.parametrize(
    ("down", "distance", "label"),
    [
        (1, 10, "1st_down"),
        (1, 1, "1st_down"),  # 1st down ignores distance
        (1, None, "1st_down"),
        (2, 3, "2nd_short"),
        (2, 4, "2nd_medium"),
        (2, 6, "2nd_medium"),
        (2, 7, "2nd_long"),
        (3, 3, "3rd_short"),
        (3, 4, "3rd_medium"),
        (3, 7, "3rd_long"),
        (4, 1, "4th_down"),
        (4, 15, "4th_down"),
        (2, None, None),  # no distance, no bucket
        (None, 5, None),
        (5, 5, None),
    ],
)
def test_down_distance(down: int | None, distance: int | None, label: str | None) -> None:
    out = one(L.down_distance(pl.col("dn"), pl.col("d")), dn=(INT, down), d=(INT, distance))
    assert out == label


@pytest.mark.parametrize(
    ("yards_to_goal", "zone"),
    [
        (1, "red_zone"),
        (20, "red_zone"),
        (21, "opp_half"),
        (49, "opp_half"),
        (50, "own_half"),
        (79, "own_half"),
        (80, "own_deep"),
        (99, "own_deep"),
        (None, None),
    ],
)
def test_field_zone_edges(yards_to_goal: int | None, zone: str | None) -> None:
    assert one(L.field_zone(pl.col("y")), y=(INT, yards_to_goal)) == zone


@pytest.mark.parametrize(
    ("diff", "state"),
    [
        (-21, "trail_9plus"),
        (-9, "trail_9plus"),
        (-8, "trail_1to8"),
        (-1, "trail_1to8"),
        (0, "tied"),
        (1, "lead_1to8"),
        (8, "lead_1to8"),
        (9, "lead_9plus"),
        (28, "lead_9plus"),
        (None, None),
    ],
)
def test_score_state_edges(diff: int | None, state: str | None) -> None:
    assert one(L.score_state(pl.col("s")), s=(INT, diff)) == state


# --- time ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("quarter", "seconds", "two_min", "bucket"),
    [
        (2, 120, True, "q2_2min"),  # the last two minutes include 2:00 itself
        (2, 121, False, "q2"),
        (2, 0, True, "q2_2min"),
        (4, 120, True, "q4_2min"),
        (4, 121, False, "q4"),
        (1, 100, False, "q1"),  # the first quarter has no two-minute warning
        (1, 120, False, "q1"),
        (3, 100, False, "q3"),
        (3, 1, False, "q3"),
        (5, 100, False, "ot"),  # overtime has no two-minute bucket
        (5, 900, False, "ot"),
        (1, 1800, False, "q1"),
        (4, 1800, False, "q4"),
    ],
)
def test_two_minute_and_time_bucket_edges(
    quarter: int, seconds: int, two_min: bool, bucket: str
) -> None:
    cols = {"q": (INT, quarter), "h": (INT, seconds)}
    assert one(L.two_minute(pl.col("q"), pl.col("h")), **cols) is two_min
    assert one(L.time_bucket(pl.col("q"), pl.col("h")), **cols) == bucket


def test_time_bucket_without_a_quarter_is_null() -> None:
    assert one(L.time_bucket(pl.col("q"), pl.col("h")), q=(INT, None), h=(INT, 100)) is None
    assert one(L.time_bucket(pl.col("q"), pl.col("h")), q=(INT, 0), h=(INT, 100)) is None
    assert one(L.time_bucket(pl.col("q"), pl.col("h")), q=(INT, 6), h=(INT, 100)) is None


def test_time_bucket_is_always_a_known_label() -> None:
    qs = [q for q in range(1, 6) for _ in range(3)]
    hs = [h for _ in range(1, 6) for h in (0, 120, 121)]
    out = ev(L.time_bucket(pl.col("q"), pl.col("h")), q=(INT, qs), h=(INT, hs))
    assert set(out) <= {"q1", "q2", "q2_2min", "q3", "q4", "q4_2min", "ot"}
    assert set(out) == {"q1", "q2", "q2_2min", "q3", "q4", "q4_2min", "ot"}


# --- neutral situation (rbsdm) ------------------------------------------------------------------
@pytest.mark.parametrize(
    ("wp", "down", "seconds", "neutral"),
    [
        (0.50, 1, 1500, True),
        (0.20, 1, 1500, True),  # inclusive at both ends
        (0.80, 1, 1500, True),
        (0.199, 1, 1500, False),
        (0.801, 1, 1500, False),
        (0.19999, 2, 1500, False),
        (0.50, 3, 1500, True),
        (0.50, 4, 1500, False),  # downs 1-3 only
        (0.50, 1, 121, True),
        (0.50, 1, 120, False),  # the last two minutes of a half are out
        (0.50, 1, 0, False),
        (None, 1, 1500, False),  # a null wp is not neutral (never null)
        (0.50, None, 1500, False),
        (0.50, 1, None, False),
        (None, None, None, False),
    ],
)
def test_neutral_edges(wp: float | None, down: int | None, seconds: int | None, neutral: bool):
    out = one(
        L.neutral(pl.col("wp"), pl.col("dn"), pl.col("h")),
        wp=(FLT, wp),
        dn=(INT, down),
        h=(INT, seconds),
    )
    assert out is neutral


def test_neutral_constants_are_rbsdms() -> None:
    assert L.NEUTRAL_WP == (0.20, 0.80)
    assert L.NEUTRAL_MAX_DOWN == 3
    assert L.TWO_MINUTE_SECONDS == 120


# --- run direction, pass zone, snake ------------------------------------------------------------
@pytest.mark.parametrize(
    ("location", "gap", "direction"),
    [
        ("middle", None, "middle"),  # a middle run has no gap
        ("middle", "guard", "middle"),  # ... and a stray gap does not matter
        ("left", "end", "left_end"),
        ("left", "tackle", "left_tackle"),
        ("left", "guard", "left_guard"),
        ("right", "guard", "right_guard"),
        ("right", "tackle", "right_tackle"),
        ("right", "end", "right_end"),
        ("left", None, None),  # a side run without a gap stays null
        ("right", None, None),
        ("left", "", None),
        ("left", "middle", None),  # not a side gap
        ("up", "end", None),
        (None, "end", None),
        (None, None, None),
    ],
)
def test_run_direction(location: str | None, gap: str | None, direction: str | None) -> None:
    out = one(L.run_direction(pl.col("loc"), pl.col("gap")), loc=(STR, location), gap=(STR, gap))
    assert out == direction


def test_run_direction_covers_exactly_the_registry_directions() -> None:
    locs = ["middle", "left", "left", "left", "right", "right", "right"]
    gaps = [None, "end", "tackle", "guard", "guard", "tackle", "end"]
    out = ev(L.run_direction(pl.col("loc"), pl.col("gap")), loc=(STR, locs), gap=(STR, gaps))
    assert sorted(out) == sorted(L.RUN_DIRECTIONS)


@pytest.mark.parametrize(
    ("length", "location", "zone"),
    [
        ("short", "left", "short_left"),
        ("short", "middle", "short_middle"),
        ("short", "right", "short_right"),
        ("deep", "left", "deep_left"),
        ("deep", "middle", "deep_middle"),
        ("deep", "right", "deep_right"),
        ("medium", "left", None),  # only the gamebook's short / deep
        ("short", None, None),
        (None, "left", None),
        ("short", "center", None),
        (None, None, None),
    ],
)
def test_pass_zone(length: str | None, location: str | None, zone: str | None) -> None:
    out = one(L.pass_zone(pl.col("len"), pl.col("loc")), len=(STR, length), loc=(STR, location))
    assert out == zone


def test_pass_zones_are_the_six_combinations() -> None:
    assert set(L.PASS_ZONES) == {
        "short_left", "short_middle", "short_right", "deep_left", "deep_middle", "deep_right",
    }  # fmt: skip
    assert len(L.PASS_ZONES) == len(set(L.PASS_ZONES)) == 6


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("HITCH/CURL", "hitch_curl"),
        ("UNDER CENTER", "under_center"),
        ("SHALLOW CROSS/DRAG", "shallow_cross_drag"),
        ("TEXAS/ANGLE", "texas_angle"),
        ("  Cover 2  ", "cover_2"),
        ("2_MAN", "2_man"),
        ("already_snake", "already_snake"),
        ("a - b", "a_b"),  # runs of separators collapse
        ("x//y", "x_y"),
        ("I_FORM", "i_form"),
        ("", ""),
    ],
)
def test_snake(text: str, label: str) -> None:
    assert L.snake(text) == label


def test_snake_is_idempotent() -> None:
    for text in ("HITCH/CURL", "UNDER CENTER", "a - b", "COVER_6"):
        assert L.snake(L.snake(text)) == L.snake(text)


# --- the situations -----------------------------------------------------------------------------
def test_situation_names_are_unique_and_in_known_families() -> None:
    names = [s.name for s in L.SITUATIONS]
    assert len(names) == len(set(names))
    assert names[0] == "all" and names[1] == "neutral"
    assert {s.family for s in L.SITUATIONS} == {
        "core", "down_distance", "field_zone", "score", "time",
    }  # fmt: skip
    assert {s.name: s.family for s in L.SITUATIONS} == L.SITUATION_FAMILY
    assert [s.name for s in L.CORE_SITUATIONS] == ["all", "neutral"]
    assert len(L.SITUATIONS) == 20  # all, neutral, 8 down & distance, 4 zones, 5 scores, 2-min


def test_situations_match_what_the_label_functions_can_return() -> None:
    by_family = {
        fam: {s.name for s in L.SITUATIONS if s.family == fam}
        for fam in ("down_distance", "field_zone", "score")
    }
    downs = [d for d in (1, 2, 3, 4) for _ in range(40)]
    dists = [x for _ in (1, 2, 3, 4) for x in range(1, 41)]
    dd = ev(L.down_distance(pl.col("a"), pl.col("b")), a=(INT, downs), b=(INT, dists))
    assert set(dd) == by_family["down_distance"]
    zones = ev(L.field_zone(pl.col("y")), y=(INT, list(range(1, 100))))
    assert set(zones) == by_family["field_zone"]
    scores = ev(L.score_state(pl.col("s")), s=(INT, list(range(-30, 31))))
    assert set(scores) == by_family["score"]


def test_situation_expressions_evaluate_and_are_never_null() -> None:
    df = pl.DataFrame(
        {
            "neutral": [True, None, False],
            "two_minute": [None, True, False],
            "down_distance": ["1st_down", None, "3rd_long"],
            "field_zone": ["red_zone", "own_deep", None],
            "score_state": ["tied", None, "lead_9plus"],
        }
    )
    for s in L.SITUATIONS:
        out = df.select(s.expr().alias("x"))["x"]
        assert out.dtype == pl.Boolean
        assert out.null_count() == 0, s.name
    # "all" counts every row, a family label only its own rows
    assert df.select(L.SITUATIONS[0].expr().alias("x"))["x"].to_list() == [True]
    pick = {s.name: s for s in L.SITUATIONS}
    assert df.select(pick["neutral"].expr().alias("x"))["x"].to_list() == [True, False, False]
    assert df.select(pick["two_minute"].expr().alias("x"))["x"].to_list() == [False, True, False]
    assert df.select(pick["3rd_long"].expr().alias("x"))["x"].to_list() == [False, False, True]
    assert df.select(pick["tied"].expr().alias("x"))["x"].to_list() == [True, False, False]


# --- the metric registry ------------------------------------------------------------------------
def test_metric_names_are_unique_and_indexed() -> None:
    names = [m.name for m in L.METRICS]
    assert len(names) == len(set(names))
    assert set(L.METRIC_BY_NAME) == set(names)
    assert L.PFR_BLITZ.name not in names  # a team-game metric, added next to the registry
    assert L.PFR_BLITZ.name == "blitz_rate_pfr" and L.PFR_BLITZ.source == "pfr"


def test_metric_units_and_sources_are_known() -> None:
    for m in (*L.METRICS, L.PFR_BLITZ):
        assert m.unit in {"share", "mean", "over_expected"}, m.name
        assert m.source in {"pbp", "ftn", "participation", "pfr"}, m.name
        assert callable(m.den) and callable(m.num)
        assert isinstance(m.den(), pl.Expr) and isinstance(m.num(), pl.Expr)
        assert m.label and m.label.strip() == m.label
    by_source = {
        s: [m for m in L.METRICS if m.source == s] for s in ("pbp", "ftn", "participation")
    }
    assert all(by_source.values())
    assert all(m.history_only for m in by_source["participation"])
    assert not any(m.history_only for m in by_source["pbp"] + by_source["ftn"])
    assert all(m.first_season == L.FTN_FIRST for m in by_source["ftn"])  # FTN from 2022
    assert all(m.first_season is None for m in by_source["pbp"])
    assert L.METRIC_BY_NAME["proe"].unit == "over_expected"


def test_situations_per_metric_kind() -> None:
    allsit = L.SITUATIONS
    assert L.METRIC_BY_NAME["dropback_rate"].situations() == allsit  # situational
    assert L.METRIC_BY_NAME["blitz_rate"].situations() == allsit
    assert L.METRIC_BY_NAME["play_action_rate"].situations() == allsit
    assert L.METRIC_BY_NAME["adot"].situations() == L.CORE_SITUATIONS  # not situational
    assert L.METRIC_BY_NAME["screen_rate"].situations() == L.CORE_SITUATIONS
    assert [s.name for s in L.METRIC_BY_NAME["adot"].situations()] == ["all", "neutral"]


def test_history_metrics_only_have_the_all_situation() -> None:
    hist = [m for m in L.METRICS if m.history_only]
    assert len(hist) > 40
    for m in hist:
        assert [s.name for s in m.situations()] == ["all"], m.name
    # even when a history metric is flagged situational, history wins
    from dataclasses import replace

    forced = replace(hist[0], situational=True)
    assert [s.name for s in forced.situations()] == ["all"]


def test_exists_in_eras() -> None:
    by = L.METRIC_BY_NAME
    # pbp metrics exist in every season
    assert all(by["dropback_rate"].exists_in(s) for s in (2010, 2016, 2025, 2030))
    # FTN: 2022 on, open ended
    assert not by["play_action_rate"].exists_in(2021)
    assert by["play_action_rate"].exists_in(2022) and by["play_action_rate"].exists_in(2030)
    # participation: bounded on both ends
    assert not by["personnel_11"].exists_in(2015)
    assert by["personnel_11"].exists_in(2016) and by["personnel_11"].exists_in(2025)
    assert not by["personnel_11"].exists_in(2026)
    # era vocabularies: NGS 2016-2022, FTN 2023-2025, a shared label spans both
    assert by["formation_singleback"].exists_in(2022) and not by["formation_singleback"].exists_in(
        2023
    )
    assert by["formation_under_center"].exists_in(2023) and not by[
        "formation_under_center"
    ].exists_in(2022)
    assert by["formation_shotgun"].exists_in(2016) and by["formation_shotgun"].exists_in(2025)
    assert by["route_hitch"].exists_in(2022) and not by["route_hitch"].exists_in(2023)
    assert by["route_quick_out"].exists_in(2023) and not by["route_quick_out"].exists_in(2022)
    assert by["route_go"].exists_in(2016) and by["route_go"].exists_in(2025)
    # coverage starts in 2018; prevent is NGS-only, cover_9 / combo / blown are FTN-only
    assert not by["cov_cover_3"].exists_in(2017) and by["cov_cover_3"].exists_in(2018)
    assert by["cov_prevent"].exists_in(2022) and not by["cov_prevent"].exists_in(2023)
    for c in ("cover_9", "combo", "blown"):
        assert not by[f"cov_{c}"].exists_in(2022) and by[f"cov_{c}"].exists_in(2023)
    assert not by["man_rate"].exists_in(2017) and by["man_rate"].exists_in(2018)
    assert by["def_nickel_share"].exists_in(2016) and not by["def_nickel_share"].exists_in(2026)
    assert L.PFR_BLITZ.exists_in(L.PFR_FIRST) and not L.PFR_BLITZ.exists_in(L.PFR_FIRST - 1)


def test_every_history_metric_has_a_participation_window() -> None:
    for m in L.METRICS:
        if m.history_only:
            assert m.first_season is not None and m.last_season is not None, m.name
            assert L.PART_FIRST <= m.first_season <= m.last_season <= L.PART_LAST, m.name


def test_headline_metrics_exist_and_use_a_situation_they_carry() -> None:
    assert len(L.HEADLINE) == len(set(L.HEADLINE))
    for name, sit in L.HEADLINE:
        m = L.METRIC_BY_NAME[name]
        assert sit in {s.name for s in m.situations()}, (name, sit)
        assert not m.history_only, name  # headline numbers are never research data
    assert ("proe", "neutral") in L.HEADLINE and ("play_action_rate", "all") in L.HEADLINE


def test_catalogue_is_plain_serialisable_data() -> None:
    cat = L.catalogue()
    assert len(cat) == len(L.METRICS) + 1  # + the PFR cross-check
    names = [c["name"] for c in cat]
    assert len(names) == len(set(names)) and names[-1] == "blitz_rate_pfr"
    for c in cat:
        assert set(c) == {
            "name", "label", "unit", "source", "situational", "history_only", "first_season",
            "last_season",
        }  # fmt: skip
    assert json.loads(json.dumps(cat)) == cat
    by = {c["name"]: c for c in cat}
    assert by["personnel_11"]["history_only"] is True and by["personnel_11"]["first_season"] == 2016
    assert (
        by["dropback_rate"]["situational"] is True and by["dropback_rate"]["first_season"] is None
    )


def test_definitions_are_plain_serialisable_data() -> None:
    d = L.definitions()
    assert json.loads(json.dumps(d)) == d
    assert d["neutral"] == {"wp": [0.2, 0.8], "max_down": 3, "not_last_seconds_of_half": 120}
    assert d["deep_shot_air_yards"] == 20
    assert d["explosive"] == {"dropback_yards": 20, "run_yards": 10}
    assert d["box"] == {"light_max": 6, "heavy_min": 8}
    assert d["distance"] == {"short_max": 3, "medium_max": 6}
    assert d["field_zone"] == {"red_zone_max": 20, "own_deep_min": 80}
    assert d["score_big_lead"] == 9 and d["recent_games"] == 4
    for key in ("scrimmage", "dropback", "blitz", "screen", "play_action", "run_direction", "proe"):
        assert isinstance(d[key], str) and d[key]


def test_season_constants_are_consistent() -> None:
    assert L.PART_FIRST == 2016 and L.PART_LAST >= 2025
    assert L.NGS_ERA_LAST == 2022 < L.PART_LAST
    assert L.FTN_FIRST == 2022 and L.PFR_FIRST == 2018 and L.COVERAGE_FIRST == 2018
    assert L.WINDOWS == ("season", "last4", "last_season") and L.SIDES == ("offense", "defense")
    assert L.RECENT_GAMES == 4
    assert set(L.PERSONNEL_GROUPS) == {"11", "12", "13", "21", "22", "10"}
