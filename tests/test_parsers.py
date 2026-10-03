import datetime as dt

from nflengine.ingest.espn import parse_fitt_table, parse_injuries, parse_news, parse_scoreboard
from nflengine.ingest.ngs_site import parse_leaders
from nflengine.ingest.odds_api import parse_odds
from nflengine.ingest.weather import StadiumLocator, kickoff_utc, pick_hour

SCOREBOARD = {
    "events": [
        {
            "id": "401",
            "date": "2026-10-09T00:15Z",
            "season": {"year": 2026, "type": 2},
            "week": {"number": 5},
            "status": {"type": {"completed": False, "name": "STATUS_SCHEDULED"}},
            "weather": {"temperature": 71, "displayValue": "Clear"},
            "competitions": [
                {
                    "venue": {"fullName": "AT&T Stadium", "indoor": True},
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "DAL"}, "score": "0"},
                        {"homeAway": "away", "team": {"abbreviation": "TB"}, "score": "0"},
                    ],
                    "odds": [
                        {
                            "provider": {"name": "DraftKings"},
                            "details": "DAL -9.5",
                            "spread": -9.5,
                            "overUnder": 47.5,
                            "homeTeamOdds": {"moneyLine": -535},
                            "awayTeamOdds": {"moneyLine": 400},
                        }
                    ],
                }
            ],
        }
    ]
}


def test_parse_scoreboard() -> None:
    [row] = parse_scoreboard(SCOREBOARD)
    assert row["espn_event_id"] == "401"
    assert (row["home_team_espn"], row["away_team_espn"]) == ("DAL", "TB")
    assert row["espn_spread"] == -9.5  # ESPN: favourite negative; curation flips the sign
    assert row["home_moneyline"] == -535 and row["over_under"] == 47.5
    assert row["weather_temp_f"] == 71 and row["completed"] is False


def test_parse_injuries_takes_athlete_id_from_link() -> None:
    data = {
        "injuries": [
            {
                "id": "22",
                "displayName": "Arizona Cardinals",
                "injuries": [
                    {
                        "status": "Out",
                        "athlete": {
                            "displayName": "A Player",
                            "position": {"abbreviation": "S"},
                            "links": [
                                {"href": "https://www.espn.com/nfl/player/_/id/4428633/a-player"}
                            ],
                        },
                        "details": {"type": "Back", "returnDate": "2026-10-11"},
                    }
                ],
            }
        ]
    }
    [row] = parse_injuries(data)
    assert row["athlete_espn_id"] == "4428633"
    assert row["status"] == "Out" and row["position"] == "S" and row["injury_type"] == "Back"


def test_parse_news_categories() -> None:
    data = {
        "articles": [
            {"id": 1, "headline": "H", "categories": [{"athleteId": 9}, {"teamId": 3}, {}]}
        ]
    }
    [row] = parse_news(data)
    assert row["athlete_espn_ids"] == ["9"] and row["team_espn_ids"] == ["3"]


def test_parse_fitt_table_flattens_categories() -> None:
    data = {
        "categories": [{"name": "fpi", "names": ["fpi", "fpirank"]}],
        "teams": [
            {
                "team": {"id": "12", "abbreviation": "KC"},
                "categories": [{"name": "fpi", "values": ["4.7", "3"]}],
            }
        ],
    }
    [row] = parse_fitt_table(data, "teams")
    assert row["espn_id"] == "12" and row["abbreviation"] == "KC"
    assert row["fpi__fpi"] == 4.7 and row["fpi__fpirank"] == 3.0


def test_parse_leaders() -> None:
    leader = {
        "play": {"gameId": 1, "playId": 2, "playDescription": "sack"},
        "leader": {"value": 2.1, "nested": {"x": 1}},
    }
    data = {"season": 2026, "seasonType": "REG", "leagueAverage": 4.6, "leaders": [leader]}
    [row] = parse_leaders("time_to_sack", data)
    assert row["rank"] == 1 and row["leader_value"] == 2.1 and "leader_nested" not in row


def test_parse_odds_sides() -> None:
    home, away = "Dallas Cowboys", "Tampa Bay Buccaneers"
    spreads = [
        {"name": home, "price": -110, "point": -9.5},
        {"name": away, "price": -110, "point": 9.5},
    ]
    totals = [{"name": "Over", "price": -110, "point": 47.5}]
    markets = [{"key": "spreads", "outcomes": spreads}, {"key": "totals", "outcomes": totals}]
    events = [
        {
            "id": "e1",
            "home_team": home,
            "away_team": away,
            "bookmakers": [{"key": "dk", "markets": markets}],
        }
    ]
    [row] = parse_odds(events, dt.datetime(2026, 10, 3, tzinfo=dt.UTC))
    assert row["spreads_home_point"] == -9.5 and row["spreads_away_point"] == 9.5
    assert row["totals_over_point"] == 47.5


def test_stadium_name_wins_over_home_team_id() -> None:
    loc = StadiumLocator()
    # nflverse keeps JAX00 for the London game; the name must win.
    hit = loc.locate("JAX00", "Tottenham Hotspur Stadium")
    assert hit["id"] == "LON02" and abs(hit["lat"] - 51.6) < 0.1
    assert loc.locate("GNB00", None)["name"] == "Lambeau Field"
    assert loc.locate("ZZZ00", "Unknown Field") is None


def test_kickoff_utc_and_pick_hour() -> None:
    ko = kickoff_utc("2026-10-11", "13:00")  # EDT -> UTC
    assert ko == dt.datetime(2026, 10, 11, 17, 0, tzinfo=dt.UTC)
    hourly = {"time": ["2026-10-11T16:00", "2026-10-11T17:00"], "temperature_2m": [50, 55]}
    assert pick_hour(hourly, ko)["temperature_2m"] == 55
