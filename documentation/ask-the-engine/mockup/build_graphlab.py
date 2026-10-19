"""Rebuild the Connections + Graph lab mockup (AE03, a single HTML file) from this folder's sources.

The visual spec for AE03's two new parts of Explore -> Ask the Engine
(documentation/ask-the-engine/AE03-connections-and-graph-ml.md), drawn inside the control room's
shell: the control room mockup's styles.css and AE01's ask.css unchanged, graphlab.css on top.
The data is the real app API's answers (the AE03 block of web/src/api/types.ts) from
dump_graphlab.py; nothing is committed and nothing is asked of Neo4j here.

    uv run python documentation/ask-the-engine/mockup/build_graphlab.py [--payloads PATH]

Reads {NFL_DATA_ROOT}/cache/ask-mockup/graphlab.json unless --payloads is given.
Writes {NFL_DATA_ROOT}/cache/ask-mockup/graphlab.html. Read-only otherwise.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from build_mockup import CONTROL_ROOM, HERE, clean

from nflengine.paths import ensure_data_root

SHELL = ("meta", "weeks", "status")
CONNECTIONS = (
    "teammates",
    "teammates_more",
    "two_steps",
    "two_steps_more",
    "player_coach",
    "coaches",
    "rookie_coach",
    "brady_rodgers",
    "brady_rodgers_strongest",
    "brady_rodgers_strongest_more",
    "teammates_strongest_more",
)
ERRORS = ("same_person", "bad_id", "not_found", "history_down", "busy")
NEIGHBOURS = ("kelce", "mahomes", "jefferson", "josh_allen", "cam_ward", "shepley")
LAB = ("map", "families", "family_of_mahomes", "family_of_reid", "chemistry", "chemistry_300",
       "benchmark")  # fmt: skip


def check_payloads(p: dict[str, Any]) -> None:
    """Fail early, naming what's missing, rather than drawing a half-empty page."""
    missing = [k for k in SHELL if not p.get(k)]
    for k in CONNECTIONS:
        got = (p.get("connections") or {}).get(k) or {}
        if got.get("http_status") != 200 or not (got.get("body") or {}).get("paths"):
            missing.append(f"connections.{k}")
    for k in ERRORS:
        got = (p.get("connection_errors") or {}).get(k) or {}
        if not ((got.get("body") or {}).get("error") or {}).get("message"):
            missing.append(f"connection_errors.{k}")
    lab = p.get("lab") or {}
    missing += [f"lab.neighbours.{k}" for k in NEIGHBOURS if k not in (lab.get("neighbours") or {})]
    missing += [f"lab.{k}" for k in LAB if k not in lab]
    if not (p.get("people") or {}):
        missing.append("people")
    if missing:
        raise SystemExit(f"graphlab payloads: missing {', '.join(missing)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--payloads", type=Path, help="the API's answers (default: the cache's)")
    args = ap.parse_args()
    paths = ensure_data_root(create_dirs=False)
    out_dir = paths.cache / "ask-mockup"
    src = args.payloads or out_dir / "graphlab.json"
    payloads = json.loads(src.read_text(encoding="utf-8"))
    check_payloads(payloads)
    blob = json.dumps(clean({"payloads": payloads}), allow_nan=False, ensure_ascii=False)
    html = (HERE / "template_graphlab.html").read_text(encoding="utf-8")
    html = (
        html.replace("{{CSS}}", (CONTROL_ROOM / "styles.css").read_text(encoding="utf-8"))
        .replace("{{ASKCSS}}", (HERE / "ask.css").read_text(encoding="utf-8"))
        .replace("{{GLCSS}}", (HERE / "graphlab.css").read_text(encoding="utf-8"))
        .replace("{{JS}}", (HERE / "graphlab.js").read_text(encoding="utf-8"))
        .replace("{{DATA}}", blob.replace("</", "<\\/"))
    )
    dest = out_dir / "graphlab.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
    print(f"wrote {dest} ({len(html):,} bytes) from {src.name}")


if __name__ == "__main__":
    main()
