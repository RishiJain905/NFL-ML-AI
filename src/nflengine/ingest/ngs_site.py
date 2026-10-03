"""Next Gen Stats site extras (documentation/03 -> Additional free sources).

Probed 2026-10-03: the weekly statboards (passing/receiving/rushing) duplicate
nflverse's NGS copy, and pass-rushing / most leader boards return HTTP 403. The
open play-level leader boards below are the only additions, used as colour for the
digest ("fastest ball carrier", "longest tackle chase", "quickest sack").
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import polars as pl

from nflengine.ingest.base import DatasetResult, SnapshotStore
from nflengine.ingest.http import PoliteClient

SOURCE = "ngs_site"
BASE = "https://nextgenstats.nfl.com/api/leaders"
HEADERS = {"Referer": "https://nextgenstats.nfl.com/stats/top-plays"}
LEADERBOARDS = {
    "speed_ball_carrier": "speed/ballCarrier",
    "distance_tackle": "distance/tackle",
    "time_to_sack": "time/sack",
}


def parse_leaders(board: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for rank, item in enumerate(data.get("leaders", []), start=1):
        play = item.get("play") or {}
        leader = item.get("leader") or {}
        row = {
            "board": board,
            "rank": rank,
            "season": data.get("season"),
            "season_type": data.get("seasonType"),
            "league_average": data.get("leagueAverage"),
            "ngs_game_id": play.get("gameId"),
            "play_id": play.get("playId"),
            "quarter": play.get("quarter"),
            "play_description": play.get("playDescription"),
            "play_time_utc": play.get("timeOfDayUTC"),
        }
        for k, v in leader.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                row[f"leader_{k}"] = v
        rows.append(row)
    return rows


def ingest_ngs_site(
    store: SnapshotStore,
    client: PoliteClient,
    season: int,
    log: Callable[[str], None] = print,
) -> list[DatasetResult]:
    rows: list[dict[str, Any]] = []
    failed = []
    for board, path in LEADERBOARDS.items():
        try:
            data = client.get_json(
                f"{BASE}/{path}",
                {"season": season, "seasonType": "REG", "limit": 50},
                headers=HEADERS,
                use_cache=False,
            )
            store.write_json(SOURCE, "leaders_raw", f"season={season}-{board}", data)
            rows += parse_leaders(board, data)
        except Exception as exc:
            failed.append(f"{board}: {type(exc).__name__}")
    if not rows:
        log(f"  ngs_site leaders: FAILED ({'; '.join(failed)}) - continuing")
        return [DatasetResult(SOURCE, "leaders", "failed", detail="; ".join(failed))]
    df = pl.DataFrame(rows, infer_schema_length=None)
    store.write_parquet(SOURCE, "leaders", f"season={season}", df)
    status = "partial" if failed else "ok"
    log(f"  ngs_site leaders: {df.height} rows" + (f" | {'; '.join(failed)}" if failed else ""))
    return [
        DatasetResult(SOURCE, "leaders", status, df.height, [f"season={season}"], "; ".join(failed))
    ]
