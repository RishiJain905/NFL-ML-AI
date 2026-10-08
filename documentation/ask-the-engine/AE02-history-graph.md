# AE02: The history graph

Status → see [PROGRESS.md](../plans/PROGRESS.md) (Ask the Engine rows)

- **Depends on:** AE00 (the guard, so Ask can read the new graph safely)
- **Unlocks:** AE03 (Connections, graph experiments, the benchmark); Ask's play-level graph questions
- **Read first:**
  - [Ask the Engine README](README.md) §2 (play-level size and load-time estimates), §4 (the second container), §6 (the weekly-graph check);
  - the `neo4j-graph` skill (how the weekly graph is built and tested: this one must not disturb it);
  - [05 Knowledge graph](../05-knowledge-graph.md) (the weekly schema; play-level nodes were deferred there);
  - the `curated-data` skill (participation: research only, 2016–2025; `plays`).

## Goal

A second, permanent Neo4j database, the **history graph**, holding every play of 2018–2025 (with the players on the field from participation) and the current season's plays as they come. It runs in its own container (`nfl-neo4j-history`), started only when wanted, built from scratch once per finished season with a fast bulk import, and appended to weekly for the current season. The weekly digest graph doesn't change in any way.

## Scope

- **In:**
  - the container under a compose profile;
  - `src/nflengine/history_graph/` (export to CSV, bulk import, weekly append, checks);
  - the schema;
  - the CLI;
  - Ask routing to it (read-only, through AE00's guard);
  - the weekly-graph check;
  - the guide.
- **Out:** derived relationships, Connections, GDS experiments, the benchmark (AE03).

## Tasks

### Probe first
- [ ] 🤖 **Sizes:** plays, drives, players, participation links per season 2018–2025, and 2026 so far (2026-10-08: 389,358 play rows 2018–2025, about 48.9k drives, about 8.0M participation links).
- [ ] 🤖 **`neo4j-admin database import full`** in the Neo4j 5.26 image: run it against a stopped, empty database in a throw-away container on a 1-season sample. Time it on the HDD (D:), and note the CSV header format, array properties and the relationship-type column.
- [ ] 🤖 **Memory:** heap and page cache for about 0.6–1 GB of store with both containers up (the weekly one has a 2 GB heap and a 1 GB page cache). Pick settings that leave the PC usable.

### Build
- [ ] 🤖 **`docker-compose.yml`:** a service `neo4j-history` (`container_name: nfl-neo4j-history`) with `profiles: ["history"]`:
  - ports 7475 (Browser) / 7688 (Bolt);
  - volumes under `${NFL_DATA_ROOT}/neo4j-history/` (data, logs, import, plugins);
  - the same image and plugins (GDS, APOC);
  - auth from the same `NEO4J_PASSWORD` variable (no new secret);
  - a smaller heap.

  **The existing `neo4j` service is unchanged**, and `docker compose up -d` (what the weekly run calls) doesn't start the new one: test that.
- [ ] 🤖 **Schema** (`history_graph/schema.cypher`):
  - **Nodes:**
    - `Season`, `Team`, `Player` (gsis_id, name, position), `Coach`;
    - `Game` (game_id, season, week, teams, score);
    - `Drive` (game_id, drive number, result);
    - `Play`: `game_id`, `play_id`, season, week, quarter, clock, down, distance, yards to goal, play type, yards, EPA, success, WP; for 2018–2025 formation, personnel, coverage, man / zone, target route and pressure from participation (labelled research); FTN labels 2022+.
  - **Relationships:**
    - `(Play)-[:IN_DRIVE]->(Drive)-[:IN_GAME]->(Game)`;
    - `(Play)-[:OFFENSE]->(Team)`, `(Play)-[:DEFENSE]->(Team)`;
    - `(Player)-[:ON_FIELD {side, position}]->(Play)` (participation);
    - `(Player)-[:PASSED|RUSHED|TARGETED|CAUGHT|TACKLED|SACKED|INTERCEPTED]->(Play)` (pbp);
    - `(Player)-[:PLAYED_FOR {season, weeks}]->(Team)`;
    - `(Coach)-[:HEAD_COACH|OFF_COORD|DEF_COORD {season}]->(Team)` (the coaching seed).
  - **Constraints:** uniqueness on ids; indexes on `Play(season, week)` and `Player(name)`.
- [ ] 🤖 **`nfl history-graph build --through-season 2025`:**
  - export the CSVs from curated data and research participation (DuckDB, read-only) into `neo4j-history/import/`;
  - stop the container, run `neo4j-admin database import full --overwrite-destination`, start it;
  - check counts against the CSVs;
  - write `{NFL_DATA_ROOT}/neo4j-history/build.json` (seasons, counts, time, versions);
  - log a W&B run (`job_type: history-graph-build`: counts, time by stage).
- [ ] 🤖 **`nfl history-graph append --season 2026 [--through-week W]`:** add the current season's games, drives, plays (no participation until it's published) and pbp player roles with batched `UNWIND MERGE`, idempotent (re-running a week changes nothing). Once a season ends and its participation is published, a full rebuild includes it.
- [ ] 🤖 **`nfl history-graph status`** (up / down, counts, last build and append). `ensure_neo4j`-style start-up helper for the history container only (`docker compose --profile history up -d neo4j-history`), never touching the weekly container.
- [ ] 🤖 **Ask routing:** a third source, `history`. Templates for play-level questions (for example "who has caught the most 3rd-down passes from X", "how often does Y blitz on 3rd & long since 2018"), the guard on its Bolt URL, the source chip "history graph through 2025 + 2026 week N".
- [ ] 🤖 **Tests:**
  - the export's CSVs on a fixture season (headers, ids, counts);
  - the append's idempotence;
  - the profile (the weekly `docker compose up -d` doesn't start the history service, parsed from the compose file, not `docker compose config`, which CLAUDE.md forbids);
  - Ask's history templates through the guard.

### Verify
- [ ] 🧑 **First full build:** Rishi runs (or delegates) `uv run nfl history-graph build --through-season 2025` and opens the Neo4j Browser on 7475 for a look (the guide's walkthrough queries).
- [ ] 🤖 **Weekly-graph check:**
  - rebuild a test week of the weekly graph with the history container up, and again with it down;
  - `graph_results.json`'s candidates and picks are identical;
  - the weekly container's node and relationship counts are unchanged;
  - build time within its usual range.
- [ ] 🤖 **Spot checks:** play counts per season equal curated `plays`; a known QB → WR target total equals the box-score sum.

### Docs and close
- [ ] 🤖 Guide `documentation/guides/history-graph.md`:
  - why a second database (Community's one-database limit, rebuild time);
  - what it holds;
  - starting, stopping, building and appending;
  - a Neo4j Browser walkthrough with real queries and results;
  - disk and memory;
  - how it differs from the weekly graph.
- [ ] 🤖 The `neo4j-graph` skill (a pointer and the "don't touch the weekly graph" rule); `guides/knowledge-graph.md` (a short "the history graph" pointer); the W&B guide (`history-graph-build`); the runbook (the weekly append, if Rishi wants it in the routine).
- [ ] 🤖 Codex / Sol review; production-unchanged check.
- [ ] ✋ **Close AE02.**

## Rishi-in-the-loop moments (what to look for)

- **The first build:** time (target under 15 minutes), counts, and a few Browser queries that feel right, for example one QB's most-targeted receivers in 2024 with the play counts.

## Exit criteria (+ how to verify)

- The history graph is built (2018–2025) and appended (2026 to the newest week), and Ask answers play-level questions from it.
- The weekly-graph check is identical.
- Tests and ruff pass; the production-unchanged check passes.

## Pitfalls / notes

- **Never run `docker compose down -v` or anything that touches the weekly container's volumes.** Commands name the history service explicitly.
- **Participation is research data.** The history graph is a research store: nothing in it feeds the weekly models or the digest.
- **Store growth:** the weekly graph's store bloats across wipes (5.2 GB on 2026-10-08). The history graph avoids it by bulk-importing into a fresh database each rebuild.
- **The HDD is the bottleneck.** Bulk import is the fast path; don't fall back to `UNWIND` for full builds.
