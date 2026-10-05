// Q10 passing-network star out (documentation/05 -> Q10; GDS): a pass catcher central to his
// team's passing network this season (top $max_rank by PageRank share among its receivers,
// >= $min_share) won't play this week -> who leads the network without him? Both come from
// graph/gds.py::pass_network (PASS_CENTRALITY): PageRank on the season's network, then on this
// week's network (the same targets without the pass catchers who won't play, Q0's rules).
//   (star)-[:PASS_CENTRALITY {season: S, out_this_week}]->(Team)-[:PLAYED_IN]->(this week's Game)
//   (leader)-[:PASS_CENTRALITY {rank_this_week: 1}]->(Team)
// "Won't play" is re-derived here for the wording (Q0 / Q2's time-scoped sources: listed Out /
// Doubtful this week, reserve list and missed the last game, or ruled out of the last game
// before this week's report). One row per team: its most central player who's out; the
// others out are listed. The network is the season's targets as of the build (THREW_TO
// strictly before week W). Params: $season, $week, $min_share, $max_rank, $min_games.
MATCH (g:Game {season: $season, week: $week})<-[:PLAYED_IN]-(t:Team)
MATCH (g)<-[:PLAYED_IN]-(opp:Team)
WHERE opp <> t
MATCH (h:Player)-[hc:PASS_CENTRALITY {season: $season}]->(t)
WHERE hc.role = 'receiver' AND hc.out_this_week
WITH g, t, opp, h, hc
ORDER BY hc.rank
WITH g, t, opp, collect({h: h, hc: hc}) AS outs
WITH g, t, opp, outs[0].h AS h, outs[0].hc AS hc, [x IN outs[1..] | x.h.name] AS also_out
WHERE hc.rank <= $max_rank AND hc.share >= $min_share
CALL (t) {
  MATCH (t)-[:PLAYED_IN]->(lg:Game {season: $season})
  WHERE lg.week < $week AND lg.completed
  RETURN lg
  ORDER BY lg.week DESC
  LIMIT 1
}
WITH g, t, opp, h, hc, also_out, lg,
     EXISTS { MATCH (:Player)-[x:ON_INJURY_REPORT]->(g) WHERE x.team_id = t.team_id } AS has_report
OPTIONAL MATCH (h)-[cur:PLAYED_FOR {season: $season}]->(t)
OPTIONAL MATCH (h)-[now:ON_INJURY_REPORT]->(g)
OPTIONAL MATCH (h)-[prev:ON_INJURY_REPORT]->(lg)
OPTIONAL MATCH (h)-[lapp:APPEARED_IN]->(lg)
WITH g, t, opp, h, hc, also_out, now, prev,
     lapp IS NOT NULL AND coalesce(lapp.snaps, 1) > 0 AS played_last,
     cur, has_report
WITH g, t, opp, h, hc, also_out, now, prev,
     CASE
       WHEN now IS NOT NULL AND now.status IN ['Out', 'Doubtful'] THEN 'this_week'
       WHEN cur.status_last = 'RES' AND NOT played_last THEN 'reserve'
       WHEN NOT has_report AND prev IS NOT NULL AND prev.status = 'Out' AND NOT played_last
         THEN 'last_week'
     END AS out_source
WHERE out_source IS NOT NULL
CALL (h, t) {
  MATCH (h)-[a:APPEARED_IN]->(x:Game {season: $season})
  WHERE a.team_id = t.team_id AND x.week < $week
  RETURN count(a) AS hub_games
}
WITH g, t, opp, h, hc, also_out, now, prev, out_source, hub_games
WHERE hub_games >= $min_games
MATCH (n:Player)-[nc:PASS_CENTRALITY {season: $season}]->(t)
WHERE nc.role = 'receiver' AND nc.rank_this_week = 1
CALL (t) {
  MATCH (t)-[p:PLAYED_IN]->(x:Game {season: $season})
  WHERE x.week < $week AND x.completed
  RETURN count(p) AS team_games
}
WITH *,
     0.35
     + 0.3 * CASE WHEN hc.share >= 0.3 THEN 1.0
                  ELSE (hc.share - $min_share) / (0.3 - $min_share) END
     + 0.1 * CASE hc.rank WHEN 1 THEN 1.0 WHEN 2 THEN 0.5 ELSE 0.0 END
     + 0.1 * CASE WHEN out_source = 'this_week' THEN 1.0 ELSE 0.5 END
     + 0.1 * CASE WHEN team_games >= 6 THEN 1.0 ELSE team_games / 6.0 END AS strength
RETURN 'network_hub' AS insight_type,
       g.game_id AS game_id,
       t.team_id AS team,
       opp.team_id AS opponent,
       h.player_id AS hub_id,
       h.name AS hub,
       h.position AS hub_position,
       out_source,
       coalesce(now.status, prev.status) AS status,
       coalesce(now.body_part, prev.body_part) AS body_part,
       round(hc.share, 4) AS hub_share,
       hc.rank AS hub_rank,
       round(hc.degree) AS hub_targets,
       hub_games,
       also_out,
       n.player_id AS next_id,
       n.name AS next,
       n.position AS next_position,
       round(nc.share, 4) AS next_share,
       nc.rank AS next_rank,
       round(nc.share_this_week, 4) AS next_share_without,
       round(nc.degree) AS next_targets,
       team_games,
       team_games AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, game_id, team
