"""Play-calling definitions, situation buckets and the metric registry (PC00, D122).

Everything the tendency tables, the Play calling pages (PC01) and the forecast (PC02) agree on
lives here, so a definition changes in one place. The guide (`documentation/guides/
play-calling.md` §3) explains each one in plain words.

The enriched plays (`build.enrich_plays`) carry the label columns named below; every metric is a
pair of Polars expressions over those columns:
- `den`: which plays count (a boolean);
- `num`: the value of a counted play (0 / 1 for a share, a number for a mean).

A team's rate over any set of plays is then `sum(num) / count(den)`, which is what makes the
as-of windows cheap: sums add up across games.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

# --- definitions ------------------------------------------------------------------------------
NEUTRAL_WP = (0.20, 0.80)  # inclusive; the offense's `wp` (nflverse, no Vegas): rbsdm's rule
NEUTRAL_MAX_DOWN = 3  # downs 1-3
TWO_MINUTE_SECONDS = 120  # the last two minutes of a half: `half_seconds_remaining` <= 120
DEEP_SHOT_AIR_YARDS = 20  # a deep shot: a pass attempt thrown 20+ air yards
EXPLOSIVE_PASS_YARDS = 20  # an explosive play: a dropback gaining 20+ yards ...
EXPLOSIVE_RUN_YARDS = 10  # ... or a designed run gaining 10+
LIGHT_BOX_MAX = 6  # FTN box count: 6 or fewer = light
HEAVY_BOX_MIN = 8  # 8 or more = heavy (stacked)
SHORT_MAX, MEDIUM_MAX = 3, 6  # distance: 1-3 short, 4-6 medium, 7+ long
RED_ZONE_MAX = 20  # yards to goal
OWN_DEEP_MIN = 80  # yards to goal 80+ = the offense's own 1-20
MIDFIELD = 50  # yards to goal 50-79 = own half; 21-49 = opponent's half
BIG_LEAD = 9  # score states: 9+ points = more than one score
RECENT_GAMES = 4  # the "last 4 games" window
# Availability (leakage rule 1, Sol review D123): an as-of-W row is what a run on the Tuesday
# after week W-1 could see. Play-by-play: every earlier game. FTN: a game counts once
# FTN_LAG_HOURS have passed since its kickoff (FTN's own 48-hour target), so Monday night's
# game waits a week. PFR: PFR_LAG_WEEKS behind (D44: week W-1 isn't out by Tuesday).
FTN_LAG_HOURS = 48
PFR_LAG_WEEKS = 1
CUTOFF_TZ = "America/New_York"
CUTOFF_WEEKDAY = 1  # Tuesday (Monday = 0); the cutoff is the end of that day, US Eastern

SIDES = ("offense", "defense")
WINDOWS = ("season", "last4", "last_season")

# Era-specific vocabularies in nflverse participation (normalized: lowercase, spaces / slashes /
# hyphens -> underscores). 2016-2022 come from NGS, 2023+ from FTN: the labels differ.
FORMATIONS_NGS = ("shotgun", "singleback", "i_form", "empty", "pistol", "jumbo", "wildcat")
FORMATIONS_FTN = ("shotgun", "under_center", "pistol")
ROUTES_NGS = (
    "go", "flat", "hitch", "cross", "screen", "slant", "out", "post", "corner", "angle", "in",
    "wheel",
)  # fmt: skip
ROUTES_FTN = (
    "quick_out", "hitch_curl", "screen", "in_dig", "go", "shallow_cross_drag", "deep_out",
    "slant", "post", "swing", "corner", "wheel", "texas_angle",
)  # fmt: skip
COVERAGES = (
    "cover_0", "cover_1", "cover_2", "2_man", "cover_3", "cover_4", "cover_6", "cover_9",
    "combo", "prevent", "blown",
)  # fmt: skip
PERSONNEL_GROUPS = ("11", "12", "13", "21", "22", "10")  # anything else counts as "other"
DEF_PACKAGES = ("base", "nickel", "dime")  # <= 4 defensive backs, 5, 6+
NGS_ERA_LAST = 2022  # participation seasons up to here use the NGS vocabularies
PART_FIRST, PART_LAST = 2016, 2025  # participation on D: (2026's arrives about February 2027)
COVERAGE_FIRST = 2018  # coverage and man / zone exist from 2018
FTN_FIRST = 2022
PFR_FIRST = 2018

RUN_DIRECTIONS = (
    "left_end", "left_tackle", "left_guard", "middle", "right_guard", "right_tackle", "right_end",
)  # fmt: skip
PASS_LOCATIONS = ("left", "middle", "right")
PASS_ZONES = tuple(f"{d}_{loc}" for d in ("short", "deep") for loc in PASS_LOCATIONS)


def snake(text: str) -> str:
    """'HITCH/CURL' -> 'hitch_curl', 'UNDER CENTER' -> 'under_center' (participation labels)."""
    out = text.strip().lower()
    for ch in (" ", "/", "-"):
        out = out.replace(ch, "_")
    while "__" in out:
        out = out.replace("__", "_")
    return out


# --- play-level labels (expressions over curated `plays` columns) ------------------------------
def scrimmage_filter() -> pl.Expr:
    """Plays a tendency counts: runs and passes (sacks and scrambles included), no two-point
    tries; kneels, spikes and `no_play` rows are other play types and drop out."""
    return pl.col("play_type").is_in(["pass", "run"]) & (
        pl.col("two_point_attempt").fill_null(0) == 0
    )


def distance_bucket(distance: pl.Expr) -> pl.Expr:
    return (
        pl.when(distance <= SHORT_MAX)
        .then(pl.lit("short"))
        .when(distance <= MEDIUM_MAX)
        .then(pl.lit("medium"))
        .when(distance.is_not_null())
        .then(pl.lit("long"))
    )


def down_distance(down: pl.Expr, distance: pl.Expr) -> pl.Expr:
    """'1st_down', '2nd_short' ... '3rd_long', '4th_down' (null without a down)."""
    db = distance_bucket(distance)
    return (
        pl.when(down == 1)
        .then(pl.lit("1st_down"))
        .when(down == 2)
        .then(pl.lit("2nd_") + db)
        .when(down == 3)
        .then(pl.lit("3rd_") + db)
        .when(down == 4)
        .then(pl.lit("4th_down"))
    )


def field_zone(yards_to_goal: pl.Expr) -> pl.Expr:
    """own_deep (own 1-20), own_half (own 21-50), opp_half (opponent's 49-21), red_zone (20-1)."""
    return (
        pl.when(yards_to_goal <= RED_ZONE_MAX)
        .then(pl.lit("red_zone"))
        .when(yards_to_goal < MIDFIELD)
        .then(pl.lit("opp_half"))
        .when(yards_to_goal < OWN_DEEP_MIN)
        .then(pl.lit("own_half"))
        .when(yards_to_goal.is_not_null())
        .then(pl.lit("own_deep"))
    )


def score_state(score_diff: pl.Expr) -> pl.Expr:
    """From the offense's side: trail_9plus, trail_1to8, tied, lead_1to8, lead_9plus."""
    return (
        pl.when(score_diff <= -BIG_LEAD)
        .then(pl.lit("trail_9plus"))
        .when(score_diff < 0)
        .then(pl.lit("trail_1to8"))
        .when(score_diff == 0)
        .then(pl.lit("tied"))
        .when(score_diff < BIG_LEAD)
        .then(pl.lit("lead_1to8"))
        .when(score_diff.is_not_null())
        .then(pl.lit("lead_9plus"))
    )


def two_minute(quarter: pl.Expr, half_seconds: pl.Expr) -> pl.Expr:
    return quarter.is_in([2, 4]) & (half_seconds <= TWO_MINUTE_SECONDS)


def time_bucket(quarter: pl.Expr, half_seconds: pl.Expr) -> pl.Expr:
    """q1, q2, q2_2min, q3, q4, q4_2min, ot."""
    q = quarter.cast(pl.Int64).cast(pl.String)
    return (
        pl.when(quarter == 5)
        .then(pl.lit("ot"))
        .when(two_minute(quarter, half_seconds))
        .then(pl.lit("q") + q + pl.lit("_2min"))
        .when(quarter.is_between(1, 4))
        .then(pl.lit("q") + q)
    )


def neutral(wp: pl.Expr, down: pl.Expr, half_seconds: pl.Expr) -> pl.Expr:
    """Win probability 20-80%, downs 1-3, not the last two minutes of a half (rbsdm)."""
    lo, hi = NEUTRAL_WP
    return (
        wp.is_between(lo, hi) & (down <= NEUTRAL_MAX_DOWN) & (half_seconds > TWO_MINUTE_SECONDS)
    ).fill_null(False)


def run_direction(location: pl.Expr, gap: pl.Expr) -> pl.Expr:
    """'left_end' ... 'middle' ... 'right_end' from pbp `run_location` x `run_gap` (a middle
    run has no gap; a side run without a gap stays null)."""
    return (
        pl.when(location == "middle")
        .then(pl.lit("middle"))
        .when(location.is_in(["left", "right"]) & gap.is_in(["end", "tackle", "guard"]))
        .then(location + pl.lit("_") + gap)
    )


def pass_zone(length: pl.Expr, location: pl.Expr) -> pl.Expr:
    """'short_left' ... 'deep_right' from the gamebook's `pass_length` (deep = 16+ air yards in
    practice) x `pass_location`."""
    return pl.when(length.is_in(["short", "deep"]) & location.is_in(list(PASS_LOCATIONS))).then(
        length + pl.lit("_") + location
    )


# --- situations --------------------------------------------------------------------------------
@dataclass(frozen=True)
class Situation:
    name: str
    family: str  # core | down_distance | field_zone | score | time
    expr: Callable[[], pl.Expr]  # over the enriched columns


def _eq(col: str, value: str) -> Callable[[], pl.Expr]:
    return lambda: (pl.col(col) == value).fill_null(False)


SITUATIONS: tuple[Situation, ...] = (
    Situation("all", "core", lambda: pl.lit(True)),
    Situation("neutral", "core", lambda: pl.col("neutral").fill_null(False)),
    *(
        Situation(dd, "down_distance", _eq("down_distance", dd))
        for dd in (
            "1st_down",
            "2nd_short",
            "2nd_medium",
            "2nd_long",
            "3rd_short",
            "3rd_medium",
            "3rd_long",
            "4th_down",
        )  # fmt: skip
    ),
    *(
        Situation(z, "field_zone", _eq("field_zone", z))
        for z in ("own_deep", "own_half", "opp_half", "red_zone")
    ),
    *(
        Situation(s, "score", _eq("score_state", s))
        for s in ("trail_9plus", "trail_1to8", "tied", "lead_1to8", "lead_9plus")
    ),
    Situation("two_minute", "time", lambda: pl.col("two_minute").fill_null(False)),
)
CORE_SITUATIONS = tuple(s for s in SITUATIONS if s.family == "core")
SITUATION_FAMILY = {s.name: s.family for s in SITUATIONS}


# --- metrics -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class Metric:
    name: str
    label: str  # plain English, for the pages
    den: Callable[[], pl.Expr]  # which plays count
    num: Callable[[], pl.Expr]  # value of a counted play
    unit: str  # share | mean | over_expected
    source: str  # pbp | ftn | participation
    situational: bool = False  # also by down & distance, field zone, score and two-minute
    history_only: bool = False  # research data (participation): never a 2026 / forecast input
    first_season: int | None = None
    last_season: int | None = None

    def exists_in(self, season: int) -> bool:
        return (self.first_season is None or season >= self.first_season) and (
            self.last_season is None or season <= self.last_season
        )

    def situations(self) -> tuple[Situation, ...]:
        if self.history_only:
            return SITUATIONS[:1]  # history metrics: all plays only
        return SITUATIONS if self.situational else CORE_SITUATIONS


def _c(name: str) -> pl.Expr:
    return pl.col(name)


def _flag(name: str) -> Callable[[], pl.Expr]:
    return lambda: _c(name).cast(pl.Float64)


def _is(col: str, value: str) -> Callable[[], pl.Expr]:
    return lambda: (_c(col) == value).cast(pl.Float64)


def _has(*cols: str, also: Callable[[], pl.Expr] | None = None) -> Callable[[], pl.Expr]:
    def expr() -> pl.Expr:
        e = pl.all_horizontal([_c(c).is_not_null() for c in cols])
        return (e & also()) if also else e

    return expr


def _dropback() -> pl.Expr:
    return _c("dropback").fill_null(False)


def _attempt() -> pl.Expr:
    return _c("pass_attempt").fill_null(False)


def _designed_run() -> pl.Expr:
    return _c("designed_run").fill_null(False)


def _ftn() -> pl.Expr:
    return _c("ftn_charted").fill_null(False)


def _part() -> pl.Expr:
    return _c("part_charted").fill_null(False)


def _pbp_metrics() -> list[Metric]:
    m = [
        Metric("dropback_rate", "Dropback rate (passes, sacks, scrambles)", lambda: pl.lit(True),
               _flag("dropback"), "share", "pbp", situational=True),
        Metric("proe", "Pass rate over expected", _has("xpass"),
               lambda: _c("dropback").cast(pl.Float64) - _c("xpass"), "over_expected", "pbp",
               situational=True),
        Metric("shotgun_rate", "Shotgun or pistol snaps (play-by-play)", _has("shotgun"),
               _flag("shotgun"), "share", "pbp", situational=True),
        Metric("no_huddle_rate", "No-huddle snaps", _has("no_huddle"), _flag("no_huddle"),
               "share", "pbp"),
        Metric("deep_shot_rate", f"Deep shots ({DEEP_SHOT_AIR_YARDS}+ air yards) per attempt",
               _has("air_yards", also=_attempt), _flag("deep_shot"), "share", "pbp",
               situational=True),
        Metric("adot", "Average depth of target (air yards per attempt)",
               _has("air_yards", also=_attempt), lambda: _c("air_yards"), "mean", "pbp"),
        *(
            Metric(f"pass_{loc}", f"Passes to the {loc}", _has("pass_location", also=_attempt),
                   _is("pass_location", loc), "share", "pbp")
            for loc in PASS_LOCATIONS
        ),
        *(
            Metric(f"pass_{z}", f"Passes {z.replace('_', ' ')}", _has("pass_zone", also=_attempt),
                   _is("pass_zone", z), "share", "pbp")
            for z in PASS_ZONES
        ),
        *(
            Metric(f"run_{d}", f"Designed runs: {d.replace('_', ' ')}",
                   _has("run_direction", also=_designed_run), _is("run_direction", d), "share",
                   "pbp")
            for d in RUN_DIRECTIONS
        ),
        Metric("explosive_rate",
               f"Explosive plays (dropbacks {EXPLOSIVE_PASS_YARDS}+, runs {EXPLOSIVE_RUN_YARDS}+ "
               "yards)", _has("explosive"), _flag("explosive"), "share", "pbp"),
        Metric("epa_per_play", "EPA per play", _has("epa"), lambda: _c("epa"), "mean", "pbp"),
        Metric("success_rate", "Success rate (EPA > 0)", _has("success"), _flag("success"),
               "share", "pbp"),
    ]  # fmt: skip
    return m


def _ftn_metrics() -> list[Metric]:
    f = FTN_FIRST
    return [
        Metric("motion_rate", "Pre-snap motion", _has("motion", also=_ftn), _flag("motion"),
               "share", "ftn", first_season=f),
        Metric("play_action_rate", "Play-action per dropback", _has("play_action", also=lambda:
               _ftn() & _dropback()), _flag("play_action"), "share", "ftn", situational=True,
               first_season=f),
        Metric("screen_rate", "Screens per dropback", _has("screen", also=lambda: _ftn() &
               _dropback()), _flag("screen"), "share", "ftn", first_season=f),
        Metric("rpo_rate", "Run-pass options (RPO)", _has("rpo", also=_ftn), _flag("rpo"),
               "share", "ftn", first_season=f),
        *(
            Metric(f"qb_{a}_share", f"QB {a.replace('_', ' ')}", _has("qb_alignment", also=_ftn),
                   _is("qb_alignment", a), "share", "ftn", first_season=f)
            for a in ("under_center", "shotgun", "pistol")
        ),
        Metric("blitz_rate", "Blitz per dropback (FTN: 1+ blitzer)", _has("blitz", also=lambda:
               _ftn() & _dropback()), _flag("blitz"), "share", "ftn", situational=True,
               first_season=f),
        Metric("rushers_avg", "Pass rushers per dropback", lambda: _ftn() & _dropback() &
               (_c("rushers") > 0).fill_null(False), lambda: _c("rushers").cast(pl.Float64),
               "mean", "ftn", first_season=f),
        Metric("box_avg", "Defenders in the box", _has("box", also=_ftn),
               lambda: _c("box").cast(pl.Float64), "mean", "ftn", first_season=f),
        Metric("light_box_rate", f"Light box ({LIGHT_BOX_MAX} or fewer)", _has("box", also=_ftn),
               lambda: (_c("box") <= LIGHT_BOX_MAX).cast(pl.Float64), "share", "ftn",
               first_season=f),
        Metric("heavy_box_rate", f"Heavy box ({HEAVY_BOX_MIN} or more)", _has("box", also=_ftn),
               lambda: (_c("box") >= HEAVY_BOX_MIN).cast(pl.Float64), "share", "ftn",
               first_season=f),
    ]  # fmt: skip


def _part_metrics() -> list[Metric]:
    h = {"source": "participation", "history_only": True}
    ms: list[Metric] = []
    for g in (*PERSONNEL_GROUPS, "other"):
        num = (
            _is("personnel", g)
            if g != "other"
            else (lambda: (~_c("personnel").is_in(list(PERSONNEL_GROUPS))).cast(pl.Float64))
        )
        ms.append(Metric(f"personnel_{g}", f"Personnel {g}", _has("personnel", also=_part), num,
                         "share", first_season=PART_FIRST, last_season=PART_LAST, **h))  # fmt: skip
    for f in dict.fromkeys((*FORMATIONS_NGS, *FORMATIONS_FTN)):
        first = PART_FIRST if f in FORMATIONS_NGS else NGS_ERA_LAST + 1
        last = PART_LAST if f in FORMATIONS_FTN else NGS_ERA_LAST
        ms.append(Metric(f"formation_{f}", f"Formation: {f.replace('_', ' ')}",
                         _has("formation", also=_part), _is("formation", f), "share",
                         first_season=first, last_season=last, **h))  # fmt: skip
    for p in DEF_PACKAGES:
        ms.append(Metric(f"def_{p}_share", f"Defense in {p}", _has("def_package", also=_part),
                         _is("def_package", p), "share", first_season=PART_FIRST,
                         last_season=PART_LAST, **h))  # fmt: skip
    era_ngs = ("prevent",)
    era_ftn = ("cover_9", "combo", "blown")
    for c in COVERAGES:
        first = NGS_ERA_LAST + 1 if c in era_ftn else COVERAGE_FIRST
        last = NGS_ERA_LAST if c in era_ngs else PART_LAST
        ms.append(Metric(f"cov_{c}", f"Coverage: {c.replace('_', ' ')}",
                         _has("coverage", also=lambda: _part() & _dropback()), _is("coverage", c),
                         "share", first_season=first, last_season=last, **h))  # fmt: skip
    ms.append(Metric("man_rate", "Man coverage (vs zone)",
                     _has("man_zone", also=lambda: _part() & _dropback()), _is("man_zone", "man"),
                     "share", first_season=COVERAGE_FIRST, last_season=PART_LAST, **h))  # fmt: skip
    for r in dict.fromkeys((*ROUTES_NGS, *ROUTES_FTN)):
        first = PART_FIRST if r in ROUTES_NGS else NGS_ERA_LAST + 1
        last = PART_LAST if r in ROUTES_FTN else NGS_ERA_LAST
        ms.append(Metric(f"route_{r}", f"Target's route: {r.replace('_', ' ')}",
                         _has("target_route", also=lambda: _part() & _attempt()),
                         _is("target_route", r), "share", first_season=first, last_season=last,
                         **h))  # fmt: skip
    ms.append(Metric("pressure_rate", "Pressure per dropback",
                     _has("pressure", also=lambda: _part() & _dropback()), _flag("pressure"),
                     "share", first_season=PART_FIRST, last_season=PART_LAST, **h))  # fmt: skip
    ms.append(Metric("time_to_throw_avg", "Time to throw (seconds)",
                     _has("time_to_throw", also=lambda: _part() & _dropback()),
                     lambda: _c("time_to_throw"), "mean", first_season=PART_FIRST,
                     last_season=PART_LAST, **h))  # fmt: skip
    return ms


METRICS: tuple[Metric, ...] = (*_pbp_metrics(), *_ftn_metrics(), *_part_metrics())
METRIC_BY_NAME = {m.name: m for m in METRICS}

# A team-game metric from PFR (not play level): blitzes the defense showed per opponent
# dropback, from the passers' `pfr_pass.times_blitzed`. FTN is the source; this is the
# cross-check (the two agree only r = 0.69 per team-game, P06).
PFR_BLITZ = Metric("blitz_rate_pfr", "Blitz per dropback (PFR cross-check)", lambda: pl.lit(True),
                   lambda: pl.lit(0.0), "share", "pfr", first_season=PFR_FIRST)  # fmt: skip

# The headline metrics and the situation they're read in (the pages' identity strip, the
# W&B league-trend chart, the guide's 2025 numbers).
HEADLINE: tuple[tuple[str, str], ...] = (
    ("proe", "neutral"),
    ("dropback_rate", "neutral"),
    ("shotgun_rate", "all"),
    ("no_huddle_rate", "all"),
    ("motion_rate", "all"),
    ("play_action_rate", "all"),
    ("screen_rate", "all"),
    ("rpo_rate", "all"),
    ("deep_shot_rate", "all"),
    ("adot", "all"),
    ("blitz_rate", "all"),
    ("rushers_avg", "all"),
    ("heavy_box_rate", "all"),
    ("explosive_rate", "all"),
)


def catalogue() -> list[dict]:
    """The metric list as plain data (build.json, the pages)."""
    return [
        {
            "name": m.name,
            "label": m.label,
            "unit": m.unit,
            "source": m.source,
            "situational": m.situational,
            "history_only": m.history_only,
            "first_season": m.first_season,
            "last_season": m.last_season,
        }
        for m in (*METRICS, PFR_BLITZ)
    ]


def definitions() -> dict:
    """The definitions as plain data (build.json, W&B config)."""
    return {
        "scrimmage": "play_type in (pass, run), no two-point tries (sacks + scrambles included)",
        "neutral": {
            "wp": list(NEUTRAL_WP),
            "max_down": NEUTRAL_MAX_DOWN,
            "not_last_seconds_of_half": TWO_MINUTE_SECONDS,
        },
        "dropback": "nflverse `pass` == 1 (pass attempts, sacks, scrambles)",
        "proe": "mean(dropback - xpass); nflverse's xpass is fit on 2006-2019 and not re-centred, "
        "so the league sits below 0 (2025 neutral: -2.0 points): read teams by `diff`",
        "deep_shot_air_yards": DEEP_SHOT_AIR_YARDS,
        "explosive": {"dropback_yards": EXPLOSIVE_PASS_YARDS, "run_yards": EXPLOSIVE_RUN_YARDS},
        "box": {"light_max": LIGHT_BOX_MAX, "heavy_min": HEAVY_BOX_MIN},
        "distance": {"short_max": SHORT_MAX, "medium_max": MEDIUM_MAX},
        "field_zone": {"red_zone_max": RED_ZONE_MAX, "own_deep_min": OWN_DEEP_MIN},
        "score_big_lead": BIG_LEAD,
        "blitz": "FTN n_blitzers > 0 on a dropback (PFR shown as a cross-check)",
        "screen": "FTN is_screen_pass on a dropback",
        "play_action": "FTN is_play_action on a dropback",
        "run_direction": "pbp run_location x run_gap (middle has no gap)",
        "recent_games": RECENT_GAMES,
        "availability": {
            "cutoff": "end of the Tuesday (US Eastern) after the previous week's last game, "
            "or week W's first kickoff if earlier",
            "ftn_lag_hours": FTN_LAG_HOURS,
            "pfr_lag_weeks": PFR_LAG_WEEKS,
            "pbp": "every game before week W",
        },
    }
