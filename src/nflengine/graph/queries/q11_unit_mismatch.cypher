// Q11 unit mismatch: in each of this week's games, which offense-vs-defense unit matchup is
// the most lopsided? Four matchups per game (A pass offense vs B pass defense, A rush offense
// vs B rush defense, and B's vs A's) from the week-W TeamWeek ratings (built from weeks < W).
// Ratings are relative to the league: off_* = EPA per play gained, def_* = EPA per play
// ALLOWED (higher = worse defense), so the offense's expected EPA in a matchup is
// off_A + def_B. A mismatch needs both units at the same end: an offense in the top $tier
// against a defense in the bottom $tier (offense edge), or the reverse (defense edge).
// Ranks are by quality among the teams rated this week (1 = best offense / the defense that
// allows the least). The size is the mean of the two units' |z| (value / league SD of that
// unit this week); one row per game: its biggest mismatch.
// Walk: (Team)-[:PLAYED_IN]->(this week's Game)<-[:PLAYED_IN]-(Team), each team
// -[:HAS_WEEK]->(TeamWeek {season, week}), ranked against every TeamWeek of the week.
// Params: $season, $week, $tier.
MATCH (tw:TeamWeek {season: $season, week: $week})
WITH collect(tw) AS tws,
     count(tw) AS n_teams,
     stDevP(tw.off_pass_epa) AS sd_op,
     stDevP(tw.def_pass_epa) AS sd_dp,
     stDevP(tw.off_rush_epa) AS sd_or,
     stDevP(tw.def_rush_epa) AS sd_dr
MATCH (a:Team)-[pa:PLAYED_IN]->(g:Game {season: $season, week: $week})<-[:PLAYED_IN]-(b:Team)
WHERE pa.home AND a <> b
MATCH (a)-[:HAS_WEEK]->(ta:TeamWeek {season: $season, week: $week})
MATCH (b)-[:HAS_WEEK]->(tb:TeamWeek {season: $season, week: $week})
UNWIND [
  {off: a, def: b, o: ta, d: tb, unit: 'pass'},
  {off: a, def: b, o: ta, d: tb, unit: 'rush'},
  {off: b, def: a, o: tb, d: ta, unit: 'pass'},
  {off: b, def: a, o: tb, d: ta, unit: 'rush'}
] AS m
WITH g, a, b, m, tws, n_teams,
     CASE m.unit WHEN 'pass' THEN m.o.off_pass_epa ELSE m.o.off_rush_epa END AS off_val,
     CASE m.unit WHEN 'pass' THEN m.d.def_pass_epa ELSE m.d.def_rush_epa END AS def_val,
     CASE m.unit WHEN 'pass' THEN sd_op ELSE sd_or END AS sd_off,
     CASE m.unit WHEN 'pass' THEN sd_dp ELSE sd_dr END AS sd_def
WHERE off_val IS NOT NULL AND def_val IS NOT NULL AND sd_off > 0 AND sd_def > 0
WITH g, a, b, m, n_teams, off_val, def_val, sd_off, sd_def,
     1 + size([x IN tws WHERE
       (CASE m.unit WHEN 'pass' THEN x.off_pass_epa ELSE x.off_rush_epa END) > off_val])
       AS off_rank,
     1 + size([x IN tws WHERE
       (CASE m.unit WHEN 'pass' THEN x.def_pass_epa ELSE x.def_rush_epa END) < def_val])
       AS def_rank
WITH g, a, b, m, n_teams, off_val, def_val, off_rank, def_rank,
     CASE
       WHEN off_rank <= $tier AND def_rank > n_teams - $tier THEN 'offense'
       WHEN off_rank > n_teams - $tier AND def_rank <= $tier THEN 'defense'
     END AS edge,
     (abs(off_val / sd_off) + abs(def_val / sd_def)) / 2.0 AS size
WHERE edge IS NOT NULL
WITH g, a, b, m, n_teams, off_val, def_val, off_rank, def_rank, edge, size
ORDER BY size DESC
WITH g, a, b, collect({m: m, off_val: off_val, def_val: def_val, off_rank: off_rank,
                       def_rank: def_rank, edge: edge, size: size, n_teams: n_teams})[0] AS top
WITH g, a, b, top, top.m.off AS ot, top.m.def AS dt, top.m.o AS ow, top.m.d AS dw
WITH g, a, b, top, ot, dt, ow, dw,
     COUNT { (ot)-[:PLAYED_IN]->(x:Game {season: $season}) WHERE x.week < $week AND x.completed }
       AS off_games,
     COUNT { (dt)-[:PLAYED_IN]->(y:Game {season: $season}) WHERE y.week < $week AND y.completed }
       AS def_games,
     CASE WHEN coalesce(ow.prior_weight, 0.0) > coalesce(dw.prior_weight, 0.0)
          THEN coalesce(ow.prior_weight, 0.0) ELSE coalesce(dw.prior_weight, 0.0) END AS prior_max
WITH *,
     (0.3 + 0.6 * CASE WHEN top.size >= 2.0 THEN 1.0
                       ELSE CASE WHEN top.size <= 0.6 THEN 0.0 ELSE (top.size - 0.6) / 1.4 END
                  END)
     * (1.0 - 0.25 * prior_max) AS strength
RETURN 'unit_mismatch' AS insight_type,
       g.game_id AS game_id,
       a.team_id AS team,
       b.team_id AS opponent,
       ot.team_id AS off_team,
       dt.team_id AS def_team,
       top.m.unit AS unit,
       top.edge AS edge,
       round(top.off_val, 4) AS off_epa,
       round(top.def_val, 4) AS def_epa,
       top.off_rank AS off_rank,
       top.def_rank AS def_rank,
       top.n_teams AS n_teams,
       round(top.off_val + top.def_val, 4) AS expected_epa,
       round(top.size, 3) AS size,
       ow.prior_weight AS off_prior_weight,
       dw.prior_weight AS def_prior_weight,
       off_games,
       def_games,
       CASE WHEN off_games < def_games THEN off_games ELSE def_games END AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, game_id
