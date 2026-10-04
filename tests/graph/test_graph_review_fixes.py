"""Regression tests for the P05 Sol code-review fixes (graph side; no Neo4j)."""

from __future__ import annotations

import dataclasses
import datetime as dt

import polars as pl
import pytest
from graph_world import BT_KEY, KEY, PFR_SCHEMA, ROSTERS_SCHEMA, frame, make_inputs

from nflengine.digest import format as F
from nflengine.digest.graph_sections import rendered
from nflengine.digest.payload import GraphInsight, GraphPerson
from nflengine.graph import insights as gi
from nflengine.graph import tables as T
from nflengine.graph.client import GraphCountMismatch, safe_error
from nflengine.paths import DataPaths


def _roster(pid, week, name, pos="WR", team="KC"):
    return {
        "gsis_id": pid,
        "team": team,
        "season": 2026,
        "week": week,
        "status": "ACT",
        "full_name": name,
        "position": pos,
    }


# ---- errors and the count check ------------------------------------------------------------------


def test_safe_error_keeps_the_type_and_a_fixed_reason_only() -> None:
    assert safe_error(GraphCountMismatch(["rel:THREW_TO"])) == (
        "GraphCountMismatch: Neo4j counts differ from the tables for rel:THREW_TO"
    )
    assert safe_error(ConnectionRefusedError("host secret")) == (
        "ConnectionRefusedError: Neo4j unreachable"
    )
    assert safe_error(ValueError("SENTINEL")) == "ValueError"


def test_a_count_mismatch_fails_the_build_before_the_queries(monkeypatch, tmp_path) -> None:
    """An incomplete graph must never come out as `status: ok` (Sol review)."""
    import nflengine.graph.build as gb

    inp = make_inputs()
    ran: list[str] = []
    monkeypatch.setattr(gb, "load_inputs", lambda *a, **k: inp)
    monkeypatch.setattr(gb.L, "wipe", lambda d: None)
    monkeypatch.setattr(gb.L, "apply_schema", lambda d: 0)
    monkeypatch.setattr(gb.L, "load_tables", lambda d, t, on_step=None: {})
    monkeypatch.setattr(gb.L, "graph_counts", lambda d: {"node:Team": 0})
    monkeypatch.setattr(gb, "run_library", lambda *a: ran.append("queries") or {})

    class Driver:
        def verify_connectivity(self) -> None:
            pass

    with pytest.raises(GraphCountMismatch):
        gb.build_graph(Driver(), KEY, DataPaths(tmp_path), log=lambda m: None)
    assert ran == []


# ---- as-of fixes in the tables -------------------------------------------------------------------


def test_player_fallback_uses_only_roster_rows_before_the_key() -> None:
    """A player missing from the master table takes his name / position from the latest
    roster row before the key, never a later one (Sol review)."""
    inp = make_inputs()
    extra = frame(
        [_roster("00-FUT", 2, "Old Name", "WR"), _roster("00-FUT", 4, "Future Name", "TE")],
        ROSTERS_SCHEMA,
    )
    inp = dataclasses.replace(inp, rosters=pl.concat([inp.rosters, extra]))
    players = T.build_tables(inp, KEY).nodes["Player"]
    row = players.filter(pl.col("player_id") == "00-FUT").to_dicts()[0]
    assert (row["name"], row["position"]) == ("Old Name", "WR")


def test_played_for_keeps_the_actual_roster_weeks() -> None:
    """Left after week 1, back in week 3: week 2 is not a week "on the team" (Q2 uses
    `roster_weeks` for its with / without games; Sol review)."""
    inp = make_inputs()
    extra = frame(
        [_roster("00-GAP", 1, "Gap Guy"), _roster("00-GAP", 3, "Gap Guy")], ROSTERS_SCHEMA
    )
    inp = dataclasses.replace(inp, rosters=pl.concat([inp.rosters, extra]))
    pf = T.build_tables(inp, KEY).rels["PLAYED_FOR"]
    row = pf.filter(pl.col("s") == "00-GAP").to_dicts()[0]
    assert (row["first_week"], row["last_week"], row["roster_weeks"]) == (1, 3, [1, 3])


def test_pfr_pressures_lag_one_week_in_backtests_only() -> None:
    """PFR for week W-1 isn't out on the Tuesday of week W (D44): a backtest graph leaves
    it out, a live run (later in the week) keeps it (Sol review)."""
    inp = make_inputs()
    appear = T.build_tables(inp, KEY).rels["APPEARED_IN"]
    late = appear.filter((pl.col("season") == 2026) & (pl.col("week") == KEY.week - 1)).row(
        0, named=True
    )
    early = appear.filter((pl.col("season") == 2026) & (pl.col("week") == 1)).row(0, named=True)
    pfr = frame(
        [
            {"gsis_id": late["s"], "game_id": late["e"], "def_pressures": 3.0},
            {"gsis_id": early["s"], "game_id": early["e"], "def_pressures": 2.0},
        ],
        PFR_SCHEMA,
    )
    inp = dataclasses.replace(inp, pfr_def=pfr)

    def pressures(key: T.GraphKey) -> dict[tuple[str, str], int | None]:
        rel = T.build_tables(inp, key).rels["APPEARED_IN"]
        return {(r["s"], r["e"]): r["pressures"] for r in rel.iter_rows(named=True)}

    live, bt = pressures(KEY), pressures(BT_KEY)
    assert live[(late["s"], late["e"])] == 3 and bt[(late["s"], late["e"])] is None
    assert live[(early["s"], early["e"])] == 2 and bt[(early["s"], early["e"])] == 2


# ---- selection and publishing --------------------------------------------------------------------


def _item(insight_id: str, game_id: str, people=(), teams=("KC", "SF")) -> GraphInsight:
    return GraphInsight(
        insight_id=insight_id,
        insight_type="revenge" if people else "trend_mismatch",
        section="non_obvious",
        strength=0.9,
        graph_query="q1_revenge",
        game_id=game_id,
        teams=list(teams),
        people=list(people),
        headline="h",
        sample=F.count(3),
    )


def test_select_skips_games_inside_the_kickoff_margin() -> None:
    """A live digest can take 20+ minutes: its picks skip games kicking off soon (Sol)."""
    run = dt.datetime(2026, 10, 4, 12, 0, tzinfo=dt.UTC)
    games = {
        "soon": gi.GameInfo("soon", "KC", "SF", run + dt.timedelta(hours=1)),
        "later": gi.GameInfo("later", "LV", "DEN", run + dt.timedelta(hours=5)),
    }
    cands = [_item("a", "soon"), _item("b", "later", teams=("LV", "DEN"))]
    tight = gi.select(cands, games, run, kickoff_margin=dt.timedelta(minutes=90))
    assert [c.insight_id for c in tight.picked] == ["b"] and tight.skipped["started"] == ["a"]
    loose = gi.select(cands, games, run)
    assert {c.insight_id for c in loose.picked} == {"a", "b"}


def test_only_items_the_prose_used_are_logged_as_published() -> None:
    """A pick the writer left out (trimmed for length) isn't logged (Sol review)."""
    person = GraphPerson(player="Kaden Elliss", player_id="00-KE", team="NO", role="former player")
    rev = _item("revenge:00-KE:ATL", "g1", people=[person], teams=("NO", "ATL"))
    trend = _item("trend_mismatch:g2", "g2", teams=("CAR", "DET"))
    only_rev = {"non_obvious": "Elliss faces the Falcons, his former team."}
    assert [i.insight_id for i in rendered([rev, trend], only_rev)] == ["revenge:00-KE:ATL"]
    both = {"non_obvious": "Kaden Elliss faces the Falcons. The Panthers trend up, the Lions down."}
    assert len(rendered([rev, trend], both)) == 2
    assert rendered([rev, trend], {"non_obvious": "No graph angle cleared the bar."}) == []


# ---- fact-check fixes (opus-high, 2026 week 4) ---------------------------------------------------


def _qb_row(**kw):
    row = {
        "game_id": "2026_04_GB_TB",
        "team": "TB",
        "opponent": "GB",
        "qb_id": "00-JD",
        "qb": "Jalon Daniels",
        "qb_rookie_season": 2026,
        "regular_id": "00-BM",
        "regular": "Baker Mayfield",
        "regular_status": "Out",
        "regular_body_part": "Thumb",
        "r_starts": 3,
        "team_games": 3,
        "q_starts_now": 0,
        "q_starts_total": 0,
        "receivers": [],
        "q_targets_total": 2,
        "strength": 0.9,
    }
    return row | kw


def test_qb_change_says_why_and_what_the_small_sample_is() -> None:
    """The hedge belongs to the receivers' history, not to the start (fact-check)."""
    ins = gi._qb_change(_qb_row(), 2026)
    assert ins.headline == (
        "Jalon Daniels is expected to start at QB for the Buccaneers this week; "
        "Baker Mayfield is listed out for this week (thumb)."
    )
    assert ins.confidence == "low"
    assert ins.note == (
        "A small sample: Jalon Daniels has 2 career targets to the Buccaneers' current "
        "receivers, so their history together says little."
    )
    tuesday = gi._qb_change(_qb_row(regular_status=None, q_targets_total=40), 2026)
    assert tuesday.headline.endswith("for the Buccaneers this week.") and tuesday.note == ""


def test_revenge_snap_share_names_the_side() -> None:
    row = {
        "game_id": "2026_04_ATL_NO",
        "team": "NO",
        "opponent": "ATL",
        "player_id": "00-KE",
        "player": "Kaden Elliss",
        "position": "LB",
        "seasons_with_opp": [2024, 2025],
        "games_with_opp": 34,
        "games_now": 3,
        "snap_pct": 1.0,
        "snap_side": "defense",
        "strength": 0.9,
    }
    ins = gi._revenge(row, 2026)
    assert "of the Saints' defensive snaps" in ins.facts[1].text


def test_a_tuesday_rule_qb_pick_is_worded_as_unconfirmed() -> None:
    """2025 week 9: Cousins started ATL's last game while Penix was out; on Tuesday that's
    all the rule sees, and Penix started. The item must not say "expected to start"."""
    row = _qb_row(
        team="ATL",
        qb="Kirk Cousins",
        regular="Michael Penix Jr.",
        regular_status=None,
        qb_source="last_game",
        qb_confirmed=False,
        q_starts_now=1,
        q_starts_total=106,
        q_targets_total=395,
    )
    ins = gi._qb_change(row, 2025)
    assert ins.headline == (
        "Kirk Cousins could start at QB for the Falcons this week (he started their last "
        "game), but it isn't confirmed yet."
    )
    assert "expected to start" not in ins.headline and ins.confidence == "low"
    assert ins.note.startswith("Unconfirmed:")
