"""Payload builders (P04 tasks: meta, games, team_trends, news; the report card lives in
`report_card.py`, under-the-hood in `under_hood.py`, the watch list in `watchlist.py`).

Each builder takes plain frames and returns payload items, with every number formatted by
`digest/format.py`. They read nothing from disk, so tests feed them small frames.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import polars as pl

from nflengine.digest import format as F
from nflengine.digest.names import nickname
from nflengine.digest.payload import (
    Consensus,
    GameHighlights,
    GameItem,
    HighlightGame,
    NewsItem,
    PredictedScore,
    TrendDriver,
    TrendItem,
    UnderHoodItem,
    WatchItem,
)

CONSENSUS_NOTABLE = 0.05  # model-only vs market win-probability gap, in probability
CONSENSUS_LARGE = 0.10
TREND_WINDOW_WEEKS = 3
MAX_TRENDS_PER_SIDE = 2
FOLLOWED_BOOST = 1.15
PART_UNITS = {
    "pass_offense": "pass offense",
    "rush_offense": "run offense",
    "pass_defense": "pass defense",
    "rush_defense": "run defense",
}


# ---- games ------------------------------------------------------------------------------------


def _kickoff_display(ts: dt.datetime | None) -> str:
    """UTC kickoff -> "Sun 1:00 PM" (US Eastern), formatted inside Polars so Windows needs
    no tz database."""
    if ts is None:
        return "TBD"
    s = pl.Series([ts])
    if s.dtype.time_zone is None:  # type: ignore[union-attr]
        s = s.dt.replace_time_zone("UTC")
    text = s.dt.convert_time_zone("America/New_York").dt.strftime("%a %I:%M %p")[0]
    day, clock, ampm = text.split(" ")
    return f"{day} {clock.lstrip('0')} {ampm}"


def build_games(preds: pl.DataFrame, run_time: dt.datetime) -> list[GameItem]:
    """Game outlook items from the week's saved `predictions_games` (both variants).

    The digest row (`is_primary`) gives the shown numbers. `model_vs_consensus` compares
    the **model-only** row with the market's implied probability, so it measures what the
    football features alone say against consensus; it's only set when the gap is notable.
    """
    if preds.is_empty():
        return []
    primary = preds.filter(pl.col("is_primary")).sort("kickoff_utc", "game_id")
    mo = {
        r["game_id"]: r
        for r in preds.filter(pl.col("variant") == "model_only").iter_rows(named=True)
    }
    items = []
    for r in primary.iter_rows(named=True):
        home, away, p = r["home_team"], r["away_team"], float(r["home_win_prob"])
        ko = r["kickoff_utc"]
        started = ko is not None and ko <= run_time
        consensus = None
        m = mo.get(r["game_id"])
        if m and m["market_prob"] is not None and m["home_win_prob"] is not None:
            gap = float(m["home_win_prob"]) - float(m["market_prob"])
            if abs(gap) >= CONSENSUS_NOTABLE:
                team = home if gap > 0 else away
                size = "large" if abs(gap) >= CONSENSUS_LARGE else "notable"
                word = "much" if size == "large" else "notably"
                consensus = Consensus(
                    direction="higher_on_home" if gap > 0 else "higher_on_away",
                    size=size,
                    team=team,
                    team_name=nickname(team),
                    text=f"{word} higher on the {nickname(team)} than consensus",
                )
        items.append(
            GameItem(
                game_id=r["game_id"],
                kickoff=_kickoff_display(ko),
                status="started" if started else "upcoming",
                home=home,
                away=away,
                home_name=nickname(home),
                away_name=nickname(away),
                matchup=matchup(home, away, bool(r.get("neutral_site") or False)),
                neutral_site=bool(r.get("neutral_site") or False),
                home_win_prob=F.pct(p, cap=True),
                away_win_prob=F.pct(1 - p, cap=True),
                favored=home if p >= 0.5 else away,
                expected_margin=F.margin(float(r["expected_margin"]), home, away),
                predicted_score=PredictedScore(
                    home=F.points(float(r["pred_home_points"])),
                    away=F.points(float(r["pred_away_points"])),
                ),
                confidence=r.get("confidence_label") or "lean",
                model_vs_consensus=consensus,
                market_fallback=bool(r.get("market_fallback") or False),
                home_qb=r.get("home_qb_name"),
                away_qb=r.get("away_qb_name"),
            )
        )
    return items


def matchup(home: str, away: str, neutral: bool = False) -> str:
    """The one way to name a game: "Titans at Colts" (away at home)."""
    tail = " (neutral site)" if neutral else ""
    return f"{nickname(away)} at {nickname(home)}{tail}"


LOPSIDED_N = 3
CLOSEST_N = 2


def build_highlights(games: list[GameItem]) -> GameHighlights:
    """Code-made orderings of the upcoming games (D53): GLM ranked games itself and got the
    "closest" and "right behind" games wrong, so the order now comes from here."""
    upcoming = [g for g in games if g.status == "upcoming"]

    def fav(g: GameItem) -> tuple[str, float]:
        p = float(g.home_win_prob.value)
        return (g.home, p) if p >= 0.5 else (g.away, 1 - p)

    def item(g: GameItem, note: str) -> HighlightGame:
        team, p = fav(g)
        prob = g.home_win_prob if team == g.home else g.away_win_prob
        return HighlightGame(
            game_id=g.game_id,
            matchup=g.matchup,
            team=team,
            team_name=nickname(team),
            prob=prob,
            rank_note=note,
        )

    by_conf = sorted(upcoming, key=lambda g: (-fav(g)[1], g.game_id))
    by_close = sorted(upcoming, key=lambda g: (fav(g)[1], g.game_id))
    lop = [
        item(
            g,
            "the most lopsided call"
            if i == 0
            else f"the {F.ordinal_str(i + 1)}-most lopsided call",
        )
        for i, g in enumerate(by_conf[:LOPSIDED_N])
    ]
    close = [
        item(g, "the closest game" if i == 0 else f"the {F.ordinal_str(i + 1)}-closest game")
        for i, g in enumerate(by_close[:CLOSEST_N])
    ]
    return GameHighlights(most_lopsided=lop, closest=close)


# ---- team trends ------------------------------------------------------------------------------


POSITION_WORDS = {
    "T": "offensive tackle", "OT": "offensive tackle", "G": "guard", "OG": "guard",
    "C": "center", "OL": "offensive lineman", "DT": "defensive tackle", "NT": "nose tackle",
    "DE": "defensive end", "DL": "defensive lineman", "EDGE": "edge rusher",
    "LB": "linebacker", "ILB": "linebacker", "MLB": "linebacker", "OLB": "outside linebacker",
    "CB": "cornerback", "DB": "defensive back", "S": "safety", "FS": "safety", "SS": "safety",
    "K": "kicker", "P": "punter", "LS": "long snapper", "FB": "fullback",
    "QB": "quarterback", "RB": "running back", "HB": "running back", "WR": "wide receiver",
    "TE": "tight end",
}  # fmt: skip


def _people(names: str | None) -> list[tuple[str, str]]:
    """'Brock Bowers (TE), Joe Alt (T)' -> [('Brock Bowers', 'TE'), ('Joe Alt', 'T')]."""
    if not names:
        return []
    out = []
    for chunk in names.split(", "):
        if not chunk.strip():
            continue
        name, _, pos = chunk.partition(" (")
        out.append((name.strip(), pos.rstrip(")").strip()))
    return out


def _who(name: str, pos: str) -> str:
    """'Joe Alt', 'T' -> 'offensive tackle Joe Alt' (GLM read "tackles" as defense)."""
    word = POSITION_WORDS.get(pos.upper(), pos)
    return f"{word} {name}" if word else name


def _evidence(r: dict) -> tuple[list[str], list[str]]:
    ev: list[str] = []
    people: list[str] = []
    before = "last season" if r.get("before_source") == "last_season" else "earlier this season"
    now, was = r.get("qb_now_name"), r.get("qb_before_name")
    if r.get("qb_change") and now and was:
        # time-scoped on purpose: "has taken over from" read as a 2025 claim when the
        # comparison QB was last season's starter (who may have left the team)
        if r.get("before_source") == "last_season":
            ev.append(f"{now} started their latest game; last season's main starter was {was}")
        else:
            ev.append(f"{now} started their latest game in place of {was}")
        people += [now, was]
    out = _people(r.get("key_players_out_names"))
    if out:
        ev.append("recently without " + ", ".join(_who(n, pos) for n, pos in out))
        people += [n for n, _ in out]
    # every rate change says which way it moved and, for pressure, what that means: a bare
    # "+7.0 points" of pressure allowed read as good next to an "up" trend (P09 fact-check)
    dp = r.get("def_pressure_rate_delta")
    if dp is not None and abs(dp) >= 0.05:
        more = "more" if dp > 0 else "less"
        ev.append(
            f"pass rush's pressure rate {F.pct_points_change(100 * dp).display} vs {before} "
            f"({more} pressure on opposing QBs)"
        )
    op = r.get("off_pressure_rate_delta")
    if op is not None and abs(op) >= 0.05:
        more = "more" if op > 0 else "less"
        ev.append(
            f"pressure rate allowed by the offense {F.pct_points_change(100 * op).display} "
            f"vs {before} (its QB under {more} pressure)"
        )
    cp = r.get("off_cpoe_delta")
    if cp is not None and abs(cp) >= 4:
        ev.append(
            "passing offense's completion % over expected "
            f"{F.pct_points_change(cp).display} vs {before}"
        )
    return ev[:2], list(dict.fromkeys(p for p in people if p))


def build_trends(
    trends: pl.DataFrame,
    drivers: pl.DataFrame,
    followed: Sequence[str] = (),
    per_side: int = MAX_TRENDS_PER_SIDE,
) -> list[TrendItem]:
    """2-4 teams trending up or down (the as-of rows for the digest week).

    Ranked by |trend_delta| (followed teams x1.15), up to `per_side` risers and fallers.
    Trends are descriptive (D47).
    """
    t = trends.filter(pl.col("direction").is_in(["up", "down"]))
    if t.is_empty():
        return []
    boost = pl.when(pl.col("team").is_in(list(followed))).then(FOLLOWED_BOOST).otherwise(1.0)
    t = t.with_columns((pl.col("trend_delta").abs() * boost).alias("_score"))
    picked = pl.concat(
        [
            t.filter(pl.col("direction") == d)
            .sort(["_score", "team"], descending=[True, False])
            .head(per_side)
            for d in ("up", "down")
        ]
    ).sort(["_score", "team"], descending=[True, False])
    out = []
    for r in picked.iter_rows(named=True):
        d = drivers.filter(pl.col("team") == r["team"]).sort("rank") if drivers.height else drivers
        ds = [
            TrendDriver(
                unit=PART_UNITS.get(x["part"], x["part"]),
                change=F.driver_change(PART_UNITS.get(x["part"], x["part"]), x["delta"]),
                effect=x["effect"],
            )
            for x in d.head(2).iter_rows(named=True)
        ]
        ev, people = _evidence(r)
        out.append(
            TrendItem(
                team=r["team"],
                team_name=nickname(r["team"]),
                direction=r["direction"],
                trend_delta=F.epa_change(r["trend_delta"], TREND_WINDOW_WEEKS),
                window=F.weeks(TREND_WINDOW_WEEKS),
                net_rating=F.epa(r["net_epa"]),
                drivers=ds,
                evidence=ev,
                people=people,
            )
        )
    return out


# ---- under the hood ---------------------------------------------------------------------------


def build_under_hood(sel: pl.DataFrame) -> list[UnderHoodItem]:
    """Payload items from `under_hood.select_under_hood` rows."""
    out = []
    for r in sel.iter_rows(named=True):
        unit = r["unit"]
        has_norm = r.get("norm") is not None and r.get("kind") != "standout"
        n = int(r.get("norm_games") or 0)
        if not has_norm:
            norm_note = None
        elif r.get("norm_source") == "last_season":
            norm_note = "his average last season"
        elif r.get("norm_source") == "season+last_season":
            norm_note = "his average this season blended with last season"
        else:
            norm_note = f"his average over the previous {n} game{'s' if n != 1 else ''}"
        low = r.get("norm_source") in ("last_season", "season+last_season") or (has_norm and n < 2)
        out.append(
            UnderHoodItem(
                player=r["player"],
                player_id=r["player_id"],
                team=r["team"],
                team_name=nickname(r["team"]),
                position=r["position"] or "",
                metric=r["metric"],
                label=r["label"],
                kind=r["kind"],
                # counts carry their unit ("9 pressures", "1.5 pressures per game"): bare
                # "9" vs "1.5" made GLM add "in 1 game" of its own (P09 fact-check)
                last_week=(
                    F.count_stat(r["last_week"], r["label"])
                    if unit == "count"
                    else F.metric(r["last_week"], unit)
                ),
                season_avg=(
                    (
                        F.per_game(r["norm"], r["label"])
                        if unit == "count"
                        else F.metric(r["norm"], unit)
                    )
                    if has_norm
                    else None
                ),
                unit=unit,
                norm_note=norm_note,
                volume=F.count(r["volume"]),
                volume_label=r["volume_label"],
                rank_note=r["rank_note"],
                source=r["source"],
                confidence="low" if (has_norm and low) else "medium",
            )
        )
    return out


# ---- players to watch -------------------------------------------------------------------------

N_TEAMS = 32


SOFT_TIER = 8  # ranks 1-8 from either end are clearly soft / tough matchups


def defense_rank_note(rank: int, n: int = N_TEAMS) -> str:
    """Rank 1 = most EPA allowed -> "weakest"; past the middle, count from the strong end
    ("4th-strongest"), so the display says which end it counts from. The middle of the
    league is labelled as such: GLM called a 17th-of-32 defense "a tougher matchup"."""
    if rank <= n // 2:
        core = "league's weakest" if rank == 1 else f"{F.ordinal_str(rank)}-weakest"
    else:
        strong = n + 1 - rank
        core = "league's strongest" if strong == 1 else f"{F.ordinal_str(strong)}-strongest"
    if SOFT_TIER < rank <= n - SOFT_TIER:
        return f"about league-average ({core})"
    return core


def build_watch(sel: pl.DataFrame) -> list[WatchItem]:
    """Payload items from `watchlist.placeholder_watchlist` rows (heuristic, low confidence)."""
    out = []
    for r in sel.iter_rows(named=True):
        before = (
            "last season" if r["usage_before_source"] == "last_season" else ("earlier this season")
        )
        out.append(
            WatchItem(
                player=r["player"],
                player_id=r["player_id"],
                team=r["team"],
                team_name=nickname(r["team"]),
                position=r["position"],
                opponent=r["opponent"],
                opponent_name=nickname(r["opponent"]),
                game_id=r["game_id"],
                target=r["target"],
                baseline=F.amount(r["baseline"], r["target"]),
                usage_metric=r["usage_metric"],
                usage_recent=F.share(r["usage_recent"]),
                usage_before=F.share(r["usage_before"]),
                usage_before_note=before,
                opp_def_rank=F.text_num(
                    defense_rank_note(int(r["opp_def_rank"])), int(r["opp_def_rank"])
                ),
                opp_def_unit=r["opp_def_unit"],
                confidence=r.get("confidence") or "low",
                source=r.get("source") or "heuristic",
            )
        )
    return out


# ---- news -------------------------------------------------------------------------------------


def build_news(
    news: pl.DataFrame | None,
    run_time: dt.datetime,
    team_by_espn_id: dict[str, str],
    teams_in_payload: set[str],
    days: int = 7,
    limit: int = 5,
) -> list[NewsItem]:
    """Recent non-fantasy ESPN headlines about payload teams (fail-soft: [] on any gap)."""
    if news is None or news.is_empty():
        return []
    cutoff = (run_time - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = run_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = (
        news.filter(~pl.col("is_fantasy").fill_null(False))
        .filter((pl.col("published") >= cutoff) & (pl.col("published") <= now))
        .sort("published", descending=True)
    )
    out: list[NewsItem] = []
    for r in rows.iter_rows(named=True):
        teams = sorted(
            {team_by_espn_id[i] for i in (r.get("team_espn_ids") or []) if i in team_by_espn_id}
            & teams_in_payload
        )
        if not teams:
            continue
        out.append(
            NewsItem(
                headline=r["headline"],
                summary=r.get("description"),
                published=r["published"],
                entities=teams,
            )
        )
        if len(out) >= limit:
            break
    return out
