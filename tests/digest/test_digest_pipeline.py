"""LLM registry, regenerate-once flow, rendering and builders (P04)."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import polars as pl
import pytest
from conftest import make_payload

from nflengine.digest import build
from nflengine.digest.facts import build_fact_index
from nflengine.digest.llm import ProviderNotConnected, get_llm
from nflengine.digest.llm.placeholder import PlaceholderLLM
from nflengine.digest.prompt import load_prompt
from nflengine.digest.render import FooterInfo, render_digest
from nflengine.digest.synthesize import synthesize

SRC = Path(__file__).resolve().parents[2] / "src" / "nflengine"
BUDGETS = {"report_card": 60, "game_outlook": 80, "team_trends": 120}


# ---- provider registry ------------------------------------------------------------------------


def test_registry_returns_placeholder_and_refuses_unconnected() -> None:
    llm = get_llm("placeholder")
    assert isinstance(llm, PlaceholderLLM) and llm.name == "placeholder"
    with pytest.raises(ProviderNotConnected):
        get_llm("anthropic")
    with pytest.raises(ValueError):
        get_llm("gpt-nonsense")


def test_nothing_outside_digest_llm_imports_a_provider() -> None:
    """Swapping the LLM provider changes only config (P04 exit criterion)."""
    pat = re.compile(r"digest\.llm\.(placeholder|anthropic|openai)|from \.placeholder")
    offenders = [
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if "digest/llm/" not in p.as_posix() and pat.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


# ---- prompt -----------------------------------------------------------------------------------


def test_prompt_hash_and_active_sections() -> None:
    p = load_prompt()
    assert len(p.hash) == 12 and "Never mention point spreads" in p.system
    spec = p.output_spec({"report_card": 60})
    ids = [s["id"] for s in spec["sections"]]
    assert ids[:2] == ["report_card", "game_outlook"]
    assert "matchup_risk" not in ids  # P05 sections stay off
    assert "matchup_risk" in [s["id"] for s in p.output_spec({}, ("P05",))["sections"]]


# ---- regenerate once, then banner -------------------------------------------------------------


class ScriptedLLM:
    name, model = "scripted", None

    def __init__(self, outputs: list[dict[str, str]]):
        self.outputs, self.specs = outputs, []

    def generate(self, system_prompt, payload, output_spec):
        self.specs.append(output_spec)
        return self.outputs[min(len(self.specs) - 1, len(self.outputs) - 1)]


GOOD = {
    "report_card": "The model went 11 of 15 on its week 7 picks.",
    "game_outlook": "The Bills are 73% against the Patriots.",
    "team_trends": "The Lions are trending down.",
    "under_the_hood": "Ja'Marr Chase had 4.1 yards of average separation last week.",
    "players_to_watch": "Jaylen Warren is a heuristic pick with low confidence.",
}
BAD = {**GOOD, "game_outlook": "The Bills are 88% to cover against the Patriots."}


def test_clean_first_attempt_is_not_regenerated() -> None:
    payload = make_payload()
    llm = ScriptedLLM([GOOD])
    s = synthesize(payload, build_fact_index(payload), llm, load_prompt(), BUDGETS)
    assert s.final.passed and not s.regenerated and s.banner is None and len(llm.specs) == 1


def test_failed_attempt_regenerates_once_with_feedback() -> None:
    payload = make_payload()
    llm = ScriptedLLM([BAD, GOOD])
    s = synthesize(payload, build_fact_index(payload), llm, load_prompt(), BUDGETS)
    assert s.regenerated and s.final.passed and s.banner is None
    feedback = "\n".join(llm.specs[1]["feedback"])
    assert "88%" in feedback and "cover" in feedback
    assert not s.first.passed and set(s.first.failed) >= {"number_provenance", "banned_language"}


def test_second_failure_publishes_with_banner() -> None:
    payload = make_payload()
    llm = ScriptedLLM([BAD, BAD])
    s = synthesize(payload, build_fact_index(payload), llm, load_prompt(), BUDGETS)
    assert s.regenerated and not s.final.passed and len(llm.specs) == 2
    assert "number_provenance" in s.banner and s.checks_json()["banner"] is True
    info = FooterInfo(False, s.final.failed, [], True, "abc123", "scripted", None)
    md = render_digest(payload, s.sections, info, s.banner)
    assert md.count("⚠️") == 1 and "FAILED: " in md


# ---- rendering --------------------------------------------------------------------------------


def test_render_has_fixed_sections_table_and_footer() -> None:
    payload = make_payload()
    llm = PlaceholderLLM()
    prompt = load_prompt()
    budgets = {**BUDGETS, "under_the_hood": 110, "players_to_watch": 160}
    s = synthesize(payload, build_fact_index(payload), llm, prompt, budgets)
    info = FooterInfo(True, [], s.final.warnings, False, prompt.hash, llm.name, llm.model)
    md = render_digest(payload, s.sections, info, s.banner)
    heads = re.findall(r"^## (.+)$", md, flags=re.M)
    assert heads == [
        "Report card",
        "Game outlook",
        "Team trend shifts",
        "Last week under the hood",
        "Players to watch",
    ]
    assert "| Kickoff (ET) | Game |" in md and "Chiefs at Broncos" in md
    assert "picks 11 of 15" in md and "Checks: all passed" in md
    assert prompt.hash in md and "(backtest)" in md
    # never quotes market numbers
    assert not re.search(r"\bspread|\bline\b|moneyline", md, flags=re.I)


# ---- builders ---------------------------------------------------------------------------------


def _pred_rows() -> pl.DataFrame:
    ko = dt.datetime(2026, 10, 4, 17, 0, tzinfo=dt.UTC)
    base = {
        "game_id": ["g1", "g1", "g2"],
        "home_team": ["BUF", "BUF", "CLE"],
        "away_team": ["NE", "NE", "PIT"],
        "variant": ["market", "model_only", "model_only"],
        "is_primary": [True, False, True],
        "market_fallback": [False, False, True],
        "home_win_prob": [0.70, 0.82, 0.40],
        "market_prob": [0.70, 0.70, None],
        "expected_margin": [7.0, 9.0, -3.0],
        "pred_home_points": [27.0, 28.0, 18.0],
        "pred_away_points": [20.0, 19.0, 21.0],
        "confidence_label": ["solid", "strong", "lean"],
        "kickoff_utc": [ko, ko, ko - dt.timedelta(days=3)],
        "neutral_site": [False, False, False],
        "home_qb_name": ["Josh Allen", "Josh Allen", None],
        "away_qb_name": ["Drake Maye", "Drake Maye", None],
    }
    return pl.DataFrame(base)


def test_build_games_consensus_and_started_status() -> None:
    run_time = dt.datetime(2026, 10, 3, 15, 0, tzinfo=dt.UTC)
    g = {x.game_id: x for x in build.build_games(_pred_rows(), run_time)}
    assert g["g1"].home_win_prob.display == "70%" and g["g1"].status == "upcoming"
    c = g["g1"].model_vs_consensus
    assert c.size == "large" and c.team == "BUF"  # model-only 82% vs market 70%
    assert g["g2"].status == "started" and g["g2"].model_vs_consensus is None
    assert g["g2"].expected_margin.display == "Steelers by 3" and g["g2"].market_fallback
    assert g["g1"].kickoff == "Sun 1:00 PM"


def test_build_trends_picks_both_directions_with_drivers() -> None:
    trends = pl.DataFrame(
        {
            "team": ["LV", "NYJ", "SF", "GB", "PHI", "KC"],
            "direction": ["up", "up", "up", "down", "down", "stable"],
            "trend_delta": [0.12, 0.11, 0.10, -0.10, -0.09, 0.01],
            "net_epa": [0.0, -0.1, 0.1, -0.04, -0.02, 0.08],
            "qb_change": [True, False, False, False, False, False],
            "qb_now_name": ["Geno Smith", None, None, None, None, None],
            "qb_before_name": ["Aidan O'Connell", None, None, None, None, None],
            "key_players_out_names": ["Brock Bowers (TE)", None, None, None, None, None],
            "def_pressure_rate_delta": [0.08, None, None, None, None, None],
        }
    )
    drivers = pl.DataFrame(
        {
            "team": ["LV", "LV"],
            "rank": [1, 2],
            "part": ["pass_defense", "rush_offense"],
            "delta": [-0.05, 0.03],
            "effect": ["better", "better"],
        }
    )
    out = build.build_trends(trends, drivers, followed=["SF"])
    assert [t.team for t in out] == ["LV", "SF", "GB", "PHI"]  # followed SF beats NYJ
    lv = out[0]
    assert lv.drivers[0].change.display == "0.05 fewer EPA per dropback allowed"
    assert lv.evidence[0] == "Geno Smith started their latest game in place of Aidan O'Connell"
    assert lv.evidence[1] == "recently without tight end Brock Bowers"
    assert lv.trend_delta.display == "up 0.12 EPA per play over the last 3 weeks"
    assert set(lv.people) == {"Geno Smith", "Aidan O'Connell", "Brock Bowers"}
    assert build.build_trends(trends.filter(pl.col("direction") == "stable"), drivers) == []


@pytest.mark.parametrize(
    "rank, note",
    [(1, "league's weakest"), (3, "3rd-weakest"), (8, "8th-weakest"),
     (16, "about league-average (16th-weakest)"), (17, "about league-average (16th-strongest)"),
     (25, "8th-strongest"), (29, "4th-strongest"), (32, "league's strongest")],
)  # fmt: skip
def test_defense_rank_note_reads_from_the_nearer_end(rank: int, note: str) -> None:
    assert build.defense_rank_note(rank) == note


def test_watch_save_keeps_earlier_picks_for_started_games(tmp_path) -> None:
    """Sol #2: a re-run after kickoff keeps the earlier, gradable picks."""
    from nflengine.digest.run import save_watch

    ko = dt.datetime(2026, 10, 4, 17, tzinfo=dt.UTC)
    tue = ko - dt.timedelta(days=5)
    path = tmp_path / "watchlist.parquet"
    pl.DataFrame(
        {"player_id": ["early", "late"], "created_at": [tue, tue], "kickoff_utc": [ko, ko]}
    ).write_parquet(path)
    later = ko + dt.timedelta(hours=2)
    new = pl.DataFrame(
        {"player_id": ["fresh"], "created_at": [later], "kickoff_utc": [ko + dt.timedelta(days=1)]}
    )
    out = save_watch(new, path, later)
    assert sorted(out["player_id"].to_list()) == ["early", "fresh", "late"]


def test_started_games_are_names_only_and_placeholder_avoids_banned_words() -> None:
    """Sol #3 and #10."""
    payload = make_payload(started_games=["Steelers at Browns"])
    payload = payload.model_copy(
        update={"meta": payload.meta.model_copy(update={"market_data_used": False})}
    )
    fx = build_fact_index(payload)
    assert ("PIT", "CLE") not in fx.matchups
    llm, prompt = PlaceholderLLM(), load_prompt()
    s = synthesize(payload, fx, llm, prompt, {"game_outlook": 80})
    assert (
        "banned_language" not in s.final.failed and "No market data" in s.sections["game_outlook"]
    )
    info = FooterInfo(True, [], [], False, prompt.hash, llm.name, llm.model)
    md = render_digest(payload, s.sections, info, None)
    assert "Kicked off before this run (not predicted here): Steelers at Browns" in md


def test_season_series_cumulative_brier_divides_by_its_own_games() -> None:
    """A week without a market Brier doesn't dilute the market's cumulative curve (found
    while writing the W&B guide)."""
    from nflengine.digest.run import SCORECARD_SCHEMA, season_series

    rows = [
        {"season": 2026, "week": 5, "games": 10, "brier_model": 0.2, "brier_market": 0.1},
        {"season": 2026, "week": 6, "games": 10, "brier_model": 0.3, "brier_market": None},
    ]
    sc = pl.DataFrame(
        [{k: r.get(k) for k in SCORECARD_SCHEMA} for r in rows], schema=SCORECARD_SCHEMA
    )
    last = season_series(sc)[-1]
    assert last["season/cum_brier_model"] == pytest.approx(0.25)
    assert "season/cum_brier_market" not in last  # no market value that week
    assert season_series(sc)[0]["season/cum_brier_market"] == pytest.approx(0.1)


def test_season_series_weekly_and_cumulative() -> None:
    from nflengine.digest.run import SCORECARD_SCHEMA, season_series

    sc = pl.DataFrame(
        [
            {"season": 2025, "week": 7, "games": 10, "picks_correct": 7, "picks_total": 10,
             "brier_model": 0.20, "brier_elo": 0.22, "brier_market": 0.21, "points_mae": 8.0,
             "watchlist_hits": 2, "watchlist_total": 4},
            {"season": 2025, "week": 8, "games": 30, "picks_correct": 18, "picks_total": 30,
             "brier_model": 0.24, "brier_elo": 0.25, "brier_market": None, "points_mae": 9.0,
             "watchlist_hits": None, "watchlist_total": None},
        ],
        schema={k: v for k, v in SCORECARD_SCHEMA.items() if k not in
                ("player_mae_vs_baseline", "checks_passed", "not_graded", "logloss_model",
                 "ece_model")},
    )  # fmt: skip
    pts = season_series(sc)
    assert [p["season/week"] for p in pts] == [7, 8]
    assert pts[1]["season/cum_brier_model"] == pytest.approx((0.20 * 10 + 0.24 * 30) / 40)
    assert pts[1]["season/cum_pick_accuracy"] == pytest.approx(25 / 40)
    assert "season/brier_market" not in pts[1] and pts[1]["season/cum_watchlist_hit_rate"] == 0.5


def test_fixed_report_card_and_length_feedback() -> None:
    """Live week 4: code writes a nothing-to-grade card; regenerations restate the limits."""
    from nflengine.digest.payload import ReportCard
    from nflengine.digest.run import fixed_sections
    from nflengine.digest.synthesize import length_feedback

    rc = ReportCard(status="no_saved_predictions", note="no predictions were saved for week 3.")
    payload = make_payload(report_card=rc)
    fixed = fixed_sections(payload)
    assert fixed == {"report_card": "Report card: no predictions were saved for week 3."}
    llm = ScriptedLLM([{k: v for k, v in GOOD.items() if k != "report_card"}])
    budgets = {**BUDGETS, "under_the_hood": 110, "players_to_watch": 160}
    s = synthesize(payload, build_fact_index(payload), llm, load_prompt(), budgets, fixed=fixed)
    assert s.sections["report_card"].startswith("Report card: no predictions")
    assert "report_card" not in [x["id"] for x in llm.specs[0]["sections"]]
    # the code-written card isn't measured against the LLM's word budget (no length_short)
    assert not any(i.section == "report_card" for i in s.final.result("length_short").issues)
    fb = length_feedback({"team_trends": "word " * 149}, {"team_trends": 120})
    assert fb == ["[length] team_trends: 149 words now; aim for 120, never more than 150"]
    # the whole digest's cap is restated too (P05: 740 words on a 735 cap, sections all fine)
    both = length_feedback(
        {"team_trends": "word " * 149, "report_card": "word " * 20},
        {"team_trends": 120},
        {"team_trends": 120, "report_card": 60},
    )
    assert both[-1].startswith("[length] whole digest: 169 words now; aim for 180")
    assert "never more than 189 in all" in both[-1]


def test_evidence_person_names_their_team() -> None:
    """Live week 4: "without guard Aaron Banks; pressure rate +8.8" binds to the Packers."""
    from nflengine.digest import format as F
    from nflengine.digest.payload import TrendItem

    trend = TrendItem(
        team="GB", team_name="Packers", direction="down", trend_delta=F.epa_change(-0.11, 3),
        window=F.weeks(3), net_rating=F.epa(-0.04),
        evidence=["recently without guard Aaron Banks", "pass rush's pressure rate +8.8 points"],
        people=["Aaron Banks"],
    )  # fmt: skip
    payload = make_payload(team_trends=[trend])
    fx = build_fact_index(payload)
    from nflengine.digest.checks import run_checks

    s = "Context: recently without guard Aaron Banks; pass rush's pressure rate +8.8 points."
    assert run_checks({"team_trends": s}, fx).result("entity_binding").passed
