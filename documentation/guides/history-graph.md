# History graph guide (every play since 2018, in its own Neo4j)

**What this is:** a plain-language guide to the **history graph**: a second, permanent Neo4j database that holds every NFL play since 2018 with the players on the field, the drives, the games, who passed, ran, caught and tackled, rosters and coaches. It's separate from the weekly knowledge graph (the one the digest uses), which it never touches. Built in AE02; AE03 adds connections ("six degrees"), graph experiments and a shortest-path benchmark on top of it.

**Related docs:** the spec is [Ask the Engine README](../ask-the-engine/README.md) (§4, §6, §8 "As built in AE02") and the phase file [AE02](../ask-the-engine/AE02-history-graph.md); decisions D112 and D145–D146 in the [decisions log](../10-decisions-log.md). The weekly graph has its own guide: [knowledge-graph.md](knowledge-graph.md). Asking it questions in plain English: [ask-the-engine.md](ask-the-engine.md) §15. W&B runs: [weights-and-biases.md](weights-and-biases.md) §6.12. Agents use the `neo4j-graph` and `ask-the-engine` skills.

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
| Append the current season | `uv run nfl history-graph append` | **Tuesdays after the weekly run** (runbook). Starts the container if it's down |
| Look at it | http://localhost:7475 | Connect URL `bolt://localhost:7688`, user `neo4j`, the password from your env file |

`build`, `append`, `start` and `stop` take the history graph's own lock (`neo4j-history/.history.lock`), so none of them runs while another does; `build` and `append` also refuse while the **weekly** run holds its lock (Tuesday's run shares the disk and has a deadline). Ask's history questions refuse while a build or an append runs.

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

Run it on Tuesdays after the weekly run (runbook). It starts the history container if it's down, refuses while the weekly lock is held, and writes `append.json` (the season, the weeks, the counts, what changed, the time) and a W&B run.

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
- **AE03** builds on it: derived relationships (teammates by shared snaps, QB → receiver chemistry), Connections ("six degrees" between any two players or coaches), GDS experiments (embeddings, communities) and the shortest-path benchmark.
