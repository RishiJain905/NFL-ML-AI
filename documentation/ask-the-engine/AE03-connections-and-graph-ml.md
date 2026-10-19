# AE03: Connections, graph experiments and the path benchmark

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Ask the Engine rows)

- **Depends on:** AE01 (the page it sits beside), AE02 (the history graph)
- **Unlocks:** the fun side: six degrees, player neighbourhoods, football families, and a real look at shortest-path algorithms
- **Read first:**
  - [Ask the Engine README](README.md) §1 (the sub-cubic paper), §3 (Connections, Graph lab);
  - the [Graph Data Science guide](../guides/graph-data-science.md) (projections, PageRank, KNN, stream / write / mutate);
  - AE02's history-graph guide;
  - the `model-experiment` skill (W&B logging).

## Goal

Three things on the history graph:
1. **Connections ("six degrees"):** the shortest chain between any two players or coaches, each link with its evidence.
2. **Graph lab:** new relationships derived from plays (teammates by snaps shared, QB → receiver chemistry) and GDS experiments on them (embeddings, communities, centrality), shown as small, explorable views.
3. **The path benchmark:** how shortest-path algorithms actually perform on our graph, written up with a plain explanation of the new Alman–Vassilevska Williams result and why it doesn't change practice.

## Scope

- **In:**
  - derived relationships (built by the history graph's commands);
  - the GDS jobs;
  - the Connections and Graph lab parts of Explore → Ask the Engine (mockup ✋ first);
  - Ask templates for them;
  - the benchmark command and its W&B runs;
  - the guide sections.
- **Out:** anything feeding the weekly models or digest (research only, D107).

## Tasks

### Derived relationships (history graph only)
- [x] 🤖 **`TEAMMATE {snaps, seasons, teams}`** between players on the field together on the same side (from `ON_FIELD`), aggregated in DuckDB first (about 37M pair-events → about 1–2M edges, an estimate to check), loaded in bulk.
- [x] 🤖 **`CONNECTION {targets, catches, yards, epa, seasons}`** QB → receiver (from `PASSED` / `TARGETED`).
- [x] 🤖 **`OPPONENT {plays}`** between players on opposite sides of the same plays (optional: check the size first, and restrict to skill positions vs DBs and OL vs DL if it's huge).
- [x] 🤖 **Coaching links:** `COACHED {seasons}` (coach → player, through `PLAYED_FOR` + `HEAD_COACH` / coordinators), `WORKED_WITH` between coaches on the same staff.
- [x] 🤖 **`nfl history-graph derive`** builds them, idempotently, after `build` / `append`; counts in `build.json` and the W&B run.

### Connections
- [x] 🤖 **`history_graph/connections.py`:** the shortest path between two people over `TEAMMATE` / `COACHED` / `WORKED_WITH`, with Cypher's `SHORTEST` (bidirectional BFS).
  - **Hub handling:** paths go person → person, never through Team or Season nodes.
  - **Options:** the k shortest (`SHORTEST k`), and "strongest", which weights links by snaps shared (GDS Dijkstra on 1 / snaps).
  - **Evidence per link:** "teammates on 2019 KC, 612 snaps together".
- [x] 🤖 **API:**
  - `GET /api/ask/people?q=`: fuzzy name search;
  - `GET /api/ask/connections?a=&b=&k=`: ids from the search, validated.
- [x] 🤖 **Tests:** known pairs (two teammates → 1 link; a known 2-step pair); no path through hubs; time under 1 s on the full graph. (`tests/history_graph/test_history_connections_live.py`, `-m integration`: Mahomes–Kelce 1 link, Brady–Mahomes and Reid–Belichick 2, only Player / Coach nodes and person links, warm connects under 1 s)

### Graph lab (GDS on the history graph)
- [x] 🤖 **Player neighbourhoods:** FastRP embeddings on the `TEAMMATE` / `CONNECTION` graph → KNN: "players most like X by who they played with and how". Logged to W&B as a table, and the embedding's 2-D projection (UMAP or PCA) as a chart.
- [x] 🤖 **Football families:** Louvain or Leiden communities on `TEAMMATE` + coaching links. Name them by their biggest team-era ("2019–2021 KC core"), with sizes and members.
- [x] 🤖 **Chemistry:** QB → receiver `CONNECTION` ranked by EPA per target with a minimum volume; PageRank on the `CONNECTION` graph for the most central receivers.
- [x] 🤖 **`nfl history-graph lab [--job neighbours|families|chemistry]`** writes results to `{NFL_DATA_ROOT}/neo4j-history/lab/*.parquet` and logs W&B runs (`job_type: history-graph-lab`).
- [x] 🤖 **API + page:** `GET /api/ask/lab/{neighbours|families|chemistry}?…`, read from the lab files. The Graph lab section shows each as a small explorable view.

### The path benchmark
- [x] 🤖 **`nfl history-graph bench-paths`:** on the history graph's person graph (and on samples of 1k, 5k, 20k nodes), time:
  - single pair: Cypher `SHORTEST` and GDS Dijkstra (source-target);
  - single source: GDS Delta-Stepping and scipy `csgraph.dijkstra`;
  - an implementation of Duan et al. 2025 "Breaking the Sorting Barrier" SSSP (arXiv 2504.17033; the public C++ / Rust ports, if their licences allow);
  - all pairs: GDS All Pairs Shortest Path and Floyd–Warshall (numba), on the small samples only.

  Log a W&B run (`job_type: path-benchmark`): time by algorithm and size, a log-log chart.
- [x] 🤖 **Write-up** in the history-graph guide:
  - what APSP and 3SUM are;
  - what the Alman–Vassilevska Williams paper (arXiv 2610.06783) proved and why it matters to theory (it breaks two central hypotheses; the paper credits Claude with finding the algorithm);
  - why it's "galactic": n^0.0005 savings, huge constants, no code;
  - what our benchmark shows (point-to-point BFS wins at our scale; Duan et al. vs Dijkstra in practice).

### Mockup, verify and close
- [x] 🤖 **Mockup** of Connections and Graph lab, both themes. ✋ **Rishi approves.** (waived at the kickoff: [mockup](https://claude.ai/artifact/JLpvtxrLwAvzxT9DzjzdeD), the agent stood in for the look-throughs)
- [x] 🤖 Web: the Connections path view (an SVG chain with link evidence), the lab views; tests.
- [x] 🤖 Codex / Sol review; the weekly-graph check again; production-unchanged check. (Sol: 10 findings, all fixed, D151; the weekly graph identical with the history graph and its projection up, history-graph guide §9; `tests/test_production_untouched.py` 9 passed)
- [x] 🤖 **Docs:**
  - the history-graph guide (derived relationships, Connections, each lab job with real results, the benchmark);
  - the Ask guide (the new parts);
  - the GDS guide (a pointer: the history graph's GDS jobs);
  - the W&B guide (`history-graph-lab`, `path-benchmark`).
- [x] ✋ **Close AE03**, which closes the core Ask the Engine track (AE04 is optional). (waived at the kickoff: "marking the phase complete"; closed 2026-10-19)

## Rishi-in-the-loop moments (what to look for)

- **Connections:** try pairs you know (two former teammates, a QB and a coach he never played for). Is the path believable and the evidence clear?
- **Graph lab:** do the "football families" look like real eras? Do a player's neighbours make sense?
- **The benchmark chart:** expect BFS / `SHORTEST` in milliseconds and all-pairs to blow up past a few thousand nodes.

## Exit criteria (+ how to verify)

- Connections answers in under 1 s with evidence.
- The three lab jobs and the benchmark are run, logged and written up.
- The weekly-graph check is identical; tests, ruff, web checks and the production-unchanged check pass.

## Pitfalls / notes

- **Hubs make "six degrees" trivial** (everyone is two steps apart through a team). Persons-only paths are the point.
- **`TEAMMATE` can get dense:** a long-time starter has thousands of edges. Typed, directed traversals stay fast; don't project the whole graph into GDS without a filter.
- **Embeddings are random-seeded:** set the seed and log it, so a run can be reproduced.

## As built: deviations from the task list (2026-10-19; D147-D151)

Rishi at the kickoff: subagents allowed, `/sol-qa` review before the commit, the ✋ close waived ("marking the phase complete"); asked once: the ✋ mockup approval **waived, the agent stands in for the look-throughs**; OpenRouter **up to $0.30, the new golden ids only** (spent $0.021); Duan et al. 2025 **our own implementation** (no third-party code).

- **TEAMMATE is 237,136 links, not 1-2M** (39.8M pair-events collapse to ~93 teammates a player). **OPPONENT keeps only the matchups the task named** (skill vs DB, OL vs DL) plus the QB vs any defender: all pairs would be 1.85M.
- **The derived links are synced over Bolt, not bulk-imported** ("loaded in bulk" in the task): the same diff sync as the append, so `build`, `append` and `derive` share one path and a re-run writes nothing (662,822 writes the first time, ~150 s). Counts are in `derive.json` and in the build's / append's documents and W&B runs (`derive` section), plus a W&B run of its own for `nfl history-graph derive`.
- **COACHED comes from curated snap counts** (`snaps`), with `PLAYED_FOR`'s team-seasons as the frame: the box score misses most linemen (only 176 of 468 2026 linemen had a box-score game). A stint's team is the player's team in that game (D147: 56,685 links fixed after the UI worker spotted "teammates on ARI 2021").
- **Connections' "fewest"** ranks equally short chains by strength (the task: `SHORTEST k` alone, which picked arbitrary weak chains); **"strongest"** is GDS Dijkstra / Yen's on 1 / snaps as the task says. The projection is warmed in the background by `nfl app` (32 s cold). Under 1 s warm: 16 ms median (fewest), 47-86 ms (strongest).
- **The people search** reads a people index the derive writes (`people.parquet`), not Neo4j, so it works with the container stopped.
- **Graph lab:** Leiden at resolution 16 (names read at 1-16: eras, not franchises); neighbours by FastRP + KNN (overall, GDS) and an exact search within the position group; chemistry's minimum 100 targets (the page offers 100-500).
- **The benchmark's sizes** are 1,000 / 2,000 / 5,000 + the whole graph (5,665): a 20k sample can't exist. GDS 2.13 has `allShortestPaths.stream`, timed by count and sum aggregated in the database. `bench-paths` and `lab` refuse while the weekly lock is held (a weekly-graph rebuild took ~17 minutes instead of ~3 while heavy history-graph work shared the HDD).
- **Ask templates:** six, with a new `person` parameter type (a player or a coach); the first targeted run found two templates without a usable `limit` for "the most" questions (fixed, 8 of 8). After the review, g141's reference turned out wrong (it counted a WR's trick-play passes as a quarterback's: 11 QBs, not 12; fixed) and the re-check routed it to a list instead of a count (routing variance, not re-run again).
- **Sol's review (D151)** found 10 real problems, all fixed with tests: an append that cleared a half-written derive's marker too early (now `derive_pending`), a `derive_id` reused after a crash, raw driver errors at the new commands, two labs / benchmarks able to drop each other's projections or keep results from links that changed mid-run (their own lock, `links_unchanged`), WORKED_WITH not limited to the weeks both coaches were there (7 links gone, 69 changed), COACHED role snaps merged (318 links changed), a coach pick in "which one?" that looped or answered for the player, Connections ignoring Back / Forward, a benchmark with a failed step finishing W&B as success, and a cache race in the people facts. The fixes' re-derive: 394 writes ([d3by1b6g](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/d3by1b6g)); the lab re-run on it (same 164 communities / 132 families).
- **The look-throughs Rishi waived** (the agent stood in): Connections on pairs a fan knows (Mahomes–Kelce 1 link; Brady–Mahomes via Joe Thuney; a QB and a coach he never played for, Mahomes–Belichick via Thuney; Reid–Belichick via Thuney; Cam Ward–Brady in 3): believable, evidence clear. Families read as real eras at resolution 16 ("2018-2021 KC offense" with Tyreek Hill, "2021-2025 KC offense", "2018-2022 GB offense" with Rodgers and Davante Adams). Neighbours are "by who he played with": a player's own unit and backups, which is what FastRP on teammate links measures. The benchmark chart: BFS / `SHORTEST` in milliseconds, all pairs blowing up past a few thousand people, as the phase expected.
