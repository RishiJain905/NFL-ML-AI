"""Names in a question: players, coaches and teams, resolved against the curated data (AE00).

Resolution runs in Python, so the weekly graph needs no full-text index (AE00 pitfall: no new
indexes or nodes for Ask). Sources:
- **teams:** the 32 codes and the digest's static names (`digest.names.TEAMS`: city +
  nickname), curated `team_aliases` (old codes: OAK, SD, STL ...) and a few nicknames
  (Niners, Bucs, Redskins, Football Team ...). Codes count only in capitals in a question
  ("NO" is New Orleans, "no" is a word); a parameter value may use any case.
- **players:** curated `players` (display, football and legal names; position) with each
  player's games, first / last season and latest team from `player_games`.
- **coaches:** head coaches from curated `games` (+ `config/head_coach_fixes.csv`) and the
  coordinators of `config/coaching_seed.csv`.

**Ambiguous names return choices, not a guess.** Several players with the same name: those
with at least `MIN_GAMES` games count; if exactly one of them played last season or this
one, he's the answer (Josh Allen, the Bills QB, not the 2014-2016 center); otherwise the
result is `ambiguous` with up to five choices. A lone last name ("Allen") resolves only when
one prominent player (or coach) has it.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

MIN_GAMES = 8  # a same-name player with fewer games doesn't make a name ambiguous
LAST_NAME_MIN_GAMES = 16  # a lone last name needs a prominent player
FUZZY_CUTOFF = 88.0
MAX_CHOICES = 5

EXTRA_TEAM_NAMES: dict[str, str] = {
    "niners": "SF", "bucs": "TB", "pats": "NE", "jags": "JAX", "philly": "PHI",
    "redskins": "WAS", "football team": "WAS", "washington football team": "WAS",
    "commies": "WAS", "oakland": "LV", "oakland raiders": "LV", "san diego": "LAC",
    "san diego chargers": "LAC", "st louis": "LA", "st louis rams": "LA", "la rams": "LA",
    "la chargers": "LAC", "los angeles rams": "LA", "los angeles chargers": "LAC",
    "ny giants": "NYG", "ny jets": "NYJ", "new york giants": "NYG", "new york jets": "NYJ",
    "g-men": "NYG", "bolts": "LAC", "cards": "ARI", "vegas": "LV",
}  # fmt: skip
SHARED = {"new york", "los angeles", "ny", "la"}  # a location alone that names two teams

_STOP_TEXT = """
    a an and are as at be best by can compare did do does down drive drives each for from
    game games give had has have he his how in is it its last list longest many more most
    much nfl afc nfc of on or over per player players playoff playoffs qb qbs rb rbs wr wrs
    te tes ol dl lb db cb ss fs k p season seasons show since super bowl team teams than the
    their them these they this those through to top under use vs versus was week weeks were
    what when where which who whom whose why will with year years yards yard touchdown
    touchdowns td tds epa coach coaches head offense defense offensive defensive sunday
    monday thursday saturday tell find rank against between first second third fourth
    who's what's ever all any home away record win wins loss losses"""
STOPWORDS = frozenset(_STOP_TEXT.split())

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.'’\-]*")
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm(text: str) -> str:
    """Lower case, accents dropped, periods / apostrophes removed, hyphens as spaces, name
    suffixes dropped: "A.J. Brown" -> "aj brown", "Michael Penix Jr." -> "michael penix"."""
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    s = s.lower().replace("’", "'")
    s = re.sub(r"'s\b", "", s)
    s = re.sub(r"[.']", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    parts = s.split()
    while len(parts) > 1 and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


@dataclass(frozen=True)
class Match:
    value: str  # team code, GSIS id, or the coach's name as the data spells it
    label: str  # "Josh Allen (QB, BUF, 2018-2026)"
    score: float = 100.0


@dataclass(frozen=True)
class Resolution:
    kind: Literal["team", "player", "coach"]
    status: Literal["unique", "ambiguous", "none"]
    matches: tuple[Match, ...] = ()

    @property
    def value(self) -> str | None:
        return self.matches[0].value if self.status == "unique" else None

    @property
    def label(self) -> str | None:
        return self.matches[0].label if self.status == "unique" else None


NONE_PLAYER = Resolution("player", "none")
NONE_COACH = Resolution("coach", "none")


@dataclass(frozen=True)
class Mention:
    text: str
    start: int
    end: int
    resolution: Resolution

    @property
    def kind(self) -> str:
        return self.resolution.kind

    def prompt_line(self) -> str:
        r = self.resolution
        if r.status == "unique":
            return f'"{self.text}" -> {r.kind} {r.value} ({r.label})'
        if r.status == "ambiguous":
            return f'"{self.text}" -> ambiguous {r.kind}: ' + "; ".join(m.label for m in r.matches)
        return f'"{self.text}" -> no {r.kind} found'


@dataclass
class _Player:
    gsis_id: str
    name: str
    position: str
    team: str
    first: int | None
    last: int | None
    games: int

    @property
    def label(self) -> str:
        span = (
            f"{self.first}-{self.last}" if self.first and self.last else str(self.last or "")
        ).strip()
        bits = ", ".join(b for b in (self.position, self.team, span) if b)
        return f"{self.name} ({bits})" if bits else self.name


@dataclass
class _Coach:
    name: str
    teams: list[str] = field(default_factory=list)
    first: int | None = None
    last: int | None = None
    roles: set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        role = "head coach" if "HC" in self.roles else "/".join(sorted(self.roles)) or "coach"
        teams = "/".join(dict.fromkeys(self.teams[-3:]))
        span = f"{self.first}-{self.last}" if self.first and self.last else ""
        bits = ", ".join(b for b in (role, teams, span) if b)
        return f"{self.name} ({bits})"


class NameIndex:
    """Resolves names; build once per process with `NameIndex.load()` (cached)."""

    def __init__(
        self,
        players: list[_Player],
        coaches: list[_Coach],
        team_names: dict[str, str],
        codes: dict[str, str],
        team_labels: dict[str, str],
        current_season: int,
    ):
        self.current_season = current_season
        self.team_names = team_names  # normalised name -> code
        self.codes = codes  # upper-case code / old code -> code
        self.team_labels = team_labels
        self.players = players
        self.coaches = coaches
        self._by_name: dict[str, list[_Player]] = {}
        self._by_last: dict[str, list[_Player]] = {}
        self._by_id = {p.gsis_id: p for p in players}
        for p in players:
            for key in {norm(p.name)}:
                self._by_name.setdefault(key, []).append(p)
            last = norm(p.name).split()[-1] if norm(p.name) else ""
            if last:
                self._by_last.setdefault(last, []).append(p)
            surname = norm(p.name.split()[-1]) if p.name.split() else ""
            if " " in surname:  # "Hines-Allen", "St. Brown": the whole surname too
                self._by_last.setdefault(surname, []).append(p)
        self._coach_by_name = {norm(c.name): c for c in coaches}
        self._coach_by_last: dict[str, list[_Coach]] = {}
        for c in coaches:
            parts = norm(c.name).split()
            if parts:
                self._coach_by_last.setdefault(parts[-1], []).append(c)
        self._player_keys = list(self._by_name)
        self._coach_keys = list(self._coach_by_name)
        self._team_keys = list(team_names)

    # ---- loading ----------------------------------------------------------------------------

    _cache: dict[tuple, NameIndex] = {}
    _lock = threading.Lock()

    @classmethod
    def load(cls, curated: Path | None = None, current_season: int | None = None) -> NameIndex:
        import polars as pl

        from nflengine.digest.names import TEAMS
        from nflengine.settings import CONFIG_DIR, get_config

        if curated is None:
            from nflengine.paths import ensure_data_root

            curated = ensure_data_root(create_dirs=False).curated
        current_season = current_season or get_config().seasons.current
        files = [curated / f for f in ("players.parquet", "player_games.parquet", "games.parquet")]
        seed = CONFIG_DIR / "coaching_seed.csv"
        fixes = CONFIG_DIR / "head_coach_fixes.csv"
        sig = tuple((str(f), f.stat().st_mtime_ns) for f in [*files, seed, fixes] if f.exists())
        key = (sig, current_season)
        with cls._lock:
            if key in cls._cache:
                return cls._cache[key]

        # never memory-mapped (D100): the control room holds this index for days and the weekly
        # curate replaces these files in place, which Windows refuses while a file is mapped
        players_df = pl.read_parquet(
            files[0],
            columns=["gsis_id", "display_name", "position", "latest_team"],
            memory_map=False,
        )
        pg = (
            pl.read_parquet(files[1], columns=["player_id", "season", "team"], memory_map=False)
            .filter(pl.col("player_id").is_not_null())
            .group_by("player_id")
            .agg(
                pl.len().alias("games"),
                pl.col("season").min().alias("first"),
                pl.col("season").max().alias("last"),
                pl.col("team").sort_by("season").last().alias("team"),
            )
        )
        joined = players_df.join(pg, left_on="gsis_id", right_on="player_id", how="left")
        players = [
            _Player(
                gsis_id=r["gsis_id"],
                name=r["display_name"],
                position=r["position"] or "",
                team=r["team"] or r["latest_team"] or "",
                first=r["first"],
                last=r["last"],
                games=int(r["games"] or 0),
            )
            for r in joined.iter_rows(named=True)
            if r["gsis_id"] and r["display_name"]
        ]

        coaches: dict[str, _Coach] = {}

        def add_coach(name: str, team: str, season: int, role: str) -> None:
            if not name or not str(name).strip():
                return
            c = coaches.setdefault(norm(name), _Coach(name=str(name).strip()))
            if team and (not c.teams or c.teams[-1] != team):
                c.teams.append(team)
            c.first = season if c.first is None else min(c.first, season)
            c.last = season if c.last is None else max(c.last, season)
            c.roles.add(role)

        cols = ["season", "home_team", "away_team", "home_coach", "away_coach"]
        games = pl.read_parquet(files[2], columns=cols, memory_map=False).sort("season")
        for r in games.iter_rows(named=True):
            add_coach(r["home_coach"], r["home_team"], r["season"], "HC")
            add_coach(r["away_coach"], r["away_team"], r["season"], "HC")
        for path, cols in ((fixes, ("coach", "team", "season", "HC")), (seed, None)):
            if not path.exists():
                continue
            df = pl.read_csv(path, comment_prefix="#", infer_schema_length=0)
            for r in df.iter_rows(named=True):
                role = "HC" if cols else (r.get("role") or "coach")
                try:
                    season = int(r["season"])
                except (TypeError, ValueError):
                    continue
                add_coach(r.get("coach") or "", r.get("team") or "", season, role)

        team_names: dict[str, str] = {}
        team_labels: dict[str, str] = {}
        for code, (loc, nick) in TEAMS.items():
            team_labels[code] = f"{loc} {nick}"
            team_names[norm(nick)] = code
            team_names[norm(f"{loc} {nick}")] = code
            if norm(loc) not in SHARED:
                team_names[norm(loc)] = code
        for name, code in EXTRA_TEAM_NAMES.items():
            team_names[norm(name)] = code
        codes = {c: c for c in TEAMS}
        alias_path = curated / "team_aliases.parquet"
        if alias_path.exists():
            for r in pl.read_parquet(alias_path, memory_map=False).iter_rows(named=True):
                if r["alias"] and r["team"] in TEAMS:
                    codes[str(r["alias"]).upper()] = r["team"]
        idx = cls(players, list(coaches.values()), team_names, codes, team_labels, current_season)
        with cls._lock:
            cls._cache = {key: idx}
        return idx

    # ---- teams -------------------------------------------------------------------------------

    def team(self, text: str) -> str | None:
        """A team code for a parameter value: a code (any case), a name, a nickname, a city,
        or a near miss ("Dalas")."""
        raw = str(text or "").strip()
        if not raw:
            return None
        if raw.upper() in self.codes:
            return self.codes[raw.upper()]
        key = norm(raw)
        if key.startswith("the "):
            key = key[4:]
        if key in self.team_names:
            return self.team_names[key]
        from rapidfuzz import fuzz, process

        hit = process.extractOne(key, self._team_keys, scorer=fuzz.ratio, score_cutoff=85)
        return self.team_names[hit[0]] if hit else None

    def team_label(self, code: str) -> str:
        return self.team_labels.get(code, code)

    # ---- players -----------------------------------------------------------------------------

    @staticmethod
    def _ranked(players: list[_Player]) -> list[_Player]:
        """Most recent first, then most games: the order choices are offered in."""
        return sorted(players, key=lambda p: (-(p.last or 0), -p.games))

    def _decide(self, cands: list[_Player], min_games: int, score: float = 100.0) -> Resolution:
        if not cands:
            return NONE_PLAYER
        uniq = list({p.gsis_id: p for p in cands}.values())
        if len(uniq) == 1:
            return Resolution("player", "unique", (Match(uniq[0].gsis_id, uniq[0].label, score),))
        real = [p for p in uniq if p.games >= min_games]
        if len(real) == 1:
            return Resolution("player", "unique", (Match(real[0].gsis_id, real[0].label, score),))
        pool = real or uniq
        recent = [p for p in pool if (p.last or 0) >= self.current_season - 1]
        if len(recent) == 1 and real:
            p = recent[0]
            return Resolution("player", "unique", (Match(p.gsis_id, p.label, score),))
        pool = self._ranked(pool)[:MAX_CHOICES]
        return Resolution(
            "player", "ambiguous", tuple(Match(p.gsis_id, p.label, score) for p in pool)
        )

    def player(self, text: str) -> Resolution:
        """A player for a parameter value: a GSIS id, a full name, a near miss, or a last name
        that only one prominent player has."""
        raw = str(text or "").strip()
        if not raw:
            return NONE_PLAYER
        if raw in self._by_id:
            p = self._by_id[raw]
            return Resolution("player", "unique", (Match(p.gsis_id, p.label),))
        key = norm(raw)
        if key in self._by_name:
            return self._decide(self._by_name[key], MIN_GAMES)
        if " " in key:
            from rapidfuzz import fuzz, process

            hits = process.extract(key, self._player_keys, scorer=fuzz.ratio, limit=6)
            hits = [h for h in hits if h[1] >= FUZZY_CUTOFF]
            if hits:
                best = hits[0][1]
                cands = [p for h in hits if h[1] >= best - 3 for p in self._by_name[h[0]]]
                return self._decide(cands, MIN_GAMES, round(best, 1))
            return NONE_PLAYER
        return self._decide(
            [p for p in self._by_last.get(key, []) if p.games >= LAST_NAME_MIN_GAMES],
            LAST_NAME_MIN_GAMES,
        )

    def person(self, text: str) -> Resolution:
        """A player or a coach (AE03's `person` parameters), by the rule a question's names
        follow: a coach beats a never-played namesake ("Andy Reid"); a prominent player (8+
        games) and a coach with the same name is "which one?"; else the player, else the coach.
        An ambiguous result can mix players (GSIS ids) and coaches (names)."""
        res_p, res_c = self.player(text), self.coach(text)
        played = [
            self._by_id[m.value]
            for m in res_p.matches
            if m.value in self._by_id and self._by_id[m.value].games >= MIN_GAMES
        ]
        if res_c.status == "unique" and res_p.status != "none" and played:
            matches = [Match(p.gsis_id, p.label) for p in self._ranked(played)]
            matches += list(res_c.matches)
            return Resolution("player", "ambiguous", tuple(matches[:MAX_CHOICES]))
        if res_c.status == "unique" and res_p.status != "none":
            return res_c
        if res_p.status != "none":
            return res_p
        return res_c

    def player_label(self, gsis_id: str) -> str | None:
        p = self._by_id.get(gsis_id)
        return p.label if p else None

    # ---- coaches -----------------------------------------------------------------------------

    def coach(self, text: str) -> Resolution:
        key = norm(text)
        if not key:
            return NONE_COACH
        c = self._coach_by_name.get(key)
        if c is None and " " in key:
            from rapidfuzz import fuzz, process

            hit = process.extractOne(
                key, self._coach_keys, scorer=fuzz.ratio, score_cutoff=FUZZY_CUTOFF
            )
            c = self._coach_by_name[hit[0]] if hit else None
        if c is not None:
            return Resolution("coach", "unique", (Match(c.name, c.label),))
        if " " not in key:
            found = self._coach_by_last.get(key, [])
            if len(found) == 1:
                return Resolution("coach", "unique", (Match(found[0].name, found[0].label),))
            if found:
                found = sorted(found, key=lambda c: -(c.last or 0))[:MAX_CHOICES]
                return Resolution(
                    "coach", "ambiguous", tuple(Match(c.name, c.label) for c in found)
                )
        return NONE_COACH

    # ---- mentions in a question --------------------------------------------------------------

    def mentions(self, question: str) -> list[Mention]:
        """Every team, player and coach the question names, longest phrases first."""
        toks = [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(question or "")]
        used = [False] * len(toks)
        out: list[Mention] = []

        def span(i: int, n: int) -> tuple[str, int, int]:
            s, e = toks[i][1], toks[i + n - 1][2]
            text = question[s:e]
            text = re.sub(r"(?:'s|’s|')$", "", text).rstrip(".")
            return text, s, s + len(text)

        def free(i: int, n: int) -> bool:
            return i + n <= len(toks) and not any(used[i : i + n])

        def take(i: int, n: int, res: Resolution) -> None:
            text, s, e = span(i, n)
            out.append(Mention(text, s, e, res))
            for k in range(i, i + n):
                used[k] = True

        # exact full names first ("Dallas Goedert" is a player, not Dallas + a last name)
        for n in (4, 3, 2):
            for i in range(len(toks)):
                if not free(i, n):
                    continue
                text, _, _ = span(i, n)
                key = norm(text)
                # any case: "mike williams" must still ask which one (Sol, AE00); a lower-case
                # window with a stop word ("the best") is never a name
                if not key or (not text[:1].isupper() and any(w in STOPWORDS for w in key.split())):
                    continue
                players = self._by_name.get(key, [])
                coach = self._coach_by_name.get(key)
                played = [p for p in players if p.games >= MIN_GAMES]
                if coach is not None and played:  # a prominent player and a coach: ask
                    matches = [Match(p.gsis_id, p.label) for p in self._ranked(played)]
                    matches.append(Match(coach.name, coach.label))
                    take(i, n, Resolution("player", "ambiguous", tuple(matches[:MAX_CHOICES])))
                elif coach is not None:  # "Andy Reid" the coach, not a never-played namesake
                    take(i, n, Resolution("coach", "unique", (Match(coach.name, coach.label),)))
                elif players:
                    take(i, n, self._decide(players, MIN_GAMES))

        # teams: names (any case), codes (capitals only)
        for n in (4, 3, 2, 1):
            for i in range(len(toks)):
                if not free(i, n):
                    continue
                text, _, _ = span(i, n)
                key = norm(text)
                if key.startswith("the "):
                    key = key[4:]
                code = self.team_names.get(key)
                if code is None and n == 1 and text.isupper() and text in self.codes:
                    code = self.codes[text]
                if code is None and n <= 2 and key in SHARED and text[:1].isupper():
                    choices = ("NYG", "NYJ") if key in ("new york", "ny") else ("LA", "LAC")
                    if key == "la" and text == "LA":
                        code = "LA"  # the Rams' code
                    else:
                        take(
                            i,
                            n,
                            Resolution(
                                "team",
                                "ambiguous",
                                tuple(Match(c, self.team_label(c)) for c in choices),
                            ),
                        )
                        continue
                if code is not None:
                    take(i, n, Resolution("team", "unique", (Match(code, self.team_label(code)),)))
                elif n == 1 and "-" in text:  # "Bills-Chiefs": two teams in one token
                    parts = [(m.group(0), m.start()) for m in re.finditer(r"[^-]+", text)]
                    found = [(p, s, self.team_names.get(norm(p))) for p, s in parts]
                    if len(found) >= 2 and all(c for _, _, c in found):
                        base = toks[i][1]
                        for p, s, c in found:
                            res = Resolution("team", "unique", (Match(c, self.team_label(c)),))
                            out.append(Mention(p, base + s, base + s + len(p), res))
                        used[i] = True

        # near misses on capitalised words ("Patrik Mahomes"), players then coaches
        for n in (3, 2):
            for i in range(len(toks)):
                if not free(i, n):
                    continue
                text, _, _ = span(i, n)
                words = text.split()
                key = norm(text)
                if not key or any(norm(w) in STOPWORDS for w in words):
                    continue
                if all(w[:1].isupper() for w in words):
                    res_p, res_c = self.player(text), self.coach(text)
                    played = [
                        self._by_id[m.value]
                        for m in res_p.matches
                        if m.value in self._by_id and self._by_id[m.value].games >= MIN_GAMES
                    ]
                    if res_c.status == "unique" and res_p.status != "none" and played:
                        matches = [Match(p.gsis_id, p.label) for p in self._ranked(played)]
                        matches += list(res_c.matches)  # a prominent player and a coach: ask
                        take(i, n, Resolution("player", "ambiguous", tuple(matches[:MAX_CHOICES])))
                    elif res_c.status == "unique" and res_p.status != "none":
                        take(i, n, res_c)  # "Andy Ried": the coach, not a never-played namesake
                    elif res_p.status != "none":
                        take(i, n, res_p)
                    elif res_c.status != "none":
                        take(i, n, res_c)

        # a lone capitalised last name
        for i, (tok, _, _) in enumerate(toks):
            if used[i] or not tok[:1].isupper():
                continue
            text, _, _ = span(i, 1)
            key = norm(text)
            if not key or key in STOPWORDS or key.isdigit() or len(key) < 3:
                continue
            players = [p for p in self._by_last.get(key, []) if p.games >= LAST_NAME_MIN_GAMES]
            coaches = self._coach_by_last.get(key, [])
            if players and coaches:
                matches = [Match(p.gsis_id, p.label) for p in self._ranked(players)[:3]]
                matches += [Match(c.name, c.label) for c in coaches[:2]]
                take(i, 1, Resolution("player", "ambiguous", tuple(matches[:MAX_CHOICES])))
            elif players:
                take(i, 1, self._decide(players, LAST_NAME_MIN_GAMES))
            elif coaches:
                take(i, 1, self.coach(text))
        return sorted(out, key=lambda m: m.start)


def ambiguous(mentions: list[Mention]) -> list[Mention]:
    return [m for m in mentions if m.resolution.status == "ambiguous"]
