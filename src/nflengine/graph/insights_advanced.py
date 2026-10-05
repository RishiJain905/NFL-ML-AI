"""Converters for the P08 graph queries: former teammates (Q6), style matchups (Q7),
officiating crews (Q9), the coaching tree from the optional seed (Q5) and the two
GDS-driven stories (Q10: a passing-network hub who won't play; a usage comparison from
k-nearest neighbors).

Same contract as `graph/insights.py` and `graph/insights_extra.py`: `(row, season) ->
GraphInsight | None`, a code-written headline, facts that are complete, time-scoped
phrases with their owners (one claim's owners per fact), a one-line `brief`, `sample`,
`confidence`, a stable `insight_id`. Every number comes from `digest/format.py`; every
direction, tier and comparison word is decided here (D53). A GDS comparison is always
worded as a comparison, never as a prediction.

`graph/insights.py` imports this module at its very end, after the helpers imported below
exist.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from nflengine.digest import format as F
from nflengine.digest.names import nickname
from nflengine.digest.payload import GraphFact, GraphInsight, GraphPerson
from nflengine.graph.insights import _games_word, _poss, _seasons_text, _the, _who
from nflengine.graph.insights_extra import _cap

QUERY_OF = {
    "former_teammates": "q6_former_teammates",
    "style_matchup": "q7_style_matchup",
    "play_action": "q7_play_action",
    "officiating": "q9_officiating",
    "coaching_tree": "q5_coaching_tree",
    "network_hub": "q10_network_hub",
    "usage_comp": "q10_usage_comp",
}
FT_LOW_TARGETS = 60  # former teammates: fewer targets together -> a small connection
STYLE_LOW_GAMES = 8  # style split over fewer games against the style -> low confidence
HUB_LOW_GAMES = 4  # a passing network over fewer team games -> low confidence
COMP_LOW_GAMES = 4  # a usage profile over fewer games -> low confidence


def _teams_text(teams: list[str]) -> str:
    """'the Vikings' / 'the Vikings and the Falcons'."""
    names = [_the(t) for t in dict.fromkeys(teams)]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _rate(p: float) -> str:
    """A style rate with one decimal ("12.7%"): whole percents make a QB at 7.2% read the
    same as the 6.8% cut."""
    return f"{math.floor(1000 * p + 0.5) / 10:.1f}%"


def _times(n: int) -> str:
    return "once" if n == 1 else f"{n} times"


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {many or word + 's'}"


# ---- Q6: former teammates on opposite sides ------------------------------------------------------


def _former_teammates(r: dict[str, Any], season: int) -> GraphInsight:
    team, opp = r["team"], r["opponent"]
    qb, qid = r["qb"], r["qb_id"]
    rec, rid = r["receiver"], r["receiver_id"]
    pos = r.get("receiver_position") or ""
    tg, ct, yd, td = int(r["targets"]), int(r["catches"]), int(r["yards"]), int(r["tds"])
    seasons = _seasons_text(r.get("seasons") or [])
    where = _teams_text(list(r.get("teams_together") or []))
    games_now = int(r["games_now"])
    share = float(r.get("target_share_now") or 0.0)
    facts = [
        GraphFact(
            text=(
                f"{qb} targeted {rec} {_times(tg)} for {where} in {seasons}: "
                f"{_plural(ct, 'catch', 'catches')}, {yd:,} {'yard' if yd == 1 else 'yards'} and "
                f"{_plural(td, 'touchdown')}"
            ),
            owners=[qid, rid],
        ),
        GraphFact(
            text=(
                f"This season {rec} has had {F.share(share).display} of {_poss(opp)} targets "
                f"in the {games_now} {_games_word(games_now)} he played for them"
            ),
            owners=[rid, opp],
        ),
    ]
    low = tg < FT_LOW_TARGETS
    return GraphInsight(
        insight_id=f"former_teammates:{qid}:{rid}",
        insight_type="former_teammates",
        section="non_obvious",
        strength=float(r["strength"]),
        confidence="low" if low else "medium",
        graph_query=QUERY_OF["former_teammates"],
        game_id=r["game_id"],
        teams=[team, opp],
        people=[
            GraphPerson(player=qb, player_id=qid, team=team, role="former teammate"),
            GraphPerson(player=rec, player_id=rid, team=opp, role="former teammate"),
        ],
        headline=(
            f"{_who(qb, team, 'QB')} faces {_who(rec, opp, pos)}, a receiver he used to throw to."
        ),
        facts=facts,
        sample=F.text_num(f"{tg} targets from {qb} to {rec}", tg),
        note=(
            f"A small sample: {qb} targeted {rec} only {_times(tg)}, so their history says little."
            if low
            else ""
        ),
        brief=(
            f"{qb} ({nickname(team)} QB) faces {rec} ({nickname(opp)}), whom he targeted "
            f"{_times(tg)} for {where} in {seasons}."
        ),
    )


# ---- Q7: style matchups --------------------------------------------------------------------------

STYLE_WORDS = {
    # style -> (the group, "against <group>", the rest)
    "scramble": ("scrambling QBs", "other QBs"),
    "deep": ("deep-passing QBs", "other QBs"),
    "play_action": ("play-action-heavy offenses", "other offenses"),
}


def _style_insight(
    r: dict[str, Any],
    *,
    insight_type: str,
    style: str,
    value: GraphFact,
    subject: str,
    who: str,
    ident: str,
    people: list[GraphPerson],
) -> GraphInsight:
    """The shared part of a style matchup: the defense's split against the style, the
    league's, and the verdict word (the QB / offense fact comes from the caller)."""
    d = r["team"]
    group, rest = STYLE_WORDS[style]
    since = int(r["since"])
    n_s, n_o = int(r["n_style"]), int(r["n_other"])
    gap = float(r["gap"])
    worse = gap > 0  # EPA allowed: higher = worse
    verdict = "worse than usual" if worse else "better than usual"
    d_s, d_o = F.epa(float(r["def_epa_style"])), F.epa(float(r["def_epa_other"]))
    l_s, l_o = F.epa(float(r["lg_epa_style"])), F.epa(float(r["lg_epa_other"]))
    facts = [
        value,
        GraphFact(
            text=(
                f"Since the start of the {since} season the {nickname(d)} defense allowed "
                f"{d_s.display} in {n_s} {_games_word(n_s)} against {group}, against "
                f"{d_o.display} in {n_o} {_games_word(n_o)} against {rest}"
            ),
            owners=[d],
        ),
        GraphFact(
            text=(
                f"Over the same span all defenses allowed {l_s.display} against {group} and "
                f"{l_o.display} against {rest}: the {nickname(d)} defense has done {verdict} "
                f"against {group}"
            ),
            owners=[d],
        ),
    ]
    head = (
        f"{subject}, and the {nickname(d)} defense has fared {verdict} against "
        f"{group} in recent seasons."
    )
    low = n_s < STYLE_LOW_GAMES
    return GraphInsight(
        insight_id=f"{insight_type}:{d}:{style}:{ident}",
        insight_type=insight_type,  # type: ignore[arg-type]
        section="non_obvious",
        strength=float(r["strength"]),
        confidence="low" if low else "medium",
        graph_query=QUERY_OF[insight_type],
        game_id=r["game_id"],
        teams=[d, r["opponent"]],
        people=people,
        headline=head,
        facts=facts,
        sample=F.text_num(f"{n_s} {_games_word(n_s)} against {group} since {since}", n_s),
        note=(
            "Descriptive: a style split over a few seasons of games; opponents differ in many "
            "other ways, so it isn't predictive on its own."
        ),
        brief=(
            f"The {nickname(d)} defense allowed {d_s.display} in {n_s} "
            f"{_games_word(n_s)} against {group} since {since} ({d_o.display} against "
            f"{rest}); it faces {who} this week."
        ),
    )


def _style_matchup(r: dict[str, Any], season: int) -> GraphInsight:
    """Q7, QB styles: a scrambling or deep-passing QB vs this defense's split."""
    style, o = r["style"], r["opponent"]
    qb, qid, db = r["qb"], r["qb_id"], int(r["qb_dropbacks"])
    v, cut = float(r["qb_value"]), float(r["cut"])
    if style == "scramble":
        text = (
            f"{qb} has scrambled on {_rate(v)} of his dropbacks this season and last "
            f"({db} dropbacks); the quarter of QBs who scramble most do so on "
            f"{_rate(cut)} or more"
        )
        subject = f"{_who(qb, o, 'QB')} is one of the league's most frequent scramblers"
    else:
        text = (
            f"{qb} has thrown {F.yards(v, unit='air yards').display} per pass attempt this "
            f"season and last ({db} dropbacks); the quarter of QBs who throw deepest average "
            f"{F.yards(cut, unit='air yards').display} or more"
        )
        subject = f"{_who(qb, o, 'QB')} is one of the league's deepest passers"
    return _style_insight(
        r,
        insight_type="style_matchup",
        style=style,
        value=GraphFact(text=text, owners=[qid]),
        subject=subject,
        who=qb,
        ident=qid,
        people=[GraphPerson(player=qb, player_id=qid, team=o, role="opposing QB")],
    )


def _play_action(r: dict[str, Any], season: int) -> GraphInsight:
    """Q7, play action: a play-action-heavy offense (FTN) vs this defense's split."""
    o = r["opponent"]
    n = int(r["offense_games"])
    text = (
        f"{_cap(_the(o))} have used play action on {_rate(float(r['offense_rate']))} "
        f"of their dropbacks this season ({n} {_games_word(n)} charted by FTN); the quarter of "
        f"offenses that use it most do so on {_rate(float(r['cut']))} or more"
    )
    return _style_insight(
        r,
        insight_type="play_action",
        style="play_action",
        value=GraphFact(text=text, owners=[o]),
        subject=f"{_cap(_the(o))} use play action more than most offenses",
        who=f"the {nickname(o)} offense",
        ident=o,
        people=[],
    )


# ---- Q9: officiating crew ------------------------------------------------------------------------


def _more_fewer(x: float) -> str:
    return "more" if x > 0 else "fewer"


def _officiating(r: dict[str, Any], season: int) -> GraphInsight:
    home, away = r["team"], r["opponent"]
    ref, oid = r["referee"], f"official:{r['official_id']}"
    n = int(r["ref_games"])
    since = int(r["first_season"])
    pen_gap, split_gap = float(r["pen_gap"]), float(r["split_gap"] or 0.0)
    facts = [
        GraphFact(
            text=(
                f"In the {n} games {ref} refereed since the start of the {since} season, the "
                f"two teams drew {F.per_game(float(r['ref_pen']), 'penalties').display} and "
                f"{F.per_game(float(r['ref_yds']), 'penalty yards').display}, against a league "
                f"average of {F.per_game(float(r['lg_pen']), 'penalties').display} and "
                f"{F.per_game(float(r['lg_yds']), 'penalty yards').display}"
            ),
            owners=[oid],
        )
    ]
    split_story = abs(split_gap) >= abs(pen_gap) / 1.5
    if r.get("ref_split") is not None and r.get("lg_split") is not None:
        rs, ls = float(r["ref_split"]), float(r["lg_split"])
        facts.append(
            GraphFact(
                text=(
                    f"In his games away from neutral sites, home teams drew "
                    f"{F.per_game(abs(rs), 'penalties').display} {_more_fewer(rs)} than "
                    f"visiting teams, against {F.per_game(abs(ls), 'penalties').display} "
                    f"{_more_fewer(ls)} league-wide"
                ),
                owners=[oid],
            )
        )
    if split_story:
        lean = "visiting" if split_gap < 0 else "home"
        tail = f"have flagged {lean} teams more often than usual"
    else:
        tail = f"have called {_more_fewer(pen_gap)} penalties than average"
    return GraphInsight(
        insight_id=f"officiating:{r['official_id']}",
        insight_type="officiating",
        section="non_obvious",
        strength=float(r["strength"]),
        confidence="low",
        graph_query=QUERY_OF["officiating"],
        game_id=r["game_id"],
        teams=[home, away],
        people=[GraphPerson(player=ref, player_id=oid, team="", role="referee")],
        headline=f"{ref} referees this game; his crews {tail}.",
        facts=facts,
        sample=F.text_num(f"{n} {_games_word(n)} refereed since {since}", n),
        note=(
            "Descriptive: penalty counts swing a lot from game to game, so a crew's tendency is "
            "a small edge at most."
        ),
        brief=f"{ref} referees; {facts[0].text[0].lower()}{facts[0].text[1:]}.",
    )


# ---- Q5: coaching tree (optional seed) -----------------------------------------------------------

ROLE_WORDS = {
    "OC": "offensive coordinator",
    "DC": "defensive coordinator",
    "ST": "special teams coordinator",
    "HC": "head coach",
}


def _step_text(step: dict[str, Any]) -> str:
    seasons = _seasons_text(step.get("seasons") or [])
    teams = [t for t in step.get("teams") or [] if t]
    where = f" with {_teams_text(teams)}" if teams else ""
    return f"{step['from']} worked under {step['to']}{where} in {seasons}"


def _coaching_tree(r: dict[str, Any], season: int) -> GraphInsight | None:
    team, opp = r["team"], r["opponent"]
    steps = list(r.get("steps") or [])
    if not steps:
        return None
    coach, other = r["coach"], r["other"]
    people = [
        GraphPerson(player=coach, player_id=r["coach_id"], team=team, role="coach"),
        GraphPerson(player=other, player_id=r["other_id"], team=opp, role="coach"),
    ]
    facts = [GraphFact(text=_step_text(s), owners=[team, opp]) for s in steps]
    if r["kind"] == "coordinator_vs_boss":
        role = ROLE_WORDS.get(str(r.get("role") or "").upper(), "coordinator")
        head = (
            f"{coach}, {_poss(team)} {role}, faces {other}'s {nickname(opp)}: he used to "
            f"work under {other}."
        )
    elif len(steps) == 1:
        head = (
            f"Head coaches {coach} ({nickname(team)}) and {other} ({nickname(opp)}) share a "
            f"coaching tree: {steps[0]['from']} worked under {steps[0]['to']}."
        )
    else:
        mid = steps[0]["to"] if steps[0]["from"] == coach else steps[0]["from"]
        head = (
            f"Head coaches {coach} ({nickname(team)}) and {other} ({nickname(opp)}) are two "
            f"steps apart in one coaching tree, through {mid}."
        )
    n = int(r.get("sample_size") or 0)
    return GraphInsight(
        insight_id=f"coaching_tree:{r['kind']}:{r['coach_id']}:{r['other_id']}",
        insight_type="coaching_tree",
        section="non_obvious",
        strength=float(r["strength"]),
        confidence="medium",
        graph_query=QUERY_OF["coaching_tree"],
        game_id=r["game_id"],
        teams=[team, opp],
        people=people,
        headline=head,
        facts=facts,
        sample=F.text_num(f"{_plural(n, 'season')} together", n),
        brief=f"{_cap(_step_text(steps[0]))}; {coach} and {other} meet this week.",
    )


# ---- Q10 (GDS): passing-network hub out ----------------------------------------------------------


def _names_text(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def _network_hub(r: dict[str, Any], season: int) -> GraphInsight:
    team, opp = r["team"], r["opponent"]
    hub, hid = r["hub"], r["hub_id"]
    nxt, nid = r["next"], r["next_id"]
    pos = r.get("hub_position") or ""
    status = (r.get("status") or "Out").lower()
    body = (r.get("body_part") or "").lower()
    who = _who(hub, team, pos)
    src = r["out_source"]
    if src == "this_week":
        out = f"is listed {status} for this week" + (f" ({body})" if body else "")
    elif src == "reserve":
        out = "is on a reserve list and missed the team's last game"
    else:
        out = (
            "was ruled out of the team's last game and didn't play; this week's injury report "
            "isn't out yet"
        )
    rank = int(r.get("hub_rank") or 1)
    # rank 1 = the center of the network; else the n-th largest part of it
    place = (
        "the largest PageRank share of any receiver"
        if rank == 1
        else f"the {F.ordinal_str(rank)}-largest PageRank share among its receivers"
    )
    role = "the center" if rank == 1 else f"the {F.ordinal_str(rank)}-most central receiver"
    hub_share = F.share(float(r["hub_share"])).display
    games = int(r["hub_games"])
    tg = int(r["hub_targets"])
    n_tg = int(r["next_targets"])
    team_games = int(r["team_games"])
    also = list(r.get("also_out") or [])
    without = _names_text([hub, *also])
    facts = [
        GraphFact(
            text=(
                f"On {_poss(team)} passing network this season (who targeted whom, weighted by "
                f"targets), {hub} has {place}, {hub_share}, from {_plural(tg, 'target')} in "
                f"{games} {_games_word(games)}"
            ),
            owners=[hid, team],
        ),
        GraphFact(
            text=(
                f"Without {without} (out this week), {nxt} leads that network with "
                f"{F.share(float(r['next_share_without'])).display} of the PageRank share, up "
                f"from {F.share(float(r['next_share'])).display} with everyone in it "
                f"({_plural(n_tg, 'target')} so far this season)"
            ),
            owners=[nid, hid],
        ),
    ]
    return GraphInsight(
        insight_id=f"network_hub:{hid}",
        insight_type="network_hub",
        section="non_obvious",
        strength=float(r["strength"]),
        confidence="low" if team_games < HUB_LOW_GAMES else "medium",
        graph_query=QUERY_OF["network_hub"],
        game_id=r["game_id"],
        teams=[team, opp],
        people=[
            GraphPerson(player=hub, player_id=hid, team=team, role="out"),
            GraphPerson(player=nxt, player_id=nid, team=team, role="next in network"),
        ],
        headline=f"{who}, {role} of {_poss(team)} passing network, {out}.",
        facts=facts,
        sample=F.text_num(
            f"{team_games} {_games_word(team_games)} of {_poss(team)} passing network",
            team_games,
        ),
        note=(
            "Descriptive: PageRank on a passer-to-receiver network mostly follows how often each "
            "player is targeted; it shows who the passing game has gone through so far, not who "
            "will be targeted this week."
        ),
        brief=(
            f"{hub} ({nickname(team)}), {role} of their passing network this season "
            f"({hub_share} PageRank share), won't play; {nxt} leads it without him."
        ),
    )


# ---- Q10 (GDS): usage comparison -----------------------------------------------------------------

YARDS_WORD = {"WR": "receiving yards", "TE": "receiving yards", "RB": "scrimmage yards"}
GROUP_WORD = {"WR": "wide receivers", "TE": "tight ends", "RB": "running backs"}


def _usage_text(r: dict[str, Any], prefix: str, team: str) -> str:
    """'27% of the Ravens' targets, 33% of their air yards, 9.2 yards average depth of
    target, 17% of their red-zone targets and 87% of offensive snaps' (RB: carries first)."""

    def s(key: str) -> str | None:
        v = r.get(f"{prefix}{key}")
        return None if v is None else F.share(max(float(v), 0.0)).display

    parts: list[str] = []
    if r["grp"] == "RB":
        if s("carry_share"):
            parts.append(f"{s('carry_share')} of {_poss(team)} designed carries")
        if s("target_share"):
            parts.append(f"{s('target_share')} of their targets")
        if s("rz_share"):
            parts.append(f"{s('rz_share')} of their red-zone carries and targets")
    else:
        if s("target_share"):
            parts.append(f"{s('target_share')} of {_poss(team)} targets")
        if s("air_yards_share"):
            parts.append(f"{s('air_yards_share')} of their air yards")
        if r.get(f"{prefix}adot") is not None:
            adot = F.yards(float(r[f"{prefix}adot"]), unit="yards").display
            parts.append(f"an average depth of target of {adot}")
        if s("rz_share"):
            parts.append(f"{s('rz_share')} of their red-zone targets")
    if s("snap_share"):
        parts.append(f"{s('snap_share')} of offensive snaps")
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _usage_comp(r: dict[str, Any], season: int) -> GraphInsight:
    team, opp, grp = r["team"], r["opponent"], r["grp"]
    name, pid = r["player"], r["player_id"]
    other, oid, os_ = r["other"], r["other_id"], int(r["other_season"])
    ot = r.get("other_team") or ""
    games, o_games = int(r["games"]), int(r["other_games"])
    yards = int(r["other_scrimmage_yds"] if grp == "RB" else r["other_rec_yds"])
    rank = int(r["other_yds_rank"])
    pos = r.get("position") or grp
    who_grp = "running back" if grp == "RB" else pos
    # rank 1 reads "the most", never "1st-most" / "a top-1 season"
    most = "the most" if rank == 1 else f"{F.ordinal_str(rank)}-most"
    tier = (
        f"the most productive season by a {who_grp} that year"
        if rank == 1
        else f"a top-{rank} season for a {who_grp}"
    )
    facts = [
        GraphFact(
            text=(
                f"In {games} {_games_word(games)} this season, {name} has had "
                f"{_usage_text(r, '', team)}"
            ),
            owners=[pid, team],
        ),
        GraphFact(
            text=(
                f"In {os_}, {other} had {_usage_text(r, 'other_', ot)} over {o_games} "
                f"{_games_word(o_games)} for {_the(ot)}"
            ),
            owners=[oid],
        ),
        GraphFact(
            text=(
                f"{other} finished {os_} with {yards:,} {YARDS_WORD[grp]}, "
                f"{most} among {GROUP_WORD[grp]} that season"
            ),
            owners=[oid],
        ),
    ]
    young = bool(r.get("young"))
    return GraphInsight(
        insight_id=f"usage_comp:{pid}",  # one story per player, whoever he matches
        insight_type="usage_comp",
        section="non_obvious",
        strength=float(r["strength"]),
        confidence="low" if games < COMP_LOW_GAMES else "medium",
        graph_query=QUERY_OF["usage_comp"],
        game_id=r["game_id"],
        teams=[team, opp],
        people=[
            GraphPerson(player=name, player_id=pid, team=team, role="usage comparison"),
            GraphPerson(player=other, player_id=oid, team=ot, role="comparison"),
        ],
        headline=(
            f"{_who(name, team, pos)}"
            + (", in his first two seasons," if young else "")
            + f" is being used much like {other} was in {os_}, {tier}."
        ),
        facts=facts,
        sample=F.text_num(f"{games} {_games_word(games)} this season", games),
        note=(
            "A comparison of how he has been used so far (GDS nearest neighbors on usage "
            f"shares), not a projection: {other}'s {os_} totals say nothing certain about "
            "this season."
        ),
        brief=(
            f"{name} ({nickname(team)} {pos}): usage this season closest to {other}'s {os_} "
            f"({yards:,} {YARDS_WORD[grp]}, {most} among {GROUP_WORD[grp]}); a comparison, "
            "not a projection."
        ),
    )


CONVERTERS: dict[str, Callable[[dict[str, Any], int], GraphInsight | None]] = {
    "q5_coaching_tree": _coaching_tree,
    "q6_former_teammates": _former_teammates,
    "q7_style_matchup": _style_matchup,
    "q7_play_action": _play_action,
    "q9_officiating": _officiating,
    "q10_network_hub": _network_hub,
    "q10_usage_comp": _usage_comp,
}
