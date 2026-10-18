"""Rebuild the Ask the Engine mockup (a single HTML file) from this folder's sources.

The mockup is the visual spec for AE01's page (documentation/ask-the-engine/AE01-ask-tab.md):
Explore -> Ask the Engine, drawn inside the control room's shell, so it reuses the control
room mockup's styles.css unchanged and adds ask.css on top. The data is the real app API's
answers (the shapes in web/src/api/types.ts, AE01 block) from dump_payloads.py; nothing is
committed and no model is called here.

    uv run python documentation/ask-the-engine/mockup/build_mockup.py [--payloads PATH]

Reads {NFL_DATA_ROOT}/cache/ask-mockup/payloads.json unless --payloads is given.
Writes {NFL_DATA_ROOT}/cache/ask-mockup/ask.html. Read-only otherwise.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from nflengine.paths import ensure_data_root

HERE = Path(__file__).parent
CONTROL_ROOM = HERE.parents[1] / "control-room" / "mockup"
# what the page draws; the build stops and names anything missing
SHELL = ("meta", "weeks", "examples", "examples_graph_down")
EXAMPLES_NEEDS = ("sources", "model", "limits", "busy", "featured", "groups")
ANSWERS = (
    "template_graph",
    "template_sql",
    "freeform",
    "lists",
    "refused_code",
    "refused_router",
    "clarify",
    "clarify_picked",
    "empty",
    "graph_down",
    "llm_down",
)
ANSWER_NEEDS = (
    "question",
    "asked_at",
    "status",
    "answer",
    "answer_kind",
    "columns",
    "rows",
    "row_count",
    "truncated",
    "query",
    "language",
    "params",
    "source",
    "route",
    "route_label",
    "template",
    "retries",
    "refusal",
    "choices",
    "error",
    "timing",
    "cost",
    "llm",
)
ERRORS = ("busy", "rate_limited", "too_long")


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
    missing = [k for k in SHELL if not p.get(k)]
    for k in ("examples", "examples_graph_down"):
        if p.get(k):
            _need(missing, k, p[k], EXAMPLES_NEEDS)
    answers = p.get("answers") or {}
    for k in ANSWERS:
        got = answers.get(k)
        if not isinstance(got, dict):
            missing.append(f"answers.{k}")
            continue
        _need(missing, f"answers.{k}", got, ("request", "http_status", "body"))
        _need(missing, f"answers.{k}.body", got.get("body"), ANSWER_NEEDS)
    if not ((answers.get("clarify_picked") or {}).get("request") or {}).get("picks"):
        missing.append("answers.clarify_picked.request.picks")
    errors = p.get("errors") or {}
    for k in ERRORS:
        _need(missing, f"errors.{k}", errors.get(k), ("http_status", "body"))
    if missing:
        raise SystemExit(f"payloads: missing {', '.join(missing)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--payloads", type=Path, help="the API's answers (default: the cache's)")
    args = ap.parse_args()
    paths = ensure_data_root(create_dirs=False)
    out_dir = paths.cache / "ask-mockup"
    src = args.payloads or out_dir / "payloads.json"
    payloads = json.loads(src.read_text(encoding="utf-8"))
    check_payloads(payloads)
    blob = json.dumps(clean({"payloads": payloads}), allow_nan=False, ensure_ascii=False)
    html = (HERE / "template.html").read_text(encoding="utf-8")
    html = (
        html.replace("{{CSS}}", (CONTROL_ROOM / "styles.css").read_text(encoding="utf-8"))
        .replace("{{ASKCSS}}", (HERE / "ask.css").read_text(encoding="utf-8"))
        .replace("{{JS}}", (HERE / "ask.js").read_text(encoding="utf-8"))
        .replace("{{DATA}}", blob.replace("</", "<\\/"))
    )
    dest = out_dir / "ask.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
    print(f"wrote {dest} ({len(html):,} bytes) from {src.name}")


if __name__ == "__main__":
    main()
