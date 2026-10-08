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
- [ ] 🤖 **`TEAMMATE {snaps, seasons, teams}`** between players on the field together on the same side (from `ON_FIELD`), aggregated in DuckDB first (about 37M pair-events → about 1–2M edges, an estimate to check), loaded in bulk.
- [ ] 🤖 **`CONNECTION {targets, catches, yards, epa, seasons}`** QB → receiver (from `PASSED` / `TARGETED`).
- [ ] 🤖 **`OPPONENT {plays}`** between players on opposite sides of the same plays (optional: check the size first, and restrict to skill positions vs DBs and OL vs DL if it's huge).
- [ ] 🤖 **Coaching links:** `COACHED {seasons}` (coach → player, through `PLAYED_FOR` + `HEAD_COACH` / coordinators), `WORKED_WITH` between coaches on the same staff.
- [ ] 🤖 **`nfl history-graph derive`** builds them, idempotently, after `build` / `append`; counts in `build.json` and the W&B run.

### Connections
- [ ] 🤖 **`history_graph/connections.py`:** the shortest path between two people over `TEAMMATE` / `COACHED` / `WORKED_WITH`, with Cypher's `SHORTEST` (bidirectional BFS).
  - **Hub handling:** paths go person → person, never through Team or Season nodes.
  - **Options:** the k shortest (`SHORTEST k`), and "strongest", which weights links by snaps shared (GDS Dijkstra on 1 / snaps).
  - **Evidence per link:** "teammates on 2019 KC, 612 snaps together".
- [ ] 🤖 **API:**
  - `GET /api/ask/people?q=`: fuzzy name search;
  - `GET /api/ask/connections?a=&b=&k=`: ids from the search, validated.
- [ ] 🤖 **Tests:** known pairs (two teammates → 1 link; a known 2-step pair); no path through hubs; time under 1 s on the full graph.

### Graph lab (GDS on the history graph)
- [ ] 🤖 **Player neighbourhoods:** FastRP embeddings on the `TEAMMATE` / `CONNECTION` graph → KNN: "players most like X by who they played with and how". Logged to W&B as a table, and the embedding's 2-D projection (UMAP or PCA) as a chart.
- [ ] 🤖 **Football families:** Louvain or Leiden communities on `TEAMMATE` + coaching links. Name them by their biggest team-era ("2019–2021 KC core"), with sizes and members.
- [ ] 🤖 **Chemistry:** QB → receiver `CONNECTION` ranked by EPA per target with a minimum volume; PageRank on the `CONNECTION` graph for the most central receivers.
- [ ] 🤖 **`nfl history-graph lab [--job neighbours|families|chemistry]`** writes results to `{NFL_DATA_ROOT}/neo4j-history/lab/*.parquet` and logs W&B runs (`job_type: history-graph-lab`).
- [ ] 🤖 **API + page:** `GET /api/ask/lab/{neighbours|families|chemistry}?…`, read from the lab files. The Graph lab section shows each as a small explorable view.

### The path benchmark
- [ ] 🤖 **`nfl history-graph bench-paths`:** on the history graph's person graph (and on samples of 1k, 5k, 20k nodes), time:
  - single pair: Cypher `SHORTEST` and GDS Dijkstra (source-target);
  - single source: GDS Delta-Stepping and scipy `csgraph.dijkstra`;
  - an implementation of Duan et al. 2025 "Breaking the Sorting Barrier" SSSP (arXiv 2504.17033; the public C++ / Rust ports, if their licences allow);
  - all pairs: GDS All Pairs Shortest Path and Floyd–Warshall (numba), on the small samples only.

  Log a W&B run (`job_type: path-benchmark`): time by algorithm and size, a log-log chart.
- [ ] 🤖 **Write-up** in the history-graph guide:
  - what APSP and 3SUM are;
  - what the Alman–Vassilevska Williams paper (arXiv 2610.06783) proved and why it matters to theory (it breaks two central hypotheses; the paper credits Claude with finding the algorithm);
  - why it's "galactic": n^0.0005 savings, huge constants, no code;
  - what our benchmark shows (point-to-point BFS wins at our scale; Duan et al. vs Dijkstra in practice).

### Mockup, verify and close
- [ ] 🤖 **Mockup** of Connections and Graph lab, both themes. ✋ **Rishi approves.**
- [ ] 🤖 Web: the Connections path view (an SVG chain with link evidence), the lab views; tests.
- [ ] 🤖 Codex / Sol review; the weekly-graph check again; production-unchanged check.
- [ ] 🤖 **Docs:**
  - the history-graph guide (derived relationships, Connections, each lab job with real results, the benchmark);
  - the Ask guide (the new parts);
  - the GDS guide (a pointer: the history graph's GDS jobs);
  - the W&B guide (`history-graph-lab`, `path-benchmark`).
- [ ] ✋ **Close AE03**, which closes the core Ask the Engine track (AE04 is optional).

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
