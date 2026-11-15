"""The Availability and Outcomes week tabs (FL02; documentation/forecast-lab/README.md §3).

Read-only: everything comes from the files `nfl forecastlab update` wrote under
`{data root}/forecastlab/<S>/` (the contract is `nflengine.forecastlab.schema`). Nothing is
recomputed except small summaries, and nothing is written.

- `get_availability`: who might sit (the chance he plays, Tuesday and Saturday), expected snap
  and opportunity shares against each player's baseline, who inherits, and once the games are
  played what happened.
- `get_outcomes`: every curve's summary (quantiles, the standard marks, the band) for the
  week; `get_curve` adds one curve's whole distribution, from which the page works out the
  chance of any number or band the reader types.

**Only shipped outputs are shown.** A model output or curve target that missed its ship rule
isn't in the answer (its baseline is): `meta.shipped` says what passed.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from nflengine.app.readers.common import clean, num, read_json, read_parquet
from nflengine.forecastlab import schema as S
from nflengine.paths import DataPaths

AT_RISK_ROWS = 150
# the "might sit" list: everyone with a game status, plus regulars (this share of their
# side's snaps over their last 2 games) whose chance of playing is low. Without the floor
# the list is ~300 fringe players and healthy scratches a week (FL00's report)
RISK_ROLE_FLOOR = 0.30
# a teammate's gain smaller than this isn't worth a line (shares are 0-1)
INHERIT_MIN = {"targets": 0.01, "carries": 0.01, "snaps": 0.03}
# the order of the might-sit groups on the page
RISK_GROUPS = ("report", "uncertain", "reserve")
MOVER_ROWS = 15
GAP_ROWS = 12
STATUS_ORDER = {"Out": 0, "Doubtful": 1, "Questionable": 2}
# the model columns each availability output owns (nulled when it didn't ship)
OUTPUT_COLS = {
    "play": ("p_play",),
    "snap": ("snap_p10", "snap_p50", "snap_p90", "exp_snap"),
    "tgt_share": ("tgt_share",),
    "carry_share": ("carry_share",),
}


STR_LIMIT = 200  # no string in an answer is longer (names, ids, labels, hints)
# which shipped output a "who inherits" row rests on
INHERIT_OUTPUT = {"targets": "tgt_share", "carries": "carry_share", "snaps": "snap"}


def _clean_strings(obj: Any, paths: DataPaths) -> Any:
    """Every string of an answer through `clean()`: the files under `forecastlab/` are made
    by our own code, and cleaned anyway like every file-derived text (the review's find: a
    secret-shaped player name or meta value would have reached the browser)."""
    if isinstance(obj, str):
        return clean(obj, paths, STR_LIMIT)
    if isinstance(obj, dict):
        return {k: _clean_strings(v, paths) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_strings(v, paths) for v in obj]
    return obj


def _text(value: Any) -> str | None:
    """A meta field that must be text (a timestamp), or None."""
    return value if isinstance(value, str) else None


def _count(value: Any) -> int | None:
    """A meta field that must be a whole number, or None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str_list(value: Any) -> list[str]:
    """`value` when it is a list, keeping its strings only (a damaged meta file)."""
    return [x for x in value if isinstance(x, str)] if isinstance(value, list) else []


def _shipped_views(meta: dict[str, Any]) -> dict[str, list[str]]:
    """availability.json's `shipped`: {view: [outputs]}, whatever shape the file has."""
    raw = meta.get("shipped")
    raw = raw if isinstance(raw, dict) else {}
    return {v: _str_list(raw.get(v)) for v in S.VIEWS}


def _unreadable(body: dict[str, Any], week: int) -> dict[str, Any]:
    return {**body, "status": "none", "hint": f"Week {week}'s file couldn't be read."}


def _state(paths: DataPaths, season: int, week: int, family: str) -> tuple[str, str | None]:
    """(`ok` | `none` | `no_model`, the empty state's sentence)."""
    if read_json(S.production_file(paths, family)) is None:
        return "no_model", (
            "This model hasn't been promoted yet: its backtest decides what ships "
            f"(`nfl forecastlab backtest --model {family} --promote`)."
        )
    if not (S.week_dir(paths, season, week) / f"{family}.parquet").exists():
        return "none", (
            f"Nothing for week {week}. It's made right after Run week {week}, once that "
            "week's run is published."
        )
    return "ok", None


def _f(x: Any, digits: int = 4) -> float | None:
    return num(x, digits)


def _scoreboard(paths: DataPaths, season: int, week: int, family: str) -> dict[str, Any]:
    """The week's graded rows and the season to date (through this week); empty when the
    file is missing or isn't a scoreboard."""
    try:
        return _scoreboard_rows(paths, season, week, family)
    except Exception:  # noqa: BLE001 - a damaged scoreboard is "no grades yet"
        return {"week": [], "season": []}


def _scoreboard_rows(paths: DataPaths, season: int, week: int, family: str) -> dict[str, Any]:
    df = read_parquet(S.season_dir(paths, season) / f"{family}_scoreboard.parquet")
    if df is None or df.is_empty() or "week" not in df.columns:
        return {"week": [], "season": []}
    df = df.filter(pl.col("week") <= week)
    keys = ["view", "output", "group"] if family == "availability" else ["target_key"]
    keys = [k for k in keys if k in df.columns]
    metrics = [
        c
        for c in df.columns
        if c not in (*keys, "season", "week", "n", "mode", "graded_at") and df[c].dtype.is_float()
    ]

    def rows(frame: pl.DataFrame) -> list[dict[str, Any]]:
        return [
            {k: (_f(v) if isinstance(v, float) else v) for k, v in r.items()}
            for r in frame.iter_rows(named=True)
        ]

    if "target_key" in df.columns:  # the body calls it `stat` (no "key" in a field name)
        df = df.rename({"target_key": "stat"})
        keys = ["stat" if k == "target_key" else k for k in keys]
    this = df.filter(pl.col("week") == week).select([*keys, "n", *metrics])
    # season to date: each metric weighted by the rows it was scored on
    w = pl.col("n").cast(pl.Float64)
    agg = [pl.col("n").sum().alias("n"), pl.col("week").n_unique().alias("weeks")] + [
        (
            (pl.col(m) * w).filter(pl.col(m).is_not_null()).sum()
            / w.filter(pl.col(m).is_not_null()).sum()
        ).alias(m)
        for m in metrics
    ]
    to_date = df.group_by(keys, maintain_order=True).agg(agg) if keys else df.select(agg)
    return {"week": rows(this), "season": rows(to_date)}


# --- Availability ---------------------------------------------------------------------------
def _gate_outputs(df: pl.DataFrame, shipped: dict[str, list[str]]) -> pl.DataFrame:
    """Null the model columns of every output that didn't ship for the row's view."""
    exprs = []
    for output, cols in OUTPUT_COLS.items():
        ok_views = [v for v in S.VIEWS if output in shipped.get(v, [])]
        for c in cols:
            if c in df.columns:
                exprs.append(
                    pl.when(pl.col("view").is_in(ok_views)).then(pl.col(c)).otherwise(None).alias(c)
                )
    return df.with_columns(exprs) if exprs else df


def _player_row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_id": r["player_id"],
        "player": r["player"],
        "team": r["team"],
        "opponent": r["opponent"],
        "home": r["home"],
        "game_id": r["game_id"],
        "position": r["position"],
        "pgroup": r["pgroup"],
        "side": r["side"],
        "view": r["view"],
        "report_status": r["report_status"],
        "practice_status": r["practice_status"],
        "prev_status": r["prev_status"],
        "roster_status": r["roster_status"],
        "p_play": _f(r["p_play"], 3),
        "p_play_tuesday": _f(r.get("p_play_tuesday"), 3),
        "base_p_play": _f(r["base_p_play"], 3),
        "snap_p10": _f(r["snap_p10"], 3),
        "snap_p50": _f(r["snap_p50"], 3),
        "snap_p90": _f(r["snap_p90"], 3),
        "base_snap": _f(r["base_snap"], 3),
        "exp_snap": _f(r["exp_snap"], 3),
        "tgt_share": _f(r["tgt_share"], 3),
        "base_tgt_share": _f(r["base_tgt_share"], 3),
        "carry_share": _f(r["carry_share"], 3),
        "base_carry_share": _f(r["base_carry_share"], 3),
        "at_risk": bool(r["at_risk"]) if r["at_risk"] is not None else False,
        "games_season": r["games_season"],
        "played": r["played"],
        "actual_snap": _f(r["actual_snap"], 3),
        "actual_tgt_share": _f(r["actual_tgt_share"], 3),
        "actual_carry_share": _f(r["actual_carry_share"], 3),
    }


def _availability_empty(season: int, week: int, status: str, hint: str | None) -> dict[str, Any]:
    return {
        "season": season,
        "week": week,
        "status": status,
        "hint": hint,
        "meta": None,
        "tiles": None,
        "at_risk": [],
        "movers": {"snap_up": [], "snap_down": [], "targets_up": [], "carries_up": []},
        "team": None,
        "players": [],
        "teams": [],
        "graded": False,
        "scoreboard": {"week": [], "season": []},
    }


def get_availability(
    paths: DataPaths, season: int, week: int, team: str | None = None
) -> dict[str, Any] | None:
    """The week's availability and role answer. `players` holds one team only (`team`;
    empty without one: every player at once is close to a megabyte); everything else
    covers the whole week. None when `team` isn't a team with rows this week (the route
    answers 404). A damaged or foreign file is an empty state, never an error."""
    status, hint = _state(paths, season, week, "availability")
    out = _availability_empty(season, week, status, hint)
    if status == "ok":
        try:
            body = _availability(paths, season, week, team, out)
        except Exception:  # noqa: BLE001 - "couldn't be read", never a 500
            body = _unreadable(out, week)
    else:
        body = out
    return _clean_strings(body, paths) if body is not None else None


def _availability(
    paths: DataPaths, season: int, week: int, team: str | None, out: dict[str, Any]
) -> dict[str, Any] | None:
    folder = S.week_dir(paths, season, week)
    df = read_parquet(folder / "availability.parquet")
    meta = read_json(folder / "availability.json") or {}
    if df is None or df.is_empty():
        return _unreadable(out, week)
    df = S.conform(df, S.AVAIL_SCHEMA)
    shipped = _shipped_views(meta)
    df = _gate_outputs(df, shipped)
    # one row per player and game: the Saturday view when it exists, else Tuesday's; the
    # Tuesday chance rides along so the page can show how the report moved it
    tue = df.filter(pl.col("view") == "tuesday").select(
        "player_id", "game_id", pl.col("p_play").alias("p_play_tuesday")
    )
    shown = (
        df.with_columns(pl.col("view").replace_strict({"saturday": 0}, default=1).alias("_o"))
        .sort("_o")
        .unique(subset=["player_id", "game_id"], keep="first", maintain_order=True)
        .drop("_o")
        .join(tue, on=["player_id", "game_id"], how="left")
    )
    rows = [_player_row(r) for r in shown.iter_rows(named=True)]
    rows.sort(key=lambda r: (r["team"] or "", r["side"] or "", -(r["base_snap"] or 0.0)))

    inherit = read_parquet(folder / "inherit.parquet")
    gains: dict[tuple[str, str], list[dict[str, Any]]] = {}
    if inherit is not None and not inherit.is_empty():
        inh = S.conform(inherit, S.INHERIT_SCHEMA).sort("rank")
        view_of = {(r["player_id"], r["game_id"]): r["view"] for r in rows}
        # a teammate who is known to be unavailable inherits nothing. Not "a low chance of
        # playing": a backup quarterback's chance is low only while the starter plays
        gone = {
            (r["player_id"], r["game_id"])
            for r in rows
            if r["roster_status"] == "RES" or r["report_status"] in ("Out", "Doubtful")
        }
        for r in inh.iter_rows(named=True):
            if view_of.get((r["out_player_id"], r["game_id"])) != r["view"]:
                continue  # only the view the page shows for that player
            if (r["player_id"], r["game_id"]) in gone:
                continue
            if INHERIT_OUTPUT.get(r["kind"] or "") not in shipped.get(r["view"] or "", []):
                continue  # the share model behind this gain didn't ship for the view
            if (r["rank"] or 99) > 3 or (r["delta"] or 0.0) < INHERIT_MIN.get(r["kind"], 0.01):
                continue
            gains.setdefault((r["out_player_id"], r["game_id"]), []).append(
                {
                    "kind": r["kind"],
                    "player_id": r["player_id"],
                    "player": r["player"],
                    "position": r["position"],
                    "share_if_in": _f(r["share_if_in"], 3),
                    "share_if_out": _f(r["share_if_out"], 3),
                    "delta": _f(r["delta"], 3),
                }
            )

    def risk_group(r: dict[str, Any]) -> str:
        """`report`: he has a game status this week; `reserve`: on a reserve list (he
        isn't expected back this week: old news, kept for who took his work);
        `uncertain`: a regular whose chance is low for another reason."""
        if r["report_status"] in STATUS_ORDER:
            return "report"
        return "reserve" if r["roster_status"] == "RES" else "uncertain"

    def stake(r: dict[str, Any]) -> float:
        """The playing time at risk: his usual snap share times the chance he sits. It puts
        a starting quarterback who may sit ahead of a rotation player who probably will."""
        p = r["p_play"] if r["p_play"] is not None else r["base_p_play"]
        return (r["base_snap"] or 0.0) * (1.0 - (p if p is not None else 1.0))

    def risk_key(r: dict[str, Any]) -> tuple:
        return (
            RISK_GROUPS.index(risk_group(r)),
            STATUS_ORDER.get(r["report_status"] or "", 3),
            -stake(r),
            -(r["base_snap"] or 0.0),
        )

    def might_sit(r: dict[str, Any]) -> bool:
        if r["report_status"] in STATUS_ORDER:
            return True
        if r["roster_status"] == "DEV":  # on the practice squad: no role to lose
            return False
        return r["at_risk"] and (r["base_snap"] or 0.0) >= RISK_ROLE_FLOOR

    risky = sorted((r for r in rows if might_sit(r)), key=risk_key)
    at_risk = risky[:AT_RISK_ROWS]
    at_risk = [
        {
            **r,
            "risk_group": risk_group(r),
            "stake": round(stake(r), 3),
            "inherit": gains.get((r["player_id"], r["game_id"]), []),
        }
        for r in at_risk
    ]
    teams = sorted({r["team"] for r in rows if r["team"]})
    if team is not None and team not in teams:
        return None

    def movers(model: str, base: str, sign: int) -> list[dict[str, Any]]:
        pool = [
            {**r, "change": round(r[model] - r[base], 3)}
            for r in rows
            if r[model] is not None
            and r[base] is not None
            and (r["p_play"] is None or r["p_play"] >= 0.5)
        ]
        pool = [r for r in pool if sign * r["change"] > 0.02]
        return sorted(pool, key=lambda r: -sign * r["change"])[:MOVER_ROWS]

    counts = {s: sum(1 for r in rows if r["report_status"] == s) for s in STATUS_ORDER}
    graded = any(r["played"] is not None for r in rows)
    views = meta.get("views") if isinstance(meta.get("views"), dict) else {}
    views = {v: d for v, d in views.items() if isinstance(d, dict)}
    return {
        **out,
        "meta": {
            "views": {
                v: {
                    "made_at": _text(views[v].get("made_at")),
                    "rows": _count(views[v].get("rows")),
                }
                for v in S.VIEWS
                if v in views
            },
            "version": clean(meta.get("version"), paths, 80),
            "trained_through": clean(meta.get("trained_through"), paths, 20),
            "shipped": shipped,
            "games_after_kickoff": _str_list(meta.get("games_after_kickoff")),
        },
        "tiles": {
            "players": len(rows),
            "at_risk": len(risky),
            "by_group": {g: sum(1 for r in risky if risk_group(r) == g) for g in RISK_GROUPS},
            "role_floor": RISK_ROLE_FLOOR,
            "out": counts["Out"],
            "doubtful": counts["Doubtful"],
            "questionable": counts["Questionable"],
            "view": "saturday" if "saturday" in views else "tuesday",
        },
        "at_risk": at_risk,
        "movers": {
            "snap_up": movers("snap_p50", "base_snap", 1),
            "snap_down": movers("snap_p50", "base_snap", -1),
            "targets_up": movers("tgt_share", "base_tgt_share", 1),
            "carries_up": movers("carry_share", "base_carry_share", 1),
        },
        "team": team,
        "players": [r for r in rows if r["team"] == team] if team else [],
        "teams": teams,
        "graded": graded,
        "scoreboard": _scoreboard(paths, season, week, "availability"),
    }


# --- Outcomes -------------------------------------------------------------------------------
def _curve_row(r: dict[str, Any]) -> dict[str, Any]:
    marks = [
        {
            "mark": _f(m["mark"], 1),
            "p_over": _f(m["p_over"], 3),
            "bell_p_over": _f(m["bell_p_over"], 3),
        }
        for m in (r["marks"] or [])
        if isinstance(m, dict)
    ]
    gap = max(
        (m for m in marks if m["p_over"] is not None and m["bell_p_over"] is not None),
        key=lambda m: abs(m["p_over"] - m["bell_p_over"]),
        default=None,
    )
    return {
        "player_id": r["player_id"],
        "player": r["player"],
        "team": r["team"],
        "opponent": r["opponent"],
        "game_id": r["game_id"],
        "position": r["position"],
        "group": r["group"],
        "stat": r["target_key"],  # the body never has a field with "key" in its name
        "target_label": r["target_label"],
        "kind": r["kind"],
        "p10": _f(r["p10"], 1),
        "p50": _f(r["p50"], 1),
        "p90": _f(r["p90"], 1),
        "mean": _f(r["mean"], 2),
        "q05": _f(r["q05"], 1),
        "q10": _f(r["q10"], 1),
        "q25": _f(r["q25"], 1),
        "q50": _f(r["q50"], 1),
        "q75": _f(r["q75"], 1),
        "q90": _f(r["q90"], 1),
        "q95": _f(r["q95"], 1),
        "band": _f(r["band"], 1),
        "p_band": _f(r["p_band"], 3),
        "bell_p_band": _f(r["bell_p_band"], 3),
        "marks": marks,
        "gap": ({**gap, "diff": round(gap["p_over"] - gap["bell_p_over"], 3)} if gap else None),
        "n_analogs": r["n_analogs"],
        "actual": _f(r["actual"], 1),
        "played": r["played"],
    }


def _curves(paths: DataPaths, season: int, week: int, cdf: bool) -> pl.DataFrame | None:
    df = read_parquet(S.week_dir(paths, season, week) / "curves.parquet")
    if df is None or df.is_empty():
        return None
    df = S.conform(df, S.CURVE_SCHEMA)
    return df if cdf else df.drop("cdf")


DEFAULT_TARGET = "rec_yds-wrte"  # the stat the Outcomes tab opens on, when it shipped


def _outcomes_empty(season: int, week: int, status: str, hint: str | None) -> dict[str, Any]:
    return {
        "season": season,
        "week": week,
        "status": status,
        "hint": hint,
        "meta": None,
        "tiles": None,
        "targets": [],
        "stat": None,
        "rows": [],
        "gaps": [],
        "graded": False,
        "scoreboard": {"week": [], "season": []},
    }


def get_outcomes(
    paths: DataPaths, season: int, week: int, target: str | None = None
) -> dict[str, Any] | None:
    """The week's curves. `rows` holds one stat only (`target`, default receiving yards or
    the first stat the week has): every stat at once is over a megabyte. `targets`, `gaps`,
    `tiles` and the scoreboard cover the whole week. None when `target` isn't a stat this
    week has (the route answers 404). A damaged or foreign file is an empty state."""
    status, hint = _state(paths, season, week, "curves")
    out = _outcomes_empty(season, week, status, hint)
    if status == "ok":
        try:
            body = _outcomes(paths, season, week, target, out)
        except Exception:  # noqa: BLE001 - "couldn't be read", never a 500
            body = _unreadable(out, week)
    else:
        body = out
    return _clean_strings(body, paths) if body is not None else None


def _outcomes(
    paths: DataPaths, season: int, week: int, target: str | None, out: dict[str, Any]
) -> dict[str, Any] | None:
    df = _curves(paths, season, week, cdf=False)
    meta = read_json(S.week_dir(paths, season, week) / "curves.json") or {}
    if df is None:
        return _unreadable(out, week)
    # only stats that shipped (the week's own list) and that the contract still knows
    shipped = _str_list(meta.get("shipped")) if "shipped" in meta else None
    rows = [
        _curve_row(r)
        for r in df.iter_rows(named=True)
        if r["target_key"] in S.CURVE_BY_KEY and (shipped is None or r["target_key"] in shipped)
    ]
    if not rows:
        return _unreadable(out, week)
    # by the projection the page shows: the median for yards, the mean for counts (a count's
    # median is a whole number, so many players tie on it)
    rows.sort(
        key=lambda r: (
            r["stat"] or "",
            -((r["mean"] if r["kind"] == "count" else r["p50"]) or 0.0),
        )
    )
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["stat"]] = counts.get(r["stat"], 0) + 1
    targets = [
        {
            "stat": t.key,  # never "key" in a field name: the leak scan masks its value
            "label": t.label,
            "group": t.group,
            "kind": t.kind,
            "marks": list(t.marks),
            "band": t.band,
            "n": counts[t.key],
        }
        for t in S.CURVE_TARGETS
        if t.key in counts
    ]
    # where the curve and the bell curve disagree most: the two biggest per stat, so one stat
    # (carries, where a Poisson is far too narrow) can't fill the list
    per_target: dict[str, list[dict[str, Any]]] = {}
    for r in sorted(
        (r for r in rows if r["gap"] is not None), key=lambda r: -abs(r["gap"]["diff"])
    ):
        got = per_target.setdefault(r["stat"], [])
        if len(got) < 2:
            got.append(r)
    gaps = sorted(
        (r for got in per_target.values() for r in got), key=lambda r: -abs(r["gap"]["diff"])
    )[:GAP_ROWS]
    if target is None:
        target = DEFAULT_TARGET if DEFAULT_TARGET in counts else (targets[0]["stat"])
    elif target not in counts:
        return None
    tiles = {
        "curves": len(rows),
        "players": len({r["player_id"] for r in rows}),
        "stats": len(counts),
    }
    graded = any(r["played"] is not None for r in rows)
    rows = [r for r in rows if r["stat"] == target]
    return {
        **out,
        "meta": {
            "made_at": _text(meta.get("made_at")),
            "rows": len(rows),
            "version": clean(meta.get("version"), paths, 80),
            "shipped": _str_list(meta.get("shipped")),
            "source_version": clean(meta.get("source_version"), paths, 80),
            "games_after_kickoff": _str_list(meta.get("games_after_kickoff")),
        },
        "tiles": tiles,
        "targets": targets,
        "stat": target,
        "rows": rows,
        "gaps": gaps,
        "graded": graded,
        "scoreboard": _scoreboard(paths, season, week, "curves"),
    }


def get_curve(
    paths: DataPaths, season: int, week: int, player_id: str, target_key: str
) -> dict[str, Any] | None:
    """One player's whole curve: `cdf[i]` = the chance of `grid_lo + i` or fewer. None when
    the week has no such curve."""
    if target_key not in S.CURVE_BY_KEY:
        return None
    try:
        return _clean_strings(_curve(paths, season, week, player_id, target_key), paths)
    except Exception:  # noqa: BLE001 - a damaged file is "no such curve"
        return None


def _curve(
    paths: DataPaths, season: int, week: int, player_id: str, target_key: str
) -> dict[str, Any] | None:
    meta = read_json(S.week_dir(paths, season, week) / "curves.json") or {}
    if "shipped" in meta and target_key not in _str_list(meta.get("shipped")):
        return None
    df = _curves(paths, season, week, cdf=True)
    if df is None:
        return None
    hit = df.filter((pl.col("player_id") == player_id) & (pl.col("target_key") == target_key))
    if hit.is_empty():
        return None
    r = hit.row(0, named=True)
    raw = r["cdf"] or []
    if any(x is None for x in raw):
        return None
    cdf = [round(float(x), 5) for x in raw]
    if not cdf or r["grid_lo"] is None:
        return None
    return {**_curve_row(r), "grid_lo": int(r["grid_lo"]), "cdf": cdf}
