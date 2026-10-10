"""Rebuild the Game day mockup (a single HTML file) from this folder's sources.

The mockup is the visual spec for LD02's Game day tab (documentation/live-decisions/
LD02-game-day-tab.md). It is drawn as a tab of the control room, so it reuses the control
room mockup's styles.css unchanged and adds gameday.css on top. The data it shows is the
reader's answers (the shapes in web/src/api/types.ts, LD02 and LD03 blocks) in a payloads JSON, plus
team colours and stadiums from the curated tables; nothing is committed. The payloads come
from dump_payloads.py (the real app API on ESPN's replayed week-4 play logs, and the decision
reviews of finished weeks from curated plays, LD03).

    uv run python documentation/live-decisions/mockup/build_mockup.py [--payloads PATH]

Reads {NFL_DATA_ROOT}/cache/live-decisions-mockup/payloads.json unless --payloads is given.
Writes {NFL_DATA_ROOT}/cache/live-decisions-mockup/game-day.html. Read-only otherwise.
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
SEASON = 2026
INCLUDE = "/* @review.js */"  # where gameday.js takes review.js (LD03)
# what each moment must carry: the board, and for a play its call and the offense's context
NEEDS = {
    "board": ("games",),
    "fourth_close": ("games", "call", "context", "call_repeat", "call_stale"),
    "fourth_tossup": ("games", "call", "context"),
    "third": ("games", "call", "context"),
    "behind": ("games", "call", "context"),
    "none": ("games", "call"),
    "between": ("games",),
    "final": ("games",),
    "before": ("games",),
    "past": ("games",),
}
# LD03: the decision reviews (dump_payloads.py's `review` block): a week's and its season's answer
REVIEWS = ("2026_4", "2026_1", "2026_5", "2025_18", "2025_22")
REVIEW_NEEDS = {
    "week": ("status", "summary", "highlights", "games", "plays"),
    "season": ("status", "through_week", "leaderboard", "league", "trend", "calibration"),
}


def clean(o: Any) -> Any:
    """NaN / inf → None, recursively (browsers reject NaN in JSON)."""
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


def check_payloads(p: dict[str, Any]) -> None:
    """Fail early, naming what's missing, rather than drawing a half-empty page."""
    moments = p.get("moments", {})
    missing = [
        f"moments.{m}.{k}" for m, keys in NEEDS.items() for k in keys if k not in moments.get(m, {})
    ]
    review = p.get("review", {})
    for name in REVIEWS:
        for kind, keys in REVIEW_NEEDS.items():
            got = review.get(f"{kind}_{name}")
            if got is None:
                missing.append(f"review.{kind}_{name}")
            else:
                missing += [f"review.{kind}_{name}.{k}" for k in keys if k not in got]
    if "final_no_plays" not in review:
        missing.append("review.final_no_plays")
    if missing:
        raise SystemExit(f"payloads: missing {', '.join(missing)}")


def curated(root: Path, weeks: set[int]) -> dict[str, Any]:
    teams = pd.read_parquet(root / "teams.parquet")
    games = pd.read_parquet(root / "games.parquet")
    games = games[(games.season == SEASON) & games.week.isin(weeks)]
    return {
        "teams": {
            t.team: {"name": t.team_name, "nick": t.team_nick, "color": t.team_color}
            for t in teams.itertuples()
        },
        "stadiums": {g.game_id: g.stadium for g in games.itertuples()},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--payloads", type=Path, help="the reader's answers (default: the cache's)")
    args = ap.parse_args()
    paths = ensure_data_root(create_dirs=False)
    out_dir = paths.cache / "live-decisions-mockup"
    src = args.payloads or out_dir / "payloads.json"
    payloads = json.loads(src.read_text(encoding="utf-8"))
    check_payloads(payloads)
    weeks = {int(m["games"]["week"]) for m in payloads["moments"].values()}
    data = {"payloads": payloads, **curated(paths.curated, weeks)}
    blob = json.dumps(clean(data), allow_nan=False, ensure_ascii=False, default=str)
    # LD03's decision review: its own CSS after gameday.css, its JS inside gameday.js's scope
    gdcss = "\n".join((HERE / f).read_text(encoding="utf-8") for f in ("gameday.css", "review.css"))
    js = (HERE / "gameday.js").read_text(encoding="utf-8")
    if js.count(INCLUDE) != 1:
        raise SystemExit(f"gameday.js: the {INCLUDE} line is missing")
    js = js.replace(INCLUDE, (HERE / "review.js").read_text(encoding="utf-8"))
    html = (HERE / "template.html").read_text(encoding="utf-8")
    html = (
        html.replace("{{CSS}}", (CONTROL_ROOM / "styles.css").read_text(encoding="utf-8"))
        .replace("{{GDCSS}}", gdcss)
        .replace("{{JS}}", js)
        .replace("{{DATA}}", blob.replace("</", "<\\/"))
    )
    dest = out_dir / "game-day.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
    print(f"wrote {dest} ({len(html):,} bytes) from {src.name}")


if __name__ == "__main__":
    main()
