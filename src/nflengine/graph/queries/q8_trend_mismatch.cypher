// Q8 trend mismatch (documentation/05 -> Query library): a team trending down meets a
// team trending up. Walks the TeamWeek chain: (3 weeks ago) -NEXT*3-> (this week's as-of
// row) per team; the change is this week's net rating minus the one 3 weeks earlier
// (= `team_trends.trend_delta`), and the direction is P02's band call (D45). Trends are
// descriptive, not predictive (D47). Needs week >= 4 (the chain stays inside the season).
// Params: $season, $week.
MATCH (a:Team)-[pa:PLAYED_IN]->(g:Game {season: $season, week: $week})<-[:PLAYED_IN]-(b:Team)
WHERE pa.home AND a <> b
MATCH (a)-[:HAS_WEEK]->(ta:TeamWeek {season: $season, week: $week})
MATCH (b)-[:HAS_WEEK]->(tb:TeamWeek {season: $season, week: $week})
MATCH (ta0:TeamWeek)-[:NEXT*3]->(ta)
MATCH (tb0:TeamWeek)-[:NEXT*3]->(tb)
WHERE ta0.season = $season AND tb0.season = $season
  AND ((ta.trend_dir = 'up' AND tb.trend_dir = 'down')
    OR (ta.trend_dir = 'down' AND tb.trend_dir = 'up'))
WITH g, a, b, ta, tb, ta.net - ta0.net AS a_delta, tb.net - tb0.net AS b_delta
WITH *,
     0.3
     + 0.35 * CASE WHEN abs(a_delta) >= 0.1 THEN 1.0 ELSE abs(a_delta) / 0.1 END
     + 0.35 * CASE WHEN abs(b_delta) >= 0.1 THEN 1.0 ELSE abs(b_delta) / 0.1 END
     AS strength
RETURN 'trend_mismatch' AS insight_type,
       g.game_id AS game_id,
       a.team_id AS team,
       b.team_id AS opponent,
       ta.trend_dir AS team_direction,
       tb.trend_dir AS opponent_direction,
       round(a_delta, 4) AS team_delta,
       round(b_delta, 4) AS opponent_delta,
       round(ta.net, 4) AS team_net,
       round(tb.net, 4) AS opponent_net,
       3 AS window_weeks,
       3 AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, game_id
