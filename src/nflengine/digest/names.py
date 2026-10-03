"""Team display names and aliases for the digest (documentation/06; P04 pitfall: use team
names consistently, and give the fact index an alias list per team).

Static on purpose: the 32 names change rarely, and the checks must work in tests without
the data drive. Prose uses the nickname ("the Chiefs"); the alias list adds the code, the
full name and the location (left out for the two shared markets, New York and Los Angeles).
"""

from __future__ import annotations

TEAMS: dict[str, tuple[str, str]] = {
    "ARI": ("Arizona", "Cardinals"),
    "ATL": ("Atlanta", "Falcons"),
    "BAL": ("Baltimore", "Ravens"),
    "BUF": ("Buffalo", "Bills"),
    "CAR": ("Carolina", "Panthers"),
    "CHI": ("Chicago", "Bears"),
    "CIN": ("Cincinnati", "Bengals"),
    "CLE": ("Cleveland", "Browns"),
    "DAL": ("Dallas", "Cowboys"),
    "DEN": ("Denver", "Broncos"),
    "DET": ("Detroit", "Lions"),
    "GB": ("Green Bay", "Packers"),
    "HOU": ("Houston", "Texans"),
    "IND": ("Indianapolis", "Colts"),
    "JAX": ("Jacksonville", "Jaguars"),
    "KC": ("Kansas City", "Chiefs"),
    "LA": ("Los Angeles", "Rams"),
    "LAC": ("Los Angeles", "Chargers"),
    "LV": ("Las Vegas", "Raiders"),
    "MIA": ("Miami", "Dolphins"),
    "MIN": ("Minnesota", "Vikings"),
    "NE": ("New England", "Patriots"),
    "NO": ("New Orleans", "Saints"),
    "NYG": ("New York", "Giants"),
    "NYJ": ("New York", "Jets"),
    "PHI": ("Philadelphia", "Eagles"),
    "PIT": ("Pittsburgh", "Steelers"),
    "SEA": ("Seattle", "Seahawks"),
    "SF": ("San Francisco", "49ers"),
    "TB": ("Tampa Bay", "Buccaneers"),
    "TEN": ("Tennessee", "Titans"),
    "WAS": ("Washington", "Commanders"),
}

SHARED_MARKETS = {"New York", "Los Angeles"}
EXTRA_ALIASES: dict[str, tuple[str, ...]] = {
    "SF": ("Niners",),
    "TB": ("Bucs",),
    "NE": ("Pats",),
    "LA": ("LA Rams",),
    "LAC": ("LA Chargers",),
}


def nickname(team: str) -> str:
    return TEAMS[team][1] if team in TEAMS else team


def full_name(team: str) -> str:
    if team not in TEAMS:
        return team
    loc, nick = TEAMS[team]
    return f"{loc} {nick}"


def team_aliases(team: str) -> list[str]:
    """Every way prose may name the team (code, nickname, full name, location, extras)."""
    if team not in TEAMS:
        return [team]
    loc, nick = TEAMS[team]
    out = [team, nick, f"{loc} {nick}", *EXTRA_ALIASES.get(team, ())]
    if loc not in SHARED_MARKETS:
        out.append(loc)
    return out


def last_name(name: str) -> str:
    """'Patrick Mahomes' -> 'Mahomes'; 'Michael Penix Jr.' -> 'Penix'."""
    parts = [p for p in name.replace(",", " ").split() if p]
    suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}
    while len(parts) > 1 and parts[-1].lower() in suffixes:
        parts.pop()
    return parts[-1] if parts else name
