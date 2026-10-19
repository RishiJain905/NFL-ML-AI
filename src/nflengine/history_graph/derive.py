"""`nfl history-graph derive` (AE03, D147): person-to-person relationships derived from the
plays, written into the history graph after a build or an append.

What it makes (`schema.DERIVED`):
- **TEAMMATE** `{snaps, seasons, teams, stints}`: two players on the field together on the
  same side (ON_FIELD: participation, 2018-2025), one link per pair (smaller id first);
- **CONNECTION** `{targets, catches, yards, touchdowns, epa, seasons, teams}`: QB -> receiver,
  from the pass attempts he threw him (PASSED + TARGETED on one play; every season, the
  appended current one too);
- **OPPONENT** `{plays, seasons}`: offense -> defense on the same scrimmage plays, the classic
  matchups only (skill vs DB, OL vs DL, the QB vs any defender: the full set is 1.85M pairs);
- **COACHED** `{snaps, seasons, teams, roles, stints}`: coach -> player, the head coach (his
  weeks) or the coordinator of the player's unit, while the player took snaps in it (curated
  snap counts, so the current season and the linemen count); one stint per season, team and
  role with that role's snaps, and `snaps` counts each snap once (a head coach who was also
  a coordinator);
- **WORKED_WITH** `{snaps, games, seasons, teams, stints}`: coaches on the same staff (head
  coach and coordinators) in the same season, over the weeks both were there (a head coach's
  own weeks), one link per pair.

How: the same export the bulk import and the append use (`export.load_inputs` +
`build_frames`, for the build's seasons and the appended weeks), aggregated in an in-memory
DuckDB (about 40M teammate pair-events -> 237k links), then **synced** like the append: the
links in the graph are read, compared with the computed ones, and only the difference is
written (a re-run writes nothing; the check re-reads and must find nothing to do). Until the
check passes, the unfinished marker says "derive" (Ask doesn't read a half-written graph).

Writes `neo4j-history/derive.json` (counts, changes, times, the build and the week it covers);
`run_derive` is called at the end of `build` and `append` (their documents get a `derive`
section) and by `nfl history-graph derive` (its own W&B run, `history-graph-derive`). Never
touches the weekly graph, and nothing here feeds a weekly model or the digest (D107).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import polars as pl

from nflengine.history_graph import schema as S
from nflengine.history_graph import state
from nflengine.history_graph.config import HistoryConfig, history_config
from nflengine.paths import DataPaths, ensure_data_root

DERIVE_VERSION = 1
DERIVE_FILE = "derive.json"

# position classes for OPPONENT (ON_FIELD.position: the spot on the play from 2023, the roster
# position before)
POSITION_CLASSES: dict[str, tuple[str, ...]] = {
    "skill": ("WR", "TE", "RB", "FB", "HB"),
    "qb": ("QB",),
    "ol": ("OL", "T", "G", "C", "OT", "OG"),
    "dl": ("DL", "DE", "DT", "NT"),
    "lb": ("LB", "OLB", "ILB", "MLB"),
    "db": ("DB", "CB", "S", "SS", "FS", "SAF"),
}
# (offense class, defense classes): the matchups OPPONENT keeps (D147)
MATCHUPS: dict[str, tuple[str, ...]] = {
    "skill": ("db",),
    "ol": ("dl",),
    "qb": ("dl", "lb", "db"),
}
UNITS = {"HC": ("off", "def", "st"), "OC": ("off",), "DC": ("def",), "ST": ("st",)}
STAFF_TYPES = {"HEAD_COACH": "HC", "OFF_COORD": "OC", "DEF_COORD": "DC", "ST_COORD": "ST"}


class HistoryDeriveError(RuntimeError):
    """The derive can't run or didn't finish (fixed, secret-free text)."""


def derive_path(paths: DataPaths):
    return paths.neo4j_history / DERIVE_FILE


# ---- inputs -----------------------------------------------------------------------------------


def coverage(build: dict[str, Any], app: dict[str, Any] | None) -> dict[str, Any]:
    """What the graph holds (and so what to derive from): the build's seasons, plus the appended
    season through its week."""
    seasons = list(range(int(build["first_season"]), int(build["through_season"]) + 1))
    season = week = None
    if app and app.get("through_week"):
        season, week = int(app["season"]), int(app["through_week"])
        if season not in seasons:
            seasons.append(season)
    return {"seasons": seasons, "season": season, "through_week": week}


def read_snaps(paths: DataPaths, seasons: list[int], current: int | None, week: int | None):
    """Curated snap counts (PFR) per (player, team, season, week): offense / defense / special
    teams, the appended season only through its week; rows without a GSIS id dropped, a
    duplicated (player, game) row kept once (the curated-data skill's quirk)."""
    f = paths.curated / "snaps.parquet"
    cols = ["gsis_id", "game_id", "team", "season", "week", "offense_snaps", "defense_snaps",
            "st_snaps"]  # fmt: skip
    df = pl.read_parquet(f, columns=cols, memory_map=False).filter(
        pl.col("season").is_in(seasons) & pl.col("gsis_id").is_not_null()
    )
    if current is not None and week is not None:
        df = df.filter((pl.col("season") != current) | (pl.col("week") <= week))
    return (
        df.unique(["gsis_id", "game_id"], keep="first", maintain_order=True)
        .group_by("gsis_id", "team", "season", "week")
        .agg(
            pl.col("offense_snaps").fill_null(0).sum().cast(pl.Int64).alias("off"),
            pl.col("defense_snaps").fill_null(0).sum().cast(pl.Int64).alias("def"),
            pl.col("st_snaps").fill_null(0).sum().cast(pl.Int64).alias("st"),
        )
        .rename({"gsis_id": "p"})
        .with_columns(pl.col("season").cast(pl.Int64), pl.col("week").cast(pl.Int64))
    )


# ---- the computation --------------------------------------------------------------------------


def _class_case(col: str) -> str:
    parts = [
        f"WHEN {col} IN ({', '.join(repr(p) for p in pos)}) THEN '{cls}'"
        for cls, pos in POSITION_CLASSES.items()
    ]
    return "CASE " + " ".join(parts) + " ELSE 'other' END"


def _matchup_sql() -> str:
    return " OR ".join(
        f"(a.cls = '{o}' AND b.cls IN ({', '.join(repr(d) for d in ds)}))"
        for o, ds in MATCHUPS.items()
    )


def derived_frames(frames, snaps: pl.DataFrame) -> tuple[dict[str, pl.DataFrame], dict[str, int]]:
    """Every derived relationship frame (`schema.DERIVED`'s columns and types), and what was
    dropped (snap-count rows of players or teams the graph doesn't hold)."""
    import duckdb

    from nflengine.history_graph.export import _conform

    plays = frames.nodes["Play"].select(
        "play_key", "game_id", "season", "offense", "defense", "play_type", "two_point",
        "complete", "yards", "epa", "touchdown", "fumble_lost",
    ).with_row_index("k")  # fmt: skip
    of = frames.rels["ON_FIELD"].select("s", "e", "side", "position")
    on = of.join(
        plays.select(
            pl.col("play_key").alias("e"),
            "k",
            "game_id",
            "season",
            "offense",
            "defense",
            "play_type",
            "two_point",
        ),  # fmt: skip
        on="e",
        how="inner",
    ).select(
        pl.col("s").alias("p"),
        "k",
        "game_id",
        "side",
        "position",
        pl.col("season").cast(pl.Int64),
        pl.when(pl.col("side") == "offense")
        .then(pl.col("offense"))
        .otherwise(pl.col("defense"))
        .alias("team"),
        (pl.col("play_type").is_in(["pass", "run"]) & ~pl.col("two_point").fill_null(False)).alias(
            "scrim"
        ),
    )
    # a player's team in a game is the team he was on the field for most often in it: on a few
    # special-teams snaps participation's side and nflverse's possession team disagree (the UI
    # worker, AE03: "teammates on ARI 2021" for two Rams), so a stint's team never comes from
    # one play
    # (a play without a team, which nflverse doesn't have today, never decides it: a null
    # can't be stored in a stint's list, the test worker)
    game_team = (
        on.filter(pl.col("team").is_not_null())
        .group_by("p", "game_id", "team")
        .len()
        .sort(["p", "game_id", "len", "team"], descending=[False, False, True, False])
        .group_by("p", "game_id", maintain_order=True)
        .first()
        .select("p", "game_id", pl.col("team").alias("game_team"))
    )
    on = (
        on.join(game_team, on=["p", "game_id"], how="left")
        .drop("team", "game_id")
        .rename({"game_team": "team"})
    )
    players = frames.nodes["Player"].select(pl.col("player_id").alias("p"))
    coaches = frames.nodes["Coach"].select(pl.col("coach_id").alias("c"))
    teams = frames.nodes["Team"].select(pl.col("team_id").alias("team"))
    staff = pl.concat(
        [
            frames.rels[rtype].select(
                pl.col("s").alias("c"),
                pl.col("e").alias("team"),
                pl.col("season").cast(pl.Int64),
                pl.lit(role).alias("role"),
                (pl.col("first_week") if rtype == "HEAD_COACH" else pl.lit(0))
                .cast(pl.Int64)
                .alias("w0"),
                (pl.col("last_week") if rtype == "HEAD_COACH" else pl.lit(99))
                .cast(pl.Int64)
                .alias("w1"),
            )
            for rtype, role in STAFF_TYPES.items()
        ]
    )
    # a team's plays (both sides) and games per week: staff links count only the weeks both
    # coaches were there (an interim head coach's, Sol)
    week_cols = [pl.col("season").cast(pl.Int64), pl.col("week").cast(pl.Int64)]
    pnodes = frames.nodes["Play"]
    team_plays = (
        pl.concat(
            [
                pnodes.select(*week_cols, pl.col(side).alias("team"))
                for side in ("offense", "defense")
            ]
        )  # fmt: skip
        .group_by("season", "week", "team")
        .agg(pl.len().cast(pl.Int64).alias("plays"))
    )
    games = frames.nodes["Game"]
    team_games = (
        pl.concat(
            [
                games.select(*week_cols, pl.col(f"{s}_team").alias("team"), "game_id")
                for s in ("home", "away")
            ]  # fmt: skip
        )
        .group_by("season", "week", "team")
        .agg(pl.col("game_id").n_unique().cast(pl.Int64).alias("games"))
    )
    team_weeks = team_plays.join(
        team_games, on=["season", "week", "team"], how="full", coalesce=True
    ).with_columns(pl.col("plays").fill_null(0), pl.col("games").fill_null(0))
    role_units = pl.DataFrame(
        [(role, unit) for role, units in UNITS.items() for unit in units],
        schema={"role": pl.String, "unit": pl.String},
        orient="row",
    )

    known = snaps.join(players, on="p", how="semi").join(teams, on="team", how="semi")
    dropped = {"snaps_rows_unknown": snaps.height - known.height}

    con = duckdb.connect()
    try:
        con.execute("SET preserve_insertion_order = false")
        for name, df in {
            "o": on,
            "plays": plays,
            "passed": frames.rels["PASSED"].select("s", "e"),
            "targeted": frames.rels["TARGETED"].select("s", "e"),
            "sn": known,
            "staff": staff,
            "role_units": role_units,
            "team_weeks": team_weeks,
        }.items():
            con.register(name, df.to_arrow())
        out = {
            "TEAMMATE": _teammate(con),
            "CONNECTION": _connection(con),
            "OPPONENT": _opponent(con),
            "COACHED": _coached(con),
            "WORKED_WITH": _worked_with(con),
        }
    finally:
        con.close()

    keys = {"Player": players["p"], "Coach": coaches["c"]}
    res: dict[str, pl.DataFrame] = {}
    for rtype, df in out.items():
        spec = S.DERIVED[rtype]
        ok = df.filter(
            pl.col("s").is_in(keys[spec.start].implode())
            & pl.col("e").is_in(keys[spec.end].implode())
        )
        if ok.height != df.height:
            dropped[rtype] = df.height - ok.height
        res[rtype] = _conform(ok, spec).sort("s", "e")
    return res, dropped


def _teammate(con) -> pl.DataFrame:
    con.execute(
        """
        CREATE TEMP TABLE tm AS
        SELECT a.p AS s, b.p AS e, a.season, a.team, count(*)::BIGINT AS snaps
        FROM o a JOIN o b ON a.k = b.k AND a.side = b.side AND a.p < b.p
        WHERE a.team IS NOT NULL
        GROUP BY ALL
        """
    )
    return con.sql(
        """
        SELECT s, e, sum(snaps)::BIGINT AS snaps,
          list_sort(list_distinct(list(season))) AS seasons,
          list_sort(list_distinct(list(team))) AS teams,
          min(season) AS first_season, max(season) AS last_season,
          list(season ORDER BY season, team) AS stint_seasons,
          list(team ORDER BY season, team) AS stint_teams,
          list(snaps ORDER BY season, team) AS stint_snaps
        FROM tm GROUP BY s, e
        """
    ).pl()


def _connection(con) -> pl.DataFrame:
    return con.sql(
        """
        SELECT pa.s AS s, ta.s AS e, count(*)::BIGINT AS targets,
          sum(CASE WHEN p.complete THEN 1 ELSE 0 END)::BIGINT AS catches,
          sum(CASE WHEN p.complete THEN coalesce(p.yards, 0) ELSE 0 END)::BIGINT AS yards,
          sum(CASE WHEN p.complete AND p.touchdown AND NOT coalesce(p.fumble_lost, false)
                   THEN 1 ELSE 0 END)::BIGINT AS touchdowns,
          round(fsum(coalesce(p.epa, 0)), 3) AS epa,
          list_sort(list_distinct(list(p.season::BIGINT))) AS seasons,
          list_sort(list_distinct(list(p.offense))) AS teams,
          min(p.season)::BIGINT AS first_season, max(p.season)::BIGINT AS last_season
        FROM passed pa JOIN targeted ta ON pa.e = ta.e JOIN plays p ON p.play_key = pa.e
        WHERE pa.s <> ta.s
        GROUP BY pa.s, ta.s
        """
    ).pl()


def _opponent(con) -> pl.DataFrame:
    return con.sql(
        f"""
        WITH c AS (SELECT p, k, side, season, {_class_case("position")} AS cls FROM o WHERE scrim)
        SELECT a.p AS s, b.p AS e, count(*)::BIGINT AS plays,
          list_sort(list_distinct(list(a.season))) AS seasons,
          min(a.season) AS first_season, max(a.season) AS last_season
        FROM c a JOIN c b ON a.k = b.k AND a.side = 'offense' AND b.side = 'defense'
        WHERE {_matchup_sql()}
        GROUP BY a.p, b.p
        """
    ).pl()


def _unit_snaps_sql() -> str:
    parts = []
    for role, units in UNITS.items():
        expr = " + ".join(f"sn.{u}" for u in units)
        parts.append(f"WHEN '{role}' THEN {expr}")
    return "CASE sf.role " + " ".join(parts) + " END"


def _coached(con) -> pl.DataFrame:
    # one stint per season, team and role, with the snaps in that role's units (a role asked
    # for alone gets its own count, Sol)
    con.execute(
        f"""
        CREATE TEMP TABLE cs AS
        SELECT sf.c AS s, sn.p AS e, sf.season, sf.team, sf.role,
          sum({_unit_snaps_sql()})::BIGINT AS snaps
        FROM staff sf JOIN sn ON sn.team = sf.team AND sn.season = sf.season
          AND sn.week BETWEEN sf.w0 AND sf.w1
        GROUP BY ALL HAVING sum({_unit_snaps_sql()}) > 0
        """
    )
    # the link's total counts each snap once: a week's snaps in every unit one of his roles
    # covered that week (a head coach who is also a coordinator isn't counted twice)
    con.execute(
        """
        CREATE TEMP TABLE ct AS
        WITH cu AS (
          SELECT DISTINCT sf.c AS s, sn.p AS e, sf.season, sf.team, sn.week, u.unit,
            CASE u.unit WHEN 'off' THEN sn.off WHEN 'def' THEN sn.def ELSE sn.st END AS n
          FROM staff sf JOIN role_units u ON u.role = sf.role
          JOIN sn ON sn.team = sf.team AND sn.season = sf.season
            AND sn.week BETWEEN sf.w0 AND sf.w1
        ), keys AS (SELECT DISTINCT s, e, season, team FROM cs)
        SELECT cu.s, cu.e, sum(cu.n)::BIGINT AS snaps
        FROM cu JOIN keys USING (s, e, season, team)
        GROUP BY cu.s, cu.e
        """
    )
    return con.sql(
        """
        SELECT c.s, c.e, ct.snaps, c.seasons, c.teams, c.roles, c.first_season, c.last_season,
          c.stint_seasons, c.stint_teams, c.stint_roles, c.stint_snaps
        FROM (
          SELECT s, e,
            list_sort(list_distinct(list(season))) AS seasons,
            list_sort(list_distinct(list(team))) AS teams,
            list_sort(list_distinct(list(role))) AS roles,
            min(season) AS first_season, max(season) AS last_season,
            list(season ORDER BY season, team, role) AS stint_seasons,
            list(team ORDER BY season, team, role) AS stint_teams,
            list(role ORDER BY season, team, role) AS stint_roles,
            list(snaps ORDER BY season, team, role) AS stint_snaps
          FROM cs GROUP BY s, e
        ) c JOIN ct USING (s, e)
        """
    ).pl()


def _worked_with(con) -> pl.DataFrame:
    con.execute(
        """
        CREATE TEMP TABLE sr AS
        SELECT c, team, season, array_to_string(list_sort(list_distinct(list(role))), '/') AS roles,
          min(w0) AS w0, max(w1) AS w1
        FROM staff GROUP BY c, team, season
        """
    )
    # two coaches of one team-season, over the weeks both were there (a head coach's own
    # weeks; coordinators the whole season): a fired head coach and his interim never overlap
    return con.sql(
        """
        WITH pairs AS (
          SELECT a.c AS s, b.c AS e, a.season, a.team, a.roles || '|' || b.roles AS roles,
            greatest(a.w0, b.w0) AS w0, least(a.w1, b.w1) AS w1
          FROM sr a JOIN sr b ON a.team = b.team AND a.season = b.season AND a.c < b.c
          WHERE greatest(a.w0, b.w0) <= least(a.w1, b.w1)
        ), together AS (
          SELECT p.s, p.e, p.season, p.team, p.roles,
            coalesce(sum(tw.plays), 0)::BIGINT AS snaps, coalesce(sum(tw.games), 0)::BIGINT AS games
          FROM pairs p LEFT JOIN team_weeks tw ON tw.team = p.team AND tw.season = p.season
            AND tw.week BETWEEN p.w0 AND p.w1
          GROUP BY p.s, p.e, p.season, p.team, p.roles
        )
        SELECT s, e, sum(snaps)::BIGINT AS snaps, sum(games)::BIGINT AS games,
          list_sort(list_distinct(list(season))) AS seasons,
          list_sort(list_distinct(list(team))) AS teams,
          min(season) AS first_season, max(season) AS last_season,
          list(season ORDER BY season, team) AS stint_seasons,
          list(team ORDER BY season, team) AS stint_teams,
          list(roles ORDER BY season, team) AS stint_roles
        FROM together GROUP BY s, e
        """
    ).pl()


# ---- the sync ---------------------------------------------------------------------------------


def _canon(v: Any) -> Any:
    if isinstance(v, list | tuple):
        return tuple(_canon(x) for x in v)
    return v


def _fingerprint(props: dict[str, Any]) -> int:
    """A link's properties as one comparable value (lists as tuples, nulls dropped)."""
    from nflengine.history_graph.append import _props

    p = _props(props)
    return hash(tuple(sorted((k, _canon(v)) for k, v in p.items())))


def _rows(df: pl.DataFrame, chunk: int = 20_000) -> Iterator[dict[str, Any]]:
    for i in range(0, df.height, chunk):
        yield from df.slice(i, chunk).to_dicts()


def want_index(df: pl.DataFrame) -> dict[tuple, int]:
    return {(r["s"], r["e"]): _fingerprint({k: v for k, v in r.items() if k not in ("s", "e")})
            for r in _rows(df)}  # fmt: skip


def have_index(driver, rtype: str) -> dict[tuple, int]:
    spec = S.DERIVED[rtype]
    sk, ek = S.key_of(spec.start), S.key_of(spec.end)
    out: dict[tuple, int] = {}
    # a type that doesn't exist yet (the first derive) is no warning worth printing
    with driver.session(notifications_min_severity="OFF") as s:
        res = s.run(
            f"MATCH (a:{spec.start})-[x:{rtype}]->(b:{spec.end}) "
            f"RETURN a.{sk} AS s, b.{ek} AS e, properties(x) AS p"
        )
        for r in res:
            key = (r["s"], r["e"])
            # a second link between the same two people is a duplicate: never equal to one
            out[key] = -1 if key in out else _fingerprint(r["p"])
    return out


def diff(want: dict[tuple, int], have: dict[tuple, int]) -> dict[str, list[tuple]]:
    return {
        "create": [k for k in want if k not in have],
        "update": [k for k, h in want.items() if k in have and have[k] != h],
        "delete": [k for k in have if k not in want],
    }


def _write_type(driver, rtype: str, df: pl.DataFrame, d: dict[str, list[tuple]], batch: int):
    from nflengine.history_graph.append import _props, _write

    spec = S.DERIVED[rtype]
    sk, ek = S.key_of(spec.start), S.key_of(spec.end)
    stale = [{"s": s, "e": e} for s, e in (*d["delete"], *d["update"])]
    if stale:  # every link between the pair (a duplicate too), then the fresh one
        _write(
            driver,
            f"UNWIND $rows AS r MATCH (a:{spec.start} {{{sk}: r.s}})-[x:{rtype}]->"
            f"(b:{spec.end} {{{ek}: r.e}}) DELETE x",
            stale,
            batch,
        )
    fresh_keys = [*d["create"], *d["update"]]
    if not fresh_keys:
        return
    keys = pl.DataFrame({"s": [k[0] for k in fresh_keys], "e": [k[1] for k in fresh_keys]},
                        schema={"s": df.schema["s"], "e": df.schema["e"]})  # fmt: skip
    rows = [
        {"s": r["s"], "e": r["e"], "p": _props(r, drop=("s", "e"))}
        for r in _rows(df.join(keys, on=["s", "e"], how="semi"))
    ]
    _write(
        driver,
        f"UNWIND $rows AS r MATCH (a:{spec.start} {{{sk}: r.s}}) "
        f"MATCH (b:{spec.end} {{{ek}: r.e}}) CREATE (a)-[x:{rtype}]->(b) SET x = r.p",
        rows,
        batch,
    )


def sync(driver, derived: dict[str, pl.DataFrame], batch: int,
         log: Callable[[str], None] = print) -> dict[str, Any]:  # fmt: skip
    """Make every derived type in the graph equal its frame; `{changes, check}` (`check` is
    what still differs after writing: empty when the graph matches)."""
    changes: dict[str, dict[str, int]] = {}
    for rtype, df in derived.items():
        want = want_index(df)
        d = diff(want, have_index(driver, rtype))
        if any(d.values()):
            _write_type(driver, rtype, df, d, batch)
            changes[rtype] = {op: len(v) for op, v in d.items() if v}
        log(f"history graph derive: {rtype} {df.height:,} links, "
            f"{sum(len(v) for v in d.values()):,} writes")  # fmt: skip
    check: dict[str, dict[str, int]] = {}
    for rtype, df in derived.items():
        if rtype not in changes:  # nothing written: the read before is the check
            continue
        d = diff(want_index(df), have_index(driver, rtype))
        if any(d.values()):
            check[rtype] = {op: len(v) for op, v in d.items() if v}
    return {"changes": changes, "check": check}


# ---- the run ----------------------------------------------------------------------------------


def run_derive(
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
    """Derive the person links for what the graph holds (the build + the appended weeks) and
    sync them; writes `derive.json`. `locked`: the caller (a build or an append) holds the
    history lock and has checked the weekly one. `start`: start the container if it's down
    (under the lock)."""
    paths = paths or ensure_data_root(create_dirs=False)
    cfg = cfg or history_config()
    if not locked:
        from nflengine.history_graph.build import weekly_lock_held

        if weekly_lock_held(paths):
            raise HistoryDeriveError(
                "the weekly run (or the injury update / Update play calling) holds the weekly "
                "lock: derive after it"
            )

    def checked_run() -> dict[str, Any]:
        build = state.read_build(paths)
        if not build:
            raise HistoryDeriveError(
                "no history-graph build yet: run `nfl history-graph build` first"
            )
        left = state.read_unfinished(paths)
        if left is not None and not (
            left.get("what") == "derive" and left.get("build_id") == build["build_id"]
        ):  # a half-written build or append comes first
            raise HistoryDeriveError(state.unfinished_advice(left))
        if start:
            from nflengine.history_graph.append import _start_if_down

            _start_if_down(cfg, log)
        return _derive(paths, cfg, build, driver, use_wandb, launched_by, smoke, log)

    if locked:
        return checked_run()
    from nflengine.ops.lock import LockHeld, run_lock

    try:
        with run_lock(state.lock_path(paths), "nfl history-graph derive"):
            return checked_run()
    except LockHeld as e:
        raise HistoryDeriveError(
            f"another history-graph build, append or derive is running (pid {e.holder.get('pid')})"
        ) from None


def _derive(paths, cfg, build, driver, use_wandb, launched_by, smoke, log) -> dict[str, Any]:
    from nflengine.history_graph.build import _write_json, utc_now
    from nflengine.history_graph.export import build_frames, load_inputs

    t0 = time.monotonic()
    started = utc_now()
    app = state.read_append(paths, build)
    prev = read_derive(paths, build)
    # a derive that didn't finish may have written links after the last derive.json: what the
    # graph holds is no longer what that id names, so a recovery never reuses it (Sol, AE03)
    recovering = (state.read_unfinished(paths) or {}).get("what") == "derive"
    cov = coverage(build, app)
    t = time.monotonic()
    frames = build_frames(
        load_inputs(paths, cov["seasons"], through_week=cov["through_week"], log=log), log=log
    )
    snaps = read_snaps(paths, cov["seasons"], cov["season"], cov["through_week"])
    t_inputs = round(time.monotonic() - t, 1)
    t = time.monotonic()
    derived, dropped = derived_frames(frames, snaps)
    from nflengine.history_graph.connections import people_frame

    people = people_frame(frames, snaps, derived)
    del frames
    t_compute = round(time.monotonic() - t, 1)
    log("history graph derive: " + ", ".join(f"{k} {v.height:,}" for k, v in derived.items())
        + f" ({t_compute:.0f} s)")  # fmt: skip
    own = driver is None
    if own:
        from nflengine.history_graph.client import get_history_driver

        driver = get_history_driver(cfg=cfg)
    try:
        t = time.monotonic()
        state.mark_unfinished(paths, "derive", build["build_id"])
        res = sync(driver, derived, cfg.batch_size, log)
        t_sync = round(time.monotonic() - t, 1)
    finally:
        if own:
            driver.close()
    doc: dict[str, Any] = {
        "status": "ok" if not res["check"] else "failed",
        "version": DERIVE_VERSION,
        # the id names what's in the graph: a re-run that wrote nothing keeps the last one (the
        # Connections projection and the lab results stay current), unless it's a recovery
        "derive_id": (
            prev["derive_id"]
            if prev and not recovering and not res["changes"] and not res["check"]
            else started.strftime("%Y%m%dT%H%M%SZ")
        ),
        "build_id": build["build_id"],
        "first_season": int(build["first_season"]),
        "through_season": int(build["through_season"]),
        "season": cov["season"],
        "through_week": cov["through_week"],
        "counts": {k: v.height for k, v in derived.items()},
        "changes": res["changes"],
        "check": res["check"],
        "dropped": dropped,
        "derived_at": utc_now().isoformat(),
        "seconds": {
            "inputs": t_inputs,
            "compute": t_compute,
            "sync": t_sync,
            "total": round(time.monotonic() - t0, 1),
        },  # fmt: skip
    }
    if use_wandb:
        _wandb_derive(doc, launched_by, smoke, log)
    if not res["check"]:
        _write_people(paths, people)
    _write_json(derive_path(paths), doc)
    if res["check"]:
        raise HistoryDeriveError(
            "the derived links still differ after the sync: " + ", ".join(sorted(res["check"]))
        )
    state.clear_unfinished(paths)
    return doc


def _write_people(paths: DataPaths, people: pl.DataFrame) -> None:
    """The people index for the Connections search (`connections.search_people`)."""
    from nflengine.fsutil import replace_file
    from nflengine.history_graph.connections import people_path

    path = people_path(paths)
    tmp = path.with_name(f".{path.name}.tmp")
    people.write_parquet(tmp)
    replace_file(tmp, path)


def derive_after(paths: DataPaths, cfg: HistoryConfig, driver, log: Callable[[str], None]):
    """The derive at the end of a build or an append (the caller holds the lock): `(summary,
    None)`, or `(None, error)` after logging it; a failed derive never fails the build or the
    append (`nfl history-graph derive` can be run again on its own)."""
    try:
        doc = run_derive(paths=paths, cfg=cfg, driver=driver, use_wandb=False, log=log,
                         locked=True)  # fmt: skip
        return summary(doc), None
    except Exception as e:
        if isinstance(e, HistoryDeriveError):
            err = str(e)
        else:
            from nflengine.graph.client import safe_error

            err = safe_error(e)  # the driver's text never leaves
        log(f"[yellow]history graph: the derive failed ({err}); run `nfl history-graph "
            "derive`[/]")  # fmt: skip
        return None, err


def summary(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """The part of a derive document a build's or an append's document keeps."""
    if not doc:
        return None
    keep = ("status", "derive_id", "counts", "changes", "seconds")
    return {k: doc.get(k) for k in keep}


def wandb_metrics(doc: dict[str, Any] | None) -> dict[str, Any]:
    """`derive/count/<type>`, `derive/writes`, `derive/time/<stage>` for a W&B summary."""
    if not doc:
        return {}
    writes = sum(sum(v.values()) for v in (doc.get("changes") or {}).values())
    return {
        **{f"derive/count/{k}": v for k, v in (doc.get("counts") or {}).items()},
        "derive/writes": writes,
        **{f"derive/time/{k}": v for k, v in (doc.get("seconds") or {}).items()},
    }


def _wandb_derive(doc: dict[str, Any], launched_by, smoke, log) -> None:
    try:
        from nflengine.tracking import git_commit, init_run

        wb = init_run(
            group="history-graph-smoke" if smoke else "history-graph",
            job_type="history-graph-derive",
            config={
                "build_id": doc["build_id"],
                "season": doc["season"],
                "through_week": doc["through_week"],
                "version": DERIVE_VERSION,
                "git_commit": git_commit(),
            },
            tags=["history-graph", *(["smoke"] if smoke else [])],
            launched_by=launched_by,
            name=f"history-graph-derive-{doc['derive_id']}",
        )
        wb.log({f"time/{k}": v for k, v in doc["seconds"].items()})
        wb.summary.update({"status": doc["status"], **wandb_metrics(doc),
                           **{f"dropped/{k}": v for k, v in doc["dropped"].items()}})  # fmt: skip
        doc["wandb_url"] = wb.url
        wb.finish(exit_code=0 if doc["status"] == "ok" else 1)
    except Exception as e:  # W&B never sinks a derive
        log(f"[yellow]W&B off for this derive ({type(e).__name__})[/]")


def links_unchanged(paths: DataPaths, derive_id: str) -> bool:
    """Are the person links still the ones `derive_id` names, with no build, append or derive
    running or half-done? The lab and the path benchmark read the graph for minutes and write
    their results only if it held still (Sol, AE03 review)."""
    if state.history_busy(paths):
        return False
    d = read_derive(paths)
    return bool(d) and d.get("derive_id") == derive_id


def read_derive(paths: DataPaths, build: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The last finished derive onto the current build, or None."""
    build = build if build is not None else state.read_build(paths)
    d = state._read(derive_path(paths))
    if not d or d.get("status") != "ok" or not build:
        return None
    return d if d.get("build_id") == build.get("build_id") else None
