"""The digest payload: one validated JSON document per run (documentation/06 -> Payload).

Every number is a `Num` (`{value, display}`). The display string is made once, in
`digest/format.py`, and the LLM must copy it exactly: the checks only accept numbers that
appear in some display string (or in a code-made text field such as `rank_note`).

`graph_insights` (P05) carry the knowledge-graph sections; `players_to_watch` and
`tough_spots` come from the player model (P06; the P04 heuristic, labelled `heuristic`, is
the fallback when a week has no projections).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Num(Model):
    value: float | int | None
    display: str


# ---- meta -------------------------------------------------------------------------------------


class Meta(Model):
    season: int
    week: int
    run_id: str
    mode: Literal["live", "backtest"]
    run_time: str  # ISO, UTC: the moment the run "happened" (simulated for backtests)
    season_display: Num
    week_display: Num
    prev_week_display: Num
    data_through: str  # e.g. "2026-W03"
    injury_snapshot: str | None = None
    market_data_used: bool = False
    model_versions: dict[str, str] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)  # source -> freshness note
    followed_teams: list[str] = Field(default_factory=list)
    early_season: bool = False  # weeks 1-3: ratings lean on last season (documentation/02)
    # knowledge graph (P05): ok | unavailable (Neo4j down / build failed) | off
    graph_status: Literal["ok", "unavailable", "off"] = "off"
    graph_note: str | None = None  # why the graph sections are missing, when they are


# ---- report card ------------------------------------------------------------------------------


class BiggestMiss(Model):
    game_id: str
    game: str  # "Steelers at Browns" (away at home)
    home: str = ""
    away: str = ""
    favored: str
    favored_name: str
    prob: Num
    winner: str
    winner_name: str
    final_score: str  # "24-17" (winner first)


class CalibrationBucket(Model):
    bucket: str  # "60-70%"
    games: Num
    favorite_wins: Num


class SeasonToDate(Model):
    weeks: Num
    picks_correct: Num
    picks_total: Num
    brier: Num
    brier_elo: Num
    watchlist_hits: Num | None = None
    watchlist_total: Num | None = None


class LookbackItem(Model):
    """One of last week's watch-list picks against what happened (P06; code-written)."""

    player: str
    player_id: str
    team: str
    team_name: str
    position: str = ""
    target: str  # "receiving yards"
    # code-made: "projected 84 receiving yards, range 52–118 receiving yards → actual 97
    # receiving yards: inside the range, above his baseline of 61 receiving yards"
    text: str
    played: bool = False  # played and scored (an unscored pressure count is not)
    hit: bool | None = None  # actual > baseline
    inside: bool | None = None  # P10 <= actual <= P90 (model picks only)


class ReportCard(Model):
    status: Literal["scored", "no_saved_predictions", "first_week"]
    scored_week: int | None = None
    scored_week_display: Num | None = None
    note: str | None = None
    picks_correct: Num | None = None
    picks_total: Num | None = None
    brier: Num | None = None
    brier_elo: Num | None = None
    points_mae: Num | None = None
    biggest_miss: BiggestMiss | None = None
    watchlist_hits: Num | None = None
    watchlist_total: Num | None = None
    not_graded: int = 0  # games predicted after kickoff (never graded)
    calibration: list[CalibrationBucket] = Field(default_factory=list)  # season to date
    season_to_date: SeasonToDate | None = None
    # player models (P06), code-made: how the projections are doing (always one weak spot;
    # backtest numbers labelled as such) and last week's picks, projected vs actual
    scoreboard_highlights: list[str] = Field(default_factory=list)
    watch_lookback: list[LookbackItem] = Field(default_factory=list)
    watchlist_inside: Num | None = None  # model picks whose actual fell inside the range


# ---- games ------------------------------------------------------------------------------------


class PredictedScore(Model):
    home: Num
    away: Num


class Consensus(Model):
    direction: Literal["higher_on_home", "higher_on_away"]
    size: Literal["notable", "large"]
    team: str  # the team the model likes more than consensus
    team_name: str
    text: str = ""  # code-made, copy as is: "much higher on the Bears than consensus"


class GameItem(Model):
    game_id: str
    kickoff: str  # display, US Eastern
    status: Literal["upcoming", "started"]
    home: str
    away: str
    home_name: str
    away_name: str
    matchup: str = ""  # code-made "Titans at Colts" (away at home): the only way to name it
    neutral_site: bool = False
    home_win_prob: Num
    away_win_prob: Num
    favored: str
    expected_margin: Num
    predicted_score: PredictedScore
    confidence: str  # toss-up / lean / solid / strong (P03 bands)
    model_vs_consensus: Consensus | None = None
    market_fallback: bool = False
    home_qb: str | None = None
    away_qb: str | None = None


class HighlightGame(Model):
    game_id: str
    matchup: str
    team: str  # the favorite
    team_name: str
    prob: Num  # the favorite's win probability
    rank_note: str  # code-made: "the most lopsided call", "the 2nd-closest game"


class GameHighlights(Model):
    """Code-made orderings, so the LLM never ranks games itself (D53)."""

    most_lopsided: list[HighlightGame] = Field(default_factory=list)  # top 3, in order
    closest: list[HighlightGame] = Field(default_factory=list)  # top 2, in order


# ---- trends -----------------------------------------------------------------------------------


class TrendDriver(Model):
    unit: str  # "pass defense"
    change: Num
    effect: Literal["better", "worse"]


class TrendItem(Model):
    team: str
    team_name: str
    direction: Literal["up", "down"]
    trend_delta: Num
    window: Num  # "3 weeks"
    net_rating: Num
    drivers: list[TrendDriver] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)  # players named in the evidence
    predictive_note: Literal["descriptive"] = "descriptive"  # D47: trends aren't predictive


# ---- last week under the hood -----------------------------------------------------------------


class UnderHoodItem(Model):
    player: str
    player_id: str
    team: str
    team_name: str
    position: str
    metric: str
    label: str
    unit: str = "yards"  # format.metric unit key (count = a per-game tally such as pressures)
    kind: Literal["riser", "faller", "standout"]
    last_week: Num
    season_avg: Num | None = None  # the player's own norm
    norm_note: str | None = None  # e.g. "his average over the previous 2 games"
    volume: Num
    volume_label: str
    rank_note: str
    source: str
    confidence: Literal["medium", "low"] = "medium"


# ---- players to watch (model from P06; the P04 heuristic is the fallback) ---------------------


class WatchItem(Model):
    """A players-to-watch pick, or a tough spot (`Payload.tough_spots`).

    `source: "model"` (P06): projection (P50), the P10-P90 `interval`, the baseline,
    `vs_baseline` (words say above / below), code-made `drivers` and notes.
    `source: "heuristic"` (the P04 fallback when no projections exist): the usage and
    opponent-defense fields instead.
    """

    player: str
    player_id: str
    team: str
    team_name: str
    position: str
    opponent: str
    opponent_name: str
    game_id: str
    matchup: str = ""  # code-made "Titans at Colts" (away at home)
    target: str  # "receiving yards"
    group: str | None = None  # model position group ("WR/TE"; model picks)
    baseline: Num
    # heuristic picks (P04 fallback)
    usage_metric: str | None = None  # "target share"
    usage_recent: Num | None = None
    usage_before: Num | None = None
    usage_before_note: str | None = None  # "earlier this season" / "last season"
    # value = rank (1 = most EPA allowed); display says which end: "3rd-weakest", "weakest",
    # "4th-strongest". A bare "1st" was read as "best" by the real LLM, so never show one.
    opp_def_rank: Num | None = None
    opp_def_unit: str | None = None
    # model picks (P06)
    projection: Num | None = None  # P50: "84 receiving yards"
    interval: Num | None = None  # P10-P90: "52–118 receiving yards"
    vs_baseline: Num | None = None  # "23 receiving yards above his baseline"
    # where a thin baseline comes from ("his baseline comes from 2 games this season")
    baseline_note: str | None = None
    role_note: str | None = None  # a role change ("regular teammates who missed ...")
    injury_note: str | None = None  # "listed questionable on this week's injury report"
    confidence: Literal["low", "medium", "high"] = "low"
    source: Literal["heuristic", "model"] = "heuristic"
    # code-made: "<phrase> (puts the projection 9 receiving yards above a typical player in
    # his group)"; only drivers >= 20% of the gap to baseline, same direction first
    drivers: list[str] = Field(default_factory=list)
    driver_note: str | None = None  # "no single factor stands out" when no driver qualifies


# ---- later phases -----------------------------------------------------------------------------


class GraphFact(Model):
    """One code-made, time-scoped phrase with its numbers ("In 5 games without X since the
    start of the 2024 season, the 49ers defense allowed +0.11 EPA per play ..."). The LLM
    copies it; its numbers belong to `owners` (team codes and player ids)."""

    text: str
    owners: list[str] = Field(default_factory=list)


class GraphPerson(Model):
    player: str
    player_id: str
    team: str
    role: str  # out | stepped in | next on chart | former player | expected starter | ...


class GraphInsight(Model):
    """A knowledge-graph finding for *Matchup / risk to watch* or *Non-obvious insights*
    (P05; built by `graph/insights.py` from the query library)."""

    insight_id: str  # stable across weeks for the same story (novelty)
    insight_type: Literal[
        "revenge",
        "injury_ripple",
        "qb_change",
        "common_opponents",
        "trend_mismatch",
        "coach_reunion",
        "unit_mismatch",
        "special_teams",
    ]
    section: Literal["matchup_risk", "non_obvious", "more"]  # more = a code-written one-liner
    strength: float
    confidence: Literal["low", "medium"] = "low"
    graph_query: str
    game_id: str
    matchup: str = ""  # code-made "Titans at Colts" (away at home)
    teams: list[str] = Field(default_factory=list)  # the game's two teams first
    people: list[GraphPerson] = Field(default_factory=list)
    headline: str  # code-made, no numbers
    # code-made one line (with the key number) for the "More from the graph" list
    brief: str = ""
    facts: list[GraphFact] = Field(default_factory=list)
    sample: Num  # what the main comparison rests on ("5 games without him")
    note: str = ""  # caveat to keep ("Descriptive: ...")


class QBChangeNote(Model):
    """A team whose expected starting QB isn't its main starter this season (graph Q3),
    flagged in the game table. `text` is code-made and time-scoped."""

    game_id: str
    team: str
    team_name: str
    qb: str
    regular: str
    confirmed: bool  # schedule / depth chart / injury report named him (a live run)
    text: str  # "Jalon Daniels starts for Baker Mayfield (out: thumb)"


class StarterOut(Model):
    """A regular starter who won't play this week (graph Q0), for the code-written list."""

    game_id: str
    team: str
    team_name: str
    player: str
    player_id: str
    position: str
    text: str  # "QB Baker Mayfield (out: thumb)"


class NewsItem(Model):
    source: str = "ESPN"
    headline: str
    summary: str | None = None
    published: str | None = None
    entities: list[str] = Field(default_factory=list)


class Payload(Model):
    meta: Meta
    report_card: ReportCard
    games: list[GameItem] = Field(default_factory=list)  # upcoming games only
    # games that kicked off before the run: names only, so no stale forecast reaches the LLM
    started_games: list[str] = Field(default_factory=list)
    game_highlights: GameHighlights = Field(default_factory=GameHighlights)
    team_trends: list[TrendItem] = Field(default_factory=list)
    under_the_hood: list[UnderHoodItem] = Field(default_factory=list)
    players_to_watch: list[WatchItem] = Field(default_factory=list)
    # regular starters projected well below their own baseline (P06): a code-written list
    # under *Matchup / risk to watch*
    tough_spots: list[WatchItem] = Field(default_factory=list)
    graph_insights: list[GraphInsight] = Field(default_factory=list)  # P05: picked items
    # code-written graph extras (after P05): QB-change flags for the game table, starters
    # out, and the strong stories that didn't fit the prose ("More from the graph")
    qb_changes: list[QBChangeNote] = Field(default_factory=list)
    starters_out: list[StarterOut] = Field(default_factory=list)
    graph_more: list[GraphInsight] = Field(default_factory=list)
    news: list[NewsItem] = Field(default_factory=list)

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)
