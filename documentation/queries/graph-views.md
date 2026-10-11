# Graph views: pictures to explore in Neo4j Browser

Fourteen queries made for the **Graph** view, for exploring rather than checking a digest. Unlike the weekly files next to this one, they aren't tied to one week: you type a player name, a team code or a season on the first line and run them against whatever week is loaded.

Every query was run against the **2026 week-5 graph** (results through week 4) on 2026-10-11; "You should see" gives what it returned then. Queries 8–10 follow the team's *next* game, so they move on when the graph is rebuilt for a new week. How to open Neo4j Browser and run a query: [README](README.md).

## Before you start

**Turn off "Connect result nodes."** Browser Settings (the gear, bottom left) → untick *Connect result nodes*. When it's on, the Browser draws every stored relationship between the nodes on screen, not just the ones the query returned: Carson Wentz's career picture would get buried under 100+ `DEPTH_CHART` arrows to his teams.

**Don't double-click to explore.** Double-clicking a node *expands* it: the Browser fetches every neighbour of that node with its own query, ignoring the query's filters (up to its *Max Neighbours* setting, 100 by default), then draws the links between the new nodes and the ones already on screen. Justin Jefferson has 132 neighbours over 265 relationships; query 1 deliberately shows about 40 of them. Double-click him and his ~99 games (`APPEARED_IN`) and 113 depth-chart rows (`DEPTH_CHART`, all into the one Vikings node) flood in, and every teammate who played in those games grows arrows into them too. To undo it, re-run the query, or single-click the node and pick *collapse* in the ring that appears.

**Give nodes readable captions.** Open the panel with the page icon at the top right of the graph, click a label and pick its caption. The Browser remembers it.

| Label | Caption to pick | What it shows otherwise |
|---|---|---|
| `Player` | `name` | the GSIS id |
| `Game` | `game_id` | a score (it picks `away_score`), e.g. "34" |
| `UsageProfile` | `season` | the player's name, so his seasons look like duplicates of him |
| `PlayerProjection` | `target_label` | a long decimal (the baseline) |

**Typing names.** Player and coach names work with dashes or spaces, any capitals, and with or without dots and apostrophes: `Mike-Evans`, `mike evans`, `Amon-Ra-St-Brown`, `Jamarr-Chase`, `TJ-Hockenson`. A Jr. / Sr. / III can be left off (`Marvin-Harrison`). When two players share a name, the queries take the one with the most games, so `Justin-Jefferson` is the receiver, not the 2026 rookie linebacker. A typo returns nothing.

**Team codes** are nflverse's: `MIN`, `KC`, `LA` (Rams), `LAC`, `LV`, `WAS`, `JAX`, ...

## The queries

| # | Query | You type | Shows |
|---|---|---|---|
| 1 | [Everything around one player](#1-everything-around-one-player) | a player | his teams, QBs, injuries, usage profiles, projections, comparisons |
| 2 | [Career path](#2-career-path) | a player | every team, one arrow per season, draft and trades |
| 3 | [Passing family tree](#3-passing-family-tree) | a player | his QBs or receivers, and theirs |
| 4 | [Six degrees between two players](#4-six-degrees-between-two-players) | two players | the shortest chains of passes between them |
| 5 | [Similar-player web](#5-similar-player-web) | a player | whose usage his looks like, and who else looks like them |
| 6 | [A team's passing network](#6-a-teams-passing-network) | team + season | QB → receiver arrows for one season |
| 7 | [A team's coaching tree](#7-a-teams-coaching-tree) | team + first season | head coaches, coordinators and who they worked under |
| 8 | [Game-day map for the next game](#8-game-day-map-for-the-next-game) | a team | venue, coaches, predictions, expected QBs, pass targets, injuries |
| 9 | [Common opponents for the next game](#9-common-opponents-for-the-next-game) | a team | the opponents both teams have played, this season and last |
| 10 | [Revenge watch for the next game](#10-revenge-watch-for-the-next-game) | a team | players facing a team they used to play for |
| 11 | [Division web](#11-division-web) | team + season | every completed game in that team's division |
| 12 | [Coaching six degrees](#12-coaching-six-degrees) | two coaches | the shortest "worked under" chains between them |
| 13 | [College pipeline](#13-college-pipeline) | a school | where its first- and second-round picks went |
| 14 | [Trade map](#14-trade-map) | a season | from-team → player → new team |

---

## Players

### 1. Everything around one player
```cypher
// 1. Everything around one player. Type a name on the next line.
WITH 'Justin-Jefferson' AS typed
MATCH (p:Player)
WHERE replace(replace(replace(toLower(p.name), '-', ' '), '.', ''), "'", '') + ' '
      STARTS WITH replace(replace(replace(toLower(trim(typed)), '-', ' '), '.', ''), "'", '') + ' '
WITH p ORDER BY COUNT { (p)-[:APPEARED_IN]->() } DESC LIMIT 1
// Box scores and weekly depth-chart rows are left out: hundreds of them, they bury the picture.
MATCH (p)-[r]-(x)
WHERE NOT type(r) IN ['DEPTH_CHART', 'APPEARED_IN']
RETURN p, r, x
```
**You should see** 42 nodes around Jefferson:
- the Vikings: 7 `PLAYED_FOR` arrows (2020–2026) plus his draft (2020, pick 22) and his passing-network edge
- 10 QBs who have thrown to him, Cousins to Kyler Murray
- 12 games he was on the injury report for (the 2023 hamstring weeks, the 2026 week-4 ankle)
- his 7 usage profiles, one per season
- this week's 4 projections
- his comparisons: Davante Adams and Drake London (out of him), Chris Olave (into him)

Also: Thielen, K.J. Osborn, Dalvin Cook and Aaron Jones are there because Jefferson has thrown 8 passes himself on trick plays, one of them a 2022 completion to **Kirk Cousins for −2 yards**. That's why Cousins has arrows both ways.

### 2. Career path
```cypher
// 2. Career path: every team, one PLAYED_FOR arrow per season, plus draft and trades. Type a name on the next line.
WITH 'Carson-Wentz' AS typed
MATCH (p:Player)
WHERE replace(replace(replace(toLower(p.name), '-', ' '), '.', ''), "'", '') + ' '
      STARTS WITH replace(replace(replace(toLower(trim(typed)), '-', ' '), '.', ''), "'", '') + ' '
WITH p ORDER BY COUNT { (p)-[:APPEARED_IN]->() } DESC LIMIT 1
MATCH (p)-[r:PLAYED_FOR|DRAFTED_BY|TRADED_TO]->(t:Team)
RETURN p, r, t
```
**You should see** Wentz in the middle of 6 teams (Eagles, Colts, Commanders, Rams, Chiefs, Vikings): 9 `PLAYED_FOR` arrows, a `DRAFTED_BY` arrow to the Eagles and 2 `TRADED_TO`. Click a `PLAYED_FOR` arrow for that season's weeks and games. Rosters start in 2018, so earlier seasons don't show.

### 3. Passing family tree
```cypher
// 3. Passing family tree: his QBs (or receivers), then theirs. Seasons with 40+ targets only. Type a name on the next line.
WITH 'Patrick-Mahomes' AS typed
MATCH (p:Player)
WHERE replace(replace(replace(toLower(p.name), '-', ' '), '.', ''), "'", '') + ' '
      STARTS WITH replace(replace(replace(toLower(trim(typed)), '-', ' '), '.', ''), "'", '') + ' '
WITH p ORDER BY COUNT { (p)-[:APPEARED_IN]->() } DESC LIMIT 1
// Lower the 40 to see more players, raise it for fewer.
MATCH path = (p)-[t1:THREW_TO]-(:Player)-[t2:THREW_TO]-(:Player)
WHERE t1.targets >= 40 AND t2.targets >= 40
RETURN path
```
**You should see** 27 players. Kelce has an arrow per season; Tyreek Hill leads off to Tua; DeAndre Hopkins to Kyler Murray, Tannehill and Deshaun Watson; JuJu Smith-Schuster to Big Ben. `Justin-Jefferson` makes a bigger web: 54 players through Cousins, Darnold and Wentz. Click an arrow for that season's targets, yards, TDs and EPA.

### 4. Six degrees between two players
```cypher
// 4. Six degrees: the shortest chains of passes between two players. Type both names on the next line.
WITH 'Justin-Jefferson' AS player_a, 'Patrick-Mahomes' AS player_b
CALL (player_a) {
  MATCH (p:Player)
  WHERE replace(replace(replace(toLower(p.name), '-', ' '), '.', ''), "'", '') + ' '
        STARTS WITH replace(replace(replace(toLower(trim(player_a)), '-', ' '), '.', ''), "'", '') + ' '
  RETURN p AS a ORDER BY COUNT { (p)-[:APPEARED_IN]->() } DESC LIMIT 1
}
CALL (player_b) {
  MATCH (p:Player)
  WHERE replace(replace(replace(toLower(p.name), '-', ' '), '.', ''), "'", '') + ' '
        STARTS WITH replace(replace(replace(toLower(trim(player_b)), '-', ' '), '.', ''), "'", '') + ' '
  RETURN p AS b ORDER BY COUNT { (p)-[:APPEARED_IN]->() } DESC LIMIT 1
}
MATCH path = allShortestPaths((a)-[:THREW_TO*..10]-(b))
RETURN path
```
**You should see** Jefferson 3 passes from Mahomes by more than 20 routes (27 players), for example Jefferson ← Carson Wentz → Noah Gray ← Mahomes, from Wentz's 2024 season as Kansas City's backup. Others run through Kyler Murray → DeAndre Hopkins and Sam Darnold → Le'Veon Bell. `Amon-Ra-St-Brown` and `Tom-Brady` gives 20 players. Only passers and pass catchers are linked by passes, so a defender returns nothing.

### 5. Similar-player web
```cypher
// 5. Similar-player web (the GDS usage comparisons): whose usage his looks like, and who else looks like them.
//    Type a name on the next line (a WR, TE or RB with a usage profile this season).
WITH 'Justin-Jefferson' AS typed
MATCH (p:Player)
WHERE replace(replace(replace(toLower(p.name), '-', ' '), '.', ''), "'", '') + ' '
      STARTS WITH replace(replace(replace(toLower(trim(typed)), '-', ' '), '.', ''), "'", '') + ' '
WITH p ORDER BY COUNT { (p)-[:APPEARED_IN]->() } DESC LIMIT 1
MATCH path = (p)-[:SIMILAR_TO]-(:Player)-[:SIMILAR_TO]-(:Player)
RETURN path
```
**You should see** 17 receivers. Jefferson → Davante Adams and Drake London; Adams links on to CeeDee Lamb, Tyreek Hill, Mike Evans and Michael Wilson. Each arrow reads "this player's current-season usage looks like that player's season X"; click it for the season and the score. The [graph data science guide](../guides/graph-data-science.md) explains how the comparisons are made.

---

## Teams

### 6. A team's passing network
```cypher
// 6. A team's passing network for one season: QB -> receiver arrows. Type a team code and a season on the next line.
WITH 'MIN' AS team, 2026 AS season
MATCH (qb:Player)-[t:THREW_TO {season: season}]->(r:Player)
WHERE t.team_id = team
RETURN qb, t, r
```
**You should see** for 2026 two hubs, Kyler Murray and Carson Wentz, feeding 11 pass catchers (13 players, 17 arrows). With `2022`: Cousins → Jefferson, Thielen and Hockenson in the 13-4 season (16 players). Click an arrow for targets, yards, TDs and EPA.

### 7. A team's coaching tree
```cypher
// 7. A team's coaching tree: head coaches and coordinators since a season, and who they worked under before.
//    Type a team code and the first season on the next line.
WITH 'MIN' AS team, 2018 AS since
MATCH (c:Coach)-[r:HEAD_COACH_OF|COORDINATOR_OF]->(t:Team {team_id: team})
WHERE r.season >= since
OPTIONAL MATCH (c)-[w:WORKED_UNDER]->(boss:Coach)
RETURN c, r, t, w, boss
```
**You should see** 15 Vikings head coaches and coordinators since 2018 around the team (Zimmer, O'Connell, Stefanski, both Kubiaks, Flores, Wes Phillips, ...), one arrow per season, and `WORKED_UNDER` arrows out to 18 coaches they worked under elsewhere: O'Connell's lead to Sean McVay, Mike Priefer has six, Klint Kubiak's reach Mike Macdonald. Head coaches start in 2018; coordinators go back to 2006 (lower `since` to see them).

### 8. Game-day map for the next game
```cypher
// 8. Game-day map for a team's next game: venue, head coaches, the model's predictions, expected QBs,
//    each team's top pass targets and anyone out, and the injury report. Type a team code on the next line.
WITH 'MIN' AS team
MATCH (:Team {team_id: team})-[:PLAYED_IN]->(g:Game)
WHERE g.completed = false
WITH g ORDER BY g.kickoff LIMIT 1
MATCH (t:Team)-[pi:PLAYED_IN]->(g)
OPTIONAL MATCH (g)-[at:AT]->(v:Venue)
OPTIONAL MATCH (g)-[hp:HAS_PREDICTION]->(gp:GamePrediction)
OPTIONAL MATCH (c:Coach)-[ci:COACHED_IN]->(g)
OPTIONAL MATCH (qb:Player)-[qf:PLAYED_FOR {season: g.season}]->(t)
  WHERE qb.player_id IN [g.home_qb_expected, g.away_qb_expected]
OPTIONAL MATCH (hub:Player)-[pc:PASS_CENTRALITY {season: g.season}]->(t)
  WHERE pc.rank <= 2 OR pc.out_this_week
OPTIONAL MATCH (hurt:Player)-[inj:ON_INJURY_REPORT]->(g)
  WHERE inj.status IS NOT NULL
RETURN g, t, pi, v, at, gp, hp, c, ci, qb, qf, hub, pc, hurt, inj
```
**You should see** Vikings at Saints around the game node: the Superdome, O'Connell and Kellen Moore, the two model predictions (click one for the home win probability), the expected QBs Kyler Murray and Tyler Shough, and each team's top pass targets plus anyone out (Hockenson and Addison; Jefferson and Josh Oliver with `out_this_week: true`; Chris Olave, Devaughn Vele and Travis Etienne for New Orleans). 17 nodes. Players on the week's injury report point at the game once the graph has that week's report: the week-5 graph was built before it came out, so it has none.

### 9. Common opponents for the next game
```cypher
// 9. Common opponents for a team's next game: teams both sides have played, this season and last.
//    Type a team code on the next line.
WITH 'MIN' AS team
MATCH (a:Team {team_id: team})-[:PLAYED_IN]->(next:Game)<-[:PLAYED_IN]-(b:Team)
WHERE next.completed = false
WITH a, b, next ORDER BY next.kickoff LIMIT 1
// Last season is included: early in a season most matchups have no common opponent yet.
MATCH (a)-[r1:PLAYED_IN]->(g1:Game)<-[o1:PLAYED_IN]-(opp:Team)-[o2:PLAYED_IN]->(g2:Game)<-[r2:PLAYED_IN]-(b)
WHERE g1.completed AND g2.completed
  AND g1.season >= next.season - 1 AND g2.season >= next.season - 1
  AND opp <> a AND opp <> b
RETURN a, r1, g1, o1, opp, o2, g2, r2, b
```
**You should see** the Vikings on one side and the Saints on the other, linked through 8 shared opponents (Falcons, Ravens, Bears, Lions, Dolphins, Giants, Seahawks, Buccaneers) and 22 games. Click a `PLAYED_IN` arrow for that team's score, result and EPA in the game.

### 10. Revenge watch for the next game
```cypher
// 10. Revenge watch: players in a team's next game who used to play for the other side.
//     Type a team code on the next line.
WITH 'MIN' AS team
MATCH (:Team {team_id: team})-[:PLAYED_IN]->(g:Game)
WHERE g.completed = false
WITH g ORDER BY g.kickoff LIMIT 1
MATCH (cur:Team)-[:PLAYED_IN]->(g)<-[:PLAYED_IN]-(old:Team)
MATCH (p:Player)-[now:PLAYED_FOR {season: g.season}]->(cur)
MATCH (p)-[was:PLAYED_FOR]->(old)
WHERE was.season < g.season
RETURN p, now, cur, was, old
```
**You should see** 7 players between the Vikings and the Saints (Ryan Wright, Ty Chandler, Daniel Carlson, Eric Wilson, Jonah Williams, Audric Estimé, Devin Neal), each with this season's arrow to his team and one arrow per old season to the other.

### 11. Division web
```cypher
// 11. Division web: every completed game in a team's division for one season.
//     Type a team code and a season on the next line.
WITH 'MIN' AS team, 2026 AS season
MATCH (me:Team {team_id: team})
MATCH (t:Team {division: me.division})-[pi:PLAYED_IN]->(g:Game {season: season})
WHERE g.completed
MATCH (opp:Team)-[oi:PLAYED_IN]->(g)
WHERE opp <> t
RETURN t, pi, g, opp, oi
```
**You should see** the NFC North's 14 games so far (26 nodes), with the 4 division teams in the middle and the games between them (Vikings–Bears, Vikings–Packers in week 1) sitting between their two teams. A past season gives a much bigger web.

---

## Fun extras

### 12. Coaching six degrees
```cypher
// 12. Coaching six degrees: the shortest chains of "worked under" links between two coaches.
//     Type both names on the next line.
WITH "Kevin-O'Connell" AS coach_a, 'Andy-Reid' AS coach_b
MATCH (a:Coach), (b:Coach)
WHERE replace(replace(replace(toLower(a.name), '-', ' '), '.', ''), "'", '')
        = replace(replace(replace(toLower(trim(coach_a)), '-', ' '), '.', ''), "'", '')
  AND replace(replace(replace(toLower(b.name), '-', ' '), '.', ''), "'", '')
        = replace(replace(replace(toLower(trim(coach_b)), '-', ' '), '.', ''), "'", '')
MATCH path = allShortestPaths((a)-[:WORKED_UNDER*..8]-(b))
RETURN path
```
**You should see** O'Connell 4 steps from Andy Reid by 4 routes (12 coaches), for example O'Connell – Sean McVay – Liam Coen – Todd Bowles – Reid. `Kyle-Shanahan` and `Bill-Belichick` gives 13 coaches, through Josh McDaniels, Matt Patricia and John Harbaugh. The links come from the coaching seed (head coaches since 2018, coordinators since 2006); the [knowledge-graph guide](../guides/knowledge-graph.md) explains it.

### 13. College pipeline
```cypher
// 13. College pipeline: where a school's first- and second-round picks went (2018 on). Type a school on the next line.
WITH 'LSU' AS school
MATCH (p:Player)-[d:DRAFTED_BY]->(t:Team)
WHERE toLower(p.college) CONTAINS toLower(school) AND d.year >= 2018 AND d.round <= 2
RETURN p, d, t
```
**You should see** 25 LSU players across 16 teams, Joe Burrow and Ja'Marr Chase both pointing at the Bengals, Jefferson at the Vikings, Jayden Daniels at the Commanders, Malik Nabers at the Giants. `Alabama` gives 44 players across 21 teams. The school is matched as text, so `Alabama` would also catch an "Alabama State" or "Alabama A&M".

### 14. Trade map
```cypher
// 14. Trade map: from-team -> player -> new team, for one season's trades. Type a season on the next line.
WITH 2026 AS season
MATCH (p:Player)-[r:TRADED_TO]->(t:Team)
WHERE r.season = season
MATCH (f:Team {team_id: r.from_team})
// TRADED_AWAY is drawn on the fly by APOC for this picture only; nothing is written to the graph.
RETURN p, r, t, f, apoc.create.vRelationship(f, 'TRADED_AWAY', {date: r.date}, p) AS traded_away
```
**You should see** 26 trades from the 2026 offseason (49 nodes), for example Jaylen Waddle from the Dolphins to the Broncos and DJ Moore from the Bears to the Bills, plus a few swaps where two teams sent players to each other. Click a `TRADED_TO` arrow for the date. Trades of draft picks aren't in the graph.
