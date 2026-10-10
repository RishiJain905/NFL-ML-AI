"""Decision review (LD03): every 4th down of a finished week, the bot's call against the coach's.

Built from curated play-by-play (nflverse states, not ESPN: reproducible) and the promoted
live-decision models, read only (D107). For one season and week:

**Which 4th downs are decisions** (`decisions_frame`; one decision per 4th-down snap that the
coach chose a play for):
- a 4th down typed run, pass, field goal or punt (nflverse types a fake as the run or pass it
  was: a fake punt or fake field goal is a **go**);
- except a kick-formation snap that was **aborted** (`aborted_play == 1`: a fumbled snap in punt
  or field-goal formation is a botched kick, so the call was the kick);
- a play **wiped out by a penalty after the snap** (`no_play`, the play described before
  "PENALTY") counts when the down was **not replayed** (the next snap isn't the same team's 4th
  down): roughing the kicker, a defensive penalty on a go. The call is read from the text
  (" punts " -> punt, "field goal is" -> fg, else go; after a reversed review only the final ruling
  counts). When the down was replayed, the replayed snap is the decision;
- never a penalty **before** the snap (`live.replay.pre_snap_penalty`: false start, delay of
  game, encroachment ...), a kneel or a spike (LD01's rules, the phase file's pitfall);
- the state needs a spread and a total (every 2025-2026 row has them).

**What the bot says** (`score_decisions`): `Engine.fourth_downs(..., bootstrap=True)`: the win
probability after each option, the best, the gap and the confidence label (the bootstrap only
for calls closer than 5 points, D113). The coach's call against it:
- `edge` = WP of the coach's option - the best *other* option's WP. Positive = the coach picked
  the bot's best and gained `gap`; negative = the coach's call cost that much;
- `cost` = WP of the bot's best - WP of the coach's option (>= 0; 0 when they agree). Summed it's
  the expected wins given up against the bot;
- both null when the coach's option isn't priced (a field goal from beyond 70 yards).

**Highlights** (`highlights`): the **costliest** call (largest cost), the **five costliest**
and the **boldest** call: of the week's go-for-its that counted (not wiped out by a penalty) in
a game still in doubt (the bot's win chance before the snap between 10% and 90%) where kicking
was a real option (within 5 points of going: a desperate 4th & 13 down 6 at the end is forced,
not bold), the one with the lowest chance to convert.

**The season** (`season_tables`, weeks 1..`through`):
- the **coach leaderboard**: per head coach (nflverse's, `config/head_coach_fixes.csv` applied):
  4th downs, go rate, **go spots** (the bot says go and isn't calling it a toss-up: Confident or
  Lean) and how often he went there (the ranking), kick spots and how often he went anyway, the
  expected wins given up (all, by kicking in go spots, by going in kick spots), agreement;
- the bot's **calibration**: its win probability on every snap of the season's finished games
  against who won (10 bins, with nflfastR's `vegas_wp` on the same snaps), its conversion
  chances on 3rd downs and on attempted 4th downs against what happened (attempted 4th downs lean
  optimistic: coaches go when they expect to make it, so 3rd downs sit next to them), its field
  goal make chances against makes;
- the **weekly trend**: coaches' and the bot's go rates, agreement, wins given up.

The promoted bundle was fit on 2010-2025 (`meta.seasons`): a review of a season inside that
window is **in-sample** (`in_sample`), so its calibration flatters the bot; 2026 is the first
out-of-sample season.

**Files** (`paths.live_data` = `{NFL_DATA_ROOT}/live/`): `<S>/week<NN>/decision_review.parquet`
(one row per decision) + `decision_review.json` (the stamp and the meta);
`<S>/season_review.parquet` (the leaderboard through the newest reviewed week) +
`season_review.json` (the stamp, every season table). A stored file is used only while its
**stamp** matches: the review schema, the model version, a hash of the week's (or the season's)
curated play rows and of the coach fixes.
The CLI (`nfl live review`) writes these; the control room reads them and, for a week without a
valid file, computes it and keeps it in its own cache folder (`ReviewStore`), never here.

Never imports `nflengine.models`, `nflengine.features` or `nflengine.graph` (D107).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nflengine.live import data, schema
from nflengine.live.backtest import brier, ece, reliability
from nflengine.live.context import _apply_fixes, coach_fixes, default_fixes_path
from nflengine.live.replay import pre_snap_penalty
from nflengine.live.show import down_text
from nflengine.live.state import plain
from nflengine.paths import DataPaths

# 2: the Sol review's rule and text fixes (stored reviews from version 1 are rebuilt)
SCHEMA_VERSION = 2
OPTIONS = ("go", "fg", "punt")
NAMES = {"go": "Go for it", "fg": "Field goal", "punt": "Punt"}
CLEAR_LABELS = ("Confident", "Lean")  # a go / kick spot: the bot's call isn't a toss-up
COMPETITIVE = (0.10, 0.90)  # the boldest call: a game still in doubt
BOLD_MAX_EDGE = 0.05  # ... and a real choice: kicking within 5 points of going (not forced)
TOP_N = 5
CAL_BINS = 10
DESC_CAP = 300
CONV_MAX = 10  # conversion rows by distance 1..9 and "10+"
FG_BANDS = ("<30", "30-39", "40-49", "50+")
REVIEW_FILE = "decision_review"
SEASON_FILE = "season_review"

# play columns the review reads on top of `data.PLAY_COLUMNS`
EXTRA_COLUMNS = [
    "aborted_play", "fourth_down_converted", "home_coach", "away_coach", "posteam_score",
    "defteam_score",
]  # fmt: skip
# the columns a stamp hashes (what a review depends on): the plays and what `games` adds to them
# (a corrected neutral site changes the state's `home`; Sol review)
HASH_COLUMNS = [*data.PLAY_COLUMNS, *EXTRA_COLUMNS, "neutral_site", "kickoff_utc"]

# one decision row: the columns written to decision_review.parquet, in order
ROW_COLUMNS = [
    "season", "week", "game_id", "play_id", "i", "kickoff_utc", "posteam", "defteam", "home_team",
    "away_team", "coach", "qtr", "clock", "half_seconds", "game_seconds", "off_score", "def_score",
    "score_diff", "down", "ydstogo", "yardline_100", "situation", "choice", "fake", "aborted",
    "wiped", "best", "agree", "label", "gap", "boot_share", "wp_go", "wp_fg", "wp_punt", "wp_now",
    "edge", "cost", "convert", "fg_make", "fg_distance", "punt_start", "success", "result",
    "yards_gained", "desc",
]  # fmt: skip


# --- reading ------------------------------------------------------------------------------------
def _plays_file(paths: DataPaths, season: int) -> Path:
    return paths.curated / "plays" / f"season={season}.parquet"


def _file_stat(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def load_season(season: int, paths: DataPaths, fixes_path: Path | None = None) -> pl.DataFrame:
    """The season's plays (REG + POST) with the state columns (`data.with_state`), the review's
    extra columns, each play's offense `coach` (fixes applied) and the game's `kickoff_utc`.
    Empty (no rows) when the season has no curated plays."""
    path = _plays_file(paths, season)
    if not path.exists():
        return pl.DataFrame()
    plays = data.load_plays([season], paths)
    if plays.is_empty():
        return plays
    extra = pl.read_parquet(path, columns=["game_id", "play_id", *EXTRA_COLUMNS])
    plays = plays.join(extra, on=["game_id", "play_id"], how="left")
    games = paths.curated / "games.parquet"
    if games.exists():
        kick = pl.read_parquet(games, columns=["game_id", "kickoff_utc"])
        plays = plays.join(kick, on="game_id", how="left")
    else:
        plays = plays.with_columns(
            pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("kickoff_utc")
        )
    plays = data.with_state(plays)
    is_home = pl.col("posteam") == pl.col("home_team")
    coach = (
        pl.when(pl.col("posteam").is_null())
        .then(None)
        .when(is_home)
        .then(pl.col("home_coach"))
        .otherwise(pl.col("away_coach"))
    )
    plays = plays.with_columns(coach.alias("coach"))
    return _apply_fixes(plays, coach_fixes(fixes_path), "posteam")


def week_hash(plays: pl.DataFrame, weeks: list[int]) -> str:
    """A hash of the play rows of `weeks` (the columns a review depends on): two reviews built
    from the same rows hash alike. Stable within one Polars version (a new version: rebuilds)."""
    cols = [c for c in HASH_COLUMNS if c in plays.columns]
    part = (
        plays.filter(pl.col("week").is_in(weeks)).select(cols).sort("game_id", "play_id")
        if plays.height
        else pl.DataFrame()
    )
    h = hashlib.sha256(f"{part.height}|{','.join(cols)}|".encode())
    if part.height:
        h.update(part.hash_rows(seed=7, seed_1=11, seed_2=13, seed_3=17).to_numpy().tobytes())
    return h.hexdigest()[:24]


def fixes_hash(fixes_path: Path | None = None) -> str | None:
    path = fixes_path or default_fixes_path()
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


# --- which 4th downs are decisions ------------------------------------------------------------
def _formation() -> pl.Expr:
    d = pl.col("desc").str.to_lowercase()
    return (
        pl.when(d.str.contains("(punt formation)", literal=True))
        .then(pl.lit("punt"))
        .when(d.str.contains("(field goal formation)", literal=True))
        .then(pl.lit("fg"))
    )


def _pre_snap_expr() -> pl.Expr:
    """A `no_play` row whose penalty came before the snap (LD01's `pre_snap_penalty`)."""
    return (pl.col("play_type") == "no_play") & pl.col("desc").fill_null("").map_elements(
        pre_snap_penalty, return_dtype=pl.Boolean
    )


def _attempt_expr() -> pl.Expr:
    """A 3rd / 4th-down row of `data.gain_frame` that was a real attempt to convert: not a
    penalty before the snap (a neutral-zone first down), not a kick wiped out by a penalty
    (roughing the kicker), not a fumbled kick-formation snap (Sol review). The gain model trains
    on all of them; the calibration counts only what the offense tried."""
    is_np = pl.col("play_type") == "no_play"
    went = pl.col("desc").fill_null("").map_elements(text_choice, return_dtype=pl.String) == "go"
    aborted_kick = _formation().is_not_null() & (pl.col("aborted_play").fill_null(0) == 1)
    return pl.when(is_np).then(~_pre_snap_expr() & went).otherwise(~aborted_kick)


def _same_half_expr() -> pl.Expr:
    """The next snap is in the same half as this one (overtime counts as one half)."""
    a, b = pl.col("qtr"), pl.col("next_qtr")
    return ((a == b) | ((a == 1) & (b == 2)) | ((a == 3) & (b == 4))).fill_null(False)


def text_choice(desc: str | None) -> str:
    """The call a wiped-out 4th down shows in its text: punt, fg or go (after a reversed review,
    only the final ruling's text counts)."""
    x = f" {(desc or '').lower()} "
    if "reversed" in x:
        x = " " + x.rsplit("reversed", 1)[1]
    if " punts " in x:
        return "punt"
    if "field goal is" in x:  # LD01's rule: "(Field Goal formation)" alone is a fake's text
        return "fg"
    return "go"


def decisions_frame(plays: pl.DataFrame) -> pl.DataFrame:
    """The 4th downs the coach decided (module docstring) with `choice` (go / fg / punt), the
    flags `fake`, `aborted`, `wiped`, and the next snap (`next_pos`, `next_down`, `next_yl`)."""
    if plays.is_empty():
        return plays
    # the next snap that was run: a penalty before the snap (false start, neutral zone ...)
    # isn't one, or a play wiped out after the snap would look "replayed" by it (Sol review)
    snaps = plays.filter(
        pl.col("down").is_not_null() & pl.col("play_type").is_in(list(data.SNAP_TYPES))
    )
    snaps = snaps.filter(~_pre_snap_expr()).sort("game_id", "i")
    snaps = snaps.select(
        "game_id",
        "i",
        pl.col("posteam").shift(-1).over("game_id").alias("next_pos"),
        pl.col("down").shift(-1).over("game_id").alias("next_down"),
        pl.col("yardline_100").shift(-1).over("game_id").alias("next_yl"),
        pl.col("qtr").shift(-1).over("game_id").alias("next_qtr"),
    )
    base = plays.filter(
        (pl.col("down") == 4)
        & pl.col("posteam").is_not_null()
        & pl.col("ydstogo").is_between(1, 99)
        & pl.col("yardline_100").is_between(1, 99)
        & pl.col("spread_line").is_not_null()
        & pl.col("total_line").is_not_null()
        & pl.col("play_type").is_in(["run", "pass", "field_goal", "punt", "no_play"])
    ).join(snaps, on=["game_id", "i"], how="left")
    if base.is_empty():
        return base.with_columns(
            pl.lit(None, dtype=pl.String).alias("choice"),
            pl.lit(False).alias("fake"),
            pl.lit(False).alias("aborted"),
            pl.lit(False).alias("wiped"),
        )
    base = base.with_columns(_same_half_expr().alias("next_same_half"))
    replayed = (
        (pl.col("next_down") == 4)
        & (pl.col("next_pos") == pl.col("posteam"))
        & pl.col("next_same_half")
    ).fill_null(False)
    base = base.with_columns(
        _pre_snap_expr().alias("_pre"),
        replayed.alias("_replayed"),
        _formation().alias("_form"),
    )
    typed = pl.col("play_type").is_in(["run", "pass", "field_goal", "punt"])
    wiped = (pl.col("play_type") == "no_play") & ~pl.col("_pre") & ~pl.col("_replayed")
    df = base.filter(typed | wiped)
    aborted = (
        pl.col("play_type").is_in(["run", "pass"])
        & pl.col("_form").is_not_null()
        & (pl.col("aborted_play").fill_null(0) == 1)
    )
    df = df.with_columns(
        (pl.col("play_type") == "no_play").alias("wiped"),
        aborted.alias("aborted"),
        (
            pl.col("play_type").is_in(["run", "pass"]) & pl.col("_form").is_not_null() & ~aborted
        ).alias("fake"),
        pl.col("desc")
        .fill_null("")
        .map_elements(text_choice, return_dtype=pl.String)
        .alias("_txt"),
    )
    choice = (
        pl.when(pl.col("play_type") == "field_goal")
        .then(pl.lit("fg"))
        .when(pl.col("play_type") == "punt")
        .then(pl.lit("punt"))
        .when(pl.col("aborted"))
        .then(pl.col("_form"))
        .when(pl.col("wiped"))
        .then(pl.col("_txt"))
        .otherwise(pl.lit("go"))
    )
    return df.with_columns(choice.alias("choice")).drop("_pre", "_replayed", "_form", "_txt")


# --- what happened ------------------------------------------------------------------------------
def _clock(qtr: int, half_seconds: float | None) -> str | None:
    """The quarter clock "mm:ss" from the half clock (in overtime both are the OT clock)."""
    if half_seconds is None or (isinstance(half_seconds, float) and math.isnan(half_seconds)):
        return None
    s = float(half_seconds)
    if qtr in (1, 3):
        s -= 900.0
    s = max(0, int(round(s)))
    return f"{s // 60}:{s % 60:02d}"


def cap_text(text: str | None, limit: int = DESC_CAP) -> str:
    """nflverse's play text, at most `limit` characters: a longer one is cut at a word and ends
    with an ellipsis (a reversed review's text can run past 600)."""
    t = plain(text, limit=10**6) or ""  # no control characters, one line (as ESPN text)
    if len(t) <= limit:
        return t
    cut = t[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.")
    return cut + "…"


def _i(x: Any) -> int | None:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return int(round(float(x)))


def outcome(row: dict) -> tuple[bool | None, str]:
    """(success, a short text) for one decision row: success = a go converted, a field goal
    made; None for a punt."""
    choice, team, opp = row["choice"], row["posteam"], row["defteam"]
    # the next snap tells a punt's or a wiped play's result only within the same half: after
    # a punt that ends the half it's the other half's first snap (Sol review)
    same_half = row.get("next_same_half", True) is not False
    nxt = row.get("next_pos") if same_half else None
    ndown = _i(row.get("next_down")) if same_half else None
    next_yl = row.get("next_yl") if same_half else None
    first_by_penalty = row.get("wiped") and nxt == team and ndown == 1
    fake = ""
    if row.get("fake"):
        fg_form = "(field goal formation)" in (row.get("desc") or "").lower()
        fake = "Fake field goal: " if fg_form else "Fake punt: "
    if row.get("wiped"):
        if first_by_penalty:
            return (True if choice == "go" else None), "Wiped out by a penalty: first down"
        if nxt == opp:
            return (False if choice == "go" else None), f"Wiped out by a penalty: {opp} ball"
        return None, "Wiped out by a penalty"
    off_td = bool(row.get("touchdown") == 1 and row.get("td_team") == team)
    def_td = bool(row.get("touchdown") == 1 and row.get("td_team") == opp)
    if choice == "go":
        gained = _i(row.get("yards_gained"))
        if off_td:
            return True, f"{fake}Touchdown"
        if row.get("fourth_down_converted") == 1:
            return (
                True,
                f"{fake}Converted ({gained:+d} yds)" if gained is not None else f"{fake}Converted",
            )
        if row.get("interception") == 1:
            return False, f"{fake}Intercepted" + (" (returned for a TD)" if def_td else "")
        if row.get("fumble_lost") == 1:
            return False, f"{fake}Fumble lost" + (" (returned for a TD)" if def_td else "")
        if row.get("safety") == 1:
            return False, f"{fake}Safety"
        if row.get("aborted"):
            return False, "Botched snap"
        need = _i(row.get("ydstogo"))
        if gained is None:
            return False, f"{fake}Stopped"
        return False, f"{fake}Stopped ({gained:+d} yds, needed {need})"
    if choice == "fg":
        dist = _i(row.get("kick_distance")) or _i(
            (row.get("yardline_100") or 0) + schema.FG_SNAP_YARDS
        )
        res = row.get("field_goal_result")
        if row.get("aborted"):
            return False, "Botched snap"
        if res == "made":
            return True, f"Good from {dist}"
        if res == "blocked":
            return False, f"Blocked from {dist}" + (" (returned for a TD)" if def_td else "")
        return False, f"Missed from {dist}"
    # punt
    if row.get("aborted"):
        return None, "Botched punt snap"
    if row.get("punt_blocked") == 1:
        return None, "Punt blocked" + (" (returned for a TD)" if def_td else "")
    if def_td:
        return None, "Punt returned for a TD"
    if nxt == opp and next_yl is not None:
        yl = _i(next_yl)  # yards to the kicking team's goal, from the receiver's side
        if yl > 50:
            return None, f"{opp} ball at own {100 - yl}"
        if yl == 50:
            return None, f"{opp} ball at midfield"
        return None, f"{opp} ball at {team} {yl}"  # a long return or a penalty
    if nxt == team:
        return None, "Punt: kicking team kept it"
    return None, "Punt"


# --- the bot's call -----------------------------------------------------------------------------
def score_decisions(
    frame: pl.DataFrame, engine: Any, bootstrap: bool = True
) -> tuple[pl.DataFrame, int]:
    """Each decision through the engine; returns (rows with `ROW_COLUMNS`, how many 4th downs
    the state rules refused)."""
    if frame.is_empty():
        return _empty_rows(), 0
    rows = frame.to_dicts()
    states, kept = [], []
    for i, r in enumerate(rows):
        try:
            states.append(data.state_of_row(r))
            kept.append(i)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            continue  # a state the input checks refuse (a clock that disagrees with itself ...)
    try:
        decisions = engine.fourth_downs(states, bootstrap=bootstrap) if states else []
    except Exception:  # noqa: BLE001 - one state the engine refuses must not sink the week
        decisions, ok = [], []
        for i, s in zip(kept, states, strict=True):
            try:
                decisions.append(engine.fourth_downs([s], bootstrap=bootstrap)[0])
                ok.append(i)
            except Exception:  # noqa: BLE001
                continue
        kept = ok
    out = []
    for i, d in zip(kept, decisions, strict=True):
        r = rows[i]
        wp = {k: d.wp.get(k) for k in OPTIONS}
        mine = wp.get(r["choice"])
        others = [v for k, v in wp.items() if k != r["choice"] and v is not None]
        edge = None if mine is None or not others else mine - max(others)
        cost = None if mine is None else max(0.0, wp[d.best] - mine)
        success, text = outcome(r)
        qtr = _i(r.get("qtr")) or 0
        out.append(
            {
                "season": _i(r["season"]),
                "week": _i(r["week"]),
                "game_id": r["game_id"],
                "play_id": _i(r["play_id"]),
                "i": _i(r["i"]),
                "kickoff_utc": r.get("kickoff_utc"),
                "posteam": r["posteam"],
                "defteam": r["defteam"],
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "coach": plain(r.get("coach")),
                "qtr": qtr,
                "clock": _clock(qtr, r.get("half_seconds")),
                "half_seconds": float(r["half_seconds"]),
                "game_seconds": float(r["game_seconds"]),
                "off_score": _i(r.get("posteam_score")),
                "def_score": _i(r.get("defteam_score")),
                "score_diff": _i(r["score_diff"]),
                "down": 4,
                "ydstogo": _i(r["ydstogo"]),
                "yardline_100": _i(r["yardline_100"]),
                "situation": down_text(
                    4, _i(r["ydstogo"]), _i(r["yardline_100"]), r["posteam"], r["defteam"]
                ),
                "choice": r["choice"],
                "fake": bool(r.get("fake")),
                "aborted": bool(r.get("aborted")),
                "wiped": bool(r.get("wiped")),
                "best": d.best,
                "agree": d.best == r["choice"],
                "label": d.label,
                "gap": float(d.gap),
                "boot_share": d.boot_share,
                "wp_go": wp["go"],
                "wp_fg": wp["fg"],
                "wp_punt": wp["punt"],
                "wp_now": float(d.wp_now),
                "edge": edge,
                "cost": cost,
                "convert": float(d.convert),
                "fg_make": d.fg_make,
                "fg_distance": float(d.fg_distance),
                "punt_start": d.punt_start,
                "success": success,
                "result": text,
                "yards_gained": _i(r.get("yards_gained")),
                "desc": cap_text(r.get("desc")),
            }
        )
    df = pl.DataFrame(out, schema=_ROW_SCHEMA) if out else _empty_rows()
    return df.sort("kickoff_utc", "game_id", "i", nulls_last=True), len(rows) - len(kept)


_ROW_SCHEMA: dict[str, Any] = {
    "season": pl.Int32, "week": pl.Int32, "game_id": pl.String, "play_id": pl.Int64,
    "i": pl.Int64, "kickoff_utc": pl.Datetime("us", "UTC"), "posteam": pl.String,
    "defteam": pl.String, "home_team": pl.String, "away_team": pl.String, "coach": pl.String,
    "qtr": pl.Int32, "clock": pl.String, "half_seconds": pl.Float64, "game_seconds": pl.Float64,
    "off_score": pl.Int32, "def_score": pl.Int32, "score_diff": pl.Int32, "down": pl.Int32,
    "ydstogo": pl.Int32, "yardline_100": pl.Int32, "situation": pl.String, "choice": pl.String,
    "fake": pl.Boolean, "aborted": pl.Boolean, "wiped": pl.Boolean, "best": pl.String,
    "agree": pl.Boolean, "label": pl.String, "gap": pl.Float64, "boot_share": pl.Float64,
    "wp_go": pl.Float64, "wp_fg": pl.Float64, "wp_punt": pl.Float64, "wp_now": pl.Float64,
    "edge": pl.Float64, "cost": pl.Float64, "convert": pl.Float64, "fg_make": pl.Float64,
    "fg_distance": pl.Float64, "punt_start": pl.Float64, "success": pl.Boolean,
    "result": pl.String, "yards_gained": pl.Int32, "desc": pl.String,
}  # fmt: skip


def _empty_rows() -> pl.DataFrame:
    return pl.DataFrame(schema=_ROW_SCHEMA)


# --- one week -------------------------------------------------------------------------------------
@dataclass
class WeekReview:
    """One week's decisions (`rows`, `ROW_COLUMNS`) and what they were built from (`meta`)."""

    rows: pl.DataFrame
    meta: dict[str, Any]
    source: str = "computed"  # computed | file | cache


def in_sample(season: int, trained: list[int] | None) -> bool:
    return bool(trained) and season in set(trained or [])


def build_week(
    season: int,
    week: int,
    plays: pl.DataFrame,
    engine: Any,
    *,
    model_version: str | None,
    trained_seasons: list[int] | None,
    fixes: str | None,
    now: datetime | None = None,
) -> WeekReview:
    """Score one week's decisions (`plays` = `load_season`'s frame for the season)."""
    week_plays = plays.filter(pl.col("week") == week) if plays.height else plays
    frame = decisions_frame(week_plays)
    rows, skipped = score_decisions(frame, engine)
    meta = {
        "schema": SCHEMA_VERSION,
        "season": season,
        "week": week,
        "stamp": {
            "schema": SCHEMA_VERSION,
            "model_version": model_version,
            "data": week_hash(plays, [week]),
            "fixes": fixes,
        },
        "model_version": model_version,
        "in_sample": in_sample(season, trained_seasons),
        "trained_seasons": [min(trained_seasons), max(trained_seasons)]
        if trained_seasons
        else None,
        "games": int(week_plays["game_id"].n_unique()) if week_plays.height else 0,
        "decisions": rows.height,
        "skipped": skipped,
        "computed_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds"),
    }
    return WeekReview(rows, meta)


def _r(x: Any, digits: int = 4) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(v) else round(v, digits)


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def week_summary(rows: pl.DataFrame) -> dict[str, Any]:
    """Counts for the week's header: decisions, the coaches' and the bot's calls, agreement,
    toss-ups, go spots and the wins given up."""
    n = rows.height

    def count(col: str, v: str) -> int:
        return int((rows[col] == v).sum()) if n else 0

    go_spot = (rows["best"] == "go") & rows["label"].is_in(CLEAR_LABELS) if n else None
    return {
        "decisions": n,
        "coach": {k: count("choice", k) for k in OPTIONS},
        "bot": {k: count("best", k) for k in OPTIONS},
        "agree": int(rows["agree"].sum()) if n else 0,
        "toss_ups": count("label", "Toss-up"),
        "go_spots": int(go_spot.sum()) if n else 0,
        "went_in_go_spots": int((go_spot & (rows["choice"] == "go")).sum()) if n else 0,
        "wp_lost": _r(rows["cost"].sum() if n else 0.0),
        "fakes": int(rows["fake"].sum()) if n else 0,
        "wiped": int(rows["wiped"].sum()) if n else 0,
    }


def highlights(rows: pl.DataFrame) -> dict[str, Any]:
    """The boldest call, the costliest and the five costliest (play keys: game_id, play_id)."""
    key = ["game_id", "play_id"]
    costly = rows.filter(pl.col("cost").is_not_null() & (pl.col("cost") > 0)).sort(
        ["cost", "game_id", "play_id"], descending=[True, False, False]
    )
    lo, hi = COMPETITIVE
    bold = rows.filter(
        (pl.col("choice") == "go")
        & ~pl.col("wiped")
        & pl.col("wp_now").is_between(lo, hi)
        & (pl.col("edge") < BOLD_MAX_EDGE)
    ).sort(["convert", "ydstogo", "game_id", "play_id"], descending=[False, True, False, False])
    return {
        "costliest": costly.select(key).row(0, named=True) if costly.height else None,
        "top": costly.head(TOP_N).select(key).to_dicts(),
        "boldest": bold.select(key).row(0, named=True) if bold.height else None,
    }


# --- the season ---------------------------------------------------------------------------------
def leaderboard(rows: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, Any]]:
    """One row per head coach (decisions with a known coach), ranked by his go rate in go spots
    (more go spots first on a tie, then the name); and the league's row."""
    clear = pl.col("label").is_in(CLEAR_LABELS)
    go_spot = (pl.col("best") == "go") & clear
    kick_spot = (pl.col("best") != "go") & clear
    went = pl.col("choice") == "go"
    aggs = [
        pl.len().alias("decisions"),
        went.sum().alias("go"),
        go_spot.sum().alias("go_spots"),
        (go_spot & went).sum().alias("went_in_go_spots"),
        kick_spot.sum().alias("kick_spots"),
        (kick_spot & went).sum().alias("went_in_kick_spots"),
        pl.col("agree").sum().alias("agree"),
        pl.col("cost").fill_null(0.0).sum().alias("wp_lost"),
        # timid / bold: only the spots the bot is clear about (a toss-up's cost is neither)
        pl.when(~went & go_spot)
        .then(pl.col("cost"))
        .otherwise(0.0)
        .fill_null(0.0)
        .sum()
        .alias("wp_lost_timid"),
        pl.when(went & kick_spot)
        .then(pl.col("cost"))
        .otherwise(0.0)
        .fill_null(0.0)
        .sum()
        .alias("wp_lost_bold"),
        pl.col("week").n_unique().alias("weeks"),
    ]

    def rates(df: pl.DataFrame) -> pl.DataFrame:
        def r(num: str, den: str) -> pl.Expr:
            return pl.when(pl.col(den) > 0).then((pl.col(num) / pl.col(den)).round(4))

        return df.with_columns(
            r("go", "decisions").alias("go_rate"),
            r("went_in_go_spots", "go_spots").alias("go_rate_spots"),
            r("went_in_kick_spots", "kick_spots").alias("go_rate_kick_spots"),
            r("agree", "decisions").alias("agree_rate"),
            *(pl.col(c).round(4) for c in ("wp_lost", "wp_lost_timid", "wp_lost_bold")),
        )

    known = rows.filter(pl.col("coach").is_not_null())
    if known.is_empty():
        board = pl.DataFrame()
    else:
        teams = (
            known.group_by("coach", "posteam")
            .agg(pl.len().alias("n"), pl.col("week").max().alias("last"))
            .sort(["coach", "last", "n"], descending=[False, True, True])
            .group_by("coach", maintain_order=True)
            .agg(pl.col("posteam").alias("teams"))
        )
        board = rates(known.group_by("coach").agg(aggs)).join(teams, on="coach", how="left")
        board = board.sort(
            ["go_rate_spots", "go_spots", "coach"],
            descending=[True, True, False],
            nulls_last=True,
        ).with_row_index("rank", offset=1)
    league_df = rates(rows.select(aggs)) if rows.height else pl.DataFrame()
    league = league_df.row(0, named=True) if league_df.height else {}
    return board, {k: (_r(v) if isinstance(v, float) else v) for k, v in league.items()}


def trend(rows: pl.DataFrame) -> list[dict[str, Any]]:
    out = []
    for (wk,), part in rows.group_by(["week"], maintain_order=True):
        n = part.height
        go4 = part.filter(
            (pl.col("choice") == "go") & ~pl.col("wiped") & pl.col("success").is_not_null()
        )
        out.append(
            {
                "week": int(wk),
                "decisions": n,
                "go_rate_coach": _rate(int((part["choice"] == "go").sum()), n),
                "go_rate_bot": _rate(int((part["best"] == "go").sum()), n),
                "agree_rate": _rate(int(part["agree"].sum()), n),
                "wp_lost": _r(part["cost"].sum()),
                "attempts": go4.height,
                "conv_actual": _r(go4["success"].cast(pl.Float64).mean()) if go4.height else None,
                "conv_pred": _r(go4["convert"].mean()) if go4.height else None,
            }
        )
    return sorted(out, key=lambda d: d["week"])


def _bins(p: np.ndarray, y: np.ndarray) -> list[dict[str, Any]]:
    r = reliability(p, y, CAL_BINS)
    return [
        {
            "bin_lo": _r(b["bin_lo"]),
            "n": int(b["n"]),
            "mean_pred": _r(b["mean_pred"]),
            "mean_outcome": _r(b["mean_outcome"]),
        }
        for b in r.iter_rows(named=True)
    ]


def calibration(plays: pl.DataFrame, models: Any) -> dict[str, Any]:
    """The bot's chances against what happened on these plays (weeks already chosen)."""
    from nflengine.live import models as LM

    out: dict[str, Any] = {"wp": None, "conversion": None, "fg": None}
    if plays.is_empty():
        return out
    wpf, ties = data.wp_frame(plays)
    wpf = wpf.filter(pl.col("vegas_wp").is_not_null())
    if wpf.height:
        p = models.wp.predict(LM.matrix(wpf, schema.WP_FEATURES))
        y = wpf["win"].to_numpy().astype(float)
        v = wpf["vegas_wp"].to_numpy()
        out["wp"] = {
            "snaps": wpf.height,
            "games": int(wpf["game_id"].n_unique()),
            "tie_games": int(ties["tie_games"]),
            "brier": _r(brier(p, y)),
            "brier_vegas": _r(brier(v, y)),
            "ece": _r(ece(p, y, CAL_BINS)),
            "ece_vegas": _r(ece(v, y, CAL_BINS)),
            "model": _bins(p, y),
            "vegas": _bins(v, y),
        }
    g = data.gain_frame(plays)
    if g.height:
        g = g.filter(_attempt_expr())
    if g.height:
        probs = LM.legal_gain(
            models.gain.predict(LM.matrix(g, models.gain_features)), g["yardline_100"].to_numpy()
        )
        conv = LM.conversion_prob(probs, g["ydstogo"].to_numpy())
        g = g.with_columns(
            pl.Series("pred", conv, dtype=pl.Float64),
            pl.col("ydstogo").clip(1, CONV_MAX).cast(pl.Int32).alias("dist"),
        )
        by = (
            g.group_by("down", "dist")
            .agg(
                pl.len().alias("n"),
                pl.col("converted").cast(pl.Float64).mean().alias("actual"),
                pl.col("pred").mean().alias("pred"),
            )
            .sort("down", "dist")
        )
        overall = []
        for d in (3, 4):
            part = g.filter(pl.col("down") == d)
            y, pp = part["converted"].to_numpy().astype(float), part["pred"].to_numpy()
            overall.append(
                {
                    "down": d,
                    "n": part.height,
                    "actual": _r(float(np.mean(y))) if part.height else None,
                    "pred": _r(float(np.mean(pp))) if part.height else None,
                    "brier": _r(brier(pp, y)) if part.height else None,
                }
            )
        out["conversion"] = {
            "rows": [
                {
                    "down": int(r["down"]),
                    "distance": int(r["dist"]),
                    "label": f"{CONV_MAX}+" if r["dist"] == CONV_MAX else str(int(r["dist"])),
                    "n": int(r["n"]),
                    "actual": _r(r["actual"]),
                    "pred": _r(r["pred"]),
                }
                for r in by.iter_rows(named=True)
            ],
            "overall": overall,
        }
    fg = data.fg_frame(plays)
    if fg.height:
        p = models.fg.predict(
            fg["distance"].to_numpy(),
            fg["era"].to_numpy(),
            fg["indoor"].to_numpy(),
            fg["wind_out"].to_list(),
            fg["temp_out"].to_list(),
        )
        fg = fg.with_columns(
            pl.Series("pred", p, dtype=pl.Float64),
            pl.when(pl.col("distance") < 30)
            .then(pl.lit(FG_BANDS[0]))
            .when(pl.col("distance") < 40)
            .then(pl.lit(FG_BANDS[1]))
            .when(pl.col("distance") < 50)
            .then(pl.lit(FG_BANDS[2]))
            .otherwise(pl.lit(FG_BANDS[3]))
            .alias("band"),
        )
        rows = []
        for band in FG_BANDS:
            part = fg.filter(pl.col("band") == band)
            if part.height:
                rows.append(
                    {
                        "band": band,
                        "n": part.height,
                        "made": _r(part["make"].cast(pl.Float64).mean()),
                        "pred": _r(part["pred"].mean()),
                    }
                )
        out["fg"] = {
            "n": fg.height,
            "made": _r(fg["make"].cast(pl.Float64).mean()),
            "pred": _r(fg["pred"].mean()),
            "rows": rows,
        }
    return out


def season_tables(
    season: int,
    through: int,
    rows: pl.DataFrame,
    plays: pl.DataFrame,
    models: Any,
    *,
    model_version: str | None,
    trained_seasons: list[int] | None,
    fixes: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Every season table through week `through` (`rows` = the weeks' decisions)."""
    rows = rows.filter(pl.col("week") <= through) if rows.height else rows
    weeks = sorted(rows["week"].unique().to_list()) if rows.height else []
    played = plays.filter(pl.col("week") <= through) if plays.height else plays
    board, league = leaderboard(rows)
    return {
        "schema": SCHEMA_VERSION,
        "season": season,
        "through_week": through,
        "weeks": weeks,
        "stamp": {
            "schema": SCHEMA_VERSION,
            "model_version": model_version,
            "data": week_hash(plays, list(range(1, through + 1))),
            "fixes": fixes,
        },
        "model_version": model_version,
        "in_sample": in_sample(season, trained_seasons),
        "trained_seasons": [min(trained_seasons), max(trained_seasons)]
        if trained_seasons
        else None,
        "decisions": rows.height,
        "leaderboard": [_plain_row(r) for r in board.iter_rows(named=True)] if board.height else [],
        "league": league,
        "trend": trend(rows) if rows.height else [],
        "calibration": calibration(played, models),
        "computed_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds"),
    }


def _plain_row(r: dict) -> dict:
    return {k: (_r(v) if isinstance(v, float) else v) for k, v in r.items()}


# --- the answers the app shows ------------------------------------------------------------------
def _play(r: dict) -> dict[str, Any]:
    return {
        "game_id": r["game_id"],
        "play_id": r["play_id"],
        "posteam": r["posteam"],
        "defteam": r["defteam"],
        "coach": r["coach"],
        "qtr": r["qtr"],
        "clock": r["clock"],
        "off_score": r["off_score"],
        "def_score": r["def_score"],
        "situation": r["situation"],
        "ydstogo": r["ydstogo"],
        "yardline_100": r["yardline_100"],
        "choice": r["choice"],
        "best": r["best"],
        "agree": r["agree"],
        "label": r["label"],
        "gap": _r(r["gap"]),
        "wp": {"go": _r(r["wp_go"]), "fg": _r(r["wp_fg"]), "punt": _r(r["wp_punt"])},
        "wp_now": _r(r["wp_now"]),
        "edge": _r(r["edge"]),
        "cost": _r(r["cost"]),
        "convert": _r(r["convert"]),
        "fg_make": _r(r["fg_make"]),
        "fg_distance": _r(r["fg_distance"], 1),
        "fake": r["fake"],
        "wiped": r["wiped"],
        "success": r["success"],
        "result": r["result"],
        "desc": r["desc"],
    }


def week_payload(rv: WeekReview, games: pl.DataFrame | None = None) -> dict[str, Any]:
    """The review endpoint's body (without the status fields the app adds)."""
    rows = rv.rows
    plays = [_play(r) for r in rows.iter_rows(named=True)]
    by_key = {(p["game_id"], p["play_id"]): p for p in plays}
    hl = highlights(rows)

    def pick(k: dict | None) -> dict | None:
        return None if k is None else by_key.get((k["game_id"], k["play_id"]))

    out_games = []
    if games is not None and games.height:
        for g in games.sort("kickoff_utc", "game_id", nulls_last=True).iter_rows(named=True):
            part = rows.filter(pl.col("game_id") == g["game_id"])
            lost = {
                t: _r(part.filter(pl.col("posteam") == t)["cost"].sum()) if part.height else 0.0
                for t in (g["away_team"], g["home_team"])
            }
            out_games.append(
                {
                    "game_id": g["game_id"],
                    "away": g["away_team"],
                    "home": g["home_team"],
                    "away_score": _i(g.get("away_score")),
                    "home_score": _i(g.get("home_score")),
                    "kickoff": g["kickoff_utc"].isoformat()
                    if isinstance(g.get("kickoff_utc"), datetime)
                    else None,
                    "decisions": part.height,
                    "wp_lost": lost,
                }
            )
    return {
        "model": {
            "version": rv.meta.get("model_version"),
            "in_sample": rv.meta.get("in_sample"),
            "trained_seasons": rv.meta.get("trained_seasons"),
        },
        "computed_at": rv.meta.get("computed_at"),
        "source": rv.source,
        "summary": {**week_summary(rows), "skipped": rv.meta.get("skipped", 0)},
        "highlights": {
            "boldest": pick(hl["boldest"]),
            "costliest": pick(hl["costliest"]),
            "top": [p for p in (pick(k) for k in hl["top"]) if p is not None],
        },
        "games": out_games,
        "plays": plays,
    }


def season_payload(tables: dict[str, Any], source: str = "computed") -> dict[str, Any]:
    keep = (
        "through_week", "weeks", "decisions", "leaderboard", "league", "trend", "calibration",
        "computed_at",
    )  # fmt: skip
    return {
        "model": {
            "version": tables.get("model_version"),
            "in_sample": tables.get("in_sample"),
            "trained_seasons": tables.get("trained_seasons"),
        },
        "source": source,
        **{k: tables.get(k) for k in keep},
    }


def week_games(paths: DataPaths, season: int, week: int) -> pl.DataFrame:
    """The week's games from curated `games` (teams, final scores, kickoff), read only."""
    path = paths.curated / "games.parquet"
    cols = ["game_id", "season", "week", "away_team", "home_team", "away_score", "home_score",
            "kickoff_utc"]  # fmt: skip
    if not path.exists():
        return pl.DataFrame()
    return pl.read_parquet(path, columns=cols, memory_map=False).filter(
        (pl.col("season") == season) & (pl.col("week") == week)
    )


# --- files ----------------------------------------------------------------------------------------
def week_dir(paths: DataPaths, season: int, week: int) -> Path:
    return paths.live_data / str(season) / f"week{week:02d}"


def season_dir(paths: DataPaths, season: int) -> Path:
    return paths.live_data / str(season)


def _atomic_write(path: Path, write: Callable[[Path], None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, np.generic):
        return o.item()
    return str(o)


_WRITE_LOCKS: dict[str, threading.Lock] = {}
_WRITE_GUARD = threading.Lock()


def _folder_lock(folder: Path) -> threading.Lock:
    """One writer at a time per destination folder (two background writes of one week)."""
    with _WRITE_GUARD:
        return _WRITE_LOCKS.setdefault(str(Path(folder).resolve()), threading.Lock())


def rows_hash(rows: pl.DataFrame) -> str:
    """Binds a week's parquet to its JSON: the JSON carries the rows' hash and a reader checks
    it, so a pair from two different writes (or a crash between them) reads as a miss."""
    h = hashlib.sha256(f"{rows.height}|{','.join(rows.columns)}|".encode())
    if rows.height:
        h.update(rows.hash_rows(seed=7, seed_1=11, seed_2=13, seed_3=17).to_numpy().tobytes())
    return h.hexdigest()[:24]


def write_week(folder: Path, rv: WeekReview) -> list[Path]:
    """`decision_review.parquet` + `.json` in `folder` (each atomic; the JSON names the rows'
    hash, written last)."""
    pq, js = folder / f"{REVIEW_FILE}.parquet", folder / f"{REVIEW_FILE}.json"
    meta = {**rv.meta, "rows_hash": rows_hash(rv.rows)}
    text = json.dumps(meta, indent=1, default=_json_default, ensure_ascii=False)
    with _folder_lock(folder):
        _atomic_write(pq, lambda p: rv.rows.write_parquet(p))
        _atomic_write(js, lambda p: p.write_text(text, encoding="utf-8"))
    return [pq, js]


def read_week(folder: Path, season: int, week: int, want: dict) -> WeekReview | None:
    """A stored week whose stamp equals `want`; None for a missing, corrupt or stale one."""
    pq, js = folder / f"{REVIEW_FILE}.parquet", folder / f"{REVIEW_FILE}.json"
    try:
        meta = json.loads(js.read_text(encoding="utf-8"))
        if (
            not isinstance(meta, dict)
            or meta.get("schema") != SCHEMA_VERSION
            or meta.get("season") != season
            or meta.get("week") != week
            or meta.get("stamp") != want
        ):
            return None
        rows = pl.read_parquet(pq, memory_map=False)
    except (OSError, ValueError, pl.exceptions.PolarsError):
        return None
    if rows.columns != ROW_COLUMNS or meta.get("rows_hash") != rows_hash(rows):
        return None
    return WeekReview(rows, meta, "file")


def write_season(folder: Path, tables: dict[str, Any]) -> list[Path]:
    """`season_review.parquet` (the leaderboard) + `season_review.json` (every table)."""
    pq, js = folder / f"{SEASON_FILE}.parquet", folder / f"{SEASON_FILE}.json"
    board = pl.DataFrame(tables["leaderboard"]) if tables["leaderboard"] else pl.DataFrame()
    text = json.dumps(tables, indent=1, default=_json_default, ensure_ascii=False)
    with _folder_lock(folder):  # readers use the JSON only; the parquet is the export
        _atomic_write(pq, lambda p: board.write_parquet(p))
        _atomic_write(js, lambda p: p.write_text(text, encoding="utf-8"))
    return [pq, js]


def read_season(folder: Path, season: int, through: int, want: dict) -> dict | None:
    js = folder / f"{SEASON_FILE}.json"
    try:
        doc = json.loads(js.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(doc, dict)
        or doc.get("schema") != SCHEMA_VERSION
        or doc.get("season") != season
        or doc.get("through_week") != through
        or doc.get("stamp") != want
        or not isinstance(doc.get("leaderboard"), list)
    ):
        return None
    return doc


# --- the store ------------------------------------------------------------------------------------
EngineFactory = Callable[[], tuple[Any, str]]


@dataclass
class _Season:
    """One season's loaded plays and the hashes computed from them (one generation)."""

    key: tuple  # (plays file stat, games file stat, coach-fixes hash): what `plays` was built from
    plays: pl.DataFrame
    hashes: dict[tuple[int, ...], str] = field(default_factory=dict)


def _hold(engine: Any) -> Any:
    """The app's shared-engine lock (`_LockedEngine.hold`) around work that calls the models
    directly (the calibration), so it never overlaps a Game day check; a no-op otherwise."""
    hold = getattr(engine, "hold", None)
    return hold() if callable(hold) else contextlib.nullcontext()


class ReviewStore:
    """Week reviews and season tables: memory, then the official files under `live/<S>/`, then
    (the app) its own cache folder, then a build. `official=True` (the CLI) writes the official
    files; the app passes `cache_folder` and never writes outside it. A stored answer counts
    only while its stamp matches (module docstring). One build at a time per key."""

    def __init__(
        self,
        *,
        cache_folder: Path | None = None,
        official: bool = False,
        fixes_path: Path | None = None,
        trained_seasons: Callable[[Any], list[int] | None] | None = None,
        background_writes: bool = False,
    ):
        self._cache = Path(cache_folder) if cache_folder is not None else None
        self._official = official
        # the app's cache files are written on a daemon thread: small writes on the data drive
        # took 2-3 s each (15 s on its first touch), and a response needn't wait for them
        self._background = background_writes and not official
        self._writers: list[threading.Thread] = []
        self._fixes_path = fixes_path
        self._trained = trained_seasons or _trained_of
        self._guard = threading.Lock()
        self._locks: dict[tuple, threading.Lock] = {}
        self._seasons: dict[int, _Season] = {}
        self._weeks: dict[tuple[int, int], WeekReview] = {}
        self._tables: dict[tuple[int, int], dict] = {}

    # -- plays and stamps
    def _season_key(self, paths: DataPaths, season: int) -> tuple:
        # the coach fixes and the games file feed the frame too: a change to either reloads it
        # (otherwise a rebuild would keep the old coach or neutral site; Sol review)
        return (
            _file_stat(_plays_file(paths, season)),
            _file_stat(paths.curated / "games.parquet"),
            fixes_hash(self._fixes_path),
        )

    def _entry(self, paths: DataPaths, season: int) -> _Season:
        key = self._season_key(paths, season)
        with self._guard:
            hit = self._seasons.get(season)
            if hit is not None and hit.key == key:
                return hit
        plays = (
            load_season(season, paths, self._fixes_path) if key[0] is not None else pl.DataFrame()
        )
        entry = _Season(key, plays)
        with self._guard:
            self._seasons[season] = entry
            for old in sorted(self._seasons)[:-2]:  # keep two seasons in memory
                del self._seasons[old]
        return entry

    def plays(self, paths: DataPaths, season: int) -> pl.DataFrame:
        return self._entry(paths, season).plays

    def weeks(self, paths: DataPaths, season: int) -> list[int]:
        """Weeks with curated plays in this season."""
        plays = self.plays(paths, season)
        return sorted(plays["week"].unique().to_list()) if plays.height else []

    def _hash(self, paths: DataPaths, season: int, weeks: tuple[int, ...]) -> str:
        entry = self._entry(paths, season)
        with self._guard:
            if weeks in entry.hashes:
                return entry.hashes[weeks]
        h = week_hash(entry.plays, list(weeks))
        with self._guard:  # kept with the generation it was computed from, never a newer one
            entry.hashes[weeks] = h
        return h

    def week_stamp(self, paths: DataPaths, season: int, week: int, version: str | None) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "model_version": version,
            "data": self._hash(paths, season, (week,)),
            "fixes": fixes_hash(self._fixes_path),
        }

    def season_stamp(
        self, paths: DataPaths, season: int, through: int, version: str | None
    ) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "model_version": version,
            "data": self._hash(paths, season, tuple(range(1, through + 1))),
            "fixes": fixes_hash(self._fixes_path),
        }

    def _lock(self, key: tuple) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _write(self, fn: Callable[[Path, Any], Any], folder: Path, obj: Any) -> None:
        """The CLI's official files: written now, and a failure is an error (`nfl live review`
        must not report files it didn't write; Sol review). The app's cache: best effort, on a
        daemon thread when `background_writes`."""
        if self._official:
            fn(folder, obj)
            return

        def job() -> None:
            with contextlib.suppress(OSError):
                fn(folder, obj)

        if not self._background:
            job()
            return
        t = threading.Thread(target=job, daemon=True, name="live-review-write")
        with self._guard:
            self._writers = [w for w in self._writers if w.is_alive()] + [t]
        t.start()

    def flush(self, timeout: float | None = None) -> None:
        """Wait for background writes (tests, a clean shutdown)."""
        with self._guard:
            writers = list(self._writers)
        for w in writers:
            w.join(timeout)

    def _cache_week_dir(self, season: int, week: int) -> Path | None:
        return None if self._cache is None else self._cache / f"{season}-w{week:02d}"

    def _cache_season_dir(self, season: int, through: int) -> Path | None:
        return None if self._cache is None else self._cache / f"{season}-season-w{through:02d}"

    # -- one week
    def cached_week(
        self, paths: DataPaths, season: int, week: int, version: str | None
    ) -> WeekReview | None:
        want = self.week_stamp(paths, season, week, version)
        with self._guard:
            hit = self._weeks.get((season, week))
        if hit is not None and hit.meta.get("stamp") == want:
            # built earlier in this process: say so (a stored file keeps its own source)
            return replace(hit, source="memory") if hit.source == "computed" else hit
        for folder in (week_dir(paths, season, week), self._cache_week_dir(season, week)):
            if folder is None:
                continue
            found = read_week(folder, season, week, want)
            if found is not None:
                if folder != week_dir(paths, season, week):
                    found.source = "cache"
                with self._guard:
                    self._weeks[(season, week)] = found
                return found
        return None

    def week(
        self,
        paths: DataPaths,
        season: int,
        week: int,
        engine_factory: EngineFactory,
        version: str | None,
    ) -> WeekReview:
        """The week's review (cached, stored or built). `version`: the bundle the caller
        expects (the pointer's), so a stored answer can be checked without loading it."""
        hit = self.cached_week(paths, season, week, version)
        if hit is not None:
            return hit
        with self._lock(("week", season, week)):
            hit = self.cached_week(paths, season, week, version)
            if hit is not None:
                return hit
            engine, built_with = engine_factory()
            plays = self.plays(paths, season)
            rv = build_week(
                season, week, plays, engine,
                model_version=built_with,
                trained_seasons=self._trained(engine),
                fixes=fixes_hash(self._fixes_path),
            )  # fmt: skip
            folder = (
                week_dir(paths, season, week)
                if self._official
                else self._cache_week_dir(season, week)
            )
            if folder is not None:  # a file that can't be written is a miss next time
                self._write(write_week, folder, rv)
            with self._guard:
                self._weeks[(season, week)] = rv
            return rv

    # -- the season
    def season(
        self,
        paths: DataPaths,
        season: int,
        through: int,
        engine_factory: EngineFactory,
        version: str | None,
    ) -> tuple[dict, str]:
        """(the season tables through `through`, where they came from)."""
        want = self.season_stamp(paths, season, through, version)
        key = (season, through)
        with self._guard:
            hit = self._tables.get(key)
        if hit is not None and hit.get("stamp") == want:
            return hit, "memory"
        with self._lock(("season", season, through)):
            with self._guard:  # built while we waited for the lock
                hit = self._tables.get(key)
            if hit is not None and hit.get("stamp") == want:
                return hit, "memory"
            for folder, src in (
                (season_dir(paths, season), "file"),
                (self._cache_season_dir(season, through), "cache"),
            ):
                if folder is None:
                    continue
                found = read_season(folder, season, through, want)
                if found is not None:
                    with self._guard:
                        self._tables[key] = found
                    return found, src
            # one engine and its version for the whole build: every week is checked (or built)
            # against that version, so a season never mixes two bundles (Sol review)
            engine, built_with = engine_factory()

            def pinned() -> tuple[Any, str]:
                return engine, built_with

            have = [w for w in self.weeks(paths, season) if w <= through]
            reviews = [self.week(paths, season, w, pinned, built_with) for w in have]
            rows = (
                pl.concat([r.rows for r in reviews], how="vertical_relaxed")
                if reviews
                else _empty_rows()
            )
            with _hold(engine):  # the calibration calls the models directly
                tables = season_tables(
                    season, through, rows, self.plays(paths, season), _models_of(engine),
                    model_version=built_with,
                    trained_seasons=self._trained(engine),
                    fixes=fixes_hash(self._fixes_path),
                )  # fmt: skip
            if self._official:
                # the newest-through rule, checked and written under one lock per season
                with self._lock(("season-file", season)):
                    newest = _stored_through(season_dir(paths, season))
                    if newest is None or through >= newest:
                        self._write(write_season, season_dir(paths, season), tables)
            else:
                folder = self._cache_season_dir(season, through)
                if folder is not None:
                    self._write(write_season, folder, tables)
            with self._guard:
                self._tables[key] = tables
            return tables, "computed"


# --- the command (`nfl live review`) and its W&B run ---------------------------------------------
DEFINITIONS = {
    "decision": "a 4th down typed run / pass / field goal / punt (a fake = go, an aborted kick-"
    "formation snap = the kick), or a play wiped out by a penalty after the snap when the down "
    "wasn't replayed; never a pre-snap penalty, a kneel or a spike",
    "edge": "WP of the coach's option minus the best other option's",
    "cost": "WP of the bot's best option minus the coach's (>= 0); summed = expected wins",
    "go_spot": "the bot says go and labels it Confident or Lean",
    "boldest": "the go-for-it (not wiped out by a penalty) with the lowest convert chance while "
    f"the bot's WP before the snap is within {COMPETITIVE[0]:.0%}-{COMPETITIVE[1]:.0%} and "
    f"kicking was within {BOLD_MAX_EDGE * 100:.0f} points of going (edge < {BOLD_MAX_EDGE})",
    "calibration": f"{CAL_BINS} equal bins; WP on every snap of finished games vs who won",
}


def run_review(
    season: int,
    weeks: list[int],
    *,
    use_wandb: bool = False,
    launched_by: str | None = None,
    log: Callable[[str], None] = print,
    paths: DataPaths | None = None,
    engine_factory: EngineFactory | None = None,
    smoke: bool = False,
) -> list[dict[str, Any]]:
    """Review each week (official files under `live/<S>/`) and the season through it; with
    `use_wandb` one `decision-review` run per week. Weeks without curated plays are skipped."""
    from nflengine.paths import ensure_data_root

    paths = paths or ensure_data_root()
    if engine_factory is None:
        engine_factory = _production_engine(paths)
    engine, version = engine_factory()
    store = ReviewStore(official=True)
    have = set(store.weeks(paths, season))
    out: list[dict[str, Any]] = []
    for w in sorted(set(weeks)):
        if w not in have:
            log(f"week {w}: no curated plays for {season} week {w} yet: skipped")
            continue
        rv = store.week(paths, season, w, lambda: (engine, version), version)
        tables, _ = store.season(paths, season, w, lambda: (engine, version), version)
        payload = week_payload(rv, week_games(paths, season, w))
        s = payload["summary"]
        line = (
            f"week {w}: {s['decisions']} decisions ({rv.source}); coaches go "
            f"{s['coach']['go']}, the bot {s['bot']['go']}; agree {s['agree']}; toss-ups "
            f"{s['toss_ups']}; wins given up {s['wp_lost']:.2f}"
        )
        entry: dict[str, Any] = {"week": w, "payload": payload, "tables": tables,
                                 "source": rv.source, "wandb": None}  # fmt: skip
        if use_wandb:
            entry["wandb"] = log_wandb(
                season,
                w,
                rv,
                tables,
                payload,
                launched_by=launched_by,
                paths=paths,
                smoke=smoke,
            )
            line += f"; W&B {entry['wandb']['url']}"
        log(line)
        out.append(entry)
    return out


def _production_engine(paths: DataPaths) -> EngineFactory:
    from nflengine.live import models as LM
    from nflengine.live import train as LT
    from nflengine.live.decide import Engine, decide_settings

    loaded: list[tuple[Any, str]] = []

    def factory() -> tuple[Any, str]:
        if not loaded:
            folder = LT.production_folder(paths)
            loaded.append((Engine(LM.load(folder), decide_settings()), folder.name))
        return loaded[0]

    return factory


def _scalar_metrics(payload: dict, tables: dict) -> dict[str, float]:
    s, lg = payload["summary"], tables.get("league") or {}
    n = s["decisions"]
    out: dict[str, Any] = {
        "week/decisions": n,
        "week/go_rate_coach": _rate(s["coach"]["go"], n),
        "week/go_rate_bot": _rate(s["bot"]["go"], n),
        "week/agree_rate": _rate(s["agree"], n),
        "week/toss_ups": s["toss_ups"],
        "week/go_spots": s["go_spots"],
        "week/went_in_go_spots": s["went_in_go_spots"],
        "week/wp_lost": s["wp_lost"],
        "week/fakes": s["fakes"],
        "week/wiped": s["wiped"],
        "week/skipped": s["skipped"],
        "season/decisions": tables.get("decisions"),
        "season/go_rate_coach": lg.get("go_rate"),
        "season/go_rate_spots": lg.get("go_rate_spots"),
        "season/agree_rate": lg.get("agree_rate"),
        "season/wp_lost": lg.get("wp_lost"),
    }
    cal = tables.get("calibration") or {}
    wp = cal.get("wp") or {}
    for k in ("brier", "brier_vegas", "ece", "ece_vegas", "snaps", "games"):
        out[f"season/wp_{k}"] = wp.get(k)
    for row in (cal.get("conversion") or {}).get("overall", []):
        out[f"season/conv{row['down']}_actual"] = row["actual"]
        out[f"season/conv{row['down']}_pred"] = row["pred"]
        out[f"season/conv{row['down']}_n"] = row["n"]
    fg = cal.get("fg") or {}
    out["season/fg_made"], out["season/fg_pred"] = fg.get("made"), fg.get("pred")
    hl = payload.get("highlights") or {}
    if hl.get("costliest"):
        out["week/costliest_cost"] = hl["costliest"]["cost"]
    if hl.get("boldest"):
        out["week/boldest_convert"] = hl["boldest"]["convert"]
    return {k: v for k, v in out.items() if isinstance(v, int | float) and math.isfinite(v)}


WANDB_PLAY_COLUMNS = [
    "game_id", "posteam", "coach", "qtr", "clock", "off_score", "def_score", "situation",
    "choice", "best", "label", "edge", "cost", "convert", "result",
]  # fmt: skip


def _play_table(plays: list[dict]) -> pl.DataFrame:
    rows = [
        {
            **{c: p.get(c) for c in WANDB_PLAY_COLUMNS},
            "wp_go": p["wp"]["go"],
            "wp_fg": p["wp"]["fg"],
            "wp_punt": p["wp"]["punt"],
        }
        for p in plays
    ]
    return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()


def log_wandb(
    season: int,
    week: int,
    rv: WeekReview,
    tables: dict[str, Any],
    payload: dict[str, Any],
    *,
    launched_by: str | None,
    paths: DataPaths,
    smoke: bool = False,
) -> dict[str, Any]:
    """One `decision-review` run: the week's table, the five costliest, the season's leaderboard,
    calibration charts and weekly trend; scalars as `week/*` and `season/*`."""
    import wandb

    from nflengine import tracking

    in_s = bool(rv.meta.get("in_sample"))
    run = tracking.init_run(
        group="live-decisions-smoke" if smoke else "live-decisions",
        job_type="decision-review",
        config={
            "season": season,
            "week": week,
            "through_week": tables.get("through_week"),
            "model": "live-decision-models",
            "model_version": rv.meta.get("model_version"),
            "in_sample": in_s,
            "trained_seasons": rv.meta.get("trained_seasons"),
            "review_schema": SCHEMA_VERSION,
            "definitions": DEFINITIONS,
            "dataset_version": tracking.dataset_version(paths),
            "git_commit": tracking.git_commit(),
        },
        tags=[
            "ld03",
            f"season:{season}",
            f"week:{week:02d}",
            *(["in-sample"] if in_s else []),
            *(["smoke"] if smoke else []),
        ],  # fmt: skip
        launched_by=launched_by,
        name=f"decision-review-{season}-w{week:02d}",
    )
    ok = False
    try:
        out: dict[str, Any] = {"week": week}
        plays = _play_table(payload["plays"])
        if plays.height:
            out["review/decisions"] = wandb.Table(dataframe=plays.to_pandas())
        top = _play_table(payload["highlights"]["top"])
        if top.height:
            out["review/costliest"] = wandb.Table(dataframe=top.to_pandas())
        board = tables.get("leaderboard") or []
        if board:
            df = pl.DataFrame(board).with_columns(pl.col("teams").list.join(" / "))
            out["season/leaderboard"] = wandb.Table(dataframe=df.to_pandas())
        cal = tables.get("calibration") or {}
        wp = cal.get("wp")
        if wp:
            m, v = wp["model"], wp["vegas"]
            out["season/wp_calibration"] = wandb.plot.line_series(
                xs=[[b["mean_pred"] for b in m], [b["mean_pred"] for b in v], [0.0, 1.0]],
                ys=[[b["mean_outcome"] for b in m], [b["mean_outcome"] for b in v], [0.0, 1.0]],
                keys=["bot", "vegas_wp", "perfect calibration"],
                title=f"Win probability calibration, {season} through week {week} "
                f"({CAL_BINS} bins)",
                xname="predicted win probability",
            )
            rows = [{"predictor": "bot", **b} for b in m] + [
                {"predictor": "vegas_wp", **b} for b in v
            ]
            out["season/wp_calibration_bins"] = wandb.Table(
                dataframe=pl.DataFrame(rows).to_pandas()
            )
        conv = cal.get("conversion")
        if conv and conv["rows"]:
            xs, ys, keys = [], [], []
            for down, name in ((4, "4th-down attempts"), (3, "3rd downs")):
                part = [r for r in conv["rows"] if r["down"] == down]
                for col, what in (("actual", "actual"), ("pred", "bot")):
                    xs.append([r["distance"] for r in part])
                    ys.append([r[col] for r in part])
                    keys.append(f"{name}: {what}")
            out["season/conversion"] = wandb.plot.line_series(
                xs=xs,
                ys=ys,
                keys=keys,
                title="Conversion by distance: the bot vs what happened",
                xname="yards to go (10 = 10+)",
            )
            out["season/conversion_rows"] = wandb.Table(
                dataframe=pl.DataFrame(conv["rows"]).to_pandas()
            )
        if cal.get("fg") and cal["fg"]["rows"]:
            out["season/fg_table"] = wandb.Table(
                dataframe=pl.DataFrame(cal["fg"]["rows"]).to_pandas()
            )
        tr = tables.get("trend") or []
        if tr:
            out["season/trend"] = wandb.Table(dataframe=pl.DataFrame(tr).to_pandas())
            out["season/go_rate_by_week"] = wandb.plot.line_series(
                xs=[r["week"] for r in tr],
                ys=[[r["go_rate_coach"] for r in tr], [r["go_rate_bot"] for r in tr]],
                keys=["coaches", "bot"],
                title="4th-down go rate by week: coaches vs the bot",
                xname="week",
            )
        scalars = _scalar_metrics(payload, tables)
        out.update(scalars)
        run.log(out)
        run.summary.update(scalars)
        ok = True
        return {"id": run.id, "url": getattr(run, "url", None)}
    finally:
        run.finish(exit_code=0 if ok else 1)


def _stored_through(folder: Path) -> int | None:
    try:
        doc = json.loads((folder / f"{SEASON_FILE}.json").read_text(encoding="utf-8"))
        return int(doc["through_week"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _models_of(engine: Any) -> Any:
    """The `LiveModels` behind an engine (the app wraps it: `_LockedEngine` forwards `m`)."""
    return engine.m


def _trained_of(engine: Any) -> list[int] | None:
    try:
        seasons = (engine.m.meta or {}).get("seasons")
    except AttributeError:
        return None
    return [int(s) for s in seasons] if seasons else None
