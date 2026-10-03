# 05: Knowledge Graph (Neo4j)

## Purpose

1. **Learning goal:** hands-on experience with Neo4j, Cypher and Graph Data Science (GDS) on a real, rich domain.
2. **Product goal:** power the *Matchup / risk to watch* and *Non-obvious insights* sections with **multi-hop** questions that would be awkward in a table: player and coach history across teams, QB–receiver connections, injury ripple effects through depth charts, common-opponent chains, crew tendencies.
3. **Store model outputs:** ratings, predictions and projections are written back as nodes so queries can combine "what the model thinks" with "how things are connected".

**Design rule:** a query belongs here only if it needs relationships, meaning multiple hops, paths, or graph algorithms. Simple filters and joins stay in DuckDB.

## Runtime: Neo4j in Docker (local)

```yaml
# docker-compose.yml (sketch)
services:
  neo4j:
    image: neo4j:5.26-community        # 5.26 = LTS; a newer calendar-versioned release is fine. Pin the exact tag.
    container_name: nfl-neo4j
    ports:
      - "7474:7474"   # Browser
      - "7687:7687"   # Bolt
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
      NEO4J_PLUGINS: '["apoc", "graph-data-science"]'
      NEO4J_server_memory_heap_max__size: 2G
      NEO4J_server_memory_pagecache_size: 1G
    volumes:
      - ${NFL_DATA_ROOT}/neo4j/data:/data   # database files live on D: (see 02 → Storage)
      - ${NFL_DATA_ROOT}/neo4j/logs:/logs
      - ${NFL_DATA_ROOT}/neo4j/plugins:/plugins   # keeps downloaded APOC/GDS across recreates
    restart: unless-stopped
```

The real file is `docker-compose.yml` at the repo root (P00). It also sets the procedure allowlist for `gds.*` / `apoc.*` and takes an optional `NEO4J_USER`.

- **The database files live on the D: drive** (a bind mount under the data root), not in Docker's own storage on C:. The graph is disposable (rebuilt weekly from Parquet), so if the HDD bind mount is ever too slow, switching to a named Docker volume costs nothing.
- Community edition has one user database (`neo4j`), with no size limit at our scale.
- Connect with the official `neo4j` Python driver over Bolt. Credentials come from `.env`.
- Neo4j Browser at `http://localhost:7474` for exploring and learning.
- Community edition has no role-based access control, so **read-only access** for any LLM-written Cypher (stretch goal, below) is enforced in code: read-only sessions (`execute_read`), and any query with write clauses (`CREATE`, `MERGE`, `SET`, `DELETE`, `REMOVE`, `CALL … IN TRANSACTIONS`, `apoc.*` writes) is rejected.

## Scope and update strategy

These answer the brief's open questions.

- **League-wide, not a slice.** Models need all teams anyway, and the cross-team history is where the interesting connections are. Followed teams (`config/followed_teams.yaml`) affect *which insights get picked* for the digest, not what's loaded.
- **Seasons:** 2018 to the current season (matches PFR advanced stats coverage). Can be extended back later.
- **Full rebuild every week** from curated Parquet, not incremental updates. The data is small, a rebuild is quick, and the graph can never drift from the source of truth. Steps:
  1. Delete everything in batches (`CALL { … } IN TRANSACTIONS`), or for a guaranteed-clean start, stop the container, empty `{NFL_DATA_ROOT}\neo4j\data` and start it again.
  2. Apply `schema.cypher` (constraints and indexes).
  3. Load nodes, then relationships, with batched `UNWIND $rows` writes (5–10k rows per batch).
  4. Write model outputs for the current week.
  5. Run GDS jobs (projections → algorithms → write results).
  6. Run the query library → save the results as `graph_results.json` in the run folder.
- **Size estimate** (2018–2026): ~10k players, ~2,500 games, ~200k `APPEARED_IN` relationships, plus aggregates. Small for Neo4j.
- **Play-level nodes** (~45k plays/season) aren't in v1. Add them later if a query really needs them.

## Schema

### Nodes

| Label | Key | Main properties |
|---|---|---|
| `Team` | `team_id` (canonical code) | `franchise_id`, `name`, `conference`, `division` |
| `Player` | `player_id` (GSIS) | `name`, `position`, `position_group`, `birth_date`, `college`, `rookie_season`, `scramble_rate`* (QB), BDB movement profile* (see [07](07-track2-big-data-bowl.md)) |
| `Game` | `game_id` (nflverse) | `season`, `week`, `game_type`, `kickoff`, `home_score`, `away_score`, `roof`, `surface`, `temp`, `wind`, `neutral_site`, `div_game` |
| `Coach` | `coach_id` (name slug) | `name` |
| `Venue` | `stadium_id` | `name`, `roof`, `surface`, `city` |
| `Official` | `official_id` | `name` |
| `TeamWeek` | (`team_id`, `season`, `week`) | Ratings (`off_epa`, `def_epa`, `net`, pass/rush splits), `elo`, `trend_delta`, `trend_dir`, `perf_vs_expected` |
| `GamePrediction` | (`game_id`, `model_version`) | `variant` (model-only / market), `home_win_prob`, `expected_margin`, `created_at` |
| `PlayerProjection` | (`player_id`, `game_id`, `model_version`) | `target`, `p10`, `p50`, `p90`, `baseline`, `outperformance`, `confidence`, `top_drivers` |
| `PublishedInsight` | `insight_id` | `type`, `entities`, `season`, `week`, used to avoid repeating an insight within 3 weeks |

\* Derived properties written by the pipeline.

### Relationships

| Pattern | Properties | Source |
|---|---|---|
| `(Player)-[:PLAYED_FOR]->(Team)` | `season`, `first_week`, `last_week`, `games` | weekly rosters |
| `(Player)-[:APPEARED_IN]->(Game)` | `team_id`, `snaps`, `snap_pct`, `targets`, `rec`, `rec_yds`, `carries`, `rush_yds`, `pass_yds`, `epa`, `pressures`, `sacks`, NGS fields where present | player stats, snaps, PFR adv, NGS |
| `(Team)-[:PLAYED_IN]->(Game)` | `home`, `points`, `epa_per_play`, `success_rate`, `penalties`, `result` | play-by-play, schedules |
| `(Game)-[:AT]->(Venue)` | | schedules |
| `(Coach)-[:HEAD_COACH_OF]->(Team)` | `season` | schedules (`home_coach` / `away_coach`) |
| `(Coach)-[:COACHED_IN]->(Game)` | `team_id` | schedules |
| `(Coach)-[:COORDINATOR_OF]->(Team)` | `season`, `role` (OC/DC) | *optional hand-made seed CSV* |
| `(Coach)-[:WORKED_UNDER]->(Coach)` | `seasons` | *optional seed*, for coaching trees |
| `(Official)-[:OFFICIATED]->(Game)` | `role` | officials |
| `(Player)-[:THREW_TO]->(Player)` | `season`, `games_together`, `targets`, `completions`, `yards`, `tds`, `epa` | play-by-play (passer → receiver), season totals |
| `(Player)-[:ON_INJURY_REPORT]->(Game)` | `status`, `practice_status`, `body_part`, `report_date` | injuries |
| `(Player)-[:DEPTH_CHART]->(Team)` | `season`, `week`, `position`, `rank` | depth charts |
| `(Player)-[:DRAFTED_BY]->(Team)` | `year`, `round`, `pick` | draft picks |
| `(Player)-[:TRADED_TO]->(Team)` | `date`, `from_team` | trades |
| `(Team)-[:HAS_WEEK]->(TeamWeek)` | | ratings model |
| `(TeamWeek)-[:NEXT]->(TeamWeek)` | | ratings model, linking weeks in order for time-based queries |
| `(Game)-[:HAS_PREDICTION]->(GamePrediction)` | | game model |
| `(Player)-[:HAS_PROJECTION]->(PlayerProjection)-[:FOR_GAME]->(Game)` | | player model |
| `(Player)-[:SIMILAR_TO]->(Player)` | `score`, `basis`, `season` | GDS node similarity (written back) |

Fixes to the original schema: the floating `WeeklyStat` node becomes stats on `APPEARED_IN`, the vague `FEEDS` relationship is removed, `Week` becomes a property, and coaches, officials, venues, QB–receiver links, depth charts and player movement are added.

### Constraints and indexes (`schema.cypher`)

```cypher
CREATE CONSTRAINT team_id   IF NOT EXISTS FOR (t:Team)   REQUIRE t.team_id   IS UNIQUE;
CREATE CONSTRAINT player_id IF NOT EXISTS FOR (p:Player) REQUIRE p.player_id IS UNIQUE;
CREATE CONSTRAINT game_id   IF NOT EXISTS FOR (g:Game)   REQUIRE g.game_id   IS UNIQUE;
CREATE CONSTRAINT coach_id  IF NOT EXISTS FOR (c:Coach)  REQUIRE c.coach_id  IS UNIQUE;
CREATE CONSTRAINT venue_id  IF NOT EXISTS FOR (v:Venue)  REQUIRE v.stadium_id IS UNIQUE;
CREATE CONSTRAINT official_id IF NOT EXISTS FOR (o:Official) REQUIRE o.official_id IS UNIQUE;
CREATE INDEX game_season_week IF NOT EXISTS FOR (g:Game) ON (g.season, g.week);
CREATE INDEX teamweek_key     IF NOT EXISTS FOR (tw:TeamWeek) ON (tw.team_id, tw.season, tw.week);
CREATE INDEX player_pos       IF NOT EXISTS FOR (p:Player) ON (p.position_group);
```

## Query library

Each query lives in `src/nflengine/graph/queries/{name}.cypher` and takes `$season`, `$week` and other parameters. Every query returns rows with:

- the entities (team, player and coach names),
- **numbers already computed** (the LLM does no math),
- `insight_type`,
- a `strength` score used for ranking.

The payload builder picks the top items by strength, prefers followed teams, and skips anything published in the last 3 weeks (`PublishedInsight`).

The sketches below show intent. Final Cypher is written and tested during the build.

### Q1: Revenge games (player vs former team)
Players with real snaps for their current team who played for this week's opponent within the last 2 seasons.
```cypher
MATCH (p:Player)-[cur:PLAYED_FOR {season: $season}]->(t:Team)
WHERE cur.last_week >= $week - 1
MATCH (t)-[:PLAYED_IN]->(g:Game {season: $season, week: $week})<-[:PLAYED_IN]-(opp:Team)
MATCH (p)-[old:PLAYED_FOR]->(opp)
WHERE old.season >= $season - 2 AND old.season < $season
OPTIONAL MATCH (p)-[a:APPEARED_IN]->(pg:Game) WHERE pg.season = $season
WITH p, t, opp, g, collect(DISTINCT old.season) AS seasons_with_opp, avg(a.snap_pct) AS snap_pct
WHERE snap_pct >= 0.5
RETURN p.name, p.position, t.team_id, opp.team_id, seasons_with_opp, round(snap_pct, 2) AS snap_pct
```

### Q2: Injury ripple (who steps in, and what happened last time)
A starter ruled Out → the next player on the depth chart → how that player and the team performed in past games where the starter didn't play.
- Path: `(starter)-[:ON_INJURY_REPORT {status:'Out'}]->(g)`, `(starter)-[:DEPTH_CHART {rank:1}]->(team)<-[:DEPTH_CHART {rank:2, position: same}]-(backup)`. Then games where `team` played and the starter has no `APPEARED_IN`, compared with games where the starter played.
- Returns: starter, backup, the backup's per-game numbers with and without the starter, the team's EPA/play with and without the starter, and the sample size (n games). A small n → confidence low.

### Q3: QB-change ripple
When a backup QB starts: which receivers have history with the backup (`THREW_TO`), and how does the backup's target distribution compare with the starter's?
- Hops: `(backup:Player)-[tt:THREW_TO]->(wr)-[:PLAYED_FOR {season:$season}]->(team)` vs `(starter)-[:THREW_TO {season:$season}]->(wr)`.
- Feeds both the player model (as a feature) and the digest (as an insight).

### Q4: Common-opponent chains
Teams A and B meet this week. Both played team C this season (or within the last 2 seasons). Compare their EPA margins against C.
```cypher
MATCH (a:Team)-[:PLAYED_IN]->(g:Game {season:$season, week:$week})<-[:PLAYED_IN]-(b:Team)
WHERE elementId(a) < elementId(b)
MATCH (a)-[ra:PLAYED_IN]->(ga:Game {season:$season})<-[:PLAYED_IN]-(c:Team),
      (b)-[rb:PLAYED_IN]->(gb:Game {season:$season})<-[:PLAYED_IN]-(c)
WHERE ga.week < $week AND gb.week < $week AND c <> a AND c <> b
RETURN a.team_id, b.team_id, c.team_id, ga.week, ra.epa_per_play, gb.week, rb.epa_per_play
```
(Margins are computed in a follow-up step or with `PLAYED_IN` properties on both sides.)

### Q5: Coaching connections
A head coach facing a former team, coordinators facing a former boss, and matchups within one coaching tree. Uses `HEAD_COACH_OF` history (from nflverse) and, if the optional seed exists, `COORDINATOR_OF` and `WORKED_UNDER` paths (variable-length: `(c1)-[:WORKED_UNDER*1..2]-(c2)`).

### Q6: Former teammates on opposite sides
A QB facing a receiver they used to throw to, or a pass rusher facing a former team's offensive line. Hops: `(qb)-[:THREW_TO {season: s}]->(wr)` where they're now on teams playing each other this week. Includes their combined history numbers.

### Q7: Style matchup (for example, defense vs mobile QBs)
For this week's opponent QB with a high `scramble_rate`, compare the defense's EPA/play allowed in past games against high-scramble QBs vs other QBs.
- Hops: `(def:Team)-[:PLAYED_IN]->(g)<-[:APPEARED_IN]-(qb:Player {position:'QB'})` with `qb.scramble_rate` above a threshold, plus the defense's `PLAYED_IN` stats from the opponent side.
- The same pattern works for other styles: deep passers (high air yards), heavy play action (from FTN), run-heavy offenses.

### Q8: Trend mismatch
Teams trending down (`TeamWeek` path over the last 3 weeks via `NEXT`) facing a team trending up. Kept from the original spec. It's simple, but useful practice with `NEXT` path traversal.

### Q9: Officiating crew tendencies
This week's referee crew: penalties per game and home/away penalty split over their recent games, compared with the league average. Hops: `(o:Official {role:'Referee'})-[:OFFICIATED]->(g)<-[pi:PLAYED_IN]-(t)`.

### Q10: Graph Data Science
- **Passing network centrality:** project each team's season `THREW_TO` graph and run PageRank or degree centrality weighted by targets or EPA. Shows "who the offense actually runs through", and how that changes when a player is out.
- **Player similarity:** node similarity or k-nearest-neighbors on usage and efficiency vectors → write `SIMILAR_TO`. Used for "this rookie's usage looks like X's breakout year" (always labeled as a comparison, not a prediction).
- **Communities (later):** community detection on the player-movement and coaching graph to surface coaching-tree clusters.

## Testing

- **Golden tests:** for 2–3 known past weeks, assert that queries return the expected entities (for example, a known revenge game, or a known QB injury ripple).
- **Load tests:** node and relationship counts after a rebuild match the counts expected from the Parquet sources.
- **Performance:** every library query finishes in under 2 seconds. Add indexes when one doesn't.

## Stretch: Q&A agent over the graph

Separate from the digest, a later learning project: a chat interface where an LLM writes Cypher (text-to-Cypher) against the schema, runs it in a **read-only session** (write clauses rejected; see Runtime above), and answers questions ("How has the Lions' pass rush done against mobile QBs since 2023?"). This covers the agentic and RAG learning goals without risking the digest's guarantee that numbers come only from the models and data.
