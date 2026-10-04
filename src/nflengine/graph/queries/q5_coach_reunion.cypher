// Q5 coach reunion: which of this week's head coaches faces a team he used to be the head
// coach of (in an earlier season since the graph's first season)? Multi-hop:
//   (Coach)-[:COACHED_IN {team_id}]->(this week's Game)<-[:PLAYED_IN]-(old Team)
//   (Coach)-[:HEAD_COACH_OF {season < this season}]->(old Team)
// plus his record WITH the old team ((Coach)-[:COACHED_IN {team_id: old}]->(Game)
// <-[:PLAYED_IN {result}]-(old Team)), split regular season / playoffs, and his record
// AGAINST it as another team's head coach ((Coach)-[:COACHED_IN]->(Game)<-[:PLAYED_IN]-
// (his Team), (old Team)-[:PLAYED_IN]->(that Game)), with the meetings since he left.
// Completed games only (results are visible strictly before week W). Strength grows with
// how recent the stint was and how long it lasted, plus a little for a first meeting since.
// Params: $season, $week.
MATCH (c:Coach)-[ci:COACHED_IN]->(g:Game {season: $season, week: $week})
MATCH (t:Team {team_id: ci.team_id})-[:PLAYED_IN]->(g)<-[:PLAYED_IN]-(opp:Team)
WHERE opp <> t
MATCH (c)-[h:HEAD_COACH_OF]->(opp)
WHERE h.season < $season
WITH g, c, t, opp, collect(DISTINCT h.season) AS seasons_with_opp, max(h.season) AS last_season
CALL (c, opp) {
  MATCH (c)-[x:COACHED_IN]->(og:Game)<-[p:PLAYED_IN]-(opp)
  WHERE x.team_id = opp.team_id AND og.completed
  RETURN sum(CASE WHEN og.game_type = 'REG' AND p.result = 'W' THEN 1 ELSE 0 END) AS reg_w,
         sum(CASE WHEN og.game_type = 'REG' AND p.result = 'L' THEN 1 ELSE 0 END) AS reg_l,
         sum(CASE WHEN og.game_type = 'REG' AND p.result = 'T' THEN 1 ELSE 0 END) AS reg_t,
         sum(CASE WHEN og.game_type <> 'REG' AND p.result = 'W' THEN 1 ELSE 0 END) AS post_w,
         sum(CASE WHEN og.game_type <> 'REG' AND p.result = 'L' THEN 1 ELSE 0 END) AS post_l
}
CALL (c, opp, last_season) {
  MATCH (c)-[x:COACHED_IN]->(vg:Game)<-[p:PLAYED_IN]-(mine:Team)
  WHERE mine.team_id = x.team_id AND x.team_id <> opp.team_id AND vg.completed
    AND EXISTS { (opp)-[:PLAYED_IN]->(vg) }
  RETURN sum(CASE p.result WHEN 'W' THEN 1 ELSE 0 END) AS vs_w,
         sum(CASE p.result WHEN 'L' THEN 1 ELSE 0 END) AS vs_l,
         sum(CASE p.result WHEN 'T' THEN 1 ELSE 0 END) AS vs_t,
         sum(CASE WHEN vg.season > last_season THEN 1 ELSE 0 END) AS meetings_since
}
WITH g, c, t, opp, seasons_with_opp, last_season, reg_w, reg_l, reg_t, post_w, post_l,
     vs_w, vs_l, vs_t, meetings_since,
     size(seasons_with_opp) AS n_seasons,
     $season - last_season AS years_since
WITH *,
     0.3
     + 0.35 * CASE WHEN years_since <= 1 THEN 1.0
                   WHEN years_since >= 6 THEN 0.15
                   ELSE 1.0 - 0.17 * (years_since - 1) END
     + 0.25 * CASE WHEN n_seasons >= 5 THEN 1.0 ELSE n_seasons / 5.0 END
     + 0.1 * CASE WHEN meetings_since = 0 THEN 1.0 ELSE 0.0 END AS strength
RETURN 'coach_reunion' AS insight_type,
       g.game_id AS game_id,
       t.team_id AS team,
       opp.team_id AS opponent,
       c.coach_id AS coach_id,
       c.name AS coach,
       seasons_with_opp,
       last_season,
       n_seasons,
       reg_w, reg_l, reg_t, post_w, post_l,
       vs_w, vs_l, vs_t,
       meetings_since,
       reg_w + reg_l + reg_t + post_w + post_l AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, game_id
