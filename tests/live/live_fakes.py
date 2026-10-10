"""Shared helpers for the LD01 live-feed tests: the saved ESPN fixtures, hand-built game contexts,
a fake clock and a network guard. A plain module, not a conftest (repo rule); the test files do
`from live_fakes import ...` (pytest puts this folder on `sys.path`).

The fixtures in `tests/fixtures/live/` are trimmed copies of real ESPN JSON (see the README
there). The contexts below copy the pre-game facts from curated `games` as literals, so no test
reads the data drive. Nothing here reaches the network.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from nflengine.live.state import GameContext

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "live"
UTC_T0 = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)


def fixture(name: str) -> dict[str, Any]:
    """One saved ESPN answer (a fresh copy every time, so a test can edit it)."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- game contexts: curated `games` values for the fixture games (nflverse's home / away) ------
def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(UTC)


CONTEXTS: dict[str, dict[str, Any]] = {
    # 2026_04_TEN_BAL: BAL a 11.5-point favourite (nflverse: + = home favoured)
    "ten_bal": dict(
        home="BAL",
        away="TEN",
        game_id="2026_04_TEN_BAL",
        week=4,
        home_spread=11.5,
        total=42.5,
        roof="outdoors",
        wind=6.0,
        temp=62.0,
        kickoff_utc=_utc("2026-10-04T17:00:00+00:00"),
    ),
    # 2026_04_LA_PHI
    "la_phi": dict(
        home="PHI",
        away="LA",
        game_id="2026_04_LA_PHI",
        week=4,
        home_spread=-3.5,
        total=42.5,
        roof="outdoors",
        wind=9.0,
        temp=62.0,
        kickoff_utc=_utc("2026-10-04T17:00:00+00:00"),
    ),
    # 2026_01_SF_LA: Melbourne, a neutral site, roof "dome"
    "sf_la": dict(
        home="LA",
        away="SF",
        game_id="2026_01_SF_LA",
        week=1,
        home_spread=3.5,
        total=47.5,
        roof="dome",
        neutral_site=True,
        kickoff_utc=_utc("2026-09-11T00:35:00+00:00"),
    ),
    # 2026_03_BAL_DAL: neutral site, DAL the nominal home team, roof "open"
    "bal_dal": dict(
        home="DAL",
        away="BAL",
        game_id="2026_03_BAL_DAL",
        week=3,
        home_spread=-3.0,
        total=53.5,
        roof="open",
        neutral_site=True,
        wind=7.0,
        temp=77.0,
        kickoff_utc=_utc("2026-09-27T20:25:00+00:00"),
    ),
    # 2026_01_DAL_NYG
    "dal_nyg": dict(
        home="NYG",
        away="DAL",
        game_id="2026_01_DAL_NYG",
        week=1,
        home_spread=-3.0,
        total=47.5,
        roof="outdoors",
        wind=7.0,
        temp=80.0,
        kickoff_utc=_utc("2026-09-14T00:20:00+00:00"),
    ),
    # 2026_04_ARI_NYG
    "ari_nyg": dict(
        home="NYG",
        away="ARI",
        game_id="2026_04_ARI_NYG",
        week=4,
        home_spread=-2.5,
        total=44.5,
        roof="outdoors",
        wind=9.0,
        temp=63.0,
        kickoff_utc=_utc("2026-10-04T17:00:00+00:00"),
    ),
    # 2026_03_ATL_GB
    "atl_gb": dict(
        home="GB",
        away="ATL",
        game_id="2026_03_ATL_GB",
        week=3,
        home_spread=4.5,
        total=43.5,
        roof="outdoors",
        wind=5.0,
        temp=61.0,
        kickoff_utc=_utc("2026-09-25T00:15:00+00:00"),
    ),
    # 2026_01_TB_CIN
    "tb_cin": dict(
        home="CIN",
        away="TB",
        game_id="2026_01_TB_CIN",
        week=1,
        home_spread=3.5,
        total=50.5,
        roof="outdoors",
        wind=7.0,
        temp=85.0,
        kickoff_utc=_utc("2026-09-13T17:00:00+00:00"),
    ),
    # 2026_05_TB_DAL: roof is empty in nflverse until the game is over; AT&T Stadium is closed
    "tb_dal": dict(
        home="DAL",
        away="TB",
        game_id="2026_05_TB_DAL",
        week=5,
        home_spread=8.5,
        total=47.5,
        roof="closed",
        kickoff_utc=_utc("2026-10-09T00:15:00+00:00"),
    ),
    # 2026_02_IND_KC: went to overtime (33-30)
    "ind_kc": dict(
        home="KC",
        away="IND",
        game_id="2026_02_IND_KC",
        week=2,
        home_spread=6.0,
        total=46.5,
        roof="outdoors",
        wind=10.0,
        temp=71.0,
        kickoff_utc=_utc("2026-09-21T00:20:00+00:00"),
    ),
    # 2026_05_PHI_JAX: London, neutral site, the pre-game slate
    "phi_jax": dict(
        home="JAX",
        away="PHI",
        game_id="2026_05_PHI_JAX",
        week=5,
        home_spread=7.0,
        total=42.5,
        roof="outdoors",
        neutral_site=True,
        kickoff_utc=_utc("2026-10-11T13:30:00+00:00"),
    ),
}


def ctx(name: str, **changes: Any) -> GameContext:
    """A `GameContext` for one fixture game (season 2026, regular season) with edits."""
    kw = {"season": 2026, **CONTEXTS[name], **changes}
    return GameContext(**kw)


# --- summaries cut at a play ------------------------------------------------------------------
def summary_upto(summary: dict[str, Any], play_id: str) -> dict[str, Any]:
    """The summary as ESPN would have served it right after `play_id`: later plays dropped, the
    unfinished drive is `current`."""
    out = copy.deepcopy(summary)
    plays = [p for d in out["drives"]["previous"] for p in d["plays"]]
    limit = int(next(p for p in plays if p["id"] == play_id)["sequenceNumber"])
    drives = []
    for d in out["drives"]["previous"]:
        d["plays"] = [p for p in d["plays"] if int(p["sequenceNumber"]) <= limit]
        if d["plays"]:
            drives.append(d)
    out["drives"] = {"previous": drives[:-1], "current": drives[-1]}
    comp = out["header"]["competitions"][0]
    comp["status"] = {"type": {"state": "in", "completed": False}}
    return out


# --- a fake clock for the ESPN client ---------------------------------------------------------
class FakeClock:
    """Monotonic seconds, a `sleep` that advances them and a wall clock: the three clocks
    `EspnClient` takes. Nothing waits for real."""

    def __init__(self, start: float = 1000.0):
        self.t = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def now(self) -> datetime:
        return UTC_T0 + timedelta(seconds=self.t - 1000.0)

    def advance(self, seconds: float) -> None:
        self.t += seconds


# --- no test touches the network ----------------------------------------------------------------
@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Importing this fixture into a test module turns it on for every test there: httpx's real
    transport raises, so a client built without a fake transport fails loudly."""

    def refuse(self, request):
        raise AssertionError(f"a test tried to reach the network: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
