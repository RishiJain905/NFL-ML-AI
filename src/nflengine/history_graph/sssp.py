"""Shortest-path algorithms on a CSR graph, for the path benchmark (AE03, `bench.py`).

Everything here is a pure function of three arrays: `indptr`, `indices`, `weights` in CSR form
(the arcs of vertex `u` are `indices[indptr[u]:indptr[u+1]]` with the matching `weights`;
weights are finite and non-negative). An undirected graph stores both arcs of every link.

What is here:

- `csr_from_edges`: edge list -> CSR (undirected: both arcs; parallel arcs keep the smallest
  weight; self-loops dropped).
- `dijkstra_heap`: textbook Dijkstra on a binary heap (`heapq`), pure Python. The same-language
  baseline for `bmssp_sssp`.
- `bmssp_sssp`: our own implementation of Duan, Mao, Mao, Shu and Yin (2025), "Breaking the
  Sorting Barrier for Directed Single-Source Shortest Paths" (arXiv 2504.17033, STOC 2025),
  O(m log^(2/3) n) in the comparison-addition model. Written from the paper alone; no third-party
  implementation was read or run.
- `to_constant_degree`: the paper's constant-degree transformation (optional, see below).
- `floyd_warshall_numba`: O(n^3) all-pairs, compiled with numba.
- `dense_from_csr`, `scipy_matrix`: adapters for the all-pairs routines and for scipy.

The algorithm in plain words
============================

Dijkstra's algorithm keeps a priority queue of "frontier" vertices and always settles the closest
one. Doing that for all n vertices means sorting them by distance, which costs about n log n
comparisons: the "sorting barrier". Bellman-Ford needs no sorting (it just relaxes edges again
and again) but takes m work per hop.

BMSSP ("bounded multi-source shortest path") mixes the two by divide and conquer. Call
`bmssp(l, B, S)`: "starting from the vertices S, find every vertex whose true distance is below
the bound B and whose shortest path goes through S". Level `l` handles at most 2^(l*t) sources.
It works in four moves:

1. **FindPivots** (`_find_pivots`). Run k rounds of Bellman-Ford from S. A vertex whose shortest
   path from S has fewer than k hops is now settled for good. The sources whose shortest-path
   trees have at least k vertices are the only ones the recursion still needs ("pivots"); there
   are at most 1/k as many of them as vertices we are about to find, so the sorting work shrinks
   by that factor. (If the k rounds touch more than k|S| vertices, every source stays a pivot.)
2. **A partial-sorting queue** (`PartialSortQueue`, the paper's Lemma 3.3). The pivots go into a
   queue that can `pull` the M = 2^((l-1)t) smallest keys without sorting everything, `insert`
   one key, or `batch_prepend` a group of keys all smaller than everything stored.
3. **Recurse** (`_Bmssp.run`). Pull a batch S_i, call `bmssp(l-1, B_i, S_i)` (B_i is the smallest
   value still in the queue, so the call only has to settle what is below it), relax the edges
   out of what came back, and put the new frontier vertices back into the queue: those at or
   above B_i go in with `insert`, those between the call's new bound B'_i and B_i go in with
   `batch_prepend` (they are smaller than everything left). A call that is cut short (it
   returns B'_i < B_i) is a "partial execution".
4. **Base case** (`_base_case`). At level 0 the source is a single settled vertex: run a tiny
   Dijkstra from it and stop once k + 1 vertices are settled; the (k+1)-th gives the bound.

The parameters are k = floor(log2(n)^(1/3)) and t = floor(log2(n)^(2/3)), the top call has level
l = ceil(log2(n) / t), S = {source} and B = infinity, which gives the O(m log^(2/3) n) bound.
For our 5,665-vertex person graph that is k = 2, t = 5, l = 3: three levels, and pivots only
have to beat trees of two vertices. That is why the theory's advantage does not show up at this
size (the benchmark's point).

Tie-breaking (the paper's Assumption 2.1, section 2)
----------------------------------------------------

The proofs need every path to have a different length, so ties are broken by comparing the
tuple (length, number of vertices, then the vertices from the end of the path back to the
source). We store, per vertex v, `dist[v]`, `hops[v]` (vertices on the path) and `pred[v]` and
use the key `(dist[v], hops[v], v)` as the vertex's value everywhere, so two vertices never
compare equal. Relaxing arc (u, v) is "valid" when `(dist[u] + w, hops[u] + 1, u)` is at most
`(dist[v], hops[v], pred[v])`; equality (the same path again) counts as valid, which the paper
requires so that an arc relaxed at a lower level can be used again at the upper levels (its
Remark 3.4). The bounds B are such keys too. This also makes `pred` a forest, so the pivot rule
("roots of trees with at least k vertices") is well defined even with zero weights and many
equal weights (the person graph's weights are 1 / snaps: lots of ties).

Does it need the constant-degree transformation?
-------------------------------------------------

The *running-time bound* does (the paper assumes in- and out-degree at most 2: it is what keeps
|W| = O(k|S|) in FindPivots and |U| = O(k 2^(lt)) per call). *Correctness* does not: every
step is sound on any graph. So `bmssp_sssp(..., constant_degree=False)` (the default) runs on
the graph as given, and `constant_degree=True` first applies the paper's transformation (each
vertex becomes a zero-weight cycle with one node per neighbour; each edge joins two cycle
nodes), which makes the graph about as large as its arc count. The person graph has hubs with
hundreds of links, so on it the default FindPivots usually hits its "touched more than k|S|"
exit at once and keeps every source as a pivot; the benchmark times both variants.

Where this implementation and the paper's cost model part ways
---------------------------------------------------------------

- One addition to the pseudocode. After a recursive call returns, the vertices it completed are
  deleted from the queue (the paper's own Delete, O(1) each). Algorithm 3 leaves their old
  entries in D, but its proofs (Remark 3.8, Lemma 3.10) assume D holds nothing below the
  last bound B'. When a deeper call improves a vertex that is still queued with its old, larger
  estimate, the old entry is pulled again later and the vertex is returned twice; |U| then
  counts it twice and can reach its cap early. The tests found this on a 16-vertex graph
  with equal weights (the top call stopped before settling one vertex).
- The queue's block search tree is a `bisect` over a Python list (same O(log) search), and its
  median splits and the batch-prepend partition use `sorted` (C code) instead of linear-time
  median selection: output identical, comparison count not.
- Python's `heapq` stands in for the base case's heap with lazy deletion (no decrease-key).
- Everything is plain Python lists and tuples: constant factors dwarf any log-factor saving.
  Do not read the benchmark as a verdict on the paper's asymptotics, only on what one can buy
  today at this size.
"""

from __future__ import annotations

import heapq
import math
from bisect import bisect_left
from collections import deque
from dataclasses import dataclass
from operator import itemgetter
from typing import Any

import numpy as np

INF = math.inf
_BY_VALUE = itemgetter(1)

# ---- CSR helpers -----------------------------------------------------------------------------


def csr_from_edges(n: int, src, dst, weight, *, undirected: bool = True):
    """`(indptr, indices, weights)` of the graph with `n` vertices and the given edges.

    `undirected` adds the reverse arc of every edge. Self-loops are dropped and parallel arcs
    keep the smallest weight, so every algorithm (and scipy, which would add duplicates) sees
    the same graph. `indptr` / `indices` are int32 (what scipy's csgraph wants), `weights`
    float64.
    """
    s = np.asarray(src, dtype=np.int64).reshape(-1)
    d = np.asarray(dst, dtype=np.int64).reshape(-1)
    w = np.asarray(weight, dtype=np.float64).reshape(-1)
    if not (len(s) == len(d) == len(w)):
        raise ValueError("src, dst and weight must have the same length")
    if len(w) and (w.min() < 0 or not np.isfinite(w).all()):
        raise ValueError("weights must be finite and non-negative")
    if len(s) and (s.min() < 0 or d.min() < 0 or s.max() >= n or d.max() >= n):
        raise ValueError("an edge end is outside 0..n-1")
    if undirected:
        s, d, w = np.concatenate([s, d]), np.concatenate([d, s]), np.concatenate([w, w])
    keep = s != d
    s, d, w = s[keep], d[keep], w[keep]
    order = np.lexsort((w, d, s))  # by source, then target, then weight
    s, d, w = s[order], d[order], w[order]
    first = np.ones(len(s), dtype=bool)
    first[1:] = (s[1:] != s[:-1]) | (d[1:] != d[:-1])
    s, d, w = s[first], d[first], w[first]
    indptr = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(np.bincount(s, minlength=n), out=indptr[1:])
    return indptr.astype(np.int32), d.astype(np.int32), w


def scipy_matrix(n: int, indptr, indices, weights):
    """The graph as a scipy CSR matrix (explicit zeros stay edges of weight zero)."""
    from scipy.sparse import csr_matrix

    return csr_matrix(
        (np.asarray(weights, dtype=np.float64), np.asarray(indices), np.asarray(indptr)),
        shape=(n, n),
    )


def dense_from_csr(n: int, indptr, indices, weights) -> np.ndarray:
    """An n x n float64 matrix: arc weights, `inf` for no arc, 0 on the diagonal."""
    ptr = np.asarray(indptr, dtype=np.int64)
    src = np.repeat(np.arange(n, dtype=np.int64), np.diff(ptr))
    mat = np.full((n, n), np.inf)
    mat[src, np.asarray(indices, dtype=np.int64)] = weights
    np.fill_diagonal(mat, 0.0)
    return mat


def _as_list(x):
    return x.tolist() if hasattr(x, "tolist") else x


def _lists(n: int, indptr, indices, weights, source: int):
    """The CSR arrays as Python lists (indexing numpy scalars one by one is several times slower
    than indexing a list; pass lists yourself to pay that conversion once for many queries)."""
    if not 0 <= source < n:
        raise ValueError("source is outside 0..n-1")
    ptr, idx, wt = _as_list(indptr), _as_list(indices), _as_list(weights)
    if len(ptr) != n + 1 or len(idx) != len(wt) or ptr[-1] != len(idx):
        raise ValueError("indptr, indices and weights don't describe an n-vertex CSR graph")
    if wt and (min(wt) < 0 or not max(wt) < INF):
        raise ValueError("weights must be finite and non-negative")
    return ptr, idx, wt


# ---- Dijkstra on a binary heap ----------------------------------------------------------------


def dijkstra_heap(n: int, indptr, indices, weights, source: int) -> np.ndarray:
    """Single-source shortest-path distances (inf where unreachable): Dijkstra's algorithm on a
    `heapq` binary heap with lazy deletion, pure Python."""
    ptr, idx, wt = _lists(n, indptr, indices, weights, source)
    dist = [INF] * n
    dist[source] = 0.0
    heap = [(0.0, source)]
    pop, push = heapq.heappop, heapq.heappush
    while heap:
        d, u = pop(heap)
        if d > dist[u]:
            continue  # a stale entry: u was already settled with a smaller value
        a, b = ptr[u], ptr[u + 1]
        for v, w in zip(idx[a:b], wt[a:b], strict=True):
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                push(heap, (nd, v))
    return np.asarray(dist, dtype=np.float64)


# ---- the constant-degree transformation --------------------------------------------------------


@dataclass(frozen=True)
class ConstantDegreeGraph:
    """The paper's transformed graph: `rep[v]` is a node on v's zero-weight cycle (all nodes of a
    cycle are at the same distance, so any of them stands for v)."""

    n: int  # nodes of the transformed graph
    indptr: Any
    indices: Any
    weights: Any
    rep: np.ndarray  # original vertex -> one of its nodes
    n_original: int

    def distances(self, source: int, **kwargs) -> np.ndarray:
        """`bmssp_sssp` on the transformed graph, mapped back to the original vertices."""
        d = bmssp_sssp(
            self.n, self.indptr, self.indices, self.weights, int(self.rep[source]), **kwargs
        )
        return d[self.rep]


def to_constant_degree(n: int, indptr, indices, weights) -> ConstantDegreeGraph:
    """Substitute every vertex v by a directed zero-weight cycle with one node x(v, w) for each
    neighbour w of v (in either direction; a vertex with no neighbour keeps one node), and every
    arc (u, v) by an arc x(u, v) -> x(v, u) of the same weight. Shortest paths are preserved, in-
    and out-degree are at most 2 and the graph has O(m) nodes and arcs."""
    if n >= 3_000_000_000:
        raise ValueError("graph too large for the int64 pair encoding")
    ptr = np.asarray(indptr, dtype=np.int64)
    dst = np.asarray(indices, dtype=np.int64)
    wt = np.asarray(weights, dtype=np.float64)
    src = np.repeat(np.arange(n, dtype=np.int64), np.diff(ptr))
    pair = np.unique(np.concatenate([src * n + dst, dst * n + src]))  # (vertex, neighbour)
    pv = pair // n
    deg = np.bincount(pv, minlength=n)
    size = np.maximum(deg, 1)
    off = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(size, out=off[1:])
    first = np.zeros(n + 1, dtype=np.int64)  # position of each vertex's first pair in `pair`
    np.cumsum(deg, out=first[1:])

    def node(v: np.ndarray, w: np.ndarray) -> np.ndarray:
        return off[v] + (np.searchsorted(pair, v * n + w) - first[v])

    arc_from, arc_to = node(src, dst), node(dst, src)
    cyc = np.flatnonzero(deg >= 2)  # cycle arcs i -> i + 1 (mod deg), weight 0
    if len(cyc):
        counts = deg[cyc]
        v_rep = np.repeat(cyc, counts)
        local = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
        c_from = off[v_rep] + local
        c_to = off[v_rep] + (local + 1) % np.repeat(counts, counts)
    else:
        c_from = c_to = np.zeros(0, dtype=np.int64)
    n2 = int(off[-1])
    ptr2, idx2, wt2 = csr_from_edges(
        n2,
        np.concatenate([arc_from, c_from]),
        np.concatenate([arc_to, c_to]),
        np.concatenate([wt, np.zeros(len(c_from))]),
        undirected=False,
    )
    return ConstantDegreeGraph(n2, ptr2, idx2, wt2, off[:-1].copy(), n)


# ---- the partial-sorting queue (the paper's Lemma 3.3) -----------------------------------------


class _Block:
    __slots__ = ("items", "ub")

    def __init__(self, items: dict, ub=None):
        self.items = items  # key -> value
        self.ub = ub  # an upper bound on the values (D1 blocks only)


class PartialSortQueue:
    """The data structure of Lemma 3.3: key / value pairs (values are totally ordered; here
    tuples) in two sequences of blocks of at most M pairs each.

    - `D1` takes `insert`s. Its blocks are ordered by value (every value in a block is at most
      the block's upper bound, which is at most every value of the next block); the upper bounds
      live in a sorted list searched with `bisect` (the paper's balanced search tree). A block
      that grows past M is split at its median.
    - `D0` takes `batch_prepend`s: new blocks (at most ceil(M/2) pairs each when the batch is
      larger than M) go in front of the old ones; they hold values smaller than everything
      stored.
    - `pull` returns up to M keys with the smallest values and a bound x: every returned value
      is below x and every remaining value is at least x (x = B, the structure's upper bound,
      when nothing remains). Only a prefix of D0's and of D1's blocks is looked at.

    A key is stored once, with its smallest value: inserting a key again keeps the smaller one.
    """

    def __init__(self, m: int, upper_bound):
        self.m = max(1, int(m))
        self.bound = upper_bound
        self._reset()

    def _reset(self) -> None:
        self._d0: deque[_Block] = deque()
        self._d1: list[_Block] = [_Block({}, self.bound)]
        self._ubs: list = [self.bound]
        self._home: dict[Any, _Block] = {}

    def __len__(self) -> int:
        return len(self._home)

    # -- insert --
    def insert(self, key, value) -> None:
        home = self._home.get(key)
        if home is not None:
            if not value < home.items[key]:
                return  # the stored value is already the smaller one
            self._remove(key, home)
        i = min(bisect_left(self._ubs, value), len(self._ubs) - 1)
        block = self._d1[i]
        block.items[key] = value
        self._home[key] = block
        if len(block.items) > self.m:
            self._split(i)

    def _split(self, i: int) -> None:
        block = self._d1[i]
        ordered = sorted(block.items.items(), key=_BY_VALUE)
        half = (len(ordered) + 1) // 2
        low = _Block(dict(ordered[:half]), ordered[half - 1][1])
        block.items = dict(ordered[half:])
        for key in low.items:
            self._home[key] = low
        self._d1.insert(i, low)
        self._ubs.insert(i, low.ub)

    def discard(self, key) -> None:
        """The paper's Delete: drop a key (no-op if it isn't stored)."""
        home = self._home.get(key)
        if home is not None:
            self._remove(key, home)

    def _remove(self, key, block: _Block) -> None:
        del block.items[key]
        del self._home[key]
        # an emptied D1 block goes (its bound with it), except the last, which holds B
        if not block.items and block.ub is not None and block is not self._d1[-1]:
            i = bisect_left(self._ubs, block.ub)
            del self._d1[i]
            del self._ubs[i]

    # -- batch prepend --
    def batch_prepend(self, pairs) -> None:
        """Store `(key, value)` pairs whose values are all smaller than anything stored. A key
        given twice, or already stored, keeps its smallest value."""
        best: dict[Any, Any] = {}
        for key, value in pairs:
            if key not in best or value < best[key]:
                best[key] = value
        fresh: list[tuple[Any, Any]] = []
        for key, value in best.items():
            home = self._home.get(key)
            if home is not None:
                if not value < home.items[key]:
                    continue
                self._remove(key, home)
            fresh.append((key, value))
        if not fresh:
            return
        if len(fresh) <= self.m:
            parts = [fresh]
        else:  # blocks of at most ceil(M / 2), in value order
            fresh.sort(key=_BY_VALUE)
            step = max(1, (self.m + 1) // 2)
            parts = [fresh[i : i + step] for i in range(0, len(fresh), step)]
        for part in reversed(parts):
            block = _Block(dict(part))
            for key, _ in part:
                self._home[key] = block
            self._d0.appendleft(block)

    # -- pull --
    def _prefix(self, blocks) -> list[_Block]:
        out, count = [], 0
        for block in blocks:
            if block.items:
                out.append(block)
                count += len(block.items)
                if count >= self.m:
                    break
        return out

    def pull(self) -> tuple[list, Any]:
        """`(keys, x)`: up to M keys holding the smallest values, and the separating bound x."""
        total = len(self._home)
        if total == 0:
            return [], self.bound
        while self._d0 and not self._d0[0].items:
            self._d0.popleft()
        cand = [
            (key, value)
            for block in (*self._prefix(self._d0), *self._prefix(self._d1))
            for key, value in block.items.items()
        ]
        if total <= self.m:  # everything fits: the prefixes covered all of it
            keys = [key for key, _ in cand]
            self._reset()
            return keys, self.bound
        cand.sort(key=_BY_VALUE)
        chosen = cand[: self.m]
        for key, _ in chosen:
            self._remove(key, self._home[key])
        return [key for key, _ in chosen], self._smallest()

    def _smallest(self):
        best = None
        while self._d0 and not self._d0[0].items:
            self._d0.popleft()
        if self._d0:
            best = min(self._d0[0].items.values())
        for block in self._d1:
            if block.items:
                v = min(block.items.values())
                if best is None or v < best:
                    best = v
                break
        return self.bound if best is None else best


# ---- BMSSP --------------------------------------------------------------------------------------


def bmssp_parameters(n: int) -> tuple[int, int, int]:
    """`(k, t, levels)` for an n-vertex graph: k = floor(log2(n)^(1/3)), t = floor(log2(n)^(2/3)),
    levels = ceil(log2(n) / t) (each at least 1)."""
    lg = math.log2(max(int(n), 2))
    k = max(1, int(lg ** (1 / 3)))
    t = max(1, int(lg ** (2 / 3)))
    return k, t, max(1, math.ceil(lg / t))


class _Bmssp:
    """One BMSSP run: the shared estimates `dist` / `hops` / `pred` and the recursion."""

    def __init__(self, n, ptr, idx, wt, k, t, stats):
        self.ptr, self.idx, self.wt = ptr, idx, wt
        self.k, self.t = k, t
        self.dist = [INF] * n
        self.hops = [0] * n
        self.pred = [-1] * n
        self.stats = stats

    # -- Algorithm 1 --
    def _find_pivots(self, bound, sources):
        k, ptr, idx, wt = self.k, self.ptr, self.idx, self.wt
        dist, hops, pred = self.dist, self.hops, self.pred
        reached = set(sources)  # W
        frontier = list(sources)  # W_(i-1)
        limit = k * len(sources)
        for _ in range(k):
            updated = set()
            for u in frontier:
                du, hv = dist[u], hops[u] + 1
                a, b = ptr[u], ptr[u + 1]
                for v, w in zip(idx[a:b], wt[a:b], strict=True):
                    nd = du + w
                    dv = dist[v]
                    if nd < dv or (nd == dv and (hv < hops[v] or (hv == hops[v] and u <= pred[v]))):
                        dist[v], hops[v], pred[v] = nd, hv, u
                        if (nd, hv, v) < bound:
                            updated.add(v)
            reached |= updated
            if len(reached) > limit:
                if self.stats is not None:
                    self.stats["pivot_early_exit"] += 1
                return list(sources), reached  # too many to shrink: every source is a pivot
            frontier = updated
        # F: the tight arcs inside W (a forest, thanks to the tie-break); a source is a pivot
        # when it is a root with at least k vertices below it (itself included)
        parent: dict[int, int] = {}
        children: dict[int, list[int]] = {}
        for u in reached:
            du, hv = dist[u], hops[u] + 1
            a, b = ptr[u], ptr[u + 1]
            for v, w in zip(idx[a:b], wt[a:b], strict=True):
                if v in reached and pred[v] == u and hops[v] == hv and dist[v] == du + w:
                    parent[v] = u
                    children.setdefault(u, []).append(v)
        pivots = []
        for u in sources:
            if u in parent:
                continue
            size, stack = 0, [u]
            while stack and size < k:
                size += 1
                stack.extend(children.get(stack.pop(), ()))
            if size >= k:
                pivots.append(u)
        return pivots, reached

    # -- Algorithm 2 --
    def _base_case(self, bound, x):
        k, ptr, idx, wt = self.k, self.ptr, self.idx, self.wt
        dist, hops, pred = self.dist, self.hops, self.pred
        heap = [(dist[x], hops[x], x)]
        queued = {x}
        settled: list[int] = []
        seen: set[int] = set()
        while heap and len(settled) < k + 1:
            d, h, u = heapq.heappop(heap)
            if u in seen or d != dist[u] or h != hops[u]:
                continue  # settled already, or a stale entry
            seen.add(u)
            settled.append(u)
            hv = h + 1
            a, b = ptr[u], ptr[u + 1]
            for v, w in zip(idx[a:b], wt[a:b], strict=True):
                nd = d + w
                dv = dist[v]
                valid = nd < dv or (nd == dv and (hv < hops[v] or (hv == hops[v] and u <= pred[v])))
                if valid and (nd, hv, v) < bound:
                    changed = nd != dv or hv != hops[v] or u != pred[v]
                    dist[v], hops[v], pred[v] = nd, hv, u
                    if v not in queued:
                        queued.add(v)
                        heapq.heappush(heap, (nd, hv, v))
                    elif changed:
                        heapq.heappush(heap, (nd, hv, v))
        if len(settled) <= k:
            return bound, settled
        last = settled[-1]
        return (dist[last], hops[last], last), settled[:-1]

    # -- Algorithm 3 --
    def run(self, level, bound, sources):
        """`(B', U)`: a new bound B' <= B and the vertices below it reached through `sources`."""
        st = self.stats
        if st is not None:
            st["calls"][level] = st["calls"].get(level, 0) + 1
        if level == 0:
            if st is not None:
                st["base_cases"] += 1
            return self._base_case(bound, sources[0])
        k, t = self.k, self.t
        ptr, idx, wt = self.ptr, self.idx, self.wt
        dist, hops, pred = self.dist, self.hops, self.pred
        pivots, reached = self._find_pivots(bound, sources)
        if st is not None:
            st["sources"] += len(sources)
            st["pivots"] += len(pivots)
            st["reached"] += len(reached)
        queue = PartialSortQueue(1 << ((level - 1) * t), bound)
        for x in pivots:
            queue.insert(x, (dist[x], hops[x], x))
        new_bound = min((dist[x], hops[x], x) for x in pivots) if pivots else bound
        done: list[int] = []
        cap = k * (1 << (level * t))
        while len(done) < cap and len(queue):
            batch, low = queue.pull()
            new_bound, part = self.run(level - 1, low, batch)
            done.extend(part)
            carry: list[tuple[int, tuple]] = []
            for u in part:
                # u is complete now: drop its old entry (an estimate from before the call, kept
                # if a deeper call improved u). Lemmas 3.7 and 3.10 assume D holds nothing below
                # B'_(i-1); without this the same vertex is pulled and returned a second time.
                queue.discard(u)
                du, hv = dist[u], hops[u] + 1
                a, b = ptr[u], ptr[u + 1]
                for v, w in zip(idx[a:b], wt[a:b], strict=True):
                    nd = du + w
                    dv = dist[v]
                    if nd < dv or (nd == dv and (hv < hops[v] or (hv == hops[v] and u <= pred[v]))):
                        dist[v], hops[v], pred[v] = nd, hv, u
                        key = (nd, hv, v)
                        if low <= key < bound:
                            queue.insert(v, key)
                        elif new_bound <= key < low:
                            carry.append((v, key))
            for x in batch:
                key = (dist[x], hops[x], x)
                if new_bound <= key < low:
                    carry.append((x, key))
            queue.batch_prepend(carry)
        out_bound = min(new_bound, bound)
        if st is not None and out_bound < bound:
            st["partial"] += 1
        have = set(done)
        done.extend(x for x in reached if x not in have and (dist[x], hops[x], x) < out_bound)
        return out_bound, done


def bmssp_sssp(
    n: int,
    indptr,
    indices,
    weights,
    source: int,
    *,
    constant_degree: bool = False,
    k: int | None = None,
    t: int | None = None,
    stats: dict | None = None,
) -> np.ndarray:
    """Single-source shortest-path distances by Duan, Mao, Mao, Shu and Yin's BMSSP (see the module
    docstring): float64 array, `inf` where unreachable.

    `constant_degree` applies the paper's transformation first (see `to_constant_degree`; pass a
    `ConstantDegreeGraph` to `ConstantDegreeGraph.distances` to pay it once for many sources).
    `k`, `t` override the paper's parameters (tests use small ones to force deep recursions).
    `stats`, if a dict, is filled with `k, t, levels, n`, the calls per level, base cases,
    partial executions, and the FindPivots totals (`sources`, `pivots`, `reached`,
    `pivot_early_exit`).
    """
    ptr, idx, wt = _lists(n, indptr, indices, weights, source)
    if constant_degree:
        cd = to_constant_degree(n, ptr, idx, wt)
        return cd.distances(source, k=k, t=t, stats=stats)
    pk, pt, levels = bmssp_parameters(n)
    k = pk if k is None else k
    t = pt if t is None else t
    if k < 1 or t < 1:
        raise ValueError("k and t must be at least 1")
    if k != pk or t != pt:  # custom parameters: enough levels for 2^(levels * t) >= n
        levels = max(1, math.ceil(math.log2(max(n, 2)) / t))
    if stats is not None:
        stats.update(k=k, t=t, levels=levels, n=n, calls={}, base_cases=0, partial=0, sources=0,
                     pivots=0, reached=0, pivot_early_exit=0)  # fmt: skip
    solver = _Bmssp(n, ptr, idx, wt, k, t, stats)
    solver.dist[source], solver.hops[source] = 0.0, 1
    solver.run(levels, (INF, 0, 0), [source])
    return np.asarray(solver.dist, dtype=np.float64)


# ---- Floyd-Warshall with numba -----------------------------------------------------------------

_KERNELS: dict[bool, Any] = {}


def _kernel(parallel: bool):
    """The compiled kernels (numba is imported and the code compiled on first use, not at import
    time; `cache=False` so nothing is written into the source tree)."""
    if not _KERNELS:
        from numba import njit, prange

        @njit(parallel=True, cache=False)
        def fw_parallel(d):
            n = d.shape[0]
            for k in range(n):
                for i in prange(n):
                    dik = d[i, k]
                    if dik != np.inf:
                        for j in range(n):
                            v = dik + d[k, j]
                            if v < d[i, j]:
                                d[i, j] = v

        @njit(cache=False)
        def fw_serial(d):
            n = d.shape[0]
            for k in range(n):
                for i in range(n):
                    dik = d[i, k]
                    if dik != np.inf:
                        for j in range(n):
                            v = dik + d[k, j]
                            if v < d[i, j]:
                                d[i, j] = v

        _KERNELS[True], _KERNELS[False] = fw_parallel, fw_serial
    return _KERNELS[parallel]


def warm_up_floyd_warshall(parallel: bool = True) -> float:
    """Compile the kernel (on a 3 x 3 matrix) and return the seconds it took: the first call in a
    process pays the JIT, later calls return about 0."""
    import time

    t0 = time.perf_counter()
    floyd_warshall_numba(np.array([[0.0, 1.0, np.inf], [1.0, 0.0, 2.0], [np.inf, 2.0, 0.0]]),
                         parallel=parallel)  # fmt: skip
    return time.perf_counter() - t0


def floyd_warshall_numba(dist: np.ndarray, *, parallel: bool = True) -> np.ndarray:
    """All-pairs shortest paths of a dense matrix (`inf` = no arc, 0 diagonal, weights >= 0) in
    O(n^3), compiled by numba (`parallel`: the rows of each k-step are split over threads). Returns
    a new matrix; the input is left alone."""
    d = np.array(dist, dtype=np.float64, order="C", copy=True)
    if d.ndim != 2 or d.shape[0] != d.shape[1]:
        raise ValueError("dist must be a square matrix")
    np.fill_diagonal(d, np.minimum(np.diag(d), 0.0))
    _kernel(parallel)(d)
    return d
