// Q7 style matchup, QB styles (documentation/05 -> Query library): this week's expected
// starting QB has a style at the league's top end (`scramble`: scrambles per dropback;
// `deep`: intended air yards per pass attempt) -> how did his opponent's defense do in its
// past games against QBs with that style, compared with its games against other QBs, and
// compared with how every defense did against that style?
//
// A QB's style in a season = his rate over that season's visible games (APPEARED_IN
// `dropbacks`, `scrambles`, `air_yards`, `air_att`; strictly before week W). The cut = the
// $pct percentile of QB-seasons with >= $min_dropbacks dropbacks in the 3 completed
// seasons before this one (league percentiles as of the week). This week's QB is rated
// over this season and last (>= $min_current_dropbacks); a past opponent QB counts as that
// style when his rate that season is over the cut on >= $min_class_dropbacks dropbacks.
// Defense games: regular season and playoffs, this season and the 2 before, strictly before
// week W; EPA per play allowed = the opposing offense's EPA per play, weighted by plays.
// gap = (defense vs style - defense vs others) - (league vs style - league vs others):
// > 0 means it allowed more than usual against that style. Fires when the defense has
// >= $min_games games against the style, >= $min_other other games and |gap| >= $min_gap.
// Walk: (QB)-[:APPEARED_IN {qb_started}]->(Game)<-[:PLAYED_IN]-(his Team), the other
// (Team)-[:PLAYED_IN]->(that Game); this week's QB from Game.<side>_qb_expected.
// Params: $season, $week, $pct, $min_dropbacks, $min_current_dropbacks,
//         $min_class_dropbacks, $min_games, $min_other, $min_gap.
CALL () {
  MATCH (qb:Player)-[a:APPEARED_IN]->(x:Game)
  WHERE qb.position_group = 'QB' AND x.completed AND x.season >= $season - 3
    AND (x.season < $season OR x.week < $week) AND a.dropbacks > 0
  WITH qb, x.season AS s, sum(a.dropbacks) AS db, sum(coalesce(a.scrambles, 0)) AS sc,
       sum(coalesce(a.air_yards, 0.0)) AS ay, sum(coalesce(a.air_att, 0)) AS aa
  RETURN collect({qb: qb.player_id, s: s, db: db, scramble: toFloat(sc) / db,
                  deep: CASE WHEN aa > 0 THEN ay / aa END}) AS styles
}
CALL (styles) {
  UNWIND [x IN styles WHERE x.s < $season AND x.db >= $min_dropbacks] AS x
  RETURN percentileCont(x.scramble, $pct) AS scramble_cut,
         percentileCont(x.deep, $pct) AS deep_cut
}
CALL (styles, scramble_cut, deep_cut) {
  UNWIND [x IN styles WHERE x.s >= $season - 2] AS st
  MATCH (qb:Player {player_id: st.qb})-[qa:APPEARED_IN]->(x:Game {season: st.s})
  WHERE qa.qb_started AND x.completed AND (x.season < $season OR x.week < $week)
  MATCH (o:Team {team_id: qa.team_id})-[op:PLAYED_IN]->(x)<-[:PLAYED_IN]-(d:Team)
  WHERE d <> o AND op.plays > 0 AND op.epa_per_play IS NOT NULL
  RETURN collect({d: d.team_id, epa: op.epa_per_play, plays: op.plays,
                  scramble: st.db >= $min_class_dropbacks AND st.scramble >= scramble_cut,
                  deep: st.db >= $min_class_dropbacks AND st.deep IS NOT NULL
                        AND st.deep >= deep_cut}) AS games
}
MATCH (g:Game {season: $season, week: $week})
UNWIND [[g.home_team, g.away_team, g.away_qb_expected, g.away_qb_source],
        [g.away_team, g.home_team, g.home_qb_expected, g.home_qb_source]] AS side
WITH scramble_cut, deep_cut, games, g,
     side[0] AS def_id, side[1] AS off_id, side[2] AS qb_id, side[3] AS qb_source
WHERE qb_id IS NOT NULL
CALL (qb_id) {
  MATCH (qb:Player {player_id: qb_id})-[a:APPEARED_IN]->(x:Game)
  WHERE x.completed AND x.season >= $season - 1 AND (x.season < $season OR x.week < $week)
    AND a.dropbacks > 0
  RETURN qb.name AS qb, sum(a.dropbacks) AS qb_dropbacks,
         toFloat(sum(coalesce(a.scrambles, 0))) / sum(a.dropbacks) AS qb_scramble,
         CASE WHEN sum(coalesce(a.air_att, 0)) > 0
              THEN sum(coalesce(a.air_yards, 0.0)) / sum(coalesce(a.air_att, 0)) END AS qb_deep
}
WITH * WHERE qb_dropbacks >= $min_current_dropbacks
UNWIND [{style: 'scramble', v: qb_scramble, cut: scramble_cut},
        {style: 'deep', v: qb_deep, cut: deep_cut}] AS st
WITH g, def_id, off_id, qb_id, qb, qb_source, qb_dropbacks, st, games
WHERE st.v IS NOT NULL AND st.cut IS NOT NULL AND st.v >= st.cut
WITH g, def_id, off_id, qb_id, qb, qb_source, qb_dropbacks, st,
     [x IN games WHERE x[st.style]] AS lg_hi,
     [x IN games WHERE NOT x[st.style]] AS lg_lo,
     [x IN games WHERE x.d = def_id AND x[st.style]] AS d_hi,
     [x IN games WHERE x.d = def_id AND NOT x[st.style]] AS d_lo
WHERE size(d_hi) >= $min_games AND size(d_lo) >= $min_other
WITH g, def_id, off_id, qb_id, qb, qb_source, qb_dropbacks, st,
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
RETURN 'style_matchup' AS insight_type,
       st.style AS style,
       g.game_id AS game_id,
       def_id AS team,
       off_id AS opponent,
       qb_id,
       qb,
       qb_source,
       qb_dropbacks,
       round(st.v, 4) AS qb_value,
       round(st.cut, 4) AS cut,
       round(def_epa_style, 4) AS def_epa_style,
       round(def_epa_other, 4) AS def_epa_other,
       round(lg_epa_style, 4) AS lg_epa_style,
       round(lg_epa_other, 4) AS lg_epa_other,
       round(gap, 4) AS gap,
       n_hi AS n_style, n_lo AS n_other, lg_n_hi AS lg_n_style, lg_n_lo AS lg_n_other,
       $season - 2 AS since,
       n_hi AS sample_size,
       round(strength, 3) AS strength
ORDER BY strength DESC, game_id, team, style
