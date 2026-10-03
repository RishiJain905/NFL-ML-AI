"""The digest payload: one validated JSON document per run (documentation/06 -> Payload).

Every number is a `Num` (`{value, display}`). The display string is made once, in
`digest/format.py`, and the LLM must copy it exactly: the checks only accept numbers that
appear in some display string (or in a code-made text field such as `rank_note`).

Slots for later phases stay in the schema now: `graph_insights` (P05) and a real model
behind `players_to_watch` (P06; v0 fills it with a labelled heuristic).
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
    scoreboard_highlights: list[str] = Field(default_factory=list)  # player models (P06)


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


# ---- players to watch (heuristic until P06) ---------------------------------------------------


class WatchItem(Model):
    player: str
    player_id: str
    team: str
    team_name: str
    position: str
    opponent: str
    opponent_name: str
    game_id: str
    target: str  # "receiving yards"
    baseline: Num
    usage_metric: str  # "target share"
    usage_recent: Num
    usage_before: Num
    usage_before_note: str  # "earlier this season" / "last season"
    # value = rank (1 = most EPA allowed); display says which end: "3rd-weakest", "weakest",
    # "4th-strongest". A bare "1st" was read as "best" by the real LLM, so never show one.
    opp_def_rank: Num
    opp_def_unit: str
    projection: Num | None = None  # P06
    interval: Num | None = None  # P06
    confidence: Literal["low", "medium", "high"] = "low"
    source: Literal["heuristic", "model"] = "heuristic"
    drivers: list[str] = Field(default_factory=list)


# ---- later phases -----------------------------------------------------------------------------


class GraphInsight(Model):
    insight_type: str
    section: str
    strength: float
    entities: list[str]
    facts: list[dict[str, str | int | float | None]] = Field(default_factory=list)
    confidence: str = "low"
    graph_query: str


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
    graph_insights: list[GraphInsight] = Field(default_factory=list)  # P05
    news: list[NewsItem] = Field(default_factory=list)

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)
