"""Fakes for the history-graph tests (AE02): a Neo4j driver that keeps nodes and relationships
in Python dicts and answers exactly the query shapes `history_graph/append.py` and `build.py`
send (anything else fails the test loudly), and a Docker runner that records every command.

A plain module (no conftest in a test subfolder). Nothing here starts a process, opens a socket
or touches Docker.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from nflengine.history_graph import schema as S
from nflengine.history_graph.docker import Done

# ---- a safety net ----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_real_docker(monkeypatch):
    """No unit test may reach Docker or the real history graph (2026-10-19: the container was
    stopped from outside while tests ran; it wasn't a test, but the net stays). `subprocess.run`
    of a `docker ...` command and a real Bolt driver fail loudly; everything else passes through.
    Import this fixture into a test module (`from history_fakes import no_real_docker  # noqa:
    F401`) and it applies to every test there."""
    import subprocess

    real_run = subprocess.run

    def guarded(args, *a, **k):
        first = args[0] if isinstance(args, (list, tuple)) and args else args
        if isinstance(first, str) and first.strip().split(" ")[0].lower() in (
            "docker",
            "docker.exe",
        ):
            raise AssertionError(f"a test tried to run a docker command: {list(args)[:3]}")
        return real_run(args, *a, **k)

    def no_bolt(*_a, **_k):
        raise AssertionError("a test tried to open a real Neo4j driver")

    monkeypatch.setattr(subprocess, "run", guarded)
    monkeypatch.setattr("neo4j.GraphDatabase.driver", no_bolt)


# ---- Neo4j -----------------------------------------------------------------------------------


class FakeNeo4jError(RuntimeError):
    """A server error with a status code, as the driver's Neo4jError has."""

    def __init__(self, code: str = "Neo.TransientError.General.TestFailure"):
        super().__init__("a failure whose text must never reach a log")
        self.code = code


class _Rec:
    def __init__(self, data: dict[str, Any]):
        self._d = data

    def data(self) -> dict[str, Any]:
        return dict(self._d)

    def __getitem__(self, k: str) -> Any:
        return self._d[k]


class _Result:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def __iter__(self):
        return iter(_Rec(r) for r in self._rows)

    def single(self) -> _Rec:
        assert len(self._rows) == 1, f"expected one row, got {len(self._rows)}"
        return _Rec(self._rows[0])

    def consume(self) -> None:
        return None


class _Tx:
    def __init__(self, graph: FakeGraph):
        self._g = graph

    def run(self, query: str, **params: Any) -> _Result:
        return self._g._run(query, params)


class _Session:
    def __init__(self, graph: FakeGraph):
        self._g = graph

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def run(self, query: str, **params: Any) -> _Result:
        return self._g._run(query, params)

    def execute_write(self, fn: Callable[[_Tx], Any]) -> Any:
        return fn(_Tx(self._g))


def _norm(q: str) -> str:
    return re.sub(r"\s+", " ", q).strip()


class FakeGraph:
    """Nodes: `nodes[label][key] = props`. Relationships: `rels[type]` = list of
    `{"s": start key, "e": end key, "p": props}` (a list, so a duplicate can be injected)."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[Any, dict[str, Any]]] = {n: {} for n in S.NODES}
        # the bulk-imported types and the derived person links (AE03: `derive.py` syncs them)
        self.rels: dict[str, list[dict[str, Any]]] = {r: [] for r in (*S.RELATIONSHIPS, *S.DERIVED)}
        self.lossy_types: set[str] = set()  # CREATEs of these link types silently do nothing
        self.writes: list[tuple[str, int]] = []  # (kind, rows) per write transaction
        self.queries: list[str] = []
        self.seasons_asked: set[int] = set()  # every $season a query was given
        self.schema_statements: list[str] = []
        self.closed = False
        self.fail_after_writes: int | None = None  # raise on the write transaction after N
        self.fail_error: Exception = FakeNeo4jError()

    # ---- the driver surface ----
    def session(self, **_kw: Any) -> _Session:  # `notifications_min_severity=...` etc.
        return _Session(self)

    def close(self) -> None:
        self.closed = True

    def verify_connectivity(self) -> None:
        raise OSError("fake driver: no server (versions() treats this as unknown)")

    # ---- loading what a bulk import would have stored ----
    def load(self, frames) -> FakeGraph:
        """Store `frames` (an `export.HistoryFrames`) the way the bulk import does: every
        non-null column is a property, the key included."""
        for label, df in frames.nodes.items():
            key = S.key_of(label)
            for r in df.to_dicts():
                self.nodes[label][r[key]] = {k: v for k, v in r.items() if v is not None}
        for rtype, df in frames.rels.items():
            for r in df.to_dicts():
                props = {k: v for k, v in r.items() if k not in ("s", "e") and v is not None}
                self.rels[rtype].append({"s": r["s"], "e": r["e"], "p": props})
        return self

    def load_csv(self, out_dir: Path, written: dict[str, dict[str, Any]]) -> FakeGraph:
        """Store the CSVs the way `neo4j-admin database import` reads them: an empty field is
        no property, `:ID` columns link relationships to nodes (a named one is also stored as a
        text property), typed columns are parsed by their header."""
        ids: dict[str, dict[str, Any]] = {}

        def convert(v: str, t: str) -> Any:
            return {
                "long": int,
                "double": float,
                "boolean": lambda x: x.lower() == "true",
                "string": str,
                "long[]": lambda x: [int(i) for i in x.split(";")],
            }[t](v)

        def read(name: str) -> tuple[list[str], list[list[str]]]:
            path = out_dir / written[name]["file"]
            with path.open(encoding="utf-8", newline="") as f:
                rows = list(csv.reader(f))
            return rows[0], rows[1:]

        for name in (n for n, i in written.items() if i["kind"] == "nodes"):
            header, body = read(name)
            idmap = ids.setdefault(name, {})
            for r in body:
                props: dict[str, Any] = {}
                node_id = None
                for col, v in zip(header, r, strict=True):
                    if m := re.fullmatch(r"(\w*):ID\((\w+)\)", col):
                        node_id = v
                        if m.group(1):
                            props[m.group(1)] = v
                        continue
                    pname, ptype = col.split(":", 1)
                    if v != "":
                        props[pname] = convert(v, ptype)
                key = props[S.key_of(name)]
                idmap[node_id] = key
                self.nodes[name][key] = props
        for rtype in (n for n, i in written.items() if i["kind"] == "relationships"):
            spec = S.RELATIONSHIPS[rtype]
            header, body = read(rtype)
            for r in body:
                props = {}
                for col, v in zip(header[2:], r[2:], strict=True):
                    pname, ptype = col.split(":", 1)
                    if v != "":
                        props[pname] = convert(v, ptype)
                self.rels[rtype].append(
                    {"s": ids[spec.start][r[0]], "e": ids[spec.end][r[1]], "p": props}
                )
        return self

    # ---- inspection ----
    def counts(self) -> dict[str, int]:
        """Every label and every type, derived ones too (`build.graph_counts`' default names)."""
        return {
            **{label: len(nodes) for label, nodes in self.nodes.items()},
            **{rtype: len(rels) for rtype, rels in self.rels.items()},
        }

    def bulk_counts(self) -> dict[str, int]:
        """What a bulk import stores: the labels and `S.RELATIONSHIPS` (not the derived links)."""
        return {k: v for k, v in self.counts().items() if k not in S.DERIVED}

    def rel_multiset(self) -> Counter:
        return Counter(
            (t, r["s"], r["e"], repr(sorted(r["p"].items())))
            for t, rs in self.rels.items()
            for r in rs
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "nodes": {
                label: {k: dict(v) for k, v in nodes.items()} for label, nodes in self.nodes.items()
            },
            "rels": self.rel_multiset(),
        }

    def n_writes(self) -> int:
        return len(self.writes)

    # ---- the query shapes ----
    def _run(self, query: str, params: dict[str, Any]) -> _Result:
        q = _norm(query)
        self.queries.append(q)
        if "season" in params:
            self.seasons_asked.add(params["season"])
        if q.startswith(("CREATE CONSTRAINT", "CREATE INDEX")):
            self.schema_statements.append(q)
            return _Result([])
        if q.startswith("CALL db.awaitIndexes"):
            return _Result([])
        if m := re.fullmatch(
            r"MATCH \(n:(\w+)\) WHERE n\.season = \$season RETURN n\.(\w+) AS k, "
            r"properties\(n\) AS p",
            q,
        ):
            label, key = m.groups()
            return _Result(
                [
                    {"k": k, "p": dict(p)}
                    for k, p in self.nodes[label].items()
                    if p.get("season") == params["season"]
                ]
            )
        if m := re.fullmatch(
            r"UNWIND \$keys AS k MATCH \(n:(\w+) \{(\w+): k\}\) RETURN k, properties\(n\) AS p", q
        ):
            label, _key = m.groups()
            return _Result(
                [
                    {"k": k, "p": dict(self.nodes[label][k])}
                    for k in params["keys"]
                    if k in self.nodes[label]
                ]
            )
        if m := re.fullmatch(
            r"MATCH \(a:(\w+)\)-\[x:(\w+)\]->\(b:(\w+)\) WHERE ([abx])\.season = \$season "
            r"RETURN a\.(\w+) AS s, b\.(\w+) AS e, properties\(x\) AS p",
            q,
        ):
            a, rtype, b, which, _sk, _ek = m.groups()
            out = []
            for r in self.rels[rtype]:
                owner = {
                    "a": self.nodes[a].get(r["s"], {}),
                    "b": self.nodes[b].get(r["e"], {}),
                    "x": r["p"],
                }[which]
                if owner.get("season") == params["season"]:
                    out.append({"s": r["s"], "e": r["e"], "p": dict(r["p"])})
            return _Result(out)
        if m := re.fullmatch(r"MATCH \(n:(\w+)\) RETURN count\(n\) AS c", q):
            return _Result([{"c": len(self.nodes[m.group(1)])}])
        if m := re.fullmatch(r"MATCH \(\)-\[r:(\w+)\]->\(\) RETURN count\(r\) AS c", q):
            return _Result([{"c": len(self.rels[m.group(1)])}])
        if m := re.fullmatch(
            r"MATCH \(a:(\w+)\)-\[x:(\w+)\]->\(b:(\w+)\) RETURN a\.(\w+) AS s, b\.(\w+) AS e, "
            r"properties\(x\) AS p",
            q,
        ):  # `derive.have_index`: every link of a type with its properties
            a, rtype, b, _sk, _ek = m.groups()
            return _Result(
                [
                    {"s": r["s"], "e": r["e"], "p": dict(r["p"])}
                    for r in self.rels[rtype]
                    if r["s"] in self.nodes[a] and r["e"] in self.nodes[b]
                ]
            )
        return self._write(q, params)

    def _write(self, q: str, params: dict[str, Any]) -> _Result:
        rows = params["rows"]
        if self.fail_after_writes is not None and len(self.writes) >= self.fail_after_writes:
            raise self.fail_error
        if m := re.fullmatch(
            r"UNWIND \$rows AS k MATCH \(n:(\w+) \{(\w+): k\}\) DETACH DELETE n", q
        ):
            label = m.group(1)
            for k in rows:
                if k in self.nodes[label]:
                    del self.nodes[label][k]
                    for rtype, spec in {**S.RELATIONSHIPS, **S.DERIVED}.items():
                        if spec.start == label:
                            self.rels[rtype] = [r for r in self.rels[rtype] if r["s"] != k]
                        if spec.end == label:
                            self.rels[rtype] = [r for r in self.rels[rtype] if r["e"] != k]
            self.writes.append((f"delete {label}", len(rows)))
        elif m := re.fullmatch(
            r"UNWIND \$rows AS p MERGE \(n:(\w+) \{(\w+): p\.(\w+)\}\) SET n = p", q
        ):
            label, key, _ = m.groups()
            for p in rows:
                self.nodes[label][p[key]] = dict(p)  # MERGE then replace all properties
            self.writes.append((f"create {label}", len(rows)))
        elif m := re.fullmatch(
            r"UNWIND \$rows AS p MATCH \(n:(\w+) \{(\w+): p\.(\w+)\}\) SET n = p", q
        ):
            label, key, _ = m.groups()
            for p in rows:
                if p[key] in self.nodes[label]:
                    self.nodes[label][p[key]] = dict(p)
            self.writes.append((f"update {label}", len(rows)))
        elif m := re.fullmatch(
            r"UNWIND \$rows AS r MATCH \(a:(\w+) \{(\w+): r\.s\}\)-\[x:(\w+)\]->"
            r"\(b:(\w+) \{(\w+): r\.e\}\)( WHERE x\.season = r\.season)? DELETE x",
            q,
        ):
            a, _sk, rtype, b, _ek, by_season = m.groups()
            for r in rows:
                self.rels[rtype] = [
                    x
                    for x in self.rels[rtype]
                    if not (
                        x["s"] == r["s"]
                        and x["e"] == r["e"]
                        and (not by_season or x["p"].get("season") == r["season"])
                    )
                ]
            self.writes.append((f"unlink {rtype}", len(rows)))
        elif m := re.fullmatch(
            r"UNWIND \$rows AS r MATCH \(a:(\w+) \{(\w+): r\.s\}\) MATCH \(b:(\w+) \{(\w+): "
            r"r\.e\}\) CREATE \(a\)-\[x:(\w+)\]->\(b\) SET x = r\.p",
            q,
        ):
            a, _sk, b, _ek, rtype = m.groups()
            for r in rows:
                if rtype in self.lossy_types:
                    break
                if r["s"] in self.nodes[a] and r["e"] in self.nodes[b]:  # MATCH finds both
                    self.rels[rtype].append({"s": r["s"], "e": r["e"], "p": dict(r["p"])})
            self.writes.append((f"link {rtype}", len(rows)))
        else:
            raise AssertionError(f"the fake Neo4j doesn't know this query: {q}")
        return _Result([])


# ---- Docker ----------------------------------------------------------------------------------


class FakeDocker:
    """A `docker.Runner` that records every command and answers from a table.

    `answers` maps a predicate on the argument list to a `Done`; the first match wins, the
    default is exit code 0 with no output. `container` is what `docker ps` reports."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []
        self.answers: list[tuple[Callable[[list[str]], bool], Done]] = []
        self.container = "running"
        self.compose_up_seen = False

    def on(self, pred: Callable[[list[str]], bool], done: Done) -> FakeDocker:
        self.answers.append((pred, done))
        return self

    def __call__(self, args: Sequence[str], timeout: float) -> Done:
        a = list(args)
        self.calls.append(a)
        self.timeouts.append(timeout)
        for pred, done in self.answers:
            if pred(a):
                return done
        if a[:2] == ["docker", "ps"]:
            return Done(0, self.container + "\n" if self.container != "missing" else "")
        if a[:2] == ["docker", "compose"] and "up" in a:
            self.compose_up_seen = True
        if a[:2] == ["docker", "version"]:
            return Done(0, "27.0.0\n")
        return Done(0, "")

    # ---- queries over the recorded commands ----
    def where(self, *words: str) -> list[list[str]]:
        return [c for c in self.calls if all(w in c for w in words)]

    def first_index(self, pred: Callable[[list[str]], bool]) -> int:
        for i, c in enumerate(self.calls):
            if pred(c):
                return i
        raise AssertionError("no such command was run")


def is_import(c: list[str]) -> bool:
    return c[:2] == ["docker", "run"] and "neo4j-admin" in c


def is_install(c: list[str]) -> bool:
    return c[:2] == ["docker", "run"] and "sh" in c and "-c" in c


def is_compose(c: list[str]) -> bool:
    return c[:2] == ["docker", "compose"]


def _mount_host(spec: str) -> str:
    """`D:/x/neo4j-history/import:/import:ro` -> `D:/x/neo4j-history/import`."""
    if spec.endswith((":ro", ":rw")):
        spec = spec[:-3]
    return spec.rsplit(":", 1)[0]


def assert_commands_safe(calls: Sequence[Sequence[str]], paths, cfg) -> None:
    """The rules every history-graph docker command must obey (AE02, CLAUDE.md): it names the
    history service or the history graph's own throw-away containers / staging volume, never
    the weekly service, container, folders or ports; nothing prints a resolved config."""
    history_root = paths.neo4j_history.as_posix()
    weekly_root = (paths.root / "neo4j").as_posix()
    own_containers = {"nfl-neo4j-history-import", "nfl-neo4j-history-copy", cfg.container}
    for raw in calls:
        c = list(raw)
        text = " ".join(c)
        assert c[0] == "docker", c
        assert "inspect" not in c, c
        assert not {"prune", "kill", "down", "system"} & set(c), c
        assert "nfl-neo4j" not in c, f"the weekly container is named: {c}"
        assert "7474" not in text and "7687" not in text, c
        assert weekly_root not in text.replace(history_root, ""), f"the weekly folder: {c}"
        if c[1] == "compose":
            assert c[2:4] == ["--profile", cfg.profile], f"a compose call without the profile: {c}"
            assert c[-1] == cfg.service, f"a compose call that doesn't end at the service: {c}"
            assert "neo4j" not in c, c  # the weekly service
            assert c[4] in ("up", "stop"), c
            assert c[4] != "stop" or c[5:7] == ["--timeout", "120"], (
                f"stop without a grace period: {c}"
            )
            assert not {"config", "rm", "-v", "--volumes"} & set(c), c
        elif c[1] == "rm":
            assert c[2] == "-f" and len(c) == 4 and c[3] in own_containers, c
        elif c[1] == "volume":
            assert c[2] in ("rm", "create") and c[-1] == cfg.staging_volume, c
            assert c[2] != "rm" or c[3] == "-f", c
        elif c[1] == "run":
            mounts = [c[i + 1] for i, x in enumerate(c) if x == "-v"]
            assert mounts, c
            for m in mounts:
                host = _mount_host(m)
                assert host == cfg.staging_volume or host.startswith(history_root + "/"), (
                    f"a mount outside the history graph: {m}"
                )
            assert c[c.index("--name") + 1] in own_containers, c
            assert not {"-e", "--env", "--env-file"} & set(c), c
        elif c[1] in ("ps", "version"):
            pass
        else:
            raise AssertionError(f"an unexpected docker command: {c}")


def quiet(*_a: Any, **_k: Any) -> None:
    return None


def diff_graphs(a: FakeGraph, b: FakeGraph, ignore_unlinked: set[str] | None = None) -> dict:
    """What differs between two graphs: per label the keys only in a / only in b / with
    different properties, and the relationship multiset difference. `ignore_unlinked` names
    labels whose nodes may differ in *extra* unlinked nodes (shared nodes an append leaves)."""
    out: dict[str, Any] = {}
    for label in S.NODES:
        na, nb = a.nodes[label], b.nodes[label]
        only_a, only_b = sorted(set(na) - set(nb), key=str), sorted(set(nb) - set(na), key=str)
        changed = sorted((k for k in set(na) & set(nb) if na[k] != nb[k]), key=str)
        if ignore_unlinked and label in ignore_unlinked:
            only_a = []
        if only_a or only_b or changed:
            out[label] = {
                "only_a": only_a[:5],
                "only_b": only_b[:5],
                "changed": [(k, na[k], nb[k]) for k in changed[:3]],
            }
    ra, rb = a.rel_multiset(), b.rel_multiset()
    if ra != rb:
        out["rels"] = {"only_a": list((ra - rb).items())[:5], "only_b": list((rb - ra).items())[:5]}
    return out


def write_state(root: Path, name: str, doc: dict[str, Any]) -> Path:
    import json

    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p
