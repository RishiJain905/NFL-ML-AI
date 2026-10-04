"""Converters for the non-QB story queries added after P05: head coach vs a former team
(Q5), offense-vs-defense unit mismatches (Q11), special-teams edges (Q12).

`CONVERTERS` maps a library query name to a function `(row, season) -> GraphInsight | None`
with the same contract as `graph/insights.py`'s converters: code-written headline (no
numbers), facts with owners (one claim's owners per fact), a one-line `brief` with the key
number, `sample`, `confidence`, a stable `insight_id`. Every number comes from
`digest/format.py`; every order, tier and direction word is decided here (D53).

`graph/insights.py` imports this module at its very end, after the helpers imported below
exist.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nflengine.digest import format as F
from nflengine.digest.names import nickname
from nflengine.digest.payload import GraphFact, GraphInsight, GraphPerson
from nflengine.graph.insights import GRAPH_SINCE, _games_word, _poss, _seasons_text, _the

N_TEAMS = 32
MIDDLE_TIER = 8  # ranks 9..24 of 32 read "about league-average" (digest/build.py's SOFT_TIER)
PRIOR_HEAVY = 0.4  # a rating with more than this share of preseason prior -> low confidence
ST_LOW_GAMES = 6  # special-teams EPA over fewer games than this -> low confidence
ST_EVEN = 0.05  # |EPA per game| under this reads "about average"

QUERY_OF = {
    "coach_reunion": "q5_coach_reunion",
    "unit_mismatch": "q11_unit_mismatch",
    "special_teams": "q12_special_teams",
}


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _record(w: int, losses: int, t: int = 0) -> str:
    """'9-8' or '9-7-1' (a tie only when there was one)."""
    return f"{w}-{losses}-{t}" if t else f"{w}-{losses}"


def _n_seasons(n: int) -> str:
    return f"{n} season" if n == 1 else f"{n} seasons"


# ---- Q5: head coach vs a former team -----------------------------------------------------------


def _coach_reunion(r: dict[str, Any], season: int) -> GraphInsight:
    team, opp = r["team"], r["opponent"]
    coach, cid = r["coach"], r["coach_id"]
    seasons = sorted(int(s) for s in r.get("seasons_with_opp") or [])
    n = int(r["n_seasons"])
    rw, rl, rt = int(r["reg_w"]), int(r["reg_l"]), int(r["reg_t"])
    pw, pl_ = int(r["post_w"]), int(r["post_l"])
    vw, vl, vt = int(r["vs_w"]), int(r["vs_l"]), int(r["vs_t"])
    since = int(r["meetings_since"])
    when = _seasons_text(seasons)
    # the graph starts in GRAPH_SINCE: a stint that reaches it may have begun earlier, so
    # say "until <last season>" and scope the record to the graph's seasons
    cut = bool(seasons) and seasons[0] <= GRAPH_SINCE
    if cut:
        stint = f"{coach} was {_poss(opp)} head coach until {seasons[-1]}; from {GRAPH_SINCE} on"
        tenure = f"until {seasons[-1]}"
    else:
        stint = f"{coach} was {_poss(opp)} head coach for {_n_seasons(n)} ({when})"
        tenure = f"for {_n_seasons(n)} ({when})"
    if rw + rl + rt:
        stint += (
            ", he went " if cut else ", going "
        ) + f"{_record(rw, rl, rt)} in the regular season"
        if pw + pl_:
            stint += f" and {_record(pw, pl_)} in the playoffs"
    elif cut:
        stint = stint.removesuffix(f"; from {GRAPH_SINCE} on")
    facts = [GraphFact(text=stint, owners=[opp, team])]
    n_vs = vw + vl + vt
    if n_vs:
        vs = (
            f"As another team's head coach, {coach} is {_record(vw, vl, vt)} against the "
            f"{nickname(opp)} since {GRAPH_SINCE}"
        )
        if since == 0:
            vs += "; this is his first game against them since he left"
        facts.append(GraphFact(text=vs, owners=[team, opp]))
    else:
        facts.append(
            GraphFact(
                text=(
                    f"This is {coach}'s first game against the {nickname(opp)} as another "
                    f"team's head coach since {GRAPH_SINCE}"
                ),
                owners=[team, opp],
            )
        )
    games_with = rw + rl + rt + pw + pl_
    return GraphInsight(
        insight_id=f"coach_reunion:{cid}:{opp}",
        insight_type="coach_reunion",
        section="non_obvious",
        strength=float(r["strength"]),
        confidence="medium",
        graph_query=QUERY_OF["coach_reunion"],
        game_id=r["game_id"],
        teams=[team, opp],
        # the coach as a named person (so naming him names his team); not a player subject
        people=[GraphPerson(player=coach, player_id=cid, team=team, role="head coach")],
        headline=(
            f"{coach}, the {nickname(team)} head coach, faces the {nickname(opp)}, a team he "
            "used to coach."
        ),
        facts=facts,
        sample=F.text_num(
            f"{games_with} {_games_word(games_with)} as their head coach"
            + (f" since {GRAPH_SINCE}" if cut else ""),
            games_with,
        ),
        brief=(
            f"{coach} ({nickname(team)}) faces the {nickname(opp)}, whom he coached {tenure}"
            + (
                f" ({_record(rw, rl, rt)} in the regular season"
                + (f" since {GRAPH_SINCE})." if cut else ").")
                if games_with
                else "."
            )
        ),
    )


# ---- Q11: unit mismatch -------------------------------------------------------------------------


def rank_tier(rank: int, n: int, best: str, worst: str) -> str:
    """Rank by quality (1 = best) in words that say which end it counts from: 'the
    league's best', '2nd-best', '3rd-worst' ('strongest' / 'weakest' for a defense); the
    middle of the league is labelled as such (digest/build.py::defense_rank_note)."""
    if rank <= n // 2:
        core = f"the league's {best}" if rank == 1 else f"{F.ordinal_str(rank)}-{best}"
    else:
        low = n + 1 - rank
        core = f"the league's {worst}" if low == 1 else f"{F.ordinal_str(low)}-{worst}"
    if MIDDLE_TIER < rank <= n - MIDDLE_TIER:
        return f"about league-average ({core})"
    return core


def _ranks(tier: str) -> str:
    """'is the league's best' / 'ranks 4th-best in the league'."""
    return f"is {tier}" if tier.startswith("the league's") else f"ranks {tier} in the league"


UNIT_WORDS = {
    # unit -> (offense name, defense name, per-play unit)
    "pass": ("pass offense", "pass defense", "EPA per dropback"),
    "rush": ("run offense", "run defense", "EPA per rush"),
}


def _unit_mismatch(r: dict[str, Any], season: int) -> GraphInsight:
    home, away = r["team"], r["opponent"]
    ot, dt = r["off_team"], r["def_team"]
    unit = r["unit"]
    off_name, def_name, per = UNIT_WORDS[unit]
    n = int(r.get("n_teams") or N_TEAMS)
    o_rank, d_rank = int(r["off_rank"]), int(r["def_rank"])
    o_tier = rank_tier(o_rank, n, "best", "worst")
    d_tier = rank_tier(d_rank, n, "strongest", "weakest")
    o_val = F.epa(float(r["off_epa"]), per).display
    d_val = F.epa(float(r["def_epa"]), f"{per} allowed").display
    o_fact = (
        f"{_cap(_poss(ot))} {off_name} {_ranks(o_tier)} by the team ratings going into this "
        f"week ({o_val}, relative to the league average)"
    )
    d_fact = (
        f"{_cap(_poss(dt))} {def_name} {_ranks(d_tier)} by the team ratings going into this "
        f"week ({d_val}, relative to the league average)"
    )
    if r["edge"] == "offense":
        head = (
            f"{_cap(_poss(ot))} {off_name}, one of the league's best, meets one of its weakest "
            f"{def_name}s in the {nickname(dt)}."
        )
    else:
        head = (
            f"{_cap(_poss(dt))} {def_name}, one of the league's strongest, meets one of its "
            f"weakest {off_name}s in the {nickname(ot)}."
        )
    p_off, p_def = r.get("off_prior_weight"), r.get("def_prior_weight")
    prior = max(float(p_off or 0.0), float(p_def or 0.0))
    heavy = prior > PRIOR_HEAVY
    games = int(r["sample_size"])
    o_where = o_tier if o_tier.startswith("the league's") else f"{o_tier} in the league"
    brief = (
        f"{_cap(_poss(ot))} {off_name}, {o_where} ({o_val}), meets "
        f"{_poss(dt)} {def_name}, {d_tier} ({d_val})."
    )
    return GraphInsight(
        insight_id=f"unit_mismatch:{r['game_id']}:{ot}:{unit}",
        insight_type="unit_mismatch",
        section="non_obvious",
        strength=float(r["strength"]),
        confidence="low" if heavy else "medium",
        graph_query=QUERY_OF["unit_mismatch"],
        game_id=r["game_id"],
        teams=[home, away],
        headline=head,
        facts=[GraphFact(text=o_fact, owners=[ot]), GraphFact(text=d_fact, owners=[dt])],
        sample=F.text_num(f"ratings after {games} {_games_word(games)} this season", games),
        note=(
            "Early season: both ratings still lean heavily on the preseason prior (last "
            "season and the offseason), so these ranks can move a lot."
            if heavy
            else ""
        ),
        brief=brief,
    )


# ---- Q12: special-teams edge --------------------------------------------------------------------


def st_phrase(team: str, value: float) -> str:
    """'the Ravens have gained 1.2 EPA per game' with a code-made direction word: gained
    (> 0), lost (< 0), about even (|x| < ST_EVEN). The value is net special-teams EPA per
    game, centered on an average team (graph/tables.py::special_teams_epa)."""
    size = F.per_game(abs(value), "EPA").display
    if abs(value) < ST_EVEN:
        return f"{_the(team)} have come out about even"
    return f"{_the(team)} have {'gained' if value > 0 else 'lost'} {size}"


def _special_teams(r: dict[str, Any], season: int) -> GraphInsight:
    a, b = r["team"], r["opponent"]
    ea, eb = float(r["team_st_epa"]), float(r["opponent_st_epa"])
    na, nb = int(r["team_games"]), int(r["opponent_games"])
    better, worse = (a, b) if ea >= eb else (b, a)
    e_better, e_worse = (ea, eb) if better == a else (eb, ea)
    gap = abs(float(r["gap"]))
    facts = [
        GraphFact(
            text=_cap(
                f"{st_phrase(t, e)} on special-teams plays (kicks, punts and returns, both "
                f"sides) over their {n} {_games_word(n)} this season, compared with an "
                "average team"
            ),
            owners=[t],
        )
        for t, e, n in ((a, ea, na), (b, eb, nb))
    ]
    facts.append(
        GraphFact(
            text=(
                f"That is a gap of {F.per_game(gap, 'EPA').display} on special teams this "
                f"season, in {_poss(better)} favor"
            ),
            owners=[a, b],
        )
    )
    n_min = min(na, nb)
    return GraphInsight(
        insight_id=f"special_teams:{r['game_id']}",
        insight_type="special_teams",
        section="non_obvious",
        strength=float(r["strength"]),
        confidence="low" if n_min < ST_LOW_GAMES else "medium",
        graph_query=QUERY_OF["special_teams"],
        game_id=r["game_id"],
        teams=[a, b],
        headline=(
            f"The {nickname(better)} have had clearly better special teams than the "
            f"{nickname(worse)} this season."
        ),
        facts=facts,
        sample=F.text_num(f"at least {n_min} {_games_word(n_min)} each", n_min),
        note=(
            "Special-teams EPA swings on a few plays (a return touchdown, a missed kick, a "
            "muffed punt), so a few games' worth is noisy."
        ),
        brief=(
            f"{_cap(st_phrase(better, e_better))} on special teams this season; "
            f"{st_phrase(worse, e_worse)}."
        ),
    )


CONVERTERS: dict[str, Callable[[dict[str, Any], int], GraphInsight | None]] = {
    "q5_coach_reunion": _coach_reunion,
    "q11_unit_mismatch": _unit_mismatch,
    "q12_special_teams": _special_teams,
}
