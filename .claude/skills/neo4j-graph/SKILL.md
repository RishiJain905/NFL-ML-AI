---
name: neo4j-graph
description: How the project's Neo4j knowledge graph is built, queried, tested and debugged (P05+). Use when changing the graph schema, the loaders (`graph/tables.py`, `graph/load.py`), the as-of visibility rules, the query library (`graph/queries/*.cypher`), insight ranking / novelty (`graph/insights.py`, `graph/published.py`), `nfl graph build` / `nfl graph query`, the weekly `graph` step, the graph sections of the digest, or when a graph build is slow, fails, or returns odd insights.
---

# Knowledge graph: build, query, test

The spec is `documentation/05-knowledge-graph.md` (schema, query sketches) plus its "As built in P05" section; decisions D58–D62. Code lives in `src/nflengine/graph/`; the digest side is `src/nflengine/digest/graph_sections.py`. Neo4j runs in Docker (`nfl-neo4j`, 5.26 Community + APOC + GDS), data bind-mounted on D:. Wiping and rebuilding the graph, and starting / stopping the container, need no approval (CLAUDE.md).

## 1. The pipeline (`nfl graph build --season S --week W [--backtest]`)
`graph/build.py::run_graph_build` → `build_graph`:
1. **Inputs + tables** (`graph/tables.py`, pure Polars, ~2 s): `load_inputs(paths, key)` reads curated Parquet + feature tables + the week's `predictions_games.parquet` + the published-insight log; `build_tables(inputs, key)` returns one frame per node label and relationship type. Relationship frames have `s` / `e` (endpoint keys) + properties; rows whose endpoints aren't nodes are dropped and counted (`GraphTables.dropped`; `DRAFTED_BY` drops thousands of pre-2018 players, which is expected).
2. **Wipe** (`CALL (r) { DELETE r } IN TRANSACTIONS`, then nodes), **schema** (`schema.cypher`, idempotent, ~12 s for index awaits), **load** (`UNWIND $rows`, nodes `CREATE ... SET n = r`, relationships `MATCH` both keys + `CREATE ... SET x = r.p`; 5k / 10k per transaction).
3. **Count check**: `graph_counts` must equal `GraphTables.expected_counts()`; a mismatch raises `GraphCountMismatch` before any query runs, so the build is `unavailable` (fail-soft: the weekly step is `degraded`, the digest shows the banner; `nfl graph build` exits 2).
4. **Query library** (`graph/queries/`), **candidates + selection** (`graph/insights.py`), **`graph_results.json`** in the run folder (live `runs/<S>/week<NN>/`, backtest `runs/digest-backtests/<S>/week<NN>/`). W&B: group `track1-graph`, job `build`, live `load/*` curve per table, `count/*`, `time/*`, `query/*`, tables of candidates and picks, and the artifact `graph-results` (type `graph`, `graph_results.json`, alias `<season>-w<NN>` / `-backtest`). The reader-facing explanation is `documentation/guides/knowledge-graph.md`; keep it current with this skill.

Measured (2026 week 4, HDD bind mount): ~15.5k nodes, ~590k relationships, ~90 s total (APPEARED_IN ~23 s, DEPTH_CHART ~22 s). Every library query < 1 s.

## 2. As-of visibility (the leakage rules)
`GraphKey(season, week, run_time, mode)`: live = now; backtest = 14:00 UTC on the Tuesday before the week's first kickoff (`digest.run.tuesday_before`). The table in `graph/tables.py`'s docstring is the contract. In short: results only from completed games strictly before week W; week-W games are nodes without scores; rosters before W; week-W depth charts only if dated by the run date; week-W injury reports only if modified by the run time, and **undated 2025+ reports only in live runs** (D62); trades by the run date; `TeamWeek` up to W. When you add a relationship, add its rule to that table and a test (see `tests/graph/test_graph_tables.py`).

## 3. Schema notes (beyond doc 05)
- Keys: `Team.team_id`, `Player.player_id` (GSIS), `Game.game_id`, `Coach.coach_id` (name slug), `Venue.stadium_id` (resolved with `features.venues.game_travel`, so 2025 international games are at the right stadium), `Official.official_id`; model outputs have one string `key` each (`TeamWeek` "KC:2026:4", `GamePrediction` "game\|version\|variant", `PublishedInsight` "2026-w4:<insight_id>"). `PlayerProjection.key` is constrained, ready for P06.
- `Game.home_qb_expected` / `away_qb_expected` (week-W games): the game model's expected starters, with `home_qb_source` / `away_qb_source`. Only `schedule`, `depth_chart`, `injury_next` (a live run) confirm a starter; a Tuesday-rule pick (`last_game` ...) is "could start, not confirmed" in Q3's text (every backtest QB change is unconfirmed, as it would have been on that Tuesday).
- `PLAYED_FOR {season, first_week, last_week, weeks, roster_weeks, status_last, games}`: `roster_weeks` is the actual list (a stint away leaves a gap); `status_last = 'RES'` = on a reserve list at the latest roster week.
- `APPEARED_IN`: box score full-joined with snaps (`off_snaps`, `def_snaps`, `st_snaps`, `snaps` = off + def and **null when there's no snap record**, `snap_pct` = max of the two pcts), `pressures` lagged a week in backtests (PFR is late), `epa`, `pressures` (PFR), `qb_started` (most dropbacks for his team), `dropbacks`, NGS `ngs_separation` / `ngs_time_to_throw` / `ngs_ryoe`.
- `PLAYED_IN {home, points, points_allowed, margin, result, epa_per_play, epa_allowed, epa_margin, success_rate, plays, penalties}`; week-W rows exist (so queries find this week's opponent) with null results.
- `DEPTH_CHART` is per (player, team, season, week, position), offense and defense slots only. **Slot formats differ**: ≤2024 has three rank-1 `WR` rows (one per slot), 2025+ one ranked `WR` list (1..8). Don't infer "the backup" from rank + 1 at WR / CB; Q2 uses usage data instead.

## 4. Query library (`graph/queries/*.cypher`)
Each file takes `$season`, `$week` (+ the extras in `LIBRARY`) and returns rows with the entities, numbers already computed, `insight_type`, `strength` in [0, 1] and `sample_size`. Run one by hand: `uv run nfl graph query q2_injury_ripple --season 2026 --week 4` (read transaction). Definitions are D59. Neo4j 5.26 syntax used: `CALL (x) { ... }` scoped subqueries, `OPTIONAL CALL`, `EXISTS { MATCH ... }`; old `CALL { WITH x ... }` prints deprecation notices.

**Adding a query:** a `.cypher` file + its `LIBRARY` entry (extra params) + a converter in `insights.py::CONVERTERS` (code-made headline + facts with owners, a stable `insight_id`, `sample`, `confidence`) + its section in `SECTION_TYPES` + tests (converter wording; a golden week in `tests/graph/test_graph_integration.py`). Keep queries read-only.

## 5. Insights → digest
- `insights.candidates` turns rows into `GraphInsight` payload items; `insights.select` scores (+0.1 followed team), skips started games, novelty (same `insight_id` in the 3 earlier weeks of the season) and a player already used, then picks 1 for `matchup_risk` and 1–2 for `non_obvious` (D60).
- **Meaning stays in code** (D53 lesson): facts are complete, time-scoped, self-describing phrases ("... the 49ers defense allowed +0.11 EPA per play ... : worse without him"). Never put a bare number, sign or comparison in a fact for the LLM to interpret. Numbers come from `digest/format.py` (`F.epa`, `F.per_game`, `F.share`, `F.epa_change`).
- Novelty's source of truth is `published_insights.parquet` (`graph/published.py`). It only grows: a re-run adds its picks and never erases one already published. The digest logs only the picks its final prose names (`graph_sections.rendered`), after publishing; live runs also MERGE them as `PublishedInsight` nodes (backtests don't touch the live graph). Every rebuild reloads the log's earlier weeks as nodes.
- A live digest skips games kicking off within 90 minutes (`LIVE_KICKOFF_MARGIN`): GLM can take 20+ minutes.
- **Say what the uncertainty is about.** A low-confidence item's `note` names the uncertain claim (Q3: the new QB's career targets to these receivers); the prompt puts the hedge there, never on the headline (the P05 fact-check found GLM hedging a well-supported QB start). Say *why* when the data has it (the main starter listed Out), and name a snap share's side (offense / defense).
- Ownership: one claim per fact clause; a receiver's targets fact is "X has 20 targets from A this season; he has 12 from B since 2018", owned by A and B (each owns only the clause naming him), not by the receiver.
- The digest (`digest/graph_sections.py`) re-picks from the stored candidates with its own run time; `--graph auto|build|read|off`.

## 6. Fail-soft
`run_graph_build(..., fail_soft=True)` (weekly step, digest, `nfl graph build`) never raises: it writes `{"status": "unavailable", "error": "<Type>: <short reason>"}`. Never write a driver's exception text anywhere (files, logs, W&B; it can carry server text): `graph.client.safe_error` keeps the type plus a fixed reason or the Neo4j status code (`Neo.ClientError...`), and `run_query` uses it too (a test injects a sentinel). The weekly `graph` step then raises `StepDegraded` (recorded as `degraded`, the run continues) and the digest shows the ℹ️ banner. Check by hand: `docker compose stop neo4j`, `uv run nfl weekly run --season S --week W --from-step graph --llm placeholder`, then `docker compose start neo4j` and wait for "Started." in `docker compose logs neo4j`.

## 7. Tests
- Unit (no Neo4j, ~330 tests): a 4-team synthetic league in `tests/graph/graph_world.py` (a plain module, not a conftest: a second `conftest` would shadow `tests/conftest.py`'s helpers) feeds `test_graph_tables.py` (visibility / leakage per relationship, future invariance), `test_graph_builders.py`, `test_graph_insights.py`, `test_graph_published.py`, `test_graph_load_helpers.py`, `test_graph_queries.py` (a `FakeDriver`) and `test_graph_review_fixes.py` (the Sol-review regressions). The digest side is `tests/digest/test_graph_sections.py`.
- Integration (`uv run pytest -m integration tests/graph/test_graph_integration.py`, ~10 min): golden weeks (Saquon Barkley vs the Giants, 2024 w7; Justin Jefferson on IR, 2023 w7; Jake Browning for Burrow, 2023 w13), no week-N results in a Tuesday graph, counts = tables and repeatable, every query < 2 s, fail-soft with an unreachable driver. Each golden week rebuilds the whole graph; the module rebuilds the live week at the end. Don't run it while someone else is using the graph.

## 8. Neo4j Browser (http://localhost:7474)
```cypher
CALL db.schema.visualization()
MATCH (q:Player {name:'Patrick Mahomes'})-[t:THREW_TO {season:2025}]->(r) RETURN q,t,r
MATCH (p:Player {name:'Saquon Barkley'})-[r:PLAYED_FOR]->(t) RETURN p,r,t
MATCH (t:Team {team_id:'SF'})-[:HAS_WEEK]->(tw:TeamWeek {season:2026}) RETURN tw ORDER BY tw.week
MATCH (pi:PublishedInsight) RETURN pi ORDER BY pi.season DESC, pi.week DESC LIMIT 20
```

## 9. Quirks found in P05 (checked live 2026-10-03)
- `officials.game_id` is the old GSIS id (`2015091000`): join on `games.old_game_id` (16,154 of 16,182 rows since 2018 match).
- `injuries.date_modified` is null for every 2025+ row (D62).
- `trades` rows for traded **draft picks** carry the eventual draftee's `pfr_id` (`pick_season` set): exclude them from player trades (2,737 of 3,943 rows with a `pfr_id`).
- Depth-chart slot formats (see §3).
- Neo4j's `SET n = map` skips null values, so absent properties read as null in Cypher; `coalesce` where it matters.

## Improving this skill
Add new quirks, query patterns and performance findings here in the same commit as the change, and note it in the `PROGRESS.md` session log. Keep it procedural; the spec stays in documentation/05.
