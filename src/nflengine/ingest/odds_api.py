"""The Odds API (free tier) current NFL lines. Optional: skipped without ODDS_API_KEY.

The API key travels as a query parameter, so errors are reported by exception type
only, never with the request URL (which would contain the key).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import polars as pl

from nflengine.ingest.base import DatasetResult, SnapshotStore
from nflengine.ingest.http import PoliteClient
from nflengine.settings import get_env

SOURCE = "odds_api"
URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"


def parse_odds(events: list[dict[str, Any]], pulled_at: dt.datetime) -> list[dict[str, Any]]:
    rows = []
    for ev in events:
        for book in ev.get("bookmakers", []):
            row: dict[str, Any] = {
                "odds_event_id": ev.get("id"),
                "commence_time": ev.get("commence_time"),
                "home_team_name": ev.get("home_team"),
                "away_team_name": ev.get("away_team"),
                "bookmaker": book.get("key"),
                "last_update": book.get("last_update"),
                "pulled_at": pulled_at,
            }
            for market in book.get("markets", []):
                for o in market.get("outcomes", []):
                    side = (
                        "home"
                        if o.get("name") == ev.get("home_team")
                        else (
                            "away"
                            if o.get("name") == ev.get("away_team")
                            else o.get("name", "").lower()
                        )
                    )
                    row[f"{market['key']}_{side}_price"] = o.get("price")
                    if o.get("point") is not None:
                        row[f"{market['key']}_{side}_point"] = o.get("point")
            rows.append(row)
    return rows


def ingest_odds_api(
    store: SnapshotStore,
    client: PoliteClient,
    season: int,
    log: Callable[[str], None] = print,
) -> list[DatasetResult]:
    env = get_env()
    if not env.is_set("ODDS_API_KEY"):
        log("  odds_api: skipped (ODDS_API_KEY not set)")
        return [DatasetResult(SOURCE, "odds", "skipped", detail="ODDS_API_KEY not set")]
    pulled_at = dt.datetime.now(dt.UTC)
    try:
        events = client.get_json(
            URL,
            {
                "apiKey": env.odds_api_key.get_secret_value(),
                "regions": "us",
                "markets": "h2h,spreads,totals",
                "oddsFormat": "american",
            },
            use_cache=False,
        )
    except Exception as exc:
        log(f"  odds_api: FAILED ({type(exc).__name__}) - continuing")
        return [DatasetResult(SOURCE, "odds", "failed", detail=type(exc).__name__)]
    df = pl.DataFrame(parse_odds(events, pulled_at), infer_schema_length=None)
    store.write_parquet(SOURCE, "odds", f"season={season}", df)
    log(f"  odds_api: {df.height} bookmaker-game rows")
    return [DatasetResult(SOURCE, "odds", "ok", df.height, [f"season={season}"])]
