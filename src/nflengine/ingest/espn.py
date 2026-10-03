"""ESPN public (unofficial) API ingestion. Optional source: always fails soft.

Endpoints confirmed working on 2026-10-03 (documentation/03 -> ESPN):
  scoreboard (per week: status, scores, odds, weather, byes), news, injuries,
  Total QBR (weekly), FPI. Raw JSON is kept next to each parsed table.
Note: ESPN spreads are from the home team's view with the favourite NEGATIVE
(e.g. "DAL -9.5" -> spread -9.5); curation converts to the nflverse convention.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import polars as pl

from nflengine.ingest.base import DatasetResult, SnapshotStore
from nflengine.ingest.http import PoliteClient

SOURCE = "espn"
SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
FITT = "https://site.web.api.espn.com/apis/fitt/v3/sports/football/nfl"


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---- parsers (pure functions, unit-tested with fixtures) -------------------------


def parse_scoreboard(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        home, away = teams.get("home", {}), teams.get("away", {})
        odds = (comp.get("odds") or [{}])[0]
        weather = ev.get("weather") or {}
        status = (ev.get("status") or {}).get("type", {})
        rows.append(
            {
                "espn_event_id": str(ev.get("id")),
                "season": (ev.get("season") or {}).get("year"),
                "season_type": (ev.get("season") or {}).get("type"),
                "week": (ev.get("week") or {}).get("number"),
                "kickoff_utc": ev.get("date"),
                "home_team_espn": (home.get("team") or {}).get("abbreviation"),
                "away_team_espn": (away.get("team") or {}).get("abbreviation"),
                "home_score": _num(home.get("score")),
                "away_score": _num(away.get("score")),
                "completed": bool(status.get("completed")),
                "status": status.get("name"),
                "venue": (comp.get("venue") or {}).get("fullName"),
                "indoor": (comp.get("venue") or {}).get("indoor"),
                "odds_provider": (odds.get("provider") or {}).get("name"),
                "odds_details": odds.get("details"),
                "espn_spread": _num(odds.get("spread")),
                "over_under": _num(odds.get("overUnder")),
                "home_moneyline": _num((odds.get("homeTeamOdds") or {}).get("moneyLine")),
                "away_moneyline": _num((odds.get("awayTeamOdds") or {}).get("moneyLine")),
                "weather_temp_f": _num(weather.get("temperature")),
                "weather_desc": weather.get("displayValue"),
            }
        )
    return rows


def parse_news(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for a in data.get("articles", []):
        cats = a.get("categories") or []
        rows.append(
            {
                "article_id": str(a.get("id")),
                "headline": a.get("headline"),
                "description": a.get("description"),
                "published": a.get("published"),
                "last_modified": a.get("lastModified"),
                "type": a.get("type"),
                "athlete_espn_ids": [str(c["athleteId"]) for c in cats if c.get("athleteId")],
                "team_espn_ids": [str(c["teamId"]) for c in cats if c.get("teamId")],
                "link": ((a.get("links") or {}).get("web") or {}).get("href"),
            }
        )
    return rows


ATHLETE_ID_RE = re.compile(r"/id/(\d+)")


def athlete_id(ath: dict[str, Any]) -> str | None:
    """ESPN injury feeds omit athlete.id; it lives in the player-card link (/id/123/...)."""
    if ath.get("id"):
        return str(ath["id"])
    for link in ath.get("links") or []:
        if m := ATHLETE_ID_RE.search(link.get("href", "")):
            return m.group(1)
    return None


def parse_injuries(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for team in data.get("injuries", []):
        for inj in team.get("injuries", []):
            ath = inj.get("athlete") or {}
            det = inj.get("details") or {}
            rows.append(
                {
                    "team_espn_id": str(team.get("id")),
                    "team_name": team.get("displayName"),
                    "athlete_espn_id": athlete_id(ath),
                    "athlete_name": ath.get("displayName"),
                    "position": (ath.get("position") or {}).get("abbreviation"),
                    "status": inj.get("status"),
                    "date": inj.get("date"),
                    "injury_type": det.get("type"),
                    "injury_location": det.get("location"),
                    "injury_detail": det.get("detail"),
                    "return_date": det.get("returnDate"),
                    "short_comment": inj.get("shortComment"),
                }
            )
    return rows


def parse_fitt_table(data: dict[str, Any], entity_key: str) -> list[dict[str, Any]]:
    """Flatten ESPN 'fitt' leaderboards (QBR: entity_key='athletes', FPI: 'teams')."""
    cat_names = {c.get("name"): c.get("names", []) for c in data.get("categories", [])}
    rows = []
    for item in data.get(entity_key, []):
        ent = item.get("athlete") or item.get("team") or {}
        row: dict[str, Any] = {
            "espn_id": str(ent.get("id")) if ent.get("id") else None,
            "name": ent.get("displayName") or ent.get("name"),
            "abbreviation": ent.get("abbreviation") or (ent.get("teamShortName")),
            "team_abbreviation": ent.get("teamShortName") or ent.get("abbreviation"),
        }
        for cat in item.get("categories", []):
            names = cat_names.get(cat.get("name"), [])
            values = cat.get("totals") or cat.get("values") or []
            for n, v in zip(names, values, strict=False):
                row[f"{cat.get('name')}__{n}"] = _num(v)
        rows.append(row)
    return rows


# ---- ingestion ---------------------------------------------------------------------


def ingest_espn(
    store: SnapshotStore,
    client: PoliteClient,
    season: int,
    weeks: list[int],
    completed_weeks: list[int],
    log: Callable[[str], None] = print,
) -> list[DatasetResult]:
    results: list[DatasetResult] = []

    def run(dataset: str, fn: Callable[[], pl.DataFrame]) -> None:
        try:
            df = fn()
            store.write_parquet(SOURCE, dataset, f"season={season}", df)
            results.append(DatasetResult(SOURCE, dataset, "ok", df.height, [f"season={season}"]))
            log(f"  espn {dataset}: {df.height:,} rows")
        except Exception as exc:  # fail soft
            results.append(DatasetResult(SOURCE, dataset, "failed", detail=type(exc).__name__))
            log(f"  espn {dataset}: FAILED ({type(exc).__name__}) - continuing")

    def scoreboard() -> pl.DataFrame:
        rows = []
        for w in weeks:
            data = client.get_json(
                f"{SITE}/scoreboard", {"seasontype": 2, "week": w, "dates": season}
            )
            store.write_json(SOURCE, "scoreboard_raw", f"season={season}-week={w:02d}", data)
            rows += parse_scoreboard(data)
        return pl.DataFrame(rows)

    def news() -> pl.DataFrame:
        data = client.get_json(f"{SITE}/news", {"limit": 100}, use_cache=False)
        store.write_json(SOURCE, "news_raw", f"season={season}", data)
        return pl.DataFrame(parse_news(data))

    def injuries() -> pl.DataFrame:
        data = client.get_json(f"{SITE}/injuries", use_cache=False)
        store.write_json(SOURCE, "injuries_raw", f"season={season}", data)
        return pl.DataFrame(parse_injuries(data))

    def qbr() -> pl.DataFrame:
        frames = []
        for w in completed_weeks:
            params = {
                "region": "us", "lang": "en", "qbrType": "weeks", "seasontype": 2,
                "isqualified": "false", "season": season, "week": w, "limit": 100,
            }  # fmt: skip
            data = client.get_json(f"{FITT}/qbr", params)
            rows = parse_fitt_table(data, "athletes")
            if rows:
                frames.append(pl.DataFrame(rows).with_columns(pl.lit(w).alias("week")))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def fpi() -> pl.DataFrame:
        params = {"region": "us", "lang": "en", "season": season, "limit": 50}
        data = client.get_json(f"{FITT}/powerindex", params, use_cache=False)
        store.write_json(SOURCE, "fpi_raw", f"season={season}", data)
        return pl.DataFrame(parse_fitt_table(data, "teams"))

    run("scoreboard", scoreboard)
    run("news", news)
    run("injuries", injuries)
    run("qbr_weekly", qbr)
    run("fpi", fpi)
    return results
