"""Deterministic rendering (documentation/06 -> Digest layout): header, report card numbers,
the game outlook table and the footer are written by code; the LLM's prose sections are
placed between them in the fixed order.
"""

from __future__ import annotations

from dataclasses import dataclass

from nflengine.digest.payload import GameItem, Payload, ReportCard

SECTION_TITLES = {
    "report_card": "Report card",
    "game_outlook": "Game outlook",
    "team_trends": "Team trend shifts",
    "under_the_hood": "Last week under the hood",
    "players_to_watch": "Players to watch",
    "matchup_risk": "Matchup / risk to watch",
    "non_obvious": "Non-obvious insights",
}
ATTRIBUTION = (
    "nflverse (play-by-play, schedules, rosters, injuries via nflreadpy), NFL Next Gen Stats, "
    "Pro Football Reference advanced stats, FTN charting (via nflverse), ESPN"
)


@dataclass
class FooterInfo:
    checks_passed: bool
    failed_checks: list[str]
    warnings: list[str]
    regenerated: bool
    prompt_hash: str
    llm_name: str
    llm_model: str | None
    fallback: bool = False  # the configured LLM failed and the placeholder wrote the prose


def header(p: Payload) -> str:
    m = p.meta
    title = f"# NFL digest: {m.season} week {m.week}"
    if m.mode == "backtest":
        title += " (backtest)"
    fresh = "; ".join(f"{k} {v}" for k, v in m.sources.items())
    run = m.run_time.replace("T", " ").replace("+00:00", " UTC")
    line = f"_Run {run} · data through {m.data_through}"
    if fresh:
        line += f" · {fresh}"
    line += "_"
    if m.mode == "backtest":
        line += (
            "\n\n_Backtest: produced as if live on the Tuesday of that week, from data "
            "available before the week's games (walk-forward predictions)._"
        )
    return f"{title}\n\n{line}"


def report_card_numbers(rc: ReportCard) -> str:
    lines: list[str] = []
    if rc.status == "scored":
        parts = [
            f"**Week {rc.scored_week}:** picks {rc.picks_correct.display} of "
            f"{rc.picks_total.display}",
            f"Brier {rc.brier.display} (Elo {rc.brier_elo.display})",
            f"score error {rc.points_mae.display} per team",
        ]
        if rc.watchlist_total:
            parts.append(f"watch list {rc.watchlist_hits.display} of {rc.watchlist_total.display}")
        lines.append(" · ".join(parts))
    s = rc.season_to_date  # also shown when last week had nothing gradable (Sol #12)
    if s:
        sp = [
            f"**Season:** picks {s.picks_correct.display} of {s.picks_total.display}",
            f"Brier {s.brier.display} (Elo {s.brier_elo.display}) over {s.weeks.display} "
            f"week{'s' if s.weeks.value != 1 else ''}",
        ]
        if s.watchlist_total:
            sp.append(f"watch list {s.watchlist_hits.display} of {s.watchlist_total.display}")
        lines.append(" · ".join(sp))
    if rc.calibration:
        cal = ", ".join(
            f"{b.bucket}: {b.favorite_wins.display} of {b.games.display}" for b in rc.calibration
        )
        lines.append(f"**Calibration (season, favorite won):** {cal}")
    if rc.not_graded:
        lines.append(f"_{rc.not_graded} game(s) not graded: predicted after kickoff._")
    return "\n".join(f"- {line}" for line in lines)


def _game_row(g: GameItem) -> str:
    mark = "" if not g.market_fallback else " †"
    site = " (n)" if g.neutral_site else ""
    return (
        f"| {g.kickoff} | {g.away_name} at {g.home_name}{site}{mark} | "
        f"{g.away_win_prob.display} / {g.home_win_prob.display} | "
        f"{g.predicted_score.away.display}–{g.predicted_score.home.display} | "
        f"{g.expected_margin.display} | {g.confidence} |"
    )


def game_table(p: Payload) -> str:
    upcoming = [g for g in p.games if g.status == "upcoming"]
    started = p.started_games
    lines = []
    if upcoming:
        lines += [
            "| Kickoff (ET) | Game | Win % (away / home) | Predicted score | Margin | Call |",
            "|---|---|---|---|---|---|",
            *[_game_row(g) for g in upcoming],
        ]
    notes = []
    if any(g.market_fallback for g in upcoming):
        notes.append("† no complete market line: model-only numbers")
    if any(g.neutral_site for g in upcoming):
        notes.append("(n) neutral site")
    if notes:
        lines.append("\n_" + "; ".join(notes) + "._")
    if started:
        lines.append(f"\n_Kicked off before this run (not predicted here): {', '.join(started)}._")
    return "\n".join(lines)


def footer(p: Payload, f: FooterInfo) -> str:
    m = p.meta
    versions = ", ".join(f"{k} `{v}`" for k, v in m.model_versions.items())
    status = "all passed" if f.checks_passed else "FAILED: " + ", ".join(f.failed_checks)
    if f.regenerated:
        status += " (after one regeneration)"
    if f.warnings:
        status += f"; warnings: {', '.join(f.warnings)}"
    sources = ATTRIBUTION + (", The Odds API" if m.market_data_used else "")
    lines = [
        f"Models: {versions}",
        "Market data used: "
        + (
            "yes (as a model input and for the consensus comparison)"
            if m.market_data_used
            else "no (model-only)"
        ),
        f"Injury snapshot: {m.injury_snapshot or 'n/a'}",
        f"Knowledge graph: {graph_line(p)}",
        f"Checks: {status}",
        f"Writer: {f.llm_name} ({f.llm_model or 'n/a'}), prompt `{f.prompt_hash}`"
        + (" (the configured LLM failed; see raw_llm_output.json)" if f.fallback else ""),
        f"Data: {sources}",
    ]
    return "---\n\n" + "\n".join(f"- {line}" for line in lines)


def graph_line(p: Payload) -> str:
    m = p.meta
    if m.graph_status == "ok":
        n = len(p.graph_insights)
        return f"rebuilt for this run (Neo4j); {n} insight{'s' if n != 1 else ''} used"
    if m.graph_status == "unavailable":
        return f"unavailable: {m.graph_note or 'no results'}"
    return "off"


def graph_banner(p: Payload) -> str | None:
    """Shown when the graph sections are missing because the graph failed (fail-soft)."""
    if p.meta.graph_status != "unavailable":
        return None
    return (
        "ℹ️ **Graph sections skipped this week:** "
        f"{p.meta.graph_note or 'the knowledge graph was unavailable'}. The rest of the "
        "digest is complete."
    )


def render_digest(
    p: Payload, sections: dict[str, str], info: FooterInfo, banner: str | None
) -> str:
    out = [header(p)]
    if banner:
        out.append(f"> {banner}")
    gb = graph_banner(p)
    if gb:
        out.append(f"> {gb}")
    order = list(SECTION_TITLES)
    for sec in order:
        if sec not in sections:
            continue
        body = []
        if sec == "report_card":
            nums = report_card_numbers(p.report_card)
            if nums:
                body.append(nums)
        if sec == "game_outlook":
            body.append(game_table(p))
        if sec == "players_to_watch":
            body.append("_Heuristic picks until the player model ships (P06): low confidence._")
        if sections[sec]:
            body.append(sections[sec])
        out.append(f"## {SECTION_TITLES[sec]}\n\n" + "\n\n".join(b for b in body if b))
    out.append(footer(p, info))
    return "\n\n".join(out) + "\n"
