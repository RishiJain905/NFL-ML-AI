"""The Game day mockup's data: the real app's answers on replayed 2026 games (LD02).

Runs the control room's API in-process (`create_app` + `TestClient`) with Game day on a
`ReplayFeed` (the saved ESPN summaries under `{NFL_DATA_ROOT}/live/summaries/`, nothing
fetched from ESPN), pinned at each mockup moment, and saves every answer the Game day tab
reads into `{NFL_DATA_ROOT}/cache/live-decisions-mockup/payloads.json` (or `--out`). Nothing
else is written apart from the app's own team-context cache (`cache/control-room/live/`).

    uv run python documentation/live-decisions/mockup/dump_payloads.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from nflengine.app.readers.live import LiveService
from nflengine.app.server import AppSettings, create_app
from nflengine.live.espn import EspnClient
from nflengine.live.replayfeed import ReplayFeed
from nflengine.paths import ensure_data_root

BASE = "http://127.0.0.1:8765"


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


# (name, event to replay, replayed time, ESPN lag in s, the game the panel shows, note)
MOMENTS = [
    ("board", "401872966", utc(2026, 10, 4, 19, 57, 45), 10, "401872966",
     "Sunday 3:57 PM ET: the 1 PM games late in the 4th, the late games not started"),
    ("fourth_close", "401872966", utc(2026, 10, 4, 19, 57, 45), 10, "401872966",
     "NYG 4th & 5 at ARI 34, 2:11 left, up 3: a close call"),
    ("fourth_tossup", "401872970", utc(2026, 10, 4, 19, 41, 25), 10, "401872970",
     "PHI 4th & Goal at LA 2, 7:31 left, up 10: a toss-up"),
    ("third", "401872970", utc(2026, 10, 4, 19, 58, 0), 10, "401872970",
     "PHI 3rd & 4 at PHI 46, 3:10 left, up 3"),
    ("behind", "401872966", utc(2026, 10, 4, 19, 57, 52), 45, "401872966",
     "ESPN 45 s behind: it still shows NYG's 3rd & 5 while the TV is at the 4th down"),
    ("none", "401872971", utc(2026, 10, 4, 19, 57, 45), 10, "401872971",
     "NE at BUF, 2nd & 5: not a 3rd or 4th down"),
    ("between", "401872978", utc(2026, 10, 4, 23, 50, 0), 10, None,
     "Sunday 7:50 PM ET: the afternoon games final, SNF not started"),
    ("final", "401872979", utc(2026, 10, 6, 4, 0, 0), 10, None,
     "Tuesday 12:00 AM ET: every week-4 game final"),
]  # fmt: skip


def app_for(feed: ReplayFeed, paths_root: Any) -> tuple[TestClient, LiveService]:
    client = EspnClient(
        transport=feed.transport(), now=feed.now, min_interval=0.0, host_interval=0.0
    )
    live = LiveService(client=client, replay=feed, background=False)
    settings = AppSettings(live=live, live_replay=feed, now=feed.now)
    return TestClient(create_app(settings), base_url=BASE), live


def get(tc: TestClient, path: str) -> Any:
    r = tc.get(path)
    body = r.json()
    if r.status_code != 200:
        return {"status": r.status_code, **body}
    return body


def moment(name: str, event: str, at: datetime, lag: float, game: str | None, note: str) -> dict:
    paths = ensure_data_root(create_dirs=False)
    folder = paths.live_data / "summaries"
    clock = {"t": 0.0}  # the replayed time stands still unless a moment moves it
    feed = ReplayFeed.from_folder(
        folder, event, at=at, lag_s=lag, speed=1.0, monotonic=lambda: clock["t"]
    )
    season, week = feed.season, feed.week()
    tc, live = app_for(feed, paths.root)
    out: dict[str, Any] = {"note": note, "at": at.isoformat(), "event": game, "lag_s": lag}
    with tc:
        out["games"] = get(tc, f"/api/live/{season}/{week}/games")
        if game is not None:
            live._fetch_summary(game)  # the snap times a second check would have (README §4)
            out["call"] = get(tc, f"/api/live/{season}/{week}/games/{game}/call")
            off = (out["call"] or {}).get("offense")
            if off:
                out["context"] = get(
                    tc, f"/api/live/{season}/{week}/games/{game}/context?offense={off}"
                )
            if name == "fourth_close":
                # a repeat check (no new play) and a stale feed (ESPN fails after a good answer)
                clock["t"] += 5.0  # five seconds later: no new play yet (the kick posts later)
                out["call_repeat"] = get(tc, f"/api/live/{season}/{week}/games/{game}/call")
                live.client()._http._transport = httpx.MockTransport(  # noqa: SLF001
                    lambda req: httpx.Response(503)
                )
                out["call_stale"] = get(tc, f"/api/live/{season}/{week}/games/{game}/call")
    return out


def past_week(season: int, week: int) -> dict:
    """A week before Game day's: no ESPN call (LD03 fills it)."""
    paths = ensure_data_root(create_dirs=False)
    feed = ReplayFeed.from_folder(paths.live_data / "summaries", "401872966")
    tc, _ = app_for(feed, paths.root)
    with tc:
        return {"note": f"{season} week {week}: a past week", "games": get(
            tc, f"/api/live/{season}/{week}/games"
        )}  # fmt: skip


def before_week() -> dict:
    """Thursday afternoon of week 5, before its first kickoff: ESPN's real week-5 scoreboard
    (saved by the LD01 probe on 2026-10-10) with every game set back to "pre" (Thursday's
    TB at DAL had been played by then: the only edit, labelled in the mockup)."""
    import copy

    paths = ensure_data_root(create_dirs=False)
    board = json.loads((paths.live_data / "probe" / "scoreboard_current.json").read_text("utf-8"))
    board = copy.deepcopy(board)
    for e in board.get("events") or []:
        for c in e.get("competitions") or []:
            c.pop("situation", None)
            for t in c.get("competitors") or []:
                t["score"] = "0"
            st = {"clock": 0.0, "displayClock": "0:00", "period": 0,
                  "type": {"state": "pre", "completed": False, "description": "Scheduled",
                           "detail": "Scheduled", "shortDetail": "Scheduled"}}  # fmt: skip
            c["status"] = st
            e["status"] = st
    at = utc(2026, 10, 8, 20, 0, 0)
    client = EspnClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=board)),
        now=lambda: at,
        min_interval=0.0,
        host_interval=0.0,
    )
    live = LiveService(client=client, now=lambda: at, background=False)
    tc = TestClient(create_app(AppSettings(live=live, now=lambda: at)), base_url=BASE)
    with tc:
        return {
            "note": "Thursday 4:00 PM ET, week 5: the slate before the first kickoff",
            "at": at.isoformat(),
            "games": get(tc, "/api/live/2026/5/games"),
        }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    paths = ensure_data_root(create_dirs=False)
    out_path = args.out or paths.cache / "live-decisions-mockup" / "payloads.json"
    data: dict[str, Any] = {
        "built_at": datetime.now(UTC).isoformat(),
        "source": "2026 week 4 replayed from ESPN's saved play logs (nfl app --live-replay)",
        "moments": {},
    }
    for name, event, at, lag, game, note in MOMENTS:
        print(f"{name}: {note}")
        data["moments"][name] = moment(name, event, at, lag, game, note)
    data["moments"]["before"] = before_week()
    data["moments"]["past"] = past_week(2026, 3)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"wrote {out_path.name} ({out_path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
