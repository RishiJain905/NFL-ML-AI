"""The shortest-path algorithms of the path benchmark (AE03, `history_graph/sssp.py`): the pure
heap Dijkstra, our BMSSP (Duan, Mao, Mao, Shu, Yin 2025), the constant-degree transformation, the
partial-sorting queue of its Lemma 3.3 and the numba Floyd-Warshall, all against scipy.

No network, no Neo4j, no W&B. Weights are integers on purpose in most graphs (sums are exact, so
ties between paths are everywhere and the paper's tie-break is exercised), plus real weights, zero
weights and unreachable vertices.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.sparse.csgraph import dijkstra as sp_dijkstra

from nflengine.history_graph import sssp as S

# ---- helpers ------------------------------------------------------------------------------------


def random_graph(seed: int, n: int, m: int, *, directed: bool, mode: str):
    rng = np.random.default_rng(seed)
    src = rng.integers(0, n, m)
    dst = rng.integers(0, n, m)
    if mode == "int":
        w = rng.integers(1, 4, m).astype(float)
    elif mode == "zero":
        w = rng.integers(0, 3, m).astype(float)
    elif mode == "one":
        w = np.ones(m)
    elif mode == "float":
        w = rng.random(m) + 0.01
    else:
        raise ValueError(mode)
    return S.csr_from_edges(n, src, dst, w, undirected=not directed)


def scipy_reference(n, ptr, idx, wt, source):
    return sp_dijkstra(S.scipy_matrix(n, ptr, idx, wt), directed=True, indices=source)


def bellman_ford(n, ptr, idx, wt, source):
    """An independent reference (zero weights included): relax every arc until nothing changes."""
    dist = np.full(n, np.inf)
    dist[source] = 0.0
    src = np.repeat(np.arange(n), np.diff(np.asarray(ptr, dtype=np.int64)))
    for _ in range(n + 1):
        cand = dist[src] + np.asarray(wt)
        new = dist.copy()
        np.minimum.at(new, np.asarray(idx), cand)
        if np.array_equal(new, dist):
            break
        dist = new
    return dist


def assert_same(a, b):
    a, b = np.asarray(a), np.asarray(b)
    assert a.shape == b.shape
    assert np.array_equal(np.isinf(a), np.isinf(b)), "different unreachable sets"
    fin = ~np.isinf(a)
    assert np.allclose(a[fin], b[fin], rtol=0, atol=1e-9)


MODES = ["int", "zero", "one", "float"]
PARAMS = [{}, {"k": 1, "t": 1}, {"k": 2, "t": 1}, {"k": 2, "t": 2}, {"k": 3, "t": 2}]

# ---- csr_from_edges -----------------------------------------------------------------------------


class TestCsr:
    def test_undirected_graph_stores_both_arcs(self):
        ptr, idx, wt = S.csr_from_edges(3, [0, 1], [1, 2], [2.0, 5.0])
        assert ptr.tolist() == [0, 1, 3, 4]
        assert idx.tolist() == [1, 0, 2, 1]
        assert wt.tolist() == [2.0, 2.0, 5.0, 5.0]
        assert ptr.dtype == np.int32 and idx.dtype == np.int32 and wt.dtype == np.float64

    def test_directed_graph_stores_the_arcs_given(self):
        ptr, idx, wt = S.csr_from_edges(3, [0, 1], [1, 2], [2.0, 5.0], undirected=False)
        assert ptr.tolist() == [0, 1, 2, 2]
        assert idx.tolist() == [1, 2]

    def test_parallel_arcs_keep_the_smallest_and_self_loops_go(self):
        ptr, idx, wt = S.csr_from_edges(2, [0, 0, 0, 1], [1, 1, 0, 1], [5.0, 3.0, 1.0, 1.0])
        assert idx.tolist() == [1, 0]
        assert wt.tolist() == [3.0, 3.0]
        assert ptr.tolist() == [0, 1, 2]

    def test_no_edges(self):
        ptr, idx, wt = S.csr_from_edges(4, [], [], [])
        assert ptr.tolist() == [0, 0, 0, 0, 0]
        assert len(idx) == len(wt) == 0

    @pytest.mark.parametrize(
        "bad",
        [
            dict(n=2, src=[0], dst=[1], weight=[-1.0]),
            dict(n=2, src=[0], dst=[1], weight=[float("inf")]),
            dict(n=2, src=[0], dst=[2], weight=[1.0]),
            dict(n=2, src=[0, 1], dst=[1], weight=[1.0]),
        ],
    )
    def test_bad_input_is_refused(self, bad):
        with pytest.raises(ValueError):
            S.csr_from_edges(bad["n"], bad["src"], bad["dst"], bad["weight"])

    def test_dense_matrix(self):
        ptr, idx, wt = S.csr_from_edges(3, [0], [1], [4.0], undirected=False)
        d = S.dense_from_csr(3, ptr, idx, wt)
        assert d[0, 1] == 4.0 and d[1, 0] == np.inf and (np.diag(d) == 0).all()


# ---- dijkstra_heap ------------------------------------------------------------------------------


class TestDijkstraHeap:
    def test_a_path_graph(self):
        n = 6
        ptr, idx, wt = S.csr_from_edges(n, range(n - 1), range(1, n), [1, 2, 3, 4, 5])
        assert S.dijkstra_heap(n, ptr, idx, wt, 0).tolist() == [0, 1, 3, 6, 10, 15]
        assert S.dijkstra_heap(n, ptr, idx, wt, 5).tolist() == [15, 14, 12, 9, 5, 0]

    def test_a_single_vertex(self):
        ptr, idx, wt = S.csr_from_edges(1, [], [], [])
        assert S.dijkstra_heap(1, ptr, idx, wt, 0).tolist() == [0.0]

    def test_unreachable_is_inf(self):
        ptr, idx, wt = S.csr_from_edges(4, [0, 2], [1, 3], [1.0, 1.0])
        d = S.dijkstra_heap(4, ptr, idx, wt, 0)
        assert d[:2].tolist() == [0.0, 1.0] and np.isinf(d[2:]).all()

    def test_a_shortcut_beats_the_direct_arc(self):
        ptr, idx, wt = S.csr_from_edges(3, [0, 0, 1], [2, 1, 2], [10.0, 1.0, 2.0])
        assert S.dijkstra_heap(3, ptr, idx, wt, 0).tolist() == [0.0, 1.0, 3.0]

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("directed", [False, True])
    @pytest.mark.parametrize("seed", range(6))
    def test_equals_scipy(self, seed, directed, mode):
        n = 40 + 7 * seed
        ptr, idx, wt = random_graph(seed, n, 3 * n, directed=directed, mode=mode)
        for s in (0, n // 2, n - 1):
            assert_same(S.dijkstra_heap(n, ptr, idx, wt, s), bellman_ford(n, ptr, idx, wt, s))
            if mode != "zero":
                assert_same(
                    S.dijkstra_heap(n, ptr, idx, wt, s), scipy_reference(n, ptr, idx, wt, s)
                )

    def test_python_lists_work_like_arrays(self):
        ptr, idx, wt = random_graph(1, 30, 90, directed=False, mode="float")
        a = S.dijkstra_heap(30, ptr, idx, wt, 3)
        b = S.dijkstra_heap(30, ptr.tolist(), idx.tolist(), wt.tolist(), 3)
        assert np.array_equal(a, b)

    def test_bad_input_is_refused(self):
        ptr, idx, wt = S.csr_from_edges(3, [0], [1], [1.0])
        with pytest.raises(ValueError):
            S.dijkstra_heap(3, ptr, idx, wt, 3)
        with pytest.raises(ValueError):
            S.dijkstra_heap(3, ptr, idx, np.array([-1.0, 1.0]), 0)
        with pytest.raises(ValueError):
            S.dijkstra_heap(4, ptr, idx, wt, 0)  # indptr is for 3 vertices


# ---- bmssp --------------------------------------------------------------------------------------


class TestParameters:
    @pytest.mark.parametrize(
        "n, expected",
        [(1, (1, 1, 1)), (2, (1, 1, 1)), (16, (1, 2, 2)), (1000, (2, 4, 3)), (5665, (2, 5, 3))],
    )
    def test_k_t_and_levels(self, n, expected):
        assert S.bmssp_parameters(n) == expected

    def test_the_top_call_can_hold_every_vertex(self):
        for n in (2, 3, 10, 100, 5665, 10**6):
            k, t, levels = S.bmssp_parameters(n)
            assert 2 ** (levels * t) >= n and k >= 1


class TestBmsspMatchesScipy:
    @pytest.mark.parametrize("params", PARAMS, ids=str)
    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("directed", [False, True])
    @pytest.mark.parametrize("seed", range(4))
    def test_random_graphs(self, seed, directed, mode, params):
        n = 30 + 25 * seed
        ptr, idx, wt = random_graph(seed + 100, n, 3 * n, directed=directed, mode=mode)
        for s in (0, n - 1, (seed * 7) % n):
            assert_same(
                S.bmssp_sssp(n, ptr, idx, wt, s, **params), bellman_ford(n, ptr, idx, wt, s)
            )

    @pytest.mark.parametrize("mode", ["int", "float"])
    @pytest.mark.parametrize("density", [1, 8, 40])
    def test_sparse_to_dense(self, mode, density):
        n = 120
        ptr, idx, wt = random_graph(5, n, density * n, directed=False, mode=mode)
        assert_same(S.bmssp_sssp(n, ptr, idx, wt, 7), scipy_reference(n, ptr, idx, wt, 7))
        assert_same(S.bmssp_sssp(n, ptr, idx, wt, 7, k=1, t=1), scipy_reference(n, ptr, idx, wt, 7))

    def test_a_path_graph_with_deep_recursion(self):
        n = 200
        ptr, idx, wt = S.csr_from_edges(n, range(n - 1), range(1, n), np.arange(1, n))
        d = S.bmssp_sssp(n, ptr, idx, wt, 0, k=1, t=1)
        assert d.tolist() == np.concatenate([[0], np.cumsum(np.arange(1, n))]).tolist()

    def test_a_star_with_equal_weights(self):  # every leaf ties with every other
        n = 60
        ptr, idx, wt = S.csr_from_edges(n, [0] * (n - 1), range(1, n), np.ones(n - 1))
        for params in PARAMS:
            d = S.bmssp_sssp(n, ptr, idx, wt, 0, **params)
            assert d[0] == 0 and (d[1:] == 1).all()
            assert (S.bmssp_sssp(n, ptr, idx, wt, 5, **params)[1:] == 2).sum() == n - 2

    def test_unreachable_vertices_are_inf(self):
        ptr, idx, wt = S.csr_from_edges(6, [0, 1, 4], [1, 2, 5], [1.0, 1.0, 1.0])
        d = S.bmssp_sssp(6, ptr, idx, wt, 0)
        assert d[:3].tolist() == [0, 1, 2] and np.isinf(d[3:]).all()

    def test_a_single_vertex_and_no_edges(self):
        ptr, idx, wt = S.csr_from_edges(1, [], [], [])
        assert S.bmssp_sssp(1, ptr, idx, wt, 0).tolist() == [0.0]
        ptr, idx, wt = S.csr_from_edges(5, [], [], [])
        d = S.bmssp_sssp(5, ptr, idx, wt, 2)
        assert d[2] == 0 and np.isinf(np.delete(d, 2)).all()

    def test_zero_weight_cycles(self):
        n = 8
        src = list(range(n))
        dst = [(i + 1) % n for i in range(n)]
        ptr, idx, wt = S.csr_from_edges(n, src, dst, np.zeros(n))
        for params in PARAMS:
            assert (S.bmssp_sssp(n, ptr, idx, wt, 3, **params) == 0).all()

    def test_the_stale_queue_entry_regression(self):
        """16 vertices, zero / one / two weights: vertex 6 was queued with a stale estimate, pulled
        and returned twice, |U| reached the top call's cap and vertex 13 was never settled."""
        ptr = [0, 3, 6, 11, 14, 16, 20, 22, 25, 26, 29, 35, 40, 44, 45, 50, 52]
        idx = [2, 9, 10, 11, 13, 14, 0, 7, 10, 11, 12, 10, 12, 14, 12, 14, 7, 8, 9, 11, 7, 10,
               2, 5, 6, 5, 0, 5, 14, 0, 2, 3, 6, 11, 15, 1, 2, 5, 10, 12, 2, 3, 4, 11, 1, 1, 3,
               4, 9, 15, 10, 14]  # fmt: skip
        wt = [0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 0.0, 0.0, 1.0, 1.0, 0.0, 1.0, 2.0, 0.0, 1.0, 0.0,
              0.0, 2.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 2.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0,
              0.0, 2.0, 0.0, 0.0, 1.0, 1.0, 2.0, 1.0, 0.0, 2.0, 1.0, 1.0, 1.0, 2.0, 0.0, 0.0,
              1.0, 0.0, 0.0, 0.0]  # fmt: skip
        want = bellman_ford(16, ptr, idx, wt, 2)
        assert want[13] == 2.0
        assert_same(S.bmssp_sssp(16, ptr, idx, wt, 2), want)

    def test_every_call_returns_each_vertex_once(self, monkeypatch):
        """The paper's Remark 3.8: the U_i of one call are disjoint, so a call's U has no
        duplicates; and everything it returns is complete (equals the true distance)."""
        calls = []
        original = S._Bmssp.run

        def checking(self, level, bound, sources):
            out = original(self, level, bound, sources)
            calls.append((self, out[1]))
            return out

        monkeypatch.setattr(S._Bmssp, "run", checking)
        for mode in MODES:
            for params in PARAMS:
                n = 90
                ptr, idx, wt = random_graph(9, n, 4 * n, directed=False, mode=mode)
                calls.clear()
                d = S.bmssp_sssp(n, ptr, idx, wt, 0, **params)
                assert calls
                for solver, done in calls:
                    assert len(done) == len(set(done))
                    assert (np.asarray(solver.dist)[done] >= d[done] - 1e-9).all()
                assert len(calls[-1][1]) <= n

    def test_stats_are_filled(self):
        n = 300
        ptr, idx, wt = random_graph(3, n, 4 * n, directed=False, mode="float")
        stats: dict = {}
        S.bmssp_sssp(n, ptr, idx, wt, 0, stats=stats)
        assert (stats["k"], stats["t"], stats["levels"], stats["n"]) == (
            *S.bmssp_parameters(n)[:2],
            S.bmssp_parameters(n)[2],
            n,
        )
        assert stats["calls"][stats["levels"]] == 1
        assert stats["base_cases"] == stats["calls"].get(0, 0) > 0
        assert stats["pivots"] <= stats["sources"]

    def test_bad_input_is_refused(self):
        ptr, idx, wt = S.csr_from_edges(3, [0], [1], [1.0])
        with pytest.raises(ValueError):
            S.bmssp_sssp(3, ptr, idx, wt, 0, k=0)
        with pytest.raises(ValueError):
            S.bmssp_sssp(3, ptr, idx, wt, -1)


class TestConstantDegree:
    def test_degrees_are_at_most_two_and_distances_survive(self):
        n = 70
        ptr, idx, wt = random_graph(11, n, 6 * n, directed=False, mode="int")
        cd = S.to_constant_degree(n, ptr, idx, wt)
        assert cd.n_original == n and len(cd.rep) == n
        out_deg = np.diff(np.asarray(cd.indptr))
        in_deg = np.bincount(np.asarray(cd.indices), minlength=cd.n)
        assert out_deg.max() <= 2 and in_deg.max() <= 2
        # about one node per (vertex, neighbour) pair: here the arc count
        assert cd.n == len(idx)
        for s in (0, 5, n - 1):
            want = scipy_reference(n, ptr, idx, wt, s)
            mat = S.scipy_matrix(cd.n, cd.indptr, cd.indices, cd.weights)
            assert_same(sp_dijkstra(mat, directed=True, indices=int(cd.rep[s]))[cd.rep], want)
            assert_same(cd.distances(s), want)

    def test_directed_and_isolated_vertices(self):
        ptr, idx, wt = S.csr_from_edges(5, [0, 1], [1, 2], [3.0, 4.0], undirected=False)
        cd = S.to_constant_degree(5, ptr, idx, wt)
        d = cd.distances(0)
        assert d[:3].tolist() == [0, 3, 7] and np.isinf(d[3:]).all()
        assert cd.n == (1 + 2 + 1) + 2  # vertices 0,1,2 have 1,2,1 neighbours; 3,4 keep a node

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("seed", range(3))
    def test_bmssp_with_the_transformation(self, seed, mode):
        n = 50
        ptr, idx, wt = random_graph(seed + 40, n, 3 * n, directed=bool(seed % 2), mode=mode)
        want = bellman_ford(n, ptr, idx, wt, 1)
        assert_same(S.bmssp_sssp(n, ptr, idx, wt, 1, constant_degree=True), want)
        assert_same(S.bmssp_sssp(n, ptr, idx, wt, 1, constant_degree=True, k=1, t=1), want)


# ---- the partial-sorting queue ------------------------------------------------------------------


def value(x: float, key: int) -> tuple:
    return (x, 1, key)


class TestPartialSortQueue:
    def test_pull_returns_the_m_smallest_and_a_separating_bound(self):
        q = S.PartialSortQueue(3, (float("inf"), 0, 0))
        for key, x in enumerate([9, 4, 7, 1, 8, 2, 6]):
            q.insert(key, value(x, key))
        keys, bound = q.pull()
        assert sorted(keys) == [1, 3, 5]  # values 4, 1, 2
        assert bound == value(6, 6)
        assert len(q) == 4
        keys, bound = q.pull()
        assert sorted(keys) == [2, 4, 6]  # values 7, 8, 6
        keys, bound = q.pull()
        assert keys == [0] and bound == (float("inf"), 0, 0)
        assert len(q) == 0

    def test_insert_keeps_the_smaller_value(self):
        q = S.PartialSortQueue(2, (99.0, 0, 0))
        q.insert(1, value(5, 1))
        q.insert(1, value(7, 1))  # ignored
        q.insert(1, value(3, 1))  # replaces
        assert len(q) == 1
        keys, bound = q.pull()
        assert keys == [1] and bound == (99.0, 0, 0)

    def test_batch_prepend_goes_in_front(self):
        q = S.PartialSortQueue(2, (99.0, 0, 0))
        for key, x in [(1, 10), (2, 11), (3, 12)]:
            q.insert(key, value(x, key))
        q.batch_prepend([(7, value(1, 7)), (8, value(2, 8)), (9, value(3, 9)), (7, value(5, 7))])
        keys, bound = q.pull()
        assert sorted(keys) == [7, 8] and bound == value(3, 9)  # the duplicate kept value 1
        keys, bound = q.pull()
        assert sorted(keys) == [1, 9] and bound == value(11, 2)
        keys, bound = q.pull()
        assert sorted(keys) == [2, 3] and bound == (99.0, 0, 0)

    def test_batch_prepend_replaces_a_stored_key_with_its_smaller_value(self):
        q = S.PartialSortQueue(4, (99.0, 0, 0))
        q.insert(1, value(10, 1))
        q.batch_prepend([(1, value(2, 1))])
        assert len(q) == 1
        assert q.pull() == ([1], (99.0, 0, 0))

    def test_discard(self):
        q = S.PartialSortQueue(2, (99.0, 0, 0))
        q.insert(1, value(1, 1))
        q.insert(2, value(2, 2))
        q.discard(1)
        q.discard(5)  # not stored: nothing happens
        assert len(q) == 1 and q.pull() == ([2], (99.0, 0, 0))

    @pytest.mark.parametrize("m", [1, 2, 3, 5, 16])
    @pytest.mark.parametrize("seed", range(8))
    def test_matches_a_brute_force_model(self, seed, m):
        """Random inserts, prepends (below everything stored), discards and pulls against a
        dict; every pull is the m smallest, with a bound between them and the rest."""
        rng = np.random.default_rng(seed)
        top = (1e9, 0, 0)
        q = S.PartialSortQueue(m, top)
        model: dict[int, tuple] = {}
        low = 0.0  # prepended values count down from here, always below what is stored
        for step in range(250):
            op = rng.choice(["insert", "insert", "prepend", "discard", "pull"])
            if op == "insert":
                key = int(rng.integers(0, 40))
                v = value(float(rng.integers(1000, 100000)) + key / 100, key)
                q.insert(key, v)
                if key not in model or v < model[key]:
                    model[key] = v
            elif op == "prepend":
                size = int(rng.integers(1, 3 * m + 2))
                pairs = []
                for _ in range(size):
                    key = int(rng.integers(0, 40))
                    low -= 1.0
                    pairs.append((key, value(low, key)))
                q.batch_prepend(pairs)
                for key, v in pairs:
                    if key not in model or v < model[key]:
                        model[key] = v
            elif op == "discard":
                key = int(rng.integers(0, 40))
                q.discard(key)
                model.pop(key, None)
            else:
                keys, bound = q.pull()
                want = sorted(model, key=lambda k: model[k])[:m]
                assert sorted(keys) == sorted(want), f"step {step}"
                pulled = [model.pop(key) for key in keys]
                assert all(v < bound for v in pulled)
                if model:
                    assert bound == min(model.values())  # the smallest value left
                else:
                    assert bound == top
            assert len(q) == len(model)


# ---- Floyd-Warshall -----------------------------------------------------------------------------


class TestFloydWarshall:
    @pytest.mark.parametrize("parallel", [True, False])
    @pytest.mark.parametrize("mode", ["int", "float", "zero"])
    @pytest.mark.parametrize("directed", [False, True])
    def test_equals_scipy_dijkstra_from_every_source(self, directed, mode, parallel):
        n = 60
        ptr, idx, wt = random_graph(21, n, 2 * n, directed=directed, mode=mode)
        dense = S.dense_from_csr(n, ptr, idx, wt)
        before = dense.copy()
        got = S.floyd_warshall_numba(dense, parallel=parallel)
        assert np.array_equal(dense, before), "the input must not change"
        assert_same(got, np.vstack([bellman_ford(n, ptr, idx, wt, s) for s in range(n)]))
        if mode != "zero":
            assert_same(got, sp_dijkstra(S.scipy_matrix(n, ptr, idx, wt), directed=True))

    def test_a_path_and_unreachable_pairs(self):
        ptr, idx, wt = S.csr_from_edges(5, [0, 1, 3], [1, 2, 4], [1.0, 2.0, 4.0])
        got = S.floyd_warshall_numba(S.dense_from_csr(5, ptr, idx, wt))
        assert got[0, 2] == 3.0 and got[2, 0] == 3.0 and got[3, 4] == 4.0
        assert np.isinf(got[0, 3]) and (np.diag(got) == 0).all()

    def test_a_square_matrix_is_required(self):
        with pytest.raises(ValueError):
            S.floyd_warshall_numba(np.zeros((3, 4)))

    def test_the_warm_up_reports_the_compile_time(self):
        assert S.warm_up_floyd_warshall() >= 0.0
        assert S.warm_up_floyd_warshall() < 1.0  # compiled already
