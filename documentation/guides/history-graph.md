# History graph guide (every play since 2018, in its own Neo4j)

**What this is:** a plain-language guide to the **history graph**: a second, permanent Neo4j database that holds every NFL play since 2018 with the players on the field, the drives, the games, who passed, ran, caught and tackled, rosters and coaches. It's separate from the weekly knowledge graph (the one the digest uses), which it never touches. Built in AE02. AE03 added **person links** (who played with, coached, worked with or faced whom, §15), **Connections** ("six degrees" between any two players or coaches, §16), the **Graph lab** (player neighbourhoods, football families, QB → receiver chemistry, §17) and a **shortest-path benchmark** with a plain explanation of 2026's sub-cubic all-pairs result (§18).

**Related docs:** the spec is [Ask the Engine README](../ask-the-engine/README.md) (§4, §6, §8 "As built in AE02 / AE03") and the phase files [AE02](../ask-the-engine/AE02-history-graph.md) and [AE03](../ask-the-engine/AE03-connections-and-graph-ml.md); decisions D112, D145–D146 and D147–D151 in the [decisions log](../10-decisions-log.md). The control room's pages for it: [control-room.md](control-room.md) §2g. Graph Data Science in general: [graph-data-science.md](graph-data-science.md). The weekly graph has its own guide: [knowledge-graph.md](knowledge-graph.md). Asking it questions in plain English: [ask-the-engine.md](ask-the-engine.md) §15. W&B runs: [weights-and-biases.md](weights-and-biases.md) §6.12. Agents use the `neo4j-graph` and `ask-the-engine` skills.

---

## 1. Why a second database

The weekly graph answers "what matters about this week's games" and is **wiped and rebuilt every Tuesday** in about 1.5–3 minutes (D58): 22k nodes, 600k relationships, a few seasons of season-level and game-level facts. Play-level history is a different animal:

- **Size.** 2018–2025 is 377,937 plays and **~8 million player-on-play links** (who was on the field, from nflverse participation). That's ~10 million relationships: 15–20 times the weekly graph.
- **Rebuild time.** Loading that with the weekly build's method (`UNWIND` batches over Bolt, ~10k relationships a second on the HDD) would take 15–20 minutes **every Tuesday** and delay the digest. The history graph changes only once a season (plus a small weekly append), so it should be built **once**, with Neo4j's fast offline bulk importer.
- **One database per server.** Neo4j Community (what we run) allows **one user database per server**. A second database therefore needs a second server: its own Docker container, `nfl-neo4j-history`, with its own ports and its own folder on D:. The weekly container and its data are never touched (D107, D112).

So the history graph is a **research store**: built from scratch per finished season, appended to for the current season, read by Ask the Engine and (from AE03) by graph experiments. Nothing in it feeds the weekly models or the digest.

## 2. How it runs here

| Thing | Value |
|---|---|
| Software | Neo4j 5.26 Community in Docker, container **`nfl-neo4j-history`**, compose service `neo4j-history` under the compose **profile `history`**, with APOC and Graph Data Science (for AE03) |
| Where the data lives | `D:/nfl-ml-data/neo4j-history/` (`data/`, `logs/`, `import/` = the bulk import's CSVs, `plugins/`, `build.json`, `append.json`) |
| Ports | **7475** = Neo4j Browser, **7688** = Bolt (the weekly graph is 7474 / 7687) |
| Login | user `neo4j`, the same `NEO4J_PASSWORD` as the weekly graph (no new secret) |
| Memory | heap 512 MB–1 GB, page cache 1.5 GB (the weekly container: heap up to 2 GB, page cache 1 GB) |
| Starts | **only when asked**: `restart: "no"`, and the profile keeps it out of a bare `docker compose up -d` (what the weekly run calls) |

**Why the profile matters.** The weekly run starts Neo4j with `docker compose up -d`. Compose starts every service that has **no** profile and skips the ones that do, so the weekly run can never start (or stop, or recreate) the history container by accident. The history commands always name the service with its profile: `docker compose --profile history up -d neo4j-history`. Nothing here ever runs `docker compose down`.

**The Browser points at the right server.** The history container advertises its Bolt address as `localhost:7688`, so the Browser opened on 7475 connects to the history graph, not to the weekly one on 7687.

## 3. Operating it

| Task | Command | Notes |
|---|---|---|
| Start | `uv run nfl history-graph start` | Starts only `nfl-neo4j-history`, waits until Bolt answers |
| Stop | `uv run nfl history-graph stop` | Frees its memory; the data stays on D: |
| What's in it | `uv run nfl history-graph status` | Container, Bolt, the last build and append, the counts in the graph, the store's size, Ask's "as of" label |
| Build from scratch | `uv run nfl history-graph build --through-season 2025` | Once per finished season, at the pre-season switch (it takes only seasons before `seasons.current`; by then nflverse has published the finished season's participation), or after a schema change. Then appends the current season |
| Append the current season | `uv run nfl history-graph append` | **Tuesdays after the weekly run** (runbook). Starts the container if it's down; then syncs the person links (`--no-derive` skips that) |
| Person links | `uv run nfl history-graph derive` | AE03 (§15): TEAMMATE, CONNECTION, OPPONENT, COACHED, WORKED_WITH. `build` and `append` run it at their end; run it alone after one failed. A re-run writes nothing |
| Graph lab | `uv run nfl history-graph lab [--job neighbours,families,chemistry]` | AE03 (§17): the three GDS experiments, files in `neo4j-history/lab/`, a W&B run each. Run it after a build, or when you want this week's appended season in the lab views (seconds) |
| Path benchmark | `uv run nfl history-graph bench-paths` | AE03 (§18): times shortest-path algorithms on the person graph; minutes; a W&B run |
| Look at it | http://localhost:7475 | Connect URL `bolt://localhost:7688`, user `neo4j`, the password from your env file |

`build`, `append`, `derive`, `start` and `stop` take the history graph's own lock (`neo4j-history/.history.lock`), so none of them runs while another does; `build` and `append` also refuse while the **weekly** run holds its lock (Tuesday's run shares the disk and has a deadline). Ask's history questions refuse while a build or an append runs.

**If a build or an append stops halfway** (an error, a crash, a reboot), the graph may be half-written. Before a build replaces the store, and before an append's first write, a marker file is written (`neo4j-history/.history.unfinished`, with what was running, the season and the build); it's removed only once the result has been checked. While it's there, Ask doesn't read the history graph ("the last build or append didn't finish"), `nfl history-graph status` says **unfinished** and what to run, `append` refuses after an unfinished build, and `start` refuses too (a build that died mid-copy may have left the store half-copied). The fix is to run the same thing again: `uv run nfl history-graph build` for a build, `uv run nfl history-graph append --season <S>` for an append (only the same season's append on the same build clears it).

## 4. What it holds

### Nodes

| Label | Key | What one is | Main properties |
|---|---|---|---|
| `Season` | `season` | 2018, 2019, ... | |
| `Team` | `team_id` | the 32 franchises (canonical codes: `LV`, `LAC`, `LA`) | `name`, `nickname`, `conference`, `division` |
| `Player` | `player_id` (GSIS id) | anyone who was on a roster, on the field or in a play's box score since 2018 | `name` (full name), `position`, `position_group` |
| `Coach` | `coach_id` (a name slug: `andy-reid`) | head coaches (from the schedules, with `config/head_coach_fixes.csv`) and coordinators (the coaching seed) | `name` |
| `Game` | `game_id` | a game with plays | `season`, `week`, `game_type` (REG / WC / DIV / CON / SB), `gameday`, `home_team`, `away_team`, `home_score`, `away_score`, `overtime`, `stadium`, `roof`, `home_coach`, `away_coach` |
| `Drive` | `drive_key` = `<game_id>:<drive>` | a possession (nflverse `fixed_drive`) | `team` (the offense), `result` (Touchdown, Field goal, Punt, Turnover, Turnover on downs, Missed field goal, End of half, Opp touchdown, Safety), `plays`, `first_downs`, `quarter`, `start` / `end` ("KC 25"), `time_of_possession`, `scored` |
| `Play` | `play_key` = `<game_id>:<play_id>` | every snap with a play type and an offense: runs, passes, kicks, punts, kneels, spikes and `no_play` penalty snaps (timeouts and quarter markers aren't plays) | see below |

**A Play's properties** (`history_graph/schema.py` has the full list with comments):
- **Situation:** `season`, `week`, `season_type` (REG / POST), `quarter` (5 = overtime), `clock` ("15:00"), `seconds_left`, `offense`, `defense` (team codes; on a kickoff the offense is the receiving team, as in nflverse), `down`, `distance`, `yards_to_goal`, `goal_to_go`, `score_diff` (offense minus defense), `wp` (the offense's win probability).
- **Result:** `play_type`, `description` (the gamebook text), `yards`, `epa`, `success`, `first_down`, `touchdown`, `penalty`, `two_point`, `dropback`, `pass_attempt`, `sack`, `scramble`, `complete`, `interception`, `fumble_lost`, `air_yards`, `yards_after_catch`, `pass_length`, `pass_location`, `run_location`, `run_gap`, `shotgun`, `no_huddle`.
- **FTN charting (2022 on):** `ftn` (FTN charted it), `play_action`, `screen`, `rpo`, `motion`, `blitzers`, `rushers`, `blitz`, `box`, `qb_alignment`, `qb_sneak`, `drop` (the targeted receiver dropped a catchable ball), `throw_away`, `out_of_pocket` (the QB). FTN's placeholder rows for plays it didn't chart are left out (the Play calling rule, PC00).
- **Participation (research only, 2018–2025):** `part` (participation has the play), `formation`, `personnel` ("11" = one back, one tight end), `extra_ol`, `def_package` (base / nickel / dime), `coverage`, `man_zone`, `target_route`, `pressure`, `time_to_throw`, `part_box`, `part_rushers`. These come from nflverse participation, which is published after each season: the current season has none until the rebuild after it ends.

### Relationships

| Type | From → to | Properties | What it says |
|---|---|---|---|
| `IN_DRIVE` | Play → Drive | | the play's possession |
| `IN_GAME` | Drive → Game | | |
| `IN_SEASON` | Game → Season | | |
| `HOME_TEAM`, `AWAY_TEAM` | Game → Team | | |
| `OFFENSE`, `DEFENSE` | Play → Team | | same as the play's `offense` / `defense` properties |
| `ON_FIELD` | Player → Play | `side` (offense / defense), `position` | **every player on the field** for the snap (participation, 2018–2025), special teams and `no_play` snaps included: a snap is any play with an `ON_FIELD` link (filter to passes and runs only when you mean plays). `position` is the player's position on that play from 2023 (FTN era); for 2018–2022 participation has no per-play positions, so it's his roster position that week |
| `PASSED` | Player → Play | | the passer on a dropback that wasn't a scramble: attempts **and sacks** (`Play.sack`; attempts are `Play.pass_attempt`) |
| `RUSHED` | Player → Play | | the ball carrier: designed runs, scrambles, kneels |
| `TARGETED`, `CAUGHT` | Player → Play | | the intended receiver of a pass attempt; a completed one |
| `INTERCEPTED` | Player → Play | | the defender who intercepted |
| `SACKED` | Player → Play | `half` | the defender who **made** the sack (`half` for a shared one) |
| `TACKLED` | Player → Play | `kind` (solo / assist / with_assist) | |
| `PLAYED_FOR` | Player → Team | `season`, `weeks`, `first_week`, `last_week`, `games`, `status_last` | roster weeks (practice squad and reserve too; cut, retired and traded-away rows left out, the weekly graph's rule); `games` = box-score games for that team |
| `HEAD_COACH` | Coach → Team | `season`, `games`, `first_week`, `last_week` | |
| `OFF_COORD`, `DEF_COORD`, `ST_COORD` | Coach → Team | `season` | the coaching seed (D86) |

**The role relationships count like the box score.** `PASSED`, `RUSHED`, `TARGETED`, `CAUGHT`, `INTERCEPTED`, `SACKED` and `TACKLED` exist only on plays that count in the box score: never on a `no_play` (a penalty wiped it out) or a two-point try. So `count()` of a role is the box-score total (§7 checks it). `ON_FIELD` is on every snap, `no_play` included: a snap is a snap.

## 5. How a build works (`nfl history-graph build`)

A build makes the graph **from scratch**. Neo4j's `neo4j-admin database import full` is an offline importer: it reads CSV files and writes the database files directly, without transactions, many times faster than loading over Bolt. It needs an empty, stopped database, which is one more reason the history graph has its own server.

| Stage | What happens | 2018–2025 (2026-10-18) |
|---|---|---|
| 1. export | The curated tables and the research participation files are read (read-only) and turned into one table per label and relationship type (`history_graph/export.py`). Relationships whose ends aren't nodes are dropped and counted | 6 s |
| 2. csv | Those tables are written to `neo4j-history/import/*.csv` with the importer's headers (`play_key:ID(Play)`, `season:long`, `:START_ID(Player)` ...) | 1 s (549 MB) |
| 3. import | `neo4j-admin` runs in a throw-away container and writes the new database into a **Docker volume** (`nfl-neo4j-history-staging`, on the SSD) | 75 s (mostly the container starting; the import itself ~1 min) |
| 4. stop | the history container stops (only it; Neo4j gets up to 2 minutes to close cleanly) | 0–8 s |
| 5. install | a second throw-away container replaces the old store in `neo4j-history/data/` with the new one (a straight copy), then the volume is removed | 34–74 s |
| 6. start | the history container starts and Bolt answers | 77–131 s |
| 7. schema | `schema.cypher`: uniqueness constraints on every key, indexes on `Play(season, week)`, `Play(season)`, `Play(offense)`, `Play(defense)`, `Player(name)`, `Game(season, week)`, `Drive(game_id)`, `Drive(season)` (the importer makes none) | 22–31 s |
| 8. check | every label's and relationship type's count in Neo4j must equal the export's; a mismatch fails the build | 1 s |
| 9. append | the current season, through its newest week (§6) | 18–20 s |
| 10. derive | the person links (§15): every one is new after a bulk import, so ~663k writes | ~150 s |
| **total** | | **253–386 s** (the builds of 2026-10-19, swinging with Docker's load; the first, 2026-10-18: 355 s) |

**Why the Docker volume (D145).** The D: drive is a spinning disk shared into Docker through Windows file sharing, and the importer writes the relationship store in many small random writes. Measured on one season (1.0 million relationships): the relationship stage took **2 min 43 s** straight onto D:, 1 min 28 s with the importer's HDD setting, **13.6 s** onto the SSD and **3.6 s** into a Docker volume. Copying the finished store to D: is one long sequential write (~75 s for 1.1 GB), so the store still lives on D: (CLAUDE.md: data on D:) and the build stays well under the 15-minute target.

**What `build.json` records:** the build id (its UTC time), the seasons, every count in Neo4j and in the export, rows dropped (`participation_plays_not_in_pbp`: 53 participation rows for plays without a type and an offense), the CSVs' and the store's size, each stage's seconds, Neo4j's / APOC's / GDS's versions and the image, the append's season and week, and the W&B run. A build that fails before the store is replaced leaves the old graph and `build.json` as they were and writes `build_failed.json`; one that fails after it marks `build.json` failed (Ask's history source then stays off until a build succeeds).

## 6. The weekly append (`nfl history-graph append`)

Participation for a season is published only after it ends, so the current season can't be bulk-imported until February. The append writes it week by week, with batched `UNWIND` over Bolt (fine at this size: ~13k plays by week 5, ~50k by the Super Bowl).

**It syncs, it doesn't add.** The append reads what the graph holds for the season (its games, drives and plays, the relationships they own, the season's rosters and coaches), compares it with the same export the bulk import uses, and writes only the difference: new plays, changed ones (nflverse corrects plays for days after a game), and plays that no longer exist. So:

- **Re-running it writes nothing** (checked live: 12,849 writes on the first run, **0 writes in 7 s** on the second).
- After `--through-week W` the season holds exactly weeks 1..W.
- A season that's in the bulk import (2025 and earlier) is refused: rebuild instead.
- 2026's plays have no participation (no `ON_FIELD`, formation, personnel, coverage, routes) until the rebuild after the season.

Run it on Tuesdays after the weekly run (runbook). It starts the history container if it's down, refuses while the weekly lock is held, and writes `append.json` (the season, the weeks, the counts, what changed, the time) and a W&B run. Then it syncs the person links (§15): the week's new QB → receiver targets and this season's coaching links; `append.json` gets a `derive` section and the W&B run its counts.

## 7. How we know it's right

Every build checks itself (stage 8: Neo4j's count of every label and relationship type equals the export's). On top of that, the first build (2026-10-18) was checked against the curated tables:

- **Plays per season** equal the curated `plays` rows with a type and an offense, every season: 2018 43,711 · 2019 43,960 · 2020 44,364 · 2021 46,319 · 2022 45,851 · 2023 46,160 · 2024 45,919 · 2025 45,186 · 2026 (weeks 1–5) 12,849.
- **The roles equal the box score** (`player_games`, 2024, every player, playoffs included): targets (497 players, 17,748), receptions (472, 12,136), sacks (455, 1,382 with halves), interceptions (230, 405) equal for every player. Pass attempts equal once spikes count as attempts, as the box score counts them (the first build missed exactly 2024's 75 spikes; fixed before the final build). Carries are off by one for one player of 335 and tackles (all kinds) by one for three of ~1,400: a 2024 fake punt (`2024_15_MIA_HOU`, play 2274) that nflverse marks `no_play` (a replay reversal plus a penalty) but still counts in the box score, and two players for whom nflverse's own box score and play-by-play disagree by a tackle.
- **A known QB → receiver total:** Patrick Mahomes → Travis Kelce in 2024 is 151 targets and 110 catches in the graph, and the same in the play-by-play.
- **The append is idempotent:** a second run on the same data writes nothing (§6).
- **The weekly graph didn't notice** (§9).

## 8. A walk through it in Neo4j Browser

Start it (`uv run nfl history-graph start`), open **http://localhost:7475**, connect to `bolt://localhost:7688` as `neo4j` with the password from your env file. Every query below was run on the first full build (2026-10-19); times are the first (cold) run and a second (warm) one.

**What's in it** (0.15 s cold, 0.04 s warm):

```cypher
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS nodes ORDER BY nodes DESC
```

Play 374,319 · Drive 50,427 · Player 7,091 · Game 2,306 · Coach 298 · Team 32 · Season 9.

**One QB's most-targeted receivers in 2024** (the phase's "feels right" query; 0.78 s cold, 0.01 s warm):

```cypher
MATCH (qb:Player {name: 'Patrick Mahomes'})-[:PASSED]->(p:Play {season: 2024})<-[:TARGETED]-(r:Player)
RETURN r.name AS receiver, count(*) AS targets,
       sum(CASE WHEN p.complete THEN 1 ELSE 0 END) AS catches, sum(p.yards) AS yards
ORDER BY targets DESC LIMIT 5
```

| receiver | targets | catches | yards |
|---|---|---|---|
| Travis Kelce | 151 | 110 | 1,023 |
| Xavier Worthy | 118 | 77 | 913 |
| DeAndre Hopkins | 66 | 43 | 458 |
| Noah Gray | 52 | 43 | 451 |
| Samaje Perine | 37 | 28 | 301 |

(2024 including the playoffs; Kelce's 151 / 110 equal the play-by-play.)

**Everyone on the field for one play:** Super Bowl LVIII's overtime touchdown (`2023_22_SF_KC:4860`, "(:06) (Shotgun) 15-P.Mahomes pass short right to 12-M.Hardman for 3 yards, TOUCHDOWN."). Return the nodes instead of their names (`RETURN pl, o, p`) and the Browser draws the 22 players around the play:

```cypher
MATCH (g:Game {game_id: '2023_22_SF_KC'})<-[:IN_GAME]-(:Drive)<-[:IN_DRIVE]-(p:Play)
WHERE p.touchdown AND p.quarter = 5
MATCH (pl:Player)-[o:ON_FIELD]->(p)
RETURN o.side AS side, o.position AS position, pl.name AS player ORDER BY side DESC, position
```

The offense: Creed Humphrey (C), Trey Smith and Nick Allegretti (G), Mahomes (QB), Jerick McKinnon (RB), Jawaan Taylor and Donovan Smith (T), Noah Gray and Travis Kelce (TE), Mecole Hardman and Marquez Valdes-Scantling (WR); then the 49ers' eleven.

**Snaps two players shared** (0.24 s cold): Mahomes and Kelce on the field together, by season:

```cypher
MATCH (a:Player {name: 'Patrick Mahomes'})-[:ON_FIELD]->(p:Play)<-[:ON_FIELD]-(b:Player {name: 'Travis Kelce'})
RETURN p.season AS season, count(*) AS snaps_together ORDER BY season
```

1,164 · 1,014 · 1,096 · 1,116 · 1,067 · 1,036 · 1,099 · 780 (2018–2025; 2026 has no participation yet).

**A defense in a situation:** how often Pittsburgh sent 5+ pass rushers against 3rd and long (participation's `part_rushers`; 0.10 s cold):

```cypher
MATCH (p:Play {defense: 'PIT'})
WHERE p.down = 3 AND p.distance >= 7 AND p.dropback AND p.part_rushers IS NOT NULL
  AND NOT p.two_point AND p.play_type IN ['pass', 'run']
RETURN p.season AS season, count(*) AS dropbacks,
       round(100.0 * avg(CASE WHEN p.part_rushers >= 5 THEN 1.0 ELSE 0.0 END), 1) AS pct_5_plus_rushers
ORDER BY season
```

2018 30.8% · 2019 24.3% · 2020 28.2% · 2021 23.5% · 2022 28.3% · 2023 16.0% · 2024 21.1% · 2025 32.6% (95–115 dropbacks a season).

**A coverage mix:** Minnesota's defense on 3rd-down dropbacks in 2025: Cover 3 50, Cover 2 36, Cover 1 29, 2-man 15, Cover 4 14 (`MATCH (p:Play {defense: 'MIN', season: 2025}) WHERE p.down = 3 AND p.dropback AND p.coverage IS NOT NULL RETURN p.coverage, count(*) ...`).

**Drives:** the longest drives of 2024 by plays: PHI at PIT, 21 plays and 10:29 (End of half), LAC at DEN, 20 plays (Field goal), KC at LV, 19 plays (Field goal) (`MATCH (d:Drive {season: 2024}) RETURN d.drive_key, d.team, d.plays, d.time_of_possession, d.result ORDER BY d.plays DESC LIMIT 3`).

**The red zone:** Detroit's 2025 red-zone targets: Amon-Ra St. Brown 35 (10 TDs), Jahmyr Gibbs 15 (5), Isaac TeSlaa 11 (6), Jameson Williams 9 (1), Sam LaPorta 6 (2).

**Coaches:** KC's 2024 staff: `MATCH (c:Coach)-[r:HEAD_COACH|OFF_COORD|DEF_COORD|ST_COORD {season: 2024}]->(:Team {team_id: 'KC'}) RETURN type(r), c.name` → Andy Reid, Matt Nagy, Steve Spagnuolo, Dave Toub.

**The current season (appended):** 2026's top tacklers through week 5: Jacob Rodriguez 57 (24 solo), Alex Singleton 53, Fred Warner 50, Ernest Jones 50, Anthony Hill Jr. 47.

**Two things to know when you write your own:**
- `p.touchdown` is any touchdown on the play, the defense's included: 31 completed passes and 45 runs since 2018 were fumbled away and returned for a score. For the offense's touchdowns add `AND NOT coalesce(p.fumble_lost, false)`.
- Start from an indexed property (`Play {offense: ...}`, `Play {defense: ...}`, `Play {season: ...}`, `Player {name: ...}`): every query above answers in well under a second that way. A pattern that starts from all 374k plays with nothing to narrow it takes seconds on the HDD.

## 9. The weekly-graph check, disk and memory

**The weekly graph didn't notice (the phase's check, 2026-10-18 / 19).** The live week (2026 week 6) was rebuilt at its published run time with `build_graph` (which writes no file), once with the history container up and once with it stopped:

| | History up | History down | Published (2026-10-15) |
|---|---|---|---|
| Node and relationship counts (28 labels and types) | identical | identical | identical |
| Candidates (all 116, every field) | identical | identical | same ids |
| Picks | identical | identical | identical |
| Build time | 205 s | 145 s | 225 s |

The published `graph_results.json` was unchanged (same SHA-256 before and after), and the week's projections and published insights were put back afterwards (the `neo4j-graph` skill §6). The two servers share only the disk and the memory; the times are within the weekly build's usual range (its wipe alone took 140 s on the 15th).

**Again for AE03 (2026-10-19, after the review's fixes), on an idle computer,** with the history graph up and holding the person links and the Connections projection (`persons-20261019T162931Z`) in GDS memory: week 6 rebuilt in **132 s**; counts, the 116 candidates and the picks identical to the published file **in order**; every query's rows identical as sets. Two raw query lists (`q3_qb_change`'s receivers, `q6_former_teammates`) came back in another order: they `collect()` without an `ORDER BY`, in the weekly graph's own queries (older than AE03, untouched by the no-touch rule); nothing downstream reads that order (the candidates were identical in order). `graph_results.json` unchanged (SHA-256), the weekly graph back to 22,154 nodes / 609,528 relationships with its 4,226 projections and published insights. (A first run of the check overlapped the benchmark and took ~17 minutes: the reason `lab` and `bench-paths` refuse while the weekly lock is held, D150.)

**Disk** (`D:/nfl-ml-data/neo4j-history/`): `data/` **1.6 GB** (the store itself 1.1 GB, 2018–2025 + 2026 weeks 1–5, plus its transaction logs and the system database), the last build's CSVs ~550 MB (`import/`, safe to delete: the next build writes them again), the plugins 69 MB (`plugins/`), logs a few MB. The staging volume (`nfl-neo4j-history-staging`, inside Docker's own disk on C:) holds the new store only during a build and is removed after it. Unlike the weekly graph's store (5.2 GB for 22k nodes on 2026-10-08, because a wipe-and-reload never gives space back), the history store doesn't grow across rebuilds: each build is a fresh store.

**Memory:** heap 512 MB–1 GB, page cache 1.5 GB (`docker-compose.yml`), so the store fits in the cache once it's warm. Measured with both up after the walkthrough: the history container 1.8 GB, the weekly one 4.2 GB, and 8.7 GB of the PC's 31 GB free (other programs included); `nfl history-graph stop` frees it when you're done.

**Starting takes 1–2 minutes** (measured 131 s): the official Neo4j image's start script copies APOC and downloads the GDS plugin (66 MB) again on every start, the weekly container's too. Then a cold first query reads from the HDD (`history_qb_receivers_situation`: 0.66 s cold, 0.02 s warm).

## 10. How it differs from the weekly graph

| | Weekly graph (`nfl-neo4j`) | History graph (`nfl-neo4j-history`) |
|---|---|---|
| Purpose | this week's digest: matchups, injuries, former teammates, coaching trees, model outputs | research and Ask the Engine: play-level history, who was on the field, situations |
| Grain | seasons, games, player-games (`APPEARED_IN`), team-games | **plays** and **player-on-play** links |
| Seasons | 2018 on, **as of** the week being built (no future results: the leakage rules) | 2018 on, everything that happened (no "as of": it's history) |
| Size | ~22k nodes, ~600k relationships | ~434k nodes, ~10.5M relationships (§4, §8) |
| How it's built | wiped and reloaded over Bolt every Tuesday by the weekly run (~1.5–3 min) | bulk-imported once per finished season (§5), the current season appended weekly by its own command (§6) |
| When it runs | always (the weekly run starts it) | only when started (`nfl history-graph start`) |
| Ports | Browser 7474, Bolt 7687 | Browser 7475, Bolt 7688 |
| Who reads it | the digest, the control room, Ask (read-only) | Ask (read-only, the `history` source), AE03's experiments |
| Model outputs (predictions, projections, published insights) | yes | no |
| Participation (research) | no | yes (2018–2025) |

Some names are shared on purpose (`Player.player_id`, `Team.team_id`, `Game.game_id`, `Coach.coach_id` slugs, `PLAYED_FOR`'s rule), so the same id works in both. The weekly graph's `THREW_TO` (a season total) is computed in the history graph from plays: `(qb)-[:PASSED]->(p)<-[:TARGETED]-(wr)`.

## 11. Asking it questions (Ask the Engine's `history` source)

The history graph is Ask the Engine's third source, next to the curated tables (`sql`) and the weekly graph (`graph`). Everything in the [Ask guide](ask-the-engine.md) holds: templates first, a generated query as the fallback, the guard on every query (the same Cypher checks, `EXPLAIN`, READ transactions, 10 s, 500 rows), every number in the answer checked against the rows. The details are in the Ask guide §15; in short:

- **Switched on** by `ask.sources.history: true` (settings.yaml). It's offered only when the graph has a build (`build.json`), and "auto" leaves it out while the container is stopped, so other questions still go to the tables or the weekly graph.
- **Asking it directly:** `uv run nfl ask "..." --source history`, or **History graph** in the page's source picker (shown only when it's built). If the container is stopped, the answer says how to start it; while a build or an append runs, it says to ask again later. No model call is spent either way.
- **The source chip** reads "history graph 2018-2025 + 2026 week 5" (from `build.json` + `append.json`).
- **11 templates** for play-level questions, each answering in under 0.05 s once warm: a QB's receivers in a situation, a team's targets or ball carriers in a situation, a defense's blitz / 5+ rushers / pressure rates, a defense's coverage mix, a QB against each coverage, a receiver's targets by route, snaps two players shared, a player's snaps against a team, a team's personnel or formations, how a team's drives ended. Situations use the play-calling definitions (3rd and long = 7+ yards; the red zone = 20 yards or less); playoffs count unless the question says otherwise; leaders keep every row tied at the cut.
- **Good questions:** "Who has caught the most 3rd-down passes from Patrick Mahomes?" (Travis Kelce, 194), "How many snaps have Mahomes and Kelce played together?" (8,372), "How often does Pittsburgh blitz on 3rd and long since 2018?", "What coverage does San Francisco play most on 3rd and long?", "Which routes is Justin Jefferson targeted on most?", "How have Baltimore's drives ended since 2019?".

## 12. W&B

Each build logs a run `history-graph-build-<first>-<through>` (group `history-graph`, job type `history-graph-build`): the seconds per stage as each finishes, the CSVs' and the store's size, a table of every count (the export's, Neo4j's, match) and the rows dropped. Each append logs `history-graph-append-<season>-w<NN>` (job type `history-graph-append`): what it wrote per label and type, and its time. `--no-wandb` turns it off; `--smoke` sends a run to `history-graph-smoke`. They aren't models: they're there so a slow or failed build can be compared with the last good one. Panels and the runs so far: [weights-and-biases.md §6.12](weights-and-biases.md).

## 13. Changing it safely

- **The schema lives in one place:** `src/nflengine/history_graph/schema.py` (labels, keys, every property and its type) and `schema.cypher` (constraints, indexes). The export builds one frame per entry, the bulk import's CSV headers come from it, and the append writes the same frames. Add a property there and in `export.py`, then rebuild (`nfl history-graph build`): a full build is ~10 minutes, so there's no migration to write.
- **Tell Ask:** a new property or relationship goes into Ask's history schema text (`ask/schema.py`, `HISTORY_SCHEMA`) or the generator won't know it exists; that changes `prompt_hash`, so `nfl ask pick` compares only later runs (the `ask-the-engine` skill).
- **Never point it at the weekly graph.** `history_graph.config` refuses a settings block whose service, container or staging volume isn't the history graph's own, or whose ports are the weekly graph's; `docker.history_dir` refuses any host folder outside `neo4j-history/`; every compose command names the service under its profile; nothing runs `docker compose down`. The tests in `tests/history_graph/` pin all of it, and `tests/test_production_untouched.py` keeps the weekly graph's code as it was (D107).
- **Memory:** the container's heap and page cache are in `docker-compose.yml`. After a change, `uv run nfl history-graph stop` then `start` (compose recreates the container with the new settings; the data stays).
- **A bigger history** (2016–2017 participation exists, without coverage): `--first-season 2016` (and `history_graph.first_season`); about +95k plays and +2M links.

## 14. Limits and what's next

- **Participation is research data and lags a season.** 2026's arrives about February 2027; until the rebuild after the season, 2026 plays have no `ON_FIELD`, formation, personnel, coverage or routes. Ask's answers say which seasons a participation question covers.
- **2018–2022 positions on `ON_FIELD` are roster positions,** not the spot on that play (participation has no per-play positions before 2023).
- **No as-of rules.** The history graph holds what happened. Nothing in it may feed a weekly model or the digest (D107, D112); questions about this week belong to the weekly graph.
- **FTN and participation disagree a little** where both exist (2022–2025): FTN's `rushers` / `box` vs participation's `part_rushers` / `part_box`. [play-calling.md §2](play-calling.md#2-which-data-covers-what-and-when) says which source covers what, and when.
- **AE03 built on it** (§15–§18): the person links, Connections, the Graph lab and the path benchmark. TEAMMATE and OPPONENT come from participation, so they reach 2026 only after the rebuild that adds 2026's participation (about February 2027); this season's CONNECTION and COACHED / WORKED_WITH links are there from the weekly append.

## 15. The person links (AE03, `nfl history-graph derive`)

The history graph's own relationships run between a person and a **play** (Mahomes `PASSED` a play, Kelce was `ON_FIELD` for it). To ask "how are these two people connected?" you'd have to hop person → play → person every time, through millions of plays. The derive adds **person → person** links, each one a summary of everything the two did together, with the evidence on it.

| Type | From → to | What it means | How many (2026-10-19) |
|---|---|---|---|
| `TEAMMATE` | Player – Player | on the field together, **same side**, on at least one snap (participation, 2018–2025; special teams and penalty snaps count) | 237,136 |
| `CONNECTION` | QB → receiver | pass attempts the QB threw to him (PASSED + TARGETED on one play; every season, this one too) | 5,743 |
| `OPPONENT` | offense → defense | on opposite sides of the same **scrimmage** plays, in the classic matchups only: skill players (WR / TE / RB / FB) vs defensive backs, offensive vs defensive linemen, the QB vs any defender | 383,743 |
| `COACHED` | Coach → Player | the head coach (in his own weeks) or the coordinator of the player's unit (OC offense, DC defense, ST special teams) while the player took snaps in it | 35,273 |
| `WORKED_WITH` | Coach – Coach | on the same staff (head coach and coordinators) in the same season, **while both were there** (a head coach's own weeks) | 920 |

**What's on a link.** Totals over every season (`snaps`, `seasons`, `teams`; CONNECTION: `targets`, `catches`, `yards`, `touchdowns`, `epa`; OPPONENT: `plays`), plus, for TEAMMATE, COACHED and WORKED_WITH, **stints**: parallel lists with one item per season and team (COACHED: per season, team **and role**, each with that role's snaps; `stint_seasons`, `stint_teams`, `stint_snaps`, `stint_roles`), so the evidence "teammates on 2018 KC, 1,164 snaps together" needs no second query. TEAMMATE and WORKED_WITH are stored once per pair (from the smaller id): match them without an arrow, `(a)-[t:TEAMMATE]-(b)`. `schema.py` (`DERIVED`) lists every property.

**`snaps` is every link's strength:** snaps together on the field (TEAMMATE); the player's snaps in the coach's units, each snap counted once (COACHED); the team's snaps, both sides, while both coaches were on the staff (WORKED_WITH: a full season is ~2,800). Connections' "strongest" chains (§16) and the families (§17) weigh links by it.

**Where each comes from, and why:**
- TEAMMATE / OPPONENT from **participation** (who was on the field), so 2018–2025 only. A stint's team is the team the player was on the field for most often **in that game**: on a few special-teams snaps participation's side and nflverse's possession team disagree, and the first derive credited about a quarter of all pairs with a stray team ("teammates on ARI 2021" for two Rams); the rule fixed 56,685 links (D147).
- COACHED from the curated **snap counts** (`snaps`, PFR), not the box score: only 176 of 468 linemen on 2026 rosters had a box-score game by week 5, but all of them have snap counts, every season including this one.
- **Two fixes from the phase's review (D151).** WORKED_WITH counts only the weeks both coaches were there: a fired head coach and an interim who wasn't already on the staff never overlapped, so 7 such pairs have no link (Frank Reich and Jeff Saturday, IND 2022; Josh McDaniels and Antonio Pierce, LV 2023), while an interim who was a coordinator keeps his (Dan Quinn and Raheem Morris, ATL 2020: Morris was the DC). And a coach with two roles in one season (an interim who stays coordinator, a head coach who took over his own defense: Marvin Lewis, CIN 2018; Ron Rivera, WAS 2023) has a stint per role: Morris → Foyesade Oluokun shows ATL 2020 DC 896 snaps, HC 775 (weeks 6-17), 964 in all (each snap once), so "as head coach" counts his head-coach weeks only (318 links changed).
- OPPONENT is restricted on purpose: all offense-vs-defense pairs on scrimmage plays are 1.85M links (43.8M pair-events), five times the rest put together, for questions nobody asks ("how often did that guard face that safety?").
- The size: participation has 7.96M ON_FIELD links, about 40M teammate pair-events; a player has about 93 different teammates on the field (the most: Christopher Ezeala, a special-teamer, 498), so they collapse to 237k pairs, not the 1–2M the spec guessed.

**The strongest of each kind:**

| | |
|---|---|
| Teammates | Dion Dawkins – Josh Allen 8,969 snaps (BUF), Travis Kelce – Patrick Mahomes 8,372 (KC), Jake Matthews – Chris Lindstrom 7,203 (ATL), Quenton Nelson – Braden Smith 7,065 (IND) |
| Coached | Andy Reid → Mahomes 9,971 snaps, Sean McDermott → Dawkins 9,831, McDermott → Allen 9,108, Kyle Shanahan → Fred Warner 8,898 |
| Same staff | Reid – Dave Toub 9 seasons, Reid – Steve Spagnuolo 8, Mike Tomlin – Danny Smith 8 (PIT), Zac Taylor – Darrin Simmons 8 (CIN); the most colleagues: Chris Tabor 18 |
| QB → receiver | Mahomes → Kelce 1,240 targets, 913 catches, 10,933 yards, 79 TDs; Jared Goff → Amon-Ra St. Brown 816; Aaron Rodgers → Davante Adams 770 |
| Opponents | Baker Mayfield vs Jessie Bates 757 plays, Mahomes vs Justin Simmons 744, Tyler Lockett vs Budda Baker 709 |

**How a derive works.** It reads the same export the bulk import and the append use (the build's seasons plus the appended weeks; ~3 s), aggregates it in an in-memory DuckDB (~18 s), then **syncs**: it reads each type's links from the graph, compares them with the computed ones and writes only the difference (a changed link is deleted and created again; a stale one deleted). The first derive after a build writes everything (662,822 links: ~150 s over Bolt); a re-run with nothing new writes nothing (the read-back check is skipped for a type with no writes). Until the result is checked, the unfinished marker says `derive` and Ask doesn't read the history graph; `nfl history-graph status` says what to run. An append that starts over half-written links carries that in its own marker (`derive_pending`) and hands the marker back to the derive when its plays check out, so the links stay marked until a derive finishes (D151). It writes `neo4j-history/derive.json` (the counts, the changes, the times, the build and the appended week it covers, the W&B run) and `neo4j-history/people.parquet` (7,389 people, 5,665 with links: the name search's index). `derive_id` changes only when something was written, so the Connections projection and the lab files stay current across Tuesday re-runs; a derive that finishes one that didn't always takes a new id (the half-done one may have written links after the last `derive.json`).

**In the Browser** (http://localhost:7475):

```cypher
// Mahomes' most frequent teammates on the field (0.02 s warm)
MATCH (a:Player {name: 'Patrick Mahomes'})-[t:TEAMMATE]-(b:Player)
RETURN b.name AS teammate, t.snaps AS snaps, t.stint_seasons AS seasons
ORDER BY snaps DESC LIMIT 5
```

Kelce 8,372, Creed Humphrey 6,333, Trey Smith 5,852, Joe Thuney 5,061, Andrew Wylie 4,677.

```cypher
// Draw one chain: return the path itself and the Browser draws people and links
MATCH (a:Player {name: 'Tom Brady'}), (b:Player {name: 'Aaron Rodgers'})
MATCH p = SHORTEST 1 (a)-[:TEAMMATE|COACHED|WORKED_WITH]-+(b)
RETURN p
```

## 16. Connections ("six degrees", AE03)

**The question:** what's the shortest chain of people between any two players or coaches? Each link is a person link (§15): teammates on the field, a coach and a player he coached, two coaches on one staff. **Never a team**: through `PLAYED_FOR` every two players of one season would be two steps apart and the answer would mean nothing (the README's pitfall).

**How small the world is.** 300 random pairs of players with links: 10 one link apart, 144 two, 145 three, 1 four. Almost everyone is within three links of everyone, because one journeyman (Christopher Ezeala: 498 teammates) joins dozens of locker rooms.

**Two ways to rank chains:**
- **Fewest links** (the default): Cypher's `SHORTEST` (a breadth-first search from both ends at once). Several chains usually tie at the shortest length (Brady and Mahomes share Joe Thuney, Donovan Smith, Josh Gordon, Justin Watson ...); when there are at most 2,000 of them they're ranked by strength (the sum of 1 ÷ snaps, so the chain through the most shared snaps comes first), else Neo4j's own order. "Show 3 more" adds the next ones, then the next length.
- **Strongest links:** every link costs 1 ÷ its snaps and the chain with the lowest total wins, **however long it is** (GDS Dijkstra for one chain, Yen's algorithm for k). Because a coordinator spends every one of a quarterback's snaps with him, and two coaches on a staff share the team's whole season, strongest chains often run through coaching staffs: Brady → Rodgers is 2 links by fewest (Brady – Scott Miller, TB 2020–2022, 945 snaps – Rodgers, PIT 2025, 154) and 5 by strongest (Byron Leftwich → Todd Bowles → Brant Boyer → Nathaniel Hackett).

**Real chains (2026-10-19):**

| From → to | Fewest links |
|---|---|
| Mahomes → Kelce | 1: teammates, 8,372 snaps (KC 2018–2025) |
| Brady → Mahomes | 2: via Joe Thuney (NE 2018–2019, 2,565 snaps; KC 2021–2024, 5,061) |
| Andy Reid → Bill Belichick | 2: Reid coached Thuney (KC 2021–2024, 5,187 snaps), Belichick coached Thuney (NE 2018–2020, 3,690) |
| Mahomes → Belichick | 2: via Thuney (or JuJu Smith-Schuster, Tyquan Thornton, Josh Gordon) |
| Cam Ward → Belichick | 3: e.g. Ward – Kevin Zeitler – Joe Judge – Belichick |
| Cam Ward → Tom Brady | 3: Brian Callahan coached Ward (TEN 2025) and Alex Cappa (CIN 2022–2023); Cappa and Brady were teammates (TB 2020–2021, 2,374 snaps) |

**How fast:** warm, on the full graph, 50 random pairs: fewest 16 ms median (31 ms p90 for four chains), strongest 47 ms (110 ms p90 for four). The strongest search needs the person graph in GDS's memory (a "projection", `persons-<derive_id>`: 5,665 people, 546,672 directed links): 0.5 s to make on a warm graph, but up to ~30 s on a cold one right after the container starts, so `nfl app` makes it (and reads every link once) in the background as soon as the page sees the history graph up. (Windows' clock ticks every ~16 ms, so the small times above are rounded to 0 / 16 / 31 ms.)

**Where:** Explore → Ask the Engine → **Connections** in the control room (two name searches, the mode, the chain drawn with each link's evidence; [control-room.md](control-room.md) §2g), `GET /api/ask/connections?a=player:<GSIS id>&b=coach:<slug>&k=1..5&mode=fewest|strongest`, Ask ("How are Tom Brady and Aaron Rodgers connected?": the `history_connection` template, [ask-the-engine.md](ask-the-engine.md) §16), or the Browser (§15's query).

**Limits:** a 2026 rookie has only coaching links until 2026's participation arrives (his teammates come with the February rebuild); a player who never took a snap with anyone linked (no participation, no snap counts) has no chain; coaches before 2018 aren't in the history graph.

## 17. The Graph lab (AE03, `nfl history-graph lab`)

Three experiments with Neo4j's **Graph Data Science** library (GDS; a primer is in [graph-data-science.md](graph-data-science.md) §1) on the person links. Each job copies the part of the graph it needs into GDS's memory (a projection, named after the job and the derive), runs an algorithm in **stream** mode (results come back to Python; nothing is written to the database), writes its answers to `{NFL_DATA_ROOT}/neo4j-history/lab/` and logs a W&B run (`job_type: history-graph-lab`). The random parts (FastRP, KNN, Leiden) use seed 42 on one thread, so a run can be repeated exactly. Each finishes in seconds. Research only: nothing here feeds a weekly model or the digest (D107).

### 17.1 Neighbours: "players most like him by who he played with"

**How:** each player with at least 100 snaps since 2018 (4,272) gets a **fingerprint** of 128 numbers from **FastRP** (fast random projection: start every player at a random vector, then repeatedly mix in his neighbours' vectors, weighted by the link's strength, so players whose teammates overlap end up with similar vectors). The links are TEAMMATE weighted by log10(1 + snaps) and CONNECTION (a target counts like three snaps). Then **KNN** (k-nearest neighbours) finds the 10 closest fingerprints by cosine similarity (1 = the same direction): over everyone (GDS KNN), and an exact search within the player's position group.

**What it finds:** players who shared the same field, in the same role. Travis Kelce's nearest tight ends are Noah Gray (0.92; 1,910 snaps together), Joseph Fortson, Jared Wiley, Nick Keizer, Blake Bell: the Chiefs' other tight ends. His nearest anyone: Mahomes (0.999), then the KC offense of his years. Mahomes' nearest quarterbacks are his backups (Chad Henne, Chris Oladokun, Matt Moore: "never on the field together", the same teammates). Justin Jefferson's: Jordan Addison, Jalen Nailor, K.J. Osborn, Adam Thielen. That's what "by who he played with" means: it's a map of football's social network, not of playing style.

**The map:** the fingerprints squeezed to two dimensions with PCA. The two axes keep only 13% of the variation (8.2% + 5.1%), so near on the map doesn't always mean similar; the lists are the answer. Files: `neighbours.parquet` (both scopes, rank, similarity, snaps together), `neighbours_map.parquet` (x, y, position group, team).

### 17.2 Families: "football families"

**How:** **Leiden** community detection (it groups nodes so there are many strong links inside each group and few between them; a refinement of Louvain that guarantees each group is connected) on TEAMMATE + COACHED + WORKED_WITH, players and coaches together, weighted by log10(1 + snaps). Each family is named after its **biggest team-era**: the team with the most member snaps, the seasons where the members had at least a quarter of that team's best season, and the side of the ball most of it played on ("core" when both).

**The resolution decides what a family is.** Leiden's `gamma` sets how big groups are; read on the real names: at 1–4 the communities are whole franchises (23–34 of them: "2018-2025 KC core"), at 8 a franchise's offense and defense, at **16 its eras**, which is what "family" should mean here, so 16 it is (D149). 164 communities, 132 with 5+ people, median 42, the biggest 91.

**Real families:** "2018-2021 KC offense" (Tyreek Hill, Andrew Wylie; Eric Bieniemy) and "2021-2025 KC offense" (Mahomes, Kelce, Humphrey, Trey Smith; Andy Reid), "2018-2022 GB offense" (Aaron Rodgers, David Bakhtiari, Davante Adams; Joe Philbin) and "2021-2025 GB defense", "2018-2022 NE offense" (Joe Thuney, Shaq Mason, David Andrews; Josh McDaniels), "2018-2023 TB offense" (Donovan Smith, Mike Evans, Tristan Wirfs; Bruce Arians, Byron Leftwich), "2020-2025 TB defense" (Lavonte David, Antoine Winfield Jr.; Todd Bowles). Members carry their snaps **in the era** and since 2018 (Mahomes 6,237 in "2021-2025 KC offense", 9,971 in all). Files: `families.parquet`, `family_members.parquet`.

### 17.3 Chemistry: QB → receiver

**Duos:** every CONNECTION with 100+ targets (339), ranked by **EPA per target** (expected points added each time that QB threw to that receiver). The top with 300+ targets: Brock Purdy → George Kittle 0.672 (305 targets, 77% caught), Russell Wilson → Tyler Lockett 0.637 (420), C.J. Stroud → Nico Collins 0.544 (344). The most targets: Mahomes → Kelce 1,240.

**Who's central:** **PageRank** on the QB ↔ receiver graph weighted by targets (a receiver scores high when many quarterbacks, and central ones, threw to him a lot): Davante Adams first (12 QBs threw to him), then Travis Kelce, Keenan Allen, Stefon Diggs, Tyreek Hill; among QBs Mahomes, Josh Allen, Matthew Stafford, Jared Goff, Baker Mayfield (84 receivers). Files: `chemistry_duos.parquet`, `chemistry_pagerank.parquet`.

### 17.4 Running and reading it

`uv run nfl history-graph lab` (all three) or `--job families` (one or several, comma-separated); `lab.json` records each job's run time, the derive it read, the parameters and the W&B run. The control room's **Graph lab** reads these files (they answer with the container stopped) and says "made on older person links" when a derive wrote something after the lab ran: run the lab again. **One lab or path benchmark runs at a time** (their own lock, `neo4j-history/.lab.lock`: Ask and Connections keep answering meanwhile), each job's projection is dropped even when the job fails, and a job's files are written only if the person links held still while it ran (the same `derive_id`, no build, append or derive): otherwise nothing is written and the command says to run it again (D151). The W&B runs (`history-graph-lab-<job>`) hold the tables and, for neighbours, the 2-D map as a scatter chart; for families, the sizes as a bar chart ([weights-and-biases.md](weights-and-biases.md) §6.13).

## 18. The path benchmark, and 2026's sub-cubic all-pairs result (AE03, `nfl history-graph bench-paths`)

How fast are shortest-path algorithms on our own graph, and does a celebrated new theory result change anything? The benchmark answers the first with real timings; §18.4 answers the second. Every number below is from the canonical run ([td5sftdt](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/td5sftdt), 713 s on this PC: 16 threads, 31 GB; Python 3.12, numba 0.62.1, scipy 1.18.1, Neo4j 5.26.31, GDS 2.13.13; seed 42; files `neo4j-history/lab/path_benchmark.{json,parquet,svg}`).

### 18.1 Three kinds of "shortest path" question

The graph is the **person graph** of §16: 5,665 players and coaches, 273,336 links (546,672 arcs, each link counted both ways), ~96 links a person. "Shortest" is either the fewest links, or the strongest chain where a link costs 1 ÷ snaps together (600 snaps shared cost 0.0017, 3 snaps 0.33).

| Question | Example | The answer | Who asks it here |
|---|---|---|---|
| **One pair** | "How is Patrick Mahomes connected to Tom Brady?" | one chain | Connections |
| **One source** | "How far is Andy Reid from everyone?" | a distance for each of 5,665 people | nothing yet (a "closest people" view could) |
| **All pairs** (APSP) | "How far is everyone from everyone?" | 32,092,225 distances (257 MB as 64-bit numbers) | nothing: the expensive one |

One pair can be answered by searching from both ends until the searches meet (a bidirectional breadth-first search, BFS). One source needs the whole graph explored once: **Dijkstra's algorithm** (1959) always settles the closest unsettled person next, from a priority queue. All pairs is a Dijkstra from every person, or **Floyd–Warshall** (1962): three nested loops, n³ steps.

### 18.2 What we timed

Samples are seeded "snowballs" (a BFS from a random person, the first n people it reaches and every link between them: 1,000 people / 63,706 arcs, 2,000 / 157,718, 5,000 / 517,030) plus the whole graph. The contenders: Cypher's `SHORTEST 1` (hops, in the database, whole graph only); GDS Dijkstra (one pair, weighted), Delta-Stepping (one source; `.stream` returns the distances, `.stats` only computes) and `allShortestPaths.stream` (all pairs), each on an in-memory projection (`bench-<n>-<derive id>`, dropped after use); scipy's `dijkstra` and `floyd_warshall` (C, one thread); **Dijkstra on a binary heap in pure Python**; **BMSSP**, our own pure-Python implementation of Duan et al. 2025 (§18.5), on the graph as it is and after the paper's constant-degree transformation; and **Floyd–Warshall compiled with numba** on 16 threads (compiling took 0.59 s, not counted). **Every timed run is checked:** the distances must equal scipy's to 1e-9 (GDS's all-pairs: the count and the sum of its distances); every one agreed. Times are what a caller sees (the database rows include the Bolt round trip). An algorithm is skipped at a size when, scaled up from the last one, it would pass the 150 s budget; Floyd–Warshall ran fewer repeats at the largest sizes for the same reason.

Median time per question (20 random pairs, 10 random sources, up to 3 all-pairs runs):

| Question | Algorithm | 1,000 | 2,000 | 5,000 | 5,665 (all) |
|---|---|---:|---:|---:|---:|
| one pair | Cypher `SHORTEST 1` (hops, bidirectional BFS) | | | | **3.6 ms** |
| one pair | GDS Dijkstra, source → target (weighted) | 20.7 ms | 22.4 ms | 20.3 ms | 29.7 ms |
| one source | scipy Dijkstra (C) | 0.28 ms | 0.59 ms | 1.75 ms | **1.82 ms** |
| one source | Dijkstra on a heap, pure Python | 4.57 ms | 11.7 ms | 48.1 ms | 54.4 ms |
| one source | BMSSP (Duan et al. 2025), pure Python | 18.2 ms | 39.1 ms | 156 ms | 157 ms |
| one source | BMSSP after the constant-degree transformation | 625 ms | 1.65 s | 6.36 s | 6.24 s |
| one source | GDS Delta-Stepping `.stream` | 26.0 ms | 60.2 ms | 114 ms | 124 ms |
| one source | GDS Delta-Stepping `.stats` (no distances back) | 8.7 ms | 11.2 ms | 17.7 ms | 17.7 ms |
| all pairs | scipy Dijkstra from every person (C, 1 thread) | 239 ms | 1.14 s | 8.44 s | 9.62 s |
| all pairs | GDS `allShortestPaths` (4 threads) | 178 ms | 822 ms | 6.39 s | **8.33 s** |
| all pairs | Floyd–Warshall, numba (16 threads) | 36 ms | 426 ms | 35.0 s | 51.1 s |
| all pairs | scipy Floyd–Warshall (C, 1 thread) | 410 ms | 3.72 s | 58.5 s | 84.7 s |

**Reading the chart** (`path_benchmark.svg`, and the W&B run's `chart_log_log_svg` panel: both axes logarithmic, so a power law is a straight line whose slope is its exponent):

- **One pair is milliseconds, at any size we have.** A random pair is 2.48 links apart on average (5 at most), so two searches that each go a link or two deep meet at once: 3.6 ms. The weighted GDS Dijkstra is 8× slower (it settles people by cost, and it's a procedure call) and still ~30 ms. That's all Connections needs.
- **One source is a couple of milliseconds in C, tens in Python.** scipy 1.82 ms; the same algorithm in pure Python 54.4 ms (30×: just Python against C); GDS Delta-Stepping 17.7 ms computing, 124 ms when it streams all 5,665 distances back.
- **All pairs is where it explodes.** From 1,000 to 2,000 people the numba Floyd–Warshall's time grows 11.8× and scipy's 9.1× (a cubic algorithm: 8×); a Dijkstra from every person 4.8×, GDS 4.6×. From 2,000 to 5,000 the work grows 15.6× but the numba time 82× (its matrix grows from 32 MB to 200 MB and probably outgrows the CPU caches; not tested). On the whole graph a search from every person (9.6 s, or GDS 8.3 s on 4 threads) beats Floyd–Warshall on 16 threads (51 s) by 5×: Floyd–Warshall always does n³ steps, a search per person about n × m, and this graph is sparse next to n².
- **Spread:** a first complete run that overlapped a weekly-graph rebuild ([h40flstf](https://wandb.ai/models-ontario-tech-university/nfl-analytics-engine/runs/h40flstf)) had local rows within 1–20% of these but database rows up to 2.9× slower; a run sharing the CPU with a test suite (aborted, `zc1xkvqx`) showed numba's Floyd–Warshall at 1,000 people taking 189 ms instead of 36. The order of the algorithms never changed. Run it on an idle computer; it refuses while the weekly run holds its lock, but it can't see other work.

**The bottom line:** answer a person-to-person question by search, never precompute all pairs, and even the full all-pairs table is a ten-second job at our size.

### 18.3 What APSP and 3SUM are, and the hypotheses about them

**APSP** is the all-pairs problem. For a dense graph (every pair linked) the textbook cost is about n³; decades of work saved only factors smaller than any power of n. The **APSP hypothesis** says that no algorithm runs in n^(3−ε) for any ε > 0. **3SUM** asks whether any three of n numbers add to zero; sorting plus a two-pointer sweep takes n², and the **3SUM hypothesis** says n^(2−ε) is impossible. These beliefs are the base of *fine-grained complexity*: dozens of problems in geometry, graphs, strings and data structures are proven "at least as hard as" 3SUM or APSP by reductions, which reads as "don't expect a fast algorithm for this". They're a map of which problems are probably stuck.

### 18.4 The new result: Alman and Vassilevska Williams (arXiv 2610.06783, 5 October 2026)

"Truly Subquadratic 3SUM and Truly Subcubic APSP via Triangles in Sparse Lopsided Graphs" by Josh Alman and Virginia Vassilevska Williams ([arXiv](https://arxiv.org/abs/2610.06783); checked on the abstract page and the paper's HTML text, 2026-10-19):

- **The first polynomial improvements** over the textbook algorithms: 3SUM on n integers of polynomial size in O(n^1.9992), deterministic, and APSP on directed n-vertex graphs with polynomially bounded integer weights in O(n^2.9995). That **refutes both hypotheses** (and, the paper says, the Exact Triangle hypothesis with them).
- How: a new algorithm for multiplying "thin" integer matrices when only some entries of the product are needed, built on a modified rectangular matrix-multiplication method.
- **The paper's abstract says Claude (Anthropic's model) discovered the algorithm** and verified the main results in the Lean 4 proof assistant (with Mathlib); the authors then understood, simplified, strengthened and extended it. (We read that statement; we didn't check the Lean proofs.)
- The paper itself calls the algorithms "potentially impractical in their current form" and says the constants hidden in the O( ) are enormous. We found no released implementation.

**Why it's called "galactic":** a galactic algorithm beats the standard one only on inputs too large to exist, because its hidden constant is astronomical. Here the saving is a factor of n^0.0005: at our n = 5,665 that's 1.0043, **0.43%**, before the constant; even at a million vertices 0.69%. For theory it's a landmark (the belief was wrong in principle); for practice, nothing changes. **For us, nothing twice over:** the bound competes with cubic methods on near-complete graphs, but the person graph has 546,672 arcs against 32 million pairs, so the baseline that matters is a search from every person (9.6 s above); and the table shows that the real costs of all-pairs are the memory and the n² answers, not the exponent.

### 18.5 Duan et al. 2025, "Breaking the Sorting Barrier", and our implementation

Dijkstra costs O(m + n log n) with a good heap, and its n log n part is **sorting**: settling people in distance order sorts them. Duan, Mao, Mao, Shu and Yin ([arXiv 2504.17033](https://arxiv.org/abs/2504.17033), STOC 2025's best paper) run single-source shortest paths in O(m log^(2/3) n) (deterministic, directed graphs, real non-negative weights), faster than Dijkstra's bound on sparse graphs, by not sorting everything: a recursion over distance ranges that mixes Dijkstra with a few rounds of Bellman–Ford and orders only a few "pivot" vertices (the roots of big shortest-path trees).

**Ours** (`history_graph/sssp.py`, explained in plain words in its docstring) was written from the paper alone (Rishi's choice: no third-party port downloaded or run). It equals scipy on every graph tested (tens of thousands of random cases: equal and zero weights, unreachable vertices, directed and undirected, and the real person graph). The tests found one place where, as we read it, the paper's pseudocode needs a fix: after a deeper call completes a vertex, its old queue entry stays and the vertex is later returned twice; deleting completed vertices from the queue (the data structure's own Delete) repairs it.

**In practice it's 2.9× slower than a heap Dijkstra** in the same language on the same inputs (157 ms vs 54 ms on the whole graph), and 40× slower again after the paper's constant-degree transformation (which turns each person into a cycle of zero-cost nodes, one per link). Why theory's win doesn't show, in numbers:

- Dijkstra's bound here is m + n log₂ n ≈ 617k steps, BMSSP's m · (log₂ n)^(2/3) ≈ 2.94M: **4.8× more**. With 96 links a person, Dijkstra's sorting term is already small next to m; there's little sorting to save. The bounds cross at ~16k arcs (2.8 a person) at this n; at our density BMSSP's bound would win only beyond n = 2^898,630.
- With n = 5,665 the parameters are k = 2 (a pivot only has to beat a 2-vertex tree) and t = 5 (3 levels). In the measured run 69 pivot searches took 5,604 vertices in and kept 3 as pivots; 3 searches gave up at once (one Bellman–Ford round from a hub touches more than k × its sources): the pivot step saved nothing.
- A sweep of k (1–64) and t (2–8) on the whole graph never beat 3.5× the heap's time; the worst (k = 1, t = 2) was 15×.
- Constant factors: each BMSSP step builds tuples, sorts blocks and walks a 3-level recursion; a heap step is a push and a pop.

### 18.6 Running it

`uv run nfl history-graph bench-paths` (~12 minutes with the defaults: samples of 1,000, 2,000 and 5,000 people plus the whole graph, 20 pairs, 10 sources, 3 repeats, a 150 s budget per algorithm and size; options `--sizes`, `--pairs`, `--sources`, `--repeats`, `--budget`, `--seed`, `--no-wandb`, `--smoke`). The history container must be up, with no build, append or derive running, and it refuses while the weekly lock is held. It writes `lab/path_benchmark.json` (the run's facts, every row, the machine), `.parquet` (the rows) and `.svg` (the chart), and a W&B run (`job_type: path-benchmark`: a live `median_ms/<family>/<algorithm>` point per size, the `results` table, the log-log chart as `chart_log_log_svg`; [weights-and-biases.md](weights-and-biases.md) §6.14). The control room's **Graph lab → Benchmark** draws the file. Nothing is written to the database; the GDS projections are dropped after use (only `bench-*` names).
