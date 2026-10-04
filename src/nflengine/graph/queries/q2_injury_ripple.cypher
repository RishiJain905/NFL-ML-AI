// Q2 injury ripple (documentation/05 -> Query library): a regular starter who won't play
// this week -> who stepped in when he missed games before, and how the team did without
// him. Multi-hop: Player -ON_INJURY_REPORT-> Game, Player -PLAYED_FOR-> Team -PLAYED_IN->
// past Games (with / without him), teammates -APPEARED_IN-> those Games, DEPTH_CHART.
//
// "Won't play" (time-scoped, said exactly that way in the digest):
//   this_week: on this week's report as Out / Doubtful (a Saturday run; a Tuesday has none)
//   reserve:   on a reserve list (IR) at the latest roster week and missed the last game
//   last_week: no report yet this week, ruled Out of the team's last game and didn't play
// "Regular starter": >= 2 games this season at >= 50% of snaps, the latest within 3 weeks
// of the team's last game. QBs are Q3's.
// With / without: the team's regular-season games over the last $history_seasons seasons
// he was on the roster (the actual roster weeks, so a stint away isn't "without"), from
// his first start in that window on: "with" = he played >= 50% of snaps, "without" = he
// didn't play (no appearance, or 0 offense / defense snaps). The teammate who stepped in = same position group, still on the team, with the
// biggest usage jump in those games (targets for WR / TE, carries + targets for RB, snap
// share otherwise). Params: $season, $week, $groups, $history_seasons.
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
WHERE s.position_group IN $groups AND cur.last_week >= $week - 1
OPTIONAL MATCH (s)-[now:ON_INJURY_REPORT]->(g)
OPTIONAL MATCH (s)-[prev:ON_INJURY_REPORT]->(lg)
OPTIONAL MATCH (s)-[lapp:APPEARED_IN]->(lg)
WITH g, t, opp, lg, s, cur, now, prev,
     // a box score without a snap record (snaps unknown) still means he played
     lapp IS NOT NULL AND coalesce(lapp.snaps, 1) > 0 AS played_last,
     has_report
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
// past games with / without him, from his first start in the window on (games before he
// was a starter are a backup's games, not missed starts)
CALL (s, t) {
  MATCH (s)-[a0:APPEARED_IN]->(g0:Game)
  WHERE a0.team_id = t.team_id AND a0.snap_pct >= 0.5 AND g0.game_type = 'REG'
    AND g0.season >= $season - $history_seasons
  WITH min(g0.season * 100 + g0.week) AS first_start
  MATCH (s)-[pf:PLAYED_FOR]->(t)
  WHERE pf.season >= $season - $history_seasons AND pf.first_week IS NOT NULL
  MATCH (t)-[pi:PLAYED_IN]->(hg:Game {season: pf.season})
  WHERE hg.completed AND hg.game_type = 'REG'
    AND hg.week IN pf.roster_weeks
    AND hg.season * 100 + hg.week >= first_start
    AND (hg.season < $season OR hg.week < $week)
  OPTIONAL MATCH (s)-[sa:APPEARED_IN]->(hg)
  WITH hg, pi,
       // unknown snaps (a box score without a snap record) are neither
       CASE
         WHEN sa IS NULL OR sa.snaps = 0 THEN 'without'
         WHEN sa.snap_pct >= 0.5 THEN 'with'
       END AS split
  WHERE split IS NOT NULL
  RETURN collect(CASE WHEN split = 'with' THEN hg.game_id END) AS with_ids,
         collect(CASE WHEN split = 'without' THEN hg.game_id END) AS without_ids,
         avg(CASE WHEN split = 'with' THEN pi.epa_per_play END) AS off_with,
         avg(CASE WHEN split = 'without' THEN pi.epa_per_play END) AS off_without,
         avg(CASE WHEN split = 'with' THEN pi.epa_allowed END) AS def_with,
         avg(CASE WHEN split = 'without' THEN pi.epa_allowed END) AS def_without,
         avg(CASE WHEN split = 'with' THEN pi.points END) AS pts_with,
         avg(CASE WHEN split = 'without' THEN pi.points END) AS pts_without,
         avg(CASE WHEN split = 'with' THEN pi.points_allowed END) AS pa_with,
         avg(CASE WHEN split = 'without' THEN pi.points_allowed END) AS pa_without
}
WITH g, t, opp, s, now, prev, out_source, starts, snap_pct, with_ids, without_ids,
     off_with, off_without, def_with, def_without, pts_with, pts_without, pa_with, pa_without
WHERE size(without_ids) >= 1 AND size(with_ids) >= 1
// the teammate who stepped in
OPTIONAL CALL (s, t, g, with_ids, without_ids) {
  MATCH (b:Player)-[cb:PLAYED_FOR {season: $season}]->(t)
  WHERE b <> s AND b.position_group = s.position_group AND cb.last_week >= $week - 1
    AND NOT EXISTS {
      MATCH (b)-[r:ON_INJURY_REPORT]->(g) WHERE r.status IN ['Out', 'Doubtful']
    }
  MATCH (b)-[ba:APPEARED_IN]->(bg:Game)
  WHERE bg.game_id IN without_ids OR bg.game_id IN with_ids
  WITH b, bg.game_id IN without_ids AS wo, ba,
       CASE
         WHEN s.position_group IN ['WR', 'TE'] THEN toFloat(coalesce(ba.targets, 0))
         WHEN s.position_group = 'RB'
           THEN toFloat(coalesce(ba.carries, 0) + coalesce(ba.targets, 0))
         ELSE coalesce(ba.snap_pct, 0.0)
       END AS usage,
       toFloat(coalesce(ba.rec_yds, 0) + coalesce(ba.rush_yds, 0)) AS yds
  WITH b,
       avg(CASE WHEN wo THEN usage END) AS use_without,
       avg(CASE WHEN NOT wo THEN usage END) AS use_with,
       count(CASE WHEN wo THEN 1 END) AS n_b_without,
       count(CASE WHEN NOT wo THEN 1 END) AS n_b_with,
       avg(CASE WHEN wo THEN yds END) AS yds_without,
       avg(CASE WHEN NOT wo THEN yds END) AS yds_with
  WHERE n_b_without >= 1 AND n_b_with >= 1
  RETURN b, use_without, use_with, n_b_without, n_b_with, yds_without, yds_with
  ORDER BY use_without - use_with DESC, b.player_id
  LIMIT 1
}
// who is next on his depth-chart slot (information; the chart format changed in 2025)
OPTIONAL CALL (s, t) {
  MATCH (s)-[sd:DEPTH_CHART]->(t)
  WITH sd
  ORDER BY sd.season DESC, sd.week DESC, sd.rank
  LIMIT 1
  MATCH (n:Player)-[nd:DEPTH_CHART {season: sd.season, week: sd.week, position: sd.position}]->(t)
  WHERE nd.rank > sd.rank
  RETURN n AS chart_next, sd.position AS chart_position
  ORDER BY nd.rank, n.player_id
  LIMIT 1
}
WITH *,
     s.position_group IN ['DL', 'LB', 'DB'] AS defense,
     size(without_ids) AS n_without,
     size(with_ids) AS n_with
WITH *,
     CASE WHEN defense THEN def_with ELSE off_with END AS team_with,
     CASE WHEN defense THEN def_without ELSE off_without END AS team_without,
     // a skill player's usage jump is news; a lineman's backup playing every snap isn't
     CASE
       WHEN b IS NULL OR NOT s.position_group IN ['WR', 'TE', 'RB'] THEN 0.0
       ELSE (use_without - use_with) / 4.0
         * CASE WHEN n_b_without >= 3 THEN 1.0 ELSE n_b_without / 3.0 END
     END AS jump
// strength: how big a piece he is (snap share), how much the team changed without him
// (weighted by how many such games exist), and the skill teammate's usage jump
WITH *,
     0.2
     + 0.2 * CASE WHEN snap_pct >= 0.9 THEN 1.0 ELSE (snap_pct - 0.5) / 0.4 END
     + 0.45 * CASE
         WHEN team_with IS NULL OR team_without IS NULL THEN 0.0
         WHEN abs(team_without - team_with) >= 0.15 THEN 1.0
         ELSE abs(team_without - team_with) / 0.15
       END * CASE WHEN n_without >= 6 THEN 1.0 ELSE n_without / 6.0 END
     + 0.15 * CASE WHEN jump >= 1.0 THEN 1.0 WHEN jump <= 0.0 THEN 0.0 ELSE jump END
     AS strength
RETURN 'injury_ripple' AS insight_type,
       g.game_id AS game_id,
       t.team_id AS team,
       opp.team_id AS opponent,
       s.player_id AS starter_id,
       s.name AS starter,
       s.position AS position,
       s.position_group AS position_group,
       defense,
       out_source,
       coalesce(now.status, prev.status) AS status,
       coalesce(now.body_part, prev.body_part) AS body_part,
       starts,
       round(snap_pct, 3) AS snap_pct,
       n_with,
       n_without,
       round(team_with, 4) AS team_epa_with,
       round(team_without, 4) AS team_epa_without,
       round(CASE WHEN defense THEN pa_with ELSE pts_with END, 2) AS team_points_with,
       round(CASE WHEN defense THEN pa_without ELSE pts_without END, 2) AS team_points_without,
       b.player_id AS backup_id,
       b.name AS backup,
       round(use_with, 3) AS backup_use_with,
       round(use_without, 3) AS backup_use_without,
       n_b_with AS backup_n_with,
       n_b_without AS backup_n_without,
       round(yds_with, 2) AS backup_yds_with,
       round(yds_without, 2) AS backup_yds_without,
       chart_next.player_id AS chart_next_id,
       chart_next.name AS chart_next,
       chart_position,
       n_without AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, starter_id
