import datetime as dt

import numpy as np
import polars as pl
import pytest
import yaml

from nflengine.features.venues import (
    STADIUMS_PATH,
    TRAVEL_COLUMNS,
    _norm_name,
    _wrap_hours,
    game_travel,
    haversine_km,
    load_game_venues,
    load_venues,
    team_home_venues,
)

VENUES = load_venues()


def _at(stadium_id: str) -> tuple[float, float]:
    row = VENUES.filter(pl.col("stadium_id") == stadium_id).row(0, named=True)
    return row["lat"], row["lon"]


def _km(a: str, b: str) -> float:
    return float(haversine_km(*_at(a), *_at(b)))


def _utc(y: int, m: int, d: int, hour: int = 17, minute: int = 0) -> dt.datetime:
    return dt.datetime(y, m, d, hour, minute, tzinfo=dt.UTC)


def _games(rows: list[dict]) -> pl.DataFrame:
    """Games from dicts with home, away, stadium_id, when; optional season, neutral, stadium."""
    out = []
    for r in rows:
        when = r["when"]
        out.append(
            {
                "game_id": r.get("game_id", f"{when:%Y%m%d%H%M}_{r['away']}_{r['home']}"),
                "season": r.get("season", when.year),
                "home_team": r["home"],
                "away_team": r["away"],
                "neutral_site": r.get("neutral", False),
                "stadium_id": r["stadium_id"],
                "kickoff_utc": when,
            }
            | ({"stadium": r.get("stadium")} if any("stadium" in x for x in rows) else {})
        )
    return pl.DataFrame(out).with_columns(pl.col("season").cast(pl.Int32))


def _g(home: str, away: str, stadium_id: str, when: dt.datetime, **extra) -> dict:
    return {"home": home, "away": away, "stadium_id": stadium_id, "when": when, **extra}


def _league(season: int, hosts: dict[str, str]) -> list[dict]:
    """One September home game per team (each hosts the next team in the dict), so every
    team has a known home venue that season."""
    teams = list(hosts)
    return [
        {
            "home": team,
            "away": teams[(i + 1) % len(teams)],
            "stadium_id": hosts[team],
            "when": _utc(season, 9, 1 + i),
            "season": season,
        }
        for i, team in enumerate(teams)
    ]


def _row(df: pl.DataFrame, game_id: str) -> dict:
    return df.filter(pl.col("game_id") == game_id).row(0, named=True)


# ---- reference data ------------------------------------------------------------------------------
def test_haversine_known_distances() -> None:
    wembley, metlife = _at("LON00"), _at("NYC01")
    assert haversine_km(*wembley, *metlife) == pytest.approx(5570, abs=50)
    assert haversine_km(*metlife, *wembley) == pytest.approx(haversine_km(*wembley, *metlife))
    assert haversine_km(*metlife, *metlife) == 0.0
    assert _km("SEA00", "MIA00") == pytest.approx(4380, abs=50)
    # Arrays work, and a missing coordinate gives NaN rather than an error.
    out = haversine_km(
        np.array([wembley[0], np.nan]), np.array([wembley[1], 0.0]), metlife[0], metlife[1]
    )
    assert out[0] == pytest.approx(5570, abs=50)
    assert np.isnan(out[1])


def test_load_venues_returns_every_yaml_entry_with_a_time_zone() -> None:
    cfg = yaml.safe_load(STADIUMS_PATH.read_text(encoding="utf-8"))["stadiums"]
    assert VENUES.columns == ["stadium_id", "name", "lat", "lon", "tz", "aliases"]
    assert sorted(VENUES["stadium_id"]) == sorted(cfg)
    assert VENUES["tz"].null_count() == 0
    assert VENUES["lat"].is_between(-90, 90).all()
    assert VENUES["lon"].is_between(-180, 180).all()
    assert VENUES.filter(pl.col("stadium_id") == "PHO00")["tz"].to_list() == ["America/Phoenix"]


def test_stadium_names_are_unambiguous() -> None:
    # Name matching is the first resolution step, so two stadiums must not share a name.
    seen: dict[str, str] = {}
    for row in VENUES.iter_rows(named=True):
        for n in [row["name"], *row["aliases"]]:
            owner = seen.setdefault(_norm_name(n), row["stadium_id"])
            assert owner == row["stadium_id"], f"{n!r} belongs to {owner} and {row['stadium_id']}"


def test_load_venues_rejects_a_missing_time_zone(tmp_path) -> None:
    bad = tmp_path / "stadiums.yaml"
    bad.write_text("stadiums:\n  AAA00: {name: Nowhere, lat: 1.0, lon: 2.0}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="AAA00"):
        load_venues(bad)


def test_game_venue_corrections_point_at_known_stadiums() -> None:
    fixes = load_game_venues()
    assert "2025_04_MIN_PIT" in fixes  # the Dublin game nflverse lists at Pittsburgh
    assert set(fixes.values()) <= set(VENUES["stadium_id"])


# ---- home venues ---------------------------------------------------------------------------------
def test_home_venue_follows_a_relocation() -> None:
    games = _games(
        [
            _g("LV", "DEN", "OAK00", _utc(2019, 9, 15)),
            _g("LV", "KC", "OAK00", _utc(2019, 10, 6)),
            _g("LV", "DEN", "VEG00", _utc(2020, 9, 20)),
            _g("LV", "KC", "VEG00", _utc(2020, 10, 4)),
        ]
    )
    homes = team_home_venues(games).filter(pl.col("team") == "LV").sort("season")
    assert homes.select("season", "home_stadium_id").rows() == [(2019, "OAK00"), (2020, "VEG00")]

    away_at_den = _games(
        [
            _g("DEN", "LV", "DEN00", _utc(2019, 11, 3)),
            _g("DEN", "LV", "DEN00", _utc(2020, 11, 3)),
        ]
    )
    travel = game_travel(pl.concat([games, away_at_den]))
    assert _row(travel, "201911031700_LV_DEN")["away_travel_km"] == pytest.approx(
        _km("OAK00", "DEN00")
    )
    assert _row(travel, "202011031700_LV_DEN")["away_travel_km"] == pytest.approx(
        _km("VEG00", "DEN00")
    )


def test_home_venue_is_the_most_played_ties_to_the_latest_neutral_games_ignored() -> None:
    games = _games(
        [
            # Tie: one game each, the later one wins.
            _g("AAA", "ZZ", "BAL00", _utc(2018, 9, 10)),
            _g("AAA", "ZZ", "PHI00", _utc(2018, 10, 10)),
            # Majority beats recency.
            _g("BBB", "ZZ", "BAL00", _utc(2018, 9, 10)),
            _g("BBB", "ZZ", "BAL00", _utc(2018, 9, 17)),
            _g("BBB", "ZZ", "PHI00", _utc(2018, 10, 10)),
            # Neutral-site "home" games don't count.
            _g("CCC", "ZZ", "BAL00", _utc(2018, 9, 10)),
            _g("CCC", "ZZ", "PHI00", _utc(2018, 10, 10), neutral=True),
            _g("CCC", "ZZ", "PHI00", _utc(2018, 11, 10), neutral=True),
            # Only neutral home games: use them rather than leave the team without a venue.
            _g("DDD", "ZZ", "PHI00", _utc(2018, 10, 10), neutral=True),
            # Home in 2018, only away in 2019: keeps its 2018 venue.
            _g("EEE", "ZZ", "BAL00", _utc(2018, 9, 10)),
            _g("ZZ", "AAA", "BAL00", _utc(2018, 9, 24)),
            _g("ZZ", "EEE", "PHI00", _utc(2019, 9, 10)),
        ]
    )
    homes = team_home_venues(games)
    got = {(r["season"], r["team"]): r["home_stadium_id"] for r in homes.iter_rows(named=True)}
    assert got[(2018, "AAA")] == "PHI00"
    assert got[(2018, "BBB")] == "BAL00"
    assert got[(2018, "CCC")] == "BAL00"
    assert got[(2018, "DDD")] == "PHI00"
    assert got[(2019, "EEE")] == "BAL00"
    # One row per team-season that appears in the games, none missing a venue.
    assert homes.height == len(got) == 8
    assert homes["home_stadium_id"].null_count() == 0


def test_every_team_gets_a_home_venue_each_season() -> None:
    hosts = {"NYG": "NYC01", "DAL": "DAL00", "LA": "STL00", "ARI": "PHO00"}
    games = _games(_league(2012, hosts) + _league(2016, hosts | {"LA": "LAX99"}))
    homes = team_home_venues(games)
    assert homes.height == 8
    assert homes.filter(pl.col("team") == "LA").sort("season")["home_stadium_id"].to_list() == [
        "STL00",
        "LAX99",
    ]


# ---- travel --------------------------------------------------------------------------------------
HOSTS = {
    "NYG": "NYC01",
    "DAL": "DAL00",
    "LAC": "LAX97",
    "ARI": "PHO00",
    "JAX": "JAX00",
    "NE": "BOS00",
}


def test_home_team_at_its_own_stadium_travels_zero() -> None:
    games = _games(_league(2017, HOSTS))
    out = game_travel(games)
    assert out.columns == TRAVEL_COLUMNS
    assert out["home_travel_km"].to_list() == [0.0] * 6
    assert out["home_tz_shift"].to_list() == [0.0] * 6
    # NYG hosts DAL: DAL flies from Dallas to the Meadowlands.
    r = _row(out, "201709011700_DAL_NYG")
    assert r["venue_stadium_id"] == "NYC01"
    assert r["away_travel_km"] == pytest.approx(_km("DAL00", "NYC01"))
    assert 2000 < r["away_travel_km"] < 2500
    assert r["away_tz_shift"] == 1.0  # Dallas (CDT) to New York (EDT): one zone east
    assert not out["venue_fallback"].any()


def test_neutral_site_makes_both_teams_travel() -> None:
    games = _games(
        _league(2017, HOSTS)
        + [
            _g("JAX", "NE", "LON00", _utc(2017, 10, 1, 13, 30), neutral=True),
        ]
    )
    r = _row(game_travel(games), "201710011330_NE_JAX")
    assert r["venue_stadium_id"] == "LON00"
    assert r["home_travel_km"] == pytest.approx(_km("JAX00", "LON00"))
    assert r["away_travel_km"] == pytest.approx(_km("BOS00", "LON00"))
    assert r["home_travel_km"] > 5000 and r["away_travel_km"] > 5000
    # 2017-10-01: London is on BST (UTC+1), the US east coast on EDT (UTC-4).
    assert r["home_tz_shift"] == r["away_tz_shift"] == 5.0


def test_tz_shift_sign() -> None:
    games = _games(
        _league(2017, HOSTS)
        + [
            # LA-based team in New York in October: +3. New York team in LA: -3.
            _g("NYG", "LAC", "NYC01", _utc(2017, 10, 15)),
            _g("LAC", "NYG", "LAX97", _utc(2017, 10, 22)),
        ]
    )
    out = game_travel(games)
    assert _row(out, "201710151700_LAC_NYG")["away_tz_shift"] == 3.0
    assert _row(out, "201710221700_NYG_LAC")["away_tz_shift"] == -3.0
    assert _row(out, "201710151700_LAC_NYG")["home_tz_shift"] == 0.0
    assert _row(out, "201710151700_LAC_NYG")["away_travel_km"] == pytest.approx(
        _km("LAX97", "NYC01")
    )


@pytest.mark.parametrize(
    ("home", "away", "stadium_id", "when", "away_shift"),
    [
        # Phoenix never changes its clock. July: LA is on PDT (-7) like Phoenix (MST, -7) ...
        ("ARI", "LAC", "PHO00", _utc(2017, 7, 16), 0.0),
        # ... October: still PDT ...
        ("ARI", "LAC", "PHO00", _utc(2017, 10, 15), 0.0),
        # ... December: LA is on PST (-8), Phoenix an hour ahead.
        ("ARI", "LAC", "PHO00", _utc(2017, 12, 10), 1.0),
        # The same trip the other way round: Phoenix to LA.
        ("LAC", "ARI", "LAX97", _utc(2017, 10, 22), 0.0),
        ("LAC", "ARI", "LAX97", _utc(2017, 12, 17), -1.0),
    ],
)
def test_phoenix_tz_shift_follows_dst_at_kickoff(home, away, stadium_id, when, away_shift) -> None:
    game = _g(home, away, stadium_id, when, game_id="x")
    r = _row(game_travel(_games([*_league(2017, HOSTS), game])), "x")
    assert r["away_tz_shift"] == away_shift
    assert r["home_tz_shift"] == 0.0


def test_london_shift_changes_in_the_week_europe_alone_leaves_dst() -> None:
    # Europe ended DST on 2026-10-25, the US ends it on 2026-11-01: for that week London is
    # only 4 hours ahead of New York, not 5.
    base = _league(2026, HOSTS)

    def london(when: dt.datetime) -> float:
        game = _g("JAX", "NE", "LON00", when, neutral=True, game_id="x")
        return _row(game_travel(_games([*base, game])), "x")["away_tz_shift"]

    assert london(_utc(2026, 10, 11, 13, 30)) == 5.0
    assert london(_utc(2026, 10, 28, 14, 30)) == 4.0
    assert london(_utc(2026, 11, 8, 14, 30)) == 5.0


def test_tz_shift_wraps_into_minus_12_to_plus_12() -> None:
    raw = pl.Series([17.0, -13.0, 13.0, 12.0, -12.0, 0.0, 5.5, -17.0, 3.0])
    assert _wrap_hours(raw).to_list() == [-7.0, 11.0, -11.0, 12.0, 12.0, 0.0, 5.5, 7.0, 3.0]

    # An LA team at Melbourne in September: Melbourne is UTC+10 (AEST), LA UTC-7 (PDT), so the
    # raw difference is +17 but the trip is 7 hours back. A New York team: +14 -> -10.
    base = _league(2026, HOSTS)

    def melbourne(when: dt.datetime) -> dict:
        game = _g("LAC", "NYG", "MEL00", when, neutral=True, game_id="x")
        return _row(game_travel(_games([*base, game])), "x")

    sept = melbourne(_utc(2026, 9, 10, 0, 15))
    assert sept["home_tz_shift"] == -7.0
    assert sept["away_tz_shift"] == -10.0
    # Australian DST began on 2026-10-04 (Melbourne UTC+11), the US still on EDT/PDT: +18 -> -6.
    assert melbourne(_utc(2026, 10, 25, 4, 0))["home_tz_shift"] == -6.0


# ---- venue resolution ----------------------------------------------------------------------------
def test_unknown_stadium_falls_back_to_the_home_venue_and_is_flagged() -> None:
    games = _games(
        _league(2017, HOSTS)
        + [
            _g("NYG", "DAL", "ZZZ99", _utc(2017, 11, 5), game_id="u"),
        ]
    )
    out = game_travel(games)
    r = _row(out, "u")
    assert r["venue_fallback"] is True
    assert r["venue_stadium_id"] == "NYC01"
    assert r["home_travel_km"] == 0.0
    assert r["away_travel_km"] == pytest.approx(_km("DAL00", "NYC01"))
    assert out.filter(pl.col("venue_fallback")).height == 1
    assert out["home_travel_km"].null_count() == 0


def test_a_home_team_whose_only_stadium_is_unknown_gets_nulls_not_an_error() -> None:
    games = _games(
        [
            _g("NEW", "DAL", "ZZZ99", _utc(2017, 9, 10), game_id="a"),
            _g("DAL", "NEW", "DAL00", _utc(2017, 9, 17), game_id="b"),
        ]
    )
    out = game_travel(games)
    a = _row(out, "a")
    assert a["venue_fallback"] is True and a["venue_stadium_id"] == "ZZZ99"
    assert a["home_travel_km"] is None and a["away_travel_km"] is None
    assert a["home_tz_shift"] is None and a["away_tz_shift"] is None
    b = _row(out, "b")
    assert b["home_travel_km"] == 0.0 and b["away_travel_km"] is None


def test_venue_resolves_by_name_before_stadium_id() -> None:
    # 2026 PHI@JAX in London: nflverse keeps stadium_id JAX00 but names the real stadium.
    games = _games(
        [
            *_league(2026, HOSTS | {"PHI": "PHI00"}),
            {
                "home": "JAX",
                "away": "PHI",
                "stadium_id": "JAX00",
                "stadium": "Tottenham Hotspur Stadium",
                "when": _utc(2026, 10, 11, 13, 30),
                "game_id": "lon",
            },
            {
                "home": "JAX",
                "away": "NE",
                "stadium_id": "JAX00",
                "stadium": "EverBank Stadium",
                "when": _utc(2026, 11, 22),
                "game_id": "home",
            },
        ]
    )
    out = game_travel(games)
    r = _row(out, "lon")
    assert r["venue_stadium_id"] == "LON02" and not r["venue_fallback"]
    assert r["home_travel_km"] == pytest.approx(_km("JAX00", "LON02"))
    # The home team's own venue is still the stadium it hosts most games in.
    assert _row(out, "home")["home_travel_km"] == 0.0
    assert team_home_venues(games).filter((pl.col("team") == "JAX") & (pl.col("season") == 2026))[
        "home_stadium_id"
    ].to_list() == ["JAX00"]
    # Without a name column the id is used.
    assert _row(game_travel(games.drop("stadium")), "lon")["venue_stadium_id"] == "JAX00"


def test_game_venue_corrections_beat_the_listed_stadium() -> None:
    # nflverse lists Acrisure Stadium for the 2025 Dublin game; the YAML corrects it.
    games = _games(
        [
            *_league(2025, {"PIT": "PIT00", "MIN": "MIN01"}),
            _g(
                "PIT",
                "MIN",
                "PIT00",
                _utc(2025, 9, 28, 13, 30),
                stadium="Acrisure Stadium",
                neutral=True,
                game_id="2025_04_MIN_PIT",
            ),
        ]
    )
    fixed = _row(game_travel(games), "2025_04_MIN_PIT")
    assert fixed["venue_stadium_id"] == "DUB00"
    assert fixed["home_travel_km"] == pytest.approx(_km("PIT00", "DUB00"))
    assert fixed["away_tz_shift"] == 6.0  # Dublin IST (+1) vs Minneapolis CDT (-5)
    off = _row(game_travel(games, overrides={}), "2025_04_MIN_PIT")
    assert off["venue_stadium_id"] == "PIT00" and off["home_travel_km"] == 0.0
    custom = _row(game_travel(games, overrides={"2025_04_MIN_PIT": "LON00"}), "2025_04_MIN_PIT")
    assert custom["venue_stadium_id"] == "LON00"


# ---- output shape --------------------------------------------------------------------------------
def test_output_schema_row_order_and_kickoff_time_zone_handling() -> None:
    rows = _league(2017, HOSTS) + [
        _g("NYG", "LAC", "NYC01", _utc(2017, 10, 15)),
        _g("ARI", "NYG", "PHO00", _utc(2017, 12, 10)),
    ]
    games = _games(rows).reverse()  # not sorted by anything
    out = game_travel(games)
    assert out["game_id"].to_list() == games["game_id"].to_list()
    assert out.columns == TRAVEL_COLUMNS
    for c in ("home_travel_km", "away_travel_km", "home_tz_shift", "away_tz_shift"):
        assert out.schema[c] == pl.Float64
        assert out[c].null_count() == 0
    assert out.schema["venue_fallback"] == pl.Boolean

    # The same instants in another zone, or with no zone at all (read as UTC), give the same result.
    toronto = games.with_columns(pl.col("kickoff_utc").dt.convert_time_zone("America/Toronto"))
    naive = games.with_columns(pl.col("kickoff_utc").dt.replace_time_zone(None))
    assert game_travel(toronto).equals(out)
    assert game_travel(naive).equals(out)
