"""The digest's knowledge-graph sections (plan P05; documentation/06 -> Digest layout 6-7).

`graph_state` decides what a digest run gets from the graph:

- `auto` (default): a live run reads this week's `graph_results.json` (the weekly `graph`
  step wrote it) and builds it now only if it's missing; a backtest builds the graph as of
  the week's Tuesday, then reads it;
- `build`: build now, then read; `read`: read only; `off`: no graph sections, no banner.

Builds are fail-soft: if Neo4j is down the results file says `unavailable`, the digest
publishes without sections 6-7 and shows a banner (`meta.graph_status` / `graph_note`).

The picks are re-made here with the digest's own run time (`graph.insights.select`): a
digest written later in the week than the graph build must not feature a game that has
kicked off since. Novelty reads the published-insight log, the same source the graph's
`PublishedInsight` nodes come from. After the digest is written, `record_published` adds
the picks to the log (and, best effort, to the live graph).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import polars as pl

from nflengine.digest.payload import GraphInsight, QBChangeNote, StarterOut
from nflengine.digest.prompt import GRAPH_PHASE
from nflengine.graph.insights import SUBJECT_ROLES

if TYPE_CHECKING:
    from nflengine.digest.run import DigestContext

GRAPH_MODES = ("auto", "build", "read", "off")
NO_ITEM = "No graph angle cleared the bar this week."
# a live digest can take 20+ minutes to write (two GLM calls): its graph items skip games
# kicking off within this margin of the run, so none is about a game already underway
LIVE_KICKOFF_MARGIN = dt.timedelta(minutes=90)


@dataclass
class GraphState:
    status: str  # ok | unavailable | off
    note: str | None = None
    matchup_risk: list[GraphInsight] = field(default_factory=list)
    non_obvious: list[GraphInsight] = field(default_factory=list)
    built_at: str | None = None
    candidates: int = 0
    skipped: dict[str, list[str]] = field(default_factory=dict)
    more: list[GraphInsight] = field(default_factory=list)  # "More from the graph"
    qb_changes: list[QBChangeNote] = field(default_factory=list)  # flags in the game table
    starters_out: list[StarterOut] = field(default_factory=list)

    @property
    def picked(self) -> list[GraphInsight]:
        return [*self.matchup_risk, *self.non_obvious]

    @property
    def enabled_phases(self) -> tuple[str, ...]:
        return (GRAPH_PHASE,) if self.status == "ok" else ()


def graph_state(
    ctx: DigestContext,
    games: pl.DataFrame,
    mode: str = "auto",
    *,
    use_wandb: bool = False,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
) -> GraphState:
    from nflengine.graph.build import game_infos, load_results, results_path, run_graph_build
    from nflengine.graph.insights import select
    from nflengine.graph.published import log_path, read_log, recent_from_log
    from nflengine.settings import load_followed_teams

    if mode not in GRAPH_MODES:
        raise ValueError(f"graph mode must be one of {GRAPH_MODES}, got {mode!r}")
    if mode == "off":
        return GraphState("off")
    s, w = ctx.season, ctx.week
    path = results_path(ctx.paths, s, w, ctx.mode)
    build = mode == "build" or (mode == "auto" and (ctx.mode == "backtest" or not path.exists()))
    if build:
        log(f"rebuilding the knowledge graph as of {s} week {w} ({ctx.mode}) ...")
        run_graph_build(
            s,
            w,
            mode=ctx.mode,
            run_time=ctx.run_time,
            use_wandb=use_wandb,
            launched_by=launched_by,
            fail_soft=True,
            log=log,
        )
    data = load_results(path)
    if data is None:
        return GraphState(
            "unavailable",
            f"no knowledge-graph results for this week (run `nfl graph build --season {s} "
            f"--week {w}`)",
        )
    if data.get("status") != "ok":
        return GraphState(
            "unavailable",
            f"the knowledge graph couldn't be rebuilt ({data.get('error') or 'unknown error'})",
        )
    if (data.get("season"), data.get("week"), data.get("mode")) != (s, w, ctx.mode):
        return GraphState("unavailable", "the graph results on file are for another run")
    cands = [GraphInsight.model_validate(c) for c in data.get("candidates", [])]
    recent = recent_from_log(read_log(log_path(ctx.run_root)), s, w)
    sel = select(
        cands,
        game_infos(games, s, w),
        ctx.run_time,
        followed=load_followed_teams(),
        recent_ids=recent,
        kickoff_margin=LIVE_KICKOFF_MARGIN if ctx.mode == "live" else dt.timedelta(0),
    )
    upcoming = {
        gid
        for gid, g in game_infos(games, s, w).items()
        if g.kickoff is None or g.kickoff > ctx.run_time
    }
    queries = data.get("queries", {})
    return GraphState(
        "ok",
        None,
        sel.matchup_risk,
        sel.non_obvious,
        data.get("built_at"),
        len(cands),
        sel.skipped,
        more=sel.more,
        qb_changes=qb_change_notes(_rows(queries, "q3_qb_change"), upcoming),
        starters_out=starters_out(_rows(queries, "q0_starters_out"), upcoming),
    )


def _rows(queries: dict, name: str) -> list[dict]:
    q = queries.get(name) or {}
    return list(q.get("results") or [])


# ---- code-written graph extras (after P05) ------------------------------------------------------

MAX_OUT_PER_TEAM = 3
# who's listed first in a team's "Starters out" line: QBs, then skill players, then the rest
OUT_PRIORITY = {"QB": 0, "WR": 1, "TE": 1, "RB": 1, "DL": 2, "LB": 2, "DB": 2, "OL": 2}


def qb_change_notes(rows: list[dict], upcoming: set[str]) -> list[QBChangeNote]:
    """Every QB change the graph found (Q3), for the ⚠ flags under the game table. Text is
    time-scoped like the graph item: confirmed (a live run's schedule / chart / report) or
    "could start ... not confirmed yet" (the Tuesday rule)."""
    from nflengine.digest.names import nickname

    out = []
    for r in rows:
        if r.get("game_id") not in upcoming:
            continue
        qb, reg = r["qb"], r["regular"]
        if r.get("qb_confirmed", True) is not False:
            text = f"{qb} starts in place of {reg}"
            status = (r.get("regular_status") or "").lower()
            if status in ("out", "doubtful"):
                body = (r.get("regular_body_part") or "").lower()
                text += f" ({status}{': ' + body if body else ''})"
        else:
            text = (
                f"{qb} could start in place of {reg} "
                "(he started their last game; not confirmed yet)"
            )
        out.append(
            QBChangeNote(
                game_id=r["game_id"],
                team=r["team"],
                team_name=nickname(r["team"]),
                qb=qb,
                regular=reg,
                confirmed=r.get("qb_confirmed", True) is not False,
                text=text,
            )
        )
    return sorted(out, key=lambda n: (n.game_id, n.team))


def _out_text(r: dict) -> str:
    pos = r.get("position") or r.get("position_group") or ""
    src = r.get("out_source")
    if src == "this_week":
        status = (r.get("status") or "out").lower()
        body = (r.get("body_part") or "").lower()
        why = f"{status}{': ' + body if body else ''}"
    elif src == "reserve":
        why = "reserve list"
    else:
        why = "missed last game"
    return f"{pos} {r['player']} ({why})".strip()


def starters_out(rows: list[dict], upcoming: set[str]) -> list[StarterOut]:
    """Regular starters who won't play (Q0), at most `MAX_OUT_PER_TEAM` per team: QBs
    first, then skill players, then by snap share."""
    from nflengine.digest.names import nickname

    by_team: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("game_id") in upcoming:
            by_team.setdefault(r["team"], []).append(r)
    out = []
    for team, rs in by_team.items():
        rs.sort(
            key=lambda r: (
                OUT_PRIORITY.get(r.get("position_group") or "", 3),
                -(r.get("snap_pct") or 0),
                r["player_id"],
            )
        )
        for r in rs[:MAX_OUT_PER_TEAM]:
            out.append(
                StarterOut(
                    game_id=r["game_id"],
                    team=team,
                    team_name=nickname(team),
                    player=r["player"],
                    player_id=r["player_id"],
                    position=r.get("position") or "",
                    text=_out_text(r),
                )
            )
    return sorted(out, key=lambda o: (o.game_id, o.team))


def fixed_graph_sections(state: GraphState) -> dict[str, str]:
    """A graph section with nothing to say is one code-written sentence (no LLM padding)."""
    if state.status != "ok":
        return {}
    out = {}
    if not state.matchup_risk:
        out["matchup_risk"] = NO_ITEM
    if not state.non_obvious:
        out["non_obvious"] = NO_ITEM
    return out


def rendered(items: list[GraphInsight], sections: dict[str, str]) -> list[GraphInsight]:
    """The picks the final prose actually used (Sol review: a pick the writer left out, e.g.
    trimmed for length, must not be logged as published). An item counts when its section
    names its subject player (full or last name), or, for a team-level item, both teams."""
    from nflengine.digest.names import last_name, nickname

    out = []
    for i in items:
        text = sections.get(i.section, "")
        subjects = [p.player for p in i.people if p.role in SUBJECT_ROLES]
        if subjects:
            hit = any(n in text or last_name(n) in text for n in subjects)
        else:
            hit = all(nickname(t) in text for t in i.teams[:2])
        if hit:
            out.append(i)
    return out


def record_published(
    ctx: DigestContext,
    state: GraphState,
    log: Callable[[str], None] = print,
    sections: dict[str, str] | None = None,
) -> int:
    """Append the published picks to the log; in a live run also add them to the graph when
    it's reachable (the next rebuild reloads them from the log either way)."""
    from nflengine.graph.published import log_path, record, write_nodes

    if state.status != "ok":
        return 0
    picked = state.picked if sections is None else rendered(state.picked, sections)
    dropped = {i.insight_id for i in state.picked} - {i.insight_id for i in picked}
    picked = [*picked, *state.more]  # "More from the graph" is written by code: always shown
    if dropped:
        log(f"[yellow]graph items not in the prose (not logged as published): {sorted(dropped)}[/]")
    record(log_path(ctx.run_root), ctx.season, ctx.week, picked, ctx.run_time)
    if not picked or ctx.mode != "live":
        # a backtest's graph is gone by the time the next week is built: the log is its record
        return len(picked)
    try:
        from nflengine.graph.client import get_driver

        driver = get_driver()
        try:
            write_nodes(driver, ctx.season, ctx.week, picked, ctx.run_time)
        finally:
            driver.close()
    except Exception as e:  # the log is the source of truth; the next rebuild reloads it
        log(f"[yellow]PublishedInsight nodes not written ({type(e).__name__}); log updated[/]")
    return len(picked)
