"""The Outcomes tab mockup's data: the real app's answers for 2026 week 10 (FL02, D158).

Runs the control room's API in-process (`create_app` + `TestClient`, no socket) on the real
data root, read-only, and saves a **trimmed** copy of what the Outcomes tab reads:

- `outcomes.<target key>`: `GET /api/weeks/2026/10/outcomes?target=...` for four stats
  (receiving yards, carries, passing yards, tackles), each with its rows cut to the top
  `--rows` projections plus the players the curves below are about;
- `curves.<player id>/<target key>`: `GET .../outcomes/curve?player=&target=` for a handful
  of players (a star receiver, a low-projection tight end, a lead back's carries, a QB) and
  the first rows of each stat, so most rows open in the mockup;
- `states`: the real empty answer of week 11 (`none`).

The Availability tab's data goes to `--availability-out` (default `payloads_availability.json`):
`base` = `GET /api/weeks/2026/10/availability` (the week: tiles, the might-sit list, the role
changes; no team's players), and `team.<TEAM>` = the `team` and `players` of `...?team=` for a
few teams (MIN: Kyler
Murray's team; DET: Jack Campbell on a reserve list; WAS: the Thursday game, already played).

Nothing is written but those two files.

    PYTHONIOENCODING=utf-8 uv run python documentation/forecast-lab/mockup/dump_payloads.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from nflengine.app.server import AppSettings, create_app

HERE = Path(__file__).parent
BASE = "http://127.0.0.1:8765"  # in-process only: the Host check wants the app's port
SEASON, WEEK = 2026, 10
TARGETS = ("rec_yds-wrte", "carries-rb", "pass_yds-qb", "tackles-lbs")


def get(tc: TestClient, path: str) -> Any:
    r = tc.get(path)
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "payloads.json")
    ap.add_argument("--availability-out", type=Path, default=HERE / "payloads_availability.json")
    ap.add_argument("--teams", default="MIN,DET,WAS", help="teams whose players are dumped")
    ap.add_argument("--rows", type=int, default=24, help="rows kept per stat")
    ap.add_argument("--curves", type=int, default=8, help="curves dumped per stat")
    a = ap.parse_args()

    tc = TestClient(create_app(AppSettings()), base_url=BASE)
    w = f"/api/weeks/{SEASON}/{WEEK}"
    full = {t: get(tc, f"{w}/outcomes?target={t}") for t in TARGETS}

    # the players the mockup is about: picked from the real rows
    rec = full["rec_yds-wrte"]["rows"]
    star = max(rec, key=lambda r: r["p50"] or 0)
    tes = [r for r in rec if r["position"] == "TE" and (r["p50"] or 0) > 0]
    low_te = sorted(tes, key=lambda r: r["p50"] or 0)[len(tes) // 4] if tes else None
    back = max(full["carries-rb"]["rows"], key=lambda r: r["p50"] or 0)
    qb = max(full["pass_yds-qb"]["rows"], key=lambda r: r["p50"] or 0)
    featured = [
        ("rec_yds-wrte", star, "a star receiver"),
        ("rec_yds-wrte", low_te, "a low-projection tight end"),
        ("carries-rb", back, "a lead back's carries"),
        ("pass_yds-qb", qb, "a quarterback"),
    ]

    want: dict[str, set[str]] = {t: set() for t in TARGETS}
    for t, r, _ in featured:
        if r is not None:
            want[t].add(r["player_id"])
    curves: dict[str, Any] = {}
    outcomes: dict[str, Any] = {}
    for t, body in full.items():
        rows = body["rows"]
        keep = rows[: a.rows] + [r for r in rows[a.rows :] if r["player_id"] in want[t]]
        # gaps cover the whole week: keep them (they are few) but drop their other stats' rows
        outcomes[t] = {**body, "rows": keep}
        for r in rows[: a.curves]:
            want[t].add(r["player_id"])
        for pid in sorted(want[t]):
            c = get(tc, f"{w}/outcomes/curve?player={pid}&target={t}")
            curves[f"{pid}/{t}"] = c
    states = {"outcomes_none": get(tc, f"/api/weeks/{SEASON}/{WEEK + 1}/outcomes")}
    doc = {
        "about": "Real answers of the control room for 2026 week 10, trimmed (dump_payloads.py)",
        "season": SEASON,
        "week": WEEK,
        "featured": [
            {"target_key": t, "player_id": r["player_id"], "player": r["player"], "why": why}
            for t, r, why in featured
            if r is not None
        ],
        "outcomes": outcomes,
        "curves": curves,
        "states": states,
    }
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    assert "nfl-ml-data" not in text, "a data-root path got into the payloads"
    a.out.write_text(text, encoding="utf-8")
    print(f"wrote {a.out} ({len(text) / 1024:.0f} KB): {len(curves)} curves, "
          f"featured {[f['player'] for f in doc['featured']]}")  # fmt: skip

    teams = [t.strip() for t in a.teams.split(",") if t.strip()]
    av = {
        "about": "Real Availability answers for 2026 week 10 (dump_payloads.py)",
        "season": SEASON,
        "week": WEEK,
        "base": get(tc, f"{w}/availability"),
        # a team's answer repeats the week's parts: keep its own (`team`, `players`)
        "team": {
            t: {
                k: v
                for k, v in get(tc, f"{w}/availability?team={t}").items()
                if k in ("team", "players")
            }
            for t in teams
        },
    }
    text = json.dumps(av, ensure_ascii=False, separators=(",", ":"))
    assert "nfl-ml-data" not in text, "a data-root path got into the payloads"
    a.availability_out.write_text(text, encoding="utf-8")
    print(f"wrote {a.availability_out} ({len(text) / 1024:.0f} KB): teams {teams}")


if __name__ == "__main__":
    main()
