"""Deterministic rendering (documentation/06 -> Digest layout): header, report card numbers,
the game outlook table and the footer are written by code; the LLM's prose sections are
placed between them in the fixed order.
"""

from __future__ import annotations

from dataclasses import dataclass

from nflengine.digest.payload import GameItem, Num, Payload, ReportCard, WatchItem

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
            wl = f"watch list {rc.watchlist_hits.display} of {rc.watchlist_total.display}"
            if rc.watchlist_inside is not None:
                wl += f" above baseline, {rc.watchlist_inside.display} inside their range"
            if rc.watchlist_by_side:
                wl += (
                    " ("
                    + ", ".join(
                        f"{t.side} {t.hits.display} of {t.total.display}"
                        for t in rc.watchlist_by_side
                    )
                    + ")"
                )
            parts.append(wl)
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
    if rc.scoreboard_highlights:
        hl = " ".join(f"{_cap(h)}." for h in rc.scoreboard_highlights)
        lines.append(f"**Player projections:** {hl}")
    if rc.not_graded:
        lines.append(f"_{rc.not_graded} game(s) not graded: predicted after kickoff._")
    out = "\n".join(f"- {line}" for line in lines)
    table = lookback_table(rc)
    return f"{out}\n\n{table}" if out and table else (out or table)


def _mark(flag: bool | None) -> str:
    return "–" if flag is None else ("✓" if flag else "✗")


def lookback_table(rc: ReportCard) -> str:
    """Last week's picks in one compact code-written table (D70: 20 picks a week). The
    one-line text per pick stays in the payload (`LookbackItem.text`)."""
    if not rc.watch_lookback:
        return ""
    lines = [
        "**Last week's watch list**",
        "",
        "| Side | Player | Stat | Projected | Range | Actual | In range | Above baseline |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for lb in rc.watch_lookback:
        actual = lb.actual.display if lb.actual is not None else (lb.status or "–")
        lines.append(
            f"| {lb.side or '–'} | {lb.player} ({lb.team_name} {lb.position}) | {lb.target} | "
            f"{_d(lb.projection)} | {_d(lb.interval)} | {actual} | {_mark(lb.inside)} | "
            f"{_mark(lb.hit)} |"
        )
    return "\n".join(lines)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


NEWS_MAX = 4  # ESPN headlines shown under "Latest news"


def _qb_cell(name: str | None, team: str, changed: set[str]) -> str:
    if not name:
        return "?"
    return f"{name} ⚠" if team in changed else name


def _game_row(g: GameItem, changed: set[str] | None = None) -> str:
    mark = "" if not g.market_fallback else " †"
    site = " (n)" if g.neutral_site else ""
    changed = changed or set()
    qbs = f"{_qb_cell(g.away_qb, g.away, changed)} / {_qb_cell(g.home_qb, g.home, changed)}"
    return (
        f"| {g.kickoff} | {g.away_name} at {g.home_name}{site}{mark} | {qbs} | "
        f"{g.away_win_prob.display} / {g.home_win_prob.display} | "
        f"{g.predicted_score.away.display}–{g.predicted_score.home.display} | "
        f"{g.expected_margin.display} | {g.confidence} |"
    )


def game_table(p: Payload) -> str:
    upcoming = [g for g in p.games if g.status == "upcoming"]
    started = p.started_games
    changed = {n.team for n in p.qb_changes}
    lines = []
    if upcoming:
        lines += [
            "| Kickoff (ET) | Game | QBs (away / home) | Win % (away / home) | Predicted score "
            "| Margin | Call |",
            "|---|---|---|---|---|---|---|",
            *[_game_row(g, changed) for g in upcoming],
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
    if p.qb_changes:
        lines.append(
            "\n**⚠ QB changes**\n\n" + "\n".join(f"- {n.team_name}: {n.text}" for n in p.qb_changes)
        )
    return "\n".join(lines)


def starters_out_list(p: Payload) -> str:
    """Regular starters who won't play, one line per game (code-written; graph Q0)."""
    if not p.starters_out:
        return ""
    games = {g.game_id: g for g in p.games}
    by_game: dict[str, dict[str, list[str]]] = {}
    for o in p.starters_out:
        by_game.setdefault(o.game_id, {}).setdefault(o.team_name, []).append(o.text)
    order = [g.game_id for g in p.games if g.game_id in by_game]
    lines = []
    for gid in order:
        teams = "; ".join(f"{t}: {', '.join(xs)}" for t, xs in by_game[gid].items())
        lines.append(f"- {games[gid].matchup}: {teams}")
    note = "regular starters, up to 3 per team"
    if any("missed last game" in o.text for o in p.starters_out):
        note += '; "missed last game" = no injury report for this week yet when this ran'
    return f"**Starters out this week** ({note})\n\n" + "\n".join(lines)


MODEL_WATCH_NOTE = (
    "_Player model projections: the projection is the model's central estimate (the median "
    "for yards, the average for counts), the range covers 80% of outcomes (10th to 90th "
    "percentile), the baseline is his own rolling average. Low confidence = thin history or "
    "a wide range._"
)
HEURISTIC_WATCH_NOTE = (
    "_Heuristic picks (no player-model projections this week): usage trend and opponent "
    "defense only, low confidence._"
)


def watch_source(p: Payload) -> str:
    """model | heuristic: where this week's picks came from (the payload's model version
    says it when the list is empty)."""
    if p.players_to_watch:
        return "model" if any(w.source == "model" for w in p.players_to_watch) else "heuristic"
    version = p.meta.model_versions.get("players to watch", "")
    return "heuristic" if version.startswith("heuristic") else "model"


MODEL_HEADER = [
    "| Player | Game | Projection | Range (80%) | Baseline | Confidence | Main driver / note |",
    "|---|---|---|---|---|---|---|",
]
SIDE_TITLES = {"offense": "Offense", "defense": "Defense"}


def _model_rows(rows: list[WatchItem]) -> list[str]:
    return [
        f"| {w.player} ({w.team_name} {w.position}) | vs {w.opponent_name} | "
        f"{_d(w.projection)} | {_d(w.interval)} | {w.baseline.display} | "
        f"{w.confidence} | {_why(w)} |"
        for w in rows
    ]


def watch_table(p: Payload) -> str:
    """Every watch-list pick, code-written; the prose covers the top ones. Model picks come
    in two tables, Offense and Defense (D70); picks without a side in one."""
    rows = p.players_to_watch
    if not rows:
        return ""
    if watch_source(p) == "model":
        if not any(w.side for w in rows):
            return "\n".join([*MODEL_HEADER, *_model_rows(rows)])
        blocks = []
        for side, title in SIDE_TITLES.items():
            part = [w for w in rows if w.side == side]
            if part:
                n = len(part)
                blocks.append(
                    f"**{title}** ({n} pick{'s' if n != 1 else ''})\n\n"
                    + "\n".join([*MODEL_HEADER, *_model_rows(part)])
                )
        return "\n\n".join(blocks)
    lines = [
        "| Player | Game | Usage (recent vs before) | Opponent defense | Baseline |",
        "|---|---|---|---|---|",
    ]
    for w in rows:
        lines.append(
            f"| {w.player} ({w.team_name} {w.position}) | vs {w.opponent_name} | "
            f"{w.usage_metric} {_d(w.usage_recent)} vs {_d(w.usage_before)} "
            f"({w.usage_before_note}) | {w.opp_def_unit}: {_d(w.opp_def_rank)} | "
            f"{w.baseline.display} |"
        )
    return "\n".join(lines)


def _why(w: WatchItem) -> str:
    lead = w.drivers[:1] or ([w.driver_note] if w.driver_note else [])
    parts = [*lead, *([w.baseline_note] if w.baseline_note else [])]
    return "; ".join(parts) or "–"


def _d(n: Num | None) -> str:
    return n.display if n is not None else "–"


def tough_spots_list(p: Payload) -> str:
    """Regular starters projected well below their own baseline (code-written, P06)."""
    if not p.tough_spots:
        return ""
    lines = [
        f"- {w.player} ({w.team_name} {w.position}) vs the {w.opponent_name}: projected "
        f"{_d(w.projection)}, {_d(w.vs_baseline)} of {w.baseline.display} (range "
        f"{_d(w.interval)})"
        for w in p.tough_spots
    ]
    return "**Tough spots** (player model: projected well below their own baseline)\n\n" + (
        "\n".join(lines)
    )


def graph_more_list(p: Payload) -> str:
    """The strong graph stories that didn't fit the prose, one code-written line each."""
    if not p.graph_more:
        return ""
    games = {g.game_id: g.matchup for g in p.games}
    lines = [f"- **{games.get(i.game_id, i.matchup)}**: {i.brief}" for i in p.graph_more]
    return "**More from the graph**\n\n" + "\n".join(lines)


def news_list(p: Payload) -> str:
    """Up to `NEWS_MAX` recent non-fantasy ESPN headlines about this week's teams, as
    published (attributed, not rewritten)."""
    items = p.news[:NEWS_MAX]
    if not items:
        return ""
    lines = []
    for n in items:
        day = f", {n.published[:10]}" if n.published else ""
        lines.append(f"- {n.headline} ({n.source}{day})")
    return "## Latest news\n\n" + "\n".join(lines)


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
            body.append(starters_out_list(p))
        if sec == "players_to_watch":
            source = watch_source(p)
            body.append(MODEL_WATCH_NOTE if source == "model" else HEURISTIC_WATCH_NOTE)
            body.append(watch_table(p))
        if sections[sec]:
            body.append(sections[sec])
        # tough spots belong to Matchup / risk; without the graph sections, under the picks
        if sec == "matchup_risk" or (sec == "players_to_watch" and "matchup_risk" not in sections):
            body.append(tough_spots_list(p))
        if sec == "non_obvious":
            body.append(graph_more_list(p))
        out.append(f"## {SECTION_TITLES[sec]}\n\n" + "\n\n".join(b for b in body if b))
    news = news_list(p)
    if news:
        out.append(news)
    out.append(footer(p, info))
    return "\n\n".join(out) + "\n"
