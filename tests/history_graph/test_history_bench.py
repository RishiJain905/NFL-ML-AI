"""The path benchmark's harness (AE03, `history_graph/bench.py`): the samplers, the refusals, the
budget, the output files, the W&B run (and a W&B that fails), and the database side against a fake
Neo4j that answers exactly the queries the harness sends from an injected person graph.

No network, no Neo4j, no real W&B (`tests/conftest.py` sets WANDB_MODE=disabled).
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any

import numpy as np
import polars as pl
import pytest
from history_fakes import write_state
from scipy.sparse.csgraph import dijkstra as sp_dijkstra

from nflengine.history_graph import bench as B
from nflengine.history_graph import sssp as S
from nflengine.history_graph import state
from nflengine.paths import DataPaths
from nflengine.tracking import assert_no_secrets

BUILD = {"status": "ok", "build_id": "b1", "first_season": 2018, "through_season": 2025}
DERIVE = {"status": "ok", "build_id": "b1", "derive_id": "d1"}
LOG: list[str] = []


def quiet(msg: str) -> None:
    LOG.append(msg)


def N(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---- a small person graph and a fake Neo4j -------------------------------------------------------


def make_graph(seed: int = 3, n: int = 70, links: int = 260) -> B.PersonGraph:
    """A random undirected graph with `1 / snaps` costs (ties on purpose): 55 players, the rest
    coaches, element ids `4:fake:<100 + i>`."""
    rng = np.random.default_rng(seed)
    src = np.concatenate([np.arange(n - 1), rng.integers(0, n, links)])  # a path keeps it whole
    dst = np.concatenate([np.arange(1, n), rng.integers(0, n, links)])
    snaps = rng.integers(1, 40, len(src))
    ptr, idx, wt = S.csr_from_edges(n, src, dst, 1.0 / snaps)
    keys = [f"player:00-{i:07d}" if i < 55 else f"coach:coach-{i:02d}" for i in range(n)]
    return B.PersonGraph(keys, [f"4:fake:{100 + i}" for i in range(n)], ptr, idx, wt)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return [dict(r) for r in self._rows]


class _Session:
    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def run(self, query, **params):
        return _Result(self.fake.answer(re.sub(r"\s+", " ", query).strip(), params))


class FakeNeo4j:
    """Answers the harness's queries from `graph` (the whole person graph): GDS projections live
    in `catalog`; every query text is kept in `queries`."""

    def __init__(self, graph: B.PersonGraph, procedures: list[str] | None = None):
        self.g = graph
        self.catalog: dict[str, dict[str, Any]] = {}
        self.queries: list[str] = []
        self.dropped: list[str] = []
        self.created: list[str] = []
        self.skew = 0.0  # added to every distance the delta-stepping stream returns
        self.apsp_sum_error = 0.0
        self.fail_on: str | None = None  # a query fragment that raises
        self.procedures = (
            [
                "gds.allShortestPaths.delta.stream",
                "gds.allShortestPaths.delta.stats",
                "gds.allShortestPaths.stream",
                "gds.shortestPath.dijkstra.stream",
            ]
            if procedures is None
            else procedures
        )
        self.ident = {k.partition(":")[2]: i for i, k in enumerate(graph.keys)}
        self.snaps_by_pair: dict[tuple[str, str], int] = {}

    def session(self, **_kwargs):
        return _Session(self)

    def close(self):
        pass

    # -- projections --
    def _csr(self, name):
        proj = self.catalog[name]
        return proj["indptr"], proj["indices"], proj["weights"]

    def _dist(self, name, local_source):
        ptr, idx, wt = self._csr(name)
        mat = S.scipy_matrix(len(ptr) - 1, ptr, idx, wt)
        return sp_dijkstra(mat, directed=True, indices=local_source)

    def answer(self, q: str, p: dict[str, Any]) -> list[dict[str, Any]]:
        self.queries.append(q)
        if self.fail_on and self.fail_on in q:
            raise RuntimeError("boom: a message with a secret in it")
        g = self.g
        if q == N(B.Q_GDS_VERSION):
            return [{"gdsVersion": "2.13.13"}]
        if q == N(B.Q_COMPONENTS):
            return [{"name": "Neo4j Kernel", "versions": ["5.26.31"]}]
        if q == N(B.Q_PROCEDURES):
            return [{"name": n} for n in self.procedures]
        if q == N(B.Q_LIST):
            return [{"graphName": n} for n in self.catalog]
        if q == N(B.Q_DROP):
            self.dropped.append(p["g"])
            self.catalog.pop(p["g"], None)
            return [{"graphName": p["g"]}]
        if q in (N(B.Q_PROJECT_FULL), N(B.Q_PROJECT_SAMPLE)):
            name = p["g"]
            if "els" in p:
                pos = {e: i for i, e in enumerate(g.eids)}
                nodes = np.array([pos[e] for e in p["els"]])
            else:
                nodes = np.arange(g.n)
            ptr, idx, wt = B.induced_subgraph(g.n, g.indptr, g.indices, g.weights, nodes)
            self.catalog[name] = {"nodes": nodes, "indptr": ptr, "indices": idx, "weights": wt}
            self.created.append(name)
            return [{"nodes": len(nodes), "rels": len(idx)}]
        if "SHORTEST 1" in q:
            hops = sp_dijkstra(
                S.scipy_matrix(g.n, g.indptr, g.indices, g.weights),
                directed=True,
                indices=self.ident[p["a"]],
                unweighted=True,
            )[self.ident[p["b"]]]
            return [{"hops": int(hops)}] if np.isfinite(hops) else []
        if "gds.shortestPath.dijkstra.stream" in q:
            proj = self.catalog[p["g"]]
            where = {int(v): i for i, v in enumerate(proj["nodes"])}
            d = self._dist(p["g"], where[self.ident[p["a"]]])[where[self.ident[p["b"]]]]
            return [{"c": float(d)}] if np.isfinite(d) else []
        if q == N(B.Q_DELTA_STREAM):
            proj = self.catalog[p["g"]]
            local = [i for i, e in enumerate(proj["nodes"]) if g.eids[e] == p["e"]][0]
            d = self._dist(p["g"], local)
            return [
                {"t": B.node_id(g.eids[proj["nodes"][i]]), "c": float(d[i]) + self.skew}
                for i in range(len(d))
                if np.isfinite(d[i])
            ]
        if q == N(B.Q_DELTA_STATS):
            return [{"ms": 1}]
        if q == N(B.Q_APSP):
            ptr, idx, wt = self._csr(p["g"])
            full = sp_dijkstra(S.scipy_matrix(len(ptr) - 1, ptr, idx, wt), directed=True)
            fin = np.isfinite(full)
            return [{"c": int(fin.sum()), "s": float(full[fin].sum()) + self.apsp_sum_error}]
        if q == N(B.Q_NODES):
            return [{"e": e, "k": k} for e, k in zip(g.eids, g.keys, strict=True)]
        if q == N(B.Q_RELS):
            src = np.repeat(np.arange(g.n), np.diff(g.indptr))
            return [
                {
                    "a": g.eids[a],
                    "b": g.eids[b],
                    "snaps": self.snaps_by_pair.get((g.keys[a], g.keys[b])),
                }
                for a, b in zip(src[src < g.indices], g.indices[src < g.indices], strict=True)
            ]
        raise AssertionError(f"the fake doesn't know this query: {q[:120]}")


# ---- fixtures -----------------------------------------------------------------------------------


@pytest.fixture
def paths(tmp_path) -> DataPaths:
    p = DataPaths(tmp_path)
    write_state(p.neo4j_history, "build.json", BUILD)
    write_state(p.neo4j_history, "derive.json", DERIVE)
    return p


@pytest.fixture
def graph() -> B.PersonGraph:
    return make_graph()


def run(paths, graph, **kw):
    args = dict(sizes=[20, 40, 10**9], pairs=3, sources=2, repeats=1, budget_s=300.0,
                use_wandb=False, smoke=True, log=quiet, paths=paths, graph=graph)  # fmt: skip
    args.update(kw)
    return B.run_bench(**args)


def rows_of(doc, algorithm, n=None):
    return [
        r for r in doc["table"] if r["algorithm"] == algorithm and (n is None or r["size_n"] == n)
    ]


# ---- samplers -----------------------------------------------------------------------------------


class TestSamplers:
    def test_snowball_has_the_size_is_connected_and_seeded(self, graph):
        ptr, idx = graph.indptr, graph.indices
        a = B.snowball(ptr, idx, 25, 7)
        assert len(a) == 25 and len(set(a.tolist())) == 25 and a[0] == 7
        sub = B.induced_subgraph(graph.n, ptr, idx, graph.weights, a)
        assert len(set(B.components(25, sub[0], sub[1]).tolist())) == 1
        assert np.array_equal(a, B.snowball(ptr, idx, 25, 7))

    def test_smaller_samples_are_prefixes_of_the_bigger_ones(self, graph):
        big = B.snowball(graph.indptr, graph.indices, 50, 3)
        assert np.array_equal(B.snowball(graph.indptr, graph.indices, 12, 3), big[:12])

    def test_a_small_component_gives_all_of_it(self):
        ptr, idx, wt = S.csr_from_edges(6, [0, 1, 4], [1, 2, 5], [1.0, 1.0, 1.0])
        assert sorted(B.snowball(ptr, idx, 5, 0).tolist()) == [0, 1, 2]
        assert B.snowball(ptr, idx, 5, 3).tolist() == [3]

    def test_induced_subgraph_keeps_exactly_the_inner_arcs(self, graph):
        nodes = np.array([5, 9, 2, 30, 31, 32, 40])
        ptr, idx, wt = B.induced_subgraph(
            graph.n, graph.indptr, graph.indices, graph.weights, nodes
        )
        full = S.dense_from_csr(graph.n, graph.indptr, graph.indices, graph.weights)
        sub = S.dense_from_csr(len(nodes), ptr, idx, wt)
        assert np.array_equal(sub, full[np.ix_(nodes, nodes)])  # renumbered by position

    def test_largest_component(self):
        ptr, idx, wt = S.csr_from_edges(7, [0, 1, 3, 4, 5], [1, 2, 4, 5, 6], np.ones(5))
        assert B.largest_component(7, ptr, idx).tolist() == [3, 4, 5, 6]

    def test_pairs_are_seeded_and_distinct(self):
        pool = np.arange(10, 40)
        a = B.draw_pairs(np.random.default_rng(1), pool, 20)
        assert a == B.draw_pairs(np.random.default_rng(1), pool, 20)
        assert len(a) == 20 and all(x != y and x in pool and y in pool for x, y in a)
        assert B.draw_pairs(np.random.default_rng(1), np.array([4]), 5) == []

    def test_sizes(self):
        assert B.resolve_sizes(None, 5665) == [1000, 2000, 5000, None]
        assert B.resolve_sizes(None, 70) == [None]
        assert B.resolve_sizes([5000, 1000, 10**9, 7000], 5665) == [1000, 5000, None]
        assert B.resolve_sizes([20, 40], 70) == [20, 40]

    def test_same_distances_and_summaries(self):
        a = np.array([0.0, 1.5, np.inf])
        assert B.same_distances(a, a + 1e-10)
        assert not B.same_distances(a, a + 1e-6)
        assert not B.same_distances(a, np.array([0.0, 1.5, 9.0]))  # inf must match
        assert not B.same_distances(a, a[:2])
        assert not B.same_distances(np.array([np.nan]), np.array([1.0]))
        med, p90, low = B.summarize([1, 2, 3, 4, 100])
        assert (med, low) == (3.0, 1.0) and 4.0 < p90 < 100.0


# ---- refusals ----------------------------------------------------------------------------------


class TestRefusals:
    def test_it_refuses_while_a_build_is_unfinished(self, paths, graph):
        state.mark_unfinished(paths, "build", "b1")
        with pytest.raises(B.HistoryBenchError) as e:
            run(paths, graph)
        assert str(e.value) == B.BUSY_TEXT
        assert not B.lab_dir(paths).exists()

    def test_it_refuses_while_the_history_lock_is_held(self, paths, graph):
        from nflengine.ops.lock import run_lock

        with (
            run_lock(state.lock_path(paths), "nfl history-graph append"),
            pytest.raises(B.HistoryBenchError) as e,
        ):
            run(paths, graph)
        assert str(e.value) == B.BUSY_TEXT

    def test_it_refuses_while_the_weekly_lock_is_held(self, paths, graph, monkeypatch):
        from nflengine.ops.lock import lock_path, run_lock

        touched = []
        monkeypatch.setattr(B, "load_person_graph", lambda d: touched.append("neo4j"))
        monkeypatch.setattr(B, "_Db", lambda d: touched.append("neo4j"))
        fake = FakeNeo4j(graph)
        with (
            run_lock(lock_path(paths.runs), "nfl weekly run --auto"),
            pytest.raises(B.HistoryBenchError) as e,
        ):
            run(paths, None, driver=fake)
        assert str(e.value) == B.WEEKLY_TEXT
        assert str(e.value) == (
            "the weekly run (or the injury update / Update play calling) holds the weekly lock: "
            "run the benchmark after it"
        )
        assert not touched and not fake.queries  # refused before anything touched Neo4j
        assert not B.lab_dir(paths).exists()
        run(paths, graph)  # the lock is released: the benchmark runs

    def test_it_refuses_without_a_derive(self, paths, graph):
        (paths.neo4j_history / "derive.json").unlink()
        with pytest.raises(B.HistoryBenchError) as e:
            run(paths, graph)
        assert str(e.value) == B.NO_DERIVE_TEXT

    def test_it_refuses_a_derive_of_another_build(self, paths, graph):
        write_state(paths.neo4j_history, "derive.json", {**DERIVE, "build_id": "old"})
        with pytest.raises(B.HistoryBenchError, match="no derive"):
            run(paths, graph)


# ---- a run without a database -------------------------------------------------------------------


class TestRunWithoutDatabase:
    def test_the_files_the_table_and_the_checks(self, paths, graph):
        doc = run(paths, graph)
        out = B.lab_dir(paths)
        assert sorted(p.name for p in out.iterdir()) == [
            "path_benchmark.json",
            "path_benchmark.parquet",
            "path_benchmark.svg",
        ]  # no temp files left behind
        df = pl.read_parquet(out / "path_benchmark.parquet")
        assert df.columns == B.COLUMNS
        assert df.schema["agrees"] == pl.Boolean and df.schema["size_n"] == pl.Int64
        on_disk = json.loads((out / "path_benchmark.json").read_text(encoding="utf-8"))
        assert on_disk["derive_id"] == "d1" and on_disk["seed"] == 42
        assert [s["n"] for s in on_disk["sizes"]] == [20, 40, 70]
        assert on_disk["sizes"][-1]["label"] == "full" and on_disk["sizes"][-1]["m"] == graph.m
        assert on_disk["agrees_all"] is True and on_disk["failures"] == []
        assert on_disk["machine"]["python"] and on_disk["machine"]["cpu_count"]
        assert on_disk["machine"]["scipy"] and on_disk["machine"]["numba"]
        assert on_disk["jit_compile_s"] is not None
        assert on_disk["wandb_url"] is None and doc["table"] == on_disk["table"]
        assert {s["step"] for s in on_disk["setup"]} >= {"csr to python lists", "scipy csr matrix"}
        algorithms = {r["algorithm"] for r in doc["table"]}
        assert algorithms == {
            "scipy-dijkstra",
            "dijkstra-heap",
            "bmssp",
            "bmssp-constant-degree",
            "scipy-dijkstra-all",
            "floyd-warshall-numba",
            "scipy-floyd-warshall",
        }  # fmt: skip  (no pair rows, no gds rows without a database)
        for r in doc["table"]:
            assert r["runs"] >= 1 and r["median_ms"] > 0
            assert r["min_ms"] <= r["median_ms"] <= r["p90_ms"]
            assert r["agrees"] in (True, None)
        for n in (20, 40, 70):
            assert rows_of(doc, "dijkstra-heap", n)[0]["agrees"] is True
            assert rows_of(doc, "scipy-dijkstra", n)[0]["agrees"] is True
            assert rows_of(doc, "floyd-warshall-numba", n)[0]["agrees"] is True
            assert len(on_disk["times_ms"][f"source/bmssp/{n}"]) == 2  # the two sources
        svg = (out / "path_benchmark.svg").read_text(encoding="utf-8")
        ET.fromstring(svg)

    def test_a_seed_gives_the_same_samples_and_runs(self, paths, graph):
        a = run(paths, graph, seed=5)
        b = run(paths, graph, seed=5)
        assert a["sizes"] == b["sizes"]
        assert {k: len(v) for k, v in a["times_ms"].items()} == {
            k: len(v) for k, v in b["times_ms"].items()
        }
        assert [r["note"] for r in a["table"]] == [r["note"] for r in b["table"]]

    def test_the_budget_skips_an_algorithm_at_the_next_size_and_says_why(self, paths, graph):
        doc = run(paths, graph, budget_s=1e-9, sources=3, repeats=3)
        first = [r for r in doc["table"] if r["size_n"] == 20]
        assert all(r["runs"] == 1 for r in first)  # one run, then the budget says stop
        assert all("budget" in r["note"] for r in first if r["family"] == "source")
        later = [r for r in doc["table"] if r["size_n"] > 20]
        assert later and all(r["runs"] == 0 and r["median_ms"] is None for r in later)
        mid = [r for r in later if r["size_n"] == 40]
        assert all(r["note"].startswith("skipped: predicted") for r in mid)
        last = [r for r in later if r["size_n"] == 70]
        assert all(r["note"].startswith("skipped: skipped at a smaller size") for r in last)
        assert doc["agrees_all"] is True  # a skipped row isn't a disagreement
        assert all(r["agrees"] is None for r in later)

    def test_a_generous_budget_skips_nothing(self, paths, graph):
        full = run(paths, graph, budget_s=300.0)
        assert all(r["runs"] >= 1 and "skipped" not in r["note"] for r in full["table"])

    def test_a_disagreement_is_written_and_then_raised(self, paths, graph, monkeypatch):
        real = S.dijkstra_heap
        monkeypatch.setattr(S, "dijkstra_heap", lambda *a, **k: real(*a, **k) + 1e-3)
        with pytest.raises(B.HistoryBenchError, match="disagreed"):
            run(paths, graph)
        doc = json.loads((B.lab_dir(paths) / "path_benchmark.json").read_text(encoding="utf-8"))
        assert doc["agrees_all"] is False
        assert all(r["agrees"] is False for r in doc["table"] if r["algorithm"] == "dijkstra-heap")
        assert all(r["agrees"] is False for r in doc["table"] if r["algorithm"] == "scipy-dijkstra")
        assert all(r["agrees"] is True for r in doc["table"] if r["algorithm"] == "bmssp")


# ---- W&B ----------------------------------------------------------------------------------------


class FakeSummary:
    def __init__(self):
        self.data: dict[str, Any] = {}

    def update(self, d):
        self.data.update(d)


class FakeRun:
    url = "https://wandb.example/run/abc"

    def __init__(self, fail_log: bool = False):
        self.logged: list[dict[str, Any]] = []
        self.metrics: list[tuple] = []
        self.summary = FakeSummary()
        self.finished: list[int] = []
        self.fail_log = fail_log

    def define_metric(self, *a, **k):
        self.metrics.append((a, k))

    def log(self, data, **_):
        if self.fail_log:
            raise ConnectionError("W&B went away")
        self.logged.append(data)

    def finish(self, exit_code=0):
        self.finished.append(exit_code)


class TestWandb:
    def test_one_run_with_live_results_a_table_and_a_chart(self, paths, graph, monkeypatch):
        fake, calls = FakeRun(), []

        def init_run(**kw):
            calls.append(kw)
            assert_no_secrets(kw["config"])
            return fake

        monkeypatch.setattr("nflengine.tracking.init_run", init_run)
        doc = run(paths, graph, use_wandb=True, launched_by="agent")
        (kw,) = calls
        assert (kw["group"], kw["job_type"], kw["name"]) == (
            "history-graph-smoke",
            "path-benchmark",
            "path-benchmark",
        )
        assert set(kw["tags"]) == {"history-graph", "path-benchmark", "smoke"}
        assert kw["launched_by"] == "agent"
        assert kw["config"]["seed"] == 42 and kw["config"]["derive_id"] == "d1"
        assert doc["wandb_url"] == fake.url
        live = [d for d in fake.logged if "size_n" in d]
        assert len(live) == len(doc["table"])  # one live log per finished (algorithm, size)
        assert any("median_ms/source/scipy-dijkstra" in d for d in live)
        final = {k for d in fake.logged for k in d}
        assert {"results", "chart_log_log_svg"} <= final
        assert fake.finished == [0]
        assert fake.summary.data["agrees_all"] is True

    def test_the_real_run_is_not_tagged_smoke(self, paths, graph, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "nflengine.tracking.init_run", lambda **kw: calls.append(kw) or FakeRun()
        )
        run(paths, graph, use_wandb=True, smoke=False)
        assert calls[0]["group"] == "history-graph" and "smoke" not in calls[0]["tags"]

    def test_a_wandb_that_cannot_start_is_ignored(self, paths, graph, monkeypatch):
        def boom(**kw):
            raise RuntimeError("no network")

        monkeypatch.setattr("nflengine.tracking.init_run", boom)
        LOG.clear()
        doc = run(paths, graph, use_wandb=True)
        assert doc["wandb_url"] is None and doc["agrees_all"] is True
        assert (B.lab_dir(paths) / "path_benchmark.parquet").exists()
        assert any("W&B off" in m for m in LOG)

    def test_a_wandb_that_fails_midway_is_ignored(self, paths, graph, monkeypatch):
        fake = FakeRun(fail_log=True)
        monkeypatch.setattr("nflengine.tracking.init_run", lambda **kw: fake)
        doc = run(paths, graph, use_wandb=True)
        assert doc["agrees_all"] is True and (B.lab_dir(paths) / "path_benchmark.json").exists()
        assert fake.finished == [0]

    def test_a_crash_closes_the_run_as_failed(self, paths, graph, monkeypatch):
        fake = FakeRun()
        monkeypatch.setattr("nflengine.tracking.init_run", lambda **kw: fake)
        monkeypatch.setattr(B, "_run_size", lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
        with pytest.raises(KeyError):
            run(paths, graph, use_wandb=True)
        assert fake.finished == [1]


# ---- the database side, against a fake ---------------------------------------------------------


class TestDatabaseSide:
    def test_the_person_graph_loaded_from_neo4j(self, graph):
        fake = FakeNeo4j(graph)
        fake.snaps_by_pair = {}  # None snaps: the cost falls back to 1 (the projection's rule)
        loaded = B.load_person_graph(fake)
        assert loaded.keys == graph.keys and loaded.eids == graph.eids
        assert loaded.m == graph.m and np.array_equal(loaded.indices, graph.indices)
        assert np.allclose(loaded.weights, 1.0)

    def test_cost_is_one_over_snaps(self, graph):
        fake = FakeNeo4j(graph)
        a, b = graph.keys[0], graph.keys[1]
        fake.snaps_by_pair = {(a, b): 4, (b, a): 4}
        loaded = B.load_person_graph(fake)
        ia = list(loaded.indices[loaded.indptr[0] : loaded.indptr[1]]).index(1)
        assert loaded.weights[loaded.indptr[0] + ia] == 0.25

    def test_every_algorithm_runs_and_agrees(self, paths, graph):
        fake = FakeNeo4j(graph)
        doc = run(paths, graph, driver=fake, pairs=4, sources=3, repeats=2)
        assert doc["agrees_all"] is True and doc["failures"] == []
        assert doc["machine"]["gds"] == "2.13.13" and doc["machine"]["neo4j"] == "5.26.31"
        names = {(r["family"], r["algorithm"]) for r in doc["table"]}
        assert ("pair", "cypher-shortest") in names and ("pair", "gds-dijkstra-pair") in names
        for alg in ("gds-delta-stream", "gds-delta-stats"):
            assert ("source", alg) in names
        assert ("all", "gds-all-pairs") in names
        # Cypher SHORTEST runs on the whole graph only
        assert [r["size_n"] for r in doc["table"] if r["algorithm"] == "cypher-shortest"] == [70]
        for r in doc["table"]:
            assert r["runs"] >= 1
            if r["algorithm"] == "gds-delta-stats":
                assert r["agrees"] is None and "not checkable" in r["note"]
            else:
                assert r["agrees"] is True, r
        assert [r["runs"] for r in doc["table"] if r["algorithm"] == "gds-all-pairs"] == [2, 2, 2]

    def test_projections_are_named_bench_and_all_dropped(self, paths, graph):
        fake = FakeNeo4j(graph)
        fake.catalog["persons-d1"] = {"nodes": np.arange(2)}  # the app's, must survive
        fake.catalog["bench-old-d0"] = {"nodes": np.arange(2)}  # a killed run's, goes
        run(paths, graph, driver=fake)
        assert fake.created == ["bench-20-d1", "bench-40-d1", "bench-70-d1"]
        assert set(fake.dropped) >= set(fake.created) | {"bench-old-d0"}
        assert all(n.startswith("bench-") for n in fake.dropped)
        assert list(fake.catalog) == ["persons-d1"]

    def test_nothing_is_written_to_the_database(self, paths, graph):
        fake = FakeNeo4j(graph)
        run(paths, graph, driver=fake)
        write = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DETACH|LOAD|FOREACH)\b")
        assert fake.queries and not [q for q in fake.queries if write.search(q)]
        assert not [q for q in fake.queries if "gds.graph.write" in q or ".write(" in q]

    def test_only_bench_projections_can_be_projected_or_dropped(self, graph):
        db = B._Db(FakeNeo4j(graph))
        with pytest.raises(B.HistoryBenchError):
            db.drop("persons-d1")
        with pytest.raises(B.HistoryBenchError):
            db.project("persons-d1", None)

    def test_a_wrong_distance_from_the_database_is_a_disagreement(self, paths, graph):
        fake = FakeNeo4j(graph)
        fake.skew = 1e-6
        with pytest.raises(B.HistoryBenchError, match="disagreed"):
            run(paths, graph, driver=fake)
        doc = json.loads((B.lab_dir(paths) / "path_benchmark.json").read_text(encoding="utf-8"))
        bad = {(r["algorithm"], r["size_n"]) for r in doc["table"] if r["agrees"] is False}
        assert bad == {("gds-delta-stream", 20), ("gds-delta-stream", 40), ("gds-delta-stream", 70)}

    def test_a_wrong_all_pairs_total_is_a_disagreement(self, paths, graph):
        fake = FakeNeo4j(graph)
        fake.apsp_sum_error = 0.5
        with pytest.raises(B.HistoryBenchError, match="disagreed"):
            run(paths, graph, driver=fake)

    def test_a_failing_gds_call_is_recorded_without_the_driver_text(self, paths, graph):
        fake = FakeNeo4j(graph)
        fake.fail_on = "gds.allShortestPaths.stream"
        with pytest.raises(B.HistoryBenchError, match="failed"):
            run(paths, graph, driver=fake)
        out = B.lab_dir(paths)
        doc = json.loads((out / "path_benchmark.json").read_text(encoding="utf-8"))
        rows = [r for r in doc["table"] if r["algorithm"] == "gds-all-pairs"]
        assert rows and all(r["runs"] == 0 and "failed" in r["note"] for r in rows)
        assert "secret" not in json.dumps(doc)  # only the exception's type reaches the files
        others = [r for r in doc["table"] if r["algorithm"] != "gds-all-pairs"]
        assert all(r["runs"] >= 1 for r in others)  # everything else still ran
        assert not any(p.name.startswith(".") for p in out.iterdir())

    def test_no_all_pairs_procedure_is_said_so(self, paths, graph):
        fake = FakeNeo4j(graph, procedures=["gds.allShortestPaths.delta.stream",
                                            "gds.allShortestPaths.delta.stats",
                                            "gds.shortestPath.dijkstra.stream"])  # fmt: skip
        doc = run(paths, graph, driver=fake)
        rows = [r for r in doc["table"] if r["algorithm"] == "gds-all-pairs"]
        assert rows and all(
            r["runs"] == 0 and "no gds.allShortestPaths.stream" in r["note"] for r in rows
        )
        assert doc["agrees_all"] is True

    def test_an_unreachable_pair_is_never_drawn(self, paths):
        # two islands: pairs and sources come from the biggest component only
        src = [0, 1, 2, 3, 5]
        dst = [1, 2, 3, 4, 6]
        ptr, idx, wt = S.csr_from_edges(8, src, dst, np.ones(5))
        keys = [f"player:00-{i:07d}" for i in range(8)]
        g = B.PersonGraph(keys, [f"4:fake:{i}" for i in range(8)], ptr, idx, wt)
        doc = run(paths, g, driver=FakeNeo4j(g), sizes=[10**9], pairs=6, sources=3)
        assert doc["agrees_all"] is True
        assert all(r["runs"] >= 1 for r in doc["table"] if r["family"] == "pair")


# ---- the chart ---------------------------------------------------------------------------------


def row(alg, fam, n, ms):
    return {"algorithm": alg, "family": fam, "size_n": n, "size_m": 10 * n, "runs": 1,
            "median_ms": ms, "p90_ms": ms, "min_ms": ms, "agrees": True, "note": ""}  # fmt: skip


class TestChart:
    ROWS = [
        row("cypher-shortest", "pair", 5665, 4.0),
        row("scipy-dijkstra", "source", 1000, 0.3),
        row("scipy-dijkstra", "source", 5665, 2.5),
        row("bmssp", "source", 1000, 19.0),
        row("bmssp", "source", 5665, 155.0),
        row("floyd-warshall-numba", "all", 1000, 53.0),
        row("floyd-warshall-numba", "all", 5665, 50000.0),
        {**row("scipy-floyd-warshall", "all", 5665, 0.0), "runs": 0, "median_ms": None},
    ]

    def test_the_svg_is_a_log_log_chart_of_the_rows_that_ran(self):
        svg = B.chart_svg(self.ROWS)
        root = ET.fromstring(svg)
        text = " ".join(t.text or "" for t in root.iter("{http://www.w3.org/2000/svg}text"))
        for word in ("single pair", "single source", "all pairs", "bmssp", "floyd-warshall-numba"):
            assert word in text
        assert "scipy-floyd-warshall" not in text  # skipped: not drawn
        assert len(list(root.iter("{http://www.w3.org/2000/svg}polyline"))) == 3
        assert "5,665" in text and "1,000" in text
        assert "10000" in text or "1e+04" in text  # the time axis spans four orders of magnitude

    def test_the_log_scale_is_log(self):
        svg = B.chart_svg([row("a", "source", 1000, 1.0), row("a", "source", 10000, 100.0),
                           row("b", "source", 1000, 10.0)])  # fmt: skip
        root = ET.fromstring(svg)
        ys = [float(c.get("cy")) for c in root.iter("{http://www.w3.org/2000/svg}circle")]
        assert len(ys) == 3
        # 1 -> 10 -> 100 on a log axis are equally spaced
        low, mid, high = sorted(ys, reverse=True)
        assert low - mid == pytest.approx(mid - high, abs=0.2)

    def test_no_rows_is_still_valid_svg(self):
        assert "no results" in B.chart_svg([])
        ET.fromstring(B.chart_svg([]))

    def test_the_png_needs_matplotlib(self):
        pytest.importorskip("matplotlib")
        png = B.chart_png(self.ROWS)
        assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_summary_text(self, paths, graph):
        doc = run(paths, graph, sizes=[20])
        text = B.summary_text(doc)
        assert "bmssp" in text and "median ms" in text


# ---- an integration check on the real graph ----------------------------------------------------


@pytest.mark.integration
def test_the_real_person_graph_is_loaded_and_agrees_with_gds():
    """Loads the real person graph (read-only) and checks its size against the derive's counts."""
    from nflengine.history_graph.client import get_history_driver, history_up
    from nflengine.history_graph.derive import read_derive
    from nflengine.paths import ensure_data_root

    if not history_up():
        pytest.skip("the history graph isn't up on Bolt 7688")
    derive = read_derive(ensure_data_root(create_dirs=False))
    if not derive:
        pytest.skip("no derive yet")
    driver = get_history_driver()
    try:
        g = B.load_person_graph(driver)
    finally:
        driver.close()
    links = sum(derive["counts"][t] for t in ("TEAMMATE", "COACHED", "WORKED_WITH"))
    assert g.m <= 2 * links and g.m > links  # both arcs; a pair linked twice counts once
    assert g.n > 1000 and np.array_equal(np.diff(g.indptr) >= 0, np.ones(g.n, dtype=bool))
    assert g.keys == sorted(g.keys)
    assert all(re.match(r"^(player:00-\d{7}|coach:[a-z0-9-]+)$", k) for k in g.keys)


# ---- Sol's AE03 review: one at a time, a graph that held still, a failure fails the run -------


class TestReviewFixes:
    def test_a_failed_step_finishes_the_wandb_run_as_failed(self, paths, graph, monkeypatch):
        fake_run = FakeRun()
        monkeypatch.setattr("nflengine.tracking.init_run", lambda **kw: fake_run)
        db = FakeNeo4j(graph)
        db.fail_on = "gds.allShortestPaths.stream"
        with pytest.raises(B.HistoryBenchError, match="failed"):
            run(paths, graph, driver=db, use_wandb=True)
        assert fake_run.summary.data["agrees_all"] is True  # nothing disagreed...
        assert fake_run.summary.data["ok"] is False  # ...but a step failed
        assert fake_run.finished == [1]
        doc = json.loads((B.lab_dir(paths) / "path_benchmark.json").read_text(encoding="utf-8"))
        assert doc["ok"] is False and doc["agrees_all"] is True

    def test_a_clean_run_is_ok(self, paths, graph):
        doc = run(paths, graph)
        assert doc["ok"] is True and doc["agrees_all"] is True

    def test_it_refuses_while_a_lab_or_another_benchmark_runs(self, paths, graph):
        from nflengine.ops.lock import run_lock

        with (
            run_lock(state.lab_lock_path(paths), "nfl history-graph lab"),
            pytest.raises(B.HistoryBenchError, match="another lab or path benchmark"),
        ):
            run(paths, graph)
        assert not (B.lab_dir(paths) / "path_benchmark.json").exists()

    def test_the_history_lock_isnt_taken_so_ask_keeps_answering(self, paths, graph, monkeypatch):
        from nflengine.ops.lock import is_locked

        seen = []
        real = B._run_size

        def spy(*a, **k):
            seen.append((state.history_busy(paths), is_locked(state.lab_lock_path(paths))))
            return real(*a, **k)

        monkeypatch.setattr(B, "_run_size", spy)
        run(paths, graph)
        assert seen and all(s == (False, True) for s in seen)
        assert not is_locked(state.lab_lock_path(paths))

    @pytest.mark.parametrize("change", ["new_derive", "derive_running"])
    def test_a_derive_during_the_run_means_nothing_is_written(
        self, paths, graph, monkeypatch, change
    ):
        real = B._run_size

        def mid(*a, **k):
            if change == "new_derive":
                write_state(paths.neo4j_history, "derive.json", {**DERIVE, "derive_id": "d2"})
            else:
                state.mark_unfinished(paths, "derive", "b1")
            return real(*a, **k)

        monkeypatch.setattr(B, "_run_size", mid)
        fake_run = FakeRun()
        monkeypatch.setattr("nflengine.tracking.init_run", lambda **kw: fake_run)
        with pytest.raises(B.HistoryBenchError) as e:
            run(paths, graph, use_wandb=True)
        assert str(e.value) == B.CHANGED_TEXT
        assert not (B.lab_dir(paths) / "path_benchmark.json").exists()
        assert fake_run.finished == [1]  # aborted, not finished as a result
