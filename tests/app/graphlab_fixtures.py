"""Files for the Graph lab / Connections tests (AE03): the people index, `derive.json`,
`build.json`, the lab's parquet files, `lab.json` and a benchmark result, written under a data
root the test owns, and a fake "is the history graph up?" check. A plain module (no conftest in a
test subfolder); nothing here reaches Neo4j, Docker, W&B or the real data root."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from nflengine.paths import DataPaths

MAHOMES, KELCE, BRADY, NOBODY = "00-0033873", "00-0030506", "00-0019596", "00-0000099"
BUILD = {"status": "ok", "build_id": "B1", "first_season": 2018, "through_season": 2025,
         "built_at": "2026-10-18T10:00:00+00:00"}  # fmt: skip
APPEND = {"status": "ok", "build_id": "B1", "season": 2026, "through_week": 5,
          "appended_at": "2026-10-18T11:00:00+00:00"}  # fmt: skip
DERIVE = {
    "status": "ok", "build_id": "B1", "derive_id": "20261018T120000Z", "first_season": 2018,
    "through_season": 2025, "season": 2026, "through_week": 5,
    "derived_at": "2026-10-18T12:00:00+00:00",
    "counts": {"TEAMMATE": 237000, "CONNECTION": 60000, "OPPONENT": 400000, "COACHED": 12000,
               "WORKED_WITH": 900},
}  # fmt: skip
D = DERIVE["derive_id"]

PEOPLE_ROWS = [
    ("player", MAHOMES, "Patrick Mahomes", "QB", "KC", ["KC"], 2018, 2025, 5000, 40),
    ("player", KELCE, "Travis Kelce", "TE", "KC", ["KC"], 2018, 2025, 6000, 90),
    ("player", BRADY, "Tom Brady", "QB", "TB", ["NE", "TB"], 2018, 2022, 4000, 80),
    ("player", NOBODY, "Nobody Links", "WR", "KC", ["KC"], 2018, 2018, 900, 0),
    ("coach", "andy-reid", "Andy Reid", "coach", "KC", ["KC"], 2018, 2026, None, 60),
    ("coach", "bill-belichick", "Bill Belichick", "coach", "NE", ["NE"], None, None, None, 70),
]
PEOPLE_SCHEMA = {"kind": pl.String, "id": pl.String, "name": pl.String, "position": pl.String,
                 "team": pl.String, "teams": pl.List(pl.String), "first_season": pl.Int64,
                 "last_season": pl.Int64, "snaps": pl.Int64, "links": pl.Int64}  # fmt: skip


class FakeCheck:
    """`ask_service.HistoryCheck`'s `.state()`: 'up' | 'down' | 'checking'."""

    def __init__(self, state: str = "up") -> None:
        self.value = state
        self.calls = 0

    def state(self) -> str:
        self.calls += 1
        return self.value


def put_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def write_history(paths: DataPaths, *, append: bool = True, derive: bool = True,
                  people: bool = True) -> None:  # fmt: skip
    d = paths.neo4j_history
    put_json(d / "build.json", BUILD)
    if append:
        put_json(d / "append.json", APPEND)
    if derive:
        put_json(d / "derive.json", DERIVE)
    if people:
        pl.DataFrame(PEOPLE_ROWS, schema=PEOPLE_SCHEMA, orient="row").write_parquet(
            d / "people.parquet"
        )


def lab_dir(paths: DataPaths) -> Path:
    d = paths.neo4j_history / "lab"
    d.mkdir(parents=True, exist_ok=True)
    return d


def lab_entry(job: str, **over: Any) -> dict[str, Any]:
    base = {"status": "ok", "job": job, "derive_id": D, "ran_at": "2026-10-18T13:00:00+00:00",
            "params": {"seed": 42}, "wandb_url": f"https://wandb.example/{job}"}  # fmt: skip
    return {**base, **over}


def write_lab_json(paths: DataPaths, jobs: dict[str, dict[str, Any]]) -> None:
    put_json(lab_dir(paths) / "lab.json", jobs)


NB_SCHEMA = {"scope": pl.String, "player_id": pl.String, "rank": pl.Int64,
             "neighbour_id": pl.String, "similarity": pl.Float64, "snaps_together": pl.Int64,
             "name": pl.String, "position": pl.String, "grp": pl.String, "team": pl.String,
             "snaps": pl.Int64, "neighbour_name": pl.String, "neighbour_position": pl.String,
             "neighbour_grp": pl.String, "neighbour_team": pl.String,
             "neighbour_snaps": pl.Int64}  # fmt: skip


def write_neighbours(paths: DataPaths) -> None:
    def row(scope, rank, nid, name, pos, team, sim, together):
        return (scope, MAHOMES, rank, nid, sim, together, "Patrick Mahomes", "QB", "QB", "KC",
                5000, name, pos, pos, team, 4000)  # fmt: skip

    rows = [  # out of order on purpose: the reader sorts by scope and rank
        row("position", 2, BRADY, "Tom Brady", "QB", "TB", 0.88, 0),
        row("overall", 1, KELCE, "Travis Kelce", "TE", "KC", 0.91, 8372),
        row("position", 1, "00-0000001", "Josh Allen", "QB", "BUF", 0.93, 0),
        row("overall", 2, "00-0000001", "Josh Allen", "QB", "BUF", 0.9, 0),
        (
            "position",
            KELCE,
            1,
            "00-0000002",
            0.8,
            0,
            "Travis Kelce",
            "TE",
            "TE",
            "KC",
            6000,
            "George Kittle",
            "TE",
            "TE",
            "SF",
            4000,
        ),
    ]
    pl.DataFrame(rows, schema=NB_SCHEMA, orient="row").write_parquet(
        lab_dir(paths) / "neighbours.parquet"
    )
    mp = [(MAHOMES, 1.23456, -0.5, "Patrick Mahomes", "QB", "QB", "KC", 5000),
          (KELCE, 0.2, 0.3, "Travis Kelce", "TE", "TE", "KC", 6000),
          (BRADY, 1.1, -0.4, "Tom Brady", "QB", "QB", "TB", 4000)]  # fmt: skip
    pl.DataFrame(
        mp, schema={"player_id": pl.String, "x": pl.Float64, "y": pl.Float64, "name": pl.String,
                    "position": pl.String, "grp": pl.String, "team": pl.String,
                    "snaps": pl.Int64}, orient="row",
    ).write_parquet(lab_dir(paths) / "neighbours_map.parquet")  # fmt: skip


def write_families(paths: DataPaths, extra_members: int = 0, snaps_total: bool = True) -> None:
    fam = [
        (
            1,
            5,
            4,
            1,
            "KC",
            2019,
            2022,
            ["Patrick Mahomes", "Travis Kelce"],
            ["Andy Reid"],
            "offense",
            "2019-2022 KC offense",
        ),
        (2, 5, 5, 0, "DEN", 2020, 2021, ["A Defender"], None, "defense", "2020-2021 DEN defense"),
    ]
    pl.DataFrame(
        fam, schema={"community": pl.Int64, "size": pl.Int64, "players": pl.Int64,
                     "coaches": pl.Int64, "team": pl.String, "first_season": pl.Int64,
                     "last_season": pl.Int64, "top_players": pl.List(pl.String),
                     "top_coaches": pl.List(pl.String), "unit": pl.String, "name": pl.String},
        orient="row",
    ).write_parquet(lab_dir(paths) / "families.parquet")  # fmt: skip
    mem = [(1, "player", MAHOMES, "Patrick Mahomes", "QB", "QB", 5000, 3900),
           (1, "player", KELCE, "Travis Kelce", "TE", "TE", 6000, 4200),
           (1, "coach", "andy-reid", "Andy Reid", "coach", "COACH", None, None),
           (2, "player", "00-0000050", "A Defender", "CB", "DB", 3000, 1500)]  # fmt: skip
    mem += [(1, "player", f"00-{i:07d}", f"Extra {i}", "WR", "WR", 10, 1)
            for i in range(1000, 1000 + extra_members)]  # fmt: skip
    schema = {"community": pl.Int64, "kind": pl.String, "id": pl.String, "name": pl.String,
              "position": pl.String, "grp": pl.String, "snaps_total": pl.Int64,
              "snaps": pl.Int64}  # fmt: skip
    df = pl.DataFrame(mem, schema=schema, orient="row")
    if not snaps_total:
        df = df.drop("snaps_total")
    df.write_parquet(lab_dir(paths) / "family_members.parquet")


def write_chemistry(paths: DataPaths) -> None:
    def duo(qb, qbn, rid, rn, pos, targets, catches, yards, tds, epa, seasons, teams):
        return (qb, qbn, rid, rn, pos, targets, catches, yards, tds, epa, seasons, teams,
                round(epa / targets, 4), round(catches / targets, 4), round(yards / targets, 2),
                min(seasons), max(seasons))  # fmt: skip

    rows = [
        duo(
            MAHOMES,
            "Patrick Mahomes",
            KELCE,
            "Travis Kelce",
            "TE",
            1000,
            700,
            8000,
            70,
            200.0,
            [2018, 2025],
            ["KC"],
        ),
        duo(
            MAHOMES,
            "Patrick Mahomes",
            "00-0000003",
            "Tyreek Hill",
            "WR",
            400,
            260,
            3500,
            30,
            120.0,
            [2018, 2021],
            ["KC"],
        ),
        duo(
            BRADY,
            "Tom Brady",
            "00-0000004",
            "Mike Evans",
            "WR",
            300,
            180,
            2500,
            20,
            30.0,
            [2020, 2022],
            ["TB"],
        ),
        duo(
            BRADY,
            "Tom Brady",
            "00-0000005",
            "Cameron Brate",
            "TE",
            120,
            80,
            700,
            8,
            0.0,
            [2020],
            ["TB"],
        ),
        duo(
            MAHOMES,
            "Patrick Mahomes",
            "00-0000006",
            "A Reserve",
            "WR",
            90,
            50,
            300,
            1,
            5.0,
            [2023],
            ["KC"],
        ),  # under the minimum volume (100)
    ]
    pl.DataFrame(
        rows, schema={"qb_id": pl.String, "qb": pl.String, "receiver_id": pl.String,
                      "receiver": pl.String, "position": pl.String, "targets": pl.Int64,
                      "catches": pl.Int64, "yards": pl.Int64, "touchdowns": pl.Int64,
                      "epa": pl.Float64, "seasons": pl.List(pl.Int64), "teams": pl.List(pl.String),
                      "epa_per_target": pl.Float64, "catch_rate": pl.Float64,
                      "yards_per_target": pl.Float64, "first_season": pl.Int64,
                      "last_season": pl.Int64}, orient="row",
    ).write_parquet(lab_dir(paths) / "chemistry_duos.parquet")  # fmt: skip
    ranks = [(f"r{i:02d}", f"Receiver {i}", "WR", "WR", 1.0 / i, 0, 2, "receiver", i)
             for i in range(1, 31)]  # fmt: skip
    ranks += [(f"q{i:02d}", f"Quarterback {i}", "QB", "QB", 0.5 / i, 5, 0, "qb", i)
              for i in range(1, 21)]  # fmt: skip
    pl.DataFrame(
        ranks, schema={"player_id": pl.String, "name": pl.String, "position": pl.String,
                       "grp": pl.String, "score": pl.Float64, "receivers": pl.Int64,
                       "qbs": pl.Int64, "role": pl.String, "rank": pl.Int64}, orient="row",
    ).write_parquet(lab_dir(paths) / "chemistry_pagerank.parquet")  # fmt: skip


BENCH_DOC = {
    "version": 1, "derive_id": D, "seed": 42, "pairs": 20, "sources": 10, "repeats": 3,
    "budget_s": 600.0, "smoke": False, "started_at": "2026-10-18T14:00:00+00:00",
    "seconds": 812.5,
    "sizes": [{"label": "1000", "n": 1000, "m": 9000, "requested": 1000},
              {"label": "full", "n": 7000, "m": 480000, "requested": None}],
    "machine": {"cpu_count": 8, "ram_gb": 32, "platform": "Windows-11", "neo4j": "5.26"},
    "gds_procedures": ["gds.shortestPath.dijkstra.stream"],
    "jit_compile_s": 4.2,
    "table": [{"algorithm": "x"}],
    "times_ms": {"cypher-shortest": [1.0, 2.0]},
    "setup": [{"size_n": 1000, "step": "load", "ms": 12.0}],
    "failures": [], "agrees_all": True, "wandb_url": "https://wandb.example/bench",
}  # fmt: skip
BENCH_ROWS = [
    ("cypher-shortest", "pair", 1000, 9000, 20, 0.8, 1.2, 0.5, True, None),
    ("cypher-shortest", "pair", 7000, 480000, 20, 3.1, 5.0, 2.0, True, None),
    ("floyd-warshall-numba", "all", 7000, 480000, 0, None, None, None, None,
     "predicted 4,000 s: over the budget"),
]  # fmt: skip


def write_benchmark(paths: DataPaths, doc: dict[str, Any] | None = None, rows: bool = True,
                    doc_file: bool = True) -> None:  # fmt: skip
    if doc_file:
        put_json(lab_dir(paths) / "path_benchmark.json", doc if doc is not None else BENCH_DOC)
    if rows:
        pl.DataFrame(
            BENCH_ROWS, schema={"algorithm": pl.String, "family": pl.String, "size_n": pl.Int64,
                                "size_m": pl.Int64, "runs": pl.Int64, "median_ms": pl.Float64,
                                "p90_ms": pl.Float64, "min_ms": pl.Float64,
                                "agrees": pl.Boolean, "note": pl.String}, orient="row",
        ).write_parquet(lab_dir(paths) / "path_benchmark.parquet")  # fmt: skip


def write_everything(paths: DataPaths) -> None:
    write_history(paths)
    write_lab_json(paths, {
        "neighbours": lab_entry("neighbours", players=4300, explained_variance_2d=[0.08, 0.05]),
        "families": lab_entry("families", communities=300, families=120, modularity=0.93,
                              largest=80, median_size=12.0),
        "chemistry": lab_entry("chemistry", duos=2000, ranked=900),
    })  # fmt: skip
    write_neighbours(paths)
    write_families(paths)
    write_chemistry(paths)
    write_benchmark(paths)


# ---- a driver for the Connections service ----------------------------------------------------


class _Consumable:
    def consume(self) -> None:
        return None

    def single(self) -> Any:
        return None


class _Session:
    def __init__(self, driver: FakeDriver) -> None:
        self.driver = driver

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def run(self, query: str, **params: Any) -> _Consumable:
        self.driver.queries.append(" ".join(query.split()))
        if self.driver.fail:
            raise self.driver.fail
        return _Consumable()


class FakeDriver:
    """What `ConnectionsService` holds: it only has to open sessions and close."""

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.closed = False
        self.fail: BaseException | None = None

    def session(self, **_kw: Any) -> _Session:
        return _Session(self)

    def close(self) -> None:
        self.closed = True


class Factory:
    """`driver_factory=`: counts the drivers it makes."""

    def __init__(self) -> None:
        self.drivers: list[FakeDriver] = []

    def __call__(self) -> FakeDriver:
        d = FakeDriver()
        self.drivers.append(d)
        return d


def canned_connection(a: str = MAHOMES, b: str = KELCE, **over: Any) -> dict[str, Any]:
    """What `connections.connect` returns for two teammates."""
    res = {
        "a": {"kind": "player", "id": a, "name": "Patrick Mahomes", "position": "QB", "links": 40},
        "b": {"kind": "player", "id": b, "name": "Travis Kelce", "position": "TE", "links": 90},
        "mode": "fewest", "k": 1, "found": True, "note": None, "ms": 12,
        "paths": [{
            "hops": 1, "weakest_snaps": 8372, "cost": 0.000119,
            "people": [
                {"kind": "player", "id": a, "name": "Patrick Mahomes", "position": "QB"},
                {"kind": "player", "id": b, "name": "Travis Kelce", "position": "TE"}],
            "links": [{"type": "TEAMMATE", "snaps": 8372, "seasons": [2018, 2025],
                       "teams": ["KC"], "stints": [], "text": "teammates on 2019 KC, 8,372 snaps",
                       "from": 0, "to": 1}],
        }],
    }  # fmt: skip
    return {**res, **over}
