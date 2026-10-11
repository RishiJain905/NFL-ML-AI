"""Rebuild the Play calling mockup (a single HTML file) from this folder's sources.

The mockup is the visual spec for PC01's pages
(documentation/play-calling/PC01-play-calling-pages.md): Explore -> Play calling (the teams
grid, a team page) and the Play calls week tab. It is drawn inside the control room's shell,
so it reuses the control room mockup's styles.css unchanged
and adds playcalling.css on top. The data it shows is the reader's answers (the shapes in
web/src/api/types.ts, PC01 block) in a payloads JSON, plus the team colours from the curated
tables; nothing is committed. The payloads come from dump_payloads.py (the real app API).

    uv run python documentation/play-calling/mockup/build_mockup.py [--payloads PATH]

Reads {NFL_DATA_ROOT}/cache/play-calling-mockup/payloads.json unless --payloads is given.
Writes {NFL_DATA_ROOT}/cache/play-calling-mockup/play-calling.html. Read-only otherwise.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from nflengine.paths import ensure_data_root

HERE = Path(__file__).parent
CONTROL_ROOM = HERE.parents[1] / "control-room" / "mockup"
# what each answer must carry (the PC01 types); the build stops and names anything missing
TEAMS_NEEDS = ("status", "season", "as_of_week", "min_n", "columns", "teams")
TEAM_NEEDS = (
    "status",
    "team",
    "side",
    "summary",
    "identity",
    "situations",
    "field",
    "runs",
    "weekly",
)
HISTORY_NEEDS = ("status", "seasons", "source_note", "groups")
WEEK_NEEDS = ("status", "season", "week", "note", "metrics", "games", "shifts")


def clean(o: Any) -> Any:
    """NaN / inf -> None, recursively (browsers reject NaN in JSON)."""
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


def _need(missing: list[str], where: str, got: Any, keys: tuple[str, ...]) -> None:
    if not isinstance(got, dict):
        missing.append(where)
        return
    missing += [f"{where}.{k}" for k in keys if k not in got]


def check_payloads(p: dict[str, Any]) -> None:
    """Fail early, naming what's missing, rather than drawing a half-empty page."""
    missing: list[str] = []
    for top in ("weeks", "teams", "team", "history", "play_calls"):
        if not p.get(top):
            missing.append(top)
    for season, ans in (p.get("teams") or {}).items():
        _need(missing, f"teams.{season}", ans, TEAMS_NEEDS)
    for season, teams in (p.get("team") or {}).items():
        for team, sides in teams.items():
            for side in ("offense", "defense"):
                _need(missing, f"team.{season}.{team}.{side}", (sides or {}).get(side), TEAM_NEEDS)
    for team, sides in (p.get("history") or {}).items():
        for side in ("offense", "defense"):
            _need(missing, f"history.{team}.{side}", (sides or {}).get(side), HISTORY_NEEDS)
    for key, ans in (p.get("play_calls") or {}).items():
        _need(missing, f"play_calls.{key}", ans, WEEK_NEEDS)
    if missing:
        raise SystemExit(f"payloads: missing {', '.join(missing)}")


def team_colours(root: Path) -> dict[str, Any]:
    teams = pd.read_parquet(root / "teams.parquet")
    return {
        t.team: {"name": t.team_name, "nick": t.team_nick, "color": t.team_color}
        for t in teams.itertuples()
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--payloads", type=Path, help="the reader's answers (default: the cache's)")
    args = ap.parse_args()
    paths = ensure_data_root(create_dirs=False)
    out_dir = paths.cache / "play-calling-mockup"
    src = args.payloads or out_dir / "payloads.json"
    payloads = json.loads(src.read_text(encoding="utf-8"))
    check_payloads(payloads)
    data = {"payloads": payloads, "teams": team_colours(paths.curated)}
    blob = json.dumps(clean(data), allow_nan=False, ensure_ascii=False, default=str)
    html = (HERE / "template.html").read_text(encoding="utf-8")
    html = (
        html.replace("{{CSS}}", (CONTROL_ROOM / "styles.css").read_text(encoding="utf-8"))
        .replace("{{PCCSS}}", (HERE / "playcalling.css").read_text(encoding="utf-8"))
        .replace("{{JS}}", (HERE / "playcalling.js").read_text(encoding="utf-8"))
        .replace("{{DATA}}", blob.replace("</", "<\\/"))
    )
    dest = out_dir / "play-calling.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
    print(f"wrote {dest} ({len(html):,} bytes) from {src.name}")


if __name__ == "__main__":
    main()
