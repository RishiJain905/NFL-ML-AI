"""`derive.run_derive` end to end (AE03) on the fixture league with a fake Neo4j and a fake Docker
runner: the document, `derive.json` and `people.parquet`, the unfinished marker, the refusals,
the lock, W&B, and how `run_build` and `run_append` call it (`derive_after=True` / `derive=True`).

Nothing here reaches Docker, Neo4j, W&B, the network or the real data root (`no_real_docker`
fails any test that tries).

The graph for most tests is the bulk import of 2024 plus the 2026 append through week 2; what
that league holds is worked out in the comments next to the numbers (the 2024 pairs by
hand from `history_world`'s participation rows, the coaching links from `snaps_rows`).
"""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import polars as pl
import pytest
from history_fakes import (
    FakeDocker,
    FakeGraph,
    FakeNeo4jError,
    is_compose,
    no_real_docker,  # noqa: F401  (the safety net)
)
from history_world import install_world, load

from nflengine.history_graph import append as A
from nflengine.history_graph import build as B
from nflengine.history_graph import derive as D
from nflengine.history_graph import export as X
from nflengine.history_graph import schema as S
from nflengine.history_graph import state
from nflengine.history_graph.config import HistoryConfig
from nflengine.history_graph.docker import Done
from nflengine.ops.lock import lock_path, run_lock

CFG = HistoryConfig()
QUIET = lambda *_: None  # noqa: E731
COUNTS = {"TEAMMATE": 9, "CONNECTION": 3, "OPPONENT": 9, "COACHED": 8, "WORKED_WITH": 2}


def at(minute: int, second: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, 19, 12, minute, second, tzinfo=dt.UTC)


class Env(SimpleNamespace):
    def derive(self, **kw):
        kw.setdefault("use_wandb", False)
        kw.setdefault("log", QUIET)
        kw.setdefault("cfg", CFG)
        kw.setdefault("driver", self.graph)
        return D.run_derive(paths=self.paths, **kw)

    def append(self, **kw):
        kw.setdefault("use_wandb", False)
        kw.setdefault("log", QUIET)
        kw.setdefault("derive", False)
        return A.run_append(2026, paths=self.paths, cfg=CFG, driver=self.graph, **kw)

    def derive_doc(self):
        return json.loads(D.derive_path(self.paths).read_text(encoding="utf-8"))


def write_build(paths, build_id="B1"):
    doc = {"status": "ok", "build_id": build_id, "first_season": 2024, "through_season": 2024,
           "seasons": [2024]}  # fmt: skip
    p = state.build_path(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture
def bare(tmp_path, monkeypatch):
    """The league and the graph a bulk import of 2024 stores; no build.json yet."""
    w = install_world(tmp_path, monkeypatch)
    fr = load(w, seasons=(2024,))
    out = tmp_path / "bulk-csv"
    graph = FakeGraph().load_csv(out, X.write_csvs(fr, out))
    return Env(world=w, paths=w.paths, graph=graph)


@pytest.fixture
def env(bare):
    """... with build B1 on record and the 2026 season appended through week 2."""
    write_build(bare.paths)
    bare.append()
    return bare


@pytest.fixture
def stamps(monkeypatch):
    """Pin `build.utc_now` (the derive, the append and the build all take their stamps from it)."""
    seq: list[dt.datetime] = []

    def set_(*times):
        seq[:] = list(times)

    def now():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    set_(at(0))
    monkeypatch.setattr(B, "utc_now", now)
    return set_


class TestTheDocument:
    def test_a_first_run(self, env, stamps):
        stamps(at(1, 2), at(1, 30))
        doc = env.derive()
        assert doc["status"] == "ok" and doc["version"] == D.DERIVE_VERSION == 1
        assert doc["derive_id"] == "20261019T120102Z"
        assert doc["build_id"] == "B1"
        assert (doc["first_season"], doc["through_season"]) == (2024, 2024)
        assert (doc["season"], doc["through_week"]) == (2026, 2)  # the appended weeks too
        # what the league holds: TEAMMATE 9 = five offense pairs + four defense pairs of the 2024
        # participation rows; CONNECTION 3 = QB_KC to WR_KC, TE_KC and UNK_RECV; OPPONENT 9;
        # COACHED 8 = Reid (3), Pierce (2), Bieniemy (3); WORKED_WITH 2 = Reid + Bieniemy (2024),
        # Joe Defense + Pete Carroll (LV 2026)
        assert doc["counts"] == COUNTS
        assert doc["changes"] == {t: {"create": n} for t, n in COUNTS.items()}
        assert doc["check"] == {}
        assert doc["dropped"] == {"snaps_rows_unknown": 1}  # the player the graph doesn't hold
        assert set(doc["seconds"]) == {"inputs", "compute", "sync", "total"}
        assert doc["derived_at"].startswith("2026-")
        assert not doc.get("wandb_url")

    def test_the_graph_holds_the_links_and_they_are_right(self, env):
        env.derive()
        g = env.graph
        assert {t: len(g.rels[t]) for t in S.DERIVED} == COUNTS
        tm = {(r["s"], r["e"]): r["p"] for r in g.rels["TEAMMATE"]}
        # QB_KC and WR_KC were on offense together on 2024_01 plays 3 and 4
        assert tm[("00-0001", "00-0003")]["snaps"] == 2
        assert tm[("00-0001", "00-0003")]["stint_teams"] == ["KC"]
        cn = {(r["s"], r["e"]): r["p"] for r in g.rels["CONNECTION"]}
        # QB_KC -> WR_KC: targets 2024 plays 3, 4 and 6 (GB) + 2026 plays 2 and 4 = 5; caught 3
        # (2024) and 2 (2026) = 2; 12 + 11 yards; epa .5 - .8 - .5 + .4 - .7
        p = cn[("00-0001", "00-0003")]
        assert (p["targets"], p["catches"], p["yards"], p["epa"]) == (5, 2, 23, -1.1)
        assert p["seasons"] == [2024, 2026]  # the appended season counts
        co = {(r["s"], r["e"]): r["p"] for r in g.rels["COACHED"]}
        # Reid, KC's head coach, 2024 weeks 1-2: QB_KC played 55 + 50; 2026 weeks 1-2: 58 + 60
        assert co[("andy-reid", "00-0001")]["stint_snaps"] == [105, 118]
        # Bieniemy was KC's OC in 2024 only; he coaches the offense's snaps and not the 2026 ones
        assert co[("eric-bieniemy", "00-0001")]["snaps"] == 105
        ww = {(r["s"], r["e"]): r["p"] for r in g.rels["WORKED_WITH"]}
        assert ww[("andy-reid", "eric-bieniemy")]["snaps"] == 21  # KC's 2024 plays
        assert ww[("andy-reid", "eric-bieniemy")]["games"] == 2
        assert ww[("joe-defense", "pete-carroll")]["stint_roles"] == ["DC|HC"]

    def test_derive_json_is_the_document(self, env):
        doc = env.derive()
        on_disk = env.derive_doc()
        assert on_disk == doc and on_disk["counts"] == COUNTS
        assert D.read_derive(env.paths) == on_disk

    def test_the_people_index_is_written_beside_it(self, env):
        env.derive()
        people = pl.read_parquet(env.paths.neo4j_history / "people.parquet")
        assert people.columns == ["kind", "id", "name", "position", "team", "teams",
                                  "first_season", "last_season", "snaps", "links"]  # fmt: skip
        by_id = {r["id"]: r for r in people.to_dicts()}
        # the person links a person has: TEAMMATE, COACHED and WORKED_WITH (not CONNECTION /
        # OPPONENT). 00-0001: 3 teammates + 2 coaches; Reid: 3 + 1; a rookie with none: 0
        assert by_id["00-0001"]["links"] == 5 and by_id["andy-reid"]["links"] == 4
        assert by_id["00-0099"]["links"] == 3 and by_id["00-0013"]["links"] == 0
        assert by_id["ghost-coach"]["links"] == 0
        assert by_id["andy-reid"]["kind"] == "coach" and by_id["00-0001"]["kind"] == "player"
        assert by_id["00-0001"]["snaps"] == 55 + 50 + 58 + 60 and by_id["00-0001"]["team"] == "KC"
        assert not list(env.paths.neo4j_history.glob(".people*.tmp"))  # no temp file left

    def test_without_an_append_it_covers_the_build_only(self, bare):
        write_build(bare.paths)
        doc = bare.derive()
        assert (doc["season"], doc["through_week"]) == (None, None)
        assert doc["counts"]["TEAMMATE"] == 9  # participation is 2024: the append adds none
        # the 2026 plays aren't read: QB_KC -> WR_KC has only the three 2024 targets
        p = next(r["p"] for r in bare.graph.rels["CONNECTION"]
                 if (r["s"], r["e"]) == ("00-0001", "00-0003"))  # fmt: skip
        assert p["targets"] == 3 and p["seasons"] == [2024]

    def test_an_append_of_an_older_build_is_not_used(self, env):
        # an append.json made onto another build doesn't count (state.read_append)
        p = state.append_path(env.paths)
        doc = json.loads(p.read_text(encoding="utf-8"))
        p.write_text(json.dumps({**doc, "build_id": "OTHER"}), encoding="utf-8")
        assert env.derive()["season"] is None


class TestIdempotent:
    def test_a_rerun_writes_nothing_and_keeps_the_derive_id(self, env, stamps):
        stamps(at(1), at(2), at(3))
        first = env.derive()
        n = env.graph.n_writes()
        again = env.derive()
        assert env.graph.n_writes() == n  # nothing written
        assert again["changes"] == {} and again["check"] == {} and again["status"] == "ok"
        assert again["derive_id"] == first["derive_id"] == "20261019T120100Z"
        assert again["counts"] == first["counts"]
        assert D.read_derive(env.paths)["derive_id"] == first["derive_id"]

    def test_a_change_in_the_graph_gets_a_new_derive_id(self, env, stamps):
        stamps(at(1), at(5))
        first = env.derive()
        env.graph.rels["TEAMMATE"][0]["p"]["snaps"] += 100  # the graph drifted
        again = env.derive()
        assert again["changes"] == {"TEAMMATE": {"update": 1}}
        assert again["derive_id"] != first["derive_id"] and again["derive_id"].endswith("0500Z")

    def test_new_snap_counts_get_a_new_derive_id_and_only_the_difference_is_written(
        self, env, stamps
    ):
        stamps(at(1), at(6))
        first = env.derive()
        before = env.graph.n_writes()
        # QB_KC's week-1 snaps in 2024 go from 55 to 80 (a curated-data correction)
        env.world.snaps[3]["offense_snaps"] = 80
        env.world.write()
        again = env.derive()
        # only the two coaches of his offense (Reid, Bieniemy) count his snaps
        assert again["changes"] == {"COACHED": {"update": 2}}
        assert again["derive_id"] != first["derive_id"] and again["check"] == {}
        assert env.graph.n_writes() == before + 2  # one unlink batch, one link batch
        co = {(r["s"], r["e"]): r["p"] for r in env.graph.rels["COACHED"]}
        assert co[("andy-reid", "00-0001")]["stint_snaps"] == [130, 118]
        assert co[("eric-bieniemy", "00-0001")]["snaps"] == 130


class TestUnfinishedMarker:
    def seen_marker(self, env, monkeypatch):
        seen = []
        real = D.sync

        def spy(driver, derived, batch, log=print):
            seen.append(state.read_unfinished(env.paths))
            return real(driver, derived, batch, log)

        monkeypatch.setattr(D, "sync", spy)
        return seen

    def test_the_marker_is_written_before_the_graph_is_touched_and_cleared_after(
        self, env, monkeypatch
    ):
        seen = self.seen_marker(env, monkeypatch)
        assert state.read_unfinished(env.paths) is None
        env.derive()
        assert len(seen) == 1 and seen[0]["what"] == "derive" and seen[0]["build_id"] == "B1"
        assert state.read_unfinished(env.paths) is None and not state.history_busy(env.paths)

    def test_the_graph_is_busy_for_ask_while_the_marker_is_there(self, env, monkeypatch):
        busy = []
        real = D.sync

        def spy(driver, derived, batch, log=print):
            busy.append(state.history_busy(env.paths))
            busy.append(state.unfinished_next_to(state.lock_path(env.paths)))
            return real(driver, derived, batch, log)

        monkeypatch.setattr(D, "sync", spy)
        env.derive()
        assert busy == [True, True]  # Ask's guard reads `unfinished_next_to` (the lock's folder)

    def test_a_failed_check_keeps_the_marker_and_says_failed(self, env):
        env.graph.lossy_types = {"OPPONENT"}
        with pytest.raises(D.HistoryDeriveError) as e:
            env.derive()
        assert str(e.value) == "the derived links still differ after the sync: OPPONENT"
        marker = state.read_unfinished(env.paths)
        assert marker["what"] == "derive" and marker["build_id"] == "B1"
        assert state.history_busy(env.paths)
        doc = env.derive_doc()
        assert doc["status"] == "failed" and doc["check"] == {"OPPONENT": {"create": 9}}
        assert D.read_derive(env.paths) is None  # a failed derive is no derive

    def test_a_failed_check_leaves_the_people_index_alone(self, env):
        path = env.paths.neo4j_history / "people.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"sentinel": [1]}).write_parquet(path)
        env.graph.lossy_types = {"TEAMMATE"}
        with pytest.raises(D.HistoryDeriveError):
            env.derive()
        assert pl.read_parquet(path).columns == ["sentinel"]  # untouched

    def test_no_people_index_is_made_by_a_failed_first_derive(self, env):
        env.graph.lossy_types = {"TEAMMATE"}
        with pytest.raises(D.HistoryDeriveError):
            env.derive()
        assert not (env.paths.neo4j_history / "people.parquet").exists()

    def test_the_next_good_run_clears_the_marker_and_writes_the_index(self, env):
        env.graph.lossy_types = {"TEAMMATE"}
        with pytest.raises(D.HistoryDeriveError):
            env.derive()
        env.graph.lossy_types = set()
        doc = env.derive()  # a derive marker of the same build doesn't stop the next derive
        assert doc["status"] == "ok" and state.read_unfinished(env.paths) is None
        assert (env.paths.neo4j_history / "people.parquet").exists()
        assert doc["changes"] == {"TEAMMATE": {"create": 9}}  # only what was missing

    def test_a_crash_while_writing_leaves_the_marker(self, env):
        env.graph.fail_after_writes = env.graph.n_writes()  # the first derive write fails
        with pytest.raises(FakeNeo4jError):
            env.derive()
        assert state.read_unfinished(env.paths)["what"] == "derive"
        assert not (env.paths.neo4j_history / "people.parquet").exists()

    def test_the_advice_for_a_derive_marker(self, env):
        state.mark_unfinished(env.paths, "derive", "B1")
        assert state.unfinished_advice(state.read_unfinished(env.paths)) == (
            "the last derive didn't finish: run `uv run nfl history-graph derive`"
        )


class TestRefusals:
    def test_no_build_yet(self, bare):
        with pytest.raises(D.HistoryDeriveError, match="no history-graph build yet"):
            bare.derive()
        assert bare.graph.n_writes() == 0

    def test_a_build_that_isnt_ok_is_no_build(self, bare):
        write_build(bare.paths)
        p = state.build_path(bare.paths)
        p.write_text(json.dumps({**json.loads(p.read_text()), "status": "failed"}))
        with pytest.raises(D.HistoryDeriveError, match="no history-graph build yet"):
            bare.derive()

    def test_an_unfinished_build_comes_first(self, env):
        state.mark_unfinished(env.paths, "build", "B1")
        with pytest.raises(D.HistoryDeriveError) as e:
            env.derive()
        assert str(e.value) == "the last build didn't finish: run `uv run nfl history-graph build`"
        assert not D.derive_path(env.paths).exists()

    def test_an_unfinished_append_comes_first(self, env):
        state.mark_unfinished(env.paths, "append", "B1", 2026)
        with pytest.raises(D.HistoryDeriveError) as e:
            env.derive()
        assert "the 2026 append didn't finish" in str(e.value)
        assert state.read_unfinished(env.paths)["what"] == "append"  # still there

    def test_a_derive_marker_of_another_build_is_refused(self, env):
        state.mark_unfinished(env.paths, "derive", "OLD-BUILD")
        with pytest.raises(D.HistoryDeriveError, match="the last derive didn't finish"):
            env.derive()

    def test_an_unreadable_marker_is_refused_with_the_build_advice(self, env):
        state.unfinished_path(env.paths).write_text("not json", encoding="utf-8")
        with pytest.raises(D.HistoryDeriveError, match="the last build didn't finish"):
            env.derive()

    def test_the_weekly_lock_blocks_it(self, env):
        with (
            run_lock(lock_path(env.paths.runs), "nfl weekly run --auto"),
            pytest.raises(D.HistoryDeriveError, match="weekly lock"),
        ):
            env.derive()
        assert not state.history_busy(env.paths)  # and it took nothing

    def test_another_history_run_blocks_it(self, env):
        with (
            run_lock(state.lock_path(env.paths), "nfl history-graph append"),
            pytest.raises(D.HistoryDeriveError, match="another history-graph build, append or"),
        ):
            env.derive()

    def test_it_holds_the_history_lock_while_it_runs_and_releases_it(self, env, monkeypatch):
        held = []
        real = D.sync

        def spy(*a, **k):
            from nflengine.ops.lock import is_locked

            held.append(is_locked(state.lock_path(env.paths)))
            return real(*a, **k)

        monkeypatch.setattr(D, "sync", spy)
        env.derive()
        assert held == [True] and not state.history_busy(env.paths)

    def test_the_lock_is_released_after_an_error(self, env):
        env.graph.fail_after_writes = 0
        with pytest.raises(FakeNeo4jError):
            env.derive()
        from nflengine.ops.lock import is_locked

        assert not is_locked(state.lock_path(env.paths))

    def test_a_derive_marker_of_this_build_lets_a_locked_caller_through(self, env):
        # a build or an append calls it with `locked=True` (it holds the lock itself)
        state.mark_unfinished(env.paths, "derive", "B1")
        assert env.derive(locked=True)["status"] == "ok"


class TestStartAndDriver:
    def test_start_runs_under_the_lock_before_the_work(self, env, monkeypatch):
        from nflengine.ops.lock import is_locked

        seen = []
        monkeypatch.setattr(
            A,
            "_start_if_down",
            lambda cfg, log: seen.append(("start", cfg, is_locked(state.lock_path(env.paths)))),
        )
        env.derive(start=True)
        assert seen == [("start", CFG, True)]

    def test_no_start_unless_asked(self, env, monkeypatch):
        monkeypatch.setattr(A, "_start_if_down", lambda *a: pytest.fail("must not start"))
        env.derive()

    def test_a_refused_start_is_the_appends_error(self, env, monkeypatch):
        def refuse(cfg, log):
            raise A.HistoryAppendError("history graph not reachable: the Docker engine isn't on")

        monkeypatch.setattr(A, "_start_if_down", refuse)
        with pytest.raises(A.HistoryAppendError, match="Docker engine"):
            env.derive(start=True)
        assert not D.derive_path(env.paths).exists()

    def test_it_opens_and_closes_its_own_driver_when_none_is_given(self, env, monkeypatch):
        g = env.graph
        monkeypatch.setattr(
            "nflengine.history_graph.client.get_history_driver", lambda cfg=None, **k: g
        )
        env.derive(driver=None)
        assert g.closed

    def test_a_given_driver_is_left_open(self, env):
        env.derive()
        assert not env.graph.closed

    def test_the_driver_is_closed_when_the_sync_fails(self, env, monkeypatch):
        g = env.graph
        g.fail_after_writes = 0
        monkeypatch.setattr(
            "nflengine.history_graph.client.get_history_driver", lambda cfg=None, **k: g
        )
        with pytest.raises(FakeNeo4jError):
            env.derive(driver=None)
        assert g.closed


class FakeRun:
    def __init__(self, **kw):
        self.kw = kw
        self.logged: list[dict] = []
        self.summary_data: dict = {}
        self.summary = SimpleNamespace(update=lambda d: self.summary_data.update(d))
        self.url = "https://wandb.example/derive"
        self.exit_code = "unset"

    def log(self, d):
        self.logged.append(d)

    def finish(self, exit_code=None):
        self.exit_code = exit_code


class TestWandb:
    def patch(self, monkeypatch):
        runs: list[FakeRun] = []
        monkeypatch.setattr(
            "nflengine.tracking.init_run", lambda **kw: runs.append(FakeRun(**kw)) or runs[-1]
        )
        monkeypatch.setattr("nflengine.tracking.git_commit", lambda: "abc1234")
        return runs

    def test_a_good_derive_logs_its_counts_and_times(self, env, monkeypatch, stamps):
        stamps(at(7, 8))
        runs = self.patch(monkeypatch)
        doc = env.derive(use_wandb=True, launched_by="agent")
        (run,) = runs
        assert run.kw["job_type"] == "history-graph-derive" and run.kw["group"] == "history-graph"
        assert run.kw["launched_by"] == "agent"
        assert run.kw["name"] == "history-graph-derive-20261019T120708Z"
        assert set(run.kw["config"]) == {"build_id", "season", "through_week", "version",
                                         "git_commit"}  # fmt: skip
        assert run.kw["tags"] == ["history-graph"]
        assert {"time/inputs", "time/compute", "time/sync", "time/total"} <= {
            k for d in run.logged for k in d
        }
        s = run.summary_data
        assert s["status"] == "ok" and s["derive/count/TEAMMATE"] == 9 and s["derive/writes"] == 31
        assert s["dropped/snaps_rows_unknown"] == 1 and "derive/time/total" in s
        assert run.exit_code == 0 and doc["wandb_url"] == "https://wandb.example/derive"
        assert env.derive_doc()["wandb_url"] == "https://wandb.example/derive"

    def test_smoke_runs_go_to_the_smoke_group(self, env, monkeypatch):
        runs = self.patch(monkeypatch)
        env.derive(use_wandb=True, smoke=True)
        assert runs[0].kw["group"] == "history-graph-smoke" and "smoke" in runs[0].kw["tags"]

    def test_a_failed_derive_finishes_the_run_as_failed(self, env, monkeypatch):
        runs = self.patch(monkeypatch)
        env.graph.lossy_types = {"COACHED"}
        with pytest.raises(D.HistoryDeriveError):
            env.derive(use_wandb=True)
        (run,) = runs
        assert run.exit_code == 1 and run.summary_data["status"] == "failed"

    def test_a_broken_wandb_never_sinks_a_derive(self, env, monkeypatch):
        def boom(**kw):
            raise RuntimeError("wandb is down SECRET-LOG-TEXT")

        monkeypatch.setattr("nflengine.tracking.init_run", boom)
        monkeypatch.setattr("nflengine.tracking.git_commit", lambda: "x")
        lines: list[str] = []
        doc = env.derive(use_wandb=True, log=lines.append)
        assert doc["status"] == "ok" and not doc.get("wandb_url")
        assert any("W&B off for this derive (RuntimeError)" in x for x in lines)
        assert not any("SECRET-LOG-TEXT" in x for x in lines)
        assert D.read_derive(env.paths)["status"] == "ok"
        assert (env.paths.neo4j_history / "people.parquet").exists()

    def test_a_run_that_cannot_log_is_ignored(self, env, monkeypatch):
        runs = self.patch(monkeypatch)

        def bad_log(self_, d):
            raise RuntimeError("cannot log")

        monkeypatch.setattr(FakeRun, "log", bad_log)
        doc = env.derive(use_wandb=True)
        assert doc["status"] == "ok" and runs and not doc.get("wandb_url")


class TestDeriveAfter:
    def test_it_returns_the_summary(self, env):
        summary, err = D.derive_after(env.paths, CFG, env.graph, QUIET)
        assert err is None
        assert set(summary) == {"status", "derive_id", "counts", "changes", "seconds"}
        assert summary["counts"] == COUNTS and summary["status"] == "ok"

    def test_it_runs_without_taking_the_lock_or_checking_the_weekly_one(self, env):
        # the caller (a build or an append) holds the history lock and has checked the weekly one
        with (
            run_lock(state.lock_path(env.paths), "nfl history-graph build"),
            run_lock(lock_path(env.paths.runs), "nfl weekly run --auto"),
        ):
            summary, err = D.derive_after(env.paths, CFG, env.graph, QUIET)
        assert err is None and summary["status"] == "ok"

    def test_our_own_errors_keep_their_fixed_words(self, bare):
        lines: list[str] = []
        summary, err = D.derive_after(bare.paths, CFG, bare.graph, lines.append)  # no build.json
        assert summary is None and err.startswith("no history-graph build yet")
        assert any("the derive failed" in x and "nfl history-graph derive" in x for x in lines)

    def test_a_drivers_text_never_leaves(self, env, monkeypatch):
        def boom(**kw):
            raise FakeNeo4jError("Neo.ClientError.Security.Unauthorized")

        monkeypatch.setattr(D, "run_derive", boom)
        lines: list[str] = []
        summary, err = D.derive_after(env.paths, CFG, env.graph, lines.append)
        assert summary is None and err == "FakeNeo4jError: Neo.ClientError.Security.Unauthorized"
        assert not any("must never reach a log" in x for x in lines)

    def test_an_unexpected_error_is_its_type_only(self, env, monkeypatch):
        def boom(**kw):
            raise RuntimeError("password=hunter2 in the message")

        monkeypatch.setattr(D, "run_derive", boom)
        lines: list[str] = []
        summary, err = D.derive_after(env.paths, CFG, env.graph, lines.append)
        assert err == "RuntimeError" and not any("hunter2" in x for x in lines)

    def test_a_failed_check_is_reported_not_raised(self, env):
        env.graph.lossy_types = {"TEAMMATE"}
        summary, err = D.derive_after(env.paths, CFG, env.graph, QUIET)
        assert summary is None and "still differ after the sync: TEAMMATE" in err


class TestReadDerive:
    def doc(self, **over):
        return {"status": "ok", "build_id": "B1", "derive_id": "X", **over}

    def put(self, paths, doc):
        p = D.derive_path(paths)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc), encoding="utf-8")

    def test_none_before_the_first_derive(self, env):
        assert D.read_derive(env.paths) is None

    def test_the_last_ok_derive_onto_the_current_build(self, env):
        self.put(env.paths, self.doc())
        assert D.read_derive(env.paths)["derive_id"] == "X"

    def test_a_failed_derive_is_none(self, env):
        self.put(env.paths, self.doc(status="failed"))
        assert D.read_derive(env.paths) is None

    def test_a_derive_of_another_build_is_none(self, env):
        self.put(env.paths, self.doc(build_id="OLD"))
        assert D.read_derive(env.paths) is None

    def test_no_build_means_none(self, bare):
        self.put(bare.paths, self.doc())
        assert D.read_derive(bare.paths) is None

    def test_a_given_build_is_used(self, bare):
        self.put(bare.paths, self.doc())
        assert D.read_derive(bare.paths, {"build_id": "B1"}) is not None
        assert D.read_derive(bare.paths, {"build_id": "B2"}) is None

    def test_garbage_is_none(self, env):
        D.derive_path(env.paths).write_text("{not json", encoding="utf-8")
        assert D.read_derive(env.paths) is None


# ---- `run_append` and `run_build` call it -----------------------------------------------------


class TestAppendDerives:
    def test_an_append_derives_by_default_and_records_it(self, bare):
        write_build(bare.paths)
        doc = A.run_append(2026, paths=bare.paths, cfg=CFG, driver=bare.graph, use_wandb=False,
                           log=QUIET)  # fmt: skip
        assert doc["status"] == "ok" and "derive_error" not in doc
        assert set(doc["derive"]) == {"status", "derive_id", "counts", "changes", "seconds"}
        assert doc["derive"]["counts"] == COUNTS and doc["derive"]["status"] == "ok"
        assert "derive" in doc["seconds"] and doc["seconds"]["total"] >= doc["seconds"]["derive"]
        saved = json.loads(state.append_path(bare.paths).read_text(encoding="utf-8"))
        assert saved["derive"] == doc["derive"]  # append.json carries it
        assert env_derive_ok(bare)
        assert state.read_unfinished(bare.paths) is None
        # the derive read the append it came after: the 2026 targets are in
        assert any(r["p"]["seasons"] == [2024, 2026] for r in bare.graph.rels["CONNECTION"])

    def test_derive_false_leaves_the_person_links_alone(self, bare):
        write_build(bare.paths)
        doc = bare.append(derive=False)
        assert "derive" not in doc and "derive" not in doc["seconds"]
        assert not any(bare.graph.rels[t] for t in S.DERIVED)
        assert not D.derive_path(bare.paths).exists()

    def test_a_failed_derive_never_fails_the_append(self, bare, monkeypatch):
        write_build(bare.paths)

        def boom(**kw):
            raise RuntimeError("the driver said SECRET-LOG-TEXT")

        monkeypatch.setattr(D, "run_derive", boom)
        lines: list[str] = []
        doc = A.run_append(2026, paths=bare.paths, cfg=CFG, driver=bare.graph, use_wandb=False,
                           log=lines.append)  # fmt: skip
        assert doc["status"] == "ok" and doc["derive"] is None
        assert doc["derive_error"] == "RuntimeError"
        saved = json.loads(state.append_path(bare.paths).read_text(encoding="utf-8"))
        assert saved["status"] == "ok" and saved["derive_error"] == "RuntimeError"
        assert state.read_append(bare.paths)["status"] == "ok"
        assert any("the derive failed" in x for x in lines)
        assert not any("SECRET-LOG-TEXT" in x for x in lines)
        assert state.read_unfinished(bare.paths) is None  # the append itself finished

    def test_a_derive_that_cannot_check_is_reported_and_leaves_its_marker(self, bare):
        write_build(bare.paths)
        bare.graph.lossy_types = {"TEAMMATE"}
        doc = A.run_append(2026, paths=bare.paths, cfg=CFG, driver=bare.graph, use_wandb=False,
                           log=QUIET)  # fmt: skip
        assert doc["status"] == "ok" and "still differ after the sync" in doc["derive_error"]
        assert state.read_unfinished(bare.paths)["what"] == "derive"
        assert state.read_append(bare.paths) is not None

    def test_a_failed_append_check_skips_the_derive(self, bare, monkeypatch):
        write_build(bare.paths)
        monkeypatch.setattr(D, "run_derive", lambda **kw: pytest.fail("no derive after a bad sync"))
        bare.graph.lossy_types = {"DEFENSE"}  # the append's DEFENSE links silently don't stick
        with pytest.raises(A.HistoryAppendError, match="still differs"):
            bare.append(derive=True)
        assert state.read_unfinished(bare.paths)["what"] == "append"  # the append's own marker

    def test_an_append_proceeds_over_a_derive_marker_only_when_it_derives(self, bare):
        write_build(bare.paths)
        state.mark_unfinished(bare.paths, "derive", "B1")
        with pytest.raises(A.HistoryAppendError, match="the last derive didn't finish"):
            bare.append(derive=False)
        assert state.read_unfinished(bare.paths)["what"] == "derive"  # untouched
        doc = bare.append(derive=True)  # its own derive redoes what the marker says
        assert doc["status"] == "ok" and doc["derive"]["status"] == "ok"
        assert state.read_unfinished(bare.paths) is None

    @pytest.mark.parametrize("what", ["build", "append"])
    def test_an_append_refuses_a_builds_or_another_appends_marker_even_when_it_derives(
        self, bare, what
    ):
        write_build(bare.paths)
        state.mark_unfinished(bare.paths, what, "B1", 2025 if what == "append" else None)
        with pytest.raises(A.HistoryAppendError):
            bare.append(derive=True)

    def test_a_derive_marker_of_another_build_is_refused_by_the_append_too(self, bare):
        write_build(bare.paths)
        state.mark_unfinished(bare.paths, "derive", "OLD")
        with pytest.raises(A.HistoryAppendError, match="didn't finish"):
            bare.append(derive=True)

    def test_a_rerun_append_derive_writes_nothing(self, bare):
        write_build(bare.paths)
        first = bare.append(derive=True)
        n = bare.graph.n_writes()
        second = bare.append(derive=True)
        assert bare.graph.n_writes() == n
        assert second["derive"]["changes"] == {} and second["changes"] == {}
        assert second["derive"]["derive_id"] == first["derive"]["derive_id"]


class TestHalfWrittenLinksStayMarked:
    """Sol's AE03 review: half-written person links are never left without a marker, and a
    recovery never reuses an id that named something else."""

    def half_written(self, env):
        env.graph.fail_after_writes = env.graph.n_writes()  # the derive's first write fails
        with pytest.raises(FakeNeo4jError):
            env.derive()
        env.graph.fail_after_writes = None
        assert state.read_unfinished(env.paths)["what"] == "derive"

    def test_an_append_whose_derive_fails_before_it_writes_keeps_the_derive_marker(
        self, env, monkeypatch
    ):
        self.half_written(env)

        def boom(*a, **k):
            raise RuntimeError("the derive's inputs")

        monkeypatch.setattr(D, "derived_frames", boom)  # fails before the derive marks anything
        doc = env.append(derive=True)
        assert doc["status"] == "ok" and doc["derive_error"] == "RuntimeError"
        marker = state.read_unfinished(env.paths)
        assert marker["what"] == "derive" and marker["build_id"] == "B1"
        assert state.history_busy(env.paths)  # Ask still stays off the half-written links

    def test_the_append_carries_the_pending_derive_in_its_own_marker(self, env, monkeypatch):
        self.half_written(env)
        seen = []
        real = A.sync

        def spy(driver, frames, season, batch, log=print):
            seen.append(state.read_unfinished(env.paths))
            return real(driver, frames, season, batch, log)

        monkeypatch.setattr(A, "sync", spy)
        env.append(derive=True)
        assert seen[0]["what"] == "append" and seen[0]["derive_pending"] is True
        assert state.read_unfinished(env.paths) is None  # its derive finished

    def test_an_append_marker_that_carries_one_hands_it_back(self, env):
        # an append that crashed after starting over a half-written derive, re-run alone
        state.mark_unfinished(env.paths, "append", "B1", 2026, derive_pending=True)
        doc = env.append(derive=False)
        assert doc["status"] == "ok"
        assert state.read_unfinished(env.paths)["what"] == "derive"  # until a derive runs
        assert env.derive()["status"] == "ok" and state.read_unfinished(env.paths) is None

    def test_a_plain_append_marker_clears_as_before(self, env):
        state.mark_unfinished(env.paths, "append", "B1", 2026)
        env.append(derive=False)
        assert state.read_unfinished(env.paths) is None

    def test_a_recovery_that_writes_nothing_gets_a_new_derive_id(self, env, stamps, monkeypatch):
        stamps(at(1), at(7), at(9))
        first = env.derive()
        env.world.snaps[3]["offense_snaps"] = 80  # links change...
        env.world.write()

        real, dies = D._write_people, [True]

        def died(*a, **k):
            if dies[0]:
                raise RuntimeError("killed after the sync")
            return real(*a, **k)

        monkeypatch.setattr(D, "_write_people", died)  # ...and derive.json isn't replaced
        with pytest.raises(RuntimeError):
            env.derive()
        dies[0] = False
        assert D.read_derive(env.paths)["derive_id"] == first["derive_id"]  # still the old id
        again = env.derive()
        assert again["changes"] == {}  # the graph already holds the new links
        assert again["derive_id"] != first["derive_id"]  # but they aren't what that id named
        assert state.read_unfinished(env.paths) is None

    def test_a_rerun_after_a_good_derive_still_keeps_it(self, env, stamps):
        stamps(at(1), at(7))
        first = env.derive()
        assert env.derive()["derive_id"] == first["derive_id"]


def env_derive_ok(env) -> bool:
    d = D.read_derive(env.paths)
    return bool(d) and d["status"] == "ok" and (env.paths.neo4j_history / "people.parquet").exists()


def written_from_dir(d):
    out = {}
    for name in S.NODES:
        out[name] = {"file": f"nodes_{name}.csv", "kind": "nodes"}
    for name in S.RELATIONSHIPS:
        out[name] = {"file": f"rels_{name}.csv", "kind": "relationships"}
    return out


class BuildRig(SimpleNamespace):
    def run(self, **kw):
        kw.setdefault("use_wandb", False)
        kw.setdefault("log", self.logs.append)
        kw.setdefault("first_season", 2024)
        kw.setdefault("through_season", 2024)
        return B.run_build(
            paths=self.paths, cfg=CFG, runner=self.docker, driver_factory=self.factory, **kw
        )

    @property
    def import_dir(self):
        return self.paths.neo4j_history / "import"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from history_fakes import is_import

    w = install_world(tmp_path, monkeypatch)
    fd = FakeDocker().on(is_import, Done(0, "IMPORT DONE\n"))
    monkeypatch.setattr(
        "nflengine.history_graph.client.history_up",
        lambda cfg=None, timeout=5.0: fd.compose_up_seen,
    )
    monkeypatch.setattr(
        "nflengine.settings.get_config",
        lambda: SimpleNamespace(seasons=SimpleNamespace(current=2026), history_graph={}),
    )
    rig = BuildRig(world=w, paths=w.paths, docker=fd, logs=[], graphs=[])

    def factory():
        g = FakeGraph().load_csv(rig.import_dir, written_from_dir(rig.import_dir))
        rig.graphs.append(g)
        return g

    rig.factory = factory
    return rig


class TestBuildDerives:
    def test_a_build_derives_by_default_and_records_it(self, rig):
        doc = rig.run()
        assert doc["status"] == "ok" and "derive_error" not in doc
        assert doc["derive"]["status"] == "ok" and doc["derive"]["counts"] == COUNTS
        assert "derive" in doc["seconds"] and "append" in doc["seconds"]
        g = rig.graphs[0]
        assert {t: len(g.rels[t]) for t in S.DERIVED} == COUNTS
        assert doc["counts_after_append"] == g.counts()  # the derived counts are in it
        assert doc["counts_after_append"]["TEAMMATE"] == 9
        # the check at the import stage is of the bulk-imported types only
        assert set(doc["counts"]) == {*S.NODES, *S.RELATIONSHIPS}
        on_disk = json.loads(state.build_path(rig.paths).read_text(encoding="utf-8"))
        assert on_disk["derive"] == doc["derive"] and on_disk["status"] == "ok"
        assert D.read_derive(rig.paths)["build_id"] == doc["build_id"]
        assert state.read_unfinished(rig.paths) is None and not state.history_busy(rig.paths)

    def test_the_derive_covers_the_appended_season(self, rig):
        rig.run()
        d = D.read_derive(rig.paths)
        assert (d["season"], d["through_week"]) == (2026, 2)

    def test_without_an_append_it_covers_the_import_only(self, rig):
        doc = rig.run(append_current=False)
        assert "append" not in doc["seconds"] and doc["derive"]["status"] == "ok"
        d = D.read_derive(rig.paths)
        assert (d["season"], d["through_week"]) == (None, None)

    def test_derive_after_false_skips_it(self, rig):
        doc = rig.run(derive_after=False)
        assert "derive" not in doc and "derive" not in doc["seconds"]
        assert not any(rig.graphs[0].rels[t] for t in S.DERIVED)
        assert D.read_derive(rig.paths) is None

    def test_the_derive_runs_after_the_append_and_inside_the_build_lock(self, rig, monkeypatch):
        order = []
        real_append, real_derive = A.run_append, D.run_derive

        def spy_append(*a, **k):
            order.append(("append", k.get("derive"), k.get("locked")))
            return real_append(*a, **k)

        def spy_derive(**k):
            order.append(("derive", k.get("locked"), state.history_busy(rig.paths)))
            return real_derive(**k)

        monkeypatch.setattr(A, "run_append", spy_append)
        monkeypatch.setattr(D, "run_derive", spy_derive)
        rig.run()
        # the append inside a build doesn't derive on its own; the build derives once, after it
        assert order == [("append", False, True), ("derive", True, True)]

    def test_a_failed_derive_never_fails_the_build(self, rig, monkeypatch):
        def boom(**kw):
            raise RuntimeError("the driver said SECRET-LOG-TEXT")

        monkeypatch.setattr(D, "run_derive", boom)
        doc = rig.run()
        assert doc["status"] == "ok" and doc["derive"] is None
        assert doc["derive_error"] == "RuntimeError"
        assert state.read_build(rig.paths)["derive_error"] == "RuntimeError"
        assert any("the derive failed" in x and "nfl history-graph derive" in x for x in rig.logs)
        assert not any("SECRET-LOG-TEXT" in x for x in rig.logs)

    def test_a_failed_check_is_the_builds_derive_error(self, rig, monkeypatch):
        orig = rig.factory

        def factory():
            g = orig()
            g.lossy_types = {"WORKED_WITH"}
            return g

        rig.factory = factory
        doc = rig.run()
        assert doc["status"] == "ok" and doc["derive"] is None
        assert "still differ after the sync: WORKED_WITH" in doc["derive_error"]
        assert state.read_unfinished(rig.paths)["what"] == "derive"  # Ask waits for the next derive
        assert state.read_build(rig.paths) is not None

    def test_a_server_error_in_the_derive_stage_is_the_status_code_not_text(self, rig, monkeypatch):
        real = D.run_derive

        def failing(**k):  # the append's writes went through; the derive's first one fails
            k["driver"].fail_after_writes = k["driver"].n_writes()
            return real(**k)

        monkeypatch.setattr(D, "run_derive", failing)
        doc = rig.run()
        assert doc["status"] == "ok" and doc["derive"] is None
        assert doc["derive_error"] == "FakeNeo4jError: Neo.TransientError.General.TestFailure"
        assert not any("must never reach a log" in x for x in rig.logs)

    def test_the_build_still_runs_every_docker_command_under_its_own_lock(self, rig):
        from history_fakes import assert_commands_safe

        rig.run()
        assert_commands_safe(rig.docker.calls, rig.paths, CFG)  # the derive adds no command
        assert all(c[:2] == ["docker", "run"] or is_compose(c) or c[:2] in (
            ["docker", "ps"], ["docker", "rm"], ["docker", "volume"], ["docker", "version"])
            for c in rig.docker.calls)  # fmt: skip

    def test_the_wandb_run_carries_the_derive_metrics(self, rig, monkeypatch):
        runs: list[FakeRun] = []
        monkeypatch.setattr(
            "nflengine.tracking.init_run", lambda **kw: runs.append(FakeRun(**kw)) or runs[-1]
        )
        monkeypatch.setattr("nflengine.tracking.git_commit", lambda: "abc1234")
        rig.run(use_wandb=True)
        s = runs[0].summary_data
        assert s["derive/count/TEAMMATE"] == 9 and s["derive/writes"] == 31
        assert s["count/Play"] == 22 and "time/derive" in s
