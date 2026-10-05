// Q10 usage comparison (documentation/05 -> Q10; GDS): a pass catcher or running back
// playing this week whose usage this season is closest (GDS k-nearest neighbors on the
// UsageProfile vectors, graph/gds.py::player_similarity -> SIMILAR_TO rank 1) to another
// player's top-$max_rank season in his group (receiving yards for WR / TE, scrimmage yards
// for RB). Always a comparison of how he's used so far, never a projection.
//   (p)-[:HAS_PROFILE]->(UsageProfile {season: S}), (p)-[:SIMILAR_TO {season: S, rank: 1}]
//   ->(x)-[:HAS_PROFILE]->(UsageProfile {season: other_season})
// "Close" = a score at or above this week's median rank-1 score in his group (Euclidean
// similarity 1 / (1 + distance) on z-scored vectors; its scale differs by group). He must
// have a real role (target share >= $min_target_share, or carry share >= $min_carry_share
// for a RB), be on the team now and not ruled Out / Doubtful. Strength: closeness, how good
// the matched season was, and a young player (a rookie or second-year player) gets a little
// more ("looks like X's breakout year").
// Params: $season, $week, $max_rank, $min_target_share, $min_carry_share.
CALL () {
  MATCH (:Player)-[s:SIMILAR_TO {season: $season, rank: 1}]->(:Player)
  WITH s.group AS grp, percentileCont(s.score, 0.5) AS median
  RETURN collect({grp: grp, median: median}) AS medians
}
MATCH (g:Game {season: $season, week: $week})<-[:PLAYED_IN]-(t:Team)
MATCH (g)<-[:PLAYED_IN]-(opp:Team)
WHERE opp <> t
MATCH (p:Player)-[:HAS_PROFILE]->(u:UsageProfile {season: $season})
WHERE u.team_id = t.team_id
  AND (u.target_share >= $min_target_share
       OR (u.group = 'RB' AND u.carry_share >= $min_carry_share))
MATCH (p)-[cur:PLAYED_FOR {season: $season}]->(t)
WHERE cur.last_week >= $week - 1 AND coalesce(cur.status_last, 'ACT') <> 'RES'
  AND NOT EXISTS { (p)-[x:ON_INJURY_REPORT]->(g) WHERE x.status IN ['Out', 'Doubtful'] }
MATCH (p)-[s:SIMILAR_TO {season: $season, rank: 1}]->(o:Player)
MATCH (o)-[:HAS_PROFILE]->(ou:UsageProfile {season: s.other_season})
WHERE ou.yds_rank <= $max_rank
WITH g, t, opp, p, u, s, o, ou,
     [m IN medians WHERE m.grp = s.group][0].median AS median
WHERE s.score >= median
WITH *,
     CASE WHEN p.rookie_season IS NOT NULL AND p.rookie_season >= $season - 1 THEN true
          ELSE false END AS young
WITH *,
     0.3
     + 0.2 * CASE WHEN median >= 1.0 THEN 0.0
                  WHEN (s.score - median) / (1.0 - median) >= 0.5 THEN 1.0
                  ELSE (s.score - median) / (1.0 - median) / 0.5 END
     + 0.15 * CASE WHEN ou.yds_rank <= 3 THEN 1.0 WHEN ou.yds_rank <= 6 THEN 0.6 ELSE 0.3 END
     + 0.2 * CASE WHEN young THEN 1.0 ELSE 0.0 END
     + 0.1 * CASE WHEN u.games >= 6 THEN 1.0 ELSE u.games / 6.0 END AS strength
RETURN 'usage_comp' AS insight_type,
       g.game_id AS game_id,
       t.team_id AS team,
       opp.team_id AS opponent,
       p.player_id AS player_id,
       p.name AS player,
       p.position AS position,
       u.group AS grp,
       p.rookie_season AS rookie_season,
       young,
       u.games AS games,
       u.targets AS targets,
       u.carries AS carries,
       u.target_share AS target_share,
       u.air_yards_share AS air_yards_share,
       u.adot AS adot,
       u.rz_share AS rz_share,
       u.snap_share AS snap_share,
       u.carry_share AS carry_share,
       o.player_id AS other_id,
       o.name AS other,
       s.other_season AS other_season,
       ou.team_id AS other_team,
       ou.games AS other_games,
       ou.targets AS other_targets,
       ou.carries AS other_carries,
       ou.target_share AS other_target_share,
       ou.air_yards_share AS other_air_yards_share,
       ou.adot AS other_adot,
       ou.rz_share AS other_rz_share,
       ou.snap_share AS other_snap_share,
       ou.carry_share AS other_carry_share,
       ou.rec_yds AS other_rec_yds,
       ou.scrimmage_yds AS other_scrimmage_yds,
       ou.yds_rank AS other_yds_rank,
       round(s.score, 4) AS score,
       round(median, 4) AS group_median,
       s.basis AS basis,
       u.games AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, game_id, player_id
