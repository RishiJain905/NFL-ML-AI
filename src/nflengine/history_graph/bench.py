"""`nfl history-graph bench-paths` (AE03, D147): how shortest-path algorithms actually perform on
the history graph's person graph, written up beside the Alman-Vassilevska Williams result.

The person graph: `Player` and `Coach` nodes joined by `TEAMMATE`, `COACHED` and `WORKED_WITH`
(`schema.PERSON_LINKS`), read as undirected, every link costing `1 / snaps` (the same cost the
Connections page's "strongest" mode uses). The whole graph has 5,665 people and 273,336 links
(546,672 arcs: `size_m` counts arcs, both directions).

What is timed, per size (a *size* is a seeded snowball sample: a breadth-first search from a
seeded start person, the first n people reached, the induced subgraph; samples are nested; the
last size is the whole graph):

- **single pair** (`pairs` seeded random pairs): Cypher `SHORTEST 1` (hops; a bidirectional BFS
  that runs on the database; whole graph only) and GDS Dijkstra source-target (weighted);
- **single source** (`sources` seeded sources): GDS Delta-Stepping (`.stream`, which returns the
  distances, and `.stats`, which only computes), scipy `csgraph.dijkstra`, `dijkstra_heap` (pure
  Python), `bmssp_sssp` (our implementation of Duan et al. 2025, on the graph as it is and after
  the paper's constant-degree transformation);
- **all pairs** (`repeats` runs of the whole thing): Floyd-Warshall with numba (the JIT compile is
  excluded and reported separately), scipy `floyd_warshall`, scipy `dijkstra` from every source,
  and GDS's all-pairs procedure `gds.allShortestPaths.stream` (its answer is aggregated in the
  database: count and sum of the distances, so no n^2 rows cross Bolt).

Every timed run is checked: distances must equal scipy's within 1e-9 (`agrees`; the pair rows
compare to scipy's distance / BFS hops, the all-pairs GDS row compares the count and the sum of
all distances). A disagreement is a bug: the run still writes its files and then raises.

GDS projections are named `bench-<n>-<derive_id>`, one at a time, dropped after use (and only
ever names starting with `bench-`: the app keeps a `persons-*` projection that is never touched).
Nothing is written to the database. Times are wall-clock milliseconds as a caller sees them (the
Bolt round trip included for the database rows).

An (algorithm, size) is skipped (and says why) when its time, scaled from the previous size,
would exceed `budget_s`; a run that would end past the budget is not started.

Outputs, in `{NFL_DATA_ROOT}/neo4j-history/lab/`: `path_benchmark.parquet` (the table),
`path_benchmark.json` (the table, the raw per-run times, the machine, the setup costs, the W&B
URL) and `path_benchmark.svg` (the log-log chart). One W&B run (`job_type: path-benchmark`).
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import platform
import sys
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np
import polars as pl

from nflengine.history_graph import sssp as P
from nflengine.history_graph import state
from nflengine.history_graph.config import HistoryConfig, history_config
from nflengine.history_graph.connections import LINKS, PersonRef
from nflengine.history_graph.derive import read_derive
from nflengine.paths import DataPaths, ensure_data_root

BENCH_VERSION = 1
LAB_DIR = "lab"
STEM = "path_benchmark"
TOL = 1e-9
STALE_PREFIX = "bench-"
DEFAULT_SIZES = [1000, 2000, 5000]
FAMILIES = ("pair", "source", "all")
FAMILY_TITLES = {"pair": "single pair", "source": "single source", "all": "all pairs"}
COLUMNS = ["algorithm", "family", "size_n", "size_m", "runs", "median_ms", "p90_ms", "min_ms",
           "agrees", "note"]  # fmt: skip

WEEKLY_TEXT = (
    "the weekly run (or the injury update / Update play calling) holds the weekly lock: "
    "run the benchmark after it"
)
BUSY_TEXT = (
    "a history-graph build, append or derive is running (or did not finish): benchmark after it"
)
NO_DERIVE_TEXT = "no derive yet: run `nfl history-graph derive` first"
CHANGED_TEXT = (
    "the person links changed while the benchmark ran (a build, append or derive): nothing "
    "was written; run it again"
)


class HistoryBenchError(RuntimeError):
    """The benchmark can't run or its checks failed (fixed, secret-free text)."""


# ---- the person graph ---------------------------------------------------------------------------

Q_NODES = (
    f"MATCH (n:Player|Coach) WHERE COUNT {{ (n)-[:{LINKS}]-() }} > 0 "
    "RETURN elementId(n) AS e, CASE WHEN n:Player THEN 'player:' + n.player_id "
    "ELSE 'coach:' + n.coach_id END AS k ORDER BY k"
)
Q_RELS = (
    f"MATCH (a:Player|Coach)-[r:{LINKS}]->(b) "
    "RETURN elementId(a) AS a, elementId(b) AS b, r.snaps AS snaps"
)
_COST = "1.0 / toFloat(CASE WHEN coalesce(r.snaps, 0) > 0 THEN r.snaps ELSE 1 END)"
_PROJECT = (
    f"WITH gds.graph.project($g, a, b, {{relationshipProperties: {{cost: {_COST}}}}}, "
    "{undirectedRelationshipTypes: ['*']}) AS p "
    "RETURN p.nodeCount AS nodes, p.relationshipCount AS rels"
)
Q_PROJECT_FULL = f"MATCH (a:Player|Coach)-[r:{LINKS}]->(b) {_PROJECT}"  # labels: never a play scan
Q_PROJECT_SAMPLE = (
    "MATCH (a) WHERE elementId(a) IN $els "
    f"MATCH (a)-[r:{LINKS}]->(b) WHERE elementId(b) IN $els {_PROJECT}"
)
Q_LIST = "CALL gds.graph.list() YIELD graphName RETURN graphName"
Q_DROP = "CALL gds.graph.drop($g, false) YIELD graphName RETURN graphName"
Q_GDS_VERSION = "CALL gds.version() YIELD gdsVersion RETURN gdsVersion"
Q_COMPONENTS = "CALL dbms.components() YIELD name, versions RETURN name, versions"
Q_PROCEDURES = (
    "SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'gds.allShortestPaths' "
    "OR name STARTS WITH 'gds.shortestPath' RETURN name"
)
_DELTA = (
    "MATCH (a) WHERE elementId(a) = $e CALL gds.allShortestPaths.delta.{mode}($g, "
    "{{sourceNode: a, relationshipWeightProperty: 'cost'}}) "
)
Q_DELTA_STREAM = (
    _DELTA.format(mode="stream") + "YIELD targetNode, totalCost RETURN targetNode AS t, "
    "totalCost AS c"
)
Q_DELTA_STATS = _DELTA.format(mode="stats") + "YIELD computeMillis RETURN computeMillis AS ms"
Q_APSP = (
    "CALL gds.allShortestPaths.stream($g, {relationshipWeightProperty: 'cost'}) "
    "YIELD sourceNodeId, targetNodeId, distance RETURN count(*) AS c, sum(distance) AS s"
)
APSP_PROC = "gds.allShortestPaths.stream"


def _match(ref: PersonRef, var: str, param: str) -> str:
    return f"MATCH ({var}:{ref.label} {{{ref.key}: ${param}}})"


def person_ref(key: str) -> PersonRef:
    kind, _, ident = key.partition(":")
    return PersonRef(kind, ident)


class PersonGraph:
    """The person graph as CSR arrays; `keys[i]` is `player:<id>` / `coach:<id>`, `eids[i]` the
    node's Neo4j element id (empty strings for an injected test graph)."""

    def __init__(self, keys: list[str], eids: list[str], indptr, indices, weights):
        self.keys, self.eids = keys, eids
        self.indptr, self.indices, self.weights = indptr, indices, weights

    @property
    def n(self) -> int:
        return len(self.keys)

    @property
    def m(self) -> int:
        return int(len(self.indices))


def load_person_graph(driver) -> PersonGraph:
    """Read the person graph from Neo4j (read-only): people sorted by key (so the node numbers,
    and with them every seeded sample, are reproducible), arcs both ways, cost `1 / snaps`."""
    with driver.session(notifications_min_severity="OFF") as s:
        nodes = s.run(Q_NODES).data()
        rels = s.run(Q_RELS).data()
    pos = {r["e"]: i for i, r in enumerate(nodes)}
    src, dst, cost = [], [], []
    for r in rels:
        a, b = pos.get(r["a"]), pos.get(r["b"])
        if a is None or b is None:
            continue
        snaps = r["snaps"]
        src.append(a)
        dst.append(b)
        cost.append(1.0 / (snaps if snaps is not None and snaps > 0 else 1))
    indptr, indices, weights = P.csr_from_edges(len(nodes), src, dst, cost, undirected=True)
    return PersonGraph([r["k"] for r in nodes], [r["e"] for r in nodes], indptr, indices, weights)


# ---- sampling -----------------------------------------------------------------------------------


def components(n: int, indptr, indices) -> np.ndarray:
    """Component label of every vertex (arcs read as undirected)."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    mat = csr_matrix((np.ones(len(indices)), np.asarray(indices), np.asarray(indptr)), shape=(n, n))
    return connected_components(mat, directed=False)[1]


def largest_component(n: int, indptr, indices) -> np.ndarray:
    """The vertices of the largest component (ascending)."""
    labels = components(n, indptr, indices)
    return np.flatnonzero(labels == np.bincount(labels).argmax())


def snowball(indptr, indices, size: int, start: int) -> np.ndarray:
    """The first `size` vertices reached by a breadth-first search from `start` (arcs in CSR
    order), in the order reached: a sample whose induced subgraph is connected. A component
    smaller than `size` gives all of it. Prefixes of one search are the smaller samples."""
    ptr, idx = np.asarray(indptr), np.asarray(indices)
    seen = np.zeros(len(ptr) - 1, dtype=bool)
    seen[start] = True
    order = [int(start)]
    queue = deque(order)
    while queue and len(order) < size:
        u = queue.popleft()
        for v in idx[ptr[u] : ptr[u + 1]]:
            if not seen[v]:
                seen[v] = True
                order.append(int(v))
                queue.append(int(v))
                if len(order) >= size:
                    break
    return np.asarray(order, dtype=np.int64)


def induced_subgraph(n: int, indptr, indices, weights, nodes: np.ndarray):
    """The CSR of the subgraph induced by `nodes`, renumbered by position in `nodes`."""
    ptr = np.asarray(indptr, dtype=np.int64)
    pos = np.full(n, -1, dtype=np.int64)
    pos[nodes] = np.arange(len(nodes))
    src = np.repeat(np.arange(n, dtype=np.int64), np.diff(ptr))
    dst = np.asarray(indices, dtype=np.int64)
    keep = (pos[src] >= 0) & (pos[dst] >= 0)
    return P.csr_from_edges(
        len(nodes), pos[src[keep]], pos[dst[keep]], np.asarray(weights)[keep], undirected=False
    )


def draw_pairs(rng: np.random.Generator, pool: np.ndarray, count: int) -> list[tuple[int, int]]:
    """`count` seeded pairs of different vertices of `pool` (fewer when `pool` is tiny)."""
    if len(pool) < 2 or count < 1:
        return []
    a = rng.choice(pool, size=count)
    b = rng.choice(pool, size=count)
    for _ in range(100):
        same = a == b
        if not same.any():
            break
        b[same] = rng.choice(pool, size=int(same.sum()))
    ok = a != b
    return [(int(x), int(y)) for x, y in zip(a[ok], b[ok], strict=True)]


# ---- agreement and summaries --------------------------------------------------------------------


def same_distances(a, b, tol: float = TOL) -> bool:
    """Equal shape, the same unreachable (inf) entries, finite ones within `tol`."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if a.shape != b.shape:
        return False
    inf_a, inf_b = np.isinf(a), np.isinf(b)
    if not np.array_equal(inf_a, inf_b):
        return False
    fin = ~inf_a
    return bool(np.all(np.abs(a[fin] - b[fin]) <= tol))


def summarize(times_ms: list[float]) -> tuple[float, float, float]:
    """`(median, p90, min)` of the times."""
    arr = np.asarray(times_ms, dtype=np.float64)
    return float(np.median(arr)), float(np.percentile(arr, 90)), float(arr.min())


# ---- the database side --------------------------------------------------------------------------


class _Db:
    """Every query the benchmark sends (all reads, plus in-memory GDS projections)."""

    def __init__(self, driver):
        self.driver = driver

    def run(self, query: str, **params) -> list[dict[str, Any]]:
        with self.driver.session(notifications_min_severity="OFF") as s:
            return s.run(query, **params).data()

    def versions(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            out["gds"] = self.run(Q_GDS_VERSION)[0]["gdsVersion"]
            kernel = [r for r in self.run(Q_COMPONENTS) if r["name"] == "Neo4j Kernel"]
            out["neo4j"] = kernel[0]["versions"][0] if kernel else None
            out["procedures"] = sorted(r["name"] for r in self.run(Q_PROCEDURES))
        except Exception as e:  # versions are a nicety: never sink the run
            out["error"] = type(e).__name__
        return out

    def drop_stale(self) -> list[str]:
        """Drop leftover `bench-*` projections (a killed earlier run); nothing else."""
        gone = []
        for r in self.run(Q_LIST):
            if str(r["graphName"]).startswith(STALE_PREFIX):
                self.drop(r["graphName"])
                gone.append(r["graphName"])
        return gone

    def project(self, name: str, eids: list[str] | None) -> dict[str, Any]:
        if not name.startswith(STALE_PREFIX):
            raise HistoryBenchError("a benchmark projection's name starts with bench-")
        t = time.perf_counter()
        if eids is None:
            rec = self.run(Q_PROJECT_FULL, g=name)[0]
        else:
            rec = self.run(Q_PROJECT_SAMPLE, g=name, els=eids)[0]
        return {"nodes": rec["nodes"], "rels": rec["rels"],
                "ms": (time.perf_counter() - t) * 1000}  # fmt: skip

    def drop(self, name: str) -> None:
        if not name.startswith(STALE_PREFIX):
            raise HistoryBenchError("only projections named bench-* are dropped")
        self.run(Q_DROP, g=name)

    def pair_hops(self, a: str, b: str) -> int | None:
        ra, rb = person_ref(a), person_ref(b)
        rows = self.run(
            f"{_match(ra, 'a', 'a')} {_match(rb, 'b', 'b')} "
            f"MATCH p = SHORTEST 1 (a)-[:{LINKS}]-+(b) RETURN length(p) AS hops",
            a=ra.id,
            b=rb.id,
        )
        return int(rows[0]["hops"]) if rows else None

    def pair_cost(self, graph: str, a: str, b: str) -> float | None:
        ra, rb = person_ref(a), person_ref(b)
        rows = self.run(
            f"{_match(ra, 'a', 'a')} {_match(rb, 'b', 'b')} "
            "CALL gds.shortestPath.dijkstra.stream($g, {sourceNode: a, targetNode: b, "
            "relationshipWeightProperty: 'cost'}) YIELD totalCost RETURN totalCost AS c",
            g=graph,
            a=ra.id,
            b=rb.id,
        )
        return float(rows[0]["c"]) if rows else None

    def delta_stream(self, graph: str, eid: str) -> list[tuple[int, float]]:
        rows = self.run(Q_DELTA_STREAM, g=graph, e=eid)
        return [(int(r["t"]), float(r["c"])) for r in rows]

    def delta_stats(self, graph: str, eid: str) -> int:
        rows = self.run(Q_DELTA_STATS, g=graph, e=eid)
        return int(rows[0]["ms"]) if rows else -1

    def apsp(self, graph: str) -> tuple[int, float]:
        row = self.run(Q_APSP, g=graph)[0]
        return int(row["c"]), float(row["s"] or 0.0)


def node_id(eid: str) -> int:
    """The numeric id inside a Neo4j element id (`4:<db uuid>:<id>`), which GDS returns."""
    return int(eid.rsplit(":", 1)[1])


# ---- the runner ---------------------------------------------------------------------------------


def _scale_arcs(prev: dict, n: int, m: int) -> float:
    return (m + n) / max(prev["n"] + prev["m"], 1)


def _scale_pairs_sources(prev: dict, n: int, m: int) -> float:
    return n * (m + n) / max(prev["n"] * (prev["n"] + prev["m"]), 1)


def _scale_cubic(prev: dict, n: int, m: int) -> float:
    return (n / max(prev["n"], 1)) ** 3


def _scale_flat(prev: dict, n: int, m: int) -> float:
    return 1.0


class _Wandb:
    """The one W&B run; every call is guarded: W&B never sinks the benchmark."""

    def __init__(self, enabled: bool, log: Callable[[str], None]):
        self.run = None
        self.enabled = enabled
        self.log = log
        self.url: str | None = None
        self.finished = False

    def start(self, config: dict[str, Any], smoke: bool, launched_by: str | None) -> None:
        if not self.enabled:
            return
        try:
            from nflengine import tracking

            self.run = tracking.init_run(
                group="history-graph-smoke" if smoke else "history-graph",
                job_type="path-benchmark",
                config=config,
                tags=["history-graph", "path-benchmark", *(["smoke"] if smoke else [])],
                launched_by=launched_by,
                name="path-benchmark",
            )
            self.run.define_metric("size_n")
            self.run.define_metric("median_ms/*", step_metric="size_n")
            self.url = getattr(self.run, "url", None)
        except Exception as e:
            self.run = None
            self.log(f"[yellow]W&B off for this benchmark ({type(e).__name__})[/]")

    def guarded(self, fn: Callable[[], Any]) -> None:
        if self.run is None:
            return
        try:
            fn()
        except Exception as e:
            self.log(f"[yellow]W&B call failed ({type(e).__name__}); carrying on[/]")

    def abort(self) -> None:
        """Close the run as failed (a crash before `finish`)."""
        if not self.finished:
            self.finished = True
            self.guarded(lambda: self.run.finish(exit_code=1))

    def result(self, row: dict[str, Any]) -> None:
        if row["runs"] < 1:
            return
        key = f"{row['family']}/{row['algorithm']}"
        self.guarded(lambda: self.run.log({
            "size_n": row["size_n"],
            "size_m": row["size_m"],
            f"median_ms/{key}": row["median_ms"],
            f"p90_ms/{key}": row["p90_ms"],
            f"min_ms/{key}": row["min_ms"],
        }))  # fmt: skip

    def finish(self, rows: list[dict[str, Any]], svg: str, summary: dict[str, Any]) -> None:
        def go() -> None:
            import wandb

            table = wandb.Table(columns=COLUMNS, data=[[r[c] for c in COLUMNS] for r in rows])
            self.run.log({"results": table, "chart_log_log_svg": wandb.Html(svg, inject=False)})
            png = chart_png(rows)
            if png is not None:
                import io

                from PIL import Image

                self.run.log({"chart_log_log": wandb.Image(Image.open(io.BytesIO(png)))})
            self.run.summary.update(summary)

        self.guarded(go)
        self.finished = True
        # a failed step fails the run too, not only a disagreement (Sol, AE03 review)
        self.guarded(lambda: self.run.finish(exit_code=0 if summary.get("ok") else 1))
        if self.run is not None and self.url is None:
            self.url = getattr(self.run, "url", None)


class _Bench:
    def __init__(self, *, pairs, sources, repeats, seed, budget_s, log, wb, derive_id):
        self.pairs, self.sources, self.repeats = pairs, sources, repeats
        self.seed, self.budget_s, self.log, self.wb = seed, float(budget_s), log, wb
        self.derive_id = derive_id
        self.rows: list[dict[str, Any]] = []
        self.times: dict[str, list[float]] = {}
        self.setup: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self.prev: dict[str, dict] = {}  # algorithm -> last size's {n, m, ms}
        self.dead: dict[str, str] = {}  # algorithm -> why it stopped at a smaller size
        self.jit_compile_s: float | None = None

    # -- bookkeeping --
    def add_setup(self, size: dict, step: str, ms: float) -> None:
        self.setup.append({"size_n": size["n"], "step": step, "ms": round(ms, 3)})

    def _row(self, size, algorithm, family, runs, times, agrees, note) -> dict[str, Any]:
        if times:
            med, p90, low = summarize(times)
            med, p90, low = round(med, 4), round(p90, 4), round(low, 4)
        else:
            med = p90 = low = None
        row = {"algorithm": algorithm, "family": family, "size_n": size["n"],
               "size_m": size["m"], "runs": runs, "median_ms": med, "p90_ms": p90,
               "min_ms": low, "agrees": agrees, "note": note}  # fmt: skip
        self.rows.append(row)
        self.times[f"{family}/{algorithm}/{size['n']}"] = [round(t, 4) for t in times]
        self.wb.result(row)
        shown = "skipped" if runs < 1 else f"median {med:.3f} ms over {runs}"
        verdict = "" if agrees is None else (" agrees" if agrees else " DISAGREES")
        self.log(f"bench-paths: {family:6s} {algorithm:24s} n={size['n']:<5d} {shown}{verdict}")
        return row

    def skip(self, size, algorithm, family, why) -> None:
        self._row(size, algorithm, family, 0, [], None, f"skipped: {why}")

    # -- the measuring loop --
    def measure(self, size, algorithm, family, items, call, *, check=None, scale=_scale_arcs,
                warm=None, note="", note_after=None, keep=10**9, min_runs=None):  # fmt: skip
        """Time `call(item)` for each item; `check(item, result)` says whether the result is
        right. Returns `(row, results)` (the first `keep` results of the runs made), or
        `(None, [])` when the algorithm was skipped or failed before its first run.
        `min_runs`: how many runs must fit the budget for it to start (default: all items; the
        all-pairs rows pass 1, a repeat is only started when it would end inside the budget)."""
        n, m = size["n"], size["m"]
        if algorithm in self.dead:
            self.skip(size, algorithm, family, self.dead[algorithm])
            return None, []
        prev = self.prev.get(algorithm)
        if prev is not None:
            wanted = len(items) if min_runs is None else min_runs
            predicted = prev["ms"] * scale(prev, n, m) * wanted / 1000.0
            if predicted > self.budget_s:
                why = (f"predicted {predicted:,.0f} s from n={prev['n']} (budget "
                       f"{self.budget_s:,.0f} s)")  # fmt: skip
                self.dead[algorithm] = f"skipped at a smaller size ({why})"
                self.skip(size, algorithm, family, why)
                return None, []
        times: list[float] = []
        results: list[Any] = []
        agrees: bool | None = None
        stopped = ""
        started = time.perf_counter()
        try:
            if warm is not None:
                warm()
            for item in items:
                spent = time.perf_counter() - started
                if times and spent + times[-1] / 1000.0 > self.budget_s:
                    stopped = f"stopped after {len(times)} of {len(items)} runs (budget)"
                    break
                t0 = time.perf_counter()
                res = call(item)
                times.append((time.perf_counter() - t0) * 1000.0)
                if check is not None:
                    ok = bool(check(item, res))
                    agrees = ok if agrees is None else (agrees and ok)
                if len(results) < keep:
                    results.append(res)
                del res
        except Exception as e:
            from nflengine.graph.client import safe_error

            why = f"failed after {len(times)} runs: {safe_error(e)}"
            self.failures.append(f"{family}/{algorithm} n={n}: {why}")
            if not times:
                self._row(size, algorithm, family, 0, [], None, why)
                return None, []
            stopped = why
        extra = note_after() if note_after is not None else ""
        text = "; ".join(x for x in (note, extra, stopped) if x)
        row = self._row(size, algorithm, family, len(times), times, agrees, text)
        if times:
            self.prev[algorithm] = {"n": n, "m": m, "ms": row["median_ms"]}
        return row, results

    def set_agrees(self, row: dict | None, agrees: bool | None) -> None:
        """Late verdict for a row already written (the scipy row, judged by the heap's runs)."""
        if row is not None and agrees is not None and row["runs"] > 0:
            row["agrees"] = agrees


def _machine() -> dict[str, Any]:
    info: dict[str, Any] = {
        "cpu_count": os.cpu_count(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "python": sys.version.split()[0],
        "numpy": np.__version__,
    }
    for mod in ("scipy", "numba", "polars"):
        try:
            info[mod] = __import__(mod).__version__
        except Exception:
            info[mod] = None
    try:
        import numba

        info["numba_threads"] = int(numba.get_num_threads())
    except Exception:
        info["numba_threads"] = None
    try:
        import psutil

        info["ram_gb"] = round(psutil.virtual_memory().total / 2**30, 1)
    except Exception:
        info["ram_gb"] = _ram_gb_windows()
    return info


def _ram_gb_windows() -> float | None:
    """Installed RAM through kernel32 (no psutil here); None off Windows or on any failure."""
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                        ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong),
                        ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                        ("ext", ctypes.c_ulonglong)]  # fmt: skip

        st = Status()
        st.length = ctypes.sizeof(Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return round(st.total / 2**30, 1)
    except Exception:
        return None


def resolve_sizes(sizes: list[int] | None, full_n: int) -> list[int | None]:
    """The sample sizes to run, ascending, `None` standing for the whole graph. Default: 1000,
    2000, 5000 and the whole graph; a size at least as big as the graph is the whole graph."""
    wanted: list[int | None] = list(DEFAULT_SIZES) + [None] if sizes is None else list(sizes)
    out: set[int | None] = set()
    for s in wanted:
        out.add(None if s is None or s >= full_n else int(s))
    out.discard(0)
    return sorted(out, key=lambda x: full_n if x is None else x)


def run_bench(
    *,
    sizes: list[int] | None = None,
    pairs: int = 20,
    sources: int = 10,
    repeats: int = 3,
    seed: int = 42,
    budget_s: float = 600.0,
    use_wandb: bool = True,
    launched_by: str | None = None,
    smoke: bool = False,
    log: Callable[[str], None] = print,
    paths: DataPaths | None = None,
    cfg: HistoryConfig | None = None,
    driver=None,
    graph: PersonGraph | None = None,
) -> dict[str, Any]:
    """Run the path benchmark (module docstring) and return the JSON document it wrote.

    `sizes`: sample sizes (None: 1000, 2000, 5000 and the whole graph; a size at least the
    number of people means the whole graph). `driver` / `cfg`: a Neo4j driver (default: the
    history graph's). `graph`: an already loaded `PersonGraph`; with no driver the database
    algorithms are left out (tests).
    """
    paths = paths or ensure_data_root(create_dirs=False)
    from nflengine.history_graph.build import weekly_lock_held

    if weekly_lock_held(
        paths
    ):  # first: the weekly graph's rebuild is slowed by shared disk and CPU
        raise HistoryBenchError(WEEKLY_TEXT)
    from nflengine.ops.lock import LockHeld, run_lock

    try:  # one lab or benchmark at a time: they'd drop each other's projections (Sol)
        with run_lock(state.lab_lock_path(paths), "nfl history-graph bench-paths"):
            return _locked(sizes=sizes, pairs=pairs, sources=sources, repeats=repeats,
                           seed=seed, budget_s=budget_s, use_wandb=use_wandb,
                           launched_by=launched_by, smoke=smoke, log=log, paths=paths,
                           cfg=cfg, driver=driver, graph=graph)  # fmt: skip
    except LockHeld as e:
        raise HistoryBenchError(
            f"another lab or path benchmark is running (pid {e.holder.get('pid')})"
        ) from None


def _locked(*, sizes, pairs, sources, repeats, seed, budget_s, use_wandb, launched_by, smoke,
            log, paths, cfg, driver, graph) -> dict[str, Any]:  # fmt: skip
    if state.history_busy(paths):
        raise HistoryBenchError(BUSY_TEXT)
    derive = read_derive(paths)
    if not derive:
        raise HistoryBenchError(NO_DERIVE_TEXT)
    derive_id = str(derive["derive_id"])
    t_all = time.monotonic()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    own_driver = False
    if graph is None:
        if driver is None:
            from nflengine.history_graph.client import get_history_driver

            driver = get_history_driver(cfg=cfg or history_config())
            own_driver = True
        t = time.monotonic()
        graph = load_person_graph(driver)
        log(f"bench-paths: loaded the person graph: {graph.n:,} people, {graph.m:,} arcs "
            f"({time.monotonic() - t:.1f} s)")  # fmt: skip
    db = _Db(driver) if driver is not None else None
    holder: dict[str, _Wandb] = {}
    try:
        return _run(graph, db, derive_id, started, t_all, sizes=sizes, pairs=pairs,
                    sources=sources, repeats=repeats, seed=seed, budget_s=budget_s,
                    use_wandb=use_wandb, launched_by=launched_by, smoke=smoke, log=log,
                    paths=paths, holder=holder)  # fmt: skip
    except BaseException:
        if "wb" in holder:
            holder["wb"].abort()  # a crash must not leave the W&B run open
        raise
    finally:
        if own_driver:
            driver.close()


def _run(graph, db, derive_id, started, t_all, *, sizes, pairs, sources, repeats, seed, budget_s,
         use_wandb, launched_by, smoke, log, paths, holder) -> dict[str, Any]:  # fmt: skip
    machine = _machine()
    versions = db.versions() if db is not None else {}
    machine.update({"gds": versions.get("gds"), "neo4j": versions.get("neo4j")})
    has_apsp = APSP_PROC in (versions.get("procedures") or [])

    full_n = graph.n
    requested = resolve_sizes(sizes, full_n)
    config = {
        "seed": seed, "sizes": [r if r is not None else "full" for r in requested],
        "pairs": pairs, "sources": sources, "repeats": repeats, "budget_s": budget_s,
        "derive_id": derive_id, "people": graph.n, "arcs": graph.m, "smoke": smoke,
        "machine": {k: v for k, v in machine.items() if k != "platform"},
    }  # fmt: skip
    wb = _Wandb(use_wandb, log)
    holder["wb"] = wb
    try:
        from nflengine.tracking import git_commit

        config["git_commit"] = git_commit()
    except Exception:
        pass
    wb.start(config, smoke, launched_by)
    bench = _Bench(pairs=pairs, sources=sources, repeats=repeats, seed=seed, budget_s=budget_s,
                   log=log, wb=wb, derive_id=derive_id)  # fmt: skip
    if db is not None:
        gone = db.drop_stale()
        if gone:
            log(f"bench-paths: dropped leftover projections {gone}")

    # the nested samples: one breadth-first search from a seeded start in the biggest component
    rng = np.random.default_rng(seed)
    big = largest_component(full_n, graph.indptr, graph.indices)
    start = int(rng.choice(big))
    biggest = max((r for r in requested if r is not None), default=0)
    order = snowball(graph.indptr, graph.indices, biggest, start) if biggest else None
    sample_sizes: list[dict[str, Any]] = []
    seen_n: set[int] = set()
    for r in requested:
        if r is None:
            nodes = np.arange(full_n)
            ptr, idx, wt = graph.indptr, graph.indices, graph.weights
        else:
            nodes = order[:r]
            ptr, idx, wt = induced_subgraph(full_n, graph.indptr, graph.indices, graph.weights,
                                            nodes)  # fmt: skip
        if len(nodes) in seen_n:
            continue
        seen_n.add(len(nodes))
        sample_sizes.append({"n": int(len(nodes)), "m": int(len(idx)),
                             "label": "full" if r is None else str(r), "requested": r,
                             "nodes": nodes, "indptr": ptr, "indices": idx,
                             "weights": wt})  # fmt: skip

    try:
        for size in sample_sizes:
            _run_size(bench, graph, db, size, has_apsp, seed)
    finally:
        if db is not None:
            with contextlib.suppress(Exception):
                db.drop_stale()

    rows = bench.rows
    bad = [r for r in rows if r["agrees"] is False]
    agrees_all = not bad
    ok = agrees_all and not bench.failures
    from nflengine.history_graph.derive import links_unchanged

    if not links_unchanged(paths, derive_id):  # a derive (or a build) changed what was timed
        raise HistoryBenchError(CHANGED_TEXT)
    svg = chart_svg(rows)
    doc = {
        "version": BENCH_VERSION,
        "derive_id": derive_id,
        "seed": seed,
        "pairs": pairs,
        "sources": sources,
        "repeats": repeats,
        "budget_s": budget_s,
        "smoke": smoke,
        "started_at": started,
        "seconds": round(time.monotonic() - t_all, 1),
        "sizes": [
            {"label": s["label"], "n": s["n"], "m": s["m"], "requested": s["requested"]}
            for s in sample_sizes
        ],  # fmt: skip
        "machine": machine,
        "gds_procedures": versions.get("procedures"),
        "jit_compile_s": None if bench.jit_compile_s is None else round(bench.jit_compile_s, 2),
        "table": rows,
        "times_ms": bench.times,
        "setup": bench.setup,
        "failures": bench.failures,
        "agrees_all": agrees_all,
        "ok": ok,
        "wandb_url": None,
    }
    wb.finish(rows, svg, {
        "ok": ok,
        "agrees_all": agrees_all,
        "failures": len(bench.failures),
        "skipped": sum(1 for r in rows if r["runs"] < 1),
        "seconds": doc["seconds"],
        "jit_compile_s": doc["jit_compile_s"],
        "derive_id": derive_id,
    })  # fmt: skip
    doc["wandb_url"] = wb.url
    write_outputs(paths, doc, svg)
    log(summary_text(doc))
    if bad:
        names = sorted({f"{r['family']}/{r['algorithm']} n={r['size_n']}" for r in bad})
        raise HistoryBenchError(
            "path algorithms disagreed (see path_benchmark.json): " + ", ".join(names)
        )
    if bench.failures:
        raise HistoryBenchError(
            f"{len(bench.failures)} benchmark step(s) failed (see path_benchmark.json)"
        )
    return doc


def _run_size(bench: _Bench, graph, db, size, has_apsp: bool, seed: int) -> None:
    """Every family at one size."""
    from scipy.sparse.csgraph import dijkstra as sp_dijkstra

    n, m = size["n"], size["m"]
    ptr, idx, wt = size["indptr"], size["indices"], size["weights"]
    nodes = size["nodes"]
    full = size["requested"] is None
    bench.log(f"bench-paths: --- size {size['label']}: n={n:,}, m={m:,} arcs ---")

    t = time.perf_counter()
    lists = (ptr.tolist(), idx.tolist(), wt.tolist())
    bench.add_setup(size, "csr to python lists", (time.perf_counter() - t) * 1000)
    t = time.perf_counter()
    mat = P.scipy_matrix(n, ptr, idx, wt)
    bench.add_setup(size, "scipy csr matrix", (time.perf_counter() - t) * 1000)

    rng = np.random.default_rng([seed, n])
    pool = largest_component(n, ptr, idx)
    src_list = [int(x) for x in rng.choice(pool, size=min(bench.sources, len(pool)),
                                           replace=False)]  # fmt: skip
    pair_list = draw_pairs(rng, pool, bench.pairs)

    projection = f"{STALE_PREFIX}{n}-{bench.derive_id}"
    keys = [graph.keys[j] for j in nodes]
    eids = [graph.eids[j] for j in nodes]
    nid_index = {node_id(e): i for i, e in enumerate(eids)} if db is not None else {}
    made = False
    try:
        if db is not None:
            try:
                info = db.project(projection, None if full else eids)
                made = True
                bench.add_setup(size, "gds projection", info["ms"])
                if info["nodes"] != n:
                    bench.failures.append(f"projection n={n}: {info['nodes']} nodes, not {n}")
                bench.log(f"bench-paths: projection {projection}: {info['nodes']:,} nodes, "
                          f"{info['rels']:,} rels ({info['ms']:.0f} ms)")  # fmt: skip
            except Exception as e:
                from nflengine.graph.client import safe_error

                bench.failures.append(f"projection n={n}: {safe_error(e)}")
                db = None

        # ---------------- single pair ----------------
        ref_cache: dict[int, np.ndarray] = {}
        hop_cache: dict[int, np.ndarray] = {}

        def ref_dist(s: int) -> np.ndarray:
            if s not in ref_cache:
                ref_cache[s] = sp_dijkstra(mat, directed=True, indices=s)
            return ref_cache[s]

        def ref_hops(s: int) -> np.ndarray:
            if s not in hop_cache:
                hop_cache[s] = sp_dijkstra(mat, directed=True, indices=s, unweighted=True)
            return hop_cache[s]

        if db is not None and pair_list:
            if full:
                bench.measure(
                    size, "cypher-shortest", "pair", pair_list,
                    lambda p: db.pair_hops(keys[p[0]], keys[p[1]]),
                    check=lambda p, r: r is not None and abs(r - ref_hops(p[0])[p[1]]) < 0.5,
                    scale=_scale_flat, warm=lambda: db.pair_hops(keys[pair_list[0][0]],
                                                                 keys[pair_list[0][1]]),
                    note="hops, bidirectional BFS in the database",
                )  # fmt: skip
            bench.measure(
                size, "gds-dijkstra-pair", "pair", pair_list,
                lambda p: db.pair_cost(projection, keys[p[0]], keys[p[1]]),
                check=lambda p, r: r is not None and abs(r - ref_dist(p[0])[p[1]]) <= TOL,
                scale=_scale_flat,
                warm=lambda: db.pair_cost(projection, keys[pair_list[0][0]], keys[pair_list[0][1]]),
                note="weighted, source-target",
            )  # fmt: skip

        # ---------------- single source ----------------
        # scipy is the reference: its row is judged by the pure-Python heap's runs below
        scipy_row, scipy_res = bench.measure(
            size, "scipy-dijkstra", "source", src_list,
            lambda s: sp_dijkstra(mat, directed=True, indices=s),
            warm=lambda: sp_dijkstra(mat, directed=True, indices=src_list[0]),
        )  # fmt: skip
        for s, r in zip(src_list, scipy_res, strict=False):
            ref_cache.setdefault(s, r)

        def vs_ref(s, r):
            return same_distances(r, ref_dist(s))

        heap_row, _ = bench.measure(
            size, "dijkstra-heap", "source", src_list,
            lambda s: P.dijkstra_heap(n, *lists, s), check=vs_ref,
            note="pure Python, heapq",
        )  # fmt: skip
        bench.set_agrees(scipy_row, None if heap_row is None else heap_row["agrees"])

        stats: dict[str, Any] = {}

        def bmssp_call(s):
            return P.bmssp_sssp(n, *lists, s, stats=stats if s == src_list[0] else None)

        def bmssp_note() -> str:
            if not stats:
                return ""
            finds = sum(stats["calls"].values()) - stats["base_cases"]
            return (f"k={stats['k']} t={stats['t']} levels={stats['levels']}; first source: "
                    f"{stats['pivots']:,} pivots of {stats['sources']:,} sources, "
                    f"{stats['pivot_early_exit']} of {finds} FindPivots calls gave up")  # fmt: skip

        bench.measure(size, "bmssp", "source", src_list, bmssp_call, check=vs_ref,
                      note_after=bmssp_note, note="pure Python, graph as is")  # fmt: skip

        cd_holder: dict[str, Any] = {}

        def cd_warm() -> None:
            t = time.perf_counter()
            cd = P.to_constant_degree(n, ptr, idx, wt)
            cd_lists = (cd.indptr.tolist(), cd.indices.tolist(), cd.weights.tolist())
            cd_holder["cd"], cd_holder["lists"] = cd, cd_lists
            bench.add_setup(size, "constant-degree transform + lists",
                            (time.perf_counter() - t) * 1000)  # fmt: skip

        def cd_call(s):
            cd = cd_holder["cd"]
            d = P.bmssp_sssp(cd.n, *cd_holder["lists"], int(cd.rep[s]))
            return d[cd.rep]

        bench.measure(
            size, "bmssp-constant-degree", "source", src_list, cd_call, check=vs_ref,
            warm=cd_warm,
            note="pure Python, after the paper's transformation (built once, not timed)",
        )  # fmt: skip

        if db is not None and src_list:

            def delta_stream_call(s):
                out = np.full(n, np.inf)
                for t_id, cost in db.delta_stream(projection, eids[s]):
                    out[nid_index[t_id]] = cost
                out[s] = 0.0
                return out

            bench.measure(
                size, "gds-delta-stream", "source", src_list, delta_stream_call, check=vs_ref,
                warm=lambda: db.delta_stream(projection, eids[src_list[0]]),
                note="Delta-Stepping, .stream (returns the distances)",
            )  # fmt: skip
            bench.measure(
                size, "gds-delta-stats", "source", src_list,
                lambda s: db.delta_stats(projection, eids[s]),
                warm=lambda: db.delta_stats(projection, eids[src_list[0]]),
                note="Delta-Stepping, .stats (computes, returns no distances: not checkable)",
            )  # fmt: skip

        # ---------------- all pairs ----------------
        _run_all_pairs(bench, db, size, projection if db is not None else None, has_apsp, mat,
                       lists, ptr, idx, wt)  # fmt: skip
    finally:
        if made and db is not None:
            with contextlib.suppress(Exception):
                db.drop(projection)


def _run_all_pairs(bench, db, size, projection, has_apsp, mat, lists, ptr, idx, wt) -> None:
    from scipy.sparse.csgraph import dijkstra as sp_dijkstra
    from scipy.sparse.csgraph import floyd_warshall as sp_floyd_warshall

    n = size["n"]
    reps = list(range(max(1, bench.repeats)))
    probe_rows = [int(x) for x in np.random.default_rng([bench.seed, n, 7]).choice(
        n, size=min(5, n), replace=False)]  # fmt: skip
    holder: dict[str, Any] = {}

    def heap_rows_agree(res) -> bool:
        return all(same_distances(res[r], P.dijkstra_heap(n, *lists, r)) for r in probe_rows)

    row, res = bench.measure(
        size, "scipy-dijkstra-all", "all", reps,
        lambda _i: sp_dijkstra(mat, directed=True),
        check=lambda _i, r: heap_rows_agree(r),
        scale=_scale_pairs_sources, keep=1, min_runs=1,
        note="every source in turn (C, one thread); 5 rows checked against dijkstra-heap",
    )  # fmt: skip
    holder["ref"] = res[0] if res else None
    res = None
    have_ref = holder["ref"] is not None

    def fw_check(_i, r) -> bool:
        return same_distances(r, holder["ref"])

    def jit_warm() -> None:
        if bench.jit_compile_s is None:
            bench.jit_compile_s = P.warm_up_floyd_warshall()
            bench.log(f"bench-paths: numba JIT compile {bench.jit_compile_s:.2f} s "
                      "(excluded from the timings)")  # fmt: skip

    holder["dense"] = P.dense_from_csr(n, ptr, idx, wt)
    bench.measure(
        size, "floyd-warshall-numba", "all", reps,
        lambda _i: P.floyd_warshall_numba(holder["dense"]), keep=0, min_runs=1,
        check=fw_check if have_ref else None, scale=_scale_cubic, warm=jit_warm,
        note=f"O(n^3), rows in parallel ({_threads()} threads); JIT compile excluded",
    )  # fmt: skip
    holder.pop("dense")

    bench.measure(
        size, "scipy-floyd-warshall", "all", reps,
        lambda _i: sp_floyd_warshall(mat, directed=True), keep=0, min_runs=1,
        check=fw_check if have_ref else None, scale=_scale_cubic,
        note="O(n^3), C, one thread",
    )  # fmt: skip

    if db is not None and projection is not None:
        if not has_apsp:
            bench.skip(size, "gds-all-pairs", "all", f"this GDS has no {APSP_PROC}")
        else:
            ref_mat = holder["ref"]
            if ref_mat is not None:
                fin = np.isfinite(ref_mat)
                expect_count = int(fin.sum())  # pairs with a path, the diagonal included
                expect_sum = float(ref_mat[fin].sum())
            else:
                expect_count = expect_sum = None

            def apsp_check(_i, r) -> bool:
                if expect_count is None:
                    return True
                count, total = r
                return count == expect_count and abs(total - expect_sum) <= 1e-9 * max(
                    1.0, abs(expect_sum)
                )

            bench.measure(
                size, "gds-all-pairs", "all", reps, lambda _i: db.apsp(projection),
                keep=0, min_runs=1, check=apsp_check if expect_count is not None else None,
                scale=_scale_pairs_sources,
                note="gds.allShortestPaths.stream, count and sum aggregated in the database",
            )  # fmt: skip
    holder.clear()


def _threads() -> int | None:
    try:
        import numba

        return int(numba.get_num_threads())
    except Exception:
        return None


# ---- outputs ------------------------------------------------------------------------------------


def lab_dir(paths: DataPaths):
    return paths.neo4j_history / LAB_DIR


def _clean(o: Any) -> Any:
    """The document with non-finite floats as None (strict JSON)."""
    if isinstance(o, float | np.floating):
        return float(o) if math.isfinite(float(o)) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [_clean(v) for v in o]
    return o


def _json_default(o: Any):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if not math.isfinite(float(o)) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def write_outputs(paths: DataPaths, doc: dict[str, Any], svg: str) -> None:
    """Parquet, JSON and SVG, each written to a temp name then replaced."""
    from nflengine.fsutil import replace_file

    out = lab_dir(paths)
    out.mkdir(parents=True, exist_ok=True)
    schema = {"algorithm": pl.String, "family": pl.String, "size_n": pl.Int64,
              "size_m": pl.Int64, "runs": pl.Int64, "median_ms": pl.Float64,
              "p90_ms": pl.Float64, "min_ms": pl.Float64, "agrees": pl.Boolean,
              "note": pl.String}  # fmt: skip
    df = pl.DataFrame([{c: r[c] for c in COLUMNS} for r in doc["table"]], schema=schema)
    files = {
        f"{STEM}.parquet": lambda p: df.write_parquet(p),
        f"{STEM}.json": lambda p: p.write_text(
            json.dumps(_clean(doc), indent=2, default=_json_default, allow_nan=False) + "\n",
            encoding="utf-8",
        ),
        f"{STEM}.svg": lambda p: p.write_text(svg, encoding="utf-8"),
    }
    for name, write in files.items():
        final = out / name
        tmp = out / f".{name}.tmp"
        write(tmp)
        replace_file(tmp, final)


def summary_text(doc: dict[str, Any]) -> str:
    """The table as plain text for the log."""
    lines = [f"{'algorithm':24s} {'family':6s} {'n':>6s} {'median ms':>12s} {'runs':>4s}  agrees"]
    for r in doc["table"]:
        med = "skipped" if r["runs"] < 1 else f"{r['median_ms']:,.3f}"
        ok = "" if r["agrees"] is None else ("yes" if r["agrees"] else "NO")
        lines.append(f"{r['algorithm']:24s} {r['family']:6s} {r['size_n']:6d} {med:>12s} "
                     f"{r['runs']:4d}  {ok}")  # fmt: skip
    return "\n".join(lines)


# ---- the chart ----------------------------------------------------------------------------------

_PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2",
            "#17becf", "#7f7f7f", "#bcbd22"]  # fmt: skip


def _series(rows: list[dict[str, Any]]) -> dict[str, dict[str, list[tuple[int, float]]]]:
    """`{family: {algorithm: [(n, median_ms)]}}` of the rows that ran."""
    out: dict[str, dict[str, list[tuple[int, float]]]] = {f: {} for f in FAMILIES}
    for r in rows:
        if r["runs"] >= 1 and r["median_ms"] is not None and r["median_ms"] > 0:
            out[r["family"]].setdefault(r["algorithm"], []).append((r["size_n"], r["median_ms"]))
    for fam in out.values():
        for pts in fam.values():
            pts.sort()
    return out


def chart_svg(rows: list[dict[str, Any]]) -> str:
    """A log-log chart (median time against n, one panel per family, a shared time axis) as an
    SVG string, with no plotting library."""
    series = _series(rows)
    algs = sorted({a for fam in series.values() for a in fam})
    color = {a: _PALETTE[i % len(_PALETTE)] for i, a in enumerate(algs)}
    xs = [x for fam in series.values() for pts in fam.values() for x, _ in pts]
    ys = [y for fam in series.values() for pts in fam.values() for _, y in pts]
    pw, ph, ml, mt, gap = 300, 280, 62, 34, 38
    width = ml + 3 * pw + 2 * gap + 20
    legend_rows = (len(algs) + 2) // 3
    height = mt + ph + 56 + 18 * max(legend_rows, 1)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
           f'width="{width}" height="{height}" font-family="sans-serif" font-size="11">',
           f'<rect width="{width}" height="{height}" fill="white"/>']  # fmt: skip
    if not xs:
        out.append('<text x="20" y="30">no results</text></svg>')
        return "\n".join(out)
    lx0, lx1 = math.log10(min(xs)), math.log10(max(xs))
    if lx1 - lx0 < 0.3:
        lx0, lx1 = lx0 - 0.15, lx1 + 0.15
    ly0, ly1 = math.floor(math.log10(min(ys))), math.ceil(math.log10(max(ys)))
    if ly1 <= ly0:
        ly1 = ly0 + 1
    tick_n = sorted(set(xs))

    def px(x: float, left: float) -> float:
        return left + (math.log10(x) - lx0) / (lx1 - lx0) * pw

    def py(y: float) -> float:
        return mt + ph - (math.log10(y) - ly0) / (ly1 - ly0) * ph

    for i, fam in enumerate(FAMILIES):
        left = ml + i * (pw + gap)
        out.append(f'<text x="{left + pw / 2}" y="{mt - 14}" text-anchor="middle" '
                   f'font-weight="bold" font-size="13">{FAMILY_TITLES[fam]}</text>')  # fmt: skip
        out.append(f'<rect x="{left}" y="{mt}" width="{pw}" height="{ph}" fill="none" '
                   'stroke="#999"/>')  # fmt: skip
        for p in range(ly0, ly1 + 1):
            y = py(10.0**p)
            out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + pw}" y2="{y:.1f}" '
                       'stroke="#e3e3e3"/>')  # fmt: skip
            if i == 0:
                label = f"{10.0**p:g}"
                out.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">'
                           f"{label}</text>")  # fmt: skip
        for x in tick_n:
            xp = px(x, left)
            out.append(f'<line x1="{xp:.1f}" y1="{mt + ph}" x2="{xp:.1f}" y2="{mt + ph + 4}" '
                       'stroke="#999"/>')  # fmt: skip
            out.append(f'<text x="{xp:.1f}" y="{mt + ph + 17}" text-anchor="middle">{x:,}</text>')
        out.append(f'<text x="{left + pw / 2}" y="{mt + ph + 34}" text-anchor="middle">'
                   "people in the graph (n)</text>")  # fmt: skip
        for alg, pts in series[fam].items():
            c = color[alg]
            coords = [(px(x, left), py(y)) for x, y in pts]
            if len(coords) > 1:
                path = " ".join(f"{a:.1f},{b:.1f}" for a, b in coords)
                out.append(f'<polyline points="{path}" fill="none" stroke="{c}" '
                           'stroke-width="2"/>')  # fmt: skip
            for a, b in coords:
                out.append(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="3.5" fill="{c}"/>')
    mid = mt + ph / 2
    out.append(
        f'<text x="14" y="{mid}" text-anchor="middle" transform="rotate(-90 14 {mid})">'
        "median time (ms, log scale)</text>"
    )
    for j, alg in enumerate(algs):
        lx = ml + (j % 3) * (pw + gap)
        ly = mt + ph + 56 + (j // 3) * 18
        out.append(f'<rect x="{lx}" y="{ly - 9}" width="12" height="12" fill="{color[alg]}"/>')
        out.append(f'<text x="{lx + 18}" y="{ly + 2}">{alg}</text>')
    out.append("</svg>")
    return "\n".join(out)


def chart_png(rows: list[dict[str, Any]]) -> bytes | None:
    """The same chart as a PNG made with matplotlib; None when matplotlib isn't installed."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import io

        import matplotlib.pyplot as plt
    except Exception:
        return None
    series = _series(rows)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8), sharey=True)
    for ax, fam in zip(axes, FAMILIES, strict=True):
        for alg, pts in series[fam].items():
            ax.plot([x for x, _ in pts], [y for _, y in pts], marker="o", label=alg)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_title(FAMILY_TITLES[fam])
        ax.set_xlabel("people in the graph (n)")
        ax.grid(True, which="both", alpha=0.3)
        if series[fam]:
            ax.legend(fontsize=7)
    axes[0].set_ylabel("median time (ms)")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
