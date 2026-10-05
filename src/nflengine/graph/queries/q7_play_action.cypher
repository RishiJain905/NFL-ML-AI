// Q7 style matchup, play action (documentation/05 -> Query library): this week's offense
// uses play action at the league's top end (FTN charting, PLAYED_IN.pa_rate = share of its
// charted dropbacks; FTN is about a week late, so a Tuesday graph lacks last week's: the
// visibility table in graph/tables.py) -> how did the opposing defense do in its past games
// against play-action-heavy offenses, against other offenses, and compared with every
// defense?
//
// An offense's play-action rate in a season = over its visible charted games (weighted by
// charted dropbacks). The cut = the $pct percentile of the team-seasons in the 3 completed
// seasons before this one (FTN starts in 2022). This week's offense is rated on this
// season (>= $min_current_games charted games); a past opponent counts as play-action-heavy
// when its rate that season is over the cut on >= $min_current_games charted games.
// Defense games: this season and the 2 before, strictly before week W, against offenses
// with a rate that season; EPA per play allowed as in q7_style_matchup, and the same gap.
// Walk: (Team)-[:PLAYED_IN {pa_rate}]->(Game)<-[:PLAYED_IN]-(Team).
// Params: $season, $week, $pct, $min_current_games, $min_games, $min_other, $min_gap.
CALL () {
  MATCH (t:Team)-[p:PLAYED_IN]->(x:Game)
  WHERE x.completed AND x.season >= $season - 3 AND (x.season < $season OR x.week < $week)
    AND p.pa_rate IS NOT NULL
  WITH t, x.season AS s, sum(p.pa_rate * p.pa_dropbacks) AS pa, sum(p.pa_dropbacks) AS db,
       count(p) AS n
  RETURN collect({t: t.team_id, s: s, n: n, rate: pa / db}) AS rates
}
CALL (rates) {
  UNWIND [x IN rates WHERE x.s < $season AND x.n >= $min_current_games] AS x
  RETURN percentileCont(x.rate, $pct) AS cut
}
CALL (rates, cut) {
  UNWIND [x IN rates WHERE x.s >= $season - 2 AND x.n >= $min_current_games] AS r
  MATCH (o:Team {team_id: r.t})-[op:PLAYED_IN]->(x:Game {season: r.s})<-[:PLAYED_IN]-(d:Team)
  WHERE d <> o AND x.completed AND (x.season < $season OR x.week < $week)
    AND op.plays > 0 AND op.epa_per_play IS NOT NULL
  RETURN collect({d: d.team_id, epa: op.epa_per_play, plays: op.plays,
                  heavy: r.rate >= cut}) AS games
}
MATCH (g:Game {season: $season, week: $week})
UNWIND [[g.home_team, g.away_team], [g.away_team, g.home_team]] AS side
WITH cut, rates, games, g, side[0] AS def_id, side[1] AS off_id
WITH cut, games, g, def_id, off_id,
     [x IN rates WHERE x.t = off_id AND x.s = $season][0] AS now
WHERE cut IS NOT NULL AND now IS NOT NULL AND now.n >= $min_current_games AND now.rate >= cut
WITH g, def_id, off_id, now, cut,
     [x IN games WHERE x.heavy] AS lg_hi,
     [x IN games WHERE NOT x.heavy] AS lg_lo,
     [x IN games WHERE x.d = def_id AND x.heavy] AS d_hi,
     [x IN games WHERE x.d = def_id AND NOT x.heavy] AS d_lo
WHERE size(d_hi) >= $min_games AND size(d_lo) >= $min_other
WITH g, def_id, off_id, now, cut,
     size(d_hi) AS n_hi, size(d_lo) AS n_lo, size(lg_hi) AS lg_n_hi, size(lg_lo) AS lg_n_lo,
     reduce(a = [0.0, 0], x IN d_hi | [a[0] + x.epa * x.plays, a[1] + x.plays]) AS s_dhi,
     reduce(a = [0.0, 0], x IN d_lo | [a[0] + x.epa * x.plays, a[1] + x.plays]) AS s_dlo,
     reduce(a = [0.0, 0], x IN lg_hi | [a[0] + x.epa * x.plays, a[1] + x.plays]) AS s_lhi,
     reduce(a = [0.0, 0], x IN lg_lo | [a[0] + x.epa * x.plays, a[1] + x.plays]) AS s_llo
WITH *,
     s_dhi[0] / s_dhi[1] AS def_epa_style, s_dlo[0] / s_dlo[1] AS def_epa_other,
     s_lhi[0] / s_lhi[1] AS lg_epa_style, s_llo[0] / s_llo[1] AS lg_epa_other
WITH *, (def_epa_style - def_epa_other) - (lg_epa_style - lg_epa_other) AS gap
WHERE abs(gap) >= $min_gap
WITH *,
     0.25
     + 0.35 * CASE WHEN abs(gap) >= 0.2 THEN 1.0 ELSE abs(gap) / 0.2 END
     + 0.15 * CASE WHEN n_hi >= 12 THEN 1.0 ELSE n_hi / 12.0 END AS strength
RETURN 'play_action' AS insight_type,
       'play_action' AS style,
       g.game_id AS game_id,
       def_id AS team,
       off_id AS opponent,
       now.n AS offense_games,
       round(now.rate, 4) AS offense_rate,
       round(cut, 4) AS cut,
       round(def_epa_style, 4) AS def_epa_style,
       round(def_epa_other, 4) AS def_epa_other,
       round(lg_epa_style, 4) AS lg_epa_style,
       round(lg_epa_other, 4) AS lg_epa_other,
       round(gap, 4) AS gap,
       n_hi AS n_style, n_lo AS n_other, lg_n_hi AS lg_n_style, lg_n_lo AS lg_n_other,
       $season - 2 AS since,
       n_hi AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, game_id, team
