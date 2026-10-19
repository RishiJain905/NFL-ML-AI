"""`nfl history-graph append --season 2026 [--through-week W]` (AE02): the current season's
games, drives, plays, pbp roles, rosters and coaches, written into the history graph with
batched `UNWIND` (participation comes only with the rebuild after the season, when nflverse
publishes it).

**Idempotent by construction:** the season's part of the graph is synced to the export, not
added to. The append reads what the graph holds for the season (its Game / Drive / Play nodes,
the relationships they own, the season's PLAYED_FOR / HEAD_COACH / coordinator links, and the
Season / Team / Player / Coach nodes the export names), compares it with the export's frames
(`export.build_frames`, the same as the bulk import's), and writes only the difference:
create what's new, update what changed (nflverse corrects plays days later), delete what's
gone. A second run on the same data writes nothing; the result is checked by reading again
(`check` must be empty). After `--through-week W` the season holds exactly weeks 1..W.

Who owns which relationships: a Game / Drive / Play of the season owns its outgoing
structural links (IN_SEASON, HOME_TEAM, AWAY_TEAM, IN_GAME, IN_DRIVE, OFFENSE, DEFENSE); a Play
of the season owns the links that end at it (ON_FIELD, the pbp roles); PLAYED_FOR, HEAD_COACH
and the coordinator links are the season's by their `season` property. Older seasons (the bulk
import's) are never read or written: a season up to the build's `through_season` is refused.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from typing import Any

from nflengine.history_graph import schema as S
from nflengine.history_graph import state
from nflengine.history_graph.config import HistoryConfig, history_config
from nflengine.paths import DataPaths, ensure_data_root

SEASON_LABELS = ("Game", "Drive", "Play")  # nodes that belong to a season (`season` property)
SHARED_LABELS = ("Season", "Team", "Coach", "Player")  # MERGEd: other seasons use them too
OWNED_BY_START = ("IN_SEASON", "HOME_TEAM", "AWAY_TEAM", "IN_GAME", "IN_DRIVE", "OFFENSE",
                  "DEFENSE")  # fmt: skip
OWNED_BY_END = ("ON_FIELD", *S.ROLE_TYPES)


class HistoryAppendError(RuntimeError):
    """The append can't run (no build, a bulk-imported season, the lock); fixed text."""


def _clean(v: Any) -> Any:
    """NaN and blank text are no value, as in the bulk import (`export._csv_ready` writes an
    empty field, which the importer stores as no property)."""
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


def _props(row: dict[str, Any], drop: Iterable[str] = ()) -> dict[str, Any]:
    """A row's properties as Neo4j will hold them: no nulls (Neo4j doesn't store them)."""
    skip = set(drop)
    out = {}
    for k, v in row.items():
        if k in skip:
            continue
        v = _clean(v)
        if v is None:
            continue
        out[k] = v
    return out


def _rel_id(rtype: str, s: Any, e: Any, p: dict[str, Any]) -> tuple:
    """A relationship's identity: its ends, plus the season for the season-owned types (a
    player has one PLAYED_FOR a team per season)."""
    return (s, e, p.get("season")) if rtype in S.SEASON_TYPES else (s, e)


# ---- reading what the graph holds ------------------------------------------------------------


def _read(driver, query: str, **params) -> list[dict[str, Any]]:
    with driver.session() as s:
        return [r.data() for r in s.run(query, **params)]


def read_season_nodes(driver, label: str, season: int) -> dict[Any, dict[str, Any]]:
    key = S.key_of(label)
    rows = _read(
        driver,
        f"MATCH (n:{label}) WHERE n.season = $season RETURN n.{key} AS k, properties(n) AS p",
        season=season,
    )
    return {r["k"]: r["p"] for r in rows}


def read_nodes(driver, label: str, keys: list[Any], batch: int) -> dict[Any, dict[str, Any]]:
    key = S.key_of(label)
    out: dict[Any, dict[str, Any]] = {}
    for i in range(0, len(keys), batch):
        rows = _read(
            driver,
            f"UNWIND $keys AS k MATCH (n:{label} {{{key}: k}}) RETURN k, properties(n) AS p",
            keys=keys[i : i + batch],
        )
        out.update({r["k"]: r["p"] for r in rows})
    return out


def read_owned_rels(driver, rtype: str, season: int) -> dict[tuple, dict[str, Any]]:
    spec = S.RELATIONSHIPS[rtype]
    sk, ek = S.key_of(spec.start), S.key_of(spec.end)
    if rtype in OWNED_BY_START:
        where = "a.season = $season"
    elif rtype in OWNED_BY_END:
        where = "b.season = $season"
    else:
        where = "x.season = $season"
    rows = _read(
        driver,
        f"MATCH (a:{spec.start})-[x:{rtype}]->(b:{spec.end}) WHERE {where} "
        f"RETURN a.{sk} AS s, b.{ek} AS e, properties(x) AS p",
        season=season,
    )
    return {_rel_id(rtype, r["s"], r["e"], r["p"]): r["p"] for r in rows}


# ---- the diff ---------------------------------------------------------------------------------


def node_rows(df, label: str) -> dict[Any, dict[str, Any]]:
    key = S.key_of(label)
    return {r[key]: _props(r) for r in df.to_dicts()}


def rel_rows(df, rtype: str) -> dict[tuple, tuple[Any, Any, dict[str, Any]]]:
    out = {}
    for r in df.to_dicts():
        p = _props(r, drop=("s", "e"))
        out[_rel_id(rtype, r["s"], r["e"], p)] = (r["s"], r["e"], p)
    return out


def diff_nodes(want: dict[Any, dict], have: dict[Any, dict], delete: bool) -> dict[str, list]:
    create = [want[k] for k in want if k not in have]
    update = [want[k] for k in want if k in have and have[k] != want[k]]
    gone = [k for k in have if k not in want] if delete else []
    return {"create": create, "update": update, "delete": gone}


def diff_rels(want: dict[tuple, tuple], have: dict[tuple, dict]) -> dict[str, list]:
    create = [w for i, w in want.items() if i not in have]
    update = [w for i, w in want.items() if i in have and have[i] != w[2]]
    gone = [i for i in have if i not in want]
    return {"create": create, "update": update, "delete": gone}


# ---- writing ----------------------------------------------------------------------------------


def _write(driver, query: str, rows: list, batch: int) -> None:
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        with driver.session() as s:
            s.execute_write(lambda tx, c=chunk: tx.run(query, rows=c).consume())


def write_nodes(driver, label: str, d: dict[str, list], batch: int) -> None:
    key = S.key_of(label)
    if d["delete"]:
        _write(driver, f"UNWIND $rows AS k MATCH (n:{label} {{{key}: k}}) DETACH DELETE n",
               d["delete"], batch)  # fmt: skip
    if d["create"]:
        _write(driver, f"UNWIND $rows AS p MERGE (n:{label} {{{key}: p.{key}}}) SET n = p",
               d["create"], batch)  # fmt: skip
    if d["update"]:
        _write(driver, f"UNWIND $rows AS p MATCH (n:{label} {{{key}: p.{key}}}) SET n = p",
               d["update"], batch)  # fmt: skip


def write_rels(driver, rtype: str, d: dict[str, list], batch: int) -> None:
    spec = S.RELATIONSHIPS[rtype]
    sk, ek = S.key_of(spec.start), S.key_of(spec.end)
    season = rtype in S.SEASON_TYPES
    match = f"MATCH (a:{spec.start} {{{sk}: r.s}})-[x:{rtype}]->(b:{spec.end} {{{ek}: r.e}})"
    where = " WHERE x.season = r.season" if season else ""
    stale = [{"s": i[0], "e": i[1], "season": i[2] if season else None} for i in d["delete"]]
    # an update = the old relationship out, the new one in (its properties replaced whole)
    stale += [{"s": s, "e": e, "season": p.get("season")} for s, e, p in d["update"]]
    if stale:
        _write(driver, f"UNWIND $rows AS r {match}{where} DELETE x", stale, batch)
    fresh = [{"s": s, "e": e, "p": p} for s, e, p in (*d["create"], *d["update"])]
    if fresh:
        _write(
            driver,
            f"UNWIND $rows AS r MATCH (a:{spec.start} {{{sk}: r.s}}) "
            f"MATCH (b:{spec.end} {{{ek}: r.e}}) CREATE (a)-[x:{rtype}]->(b) SET x = r.p",
            fresh,
            batch,
        )


# ---- the sync ---------------------------------------------------------------------------------


def plan(driver, frames, season: int, batch: int) -> dict[str, dict[str, list]]:
    """What the graph needs to match the frames for `season` (nothing when it already does)."""
    out: dict[str, dict[str, list]] = {}
    for label in SHARED_LABELS:
        want = node_rows(frames.nodes[label], label)
        have = read_nodes(driver, label, list(want), batch)
        out[label] = diff_nodes(want, have, delete=False)
    for label in SEASON_LABELS:
        want = node_rows(frames.nodes[label], label)
        out[label] = diff_nodes(want, read_season_nodes(driver, label, season), delete=True)
    return out


def plan_rels(driver, frames, season: int) -> dict[str, dict[str, list]]:
    return {
        rtype: diff_rels(
            rel_rows(frames.rels[rtype], rtype), read_owned_rels(driver, rtype, season)
        )
        for rtype in S.RELATIONSHIPS
    }


def changes(p: dict[str, dict[str, list]]) -> dict[str, dict[str, int]]:
    return {k: {op: len(v) for op, v in d.items() if v} for k, d in p.items() if any(d.values())}


def sync(driver, frames, season: int, batch: int, log: Callable[[str], None] = print) -> dict:
    """Make the graph's `season` equal the frames; returns `{changes, check}` (`check` is the
    plan after writing: empty when the graph matches)."""
    from nflengine.history_graph.build import apply_schema

    apply_schema(driver)
    nodes = plan(driver, frames, season, batch)
    # deletes first (DETACH removes their links), then shared nodes, then the season's
    for label in SEASON_LABELS[::-1]:
        if nodes[label]["delete"]:
            only = {"create": [], "update": [], "delete": nodes[label]["delete"]}
            write_nodes(driver, label, only, batch)
    for label in (*SHARED_LABELS, *SEASON_LABELS):
        write_nodes(driver, label, {**nodes[label], "delete": []}, batch)
    rels = plan_rels(driver, frames, season)
    for rtype, d in rels.items():
        write_rels(driver, rtype, d, batch)
    done = {**changes(nodes), **changes(rels)}
    after = {**plan(driver, frames, season, batch), **plan_rels(driver, frames, season)}
    check = changes(after)
    log(f"history graph append: {sum(sum(v.values()) for v in done.values()):,} writes "
        f"({', '.join(f'{k} {v}' for k, v in done.items()) or 'nothing to change'})")  # fmt: skip
    return {"changes": done, "check": check}


def newest_week(paths: DataPaths, season: int) -> int | None:
    import polars as pl

    f = paths.curated / "plays" / f"season={season}.parquet"
    if not f.exists():
        return None
    w = pl.read_parquet(f, columns=["week"], memory_map=False)["week"].max()
    return int(w) if w is not None else None


def run_append(
    season: int | None = None,
    through_week: int | None = None,
    *,
    paths: DataPaths | None = None,
    cfg: HistoryConfig | None = None,
    driver=None,
    use_wandb: bool = True,
    launched_by: str | None = None,
    smoke: bool = False,
    log: Callable[[str], None] = print,
    locked: bool = False,
    start: bool = False,
) -> dict[str, Any]:
    """Sync the current season (default `seasons.current`) through `through_week` (default:
    the newest week with plays) into the history graph; writes `append.json`. `start`: start
    the history container if it's down, under the lock (Sol: a start outside the lock could
    open a store a build was replacing). `locked`: the caller (a build) holds the lock."""
    from nflengine.history_graph.build import weekly_lock_held
    from nflengine.settings import get_config

    paths = paths or ensure_data_root(create_dirs=False)
    cfg = cfg or history_config()
    season = season if season is not None else get_config().seasons.current
    if not locked and weekly_lock_held(paths):
        raise HistoryAppendError(
            "the weekly run (or the injury update / Update play calling) holds the weekly lock: "
            "append after it"
        )

    def checked_run() -> dict[str, Any]:
        # everything about the build is read under the lock (Sol): a build finishing between a
        # check and the writes could otherwise let an append rewrite a bulk-imported season
        build = state.read_build(paths)
        if not build:
            raise HistoryAppendError(
                "no history-graph build yet: run `nfl history-graph build` first"
            )
        if season <= int(build["through_season"]):
            raise HistoryAppendError(
                f"{season} is in the bulk import (through {build['through_season']}): rebuild "
                "instead (`nfl history-graph build`)"
            )
        left = state.read_unfinished(paths)
        if left is not None and not (
            left.get("what") == "append"
            and left.get("season") == season
            and left.get("build_id") == build["build_id"]
        ):  # only the same unfinished append may be finished by this run
            raise HistoryAppendError(state.unfinished_advice(left))
        week = through_week if through_week is not None else newest_week(paths, season)
        if not week:
            raise HistoryAppendError(f"no curated plays for {season} yet")
        if start:
            _start_if_down(cfg, log)
        return _append(paths, cfg, build, season, week, driver, use_wandb, launched_by, smoke, log)

    if locked:
        return checked_run()
    from nflengine.ops.lock import LockHeld, run_lock

    try:
        with run_lock(state.lock_path(paths), f"nfl history-graph append --season {season}"):
            return checked_run()
    except LockHeld as e:
        raise HistoryAppendError(
            f"another history-graph build or append is running (pid {e.holder.get('pid')})"
        ) from None


def _start_if_down(cfg: HistoryConfig, log: Callable[[str], None]) -> None:
    from nflengine.history_graph.client import history_up
    from nflengine.history_graph.docker import start

    if history_up(cfg):
        return
    res = start(cfg, log)
    if not res.ok:
        raise HistoryAppendError(f"history graph not reachable: {res.detail}")


def _append(paths, cfg, build, season, week, driver, use_wandb, launched_by, smoke, log):
    from nflengine.history_graph.build import _write_json, utc_now
    from nflengine.history_graph.export import build_frames, load_inputs

    t0 = time.monotonic()
    own = driver is None
    if own:
        from nflengine.history_graph.client import get_history_driver

        driver = get_history_driver(cfg=cfg)
    try:
        t = time.monotonic()
        frames = build_frames(
            load_inputs(paths, [season], through_week=week, participation=False, log=log), log=log
        )
        t_export = round(time.monotonic() - t, 1)
        t = time.monotonic()
        # until the result is checked, Ask doesn't read the graph (Sol): a failed batch or a
        # crash leaves the season half-written, and the marker says so until a good append
        state.mark_unfinished(paths, "append", build["build_id"], season)
        res = sync(driver, frames, season, cfg.batch_size, log)
        t_sync = round(time.monotonic() - t, 1)
    finally:
        if own:
            driver.close()
    plays = frames.nodes["Play"]
    doc = {
        "status": "ok" if not res["check"] else "failed",
        "build_id": build["build_id"],
        "season": season,
        "through_week": week,
        "weeks": sorted(plays["week"].unique().to_list()),
        "games": frames.nodes["Game"].height,
        "plays": plays.height,
        "counts": frames.expected_counts(),
        "changes": res["changes"],
        "check": res["check"],
        "dropped": frames.dropped,
        "appended_at": utc_now().isoformat(),
        "seconds": {"export": t_export, "sync": t_sync, "total": round(time.monotonic() - t0, 1)},
    }
    _wandb_append(doc, use_wandb, launched_by, smoke, log)
    _write_json(state.append_path(paths), doc)
    if not res["check"]:
        state.clear_unfinished(paths)
    if res["check"]:
        raise HistoryAppendError(
            "the graph still differs from the export after the append: "
            + ", ".join(sorted(res["check"]))
        )
    return doc


def _wandb_append(doc: dict[str, Any], use_wandb: bool, launched_by, smoke, log) -> None:
    if not use_wandb:
        return
    try:
        from nflengine.tracking import git_commit, init_run

        wb = init_run(
            group="history-graph-smoke" if smoke else "history-graph",
            job_type="history-graph-append",
            config={
                "season": doc["season"],
                "through_week": doc["through_week"],
                "build_id": doc["build_id"],
                "git_commit": git_commit(),
            },  # fmt: skip
            tags=["history-graph", *(["smoke"] if smoke else [])],
            launched_by=launched_by,
            name=f"history-graph-append-{doc['season']}-w{doc['through_week']:02d}",
        )
        writes = {f"writes/{k}": sum(v.values()) for k, v in doc["changes"].items()}
        wb.log({**writes, **{f"time/{k}": v for k, v in doc["seconds"].items()}})
        wb.summary.update(
            {
                "status": doc["status"],
                "plays": doc["plays"],
                "games": doc["games"],
                "writes": sum(writes.values()),
                **{f"count/{k}": v for k, v in doc["counts"].items()},
            }
        )
        doc["wandb_url"] = wb.url
        wb.finish(exit_code=0 if doc["status"] == "ok" else 1)
    except Exception as e:  # W&B never sinks an append
        log(f"[yellow]W&B off for this append ({type(e).__name__})[/]")
