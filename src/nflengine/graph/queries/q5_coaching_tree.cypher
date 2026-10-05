// Q5 coaching tree (documentation/05 -> Q5; decisions-log open question Q06): links between
// this week's coaching staffs that only the optional hand-made seed knows
// (`config/coaching_seed.csv` -> COORDINATOR_OF, WORKED_UNDER; graph/tables_extra.py). With
// no seed there are no such relationships and the query returns nothing. Two patterns:
//   coordinator_vs_boss: a coordinator this season faces the head coach he used to work
//     under: (c)-[:COORDINATOR_OF {season: S}]->(his Team)-[:PLAYED_IN]->(g)
//     <-[:COACHED_IN]-(boss), (c)-[:WORKED_UNDER]->(boss)
//   head_coach_tree: the two head coaches are 1-2 WORKED_UNDER hops apart in either
//     direction (one worked under the other, or both under the same mentor):
//     shortestPath((h1)-[:WORKED_UNDER*1..2]-(h2))
// Head coaches come from COACHED_IN (the schedules). `steps` lists each WORKED_UNDER hop
// (who worked under whom, seasons, teams) so the text is written by code.
// Strength: a direct link > a shared mentor; more recent and longer links rank higher.
// Params: $season, $week.
MATCH (t:Team)-[:PLAYED_IN]->(g:Game {season: $season, week: $week})<-[:PLAYED_IN]-(opp:Team)
WHERE t <> opp
MATCH (boss:Coach)-[bi:COACHED_IN]->(g)
WHERE bi.team_id = opp.team_id
MATCH (c:Coach)-[co:COORDINATOR_OF {season: $season}]->(t)
MATCH (c)-[w:WORKED_UNDER]->(boss)
WHERE c <> boss
WITH g, t, opp, c, co, boss, w, $season - w.seasons[-1] AS years_since
RETURN 'coaching_tree' AS insight_type,
       'coordinator_vs_boss' AS kind,
       g.game_id AS game_id,
       t.team_id AS team,
       opp.team_id AS opponent,
       c.coach_id AS coach_id,
       c.name AS coach,
       co.role AS role,
       boss.coach_id AS other_id,
       boss.name AS other,
       [{from: c.name, to: boss.name, seasons: w.seasons, teams: w.teams}] AS steps,
       size(w.seasons) AS sample_size,
       round(0.45
             + 0.2 * CASE WHEN years_since <= 1 THEN 1.0 WHEN years_since >= 6 THEN 0.2
                          ELSE 1.0 - 0.16 * (years_since - 1) END
             + 0.15 * CASE WHEN size(w.seasons) >= 3 THEN 1.0 ELSE size(w.seasons) / 3.0 END,
             3) AS strength
UNION ALL
MATCH (t:Team)-[pt:PLAYED_IN]->(g:Game {season: $season, week: $week})<-[:PLAYED_IN]-(opp:Team)
WHERE pt.home AND t <> opp
MATCH (h1:Coach)-[c1:COACHED_IN]->(g)
WHERE c1.team_id = t.team_id
MATCH (h2:Coach)-[c2:COACHED_IN]->(g)
WHERE c2.team_id = opp.team_id AND h1 <> h2
MATCH path = shortestPath((h1)-[:WORKED_UNDER*1..2]-(h2))
WITH g, t, opp, h1, h2, path, relationships(path) AS rels,
     reduce(m = 0, r IN relationships(path) | CASE WHEN r.seasons[-1] > m THEN r.seasons[-1]
                                                ELSE m END) AS last_season,
     reduce(n = 0, r IN relationships(path) | n + size(r.seasons)) AS n_seasons
RETURN 'coaching_tree' AS insight_type,
       'head_coach_tree' AS kind,
       g.game_id AS game_id,
       t.team_id AS team,
       opp.team_id AS opponent,
       h1.coach_id AS coach_id,
       h1.name AS coach,
       'HC' AS role,
       h2.coach_id AS other_id,
       h2.name AS other,
       [r IN rels | {from: startNode(r).name, to: endNode(r).name, seasons: r.seasons,
                     teams: r.teams}] AS steps,
       n_seasons AS sample_size,
       round(CASE WHEN size(rels) = 1 THEN 0.55 ELSE 0.4 END
             + 0.2 * CASE WHEN $season - last_season <= 2 THEN 1.0
                          WHEN $season - last_season >= 8 THEN 0.2
                          ELSE 1.0 - 0.13 * ($season - last_season - 2) END
             + 0.1 * CASE WHEN n_seasons >= 4 THEN 1.0 ELSE n_seasons / 4.0 END,
             3) AS strength
