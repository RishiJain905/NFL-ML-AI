"""Fact index: entity -> the display strings (and number atoms) it owns (P04 task).

Built from the payload; used by the checks:
- **number provenance**: every number in the prose must be an atom of some display string;
- **entity binding**: in a sentence with numbers, some entity named in that sentence must
  own each number (a number attached to the wrong player or team fails);
- **unknown entities**: teams and players named in the prose must be payload entities.

Owners: game numbers belong to the side they describe (the home win % to the home team;
the margin, which names the favourite, to both); report-card numbers to "the model" (Elo's
Brier also to "Elo"), except calibration counts, which belong to their bucket ("70-80%");
trend numbers to the team; under-the-hood and watch-list numbers to the player. `meta`
numbers (season, week) are global and need no owner.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from nflengine.digest.format import normalize_text, number_atoms
from nflengine.digest.names import last_name, team_aliases
from nflengine.digest.payload import Num, Payload

MODEL_ALIASES = [
    "the model",
    "model",
    "our picks",
    "picks",
    "we",
    "our",
    "report card",
    "pick record",
    "brier",
    "calibration",
    "watch list",
    "watch-list",
]
_RANGE_PCT = re.compile(r"(\d+)\s?-\s?(\d+)%")


@dataclass
class Entity:
    key: str
    kind: str  # team | player | model | elo | meta
    aliases: list[str]
    displays: set[str] = field(default_factory=set)
    atoms: set[str] = field(default_factory=set)
    teams: set[str] = field(default_factory=set)  # a person's team(s): naming him names them

    def add(self, *items: Num | str | None) -> None:
        for it in items:
            if it is None:
                continue
            text = it.display if isinstance(it, Num) else str(it)
            self.displays.add(normalize_text(text))
            self.atoms.update(text_atoms(text))


def text_atoms(text: str) -> set[str]:
    """Atoms of a display string, plus "60%" for a range written "60-70%"."""
    norm = normalize_text(text)
    atoms = set(number_atoms(norm))
    for a, _ in _RANGE_PCT.findall(norm):
        atoms.add(f"{a}%")
    return atoms


@dataclass
class FactIndex:
    entities: dict[str, Entity] = field(default_factory=dict)
    low_confidence: list[str] = field(default_factory=list)  # entity keys needing a hedge
    # meaning facts for the semantic checks (D53)
    matchups: set[tuple[str, str]] = field(default_factory=set)  # this week: (away, home)
    past_matchups: set[tuple[str, str]] = field(default_factory=set)  # e.g. last week's miss
    game_bands: dict[str, str] = field(default_factory=dict)  # team -> its game's band
    lopsided_teams: set[str] = field(default_factory=set)  # teams in the top lopsided games
    closest_teams: set[str] = field(default_factory=set)  # teams in the closest games
    consensus: dict[str, str] = field(default_factory=dict)  # team -> notable | large

    def get(self, key: str, kind: str, aliases: Iterable[str]) -> Entity:
        if key not in self.entities:
            self.entities[key] = Entity(key, kind, list(dict.fromkeys(aliases)))
        return self.entities[key]

    def team(self, code: str) -> Entity:
        return self.get(f"team:{code}", "team", team_aliases(code))

    def player(self, player_id: str, name: str) -> Entity:
        self.entities.pop(f"name:{name}", None)  # a numbers-owning entry replaces a bare name
        return self.get(f"player:{player_id}", "player", [name])

    def name(self, name: str) -> Entity:
        """A person the payload mentions without numbers of their own (a QB, a player out)."""
        for e in self.entities.values():
            if e.kind == "player" and e.aliases and e.aliases[0] == name:
                return e
        return self.get(f"name:{name}", "player", [name])

    @property
    def all_atoms(self) -> set[str]:
        return set().union(*(e.atoms for e in self.entities.values())) if self.entities else set()

    @property
    def global_atoms(self) -> set[str]:
        meta = self.entities.get("meta")
        return set(meta.atoms) if meta else set()

    def finalize(self) -> FactIndex:
        """Add unique last names as player aliases ("Mahomes"), after all players exist."""
        players = [e for e in self.entities.values() if e.kind == "player"]
        lasts: dict[str, int] = {}
        for full in {e.aliases[0] for e in players}:
            ln = last_name(full).lower()
            lasts[ln] = lasts.get(ln, 0) + 1
        for e in players:
            ln = last_name(e.aliases[0])
            if lasts[ln.lower()] == 1 and ln not in e.aliases:
                e.aliases.append(ln)
        return self

    def entity_names(self, kind: str | None = None) -> set[str]:
        return {
            a.lower()
            for e in self.entities.values()
            if kind is None or e.kind == kind
            for a in e.aliases
        }


def build_fact_index(payload: Payload) -> FactIndex:
    fx = FactIndex()
    m = payload.meta
    meta = fx.get("meta", "meta", [])
    meta.add(m.season_display, m.week_display, m.prev_week_display)

    model = fx.get("model", "model", MODEL_ALIASES)
    elo = fx.get("elo", "elo", ["Elo", "Elo baseline"])
    rc = payload.report_card
    model.add(rc.scored_week_display, rc.picks_correct, rc.picks_total, rc.brier, rc.points_mae)
    model.add(rc.watchlist_hits, rc.watchlist_total, rc.brier_elo, rc.note)
    elo.add(rc.brier_elo, rc.scored_week_display)
    for b in rc.calibration:
        # a bucket owns its own counts, so "70-80%: 14 of 20" fails when 14 of 20 is
        # another bucket's (a real GLM slip in the 2025 week-4 backtest)
        label = normalize_text(b.bucket)
        bucket = fx.get(f"bucket:{label}", "bucket", [label, label.replace("%", "")])
        bucket.add(b.bucket, b.games, b.favorite_wins)
    if rc.season_to_date:
        s = rc.season_to_date
        model.add(s.weeks, s.picks_correct, s.picks_total, s.brier, s.brier_elo)
        model.add(s.watchlist_hits, s.watchlist_total)
        elo.add(s.brier_elo)
    if rc.biggest_miss:
        bm = rc.biggest_miss
        model.add(bm.prob, bm.final_score)
        fx.team(bm.favored).add(bm.prob, bm.final_score)
        fx.team(bm.winner).add(bm.prob, bm.final_score)
        if bm.home and bm.away:
            fx.past_matchups.add((bm.away, bm.home))

    for g in payload.games:
        home, away = fx.team(g.home), fx.team(g.away)
        home.add(g.home_win_prob, g.predicted_score.home, g.expected_margin)
        away.add(g.away_win_prob, g.predicted_score.away, g.expected_margin)
        for qb, code in ((g.home_qb, g.home), (g.away_qb, g.away)):
            if qb:
                fx.name(qb).teams.add(code)
        fx.matchups.add((g.away, g.home))
        if g.status == "upcoming":
            fx.game_bands[g.home] = fx.game_bands[g.away] = g.confidence
        if g.model_vs_consensus is not None:
            fx.consensus[g.model_vs_consensus.team] = g.model_vs_consensus.size
    games_by_id = {g.game_id: g for g in payload.games}
    for hs, target in (
        (payload.game_highlights.most_lopsided, fx.lopsided_teams),
        (payload.game_highlights.closest, fx.closest_teams),
    ):
        for h in hs:
            g = games_by_id.get(h.game_id)
            target.update((g.home, g.away) if g else (h.team,))
    for h in [*payload.game_highlights.most_lopsided, *payload.game_highlights.closest]:
        g = games_by_id.get(h.game_id)
        fx.team(h.team).add(h.prob)  # the favorite's probability is the favorite's only
        for code in (g.home, g.away) if g else (h.team,):
            fx.team(code).add(h.rank_note)

    for t in payload.team_trends:
        e = fx.team(t.team)
        e.add(t.trend_delta, t.window, t.net_rating, *[d.change for d in t.drivers])
        e.add(*t.evidence)
        for person in t.people:
            fx.name(person).teams.add(t.team)

    for u in payload.under_the_hood:
        p = fx.player(u.player_id, u.player)
        p.add(u.last_week, u.season_avg, u.volume, u.rank_note, u.norm_note)
        fx.team(u.team)  # the team is a known entity, but doesn't own the player's numbers
        if u.confidence == "low":
            fx.low_confidence.append(p.key)

    for w in payload.players_to_watch:
        p = fx.player(w.player_id, w.player)
        p.add(w.baseline, w.usage_recent, w.usage_before, w.opp_def_rank, w.projection)
        p.add(w.interval, *w.drivers)
        fx.team(w.team)
        fx.team(w.opponent)
        if w.confidence == "low":
            fx.low_confidence.append(p.key)
    return fx.finalize()
