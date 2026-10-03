import datetime as dt

import httpx
import polars as pl

from nflengine.curate.lines import odds_api_lines
from nflengine.ingest.odds_api import fetch_with_key_rotation


class FakeSecret:
    def __init__(self, v: str) -> None:
        self.v = v

    def get_secret_value(self) -> str:
        return self.v


class FakeClient:
    """Returns a scripted status per key; never touches the network."""

    def __init__(self, statuses: dict[str, int]) -> None:
        self.statuses = statuses
        self.calls: list[str] = []
        self.last_headers: dict[str, str] = {}

    def get_json(self, url, params, use_cache=True, retry_429=True):
        key = params["apiKey"]
        self.calls.append(key)
        code = self.statuses[key]
        if code != 200:
            req = httpx.Request("GET", "https://example.invalid")
            raise httpx.HTTPStatusError(
                "x", request=req, response=httpx.Response(code, request=req)
            )
        return [{"id": "e1"}]


def keys(*vals: str):
    names = ["ODDS_API_KEY", "ODDS_API_KEY2"]
    return [(n, FakeSecret(v)) for n, v in zip(names, vals, strict=True)]


def test_first_key_used_when_ok() -> None:
    c = FakeClient({"k1": 200, "k2": 200})
    events, used, failures = fetch_with_key_rotation(c, keys("k1", "k2"))
    assert events and used == "ODDS_API_KEY" and failures == [] and c.calls == ["k1"]


def test_falls_back_on_quota_or_auth() -> None:
    for code in (401, 429):
        c = FakeClient({"k1": code, "k2": 200})
        events, used, failures = fetch_with_key_rotation(c, keys("k1", "k2"))
        assert events and used == "ODDS_API_KEY2" and failures == [f"ODDS_API_KEY: HTTP {code}"]


def test_no_fallback_on_non_key_errors_and_no_secret_in_messages() -> None:
    c = FakeClient({"k1": 404, "k2": 200})
    events, used, failures = fetch_with_key_rotation(c, keys("k1", "k2"))
    assert events is None and c.calls == ["k1"]
    assert all("k1" not in f and "k2" not in f for f in failures)


def test_odds_api_lines_flip_sign_and_match_games() -> None:
    odds = pl.DataFrame(
        {
            "odds_event_id": ["e1", "e1", "e2"],
            "commence_time": ["2026-10-09T00:15:00Z"] * 2 + ["2026-12-25T18:00:00Z"],
            "home_team_name": ["Dallas Cowboys", "Dallas Cowboys", "Unknown Team"],
            "away_team_name": ["Tampa Bay Buccaneers"] * 3,
            "bookmaker": ["dk", "fd", "dk"],
            "spreads_home_point": [-9.5, -10.0, -1.0],
            "totals_over_point": [47.5, 47.0, 40.0],
            "h2h_home_price": [-535, -500, -110],
            "h2h_away_price": [400, 380, -110],
        }
    )
    games = pl.DataFrame(
        {
            "game_id": ["2026_05_TB_DAL"],
            "home_team": ["DAL"],
            "away_team": ["TB"],
            "kickoff_utc": [dt.datetime(2026, 10, 9, 0, 15, tzinfo=dt.UTC)],
        }
    )
    names = pl.DataFrame(
        {"team_name": ["Dallas Cowboys", "Tampa Bay Buccaneers"], "team": ["DAL", "TB"]}
    )
    rows, matched, total = odds_api_lines(odds, games, names)
    assert (matched, total) == (1, 2)
    assert rows["home_spread"].to_list() == [9.5, 10.0]  # + = home favoured
    assert rows["provider"].to_list() == ["dk", "fd"]
    assert rows["home_moneyline"].to_list() == [-535.0, -500.0]
