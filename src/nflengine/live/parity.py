"""State and decision parity: ESPN's replayed states against nflverse's play-by-play (LD01).

Two checks on finished games, both read only (curated data, the saved ESPN summaries; nothing
is written outside `{NFL_DATA_ROOT}/live/parity/`):

- **state parity** (`run_parity`): every 3rd / 4th-down start state of a replayed ESPN summary
  against nflverse's on the same play (ESPN play id = event id + nflverse `play_id`): offense,
  down, distance, yards to goal, score difference, quarter, clock (within 5 s) and both teams'
  timeouts. Target: >= 98% of states equal. Each mismatch gets a class that explains it;
  rows only one feed has as a 3rd / 4th-down snap are counted apart, with what the other feed
  has for that play.
- **decision parity** (`decision_parity`): the same 4th downs (`data.fourth_frame`) scored by the
  LD00 engine from the ESPN state and from the nflverse state. Equal states must give the same
  call (same engine); every disagreement lists the state fields that differ.

A game curated `plays` doesn't have yet (the newest week) is read from nflverse's own file in
memory (`nflreadpy`, never written to curated data); without it the game is skipped.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import polars as pl

from nflengine.live import data as D
from nflengine.live import schema
from nflengine.live.espn import EspnClient, EspnError
from nflengine.live.replay import ReplayPlay, load_summary, replay, summary_state
from nflengine.live.schema import GameState
from nflengine.live.state import Contexts, load_contexts
from nflengine.paths import DataPaths, configure_tool_env, ensure_data_root

TARGET = 0.98
CLOCK_TOLERANCE_S = 5.0
FIELDS = (
    "offense", "down", "distance", "yardline_100", "score_diff", "quarter", "clock",
    "off_timeouts", "def_timeouts",
)  # fmt: skip
# nflverse column for each field (the ESPN side's columns are `espn_<field>`)
NV_FIELD = {
    "offense": "posteam",
    "down": "down",
    "distance": "ydstogo",
    "yardline_100": "yardline_100",
    "score_diff": "score_differential",
    "quarter": "qtr",
    "clock": "quarter_seconds_remaining",
    "off_timeouts": "posteam_timeouts_remaining",
    "def_timeouts": "defteam_timeouts_remaining",
}
NV_COLUMNS = [
    "game_id", "play_id", "week", "qtr", "down", "ydstogo", "yardline_100", "posteam",
    "play_type", "quarter_seconds_remaining", "score_differential", "posteam_timeouts_remaining",
    "defteam_timeouts_remaining", "two_point_attempt", "desc",
]  # fmt: skip
SCORING_TYPE = re.compile(r"touchdown|field goal good|safety", re.IGNORECASE)
CHALLENGE_UPHELD = re.compile(r"challenged\b[^.]*?upheld", re.IGNORECASE)
EXAMPLES = 5

# every mismatch class, in the order a row is classified (the first that fits)
EXPLAIN = {
    "offense_or_down": "The feeds disagree on who has the ball, the down or the quarter.",
    "score": "The score before the play differs.",
    "spot": (
        "The feeds put the ball (or the line to gain) a yard or so apart: ESPN's start spot "
        "can disagree with its own play text by a yard, and after a penalty its distance can be "
        "a yard off nflverse's (spot rounding, corrected spots)."
    ),
    "clock_scoring_play": (
        "A scoring play: ESPN's clock is the clock at the score, not at the snap. The replay "
        "estimates the snap clock (`replay.scoring_start_clock`); it can still be a few "
        "seconds short (or, rarely, long)."
    ),
    "clock_other": "The start clock differs by more than 5 s on a play that didn't score.",
    "timeouts_challenge_play": (
        "nflverse charges a lost challenge's timeout to the challenged play's own start state "
        "(its count includes the play's own timeout); the timeout is charged after the play, "
        "which is how ESPN's replay counts it."
    ),
    "timeouts_log": (
        "The feeds log different timeouts earlier in the half: ESPN's short-form play text "
        "('Daniel Jones Pass Complete for 5 Yds ...') drops a lost challenge, or one feed "
        "keeps a timeout the other dropped when the play was corrected."
    ),
    "espn_only": "Only ESPN has this play as a 3rd / 4th-down snap.",
    "nflverse_only": "Only nflverse has this play as a 3rd / 4th-down snap.",
}


# --- the two sides ----------------------------------------------------------------------------
def espn_rows(plays: Iterable[ReplayPlay], game_id: str) -> pl.DataFrame:
    """Every replayed ESPN play keyed by (game_id, play_id = nflverse's), with the start state
    the engine would get (the `GameState`'s distance, score and timeouts when one was built)."""
    rows = []
    for p in plays:
        if p.nflverse_play_id is None:
            continue
        s = p.state
        rows.append(
            {
                "game_id": game_id,
                "play_id": int(p.nflverse_play_id),
                "espn_snap": bool(p.snap),
                "espn_type": p.type,
                "espn_text": p.text,
                "espn_display_clock": p.display_clock,
                "espn_scoring": bool(SCORING_TYPE.search(p.type or "")),
                "espn_has_state": s is not None,
                "espn_reason": p.reason,
                "espn_offense": p.offense,
                "espn_down": p.down,
                "espn_distance": s.ydstogo if s is not None else p.distance,
                "espn_yardline_100": p.yardline_100,
                "espn_score_diff": s.score_diff if s is not None else p.score_diff,
                "espn_quarter": p.period,
                "espn_clock": p.clock,
                "espn_off_timeouts": p.off_timeouts,
                "espn_def_timeouts": p.def_timeouts,
            }
        )
    return pl.DataFrame(rows, schema=ESPN_SCHEMA)


ESPN_SCHEMA = {
    "game_id": pl.String,
    "play_id": pl.Int64,
    "espn_snap": pl.Boolean,
    "espn_type": pl.String,
    "espn_text": pl.String,
    "espn_display_clock": pl.String,
    "espn_scoring": pl.Boolean,
    "espn_has_state": pl.Boolean,
    "espn_reason": pl.String,
    "espn_offense": pl.String,
    "espn_down": pl.Int64,
    "espn_distance": pl.Int64,
    "espn_yardline_100": pl.Int64,
    "espn_score_diff": pl.Int64,
    "espn_quarter": pl.Int64,
    "espn_clock": pl.Float64,
    "espn_off_timeouts": pl.Int64,
    "espn_def_timeouts": pl.Int64,
}


def nflverse_decision_rows(plays: pl.DataFrame) -> pl.DataFrame:
    """nflverse's 3rd / 4th-down start states: snaps (runs, passes, punts, field goals, kneels,
    spikes and pre-snap penalties, `no_play`) with a down of 3 or 4 and a team on offense; no
    two-point tries (kickoffs and extra points carry no down)."""
    return plays.filter(
        pl.col("down").is_in([3, 4])
        & pl.col("play_type").is_in(list(D.SNAP_TYPES))
        & pl.col("posteam").is_not_null()
        & (pl.col("two_point_attempt").fill_null(0) == 0)
    )


def espn_decision_rows(espn: pl.DataFrame) -> pl.DataFrame:
    """ESPN's 3rd / 4th-down start states: replayed snaps (pre-snap penalties included) with a
    down of 3 or 4."""
    return espn.filter(pl.col("espn_snap") & pl.col("espn_down").is_in([3, 4]))


def _standard(plays: pl.DataFrame) -> pl.DataFrame:
    """nflverse rows with the columns and types the comparison reads."""
    missing = [c for c in NV_COLUMNS if c not in plays.columns]
    plays = plays.with_columns([pl.lit(None).alias(c) for c in missing])
    ints = ["play_id", "week", "qtr", "down", "ydstogo", "yardline_100", "score_differential"]
    ints += ["posteam_timeouts_remaining", "defteam_timeouts_remaining", "two_point_attempt"]
    return plays.select(NV_COLUMNS).with_columns(
        [pl.col(c).cast(pl.Int64) for c in ints]
        + [pl.col("quarter_seconds_remaining").cast(pl.Float64)]
        + [pl.col(c).cast(pl.String) for c in ("game_id", "posteam", "play_type", "desc")]
    )


# --- the comparison ---------------------------------------------------------------------------
def compare_states(espn: pl.DataFrame, nflverse: pl.DataFrame) -> pl.DataFrame:
    """One row per 3rd / 4th-down start state either feed has (a full join on game and play):
    `side` (both / espn_only / nflverse_only), `ok_<field>` per field, `equal`, the mismatched
    `fields` and the row's `category` (a key of `EXPLAIN`; null when equal). `espn` = every
    replayed ESPN play (`espn_rows`), `nflverse` = every nflverse play of the same games: the
    rows outside the 3rd / 4th-down states explain the one-sided rows (`note`)."""
    nflverse = _standard(nflverse)
    e3 = espn_decision_rows(espn)
    n3 = nflverse_decision_rows(nflverse)
    comp = e3.join(
        n3.with_columns(pl.lit(True).alias("nv_row")),
        on=["game_id", "play_id"],
        how="full",
        coalesce=True,
    )
    comp = comp.with_columns(
        pl.when(pl.col("espn_down").is_null())
        .then(pl.lit("nflverse_only"))
        .when(pl.col("nv_row").is_null())
        .then(pl.lit("espn_only"))
        .otherwise(pl.lit("both"))
        .alias("side")
    ).drop("nv_row")
    checks = {}
    for f in FIELDS:
        e, n = pl.col(f"espn_{f}"), pl.col(NV_FIELD[f])
        ok = (e - n).abs() <= CLOCK_TOLERANCE_S if f == "clock" else e == n
        checks[f"ok_{f}"] = (pl.col("side") == "both") & ok.fill_null(False)
    comp = comp.with_columns(**checks)
    comp = comp.with_columns(
        ((pl.col("side") == "both") & pl.all_horizontal(list(checks))).alias("equal")
    )
    rows = comp.to_dicts()
    # what the other feed has for a one-sided play
    other_e = {(r["game_id"], r["play_id"]): r for r in espn.to_dicts()}
    other_n = {(r["game_id"], r["play_id"]): r for r in nflverse.to_dicts()}
    for r in rows:
        r["fields"] = [f for f in FIELDS if r["side"] == "both" and not r[f"ok_{f}"]]
        r["category"], r["note"] = _classify(r, other_e, other_n)
    out = pl.DataFrame(
        rows,
        schema={
            **comp.schema,
            "fields": pl.List(pl.String),
            "category": pl.String,
            "note": pl.String,
        },
    )
    return out.sort("game_id", "play_id")


def _classify(
    r: dict[str, Any],
    espn_all: dict[tuple[str, int], dict[str, Any]],
    nv_all: dict[tuple[str, int], dict[str, Any]],
) -> tuple[str | None, str | None]:
    key = (r["game_id"], r["play_id"])
    if r["side"] == "nflverse_only":
        e = espn_all.get(key)
        if e is None:
            return "nflverse_only", f"ESPN has no play {r['play_id']}"
        how = f"a snap, down {e['espn_down']}" if e["espn_snap"] else "not a snap"
        return "nflverse_only", f"ESPN has play {r['play_id']} as '{e['espn_type']}' ({how})"
    if r["side"] == "espn_only":
        n = nv_all.get(key)
        if n is None:
            return "espn_only", f"nflverse has no play {r['play_id']}"
        return "espn_only", (
            f"nflverse has play {r['play_id']} as {n['play_type']}, down {n['down']} & "
            f"{n['ydstogo']} at {n['yardline_100']} yards to go"
        )
    bad = set(r["fields"])
    if not bad:
        return None, None
    if bad & {"offense", "down", "quarter"}:
        return "offense_or_down", None
    if "score_diff" in bad:
        return "score", None
    if bad & {"distance", "yardline_100"}:
        return "spot", None
    if "clock" in bad:
        return ("clock_scoring_play" if r["espn_scoring"] else "clock_other"), None
    if CHALLENGE_UPHELD.search(r.get("desc") or ""):
        return "timeouts_challenge_play", None
    return "timeouts_log", None


def _example(r: dict[str, Any]) -> dict[str, Any]:
    ex: dict[str, Any] = {"game_id": r["game_id"], "play_id": r["play_id"]}
    if r["side"] == "both":
        ex["fields"] = r["fields"]
        ex["espn"] = {f: r[f"espn_{f}"] for f in r["fields"]}
        ex["nflverse"] = {f: r[NV_FIELD[f]] for f in r["fields"]}
    else:
        ex["note"] = r["note"]
    ex["espn_type"] = r.get("espn_type")
    ex["text"] = (r.get("desc") or r.get("espn_text") or "")[:240]
    return ex


def summarize(comp: pl.DataFrame, examples: int = EXAMPLES) -> dict[str, Any]:
    """The report's numbers: states compared, % equal, mismatches per field and per class (with
    examples), and every one-sided row. The gate (`share_equal`, `passed`) counts a state found
    on one side only as not equal, so a feed that drops states can't pass (Sol review, LD01);
    `share_equal_overlap` is the share among states both sides have."""
    both = comp.filter(pl.col("side") == "both")
    n_equal = int(both["equal"].sum()) if both.height else 0
    total = comp.height
    share = n_equal / total if total else None
    overlap = n_equal / both.height if both.height else None
    fields = {f: int((~both[f"ok_{f}"]).sum()) for f in FIELDS} if both.height else {}
    cats: dict[str, Any] = {}
    for cat in EXPLAIN:
        sub = comp.filter(pl.col("category") == cat)
        if not sub.height:
            continue
        cats[cat] = {
            "count": sub.height,
            "explanation": EXPLAIN[cat],
            "examples": [_example(r) for r in sub.head(examples).to_dicts()],
        }
    one_side = comp.filter(pl.col("side") != "both")
    return {
        "states_compared": both.height,
        "states_total": total,
        "states_equal": n_equal,
        "share_equal": share,
        "share_equal_overlap": overlap,
        "target": TARGET,
        "passed": share is not None and share >= TARGET,
        "field_mismatches": fields,
        "categories": cats,
        "espn_only": int((comp["side"] == "espn_only").sum()),
        "nflverse_only": int((comp["side"] == "nflverse_only").sum()),
        "one_sided": [_example(r) for r in one_side.to_dicts()],
    }


# --- reading the data -------------------------------------------------------------------------
def nflverse_plays(
    season: int,
    game_ids: Iterable[str],
    paths: DataPaths,
    log: Callable[[str], Any] = print,
) -> tuple[pl.DataFrame, dict[str, str]]:
    """Every play of these games (all columns of curated `plays`): curated first, then
    nflverse's own play-by-play file (through nflreadpy and its download cache under
    `cache/nflreadpy`, like the ingest; never written to curated) for games curated doesn't
    have yet.
    Returns the plays and where each game came from ("curated" / "nflverse")."""
    want = sorted(set(game_ids))
    one = paths.curated / "plays" / f"season={season}.parquet"
    if one.exists():
        lf = pl.scan_parquet(one)
    else:  # the season files' column types can differ: read only this season's rows
        lf = pl.scan_parquet((paths.curated / "plays" / "*.parquet").as_posix()).filter(
            pl.col("season") == season
        )
    cur = lf.filter(pl.col("game_id").is_in(want)).collect()
    source = {g: "curated" for g in cur["game_id"].unique().to_list()}
    rest = [g for g in want if g not in source]
    if not rest:
        return cur, source
    log(f"curated plays don't have {len(rest)} game(s) yet: reading nflverse's file (nflreadpy)")
    try:
        configure_tool_env(paths)
        import nflreadpy

        pbp = nflreadpy.load_pbp([season])
        if not isinstance(pbp, pl.DataFrame):
            pbp = pl.from_pandas(pbp)
        extra = pbp.filter(pl.col("game_id").is_in(rest))
    except Exception as exc:  # network, cache or a format change: those games are skipped
        log(f"nflverse play-by-play unavailable ({type(exc).__name__})")
        return cur, source
    source.update({g: "nflverse" for g in extra["game_id"].unique().to_list()})
    if extra.height:
        common = [c for c in cur.columns if c in extra.columns]
        extra = extra.select(common).cast({c: cur.schema[c] for c in common}, strict=False)
        cur = pl.concat([cur.select(common), extra], how="vertical")
    return cur, source


def _finished_summary(
    event_id: str,
    folder,
    client: EspnClient | None,
    kickoff: datetime | None,
    now: datetime,
) -> tuple[dict[str, Any] | None, str | None]:
    """(summary, None) or (None, why it's skipped). A game not yet played isn't asked for."""
    saved = (folder / f"{event_id}.json.gz").exists()
    if not saved and (kickoff is None or kickoff > now):
        return None, "not played yet"
    try:
        summary, _ = load_summary(event_id, folder, client)
    except EspnError as e:
        return None, f"ESPN: {e}"
    if summary_state(summary) != "post":
        return None, f"not finished on ESPN ({summary_state(summary)})"
    return summary, None


def _games(ctxs: Contexts, weeks: Iterable[int]) -> list[dict[str, Any]]:
    g = ctxs.games.filter(pl.col("week").is_in(sorted(set(weeks))))
    return g.sort("week", "kickoff_utc", "game_id").to_dicts()


def run_parity(
    season: int,
    weeks: list[int],
    paths: DataPaths | None = None,
    client: EspnClient | None = None,
    log: Callable[[str], Any] = print,
) -> dict[str, Any]:
    """State parity for every game of these weeks with an ESPN event id and a finished summary.
    Saves `live/parity/<season>-wNN-wNN.json` (the report) and `.parquet` (every row)."""
    paths = paths or ensure_data_root(create_dirs=False)
    ctxs = load_contexts(season, paths)
    by_game = {gid: ev for ev, gid in ctxs.event_map.items()}
    folder = paths.live_data / "summaries"
    own = client is None
    client = client or EspnClient()
    now = datetime.now(UTC)
    skipped: list[dict[str, Any]] = []
    replayed: dict[str, tuple[str, int, pl.DataFrame]] = {}
    try:
        for g in _games(ctxs, weeks):
            gid = g["game_id"]
            ev = by_game.get(gid)
            if ev is None:
                skipped.append({"game_id": gid, "event_id": None, "reason": "no ESPN event id"})
                continue
            summary, why = _finished_summary(ev, folder, client, g.get("kickoff_utc"), now)
            if summary is None:
                skipped.append({"game_id": gid, "event_id": ev, "reason": why})
                continue
            ctx = ctxs.context(ev)
            replayed[gid] = (ev, int(g["week"]), espn_rows(replay(summary, ctx), gid))
    finally:
        if own:
            client.close()
    nv, source = nflverse_plays(season, list(replayed), paths, log)
    for gid in [g for g in replayed if g not in source]:
        ev, _, _ = replayed.pop(gid)
        skipped.append({"game_id": gid, "event_id": ev, "reason": "no nflverse play-by-play yet"})
    espn = pl.concat([r[2] for r in replayed.values()]) if replayed else espn_rows([], "")
    comp = compare_states(espn, nv)
    report = summarize(comp)
    games = []
    for gid, (ev, week, _) in replayed.items():
        sub = comp.filter(pl.col("game_id") == gid)
        b = sub.filter(pl.col("side") == "both")
        games.append(
            {
                "game_id": gid,
                "event_id": ev,
                "week": week,
                "nflverse_source": source[gid],
                "states": b.height,
                "equal": int(b["equal"].sum()),
                "espn_only": int((sub["side"] == "espn_only").sum()),
                "nflverse_only": int((sub["side"] == "nflverse_only").sum()),
            }
        )
    report = {
        "season": season,
        "weeks": sorted(set(weeks)),
        "created_at": now.isoformat(timespec="seconds"),
        **report,
        "games_compared": len(games),
        "games_skipped": len(skipped),
        "games": games,
        "skipped": skipped,
    }
    out = paths.live_data / "parity"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{season}-w{min(weeks):02d}-w{max(weeks):02d}"
    (out / f"{stem}.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    comp.write_parquet(out / f"{stem}.parquet")
    report["files"] = [f"live/parity/{stem}.json", f"live/parity/{stem}.parquet"]
    share = report["share_equal"]
    log(
        f"state parity {season} weeks {min(weeks)}-{max(weeks)}: {report['states_equal']} / "
        f"{report['states_total']} equal ({report['states_compared']} on both sides)"
        + (f" ({share:.1%}, target {TARGET:.0%})" if share is not None else "")
        + f"; {len(games)} games compared, {len(skipped)} skipped"
    )
    return report


# --- decision parity --------------------------------------------------------------------------
STATE_FIELDS = (
    "season", "score_diff", "game_seconds", "half_seconds", "down", "ydstogo", "yardline_100",
    "off_timeouts", "def_timeouts", "home", "receive_2h_ko", "spread", "total", "indoor", "ot",
    "playoffs", "wind", "temp",
)  # fmt: skip


def state_diff(a: GameState, b: GameState, tol: float = 1e-6) -> list[str]:
    """The fields two states differ on, as the engine sees them (`roof` as indoor or not)."""
    da, db = a.as_dict(), b.as_dict()
    da["indoor"], db["indoor"] = schema.indoor(a.roof), schema.indoor(b.roof)
    out = []
    for f in STATE_FIELDS:
        x, y = da[f], db[f]
        if x is None or y is None:
            if (x is None) != (y is None):
                out.append(f)
        elif abs(float(x) - float(y)) > tol:
            out.append(f)
    return out


def _decision_frame(
    season: int, game_ids: list[str], paths: DataPaths, log: Callable[[str], Any]
) -> pl.DataFrame:
    """`load_plays`'s frame for these games (curated, else nflverse's file through nflreadpy)."""
    cur = D.load_plays([season], paths).filter(pl.col("game_id").is_in(game_ids))
    rest = [g for g in game_ids if g not in set(cur["game_id"].to_list())]
    if not rest:
        return cur
    extra, source = nflverse_plays(season, rest, paths, log)
    extra = extra.filter(pl.col("game_id").is_in([g for g, s in source.items() if s != "curated"]))
    if not extra.height:
        return cur
    games = pl.read_parquet(paths.curated / "games.parquet", columns=["game_id", "neutral_site"])
    extra = (
        extra.select([c for c in D.PLAY_COLUMNS if c in extra.columns])
        .join(games, on="game_id", how="left")
        .with_columns(pl.col("neutral_site").fill_null(False))
        .sort("game_id", "order_sequence", "play_id")
        .with_columns(pl.int_range(pl.len()).over("game_id").alias("i"))
    )
    common = [c for c in cur.columns if c in extra.columns]
    extra = extra.select(common).cast({c: cur.schema[c] for c in common}, strict=False)
    return pl.concat([cur.select(common), extra], how="vertical")


def compare_calls(
    pairs: list[tuple[dict[str, Any], GameState, GameState]],
    engine: Any,
) -> list[dict[str, Any]]:
    """Score each (nflverse row, nflverse state, ESPN state) with the engine (no bootstrap) and
    return one row per play: both calls, both win probabilities and the differing fields."""
    if not pairs:
        return []
    nv_calls = engine.fourth_downs([p[1] for p in pairs], bootstrap=False)
    es_calls = engine.fourth_downs([p[2] for p in pairs], bootstrap=False)
    out = []
    for (row, nv_s, es_s), a, b in zip(pairs, nv_calls, es_calls, strict=True):
        dwp = {}
        for opt in ("go", "fg", "punt"):
            x, y = a.wp.get(opt), b.wp.get(opt)
            dwp[opt] = None if x is None or y is None else float(y) - float(x)
        out.append(
            {
                "play_id": int(row["play_id"]),
                "choice": row.get("choice"),
                "nflverse_best": a.best,
                "espn_best": b.best,
                "agree": a.best == b.best,
                "fields": state_diff(nv_s, es_s),
                "nflverse_wp": a.wp,
                "espn_wp": b.wp,
                "dwp": dwp,
                "nflverse_state": nv_s.as_dict(),
                "espn_state": es_s.as_dict(),
                "text": (row.get("desc") or "")[:240],
            }
        )
    return out


def _max_abs(rows: list[dict[str, Any]], opt: str) -> float | None:
    vals = [abs(r["dwp"][opt]) for r in rows if r["dwp"].get(opt) is not None]
    return max(vals) if vals else None


def _field_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        for f in r["fields"]:
            out[f] = out.get(f, 0) + 1
    return out


def decision_parity(
    event_ids: list[str],
    paths: DataPaths | None = None,
    client: EspnClient | None = None,
    log: Callable[[str], Any] = print,
) -> dict[str, Any]:
    """The same 4th downs scored by the LD00 engine from ESPN's and nflverse's states. Saves
    `live/parity/decisions-<season>.json` (+ `.parquet`, one row per 4th down)."""
    from nflengine.live.decide import Engine, decide_settings
    from nflengine.live.train import load_production

    paths = paths or ensure_data_root(create_dirs=False)
    folder = paths.live_data / "summaries"
    own = client is None
    client = client or EspnClient()
    ctx_by_season: dict[int, Contexts] = {}
    games: list[dict[str, Any]] = []
    try:
        for ev in event_ids:
            try:
                summary, _ = load_summary(str(ev), folder, client)
            except EspnError as e:
                games.append({"event_id": str(ev), "skipped": f"ESPN: {e}"})
                continue
            season = int(((summary.get("header") or {}).get("season") or {}).get("year"))
            if season not in ctx_by_season:
                ctx_by_season[season] = load_contexts(season, paths)
            ctx = ctx_by_season[season].context(str(ev))
            if ctx is None or summary_state(summary) != "post":
                games.append({"event_id": str(ev), "skipped": "no game id or not finished"})
                continue
            games.append({"event_id": str(ev), "season": season, "ctx": ctx, "summary": summary})
    finally:
        if own:
            client.close()
    engine = Engine(load_production(paths), decide_settings())
    all_rows: list[dict[str, Any]] = []
    try:
        for season in sorted({g["season"] for g in games if "season" in g}):
            mine = [g for g in games if g.get("season") == season]
            frame = _decision_frame(season, [g["ctx"].game_id for g in mine], paths, log)
            fourth = D.fourth_frame(D.with_state(frame))
            for g in mine:
                ctx = g["ctx"]
                espn = {p.nflverse_play_id: p for p in replay(g.pop("summary"), ctx)}
                pairs, missing = [], []
                for row in fourth.filter(pl.col("game_id") == ctx.game_id).to_dicts():
                    p = espn.get(int(row["play_id"]))
                    if p is None or p.state is None or p.down != 4:
                        why = (
                            "ESPN has no such play"
                            if p is None
                            else (p.reason or f"ESPN's down is {p.down}")
                        )
                        missing.append({"play_id": int(row["play_id"]), "reason": why})
                        continue
                    pairs.append((row, D.state_of_row(row), p.state))
                rows = compare_calls(pairs, engine)
                for r in rows:
                    r["game_id"] = ctx.game_id
                all_rows.extend(rows)
                eq = [r for r in rows if not r["fields"]]
                g.update(
                    game_id=ctx.game_id,
                    fourth_downs=len(pairs) + len(missing),
                    scored=len(rows),
                    agree=sum(r["agree"] for r in rows),
                    equal_states=len(eq),
                    equal_states_agree=sum(r["agree"] for r in eq),
                    # same state, same engine: the win probabilities must be identical
                    equal_states_wp_differ=[
                        r["play_id"]
                        for r in eq
                        if any(v is not None and abs(v) > 1e-12 for v in r["dwp"].values())
                    ],
                    max_abs_dwp={o: _max_abs(rows, o) for o in ("go", "fg", "punt")},
                    state_fields_differ=_field_counts(rows),
                    disagreements=[
                        {
                            k: r[k]
                            for k in ("play_id", "choice", "nflverse_best", "espn_best", "fields")
                        }
                        | {
                            "nflverse_wp": r["nflverse_wp"],
                            "espn_wp": r["espn_wp"],
                            "differs": {
                                f: [r["nflverse_state"].get(f), r["espn_state"].get(f)]
                                for f in r["fields"]
                                if f in r["nflverse_state"]
                            },
                            "text": r["text"],
                        }
                        for r in rows
                        if not r["agree"]
                    ],
                    missing=missing,
                )
                g.pop("ctx")
    finally:
        engine.close()
    for g in games:
        g.pop("ctx", None)
        g.pop("summary", None)
    done = [g for g in games if "scored" in g]
    report = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "event_ids": [str(e) for e in event_ids],
        "fourth_downs": sum(g["fourth_downs"] for g in done),
        "scored": sum(g["scored"] for g in done),
        "agree": sum(g["agree"] for g in done),
        "equal_states": sum(g["equal_states"] for g in done),
        "equal_states_agree": sum(g["equal_states_agree"] for g in done),
        "max_abs_dwp": {o: _max_abs(all_rows, o) for o in ("go", "fg", "punt")},
        "state_fields_differ": _field_counts(all_rows),
        "games": games,
    }
    out = paths.live_data / "parity"
    out.mkdir(parents=True, exist_ok=True)
    seasons = sorted({g["season"] for g in done}) or ["none"]
    stem = "decisions-" + "-".join(str(s) for s in seasons)
    (out / f"{stem}.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    if all_rows:
        flat = [
            {
                "game_id": r["game_id"],
                "play_id": r["play_id"],
                "choice": r["choice"],
                "nflverse_best": r["nflverse_best"],
                "espn_best": r["espn_best"],
                "agree": r["agree"],
                "fields": r["fields"],
                **{f"dwp_{o}": r["dwp"][o] for o in ("go", "fg", "punt")},
                "game_seconds_diff": r["espn_state"]["game_seconds"]
                - r["nflverse_state"]["game_seconds"],
            }
            for r in all_rows
        ]
        pl.DataFrame(flat).write_parquet(out / f"{stem}.parquet")
    report["files"] = [f"live/parity/{stem}.json"] + (
        [f"live/parity/{stem}.parquet"] if all_rows else []
    )
    log(
        f"decision parity: {report['agree']} / {report['scored']} calls agree "
        f"({report['equal_states']} equal states, {report['equal_states_agree']} agree)"
    )
    return report
