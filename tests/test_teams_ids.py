import polars as pl

from nflengine.curate.ids import attach_gsis, player_crosswalk
from nflengine.curate.teams import (
    CANONICAL_TEAMS,
    normalize_team,
    normalize_team_cols,
    team_like_columns,
    unknown_codes,
)


def test_canonical_has_32_teams() -> None:
    assert len(CANONICAL_TEAMS) == 32 == len(set(CANONICAL_TEAMS))


def test_aliases_map_relocations_and_source_spellings() -> None:
    for alias, canon in [
        ("OAK", "LV"), ("SD", "LAC"), ("STL", "LA"), ("LAR", "LA"), ("WSH", "WAS"),
        ("JAC", "JAX"), ("GNB", "GB"), ("KAN", "KC"), ("ARZ", "ARI"), ("lar", "LA"),
    ]:  # fmt: skip
        assert normalize_team(alias) == canon
    assert normalize_team("KC") == "KC"
    assert normalize_team(None) is None


def test_normalize_columns_and_unknowns() -> None:
    df = pl.DataFrame({"posteam": ["OAK", "KC", None], "home_team": ["STL", "XYZ", "SD"]})
    out = normalize_team_cols(df, team_like_columns(df))
    assert out["posteam"].to_list() == ["LV", "KC", None]
    assert out["home_team"].to_list() == ["LA", "XYZ", "LAC"]
    assert unknown_codes(out, ["posteam", "home_team"]) == {"home_team": ["XYZ"]}


def test_team_like_columns_skips_names() -> None:
    df = pl.DataFrame({"team": ["KC"], "team_name": ["Chiefs"], "td_team": ["KC"], "x": [1]})
    assert set(team_like_columns(df)) == {"team", "td_team"}


def test_attach_gsis_and_join_rate() -> None:
    players = pl.DataFrame(
        {"gsis_id": ["00-1", "00-2"], "pfr_id": ["AbcdXx00", "EfghYy00"], "espn_id": [11, 22]}
    )
    xw = player_crosswalk(players)
    snaps = pl.DataFrame({"pfr_player_id": ["AbcdXx00", "Nope", None], "snaps": [50, 3, 1]})
    out, rate = attach_gsis(snaps, "pfr_player_id", xw, "pfr_id", "snaps")
    assert out["gsis_id"].to_list() == ["00-1", None, None]
    assert (rate.matched, rate.total) == (1, 2)  # null keys are not counted
    espn = pl.DataFrame({"athlete_espn_id": ["22"]})
    out, rate = attach_gsis(espn, "athlete_espn_id", xw, "espn_id", "espn")
    assert out["gsis_id"].to_list() == ["00-2"] and rate.rate == 1.0
