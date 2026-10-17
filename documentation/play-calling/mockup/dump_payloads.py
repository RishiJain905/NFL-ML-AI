"""The Play calling mockup's data: the real app's answers on the real tables (PC01).

Runs the control room's API in-process (`create_app` + `TestClient`; nothing is served and
nothing is fetched) on the play-calling tables `nfl playcalling build` wrote (PC00), and saves
every answer the Play calling pages and the Play calls week tab read into
`{NFL_DATA_ROOT}/cache/play-calling-mockup/payloads.json` (or `--out`). Read-only otherwise.

    PYTHONIOENCODING=utf-8 uv run python documentation/play-calling/mockup/dump_payloads.py

The layout (README.md in this folder): `meta`, `weeks`, `team_info` (the shell);
`teams["<S>"]` (the grid); `team["<S>"]["<TEAM>"]["<side>"]` (a team page);
`history["<TEAM>"]["<side>"]`; `play_calls["<S>_<W>"]` (the week tab); `states` (the empty
answers: `not_built.{teams,team,week}`, `not_yet.week`, `unknown_team`).

PC02 (the forecast): `play_calls["2026_5"]` is a forecast week not graded yet (the clock's week),
`["2026_4"]` a graded week (ATL at NO waiting for FTN), `["2026_1"]` graded on last season's
rates, `["2026_6"]` not yet, `["2025_10"]` no forecast (descriptive, with `forecast_hint`). The
team pages carry `next_game.forecast` (week 5) or `next_game.forecast_hint` (KC: a week-5 bye,
week 6 not forecast yet) and `forecast_record` (weeks 1-4). D132: `preflight` (GET
/api/preflight at NOW) for the current week's Update play calling card.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from nflengine.app.server import AppSettings, create_app
from nflengine.paths import ensure_data_root

BASE = "http://127.0.0.1:8765"
SEASON = 2026
# the team pages in the mockup: strong identities on both sides of the ball, plus a 2025 view
TEAMS_NOW = ("MIN", "KC", "CIN", "LA", "BAL", "PHI", "NYG", "DAL", "ATL")
TEAMS_2025 = ("KC", "MIN", "LA", "BAL")
WEEKS = ((2026, 5), (2026, 4), (2026, 1), (2026, 6), (2025, 10))
NOW = datetime(2026, 10, 10, 16, 0, tzinfo=UTC)  # Saturday noon ET: week 5 next (KC: bye -> week 6)


def get(tc: TestClient, path: str) -> Any:
    r = tc.get(path)
    body = r.json()
    if r.status_code != 200:
        return {"http_status": r.status_code, **body}
    return body


def _preflight(pf: dict[str, Any]) -> dict[str, Any]:
    """Only what the card reads (the week and the gate): the checks stay out of the mockup."""
    return {k: pf.get(k) for k in ("checked_at", "mode", "season", "week", "playcalling")}


def _play_browser(tc: TestClient, p: dict[str, Any]) -> None:
    """PC03: the play browser's lists and diagrams. `plays["<S>"]["<TEAM>"]["<side>"]["<week>"]`
    (`newest` = no week asked); `plays_filtered` one filtered list; `diagrams["<game>/<play>"]`
    every play of the dumped 2026 offense weeks plus a handful from 2025 and 2019 (participation:
    personnel, coverage, the target's route)."""
    p["plays"], p["diagrams"] = {}, {}
    lists = (
        (SEASON, "MIN", "offense", (None, 1, 2, 3, 4)),
        (SEASON, "MIN", "defense", (None,)),
        (SEASON, "KC", "offense", (None, 3)),
        (SEASON, "KC", "defense", (None,)),
        (2025, "KC", "offense", (5,)),
        (2025, "MIN", "offense", (8,)),
        (2019, "BAL", "offense", (6,)),
    )
    for season, team, side, weeks in lists:
        for w in weeks:
            q = f"?season={season}&side={side}" + (f"&week={w}" if w else "")
            ans = get(tc, f"/api/playcalling/teams/{team}/plays{q}")
            p["plays"].setdefault(str(season), {}).setdefault(team, {}).setdefault(side, {})[
                "newest" if w is None else str(w)
            ] = ans
    p["plays_filtered"] = get(
        tc,
        f"/api/playcalling/teams/KC/plays?season={SEASON}&side=defense&week=2&type=pass&result=first_down",
    )
    want: list[tuple[str, int]] = []
    # every play of the weeks the mockup opens on (the newest, as the app does) and of week 3
    for team, side, w in (
        ("MIN", "offense", "3"),
        ("KC", "offense", "3"),
        ("MIN", "offense", "newest"),
        ("KC", "offense", "newest"),
        ("MIN", "defense", "newest"),
        ("KC", "defense", "newest"),
    ):
        want += [
            (x["game_id"], x["play_id"]) for x in p["plays"][str(SEASON)][team][side][w]["plays"]
        ]
    for season, team, w, n in (
        (2025, "KC", "5", 14),
        (2025, "MIN", "8", 10),
        (2019, "BAL", "6", 6),
    ):
        want += [
            (x["game_id"], x["play_id"])
            for x in p["plays"][str(season)][team]["offense"][w]["plays"][:n]
        ]
    for g, pid in dict.fromkeys(want):
        p["diagrams"][f"{g}/{pid}"] = get(tc, f"/api/playcalling/plays/{g}/{pid}/diagram")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    paths = ensure_data_root(create_dirs=False)
    out = args.out or paths.cache / "play-calling-mockup" / "payloads.json"

    t0 = time.perf_counter()
    tc = TestClient(create_app(AppSettings(now=lambda: NOW)), base_url=BASE)
    p: dict[str, Any] = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "the control room API in-process on playcalling/<S>/ (PC00 tables)",
        "now": NOW.isoformat(),
        "meta": get(tc, "/api/meta"),
        "weeks": get(tc, "/api/weeks"),
        "team_info": get(tc, "/api/team-info"),
        # D132: the Update play calling card reads pre-flight's `playcalling` gate (at NOW)
        "preflight": _preflight(get(tc, "/api/preflight")),
        "teams": {str(s): get(tc, f"/api/playcalling/teams?season={s}") for s in (2026, 2025)},
        "team": {},
        "history": {},
        "play_calls": {},
    }
    for season, teams in ((SEASON, TEAMS_NOW), (2025, TEAMS_2025)):
        p["team"][str(season)] = {
            team: {
                side: get(tc, f"/api/playcalling/teams/{team}?season={season}&side={side}")
                for side in ("offense", "defense")
            }
            for team in teams
        }
    p["history"] = {
        team: {
            side: get(tc, f"/api/playcalling/teams/{team}/history?side={side}")
            for side in ("offense", "defense")
        }
        for team in TEAMS_NOW
    }
    for s, w in WEEKS:
        p["play_calls"][f"{s}_{w}"] = get(tc, f"/api/weeks/{s}/{w}/play-calls")
    p["states"] = {
        "not_built": {
            "teams": get(tc, "/api/playcalling/teams?season=2015"),
            "team": get(tc, "/api/playcalling/teams/KC?season=2015&side=offense"),
            "week": get(tc, "/api/weeks/2015/5/play-calls"),
            "plays": get(tc, "/api/playcalling/teams/KC/plays?season=2015&side=offense"),
        },
        "not_yet": {"week": p["play_calls"][f"{SEASON}_6"]},
        "unknown_team": get(tc, "/api/playcalling/teams/XYZ?side=offense"),
        "no_week": get(tc, f"/api/playcalling/teams/KC/plays?season={SEASON}&week=5"),
        "no_play": get(tc, f"/api/playcalling/plays/{SEASON}_03_MIN_TB/9999/diagram"),
    }
    _play_browser(tc, p)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(p, ensure_ascii=False, separators=(",", ":"))
    out.write_text(text, encoding="utf-8")
    sizes = {k: len(json.dumps(v)) for k, v in p.items() if isinstance(v, dict)}
    print(f"wrote {out.name}: {len(text) / 1e6:.2f} MB in {time.perf_counter() - t0:.1f} s")
    print("sizes (chars):", sizes)


if __name__ == "__main__":
    main()
