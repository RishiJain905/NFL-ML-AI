"""Explore -> Play calling (PC01): the teams grid, a team's page, its history, and the week's
Play calls tab, read from the tables `nfl playcalling build` writes (PC00, D122).

Files under `{NFL_DATA_ROOT}/playcalling/<S>/` (the guide `guides/play-calling.md` §4):
- `build.json`, written last by the build: a folder without it is "not built";
- `team_tendencies.parquet`: team x side x as-of week x window x metric x situation, with
  `n`, `games`, `value`, `league_value`, `diff`, `pct`;
- `team_game_tendencies.parquet`: the same per team-game (the week-by-week sparklines).

Read-only, one short read per file with no memory map (D100): `pyarrow.parquet.read_table(...,
filters=..., memory_map=False)`, so a Tuesday build can replace a file while the app is open.

The rules the pages follow (README §3, the PC01 phase file):
- a team page shows a season's newest as-of week (`build.json -> as_of_weeks`): what a run on
  the Tuesday before that week could see; the week tab shows the tables as of its own week;
- a rate on fewer than `MIN_N` plays is `small` (the pages grey it out);
- `history_only` rows (nflverse participation, research data) appear only in the History
  answer, never in the identity, the situations, the grid or the matchups (PC00's rule);
- a defense's rows are what offenses do against it, plus its own calls (blitz, rushers, box);
- PROE is compared with the league (`diff`), never with 0 (nflverse's xpass isn't re-centred).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow.parquet as pq

from nflengine.app.readers.common import clean, curated, num, read_json
from nflengine.curate.teams import CANONICAL_TEAMS, TEAM_MAP
from nflengine.paths import DataPaths
from nflengine.playcalling import labels as L

MIN_N = 20  # the phase file: grey out rates on fewer plays
WINDOWS = ("season", "last4", "last_season")
SIDES = ("offense", "defense")
HISTORY_FIRST = 2023  # participation's FTN-charted era: one vocabulary (the guide §2)
HISTORY_SEASONS = 3  # the newest three participation seasons (2023-2025 until 2026's arrives)
MAX_SHIFTS = 8
SHIFT_MIN_SD = 1.0  # a matchup shift: the defense at least one SD from the league
BUILD = "uv run nfl playcalling build --season {season}"

# ---- how each metric is named and read ------------------------------------------------------
# metric -> (short name, what one counted play is, a plain sentence for the tooltip)
_SHOW: dict[str, tuple[str, str, str | None]] = {
    "dropback_rate": ("Dropback rate", "play", "Passes, sacks and scrambles, per play."),
    "proe": (
        "PROE",
        "play",
        "Pass rate over expected: dropbacks minus nflverse's expected pass rate for the same "
        "down, distance, field "
        "position, score and clock, in points. The league sits below 0 (nflverse's model "
        "isn't re-centred each season), so compare with the league, not with 0.",
    ),
    "shotgun_rate": ("Shotgun or pistol", "snap", "Play-by-play's shotgun flag: pistol counts."),
    "no_huddle_rate": ("No-huddle", "snap", "Snaps without a huddle (play-by-play)."),
    "deep_shot_rate": (
        "Deep shots",
        "attempt",
        f"Passes thrown {L.DEEP_SHOT_AIR_YARDS}+ yards past the line of scrimmage, per attempt.",
    ),
    "adot": ("aDOT", "attempt", "Average depth of target: air yards per pass attempt."),
    "explosive_rate": (
        "Explosive plays",
        "play",
        f"Dropbacks gaining {L.EXPLOSIVE_PASS_YARDS}+ yards and designed runs gaining "
        f"{L.EXPLOSIVE_RUN_YARDS}+.",
    ),
    "epa_per_play": ("EPA per play", "play", "Expected points added per play (nflverse)."),
    "success_rate": ("Success rate", "play", "Plays that added expected points (EPA > 0)."),
    "motion_rate": ("Motion", "snap", "A player moves before the snap (FTN charting)."),
    "play_action_rate": (
        "Play-action",
        "dropback",
        "The quarterback fakes a handoff before he throws (FTN charting). Per dropback: sites "
        "that quote it per snap read lower (KC 2025: 15.3% of dropbacks = 10% of snaps).",
    ),
    "screen_rate": ("Screens", "dropback", "Screen passes per dropback (FTN charting)."),
    "rpo_rate": (
        "RPO",
        "snap",
        "Run-pass options: the quarterback reads a defender after the snap (FTN charting).",
    ),
    "qb_under_center_share": ("Under center", "snap", "The QB right behind the center (FTN)."),
    "qb_shotgun_share": ("Shotgun", "snap", "The QB 5-7 yards back (FTN; pistol separate)."),
    "qb_pistol_share": ("Pistol", "snap", "The QB about 4 yards back, a back behind him (FTN)."),
    "blitz_rate": (
        "Blitz",
        "dropback",
        "One or more blitzers: extra rushers from the linebackers or the secondary (FTN). "
        "Sites that count 5+ rushers read a few points lower (MIN 2025: 51% vs 46%).",
    ),
    "blitz_rate_pfr": (
        "Blitz (PFR)",
        "dropback",
        "PFR's blitz count, a cross-check: FTN and PFR chart blitzes differently (r 0.86 per "
        "team-season).",
    ),
    "rushers_avg": ("Pass rushers", "dropback", "Rushers per dropback (4 is standard)."),
    "box_avg": ("Box count", "snap", "Defenders near the line before the snap (FTN)."),
    "light_box_rate": (
        f"Light box ({L.LIGHT_BOX_MAX} or fewer)",
        "snap",
        "Few defenders near the line: good for the run.",
    ),
    "heavy_box_rate": (
        f"Heavy box ({L.HEAVY_BOX_MIN}+)",
        "snap",
        "A stacked box against the run.",
    ),
    "pressure_rate": ("Pressure", "dropback", "Dropbacks with the quarterback pressured."),
    "time_to_throw_avg": ("Time to throw", "dropback", "Seconds from the snap to the throw."),
    "man_rate": ("Man coverage", "dropback", "Man coverage, as a share of man + zone."),
}
_MEAN_NOUN = {
    "adot": "air yards",
    "rushers_avg": "rushers",
    "box_avg": "defenders",
    "epa_per_play": "EPA",
    "time_to_throw_avg": "seconds",
}
_DIGITS = {"rushers_avg": 2, "box_avg": 2, "epa_per_play": 2, "time_to_throw_avg": 2}
# the defense chooses these; every other rate on a defense row is what offenses did against it
_DEFENSE_CALLS = {
    "blitz_rate",
    "blitz_rate_pfr",
    "rushers_avg",
    "box_avg",
    "light_box_rate",
    "heavy_box_rate",
    "man_rate",
    "pressure_rate",
}
# the situation each headline metric is read in (labels.HEADLINE); everything else: all plays
_READ_IN = {"proe": "neutral", "dropback_rate": "neutral"}

_LANE_LABEL = {
    "left_end": "Left end",
    "left_tackle": "Left tackle",
    "left_guard": "Left guard",
    "middle": "Middle",
    "right_guard": "Right guard",
    "right_tackle": "Right tackle",
    "right_end": "Right end",
}
_SITUATION_LABEL = {
    "all": "All plays",
    "neutral": "Neutral situations",
    "1st_down": "1st down",
    "2nd_short": f"2nd & 1-{L.SHORT_MAX}",
    "2nd_medium": f"2nd & {L.SHORT_MAX + 1}-{L.MEDIUM_MAX}",
    "2nd_long": f"2nd & {L.MEDIUM_MAX + 1}+",
    "3rd_short": f"3rd & 1-{L.SHORT_MAX}",
    "3rd_medium": f"3rd & {L.SHORT_MAX + 1}-{L.MEDIUM_MAX}",
    "3rd_long": f"3rd & {L.MEDIUM_MAX + 1}+",
    "4th_down": "4th down",
    "own_deep": "Own 1-20",
    "own_half": "Own 21-50",
    "opp_half": "Opp. 49-21",
    "red_zone": "Red zone (20-1)",
    "trail_9plus": "Down 9+",
    "trail_1to8": "Down 1-8",
    "tied": "Tied",
    "lead_1to8": "Up 1-8",
    "lead_9plus": "Up 9+",
    "two_minute": "Last 2 min of a half",
}
_FAMILIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "down_distance",
        "Down & distance",
        (
            "1st_down",
            "2nd_short",
            "2nd_medium",
            "2nd_long",
            "3rd_short",
            "3rd_medium",
            "3rd_long",
            "4th_down",
        ),
    ),
    ("field_zone", "Field zone", ("own_deep", "own_half", "opp_half", "red_zone")),
    (
        "score",
        "Score & clock",
        ("trail_9plus", "trail_1to8", "tied", "lead_1to8", "lead_9plus", "two_minute"),
    ),
)
SITUATION_METRICS = tuple(m.name for m in L.METRICS if m.situational)
_ALL_BUCKETS = tuple(s for _, _, buckets in _FAMILIES for s in buckets)

# identity groups per side: (id, title, note, metrics)
_FTN_SNAP = ("qb_under_center_share", "qb_shotgun_share", "qb_pistol_share", "motion_rate")
_IDENTITY: dict[str, tuple[tuple[str, str, str | None, tuple[str, ...]], ...]] = {
    "offense": (
        ("pass_run", "Run or pass", None, ("proe", "dropback_rate", "no_huddle_rate")),
        ("formation", "Before the snap", None, (*_FTN_SNAP, "shotgun_rate")),
        (
            "pass_game",
            "The pass game",
            None,
            ("play_action_rate", "screen_rate", "rpo_rate", "deep_shot_rate", "adot"),
        ),
        ("results", "Results", None, ("explosive_rate", "epa_per_play", "success_rate")),
    ),
    "defense": (
        ("pressure", "Pressure", None, ("blitz_rate", "blitz_rate_pfr", "rushers_avg")),
        ("box", "The box", None, ("box_avg", "light_box_rate", "heavy_box_rate")),
        (
            "faced",
            "What offenses do against it",
            "These are the offenses' calls against this defense, so they depend on who it "
            "played as well as on the defense itself (PC02 adjusts for the opponents).",
            (
                "proe",
                "dropback_rate",
                "motion_rate",
                "play_action_rate",
                "screen_rate",
                "deep_shot_rate",
                "adot",
            ),
        ),
        (
            "results",
            "Results allowed",
            "Lower is better for a defense here: these are the offenses' results.",
            ("explosive_rate", "epa_per_play", "success_rate"),
        ),
    ),
}
_GRID: tuple[tuple[str, str, bool], ...] = (
    ("offense", "proe", True),
    ("offense", "play_action_rate", False),
    ("offense", "motion_rate", False),
    ("offense", "qb_under_center_share", False),
    ("offense", "deep_shot_rate", False),
    ("offense", "no_huddle_rate", False),
    ("offense", "epa_per_play", False),
    ("defense", "blitz_rate", True),
    ("defense", "rushers_avg", False),
    ("defense", "heavy_box_rate", False),
    ("defense", "proe", False),
    ("defense", "epa_per_play", False),
)
_WEEKLY: dict[str, tuple[str, ...]] = {
    "offense": (
        "proe",
        "play_action_rate",
        "motion_rate",
        "qb_under_center_share",
        "deep_shot_rate",
        "epa_per_play",
    ),
    "defense": ("blitz_rate", "rushers_avg", "heavy_box_rate", "proe", "epa_per_play"),
}
_MATCHUP: tuple[str, ...] = (
    "proe",
    "play_action_rate",
    "motion_rate",
    "screen_rate",
    "deep_shot_rate",
    "explosive_rate",
    "epa_per_play",
    "blitz_rate",
    "rushers_avg",
    "heavy_box_rate",
)
# the five-second summary: the sentences pick from these, by how far from the league
_SUMMARY_LEAD = {"offense": "proe", "defense": "blitz_rate"}
_SUMMARY_POOL = {
    "offense": (
        "no_huddle_rate",
        "motion_rate",
        "qb_under_center_share",
        "qb_shotgun_share",
        "play_action_rate",
        "screen_rate",
        "rpo_rate",
        "deep_shot_rate",
        "adot",
    ),
    "defense": (
        "rushers_avg",
        "light_box_rate",
        "heavy_box_rate",
        "proe",
        "play_action_rate",
        "deep_shot_rate",
        "explosive_rate",
    ),
}

# ---- history (participation): (id, title, kind, note, metric prefix or names) ---------------
_HISTORY: dict[str, tuple[tuple[str, str, str, str | None, tuple[str, ...]], ...]] = {
    "offense": (
        ("personnel", "Personnel", "stack", "Backs, then tight ends: 11 = 1 RB, 1 TE, 3 WRs.",
         tuple(f"personnel_{g}" for g in (*L.PERSONNEL_GROUPS, "other"))),
        ("formation", "Formation", "stack", "Where the QB lines up (FTN-charted since 2023).",
         tuple(f"formation_{f}" for f in L.FORMATIONS_FTN)),
        ("routes", "Routes of targeted receivers", "bars",
         "Only the targeted receiver's route is charted, per pass attempt with a route.",
         tuple(f"route_{r}" for r in L.ROUTES_FTN)),
        ("coverage", "Coverage it faced", "stack", "Coverage shells against its dropbacks.",
         tuple(f"cov_{c}" for c in L.COVERAGES)),
        ("pressure", "Pressure and time to throw", "bars", None,
         ("pressure_rate", "time_to_throw_avg")),
    ),
    "defense": (
        ("coverage", "Coverage shells", "stack", "Per dropback with a charted shell.",
         tuple(f"cov_{c}" for c in L.COVERAGES)),
        ("man_zone", "Man vs zone", "bars", None, ("man_rate",)),
        ("packages", "Defensive packages", "stack",
         "Defensive backs on the field: base 4 or fewer, nickel 5, dime 6+.",
         tuple(f"def_{p}_share" for p in L.DEF_PACKAGES)),
        ("pressure", "Pressure and time to throw", "bars", None,
         ("pressure_rate", "time_to_throw_avg")),
        ("personnel", "Personnel it faced", "stack", None,
         tuple(f"personnel_{g}" for g in (*L.PERSONNEL_GROUPS, "other"))),
    ),
}  # fmt: skip
HISTORY_NOTE = (
    "Research data: nflverse participation (charted by FTN since 2023), published after each "
    "season (2026's about February 2027). Never used by a forecast."
)


def _metric(name: str) -> L.Metric | None:
    return L.METRIC_BY_NAME.get(name) or (L.PFR_BLITZ if name == L.PFR_BLITZ.name else None)


_ROUTE_NAME = {
    "quick_out": "Quick out",
    "hitch_curl": "Hitch / curl",
    "in_dig": "In / dig",
    "shallow_cross_drag": "Shallow cross / drag",
    "deep_out": "Deep out",
    "texas_angle": "Texas / angle",
}
# a metric family by name prefix: (what one counted play is, the short name of the rest)
_PREFIX: tuple[tuple[str, str], ...] = (
    ("pass_", "attempt"),
    ("run_", "designed run"),
    ("route_", "attempt"),
    ("cov_", "dropback"),
    ("personnel_", "snap"),
    ("formation_", "snap"),
    ("def_", "snap"),
)


def _short(name: str) -> str:
    if name in _SHOW:
        return _SHOW[name][0]
    prefix = next((p for p, _ in _PREFIX if name.startswith(p)), "")
    rest = name[len(prefix) :]
    if prefix == "run_":
        return _LANE_LABEL.get(rest, rest.replace("_", " ").capitalize())
    if prefix == "personnel_":
        return "Other" if rest == "other" else rest  # "11": backs, then tight ends
    if prefix == "route_":
        return _ROUTE_NAME.get(rest, rest.replace("_", " ").capitalize())
    if prefix == "cov_":
        if rest.startswith("cover_"):
            return f"Cover {rest[6:]}"
        return "2-man" if rest == "2_man" else rest.capitalize()
    if prefix == "def_":
        rest = rest.removesuffix("_share")
    return rest.replace("_", " ").capitalize()


def _spelled(name: str) -> str:
    """The name a sentence uses: the short name, spelled out where it's an abbreviation."""
    return {"proe": "Pass rate over expected"}.get(name, _short(name))


def metric_info(name: str, situation: str | None = None) -> dict[str, Any]:
    """How a metric is named and formatted (the TypeScript `PlaycallMetric`)."""
    m = _metric(name)
    unit = m.unit if m else "share"
    per = next((p for prefix, p in _PREFIX if name.startswith(prefix)), "play")
    if name in _SHOW:
        per = _SHOW[name][1]
    caller = "defense" if name in _DEFENSE_CALLS or name.startswith(("cov_", "def_")) else "offense"
    return {
        "metric": name,
        "label": m.label if m else name,
        "short": _short(name),
        "unit": unit,
        "digits": _DIGITS.get(name, 1),
        "source": m.source if m else "pbp",
        "situation": situation or _READ_IN.get(name, "all"),
        "per": per,
        "caller": caller,
        "help": _SHOW.get(name, ("", "", None))[2],
    }


# ---- reading ------------------------------------------------------------------------------


def _season_dir(paths: DataPaths, season: int) -> Path:
    return paths.playcalling / str(season)


def built_seasons(paths: DataPaths) -> list[int]:
    """Seasons with a finished build (`build.json` is written last), newest first."""
    try:
        entries = list(paths.playcalling.iterdir())
    except OSError:
        return []
    out = [
        int(d.name)
        for d in entries
        if d.name.isdigit() and len(d.name) == 4 and (d / "build.json").is_file()
    ]
    return sorted(out, reverse=True)


def _build(paths: DataPaths, season: int) -> dict[str, Any] | None:
    b = read_json(_season_dir(paths, season) / "build.json")
    if b is None:
        return None
    weeks = b.get("as_of_weeks")
    if not (isinstance(weeks, list) and len(weeks) == 2 and all(isinstance(w, int) for w in weeks)):
        return None
    return b


def _read(path: Path, filters: list[tuple[str, str, Any]]) -> pl.DataFrame | None:
    """One short, filtered read with no memory map (D100); None when missing or unreadable."""
    if not path.is_file():
        return None
    try:
        table = pq.read_table(path, filters=filters, memory_map=False)
    except Exception:  # noqa: BLE001 - a half-written or foreign file is "no data"
        return None
    df = pl.from_arrow(table)
    return df if isinstance(df, pl.DataFrame) else None


def _tendencies(paths: DataPaths, season: int, filters: list[tuple[str, str, Any]]) -> pl.DataFrame:
    df = _read(_season_dir(paths, season) / "team_tendencies.parquet", filters)
    return df if df is not None else pl.DataFrame()


def _cell(r: dict[str, Any], games: bool = True) -> dict[str, Any]:
    n = int(r.get("n") or 0)
    g = r.get("games")
    return {
        "n": n,
        "games": int(g) if games and g is not None else None,
        "value": num(r.get("value"), 6),
        "league": num(r.get("league_value"), 6),
        "diff": num(r.get("diff"), 6),
        "pct": num(r.get("pct"), 1),
        "small": n < MIN_N,
    }


def _index(df: pl.DataFrame) -> dict[tuple[str, str, str], dict[str, Any]]:
    """(metric, situation, window) -> cell, for one team, side and as-of week."""
    if df.is_empty():
        return {}
    return {(r["metric"], r["situation"], r["window"]): _cell(r) for r in df.iter_rows(named=True)}


def _windows(ix: dict, metric: str, situation: str) -> dict[str, Any]:
    return {w: ix.get((metric, situation, w)) for w in WINDOWS}


def _has_data(windows: dict[str, Any]) -> bool:
    return any(c is not None and c.get("n") for c in windows.values())


def _game_week(game_id: str) -> int | None:
    parts = str(game_id).split("_")
    return int(parts[1]) if len(parts) == 4 and parts[1].isdigit() else None


def _game_label(game_id: str) -> str | None:
    """'ATL at NO, week 4' built from canonical team codes only; an id that isn't
    `<season>_<week>_<away>_<home>` with two known teams gives None (dropped): no file text
    reaches the browser as a game's name (Sol review, PC01)."""
    parts = str(game_id).split("_")
    if len(parts) == 4 and parts[1].isdigit():
        away, home = _canon(parts[2]), _canon(parts[3])
        if away and home:
            return f"{away} at {home}, week {int(parts[1])}"
    return None


def _canon(code: Any) -> str | None:
    """A team code as one of the 32 canonical ones (an old code mapped: OAK -> LV), else None:
    nothing from a file reaches the browser as a team unless it is a known code (Sol review)."""
    c = TEAM_MAP.get(str(code)) if code is not None else None
    return c if c in CANONICAL_TEAMS else None


def _meta(
    paths: DataPaths, season: int, b: dict[str, Any] | None, seasons: list[int]
) -> dict[str, Any]:
    if b is None:
        return {
            "status": "not_built",
            "message": (
                f"No play-calling tables for {season} yet. Run `{BUILD.format(season=season)}` "
                "(after Tuesday's weekly run)."
            ),
            "season": season,
            "seasons": seasons,
            "as_of_week": None,
            "through_week": None,
            "built_at": None,
            "min_n": MIN_N,
            "ftn_waiting": [],
        }
    as_of = int(b["as_of_weeks"][1])
    waiting = (b.get("coverage") or {}).get("games_without_ftn") or []
    return {
        "status": "ok",
        "message": None,
        "season": season,
        "seasons": seasons,
        "as_of_week": as_of,
        "through_week": as_of - 1 if as_of > 1 else None,
        "built_at": clean(b.get("built_at"), None, 40),
        "min_n": MIN_N,
        "ftn_waiting": [lbl for g in waiting if isinstance(g, str) and (lbl := _game_label(g))][
            :40
        ],
    }


def default_season(paths: DataPaths, fallback: int) -> int:
    seasons = built_seasons(paths)
    return seasons[0] if seasons else fallback


# ---- formatting for the server's sentences ---------------------------------------------------


def _minus(text: str) -> str:
    return text.replace("-", "−") if text.startswith("-") else text


def fmt_value(name: str, v: float | None) -> str:
    """A value as the pages show it: shares in %, PROE in signed points, means in their unit."""
    if v is None:
        return "n/a"
    info = metric_info(name)
    if info["unit"] == "share":
        return f"{v * 100:.1f}%"
    if info["unit"] == "over_expected":
        p = round(v * 100, 1)
        return _minus(f"{p:+.1f}") if p else "0.0"
    return _minus(f"{v:.{info['digits']}f}")


def _value_phrase(name: str, v: float | None) -> str:
    info = metric_info(name)
    text = fmt_value(name, v)
    if info["unit"] == "share":
        return f"{text} of {info['per']}s"
    if info["unit"] == "over_expected":
        return f"{text} points"
    noun = _MEAN_NOUN.get(name)
    return f"{text} {noun}" if noun else text


def _ordinal(k: int) -> str:
    suffix = "th" if 10 <= k % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(k % 10, "th")
    return f"{k}{suffix}"


def _pct_phrase(pct: float | None) -> str:
    if pct is None:
        return ""
    if pct >= 99.95:
        return "the league's highest"
    if pct <= 0.05:
        return "the league's lowest"
    return f"{_ordinal(round(pct))} percentile"


# ---- the teams grid ---------------------------------------------------------------------------


def _window_for(as_of: int) -> str:
    return "season" if as_of > 1 else "last_season"


def get_teams(paths: DataPaths, season: int) -> dict[str, Any]:
    seasons = built_seasons(paths)
    b = _build(paths, season)
    meta = _meta(paths, season, b, seasons)
    columns = [
        {**metric_info(m), "id": f"{side}.{m}", "side": side, "signature": sig}
        for side, m, sig in _GRID
    ]
    if b is None:
        return {**meta, "window": "season", "columns": columns, "teams": []}
    as_of = meta["as_of_week"]
    window = _window_for(as_of)
    metrics = sorted({m for _, m, _ in _GRID} | {L.PFR_BLITZ.name})
    df = _tendencies(
        paths,
        season,
        [
            ("as_of_week", "=", as_of),
            ("window", "=", window),
            ("metric", "in", metrics),
            ("situation", "in", ["all", "neutral"]),
        ],
    )
    cells: dict[tuple[str, str, str], dict[str, Any]] = {}
    games: dict[str, int] = {}
    if not df.is_empty():
        df = df.filter(~pl.col("history_only"))
        for r in df.iter_rows(named=True):
            info = metric_info(r["metric"])
            if r["situation"] != info["situation"]:
                continue
            cells[(r["team"], r["side"], r["metric"])] = _cell(r)
            if r["games"] is not None:
                games[r["team"]] = max(games.get(r["team"], 0), int(r["games"]))
    present = {f"{side}.{m}" for _, side, m in cells}
    if "defense.blitz_rate" not in present:
        # before FTN the defense's signature is PFR's blitz count (2018-2021), and with no
        # blitz data at all (2016-2017) the PROE offenses had against it (review, PC01)
        if "defense.blitz_rate_pfr" in present:
            sig = {**metric_info(L.PFR_BLITZ.name), "side": "defense", "signature": True}
            sig["id"] = "defense.blitz_rate_pfr"
            columns = [sig if c["id"] == "defense.blitz_rate" else c for c in columns]
        else:
            columns = [
                {**c, "signature": True} if c["id"] == "defense.proe" else c
                for c in columns
                if c["id"] != "defense.blitz_rate"
            ]
    columns = [c for c in columns if c["id"] in present or c["signature"]]
    teams = [
        {
            "team": t,
            "games": games.get(t, 0),
            "cells": {c["id"]: cells.get((t, c["side"], c["metric"])) for c in columns},
        }
        for t in sorted(CANONICAL_TEAMS)
    ]
    return {**meta, "window": window, "columns": columns, "teams": teams}


# ---- a team's page -----------------------------------------------------------------------------


def _summary(side: str, ix: dict, window: str) -> list[str]:
    """Two or three plain sentences: the team's identity in five seconds."""

    def sentence(name: str) -> str | None:
        info = metric_info(name)
        c = ix.get((name, info["situation"], window))
        if c is None or c["small"] or c["value"] is None:
            return None
        where = " in neutral situations" if info["situation"] == "neutral" else ""
        lead = _spelled(name)
        if side == "defense" and info["caller"] == "offense":
            lead = f"Offenses against it, {lead[0].lower()}{lead[1:]}"
        rank = _pct_phrase(c["pct"])
        league = fmt_value(name, c["league"])
        tail = f", {rank}" if rank else ""
        return f"{lead}: {_value_phrase(name, c['value'])}{where} (league {league}){tail}."

    out = []
    first = sentence(_SUMMARY_LEAD[side])
    if first:
        out.append(first)
    pool = []
    for name in _SUMMARY_POOL[side]:
        info = metric_info(name)
        c = ix.get((name, info["situation"], window))
        if c is None or c["small"] or c["pct"] is None:
            continue
        if c["pct"] >= 85 or c["pct"] <= 15:
            pool.append((abs(c["pct"] - 50), name))
    for _, name in sorted(pool, key=lambda x: (-x[0], x[1]))[:2]:
        s = sentence(name)
        if s:
            out.append(s)
    return out


def _next_game(
    paths: DataPaths, season: int, team: str, from_week: int, now: dt.datetime | None
) -> dict[str, Any] | None:
    """The team's first game from the as-of week on that hasn't kicked off yet (a Thursday
    game played since the tables were built isn't "next"; review, PC01)."""
    g = curated(
        paths, "games", ["game_id", "season", "week", "away_team", "home_team", "kickoff_utc"]
    )
    if g is None or g.is_empty() or not set(g.columns) >= _GAME_COLS:
        return None
    g = g.filter(
        (pl.col("season") == season)
        & (pl.col("week") >= from_week)
        & ((pl.col("away_team") == team) | (pl.col("home_team") == team))
    )
    if now is not None and "kickoff_utc" in g.columns and g.height:
        cut = now if now.tzinfo else now.replace(tzinfo=dt.UTC)
        g = g.filter(pl.col("kickoff_utc").is_null() | (pl.col("kickoff_utc") > cut))
    for r in g.sort("week").iter_rows(named=True):
        away, home_team = _canon(r["away_team"]), _canon(r["home_team"])
        if not (away and home_team):
            continue  # a game whose teams aren't known codes isn't shown (Sol review)
        home = home_team == team
        return {
            "season": season,
            "week": int(r["week"]),
            "game_id": clean(r["game_id"], None, 40),
            "opponent": away if home else home_team,
            "home": home,
            "kickoff": _iso(r.get("kickoff_utc")),
        }
    return None


_GAME_COLS = {"game_id", "season", "week", "away_team", "home_team"}


def _iso(t: Any) -> str | None:
    if isinstance(t, dt.datetime):
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.UTC)
        return t.isoformat().replace("+00:00", "Z")
    return None


def _weekly(
    paths: DataPaths, season: int, team: str, side: str, ix: dict, window: str, as_of: int
) -> dict[str, Any] | None:
    """One point per game before the as-of week: the same games as the header's "weeks 1-N
    played" (a Thursday game curated since then waits for the next as-of week; review, PC01)."""
    names = _WEEKLY[side]
    df = _read(
        _season_dir(paths, season) / "team_game_tendencies.parquet",
        [
            ("team", "=", team),
            ("side", "=", side),
            ("week", "<", as_of),
            ("metric", "in", list(names)),
            ("situation", "in", ["all", "neutral"]),
        ],
    )
    if df is None or df.is_empty():
        return None
    df = df.filter(~pl.col("history_only"))
    games = (
        df.select("week", "game_id", "opponent")
        .unique()
        .sort("week", "game_id")
        .iter_rows(named=True)
    )
    game_list, raw_ids = [], []
    for g in games:
        parts = str(g["game_id"]).split("_")
        opponent = _canon(g["opponent"])
        if opponent is None:
            continue  # an opponent that isn't a known team code isn't shown (Sol review)
        raw_ids.append(g["game_id"])
        game_list.append(
            {
                "week": int(g["week"]),
                "game_id": clean(g["game_id"], None, 40),
                "opponent": opponent,
                # a relocated franchise's old id (2019_03_KC_OAK) names the old code: map it
                # before comparing with the canonical team (Sol review, PC01)
                "home": len(parts) == 4 and _canon(parts[3]) == team,
            }
        )
    series = []
    for name in names:
        info = metric_info(name)
        rows = {
            (r["game_id"]): r
            for r in df.filter(
                (pl.col("metric") == name) & (pl.col("situation") == info["situation"])
            ).iter_rows(named=True)
        }
        if not rows:
            continue
        league_cell = ix.get((name, info["situation"], window))
        series.append(
            {
                **info,
                "league": league_cell["league"] if league_cell else None,
                "points": [
                    {
                        "week": g["week"],
                        "value": num(rows[gid]["value"], 6) if gid in rows else None,
                        "n": int(rows[gid]["n"] or 0) if gid in rows else 0,
                    }
                    for g, gid in zip(game_list, raw_ids, strict=True)
                ],
            }
        )
    return {"games": game_list, "series": series}


def get_team(
    paths: DataPaths, season: int, team: str, side: str, now: dt.datetime | None = None
) -> dict[str, Any]:
    seasons = built_seasons(paths)
    b = _build(paths, season)
    meta = _meta(paths, season, b, seasons)
    empty = {
        "team": team,
        "side": side,
        "games": 0,
        "summary": [],
        "identity": [],
        "situations": None,
        "field": None,
        "runs": [],
        "weekly": None,
        "next_game": None,
        "notes": [],
    }
    if b is None:
        return {**meta, **empty}
    as_of = meta["as_of_week"]
    df = _tendencies(
        paths,
        season,
        [("team", "=", team), ("side", "=", side), ("as_of_week", "=", as_of)],
    )
    if not df.is_empty():
        df = df.filter(~pl.col("history_only"))
    ix = _index(df)
    window = _window_for(as_of)
    in_season = df.filter(pl.col("window") == "season") if not df.is_empty() else df
    season_games = int(in_season["games"].max() or 0) if in_season.height else 0

    identity = []
    for gid, title, note, names in _IDENTITY[side]:
        rows = []
        for name in names:
            if name == "shotgun_rate" and any(_has_data(_windows(ix, m, "all")) for m in _FTN_SNAP):
                continue  # FTN splits shotgun from pistol from 2022; pbp's flag is the fallback
            info = metric_info(name)
            w = _windows(ix, name, info["situation"])
            if _has_data(w):
                rows.append({**info, "windows": w})
        if rows:
            identity.append({"id": gid, "title": title, "note": note, "rows": rows})

    # the columns: only the metrics the season has (no FTN before 2022: no play-action or
    # blitz column of empty cells; review, PC01)
    sit_names = [
        m
        for m in SITUATION_METRICS
        if any(_has_data(_windows(ix, m, s)) for s in ("all", *_ALL_BUCKETS))
    ]
    families = []
    for fam, title, buckets in _FAMILIES:
        rows = [
            {
                "situation": s,
                "label": _SITUATION_LABEL[s],
                "cells": {m: _windows(ix, m, s) for m in sit_names},
            }
            for s in buckets
        ]
        families.append({"family": fam, "title": title, "rows": rows})
    situations = {
        "metrics": [metric_info(m, "all") for m in sit_names],
        "baseline": {
            "situation": "all",
            "label": _SITUATION_LABEL["all"],
            "cells": {m: _windows(ix, m, "all") for m in sit_names},
        },
        "families": families,
    }

    def zone(metric: str, depth: str | None, direction: str) -> dict[str, Any]:
        return {
            "metric": metric,
            "depth": depth,
            "direction": direction,
            "windows": _windows(ix, metric, "all"),
        }

    field = {
        "zones": [
            zone(f"pass_{d}_{loc}", d, loc) for d in ("short", "deep") for loc in L.PASS_LOCATIONS
        ],
        "directions": [zone(f"pass_{loc}", None, loc) for loc in L.PASS_LOCATIONS],
        "depth": [
            {**metric_info(m), "windows": _windows(ix, m, "all")}
            for m in ("deep_shot_rate", "adot")
        ],
    }
    runs = [
        {
            "lane": d,
            "metric": f"run_{d}",
            "label": _LANE_LABEL[d],
            "windows": _windows(ix, f"run_{d}", "all"),
        }
        for d in L.RUN_DIRECTIONS
    ]
    notes = []
    if side == "defense":
        notes.append(
            "A defense's rates are what offenses did against it, except its own calls (blitz, "
            "rushers, box). They depend on the offenses it faced; PC02's forecast adjusts for "
            "them. Situations read from the offense's side: 'Down 9+' means the offense trailed."
        )
    if as_of == 1:
        notes.append(
            "Before week 1's games: every rate is last season's (rosters and coaches may have "
            "changed)."
        )
    if meta["ftn_waiting"]:
        notes.append(
            "FTN hasn't charted every game yet (it lands about two days after a game): "
            + "; ".join(meta["ftn_waiting"][:6])
            + ". Play-action, motion, blitz and box rates count the charted games only."
        )
    return {
        **meta,
        "team": team,
        "side": side,
        "games": season_games,
        "summary": _summary(side, ix, window),
        "identity": identity,
        "situations": situations,
        "field": field,
        "runs": runs,
        "weekly": _weekly(paths, season, team, side, ix, window, as_of),
        "next_game": _next_game(paths, season, team, as_of, now),
        "notes": notes,
    }


# ---- History (participation, research data) ---------------------------------------------------


def history_seasons(paths: DataPaths) -> list[int]:
    """The newest participation seasons in one vocabulary (2023+), oldest first."""
    out = []
    for s in built_seasons(paths):
        b = _build(paths, s)
        # `is True`: the build writes a bool; anything else isn't a participation season
        if s >= HISTORY_FIRST and b is not None and b.get("history_metrics") is True:
            out.append(s)
    return sorted(out[:HISTORY_SEASONS])


def get_history(paths: DataPaths, team: str, side: str) -> dict[str, Any]:
    seasons = history_seasons(paths)
    base = {
        "team": team,
        "side": side,
        "seasons": seasons,
        "research": True,
        "source_note": HISTORY_NOTE,
        "min_n": MIN_N,
    }
    if not seasons:
        newest = default_season(paths, HISTORY_FIRST)
        return {
            **base,
            "status": "not_built",
            "message": (
                "No participation history is built. Run "
                f"`{BUILD.format(season=newest)} --history all`."
            ),
            "groups": [],
        }
    by_season: dict[int, dict[str, dict[str, Any]]] = {}
    for s in seasons:
        b = _build(paths, s)
        as_of = int(b["as_of_weeks"][1]) if b else None
        df = _tendencies(
            paths,
            s,
            [
                ("team", "=", team),
                ("side", "=", side),
                ("as_of_week", "=", as_of),
                ("window", "=", "season"),
                ("situation", "=", "all"),
            ],
        )
        if not df.is_empty():
            df = df.filter(pl.col("history_only"))
        by_season[s] = {r["metric"]: _cell(r) for r in df.iter_rows(named=True)}

    groups = []
    for gid, title, kind, note, names in _HISTORY[side]:
        rows = []
        for name in names:
            cells = {str(s): by_season[s].get(name) for s in seasons}
            if any(c is not None and c["n"] for c in cells.values()):
                rows.append({**metric_info(name), "seasons": cells})
        if kind == "bars" and gid == "routes":
            newest = str(seasons[-1])
            rows.sort(key=lambda r: -((r["seasons"].get(newest) or {}).get("value") or 0))
        if rows:
            groups.append({"id": gid, "title": title, "kind": kind, "note": note, "rows": rows})
    return {**base, "status": "ok", "message": None, "groups": groups}


# ---- the week's Play calls tab ------------------------------------------------------------------


def _week_games(paths: DataPaths, season: int, week: int) -> list[dict[str, Any]]:
    g = curated(
        paths, "games", ["game_id", "season", "week", "away_team", "home_team", "kickoff_utc"]
    )
    if g is None or g.is_empty() or not set(g.columns) >= _GAME_COLS:
        return []  # no schedule (or an older table without these columns): no games
    if "kickoff_utc" not in g.columns:  # no kickoff times: games with a null kickoff (Sol)
        g = g.with_columns(pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("kickoff_utc"))
    g = g.filter((pl.col("season") == season) & (pl.col("week") == week)).sort(
        "kickoff_utc", "game_id", nulls_last=True
    )
    return [
        {
            "game_id": clean(r["game_id"], None, 40),
            "kickoff": _iso(r.get("kickoff_utc")),
            "away": r["away_team"],
            "home": r["home_team"],
        }
        for r in g.iter_rows(named=True)
        if r["away_team"] in CANONICAL_TEAMS and r["home_team"] in CANONICAL_TEAMS
    ]


def _sd(values: Iterable[float | None]) -> float | None:
    v = [x for x in values if x is not None and math.isfinite(x)]
    if len(v) < 3:
        return None
    m = sum(v) / len(v)
    var = sum((x - m) ** 2 for x in v) / (len(v) - 1)
    return math.sqrt(var) if var > 0 else None


def _rank_phrase(value: float, values: list[float]) -> str:
    higher = sum(1 for x in values if x > value)
    lower = sum(1 for x in values if x < value)
    if higher == 0:
        return "the league's highest"
    if lower == 0:
        return "the league's lowest"
    if higher <= lower:
        return f"{_ordinal(higher + 1)} highest"
    return f"{_ordinal(lower + 1)} lowest"


def _shift_text(
    offense: str, defense: str, name: str, d: dict, o: dict | None, values: list[float]
) -> str:
    info = metric_info(name)
    rank = _rank_phrase(d["value"], values)
    league = fmt_value(name, d["league"])
    where = " in neutral situations" if info["situation"] == "neutral" else ""
    dv = _value_phrase(name, d["value"])
    ov = fmt_value(name, o["value"]) if o and o["value"] is not None else "n/a"
    spelled = _spelled(name)
    short = spelled[0].lower() + spelled[1:]
    if info["caller"] == "defense":
        return (
            f"{defense}'s {short}: {dv}{where} ({rank}; league {league}). {offense} has faced {ov}."
        )
    return (
        f"Against {defense}: {short} {dv}{where} ({rank}; league {league}). "
        f"{offense}'s offense: {ov}."
    )


def get_play_calls(paths: DataPaths, season: int, week: int) -> dict[str, Any]:
    seasons = built_seasons(paths)
    b = _build(paths, season)
    meta = _meta(paths, season, b, seasons)
    metrics = [metric_info(m) for m in _MATCHUP]
    note = (
        "Descriptive: each offense's rates so far next to what the other defense has allowed "
        "and the league. It isn't a forecast: PC02 adds one, adjusted for the opponents faced."
    )
    base = {"week": week, "note": note, "metrics": metrics, "games": [], "shifts": []}
    if b is None:
        return {**meta, **base, "window": "season"}
    newest = meta["as_of_week"]
    if not 1 <= week <= newest:
        return {
            **meta,
            **base,
            "status": "not_yet",
            "message": (
                f"The {season} play-calling tables run through week {newest}. Week {week}'s "
                f"come with `{BUILD.format(season=season)}` after the Tuesday run before it."
            ),
            "window": "season",
            "as_of_week": None,
            "through_week": None,
        }
    window = _window_for(week)
    waiting = [
        lbl
        for g in (b.get("coverage") or {}).get("games_without_ftn") or []
        if isinstance(g, str) and (_game_week(g) or week) < week and (lbl := _game_label(g))
    ]
    meta = {
        **meta,
        "as_of_week": week,
        "through_week": week - 1 if week > 1 else None,
        "ftn_waiting": waiting[:40],  # only the games this week's rates count (review, PC01)
    }
    df = _tendencies(
        paths,
        season,
        [
            ("as_of_week", "=", week),
            ("window", "=", window),
            ("metric", "in", list(_MATCHUP)),
            ("situation", "in", ["all", "neutral"]),
        ],
    )
    cells: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not df.is_empty():
        df = df.filter(~pl.col("history_only"))
        for r in df.iter_rows(named=True):
            if r["situation"] == metric_info(r["metric"])["situation"]:
                cells[(r["team"], r["side"], r["metric"])] = _cell(r)
    # the rows: only the metrics the season has (no FTN before 2022; review, PC01)
    active = [m for m in _MATCHUP if any(mm == m and c["n"] for (_, _, mm), c in cells.items())]
    # the spread and the ranks over the defenses with a real sample (review, PC01)
    def_values = {
        m: [
            c["value"]
            for (_, s, mm), c in cells.items()
            if s == "defense" and mm == m and c["value"] is not None and not c["small"]
        ]
        for m in active
    }
    sds = {m: _sd(v) for m, v in def_values.items()}

    games, candidates = [], []
    for g in _week_games(paths, season, week):
        matchups = []
        for off, dfn in ((g["away"], g["home"]), (g["home"], g["away"])):
            rows = []
            for m in active:
                o = cells.get((off, "offense", m))
                d = cells.get((dfn, "defense", m))
                league = (d or o or {}).get("league")
                shift = raw = None
                if d and d["diff"] is not None and sds[m]:
                    raw = d["diff"] / sds[m]
                    shift = round(raw, 2)  # for display; the rules use the raw ratio (Sol)
                same = None
                if o and d and o["diff"] is not None and d["diff"] is not None:
                    same = (o["diff"] > 0) == (d["diff"] > 0)
                rows.append(
                    {
                        "metric": m,
                        "offense": o,
                        "defense": d,
                        "league": league,
                        "shift": shift,
                        "same_way": same,
                    }
                )
                if (
                    raw is not None
                    and abs(raw) >= SHIFT_MIN_SD
                    and d is not None
                    and not d["small"]
                    and o is not None
                    and not o["small"]
                ):
                    candidates.append((abs(raw), g["game_id"], off, dfn, m, shift, d, o))
            matchups.append({"offense": off, "defense": dfn, "rows": rows})
        games.append({**g, "matchups": matchups})

    shifts, seen = [], set()
    for _, gid, off, dfn, m, shift, d, o in sorted(candidates, key=lambda c: -c[0]):
        if (gid, off) in seen:
            continue  # one per matchup: the week's biggest, across games
        seen.add((gid, off))
        shifts.append(
            {
                "game_id": gid,
                "offense": off,
                "defense": dfn,
                "metric": m,
                "shift": shift,
                "text": _shift_text(off, dfn, m, d, o, def_values[m]),
            }
        )
        if len(shifts) == MAX_SHIFTS:
            break
    message = None
    if not games:
        message = f"No week-{week} games in the schedule."
    return {
        **meta,
        **base,
        "message": message,
        "window": window,
        "metrics": [metric_info(m) for m in active],
        "games": games,
        "shifts": shifts,
    }
