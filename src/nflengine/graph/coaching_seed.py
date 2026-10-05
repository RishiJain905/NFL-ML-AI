"""Build `config/coaching_seed.csv` from Wikipedia (plan P08; decisions D83, D86; Q06).

The coaching seed feeds Q5b (coordinator vs his former boss, head coaches in one coaching
tree): one row per (coach, team, season, role) with role OC, DC or ST, and the season's head
coach for seasons before 2018 (from 2018 on the graph takes him from the schedules).

**Source:** English Wikipedia, read through its API (`action=parse`, wikitext) with a
descriptive user agent and about one request a second:
- seasons up to last season: the team-season article ("2021 Los Angeles Rams season"), its
  `{{NFL final staff}}` template (one line per job: "*Offensive coordinator – [[Name]]"), or
  for older articles without it the infobox fields `coach` / `off_coach` / `def_coach`;
- the current season: the team's staff template ("Template:Kansas City Chiefs staff"),
  because a season article only gets its final staff after the season.

**Scope:** coordinators only (offensive, defensive, special teams; co-coordinators count,
assistant coordinators don't). Position coaches (a quarterbacks coach ...) are left out on
purpose: every seed row also becomes `WORKED_UNDER` that season's head coach, and with every
assistant in the file almost any two head coaches would share a mentor within two steps.

Pages are cached under the data root (`cache/http/wikipedia_staff/`), so a rebuild re-reads
the cache; `refresh=True` re-fetches. Names are Wikipedia's display names; `check_names`
lists names that look like an nflverse head coach's but don't slug the same way (the graph
keys coaches by `graph.tables.slug(name)`), and `NAME_FIXES` maps the known ones.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import polars as pl

UA = "NFLAnalyticsEngine/1.0 (https://github.com/RishiJain905/NFL-ML-AI; personal research)"
API = "https://en.wikipedia.org/w/api.php"
REQUEST_GAP_S = 1.0
FIRST_SEASON = 2006
GRAPH_START = 2018  # from here on the head coach comes from the schedules (graph/tables.py)
ROLES = ("OC", "DC", "ST")

TEAM_NAMES: dict[str, str] = {
    "ARI": "Arizona Cardinals",
    "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers",
    "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals",
    "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos",
    "DET": "Detroit Lions",
    "GB": "Green Bay Packers",
    "HOU": "Houston Texans",
    "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs",
    "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings",
    "NE": "New England Patriots",
    "NO": "New Orleans Saints",
    "NYG": "New York Giants",
    "NYJ": "New York Jets",
    "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks",
    "SF": "San Francisco 49ers",
    "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans",
}
TEAMS = (*TEAM_NAMES, "LA", "LAC", "LV", "WAS")

# Wikipedia display name -> the name nflverse's schedules use for the same head coach (from
# `check_names`, checked by hand: "Jim Johnson", the Eagles' DC, is not "Jimmy Johnson")
NAME_FIXES: dict[str, str] = {"Richard Bisaccia": "Rich Bisaccia"}


def team_name(team: str, season: int) -> str:
    """The franchise's name that season (relocations and renames follow the franchise)."""
    if team == "LA":
        return "St. Louis Rams" if season <= 2015 else "Los Angeles Rams"
    if team == "LAC":
        return "San Diego Chargers" if season <= 2016 else "Los Angeles Chargers"
    if team == "LV":
        return "Oakland Raiders" if season <= 2019 else "Las Vegas Raiders"
    if team == "WAS":
        if season <= 2019:
            return "Washington Redskins"
        return "Washington Football Team" if season <= 2021 else "Washington Commanders"
    return TEAM_NAMES[team]


def page_title(team: str, season: int, current: int) -> str:
    if season >= current:
        return f"Template:{team_name(team, season)} staff"
    return f"{season} {team_name(team, season)} season"


# ---- parsing --------------------------------------------------------------------------------

_LINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]")
_REF = re.compile(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", re.S)
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_DASH = re.compile(r"\s+[–—-]\s+|\s*[–—]\s*")


def clean(text: str) -> str:
    """Wikitext -> plain text: links to their display text, no refs, templates or markup."""
    text = _REF.sub("", text)
    for _ in range(3):  # nested templates, inside out
        text = _TEMPLATE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text).replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", text).strip()


_NOTE_WORDS = re.compile(
    r"\b(fired|resigned|retired|released|promoted|hired|interim|until|from|week|after|"
    r"before|left|died|dismissed|demoted|reassigned|replaced)\b",
    re.I,
)


def names_in(value: str) -> list[str]:
    """The people named in a staff line's value: "[[A]] and [[B]]" -> [A, B]. Notes are
    dropped: in parentheses ("(interim)"), after a semicolon ("Ken Dorsey; fired after Week
    10"), or any part with a digit or a note word. Suffix periods stay ("Sr.")."""
    value = re.sub(r"\([^)]*\)", "", clean(value)).split(";")[0]
    value = re.sub(r"[†‡§¶#^*]+", "", value)  # footnote marks ("Gregg Williams†")
    out = []
    for part in re.split(r"\s*(?:,|&|/|\band\b)\s*", value):
        name = part.strip(" :")
        if len(name) <= 2 or re.search(r"\d", name) or _NOTE_WORDS.search(name):
            continue
        if len(name.split()) < 2:  # a lone word is never a full name
            continue
        out.append(name)
    return out


def roles_of(title: str) -> set[str]:
    """Roles in a job title ("Assistant head coach/special teams coordinator" -> {ST},
    "Interim head coach/defensive coordinator" -> {HC, DC})."""
    out: set[str] = set()
    for part in re.split(r"\s*/\s*|\s+and\s+", clean(title).lower()):
        part = part.strip()
        if part.startswith(("assistant", "associate")):
            continue
        if re.fullmatch(r"(interim )?head coach", part):
            out.add("HC")
        elif re.fullmatch(r"(co-)?offensive coordinator", part):
            out.add("OC")
        elif re.fullmatch(r"(co-)?defensive coordinator", part):
            out.add("DC")
        elif re.fullmatch(r"(co-)?special teams coordinator", part):
            out.add("ST")
    return out


def staff_jobs(wikitext: str) -> list[tuple[str, str]]:
    """(role, name) from a staff template's lines ("* <title> – <name>")."""
    jobs: list[tuple[str, str]] = []
    for raw in wikitext.splitlines():
        line = raw.strip()
        if not line.startswith("*"):
            continue
        parts = _DASH.split(line.lstrip("* "), maxsplit=1)
        if len(parts) != 2:
            continue
        for role in sorted(roles_of(parts[0])):
            for name in names_in(parts[1]):
                jobs.append((role, name))
    return jobs


def infobox_jobs(wikitext: str) -> list[tuple[str, str]]:
    """(role, name) from an infobox (`coach`, `off_coach`, `def_coach`): older season
    articles have no staff template."""
    fields = {"coach": "HC", "off_coach": "OC", "def_coach": "DC"}
    jobs: list[tuple[str, str]] = []
    for raw in wikitext.splitlines():
        m = re.match(r"\s*\|\s*(coach|off_coach|def_coach)\s*=\s*(.*)$", raw)
        if m:
            for name in names_in(m.group(2).replace("<br>", ",").replace("<br />", ",")):
                jobs.append((fields[m.group(1)], name))
    return jobs


def page_jobs(wikitext: str, staff_page: bool = False) -> tuple[list[tuple[str, str]], str]:
    """(jobs, source): a team staff template page is all staff; a season article's
    `{{NFL final staff}}` block when it has one; else its infobox. An article's other
    bullet lists (coaching changes, transactions) are never read as staff."""
    block = wikitext if staff_page else _staff_block(wikitext)
    if block:
        staff = staff_jobs(block)
        if any(r in ROLES for r, _ in staff):
            return staff, "staff"
    return infobox_jobs(wikitext), "infobox"


def _staff_block(wikitext: str) -> str:
    """The `{{NFL final staff ...}}` template of a season article ("" without one)."""
    i = wikitext.find("{{NFL final staff")
    if i < 0:
        return ""
    depth, j = 0, i
    while j < len(wikitext):
        if wikitext.startswith("{{", j):
            depth, j = depth + 1, j + 2
            continue
        if wikitext.startswith("}}", j):
            depth, j = depth - 1, j + 2
            if depth == 0:
                return wikitext[i:j]
            continue
        j += 1
    return wikitext[i:]


def fix(name: str) -> str:
    return NAME_FIXES.get(name, name)


def season_rows(
    team: str, season: int, wikitext: str, staff_page: bool = False
) -> tuple[list[dict], str]:
    """Seed rows of one team-season (coordinators; head coach filled before 2018)."""
    jobs, source = page_jobs(wikitext, staff_page)
    hcs = [fix(n) for r, n in jobs if r == "HC"]
    hc = hcs[0] if hcs else ""
    rows = []
    for role, name in jobs:
        if role not in ROLES:
            continue
        rows.append(
            {
                "coach": fix(name),
                "team": team,
                "season": season,
                "role": role,
                "head_coach": hc if season < GRAPH_START else "",
            }
        )
    return rows, source


# ---- fetching -------------------------------------------------------------------------------


def _cache_file(cache: Path, title: str) -> Path:
    return cache / (hashlib.sha256(title.encode()).hexdigest()[:16] + ".json")


def fetch_wikitext(
    title: str, cache: Path, *, refresh: bool = False, client=None, gap: float = REQUEST_GAP_S
) -> tuple[str, str | None]:
    """(wikitext, revision id) of a page, from the cache unless `refresh` (redirects
    followed). A missing page is ("", None)."""
    import httpx

    f = _cache_file(cache, title)
    if f.exists() and not refresh:
        data = json.loads(f.read_text(encoding="utf-8"))
    else:
        own = client is None
        client = client or httpx.Client(headers={"User-Agent": UA}, timeout=30)
        try:
            for attempt in range(4):
                try:
                    r = client.get(
                        API,
                        params={
                            "action": "parse",
                            "page": title,
                            "prop": "wikitext|revid",
                            "format": "json",
                            "redirects": 1,
                            "maxlag": 5,
                        },
                    )
                    if r.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(2**attempt)
            else:
                raise RuntimeError(f"wikipedia: {title!r} could not be fetched")
            data = {
                "title": title,
                "fetched": dt.date.today().isoformat(),
                "response": r.json(),
            }
            cache.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(data), encoding="utf-8")
            time.sleep(gap)
        finally:
            if own:
                client.close()
    parse = (data.get("response") or {}).get("parse") or {}
    return (parse.get("wikitext") or {}).get("*", ""), parse.get("revid")


def build_seed(
    seasons: Iterable[int],
    current: int,
    cache: Path,
    *,
    teams: Iterable[str] = TEAMS,
    refresh: bool = False,
    log: Callable[[str], None] = print,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Every team-season's coordinator rows, and counts of where they came from."""
    import httpx

    rows: list[dict] = []
    stats = {"staff": 0, "infobox": 0, "empty": 0, "missing": 0}
    with httpx.Client(headers={"User-Agent": UA}, timeout=30) as client:
        for team in teams:
            for season in seasons:
                title = page_title(team, season, current)
                text, _ = fetch_wikitext(
                    title, cache, refresh=refresh and season >= current, client=client
                )
                if not text:
                    stats["missing"] += 1
                    continue
                got, source = season_rows(team, season, text, staff_page=season >= current)
                stats[source if got else "empty"] += 1
                rows += got
            log(f"  {team}: {sum(r['team'] == team for r in rows)} rows")
    schema = {
        "coach": pl.String,
        "team": pl.String,
        "season": pl.Int32,
        "role": pl.String,
        "head_coach": pl.String,
    }
    df = pl.DataFrame(rows, schema=schema, orient="row") if rows else pl.DataFrame(schema=schema)
    return df.unique().sort("season", "team", "role", "coach"), stats


def check_names(seed: pl.DataFrame, nflverse_coaches: Iterable[str]) -> list[tuple[str, str]]:
    """Seed names that share a last name with an nflverse head coach but slug differently
    (a likely spelling difference that would split one coach into two graph nodes)."""
    from nflengine.graph.tables import slug

    def letters(text: str) -> str:
        return re.sub(r"[^a-z]", "", text.lower())

    known = {slug(n): n for n in nflverse_coaches if n}
    by_last: dict[str, list[str]] = {}
    for n in known.values():
        by_last.setdefault(letters(n.split()[-1]), []).append(n)
    out = []
    names = set(seed["coach"].to_list()) | {n for n in seed["head_coach"].to_list() if n}
    for n in sorted(names):
        if slug(n) in known:
            continue
        for k in by_last.get(letters(n.split()[-1]), []):
            if letters(k.split()[0])[:3] == letters(n.split()[0])[:3]:
                out.append((n, k))
    return out


def write_seed(seed: pl.DataFrame, path: Path, *, built: str, stats: dict[str, int]) -> Path:
    """The CSV with a header saying where the rows came from (comment lines start with #)."""
    head = [
        "# Coaching seed (documentation/05 -> Q5b; decisions D83, D86; open question Q06).",
        "# Built by `nfl graph coaching-seed` from English Wikipedia: each team-season article's",
        "# {{NFL final staff}} template (or its infobox when the article has none), and the teams'",
        "# current staff templates for the current season.",
        f"# Built {built}; pages: {stats.get('staff', 0)} staff templates, "
        f"{stats.get('infobox', 0)} infoboxes, {stats.get('empty', 0)} without coordinators, "
        f"{stats.get('missing', 0)} missing.",
        "# One row per coach, team (canonical code), season and role (OC / DC / ST coordinators;",
        "# co-coordinators included). head_coach is filled before 2018 (from 2018 on the graph",
        "# takes the head coach from the schedules). Edit by hand if a row is wrong.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(head) + "\n")
        # a blank head coach is an empty cell (Polars would quote an empty string as "")
        seed.with_columns(pl.col("head_coach").replace("", None)).write_csv(fh)
    tmp.replace(path)
    return path
