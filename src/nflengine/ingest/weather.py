"""Open-Meteo game-time forecasts for upcoming games (no API key needed).

Forecasts are stored as *data available at pull time* (`pulled_at`), so the live
model only ever sees what was knowable when it ran (documentation/04 -> Leakage).
Domes / closed roofs are skipped. Forecast horizon is ~16 days.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from nflengine.ingest.base import DatasetResult, SnapshotStore
from nflengine.ingest.http import PoliteClient
from nflengine.settings import CONFIG_DIR

SOURCE = "open_meteo"
URL = "https://api.open-meteo.com/v1/forecast"
HOURLY = "temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation,precipitation_probability"
SKIP_ROOFS = {"dome", "closed"}


def _norm(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


class StadiumLocator:
    def __init__(self, path: Path | None = None):
        cfg = yaml.safe_load((path or CONFIG_DIR / "stadiums.yaml").read_text(encoding="utf-8"))
        self.by_id: dict[str, dict[str, Any]] = cfg["stadiums"]
        self.by_name: dict[str, dict[str, Any]] = {}
        for sid, s in self.by_id.items():
            for n in [s["name"], *(s.get("aliases") or [])]:
                self.by_name[_norm(n)] = {**s, "id": sid}

    def locate(self, stadium_id: str | None, stadium_name: str | None) -> dict[str, Any] | None:
        if stadium_name and (hit := self.by_name.get(_norm(stadium_name))):
            return hit
        if stadium_id and stadium_id in self.by_id:
            return {**self.by_id[stadium_id], "id": stadium_id}
        return None


def kickoff_utc(gameday: str, gametime: str | None) -> dt.datetime:
    """nflverse gameday/gametime are US Eastern."""
    from zoneinfo import ZoneInfo

    hh, mm = (gametime or "13:00").split(":")[:2]
    local = dt.datetime.fromisoformat(gameday).replace(
        hour=int(hh), minute=int(mm), tzinfo=ZoneInfo("America/New_York")
    )
    return local.astimezone(dt.UTC)


def pick_hour(hourly: dict[str, list[Any]], when: dt.datetime) -> dict[str, Any]:
    times = [dt.datetime.fromisoformat(t).replace(tzinfo=dt.UTC) for t in hourly["time"]]
    i = min(range(len(times)), key=lambda k: abs((times[k] - when).total_seconds()))
    return {k: v[i] for k, v in hourly.items() if k != "time"} | {"forecast_hour_utc": times[i]}


def ingest_weather(
    store: SnapshotStore,
    client: PoliteClient,
    upcoming_games: pl.DataFrame,
    log: Callable[[str], None] = print,
) -> list[DatasetResult]:
    """`upcoming_games`: schedule rows (game_id, season, week, gameday, gametime, roof,
    stadium_id, stadium) for games in the forecast horizon."""
    locator = StadiumLocator()
    now = dt.datetime.now(dt.UTC)
    rows, unresolved = [], []
    for g in upcoming_games.iter_rows(named=True):
        if (g.get("roof") or "").lower() in SKIP_ROOFS:
            continue
        ko = kickoff_utc(g["gameday"], g.get("gametime"))
        if not (now <= ko <= now + dt.timedelta(days=15)):
            continue
        loc = locator.locate(g.get("stadium_id"), g.get("stadium"))
        if loc is None:
            unresolved.append(f"{g['game_id']} ({g.get('stadium')})")
            continue
        try:
            data = client.get_json(
                URL,
                {
                    "latitude": loc["lat"],
                    "longitude": loc["lon"],
                    "hourly": HOURLY,
                    "wind_speed_unit": "mph",
                    "temperature_unit": "fahrenheit",
                    "precipitation_unit": "mm",
                    "timezone": "UTC",
                    "start_date": ko.date().isoformat(),
                    "end_date": ko.date().isoformat(),
                },  # fmt: skip
                use_cache=False,
            )
        except Exception as exc:
            unresolved.append(f"{g['game_id']} ({type(exc).__name__})")
            continue
        hour = pick_hour(data["hourly"], ko)
        rows.append(
            {
                "game_id": g["game_id"],
                "season": g["season"],
                "week": g["week"],
                "kickoff_utc": ko,
                "stadium_used": loc["name"],
                "roof": g.get("roof"),
                "temp_f": hour.get("temperature_2m"),
                "wind_mph": hour.get("wind_speed_10m"),
                "gust_mph": hour.get("wind_gusts_10m"),
                "precip_mm": hour.get("precipitation"),
                "precip_prob": hour.get("precipitation_probability"),
                "forecast_hour_utc": hour["forecast_hour_utc"],
                "pulled_at": now,
            }
        )
    detail = f"unresolved: {', '.join(unresolved)}" if unresolved else ""
    if not rows:
        log("  open_meteo forecasts: no games in horizon" + (f" | {detail}" if detail else ""))
        return [
            DatasetResult(SOURCE, "forecasts", "skipped", detail=detail or "no games in horizon")
        ]
    df = pl.DataFrame(rows)
    season = int(df["season"].max())
    store.write_parquet(SOURCE, "forecasts", f"season={season}", df)
    log(f"  open_meteo forecasts: {df.height} games" + (f" | {detail}" if detail else ""))
    return [
        DatasetResult(
            SOURCE,
            "forecasts",
            "partial" if unresolved else "ok",
            df.height,
            [f"season={season}"],
            detail,
        )
    ]
