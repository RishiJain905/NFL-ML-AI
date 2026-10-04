// Q0 starters out (not an insight: feeds the digest's code-written "Starters out" list).
// Regular starters (>= 2 games this season at >= 50% of snaps, the latest within 3 weeks of
// the team's last game) who won't play this week, with the same time-scoped sources as Q2:
//   this_week: on this week's report as Out / Doubtful (a Saturday run; a Tuesday has none)
//   reserve:   on a reserve list (IR) at the latest roster week and missed the last game
//   last_week: no report yet this week, ruled Out of the team's last game and didn't play
// Unlike Q2 it needs no with / without history and includes QBs. Params: $season, $week.
MATCH (g:Game {season: $season, week: $week})<-[:PLAYED_IN]-(t:Team)
MATCH (g)<-[:PLAYED_IN]-(opp:Team)
WHERE opp <> t
CALL (t) {
  MATCH (t)-[:PLAYED_IN]->(lg:Game {season: $season})
  WHERE lg.week < $week AND lg.completed
  RETURN lg
  ORDER BY lg.week DESC
  LIMIT 1
}
WITH g, t, opp, lg,
     EXISTS { MATCH (:Player)-[x:ON_INJURY_REPORT]->(g) WHERE x.team_id = t.team_id } AS has_report
MATCH (s:Player)-[cur:PLAYED_FOR {season: $season}]->(t)
WHERE cur.last_week >= $week - 1 AND s.position_group <> 'SPEC'
OPTIONAL MATCH (s)-[now:ON_INJURY_REPORT]->(g)
OPTIONAL MATCH (s)-[prev:ON_INJURY_REPORT]->(lg)
OPTIONAL MATCH (s)-[lapp:APPEARED_IN]->(lg)
WITH g, t, opp, lg, s, cur, now, prev, has_report,
     lapp IS NOT NULL AND coalesce(lapp.snaps, 1) > 0 AS played_last
WITH g, t, opp, lg, s, now, prev,
     CASE
       WHEN now IS NOT NULL AND now.status IN ['Out', 'Doubtful'] THEN 'this_week'
       WHEN cur.status_last = 'RES' AND NOT played_last THEN 'reserve'
       WHEN NOT has_report AND prev IS NOT NULL AND prev.status = 'Out' AND NOT played_last
         THEN 'last_week'
     END AS out_source
WHERE out_source IS NOT NULL
CALL (s, t) {
  MATCH (s)-[a:APPEARED_IN]->(sg:Game {season: $season})
  WHERE a.team_id = t.team_id AND a.snap_pct >= 0.5 AND sg.week < $week
  RETURN count(a) AS starts, max(sg.week) AS last_start, avg(a.snap_pct) AS snap_pct
}
WITH g, t, opp, lg, s, now, prev, out_source, starts, last_start, snap_pct
WHERE starts >= 2 AND last_start >= lg.week - 3
RETURN g.game_id AS game_id,
       t.team_id AS team,
       opp.team_id AS opponent,
       s.player_id AS player_id,
       s.name AS player,
       s.position AS position,
       s.position_group AS position_group,
       out_source,
       coalesce(now.status, prev.status) AS status,
       coalesce(now.body_part, prev.body_part) AS body_part,
       starts,
       round(snap_pct, 3) AS snap_pct
ORDER BY team, snap_pct DESC, player_id
