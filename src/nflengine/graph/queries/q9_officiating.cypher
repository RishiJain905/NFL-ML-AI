// Q9 officiating crew tendencies (documentation/05 -> Query library): this week's referee
// (his crew) -> penalties and penalty yards per game, and the home / away penalty split,
// over his recent games, against the league average over the same span.
//   (Official)-[:OFFICIATED {role: Referee}]->(this week's Game)
//   (Official)-[:OFFICIATED {role: Referee}]->(past Game)<-[:PLAYED_IN {penalties}]-(Team)
// Crews are published after their games (graph/tables.py: a live run loads a week-W crew
// only when the snapshot has it; a backtest never does), so this query is quiet until a
// week's crews are in the data. Window: this season and the $seasons before, completed games
// strictly before week W with penalty counts for both teams; the split leaves out neutral
// sites. Penalties are noisy: it fires only on a clear gap (>= $min_pen_gap penalties per
// game, or a home-minus-away split >= $min_split_gap from the league's) over >= $min_games
// games, and its strength stays modest (<= 0.5).
// Params: $season, $week, $roles, $seasons, $min_games, $min_pen_gap, $min_split_gap.
MATCH (ref:Official)-[o:OFFICIATED]->(g:Game {season: $season, week: $week})
WHERE o.role IN $roles
MATCH (h:Team)-[hp:PLAYED_IN]->(g)<-[:PLAYED_IN]-(a:Team)
WHERE hp.home AND h <> a
CALL () {
  MATCH (x:Game)<-[p:PLAYED_IN]-(:Team)
  WHERE x.completed AND x.season >= $season - $seasons
    AND (x.season < $season OR x.week < $week) AND p.penalties IS NOT NULL
  WITH x, sum(p.penalties) AS pen, sum(p.penalty_yards) AS yds,
       sum(CASE WHEN p.home THEN p.penalties ELSE 0 END) AS home_pen,
       sum(CASE WHEN p.home THEN 0 ELSE p.penalties END) AS away_pen, count(p) AS sides
  WHERE sides = 2
  RETURN count(x) AS lg_games, avg(pen) AS lg_pen, avg(yds) AS lg_yds,
         avg(CASE WHEN NOT x.neutral_site THEN home_pen - away_pen END) AS lg_split
}
CALL (ref) {
  MATCH (ref)-[o2:OFFICIATED]->(x:Game)<-[p:PLAYED_IN]-(:Team)
  WHERE o2.role IN $roles AND x.completed AND x.season >= $season - $seasons
    AND (x.season < $season OR x.week < $week) AND p.penalties IS NOT NULL
  WITH x, sum(p.penalties) AS pen, sum(p.penalty_yards) AS yds,
       sum(CASE WHEN p.home THEN p.penalties ELSE 0 END) AS home_pen,
       sum(CASE WHEN p.home THEN 0 ELSE p.penalties END) AS away_pen, count(p) AS sides
  WHERE sides = 2
  RETURN count(x) AS ref_games, avg(pen) AS ref_pen, avg(yds) AS ref_yds,
         avg(CASE WHEN NOT x.neutral_site THEN home_pen - away_pen END) AS ref_split,
         count(CASE WHEN NOT x.neutral_site THEN 1 END) AS ref_split_games,
         min(x.season) AS first_season
}
WITH g, h, a, ref, lg_games, lg_pen, lg_yds, lg_split, ref_games, ref_pen, ref_yds,
     ref_split, ref_split_games, first_season,
     ref_pen - lg_pen AS pen_gap, ref_split - lg_split AS split_gap
WHERE ref_games >= $min_games
  AND (abs(pen_gap) >= $min_pen_gap OR abs(split_gap) >= $min_split_gap)
WITH *,
     0.2
     + 0.2 * CASE WHEN abs(pen_gap) / 3.0 >= abs(split_gap) / 2.0
                  THEN CASE WHEN abs(pen_gap) >= 3.0 THEN 1.0 ELSE abs(pen_gap) / 3.0 END
                  ELSE CASE WHEN abs(split_gap) >= 2.0 THEN 1.0 ELSE abs(split_gap) / 2.0 END
             END
     + 0.1 * CASE WHEN ref_games >= 30 THEN 1.0 ELSE ref_games / 30.0 END AS strength
RETURN 'officiating' AS insight_type,
       g.game_id AS game_id,
       h.team_id AS team,
       a.team_id AS opponent,
       ref.official_id AS official_id,
       ref.name AS referee,
       ref_games,
       round(ref_pen, 2) AS ref_pen,
       round(ref_yds, 1) AS ref_yds,
       round(ref_split, 2) AS ref_split,
       ref_split_games,
       lg_games,
       round(lg_pen, 2) AS lg_pen,
       round(lg_yds, 1) AS lg_yds,
       round(lg_split, 2) AS lg_split,
       round(pen_gap, 2) AS pen_gap,
       round(split_gap, 2) AS split_gap,
       first_season,
       ref_games AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, game_id
