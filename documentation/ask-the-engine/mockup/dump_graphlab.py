"""Write the Connections + Graph lab mockup's payloads: the real app's answers, in-process (AE03).

Runs the control room (`create_app` + `TestClient`, no server, no browser) on the real data
root with the history graph up, and saves every answer the two new parts of Explore -> Ask
the Engine need, so the mockup shows only real numbers and its JavaScript reads the same
shapes as the React page (the AE03 block of web/src/api/types.ts):

- `status`: `GET /api/ask/graph-lab` (the history graph, the person links, the lab runs);
- `people.<query>`: `GET /api/ask/people?q=` for a few names (and one that matches nobody);
- `connections.<key>`: `GET /api/ask/connections?a=&b=&k=&mode=` for real pairs, fewest and
  strongest, one and four chains; `connection_errors.<key>`: the refusals (the same person, an
  unknown id, the history graph down, another search running);
- `lab.neighbours.<player>`, `lab.map`, `lab.families` (+ a person's family), `lab.chemistry`
  (100 and 300 targets), `lab.benchmark` (404 until `nfl history-graph bench-paths` has run).

No model call, nothing written but `{NFL_DATA_ROOT}/cache/ask-mockup/graphlab.json`; no data
goes into git.

    $env:PYTHONIOENCODING = "utf-8"
    uv run python documentation/ask-the-engine/mockup/dump_graphlab.py
"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from nflengine.app.ask_service import AskService
from nflengine.app.connections_service import ConnectionsService
from nflengine.app.server import AppSettings, create_app
from nflengine.app.status import ServiceStatus
from nflengine.paths import ensure_data_root

BASE = "http://127.0.0.1:8765"
PEOPLE = ["mahomes", "kelce", "andy reid", "tom brady", "josh allen", "belichick", "zzqx"]
MAHOMES, KELCE, BRADY, RODGERS = (
    "player:00-0033873",
    "player:00-0030506",
    "player:00-0019596",
    "player:00-0023459",
)
JEFFERSON, ALLEN, CAM_WARD = "player:00-0036322", "player:00-0034857", "player:00-0040676"
SHEPLEY = "player:00-0034229"  # 99 snaps: not in the map (the neighbours job keeps 100+)
REID, BELICHICK = "coach:andy-reid", "coach:bill-belichick"
PAIRS = {
    "teammates": (MAHOMES, KELCE, 1, "fewest"),
    "teammates_more": (MAHOMES, KELCE, 4, "fewest"),
    "two_steps": (BRADY, MAHOMES, 1, "fewest"),
    "two_steps_more": (BRADY, MAHOMES, 4, "fewest"),
    "player_coach": (MAHOMES, BELICHICK, 4, "fewest"),
    "coaches": (REID, BELICHICK, 1, "fewest"),
    "rookie_coach": (CAM_WARD, BELICHICK, 4, "fewest"),
    "brady_rodgers": (BRADY, RODGERS, 1, "fewest"),
    "brady_rodgers_strongest": (BRADY, RODGERS, 1, "strongest"),
    "brady_rodgers_strongest_more": (BRADY, RODGERS, 4, "strongest"),
    "teammates_strongest_more": (MAHOMES, KELCE, 4, "strongest"),
}


class Down:
    def state(self) -> str:
        return "down"


def main() -> None:
    paths = ensure_data_root(create_dirs=False)
    out_dir = paths.cache / "ask-mockup"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "graphlab.json"
    token = "mockup-token"
    real = ServiceStatus()
    real.check_now()
    ask = AskService(background=False)
    conn = ConnectionsService()
    c = TestClient(create_app(AppSettings(token=token, services=real, ask=ask,
                                          connections=conn)), base_url=BASE)  # fmt: skip
    out: dict = {"people": {}, "connections": {}, "connection_errors": {}, "lab": {}}

    def get(path: str, **params):
        t = time.monotonic()
        r = c.get(path, params=params)
        return {"http_status": r.status_code, "ms": round((time.monotonic() - t) * 1000),
                "body": r.json()}  # fmt: skip

    for key, p in (("meta", "/api/meta"), ("weeks", "/api/weeks"), ("team_info", "/api/team-info")):
        out[key] = c.get(p).json()
    out["status"] = get("/api/ask/graph-lab")
    for q in PEOPLE:
        out["people"][q] = get("/api/ask/people", q=q)
    for key, (a, b, k, mode) in PAIRS.items():
        out["connections"][key] = get("/api/ask/connections", a=a, b=b, k=k, mode=mode)
        res = out["connections"][key]
        paths_ = (res["body"].get("paths") or []) if res["http_status"] == 200 else []
        print(f"{key:30} {res['http_status']} {res['ms']:5d} ms, "
              f"{len(paths_)} chains, hops {[p['hops'] for p in paths_]}")  # fmt: skip
    errs = out["connection_errors"]
    errs["same_person"] = get("/api/ask/connections", a=MAHOMES, b=MAHOMES)
    errs["bad_id"] = get("/api/ask/connections", a="player:12", b=KELCE)
    errs["not_found"] = get("/api/ask/connections", a="player:00-0000001", b=KELCE)
    down = TestClient(create_app(AppSettings(token=token, services=real, ask=ask,
                                             connections=ConnectionsService(check=Down()))),
                      base_url=BASE)  # fmt: skip
    r = down.get("/api/ask/connections", params={"a": MAHOMES, "b": KELCE})
    errs["history_down"] = {"http_status": r.status_code, "body": r.json()}
    busy = ConnectionsService(max_inflight=1)
    busy._slots.acquire()  # another search running
    bc = TestClient(create_app(AppSettings(token=token, services=real, ask=ask,
                                           connections=busy)), base_url=BASE)  # fmt: skip
    r = bc.get("/api/ask/connections", params={"a": MAHOMES, "b": KELCE})
    errs["busy"] = {"http_status": r.status_code, "body": r.json()}

    lab = out["lab"]
    lab["neighbours"] = {
        name: get("/api/ask/lab/neighbours", player=pid)
        for name, pid in (
            ("kelce", KELCE),
            ("mahomes", MAHOMES),
            ("jefferson", JEFFERSON),
            ("josh_allen", ALLEN),
            ("cam_ward", CAM_WARD),
            ("shepley", SHEPLEY),
        )  # fmt: skip
    }
    lab["map"] = get("/api/ask/lab/neighbours/map")
    lab["families"] = get("/api/ask/lab/families")
    lab["family_of_mahomes"] = get("/api/ask/lab/families", person=MAHOMES)
    lab["family_of_reid"] = get("/api/ask/lab/families", person=REID)
    lab["chemistry"] = get("/api/ask/lab/chemistry")
    lab["chemistry_300"] = get("/api/ask/lab/chemistry", min_targets=300)
    lab["benchmark"] = get("/api/ask/lab/benchmark")
    ask.close()
    conn.close()
    out_file.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print("wrote", out_file.relative_to(paths.root))


if __name__ == "__main__":
    main()
