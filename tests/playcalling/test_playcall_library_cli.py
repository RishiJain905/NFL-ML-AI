"""`nfl playcalling library` (PC03): the CLI wiring of the Big Data Bowl concept library, with
`bdb.list_files`, `bdb.download` and `bdb.build_library` stubbed and the data root pointed at a
tmp tree: nothing here reaches Kaggle, D: or the network, and no env file is read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pytest
from typer.testing import CliRunner

import nflengine.paths as paths_mod
import nflengine.playcalling.bdb as bdb
from nflengine import cli
from nflengine.cli import app
from nflengine.paths import DataPaths, DataRootError
from nflengine.weekly import EXIT_NO_DRIVE

runner = CliRunner()

LISTING = [
    {"name": "supplementary_data.csv", "bytes": 5_000_000},
    {"name": "train/input_2023_w01.csv", "bytes": 700_000_000},
    {"name": "train/output_2023_w01.csv", "bytes": 30_000_000},
    {"name": "train/input_2023_w02.csv", "bytes": 800_000_000},
    {"name": "train/output_2023_w02.csv", "bytes": 40_000_000},
]


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


@dataclass
class Env:
    paths: DataPaths
    calls: list[tuple[str, tuple, dict]] = field(default_factory=list)
    listing: list[dict] = field(default_factory=lambda: list(LISTING))
    raises: dict[str, Exception] = field(default_factory=dict)
    index: dict[str, Any] = field(
        default_factory=lambda: {
            "concepts": {"screen": {"examples": [{}, {}]}, "cover_3": {"examples": [{}]}}
        }
    )
    build_log: list[str] = field(default_factory=list)

    @property
    def order(self) -> list[str]:
        return [c[0] for c in self.calls]

    def of(self, name: str) -> list[tuple[tuple, dict]]:
        return [(a, k) for n, a, k in self.calls if n == name]


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    paths = DataPaths(tmp_path)
    for d in paths.all_dirs():
        d.mkdir(parents=True, exist_ok=True)
    e = Env(paths)

    def stub(name, result):
        def fn(*a, **k):
            e.calls.append((name, a, k))
            if name in e.raises:
                raise e.raises[name]
            return result() if callable(result) else result

        return fn

    monkeypatch.setattr(paths_mod, "ensure_data_root", lambda *a, **k: paths)
    monkeypatch.setattr(bdb, "list_files", stub("list_files", lambda: e.listing))
    monkeypatch.setattr(bdb, "download", stub("download", []))

    def build(*a, **k):
        e.calls.append(("build_library", a, k))
        if "build_library" in e.raises:
            raise e.raises["build_library"]
        for line in e.build_log:
            k["log"](line)
        return e.index

    monkeypatch.setattr(bdb, "build_library", build)
    monkeypatch.setattr(cli.console, "_width", 250)  # no wrapping inside long lines
    return e


def run(*args: str):
    return runner.invoke(app, ["playcalling", "library", *args])


# --- wiring ------------------------------------------------
def test_the_command_and_its_options_are_listed() -> None:
    group = runner.invoke(app, ["playcalling", "--help"])
    assert group.exit_code == 0 and "library" in group.output
    out = runner.invoke(app, ["playcalling", "library", "--help"])
    assert out.exit_code == 0
    for opt in ("--list", "--download", "--weeks", "--per-concept", "--build", "--no-build"):
        assert opt in out.output, opt
    text = flat(out.output)
    assert "Big Data Bowl" in text and "KAGGLE_API_TOKEN" in text and "non-commercial" in text


def test_the_options_name_this_edition_not_a_withdrawn_one() -> None:
    text = flat(runner.invoke(app, ["playcalling", "library", "--help"]).output)
    assert "2026" in text and "2025 files" not in text


# --- build (the default) ------------------------------------------------
def test_by_default_it_only_builds_from_the_files_on_disk(env: Env) -> None:
    res = run()
    assert res.exit_code == 0, res.output
    assert env.order == ["build_library"]  # no listing, no download
    ((args, kwargs),) = env.of("build_library")
    assert args == (env.paths, 10, None) and callable(kwargs["log"])
    assert "library: 3 real plays in 2 concepts" in flat(res.output)


def test_per_concept_and_weeks_go_to_the_build(env: Env) -> None:
    res = run("--per-concept", "5", "--weeks", "2,1,2")
    assert res.exit_code == 0, res.output
    ((args, _),) = env.of("build_library")
    assert args == (env.paths, 5, [1, 2])  # sorted, each week once


@pytest.mark.parametrize("weeks", ["1,2", "1 2", "1, 2", "1,,2", " 1 ,2 "])
def test_weeks_may_be_separated_by_commas_or_spaces(env: Env, weeks: str) -> None:
    assert run("--weeks", weeks).exit_code == 0
    assert env.of("build_library")[0][0][2] == [1, 2]


@pytest.mark.parametrize("weeks", ["1", "18", "9"])
def test_the_edge_weeks_of_the_edition_are_accepted(env: Env, weeks: str) -> None:
    assert run("--weeks", weeks).exit_code == 0
    assert env.of("build_library")[0][0][2] == [int(weeks)]


@pytest.mark.parametrize("weeks", ["0", "19", "100", "abc", "1.5", "-1", "1;2", "one", "1,x"])
def test_weeks_outside_the_edition_or_not_numbers_are_a_usage_error(env: Env, weeks: str) -> None:
    res = run("--weeks", weeks)
    assert res.exit_code == 2, (weeks, res.exit_code, res.output)
    assert "--weeks" in flat(res.output)
    assert env.calls == []  # nothing was listed, downloaded or built


@pytest.mark.parametrize("n", ["0", "41", "-3", "x"])
def test_per_concept_is_one_to_forty(env: Env, n: str) -> None:
    res = run("--per-concept", n)
    assert res.exit_code == 2 and env.calls == []


def test_per_concept_edges(env: Env) -> None:
    for n in (1, 40):
        assert run("--per-concept", str(n)).exit_code == 0
        assert env.of("build_library")[-1][0][1] == n


def test_no_build_and_nothing_else_does_nothing(env: Env) -> None:
    res = run("--no-build")
    assert res.exit_code == 0 and env.calls == []


def test_the_builds_log_lines_are_printed_without_rich_markup(env: Env) -> None:
    env.build_log = ["week 1: 6 plays [1, 2] [red]x[/red] [/]"]
    res = run()
    assert res.exit_code == 0
    assert "week 1: 6 plays [1, 2] [red]x[/red] [/]" in flat(res.output)


# --- --list ------------------------------------------------
def test_list_prints_the_files_with_sizes_and_a_total_and_writes_nothing(env: Env) -> None:
    res = run("--list")
    out = flat(res.output)
    assert res.exit_code == 0, res.output
    assert env.order == ["list_files"]  # no download, no build
    for f in LISTING:
        assert f["name"] in out
    assert "700.0 MB" in out and "5.0 MB" in out
    assert "5 files, 1.57 GB" in out
    # sorted by name
    names = [n for n in re.findall(r"(supplementary_data\.csv|train/\S+\.csv)", out)]
    assert names == sorted(names)


def test_list_marks_the_files_already_on_disk_whatever_folder_kaggle_keeps_them_in(
    env: Env,
) -> None:
    raw = bdb.raw_dir(env.paths)
    raw.mkdir(parents=True)
    (raw / "supplementary_data.csv").write_text("x")
    (raw / "input_2023_w01.csv").write_text("x")  # saved by its file name, not train/...
    out = flat(run("--list").output)
    assert re.search(r"supplementary_data\.csv\s+5\.0 MB \(on disk\)", out)
    assert re.search(r"train/input_2023_w01\.csv\s+700\.0 MB \(on disk\)", out)
    assert not re.search(r"train/output_2023_w01\.csv\s+30\.0 MB \(on disk\)", out)


def test_list_beats_download_and_build(env: Env) -> None:
    res = run("--list", "--download", "--weeks", "1")
    assert res.exit_code == 0 and env.order == ["list_files"]


# --- --download ------------------------------------------------
def test_download_resolves_kaggles_folder_names_then_downloads_then_builds(env: Env) -> None:
    res = run("--download", "--weeks", "1")
    assert res.exit_code == 0, res.output
    assert env.order == ["list_files", "download", "build_library"]
    ((args, kwargs),) = env.of("download")
    assert args[0] == env.paths
    assert list(args[1]) == [
        "supplementary_data.csv",
        "train/input_2023_w01.csv",
        "train/output_2023_w01.csv",
    ]
    assert callable(kwargs["log"])
    ((bargs, _),) = env.of("build_library")
    assert bargs == (env.paths, 10, [1])  # the build is limited to the weeks just downloaded


def test_download_of_two_weeks(env: Env) -> None:
    run("--download", "--weeks", "1,2", "--per-concept", "3")
    ((args, _),) = env.of("download")
    assert list(args[1]) == [
        "supplementary_data.csv",
        "train/input_2023_w01.csv",
        "train/output_2023_w01.csv",
        "train/input_2023_w02.csv",
        "train/output_2023_w02.csv",
    ]
    assert env.of("build_library")[0][0] == (env.paths, 3, [1, 2])


def test_download_without_weeks_is_a_usage_error_and_does_nothing(env: Env) -> None:
    res = run("--download")
    assert res.exit_code == 2 and "--weeks" in flat(res.output)
    assert env.of("download") == [] and env.of("build_library") == []


def test_download_no_build_stops_after_the_files(env: Env) -> None:
    res = run("--download", "--weeks", "2", "--no-build")
    assert res.exit_code == 0 and env.order == ["list_files", "download"]


def test_a_week_kaggle_does_not_list_fails_loudly_before_any_download(env: Env) -> None:
    env.listing = LISTING[:3]  # no week 2
    res = run("--download", "--weeks", "2")
    assert res.exit_code == 1
    out = flat(res.output)
    assert "input_2023_w02.csv" in out and "output_2023_w02.csv" in out
    assert env.order == ["list_files"]


# --- errors ------------------------------------------------
@pytest.mark.parametrize("where", ["list_files", "download", "build_library"])
def test_a_kaggle_error_is_exit_1_with_the_message_and_no_secret(env: Env, where: str) -> None:
    env.raises[where] = bdb.KaggleError(
        "downloading x: Kaggle refused KAGGLE_KEY=abcdef123456 and [red]markup[/red]"
    )
    args = {
        "list_files": ["--list"],
        "download": ["--download", "--weeks", "1"],
        "build_library": [],
    }[where]
    res = run(*args)
    out = flat(res.output)
    assert res.exit_code == 1, res.output
    assert "Kaggle refused" in out and "abcdef123456" not in out
    assert "KAGGLE_KEY=***" in out and "[red]markup[/red]" in out  # printed literally


def test_a_missing_file_is_exit_1(env: Env) -> None:
    env.raises["build_library"] = FileNotFoundError(
        "Big Data Bowl files missing under bdb/2026/raw: ['supplementary_data.csv']"
    )
    res = run()
    assert res.exit_code == 1 and "supplementary_data.csv" in flat(res.output)


def test_no_credentials_is_the_kaggle_error_message(env: Env) -> None:
    env.raises["list_files"] = bdb.KaggleError(bdb.NO_CREDENTIALS)
    res = run("--list")
    assert res.exit_code == 1
    assert "KAGGLE_API_TOKEN" in flat(res.output) and "KAGGLE_USERNAME" in flat(res.output)


def test_a_missing_data_drive_is_exit_5_and_nothing_runs(env: Env, monkeypatch) -> None:
    def gone(*a, **k):
        raise DataRootError("Data drive D:\\ is not connected (NFL_DATA_ROOT=D:/nfl-ml-data).")

    monkeypatch.setattr(paths_mod, "ensure_data_root", gone)
    res = run("--list")
    assert res.exit_code == EXIT_NO_DRIVE == 5
    assert "not connected" in flat(res.output) and env.calls == []


def test_the_command_never_reads_the_env_through_a_real_call(env: Env, monkeypatch) -> None:
    """The data root is the stub's; if the command asked settings for anything, this fails."""
    import nflengine.settings as S

    monkeypatch.setattr(S, "get_env", lambda: pytest.fail("the CLI read the env"))
    assert run().exit_code == 0 and run("--list").exit_code == 0


# ===== CLI ranges =====


@pytest.mark.parametrize(
    ("weeks", "want"),
    [
        ("3-5", [3, 4, 5]),
        ("1-3,7", [1, 2, 3, 7]),
        ("7,1-3", [1, 2, 3, 7]),
        ("2-2", [2]),
        ("1-18", list(range(1, 19))),
        ("1,1-2", [1, 2]),
        ("5 6-7", [5, 6, 7]),
        ("17-18,1", [1, 17, 18]),
    ],
)
def test_weeks_may_be_ranges_and_come_out_sorted_and_without_repeats(env: Env, weeks, want) -> None:
    res = run("--weeks", weeks)
    assert res.exit_code == 0, res.output
    assert env.of("build_library")[0][0][2] == want


@pytest.mark.parametrize(
    "weeks", ["5-3", "1-19", "0-3", "1-", "-3", "1-2-3", "a-b", "1 - 3", "1--3", "3-x", "19-20"]
)
def test_a_bad_range_is_a_usage_error_and_nothing_runs(env: Env, weeks) -> None:
    res = run("--weeks", weeks)
    assert res.exit_code == 2, (weeks, res.exit_code, res.output)
    assert "--weeks" in flat(res.output) and env.calls == []


def test_download_takes_a_range(env: Env) -> None:
    env.listing = [
        {"name": "supplementary_data.csv", "bytes": 1},
        *({"name": f"train/{n}", "bytes": 1} for n in bdb.week_files([1, 2, 3])),
    ]
    res = run("--download", "--weeks", "1-2")
    assert res.exit_code == 0, res.output
    ((args, _),) = env.of("download")
    assert list(args[1]) == [
        "supplementary_data.csv",
        "train/input_2023_w01.csv",
        "train/output_2023_w01.csv",
        "train/input_2023_w02.csv",
        "train/output_2023_w02.csv",
    ]
    assert env.of("build_library")[0][0][2] == [1, 2]


def test_the_help_shows_the_range_form() -> None:
    assert "1-18" in flat(runner.invoke(app, ["playcalling", "library", "--help"]).output)
