// Q4 common-opponent chains (documentation/05 -> Query library): A and B meet this week;
// both already played C this season. Their point and EPA margins against C, side by side,
// next to the game model's favorite (Game -HAS_PREDICTION-> GamePrediction, primary row).
// One row per game. Params: $season, $week.
MATCH (a:Team)-[pa:PLAYED_IN]->(g:Game {season: $season, week: $week})<-[:PLAYED_IN]-(b:Team)
WHERE pa.home AND a <> b
MATCH (a)-[ra:PLAYED_IN]->(ga:Game {season: $season})<-[:PLAYED_IN]-(c:Team)
WHERE ga.completed AND ga.week < $week AND c <> a AND c <> b
MATCH (b)-[rb:PLAYED_IN]->(gb:Game {season: $season})<-[:PLAYED_IN]-(c)
WHERE gb.completed AND gb.week < $week
WITH g, a, b, c,
     collect(DISTINCT {week: ga.week, margin: ra.margin, epa: ra.epa_margin}) AS a_games,
     collect(DISTINCT {week: gb.week, margin: rb.margin, epa: rb.epa_margin}) AS b_games
WITH g, a, b, c, a_games, b_games,
     reduce(s = 0.0, x IN a_games | s + x.margin) / size(a_games) AS a_margin,
     reduce(s = 0.0, x IN b_games | s + x.margin) / size(b_games) AS b_margin,
     // EPA margins: the mean of the known values only (null when none is known)
     [x IN a_games WHERE x.epa IS NOT NULL | x.epa] AS a_epas,
     [x IN b_games WHERE x.epa IS NOT NULL | x.epa] AS b_epas
WITH g, a, b, c, a_games, b_games, a_margin, b_margin,
     CASE WHEN size(a_epas) = 0 THEN null
          ELSE reduce(s = 0.0, x IN a_epas | s + x) / size(a_epas) END AS a_epa,
     CASE WHEN size(b_epas) = 0 THEN null
          ELSE reduce(s = 0.0, x IN b_epas | s + x) / size(b_epas) END AS b_epa
ORDER BY c.team_id
WITH g, a, b,
     collect({
       team: c.team_id, a_games: a_games, b_games: b_games,
       a_margin: a_margin, b_margin: b_margin, a_epa: a_epa, b_epa: b_epa
     }) AS common,
     avg(a_margin) AS a_avg_margin,
     avg(b_margin) AS b_avg_margin,
     avg(a_epa) AS a_avg_epa,
     avg(b_epa) AS b_avg_epa
OPTIONAL MATCH (g)-[:HAS_PREDICTION]->(gp:GamePrediction {is_primary: true})
WITH g, a, b, common, a_avg_margin, b_avg_margin, a_avg_epa, b_avg_epa,
     head(collect(gp)) AS gp
WITH *,
     size(common) AS n_common,
     a_avg_margin - b_avg_margin AS edge,
     CASE
       WHEN gp IS NULL THEN NULL
       WHEN gp.home_win_prob >= 0.5 THEN a.team_id
       ELSE b.team_id
     END AS model_favorite
WITH *,
     CASE WHEN edge > 0 THEN a.team_id WHEN edge < 0 THEN b.team_id END AS chain_favorite
WITH *,
     0.25
     + 0.4 * CASE WHEN abs(edge) >= 14 THEN 1.0 ELSE abs(edge) / 14.0 END
           * CASE WHEN n_common >= 2 THEN 1.0 ELSE 0.6 END
     + 0.2 * CASE
         WHEN model_favorite IS NOT NULL AND chain_favorite IS NOT NULL
              AND model_favorite <> chain_favorite THEN 1.0
         ELSE 0.0
       END
     + 0.15 * CASE WHEN n_common >= 3 THEN 1.0 ELSE n_common / 3.0 END
     AS strength
RETURN 'common_opponents' AS insight_type,
       g.game_id AS game_id,
       a.team_id AS team,
       b.team_id AS opponent,
       common,
       n_common,
       round(a_avg_margin, 2) AS team_avg_margin,
       round(b_avg_margin, 2) AS opponent_avg_margin,
       round(a_avg_epa, 4) AS team_avg_epa_margin,
       round(b_avg_epa, 4) AS opponent_avg_epa_margin,
       round(edge, 2) AS edge,
       chain_favorite,
       model_favorite,
       CASE WHEN gp IS NULL THEN NULL ELSE round(gp.home_win_prob, 4) END AS model_home_win_prob,
       n_common AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, game_id
