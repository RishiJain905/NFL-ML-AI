"""Connections, "six degrees" (AE03, D147): the shortest chain between two people (players or
coaches) in the history graph, each link with its evidence.

- **Who's a person:** a `Player` or a `Coach`; the chain walks only the person links
  (`schema.PERSON_LINKS`: TEAMMATE, COACHED, WORKED_WITH), never a Team, a Season or a Play
  (through a team everyone is two steps from everyone, the README's pitfall).
- **Fewest links** (`mode="fewest"`): Cypher's `SHORTEST k` (a bidirectional breadth-first
  search). Among the chains of the shortest length the strongest come first when there are few
  enough of them to rank (`ALL SHORTEST`, capped); otherwise Neo4j's own order.
- **Strongest links** (`mode="strongest"`): GDS on an in-memory projection of the person graph
  weighted by `cost = 1 / snaps` (TEAMMATE: snaps together; COACHED: the player's snaps in the
  coach's unit; WORKED_WITH: the team's snaps while both were on the staff): Dijkstra for one
  chain, Yen's algorithm for k. The projection is named after the derive it reads
  (`persons-<derive_id>`) and made on first use (a few seconds), so it's never stale.
- **Evidence:** every link's stints are on the relationship (`stint_*`), so "teammates on 2019
  KC, 612 snaps together" needs no second query.

Read-only: the person links are written by `nfl history-graph derive`; this module only reads
them (the projection lives in GDS's in-memory catalog, not in the database). The people index
for the name search is `neo4j-history/people.parquet`, written by the derive.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import polars as pl

from nflengine.history_graph import schema as S
from nflengine.paths import DataPaths

LINKS = "|".join(S.PERSON_LINKS)
MAX_K = 5
TIMEOUT_S = 8.0
RANK_LIMIT = 2_000  # rank "fewest" ties by strength only when there are at most this many
# ASCII digits only: `\d` would take any Unicode digit (the test worker, AE03)
PERSON_ID = re.compile(r"^(player):(00-[0-9]{7})$|^(coach):([a-z0-9][a-z0-9-]{0,63})$")
ROLE_WORDS = {"HC": "head coach", "OC": "offensive coordinator", "DC": "defensive coordinator",
              "ST": "special teams coordinator"}  # fmt: skip
PEOPLE_FILE = "people.parquet"
_PROJECTION_LOCK = threading.Lock()


class ConnectionError_(ValueError):
    """A request that can't be answered (fixed text; `code` for the API)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PersonRef:
    kind: str  # player | coach
    id: str

    @property
    def label(self) -> str:
        return "Player" if self.kind == "player" else "Coach"

    @property
    def key(self) -> str:
        return "player_id" if self.kind == "player" else "coach_id"

    def text(self) -> str:
        return f"{self.kind}:{self.id}"


def parse_person(value: str) -> PersonRef:
    """`player:00-0033873` or `coach:andy-reid` (the people search's ids); anything else is
    refused before any query."""
    m = PERSON_ID.match((value or "").strip())
    if not m:
        raise ConnectionError_("bad_id", "a person is `player:<GSIS id>` or `coach:<id>`")
    if m.group(1):
        return PersonRef("player", m.group(2))
    return PersonRef("coach", m.group(4))


def _span(first: int, last: int | None) -> str:
    """ "2018-2025", or "2018" for one season."""
    return str(first) if last is None or last == first else f"{first}-{last}"


# ---- formatting -------------------------------------------------------------------------------


def season_ranges(seasons: list[int]) -> str:
    """[2018, 2019, 2020, 2023] -> "2018-2020, 2023"."""
    out: list[str] = []
    ss = sorted(set(int(s) for s in seasons))
    i = 0
    while i < len(ss):
        j = i
        while j + 1 < len(ss) and ss[j + 1] == ss[j] + 1:
            j += 1
        out.append(str(ss[i]) if i == j else f"{ss[i]}-{ss[j]}")
        i = j + 1
    return ", ".join(out)


def _stints(props: dict[str, Any]) -> list[dict[str, Any]]:
    seasons = props.get("stint_seasons") or []
    teams = props.get("stint_teams") or []
    snaps = props.get("stint_snaps") or [None] * len(seasons)
    roles = props.get("stint_roles") or [None] * len(seasons)
    return [
        {"season": int(s), "team": t, "snaps": None if n is None else int(n), "roles": r}
        for s, t, n, r in zip(seasons, teams, snaps, roles, strict=False)
    ]


def _by_team(stints: list[dict[str, Any]]) -> list[tuple[str, list[int]]]:
    """[(team, seasons)] in order of first appearance."""
    out: dict[str, list[int]] = {}
    for s in stints:
        out.setdefault(s["team"], []).append(s["season"])
    return list(out.items())


def _role_words(roles: str | None) -> str:
    parts = [ROLE_WORDS.get(r, r) for r in (roles or "").split("/") if r]
    return " and ".join(parts)


def evidence(rtype: str, props: dict[str, Any], start: dict[str, Any], end: dict[str, Any]):
    """The link's evidence: `{type, snaps, seasons, teams, best, stints, text}`; `start` /
    `end` are the people at the relationship's start and end (its stored direction)."""
    stints = _stints(props)
    seasons = [int(s) for s in props.get("seasons") or []]
    teams = list(props.get("teams") or [])
    snaps = props.get("snaps")
    snaps = int(snaps) if snaps is not None else None
    out: dict[str, Any] = {"type": rtype, "snaps": snaps, "seasons": seasons, "teams": teams,
                           "stints": stints}  # fmt: skip
    where = "; ".join(f"{t} {season_ranges(ss)}" for t, ss in _by_team(stints))
    if rtype == "TEAMMATE":
        best = max(stints, key=lambda s: (s["snaps"] or 0, s["season"])) if stints else None
        out["best"] = best
        text = (
            f"teammates on {best['season']} {best['team']}, {best['snaps']:,} snaps together"
            if best
            else "teammates"
        )
        if len(stints) > 1 and snaps is not None:
            text += f" ({snaps:,} in all: {where})"
        out["text"] = text
    elif rtype == "COACHED":
        roles = sorted({r for s in stints for r in (s["roles"] or "").split("/") if r})
        what = _role_words("/".join(roles)) or "coach"
        out["roles"] = roles
        out["best"] = max(stints, key=lambda s: (s["snaps"] or 0, s["season"])) if stints else None
        out["text"] = f"{start.get('name')} coached {end.get('name')} ({what}): {where}" + (
            f", {snaps:,} snaps" if snaps is not None else ""
        )
    elif rtype == "WORKED_WITH":
        pairs = []
        for s in stints:
            a, _, b = (s["roles"] or "").partition("|")
            pairs.append((s["team"], s["season"], _role_words(a), _role_words(b)))
        last = pairs[-1] if pairs else None
        out["games"] = props.get("games")
        out["best"] = stints[-1] if stints else None
        out["text"] = f"on the same staff: {where}" + (
            f" ({start.get('name')} {last[2]}, {end.get('name')} {last[3]} in {last[1]})"
            if last and last[2] and last[3]
            else ""
        )
    else:
        out["text"] = rtype.lower()
    return out


# ---- the queries -----------------------------------------------------------------------------


def _match(ref: PersonRef, var: str, param: str) -> str:
    return f"MATCH ({var}:{ref.label} {{{ref.key}: ${param}}})"


_POSITION = "CASE WHEN n:Coach THEN 'coach' ELSE n.position END"
_PEOPLE = (
    "[n IN nodes(p) | {element: elementId(n), kind: CASE WHEN n:Player THEN 'player' ELSE "
    f"'coach' END, id: coalesce(n.player_id, n.coach_id), name: n.name, position: {_POSITION}}}]"
)
_LINKS = (
    "[r IN relationships(p) | {type: type(r), start: elementId(startNode(r)), "
    "props: properties(r)}]"
)


def _run(driver, query: str, timeout: float, **params) -> list[dict[str, Any]]:
    """A read transaction with a server-side timeout."""
    from neo4j import unit_of_work

    @unit_of_work(timeout=timeout)
    def work(tx):
        return [r.data() for r in tx.run(query, **params)]

    with driver.session() as s:
        return s.execute_read(work)


def _exists(driver, ref: PersonRef) -> dict[str, Any] | None:
    rows = _run(
        driver,
        f"{_match(ref, 'a', 'a')} RETURN a.name AS name, "
        "CASE WHEN a:Coach THEN 'coach' ELSE a.position END AS position, "
        f"COUNT {{ (a)-[:{LINKS}]-() }} AS links",
        TIMEOUT_S,
        a=ref.id,
    )
    return rows[0] if rows else None


def _assemble(people: list[dict], links: list[dict]) -> dict[str, Any]:
    by_el = {p["element"]: p for p in people}
    out_links = []
    for i, r in enumerate(links):
        a, b = people[i], people[i + 1]
        start = by_el.get(r["start"], a)
        end = b if start is a else a
        ev = evidence(r["type"], r["props"], start, end)
        ev["from"], ev["to"] = i, i + 1
        out_links.append(ev)
    clean = [{k: v for k, v in p.items() if k != "element"} for p in people]
    strength = [link["snaps"] for link in out_links if link.get("snaps") is not None]
    return {
        "hops": len(out_links),
        "people": clean,
        "links": out_links,
        "weakest_snaps": min(strength) if strength else None,
        "cost": round(sum(1.0 / max(s, 1) for s in strength), 6) if strength else None,
    }


def fewest(driver, a: PersonRef, b: PersonRef, k: int = 1) -> list[dict[str, Any]]:
    """Up to k chains with the fewest links (Cypher `SHORTEST`): the shortest ones first,
    then (for "show more") the next length; within a length, the strongest first whenever the
    chains of that length are few enough to rank (`RANK_LIMIT`), else Neo4j's own order."""
    k = max(1, min(int(k), MAX_K))
    head = f"{_match(a, 'a', 'a')} {_match(b, 'b', 'b')} "
    params = {"a": a.id, "b": b.id}
    for groups in (1, 2):
        pattern = f"MATCH p = SHORTEST {groups} GROUPS (a)-[:{LINKS}]-+(b) "
        n = _run(driver, head + pattern + f"WITH p LIMIT {RANK_LIMIT + 1} RETURN count(p) AS n",
                 TIMEOUT_S, **params)  # fmt: skip
        if not n or int(n[0]["n"]) == 0:
            return []
        if int(n[0]["n"]) > RANK_LIMIT:
            break  # too many to rank: Neo4j's own k shortest
        rows = _run(
            driver,
            head + pattern + "WITH p, reduce(c = 0.0, r IN relationships(p) | "
            "c + 1.0 / CASE WHEN coalesce(r.snaps, 0) > 0 THEN r.snaps ELSE 1 END) AS cost "
            f"ORDER BY length(p), cost LIMIT {k} RETURN {_PEOPLE} AS people, {_LINKS} AS links",
            TIMEOUT_S,
            **params,
        )
        if len(rows) >= k or groups == 2:
            return [_assemble(r["people"], r["links"]) for r in rows]
    rows = _run(
        driver,
        head + f"MATCH p = SHORTEST {k} (a)-[:{LINKS}]-+(b) "
        f"RETURN {_PEOPLE} AS people, {_LINKS} AS links",
        TIMEOUT_S,
        **params,
    )
    return [_assemble(r["people"], r["links"]) for r in rows]


def projection_name(derive_id: str) -> str:
    return f"persons-{derive_id}"


def _graph_exists(session, name: str) -> bool:
    return bool(
        session.run("CALL gds.graph.exists($n) YIELD exists RETURN exists", n=name).single()[0]
    )


def ensure_projection(driver, derive_id: str) -> dict[str, Any]:
    """The person graph in GDS's catalog for this derive (made once; older ones dropped).

    Safe across processes (the app's warm-up, a dump script, the CLI share one server; the
    lock covers only this process): only other derives' projections are dropped, never this
    one's, and "already exists" from a projection another process just made counts as made
    (a race the UI worker hit: a 500 on a strongest search, AE03)."""
    name = projection_name(derive_id)
    with _PROJECTION_LOCK, driver.session() as s:
        if _graph_exists(s, name):
            return {"name": name, "made": False}
        for r in s.run("CALL gds.graph.list() YIELD graphName RETURN graphName").data():
            other = str(r["graphName"])
            if other.startswith("persons-") and other != name:
                s.run("CALL gds.graph.drop($n, false) YIELD graphName RETURN graphName",
                      n=other).consume()  # fmt: skip
        t = time.monotonic()
        try:
            rec = s.run(
                f"MATCH (a:Player|Coach)-[r:{LINKS}]->(b) "  # labels: never a scan of the plays
                "WITH gds.graph.project($n, a, b, {relationshipProperties: {cost: 1.0 / "
                "toFloat(CASE WHEN coalesce(r.snaps, 0) > 0 THEN r.snaps ELSE 1 END)}}, "
                "{undirectedRelationshipTypes: ['*']}) AS g "
                "RETURN g.nodeCount AS nodes, g.relationshipCount AS rels",
                n=name,
            ).single()
        except Exception:
            if _graph_exists(s, name):  # another process made it meanwhile
                return {"name": name, "made": False}
            raise
        return {"name": name, "made": True, "nodes": rec["nodes"], "rels": rec["rels"],
                "seconds": round(time.monotonic() - t, 2)}  # fmt: skip


def strongest(driver, a: PersonRef, b: PersonRef, derive_id: str, k: int = 1):
    """Up to k chains with the strongest links (GDS: Dijkstra for one, Yen's for k)."""
    k = max(1, min(int(k), MAX_K))
    head = f"{_match(a, 'a', 'a')} {_match(b, 'b', 'b')} "
    if k == 1:
        call = ("CALL gds.shortestPath.dijkstra.stream($g, {sourceNode: a, targetNode: b, "
                "relationshipWeightProperty: 'cost'}) YIELD nodeIds")  # fmt: skip
    else:
        call = ("CALL gds.shortestPath.yens.stream($g, {sourceNode: a, targetNode: b, k: $k, "
                "relationshipWeightProperty: 'cost'}) YIELD index, nodeIds")  # fmt: skip
    query = head + call + " RETURN [x IN nodeIds | elementId(gds.util.asNode(x))] AS els"
    for attempt in (1, 2):
        proj = ensure_projection(driver, derive_id)
        try:
            found = _run(driver, query, TIMEOUT_S, a=a.id, b=b.id, g=proj["name"], k=k)
            break
        except Exception:
            # the projection was dropped under us (another process's newer derive, a
            # restart): make it again once; anything else is a real failure
            with driver.session() as s:
                gone = not _graph_exists(s, proj["name"])
            if attempt == 2 or not gone:
                raise
    out = []
    for row in found:
        els = row["els"]
        rows = _run(
            driver,
            "UNWIND range(0, size($els) - 2) AS i "
            "MATCH (x) WHERE elementId(x) = $els[i] "
            "MATCH (y) WHERE elementId(y) = $els[i + 1] "
            f"MATCH (x)-[r:{LINKS}]-(y) "
            "WITH i, x, y, r ORDER BY i, coalesce(r.snaps, 0) DESC "
            "WITH i, x, y, head(collect(r)) AS r ORDER BY i "
            "RETURN collect({type: type(r), start: elementId(startNode(r)), props: "
            "properties(r)}) AS links, "
            "collect(x)[0] AS first, collect(y) AS rest",
            TIMEOUT_S,
            els=els,
        )
        if not rows:
            continue
        people = _people_from(driver, els)
        out.append(_assemble(people, rows[0]["links"]))
    return out


def _people_from(driver, els: list[str]) -> list[dict[str, Any]]:
    rows = _run(
        driver,
        "UNWIND range(0, size($els) - 1) AS i MATCH (n) WHERE elementId(n) = $els[i] "
        "RETURN i, {element: elementId(n), kind: CASE WHEN n:Player THEN 'player' ELSE 'coach' "
        f"END, id: coalesce(n.player_id, n.coach_id), name: n.name, position: {_POSITION}}} AS p "
        "ORDER BY i",
        TIMEOUT_S,
        els=els,
    )
    return [r["p"] for r in rows]


def connect(driver, a: PersonRef, b: PersonRef, *, mode: str = "fewest", k: int = 1,
            derive_id: str | None = None) -> dict[str, Any]:  # fmt: skip
    """The answer for the API and the CLI: `{a, b, mode, k, paths, found, ms, note}`."""
    if mode not in ("fewest", "strongest"):
        raise ConnectionError_("bad_mode", "mode is fewest or strongest")
    if a == b:
        raise ConnectionError_("same_person", "pick two different people")
    t0 = time.monotonic()
    pa, pb = _exists(driver, a), _exists(driver, b)
    for ref, p in ((a, pa), (b, pb)):
        if p is None:
            raise ConnectionError_("not_found", f"{ref.text()} isn't in the history graph")
    res: dict[str, Any] = {
        "a": {
            "kind": a.kind,
            "id": a.id,
            "name": pa["name"],
            "position": pa["position"],
            "links": pa["links"],
        },  # fmt: skip
        "b": {
            "kind": b.kind,
            "id": b.id,
            "name": pb["name"],
            "position": pb["position"],
            "links": pb["links"],
        },  # fmt: skip
        "mode": mode,
        "k": max(1, min(int(k), MAX_K)),
        "paths": [],
        "note": None,
    }
    lonely = [p["name"] for p in (pa, pb) if not p["links"]]
    if lonely:
        res["note"] = (
            f"{' and '.join(lonely)} {'has' if len(lonely) == 1 else 'have'} no person links "
            "yet (teammates come from participation, 2018-2025; coaches from snap counts)"
        )
    elif mode == "fewest":
        res["paths"] = fewest(driver, a, b, k)
    else:
        if not derive_id:
            raise ConnectionError_("no_derive", "the person links haven't been derived yet")
        res["paths"] = strongest(driver, a, b, derive_id, k)
    res["found"] = bool(res["paths"])
    if not res["paths"] and not res["note"]:
        res["note"] = "no chain of person links joins them"
    res["ms"] = round((time.monotonic() - t0) * 1000)
    return res


# ---- the people index (written by the derive, read by the search) -----------------------------


def people_path(paths: DataPaths):
    return paths.neo4j_history / PEOPLE_FILE


def people_frame(frames, snaps: pl.DataFrame, derived: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """One row per person in the graph: kind, id, name, position, team (the latest), teams,
    first / last season, snaps (2018 on, from the snap counts; coaches: their team's), links
    (person links), for the name search."""
    players = frames.nodes["Player"].select(
        pl.lit("player").alias("kind"), pl.col("player_id").alias("id"), "name", "position"
    )
    coaches = frames.nodes["Coach"].select(
        pl.lit("coach").alias("kind"), pl.col("coach_id").alias("id"), "name",
        pl.lit("coach").alias("position"),
    )  # fmt: skip
    known = frames.nodes["Team"].select(pl.col("team_id").alias("team"))
    ps = (
        snaps.join(known, on="team", how="semi")  # the graph's teams only (a stray code too)
        .with_columns((pl.col("off") + pl.col("def") + pl.col("st")).alias("n"))
        .group_by("p", "team", "season")
        .agg(pl.col("n").sum())
        .sort("season", "n")
    )
    p_team = (
        ps.group_by("p")
        .agg(
            pl.col("team").last().alias("team"),
            pl.col("team").unique(maintain_order=True).alias("teams"),
            pl.col("season").min().cast(pl.Int64).alias("first_season"),
            pl.col("season").max().cast(pl.Int64).alias("last_season"),
            pl.col("n").sum().cast(pl.Int64).alias("snaps"),
        )
        .rename({"p": "id"})
    )
    staff = pl.concat(
        [
            frames.rels[t].select(
                pl.col("s").alias("id"), pl.col("e").alias("team"), pl.col("season").cast(pl.Int64)
            )  # fmt: skip
            for t in ("HEAD_COACH", "OFF_COORD", "DEF_COORD", "ST_COORD")
        ]
    ).sort("season")
    c_team = staff.group_by("id").agg(
        pl.col("team").last().alias("team"),
        pl.col("team").unique(maintain_order=True).alias("teams"),
        pl.col("season").min().alias("first_season"),
        pl.col("season").max().alias("last_season"),
        pl.lit(None, dtype=pl.Int64).alias("snaps"),
    )
    ends = (
        pl.concat(
            [
                pl.concat([df.select(pl.col("s").alias("id")), df.select(pl.col("e").alias("id"))])
                for t, df in derived.items()
                if t in S.PERSON_LINKS
            ]
        )
        .group_by("id")
        .agg(pl.len().cast(pl.Int64).alias("links"))
    )
    out = pl.concat(
        [players.join(p_team, on="id", how="left"), coaches.join(c_team, on="id", how="left")],
        how="vertical_relaxed",
    ).join(ends, on="id", how="left")
    return out.with_columns(pl.col("links").fill_null(0)).sort("kind", "id")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (s or "").lower().replace("-", " ")).strip()


def search_people(people: pl.DataFrame, q: str, limit: int = 10) -> list[dict[str, Any]]:
    """Fuzzy name search: exact and prefix matches first, then rapidfuzz's WRatio >= 75;
    ties broken by snaps (the more prominent first). Only people with person links."""
    from rapidfuzz import fuzz, process

    qn = _norm(q)
    if len(qn) < 2:
        return []
    cand = people.filter(pl.col("links") > 0)
    names = [_norm(n) for n in cand["name"].to_list()]
    hits: dict[int, float] = {}
    for i, n in enumerate(names):
        if n == qn:
            hits[i] = 200.0
        elif n.startswith(qn) or any(w.startswith(qn) for w in n.split()):
            hits[i] = 150.0
    for _name, score, i in process.extract(qn, names, scorer=fuzz.WRatio, limit=50):
        if score >= 75:
            hits[i] = max(hits.get(i, 0.0), float(score))
    rows = cand.with_row_index("_i").filter(pl.col("_i").is_in(list(hits)))
    out = []
    for r in rows.to_dicts():
        r["score"] = hits[r.pop("_i")]
        out.append(r)
    out.sort(key=lambda r: (-r["score"], -(r.get("snaps") or 0), r["name"] or ""))
    return [
        {
            "id": f"{r['kind']}:{r['id']}",
            "kind": r["kind"],
            "name": r["name"],
            "position": r["position"],
            "team": r.get("team"),
            "seasons": (
                _span(r["first_season"], r["last_season"])
                if r.get("first_season") is not None
                else None
            ),
            "links": r["links"],
        }
        for r in out[:limit]
    ]
