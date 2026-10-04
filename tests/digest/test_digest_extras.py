"""The digest's code-written graph extras (after P05): QB-change flags in the game table,
starters out, "More from the graph", latest news, the 8-player watch table, and the
selection rules that feed them (Rishi: more of the strong stories, not only QB changes)."""

from __future__ import annotations

import datetime as dt

from conftest import make_payload

from nflengine.digest import format as F
from nflengine.digest import render as R
from nflengine.digest.facts import build_fact_index
from nflengine.digest.graph_sections import (
    MAX_OUT_PER_TEAM,
    GraphState,
    qb_change_notes,
    record_published,
    starters_out,
)
from nflengine.digest.payload import GraphInsight, GraphPerson, NewsItem
from nflengine.graph import insights as gi

RUN = dt.datetime(2025, 10, 21, 14, 0, tzinfo=dt.UTC)
G1, G2 = "2025_08_KC_DEN", "2025_08_NE_BUF"


def _item(iid, itype, game, strength, *, people=(), teams=("DEN", "KC"), brief="b."):
    return GraphInsight(
        insight_id=iid,
        insight_type=itype,
        section="non_obvious",
        strength=strength,
        graph_query="q",
        game_id=game,
        teams=list(teams),
        people=list(people),
        headline="h",
        brief=brief,
        sample=F.count(3),
    )


def _person(pid, name, team="DEN", role="out"):
    return GraphPerson(player=name, player_id=pid, team=team, role=role)


GAMES = {
    G1: gi.GameInfo(G1, "DEN", "KC", RUN + dt.timedelta(days=5)),
    G2: gi.GameInfo(G2, "BUF", "NE", RUN + dt.timedelta(days=5)),
}


# ---- selection ----------------------------------------------------------------------------------


def test_a_strong_injury_ripple_beats_a_qb_change_for_the_risk_slot() -> None:
    """QB changes are all flagged in the game table, so they rank at 80% for the prose."""
    qb = _item(
        "qb:DEN", "qb_change", G1, 0.9, people=[_person("q", "Q B", role="expected starter")]
    )
    ripple = _item("ripple:x", "injury_ripple", G2, 0.78, people=[_person("x", "X Y", "BUF")])
    sel = gi.select([qb, ripple], GAMES, RUN)
    assert [c.insight_id for c in sel.matchup_risk] == ["ripple:x"]
    weak = ripple.model_copy(update={"strength": 0.6})
    assert [c.insight_id for c in gi.select([qb, weak], GAMES, RUN).matchup_risk] == ["qb:DEN"]


def test_more_from_the_graph_takes_the_strong_leftovers_but_no_qb_changes() -> None:
    cands = [
        _item("rev:1", "revenge", G1, 0.95, people=[_person("p1", "A One", role="former player")]),
        _item("unit:1", "unit_mismatch", G2, 0.9, teams=("BUF", "NE")),
        _item(
            "rev:2", "revenge", G2, 0.85, people=[_person("p2", "B Two", "BUF", "former player")]
        ),
        _item(
            "rev:3", "revenge", G2, 0.84, people=[_person("p3", "C Three", "NE", "former player")]
        ),
        _item("rev:4", "revenge", G1, 0.83, people=[_person("p4", "D Four", role="former player")]),
        _item("qb:x", "qb_change", G1, 0.99, people=[_person("q", "Q B", role="expected starter")]),
        _item("st:1", "special_teams", G1, 0.8, teams=("DEN", "KC")),
        _item("weak", "special_teams", G2, 0.5, teams=("BUF", "NE")),
        _item("rip:1", "injury_ripple", G2, 0.8, people=[_person("r", "R Out", "BUF")]),
    ]
    sel = gi.select(cands, GAMES, RUN)
    shown = {c.insight_id for c in sel.picked}
    more = [c.insight_id for c in sel.more]
    assert "qb:x" not in more and "weak" not in more  # QB changes live in the table
    assert not shown & set(more)
    assert all(c.section == "more" for c in sel.more)
    kinds = [c.insight_type for c in sel.more]
    assert max(kinds.count(k) for k in set(kinds)) <= gi.MORE_PER_TYPE
    per_game = [c.game_id for c in sel.more]
    assert max(per_game.count(g) for g in set(per_game)) <= gi.MORE_PER_GAME
    assert len(sel.more) <= gi.MORE_MAX


def test_more_skips_novelty_and_players_already_used() -> None:
    p = _person("p1", "A One", role="former player")
    cands = [
        _item("rev:1", "revenge", G1, 0.95, people=[p]),
        _item("rip:1", "injury_ripple", G2, 0.9, people=[_person("p1", "A One", role="out")]),
        _item("st:1", "special_teams", G2, 0.85, teams=("BUF", "NE")),
    ]
    sel = gi.select(cands, GAMES, RUN, recent_ids=["st:1"])
    assert "st:1" not in [c.insight_id for c in sel.more]  # published in the last 3 weeks
    assert "rip:1" not in [c.insight_id for c in sel.more + sel.picked if c.section == "more"]


# ---- QB changes and starters out ----------------------------------------------------------------


def test_qb_change_notes_say_why_or_that_it_isnt_confirmed() -> None:
    rows = [
        {"game_id": G1, "team": "DEN", "qb": "Jarrett Stidham", "regular": "Bo Nix",
         "regular_status": "Out", "regular_body_part": "Ankle", "qb_confirmed": True},
        {"game_id": G2, "team": "NE", "qb": "Josh Dobbs", "regular": "Drake Maye",
         "regular_status": None, "qb_confirmed": False},
        {"game_id": "started", "team": "KC", "qb": "x", "regular": "y", "qb_confirmed": True},
    ]  # fmt: skip
    notes = qb_change_notes(rows, {G1, G2})
    assert [n.text for n in notes] == [
        "Jarrett Stidham starts in place of Bo Nix (out: ankle)",
        "Josh Dobbs could start in place of Drake Maye (he started their last game; "
        "not confirmed yet)",
    ]
    assert [n.confirmed for n in notes] == [True, False]


def test_starters_out_lists_qbs_first_and_caps_each_team() -> None:
    def row(pid, group, pos, snap, src="this_week", status="Out", body="Knee"):
        return {"game_id": G1, "team": "DEN", "player_id": pid, "player": f"P {pid}",
                "position": pos, "position_group": group, "out_source": src, "status": status,
                "body_part": body, "snap_pct": snap}  # fmt: skip

    rows = [
        row("ol", "OL", "G", 0.99),
        row("cb", "DB", "CB", 0.9),
        row("qb", "QB", "QB", 1.0, status="Doubtful", body="Thumb"),
        row("wr", "WR", "WR", 0.8, src="reserve"),
        row("lb", "LB", "LB", 0.7, src="last_week"),
    ]
    out = starters_out(rows, {G1})
    assert len(out) == MAX_OUT_PER_TEAM
    assert [o.player_id for o in out] == ["qb", "wr", "ol"]  # QB, skill, then snap share
    assert out[0].text == "QB P qb (doubtful: thumb)"
    assert out[1].text == "WR P wr (reserve list)"
    assert starters_out(rows, set()) == []  # games already underway are left out


# ---- render -------------------------------------------------------------------------------------


def _extras_payload():
    from nflengine.digest.graph_sections import qb_change_notes, starters_out

    qb = qb_change_notes(
        [{"game_id": G1, "team": "DEN", "qb": "Jarrett Stidham", "regular": "Bo Nix",
          "regular_status": "Out", "regular_body_part": "Ankle", "qb_confirmed": True}],
        {G1},
    )  # fmt: skip
    out = starters_out(
        [{"game_id": G1, "team": "KC", "player_id": "x", "player": "Xavier Worthy",
          "position": "WR", "position_group": "WR", "out_source": "this_week",
          "status": "Out", "body_part": "Shoulder", "snap_pct": 0.9}],
        {G1},
    )  # fmt: skip
    more = [
        _item(
            "st:1",
            "special_teams",
            G2,
            0.8,
            teams=("BUF", "NE"),
            brief="The Bills' special teams have added +1.2 EPA per game this season.",
        )
    ]
    news = [NewsItem(headline="Bills place S Damar Hamlin on IR", published="2025-10-20T15:00Z")]
    p = make_payload(qb_changes=qb, starters_out=out, graph_more=more, news=news)
    games = []
    for g in p.games:
        upd = (
            {"home_qb": "Jarrett Stidham", "away_qb": "Patrick Mahomes"} if g.game_id == G1 else {}
        )
        games.append(g.model_copy(update=upd))
    return p.model_copy(update={"games": games})


def test_game_table_has_a_qb_column_with_change_flags() -> None:
    table = R.game_table(_extras_payload())
    assert "| QBs (away / home) |" in table
    assert "Patrick Mahomes / Jarrett Stidham ⚠" in table
    assert "**⚠ QB changes**" in table
    assert "- Broncos: Jarrett Stidham starts in place of Bo Nix (out: ankle)" in table


def test_starters_out_more_and_news_are_rendered() -> None:
    p = _extras_payload()
    assert R.starters_out_list(p).splitlines()[-1] == (
        "- Chiefs at Broncos: Chiefs: WR Xavier Worthy (out: shoulder)"
    )
    assert R.graph_more_list(p).endswith(
        "- **Patriots at Bills**: The Bills' special teams have added +1.2 EPA per game "
        "this season."
    )
    assert (
        R.news_list(p) == "## Latest news\n\n- Bills place S Damar Hamlin on IR (ESPN, 2025-10-20)"
    )
    assert R.graph_more_list(make_payload()) == "" and R.news_list(make_payload()) == ""


def test_watch_table_lists_every_pick() -> None:
    p = make_payload()
    table = R.watch_table(p)
    assert table.count("\n") == 1 + len(p.players_to_watch)  # header, divider, one row each
    w = p.players_to_watch[0]
    assert f"{w.player} ({w.team_name} {w.position})" in table
    assert w.opp_def_rank.display in table and w.baseline.display in table


def test_render_digest_places_the_extras() -> None:
    p = _extras_payload()
    info = R.FooterInfo(True, [], [], False, "abc", "placeholder", "templates-v1")
    sections = {k: "Text." for k in ("report_card", "game_outlook", "players_to_watch")}
    sections["non_obvious"] = "Prose."
    md = R.render_digest(p, sections, info, None)
    order = [
        "## Game outlook",
        "**⚠ QB changes**",
        "**Starters out this week**",
        "## Players to watch",
        "| Player | Game |",
        "## Non-obvious insights",
        "Prose.",
        "**More from the graph**",
        "## Latest news",
    ]
    positions = [md.index(x) for x in order]
    assert positions == sorted(positions)


# ---- facts and publishing -----------------------------------------------------------------------


def test_people_in_the_extras_are_known_entities_without_hedge_duty() -> None:
    p = _extras_payload()
    low = _item("rip:9", "injury_ripple", G2, 0.8, people=[_person("z9", "Zed Nine", "BUF")])
    low = low.model_copy(update={"confidence": "low"})
    p = p.model_copy(update={"graph_more": [*p.graph_more, low]})
    fx = build_fact_index(p)
    names = fx.entity_names()
    assert {"jarrett stidham", "bo nix", "xavier worthy", "zed nine"} <= names
    assert "player:z9" not in fx.low_confidence and "more" not in fx.hedge_sections


def test_more_items_are_logged_as_published(tmp_path) -> None:
    from nflengine.digest.run import make_context
    from nflengine.graph.published import log_path, read_log
    from nflengine.paths import DataPaths

    ctx = make_context(2025, 8, "backtest", RUN, DataPaths(tmp_path))
    more = _item("st:1", "special_teams", G2, 0.8, teams=("BUF", "NE"))
    state = GraphState("ok", more=[more.model_copy(update={"section": "more"})])
    record_published(ctx, state, lambda m: None, sections={})
    log = read_log(log_path(ctx.run_root))
    assert log["insight_id"].to_list() == ["st:1"] and log["section"].to_list() == ["more"]
