"""The play browser and the reconstructed play diagrams (PC03, documentation/play-calling/).

- `get_team_plays`: a team's plays for one week of a season (its offense's plays, or on
  defense the plays run against it), filtered by down, play type and result;
- `get_play_diagram`: one play's reconstruction (`playcalling.diagrams`), with the Big Data
  Bowl concept it matches when the concept library has real examples of it.

Reads `{NFL_DATA_ROOT}/playcalling/<S>/plays_enriched.parquet` (PC00) with one short filtered
read and no memory map (D100), and the library index `playcalling/bdb_library/index.json`.
Read-only. Every free text (the play-by-play description names players) goes through
`clean()`, and team codes through `_canon`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow.parquet as pq

from nflengine.app.readers.common import clean, read_json
from nflengine.app.readers.playcalling import _canon, built_seasons
from nflengine.paths import DataPaths
from nflengine.playcalling import diagrams as D

GAME_ID_RE = re.compile(r"^(\d{4})_(\d{2})_([A-Z]{2,3})_([A-Z]{2,3})$")
DOWNS = (1, 2, 3, 4)
# filter values (the route validates them with the same lists)
TYPES = (
    "run",
    "pass",
    "sack",
    "scramble",
    "play_action",
    "screen",
    "rpo",
    "deep_shot",
    "motion",
)
RESULTS = (
    "first_down",
    "touchdown",
    "turnover",
    "explosive",
    "negative",
    "incomplete",
    "success",
)
TYPE_NAME = {
    "run": "Runs",
    "pass": "Passes",
    "sack": "Sacks",
    "scramble": "Scrambles",
    "play_action": "Play-action",
    "screen": "Screens",
    "rpo": "RPOs",
    "deep_shot": "Deep shots",
    "motion": "Motion",
}
RESULT_NAME = {
    "first_down": "First downs",
    "touchdown": "Touchdowns",
    "turnover": "Turnovers",
    "explosive": "Explosive",
    "negative": "Lost yards",
    "incomplete": "Incomplete",
    "success": "Successful",
}
LIST_COLUMNS = (
    "game_id", "play_id", "season", "week", "season_type", "order_sequence", "offense",
    "defense", "home", "quarter", "half_seconds", "down", "distance", "yards_to_goal",
    "goal_to_go", "call", "dropback", "designed_run", "pass_attempt", "sack", "scramble",
    "aborted", "pass_length", "pass_location", "air_yards", "complete", "interception",
    "run_location", "run_gap", "no_huddle", "yards_gained", "epa", "success", "first_down",
    "touchdown", "explosive", "deep_shot", "ftn_charted", "play_action", "screen", "rpo",
    "motion", "trick_play", "coverage", "desc",
)  # fmt: skip
DESC_LIMIT = 300
LIBRARY = "bdb_library"


def _plays_path(paths: DataPaths, season: int) -> Path:
    return paths.playcalling / str(season) / "plays_enriched.parquet"


def _read(path: Path, filters: list, columns: list[str] | None = None) -> pl.DataFrame | None:
    """One short, filtered read with no memory map (D100); None when missing or unreadable."""
    if not path.is_file():
        return None
    try:
        names = set(pq.read_schema(path).names)
        cols = [c for c in columns if c in names] if columns is not None else None
        table = pq.read_table(path, columns=cols, filters=filters, memory_map=False)
    except Exception:  # noqa: BLE001 - a half-written or foreign file is "no data"
        return None
    df = pl.from_arrow(table)
    return df if isinstance(df, pl.DataFrame) else None


def _matches_type(row: dict[str, Any], kind: str) -> bool:
    t = D.play_kind(row)
    if kind == "run":
        return t == "run"
    if kind == "pass":
        return t in ("pass", "sack", "scramble", "dropback")
    if kind in ("sack", "scramble"):
        return t == kind
    return row.get(kind) is True


def _matches_result(row: dict[str, Any], res: str) -> bool:
    r = D.result_of(row)
    if res == "first_down":
        return r["first_down"]
    if res == "touchdown":
        return r["touchdown"]
    if res == "turnover":
        return r["turnover"]
    if res == "explosive":
        return bool(row.get("explosive"))
    if res == "negative":
        return r["yards"] is not None and r["yards"] < 0
    if res == "incomplete":
        return r["kind"] == "incomplete"
    if res == "success":
        return bool(row.get("success"))
    return False


def library_index(paths: DataPaths) -> dict[str, Any] | None:
    """The concept library's index (`nfl playcalling library` writes it last), or None."""
    idx = read_json(paths.playcalling / LIBRARY / "index.json")
    if idx is None or not isinstance(idx.get("concepts"), dict):
        return None
    return idx


def _examples(idx: dict[str, Any] | None, concept: str) -> list[tuple[str, Any]]:
    """A concept's usable examples in the library index: (file, label) for entries whose file
    name is `<gameId>_<playId>.json` (anything else in the index is ignored)."""
    entry = (idx or {}).get("concepts", {}).get(concept)
    examples = entry.get("examples") if isinstance(entry, dict) else None
    return [
        (e["file"], e.get("label"))
        for e in (examples if isinstance(examples, list) else [])
        if isinstance(e, dict)
        and isinstance(e.get("file"), str)
        and _EXAMPLE_FILE.fullmatch(e["file"])
    ]


def _real(paths: DataPaths, concept: str | None) -> dict[str, Any] | None:
    """`real_example`: the concept's library entry, when it holds at least one usable real play
    (counted the way the clip endpoint reads them, so a button always leads to a clip)."""
    if concept is None:
        return None
    files = _examples(library_index(paths), concept)
    if not files:
        return None
    info = D.CONCEPTS.get(concept, {})
    return {
        "concept": concept,
        "name": info.get("name", concept),
        "match": info.get("match"),
        "count": len(files),
    }


def _week_label(team: str, opp: str | None, home: bool | None, week: int, stype: Any) -> str:
    where = "vs" if home else "at"
    w = f"Week {week}" if stype in (None, "REG") else f"Week {week} (playoffs)"
    return f"{w} · {where} {opp}" if opp else w


def get_team_plays(
    paths: DataPaths,
    season: int,
    team: str,
    side: str = "offense",
    week: int | None = None,
    down: int | None = None,
    kind: str | None = None,
    result: str | None = None,
) -> dict[str, Any]:
    """The play browser's list: `team`'s plays (`side` offense) or the plays run against it
    (defense) in one week; `week` None = the newest week with plays."""
    seasons = [s for s in built_seasons(paths)]
    base: dict[str, Any] = {
        "season": season,
        "seasons": seasons,
        "team": team,
        "side": side,
        "label": D.LABEL,
        "weeks": [],
        "week": None,
        "game": None,
        "filters": {"down": down, "type": kind, "result": result},
        "options": {
            "down": list(DOWNS),
            "type": [{"value": t, "label": TYPE_NAME[t]} for t in TYPES],
            "result": [{"value": r, "label": RESULT_NAME[r]} for r in RESULTS],
        },
        "counts": {"total": 0, "shown": 0},
        "plays": [],
        "library": library_index(paths) is not None,
    }
    path = _plays_path(paths, season)
    if season not in seasons or not path.is_file():
        return {
            **base,
            "status": "not_built",
            "message": f"No play-by-play tables for {season} yet "
            f"(`uv run nfl playcalling build --season {season}`).",
        }
    col = "offense" if side == "offense" else "defense"
    games = _read(
        path, [(col, "=", team)], ["week", "game_id", "offense", "defense", "home", "season_type"]
    )
    if games is None or games.is_empty():
        return {**base, "status": "no_plays", "message": f"No plays for {team} in {season}."}
    wk = (
        games.group_by("week", "game_id")
        .agg(
            pl.len().alias("plays"),
            pl.col("offense").first(),
            pl.col("defense").first(),
            pl.col("home").first(),
            pl.col("season_type").first(),
        )
        .sort("week", "game_id")
    )
    weeks = []
    for r in wk.iter_rows(named=True):
        opp = _canon(r["defense"] if side == "offense" else r["offense"])
        # `home` is the offense's; on defense the team is home when the offense isn't
        home = r["home"] if side == "offense" else (None if r["home"] is None else not r["home"])
        if not GAME_ID_RE.match(str(r["game_id"])):
            continue
        weeks.append(
            {
                "week": int(r["week"]),
                "game_id": r["game_id"],
                "opponent": opp,
                "home": home,
                "plays": int(r["plays"]),
                "label": _week_label(team, opp, home, int(r["week"]), r["season_type"]),
            }
        )
    if not weeks:
        return {**base, "status": "no_plays", "message": f"No plays for {team} in {season}."}
    available = [w["week"] for w in weeks]
    chosen = week if week in available else (None if week is not None else available[-1])
    base = {**base, "weeks": weeks}
    if chosen is None:
        return {
            **base,
            "status": "no_week",
            "message": (
                f"{team} has no plays in week {week} of {season} (a bye, or not played yet)."
            ),
        }
    game = next(w for w in weeks if w["week"] == chosen)
    df = _read(path, [(col, "=", team), ("week", "=", chosen)], list(LIST_COLUMNS))
    rows = [] if df is None else df.sort("order_sequence", "play_id").iter_rows(named=True)
    plays, total = [], 0
    for r in rows:
        if not GAME_ID_RE.match(str(r.get("game_id"))):
            continue
        total += 1
        if down is not None and r.get("down") != down:
            continue
        if kind is not None and not _matches_type(r, kind):
            continue
        if result is not None and not _matches_result(r, result):
            continue
        s = D.summary(r)
        s["offense"], s["defense"] = _canon(r.get("offense")), _canon(r.get("defense"))
        s["spot"] = D.spot_text(r.get("yards_to_goal"), s["offense"], s["defense"])
        s["desc"] = clean(s["desc"], paths, DESC_LIMIT)
        plays.append(_clean_strings(s, paths))
    return {
        **base,
        "status": "ok",
        "message": None,
        "week": chosen,
        "game": game,
        "counts": {"total": total, "shown": len(plays)},
        "plays": plays,
    }


_EXAMPLE_FILE = re.compile(r"\d{10}_\d{1,5}\.json")  # used with fullmatch: no trailing \n
LIBRARY_CMD = "uv run nfl playcalling library"
NOTE = (
    "Big Data Bowl 2026 data: NFL Next Gen Stats tracking of 2023 pass plays, used under the "
    "competition's rules (non-commercial, not redistributed), kept on this computer only."
)


def get_real_clip(paths: DataPaths, concept: str, example: int = 0) -> dict[str, Any] | None:
    """ "See a real one": the `example`-th real Big Data Bowl play of `concept` from the library
    on this computer (`nfl playcalling library`), or None for a concept the library doesn't
    know (the route answers 404). Example file names come from the index only when they match
    `<gameId>_<playId>.json`, and are read inside the concept's own folder."""
    info = D.CONCEPTS.get(concept)
    if info is None:
        return None
    idx = library_index(paths)
    base: dict[str, Any] = {
        "concept": concept,
        "name": info["name"],
        "match": info["match"],
        "note": clean((idx or {}).get("note"), paths, 300) or NOTE,
        "examples": [],
        "index": None,
        "diagram": None,
    }
    if idx is None:
        return {
            **base,
            "status": "no_library",
            "message": "The Big Data Bowl library isn't built on this computer "
            f"(`{LIBRARY_CMD}`, after the data is downloaded).",
        }
    files = _examples(idx, concept)
    if not files:
        return {**base, "status": "none", "message": "No real example of this concept yet."}
    i = min(max(example, 0), len(files) - 1)
    listed = [
        {"index": k, "label": clean(lbl, paths, 160) or f"Example {k + 1}"}
        for k, (_, lbl) in enumerate(files)
    ]
    d = read_json(paths.playcalling / LIBRARY / concept / files[i][0])
    if d is None or d.get("kind") != "real" or not isinstance(d.get("play"), dict):
        return {
            **base,
            "examples": listed,
            "status": "none",
            "message": "That example can't be read.",
        }
    d = _clean_strings(d, paths)  # every text in the file, not only the description
    d["play"]["desc"] = clean(d["play"].get("desc"), paths, DESC_LIMIT)
    for k in ("offense", "defense"):
        d["play"][k] = _canon(d["play"].get(k))
    return {**base, "status": "ok", "message": None, "examples": listed, "index": i, "diagram": d}


def _clean_strings(obj: Any, paths: DataPaths) -> Any:
    """A library file's every string through `clean()` (its texts are made by our code from the
    Big Data Bowl's labels; cleaned anyway, like every file-derived text)."""
    if isinstance(obj, str):
        return clean(obj, paths, DESC_LIMIT)
    if isinstance(obj, dict):
        return {k: _clean_strings(v, paths) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_strings(v, paths) for v in obj]
    return obj


def _positions(paths: DataPaths, ids: list[Any]) -> dict[str, str]:
    """Position groups ('RB', 'WR', 'TE', 'QB' ...) of these players from curated `players`
    (one filtered read); {} when the table or the ids are missing."""
    want = [i for i in ids if isinstance(i, str) and i]
    if not want:
        return {}
    df = _read(
        paths.curated / "players.parquet", [("gsis_id", "in", want)], ["gsis_id", "position_group"]
    )
    if df is None or "position_group" not in df.columns:
        return {}
    return {
        r["gsis_id"]: r["position_group"] for r in df.iter_rows(named=True) if r["position_group"]
    }


def get_play_diagram(paths: DataPaths, game_id: str, play_id: int) -> dict[str, Any] | None:
    """One play's reconstruction, or None when the game id is malformed, its season isn't
    built, or the play isn't in the season's enriched plays (the route answers 404)."""
    m = GAME_ID_RE.match(game_id)
    if m is None:
        return None
    season = int(m.group(1))
    path = _plays_path(paths, season)
    if season not in built_seasons(paths) or not path.is_file():
        return None
    game = _read(path, [("game_id", "=", game_id)])
    if game is None or game.is_empty():
        return None
    hit = game.filter(pl.col("play_id") == float(play_id))
    if hit.is_empty():
        return None
    row = hit.row(0, named=True)
    qbs = (
        game.filter(pl.col("offense") == row.get("offense"))
        .get_column("passer_id")
        .drop_nulls()
        .unique()
        .to_list()
        if "passer_id" in game.columns
        else []
    )
    off, dfn = _canon(row.get("offense")), _canon(row.get("defense"))
    row = {**row, "offense": off, "defense": dfn}
    d = D.diagram(row, qbs, _positions(paths, [row.get("receiver_id"), row.get("rusher_id")]))
    d["play"]["desc"] = clean(d["play"]["desc"], paths, DESC_LIMIT)
    d["play"]["game_label"] = (
        f"{_canon(m.group(3))} at {_canon(m.group(4))}, week {int(m.group(2))}"
        if _canon(m.group(3)) and _canon(m.group(4))
        else None
    )
    d["play"]["season"] = season
    d["real_example"] = _real(paths, d.get("concept"))
    # every string of the answer, not only the description: the known / approximate lines
    # quote file values (a coverage name, a route) (Sol, PC03)
    return {"status": "ok", "diagram": _clean_strings(d, paths)}
