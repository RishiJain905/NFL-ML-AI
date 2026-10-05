// Q6 former teammates on opposite sides (documentation/05 -> Query library): this week's
// expected starting QB faces a receiver he used to throw to, who now plays for the
// opponent. Multi-hop:
//   (Game {week W}).home_qb_expected / away_qb_expected -> (QB:Player)
//   (QB)-[:THREW_TO {any visible season}]->(Receiver)-[:PLAYED_FOR {season: S}]->(opponent)
// "Used to throw to": any THREW_TO history (earlier seasons, or earlier this season with
// another team) while the two are now on the two sides of this week's game. Their combined
// history: targets, catches, yards, TDs, seasons and the team(s) it was for (THREW_TO is per
// season with its team; results strictly before week W). The receiver must be on the
// opponent now (roster through last week, not on a reserve list, not ruled Out / Doubtful)
// and playing (>= 1 game for them this season; his target share there is his role).
// Strength grows with the history (targets), how recent it is, and his role now.
// Params: $season, $week, $min_targets.
MATCH (g:Game {season: $season, week: $week})
UNWIND [[g.home_team, g.home_qb_expected, g.home_qb_source, g.away_team],
        [g.away_team, g.away_qb_expected, g.away_qb_source, g.home_team]] AS side
WITH g, side[0] AS team_id, side[1] AS qb_id, side[2] AS qb_source, side[3] AS opp_id
WHERE qb_id IS NOT NULL
MATCH (qb:Player {player_id: qb_id})
MATCH (qb)-[tt:THREW_TO]->(r:Player)
WHERE r <> qb
WITH g, team_id, opp_id, qb, qb_source, r,
     sum(tt.targets) AS targets, sum(tt.completions) AS catches, sum(tt.yards) AS yards,
     sum(tt.tds) AS tds, sum(tt.games_together) AS games_together,
     collect(DISTINCT tt.season) AS seasons, collect(DISTINCT tt.team_id) AS teams_together,
     max(tt.season) AS last_season
WHERE targets >= $min_targets
MATCH (r)-[cur:PLAYED_FOR {season: $season}]->(o:Team {team_id: opp_id})
WHERE cur.last_week >= $week - 1 AND coalesce(cur.status_last, 'ACT') <> 'RES'
  AND NOT EXISTS { (r)-[x:ON_INJURY_REPORT]->(g) WHERE x.status IN ['Out', 'Doubtful'] }
  // still with the opponent: no later stint with another team this season
  AND NOT EXISTS {
    MATCH (r)-[other:PLAYED_FOR {season: $season}]->(t2:Team)
    WHERE t2 <> o AND other.first_week > cur.last_week
  }
CALL (r, o) {
  MATCH (r)-[a:APPEARED_IN]->(x:Game {season: $season})
  WHERE a.team_id = o.team_id AND x.week < $week
  RETURN count(a) AS games_now, avg(coalesce(a.target_share, 0.0)) AS target_share_now
}
WITH g, team_id, opp_id, qb, qb_source, r, targets, catches, yards, tds, games_together,
     seasons, teams_together, last_season, games_now, target_share_now
WHERE games_now >= 1
WITH *,
     0.3
     + 0.3 * CASE WHEN targets >= 300 THEN 1.0 ELSE targets / 300.0 END
     + 0.15 * CASE WHEN last_season >= $season - 1 THEN 1.0
                   WHEN last_season >= $season - 3 THEN 0.5 ELSE 0.0 END
     + 0.15 * CASE WHEN target_share_now >= 0.2 THEN 1.0 ELSE target_share_now / 0.2 END
     AS strength
RETURN 'former_teammates' AS insight_type,
       g.game_id AS game_id,
       team_id AS team,
       opp_id AS opponent,
       qb.player_id AS qb_id,
       qb.name AS qb,
       qb_source,
       r.player_id AS receiver_id,
       r.name AS receiver,
       r.position AS receiver_position,
       targets, catches, yards, tds, games_together,
       seasons,
       teams_together,
       last_season,
       games_now,
       round(target_share_now, 3) AS target_share_now,
       targets AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, game_id, receiver_id
