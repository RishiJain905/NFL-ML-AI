// Q1 revenge games (documentation/05 -> Query library): players who play real snaps for
// their current team and face a team they played for in the last 2 seasons (or earlier
// this season). Multi-hop: Player -PLAYED_FOR-> Team -PLAYED_IN-> Game <-PLAYED_IN- Team
// <-PLAYED_FOR- Player, plus the optional DRAFTED_BY / TRADED_TO links to the old team.
// Params: $season, $week, $min_old_games, $min_snap_pct.
MATCH (g:Game {season: $season, week: $week})<-[:PLAYED_IN]-(t:Team)
MATCH (g)<-[:PLAYED_IN]-(opp:Team)
WHERE opp <> t
MATCH (p:Player)-[cur:PLAYED_FOR {season: $season}]->(t)
WHERE cur.last_week >= $week - 1 AND coalesce(cur.status_last, 'ACT') <> 'RES'
MATCH (p)-[old:PLAYED_FOR]->(opp)
WHERE old.season >= $season - 2 AND old.games >= 1
  AND (old.season < $season OR old.last_week < cur.first_week)
WITH g, t, opp, p, cur,
     collect(DISTINCT old.season) AS seasons_with_opp,
     sum(old.games) AS games_with_opp,
     max(old.season) AS last_season_with_opp
WHERE games_with_opp >= $min_old_games
  // not ruled out this week
  AND NOT EXISTS {
    (p)-[r:ON_INJURY_REPORT]->(g) WHERE r.status IN ['Out', 'Doubtful']
  }
CALL (p, t) {
  MATCH (p)-[a:APPEARED_IN]->(:Game {season: $season})
  WHERE a.team_id = t.team_id
  RETURN avg(a.snap_pct) AS snap_pct, count(a) AS games_now,
         // which side the share is of (snap_pct = the larger of the two)
         CASE WHEN sum(coalesce(a.def_snaps, 0)) > sum(coalesce(a.off_snaps, 0))
              THEN 'defense' ELSE 'offense' END AS snap_side
}
WITH g, t, opp, p, seasons_with_opp, games_with_opp, last_season_with_opp, snap_pct, games_now,
     snap_side
WHERE games_now >= 1 AND snap_pct >= $min_snap_pct
OPTIONAL MATCH (p)-[d:DRAFTED_BY]->(opp)
OPTIONAL MATCH (p)-[tr:TRADED_TO]->(t)
WHERE tr.from_team = opp.team_id
WITH g, t, opp, p, seasons_with_opp, games_with_opp, last_season_with_opp, snap_pct, games_now,
     snap_side, d, max(tr.date) AS traded_on
WITH *,
     0.3
     + 0.25 * CASE WHEN games_with_opp >= 17 THEN 1.0 ELSE games_with_opp / 17.0 END
     + 0.25 * CASE WHEN snap_pct >= 0.9 THEN 1.0 ELSE (snap_pct - 0.5) / 0.4 END
     + 0.1 * CASE WHEN last_season_with_opp >= $season - 1 THEN 1.0 ELSE 0.5 END
     + 0.1 * CASE WHEN d IS NOT NULL OR traded_on IS NOT NULL THEN 1.0 ELSE 0.0 END
     AS strength
RETURN 'revenge' AS insight_type,
       g.game_id AS game_id,
       t.team_id AS team,
       opp.team_id AS opponent,
       p.player_id AS player_id,
       p.name AS player,
       p.position AS position,
       seasons_with_opp,
       games_with_opp,
       games_now,
       round(snap_pct, 3) AS snap_pct,
       snap_side,
       d.year AS drafted_year,
       d.round AS drafted_round,
       toString(traded_on) AS traded_on,
       games_with_opp AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, player_id
