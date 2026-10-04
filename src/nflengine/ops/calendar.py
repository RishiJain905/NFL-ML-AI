"""The game calendar (P07; documentation/02 -> Weekly schedule): which week to run, whether
the previous week is complete, the deadline, and the week's special cases.

Everything is worked out from the schedule and a clock (`now`), never from fixed weekdays,
so the same code answers "what should Tuesday's run do?" live and, with `--as-of`, for any
moment in the past (time travel). Kickoff times in the schedule are US Eastern
(`gameday` + `gametime`); they are converted exactly as `curate/build.py` does.

Definitions:
- **target week**: the earliest week of the season that still has a game kicking off after
  `now`. On a Tuesday that's the next slate; on a Sunday afternoon it's the week being played
  (with `started_games` > 0 and the deadline passed).
- **deadline**: the first kickoff of the target week's slate (Thursday night, Thanksgiving
  noon, an international morning game in week 1, ...). The digest has to be out before it.
- **previous week complete**: every game of week N-1 has a result. Live, that comes from
  the freshest schedule snapshot (the `ready` step then also checks play-by-play). In a
  simulation (`data_lag_hours` given) a game only counts once `kickoff + data_lag_hours`
  has passed, which stands in for "final and in nflverse's play-by-play".
- **retry window**: "not ready" is retried until Wednesday 18:00 Eastern after the previous
  week's last game (documentation/02), or the week's first kickoff if that comes first;
  after that it's an alert.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

EASTERN = ZoneInfo("America/New_York")
PLAYOFF_TYPES = ("WC", "DIV", "CON", "SB")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# a season "owns" the clock from this long before its first kickoff (pre-season prep)
SEASON_LEAD = dt.timedelta(days=10)


def with_kickoffs(schedules: pl.DataFrame) -> pl.DataFrame:
    """Add `kickoff_utc` (tz-aware UTC) the way curation does, if it's missing."""
    if "kickoff_utc" in schedules.columns:
        return schedules
    return schedules.with_columns(
        pl.concat_str([pl.col("gameday"), pl.col("gametime").fill_null("13:00")], separator=" ")
        .str.to_datetime("%Y-%m-%d %H:%M", strict=False)
        .dt.replace_time_zone("America/New_York", ambiguous="earliest", non_existent="null")
        .dt.convert_time_zone("UTC")
        .alias("kickoff_utc")
    )


def load_schedules(paths=None) -> pl.DataFrame:
    """The newest raw schedules snapshot (works before curation runs), with kickoffs."""
    from nflengine.ingest.base import SnapshotStore
    from nflengine.paths import ensure_data_root

    paths = paths or ensure_data_root()
    s = SnapshotStore(paths.raw).read_latest("nflverse", "schedules")
    if s is None:
        raise FileNotFoundError("no schedules snapshot yet: run `nfl ingest` first")
    return with_kickoffs(s)


def to_eastern(t: dt.datetime) -> dt.datetime:
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.UTC)
    return t.astimezone(EASTERN)


def _utc(t: dt.datetime | None) -> dt.datetime:
    t = t or dt.datetime.now(dt.UTC)
    if t.tzinfo is None:  # naive = UTC, like every timestamp the pipeline writes
        t = t.replace(tzinfo=dt.UTC)
    return t.astimezone(dt.UTC).replace(microsecond=0)


def thanksgiving(year: int) -> dt.date:
    """Fourth Thursday of November."""
    first = dt.date(year, 11, 1)
    return first + dt.timedelta(days=(3 - first.weekday()) % 7 + 21)


@dataclass(frozen=True)
class Slate:
    """One week of games and what's unusual about it."""

    season: int
    week: int
    game_type: str  # REG | WC | DIV | CON | SB
    games: int
    first_kickoff: dt.datetime  # UTC
    last_kickoff: dt.datetime  # UTC
    days: tuple[str, ...]  # Eastern weekdays with games, in kickoff order (thu, sun, mon ...)
    teams_on_bye: tuple[str, ...] = ()
    special: tuple[str, ...] = ()  # see `slate()`
    neutral_sites: tuple[str, ...] = ()  # "AWAY@HOME" of neutral-site games (not the SB)
    international: tuple[str, ...] = ()  # ... of those, the ones played outside the US

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["first_kickoff"] = self.first_kickoff.isoformat()
        d["last_kickoff"] = self.last_kickoff.isoformat()
        return d


def slate(schedules: pl.DataFrame, season: int, week: int) -> Slate | None:
    """The week's games and special cases (None if the schedule has no such week yet).

    `special` tags: `thursday` (any Thursday game), `thanksgiving`, `black_friday`,
    `christmas`, `friday` / `saturday` / `midweek` (games on a Friday that isn't Black Friday,
    a Saturday, or a Tuesday / Wednesday), `monday_doubleheader`, `neutral_site` (a neutral
    site that isn't the Super Bowl, e.g. the 2024 wildfire-moved MIN@LA wild card in
    Glendale), `international` (a neutral site outside the US, from the stadium's time zone in
    `config/stadiums.yaml`; a stadium missing there counts as abroad when it kicks off before
    11:00 Eastern), `morning_kickoff` (before 11:00 Eastern), `byes`,
    `early_season` (weeks 1-3: ratings lean on last season), `final_week` (the last
    regular-season week: rested starters), `playoffs`.
    """
    s = with_kickoffs(schedules)
    wk = s.filter((pl.col("season") == season) & (pl.col("week") == week)).sort("kickoff_utc")
    if wk.is_empty():
        return None
    gtypes = wk["game_type"].unique().to_list()
    game_type = "REG" if "REG" in gtypes else str(gtypes[0])
    kicks = [_utc(k) for k in wk["kickoff_utc"].to_list()]
    local = [to_eastern(k) for k in kicks]
    days: list[str] = []
    for t in local:
        d = WEEKDAYS[t.weekday()]
        if d not in days:
            days.append(d)
    special: list[str] = []
    tg = thanksgiving(local[0].year if local[0].month >= 9 else local[0].year - 1)
    dates = {t.date() for t in local}
    if "thu" in days:
        special.append("thursday")
    if tg in dates:
        special.append("thanksgiving")
    if tg + dt.timedelta(days=1) in dates:
        special.append("black_friday")
    if any(d.month == 12 and d.day == 25 for d in dates):
        special.append("christmas")
    if any(t.weekday() == 4 and t.date() != tg + dt.timedelta(days=1) for t in local):
        special.append("friday")
    if "sat" in days:
        special.append("saturday")
    if any(t.weekday() in (1, 2) for t in local):
        special.append("midweek")
    if sum(t.weekday() == 0 for t in local) > 1:
        special.append("monday_doubleheader")
    neutral, abroad = _neutral_sites(wk, local)
    if neutral:
        special.append("neutral_site")
    if abroad:
        special.append("international")
    if any(t.hour < 11 for t in local):
        special.append("morning_kickoff")
    teams_on_bye: tuple[str, ...] = ()
    if game_type == "REG":
        reg = s.filter((pl.col("season") == season) & (pl.col("game_type") == "REG"))
        season_teams = set(reg["home_team"].to_list()) | set(reg["away_team"].to_list())
        playing = set(wk["home_team"].to_list()) | set(wk["away_team"].to_list())
        teams_on_bye = tuple(sorted(season_teams - playing))
        if teams_on_bye:
            special.append("byes")
        if week <= 3:
            special.append("early_season")
        last_reg = s.filter((pl.col("season") == season) & (pl.col("game_type") == "REG"))[
            "week"
        ].max()
        if week == last_reg:
            special.append("final_week")
    else:
        special.append("playoffs")
    return Slate(
        season=season,
        week=week,
        game_type=game_type,
        games=wk.height,
        first_kickoff=kicks[0],
        last_kickoff=kicks[-1],
        days=tuple(days),
        teams_on_bye=teams_on_bye,
        special=tuple(special),
        neutral_sites=tuple(neutral),
        international=tuple(abroad),
    )


# time zones of US venues (`config/stadiums.yaml` -> tz); anything else is abroad
US_ZONES = frozenset(
    {
        "America/New_York",
        "America/Detroit",
        "America/Indiana/Indianapolis",
        "America/Kentucky/Louisville",
        "America/Chicago",
        "America/Denver",
        "America/Phoenix",
        "America/Los_Angeles",
        "America/Anchorage",
        "Pacific/Honolulu",
    }
)


def _game_zones(game_ids: list[str]) -> dict[str, str]:
    """game_id -> IANA zone of the venue, from `config/stadiums.yaml`: the `game_venues`
    corrections first (nflverse lists some neutral-site games under the home team's stadium,
    e.g. 2025's Dublin game as Acrisure Stadium), then the stadium name."""
    try:
        from nflengine.features.venues import load_game_venues, load_venues

        venues, fixes = load_venues(), load_game_venues()
    except Exception:  # the calendar must work without the venue file
        return {}
    by_id = dict(zip(venues["stadium_id"], venues["tz"], strict=True))
    return {g: by_id[fixes[g]] for g in game_ids if fixes.get(g) in by_id}


def _name_zones() -> dict[str, str]:
    try:
        from nflengine.features.venues import load_venues

        venues = load_venues()
    except Exception:
        return {}
    zones: dict[str, str] = {}
    for r in venues.iter_rows(named=True):
        for name in [r["name"], *(r.get("aliases") or [])]:
            zones[str(name).strip().lower()] = r["tz"]
    return zones


def _neutral_sites(wk: pl.DataFrame, local: list[dt.datetime]) -> tuple[list[str], list[str]]:
    """(neutral-site games, the ones abroad) as "AWAY@HOME", Super Bowl excluded."""
    if "location" not in wk.columns:
        return [], []
    rows = [
        (r, t)
        for r, t in zip(wk.iter_rows(named=True), local, strict=True)
        if r["location"] == "Neutral" and r["game_type"] != "SB"
    ]
    if not rows:
        return [], []
    by_game = _game_zones([r["game_id"] for r, _ in rows]) if "game_id" in wk.columns else {}
    by_name = _name_zones() if "stadium" in wk.columns else {}
    neutral, abroad = [], []
    for r, t in rows:
        label = f"{r['away_team']}@{r['home_team']}"
        neutral.append(label)
        tz = by_game.get(r.get("game_id")) or by_name.get(str(r.get("stadium") or "").lower())
        if (tz is not None and tz not in US_ZONES) or (tz is None and t.hour < 11):
            abroad.append(label)
    return neutral, abroad


@dataclass(frozen=True)
class WeekPlan:
    """What a weekly run started at `now` should do."""

    now: dt.datetime  # UTC
    season: int
    phase: str  # preseason | regular | playoffs | offseason
    week: int | None  # the week to predict (None in the offseason)
    previous_week: int | None
    previous_games: int = 0
    previous_final: int = 0
    previous_missing: tuple[str, ...] = ()  # game ids of week N-1 not final yet
    deadline: dt.datetime | None = None  # first kickoff of the target week, UTC
    started_games: int = 0  # target-week games already kicked off at `now`
    retry_until: dt.datetime | None = None  # UTC
    slate: Slate | None = None
    simulated: bool = False  # readiness came from `kickoff + data_lag_hours` (time travel)
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def previous_complete(self) -> bool:
        return self.previous_week is None or (
            self.previous_games > 0 and self.previous_final == self.previous_games
        )

    @property
    def hours_to_deadline(self) -> float | None:
        if self.deadline is None:
            return None
        return round((self.deadline - self.now).total_seconds() / 3600, 2)

    @property
    def deadline_passed(self) -> bool:
        return self.deadline is not None and self.now >= self.deadline

    @property
    def past_retry_window(self) -> bool:
        return self.retry_until is not None and self.now > self.retry_until

    def describe(self) -> list[str]:
        """Plain lines for the console and the run summary."""
        now_et = to_eastern(self.now).strftime("%a %Y-%m-%d %H:%M ET")
        if self.week is None:
            return [f"{now_et}: {self.season} offseason, nothing to run", *self.notes]
        lines = [f"{now_et}: {self.season} week {self.week} ({self.phase})"]
        if self.previous_week is None:
            lines.append("previous week: none to wait for (week 1)")
        else:
            state = "complete" if self.previous_complete else "NOT complete"
            how = " (simulated: kickoff + data lag)" if self.simulated else ""
            lines.append(
                f"previous week {self.previous_week}: {self.previous_final}/"
                f"{self.previous_games} final, {state}{how}"
            )
        if self.deadline is not None:
            d = to_eastern(self.deadline).strftime("%a %Y-%m-%d %H:%M ET")
            hours = self.hours_to_deadline or 0.0
            when = f"{hours:.1f} h from now" if hours > 0 else f"passed {-hours:.1f} h ago"
            lines.append(f"deadline (first kickoff): {d}, {when}")
        if self.started_games:
            lines.append(f"{self.started_games} game(s) of week {self.week} already kicked off")
        if self.retry_until is not None and not self.previous_complete:
            r = to_eastern(self.retry_until).strftime("%a %Y-%m-%d %H:%M ET")
            lines.append(f"retry window for 'not ready' ends {r}")
        if self.slate is not None:
            lines.append(
                f"slate: {self.slate.games} games on {', '.join(self.slate.days)}"
                + (f"; special: {', '.join(self.slate.special)}" if self.slate.special else "")
            )
            if self.slate.teams_on_bye:
                lines.append(f"byes: {', '.join(self.slate.teams_on_bye)}")
            if self.slate.neutral_sites:
                abroad = set(self.slate.international)
                lines.append(
                    "neutral site: "
                    + ", ".join(
                        f"{g} (abroad)" if g in abroad else g for g in self.slate.neutral_sites
                    )
                )
        lines.extend(self.notes)
        return lines

    def as_dict(self) -> dict[str, Any]:
        return {
            "now": self.now.isoformat(),
            "season": self.season,
            "phase": self.phase,
            "week": self.week,
            "previous_week": self.previous_week,
            "previous_games": self.previous_games,
            "previous_final": self.previous_final,
            "previous_missing": list(self.previous_missing),
            "previous_complete": self.previous_complete,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "hours_to_deadline": self.hours_to_deadline,
            "deadline_passed": self.deadline_passed,
            "started_games": self.started_games,
            "retry_until": self.retry_until.isoformat() if self.retry_until else None,
            "past_retry_window": self.past_retry_window,
            "simulated": self.simulated,
            "slate": self.slate.as_dict() if self.slate else None,
            "notes": list(self.notes),
        }


def infer_season(schedules: pl.DataFrame, now: dt.datetime) -> int:
    """The season the clock is in: the latest season whose first kickoff is no more than
    `SEASON_LEAD` away (an 8-month offseason still belongs to the season that just ended)."""
    s = with_kickoffs(schedules)
    firsts = s.group_by("season").agg(pl.col("kickoff_utc").min().alias("first"))
    started = firsts.filter(pl.col("first") <= _utc(now) + SEASON_LEAD)
    if started.is_empty():
        return int(firsts["season"].min())
    return int(started["season"].max())


def retry_deadline(last_kickoff: dt.datetime) -> dt.datetime:
    """Wednesday 18:00 Eastern after the given game (documentation/02 retry window)."""
    t = to_eastern(last_kickoff)
    days = (2 - t.weekday()) % 7 or 7  # the next Wednesday, never the same day
    wed = (t + dt.timedelta(days=days)).date()
    return dt.datetime.combine(wed, dt.time(18, 0), tzinfo=EASTERN).astimezone(dt.UTC)


def plan_week(
    schedules: pl.DataFrame,
    now: dt.datetime | None = None,
    season: int | None = None,
    data_lag_hours: float | None = None,
) -> WeekPlan:
    """Work out the week to run at `now` (UTC; naive means UTC).

    `data_lag_hours` turns on time travel for readiness: a game counts as final only once
    `kickoff + data_lag_hours <= now` (and it has a result). Leave it None live, where the
    freshest snapshot's results are the truth.
    """
    now = _utc(now)
    s = with_kickoffs(schedules)
    season = season if season is not None else infer_season(s, now)
    games = s.filter(pl.col("season") == season)
    if games.is_empty():
        return WeekPlan(now, season, "offseason", None, None, notes=("no games scheduled",))
    upcoming = games.filter(pl.col("kickoff_utc") > now)
    reg = games.filter(pl.col("game_type") == "REG")
    if upcoming.is_empty():
        sb_played = games.filter(pl.col("game_type") == "SB")["result"].is_not_null().any()
        if sb_played or reg.is_empty():
            return WeekPlan(now, season, "offseason", None, None, notes=("season complete",))
        # the regular season (or a playoff round) is over but the next round's games
        # aren't in the schedule yet: wait for nflverse to add them
        week = int(games["week"].max()) + 1
        prev = _previous(games, week - 1, now, data_lag_hours)
        return WeekPlan(
            now,
            season,
            "playoffs",
            week,
            week - 1,
            *prev,
            retry_until=retry_deadline(_utc(games["kickoff_utc"].max())),
            simulated=data_lag_hours is not None,
            notes=("the next playoff round isn't in the schedule yet: retry later",),
        )
    return plan_for(s, season, int(upcoming["week"].min()), now, data_lag_hours)


def plan_for(
    schedules: pl.DataFrame,
    season: int,
    week: int,
    now: dt.datetime | None = None,
    data_lag_hours: float | None = None,
) -> WeekPlan:
    """The plan for a given week at `now` (a manual run of a week the calendar didn't pick,
    e.g. re-running last week: its own deadline and readiness, not the calendar's week's)."""
    now = _utc(now)
    s = with_kickoffs(schedules)
    games = s.filter(pl.col("season") == season)
    notes: list[str] = []
    sl = slate(s, season, week)
    if sl is None:
        prev = _previous(games, week - 1, now, data_lag_hours) if week > 1 else (0, 0, ())
        return WeekPlan(
            now,
            season,
            "playoffs" if week > 18 else "regular",
            week,
            week - 1 if week > 1 else None,
            *prev,
            simulated=data_lag_hours is not None,
            notes=(f"week {week} isn't in the schedule yet",),
        )
    phase = "playoffs" if sl.game_type != "REG" else "regular"
    if week == int(games["week"].min()) and now < sl.first_kickoff:
        phase = "preseason" if week == 1 else phase
    started = games.filter((pl.col("week") == week) & (pl.col("kickoff_utc") <= now)).height
    previous_week = week - 1 if week > int(games["week"].min()) else None
    prev: tuple[int, int, tuple[str, ...]] = (0, 0, ())
    retry_until = None
    if previous_week is not None:
        prev = _previous(games, previous_week, now, data_lag_hours)
        last_prev = games.filter(pl.col("week") == previous_week)["kickoff_utc"].max()
        # never later than the week's first kickoff (Christmas 2024: Wednesday 13:00 ET)
        retry_until = min(retry_deadline(_utc(last_prev)), sl.first_kickoff)
    if sl.first_kickoff <= now:
        notes.append(
            f"late run: week {week}'s first game kicked off already; games that started "
            "keep their earlier predictions (or get none)"
        )
    return WeekPlan(
        now,
        season,
        phase,
        week,
        previous_week,
        *prev,
        deadline=sl.first_kickoff,
        started_games=started,
        retry_until=retry_until,
        slate=sl,
        simulated=data_lag_hours is not None,
        notes=tuple(notes),
    )


def _previous(
    games: pl.DataFrame, week: int, now: dt.datetime, data_lag_hours: float | None
) -> tuple[int, int, tuple[str, ...]]:
    wk = games.filter(pl.col("week") == week)
    final = pl.col("result").is_not_null()
    if data_lag_hours is not None:
        final = final & (pl.col("kickoff_utc") + pl.duration(hours=data_lag_hours) <= now)
    done = wk.filter(final)
    missing = tuple(sorted(set(wk["game_id"].to_list()) - set(done["game_id"].to_list())))
    return wk.height, done.height, missing
