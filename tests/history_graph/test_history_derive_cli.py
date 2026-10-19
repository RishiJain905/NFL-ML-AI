"""`nfl history-graph derive | lab`, the `--no-derive` options and the AE03 changes to `status` /
`start` (the CLI group's wiring). Each command stubs the outermost function it calls, so no test
reaches Docker, Neo4j, W&B or the data root; a stubbed `start` / `stop` that is called when it
shouldn't be fails the test."""

from __future__ import annotations

import pytest
from history_fakes import no_real_docker  # noqa: F401  (the safety net)
from typer.testing import CliRunner

from nflengine import cli
from nflengine.history_graph.derive import HistoryDeriveError
from nflengine.history_graph.docker import ServiceResult
from nflengine.history_graph.lab import HistoryLabError

runner = CliRunner()
GROUP = ["history-graph"]
DERIVE_DOC = {
    "status": "ok",
    "derive_id": "20261019T120100Z",
    "counts": {"TEAMMATE": 237000, "CONNECTION": 60000, "COACHED": 12000},
    "changes": {"TEAMMATE": {"create": 237000, "update": 5}, "COACHED": {"delete": 2}},
    "seconds": {"total": 95.0},
    "dropped": {"snaps_rows_unknown": 12},
    "wandb_url": "https://wandb.example/derive",
}


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch):
    monkeypatch.setattr(cli.console, "_width", 250)


@pytest.fixture(autouse=True)
def _tmp_data_root(tmp_path, monkeypatch):
    from nflengine.paths import DataPaths

    root = DataPaths(tmp_path / "data")
    monkeypatch.setattr("nflengine.paths.ensure_data_root", lambda *a, **k: root)
    return root


def invoke(*args):
    return runner.invoke(cli.app, [*GROUP, *args])


class TestDerive:
    def test_it_is_in_the_group_with_its_siblings(self):
        r = invoke("--help")
        for name in ("derive", "lab", "bench-paths"):
            assert name in r.output

    def test_it_runs_derive_and_prints_the_counts(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            "nflengine.history_graph.derive.run_derive",
            lambda **kw: seen.update(kw) or DERIVE_DOC,
        )
        r = invoke("derive", "--no-wandb", "--launched-by", "agent")
        assert r.exit_code == 0, r.output
        # it starts the container itself (under the lock), logs to the console, skips W&B
        assert seen["start"] is True and seen["use_wandb"] is False
        assert seen["launched_by"] == "agent" and seen["smoke"] is False
        assert "person links: TEAMMATE 237,000, CONNECTION 60,000, COACHED 12,000" in r.output
        assert "2 writes" not in r.output and "237,007 writes" in r.output  # 237000 + 5 + 2
        assert "(95 s)" in r.output
        assert "dropped: {'snaps_rows_unknown': 12}" in r.output
        assert "W&B: https://wandb.example/derive" in r.output

    def test_wandb_is_on_by_default_and_smoke_is_passed(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            "nflengine.history_graph.derive.run_derive",
            lambda **kw: seen.update(kw) or {**DERIVE_DOC, "dropped": {}, "wandb_url": None},
        )
        r = invoke("derive", "--smoke")
        assert r.exit_code == 0 and seen["use_wandb"] is True and seen["smoke"] is True
        assert "dropped" not in r.output and "W&B" not in r.output

    def test_a_refused_derive_exits_2_with_its_words(self, monkeypatch):
        def refuse(**kw):
            raise HistoryDeriveError("the last build didn't finish: run `uv run nfl x [y]`")

        monkeypatch.setattr("nflengine.history_graph.derive.run_derive", refuse)
        r = invoke("derive")
        assert r.exit_code == 2 and "the last build didn't finish" in r.output
        assert "[y]" in r.output  # markup in a message is shown, not eaten

    def test_it_never_starts_docker_itself(self, monkeypatch):
        monkeypatch.setattr("nflengine.history_graph.docker.start",
                            lambda *a, **k: pytest.fail("derive starts the container inside "
                                                        "run_derive, under the lock"))  # fmt: skip
        monkeypatch.setattr("nflengine.history_graph.derive.run_derive", lambda **kw: DERIVE_DOC)
        assert invoke("derive").exit_code == 0


class TestNoDerive:
    def test_build_derives_unless_told_not_to(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            "nflengine.history_graph.build.run_build",
            lambda *a, **kw: seen.append(kw) or {"counts": {"Play": 1}, "seconds": {}},
        )
        assert invoke("build").exit_code == 0
        assert invoke("build", "--no-derive").exit_code == 0
        assert [kw["derive_after"] for kw in seen] == [True, False]

    def test_append_derives_unless_told_not_to(self, monkeypatch):
        seen = []
        doc = {"season": 2026, "weeks": [1], "games": 1, "plays": 1, "changes": {},
               "seconds": {"total": 1.0}}  # fmt: skip
        monkeypatch.setattr(
            "nflengine.history_graph.append.run_append",
            lambda *a, **kw: seen.append(kw) or doc,
        )
        assert invoke("append").exit_code == 0
        assert invoke("append", "--no-derive").exit_code == 0
        assert [kw["derive"] for kw in seen] == [True, False]

    def test_append_prints_the_derive_it_made(self, monkeypatch):
        doc = {"season": 2026, "weeks": [1, 2], "games": 2, "plays": 8, "changes": {},
               "seconds": {"total": 3.0}, "derive": DERIVE_DOC}  # fmt: skip
        monkeypatch.setattr("nflengine.history_graph.append.run_append", lambda *a, **kw: doc)
        r = invoke("append")
        assert r.exit_code == 0
        assert "person links: TEAMMATE 237,000" in r.output and "237,007 writes" in r.output

    def test_append_reports_a_failed_derive_and_still_succeeds(self, monkeypatch):
        doc = {"season": 2026, "weeks": [1], "games": 1, "plays": 1, "changes": {},
               "seconds": {"total": 1.0}, "derive": None,
               "derive_error": "the derived links still differ after the sync: TEAMMATE",
               }  # fmt: skip
        monkeypatch.setattr("nflengine.history_graph.append.run_append", lambda *a, **kw: doc)
        r = invoke("append")
        assert r.exit_code == 0  # the append worked
        assert "derive failed: the derived links still differ after the sync: TEAMMATE" in r.output
        assert "run `nfl history-graph derive`" in r.output

    def test_a_derive_error_with_markup_is_shown_not_interpreted(self, monkeypatch):
        doc = {"season": 2026, "weeks": [1], "games": 1, "plays": 1, "changes": {},
               "seconds": {"total": 1.0}, "derive": None,
               "derive_error": "[red]boom[/red] [x]"}  # fmt: skip
        monkeypatch.setattr("nflengine.history_graph.append.run_append", lambda *a, **kw: doc)
        r = invoke("append")
        assert "[red]boom[/red] [x]" in r.output

    def test_the_counts_table_lists_the_derived_types(self):
        t = cli._history_counts_table("x", {"Play": 5, "TEAMMATE": 9, "WORKED_WITH": 2})
        rows = list(t.columns[0].cells)
        assert rows == ["Play", "TEAMMATE", "WORKED_WITH"]


class TestLab:
    def test_it_runs_the_named_jobs_with_the_seed(self, monkeypatch):
        seen = {}

        def fake(jobs, **kw):
            seen.update(jobs=jobs, **kw)
            return {
                "neighbours": {"players": 4300, "wandb_url": "https://wandb.example/n"},
                "families": {"communities": 300, "families": 120, "modularity": 0.93},
            }

        monkeypatch.setattr("nflengine.history_graph.lab.run_lab", fake)
        r = invoke("lab", "--job", "neighbours, families", "--seed", "7", "--no-wandb")
        assert r.exit_code == 0, r.output
        assert seen["jobs"] == ["neighbours", "families"] and seen["seed"] == 7
        assert seen["use_wandb"] is False
        assert "neighbours: players 4300; W&B https://wandb.example/n" in r.output
        assert "families: families 120, communities 300, modularity 0.93" in r.output

    def test_all_jobs_and_seed_42_by_default(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            "nflengine.history_graph.lab.run_lab",
            lambda jobs, **kw: seen.update(j=jobs, **kw) or {},
        )
        assert invoke("lab").exit_code == 0
        assert seen["j"] is None and seen["seed"] == 42 and seen["use_wandb"] is True

    def test_a_refusal_exits_2(self, monkeypatch):
        def refuse(jobs, **kw):
            raise HistoryLabError("no person links yet: run `nfl history-graph derive` first")

        monkeypatch.setattr("nflengine.history_graph.lab.run_lab", refuse)
        r = invoke("lab")
        assert r.exit_code == 2 and "no person links yet" in r.output

    def test_it_never_starts_or_stops_docker(self, monkeypatch):
        for name in ("start", "stop"):
            monkeypatch.setattr(f"nflengine.history_graph.docker.{name}",
                                lambda *a, **k: pytest.fail("the lab only reads"))  # fmt: skip
        monkeypatch.setattr("nflengine.history_graph.lab.run_lab", lambda jobs, **kw: {})
        assert invoke("lab").exit_code == 0


class TestStatusAndStart:
    def status(self, **over):
        base = {
            "container": "running", "up": True, "bolt_uri": "bolt://localhost:7688",
            "browser_url": "http://localhost:7475",
            "asof": {"label": "history graph 2018-2025 + 2026 week 5"},
            "build": {"build_id": "B1", "first_season": 2018, "through_season": 2025,
                      "seconds": {"total": 355.0}},
            "last_failed_build": None, "append": None, "counts": {"Play": 3},
            "store_mb": 1.0, "busy": False,
        }  # fmt: skip
        return {**base, **over}

    def test_status_shows_the_person_links_it_has(self, monkeypatch):
        dv = {"derive_id": "20261019T120100Z", "through_season": 2025, "season": 2026,
              "through_week": 5}  # fmt: skip
        monkeypatch.setattr("nflengine.history_graph.status.history_status",
                            lambda: self.status(derive=dv))  # fmt: skip
        r = invoke("status")
        assert "person links: derived 20261019T120100Z (through 2025 + 2026 week 5)" in r.output

    def test_status_of_a_build_only_derive(self, monkeypatch):
        dv = {"derive_id": "D", "through_season": 2025, "season": None, "through_week": None}
        monkeypatch.setattr("nflengine.history_graph.status.history_status",
                            lambda: self.status(derive=dv))  # fmt: skip
        r = invoke("status")
        assert "person links: derived D (through 2025)" in r.output

    def test_status_without_person_links_points_at_derive(self, monkeypatch):
        monkeypatch.setattr("nflengine.history_graph.status.history_status",
                            lambda: self.status(derive=None))  # fmt: skip
        r = invoke("status")
        assert "no person links yet: `uv run nfl history-graph derive`" in r.output

    def test_status_without_a_build_doesnt_ask_for_a_derive(self, monkeypatch):
        monkeypatch.setattr("nflengine.history_graph.status.history_status",
                            lambda: self.status(derive=None, build=None, asof=None))  # fmt: skip
        assert "no person links yet" not in invoke("status").output

    def test_status_counts_include_the_derived_types(self, monkeypatch):
        monkeypatch.setattr(
            "nflengine.history_graph.status.history_status",
            lambda: self.status(counts={"Play": 3, "TEAMMATE": 237000}),
        )
        assert "TEAMMATE" in invoke("status").output

    def test_start_is_allowed_after_an_unfinished_derive(self, monkeypatch, _tmp_data_root):
        from nflengine.history_graph.state import mark_unfinished

        mark_unfinished(_tmp_data_root, "derive", "B9")
        monkeypatch.setattr(
            "nflengine.history_graph.docker.start",
            lambda cfg, log: ServiceResult(True, "started", "history graph started"),
        )
        r = invoke("start")
        assert r.exit_code == 0 and "history graph started" in r.output

    @pytest.mark.parametrize("what", ["build"])
    def test_start_still_refuses_after_an_unfinished_build(self, monkeypatch, _tmp_data_root, what):
        from nflengine.history_graph.state import mark_unfinished

        mark_unfinished(_tmp_data_root, what, "B9")
        monkeypatch.setattr("nflengine.history_graph.docker.start",
                            lambda *a, **k: pytest.fail("must not start"))  # fmt: skip
        r = invoke("start")
        assert r.exit_code == 2 and "the last build didn't finish" in r.output

    def test_status_says_a_derive_didnt_finish(self, monkeypatch, _tmp_data_root):
        from nflengine.history_graph.state import mark_unfinished, read_unfinished

        mark_unfinished(_tmp_data_root, "derive", "B9")
        left = read_unfinished(_tmp_data_root)
        monkeypatch.setattr("nflengine.history_graph.status.history_status",
                            lambda: self.status(busy=True, unfinished=left))  # fmt: skip
        r = invoke("status")
        assert r.exit_code == 0 and "derive" in r.output


class TestBenchPaths:
    """`nfl history-graph bench-paths`: the CLI wiring only (test_history_bench has the rest)."""

    def fake(self, seen):
        def run(**kw):
            seen.update(kw)
            return {"wandb_url": "https://wandb.example/bench"}

        return run

    def test_the_defaults(self, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr("nflengine.history_graph.bench.run_bench", self.fake(seen))
        r = invoke("bench-paths")
        assert r.exit_code == 0, r.output
        assert seen["sizes"] is None and seen["pairs"] == 20 and seen["sources"] == 10
        assert seen["repeats"] == 3 and seen["seed"] == 42
        assert seen["budget_s"] == 150.0  # the default budget
        assert seen["use_wandb"] is True and seen["smoke"] is False
        assert "W&B: https://wandb.example/bench" in r.output

    def test_the_options(self, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr("nflengine.history_graph.bench.run_bench", self.fake(seen))
        args = [
            "--sizes",
            "1000, 2000,,5000",
            "--pairs",
            "5",
            "--sources",
            "2",
            "--repeats",
            "1",
            "--seed",
            "7",
            "--budget",
            "60",
            "--no-wandb",
            "--smoke",
        ]
        r = invoke("bench-paths", *args)
        assert r.exit_code == 0, r.output
        assert seen["sizes"] == [1000, 2000, 5000] and seen["pairs"] == 5
        assert seen["sources"] == 2 and seen["repeats"] == 1 and seen["seed"] == 7
        assert seen["budget_s"] == 60.0 and seen["use_wandb"] is False and seen["smoke"] is True

    def test_a_refusal_exits_2_with_its_words(self, monkeypatch):
        from nflengine.history_graph.bench import HistoryBenchError

        def refuse(**kw):
            raise HistoryBenchError("the weekly run holds the weekly lock [x]")

        monkeypatch.setattr("nflengine.history_graph.bench.run_bench", refuse)
        r = invoke("bench-paths")
        assert r.exit_code == 2 and "the weekly run holds the weekly lock [x]" in r.output

    def test_it_never_starts_or_stops_docker(self, monkeypatch):
        for name in ("start", "stop"):
            monkeypatch.setattr(
                f"nflengine.history_graph.docker.{name}",
                lambda *a, **k: pytest.fail("the benchmark only reads"),
            )
        monkeypatch.setattr("nflengine.history_graph.bench.run_bench", lambda **kw: {})
        assert invoke("bench-paths").exit_code == 0


class FakeServerError(Exception):
    """A driver error whose text echoes what the server said (it must never be printed)."""

    code = "Neo.ClientError.Procedure.ProcedureCallFailed"


class TestUnexpectedErrors:
    """Sol's AE03 review: an error that isn't the command's own is shown as its type and a fixed
    reason (`graph.client.safe_error`), never the driver's text, with no traceback."""

    @pytest.mark.parametrize(
        ("command", "target"),
        [
            (["derive"], "nflengine.history_graph.derive.run_derive"),
            (["lab"], "nflengine.history_graph.lab.run_lab"),
            (["bench-paths"], "nflengine.history_graph.bench.run_bench"),
            (["append"], "nflengine.history_graph.append.run_append"),
        ],
    )
    def test_the_drivers_text_never_reaches_the_terminal(self, monkeypatch, command, target):
        def boom(*a, **k):
            raise FakeServerError("server said: SECRET-DRIVER-TEXT at D:/nfl-ml-data/x")

        monkeypatch.setattr(target, boom)
        if command == ["append"]:
            monkeypatch.setattr("nflengine.history_graph.config.history_config", lambda: object())
        r = invoke(*command, "--no-wandb")
        assert r.exit_code == 1
        assert "FakeServerError: Neo.ClientError.Procedure.ProcedureCallFailed" in r.output
        assert "SECRET-DRIVER-TEXT" not in r.output and "nfl-ml-data" not in r.output
        assert "Traceback" not in r.output
        assert r.exception is None or isinstance(r.exception, SystemExit)
