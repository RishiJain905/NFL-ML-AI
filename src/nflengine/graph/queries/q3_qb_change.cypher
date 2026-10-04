// Q3 QB-change ripple (documentation/05 -> Query library): the QB expected to start this
// week (`Game.home_qb_expected` / `away_qb_expected`: the game model's Tuesday rule, plus
// the live schedule / depth chart / injury report in a live run) differs from the team's
// main starter this season (most games as the team's main QB, `APPEARED_IN.qb_started`).
// Then: which of the team's current receivers have history with the expected QB
// (THREW_TO, any season, any team) next to their targets from the main starter this
// season. Feeds the digest and, from P06, the player model.
// Params: $season, $week, $min_receiver_targets.
MATCH (g:Game {season: $season, week: $week})<-[pi:PLAYED_IN]-(t:Team)
MATCH (g)<-[:PLAYED_IN]-(opp:Team)
WHERE opp <> t
WITH g, t, opp,
     CASE WHEN pi.home THEN g.home_qb_expected ELSE g.away_qb_expected END AS exp_id,
     CASE WHEN pi.home THEN g.home_qb_source ELSE g.away_qb_source END AS exp_source
WHERE exp_id IS NOT NULL
MATCH (q:Player {player_id: exp_id})
// confirmed = this week's schedule / depth chart / injury report named him (a live run);
// otherwise he only rests on the Tuesday rule (say, the last game's starter after a one-game
// fill-in: 2025 week 9 Cousins, who didn't start) and the digest says it isn't confirmed
WITH g, t, opp, q, exp_source,
     coalesce(exp_source IN ['schedule', 'depth_chart', 'injury_next'], false) AS confirmed
CALL (t) {
  MATCH (r:Player)-[a:APPEARED_IN]->(sg:Game {season: $season})
  WHERE a.team_id = t.team_id AND a.qb_started AND sg.week < $week
  WITH r, count(*) AS starts, max(sg.week) AS last_start
  RETURN r, starts AS r_starts, last_start AS r_last_start
  ORDER BY starts DESC, last_start DESC, r.player_id
  LIMIT 1
}
WITH g, t, opp, q, exp_source, confirmed, r, r_starts, r_last_start
WHERE r <> q
// why: the main starter's status on this week's report (a Tuesday run has none yet)
OPTIONAL MATCH (r)-[ri:ON_INJURY_REPORT]->(g)
WITH g, t, opp, q, exp_source, confirmed, r, r_starts, r_last_start, ri.status AS r_status,
     ri.body_part AS r_body_part
CALL (t) {
  MATCH (t)-[:PLAYED_IN]->(tg:Game {season: $season})
  WHERE tg.completed AND tg.week < $week
  RETURN count(tg) AS team_games
}
// the expected QB's starts: for this team this season, and in the graph overall
CALL (q, t) {
  OPTIONAL MATCH (q)-[a:APPEARED_IN]->(sg:Game)
  WHERE a.qb_started
  RETURN count(CASE WHEN sg.season = $season AND a.team_id = t.team_id THEN 1 END) AS q_starts_now,
         count(a) AS q_starts_total,
         max(CASE WHEN a.team_id = t.team_id AND sg.season = $season THEN sg.week END) AS q_last_start
}
// receivers on the team now: targets from the main starter this season, history with q
CALL (q, r, t) {
  MATCH (w:Player)-[cw:PLAYED_FOR {season: $season}]->(t)
  WHERE cw.last_week >= $week - 1 AND w.position_group IN ['WR', 'TE', 'RB']
  OPTIONAL MATCH (r)-[rt:THREW_TO {season: $season}]->(w)
  OPTIONAL MATCH (q)-[qt:THREW_TO]->(w)
  WITH w, coalesce(rt.targets, 0) AS r_targets,
       sum(coalesce(qt.targets, 0)) AS q_targets,
       sum(coalesce(qt.yards, 0)) AS q_yards,
       sum(coalesce(qt.tds, 0)) AS q_tds,
       collect(DISTINCT qt.season) AS q_seasons
  WHERE r_targets >= $min_receiver_targets OR q_targets >= $min_receiver_targets
  RETURN collect({
    player_id: w.player_id, name: w.name, position: w.position,
    r_targets: r_targets, q_targets: q_targets, q_yards: q_yards, q_tds: q_tds,
    q_seasons: q_seasons
  }) AS receivers,
  sum(r_targets) AS r_targets_total,
  sum(q_targets) AS q_targets_total
}
WITH g, t, opp, q, exp_source, confirmed, r, r_starts, r_last_start, r_status, r_body_part,
     team_games,
     q_starts_now, q_starts_total, q_last_start, receivers, r_targets_total, q_targets_total,
     size([x IN receivers WHERE x.q_targets >= 10]) AS receivers_with_history
WITH *,
     // a QB change is usually the week's biggest single story: base 0.62
     0.62
     + 0.2 * CASE WHEN r_starts >= 4 THEN 1.0 ELSE r_starts / 4.0 END
     + 0.05 * CASE WHEN receivers_with_history >= 2 THEN 1.0 ELSE receivers_with_history / 2.0 END
     + 0.13 * CASE WHEN q_starts_now = 0 THEN 1.0 ELSE 0.0 END
     AS base
WITH *, base * CASE WHEN confirmed THEN 1.0 ELSE 0.6 END AS strength
RETURN 'qb_change' AS insight_type,
       g.game_id AS game_id,
       t.team_id AS team,
       opp.team_id AS opponent,
       q.player_id AS qb_id,
       q.name AS qb,
       q.rookie_season AS qb_rookie_season,
       exp_source AS qb_source,
       confirmed AS qb_confirmed,
       r.player_id AS regular_id,
       r.name AS regular,
       r_status AS regular_status,
       r_body_part AS regular_body_part,
       r_starts,
       r_last_start,
       team_games,
       q_starts_now,
       q_starts_total,
       q_last_start,
       receivers,
       r_targets_total,
       q_targets_total,
       receivers_with_history,
       q_targets_total AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, team
