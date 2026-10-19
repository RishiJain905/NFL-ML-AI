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
- [x] 🤖 **Sizes:** plays, drives, players, participation links per season 2018–2025, and 2026 so far (2026-10-08: 389,358 play rows 2018–2025, about 48.9k drives, about 8.0M participation links). **2026-10-18:** 377,937 rows with a play type (361,470 once timeouts, `no_play` rows without an offense, are left out), 48,712 drives, 2,227 games, 7,999,769 participation player slots (7,955,985 `ON_FIELD` links after the join), ~760k pbp role ids; 2026 weeks 1–5: 12,849 plays, 1,715 drives.
- [x] 🤖 **`neo4j-admin database import full`** in the Neo4j 5.26 image: run it against a stopped, empty database in a throw-away container on a 1-season sample. Time it on the HDD (D:), and note the CSV header format, array properties and the relationship-type column. **Done:** 2024 (48k plays, 1.0M `ON_FIELD`): the relationship stage 2 m 43 s onto the D: bind mount (1 m 28 s with `--high-parallel-io=off`), 13.6 s onto the SSD, **3.6 s into a Docker volume** → the build imports into a volume and copies the store to D: (D145). Headers `key:ID(Label)`, `prop:long|double|boolean|string|long[]` (int is normalised to long), `:START_ID(Label)` / `:END_ID(Label)`, the type given per file (`--relationships=TYPE=file`), arrays `;`-separated; the database name must come before the variadic options.
- [x] 🤖 **Memory:** heap and page cache for about 0.6–1 GB of store with both containers up (the weekly one has a 2 GB heap and a 1 GB page cache). Pick settings that leave the PC usable. **Chosen:** heap 512 MB–1 GB, page cache 1.5 GB (the store is 1.1 GB); measured with both up: 1.8 GB (history) + 4.2 GB (weekly), 8.7 GB of 31 GB free.

### Build
- [x] 🤖 **`docker-compose.yml`:** a service `neo4j-history` (`container_name: nfl-neo4j-history`) with `profiles: ["history"]`:
  - ports 7475 (Browser) / 7688 (Bolt);
  - volumes under `${NFL_DATA_ROOT}/neo4j-history/` (data, logs, import, plugins);
  - the same image and plugins (GDS, APOC);
  - auth from the same `NEO4J_PASSWORD` variable (no new secret);
  - a smaller heap.

  **The existing `neo4j` service is unchanged**, and `docker compose up -d` (what the weekly run calls) doesn't start the new one: test that.
- [x] 🤖 **Schema** (`history_graph/schema.cypher`):
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
- [x] 🤖 **`nfl history-graph build --through-season 2025`:**
  - export the CSVs from curated data and research participation (DuckDB, read-only) into `neo4j-history/import/`;
  - stop the container, run `neo4j-admin database import full --overwrite-destination`, start it;
  - check counts against the CSVs;
  - write `{NFL_DATA_ROOT}/neo4j-history/build.json` (seasons, counts, time, versions);
  - log a W&B run (`job_type: history-graph-build`: counts, time by stage).
- [x] 🤖 **`nfl history-graph append --season 2026 [--through-week W]`:** add the current season's games, drives, plays (no participation until it's published) and pbp player roles with batched `UNWIND MERGE`, idempotent (re-running a week changes nothing). Once a season ends and its participation is published, a full rebuild includes it.
- [x] 🤖 **`nfl history-graph status`** (up / down, counts, last build and append). `ensure_neo4j`-style start-up helper for the history container only (`docker compose --profile history up -d neo4j-history`), never touching the weekly container.
- [x] 🤖 **Ask routing:** a third source, `history`. Templates for play-level questions (for example "who has caught the most 3rd-down passes from X", "how often does Y blitz on 3rd & long since 2018"), the guard on its Bolt URL, the source chip "history graph through 2025 + 2026 week N".
- [x] 🤖 **Tests:**
  - the export's CSVs on a fixture season (headers, ids, counts);
  - the append's idempotence;
  - the profile (the weekly `docker compose up -d` doesn't start the history service, parsed from the compose file, not `docker compose config`, which CLAUDE.md forbids);
  - Ask's history templates through the guard.

### Verify
- [x] 🧑 **First full build:** Rishi runs (or delegates) `uv run nfl history-graph build --through-season 2025` and opens the Neo4j Browser on 7475 for a look (the guide's walkthrough queries). **Delegated at the kickoff ("agent runs it"):** 2026-10-18, 355 s ([x27v679p](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/x27v679p)); the builds after the fixes 2026-10-19, 253 s ([bz131yeb](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/bz131yeb)) and, with FTN's flags, 386 s ([sx0sl85u](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/sx0sl85u)); the walkthrough queries run and written into the guide §8. Rishi can look at 7475 whenever he likes.
- [x] 🤖 **Weekly-graph check:**
  - rebuild a test week of the weekly graph with the history container up, and again with it down;
  - `graph_results.json`'s candidates and picks are identical;
  - the weekly container's node and relationship counts are unchanged;
  - build time within its usual range.
- [x] 🤖 **Spot checks:** play counts per season equal curated `plays`; a known QB → WR target total equals the box-score sum.

### Docs and close
- [x] 🤖 Guide `documentation/guides/history-graph.md`:
  - why a second database (Community's one-database limit, rebuild time);
  - what it holds;
  - starting, stopping, building and appending;
  - a Neo4j Browser walkthrough with real queries and results;
  - disk and memory;
  - how it differs from the weekly graph.
- [x] 🤖 The `neo4j-graph` skill (a pointer and the "don't touch the weekly graph" rule); `guides/knowledge-graph.md` (a short "the history graph" pointer); the W&B guide (`history-graph-build`); the runbook (the weekly append, if Rishi wants it in the routine).
- [x] 🤖 Codex / Sol review; production-unchanged check.
- [x] ✋ **Close AE02.** (Waived at the kickoff: "marking the phase complete".)

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

## As built: deviations from the task list (2026-10-18 to 19; D145, D146)

- **The bulk import goes through a Docker volume, not into the stopped container's own folder.** The probe (task 2) found the relationship stage 12–45x slower onto the HDD bind mount (one season: 2 m 43 s, 1 m 28 s with `--high-parallel-io=off`) than onto the SSD (13.6 s) or into a Docker volume (3.6 s). So `neo4j-admin database import full` writes into the volume `nfl-neo4j-history-staging` from a throw-away container (`--entrypoint neo4j-admin`, no environment), and the finished store is copied onto D: while the history container is stopped (~75 s for 1.1 GB). The store still lives on D: (`neo4j-history/data/`). First full build: **355 s**; the later ones (after the fixes) **253–386 s** (target: under 15 minutes).
- **What a Play is:** a `plays` row with a `play_type` and a `posteam` (timeouts are `no_play` rows without one): 361,470 plays 2018–2025 (the README's 389,358 counted every row).
- **More than the schema task listed:** `Season` linked by `IN_SEASON`, `HOME_TEAM` / `AWAY_TEAM`, `ST_COORD` (the seed has special-teams coordinators), `Play` properties beyond the list (`seconds_left`, `score_diff`, `description`, `pass_attempt`, `air_yards`, `run_gap` ...), indexes also on `Play(season)`, `Play(offense)`, `Play(defense)`, `Game(season, week)`, `Drive(game_id)`, `Drive(season)`. `SACKED` is the defender who made the sack (`half` for a shared one), `TACKLED {kind}`. Roles exist only on plays that count in the box score. `ON_FIELD.position` is per play from 2023 and the roster position before (participation has no per-play positions until 2023).
- **The append syncs instead of adding** (`append.py`): it reads the season's part of the graph and writes only the difference, so a re-run writes nothing (live: 12,849 writes, then 0 in 7 s), corrections and removed plays are applied, and `--through-week W` leaves exactly weeks 1..W. A season in the bulk import is refused. `build` appends the current season at its end; a failed append there doesn't fail the build.
- **Locks:** build and append take `neo4j-history/.history.lock` and refuse while the weekly lock is held; Ask's history questions refuse while the history lock is held.
- **The container:** `restart: "no"`, `stop_grace_period: 2m` (compose's default 10 s stop SIGKILLed Neo4j in the first full build), Bolt / HTTP advertised on 7688 / 7475 (so the Browser on 7475 can't land on the weekly graph), the anonymous usage report off; heap 512 MB–1 GB, page cache 1.5 GB. `history_graph.config` refuses settings that point at the weekly service, container, volume or ports.
- **Extra commands:** `nfl history-graph start` / `stop` (the task named a start-up helper); `nfl history-graph append` also logs a W&B run (`history-graph-append`).
- **Ask's label** is "history graph 2018-2025 + 2026 week 5" (the task said "through 2025 + 2026 week N").
- **Spot checks found two definition fixes** before the final build: spikes count as pass attempts (the box score's rule: the first build was short exactly 2024's 75 spikes), and a whitespace GSIS id in `rosters_weekly` broke the first smoke build (ids are now stripped, blanks dropped).
- **Review fixes (D146):** a durable marker (`neo4j-history/.history.unfinished`) is written before a build replaces the store and before an append's first write, and removed only after the result is verified; while it's there Ask refuses history questions and `append` refuses after an unfinished build (Sol: a crash or a failed batch left a half-written graph that Ask would have read). `start`, `stop` and the append's own start take the history lock. The eval probes Bolt once per run and skips the history questions when it's down. A rewind's `PLAYED_FOR.games` counts only the weeks kept. A non-Neo4j failure is no longer called "Neo4j unreachable".
- **The weekly-graph check** (task "Weekly-graph check"): 2026 week 6 rebuilt at its published run time with the history container up (205 s) and down (145 s): identical counts, candidates and picks, all equal to the published file (unchanged, same SHA-256); the PC's reboot during the second run was recovered (the weekly graph replayed its logs, week 6 rebuilt and its projections and published insights put back).
- **Ask with history on:** main 97.1% (history 17 of 17), held-out 85.3%, 12 fresh play-level questions 10 right + 1 with the fallback sentence + 1 missed tie (the Ask guide §15.4); FTN's `qb_sneak`, `drop`, `throw_away`, `out_of_pocket` added after the first eval run, and one rule for what a snap is.
