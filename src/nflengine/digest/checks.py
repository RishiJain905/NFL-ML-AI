"""Automated checks on the LLM's prose (documentation/06 -> Automated checks).

They run on every generation, for every provider, against the fact index built from the
payload. Fail-level checks trigger one regeneration and then a warning banner. Eleven
checks (documentation/guides/llm-digest-writer.md has the full table with examples):

- complete (fail): every requested section has prose (a partial reply must not pass).
- number_provenance (fail): a number that isn't in any payload display string.
- spelled_out_numbers (fail): number words ("two-thirds", "a dozen", "doubled", "half")
  that aren't in the payload.
- entity_binding (fail): a number in a sentence where no named entity owns it (wrong
  player / team).
- unknown_entities (fail): a team or rostered player named in the prose but absent from
  the payload.
- meaning (fail): "A at B" must be a real game in that home / road order; game
  superlatives must match the code-made `game_highlights`; consensus-gap tiers must match.
- banned_language (fail): betting / fantasy words (allow-list: "offensive line", "line of
  scrimmage" ...).
- length (fail): a section > 25% over budget, or the total > 5% over the sum of the LLM's
  sections' budgets; `length_short` (warn) when a section is > 25% under. Code-written
  sections (a report card with nothing to grade, a graph section with no pick) aren't
  measured.
- hedging (warn): a low-confidence item mentioned without a hedge phrase, or a graph
  section holding a low-confidence item with no hedge phrase at all.
- name_heuristic (warn): a capitalized name that matches nothing known (possible made-up
  player).

Under-length is a warning, not a failure (D53): padding a thin section invites invention.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Literal

from nflengine.digest.facts import Entity, FactIndex
from nflengine.digest.format import normalize_text, number_atoms
from nflengine.digest.names import TEAMS, team_aliases

FAIL: Literal["fail"] = "fail"
WARN: Literal["warn"] = "warn"

LENGTH_TOLERANCE = 0.25
TOTAL_TOLERANCE = 0.05
# Sections whose subject is implicit: every sentence of the report card is about the
# model's own record, so "Week 3 went 12 of 16" binds without naming "the model". Team,
# player and calibration-bucket numbers still need their owner named.
SECTION_OWNERS: dict[str, tuple[str, ...]] = {"report_card": ("model",)}

# ---- word lists -------------------------------------------------------------------------------

NUMBER_WORDS = [
    "zero", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety", "hundred", "thousand", "million", "dozen", "dozens", "half",
    "halves", "twice", "double", "doubled", "doubles", "doubling", "triple", "tripled",
    "triples", "quadruple", "quadrupled", "one-third", "one-thirds", "two-thirds",
    "three-quarters", "one-quarter", "one-half", "a third", "a quarter",
]  # fmt: skip
# Football phrases that contain a number word but aren't a numeric claim.
NUMBER_WORD_ALLOW = [
    "first half", "second half", "1st half", "2nd half", "half of the field",
    "double coverage", "double team", "double-team", "double-teamed", "triple coverage",
    "two-minute drill", "two-minute warning", "two-point", "two-point conversion",
    "three-and-out", "three-and-outs", "red zone",
]  # fmt: skip

BANNED_PATTERNS = [
    r"spreads?",
    r"lines?",
    r"odds",
    r"money ?lines?",
    r"over[/ -]under",
    r"total points",
    r"point totals?",
    r"bets?",
    r"betting",
    r"bettors?",
    r"wager\w*",
    r"locks?",
    r"cover(?:s|ed|ing)?",
    r"fades?",
    r"value (?:play|pick|bet)s?",
    r"fantasy",
    r"start[/ -]sit",
    r"start or sit",
    r"sit[/ -]start",
    r"ppr",
    r"points? per reception",
    r"sportsbooks?",
    r"parlays?",
    r"vig",
    r"against the spread",
    r"ats",
    r"waiver wire",
]
BANNED_ALLOW = [
    "offensive line",
    "offensive lines",
    "defensive line",
    "defensive lines",
    "line of scrimmage",
    "o-line",
    "d-line",
    "line up",
    "lines up",
    "lined up",
    "front line",
    "goal line",
    "goal-line",
    "sideline",
    "sidelines",
    "spread offense",
    "spread formation",
    "fade route",
    "fade routes",
    "lockdown",
    "lock-down",
    "cover 0",
    "cover 1",
    "cover 2",
    "cover 3",
    "cover 4",
    "cover 6",
    "cover-0",
    "cover-1",
    "cover-2",
    "cover-3",
    "cover-4",
    "cover-6",
]

HEDGES = [
    "low confidence",
    "low-confidence",
    "heuristic",
    "small sample",
    "small-sample",
    "tentative",
    "early",
    "uncertain",
    "could",
    "might",
    "may",
    "maybe",
    "grain of salt",
    "limited",
    "rough",
    "only a",
    "not a projection",
    "no model",
    "descriptive",
    "not predictive",
    "small samples",
]

# Capitalized words that aren't names (the name heuristic ignores them).
COMMON_CAPS = {
    "The", "A", "An", "And", "But", "With", "In", "On", "At", "For", "Of", "To", "From",
    "His", "Her", "Their", "Its", "This", "That", "These", "Those", "Last", "Next", "Week",
    "Weeks", "Season", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Night", "Football", "NFL", "EPA", "Elo", "NGS", "PFR", "FTN", "ESPN",
    "Next Gen Stats", "Gen", "Stats", "Brier", "Report", "Card", "Game", "Outlook", "Team",
    "Trend", "Shifts", "Under", "Hood", "Players", "Watch", "QB", "QBs", "WR", "WRs", "TE",
    "TEs", "RB", "RBs", "October", "November", "December", "January", "February",
    "September", "Pro", "Bowl", "Super", "Wild", "Divisional", "Conference",
    "Per", "No", "Not", "Still", "Up", "Down", "Both", "Over", "Only", "So", "If", "As",
}  # fmt: skip


# ---- results ----------------------------------------------------------------------------------


@dataclass
class Issue:
    section: str
    token: str
    reason: str
    sentence: str = ""


@dataclass
class CheckResult:
    name: str
    level: Literal["fail", "warn"]
    passed: bool
    issues: list[Issue] = field(default_factory=list)


@dataclass
class CheckReport:
    results: list[CheckResult]
    word_counts: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results if r.level == FAIL)

    @property
    def failed(self) -> list[str]:
        return [r.name for r in self.results if r.level == FAIL and not r.passed]

    @property
    def warnings(self) -> list[str]:
        return [r.name for r in self.results if r.level == WARN and not r.passed]

    def result(self, name: str) -> CheckResult:
        return next(r for r in self.results if r.name == name)

    def feedback(self) -> list[str]:
        """Offending tokens with reasons, for the one regeneration (fail-level only)."""
        return [
            f"[{r.name}] {i.section}: {i.token!r} ({i.reason})"
            for r in self.results
            if r.level == FAIL and not r.passed
            for i in r.issues
        ]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "failed": self.failed,
            "warnings": self.warnings,
            "word_counts": self.word_counts,
            "checks": [
                {
                    "name": r.name,
                    "level": r.level,
                    "passed": r.passed,
                    "issues": [asdict(i) for i in r.issues],
                }
                for r in self.results
            ],
        }


# ---- text helpers -----------------------------------------------------------------------------

_MD = re.compile(r"[*_`#>|]")
_SENT_SPLIT = re.compile(
    r"(?<!\bJr\.)(?<!\bSr\.)(?<!\bvs\.)(?<!\bSt\.)(?<!\s[A-Z]\.)(?<!\.[A-Z]\.)(?<!^[A-Z]\.)"
    r"(?<=[.!?])\s+"
)


def plain(text: str) -> str:
    return normalize_text(_MD.sub("", text))


# After "Jr." / "Sr." the splitter normally keeps going ("Michael Penix Jr. threw"), but a
# common sentence opener means a new sentence ("... Deebo Samuel Sr. The Vikings ...").
_SUFFIX_SPLIT = re.compile(
    r"(?<=\b[JS]r\.)\s+(?=(?:The|A|An|In|On|At|His|Their|That|This|It|But|And|With|For|"
    r"Meanwhile|Elsewhere|Season|Last|Next|Week)\b)"
)


def sentences(text: str) -> list[str]:
    out: list[str] = []
    for line in plain(text).splitlines():
        line = line.strip().lstrip("-•").strip()
        if line:
            for s in _SENT_SPLIT.split(line):
                out += [x.strip() for x in _SUFFIX_SPLIT.split(s) if x.strip()]
    return out


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'%.,+\-]*", plain(text)))


def _alias_re(alias: str) -> re.Pattern[str]:
    """An entity name as a whole word. A hyphen counts as a boundary, so the teams in a
    matchup written "Vikings-Steelers" are both named (a GLM false positive in P04)."""
    flags = 0 if any(c.isupper() for c in alias) else re.IGNORECASE
    return re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)", flags)


def _phrase_re(words: Iterable[str]) -> re.Pattern[str]:
    alts = sorted({re.escape(w) for w in words}, key=len, reverse=True)
    return re.compile(rf"(?<![\w-])(?:{'|'.join(alts)})(?![\w-])", re.IGNORECASE)


def _mask(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def names_re(names: Iterable[str]) -> re.Pattern[str] | None:
    """One pattern for every multi-word person name (longest first)."""
    names = {n for n in names if n and len(n.split()) >= 2}
    if not names:
        return None
    alts = sorted((re.escape(n) for n in names), key=len, reverse=True)
    return re.compile(rf"(?<![\w-])(?:{'|'.join(alts)})(?![\w-])")


def mentioned(
    sentence: str, entities: Iterable[Entity], full_names: re.Pattern[str] | None = None
) -> list[Entity]:
    """Entities named in the sentence. A player's full name matches anywhere; his short
    alias (a unique last name) and team names only match outside every known full name,
    so "Chase Brown" doesn't count as a mention of Ja'Marr Chase ("Chase")."""
    masked = _mask(sentence, full_names) if full_names is not None else sentence
    out = []
    for e in entities:
        full = e.aliases[:1] if e.kind == "player" else []
        short = e.aliases[1:] if e.kind == "player" else e.aliases
        if any(_alias_re(a).search(sentence) for a in full) or any(
            _alias_re(a).search(masked) for a in short
        ):
            out.append(e)
    return out


# ---- lexicon for unknown entities -------------------------------------------------------------


@dataclass
class Lexicon:
    """Names the prose could mention: all 32 teams + rostered players (nflverse rosters)."""

    player_names: set[str] = field(default_factory=set)

    def player_pattern(self) -> re.Pattern[str] | None:
        return names_re(self.player_names)


# ---- the checks -------------------------------------------------------------------------------


def check_provenance(sections: dict[str, str], fx: FactIndex) -> CheckResult:
    allowed = fx.all_atoms
    issues = [
        Issue(sec, atom, "not in any payload display string", s)
        for sec, text in sections.items()
        for s in sentences(text)
        for atom in number_atoms(s)
        if atom not in allowed
    ]
    return CheckResult("number_provenance", FAIL, not issues, issues)


def check_spelled_numbers(sections: dict[str, str], fx: FactIndex) -> CheckResult:
    payload_words = " ".join(d for e in fx.entities.values() for d in e.displays).lower()
    words_re = _phrase_re(NUMBER_WORDS)
    allow_re = _phrase_re(NUMBER_WORD_ALLOW)
    issues: list[Issue] = []
    for sec, text in sections.items():
        for s in sentences(text):
            for m in words_re.finditer(_mask(s, allow_re)):
                w = m.group(0).lower()
                if not re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", payload_words):
                    issues.append(Issue(sec, m.group(0), "number word not in the payload", s))
    return CheckResult("spelled_out_numbers", FAIL, not issues, issues)


def check_binding(
    sections: dict[str, str], fx: FactIndex, full_names: re.Pattern[str] | None = None
) -> CheckResult:
    owners = [e for e in fx.entities.values() if e.kind != "meta"]
    global_atoms = fx.global_atoms
    issues: list[Issue] = []
    for sec, text in sections.items():
        implicit = [fx.entities[k] for k in SECTION_OWNERS.get(sec, ()) if k in fx.entities]
        for s in sentences(text):
            atoms = [a for a in number_atoms(s) if a not in global_atoms]
            if not atoms:
                continue
            named = mentioned(s, owners, full_names)
            # a person from a team's evidence or QB slot names that team too ("without
            # guard Aaron Banks; pass rush's pressure rate +8.8" is the Packers' context)
            named += [
                fx.entities[f"team:{c}"]
                for e in list(named)
                for c in e.teams
                if f"team:{c}" in fx.entities
            ]
            if not any(e.kind == "bucket" for e in named):  # a bucket sentence: bucket numbers
                named += implicit
            for a in atoms:
                if not any(a in e.atoms for e in named):
                    who = ", ".join(e.aliases[0] for e in named) or "no entity named"
                    issues.append(Issue(sec, a, f"not owned by: {who}", s))
    return CheckResult("entity_binding", FAIL, not issues, issues)


def check_unknown_entities(
    sections: dict[str, str], fx: FactIndex, lexicon: Lexicon | None
) -> CheckResult:
    known = fx.entity_names()
    known_team_codes = {k.split(":", 1)[1] for k in fx.entities if k.startswith("team:")}
    pp = lexicon.player_pattern() if lexicon else None
    issues: list[Issue] = []
    for sec, text in sections.items():
        for s in sentences(text):
            rest = s
            if pp is not None:
                for m in pp.finditer(s):
                    if m.group(0).lower() not in known:
                        issues.append(Issue(sec, m.group(0), "player not in the payload", s))
                rest = _mask(s, pp)
            for code in TEAMS:
                if code in known_team_codes:
                    continue
                for a in team_aliases(code):
                    if _alias_re(a).search(rest):
                        issues.append(Issue(sec, a, "team not in the payload", s))
                        break
    return CheckResult("unknown_entities", FAIL, not issues, issues)


def check_name_heuristic(sections: dict[str, str], fx: FactIndex) -> CheckResult:
    """Two or more capitalized words in a row that match no known alias (warn only)."""
    known = fx.entity_names()
    cap_seq = re.compile(r"\b[A-Z][a-z'.]+(?:\s+[A-Z][a-z'.]+)+")
    issues: list[Issue] = []
    for sec, text in sections.items():
        for s in sentences(text):
            for m in cap_seq.finditer(s):
                # possessives ("Chase's") must not hide a known name
                words = [re.sub(r"'s$", "", w).strip(".'") for w in m.group(0).split()]
                words = [w for w in words if w not in COMMON_CAPS]
                if len(words) < 2:
                    continue
                phrase = " ".join(words)
                low = phrase.lower()
                if low in known or any(low in k or k in low for k in known if " " in k):
                    continue
                issues.append(Issue(sec, phrase, "capitalized name matches nothing known", s))
    return CheckResult("name_heuristic", WARN, not issues, issues)


def team_spans(
    sentence: str, fx: FactIndex, full_names: re.Pattern[str] | None = None
) -> list[tuple[int, int, str]]:
    """(start, end, team code) for every payload team named in the sentence, in order;
    names inside a known full player name don't count."""
    masked = _mask(sentence, full_names) if full_names is not None else sentence
    found: list[tuple[int, int, str]] = []
    for key, e in fx.entities.items():
        if e.kind != "team":
            continue
        code = key.split(":", 1)[1]
        for alias in sorted(e.aliases, key=len, reverse=True):
            for m in _alias_re(alias).finditer(masked):
                if not any(s < m.end() and m.start() < t for s, t, _ in found):
                    found.append((m.start(), m.end(), code))
    return sorted(found)


_AT = re.compile(r"\s+(?:at|@)\s+(?:the\s+)?", re.IGNORECASE)
CLOSE_WORDS = re.compile(r"\b(?:closest|tightest|nearest to even)\b", re.IGNORECASE)
TOSSUP_WORDS = re.compile(r"\b(?:toss-?ups?|coin[- ]?flips?|pick-?'?em)\b", re.IGNORECASE)
LOPSIDED_WORDS = re.compile(
    r"\b(?:lopsided|strongest call|biggest favou?rite|most confident|safest|right behind|"
    r"runner-?up|next-most|second-most|2nd-most)\b",
    re.IGNORECASE,
)
DISAGREE_WORDS = re.compile(
    r"\b(?:higher on|lower on|above consensus|below consensus|disagree\w*|splits?|"
    r"more bullish|less bullish|differs?|leans? away)\b",
    re.IGNORECASE,
)
LARGE_WORDS = re.compile(
    r"\b(?:much|well|far|way|considerably|significantly|sharply)\s+higher\b", re.I
)
NOTABLE_WORDS = re.compile(
    r"\b(?:notably|somewhat|moderately|slightly|a bit|a little)\s+higher\b", re.I
)


def check_meaning(
    sections: dict[str, str], fx: FactIndex, full_names: re.Pattern[str] | None = None
) -> CheckResult:
    """Meaning checks the number checks can't do (D53; from GLM's P04 backtests):
    - "A at B" must be a real game with A away and B home (a road/home swap was published);
    - in the game outlook, "closest" / "most lopsided" / "right behind" must name a team in
      the code-made `game_highlights`; "toss-up" also accepts a game in the toss-up band;
    - "much / notably higher ... consensus" must name a team with a consensus gap of that
      tier."""
    issues: list[Issue] = []
    for sec, text in sections.items():
        for s in sentences(text):
            spans = team_spans(s, fx, full_names)
            for (_, e1, a), (s2, _, b) in zip(spans, spans[1:], strict=False):
                known = fx.matchups | fx.past_matchups
                if _AT.fullmatch(s[e1:s2]) and a != b and (a, b) not in known:
                    if (b, a) in known:  # any section: the reverse is a real game
                        issues.append(Issue(sec, f"{a} at {b}", "home/road swapped", s))
                    elif sec == "game_outlook":  # elsewhere it may be an older game
                        issues.append(Issue(sec, f"{a} at {b}", "no such game this week", s))
            teams = {c for _, _, c in spans}
            if sec == "game_outlook" and teams:
                if CLOSE_WORDS.search(s) and not teams & fx.closest_teams:
                    issues.append(
                        Issue(sec, CLOSE_WORDS.search(s).group(0), "not a closest game", s)
                    )
                m = TOSSUP_WORDS.search(s)
                bands = {fx.game_bands.get(t) for t in teams}
                if m and not (teams & fx.closest_teams or "toss-up" in bands):
                    issues.append(Issue(sec, m.group(0), "not a toss-up or closest game", s))
                m = LOPSIDED_WORDS.search(s)
                if m and not teams & fx.lopsided_teams:
                    issues.append(Issue(sec, m.group(0), "not among the most lopsided games", s))
            if "consensus" in s.lower() and teams and DISAGREE_WORDS.search(s):
                tiers = {fx.consensus.get(t) for t in teams} - {None}
                if not tiers:
                    issues.append(Issue(sec, "consensus", "no consensus gap for these teams", s))
                elif LARGE_WORDS.search(s) and "large" not in tiers:
                    issues.append(
                        Issue(sec, LARGE_WORDS.search(s).group(0), "gap is only notable", s)
                    )
                elif NOTABLE_WORDS.search(s) and "notable" not in tiers:
                    issues.append(Issue(sec, NOTABLE_WORDS.search(s).group(0), "gap is large", s))
    return CheckResult("meaning", FAIL, not issues, issues)


_BANNED_RE = re.compile(
    rf"(?<![A-Za-z0-9])(?:{'|'.join(BANNED_PATTERNS)})(?![A-Za-z0-9])", re.IGNORECASE
)
_BANNED_ALLOW_RE = _phrase_re(BANNED_ALLOW)


def banned_terms(text: str) -> list[str]:
    """Banned betting / fantasy words in a text (allowed football phrases masked first).
    Also used to keep code-made phrases (model drivers) out of trouble."""
    return [m.group(0) for m in _BANNED_RE.finditer(_mask(text, _BANNED_ALLOW_RE))]


def check_banned(sections: dict[str, str]) -> CheckResult:
    """Banned words match on letter boundaries, so hyphenated compounds ("must-bet",
    "betting-lock") are caught; allowed football phrases are masked first."""
    issues = [
        Issue(sec, term, "banned betting / fantasy language", s)
        for sec, text in sections.items()
        for s in sentences(text)
        for term in banned_terms(s)
    ]
    return CheckResult("banned_language", FAIL, not issues, issues)


def check_complete(sections: dict[str, str]) -> CheckResult:
    """Every requested section has prose (a partial reply must not pass as "checks
    passed" with empty sections; Sol review)."""
    issues = [
        Issue(sec, "empty", "section has no prose") for sec, t in sections.items() if not t.strip()
    ]
    return CheckResult("complete", FAIL, not issues, issues)


def check_length(
    sections: dict[str, str], budgets: dict[str, int]
) -> tuple[CheckResult, CheckResult, dict[str, int]]:
    counts = {sec: word_count(text) for sec, text in sections.items()}
    over: list[Issue] = []
    under: list[Issue] = []
    for sec, budget in budgets.items():
        n = counts.get(sec, 0)
        if n > budget * (1 + LENGTH_TOLERANCE):
            over.append(Issue(sec, f"{n} words", f"over the {budget}-word budget by >25%"))
        elif n < budget * (1 - LENGTH_TOLERANCE):
            under.append(Issue(sec, f"{n} words", f"under the {budget}-word budget by >25%"))
    total, total_budget = sum(counts.get(s, 0) for s in budgets), sum(budgets.values())
    if total > total_budget * (1 + TOTAL_TOLERANCE):
        over.append(Issue("all", f"{total} words", f"over the {total_budget}-word total"))
    return (
        CheckResult("length", FAIL, not over, over),
        CheckResult("length_short", WARN, not under, under),
        counts,
    )


def check_hedging(
    sections: dict[str, str], fx: FactIndex, full_names: re.Pattern[str] | None = None
) -> CheckResult:
    """A low-confidence item needs a hedge in a sentence that names it, or a section-wide
    disclaimer: a hedged sentence in the same section that names no player or team (D53)."""
    hedge = _phrase_re(HEDGES)
    everyone = [e for e in fx.entities.values() if e.kind in ("player", "team")]
    disclaimed = {
        sec
        for sec, text in sections.items()
        for s in sentences(text)
        if hedge.search(s) and not mentioned(s, everyone, full_names)
    }
    issues: list[Issue] = []
    for key in dict.fromkeys(fx.low_confidence):
        e = fx.entities[key]
        hits = [
            (sec, s)
            for sec, text in sections.items()
            for s in sentences(text)
            if mentioned(s, [e], full_names)
        ]
        if hits and all(sec in disclaimed for sec, _ in hits):
            continue
        if hits and not any(hedge.search(s) for _, s in hits):
            sec, s = hits[0]
            issues.append(Issue(sec, e.aliases[0], "low-confidence item without a hedge", s))
    # a graph section holding a low-confidence item needs a hedge somewhere, even when the
    # item has no person to name (common opponents, a trend mismatch)
    for sec in sorted(fx.hedge_sections & set(sections)):
        if sections[sec].strip() and not hedge.search(plain(sections[sec])):
            issues.append(Issue(sec, sec, "section with a low-confidence item has no hedge"))
    return CheckResult("hedging", WARN, not issues, issues)


def run_checks(
    sections: dict[str, str],
    fx: FactIndex,
    *,
    budgets: dict[str, int] | None = None,
    lexicon: Lexicon | None = None,
) -> CheckReport:
    budgets = {k: v for k, v in (budgets or {}).items() if k in sections}
    length, short, counts = check_length(sections, budgets)
    players = [e.aliases[0] for e in fx.entities.values() if e.kind == "player"]
    full_names = names_re([*players, *(lexicon.player_names if lexicon else ())])
    results = [
        check_complete(sections),
        check_provenance(sections, fx),
        check_spelled_numbers(sections, fx),
        check_binding(sections, fx, full_names),
        check_unknown_entities(sections, fx, lexicon),
        check_meaning(sections, fx, full_names),
        check_banned(sections),
        length,
        short,
        check_hedging(sections, fx, full_names),
        check_name_heuristic(sections, fx),
    ]
    return CheckReport(results, counts)
