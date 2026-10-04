// Q12 special-teams edge: in which of this week's games do the two teams' special teams
// differ most this season? PLAYED_IN.st_epa is a team's net special-teams EPA in a game
// (kickoffs, punts, field goals, extra points; both sides of each play; centered on the
// league's mean per play type, so 0 = an average unit; graph/tables.py::special_teams_epa).
// Averaged over this season's completed games before week W; both teams need at least
// $min_games of them. Special-teams EPA is noisy (one return touchdown or missed kick is
// 4-7 EPA), so the gap is shrunk toward 0 by n / (n + $shrink_games), n = the smaller game
// count, before the $min_gap filter and the strength (the rows show the raw averages).
// Walk: (Team)-[:PLAYED_IN]->(this week's Game)<-[:PLAYED_IN]-(Team), then each team
// -[:PLAYED_IN {st_epa}]->(this season's earlier Games).
// Params: $season, $week, $min_games, $min_gap, $shrink_games.
MATCH (a:Team)-[pa:PLAYED_IN]->(g:Game {season: $season, week: $week})<-[:PLAYED_IN]-(b:Team)
WHERE pa.home AND a <> b
CALL (a) {
  MATCH (a)-[p:PLAYED_IN]->(x:Game {season: $season})
  WHERE x.week < $week AND x.completed AND p.st_epa IS NOT NULL
  RETURN count(p) AS a_games, avg(p.st_epa) AS a_st, sum(p.st_epa) AS a_total
}
CALL (b) {
  MATCH (b)-[p:PLAYED_IN]->(x:Game {season: $season})
  WHERE x.week < $week AND x.completed AND p.st_epa IS NOT NULL
  RETURN count(p) AS b_games, avg(p.st_epa) AS b_st, sum(p.st_epa) AS b_total
}
WITH g, a, b, a_games, a_st, a_total, b_games, b_st, b_total, a_st - b_st AS gap,
     CASE WHEN a_games < b_games THEN a_games ELSE b_games END AS n_min
WHERE a_games >= $min_games AND b_games >= $min_games
WITH *, abs(gap) * n_min / (n_min + $shrink_games) AS shrunk
WHERE shrunk >= $min_gap
WITH *,
     0.25 + 0.6 * CASE WHEN shrunk >= 4.0 THEN 1.0
                       ELSE (shrunk - $min_gap) / (4.0 - $min_gap) END AS strength
RETURN 'special_teams' AS insight_type,
       g.game_id AS game_id,
       a.team_id AS team,
       b.team_id AS opponent,
       round(a_st, 4) AS team_st_epa,
       round(b_st, 4) AS opponent_st_epa,
       round(a_total, 3) AS team_st_total,
       round(b_total, 3) AS opponent_st_total,
       a_games AS team_games,
       b_games AS opponent_games,
       round(gap, 4) AS gap,
       round(shrunk, 4) AS shrunk_gap,
       n_min AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, game_id
