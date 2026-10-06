"""The Digest tab: the published Markdown, its checks, the writer, the files and Saturday's
addendum.

`raw_llm_output.json` holds the prompt's answer (`attempts`: the raw text of every section):
only the metadata in `CALL_FIELDS` / `WRITER_FIELDS` leaves the server, copied field by field
(an allowlist, so a new field in the file never reaches the browser by accident).
"""

from __future__ import annotations

import re
from typing import Any

from nflengine.app.readers.common import clean, num, read_json, read_text, wandb_urls
from nflengine.paths import DataPaths
from nflengine.weekly import already_published, read_state

PROMPT_ID = re.compile(r"prompt `([0-9a-f]{6,16})`")
MAX_DIGEST_CHARS = 200_000  # a digest is ~15 KB; anything far bigger isn't a digest


def _check_round(r: Any, paths: DataPaths) -> dict[str, Any] | None:
    if not isinstance(r, dict):
        return None
    checks = []
    for c in r.get("checks") or []:
        if not isinstance(c, dict):
            continue
        issues = [clean(i, paths, 300) for i in (c.get("issues") or [])[:20]]
        checks.append(
            {
                "name": clean(c.get("name"), None, 60),
                "level": clean(c.get("level"), None, 20),
                "passed": bool(c.get("passed")),
                "issues": [i for i in issues if i],
            }
        )
    words = r.get("word_counts") or {}
    return {
        "passed": bool(r.get("passed")),
        "failed": [clean(x, None, 60) for x in r.get("failed") or []],
        "warnings": [clean(x, None, 60) for x in r.get("warnings") or []],
        "word_counts": {
            clean(k, None, 60): int(v)
            for k, v in words.items()
            if isinstance(v, int | float) and not isinstance(v, bool)
        },
        "checks": checks,
    }


def _checks(checks: dict[str, Any] | None, paths: DataPaths) -> dict[str, Any] | None:
    if checks is None:
        return None
    return {
        "passed": bool(checks.get("passed")),
        "regenerated": bool(checks.get("regenerated")),
        "banner": bool(checks.get("banner")),
        "writers": [clean(w, None, 40) for w in checks.get("writers") or []],
        "fallback_notes": [clean(n, paths, 300) for n in checks.get("fallback_notes") or []],
        "first_attempt": _check_round(checks.get("first_attempt"), paths),
        "final": _check_round(checks.get("final"), paths),
    }


def _call(c: dict[str, Any]) -> dict[str, Any]:
    """One LLM call's metadata (an allowlist: never the prompt or the text)."""
    return {
        "model": clean(c.get("model"), None, 80),
        "provider": clean(c.get("provider"), None, 60),
        "latency_s": num(c.get("latency_s"), 2),
        "usage": {
            "prompt": num(c.get("prompt_tokens")),
            "completion": num(c.get("completion_tokens")),
            "reasoning": num(c.get("reasoning_tokens")),
        },
        "cost": num(c.get("cost")),
        "finish_reason": clean(c.get("finish_reason"), None, 40),
        "regeneration": bool(c.get("regeneration")),
        "parsed": c.get("parsed") if isinstance(c.get("parsed"), bool) else None,
    }


def _writer(raw: dict[str, Any] | None, markdown: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    calls = [_call(c) for c in raw.get("calls") or [] if isinstance(c, dict)]
    costs = [c["cost"] for c in calls if c["cost"] is not None]
    times = [c["latency_s"] for c in calls if c["latency_s"] is not None]
    m = PROMPT_ID.search(markdown or "")
    return {
        "provider": clean(raw.get("provider"), None, 40) or "",
        "model": clean(raw.get("model"), None, 80) or "",
        "prompt": m.group(1) if m else None,
        "writers": [clean(w, None, 40) for w in raw.get("writers") or []],
        "calls": calls,
        "total_cost": round(sum(costs), 6) if costs else None,
        "total_seconds": round(sum(times), 1) if times else None,
    }


def _rel(path, paths: DataPaths) -> str:
    return path.relative_to(paths.root).as_posix()


def get_digest(paths: DataPaths, season: int, week: int) -> dict[str, Any]:
    run_dir = paths.run_dir(season, week)
    report = paths.report_path(season, week)
    published = already_published(run_dir / "weekly_run.json", report)
    text = read_text(report) if published else None
    status = "published" if text is not None else "none"
    if text is None and (run_dir / "digest.md").exists():
        text, status = read_text(run_dir / "digest.md"), "unpublished"
    if text is not None and len(text) > MAX_DIGEST_CHARS:
        text = text[:MAX_DIGEST_CHARS]
    # scrubbed like every text (a digest never holds a credential; the parity test checks
    # that scrubbing leaves the published file byte for byte)
    markdown = clean(text, paths, MAX_DIGEST_CHARS) if text is not None else None

    raw = read_json(run_dir / "raw_llm_output.json")
    state = read_state(run_dir / "weekly_run.json") or {}
    digest_detail = ((state.get("steps") or {}).get("digest") or {}).get("detail")
    urls = wandb_urls(digest_detail)

    update = read_json(run_dir / "injury_update.json")
    addendum_path = paths.report_path(season, week, kind="injury-update")
    addendum_md = read_text(addendum_path) if addendum_path.exists() else None
    addendum = None
    if update is not None or addendum_md is not None:
        addendum = {
            "markdown": clean(addendum_md, paths, MAX_DIGEST_CHARS) if addendum_md else None,
            "material": bool(update.get("material")) if isinstance(update, dict) else None,
            "at": clean((update or {}).get("finished"), None, 40),
        }

    files = [
        ("digest", report),
        ("payload", run_dir / "payload.json"),
        ("checks", run_dir / "checks.json"),
        ("raw LLM", run_dir / "raw_llm_output.json"),
        ("Saturday", addendum_path),
    ]
    return {
        "season": season,
        "week": week,
        "status": status,
        "markdown": markdown,
        "checks": _checks(read_json(run_dir / "checks.json"), paths),
        "writer": _writer(raw, text),
        "wandb_url": urls[-1] if urls else None,
        "files": [
            {"label": label, "path": _rel(p, paths), "exists": p.exists()} for label, p in files
        ],
        "addendum": addendum,
    }
