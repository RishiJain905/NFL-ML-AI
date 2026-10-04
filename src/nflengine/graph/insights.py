"""Graph insights for the digest (plan P05; documentation/05 -> Query library, 06 -> Payload).

Turns query-library rows into digest items and picks the week's few:

1. **Candidates**: each row becomes a `GraphInsight` (`digest/payload.py`) whose text is
   written by code: a headline without numbers plus `facts`, each a complete,
   time-scoped phrase with its numbers (made by `digest/format.py`) and the entities that
   own them. The LLM copies phrases; it never derives an order, a direction or a tier
   (D53, the P04 fact-check lesson).
2. **Score**: the query's `strength`, +0.1 when a followed team plays in the game (capped
   at 1).
3. **Filter**: games that kicked off before the run are skipped; an insight published in
   the last 3 weeks of the season (a `PublishedInsight` with the same `insight_id`) is
   skipped (novelty); a player is the subject of one item at most.
4. **Pick**: the top 1 for *Matchup / risk to watch* (injury ripple, QB change; a trend
   mismatch if neither exists), the top 1-2 for *Non-obvious insights* (revenge, common
   opponents, trend mismatch), from different games, preferring two different kinds.

`insight_id`s are stable across weeks for the same story (`injury_ripple:<starter>`,
`revenge:<player>:<opponent>`, `qb_change:<team>:<qb>`, `common_opponents:<game>`,
`trend_mismatch:<game>`), so novelty catches a repeat.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from nflengine.digest import format as F
from nflengine.digest.names import nickname
from nflengine.digest.payload import GraphFact, GraphInsight, GraphPerson

NOVELTY_WEEKS = 3
FOLLOWED_BOOST = 0.1
SECOND_PICK_MIN = 0.45  # a second non-obvious item only when it's reasonably strong
LOW_SAMPLE = 4  # games behind a with / without comparison below this -> low confidence
GRAPH_SINCE = 2018  # first season in the graph (seasons.graph_start)

SECTION_TYPES = {
    "matchup_risk": ("injury_ripple", "qb_change"),
    "non_obvious": ("revenge", "common_opponents", "trend_mismatch"),
}
QUERY_OF = {
    "revenge": "q1_revenge",
    "injury_ripple": "q2_injury_ripple",
    "qb_change": "q3_qb_change",
    "common_opponents": "q4_common_opponents",
    "trend_mismatch": "q8_trend_mismatch",
}
# people roles that make a player the subject of an item (used once per digest)
SUBJECT_ROLES = ("out", "former player", "expected starter")


@dataclass
class GameInfo:
    game_id: str
    home: str
    away: str
    kickoff: dt.datetime | None = None
    neutral: bool = False

    @property
    def matchup(self) -> str:
        from nflengine.digest.build import matchup

        return matchup(self.home, self.away, self.neutral)


def _games_word(n: int) -> str:
    return "game" if n == 1 else "games"


def _seasons_text(seasons: Iterable[int]) -> str:
    ss = sorted({int(s) for s in seasons})
    if len(ss) == 1:
        return str(ss[0])
    return ", ".join(str(s) for s in ss[:-1]) + f" and {ss[-1]}"


def _the(team: str) -> str:
    return f"the {nickname(team)}"


def _poss(team: str) -> str:
    """'the 49ers'' / 'the Chiefs'' (plural nicknames take a bare apostrophe)."""
    nick = nickname(team)
    return f"the {nick}'" if nick.endswith("s") else f"the {nick}'s"


# ---- one converter per query --------------------------------------------------------------------


def _who(name: str, team: str, pos: str | None) -> str:
    """'Nick Bosa (49ers DE)', or 'Nick Bosa (49ers)' without a position."""
    return f"{name} ({nickname(team)} {pos})" if pos else f"{name} ({nickname(team)})"


def _injury_ripple(r: dict[str, Any], season: int) -> GraphInsight | None:
    team, s_name, s_id = r["team"], r["starter"], r["starter_id"]
    pos = r.get("position") or ""
    n_wo, n_w = int(r["n_without"]), int(r["n_with"])
    status = (r.get("status") or "Out").lower()
    body = (r.get("body_part") or "").lower()
    who = _who(s_name, team, pos)
    if r["out_source"] == "this_week":
        head = f"{who} is listed {status} for this week" + (f" ({body})." if body else ".")
    elif r["out_source"] == "reserve":
        head = f"{who} is on a reserve list and missed the team's last game."
    else:
        head = (
            f"{who} was ruled out of the team's last game and didn't play; this week's "
            "injury report isn't out yet."
        )
    facts: list[GraphFact] = []
    tw, two = r.get("team_epa_with"), r.get("team_epa_without")
    since = season - 2
    if tw is not None and two is not None:
        e_wo, e_w = F.epa(two), F.epa(tw)
        if r.get("defense"):
            unit, worse = (
                f"the {nickname(team)} defense allowed",
                two > tw,
            )  # allowed: lower = better
        else:
            unit, worse = f"the {nickname(team)} offense averaged", two < tw
        verdict = "worse without him" if worse else "better without him"
        if abs(two - tw) < 0.02:
            verdict = "about the same either way"
        facts.append(
            GraphFact(
                text=(
                    f"In {n_wo} {_games_word(n_wo)} without {s_name} since the start of the "
                    f"{since} season, {unit} {e_wo.display}, against {e_w.display} in {n_w} "
                    f"{_games_word(n_w)} he started: {verdict}"
                ),
                owners=[team, s_id],
            )
        )
    people = [GraphPerson(player=s_name, player_id=s_id, team=team, role="out")]
    b = r.get("backup")
    if b and r.get("backup_use_without") is not None:
        nb_wo, nb_w = int(r["backup_n_without"]), int(r["backup_n_with"])
        grp = r.get("position_group")
        if grp in ("WR", "TE", "RB"):
            unit = "targets" if grp in ("WR", "TE") else "carries plus targets"
            u_wo = F.per_game(r["backup_use_without"], unit).display
            u_w = F.per_game(r["backup_use_with"], unit).display
        else:
            side = "offensive" if grp == "OL" else "defensive"
            u_wo = f"{F.share(r['backup_use_without']).display} of {side} snaps"
            u_w = f"{F.share(r['backup_use_with']).display} of {side} snaps"
        facts.append(
            GraphFact(
                text=(
                    f"{b} had {u_wo} in {nb_wo} {_games_word(nb_wo)} without {s_name}, "
                    f"against {u_w} in {nb_w} {_games_word(nb_w)} with him"
                ),
                owners=[r["backup_id"], s_id],
            )
        )
        people.append(GraphPerson(player=b, player_id=r["backup_id"], team=team, role="stepped in"))
    nxt = r.get("chart_next")
    if nxt and r.get("chart_next_id") != r.get("backup_id"):
        facts.append(
            GraphFact(
                text=f"{nxt} is next behind {s_name} on {_poss(team)} latest depth chart",
                owners=[r["chart_next_id"], s_id, team],
            )
        )
        people.append(
            GraphPerson(player=nxt, player_id=r["chart_next_id"], team=team, role="next on chart")
        )
    if not facts:
        return None
    return GraphInsight(
        insight_id=f"injury_ripple:{s_id}",
        insight_type="injury_ripple",
        section="matchup_risk",
        strength=float(r["strength"]),
        confidence="low" if n_wo < LOW_SAMPLE else "medium",
        graph_query=QUERY_OF["injury_ripple"],
        game_id=r["game_id"],
        teams=[team, r["opponent"]],
        people=people,
        headline=head,
        facts=facts,
        sample=F.text_num(f"{n_wo} {_games_word(n_wo)} without him", n_wo),
    )


def _revenge(r: dict[str, Any], season: int) -> GraphInsight:
    team, opp, name, pid = r["team"], r["opponent"], r["player"], r["player_id"]
    seasons = _seasons_text(r.get("seasons_with_opp") or [])
    games_old, games_now = int(r["games_with_opp"]), int(r["games_now"])
    snap = F.share(float(r["snap_pct"])).display
    side = {"defense": "defensive ", "offense": "offensive "}.get(r.get("snap_side") or "", "")
    facts = [
        GraphFact(
            text=(
                f"{name} played {games_old} {_games_word(games_old)} for the {nickname(opp)} "
                f"in {seasons}"
            ),
            owners=[pid, opp],
        ),
        GraphFact(
            text=(
                f"This season {name} has played {snap} of {_poss(team)} {side}snaps in the "
                f"{games_now} {_games_word(games_now)} he played"
            ),
            owners=[pid, team],
        ),
    ]
    if r.get("drafted_year"):
        facts.append(
            GraphFact(
                text=(
                    f"The {nickname(opp)} drafted {name} in round {int(r['drafted_round'])} of "
                    f"the {int(r['drafted_year'])} draft"
                ),
                owners=[pid, opp],
            )
        )
    if r.get("traded_on"):
        d = dt.date.fromisoformat(str(r["traded_on"])[:10])
        facts.append(
            GraphFact(
                text=(
                    f"The {nickname(opp)} traded {name} to the {nickname(team)} on "
                    f"{d.strftime('%B')} {d.day}, {d.year}"
                ),
                owners=[pid, opp, team],
            )
        )
    return GraphInsight(
        insight_id=f"revenge:{pid}:{opp}",
        insight_type="revenge",
        section="non_obvious",
        strength=float(r["strength"]),
        confidence="medium",
        graph_query=QUERY_OF["revenge"],
        game_id=r["game_id"],
        teams=[team, opp],
        people=[GraphPerson(player=name, player_id=pid, team=team, role="former player")],
        headline=(
            f"{_who(name, team, r.get('position'))} faces the {nickname(opp)}, his former team."
        ),
        facts=facts,
        sample=F.text_num(f"{games_old} {_games_word(games_old)} with them", games_old),
    )


def _qb_change(r: dict[str, Any], season: int) -> GraphInsight:
    team, qb, reg = r["team"], r["qb"], r["regular"]
    qid, rid = r["qb_id"], r["regular_id"]
    rs, tg = int(r["r_starts"]), int(r["team_games"])
    q_now, q_tot = int(r["q_starts_now"]), int(r["q_starts_total"])
    confirmed = r.get("qb_confirmed", True) is not False
    status = (r.get("regular_status") or "").lower()
    if confirmed:
        head = f"{qb} is expected to start at QB for the {nickname(team)} this week"
        if status in ("out", "doubtful"):
            body = (r.get("regular_body_part") or "").lower()
            head += f"; {reg} is listed {status} for this week" + (f" ({body})" if body else "")
        head += "."
    else:
        # only the Tuesday rule: say it isn't confirmed (2025 w9: Cousins filled in for one
        # game, and Penix was back; fact-check)
        why = (
            "he started their last game"
            if r.get("qb_source") == "last_game"
            else "no injury report or depth chart for this week yet"
        )
        head = (
            f"{qb} could start at QB for the {nickname(team)} this week ({why}), but it isn't "
            "confirmed yet."
        )
    facts = [
        GraphFact(
            text=f"{reg} started {rs} of {_poss(team)} {tg} games this season",
            owners=[rid, team],
        )
    ]
    if q_now:
        txt = f"{qb} started {q_now} of them"
    else:
        txt = f"{qb} hasn't started a game for the {nickname(team)} this season"
    rookie = r.get("qb_rookie_season")
    if q_tot:
        txt += f"; he has {q_tot} NFL {'start' if q_tot == 1 else 'starts'} since {GRAPH_SINCE}"
    elif rookie is not None and int(rookie) >= GRAPH_SINCE:
        txt += "; he has never started an NFL game"
    else:
        txt += f"; he has no NFL start since {GRAPH_SINCE}"
    facts.append(GraphFact(text=txt, owners=[qid, team]))
    people = [
        GraphPerson(player=qb, player_id=qid, team=team, role="expected starter"),
        GraphPerson(player=reg, player_id=rid, team=team, role="main starter"),
    ]
    receivers = sorted(
        r.get("receivers") or [], key=lambda x: (-int(x["r_targets"]), -int(x["q_targets"]))
    )
    for w in receivers[:3]:
        rt, qt = int(w["r_targets"]), int(w["q_targets"])
        # one clause per QB: each "; " clause's numbers belong to the QB it names (the
        # receiver owns neither), so "Ben Backup targeted him 14 times" can't borrow the
        # main starter's 14 (Sol review)
        part = f"{w['name']} has {rt} targets from {reg} this season; "
        part += f"he has {qt} from {qb} since {GRAPH_SINCE}" if qt else f"he has none from {qb}"
        facts.append(GraphFact(text=part, owners=[rid, qid]))
        people.append(
            GraphPerson(player=w["name"], player_id=w["player_id"], team=team, role="receiver")
        )
    qt_total = int(r.get("q_targets_total") or 0)
    return GraphInsight(
        insight_id=f"qb_change:{team}:{qid}",
        insight_type="qb_change",
        section="matchup_risk",
        strength=float(r["strength"]),
        confidence="low" if qt_total < 20 or not confirmed else "medium",
        graph_query=QUERY_OF["qb_change"],
        game_id=r["game_id"],
        teams=[team, r["opponent"]],
        people=people,
        headline=head,
        facts=facts,
        sample=F.text_num(
            f"{qt_total} career targets from {qb} to {_poss(team)} current receivers", qt_total
        ),
        # the low confidence is about the receiver history, not the start (fact-check)
        note=(
            "Unconfirmed: the starter isn't known until this week's injury report."
            if not confirmed
            else f"A small sample: {qb} has {qt_total} career "
            f"{'target' if qt_total == 1 else 'targets'} to {_poss(team)} current receivers, "
            "so their history together says little."
            if qt_total < 20
            else ""
        ),
    )


def _margin_phrase(team: str, games: list[dict[str, Any]], opp: str) -> str:
    """'the Bucs lost to the Vikings by 7 in week 3' (one game), or the average of several."""
    if len(games) == 1:
        m, wk = int(games[0]["margin"]), int(games[0]["week"])
        if m > 0:
            return f"{_the(team)} beat the {nickname(opp)} by {m} in week {wk}"
        if m < 0:
            return f"{_the(team)} lost to the {nickname(opp)} by {abs(m)} in week {wk}"
        return f"{_the(team)} tied the {nickname(opp)} in week {wk}"
    avg = sum(float(g["margin"]) for g in games) / len(games)
    if round(avg, 1) == 0:
        return f"{_the(team)} and the {nickname(opp)} came out even over {len(games)} games"
    disp = F.per_game(abs(avg), "points").display
    verb = "outscored the" if avg > 0 else "were outscored by the"
    return f"{_the(team)} {verb} {nickname(opp)} by {disp} over {len(games)} games"


def _common_opponents(r: dict[str, Any], season: int) -> GraphInsight | None:
    a, b = r["team"], r["opponent"]  # a = home
    common = r.get("common") or []
    if not common:
        return None
    names = [nickname(c["team"]) for c in common]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
    facts: list[GraphFact] = []
    for c in common[:3]:
        # one fact per shared opponent, so a budget cut never keeps half a comparison; each
        # "; " clause's margin belongs to the team it names (facts.py's clause rule), and the
        # shared opponent owns neither, so "the Colts beat the Broncos by 3" can't borrow the
        # Chargers' 3
        pa = _margin_phrase(a, c["a_games"], c["team"])
        pb = _margin_phrase(b, c["b_games"], c["team"])
        facts.append(GraphFact(text=f"{pa[0].upper()}{pa[1:]}; {pb}", owners=[a, b]))
    edge = float(r["edge"])
    if abs(edge) >= 1:
        better, worse = (a, b) if edge > 0 else (b, a)
        facts.append(
            GraphFact(
                text=(
                    f"Against those common opponents, the {nickname(better)} have been "
                    f"{F.per_game(abs(edge), 'points').display} better than the "
                    f"{nickname(worse)} on average"
                ),
                owners=[a, b],
            )
        )
    mf, cf = r.get("model_favorite"), r.get("chain_favorite")
    if mf and cf and mf != cf:
        facts.append(
            GraphFact(text=f"The game model still favors the {nickname(mf)}", owners=[a, b])
        )
    n = int(r["n_common"])
    return GraphInsight(
        insight_id=f"common_opponents:{r['game_id']}",
        insight_type="common_opponents",
        section="non_obvious",
        strength=float(r["strength"]),
        confidence="low" if n < LOW_SAMPLE else "medium",
        graph_query=QUERY_OF["common_opponents"],
        game_id=r["game_id"],
        teams=[a, b, *[c["team"] for c in common]],
        headline=(
            f"The {nickname(a)} and the {nickname(b)} have both already played the {listed} "
            "this season."
        ),
        facts=facts,
        sample=F.text_num(f"{n} common {'opponent' if n == 1 else 'opponents'}", n),
        note="A small sample: a few games against shared opponents.",
    )


def _trend_mismatch(r: dict[str, Any], season: int) -> GraphInsight:
    a, b = r["team"], r["opponent"]
    up, down = (a, b) if r["team_direction"] == "up" else (b, a)
    d_up = float(r["team_delta"] if up == a else r["opponent_delta"])
    d_down = float(r["team_delta"] if down == a else r["opponent_delta"])
    n = int(r.get("window_weeks") or 3)
    return GraphInsight(
        insight_id=f"trend_mismatch:{r['game_id']}",
        insight_type="trend_mismatch",
        section="non_obvious",
        strength=float(r["strength"]),
        confidence="low",
        graph_query=QUERY_OF["trend_mismatch"],
        game_id=r["game_id"],
        teams=[a, b],
        headline=(
            f"The {nickname(up)} are trending up and the {nickname(down)} are trending down "
            "going into this game."
        ),
        facts=[
            GraphFact(
                text=f"{_poss(up)[0].upper()}{_poss(up)[1:]} net rating is "
                f"{F.epa_change(d_up, n).display}",
                owners=[up],
            ),
            GraphFact(
                text=f"{_poss(down)[0].upper()}{_poss(down)[1:]} net rating is "
                f"{F.epa_change(d_down, n).display}",
                owners=[down],
            ),
        ],
        sample=F.weeks(n),
        note="Descriptive: trends don't predict the next game on their own.",
    )


CONVERTERS: dict[str, Callable[[dict[str, Any], int], GraphInsight | None]] = {
    "q1_revenge": _revenge,
    "q2_injury_ripple": _injury_ripple,
    "q3_qb_change": _qb_change,
    "q4_common_opponents": _common_opponents,
    "q8_trend_mismatch": _trend_mismatch,
}


# ---- candidates and selection --------------------------------------------------------------------


def candidates(
    results: dict[str, list[dict[str, Any]]],
    season: int,
    games: dict[str, GameInfo] | None = None,
) -> list[GraphInsight]:
    """Every query row as a payload item (with its code-made matchup when the game is known)."""
    games = games or {}
    out: list[GraphInsight] = []
    for name, rows in results.items():
        conv = CONVERTERS.get(name)
        if conv is None:
            continue
        for r in rows:
            ins = conv(r, season)
            if ins is None:
                continue
            g = games.get(ins.game_id)
            if g is not None:
                ins = ins.model_copy(update={"matchup": g.matchup})
            out.append(ins)
    return out


@dataclass
class Selection:
    matchup_risk: list[GraphInsight] = field(default_factory=list)
    non_obvious: list[GraphInsight] = field(default_factory=list)
    skipped: dict[str, list[str]] = field(default_factory=dict)  # reason -> insight ids

    @property
    def picked(self) -> list[GraphInsight]:
        return [*self.matchup_risk, *self.non_obvious]


def _subjects(c: GraphInsight) -> set[str]:
    return {p.player_id for p in c.people if p.role in SUBJECT_ROLES}


def select(
    cands: list[GraphInsight],
    games: dict[str, GameInfo],
    run_time: dt.datetime,
    *,
    followed: Iterable[str] = (),
    recent_ids: Iterable[str] = (),
    kickoff_margin: dt.timedelta = dt.timedelta(0),
) -> Selection:
    """Score, filter and pick the week's graph insights (see the module docstring).
    `kickoff_margin`: also skip games kicking off within this long after `run_time` (a live
    digest can take a while to write; Sol review)."""
    followed, recent = set(followed), set(recent_ids)
    sel = Selection(skipped={"started": [], "novelty": [], "duplicate_player": []})
    pool: list[GraphInsight] = []
    for c in cands:
        g = games.get(c.game_id)
        if g is not None and g.kickoff is not None and g.kickoff <= run_time + kickoff_margin:
            sel.skipped["started"].append(c.insight_id)
            continue
        if c.insight_id in recent:
            sel.skipped["novelty"].append(c.insight_id)
            continue
        boost = FOLLOWED_BOOST if followed & set(c.teams[:2]) else 0.0
        pool.append(c.model_copy(update={"strength": min(1.0, round(c.strength + boost, 3))}))
    pool.sort(key=lambda c: (-c.strength, c.insight_id))

    used: set[str] = set()
    risk = [c for c in pool if c.insight_type in SECTION_TYPES["matchup_risk"]]
    if not risk:  # no injury or QB story: a trend mismatch is the risk angle
        risk = [c for c in pool if c.insight_type == "trend_mismatch"]
    if risk:
        sel.matchup_risk.append(risk[0].model_copy(update={"section": "matchup_risk"}))
        used |= _subjects(risk[0])
    taken = {c.insight_id for c in sel.matchup_risk}
    games_used = {c.game_id for c in sel.matchup_risk}
    others = [
        c
        for c in pool
        if c.insight_type in SECTION_TYPES["non_obvious"] and c.insight_id not in taken
    ]
    for c in others:
        if len(sel.non_obvious) >= 2:
            break
        if _subjects(c) & used:
            sel.skipped["duplicate_player"].append(c.insight_id)
            continue
        if sel.non_obvious:
            first = sel.non_obvious[0]
            if c.strength < SECOND_PICK_MIN or c.game_id in games_used:
                continue
            alt = any(
                o.insight_type != first.insight_type
                and o.strength >= SECOND_PICK_MIN
                and o.game_id not in games_used
                and not (_subjects(o) & used)
                for o in others
            )
            if c.insight_type == first.insight_type and alt:
                continue  # prefer a different kind of insight in the second slot
        sel.non_obvious.append(c.model_copy(update={"section": "non_obvious"}))
        used |= _subjects(c)
        games_used.add(c.game_id)
    return sel
