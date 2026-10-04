# P05: Knowledge Graph v1

Status → see [PROGRESS.md](PROGRESS.md)

- **Depends on:** P04 (needs curated data from P01 and model outputs from P02/P03)
- **Unlocks:** P06
- **Read first:** [05 Knowledge graph](../05-knowledge-graph.md) (all of it), [06 → Payload `graph_insights`](../06-weekly-digest.md#payload-json-validated-with-pydantic)

## Goal

The full Neo4j graph (2018 to now) **rebuilt from Parquet every week**, with model outputs written in, a tested library of core **multi-hop** queries, and the digest's *Matchup / risk to watch* and *Non-obvious insights* sections switched on.

## Scope

- **In:** schema + constraints, loaders for every node and relationship in [05](../05-knowledge-graph.md#schema) (coordinator and coaching-tree seed relationships optional / later), model-output nodes, the weekly rebuild command, queries Q1 (revenge), Q2 (injury ripple), Q3 (QB-change ripple), Q4 (common opponents), Q8 (trend mismatch), insight ranking + novelty, golden tests, the digest integration.
- **Out:** Q5–Q7, Q9, GDS (Q10) → P08. The Q&A agent → STRETCH.

## Tasks

### Schema and loading
- [x] 🤖 `graph/schema.cypher` from [05](../05-knowledge-graph.md#constraints-and-indexes-schemacypher); applied idempotently.
- [x] 🤖 `graph/load.py`: build node and relationship tables in Polars from curated Parquet, then batched `UNWIND $rows` writes (5–10k per batch). Order: Team, Venue, Coach, Official, Player, Game → PLAYED_FOR, APPEARED_IN, PLAYED_IN, AT, HEAD_COACH_OF, COACHED_IN, OFFICIATED, THREW_TO, ON_INJURY_REPORT, DEPTH_CHART, DRAFTED_BY, TRADED_TO.
- [x] 🤖 Derived properties: `Player.scramble_rate` (QBs), `Player.position_group`.
- [x] 🤖 Model outputs: `TeamWeek` (+ `HAS_WEEK`, `NEXT` chain), `GamePrediction`.
- [x] 🤖 `nfl graph build --season 2026 --week N`: wipe in batches (or a fresh data folder) → schema → load → outputs → run queries → `graph_results.json` in the run folder. Logs counts and timings to W&B (`track1-graph`).
- [x] 🧑 **Rishi runs** the **first full build** and explores it in Neo4j Browser (suggested queries below). *Delegated (P05 waiver): built twice with identical counts (W&B `zb406f8m`, `pj1ipclt`); Browser queries are in the `neo4j-graph` skill.*

### Query library (v1)
- [x] 🤖 `graph/queries/q1_revenge.cypher`, `q2_injury_ripple.cypher`, `q3_qb_change.cypher`, `q4_common_opponents.cypher`, `q8_trend_mismatch.cypher`. Parameterized; each returns entities, numbers already computed, `insight_type`, `strength`, and sample size.
- [x] 🤖 `graph/insights.py`: run the library, score strength, prefer followed teams, filter by novelty (`PublishedInsight`, 3-week window), pick the top 1 for *Matchup / risk* and the top 1–2 for *Non-obvious insights*.
- [x] 🤖 Write `PublishedInsight` nodes after a digest is published (rebuilt each week from a small `published_insights.parquet` log on D:, so novelty survives rebuilds).

### Digest integration
- [x] 🤖 Payload `graph_insights` builder; placeholder templates for sections 6–7; the checks cover the new sections.
- [x] 🤖 Add `graph` as a named step in `nfl weekly run` (fail-soft: if Neo4j is down, publish without graph sections and show a banner).

### Testing
- [x] 🤖 Load tests: node and relationship counts match what the Parquet sources predict.
- [x] 🤖 **Golden tests** on 3 known past weeks (e.g. a known revenge game, a known QB injury, a known backup-QB start), each asserting the expected entity appears.
- [x] 🤖 Performance: every library query runs in under 2 s on the full graph.
- [x] 🧑 **Rishi reviews** a digest with graph sections (a 2025 backtest week + the current week). *Delegated (P05 waiver): reviewed by the agent plus an opus-high fact-check (2025 week 9, 2026 week 4).*
- [x] ✋ **Checkpoint:** close P05. *Approved by Rishi on 2026-10-04.*

## Rishi-in-the-loop moments: what to look for

Suggested Neo4j Browser exploration after the first build:
- `CALL db.schema.visualization()`: does the schema look like [05](../05-knowledge-graph.md#schema)?
- A QB's passing network: `MATCH (q:Player {name:'<QB>'})-[t:THREW_TO {season:2025}]->(r) RETURN q,t,r`
- A player's career path: `MATCH (p:Player {name:'<player>'})-[r:PLAYED_FOR]->(t) RETURN p,r,t`
- Run Q2 by hand for a recent injury and follow the path visually.
- Check the W&B build run: rebuild time (should be minutes, not tens of minutes, even on the HDD).

## Exit criteria (and how to verify)

| Criterion | Verify |
|---|---|
| Full rebuild works and is repeatable | Two consecutive `nfl graph build` runs give identical counts |
| Queries return sensible, checked results | Golden tests pass for 3 past weeks |
| Graph sections in the digest | The current-week digest includes *Matchup / risk* and *Non-obvious insights*; checks pass |
| Novelty works | The same insight isn't repeated within 3 weeks in a backtest across consecutive weeks |
| Fail-soft | With the Neo4j container stopped, `nfl weekly run` still publishes, with a banner |

## Handoff to P06

- Q2 (injury ripple) and Q3 (QB-change) results are available **as player-model features** (vacated targets, history with the new QB).
- `PlayerProjection` node type ready to fill in.

## Pitfalls / notes

- Batch every write. Never send one transaction per row.
- `MERGE` only on constrained keys; use `CREATE` for relationships in a fresh build (faster).
- Bind-mount performance on the HDD: if the build is slow, profile it first (batch size, indexes), then consider a named Docker volume ([D27](../10-decisions-log.md)).

## As built: deviations from the task list

- **The graph is built as of a key, not just "for week N"** (D58). The same build serves live runs (as of now) and backtest digests (as of their Tuesday), with a visibility rule per data source in `graph/tables.py`. This was needed for the backtest digests' graph sections to be leakage-free; an integration test checks a Tuesday graph holds no week-N results.
- **Golden tests:** the plan's "a known QB injury, a known backup-QB start" became Q2 = a non-QB starter injury (Justin Jefferson on IR, 2023 week 7) and Q3 = a backup-QB start (Jake Browning for Joe Burrow, 2023 week 13), because QB injuries are Q3's by design (D59). The revenge case is Saquon Barkley vs the Giants (2024 week 7).
- **Q2 finds who stepped in from usage data**, with the depth chart's next man as information: chart slots changed format in 2025 (D59).
- **`graph_insights` payload** is richer than doc 06's sketch: code-made headline and facts with owners, people with roles, matchup, sample, note (D60). The digest re-picks with its own run time.
- **Novelty log location:** `runs/published_insights.parquet` (live) and `runs/digest-backtests/published_insights.parquet` (backtests), so backtests never suppress live insights.
- **Fail-soft details** (D61): the weekly step is recorded as `degraded`; the one-restart attempt from doc 02 is left to P07. `nfl weekly run --llm` was added to run the Neo4j-down check with the template writer.
- **Extra commands:** `nfl graph query <name>` (read-only, for exploring) and `nfl digest --graph auto|build|read|off`.
- **Golden / load / performance tests are integration tests** (`-m integration`, Neo4j + data drive, ~10 minutes); the default `pytest` run covers the tables, insights, log and digest sections without Neo4j.
