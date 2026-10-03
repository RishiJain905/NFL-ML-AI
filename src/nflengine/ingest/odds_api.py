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
    keys = [
        (name, getattr(env, name.lower()))
        for name in ("ODDS_API_KEY", "ODDS_API_KEY2")
        if env.is_set(name)
    ]
    if not keys:
        log("  odds_api: skipped (no ODDS_API_KEY set)")
        return [DatasetResult(SOURCE, "odds", "skipped", detail="no ODDS_API_KEY set")]
    pulled_at = dt.datetime.now(dt.UTC)
    events, used, failures = fetch_with_key_rotation(client, keys)
    if events is None:
        detail = "; ".join(failures)
        log(f"  odds_api: FAILED ({detail}) - continuing")
        return [DatasetResult(SOURCE, "odds", "failed", detail=detail)]
    quota = client.last_headers.get("x-requests-remaining", "?")
    df = pl.DataFrame(parse_odds(events, pulled_at), infer_schema_length=None)
    store.write_parquet(SOURCE, "odds", f"season={season}", df)
    detail = f"via {used}; {quota} requests left on it" + (
        f" (fell back: {'; '.join(failures)})" if failures else ""
    )
    log(f"  odds_api: {df.height} bookmaker-game rows | {detail}")
    return [DatasetResult(SOURCE, "odds", "ok", df.height, [f"season={season}"], detail)]


def fetch_with_key_rotation(
    client: PoliteClient, keys: list[tuple[str, Any]]
) -> tuple[Any | None, str | None, list[str]]:
    """Try each key in order; fall through on auth/quota errors (401/403/429).

    Failures are reported as '<VAR NAME>: <status or exception type>' only, never
    with the URL (it contains the key).
    """
    import httpx

    failures: list[str] = []
    for name, secret in keys:
        try:
            events = client.get_json(
                URL,
                {
                    "apiKey": secret.get_secret_value(),
                    "regions": "us",
                    "markets": "h2h,spreads,totals",
                    "oddsFormat": "american",
                },
                use_cache=False,
                retry_429=False,
            )
            return events, name, failures
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            failures.append(f"{name}: HTTP {code}")
            if code not in (401, 403, 429):
                break  # not a key problem; another key won't help
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}")
            break
    return None, None, failures
