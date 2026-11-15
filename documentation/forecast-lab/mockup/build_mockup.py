"""Build the FL02 tab mockups (one self-contained HTML file each) from this folder's sources.

The mockups are the visual spec for FL02's Outcomes and Availability week tabs
(documentation/forecast-lab/README.md §3). Each inlines the control room mockup's styles.css
unchanged, then the tab's CSS, JS and payloads (the real answers, trimmed: dump_payloads.py).

    uv run python documentation/forecast-lab/mockup/build_mockup.py --out <file.html>
    ... build_mockup.py --page availability --out <file.html>

Reads only this folder and ../../control-room/mockup/styles.css; writes only `--out`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).parent
CONTROL_ROOM = HERE.parents[1] / "control-room" / "mockup"
NEEDS = ("season", "week", "featured", "outcomes", "curves", "states")
ANSWER_NEEDS = ("status", "meta", "tiles", "targets", "target_key", "rows", "gaps", "graded")
AV_NEEDS = ("season", "week", "base", "team")
AV_BASE_NEEDS = ("status", "meta", "tiles", "at_risk", "movers", "teams", "graded", "scoreboard")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--page", choices=("outcomes", "availability"), default="outcomes")
    ap.add_argument("--payloads", type=Path, default=None)
    a = ap.parse_args()
    av = a.page == "availability"
    default = HERE / ("payloads_availability.json" if av else "payloads.json")
    data = json.loads((a.payloads or default).read_text(encoding="utf-8"))
    if av:
        missing = [k for k in AV_NEEDS if k not in data]
        missing += [f"base.{k}" for k in AV_BASE_NEEDS if k not in data.get("base", {})]
    else:
        missing = [k for k in NEEDS if k not in data]
        for key, body in data.get("outcomes", {}).items():
            missing += [f"outcomes.{key}.{k}" for k in ANSWER_NEEDS if k not in body]
    if missing:
        raise SystemExit(f"payloads are missing: {', '.join(missing)}")
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    template, css, js, slot = (
        ("template_availability.html", "availability.css", "availability.js", "{{AVCSS}}")
        if av
        else ("template.html", "outcomes.css", "outcomes.js", "{{OCCSS}}")
    )
    html = (
        (HERE / template)
        .read_text(encoding="utf-8")
        .replace("{{CSS}}", (CONTROL_ROOM / "styles.css").read_text(encoding="utf-8"))
        .replace(slot, (HERE / css).read_text(encoding="utf-8"))
        .replace("{{JS}}", (HERE / js).read_text(encoding="utf-8"))
        .replace("{{DATA}}", text)
    )
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(html, encoding="utf-8")
    print(f"wrote {a.out} ({len(html) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
