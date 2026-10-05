"""The graph query library v1 (documentation/05 -> Query library; plan P05).

Each query is a parameterized `.cypher` file next to this module. Every row it returns
carries the entities, numbers already computed (the LLM does no math), `insight_type`, a
`strength` score in [0, 1] and `sample_size`. Queries run in **read** transactions only.

| query | insight_type | digest section |
|---|---|---|
| q0_starters_out | (none: rows) | the code-written "Starters out" list |
| q1_revenge | revenge | non_obvious |
| q2_injury_ripple | injury_ripple | matchup_risk |
| q3_qb_change | qb_change | matchup_risk |
| q4_common_opponents | common_opponents | non_obvious |
| q5_coach_reunion | coach_reunion | non_obvious |
| q5_coaching_tree | coaching_tree | non_obvious |
| q6_former_teammates | former_teammates | non_obvious |
| q7_style_matchup | style_matchup | non_obvious |
| q7_play_action | play_action | non_obvious |
| q8_trend_mismatch | trend_mismatch | matchup_risk / non_obvious |
| q9_officiating | officiating | non_obvious |
| q10_network_hub | network_hub | non_obvious |
| q10_usage_comp | usage_comp | non_obvious |
| q11_unit_mismatch | unit_mismatch | non_obvious |
| q12_special_teams | special_teams | non_obvious |

Q5 is documentation/05's Q5 in two parts: head coaches vs a former team (nflverse), and the
coaching tree (coordinators, mentors), which needs the optional hand-made seed
(`config/coaching_seed.csv`) and is quiet without it. Q11 / Q12 are unit-level stories
(offense vs defense units, special teams). P08 added Q6 (former teammates), Q7 (style
matchups: scrambling / deep-passing QBs, play-action offenses), Q9 (referee crews) and the
GDS-driven Q10 queries, which read what `graph/gds.py` writes (PASS_CENTRALITY, SIMILAR_TO).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j import Driver

QUERY_DIR = Path(__file__).resolve().parent
OFFENSE_GROUPS = ["RB", "WR", "TE", "OL"]
DEFENSE_GROUPS = ["DL", "LB", "DB"]

# query name -> extra parameters (besides $season / $week)
LIBRARY: dict[str, dict[str, Any]] = {
    "q0_starters_out": {},  # not an insight: the digest's "Starters out" list
    "q1_revenge": {"min_old_games": 4, "min_snap_pct": 0.5},
    "q2_injury_ripple": {"groups": OFFENSE_GROUPS + DEFENSE_GROUPS, "history_seasons": 2},
    "q3_qb_change": {"min_receiver_targets": 5},
    "q4_common_opponents": {},
    "q5_coach_reunion": {},
    "q5_coaching_tree": {},  # only with the optional coaching seed
    "q6_former_teammates": {"min_targets": 40},
    # league percentile cut; QB-seasons with enough dropbacks; defense sample floors
    "q7_style_matchup": {
        "pct": 0.75,
        "min_dropbacks": 200,
        "min_current_dropbacks": 150,
        "min_class_dropbacks": 100,
        "min_games": 4,
        "min_other": 8,
        "min_gap": 0.05,
    },
    "q7_play_action": {
        "pct": 0.75,
        "min_current_games": 3,
        "min_games": 4,
        "min_other": 8,
        "min_gap": 0.05,
    },
    "q8_trend_mismatch": {},
    # crews are known only after their games (a live run may have a week-W crew)
    "q9_officiating": {
        "roles": ["Referee", "R"],
        "seasons": 2,
        "min_games": 10,
        "min_pen_gap": 1.5,
        "min_split_gap": 1.0,
    },
    # GDS (graph/gds.py): passing-network hub out; usage comparison (KNN)
    "q10_network_hub": {"min_share": 0.15, "max_rank": 3, "min_games": 2},
    "q10_usage_comp": {"max_rank": 10, "min_target_share": 0.18, "min_carry_share": 0.4},
    # tier: both units must be in the top / bottom `tier` of the league that week
    "q11_unit_mismatch": {"tier": 8},
    # the per-game gap is shrunk by n / (n + shrink_games) before min_gap (ST EPA is noisy)
    "q12_special_teams": {"min_games": 3, "min_gap": 2.0, "shrink_games": 4.0},
}


def query_text(name: str) -> str:
    if name not in LIBRARY:
        raise KeyError(f"unknown graph query {name!r}; library: {', '.join(LIBRARY)}")
    return (QUERY_DIR / f"{name}.cypher").read_text(encoding="utf-8")


@dataclass
class QueryResult:
    name: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rows": len(self.rows),
            "seconds": round(self.seconds, 3),
            "error": self.error,
            "results": self.rows,
        }


def _plain(v: Any) -> Any:
    """Neo4j values -> JSON-friendly Python values."""
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if hasattr(v, "iso_format"):  # neo4j.time types
        return v.iso_format()
    return v


# A query may name a relationship type an optional input didn't create (no coaching seed, a
# failed GDS job): Neo4j then warns "relationship type does not exist" (classification
# UNRECOGNIZED) through the driver's logger on every build. The empty result says it already.
QUIET = {"notifications_disabled_classifications": ["UNRECOGNIZED"]}


def run_query(driver: Driver, name: str, season: int, week: int, **overrides: Any) -> QueryResult:
    """Run one library query in a read transaction; never raises (errors are recorded)."""
    params = {"season": season, "week": week, **LIBRARY[name], **overrides}
    text = query_text(name)
    t0 = time.perf_counter()
    try:
        with driver.session(**QUIET) as session:
            records = session.execute_read(lambda tx: [r.data() for r in tx.run(text, **params)])
        rows = [{k: _plain(v) for k, v in r.items()} for r in records]
        return QueryResult(name, rows, time.perf_counter() - t0)
    except Exception as e:  # one broken query must not sink the build
        from nflengine.graph.client import safe_error

        return QueryResult(name, [], time.perf_counter() - t0, safe_error(e))


def run_library(driver: Driver, season: int, week: int) -> dict[str, QueryResult]:
    return {name: run_query(driver, name, season, week) for name in LIBRARY}
